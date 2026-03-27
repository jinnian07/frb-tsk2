/* Linker script for QEMU "stm32vldiscovery" (STM32F100, Cortex-M3).
 *
 * QEMU maps:
 *   - Flash: 0x0800_0000, size 128K
 *   - SRAM : 0x2000_0000, size 8K
 *
 * QEMU additionally aliases flash to address 0x0000_0000, so the vector table
 * is readable at both 0x0800_0000 and 0x0000_0000.
 */

ENTRY(Reset_Handler)

MEMORY
{
    FLASH (rx)  : ORIGIN = 0x08000000, LENGTH = 128K
    RAM   (rwx) : ORIGIN = 0x20000000, LENGTH = 8K
}

SECTIONS
{
    .isr_vector :
    {
        KEEP(*(.isr_vector))
    } > FLASH

    .text :
    {
        *(.text*)         /* code */
        *(.rodata*)       /* read-only data */
        *(.ARM.extab* .gnu.linkonce.armextab.*)
        *(.ARM.exidx* .gnu.linkonce.armexidx.*)
    } > FLASH

    .data : AT (ADDR(.text) + SIZEOF(.text))
    {
        _sdata = .;
        *(.data*)
        *(.ramfunc*)
        _edata = .;
    } > RAM

    /* Load address of .data in flash */
    _sidata = LOADADDR(.data);

    .bss :
    {
        _sbss = .;
        *(.bss*)
        *(COMMON)
        _ebss = .;
    } > RAM

    /* Provide an explicit stack top symbol for the vector table. */
    ._stack_top (NOLOAD) :
    {
        . = ORIGIN(RAM) + LENGTH(RAM);
    }
    _estack = ORIGIN(RAM) + LENGTH(RAM);
    /*
     * Stack grows down from _estack toward RAM. Usable stack for painting/watermark
     * is [_ebss, _estack) — no separate heap in this script; all RAM above .bss is stack.
     */
}

