"""
Filter definitions and date parsing utilities for Discord messages.
"""

from datetime import datetime, timedelta
import re
from typing import Any, Dict, List, Optional
from unidecode import unidecode


def parse_datetime_input(val: Optional[str]) -> Optional[datetime]:
    """
    Parse a variety of human date formats:
    - 'today', 'yesterday'
    - '3d', '7d', '24h', '12h', '1w' (relative past from now)
    - 'YYYY-MM-DD'
    - 'YYYY-MM-DD HH:MM' or 'YYYY-MM-DD HH:MM:SS'
    - 'DD/MM/YYYY'
    Returns timezone-aware datetime in local timezone.
    """
    if not val:
        return None

    val = str(val).strip().lower()
    now = datetime.now().astimezone()

    if val == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)

    if val == "yesterday":
        yesterday = now - timedelta(days=1)
        return yesterday.replace(hour=0, minute=0, second=0, microsecond=0)

    # Relative offset: e.g. 3d, 7d, 24h, 2w, 30m
    rel_match = re.match(r"^(\d+)\s*(m|min|h|hr|hour|hours|d|day|days|w|week|weeks)$", val)
    if rel_match:
        num = int(rel_match.group(1))
        unit = rel_match.group(2)
        if unit in ("m", "min"):
            return now - timedelta(minutes=num)
        elif unit in ("h", "hr", "hour", "hours"):
            return now - timedelta(hours=num)
        elif unit in ("d", "day", "days"):
            return now - timedelta(days=num)
        elif unit in ("w", "week", "weeks"):
            return now - timedelta(weeks=num)

    # Date formats
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
        "%d-%m-%Y",
        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y %H:%M",
        "%d.%m.%Y",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y/%m/%d",
        "%m/%d/%Y",
        "%m-%d-%Y",
        "%d/%m",
        "%d-%m",
    ]

    for fmt in formats:
        try:
            dt = datetime.strptime(val, fmt)
            if "%Y" not in fmt and "%y" not in fmt:
                dt = dt.replace(year=now.year)
            return dt.astimezone()
        except ValueError:
            continue

    raise ValueError(
        f"Unable to parse date/time '{val}'. Supported: 'today', 'yesterday', '3d', '7d', '24h', 'YYYY-MM-DD', 'DD/MM/YYYY', 'DD-MM-YYYY'."
    )



def filter_messages(
    messages: List[Dict[str, Any]],
    user_query: Optional[str] = None,
    since_dt: Optional[datetime] = None,
    until_dt: Optional[datetime] = None,
    text_query: Optional[str] = None,
    has_file: Optional[bool] = None,
    has_link: Optional[bool] = None,
    human_only: bool = False,
) -> List[Dict[str, Any]]:
    """Apply multiple filters in-memory on message list."""
    filtered = []

    user_norm = unidecode(user_query.strip()).lower() if user_query else None
    is_user_id = user_query.strip().isdigit() and len(user_query.strip()) >= 17 if user_query else False
    user_id_str = user_query.strip() if is_user_id else None

    query_norm = unidecode(text_query.strip()).lower() if text_query else None

    for msg in messages:
        # 1. Author Filter
        author = msg.get("author", {})
        is_bot = author.get("bot", False)

        if human_only and is_bot:
            continue

        if user_norm:
            author_id = str(author.get("id", ""))
            if user_id_str:
                if author_id != user_id_str:
                    continue
            else:
                username = unidecode(author.get("username", "")).lower()
                global_name = unidecode(author.get("global_name") or "").lower()
                if user_norm not in username and user_norm not in global_name:
                    continue

        # 2. Date Filter
        msg_ts_str = msg.get("timestamp")
        if msg_ts_str and (since_dt or until_dt):
            try:
                msg_dt = datetime.fromisoformat(msg_ts_str.replace("Z", "+00:00")).astimezone()
                if since_dt and msg_dt < since_dt:
                    continue
                if until_dt and msg_dt > until_dt:
                    continue
            except Exception:
                pass

        # 3. Content query filter
        content = msg.get("content", "")
        if query_norm:
            content_norm = unidecode(content).lower()
            if query_norm not in content_norm:
                continue

        # 4. Attachments filter
        attachments = msg.get("attachments", [])
        if has_file is True and not attachments:
            continue

        # 5. Link filter
        if has_link is True:
            has_url = bool(re.search(r"https?://", content))
            if not has_url:
                continue

        filtered.append(msg)

    return filtered
