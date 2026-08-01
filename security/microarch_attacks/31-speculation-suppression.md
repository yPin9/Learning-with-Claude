# Ch 31 — 推測抑制

> **目標**：深挖「直接壓制推測執行」這條防禦路線——retpoline 把間接跳轉換成 CPU 永遠猜錯的指令序列、lfence 在 bounds check 後插入 load fence 切斷推測路徑、IBRS/IBPB/STIBP 在硬體層面隔離分支預測器狀態。真跑：用 `objdump` 看 retpoline 的真實組語、示範 lfence 如何改變執行序列化行為（附計時對照）、讀本機 spectre_v2 mitigation 狀態。

---

## 推測執行的攻擊面在哪裡

Part 3 的六章描述了各種推測執行攻擊，但它們的「讓 CPU 做壞事」機制其實只有兩條路：

**路徑一：分支預測器被污染（Spectre-v1/v2/RSB/BTI）**

```
攻擊者訓練分支預測器：
    CPU 記錄「上次跳轉到 X，下次也跳到 X」
    ↓
受害者遇到相同的分支 / 間接跳轉：
    CPU 推測跳到 X（攻擊者選的地址）
    ↓
CPU 在推測路徑上執行到 X：
    X 是攻擊者挑選的 gadget，讀出秘密 → 進 cache
    ↓
分支 retire：發現跳錯了，丟掉結果
    但 cache 痕跡留下了 → 攻擊者用 Flush+Reload 讀出
```

**路徑二：權限檢查被推測跳過（Meltdown、Spectre-v1 bounds check）**

```
CPU 推測執行越過 if (x < size) 或越過 page fault 保護：
    讀到不該讀的資料 → 進 cache
    ↓
檢查失敗，推測結果被丟棄
    但 cache 痕跡留下了
```

推測抑制的目標就是打斷這兩條路的某個關鍵環節：
- **不讓分支預測器被攻擊者污染**（IBRS/IBPB/STIBP、retpoline）
- **讓推測執行在敏感點前停下來**（lfence、array_index_nospec）
- **不讓推測路徑讀到秘密資料**（pointer sanitization、constant-time）

本章著重前兩個。

---

## Retpoline：讓間接跳轉「推測到死路」

### 問題：`jmp *%rax` 的危險

間接跳轉（indirect branch）是 Spectre-v2（BTI，Branch Target Injection）的核心攻擊對象。考慮這條指令：

```asm
jmp *%rax     ; 跳到 rax 裡存的位址
```

CPU 在看到這條指令時不知道要跳去哪裡（`%rax` 的值要到 runtime 才確定），但為了不停下 pipeline 等待，它用 **BTB（Branch Target Buffer）** 預測：「上次執行到這條 `jmp` 時跳去了 Y，所以這次也先推測跳去 Y。」

攻擊者用另一段程式碼訓練 BTB，讓「跳去 attacker_gadget」成為 BTB 裡對應這條 `jmp` 地址的預測。受害者執行到 `jmp *%rax` 時，CPU 推測跳到 `attacker_gadget`，在那裡推測執行讀出秘密，再被 Flush+Reload 讀走。

**Retpoline 的思路**：不改變間接跳轉的最終目的地，但讓 CPU 在推測時**走到一個無法前進的地方**，讓推測執行的資源浪費在無用的地方，而不是在攻擊者選定的 gadget 上。

### Retpoline 的組語

```asm
; 替換：jmp *%rax（直接被 BTB 污染）
; 改為：__x86_indirect_thunk_rax（retpoline thunk）

__x86_indirect_thunk_rax:
    call    retpoline_call          ; (1) call 把 rip（即 pause_loop 的地址）push 到 stack 上
                                    ;     CPU 推測：從 call 返回後繼續執行 pause_loop

retpoline_pause:
    pause                           ; (2) CPU 推測執行走到這裡
    lfence                          ; (3) lfence 讓推測執行在這裡停住，等待 ret 結果
    jmp     retpoline_pause         ; (4) 無窮迴圈——推測執行被困在這裡

retpoline_call:
    mov     %rax, (%rsp)            ; (5) 真實路徑：把 rax 覆蓋到 stack 上 call 留下的返回地址
    ret                             ; (6) ret 從 stack 彈出地址 → 跳到 rax 的真實目標
```

關鍵在於**對 RSB（Return Stack Buffer）的利用**：
- `call retpoline_call` 執行時，CPU 的 RSB 記錄「這個 call 的返回地址是 pause_loop」
- 推測路徑：CPU 推測 `ret` 會返回到 pause_loop（RSB 的預測）→ 推測執行進入 `pause; lfence; jmp` 無窮迴圈
- 但 `lfence` 讓推測在這裡等待，等到 `ret` 真正從 stack 彈出的地址確認
- 真實路徑：`retpoline_call` 把 `%rax` 寫到 stack 的返回地址位置，`ret` 彈出的是 `%rax`，跳到正確目標

結果：
- **推測執行走到的地方是 `pause_loop`**，不是 BTB 預測的任何 gadget
- **真實執行走到的地方是 `%rax` 的內容**，和原來的 `jmp *%rax` 相同
- **BTB 被繞過了**：retpoline 不走間接跳轉，它走 `call/ret`，而 `ret` 用 RSB 而非 BTB 預測，只要 RSB 沒被外部污染，預測就是 `pause_loop`

### 本機 objdump 驗證

