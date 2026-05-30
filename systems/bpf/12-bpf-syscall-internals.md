# Ch 12 — BPF syscall 底層序列

> **目標**：理解 `bpf(2)` syscall 的完整 command 集合、每個操作的 fd 語意、object 的生命週期，以及不用任何框架、只用 raw syscall 從頭載入一個 BPF 程式的完整流程。

## 為什麼需要這個？

libbpf、bpftrace、BCC 都是包裝好的框架。但框架出問題的時候（載入失敗、奇怪的錯誤），你需要知道底層發生了什麼。而且，真正理解 eBPF 的架構，就必須知道 `bpf()` syscall 是什麼、每個 command 做什麼。

這章讓你能「手動操作」BPF subsystem，不依賴任何框架。

## 先建立直覺：`bpf()` 是什麼？

`bpf(2)` 是一個多功能 syscall，透過 `cmd` 參數區分不同操作：

```c
#include <linux/bpf.h>

int bpf(int cmd, union bpf_attr *attr, unsigned int size);
```

```
主要 bpf() commands：

BPF_MAP_CREATE      → 建立 BPF map，回傳 map fd
BPF_MAP_LOOKUP_ELEM → 從 map 讀取 key-value
BPF_MAP_UPDATE_ELEM → 寫入 / 更新 map
BPF_MAP_DELETE_ELEM → 刪除 map entry
BPF_MAP_GET_NEXT_KEY → 遍歷 map 的 key

BPF_PROG_LOAD       → 載入 BPF 程式（驗證 + JIT），回傳 prog fd
BPF_PROG_ATTACH     → 把程式 attach 到 cgroup
BPF_PROG_DETACH     → detach

BPF_OBJ_PIN         → pin object 到 BPF filesystem
BPF_OBJ_GET         → 從 BPF filesystem 取得已 pinned 的 object fd

BPF_LINK_CREATE     → 建立 bpf_link（現代 attach 方式）
BPF_LINK_UPDATE     → 更新 link 上的 prog（hot reload）
BPF_LINK_DESTROY    → 銷毀 link

BPF_BTF_LOAD        → 載入 BTF blob，回傳 btf fd

BPF_PROG_QUERY      → 查詢 attach 到某個 hook 的 programs

BPF_OBJ_GET_INFO_BY_FD → 取得 map/prog/btf 的 info
```

## Step 1：建立 Map（`BPF_MAP_CREATE`）

```c
#include <linux/bpf.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <string.h>
#include <stdio.h>
#include <errno.h>

/* bpf() syscall wrapper */
static inline int bpf_syscall(int cmd, union bpf_attr *attr, unsigned int size)
{
    return syscall(__NR_bpf, cmd, attr, size);
}

int create_array_map(int max_entries)
{
    union bpf_attr attr;
    memset(&attr, 0, sizeof(attr));

    attr.map_type    = BPF_MAP_TYPE_ARRAY;
    attr.key_size    = sizeof(__u32);    /* key 是 u32 */
    attr.value_size  = sizeof(__u64);   /* value 是 u64 */
    attr.max_entries = max_entries;
    /* 可選：設 map name（方便 bpftool 識別）*/
    strncpy(attr.map_name, "my_array", sizeof(attr.map_name) - 1);

    int fd = bpf_syscall(BPF_MAP_CREATE, &attr, sizeof(attr));
    if (fd < 0) {
        fprintf(stderr, "BPF_MAP_CREATE failed: %s\n", strerror(errno));
        return -1;
    }
    printf("created map fd=%d\n", fd);
    return fd;
}
```

## Step 2：操作 Map

