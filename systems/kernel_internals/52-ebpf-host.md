# Ch 52 — Kernel 如何 host eBPF：verifier、JIT、hooks

> **目標**：從 kernel 端理解一件事——為什麼 kernel 敢讓 userspace 把一段程式碼載進來、在最高特權層跑，卻不會被一個 bug 或惡意程式帶著 panic 或提權。答案是三件套：**受限的指令集 VM**、**載入時的靜態 verifier**、**過驗證後的 JIT**。這章讀 `kernel/bpf/verifier.c` 與 `kernel/bpf/core.c`，看 kernel 怎麼「安全地執行不受信任的程式碼」。

> **這章和 bpf 課的分工**：本 repo 有一整門 `bpf` 課，那門是**使用者視角**——教你怎麼寫 BPF、用 libbpf/bpftrace、掛 XDP、讀 map。這章是**kernel 視角**——kernel 這一側收到一段 bytecode 之後，內部發生什麼、憑什麼相信它。兩門互補：想寫 BPF 去 bpf 課，想知道 verifier 為什麼拒絕你的程式、JIT 出來長什麼樣，看這裡。

## 為什麼需要這個？

kernel 跑在最高特權層，它的位址空間裡沒有邊界——一個能在 kernel context 執行的迴圈，可以讀任何記憶體、寫任何結構、關中斷不放。傳統上，想在 kernel 裡加一段自訂邏輯只有兩條路：

- **改 kernel 源碼重編**——要 root、要重開機、改壞了 panic，不可能給一般 observability 工具用。
- **寫核心模組（Ch 8）**——`insmod` 進來的模組是**完全信任**的原生程式碼，和 kernel 同一個位址空間、同樣特權。一個空指標解參考就是整台機器掛掉。模組能做的壞事，惡意 `.ko` 一樣能做。

這兩條路的共同問題是：**執行的是完全信任、不受檢查的原生碼**。你不能把「讓 nginx 那個 unprivileged 服務掛一段程式去 kernel 裡數封包」建立在「相信這段程式不會寫壞 `task_struct`」之上。

eBPF（extended Berkeley Packet Filter）的整個設計就是為了打破這個困局：**讓 userspace 提交一段程式碼進 kernel 跑，但 kernel 不信任這段程式，而是在載入時證明它安全**。它出現前，封包過濾用的是 classic BPF（cBPF，`tcpdump` 背後那個），一個只能做無狀態封包比對的小 VM。2014 年 Alexei Starovoitov 把它擴展成通用的 kernel 內執行引擎——暫存器變寬、加上 map、加上 helper、加上一個嚴格得多的 verifier。今天它是 observability（Ch 51 kprobe/tracepoint）、網路（Ch 46 XDP/tc）、安全（Ch 48 BPF LSM、Ch 49 seccomp）的共同底座。

核心命題就一句：**如何在 kernel 裡安全地執行不受信任的程式碼？** 這章拆解 kernel 給出的答案。

## 先建立直覺

kernel host BPF 的信任模型，關鍵在**信任的時間點**：它不在**執行時**檢查（那太慢，而且很多性質執行時根本檢查不了），而是在**載入時**一次性地把整段程式靜態證明過關，之後執行時零檢查、全速跑。這是「一次驗證，多次無檢查執行」的賭注——賭注能成立，全靠 verifier 夠嚴。

先看一段 BPF 從 userspace 到真正被觸發執行的完整生命週期：

```
  userspace                                    kernel
 ┌──────────┐
 │ 寫 .c    │  clang -target bpf
 │ 編成     │─────────────┐
 │ bytecode │             │
 └──────────┘             ▼
                    ┌────────────────────────────────────────────────┐
                    │              bpf() syscall                      │
                    │              BPF_PROG_LOAD    (Ch 4 syscall)    │
                    └───────────────────────┬────────────────────────┘
                                            ▼
                    ┌────────────────────────────────────────────────┐
                    │  ① VERIFIER   kernel/bpf/verifier.c            │
                    │     靜態分析：模擬所有路徑，證明                 │
                    │       - 會終止（無無界迴圈）                     │
                    │       - 每次記憶體存取都在界內                   │
                    │       - 不讀未初始化暫存器/stack                │
                    │       - helper 呼叫合法、指標算術受限            │
                    │     不過 → -EACCES / -EINVAL，程式被拒           │
                    └───────────────────────┬────────────────────────┘
                                            ▼ 過關
                    ┌────────────────────────────────────────────────┐
                    │  ② JIT   arch/x86/net/bpf_jit_comp.c           │
                    │     BPF bytecode → 原生 x86-64 / ARM64 機器碼   │
                    │     (bpf_jit_enable=0 時退回解譯器 core.c)      │
                    └───────────────────────┬────────────────────────┘
                                            ▼
                    ┌────────────────────────────────────────────────┐
                    │  ③ ATTACH 到 hook                              │
                    │     kprobe/tracepoint(Ch51)  XDP/tc(Ch46)      │
                    │     LSM(Ch48)  seccomp(Ch49)  cgroup(Ch50)     │
                    └───────────────────────┬────────────────────────┘
                                            ▼
                    ┌────────────────────────────────────────────────┐
                    │  ④ 事件發生 → hook 觸發 → 執行那段 JIT 碼      │
                    │     讀 context、查 map、呼 helper、回傳         │
                    └────────────────────────────────────────────────┘
```