本機（WSL2 Ubuntu 22.04，gcc 11.4）真跑的組語示範：

```bash
# 先看一個直接間接跳轉（無防禦）
cat > /tmp/indirect_test.c << 'EOF'
typedef int (*fp)(int);
int add_one(int x) { return x + 1; }
int main() { fp f = add_one; return f(41); }
EOF
gcc -O2 -o /tmp/indirect_test /tmp/indirect_test.c
objdump -d /tmp/indirect_test | grep -A2 'call.*rax\|jmp.*rax'
```

**真實輸出（-O2 無 retpoline）：**

```
1014:   ff d0                   call   *%rax       ← 直接間接呼叫
1016:   48 83 c4 08             add    $0x8,%rsp
...
109f:   ff e0                   jmp    *%rax       ← 直接間接跳轉
10a1:   0f 1f 80 00 00 00 00    nopl   0x0(%rax)
```

`ff d0` 是 `call *%rax`，`ff e0` 是 `jmp *%rax`——這兩條指令直接暴露給 BTB 污染。

**真實的 retpoline 組語（手工組合示範，retpoline thunk 的標準格式）：**

```bash
# 我們直接寫出 retpoline thunk 的組語並反組譯
cat > /tmp/retpoline_example.s << 'EOF'
.text
.globl __x86_indirect_thunk_rax
.type __x86_indirect_thunk_rax, @function
__x86_indirect_thunk_rax:
    call    retpoline_call
retpoline_pause:
    pause
    lfence
    jmp     retpoline_pause
retpoline_call:
    mov     %rax, (%rsp)
    ret
EOF
as /tmp/retpoline_example.s -o /tmp/retpoline_example.o
objdump -d /tmp/retpoline_example.o
```

**本機 objdump 真實輸出（已在 WSL2 驗證）：**

```
/tmp/retpoline_example.o:     file format elf64-x86-64

Disassembly of section .text:

0000000000000000 <__x86_indirect_thunk_rax>:
   0:   e8 07 00 00 00          call   c <retpoline_call>

0000000000000005 <retpoline_pause>:
   5:   f3 90                   pause
   7:   0f ae e8                lfence
   a:   eb f9                   jmp    5 <retpoline_pause>

000000000000000c <retpoline_call>:
   c:   48 89 04 24             mov    %rax,(%rsp)
  10:   c3                      ret
```

每個位元組的意義：
- `e8 07 00 00 00`：`call +0x7`（相對跳轉，到 `retpoline_call`）
- `f3 90`：`pause`（PAUSE 指令，降低 memory-order violation 代價，hint CPU 在 spin-wait 中）
- `0f ae e8`：`lfence`（load fence，擋住推測執行繼續越過這點）
- `eb f9`：`jmp -7`（跳回 `pause`，無窮迴圈）
- `48 89 04 24`：`mov %rax, (%rsp)`（把 rax 寫到 stack 返回地址）
- `c3`：`ret`

這就是 Linux kernel 裡每一個 `jmp *%rax` 被 retpoline 替換後的真實樣子。

### Retpoline 在 Linux Kernel 中的部署

Linux kernel 用 `-mindirect-branch=thunk` 或 `-mindirect-branch=thunk-inline` 編譯選項（GCC 7.3+），讓所有間接分支自動替換成 retpoline thunk 呼叫。注意：在本機上，Ubuntu 的系統 headers 預設開了 `-fcf-protection`（Intel CET/IBT 支援），和 `-mindirect-branch` 不相容——這是 Ubuntu 選擇的 CET 替代路線，在有 CET 硬體的新 CPU 上 IBT 比 retpoline 效能更好。

### 效能代價

Retpoline 的代價來自把「一條快速的間接跳轉」換成「一個有 call、mov、ret 的序列」。每次間接跳轉多幾條指令，但更重要的是：

1. **RSB 被佔用一個 slot**：`call retpoline_call` 推進了 RSB，但緊接著的 `ret` 用掉它。如果呼叫深度深，RSB 可能 underflow。
2. **Pipeline 氣泡**：`lfence` 在 `pause_loop` 裡強制序列化，推測執行在這裡等待，pipeline 利用率降低。
3. **分支預測 miss 增加**：原本 BTB 能正確預測的跳轉，現在換成了「永遠預測錯」——雖然這是設計，但仍然有 pipeline refetch 代價。

典型數字：間接跳轉密集的 kernel code（如 virtual function dispatch、callback chain）慢 5–20%；一般 userspace 幾乎感覺不到（大部分 hot path 是直接跳轉）。

### 本機 retpoline 狀態

```bash
cat /sys/devices/system/cpu/vulnerabilities/spectre_v2
# → Mitigation: Enhanced / Automatic IBRS; IBPB: conditional;
#   PBRSB-eIBRS: SW sequence; BHI: SW loop, KVM: SW loop
```

`Enhanced / Automatic IBRS` 表示本機使用的是 eIBRS 而非 retpoline——因為 i7-10700 支援 Enhanced IBRS（硬體層面），不需要 retpoline 的軟體緩解。retpoline 是給不支援 eIBRS 的舊 CPU 用的 SW workaround；eIBRS 直接在 CPU 微碼層面限制間接分支的跨特權預測，代價更低。

---

## lfence：推測執行的硬路障

### 什麼是 lfence

`LFENCE`（Load Fence）是 x86 的一條記憶體柵欄指令，語意是：

