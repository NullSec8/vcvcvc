from __future__ import annotations

import json
import struct
import time
from dataclasses import dataclass
from typing import Any

HEADER_FORMAT = "!IIQH"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)


@dataclass(slots=True)
class AudioPacket:
    session_id: int
    sequence: int
    timestamp_ms: int
    payload: bytes


def now_ms() -> int:
    return int(time.time() * 1000)


def encode_message(message: dict[str, Any]) -> str:
    return json.dumps(message, separators=(",", ":"))


def decode_message(raw: str) -> dict[str, Any]:
    return json.loads(raw)


def pack_audio_packet(packet: AudioPacket) -> bytes:
    payload_len = len(packet.payload)
    header = struct.pack(
        HEADER_FORMAT,
        packet.session_id,
        packet.sequence,
        packet.timestamp_ms,
        payload_len,
    )
    return header + packet.payload


def unpack_audio_packet(data: bytes) -> AudioPacket | None:
    if len(data) < HEADER_SIZE:
        return None

    session_id, sequence, timestamp_ms, payload_len = struct.unpack(
        HEADER_FORMAT, data[:HEADER_SIZE]
    )
    payload = data[HEADER_SIZE : HEADER_SIZE + payload_len]
    if len(payload) != payload_len:
        return None

    return AudioPacket(
        session_id=session_id,
        sequence=sequence,
        timestamp_ms=timestamp_ms,
        payload=payload,
    )
