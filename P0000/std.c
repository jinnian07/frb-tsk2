/*
 * P0000 标程：USART1 115200 8N1 初始化 + 字符串发送（裸机 OJ）。
 * uart_init 为强符号，覆盖 baremetal/uart1_qemu.c 中的 weak 默认实现。
 */
#include <stddef.h>
#include <stdint.h>

#include "uart_oj_rx_poll.h"

#define USART1_BASE 0x40013800u

#define USART_SR_OFFSET 0x00u
#define USART_DR_OFFSET 0x04u
#define USART_BRR_OFFSET 0x08u
#define USART_CR1_OFFSET 0x0Cu

#define USART_SR_TXE (1u << 7)
#define USART_CR1_UE (1u << 13)
#define USART_CR1_TE (1u << 3)
#define USART_CR1_RE (1u << 2)

#define SYSCLK_HZ 24000000u

static inline volatile uint32_t *reg32(uint32_t offset)
{
    return (volatile uint32_t *)(USART1_BASE + offset);
}

extern void uart_write_byte(uint8_t ch);

/*
 * QEMU USART1 经 TCP server,nowait：若上电立刻 TX，主机尚未 connect 时字节会丢失。
 * 先等到 oj_uart_poll_step 收到评测机注入的至少 1 字节后再发送问候（与 P0001 类题一致）。
 */
static volatile uint8_t s_uart_got_byte;

void UART_IRQHandler(void)
{
    s_uart_got_byte = 1u;
}

void uart_init(void)
{
    const uint32_t baud = 115200u;
    uint32_t brr = (SYSCLK_HZ + (baud * 8u)) / (baud * 16u);

    *reg32(USART_BRR_OFFSET) = brr;
    *reg32(USART_CR1_OFFSET) = USART_CR1_UE | USART_CR1_TE | USART_CR1_RE;
}

void uart_send_string(const char *s)
{
    if (s == NULL) {
        return;
    }
    while (*s != '\0') {
        uart_write_byte((uint8_t)*s);
        s++;
    }
}

int main(void)
{
    uart_init();
    while (s_uart_got_byte == (uint8_t)0) {
        oj_uart_poll_step();
    }
    uart_send_string("Hello, Relilearn!");
    uart_write_byte((uint8_t)'\n');

    while (1) {
        oj_uart_poll_step();
    }
}
