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

#ifndef TOF_HPP
#define TOF_HPP

#include <stdint.h>
#include <vl53lx_api.h>
#include <vl53lx_platform.h>

#define INT_BOTTOM   6
#define XSHUT_BOTTOM 7
#define INT_FRONT    8
#define XSHUT_FRONT  9
#define USER_A       0

void tof_init(void);
int16_t tof_range_get(VL53LX_DEV dev);
void tof_test_ranging(VL53LX_DEV dev);
int16_t tof_bottom_get_range();
int16_t tof_front_get_range();

struct ToFDiagnostics {
    int16_t raw_range_mm = 0;
    int8_t data_ready_status = 0;
    uint8_t data_ready = 0;
    int8_t raw_stream_status = 0;
    uint8_t raw_stream_count = 0;
    int8_t get_status = 0;
    int8_t restart_status = 0;
    int8_t init_clear_status = 0;
    int8_t init_start_status = 0;
    uint8_t object_count = 0;
    uint8_t range_status = VL53LX_RANGESTATUS_NONE;
    uint8_t stream_count = 0;
    uint32_t data_ready_count = 0;
};

ToFDiagnostics tof_bottom_diagnostics();

#endif
