"""Pure Python mirror of the versioned ESP-NOW flight-link core.

The module intentionally has no radio, serial, clock, or filesystem access.
Its wire bytes are kept in lockstep with ``firmware/stampfly/src`` so gateway
and receiver code can be added later without changing the safety contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
import math
import struct


PROTOCOL_VERSION = 1
MAGIC = b"CF"
HEADER_FORMAT = "<2sBBQIHHH"
HEADER_BYTES = struct.calcsize(HEADER_FORMAT)
CRC_BYTES = 2
MAX_PAYLOAD_BYTES = 64
MAX_TTL_MS = 60_000
MAX_SEQUENCE = 0xFFFFFFFF
CAPABILITY_VERSIONED_SESSION = 1 << 0
CAPABILITY_APPLICATION_ACK = 1 << 1
CAPABILITY_EMERGENCY_STOP = 1 << 2
REQUIRED_CAPABILITIES = (
    CAPABILITY_VERSIONED_SESSION | CAPABILITY_APPLICATION_ACK | CAPABILITY_EMERGENCY_STOP
)
UNKNOWN_AGE_MS = MAX_SEQUENCE


class MessageKind(IntEnum):
    CLAIM = 1
    SET = 2
    ACK = 3
    RELEASE = 4
    DISARM = 5
    EMERGENCY_STOP = 6


class AckClass(IntEnum):
    APPLICATION = 1
    RF_DELIVERY = 2


class Error(IntEnum):
    NONE = 0
    INVALID_ARGUMENT = 1
    INVALID_LENGTH = 2
    BAD_MAGIC = 3
    UNSUPPORTED_VERSION = 4
    UNKNOWN_KIND = 5
    PAYLOAD_TOO_LARGE = 6
    CHECKSUM = 7
    INVALID_SESSION = 8
    INVALID_SEQUENCE = 9
    INVALID_TTL = 10
    INVALID_CAPABILITIES = 11
    INVALID_PAYLOAD = 12
    INVALID_NUMBER = 13
    RANGE = 14
    WRONG_SESSION = 15
    NOT_CLAIMED = 16
    DUPLICATE_SEQUENCE = 17
    STALE_SEQUENCE = 18
    SEQUENCE_EXHAUSTED = 19
    ACK_NOT_APPLICABLE = 20


class FlightLinkError(ValueError):
    def __init__(self, message: str, error: Error = Error.INVALID_ARGUMENT) -> None:
        super().__init__(message)
        self.error = error


@dataclass(frozen=True)
class SetCommand:
    roll: float = 0.0
    pitch: float = 0.0
    yaw: float = 0.0
    throttle: float = 0.0
    control_mode: int = 0
    alt_mode: int = 5


@dataclass(frozen=True)
class Ack:
    acknowledged_kind: MessageKind = MessageKind.SET
    acknowledged_sequence: int = 0
    ack_class: AckClass = AckClass.APPLICATION
    accepted: bool = False


@dataclass(frozen=True)
class Packet:
    version: int = PROTOCOL_VERSION
    kind: MessageKind = MessageKind.SET
    session_id: int = 0
    sequence: int = 0
    ttl_ms: int = 0
    capabilities: int = REQUIRED_CAPABILITIES
    payload: bytes = field(default=b"")


def _error(error: Error, message: str) -> FlightLinkError:
    return FlightLinkError(message, error)


def _valid_set(command: SetCommand) -> bool:
    values = (command.roll, command.pitch, command.yaw, command.throttle)
    return (
        all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) for value in values)
        and -1.0 <= command.roll <= 1.0
        and -1.0 <= command.pitch <= 1.0
        and -1.0 <= command.yaw <= 1.0
        and 0.0 <= command.throttle <= 1.0
        and command.control_mode in (0, 1)
        and command.alt_mode in (4, 5)
    )


def _validate_packet(packet: Packet) -> None:
    if packet.version != PROTOCOL_VERSION:
        raise _error(Error.UNSUPPORTED_VERSION, "unsupported flight-link version")
    if not isinstance(packet.kind, MessageKind):
        raise _error(Error.UNKNOWN_KIND, "unknown flight-link message kind")
    if type(packet.session_id) is not int or not 1 <= packet.session_id <= 0xFFFFFFFFFFFFFFFF:
        raise _error(Error.INVALID_SESSION, "session ID must be a non-zero uint64")
    if type(packet.sequence) is not int or not 1 <= packet.sequence <= MAX_SEQUENCE:
        raise _error(Error.INVALID_SEQUENCE, "sequence must be a non-zero uint32")
    if type(packet.ttl_ms) is not int or not 1 <= packet.ttl_ms <= MAX_TTL_MS:
        raise _error(Error.INVALID_TTL, "TTL must be in [1, 60000] ms")
    if type(packet.capabilities) is not int or packet.capabilities & REQUIRED_CAPABILITIES != REQUIRED_CAPABILITIES:
        raise _error(Error.INVALID_CAPABILITIES, "required flight-link capabilities are missing")
    if not isinstance(packet.payload, bytes) or len(packet.payload) > MAX_PAYLOAD_BYTES:
        raise _error(Error.PAYLOAD_TOO_LARGE, "payload exceeds flight-link limit")
    if packet.kind in {
        MessageKind.CLAIM,
        MessageKind.RELEASE,
        MessageKind.DISARM,
        MessageKind.EMERGENCY_STOP,
    } and packet.payload:
        raise _error(Error.INVALID_PAYLOAD, "message must not have a payload")
    if packet.kind is MessageKind.SET and len(packet.payload) != 10:
        raise _error(Error.INVALID_PAYLOAD, "SET payload has the wrong length")
    if packet.kind is MessageKind.ACK and len(packet.payload) != 8:
        raise _error(Error.INVALID_PAYLOAD, "ACK payload has the wrong length")
    if packet.kind is MessageKind.SET:
        decode_set(packet)
    if packet.kind is MessageKind.ACK:
        decode_ack(packet)


def _crc16(data: bytes) -> int:
    crc = 0xFFFF
    for value in data:
        crc ^= value << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def _quantize_axis(value: float) -> int:
    scaled = value * 1000.0
    return math.floor(scaled + 0.5) if scaled >= 0 else math.ceil(scaled - 0.5)


def _quantize_throttle(value: float) -> int:
    return _quantize_axis(value)


def make_claim(session_id: int, sequence: int, ttl_ms: int) -> Packet:
    return Packet(kind=MessageKind.CLAIM, session_id=session_id, sequence=sequence, ttl_ms=ttl_ms)


def make_set(session_id: int, sequence: int, ttl_ms: int, command: SetCommand) -> Packet:
    if not _valid_set(command):
        raise _error(Error.RANGE, "SET command is non-finite or out of range")
    payload = struct.pack(
        "<hhhHBB",
        _quantize_axis(command.roll),
        _quantize_axis(command.pitch),
        _quantize_axis(command.yaw),
        _quantize_throttle(command.throttle),
        command.control_mode,
        command.alt_mode,
    )
    return Packet(kind=MessageKind.SET, session_id=session_id, sequence=sequence, ttl_ms=ttl_ms, payload=payload)


def make_ack(
    session_id: int,
    sequence: int,
    ttl_ms: int,
    acknowledged_kind: MessageKind,
    acknowledged_sequence: int,
    ack_class: AckClass,
    accepted: bool,
) -> Packet:
    payload = struct.pack(
        "<BI3B",
        int(acknowledged_kind),
        acknowledged_sequence,
        int(ack_class),
        int(accepted),
        0,
    )
    return Packet(kind=MessageKind.ACK, session_id=session_id, sequence=sequence, ttl_ms=ttl_ms, payload=payload)


def make_release(session_id: int, sequence: int, ttl_ms: int) -> Packet:
    return Packet(kind=MessageKind.RELEASE, session_id=session_id, sequence=sequence, ttl_ms=ttl_ms)


def make_disarm(session_id: int, sequence: int, ttl_ms: int) -> Packet:
    return Packet(kind=MessageKind.DISARM, session_id=session_id, sequence=sequence, ttl_ms=ttl_ms)


def make_emergency_stop(session_id: int, sequence: int, ttl_ms: int) -> Packet:
    return Packet(kind=MessageKind.EMERGENCY_STOP, session_id=session_id, sequence=sequence, ttl_ms=ttl_ms)


def decode_set(packet: Packet) -> SetCommand:
    if packet.kind is not MessageKind.SET or len(packet.payload) != 10:
        raise _error(Error.INVALID_PAYLOAD, "not a SET packet")
    roll, pitch, yaw, throttle, control_mode, alt_mode = struct.unpack("<hhhHBB", packet.payload)
    command = SetCommand(roll / 1000.0, pitch / 1000.0, yaw / 1000.0, throttle / 1000.0, control_mode, alt_mode)
    if not _valid_set(command):
        raise _error(Error.RANGE, "SET command is out of range")
    return command


def decode_ack(packet: Packet) -> Ack:
    if packet.kind is not MessageKind.ACK or len(packet.payload) != 8:
        raise _error(Error.INVALID_PAYLOAD, "not an ACK packet")
    raw_kind, sequence, raw_class, accepted, reserved = struct.unpack("<BI3B", packet.payload)
    try:
        kind = MessageKind(raw_kind)
        ack_class = AckClass(raw_class)
    except ValueError as exc:
        raise _error(Error.INVALID_PAYLOAD, "ACK contains an unknown enum") from exc
    if kind is MessageKind.ACK or sequence == 0 or accepted not in (0, 1) or reserved != 0:
        raise _error(Error.INVALID_PAYLOAD, "ACK payload is malformed")
    return Ack(kind, sequence, ack_class, bool(accepted))


def encode(packet: Packet) -> bytes:
    _validate_packet(packet)
    header = struct.pack(
        HEADER_FORMAT,
        MAGIC,
        packet.version,
        int(packet.kind),
        packet.session_id,
        packet.sequence,
        packet.ttl_ms,
        packet.capabilities,
        len(packet.payload),
    )
    body = header + packet.payload
    return body + struct.pack("<H", _crc16(body))


def decode(data: bytes) -> Packet:
    if not isinstance(data, (bytes, bytearray)) or len(data) < HEADER_BYTES + CRC_BYTES:
        raise _error(Error.INVALID_LENGTH, "flight-link packet is too short")
    magic, version, raw_kind, session_id, sequence, ttl_ms, capabilities, payload_length = struct.unpack(
        HEADER_FORMAT, data[:HEADER_BYTES]
    )
    if magic != MAGIC:
        raise _error(Error.BAD_MAGIC, "flight-link magic is invalid")
    if version != PROTOCOL_VERSION:
        raise _error(Error.UNSUPPORTED_VERSION, "unsupported flight-link version")
    try:
        kind = MessageKind(raw_kind)
    except ValueError as exc:
        raise _error(Error.UNKNOWN_KIND, "unknown flight-link message kind") from exc
    expected_length = HEADER_BYTES + payload_length + CRC_BYTES
    if payload_length > MAX_PAYLOAD_BYTES:
        raise _error(Error.PAYLOAD_TOO_LARGE, "flight-link payload is too large")
    if len(data) != expected_length:
        raise _error(Error.INVALID_LENGTH, "flight-link packet length does not match payload")
    expected_crc = struct.unpack("<H", data[-CRC_BYTES:])[0]
    if _crc16(data[:-CRC_BYTES]) != expected_crc:
        raise _error(Error.CHECKSUM, "flight-link checksum mismatch")
    packet = Packet(version, kind, session_id, sequence, ttl_ms, capabilities, bytes(data[HEADER_BYTES:-CRC_BYTES]))
    _validate_packet(packet)
    return packet


def sequence_is_newer(sequence: int, previous: int) -> bool:
    return type(sequence) is int and type(previous) is int and 1 <= sequence <= MAX_SEQUENCE and previous < MAX_SEQUENCE and sequence > previous


@dataclass
class SessionState:
    _claimed: bool = False
    _has_set: bool = False
    _zero_setpoint: bool = True
    _sequence_exhausted: bool = False
    _session_id: int = 0
    _last_sequence: int = 0
    _last_control_ms: int = 0
    _ttl_ms: int = 0
    _setpoint: SetCommand = field(default_factory=SetCommand)

    def reset(self) -> None:
        self._claimed = False
        self._has_set = False
        self._zero_setpoint = True
        self._sequence_exhausted = False
        self._session_id = 0
        self._last_sequence = 0
        self._last_control_ms = 0
        self._ttl_ms = 0
        self._setpoint = SetCommand()

    @property
    def claimed(self) -> bool:
        return self._claimed

    @property
    def disarmed(self) -> bool:
        return not self._claimed or not self._has_set or self._zero_setpoint

    @property
    def sequence_exhausted(self) -> bool:
        return self._sequence_exhausted

    @property
    def session_id(self) -> int:
        return self._session_id

    @property
    def last_sequence(self) -> int:
        return self._last_sequence

    @property
    def last_control_ms(self) -> int:
        return self._last_control_ms

    @property
    def setpoint(self) -> SetCommand:
        return self._setpoint

    def _accept_sequence(self, packet: Packet, now_ms: int) -> None:
        self._last_sequence = packet.sequence
        self._sequence_exhausted = packet.sequence == MAX_SEQUENCE
        self._last_control_ms = now_ms & MAX_SEQUENCE
        self._ttl_ms = packet.ttl_ms

    def accept(self, packet: Packet, now_ms: int) -> Error:
        try:
            _validate_packet(packet)
        except FlightLinkError as exc:
            return exc.error
        if type(now_ms) is not int or not 0 <= now_ms <= MAX_SEQUENCE:
            return Error.INVALID_ARGUMENT
        if packet.kind is MessageKind.CLAIM:
            if self._claimed and packet.session_id == self._session_id:
                if self._sequence_exhausted:
                    return Error.DUPLICATE_SEQUENCE if packet.sequence == self._last_sequence else Error.SEQUENCE_EXHAUSTED
                if packet.sequence <= self._last_sequence:
                    return Error.DUPLICATE_SEQUENCE if packet.sequence == self._last_sequence else Error.STALE_SEQUENCE
            self._claimed = True
            self._has_set = False
            self._zero_setpoint = True
            self._sequence_exhausted = False
            self._session_id = packet.session_id
            self._setpoint = SetCommand()
            self._accept_sequence(packet, now_ms)
            return Error.NONE
        if not self._claimed:
            return Error.NOT_CLAIMED
        if packet.session_id != self._session_id:
            return Error.WRONG_SESSION
        if self._sequence_exhausted:
            return Error.DUPLICATE_SEQUENCE if packet.sequence == self._last_sequence else Error.SEQUENCE_EXHAUSTED
        if packet.sequence <= self._last_sequence:
            return Error.DUPLICATE_SEQUENCE if packet.sequence == self._last_sequence else Error.STALE_SEQUENCE
        if packet.kind is MessageKind.ACK:
            return Error.ACK_NOT_APPLICABLE
        if packet.kind is MessageKind.RELEASE:
            self.reset()
            return Error.NONE
        if packet.kind in {MessageKind.DISARM, MessageKind.EMERGENCY_STOP}:
            self._accept_sequence(packet, now_ms)
            self._has_set = False
            self._zero_setpoint = True
            self._setpoint = SetCommand()
            return Error.NONE
        if packet.kind is MessageKind.SET:
            self._setpoint = decode_set(packet)
            self._accept_sequence(packet, now_ms)
            self._has_set = True
            self._zero_setpoint = self._setpoint == SetCommand()
            return Error.NONE
        return Error.UNKNOWN_KIND

    def accept_application_ack(self, packet: Packet, now_ms: int) -> Error:
        try:
            _validate_packet(packet)
            ack = decode_ack(packet)
        except FlightLinkError as exc:
            return exc.error
        if packet.kind is not MessageKind.ACK:
            return Error.ACK_NOT_APPLICABLE
        if not self._claimed:
            return Error.NOT_CLAIMED
        if packet.session_id != self._session_id:
            return Error.WRONG_SESSION
        if ack.ack_class is not AckClass.APPLICATION:
            return Error.ACK_NOT_APPLICABLE
        _ = now_ms
        return Error.NONE

    def watchdog_expired(self, now_ms: int) -> bool:
        if not self._claimed or self._ttl_ms == 0:
            return True
        return ((now_ms - self._last_control_ms) & MAX_SEQUENCE) > self._ttl_ms

    def connected(self, now_ms: int) -> bool:
        return self._claimed and not self.watchdog_expired(now_ms)


__all__ = [
    "Ack",
    "AckClass",
    "Error",
    "FlightLinkError",
    "MessageKind",
    "Packet",
    "SessionState",
    "SetCommand",
    "decode",
    "decode_ack",
    "decode_set",
    "encode",
    "make_ack",
    "make_claim",
    "make_disarm",
    "make_emergency_stop",
    "make_release",
    "make_set",
]
