# Ch 17 — Spectre-RSB / ret2spec：Return Stack Buffer 污染攻擊

> **目標**：理解 Return Stack Buffer (RSB) 的微架構角色，掌握攻擊者如何透過污染 RSB 讓 `ret` 指令在推測執行期間跳往任意位址，並分析 RSB 填充 (RSB stuffing) 這項防禦機制的設計與邊界條件。

---

## RSB 的微架構角色

現代 CPU 對 `ret` 指令的處理和對 `jmp`/`call` 指令完全不同。`ret` 在架構語義上等同於 `pop rip`，但在分支預測器眼中，它有獨立的預測資料結構：**Return Stack Buffer (RSB)**，也稱為 Return Address Stack (RAS)。

RSB 是一個每核心 (per-core) 的環形緩衝區，Intel Skylake 世代通常為 **16 個 entry**。每次 CPU 執行 `call` 指令，硬體就把下一條指令的 PC（即返回位址）推入 RSB；每次執行 `ret`，硬體就從 RSB 頂端彈出預測用的返回位址，讓前端 (frontend) 推測性地擷取那段程式碼，不等記憶體讀出真正的返回位址。

```
正常 call/ret 配對下 RSB 的狀態變化：

call A  →  RSB: [retA           ]   top=1
  call B  →  RSB: [retA, retB      ]   top=2
    call C  →  RSB: [retA, retB, retC]   top=3
    ret C   →  RSB: [retA, retB     ]   top=2  (預測 retC ✓)
  ret B     →  RSB: [retA           ]   top=1  (預測 retB ✓)
ret A       →  RSB: [               ]   top=0  (預測 retA ✓)
```

RSB 與 Branch Target Buffer (BTB) 的定位完全不同：

| 特性 | RSB | BTB |
|------|-----|-----|
| 預測目標 | `ret` 指令的返回位址 | 間接 `jmp`/`call` 的目標 |
| 典型大小 | 16 entry（per core） | 數千 entry（多核共享/分割） |
| 維護方式 | 硬體自動 push/pop，類似 stack | 程式執行歷史寫入 |
| 攻擊向量 | RSB 污染、RSB overflow underflow | BTB 投毒（Spectre-v2） |
| 主要防禦 | RSB stuffing、eIBRS | IBRS、retpoline、eIBRS |

RSB 在架構設計上比 BTB 更「精確」——它不靠歷史統計，而是追蹤硬體 call stack。然而正是這個設計讓它成為攻擊面：RSB 的內容在多種情境下會與軟體棧 (software stack) 出現**結構性偏差**。

---

## RSB 為何會偏離真實 Stack

### 情境一：RSB overflow → underflow

RSB 是固定深度的環形緩衝區。當 call 深度超過 16 時，最舊的 entry 被覆寫：

```
call 1..16  →  RSB 全滿，ring pointer wraps
call 17     →  entry[0] (原 ret1) 被 entry[17] 覆寫 ← overflow
...
call 32     →  所有原始 entry 均已遭覆寫

接著開始 ret：
ret 32..17  →  RSB 仍有 entry，預測可能正確（最近 16 次的返回位址）
ret 16      →  RSB 空了！← underflow
ret 15..1   →  RSB 持續 underflow

underflow 的後果：CPU 回退到 BTB 做預測 ← 可被 Spectre-v2 手法利用
```

### 情境二：Context Switch 殘留

作業系統切換行程時，RSB 內的 entry 屬於舊行程的呼叫框架。核心切換至新行程後，新行程的 `ret` 仍可能讀到舊行程留下的返回位址，發生**跨行程 RSB 污染**。

### 情境三：VM Exit 殘留

Guest VM 執行 `ret` → VM exit 觸發 → Hypervisor 接管控制流 → Hypervisor 的 `ret` 卻讀到 Guest 的 RSB entry。這讓 Guest 可以在 Hypervisor 的推測執行中導引控制流。

### 情境四：軟體棧與 RSB 不配對

`setjmp`/`longjmp`、C++ 例外處理、訊號處理器 (signal handler) 返回都可能跳過若干 `ret`，造成 RSB 與真實回傳位址不同步。

