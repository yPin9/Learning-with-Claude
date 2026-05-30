# Ch 39 — Breakpoint / single-step 底層實作

> **目標**：揭開 breakpoint 與 single-step 的最底層——軟體斷點怎麼 patch INT3（`0xCC`）、命中後怎麼還原、硬體斷點怎麼用 debug register、single-step 怎麼做、displaced stepping 解決什麼問題。學完你完全理解 Ch 4/5/13 的黑盒，並具備 Ch 41 自寫 debugger 的核心知識。

> **環境**：GDB 13/14，Linux x86_64。機制 x86-64 專屬（其他架構概念類似、指令不同）。

## 為什麼要看到最底

Ch 4 你學會下斷點、Ch 5 學會 single-step、Ch 13 學會 watchpoint——但都把「怎麼做到」當黑盒。這章打開最後的黑盒。理解它你會懂：

- 為什麼斷點能斷在 flash/ROM 要用硬體斷點
- 為什麼硬體 watchpoint 只有 4 個
- 為什麼 self-modifying code / JIT 的斷點很麻煩
- 為什麼 `stepi` 在斷點上要特殊處理
- **怎麼自己實作斷點**（Ch 41）

這是 ptrace（Ch 2）+ DWARF（Ch 38）之外，自寫 debugger 的第三塊基石。

## 軟體斷點：patch INT3

最常見的斷點，機制簡單到優雅：**把目標位址的第一個 byte 換成 `0xCC`（INT3 指令）。**

```
   下斷點前：              下斷點後：
   0x1149: 55 48 89 e5     0x1149: CC 48 89 e5
           push %rbp               int3   ← 原本的 0x55 被存起來，換成 0xCC
                                   (其餘 byte 不動)
```

INT3（`0xCC`）是一個單 byte 的特殊指令——CPU 執行到它就觸發 `SIGTRAP`，被 OS 凍結、通知 tracer（Ch 2 的機制）。完整流程：

```
   下斷點 (GDB):
   1. PTRACE_PEEKTEXT 讀 0x1149 的原始 byte（0x55），存起來
   2. PTRACE_POKETEXT 把 0x1149 寫成 0xCC（保留其餘 byte）

   命中:
   3. CPU 執行到 0xCC → SIGTRAP → OS 凍結 inferior → 通知 GDB
   4. 此時 $pc 已經 = 0x114a（INT3 是 1 byte，CPU 執行完它 pc+1）
   5. GDB 偵測到停在斷點

   還原與繼續:
   6. GDB 把 $pc 退回 0x1149（減 1，回到斷點位址）
   7. PTRACE_POKETEXT 把 0xCC 換回原始的 0x55
   8. 給你控制權（你看到「停在斷點」）
   9. 你 continue 時：先 single-step 一步（執行原始的 0x55）
   10. 再把 0xCC patch 回去（斷點還要生效）
   11. PTRACE_CONT 繼續
```

關鍵細節：

- **$pc 要退 1**：INT3 執行後 pc 指向下一個 byte，GDB 退回斷點位址（步驟 6）。Ch 2 的 mini tracer 沒做這個，Ch 41 要做。
- **continue 時的「跨過自己的斷點」**：要先還原原始指令、single-step 一步、再 patch 回 INT3（步驟 9-11）——否則要嘛斷點失效、要嘛無限觸發。這是斷點實作最 tricky 的部分。

## 親眼看 INT3

```c
// bp_demo.c — gcc -g -O0 bp_demo.c -o bp_demo
int target(void){ return 42; }
int main(void){ return target(); }
```

```
(gdb) break target
(gdb) x/4xb target            # 還沒 run，看原始 byte
0x1149 <target>: 0x55 0x48 0x89 0xe5
(gdb) run
(gdb) x/4xb target            # run 後（斷點實體化），第一個 byte 變了？
```

> 微妙：GDB 很聰明——當你 `x/i` 或 `disassemble` 一個下了斷點的位址，它會**顯示原始指令**（自動把 0xCC 換回原本的給你看），所以你不會看到 0xCC。要真的看到 0xCC 得用其他 tracer 或在 GDB 內部繞過。這個「對使用者隱藏斷點 byte」是 GDB 的貼心設計，但理解底層真的是 0xCC 很重要（Ch 41 你自己實作時沒這層美化）。

## 硬體斷點：debug register

軟體斷點要 patch 記憶體——但有些記憶體**不能寫**（flash、ROM、唯讀程式碼段）。這時用**硬體斷點**：CPU 的 debug register。

x86 有 4 個 debug address register（**DR0–DR3**）+ 控制（DR7）+ 狀態（DR6）：

```
(gdb) hbreak target           # 硬體斷點
(gdb) info registers $dr0 $dr7
```

機制：

- 把目標位址寫進 DR0（~DR3 之一）。
- 設定 DR7 啟用它、設模式（執行/讀/寫）。
- CPU 硬體層比對——PC 到該位址時自己觸發例外，**不需 patch 記憶體**。

