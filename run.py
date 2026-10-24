import argparse
import ctypes
import sys
import time

from elrs_sim.config import Calibration, Config
from elrs_sim.engine import Engine
from elrs_sim.link import SerialLink, describe_ports, find_ch340_port
from elrs_sim.mapping import ChannelMapper
from elrs_sim.monitor import ConsoleMonitor
from elrs_sim.pad import VirtualPad


class TimerResolution:
    def __init__(self) -> None:
        self._winmm = None

    def __enter__(self) -> "TimerResolution":
        if sys.platform == "win32":
            try:
                self._winmm = ctypes.WinDLL("winmm")
                self._winmm.timeBeginPeriod(1)
            except Exception:
                self._winmm = None
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self._winmm is not None:
            self._winmm.timeEndPeriod(1)


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Use an ExpressLRS receiver as a virtual Xbox 360 gamepad."
    )
    parser.add_argument("--port", help="serial port such as COM6 (default: auto-detect CH340)")
    parser.add_argument("--baud", type=int, help="CRSF baud rate (default: 420000)")
    parser.add_argument("--config", default="config.json", help="config file path")
    parser.add_argument("--list", action="store_true", help="list serial ports and exit")
    parser.add_argument("--monitor", action="store_true", help="print channels without a virtual gamepad")
    parser.add_argument("--no-pad", action="store_true", help="alias for --monitor")
    parser.add_argument(
        "--calibrate",
        type=float,
        nargs="?",
        const=10.0,
        metavar="SECONDS",
        help="capture stick extremes for SECONDS (default 10) and save calibration",
    )
    return parser.parse_args(argv)


def list_ports() -> int:
    ports = describe_ports()
    if not ports:
        print("No serial ports found.")
        return 1
    for line in ports:
        print(line)
    detected = find_ch340_port()
    if detected:
        print(f"\nDetected CH340: {detected}")
    return 0


def capture_calibration(link: SerialLink, config: Config, seconds: float, path: str) -> None:
    minimums = [None] * 16
    maximums = [None] * 16
    mapper = ChannelMapper(config)
    monitor = ConsoleMonitor(show_channels=True)

    print(f"Calibrating for {seconds:.0f}s.")
    print("Move roll, pitch and yaw to both limits; leave throttle at minimum.")
    start = time.monotonic()
    try:
        while time.monotonic() - start < seconds:
            snapshot = link.snapshot()
            channels = snapshot.channels
            if channels:
                for index, value in enumerate(channels):
                    if minimums[index] is None or value < minimums[index]:
                        minimums[index] = value
                    if maximums[index] is None or value > maximums[index]:
                        maximums[index] = value
                monitor.update(
                    snapshot,
                    mapper.map(channels),
                    config.failsafe_timeout_ms,
                )
            time.sleep(0.01)
    except KeyboardInterrupt:
        print("\nCalibration interrupted, keeping collected samples.")
    finally:
        monitor.finish()

    changed = 0
    for index in range(16):
        if minimums[index] is None or maximums[index] is None:
            continue
        span = maximums[index] - minimums[index]
        if span < 300:
            continue
        low = minimums[index]
        high = maximums[index]
        config.calibration[index] = Calibration(
            min=low,
            center=(low + high) // 2,
            max=high,
        )
        changed += 1
        print(f"CH{index + 1:02d}: {low} .. {high} (center {(low + high) // 2})")

    config.save(path)
    print(f"Saved {changed} calibrated channels to {path}")
    if changed == 0:
        print("No channel moved more than expected; check wiring, baud rate or binding.")


def run(config: Config, monitor_only: bool) -> int:
    if not monitor_only:
        valid_buttons = set(VirtualPad.available_buttons())
        for entry in config.buttons:
            if entry.button not in valid_buttons:
                print(
                    f"warning: unknown button '{entry.button}' in config, ignored",
                    file=sys.stderr,
                )

    engine = Engine(config)
    try:
        engine.start(pad_enabled=not monitor_only)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    monitor = ConsoleMonitor(show_channels=monitor_only)
    try:
        while True:
            status = engine.status()
            monitor.update(status.snapshot, status.state, config.failsafe_timeout_ms)
            time.sleep(0.01)
    except KeyboardInterrupt:
        pass
    finally:
        monitor.finish()
        engine.stop()

    return 0


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.list:
        return list_ports()

    try:
        config = Config.load(args.config)
    except Exception as exc:
        print(f"error: invalid config {args.config}: {exc}", file=sys.stderr)
        return 2

    if args.port:
        config.serial.port = args.port
    if args.baud:
        config.serial.baud = args.baud

    monitor_only = args.monitor or args.no_pad

    with TimerResolution():
        link = SerialLink(config)
        if args.calibrate:
            link.start()
            try:
                capture_calibration(link, config, args.calibrate, args.config)
            finally:
                link.stop()
                link.join(timeout=1.0)
            return 0

        port = config.serial.port
        if port == "auto":
            port = find_ch340_port() or "auto"
        mode = "monitor" if monitor_only else "virtual gamepad"
        print(f"port={port} baud={config.serial.baud} mode={mode}")
        print("Press Ctrl+C to stop.")
        return run(config, monitor_only)


if __name__ == "__main__":
    sys.exit(main())