四個階段裡，**驗證只做一次（載入時），執行可以做幾百萬次（事件時）**。所有的安全都壓在階段 ①。這也是為什麼 verifier 是 kernel 裡最複雜、最難寫、也最容易出漏洞的一塊程式碼之一——它是整個信任模型的唯一守門員。

## eBPF 虛擬機：一個為「好驗證」而設計的 ISA

BPF 不是隨便挑的指令集。它是一個**類 RISC 的暫存器機**，`include/uapi/linux/bpf.h` 定義它的指令編碼，`include/uapi/linux/bpf_common.h` 定義 opcode class。關鍵設計：

- **11 個 64-bit 暫存器** `r0`–`r10`。`r0` 放回傳值與 helper 回傳值；`r1`–`r5` 傳 helper 參數；`r6`–`r9` 是 callee-saved；`r10` 是**唯讀的 frame pointer**，指向一塊 512 bytes 的 BPF stack。r10 唯讀是刻意的——程式不能亂改 frame pointer 去越過 stack 邊界。
- **固定寬度指令**（多數 8 bytes，少數 16 bytes 的 `BPF_LD_IMM64`）。定長好 decode、好驗證。
- **沒有間接跳躍到任意位址**——控制流只有條件/無條件跳到**程式內的固定偏移**，加上 helper 呼叫與 tail call。這是能做靜態控制流分析的前提：verifier 能把整段程式建成一張圖。

為什麼設計成這樣？**每一個設計決定都是為了讓 verifier 的工作變可能。** 定長指令、有限暫存器、無任意間接跳躍、有界 stack——這些限制單獨看都是「功能變弱」，合起來卻換到一個關鍵性質：**這段程式的所有可能行為可以在載入時被完全枚舉分析**。一個圖靈完備、能任意間接跳躍、動態配置記憶體的 VM，你沒辦法靜態證明它安全（停機問題）。BPF 故意不圖靈完備（早期版本連迴圈都禁），就是拿表達力換可驗證性。

載入走 `bpf()` syscall（Ch 4）的 `BPF_PROG_LOAD` 命令，入口在 `kernel/bpf/syscall.c` 的 `bpf_prog_load()`：userspace 把 bytecode、指令數、program type、期望的 attach type、log buffer 位址等打包進 `union bpf_attr` 傳進來。kernel 複製 bytecode 到內部、配置 `struct bpf_prog`，然後交給 verifier。

## verifier：載入時證明它安全（這章的重點）

verifier 在 `kernel/bpf/verifier.c`，主入口 `bpf_check()`。它的工作不是「跑一遍看有沒有壞」——那證明不了未走到的路徑安全。它做的是**抽象解譯（abstract interpretation）**：模擬執行程式的每一條可能路徑，在每個程式點追蹤每個暫存器與每個 stack slot 的抽象狀態，證明**在所有路徑上**都不會發生不安全操作。

分兩大階段：

### 階段一：建控制流圖、檢查會終止

`check_cfg()` 先把 bytecode 走一遍做 DFS，建出控制流圖並檢查：

- **沒有不可達指令**、跳躍目標都落在程式內、`BPF_EXIT` 存在。
- **沒有 back-edge 形成無界迴圈**——早期 BPF 完全禁迴圈，程式必須是 DAG，這樣天然保證終止。5.3 起支援**有界迴圈（bounded loop）**：verifier 允許 back-edge，但要靠階段二的狀態模擬證明迴圈變數單調收斂、迭代次數有上界（否則模擬會撞到指令上限而拒絕）。無論哪種，最終保證都是**程式一定會停**——kernel 不能容忍一段 BPF 在中斷 context 裡無限迴圈把 CPU 卡死。

