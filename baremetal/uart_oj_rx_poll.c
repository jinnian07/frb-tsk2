#include <stdint.h>

#include "uart_oj_rx_poll.h"

extern int uart_try_read_byte(void);

/* Last byte delivered to user ISR (QEMU has no NVIC hook for student USART1 ISR). */
static uint8_t s_uart_rx_shadow;

uint8_t UART_ReceiveByte(void)
{
    return s_uart_rx_shadow;
}

/*
 * Weak default: problems that only use stdio never call oj_uart_poll_step, so this
 * is unused. If poll is used without defining UART_IRQHandler, link still succeeds.
 */
__attribute__((weak)) void UART_IRQHandler(void)
{
}

void oj_uart_poll_step(void)
{
    int b = uart_try_read_byte();
    if (b >= 0) {
        s_uart_rx_shadow = (uint8_t)b;
        UART_IRQHandler();
    }
}
