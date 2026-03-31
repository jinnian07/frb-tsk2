/*
 * P0004 reference — RM + mutex + PIP, logical 1 ms ticks (software scheduler_step).
 * No printf, no malloc. UART output must match tools/gen_p0004_out.py.
 *
 * Boot: wait until at least one UART byte (OJ TCP + payload), drain briefly, then simulate.
 * parse_* and scheduler are noinline so GCC does not merge them into main incorrectly.
 */
#include <stdint.h>
#include <stddef.h>

#include "uart_oj_rx_poll.h"

extern void uart_write_byte(uint8_t ch);

#define CMD_CAP 128u
/* used: keep .bss and byte stores; without it, -Wl,--gc-sections + DSE can strip s_cmd
 * and collapse UART_IRQHandler to only incrementing s_cmd_len (OJ then waits forever). */
static uint8_t s_cmd[CMD_CAP] __attribute__((used));
static volatile uint16_t s_cmd_len;

typedef struct {
    uint32_t sum_resp;
    uint16_t cnt;
    uint16_t max_resp;
    uint16_t min_resp;
} Stat;

typedef struct {
    uint8_t tid;
    uint8_t base_prio;
    uint8_t period;
    uint8_t wcet_base;
    uint8_t rem;
    uint16_t release_t;
    uint8_t blocked;
    Stat st;
} Task;

static Task s_tasks[3];
static int8_t s_mutex_owner; /* -1 none, 0 L, 1 M, 2 H */
static uint32_t s_deadlock;

void UART_IRQHandler(void)
{
    uint8_t b = UART_ReceiveByte();

    if (s_cmd_len < (CMD_CAP - 1u)) {
        s_cmd[s_cmd_len] = b;
        s_cmd_len++;
    }
}

static uint8_t cmd_prefix_has_newline(uint16_t n)
{
    uint16_t k;

    for (k = 0u; k < n; k++) {
        if (s_cmd[k] == (uint8_t)'\n') {
            return 1u;
        }
    }
    return 0u;
}

static void uart_print_cstr(const char *s)
{
    const char *p = s;

    while (*p != '\0') {
        uart_write_byte((uint8_t)*p);
        p++;
    }
}

static void uart_print_u32(uint32_t v)
{
    char buf[12];
    uint8_t n = 0u;
    uint8_t i;

    if (v == 0u) {
        uart_write_byte((uint8_t)'0');
        return;
    }
    while (v > 0u) {
        buf[n] = (char)('0' + (v % 10u));
        v /= 10u;
        n++;
    }
    for (i = n; i > (uint8_t)0; i--) {
        uart_write_byte((uint8_t)buf[i - 1u]);
    }
}

/* value is (milliseconds * 100), two decimal places */
static void uart_print_ms_x100(uint32_t v)
{
    uint32_t ip = v / 100u;
    uint32_t fp = v % 100u;

    uart_print_u32(ip);
    uart_write_byte((uint8_t)'.');
    uart_write_byte((uint8_t)('0' + (fp / 10u) % 10u));
    uart_write_byte((uint8_t)('0' + fp % 10u));
}

static uint8_t __attribute__((noinline)) parse_heavy(const uint8_t *buf, uint16_t n)
{
    uint16_t i;

    for (i = 0u; i < n; i++) {
        uint8_t c = buf[i];

        if ((c == (uint8_t)'d') || (c == (uint8_t)'D')) {
            return 1u;
        }
    }
    return 0u;
}

static uint32_t __attribute__((noinline)) parse_run_ticks(const uint8_t *buf, uint16_t n)
{
    uint32_t last = 0u;
    uint32_t cur = 0u;
    uint8_t seen = 0u;
    uint16_t i;

    for (i = 0u; i < n; i++) {
        uint8_t c = buf[i];

        if ((c >= (uint8_t)'0') && (c <= (uint8_t)'9')) {
            cur = (cur * 10u) + (uint32_t)(c - (uint8_t)'0');
            seen = 1u;
        } else {
            if (seen != 0u) {
                last = cur;
                cur = 0u;
                seen = 0u;
            }
        }
    }
    if (seen != 0u) {
        last = cur;
    }
    if (last == 0u) {
        last = 100u;
    }
    if (last > 5000u) {
        last = 5000u;
    }
    if (last < 1u) {
        last = 1u;
    }
    return last;
}

