from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import websockets

if __package__ in (None, ""):
    sys.path.append(str(Path(__file__).resolve().parents[1]))
    from voicechat.auth import validate_credentials
    from voicechat.protocol import decode_message, encode_message, unpack_audio_packet
    from voicechat.ui import banner, error, info, init_console, ok, prompt_text, section, warn
else:
    from .auth import validate_credentials
    from .protocol import decode_message, encode_message, unpack_audio_packet
    from .ui import banner, error, info, init_console, ok, prompt_text, section, warn


@dataclass(slots=True)
class ClientSession:
    websocket: Any
    username: str
    room: str
    token: str | None
    session_id: int
    udp_addr: tuple[str, int] | None = None
    last_heartbeat: float = 0.0


class RelayState:
    def __init__(self) -> None:
        self.clients_by_ws: dict[Any, ClientSession] = {}
        self.clients_by_session_id: dict[int, ClientSession] = {}
        self.rooms: dict[str, set[Any]] = {}
        self.lock = asyncio.Lock()


class VoiceRelayDatagramProtocol(asyncio.DatagramProtocol):
    def __init__(self, state: RelayState) -> None:
        self.state = state
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        packet = unpack_audio_packet(data)
        if packet is None:
            return

        session = self.state.clients_by_session_id.get(packet.session_id)
        if session is None:
            return

        session.udp_addr = addr
        room_peers = self.state.rooms.get(session.room, set())
        for peer_ws in room_peers:
            if peer_ws == session.websocket:
                continue
            peer_session = self.state.clients_by_ws.get(peer_ws)
            if peer_session and peer_session.udp_addr and self.transport:
                self.transport.sendto(data, peer_session.udp_addr)


class VoiceRelayServer:
    def __init__(self, host: str, signal_port: int, udp_port: int) -> None:
        self.host = host
        self.signal_port = signal_port
        self.udp_port = udp_port
        self.state = RelayState()

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_sock.bind((self.host, self.udp_port))
        udp_sock.setblocking(False)

        await loop.create_datagram_endpoint(
            lambda: VoiceRelayDatagramProtocol(self.state),
            sock=udp_sock,
        )

        async with websockets.serve(self.handle_ws, self.host, self.signal_port):
            banner("Terminal Voice Chat Relay Server")
            info(f"Control channel: {self.host}:{self.signal_port}")
            info(f"UDP relay: {self.host}:{self.udp_port}")
            for hint in _join_hints(self.host, self.signal_port):
                info(hint)
            info("Waiting for clients... Press Ctrl+C to stop.")
            await self._heartbeat_monitor()

    async def _heartbeat_monitor(self) -> None:
        while True:
            await asyncio.sleep(2)
            now = time.time()
            stale_websockets: list[Any] = []

            async with self.state.lock:
                for ws, session in self.state.clients_by_ws.items():
                    if now - session.last_heartbeat > 12.0:
                        stale_websockets.append(ws)

            for ws in stale_websockets:
                await ws.close(code=4000, reason="heartbeat timeout")

    async def handle_ws(self, websocket: Any) -> None:
        session: ClientSession | None = None
        try:
            raw = await websocket.recv()
            if not isinstance(raw, str):
                await websocket.close(code=4001, reason="invalid handshake")
                return

            message = decode_message(raw)
            if message.get("type") != "auth":
                await websocket.close(code=4001, reason="auth required")
                return

            username = str(message.get("username", "")).strip()
            room = str(message.get("room", "")).strip()
            token = message.get("token")
            token = str(token).strip() if token is not None else None

            auth_ok, reason = validate_credentials(username, room, token)
            if not auth_ok:
                await websocket.send(encode_message({"type": "auth_error", "reason": reason}))
                await websocket.close(code=4003, reason=reason)
                return

            session_id = secrets.randbits(31)
            session = ClientSession(
                websocket=websocket,
                username=username,
                room=room,
                token=token,
                session_id=session_id,
                last_heartbeat=time.time(),
            )

            async with self.state.lock:
                peers = self.state.rooms.setdefault(room, set())
                if len(peers) >= 2:
                    await websocket.send(encode_message({"type": "auth_error", "reason": "room full"}))
                    await websocket.close(code=4004, reason="room full")
                    return

                self.state.clients_by_ws[websocket] = session
                self.state.clients_by_session_id[session_id] = session
                peers.add(websocket)

            ok(f"User '{username}' joined room '{room}' (session {session_id})")

            await websocket.send(
                encode_message(
                    {
                        "type": "auth_ok",
                        "session_id": session_id,
                        "udp_port": self.udp_port,
                    }
                )
            )
            await self._notify_room(session.room, {"type": "peer_update", "count": self._room_count(session.room)})

            async for incoming in websocket:
                if not isinstance(incoming, str):
                    continue
                payload = decode_message(incoming)
                message_type = payload.get("type")
                if message_type == "heartbeat":
                    session.last_heartbeat = time.time()
                elif message_type == "text":
                    body = str(payload.get("body", "")).strip()
                    if body:
                        await self._broadcast_text(session, body)
        except websockets.ConnectionClosed:
            pass
        finally:
            if session is not None:
                await self._remove_session(session)

    async def _broadcast_text(self, sender: ClientSession, body: str) -> None:
        room_peers = self.state.rooms.get(sender.room, set())
        message = encode_message({"type": "text", "from": sender.username, "body": body})
        for peer_ws in room_peers:
            if peer_ws != sender.websocket:
                await peer_ws.send(message)

    async def _notify_room(self, room: str, payload: dict[str, Any]) -> None:
        peers = self.state.rooms.get(room, set())
        data = encode_message(payload)
        for ws in list(peers):
            try:
                await ws.send(data)
            except websockets.ConnectionClosed:
                pass

    async def _remove_session(self, session: ClientSession) -> None:
        async with self.state.lock:
            self.state.clients_by_ws.pop(session.websocket, None)
            self.state.clients_by_session_id.pop(session.session_id, None)
            peers = self.state.rooms.get(session.room)
            if peers and session.websocket in peers:
                peers.remove(session.websocket)
                if not peers:
                    self.state.rooms.pop(session.room, None)
            info(f"User '{session.username}' left room '{session.room}'")

        await self._notify_room(session.room, {"type": "peer_update", "count": self._room_count(session.room)})

    def _room_count(self, room: str) -> int:
        return len(self.state.rooms.get(room, set()))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Voice relay server",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--host", default="")
    parser.add_argument(
        "--mode",
        choices=["local", "public"],
        default="",
        help="Server binding mode. local=127.0.0.1, public=0.0.0.0",
    )
    parser.add_argument(
        "--signal-port",
        "--ws-port",
        dest="signal_port",
        type=int,
        default=None,
        help="Control/signaling port (ws alias kept for compatibility)",
    )
    parser.add_argument("--udp-port", type=int, default=None)
    parser.add_argument("--token", default=None, help="Optional room token (also supports VOICECHAT_ROOM_TOKEN env var)")
    return parser.parse_args()