---

## 兩篇 2018 年論文

2018 年 7 月，兩個研究團隊幾乎同時獨立發表了 RSB 攻擊：

**Maisuradze & Rossow**（CISPA）：
"ret2spec: Speculative Execution Using Return Stack Buffers"
ACM CCS 2018 / arXiv:1807.10364

**Koruyeh et al.**（UCR + 其他）：
"Spectre Returns! Speculation Attacks using the Return Stack Buffer"
USENIX WOOT 2018 / arXiv:1807.07940

兩篇論文分別命名為 **ret2spec** 與 **SpectreRSB**，攻擊核心相同：讓受害者的 `ret` 在推測執行期間跳到攻擊者控制的 gadget，透過 cache 側通道洩漏秘密。

---

## 攻擊機制詳解

### 基本 RSB 污染攻擊

攻擊者（與受害者共享 CPU 核心時）透過大量巢狀 `call` 把整個 RSB 填滿攻擊者控制的返回位址：

```c
/* 攻擊者端：填滿 RSB（偽碼） */
void fill_rsb_with_gadget(void *gadget_addr) {
    /*
     * 16 層巢狀 call，每層的「返回位址」都指向 gadget_addr。
     * 實際上透過組合語言精確控制 RSB entry 值，
     * 而非真的讓函式返回到 gadget_addr。
     */
    __asm__ volatile(
        "lea 1f(%%rip), %%rax\n\t"   /* rax = 下一條指令位址   */
        "mov %0, %%rbx\n\t"           /* rbx = gadget 位址      */
        /* 壓入 16 個假的 retaddr = gadget_addr */
        ".rept 16\n\t"
        "call 2f\n\t"
        "nop\n\t"                     /* 這裡永遠不會架構性執行 */
        "2:\n\t"
        "mov %%rbx, (%%rsp)\n\t"      /* 覆寫 RSB push 的值     */
        ".endr\n\t"
        /* 清理：彈出 16 個 frame，但 RSB 仍保有 gadget_addr */
        ".rept 16\n\t"
        "add $8, %%rsp\n\t"
        ".endr\n\t"
        "1:\n\t"
        : : "r"(gadget_addr) : "rax", "rbx", "memory"
    );
}
```

受害者（被排程在同一核心）執行 `ret` 時：
1. 硬體從 RSB 彈出攻擊者填入的 gadget 位址
2. 推測性地擷取並執行 gadget
3. Gadget 對受害者的秘密資料做記憶體存取（e.g., `mov al, [secret]; mov rbx, [rbx + rax * 4096]`）
4. 攻擊者用 Flush+Reload (F+R) 讀出 cache 側通道，重建 `secret` 的值

```
攻擊時序圖：

[攻擊者執行緒]         [受害者執行緒]         [RSB 狀態]
fill_rsb(gadget)  →                           [gadget x16]
yield / wait      →   執行正常程式碼           [gadget x16]
                  →   執行 ret                [gadget x15]
                  →     ↓ RSB 給出 gadget
                  →     推測執行 gadget !!!
                  →       gadget 存取 secret
                  →       污染 cache line
                  →   架構性：ret 跳回正確位址
[攻擊者]          ←   Flush+Reload 讀出 cache
leak = byte       ←
```

### ret2spec 變體：軟體棧與 RSB 不同步

ret2spec 論文描述了另一種情境：利用緩衝區溢位改寫軟體棧上的返回位址，RSB 仍保有原始的返回位址。此時：

- **架構執行**：`ret` 讀記憶體中被改寫的返回位址，跳到攻擊者指定的位置（ROP 鏈）
- **推測執行**：CPU 先信任 RSB，推測性地執行 RSB 指向的原始返回位址

這樣架構和推測兩條路線**同時**被攻擊者控制，但方式不同：

```
+------------------+          +-----------------+
|  軟體 stack      |          |  RSB             |
| [攻擊者覆寫的   |          | [原始 retaddr   |
|  返回位址 → B] |          |  → A           ]|
+------------------+          +-----------------+
      ↓                             ↓
  架構執行 → B               推測執行 → A
  (可控制 ROP)               (可洩漏 A 附近的秘密)
```

