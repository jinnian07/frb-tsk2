#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

#include "uart_oj_rx_poll.h"

/* 全局缓冲区（由平台提供） */
#define MAX_DATA_LEN 16
#define BUFFER_SIZE (2 + MAX_DATA_LEN + 1) // 最大20字节
uint8_t rx_buffer[BUFFER_SIZE];
uint8_t received_data[MAX_DATA_LEN];
volatile uint8_t data_ready; // 由volatile修饰
volatile uint8_t received_data_len; /* 校验通过时有效数据长度，供主循环打印 */

/* 状态机定义 */
typedef enum {
    STATE_IDLE,
    STATE_RECEIVING
} rx_state_t;

static rx_state_t rx_state = STATE_IDLE;
static uint8_t rx_index = 0;
static uint8_t expected_len = 0;

extern void uart_write_byte(uint8_t ch);

/* 前置声明：UART_IRQHandler 中会调用，定义在文件后部 */
uint8_t process_frame(uint8_t *raw, uint8_t len);

/* 中断服务程序 */
void UART_IRQHandler(void)
{
    uint8_t byte = UART_ReceiveByte();

    switch (rx_state) {
    case STATE_IDLE:
        if (byte == 0xAA) {
            rx_buffer[0] = byte;
            rx_index = 1;
            rx_state = STATE_RECEIVING;
        }
        break;

    case STATE_RECEIVING:
        if (rx_index >= BUFFER_SIZE) {
            rx_state = STATE_IDLE; // 溢出，复位
            break;
        }
        rx_buffer[rx_index] = byte;
        rx_index++;

        if (rx_index == 2) {
            expected_len = byte;
            if (expected_len < 1 || expected_len > MAX_DATA_LEN) {
                rx_state = STATE_IDLE; // 非法长度，丢弃
            }
        } else if (rx_index == (expected_len + 3)) {
            if (process_frame(rx_buffer, expected_len + 3)) {
                data_ready = 1;
            }
            rx_state = STATE_IDLE;
        }
        break;

    default:
        rx_state = STATE_IDLE;
        break;
    }
}

/* 帧处理函数 */
uint8_t process_frame(uint8_t *raw, uint8_t len)
{
    if (raw == NULL || len < 3)
        return 0;

    uint8_t header = raw[0];
    uint8_t length = raw[1];
    uint8_t checksum = raw[len - 1];
    uint8_t cal_checksum = 0;

    if (header != 0xAA)
        return 0;
    if (length < 1 || length > MAX_DATA_LEN)
        return 0;
    if (len != (length + 3))
        return 0;

    for (uint8_t i = 0; i < len - 1; i++) {
        cal_checksum += raw[i];
    }
    if (cal_checksum != checksum)
        return 0;

    for (uint8_t i = 0; i < length; i++) {
        received_data[i] = raw[2 + i];
    }

    received_data_len = length;
    return 1;
}

static void print_ok_line(void)
{
    static const char hdr[] = "OK: ";
    const char *p = hdr;

    while (*p != '\0') {
        uart_write_byte((uint8_t)*p);
        p++;
    }

    for (uint8_t i = 0; i < received_data_len; i++) {
        uint8_t b = received_data[i];
        static const char hex[] = "0123456789ABCDEF";

        if (i > (uint8_t)0) {
            uart_write_byte((uint8_t)' ');
        }
        uart_write_byte((uint8_t)hex[(b >> 4) & (uint8_t)0x0F]);
        uart_write_byte((uint8_t)hex[b & (uint8_t)0x0F]);
    }
    uart_write_byte((uint8_t)'\n');
}

