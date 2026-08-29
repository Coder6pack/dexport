"""
Telegram Bot remote control daemon for dexport.
Allows commanding Discord reading, sending, and summarizing via natural language from Telegram.
"""

import asyncio
from datetime import datetime
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Set
import aiohttp

from .cdp import DEFAULT_PORT
from .client import DiscordClient
from .exporter import parse_timestamp
from .summarizer import (
    DEFAULT_USER_AGENT,
    call_ai_summary,
    export_summary_markdown,
    generate_local_summary,
)

logger = logging.getLogger("dexport.telegram")


class TelegramBotClient:
    """Async Telegram Bot client using aiohttp with zero heavy dependencies."""

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
                    raise RuntimeError(f"Telegram Bot Token không hợp lệ: {data.get('description')}")
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
            # Find closest newline
            split_idx = text.rfind("\n", 0, max_len)
            if split_idx == -1:
                split_idx = max_len
            chunks.append(text[:split_idx])
            text = text[split_idx:].lstrip()
        return chunks


Union_Id = Any


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
    Use LLM to translate natural language Vietnamese message into structured intent.
    """
    guild_names = [g.get("name", "") for g in guilds_list]
    guilds_context = ", ".join([f"'{name}'" for name in guild_names])

    system_prompt = f"""
Bạn là Trợ lý AI dexport kiêm Agent cá nhân thông minh và chu đáo của người dùng trên Telegram.
Bạn có quyền truy cập vào các server Discord của người dùng: [{guilds_context}].

Hãy phân tích tin nhắn của người dùng:
1. Nếu là lệnh thao tác Discord (tóm tắt, đọc tin nhắn, gửi tin nhắn, bật/tắt autochat, xem server, xem kênh):
   Trả về JSON với các trường tương ứng:
   {{
     "action": "summarize" | "read" | "send_message" | "autochat_on" | "autochat_off" | "list_guilds" | "list_channels" | "status",
     "guild": "<Tên server gần đúng nhất trong danh sách, hoặc null>",
     "channel": "<Tên kênh được nhắc đến, hoặc null>",
     "user": "<Tên người dùng cần lọc nếu có, hoặc null>",
     "since": "<'today', 'yesterday', '3d', '24h', '1w', 'YYYY-MM-DD' hoặc null>",
     "until": "<'YYYY-MM-DD' hoặc null>",
     "query": "<từ khóa tìm kiếm nếu có, hoặc null>",
     "limit": <số lượng tin nhắn số nguyên, mặc định 100 nếu summarize, 20 nếu read>,
     "message": "<nội dung tin nhắn muốn gửi nếu action là send_message, hoặc null>",
     "prompt": "<chỉ đạo persona nếu action là autochat_on, hoặc null>",
     "model": "<tên model AI nếu có yêu cầu riêng, hoặc null>"
   }}

2. Nếu là câu chào hỏi, trò chuyện tự nhiên, hỏi đáp kiến thức, giải thích code, tán gẫu hoặc câu hỏi chung:
   Trả về JSON:
   {{
     "action": "chat",
     "reply": "<Câu trả lời tự nhiên, thân thiện, thông minh và hữu ích bằng tiếng Việt>"
   }}