> 在 `lfence` 之前的所有 load 指令全部 retire，才允許 `lfence` 之後的指令開始執行。

在 Intel 的亂序執行模型下，load 可以提前推測執行（即使之前的指令還沒 retire）。`lfence` 把這個窗口強制關閉：`lfence` 後面的指令，必須等 `lfence` 前面所有 load 都確認完成，才能進 pipeline。

這對 Spectre-v1 防禦的意義：

```c
// 有漏洞的版本：
if (x < array_size) {            // (A) bounds check
    y = array2[array1[x] * 64];  // (B) 推測執行時，x 越界仍進行
}
// CPU 在 (A) 的分支結果確認前，可能推測執行 (B)

// 加 lfence 後：
if (x < array_size) {
    _mm_lfence();                 // ← lfence 在這裡！
    y = array2[array1[x] * 64];  // (B) 必須等 (A) 的 bounds check 完全確認
}
// lfence 讓 CPU 在繼續 (B) 前，必須等 (A) 的比較結果確認
// 即使預測器預測「不跳轉」，lfence 讓 (B) 在推測路徑上也無法開始
```

更精確的描述：`lfence` 序列化指令流，讓後續指令的執行不能超越它。在 bounds check 後插入 `lfence`，讓越界讀取（B）無法在推測路徑上搶先執行。

### lfence 的效能代價（真實量測）

本機實測（WSL2 i7-10700，`taskset -c 2`）：

```c
// 測試：兩個 cache miss 存取，有/無 lfence 中間
// 無 lfence：CPU 可以 pipeline 兩個 load（部分重疊執行）
// 有 lfence：第二個 load 必須等第一個 load 完全 retire
```

**真實輸出（已驗證）：**

```
無 lfence 平均: 252.9 cycles/pair
有 lfence 平均: 403.9 cycles/pair
lfence 序列化開銷: +151 cycles (1.6x)

（第二次跑）
無 lfence 平均: 226.5 cycles/pair
有 lfence 平均: 362.6 cycles/pair
lfence 序列化開銷: +136 cycles (1.6x)
```

**解讀**：兩個連續 cache miss（各約 240 cycles），沒有 lfence 時 CPU 可以讓它們部分 pipeline 重疊，總時間約 253 cycles（比兩個各自的延遲小）。有 lfence 後，第二個 load 必須等第一個完全結束才開始，總時間 404 cycles（約 1.6 倍）。

這個 1.6x 的開銷就是「lfence 序列化代價」——在 Spectre-v1 防禦中，bounds check 後的每個陣列存取都得付這個代價。

### 現代 kernel 的 Spectre-v1 mitigation

本機 sysfs：

```
spectre_v1: Mitigation: usercopy/swapgs barriers and __user pointer sanitization
```

這一行告訴你 kernel 用了兩種技術：

**1. usercopy barriers**：在 `copy_from_user`/`copy_to_user`（kernel 從使用者空間讀/寫資料的函式）的邊界加入 speculation barrier，防止 kernel 在處理使用者提供的指標時被推測執行帶到錯誤的 kernel 記憶體。

**2. swapgs barriers**：SWAPGS 是 x86 切換到 kernel GS register 的指令，在 Spectre-v1 的 context 下，SWAPGS 的推測執行可以讓 kernel code 在還沒準備好的 GS 環境下跑到有洩漏的 gadget。Kernel 在 SWAPGS 前後加入 `lfence` 防止這個。

**3. `__user` pointer sanitization**：這是 Linux kernel 的 `__uaccess_mask_ptr` 技術——把使用者提供的指標做 masking：

```c
/* kernel 的 __uaccess_mask_ptr 實作概念 */
static inline void *mask_user_address(const void __user *p) {
    unsigned long mask;
    /* 如果 p 在使用者空間範圍內，mask = ~0；否則 mask = 0 */
    /* 用無分支的算術運算實現，不暴露分支給分支預測器 */
    asm("cmp %1,%0; sbb %0,%0" :"=r" (mask) :"r" (p));
    return (__force void *)(p & mask);
}
```

即使在推測執行路徑上，超出使用者空間的指標被 masking 成 NULL，`copy_from_user` 讀不到 kernel 資料。

### array_index_nospec：不用 lfence 的替代方案

`lfence` 在每個需要保護的陣列存取前加，代價固定（每次序列化 +100–200 cycles）。Linux kernel 提供了另一個工具：`array_index_nospec`，用**無分支算術**把越界的 index 強制清零：

```c
/* 原始實作概念（kernel include/linux/nospec.h）：
 * 如果 index < size，返回 index；
 * 如果 index >= size，返回 0
 * 全程無 conditional branch，預測器無法推測「走了哪條路」
 */
static inline size_t array_index_nospec(size_t index, size_t size) {
    /* 計算 mask：
     * (size - 1 - index) 的 MSB：
     *   如果 index < size  → 非負 → MSB=0 → 移位得 0 → mask = ~0
     *   如果 index >= size → 負數 → MSB=1 → 移位得全 1 → mask = 0
     */
    unsigned long mask = ~(size_t)0;
    /* 真實 kernel 用無 UB 的方式計算，下面是示意 */
    mask &= ~((long)(index | (mask - index)) >> 63);
    return index & mask;
}
```

用法：