```c
int update_map(int map_fd, __u32 key, __u64 value)
{
    union bpf_attr attr;
    memset(&attr, 0, sizeof(attr));

    attr.map_fd  = map_fd;
    attr.key     = (__u64)(uintptr_t)&key;    /* userspace key 的指標 */
    attr.value   = (__u64)(uintptr_t)&value;  /* userspace value 的指標 */
    attr.flags   = BPF_ANY;  /* 插入或更新 */

    if (bpf_syscall(BPF_MAP_UPDATE_ELEM, &attr, sizeof(attr)) < 0) {
        fprintf(stderr, "BPF_MAP_UPDATE_ELEM failed: %s\n", strerror(errno));
        return -1;
    }
    return 0;
}

int lookup_map(int map_fd, __u32 key, __u64 *value_out)
{
    union bpf_attr attr;
    memset(&attr, 0, sizeof(attr));

    attr.map_fd  = map_fd;
    attr.key     = (__u64)(uintptr_t)&key;
    attr.value   = (__u64)(uintptr_t)value_out;

    if (bpf_syscall(BPF_MAP_LOOKUP_ELEM, &attr, sizeof(attr)) < 0) {
        if (errno == ENOENT)
            return -ENOENT;  /* key 不存在 */
        fprintf(stderr, "BPF_MAP_LOOKUP_ELEM failed: %s\n", strerror(errno));
        return -1;
    }
    return 0;
}

/* 遍歷所有 key */
void iterate_map(int map_fd)
{
    __u32 prev_key = 0, next_key = 0;
    union bpf_attr attr;
    __u64 value;

    /* 第一次呼叫：attr.key = NULL（用 0 模擬）取得第一個 key */
    memset(&attr, 0, sizeof(attr));
    attr.map_fd   = map_fd;
    attr.key      = 0;  /* NULL = 取得第一個 key */
    attr.next_key = (__u64)(uintptr_t)&next_key;

    while (bpf_syscall(BPF_MAP_GET_NEXT_KEY, &attr, sizeof(attr)) == 0) {
        prev_key = next_key;
        lookup_map(map_fd, prev_key, &value);
        printf("key=%u value=%llu\n", prev_key, value);
        attr.key = (__u64)(uintptr_t)&prev_key;
    }
}
```

## Step 3：載入 BPF Program（`BPF_PROG_LOAD`）

這是最複雜的操作。你需要提供 BPF bytecode（`__u64` 陣列）和相關 metadata：

```c
/* 一個最小的 BPF 程式：什麼都不做，直接 return 0 */
static const struct bpf_insn minimal_prog[] = {
    /*
     * BPF_MOV64_IMM(BPF_REG_0, 0):  r0 = 0
     * BPF_EXIT_INSN():               exit
     */
    /* opcode 0xb7 = BPF_ALU64 | BPF_MOV | BPF_K */
    { .code = 0xb7, .dst_reg = 0, .src_reg = 0, .off = 0, .imm = 0 },
    /* opcode 0x95 = BPF_JMP | BPF_EXIT */
    { .code = 0x95, .dst_reg = 0, .src_reg = 0, .off = 0, .imm = 0 },
};

int load_minimal_prog(int map_fd)
{
    /* verifier log buffer */
    char log_buf[8192];

    union bpf_attr attr;
    memset(&attr, 0, sizeof(attr));

    attr.prog_type    = BPF_PROG_TYPE_TRACEPOINT;
    attr.insns        = (__u64)(uintptr_t)minimal_prog;
    attr.insn_cnt     = sizeof(minimal_prog) / sizeof(minimal_prog[0]);
    attr.license      = (__u64)(uintptr_t)"GPL";

    /* verifier log（如果載入失敗，這裡會有詳細錯誤）*/
    attr.log_buf      = (__u64)(uintptr_t)log_buf;
    attr.log_size     = sizeof(log_buf);
    attr.log_level    = 1;  /* 基本 log；2 = verbose；3 = 最詳細 */

    /* 設 prog name（方便識別）*/
    strncpy(attr.prog_name, "minimal", sizeof(attr.prog_name) - 1);

    int fd = bpf_syscall(BPF_PROG_LOAD, &attr, sizeof(attr));
    if (fd < 0) {
        fprintf(stderr, "BPF_PROG_LOAD failed: %s\n", strerror(errno));
        fprintf(stderr, "Verifier log:\n%s\n", log_buf);
        return -1;
    }
    printf("loaded prog fd=%d\n", fd);
    return fd;
}
```

**`BPF_PROG_LOAD` 的主要欄位**：

| 欄位 | 說明 |
|---|---|
| `prog_type` | Program type（`BPF_PROG_TYPE_*`）|
| `insns` / `insn_cnt` | BPF bytecode 的指標和長度 |
| `license` | "GPL" 或 "Dual BSD/GPL" 等；影響可用的 helper |
| `kern_version` | 已廢棄（早期用於版本檢查，現在不需要）|
| `log_buf` / `log_size` / `log_level` | Verifier log buffer（載入失敗時查看）|
| `prog_name` | 最長 16 bytes 的名稱（方便識別）|
| `prog_btf_fd` | 程式的 BTF fd（CO-RE 需要）|
| `func_info` / `line_info` | BTF 的行號 info（來自 `.BTF.ext`）|
| `expected_attach_type` | 某些 prog type 需要指定 attach subtype |

## Step 4：Attach（以 tracepoint 為例）

Tracepoint 的 attach 不直接用 `bpf()` syscall，而是透過 `perf_event_open` syscall：

