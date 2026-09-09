#include "usb_bridge.hpp"

#include <Arduino.h>
#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "cf1_protocol.hpp"
#include "flight_control.hpp"
#include "rc.hpp"
#include "sensor.hpp"
#include "tof.hpp"

namespace {
// A 20 Hz SET is shorter than this budget.  Limiting work per 400 Hz poll
// leaves the stabilization loop a bounded path even when the host floods USB.
constexpr size_t kMaxBytesPerPoll = 96;
// STATUS is larger than the USB-JTAG FIFO.  Defer one complete reply when the
// non-blocking TX ring cannot accept it yet; never wait for the host to drain
// USB.  A second reply while one is pending is still dropped and reported.
constexpr size_t kTxCapacity = 1024;

cf1::Session g_session;
cf1::LineCollector g_line;
uint32_t g_tx_dropped = 0;
uint32_t g_tx_deferred = 0;
char g_pending_tx[kTxCapacity];
size_t g_pending_tx_length = 0;
size_t g_pending_tx_offset = 0;
uint32_t g_usb_poll_count = 0;
uint32_t g_usb_command_count = 0;
uint32_t g_watchdog_check_count = 0;
uint32_t g_watchdog_disarm_count = 0;

bool flush_pending_tx() {
    if (g_pending_tx_length == 0) return true;

    const int available = USBSerial.availableForWrite();
    if (available <= 0) return false;

    const size_t remaining = g_pending_tx_length - g_pending_tx_offset;
    const size_t chunk = remaining < static_cast<size_t>(available)
                             ? remaining
                             : static_cast<size_t>(available);
    const size_t written = USBSerial.write(
        reinterpret_cast<const uint8_t*>(g_pending_tx + g_pending_tx_offset), chunk);
    if (written == 0) return false;

    g_pending_tx_offset += written;
    if (g_pending_tx_offset >= g_pending_tx_length) {
        g_pending_tx_length = 0;
        g_pending_tx_offset = 0;
        return true;
    }
    return false;
}

bool tx_bytes(const char* bytes, size_t length) {
    if (bytes == nullptr || length == 0 || length > kTxCapacity) return false;

    if (!flush_pending_tx()) {
        ++g_tx_dropped;
        return false;
    }

    memcpy(g_pending_tx, bytes, length);
    g_pending_tx_length = length;
    g_pending_tx_offset = 0;
    if (!flush_pending_tx()) ++g_tx_deferred;
    return true;
}

void tx_literal(const char* text) {
    if (text == nullptr) return;
    tx_bytes(text, strlen(text));
}

void tx_format(const char* format, ...) {
    char buffer[kTxCapacity];
    va_list arguments;
    va_start(arguments, format);
    const int length = vsnprintf(buffer, sizeof(buffer), format, arguments);
    va_end(arguments);
    if (length <= 0 || static_cast<size_t>(length) >= sizeof(buffer)) {
        ++g_tx_dropped;
        return;
    }
    tx_bytes(buffer, static_cast<size_t>(length));
}

void zero_controls() {
    Stick[RUDDER] = 0.0f;
    Stick[ELEVATOR] = 0.0f;
    Stick[THROTTLE] = 0.0f;
    Stick[AILERON] = 0.0f;
    Stick[BUTTON_ARM] = 0.0f;
    Stick[BUTTON_FLIP] = 0.0f;
}

void disarm_now(const char* reason, bool report) {
    const bool was_active = g_session.armed() || Mode == FLIGHT_MODE || Mode == FLIP_MODE || Mode == AUTO_LANDING_MODE;
    zero_controls();
    g_session.disarm();
    if (was_active && Mode > AVERAGE_MODE) Mode = PARKING_MODE;
    if (report) tx_format("CF1 EVENT DISARM %s\r\n", reason);
}

void status_reply() {
    const uint32_t now = millis();
    const telemetry_contract::Validity& validity = telemetry_validity();
    const ToFDiagnostics tof = tof_bottom_diagnostics();
    tx_format(
        "CF1 STATUS claimed=%u armed=%u connected=%u mode=%u voltage=%.3f roll=%.3f pitch=%.3f yaw=%.3f "
        "altitude=%.3f range=%d altitude_m=%.3f altitude_valid=%u altitude_age_ms=%lu "
        "altitude_source=tof_imu range_mm=%d range_valid=%u range_age_ms=%lu range_source=tof_bottom "
        "imu_valid=%u imu_age_ms=%lu imu_source=bmi270 capabilities=telemetry_validity_v1 safe_test=%u "
        "status_millis=%lu loop_count=%lu loop_period_us=%lu sensor_us=%lu sensor_max_us=%lu "
        "usb_poll_count=%lu usb_command_count=%lu tx_dropped=%lu tx_deferred=%lu tx_pending=%u watchdog_checks=%lu watchdog_disarm_count=%lu "
        "tof_raw_mm=%d tof_ready_status=%d tof_ready=%u tof_raw_stream_status=%d tof_raw_stream=%u "
        "tof_get_status=%d tof_restart_status=%d tof_init_clear_status=%d tof_init_start_status=%d tof_objects=%u "
        "tof_range_status=%u tof_stream_count=%u tof_data_ready_count=%lu\r\n",
        g_session.claimed() ? 1U : 0U,
        g_session.armed() ? 1U : 0U,
        g_session.connected(now) ? 1U : 0U,
        static_cast<unsigned>(Mode),
        static_cast<double>(Voltage),
        static_cast<double>((Roll_angle - Roll_angle_offset) * 180.0f / PI),
        static_cast<double>((Pitch_angle - Pitch_angle_offset) * 180.0f / PI),
        static_cast<double>((Yaw_angle - Yaw_angle_offset) * 180.0f / PI),
        static_cast<double>(Altitude),
        static_cast<int>(Range),
        static_cast<double>(Altitude),
        validity.altitude_valid(now) ? 1U : 0U,
        static_cast<unsigned long>(validity.altitude_age_ms(now)),
        static_cast<int>(Range),
        validity.range_valid(now) ? 1U : 0U,
        static_cast<unsigned long>(validity.range_age_ms(now)),
        validity.imu_valid(now) ? 1U : 0U,
        static_cast<unsigned long>(validity.imu_age_ms(now)),
#ifdef CAMFLY_SAFE_TEST
        1U,
#else
        0U,
#endif
        static_cast<unsigned long>(now),
        static_cast<unsigned long>(camfly_loop_count),
        static_cast<unsigned long>(camfly_last_loop_period_us),
        static_cast<unsigned long>(camfly_last_sensor_us),
        static_cast<unsigned long>(camfly_max_sensor_us),
        static_cast<unsigned long>(g_usb_poll_count),
        static_cast<unsigned long>(g_usb_command_count),
        static_cast<unsigned long>(g_tx_dropped),
        static_cast<unsigned long>(g_tx_deferred),
        g_pending_tx_length != 0 ? 1U : 0U,
        static_cast<unsigned long>(g_watchdog_check_count),
        static_cast<unsigned long>(g_watchdog_disarm_count),
        static_cast<int>(tof.raw_range_mm),
        static_cast<int>(tof.data_ready_status),
        static_cast<unsigned>(tof.data_ready),
        static_cast<int>(tof.raw_stream_status),
        static_cast<unsigned>(tof.raw_stream_count),
        static_cast<int>(tof.get_status),
        static_cast<int>(tof.restart_status),
        static_cast<int>(tof.init_clear_status),
        static_cast<int>(tof.init_start_status),
        static_cast<unsigned>(tof.object_count),
        static_cast<unsigned>(tof.range_status),
        static_cast<unsigned>(tof.stream_count),
        static_cast<unsigned long>(tof.data_ready_count)
    );
}

void parse_error(const cf1::ParseResult& parsed) {
    tx_format("CF1 ERR PARSE %s\r\n", cf1::error_name(parsed.error));
}

void handle_line(const char* line, size_t length) {
    const cf1::ParseResult parsed = cf1::parse_line(line, length);
    if (!parsed.ok()) {
        parse_error(parsed);
        return;
    }

    ++g_usb_command_count;
    const uint32_t now = millis();
    switch (parsed.request.command) {
        case cf1::Command::HELLO:
            tx_literal("CF1 HELLO stampfly-camfly/3 telemetry_validity_v1\r\n");
            return;
        case cf1::Command::CLAIM:
            g_session.claim();
            telemetry_validity_reset();
            disarm_now("claim", false);
            tx_literal("CF1 OK CLAIM\r\n");
            return;
        case cf1::Command::RELEASE:
            disarm_now("release", false);
            g_session.release();
            tx_literal("CF1 OK RELEASE\r\n");
            return;
        case cf1::Command::DISARM:
            disarm_now("command", false);
            tx_literal("CF1 OK DISARM\r\n");
            return;
        case cf1::Command::STATUS:
            status_reply();
            return;
        case cf1::Command::SET: {
            const cf1::Setpoint& setpoint = parsed.request.setpoint;
            const cf1::Error error = g_session.accept_set(setpoint, now);
            if (error != cf1::Error::NONE) {
                tx_format("CF1 ERR %lu %s\r\n", static_cast<unsigned long>(setpoint.sequence),
                          cf1::error_name(error));
                return;
            }
            Stick[AILERON] = setpoint.roll;
            Stick[ELEVATOR] = setpoint.pitch;
            Stick[RUDDER] = setpoint.yaw;
            Stick[THROTTLE] = setpoint.throttle;
            Stick[CONTROLMODE] = setpoint.control_mode == RATECONTROL ? RATECONTROL : ANGLECONTROL;
            Stick[ALTCONTROLMODE] = setpoint.alt_mode == AUTO_ALT ? AUTO_ALT : MANUAL_ALT;
            Stick[BUTTON_FLIP] = 0.0f;
            tx_format("CF1 OK %lu\r\n", static_cast<unsigned long>(setpoint.sequence));
            return;
        }
        case cf1::Command::ARM: {
            if (Mode != PARKING_MODE) {
                tx_format("CF1 ERR ARM MODE_%u\r\n", static_cast<unsigned>(Mode));
                return;
            }
            const cf1::Error error = g_session.arm(now, Stick[THROTTLE]);
            if (error != cf1::Error::NONE) {
                tx_format("CF1 ERR ARM %s\r\n", cf1::error_name(error));
                return;
            }
            Stick[BUTTON_ARM] = 1.0f;
            tx_literal("CF1 OK ARM\r\n");
            return;
        }
        case cf1::Command::UNKNOWN:
            break;
    }
    tx_literal("CF1 ERR UNKNOWN\r\n");
}

void enforce_watchdog() {
    ++g_watchdog_check_count;
    const uint32_t now = millis();
    if (g_session.claimed() && g_session.armed() && g_session.watchdog_expired(now)) {
        ++g_watchdog_disarm_count;
        disarm_now("watchdog", true);
    }
}
}  // namespace