### 階段二：逐指令狀態模擬

`do_check()` 是核心。它從程式進入點開始，一條一條指令地**符號執行**，維護一個 `struct bpf_verifier_state`：裡面有每個暫存器的 `struct bpf_reg_state`、stack 每個 slot 的狀態、以及一個模擬用的 call stack。每個暫存器的抽象狀態包含：

- **型別**：`SCALAR_VALUE`（純數值，不能當指標解參考）、`PTR_TO_CTX`（指向 context）、`PTR_TO_MAP_VALUE`、`PTR_TO_STACK`、`PTR_TO_PACKET`… 型別決定這個值能做什麼。把一個 scalar 當指標解參考 → 直接拒絕。
- **範圍**：用 **tnum（tracked number）** 追蹤「哪些 bit 已知是 0/1、哪些未知」，外加 `umin/umax/smin/smax` 有無號上下界。這讓 verifier 能推理「這個 offset 一定在 0..4095 之間」這種性質。tnum 定義在 `kernel/bpf/tnum.c`。
- **是否已初始化**：讀一個沒寫過的暫存器或 stack slot → 拒絕（防洩漏 kernel 記憶體）。

用這些狀態，verifier 在每條指令上執行對應的檢查：

```
   模擬到 "r2 = *(u32 *)(r1 + 16)"  這種記憶體讀取時：
   ┌─────────────────────────────────────────────────────────┐
   │ 1. r1 的型別是什麼？                                      │
   │      PTR_TO_CTX → 查這個 program type 的 context 佈局，   │
   │                   offset 16 是不是合法欄位？可讀嗎？      │
   │      PTR_TO_MAP_VALUE → offset+size 有沒有超出 value 大小 │
   │      PTR_TO_PACKET → 有沒有先做過 data_end 邊界檢查？     │
   │      SCALAR_VALUE → 拒絕：不能解參考一個純數值            │
   │ 2. offset 落在合法範圍內嗎？（用 tnum + umax 判斷）       │
   │ 3. 讀出來的 r2 現在型別/範圍是什麼？→ 更新 r2 狀態        │
   └─────────────────────────────────────────────────────────┘
```

每次記憶體存取都靠**當下追蹤到的暫存器範圍**證明落在合法物件內。這就是 memory safety 的來源：不是執行時加 bounds check（那會慢），而是載入時證明**永遠不會越界**，所以執行時可以完全不檢查。

**分支怎麼處理**：遇到條件跳躍 `if r3 > 100 goto +5`，verifier 會**fork 狀態**——沿「成立」路徑走時，它知道 `r3 > 100`（收窄 r3 的下界）；沿「不成立」路徑走時知道 `r3 <= 100`。這正是 packet 邊界檢查生效的機制：你寫 `if (data + 14 > data_end) return;`，verifier 在通過那個 `if` 之後的路徑上，才把 `data` 的可讀範圍放寬到 14 bytes。少寫這個 check，verifier 就不肯讓你讀，程式被拒。

### 路徑爆炸與 state pruning

模擬所有路徑，最壞情況是**指數爆炸**——每個分支翻倍。verifier 靠兩個機制活下來：

- **硬上限**：總共最多模擬 100 萬條指令（`BPF_COMPLEXITY_LIMIT_INSNS`）。超過就拒絕，回 log 說「program too complex」。這是為什麼很深的迴圈或很多分支的 BPF 會被拒——不是它不安全，是 verifier 證不完。
- **state pruning（狀態剪枝）**：`is_state_visited()` / `states_equal()`。verifier 在關鍵指令點記錄走過的狀態；之後若某條路徑到達同一點、而當前狀態是「先前某個已驗證狀態的子集」（等價或更受限），就**不必再往下模擬這條路徑**——已經證過了。剪枝是 verifier 能對付真實程式的關鍵，也是它最微妙、最容易出 bug 的地方：剪枝條件寫鬆一點，就可能把「其實不等價」的狀態當等價，放過不安全路徑。

### helper 呼叫與指標算術限制

