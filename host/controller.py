#!/usr/bin/env python3
"""Initial conservative Atom Cam 1 × StampFly controller.

This mode has no vision and no ARM path.  It claims the StampFly, sends zero
control at 20 Hz or faster, and prints firmware STATUS responses.
"""

from __future__ import annotations

import argparse
import signal
import threading
import time

try:
    from .control_loop import ControlIntent, ControlScheduler, SchedulerState
    from .stampfly import StampFly, StampFlyError
except ImportError:  # pragma: no cover - script entrypoint
    from control_loop import ControlIntent, ControlScheduler, SchedulerState
    from stampfly import StampFly, StampFlyError


def main() -> int:
    parser = argparse.ArgumentParser(description="zero-control StampFly controller")
    parser.add_argument("--port", required=True, help="explicit StampFly serial device")
    parser.add_argument("--hz", type=float, default=20.0)
    parser.add_argument("--status-seconds", type=float, default=1.0)
    parser.add_argument("--duration", type=float, default=0.0, help="0 means until Ctrl-C")
    args = parser.parse_args()

    if args.hz < 20.0:
        parser.error("--hz must be at least 20 Hz")
    if args.status_seconds <= 0:
        parser.error("--status-seconds must be positive")

    stop = threading.Event()

    def request_stop(signum, frame) -> None:
        del signum, frame
        stop.set()

    previous_handlers = {}
    for signal_number in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[signal_number] = signal.getsignal(signal_number)
        signal.signal(signal_number, request_stop)

    period = 1.0 / args.hz
    abnormal = True
    stampfly: StampFly | None = None
    scheduler: ControlScheduler | None = None
    intent_sequence = 0

    try:
        stampfly = StampFly(args.port)
        hello = stampfly.connect()
        scheduler = ControlScheduler(stampfly, local_watchdog_seconds=0.200)
        print(f"connected: {hello}", flush=True)
        print("claimed: true; ARM is intentionally not automatic", flush=True)

        started = time.monotonic()
        next_status = started
        while not stop.is_set():
            cycle_started = time.monotonic()
            intent_sequence += 1
            intent = ControlIntent.zero(sequence=intent_sequence, now=cycle_started)
            # The producer timestamp is deliberately created before STATUS
            # I/O.  If STATUS blocks, the scheduler rejects the expired
            # previous heartbeat before it can write another SET.
            scheduler.publish(intent)
            result = scheduler.tick(now=cycle_started)
            if result.state is SchedulerState.FAULT:
                raise StampFlyError(result.fault_reason or "control scheduler fault")

            now = time.monotonic()
            if now >= next_status:
                status = stampfly.status()
                print(
                    "status: "
                    f"claimed={int(status.claimed)} "
                    f"armed={int(status.armed)} "
                    f"connected={int(status.connected)} "
                    f"mode={status.mode_name} "
                    f"voltage={status.voltage:.3f} "
                    f"roll={status.roll:.3f} "
                    f"pitch={status.pitch:.3f} "
                    f"yaw={status.yaw:.3f} "
                    f"altitude={status.altitude:.3f} "
                    f"range={status.range_mm} "
                    f"safe_test={int(status.safe_test)}",
                    flush=True,
                )
                for event in stampfly.drain_events():
                    print(f"event: {event}", flush=True)
                next_status = now + args.status_seconds

            if args.duration > 0 and now - started >= args.duration:
                abnormal = False
                break
            time.sleep(max(0.0, period - (time.monotonic() - cycle_started)))
    except KeyboardInterrupt:
        pass
    except StampFlyError as exc:
        print(f"controller error: {exc}", flush=True)
    finally:
        if stampfly is not None:
            if scheduler is not None:
                scheduler.stop()
            if abnormal:
                # Keep the USB claim fenced on abnormal exit; the firmware
                # watchdog and this best-effort command both disarm.
                stampfly.best_effort_disarm()
            else:
                try:
                    stampfly.disarm()
                    stampfly.release()
                except StampFlyError:
                    stampfly.best_effort_disarm()
            stampfly.close()
        for signal_number, handler in previous_handlers.items():
            signal.signal(signal_number, handler)

    return 1 if abnormal and not stop.is_set() else 0


if __name__ == "__main__":
    raise SystemExit(main())
