# Ch 1 — 為什麼是 eBPF？

> **目標**：理解 eBPF 在 Linux 生態系中的定位——它解決了什麼根本問題、為什麼不用 kernel module、歷史上怎麼演化到今天、以及它的三大應用域和它的限制在哪裡。

## 為什麼需要這個？

在 eBPF 出現之前，如果你想在 Linux 上做這些事：

- 追蹤某個 process 每次呼叫 `read()` 時讀了哪個 fd、讀了幾個 bytes
- 在封包進入網路卡之後、進入 kernel 之前就把它丟掉
- 強制某個 container 不能呼叫 `ptrace()`

你有幾個選擇，每個都有致命缺點：

**選擇一：strace**。原理是用 `ptrace()` 攔截每一個 syscall，在 kernel 和 userspace 之間來回切換。每攔截一個 syscall 就是兩次 context switch，overhead 高到在生產環境完全不可行——在繁忙服務上開 strace，系統直接變慢幾十倍。

**選擇二：kernel module**。直接寫 kernel module，要什麼資訊就拿什麼。問題是 kernel module 在 ring 0 執行，一個 bug 就是系統 crash，而且每換一個 kernel 版本就要重新編譯。在生產環境推一個客製化 kernel module 的審核流程通常需要幾個月。

**選擇三：修改 kernel source**。把 instrumentation 直接加進 kernel。問題是你的修改需要合進 upstream，或者你要自己維護一個 kernel fork——沒有任何公司有資源這樣做。

**選擇四：SystemTap / DTrace**。這兩個工具都試圖解決問題，但有自己的限制：SystemTap 需要 kernel debug symbols 和 kernel-devel 套件，腳本語言不穩定，有時在 production kernel 上跑不起來；DTrace 只在 Solaris/macOS 上成熟，Linux port 長期是二等公民。

eBPF 的答案是：**讓你把任意程式注入到 kernel 裡執行，但在執行之前用 verifier 靜態地證明這段程式不會讓 kernel crash、不會出界存取記憶體、不會陷入無窮迴圈**。

## 先建立直覺：eBPF 是什麼？

把 eBPF 想成 kernel 裡的一個 JVM。

Java JVM 的問題是你想在 server 上執行來自外部的不信任 Java bytecode，但又不能讓它存取任意記憶體或讓 server crash。JVM 的解法是：定義一個 sandboxed bytecode 格式，在執行前做安全性檢查，在執行時做邊界檢查。

eBPF 做的是同樣的事，只是目標是 kernel 而不是 server：

```
                    你寫的程式（C / bpftrace script）
                              │
                              ▼ 編譯
                    BPF bytecode（受限的 ISA）
                              │
                              ▼ bpf() syscall 載入
                    ┌─────────────────────────┐
                    │    kernel verifier       │
                    │  （靜態安全性分析）       │
                    │  - 沒有無窮迴圈          │
                    │  - 沒有越界記憶體存取    │
                    │  - 所有 pointer 已初始化 │
                    └─────────┬───────────────┘
                              │ 通過
                              ▼
                    JIT compiler（x86-64 / ARM64 / ...）
                              │
                              ▼
                    native code，掛在 kernel hook 上執行
```

和 kernel module 的差別：kernel module 不需要過 verifier，一個野指標就讓機器直接 kernel panic；eBPF 程式先過 verifier，通過才執行。

和 strace 的差別：strace 用 ptrace 做 userspace 攔截，每次要 context switch 兩次；eBPF 在 kernel 內部執行，沒有 user/kernel 切換的 overhead。

## 歷史演進：從 1992 到現在

### 1992：Classic BPF

Steven McCanne 和 Van Jacobson 在 USENIX 1993 發表了 BSD Packet Filter（BPF）。核心想法：不要把封包從 kernel 傳到 userspace 再做過濾，讓 userspace 提供一個 filter 程式，在 kernel 裡執行，只把符合條件的封包傳出去。

這就是 `tcpdump` 的基礎。你執行 `tcpdump 'tcp and port 80'`，tcpdump 把你的 filter 表達式編譯成 Classic BPF bytecode，透過 `setsockopt(SO_ATTACH_FILTER)` 注入到 kernel，kernel 用一個小型 virtual machine 執行這個 filter。