QUY TẮC:
- Chỉ trả về duy nhất chuỗi JSON hợp lệ (không kèm markdown ```json).
- Nếu người dùng chào hỏi (ví dụ "Xin chào", "Hi bro", "Hello", "Có ai không"), action = "chat" và viết câu chào thân thiện, sẵn sàng hỗ trợ.
- Nếu người dùng hỏi câu hỏi bất kỳ (code, tech, đời sống, kiến thức), action = "chat" và trả lời đầy đủ, thông minh như một AI Agent thực thụ.
"""


    prompt = f"Yêu cầu người dùng: \"{user_prompt}\""

    # Execute LLM call
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

        # Extract JSON from response
        clean_json = raw_response.strip()
        if "```json" in clean_json:
            clean_json = clean_json.split("```json")[1].split("```")[0].strip()
        elif "```" in clean_json:
            clean_json = clean_json.split("```")[1].split("```")[0].strip()

        return json.loads(clean_json)
    except Exception as e:
        logger.debug(f"AI Intent parse error: {e}")
        # Fallback simple rule-based parsing
        lower_prompt = user_prompt.lower()
        if "bật auto" in lower_prompt or "bật autochat" in lower_prompt:
            return {"action": "autochat_on", "channel": "ai-lười-chat-tổng"}
        elif "tắt auto" in lower_prompt or "tắt autochat" in lower_prompt:
            return {"action": "autochat_off"}
        elif "tóm tắt" in lower_prompt or "tổng hợp" in lower_prompt:
            return {
                "action": "summarize",
                "guild": "Cú Đêm AI",
                "channel": "ai-lười-chat-tổng",
                "since": "today",
                "limit": 100,
                "model": default_model,
            }
        return {"action": "unknown"}



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
                    [{"text": "▶️ KÍCH HOẠT LẠI BOT"}, {"text": "❓ Hướng dẫn"}],
                ],
                "resize_keyboard": True,
                "persistent": True,
            }

        autochat_btn = "🔴 Tắt Auto-Chat" if (self.autochat_session and self.autochat_session.is_running) else "🤖 Bật Auto-Chat"
        return {
            "keyboard": [
                [{"text": "📊 Tóm tắt hôm nay"}, {"text": "💬 Đọc tin nhắn mới"}],
                [{"text": autochat_btn}, {"text": "🏰 Danh sách Server"}],
                [{"text": "⚙️ Cài đặt & Model"}, {"text": "👤 Profile & Status"}],
                [{"text": "⏸️ Tạm dừng Bot"}, {"text": "❓ Hướng dẫn"}],
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
            reply_tag = f" `(Rep ID: {info.get('reply_to')})`" if info.get("reply_to") else ""
            msg = (
                f"💬 **[AutoChat] Đã tự động gửi vào #{info['channel_name']} ({info['guild_name']}):**\n\n"
                f"\"{info['content']}\"{reply_tag}\n\n"
                f"🕒 Lúc `{info.get('timestamp')}`"
            )
            await self.bot.send_message(self.last_active_chat_id, msg, reply_markup=self._get_keyboard())

    async def start(self) -> None:
        """Start long-polling loop."""
        bot_info = await self.bot.get_me()
        bot_username = bot_info.get("username", "dexport_bot")
        print(f"🤖 Telegram Bot @{bot_username} đã sẵn sàng lắng nghe lệnh!")

        if self.allowed_user_ids:
            print(f"🔒 Khóa quyền cho User ID: {list(self.allowed_user_ids)}")
        else:
            print("⚠️ CẢNH BÁO: Chưa set TELEGRAM_ALLOWED_USER_ID, bất kỳ ai cũng có thể gửi lệnh.")

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
                logger.error(f"Lỗi polling Telegram: {e}")
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
            await self.bot.answer_callback_query(cb_id, "⛔ Bạn không có quyền điều khiển!")
            return

        if data.startswith("set_model:"):
            new_model = data.replace("set_model:", "").strip()
            self.default_model = new_model
            if self.autochat_session:
                self.autochat_session.model = new_model
            await self.bot.answer_callback_query(cb_id, f"✅ Đã chọn: {new_model}")
            await self.bot.send_message(
                chat_id,
                f"✅ **Đã chuyển sang Model AI:** `{new_model}`\n\n"
                f"Mọi tác vụ tóm tắt và AutoChat từ giờ sẽ sử dụng model này.",
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
                f"⛔ **Từ chối truy cập!**\nUser ID `{user_id}` của bạn chưa được cấp quyền điều khiển máy tính này.",
            )
            return

        # Check pause/standby state
        if self.is_paused:
            if text in ("▶️ KÍCH HOẠT LẠI BOT", "/start", "/resume", "start", "resume", "bật lại"):
                self.is_paused = False
                await self.bot.send_message(
                    chat_id,
                    "🟢 **Bot đã được KÍCH HOẠT LẠI!**\nSẵn sàng phục vụ bạn. Hãy chọn nút bên dưới hoặc nhắn tin tự nhiên:",
                    reply_markup=self._get_keyboard(),
                )
                return
            else:
                await self.bot.send_message(
                    chat_id,
                    "💤 **Bot đang ở chế độ TẠM DỪNG (Standby).**\nBấm nút `[▶️ KÍCH HOẠT LẠI BOT]` hoặc gõ `/start` khi bạn muốn tiếp tục.",
                    reply_markup=self._get_keyboard(),
                )
                return

        # Handle Menu Button Taps
        if text == "📊 Tóm tắt hôm nay":
            await self._handle_natural_language(chat_id, "Tóm tắt kênh ai-lười-chat-tổng hôm nay", msg)
            return
        elif text == "💬 Đọc tin nhắn mới":
            await self._handle_natural_language(chat_id, "Đọc 10 tin nhắn mới nhất kênh ai-lười-chat-tổng", msg)
            return
        elif text == "🤖 Bật Auto-Chat":
            await self._handle_slash_command(chat_id, "/autochat on ai-lười-chat-tổng", msg)
            return
        elif text == "🔴 Tắt Auto-Chat":
            await self._handle_slash_command(chat_id, "/autochat off", msg)
            return
        elif text in ("⚙️ Cài đặt & Model", "/settings", "/model", "/models"):
            await self._send_settings_menu(chat_id)
            return
        elif text == "🏰 Danh sách Server":
            await self._handle_slash_command(chat_id, "/guilds", msg)
            return
        elif text == "👤 Profile & Status":
            await self._handle_slash_command(chat_id, "/status", msg)
            return
        elif text in ("⏸️ Tạm dừng Bot", "/pause", "/stop"):
            self.is_paused = True
            if self.autochat_session:
                self.autochat_session.stop()
                self.autochat_session = None
            if self.autochat_task and not self.autochat_task.done():
                self.autochat_task.cancel()
                self.autochat_task = None
            await self.bot.send_message(
                chat_id,
                "💤 **Đã chuyển sang chế độ TẠM DỪNG (Standby).**\nBot sẽ không thực hiện bất kỳ tác vụ nào cho đến khi bạn bấm `[▶️ KÍCH HOẠT LẠI BOT]`.",
                reply_markup=self._get_keyboard(),
            )
            return
        elif text in ("❓ Hướng dẫn", "/help"):
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
            "⚙️ **CÀI ĐẶT & CHỌN MODEL AI**\n\n"
            f"• **Model hiện tại:** `{self.default_model}`\n"
            f"• **Nhà cung cấp:** `OpenCode Go (https://opencode.ai/zen/go/v1)`\n"
            f"• **Kênh Discord mặc định:** `#ai-lười-chat-tổng (Cú Đêm AI)`\n"
            f"• **Thư mục lưu báo cáo:** `/Users/mac/Documents/report`\n\n"
            "👇 **Chọn nhanh Model AI bên dưới bằng cách bấm nút:**"
        )
        await self.bot.send_message(
            chat_id,
            text,
            reply_markup=self._get_model_inline_keyboard(),
        )


    async def _handle_slash_command(self, chat_id: int, text: str, msg: Dict[str, Any]) -> None:
        """Handle direct slash commands like /start, /guilds, /status, /autochat."""
        cmd_parts = text.split()
        cmd = cmd_parts[0].lower()

        if cmd in ("/start", "/help"):
            help_text = (
                "👋 **Xin chào! Tôi là Trợ lý AI dexport điều khiển Discord.**\n\n"
                "Bạn có thể dùng **Menu nút bấm bên dưới** hoặc nhắn bất kỳ câu nào bằng tiếng Việt tự nhiên:\n"
                "• *\"Tóm tắt kênh ai-lười-chat-tổng bên server Cú Đêm hôm nay\"*\n"
                "• *\"Đọc 10 tin nhắn mới nhất trong kênh #thông-báo-chung\"*\n"
                "• *\"Bật autochat kênh lười-chat, trả lời vui vẻ ngắn gọn\"*\n"
                "• *\"Gửi tin nhắn 'Chào anh em' vào kênh #nội-quy\"*\n\n"
                "**Bấm các nút tiện ích ở bàn phím bên dưới để thao tác nhanh!**"
            )
            await self.bot.send_message(chat_id, help_text, reply_markup=self._get_keyboard())

        elif cmd in ("/stop", "/pause"):
            self.is_paused = True
            if self.autochat_session:
                self.autochat_session.stop()
            await self.bot.send_message(
                chat_id,
                "💤 **Đã chuyển sang chế độ TẠM DỪNG (Standby).**\nBấm nút `[▶️ KÍCH HOẠT LẠI BOT]` để tiếp tục.",
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
                await self.bot.send_message(chat_id, "🔴 **Đã TẮT tính năng AutoChat thành công.**", reply_markup=self._get_keyboard())

            elif sub_cmd == "status":
                if self.autochat_session and self.autochat_session.is_running:
                    session = self.autochat_session
                    stats = session.stats
                    duration = ""
                    if stats.get("start_time"):
                        mins = int((datetime.now() - stats["start_time"]).total_seconds() / 60)
                        duration = f" ({mins} phút)"
                    status_text = (
                        f"🟢 **AutoChat ĐANG BẬT:**\n"
                        f"• Server: **{session.guild_name}**\n"
                        f"• Kênh: **#{session.channel_name}**\n"
                        f"• Model: `{session.model}`\n"
                        f"• Chế độ: `{'Chỉ khi được tag / rep' if session.mentions_only else 'Tham gia trò chuyện cả kênh'}`\n"
                        f"• Tin đã tự gửi: `{stats.get('sent_count', 0)}` tin{duration}\n"
                        f"• Prompt persona: *\"{session.prompt}\"*"
                    )
                else:
                    status_text = (
                        "⚪ **AutoChat hiện ĐANG TẮT.**\n"
                        "👉 Dùng nút `[🤖 Bật Auto-Chat]` hoặc gõ `/autochat on <kênh>` để bật."
                    )
                await self.bot.send_message(chat_id, status_text, reply_markup=self._get_keyboard())

            elif sub_cmd == "on":
                from .autochat import AutoChatSession, DEFAULT_PERSONA_PROMPT

                # Extract channel and prompt
                channel_arg = cmd_parts[2] if len(cmd_parts) > 2 else "ai-lười-chat-tổng"
                prompt_arg = " ".join(cmd_parts[3:]) if len(cmd_parts) > 3 else DEFAULT_PERSONA_PROMPT

                # Stop existing session if any
                if self.autochat_session:
                    self.autochat_session.stop()

                # Get guilds
                try:
                    async with DiscordClient(port=self.cdp_port, auto_restart=True) as client:
                        guilds = await client.get_guilds()
                    target_guild = guilds[0]["name"] if guilds else "Cú Đêm AI"
                except Exception:
                    target_guild = "Cú Đêm AI"

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
                    f"🟢 **Đã KÍCH HOẠT AutoChat thành công!**\n\n"
                    f"• Server: **{target_guild}**\n"
                    f"• Kênh: **#{channel_arg}**\n"
                    f"• Model AI: `{self.default_model}`\n"
                    f"• Chỉ đạo Prompt: *\"{prompt_arg}\"*\n\n"
                    f"⚡ Bot sẽ tự động trả lời khi có ai tag bạn hoặc reply tin nhắn của bạn.\n"
                    f"👉 Để tắt: Bấm nút `[🔴 Tắt Auto-Chat]` bên dưới!",
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
                    f"👤 **Tài khoản Discord:** {username} ({tag})\n"
                    f"🆔 **User ID:** `{user.get('id')}`\n"
                    f"🏰 **Số Server:** {len(guilds)} servers\n"
                    f"🟢 **Kết nối CDP:** Hoạt động (Port {self.cdp_port})"
                )
                await self.bot.send_message(chat_id, msg_text)
            except Exception as e:
                await self.bot.send_message(chat_id, f"❌ Lỗi kết nối Discord: `{e}`")

        elif cmd == "/guilds":
            await self.bot.send_chat_action(chat_id, "typing")
            try:
                async with DiscordClient(port=self.cdp_port, auto_restart=True) as client:
                    guilds = await client.get_guilds()
                lines = [f"🏰 **Danh sách Server của bạn ({len(guilds)}):**\n"]
                for i, g in enumerate(guilds, 1):
                    role = "👑 Owner" if g.get("owner") else "Member"
                    lines.append(f"{i}. **{g['name']}** `({role})` — ID: `{g['id']}`")
                await self.bot.send_message(chat_id, "\n".join(lines))
            except Exception as e:
                await self.bot.send_message(chat_id, f"❌ Lỗi: `{e}`")

        elif cmd.startswith("/channel"):
            guild_query = " ".join(cmd_parts[1:]) if len(cmd_parts) > 1 else ""
            if not guild_query:
                await self.bot.send_message(chat_id, "⚠️ Hãy nhập tên server, ví dụ: `/channels Cú Đêm AI`")
                return
            await self.bot.send_chat_action(chat_id, "typing")
            try:
                async with DiscordClient(port=self.cdp_port, auto_restart=True) as client:
                    g_info = await client.resolve_guild(guild_query)
                    channels = await client.get_channels(g_info["id"])
                text_channels = [f"#{c['name']}" for c in channels if c.get("type") in (0, 5)][:30]
                await self.bot.send_message(
                    chat_id,
                    f"📋 **Các kênh trong server {g_info['name']}:**\n" + ", ".join(text_channels),
                )
            except Exception as e:
                await self.bot.send_message(chat_id, f"❌ Lỗi: `{e}`")

    async def _handle_natural_language(self, chat_id: int, text: str, msg: Dict[str, Any]) -> None:
        """Parse natural language request and execute action on Discord."""
        await self.bot.send_chat_action(chat_id, "typing")

        # 1. Fetch user's joined guilds for context matching
        guilds = []
        try:
            async with DiscordClient(port=self.cdp_port, auto_restart=True) as client:
                guilds = await client.get_guilds()
        except Exception:
            pass

        # 2. Parse intent via AI
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
                channel_name = "ai-lười-chat-tổng"  # intelligent default

            await self.bot.send_message(
                chat_id,
                f"⏳ **Đang cào dữ liệu và phân tích bằng `{ai_model}`...**\n"
                f"• Server: **{guild_name}**\n"
                f"• Kênh: **#{channel_name}**\n"
                f"• Thời gian: `{since or 'gần đây'}`",
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
                    await self.bot.send_message(chat_id, f"⚠️ Không tìm thấy tin nhắn nào trong kênh #{channel_name} phù hợp với điều kiện lọc.")
                    return

                # Generate Local Summary + AI summary
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

                # Save file to /Users/mac/Documents/report
                from .cli import resolve_report_filepath
                out_file = resolve_report_filepath(None, default_prefix="report", ext="md")
                export_summary_markdown(summary_data, ai_summary_text, out_file, model_name=ai_model)

                # Send summary text to Telegram
                header = (
                    f"📊 **BÁO CÁO TỔNG HỢP: #{c_info['name']}**\n"
                    f"🏰 Server: **{g_info['name']}** | 💬 Tin nhắn: `{len(messages)}`\n"
                    f"🤖 AI Model: `{ai_model}`\n"
                    f"🕒 Khung giờ: `{summary_data.get('first_seen')} ➔ {summary_data.get('last_seen')}`\n\n"
                )
                full_reply = header + (ai_summary_text or "Không có phản hồi AI.")
                await self.bot.send_message(chat_id, full_reply)

                # Send .md document file to Telegram!
                await self.bot.send_chat_action(chat_id, "upload_document")
                await self.bot.send_document(chat_id, file_path=out_file, caption=f"📄 File báo cáo chi tiết: {os.path.basename(out_file)}")

            except Exception as e:
                await self.bot.send_message(chat_id, f"❌ Lỗi xử lý tóm tắt: `{e}`")

        elif action == "read":
            if not channel_name:
                channel_name = "ai-lười-chat-tổng"
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
                    await self.bot.send_message(chat_id, f"⚠️ Không có tin nhắn nào trong kênh #{channel_name}.")
                    return

                sorted_msgs = sorted(messages, key=lambda m: m.get("id", "0"))
                lines = [f"💬 **Tin nhắn mới trong #{c_info['name']} ({g_info['name']}):**\n"]
                for m in sorted_msgs:
                    author = m.get("author", {}).get("global_name") or m.get("author", {}).get("username", "Unknown")
                    ts = parse_timestamp(m.get("timestamp"))
                    content = m.get("content", "")
                    lines.append(f"• **{author}** `({ts})`: {content}")

                await self.bot.send_message(chat_id, "\n".join(lines))
            except Exception as e:
                await self.bot.send_message(chat_id, f"❌ Lỗi đọc tin nhắn: `{e}`")

        elif action == "send_message":
            msg_content = intent.get("message")
            if not channel_name or not msg_content:
                await self.bot.send_message(chat_id, "⚠️ Hãy chỉ định rõ kênh và nội dung muốn gửi (ví dụ: *Gửi 'Chào ae' vào kênh nội quy*).")
                return
            await self.bot.send_chat_action(chat_id, "typing")
            try:
                async with DiscordClient(port=self.cdp_port, auto_restart=True) as client:
                    g_info = await client.resolve_guild(guild_name)
                    c_info = await client.resolve_channel(g_info["id"], channel_name)
                    res = await client.send_message(c_info["id"], content=msg_content)
                await self.bot.send_message(
                    chat_id,
                    f"✔ **Đã gửi thành công vào #{c_info['name']} ({g_info['name']}):**\n\"{msg_content}\" `(ID: {res.get('id')})`",
                )
            except Exception as e:
                await self.bot.send_message(chat_id, f"❌ Lỗi gửi tin nhắn: `{e}`")

        elif action == "autochat_on":
            ch = channel_name or "ai-lười-chat-tổng"
            custom_p = intent.get("prompt") or "Hãy trả lời tự nhiên, thân thiện và ngắn gọn theo đúng chủ đề cuộc trò chuyện."
            await self._handle_slash_command(chat_id, f"/autochat on {ch} {custom_p}", msg)

        elif action == "autochat_off":
            await self._handle_slash_command(chat_id, "/autochat off", msg)

        elif action == "list_guilds":
            await self._handle_slash_command(chat_id, "/guilds", msg)

        elif action == "list_channels":
            await self._handle_slash_command(chat_id, f"/channels {guild_name}", msg)


        elif action == "chat":
            reply_text = intent.get("reply")
            if not reply_text:
                reply_text = "Tôi đang lắng nghe đây! Bạn cần tôi hỗ trợ tóm tắt, đọc/gửi tin nhắn Discord hay có câu hỏi nào không?"
            await self.bot.send_message(chat_id, reply_text, reply_markup=self._get_keyboard())

        else:
            reply_text = intent.get("reply")
            if not reply_text:
                reply_text = f"Chào bạn! Tôi đã nhận được: *\"{text}\"*. Bạn muốn tôi tóm tắt kênh, đọc tin nhắn Discord hay hỗ trợ điều gì?"
            await self.bot.send_message(
                chat_id,
                reply_text,
                reply_markup=self._get_keyboard(),
            )