void usb_bridge_init() {
    g_session.reset();
    g_line.reset();
    g_tx_dropped = 0;
    g_tx_deferred = 0;
    g_pending_tx_length = 0;
    g_pending_tx_offset = 0;
    g_usb_poll_count = 0;
    g_usb_command_count = 0;
    g_watchdog_check_count = 0;
    g_watchdog_disarm_count = 0;
    zero_controls();
    tx_literal("CF1 READY\r\n");
}

bool usb_bridge_claimed() {
    return g_session.claimed();
}

bool usb_bridge_connected() {
    return g_session.connected(millis());
}

void usb_bridge_poll() {
    ++g_usb_poll_count;
    enforce_watchdog();
    flush_pending_tx();
    size_t processed = 0;
    while (USBSerial.available() > 0 && processed < kMaxBytesPerPoll) {
        const int raw = USBSerial.read();
        if (raw < 0) break;
        ++processed;
        const cf1::LineFeedResult result = g_line.feed(static_cast<char>(raw));
        if (result == cf1::LineFeedResult::LINE_TOO_LONG) {
            tx_literal("CF1 ERR LINE_TOO_LONG\r\n");
        } else if (result == cf1::LineFeedResult::LINE_READY) {
            handle_line(g_line.line(), g_line.length());
        }
    }

    enforce_watchdog();
    flush_pending_tx();
}
