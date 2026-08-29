"""
Command-line interface (CLI) for dexport.
"""

import asyncio
from datetime import datetime
from functools import wraps
import os
import sys
from typing import Optional
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
import typer

from .cdp import DEFAULT_PORT
from .client import DiscordClient
from .exporter import export_json, export_markdown, parse_timestamp, render_messages


def load_dexport_env():
    """Auto-load environment variables from ~/.dexport.env or project .env."""
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


load_dexport_env()

app = typer.Typer(
    name="dexport",
    help="🚀 dexport - Control Discord Desktop via Chrome DevTools Protocol from the command line.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


def async_command(f):
    """Decorator to run async functions within typer commands while preserving signature."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        return asyncio.run(f(*args, **kwargs))
    return wrapper


@app.command(name="status", help="Display current Discord user profile and CDP connection status.")
@async_command
async def cmd_status(
    port: int = typer.Option(DEFAULT_PORT, "--port", "-p", help="CDP Debugging Port"),
    auto_restart: bool = typer.Option(True, "--auto-restart/--no-restart", help="Auto-restart Discord with CDP flags if port is closed"),
):
    with console.status("[bold green]Connecting to Discord Desktop...[/bold green]"):
        try:
            async with DiscordClient(port=port, auto_restart=auto_restart) as client:
                user = await client.get_current_user()
                guilds = await client.get_guilds()
        except Exception as e:
            console.print(f"[bold red]❌ Connection error:[/bold red] {e}")
            sys.exit(1)

    username = user.get("username", "")
    global_name = user.get("global_name") or username
    user_id = user.get("id", "")
    email = user.get("email") or "Hidden/None"
    phone = user.get("phone") or "None"
    mfa = "Enabled" if user.get("mfa_enabled") else "Disabled"

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", justify="right")
    grid.add_column(style="white")

    grid.add_row("Display Name:", f"[bold yellow]{global_name}[/bold yellow] (@{username})")
    grid.add_row("User ID:", f"[dim]{user_id}[/dim]")
    grid.add_row("Email:", email)
    grid.add_row("Phone:", phone)
    grid.add_row("2FA/MFA:", mfa)
    grid.add_row("Joined Guilds:", f"[green]{len(guilds)}[/green] servers")
    grid.add_row("CDP Port:", f"[magenta]{port}[/magenta] (Connected)")

    console.print()
    console.print(Panel(grid, title="👤 [bold green]Discord User Profile[/bold green]", border_style="green"))
    console.print()


@app.command(name="guilds", help="List all joined Discord servers (Guilds).")
@async_command
async def cmd_guilds(
    port: int = typer.Option(DEFAULT_PORT, "--port", "-p", help="CDP Debugging Port"),
    auto_restart: bool = typer.Option(True, "--auto-restart/--no-restart"),
):
    with console.status("[bold green]Fetching guilds list...[/bold green]"):
        try:
            async with DiscordClient(port=port, auto_restart=auto_restart) as client:
                guilds = await client.get_guilds(force_refresh=True)
        except Exception as e:
            console.print(f"[bold red]❌ Error:[/bold red] {e}")
            sys.exit(1)

    table = Table(title=f"🏰 Joined Servers ({len(guilds)})", border_style="cyan", show_lines=True)
    table.add_column("#", justify="right", style="dim", width=4)
    table.add_column("Server Name", style="bold yellow")
    table.add_column("Server ID", style="dim cyan")
    table.add_column("Role", justify="center")

    for i, g in enumerate(guilds, 1):
        is_owner = "👑 Owner" if g.get("owner") else "Member"
        table.add_row(str(i), g.get("name", "Unknown"), str(g.get("id")), is_owner)

    console.print()
    console.print(table)
    console.print()


@app.command(name="channels", help="List channels in a Discord server.")
@async_command
async def cmd_channels(
    guild: str = typer.Option(..., "--guild", "-g", help="Server name or ID"),
    port: int = typer.Option(DEFAULT_PORT, "--port", "-p", help="CDP Debugging Port"),
    auto_restart: bool = typer.Option(True, "--auto-restart/--no-restart"),
):
    with console.status(f"[bold green]Resolving server '{guild}' and fetching channels...[/bold green]"):
        try:
            async with DiscordClient(port=port, auto_restart=auto_restart) as client:
                g_info = await client.resolve_guild(guild)
                channels = await client.get_channels(g_info["id"], force_refresh=True)
        except Exception as e:
            console.print(f"[bold red]❌ Error:[/bold red] {e}")
            sys.exit(1)

    table = Table(
        title=f"📋 Channels in server [bold yellow]{g_info['name']}[/bold yellow] ({len(channels)} channels)",
        border_style="cyan",
        show_lines=False,
    )
    table.add_column("Type", justify="center", width=8)
    table.add_column("Channel Name", style="bold white")
    table.add_column("Channel ID", style="dim cyan")

    type_map = {
        0: "💬 Text",
        2: "🔊 Voice",
        4: "📁 Category",
        5: "📢 News",
        13: "🎤 Stage",
        15: "🧵 Forum",
    }

    categories = {c["id"]: c.get("name") for c in channels if c.get("type") == 4}

    for c in channels:
        c_type_val = c.get("type", 0)
        c_type = type_map.get(c_type_val, f"Type {c_type_val}")
        name = c.get("name", "")
        if c_type_val == 4:
            name = f"[bold cyan]▼ {name.upper()}[/bold cyan]"
        elif c.get("parent_id") and c.get("parent_id") in categories:
            name = f"  #{name}"
        else:
            name = f"#{name}"

        table.add_row(c_type, name, str(c.get("id")))

    console.print()
    console.print(table)
    console.print()


from .summarizer import (
    call_ai_summary,
    export_summary_markdown,
    generate_local_summary,
    render_summary_report,
)


DEFAULT_REPORT_DIR = os.environ.get("DEXPORT_REPORT_DIR", os.path.expanduser("~/Documents/report"))


def resolve_report_filepath(export_arg: Optional[str] = None, default_prefix: str = "report", ext: str = "md") -> str:
    """
    Resolve report save path.
    Default directory: ~/Documents/report
    Default file format: report-DD-MM-YYYY.md (or report-DD-MM-YYYY_HHMMSS.md if duplicate)
    """
    base_dir = os.path.expanduser(DEFAULT_REPORT_DIR)
    os.makedirs(base_dir, exist_ok=True)

    if not export_arg:
        date_str = datetime.now().strftime("%d-%m-%Y")
        candidate = os.path.join(base_dir, f"{default_prefix}-{date_str}.{ext}")
        if os.path.exists(candidate):
            time_str = datetime.now().strftime("%d-%m-%Y_%H%M%S")
            candidate = os.path.join(base_dir, f"{default_prefix}-{time_str}.{ext}")
        return candidate

    expanded = os.path.expanduser(export_arg)
    if os.path.isabs(expanded):
        os.makedirs(os.path.dirname(expanded), exist_ok=True)
        return expanded
    else:
        target = os.path.join(base_dir, expanded)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        return target


@app.command(name="read", help="Read messages from a channel with advanced filters and export to MD/JSON.")
@async_command
async def cmd_read(
    guild: str = typer.Option(..., "--guild", "-g", help="Server name or ID"),
    channel: str = typer.Option(..., "--channel", "-c", help="Channel name or ID"),
    user: Optional[str] = typer.Option(None, "--user", "-u", help="Filter by username or ID"),
    since: Optional[str] = typer.Option(None, "--since", "-s", help="Filter since: 'today', 'yesterday', '3d', '24h', 'YYYY-MM-DD'"),
    until: Optional[str] = typer.Option(None, "--until", help="Filter until: 'YYYY-MM-DD'"),
    query: Optional[str] = typer.Option(None, "--query", "-q", help="Search for keyword in message text"),
    has_file: Optional[bool] = typer.Option(None, "--has-file", help="Filter messages with attachments/images only"),
    has_link: Optional[bool] = typer.Option(None, "--has-link", help="Filter messages containing URLs"),
    human_only: bool = typer.Option(False, "--human-only", help="Exclude messages from bot accounts"),
    limit: int = typer.Option(50, "--limit", "-l", help="Number of messages to display"),
    scan_depth: int = typer.Option(1000, "--scan-depth", help="Maximum messages to scan backwards for filtering"),
    export: Optional[str] = typer.Option(None, "--export", "-e", help="Export format: 'md' or 'json'"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Output filepath (defaults to ~/Documents/report)"),
    port: int = typer.Option(DEFAULT_PORT, "--port", "-p", help="CDP Debugging Port"),
    auto_restart: bool = typer.Option(True, "--auto-restart/--no-restart"),
):
    with console.status(f"[bold green]Fetching and filtering messages from #{channel} in '{guild}'...[/bold green]"):
        try:
            async with DiscordClient(port=port, auto_restart=auto_restart) as client:
                g_info = await client.resolve_guild(guild)
                c_info = await client.resolve_channel(g_info["id"], channel)
                messages, matched_user = await client.get_filtered_messages(
                    c_info["id"],
                    user_query=user,
                    since=since,
                    until=until,
                    text_query=query,
                    has_file=has_file,
                    has_link=has_link,
                    human_only=human_only,
                    limit=limit,
                    scan_depth=scan_depth,
                )
        except Exception as e:
            console.print(f"[bold red]❌ Error:[/bold red] {e}")
            sys.exit(1)

    render_messages(console, messages, g_info["name"], c_info.get("name", channel), limit=limit)

    if export:
        export_type = export.lower().strip()
        safe_cname = "".join([c if c.isalnum() else "_" for c in c_info.get("name", "chat")])
        prefix = f"chat_{safe_cname}" if not user else f"chat_{safe_cname}_{user}"

        if export_type == "md":
            out_file = resolve_report_filepath(output, default_prefix=prefix, ext="md")
            export_markdown(messages, g_info["name"], c_info.get("name", channel), out_file)
            console.print(f"[bold green]✔ Exported {len(messages)} messages to Markdown:[/bold green] [cyan]{out_file}[/cyan]\n")
        elif export_type == "json":
            out_file = resolve_report_filepath(output, default_prefix=prefix, ext="json")
            export_json(messages, g_info["name"], c_info.get("name", channel), out_file)
            console.print(f"[bold green]✔ Exported {len(messages)} messages to JSON:[/bold green] [cyan]{out_file}[/cyan]\n")
        else:
            console.print(f"[yellow]Unsupported export format '{export}'. Use 'md' or 'json'.[/yellow]")


@app.command(name="summarize", help="Analyze and summarize channel discussions using AI (automatically saved to ~/Documents/report).")
@async_command
async def cmd_summarize(
    guild: str = typer.Option(..., "--guild", "-g", help="Server name or ID"),
    channel: str = typer.Option(..., "--channel", "-c", help="Channel name or ID"),
    user: Optional[str] = typer.Option(None, "--user", "-u", help="Filter by specific user (leave blank for entire channel)"),
    since: Optional[str] = typer.Option(None, "--since", "-s", help="Filter since: 'today', 'yesterday', '3d', '24h', 'YYYY-MM-DD'"),
    until: Optional[str] = typer.Option(None, "--until", help="Filter until: 'YYYY-MM-DD'"),
    query: Optional[str] = typer.Option(None, "--query", "-q", help="Search for keyword in message text"),
    has_file: Optional[bool] = typer.Option(None, "--has-file", help="Filter messages with attachments/images only"),
    has_link: Optional[bool] = typer.Option(None, "--has-link", help="Filter messages containing URLs"),
    human_only: bool = typer.Option(False, "--human-only", help="Exclude messages from bot accounts"),
    model: str = typer.Option("gemini-2.5-flash", "--model", "-m", help="AI Model: gemini-2.5-flash, gpt-4o, claude-3-7-sonnet, deepseek-v4-flash, kimi-k3, qwen3.8-max..."),
    provider: Optional[str] = typer.Option(None, "--provider", help="AI Provider: 'opencode' (OpenCode Go), 'gemini', 'openai', 'claude', 'deepseek', 'ollama'"),
    api_key: Optional[str] = typer.Option(None, "--api-key", help="API Key override (OPENCODE_API_KEY, GEMINI_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY, DEEPSEEK_API_KEY)"),
    base_url: Optional[str] = typer.Option(None, "--base-url", help="Custom OpenAI-compatible API base URL (e.g. https://opencode.ai/zen/go/v1 or http://localhost:11434/v1)"),
    limit: int = typer.Option(100, "--limit", "-l", help="Maximum messages to retrieve for summary"),
    scan_depth: int = typer.Option(1500, "--scan-depth", help="Maximum scan depth backwards"),
    export: Optional[str] = typer.Option(None, "--export", "-o", help="Output report filepath (defaults to ~/Documents/report/report-DD-MM-YYYY.md)"),
    port: int = typer.Option(DEFAULT_PORT, "--port", "-p", help="CDP Debugging Port"),
    auto_restart: bool = typer.Option(True, "--auto-restart/--no-restart"),
):
    target_info = f"from '{user}'" if user else "entire channel"
    with console.status(f"[bold green]Scanning and collecting messages {target_info} in #{channel}...[/bold green]"):
        try:
            async with DiscordClient(port=port, auto_restart=auto_restart) as client:
                g_info = await client.resolve_guild(guild)
                c_info = await client.resolve_channel(g_info["id"], channel)
                messages, resolved_user = await client.get_filtered_messages(
                    c_info["id"],
                    user_query=user,
                    since=since,
                    until=until,
                    text_query=query,
                    has_file=has_file,
                    has_link=has_link,
                    human_only=human_only,
                    limit=limit,
                    scan_depth=scan_depth,
                )
        except Exception as e:
            console.print(f"[bold red]❌ Error:[/bold red] {e}")
            sys.exit(1)

    if not messages:
        console.print(f"[yellow]⚠️ No messages matched filtering criteria in the last {scan_depth} messages of #{c_info.get('name')}.[/yellow]")
        sys.exit(0)

    user_display = (
        resolved_user.get("global_name") or resolved_user.get("username")
        if resolved_user
        else (user or f"Discussion #{c_info.get('name')}")
    )

    summary_data = generate_local_summary(
        messages,
        target_user_name=user_display,
        guild_name=g_info["name"],
        channel_name=c_info.get("name", channel),
    )

    ai_summary_text = None
    with console.status(f"[bold magenta]🤖 Sending context to AI ({model}) for analysis & summarization...[/bold magenta]"):
        ai_summary_text = call_ai_summary(
            messages,
            target_user_name=user_display,
            guild_name=g_info["name"],
            channel_name=c_info.get("name", channel),
            model=model,
            provider=provider,
            api_key=api_key,
            base_url=base_url,
        )

    render_summary_report(console, summary_data, ai_summary_text, model_name=model)

    out_file = resolve_report_filepath(export, default_prefix="report", ext="md")
    export_summary_markdown(summary_data, ai_summary_text, out_file, model_name=model)
    console.print(f"[bold green]✔ Summary report saved to:[/bold green] [cyan]{out_file}[/cyan]\n")


@app.command(name="models", help="Display list of supported AI models and providers.")
def cmd_models():
    table = Table(title="🤖 Supported AI Models & Providers in dexport", border_style="magenta", show_lines=True)
    table.add_column("Provider", style="bold cyan")
    table.add_column("Recommended Models (-m)", style="bold yellow")
    table.add_column("Environment Variable", style="green")
    table.add_column("Notes & Endpoint", style="white")

    table.add_row(
        "OpenCode Go ⭐",
        "deepseek-v4-flash\ndeepseek-v4-pro\nclaude-3-7-sonnet\nkimi-k3\nqwen3.8-max\ngpt-4o\nglm-5.3\nminimax-m3\ngrok-4.6",
        "OPENCODE_API_KEY",
        "Ultra low-cost universal API gateway: https://opencode.ai/zen/go/v1\nUse --provider opencode or export OPENCODE_API_KEY"
    )
    table.add_row(
        "Google Gemini",
        "gemini-2.5-flash (default)\ngemini-2.5-pro\ngemini-1.5-flash",
        "GEMINI_API_KEY",
        "Ultra fast, massive context window, excellent multilingual support"
    )
    table.add_row(
        "OpenAI",
        "gpt-4o\ngpt-4o-mini\no3-mini",
        "OPENAI_API_KEY",
        "High intelligence and structured reasoning"
    )
    table.add_row(
        "Anthropic Claude",
        "claude-3-7-sonnet\nclaude-3-5-haiku",
        "ANTHROPIC_API_KEY",
        "In-depth analysis and coherent prose"
    )
    table.add_row(
        "DeepSeek",
        "deepseek-chat\ndeepseek-reasoner",
        "DEEPSEEK_API_KEY",
        "High cost-efficiency and great coding capabilities"
    )
    table.add_row(
        "Ollama / Local LLM",
        "llama3.2\nqwen2.5\nmistral\ngemma2",
        "None (Optional)",
        "Runs offline locally via --base-url http://localhost:11434/v1"
    )
    table.add_row(
        "OpenRouter / Groq",
        "openai/gpt-4o, meta-llama/...",
        "OPENAI_API_KEY or --api-key",
        "Use --base-url https://openrouter.ai/api/v1 or https://api.groq.com/openai/v1"
    )

    console.print()
    console.print(table)
    console.print()


@app.command(name="send", help="Send a message to a Discord channel.")
@async_command
async def cmd_send(
    guild: str = typer.Option(..., "--guild", "-g", help="Server name or ID"),
    channel: str = typer.Option(..., "--channel", "-c", help="Channel name or ID"),
    message: str = typer.Option(..., "--message", "-m", help="Message content to send"),
    port: int = typer.Option(DEFAULT_PORT, "--port", "-p", help="CDP Debugging Port"),
    auto_restart: bool = typer.Option(True, "--auto-restart/--no-restart"),
):
    with console.status(f"[bold green]Sending message to #{channel}...[/bold green]"):
        try:
            async with DiscordClient(port=port, auto_restart=auto_restart) as client:
                g_info = await client.resolve_guild(guild)
                c_info = await client.resolve_channel(g_info["id"], channel)
                res = await client.send_message(c_info["id"], content=message)
        except Exception as e:
            console.print(f"[bold red]❌ Failed to send message:[/bold red] {e}")
            sys.exit(1)

    msg_id = res.get("id")
    console.print(f"[bold green]✔ Successfully sent to #{c_info.get('name')}:[/bold green] {message} [dim](ID: {msg_id})[/dim]\n")


@app.command(name="reply", help="Reply to a specific message ID in a channel.")
@async_command
async def cmd_reply(
    guild: str = typer.Option(..., "--guild", "-g", help="Server name or ID"),
    channel: str = typer.Option(..., "--channel", "-c", help="Channel name or ID"),
    msg_id: str = typer.Option(..., "--msg-id", help="Message ID to reply to"),
    message: str = typer.Option(..., "--message", "-m", help="Reply message content"),
    port: int = typer.Option(DEFAULT_PORT, "--port", "-p", help="CDP Debugging Port"),
    auto_restart: bool = typer.Option(True, "--auto-restart/--no-restart"),
):
    with console.status(f"[bold green]Replying to message {msg_id}...[/bold green]"):
        try:
            async with DiscordClient(port=port, auto_restart=auto_restart) as client:
                g_info = await client.resolve_guild(guild)
                c_info = await client.resolve_channel(g_info["id"], channel)
                res = await client.send_message(c_info["id"], content=message, reply_to_id=msg_id)
        except Exception as e:
            console.print(f"[bold red]❌ Failed to reply:[/bold red] {e}")
            sys.exit(1)

    new_id = res.get("id")
    console.print(f"[bold green]✔ Replied to message {msg_id}:[/bold green] {message} [dim](New ID: {new_id})[/dim]\n")


@app.command(name="react", help="Add an emoji reaction to a message.")
@async_command
async def cmd_react(
    guild: str = typer.Option(..., "--guild", "-g", help="Server name or ID"),
    channel: str = typer.Option(..., "--channel", "-c", help="Channel name or ID"),
    msg_id: str = typer.Option(..., "--msg-id", help="Message ID"),
    emoji: str = typer.Option(..., "--emoji", "-e", help="Emoji to react with (e.g. 🔥, 👍, ❤️, 😎)"),
    port: int = typer.Option(DEFAULT_PORT, "--port", "-p", help="CDP Debugging Port"),
    auto_restart: bool = typer.Option(True, "--auto-restart/--no-restart"),
):
    with console.status(f"[bold green]Adding reaction '{emoji}' to message {msg_id}...[/bold green]"):
        try:
            async with DiscordClient(port=port, auto_restart=auto_restart) as client:
                g_info = await client.resolve_guild(guild)
                c_info = await client.resolve_channel(g_info["id"], channel)
                await client.add_reaction(c_info["id"], msg_id, emoji=emoji)
        except Exception as e:
            console.print(f"[bold red]❌ Failed to add reaction:[/bold red] {e}")
            sys.exit(1)

    console.print(f"[bold green]✔ Added {emoji} reaction to message {msg_id} successfully![/bold green]\n")


@app.command(name="watch", help="Live-stream new messages from a channel in real time.")
@async_command
async def cmd_watch(
    guild: str = typer.Option(..., "--guild", "-g", help="Server name or ID"),
    channel: str = typer.Option(..., "--channel", "-c", help="Channel name or ID"),
    interval: float = typer.Option(2.0, "--interval", "-i", help="Polling interval in seconds"),
    port: int = typer.Option(DEFAULT_PORT, "--port", "-p", help="CDP Debugging Port"),
    auto_restart: bool = typer.Option(True, "--auto-restart/--no-restart"),
):
    try:
        async with DiscordClient(port=port, auto_restart=auto_restart) as client:
            g_info = await client.resolve_guild(guild)
            c_info = await client.resolve_channel(g_info["id"], channel)
            channel_id = c_info["id"]

            console.print(
                Panel(
                    f"Live watching channel [bold green]#{c_info.get('name')}[/bold green] in [bold yellow]{g_info['name']}[/bold yellow]...\n"
                    f"[dim]Press Ctrl+C to stop.[/dim]",
                    title="👀 [bold magenta]dexport Live Watch[/bold magenta]",
                    border_style="magenta",
                )
            )

            initial_msgs = await client.get_messages(channel_id, limit=5)
            last_seen_id = initial_msgs[0]["id"] if initial_msgs else "0"

            if initial_msgs:
                render_messages(console, initial_msgs, g_info["name"], c_info.get("name", channel), limit=5)

            while True:
                await asyncio.sleep(interval)
                new_msgs = await client.get_messages(channel_id, limit=20, after=last_seen_id)
                if new_msgs:
                    sorted_new = sorted(new_msgs, key=lambda m: m.get("id", "0"))
                    for msg in sorted_new:
                        author = msg.get("author", {})
                        username = author.get("global_name") or author.get("username", "Unknown")
                        timestamp = parse_timestamp(msg.get("timestamp"))
                        content = msg.get("content", "")
                        console.print(f"[bold yellow]{username}[/bold yellow] [dim]({timestamp}):[/dim] {content}")
                        last_seen_id = msg["id"]

    except (KeyboardInterrupt, asyncio.CancelledError):
        console.print("\n[yellow]Live watch stopped.[/yellow]")
    except Exception as e:
        console.print(f"[bold red]❌ Watch error:[/bold red] {e}")
        sys.exit(1)


@app.command(name="bot", help="Start Telegram Bot daemon for mobile remote control and AI conversation.")
@async_command
async def cmd_bot(
    token: Optional[str] = typer.Option(None, "--token", "-t", help="Telegram Bot Token (or TELEGRAM_BOT_TOKEN)"),
    allowed_user: Optional[str] = typer.Option(None, "--allowed-user", "-u", help="Your Telegram User ID to lock permissions (or TELEGRAM_USER_ID)"),
    model: str = typer.Option("deepseek-v4-flash", "--model", "-m", help="Default AI Model: deepseek-v4-flash, gpt-4o, claude-3-7-sonnet..."),
    port: int = typer.Option(DEFAULT_PORT, "--port", "-p", help="CDP Debugging Port"),
):
    from .telegram_bot import TelegramBotDaemon

    bot_token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        console.print("[bold red]❌ Missing Telegram Bot Token![/bold red]")
        console.print("👉 Create a bot via @BotFather on Telegram and pass `--token <TOKEN>` or export `TELEGRAM_BOT_TOKEN`.")
        sys.exit(1)

    allowed_ids = set()
    raw_user_id = allowed_user or os.environ.get("TELEGRAM_USER_ID") or os.environ.get("TELEGRAM_ALLOWED_USER_ID")
    if raw_user_id:
        for uid in raw_user_id.split(","):
            try:
                allowed_ids.add(int(uid.strip()))
            except ValueError:
                pass

    console.print(
        Panel(
            f"Starting [bold magenta]dexport Telegram Bot Daemon[/bold magenta]...\n\n"
            f"• AI Model: [bold green]{model}[/bold green]\n"
            f"• CDP Port: [cyan]{port}[/cyan]\n"
            f"• Allowed User IDs: [bold yellow]{list(allowed_ids) if allowed_ids else 'Public (no ID lock)'}[/bold yellow]\n\n"
            f"[dim]You can now control Discord from your phone via Telegram.\nPress Ctrl+C to stop.[/dim]",
            title="🤖 [bold cyan]dexport Telegram Remote Control[/bold cyan]",
            border_style="cyan",
        )
    )

    daemon = TelegramBotDaemon(
        bot_token=bot_token,
        allowed_user_ids=allowed_ids,
        default_model=model,
        cdp_port=port,
    )

    try:
        await daemon.start()
    except (KeyboardInterrupt, asyncio.CancelledError):
        daemon.stop()
        console.print("\n[yellow]Telegram Bot Daemon stopped.[/yellow]")


@app.command(name="autochat", help="Start autonomous AI Ghostwriter persona to auto-reply on Discord.")
@async_command
async def cmd_autochat(
    guild: str = typer.Option(..., "--guild", "-g", help="Server name or ID"),
    channel: str = typer.Option(..., "--channel", "-c", help="Channel name or ID"),
    prompt: Optional[str] = typer.Option(None, "--prompt", "-P", help="Persona style instructions for AI"),
    all_chat: bool = typer.Option(False, "--all-chat", help="Auto-participate in general channel discussions (default: mentions & replies only)"),
    model: str = typer.Option("deepseek-v4-flash", "--model", "-m", help="AI Model: deepseek-v4-flash, gpt-4o, claude-3-7-sonnet..."),
    cooldown: float = typer.Option(20.0, "--cooldown", help="Minimum cooldown between auto-sent messages in seconds"),
    port: int = typer.Option(DEFAULT_PORT, "--port", "-p", help="CDP Debugging Port"),
):
    from .autochat import AutoChatSession, DEFAULT_PERSONA_PROMPT

    persona_prompt = prompt or DEFAULT_PERSONA_PROMPT

    console.print(
        Panel(
            f"Activating [bold magenta]Auto-Chat Persona[/bold magenta]...\n\n"
            f"• Server: [bold yellow]{guild}[/bold yellow]\n"
            f"• Channel: [bold green]#{channel}[/bold green]\n"
            f"• AI Model: [bold cyan]{model}[/bold cyan]\n"
            f"• Mode: [bold white]{'General Channel Discussion' if all_chat else 'Mentions & Replies Only'}[/bold white]\n"
            f"• Cooldown: [dim]{cooldown}s[/dim]\n\n"
            f"🎯 [bold underline]Persona Prompt:[/bold underline]\n[italic white]\"{persona_prompt}\"[/italic white]\n\n"
            f"[dim]Press Ctrl+C to stop.[/dim]",
            title="🤖 [bold magenta]dexport AI Ghostwriter[/bold magenta]",
            border_style="magenta",
        )
    )

    session = AutoChatSession(
        guild_name=guild,
        channel_name=channel,
        prompt=persona_prompt,
        mentions_only=not all_chat,
        model=model,
        cooldown=cooldown,
        port=port,
        on_message_sent=lambda info: console.print(
            f"[bold green]✔ [AutoChat {info['timestamp']}][/bold green] Sent: [white]\"{info['content']}\"[/white]"
        ),
    )

    try:
        await session.start()
    except (KeyboardInterrupt, asyncio.CancelledError):
        session.stop()
        console.print("\n[yellow]AutoChat stopped.[/yellow]")


def main():
    app()


if __name__ == "__main__":
    main()
