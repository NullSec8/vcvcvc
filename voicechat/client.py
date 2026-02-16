from __future__ import annotations

import argparse
import asyncio
import contextlib
import getpass
import socket
import sys
from pathlib import Path

import websockets

if __package__ in (None, ""):
    sys.path.append(str(Path(__file__).resolve().parents[1]))
    from voicechat.audio import AudioCapture, AudioPlayback
    from voicechat.config import AudioConfig, NetworkConfig
    from voicechat.protocol import (
        AudioPacket,
        decode_message,
        encode_message,
        now_ms,
        pack_audio_packet,
        unpack_audio_packet,
    )
    from voicechat.ui import banner, chat, info, init_console, ok, prompt_text, section, warn
else:
    from .audio import AudioCapture, AudioPlayback
    from .config import AudioConfig, NetworkConfig
    from .protocol import (
        AudioPacket,
        decode_message,
        encode_message,
        now_ms,
        pack_audio_packet,
        unpack_audio_packet,
    )
    from .ui import banner, chat, info, init_console, ok, prompt_text, section, warn


class ClientDatagramProtocol(asyncio.DatagramProtocol):
    def __init__(self, client: "VoiceClient") -> None:
        self.client = client
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]
        self.client.udp_transport = self.transport

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        packet = unpack_audio_packet(data)
        if packet is None:
            return
        if packet.session_id == self.client.session_id:
            return
        self.client.enqueue_playback(packet.payload)


class VoiceClient:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.audio_config = AudioConfig(sample_rate=args.sample_rate, frame_ms=args.frame_ms)
        self.network_config = NetworkConfig()
        self.capture_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=self.audio_config.input_queue_maxsize)
        self.playback_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=self.audio_config.playback_queue_maxsize)
        self.capture: AudioCapture | None = None
        self.playback: AudioPlayback | None = None

        self.udp_transport: asyncio.DatagramTransport | None = None
        self.ws: websockets.WebSocketClientProtocol | None = None
        self.session_id: int = 0
        self.sequence: int = 0
        self.udp_target: tuple[str, int] = (args.server, args.udp_port)
        self.stop_event = asyncio.Event()

    @staticmethod
    def print_help_commands() -> None:
        section("Commands")
        print("  /msg <text>  Send text message to peer")
        print("  /status      Show current connection/session status")
        print("  /help        Show this help")
        print("  /quit        Exit client")

    async def run(self) -> None:
        banner("Terminal Voice Chat Client")
        info(f"Server: {self.args.server}:{self.args.signal_port} (UDP {self.args.udp_port})")
        info(f"User: {self.args.username} | Room: {self.args.room}")
        self.print_help_commands()

        backoff = self.network_config.reconnect_backoff_min_s
        while not self.stop_event.is_set():
            try:
                await self._run_connected()
                backoff = self.network_config.reconnect_backoff_min_s
            except (OSError, websockets.WebSocketException) as exc:
                warn(f"Disconnected: {exc}")
                await self._cleanup_connected_state()
                info(f"Reconnecting in {backoff:.1f}s... (Ctrl+C to stop)")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self.network_config.reconnect_backoff_max_s)
            except asyncio.CancelledError:
                break

    async def _run_connected(self) -> None:
        ws_url = f"ws://{self.args.server}:{self.args.signal_port}"
        info(f"Connecting to {self.args.server}:{self.args.signal_port}")
        async with websockets.connect(ws_url, ping_interval=None) as ws:
            self.ws = ws
            await self._authenticate()
            await self._setup_udp()
            await self._start_audio()
            ok("Connected. You can start speaking now.")

            tasks = [
                asyncio.create_task(self._heartbeat_loop(), name="heartbeat"),
                asyncio.create_task(self._ws_reader_loop(), name="ws_reader"),
                asyncio.create_task(self._audio_sender_loop(), name="audio_sender"),
                asyncio.create_task(self._text_input_loop(), name="text_input"),
            ]

            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
            for task in pending:
                task.cancel()
            for task in pending:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            for task in done:
                exc = task.exception()
                if exc:
                    raise exc

    async def _authenticate(self) -> None:
        assert self.ws is not None
        await self.ws.send(
            encode_message(
                {
                    "type": "auth",
                    "username": self.args.username,
                    "room": self.args.room,
                    "token": self.args.token,
                }
            )
        )

        raw = await self.ws.recv()
        if not isinstance(raw, str):
            raise RuntimeError("invalid auth response")
        response = decode_message(raw)
        if response.get("type") != "auth_ok":
            reason = response.get("reason", "authentication failed")
            raise RuntimeError(str(reason))

        self.session_id = int(response["session_id"])
        ok(f"Authenticated (session {self.session_id})")

    async def _setup_udp(self) -> None:
        loop = asyncio.get_running_loop()
        local_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        local_sock.bind(("0.0.0.0", 0))
        local_sock.setblocking(False)

        await loop.create_datagram_endpoint(
            lambda: ClientDatagramProtocol(self),
            sock=local_sock,
        )

        info(f"UDP ready | local={local_sock.getsockname()} -> relay={self.udp_target}")

    async def _start_audio(self) -> None:
        loop = asyncio.get_running_loop()
        self.capture = AudioCapture(loop, self.audio_config, self.capture_queue)
        self.playback = AudioPlayback(self.audio_config, self.playback_queue)
        self.capture.start()
        self.playback.start()
        ok("Audio started (mic + speaker)")

    async def _cleanup_connected_state(self) -> None:
        if self.capture is not None:
            self.capture.stop()
            self.capture = None
        if self.playback is not None:
            self.playback.stop()
            self.playback = None
        if self.udp_transport is not None:
            self.udp_transport.close()
            self.udp_transport = None
        if self.ws is not None:
            with contextlib.suppress(Exception):
                await self.ws.close()
            self.ws = None

        while not self.capture_queue.empty():
            with contextlib.suppress(asyncio.QueueEmpty):
                self.capture_queue.get_nowait()
        while not self.playback_queue.empty():
            with contextlib.suppress(asyncio.QueueEmpty):
                self.playback_queue.get_nowait()

    async def _heartbeat_loop(self) -> None:
        assert self.ws is not None
        while True:
            await asyncio.sleep(self.network_config.heartbeat_interval_s)
            await self.ws.send(encode_message({"type": "heartbeat"}))

    async def _ws_reader_loop(self) -> None:
        assert self.ws is not None
        async for raw in self.ws:
            if not isinstance(raw, str):
                continue
            message = decode_message(raw)
            message_type = message.get("type")
            if message_type == "text":
                sender = message.get("from", "peer")
                body = message.get("body", "")
                chat(str(sender), str(body))
            elif message_type == "peer_update":
                count = message.get("count")
                info(f"Peer count in room: {count}")

    async def _audio_sender_loop(self) -> None:
        while True:
            payload = await self.capture_queue.get()
            self.sequence = (self.sequence + 1) % (2**32)
            packet = AudioPacket(
                session_id=self.session_id,
                sequence=self.sequence,
                timestamp_ms=now_ms(),
                payload=payload,
            )
            encoded = pack_audio_packet(packet)
            if self.udp_transport is not None:
                self.udp_transport.sendto(encoded, self.udp_target)

    async def _text_input_loop(self) -> None:
        assert self.ws is not None
        info("Text chat ready. Type /help for commands.")
        while True:
            line = await asyncio.to_thread(input, "")
            line = line.strip()
            if not line:
                continue
            if line == "/quit":
                self.stop_event.set()
                raise asyncio.CancelledError()
            if line == "/help":
                self.print_help_commands()
                continue
            if line == "/status":
                info(
                    f"Status | server={self.args.server}:{self.args.signal_port}, "
                    f"udp={self.args.udp_port}, room={self.args.room}, "
                    f"session={self.session_id}"
                )
                continue
            if line.startswith("/msg "):
                body = line[5:].strip()
                if body:
                    await self.ws.send(encode_message({"type": "text", "body": body}))
                continue
            warn("Unknown command. Use /help.")

    def enqueue_playback(self, payload: bytes) -> None:
        if self.playback_queue.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                self.playback_queue.get_nowait()
        with contextlib.suppress(asyncio.QueueFull):
            self.playback_queue.put_nowait(payload)


