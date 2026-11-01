"""Authenticated desktop <-> phone bridge.

Protocol (JSON text frames, max 64 KB):
  phone -> desktop   {"type":"pair","code":"123456","device_id":"...","device_name":"..."}
                     {"type":"hello","device_id":"...","token":"...","device_name":"..."}
                     {"type":"result","id":"...","ok":true,"message":"...","data":{}}
                     {"type":"event","event":"battery","data":{"percent":72,"charging":true}}
                     {"type":"ping"}
                     {"type":"request","id":"...","action":"desktop.status"|"desktop.command",
                      "args":{...}}
  desktop -> phone   {"type":"paired","token":"...","device_id":"...","desktop_id":"..."}
                     {"type":"welcome","desktop_name":"..."}
                     {"type":"command","id":"...","action":"battery","args":{}}
                     {"type":"response","id":"...","ok":true,"message":"...","data":{}}
                     {"type":"event","event":"...","data":{}}
                     {"type":"pong"}
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
import socket
import time
import uuid
from typing import Any, Awaitable, Callable

from config import Settings
from database.repository import Repository
from events import EventBus
from security.audit_log import AuditLog
from security.authentication import PairingCodes, SecurityService

logger = logging.getLogger("jarvis.phone")

MAX_FRAME = 65536
PHONE_ACTIONS = {
    "battery", "app.open", "alarm.set", "contacts.search", "call.dial",
    "whatsapp.chat", "reels.start", "reels.stop", "status",
}
PHONE_REQUESTS = {"desktop.status", "desktop.command"}


class PhoneOffline(RuntimeError):
    pass


class PhoneBridge:
    def __init__(
        self,
        settings: Settings,
        repo: Repository,
        bus: EventBus,
        security: SecurityService,
        pairing: PairingCodes,
        audit: AuditLog,
    ):
        self.settings = settings
        self.repo = repo
        self.bus = bus
        self.security = security
        self.pairing = pairing
        self.audit = audit
        self._connections: dict[str, Any] = {}
        self._names: dict[str, str] = {}
        self._battery: dict[str, Any] = {}
        self._last_seen: dict[str, float] = {}
        self._pending: dict[str, tuple[str, asyncio.Future[dict[str, Any]]]] = {}
        self._rates: dict[str, list[float]] = {}
        self._last_active: str | None = None
        self.command_handler: Callable[[str, dict[str, Any], str], Awaitable[dict[str, Any]]] | None = None

    # ---------------------------------------------------------------- status
    def status(self) -> dict[str, Any]:
        device_id = self._last_active if self._last_active in self._connections else None
        if device_id is None and self._connections:
            device_id = next(iter(self._connections))
        battery = self._battery.get(device_id or "", {})
        return {
            "connected": device_id is not None,
            "device_id": device_id,
            "name": self._names.get(device_id or "", None),
            "battery": battery.get("percent"),
            "charging": battery.get("charging"),
            "last_seen": self._last_seen.get(device_id or ""),
            "desktop_id": self.security.device_id,
            "lan_ip": self.settings.lan_ip(),
            "port": self.settings.port,
            "bound_host": self.settings.host,
        }

    def _publish_status(self) -> None:
        self.bus.publish("phone.status", self.status())

    # -------------------------------------------------------------- pairing
    def pair_start(self) -> dict[str, Any]:
        code = self.pairing.create()
        lan_ip = self.settings.lan_ip()
        lan_url = f"ws://{lan_ip}:{self.settings.port}/ws/phone"
        loopback = self.settings.host in {"127.0.0.1", "localhost", "::1"}
        note = (
            "The backend is bound to loopback. For USB/ADB use 'adb reverse tcp:"
            f"{self.settings.port} tcp:{self.settings.port}'. For Wi-Fi set JARVIS_HOST=0.0.0.0, "
            "allow the port in Windows Firewall, and restart the backend."
            if loopback else
            "The backend is listening on the LAN. Only pair over a trusted private network."
        )
        return {
            "code": code.code,
            "expires_in": self.pairing.ttl,
            "ws_url_loopback": f"ws://127.0.0.1:{self.settings.port}/ws/phone",
            "ws_url_lan": lan_url,
            "desktop_id": self.security.device_id,
            "desktop_name": socket.gethostname(),
            "note": note,
        }

    # --------------------------------------------------------------- socket
    async def handle_socket(self, websocket: Any) -> None:
        await websocket.accept()
        device_id: str | None = None
        try:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=25)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "error", "message": "Handshake timed out."})
                return
            message = self._parse(raw)
            if message is None:
                await websocket.send_json({"type": "error", "message": "Invalid JSON."})
                return
            kind = message.get("type")
            if kind == "pair":
                device_id = await self._handle_pair(websocket, message)
            elif kind == "hello":
                device_id = await self._handle_hello(websocket, message)
            else:
                await websocket.send_json(
                    {"type": "error", "message": "Expected a 'pair' or 'hello' message first."}
                )
                return
            if not device_id:
                return
            await self._register(websocket, device_id, message.get("device_name"))
            await self._read_loop(websocket, device_id)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - socket errors must not kill the server
            logger.exception("Phone socket error")
        finally:
            if device_id:
                self._disconnect(device_id)
            try:
                await websocket.close()
            except Exception:  # noqa: BLE001
                pass

    async def _handle_pair(self, websocket: Any, message: dict[str, Any]) -> str | None:
        code = str(message.get("code", "")).strip()
        if not self.pairing.consume(code):
            self.audit.record("phone", "phone.pair", "high",
                              {"outcome": "invalid_code"}, "denied")
            await websocket.send_json(
                {"type": "error", "message": "Pairing code is invalid or expired. Generate a new one."}
            )
            return None
        device_id = str(message.get("device_id") or "").strip() or uuid.uuid4().hex
        if not _valid_device_id(device_id):
            await websocket.send_json({"type": "error", "message": "Invalid device id."})
            return None
        name = str(message.get("device_name") or "Android phone")[:80]
        token = self.security.new_device_token()
        self.repo.upsert_device(
            device_id, name, self.security.hash_token(token),
            {"platform": message.get("platform", "android"), "model": message.get("model", "")},
        )
        self.audit.record("phone", "phone.pair", "high",
                          {"device_id": device_id, "name": name}, "ok")
        await websocket.send_json(
            {
                "type": "paired",
                "token": token,
                "device_id": device_id,
                "desktop_id": self.security.device_id,
                "desktop_name": socket.gethostname(),
            }
        )
        return device_id

    async def _handle_hello(self, websocket: Any, message: dict[str, Any]) -> str | None:
        device_id = str(message.get("device_id") or "")
        token = str(message.get("token") or "")
        if not self.security.verify_device(device_id, token):
            self.audit.record("phone", "phone.connect", "high",
                              {"device_id": device_id}, "denied")
            await websocket.send_json(
                {"type": "error", "message": "Authentication failed. Revoke and re-pair the device."}
            )
            return None
        self.repo.touch_device(device_id)
        await websocket.send_json(
            {"type": "welcome", "desktop_id": self.security.device_id,
             "desktop_name": socket.gethostname()}
        )
        return device_id

    async def _register(self, websocket: Any, device_id: str, name: Any) -> None:
        previous = self._connections.get(device_id)
        if previous is not None and previous is not websocket:
            try:
                await previous.close()
            except Exception:  # noqa: BLE001
                pass
        self._connections[device_id] = websocket
        self._names[device_id] = str(name or self._names.get(device_id) or "Android phone")[:80]
        self._last_seen[device_id] = time.time()
        self._last_active = device_id
        self.bus.publish("phone.connected", {"device_id": device_id, "name": self._names[device_id]})
        self._publish_status()

    def _disconnect(self, device_id: str) -> None:
        if self._connections.pop(device_id, None) is not None:
            for pending_id, (owner, future) in list(self._pending.items()):
                if owner == device_id and not future.done():
                    future.set_exception(PhoneOffline("The phone disconnected."))
            self.bus.publish("phone.disconnected", {"device_id": device_id})
            self._publish_status()

    # ------------------------------------------------------------ read loop
    async def _read_loop(self, websocket: Any, device_id: str) -> None:
        while True:
            try:
                raw = await websocket.receive_text()
            except Exception:  # noqa: BLE001 - closed by peer
                return
            if len(raw) > MAX_FRAME:
                continue
            message = self._parse(raw)
            if message is None:
                continue
            self._last_seen[device_id] = time.time()
            kind = message.get("type")
            if kind == "result":
                self._resolve(message)
            elif kind == "event":
                self._handle_event(device_id, message)
            elif kind == "ping":
                await websocket.send_json({"type": "pong"})
            elif kind == "request":
                await self._handle_request(websocket, device_id, message)
            else:
                logger.debug("Ignoring phone message type %s", kind)

    def _handle_event(self, device_id: str, message: dict[str, Any]) -> None:
        event = str(message.get("event", ""))
        data = message.get("data") if isinstance(message.get("data"), dict) else {}
        if event == "battery":
            self._battery[device_id] = {
                "percent": data.get("percent"),
                "charging": data.get("charging"),
            }
            self._last_active = device_id
            self.repo.touch_device(device_id)
            self._publish_status()
        elif event == "notification":
            title = str(data.get("title", "Phone"))[:120]
            body = str(data.get("body", ""))[:500]
            self.repo.add_notification("phone", title, body)
            self.bus.publish("notification.created",
                             {"source": "phone", "title": title, "body": body})
        else:
            self.bus.publish("phone.event", {"event": event, "data": data})

    def _resolve(self, message: dict[str, Any]) -> None:
        request_id = str(message.get("id", ""))
        entry = self._pending.get(request_id)
        if entry and not entry[1].done():
            entry[1].set_result(
                {
                    "ok": bool(message.get("ok")),
                    "message": str(message.get("message", ""))[:1000],
                    "data": message.get("data") if isinstance(message.get("data"), dict) else {},
                }
            )

    async def _handle_request(self, websocket: Any, device_id: str, message: dict[str, Any]) -> None:
        request_id = str(message.get("id", ""))
        action = str(message.get("action", ""))
        if action not in PHONE_REQUESTS:
            await websocket.send_json(
                {"type": "response", "id": request_id, "ok": False,
                 "message": f"Request '{action}' is not allowed."}
            )
            return
        if not self._allow(device_id, limit=10, window=60):
            await websocket.send_json(
                {"type": "response", "id": request_id, "ok": False,
                 "message": "Rate limit exceeded."}
            )
            return
        if action == "desktop.status":
            await websocket.send_json(
                {"type": "response", "id": request_id, "ok": True,
                 "message": "Desktop online.", "data": self.status()}
            )
            return
        args = message.get("args") if isinstance(message.get("args"), dict) else {}
        if self.command_handler is None:
            await websocket.send_json(
                {"type": "response", "id": request_id, "ok": False,
                 "message": "Desktop command handling is not available."}
            )
            return
        try:
            result = await self.command_handler(action, args, device_id)
            await websocket.send_json(
                {"type": "response", "id": request_id, "ok": bool(result.get("ok")),
                 "message": str(result.get("message", ""))[:1000],
                 "data": result.get("data") or {}}
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Phone command failed")
            await websocket.send_json(
                {"type": "response", "id": request_id, "ok": False,
                 "message": f"Desktop rejected the command: {exc}"}
            )

    def _allow(self, device_id: str, limit: int, window: int) -> bool:
        now = time.time()
        hits = [t for t in self._rates.get(device_id, []) if now - t < window]
        if len(hits) >= limit:
            self._rates[device_id] = hits
            return False
        hits.append(now)
        self._rates[device_id] = hits
        return True

    # --------------------------------------------------------------- desktop
    async def request(self, action: str, args: dict[str, Any], timeout: float = 12.0) -> dict[str, Any]:
        if not self._connections:
            raise PhoneOffline("No Android phone is connected.")
        device_id = self._last_active if self._last_active in self._connections else next(
            iter(self._connections)
        )
        websocket = self._connections[device_id]
        request_id = uuid.uuid4().hex[:12]
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = (device_id, future)
        try:
            await websocket.send_json(
                {"type": "command", "id": request_id, "action": action, "args": args}
            )
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise PhoneOffline(
                "The phone did not respond in time. Check that the companion app is in the foreground."
            ) from exc
        except PhoneOffline:
            raise
        except Exception as exc:  # noqa: BLE001
            raise PhoneOffline(f"Phone connection failed: {exc}") from exc
        finally:
            self._pending.pop(request_id, None)

    async def push_event(self, event: str, data: dict[str, Any]) -> int:
        delivered = 0
        for websocket in list(self._connections.values()):
            try:
                await websocket.send_json({"type": "event", "event": event, "data": data})
                delivered += 1
            except Exception:  # noqa: BLE001
                continue
        return delivered

    async def disconnect(self, device_id: str) -> bool:
        websocket = self._connections.get(device_id)
        if websocket is None:
            return False
        try:
            await websocket.close(code=1008, reason="Device revoked")
        except Exception:  # noqa: BLE001
            pass
        self._disconnect(device_id)
        return True

    async def close_all(self) -> None:
        for device_id in list(self._connections):
            websocket = self._connections.get(device_id)
            if websocket is not None:
                try:
                    await websocket.close(code=1001, reason="Desktop shutting down")
                except Exception:  # noqa: BLE001
                    pass
            self._disconnect(device_id)

    @staticmethod
    def _parse(raw: str) -> dict[str, Any] | None:
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return value if isinstance(value, dict) else None


def _valid_device_id(value: str) -> bool:
    return bool(value) and len(value) <= 64 and all(
        ch.isalnum() or ch in "-_.:" for ch in value
    )
