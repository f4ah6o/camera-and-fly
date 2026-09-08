#include "legacy_rc_protocol.hpp"

#include <assert.h>
#include <math.h>
#include <stdint.h>
#include <string.h>

#include <limits>

static void put_float(uint8_t* destination, float value) {
    memcpy(destination, &value, sizeof(value));
}

static void finish_checksum(uint8_t packet[legacy_rc::kPacketBytes]) {
    uint8_t checksum = 0;
    for (size_t index = 0; index < legacy_rc::kPacketBytes - 1; ++index) {
        checksum = static_cast<uint8_t>(checksum + packet[index]);
    }
    packet[legacy_rc::kPacketBytes - 1] = checksum;
}

static void make_packet(uint8_t packet[legacy_rc::kPacketBytes], const uint8_t target[3]) {
    memset(packet, 0, legacy_rc::kPacketBytes);
    memcpy(packet, target, 3);
    put_float(packet + 3, 0.1f);
    put_float(packet + 7, -0.2f);
    put_float(packet + 11, 0.3f);
    put_float(packet + 15, -0.4f);
    packet[19] = 1;
    packet[20] = 0;
    packet[21] = 0;
    packet[22] = 5;
    packet[23] = 0;
    finish_checksum(packet);
}

int main() {
    const uint8_t target[3] = {0x11, 0x22, 0x33};
    uint8_t packet[legacy_rc::kPacketBytes];
    make_packet(packet, target);

    legacy_rc::Packet parsed;
    assert(legacy_rc::parse(packet, sizeof(packet), target, &parsed) == legacy_rc::Error::NONE);
    assert(fabsf(parsed.rudder - 0.1f) < 0.0001f);
    assert(fabsf(parsed.throttle + 0.2f) < 0.0001f);
    assert(parsed.button_arm == 1);
    assert(parsed.control_mode == 0);
    assert(parsed.alt_mode == 5);

    assert(legacy_rc::parse(packet, sizeof(packet) - 1, target, &parsed) == legacy_rc::Error::INVALID_LENGTH);

    const uint8_t wrong_target[3] = {0x44, 0x55, 0x66};
    assert(legacy_rc::parse(packet, sizeof(packet), wrong_target, &parsed) == legacy_rc::Error::WRONG_TARGET);

    packet[10] ^= 0x40;
    assert(legacy_rc::parse(packet, sizeof(packet), target, &parsed) == legacy_rc::Error::CHECKSUM);
    make_packet(packet, target);

    put_float(packet + 3, std::numeric_limits<float>::infinity());
    finish_checksum(packet);
    assert(legacy_rc::parse(packet, sizeof(packet), target, &parsed) == legacy_rc::Error::NON_FINITE);
    make_packet(packet, target);

    packet[21] = 9;
    finish_checksum(packet);
    assert(legacy_rc::parse(packet, sizeof(packet), target, &parsed) == legacy_rc::Error::INVALID_MODE);

    return 0;
}
