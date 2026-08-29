"""
Discord API client executing requests via CDP in-page fetch.
"""

import asyncio
from datetime import datetime
import logging
import re
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple, Union
from unidecode import unidecode

from .cdp import CDPClient, DEFAULT_PORT
from .filters import filter_messages, parse_datetime_input

logger = logging.getLogger("dexport.client")



def normalize_string(s: str) -> str:
    """Normalize string for fuzzy matching (removes accents, leading #, lowercases, standardizes separators)."""
    s = s.strip().lstrip("#")
    s = unidecode(s).lower()
    s = re.sub(r"[\s\-_]+", " ", s).strip()
    return s


class DiscordClient:
    """High-level Discord API wrapper that executes requests inside the Discord desktop Chromium context."""

    def __init__(self, port: int = DEFAULT_PORT, auto_restart: bool = True):
        self.cdp = CDPClient(port=port)
        self.auto_restart = auto_restart
        self._cached_guilds: Optional[List[Dict[str, Any]]] = None
        self._cached_channels: Dict[str, List[Dict[str, Any]]] = {}
        self._current_user: Optional[Dict[str, Any]] = None

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def connect(self) -> None:
        """Connect to Discord desktop CDP."""
        await self.cdp.connect(auto_restart=self.auto_restart)

    async def close(self) -> None:
        """Close CDP connection."""
        await self.cdp.close()

    async def _request(
        self,
        endpoint: str,
        method: str = "GET",
        body: Optional[Any] = None,
        custom_headers: Optional[Dict[str, str]] = None,
        retries: int = 4,
    ) -> Any:
        """Execute request inside Discord page and handle errors + auto 429 rate limit retry."""
        for attempt in range(retries):
            res = await self.cdp.in_page_fetch(
                endpoint=endpoint,
                method=method,
                body=body,
                custom_headers=custom_headers,
            )

            status = res.get("status", 0)
            data = res.get("data")

            # Handle 429 Rate Limit
            if status == 429 and attempt < retries - 1:
                retry_after = 1.0
                if isinstance(data, dict):
                    retry_after = float(data.get("retry_after", 1.0))
                await asyncio.sleep(retry_after + 0.2)
                continue

            if not res.get("ok"):
                err_msg = res.get("error") or res.get("statusText")
                if isinstance(data, dict):
                    err_msg = data.get("message", err_msg)
                raise RuntimeError(f"Discord API Error [HTTP {status}]: {err_msg}")

            return res.get("data")


    # =========================================================================
    # User Profile & Status
    # =========================================================================

    async def get_current_user(self) -> Dict[str, Any]:
        """Fetch logged-in user profile (/api/v9/users/@me)."""
        if not self._current_user:
            self._current_user = await self._request("/api/v9/users/@me")
        return self._current_user

    # =========================================================================
    # Guilds & Channels
    # =========================================================================

    async def get_guilds(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """Fetch list of joined guilds / servers."""
        if self._cached_guilds is None or force_refresh:
            guilds = await self._request("/api/v9/users/@me/guilds")
            self._cached_guilds = sorted(guilds, key=lambda g: g.get("name", "").lower())
        return self._cached_guilds

    async def get_channels(self, guild_id: str, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """Fetch list of channels in a guild."""
        if guild_id not in self._cached_channels or force_refresh:
            channels = await self._request(f"/api/v9/guilds/{guild_id}/channels")
            # Sort channels by position and type
            self._cached_channels[guild_id] = sorted(
                channels,
                key=lambda c: (c.get("type", 0) == 4, c.get("position", 0))  # Category header first or positional
            )
        return self._cached_channels[guild_id]

    async def get_channel(self, channel_id: str) -> Dict[str, Any]:
        """Fetch specific channel info."""
        return await self._request(f"/api/v9/channels/{channel_id}")

    # =========================================================================
    # Smart Resolver (Name -> ID)
    # =========================================================================

    async def resolve_guild(self, query: str) -> Dict[str, Any]:
        """
        Resolve a guild by ID, exact name, or fuzzy name (Vietnamese accent-insensitive).
        Returns the matched guild dict.
        """
        query_str = str(query).strip()
        guilds = await self.get_guilds()

        # 1. Match by exact ID
        if query_str.isdigit() and len(query_str) >= 17:
            for g in guilds:
                if str(g.get("id")) == query_str:
                    return g

        # 2. Exact match on name
        for g in guilds:
            if g.get("name", "") == query_str:
                return g

        # 3. Case-insensitive match
        for g in guilds:
            if g.get("name", "").lower() == query_str.lower():
                return g

        # 4. Normalized match (accents removed)
        norm_query = normalize_string(query_str)
        norm_matches = []
        for g in guilds:
            norm_name = normalize_string(g.get("name", ""))
            if norm_name == norm_query:
                norm_matches.append(g)

        if len(norm_matches) == 1:
            return norm_matches[0]
        elif len(norm_matches) > 1:
            names = ", ".join(f"'{g['name']}' ({g['id']})" for g in norm_matches[:5])
            raise ValueError(f"Có nhiều server trùng khớp với '{query_str}': {names}. Hãy dùng ID hoặc tên cụ thể hơn.")

        # 5. Substring match
        substring_matches = []
        for g in guilds:
            norm_name = normalize_string(g.get("name", ""))
            if norm_query in norm_name:
                substring_matches.append(g)

        if len(substring_matches) == 1:
            return substring_matches[0]
        elif len(substring_matches) > 1:
            names = ", ".join(f"'{g['name']}' ({g['id']})" for g in substring_matches[:5])
            raise ValueError(f"Tìm thấy nhiều server phù hợp với '{query_str}': {names}. Hãy nhập tên chi tiết hơn.")

        available = ", ".join(f"'{g['name']}'" for g in guilds[:8])
        raise ValueError(f"Không tìm thấy server '{query_str}'. Một số server của bạn: {available}...")

    async def resolve_channel(self, guild_id: str, query: str) -> Dict[str, Any]:
        """
        Resolve a channel in a guild by ID, name, or slug (e.g. 'luoi-chat-tong').
        """
        query_str = str(query).strip().lstrip("#")
        channels = await self.get_channels(guild_id)

        # 1. Match by exact ID
        if query_str.isdigit() and len(query_str) >= 17:
            for c in channels:
                if str(c.get("id")) == query_str:
                    return c

        # 2. Exact name match
        for c in channels:
            if c.get("name") == query_str:
                return c

        # 3. Case-insensitive match
        for c in channels:
            if str(c.get("name", "")).lower() == query_str.lower():
                return c

        # 4. Normalized match (accents & dashes normalized)
        norm_query = normalize_string(query_str)
        norm_matches = []
        for c in channels:
            norm_name = normalize_string(c.get("name", ""))
            if norm_name == norm_query:
                norm_matches.append(c)

        if len(norm_matches) == 1:
            return norm_matches[0]
        elif len(norm_matches) > 1:
            # Prefer text channels (type == 0 or type == 5) over others
            text_candidates = [c for c in norm_matches if c.get("type") in (0, 5, 15)]
            if len(text_candidates) == 1:
                return text_candidates[0]
            names = ", ".join(f"#{c.get('name')} ({c.get('id')})" for c in norm_matches[:5])
            raise ValueError(f"Nhiều kênh trùng khớp với '{query_str}': {names}.")

        # 5. Substring match
        substring_matches = []
        for c in channels:
            norm_name = normalize_string(c.get("name", ""))
            if norm_query in norm_name:
                substring_matches.append(c)

        if len(substring_matches) == 1:
            return substring_matches[0]
        elif len(substring_matches) > 1:
            text_candidates = [c for c in substring_matches if c.get("type") in (0, 5, 15)]
            if len(text_candidates) == 1:
                return text_candidates[0]
            names = ", ".join(f"#{c.get('name')} ({c.get('id')})" for c in substring_matches[:5])
            raise ValueError(f"Tìm thấy nhiều kênh phù hợp với '{query_str}': {names}.")

        text_channels = [f"#{c.get('name')}" for c in channels if c.get("type") in (0, 5)][:10]
        sample = ", ".join(text_channels)
        raise ValueError(f"Không tìm thấy kênh '{query_str}' trong server này. Một số kênh: {sample}...")

    # =========================================================================
    # Messages & Chat Operations
    # =========================================================================

    async def get_messages(
        self,
        channel_id: str,
        limit: int = 50,
        before: Optional[str] = None,
        after: Optional[str] = None,
        around: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Fetch messages from channel with automatic pagination support.
        """
        all_messages: List[Dict[str, Any]] = []
        remaining = limit
        current_before = before

        while remaining > 0:
            batch_size = min(remaining, 100)
            params = [f"limit={batch_size}"]
            if current_before:
                params.append(f"before={current_before}")
            elif after:
                params.append(f"after={after}")
            elif around:
                params.append(f"around={around}")

            query_param_str = "&".join(params)
            endpoint = f"/api/v9/channels/{channel_id}/messages?{query_param_str}"
            batch = await self._request(endpoint)

            if not batch or not isinstance(batch, list):
                break

            all_messages.extend(batch)
            remaining -= len(batch)

            if len(batch) < batch_size or after or around:
                # Reached beginning or single targeted query
                break

            current_before = batch[-1].get("id")
            if not current_before:
                break

            # Short pause to prevent aggressive rate limits
            if remaining > 0:
                await asyncio.sleep(0.15)

        return all_messages

    async def get_filtered_messages(

        self,
        channel_id: str,
        user_query: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        text_query: Optional[str] = None,
        has_file: Optional[bool] = None,
        has_link: Optional[bool] = None,
        human_only: bool = False,
        limit: int = 50,
        scan_depth: int = 1000,
    ) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """
        Fetch channel messages and apply multi-dimensional filters:
        - user_query: filter by author name/id
        - since / until: filter by date range ('today', '3d', 'YYYY-MM-DD')
        - text_query: keyword search in content
        - has_file: only messages with attachments
        - has_link: only messages with URLs
        - human_only: exclude bots
        """
        since_dt = parse_datetime_input(since) if since else None
        until_dt = parse_datetime_input(until) if until else None

        matched: List[Dict[str, Any]] = []
        resolved_user: Optional[Dict[str, Any]] = None
        current_before = None
        remaining_scan = scan_depth

        while remaining_scan > 0 and len(matched) < limit:
            batch_size = min(remaining_scan, 100)
            endpoint = f"/api/v9/channels/{channel_id}/messages?limit={batch_size}"
            if current_before:
                endpoint += f"&before={current_before}"

            batch = await self._request(endpoint)
            if not batch or not isinstance(batch, list):
                break

            remaining_scan -= len(batch)

            # Check if any messages are older than since_dt
            filtered_batch = filter_messages(
                batch,
                user_query=user_query,
                since_dt=since_dt,
                until_dt=until_dt,
                text_query=text_query,
                has_file=has_file,
                has_link=has_link,
                human_only=human_only,
            )

            for msg in filtered_batch:
                matched.append(msg)
                if not resolved_user and user_query:
                    resolved_user = msg.get("author")
                if len(matched) >= limit:
                    break

            # If the oldest message in batch is already older than since_dt, no need to scan further back
            if since_dt and batch:
                oldest_ts_str = batch[-1].get("timestamp")
                if oldest_ts_str:
                    try:
                        oldest_dt = datetime.fromisoformat(oldest_ts_str.replace("Z", "+00:00")).astimezone()
                        if oldest_dt < since_dt:
                            break
                    except Exception:
                        pass

            if len(batch) < batch_size:
                break
            current_before = batch[-1].get("id")
            if not current_before:
                break

            await asyncio.sleep(0.1)

        return matched, resolved_user

    async def get_user_messages(
        self,
        channel_id: str,
        user_query: str,
        limit: int = 50,
        scan_depth: int = 500,
    ) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """Backward compatible helper for user messages."""
        return await self.get_filtered_messages(
            channel_id=channel_id,
            user_query=user_query,
            limit=limit,
            scan_depth=scan_depth,
        )



    async def send_typing(self, channel_id: str) -> None:
        """Trigger 'is typing...' indicator in a channel."""
        try:
            await self._request(
                f"/api/v9/channels/{channel_id}/typing",
                method="POST",
                body={},
            )
        except Exception:
            pass

    async def send_message(
        self,
        channel_id: str,
        content: str,
        reply_to_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send a new message or reply to an existing message."""
        payload: Dict[str, Any] = {
            "content": content,
            "flags": 0,
            "tts": False,
        }
        if reply_to_id:
            payload["message_reference"] = {
                "channel_id": channel_id,
                "message_id": reply_to_id,
            }

        return await self._request(
            f"/api/v9/channels/{channel_id}/messages",
            method="POST",
            body=payload,
        )


    async def add_reaction(
        self,
        channel_id: str,
        message_id: str,
        emoji: str,
    ) -> Any:
        """Add a reaction emoji to a message."""
        encoded_emoji = urllib.parse.quote(emoji.strip())
        endpoint = f"/api/v9/channels/{channel_id}/messages/{message_id}/reactions/{encoded_emoji}/@me"
        return await self._request(endpoint, method="PUT")

    async def delete_reaction(
        self,
        channel_id: str,
        message_id: str,
        emoji: str,
    ) -> Any:
        """Remove user's reaction emoji from a message."""
        encoded_emoji = urllib.parse.quote(emoji.strip())
        endpoint = f"/api/v9/channels/{channel_id}/messages/{message_id}/reactions/{encoded_emoji}/%40me"
        return await self._request(endpoint, method="DELETE")
