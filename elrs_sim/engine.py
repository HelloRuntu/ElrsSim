import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

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


def pad_changed(previous: Optional[PadState], current: PadState) -> bool:
    if previous is None or previous.buttons != current.buttons:
        return True
    return (
        abs(previous.left_x - current.left_x) >= 0.001
        or abs(previous.left_y - current.left_y) >= 0.001
        or abs(previous.right_x - current.right_x) >= 0.001
        or abs(previous.right_y - current.right_y) >= 0.001
    )


class Engine:
    def __init__(
        self,
        config: Config,
        pad: Optional[VirtualPad] = None,
        link_factory: Optional[Callable[[], SerialLink]] = None,
    ) -> None:
        self.config = config
        self.mapper = ChannelMapper(config)
        self._link_factory = link_factory or (lambda: SerialLink(self.config))
        self.link = self._link_factory()
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
        self.link = self._link_factory()
        self.link.start()
        self._thread = threading.Thread(target=self._loop, name="elrs-engine", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        min_interval = 1.0 / max(1.0, self.config.update_hz)
        last_pad_time = 0.0
        last_pad_state: Optional[PadState] = None
        while not self._stop_event.is_set():
            snapshot = self.link.snapshot()
            now = time.monotonic()
            age_ms = snapshot.age_ms(now)
            fresh = bool(
                snapshot.channels
                and age_ms is not None
                and age_ms <= self.config.failsafe_timeout_ms
            )
            state = (
                self.mapper.map(snapshot.channels)
                if fresh
                else self.mapper.neutral_state()
            )

            error = None
            if self._pad_enabled and self.pad is not None:
                pad_state = self.mapper.pad_state(state)
                due = (
                    last_pad_state is None
                    or not fresh
                    or now - last_pad_time >= min_interval
                )
                if due and pad_changed(last_pad_state, pad_state):
                    try:
                        self.pad.apply(pad_state)
                    except Exception as exc:
                        error = str(exc)
                    else:
                        last_pad_time = now
                        last_pad_state = pad_state

            with self._lock:
                self._status = EngineStatus(
                    running=True,
                    pad_enabled=self._pad_enabled,
                    snapshot=snapshot,
                    state=state,
                    failsafe=not fresh,
                    error=error,
                )

            if fresh:
                remaining = max(
                    0.0,
                    (
                        self.config.failsafe_timeout_ms - (age_ms or 0.0)
                    )
                    / 1000.0,
                )
                self.link.wait_for_frame(remaining)
            else:
                self.link.wait_for_frame(0.5)

    def stop(self) -> None:
        self._stop_event.set()
        link = self.link
        link.stop()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=1.0)
            self._thread = None
        if link.is_alive():
            link.join(timeout=1.0)
        if self.pad is not None:
            try:
                self.pad.reset()
            except Exception:
                pass
        with self._lock:
            self._status = EngineStatus(
                running=False,
                pad_enabled=self._pad_enabled,
                snapshot=link.snapshot(),
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
        self.link = self._link_factory()
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