- **helper 呼叫**：BPF 不能呼叫任意 kernel 函式，只能呼叫**白名單 helper**（`bpf_map_lookup_elem`、`bpf_probe_read_kernel`、`bpf_get_current_pid_tgid`…）。每個 program type 有自己允許的 helper 集合（`kernel/bpf/verifier.c` 的 `check_helper_call()` 配合各 program type 的 `bpf_verifier_ops`）。verifier 檢查參數型別對不對（`ARG_PTR_TO_MAP_KEY` 要求傳進去的指標範圍剛好蓋住 map key 大小），回傳值型別是什麼（`bpf_map_lookup_elem` 可能回 NULL，verifier 逼你在解參考前先檢查 NULL）。
- **指標算術限制**：對指標做加減有嚴格規則——不能讓一個指標算到指向別的物件、不能對某些指標型別（如 `PTR_TO_CTX`）做任意偏移。這裡也是 **Spectre 緩解**的所在（接 Ch 23 的推測執行/cache 側信道）：`kernel/bpf/verifier.c` 的 `sanitize_ptr_alu()` / `sanitize_speculative_path()` 會偵測「攻擊者可控的指標算術可能被推測執行拿去做界外讀取洩漏 cache 狀態」，插入遮罩指令或直接拒絕。BPF 是少數在**驗證層**主動對付 Spectre 的地方，因為它跑不受信任的程式、又常在特權 context，是側信道的高價值目標。

### 為什麼 verifier 難寫、又是攻擊面

verifier 要做的事本質上是**在 kernel 裡實作一個 sound 的靜態分析器**，還要對每種 program type、每個 helper、每種指標型別都正確。它有上萬行、幾百個邊界情況。任何一個「型別追蹤算錯範圍、剪枝誤判等價、tnum 運算不精確」的 bug，都意味著**可以構造一段其實會越界、卻被 verifier 放行的 BPF**——載入成功、JIT 成原生碼、以 kernel 特權執行任意讀寫。這是一條完整的 **LPE（本地提權）** 路徑，unprivileged BPF（若開啟）甚至不需要 root 就能觸發。

這正是 `kernel_pwn` 課會攻的東西：CVE-2020-8835（tnum 邊界算錯）、CVE-2021-3490（ALU32 邊界追蹤 bug）這類 verifier 漏洞，把「證明安全的那個證明器」本身當成攻擊面。也因此，現代發行版預設 `kernel.unprivileged_bpf_disabled=1`——把 verifier 這個大攻擊面藏在 root 後面。從防禦角度看：verifier 越強，你越敢開放 BPF 給非特權使用者；verifier 有洞，整個信任模型崩塌。

## JIT：過驗證後，編成原生碼全速跑

verifier 只證明安全，不管快。程式過關後，`bpf_prog_select_runtime()`（`kernel/bpf/core.c`）決定怎麼執行它：

- **JIT 開啟**（現代 x86-64/ARM64 預設）：呼叫 `bpf_int_jit_compile()`（x86 在 `arch/x86/net/bpf_jit_comp.c`，ARM64 在 `arch/arm64/net/bpf_jit_comp.c`），把 BPF bytecode 逐指令翻成原生機器碼，配置一塊可執行記憶體，之後 hook 觸發時直接 `call` 進這塊原生碼，接近原生速度。
- **JIT 關閉**：退回**解譯器** `___bpf_prog_run()`（`kernel/bpf/core.c`），一個巨大的 computed-goto dispatch loop，一條 BPF 指令一條地軟體解譯。慢，但架構無關——沒 JIT 後端的架構、或你刻意關 JIT 除錯時走這條。

為什麼要 JIT？BPF 常掛在**熱路徑**——XDP 在每個進來的封包上跑、kprobe 在每次 syscall 上跑。解譯執行每條 BPF 指令要跑好幾條原生指令去 dispatch，overhead 在百萬 PPS 的網路路徑上不可接受。JIT 把 `r1 += r2` 直接變成一條 `add` 指令，dispatch overhead 歸零。這是「載入時多花一次編譯，換執行時每次都快」的取捨，和 verifier「載入時多花一次驗證，換執行時零檢查」是同一個哲學。

安全性上有個微妙點：JIT 出來的是**可執行記憶體**，本身是攻擊者眼中的肥肉（把可控資料放進去當 gadget）。緩解措施 **constant blinding**（`bpf_jit_harden`）會把 BPF 程式裡的立即數用隨機值 XOR 打散，避免攻擊者把選定的常數放進可執行頁當 gadget 用。x86 上 JIT 出來的碼也走 kernel 的 W^X（可寫與可執行互斥）。

## 掛載點（hooks）：同一個引擎，插在 kernel 各處

verifier + JIT 是共用引擎，但一段 BPF 「什麼時候被觸發、拿得到什麼 context、能呼哪些 helper」由它的 **program type** 決定。每種 program type 對應一種 hook，本課前面各章講的都是這些 hook：

