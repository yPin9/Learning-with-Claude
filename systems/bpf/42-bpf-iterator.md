# Ch 42 — BPF Iterator 與批次操作

> **目標**：理解 BPF iterator 的設計——如何讓 BPF 程式在 kernel 裡批次遍歷 kernel objects（tasks、files、maps、network sockets）——以及 map 的 batch lookup/update API。

## 問題：大量 Kernel Object 的效率遍歷

傳統方式從 userspace 遍歷 kernel objects 很低效：

```
傳統方式（userspace 遍歷 /proc）：
  open("/proc") → readdir × N → open("/proc/<pid>/") → ... 
  每個 process 需要多次 syscall

傳統方式（userspace 遍歷 BPF map）：
  bpf(BPF_MAP_GET_NEXT_KEY) × N
  每個 entry 一次 syscall，10K entries = 10K syscalls
```

**BPF Iterator** 讓 BPF 程式在 kernel 裡直接遍歷，把遍歷邏輯和資料處理都放在 kernel 側，最後用 seq_file 介面輸出結果。

## BPF Iterator 的架構

```
BPF Iterator program
  │
  ├── 附加到一個 kernel 物件集合（task、map、file 等）
  │
  └── kernel 迭代每個物件，呼叫 BPF program
           │
           └── BPF program 處理這個物件，用 bpf_seq_write 輸出
                    │
                    └── 透過 BPF link 的 fd 讀取輸出
```

## 遍歷所有 Tasks（Processes）

```c
/* task_iter.bpf.c */
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>
#include <bpf/bpf_core_read.h>

SEC("iter/task")
int dump_tasks(struct bpf_iter__task *ctx)
{
    struct seq_file *seq = ctx->meta->seq;
    struct task_struct *task = ctx->task;

    if (!task) return 0;  /* 迭代結束 */

    pid_t  pid  = BPF_CORE_READ(task, pid);
    pid_t  tgid = BPF_CORE_READ(task, tgid);
    char   comm[16];
    bpf_probe_read_kernel_str(comm, sizeof(comm),
                              BPF_CORE_READ(task, comm));

    BPF_SEQ_PRINTF(seq, "%d\t%d\t%s\n", pid, tgid, comm);
    return 0;
}

char LICENSE[] SEC("license") = "GPL";
```

**Userspace 讀取輸出**：

```c
/* 建立 iter link，通過 fd 讀取結果 */
struct bpf_iter_attach_opts opts = {
    .sz = sizeof(opts),
};

int iter_fd = bpf_iter_create(bpf_link__fd(link));
char buf[4096];
ssize_t n;

while ((n = read(iter_fd, buf, sizeof(buf))) > 0)
    fwrite(buf, 1, n, stdout);

close(iter_fd);
```

## BPF Iterator 支援的物件類型

| 類型 | SEC 標注 | 遍歷的物件 |
|---|---|---|
| `task` | `iter/task` | 所有 tasks（threads）|
| `task_file` | `iter/task_file` | 每個 task 開啟的 files |
| `bpf_map` | `iter/bpf_map` | 所有 loaded BPF maps |
| `bpf_prog` | `iter/bpf_prog` | 所有 loaded BPF programs |
| `bpf_link` | `iter/bpf_link` | 所有 BPF links |
| `bpf_map_elem` | `iter/bpf_map_elem` | 某個 BPF map 的所有 entry |
| `tcp6` / `udp6` | `iter/tcp6`, `iter/udp6` | 所有 TCP/UDP sockets |
| `cgroup` | `iter/cgroup` | 所有 cgroups |

## Map Element Iterator

遍歷 BPF map 的所有 entry（比 `BPF_MAP_GET_NEXT_KEY` + `BPF_MAP_LOOKUP_ELEM` 快得多）：

```c
SEC("iter/bpf_map_elem")
int dump_map(struct bpf_iter__bpf_map_elem *ctx)
{
    struct seq_file *seq = ctx->meta->seq;
    void *key   = ctx->key;
    void *value = ctx->value;

    if (!key) return 0;

    /* 對於 u32→u64 的 map */
    u32 k = *(u32 *)key;
    u64 v = *(u64 *)value;
    BPF_SEQ_PRINTF(seq, "%u\t%llu\n", k, v);
    return 0;
}
```

**Attach 到特定 map**：

