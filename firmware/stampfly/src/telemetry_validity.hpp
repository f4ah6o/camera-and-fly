#pragma once

#include <stdint.h>

namespace telemetry_contract {

// UINT32_MAX is reserved for an unknown/overflowed age.  It must never be
// rendered as zero: zero means that a sample was observed at the status time.
constexpr uint32_t kUnknownAgeMs = 0xFFFFFFFFu;
constexpr uint32_t kMaxRepresentableAgeMs = 0xFFFFFFFEu;

constexpr uint32_t kAltitudeFreshnessMs = 250u;
constexpr uint32_t kRangeFreshnessMs = 250u;
constexpr uint32_t kImuFreshnessMs = 100u;

struct SampleState {
    bool observed = false;
    bool valid = false;
    uint32_t updated_ms = 0;
};

class Validity {
   public:
    void reset();

    // An invalid attempt is still recorded so a retained numeric value cannot
    // look fresh after a failed sensor read.
    void mark_altitude(bool valid, uint32_t now_ms);
    void mark_range(bool valid, uint32_t now_ms);
    void mark_imu(bool valid, uint32_t now_ms);

    uint32_t altitude_age_ms(uint32_t now_ms) const;
    uint32_t range_age_ms(uint32_t now_ms) const;
    uint32_t imu_age_ms(uint32_t now_ms) const;

    bool altitude_valid(uint32_t now_ms, uint32_t max_age_ms = kAltitudeFreshnessMs) const;
    bool range_valid(uint32_t now_ms, uint32_t max_age_ms = kRangeFreshnessMs) const;
    bool imu_valid(uint32_t now_ms, uint32_t max_age_ms = kImuFreshnessMs) const;

    const SampleState& altitude() const { return altitude_; }
    const SampleState& range() const { return range_; }
    const SampleState& imu() const { return imu_; }

   private:
    static uint32_t age_ms(const SampleState& sample, uint32_t now_ms);
    static bool is_valid(const SampleState& sample, uint32_t now_ms, uint32_t max_age_ms);

    SampleState altitude_;
    SampleState range_;
    SampleState imu_;
};

}  // namespace telemetry_contract