```c
/* 有 Spectre-v1 風險：*/
void victim_fn(size_t idx) {
    if (idx < array_size)
        return array[idx];  /* 推測執行時 idx 可能越界 */
}

/* 修好後：*/
void victim_fn(size_t idx) {
    idx = array_index_nospec(idx, array_size);  /* 越界 idx → 0，無分支 */
    return array[idx];                           /* 即使推測，也只讀 array[0] */
}
```

**array_index_nospec vs lfence 的取捨**：

| | lfence | array_index_nospec |
|---|---|---|
| 機制 | 序列化指令流，阻止推測繼續 | 越界 index 被清零，推測執行讀 array[0] |
| 代價 | 固定序列化開銷（+100–200 cycles） | 幾條額外算術指令（很便宜） |
| 洩漏風險 | 推測路徑完全停止，array[0] 不洩漏 | 推測路徑讀 array[0]，array[0] 可能被洩漏（通常可接受） |
| 使用場景 | 高安全性邊界，完全不能有推測讀 | 大多數 kernel 陣列存取，array[0] 洩漏可接受 |

現代 kernel 大量使用 `array_index_nospec`，偶爾在特別敏感的點（如 swapgs 前後）加 `lfence`。

---

## IBRS / IBPB / STIBP：硬體層面的預測器屏障

### IBRS（Indirect Branch Restricted Speculation）

**問題**：Spectre-v2 的 BTI（Branch Target Injection）靠的是攻擊者在 BTB 裡填入錯誤的 target，讓受害者的間接跳轉推測到攻擊者選的 gadget。

**IBRS 的做法**：Intel 在 MSR `IA32_SPEC_CTRL`（`0x48`）的 bit 0 加了 IBRS 控制位。設定後：

> **當前的特權級不能受到低特權級填充的 BTB entry 影響。**

具體：kernel mode 開啟 IBRS 後，user mode 填的 BTB 不能影響 kernel mode 的間接分支預測；VM host 開啟 IBRS 後，VM guest 填的 BTB 不能影響 host 的間接分支預測。

**舊 IBRS 的代價**：每次進入 kernel（syscall、中斷）要寫 MSR 開啟 IBRS，每次返回 userspace 要寫 MSR 關閉——MSR 寫是非常貴的操作（約 100–200 cycles）。早期部署（2018 年）讓 syscall 密集的工作負載慢了 20–30%。

**Enhanced IBRS（eIBRS）**：Intel Cascade Lake 以後（對本機 Comet Lake 也有），引入 eIBRS。eIBRS 是「持續模式」——只要 CPU 啟動時設定一次，不需要每次 kernel entry/exit 都寫 MSR。代價大幅降低。

```bash
# 本機的 IBRS 相關 CPU flags
grep -o 'ibrs\|ibrs_enhanced\|ibpb\|stibp' /proc/cpuinfo | sort -u
# → ibpb
# → ibrs
# → ibrs_enhanced
# → stibp
```

`ibrs_enhanced` 表示本機 CPU 支援 eIBRS，kernel 使用 eIBRS 模式而非舊 IBRS。

### IBPB（Indirect Branch Predictor Barrier）

`IBPB` 是一個**一次性 barrier**：執行後，之前填入 BTB 的所有 entry 對之後的推測執行無效。功能上像「清空分支預測歷史」。

**使用場景**：在特別敏感的邊界執行一次 IBPB，確保之前的 code（可能被不信任的攻擊者控制）填的 BTB 不能影響此後的執行。

```
cat /sys/devices/system/cpu/vulnerabilities/spectre_v2
→ IBPB: conditional
```

「conditional」表示 kernel 不是每次 syscall 都執行 IBPB，而是在特定高風險的 context switch 邊界才執行——比如從一個不信任的行程切換到高特權的 service。每次 IBPB 代價約 200–500 cycles，全開會讓排程密集的系統慢 10–20%。

**IBPB vs IBRS 的差別**：

```
IBRS：持續性屏障，防止「此後在低特權填的 BTB 污染高特權的推測」
IBPB：一次性清除，清空「之前累積的所有 BTB 歷史」

用比喻：
IBRS 是「不讓低年級的學生在高年級的黑板上寫東西」
IBPB 是「把黑板上所有東西全部擦掉」
```

### STIBP（Single Thread Indirect Branch Predictors）

**問題**：在 SMT 環境下，同一個物理核心的兩條 hardware thread 共用 BTB。thread 0 訓練的 BTB entry 可以影響 thread 1 的間接分支預測。如果 thread 0 是攻擊者，thread 1 是受害者，跨 thread 的 Spectre-v2 攻擊可行。

**STIBP**：把 BTB 從「整個物理核心共享」改成「每條 hardware thread 獨立」。設定後，thread 0 和 thread 1 的 BTB 相互隔離，跨 thread BTB 污染不可行。

```
stibp: (CPU flag in /proc/cpuinfo)
```

STIBP 在 kernel 裡是「按需啟用」的——通常只對啟用了 PR_SPEC_INDIRECT_BRANCH 抑制的行程（透過 prctl）啟動 per-thread STIBP。因為 STIBP 代價約 2–10%（BTB miss 率上升），全局開啟在非必要場景浪費效能。

### SSBD（Speculative Store Bypass Disable）

**問題**：Spectre v4（Speculative Store Bypass）：在 store + load 序列中，load 可能推測性地讀到 store 寫入前的舊值（store forwarding 的推測化）。如果 store 的值是攻擊者控制的、而 load 的結果被用於後續計算，可以洩漏秘密。

