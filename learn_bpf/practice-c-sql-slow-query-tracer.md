# 練習 C — SQL 慢查詢 tracer

> 目標：用 USDT 接 PostgreSQL 的 `query__start` / `query__done` probe，做出一支只印超過閾值的慢 query tracer。整合 Ch 11 (bpftrace)、Ch 13–15 (libbpf)、Ch 17 (USDT) 的所有技能。

## 任務規格

寫一個 tracer，每當 PostgreSQL 處理的 query 超過閾值（例如 100ms）就印一行：

```
TIME             PID    LATENCY(ms) QUERY
14:23:45.123     12345         234  SELECT * FROM users WHERE id = $1
14:23:46.789     12346        1234  SELECT count(*) FROM logs WHERE created_at > $1
```

要求：
- 用 PostgreSQL USDT（不能用 uprobe 寫死 function name）
- threshold 可從 cmdline 設（預設 100ms）
- query string 完整捕獲（PG query 可能很長，至少 256 byte）
- 高吞吐 OK：100 query/sec 不該丟

## 環境準備

裝 PostgreSQL（要編譯時開 `--enable-dtrace` 才有 USDT）：

```bash
# Ubuntu 22.04+ apt 版本通常有 USDT
sudo apt install postgresql-15

# 確認 binary 有 USDT
readelf -n /usr/lib/postgresql/15/bin/postgres | grep -A 5 stapsdt | head
# 應該看到一堆 "Provider: postgresql"
```

列出可用的 USDT：

```bash
sudo bpftrace -l 'usdt:/usr/lib/postgresql/15/bin/postgres:*' | head
# usdt:postgresql:query__start
# usdt:postgresql:query__done
# usdt:postgresql:transaction__start
# usdt:postgresql:transaction__commit
# usdt:postgresql:lwlock__acquire
# ...
```

開個本地 DB 做測試：

```bash
sudo systemctl start postgresql
sudo -u postgres psql -c "CREATE DATABASE testdb;"
sudo -u postgres psql testdb -c "CREATE TABLE t (id int, data text);"
sudo -u postgres psql testdb -c "INSERT INTO t SELECT i, repeat('x', 100) FROM generate_series(1, 100000) i;"
```

製造一個慢 query 測試：

```bash
sudo -u postgres psql testdb -c "SELECT pg_sleep(0.5), count(*) FROM t WHERE data LIKE '%y%';"
```

## 三種寫法

按你的時間預算選：

- **快速版（5 分鐘）**：bpftrace one-script
- **中等版（30 分鐘）**：bpftrace .bt 完整檔案
- **完整版（2 小時）**：libbpf+CO-RE C 版本

## 版本 A：bpftrace 快速版

<details>
<summary>展開參考實作</summary>

存成 `pgslow.bt`：

```
#!/usr/bin/env bpftrace

BEGIN {
    printf("Tracing PostgreSQL slow queries (>100ms)... Ctrl-C to stop.\n");
    printf("%-15s %-7s %-12s %s\n", "TIME", "PID", "LATENCY(ms)", "QUERY");
}

usdt:/usr/lib/postgresql/15/bin/postgres:postgresql:query__start
{
    @start[pid] = nsecs;
    @query[pid] = str(arg0);
}

usdt:/usr/lib/postgresql/15/bin/postgres:postgresql:query__done
/@start[pid]/
{
    $lat_ns = nsecs - @start[pid];
    $lat_ms = $lat_ns / 1000000;
    if ($lat_ms >= 100) {
        time("%H:%M:%S      ");
        printf("%-7d %-12d %s\n", pid, $lat_ms, @query[pid]);
    }
    delete(@start[pid]);
    delete(@query[pid]);
}
```

跑：

```bash
sudo ./pgslow.bt
```

另一個 terminal 觸發慢 query：

```bash
sudo -u postgres psql testdb -c "SELECT pg_sleep(0.5)"
```

</details>

bpftrace 版本 ~25 行，幾秒就能改寫。**ad-hoc 排查首選**。

## 版本 B：libbpf+CO-RE 完整版

#### kernel side `pgslow.bpf.c`

<details>
<summary>展開 kernel side</summary>

```c
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>
#include <bpf/usdt.bpf.h>

char LICENSE[] SEC("license") = "Dual BSD/GPL";

#define MAX_QUERY 384

struct event {
    __u64 ts_ns;
    __u32 pid;
    __u64 latency_ns;
    char  query[MAX_QUERY];
};

struct query_state {
    __u64 start_ns;
    char  query[MAX_QUERY];
};

struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 4096);
    __type(key, __u32);
    __type(value, struct query_state);
} states SEC(".maps");

struct {
    __uint(type, BPF_MAP_TYPE_RINGBUF);
    __uint(max_entries, 1024 * 1024);
} events SEC(".maps");

const volatile __u64 threshold_ns = 100ULL * 1000 * 1000;   // 100ms 預設

SEC("usdt")
int BPF_USDT(query_start, char *query)
{
    __u32 pid = bpf_get_current_pid_tgid() >> 32;
    struct query_state s = {};
    s.start_ns = bpf_ktime_get_ns();
    bpf_probe_read_user_str(&s.query, sizeof(s.query), query);
    bpf_map_update_elem(&states, &pid, &s, BPF_ANY);
    return 0;
}

SEC("usdt")
int BPF_USDT(query_done)
{
    __u32 pid = bpf_get_current_pid_tgid() >> 32;
    struct query_state *s = bpf_map_lookup_elem(&states, &pid);
    if (!s) return 0;

    __u64 lat = bpf_ktime_get_ns() - s->start_ns;
    if (lat < threshold_ns) goto cleanup;

    struct event *e = bpf_ringbuf_reserve(&events, sizeof(*e), 0);
    if (!e) goto cleanup;

    e->ts_ns = bpf_ktime_get_ns();
    e->pid = pid;
    e->latency_ns = lat;
    __builtin_memcpy(&e->query, &s->query, MAX_QUERY);

    bpf_ringbuf_submit(e, 0);

cleanup:
    bpf_map_delete_elem(&states, &pid);
    return 0;
}
```

