#include "telemetry_validity.hpp"

#include <assert.h>
#include <stdint.h>

int main() {
    telemetry_contract::Validity validity;

    assert(validity.altitude_age_ms(10) == telemetry_contract::kUnknownAgeMs);
    assert(!validity.altitude_valid(10));
    assert(validity.range_age_ms(10) == telemetry_contract::kUnknownAgeMs);
    assert(!validity.imu_valid(10));

    validity.mark_range(true, 1000);
    assert(validity.range_age_ms(1000) == 0);
    assert(validity.range_valid(1250));
    assert(!validity.range_valid(1251));

    // A failed ToF read invalidates the retained numeric value immediately.
    validity.mark_range(false, 1300);
    assert(validity.range_age_ms(1300) == 0);
    assert(!validity.range_valid(1300));

    // millis() wrap remains fresh when the elapsed duration is unambiguous.
    validity.mark_imu(true, 0xFFFFFF00u);
    assert(validity.imu_age_ms(0xFFFFFF20u) == 0x20u);
    assert(validity.imu_valid(0xFFFFFF20u));
    assert(validity.imu_age_ms(0x80000000u) == telemetry_contract::kUnknownAgeMs);
    assert(!validity.imu_valid(0x80000000u));

    validity.reset();
    assert(validity.altitude_age_ms(0) == telemetry_contract::kUnknownAgeMs);
    return 0;
}