**SSBD**：禁止推測 store forwarding，確保 load 一定等到 store 確認完成再讀值。

```
spec_store_bypass: Mitigation: Speculative Store Bypass disabled via prctl
ssbd: (CPU flag)
```

SSBD 透過 `prctl(PR_SET_SPECULATION_CTRL, PR_SPEC_STORE_BYPASS, PR_SPEC_DISABLE, ...)` 啟用，是 per-process 的——安全敏感行程（如密碼管理器）啟用，普通行程不啟用，避免全局效能損失（store-load forwarding 是 CPU 很重要的效能優化，禁掉大約 3–8%）。

---

## Spectre-safe coding patterns 彙整

除了上述的硬體/compiler 防禦，還有幾個在 code 層面的 pattern：

### Pattern 1：不讓推測執行到達有秘密的路徑

```c
/* 危險：bounds check 後直接讀秘密 */
if (user_idx < secret_array_size)
    leak_via_cache(secret_array[user_idx]);

/* 安全：array_index_nospec 無分支替換 index */
size_t safe_idx = array_index_nospec(user_idx, secret_array_size);
leak_via_cache(secret_array[safe_idx]);  /* safe_idx=0 if OOB */

/* 安全：lfence 讓推測不繼續 */
if (user_idx < secret_array_size) {
    _mm_lfence();
    leak_via_cache(secret_array[user_idx]);
}
```

### Pattern 2：不讓推測執行依賴秘密資料做 cache 存取

```c
/* 危險：cache 存取的地址 = f(秘密) → 洩漏秘密 */
volatile char dummy = probe_array[secret_byte * 64];

/* 安全（constant-time access）：訪問所有可能的 byte，混淆哪個是真的 */
/* 但這代價極高，通常不這樣做；而是從根本不讓秘密進入推測路徑 */
```

### Pattern 3：不信任使用者提供的函數指標

```c
/* 危險：callback 是使用者提供的，BTB 被訓練指向 gadget */
callback = user_provided_function_ptr;
callback(data);

/* 安全：用 retpoline thunk 包裝 */
extern void __x86_indirect_thunk_rax(void);
asm("mov %0, %%rax; call __x86_indirect_thunk_rax" :: "r"(callback));
```

### Pattern 4：把秘密操作放在 IBPB 之後

```c
/* 從不信任的 context 切回到信任的 service */
switch_from_untrusted_task();
asm volatile("wrmsr" :: "c"(0x49), "A"(0));  /* IBPB（MSR 0x49 bit 0 = IBPB command） */
/* 現在 BTB 被清空，之前 untrusted task 填的 BTB 無效 */
process_sensitive_request();
```

---

## 對比與取捨

| 機制 | 擋住什麼 | 代價 | 適用場景 | 殘餘風險 |
|------|---------|------|---------|---------|
| retpoline | Spectre-v2 BTI（BTB 污染） | 間接跳轉 5–20% 慢 | 舊 CPU 無 eIBRS | 某些路徑（RSB underflow）可能繞過 |
| eIBRS | Spectre-v2 BTI（跨特權） | < 3%（持續模式） | 現代 Intel CPU（Cascade Lake+） | 不擋同特權級 BHI |
| lfence | Spectre-v1 bounds check bypass | 約 +150 cycles/次（1.6x） | 高安全 bounds check | 每個 call site 要手動加；漏加就是 gadget |
| array_index_nospec | Spectre-v1（無分支替換） | 幾條算術指令（便宜） | 大多數 kernel 陣列存取 | 越界時讀 array[0]（小洩漏） |
| IBPB | 清除 BTB 歷史 | 200–500 cycles（每次） | 信任邊界 context switch | 只在執行 IBPB 後才清除，高頻率代價高 |
| STIBP | 跨 SMT thread BTB 污染 | 2–10% | SMT 環境下的跨 thread 隔離 | 不擋同 thread 的 BTB 污染 |
| SSBD | Spectre v4（store forwarding）| 3–8% | 密碼敏感行程 | per-process opt-in，非全局 |
| `__user` sanitization | Spectre-v1 via 使用者指標 | 幾條 mask 指令 | kernel syscall 路徑 | 只擋使用者指標路徑，不擋 kernel 內部 gadget |

---

## 踩雷集錦

1. **認為 retpoline 一勞永逸解決 Spectre-v2**：retpoline 的設計假設「RSB 沒有被攻擊者污染」。2022 年 Retbleed 和 2023 年 Inception 分別展示了在不同 CPU 上，RSB 被污染或 retpoline 的 pause loop 可以被攻擊者利用的路徑。eIBRS 繞過也有 PBRSB，BHI 繞過 eIBRS。Spectre-v2 的防禦是一個持續演化的故事，不是「裝了 retpoline 就好」。

2. **忘了 lfence 只阻止 load 的推測，不序列化 store**：`lfence` 是 load fence——它讓後續 load 指令等待前面的 load retire，但它不序列化 store。如果你的秘密洩漏路徑涉及 store 的推測（如 Spectre v4），需要 `sfence` 或 `mfence`，或是 SSBD。把 `lfence` 當成「所有推測的通用屏障」是錯的。

3. **以為在每個 if 前面都加 lfence 就安全了**：lfence 的插入位置必須精確——要在 bounds check **通過之後**、秘密讀取**之前**。如果插在 bounds check 前面，等 bounds check 執行完了再推測執行，推測仍然能在秘密讀取上發生。正確位置：`if (x < size) { lfence(); use(array[x]); }`。

