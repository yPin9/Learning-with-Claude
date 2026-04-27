# Ch 8 — BPF maps：kernel 與 user space 共享狀態

> 目標：搞懂 maps 在 BPF 體系裡的角色、十幾種 map type 各自的取捨、percpu 的概念、kernel 端與 user 端的操作差異、以及 pinning 與生命週期。

## 為什麼必須有 maps？

BPF program 的記憶體只有 **512 byte stack** 跟 **register**。沒有 heap，沒有 global 變數（其實有，但底層也是 map），更沒有跨呼叫的記憶體。

但你寫 observability 工具八成要做這幾件事：

- 「累計每個 PID 的 read 字數」 — 需要 PID → counter 的對照
- 「記住每個 connection 的開始時間」 — 需要 socket → timestamp
- 「lookup IP 是否在 blocklist」 — 需要 IP set
- 「把 event 串流送回 user space」 — 需要 producer/consumer queue

**沒有 maps，這些事都做不了**。

Maps 解決三個問題：

1. **跨 BPF program 呼叫保留狀態**（同一個 BPF program 多次觸發之間共享）
2. **跨不同 BPF program 共享狀態**（一個 program 寫、另一個讀）
3. **kernel ↔ user space 雙向通訊**（user 空間用 syscall 操作同一份 map）

## Map 的本質

Map 就是一個**有 type 的 key-value store**。建立時要決定五件事：

```c
struct {
    __uint(type,        BPF_MAP_TYPE_HASH);   // 哪種 map
    __type(key,         u32);                 // key 型別
    __type(value,       u64);                 // value 型別
    __uint(max_entries, 10240);               // 容量上限
    __uint(map_flags,   0);                   // 額外 flag
} my_map SEC(".maps");
```

`SEC(".maps")` 告訴 libbpf「這是個 map 宣告」，會在 BPF object 載入時把 map 建好、給你個 fd。

操作：

```c
// kernel 端
u64 *val = bpf_map_lookup_elem(&my_map, &key);
bpf_map_update_elem(&my_map, &key, &value, BPF_ANY);
bpf_map_delete_elem(&my_map, &key);

// user 端（透過 libbpf）
bpf_map__lookup_elem(map, &key, sizeof(key), &val, sizeof(val), 0);
bpf_map__update_elem(map, &key, sizeof(key), &val, sizeof(val), BPF_ANY);
```

兩邊看到的是**同一份 kernel-managed memory**，沒有複製。

## Map type 全景

按用途分四類，最常用的列出來：

### 1. 一般 key-value 儲存

| Type | 特性 | 典型用途 |
|---|---|---|
| `BPF_MAP_TYPE_HASH` | 任意 key 大小、hash 查表 | PID → counter、IP → metadata |
| `BPF_MAP_TYPE_ARRAY` | key 必為 u32 index，固定大小 | 全域常數、preallocated buckets |
| `BPF_MAP_TYPE_PERCPU_HASH` | 每個 CPU 一份，無 lock | **高頻計數器（首選）** |
| `BPF_MAP_TYPE_PERCPU_ARRAY` | 同上，array 版 | per-CPU 統計 |
| `BPF_MAP_TYPE_LRU_HASH` | hash + LRU 淘汰 | 滿了會自動踢舊的 |
| `BPF_MAP_TYPE_LRU_PERCPU_HASH` | 結合 LRU + percpu | 高頻 + 容量受限 |

### 2. 特殊查找結構

| Type | 特性 | 典型用途 |
|---|---|---|
| `BPF_MAP_TYPE_LPM_TRIE` | longest prefix match | IP 路由、CIDR 比對 |
| `BPF_MAP_TYPE_QUEUE` | FIFO | task queue |
| `BPF_MAP_TYPE_STACK` | LIFO | 較少用 |
| `BPF_MAP_TYPE_BLOOM_FILTER` | 機率成員測試（5.16+） | 大型 set 快速排除 |

### 3. user/kernel 通訊

| Type | 特性 | 典型用途 |
|---|---|---|
| `BPF_MAP_TYPE_RINGBUF` | 多生產者、單消費者 ring buffer | event 串流（**現代首選**） |
| `BPF_MAP_TYPE_PERF_EVENT_ARRAY` | per-CPU perf buffer | event 串流（舊） |

### 4. 程式組合（Ch 26 詳述）

| Type | 特性 | 典型用途 |
|---|---|---|
| `BPF_MAP_TYPE_PROG_ARRAY` | value 是 BPF program fd | tail call |
| `BPF_MAP_TYPE_ARRAY_OF_MAPS` | value 是 map fd | 動態 dispatch |
| `BPF_MAP_TYPE_HASH_OF_MAPS` | value 是 map fd | 同上 |

### 5. 網路特化（Part 5 細講）