| program type | hook 在哪 | context (r1 指向) | 本課章節 |
|---|---|---|---|
| `BPF_PROG_TYPE_KPROBE` | kprobe / kretprobe | `struct pt_regs`（被探點的暫存器） | Ch 51 |
| `BPF_PROG_TYPE_TRACEPOINT` / `RAW_TRACEPOINT` | tracepoint | tracepoint 參數 | Ch 51 |
| `BPF_PROG_TYPE_XDP` | 網卡驅動收包最早期 | `struct xdp_md`（原始封包） | Ch 46 |
| `BPF_PROG_TYPE_SCHED_CLS` | tc ingress/egress | `struct __sk_buff` | Ch 46 |
| `BPF_PROG_TYPE_LSM` | LSM hook 點 | LSM hook 參數 | Ch 48 |
| `BPF_PROG_TYPE_CGROUP_*` | cgroup 邊界（socket/skb…） | 視 hook 而定 | Ch 50 |
| `BPF_PROG_TYPE_PERF_EVENT` | perf/PMU 取樣 | `struct bpf_perf_event_data` | Ch 51、perf |
| seccomp（cBPF，非 eBPF） | syscall 入口 | `struct seccomp_data` | Ch 49 |

（seccomp 用的是**經典 BPF**、不是 eBPF——一個歷史遺留，Ch 49 有交代；放這裡對照，讓你看到「BPF」這個字在 kernel 裡涵蓋兩代 VM。）

program type 決定 context 佈局，verifier 就是靠這個佈局判斷「offset 16 是不是 context 的合法欄位」。同一段讀 `r1+16` 的 bytecode，掛成 XDP 和掛成 kprobe，verifier 的判斷完全不同——因為 context 結構不同。這也是為什麼 BPF 程式和它的 program type 綁死，不能隨便換 hook。

## maps：BPF 的狀態與對外通道

BPF 程式本身無狀態、每次觸發從乾淨的 stack 開始。要跨觸發保存狀態、或和 userspace 交換資料，靠 **map**——kernel 管理的鍵值容器。`kernel/bpf/syscall.c` 的 `BPF_MAP_CREATE` 建立，`kernel/bpf/hashtab.c`、`kernel/bpf/arraymap.c` 等實作各種 map type：

- `BPF_MAP_TYPE_HASH` / `ARRAY`：一般鍵值/索引儲存。
- `BPF_MAP_TYPE_PERCPU_*`：每 CPU 一份，避開鎖（接 Ch 7 per-CPU）。
- `BPF_MAP_TYPE_RINGBUF`：BPF → userspace 的高效事件串流（bpf 課 Ch 25 談 ringbuf vs perfbuf）。
- `BPF_MAP_TYPE_PROG_ARRAY`：存別的 BPF 程式，供 **tail call** 跳過去。

map 是 kernel 物件，有自己的生命週期與 refcount。BPF 程式透過 helper（`bpf_map_lookup_elem` 等）存取，userspace 透過 `bpf()` 的 `BPF_MAP_LOOKUP_ELEM`/`UPDATE_ELEM` 存取——map 就是這兩側的會合點。verifier 在驗證時就把「你要查的是哪個 map、它的 key/value 多大」綁定進 helper 呼叫檢查，所以查 map 的邊界也是載入時證好的。

## CO-RE / BTF：讓一段 BPF 跨 kernel 版本可攜

kernel 結構的欄位偏移每版都可能變（Ch 0 開了 `DEBUG_INFO_BTF`）。早期 BPF 要讀 `task_struct->pid`，得在**目標 kernel 上**現編才知道 pid 的 offset——這對散佈預編 BPF 的工具是災難。**BTF（BPF Type Format）** 把 kernel 的型別資訊（結構佈局、欄位偏移）壓進 `/sys/kernel/btf/vmlinux`。**CO-RE（Compile Once, Run Everywhere）** 讓 BPF 程式在編譯時只記「我要 `task_struct` 的 `pid` 欄位」這個**relocation**，載入時由 libbpf 讀目標 kernel 的 BTF 算出實際 offset 填進去。一次編譯，到處能跑。細節在 bpf 課 Ch 9/10，這裡點出 kernel 側的支撐：BTF 由 `pahole` 在 build 時產生、由 kernel 匯出，是 verifier 做型別檢查（如 `bpf_probe_read_kernel` 的型別感知變體）和 CO-RE relocation 的共同資料源。

