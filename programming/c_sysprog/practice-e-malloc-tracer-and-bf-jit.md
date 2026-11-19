# 練習 E — LD_PRELOAD malloc 追蹤器與 Brainfuck JIT

> **目標**：把 Part 6 拼起來做兩個真的會用到的工具。第一部分用 [Ch 40](./40-dynamic-linking.md) 的符號介入寫一個 `LD_PRELOAD` 記憶體追蹤器，涵蓋 `malloc`/`free`/`calloc`/`realloc`，處理 `dlsym` 內部呼叫 `calloc` 的遞迴、多執行緒與「程式關掉 stderr」等陷阱，在 destructor 印出洩漏報告；第二部分從 [Ch 38](./38-compiler-construction.md) 的 `bf.c` 出發，替它的 IR 加上「迴圈改寫成乘法」（`fold_loop`），讓直譯器與 x86-64 JIT 都支援新指令，並量測直譯器與 JIT 的加速比。完成後你驗證了：會讀 `ld.so` 的符號解析順序、會在「不能用 malloc 的地方」寫程式、會手工編碼 x86-64 指令並把最佳化接到後端。

> **環境**：WSL Ubuntu 22.04 x86_64，gcc 11.4、clang 14、glibc 2.35。`dlsym` 遞迴問題另以 Docker `ubuntu:20.04`（glibc 2.31）重現。Brainfuck 測試程式取自 Ch 38 使用的 [jit-construct](https://github.com/jserv/jit-construct) 的 `progs/` 目錄。

## 背景與動機

**第一部分**：Valgrind 和 AddressSanitizer 很強，但前者（Memcheck）依其手冊會讓程式慢 10–50 倍，後者要重新編譯。實務上常常需要一個「不重編、低成本、只回答一個問題」的工具：這個程式到底配置了多少記憶體？哪裡沒釋放？heaptrack、jemalloc 的 profiling、glibc 自帶的 `mtrace(3)` 都是這一類，核心技術就是 Ch 40 的 `LD_PRELOAD` + `dlsym(RTLD_NEXT)`。難的不是攔截本身，而是攔截函式內部「不能用 malloc」帶來的一連串限制。

**第二部分**：Ch 38 的量測顯示，`bf.c` 的 JIT 在 `mandelbrot.b` 上仍輸給 `gcc -O2` 翻出來的 C。差距的一大來源是 Brainfuck 常見的「搬運迴圈」：`[->+++<]` 的語意是 `p[1] += p[0] * 3; p[0] = 0;`，但直譯器和 JIT 都老老實實跑 `p[0]` 圈。辨識這種迴圈並改寫成乘法，正是 Ch 37 講的**強度折減與迴圈消除**在一個玩具語言上的完整演練：前端辨識模式、IR 加新指令、兩個後端各自實作。

## 第一部分：malloc 追蹤器

### 任務規格

| 項目 | 規格 |
|---|---|
| 產出 | 共享程式庫 `libmtrace.so`，以 `LD_PRELOAD=./libmtrace.so ./prog` 使用，**不得**修改或重編 `prog` |
| 攔截 | `malloc`、`free`、`calloc`、`realloc`，全部轉給下一個定義（`dlsym(RTLD_NEXT, ...)`） |
| 統計 | 四種呼叫各自的次數；總配置位元組、目前存活位元組、峰值 |
| 洩漏 | 程式結束時仍存活的區塊：數量、總位元組，並列出前 N 筆（大小、位址、呼叫者位於哪個模組的哪個偏移） |
| 報告 | 在 `__attribute__((destructor))` 中輸出到 stderr；環境變數 `MTRACE_TOP` 控制列出幾筆（預設 10） |
| 限制 | 記錄存活配置**不得使用 `malloc`**；要能在多執行緒程式中正確計數；`dlsym` 若內部呼叫 `calloc`/`malloc` 不得無窮遞迴 |
| 語意 | `free(NULL)` 合法；`realloc(NULL, n)` 等同 `malloc(n)`；`realloc` 失敗時舊區塊仍有效（紀錄不能被刪）；`calloc` 的乘法溢位要傳回 NULL |
| 效能 | 不要求；但要能跑完 `ls`、`python3 -c ...` 這類真實程式 |

### 期望輸出範例

測試程式 `leak.c`：

```c
/* leak.c：故意洩漏的測試程式 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static char *dup_name(const char *s)
{
    char *p = malloc(strlen(s) + 1); /* 呼叫者忘了 free */
    if (p)
        strcpy(p, s);
    return p;
}

int main(void)
{
    int *arr = calloc(10, sizeof *arr);          /* 40 位元組，會釋放 */
    arr = realloc(arr, 100 * sizeof *arr);       /* 搬到 400 位元組 */
    free(arr);

    for (int i = 0; i < 3; i++)
        dup_name("leaked-string");               /* 3 × 14 位元組洩漏 */

    void *big = malloc(1 << 20);                 /* 1 MiB 洩漏 */
    (void) big;

    free(NULL);                                  /* 合法，什麼都不做 */
    void *z = realloc(NULL, 32);                 /* 等同 malloc(32) */
    z = realloc(z, 0);                           /* glibc：釋放並傳回 NULL */
    printf("realloc(z, 0) returned %p\n", z);
    return 0;
}
```

```console
$ gcc -std=c11 -Wall -Wextra -O0 -g leak.c -o leak
$ LD_PRELOAD=./libmtrace.so ./leak
==mtrace== malloc=6 calloc=1 realloc=2 free=1
==mtrace== total=1053186 bytes, peak=1052714 bytes
==mtrace== still reachable or leaked: 5 block(s), 1052714 bytes
==mtrace==         14 bytes at 0x6179d8b6a440 from leak+0x1231
==mtrace==         14 bytes at 0x6179d8b6a460 from leak+0x1231
==mtrace==         14 bytes at 0x6179d8b6a480 from leak+0x1231
==mtrace==       4096 bytes at 0x6179d8b6a4d0 from libc.so.6+0x7eaf4
==mtrace==    1048576 bytes at 0x7f7aa5eff010 from leak+0x12c1
realloc(z, 0) returned (nil)
$ addr2line -e leak -f -i 0x1231 0x12c1
dup_name
/home/ypp/c_sysprog_lab/p6b/pe/leak.c:8
main
/home/ypp/c_sysprog_lab/p6b/pe/leak.c:23
```

（位址每次執行不同；報告與程式輸出的先後取決於 stdout 是否為終端機。）先別急著看解答，**自己先把這份輸出解釋清楚**，它藏了三個值得想的地方：

- 原始碼明明呼叫了 **3 次** `realloc`，報告卻只有 2 次，`malloc` 反而是 6 次而非 5 次。看 `objdump -d leak | grep 'call.*@plt'`：`realloc(NULL, 32)` 被 gcc **在 `-O0` 下**就改寫成 `malloc(32)`（builtin 語意，和 Ch 40 刪掉成對 `malloc`/`free` 是同一類事）。
- 4096 位元組來自 `libc.so.6`：那是 `printf` 替 stdout 配置的緩衝區。它「沒釋放」但不算 bug——工具要能區分 *still reachable* 和真正的洩漏（這是延伸挑戰）。
- `leak+0x1231` 是 PIE 內的偏移，所以能直接餵給 `addr2line`；位於共享程式庫的呼叫者同理。

邊界輸入：

```console
$ LD_PRELOAD=./libmtrace.so ./mt          # 4 執行緒各 100000 次 malloc/free
==mtrace== malloc=400001 calloc=4 realloc=0 free=400000
==mtrace== total=51374528 bytes, peak=5248 bytes
==mtrace== still reachable or leaked: 5 block(s), 5248 bytes
==mtrace==       4096 bytes at 0x601984cc5760 from libc.so.6+0x7eaf4
==mtrace==        288 bytes at 0x601984cc52a0 from ld-linux-x86-64.so.2+0x147fa
...
threads done
$ LD_PRELOAD=./libmtrace.so ls / > /dev/null
==mtrace== malloc=266 calloc=0 realloc=4 free=93
==mtrace== total=82618 bytes, peak=66662 bytes
==mtrace== still reachable or leaked: 175 block(s), 34388 bytes
...
```

多執行緒時計數必須**剛好** 400000 + 1（stdout 緩衝）；4 個 288 位元組來自 `ld.so`，是每個執行緒的 TLS 區塊（`pthread_create` 經 `_dl_allocate_tls` 以 `calloc` 配置，執行緒結束後被快取以便重用）。`ls` 的報告必須完整印出——`ls` 會在 `atexit` 中關閉 stderr。

### 如果你卡住了

1. **報告沒印出來，程式也沒當掉**：你在 destructor 裡寫的是 fd 2 嗎？`exit` 的順序是 `atexit` 函式（反序）→ `_dl_fini` 跑各物件的 `.fini_array`（[Ch 41](./41-c-runtime.md)），等你的 destructor 執行時，程式可能早就把 stderr 關了。想想 constructor 能先做什麼。
2. **一啟動就 SIGSEGV，backtrace 是 `malloc` → `printf` → `malloc` → ……**：你在攔截函式裡呼叫了會配置記憶體的函式。哪些函式可能配置？glibc 手冊〈Replacing malloc〉列了一些；最保險的做法是攔截路徑上**只做算術與 atomic 操作**，格式化與 I/O 一律延到報告階段，並用 `snprintf` 到堆疊緩衝區再 `write`。
3. **「不能用 malloc 記錄 malloc」那要存在哪？**：靜態陣列。固定大小的開放定址雜湊表（open addressing）就夠了：key 是指標、value 是大小與呼叫者。刪除時不能直接清成空，否則探測鏈會斷——查一下 *tombstone*。表滿了怎麼辦？規格沒要求擴充，但要誠實地在報告裡說「有 N 筆沒追蹤到」。
4. **報告階段呼叫 `dladdr`、`getenv`，它們若又呼叫 `malloc`，會改到正在走訪的表**：需要一個「我已經在追蹤器裡面了」的旗標，旗標為真時直接轉給真正的函式、不記錄。這個旗標要每個執行緒一份——為什麼不能是全域變數？
5. **`dlsym` 會呼叫 `calloc`，而你的 `calloc` 要呼叫 `dlsym` 才知道真正的 `calloc` 在哪**：雞生蛋。在初始化**進行中**時，讓配置由一塊靜態緩衝區供應（bump allocator），而且 `free` 要能認出這塊緩衝區裡的指標並忽略它。在 glibc 2.35 上你可能遇不到這個問題，用 Docker `ubuntu:20.04` 跑多執行緒測試就會遇到（見下文）。
6. **多執行緒時計數對不上，或偶爾當掉**：表格的插入/刪除要互斥。注意：你不能用 `pthread_mutex_lock` 嗎？其實可以，但思考它會不會配置記憶體、會不會在 `fork` 之後卡死；一個 `atomic_flag` 自旋鎖最單純。

### 實作步驟建議

#### Step 1：最小攔截器（只計數）

從 Ch 40 的 `mcount.c` 出發，加上 `calloc`、`realloc`。驗收：`LD_PRELOAD=./libmtrace.so ./leak` 印出四個計數，而且 `ls` 也印得出來（記得 `dup`）。

#### Step 2：先看清楚 dlsym 遞迴

寫一個「天真」版：`calloc` 第一次被呼叫時才 `dlsym(RTLD_NEXT, "calloc")`，並在解析前用 `write` 印一行。分別在 glibc 2.35 與 2.31 上對多執行緒程式跑：

```c
/* naive_calloc.c：天真地在第一次呼叫時才以 dlsym 取得真正的 calloc */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stddef.h>
#include <unistd.h>

void *calloc(size_t n, size_t size)
{
    static void *(*real)(size_t, size_t);
    if (!real) {
        static const char msg[] = "naive calloc: resolving...\n";
        ssize_t w = write(2, msg, sizeof msg - 1);
        (void) w;
        real = (void *(*)(size_t, size_t)) dlsym(RTLD_NEXT, "calloc");
    }
    return real(n, size);
}
```

```console
$ LD_PRELOAD=./n22.so ./mt; echo "exit=$?"               # glibc 2.35
naive calloc: resolving...
threads done
exit=0
$ docker run --rm -v "$PWD":/w -w /w ubuntu:20.04 sh -c \
    'LD_PRELOAD=./n20.so ./mt20 2>&1 | head -5; LD_PRELOAD=./n20.so ./mt20 >/dev/null 2>&1; echo "exit=$?"'
naive calloc: resolving...
naive calloc: resolving...
naive calloc: resolving...
naive calloc: resolving...
naive calloc: resolving...
exit=139
```

（`n20.so`、`mt20` 是在容器內以 glibc 2.31 編出的同一份原始碼，需加 `-ldl` 與 `-pthread`。）glibc 2.31 的 `dlsym` 在多執行緒程式裡為錯誤狀態呼叫 `calloc`，於是 `calloc` → `dlsym` → `calloc` → ……直到堆疊耗盡（139 = 128 + SIGSEGV）。glibc 2.34 改寫了 `dlerror` 的狀態管理，這個呼叫不見了——但你的工具應該在兩個版本上都能跑。

再看另一個常見的自殺方式：在 `malloc` 裡 `printf`。

```c
/* naive2.c：天真的追蹤器——在 malloc 裡用 printf 記錄到 stdout */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdio.h>

void *malloc(size_t size)
{
    static void *(*real)(size_t);
    if (!real)
        real = (void *(*)(size_t)) dlsym(RTLD_NEXT, "malloc");
    void *p = real(size);
    printf("malloc(%zu) = %p\n", size, p); /* 第一次 printf 要替 stdout 配置緩衝區 */
    return p;
}
```

```console
$ LD_PRELOAD=./libnaive2.so ./leak > /dev/null; echo "naive2 exit=$?"
Segmentation fault (core dumped)
naive2 exit=139
```

（同樣的程式改成 `fprintf(stderr, ...)` 在 glibc 上反而能跑，因為 stderr 無緩衝、不配置記憶體——**能跑不代表正確**，換一個 libc 或換成有緩衝的串流就炸。）

#### Step 3：靜態雜湊表記錄存活配置

實作 `record_alloc(p, size, caller)` 與 `record_free(p)`，`caller` 用 `__builtin_return_address(0)`。驗收：`leak` 的存活區塊數與位元組正確；`mt` 結束時存活位元組不隨迴圈次數成長。

#### Step 4：重入旗標與 bootstrap 緩衝區

加上每執行緒的 `in_tracer` 旗標與 `dlsym` 期間的 bump allocator。驗收：在 `ubuntu:20.04` 容器內跑多執行緒測試不會遞迴，報告最後一行顯示用了多少 bootstrap 緩衝區。

#### Step 5：報告、多執行緒與真實程式

destructor 走訪表格、`dladdr` 轉成「模組+偏移」、`MTRACE_TOP` 控制筆數。驗收：`mt` 計數正確、`ls`、`python3 -c 'print(1)'` 都能完整跑完並印出報告。

### 完整參考解答

**寫完再看！不要偷看**，否則學不到東西。

<details>
<summary>點開參考實作：mtrace.c</summary>

```c
/* mtrace.c：LD_PRELOAD 的 malloc/free/calloc/realloc 追蹤器
 *
 * 建置：gcc -std=c11 -Wall -Wextra -O2 -fPIC -shared -ftls-model=initial-exec \
 *           mtrace.c -o libmtrace.so
 * 使用：LD_PRELOAD=./libmtrace.so ./prog
 * 環境變數：MTRACE_TOP=<n> 報告最多列出幾筆洩漏 (預設 10)
 */
#define _GNU_SOURCE /* RTLD_NEXT、dladdr (glibc 2.35 以前需要) */
#include <dlfcn.h>
#include <stdarg.h>
#include <stdatomic.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

/* ---------- 1. 真正的配置函式 ---------- */
static void *(*real_malloc)(size_t);
static void (*real_free)(void *);
static void *(*real_calloc)(size_t, size_t);
static void *(*real_realloc)(void *, size_t);

/* ---------- 2. dlsym 期間的臨時配置器 ----------
 * 某些 C 函式庫 (例如 glibc 2.34 以前，在多執行緒程式中) 的 dlsym 內部會呼叫
 * calloc/malloc。那時 real_calloc 還是 NULL，若轉回自己就無窮遞迴。
 * 解法：初始化期間的配置改由一塊靜態緩衝區供應，且永不歸還。 */
static _Alignas(max_align_t) unsigned char boot_buf[4096];
static size_t boot_used;

static int from_boot(const void *p)
{
    return (const unsigned char *) p >= boot_buf &&
           (const unsigned char *) p < boot_buf + sizeof boot_buf;
}

static void *boot_alloc(size_t n)
{
    size_t a = _Alignof(max_align_t);
    size_t off = (boot_used + a - 1) & ~(a - 1);
    if (n > sizeof boot_buf - off)
        return NULL;
    boot_used = off + n;
    return boot_buf + off; /* 靜態儲存期，內容已是 0，calloc 也適用 */
}

static atomic_int init_state; /* 0 = 未開始，1 = 進行中，2 = 完成 */

static void resolve_real(void)
{
    int expected = 0;
    if (!atomic_compare_exchange_strong(&init_state, &expected, 1)) {
        while (atomic_load(&init_state) != 2) /* 其他執行緒正在初始化 */
            ;
        return;
    }
    real_malloc = (void *(*)(size_t)) dlsym(RTLD_NEXT, "malloc");
    real_free = (void (*)(void *)) dlsym(RTLD_NEXT, "free");
    real_calloc = (void *(*)(size_t, size_t)) dlsym(RTLD_NEXT, "calloc");
    real_realloc = (void *(*)(void *, size_t)) dlsym(RTLD_NEXT, "realloc");
    if (!real_malloc || !real_free || !real_calloc || !real_realloc)
        _exit(127);
    atomic_store(&init_state, 2);
}

/* ---------- 3. 存活配置表：靜態的開放定址雜湊表 ----------
 * 不能用 malloc 來記錄 malloc，所以表格是固定大小的靜態陣列。 */
#define TABLE_BITS 16
#define TABLE_SIZE (1u << TABLE_BITS) /* 65536 格，最多追蹤這麼多筆存活配置 */
#define TOMBSTONE ((void *) 1)        /* 刪除標記：不能直接清成 NULL，否則探測鏈會斷 */

struct slot {
    void *ptr;    /* NULL = 空，TOMBSTONE = 已刪除 */
    size_t size;
    void *caller; /* 呼叫 malloc 的位址，供報告定位 */
};
static struct slot table[TABLE_SIZE];
static atomic_flag table_lock = ATOMIC_FLAG_INIT;

static struct {
    unsigned long n_malloc, n_calloc, n_realloc, n_free;
    unsigned long n_free_unknown, n_overflow;
    size_t cur_bytes, peak_bytes, total_bytes;
} st;

static void lock(void)
{
    while (atomic_flag_test_and_set_explicit(&table_lock, memory_order_acquire))
        ;
}
static void unlock(void)
{
    atomic_flag_clear_explicit(&table_lock, memory_order_release);
}

static size_t hash_ptr(const void *p)
{
    uintptr_t x = (uintptr_t) p >> 4; /* malloc 傳回的位址至少 16 位元組對齊，低 4 位元恆為 0 */
    x *= 0x9E3779B97F4A7C15u;         /* Fibonacci hashing：2^64 / 黃金比例 */
    return (size_t) (x >> (64 - TABLE_BITS));
}

static void record_alloc(void *p, size_t size, void *caller)
{
    if (!p)
        return;
    lock();
    size_t i = hash_ptr(p);
    for (size_t n = 0; n < TABLE_SIZE; n++, i = (i + 1) & (TABLE_SIZE - 1)) {
        if (table[i].ptr == NULL || table[i].ptr == TOMBSTONE) {
            table[i] = (struct slot){p, size, caller};
            st.cur_bytes += size;
            st.total_bytes += size;
            if (st.cur_bytes > st.peak_bytes)
                st.peak_bytes = st.cur_bytes;
            unlock();
            return;
        }
    }
    st.n_overflow++; /* 表滿了：這筆配置不追蹤，報告會註明 */
    unlock();
}

/* 傳回 1 表示找到並移除 */
static int record_free(void *p)
{
    lock();
    size_t i = hash_ptr(p);
    for (size_t n = 0; n < TABLE_SIZE; n++, i = (i + 1) & (TABLE_SIZE - 1)) {
        if (table[i].ptr == NULL)
            break;
        if (table[i].ptr == p) {
            st.cur_bytes -= table[i].size;
            table[i].ptr = TOMBSTONE;
            unlock();
            return 1;
        }
    }
    st.n_free_unknown++;
    unlock();
    return 0;
}

/* ---------- 4. 重入防護 ----------
 * 報告階段會呼叫 snprintf、dladdr 等函式，它們可能再呼叫 malloc。
 * 每個執行緒一個旗標：已在追蹤器內部時，直接轉給真正的函式，不再記錄。 */
static _Thread_local int in_tracer;

/* ---------- 5. 介入函式 ---------- */
void *malloc(size_t size)
{
    if (atomic_load(&init_state) != 2) {
        if (atomic_load(&init_state) == 1) /* dlsym 正在執行，自己呼叫回來 */
            return boot_alloc(size);
        resolve_real();
    }
    if (in_tracer)
        return real_malloc(size);
    in_tracer = 1;
    void *p = real_malloc(size);
    lock();
    st.n_malloc++;
    unlock();
    record_alloc(p, size, __builtin_return_address(0));
    in_tracer = 0;
    return p;
}

void *calloc(size_t n, size_t size)
{
    if (atomic_load(&init_state) != 2) {
        if (atomic_load(&init_state) == 1)
            return (size && n > SIZE_MAX / size) ? NULL : boot_alloc(n * size);
        resolve_real();
    }
    if (in_tracer)
        return real_calloc(n, size);
    in_tracer = 1;
    void *p = real_calloc(n, size);
    lock();
    st.n_calloc++;
    unlock();
    if (p) /* real_calloc 成功代表 n * size 沒有溢位 */
        record_alloc(p, n * size, __builtin_return_address(0));
    in_tracer = 0;
    return p;
}

void free(void *ptr)
{
    if (!ptr || from_boot(ptr)) /* free(NULL) 合法；臨時緩衝區不歸還 */
        return;
    if (atomic_load(&init_state) != 2)
        resolve_real();
    if (in_tracer) {
        real_free(ptr);
        return;
    }
    in_tracer = 1;
    lock();
    st.n_free++;
    unlock();
    record_free(ptr);
    real_free(ptr);
    in_tracer = 0;
}

void *realloc(void *ptr, size_t size)
{
    if (atomic_load(&init_state) != 2) {
        if (atomic_load(&init_state) == 1 && !ptr)
            return boot_alloc(size);
        resolve_real();
    }
    if (ptr && from_boot(ptr)) { /* 從臨時緩衝區搬到真正的 heap */
        void *q = malloc(size);
        if (q) {
            size_t avail = (size_t) (boot_buf + sizeof boot_buf - (unsigned char *) ptr);
            memcpy(q, ptr, size < avail ? size : avail);
        }
        return q;
    }
    if (in_tracer)
        return real_realloc(ptr, size);
    in_tracer = 1;
    lock();
    st.n_realloc++;
    unlock();
    /* 先嘗試 realloc；成功後才更新紀錄。失敗時舊區塊仍有效，紀錄不動 */
    void *q = real_realloc(ptr, size);
    if (q || size == 0) {
        if (ptr)
            record_free(ptr);
        record_alloc(q, size, __builtin_return_address(0));
    }
    in_tracer = 0;
    return q;
}

/* ---------- 6. 報告 ---------- */
static int log_fd = -1;

__attribute__((constructor)) static void mtrace_init(void)
{
    log_fd = dup(STDERR_FILENO); /* 程式可能在 atexit 中關閉 fd 2 */
}

static void say(const char *fmt, ...) __attribute__((format(printf, 1, 2)));

static void say(const char *fmt, ...)
{
    char buf[256];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    if (n < 0)
        return;
    if ((size_t) n >= sizeof buf)
        n = sizeof buf - 1;
    ssize_t w = write(log_fd, buf, (size_t) n);
    (void) w;
}

__attribute__((destructor)) static void mtrace_report(void)
{
    in_tracer = 1; /* 報告期間的配置不再記錄 */
    unsigned top = 10;
    const char *env = getenv("MTRACE_TOP");
    if (env)
        top = (unsigned) strtoul(env, NULL, 10);

    say("==mtrace== malloc=%lu calloc=%lu realloc=%lu free=%lu\n", st.n_malloc,
        st.n_calloc, st.n_realloc, st.n_free);
    say("==mtrace== total=%zu bytes, peak=%zu bytes\n", st.total_bytes,
        st.peak_bytes);

    unsigned long leaks = 0;
    for (size_t i = 0; i < TABLE_SIZE; i++)
        if (table[i].ptr && table[i].ptr != TOMBSTONE)
            leaks++;
    say("==mtrace== still reachable or leaked: %lu block(s), %zu bytes\n",
        leaks, st.cur_bytes);

    unsigned shown = 0;
    for (size_t i = 0; i < TABLE_SIZE && shown < top; i++) {
        struct slot *s = &table[i];
        if (!s->ptr || s->ptr == TOMBSTONE)
            continue;
        Dl_info info;
        if (dladdr(s->caller, &info) && info.dli_fname) {
            const char *base = strrchr(info.dli_fname, '/');
            say("==mtrace==   %8zu bytes at %p from %s+%#lx\n", s->size, s->ptr,
                base ? base + 1 : info.dli_fname,
                (unsigned long) ((uintptr_t) s->caller - (uintptr_t) info.dli_fbase));
        } else {
            say("==mtrace==   %8zu bytes at %p from %p\n", s->size, s->ptr,
                s->caller);
        }
        shown++;
    }
    if (st.n_free_unknown)
        say("==mtrace== free() of untracked pointer: %lu time(s)\n",
            st.n_free_unknown);
    if (st.n_overflow)
        say("==mtrace== table full, %lu allocation(s) not tracked\n",
            st.n_overflow);
    if (boot_used)
        say("==mtrace== bootstrap buffer used during dlsym: %zu bytes\n",
            boot_used);
}
```

**解答說明**：

- **初始化狀態機**（`init_state` 0/1/2）：0 → 1 的轉換用 CAS，只有一個執行緒會呼叫 `dlsym`；狀態為 1 時，任何配置（包括 `dlsym` 自己呼叫回來的）都由 `boot_buf` 供應。其他執行緒若在狀態 1 時進來也拿 bootstrap 記憶體，不會卡住。`free` 以 `from_boot` 認出這些指標並忽略；`realloc` 遇到它們就搬到真正的 heap。在 glibc 2.31 容器內實測，報告最後一行是 `bootstrap buffer used during dlsym: 32 bytes`——`dlsym` 確實呼叫了一次 32 位元組的配置；在 glibc 2.35 上這一行不會出現。
- **`_Thread_local` 重入旗標與 `-ftls-model=initial-exec`**：存取共享程式庫裡的 TLS 變數，預設的 general-dynamic 模型會呼叫 `__tls_get_addr`，在某些情況下它自己可能配置記憶體（為新的 TLS 區塊）。`LD_PRELOAD` 的物件在啟動時就載入，可以安全使用 initial-exec 模型，存取只是一條 `%fs` 相對的 `mov`。若改用全域旗標，執行緒 A 在報告階段設的旗標會讓執行緒 B 的配置漏記。
- **雜湊**：malloc 傳回的位址至少 16 位元組對齊，先右移 4 位去掉恆為 0 的低位，再乘上 2⁶⁴/φ（`0x9E3779B97F4A7C15`，Knuth 的 Fibonacci hashing）取高 16 位，讓連續配置的位址分散到表中。65536 格 × 24 位元組 = 1.5 MiB 的 `.bss`，只在被碰到的頁面才佔實體記憶體。
- **鎖**：`atomic_flag` 自旋鎖。代價實測：`mt`（4 執行緒 × 10 萬次配置）不掛追蹤器 5 次共 0.016 s，掛上後 0.547 s，慢了約 30 倍，主要是 4 個執行緒搶同一把鎖。正式的工具會用分段鎖或每執行緒緩衝區（延伸挑戰）。
- **`realloc` 的順序**：先呼叫真正的 `realloc`，成功後才刪舊紀錄、加新紀錄；失敗時舊區塊依標準仍有效，紀錄保持不動。`realloc(p, 0)` 在 glibc 上會釋放 `p` 並傳回 NULL（C23 已把 size 0 的 `realloc` 列為未定義行為），所以 `size == 0` 時也要刪掉舊紀錄。
- **`calloc` 溢位**：bootstrap 路徑自己檢查 `n > SIZE_MAX / size`；正常路徑交給真正的 `calloc`，它成功就代表沒溢位，`n * size` 可以安全計算。
- **報告**：`say()` 用 `vsnprintf` 到 256 位元組的堆疊緩衝區再 `write` 到 constructor 裡 `dup` 出來的 fd；`dladdr` 把呼叫者位址轉成「模組名稱 + 相對載入基底的偏移」，對 PIE 和共享程式庫都能直接餵給 `addr2line`。

</details>

### 測試用例

| 輸入 | 預期輸出 | 說明 |
|---|---|---|
| `./leak` | 存活 5 塊，含 3×14、1 MiB、stdout 緩衝 4096 | 正常路徑 |
| `./mt`（4 執行緒） | `malloc=400001 free=400000`，存活位元組不隨迴圈成長 | 計數無 data race |
| `ls / > /dev/null` | 報告完整印出 | 程式在 `atexit` 中關閉 stderr |
| `python3 -c 'print("py ok")'` | 數千次呼叫、報告完整 | 大量 `realloc`，壓力測試 |
| 多執行緒程式 @ glibc 2.31 | 不遞迴；報告顯示 bootstrap 用量 | `dlsym` 內部呼叫 `calloc` |
| `free(NULL)`、`realloc(NULL, n)`、`realloc(p, 0)` | 不當掉、紀錄一致 | 標準語意邊界 |
| 存活配置超過 65536 筆 | 報告註明「table full, N allocation(s) not tracked」 | 誠實面對容量上限 |

### 延伸挑戰（加分）

- **呼叫堆疊**：用 libgcc 的 `_Unwind_Backtrace`（或 `backtrace(3)`，注意它第一次呼叫時會載入 libgcc_s 並配置記憶體）記錄 4 層呼叫者，並依「相同呼叫堆疊」彙總洩漏，像 Valgrind 的 *loss record*。
- **still reachable vs definitely lost**：在 destructor 掃描 `.data`、`.bss` 與存活區塊本身，看有沒有指標指向每個存活區塊——這就是保守式垃圾收集器（Boehm GC）的標記階段。
- **補齊介面**：`posix_memalign`、`aligned_alloc`、`memalign`、`valloc`、`malloc_usable_size`。目前它們配置的區塊被 `free` 時會出現「free() of untracked pointer」。glibc 手冊〈Replacing malloc〉列出了替換時必須一併提供的函式。
- **雙重釋放偵測**：`record_free` 找不到指標時，區分「從沒見過」與「剛被釋放過」（保留一個小的最近釋放環形緩衝區）。
- **降低鎖競爭**：每個執行緒先寫入自己的事件緩衝區，滿了再批次合併進全域表。

## 第二部分：為 Ch 38 的 bf.c 加上 fold_loop

> 如果你對 Brainfuck、`bf.c` 的 IR（`struct op`）、JIT 的 backpatching 與 W^X 還不熟，先回看 [Ch 38 — 編譯器實作](./38-compiler-construction.md) 的「一份前端，四種執行方式：bf.c」與「JIT 逐位元組解剖」兩節。本部分直接以那份 206 行的 `bf.c` 為起點。

### 任務規格

| 項目 | 規格 |
|---|---|
| 起點 | Ch 38 的 `bf.c`（IR：`ADD`、`MOVE`、`OUT`、`IN`、`JZ`、`JNZ`、`CLEAR`；後端 `run_ir` 與 `run_jit`） |
| 新增選項 | `-O2`：在 `-O` 的收縮與 `[-]` 清零之外，再做迴圈乘法改寫；`-t`：在 stderr 印出執行時間與 op 數 |
| 改寫條件 | 迴圈主體只有 `ADD`/`MOVE`；指標淨位移為 0；`p[0]` 每圈的淨增量為 −1（mod 256 即 255） |
| 改寫結果 | 對主體中每個 `off ≠ 0`、每圈增量 `k ≠ 0` 的格，產生 `MUL off, k`（語意 `p[off] += p[0] * k`，mod 256），最後一個 `CLEAR` |
| 後端 | `run_ir` 與 `run_jit` 都要支援 `MUL`；JIT 只用 x86-64 指令，手工編碼 |
| 正確性 | 對 `hello.b`、`hanoi.b`、`mandelbrot.b`，`-O`/`-O2` × `-i`/`-j` 六種組合的輸出與 `bf -n`（逐字元直譯）逐位元組相同 |
| 量測 | 對 `mandelbrot.b`、`hanoi.b` 報告：`-O2` 相對 `-O` 的 op 數與時間變化、JIT 相對直譯器的加速比 |

### 期望輸出範例

```console
$ printf '+++++++[>++++++++<-]>.' > t1.b      # 7 × 8 = 56 = '8'
$ ./bf2 -O2 -i t1.b
8
$ printf '++++++++++++++++[>++++++++++++++++<-]>.' > t2.b   # 16 × 16 = 256 ≡ 0
$ ./bf2 -O2 -j t2.b | od -An -tu1
   0
$ ./bf2 -O2 -t -j mandelbrot.b > /dev/null
[j O2] 2915 ops, 0.469 s
```

邊界輸入：

```console
$ printf '[-<+>]+++++++++++++++++++++++++++++++++.' > t5.b    # 在紙帶最左端、p[0] == 0
$ ./bf2 -O2 -j t5.b
!
```

原迴圈因為 `p[0] == 0` 一圈都不跑；改寫後的 `MUL -1, 1` **仍然會讀寫 `p[-1]`**——在紙帶最左邊，那是陣列外。結果必須是 `!`（33），而不是 SIGSEGV 或改到別的變數。

### 如果你卡住了

1. **辨識迴圈要在哪裡做？**：在 `parse()` 處理 `]` 的地方。此時 `ops[start]` 是配對的 `JZ`，`ops[start+1 .. nops-1]` 就是剛收縮完的迴圈主體；`[-]` 的清零判斷也在同一處，順序上先試清零、再試乘法。
2. **怎麼算每一格的增量？**：沿主體走一遍，維護目前偏移 `off`；遇到 `MOVE` 就 `off += arg`，遇到 `ADD` 就把 `arg` 累加到 `delta[off]`。偏移範圍不大，用一個以 `MAXOFF` 為中心的小陣列即可；超出範圍就放棄改寫（正確性優先）。
3. **`p[0]` 每圈減 1 的條件為什麼必要？**：迴圈跑的圈數必須恰好是 `p[0]` 的初值，`p[off] += k * p[0]` 才成立。若每圈減 2，圈數取決於奇偶，甚至永不結束——想想 `p[0] = 3` 時 `[-->+<]` 會怎樣（3、1、255、253……永遠不會剛好歸零）。
4. **邊界例子會當掉**：`MUL` 在 `p[0] == 0` 時仍會碰到 `p[off]`。兩種解法：每個 `MUL` 群組前加一個判斷，或讓紙帶兩端各多配置一段緩衝（原文 jserv 的做法）。後者讓熱迴圈裡沒有多餘的分支。
5. **x86-64 怎麼寫「`p[off] += p[0] * k`」？**：三條指令就夠：`movzbl (%rbx),%eax`（零延伸讀 `p[0]`）、`imul $k,%eax,%eax`、`add %al,off(%rbx)`。只取 `%al` 就自動完成 mod 256。編碼不確定時，寫成 `.s` 用 `as` 組譯再 `objdump -d` 對照（下面的解答說明就是這樣核對的）。

### 實作步驟建議

#### Step 1：加上 `-t` 計時，量出基準

以 `clock_gettime(CLOCK_MONOTONIC)` 包住 `run_ir()`/`run_jit()`，結束前 `fflush(stdout)` 再取時間（否則輸出還在緩衝區裡沒算到）。用 `taskset -c N` 綁核心跑 3 次。驗收：重現 Ch 38 表格中 `-O -i` 與 `-O -j` 的數量級。

#### Step 2：IR 加上 `MUL` 與 `fold_loop()`

`struct op` 多一個 `off` 欄位。寫 `fold_loop(start)`，可改寫就把 `nops` 退回 `start` 並輸出 `MUL`… `CLEAR`，傳回 1。驗收：`-O2` 的 op 數比 `-O` 少（`mandelbrot.b`：3867 → 2915）。

#### Step 3：直譯器支援 `MUL`

`case MUL: p[o.off] += (uint8_t) (*p * o.arg);`，並處理紙帶邊界。驗收：`t1.b`～`t6.b` 與三個測試程式的 `-O2 -i` 輸出與 `bf -n` 相同。

#### Step 4：JIT 支援 `MUL`

在 `run_jit()` 的 `switch` 加一個 case，產生上面三條指令。驗收：`-O2 -j` 六種組合輸出全部相同。

#### Step 5：量測與解讀

跑完 `mandelbrot.b`、`hanoi.b` 的四種組合，算加速比，並試著解釋為什麼兩個程式從 `fold_loop` 得到的好處差這麼多。

### 完整參考解答

**寫完再看！**

<details>
<summary>點開參考實作：bf2.c（以 Ch 38 的 bf.c 為基礎，標出差異）</summary>

和 Ch 38 `bf.c` 的差異：`struct op` 多了 `off`；新增 `MUL` 與 `fold_loop()`；`parse()` 的 `]` 分支在清零之後嘗試 `fold_loop`；`run_ir()`/`run_jit()` 多一個 case；紙帶改成兩端各留 `MARGIN` 格的 `tape_mem`；`main()` 改為解析 `-O`/`-O2`/`-t`。為了篇幅，拿掉了 Ch 38 的 `-n` 與 `-c` 後端（驗證時用 Ch 38 原本的 `bf -n` 當參考答案）。

```c
/* bf2.c：以 Ch 38 的 bf.c 為基礎，加上 -O2 的迴圈乘法改寫 (fold_loop)
 * 與 -t 計時。用法：bf2 [-O|-O2] [-t] -i|-j file */
#define _DEFAULT_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>

#define TAPE 30000                        /* Müller 原始實作的格數 */
#define MARGIN 1024                       /* MUL 可能在 *p == 0 時碰到 p[off]，左右各留緩衝 */
enum kind { ADD, MOVE, OUT, IN, JZ, JNZ, CLEAR, MUL };
struct op {
    enum kind kind;
    int arg; /* ADD: 增量 mod 256；MOVE: 位移；JZ/JNZ: 配對索引；MUL: 倍數 mod 256 */
    int off; /* MUL: 目標格相對於 p 的偏移 */
};

static struct op ops[1 << 16];
static int nops;

static void die(const char *m) { fprintf(stderr, "bf: %s\n", m); exit(1); }

static void emit(enum kind k, int arg, int opt)
{
    if (opt && nops > 0 && ops[nops - 1].kind == k && (k == ADD || k == MOVE)) {
        ops[nops - 1].arg += arg;              /* 收縮：+++ 變成 ADD 3 */
        if (k == ADD) ops[nops - 1].arg &= 255;
        if (ops[nops - 1].arg == 0) nops--;    /* +- 互相抵銷 */
        return;
    }
    if (nops == (int) (sizeof ops / sizeof ops[0])) die("program too long");
    ops[nops++] = (struct op) {k, arg, 0};
}

/* fold_loop：迴圈 ops[start] (JZ) ... ops[nops-1] 若可改寫成乘法就改寫，傳回 1。
 * 條件：主體只有 ADD/MOVE、指標淨位移為 0、p[0] 每圈恰好減 1。
 * 結果：對每個 off != 0 的格產生 MUL(off, delta)，最後 CLEAR p[0]。 */
static int fold_loop(int start)
{
    enum { MAXOFF = 64 };
    int delta[2 * MAXOFF + 1] = {0}; /* delta[MAXOFF + off] = 每圈對 p[off] 的增量 */
    int off = 0;
    for (int i = start + 1; i < nops; i++) {
        if (ops[i].kind == MOVE)
            off += ops[i].arg;
        else if (ops[i].kind == ADD)
            delta[MAXOFF + off] = (delta[MAXOFF + off] + ops[i].arg) & 255;
        else
            return 0; /* 有 I/O 或內層迴圈：不能改寫 */
        if (off < -MAXOFF || off > MAXOFF)
            return 0;
    }
    if (off != 0 || delta[MAXOFF] != 255) /* 淨位移要歸零，p[0] 每圈 -1 */
        return 0;
    nops = start;
    for (int o = -MAXOFF; o <= MAXOFF; o++)
        if (o != 0 && delta[MAXOFF + o])
            ops[nops++] = (struct op) {MUL, delta[MAXOFF + o], o};
    ops[nops++] = (struct op) {CLEAR, 0, 0};
    return 1;
}

static void parse(const char *s, int opt)
{
    int stack[1024], depth = 0;
    for (; *s; s++) {
        switch (*s) {
        case '+': emit(ADD, 1, opt); break;
        case '-': emit(ADD, 255, opt); break;
        case '>': emit(MOVE, 1, opt); break;
        case '<': emit(MOVE, -1, opt); break;
        case '.': emit(OUT, 0, 0); break;
        case ',': emit(IN, 0, 0); break;
        case '[':
            if (depth == 1024) die("nesting too deep");
            stack[depth++] = nops;
            emit(JZ, 0, 0);
            break;
        case ']': {
            if (depth == 0) die("unmatched ]");
            int start = stack[--depth];
            if (opt && nops == start + 2 && ops[start + 1].kind == ADD &&
                (ops[start + 1].arg & 1)) {      /* [-]、[+]：奇數增量必定歸零 */
                nops = start;
                emit(CLEAR, 0, 0);
                break;
            }
            if (opt >= 2 && fold_loop(start))
                break;
            ops[start].arg = nops;
            emit(JNZ, start, 0);
            break;
        }
        }
    }
    if (depth) die("unmatched [");
}

static uint8_t tape_mem[MARGIN + TAPE + MARGIN];

static void run_ir(void)                      /* 直譯 IR：跳躍目標已算好 */
{
    uint8_t *p = tape_mem + MARGIN;
    for (int pc = 0; pc < nops; pc++) {
        struct op o = ops[pc];
        switch (o.kind) {
        case ADD: *p += o.arg; break;
        case MOVE: p += o.arg; break;
        case OUT: putchar(*p); break;
        case IN: *p = (uint8_t) getchar(); break;
        case JZ: if (!*p) pc = o.arg; break;
        case JNZ: if (*p) pc = o.arg; break;
        case CLEAR: *p = 0; break;
        case MUL: p[o.off] += (uint8_t) (*p * o.arg); break;
        }
    }
}

static uint8_t code[1 << 22];
static size_t len;
static void put(const void *b, size_t n)
{
    if (len + n > sizeof code) die("code buffer full");
    memcpy(code + len, b, n);
    len += n;
}
#define PUT(...) put((const uint8_t[]) {__VA_ARGS__}, sizeof((const uint8_t[]) {__VA_ARGS__}))
static void put32(int32_t v) { put(&v, 4); }           /* x86-64 為 little-endian */
static void put_call(uintptr_t fn)
{
    uint64_t a = fn;
    PUT(0x48, 0xb8); put(&a, 8);                      /* mov rax, imm64 */
    PUT(0xff, 0xd0);                                  /* call rax */
}

static void run_jit(void)                     /* JIT：把 IR 編碼成 x86-64 機械碼 */
{
#if defined(__x86_64__)
    static int32_t after_jz[1 << 16];               /* 每個 JZ 的 rel32 之後的位置 */
    PUT(0x53);                                      /* push rbx */
    PUT(0x48, 0x89, 0xfb);                          /* mov rbx, rdi */
    for (int i = 0; i < nops; i++) {
        struct op o = ops[i];
        switch (o.kind) {
        case ADD: PUT(0x80, 0x03, (uint8_t) o.arg); break;          /* add byte [rbx], imm8 */
        case MOVE: PUT(0x48, 0x81, 0xc3); put32(o.arg); break;      /* add rbx, imm32 */
        case OUT: PUT(0x0f, 0xb6, 0x3b);                             /* movzx edi, byte [rbx] */
                  put_call((uintptr_t) putchar); break;
        case IN: put_call((uintptr_t) getchar); PUT(0x88, 0x03); break; /* mov [rbx], al */
        case CLEAR: PUT(0xc6, 0x03, 0x00); break;                    /* mov byte [rbx], 0 */
        case MUL:
            PUT(0x0f, 0xb6, 0x03);                                   /* movzx eax, byte [rbx] */
            PUT(0x69, 0xc0); put32(o.arg);                           /* imul eax, eax, imm32 */
            PUT(0x00, 0x83); put32(o.off);                           /* add [rbx + disp32], al */
            break;
        case JZ:                                                     /* cmp byte [rbx],0; je ? */
            PUT(0x80, 0x3b, 0x00, 0x0f, 0x84); put32(0);
            after_jz[i] = (int32_t) len;
            break;
        case JNZ: {                                                  /* cmp byte [rbx],0; jne body */
            int32_t body = after_jz[o.arg];
            PUT(0x80, 0x3b, 0x00, 0x0f, 0x85); put32(body - (int32_t) (len + 4));
            int32_t fwd = (int32_t) len - body;                      /* 回填 JZ 的目的地 */
            memcpy(code + body - 4, &fwd, 4);
            break;
        }
        }
    }
    PUT(0x5b, 0xc3);                                /* pop rbx; ret */

    void *mem = mmap(NULL, len, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (mem == MAP_FAILED) die("mmap");
    memcpy(mem, code, len);
    if (mprotect(mem, len, PROT_READ | PROT_EXEC) != 0) die("mprotect");  /* W^X */
    ((void (*)(uint8_t *)) mem)(tape_mem + MARGIN);
    munmap(mem, len);
#else
    die("JIT supports x86-64 only");
#endif
}

static char *slurp(const char *path)
{
    static char buf[1 << 20];
    FILE *f = fopen(path, "rb");
    if (!f) die("cannot open input");
    size_t n = fread(buf, 1, sizeof buf - 1, f);
    fclose(f);
    buf[n] = '\0';
    return buf;
}

int main(int argc, char **argv)
{
    int ai = 1, opt = 0, timing = 0;
    for (; ai < argc - 2; ai++) {
        if (!strcmp(argv[ai], "-O")) opt = 1;
        else if (!strcmp(argv[ai], "-O2")) opt = 2;
        else if (!strcmp(argv[ai], "-t")) timing = 1;
        else die("usage: bf2 [-O|-O2] [-t] -i|-j file");
    }
    if (ai != argc - 2) die("usage: bf2 [-O|-O2] [-t] -i|-j file");
    const char *mode = argv[ai];
    parse(slurp(argv[ai + 1]), opt);
    struct timespec t0, t1;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    if (!strcmp(mode, "-i")) run_ir();
    else if (!strcmp(mode, "-j")) run_jit();
    else die("unknown mode");
    fflush(stdout);
    clock_gettime(CLOCK_MONOTONIC, &t1);
    if (timing)
        fprintf(stderr, "[%s O%d] %d ops, %.3f s\n", mode + 1, opt, nops,
                (double) (t1.tv_sec - t0.tv_sec) + (t1.tv_nsec - t0.tv_nsec) / 1e9);
    return 0;
}
```

**解答說明**：

- **`fold_loop` 的判斷**：`delta[MAXOFF]`（偏移 0）必須是 255；所有 `ADD` 已在 `emit()` 中 `& 255`，所以 `-` 本來就是 255，`--+` 收縮後也是 255。主體出現 `OUT`/`IN` 或內層迴圈（`JZ`/`JNZ`/`CLEAR` 都不是 `ADD`/`MOVE`）就放棄。注意內層的 `[-]` 已被收縮成 `CLEAR`，所以 `[>[-]<-]` 這種「外層每圈清一次」的迴圈不會被改寫——它的語意其實是「若 `p[0] != 0` 則 `p[1] = 0`」，也可以化簡，留作延伸挑戰。
- **`MARGIN` 而不是判斷**：`MUL` 在 `p[0] == 0` 時寫入 `p[off] += 0`，值不變，唯一的風險是位址越界。紙帶兩端各留 1024 格（`fold_loop` 限制 `|off| ≤ 64`，綽綽有餘），熱迴圈裡就不必多一個分支。這是和原文 jserv 的 `bf.c` 相同的取捨。
- **JIT 編碼**（以 `as` 組譯後 `objdump -d` 核對）：

  ```
  0f b6 03             movzbl (%rbx),%eax
  69 c0 08 00 00 00    imul   $0x8,%eax,%eax      ← 我們固定用 imm32 形式
  00 83 01 00 00 00    add    %al,0x1(%rbx)       ← 固定用 disp32 形式
  ```

  `as` 會為小常數選短編碼（`6b c0 08`、`00 43 01`）；和 Ch 38 的 `MOVE` 一樣，JIT 為了簡單一律用 32 位元欄位。`add r/m8, r8` 的 opcode 是 `0x00`，ModRM `0x83` = mod 10（disp32）、reg 000（`%al`）、r/m 011（`%rbx`）。
- **正確性驗證**：`hello.b`、`hanoi.b`、`mandelbrot.b` 在 `-O`/`-O2` × `-i`/`-j` 六種組合下的 `md5sum` 與 `bf -n` 完全相同；`t1`～`t6` 的手寫邊界測試（乘積溢位成 0、`p[0] = 255` 跑 255 圈、多目標格、紙帶最左端 `p[0] == 0`）也全部一致。

</details>

### 量測結果與解讀

`taskset -c 5`，每組跑 3 次（WSL2，1 分鐘平均負載約 1.0–1.2，背景未控制，只看比例）：

| 程式 | 組態 | op 數 | 直譯 `-i` | JIT `-j` | JIT 加速比 |
|---|---|---:|---:|---:|---:|
| `mandelbrot.b` | `-O` | 3867 | 9.78–9.97 s | 0.578–0.601 s | ≈ 17× |
| `mandelbrot.b` | `-O2` | 2915 | 9.03–9.07 s | 0.463–0.477 s | ≈ 19.5× |
| `hanoi.b` | `-O` | 14853 | 0.870–0.882 s | 0.081 s | ≈ 11× |
| `hanoi.b` | `-O2` | 9841 | 0.255–0.257 s | 0.034–0.035 s | ≈ 7.4× |

能讀出的事：

1. **`fold_loop` 的效益高度依賴程式**：`hanoi.b` 的直譯器快了 3.4 倍、JIT 快了 2.4 倍；`mandelbrot.b` 只快 8%（直譯）與 25%（JIT）。差別應在於熱點迴圈的形狀：`hanoi.b` 的 op 數因改寫少了三分之一，表示它有大量 `[->+<]` 型的搬運迴圈；`mandelbrot.b` 的熱點則多是含內層迴圈的算術，不符合改寫條件（這是由 op 數變化與時間比例推得的解讀，沒有用 profiler 逐迴圈驗證）。**op 數減少不等於執行的 op 數減少**——靜態 op 數 3867→2915 少了 25%，但時間只少 8%。這和 Ch 38 「收縮是最划算的最佳化」不矛盾：收縮作用在每一個熱迴圈上，乘法改寫只作用在特定形狀的迴圈上。
2. **JIT 對直譯器的加速比在 7–20 倍之間**，和 Ch 38 量到的 13–16 倍同數量級。加速比在 `hanoi.b -O2` 反而下降，因為 `fold_loop` 已經把直譯器最痛的部分（大量重複分派）消掉了，剩下的工作中 JIT 能省的比例變小。
3. **和 gcc 比**：Ch 38 量到 `gcc -O2` 跑翻譯出的 C 是 0.56 s；我們的 JIT 加上 `fold_loop` 在本機是 0.46–0.48 s。由於量測是在不同時間、未控制背景負載下進行的，只能說「同一個數量級、互有勝負」，不能下「JIT 贏過 gcc」的結論；要比請在同一次 session 交錯量測。

### 測試用例

| 輸入 | 預期輸出 | 說明 |
|---|---|---|
| `+++++++[>++++++++<-]>.` | `8`（56） | 最基本的乘法改寫 |
| `++++++++++++++++[>++++++++++++++++<-]>.` | 位元組 0 | 16 × 16 = 256 ≡ 0 (mod 256) |
| `-[>+<-]>.` | 位元組 255 | `p[0]` 從 255 起算，跑 255 圈 |
| `++++++++[>++++<-]>[<++>>+++<-]<.>>.` | 64、96 | 一個迴圈同時寫左右兩格，偏移為負 |
| `[-<+>]` 接 33 個 `+` 再 `.` | `!`（33） | 紙帶最左端、`p[0] == 0`，`MUL` 碰到 `p[-1]` |
| `++[-->+<]>.`（`p[0]` 每圈 −2） | 位元組 1；`-O2` 與 `-O` 的 op 數相同（都是 9） | 改寫條件不成立，不得改寫 |
| `mandelbrot.b`、`hanoi.b` | 六種組合 `md5sum` 一致 | 真實程式回歸測試 |

### 延伸挑戰（加分）

- **偏移定址**：`MOVE 1; ADD 8; MOVE -1` 可以直接編成 `addb $8, 1(%rbx)`，不必移動 `%rbx`。在 IR 給 `ADD` 也加上 `off`，讓 `MOVE` 延到真的需要時才做（原文稱為 offset 最佳化）。
- **常數傳遞**：`CLEAR; ADD 10` 合成 `SET 10`；`CLEAR` 之後緊接的 `JZ` 必定跳過，整個迴圈可以刪掉。
- **掃描迴圈**：`[>]`、`[<]` 改成 `memchr`／`memrchr` 式的掃描指令，在 JIT 裡可以用 SSE2 一次比 16 格。
- **更多 `fold_loop`**：讓外層迴圈的主體允許出現 `CLEAR`（例如 `[>[-]<-]` → 「若 `p[0]` 則 `p[1] = 0`」），並證明改寫的正確條件。
- **ARM64 後端**：仿照 Ch 38 的 `jit_add.c`，替 `run_jit` 寫一個 AArch64 版本，記得 `__builtin___clear_cache`，在 `qemu-aarch64` 上驗證。

## 自我檢核

- [ ] 不看解答，能說出追蹤器報告為什麼要寫到 constructor 裡 `dup` 出來的 fd，並畫出 `exit` 時各收尾函式的執行順序？
- [ ] 能解釋 bootstrap 緩衝區解決的是什麼雞生蛋問題，以及為什麼在 glibc 2.35 上看不到它而在 2.31 上會遇到？
- [ ] 能列出在 `malloc` 攔截函式中「不能做」的三件事，並各自說明會出什麼事？
- [ ] 能說明 `fold_loop` 的三個改寫條件各自防止什麼錯誤，以及為什麼改寫後需要紙帶緩衝？
- [ ] 不查表，能寫出 `p[off] += p[0] * k` 的三條 x86-64 指令，並說明為什麼只要 `add %al` 就完成了 mod 256？
- [ ] 能說出自己的實作和參考解答的差異（例如鎖、雜湊表、`MUL` 邊界處理），並解釋各自取捨？

## 延伸閱讀

- **[glibc 手冊：Replacing malloc](https://sourceware.org/glibc/manual/latest/html_node/Replacing-malloc.html)**
  - **讀哪裡**：整節。列出替換 `malloc` 時必須提供的函式（含 `aligned_alloc`、`malloc_usable_size` 等）以及「不得呼叫哪些函式」的限制。
  - **和本練習的關聯**：第一部分規格與延伸挑戰「補齊介面」的依據。
- **[heaptrack](https://github.com/KDE/heaptrack)** — Milian Wolff（KDE）
  - **這是什麼**：生產等級的 `LD_PRELOAD` heap profiler。
  - **讀哪裡**：`src/track/heaptrack_preload.cpp`（攔截層，有同樣的 `dlsym` bootstrap 處理）與 `heaptrack_inject.cpp`（不靠 `LD_PRELOAD`、改寫 GOT 注入到執行中行程的做法，和 Ch 40 的 GOT 一節互相呼應）。
  - **前提**：會讀 C++。
- **[Adventures in JIT compilation, Part 2: an x64 JIT](https://eli.thegreenplace.net/2017/adventures-in-jit-compilation-part-2-an-x64-jit/)** — Eli Bendersky（2017）
  - **這篇說什麼**：同樣以 Brainfuck 為例，從手工編碼的 `simplejit` 走到用 asmjit 的 `optasmjit`，後者加入 set-to-zero、搬運迴圈等最佳化，作者量到比最佳化直譯器快約 4 倍。
  - **讀哪裡**：整篇；Part 1（直譯器與最佳化）可先讀。
  - **為什麼值得讀**：本練習的 `fold_loop` 對應他的 "move-data loops" 最佳化；他的量測方法與比例可以和你的數字對照。
- **[Hello, JIT World: The Joy of Simple JITs](https://blog.reverberate.org/2012/12/hello-jit-world-joy-of-simple-jits.html)** — Josh Haberman（2012）
  - **這篇說什麼**：用 DynASM 寫 Brainfuck JIT，說明為什麼小型 JIT 值得手寫。
  - **讀哪裡**：整篇。
  - **為什麼值得讀**：如果你想跳過手工編碼位元組（延伸挑戰的 ARM64 後端），DynASM 是 LuaJIT 使用的工具，這篇是最好的入門。
- **《Computer Systems: A Programmer's Perspective》（3rd ed.）** 第 7.13 節與第 9.9 節 — Bryant & O'Hallaron
  - **讀哪幾章**：7.13（三種函式介入，第一部分的理論基礎）、9.9（動態記憶體配置器的設計，理解你攔截到的那一層）。

Part 6 從原始碼一路走到行程，你已經能看懂編譯器、連結器、動態連結器與 C runtime 各自做了什麼，也親手借用過它們。Part 7 換個角度：用 0x10 道嵌入式系統考題，把整門課的指標、位元、`volatile`、ABI 知識綜合檢驗一遍。

→ [Ch 42 0x10 道嵌入式考題 I（0x01–0x08）](./42-embedded-quiz-part1.md)
