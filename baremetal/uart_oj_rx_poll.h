#ifndef UART_OJ_RX_POLL_H
#define UART_OJ_RX_POLL_H

#include <stdint.h>

/* Platform: last byte from QEMU USART1 RX, for UART_IRQHandler-style solutions. */
uint8_t UART_ReceiveByte(void);

/* Call from main loop: drains one byte from UART (if any) and invokes UART_IRQHandler. */
void oj_uart_poll_step(void);

#endif
