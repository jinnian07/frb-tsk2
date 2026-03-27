/* Vector table for QEMU `stm32vldiscovery` (STM32F100, Cortex-M3).
 *
 * We generate it in assembly to avoid GCC "initializer element is not constant"
 * when trying to build the vector table in C.
 *
 * Vector table size must match:
 *   16 system vectors + 61 external IRQs = 77 words
 */

.syntax unified
.thumb

.section .isr_vector, "a", %progbits
.balign 256

.global vector_table
vector_table:
    /* 0: initial stack pointer */
    .word _estack
    /* 1: reset handler (Thumb entry -> +1) */
    .word Reset_Handler + 1

    /* 2..76: all other vectors -> Default_Handler (Thumb entry -> +1) */
    .rept (77 - 2)
    .word Default_Handler + 1
    .endr

