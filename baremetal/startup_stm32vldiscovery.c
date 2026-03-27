#include <stdint.h>

// Minimal Cortex-M startup for QEMU "stm32vldiscovery" (STM32F100 / Cortex-M3).
// Provides vector table, C runtime init (.data/.bss) and calls main().

extern int main(void);
extern void uart_init(void);

extern uint32_t _sidata;
extern uint32_t _sdata;
extern uint32_t _edata;
extern uint32_t _sbss;
extern uint32_t _ebss;
extern uint32_t _estack;

/* Filled by startup before main; host reads SRAM via GDB stub to compute max stack depth. */
#define OJ_STACK_PAINT_U32 0xDEADBEEFu

static void oj_paint_stack(void) {
    /*
     * Assumption (linker_stm32vldiscovery.ld): stack is [_ebss .. _estack), growing down from _estack.
     * Paint so the host can find the lowest touched word after the run.
     */
    uintptr_t lo = (uintptr_t)&_ebss;
    lo = (lo + 3u) & ~(uintptr_t)3u;
    uintptr_t hi = (uintptr_t)&_estack;
    for (uintptr_t a = lo; a < hi; a += 4u) {
        *(volatile uint32_t *)a = OJ_STACK_PAINT_U32;
    }
}

void Default_Handler(void) __attribute__((noreturn));
void Reset_Handler(void) __attribute__((noreturn));

void Default_Handler(void) {
    while (1) {
        // Trap: no extra UART/debug output (keeps judge output clean).
    }
}

static void copy_and_zero(void) {
    // Copy .data from flash (_sidata) to RAM (_sdata.._edata), then zero .bss.
    uint32_t* src = &_sidata;
    uint32_t* dst = &_sdata;
    while (dst < &_edata) {
        *dst++ = *src++;
    }

    for (uint32_t* b = &_sbss; b < &_ebss; ++b) {
        *b = 0;
    }
}

void Reset_Handler(void) {
    copy_and_zero();
    oj_paint_stack();

    // USART model in QEMU accepts RX only when UE+RE bits are set.
    uart_init();

    (void)main();

    while (1) {
        // If user main returns, stay alive (QEMU keeps running).
    }
}

