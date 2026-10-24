from dataclasses import dataclass
from typing import Iterator, List, Sequence

ADDR_BROADCAST = 0x00
ADDR_FLIGHT_CONTROLLER = 0xC8
ADDR_RADIO_TRANSMITTER = 0xEA
ADDR_CRSF_RECEIVER = 0xEC
ADDR_CRSF_TRANSMITTER = 0xEE

SYNC_BYTES = frozenset(
    {
        ADDR_BROADCAST,
        ADDR_FLIGHT_CONTROLLER,
        ADDR_RADIO_TRANSMITTER,
        ADDR_CRSF_RECEIVER,
        ADDR_CRSF_TRANSMITTER,
    }
)

TYPE_LINK_STATISTICS = 0x14
TYPE_RC_CHANNELS_PACKED = 0x16
TYPE_COMMAND = 0x32

COMMAND_SUBCMD_RX = 0x10
COMMAND_SUBCMD_RX_BIND = 0x01
COMMAND_CRC_POLY = 0xBA

MIN_FRAME_LENGTH = 2
MAX_FRAME_LENGTH = 64
MAX_BUFFER_SIZE = 4096

CHANNEL_COUNT = 16
CHANNEL_BITS = 11
CHANNEL_MASK = 0x7FF
CHANNEL_MIN = 172
CHANNEL_MID = 992
CHANNEL_MAX = 1811


def crc8_poly(data: bytes, poly: int) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ poly) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def crc8(data: bytes) -> int:
    return crc8_poly(data, 0xD5)


@dataclass(frozen=True)
class Frame:
    address: int
    type: int
    payload: bytes


def unpack_channels(payload: bytes) -> List[int]:
    if len(payload) < 22:
        raise ValueError("channel payload too short")
    values: List[int] = []
    bits = 0
    available = 0
    index = 0
    for _ in range(CHANNEL_COUNT):
        while available < CHANNEL_BITS:
            bits |= payload[index] << available
            index += 1
            available += 8
        values.append(bits & CHANNEL_MASK)
        bits >>= CHANNEL_BITS
        available -= CHANNEL_BITS
    return values


def pack_channels(values: Sequence[int]) -> bytes:
    if len(values) != CHANNEL_COUNT:
        raise ValueError("expected 16 channels")
    out = bytearray()
    bits = 0
    available = 0
    for value in values:
        bits |= (int(value) & CHANNEL_MASK) << available
        available += CHANNEL_BITS
        while available >= 8:
            out.append(bits & 0xFF)
            bits >>= 8
            available -= 8
    if available:
        out.append(bits & 0xFF)
    if len(out) != 22:
        raise ValueError("packed channel frame must be 22 bytes")
    return bytes(out)


def build_command_frame(
    destination: int,
    origin: int,
    command: int,
    payload: bytes = b"",
) -> bytes:
    command_body = bytes([TYPE_COMMAND, destination, origin, command]) + payload
    command_crc = crc8_poly(command_body, COMMAND_CRC_POLY)
    body = command_body + bytes([command_crc])
    return bytes([ADDR_FLIGHT_CONTROLLER, len(body) + 1]) + body + bytes([crc8(body)])


def build_bind_frame() -> bytes:
    return build_command_frame(
        ADDR_CRSF_RECEIVER,
        ADDR_FLIGHT_CONTROLLER,
        COMMAND_SUBCMD_RX,
        bytes([COMMAND_SUBCMD_RX_BIND]),
    )


class CrsfParser:
    def __init__(self) -> None:
        self._buffer = bytearray()
        self.crc_errors = 0
        self.resyncs = 0

    def reset(self) -> None:
        self._buffer.clear()
        self.crc_errors = 0
        self.resyncs = 0

    def feed(self, data: bytes) -> Iterator[Frame]:
        self._buffer.extend(data)
        if len(self._buffer) > MAX_BUFFER_SIZE:
            del self._buffer[:-1024]
            self.resyncs += 1

        while True:
            dropped = False
            while self._buffer and self._buffer[0] not in SYNC_BYTES:
                del self._buffer[0]
                dropped = True
            if dropped:
                self.resyncs += 1
            if len(self._buffer) < 2:
                return

            length = self._buffer[1]
            if length < MIN_FRAME_LENGTH or length > MAX_FRAME_LENGTH:
                del self._buffer[0]
                self.resyncs += 1
                continue

            total = length + 2
            if len(self._buffer) < total:
                return

            expected = self._buffer[total - 1]
            actual = crc8(bytes(self._buffer[2 : total - 1]))
            if actual == expected:
                frame = Frame(
                    address=self._buffer[0],
                    type=self._buffer[2],
                    payload=bytes(self._buffer[3 : total - 1]),
                )
                del self._buffer[:total]
                yield frame
            else:
                self.crc_errors += 1
                del self._buffer[0]
                self.resyncs += 1

    def build_frame(self, frame_type: int, payload: bytes, address: int = ADDR_FLIGHT_CONTROLLER) -> bytes:
        body = bytes([frame_type]) + payload
        return bytes([address, len(body) + 1]) + body + bytes([crc8(body)])
