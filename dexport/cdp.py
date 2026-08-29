"""
Chrome DevTools Protocol (CDP) manager and WebSocket client for Discord Desktop.
"""

import asyncio
import json
import logging
import os
import platform
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional
import aiohttp
import websockets

logger = logging.getLogger("dexport.cdp")

DEFAULT_PORT = 41829


class DiscordNotFoundError(Exception):
    """Raised when Discord Desktop executable is not found."""
    pass


class CDPConnectionError(Exception):
    """Raised when connecting to CDP fails."""
    pass


class DiscordProcessManager:
    """Manages Discord desktop lifecycle and ensures CDP debugging port is active."""

    def __init__(self, port: int = DEFAULT_PORT):
        self.port = port
        self.os_type = platform.system().lower()

    async def is_port_open(self) -> bool:
        """Check if CDP JSON endpoint is reachable."""
        url = f"http://127.0.0.1:{self.port}/json/version"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=1.5)) as resp:
                    return resp.status == 200
        except Exception:
            return False

    def find_discord_executable(self) -> str:
        """Find Discord executable path based on OS."""
        if self.os_type == "darwin":
            candidates = [
                "/Applications/Discord.app/Contents/MacOS/Discord",
                "/Applications/Discord Canary.app/Contents/MacOS/Discord Canary",
                "/Applications/Discord PTB.app/Contents/MacOS/Discord PTB",
                os.path.expanduser("~/Applications/Discord.app/Contents/MacOS/Discord"),
            ]
            for path in candidates:
                if os.path.isfile(path) and os.access(path, os.X_OK):
                    return path

        elif self.os_type == "linux":
            candidates = [
                "/usr/bin/discord",
                "/usr/bin/discord-canary",
                "/usr/bin/discord-ptb",
                "/opt/discord/Discord",
                os.path.expanduser("~/.local/bin/discord"),
            ]
            for path in candidates:
                if os.path.isfile(path) and os.access(path, os.X_OK):
                    return path
            # Try `which`
            for bin_name in ["discord", "discord-canary", "discord-ptb"]:
                try:
                    res = subprocess.run(["which", bin_name], capture_output=True, text=True)
                    if res.returncode == 0 and res.stdout.strip():
                        return res.stdout.strip()
                except Exception:
                    pass

        elif self.os_type == "windows":
            local_appdata = os.environ.get("LOCALAPPDATA", "")
            for folder in ["Discord", "DiscordCanary", "DiscordPTB"]:
                dir_path = os.path.join(local_appdata, folder)
                if os.path.isdir(dir_path):
                    # Search for app-* folders
                    for sub in os.listdir(dir_path):
                        if sub.startswith("app-"):
                            exe = os.path.join(dir_path, sub, f"{folder}.exe")
                            if os.path.isfile(exe):
                                return exe

        raise DiscordNotFoundError(
            f"Không tìm thấy Discord Desktop trên hệ điều hành {self.os_type}. Vui lòng kiểm tra lại cài đặt Discord."
        )

    def kill_existing_discord(self) -> None:
        """Terminate existing Discord instances to restart with debugging port."""
        try:
            if self.os_type == "darwin":
                subprocess.run(["pkill", "-f", "Discord.app/Contents/MacOS/Discord"], capture_output=True)
                subprocess.run(["killall", "Discord"], capture_output=True)
            elif self.os_type == "linux":
                subprocess.run(["pkill", "-f", "discord"], capture_output=True)
            elif self.os_type == "windows":
                subprocess.run(["taskkill", "/F", "/IM", "Discord.exe"], capture_output=True)
            time.sleep(1.0)
        except Exception as e:
            logger.debug(f"Kill discord error (non-fatal): {e}")

    async def ensure_discord_running_with_cdp(self, auto_restart: bool = True) -> bool:
        """Ensure Discord is running with CDP port active. Restarts if necessary."""
        if await self.is_port_open():
            return True

        if not auto_restart:
            raise CDPConnectionError(
                f"Discord không mở cổng CDP {self.port}. Hãy bật cờ --remote-debugging-port={self.port} hoặc cho phép auto_restart."
            )

        exe_path = self.find_discord_executable()
        logger.info(f"Đang khởi động lại Discord với CDP port {self.port} từ: {exe_path}...")
        self.kill_existing_discord()

        # Launch Discord with flag in background
        cmd = [exe_path, f"--remote-debugging-port={self.port}"]
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )

        # Wait for CDP endpoint to become ready
        max_attempts = 40  # 20 seconds total
        for _ in range(max_attempts):
            await asyncio.sleep(0.5)
            if await self.is_port_open():
                return True

        raise CDPConnectionError(
            f"Đã khởi động Discord nhưng cổng CDP {self.port} không phản hồi sau 20s."
        )


