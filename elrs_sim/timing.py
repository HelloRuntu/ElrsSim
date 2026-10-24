import ctypes
import sys


class TimerResolution:
    def __init__(self, milliseconds: int = 1) -> None:
        self._period = milliseconds
        self._winmm = None

    def __enter__(self) -> "TimerResolution":
        if sys.platform == "win32":
            try:
                self._winmm = ctypes.WinDLL("winmm")
                self._winmm.timeBeginPeriod(self._period)
            except Exception:
                self._winmm = None
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self._winmm is not None:
            try:
                self._winmm.timeEndPeriod(self._period)
            except Exception:
                pass
