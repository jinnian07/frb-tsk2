/*
 * P0003 reference — static memory pool + UART command I/O (bare-metal OJ).
 * No printf/sprintf/strcpy: uart_write_byte only.
 */
#include <stddef.h>
#include <stdint.h>

#include "uart_oj_rx_poll.h"

#define BLOCK_COUNT     8u
#define USER_BYTES      24u
#define PHYS_BYTES      32u
#define POOL_SIZE       (PHYS_BYTES * BLOCK_COUNT)

#define POOL_MAGIC      0xA5A5u
#define CANARY_U32      0xDEADBEEFu

extern void uart_write_byte(uint8_t ch);

typedef struct {
    uint8_t used;
    uint8_t *ptr;
    uint16_t magic;
} block_ctrl_t;

static uint8_t memory_pool[POOL_SIZE];
static block_ctrl_t block_ctl[BLOCK_COUNT];
static void *id_ptr[BLOCK_COUNT];
/* id_live: 编号当前是否仍指向未释放块；id_issued: 本会话是否曾 ALLOC 过该编号（区分非法 id / 重复释放） */
static uint8_t id_live[BLOCK_COUNT];
static uint8_t id_issued[BLOCK_COUNT];

/* mem_pool_free detail for UART messages */
enum {
    FREE_OK = 0,
    FREE_ERR_ILLEGAL_PTR = 1,
    FREE_ERR_DOUBLE = 2,
    FREE_ERR_CANARY = 3,
    FREE_ERR_MAGIC = 4
};

static void crit_enter(void)
{
    __asm volatile("cpsid i" ::: "memory");
}

static void crit_leave(void)
{
    __asm volatile("cpsie i" ::: "memory");
}

static void store_canary_bytes(uint8_t *p)
{
    uint32_t c = CANARY_U32;

    p[0] = (uint8_t)(c & 0xFFu);
    p[1] = (uint8_t)((c >> 8) & 0xFFu);
    p[2] = (uint8_t)((c >> 16) & 0xFFu);
    p[3] = (uint8_t)((c >> 24) & 0xFFu);
}

