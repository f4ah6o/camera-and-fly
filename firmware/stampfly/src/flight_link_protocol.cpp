#include "flight_link_protocol.hpp"

#include <math.h>
#include <string.h>

#include <limits>

namespace flight_link {
namespace {

constexpr size_t kSetPayloadBytes = 10;
constexpr size_t kAckPayloadBytes = 8;

uint16_t read_u16(const uint8_t* value) {
    return static_cast<uint16_t>(value[0]) | static_cast<uint16_t>(value[1] << 8);
}

uint32_t read_u32(const uint8_t* value) {
    return static_cast<uint32_t>(value[0]) | (static_cast<uint32_t>(value[1]) << 8) |
           (static_cast<uint32_t>(value[2]) << 16) | (static_cast<uint32_t>(value[3]) << 24);
}

uint64_t read_u64(const uint8_t* value) {
    uint64_t result = 0;
    for (size_t index = 0; index < 8; ++index) result |= static_cast<uint64_t>(value[index]) << (index * 8);
    return result;
}

int16_t read_i16(const uint8_t* value) {
    return static_cast<int16_t>(read_u16(value));
}

void append_u16(std::vector<uint8_t>* output, uint16_t value) {
    output->push_back(static_cast<uint8_t>(value & 0xFFu));
    output->push_back(static_cast<uint8_t>((value >> 8) & 0xFFu));
}

void append_u32(std::vector<uint8_t>* output, uint32_t value) {
    for (size_t index = 0; index < 4; ++index) output->push_back(static_cast<uint8_t>((value >> (index * 8)) & 0xFFu));
}

void append_u64(std::vector<uint8_t>* output, uint64_t value) {
    for (size_t index = 0; index < 8; ++index) output->push_back(static_cast<uint8_t>((value >> (index * 8)) & 0xFFu));
}

void append_i16(std::vector<uint8_t>* output, int16_t value) {
    append_u16(output, static_cast<uint16_t>(value));
}

uint16_t crc16(const uint8_t* bytes, size_t length) {
    uint16_t crc = 0xFFFFu;
    for (size_t index = 0; index < length; ++index) {
        crc ^= static_cast<uint16_t>(bytes[index]) << 8;
        for (uint8_t bit = 0; bit < 8; ++bit) {
            crc = (crc & 0x8000u) != 0 ? static_cast<uint16_t>((crc << 1) ^ 0x1021u)
                                      : static_cast<uint16_t>(crc << 1);
        }
    }
    return crc;
}

bool known_kind(uint8_t raw) {
    return raw >= static_cast<uint8_t>(MessageKind::CLAIM) && raw <= static_cast<uint8_t>(MessageKind::EMERGENCY_STOP);
}

bool no_payload_kind(MessageKind kind) {
    return kind == MessageKind::CLAIM || kind == MessageKind::RELEASE || kind == MessageKind::DISARM ||
           kind == MessageKind::EMERGENCY_STOP;
}

bool valid_set(const SetCommand& command) {
    return isfinite(command.roll) && isfinite(command.pitch) && isfinite(command.yaw) &&
           isfinite(command.throttle) && command.roll >= -1.0f && command.roll <= 1.0f &&
           command.pitch >= -1.0f && command.pitch <= 1.0f && command.yaw >= -1.0f && command.yaw <= 1.0f &&
           command.throttle >= 0.0f && command.throttle <= 1.0f &&
           (command.control_mode == 0 || command.control_mode == 1) &&
           (command.alt_mode == 4 || command.alt_mode == 5);
}

int16_t quantize_axis(float value) {
    return static_cast<int16_t>(lroundf(value * 1000.0f));
}

uint16_t quantize_throttle(float value) {
    return static_cast<uint16_t>(lroundf(value * 1000.0f));
}

Error validate_packet_shape(const Packet& packet) {
    if (packet.version != kProtocolVersion) return Error::UNSUPPORTED_VERSION;
    if (packet.session_id == 0) return Error::INVALID_SESSION;
    if (packet.sequence == 0) return Error::INVALID_SEQUENCE;
    if (packet.ttl_ms == 0 || packet.ttl_ms > kMaxTtlMs) return Error::INVALID_TTL;
    if ((packet.capabilities & kRequiredCapabilities) != kRequiredCapabilities) return Error::INVALID_CAPABILITIES;
    if (packet.payload.size() > kMaxPayloadBytes) return Error::PAYLOAD_TOO_LARGE;
    if (no_payload_kind(packet.kind) && !packet.payload.empty()) return Error::INVALID_PAYLOAD;
    if (packet.kind == MessageKind::SET && packet.payload.size() != kSetPayloadBytes) return Error::INVALID_PAYLOAD;
    if (packet.kind == MessageKind::ACK && packet.payload.size() != kAckPayloadBytes) return Error::INVALID_PAYLOAD;
    if (packet.kind != MessageKind::SET && packet.kind != MessageKind::ACK && !no_payload_kind(packet.kind)) {
        return Error::UNKNOWN_KIND;
    }
    if (packet.kind == MessageKind::SET) {
        SetCommand command;
        const Error error = decode_set(packet, &command);
        if (error != Error::NONE) return error;
    }
    if (packet.kind == MessageKind::ACK) {
        Ack ack;
        const Error error = decode_ack(packet, &ack);
        if (error != Error::NONE) return error;
    }
    return Error::NONE;
}

}  // namespace

Packet make_claim(uint64_t session_id, uint32_t sequence, uint16_t ttl_ms) {
    Packet packet;
    packet.kind = MessageKind::CLAIM;
    packet.session_id = session_id;
    packet.sequence = sequence;
    packet.ttl_ms = ttl_ms;
    return packet;
}

Packet make_set(uint64_t session_id, uint32_t sequence, uint16_t ttl_ms, const SetCommand& command) {
    Packet packet;
    packet.kind = MessageKind::SET;
    packet.session_id = session_id;
    packet.sequence = sequence;
    packet.ttl_ms = ttl_ms;
    // The value-returning convenience API cannot return an Error.  Leave an
    // invalid command payload empty so encode() rejects it before any unsafe
    // float-to-integer conversion or state update can occur.
    if (!valid_set(command)) return packet;
    packet.payload.reserve(kSetPayloadBytes);
    append_i16(&packet.payload, quantize_axis(command.roll));
    append_i16(&packet.payload, quantize_axis(command.pitch));
    append_i16(&packet.payload, quantize_axis(command.yaw));
    append_u16(&packet.payload, quantize_throttle(command.throttle));
    packet.payload.push_back(command.control_mode);
    packet.payload.push_back(command.alt_mode);
    return packet;
}

Packet make_ack(uint64_t session_id, uint32_t sequence, uint16_t ttl_ms, MessageKind acknowledged_kind,
               uint32_t acknowledged_sequence, AckClass ack_class, bool accepted) {
    Packet packet;
    packet.kind = MessageKind::ACK;
    packet.session_id = session_id;
    packet.sequence = sequence;
    packet.ttl_ms = ttl_ms;
    packet.payload.push_back(static_cast<uint8_t>(acknowledged_kind));
    append_u32(&packet.payload, acknowledged_sequence);
    packet.payload.push_back(static_cast<uint8_t>(ack_class));
    packet.payload.push_back(accepted ? 1u : 0u);
    packet.payload.push_back(0u);
    return packet;
}

Packet make_release(uint64_t session_id, uint32_t sequence, uint16_t ttl_ms) {
    Packet packet = make_claim(session_id, sequence, ttl_ms);
    packet.kind = MessageKind::RELEASE;
    return packet;
}

Packet make_disarm(uint64_t session_id, uint32_t sequence, uint16_t ttl_ms) {
    Packet packet = make_claim(session_id, sequence, ttl_ms);
    packet.kind = MessageKind::DISARM;
    return packet;
}

Packet make_emergency_stop(uint64_t session_id, uint32_t sequence, uint16_t ttl_ms) {
    Packet packet = make_claim(session_id, sequence, ttl_ms);
    packet.kind = MessageKind::EMERGENCY_STOP;
    return packet;
}

Error decode_set(const Packet& packet, SetCommand* command) {
    if (command == nullptr || packet.kind != MessageKind::SET || packet.payload.size() != kSetPayloadBytes) {
        return Error::INVALID_ARGUMENT;
    }
    command->roll = static_cast<float>(read_i16(packet.payload.data())) / 1000.0f;
    command->pitch = static_cast<float>(read_i16(packet.payload.data() + 2)) / 1000.0f;
    command->yaw = static_cast<float>(read_i16(packet.payload.data() + 4)) / 1000.0f;
    command->throttle = static_cast<float>(read_u16(packet.payload.data() + 6)) / 1000.0f;
    command->control_mode = packet.payload[8];
    command->alt_mode = packet.payload[9];
    if (!valid_set(*command)) return Error::RANGE;
    return Error::NONE;
}

Error decode_ack(const Packet& packet, Ack* ack) {
    if (ack == nullptr || packet.kind != MessageKind::ACK || packet.payload.size() != kAckPayloadBytes) {
        return Error::INVALID_ARGUMENT;
    }
    if (!known_kind(packet.payload[0]) || packet.payload[0] == static_cast<uint8_t>(MessageKind::ACK)) {
        return Error::INVALID_PAYLOAD;
    }
    if (packet.payload[5] != static_cast<uint8_t>(AckClass::APPLICATION) &&
        packet.payload[5] != static_cast<uint8_t>(AckClass::RF_DELIVERY)) {
        return Error::INVALID_PAYLOAD;
    }
    if (packet.payload[6] > 1u || packet.payload[7] != 0u) return Error::INVALID_PAYLOAD;
    ack->acknowledged_kind = static_cast<MessageKind>(packet.payload[0]);
    ack->acknowledged_sequence = read_u32(packet.payload.data() + 1);
    if (ack->acknowledged_sequence == 0) return Error::INVALID_SEQUENCE;
    ack->ack_class = static_cast<AckClass>(packet.payload[5]);
    ack->accepted = packet.payload[6] == 1u;
    return Error::NONE;
}

Error encode(const Packet& packet, std::vector<uint8_t>* output) {
    if (output == nullptr) return Error::INVALID_ARGUMENT;
    const Error shape = validate_packet_shape(packet);
    if (shape != Error::NONE) return shape;
    output->clear();
    output->reserve(kHeaderBytes + packet.payload.size() + kCrcBytes);
    output->push_back(kMagic0);
    output->push_back(kMagic1);
    output->push_back(packet.version);
    output->push_back(static_cast<uint8_t>(packet.kind));
    append_u64(output, packet.session_id);
    append_u32(output, packet.sequence);
    append_u16(output, packet.ttl_ms);
    append_u16(output, packet.capabilities);
    append_u16(output, static_cast<uint16_t>(packet.payload.size()));
    output->insert(output->end(), packet.payload.begin(), packet.payload.end());
    append_u16(output, crc16(output->data(), output->size()));
    return Error::NONE;
}

DecodeResult decode(const uint8_t* bytes, size_t length) {
    DecodeResult result;
    if (bytes == nullptr || length < kHeaderBytes + kCrcBytes) {
        result.error = Error::INVALID_LENGTH;
        return result;
    }
    if (bytes[0] != kMagic0 || bytes[1] != kMagic1) {
        result.error = Error::BAD_MAGIC;
        return result;
    }
    if (bytes[2] != kProtocolVersion) {
        result.error = Error::UNSUPPORTED_VERSION;
        return result;
    }
    if (!known_kind(bytes[3])) {
        result.error = Error::UNKNOWN_KIND;
        return result;
    }
    const uint16_t payload_length = read_u16(bytes + 20);
    if (payload_length > kMaxPayloadBytes || length != kHeaderBytes + payload_length + kCrcBytes) {
        result.error = payload_length > kMaxPayloadBytes ? Error::PAYLOAD_TOO_LARGE : Error::INVALID_LENGTH;
        return result;
    }
    const uint16_t expected_crc = read_u16(bytes + kHeaderBytes + payload_length);
    if (crc16(bytes, kHeaderBytes + payload_length) != expected_crc) {
        result.error = Error::CHECKSUM;
        return result;
    }
    result.packet.version = bytes[2];
    result.packet.kind = static_cast<MessageKind>(bytes[3]);
    result.packet.session_id = read_u64(bytes + 4);
    result.packet.sequence = read_u32(bytes + 12);
    result.packet.ttl_ms = read_u16(bytes + 16);
    result.packet.capabilities = read_u16(bytes + 18);
    result.packet.payload.assign(bytes + kHeaderBytes, bytes + kHeaderBytes + payload_length);
    result.error = validate_packet_shape(result.packet);
    return result;
}

bool sequence_is_newer(uint32_t sequence, uint32_t previous) {
    return sequence != 0 && previous != kMaxSequence && sequence > previous;
}

const char* error_name(Error error) {
    switch (error) {
        case Error::NONE: return "NONE";
        case Error::INVALID_ARGUMENT: return "INVALID_ARGUMENT";
        case Error::INVALID_LENGTH: return "INVALID_LENGTH";
        case Error::BAD_MAGIC: return "BAD_MAGIC";
        case Error::UNSUPPORTED_VERSION: return "UNSUPPORTED_VERSION";
        case Error::UNKNOWN_KIND: return "UNKNOWN_KIND";
        case Error::PAYLOAD_TOO_LARGE: return "PAYLOAD_TOO_LARGE";
        case Error::CHECKSUM: return "CHECKSUM";
        case Error::INVALID_SESSION: return "INVALID_SESSION";
        case Error::INVALID_SEQUENCE: return "INVALID_SEQUENCE";
        case Error::INVALID_TTL: return "INVALID_TTL";
        case Error::INVALID_CAPABILITIES: return "INVALID_CAPABILITIES";
        case Error::INVALID_PAYLOAD: return "INVALID_PAYLOAD";
        case Error::INVALID_NUMBER: return "INVALID_NUMBER";
        case Error::RANGE: return "RANGE";
        case Error::WRONG_SESSION: return "WRONG_SESSION";
        case Error::NOT_CLAIMED: return "NOT_CLAIMED";
        case Error::DUPLICATE_SEQUENCE: return "DUPLICATE_SEQUENCE";
        case Error::STALE_SEQUENCE: return "STALE_SEQUENCE";
        case Error::SEQUENCE_EXHAUSTED: return "SEQUENCE_EXHAUSTED";
        case Error::ACK_NOT_APPLICABLE: return "ACK_NOT_APPLICABLE";
    }
    return "UNKNOWN";
}

void SessionState::reset() {
    claimed_ = false;
    has_set_ = false;
    zero_setpoint_ = true;
    sequence_exhausted_ = false;
    session_id_ = 0;
    last_sequence_ = 0;
    last_control_ms_ = 0;
    ttl_ms_ = 0;
    setpoint_ = SetCommand{};
}

Error SessionState::validate_for_state(const Packet& packet) const {
    return validate_packet_shape(packet);
}

bool SessionState::sequence_allowed(const Packet& packet) const {
    return !sequence_exhausted_ && packet.sequence > last_sequence_;
}

void SessionState::accept_sequence(const Packet& packet, uint32_t now_ms) {
    last_sequence_ = packet.sequence;
    sequence_exhausted_ = packet.sequence == kMaxSequence;
    last_control_ms_ = now_ms;
    ttl_ms_ = packet.ttl_ms;
}

Error SessionState::accept(const Packet& packet, uint32_t now_ms) {
    const Error shape = validate_for_state(packet);
    if (shape != Error::NONE) return shape;

    if (packet.kind == MessageKind::CLAIM) {
        if (claimed_ && packet.session_id == session_id_ && !sequence_allowed(packet)) {
            return packet.sequence == last_sequence_
                       ? Error::DUPLICATE_SEQUENCE
                       : (sequence_exhausted_ ? Error::SEQUENCE_EXHAUSTED : Error::STALE_SEQUENCE);
        }
        // CLAIM is the only message allowed to establish a new owner.  It
        // deliberately discards every previous setpoint and starts disarmed.
        claimed_ = true;
        has_set_ = false;
        zero_setpoint_ = true;
        sequence_exhausted_ = false;
        session_id_ = packet.session_id;
        setpoint_ = SetCommand{};
        accept_sequence(packet, now_ms);
        return Error::NONE;
    }
    if (!claimed_) return Error::NOT_CLAIMED;
    if (packet.session_id != session_id_) return Error::WRONG_SESSION;
    if (!sequence_allowed(packet)) {
        return packet.sequence == last_sequence_ ? Error::DUPLICATE_SEQUENCE :
                                                     (sequence_exhausted_ ? Error::SEQUENCE_EXHAUSTED : Error::STALE_SEQUENCE);
    }
    if (packet.kind == MessageKind::ACK) return Error::ACK_NOT_APPLICABLE;

    if (packet.kind == MessageKind::RELEASE) {
        reset();
        return Error::NONE;
    }
    if (packet.kind == MessageKind::DISARM || packet.kind == MessageKind::EMERGENCY_STOP) {
        accept_sequence(packet, now_ms);
        has_set_ = false;
        zero_setpoint_ = true;
        setpoint_ = SetCommand{};
        return Error::NONE;
    }
    if (packet.kind == MessageKind::SET) {
        SetCommand command;
        const Error error = decode_set(packet, &command);
        if (error != Error::NONE) return error;
        accept_sequence(packet, now_ms);
        has_set_ = true;
        setpoint_ = command;
        zero_setpoint_ = command.roll == 0.0f && command.pitch == 0.0f && command.yaw == 0.0f &&
                         command.throttle == 0.0f;
        return Error::NONE;
    }
    return Error::UNKNOWN_KIND;
}

Error SessionState::accept_application_ack(const Packet& packet, uint32_t now_ms) {
    const Error shape = validate_for_state(packet);
    if (shape != Error::NONE) return shape;
    if (packet.kind != MessageKind::ACK) return Error::ACK_NOT_APPLICABLE;
    if (!claimed_) return Error::NOT_CLAIMED;
    if (packet.session_id != session_id_) return Error::WRONG_SESSION;
    Ack ack;
    const Error error = decode_ack(packet, &ack);
    if (error != Error::NONE) return error;
    if (ack.ack_class != AckClass::APPLICATION) return Error::ACK_NOT_APPLICABLE;
    // ACK freshness is tracked separately from command freshness.  It never
    // refreshes the SET watchdog and never changes the active setpoint.
    (void)now_ms;
    return Error::NONE;
}

bool SessionState::connected(uint32_t now_ms) const {
    return claimed_ && !watchdog_expired(now_ms);
}

bool SessionState::watchdog_expired(uint32_t now_ms) const {
    if (!claimed_ || ttl_ms_ == 0) return true;
    return static_cast<uint32_t>(now_ms - last_control_ms_) > ttl_ms_;
}

}  // namespace flight_link
