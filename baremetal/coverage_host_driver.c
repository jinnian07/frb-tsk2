/*
 * Host main: load binary UART payload from argv[1], run oj_uart_poll_step until idle.
 * Linked with stripped user TU + uart_oj_rx_poll.c + coverage_host_stubs.c (--coverage).
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "uart_oj_rx_poll.h"

extern void cov_host_set_input(const uint8_t *data, size_t len);

#define MAX_IN 65536u

int main(int argc, char **argv)
{
    uint8_t buf[MAX_IN];

    if (argc < 2) {
        return 1;
    }

    FILE *fp = fopen(argv[1], "rb");
    if (fp == NULL) {
        return 1;
    }

    size_t n = fread(buf, 1u, MAX_IN, fp);
    (void)fclose(fp);

    cov_host_set_input(buf, n);

    /* Enough iterations to drain UART FIFO for typical OJ inputs */
    for (unsigned i = 0u; i < 250000u; i++) {
        oj_uart_poll_step();
    }

    return 0;
}
