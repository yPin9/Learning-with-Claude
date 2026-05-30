# Ch 16 — BPF Skeleton：自動生成的 userspace 介面

> **目標**：理解 BPF skeleton 是什麼、`bpftool gen skeleton` 生成了哪些程式碼、skeleton 如何提供型別安全的 map/prog/link 存取，以及如何用 skeleton 讓你的 libbpf 程式更健壯。

> 如果你對 libbpf 的 open/load/attach lifecycle 還不熟，先讀 [Ch 15](./15-libbpf.md)。

## 為什麼需要 Skeleton？

沒有 skeleton 的 libbpf 程式：

```c
/* 沒有 skeleton：透過 string 名稱查找，沒有型別檢查 */
struct bpf_program *prog = bpf_object__find_program_by_name(obj, "my_func");
if (!prog) { /* ... */ }

struct bpf_map *map = bpf_object__find_map_by_name(obj, "my_map");
int map_fd = bpf_map__fd(map);
```

問題：函式名稱打錯了（"my_fnuc"）？`prog` 是 NULL，crash 在後面某處，很難 debug。

有 skeleton 的程式：

```c
/* 有 skeleton：直接用 struct member 存取，compiler 幫你檢查型別 */
skel->progs.my_func;  /* 如果 my_func 不存在，編譯就失敗 */
skel->maps.my_map;    /* 型別是 struct bpf_map *，有完整的 compiler 支援 */
```

Skeleton 把你的 `.bpf.o` 的 layout「硬編碼」進一個 C header，讓 compiler 在 compile time 就能抓到錯誤。

## `bpftool gen skeleton` 生成什麼

假設你有 `counter.bpf.o`，執行：

```bash
bpftool gen skeleton counter.bpf.o > counter.skel.h
```

生成的 `counter.skel.h` 包含：

```c
/* counter.skel.h（bpftool 自動生成，不要手動修改）*/

struct counter_bpf {
    struct bpf_object_skeleton *skeleton;
    struct bpf_object *obj;

    /* 所有 map（按名稱）*/
    struct {
        struct bpf_map *rb;           /* 你的 ringbuf map */
        struct bpf_map *pid_count;    /* 你的 hash map */
    } maps;

    /* 所有 program（按名稱）*/
    struct {
        struct bpf_program *handle_write;   /* 你的 BPF 函式 */
    } progs;

    /* 所有 link（load 後、attach 後填充）*/
    struct {
        struct bpf_link *handle_write;
    } links;

    /* rodata（const volatile 變數）*/
    struct counter_bpf__rodata {
        pid_t target_pid;
    } *rodata;

    /* bss（全域可變變數）*/
    struct counter_bpf__bss {
        u64 event_count;
    } *bss;
};

/* 生成的 lifecycle 函式 */
static inline struct counter_bpf *counter_bpf__open(void);
static inline struct counter_bpf *counter_bpf__open_opts(const struct bpf_object_open_opts *opts);
static inline int  counter_bpf__load(struct counter_bpf *obj);
static inline int  counter_bpf__attach(struct counter_bpf *obj);
static inline void counter_bpf__detach(struct counter_bpf *obj);
static inline void counter_bpf__destroy(struct counter_bpf *obj);
```

**skeleton 的三個 sections**：

| Section | 說明 | 能做什麼 |
|---|---|---|
| `maps` | map 的指標 | 用 `bpf_map__fd()` 取 fd，操作 map |
| `progs` | program 的指標 | 查看 prog info，手動 attach |
| `links` | attach 後的 link | `destroy()` 來 detach |
| `rodata` | const volatile 變數 | 在 load 前設定 BPF 程式的 config |
| `bss` | 全域可變變數 | 在 userspace 讀取 BPF 程式的全域狀態 |
| `data` | 有初始值的全域變數 | 類似 bss，但有初始值 |

## 一個完整的 skeleton 使用範例

**kernel-side（`tracer.bpf.c`）**：

```c
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>

/* rodata：userspace 可設定的 config */
const volatile pid_t target_pid = 0;

/* bss：全域狀態（userspace 可讀）*/
volatile u64 total_events = 0;

/* maps */
struct {
    __uint(type, BPF_MAP_TYPE_RINGBUF);
    __uint(max_entries, 1024 * 1024);
} rb SEC(".maps");

struct event {
    pid_t pid;
    char  comm[16];
};

SEC("tracepoint/syscalls/sys_enter_write")
int handle_write(struct trace_event_raw_sys_enter *ctx)
{
    pid_t pid = bpf_get_current_pid_tgid() >> 32;

    /* 用 rodata 做 filter */
    if (target_pid && pid != target_pid)
        return 0;

    total_events++;

    struct event *e = bpf_ringbuf_reserve(&rb, sizeof(*e), 0);
    if (!e) return 0;

    e->pid = pid;
    bpf_get_current_comm(&e->comm, sizeof(e->comm));
    bpf_ringbuf_submit(e, 0);
    return 0;
}

char LICENSE[] SEC("license") = "GPL";
```

**生成 skeleton**：

```bash
clang -g -O2 -target bpf -D__TARGET_ARCH_x86_64 -c tracer.bpf.c -o tracer.bpf.o
bpftool gen skeleton tracer.bpf.o > tracer.skel.h
```

**userspace（`tracer.c`）**：

