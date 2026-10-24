import sys
import time
from typing import Optional

from .link import LinkSnapshot
from .mapping import PadState


class ConsoleMonitor:
    def __init__(self, show_channels: bool = False, interval_s: float = 0.1) -> None:
        self.show_channels = show_channels
        self.interval_s = interval_s
        self._last_print = 0.0
        self._width = 0

    def update(
        self,
        snapshot: LinkSnapshot,
        state: PadState,
        failsafe_timeout_ms: float,
        force: bool = False,
    ) -> None:
        now = time.monotonic()
        if not force and now - self._last_print < self.interval_s:
            return
        self._last_print = now

        parts = [
            f"{(snapshot.port or '--'):>5}",
            "OK" if snapshot.connected else "--",
            f"{snapshot.rate_hz:5.0f}Hz",
            f"frame={snapshot.frames}",
            f"crc={snapshot.crc_errors}",
        ]

        age_ms = snapshot.age_ms(now)
        if age_ms is not None:
            parts.append(f"age={age_ms:4.0f}ms")
            if age_ms > failsafe_timeout_ms:
                parts.append("FAILSAFE")
        else:
            parts.append("age=--")

        parts.extend(
            [
                f"R{state.right_x:+.2f}",
                f"P{state.right_y:+.2f}",
                f"T{state.left_y:+.2f}",
                f"Y{state.left_x:+.2f}",
            ]
        )

        if state.buttons:
            parts.append("btn=" + ",".join(sorted(state.buttons)))

        if self.show_channels and snapshot.channels:
            raw = " ".join(f"{value:4d}" for value in snapshot.channels)
            parts.append("| " + raw)

        line = " ".join(parts)
        padding = " " * max(0, self._width - len(line))
        sys.stdout.write("\r" + line + padding)
        sys.stdout.flush()
        self._width = len(line)

    def finish(self) -> None:
        if self._width:
            sys.stdout.write("\n")
            sys.stdout.flush()
            self._width = 0


def print_ports(lines: list, stream=sys.stdout) -> None:
    if not lines:
        stream.write("No serial ports found.\n")
        return
    for line in lines:
        stream.write(line + "\n")