def _prompt_text(label: str, default: str = "") -> str:
    return prompt_text(label, default)


def _prompt_int(label: str, default: int) -> int:
    while True:
        raw = _prompt_text(label, str(default))
        try:
            value = int(raw)
            if value <= 0:
                raise ValueError
            return value
        except ValueError:
            warn("Please enter a valid positive number.")


def _prompt_mode() -> str:
    section("Connection mode")
    print("  1) Local test (same PC / LAN)")
    print("  2) Online server (VPS/public IP)")
    while True:
        choice = _prompt_text("Choose mode", "1")
        if choice in {"1", "2"}:
            return choice
        warn("Please choose 1 or 2.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Terminal voice chat client",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--server", "--host", dest="server", default="", help="Server hostname or IP")
    parser.add_argument(
        "--signal-port",
        "--ws-port",
        dest="signal_port",
        type=int,
        default=None,
        help="Control/signaling port (ws alias kept for compatibility)",
    )
    parser.add_argument("--udp-port", type=int, default=None)
    parser.add_argument("--username", default="")
    parser.add_argument("--room", default="")
    parser.add_argument("--token", default=None)
    parser.add_argument("--sample-rate", type=int, default=None)
    parser.add_argument("--frame-ms", type=int, default=None)
    return parser.parse_args()


def ensure_required_identity(args: argparse.Namespace) -> None:
    if not args.server:
        mode = _prompt_mode()
        default_server = "127.0.0.1" if mode == "1" else ""
        args.server = _prompt_text("Server IP/hostname", default_server)

    if args.server == "0.0.0.0":
        warn("0.0.0.0 is not a valid remote server address for clients.")
        args.server = _prompt_text("Enter server IP/hostname", "127.0.0.1")

    if args.signal_port is None:
        args.signal_port = _prompt_int("Control port", 8765)

    if args.udp_port is None:
        args.udp_port = _prompt_int("UDP port", 9999)

    if args.sample_rate is None:
        args.sample_rate = _prompt_int("Sample rate", 16000)

    if args.frame_ms is None:
        args.frame_ms = _prompt_int("Frame size (ms)", 20)

    if not args.username:
        default_username = getpass.getuser() or "user"
        entered = _prompt_text("Username", default_username)
        args.username = entered or default_username

    if not args.room:
        default_room = "room1"
        entered = _prompt_text("Room", default_room)
        args.room = entered or default_room

    if args.token is None:
        entered_token = _prompt_text("Token (optional, press Enter to skip)", "")
        if entered_token:
            args.token = entered_token

    section("Configuration summary")
    info(f"Server: {args.server}")
    info(f"Control Port: {args.signal_port}")
    info(f"UDP Port: {args.udp_port}")
    info(f"Username: {args.username}")
    info(f"Room: {args.room}")
    info(f"Sample Rate: {args.sample_rate}")
    info(f"Frame MS: {args.frame_ms}")
    info(f"Token: {'set' if args.token else 'not set'}")


async def main() -> None:
    init_console()
    args = parse_args()
    ensure_required_identity(args)
    client = VoiceClient(args)
    try:
        await client.run()
    finally:
        await client._cleanup_connected_state()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        warn("Client stopped by user.")
