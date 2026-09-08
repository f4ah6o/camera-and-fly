#include "legacy_rc_protocol.hpp"

#include <math.h>
#include <string.h>

namespace legacy_rc {
namespace {

float read_float(const uint8_t* data) {
    float value = 0.0f;
    memcpy(&value, data, sizeof(value));
    return value;
}

}  // namespace

Error parse(const uint8_t* data, size_t length, const uint8_t target_tail[3], Packet* output) {
    if (data == nullptr || target_tail == nullptr || output == nullptr || length != kPacketBytes) {
        return Error::INVALID_LENGTH;
    }
    if (data[0] != target_tail[0] || data[1] != target_tail[1] || data[2] != target_tail[2]) {
        return Error::WRONG_TARGET;
    }

    uint8_t checksum = 0;
    for (size_t index = 0; index < kPacketBytes - 1; ++index) checksum = static_cast<uint8_t>(checksum + data[index]);
    if (checksum != data[kPacketBytes - 1]) return Error::CHECKSUM;

    Packet parsed;
    parsed.rudder = read_float(data + 3);
    parsed.throttle = read_float(data + 7);
    parsed.aileron = read_float(data + 11);
    parsed.elevator = read_float(data + 15);
    if (!isfinite(parsed.rudder) || !isfinite(parsed.throttle) || !isfinite(parsed.aileron) ||
        !isfinite(parsed.elevator)) {
        return Error::NON_FINITE;
    }

    parsed.button_arm = data[19];
    parsed.button_flip = data[20];
    parsed.control_mode = data[21];
    parsed.alt_mode = data[22];
    parsed.ahrs_reset = data[23];
    if ((parsed.control_mode != 0 && parsed.control_mode != 1) ||
        (parsed.alt_mode != 4 && parsed.alt_mode != 5)) {
        return Error::INVALID_MODE;
    }

    *output = parsed;
    return Error::NONE;
}

const char* error_name(Error error) {
    switch (error) {
        case Error::NONE: return "NONE";
        case Error::INVALID_LENGTH: return "INVALID_LENGTH";
        case Error::WRONG_TARGET: return "WRONG_TARGET";
        case Error::CHECKSUM: return "CHECKSUM";
        case Error::NON_FINITE: return "NON_FINITE";
        case Error::INVALID_MODE: return "INVALID_MODE";
    }
    return "UNKNOWN";
}

}  // namespace legacy_rc
