from __future__ import annotations

import unittest

from host.flight_link_protocol import (
    AckClass,
    Error,
    FlightLinkError,
    MessageKind,
    SessionState,
    SetCommand,
    decode,
    decode_ack,
    decode_set,
    encode,
    make_ack,
    make_claim,
    make_set,
)


class FlightLinkProtocolTests(unittest.TestCase):
    def test_round_trip_and_ack_classes(self):
        command = SetCommand(0.25, -0.5, 0.125, 0.4)
        packet = make_set(0x0102030405060708, 2, 250, command)
        decoded = decode(encode(packet))
        self.assertEqual(decode_set(decoded), command)

        application = decode_ack(
            decode(encode(make_ack(1, 3, 250, MessageKind.SET, 2, AckClass.APPLICATION, True)))
        )
        delivery = decode_ack(
            decode(encode(make_ack(1, 4, 250, MessageKind.SET, 2, AckClass.RF_DELIVERY, True)))
        )
        self.assertEqual(application.ack_class, AckClass.APPLICATION)
        self.assertEqual(delivery.ack_class, AckClass.RF_DELIVERY)

    def test_malformed_packet_is_rejected_before_state(self):
        packet = bytearray(encode(make_claim(1, 1, 250)))
        packet[-1] ^= 1
        with self.assertRaises(FlightLinkError) as context:
            decode(bytes(packet))
        self.assertEqual(context.exception.error, Error.CHECKSUM)

        state = SessionState()
        self.assertEqual(state.accept(make_claim(1, 1, 250), 1000), Error.NONE)
        self.assertEqual(state.last_control_ms, 1000)
        malformed = make_set(1, 2, 250, SetCommand())
        malformed = type(malformed)(
            malformed.version,
            malformed.kind,
            malformed.session_id,
            malformed.sequence,
            malformed.ttl_ms,
            malformed.capabilities,
            b"bad",
        )
        self.assertEqual(state.accept(malformed, 1001), Error.INVALID_PAYLOAD)
        self.assertEqual(state.last_sequence, 1)
        self.assertEqual(state.last_control_ms, 1000)

    def test_duplicate_reordered_wrong_session_expiry_and_reconnect(self):
        state = SessionState()
        self.assertEqual(state.accept(make_claim(9, 1, 250), 0xFFFFFF00), Error.NONE)
        nonzero = make_set(9, 2, 250, SetCommand(throttle=0.4))
        self.assertEqual(state.accept(nonzero, 0xFFFFFF10), Error.NONE)
        self.assertFalse(state.disarmed)
        self.assertEqual(state.accept(nonzero, 0xFFFFFF11), Error.DUPLICATE_SEQUENCE)
        self.assertEqual(state.accept(make_set(9, 1, 250, SetCommand()), 0xFFFFFF12), Error.STALE_SEQUENCE)
        self.assertEqual(state.accept(make_set(10, 3, 250, SetCommand()), 0xFFFFFF13), Error.WRONG_SESSION)
        self.assertTrue(state.connected(0xFFFFFFF0))
        self.assertTrue(state.watchdog_expired(0x00000010))

        self.assertEqual(state.accept(make_claim(9, 1, 250), 0xFFFFFFF1), Error.STALE_SEQUENCE)
        self.assertFalse(state.disarmed)
        self.assertEqual(state.accept(make_claim(9, 3, 250), 0xFFFFFFF2), Error.NONE)
        self.assertTrue(state.disarmed)

        self.assertEqual(state.accept(make_claim(10, 1, 250), 20), Error.NONE)
        self.assertTrue(state.disarmed)
        self.assertEqual(state.setpoint, SetCommand())

    def test_set_range_and_ttl_fail_closed(self):
        with self.assertRaises(FlightLinkError):
            encode(make_set(1, 1, 0, SetCommand()))
        with self.assertRaises(FlightLinkError):
            make_set(1, 1, 250, SetCommand(roll=2.0))


if __name__ == "__main__":
    unittest.main()
