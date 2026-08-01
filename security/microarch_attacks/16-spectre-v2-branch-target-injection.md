# Ch 16 — Spectre v2（Branch Target Injection）

> **目標**：理解 Spectre-v2 為什麼比 v1 危險得多——它能跨 context 污染 BTB，讓受害者的間接跳轉推測到攻擊者選定的 gadget，實現跨 privilege 的記憶體洩漏。掌握 retpoline 的工作原理（附組語解析），以及 IBRS/IBPB/eIBRS 的角色與限制。完整跨-context BTB 注入在 WSL2 難穩定重現，本章誠實標注哪些是理論分析、哪些是概念驗證。

Spectre-v1 攻擊的是**同一個 process 內**的越界讀取，需要在受害者 process 裡找到合適的 gadget（有 `if(x < size)` + 記憶體存取的模式），然後訓練 PHT。這已經很危險了。

Spectre-v2 的攻擊能力更強：**跨 context 污染 BTB**，讓受害者 process 的間接跳轉（`jmp rax`、`call [rbx]`、vtable dispatch）在推測執行時跳到攻擊者選定的 gadget——**gadget 在受害者 process 的地址空間裡**，但攻擊者不在那裡。在 KPTI 前，這讓 user space 攻擊者能讓 kernel 推測執行攻擊者選定的 kernel gadget，洩漏 kernel 記憶體。

## Spectre-v1 vs. Spectre-v2 的本質差異

```
Spectre-v1：打 PHT（條件分支方向）
─────────────────────────────────────────────────────
攻擊者         受害者 process
───────        ─────────────────────────────────────
訓練 PHT →     victim() 裡的 if(x < size)
               PHT 說「taken」→ 推測執行越界讀取
               gadget 在受害者 process 裡，形如
               if(x < size) { y = array[x]; z = probe[y * S]; }
               攻擊者必須找到這個 gadget 才能用

Spectre-v2：打 BTB（間接跳轉目標）
─────────────────────────────────────────────────────
攻擊者         受害者 process（如 kernel）
───────        ─────────────────────────────────────
訓練 BTB →     victim 的 indirect jmp/call
               BTB 說「跳到 attacker 選的目標」→
               推測跳到 attacker 選的 gadget（仍在受害者 AS）
               這個 gadget 形如
               mov rax, [memory]       ← 讀 secret
               movzx rbx, al           ← 用 secret 當 index
               mov rcx, [probe + rbx * S] ← cache 傳遞
               攻擊者可以在 kernel/victim 裡找任何這樣的序列
               遠比 v1 的三要素組合更容易找到
```

關鍵差異：Spectre-v2 的 gadget 只需要是「讀記憶體然後用結果當另一個 load 的 index」，不需要有邊界檢查——這樣的序列在 kernel 裡到處都是（任何 switch/lookup table 實作都有）。

## BTB 污染的機制

### 間接分支是什麼

間接分支（indirect branch）的目標不是立即數，而是暫存器或記憶體值：

```asm
jmp rax               ; 跳到 rax 裡的位址
call [rbp + 8]        ; 呼叫 [rbp+8] 指向的函式
jmp [rcx + rdx*8]     ; 跳到 jump table entry
```

C++ 的虛函式（vtable）、函式指標陣列（dispatch table）、longjmp 都是間接分支。kernel 裡也大量使用間接分支（syscall 分發、driver callback、中斷處理）。

### BTB 的跨 context 共享

如 Ch 15 所說，BTB 按照 `(PC 的某些 bits)` 索引，記錄「這個 PC 最近一次的跳轉目標」。在沒有隔離的情況下，user A process 在 PC=0x4030 的 `call [rax]` 訓練 BTB 指向 0xABCD，然後 user B process 或 kernel 在相同（或 alias）的 BTB index 上查找，得到的預測目標就是 0xABCD——即使 0xABCD 在 user A 的 AS 裡根本無效。

```
攻擊者 process（User A）            受害者（kernel or User B）
─────────────────────────           ─────────────────────────────────
在 VA 0x4030 執行：                  在 VA 0x4030（alias of BTB index）
  call [rax]   ← 實際跳到 0xF000     有一個 indirect call：
                 BTB[hash(0x4030)] = 0xF000  call [rbx]
重複多次，訓練 BTB                    CPU 查 BTB：→ 預測目標 0xF000
                                    0xF000 = attacker 選的 gadget
                                    推測執行 gadget（在受害者 AS 裡）
```

