"""
Message analysis and multi-model AI summarization module.
Supports: Google Gemini, OpenAI, Anthropic Claude, DeepSeek, Ollama / Local LLM, Groq, OpenRouter.
"""

from collections import Counter
from datetime import datetime
import json
import os
import re
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional, Tuple
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.markdown import Markdown

from .exporter import parse_timestamp


def extract_keywords(texts: List[str], top_n: int = 10) -> List[Tuple[str, int]]:
    """Extract most frequent significant words from user messages."""
    stop_words = {
        "và", "là", "của", "cho", "có", "được", "với", "trong", "để", "thì",
        "này", "đó", "không", "một", "các", "những", "người", "khi", "tại",
        "đã", "sẽ", "đang", "như", "nhưng", "hoặc", "nếu", "mà", "lại", "ra",
        "vào", "lên", "xuống", "đi", "đến", "qua", "làm", "gì", "sao", "ai",
        "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
        "of", "with", "is", "are", "was", "were", "it", "this", "that", "i", "you", "he", "she", "we", "they"
    }
    words = []
    for text in texts:
        cleaned = re.sub(r"https?://\S+", "", text)
        cleaned = re.sub(r"<:[^:]+:\d+>", "", cleaned)
        tokens = re.findall(r"\b[a-zA-Z0-9_\u00C0-\u1EF9]{3,}\b", cleaned.lower())
        for t in tokens:
            if t not in stop_words and not t.isdigit():
                words.append(t)

    return Counter(words).most_common(top_n)


def generate_local_summary(
    messages: List[Dict[str, Any]],
    target_user_name: Optional[str],
    guild_name: str,
    channel_name: str,
) -> Dict[str, Any]:
    """Generate a structured analytical summary without external APIs."""
    if not messages:
        return {
            "total_messages": 0,
            "first_seen": "",
            "last_seen": "",
            "links": [],
            "attachments": [],
            "top_keywords": [],
            "timeline": [],
        }

    sorted_msgs = sorted(messages, key=lambda m: m.get("id", "0"))
    total = len(sorted_msgs)
    first_seen = parse_timestamp(sorted_msgs[0].get("timestamp"))
    last_seen = parse_timestamp(sorted_msgs[-1].get("timestamp"))

    links = []
    attachments = []
    texts = []

    for msg in sorted_msgs:
        content = msg.get("content", "")
        if content:
            texts.append(content)
            found_urls = re.findall(r"https?://[^\s<>]+", content)
            links.extend(found_urls)

        for att in msg.get("attachments", []):
            attachments.append({
                "filename": att.get("filename", "file"),
                "url": att.get("url", ""),
                "size_kb": att.get("size", 0) / 1024,
            })

    top_keywords = extract_keywords(texts, top_n=8)

    return {
        "user_name": target_user_name or "Tất cả mọi người",
        "guild_name": guild_name,
        "channel_name": channel_name,
        "total_messages": total,
        "first_seen": first_seen,
        "last_seen": last_seen,
        "links": list(dict.fromkeys(links)),
        "attachments": attachments,
        "top_keywords": top_keywords,
        "messages": sorted_msgs,
    }


# =============================================================================
# Multi-Model AI Dispatcher
# =============================================================================

def build_summary_prompt(
    messages: List[Dict[str, Any]],
    target_user_name: Optional[str],
    guild_name: str,
    channel_name: str,
) -> str:
    """Construct a clean, structured transcript prompt for the AI."""
    sorted_msgs = sorted(messages, key=lambda m: m.get("id", "0"))
    lines = []
    for msg in sorted_msgs:
        ts = parse_timestamp(msg.get("timestamp"))
        author = msg.get("author", {}).get("global_name") or msg.get("author", {}).get("username", "Unknown")
        content = msg.get("content", "")
        ref = ""
        if msg.get("referenced_message"):
            ref_author = msg["referenced_message"].get("author", {}).get("username", "ai đó")
            ref_snippet = (msg["referenced_message"].get("content") or "")[:40]
            ref = f" [Trả lời {ref_author}: '{ref_snippet}']"
        lines.append(f"[{ts}] {author}: {content}{ref}")

    chat_transcript = "\n".join(lines)
    target_desc = f"của '{target_user_name}'" if target_user_name else "trong cuộc thảo luận"

    return f"""
Bạn là một chuyên gia tóm tắt và phân tích dữ liệu chat. Hãy đọc toàn bộ nội dung chat {target_desc} tại kênh #{channel_name} (Server: {guild_name}) dưới đây và viết một bản BÁO CÁO TỔNG HỢP chi tiết, súc tích bằng tiếng Việt.

Cấu trúc báo cáo yêu cầu:
1. 📌 **Executive Summary (Tổng quan ngắn gọn 1-2 câu)**: Nội dung và mục đích chính của các cuộc trao đổi.
2. 📋 **Các công việc, báo cáo hoặc thông tin chính** được chia sẻ/thực hiện.
3. ❓ **Các câu hỏi, yêu cầu, thắc mắc hoặc khó khăn** được đưa ra.
4. 🎯 **Các kết luận, thống nhất, hạn chót (deadlines) hoặc hành động tiếp theo (Action Items)**.

Nội dung chat transcript:
\"\"\"
{chat_transcript}
\"\"\"
"""