| Type | 用途 |
|---|---|
| `BPF_MAP_TYPE_SOCKMAP` | socket 重新導向 |
| `BPF_MAP_TYPE_SOCKHASH` | 同上 hash 版 |
| `BPF_MAP_TYPE_DEVMAP` | XDP 跨 device redirect |
| `BPF_MAP_TYPE_CPUMAP` | XDP 跨 CPU redirect |

完整列表：`enum bpf_map_type` in `include/uapi/linux/bpf.h`，目前 30+ 種。

## 三大主力：HASH / ARRAY / PERCPU

90% 場景靠這三家。逐個看。

### HASH

```c
struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __type(key, u32);
    __type(value, u64);
    __uint(max_entries, 10240);
} pid_counts SEC(".maps");

// kernel 端
u32 pid = bpf_get_current_pid_tgid() >> 32;
u64 *count = bpf_map_lookup_elem(&pid_counts, &pid);
if (count) {
    __sync_fetch_and_add(count, 1);   // 原子加，多核安全
} else {
    u64 init = 1;
    bpf_map_update_elem(&pid_counts, &pid, &init, BPF_NOEXIST);
}
```

**注意 race condition**：lookup 跟 update 不是原子的。兩個 CPU 同時 lookup 都拿不到、都 update — 後 update 的會贏（BPF_ANY 模式）。

要解決 race，要嘛用 `BPF_NOEXIST`（已存在就失敗），要嘛用 percpu map（下面會講）。

### ARRAY

```c
struct {
    __uint(type, BPF_MAP_TYPE_ARRAY);
    __type(key, u32);
    __type(value, struct config);
    __uint(max_entries, 1);   // 常見 idiom：用 1-element array 當 global 變數
} cfg SEC(".maps");
```

Array 最大特色：**所有 entry 在 map 建立時就分配記憶體**，lookup 永遠 success。沒有「key 不存在」的概念 — 用 BPF_MAP_TYPE_ARRAY 拿到的指標一定不是 NULL，但 verifier 仍要求你檢查（為了通用性）。

常見用法：把一個 struct 當「全域 config」放 1-entry array 裡。

### PERCPU 系列

**這是 BPF 高效計數器的關鍵設計**。Percpu map 為**每個 CPU 各分配一份 value**：

```
PERCPU_HASH 內部結構（key=K 為例）：
                ┌──────────┐
key K  ──→  CPU 0: value_0  │
            ├──────────┤
            CPU 1: value_1   │
            ├──────────┤
            CPU 2: value_2   │
            ├──────────┤
            CPU 3: value_3   │
            └──────────┘
```

意思是：

- BPF kernel 端 lookup K，**只看到自己這顆 CPU 的那份**
- 累加完全無 lock、無 atomic — 因為單一 CPU 自己跟自己沒競爭
- user space 讀的時候會拿到 array of values（每顆 CPU 一個），自己加總

**對「高頻寫入、最終彙總」的場景，percpu 比 hash 快非常多**。execsnoop 之類的計數器都用 percpu。

```c
// kernel 端
u64 *cnt = bpf_map_lookup_elem(&pcpu_counts, &key);
if (cnt) (*cnt)++;   // 不用 atomic，單 CPU 安全

// user 端拿到的是 array
u64 vals[nr_cpus];
bpf_map__lookup_elem(map, &key, sizeof(key), vals, sizeof(vals), 0);
u64 total = 0;
for (int i = 0; i < nr_cpus; i++) total += vals[i];
```

## LPM_TRIE：IP 路由 / CIDR 比對

普通 hash 不能做「`192.168.0.0/16` 比對所有屬於這段的 IP」。LPM_TRIE 專門解這個：

```c
struct {
    __uint(type, BPF_MAP_TYPE_LPM_TRIE);
    __type(key, struct {
        u32 prefixlen;     // 前綴長度
        u32 addr;          // IP
    });
    __type(value, u32);    // 例如 action
    __uint(max_entries, 1024);
    __uint(map_flags, BPF_F_NO_PREALLOC);   // LPM 必須加這個 flag
} acl SEC(".maps");
```

lookup 時 kernel 會找「最長 prefix match」 — 跟路由表 / firewall ACL 相同邏輯。Ch 19 寫 XDP 防火牆會用到。

## RINGBUF：上報 event 的現代方式

5.8 加入。`PERF_EVENT_ARRAY`（perf buffer）的取代品：

```c
struct {
    __uint(type, BPF_MAP_TYPE_RINGBUF);
    __uint(max_entries, 256 * 1024);   // 256 KB ring
} events SEC(".maps");

// 寫入（kernel 端）
struct event *e = bpf_ringbuf_reserve(&events, sizeof(*e), 0);
if (!e) return 0;
e->pid = pid;
e->ts  = bpf_ktime_get_ns();
bpf_ringbuf_submit(e, 0);
```

特性對照（Ch 25 會深入比較）：

| | perf buffer | ring buffer |
|---|---|---|
| 結構 | per-CPU | 全域共享（多生產者單消費者） |
| 順序 | 各 CPU 自己有序 | **全域有序** |
| 記憶體 | 每 CPU 都要分 buffer | 一份就好 |
| Wake-up | 每事件都會 | 自適應，效率較好 |
| Kernel 版本 | 4.3+ | 5.8+ |

