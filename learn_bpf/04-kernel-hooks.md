# Ch 4 — Kernel 鉤子機制：kprobe / uprobe / tracepoint / fentry

> 目標：BPF 程式不會自己跑，必須掛在某個事件上。本章認識 kernel 提供的四大鉤子機制，搞懂各自能掛在哪、開銷多少、穩定度如何 — 這決定你寫 BPF 時要選哪一種 attach 方式。

## Hook 是什麼？為什麼需要它？

回想 Ch 1 講的：BPF 程式被載入後，**它要等一個事件來觸發**。沒事件就不跑。

這個「事件」可以是：
- 某個 kernel function 被呼叫
- 某個 syscall 進入
- 某個網卡收到 packet
- 某個 process 開檔案
- 某個硬體 perf counter 溢位

每一種事件背後都有一個 **kernel 機制**負責「這個事件發生時去叫醒掛在這裡的 BPF 程式」。這個機制就叫 **hook（鉤子）**或 **attach point（附著點）**。

```
                ┌──────────────────────────┐
                │  BPF program (你寫的)     │
                └────────────┬─────────────┘
                             │ attach
                             ▼
        ┌──────────────────────────────────────┐
        │   Hook 機制（kprobe / tracepoint /    │
        │            uprobe / fentry / ...）    │
        └────────────┬─────────────────────────┘
                     │ 監聽
                     ▼
        ┌──────────────────────────────────────┐
        │   Kernel / User space 中的某個事件     │
        │   （function call、syscall、packet、...） │
        └──────────────────────────────────────┘
```

**這章就是逐個認識右下那個盒子裡有哪些 hook 可選。**

## 全景表

| Hook 類型 | 掛在哪 | Dynamic / Static | 開銷 | ABI 穩定？ | 經典用途 |
|---|---|---|---|---|---|
| **kprobe** | 任意 kernel 函式入口 | Dynamic | 中 | ❌ 不穩 | 觀察 kernel 內部 |
| **kretprobe** | 任意 kernel 函式出口 | Dynamic | 中–高 | ❌ 不穩 | 拿回傳值、量延遲 |
| **fentry** | 同 kprobe 但更快 | Dynamic | **低** | ❌ 不穩 | kprobe 的現代版 |
| **fexit** | 同 kretprobe 但更快 | Dynamic | **低** | ❌ 不穩 | kretprobe 的現代版 |
| **tracepoint** | kernel 預先標記的點 | **Static** | 低 | ✅ **穩** | 跨 kernel 版本相容的觀測 |
| **raw_tracepoint** | 同 tracepoint 但更原始 | Static | **更低** | ✅ 穩 | 高效能 tracing |
| **uprobe** | 任意 user 程式函式 | Dynamic | **高** | ❌ 取決 binary | 觀察 user 應用 |
| **USDT** | user 應用預先標的 probe | Static | 低 | ✅ 穩 | Postgres / Python / JVM 觀測 |
| **perf_event** | hw counter 或軟體 sampling | Static | 低 | ✅ 穩 | profiling、flamegraph |
| **XDP / TC** | 網路 packet 路徑 | Static | **極低** | ✅ 穩 | Ch 19、20 主角 |
| **LSM / cgroup** | 安全鉤子 / cgroup 事件 | Static | 低 | ✅ 穩 | Ch 23 主角 |

下面四節先講 tracing 場景最常用的四種：kprobe、tracepoint、uprobe、fentry。**XDP / TC / LSM 等 Part 5–6 才細講**，這裡只先在表上有個座標。

## kprobe / kretprobe — kernel 動態鉤子

這是 BPF tracing 的「萬能鉤」。可以掛在**任何 kernel 函式**的入口（kprobe）或出口（kretprobe），執行時拿到所有參數（kprobe）或回傳值（kretprobe）。

底層機制（理解原理用）：
- kprobe 把目標 function 的第一條指令**換成 `int 3` (breakpoint)**
- CPU 跑到那邊觸發 trap → kernel 進中斷 handler → 跑你的 BPF code → 跑回原本第一條指令 → 繼續

寫起來大概是這樣（bpftrace 版本，簡單示意）：

```bash
sudo bpftrace -e 'kprobe:vfs_read { printf("read by %s\n", comm); }'
```

每次有任何 process 呼叫 `vfs_read`（kernel 的檔案讀取入口），你就會收到一行通知。

