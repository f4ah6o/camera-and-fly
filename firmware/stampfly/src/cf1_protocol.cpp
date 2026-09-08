#include "cf1_protocol.hpp"

#include <math.h>
#include <stdlib.h>
#include <string.h>

#include <limits>

namespace cf1 {
namespace {

constexpr size_t kMaxTokens = 10;
constexpr size_t kMaxNumberToken = 31;

struct Token {
    const char* begin;
    size_t length;
};

bool token_equals(const Token& token, const char* value) {
    const size_t length = strlen(value);
    return token.length == length && strncmp(token.begin, value, length) == 0;
}

bool parse_uint32(const Token& token, uint32_t* value) {
    if (token.length == 0) return false;
    uint64_t result = 0;
    for (size_t index = 0; index < token.length; ++index) {
        const char c = token.begin[index];
        if (c < '0' || c > '9') return false;
        const uint64_t digit = static_cast<unsigned>(c - '0');
        if (result > (std::numeric_limits<uint32_t>::max() - digit) / 10U) return false;
        result = result * 10U + digit;
    }
    *value = static_cast<uint32_t>(result);
    return true;
}

bool parse_float(const Token& token, float* value) {
    if (token.length == 0 || token.length > kMaxNumberToken) return false;
    char buffer[kMaxNumberToken + 1];
    memcpy(buffer, token.begin, token.length);
    buffer[token.length] = '\0';
    char* end = nullptr;
    const float parsed = strtof(buffer, &end);
    if (end == buffer || *end != '\0' || !isfinite(parsed)) return false;
    *value = parsed;
    return true;
}

bool parse_uint8(const Token& token, uint8_t* value) {
    uint32_t parsed = 0;
    if (!parse_uint32(token, &parsed) || parsed > 255U) return false;
    *value = static_cast<uint8_t>(parsed);
    return true;
}

}  // namespace

ParseResult parse_line(const char* line, size_t length) {
    ParseResult result;
    if (line == nullptr || length == 0) {
        result.error = Error::UNKNOWN_COMMAND;
        return result;
    }

    Token tokens[kMaxTokens];
    size_t token_count = 0;
    size_t index = 0;
    while (index < length) {
        while (index < length && (line[index] == ' ' || line[index] == '\t')) ++index;
        if (index == length) break;
        const size_t begin = index;
        while (index < length && line[index] != ' ' && line[index] != '\t') ++index;
        if (token_count >= kMaxTokens) {
            result.error = Error::TOKEN_COUNT;
            return result;
        }
        tokens[token_count++] = {line + begin, index - begin};
    }

    if (token_count < 2 || !token_equals(tokens[0], "CF1")) {
        result.error = Error::UNKNOWN_COMMAND;
        return result;
    }

    if (token_equals(tokens[1], "HELLO")) result.request.command = Command::HELLO;
    else if (token_equals(tokens[1], "CLAIM")) result.request.command = Command::CLAIM;
    else if (token_equals(tokens[1], "RELEASE")) result.request.command = Command::RELEASE;
    else if (token_equals(tokens[1], "DISARM")) result.request.command = Command::DISARM;
    else if (token_equals(tokens[1], "STATUS")) result.request.command = Command::STATUS;
    else if (token_equals(tokens[1], "ARM")) result.request.command = Command::ARM;
    else if (token_equals(tokens[1], "SET")) result.request.command = Command::SET;
    else {
        result.error = Error::UNKNOWN_COMMAND;
        return result;
    }

    const size_t expected = result.request.command == Command::SET ? 9 : 2;
    if (token_count != expected) {
        result.error = Error::TOKEN_COUNT;
        return result;
    }
    if (result.request.command != Command::SET) return result;

    Setpoint& setpoint = result.request.setpoint;
    if (!parse_uint32(tokens[2], &setpoint.sequence) || setpoint.sequence == 0) {
        result.error = Error::INVALID_SEQUENCE;
        return result;
    }
    if (!parse_float(tokens[3], &setpoint.roll) || !parse_float(tokens[4], &setpoint.pitch) ||
        !parse_float(tokens[5], &setpoint.yaw) || !parse_float(tokens[6], &setpoint.throttle)) {
        result.error = Error::INVALID_NUMBER;
        return result;
    }
    if (setpoint.roll < -1.0f || setpoint.roll > 1.0f || setpoint.pitch < -1.0f || setpoint.pitch > 1.0f ||
        setpoint.yaw < -1.0f || setpoint.yaw > 1.0f || setpoint.throttle < 0.0f || setpoint.throttle > 1.0f) {
        result.error = Error::RANGE;
        return result;
    }
    if (!parse_uint8(tokens[7], &setpoint.control_mode) ||
        (setpoint.control_mode != 0 && setpoint.control_mode != 1)) {
        result.error = Error::INVALID_MODE;
        return result;
    }
    if (!parse_uint8(tokens[8], &setpoint.alt_mode) ||
        (setpoint.alt_mode != 4 && setpoint.alt_mode != 5)) {
        result.error = Error::INVALID_MODE;
        return result;
    }
    return result;
}

void LineCollector::reset() {
    length_ = 0;
    completed_length_ = 0;
    discarding_ = false;
    line_[0] = '\0';
}

LineFeedResult LineCollector::feed(char value) {
    completed_length_ = 0;
    if (value == '\r') return LineFeedResult::NONE;
    if (value == '\n') {
        if (discarding_) {
            length_ = 0;
            discarding_ = false;
            line_[0] = '\0';
            return LineFeedResult::LINE_TOO_LONG;
        }
        if (length_ == 0) return LineFeedResult::NONE;
        line_[length_] = '\0';
        completed_length_ = length_;
        length_ = 0;
        return LineFeedResult::LINE_READY;
    }
    if (discarding_) return LineFeedResult::NONE;
    if (length_ + 1 >= kLineCapacity) {
        length_ = 0;
        discarding_ = true;
        line_[0] = '\0';
        return LineFeedResult::NONE;
    }
    line_[length_++] = value;
    return LineFeedResult::NONE;
}

const char* error_name(Error error) {
    switch (error) {
        case Error::NONE: return "NONE";
        case Error::UNKNOWN_COMMAND: return "UNKNOWN";
        case Error::TOKEN_COUNT: return "TOKEN_COUNT";
        case Error::INVALID_SEQUENCE: return "INVALID_SEQUENCE";
        case Error::INVALID_NUMBER: return "INVALID_NUMBER";
        case Error::RANGE: return "RANGE";
        case Error::INVALID_MODE: return "INVALID_MODE";
        case Error::NOT_CLAIMED: return "NOT_CLAIMED";
        case Error::STALE_SEQUENCE: return "STALE_SEQUENCE";
        case Error::SEQUENCE_EXHAUSTED: return "SEQUENCE_EXHAUSTED";
        case Error::STALE_CONTROL: return "STALE_CONTROL";
        case Error::THROTTLE_NOT_ZERO: return "THROTTLE_NOT_ZERO";
    }
    return "UNKNOWN";
}

void Session::reset() {
    claimed_ = false;
    armed_ = false;
    has_set_ = false;
    sequence_exhausted_ = false;
    last_sequence_ = 0;
    last_control_ms_ = 0;
}

Error Session::claim() {
    claimed_ = true;
    armed_ = false;
    has_set_ = false;
    sequence_exhausted_ = false;
    last_sequence_ = 0;
    last_control_ms_ = 0;
    return Error::NONE;
}

Error Session::release() {
    reset();
    return Error::NONE;
}

Error Session::disarm() {
    armed_ = false;
    return Error::NONE;
}

Error Session::accept_set(const Setpoint& setpoint, uint32_t now_ms) {
    if (!claimed_) return Error::NOT_CLAIMED;
    if (sequence_exhausted_) return Error::SEQUENCE_EXHAUSTED;
    if (has_set_ && setpoint.sequence <= last_sequence_) return Error::STALE_SEQUENCE;
    last_sequence_ = setpoint.sequence;
    sequence_exhausted_ = setpoint.sequence == std::numeric_limits<uint32_t>::max();
    has_set_ = true;
    last_control_ms_ = now_ms;
    return Error::NONE;
}

Error Session::arm(uint32_t now_ms, float throttle) {
    if (!claimed_) return Error::NOT_CLAIMED;
    if (!has_set_ || watchdog_expired(now_ms)) return Error::STALE_CONTROL;
    if (!isfinite(throttle) || throttle > 0.05f) return Error::THROTTLE_NOT_ZERO;
    armed_ = true;
    return Error::NONE;
}

bool Session::connected(uint32_t now_ms) const {
    return claimed_ && has_set_ && !watchdog_expired(now_ms);
}

bool Session::watchdog_expired(uint32_t now_ms) const {
    return !has_set_ || static_cast<uint32_t>(now_ms - last_control_ms_) > kControlTimeoutMs;
}

}  // namespace cf1