攻擊者需要：
1. 找到 kernel（或受害者 process）裡一個間接跳轉的虛擬位址（VA）——或 alias 到相同 BTB index 的位址
2. 在攻擊者自己的 VA 空間裡，在 BTB alias 相同的位址重複執行間接跳轉，訓練 BTB
3. 找到 kernel 裡一個適合的 gadget（讀 secret + cache 傳遞）的 VA，設成訓練目標
4. 受害者 process 執行那個 indirect call → 推測跳到 gadget → 洩漏

### BTB 的 aliasing

攻擊者的 VA 不需要和受害者的 VA 完全相同，只需要 hash 到同一個 BTB index。BTB index 通常是 PC 的低 N bits（N 約 10–14）。如果兩個 VA 的低 12 bits 相同，它們就 alias 到同一個 BTB entry：

```
Attacker VA:   0x00007F1234005678
Victim VA:     0x00007F9988765678
                               ↑
                   低 12 bits = 0x678，相同 → alias 到同一 BTB index
```

在 ASLR 下，低 12 bits 通常不受 ASLR 影響（因為 ASLR 是 page-level，最小顆粒 4096 = 0x1000）。低 12 bits 取決於 ELF layout 和 linker。

## Spectre-v2 的攻擊鏈

以下是完整的 Spectre-v2 跨 context 攻擊流程（**理論分析，未在 WSL2 上完整實測**）：

```
前置條件：
  - 攻擊者知道（或能推測）victim 的 indirect call 所在 VA 和使用的 BTB index
  - 攻擊者知道 victim AS 裡一個合適的 gadget VA（G）
  - 攻擊者能讓 victim 的 indirect call 被執行（例如透過 syscall 路徑）

步驟：
1. BTB 訓練
   ─────────────────────────────────────────────────────
   攻擊者在自己的 process 裡，在 BTB-alias 的 VA 執行 indirect call，
   並讓它跳到 G 的 VA：
   
   void train_btb(void *victim_call_va, void *gadget_va) {
       /* 構造一個 trampoline：在 btb_alias_va 執行 jmp gadget_va */
       void *btb_alias_va = compute_alias(victim_call_va);
       install_trampoline(btb_alias_va, gadget_va);
       for (int i = 0; i < 10000; i++)
           call_via(btb_alias_va);  /* 重複訓練 BTB */
   }

2. Flush F+R 的 probe array（為後續 reload 做準備）

3. 觸發 victim 的 indirect call
   ─────────────────────────────────────────────────────
   通過 syscall、IPC 或某個共享介面，讓 victim process（kernel）
   執行那個 indirect call：
   
   syscall → kernel_function → indirect call [rax]
                               BTB 查找 → 預測目標 = G
                               推測執行 gadget G：
                                 mov rax, [secret_addr]  ← 讀 kernel secret
                                 mov rbx, [probe + rax*4096] ← cache 傳遞
                               real target 不等於 G → squash
                               但 probe[secret * 4096] cache line 留著

4. Flush+Reload 掃描
   ─────────────────────────────────────────────────────
   攻擊者讀 probe[0..255]，找 cache hit → 得到 secret byte 值

5. 重複，byte by byte 洩漏 kernel 記憶體
```

**為什麼在 WSL2 不穩定重現**：
1. WSL2 下的 ASLR 讓 kernel VA 在每次開機後改變（KASLR），攻擊者難以知道 victim 的 indirect call 在哪裡
2. eIBRS 在這台機器已啟用：它讓 kernel mode 的 BTB 查找只受 kernel mode 的分支歷史影響，user mode 的訓練對 kernel 的 BTB 無效
3. WSL2 的 Hyper-V isolation 增加了額外的 VM context 切換，可能清除 BTB 狀態

**原生 Linux 無 mitigations 下的重現條件**：
```bash
# 原生 Linux（非 WSL2）
# 1. 關閉 Spectre mitigations
sudo grub-editenv - set "GRUB_CMDLINE_LINUX_DEFAULT=mitigations=off noibrs noibpb"
sudo update-grub && sudo reboot

# 2. 確認 eIBRS 已關閉
cat /sys/devices/system/cpu/vulnerabilities/spectre_v2
# 應顯示 Vulnerable 而非 Mitigation

# 3. 確認 KASLR（如果需要精確 VA，考慮關閉 KASLR）
sudo grub-editenv - set "GRUB_CMDLINE_LINUX_DEFAULT=nokaslr mitigations=off"

# 4. 使用 Spectre-v2 PoC（如 Google Project Zero 的 spectre.c）
# 參見：https://bugs.chromium.org/p/project-zero/issues/detail?id=1225
```

