from __future__ import annotations

import asyncio
from typing import Callable

import sounddevice as sd

from .config import AudioConfig


class AudioCapture:
    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        config: AudioConfig,
        output_queue: asyncio.Queue[bytes],
    ) -> None:
        self._loop = loop
        self._config = config
        self._output_queue = output_queue
        self._stream: sd.RawInputStream | None = None

    def start(self) -> None:
        frames_per_buffer = self._config.frame_samples

        def callback(indata: bytes, frames: int, time_info: dict, status: sd.CallbackFlags) -> None:
            if status:
                return
            if frames <= 0:
                return

            packet = bytes(indata)

            def enqueue() -> None:
                if self._output_queue.full():
                    try:
                        self._output_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                self._output_queue.put_nowait(packet)

            self._loop.call_soon_threadsafe(enqueue)

        self._stream = sd.RawInputStream(
            samplerate=self._config.sample_rate,
            channels=self._config.channels,
            dtype=self._config.dtype,
            blocksize=frames_per_buffer,
            callback=callback,
        )
        self._stream.start()

    def stop(self) -> None:
        if self._stream is None:
            return
        self._stream.stop()
        self._stream.close()
        self._stream = None


class AudioPlayback:
    def __init__(
        self,
        config: AudioConfig,
        input_queue: asyncio.Queue[bytes],
    ) -> None:
        self._config = config
        self._input_queue = input_queue
        self._stream: sd.RawOutputStream | None = None

    def start(self) -> None:
        frames_per_buffer = self._config.frame_samples
        silence = bytes(frames_per_buffer * 2)

        def callback(outdata: bytearray, frames: int, time_info: dict, status: sd.CallbackFlags) -> None:
            if status:
                outdata[:] = silence
                return
            try:
                payload = self._input_queue.get_nowait()
            except asyncio.QueueEmpty:
                payload = silence

            expected_bytes = frames * 2
            if len(payload) < expected_bytes:
                payload = payload + bytes(expected_bytes - len(payload))
            elif len(payload) > expected_bytes:
                payload = payload[:expected_bytes]

            outdata[:] = payload

        self._stream = sd.RawOutputStream(
            samplerate=self._config.sample_rate,
            channels=self._config.channels,
            dtype=self._config.dtype,
            blocksize=frames_per_buffer,
            callback=callback,
        )
        self._stream.start()

    def stop(self) -> None:
        if self._stream is None:
            return
        self._stream.stop()
        self._stream.close()
        self._stream = None
