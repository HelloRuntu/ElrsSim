import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from .crsf import CHANNEL_COUNT, CHANNEL_MAX, CHANNEL_MID, CHANNEL_MIN


@dataclass
class SerialConfig:
    port: str = "auto"
    baud: int = 420000
    reconnect_delay_s: float = 1.0


@dataclass
class AxisConfig:
    channel: int = 1
    invert: bool = False
    centered: bool = True
    deadband: float = 0.03
    expo: float = 0.0
    map_0_1: bool = False


@dataclass
class Calibration:
    min: int = CHANNEL_MIN
    center: int = CHANNEL_MID
    max: int = CHANNEL_MAX


@dataclass
class ButtonConfig:
    channel: int = 5
    position: str = "high"
    button: str = "A"


def default_axes() -> Dict[str, AxisConfig]:
    return {
        "roll": AxisConfig(channel=1),
        "pitch": AxisConfig(channel=2),
        "throttle": AxisConfig(channel=3, centered=False, deadband=0.0),
        "yaw": AxisConfig(channel=4),
    }


def default_calibration() -> List[Calibration]:
    return [Calibration() for _ in range(CHANNEL_COUNT)]


def default_buttons() -> List[ButtonConfig]:
    return [
        ButtonConfig(channel=5, position="high", button="A"),
        ButtonConfig(channel=6, position="low", button="B"),
        ButtonConfig(channel=6, position="high", button="X"),
    ]


@dataclass
class Config:
    serial: SerialConfig = field(default_factory=SerialConfig)
    update_hz: float = 500.0
    failsafe_timeout_ms: float = 250.0
    failsafe_throttle: float = -1.0
    button_low: float = 0.25
    button_high: float = 0.75
    axes: Dict[str, AxisConfig] = field(default_factory=default_axes)
    calibration: List[Calibration] = field(default_factory=default_calibration)
    buttons: List[ButtonConfig] = field(default_factory=default_buttons)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Config":
        cfg = cls()

        serial_data = data.get("serial", {})
        cfg.serial = SerialConfig(
            port=str(serial_data.get("port", cfg.serial.port)),
            baud=int(serial_data.get("baud", cfg.serial.baud)),
            reconnect_delay_s=float(
                serial_data.get("reconnect_delay_s", cfg.serial.reconnect_delay_s)
            ),
        )
        cfg.update_hz = float(data.get("update_hz", cfg.update_hz))
        cfg.failsafe_timeout_ms = float(
            data.get("failsafe_timeout_ms", cfg.failsafe_timeout_ms)
        )
        cfg.failsafe_throttle = float(
            data.get("failsafe_throttle", cfg.failsafe_throttle)
        )
        cfg.button_low = float(data.get("button_low", cfg.button_low))
        cfg.button_high = float(data.get("button_high", cfg.button_high))

        map_default = bool(data.get("map_axes_0_1", False))
        axes_data = data.get("axes", {})
        axes = default_axes()
        for name, axis in axes.items():
            entry = axes_data.get(name, {})
            axis.channel = int(entry.get("channel", axis.channel))
            axis.invert = bool(entry.get("invert", axis.invert))
            axis.centered = bool(entry.get("centered", axis.centered))
            axis.deadband = float(entry.get("deadband", axis.deadband))
            axis.expo = float(entry.get("expo", axis.expo))
            axis.map_0_1 = bool(entry.get("map_0_1", map_default))
        cfg.axes = axes

        calibration_data = data.get("calibration", [])
        calibration = default_calibration()
        for index in range(min(len(calibration_data), CHANNEL_COUNT)):
            entry = calibration_data[index]
            calibration[index] = Calibration(
                min=int(entry.get("min", CHANNEL_MIN)),
                center=int(entry.get("center", CHANNEL_MID)),
                max=int(entry.get("max", CHANNEL_MAX)),
            )
        cfg.calibration = calibration

        cfg.buttons = [
            ButtonConfig(
                channel=int(entry.get("channel", 5)),
                position=str(entry.get("position", "high")),
                button=str(entry.get("button", "A")),
            )
            for entry in data.get("buttons", [])
        ]

        cfg.validate()
        return cfg

    def validate(self) -> None:
        for name, axis in self.axes.items():
            if not 1 <= axis.channel <= CHANNEL_COUNT:
                raise ValueError(f"axis {name}: channel out of range: {axis.channel}")
            if not 0.0 <= axis.deadband < 1.0:
                raise ValueError(f"axis {name}: deadband out of range: {axis.deadband}")
            if not 0.0 <= axis.expo <= 1.0:
                raise ValueError(f"axis {name}: expo out of range: {axis.expo}")
        for index, calibration in enumerate(self.calibration):
            if not calibration.min < calibration.center < calibration.max:
                raise ValueError(
                    f"channel {index + 1}: calibration must satisfy min < center < max"
                )
        if self.button_low >= self.button_high:
            raise ValueError("button_low must be smaller than button_high")
        for entry in self.buttons:
            if not 1 <= entry.channel <= CHANNEL_COUNT:
                raise ValueError(f"button channel out of range: {entry.channel}")
            if entry.position not in ("low", "mid", "high"):
                raise ValueError(f"button position invalid: {entry.position}")

    @classmethod
    def load(cls, path: str) -> "Config":
        config_path = Path(path)
        if not config_path.exists():
            cfg = cls()
            cfg.save(config_path)
            return cfg
        with config_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return cls.from_dict(data)

    def save(self, path: str) -> None:
        config_path = Path(path)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with config_path.open("w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, indent=2, ensure_ascii=False)
            handle.write("\n")
