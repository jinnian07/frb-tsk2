/*
 * P0008 标程 — 低功耗采集与功耗报告（裸机 UART OJ）。
 * 实机可用 SysTick 1Hz + __WFI；QEMU 判题下无可靠 NVIC 唤醒时 __WFI 可能挂死，低功耗阶段与正常阶段同样用软件延时 + oj_uart_poll_step。
 */
#include <stdint.h>

#include "uart_oj_rx_poll.h"

extern void uart_write_byte(uint8_t ch);

#define SAMPLES_PER_PHASE 3u
/* 约数毫秒级间隔，保证首行 UART 在判题 idle 内到达且总时长可接受 */
#define OJ_SAMPLE_SPIN (12000u)

#define SIM_NORMAL_T 248 /* 24.8 mA，十分之一 mA */
#define SIM_LP_T 11 /* 1.1 mA */

static void uart_print_cstr(const char *s)
{
    while (*s != '\0') {
        uart_write_byte((uint8_t)*s);
        s++;
    }
}

static void uart_print_uint(uint32_t v)
{
    if (v >= 10u) {
        uart_print_uint(v / 10u);
    }
    uart_write_byte((uint8_t)('0' + (v % 10u)));
}

static void uart_print_ma_tenths(uint32_t tenths)
{
    uint32_t w = tenths / 10u;
    uint32_t f = tenths % 10u;

    uart_print_uint(w);
    uart_write_byte((uint8_t)'.');
    uart_write_byte((uint8_t)('0' + f));
}

static int32_t reduction_pct_x10(uint32_t hw_n_t, uint32_t hw_lp_t)
{
    if (hw_n_t == 0u) {
        return 0;
    }
    uint32_t d = hw_n_t - hw_lp_t;
    int32_t q = (int32_t)((d * 1000u) / hw_n_t);
    uint32_t r = (d * 1000u) % hw_n_t;
    if ((r * 2u) >= hw_n_t) {
        q += 1;
    }
    return q;
}

static int32_t diff_pct_x10(int32_t hw_t, int32_t sim_t)
{
    if (sim_t <= 0) {
        return 0;
    }
    int32_t d = hw_t - sim_t;
    uint32_t ad = (d < 0) ? (uint32_t)(-d) : (uint32_t)d;
    int32_t sign = (d < 0) ? -1 : 1;
    int32_t q = (int32_t)((ad * 1000u) / (uint32_t)sim_t);
    uint32_t r = (ad * 1000u) % (uint32_t)sim_t;
    if ((r * 2u) >= (uint32_t)sim_t) {
        q += 1;
    }
    return sign * q;
}

static void uart_print_pct_signed(int32_t q10)
{
    uint32_t u;

    if (q10 < 0) {
        uart_write_byte((uint8_t)'-');
        u = (uint32_t)(-q10);
    } else {
        uart_write_byte((uint8_t)'+');
        u = (uint32_t)q10;
    }
    uart_print_uint(u / 10u);
    uart_write_byte((uint8_t)'.');
    uart_write_byte((uint8_t)('0' + (u % 10u)));
    uart_write_byte((uint8_t)'%');
}

static void uart_print_temp_sample(uint32_t idx)
{
    uart_print_cstr("Temp: 25.");
    uart_write_byte((uint8_t)('1' + (char)(idx % 3u)));
    uart_print_cstr(" C\n");
}

static int32_t parse_ma_tenths(const char *buf, uint8_t len)
{
    int32_t v = 0;
    uint8_t i = 0u;
    uint8_t neg = 0u;

    while (i < len && (buf[i] == ' ' || buf[i] == '\t' || buf[i] == '\r')) {
        i++;
    }
    if (i < len && buf[i] == '-') {
        neg = 1u;
        i++;
    }
    while (i < len && buf[i] >= '0' && buf[i] <= '9') {
        v = v * 10 + (int32_t)(buf[i] - '0');
        i++;
    }
    if (i < len && buf[i] == '.') {
        i++;
        if (i < len && buf[i] >= '0' && buf[i] <= '9') {
            v = v * 10 + (int32_t)(buf[i] - '0');
            i++;
        } else {
            v *= 10;
        }
    } else {
        v *= 10;
    }
    if (neg != 0u) {
        v = -v;
    }
    return v;
}

#define LINE_CAP 40u

/* 与 P0002 相同：oj_uart_poll_step 同步调用 UART_IRQHandler，用全局行缓冲避免指针在 ISR 路径上的歧义 */
static char s_rx_line[LINE_CAP];
static uint8_t s_line_len;
static volatile uint8_t s_line_ready;