## 動手：看 kernel 裡的 BPF

不用寫 BPF——用系統上已經在跑的（systemd、docker 都會載 BPF）來觀察 kernel 側。工具是 `bpftool`（`apt install linux-tools-$(uname -r)` 或 `bpftool` 套件）。

**1. 列出目前載入的 BPF 程式**

```bash
sudo bpftool prog list
# 27: cgroup_skb  name egress  tag 6deef7357e7b4530  gpl
#     loaded_at 2026-07-31T...  uid 0
#     xlated 96B  jited 84B  memlock 4096B  map_ids 12
```

`xlated`（verifier 改寫後的 bytecode 大小）vs `jited`（JIT 出來的原生碼大小）並列——你看到的每個程式都**已經過 verifier、已經 JIT**。

**2. 看 JIT 出來的原生組語**

```bash
sudo bpftool prog dump jited id 27
#  0: push   %rbp
#  1: mov    %rsp,%rbp
#  4: ...
# 這是 x86-64 原生指令——BPF bytecode 被翻成的機器碼
```

對照 `bpftool prog dump xlated id 27`（BPF 層的指令），你能直接看到「一條 BPF 指令 → 幾條 x86 指令」的 JIT 對映。這是 Ch 0 那套「讀源碼還不夠，要看它真的變成什麼」精神在 BPF 上的體現。

**3. 列出 map**

```bash
sudo bpftool map list
# 12: hash  name my_map  flags 0x0
#     key 4B  value 8B  max_entries 1024  memlock 16384B
sudo bpftool map dump id 12          # 看裡面的鍵值
```

**4. 故意讓 verifier 拒絕一個程式**

寫一個讀未初始化暫存器的最小 BPF，用 raw `bpf()` 或 libbpf 載入，看 verifier log。最直接的方式是找一個 bpf 課的範例，故意刪掉 packet 邊界檢查（`if (data + n > data_end)`），編了載入：

```
; R1 type=ctx expected=fp
0: (61) r2 = *(u32 *)(r1 +0)
invalid access to packet, off=0 size=4, R1(id=0,off=0,r=0)
R1 !read_ok
```

這行 `invalid access to packet` 就是 verifier 在告訴你：你沒證明這個讀在界內，我不放行。**verifier log 是你和守門員的對話**——寫 BPF 卡關九成是在讀這個 log。

**5. JIT 開關**

```bash
cat /proc/sys/net/core/bpf_jit_enable       # 1 = 開 JIT（預設）
# 0 = 關 JIT，走解譯器；2 = 開 JIT 並 dump JIT 碼到 kernel log（除錯用）
sudo sysctl net.core.bpf_jit_harden=1        # 開 constant blinding
```

把 `bpf_jit_enable` 設 0，再 `bpftool prog list`，你會看到 `jited` 欄位消失——程式改走 `___bpf_prog_run()` 解譯執行。這是驗證「JIT 是可選的、解譯器是 fallback」最直接的方式。

## 對比與取捨

| 在 kernel 裡加邏輯的方式 | 信任模型 | 安全性 | 需要權限 | 熱更新 |
|---|---|---|---|---|
| 改源碼重編 kernel | 完全信任 | 改壞就 panic | root + 重開機 | 否 |
| 核心模組 `.ko`（Ch 8） | 完全信任 | 一個 bug 全機掛 | root（`CAP_SYS_MODULE`） | 卸載重載 |
| eBPF | **不信任 + 載入時證明** | verifier 過關才跑；有界、記憶體安全 | root，或開放 unprivileged | 是（隨載隨掛） |

| BPF 執行方式 | 速度 | 可攜性 | 何時用 |
|---|---|---|---|
| JIT（`bpf_int_jit_compile`） | 接近原生 | 需該架構有 JIT 後端 | 生產預設 |
| 解譯器（`___bpf_prog_run`） | 慢數倍 | 任何架構 | 無 JIT 後端 / 除錯 |

一句話取捨：eBPF 用**表達力的限制**（不圖靈完備、受限指令集、白名單 helper）換**可驗證的安全**，再用 JIT 把限制帶來的效能損失補回來。模組是「全能但危險」，eBPF 是「受限但安全」。

## 踩雷集錦

1. **以為 verifier 是「跑跑看有沒有崩」**。錯。它是**靜態抽象解譯，模擬所有路徑**，不執行你的程式。所以它能拒絕一段「大部分輸入都沒事、但某個邊角會越界」的程式——正因為它不靠實際執行，才擋得住只在特定輸入下才觸發的 bug。

