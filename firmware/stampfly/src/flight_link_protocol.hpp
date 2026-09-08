#pragma once

#include <stddef.h>
#include <stdint.h>

#include <vector>

namespace flight_link {

constexpr uint8_t kProtocolVersion = 1;
constexpr uint8_t kMagic0 = 'C';
constexpr uint8_t kMagic1 = 'F';
constexpr size_t kHeaderBytes = 22;
constexpr size_t kCrcBytes = 2;
constexpr size_t kMaxPayloadBytes = 64;
constexpr uint16_t kMaxTtlMs = 60000;
constexpr uint32_t kMaxSequence = 0xFFFFFFFFu;

enum Capability : uint16_t {
    CAPABILITY_VERSIONED_SESSION = 1u << 0,
    CAPABILITY_APPLICATION_ACK = 1u << 1,
    CAPABILITY_EMERGENCY_STOP = 1u << 2,
};
constexpr uint16_t kRequiredCapabilities =
    CAPABILITY_VERSIONED_SESSION | CAPABILITY_APPLICATION_ACK | CAPABILITY_EMERGENCY_STOP;

enum class MessageKind : uint8_t {
    CLAIM = 1,
    SET = 2,
    ACK = 3,
    RELEASE = 4,
    DISARM = 5,
    EMERGENCY_STOP = 6,
};

enum class AckClass : uint8_t {
    APPLICATION = 1,
    RF_DELIVERY = 2,
};

enum class Error : uint8_t {
    NONE,
    INVALID_ARGUMENT,
    INVALID_LENGTH,
    BAD_MAGIC,
    UNSUPPORTED_VERSION,
    UNKNOWN_KIND,
    PAYLOAD_TOO_LARGE,
    CHECKSUM,
    INVALID_SESSION,
    INVALID_SEQUENCE,
    INVALID_TTL,
    INVALID_CAPABILITIES,
    INVALID_PAYLOAD,
    INVALID_NUMBER,
    RANGE,
    WRONG_SESSION,
    NOT_CLAIMED,
    DUPLICATE_SEQUENCE,
    STALE_SEQUENCE,
    SEQUENCE_EXHAUSTED,
    ACK_NOT_APPLICABLE,
};

struct SetCommand {
    float roll = 0.0f;
    float pitch = 0.0f;
    float yaw = 0.0f;
    float throttle = 0.0f;
    uint8_t control_mode = 0;
    uint8_t alt_mode = 5;
};

struct Ack {
    MessageKind acknowledged_kind = MessageKind::SET;
    uint32_t acknowledged_sequence = 0;
    AckClass ack_class = AckClass::APPLICATION;
    bool accepted = false;
};

struct Packet {
    uint8_t version = kProtocolVersion;
    MessageKind kind = MessageKind::SET;
    uint64_t session_id = 0;
    uint32_t sequence = 0;
    uint16_t ttl_ms = 0;
    uint16_t capabilities = kRequiredCapabilities;
    std::vector<uint8_t> payload;
};

struct DecodeResult {
    Packet packet;
    Error error = Error::NONE;

    bool ok() const { return error == Error::NONE; }
};

Packet make_claim(uint64_t session_id, uint32_t sequence, uint16_t ttl_ms);
Packet make_set(uint64_t session_id, uint32_t sequence, uint16_t ttl_ms, const SetCommand& command);
Packet make_ack(uint64_t session_id, uint32_t sequence, uint16_t ttl_ms, MessageKind acknowledged_kind,
               uint32_t acknowledged_sequence, AckClass ack_class, bool accepted);
Packet make_release(uint64_t session_id, uint32_t sequence, uint16_t ttl_ms);
Packet make_disarm(uint64_t session_id, uint32_t sequence, uint16_t ttl_ms);
Packet make_emergency_stop(uint64_t session_id, uint32_t sequence, uint16_t ttl_ms);

Error encode(const Packet& packet, std::vector<uint8_t>* output);
DecodeResult decode(const uint8_t* bytes, size_t length);
Error decode_set(const Packet& packet, SetCommand* command);
Error decode_ack(const Packet& packet, Ack* ack);
const char* error_name(Error error);

bool sequence_is_newer(uint32_t sequence, uint32_t previous);

// This state machine owns only protocol/session freshness.  It never touches
// Stick, ESP-NOW, or any Arduino API.
class SessionState {
   public:
    void reset();
    Error accept(const Packet& packet, uint32_t now_ms);
    Error accept_application_ack(const Packet& packet, uint32_t now_ms);

    bool claimed() const { return claimed_; }
    bool armed() const { return false; }
    bool disarmed() const { return !claimed_ || !has_set_ || zero_setpoint_; }
    bool sequence_exhausted() const { return sequence_exhausted_; }
    bool has_set() const { return has_set_; }
    uint64_t session_id() const { return session_id_; }
    uint32_t last_sequence() const { return last_sequence_; }
    uint32_t last_control_ms() const { return last_control_ms_; }
    uint16_t ttl_ms() const { return ttl_ms_; }
    const SetCommand& setpoint() const { return setpoint_; }
    bool connected(uint32_t now_ms) const;
    bool watchdog_expired(uint32_t now_ms) const;

   private:
    Error validate_for_state(const Packet& packet) const;
    bool sequence_allowed(const Packet& packet) const;
    void accept_sequence(const Packet& packet, uint32_t now_ms);

    bool claimed_ = false;
    bool has_set_ = false;
    bool zero_setpoint_ = true;
    bool sequence_exhausted_ = false;
    uint64_t session_id_ = 0;
    uint32_t last_sequence_ = 0;
    uint32_t last_control_ms_ = 0;
    uint16_t ttl_ms_ = 0;
    SetCommand setpoint_;
};

}  // namespace flight_link