所以硬體斷點能斷在唯讀記憶體、不改變程式 byte（self-modifying code / 防偵測場景關鍵）。代價：**只有 4 個**（DR0-3），且和硬體 watchpoint 共用這 4 個（Ch 13）。

## 硬體 watchpoint：同一批 register

Ch 13 的硬體 watchpoint 用同樣的 DR0-3，但設成「監看資料存取」而非「執行」：

```
   DR0-3: 監看的位址
   DR7:   每個 DR 的設定
          - 模式：執行(00) / 寫(01) / 讀寫(11)
          - 長度：1/2/4/8 byte
```

這解釋了 Ch 13 的兩個限制：

- **只有 4 個**：4 個 DR register。hbreak + 硬體 watch 加起來最多 4。
- **1/2/4/8 byte 對齊**：DR 的長度欄位只支援這些。監看更大範圍就退回軟體 watchpoint（single-step 比對，超慢）。

`info registers $dr6` 看哪個 DR 觸發了（狀態），`$dr7` 看設定——Ch 13 進階提過，現在你懂它們是什麼了。

## Single-step：PTRACE_SINGLESTEP / TF flag

Ch 5 的 `stepi` 怎麼做到「執行一條指令就停」？

**方法一：`PTRACE_SINGLESTEP`**（Ch 2 用過）——ptrace 直接支援，OS 設定 CPU 的 **TF（Trap Flag）**，CPU 執行一條指令後自動觸發 SIGTRAP。

```
   PTRACE_SINGLESTEP:
   1. OS 設 EFLAGS 的 TF（trap flag）= 1
   2. CPU 執行一條指令
   3. TF=1 → 執行後立刻觸發 SIGTRAP
   4. OS 凍結、通知 tracer
```

Ch 5 講的 source-level `step` 就是「反覆 PTRACE_SINGLESTEP + 查 line table（Ch 38）比對 PC 範圍」——直到 PC 走到下一行。現在你看到完整鏈：DWARF line table（Ch 38）+ single-step（這章）= `step`。

## Displaced stepping：跨過斷點的難題

承前面「continue 時要跨過自己的斷點」（步驟 9-11）。問題：要 single-step 執行原始指令，得先把 0xCC 換回原指令——但這瞬間斷點是「關閉」的，如果另一個 thread 剛好執行到這（多執行緒），就漏掉斷點了。

**displaced stepping**（out-of-line stepping）解法：不在原地還原執行，而是**把原始指令複製到別處執行**：

```
   原地還原（有 race 風險）:       displaced stepping:
   1. 0x1149 換回 0x55             1. 把原始指令複製到一個 scratch 區
   2. single-step（執行 0x55）     2. 在 scratch 區 single-step 執行
   3. 換回 0xCC                    3. 調整結果（pc 等），回到原流程
   ↑ 步驟 2 期間斷點是關的         ↑ 原位址的 0xCC 一直在，不關閉
```

displaced stepping 讓「跨過斷點」期間原位址的 INT3 不用移除，避免多執行緒漏斷點。GDB 預設對支援的架構用它（`set displaced-stepping on`）。這是 Ch 41 簡易 debugger 不會做（太複雜），但理解它解釋了真實 debugger 的精巧。

## 為什麼這些細節重要

| 現象（前面章節） | 底層原因（這章） |
|---|---|
| 斷點能斷在唯讀記憶體要用 hbreak（Ch 4） | 軟體斷點要 patch（POKETEXT），唯讀不能寫 |
| 硬體 watchpoint 只有 4 個（Ch 13） | 只有 DR0-3 四個 register |
| 硬體 watch 限 1/2/4/8 byte（Ch 13） | DR7 長度欄位限制 |
| self-modifying code 斷點怪 | 程式改了自己的 byte，和 INT3 patch 衝突 |
| `stepi` 在斷點上要特殊處理 | 要先還原指令才能正確執行（步驟 9-11） |

## 踩雷集錦

1. **以為 `x/i` 看到的是真實 byte**：GDB 對下了斷點的位址顯示原始指令（隱藏 0xCC）。底層真的是 0xCC。
2. **自己寫 debugger 忘了 $pc 退 1**：INT3 後 pc 多 1，不退回斷點位址就錯亂（Ch 41 必做）。
3. **忘了 continue 時要跨過自己的斷點**：不還原原指令就 single-step，會執行到 0xCC（無限觸發）；不 patch 回去，斷點失效。
4. **硬體斷點超過 4 個**：DR0-3 用完，第 5 個報錯。hbreak + 硬體 watch 共用。
5. **self-modifying code / JIT**：程式自己改 byte，可能覆蓋你的 INT3，或你的 INT3 改了它的邏輯。JIT debug 要特殊處理。
6. **多執行緒漏斷點**：原地還原的 race window。GDB 用 displaced stepping 解，自寫的簡易 debugger 在多執行緒會有這問題。

## 進階：再往深一層

