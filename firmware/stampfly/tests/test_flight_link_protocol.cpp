#include "flight_link_protocol.hpp"

#include <assert.h>
#include <stdint.h>

using namespace flight_link;

static SetCommand nonzero_set() {
    SetCommand command;
    command.roll = 0.25f;
    command.pitch = -0.5f;
    command.yaw = 0.125f;
    command.throttle = 0.4f;
    return command;
}

int main() {
    const uint64_t session_id = 0x0102030405060708ULL;
    const Packet original = make_set(session_id, 2, 250, nonzero_set());
    std::vector<uint8_t> wire;
    assert(encode(original, &wire) == Error::NONE);
    assert(wire.size() == kHeaderBytes + 10 + kCrcBytes);
    DecodeResult decoded = decode(wire.data(), wire.size());
    assert(decoded.ok());
    SetCommand parsed;
    assert(decode_set(decoded.packet, &parsed) == Error::NONE);
    assert(parsed.roll == 0.25f);
    assert(parsed.pitch == -0.5f);
    assert(parsed.control_mode == 0);

    wire[wire.size() - 1] ^= 0x01;
    assert(decode(wire.data(), wire.size()).error == Error::CHECKSUM);
    assert(decode(nullptr, 0).error == Error::INVALID_LENGTH);

    SessionState state;
    assert(state.accept(make_claim(session_id, 1, 250), 1000) == Error::NONE);
    assert(state.claimed());
    assert(state.disarmed());
    assert(state.accept(original, 1010) == Error::NONE);
    assert(!state.disarmed());
    assert(state.connected(1260));
    assert(state.watchdog_expired(1261));

    const Packet duplicate = original;
    assert(state.accept(duplicate, 1011) == Error::DUPLICATE_SEQUENCE);
    assert(state.last_control_ms() == 1010);
    assert(state.accept(make_set(session_id, 1, 250, SetCommand{}), 1012) == Error::STALE_SEQUENCE);
    assert(state.accept(make_set(session_id + 1, 3, 250, SetCommand{}), 1013) == Error::WRONG_SESSION);
    assert(state.accept(make_claim(session_id, 1, 250), 1014) == Error::STALE_SEQUENCE);
    assert(!state.disarmed());
    assert(state.accept(make_claim(session_id, 3, 250), 1015) == Error::NONE);
    assert(state.disarmed());

    const Packet application_ack = make_ack(session_id, 4, 250, MessageKind::SET, 2, AckClass::APPLICATION, true);
    const Packet delivery = make_ack(session_id, 5, 250, MessageKind::SET, 2, AckClass::RF_DELIVERY, true);
    assert(state.accept_application_ack(application_ack, 1014) == Error::NONE);
    assert(state.accept_application_ack(delivery, 1015) == Error::ACK_NOT_APPLICABLE);
    assert(state.last_control_ms() == 1015);
    assert(state.accept(application_ack, 1016) == Error::ACK_NOT_APPLICABLE);

    SessionState exhaustion;
    assert(exhaustion.accept(make_claim(session_id, 1, 250), 0) == Error::NONE);
    assert(exhaustion.accept(make_set(session_id, kMaxSequence, 250, SetCommand{}), 1) == Error::NONE);
    assert(exhaustion.sequence_exhausted());
    assert(exhaustion.accept(make_set(session_id, 3, 250, SetCommand{}), 2) == Error::SEQUENCE_EXHAUSTED);
    assert(exhaustion.accept(make_claim(session_id + 1, 1, 250), 3) == Error::NONE);
    assert(exhaustion.disarmed());

    assert(encode(make_set(session_id, 4, 0, SetCommand{}), &wire) == Error::INVALID_TTL);
    SetCommand invalid = SetCommand{};
    invalid.roll = 2.0f;
    assert(encode(make_set(session_id, 5, 250, invalid), &wire) == Error::INVALID_PAYLOAD);
    return 0;
}