Classic BPF 的 VM 很簡陋：只有兩個 32-bit 暫存器（accumulator 和 index）、固定大小的 scratch memory、有限的指令集。它能做的事只有封包過濾。

### 2012：seccomp-bpf（kernel 3.5）

Will Drewry 把 Classic BPF 重新利用到 syscall 過濾上：你提供一個 BPF 程式，它讀取 syscall 號碼和參數，回傳 ALLOW / DENY / 其他 action。這就是 seccomp-bpf，Chrome 和 Docker 用它來限制 process 能呼叫的 syscall。

### 2013–2014：Extended BPF（eBPF）

Alexei Starovoitov 在 kernel 3.15-3.18 期間把 Classic BPF 從根本上改造：

- 暫存器從 2 個擴充到 11 個，全部 64-bit
- 增加 BPF maps：讓 BPF 程式可以保存狀態，和 userspace 共享資料
- 增加更多 program type：不只是 socket filter，還有 kprobe、tracepoint 等
- 增加 in-kernel JIT compiler（x86-64），讓 BPF 程式以接近 native 的速度執行

這個「extended」版本就是今天說的 eBPF（雖然官方現在直接說 BPF，不再強調 extended）。

### 2015–2018：工具成熟期

- 2015：BCC 工具集出現，讓你用 Python 寫 userspace、用 C 寫 kernel-side
- 2016：XDP（eXpress Data Path）進入 kernel，讓 eBPF 在網路卡 driver 層處理封包
- 2018：BTF（BPF Type Format）進入 kernel 4.18，解決了 debug info 的攜帶問題

### 2020–現在：CO-RE 時代

- 2020：CO-RE（Compile Once Run Everywhere）配合 libbpf 0.1 實用化，解決了 eBPF 程式跨 kernel 版本相容的問題
- 2020：ringbuf（kernel 5.8）取代 perfbuf 成為推薦的事件傳輸機制
- 2021：BPF-LSM 讓 eBPF 用於強制存取控制
- 2023+：BPF arena（大型共享記憶體）、BPF exception、更多進階功能陸續加入

## eBPF 的架構全圖

```
userspace                    kernel
─────────                    ──────
                             ┌──── kernel hooks ─────────────────────────┐
你的程式 ──bpf() syscall──▶  │  kprobe / kretprobe                       │
（libbpf/                    │  tracepoint / raw_tracepoint               │
 BCC/bpftrace）              │  fentry / fexit                            │
                             │  XDP (network driver)                      │
                             │  TC (traffic control)                      │
                             │  socket filter                             │
                             │  cgroup hooks                              │
                             │  LSM hooks                                 │
                             └───────────────────────────────────────────┘
                                        │ 事件觸發時
                                        ▼
                             ┌──── BPF program ──────────┐
                             │  JIT-compiled native code │
                             │  執行在 kernel context     │
                             │  可以讀取 kernel 資料       │
                             │  可以存取 BPF maps          │
                             └───────────┬───────────────┘
                                         │ 透過 maps 共享資料
                             ┌───────────▼───────────────┐
                             │       BPF maps             │
                             │  hash / array / ringbuf   │
                             └───────────┬───────────────┘
                                         │
你的 userspace 程式 ◀────────────────────┘
（讀取事件、展示結果、觸發 action）
```

## 三大應用域

### Observability（可觀測性）

這是 eBPF 最成熟的應用。你可以在幾乎任何 kernel 或 userspace 函式上掛一個 BPF 程式，收集執行時資訊：

- `execsnoop`：追蹤所有 `execve()` 呼叫，知道有哪些 process 被建立
- `tcpconnect`：追蹤 TCP 連線的建立，記錄 source/destination IP 和 port
- off-CPU analysis：追蹤 process 因為什麼原因被 scheduler 排開，找出 blocking I/O 的根源
- memory allocation profiler：追蹤 `malloc()`/`free()`，找出記憶體洩漏

Datadog Agent、Grafana Beyla、OpenTelemetry 的 auto-instrumentation 都用 eBPF 做這件事。

### Networking

eBPF 可以在多個 networking stack 層次介入：