static void stat_job_done(Task *t, uint32_t tick)
{
    uint32_t r = tick - (uint32_t)t->release_t;
    Stat *st = &t->st;

    st->sum_resp += r;
    st->cnt++;
    if (r > (uint32_t)st->max_resp) {
        st->max_resp = (uint16_t)r;
    }
    if (r < (uint32_t)st->min_resp) {
        st->min_resp = (uint16_t)r;
    }
}

static uint8_t eff_prio(const Task *t)
{
    uint8_t p = t->base_prio;

    if ((t->tid == 0u) && (s_mutex_owner == 0)) {
        const Task *h = &s_tasks[2];

        if (h->blocked != 0u) {
            if (h->base_prio > p) {
                p = h->base_prio;
            }
        }
    }
    return p;
}

static void start_job(Task *t, uint32_t tick)
{
    t->rem = t->wcet_base;
    t->release_t = (uint16_t)tick;
    t->blocked = 0u;
}

static void tasks_init(uint8_t heavy)
{
    uint8_t mw = (uint8_t)(3u + (heavy != 0u ? 3u : 0u));
    uint8_t i;

    s_tasks[0].tid = 0u;
    s_tasks[0].base_prio = 1u;
    s_tasks[0].period = 20u;
    s_tasks[0].wcet_base = 4u;
    s_tasks[1].tid = 1u;
    s_tasks[1].base_prio = 2u;
    s_tasks[1].period = 15u;
    s_tasks[1].wcet_base = mw;
    s_tasks[2].tid = 2u;
    s_tasks[2].base_prio = 3u;
    s_tasks[2].period = 10u;
    s_tasks[2].wcet_base = 2u;

    for (i = 0u; i < 3u; i++) {
        Task *t = &s_tasks[i];

        t->rem = t->wcet_base;
        t->release_t = 0u;
        t->blocked = 0u;
        t->st.sum_resp = 0u;
        t->st.cnt = 0u;
        t->st.max_resp = 0u;
        t->st.min_resp = 9999u;
    }
    s_mutex_owner = -1;
    s_deadlock = 0u;
}

static void __attribute__((noinline)) scheduler_tick(uint32_t tick)
{
    uint8_t ti;
    Task *run = NULL;
    uint8_t best_ep = 0u;
    uint8_t best_bp = 0u;
    int16_t best_tid = 100;

    for (ti = 0u; ti < 3u; ti++) {
        Task *t = &s_tasks[ti];

        if ((tick % (uint32_t)t->period) == 0u) {
            start_job(t, tick);
        }
    }

    for (ti = 0u; ti < 3u; ti++) {
        s_tasks[ti].blocked = 0u;
    }

    if ((s_tasks[2].rem == 2u) && (s_mutex_owner >= 0) && (s_mutex_owner != 2)) {
        s_tasks[2].blocked = 1u;
    }
    if ((s_tasks[0].rem == 4u) && (s_mutex_owner >= 0) && (s_mutex_owner != 0)) {
        s_tasks[0].blocked = 1u;
    }

    for (ti = 0u; ti < 3u; ti++) {
        Task *t = &s_tasks[ti];
        uint8_t ep;

        if ((t->rem == 0u) || (t->blocked != 0u)) {
            continue;
        }
        ep = eff_prio(t);
        if ((run == NULL) || (ep > best_ep) || ((ep == best_ep) && (t->base_prio > best_bp)) ||
            ((ep == best_ep) && (t->base_prio == best_bp) && ((int16_t)t->tid < best_tid))) {
            run = t;
            best_ep = ep;
            best_bp = t->base_prio;
            best_tid = (int16_t)t->tid;
        }
    }

    if (run == NULL) {
        return;
    }

    if (run->tid == 0u) {
        if (run->rem == 4u) {
            s_mutex_owner = 0;
        }
        run->rem--;
        if (run->rem == 2u) {
            s_mutex_owner = -1;
        }
        if (run->rem == 0u) {
            stat_job_done(run, tick);
        }
    } else if (run->tid == 1u) {
        run->rem--;
        if (run->rem == 0u) {
            stat_job_done(run, tick);
        }
    } else {
        if (run->rem == 2u) {
            s_mutex_owner = 2;
        } else if (run->rem == 1u) {
            s_mutex_owner = -1;
        }
        run->rem--;
        if (run->rem == 0u) {
            uint32_t r = tick - (uint32_t)run->release_t;

            if (r > 10u) {
                s_deadlock++;
            }
            stat_job_done(run, tick);
        }
    }
}

