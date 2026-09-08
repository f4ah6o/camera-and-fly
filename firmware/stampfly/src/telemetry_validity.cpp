#include "telemetry_validity.hpp"

namespace telemetry_contract {

void Validity::reset() {
    altitude_ = SampleState{};
    range_ = SampleState{};
    imu_ = SampleState{};
}

void Validity::mark_altitude(bool valid, uint32_t now_ms) {
    altitude_.observed = true;
    altitude_.valid = valid;
    altitude_.updated_ms = now_ms;
}

void Validity::mark_range(bool valid, uint32_t now_ms) {
    range_.observed = true;
    range_.valid = valid;
    range_.updated_ms = now_ms;
}

void Validity::mark_imu(bool valid, uint32_t now_ms) {
    imu_.observed = true;
    imu_.valid = valid;
    imu_.updated_ms = now_ms;
}

uint32_t Validity::age_ms(const SampleState& sample, uint32_t now_ms) {
    if (!sample.observed) return kUnknownAgeMs;

    // Unsigned subtraction intentionally handles one uint32 millis() wrap.
    // A backwards/ambiguous jump is represented as unknown rather than a
    // falsely fresh small value.
    const uint32_t elapsed = now_ms - sample.updated_ms;
    if (elapsed > 0x7FFFFFFFu) return kUnknownAgeMs;
    return elapsed > kMaxRepresentableAgeMs ? kMaxRepresentableAgeMs : elapsed;
}

bool Validity::is_valid(const SampleState& sample, uint32_t now_ms, uint32_t max_age_ms) {
    if (max_age_ms > kMaxRepresentableAgeMs) return false;
    const uint32_t age = age_ms(sample, now_ms);
    return sample.valid && age != kUnknownAgeMs && age <= max_age_ms;
}

uint32_t Validity::altitude_age_ms(uint32_t now_ms) const {
    return age_ms(altitude_, now_ms);
}

uint32_t Validity::range_age_ms(uint32_t now_ms) const {
    return age_ms(range_, now_ms);
}

uint32_t Validity::imu_age_ms(uint32_t now_ms) const {
    return age_ms(imu_, now_ms);
}

bool Validity::altitude_valid(uint32_t now_ms, uint32_t max_age_ms) const {
    return is_valid(altitude_, now_ms, max_age_ms);
}

bool Validity::range_valid(uint32_t now_ms, uint32_t max_age_ms) const {
    return is_valid(range_, now_ms, max_age_ms);
}

bool Validity::imu_valid(uint32_t now_ms, uint32_t max_age_ms) const {
    return is_valid(imu_, now_ms, max_age_ms);
}

}  // namespace telemetry_contract
