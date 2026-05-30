# Ch 24 — Profiling 與 Flamegraph

> **目標**：理解 CPU profiling 的完整流程——從 BPF 的 stack trace 採集、到 flamegraph 的生成、到讀懂 flamegraph 的技術——以及 off-CPU、memory allocation profiling 的方法。

## 先建立直覺：Flamegraph 是什麼？

Flamegraph 是一種視覺化 stack trace 的方式，讓你一眼看出程式在哪裡花最多 CPU：

```
                ┌───────────────────────────────────────────────────────┐
                │               main（整個寬度 = 100% CPU）              │
                ├────────────────────────┬──────────────────────────────┤
                │     handle_request     │         idle loop            │
                ├──────────────┬─────────┤
                │   db_query   │  render │
                ├──────┬───────┤
                │ exec │ parse │
                └──────┴───────┘

X 軸 = 時間比例（每個 frame 的寬度 = 它佔的 CPU 時間）
Y 軸 = call depth（上面的呼叫下面的）
顏色 = 隨機（不代表熱度，只是區分 frame）
```

寬的 frame = 花時間多 = 優化目標。

## 完整的 CPU Profiling 流程

### Step 1：用 bpftrace 採集 stack trace

```bash
sudo bpftrace -e '
profile:hz:99 {
    @[comm, kstack, ustack] = count();
}
interval:s:30 {
    print(@);
    clear(@);
    exit();
}' > /tmp/profile_raw.txt
```

### Step 2：折疊 stack（stackcollapse）

FlameGraph 工具包的 `stackcollapse-bpftrace.pl` 把 bpftrace 輸出轉成折疊格式：

```bash
# 安裝 FlameGraph 工具包
git clone https://github.com/brendangregg/FlameGraph
cd FlameGraph

# 折疊 stack
cat /tmp/profile_raw.txt | ./stackcollapse-bpftrace.pl > /tmp/profile_folded.txt

# 折疊後的格式（每行一個 stack）：
# bash;main;handle_cmd;bash_execute;execute_command 42
# bash;main;handle_cmd;readline 8
```

### Step 3：生成 SVG

```bash
./flamegraph.pl /tmp/profile_folded.txt > /tmp/flame.svg
# 用瀏覽器打開
firefox /tmp/flame.svg
```

### Step 4：讀懂 Flamegraph

```
讀 flamegraph 的方法：

1. 找最寬的 frame（最耗 CPU 的）
   → 這是你的優化目標

2. 看「平台」（plataeu）：有很多 frame 指向同一個底部函式
   → 代表很多不同 code path 都在等同一個操作

3. 看高度（call stack 深度）
   → 很深的 stack 通常意味著複雜的 abstraction layer

4. interactive SVG：點擊 frame 可以 zoom in
   → 看某個函式的 sub-callees
```

## BCC 的 profile 工具（更簡便）

```bash
# BCC 的 profile 工具一鍵生成 flamegraph
sudo /usr/share/bcc/tools/profile -F 99 -f 30 > /tmp/bcc_profile.txt
/path/to/FlameGraph/flamegraph.pl /tmp/bcc_profile.txt > flame.svg
```

`profile` 工具會同時採集 kernel stack 和 userspace stack。

## Off-CPU Flamegraph

Off-CPU flamegraph 顯示程式在「不使用 CPU」時在哪裡等待（disk I/O、lock、sleep）：

```bash
# 用 BCC 的 offcputime 工具
sudo /usr/share/bcc/tools/offcputime -f 30 > /tmp/offcpu.txt
/path/to/FlameGraph/flamegraph.pl --color=io --title="Off-CPU Flame Graph" \
    /tmp/offcpu.txt > offcpu.svg
```

解讀：off-CPU flamegraph 裡的 wide frame 代表 process 在那段 code 裡 blocked 的時間最長。

## Memory Allocation Profiling

找出誰在分配記憶體（追蹤 `kmalloc` / `malloc`）：

```bash
# kernel memory allocation（追蹤 kmalloc）
sudo bpftrace -e '
kprobe:kmalloc {
    @alloc_size[kstack] = sum(arg1);  /* arg1 = size */
}
interval:s:10 { print(@alloc_size); exit(); }'

# userspace malloc（libc）
sudo bpftrace -e '
uprobe:/lib/x86_64-linux-gnu/libc.so.6:malloc {
    @alloc_size[ustack, comm] = sum(arg0);
}
interval:s:10 { print(@alloc_size); exit(); }'
```

## libbpf 做 CPU Profiling