2. **「program too complex」以為是程式有錯**。不是。是 verifier 模擬撞到 100 萬指令上限、或狀態太多剪枝救不了。程式可能完全正確，只是太複雜證不完。解法是簡化控制流、減少迴圈、拆成多個程式用 tail call 串。

3. **把 verifier 的嚴格當成「BPF 語言的限制」**。verifier 不是不讓你寫迴圈或指標——是不讓你寫**它證不了安全**的迴圈或指標。同樣一段邏輯，多加一個邊界檢查、讓 tnum 能收窄範圍，verifier 就放行。你是在**幫 verifier 證明**，不是在跟它鬥。

4. **以為 JIT 關掉程式就不能跑**。JIT 只是加速。關掉 `bpf_jit_enable`，程式改走解譯器 `___bpf_prog_run()`，一樣能跑、一樣安全，只是慢。JIT 和安全無關，安全全在 verifier。

5. **忽略 program type 決定一切**。同一段 bytecode 換 program type 就可能從「載入成功」變「verifier 拒絕」——因為 context 佈局、可用 helper、允許的操作全變了。「這段 BPF 為什麼在別的 hook 過不了」的答案幾乎總是 program type。

6. **把 seccomp-BPF 當 eBPF**。seccomp 用的是**經典 BPF（cBPF）**，跑在不同的、簡單得多的過濾框架上（Ch 49），不經過這章講的 eBPF verifier。同名不同物。

## 進階：再往深一層

- **verifier 的 `xlated` 是被改寫過的**：verifier 不只驗證，還會**重寫** bytecode——把 map lookup 換成更快的形式、插入 Spectre 遮罩、做 inline 優化。你 `bpftool prog dump xlated` 看到的不是你編的原始 bytecode，是 verifier 加工後的。
- **BPF-to-BPF call 與 tail call**：早期 BPF 全 inline，現在支援子函式呼叫（`kernel/bpf/verifier.c` 的 `check_func_call()`）與 tail call（跳到另一個 BPF 程式不返回）。verifier 要對這些跨函式流也做狀態追蹤，複雜度陡升——這是 verifier 近年 bug 的重災區之一。
- **`bpf_trampoline` 與 fentry/fexit**：比 kprobe 更低 overhead 的 attach 機制（bpf 課 Ch 21），kernel 用 BPF trampoline 直接改寫函式入口跳進 BPF，省掉 kprobe 的 int3 陷阱開銷。
- **面試常問**：「eBPF 憑什麼安全？」——標準答案要點出**受限 ISA + 載入時 verifier（不是執行時檢查）+ 有界終止 + 記憶體安全靠靜態範圍證明**，並能說出 verifier 本身是攻擊面（LPE）、現代發行版把 unprivileged BPF 關掉的原因。
- **kernel 版本演進**：迴圈支援（5.3 有界迴圈）、`bpf_loop` helper（5.17，把迴圈交給 helper 繞開 verifier 展開）、verifier 對 Spectre 的緩解一路在加強。這是仍在高速演化的子系統，6.12 的行為未必等於更新版。

## 動手練習

1. **看穿一個系統上的 BPF**：`sudo bpftool prog list` 挑一個程式，`dump xlated` 和 `dump jited` 都看一次，對照「BPF 指令 → x86 指令」。用 `bpftool prog show id N --json` 看它的 program type 和掛在哪。
2. **弄壞給 verifier 看**：拿 bpf 課練習裡任一個 XDP 或 kprobe 程式，刪掉一個邊界檢查或用一個未初始化的變數，載入，把 verifier log 完整讀一遍，指出它是在哪條指令、因為什麼型別/範圍拒絕你。
3. **關 JIT 對比**：同一個程式，`bpf_jit_enable=1` 和 `=0` 各載入一次，`bpftool prog list` 對比 `jited` 欄位有無。若有 microbenchmark，量兩者觸發成本差多少，體會 JIT 的價值。
4. **讀源碼定位**：在 Bootlin 開 `kernel/bpf/verifier.c`，找到 `do_check()` 和 `check_mem_access()`，讀 `check_mem_access()` 怎麼根據暫存器型別分派到不同的邊界檢查——這是 memory safety 的實作核心。

## 本章重點整理