**痛點 1：ABI 不穩**。`vfs_read` 在不同 kernel 版本可能改名、改參數順序、甚至被 inline 掉消失。你的工具下個 kernel 升級就壞掉。

**痛點 2：開銷中等**。每次觸發都進中斷、複製 register、查 BPF program、再跳回去 — 熱路徑上會吃掉幾十 ns。

**痛點 3：kretprobe 特別貴**。要在 entry 時改 stack 把 return address 換成 trampoline、function 結束時再回到 trampoline、再跳回真正的 caller。比 kprobe 慢、且有併發 race 問題（trampoline 數量有限）。

## tracepoint — kernel 預先標記的「合約」

kernel 開發者在「該被觀察的地方」**主動寫了標記**：

```c
// kernel source 裡（簡化版）
trace_sched_switch(prev, next);
```

這些 tracepoint 是 **API 合約**：kernel 開發者承諾「不會隨便改」，欄位有正式定義。BPF 可以掛上去拿到結構化參數：

```bash
sudo bpftrace -e 'tracepoint:sched:sched_switch {
    printf("%s -> %s\n", args->prev_comm, args->next_comm);
}'
```

**列出系統上所有 tracepoint**：

```bash
sudo bpftrace -l 'tracepoint:*' | head -20
# tracepoint:alarmtimer:alarmtimer_cancel
# tracepoint:alarmtimer:alarmtimer_fired
# tracepoint:alarmtimer:alarmtimer_start
# ...
ls /sys/kernel/tracing/events/                # 同一份資訊
```

**每個 tracepoint 的 schema** 寫在這：

```bash
sudo cat /sys/kernel/tracing/events/sched/sched_switch/format
# name: sched_switch
# ID: 314
# format:
#     field:char prev_comm[16];      offset:8;       size:16;
#     field:pid_t prev_pid;          offset:24;      size:4;
#     ...
```

**選擇的原則**：**有 tracepoint 就用 tracepoint，沒有才用 kprobe**。tracepoint 穩、開銷略低、欄位有定義、跨 kernel 版本可移植。

## uprobe / uretprobe — user space 的動態鉤子

跟 kprobe 同樣機制（換 `int 3`），但目標是 **user space 程式**。可以掛在任何 user binary 的任何 symbol：

```bash
sudo bpftrace -e 'uprobe:/usr/bin/bash:readline { printf("you typed: %s\n", str(retval)); }'
# 在某個 bash session 按 enter → bpftrace 印出你打的內容
```

**痛點 1：開銷極高**。uprobe 比 kprobe 還貴 — user/kernel 切換 + trap + BPF + 切回。在熱路徑（比如每次 function call）會有可量測的影響。

**痛點 2：穩定度取決於 binary**。`malloc` 在 glibc 裡叫 `__libc_malloc`，但 musl 裡可能直接叫 `malloc`，靜態連結的 Go binary 又是另一套 — 你的 uprobe 工具要為每個 runtime 寫一份。

**痛點 3：optimised binary 沒 symbol** — strip 過的 binary 連 function name 都沒了，uprobe 只能用 offset。

對 user space 觀測，**有 USDT 就用 USDT** — 它是 user 版的 tracepoint，穩定且開銷低。Ch 17 會詳細講。

## fentry / fexit — kprobe 的現代版

2020 年左右進 mainline。原理跟 kprobe 完全不同：

- kprobe：**換指令 + trap + 中斷 handler** — 慢
- fentry：**用 BPF trampoline 直接跳** — 沒中斷、沒 trap

效果上差多少？Brendan Gregg 跑過 benchmark：fentry 大概比 kprobe **快 2–3 倍**，fexit 比 kretprobe 快 **5 倍以上**（因為不用搞 trampoline race）。

```bash
sudo bpftrace -e 'fentry:vfs_read { printf("read by %s\n", comm); }'
```

寫法跟 kprobe 幾乎一樣 — bpftrace 把細節藏起來了。

**規則**：**新代碼一律用 fentry/fexit，不要寫 kprobe/kretprobe**。除非你卡在很舊的 kernel（< 5.5），那才退回 kprobe。

但 fentry 也有限制：
- 只能掛 BTF-aware function（kernel 必須開 `CONFIG_DEBUG_INFO_BTF`）
- 部分 function（如 `notrace` 標記的）不能掛
- 不能掛 user space — uprobe 還是無可取代

