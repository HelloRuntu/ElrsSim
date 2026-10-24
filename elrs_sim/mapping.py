from dataclasses import dataclass, field
from typing import List, Set

from .config import Config
from .crsf import CHANNEL_COUNT


@dataclass
class PadState:
    left_x: float = 0.0
    left_y: float = 0.0
    right_x: float = 0.0
    right_y: float = 0.0
    buttons: Set[str] = field(default_factory=set)


def axis_0_1(value: float) -> float:
    return (max(-1.0, min(1.0, value)) + 1.0) * 0.5


class ChannelMapper:
    def __init__(self, config: Config) -> None:
        self.config = config

    def channel_fraction(self, index: int, raw: int) -> float:
        calibration = self.config.calibration[index]
        span = calibration.max - calibration.min
        if span <= 0:
            return 0.5
        return max(0.0, min(1.0, (raw - calibration.min) / span))

    def axis_value(self, axis_name: str, channels: List[int]) -> float:
        axis = self.config.axes[axis_name]
        index = axis.channel - 1
        if index < 0 or index >= len(channels):
            return 0.0

        calibration = self.config.calibration[index]
        raw = max(calibration.min, min(calibration.max, channels[index]))
        fraction = self.channel_fraction(index, raw)

        if axis.centered:
            center = (calibration.center - calibration.min) / (
                calibration.max - calibration.min
            )
            center = max(0.0, min(1.0, center))
            if fraction <= center:
                value = (fraction - center) / center if center > 0.0 else 0.0
            else:
                value = (fraction - center) / (1.0 - center) if center < 1.0 else 0.0
        else:
            value = fraction * 2.0 - 1.0

        value = max(-1.0, min(1.0, value))

        if axis.deadband > 0.0:
            if abs(value) <= axis.deadband:
                value = 0.0
            else:
                sign = 1.0 if value > 0.0 else -1.0
                value = sign * (abs(value) - axis.deadband) / (1.0 - axis.deadband)

        if axis.expo > 0.0:
            value = (1.0 - axis.expo) * value + axis.expo * value ** 3

        if axis.invert:
            value = -value

        return max(-1.0, min(1.0, value))

    def button_position(self, index: int, raw: int) -> str:
        fraction = self.channel_fraction(index, raw)
        if fraction <= self.config.button_low:
            return "low"
        if fraction >= self.config.button_high:
            return "high"
        return "mid"

    def map(self, channels: List[int]) -> PadState:
        if not channels or len(channels) < CHANNEL_COUNT:
            return self.neutral_state()

        state = PadState(
            left_x=self.axis_value("yaw", channels),
            left_y=self.axis_value("throttle", channels),
            right_x=self.axis_value("roll", channels),
            right_y=self.axis_value("pitch", channels),
        )
        for entry in self.config.buttons:
            index = entry.channel - 1
            if self.button_position(index, channels[index]) == entry.position:
                state.buttons.add(entry.button)
        return state

    def output_value(self, axis_name: str, value: float) -> float:
        if self.config.axes[axis_name].map_0_1:
            return axis_0_1(value)
        return value

    def pad_state(self, state: PadState) -> PadState:
        return PadState(
            left_x=self.output_value("yaw", state.left_x),
            left_y=self.output_value("throttle", state.left_y),
            right_x=self.output_value("roll", state.right_x),
            right_y=self.output_value("pitch", state.right_y),
            buttons=state.buttons,
        )

    def neutral_state(self) -> PadState:
        return PadState(left_y=self.config.failsafe_throttle)