ret2spec 與 ROP 的本質區別：ROP 改變的是**架構可見狀態**；ret2spec 洩漏資料的管道是**推測執行的 cache 副作用**，架構上看什麼事都沒有發生。

---

## RSB overflow → BTB fallback 的特殊危險

下圖說明 RSB overflow 如何接上 Spectre-v2 攻擊面：

```
深度巢狀呼叫場景（call depth > 16）：

深度 1-16：RSB 正常追蹤返回位址
深度 17+：RSB overflow，環形緩衝區開始覆寫最舊 entry

回傳序列到 RSB empty 時：
  ret → RSB underflow → CPU fallback 到 BTB

BTB 可被 Spectre-v2 手法毒化
  → 攻擊者可控制 underflow 後 ret 的推測目標
  → 即使已部署 retpoline，也可能繞過！

因為 retpoline 利用 ret 迴避 BTB 間接跳轉的問題，
但當 RSB overflow 後 ret 本身改走 BTB，
retpoline 的隔離假設就失效了。
```

這是為什麼在 retpoline 部署後，RSB 防禦依然是獨立需求。

---

## 防禦：RSB 填充 (RSB Stuffing)

### 核心思路

在每次 context switch 與 VM entry 時，主動用 16 個「安全」返回位址覆寫整個 RSB，確保新 context 不會讀到舊 context 的殘留：

```c
/*
 * Linux arch/x86/entry/entry_64.S 中的 __fill_return_buffer
 * 概念性偽碼（實際為組合語言）
 */
.macro FILL_RETURN_BUFFER reg:req nr:req
    mov $\nr, \reg
.Lloop_\@:
    call .Lnext_\@
.Lnext_\@:
    lfence          /* 防止推測執行越過此點  */
    dec \reg
    jnz .Lloop_\@
    /* 此時 RSB 已被 nr 個「安全」位址填滿 */
.endm

/* 在 context switch path 呼叫（nr = RSB 深度，通常 = 16）： */
FILL_RETURN_BUFFER rax, 16
```

每個 `call` 把下一條 `lfence` 的位址推入 RSB；隨後的 `dec/jnz` 讓這 16 個 call 對應的 `ret` 都不被執行（直接透過修改計數器返回），保持 RSB 被安全位址填滿。

### eIBRS：硬體解法

Intel 在 Cascade Lake/Ice Lake 等世代引入 Enhanced IBRS (eIBRS)，其中包含**硬體層級的 RSB 隔離**：

- context switch 時 CPU 自動清空 RSB
- VM exit 時 RSB 被隔離
- 不再需要軟體 RSB stuffing（對 cross-context 場景而言）

查詢當前機器的緩解狀態：

```bash
$ cat /sys/devices/system/cpu/vulnerabilities/spectre_v2
Mitigation: Enhanced / Automatic IBRS; RSB filling

# 或確認 CPUID：
$ grep -m1 'model name' /proc/cpuinfo
# i7-10700 (Comet Lake) 具備 eIBRS
```

i7-10700 (Comet Lake, 10th gen) 支援 eIBRS，核心顯示 `Enhanced IBRS`，表示 RSB 跨 context 的污染在硬體層面被阻擋。跨 context RSB 攻擊的 PoC **不適用於此機器**，需在 2017 年前（無 eIBRS）的 Skylake/Kaby Lake 或未打補丁的 hypervisor 上重現。

### 各代 Intel 防禦矩陣

| CPU 世代 | eIBRS | 需要 RSB stuffing | RSB overflow 後 BTB fallback |
|---------|-------|------------------|-------------------------------|
| Haswell / Broadwell | 無 | 是（軟體必做） | 有風險 |
| Skylake / Kaby Lake | 無 | 是（軟體必做） | 有風險 |
| Cascade Lake | 有 | Context switch 可省略 | 硬體隔離 |
| Ice Lake / Comet Lake (i7-10700) | 有 | 大部分場景可省 | 硬體隔離 |

