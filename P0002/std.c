/*
 * P0002 reference solution — sensor fusion + UART batch I/O (bare-metal OJ).
 * No printf/stdio: avoids newlib float printf stack and .bss bloat on 8KB RAM.
 * No strtof/strtol: avoids newlib strtod/locale/__malloc_av_ and deep call stacks.
 */
#include <math.h>
#include <stddef.h>
#include <stdint.h>

#include "uart_oj_rx_poll.h"

/* Quiet NaN / Inf without nanf()/INFINITY to avoid pulling newlib strtod/locale. */
static float f32_nan(void)
{
    union {
        uint32_t u;
        float f;
    } x;

    x.u = 0x7fc00000u;
    return x.f;
}

static float f32_inf(void)
{
    union {
        uint32_t u;
        float f;
    } x;

    x.u = 0x7f800000u;
    return x.f;
}

static uint32_t f32_u32(float x)
{
    union {
        uint32_t u;
        float f;
    } v;

    v.f = x;
    return v.u;
}

static int f32_is_nan(float x)
{
    uint32_t u = f32_u32(x);
    uint32_t exp = (u >> 23) & 255u;
    uint32_t frac = u & 0x7fffffu;

    return ((exp == 255u) && (frac != 0u)) ? 1 : 0;
}

static int f32_is_inf(float x)
{
    uint32_t u = f32_u32(x);
    uint32_t exp = (u >> 23) & 255u;
    uint32_t frac = u & 0x7fffffu;

    return ((exp == 255u) && (frac == 0u)) ? 1 : 0;
}

static float oj_fabsf(float x)
{
    return (x < 0.0f) ? (-x) : x;
}

#define DT_S (0.01f)
#define ALPHA_G (0.98f)
#define BETA_A (0.02f)
#define ACCEL_LIM_G (2.0f)
#define GYRO_LIM_DPS (250.0f)
#define CONSECUTIVE_BAD_LIMIT (5u)
#define PI_F (3.14159265f)
#define RAD_TO_DEG ((180.0f) / PI_F)
#define LINE_CAP (160u)
#define EPS_RMSE (1e-6f)

extern void uart_write_byte(uint8_t ch);

volatile float accel_data[3];
volatile float gyro_data[3];
volatile float pitch;
volatile float roll;

static float s_pitch_filt;
static float s_roll_filt;
static uint32_t s_consecutive_bad;

static uint8_t s_step_applied;

static char s_line[LINE_CAP];
static volatile uint16_t s_line_len;
static volatile uint8_t s_line_ready;

static void uart_print_cstr(const char *s)
{
    const char *p = s;

    while ((*p) != '\0') {
        uart_write_byte((uint8_t)*p);
        p++;
    }
}

static void uart_print_u32(uint32_t v)
{
    char buf[12];
    uint8_t n = 0u;
    uint8_t i;

    if (v == 0u) {
        uart_write_byte((uint8_t)'0');
        return;
    }
    while (v > 0u) {
        buf[n] = (char)('0' + (v % 10u));
        v /= 10u;
        n++;
    }
    for (i = n; i > (uint8_t)0; i--) {
        uart_write_byte((uint8_t)buf[i - 1u]);
    }
}

/* Non-negative values; RMSE outputs are >= 0. */
static void uart_print_float_3(float x)
{
    float ax = x;
    float scaled;
    uint32_t u;

    if (ax < 0.0f) {
        ax = -ax;
    }
    scaled = (ax * 1000.0f) + 0.5f;
    if ((scaled >= 4294967000.0f) || (scaled != scaled)) {
        scaled = 0.0f;
    }
    u = (uint32_t)scaled;
    uart_print_u32(u / 1000u);
    uart_write_byte((uint8_t)'.');
    u = u % 1000u;
    uart_write_byte((uint8_t)('0' + (u / 100u) % 10u));
    uart_write_byte((uint8_t)('0' + (u / 10u) % 10u));
    uart_write_byte((uint8_t)('0' + u % 10u));
}

static void uart_print_float_1(float x)
{
    float ax = x;

    if (ax < 0.0f) {
        uart_write_byte((uint8_t)'-');
        ax = -ax;
    }
    {
        uint32_t u = (uint32_t)(ax * 10.0f + 0.5f);

        uart_print_u32(u / 10u);
        uart_write_byte((uint8_t)'.');
        uart_write_byte((uint8_t)('0' + (u % 10u)));
    }
}

static int is_data_valid(const float *accel, const float *gyro)
{
    uint8_t i;

    if ((accel == NULL) || (gyro == NULL)) {
        return 0;
    }

    for (i = 0u; i < 3u; i++) {
        float a = accel[i];
        float g = gyro[i];

        if ((f32_is_nan(a) != 0) || (f32_is_inf(a) != 0)) {
            return 0;
        }
        if ((f32_is_nan(g) != 0) || (f32_is_inf(g) != 0)) {
            return 0;
        }
    }

    for (i = 0u; i < 3u; i++) {
        float af = accel[i];
        if ((af > ACCEL_LIM_G) || (af < (-ACCEL_LIM_G))) {
            return 0;
        }
    }

    for (i = 0u; i < 3u; i++) {
        float gf = gyro[i];
        if ((gf > GYRO_LIM_DPS) || (gf < (-GYRO_LIM_DPS))) {
            return 0;
        }
    }

    return 1;
}

