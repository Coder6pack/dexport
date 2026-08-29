"""
Autonomous AI Auto-Chat Persona Module for dexport.
Simulates natural human typing, follows user-defined prompts, and handles auto-reply / engagement.
"""

import asyncio
from datetime import datetime
import json
import logging
import os
import random
from typing import Any, Callable, Dict, List, Optional
import urllib.parse

from .cdp import DEFAULT_PORT
from .client import DiscordClient
from .summarizer import _call_gemini, _call_openai_compatible

logger = logging.getLogger("dexport.autochat")


DEFAULT_PERSONA_PROMPT = (
    "Hãy đóng vai một thành viên thân thiện, vui vẻ và nhiệt tình trong nhóm. "
    "Nói chuyện tự nhiên, ngắn gọn (1-2 câu), xưng hô anh/em/ae/bro, "
    "trả lời đúng trọng tâm câu hỏi nếu có ai hỏi, dùng từ ngữ đời thường của cộng đồng công nghệ/chat Discord."
)


async def generate_persona_reply(
    messages: List[Dict[str, Any]],
    current_user: Dict[str, Any],
    channel_name: str,
    guild_name: str,
    custom_prompt: str = DEFAULT_PERSONA_PROMPT,
    model: str = "deepseek-v4-flash",
    api_key: Optional[str] = None,
) -> Optional[str]:
    """
    Generate a natural human response based on conversation context and custom persona prompt.
    """
    if not messages:
        return None

    my_id = str(current_user.get("id", ""))
    my_username = current_user.get("username", "")
    my_global_name = current_user.get("global_name") or my_username

    # Format recent conversation context
    context_lines = []
    for m in messages[-12:]:
        author = m.get("author", {})
        author_name = author.get("global_name") or author.get("username", "Unknown")
        author_id = str(author.get("id", ""))
        is_me = (author_id == my_id)
        tag = " (BẠN)" if is_me else ""
        content = m.get("content", "").strip()

        # Check attachments
        if m.get("attachments"):
            content += f" [File/Ảnh: {len(m['attachments'])}]"

        # Check reply
        ref = m.get("referenced_message")
        ref_text = ""
        if ref:
            ref_author = ref.get("author", {}).get("global_name") or ref.get("author", {}).get("username", "Ai đó")
            ref_text = f" (Trả lời {ref_author}: '{ref.get('content', '')[:30]}...')"

        if content:
            context_lines.append(f"{author_name}{tag}{ref_text}: {content}")

    conversation_text = "\n".join(context_lines)

    system_instruction = f"""
Bạn đang đóng vai tài khoản '{my_global_name}' (@{my_username}) trên Discord trong server '{guild_name}', kênh '#{channel_name}'.
Nhiệm vụ của bạn là tiếp tục cuộc trò chuyện và viết 1 câu trả lời tự nhiên theo đúng chỉ đạo dưới đây.

🎯 CHỈ ĐẠO & PHONG CÁCH CỦA BẠN:
{custom_prompt}

📜 CÁC NGUYÊN TẮC BẮT BUỘC:
1. Viết tự nhiên bằng tiếng Việt, ngắn gọn (chỉ 1 đến 2 câu), giống người dùng Discord thật đang chat.
2. TUYỆT ĐỐI KHÔNG xưng là AI, trợ lý ảo hay dùng giọng văn robot sách vở/khách sáo.
3. Không lặp lại y nguyên câu nói của người khác.
4. Nếu thấy đoạn hội thoại không cần thiết phải nói gì hoặc không liên quan, hãy trả về đúng từ: [PASS]
5. Chỉ trả về duy nhất nội dung câu chat cần gửi, không thêm dấu ngoặc kép hay lời dẫn.
"""

    prompt = f"Ngữ cảnh các tin nhắn gần nhất:\n{conversation_text}\n\nCâu trả lời của bạn ({my_global_name}):"

    try:
        m_lower = model.lower()
        key = api_key or os.environ.get("OPENCODE_API_KEY") or os.environ.get("OPENCODE_GO_API_KEY") or os.environ.get("GEMINI_API_KEY") or os.environ.get("OPENAI_API_KEY")

        if os.environ.get("OPENCODE_API_KEY") or os.environ.get("OPENCODE_GO_API_KEY"):
            opencode_key = os.environ.get("OPENCODE_API_KEY") or os.environ.get("OPENCODE_GO_API_KEY")
            reply = _call_openai_compatible(f"{system_instruction}\n\n{prompt}", model, opencode_key, base_url="https://opencode.ai/zen/go/v1")
        elif "gemini" in m_lower and os.environ.get("GEMINI_API_KEY"):
            reply = _call_gemini(f"{system_instruction}\n\n{prompt}", model, os.environ["GEMINI_API_KEY"])
        else:
            reply = _call_openai_compatible(f"{system_instruction}\n\n{prompt}", "gpt-4o-mini", os.environ.get("OPENAI_API_KEY", key))

        reply = reply.strip().strip('"').strip("'")
        if not reply or reply == "[PASS]" or reply.lower().startswith("[pass]"):
            return None

        return reply
    except Exception as e:
        logger.error(f"Lỗi tạo câu trả lời AutoChat: {e}")
        return None