---

## 可實測：測量 RSB 深度

這個實驗不涉及攻擊，只是觀測 RSB 深度對 `ret` 預測準確率的影響，在任何機器上均可安全執行：

```c
/* rsb_depth_probe.c
 * 透過 rdtsc 觀察不同巢狀深度下 ret 的時延
 * 理論：超過 RSB 深度後，ret 走 BTB，時延分布改變
 *
 * 編譯：gcc -O0 -o rsb_depth_probe rsb_depth_probe.c
 */
#include <stdio.h>
#include <stdint.h>
#include <string.h>

#define MAX_DEPTH 32
#define TRIALS    100000

static uint64_t results[MAX_DEPTH + 1];

/* 遞迴函式：depth 層巢狀 call，回傳途中量測第一個 ret 的時延 */
static void nested_call(int depth, uint64_t *out) {
    if (depth == 0) {
        /* 最深處：量測 ret 時延 */
        uint64_t t0, t1;
        __asm__ volatile("rdtsc; shl $32, %%rdx; or %%rdx, %%rax"
                         : "=a"(t0) :: "rdx");
        /* ret 發生在此函式返回時 */
        *out = t0;
        return;
    }
    nested_call(depth - 1, out);
    uint64_t t1;
    __asm__ volatile("rdtsc; shl $32, %%rdx; or %%rdx, %%rax"
                     : "=a"(t1) :: "rdx");
    if (depth == 1)   /* 只記錄最淺層 ret */
        *out = t1 - *out;
}

int main(void) {
    printf("depth\tavg_cycles\n");
    for (int d = 1; d <= MAX_DEPTH; d++) {
        uint64_t total = 0;
        for (int t = 0; t < TRIALS; t++) {
            uint64_t out = 0;
            nested_call(d, &out);
            total += out;
        }
        printf("%d\t%lu\n", d, total / TRIALS);
    }
    return 0;
}
```

預期觀察：depth 1–16 的平均 cycle 數相對穩定，depth > 16 後出現抖動或升高（BTB fallback 預測準確率下降）。在 eIBRS 機器上，實際數字受其他因素影響，但趨勢通常仍可見。

---

## 對比與取捨

| 面向 | RSB 攻擊 | Spectre-v2 (BTB 投毒) |
|------|---------|----------------------|
| 目標指令 | `ret` | 間接 `jmp` / `call` |
| 預測器被污染 | RSB（環形棧，per-core） | BTB（歷史統計表） |
| 攻擊前提 | 同核心 or context switch 殘留 | 同核心 or BTB 跨 SMT 洩漏 |
| 污染方式 | 連續巢狀 call 覆寫全部 entry | 重複執行 indirect branch 訓練 |
| Retpoline 是否防禦 | **否**（retpoline 本身用 ret，反過來受 RSB 攻擊） | 是（避免 indirect branch 走 BTB） |
| 主要防禦 | RSB stuffing / eIBRS | IBRS / retpoline / eIBRS |
| 攻擊視窗 | `ret` 解析真正位址前 | indirect branch 解析前 |

RSB 攻擊的獨特威脅：它繞過了 retpoline。Retpoline 把 indirect branch 替換為 `call; lfence; jmp -; ret` 序列，核心假設是 `ret` 走 RSB 而非 BTB。若攻擊者污染了 RSB，retpoline 的隔離邊界就被突破。

---

## 踩雷集錦

**踩雷 1：RSB ≠ BTB，兩者預測不同指令**

RSB 預測 `ret`；BTB 預測間接 `jmp`/`call`。把 Spectre-v2 的「BTB 投毒」手法直接套用在 `ret` 上行不通，因為 `ret` 查的是 RSB。反之，RSB 污染對 `jmp` 指令無效。

最容易混淆的地方：retpoline 把間接跳轉改寫成 `ret`，意圖讓預測查 RSB（RSB 有準確的 stack 資訊），避免 BTB 被毒化。然而這正好讓 RSB 本身成為新的攻擊面。

**踩雷 2：RSB stuffing 的填充深度必須等於 CPU 的 RSB 深度**

