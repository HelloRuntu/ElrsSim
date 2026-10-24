import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, List, Optional

import serial
from serial.tools import list_ports

from .config import Config
from .crsf import (
    CrsfParser,
    Frame,
    TYPE_RC_CHANNELS_PACKED,
    build_bind_frame,
    unpack_channels,
)

CH340_VID = 0x1A86
CH340_PID = 0x7523


def find_ch340_port() -> Optional[str]:
    for port in list_ports.comports():
        if port.vid == CH340_VID and port.pid == CH340_PID:
            return port.device
    for port in list_ports.comports():
        description = (port.description or "").upper()
        if "CH340" in description or "CH341" in description:
            return port.device
    return None


def describe_ports() -> List[str]:
    lines = []
    for port in list_ports.comports():
        vid_pid = ""
        if port.vid is not None and port.pid is not None:
            vid_pid = f" [{port.vid:04X}:{port.pid:04X}]"
        lines.append(f"{port.device}: {port.description}{vid_pid}")
    return lines


@dataclass
class LinkSnapshot:
    channels: Optional[List[int]]
    last_frame_time: Optional[float]
    frames: int
    crc_errors: int
    bytes_read: int
    rate_hz: float
    connected: bool
    port: Optional[str]
    error: Optional[str]

    def age_ms(self, now: Optional[float] = None) -> Optional[float]:
        if self.last_frame_time is None:
            return None
        current = time.monotonic() if now is None else now
        return (current - self.last_frame_time) * 1000.0


class SerialLink(threading.Thread):
    def __init__(self, config: Config) -> None:
        super().__init__(daemon=True, name="crsf-serial")
        self.config = config
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._channels: Optional[List[int]] = None
        self._last_frame_time: Optional[float] = None
        self._connected = False
        self._port: Optional[str] = None
        self._error: Optional[str] = None
        self._frames = 0
        self._bytes_read = 0
        self._rate_times: Deque[float] = deque(maxlen=256)
        self._parser = CrsfParser()
        self._handle = None
        self._write_lock = threading.Lock()

    def run(self) -> None:
        while not self._stop_event.is_set():
            port = self.config.serial.port
            if port == "auto":
                port = find_ch340_port()

            if not port:
                with self._lock:
                    self._error = "no CH340 serial port found"
                    self._connected = False
                self._stop_event.wait(1.0)
                continue

            try:
                parser = CrsfParser()
                with serial.Serial(
                    port=port,
                    baudrate=self.config.serial.baud,
                    timeout=0.05,
                ) as handle:
                    with self._lock:
                        self._port = port
                        self._connected = True
                        self._error = None
                        self._parser = parser
                        self._handle = handle
                    try:
                        while not self._stop_event.is_set():
                            data = handle.read(handle.in_waiting or 1)
                            if not data:
                                continue
                            with self._lock:
                                self._bytes_read += len(data)
                            for frame in parser.feed(data):
                                self._handle_frame(frame)
                    finally:
                        with self._lock:
                            if self._handle is handle:
                                self._handle = None
            except Exception as exc:
                with self._lock:
                    self._connected = False
                    self._error = str(exc)
                self._stop_event.wait(self.config.serial.reconnect_delay_s)

    def _handle_frame(self, frame: Frame) -> None:
        if frame.type != TYPE_RC_CHANNELS_PACKED:
            return
        try:
            channels = unpack_channels(frame.payload)
        except ValueError:
            return
        now = time.monotonic()
        with self._lock:
            self._channels = channels
            self._last_frame_time = now
            self._frames += 1
            self._rate_times.append(now)

    def send(self, data: bytes) -> bool:
        with self._lock:
            handle = self._handle
        if handle is None:
            return False
        try:
            with self._write_lock:
                if not handle.is_open:
                    return False
                handle.write(data)
            return True
        except Exception as exc:
            logging.warning("serial write failed: %s", exc)
            return False

    def bind(self) -> bool:
        return self.send(build_bind_frame())

    def snapshot(self) -> LinkSnapshot:
        now = time.monotonic()
        with self._lock:
            channels = list(self._channels) if self._channels is not None else None
            rate = 0.0
            if len(self._rate_times) >= 2:
                span = self._rate_times[-1] - self._rate_times[0]
                if span > 0:
                    rate = (len(self._rate_times) - 1) / span
            return LinkSnapshot(
                channels=channels,
                last_frame_time=self._last_frame_time,
                frames=self._frames,
                crc_errors=self._parser.crc_errors,
                bytes_read=self._bytes_read,
                rate_hz=rate,
                connected=self._connected,
                port=self._port,
                error=self._error,
            )

    def stop(self) -> None:
        self._stop_event.set()