```c
/* 只遍歷指定的 map */
struct bpf_iter_attach_opts opts = {
    .sz = sizeof(opts),
    .flags = BPF_F_ITER_RESCHED,
};
opts.link_info.map.map_fd = bpf_map__fd(skel->maps.my_map);

struct bpf_link *link = bpf_program__attach_iter(
    skel->progs.dump_map,
    &opts
);
```

## Map Batch Operations

對於 userspace 需要快速讀取整個 map 的情況，batch API 比逐條 lookup 效率高很多：

```c
/* 批次讀取整個 map（userspace API）*/
int batch_lookup(int map_fd, int map_size)
{
    u32 *keys   = malloc(map_size * sizeof(u32));
    u64 *values = malloc(map_size * sizeof(u64));
    u32 count   = map_size;
    void *in_batch = NULL;   /* 第一次呼叫傳 NULL */

    while (true) {
        /* 一次呼叫可以讀取多個 entry */
        int err = bpf_map_lookup_batch(map_fd,
                                        &in_batch, &in_batch,  /* 進出 batch cursor */
                                        keys, values,
                                        &count,                /* 輸入：buffer 大小；輸出：實際讀了幾個 */
                                        NULL);

        for (u32 i = 0; i < count; i++)
            printf("key=%u val=%llu\n", keys[i], values[i]);

        if (err == -ENOENT) break;  /* 已經遍歷完所有 entry */
        if (err < 0) { perror("batch lookup"); break; }

        count = map_size;  /* 重置 count for next batch */
    }

    free(keys); free(values);
    return 0;
}
```

**Batch update**（批次更新，比逐條 update 少 N 次 syscall）：

```c
/* 一次更新多個 entry */
u32 keys[100];
u64 vals[100];
/* ... fill keys and values ... */
u32 count = 100;
bpf_map_update_batch(map_fd, keys, vals, &count, NULL);
```

## 效能比較：傳統遍歷 vs Iterator vs Batch

| 方法 | 10K entries 的 syscall 數 | 適合場景 |
|---|---|---|
| 逐條 `GET_NEXT_KEY + LOOKUP` | 20K | 任何 kernel 版本 |
| Batch lookup | ~10–100 | 需要完整讀取 map |
| BPF Iterator | 1（read syscall）| 需要在 kernel 做計算後再輸出 |

## 踩雷集錦

1. **BPF Iterator 需要 kernel 5.8+**：task、tcp 等 iterator type 各有不同的最低版本需求；用 `sudo bpftrace -l 'iter:*'` 查看你的 kernel 支援什麼

2. **`BPF_SEQ_PRINTF` 的格式化限制**：類似 `bpf_printk`，參數有限制；複雜的輸出要用 `bpf_seq_write` 寫 raw bytes

3. **batch lookup 的 `in_batch` cursor**：每次 batch 呼叫都要把上次返回的 `in_batch` 作為這次的輸入；清空 cursor（設為 NULL）會從頭開始，不是 resume

4. **Iterator 的 `ctx->task` 可能是 NULL**：迭代結束時 kernel 傳入 NULL；BPF 程式必須先做 NULL check

## 動手練習

1. 用 BPF iterator 實作一個類似 `ps aux` 的工具：遍歷所有 tasks，輸出 pid、comm、state；比較和用 `/proc` 遍歷的速度差異

2. 用 `bpf_map_lookup_batch` 在 1 秒內讀取一個 10K entry 的 HASH map 10 次，比較和逐條 lookup 的時間

## 本章重點整理

- BPF Iterator 讓 BPF 程式在 kernel 批次遍歷 objects，用 seq_file 介面輸出
- 比 userspace 逐條遍歷（/proc 或 GET_NEXT_KEY）效率高得多
- Map batch API（`bpf_map_lookup_batch` / `update_batch`）用少量 syscall 操作大量 entry
- Iterator 支援 tasks、files、BPF maps、network sockets 等物件

## 自我檢核

- [ ] 能說出 BPF iterator 和 userspace `/proc` 遍歷在效率上的差異
- [ ] 知道 `bpf_map_lookup_batch` 的 cursor 語意（in_batch/out_batch）
- [ ] 能解釋為什麼 iterator callback 裡的 `ctx->task` 可能是 NULL

→ [Ch 43 Task/inode/sk local storage](./43-task-inode-sk-storage.md)
