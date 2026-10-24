import unittest

from elrs_sim.config import Config
from elrs_sim.crsf import (
    ADDR_CRSF_RECEIVER,
    TYPE_RC_CHANNELS_PACKED,
    Frame,
    pack_channels,
)
from elrs_sim.link import SerialLink


class LinkEventTest(unittest.TestCase):
    def test_channel_frame_signals_waiter(self):
        link = SerialLink(Config())
        self.assertFalse(link.wait_for_frame(0.01))

        channels = [992] * 16
        channels[0] = 1811
        frame = Frame(
            ADDR_CRSF_RECEIVER,
            TYPE_RC_CHANNELS_PACKED,
            pack_channels(channels),
        )
        link._handle_frame(frame)

        self.assertTrue(link.wait_for_frame(0.1))
        snapshot = link.snapshot()
        self.assertEqual(snapshot.channels, channels)
        self.assertEqual(snapshot.frames, 1)
        self.assertFalse(link.wait_for_frame(0.01))

    def test_other_frame_does_not_signal(self):
        link = SerialLink(Config())
        link._handle_frame(Frame(ADDR_CRSF_RECEIVER, 0x14, b""))
        self.assertFalse(link.wait_for_frame(0.01))
        self.assertIsNone(link.snapshot().channels)


if __name__ == "__main__":
    unittest.main()
