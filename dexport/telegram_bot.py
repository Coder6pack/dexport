"""
Telegram Bot Remote Control & AI Agent Daemon for dexport.
"""

import asyncio
from datetime import datetime
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional, Set, Union

import aiohttp

from .cdp import DEFAULT_PORT
from .client import DiscordClient
from .exporter import parse_timestamp
from .summarizer import call_ai_summary, export_summary_markdown, generate_local_summary

logger = logging.getLogger(__name__)

Union_Id = Union[int, str]


class TelegramBotClient:
    """Lightweight async Telegram Bot API client using pure aiohttp."""

    def __init__(self, token: str):
        self.token = token.strip()
        self.base_url = f"https://api.telegram.org/bot{self.token}"

    async def get_me(self) -> Dict[str, Any]:
        """Fetch bot info."""
        url = f"{self.base_url}/getMe"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()
                if not data.get("ok"):
                    raise RuntimeError(f"Invalid Telegram Bot Token: {data.get('description')}")
                return data.get("result", {})

    async def get_updates(self, offset: Optional[int] = None, timeout: int = 30) -> List[Dict[str, Any]]:
        """Long polling updates."""
        url = f"{self.base_url}/getUpdates"
        params = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=timeout + 5)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("result", [])
                    return []
        except asyncio.TimeoutError:
            return []
        except Exception as e:
            logger.debug(f"Telegram getUpdates error: {e}")
            await asyncio.sleep(2.0)
            return []

    async def send_message(
        self,
        chat_id: Union_Id,
        text: str,
        parse_mode: Optional[str] = "Markdown",
        reply_to_message_id: Optional[int] = None,
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Send text message (auto-splits if text > 4000 characters)."""
        url = f"{self.base_url}/sendMessage"
        chunks = self._split_text(text, max_len=4000)

        last_resp = {}
        for i, chunk in enumerate(chunks):
            payload: Dict[str, Any] = {
                "chat_id": chat_id,
                "text": chunk,
            }
            if parse_mode:
                payload["parse_mode"] = parse_mode
            if reply_to_message_id and i == 0:
                payload["reply_to_message_id"] = reply_to_message_id
            if reply_markup and i == len(chunks) - 1:
                payload["reply_markup"] = reply_markup

            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    res_data = await resp.json()
                    if not res_data.get("ok") and parse_mode:
                        payload.pop("parse_mode", None)
                        async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp2:
                            last_resp = await resp2.json()
                    else:
                        last_resp = res_data

        return last_resp

    async def send_chat_action(self, chat_id: Any, action: str = "typing") -> None:
        """Send chat action indicator (typing, upload_document)."""
        url = f"{self.base_url}/sendChatAction"
        payload = {"chat_id": chat_id, "action": action}
        try:
            async with aiohttp.ClientSession() as session:
                await session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=5))
        except Exception:
            pass

    async def send_document(
        self,
        chat_id: Any,
        file_path: str,
        caption: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send document file to chat."""
        url = f"{self.base_url}/sendDocument"
        data = aiohttp.FormData()
        data.add_field("chat_id", str(chat_id))
        if caption:
            data.add_field("caption", caption)

        with open(file_path, "rb") as f:
            data.add_field("document", f, filename=os.path.basename(file_path))
            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=data, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    return await resp.json()

    async def answer_callback_query(self, callback_query_id: str, text: Optional[str] = None) -> None:
        """Acknowledge inline callback button clicks."""
        url = f"{self.base_url}/answerCallbackQuery"
        payload: Dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        try:
            async with aiohttp.ClientSession() as session:
                await session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=5))
        except Exception:
            pass

    @staticmethod
    def _split_text(text: str, max_len: int = 4000) -> List[str]:
        """Split text safely into message chunks."""
        if len(text) <= max_len:
            return [text]
        chunks = []
        while text:
            if len(text) <= max_len:
                chunks.append(text)
                break
            split_idx = text.rfind("\n", 0, max_len)
            if split_idx == -1:
                split_idx = max_len
            chunks.append(text[:split_idx])
            text = text[split_idx:].lstrip()
        return chunks


# =============================================================================
# Natural Language AI Intent Parser
# =============================================================================

