/*
 * Host-side UART stubs for classroom gcov coverage (not used on QEMU firmware).
 * Feeds RX from a buffer set by coverage_host_driver.c; TX discarded.
 */
#include <stdint.h>
#include <string.h>

#define COV_UART_BUF 65536u

static uint8_t s_in[COV_UART_BUF];
static size_t s_pos;
static size_t s_len;

void cov_host_set_input(const uint8_t *data, size_t len)
{
    if (len > COV_UART_BUF) {
        len = COV_UART_BUF;
    }
    if (data != NULL && len > 0u) {
        (void)memcpy(s_in, data, len);
    }
    s_len = len;
    s_pos = 0u;
}

int uart_try_read_byte(void)
{
    if (s_pos >= s_len) {
        return -1;
    }
    return (int)s_in[s_pos++];
}

void uart_write_byte(uint8_t ch)
{
    (void)ch;
}