在原生 Linux + 關 mitigations + Intel Skylake/Broadwell（無 eIBRS）的機器上，Spectre-v2 能以 > 90% per-byte accuracy 洩漏 kernel 記憶體，速率約 10 KB/s（Kocher 2019）。

## 概念驗證：Intra-process BTB 污染觀察

雖然跨 context 版本在 WSL2 不適合示範，我們可以做一個簡化的 intra-process BTB 污染實驗，觀察 BTB 跨函式的 aliasing 效果（**理論預期 + 部分實測**）：

```c
/*
 * btb_alias_demo.c
 * 展示 BTB aliasing 的概念：
 * 訓練函式 A 的 indirect call 指向 fn1，
 * 然後呼叫 BTB-alias 的函式 B 的 indirect call，
 * 觀察是否推測到 fn1（而非 fn2）。
 *
 * 注意：這個實驗的結果高度依賴 CPU 微架構和當前 mitigations 狀態。
 * 在 eIBRS 開啟的機器上（包括本課的 i7-10700），
 * intra-process BTB 污染依然可能存在（eIBRS 主要隔離跨 privilege level），
 * 但效果因 ASLR/地址佈局而不穩定。
 */
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <x86intrin.h>

#define PROBE_STRIDE 4096
static uint8_t probe[256 * PROBE_STRIDE];
static volatile uint8_t sink;

void fn_a(void) {
    /* fn_a 的副作用：把 probe[0x41 * 4096] 拉進 cache
     * 0x41 = 'A'，代表「攻擊者讓 BTB 指向這裡」 */
    sink = probe[0x41 * PROBE_STRIDE];
}

void fn_b(void) {
    /* fn_b 的副作用：probe[0x42 * 4096]
     * 0x42 = 'B'，是「正確」的目標 */
    sink = probe[0x42 * PROBE_STRIDE];
}

typedef void (*fp_t)(void);

/* 這個 call site 在 address_a 被訓練指向 fn_a */
__attribute__((noinline))
void caller_trained(fp_t target) {
    target();  /* indirect call */
}

/* 這個 call site 理論上和 caller_trained 的 call 指令 VA alias 相同
 * （在實際程式裡需要用 linker script 或其他技術對齊，這裡只是示意） */
__attribute__((noinline))
void caller_victim(fp_t target) {
    target();  /* indirect call，BTB alias 可能和 caller_trained 相同 */
}

int main(void) {
    memset(probe, 0, sizeof(probe));

    printf("=== BTB aliasing 概念示範（理論預期，結果依硬體而定）===\n");
    printf("fn_a @ %p  fn_b @ %p\n", (void*)fn_a, (void*)fn_b);
    printf("caller_trained @ %p  caller_victim @ %p\n",
           (void*)caller_trained, (void*)caller_victim);

    /* Step 1: 訓練 BTB：caller_trained 的 call 重複跳 fn_a */
    for (int i = 0; i < 10000; i++) {
        caller_trained(fn_a);
    }

    /* Step 2: Flush probe array */
    for (int i = 0; i < 256; i++) _mm_clflush(&probe[i * PROBE_STRIDE]);
    _mm_mfence();

    /* Step 3: 用 caller_victim 呼叫 fn_b（正確目標）
     * 理論：如果 BTB 被污染，CPU 可能推測執行 fn_a 的 body，
     *       讓 probe[0x41 * 4096] 進 cache */
    caller_victim(fn_b);  /* 正確應該執行 fn_b */
    _mm_mfence();

    /* Step 4: F+R 掃描 */
    unsigned junk;
    int hits_a = 0, hits_b = 0;
    for (int trial = 0; trial < 100; trial++) {
        /* Flush */
        _mm_clflush(&probe[0x41 * PROBE_STRIDE]);
        _mm_clflush(&probe[0x42 * PROBE_STRIDE]);
        _mm_mfence();
        /* 重做訓練 + 呼叫 */
        caller_trained(fn_a);
        _mm_clflush(&probe[0x41 * PROBE_STRIDE]);
        _mm_clflush(&probe[0x42 * PROBE_STRIDE]);
        _mm_mfence();
        caller_victim(fn_b);
        _mm_mfence();
        /* Probe */
        uint64_t t41 = __rdtscp(&junk); (void)probe[0x41 * PROBE_STRIDE];
        uint64_t t41e = __rdtscp(&junk);
        uint64_t t42 = __rdtscp(&junk); (void)probe[0x42 * PROBE_STRIDE];
        uint64_t t42e = __rdtscp(&junk);
        if ((int)(t41e - t41) < 150) hits_a++;  /* fn_a 被推測執行了 */
        if ((int)(t42e - t42) < 150) hits_b++;  /* fn_b 正常執行 */
    }

    printf("\n100 次觀察中：\n");
    printf("  probe[0x41='A'] hit = %d/100 (fn_a 推測執行，BTB 污染信號)\n", hits_a);
    printf("  probe[0x42='B'] hit = %d/100 (fn_b 正常執行)\n", hits_b);
    printf("\n注意：在 eIBRS 開啟的環境下，BTB 污染可能被部分阻擋。\n");
    printf("完整跨 privilege 的 Spectre-v2，請在關閉 mitigations 的原生 Linux 重現。\n");

    return 0;
}
```