```c
#include <linux/perf_event.h>
#include <sys/ioctl.h>

int attach_tracepoint(int prog_fd,
                      const char *category,   /* e.g. "syscalls" */
                      const char *name)       /* e.g. "sys_enter_openat" */
{
    /* 讀取 tracepoint id */
    char path[256];
    snprintf(path, sizeof(path),
             "/sys/kernel/debug/tracing/events/%s/%s/id",
             category, name);

    FILE *f = fopen(path, "r");
    if (!f) { perror("fopen tracepoint id"); return -1; }

    int tp_id;
    fscanf(f, "%d", &tp_id);
    fclose(f);

    /* 用 perf_event_open 建立 tracepoint perf event */
    struct perf_event_attr pattr = {
        .type       = PERF_TYPE_TRACEPOINT,
        .sample_type= PERF_SAMPLE_RAW,
        .config     = tp_id,
        .wakeup_events = 1,
    };

    int pfd = syscall(__NR_perf_event_open, &pattr,
                      -1,  /* pid = -1（所有 process）*/
                      0,   /* cpu = 0（只在 CPU 0）或 -1（所有 CPU）*/
                      -1,  /* group_fd */
                      0);  /* flags */
    if (pfd < 0) { perror("perf_event_open"); return -1; }

    /* 把 BPF prog attach 到這個 perf event */
    if (ioctl(pfd, PERF_EVENT_IOC_SET_BPF, prog_fd) < 0) {
        perror("PERF_EVENT_IOC_SET_BPF"); close(pfd); return -1;
    }

    /* 啟用 perf event */
    if (ioctl(pfd, PERF_EVENT_IOC_ENABLE, 0) < 0) {
        perror("PERF_EVENT_IOC_ENABLE"); close(pfd); return -1;
    }

    printf("attached: tracepoint %s:%s, pfd=%d\n", category, name, pfd);
    return pfd;  /* 持有 pfd = attach 保持；close(pfd) = detach */
}
```

## Step 5：Pin 到 BPF Filesystem（`BPF_OBJ_PIN`）

```c
int pin_object(int fd, const char *path)
{
    union bpf_attr attr;
    memset(&attr, 0, sizeof(attr));

    attr.pathname = (__u64)(uintptr_t)path;
    attr.bpf_fd   = fd;

    if (bpf_syscall(BPF_OBJ_PIN, &attr, sizeof(attr)) < 0) {
        fprintf(stderr, "BPF_OBJ_PIN failed: %s\n", strerror(errno));
        return -1;
    }
    printf("pinned to %s\n", path);
    return 0;
}

/* 從 pin path 取回 fd */
int get_pinned_object(const char *path)
{
    union bpf_attr attr;
    memset(&attr, 0, sizeof(attr));
    attr.pathname = (__u64)(uintptr_t)path;

    int fd = bpf_syscall(BPF_OBJ_GET, &attr, sizeof(attr));
    if (fd < 0) {
        fprintf(stderr, "BPF_OBJ_GET failed: %s\n", strerror(errno));
        return -1;
    }
    return fd;
}
```

## 完整的 raw syscall 流程

把上面的步驟串起來：

```c
#include <linux/bpf.h>
#include <sys/syscall.h>
#include <sys/ioctl.h>
#include <linux/perf_event.h>
#include <unistd.h>
#include <string.h>
#include <stdio.h>
#include <errno.h>

/* ... 上面的所有函式 ... */

int main(void)
{
    /* 1. 建立 map */
    int map_fd = create_array_map(1024);
    if (map_fd < 0) return 1;

    /* 2. 初始化 map */
    for (__u32 i = 0; i < 256; i++) {
        __u64 v = 0;
        update_map(map_fd, i, v);
    }

    /* 3. 載入 BPF program（這裡用 minimal 範例）*/
    int prog_fd = load_minimal_prog(map_fd);
    if (prog_fd < 0) { close(map_fd); return 1; }

    /* 4. Attach 到 tracepoint */
    int perf_fd = attach_tracepoint(prog_fd, "syscalls", "sys_enter_write");
    if (perf_fd < 0) { close(prog_fd); close(map_fd); return 1; }

    printf("BPF program running. Press Ctrl+C to stop.\n");
    sleep(10);

    /* 5. Cleanup（close 觸發 detach）*/
    close(perf_fd);   /* detach from tracepoint */
    close(prog_fd);   /* prog refcount 降低（如果沒有其他 fd，被釋放）*/
    close(map_fd);

    return 0;
}
```

## Object 生命週期的 Reference Counting

```
BPF objects 的生命週期（全部是 reference counted）

map fd 建立: refcount = 1（fd 本身）
  └── BPF prog 引用 map: refcount = 2
  └── userspace 再 pin: refcount = 3
  └── close(map_fd): refcount = 2
  └── prog 被 detach: refcount = 1（pin 還在）
  └── rm pin path: refcount = 0 → map 被釋放

同樣的邏輯適用於 prog fd 和 btf fd
```