async def parse_user_intent_with_ai(
    user_prompt: str,
    guilds_list: List[Dict[str, Any]],
    default_model: str = "deepseek-v4-flash",
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Use LLM to translate natural language user messages into structured intents or agent replies.
    """
    guild_names = [g.get("name", "") for g in guilds_list]
    guilds_context = ", ".join([f"'{name}'" for name in guild_names])

    system_prompt = f"""
You are the dexport AI Assistant and personal intelligent Discord Agent on Telegram.
You have access to the user's joined Discord servers: [{guilds_context}].

Analyze the user's message:
1. If it is an explicit Discord operation (summarize, read messages, send message, toggle autochat, list servers, list channels, status):
   Return JSON with the corresponding fields:
   {{
     "action": "summarize" | "read" | "send_message" | "autochat_on" | "autochat_off" | "list_guilds" | "list_channels" | "status",
     "guild": "<Closest matching server name from the list, or null>",
     "channel": "<Channel name mentioned, or null>",
     "user": "<Username/ID to filter if any, or null>",
     "since": "<'today', 'yesterday', '3d', '24h', '1w', 'YYYY-MM-DD' or null>",
     "until": "<'YYYY-MM-DD' or null>",
     "query": "<Search keywords if any, or null>",
     "limit": <Integer message limit: 100 for summarize, 20 for read>,
     "message": "<Message text to send if send_message, or null>",
     "prompt": "<Persona style prompt if autochat_on, or null>",
     "model": "<Specific AI model requested if any, or null>"
   }}

2. If it is a greeting, natural conversation, question, coding request, explanation, or general query:
   Return JSON:
   {{
     "action": "chat",
     "reply": "<A helpful, intelligent, friendly, natural response in English>"
   }}

RULES:
- Return raw JSON only (no markdown ```json formatting).
- If the user greets (e.g. "Hello", "Hi", "Hey"), action = "chat" and write a friendly greeting ready to help.
- If the user asks general questions (coding, tech, advice, reasoning), action = "chat" and answer thoroughly like a smart AI agent.
"""

    prompt = f"User Request: \"{user_prompt}\""

    try:
        from .summarizer import _call_openai_compatible, _call_gemini
        m_lower = default_model.lower()
        key = api_key or os.environ.get("OPENCODE_API_KEY") or os.environ.get("OPENCODE_GO_API_KEY") or os.environ.get("GEMINI_API_KEY") or os.environ.get("OPENAI_API_KEY")

        if os.environ.get("OPENCODE_API_KEY") or os.environ.get("OPENCODE_GO_API_KEY"):
            opencode_key = os.environ.get("OPENCODE_API_KEY") or os.environ.get("OPENCODE_GO_API_KEY")
            raw_response = _call_openai_compatible(f"{system_prompt}\n\n{prompt}", default_model, opencode_key, base_url="https://opencode.ai/zen/go/v1")
        elif "gemini" in m_lower and os.environ.get("GEMINI_API_KEY"):
            raw_response = _call_gemini(f"{system_prompt}\n\n{prompt}", default_model, os.environ["GEMINI_API_KEY"])
        else:
            raw_response = _call_openai_compatible(f"{system_prompt}\n\n{prompt}", "gpt-4o-mini", os.environ.get("OPENAI_API_KEY", key))

        clean_json = raw_response.strip()
        if "```json" in clean_json:
            clean_json = clean_json.split("```json")[1].split("```")[0].strip()
        elif "```" in clean_json:
            clean_json = clean_json.split("```")[1].split("```")[0].strip()

        return json.loads(clean_json)
    except Exception as e:
        logger.debug(f"AI Intent parse error: {e}")
        lower_prompt = user_prompt.lower()
        if "enable auto" in lower_prompt or "start autochat" in lower_prompt or "autochat on" in lower_prompt:
            return {"action": "autochat_on", "channel": "general"}
        elif "disable auto" in lower_prompt or "stop autochat" in lower_prompt or "autochat off" in lower_prompt:
            return {"action": "autochat_off"}
        elif "summarize" in lower_prompt or "summary" in lower_prompt:
            return {"action": "summarize", "since": "today"}
        elif "server" in lower_prompt or "guild" in lower_prompt:
            return {"action": "list_guilds"}
        return {"action": "chat", "reply": "Hello! I am your dexport AI Assistant. How can I help you with Discord today?"}


# =============================================================================
# Telegram Daemon Runner
# =============================================================================

class TelegramBotDaemon:
    """Daemon running in background, receiving commands from Telegram and controlling dexport."""

    def __init__(
        self,
        bot_token: str,
        allowed_user_ids: Optional[Set[int]] = None,
        default_model: str = "deepseek-v4-flash",
        cdp_port: int = DEFAULT_PORT,
    ):
        self.bot = TelegramBotClient(bot_token)
        self.allowed_user_ids = allowed_user_ids or set()
        self.default_model = default_model
        self.cdp_port = cdp_port
        self._is_running = False
        self.is_paused = False

        # AutoChat Session management
        self.autochat_session: Optional[Any] = None
        self.autochat_task: Optional[asyncio.Task] = None
        self.last_active_chat_id: Optional[int] = None

    def _get_keyboard(self) -> Dict[str, Any]:
        """Generate interactive persistent reply keyboard for Telegram."""
        if self.is_paused:
            return {
                "keyboard": [
                    [{"text": "▶️ Resume Bot"}, {"text": "❓ Help"}],
                ],
                "resize_keyboard": True,
                "persistent": True,
            }

        autochat_btn = "🔴 Disable Auto-Chat" if (self.autochat_session and self.autochat_session.is_running) else "🤖 Enable Auto-Chat"
        return {
            "keyboard": [
                [{"text": "📊 Summarize Today"}, {"text": "💬 Read Recent"}],
                [{"text": autochat_btn}, {"text": "🏰 Servers List"}],
                [{"text": "⚙️ Settings & Models"}, {"text": "👤 Profile & Status"}],
                [{"text": "⏸️ Pause Bot"}, {"text": "❓ Help"}],
            ],
            "resize_keyboard": True,
            "persistent": True,
        }

    def _get_model_inline_keyboard(self) -> Dict[str, Any]:
        """Generate inline keyboard for 1-tap model switching."""
        return {
            "inline_keyboard": [
                [
                    {"text": f"{'✅ ' if self.default_model=='deepseek-v4-flash' else ''}⚡ DeepSeek V4 Flash", "callback_data": "set_model:deepseek-v4-flash"},
                    {"text": f"{'✅ ' if self.default_model=='deepseek-v4-pro' else ''}🧠 DeepSeek V4 Pro", "callback_data": "set_model:deepseek-v4-pro"}
                ],
                [
                    {"text": f"{'✅ ' if self.default_model=='qwen3.8-max' else ''}🌟 Qwen 3.8 Max", "callback_data": "set_model:qwen3.8-max"},
                    {"text": f"{'✅ ' if self.default_model=='kimi-k3' else ''}🚀 Kimi K3", "callback_data": "set_model:kimi-k3"}
                ],
                [
                    {"text": f"{'✅ ' if self.default_model=='glm-5.3' else ''}💎 GLM 5.3", "callback_data": "set_model:glm-5.3"},
                    {"text": f"{'✅ ' if self.default_model=='minimax-m3' else ''}🤖 MiniMax M3", "callback_data": "set_model:minimax-m3"}
                ],
                [
                    {"text": f"{'✅ ' if self.default_model=='claude-3-7-sonnet' else ''}🔥 Claude 3.7 Sonnet", "callback_data": "set_model:claude-3-7-sonnet"},
                    {"text": f"{'✅ ' if self.default_model=='gpt-4o' else ''}✨ GPT-4o", "callback_data": "set_model:gpt-4o"}
                ],
                [
                    {"text": f"{'✅ ' if self.default_model=='gemini-2.5-flash' else ''}⚡ Gemini 2.5 Flash", "callback_data": "set_model:gemini-2.5-flash"},
                    {"text": f"{'✅ ' if self.default_model=='grok-4.6' else ''}🦁 Grok 4.6", "callback_data": "set_model:grok-4.6"}
                ]
            ]
        }

    async def _on_autochat_sent(self, info: Dict[str, Any]) -> None:
        """Notify user on Telegram whenever AutoChat responds on Discord."""
        if self.last_active_chat_id:
            reply_tag = f" `(Reply to ID: {info.get('reply_to')})`" if info.get("reply_to") else ""
            msg = (
                f"💬 **[AutoChat] Sent message to #{info['channel_name']} ({info['guild_name']}):**\n\n"
                f"\"{info['content']}\"{reply_tag}\n\n"
                f"🕒 At `{info.get('timestamp')}`"
            )
            await self.bot.send_message(self.last_active_chat_id, msg, reply_markup=self._get_keyboard())

    async def start(self) -> None:
        """Start long-polling loop."""
        bot_info = await self.bot.get_me()
        bot_username = bot_info.get("username", "dexport_bot")
        print(f"🤖 Telegram Bot @{bot_username} is online and listening!")

        if self.allowed_user_ids:
            print(f"🔒 Locked to User IDs: {list(self.allowed_user_ids)}")
        else:
            print("⚠️ WARNING: No TELEGRAM_ALLOWED_USER_ID set. Anyone can send commands.")

        self._is_running = True
        offset = None

        while self._is_running:
            try:
                updates = await self.bot.get_updates(offset=offset, timeout=25)
                for update in updates:
                    offset = update["update_id"] + 1
                    if "callback_query" in update:
                        asyncio.create_task(self._process_callback_query(update["callback_query"]))
                    elif "message" in update and update["message"].get("text"):
                        asyncio.create_task(self._process_message(update["message"]))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Telegram polling error: {e}")
                await asyncio.sleep(2.0)

    def stop(self) -> None:
        """Stop the daemon loop and any active autochat."""
        self._is_running = False
        if self.autochat_session:
            self.autochat_session.stop()
        if self.autochat_task and not self.autochat_task.done():
            self.autochat_task.cancel()

    async def _process_callback_query(self, cb: Dict[str, Any]) -> None:
        """Handle inline button clicks for model switching."""
        cb_id = cb["id"]
        from_user = cb.get("from", {})
        user_id = from_user.get("id")
        data = cb.get("data", "")
        chat_id = cb.get("message", {}).get("chat", {}).get("id") or user_id

        if self.allowed_user_ids and user_id not in self.allowed_user_ids:
            await self.bot.answer_callback_query(cb_id, "⛔ Access denied!")
            return

        if data.startswith("set_model:"):
            new_model = data.replace("set_model:", "").strip()
            self.default_model = new_model
            if self.autochat_session:
                self.autochat_session.model = new_model
            await self.bot.answer_callback_query(cb_id, f"✅ Selected: {new_model}")
            await self.bot.send_message(
                chat_id,
                f"✅ **Switched AI Model to:** `{new_model}`\n\n"
                f"All future summarization and AutoChat tasks will use this model.",
                reply_markup=self._get_keyboard(),
            )

    async def _process_message(self, msg: Dict[str, Any]) -> None:
        """Handle incoming message from user."""
        chat_id = msg["chat"]["id"]
        self.last_active_chat_id = chat_id
        from_user = msg.get("from", {})
        user_id = from_user.get("id")
        text = msg.get("text", "").strip()

        # Security check
        if self.allowed_user_ids and user_id not in self.allowed_user_ids:
            await self.bot.send_message(
                chat_id,
                f"⛔ **Access Denied!**\nUser ID `{user_id}` is not authorized to control this machine.",
            )
            return

        # Check pause/standby state
        if self.is_paused:
            if text in ("▶️ Resume Bot", "/start", "/resume", "start", "resume"):
                self.is_paused = False
                await self.bot.send_message(
                    chat_id,
                    "🟢 **Bot Resumed!**\nReady to assist. Select an action below or type naturally:",
                    reply_markup=self._get_keyboard(),
                )
                return
            else:
                await self.bot.send_message(
                    chat_id,
                    "💤 **Bot is in Standby Mode.**\nTap `[▶️ Resume Bot]` or type `/start` to continue.",
                    reply_markup=self._get_keyboard(),
                )
                return

        # Handle Menu Button Taps
        if text == "📊 Summarize Today":
            await self._handle_natural_language(chat_id, "Summarize channel discussions for today", msg)
            return
        elif text == "💬 Read Recent":
            await self._handle_natural_language(chat_id, "Read latest 10 messages", msg)
            return
        elif text == "🤖 Enable Auto-Chat":
            await self._handle_slash_command(chat_id, "/autochat on general", msg)
            return
        elif text == "🔴 Disable Auto-Chat":
            await self._handle_slash_command(chat_id, "/autochat off", msg)
            return
        elif text in ("⚙️ Settings & Models", "/settings", "/model", "/models"):
            await self._send_settings_menu(chat_id)
            return
        elif text == "🏰 Servers List":
            await self._handle_slash_command(chat_id, "/guilds", msg)
            return
        elif text == "👤 Profile & Status":
            await self._handle_slash_command(chat_id, "/status", msg)
            return
        elif text in ("⏸️ Pause Bot", "/pause", "/stop"):
            self.is_paused = True
            if self.autochat_session:
                self.autochat_session.stop()
                self.autochat_session = None
            if self.autochat_task and not self.autochat_task.done():
                self.autochat_task.cancel()
                self.autochat_task = None
            await self.bot.send_message(
                chat_id,
                "💤 **Bot Paused (Standby).**\nTap `[▶️ Resume Bot]` whenever you wish to reactivate.",
                reply_markup=self._get_keyboard(),
            )
            return
        elif text in ("❓ Help", "/help"):
            await self._handle_slash_command(chat_id, "/help", msg)
            return

        # Handle Slash Commands
        if text.startswith("/"):
            await self._handle_slash_command(chat_id, text, msg)
            return

        # Handle Natural Language via AI
        await self._handle_natural_language(chat_id, text, msg)

    async def _send_settings_menu(self, chat_id: int) -> None:
        """Send settings and interactive model picker."""
        text = (
            "⚙️ **SETTINGS & AI MODEL CONFIGURATION**\n\n"
            f"• **Active Model:** `{self.default_model}`\n"
            f"• **Gateway:** `OpenCode Go (https://opencode.ai/zen/go/v1)`\n"
            f"• **Reports Directory:** `~/Documents/report`\n\n"
            "👇 **Tap any model below to switch instantly:**"
        )
        await self.bot.send_message(
            chat_id,
            text,
            reply_markup=self._get_model_inline_keyboard(),
        )

    async def _handle_slash_command(self, chat_id: int, text: str, msg: Dict[str, Any]) -> None:
        """Handle direct slash commands."""
        cmd_parts = text.split()
        cmd = cmd_parts[0].lower()

        if cmd in ("/start", "/help"):
            help_text = (
                "👋 **Welcome! I am your dexport Discord AI Agent.**\n\n"
                "You can use the **interactive keyboard below** or chat naturally in plain English:\n"
                "• *\"Summarize discussions today\"*\n"
                "• *\"Read 10 recent messages from #announcements\"*\n"
                "• *\"Enable autochat for #chat, be friendly and concise\"*\n"
                "• *\"Send 'Hello team' to channel #general\"*\n\n"
                "**Quick Commands:**\n"
                "/status — View Discord account & CDP status\n"
                "/guilds — List joined servers\n"
                "/channels `<server_name>` — List channels in server\n"
                "/autochat on `<channel>` `[prompt]` — Start Auto-Chat\n"
                "/autochat off — Stop Auto-Chat\n"
                "/autochat status — View Auto-Chat status"
            )
            await self.bot.send_message(chat_id, help_text, reply_markup=self._get_keyboard())

        elif cmd in ("/stop", "/pause"):
            self.is_paused = True
            if self.autochat_session:
                self.autochat_session.stop()
            await self.bot.send_message(
                chat_id,
                "💤 **Bot Paused (Standby).**\nTap `[▶️ Resume Bot]` to continue.",
                reply_markup=self._get_keyboard(),
            )

        elif cmd == "/autochat":
            sub_cmd = cmd_parts[1].lower() if len(cmd_parts) > 1 else "status"

            if sub_cmd == "off":
                if self.autochat_session:
                    self.autochat_session.stop()
                    self.autochat_session = None
                if self.autochat_task and not self.autochat_task.done():
                    self.autochat_task.cancel()
                    self.autochat_task = None
                await self.bot.send_message(chat_id, "🔴 **AutoChat disabled successfully.**", reply_markup=self._get_keyboard())

            elif sub_cmd == "status":
                if self.autochat_session and self.autochat_session.is_running:
                    session = self.autochat_session
                    stats = session.stats
                    duration = ""
                    if stats.get("start_time"):
                        mins = int((datetime.now() - stats["start_time"]).total_seconds() / 60)
                        duration = f" ({mins} min)"
                    status_text = (
                        f"🟢 **AutoChat is ACTIVE:**\n"
                        f"• Server: **{session.guild_name}**\n"
                        f"• Channel: **#{session.channel_name}**\n"
                        f"• Model: `{session.model}`\n"
                        f"• Mode: `{'Mentions & Replies Only' if session.mentions_only else 'General Channel Discussion'}`\n"
                        f"• Auto-sent: `{stats.get('sent_count', 0)}` messages{duration}\n"
                        f"• Persona Prompt: *\"{session.prompt}\"*"
                    )
                else:
                    status_text = (
                        "⚪ **AutoChat is currently OFF.**\n"
                        "👉 Tap `[🤖 Enable Auto-Chat]` or type `/autochat on <channel>` to start."
                    )
                await self.bot.send_message(chat_id, status_text, reply_markup=self._get_keyboard())

            elif sub_cmd == "on":
                from .autochat import AutoChatSession, DEFAULT_PERSONA_PROMPT

                channel_arg = cmd_parts[2] if len(cmd_parts) > 2 else "general"
                prompt_arg = " ".join(cmd_parts[3:]) if len(cmd_parts) > 3 else DEFAULT_PERSONA_PROMPT

                if self.autochat_session:
                    self.autochat_session.stop()

                try:
                    async with DiscordClient(port=self.cdp_port, auto_restart=True) as client:
                        guilds = await client.get_guilds()
                    target_guild = guilds[0]["name"] if guilds else "General"
                except Exception:
                    target_guild = "General"

                self.autochat_session = AutoChatSession(
                    guild_name=target_guild,
                    channel_name=channel_arg,
                    prompt=prompt_arg,
                    mentions_only=True,
                    model=self.default_model,
                    port=self.cdp_port,
                    on_message_sent=self._on_autochat_sent,
                )

                self.autochat_task = asyncio.create_task(self.autochat_session.start())

                await self.bot.send_message(
                    chat_id,
                    f"🟢 **AutoChat Activated!**\n\n"
                    f"• Server: **{target_guild}**\n"
                    f"• Channel: **#{channel_arg}**\n"
                    f"• AI Model: `{self.default_model}`\n"
                    f"• Persona: *\"{prompt_arg}\"*\n\n"
                    f"⚡ Bot will automatically respond when you are mentioned or replied to.\n"
                    f"👉 To stop: Tap `[🔴 Disable Auto-Chat]` below!",
                    reply_markup=self._get_keyboard(),
                )

        elif cmd == "/status":
            await self.bot.send_chat_action(chat_id, "typing")
            try:
                async with DiscordClient(port=self.cdp_port, auto_restart=True) as client:
                    user = await client.get_current_user()
                    guilds = await client.get_guilds()
                username = user.get("global_name") or user.get("username", "")
                tag = f"@{user.get('username')}"
                msg_text = (
                    f"👤 **Discord Account:** {username} ({tag})\n"
                    f"🆔 **User ID:** `{user.get('id')}`\n"
                    f"🏰 **Joined Guilds:** {len(guilds)} servers\n"
                    f"🟢 **CDP Connection:** Active (Port {self.cdp_port})"
                )
                await self.bot.send_message(chat_id, msg_text)
            except Exception as e:
                await self.bot.send_message(chat_id, f"❌ Discord connection error: `{e}`")

        elif cmd == "/guilds":
            await self.bot.send_chat_action(chat_id, "typing")
            try:
                async with DiscordClient(port=self.cdp_port, auto_restart=True) as client:
                    guilds = await client.get_guilds()
                lines = [f"🏰 **Your Joined Servers ({len(guilds)}):**\n"]
                for i, g in enumerate(guilds, 1):
                    role = "👑 Owner" if g.get("owner") else "Member"
                    lines.append(f"{i}. **{g['name']}** `({role})` — ID: `{g['id']}`")
                await self.bot.send_message(chat_id, "\n".join(lines))
            except Exception as e:
                await self.bot.send_message(chat_id, f"❌ Error: `{e}`")

        elif cmd.startswith("/channel"):
            guild_query = " ".join(cmd_parts[1:]) if len(cmd_parts) > 1 else ""
            if not guild_query:
                await self.bot.send_message(chat_id, "⚠️ Specify server name, e.g.: `/channels Work`")
                return
            await self.bot.send_chat_action(chat_id, "typing")
            try:
                async with DiscordClient(port=self.cdp_port, auto_restart=True) as client:
                    g_info = await client.resolve_guild(guild_query)
                    channels = await client.get_channels(g_info["id"])
                text_channels = [f"#{c['name']}" for c in channels if c.get("type") in (0, 5)][:30]
                await self.bot.send_message(
                    chat_id,
                    f"📋 **Channels in server {g_info['name']}:**\n" + ", ".join(text_channels),
                )
            except Exception as e:
                await self.bot.send_message(chat_id, f"❌ Error: `{e}`")

    async def _handle_natural_language(self, chat_id: int, text: str, msg: Dict[str, Any]) -> None:
        """Parse natural language request and execute action or converse."""
        await self.bot.send_chat_action(chat_id, "typing")

        guilds = []
        try:
            async with DiscordClient(port=self.cdp_port, auto_restart=True) as client:
                guilds = await client.get_guilds()
        except Exception:
            pass

        intent = await parse_user_intent_with_ai(
            user_prompt=text,
            guilds_list=guilds,
            default_model=self.default_model,
        )

        action = intent.get("action", "unknown")
        guild_name = intent.get("guild") or (guilds[0]["name"] if guilds else "")
        channel_name = intent.get("channel")
        since = intent.get("since")
        until = intent.get("until")
        target_user = intent.get("user")
        query = intent.get("query")
        limit = intent.get("limit", 100)
        ai_model = intent.get("model") or self.default_model

        if action == "summarize":
            if not channel_name:
                try:
                    async with DiscordClient(port=self.cdp_port, auto_restart=True) as client:
                        g_info = await client.resolve_guild(guild_name)
                        channels = await client.get_channels(g_info["id"])
                        first_text = next((c["name"] for c in channels if c.get("type") == 0), "general")
                        channel_name = first_text
                except Exception:
                    channel_name = "general"

            await self.bot.send_message(
                chat_id,
                f"⏳ **Scraping data and analyzing with `{ai_model}`...**\n"
                f"• Server: **{guild_name}**\n"
                f"• Channel: **#{channel_name}**\n"
                f"• Timeframe: `{since or 'recent'}`",
            )
            await self.bot.send_chat_action(chat_id, "typing")

            try:
                async with DiscordClient(port=self.cdp_port, auto_restart=True) as client:
                    g_info = await client.resolve_guild(guild_name)
                    c_info = await client.resolve_channel(g_info["id"], channel_name)
                    messages, resolved_user = await client.get_filtered_messages(
                        c_info["id"],
                        user_query=target_user,
                        since=since,
                        until=until,
                        text_query=query,
                        limit=limit,
                        scan_depth=1500,
                    )

                if not messages:
                    await self.bot.send_message(chat_id, f"⚠️ No messages found matching criteria in #{channel_name}.")
                    return

                user_display = resolved_user.get("global_name") or resolved_user.get("username") if resolved_user else (target_user or f"#{c_info.get('name')}")
                summary_data = generate_local_summary(messages, target_user_name=user_display, guild_name=g_info["name"], channel_name=c_info["name"])

                await self.bot.send_chat_action(chat_id, "typing")
                ai_summary_text = call_ai_summary(
                    messages,
                    target_user_name=user_display,
                    guild_name=g_info["name"],
                    channel_name=c_info["name"],
                    model=ai_model,
                )

                from .cli import resolve_report_filepath
                out_file = resolve_report_filepath(None, default_prefix="report", ext="md")
                export_summary_markdown(summary_data, ai_summary_text, out_file, model_name=ai_model)

                header = (
                    f"📊 **DISCUSSION SUMMARY: #{c_info['name']}**\n"
                    f"🏰 Server: **{g_info['name']}** | 💬 Messages: `{len(messages)}`\n"
                    f"🤖 AI Model: `{ai_model}`\n"
                    f"🕒 Timeline: `{summary_data.get('first_seen')} ➔ {summary_data.get('last_seen')}`\n\n"
                )
                full_reply = header + (ai_summary_text or "No AI output generated.")
                await self.bot.send_message(chat_id, full_reply)

                await self.bot.send_chat_action(chat_id, "upload_document")
                await self.bot.send_document(chat_id, file_path=out_file, caption=f"📄 Summary report: {os.path.basename(out_file)}")

            except Exception as e:
                await self.bot.send_message(chat_id, f"❌ Summarization error: `{e}`")

        elif action == "read":
            if not channel_name:
                channel_name = "general"
            await self.bot.send_chat_action(chat_id, "typing")
            try:
                async with DiscordClient(port=self.cdp_port, auto_restart=True) as client:
                    g_info = await client.resolve_guild(guild_name)
                    c_info = await client.resolve_channel(g_info["id"], channel_name)
                    messages, _ = await client.get_filtered_messages(
                        c_info["id"],
                        user_query=target_user,
                        since=since,
                        until=until,
                        text_query=query,
                        limit=min(limit, 20),
                    )

                if not messages:
                    await self.bot.send_message(chat_id, f"⚠️ No messages found in #{channel_name}.")
                    return

                sorted_msgs = sorted(messages, key=lambda m: m.get("id", "0"))
                lines = [f"💬 **Recent messages in #{c_info['name']} ({g_info['name']}):**\n"]
                for m in sorted_msgs:
                    author = m.get("author", {}).get("global_name") or m.get("author", {}).get("username", "Unknown")
                    ts = parse_timestamp(m.get("timestamp"))
                    content = m.get("content", "")
                    lines.append(f"• **{author}** `({ts})`: {content}")

                await self.bot.send_message(chat_id, "\n".join(lines))
            except Exception as e:
                await self.bot.send_message(chat_id, f"❌ Read error: `{e}`")

        elif action == "send_message":
            msg_content = intent.get("message")
            if not channel_name or not msg_content:
                await self.bot.send_message(chat_id, "⚠️ Specify channel and message content (e.g. *Send 'Hello team' to general*).")
                return
            await self.bot.send_chat_action(chat_id, "typing")
            try:
                async with DiscordClient(port=self.cdp_port, auto_restart=True) as client:
                    g_info = await client.resolve_guild(guild_name)
                    c_info = await client.resolve_channel(g_info["id"], channel_name)
                    res = await client.send_message(c_info["id"], content=msg_content)
                await self.bot.send_message(
                    chat_id,
                    f"✔ **Sent successfully to #{c_info['name']} ({g_info['name']}):**\n\"{msg_content}\" `(ID: {res.get('id')})`",
                )
            except Exception as e:
                await self.bot.send_message(chat_id, f"❌ Send error: `{e}`")

        elif action == "autochat_on":
            ch = channel_name or "general"
            custom_p = intent.get("prompt") or "Reply naturally, concisely, and appropriately to conversation topics."
            await self._handle_slash_command(chat_id, f"/autochat on {ch} {custom_p}", msg)

        elif action == "autochat_off":
            await self._handle_slash_command(chat_id, "/autochat off", msg)

        elif action == "list_guilds":
            await self._handle_slash_command(chat_id, "/guilds", msg)

        elif action == "list_channels":
            await self._handle_slash_command(chat_id, f"/channels {guild_name}", msg)

        elif action == "chat":
            reply_text = intent.get("reply") or "I am listening! How can I assist you with Discord or answering questions?"
            await self.bot.send_message(chat_id, reply_text, reply_markup=self._get_keyboard())

        else:
            reply_text = intent.get("reply") or f"Hello! Received: *\"{text}\"*. How can I assist you with Discord operations or general queries?"
            await self.bot.send_message(
                chat_id,
                reply_text,
                reply_markup=self._get_keyboard(),
            )
