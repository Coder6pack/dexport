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
    help="🚀 dexport - Điều khiển Discord Desktop từ command line qua Chrome DevTools Protocol.",
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



@app.command(name="status", help="Hiển thị thông tin tài khoản Discord và kết nối CDP.")
@async_command
async def cmd_status(
    port: int = typer.Option(DEFAULT_PORT, "--port", "-p", help="CDP Debugging Port"),
    auto_restart: bool = typer.Option(True, "--auto-restart/--no-restart", help="Tự khởi động lại Discord nếu chưa mở port debug"),
):
    with console.status("[bold green]Đang kết nối tới Discord Desktop...[/bold green]"):
        try:
            async with DiscordClient(port=port, auto_restart=auto_restart) as client:
                user = await client.get_current_user()
                guilds = await client.get_guilds()
        except Exception as e:
            console.print(f"[bold red]❌ Lỗi kết nối:[/bold red] {e}")
            sys.exit(1)

    username = user.get("username", "")
    global_name = user.get("global_name") or username
    user_id = user.get("id", "")
    email = user.get("email") or "Ẩn/Không có"
    phone = user.get("phone") or "Không có"
    mfa = "Bật" if user.get("mfa_enabled") else "Tắt"

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", justify="right")
    grid.add_column(style="white")

    grid.add_row("Tên hiển thị:", f"[bold yellow]{global_name}[/bold yellow] (@{username})")
    grid.add_row("User ID:", f"[dim]{user_id}[/dim]")
    grid.add_row("Email:", email)
    grid.add_row("Phone:", phone)
    grid.add_row("2FA/MFA:", mfa)
    grid.add_row("Số Server tham gia:", f"[green]{len(guilds)}[/green] servers")
    grid.add_row("Cổng CDP:", f"[magenta]{port}[/magenta] (Connected)")

    console.print()
    console.print(Panel(grid, title="👤 [bold green]Thông tin tài khoản Discord[/bold green]", border_style="green"))
    console.print()


@app.command(name="guilds", help="Liệt kê danh sách tất cả các Server (Guilds) mà bạn đã tham gia.")
@async_command
async def cmd_guilds(
    port: int = typer.Option(DEFAULT_PORT, "--port", "-p", help="CDP Debugging Port"),
    auto_restart: bool = typer.Option(True, "--auto-restart/--no-restart"),
):
    with console.status("[bold green]Đang tải danh sách server...[/bold green]"):
        try:
            async with DiscordClient(port=port, auto_restart=auto_restart) as client:
                guilds = await client.get_guilds(force_refresh=True)
        except Exception as e:
            console.print(f"[bold red]❌ Lỗi:[/bold red] {e}")
            sys.exit(1)

    table = Table(title=f"🏰 Danh sách Server ({len(guilds)})", border_style="cyan", show_lines=True)
    table.add_column("#", justify="right", style="dim", width=4)
    table.add_column("Tên Server", style="bold yellow")
    table.add_column("Server ID", style="dim cyan")
    table.add_column("Chủ sở hữu", justify="center")

    for i, g in enumerate(guilds, 1):
        is_owner = "👑 Owner" if g.get("owner") else "Member"
        table.add_row(str(i), g.get("name", "Unknown"), str(g.get("id")), is_owner)

    console.print()
    console.print(table)
    console.print()


@app.command(name="channels", help="Liệt kê danh sách các kênh (Channels) trong một Server.")
@async_command
async def cmd_channels(
    guild: str = typer.Option(..., "--guild", "-g", help="Tên hoặc ID của Server"),
    port: int = typer.Option(DEFAULT_PORT, "--port", "-p", help="CDP Debugging Port"),
    auto_restart: bool = typer.Option(True, "--auto-restart/--no-restart"),
):
    with console.status(f"[bold green]Đang tìm server '{guild}' và tải danh sách kênh...[/bold green]"):
        try:
            async with DiscordClient(port=port, auto_restart=auto_restart) as client:
                g_info = await client.resolve_guild(guild)
                channels = await client.get_channels(g_info["id"], force_refresh=True)
        except Exception as e:
            console.print(f"[bold red]❌ Lỗi:[/bold red] {e}")
            sys.exit(1)

    table = Table(
        title=f"📋 Danh sách kênh trong server [bold yellow]{g_info['name']}[/bold yellow] ({len(channels)} kênh)",
        border_style="cyan",
        show_lines=False,
    )
    table.add_column("Loại", justify="center", width=8)
    table.add_column("Tên kênh", style="bold white")
    table.add_column("Channel ID", style="dim cyan")

    # Map channel types
    type_map = {
        0: "💬 Text",
        2: "🔊 Voice",
        4: "📁 Category",
        5: "📢 News",
        13: "🎤 Stage",
        15: "🧵 Forum",
    }

    # Group by category if possible
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