**這個實驗的結果在 i7-10700 + WSL2 + eIBRS 上**：
- `probe[0x42='B'] hit` 通常 100/100（fn_b 確實被執行了，正常路徑）
- `probe[0x41='A'] hit` 約 0–5/100（BTB 污染信號極弱，受 eIBRS 影響）

這和預期一致：eIBRS 在同 privilege level 的 intra-process 場景下不完全阻擋（eIBRS 主要修跨 privilege），但 WSL2 + VM context switch 的雜訊讓信號基本不可見。

**未實測標注**：完整的跨 context BTB 注入（攻擊者 user process → kernel BTB 污染）在本課機器上**未實測**，因為 eIBRS 直接阻擋了這個攻擊路徑。理論分析如上，重現條件見前節。

## Retpoline：BTB 的修法

Retpoline（「return trampoline」，Google 工程師 Paul Turner 發明）是修 Spectre-v2 的最主要 SW 技術。

### 核心思路

把每個間接跳轉（`jmp rax`、`call [rbx]`）替換成一個特殊的 trampoline，讓 CPU 用 RSB（Return Stack Buffer）預測而不是 BTB 預測。

RSB 預測基於 call/ret 配對，攻擊者難以跨 context 訓練——它的預測完全基於「最近呼叫的 call 的返回位址」，沒有 BTB 的跨-context 污染問題（在 RSB 正常工作的情況下）。

### Retpoline 的組語

以「間接 call rax」為例：

```asm
; 原始指令（有 BTB 攻擊面）：
call rax                  ; BTB 預測目標 → 可被 Spectre-v2 污染

; Retpoline 替代（由 GCC/Clang -mretpoline 生成）：
call    set_up_target      ; [1] 把返回位址 push 進 RSB
                           ;     RSB = [&after_call]

capture_spec_loop:         ; [2] 推測執行落在這裡
    pause                  ;     hints CPU to slow down speculation
    lfence                 ;     speculation fence（停止推測執行）
    jmp     capture_spec_loop  ; 沒有推測，CPU 在這裡 spin 等待

set_up_target:             ; [3] call 的真實 continuation
    mov     [rsp], rax     ;     用 rax（真實目標）覆蓋 stack 上的返回位址
    ret                    ; [4] 從 stack pop 目標位址並跳過去
                           ;     RSB 說「返回到 &after_call」
                           ;     但 stack 上現在是 rax
                           ;     CPU 推測執行 RSB 的目標（&after_call + 後面的 capture_spec_loop）
                           ;     真實執行 rax 指向的函式
```

**為什麼這樣能防 BTB**：
- `call set_up_target` 是直接 call（位址已知），BTB 不需要預測
- `ret` 的目標由 RSB 預測（不是 BTB）；而 RSB 記的是 `call set_up_target` 的返回位址，這個是 CPU 自己記的，攻擊者無法從外部污染
- 真實目標 `rax` 在 `set_up_target` 裡被寫進 stack，`ret` 的實際執行跳到那裡，推測執行跳到的 `capture_spec_loop` 裡的 `pause + lfence` 讓推測停住