4. **混淆 IBRS 和 IBPB 的語意**：IBRS 是「持續性防禦」（設定後低特權不能污染高特權 BTB），IBPB 是「一次性清除」（清空目前為止的 BTB 歷史）。它們解決的問題不同：IBRS 擋的是「未來的污染」，IBPB 清除的是「過去的污染」。在 context switch 時，同時需要 IBPB（清掉前一個 task 填的 BTB）和 IBRS（防止當前 task 的 BTB 被下一個 task 污染），缺一不可。

5. **在本機 WSL2 看到 eIBRS 就以為不需要 retpoline**：eIBRS 擋的是「低特權→高特權」的 BTB 污染，但它不擋「同特權級之間」（如跨行程的 Spectre-v2）。BHI（Branch History Injection）繞過 eIBRS 的方式就是用同特權級的分支歷史。本機的 `BHI: SW loop` 是額外加的 SW 緩解。「有 eIBRS = 完全安全」的想法在 2022 年 BHI 出現後就不成立了。

6. **把 array_index_nospec 的語意當成「越界時不存取」**：`array_index_nospec(index, size)` 在越界時返回 **0**，不是不存取——後續的 `array[0]` 仍然被讀取（推測路徑和真實路徑都是）。如果 `array[0]` 本身是秘密（比如你的 array 開頭就是敏感資料），這個防禦不夠。需要確保 `array[0]` 是可以被洩漏的安全資料，或是改用 lfence。

---

## 進階：再往深一層

**BHI（Branch History Injection）繞過 eIBRS 的機制**：eIBRS 的保護邊界是「特權級」——它防止 user mode 在 BTB 裡填的 entry 影響 kernel mode 的推測。但 CPU 除了 BTB 還有 BHB（Branch History Buffer），紀錄最近執行的分支歷史，用來輔助 BTB 的預測。BHI 讓攻擊者用 user mode 的分支歷史「引導」kernel mode 在 BTB 裡找到攻擊者想要的 entry——即使這個 BTB entry 是 kernel 自己填的，也能被 user mode 的分支歷史「激活」指向特定的 gadget。eIBRS 阻止了「user 填 BTB entry 影響 kernel」，但沒有阻止「user 的 BHB 歷史影響 kernel 對 BTB 的查找」。Linux 的 `BHI: SW loop` 緩解是在每次 kernel entry 前執行一段特殊的分支序列，把 BHB 的歷史清空成「已知無害的狀態」。

**RSB Stuffing（Return Stack Buffer 填充）**：retpoline 的安全性依賴 RSB 裡有「pause_loop 的返回地址」——這個 entry 是 `call retpoline_call` 推進去的。如果在此之前 RSB 被耗盡（RSB underflow，通常在深層 call stack 後），CPU 會改用 BTB 預測 `ret` 的目標，讓 retpoline 的保護失效。Kernel 的對策是 RSB stuffing：在 kernel entry 時（如 syscall handler）先執行一段虛假的 call/ret 序列，把 RSB 填滿「已知安全的地址」，讓之後的 retpoline 能可靠依賴 RSB。

**Spectre 的 software mitigation 自動化難題**：理論上，編譯器可以自動掃描所有可能的 Spectre-v1 gadget 並插入 lfence/array_index_nospec。但實際困難在於：「什麼算是 Spectre-v1 gadget」沒有完整的形式化定義，攻擊者可以用多步間接傳播秘密（A 的推測讀取影響 B，B 的推測讀取影響 C，最後 C 的 cache 狀態被 Flush+Reload 讀出）。自動工具（如 LLVM 的 speculative load hardening，`-mspeculative-load-hardening`）只能覆蓋明顯的 single-step 路徑，漏掉的比例不明。Google 的 SFI/CFI 結合 speculative hardening 是目前覆蓋率最高的工業方案，但效能代價也最高（高達 50%）。

**後 retpoline 時代的 eIBRS + retpoline 共存**：有趣的是，Linux 在有 eIBRS 的 CPU 上**同時**用 retpoline 和 eIBRS——eIBRS 擋跨特權污染，retpoline 擋同特權級的 BTI（以及 RSB 相關路徑）。這兩個防禦互補，不互斥。kernel 的 `MITIGATION_RETPOLINE` 和 `MITIGATION_ENHANCED_IBRS` 可以同時啟用，這是「防禦縱深」而非「選一個就好」。

---

## 動手練習

1. **看 retpoline 真實組語**：

   ```bash
   cat > /tmp/retpoline_demo.s << 'EOF'
   .text
   .globl __x86_indirect_thunk_rax
   __x86_indirect_thunk_rax:
       call    retpoline_call
   retpoline_pause:
       pause
       lfence
       jmp     retpoline_pause
   retpoline_call:
       mov     %rax, (%rsp)
       ret
   EOF
   as /tmp/retpoline_demo.s -o /tmp/retpoline_demo.o
   objdump -d /tmp/retpoline_demo.o
   ```
   
   對照本章的解析，確認每條指令的位元組編碼（`f3 90` = pause，`0f ae e8` = lfence）和它在 retpoline 整體機制中的角色。