用 libbpf 做更精確的控制：

```c
/* profiler.bpf.c */
struct {
    __uint(type, BPF_MAP_TYPE_STACK_TRACE);
    __uint(key_size, sizeof(u32));
    __uint(value_size, 127 * sizeof(u64));
    __uint(max_entries, 16384);
} stacks SEC(".maps");

/* (pid, user_stack_id, kernel_stack_id) → count */
struct stack_key { u32 pid; s32 user_stack; s32 kern_stack; char comm[16]; };

struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 16384);
    __type(key, struct stack_key);
    __type(value, u64);
} counts SEC(".maps");

SEC("perf_event")
int do_sample(struct bpf_perf_event_data *ctx)
{
    struct stack_key key = {};
    key.pid = bpf_get_current_pid_tgid() >> 32;
    key.user_stack = bpf_get_stackid(&ctx->regs, &stacks,
                                      BPF_F_USER_STACK | BPF_F_REUSE_STACKID);
    key.kern_stack = bpf_get_stackid(&ctx->regs, &stacks,
                                      BPF_F_REUSE_STACKID);
    bpf_get_current_comm(&key.comm, sizeof(key.comm));

    u64 zero = 0;
    u64 *cnt = bpf_map_lookup_or_try_init(&counts, &key, &zero);
    if (cnt) (*cnt)++;
    return 0;
}
```

**Userspace 符號解析（symbol resolution）**：

採集到的 stack trace 是 instruction pointer 陣列。要把 IP 轉成函式名稱，需要：

1. **Kernel IP**：從 `/proc/kallsyms` 查
2. **Userspace IP**：從 `/proc/<pid>/maps` + ELF symbol table 查，或用 `libunwind`、`addr2line`

```c
/* 簡單的 kernel symbol lookup */
u64 ip = 0xffffffff81234567;
/* 讀 /proc/kallsyms，找最近的比 ip 小的 symbol */
```

BCC 和 bpftrace 都自動做 symbol resolution。libbpf 工具需要自己處理（或用 `blazesym` 庫）。

## 踩雷集錦

1. **Flamegraph 沒有 userspace frame**：可能是 binary strip 了 debug symbol（`strip -s`）；加 `-g` 重新編譯，或安裝 debug symbol package（`apt install <pkg>-dbg`）

2. **bpftrace 的 userspace stack 顯示 `[unknown]`**：和上面一樣，缺少 symbol；也可能是 JIT-compiled code（Java、JavaScript）——需要 JVMTI agent 或 perf-map 工具生成 `/tmp/perf-<pid>.map`

3. **Profile 結果裡有很多 `do_idle`**：這是正常的——CPU 沒有工作時跑 idle loop；在 flamegraph 裡可以過濾掉（`grep -v do_idle`）

4. **採樣頻率太高導致 overhead 可觀**：99 Hz 通常 overhead < 1%；1000 Hz 可能達 5–10%；production 上不要超過 99 Hz 除非你知道 overhead

5. **multi-threaded 程式的 flamegraph**：每個 CPU 上的 thread 獨立採樣；最後 merge 所有 CPU 的結果才能看全貌

## 動手練習

1. 對一個你知道很慢的程式（例如 `find / -name "*.so" 2>/dev/null`）做 30 秒 CPU profile，生成 flamegraph，找出最寬的 frame

2. 對同一個程式做 off-CPU flamegraph，比較 on-CPU 和 off-CPU 的時間比例

3. 用 `bpftrace -e 'uprobe:/lib/x86_64-linux-gnu/libc.so.6:malloc ...'` 找出在 `find` 執行期間哪些 call site 分配了最多記憶體

## 本章重點整理

- Flamegraph 的 X 軸是時間比例，Y 軸是 call depth；最寬的 frame 是優化目標
- 完整流程：BPF 採集 stack trace → stackcollapse 折疊 → flamegraph.pl 生成 SVG
- Off-CPU flamegraph 找等待（I/O、lock）；On-CPU flamegraph 找計算
- Userspace frame 顯示 `[unknown]` 通常是缺少 debug symbol

## 自我檢核

- [ ] 能讀懂一個 flamegraph，找出最耗時的 code path
- [ ] 知道 on-CPU 和 off-CPU flamegraph 各自採集什麼，以及用哪個工具
- [ ] 能說出 `[unknown]` frame 的常見原因和解法

→ [Ch 25 ringbuf vs perfbuf：事件傳輸設計](./25-ringbuf-vs-perfbuf.md)