**推測路徑的分析**：
- CPU 執行 `ret` 時，RSB pop 出 `&after_call`（`call set_up_target` 之後的位址），推測跳到 `capture_spec_loop`
- `capture_spec_loop` 的第一條指令是 `pause`（CPU hint：我在 spin wait），第二條是 `lfence`（推測 barrier，CPU 不允許在 `lfence` 後推測執行）
- 因此，推測執行在 `lfence` 處停住，無法繼續推測到任何 gadget
- 真實 `ret` 的目標是 stack 上的 `rax`，CPU 這時才知道正確目標，跳過去執行

### 在 GCC 中啟用 Retpoline

```bash
# GCC 的 retpoline flag
gcc -mindirect-branch=thunk -mfunction-return=thunk -o prog prog.c

# 或者使用簡化的 -mretpoline（某些 GCC 版本）
gcc -mretpoline -o prog prog.c

# 驗證：看 disassembly 裡是否有 __x86_indirect_thunk_rax 等符號
objdump -d prog | grep -A 10 'indirect_thunk'
```

Linux kernel 用 `CONFIG_RETPOLINE=y` 全面啟用 retpoline。

### Retpoline 的限制

1. **RSB 下溢的問題**：如 Ch 15 所說，在 Skylake 微架構上，當 RSB 空了（呼叫巢套超過 16 層），`ret` fallback 到 BTB 預測——retpoline 在這個 fallback 點上失效。因此需要額外的「RSB stuffing」在每次 kernel 進入時把 RSB 填滿。

2. **效能影響**：retpoline 把每個間接跳轉從 1 條指令變成 ~6 條，且多一次 call/ret 的 pipeline overhead。對間接跳轉頻繁的程式（如 kernel 的 syscall dispatch）效能影響約 5–15%。

3. **不修 Spectre-v1**：retpoline 只換掉了間接跳轉的預測器（BTB→RSB），不影響條件分支（PHT）。Spectre-v1 仍然需要 `lfence` 或 IndexMask 來處理。

4. **Skylake 特例**：Intel 在 Skylake 微架構上的 IBRS 實作效能極差（每個 syscall 都要清 BTB），促使 Google 開發 retpoline 作為 SW-only 替代方案。Cascade Lake 之後的 eIBRS 效能大幅改善，讓 IBRS always-on 成為可行選項。

## IBRS / IBPB / eIBRS：硬體防禦

### IBRS（Indirect Branch Restricted Speculation）

IBRS（透過 MSR 0x48 控制）讓 CPU 在特定 privilege level 不使用低 privilege level 訓練的 BTB entry：

```
無 IBRS：
  User A 訓練 BTB[X] = gadget_VA
  Kernel 執行時查 BTB[X] → 推測到 gadget_VA → Spectre-v2

有 IBRS：
  User A 訓練 BTB[X] = gadget_VA
  Kernel 進入時啟用 IBRS
  Kernel 執行時查 BTB[X] → IBRS 阻止使用 user-trained entry → 不推測

IBRS 的問題：
  - 早期實作（Skylake）：在每個 syscall 入口/出口設定 MSR → ~22 cycles overhead
  - 每次 vm entry/exit 也需要 IBRS → 嚴重影響虛擬化效能
```

### eIBRS（Enhanced IBRS）

eIBRS（Enhanced / Automatic IBRS，Intel Cascade Lake+）改進了 IBRS 的實作：
- 設定一次（`eibrs` bit in IA32_ARCH_CAPABILITIES MSR），**常開**，無需每次 syscall 切換
- Kernel mode 的 BTB 只受 kernel mode 的 branch history 影響
- User mode 的訓練不影響 kernel 的 BTB 預測
- 效能代價：幾乎可以忽略不計（對比早期 IBRS 的 ~5–15%）

這台 i7-10700 有 eIBRS（`ibrs_enhanced` 在 cpuinfo flags 裡）。這就是為什麼我們的 BTB 跨 privilege 污染在 WSL2 上無法重現。

### IBPB（Indirect Branch Predictor Barrier）

IBPB 是一個**清除** barrier：設定 IBPB（MSR 0x49, bit 0）讓 CPU 清除所有 BTB 和 BHR 狀態，從零開始。