- kernel host 不受信任 BPF 的信任模型是**「載入時證明，執行時零檢查」**：安全全壓在 verifier，速度靠 JIT 補回。
- **verifier**（`kernel/bpf/verifier.c` 的 `bpf_check`）做靜態抽象解譯，模擬所有路徑，用型別 + tnum 範圍證明控制流會終止、記憶體存取不越界、不讀未初始化；靠 state pruning 對付路徑爆炸。它是最大攻擊面——verifier bug = LPE，接 kernel_pwn。
- **JIT**（`arch/x86/net/bpf_jit_comp.c`）把過關的 bytecode 編成原生碼；`bpf_jit_enable=0` 時退回 `core.c` 的解譯器 `___bpf_prog_run()`。
- **program type** 決定 hook、context 佈局與可用 helper——kprobe/tracepoint(Ch51)、XDP/tc(Ch46)、LSM(Ch48)、cgroup(Ch50) 共用同一個 verifier+JIT 引擎；**map** 是跨觸發與對 userspace 的狀態通道；**BTF/CO-RE** 讓 BPF 跨版本可攜。

## 自我檢核

- [ ] 不看筆記，能解釋「為什麼 kernel 敢執行 userspace 送進來的程式碼」——並說出這和載入核心模組在信任模型上的根本差別
- [ ] 能說明 verifier 為什麼是**靜態模擬所有路徑**而非執行，以及它怎麼用型別 + 範圍證明記憶體安全
- [ ] 能解釋 tnum、state pruning、100 萬指令上限各解決什麼問題
- [ ] 面試被問「eBPF 憑什麼安全、它的攻擊面在哪」，你能完整回答（含 verifier bug → LPE）
- [ ] 能說出 JIT 與解譯器的關係、JIT 為什麼存在、關掉 JIT 會怎樣
- [ ] 能解釋 program type 為什麼決定一段 BPF 能掛哪、能讀什麼 context
- [ ] 能用 bpftool 列出程式/map、dump 出 jited 組語、讀懂一則 verifier 拒絕訊息

## 延伸閱讀

### 官方文件

- **[Documentation/bpf/verifier.rst](https://www.kernel.org/doc/html/latest/bpf/verifier.html)**
  - **讀哪裡**：整篇，尤其「Register value tracking」「State pruning」兩節
  - **和本章的關聯**：這是 kernel 官方對 verifier 追蹤機制的權威說明，本章「階段二狀態模擬」的正式版；讀不懂 verifier log 時回來查

- **[Documentation/bpf/instruction-set.rst](https://www.kernel.org/doc/html/latest/bpf/instruction-set.html)**
  - **讀哪裡**：暫存器、opcode 編碼兩節
  - **能學到什麼**：eBPF ISA 的正式規格，對照本章「為好驗證而設計的 ISA」

### 原始論文 / 權威文章

- **[BPF Design Q&A（kernel Documentation）](https://www.kernel.org/doc/html/latest/bpf/bpf_design_QA.html)**
  - **讀哪裡**：全篇 Q&A
  - **為什麼值得讀**：由 BPF maintainer 直接回答「為什麼這樣設計」，包含為什麼不圖靈完備、為什麼要 verifier、helper 白名單的理由——正是本章「為什麼」的一手來源

- **[Cilium: BPF and XDP Reference Guide](https://docs.cilium.io/en/stable/bpf/)**
  - **讀哪裡**：「BPF Architecture」「Verifier」「JIT」章節
  - **為什麼值得讀**：目前最完整的第三方 BPF 內部機制文件，圖多、把 verifier/JIT/map/program type 串成一張完整地圖
  - **前提**：讀完本章再看，能對上號

### 跨課

- **本 repo `bpf` 課全門**：Ch 4（ISA 與 JIT 使用者視角）、Ch 5（verifier）、Ch 8（maps）、Ch 9/10（BTF/CO-RE）——那門教你**寫** BPF、用工具鏈，和這章（kernel 怎麼**跑**它）互補
- **本 repo `kernel_pwn` 課**：verifier 漏洞的攻擊面——CVE-2020-8835、CVE-2021-3490 這類「繞過安全證明器本身」的 LPE，把本章的守門員當標靶

verifier 過了、JIT 編了、掛上 hook 跑起來——但如果 BPF 程式（或任何 kernel 程式碼）行為不如預期，你怎麼看穿 kernel 內部發生了什麼？下一章把整套 kernel 除錯與觀測工具攤開：printk、ftrace、KASAN、kgdb——從「印一行」到「單步一顆活的 kernel」。

→ [Ch 53 Kernel 除錯：printk/ftrace/KASAN/kgdb](./53-kernel-debugging.md)
