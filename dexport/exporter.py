"""
Message rendering with Rich and export utilities (Markdown, JSON).
"""

from datetime import datetime
import json
import os
from typing import Any, Dict, List, Optional
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.markdown import Markdown


def parse_timestamp(iso_str: Optional[str]) -> str:
    """Convert ISO 8601 timestamp to human-friendly local datetime string."""
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        local_dt = dt.astimezone()
        return local_dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return iso_str


def render_messages(
    console: Console,
    messages: List[Dict[str, Any]],
    guild_name: str,
    channel_name: str,
    limit: int = 50,
) -> None:
    """Render messages in a stylish Rich layout on terminal."""
    if not messages:
        console.print(f"[yellow]No messages found in channel #{channel_name}.[/yellow]")
        return

    sorted_msgs = sorted(messages, key=lambda m: m.get("id", "0"))

    console.print()
    console.print(
        Panel(
            f"[bold cyan]Server:[/bold cyan] [white]{guild_name}[/white]  |  "
            f"[bold cyan]Channel:[/bold cyan] [green]#{channel_name}[/green]  |  "
            f"[bold cyan]Message Count:[/bold cyan] [yellow]{len(sorted_msgs)}[/yellow]",
            title="💬 [bold magenta]dexport Discord Chat[/bold magenta]",
            border_style="cyan",
        )
    )

    for msg in sorted_msgs:
        author = msg.get("author", {})
        username = author.get("global_name") or author.get("username", "Unknown")
        user_tag = f"@{author.get('username')}" if author.get("username") else ""
        is_bot = author.get("bot", False)
        bot_badge = " [bold blue][BOT][/bold blue]" if is_bot else ""
        timestamp = parse_timestamp(msg.get("timestamp"))
        msg_id = msg.get("id", "")
        content = msg.get("content", "")

        header = Text()
        header.append(f"{username}", style="bold yellow")
        if user_tag:
            header.append(f" ({user_tag})", style="dim")
        if bot_badge:
            header.append(" [BOT]", style="bold blue")
        header.append(f" • {timestamp}", style="dim")
        header.append(f" (ID: {msg_id})", style="dim cyan")

        console.print(header)

        if msg.get("referenced_message"):
            ref = msg["referenced_message"]
            ref_author = ref.get("author", {}).get("global_name") or ref.get("author", {}).get("username", "Unknown")
            ref_snippet = (ref.get("content") or "[Attachment/Embed]")[:60]
            if len(ref.get("content") or "") > 60:
                ref_snippet += "..."
            console.print(f"  [dim]↩ Replying to {ref_author}: {ref_snippet}[/dim]")

        if content:
            console.print(f"  {content}")

        attachments = msg.get("attachments", [])
        for att in attachments:
            fname = att.get("filename", "file")
            size_kb = att.get("size", 0) / 1024
            url = att.get("url", "")
            console.print(f"  [dim blue]📎 {fname} ({size_kb:.1f} KB): {url}[/dim blue]")

        embeds = msg.get("embeds", [])
        for emb in embeds:
            emb_title = emb.get("title", "")
            emb_desc = emb.get("description", "")
            if emb_title or emb_desc:
                console.print(f"  [italic dim]📦 Embed: {emb_title} - {emb_desc[:80]}[/italic dim]")

        reactions = msg.get("reactions", [])
        if reactions:
            react_str = "  "
            for r in reactions:
                emoji = r.get("emoji", {}).get("name", "")
                count = r.get("count", 0)
                react_str += f"[dim][{emoji} {count}][/dim] "
            console.print(react_str)

        console.print()


def export_markdown(
    messages: List[Dict[str, Any]],
    guild_name: str,
    channel_name: str,
    output_path: str,
) -> None:
    """Export messages to clean GitHub-flavored Markdown."""
    sorted_msgs = sorted(messages, key=lambda m: m.get("id", "0"))
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        f"# Chat History: #{channel_name}\n\n",
        f"**Server:** {guild_name}  \n",
        f"**Channel:** #{channel_name}  \n",
        f"**Exported At:** {now_str}  \n",
        f"**Total Messages:** {len(sorted_msgs)}  \n",
        "\n---\n\n",
    ]

    for msg in sorted_msgs:
        author = msg.get("author", {})
        username = author.get("global_name") or author.get("username", "Unknown")
        user_tag = f"@{author.get('username')}" if author.get("username") else ""
        timestamp = parse_timestamp(msg.get("timestamp"))
        msg_id = msg.get("id", "")
        content = msg.get("content", "")

        lines.append(f"### {username} ({user_tag}) — *{timestamp}* `[ID: {msg_id}]`\n\n")

        if msg.get("referenced_message"):
            ref = msg["referenced_message"]
            ref_author = ref.get("author", {}).get("global_name") or ref.get("author", {}).get("username", "Unknown")
            ref_content = ref.get("content", "")
            lines.append(f"> **Replying to {ref_author}:** {ref_content}\n\n")

        if content:
            lines.append(f"{content}\n\n")

        for att in msg.get("attachments", []):
            fname = att.get("filename", "file")
            url = att.get("url", "")
            lines.append(f"- 📎 [{fname}]({url})\n\n")

        for emb in msg.get("embeds", []):
            if emb.get("title") or emb.get("description"):
                lines.append(f"> **Embed:** {emb.get('title', '')}\n> {emb.get('description', '')}\n\n")

        reactions = msg.get("reactions", [])
        if reactions:
            react_str = " ".join([f"`{r.get('emoji', {}).get('name')}: {r.get('count')}`" for r in reactions])
            lines.append(f"\n*Reactions:* {react_str}\n\n")

        lines.append("\n---\n\n")

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def export_json(
    messages: List[Dict[str, Any]],
    guild_name: str,
    channel_name: str,
    output_path: str,
) -> None:
    """Export messages to formatted JSON with metadata."""
    sorted_msgs = sorted(messages, key=lambda m: m.get("id", "0"))
    payload = {
        "metadata": {
            "guild_name": guild_name,
            "channel_name": channel_name,
            "exported_at": datetime.now().isoformat(),
            "message_count": len(sorted_msgs),
        },
        "messages": sorted_msgs,
    }

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