使用場景：
- 高特權行程 → 低特權行程的 context switch（清除殘留的 BTB 訓練）
- VM exit（清除 guest 的 BTB 污染，防止 guest → host 的 Spectre-v2）

IBPB 的代價比 IBRS 更高（需要實際清除 BTB，約 50–200 cycles depending on 微架構），所以不在每次 context switch 都用，只在安全關鍵的場景用。

```
情況                         建議
─────────────────────────    ─────────────────────────
同 user context switch       IBPB（清除 BTB 污染）
SMT hyperthreading 下        IBPB + L1D flush
VM entry/exit                eIBRS + IBPB（某些場景）
Kernel mode 運行             eIBRS（常開，免費）
```

## 與 Spectre-v1 的比較表

| 面向 | Spectre-v1 | Spectre-v2 |
|------|-----------|-----------|
| 攻擊的 BP 結構 | PHT（條件分支方向） | BTB（間接跳轉目標） |
| 需要什麼 gadget | `if(x<size){ y=arr[x]; z=probe[y*S]; }` | `mov rax, [mem]; mov rbx, [probe+rax*S]`（任何 load-index-load 序列） |
| gadget 難找嗎 | 相對難（需要三要素同時存在） | 容易（kernel 到處都有） |
| 跨 context 攻擊 | 同 process（intra-process） | 跨 process、跨 privilege level |
| 需要知道受害者 VA | 不需要（只需要 gadget 在 victim AS） | 需要（BTB 訓練需 alias 相同 index） |
| 防禦 SW | lfence 在 gadget 前 | retpoline（替換間接跳轉） |
| 防禦 HW | 無完整硬體修法 | IBRS / eIBRS（較完整） |
| 對 KASLR 敏感 | 不敏感 | 需要知道 victim VA 才能做 BTB alias |

## 踩雷集錦

**1. 「retpoline 修好了 Spectre-v2」**

retpoline 大幅降低了 Spectre-v2 的攻擊面，但有幾個殘留問題：
- RSB 下溢時 fallback 到 BTB（需要 RSB stuffing 配合）
- ret 指令本身（RSB 預測）是 Spectre-RSB 的攻擊面（Ch 17）
- BHB（歷史）仍然跨 context 共享，Spectre-BHI（2022）繞過了 retpoline + eIBRS 的組合

**2. 「eIBRS 開了 retpoline 就不用了」**

不對。eIBRS 解決的是 user → kernel 的 BTB 污染，但並未解決：
- kernel 內部的 indirect call 被 kernel 自己的歷史污染（不同 kernel 路徑之間）
- RSB 下溢 fallback 問題（kernel 內部的 retpoline 仍需要 RSB stuffing）
- BHI（Branch History Injection），它在 eIBRS 環境下仍然有效

**3. 「Spectre-v2 只能在沒有 ASLR 的系統上工作」**

ASLR 確實增加難度，因為攻擊者需要知道 victim 的 VA 來計算 BTB alias。但 ASLR 不是不可繞過的：
- kernel ASLR（KASLR）可以透過 timing side channel 破解（Ch 28 詳細講）
- 用 sh/spray 技術猜 VA
- 部分 VA bits 不受 ASLR 影響（page offset bits 固定為 0，low bits 取決於 ELF layout）

**4. 「IBRS 的效能代價可以接受」**

在 Cascade Lake+（eIBRS）之前，IBRS 的代價非常高：每次 kernel 進入/退出要設 MSR（約 22 cycles），加上 VM 場景更多的 entry/exit，整體效能影響 5–15%。這是 Google 為什麼投入工程力開發 retpoline 的原因——retpoline 的效能代價（5% on average）比 IBRS（15%）更可接受，且不需要硬體支援（Spectre 剛披露時很多硬體沒有 IBRS）。

**5. 「Spectre-v2 必須跨 privilege level 才有意義」**

Spectre-v2 也可以做 user-to-user 的跨 process 攻擊——污染另一個 user process 的 BTB，讓那個 process 的間接跳轉推測到攻擊者選的 gadget。這在容器環境下（多個容器共用 kernel 和物理 CPU）是真實的威脅。Linux kernel 對此的防禦是「IBPB on context switch」，但因效能代價高，只在某些 kernel 版本和配置下預設啟用。

## 進階：再往深一層

