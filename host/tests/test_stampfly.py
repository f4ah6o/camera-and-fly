from __future__ import annotations

from collections import deque
import unittest

from host.stampfly import (
    LocalWatchdogError,
    MAX_SEQUENCE,
    ProtocolError,
    StampFly,
    StampFlyError,
    StampFlyStatus,
)


class Clock:
    def __init__(self):
        self.value = 0.0

    def now(self):
        return self.value


class FakeSerial:
    def __init__(self):
        self.rx = deque()
        self.writes = []
        self.short_write = False
        self.closed = False

    @property
    def in_waiting(self):
        return len(self.rx)

    def write(self, payload):
        self.writes.append(payload.decode())
        command = payload.decode().strip()
        if command == "CF1 HELLO":
            self.rx.append(b"CF1 HELLO stampfly-camfly/2\r\n")
        elif command == "CF1 CLAIM":
            self.rx.append(b"CF1 OK CLAIM\r\n")
        elif command.startswith("CF1 SET "):
            self.rx.append(f"CF1 OK {command.split()[2]}\r\n".encode())
        elif command == "CF1 STATUS":
            self.rx.append(
                b"CF1 STATUS claimed=1 armed=0 connected=1 mode=3 voltage=4.1 "
                b"roll=0 pitch=0 yaw=0 altitude=0 range=0 safe_test=1\r\n"
            )
        elif command in {"CF1 DISARM", "CF1 RELEASE"}:
            self.rx.append(f"CF1 OK {command.split()[1]}\r\n".encode())
        if self.short_write:
            return len(payload) - 1
        return len(payload)

    def read(self, size):
        if not self.rx:
            return b""
        value = self.rx.popleft()
        if len(value) > size:
            self.rx.appendleft(value[size:])
            return value[:size]
        return value

    def flush(self):
        pass

    def close(self):
        self.closed = True


class StampFlyTests(unittest.TestCase):
    def test_fake_serial_handshake_and_status(self):
        fake = FakeSerial()
        client = StampFly("fake", serial_instance=fake, sleep=lambda _: None)
        self.assertEqual(client.connect(), "CF1 HELLO stampfly-camfly/2")
        client.set_control(0, 0, 0, 0, wait_ack=True)
        status = client.status()
        self.assertTrue(status.safe_test)
        self.assertNotIn("CF1 ARM", "".join(fake.writes))

    def test_invalid_status_boolean_and_mode_are_rejected(self):
        base = "CF1 STATUS claimed=1 armed=0 connected=maybe mode=3 voltage=4.1 roll=0 pitch=0 yaw=0 altitude=0 range=0 safe_test=1"
        with self.assertRaises(ProtocolError):
            StampFlyStatus.parse(base)
        with self.assertRaises(ProtocolError):
            StampFlyStatus.parse(base.replace("connected=maybe", "connected=1").replace("mode=3", "mode=99"))

    def test_expired_host_watchdog_and_sequence_limit(self):
        fake = FakeSerial()
        clock = Clock()
        client = StampFly("fake", serial_instance=fake, clock=clock.now, sleep=lambda _: None)
        client.connect()
        client.set_control(0, 0, 0, 0, wait_ack=False)
        clock.value = 0.201
        with self.assertRaises(LocalWatchdogError):
            client.watchdog_check()
        self.assertTrue(any(line.startswith("CF1 DISARM") for line in fake.writes))
        client.claim()
        client._sequence = MAX_SEQUENCE
        client.set_control(0, 0, 0, 0, wait_ack=False)
        with self.assertRaises(ProtocolError):
            client.set_control(0, 0, 0, 0, wait_ack=False)

    def test_short_write_is_failure(self):
        fake = FakeSerial()
        fake.short_write = True
        client = StampFly("fake", serial_instance=fake, sleep=lambda _: None)
        with self.assertRaises(StampFlyError):
            client.hello()

    def test_partial_lines_and_many_delayed_acks_remain_bounded(self):
        class NoisySerial(FakeSerial):
            def write(self, payload):
                self.writes.append(payload.decode())
                command = payload.decode().strip()
                if command == "CF1 HELLO":
                    for sequence in range(200):
                        self.rx.append(f"CF1 OK {sequence}\r\n".encode())
                    self.rx.append(b"CF1 HELLO stampfly-camfly/2\r\n")
                return len(payload)

        fake = NoisySerial()
        client = StampFly("fake", serial_instance=fake, sleep=lambda _: None, max_rx_line_bytes=128)
        self.assertEqual(client.hello(), "CF1 HELLO stampfly-camfly/2")
        self.assertLessEqual(len(client._rx_buffer), 128)

    def test_overlong_received_line_is_rejected_and_buffer_cleared(self):
        class OverlongSerial(FakeSerial):
            def write(self, payload):
                self.writes.append(payload.decode())
                self.rx.append(b"X" * 96 + b"\n")
                return len(payload)

        fake = OverlongSerial()
        client = StampFly("fake", serial_instance=fake, sleep=lambda _: None, max_rx_line_bytes=64)
        with self.assertRaises(ProtocolError):
            client.hello()
        self.assertEqual(client._rx_buffer, bytearray())

    def test_serial_read_disconnect_is_failure(self):
        class DisconnectSerial(FakeSerial):
            def read(self, size):
                raise OSError("disconnected")

        fake = DisconnectSerial()
        client = StampFly("fake", serial_instance=fake, sleep=lambda _: None)
        with self.assertRaises(StampFlyError):
            client.hello()

    def test_missing_response_times_out_with_bounded_buffer(self):
        class SilentSerial(FakeSerial):
            def write(self, payload):
                self.writes.append(payload.decode())
                return len(payload)

        clock = Clock()
        fake = SilentSerial()

        def advance(delay):
            clock.value += max(delay, 0.001)

        client = StampFly(
            "fake",
            serial_instance=fake,
            clock=clock.now,
            sleep=advance,
            response_timeout=0.01,
            max_rx_line_bytes=64,
        )
        with self.assertRaises(ProtocolError):
            client.hello()
        self.assertLessEqual(len(client._rx_buffer), 64)


if __name__ == "__main__":
    unittest.main()
