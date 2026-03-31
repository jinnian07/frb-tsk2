#include <stdint.h>

// UART1 (USART1) for QEMU "stm32vldiscovery".
// QEMU maps USART0/1/2 at:
//   - 0x40013800 (USART1)   <- UART1 we use
//   - 0x40004400 (USART2)
//   - 0x40004800 (USART3)
//
// USART register layout/bit masks follow QEMU's STM32F2XX USART model.

#define USART1_BASE 0x40013800u

#define USART_SR_OFFSET 0x00u
#define USART_DR_OFFSET 0x04u
#define USART_BRR_OFFSET 0x08u
#define USART_CR1_OFFSET 0x0Cu

#define USART_SR_TXE (1u << 7)
#define USART_SR_TC  (1u << 6)
#define USART_SR_RXNE (1u << 5)

#define USART_CR1_UE (1u << 13)
#define USART_CR1_TE (1u << 3)
#define USART_CR1_RE (1u << 2)

// QEMU "stm32vldiscovery" SYSCLK is fixed at 24MHz.
#define SYSCLK_HZ 24000000u

static inline volatile uint32_t* reg32(uint32_t offset) {
    return (volatile uint32_t*)(USART1_BASE + offset);
}

/*
 * Weak default: P0001/P0002/… 未实现 uart_init 时由链接器选用本实现。
 * P0000 等题若提交强符号 uart_init，将覆盖本函数，避免重复定义。
 */
__attribute__((weak)) void uart_init(void) {
    // Baud rate register (BRR) is accepted/stored by QEMU's USART model.
    // RX/TX in the model is mostly synchronous; we still set BRR for correctness.
    const uint32_t baud = 115200u;
    uint32_t brr = (SYSCLK_HZ + (baud * 8u)) / (baud * 16u); // rounded integer
    *reg32(USART_BRR_OFFSET) = brr;

    // Enable USART + transmitter + receiver.
    *reg32(USART_CR1_OFFSET) = USART_CR1_UE | USART_CR1_TE | USART_CR1_RE;
}

// Returns -1 if no byte available, else returns 0..255.
int uart_try_read_byte(void) {
    uint32_t sr = *reg32(USART_SR_OFFSET);
    if ((sr & USART_SR_RXNE) == 0u) {
        return -1;
    }
    uint32_t dr = *reg32(USART_DR_OFFSET);
    return (int)(dr & 0xFFu);
}

void uart_write_byte(uint8_t ch) {
    // TXE is "always set" in QEMU model, but keep the wait for portability.
    while ((*reg32(USART_SR_OFFSET) & USART_SR_TXE) == 0u) {
        // busy wait
    }
    *reg32(USART_DR_OFFSET) = (uint32_t)ch;
}

