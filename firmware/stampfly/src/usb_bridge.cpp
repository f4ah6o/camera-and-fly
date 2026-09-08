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

namespace {
constexpr size_t kLineCapacity = 192;
// A 20 Hz SET is shorter than this budget.  Limiting work per 400 Hz poll
// leaves the stabilization loop a bounded path even when the host floods USB.
constexpr size_t kMaxBytesPerPoll = 96;
// Replies are optional acknowledgements.  Never wait for a host to drain USB;
// a dropped reply becomes a host-side timeout/fault and the firmware watchdog
// remains the final control safety boundary.
constexpr size_t kTxCapacity = 256;

cf1::Session g_session;
bool g_discarding_line = false;
char g_line[kLineCapacity];
size_t g_line_len = 0;
uint32_t g_tx_dropped = 0;

bool tx_bytes(const char* bytes, size_t length) {
    if (bytes == nullptr || length == 0 || length > kTxCapacity) return false;
    const int available = USBSerial.availableForWrite();
    if (available < 0 || static_cast<size_t>(available) < length) {
        ++g_tx_dropped;
        return false;
    }
    if (USBSerial.write(reinterpret_cast<const uint8_t*>(bytes), length) != length) {
        ++g_tx_dropped;
        return false;
    }
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
    tx_format(
        "CF1 STATUS claimed=%u armed=%u connected=%u mode=%u voltage=%.3f roll=%.3f pitch=%.3f yaw=%.3f altitude=%.3f range=%d safe_test=%u\r\n",
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
#ifdef CAMFLY_SAFE_TEST
        1U
#else
        0U
#endif
    );
}

void parse_error(const cf1::ParseResult& parsed) {
    tx_format("CF1 ERR PARSE %s\r\n", cf1::error_name(parsed.error));
}

void handle_line(const char* line) {
    const cf1::ParseResult parsed = cf1::parse_line(line, strlen(line));
    if (!parsed.ok()) {
        parse_error(parsed);
        return;
    }

    const uint32_t now = millis();
    switch (parsed.request.command) {
        case cf1::Command::HELLO:
            tx_literal("CF1 HELLO stampfly-camfly/2\r\n");
            return;
        case cf1::Command::CLAIM:
            g_session.claim();
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
                USBSerial.printf("CF1 ERR %lu %s\r\n", static_cast<unsigned long>(setpoint.sequence),
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
    const uint32_t now = millis();
    if (g_session.claimed() && g_session.armed() && g_session.watchdog_expired(now)) {
        disarm_now("watchdog", true);
    }
}
}  // namespace

void usb_bridge_init() {
    g_session.reset();
    g_line_len = 0;
    g_discarding_line = false;
    g_tx_dropped = 0;
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
    enforce_watchdog();
    size_t processed = 0;
    while (USBSerial.available() > 0 && processed < kMaxBytesPerPoll) {
        const int raw = USBSerial.read();
        if (raw < 0) break;
        ++processed;
        const char c = static_cast<char>(raw);
        if (c == '\r') continue;
        if (c == '\n') {
            if (g_discarding_line) {
                tx_literal("CF1 ERR LINE_TOO_LONG\r\n");
            } else if (g_line_len > 0) {
                g_line[g_line_len] = '\0';
                handle_line(g_line);
            }
            g_line_len = 0;
            g_discarding_line = false;
            continue;
        }
        if (g_discarding_line) continue;
        if (g_line_len + 1 >= kLineCapacity) {
            g_line_len = 0;
            g_discarding_line = true;
            continue;
        }
        g_line[g_line_len++] = c;
    }

    enforce_watchdog();
}