DEFAULT_REPORT_DIR = os.environ.get("DEXPORT_REPORT_DIR", "/Users/mac/Documents/report")


def resolve_report_filepath(export_arg: Optional[str] = None, default_prefix: str = "report", ext: str = "md") -> str:
    """
    Resolve report save path.
    Default directory: /Users/mac/Documents/report
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


@app.command(name="read", help="Đọc tin nhắn từ một kênh, hỗ trợ lọc theo ngày, người dùng, từ khóa, file, link và xuất file MD/JSON.")
@async_command
async def cmd_read(
    guild: str = typer.Option(..., "--guild", "-g", help="Tên hoặc ID của Server"),
    channel: str = typer.Option(..., "--channel", "-c", help="Tên hoặc ID của Kênh"),
    user: Optional[str] = typer.Option(None, "--user", "-u", help="Lọc tin nhắn của người dùng cụ thể (tên hoặc ID)"),
    since: Optional[str] = typer.Option(None, "--since", "-s", help="Lọc từ thời điểm: 'today', 'yesterday', '3d', '24h', 'YYYY-MM-DD'"),
    until: Optional[str] = typer.Option(None, "--until", help="Lọc đến thời điểm: 'YYYY-MM-DD'"),
    query: Optional[str] = typer.Option(None, "--query", "-q", help="Tìm kiếm tin nhắn chứa từ khóa"),
    has_file: Optional[bool] = typer.Option(None, "--has-file", help="Chỉ lấy tin nhắn có file đính kèm/ảnh"),
    has_link: Optional[bool] = typer.Option(None, "--has-link", help="Chỉ lấy tin nhắn có chứa đường dẫn liên kết (URL)"),
    human_only: bool = typer.Option(False, "--human-only", help="Loại bỏ tin nhắn từ bot"),
    limit: int = typer.Option(50, "--limit", "-l", help="Số lượng tin nhắn muốn đọc"),
    scan_depth: int = typer.Option(1000, "--scan-depth", help="Số lượng tin nhắn quét tối đa khi áp dụng bộ lọc"),
    export: Optional[str] = typer.Option(None, "--export", "-e", help="Định dạng xuất: 'md' hoặc 'json'"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Đường dẫn file xuất (mặc định lưu tại /Users/mac/Documents/report)"),
    port: int = typer.Option(DEFAULT_PORT, "--port", "-p", help="CDP Debugging Port"),
    auto_restart: bool = typer.Option(True, "--auto-restart/--no-restart"),
):
    with console.status(f"[bold green]Đang tải và lọc tin nhắn từ #{channel} trong '{guild}'...[/bold green]"):
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
            console.print(f"[bold red]❌ Lỗi:[/bold red] {e}")
            sys.exit(1)

    # Render to terminal
    render_messages(console, messages, g_info["name"], c_info.get("name", channel), limit=limit)

    # Handle Export
    if export:
        export_type = export.lower().strip()
        safe_cname = "".join([c if c.isalnum() else "_" for c in c_info.get("name", "chat")])
        prefix = f"chat_{safe_cname}" if not user else f"chat_{safe_cname}_{user}"

        if export_type == "md":
            out_file = resolve_report_filepath(output, default_prefix=prefix, ext="md")
            export_markdown(messages, g_info["name"], c_info.get("name", channel), out_file)
            console.print(f"[bold green]✔ Đã xuất {len(messages)} tin nhắn ra Markdown:[/bold green] [cyan]{out_file}[/cyan]\n")
        elif export_type == "json":
            out_file = resolve_report_filepath(output, default_prefix=prefix, ext="json")
            export_json(messages, g_info["name"], c_info.get("name", channel), out_file)
            console.print(f"[bold green]✔ Đã xuất {len(messages)} tin nhắn ra JSON:[/bold green] [cyan]{out_file}[/cyan]\n")
        else:
            console.print(f"[yellow]Định dạng export '{export}' không được hỗ trợ. Chỉ hỗ trợ 'md' hoặc 'json'.[/yellow]")


@app.command(name="summarize", help="Tổng hợp, phân tích và tóm tắt tin nhắn (tự động xuất báo cáo ra /Users/mac/Documents/report).")
@async_command
async def cmd_summarize(
    guild: str = typer.Option(..., "--guild", "-g", help="Tên hoặc ID của Server"),
    channel: str = typer.Option(..., "--channel", "-c", help="Tên hoặc ID của Kênh"),
    user: Optional[str] = typer.Option(None, "--user", "-u", help="Lọc theo người dùng cụ thể (tên hoặc ID, bỏ trống nếu tóm tắt cả kênh)"),
    since: Optional[str] = typer.Option(None, "--since", "-s", help="Lọc từ thời điểm: 'today', 'yesterday', '3d', '24h', 'YYYY-MM-DD'"),
    until: Optional[str] = typer.Option(None, "--until", help="Lọc đến thời điểm: 'YYYY-MM-DD'"),
    query: Optional[str] = typer.Option(None, "--query", "-q", help="Tìm kiếm tin nhắn chứa từ khóa"),
    has_file: Optional[bool] = typer.Option(None, "--has-file", help="Chỉ lấy tin nhắn có file đính kèm/ảnh"),
    has_link: Optional[bool] = typer.Option(None, "--has-link", help="Chỉ lấy tin nhắn có chứa đường dẫn (URL)"),
    human_only: bool = typer.Option(False, "--human-only", help="Loại bỏ tin nhắn từ bot"),
    model: str = typer.Option("gemini-2.5-flash", "--model", "-m", help="Mô hình AI: gemini-2.5-flash, gpt-4o, claude-3-7-sonnet, deepseek-v4-flash, kimi-k3, qwen3.8-max..."),
    provider: Optional[str] = typer.Option(None, "--provider", help="Nhà cung cấp AI: 'opencode' (OpenCode Go), 'gemini', 'openai', 'claude', 'deepseek', 'ollama'"),
    api_key: Optional[str] = typer.Option(None, "--api-key", help="API Key tương ứng (OPENCODE_API_KEY, GEMINI_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY, DEEPSEEK_API_KEY)"),
    base_url: Optional[str] = typer.Option(None, "--base-url", help="Custom OpenAI-compatible API base URL (ví dụ: https://opencode.ai/zen/go/v1 hoặc http://localhost:11434/v1)"),
    limit: int = typer.Option(100, "--limit", "-l", help="Số lượng tin nhắn tối đa muốn lấy"),
    scan_depth: int = typer.Option(1500, "--scan-depth", help="Số tin nhắn trong kênh quét tối đa để lọc"),
    export: Optional[str] = typer.Option(None, "--export", "-o", help="Đường dẫn lưu file báo cáo (mặc định: /Users/mac/Documents/report/report-DD-MM-YYYY.md)"),
    port: int = typer.Option(DEFAULT_PORT, "--port", "-p", help="CDP Debugging Port"),
    auto_restart: bool = typer.Option(True, "--auto-restart/--no-restart"),
):
    target_info = f"của '{user}'" if user else "toàn bộ kênh"
    with console.status(f"[bold green]Đang quét và thu thập tin nhắn {target_info} trong #{channel}...[/bold green]"):
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
            console.print(f"[bold red]❌ Lỗi:[/bold red] {e}")
            sys.exit(1)

    if not messages:
        console.print(f"[yellow]⚠️ Không tìm thấy tin nhắn nào thỏa mãn điều kiện lọc trong {scan_depth} tin nhắn gần nhất của kênh #{c_info.get('name')}.[/yellow]")
        sys.exit(0)

    user_display = (
        resolved_user.get("global_name") or resolved_user.get("username")
        if resolved_user
        else (user or f"Thảo luận #{c_info.get('name')}")
    )

    # 1. Local structured analysis
    summary_data = generate_local_summary(
        messages,
        target_user_name=user_display,
        guild_name=g_info["name"],
        channel_name=c_info.get("name", channel),
    )

    # 2. AI Summarization
    ai_summary_text = None
    with console.status(f"[bold magenta]🤖 Đang gửi dữ liệu cho AI ({model}) để phân tích & tóm tắt...[/bold magenta]"):
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

    # Render report on terminal
    render_summary_report(console, summary_data, ai_summary_text, model_name=model)

    # Automatically save report to /Users/mac/Documents/report/report-DD-MM-YYYY.md
    out_file = resolve_report_filepath(export, default_prefix="report", ext="md")
    export_summary_markdown(summary_data, ai_summary_text, out_file, model_name=model)
    console.print(f"[bold green]✔ Đã lưu báo cáo tổng hợp vào:[/bold green] [cyan]{out_file}[/cyan]\n")



@app.command(name="models", help="Hiển thị danh sách các Model AI và nhà cung cấp được hỗ trợ.")
def cmd_models():
    table = Table(title="🤖 Các Model AI & Cấu hình hỗ trợ trong dexport", border_style="magenta", show_lines=True)
    table.add_column("Nhà cung cấp", style="bold cyan")
    table.add_column("Model gợi ý (-m)", style="bold yellow")
    table.add_column("Biến môi trường API Key", style="green")
    table.add_column("Ghi chú & Endpoint", style="white")

    table.add_row(
        "OpenCode Go ⭐",
        "deepseek-r1\nclaude-3-7-sonnet\nkimi-k1.5\nqwen-2.5-coder-32b\ngpt-4o\nminimax-01\ngrok-2",
        "OPENCODE_API_KEY",
        "Gateway tổng hợp siêu rẻ ($10/tháng): https://opencode.ai/zen/go/v1\nDùng flag --provider opencode hoặc export OPENCODE_API_KEY"
    )
    table.add_row(
        "Google Gemini",
        "gemini-2.5-flash (mặc định)\ngemini-2.5-pro\ngemini-1.5-flash",
        "GEMINI_API_KEY",
        "Tốc độ cực nhanh, context window lớn, tóm tắt tiếng Việt rất tốt"
    )
    table.add_row(
        "OpenAI",
        "gpt-4o\ngpt-4o-mini\no3-mini",
        "OPENAI_API_KEY",
        "Thông minh, lập luận và tổng hợp chi tiết"
    )
    table.add_row(
        "Anthropic Claude",
        "claude-3-7-sonnet-latest\nclaude-3-5-haiku-latest",
        "ANTHROPIC_API_KEY",
        "Phân tích chuyên sâu, hành văn mạch lạc"
    )
    table.add_row(
        "DeepSeek",
        "deepseek-chat\ndeepseek-reasoner",
        "DEEPSEEK_API_KEY",
        "Chi phí cực rẻ, chất lượng cao"
    )
    table.add_row(
        "Ollama / Local LLM",
        "llama3.2\nqwen2.5\nmistral\ngemma2",
        "Không cần (hoặc tùy chọn)",
        "Chạy offline trên máy qua cờ --base-url http://localhost:11434/v1"
    )
    table.add_row(
        "OpenRouter / Groq",
        "openai/gpt-4o, meta-llama/...",
        "OPENAI_API_KEY hoặc --api-key",
        "Dùng cờ --base-url https://openrouter.ai/api/v1 hoặc https://api.groq.com/openai/v1"
    )

    console.print()
    console.print(table)
    console.print()





@app.command(name="send", help="Gửi tin nhắn vào một kênh như chính người dùng.")
@async_command
async def cmd_send(
    guild: str = typer.Option(..., "--guild", "-g", help="Tên hoặc ID của Server"),
    channel: str = typer.Option(..., "--channel", "-c", help="Tên hoặc ID của Kênh"),
    message: str = typer.Option(..., "--message", "-m", help="Nội dung tin nhắn muốn gửi"),
    port: int = typer.Option(DEFAULT_PORT, "--port", "-p", help="CDP Debugging Port"),
    auto_restart: bool = typer.Option(True, "--auto-restart/--no-restart"),
):
    with console.status(f"[bold green]Đang gửi tin nhắn vào #{channel}...[/bold green]"):
        try:
            async with DiscordClient(port=port, auto_restart=auto_restart) as client:
                g_info = await client.resolve_guild(guild)
                c_info = await client.resolve_channel(g_info["id"], channel)
                res = await client.send_message(c_info["id"], content=message)
        except Exception as e:
            console.print(f"[bold red]❌ Lỗi gửi tin nhắn:[/bold red] {e}")
            sys.exit(1)

    msg_id = res.get("id")
    console.print(f"[bold green]✔ Đã gửi thành công vào #{c_info.get('name')}:[/bold green] {message} [dim](ID: {msg_id})[/dim]\n")


@app.command(name="reply", help="Trả lời (Reply) một tin nhắn cụ thể trong kênh.")
@async_command
async def cmd_reply(
    guild: str = typer.Option(..., "--guild", "-g", help="Tên hoặc ID của Server"),
    channel: str = typer.Option(..., "--channel", "-c", help="Tên hoặc ID của Kênh"),
    msg_id: str = typer.Option(..., "--msg-id", help="ID tin nhắn cần trả lời"),
    message: str = typer.Option(..., "--message", "-m", help="Nội dung tin nhắn trả lời"),
    port: int = typer.Option(DEFAULT_PORT, "--port", "-p", help="CDP Debugging Port"),
    auto_restart: bool = typer.Option(True, "--auto-restart/--no-restart"),
):
    with console.status(f"[bold green]Đang reply tin nhắn {msg_id}...[/bold green]"):
        try:
            async with DiscordClient(port=port, auto_restart=auto_restart) as client:
                g_info = await client.resolve_guild(guild)
                c_info = await client.resolve_channel(g_info["id"], channel)
                res = await client.send_message(c_info["id"], content=message, reply_to_id=msg_id)
        except Exception as e:
            console.print(f"[bold red]❌ Lỗi reply tin nhắn:[/bold red] {e}")
            sys.exit(1)

    new_id = res.get("id")
    console.print(f"[bold green]✔ Đã trả lời tin nhắn {msg_id}:[/bold green] {message} [dim](New ID: {new_id})[/dim]\n")


@app.command(name="react", help="Thả reaction cảm xúc (emoji) vào tin nhắn.")
@async_command
async def cmd_react(
    guild: str = typer.Option(..., "--guild", "-g", help="Tên hoặc ID của Server"),
    channel: str = typer.Option(..., "--channel", "-c", help="Tên hoặc ID của Kênh"),
    msg_id: str = typer.Option(..., "--msg-id", help="ID của tin nhắn"),
    emoji: str = typer.Option(..., "--emoji", "-e", help="Emoji muốn thả (ví dụ: 🔥, 👍, ❤️, 😎)"),
    port: int = typer.Option(DEFAULT_PORT, "--port", "-p", help="CDP Debugging Port"),
    auto_restart: bool = typer.Option(True, "--auto-restart/--no-restart"),
):
    with console.status(f"[bold green]Đang thả reaction '{emoji}' vào tin nhắn {msg_id}...[/bold green]"):
        try:
            async with DiscordClient(port=port, auto_restart=auto_restart) as client:
                g_info = await client.resolve_guild(guild)
                c_info = await client.resolve_channel(g_info["id"], channel)
                await client.add_reaction(c_info["id"], msg_id, emoji=emoji)
        except Exception as e:
            console.print(f"[bold red]❌ Lỗi thả reaction:[/bold red] {e}")
            sys.exit(1)

    console.print(f"[bold green]✔ Đã thả {emoji} vào tin nhắn {msg_id} thành công![/bold green]\n")


@app.command(name="watch", help="Theo dõi tin nhắn mới trong kênh theo thời gian thực (Live Stream).")
@async_command
async def cmd_watch(
    guild: str = typer.Option(..., "--guild", "-g", help="Tên hoặc ID của Server"),
    channel: str = typer.Option(..., "--channel", "-c", help="Tên hoặc ID của Kênh"),
    interval: float = typer.Option(2.0, "--interval", "-i", help="Chu kỳ kiểm tra tin nhắn mới (giây)"),
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
                    f"Đang theo dõi kênh [bold green]#{c_info.get('name')}[/bold green] trong [bold yellow]{g_info['name']}[/bold yellow]...\n"
                    f"[dim]Nhấn Ctrl+C để dừng.[/dim]",
                    title="👀 [bold magenta]dexport Live Watch[/bold magenta]",
                    border_style="magenta",
                )
            )

            # Get initial latest message ID
            initial_msgs = await client.get_messages(channel_id, limit=5)
            last_seen_id = initial_msgs[0]["id"] if initial_msgs else "0"

            # Print last few messages first
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
        console.print("\n[yellow]Đã dừng theo dõi.[/yellow]")
    except Exception as e:
        console.print(f"[bold red]❌ Lỗi theo dõi:[/bold red] {e}")
        sys.exit(1)


@app.command(name="bot", help="Khởi chạy Telegram Bot Daemon để điều khiển dexport từ xa bằng điện thoại qua tin nhắn tự nhiên.")
@async_command
async def cmd_bot(
    token: Optional[str] = typer.Option(None, "--token", "-t", help="Telegram Bot Token (hoặc TELEGRAM_BOT_TOKEN)"),
    allowed_user: Optional[str] = typer.Option(None, "--allowed-user", "-u", help="Telegram User ID của bạn để khóa quyền (hoặc TELEGRAM_USER_ID)"),
    model: str = typer.Option("deepseek-v4-flash", "--model", "-m", help="AI Model mặc định: deepseek-v4-flash, gpt-4o, claude-3-7-sonnet..."),
    port: int = typer.Option(DEFAULT_PORT, "--port", "-p", help="CDP Debugging Port"),
):
    from .telegram_bot import TelegramBotDaemon

    bot_token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        console.print("[bold red]❌ Thiếu Telegram Bot Token![/bold red]")
        console.print("👉 Hãy tạo bot miễn phí qua @BotFather trên Telegram rồi truyền cờ `--token <TOKEN>` hoặc export biến `TELEGRAM_BOT_TOKEN`.")
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
            f"Đang khởi chạy [bold magenta]dexport Telegram Bot Daemon[/bold magenta]...\n\n"
            f"• AI Model: [bold green]{model}[/bold green]\n"
            f"• CDP Port: [cyan]{port}[/cyan]\n"
            f"• Quyền điều khiển: [bold yellow]{list(allowed_ids) if allowed_ids else 'Công khai (chưa khóa ID)'}[/bold yellow]\n\n"
            f"[dim]Bạn có thể nhắn tin bằng tiếng Việt tự nhiên cho bot trên Telegram để đọc, gửi và tóm tắt chat Discord.\nNhấn Ctrl+C để dừng.[/dim]",
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
        console.print("\n[yellow]Đã dừng Telegram Bot Daemon.[/yellow]")


@app.command(name="autochat", help="Tự động trò chuyện/trả lời tin nhắn trên Discord theo Persona & Prompt tùy chỉnh.")

@async_command
async def cmd_autochat(
    guild: str = typer.Option(..., "--guild", "-g", help="Tên hoặc ID của Server"),
    channel: str = typer.Option(..., "--channel", "-c", help="Tên hoặc ID của Kênh"),
    prompt: Optional[str] = typer.Option(None, "--prompt", "-P", help="Chỉ đạo phong cách / Persona cho AI (mặc định: thân thiện, ngắn gọn, xưng hô ae)"),
    all_chat: bool = typer.Option(False, "--all-chat", help="Tự động tham gia thảo luận cả kênh (mặc định: chỉ trả lời khi được tag hoặc rep)"),
    model: str = typer.Option("deepseek-v4-flash", "--model", "-m", help="AI Model sử dụng: deepseek-v4-flash, gpt-4o, claude-3-7-sonnet..."),
    cooldown: float = typer.Option(20.0, "--cooldown", help="Thời gian chờ tối thiểu giữa các tin nhắn tự gửi (giây)"),
    port: int = typer.Option(DEFAULT_PORT, "--port", "-p", help="CDP Debugging Port"),
):
    from .autochat import AutoChatSession, DEFAULT_PERSONA_PROMPT

    persona_prompt = prompt or DEFAULT_PERSONA_PROMPT

    console.print(
        Panel(
            f"Đang kích hoạt [bold magenta]Auto-Chat Persona[/bold magenta]...\n\n"
            f"• Server: [bold yellow]{guild}[/bold yellow]\n"
            f"• Kênh: [bold green]#{channel}[/bold green]\n"
            f"• Model AI: [bold cyan]{model}[/bold cyan]\n"
            f"• Chế độ: [bold white]{'Tham gia cả kênh' if all_chat else 'Chỉ trả lời khi được Tag / Rep'}[/bold white]\n"
            f"• Cooldown: [dim]{cooldown}s[/dim]\n\n"
            f"🎯 [bold underline]Chỉ đạo Persona:[/bold underline]\n[italic white]\"{persona_prompt}\"[/italic white]\n\n"
            f"[dim]Nhấn Ctrl+C để dừng.[/dim]",
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
            f"[bold green]✔ [AutoChat {info['timestamp']}][/bold green] Đã gửi: [white]\"{info['content']}\"[/white]"
        ),
    )

    try:
        await session.start()
    except (KeyboardInterrupt, asyncio.CancelledError):
        session.stop()
        console.print("\n[yellow]Đã dừng AutoChat.[/yellow]")


def main():
    app()


if __name__ == "__main__":
    main()