如果 CPU RSB 有 16 個 entry，但 stuffing 只填 8 個，RSB 底部 8 個 entry 仍含舊 context 的資料。攻擊者只要觸發足夠多次 `ret`（超過 8 次），就能讀到未覆蓋的 stale entry。Linux 核心的 `__fill_return_buffer` 明確寫死 `RETPOLINE_STUFF_RSB_DEPTH`（目前為 16），但如果廠商調整 RSB 大小或自製核心時計算錯誤，這個假設就會失效。

**踩雷 3：ret2spec ≠ ROP**

ROP (Return-Oriented Programming) 改寫軟體棧上的返回位址，讓 CPU **架構性地**跳到 gadget 執行。這在 dmesg/strace/GDB 等工具都看得到。

ret2spec 的推測執行洩漏只在**推測視窗內**發生。架構可見的執行路徑永遠是「正確」的——受害者的 `ret` 最終架構上還是跳到合法位址，只是在那之前的推測 cycle 裡悄悄讀了秘密並污染了 cache。傳統安全工具看不到推測執行的副作用，這也是所有 Spectre 類攻擊的根本難處。

**踩雷 4：eIBRS 保護 context switch 殘留，但不保護 RSB overflow underflow**

即使 CPU 支援 eIBRS（如 i7-10700），RSB overflow 後的 BTB fallback 在某些核心版本下仍需軟體配合。eIBRS 主要解決的是「切換 context 時 RSB 的跨行程污染」，並非所有 RSB 攻擊向量。

---

## 進階：再往深一層

### 同步 SMT 上的 RSB 共享問題

RSB 是 **per-core** 而非 per-thread 的結構在某些 CPU 上是錯誤的理解——Intel 文件顯示 RSB 在 HT 兄弟執行緒之間通常是獨立的。但 BTB fallback 路徑用的 BTB 可能是共享的，因此 RSB overflow → BTB fallback 在 SMT 環境下仍可能讓 sibling thread 交叉影響。

### ARM 的等效結構

ARM 稱之為 Return Address Stack (RAS)，深度因核心而異（Cortex-A72 為 8 entry，Cortex-A76 為 16 entry）。ARM 的 CVE-2022-23960（Spectre-BHB）也部分涉及類似的間接預測污染機制，RSB/RAS 攻擊在 ARM 上具有可移植性。

### 推測執行與微碼更新的互動

Intel 2019 年之後的微碼更新對部分 CPU 加入了 RSB alternative（RSB 替代預測器）管理，eIBRS 的行為細節也在微碼層面被強化。這意味著在相同 CPUID 的 CPU 上，微碼版本不同的機器，RSB 行為可能有差異。安全研究中必須確認 `cpuid -1 | grep microcode` 版本。

### SpectreBHB 與 RSB 的關係

2022 年的 Spectre-BHB 攻擊主要針對 Branch History Buffer (BHB)，與 RSB 不同。然而攻擊者往往組合多種預測器污染手法——RSB 污染 + BHB 操縱 + BTB 投毒——在同一個攻擊鏈中達成更精確的 gadget 導引。理解 RSB 的邊界是分析這類組合攻擊的前提。

---

## 動手練習

### 練習一：確認你的 CPU 的 RSB 防禦配置

```bash
# 1. 查看 spectre_v2 緩解狀態（RSB filling 是否啟用）
cat /sys/devices/system/cpu/vulnerabilities/spectre_v2

# 2. 找出 Linux 核心的 RSB stuffing 呼叫點
grep -r "fill_return_buffer\|FILL_RETURN_BUFFER" \
     /usr/src/linux/arch/x86/ 2>/dev/null | head -20

# 3. 查詢 MSR IA32_SPEC_CTRL 的值（需要 root + msr 模組）
sudo modprobe msr
sudo rdmsr 0x48    # bit 1 = STIBP, bit 0 = IBRS, bit 2 = SSBD
```

### 練習二：RSB 深度量測實驗

編譯並執行上文的 `rsb_depth_probe.c`，繪製 depth vs. avg_cycles 折線圖。

