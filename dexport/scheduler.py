"""
Periodic Loop Automation & Scheduler for dexport.
Executes automated tasks (chatting, reacting, bumping) on a recurring schedule.
"""

import asyncio
from datetime import datetime
import logging
from typing import Any, Callable, Dict, List, Optional

from .cdp import DEFAULT_PORT
from .client import DiscordClient

logger = logging.getLogger("dexport.scheduler")


class PeriodicLoopTask:
    """Runs a recurring background task every N seconds/minutes."""

    def __init__(
        self,
        guild_name: str,
        channel_name: str,
        interval_seconds: float = 120.0,
        message: Optional[str] = None,
        react_emoji: Optional[str] = None,
        react_count: int = 5,
        port: int = DEFAULT_PORT,
        on_tick: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ):
        self.guild_name = guild_name
        self.channel_name = channel_name
        self.interval_seconds = max(interval_seconds, 15.0)  # Safety minimum 15s
        self.message = message
        self.react_emoji = react_emoji
        self.react_count = react_count
        self.port = port
        self.on_tick = on_tick

        self.is_running = False
        self.tick_count = 0
        self.stats = {
            "start_time": None,
            "tick_count": 0,
            "last_tick_time": None,
            "guild_name": self.guild_name,
            "channel_name": self.channel_name,
            "interval_seconds": self.interval_seconds,
            "message": self.message,
            "react_emoji": self.react_emoji,
        }

    async def start(self) -> None:
        """Start the recurring loop."""
        self.is_running = True
        self.stats["start_time"] = datetime.now()

        async with DiscordClient(port=self.port, auto_restart=True) as client:
            g_info = await client.resolve_guild(self.guild_name)
            c_info = await client.resolve_channel(g_info["id"], self.channel_name)
            channel_id = c_info["id"]

            logger.info(
                f"Loop started: every {self.interval_seconds}s in #{c_info['name']} ({g_info['name']})..."
            )

            while self.is_running:
                try:
                    self.tick_count += 1
                    self.stats["tick_count"] = self.tick_count
                    self.stats["last_tick_time"] = datetime.now()

                    sent_msg_id = None
                    # 1. Send periodic message if configured
                    if self.message:
                        res = await client.send_message(channel_id, content=self.message)
                        sent_msg_id = res.get("id")
                        await asyncio.sleep(0.5)

                    # 2. Add reactions to recent messages if configured
                    reacted_list = []
                    if self.react_emoji:
                        reacted_list = await client.add_batch_reactions(
                            channel_id=channel_id,
                            emoji=self.react_emoji,
                            count=self.react_count,
                        )

                    tick_info = {
                        "tick": self.tick_count,
                        "guild_name": g_info["name"],
                        "channel_name": c_info["name"],
                        "message_sent": self.message,
                        "message_id": sent_msg_id,
                        "react_emoji": self.react_emoji,
                        "react_count": len(reacted_list),
                        "timestamp": datetime.now().strftime("%H:%M:%S"),
                        "next_in_seconds": int(self.interval_seconds),
                    }

                    if self.on_tick:
                        try:
                            if asyncio.iscoroutinefunction(self.on_tick):
                                await self.on_tick(tick_info)
                            else:
                                self.on_tick(tick_info)
                        except Exception as cb_err:
                            logger.debug(f"Loop tick callback error: {cb_err}")

                    # Sleep until next tick
                    await asyncio.sleep(self.interval_seconds)

                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Error in PeriodicLoopTask tick: {e}")
                    await asyncio.sleep(10.0)

    def stop(self) -> None:
        """Stop the loop."""
        self.is_running = False
