#include <errno.h>
#include <stddef.h>
#include <stdint.h>
#include <sys/types.h>
#include <sys/stat.h>

// Newlib syscalls for "no OS" Cortex-M.
// We map:
//   - stdin/stdout reads/writes to QEMU's USART1 UART1 device
//   - input EOF is emulated by: "last received char was '\\n' and UART has been idle for a while"

extern int uart_try_read_byte(void);
extern void uart_write_byte(uint8_t ch);

// Busy-spin counts. QEMU doesn't provide real time; tune if needed.
// After reading a newline, if UART stays idle for this many spins, we treat it as EOF.
#define UART_EOF_IDLE_SPIN 6000000u

// Wait for at least one byte for a short period; used before EOF candidate becomes true.
#define UART_RX_WAIT_SPIN 500000u

ssize_t _write(int fd, const void* buf, size_t count) {
    (void)fd;
    const uint8_t* p = (const uint8_t*)buf;
    for (size_t i = 0; i < count; i++) {
        uart_write_byte(p[i]);
    }
    return (ssize_t)count;
}

// Return 0 bytes (EOF) only when we already read a trailing '\\n' and then observe idle.
ssize_t _read(int fd, void* buf, size_t count) {
    (void)fd;
    uint8_t* p = (uint8_t*)buf;
    size_t i = 0;

    int last_was_newline = 0;

    while (i < count) {
        int b = uart_try_read_byte();
        if (b >= 0) {
            p[i++] = (uint8_t)b;
            last_was_newline = (b == '\n');
            continue;
        }

        if (last_was_newline) {
            // After newline: wait for "idle => EOF".
            for (uint32_t spin = 0; spin < UART_EOF_IDLE_SPIN; spin++) {
                b = uart_try_read_byte();
                if (b >= 0) {
                    p[i++] = (uint8_t)b;
                    last_was_newline = (b == '\n');
                    goto next_byte;
                }
            }
            // UART idle after last newline => EOF.
            return (i == 0) ? 0 : (ssize_t)i;
        }

        // Not yet in newline-EOF phase: wait a bit for first byte(s).
        for (uint32_t spin = 0; spin < UART_RX_WAIT_SPIN; spin++) {
            b = uart_try_read_byte();
            if (b >= 0) {
                p[i++] = (uint8_t)b;
                last_was_newline = (b == '\n');
                goto next_byte;
            }
        }
        // Still no data: keep waiting (do not force EOF until we saw '\\n').
    next_byte:
        continue;
    }

    return (ssize_t)i;
}

// Minimal stubs for newlib.
int _close(int file) {
    (void)file;
    return -1;
}

off_t _lseek(int file, off_t ptr, int dir) {
    (void)file;
    (void)ptr;
    (void)dir;
    return 0;
}

int _fstat(int file, struct stat* st) {
    (void)file;
    if (st) {
        st->st_mode = S_IFCHR;
    }
    return 0;
}

int _isatty(int file) {
    (void)file;
    return 1;
}

int _kill(int pid, int sig) {
    (void)pid;
    (void)sig;
    errno = EINVAL;
    return -1;
}

pid_t _getpid(void) {
    return 1;
}

caddr_t _sbrk(int incr) {
    (void)incr;
    errno = ENOMEM;
    return (caddr_t)-1;
}

void _exit(int status) {
    (void)status;
    while (1) {
        // no debug output
    }
}

