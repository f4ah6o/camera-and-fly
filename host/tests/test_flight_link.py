from __future__ import annotations

import unittest
from unittest import mock

import host.flight_link as flight_link
from host.flight_link import (
    FlightLink,
    FlightLinkDisconnected,
    FlightLinkSequenceExhausted,
    FlightLinkTimeout,
    PacketStreamDecoder,
)
from host.flight_link_protocol import (
    AckClass,
    MAX_SEQUENCE,
    MessageKind,
    decode,
    encode,
    make_ack,
    make_claim,
)
from host.control_loop import ControlIntent, ControlScheduler, SchedulerState


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def now(self) -> float:
        return self.value

    def sleep(self, duration: float) -> None:
        self.value += max(duration, 0.001)


class AckingSerial:
    def __init__(self, *, delivery_before_application: bool = False) -> None:
        self.rx = bytearray()
        self.writes = []
        self.ack_sequence = 1
        self.closed = False
        self.delivery_before_application = delivery_before_application

    @property
    def in_waiting(self) -> int:
        return len(self.rx)

    def write(self, payload: bytes) -> int:
        packet = decode(payload)
        self.writes.append(packet)
        if packet.kind is not MessageKind.DISARM or self.delivery_before_application:
            if self.delivery_before_application and packet.kind is MessageKind.SET:
                self.enqueue_ack(packet, AckClass.RF_DELIVERY)
            self.enqueue_ack(packet, AckClass.APPLICATION)
        return len(payload)

    def enqueue_ack(self, packet, ack_class: AckClass, *, accepted: bool = True) -> None:
        response = make_ack(
            packet.session_id,
            self.ack_sequence,
            packet.ttl_ms,
            packet.kind,
            packet.sequence,
            ack_class,
            accepted,
        )
        self.ack_sequence += 1
        self.rx.extend(encode(response))

    def enqueue_packet(self, packet) -> None:
        self.rx.extend(encode(packet))

    def read(self, size: int) -> bytes:
        if not self.rx:
            return b""
        amount = min(size, len(self.rx))
        result = bytes(self.rx[:amount])
        del self.rx[:amount]
        return result

    def flush(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class SilentSerial(AckingSerial):
    def write(self, payload: bytes) -> int:
        self.writes.append(decode(payload))
        return len(payload)


class DisconnectSerial(AckingSerial):
    def read(self, size: int) -> bytes:
        raise OSError("USB disconnected")


class FlightLinkTransportTests(unittest.TestCase):
    def test_stream_decoder_resynchronizes_after_bad_frame_and_partial_reads(self) -> None:
        valid = encode(make_claim(7, 1, 100))
        malformed = bytearray(valid)
        malformed[-1] ^= 1
        decoder = PacketStreamDecoder(max_buffer_bytes=128)

        self.assertEqual(decoder.feed(b"garbage" + bytes(malformed[:5])), ())
        packets = decoder.feed(bytes(malformed[5:]) + valid[:3])
        self.assertEqual(packets, ())
        packets = decoder.feed(valid[3:])
        self.assertEqual(len(packets), 1)
        self.assertEqual(packets[0].kind, MessageKind.CLAIM)
        self.assertGreaterEqual(decoder.decode_errors, 1)
        self.assertLessEqual(decoder.buffered_bytes, 128)

    def test_claim_set_delivery_ack_and_disarm_use_versioned_packets(self) -> None:
        serial = AckingSerial(delivery_before_application=True)
        transport = FlightLink(
            "/dev/cu.gateway",
            serial_instance=serial,
            session_id=0x1001,
        )

        self.assertEqual(transport.connect(), 0x1001)
        self.assertTrue(transport.claimed)
        set_sequence = transport.set_control(0.25, 0, 0, 0.4, wait_ack=True)
        self.assertEqual(set_sequence, 2)
        self.assertTrue(transport.has_setpoint)
        self.assertEqual(transport.setpoint.throttle, 0.4)
        self.assertIsNotNone(transport.last_delivery_ack)
        self.assertEqual(transport.last_delivery_ack.ack.ack_class, AckClass.RF_DELIVERY)
        self.assertEqual(transport.disarm(), 3)
        self.assertFalse(transport.has_setpoint)
        self.assertFalse(transport.armed)
        self.assertEqual([packet.kind for packet in serial.writes], [
            MessageKind.CLAIM,
            MessageKind.SET,
            MessageKind.DISARM,
        ])

    def test_reordered_and_duplicate_ack_cannot_ack_a_different_set(self) -> None:
        serial = SilentSerial()
        transport = FlightLink("gateway", serial_instance=serial, session_id=11)

        # Claim is acknowledged by an explicitly queued application ACK.
        claim = make_claim(11, 1, 100)
        serial.enqueue_ack(claim, AckClass.APPLICATION)
        self.assertEqual(transport.connect(), 11)

        first = transport.set_control(0, 0, 0, 0, wait_ack=False)
        old_ack = make_ack(11, 2, 100, MessageKind.SET, first, AckClass.APPLICATION, True)
        serial.enqueue_packet(old_ack)

        second = transport.set_control(0.1, 0, 0, 0, wait_ack=False)
        self.assertEqual(serial.writes[-1].sequence, second)
        # A duplicate and a newer ACK arrive before an older one.  All of the
        # old SET acknowledgements must be ignored for the next SET.
        serial.enqueue_packet(old_ack)
        serial.enqueue_packet(make_ack(11, 4, 100, MessageKind.SET, second, AckClass.APPLICATION, True))
        serial.enqueue_packet(make_ack(11, 3, 100, MessageKind.SET, first, AckClass.APPLICATION, True))
        third = transport.set_control(0.2, 0, 0, 0, wait_ack=False)
        next_sequence = transport._sequence
        serial.enqueue_packet(make_ack(11, 5, 100, MessageKind.SET, third, AckClass.APPLICATION, True))
        serial.enqueue_packet(make_ack(11, 6, 100, MessageKind.SET, next_sequence, AckClass.APPLICATION, True))
        self.assertEqual(transport.set_control(0.3, 0, 0, 0, wait_ack=True), next_sequence)
        self.assertGreaterEqual(transport.duplicate_ack_count, 1)
        self.assertTrue(transport.has_setpoint)
        self.assertEqual(transport.setpoint.roll, 0.3)

    def test_reconnect_uses_new_session_and_does_not_restore_set_or_arm(self) -> None:
        ids = iter((100, 200))
        first_serial = AckingSerial()
        second_serial = AckingSerial()
        transport = FlightLink(
            "gateway",
            serial_instance=first_serial,
            session_id_factory=lambda: next(ids),
        )

        self.assertEqual(transport.connect(), 100)
        transport.set_control(0.5, 0, 0, 0.4, wait_ack=False)
        self.assertTrue(transport.has_setpoint)
        self.assertFalse(transport.armed)

        self.assertEqual(transport.reconnect(serial_instance=second_serial), 200)
        self.assertTrue(transport.claimed)
        self.assertFalse(transport.armed)
        self.assertFalse(transport.has_setpoint)
        self.assertEqual(transport.setpoint.roll, 0.0)
        self.assertEqual([packet.kind for packet in second_serial.writes], [MessageKind.CLAIM])
        self.assertEqual(second_serial.writes[0].sequence, 1)
        self.assertEqual(second_serial.writes[0].session_id, 200)

    def test_timeout_and_disconnect_are_bounded_and_require_explicit_reconnect(self) -> None:
        clock = FakeClock()
        silent = SilentSerial()
        transport = FlightLink(
            "gateway",
            serial_instance=silent,
            session_id=42,
            response_timeout=0.005,
            clock=clock.now,
            sleep=clock.sleep,
        )
        with self.assertRaises(FlightLinkTimeout):
            transport.connect()
        self.assertFalse(transport.claimed)
        self.assertLessEqual(transport._decoder.buffered_bytes, transport._decoder.max_buffer_bytes)

        disconnected = FlightLink("gateway", serial_instance=DisconnectSerial(), session_id=43)
        with self.assertRaises(FlightLinkDisconnected):
            disconnected.connect()
        with self.assertRaises(FlightLinkDisconnected):
            disconnected.claim()

        recovered = AckingSerial()
        recovered_session = disconnected.reconnect(serial_instance=recovered)
        self.assertNotEqual(recovered_session, 43)
        self.assertEqual(recovered.writes[0].session_id, recovered_session)
        self.assertTrue(disconnected.claimed)

    def test_sequence_exhaustion_requires_a_new_session(self) -> None:
        transport = FlightLink("gateway", serial_instance=AckingSerial(), session_id=51)
        transport.connect()
        transport._sequence = MAX_SEQUENCE
        self.assertEqual(transport.set_control(0, 0, 0, 0, wait_ack=False), MAX_SEQUENCE)
        with self.assertRaises(FlightLinkSequenceExhausted):
            transport.set_control(0, 0, 0, 0, wait_ack=False)

    def test_scheduler_expiry_is_not_replaced_by_a_transport_heartbeat(self) -> None:
        clock = FakeClock()
        serial = AckingSerial()
        transport = FlightLink("gateway", serial_instance=serial, session_id=61)
        transport.connect()
        scheduler = ControlScheduler(transport, monotonic_clock=clock.now)
        scheduler.publish(
            ControlIntent(
                sequence=1,
                generated_monotonic=0.0,
                valid_until_monotonic=0.2,
                roll=0.4,
                pitch=0.0,
                yaw=0.0,
                throttle=0.0,
            )
        )

        self.assertEqual(scheduler.tick(now=0.0).state, SchedulerState.READY)
        clock.value = 0.201
        self.assertEqual(scheduler.tick().state, SchedulerState.FAULT)
        self.assertEqual([packet.kind for packet in serial.writes], [
            MessageKind.CLAIM,
            MessageKind.SET,
            MessageKind.DISARM,
        ])

    def test_port_is_explicit_and_pyserial_is_not_optional_for_live_use(self) -> None:
        with self.assertRaises(ValueError):
            FlightLink(" ", serial_instance=AckingSerial())
        with self.assertRaises(ValueError):
            FlightLink(" gateway", serial_instance=AckingSerial())
        with mock.patch.object(flight_link, "serial", None):
            with self.assertRaises(flight_link.FlightLinkTransportError):
                FlightLink("/dev/cu.gateway")

    def test_closed_injected_endpoint_requires_a_reopened_instance(self) -> None:
        serial = AckingSerial()
        transport = FlightLink("gateway", serial_instance=serial)
        transport.close()
        with self.assertRaises(FlightLinkDisconnected):
            transport.reconnect()


if __name__ == "__main__":
    unittest.main()
