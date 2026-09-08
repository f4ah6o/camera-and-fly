/*
 * MIT License
 *
 * Copyright (c) 2024 Kouhei Ito
 * Copyright (c) 2024 M5Stack
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in all
 * copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 * SOFTWARE.
 */

#include "rc.hpp"
#include "usb_bridge.hpp"
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include "flight_control.hpp"
#include "legacy_rc_protocol.hpp"

// esp_now_peer_info_t slave;

volatile uint16_t Connect_flag = 0;

// The telemetry peer is learned at runtime. Do not hard-code device MACs here.
uint8_t TelemAddr[6] = {0};
// uint8_t TelemAddr[6] = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff};
volatile uint8_t MyMacAddr[6];
volatile uint8_t peer_command[4] = {0xaa, 0x55, 0x16, 0x88};
volatile uint8_t Rc_err_flag     = 0;
esp_now_peer_info_t peerInfo;

// RC
volatile float Stick[16];
volatile uint8_t Recv_MAC[3];

void on_esp_now_sent(const uint8_t *mac_addr, esp_now_send_status_t status);

// 受信コールバック
void OnDataRecv(const uint8_t *mac_addr, const uint8_t *recv_data, int data_len) {
    if (usb_bridge_claimed()) return;

    const uint8_t target_tail[3] = {MyMacAddr[3], MyMacAddr[4], MyMacAddr[5]};
    legacy_rc::Packet packet;
    const legacy_rc::Error parse_error =
        legacy_rc::parse(recv_data, data_len < 0 ? 0U : static_cast<size_t>(data_len), target_tail, &packet);
    if (parse_error != legacy_rc::Error::NONE) {
        Rc_err_flag = 1;
        return;
    }

    // Only a fully validated packet refreshes receiver freshness or teaches
    // the telemetry peer. Short, malformed, wrong-target, and bad-checksum
    // packets cannot keep the legacy radio link alive.
    Connect_flag = 0;
    Rc_err_flag = 0;

    if (!TelemAddr[0] && !TelemAddr[1] && !TelemAddr[2] && !TelemAddr[3] && !TelemAddr[4] && !TelemAddr[5]) {
        memcpy(TelemAddr, mac_addr, 6);
        memcpy(peerInfo.peer_addr, TelemAddr, 6);
        peerInfo.channel = CHANNEL;
        peerInfo.encrypt = false;
        if (esp_now_add_peer(&peerInfo) != ESP_OK) {
            USBSerial.println("Failed to add peer2");
            memset(TelemAddr, 0, 6);
        } else {
            esp_now_register_send_cb(on_esp_now_sent);
        }
    }

    Recv_MAC[0] = recv_data[0];
    Recv_MAC[1] = recv_data[1];
    Recv_MAC[2] = recv_data[2];

    Stick[RUDDER] = packet.rudder;
    Stick[THROTTLE] = packet.throttle;
    Stick[AILERON] = packet.aileron;
    Stick[ELEVATOR] = packet.elevator;
    Stick[BUTTON_ARM] = packet.button_arm;
    Stick[BUTTON_FLIP] = packet.button_flip;
    Stick[CONTROLMODE] = packet.control_mode;
    Stick[ALTCONTROLMODE] = packet.alt_mode;
    ahrs_reset_flag = packet.ahrs_reset;
    Stick[LOG] = 0.0;
}

// 送信コールバック
uint8_t esp_now_send_status;
void on_esp_now_sent(const uint8_t *mac_addr, esp_now_send_status_t status) {
    esp_now_send_status = status;
}

void rc_init(void) {
    // Initialize Stick list
    for (uint8_t i = 0; i < 16; i++) Stick[i] = 0.0;

    // ESP-NOW初期化
    WiFi.mode(WIFI_STA);
    WiFi.disconnect();

    WiFi.macAddress((uint8_t *)MyMacAddr);
    USBSerial.printf("MAC ADDRESS: %02X:%02X:%02X:%02X:%02X:%02X\r\n", MyMacAddr[0], MyMacAddr[1], MyMacAddr[2],
                     MyMacAddr[3], MyMacAddr[4], MyMacAddr[5]);

    if (esp_now_init() == ESP_OK) {
        USBSerial.println("ESPNow Init Success");
    } else {
        USBSerial.println("ESPNow Init Failed");
        ESP.restart();
    }

    // MACアドレスブロードキャスト
    uint8_t addr[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
    memcpy(peerInfo.peer_addr, addr, 6);
    peerInfo.channel = CHANNEL;
    peerInfo.encrypt = false;
    if (esp_now_add_peer(&peerInfo) != ESP_OK) {
        USBSerial.println("Failed to add peer");
        return;
    }
    esp_wifi_set_channel(CHANNEL, WIFI_SECOND_CHAN_NONE);

    // Send my MAC address
    for (uint16_t i = 0; i < 50; i++) {
        send_peer_info();
        delay(50);
        USBSerial.printf("%d\n", i);
    }

    // ESP-NOW再初期化
    WiFi.mode(WIFI_STA);
    WiFi.disconnect();
    if (esp_now_init() == ESP_OK) {
        USBSerial.println("ESPNow Init Success2");
    } else {
        USBSerial.println("ESPNow Init Failed2");
        ESP.restart();
    }

    // ESP-NOWコールバック登録
    esp_now_register_recv_cb(OnDataRecv);
    USBSerial.println("ESP-NOW Ready.");
}

void send_peer_info(void) {
    uint8_t data[11];
    data[0] = CHANNEL;
    memcpy(&data[1], (uint8_t *)MyMacAddr, 6);
    memcpy(&data[1 + 6], (uint8_t *)peer_command, 4);
    esp_now_send(peerInfo.peer_addr, data, 11);
}

uint8_t telemetry_send(uint8_t *data, uint16_t datalen) {
    static uint32_t cnt       = 0;
    static uint8_t error_flag = 0;
    static uint8_t state      = 0;

    esp_err_t result;

    if ((error_flag == 0) && (state == 0)) {
        result = esp_now_send(peerInfo.peer_addr, data, datalen);
        cnt    = 0;
    } else
        cnt++;

    if (esp_now_send_status == 0) {
        error_flag = 0;
        // state = 0;
    } else {
        error_flag = 1;
        // state = 1;
    }
    // 一度送信エラーを検知してもしばらくしたら復帰する
    if (cnt > 500) {
        error_flag = 0;
        cnt        = 0;
    }
    cnt++;
    // USBSerial.printf("%6d %d %d\r\n", cnt, error_flag, esp_now_send_status);

    return error_flag;
}

void rc_end(void) {
    // Ps3.end();
}

uint8_t rc_isconnected(void) {
    if (usb_bridge_claimed()) {
        return usb_bridge_connected() ? 1 : 0;
    }
    bool status;
    Connect_flag++;
    if (Connect_flag < 40)
        status = 1;
    else
        status = 0;
    // USBSerial.printf("%d \n\r", Connect_flag);
    return status;
}

void rc_demo() {
}