void UART_IRQHandler(void)
{
    uint8_t ch = UART_ReceiveByte();

    if (ch == (uint8_t)'\r') {
        return;
    }
    if (ch == (uint8_t)'\n') {
        if (s_line_len < (LINE_CAP - 1u)) {
            s_rx_line[s_line_len] = '\0';
        } else {
            s_rx_line[LINE_CAP - 1u] = '\0';
        }
        s_line_ready = 1u;
        return;
    }
    if (s_line_len < (LINE_CAP - 1u)) {
        s_rx_line[s_line_len] = (char)ch;
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

static void copy_rx_line_to(char *out, uint8_t cap)
{
    uint8_t i = 0u;

    while (i + 1u < cap && s_rx_line[i] != '\0') {
        out[i] = s_rx_line[i];
        i++;
    }
    out[i] = '\0';
}

static void wait_sample_normal(void)
{
    volatile uint32_t i;

    for (i = 0u; i < OJ_SAMPLE_SPIN; i++) {
        oj_uart_poll_step();
    }
}

static void wait_sample_low_power(void)
{
    volatile uint32_t i;

    for (i = 0u; i < OJ_SAMPLE_SPIN; i++) {
        oj_uart_poll_step();
        /* 实机可在此插入 __WFI；QEMU 无 NVIC 投递时 WFI 可能永不返回，判题路径与正常阶段同样忙等 */
    }
}

static void run_phase_normal(void)
{
    uint32_t k;

    for (k = 0u; k < SAMPLES_PER_PHASE; k++) {
        wait_sample_normal();
        uart_print_temp_sample(k);
    }
}

static void run_phase_low_power(void)
{
    uint32_t k;

    for (k = 0u; k < SAMPLES_PER_PHASE; k++) {
        wait_sample_low_power();
        uart_print_temp_sample(k);
    }
}

static void print_report(uint32_t hw_n_t, uint32_t hw_lp_t)
{
    int32_t red10 = reduction_pct_x10(hw_n_t, hw_lp_t);
    int32_t dn = diff_pct_x10((int32_t)hw_n_t, (int32_t)SIM_NORMAL_T);
    int32_t dl = diff_pct_x10((int32_t)hw_lp_t, (int32_t)SIM_LP_T);

    uart_print_cstr("==== Power Consumption Report ====\n");
    uart_print_cstr("Normal mode avg current: ");
    uart_print_ma_tenths(hw_n_t);
    uart_print_cstr(" mA\n");
    uart_print_cstr("Low power mode avg current: ");
    uart_print_ma_tenths(hw_lp_t);
    uart_print_cstr(" mA\n");
    uart_print_cstr("Reduction: ");
    if (red10 < 0) {
        uart_write_byte((uint8_t)'-');
        red10 = -red10;
    }
    uart_print_uint((uint32_t)red10 / 10u);
    uart_write_byte((uint8_t)'.');
    uart_write_byte((uint8_t)('0' + ((uint32_t)red10 % 10u)));
    uart_print_cstr("%\n");
    uart_print_cstr("Simulation vs Hardware difference: \n");
    uart_print_cstr("  Sim Normal: ");
    uart_print_ma_tenths((uint32_t)SIM_NORMAL_T);
    uart_print_cstr(" mA, HW Normal: ");
    uart_print_ma_tenths(hw_n_t);
    uart_print_cstr(" mA (diff ");
    uart_print_pct_signed(dn);
    uart_print_cstr(")\n");
    uart_print_cstr("  Sim LP: ");
    uart_print_ma_tenths((uint32_t)SIM_LP_T);
    uart_print_cstr(" mA, HW LP: ");
    uart_print_ma_tenths(hw_lp_t);
    uart_print_cstr(" mA (diff ");
    uart_print_pct_signed(dl);
    uart_print_cstr(")\n");
    uart_print_cstr(
        "Analysis: QEMU not cycle-accurate; UART and LDO not in energy model; meter noise on HW.\n");
}

int main(void)
{
    char line1[LINE_CAP];
    char line2[LINE_CAP];
    uint8_t n1;
    uint8_t n2;
    int32_t hw_n;
    int32_t hw_lp;
    uint32_t hw_n_u;
    uint32_t hw_lp_u;

    read_line_blocking();
    copy_rx_line_to(line1, LINE_CAP);
    read_line_blocking();
    copy_rx_line_to(line2, LINE_CAP);
    n1 = 0u;
    while (line1[n1] != '\0') {
        n1++;
    }
    n2 = 0u;
    while (line2[n2] != '\0') {
        n2++;
    }
    hw_n = parse_ma_tenths(line1, n1);
    hw_lp = parse_ma_tenths(line2, n2);
    if (hw_n < 0) {
        hw_n = 0;
    }
    if (hw_lp < 0) {
        hw_lp = 0;
    }
    hw_n_u = (uint32_t)hw_n;
    hw_lp_u = (uint32_t)hw_lp;

    run_phase_normal();
    run_phase_low_power();
    print_report(hw_n_u, hw_lp_u);

    for (;;) {
        oj_uart_poll_step();
    }
}