- **XDP**：在網路卡 driver 層就處理封包，甚至在 kernel 分配 skb 之前。這是目前 Linux 上最快的 packet processing 方式
- **TC（Traffic Control）**：在 kernel 的 traffic control 層做 ingress/egress 過濾和整形
- **Socket level**：在 socket 層做 socket redirection，讓兩個 socket 之間的資料不用穿越完整的 networking stack

Cilium（Kubernetes CNI）用 eBPF 取代 iptables 和 kube-proxy，實現 identity-based network policy；Cloudflare 用 XDP 做 DDoS mitigation。

### Security

- **seccomp-bpf**：過濾 process 能呼叫的 syscall，是 container runtime 的標準安全機制
- **BPF-LSM**：透過 Linux Security Module 的 hook 實施強制存取控制（MAC）
- **Falco / Tetragon**：基於 eBPF 的 runtime security，偵測異常行為

## eBPF 的限制（設計邊界，不是 bug）

eBPF 的限制不是設計缺陷，而是讓 verifier 能靜態證明安全性的必要代價：

| 限制 | 原因 | 如何繞過（若有必要） |
|---|---|---|
| Stack 大小 512 bytes | verifier 要靜態追蹤所有 stack 存取 | 用 BPF maps 當 heap |
| 不能呼叫任意 kernel 函式 | 只能呼叫 approved helper functions | 用 helper 間接操作 |
| Loop 次數有上限（kernel 5.3 前完全不允許） | 防止無窮迴圈 | 用 tail calls 或 bounded loop |
| 不能 sleep 或 block | kernel context 的基本要求 | 用 BPF timer 做非同步邏輯 |
| Program 大小有限（100 萬 instructions） | verifier 分析時間的上限 | 拆成多個 program 用 tail call 串接 |
| 不能直接存取任意 kernel pointer | 需要 verifier 追蹤 pointer validity | 用 `bpf_probe_read_kernel()` |

> **注意**：Bounded loop（`for (int i = 0; i < N; i++)`，N 是編譯期常數）在 kernel 5.3+ 支援。動態長度的 loop 需要用 `bpf_loop()` helper（kernel 5.17+）。

## 踩雷集錦

1. **「eBPF 是 kernel module 的替代品」**：這個說法不完全對。kernel module 可以做任何事；eBPF 只能做 verifier 允許的事。eBPF 是「安全的 kernel 擴充機制」，不是「功能等同 kernel module 的東西」

2. **「eBPF 程式跑在 userspace」**：完全錯誤。eBPF 程式跑在 kernel context，和 kernel 共享記憶體空間。只是 verifier 保證它不會讓 kernel crash

3. **「eBPF overhead 可以忽略不計」**：錯。eBPF 程式有 overhead，只是比 ptrace 低很多。在每個 syscall 都掛 BPF program 的情況下，overhead 可能達到 5–15%。要 benchmark 再決定要不要用

4. **「verifier 保證了正確性」**：verifier 只保證「不會 crash kernel」，不保證你的程式邏輯是正確的。你的 BPF program 可以通過 verifier 但收集到錯誤的資料

5. **「eBPF 只能做 observability」**：早期的用法以 observability 為主，但現在 XDP/TC 做 networking、seccomp/LSM 做 security 的應用都很成熟

## 進階：再往深一層

**eBPF 的 verifier 和 SMT solver 的關係**：verifier 本質上做的是 abstract interpretation——把 register 的值域抽象化成 value range、tracked pointer、scalar 等類型，在整個 control flow graph 上做 dataflow analysis。這和形式驗證領域的 abstract interpretation framework 同源，但做了很多 BPF 特定的優化。

**JIT 的現況**：目前 x86-64、ARM64、MIPS、PowerPC、s390 都有 JIT compiler；在沒有 JIT 的平台上，BPF bytecode 用 interpreter 執行（效能低很多）。`/proc/sys/net/core/bpf_jit_enable` 可以查 / 開關 JIT。

**eBPF 之外的沙箱技術**：和 eBPF 做類似事情的還有 WebAssembly（WASM）for kernel extension，以及 Rust kernel driver（safety 由型別系統保證）。比較這三種方法是個好的面試題目。

## 動手練習

執行 `sudo bpftool feature probe`，找出以下問題的答案：