def _prompt_text(label: str, default: str = "") -> str:
    return prompt_text(label, default)


def _prompt_int(label: str, default: int) -> int:
    while True:
        value = _prompt_text(label, str(default))
        try:
            number = int(value)
            if number <= 0:
                raise ValueError
            return number
        except ValueError:
            warn("Please enter a valid positive number.")


def _prompt_server_mode() -> str:
    section("Server mode")
    print("  1) Local only (127.0.0.1)")
    print("  2) LAN/VPS public (0.0.0.0)")
    while True:
        choice = _prompt_text("Choose mode", "2")
        if choice in {"1", "2"}:
            return choice
        warn("Please choose 1 or 2.")


def _join_hints(host: str, signal_port: int) -> list[str]:
    hints: list[str] = []
    if host == "127.0.0.1":
        hints.append(f"Clients on this PC can join with: --host 127.0.0.1 --signal-port {signal_port}")
        return hints

    if host == "0.0.0.0":
        hints.append("Public mode enabled: remote users can join using your VPS public IP.")
        hints.append("Open firewall ports: TCP signal-port and UDP udp-port.")
        try:
            lan_ip = socket.gethostbyname(socket.gethostname())
            if lan_ip and lan_ip != "127.0.0.1":
                hints.append(f"LAN example: --host {lan_ip} --signal-port {signal_port}")
        except OSError:
            pass
    return hints


def ensure_server_config(args: argparse.Namespace) -> None:
    if args.mode:
        args.host = "127.0.0.1" if args.mode == "local" else "0.0.0.0"

    if not args.host:
        if not sys.stdin.isatty():
            args.host = "0.0.0.0"
            info("No interactive terminal detected; defaulting to public mode (0.0.0.0).")
        else:
            mode = _prompt_server_mode()
            args.host = "127.0.0.1" if mode == "1" else "0.0.0.0"

    if args.signal_port is None:
        args.signal_port = _prompt_int("Control port", 8765)

    if args.udp_port is None:
        args.udp_port = _prompt_int("UDP port", 9999)

    if args.token is None:
        default = os.getenv("VOICECHAT_ROOM_TOKEN", "")
        if sys.stdin.isatty():
            token = _prompt_text("Room token (optional)", default)
            args.token = token or None
        else:
            args.token = default or None

    if args.token:
        os.environ["VOICECHAT_ROOM_TOKEN"] = args.token

    section("Configuration summary")
    info(f"Host: {args.host}")
    info(f"Mode: {'public' if args.host == '0.0.0.0' else 'local'}")
    info(f"Control Port: {args.signal_port}")
    info(f"UDP Port: {args.udp_port}")
    info(f"Token: {'set' if args.token else 'not set'}")


async def main() -> None:
    init_console()
    args = parse_args()
    ensure_server_config(args)
    server = VoiceRelayServer(args.host, args.signal_port, args.udp_port)
    try:
        await server.run()
    except OSError as exc:
        message = str(exc)
        if "10048" in message or "Address already in use" in message:
            error("Port already in use. Stop other server process or change ports.")
            info(
                f"Try: python3 server.py --mode public --signal-port {args.signal_port + 1} --udp-port {args.udp_port + 1}"
            )
            return
        error(f"Network startup failed: {exc}")
        return


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        warn("Server stopped by user.")
