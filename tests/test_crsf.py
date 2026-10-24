import unittest

from elrs_sim.crsf import (
    ADDR_FLIGHT_CONTROLLER,
    COMMAND_CRC_POLY,
    TYPE_RC_CHANNELS_PACKED,
    CrsfParser,
    build_bind_frame,
    crc8,
    crc8_poly,
    pack_channels,
    unpack_channels,
)


class CrcTest(unittest.TestCase):
    def test_check_value(self):
        self.assertEqual(crc8(b"123456789"), 0xBC)


class CommandFrameTest(unittest.TestCase):
    def test_bind_frame_matches_betaflight(self):
        self.assertEqual(build_bind_frame(), bytes.fromhex("C80732ECC810019EE8"))

    def test_bind_frame_crcs(self):
        frame = build_bind_frame()
        self.assertEqual(crc8_poly(frame[2:7], COMMAND_CRC_POLY), frame[7])
        self.assertEqual(crc8(frame[2:-1]), frame[-1])


class ChannelPackingTest(unittest.TestCase):
    def test_roundtrip(self):
        values = [172, 1811, 992, 1000, 1500, 500, 2047, 0, 1024, 700, 1300, 900, 1100, 600, 1400, 800]
        payload = pack_channels(values)
        self.assertEqual(len(payload), 22)
        self.assertEqual(unpack_channels(payload), values)

    def test_extremes(self):
        values = [0] * 16
        self.assertEqual(unpack_channels(pack_channels(values)), values)
        values = [2047] * 16
        self.assertEqual(unpack_channels(pack_channels(values)), values)

    def test_short_payload(self):
        with self.assertRaises(ValueError):
            unpack_channels(b"\x00" * 21)


class ParserTest(unittest.TestCase):
    def build(self, values, address=ADDR_FLIGHT_CONTROLLER):
        payload = pack_channels(values)
        body = bytes([TYPE_RC_CHANNELS_PACKED]) + payload
        return bytes([address, len(body) + 1]) + body + bytes([crc8(body)])

    def test_single_frame(self):
        parser = CrsfParser()
        values = list(range(500, 516))
        frames = list(parser.feed(self.build(values)))
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].type, TYPE_RC_CHANNELS_PACKED)
        self.assertEqual(unpack_channels(frames[0].payload), values)

    def test_split_frame(self):
        parser = CrsfParser()
        values = [992] * 16
        data = self.build(values)
        frames = list(parser.feed(data[:5]))
        self.assertEqual(frames, [])
        frames = list(parser.feed(data[5:]))
        self.assertEqual(len(frames), 1)

    def test_garbage_prefix(self):
        parser = CrsfParser()
        values = [1000] * 16
        frames = list(parser.feed(b"\x01\x02garbage" + self.build(values)))
        self.assertEqual(len(frames), 1)
        self.assertGreater(parser.resyncs, 0)

    def test_bad_crc_is_dropped(self):
        parser = CrsfParser()
        data = bytearray(self.build([992] * 16))
        data[-1] ^= 0xFF
        frames = list(parser.feed(bytes(data)))
        self.assertEqual(frames, [])
        self.assertEqual(parser.crc_errors, 1)

    def test_two_frames_in_one_chunk(self):
        parser = CrsfParser()
        data = self.build([500] * 16) + self.build([1500] * 16)
        frames = list(parser.feed(data))
        self.assertEqual(len(frames), 2)


if __name__ == "__main__":
    unittest.main()
