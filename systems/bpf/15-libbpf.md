# Ch 15 — libbpf：現代 C 開發框架

> **目標**：掌握 libbpf 的核心 API——BPF object 的 open/load/attach/destroy lifecycle、map 的型別安全操作、ring buffer consumer、error handling——能用 libbpf 寫出生產等級的 BPF 工具。

> **環境**：libbpf 1.x（`apt install libbpf-dev`），Ubuntu 22.04。

## 為什麼選 libbpf？

libbpf 是目前 eBPF 開發的標準框架，也是 Linux kernel 自帶的 BPF loader library（位於 `tools/lib/bpf/`）。和 BCC 相比：

- **預先編譯**：你的工具 compile 一次，就能在任何有 CO-RE 支援的 kernel 上執行，不需要 kernel headers
- **輕量**：沒有 LLVM 依賴，binary 小，啟動快
- **C API**：更接近底層，控制力更強
- **skeleton**：自動生成的 C header，讓 map 存取有型別安全

缺點：比 BCC Python API 繁瑣，需要更多 boilerplate。

## 先建立直覺：BPF Object 的生命週期

```
你的 .bpf.c（kernel-side）
    │
    ▼ clang -target bpf
.bpf.o（BPF bytecode + BTF + maps definition）
    │
    ▼ bpf_object__open()
BPF object（in memory）
    │  open 後可以修改 map 設定、程式設定
    ▼ bpf_object__load()
BPF object（loaded into kernel）
    │  load 後 map 和 prog 有 fd，map 可以 pin
    ▼ bpf_program__attach()
bpf_link（attached to hook）
    │  link 存活期間，BPF program 執行
    ▼ bpf_link__destroy() + bpf_object__close()
Cleanup
```

這個 open → load → attach → destroy 的序列是 libbpf 程式的骨架。

## 完整的 libbpf 程式結構

一個完整的 libbpf eBPF 工具由兩個檔案組成：

**kernel-side（`minimal.bpf.c`）**：

```c
/* minimal.bpf.c */
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>
#include <bpf/bpf_core_read.h>

/* Map 定義（BTF-typed，現代方式）*/
struct {
    __uint(type, BPF_MAP_TYPE_RINGBUF);
    __uint(max_entries, 256 * 1024);
} rb SEC(".maps");

/* 事件結構（kernel 和 userspace 共享）*/
struct event {
    pid_t pid;
    char  comm[16];
    char  filename[256];
};

/* 強制 kernel 把這個 struct 的 BTF 資訊包含在 .bpf.o 裡 */
const struct event *unused __attribute__((unused));

SEC("tracepoint/syscalls/sys_enter_openat")
int handle_openat(struct trace_event_raw_sys_enter *ctx)
{
    struct event *e;

    e = bpf_ringbuf_reserve(&rb, sizeof(*e), 0);
    if (!e)
        return 0;

    e->pid = bpf_get_current_pid_tgid() >> 32;
    bpf_get_current_comm(&e->comm, sizeof(e->comm));
    bpf_probe_read_user_str(&e->filename, sizeof(e->filename),
                            (void *)ctx->args[1]);
    bpf_ringbuf_submit(e, 0);
    return 0;
}

char LICENSE[] SEC("license") = "GPL";
```

**Userspace（`minimal.c`）**：