```bash
gcc -O0 -o rsb_depth_probe rsb_depth_probe.c
./rsb_depth_probe | tee rsb_result.txt
# 用 gnuplot 或 Python matplotlib 繪圖，觀察 depth=16 前後是否有差異
```

### 練習三：閱讀 Linux 核心的 RSB stuffing 實作

在 Linux 核心原始碼中找到 `arch/x86/entry/entry_64.S`，搜尋 `__fill_return_buffer` 或 `FILL_RETURN_BUFFER`。

1. 確認填充的次數（nr 值）
2. 找到它被呼叫的位置（context switch path 哪一行？）
3. 搜尋 `CONFIG_RETPOLINE` 與 `CONFIG_SPECTRE_V2` 如何影響此 macro 的展開

---

## 本章重點整理

- RSB 是每核心 16 entry 的環形緩衝區，負責預測 `ret` 的目標位址，獨立於 BTB
- RSB overflow（call 深度超過 16）、context switch、VM exit、longjmp 都會造成 RSB 與真實 stack 不同步
- RSB 攻擊（ret2spec / SpectreRSB）透過填滿 RSB 讓受害者的 `ret` 在推測執行期間跳到攻擊者 gadget，配合 F+R 洩漏秘密
- ret2spec 變體利用軟體棧被覆寫後 RSB 仍保有原始位址的不同步，同時控制架構執行（ROP）與推測執行（cache 洩漏）
- RSB overflow → BTB fallback 是 retpoline 的盲點，攻擊者可繞過 retpoline 防禦
- 防禦：軟體 RSB stuffing（context switch 時填入 16 個安全位址）；現代 Intel eIBRS 提供硬體層級 RSB 隔離

---

## 自我檢核

1. RSB 和 BTB 各自預測哪種指令？兩者在物理上如何區分（大小、結構）？
2. 「RSB overflow → underflow → BTB fallback」這個鏈條如何讓 retpoline 失效？請畫出流程。
3. ret2spec 和 ROP 都牽涉返回位址。兩者的根本區別是什麼？為什麼傳統 canary 無法防禦 ret2spec？
4. RSB stuffing 若只填 8 個 entry（而 CPU RSB 是 16 entry），攻擊者需要幾次 `ret` 才能觸碰到 stale entry？
5. i7-10700 有 eIBRS，為什麼跨 context RSB 攻擊在此機器上不可行？eIBRS 保護的邊界在哪裡？

---

## 延伸閱讀

- Maisuradze, G. & Rossow, C. (2018). **ret2spec: Speculative Execution Using Return Stack Buffers**. ACM CCS 2018. arXiv:1807.10364. 原始 ret2spec 論文，包含 Linux kernel 與 Chrome sandbox 的實際 PoC。

- Koruyeh, E. M. et al. (2018). **Spectre Returns! Speculation Attacks using the Return Stack Buffer**. USENIX WOOT 2018. arXiv:1807.07940. 獨立同期發現，側重 VM 環境下的跨 context 攻擊。

- Linux Kernel Documentation. **RSB / Return Stack Buffer**. https://docs.kernel.org/admin-guide/hw-vuln/rsb.html — 核心視角的 RSB 漏洞說明與緩解狀態說明。

- Intel. (2018). **Retpoline: A Branch Target Injection Mitigation**. Intel White Paper. — Retpoline 設計文件，理解為何 RSB 被選為 retpoline 的信任基礎，進而理解為何 RSB 攻擊能繞過它。

- ARM Limited. (2022). **Spectre-BHB**. https://developer.arm.com/support/arm-security-updates/speculative-processor-vulnerability/spectre-bhb — ARM 側的預測器污染攻擊，與 RSB 攻擊的演進關係。

---

下一章我們轉向另一個方向——Spectre 只讓 CPU 推測性地讀取資料，而 Meltdown 則讓 CPU 在推測視窗內讀取**架構上沒有權限存取**的核心記憶體。兩者的側通道機制類似，但攻擊前提和緩解手段截然不同。

→ [Ch 18 — Meltdown：核心記憶體的推測性越界讀取](18-meltdown.md)