```c
#include <stdio.h>
#include <signal.h>
#include <unistd.h>
#include <bpf/libbpf.h>
#include "tracer.skel.h"

struct event { int pid; char comm[16]; };

static volatile int stop = 0;
static void sig_handler(int s) { stop = 1; }

static int handle_event(void *ctx, void *data, size_t sz)
{
    struct event *e = data;
    printf("pid=%-6d comm=%s\n", e->pid, e->comm);
    return 0;
}

int main(int argc, char **argv)
{
    struct tracer_bpf *skel;
    struct ring_buffer *rb;
    int err;

    libbpf_set_print(NULL);

    /* open */
    skel = tracer_bpf__open();
    if (!skel) { perror("open"); return 1; }

    /* 設定 rodata（在 load 之前）*/
    if (argc > 1)
        skel->rodata->target_pid = atoi(argv[1]);

    /* load */
    err = tracer_bpf__load(skel);
    if (err) { fprintf(stderr, "load: %d\n", err); goto out; }

    /* attach */
    err = tracer_bpf__attach(skel);
    if (err) { fprintf(stderr, "attach: %d\n", err); goto out; }

    /* ring buffer */
    rb = ring_buffer__new(bpf_map__fd(skel->maps.rb), handle_event, NULL, NULL);
    if (!rb) { err = -1; goto out; }

    signal(SIGINT, sig_handler);

    while (!stop) {
        ring_buffer__poll(rb, 100);
        /* 順便讀取 bss 的 total_events */
        printf("\r[total: %llu events]", skel->bss->total_events);
        fflush(stdout);
    }

    ring_buffer__free(rb);
out:
    tracer_bpf__destroy(skel);
    return err;
}
```

## BPF Object 的嵌入（靜態嵌入 .bpf.o）

skeleton 可以把 `.bpf.o` 的內容直接嵌進 userspace binary（不需要單獨的 `.bpf.o` 檔案）：

```bash
# 生成帶嵌入的 skeleton
bpftool gen skeleton tracer.bpf.o name tracer > tracer.skel.h
```

生成的 skeleton 包含：

```c
/* .bpf.o 的內容以 bytes array 形式嵌入 */
static const char _tracer_bpf_data[] = {
    0x7f, 0x45, 0x4c, 0x46, ...  /* ELF bytes */
};
```

這樣你的工具是一個完全自包含的 binary，部署時不需要附帶 `.bpf.o`。

## 踩雷集錦

1. **每次修改 .bpf.c 後都要重新生成 skeleton**：skeleton 是根據 `.bpf.o` 的 layout 生成的，加了新 map 或改了函式名，skeleton 就過期了，要重新 `bpftool gen skeleton` 然後重新編譯 userspace

2. **`skel->bss` 和 `skel->rodata` 在 `load` 之前可能是 NULL**：某些 libbpf 版本在 open 之後 `bss` 才有效；最安全的做法是只在 open 後設 `rodata`，只在 load 後讀 `bss`

3. **skeleton 的 attach 一次 attach 所有 programs**：`tracer_bpf__attach(skel)` 會 attach skel 裡所有有 `SEC()` annotation 的 programs；如果你只想 attach 特定的，用 `bpf_program__attach(skel->progs.xxx)` 手動 attach，然後把 `links.xxx` 存好用於後續 detach

4. **bss 中的變數在多個 CPU 上有競爭**：`volatile u64 total_events` 是全域變數，多個 CPU 的 BPF program 會並發 `total_events++`，沒有 atomic 保護；用 `__sync_fetch_and_add(&total_events, 1)` 或改用 per-CPU map

## 動手練習

1. 把 Ch 15 的 `minimal.c` 改成用 skeleton，驗證 `skel->maps.rb` 和 `bpf_object__find_map_by_name(obj, "rb")` 回傳相同的 map

2. 在 `tracer.bpf.c` 裡加一個 `const volatile int max_comm_len = 8` 的 rodata 變數，在 userspace 改成 4，觀察 comm 是否被截斷

3. 用 `bpftool gen skeleton tracer.bpf.o name tracer` 生成帶嵌入的 skeleton，編譯成 static binary（`gcc -static ...`），在另一台機器上執行（確認不需要 `.bpf.o` 檔案）

## 本章重點整理

- Skeleton 把 `.bpf.o` 的 layout 轉成型別安全的 C struct，讓 compiler 在 compile time 抓到名稱錯誤
- `rodata` 是 load 前設定 BPF config 的標準方式；`bss` 是 BPF 程式的全域狀態，userspace 可以讀取
- `bpftool gen skeleton --embed` 把 `.bpf.o` 嵌進 header，讓工具成為單一 binary

## 自我檢核

- [ ] 能說出 skeleton 的 `maps`、`progs`、`links`、`rodata`、`bss` 各自的用途
- [ ] 知道為什麼修改 `.bpf.c` 後需要重新生成 skeleton
- [ ] 能解釋 `rodata` 和 `bss` 的差異（compile-time default vs runtime）
- [ ] 知道 embedded skeleton 的用途，以及它如何讓 binary 自包含

## 延伸閱讀

### 部落格

- **[BPF skeleton and BPF app lifecycle](https://nakryiko.com/posts/bpf-skeleton-and-bpf-app-lifecycle/)** — Andrii Nakryiko
  - **這篇說什麼**：skeleton 設計的動機、rodata/bss/data 的完整說明、embedded skeleton
  - **讀哪裡**：整篇；這是 skeleton 最完整的設計文件
  - **為什麼值得讀**：作者是 skeleton 的設計者

→ [Ch 17 cilium/ebpf：Go 生態系](./17-cilium-ebpf-go.md)