**關鍵**：BPF object 的生命週期是 reference counted 的，不是 "close fd 就馬上釋放"。只要還有任何引用（另一個 fd、pin、或 link），object 就繼續存在。

## `BPF_OBJ_GET_INFO_BY_FD`：查詢 Object 資訊

```c
void show_prog_info(int prog_fd)
{
    struct bpf_prog_info info;
    memset(&info, 0, sizeof(info));

    union bpf_attr attr;
    memset(&attr, 0, sizeof(attr));
    attr.info.bpf_fd = prog_fd;
    attr.info.info   = (__u64)(uintptr_t)&info;
    attr.info.info_len = sizeof(info);

    if (bpf_syscall(BPF_OBJ_GET_INFO_BY_FD, &attr, sizeof(attr)) < 0) {
        perror("BPF_OBJ_GET_INFO_BY_FD"); return;
    }

    printf("prog id=%u type=%u name=%s run_cnt=%llu run_time_ns=%llu\n",
           info.id, info.type, info.name,
           info.run_cnt, info.run_time_ns);
}
```

這就是 `bpftool prog show` 底層做的事。

## 踩雷集錦

1. **`attr.size` 要傳正確**：`bpf()` syscall 的第三個參數是 `union bpf_attr` 的大小；傳 `sizeof(attr)` 就對，傳 0 或錯誤大小會得到 -EINVAL

2. **Pointer 要 cast 成 `u64`**：`bpf_attr` 裡的 pointer 欄位是 `__aligned_u64` 型別；在 32-bit 平台上直接用指標會 overflow；標準做法是 `(__u64)(uintptr_t)ptr`

3. **`BPF_PROG_LOAD` 的 `kern_version`**：這個欄位在舊版 kernel 需要填 `LINUX_VERSION_CODE`，但在現代 kernel（4.20+）已不再需要；填 0 即可

4. **perf_event_open 的 `cpu` 參數**：傳 `-1` 表示所有 CPU，但需要 `pid` 也是 `-1`（或有 `CAP_PERFMON`）；如果只想在某個 CPU 上，傳 CPU id 和 `pid=-1`

5. **Log buffer 不夠大時被截斷**：如果 verifier log 超過 `log_size`，syscall 回傳 -ENOSPC（不是 -EINVAL）；增大 `log_buf` 看完整 log

## 動手練習

1. 把這章的 raw syscall 程式碼整合成一個可以編譯執行的 `raw_bpf.c`，載入一個最小 BPF 程式，attach 到 tracepoint，讀取 run_cnt 確認它被執行了

2. 用 `strace -e bpf` 追蹤 `bpftool prog list` 的執行，對照 bpftool 呼叫了哪些 `bpf()` commands 和哪些參數；和你在這章學到的 API 做對應

3. 用 `BPF_OBJ_GET_INFO_BY_FD` 實作一個列出所有 loaded programs 的程式（hint：用 `BPF_PROG_GET_FD_BY_ID` 遍歷 id 從 1 開始，直到 -ENOENT）

## 本章重點整理

- `bpf(2)` 是一個多功能 syscall，透過 `cmd` 參數區分 map / prog / link / btf / obj 等操作
- 所有 BPF objects 透過 fd 引用，生命週期是 reference counted
- `BPF_PROG_LOAD` 是最複雜的操作，需要 bytecode、license、verifier log buffer
- Tracepoint attach 不是純 bpf syscall，需要搭配 `perf_event_open` + ioctl

## 自我檢核

- [ ] 能說出 `bpf()` syscall 最常用的 5 個 commands，以及每個的用途
- [ ] 能解釋 BPF object 的 reference counting 機制，以及 close fd 後 object 何時才真正被釋放
- [ ] 知道 tracepoint attach 的完整步驟（不是 bpf syscall 本身）
- [ ] 能用 `BPF_OBJ_GET_INFO_BY_FD` 取得一個 prog 的基本 metadata

## 延伸閱讀

### 官方文件

- **[bpf(2) man page](https://man7.org/linux/man-pages/man2/bpf.2.html)**
  - **讀哪裡**：每個 command 的 `union bpf_attr` 欄位說明
  - **學什麼**：所有 command 的完整 API 規格；作為 raw syscall 開發的參考

- **[kernel/bpf/syscall.c](https://elixir.bootlin.com/linux/latest/source/kernel/bpf/syscall.c)**
  - **讀哪裡**：`__sys_bpf()` 函式；每個 case 對應一個 command
  - **學什麼**：syscall 的 kernel 側實作；看 kernel 怎麼處理你傳的 attr

→ [練習 B：裸 BPF syscall 實作](./practice-b-raw-bpf-syscall.md)
