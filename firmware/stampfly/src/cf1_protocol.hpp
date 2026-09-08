#pragma once

#include <stddef.h>
#include <stdint.h>

namespace cf1 {

constexpr uint32_t kControlTimeoutMs = 250;
constexpr uint32_t kFirstSequence = 1;
constexpr size_t kLineCapacity = 192;

enum class Command : uint8_t {
    UNKNOWN,
    HELLO,
    CLAIM,
    RELEASE,
    DISARM,
    STATUS,
    ARM,
    SET,
};

enum class Error : uint8_t {
    NONE,
    UNKNOWN_COMMAND,
    TOKEN_COUNT,
    INVALID_SEQUENCE,
    INVALID_NUMBER,
    RANGE,
    INVALID_MODE,
    NOT_CLAIMED,
    STALE_SEQUENCE,
    SEQUENCE_EXHAUSTED,
    STALE_CONTROL,
    THROTTLE_NOT_ZERO,
};

struct Setpoint {
    uint32_t sequence = 0;
    float roll = 0.0f;
    float pitch = 0.0f;
    float yaw = 0.0f;
    float throttle = 0.0f;
    uint8_t control_mode = 0;
    uint8_t alt_mode = 5;
};

struct Request {
    Command command = Command::UNKNOWN;
    Setpoint setpoint;
};

struct ParseResult {
    Request request;
    Error error = Error::NONE;

    bool ok() const { return error == Error::NONE; }
};

ParseResult parse_line(const char* line, size_t length);
const char* error_name(Error error);

enum class LineFeedResult : uint8_t {
    NONE,
    LINE_READY,
    LINE_TOO_LONG,
};

// Bounded CR/LF line collector shared by Arduino integration and native tests.
// Once a line exceeds kLineCapacity, every byte through the next newline is
// discarded so a suffix such as "CF1 ARM" can never become a new command.
class LineCollector {
   public:
    void reset();
    LineFeedResult feed(char value);

    const char* line() const { return line_; }
    size_t length() const { return completed_length_; }
    bool discarding() const { return discarding_; }

   private:
    char line_[kLineCapacity] = {};
    size_t length_ = 0;
    size_t completed_length_ = 0;
    bool discarding_ = false;
};

// Session state is independent from Arduino and can be tested on a native
// host.  It owns only claim/sequence/freshness/ARM preconditions; the flight
// firmware remains responsible for applying an accepted setpoint to Stick.
class Session {
   public:
    void reset();

    Error claim();
    Error release();
    Error disarm();
    Error accept_set(const Setpoint& setpoint, uint32_t now_ms);
    Error arm(uint32_t now_ms, float throttle);

    bool claimed() const { return claimed_; }
    bool armed() const { return armed_; }
    bool has_set() const { return has_set_; }
    uint32_t last_sequence() const { return last_sequence_; }
    uint32_t last_control_ms() const { return last_control_ms_; }
    bool connected(uint32_t now_ms) const;
    bool watchdog_expired(uint32_t now_ms) const;

   private:
    bool claimed_ = false;
    bool armed_ = false;
    bool has_set_ = false;
    bool sequence_exhausted_ = false;
    uint32_t last_sequence_ = 0;
    uint32_t last_control_ms_ = 0;
};

}  // namespace cf1
