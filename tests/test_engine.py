import threading
import time
import unittest

from elrs_sim.config import Config
from elrs_sim.engine import Engine, pad_changed
from elrs_sim.link import LinkSnapshot
from elrs_sim.mapping import PadState


class FakeLink:
    def __init__(self):
        self.channels = None
        self.last_frame_time = None
        self.frames = 0
        self.event = threading.Event()
        self.stopped = False

    def start(self):
        pass

    def stop(self):
        self.stopped = True
        self.event.set()

    def is_alive(self):
        return False

    def join(self, timeout=None):
        pass

    def snapshot(self):
        return LinkSnapshot(
            channels=list(self.channels) if self.channels is not None else None,
            last_frame_time=self.last_frame_time,
            frames=self.frames,
            crc_errors=0,
            bytes_read=0,
            rate_hz=0.0,
            connected=True,
            port="FAKE",
            error=None,
        )

    def wait_for_frame(self, timeout=None):
        signaled = self.event.wait(timeout)
        if signaled:
            self.event.clear()
        return signaled


class EngineEventTest(unittest.TestCase):
    def test_pad_change_threshold(self):
        base = PadState()
        self.assertFalse(pad_changed(base, PadState(left_x=0.0005)))
        self.assertTrue(pad_changed(base, PadState(left_x=0.001)))
        self.assertTrue(pad_changed(base, PadState(buttons={"A"})))

    def test_engine_applies_new_frame(self):
        link = FakeLink()
        engine = Engine(Config(), link_factory=lambda: link)
        engine.start(pad_enabled=False)
        try:
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline and not engine.status().running:
                time.sleep(0.01)
            self.assertTrue(engine.status().running)
            self.assertTrue(engine.status().failsafe)

            channels = [992] * 16
            channels[2] = 1811
            link.channels = channels
            link.last_frame_time = time.monotonic()
            link.frames = 1
            link.event.set()

            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline and engine.status().snapshot.frames < 1:
                time.sleep(0.01)

            status = engine.status()
            self.assertFalse(status.failsafe)
            self.assertEqual(status.snapshot.frames, 1)
            self.assertAlmostEqual(status.state.left_y, 1.0, places=3)
        finally:
            engine.stop()


if __name__ == "__main__":
    unittest.main()
