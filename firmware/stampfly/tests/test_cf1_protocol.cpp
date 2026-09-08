#include "cf1_protocol.hpp"

#include <assert.h>
#include <stdint.h>
#include <string.h>

#include <limits>

using cf1::Error;

static void test_parser() {
    const char* normal = "CF1 SET 1 0.1 -0.2 0 0 0 5";
    auto parsed = cf1::parse_line(normal, strlen(normal));
    assert(parsed.ok());
    assert(parsed.request.setpoint.sequence == 1);
    assert(parsed.request.setpoint.control_mode == 0);
    assert(parsed.request.setpoint.alt_mode == 5);

    const char* extra = "CF1 SET 1 0 0 0 0 0 5 extra";
    const char* nan = "CF1 SET 1 nan 0 0 0 0 5";
    const char* range = "CF1 SET 1 2 0 0 0 0 5";
    const char* mode = "CF1 SET 1 0 0 0 0 2 5";
    const char* zero = "CF1 SET 0 0 0 0 0 0 5";
    const char* status = "CF1 STATUS extra";
    assert(cf1::parse_line(extra, strlen(extra)).error == Error::TOKEN_COUNT);
    assert(cf1::parse_line(nan, strlen(nan)).error == Error::INVALID_NUMBER);
    assert(cf1::parse_line(range, strlen(range)).error == Error::RANGE);
    assert(cf1::parse_line(mode, strlen(mode)).error == Error::INVALID_MODE);
    assert(cf1::parse_line(zero, strlen(zero)).error == Error::INVALID_SEQUENCE);
    assert(cf1::parse_line(status, strlen(status)).error == Error::TOKEN_COUNT);
}

static cf1::Setpoint setpoint(uint32_t sequence) {
    cf1::Setpoint value;
    value.sequence = sequence;
    return value;
}

static void test_session() {
    cf1::Session session;
    assert(session.arm(0, 0.0f) == Error::NOT_CLAIMED);
    session.claim();
    assert(session.arm(0, 0.0f) == Error::STALE_CONTROL);
    assert(session.accept_set(setpoint(1), 100) == Error::NONE);
    assert(session.arm(100, 0.0f) == Error::NONE);
    assert(session.accept_set(setpoint(1), 101) == Error::STALE_SEQUENCE);
    assert(session.accept_set(setpoint(2), 102) == Error::NONE);
    session.disarm();

    cf1::Session wrap;
    wrap.claim();
    assert(wrap.accept_set(setpoint(0xFFFFFFFEu), 0) == Error::NONE);
    assert(wrap.accept_set(setpoint(0xFFFFFFFFu), 1) == Error::NONE);
    assert(wrap.accept_set(setpoint(3), 2) == Error::SEQUENCE_EXHAUSTED);

    cf1::Session clock;
    clock.claim();
    assert(clock.accept_set(setpoint(1), 0xFFFFFF00u) == Error::NONE);
    assert(!clock.watchdog_expired(0xFFFFFF00u + 250u));
    assert(clock.watchdog_expired(0xFFFFFF00u + 251u));
}

int main() {
    test_parser();
    test_session();
    return 0;
}
