import unittest

from elrs_sim.config import Config
from elrs_sim.mapping import ChannelMapper, PadState, axis_0_1
from elrs_sim.pad import VirtualPad


def channels_with(**values):
    channels = [992] * 16
    for name, value in values.items():
        index = int(name[2:]) - 1
        channels[index] = value
    return channels


class AxisMappingTest(unittest.TestCase):
    def setUp(self):
        self.config = Config()
        self.mapper = ChannelMapper(self.config)

    def test_roll_extremes(self):
        self.assertAlmostEqual(self.mapper.axis_value("roll", channels_with(ch1=172)), -1.0, places=3)
        self.assertAlmostEqual(self.mapper.axis_value("roll", channels_with(ch1=1811)), 1.0, places=3)
        self.assertAlmostEqual(self.mapper.axis_value("roll", channels_with(ch1=992)), 0.0, places=3)

    def test_throttle_is_linear(self):
        self.assertAlmostEqual(self.mapper.axis_value("throttle", channels_with(ch3=172)), -1.0, places=3)
        self.assertAlmostEqual(self.mapper.axis_value("throttle", channels_with(ch3=1811)), 1.0, places=3)

    def test_invert(self):
        self.config.axes["roll"].invert = True
        self.assertAlmostEqual(self.mapper.axis_value("roll", channels_with(ch1=1811)), -1.0, places=3)

    def test_deadband(self):
        self.config.axes["roll"].deadband = 0.1
        near_center = int(992 + 0.05 * (1811 - 992))
        self.assertEqual(self.mapper.axis_value("roll", channels_with(ch1=near_center)), 0.0)

    def test_pad_state(self):
        state = self.mapper.map(
            channels_with(ch1=1811, ch2=172, ch3=172, ch4=992)
        )
        self.assertAlmostEqual(state.right_x, 1.0, places=3)
        self.assertAlmostEqual(state.right_y, -1.0, places=3)
        self.assertAlmostEqual(state.left_y, -1.0, places=3)
        self.assertAlmostEqual(state.left_x, 0.0, places=3)

    def test_buttons(self):
        state = self.mapper.map(channels_with(ch5=1811, ch6=172))
        self.assertIn("A", state.buttons)
        self.assertIn("B", state.buttons)
        state = self.mapper.map(channels_with(ch5=172, ch6=1811))
        self.assertNotIn("A", state.buttons)
        self.assertIn("X", state.buttons)

    def test_neutral_state(self):
        state = self.mapper.neutral_state()
        self.assertEqual(state.left_y, self.config.failsafe_throttle)
        self.assertEqual(state.buttons, set())


class OutputRangeTest(unittest.TestCase):
    def test_axis_0_1(self):
        self.assertAlmostEqual(axis_0_1(-1.0), 0.0, places=3)
        self.assertAlmostEqual(axis_0_1(-0.5), 0.25, places=3)
        self.assertAlmostEqual(axis_0_1(0.0), 0.5, places=3)
        self.assertAlmostEqual(axis_0_1(0.5), 0.75, places=3)
        self.assertAlmostEqual(axis_0_1(1.0), 1.0, places=3)
        self.assertAlmostEqual(axis_0_1(-2.0), 0.0, places=3)
        self.assertAlmostEqual(axis_0_1(2.0), 1.0, places=3)

    def test_pad_state_maps_selected_axes(self):
        config = Config()
        config.axes["throttle"].map_0_1 = True
        config.axes["roll"].map_0_1 = True
        mapper = ChannelMapper(config)
        state = PadState(left_x=-1.0, left_y=-1.0, right_x=0.0, right_y=1.0)
        mapped = mapper.pad_state(state)
        self.assertEqual(mapped.left_x, -1.0)
        self.assertEqual(mapped.left_y, 0.0)
        self.assertEqual(mapped.right_x, 0.5)
        self.assertEqual(mapped.right_y, 1.0)

    def test_pad_state_failsafe_per_axis(self):
        config = Config()
        config.axes["throttle"].map_0_1 = True
        mapper = ChannelMapper(config)
        state = mapper.pad_state(mapper.neutral_state())
        self.assertEqual(state.left_y, 0.0)
        self.assertEqual(state.left_x, 0.0)

    def test_apply_writes_mapped_state(self):
        class FakePad:
            def __init__(self):
                self.left = None
                self.right = None
                self.updated = False

            def left_joystick_float(self, x_value_float, y_value_float):
                self.left = (x_value_float, y_value_float)

            def right_joystick_float(self, x_value_float, y_value_float):
                self.right = (x_value_float, y_value_float)

            def update(self):
                self.updated = True

        config = Config()
        config.axes["throttle"].map_0_1 = True
        config.axes["roll"].map_0_1 = True
        mapper = ChannelMapper(config)
        state = mapper.pad_state(
            PadState(left_x=-1.0, left_y=-1.0, right_x=0.0, right_y=1.0)
        )
        pad = object.__new__(VirtualPad)
        pad._pad = FakePad()
        pad._buttons = {}
        pad._pressed = set()
        pad.apply(state)
        self.assertEqual(pad._pad.left, (-1.0, 0.0))
        self.assertEqual(pad._pad.right, (0.5, 1.0))
        self.assertTrue(pad._pad.updated)


class ConfigTest(unittest.TestCase):
    def test_defaults_validate(self):
        config = Config()
        config.validate()
        self.assertFalse(any(axis.map_0_1 for axis in config.axes.values()))

    def test_roundtrip_dict(self):
        config = Config()
        restored = Config.from_dict(config.to_dict())
        self.assertEqual(restored.axes["roll"].channel, 1)
        self.assertEqual(len(restored.calibration), 16)
        self.assertFalse(any(axis.map_0_1 for axis in restored.axes.values()))

    def test_roundtrip_per_axis_flag(self):
        config = Config()
        config.axes["throttle"].map_0_1 = True
        restored = Config.from_dict(config.to_dict())
        self.assertTrue(restored.axes["throttle"].map_0_1)
        self.assertFalse(restored.axes["roll"].map_0_1)

    def test_legacy_global_flag_fallback(self):
        config = Config.from_dict({"map_axes_0_1": True})
        self.assertTrue(all(axis.map_0_1 for axis in config.axes.values()))

    def test_per_axis_overrides_legacy_flag(self):
        config = Config.from_dict(
            {
                "map_axes_0_1": True,
                "axes": {"throttle": {"map_0_1": False}},
            }
        )
        self.assertFalse(config.axes["throttle"].map_0_1)
        self.assertTrue(config.axes["roll"].map_0_1)


if __name__ == "__main__":
    unittest.main()
