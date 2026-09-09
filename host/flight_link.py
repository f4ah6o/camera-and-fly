"""Bounded host transport for the versioned flight-link protocol.

The transport is deliberately smaller than a mission controller.  It opens
only the explicitly supplied serial port, sends one versioned packet at a
time, and waits for an application ACK without turning an old command into a
new heartbeat.  A reconnect always starts a new session and never restores a
setpoint or ARM state.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
import secrets
import struct
import threading
import time
from typing import Any, Callable, Deque, Optional

try:
    from .flight_link_protocol import (
        Ack,
        AckClass,
        FlightLinkError as CodecError,
        HEADER_BYTES,
        MAX_PAYLOAD_BYTES,
        MAX_SEQUENCE,
        MAX_TTL_MS,
        MessageKind,
        Packet,
        SetCommand,
        decode,
        decode_ack,
        encode,
        make_claim,
        make_disarm,
        make_emergency_stop,
        make_release,
        make_set,
    )
except ImportError:  # pragma: no cover - exercised by the script entrypoint
    from flight_link_protocol import (
        Ack,
        AckClass,
        FlightLinkError as CodecError,
        HEADER_BYTES,
        MAX_PAYLOAD_BYTES,
        MAX_SEQUENCE,
        MAX_TTL_MS,
        MessageKind,
        Packet,
        SetCommand,
        decode,
        decode_ack,
        encode,
        make_claim,
        make_disarm,
        make_emergency_stop,
        make_release,
        make_set,
    )

try:
    import serial
    from serial import SerialException
except ImportError:  # Replay and native tests do not require pyserial.
    serial = None  # type: ignore[assignment]

    class SerialException(OSError):
        pass


MAX_FRAME_BYTES = HEADER_BYTES + MAX_PAYLOAD_BYTES + 2
DEFAULT_RESPONSE_TIMEOUT = 0.15
DEFAULT_WRITE_TIMEOUT = 0.20
DEFAULT_TTL_MS = 100
DEFAULT_MAX_RX_BUFFER_BYTES = 4096
DEFAULT_MAX_PENDING_PACKETS = 32


class FlightLinkTransportError(RuntimeError):
    """Base class for transport failures."""


class FlightLinkProtocolError(FlightLinkTransportError):
    """A packet or command violated the flight-link contract."""


class FlightLinkTimeout(FlightLinkTransportError):
    """An application response did not arrive before the bounded deadline."""


class FlightLinkDisconnected(FlightLinkTransportError):
    """The serial endpoint stopped accepting reads or writes."""


class FlightLinkRejected(FlightLinkTransportError):
    """The peer returned an application ACK with ``accepted=0``."""


class FlightLinkSequenceExhausted(FlightLinkProtocolError):
    """The current session reached its uint32 sequence limit."""


@dataclass(frozen=True)
class DeliveryAck:
    """The most recent RF-delivery indication for a command."""

    packet: Packet
    ack: Ack


class PacketStreamDecoder:
    """Decode length-prefixed flight-link packets from arbitrary serial chunks.

    The decoder is intentionally bounded.  Bad bytes are discarded until the
    next ``CF`` magic sequence, and an invalid frame never reaches callers as
    a valid packet.  Incomplete input is retained so partial serial reads are
    safe.
    """

    def __init__(self, *, max_buffer_bytes: int = DEFAULT_MAX_RX_BUFFER_BYTES) -> None:
        if type(max_buffer_bytes) is not int or max_buffer_bytes < MAX_FRAME_BYTES:
            raise ValueError(f"max_buffer_bytes must be an integer >= {MAX_FRAME_BYTES}")
        self.max_buffer_bytes = max_buffer_bytes
        self._buffer = bytearray()
        self._decode_errors = 0
        self._discarded_bytes = 0

    @property
    def buffered_bytes(self) -> int:
        return len(self._buffer)

    @property
    def decode_errors(self) -> int:
        return self._decode_errors

    @property
    def discarded_bytes(self) -> int:
        return self._discarded_bytes

    def reset(self) -> None:
        self._buffer.clear()
        self._decode_errors = 0
        self._discarded_bytes = 0

    def feed(self, data: bytes | bytearray | memoryview) -> tuple[Packet, ...]:
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("flight-link stream data must be bytes-like")
        if data:
            self._buffer.extend(data)
        self._trim_overflow()

        packets: list[Packet] = []
        while True:
            if len(self._buffer) < 2:
                break

            magic_index = self._buffer.find(b"CF")
            if magic_index < 0:
                # Preserve a trailing 'C'; it may be the first byte of a
                # magic sequence split across two serial reads.
                if self._buffer[-1] == ord("C"):
                    self._discard(len(self._buffer) - 1)
                else:
                    self._discard(len(self._buffer))
                break
            if magic_index > 0:
                self._discard(magic_index)

            if len(self._buffer) < HEADER_BYTES + 2:
                break
            payload_length = struct.unpack_from("<H", self._buffer, 20)[0]
            if payload_length > MAX_PAYLOAD_BYTES:
                self._decode_errors += 1
                self._discard(1)
                continue

            frame_length = HEADER_BYTES + payload_length + 2
            if len(self._buffer) < frame_length:
                break
            frame = bytes(self._buffer[:frame_length])
            try:
                packet = decode(frame)
            except CodecError:
                self._decode_errors += 1
                # Move by one byte rather than skipping the entire candidate;
                # a valid frame may begin immediately after malformed data.
                self._discard(1)
                continue
            del self._buffer[:frame_length]
            packets.append(packet)

        self._trim_overflow()
        return tuple(packets)

    def _discard(self, count: int) -> None:
        if count <= 0:
            return
        del self._buffer[:count]
        self._discarded_bytes += count

    def _trim_overflow(self) -> None:
        if len(self._buffer) <= self.max_buffer_bytes:
            return
        self._discard(len(self._buffer) - self.max_buffer_bytes)


def _validate_port(port: str) -> None:
    if (
        not isinstance(port, str)
        or not port.strip()
        or port != port.strip()
        or "\r" in port
        or "\n" in port
    ):
        raise ValueError("port must be a non-empty explicit serial device name")


def _validate_session_id(session_id: int) -> int:
    if type(session_id) is not int or not 1 <= session_id <= 0xFFFFFFFFFFFFFFFF:
        raise ValueError("session_id must be a non-zero uint64")
    return session_id


def _validate_positive_timeout(name: str, value: float) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return float(value)


class FlightLink:
    """Explicit-port versioned flight-link transport.

    The class implements the ``ControlTransport`` methods used by
    ``ControlScheduler``: ``set_control`` and ``best_effort_disarm``.  It has
    no ARM method by design.  Any caller that needs to reconnect must do so
    explicitly; ``reconnect()`` claims a new session and starts disarmed.
    """

    def __init__(
        self,
        port: str,
        *,
        baudrate: int = 115200,
        ttl_ms: int = DEFAULT_TTL_MS,
        response_timeout: float = DEFAULT_RESPONSE_TIMEOUT,
        write_timeout: float = DEFAULT_WRITE_TIMEOUT,
        serial_instance: Any | None = None,
        session_id: int | None = None,
        session_id_factory: Callable[[], int] = lambda: secrets.randbits(64),
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        max_rx_buffer_bytes: int = DEFAULT_MAX_RX_BUFFER_BYTES,
        max_pending_packets: int = DEFAULT_MAX_PENDING_PACKETS,
    ) -> None:
        _validate_port(port)
        if type(baudrate) is not int or baudrate <= 0:
            raise ValueError("baudrate must be a positive integer")
        if type(ttl_ms) is not int or not 1 <= ttl_ms <= MAX_TTL_MS:
            raise ValueError(f"ttl_ms must be an integer in [1, {MAX_TTL_MS}]")
        if not callable(session_id_factory) or not callable(clock) or not callable(sleep):
            raise ValueError("session_id_factory, clock, and sleep must be callable")
        if session_id is not None:
            _validate_session_id(session_id)
        if type(max_pending_packets) is not int or max_pending_packets < 1:
            raise ValueError("max_pending_packets must be a positive integer")

        self.port = port
        self.baudrate = baudrate
        self.ttl_ms = ttl_ms
        self.response_timeout = _validate_positive_timeout("response_timeout", response_timeout)
        self.write_timeout = _validate_positive_timeout("write_timeout", write_timeout)
        self._clock = clock
        self._sleep = sleep
        self._session_id_factory = session_id_factory
        self._configured_session_id = session_id
        self._configured_session_id_used = False
        self._previous_session_id: int | None = None
        self._serial_injected = serial_instance is not None
        self._decoder = PacketStreamDecoder(max_buffer_bytes=max_rx_buffer_bytes)
        self._max_pending_packets = max_pending_packets
        self._serial = serial_instance if serial_instance is not None else self._open_serial()
        self._pending_packets: Deque[Packet] = deque(maxlen=max_pending_packets)
        self._unmatched_packets: Deque[Packet] = deque(maxlen=max_pending_packets)
        self._lock = threading.RLock()

        self._session_id: int | None = None
        self._sequence = 1
        self._sequence_exhausted = False
        self._last_ack_sequence = 0
        self._last_delivery_ack: DeliveryAck | None = None
        self._claimed = False
        self._armed = False
        self._has_setpoint = False
        self._setpoint = SetCommand()
        self._last_set_at: float | None = None
        self._closed = False
        self._faulted = False
        self._pending_packet_drops = 0
        self._foreign_packet_count = 0
        self._duplicate_ack_count = 0
        self._seen_ack_sequences: set[int] = set()
        self._ack_sequence_order: Deque[int] = deque(maxlen=max_pending_packets)
        self._completed_ack_targets: set[tuple[MessageKind, int]] = set()
        self._completed_ack_order: Deque[tuple[MessageKind, int]] = deque(maxlen=max_pending_packets)

    def __enter__(self) -> "FlightLink":
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    @property
    def claimed(self) -> bool:
        return self._claimed

    @property
    def armed(self) -> bool:
        # This transport intentionally exposes no ARM path.  Keep the state
        # explicit for callers that share a status interface with StampFly.
        return self._armed

    @property
    def session_id(self) -> int | None:
        return self._session_id

    @property
    def last_set_age(self) -> Optional[float]:
        if self._last_set_at is None:
            return None
        return self._clock() - self._last_set_at

    @property
    def setpoint(self) -> SetCommand:
        return self._setpoint

    @property
    def has_setpoint(self) -> bool:
        return self._has_setpoint

    @property
    def sequence_exhausted(self) -> bool:
        return self._sequence_exhausted

    @property
    def requires_reconnect(self) -> bool:
        return self._faulted

    @property
    def last_delivery_ack(self) -> DeliveryAck | None:
        return self._last_delivery_ack

    @property
    def decoder_errors(self) -> int:
        return self._decoder.decode_errors

    @property
    def pending_packet_drops(self) -> int:
        return self._pending_packet_drops

    @property
    def foreign_packet_count(self) -> int:
        return self._foreign_packet_count

    @property
    def duplicate_ack_count(self) -> int:
        return self._duplicate_ack_count

    def connect(self, *, session_id: int | None = None) -> int:
        """Claim a fresh session; no SET or ARM is restored or generated."""

        return self.claim(session_id=session_id)

    def claim(self, *, session_id: int | None = None) -> int:
        with self._lock:
            self._ensure_open()
            if self._faulted:
                raise FlightLinkDisconnected("reconnect is required after a serial failure")
            if self._claimed:
                raise FlightLinkProtocolError("flight-link session is already claimed")
            self._reset_session()
            resolved_session = self._resolve_session_id(session_id)
            # A written CLAIM with a lost ACK may already own the peer.  Do
            # not allow a retry to reuse that uncertain session ID.
            self._previous_session_id = resolved_session
            self._session_id = resolved_session
            packet = make_claim(resolved_session, 1, self.ttl_ms)
            try:
                self._write_packet(packet)
                self._wait_for_application_ack(packet)
            except FlightLinkDisconnected:
                raise
            except FlightLinkTimeout:
                self._faulted = True
                self._reset_session()
                raise
            except FlightLinkTransportError:
                self._reset_session()
                raise
            self._claimed = True
            self._sequence = 2
            self._sequence_exhausted = False
            self._armed = False
            self._has_setpoint = False
            self._setpoint = SetCommand()
            self._last_set_at = None
            return resolved_session

    def reconnect(self, *, serial_instance: Any | None = None, session_id: int | None = None) -> int:
        """Explicitly reset the endpoint and claim a new session.

        A supplied ``serial_instance`` is useful for tests and for callers
        that have reopened a device after a USB disconnect.  No old session,
        setpoint, ACK, or ARM state survives this method.
        """

        with self._lock:
            if serial_instance is not None:
                self._serial = serial_instance
                self._serial_injected = True
            elif self._serial_injected:
                closed = getattr(self._serial, "closed", False)
                if isinstance(closed, bool) and closed:
                    self._mark_reconnect_failed()
                    raise FlightLinkDisconnected(
                        "a reopened serial_instance is required after close"
                    )
            elif not self._serial_injected:
                try:
                    self._serial.close()
                except (OSError, SerialException):
                    pass
                try:
                    new_serial = self._open_serial()
                except FlightLinkTransportError:
                    self._mark_reconnect_failed()
                    raise
                except (OSError, SerialException) as exc:
                    self._mark_reconnect_failed()
                    raise FlightLinkDisconnected(
                        f"cannot reopen explicit serial port {self.port}: {exc}"
                    ) from exc
                self._serial = new_serial
            self._closed = False
            self._faulted = False
            self._reset_session()
        return self.claim(session_id=session_id)

    def set_control(
        self,
        roll: float,
        pitch: float,
        yaw: float,
        throttle: float,
        *,
        control_mode: int = 0,
        alt_mode: int = 5,
        wait_ack: bool = False,
    ) -> int:
        """Send one explicit SET; this method never regenerates old intents."""

        with self._lock:
            self._ensure_claimed()
            command = self._make_set_command(roll, pitch, yaw, throttle, control_mode, alt_mode)
            sequence = self._reserve_sequence()
            packet = make_set(self._session_id_or_error(), sequence, self.ttl_ms, command)
            sent_at = self._clock()
            try:
                self._write_packet(packet)
                if wait_ack:
                    self._wait_for_application_ack(packet)
            except FlightLinkTimeout:
                # The remote may have applied a command whose ACK was lost.
                # Drop local freshness and force the scheduler's fail-closed
                # path; never retry the same SET as a new heartbeat.
                self._faulted = True
                self._invalidate_local_control()
                raise
            except FlightLinkRejected:
                self._invalidate_local_control()
                raise
            self._setpoint = command
            self._has_setpoint = True
            self._armed = False
            self._last_set_at = sent_at
            return sequence

    def disarm(self, *, wait_ack: bool = True) -> int:
        """Send an acknowledged DISARM and clear the local setpoint."""

        return self._send_zeroing_command(make_disarm, wait_ack=wait_ack)

    def emergency_stop(self, *, wait_ack: bool = True) -> int:
        """Send an acknowledged EMERGENCY_STOP and clear the local setpoint."""

        return self._send_zeroing_command(make_emergency_stop, wait_ack=wait_ack)

    # Short alias for callers that use the wire message name as a method.
    emergency = emergency_stop

    def release(self, *, wait_ack: bool = True) -> None:
        """DISARM first, then release the session, even if the ACK is late."""

        with self._lock:
            if not self._claimed:
                return
            try:
                self.disarm(wait_ack=wait_ack)
            except FlightLinkTransportError:
                self.best_effort_disarm()

            release_error: FlightLinkTransportError | None = None
            try:
                sequence = self._reserve_sequence()
                packet = make_release(self._session_id_or_error(), sequence, self.ttl_ms)
                self._write_packet(packet)
                if wait_ack:
                    self._wait_for_application_ack(packet)
            except FlightLinkTransportError as exc:
                release_error = exc
            finally:
                self._reset_session()

            if release_error is not None:
                raise release_error
            # RELEASE itself is the final ownership boundary.  A missing
            # DISARM ACK does not prevent a successful release from clearing
            # ownership and local control state.

    def best_effort_disarm(self) -> None:
        """Write one DISARM without waiting, for watchdog/fault paths."""

        with self._lock:
            if self._closed or not self._claimed:
                self._invalidate_local_control()
                return
            try:
                sequence = self._reserve_sequence()
                packet = make_disarm(self._session_id_or_error(), sequence, self.ttl_ms)
                self._write_packet(packet)
            except (FlightLinkTransportError, OSError, SerialException):
                pass
            finally:
                self._invalidate_local_control()

    def drain_unmatched_packets(self) -> tuple[Packet, ...]:
        """Return and clear bounded packets unrelated to the last ACK wait."""

        with self._lock:
            packets = tuple(self._unmatched_packets)
            self._unmatched_packets.clear()
            return packets

    def close(self) -> None:
        """Best-effort DISARM, then close the explicitly selected endpoint."""

        with self._lock:
            if self._closed:
                return
            try:
                self.best_effort_disarm()
            finally:
                try:
                    self._serial.close()
                except (OSError, SerialException):
                    pass
                self._closed = True
                self._reset_session()

    def _send_zeroing_command(
        self,
        factory: Callable[[int, int, int], Packet],
        *,
        wait_ack: bool,
    ) -> int:
        with self._lock:
            self._ensure_claimed()
            sequence = self._reserve_sequence()
            packet = factory(self._session_id_or_error(), sequence, self.ttl_ms)
            try:
                self._write_packet(packet)
                if wait_ack:
                    self._wait_for_application_ack(packet)
            except FlightLinkTimeout:
                self._faulted = True
                raise
            finally:
                self._invalidate_local_control()
            return sequence

    def _make_set_command(
        self,
        roll: float,
        pitch: float,
        yaw: float,
        throttle: float,
        control_mode: int,
        alt_mode: int,
    ) -> SetCommand:
        values = (roll, pitch, yaw, throttle)
        if any(
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
            for value in values
        ):
            raise FlightLinkProtocolError("SET values must be finite numbers")
        if not -1.0 <= roll <= 1.0 or not -1.0 <= pitch <= 1.0 or not -1.0 <= yaw <= 1.0:
            raise FlightLinkProtocolError("SET attitude values must be in [-1, 1]")
        if not 0.0 <= throttle <= 1.0:
            raise FlightLinkProtocolError("SET throttle must be in [0, 1]")
        if type(control_mode) is not int or control_mode not in (0, 1):
            raise FlightLinkProtocolError("SET control_mode must be 0 or 1")
        if type(alt_mode) is not int or alt_mode not in (4, 5):
            raise FlightLinkProtocolError("SET alt_mode must be 4 or 5")
        return SetCommand(roll, pitch, yaw, throttle, control_mode, alt_mode)

    def _reserve_sequence(self) -> int:
        if self._sequence_exhausted or not 1 <= self._sequence <= MAX_SEQUENCE:
            raise FlightLinkSequenceExhausted("sequence exhausted; reconnect and claim a new session")
        sequence = self._sequence
        self._sequence_exhausted = sequence == MAX_SEQUENCE
        if not self._sequence_exhausted:
            self._sequence += 1
        return sequence

    def _resolve_session_id(self, requested: int | None) -> int:
        if requested is not None:
            candidate = _validate_session_id(requested)
        elif self._configured_session_id is not None and not self._configured_session_id_used:
            candidate = self._configured_session_id
            self._configured_session_id_used = True
        else:
            candidate = 0
            for _ in range(8):
                try:
                    candidate = _validate_session_id(self._session_id_factory())
                except (TypeError, ValueError) as exc:
                    raise FlightLinkProtocolError("session_id_factory returned an invalid ID") from exc
                if candidate != self._previous_session_id:
                    break
            else:
                raise FlightLinkProtocolError("session_id_factory reused the previous session ID")
        if candidate == self._previous_session_id:
            raise FlightLinkProtocolError("a reconnect must use a new session ID")
        return candidate

    def _session_id_or_error(self) -> int:
        if self._session_id is None:
            raise FlightLinkProtocolError("flight-link session is not claimed")
        return self._session_id

    def _ensure_open(self) -> None:
        if self._closed:
            raise FlightLinkDisconnected("flight-link serial connection is closed")

    def _ensure_claimed(self) -> None:
        self._ensure_open()
        if self._faulted:
            raise FlightLinkDisconnected("reconnect is required after a serial failure")
        if not self._claimed or self._session_id is None:
            raise FlightLinkProtocolError("command requires CLAIM")

    def _invalidate_local_control(self) -> None:
        self._armed = False
        self._has_setpoint = False
        self._setpoint = SetCommand()
        self._last_set_at = None

    def _reset_session(self) -> None:
        self._session_id = None
        self._sequence = 1
        self._sequence_exhausted = False
        self._last_ack_sequence = 0
        self._last_delivery_ack = None
        self._claimed = False
        self._invalidate_local_control()
        self._decoder.reset()
        self._pending_packets.clear()
        self._unmatched_packets.clear()
        self._seen_ack_sequences.clear()
        self._ack_sequence_order.clear()
        self._completed_ack_targets.clear()
        self._completed_ack_order.clear()

    def _mark_disconnected(self) -> None:
        self._faulted = True
        self._reset_session()

    def _mark_reconnect_failed(self) -> None:
        """Clear every claim/control invariant after an open failure."""

        self._closed = False
        self._reset_session()
        self._faulted = True

    def _open_serial(self) -> Any:
        if serial is None:
            raise FlightLinkDisconnected("pyserial is required for a live flight-link connection")
        try:
            return serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=min(0.02, self.response_timeout),
                write_timeout=self.write_timeout,
            )
        except (OSError, SerialException) as exc:
            raise FlightLinkDisconnected(f"cannot open explicit serial port {self.port}: {exc}") from exc

    def _write_packet(self, packet: Packet) -> None:
        try:
            wire = encode(packet)
        except CodecError as exc:
            raise FlightLinkProtocolError(str(exc)) from exc
        try:
            written = self._serial.write(wire)
            if written is not None and written != len(wire):
                raise FlightLinkDisconnected(
                    f"serial short write: expected {len(wire)} bytes, wrote {written}"
                )
            flush = getattr(self._serial, "flush", None)
            if callable(flush):
                flush()
        except FlightLinkDisconnected:
            self._mark_disconnected()
            raise
        except (OSError, SerialException) as exc:
            self._mark_disconnected()
            raise FlightLinkDisconnected(f"flight-link serial write failed: {exc}") from exc

    def _wait_for_application_ack(self, target: Packet) -> Ack:
        deadline = self._clock() + self.response_timeout
        while self._clock() < deadline:
            packet = self._next_packet_until(deadline)
            if packet is None:
                break
            ack = self._consume_ack_packet(packet, target)
            if ack is None:
                continue
            self._remember_completed_ack(target)
            # A response chunk can contain application and RF-delivery ACKs
            # in either order.  Consume already-buffered packets before
            # returning so a same-command delivery indication is not left for
            # the next command's wait.
            self._drain_pending_ack_packets(target)
            return ack
        raise FlightLinkTimeout(
            f"timeout waiting for application ACK for {target.kind.name} sequence {target.sequence}"
        )

    def _consume_ack_packet(self, packet: Packet, target: Packet) -> Ack | None:
        if packet.session_id != self._session_id:
            self._foreign_packet_count += 1
            return None
        if packet.kind is not MessageKind.ACK:
            self._remember_unmatched(packet)
            return None

        ack = decode_ack(packet)
        if packet.sequence in self._seen_ack_sequences:
            self._duplicate_ack_count += 1
            return None
        self._remember_ack_sequence(packet.sequence)
        if ack.ack_class is AckClass.RF_DELIVERY:
            if self._ack_matches(ack, target):
                self._last_delivery_ack = DeliveryAck(packet, ack)
            return None
        if not self._ack_matches(ack, target):
            self._remember_unmatched(packet)
            return None
        if self._ack_target_key(target) in self._completed_ack_targets:
            self._duplicate_ack_count += 1
            return None
        if not ack.accepted:
            raise FlightLinkRejected(
                f"peer rejected {target.kind.name} sequence {target.sequence}"
            )
        return ack

    def _drain_pending_ack_packets(self, target: Packet) -> None:
        while self._pending_packets:
            self._consume_ack_packet(self._pending_packets.popleft(), target)

    @staticmethod
    def _ack_matches(ack: Ack, target: Packet) -> bool:
        return ack.acknowledged_kind == target.kind and ack.acknowledged_sequence == target.sequence

    def _next_packet_until(self, deadline: float) -> Packet | None:
        while self._clock() < deadline:
            if self._pending_packets:
                return self._pending_packets.popleft()
            try:
                chunk = self._read_chunk()
            except (OSError, SerialException) as exc:
                self._mark_disconnected()
                raise FlightLinkDisconnected(f"flight-link serial read failed: {exc}") from exc
            if chunk:
                packets = self._decoder.feed(chunk)
                for packet in packets:
                    if len(self._pending_packets) >= self._max_pending_packets:
                        self._pending_packet_drops += 1
                    self._pending_packets.append(packet)
                continue
            remaining = max(0.0, deadline - self._clock())
            if remaining <= 0:
                break
            self._sleep(min(0.001, remaining))
        return None

    def _read_chunk(self) -> bytes:
        read = getattr(self._serial, "read", None)
        if callable(read):
            waiting = getattr(self._serial, "in_waiting", 0)
            if callable(waiting):
                waiting = waiting()
            try:
                waiting_int = int(waiting or 0)
            except (TypeError, ValueError) as exc:
                raise FlightLinkProtocolError("serial in_waiting is not an integer") from exc
            size = min(256, max(1, waiting_int))
            raw = read(size)
        else:
            readline = getattr(self._serial, "readline", None)
            if not callable(readline):
                raise FlightLinkDisconnected("serial object has no read method")
            raw = readline()
        if not isinstance(raw, (bytes, bytearray, memoryview)):
            raise FlightLinkDisconnected("serial read returned a non-byte value")
        return bytes(raw)

    def _remember_unmatched(self, packet: Packet) -> None:
        self._unmatched_packets.append(packet)

    def _remember_ack_sequence(self, sequence: int) -> None:
        if len(self._ack_sequence_order) >= self._max_pending_packets:
            evicted = self._ack_sequence_order.popleft()
            self._seen_ack_sequences.discard(evicted)
        self._ack_sequence_order.append(sequence)
        self._seen_ack_sequences.add(sequence)
        self._last_ack_sequence = max(self._last_ack_sequence, sequence)

    @staticmethod
    def _ack_target_key(packet: Packet) -> tuple[MessageKind, int]:
        return packet.kind, packet.sequence

    def _remember_completed_ack(self, target: Packet) -> None:
        key = self._ack_target_key(target)
        if len(self._completed_ack_order) >= self._max_pending_packets:
            evicted = self._completed_ack_order.popleft()
            self._completed_ack_targets.discard(evicted)
        self._completed_ack_order.append(key)
        self._completed_ack_targets.add(key)


# Names that make the adapter easy to discover without introducing separate
# implementations or compatibility behavior.
FlightLinkTransport = FlightLink
FlightLinkClient = FlightLink
FlightLinkAdapter = FlightLink
BinaryFrameDecoder = PacketStreamDecoder
FrameStreamDecoder = PacketStreamDecoder
FlightLinkError = FlightLinkTransportError
ProtocolError = FlightLinkProtocolError
TransportTimeout = FlightLinkTimeout
DisconnectedError = FlightLinkDisconnected


__all__ = [
    "BinaryFrameDecoder",
    "DeliveryAck",
    "DisconnectedError",
    "FlightLink",
    "FlightLinkAdapter",
    "FlightLinkClient",
    "FlightLinkDisconnected",
    "FlightLinkError",
    "FlightLinkRejected",
    "FlightLinkProtocolError",
    "FlightLinkSequenceExhausted",
    "FlightLinkTimeout",
    "FlightLinkTransport",
    "FlightLinkTransportError",
    "FrameStreamDecoder",
    "PacketStreamDecoder",
    "ProtocolError",
    "TransportTimeout",
]
