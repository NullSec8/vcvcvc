from __future__ import annotations

import os


def validate_credentials(username: str, room: str, token: str | None) -> tuple[bool, str]:
    if not username or not room:
        return False, "username and room are required"

    expected = os.getenv("VOICECHAT_ROOM_TOKEN")
    if expected and token != expected:
        return False, "invalid token"

    return True, "ok"