```c
/* minimal.c */
#include <stdio.h>
#include <signal.h>
#include <unistd.h>
#include <bpf/libbpf.h>
#include "minimal.skel.h"  /* bpftool 自動生成 */

/* 事件結構（和 kernel-side 保持一致）*/
struct event {
    int  pid;
    char comm[16];
    char filename[256];
};

static volatile int stop = 0;
static void handle_sig(int s) { stop = 1; }

/* ring buffer 的 callback */
static int handle_event(void *ctx, void *data, size_t size)
{
    const struct event *e = data;
    printf("%-8d %-16s %s\n", e->pid, e->comm, e->filename);
    return 0;
}

int main(void)
{
    struct minimal_bpf *skel;
    struct ring_buffer *rb;
    int err;

    /* 設定 libbpf 的 error / info 輸出 */
    libbpf_set_print(NULL);  /* 靜音；或傳入 callback 輸出 */

    /* open：讀取 .bpf.o，還沒 load 到 kernel */
    skel = minimal_bpf__open();
    if (!skel) {
        fprintf(stderr, "failed to open BPF object\n");
        return 1;
    }

    /* （可選）在 load 之前修改設定 */
    /* skel->rodata->my_pid = getpid();  過濾自己 */

    /* load：把 BPF programs 和 maps 載入到 kernel */
    err = minimal_bpf__load(skel);
    if (err) {
        fprintf(stderr, "failed to load BPF: %d\n", err);
        goto cleanup;
    }

    /* attach：把 BPF program 附加到 tracepoint */
    err = minimal_bpf__attach(skel);
    if (err) {
        fprintf(stderr, "failed to attach BPF: %d\n", err);
        goto cleanup;
    }

    /* 設定 ring buffer consumer */
    rb = ring_buffer__new(bpf_map__fd(skel->maps.rb), handle_event, NULL, NULL);
    if (!rb) {
        fprintf(stderr, "failed to create ring buffer\n");
        goto cleanup;
    }

    signal(SIGINT, handle_sig);
    printf("%-8s %-16s %s\n", "PID", "COMM", "FILENAME");

    while (!stop) {
        err = ring_buffer__poll(rb, 100 /* timeout_ms */);
        if (err < 0) break;
    }

    ring_buffer__free(rb);

cleanup:
    minimal_bpf__destroy(skel);
    return err < 0 ? 1 : 0;
}
```

**Makefile**：

```makefile
# Makefile
CLANG   := clang
BPFTOOL := bpftool

BPFCFLAGS := -g -O2 -target bpf -D__TARGET_ARCH_x86_64
CFLAGS    := -g -Wall
LDFLAGS   := -lbpf -lelf -lz

all: minimal

# 生成 vmlinux.h（如果還沒有）
vmlinux.h:
	$(BPFTOOL) btf dump file /sys/kernel/btf/vmlinux format c > $@

# 編譯 BPF 程式
minimal.bpf.o: minimal.bpf.c vmlinux.h
	$(CLANG) $(BPFCFLAGS) -c $< -o $@

# 生成 skeleton header
minimal.skel.h: minimal.bpf.o
	$(BPFTOOL) gen skeleton $< > $@

# 編譯 userspace
minimal: minimal.c minimal.skel.h
	$(CC) $(CFLAGS) -o $@ $< $(LDFLAGS)

clean:
	rm -f minimal minimal.bpf.o minimal.skel.h vmlinux.h
```

## libbpf 的核心 API

### Open Phase

```c
/* 從檔案 open */
struct bpf_object *obj = bpf_object__open("prog.bpf.o");

/* 或用 skeleton（更型別安全，推薦）*/
struct myprog_bpf *skel = myprog_bpf__open();

/* open 之後可以查找特定 program 或 map */
struct bpf_program *prog = bpf_object__find_program_by_name(obj, "my_func");
struct bpf_map *map = bpf_object__find_map_by_name(obj, "my_map");

/* 修改設定（在 load 之前）*/
bpf_program__set_type(prog, BPF_PROG_TYPE_KPROBE);
bpf_map__set_max_entries(map, 10240);  /* 動態調整 max_entries */
```

### Load Phase

```c
/* load 把 programs 和 maps 載入 kernel */
err = bpf_object__load(obj);
/* 或 */
err = myprog_bpf__load(skel);

/* load 之後，maps 有 fd，可以 pin */
int map_fd = bpf_map__fd(map);
bpf_obj_pin(map_fd, "/sys/fs/bpf/my_map");
```

### Attach / Detach