DEFAULT_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 dexport/0.1.0"


def _call_gemini(prompt: str, model: str, api_key: str) -> str:
    """Call Google Gemini API."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3},
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": DEFAULT_USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            candidates = data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts:
                    return parts[0].get("text", "")
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Gemini API [HTTP {e.code}]: {err_body}")
    raise RuntimeError("Gemini không trả về kết quả.")


def _call_openai_compatible(
    prompt: str,
    model: str,
    api_key: Optional[str],
    base_url: str = "https://api.openai.com/v1",
) -> str:
    """Call OpenAI / OpenCode Go / DeepSeek / Ollama / Groq / OpenRouter compatible chat completion endpoint."""
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key.strip()}"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Bạn là trợ lý AI chuyên tóm tắt và phân tích nội dung chat chuyên nghiệp, mạch lạc."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            choices = data.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "")
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"API [HTTP {e.code}] ({url}): {err_body}")

    raise RuntimeError("API chat completion không trả về kết quả.")


def _call_anthropic(prompt: str, model: str, api_key: str) -> str:
    """Call Anthropic Claude Messages API."""
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key.strip(),
        "anthropic-version": "2023-06-01",
        "User-Agent": DEFAULT_USER_AGENT,
    }
    payload = {
        "model": model,
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            content = data.get("content", [])
            if content and isinstance(content, list):
                for part in content:
                    if part.get("type") == "text":
                        return part.get("text", "")
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Claude API [HTTP {e.code}]: {err_body}")

    raise RuntimeError("Anthropic Claude API không trả về kết quả.")


def call_ai_summary(
    messages: List[Dict[str, Any]],
    target_user_name: Optional[str],
    guild_name: str,
    channel_name: str,
    model: str = "gemini-2.5-flash",
    provider: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> Optional[str]:
    """
    Universal multi-model AI summarizer dispatcher.
    Auto-detects provider based on model name, provider argument, or environment variables:
    - OpenCode Go: opencode/*, deepseek-v4*, kimi-*, qwen3*, glm-5*, minimax-m*, etc. (OPENCODE_API_KEY -> https://opencode.ai/zen/go/v1)
    - Google Gemini: gemini-* (GEMINI_API_KEY)
    - OpenAI: gpt-*, o1-*, o3-* (OPENAI_API_KEY)
    - Anthropic Claude: claude-* (ANTHROPIC_API_KEY)
    - DeepSeek: deepseek-* (DEEPSEEK_API_KEY)
    - Ollama / Local: ollama/*, llama*, qwen*, mistral* (OLLAMA_BASE_URL)
    """
    if not messages:
        return None

    prompt = build_summary_prompt(messages, target_user_name, guild_name, channel_name)
    m_lower = model.lower()
    prov_lower = (provider or "").lower()

    # OpenCode Go specific models
    opencode_models = (
        "deepseek-v4-flash", "deepseek-v4-pro", "deepseek-v4",
        "kimi-k3", "kimi-k2.7-code", "kimi-k2.6", "kimi-k2.5",
        "glm-5.3", "glm-5.3-flash", "glm-5.2", "glm-5.1", "glm-5",
        "qwen3.8-max", "qwen3.7-max", "qwen3.7-plus", "qwen3.6-plus", "qwen3.5-plus",
        "mimo-v2.5-pro", "mimo-v2.5", "mimo-v2-pro", "mimo-v2-omni",
        "gpt-5.6-luna", "grok-4.6", "grok-4.5", "longcat-2.0",
        "minimax-m3", "minimax-m2.7", "minimax-m2.5", "hy3", "hy3-preview"
    )

    try:
        # Check if user wants OpenCode Go or provided OpenCode Go model
        is_opencode = (
            prov_lower in ("opencode", "opencode-go", "opencode_go")
            or m_lower.startswith("opencode/")
            or m_lower in opencode_models
            or (os.environ.get("OPENCODE_API_KEY") and not os.environ.get("GEMINI_API_KEY") and not os.environ.get("OPENAI_API_KEY") and not base_url)
        )

        if is_opencode and not base_url:
            key = api_key or os.environ.get("OPENCODE_API_KEY") or os.environ.get("OPENCODE_GO_API_KEY") or os.environ.get("OPENCODE_TOKEN")
            if not key:
                return "⚠️ Cần OPENCODE_API_KEY hoặc cờ --api-key để sử dụng OpenCode Go API."
            actual_model = model.replace("opencode/", "")
            return _call_openai_compatible(prompt, actual_model, key, base_url="https://opencode.ai/zen/go/v1")

        # 2. Google Gemini
        if "gemini" in m_lower and prov_lower != "opencode":
            key = api_key or os.environ.get("GEMINI_API_KEY")
            if not key:
                opencode_key = os.environ.get("OPENCODE_API_KEY") or os.environ.get("OPENCODE_GO_API_KEY")
                if opencode_key:
                    return _call_openai_compatible(prompt, model, opencode_key, base_url="https://opencode.ai/zen/go/v1")
                return "⚠️ Cần GEMINI_API_KEY hoặc cờ --api-key để sử dụng Gemini."
            return _call_gemini(prompt, model, key)

        # 3. Anthropic Claude (direct)
        elif "claude" in m_lower and not base_url and prov_lower != "opencode":
            key = api_key or os.environ.get("ANTHROPIC_API_KEY")
            if not key:
                opencode_key = os.environ.get("OPENCODE_API_KEY") or os.environ.get("OPENCODE_GO_API_KEY")
                if opencode_key:
                    return _call_openai_compatible(prompt, model, opencode_key, base_url="https://opencode.ai/zen/go/v1")
                return "⚠️ Cần ANTHROPIC_API_KEY hoặc cờ --api-key để sử dụng Claude (hoặc dùng qua OpenCode Go)."
            return _call_anthropic(prompt, model, key)

        # 4. DeepSeek (direct)
        elif "deepseek" in m_lower and not base_url and prov_lower != "opencode":
            key = api_key or os.environ.get("DEEPSEEK_API_KEY")
            if not key:
                opencode_key = os.environ.get("OPENCODE_API_KEY") or os.environ.get("OPENCODE_GO_API_KEY")
                if opencode_key:
                    return _call_openai_compatible(prompt, model, opencode_key, base_url="https://opencode.ai/zen/go/v1")
                return "⚠️ Cần DEEPSEEK_API_KEY hoặc cờ --api-key để sử dụng DeepSeek."
            return _call_openai_compatible(prompt, model, key, base_url="https://api.deepseek.com/v1")

        # 5. Ollama / Local LLM
        elif base_url or m_lower.startswith("ollama/") or m_lower in ("llama3", "llama3.2", "qwen2.5", "mistral", "gemma2"):
            endpoint = base_url or os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")
            actual_model = model.replace("ollama/", "")
            return _call_openai_compatible(prompt, actual_model, api_key=api_key or "ollama", base_url=endpoint)

        # 6. Default / OpenAI compatible
        else:
            key = api_key or os.environ.get("OPENAI_API_KEY")
            if not key:
                opencode_key = os.environ.get("OPENCODE_API_KEY") or os.environ.get("OPENCODE_GO_API_KEY")
                if opencode_key:
                    return _call_openai_compatible(prompt, model, opencode_key, base_url="https://opencode.ai/zen/go/v1")
                return f"⚠️ Cần OPENAI_API_KEY (hoặc OPENCODE_API_KEY) để sử dụng model '{model}'."
            endpoint = base_url or "https://api.openai.com/v1"
            return _call_openai_compatible(prompt, model, key, base_url=endpoint)

    except Exception as e:
        return f"❌ Lỗi khi gọi AI ({model}): {e}"




def render_summary_report(
    console: Console,
    summary_data: Dict[str, Any],
    ai_summary_text: Optional[str] = None,
    model_name: Optional[str] = None,
) -> None:
    """Render summary report on terminal."""
    user_name = summary_data.get("user_name", "Tất cả mọi người")
    guild_name = summary_data.get("guild_name", "")
    channel_name = summary_data.get("channel_name", "")
    total = summary_data.get("total_messages", 0)

    console.print()
    console.print(
        Panel(
            f"[bold cyan]Đối tượng:[/bold cyan] [bold yellow]{user_name}[/bold yellow]  |  "
            f"[bold cyan]Kênh:[/bold cyan] [green]#{channel_name}[/green] ({guild_name})  |  "
            f"[bold cyan]Số tin nhắn:[/bold cyan] [yellow]{total}[/yellow]",
            title="📊 [bold magenta]BÁO CÁO TỔNG HỢP TIN NHẮN[/bold magenta]",
            border_style="magenta",
        )
    )

    if total == 0:
        console.print(f"[yellow]Không tìm thấy tin nhắn nào trong phạm vi bộ lọc.[/yellow]\n")
        return

    # Info grid
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", justify="right")
    grid.add_column(style="white")

    grid.add_row("Khung thời gian:", f"{summary_data.get('first_seen')} ➔ {summary_data.get('last_seen')}")
    grid.add_row("Tổng số link gửi:", f"[green]{len(summary_data.get('links', []))}[/green] links")
    grid.add_row("Số file đính kèm:", f"[green]{len(summary_data.get('attachments', []))}[/green] files")

    keywords = summary_data.get("top_keywords", [])
    if keywords:
        kw_str = ", ".join([f"[yellow]{w}[/yellow] ({c})" for w, c in keywords])
        grid.add_row("Từ khóa nổi bật:", kw_str)

    console.print(Panel(grid, title="📈 [bold cyan]Thống kê hoạt động[/bold cyan]", border_style="cyan"))

    # AI Summary
    if ai_summary_text:
        console.print()
        console.print(
            Panel(
                Markdown(ai_summary_text),
                title=f"🤖 [bold green]AI Tóm Tắt ({model_name or 'AI'})[/bold green]",
                border_style="green",
            )
        )
    else:
        console.print(
            "\n[dim italic]💡 Mẹo: Set GEMINI_API_KEY / OPENAI_API_KEY hoặc truyền --model và --api-key để kích hoạt AI tóm tắt.[/dim italic]"
        )

    # Show links if any
    links = summary_data.get("links", [])
    if links:
        console.print()
        console.print("[bold cyan]🔗 Các liên kết đã chia sẻ:[/bold cyan]")
        for url in links[:10]:
            console.print(f"  • [blue underline]{url}[/blue underline]")
        if len(links) > 10:
            console.print(f"  [dim]...và {len(links) - 10} liên kết khác.[/dim]")

    console.print()


def export_summary_markdown(
    summary_data: Dict[str, Any],
    ai_summary_text: Optional[str],
    output_path: str,
    model_name: Optional[str] = None,
) -> None:
    """Save full summary report + chat history to a Markdown file."""
    user_name = summary_data.get("user_name", "Tất cả mọi người")
    guild_name = summary_data.get("guild_name", "")
    channel_name = summary_data.get("channel_name", "")
    total = summary_data.get("total_messages", 0)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        f"# Báo cáo tổng hợp tin nhắn: {user_name}\n\n",
        f"- **Đối tượng:** {user_name}\n",
        f"- **Server:** {guild_name}\n",
        f"- **Kênh:** #{channel_name}\n",
        f"- **Thời gian xuất:** {now_str}\n",
        f"- **Tổng số tin nhắn:** {total}\n",
        f"- **Khung giờ hoạt động:** {summary_data.get('first_seen')} ➔ {summary_data.get('last_seen')}\n\n",
        "---\n\n",
    ]

    if ai_summary_text:
        lines.append(f"## 🤖 Tóm tắt thông minh bởi AI ({model_name or 'AI'})\n\n")
        lines.append(f"{ai_summary_text}\n\n")
        lines.append("---\n\n")

    # Links
    links = summary_data.get("links", [])
    if links:
        lines.append("## 🔗 Danh sách liên kết đã chia sẻ\n\n")
        for link in links:
            lines.append(f"- {link}\n")
        lines.append("\n---\n\n")

    # Attachments
    attachments = summary_data.get("attachments", [])
    if attachments:
        lines.append("## 📎 File & Ảnh đính kèm\n\n")
        for att in attachments:
            lines.append(f"- [{att['filename']}]({att['url']}) ({att['size_kb']:.1f} KB)\n")
        lines.append("\n---\n\n")

    # Detailed Messages
    lines.append("## 💬 Chi tiết toàn bộ tin nhắn theo trình tự thời gian\n\n")
    for msg in summary_data.get("messages", []):
        author = msg.get("author", {}).get("global_name") or msg.get("author", {}).get("username", "Unknown")
        ts = parse_timestamp(msg.get("timestamp"))
        msg_id = msg.get("id", "")
        content = msg.get("content", "")

        lines.append(f"**[{ts}] {author}** `[ID: {msg_id}]`  \n")
        if msg.get("referenced_message"):
            ref_author = msg["referenced_message"].get("author", {}).get("username", "ai đó")
            ref_content = msg["referenced_message"].get("content", "")
            lines.append(f"> **Trả lời {ref_author}:** {ref_content}\n")
        if content:
            lines.append(f"{content}\n")
        lines.append("\n")

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