2. **量測 lfence 的序列化代價**：

   ```c
   /* lfence_timing.c */
   #define _GNU_SOURCE
   #include <stdio.h>
   #include <stdint.h>
   #include <x86intrin.h>
   volatile uint8_t buf[4096 * 64];
   
   static inline uint64_t rdtscp_f(void) {
       unsigned junk; _mm_lfence();
       return __rdtscp(&junk);
   }
   
   int main(void) {
       for(int i=0;i<100;i++) (void)buf[i*64];
       uint64_t s1=0, s2=0; int N=10000;
       for(int i=0;i<N;i++){
           _mm_clflush((void*)&buf[1024]); _mm_clflush((void*)&buf[2048]); _mm_mfence();
           uint64_t t0=rdtscp_f(); (void)buf[1024]; (void)buf[2048]; s1+=rdtscp_f()-t0;
       }
       for(int i=0;i<N;i++){
           _mm_clflush((void*)&buf[1024]); _mm_clflush((void*)&buf[2048]); _mm_mfence();
           uint64_t t0=rdtscp_f(); (void)buf[1024]; _mm_lfence(); (void)buf[2048]; s2+=rdtscp_f()-t0;
       }
       printf("無 lfence: %.1f cycles\n有 lfence: %.1f cycles\n開銷: %.1fx\n",
              (double)s1/N, (double)s2/N, (double)s2/s1);
   }
   ```
   
   ```bash
   gcc -O1 -o /tmp/lfence_timing /tmp/lfence_timing.c
   taskset -c 2 /tmp/lfence_timing
   ```
   
   對照本章的真實輸出（約 1.6x 開銷）；你的機器數字可能不同。

3. **確認本機的推測抑制 mitigation 狀態**：

   ```bash
   cat /sys/devices/system/cpu/vulnerabilities/spectre_v1
   cat /sys/devices/system/cpu/vulnerabilities/spectre_v2
   cat /sys/devices/system/cpu/vulnerabilities/spec_store_bypass
   cat /sys/devices/system/cpu/vulnerabilities/retbleed
   # 把輸出對照本章的機制表，找出對應的防禦
   grep -o 'ibrs\|ibpb\|stibp\|ssbd\|ibrs_enhanced' /proc/cpuinfo | sort -u
   ```

4. **array_index_nospec 的行為驗證**：

   ```c
   /* 測試 array_index_nospec 的語意 */
   #include <stdio.h>
   #include <stdint.h>
   
   static inline size_t array_index_nospec(size_t index, size_t size) {
       size_t mask = ~(size_t)0;
       /* 簡化版：用 signed subtraction 的溢出行為 */
       mask &= ~((size_t)((long)(size - 1 - index) >> (sizeof(long)*8 - 1)));
       return index & mask;
   }
   
   int main(void) {
       uint8_t data[8] = {10,20,30,40,50,60,70,80};
       for (size_t i = 0; i < 12; i++) {
           size_t safe = array_index_nospec(i, 8);
           printf("index=%zu -> safe=%zu, data[safe]=%d %s\n",
                  i, safe, data[safe], i<8?"(valid)":"(OOB→0)");
       }
   }
   ```
   
   確認：越界 index 被映射到 0（讀 data[0]=10），不是超出陣列。理解這為什麼仍有「洩漏 data[0]」的殘餘風險。

5. **在 kernel source 裡找 Spectre-v1 防禦**：

   ```bash
   # 下載或找一份 Linux kernel source
   # 在 arch/x86/include/asm/nospec-branch.h 裡找 array_index_nospec 的實際實作
   # 在 arch/x86/kernel/cpu/bugs.c 裡找 spectre_v1_select_mitigation()
   # 在 arch/x86/entry/entry_64.S 裡找 swapgs barriers（FENCE_SWAPGS_*）
   ```
   
   把 kernel 的實際實作對照本章的概念描述，找出哪些細節本章省略了。

---

## 本章重點整理

- 推測抑制的核心是「讓 CPU 在敏感點前停下推測，或讓推測路徑走到死路」——本質是效能的主動犧牲換安全。
- **Retpoline**：把間接跳轉替換成「call + pause_loop + lfence + jmp」的序列，讓 CPU 的推測路徑走進無限迴圈，不走攻擊者訓練的 BTB target。本機 objdump 確認：`e8 07 00 00 00`（call）+ `f3 90`（pause）+ `0f ae e8`（lfence）+ `eb f9`（jmp）。
- **eIBRS**：本機使用 Enhanced IBRS 取代 retpoline——eIBRS 在硬體層面持續阻止低特權 BTB 污染高特權，代價低於 retpoline。本機 flags 包含 `ibrs_enhanced`。
- **lfence**：在 bounds check 後插入 load fence，讓後續 load 指令等前面的 bounds check 確認完成，不能在推測路徑上搶先執行。本機實測：每對 cache miss 存取，有 lfence 比無 lfence 慢 **1.6x（+136–151 cycles）**。
- **array_index_nospec**：無分支算術把越界 index 清零，讓推測路徑讀 array[0] 而非真正越界地址。代價低於 lfence，但 array[0] 仍可能被洩漏。
- **IBPB/STIBP/SSBD**：分別針對「清除 BTB 歷史」「SMT 跨 thread BTB 隔離」「禁止 store forwarding 推測」。本機全部支援，但採用 conditional/per-process 模式以平衡效能。
- 沒有單一的推測抑制機制能涵蓋所有 Spectre 變體——現代 kernel 是 retpoline/eIBRS/IBPB/RSB stuffing/BHI SW loop 的組合。

---

## 自我檢核

