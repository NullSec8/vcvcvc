from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class AudioConfig:
    sample_rate: int = 16000
    channels: int = 1
    frame_ms: int = 20
    dtype: str = "int16"
    input_queue_maxsize: int = 64
    playback_queue_maxsize: int = 32

    @property
    def frame_samples(self) -> int:
        return int(self.sample_rate * (self.frame_ms / 1000.0))


@dataclass(slots=True)
class NetworkConfig:
    heartbeat_interval_s: float = 3.0
    heartbeat_timeout_s: float = 12.0
    reconnect_backoff_min_s: float = 1.0
    reconnect_backoff_max_s: float = 15.0