void fusion_init(void)
{
    s_pitch_filt = 0.0f;
    s_roll_filt = 0.0f;
    pitch = 0.0f;
    roll = 0.0f;
    s_consecutive_bad = 0u;
}

void fusion_update(void)
{
    float ax;
    float ay;
    float az;
    float gx;
    float gy;
    float gz;
    float pitch_acc;
    float roll_acc;
    float ay2;
    float az2;
    float denom;

    float alocal[3];
    float glocal[3];

    s_step_applied = 0u;

    alocal[0] = accel_data[0];
    alocal[1] = accel_data[1];
    alocal[2] = accel_data[2];
    glocal[0] = gyro_data[0];
    glocal[1] = gyro_data[1];
    glocal[2] = gyro_data[2];

    if (is_data_valid(alocal, glocal) == 0) {
        s_consecutive_bad++;
        if (s_consecutive_bad >= CONSECUTIVE_BAD_LIMIT) {
            fusion_init();
        }
        return;
    }

    s_consecutive_bad = 0u;

    ax = alocal[0];
    ay = alocal[1];
    az = alocal[2];
    gx = glocal[0];
    gy = glocal[1];
    gz = glocal[2];
    (void)gz;

    ay2 = ay * ay;
    az2 = az * az;
    denom = sqrtf(ay2 + az2);
    pitch_acc = atan2f(-ax, denom) * RAD_TO_DEG;
    roll_acc = atan2f(ay, az) * RAD_TO_DEG;

    s_pitch_filt = (ALPHA_G * (s_pitch_filt + (gx * DT_S))) + (BETA_A * pitch_acc);
    s_roll_filt = (ALPHA_G * (s_roll_filt + (gy * DT_S))) + (BETA_A * roll_acc);

    pitch = s_pitch_filt;
    roll = s_roll_filt;
    s_step_applied = 1u;
}

void UART_IRQHandler(void)
{
    uint8_t b = UART_ReceiveByte();

    if (b == (uint8_t)'\r') {
        return;
    }

    if (b == (uint8_t)'\n') {
        if (s_line_len < (LINE_CAP - 1u)) {
            s_line[s_line_len] = '\0';
        } else {
            s_line[LINE_CAP - 1u] = '\0';
        }
        s_line_ready = 1u;
        return;
    }

    if (s_line_len < (LINE_CAP - 1u)) {
        s_line[s_line_len] = (char)b;
        s_line_len++;
    }
}

static void line_reader_reset(void)
{
    s_line_len = 0u;
    s_line_ready = 0u;
}

static void read_line_blocking(void)
{
    line_reader_reset();
    while (s_line_ready == 0u) {
        oj_uart_poll_step();
    }
}

static const char *skip_ws(const char *p)
{
    while (((*p) == ' ') || (*p == '\t') || (*p == '\r')) {
        p++;
    }
    return p;
}

static int match_lc(const char *p, const char *lower_word)
{
    const char *w = lower_word;

    while (*w != '\0') {
        char c = *p;
        if ((c >= 'A') && (c <= 'Z')) {
            c = (char)(c + 32);
        }
        if (c != *w) {
            return 0;
        }
        p++;
        w++;
    }
    return 1;
}

/* Parse one float; advances *pp past the token. Returns 0 on success. */
static int parse_float_token(const char **pp, float *out)
{
    const char *p = skip_ws(*pp);
    uint8_t neg = 0u;
    float val;
    uint8_t has_digit = 0u;
    float intpart = 0.0f;

    if (*p == '+') {
        p++;
    } else if (*p == '-') {
        neg = 1u;
        p++;
    }

    if (match_lc(p, "nan")) {
        p += 3;
        *out = f32_nan();
        *pp = p;
        return 0;
    }

    if (match_lc(p, "infinity")) {
        p += 8;
        val = f32_inf();
        *out = neg ? (-val) : val;
        *pp = p;
        return 0;
    }
    if (match_lc(p, "inf")) {
        p += 3;
        val = f32_inf();
        *out = neg ? (-val) : val;
        *pp = p;
        return 0;
    }

    while (((*p) >= '0') && ((*p) <= '9')) {
        intpart = (intpart * 10.0f) + (float)((*p) - '0');
        has_digit = 1u;
        p++;
    }

    if (*p == '.') {
        float scale = 0.1f;
        p++;
        while (((*p) >= '0') && ((*p) <= '9')) {
            intpart += ((float)((*p) - '0')) * scale;
            scale *= 0.1f;
            has_digit = 1u;
            p++;
        }
    }

    if (has_digit == 0u) {
        return -1;
    }

    val = intpart;
    *out = neg ? (-val) : val;
    *pp = p;
    return 0;
}

