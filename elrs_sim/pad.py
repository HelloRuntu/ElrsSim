from typing import Dict, List, Set

from .mapping import PadState

BUTTON_NAMES: Dict[str, str] = {
    "A": "XUSB_GAMEPAD_A",
    "B": "XUSB_GAMEPAD_B",
    "X": "XUSB_GAMEPAD_X",
    "Y": "XUSB_GAMEPAD_Y",
    "LB": "XUSB_GAMEPAD_LEFT_SHOULDER",
    "RB": "XUSB_GAMEPAD_RIGHT_SHOULDER",
    "LS": "XUSB_GAMEPAD_LEFT_THUMB",
    "RS": "XUSB_GAMEPAD_RIGHT_THUMB",
    "BACK": "XUSB_GAMEPAD_BACK",
    "START": "XUSB_GAMEPAD_START",
    "GUIDE": "XUSB_GAMEPAD_GUIDE",
    "UP": "XUSB_GAMEPAD_DPAD_UP",
    "DOWN": "XUSB_GAMEPAD_DPAD_DOWN",
    "LEFT": "XUSB_GAMEPAD_DPAD_LEFT",
    "RIGHT": "XUSB_GAMEPAD_DPAD_RIGHT",
}


class VirtualPad:
    def __init__(self) -> None:
        try:
            import vgamepad
        except ImportError as exc:
            raise RuntimeError(
                "vgamepad is not installed. Run: python -m pip install vgamepad"
            ) from exc

        self._vgamepad = vgamepad
        try:
            self._pad = vgamepad.VX360Gamepad()
        except Exception as exc:
            raise RuntimeError(
                "ViGEmBus is not available. Install the ViGEmBus driver and retry."
            ) from exc

        self._buttons: Dict[str, int] = {}
        for name, attribute in BUTTON_NAMES.items():
            value = getattr(vgamepad.XUSB_BUTTON, attribute, None)
            if value is not None:
                self._buttons[name] = value
        self._pressed: Set[str] = set()

    @staticmethod
    def available_buttons() -> List[str]:
        return sorted(BUTTON_NAMES)

    def apply(self, state: PadState) -> None:
        pad = self._pad
        pad.left_joystick_float(
            x_value_float=state.left_x,
            y_value_float=state.left_y,
        )
        pad.right_joystick_float(
            x_value_float=state.right_x,
            y_value_float=state.right_y,
        )

        for name in state.buttons:
            button = self._buttons.get(name)
            if button is None or name in self._pressed:
                continue
            pad.press_button(button)
            self._pressed.add(name)

        for name in list(self._pressed):
            if name in state.buttons:
                continue
            pad.release_button(self._buttons[name])
            self._pressed.discard(name)

        pad.update()

    def reset(self) -> None:
        self._pressed.clear()
        self._pad.reset()
        self._pad.update()