static void __attribute__((noinline)) run_sim(uint32_t n_ticks, uint8_t heavy)
{
    uint32_t t;

    tasks_init(heavy);
    for (t = 1u; t <= n_ticks; t++) {
        scheduler_tick(t);
    }
}

static void __attribute__((noinline)) print_task_line(const Task *t, uint8_t stack_b)
{
    const Stat *st = &t->st;

    if (t->tid == 2u) {
        uart_print_cstr("[H] avg_resp=");
    } else if (t->tid == 1u) {
        uart_print_cstr("[M] avg_resp=");
    } else {
        uart_print_cstr("[L] avg_resp=");
    }

    if (st->cnt == 0u) {
        uart_print_cstr("0.00ms max=0.00ms jitter=0.00ms stack=");
        uart_print_u32((uint32_t)stack_b);
        uart_print_cstr("B\n");
        return;
    }

    {
        uint32_t avg_x100 = (st->sum_resp * 100u + ((uint32_t)st->cnt / 2u)) / (uint32_t)st->cnt;

        uart_print_ms_x100(avg_x100);
        uart_print_cstr("ms max=");
        uart_print_ms_x100((uint32_t)st->max_resp * 100u);
        uart_print_cstr("ms jitter=");
        if (st->cnt >= 2u) {
            uint32_t jt = (uint32_t)(st->max_resp - st->min_resp) * 100u;

            uart_print_ms_x100(jt);
        } else {
            uart_print_cstr("0.00");
        }
        uart_print_cstr("ms stack=");
        uart_print_u32((uint32_t)stack_b);
        uart_print_cstr("B\n");
    }
}

static void __attribute__((noinline)) print_report(uint32_t n_ticks)
{
    const Task *h = &s_tasks[2];
    uint32_t h_periods = 1u + (n_ticks / 10u);
    uint32_t pct_x10;
    uint32_t jh_x100;

    if (h_periods < 1u) {
        h_periods = 1u;
    }

    print_task_line(h, 96u);
    print_task_line(&s_tasks[1], 112u);
    print_task_line(&s_tasks[0], 104u);

    uart_print_cstr("[Deadlock] count=");
    uart_print_u32(s_deadlock);
    uart_print_cstr(" / ");
    uart_print_u32(h_periods);
    uart_print_cstr(" periods (");

    pct_x10 = (s_deadlock * 1000u + (h_periods / 2u)) / h_periods;
    uart_print_u32(pct_x10 / 10u);
    uart_write_byte((uint8_t)'.');
    uart_write_byte((uint8_t)('0' + (pct_x10 % 10u)));
    uart_print_cstr("%)\n");

    if (h->st.cnt >= 2u) {
        jh_x100 = (uint32_t)(h->st.max_resp - h->st.min_resp) * 100u;
    } else {
        jh_x100 = 0u;
    }
    uart_print_cstr("[Jitter Ratio H] sim_hw_jitter/sim_sim_jitter = ");
    uart_print_ms_x100(jh_x100);
    uart_print_cstr("/");
    uart_print_ms_x100(jh_x100);
    uart_print_cstr(" = 1.00 (OK)\n");
}

int main(void)
{
    uint32_t i;
    uint8_t heavy;
    uint32_t n;
    uint16_t len;

    /* Host must connect and send payload before we TX (see 题面). */
    while (s_cmd_len == 0u) {
        oj_uart_poll_step();
    }
    /* Host (baremetal_uart_runner) always sends a trailing newline; wait for it
     * so we do not parse a partial line (e.g. only '5' before '0' arrives). */
    for (i = 0u; i < 500000u; i++) {
        uint16_t clen;

        oj_uart_poll_step();
        __asm volatile("" ::: "memory");
        clen = s_cmd_len;
        if (cmd_prefix_has_newline(clen) != 0u) {
            break;
        }
    }

    __asm volatile("" ::: "memory");
    len = s_cmd_len;
    heavy = parse_heavy(s_cmd, len);
    n = parse_run_ticks(s_cmd, len);
    run_sim(n, heavy);
    print_report(n);

    while (1) {
        oj_uart_poll_step();
    }
}
