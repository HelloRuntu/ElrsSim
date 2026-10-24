import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

from .config import Config
from .link import LinkSnapshot, SerialLink
from .mapping import ChannelMapper, PadState
from .pad import VirtualPad


@dataclass
class EngineStatus:
    running: bool
    pad_enabled: bool
    snapshot: LinkSnapshot
    state: PadState
    failsafe: bool
    error: Optional[str]


class Engine:
    def __init__(self, config: Config, pad: Optional[VirtualPad] = None) -> None:
        self.config = config
        self.mapper = ChannelMapper(config)
        self.link = SerialLink(config)
        self.pad = pad
        self._pad_enabled = pad is not None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._status = EngineStatus(
            running=False,
            pad_enabled=self._pad_enabled,
            snapshot=self.link.snapshot(),
            state=self.mapper.neutral_state(),
            failsafe=True,
            error=None,
        )

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, pad_enabled: bool = True) -> None:
        if self.is_running():
            return
        if pad_enabled:
            if self.pad is None:
                self.pad = VirtualPad()
            self.pad.reset()
        self._pad_enabled = pad_enabled and self.pad is not None
        self._stop_event.clear()
        self.link = SerialLink(self.config)
        self.link.start()
        self._thread = threading.Thread(target=self._loop, name="elrs-engine", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        period = 1.0 / max(1.0, self.config.update_hz)
        next_tick = time.monotonic()
        while not self._stop_event.is_set():
            snapshot = self.link.snapshot()
            now = time.monotonic()
            age_ms = snapshot.age_ms(now)
            failsafe = not (
                snapshot.channels
                and age_ms is not None
                and age_ms <= self.config.failsafe_timeout_ms
            )
            state = (
                self.mapper.neutral_state()
                if failsafe
                else self.mapper.map(snapshot.channels)
            )

            error = None
            if self._pad_enabled and self.pad is not None:
                try:
                    self.pad.apply(self.mapper.pad_state(state))
                except Exception as exc:
                    error = str(exc)

            with self._lock:
                self._status = EngineStatus(
                    running=True,
                    pad_enabled=self._pad_enabled,
                    snapshot=snapshot,
                    state=state,
                    failsafe=failsafe,
                    error=error,
                )

            next_tick += period
            delay = next_tick - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                next_tick = time.monotonic()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=1.0)
            self._thread = None
        self.link.stop()
        self.link.join(timeout=1.0)
        if self.pad is not None:
            try:
                self.pad.reset()
            except Exception:
                pass
        with self._lock:
            self._status = EngineStatus(
                running=False,
                pad_enabled=self._pad_enabled,
                snapshot=self.link.snapshot(),
                state=self.mapper.neutral_state(),
                failsafe=True,
                error=None,
            )

    def reload(self, config: Config) -> None:
        was_running = self.is_running()
        pad_enabled = self._pad_enabled
        self.stop()
        self.config = config
        self.mapper = ChannelMapper(config)
        self.link = SerialLink(config)
        with self._lock:
            self._status = EngineStatus(
                running=False,
                pad_enabled=pad_enabled,
                snapshot=self.link.snapshot(),
                state=self.mapper.neutral_state(),
                failsafe=True,
                error=None,
            )
        if was_running:
            self.start(pad_enabled)

    def bind(self) -> bool:
        if not self.is_running():
            return False
        ok = self.link.bind()
        logging.info("bind command sent=%s", ok)
        return ok

    def status(self) -> EngineStatus:
        with self._lock:
            return self._status