- [ ] 不看筆記，畫出 retpoline thunk 的六條指令、每條指令的用途、以及為什麼推測執行會走到 `pause_loop` 而不是攻擊者的 gadget。
- [ ] `lfence` 在 Spectre-v1 防禦中的正確插入位置是哪裡？插錯了（放在 bounds check 前）會怎樣？
- [ ] 本機 `spectre_v2` 用的是 eIBRS 還是 retpoline？兩者的差別是什麼？為什麼現代 CPU 傾向 eIBRS？
- [ ] IBRS 和 IBPB 的語意差別是什麼？什麼情況下需要 IBPB 而不只是 IBRS？
- [ ] BHI 怎麼繞過 eIBRS？為什麼 eIBRS 不能防住它？本機的 `BHI: SW loop` 緩解是怎麼工作的？
- [ ] 如果你要在 kernel 裡新增一個接受使用者提供 index 的 syscall，你會用哪種防禦（lfence / array_index_nospec / `__user` sanitization）？為什麼？

---

## 延伸閱讀

### 論文

- **[Spectre Attacks: Exploiting Speculative Execution](https://spectreattack.com/spectre.pdf)** — Kocher et al., IEEE S&P 2019
  - **讀哪裡**：Section VI（Mitigations）——這是 Spectre 論文本身對防禦的分析；retpoline 和 lfence 的概念在這裡首次系統性出現。
  - **學到什麼**：攻擊者眼中的防禦面——哪些防禦有效、哪些被繞過、哪些只是增加攻擊成本。原始論文的防禦評估不會帶有後來補丁的美化。
  - **為什麼值得**：Ch 14 的攻擊和本章的防禦是同一篇論文的兩面；對照著讀才能理解防禦設計的動機。

- **[Retpoline: A Software Construct for Preventing Branch-Target-Injection](https://support.google.com/faqs/answer/7625886)** — Paul Turner, Google 2018
  - **讀哪裡**：全文（短，只有幾頁）——這是 retpoline 原始技術說明。
  - **學到什麼**：retpoline 怎麼被設計出來的、它的安全假設是什麼（依賴 RSB 而非 BTB）、在哪些 CPU 上有哪些例外（Skylake 的某些路徑仍可能讓 RSB underflow 讓 BTB 取而代之）。
  - **為什麼值得**：理解 retpoline 的正確姿勢是「知道它依賴 RSB 的假設」，不是只會背「替換了 indirect branch」。

- **[Branch History Injection: On the Effectiveness of Hardware Mitigations Against Cross-Privilege Spectre-v2 Attacks](https://www.vusec.net/projects/bhi-spectre-bhb/)** — Barberis et al., USENIX Security 2022
  - **讀哪裡**：Section 3（BHI 的完整攻擊路徑）和 Section 5（PoC 說明）。
  - **學到什麼**：eIBRS 為什麼不夠；BHB 和 BTB 的區別；用 user-space branch history 引導 kernel 找到特定 BTB entry 的機制。
  - **為什麼值得**：本機 sysfs 裡 `BHI: SW loop` 這一行的完整背景在這篇。不讀這篇你不知道為什麼要在 kernel entry 前做 BHB 清空。

### 官方文件

- **[Linux kernel: Speculation Mitigations](https://www.kernel.org/doc/html/latest/admin-guide/hw-vuln/spectre.html)** — kernel.org
  - **讀哪裡**：「Retpoline」和「Spectre v1 Mitigations」兩節，以及「Overview」的 mitigation table。
  - **學到什麼**：kernel 提供的所有相關 boot parameter（`spectre_v2=retpoline`、`nospectre_v1` 等）；哪些 mitigation 預設開、哪些需要明確啟用；在什麼 CPU microarchitecture 上哪些選項有效。
  - **為什麼值得**：實際部署時「我要調哪個 boot parameter」的第一手文件。

- **[Intel Speculative Execution Side Channel Mitigations White Paper](https://www.intel.com/content/www/us/en/developer/articles/technical/software-security-guidance/technical-documentation/speculative-execution-side-channel-mitigations.html)** — Intel
  - **讀哪裡**：「Indirect Branch Control」和「IBRS/IBPB/STIBP Technical Details」章節。
  - **學到什麼**：MSR `IA32_SPEC_CTRL` 的 bit 格式；eIBRS 和舊 IBRS 的精確語意差異；IBPB 的執行代價（cycle count）。
  - **為什麼值得**：硬體 barrier 的最權威技術規格。

### 技術文章

- **[Meltdown and Spectre: How deep do they go?](https://www.brendangregg.com/blog/2018-02-09/kpti-kaiser-meltdown-performance.html)** — Brendan Gregg
  - **讀哪裡**：Spectre mitigation 的效能影響分析，以及 perf 怎麼量。
  - **學到什麼**：專業的效能分析師如何評估 mitigation 代價；什麼 metric（instruction retired、IPC）能看出 speculation 被壓制的影響。
  - **為什麼值得**：把本章「lfence 讓執行慢 1.6x」這樣的微觀數字，連結到系統層面的實際影響。

下一章從「不讓 CPU 洩漏」轉向「讓程式碼本身就是沒東西可洩漏的」——constant-time 程式設計的形式化定義、驗證工具（ct-verif、dudect）、常見密碼學原語的 constant-time 實作方法，以及在 WSL2 上用 dudect 實際量測你的函式有沒有 timing leak。

→ [Ch 32 Constant-time 程式設計](32-constant-time-programming.md)