1. 你的 kernel 支援哪些 program type？哪些不支援？
2. `BPF_MAP_TYPE_RINGBUF` 有沒有支援？如果沒有，你的 kernel 版本是多少？
3. `bpf_get_current_task_btf` helper 有沒有支援？這個 helper 是做什麼的？

## 本章重點整理

- eBPF 解決了「在 kernel 執行任意程式」和「保證 kernel 安全」這兩個目標之間的矛盾，透過靜態 verifier 而不是 runtime sandbox
- 歷史上從 1992 年的 Classic BPF packet filter 演化，2013–2014 被徹底重設計成 eBPF
- 三大應用域：observability（追蹤）、networking（高效封包處理）、security（系統呼叫過濾、強制存取控制）
- eBPF 的限制（stack size、無法 sleep、需要 helper 才能讀 kernel 記憶體）不是 bug，是讓 verifier 能靜態證明安全性的必要代價

## 自我檢核

- [ ] 能不看筆記解釋「為什麼 strace 在生產環境不能用，而 eBPF 可以」
- [ ] 能說出 eBPF 和 kernel module 各自適合什麼場景
- [ ] 能說出 BTF 在 eBPF 歷史演化中解決了什麼問題（不只是「它是一個 format」）
- [ ] 知道 eBPF 程式的 stack 大小限制是多少，以及當你需要更多空間時的正確做法

## 延伸閱讀

### 論文

- **[The BSD Packet Filter: A New Architecture for User-level Packet Capture](https://www.tcpdump.org/papers/bpf-usenix93.pdf)** — McCanne & Jacobson, USENIX Winter 1993
  - **核心貢獻**：提出在 kernel 裡執行 filter 程式而不是在 userspace 過濾封包，大幅降低 overhead；是 eBPF 的直接前身
  - **讀哪裡**：Section 2（BPF VM 設計）和 Section 3（filter 語言）；Section 4 的效能數字已過時但結構值得看
  - **和本章的關聯**：理解 Classic BPF 的設計動機，能回答「eBPF 的 extended 擴充了什麼」

- **[eBPF: In-kernel Virtual Machine](https://www.kernel.org/doc/ols/2015/ols2015-final-jberkus.pdf)** — Alexei Starovoitov, OLS 2015
  - **核心貢獻**：eBPF 主要設計者說明設計決策——為什麼擴充暫存器、為什麼加 maps、JIT 的實作細節
  - **讀哪裡**：整篇；這是歷史文件，幫助你理解設計背後的 WHY
  - **和本章的關聯**：理解 eBPF 和 Classic BPF 的技術差異

### 部落格 / 文章

- **[eBPF - The Future of Networking & Security](https://cilium.io/blog/2020/11/10/ebpf-future-of-networking/)** — Thomas Graf（Cilium 創辦人）, 2020
  - **這篇說什麼**：從 networking 和 security 角度解釋 eBPF 為什麼重要；用具體例子說明 eBPF 如何取代 iptables
  - **讀哪裡**：整篇；特別是「Why eBPF」那幾節
  - **為什麼值得讀**：Thomas Graf 是業界最了解 eBPF networking 應用的人之一；這篇提供了很好的宏觀視角

- **[A thorough introduction to eBPF](https://lwn.net/Articles/740157/)** — Matt Fleming, LWN.net, 2017
  - **這篇說什麼**：kernel 角度的技術入門，解釋 maps、helper、verifier 的設計
  - **讀哪裡**：整篇；雖然 2017 年，核心概念不過時
  - **為什麼值得讀**：LWN 的文章品質高，寫者是 kernel 開發者，不是 tutorial 農場

### 官方文件

- **[Linux kernel BPF documentation — Overview](https://www.kernel.org/doc/html/latest/bpf/index.html)**
  - **讀哪裡**：`index.html` 本身；找到 "BPF Design Q&A" 那份文件，讀完整份
  - **學什麼**：kernel 開發者對 eBPF 設計決策的官方解釋；為什麼某些功能被拒絕、為什麼某些限制存在
  - **前提**：讀完本章之後再讀，會有很多 "aha" 時刻

→ [Ch 2 你需要的 Linux kernel 底層知識](./02-linux-kernel-basics.md)