</details>

#### user side `pgslow.c`

<details>
<summary>展開 user side</summary>

```c
#include <stdio.h>
#include <unistd.h>
#include <signal.h>
#include <stdlib.h>
#include <time.h>
#include <bpf/libbpf.h>
#include "pgslow.skel.h"

#define MAX_QUERY 384
#define PG_BIN "/usr/lib/postgresql/15/bin/postgres"

struct event {
    unsigned long long ts_ns;
    unsigned int pid;
    unsigned long long latency_ns;
    char query[MAX_QUERY];
};

static volatile int running = 1;
static void sig(int) { running = 0; }

static int handle_event(void *ctx, void *data, size_t sz) {
    struct event *e = data;
    char ts[16];
    time_t t = time(NULL);
    strftime(ts, sizeof(ts), "%H:%M:%S", localtime(&t));
    printf("%-15s %-7u %-12llu %s\n",
           ts, e->pid, e->latency_ns / 1000000, e->query);
    return 0;
}

int main(int argc, char **argv) {
    struct pgslow_bpf *skel;
    struct ring_buffer *rb;
    unsigned long long threshold_ms = 100;

    if (argc > 1) threshold_ms = strtoull(argv[1], NULL, 10);

    signal(SIGINT, sig);

    skel = pgslow_bpf__open();
    if (!skel) return 1;

    skel->rodata->threshold_ns = threshold_ms * 1000 * 1000;

    if (pgslow_bpf__load(skel)) { fprintf(stderr, "load failed\n"); goto out; }

    skel->links.query_start = bpf_program__attach_usdt(
        skel->progs.query_start, -1, PG_BIN, "postgresql", "query__start", NULL);
    skel->links.query_done = bpf_program__attach_usdt(
        skel->progs.query_done, -1, PG_BIN, "postgresql", "query__done", NULL);
    if (!skel->links.query_start || !skel->links.query_done) {
        fprintf(stderr, "attach USDT failed\n"); goto out;
    }

    rb = ring_buffer__new(bpf_map__fd(skel->maps.events), handle_event, NULL, NULL);

    printf("Tracing PG queries > %llu ms... Ctrl-C to stop.\n", threshold_ms);
    printf("%-15s %-7s %-12s %s\n", "TIME", "PID", "LATENCY(ms)", "QUERY");

    while (running) {
        if (ring_buffer__poll(rb, 100) == -EINTR) break;
    }

    ring_buffer__free(rb);
out:
    pgslow_bpf__destroy(skel);
    return 0;
}
```

</details>

Build & run：

```bash
make
sudo ./pgslow 50          # threshold 50ms
```

## 測試用例

製造各種 query：

```bash
# 快 query - 不該被印
sudo -u postgres psql testdb -c "SELECT 1"

# 邊界 - 看 threshold 是否準確
sudo -u postgres psql testdb -c "SELECT pg_sleep(0.05)"   # 50ms

# 慢 query - 該被印
sudo -u postgres psql testdb -c "SELECT pg_sleep(0.3)"    # 300ms

# 真實熱 query
sudo -u postgres psql testdb -c "SELECT count(*) FROM t WHERE data LIKE '%y%'"

# 高吞吐測試 - 50 個 query 並發
for i in {1..50}; do
    sudo -u postgres psql testdb -c "SELECT pg_sleep(0.1)" &
done
wait
```

驗證 tracer 沒丟事件、threshold 正確、高吞吐穩定。

## 進階改造

完成基本版後試試：

5. **加 transaction grouping**：用 `transaction__start` / `transaction__commit` 把 queries 組成 transaction，輸出時印 transaction 內各 query 的時間佔比
6. **加 query 正規化**：`SELECT * FROM t WHERE id = 123` 跟 `SELECT * FROM t WHERE id = 456` 應該被視為同一個 query — 把 literal 換成 `?`
7. **加 percentile**：每分鐘印一次 p50 / p99 / p99.9 latency
8. **輸出 JSON**：每行 event 輸出 JSON，方便餵 jq / fluentd / 其他工具

## 自我檢核

- [ ] 我能用 bpftrace 接 USDT 並用 map 維護 per-pid 狀態
- [ ] 我能寫 libbpf 版本接 USDT、用 BPF_USDT macro
- [ ] 我能用 ringbuf 把 event 上報、user 端 polling 處理
- [ ] 我能用 `rodata->threshold_ns` 做 BPF runtime 可調參數
- [ ] 我知道為什麼這比 query log 解析方案開銷更低（不用 print 出來再 parse）

下一個 Part 進 networking。我們從 XDP 開始 — kernel 收 packet 路徑上**最早**的 BPF 點，能做到百萬 PPS 的 DDoS mitigation。

→ [Ch 19 XDP：最快的封包處理路徑](./19-xdp.md)