/* First line: non-negative integer N, optional surrounding whitespace only. */
static int parse_nonneg_int_line(const char *s, int32_t *out_n)
{
    const char *p = skip_ws(s);
    uint32_t v = 0u;
    uint8_t any = 0u;

    if (out_n == NULL) {
        return -1;
    }

    while (((*p) >= '0') && ((*p) <= '9')) {
        uint8_t d = (uint8_t)((*p) - '0');
        if (v > (2147483647u - (uint32_t)d) / 10u) {
            return -1;
        }
        v = (v * 10u) + (uint32_t)d;
        any = 1u;
        p++;
    }

    if (any == 0u) {
        return -1;
    }

    p = skip_ws(p);
    if (*p != '\0') {
        return -1;
    }

    *out_n = (int32_t)v;
    return 0;
}

static int parse_eight_floats(const char *s, float *o0, float *o1, float *o2, float *o3, float *o4,
                              float *o5, float *o6, float *o7)
{
    const char *p = s;
    float *outs[8];
    uint8_t k;

    outs[0] = o0;
    outs[1] = o1;
    outs[2] = o2;
    outs[3] = o3;
    outs[4] = o4;
    outs[5] = o5;
    outs[6] = o6;
    outs[7] = o7;

    for (k = 0u; k < 8u; k++) {
        if (parse_float_token(&p, outs[k]) != 0) {
            return -1;
        }
    }

    p = skip_ws(p);
    if (*p != '\0') {
        return -1;
    }
    return 0;
}

static float rmse_from_sums(float sum_sq_err, uint32_t cnt)
{
    float m;

    if (cnt == 0u) {
        return 0.0f;
    }
    m = sum_sq_err / (float)cnt;
    return sqrtf(m);
}

int main(void)
{
    int32_t n;
    int32_t i;
    uint32_t drop_count;
    float sum_sim;
    float sum_hw;
    uint32_t valid_cnt;
    float rmse_sim;
    float rmse_hw;
    float diff_pct;
    const char *spec_str;

    fusion_init();
    drop_count = 0u;
    sum_sim = 0.0f;
    sum_hw = 0.0f;
    valid_cnt = 0u;

    read_line_blocking();
    if (parse_nonneg_int_line(s_line, &n) != 0) {
        return 1;
    }

    for (i = 0; i < n; i++) {
        float ax;
        float ay;
        float az;
        float gx;
        float gy;
        float gz;
        float ref_p;
        float ref_r;
        float pa;
        float ra;
        float ay2;
        float az2;
        float d;
        float ep;
        float er;

        read_line_blocking();

        if (parse_eight_floats(s_line, &ax, &ay, &az, &gx, &gy, &gz, &ref_p, &ref_r) != 0) {
            return 1;
        }

        accel_data[0] = ax;
        accel_data[1] = ay;
        accel_data[2] = az;
        gyro_data[0] = gx;
        gyro_data[1] = gy;
        gyro_data[2] = gz;

        fusion_update();

        if (s_step_applied == 0u) {
            drop_count++;
            continue;
        }

        ay2 = ay * ay;
        az2 = az * az;
        d = sqrtf(ay2 + az2);
        pa = atan2f(-ax, d) * RAD_TO_DEG;
        ra = atan2f(ay, az) * RAD_TO_DEG;

        ep = pitch - ref_p;
        er = roll - ref_r;
        sum_sim += (ep * ep) + (er * er);

        ep = pa - ref_p;
        er = ra - ref_r;
        sum_hw += (ep * ep) + (er * er);

        valid_cnt++;
    }

    rmse_sim = rmse_from_sums(sum_sim, valid_cnt);
    rmse_hw = rmse_from_sums(sum_hw, valid_cnt);

    if (n > 0) {
        float a = oj_fabsf(rmse_sim - rmse_hw);
        float mx = rmse_sim;

        if (rmse_hw > mx) {
            mx = rmse_hw;
        }
        if (mx < EPS_RMSE) {
            mx = EPS_RMSE;
        }
        diff_pct = (a / mx) * 100.0f;
    } else {
        diff_pct = 0.0f;
    }

    if (diff_pct <= 10.0f) {
        spec_str = "within spec";
    } else {
        spec_str = "not within spec";
    }

    uart_print_cstr("[FUSION] RMSE_sim: ");
    uart_print_float_3(rmse_sim);
    uart_print_cstr(" deg\n");

    uart_print_cstr("[FUSION] RMSE_hw: ");
    uart_print_float_3(rmse_hw);
    uart_print_cstr(" deg\n");

    uart_print_cstr("[FUSION] Diff: ");
    uart_print_float_1(diff_pct);
    uart_print_cstr("% (");
    uart_print_cstr(spec_str);
    uart_print_cstr(")\n");

    uart_print_cstr("[FUSION] Abnormal drops: ");
    uart_print_u32(drop_count);
    uart_print_cstr(" times (");
    if (n > 0) {
        float pct = (100.0f * (float)drop_count) / (float)n;
        uart_print_float_1(pct);
    } else {
        uart_print_cstr("0.0");
    }
    uart_print_cstr("% of total)\n");

    return 0;
}