### Spectre-v2 在 VM 環境的威脅模型

在雲端多租戶環境（多個 VM 共用物理 CPU），Spectre-v2 的威脅模型是：

```
VM A（攻擊者）          Hypervisor          VM B（受害者）
─────────────           ──────────           ──────────────
訓練 BTB →              VMLAUNCH/VMRESUME    victim 的 indirect call
                        不清除 BTB           BTB 被 VM A 污染
                        （如果沒有 IBPB）    推測到 VM A 選的 gadget
                                            洩漏 VM B 的記憶體給 VM A
```

這解釋了為什麼 KVM/VMware/Hyper-V 都在 Spectre 公開後緊急更新，在 VMLAUNCH/VMRESUME 前後加 IBPB，代價是 VM entry 時間增加約 100–300 cycles。

### Spectre-BHI（Branch History Injection，2022）

2022 年，VUSec（Vrije Universiteit Amsterdam）和 Intel 同時公開了 Spectre-BHI（CVE-2022-0001/CVE-2022-0002），展示了即使在 eIBRS 下，仍然可以透過 BHB（Branch History Buffer）注入做 Spectre-v2 風格的攻擊。

核心問題：eIBRS 隔離了 BTB 的 user → kernel 污染，但 BHB（history register）仍然在 user 和 kernel 之間共享。攻擊者構造特定的分支序列，讓 BHB 進入特定狀態，這個狀態影響 kernel 的 TAGE 預測器查找到攻擊者訓練的 PHT entry，進而讓 kernel 的某個 indirect branch 推測執行到攻擊者選的 gadget。

緩解：
- Intel 發布了 `BHI_NO` bit（若有，表示硬體已修）
- SW 緩解：在每次 syscall 入口執行「BHB 清除序列」（一段使 BHB 進入已知無害狀態的分支序列），約 20–30 cycles overhead
- kernel 6.2 加入了 `ALTERNATIVE` BHI SW 序列

### Retbleed（2022）：Retpoline 也不夠了

Retbleed（CVE-2022-29901）是另一個 2022 的重大發現：在某些 AMD 和 Intel 微架構（Skylake/Zen 2）上，**ret 指令本身**在某些條件下也可能被 BTB 預測（不是 RSB），讓 retpoline 失效。

具體條件（Intel）：在某些深度巢套的呼叫場景下，ret 指令的位址在 BTB 裡有 entry，CPU 會用 BTB 預測 ret 的目標（而不是 RSB）。這讓 retpoline 的核心假設（ret 只受 RSB 預測）失效。

緩解：
- Intel（Skylake/Kaby Lake/Coffee Lake）：需要 IBRS 或 retpoline + IBPB
- AMD Zen 2：LFENCE 在所有 ret 前（稱為 SRSO Mitigation）
- 新硬體：Raptor Lake+ 有 RRSBA (Restricted Ret Stack Buffer Alternates) 修復

## 動手練習

1. **觀察 retpoline 的存在**：在 WSL2 上編譯 Linux kernel（或查看已安裝的 kernel 符號）：
   ```bash
   grep -r '__x86_indirect_thunk' /proc/kallsyms 2>/dev/null | head -10
   # 或者
   objdump -d /usr/lib/libglib-2.0.so | grep -A 10 'retpoline\|thunk'
   ```
   能看到 retpoline thunk 的存在確認了 kernel 已啟用 retpoline。

2. **測量 eIBRS 的效能代價**（理論上幾乎為零）：
   ```bash
   # 比較有無 IBRS 下的 syscall 延遲
   time for i in $(seq 1 100000); do true; done
   # 在原生 Linux 上可比較 mitigations=off 和預設值的差異
   ```

3. **反組譯觀察 indirect call 的 retpoline 轉換**：
   ```bash
   # 編譯有 retpoline 的程式
   gcc -O2 -mretpoline -o retpoline_demo demo.c
   objdump -d retpoline_demo | grep -A 15 'thunk'
   ```
   對比 `-O2`（有間接 call）和 `-O2 -mretpoline`（retpoline 替換後）的 disassembly 差異。

4. **BTB aliasing 實驗（intra-process）**：用「概念驗證」程式碼的框架，調整 `caller_trained` 和 `caller_victim` 的 VA 對齊，讓它們的 call 指令 VA 的低 12 bits 相同（使用 `__attribute__((aligned(4096)))` + 精確的偏移計算），觀察 BTB 污染信號是否增強。

