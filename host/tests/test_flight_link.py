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

    def test_higher_unrelated_ack_does_not_discard_lower_matching_ack(self) -> None:
        serial = SilentSerial()
        transport = FlightLink("gateway", serial_instance=serial, session_id=12)
        claim = make_claim(12, 1, 100)
        serial.enqueue_ack(claim, AckClass.APPLICATION)
        transport.connect()

        target_sequence = transport._sequence
        serial.enqueue_packet(
            make_ack(12, 11, 100, MessageKind.SET, target_sequence + 99, AckClass.APPLICATION, True)
        )
        serial.enqueue_packet(
            make_ack(12, 10, 100, MessageKind.SET, target_sequence, AckClass.APPLICATION, True)
        )

        self.assertEqual(
            transport.set_control(0.1, 0, 0, 0, wait_ack=True),
            target_sequence,
        )
        self.assertEqual(transport._last_ack_sequence, 11)
        self.assertEqual(len(transport.drain_unmatched_packets()), 1)

    def test_reordered_rf_delivery_and_application_ack_keep_their_meanings(self) -> None:
        for session_id, response_order in (
            (13, (AckClass.RF_DELIVERY, AckClass.APPLICATION)),
            (17, (AckClass.APPLICATION, AckClass.RF_DELIVERY)),
        ):
            with self.subTest(session_id=session_id, response_order=response_order):
                serial = SilentSerial()
                transport = FlightLink("gateway", serial_instance=serial, session_id=session_id)
                claim = make_claim(session_id, 1, 100)
                serial.enqueue_ack(claim, AckClass.APPLICATION)
                transport.connect()

                target_sequence = transport._sequence
                packets = (
                    ((20, AckClass.RF_DELIVERY), (19, AckClass.APPLICATION))
                    if response_order[0] is AckClass.RF_DELIVERY
                    else ((19, AckClass.APPLICATION), (20, AckClass.RF_DELIVERY))
                )
                for packet_sequence, ack_class in packets:
                    serial.enqueue_packet(
                        make_ack(
                            session_id,
                            packet_sequence,
                            100,
                            MessageKind.SET,
                            target_sequence,
                            ack_class,
                            True,
                        )
                    )

                self.assertEqual(transport.set_control(0.2, 0, 0, 0, wait_ack=True), target_sequence)
                self.assertIsNotNone(transport.last_delivery_ack)
                self.assertEqual(transport.last_delivery_ack.packet.sequence, 20)
                self.assertEqual(transport.last_delivery_ack.ack.ack_class, AckClass.RF_DELIVERY)

    def test_duplicate_ack_is_seen_once_and_cannot_ack_the_next_set(self) -> None:
        serial = SilentSerial()
        transport = FlightLink("gateway", serial_instance=serial, session_id=14)
        claim = make_claim(14, 1, 100)
        serial.enqueue_ack(claim, AckClass.APPLICATION)
        transport.connect()

        first = transport.set_control(0, 0, 0, 0, wait_ack=False)
        second = transport._sequence
        duplicate = make_ack(14, 2, 100, MessageKind.SET, first, AckClass.APPLICATION, True)
        serial.enqueue_packet(duplicate)
        serial.enqueue_packet(duplicate)
        serial.enqueue_packet(make_ack(14, 3, 100, MessageKind.SET, second, AckClass.APPLICATION, True))

        self.assertEqual(transport.set_control(0.3, 0, 0, 0, wait_ack=True), second)
        self.assertGreaterEqual(transport.duplicate_ack_count, 1)
        self.assertEqual(len(transport.drain_unmatched_packets()), 1)

    def test_foreign_session_ack_is_ignored_before_matching_local_ack(self) -> None:
        serial = SilentSerial()
        transport = FlightLink("gateway", serial_instance=serial, session_id=15)
        claim = make_claim(15, 1, 100)
        serial.enqueue_ack(claim, AckClass.APPLICATION)
        transport.connect()

        target_sequence = transport._sequence
        serial.enqueue_packet(
            make_ack(99, 50, 100, MessageKind.SET, target_sequence, AckClass.APPLICATION, True)
        )
        serial.enqueue_packet(
            make_ack(15, 2, 100, MessageKind.SET, target_sequence, AckClass.APPLICATION, True)
        )

        self.assertEqual(transport.set_control(0.4, 0, 0, 0, wait_ack=True), target_sequence)
        self.assertEqual(transport.foreign_packet_count, 1)

    def test_ack_seen_state_and_unmatched_packets_remain_bounded(self) -> None:
        serial = SilentSerial()
        limit = 4
        transport = FlightLink(
            "gateway",
            serial_instance=serial,
            session_id=16,
            max_pending_packets=limit,
        )
        claim = make_claim(16, 1, 100)
        serial.enqueue_ack(claim, AckClass.APPLICATION)
        transport.connect()

        target_sequence = transport._sequence
        for packet_sequence in range(10, 30):
            serial.enqueue_packet(
                make_ack(16, packet_sequence, 100, MessageKind.SET, target_sequence + 1000, AckClass.APPLICATION, True)
            )
        serial.enqueue_packet(
            make_ack(16, 30, 100, MessageKind.SET, target_sequence, AckClass.APPLICATION, True)
        )

        self.assertEqual(transport.set_control(0.5, 0, 0, 0, wait_ack=True), target_sequence)
        self.assertLessEqual(len(transport._pending_packets), limit)
        self.assertLessEqual(len(transport._unmatched_packets), limit)
        self.assertLessEqual(len(transport._seen_ack_sequences), limit)
        self.assertLessEqual(len(transport._ack_sequence_order), limit)

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

    def test_successful_live_reconnect_swaps_endpoint_and_starts_fresh(self) -> None:
        ids = iter((250, 350))
        first_serial = AckingSerial()
        second_serial = AckingSerial()
        with mock.patch.object(
            FlightLink,
            "_open_serial",
            side_effect=[first_serial, second_serial],
        ):
            transport = FlightLink(
                "gateway",
                session_id_factory=lambda: next(ids),
            )
            self.assertEqual(transport.connect(), 250)
            transport.set_control(0.6, 0, 0, 0.4, wait_ack=False)

            self.assertEqual(transport.reconnect(), 350)

        self.assertTrue(first_serial.closed)
        self.assertTrue(transport.claimed)
        self.assertFalse(transport.has_setpoint)
        self.assertFalse(transport.armed)
        self.assertEqual([packet.kind for packet in second_serial.writes], [MessageKind.CLAIM])
        self.assertEqual(second_serial.writes[0].session_id, 350)
        self.assertEqual(second_serial.writes[0].sequence, 1)

    def test_live_reconnect_open_failure_clears_state_and_retry_is_fresh(self) -> None:
        ids = iter((300, 400))
        first_serial = AckingSerial()
        second_serial = AckingSerial()
        with mock.patch.object(
            FlightLink,
            "_open_serial",
            side_effect=[first_serial, OSError("port is unavailable"), second_serial],
        ) as open_serial:
            transport = FlightLink(
                "gateway",
                session_id_factory=lambda: next(ids),
            )
            self.assertEqual(transport.connect(), 300)
            transport.set_control(0.6, 0, 0, 0.4, wait_ack=False)
            self.assertTrue(transport.claimed)
            self.assertTrue(transport.has_setpoint)

            with self.assertRaises(FlightLinkDisconnected):
                transport.reconnect()

            self.assertTrue(first_serial.closed)
            self.assertFalse(transport.claimed)
            self.assertFalse(transport.has_setpoint)
            self.assertFalse(transport.armed)
            self.assertTrue(transport.requires_reconnect)
            self.assertIsNone(transport.session_id)
            self.assertIsNone(transport.last_set_age)
            self.assertEqual(transport.setpoint.roll, 0.0)
            with self.assertRaises(FlightLinkDisconnected):
                transport.set_control(0, 0, 0, 0)

            self.assertEqual(transport.reconnect(), 400)

        self.assertTrue(transport.claimed)
        self.assertFalse(transport.has_setpoint)
        self.assertFalse(transport.armed)
        self.assertFalse(transport.requires_reconnect)
        self.assertEqual([packet.kind for packet in second_serial.writes], [MessageKind.CLAIM])
        self.assertEqual(second_serial.writes[0].session_id, 400)
        self.assertEqual(second_serial.writes[0].sequence, 1)
        self.assertEqual(open_serial.call_count, 3)

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