- **2-byte / 多 byte 斷點**：某些架構的斷點指令不只 1 byte。x86 的 INT3 是 1 byte（`0xCC`）很方便；ARM Thumb/RISC-V 的斷點指令不同寬度。
- **`set breakpoint auto-hw`**：GDB 自動對唯讀記憶體改用硬體斷點。
- **range stepping**：`PTRACE_SINGLESTEP` 一條條太慢，較新的機制能「single-step 直到離開某位址範圍」，GDB 的 `step` 可加速。
- **breakpoint 與 instruction cache**：patch 記憶體後，CPU 的 i-cache 可能還有舊指令。GDB/OS 要處理 cache 一致性（某些架構需顯式 flush）。
- **debug register 的 per-thread 性**：DR0-3 是 per-CPU/per-thread context 的一部分，GDB 對多執行緒要在每個 thread 設（Ch 13 提過）。
- **kernel 的 INT3**：kprobe（kernel probe）用同樣的 INT3 patch 技術 debug kernel——呼應 bpf/kernel 課程。
- **反偵測**：惡意程式檢查自己的 byte 有沒有 0xCC（偵測軟體斷點）、或檢查 DR register（偵測硬體斷點）——anti-debugging（呼應 malware_analysis 課程）。

## 動手練習

1. 對 `bp_demo.c`，`break target` 後 `x/4xb target`——GDB 顯示原始 byte（它隱藏了 0xCC）。理解這個美化。
2. 用 Ch 2 的 mini tracer 概念，手動 PEEK target 的 byte、POKE 一個 0xCC、CONT，觀察 SIGTRAP——親手做一個斷點（Ch 41 的前奏）。
3. `hbreak target` + `info registers $dr0 $dr7`，看硬體斷點怎麼用 debug register。
4. 下 4 個硬體斷點 + 1 個硬體 watchpoint，看第 5 個報錯（DR0-3 用完）。
5. `set displaced-stepping` 看當前設定；理解它解決的多執行緒漏斷點問題。
6. 寫一個 self-modifying 的小程式（執行時改自己的指令），對被改的位址下軟體斷點，觀察衝突——理解 SMC 的麻煩。

## 本章重點整理

- 軟體斷點：把位址第一 byte patch 成 INT3（`0xCC`），命中觸發 SIGTRAP；命中後 $pc 退 1、還原原 byte；continue 要 single-step 跨過自己的斷點再 patch 回。
- 硬體斷點：用 debug register（DR0-3 + DR7 控制），不 patch 記憶體 → 能斷唯讀記憶體；只有 4 個（與硬體 watch 共用）。
- 硬體 watchpoint：同 DR0-3，設成監看資料存取，限 1/2/4/8 byte——解釋 Ch 13 的限制。
- single-step：PTRACE_SINGLESTEP（設 TF flag）；source-level step = single-step + 查 DWARF line table。
- displaced stepping：把原指令複製到別處執行，避免「跨過斷點時關閉 INT3」的多執行緒 race。

## 自我檢核

- [ ] 軟體斷點下、命中、還原的完整流程是什麼？$pc 為什麼要退 1？
- [ ] 為什麼斷在唯讀記憶體要用硬體斷點？硬體斷點為什麼只有 4 個？
- [ ] source-level `step` 怎麼用 single-step + DWARF 拼出來？（串 Ch 5/38）
- [ ] continue 時怎麼「跨過自己的斷點」？displaced stepping 解決什麼問題？
- [ ] 為什麼 `x/i` 看不到 0xCC？

## 延伸閱讀

### 部落格 / 文章

- **[How debuggers work: Part 2 (Breakpoints)](https://eli.thegreenplace.net/2011/01/27/how-debuggers-work-part-2-breakpoints)** — Eli Bendersky
  - **這篇說什麼**：用 ptrace PEEK/POKE 親手實作 INT3 軟體斷點，含 $pc 退 1、還原。
  - **讀哪裡**：整篇；Ch 41 的直接前置，本章的可跑 code 版。
  - **為什麼值得讀**：理論變實作，做完你就會寫斷點了。

- **[Writing a Linux Debugger: Breakpoints](https://blog.tartanllama.com/writing-a-linux-debugger-breakpoints/)** — Sy Brand
  - **這篇說什麼**：C++ 版的斷點實作，含 enable/disable 的 byte 還原。
  - **和本章的關聯**：Ch 41 mini debugger 的參考實作。

### 規格 / 參考

- **[Intel SDM Vol.3 Ch.17 — Debug, Branch Profile, TSC](https://www.intel.com/sdm)**
  - **讀哪裡**：17.2 Debug Registers（DR0-7 的精確語意）。
  - **和本章的關聯**：硬體斷點/watchpoint 的權威；DR7 的每個 bit。

- **[man 2 ptrace — PTRACE_SINGLESTEP / POKETEXT](https://man7.org/linux/man-pages/man2/ptrace.2.html)**
  - **和本章的關聯**：軟體斷點與 single-step 的 ptrace 介面。

下一章補上最後一塊位址相關的原理：ASLR / PIE / 符號重定位——為什麼位址每次不同、怎麼把 runtime 位址對回符號。

→ [Ch 40 ASLR / PIE / 符號重定位](./40-aslr-pie-relocation.md)