**寫新 code 一律用 ringbuf**。perfbuf 還在 BCC 老工具裡常見。

## Map operations from user space

User space 透過 libbpf API 或裸 `bpf()` syscall 操作 map：

```c
// libbpf style
struct bpf_map *map = bpf_object__find_map_by_name(obj, "pid_counts");

u32 key = 1234;
u64 value;
bpf_map__lookup_elem(map, &key, sizeof(key), &value, sizeof(value), 0);

// 遍歷 map
u32 prev = 0, cur;
while (bpf_map__get_next_key(map, &prev, &cur, sizeof(cur)) == 0) {
    bpf_map__lookup_elem(map, &cur, sizeof(cur), &value, sizeof(value), 0);
    printf("pid=%u count=%llu\n", cur, value);
    prev = cur;
}
```

或從 command line：

```bash
sudo bpftool map list
sudo bpftool map dump id <map_id>
sudo bpftool map update id <map_id> key 0x12 0x34 value 0x56 0x78
```

## Pinning：讓 map 活過 user process

預設 map 跟 BPF program 一起載入、user process 死掉就 map 沒了。但有時你要：

- user process 重啟，map 內容保留
- 多個獨立 process 共享同一個 map

解法：把 map **pin 到 bpffs**（一個 kernel 提供的虛擬檔案系統）：

```bash
sudo mount -t bpf bpf /sys/fs/bpf   # 通常已經 mounted
sudo bpftool map pin id <map_id> /sys/fs/bpf/my_pinned_map
```

或在 BPF C 裡直接宣告 pinned：

```c
struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(pinning, LIBBPF_PIN_BY_NAME);   // 自動 pin to /sys/fs/bpf/<map_name>
    ...
} my_map SEC(".maps");
```

之後別的 process 可以 `bpf_obj_get("/sys/fs/bpf/my_map")` 拿到同一份 map。

**Cilium 等大型 BPF 系統大量靠 pinning** 做組件解耦 — daemon 重啟時 dataplane 不掉。

## 容量限制與記憶體預算

`max_entries` 不只是上限，**它決定 kernel 預先分配多少記憶體**（除非加 `BPF_F_NO_PREALLOC`）。

例：`HASH` map、key 8 byte、value 32 byte、max_entries 100 萬 — 記憶體大概 100MB+（hash bucket 額外開銷）。

這些記憶體會算到 `memlock` 限額。新 kernel 改用 cgroup memory accounting，舊的還會卡 ulimit。Cilium 之類大型系統會調 sysctl 讓 BPF 能拿夠記憶體。

## 一個常見誤解

「map 操作都是 thread-safe」 — **不全然**。

- `bpf_map_lookup_elem` + 修改回傳指標 — **不是 atomic**。多 CPU 同時改要用 `__sync_fetch_and_add` 或 percpu。
- `bpf_map_update_elem` 本身是 atomic 的（kernel 內有 lock），但「讀-改-寫」三步驟不是。
- LRU map 的 LRU bookkeeping 在高並發下可能有 race，導致「應該保留的被踢掉」 — 這是已知 trade-off。

## 動手練習

1. **看你機器上有哪些 map**：
   ```bash
   sudo bpftool map list
   ```
2. **dump 一個 map 的內容**（找個 systemd 的 array map 之類）：
   ```bash
   sudo bpftool map dump id <id>
   ```
3. **pin 一個 map 試試**：先用 bpftrace 跑個 one-liner 創 map，再 pin：
   ```bash
   sudo bpftrace -e 'kprobe:vfs_read { @[comm] = count(); }' &
   sleep 2
   sudo bpftool map list | grep -i bpftrace   # 找 map id
   sudo bpftool map pin id <id> /sys/fs/bpf/test_map
   ls -la /sys/fs/bpf/
   ```
4. **算記憶體**：宣告一個 HASH map、key u32、value 一個 1024 byte 的 struct、max_entries 1 萬 — 估算記憶體用量（提示：bucket 開銷約 1.5–2x value size）。

## 自我檢核

- [ ] 我能說出 maps 解決 BPF 的三大痛點
- [ ] 我能解釋 PERCPU map 為什麼比一般 HASH 快
- [ ] 我能說出 ringbuf 跟 perfbuf 的核心差別
- [ ] 我能解釋什麼時候要 pin map
- [ ] 我能說出至少一個 map 操作的 race condition 場景

下一章我們進 BPF 最痛苦也最有特色的東西：**verifier**。為什麼你的 code 一直被拒絕、verifier log 怎麼讀、bounded loop 是什麼。讀完之後，你的 BPF debug 能力會升級一個檔次。

→ [Ch 9 Verifier 深入：為什麼你的 BPF 會被拒絕](./09-verifier-deep-dive.md)