class AutoChatSession:
    """Session running auto-chat loop in a specific Discord channel."""

    def __init__(
        self,
        guild_name: str,
        channel_name: str,
        prompt: str = DEFAULT_PERSONA_PROMPT,
        mentions_only: bool = True,
        model: str = "deepseek-v4-flash",
        cooldown: float = 15.0,
        port: int = DEFAULT_PORT,
        on_message_sent: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ):
        self.guild_name = guild_name
        self.channel_name = channel_name
        self.prompt = prompt
        self.mentions_only = mentions_only
        self.model = model
        self.cooldown = cooldown
        self.port = port
        self.on_message_sent = on_message_sent

        self.is_running = False
        self.last_sent_time = 0.0
        self.last_seen_msg_id = "0"
        self.stats = {"sent_count": 0, "start_time": None}

    async def start(self) -> None:
        """Start the polling and auto-reply loop."""
        self.is_running = True
        self.stats["start_time"] = datetime.now()

        async with DiscordClient(port=self.port, auto_restart=True) as client:
            user = await client.get_current_user()
            my_id = str(user.get("id", ""))
            my_username = user.get("username", "").lower()
            my_global = (user.get("global_name") or "").lower()

            g_info = await client.resolve_guild(self.guild_name)
            c_info = await client.resolve_channel(g_info["id"], self.channel_name)
            channel_id = c_info["id"]

            # Fetch initial latest message
            initial_msgs = await client.get_messages(channel_id, limit=3)
            if initial_msgs:
                self.last_seen_msg_id = initial_msgs[0]["id"]

            logger.info(f"AutoChat bắt đầu chạy trong #{c_info['name']} ({g_info['name']})...")

            while self.is_running:
                try:
                    await asyncio.sleep(3.0)

                    # Poll for new messages
                    new_msgs = await client.get_messages(channel_id, limit=10, after=self.last_seen_msg_id)
                    if not new_msgs:
                        continue

                    # Sort oldest to newest
                    sorted_new = sorted(new_msgs, key=lambda m: int(m.get("id", "0")))
                    self.last_seen_msg_id = sorted_new[-1]["id"]

                    # Filter out messages from myself and bots
                    candidate_msgs = []
                    for m in sorted_new:
                        author = m.get("author", {})
                        if str(author.get("id")) == my_id:
                            continue
                        if author.get("bot"):
                            continue
                        candidate_msgs.append(m)

                    if not candidate_msgs:
                        continue

                    # Check trigger condition
                    latest_msg = candidate_msgs[-1]
                    trigger_reply_to_id = None
                    should_respond = False

                    # Check if mentioned or replied to me
                    content_lower = latest_msg.get("content", "").lower()
                    ref = latest_msg.get("referenced_message")
                    is_reply_to_me = bool(ref and str(ref.get("author", {}).get("id")) == my_id)
                    is_mention = bool(
                        my_id in latest_msg.get("content", "")
                        or (my_username and f"@{my_username}" in content_lower)
                        or (my_global and my_global in content_lower)
                    )

                    if is_reply_to_me or is_mention:
                        should_respond = True
                        trigger_reply_to_id = latest_msg["id"]
                    elif not self.mentions_only:
                        # General chat engagement: check cooldown
                        now = asyncio.get_event_loop().time()
                        if now - self.last_sent_time >= self.cooldown:
                            should_respond = True

                    if not should_respond:
                        continue

                    # Check cooldown
                    now = asyncio.get_event_loop().time()
                    if now - self.last_sent_time < self.cooldown:
                        continue

                    # Get recent context
                    all_recent = await client.get_messages(channel_id, limit=15)
                    all_recent_sorted = sorted(all_recent, key=lambda m: int(m.get("id", "0")))

                    # Generate AI Persona Response
                    response_text = await generate_persona_reply(
                        messages=all_recent_sorted,
                        current_user=user,
                        channel_name=c_info["name"],
                        guild_name=g_info["name"],
                        custom_prompt=self.prompt,
                        model=self.model,
                    )

                    if not response_text:
                        continue

                    # Simulate realistic human typing
                    await client.send_typing(channel_id)
                    typing_duration = min(max(len(response_text) * 0.04 + random.uniform(1.5, 3.0), 2.0), 6.0)
                    await asyncio.sleep(typing_duration)

                    # Send message
                    res = await client.send_message(
                        channel_id=channel_id,
                        content=response_text,
                        reply_to_id=trigger_reply_to_id,
                    )

                    self.last_sent_time = asyncio.get_event_loop().time()
                    self.stats["sent_count"] += 1

                    sent_info = {
                        "guild_name": g_info["name"],
                        "channel_name": c_info["name"],
                        "content": response_text,
                        "reply_to": trigger_reply_to_id,
                        "timestamp": datetime.now().strftime("%H:%M:%S"),
                        "message_id": res.get("id"),
                    }

                    logger.info(f"AutoChat sent: {response_text}")

                    # Trigger notification callback (e.g. to Telegram)
                    if self.on_message_sent:
                        try:
                            if asyncio.iscoroutinefunction(self.on_message_sent):
                                await self.on_message_sent(sent_info)
                            else:
                                self.on_message_sent(sent_info)
                        except Exception as cb_err:
                            logger.debug(f"Callback error: {cb_err}")

                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Lỗi trong vòng lặp AutoChat: {e}")
                    await asyncio.sleep(5.0)

    def stop(self) -> None:
        """Stop the session."""
        self.is_running = False
