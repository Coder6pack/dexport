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

def _ensure_env():
    env_paths = [
        os.path.expanduser("~/.dexport.env"),
        os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"),
    ]
    for p in env_paths:
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            k = k.strip()
                            v = v.strip().strip('"').strip("'")
                            if k and not os.environ.get(k):
                                os.environ[k] = v
            except Exception:
                pass

_ensure_env()



def extract_keywords(texts: List[str], top_n: int = 10) -> List[Tuple[str, int]]:
    """Extract most frequent significant words from user messages."""
    stop_words = {
        "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
        "of", "with", "is", "are", "was", "were", "it", "this", "that", "i",
        "you", "he", "she", "we", "they", "have", "has", "had", "do", "does",
        "did", "be", "been", "being", "from", "by", "as", "about", "can", "will",
        "just", "like", "so", "what", "there", "all", "if", "would", "my", "your"
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
        "user_name": target_user_name or "Everyone",
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
            ref_author = msg["referenced_message"].get("author", {}).get("username", "someone")
            ref_snippet = (msg["referenced_message"].get("content") or "")[:40]
            ref = f" [Replying to {ref_author}: '{ref_snippet}']"
        lines.append(f"[{ts}] {author}: {content}{ref}")

    chat_transcript = "\n".join(lines)
    target_desc = f"from '{target_user_name}'" if target_user_name else "in the discussion"

    return f"""
You are an expert conversation analyst. Review the chat transcript {target_desc} in channel #{channel_name} (Server: {guild_name}) below and write a comprehensive, well-structured SUMMARY REPORT in English.

Required Structure:
1. 📌 **Executive Summary**: Brief 1-2 sentence overview of main topics and objectives.
2. 📋 **Key Discussions & Updates**: Essential points, status reports, or information shared.
3. ❓ **Questions, Issues & Challenges**: Obstacles, questions raised, or blockers encountered.
4. 🎯 **Decisions, Next Steps & Action Items**: Key conclusions, agreed solutions, deadlines, and deliverables.

Chat Transcript:
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
    raise RuntimeError("Gemini returned empty response.")


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
            {"role": "system", "content": "You are a professional AI assistant specializing in coherent, insightful chat summaries."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
    }

    last_err = None
    for attempt in range(2):
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                choices = data.get("choices", [])
                if choices:
                    return choices[0].get("message", {}).get("content", "")
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"API [HTTP {e.code}] ({url}): {err_body}")
        except Exception as e:
            last_err = e
            if attempt == 0:
                continue

    raise RuntimeError(f"Chat completion API error: {last_err}")



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

    raise RuntimeError("Anthropic Claude API returned empty response.")


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
        is_opencode = (
            prov_lower in ("opencode", "opencode-go", "opencode_go")
            or m_lower.startswith("opencode/")
            or m_lower in opencode_models
            or (os.environ.get("OPENCODE_API_KEY") and not os.environ.get("GEMINI_API_KEY") and not os.environ.get("OPENAI_API_KEY") and not base_url)
        )

        if is_opencode and not base_url:
            key = api_key or os.environ.get("OPENCODE_API_KEY") or os.environ.get("OPENCODE_GO_API_KEY") or os.environ.get("OPENCODE_TOKEN")
            if not key:
                return "⚠️ Missing OPENCODE_API_KEY or --api-key flag to use OpenCode Go API."
            actual_model = model.replace("opencode/", "")
            return _call_openai_compatible(prompt, actual_model, key, base_url="https://opencode.ai/zen/go/v1")

        if "gemini" in m_lower and prov_lower != "opencode":
            key = api_key or os.environ.get("GEMINI_API_KEY")
            if not key:
                opencode_key = os.environ.get("OPENCODE_API_KEY") or os.environ.get("OPENCODE_GO_API_KEY")
                if opencode_key:
                    return _call_openai_compatible(prompt, model, opencode_key, base_url="https://opencode.ai/zen/go/v1")
                return "⚠️ Missing GEMINI_API_KEY or --api-key flag to use Gemini."
            return _call_gemini(prompt, model, key)

        elif "claude" in m_lower and not base_url and prov_lower != "opencode":
            key = api_key or os.environ.get("ANTHROPIC_API_KEY")
            if not key:
                opencode_key = os.environ.get("OPENCODE_API_KEY") or os.environ.get("OPENCODE_GO_API_KEY")
                if opencode_key:
                    return _call_openai_compatible(prompt, model, opencode_key, base_url="https://opencode.ai/zen/go/v1")
                return "⚠️ Missing ANTHROPIC_API_KEY or --api-key flag to use Claude directly."
            return _call_anthropic(prompt, model, key)

        elif "deepseek" in m_lower and not base_url and prov_lower != "opencode":
            key = api_key or os.environ.get("DEEPSEEK_API_KEY")
            if not key:
                opencode_key = os.environ.get("OPENCODE_API_KEY") or os.environ.get("OPENCODE_GO_API_KEY")
                if opencode_key:
                    return _call_openai_compatible(prompt, model, opencode_key, base_url="https://opencode.ai/zen/go/v1")
                return "⚠️ Missing DEEPSEEK_API_KEY or --api-key flag to use DeepSeek."
            return _call_openai_compatible(prompt, model, key, base_url="https://api.deepseek.com/v1")

        elif base_url or m_lower.startswith("ollama/") or m_lower in ("llama3", "llama3.2", "qwen2.5", "mistral", "gemma2"):
            endpoint = base_url or os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")
            actual_model = model.replace("ollama/", "")
            return _call_openai_compatible(prompt, actual_model, api_key=api_key or "ollama", base_url=endpoint)

        else:
            key = api_key or os.environ.get("OPENAI_API_KEY")
            if not key:
                opencode_key = os.environ.get("OPENCODE_API_KEY") or os.environ.get("OPENCODE_GO_API_KEY")
                if opencode_key:
                    return _call_openai_compatible(prompt, model, opencode_key, base_url="https://opencode.ai/zen/go/v1")
                return f"⚠️ Missing OPENAI_API_KEY (or OPENCODE_API_KEY) to use model '{model}'."
            endpoint = base_url or "https://api.openai.com/v1"
            return _call_openai_compatible(prompt, model, key, base_url=endpoint)

    except Exception as e:
        return f"❌ AI Error ({model}): {e}"


def render_summary_report(
    console: Console,
    summary_data: Dict[str, Any],
    ai_summary_text: Optional[str] = None,
    model_name: Optional[str] = None,
) -> None:
    """Render summary report on terminal."""
    user_name = summary_data.get("user_name", "Everyone")
    guild_name = summary_data.get("guild_name", "")
    channel_name = summary_data.get("channel_name", "")
    total = summary_data.get("total_messages", 0)

    console.print()
    console.print(
        Panel(
            f"[bold cyan]Target:[/bold cyan] [bold yellow]{user_name}[/bold yellow]  |  "
            f"[bold cyan]Channel:[/bold cyan] [green]#{channel_name}[/green] ({guild_name})  |  "
            f"[bold cyan]Total Messages:[/bold cyan] [yellow]{total}[/yellow]",
            title="📊 [bold magenta]DISCUSSION SUMMARY REPORT[/bold magenta]",
            border_style="magenta",
        )
    )

    if total == 0:
        console.print(f"[yellow]No messages found matching filtering criteria.[/yellow]\n")
        return

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", justify="right")
    grid.add_column(style="white")

    grid.add_row("Time Range:", f"{summary_data.get('first_seen')} ➔ {summary_data.get('last_seen')}")
    grid.add_row("Shared Links:", f"[green]{len(summary_data.get('links', []))}[/green] links")
    grid.add_row("Attachments:", f"[green]{len(summary_data.get('attachments', []))}[/green] files")

    keywords = summary_data.get("top_keywords", [])
    if keywords:
        kw_str = ", ".join([f"[yellow]{w}[/yellow] ({c})" for w, c in keywords])
        grid.add_row("Top Keywords:", kw_str)

    console.print(Panel(grid, title="📈 [bold cyan]Activity Statistics[/bold cyan]", border_style="cyan"))

    if ai_summary_text:
        console.print()
        console.print(
            Panel(
                Markdown(ai_summary_text),
                title=f"🤖 [bold green]AI Summary ({model_name or 'AI'})[/bold green]",
                border_style="green",
            )
        )
    else:
        console.print(
            "\n[dim italic]💡 Tip: Set GEMINI_API_KEY / OPENCODE_API_KEY or pass --model and --api-key to activate AI summarization.[/dim italic]"
        )

    links = summary_data.get("links", [])
    if links:
        console.print()
        console.print("[bold cyan]🔗 Shared Links:[/bold cyan]")
        for url in links[:10]:
            console.print(f"  • [blue underline]{url}[/blue underline]")
        if len(links) > 10:
            console.print(f"  [dim]...and {len(links) - 10} more links.[/dim]")

    console.print()


def export_summary_markdown(
    summary_data: Dict[str, Any],
    ai_summary_text: Optional[str],
    output_path: str,
    model_name: Optional[str] = None,
) -> None:
    """Save full summary report + chat history to a Markdown file."""
    user_name = summary_data.get("user_name", "Everyone")
    guild_name = summary_data.get("guild_name", "")
    channel_name = summary_data.get("channel_name", "")
    total = summary_data.get("total_messages", 0)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        f"# Discussion Summary Report: {user_name}\n\n",
        f"- **Target:** {user_name}\n",
        f"- **Server:** {guild_name}\n",
        f"- **Channel:** #{channel_name}\n",
        f"- **Exported At:** {now_str}\n",
        f"- **Total Messages:** {total}\n",
        f"- **Active Timeline:** {summary_data.get('first_seen')} ➔ {summary_data.get('last_seen')}\n\n",
        "---\n\n",
    ]

    if ai_summary_text:
        lines.append(f"## 🤖 AI Summary ({model_name or 'AI'})\n\n")
        lines.append(f"{ai_summary_text}\n\n")
        lines.append("---\n\n")

    links = summary_data.get("links", [])
    if links:
        lines.append("## 🔗 Shared Links\n\n")
        for link in links:
            lines.append(f"- {link}\n")
        lines.append("\n---\n\n")

    attachments = summary_data.get("attachments", [])
    if attachments:
        lines.append("## 📎 Attachments & Media\n\n")
        for att in attachments:
            lines.append(f"- [{att['filename']}]({att['url']}) ({att['size_kb']:.1f} KB)\n")
        lines.append("\n---\n\n")

    lines.append("## 💬 Complete Chronological Message History\n\n")
    for msg in summary_data.get("messages", []):
        author = msg.get("author", {}).get("global_name") or msg.get("author", {}).get("username", "Unknown")
        ts = parse_timestamp(msg.get("timestamp"))
        msg_id = msg.get("id", "")
        content = msg.get("content", "")

        lines.append(f"**[{ts}] {author}** `[ID: {msg_id}]`  \n")
        if msg.get("referenced_message"):
            ref_author = msg["referenced_message"].get("author", {}).get("username", "someone")
            ref_content = msg["referenced_message"].get("content", "")
            lines.append(f"> **Replying to {ref_author}:** {ref_content}\n")
        if content:
            lines.append(f"{content}\n")
        lines.append("\n")

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
