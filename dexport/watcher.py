"""
Targeted User & Channel Watcher module for dexport.
Monitors Discord channels in real-time and triggers alerts whenever a specific user sends a message.
"""

import asyncio
from datetime import datetime
import logging
from typing import Any, Callable, Dict, List, Optional, Union
from unidecode import unidecode

from .cdp import DEFAULT_PORT
from .client import DiscordClient
from .exporter import parse_timestamp

logger = logging.getLogger("dexport.watcher")


class UserWatcher:
    """Monitors channels in background for new messages from a specific targeted user."""

    def __init__(
        self,
        guild_name: str,
        channel_name: str,
        target_user: str,
        on_message: Callable[[Dict[str, Any]], Any],
        poll_interval: float = 2.5,
        port: int = DEFAULT_PORT,
    ):
        self.guild_name = guild_name
        self.channel_name = channel_name
        self.target_user = target_user.strip()
        self.on_message = on_message
        self.poll_interval = poll_interval
        self.port = port

        self.is_running = False
        self.last_seen_msg_id = "0"
        self.stats = {
            "target_user": self.target_user,
            "guild_name": self.guild_name,
            "channel_name": self.channel_name,
            "detected_count": 0,
            "start_time": None,
        }

    async def start(self) -> None:
        """Start the background monitoring loop."""
        self.is_running = True
        self.stats["start_time"] = datetime.now()

        norm_target = unidecode(self.target_user).lower()
        is_id = self.target_user.isdigit() and len(self.target_user) >= 17

        async with DiscordClient(port=self.port, auto_restart=True) as client:
            g_info = await client.resolve_guild(self.guild_name)
            c_info = await client.resolve_channel(g_info["id"], self.channel_name)
            channel_id = c_info["id"]

            initial_msgs = await client.get_messages(channel_id, limit=3)
            if initial_msgs:
                self.last_seen_msg_id = initial_msgs[0]["id"]

            logger.info(
                f"Watcher started: monitoring '{self.target_user}' in #{c_info['name']} ({g_info['name']})..."
            )

            while self.is_running:
                try:
                    await asyncio.sleep(self.poll_interval)

                    new_msgs = await client.get_messages(channel_id, limit=15, after=self.last_seen_msg_id)
                    if not new_msgs:
                        continue

                    sorted_new = sorted(new_msgs, key=lambda m: int(m.get("id", "0")))
                    self.last_seen_msg_id = sorted_new[-1]["id"]

                    for msg in sorted_new:
                        author = msg.get("author", {})
                        author_id = str(author.get("id", ""))
                        author_user = author.get("username", "").lower()
                        author_global = (author.get("global_name") or "").lower()
                        author_norm_global = unidecode(author_global)
                        author_norm_user = unidecode(author_user)

                        matched = False
                        if is_id and author_id == self.target_user:
                            matched = True
                        elif norm_target in author_norm_user or norm_target in author_norm_global:
                            matched = True

                        if matched:
                            self.stats["detected_count"] += 1
                            display_name = author.get("global_name") or author.get("username", "Unknown")
                            user_tag = f"@{author.get('username')}" if author.get("username") else ""

                            event_info = {
                                "author_name": display_name,
                                "author_tag": user_tag,
                                "author_id": author_id,
                                "guild_name": g_info["name"],
                                "channel_name": c_info["name"],
                                "content": msg.get("content", ""),
                                "attachments": msg.get("attachments", []),
                                "timestamp": parse_timestamp(msg.get("timestamp")),
                                "message_id": msg.get("id"),
                                "referenced_message": msg.get("referenced_message"),
                            }

                            logger.info(f"Watcher triggered for '{display_name}': {event_info['content']}")

                            if self.on_message:
                                try:
                                    if asyncio.iscoroutinefunction(self.on_message):
                                        await self.on_message(event_info)
                                    else:
                                        self.on_message(event_info)
                                except Exception as cb_err:
                                    logger.debug(f"Watcher callback error: {cb_err}")

                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.debug(f"Watcher loop error: {e}")
                    await asyncio.sleep(4.0)

    def stop(self) -> None:
        """Stop monitoring."""
        self.is_running = False