```c
/* 用 SEC() annotation 自動 attach */
err = bpf_object__attach_skeleton(skel->skeleton);

/* 或明確 attach 特定 program */
struct bpf_link *link = bpf_program__attach(prog);
struct bpf_link *klink = bpf_program__attach_kprobe(prog, false, "vfs_read");
struct bpf_link *tplink = bpf_program__attach_tracepoint(prog, "syscalls", "sys_enter_openat");

/* pin link（讓 program 在 process 退出後繼續）*/
bpf_link__pin(link, "/sys/fs/bpf/my_link");

/* detach */
bpf_link__destroy(link);
```

### Map 操作

```c
/* 用 fd 直接操作 */
int map_fd = bpf_map__fd(skel->maps.my_map);

u32 key = 42;
u64 value = 0;
bpf_map_lookup_elem(map_fd, &key, &value);

u64 new_val = 100;
bpf_map_update_elem(map_fd, &key, &new_val, BPF_ANY);

bpf_map_delete_elem(map_fd, &key);

/* 遍歷所有 key */
u32 cur_key = 0, next_key;
while (!bpf_map_get_next_key(map_fd, &cur_key, &next_key)) {
    bpf_map_lookup_elem(map_fd, &next_key, &value);
    printf("key=%u val=%llu\n", next_key, value);
    cur_key = next_key;
}
```

### Ring Buffer

```c
/* 建立 ring buffer consumer */
struct ring_buffer *rb = ring_buffer__new(
    bpf_map__fd(skel->maps.rb),
    my_callback,      /* 每個 event 呼叫一次 */
    ctx,              /* 傳給 callback 的 context */
    NULL              /* ring_buffer_opts，通常 NULL */
);

/* blocking poll（最多等 timeout_ms）*/
int n = ring_buffer__poll(rb, 100);
/* n = 處理的 event 數，<0 = error */

/* non-blocking consume（一次處理所有 pending events）*/
int n = ring_buffer__consume(rb);

ring_buffer__free(rb);
```

## libbpf 的 Log 控制

```c
/* 自訂 log callback（預設輸出到 stderr）*/
static int my_print(enum libbpf_print_level level, const char *format, va_list args)
{
    if (level == LIBBPF_DEBUG)
        return 0;  /* 忽略 debug 訊息 */
    return vfprintf(stderr, format, args);
}

libbpf_set_print(my_print);
```

```c
/* 取得 verifier log（載入失敗時）*/
struct bpf_object_open_opts opts = {
    .sz               = sizeof(opts),
    .kernel_log_size  = 65536,
    .kernel_log_level = 2,  /* verbose */
};
char log[65536];
opts.kernel_log_buf = log;
struct myprog_bpf *skel = myprog_bpf__open_opts(&opts);
/* ... load ... */
if (err)
    fprintf(stderr, "verifier log:\n%s\n", log);
```

## rodata：編譯期常數傳給 BPF 程式

```c
/* 在 .bpf.c 裡宣告 rodata */
const volatile pid_t target_pid = 0;  /* 0 = trace all */
const volatile bool  verbose    = false;

SEC("tracepoint/syscalls/sys_enter_write")
int trace_write(void *ctx)
{
    if (target_pid && bpf_get_current_pid_tgid() >> 32 != target_pid)
        return 0;
    if (verbose)
        bpf_printk("write called\n");
    return 0;
}
```

```c
/* 在 userspace 設定（在 open() 之後、load() 之前）*/
skel->rodata->target_pid = 1234;
skel->rodata->verbose    = true;
err = myprog_bpf__load(skel);
```

rodata 在 load 時被複製進 kernel，之後就是 read-only 的。

## Error Handling Pattern

```c
/* libbpf 1.0+ 的錯誤處理方式 */
struct myprog_bpf *skel = myprog_bpf__open();
if (!skel) {
    /* errno 已被 libbpf 設定 */
    fprintf(stderr, "open failed: %s\n", strerror(errno));
    return 1;
}

/* 大部分函式回傳 int，0 = ok，負數 = -errno */
err = myprog_bpf__load(skel);
if (err) {
    fprintf(stderr, "load failed: %s\n", strerror(-err));
    goto cleanup;
}

struct bpf_link *link = bpf_program__attach(skel->progs.my_prog);
if (!link) {
    err = -errno;
    fprintf(stderr, "attach failed: %s\n", strerror(-err));
    goto cleanup;
}
```