static int canary_ok(const uint8_t *p)
{
    uint32_t v;

    v = (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
    return (v == CANARY_U32) ? 1 : 0;
}

static int pool_index_from_user_ptr(const uint8_t *p)
{
    ptrdiff_t off;
    ptrdiff_t base_off;

    if (p == NULL) {
        return -1;
    }
    base_off = (ptrdiff_t)(p - memory_pool);
    if (base_off < 4) {
        return -1;
    }
    off = base_off - 4;
    if ((off % (ptrdiff_t)PHYS_BYTES) != 0) {
        return -1;
    }
    if (off < 0) {
        return -1;
    }
    {
        int idx = (int)(off / (ptrdiff_t)PHYS_BYTES);
        if (idx < 0 || idx >= (int)BLOCK_COUNT) {
            return -1;
        }
        return idx;
    }
}

static void block_refresh_canaries(int idx)
{
    uint8_t *blk = memory_pool + ((uint32_t)idx * PHYS_BYTES);

    store_canary_bytes(blk);
    store_canary_bytes(blk + 4u + USER_BYTES);
}

void mem_pool_init(void)
{
    uint32_t i;

    for (i = 0u; i < BLOCK_COUNT; i++) {
        block_ctl[i].used = 0u;
        block_ctl[i].ptr = NULL;
        block_ctl[i].magic = 0u;
    }

    for (i = 0u; i < BLOCK_COUNT; i++) {
        uint8_t *blk = memory_pool + (i * PHYS_BYTES);
        uint32_t j;

        block_refresh_canaries((int)i);
        for (j = 0u; j < USER_BYTES; j++) {
            blk[4u + j] = 0u;
        }
    }
}

void *mem_pool_alloc(void)
{
    void *ret = NULL;
    uint32_t i;

    crit_enter();
    for (i = 0u; i < BLOCK_COUNT; i++) {
        if (block_ctl[i].used == 0u) {
            uint8_t *blk = memory_pool + (i * PHYS_BYTES);
            uint8_t *user = blk + 4u;

            block_ctl[i].used = 1u;
            block_ctl[i].magic = POOL_MAGIC;
            block_ctl[i].ptr = user;
            block_refresh_canaries((int)i);
            ret = (void *)user;
            break;
        }
    }
    crit_leave();
    return ret;
}

int mem_pool_check(void *p, size_t write_size)
{
    int idx;
    uint8_t *blk;

    if (p == NULL) {
        return 0;
    }
    idx = pool_index_from_user_ptr((const uint8_t *)p);
    if (idx < 0) {
        return 0;
    }
    if (block_ctl[(uint32_t)idx].used == 0u) {
        return 0;
    }
    if (block_ctl[(uint32_t)idx].ptr != (uint8_t *)p) {
        return 0;
    }
    if (block_ctl[(uint32_t)idx].magic != POOL_MAGIC) {
        return 0;
    }
    if (write_size > USER_BYTES) {
        return 0;
    }
    blk = memory_pool + ((uint32_t)idx * PHYS_BYTES);
    if (!canary_ok(blk)) {
        return 0;
    }
    if (!canary_ok(blk + 4u + USER_BYTES)) {
        return 0;
    }
    return 1;
}

static int mem_pool_free_detail(void *p)
{
    int idx;
    uint8_t *blk;
    uint8_t *user;

    if (p == NULL) {
        return FREE_ERR_ILLEGAL_PTR;
    }
    user = (uint8_t *)p;
    idx = pool_index_from_user_ptr(user);
    if (idx < 0) {
        return FREE_ERR_ILLEGAL_PTR;
    }

    crit_enter();
    if (block_ctl[(uint32_t)idx].used == 0u) {
        crit_leave();
        return FREE_ERR_DOUBLE;
    }
    if (block_ctl[(uint32_t)idx].magic != POOL_MAGIC) {
        crit_leave();
        return FREE_ERR_MAGIC;
    }
    if (block_ctl[(uint32_t)idx].ptr != user) {
        crit_leave();
        return FREE_ERR_ILLEGAL_PTR;
    }

    blk = memory_pool + ((uint32_t)idx * PHYS_BYTES);
    if (!canary_ok(blk) || !canary_ok(blk + 4u + USER_BYTES)) {
        crit_leave();
        return FREE_ERR_CANARY;
    }

    block_ctl[(uint32_t)idx].used = 0u;
    block_ctl[(uint32_t)idx].magic = 0u;
    block_ctl[(uint32_t)idx].ptr = NULL;
    {
        uint32_t j;
        for (j = 0u; j < USER_BYTES; j++) {
            blk[4u + j] = 0u;
        }
    }
    block_refresh_canaries(idx);
    crit_leave();
    return FREE_OK;
}

int mem_pool_free(void *p)
{
    return (mem_pool_free_detail(p) == FREE_OK) ? 0 : -1;
}

/* ---------- UART protocol ---------- */

/* 最长行约「WRITE 0」+ 25 组 hex；160 足够且减小 .bss */
#define LINE_CAP 160u

static char s_line[LINE_CAP];
static volatile uint16_t s_line_len;
static volatile uint16_t s_line_commit_len;
static volatile uint8_t s_line_ready;

static void print_cstr(const char *s)
{
    while (*s != '\0') {
        uart_write_byte((uint8_t)*s);
        s++;
    }
}

static void print_u32_dec(uint32_t v)
{
    char buf[12];
    uint8_t n = 0u;
    uint8_t i;

    if (v == 0u) {
        uart_write_byte((uint8_t)'0');
        return;
    }
    while (v > 0u && n < (uint8_t)sizeof(buf)) {
        buf[n] = (char)('0' + (v % 10u));
        v /= 10u;
        n++;
    }
    for (i = n; i > 0u; i--) {
        uart_write_byte((uint8_t)buf[i - 1u]);
    }
}

static int hex_val(uint8_t c)
{
    if (c >= (uint8_t)'0' && c <= (uint8_t)'9') {
        return (int)(c - (uint8_t)'0');
    }
    if (c >= (uint8_t)'A' && c <= (uint8_t)'F') {
        return (int)(c - (uint8_t)'A' + 10);
    }
    if (c >= (uint8_t)'a' && c <= (uint8_t)'f') {
        return (int)(c - (uint8_t)'a' + 10);
    }
    return -1;
}

static int alloc_slot_id(void *p)
{
    uint32_t i;

    for (i = 0u; i < BLOCK_COUNT; i++) {
        if (id_live[i] == 0u) {
            id_ptr[i] = p;
            id_live[i] = 1u;
            id_issued[i] = 1u;
            return (int)i;
        }
    }
    return -1;
}

static void *ptr_for_id(uint32_t id)
{
    if (id >= BLOCK_COUNT) {
        return NULL;
    }
    if (id_live[id] == 0u) {
        return NULL;
    }
    return id_ptr[id];
}

static void clear_all_ids(void)
{
    uint32_t i;

    for (i = 0u; i < BLOCK_COUNT; i++) {
        id_ptr[i] = NULL;
        id_live[i] = 0u;
        id_issued[i] = 0u;
    }
}

static uint32_t parse_u32(const char *line, uint16_t len, uint16_t *ppos)
{
    uint32_t v = 0u;
    uint16_t pos = *ppos;

    while (pos < len && line[pos] == ' ') {
        pos++;
    }
    if (pos >= len || line[pos] < '0' || line[pos] > '9') {
        *ppos = pos;
        return 0xFFFFFFFFu;
    }
    while (pos < len && line[pos] >= '0' && line[pos] <= '9') {
        v = v * 10u + (uint32_t)(line[pos] - '0');
        pos++;
    }
    *ppos = pos;
    return v;
}

static void handle_line(const char *line, uint16_t len)
{
    uint16_t pos = 0u;

    while (pos < len && line[pos] == ' ') {
        pos++;
    }
    if (pos >= len) {
        return;
    }

    if (len >= 5u && line[0] == 'A' && line[1] == 'L' && line[2] == 'L' && line[3] == 'O' &&
        line[4] == 'C') {
        void *p = mem_pool_alloc();

        if (p == NULL) {
            print_cstr("ALLOC FAIL\n");
        } else {
            int sid = alloc_slot_id(p);
            print_cstr("ALLOC OK: ");
            print_u32_dec((uint32_t)sid);
            print_cstr("\n");
        }
        return;
    }

    if (len >= 5u && line[0] == 'R' && line[1] == 'E' && line[2] == 'S' && line[3] == 'E' &&
        line[4] == 'T') {
        clear_all_ids();
        mem_pool_init();
        print_cstr("RESET OK\n");
        return;
    }

    if (len >= 5u && line[0] == 'C' && line[1] == 'H' && line[2] == 'E' && line[3] == 'C' &&
        line[4] == 'K') {
        uint32_t id;

        pos = 5u;
        id = parse_u32(line, len, &pos);
        if (id == 0xFFFFFFFFu || id >= BLOCK_COUNT) {
            print_cstr("CHECK ERROR: 内存损坏\n");
            return;
        }
        {
            void *p = ptr_for_id(id);

            if (p == NULL) {
                print_cstr("CHECK ERROR: 内存损坏\n");
            } else if (mem_pool_check(p, 0u) != 0) {
                print_cstr("CHECK OK\n");
            } else {
                print_cstr("CHECK ERROR: 内存损坏\n");
            }
        }
        return;
    }

    if (len >= 4u && line[0] == 'F' && line[1] == 'R' && line[2] == 'E' && line[3] == 'E') {
        uint32_t id;
        int fr;

        pos = 4u;
        id = parse_u32(line, len, &pos);
        if (id == 0xFFFFFFFFu || id >= BLOCK_COUNT) {
            print_cstr("FREE ERROR: 非法指针\n");
            return;
        }
        {
            void *p;

            if (id_live[id] == 0u) {
                if (id_issued[id] != 0u) {
                    print_cstr("FREE ERROR: 重复释放\n");
                } else {
                    print_cstr("FREE ERROR: 非法指针\n");
                }
                return;
            }
            p = id_ptr[id];
            fr = mem_pool_free_detail(p);
            if (fr == FREE_OK) {
                id_live[id] = 0u;
                id_ptr[id] = NULL;
                print_cstr("FREE OK\n");
            } else if (fr == FREE_ERR_DOUBLE) {
                print_cstr("FREE ERROR: 重复释放\n");
            } else if (fr == FREE_ERR_CANARY) {
                print_cstr("FREE ERROR: 内存越界，金丝雀值被破坏\n");
            } else {
                print_cstr("FREE ERROR: 非法指针\n");
            }
        }
        return;
    }

    if (len >= 5u && line[0] == 'W' && line[1] == 'R' && line[2] == 'I' && line[3] == 'T' &&
        line[4] == 'E') {
        uint32_t id;
        uint32_t bi = 0u;
        int hi = -1;

        pos = 5u;
        id = parse_u32(line, len, &pos);
        if (id == 0xFFFFFFFFu || id >= BLOCK_COUNT) {
            print_cstr("WRITE ERROR: 非法指针\n");
            return;
        }
        {
            void *p = ptr_for_id(id);
            uint8_t *up;

            if (p == NULL) {
                print_cstr("WRITE ERROR: 非法指针\n");
                return;
            }
            up = (uint8_t *)p;
            while (pos < len && line[pos] == ' ') {
                pos++;
            }
            for (; pos < len; pos++) {
                uint8_t c = (uint8_t)line[pos];
                int v;

                if (c == ' ' || c == '\t') {
                    continue;
                }
                v = hex_val(c);
                if (v < 0) {
                    break;
                }
                if (hi < 0) {
                    hi = v;
                } else {
                    up[bi] = (uint8_t)(((uint32_t)hi << 4) | (uint32_t)v);
                    bi++;
                    hi = -1;
                }
            }
            print_cstr("WRITE OK\n");
        }
        return;
    }
}

int main(void)
{
    mem_pool_init();

    while (1) {
        oj_uart_poll_step();
        if (s_line_ready != 0u) {
            uint16_t n = s_line_commit_len;

            if (n >= LINE_CAP) {
                n = (uint16_t)(LINE_CAP - 1u);
            }
            /* 同步 poll 模型下无重入；避免 main 栈上 160B+ 副本 */
            s_line_ready = 0u;
            handle_line(s_line, n);
        }
    }
}

void UART_IRQHandler(void)
{
    uint8_t b = UART_ReceiveByte();

    if (b == (uint8_t)'\r') {
        return;
    }
    if (b == (uint8_t)'\n') {
        s_line_commit_len = s_line_len;
        s_line[s_line_len] = '\0';
        s_line_ready = 1u;
        s_line_len = 0u;
        return;
    }
    if (s_line_len < (LINE_CAP - 1u)) {
        s_line[s_line_len] = (char)b;
        s_line_len++;
    }
}