class CDPClient:
    """CDP WebSocket client to evaluate JS and sniff headers inside Discord renderer."""

    def __init__(self, port: int = DEFAULT_PORT):
        self.port = port
        self.proc_mgr = DiscordProcessManager(port)
        self.ws: Optional[websockets.ClientConnection] = None
        self._msg_id = 0
        self._pending_requests: Dict[int, asyncio.Future] = {}
        self._target_info: Optional[Dict[str, Any]] = None
        self._read_loop_task: Optional[asyncio.Task] = None
        self._headers_cache: Dict[str, str] = {}
        self._auth_token: Optional[str] = None

    async def connect(self, auto_restart: bool = True) -> None:
        """Connect to Discord's CDP debugger WebSocket."""
        await self.proc_mgr.ensure_discord_running_with_cdp(auto_restart=auto_restart)

        # Fetch targets from http://127.0.0.1:{port}/json
        url = f"http://127.0.0.1:{self.port}/json"
        target_ws_url = None

        # Give Discord page a moment to load if just started
        for _ in range(15):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=2)) as resp:
                        if resp.status == 200:
                            targets = await resp.json()
                            target_ws_url = self._select_discord_target(targets)
                            if target_ws_url:
                                break
            except Exception:
                pass
            await asyncio.sleep(1.0)

        if not target_ws_url:
            raise CDPConnectionError(
                f"Không tìm thấy target renderer Discord tại http://127.0.0.1:{self.port}/json"
            )

        logger.info(f"Kết nối tới CDP WebSocket: {target_ws_url}")
        self.ws = await websockets.connect(
            target_ws_url,
            max_size=100 * 1024 * 1024,  # 100MB max message size
            ping_interval=20,
            ping_timeout=20,
        )
        self._read_loop_task = asyncio.create_task(self._listen_loop())

        # Enable essential CDP domains
        await self.send_command("Runtime.enable")
        await self.send_command("Network.enable")
        await self.send_command("Page.enable")

        # Sniff auth headers / token
        await self._init_session_auth()

    def _select_discord_target(self, targets: List[Dict[str, Any]]) -> Optional[str]:
        """Find the best matching Discord window target."""
        # 1. Prefer page with discord.com/channels or title containing Discord
        for t in targets:
            if t.get("type") == "page":
                url = t.get("url", "")
                title = t.get("title", "")
                ws_url = t.get("webSocketDebuggerUrl")
                if ws_url and ("discord.com" in url or "Discord" in title or "channels" in url):
                    self._target_info = t
                    return ws_url

        # 2. Fallback to any page type
        for t in targets:
            if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                self._target_info = t
                return t.get("webSocketDebuggerUrl")

        return None

    async def _listen_loop(self) -> None:
        """Background loop to receive CDP JSON-RPC messages and events."""
        try:
            async for raw_msg in self.ws:
                msg = json.loads(raw_msg)
                msg_id = msg.get("id")
                if msg_id is not None and msg_id in self._pending_requests:
                    fut = self._pending_requests.pop(msg_id)
                    if not fut.done():
                        fut.set_result(msg)
                elif "method" in msg:
                    # Event received (e.g. Network.requestWillBeSent)
                    self._handle_cdp_event(msg)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"CDP listen loop error: {e}")

    def _handle_cdp_event(self, event: Dict[str, Any]) -> None:
        """Handle incoming CDP events like Network requests to sniff headers."""
        method = event.get("method")
        params = event.get("params", {})

        if method == "Network.requestWillBeSent":
            request = params.get("request", {})
            url = request.get("url", "")
            if "discord.com/api" in url or "/api/v" in url:
                headers = request.get("headers", {})
                for k, v in headers.items():
                    k_lower = k.lower()
                    if k_lower in ("authorization", "x-super-properties", "x-discord-locale", "x-discord-timezone"):
                        self._headers_cache[k] = v
                        if k_lower == "authorization" and not self._auth_token:
                            self._auth_token = v
                            logger.debug("Captured Authorization header from network traffic.")

    def is_connected(self) -> bool:
        """Check if WebSocket connection is active and open."""
        if not self.ws:
            return False
        if hasattr(self.ws, "state"):
            return getattr(self.ws.state, "name", "") == "OPEN" or self.ws.state == 1
        if hasattr(self.ws, "closed"):
            return not self.ws.closed
        if hasattr(self.ws, "close_code"):
            return self.ws.close_code is None
        return True

    async def send_command(self, method: str, params: Optional[Dict[str, Any]] = None, timeout: float = 15.0) -> Dict[str, Any]:
        """Send a JSON-RPC command to CDP and await response."""
        if not self.is_connected():
            raise CDPConnectionError("CDP WebSocket chưa được kết nối.")

        self._msg_id += 1
        current_id = self._msg_id
        payload = {"id": current_id, "method": method, "params": params or {}}


        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._pending_requests[current_id] = fut

        await self.ws.send(json.dumps(payload))
        try:
            res = await asyncio.wait_for(fut, timeout=timeout)
            if "error" in res:
                raise RuntimeError(f"CDP Error ({method}): {res['error'].get('message', res['error'])}")
            return res.get("result", {})
        except asyncio.TimeoutError:
            self._pending_requests.pop(current_id, None)
            raise TimeoutError(f"Hết thời gian chờ phản hồi CDP cho lệnh '{method}'")

    async def evaluate_js(self, expression: str, await_promise: bool = True, return_by_value: bool = True) -> Any:
        """Evaluate a JavaScript expression inside the Discord renderer."""
        res = await self.send_command(
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": await_promise,
                "returnByValue": return_by_value,
                "userGesture": True,
            },
        )
        result_obj = res.get("result", {})
        if res.get("exceptionDetails"):
            exc = res["exceptionDetails"]
            text = exc.get("text", "")
            desc = exc.get("exception", {}).get("description", text)
            raise RuntimeError(f"Lỗi JavaScript trong Discord: {desc}")

        return result_obj.get("value")

    async def _init_session_auth(self) -> None:
        """Extract or sniff Authorization token from Discord runtime."""
        js_extract_token = """
        (async () => {
            try {
                // Method 1: Iframe localStorage + Electron safeStorage decryption (macOS Keychain / Windows DPAPI)
                const iframe = document.createElement('iframe');
                document.body.appendChild(iframe);
                const rawToken = JSON.parse(iframe.contentWindow.localStorage.getItem('token'));
                document.body.removeChild(iframe);

                if (rawToken && typeof rawToken === 'string') {
                    if (rawToken.startsWith('dQw4w9WgXcQ:')) {
                        const stripped = rawToken.replace(/^dQw4w9WgXcQ:/, '');
                        if (window.DiscordNative?.safeStorage?.decryptString) {
                            const decrypted = await window.DiscordNative.safeStorage.decryptString(stripped);
                            if (decrypted) {
                                return { token: decrypted, source: 'safeStorage_decrypted' };
                            }
                        }
                    } else {
                        return { token: rawToken, source: 'localStorage_raw' };
                    }
                }
            } catch (e) {}

            try {
                // Method 2: Webpack internal modules fallback
                if (window.webpackChunkdiscord_app) {
                    let req;
                    window.webpackChunkdiscord_app.push([[Symbol()], {}, r => { req = r; }]);
                    if (req && req.c) {
                        for (const id of Object.keys(req.c)) {
                            const mod = req.c[id]?.exports;
                            if (!mod) continue;
                            if (typeof mod.getToken === 'function') {
                                const t = mod.getToken();
                                if (t) return { token: t, source: 'webpack_getToken' };
                            }
                            if (mod.default && typeof mod.default.getToken === 'function') {
                                const t = mod.default.getToken();
                                if (t) return { token: t, source: 'webpack_default_getToken' };
                            }
                        }
                    }
                }
            } catch (e) {}

            return { token: null, source: 'none' };
        })()
        """
        try:
            res = await self.evaluate_js(js_extract_token)
            if isinstance(res, dict) and res.get("token"):
                self._auth_token = res["token"]
                self._headers_cache["authorization"] = res["token"]
                logger.debug(f"Trích xuất Token thành công qua {res.get('source')}")
                return
        except Exception as e:
            logger.debug(f"Không lấy được token qua JS direct: {e}")

        # Fallback: trigger fetch to sniff Network traffic
        if not self._auth_token:
            trigger_fetch_js = """
            (async () => {
                try {
                    await window.fetch('/api/v9/users/@me/affinities/guilds', { method: 'GET' });
                } catch(e) {}
            })()
            """
            try:
                await self.evaluate_js(trigger_fetch_js)
                await asyncio.sleep(0.5)
            except Exception:
                pass


    async def in_page_fetch(
        self,
        endpoint: str,
        method: str = "GET",
        body: Optional[Any] = None,
        custom_headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Execute fetch() INSIDE the Discord page context via CDP.
        Ensures TLS fingerprint, User-Agent, and cookies match the genuine Discord app.
        """
        headers = {
            "Accept": "*/*",
            "Content-Type": "application/json",
        }

        # Add sniffed/extracted auth header if present
        if self._auth_token:
            headers["Authorization"] = self._auth_token
        elif "authorization" in self._headers_cache:
            headers["Authorization"] = self._headers_cache["authorization"]

        # Merge other sniffed headers if available
        for k, v in self._headers_cache.items():
            if k.lower().startswith("x-"):
                headers[k] = v

        if custom_headers:
            headers.update(custom_headers)

        full_url = endpoint if endpoint.startswith("http") else f"https://discord.com{endpoint}"
        body_json_str = json.dumps(body) if body is not None else "null"
        headers_json_str = json.dumps(headers)

        js_code = f"""
        (async () => {{
            const url = {json.dumps(full_url)};
            const method = {json.dumps(method.upper())};
            const reqHeaders = {headers_json_str};
            const reqBody = {body_json_str};

            const options = {{
                method: method,
                headers: reqHeaders,
            }};

            if (reqBody !== null && method !== 'GET' && method !== 'HEAD') {{
                options.body = typeof reqBody === 'string' ? reqBody : JSON.stringify(reqBody);
            }}

            try {{
                const res = await window.fetch(url, options);
                const status = res.status;
                const ok = res.ok;
                const statusText = res.statusText;
                let data = null;
                const text = await res.text();
                try {{
                    data = JSON.parse(text);
                }} catch (e) {{
                    data = text;
                }}
                return {{ ok, status, statusText, data, error: null }};
            }} catch (err) {{
                return {{ ok: false, status: 0, statusText: 'Fetch Error', data: null, error: err.toString() }};
            }}
        }})()
        """

        result = await self.evaluate_js(js_code)
        if not isinstance(result, dict):
            raise RuntimeError(f"Kết quả fetch không hợp lệ: {result}")

        return result

    async def close(self) -> None:
        """Close WebSocket connection cleanly."""
        if self._read_loop_task:
            self._read_loop_task.cancel()
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
            self.ws = None