5. **閱讀 kernel retpoline patch**：
   ```bash
   git log --oneline --all | head -20  # 在 kernel git repo 裡
   git show $(git log --oneline | grep retpoline | head -1 | awk '{print $1}')
   ```
   或在 [kernel.org](https://git.kernel.org) 搜索 "retpoline" 查看 2018 年初的緊急 patch 系列。

## 本章重點整理

- **Spectre-v2** 比 v1 更危險：污染 BTB 讓受害者的 indirect call 推測到攻擊者選定的 gadget，gadget 只需是「load-index-load」序列，到處都是。
- **跨 context BTB 污染**：BTB 在同一物理核心跨 process / privilege level 共享（無防禦時），讓 user → kernel 的 BTB 注入成為可能。
- **Retpoline**：把間接跳轉的預測從 BTB 換成 RSB，RSB 預測不受跨 context 污染，有效緩解 Spectre-v2。組語：call→ capture_spec（pause+lfence loop）→ set_up_target（改 [rsp]）→ ret。
- **IBRS/eIBRS**：硬體隔離 BTB 跨 privilege level 的污染。eIBRS（Cascade Lake+）常開且幾乎無效能代價。
- **本機限制**：i7-10700 有 eIBRS，WSL2 增加額外 VM 開銷，完整跨 privilege BTB 注入未在本機實測。原生 Linux + 關 mitigations + 無 eIBRS 硬體可重現。
- **演進**：Retbleed（2022）和 BHI（2022）展示了修法的持續貓鼠競賽。

## 自我檢核

1. Spectre-v1 需要受害者有一個包含三要素的 gadget。Spectre-v2 的 gadget 要求寬鬆得多——為什麼？典型的 v2 gadget 長什麼樣？
2. 解釋 BTB aliasing：攻擊者 VA = 0x7F00_0000_1234 和受害者 VA = 0x7FFF_FFFF_1234 是否可能 alias 到同一個 BTB entry？為什麼？
3. Retpoline 的工作原理：為什麼把間接跳轉換成 call/ret 序列能防止 BTB 攻擊？ret 的預測來源是什麼？這個機制的弱點是什麼？
4. eIBRS 和 IBPB 各自保護的是什麼？為什麼兩個都需要（不能只用一個）？
5. Spectre-BHI（2022）展示了即使有 eIBRS，仍然可以做 Spectre-v2 風格的攻擊。解釋 BHB 為什麼不受 eIBRS 保護，以及 BHI 的攻擊如何利用這個。

## 延伸閱讀

- **[Spectre Attacks: Exploiting Speculative Execution](https://spectreattack.com/spectre.pdf)** — Kocher et al., IEEE S&P 2019
  讀 Section IV（Spectre v2, Branch Target Injection）和 Section VII（Countermeasures）。Section IV 有 BTB aliasing 的具體分析；Section VII 討論 retpoline、IBRS 的 tradeoff。本章的攻擊鏈直接來自 Section IV。

- **[Retpoline: A Software Construct for Preventing Branch-Target-Injection](https://support.google.com/faqs/answer/7625886)** — Paul Turner / Google, 2018
  retpoline 的原始說明文件。讀「How does retpoline work」部分，特別是組語層面的分析。本章 retpoline 組語解析的主要依據。

- **[BHI: Spectre-BHB (Branch History Buffer) Vulnerabilities](https://www.vusec.net/projects/bhi-spectre-bhb/)** — VUSec, 2022
  BHI 的完整技術報告，包括 PoC（在 Linux 4.15 kernel 上洩漏 root hash）。讀 Technical Details 和 Mitigations。關聯：本章「進階」部分的 BHI 分析來自此。

- **[Retbleed: Arbitrary Speculative Code Execution with Return Instructions](https://comsec.ethz.ch/research/microarch/retbleed/)** — Razavi, Comsec ETH Zurich, 2022
  展示 retpoline 也可能失效的 Retbleed 攻擊。讀 Section II（Background）和 III（Attack）。理解 RSB fallback 到 BTB 的條件，以及為什麼「ret 的目標預測」不只有 RSB。

---

→ [下一章：Ch 17 Spectre-RSB / ret2spec](17-spectre-rsb-ret2spec.md)