## 踩雷集錦

1. **`bpf_map__fd()` 在 load 之前回傳 -1**：map 在 `bpf_object__load()` 之後才有 fd；在 open 之後就想用 fd 是錯的

2. **skeleton 的 `skel->maps.xxx` 是 `struct bpf_map *`，不是 fd**：要用 `bpf_map__fd(skel->maps.xxx)` 才能拿到 fd；直接當 fd 用是常見的初學錯誤

3. **`ring_buffer__poll` 超時不是錯誤**：`ring_buffer__poll` 回傳 0 代表超時（沒有 event），不是錯誤；只有 < 0 才是錯誤

4. **修改 map 的 `max_entries` 只在 load 之前有效**：`bpf_map__set_max_entries()` 只能在 `open()` 之後、`load()` 之前呼叫；load 之後的 map 大小固定

5. **`libbpf_set_strict_mode(LIBBPF_STRICT_ALL)` 可以讓錯誤更明顯**：預設 libbpf 1.0 已啟用 strict mode；如果你看到舊文章用 `libbpf_set_strict_mode` 說 "legacy API not available"，不用擔心，是舊的相容性設定

## 動手練習

1. 把 Ch 0 的 `hello.bpf.c` 改成用 libbpf skeleton（用 `bpftool gen skeleton`），寫出對應的 userspace `hello.c`，編譯並執行

2. 加入 `rodata` 支援：讓 userspace 可以傳入 target_pid，BPF 程式只追蹤對應 pid 的事件

3. 把 ring buffer 的 consumer 改成 non-blocking（用 `ring_buffer__consume`），在 main loop 裡加入 sleep 和 ring buffer poll 交替，觀察 latency 的影響

## 本章重點整理

- libbpf 的核心 lifecycle：open → load → attach → poll/consume → destroy
- Skeleton 提供型別安全的 BPF object 存取（`skel->maps.xxx`、`skel->progs.xxx`、`skel->rodata->xxx`）
- `ring_buffer__new` + `ring_buffer__poll` 是消費 BPF 事件的標準方式
- `rodata` 讓你在 load time 設定 BPF 程式的「config」，不需要另外的 map

## 自我檢核

- [ ] 能說出 open/load/attach 三個步驟各做了什麼，以及為什麼是這個順序
- [ ] 知道 `skel->maps.xxx` 和 `bpf_map__fd(skel->maps.xxx)` 的差別
- [ ] 能解釋 `rodata` 的工作原理（為什麼是 "read-only data"）
- [ ] 知道 `ring_buffer__poll(rb, 0)` 和 `ring_buffer__poll(rb, -1)` 的行為差異

## 延伸閱讀

### 官方文件

- **[libbpf API docs](https://libbpf.readthedocs.io/en/latest/api.html)**
  - **讀哪裡**：`bpf_object_*`、`bpf_program_*`、`bpf_map_*`、`ring_buffer_*` 這幾組 API
  - **學什麼**：所有 API 函式的完整說明；作為 reference 查閱

- **[libbpf bootstrap](https://github.com/libbpf/libbpf-bootstrap)**
  - **讀哪裡**：`examples/c/` 目錄下的範例（`minimal`、`uprobe`、`fentry`）
  - **學什麼**：完整的、可以直接編譯的 libbpf 程式範例；這是最好的 starter template

### 部落格

- **[BPF CO-RE and libbpf: A Practical Guide](https://nakryiko.com/posts/libbpf-bootstrap/)** — Andrii Nakryiko
  - **這篇說什麼**：用 libbpf-bootstrap 的 minimal 範例解釋整個 libbpf 工作流程
  - **讀哪裡**：整篇；跟著範例動手做
  - **為什麼值得讀**：作者是 libbpf 的主要維護者；這篇文章是 libbpf 的官方推薦入門

→ [Ch 16 BPF Skeleton：自動生成的 userspace 介面](./16-bpf-skeleton.md)