## 怎麼發現可用的 hook？

**列 kernel 函式（kprobe / fentry 可掛的）**：

```bash
sudo bpftrace -l 'kprobe:vfs_*'
sudo bpftrace -l 'fentry:tcp_*'

# 或直接讀 kernel 給的清單：
cat /proc/kallsyms | head
# 上萬筆 — 不是每個都能掛（有些 inline / notrace）
```

**列 tracepoint**：

```bash
sudo bpftrace -l 'tracepoint:*' | wc -l
# 大概 1500–2000 個
```

**列 user binary 的 uprobe 點**：

```bash
sudo bpftrace -l 'uprobe:/usr/bin/bash:*' | head
# uprobe:/usr/bin/bash:add_history
# uprobe:/usr/bin/bash:readline
# ...
```

**列 USDT 點**：

```bash
sudo bpftrace -l 'usdt:/usr/lib/postgresql/*/bin/postgres:*'
```

`bpftrace -l` 是你以後最常用的「BPF 自助餐菜單」。

## 開銷與穩定度的選擇樹

每次寫 BPF 工具，按這個順序選 attach 方式：

```
要觀察的對象在 kernel？
├── 是
│   ├── 有對應的 tracepoint？──→ 用 tracepoint（穩、便宜）
│   └── 沒有
│       ├── kernel >= 5.5 + BTF？ ──→ 用 fentry/fexit
│       └── 否 ─────────────────→ 用 kprobe/kretprobe（最後手段）
└── 否（在 user space）
    ├── 應用有 USDT？──→ 用 USDT（穩、便宜）
    └── 沒有 ────────→ 用 uprobe（貴、脆弱，但能用）
```

照這個順序走，未來重構與 kernel 升級的痛苦會大幅降低。

## 一個常見誤解

「kprobe 跟 tracepoint 是兩種不同的東西，不能換」 — **不全然**。

事實是：一個 syscall（例如 `openat`）通常**同時有**：
- 一個 syscall tracepoint：`tracepoint:syscalls:sys_enter_openat`
- 一個對應的 kernel function：`kprobe:do_sys_openat2`

兩種都掛得到、看到的東西略不同。tracepoint 拿到的是 user 給的參數（filename string、flags），kprobe 拿到的是 kernel 函式收到的（已經處理過、可能變成 struct）。

**新手選 tracepoint 開始**，遇到「tracepoint 沒給我我要的欄位」再退回 kprobe。

## 動手練習

1. 列出 kernel 裡所有名字含 `tcp` 的 tracepoint：`sudo bpftrace -l 'tracepoint:*tcp*'`，挑一個用 bpftrace 掛上去看看會收到什麼。
2. 比較 `kprobe` vs `fentry` 的存在性：`sudo bpftrace -l 'kprobe:do_sys_openat2'` 跟 `sudo bpftrace -l 'fentry:do_sys_openat2'`，兩個應該都有，下面這個指令掛 fentry 試試：
   ```bash
   sudo bpftrace -e 'fentry:do_sys_openat2 { @[comm] = count(); }'
   ```
   按 Ctrl+C 結束會印一張 histogram — 哪個 process 開檔次數最多。
3. 故意找個會被 inline 的 function 掛 kprobe 看會怎樣（例如 `__list_add` 之類）— 你會看到 `Could not add kprobe: ...` 的錯誤。**體會「不是每個 function 都能掛」**。
4. 找一個你機器上有的服務（postgres、nginx、redis），用 `bpftrace -l 'usdt:<binary>:*'` 看有沒有 USDT 點。

## 自我檢核

- [ ] 我能說出 kprobe 跟 tracepoint 的核心差別（dynamic vs static、ABI 穩定性）
- [ ] 我能說出為什麼 fentry 比 kprobe 快
- [ ] 我能解釋 uprobe 為什麼開銷比 kprobe 還高
- [ ] 我能用一句話描述「選 hook 的優先順序」
- [ ] 我能用 `bpftrace -l` 列出系統上所有可用的 hook

Part 1 結束。下一章開始 Part 2，正式進 BPF 核心架構 — 從歷史的 cBPF 講起，看看 1992 年那個 2 register、30 條指令的迷你 VM 長什麼樣。

→ [Ch 5 classic BPF：為什麼會發明一個 in-kernel VM](./05-classic-bpf.md)
