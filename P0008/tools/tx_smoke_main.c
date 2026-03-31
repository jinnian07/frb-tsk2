#include <stdint.h>
#include "uart_oj_rx_poll.h"
extern void uart_write_byte(uint8_t ch);
int main(void) {
    const char *s = "TXOK\n";
    while (*s) uart_write_byte((uint8_t)*s++);
    while (1) { oj_uart_poll_step(); }
}
