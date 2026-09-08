#pragma once

#include <stddef.h>
#include <stdint.h>

namespace legacy_rc {

constexpr size_t kPacketBytes = 25;

enum class Error : uint8_t {
    NONE,
    INVALID_LENGTH,
    WRONG_TARGET,
    CHECKSUM,
    NON_FINITE,
    INVALID_MODE,
};

struct Packet {
    float rudder = 0.0f;
    float throttle = 0.0f;
    float aileron = 0.0f;
    float elevator = 0.0f;
    uint8_t button_arm = 0;
    uint8_t button_flip = 0;
    uint8_t control_mode = 0;
    uint8_t alt_mode = 5;
    uint8_t ahrs_reset = 0;
};

Error parse(const uint8_t* data, size_t length, const uint8_t target_tail[3], Packet* output);
const char* error_name(Error error);

}  // namespace legacy_rc
