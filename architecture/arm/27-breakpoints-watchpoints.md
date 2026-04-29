# Ch 27 — 硬體斷點 vs 軟體斷點、watchpoint

> 目標：搞懂兩種斷點機制的實作差異、各自的限制、watchpoint 怎麼運作、Cortex-M 的 FPB/DWT 與 Cortex-A 的 break/watch comparator 細節。debug 「為什麼斷點不命中」「斷點突然多了一條」之類問題的核心知識。

## 軟體斷點：把指令改成 trap

```
原本：       0x08000040: 4801      ldr r0, [pc, #4]
GDB 設斷點： 0x08000040: BE00      bkpt #0
```

GDB 把目標位址的指令**換成 BKPT**（Cortex-M）或 **BRK #1**（AArch64）。CPU 跑到那條 trap → debugger 拿回控制 → 顯示「stop in main」。

優點：

- **沒有數量限制**：可以設一百個 software breakpoint
- 速度快（trap 即時觸發）

缺點：

- **位址必須可寫**：flash 不能寫 → flash 上的 software breakpoint 只能用 hardware
- **self-modifying code 衝突**：JIT、bootloader 寫 flash 時 breakpoint 會被覆蓋
- **多核 race**：兩個 core 都跑到那裡，可能同時 trap、相互干擾

## 硬體斷點：CPU 自己比對位址

CPU 有 **breakpoint comparator** 硬體。設定 register `BP_COMP_n` = 目標位址：

```
CPU fetch 一條指令 → 同時跟所有 BP_COMP 比對
              ├── 沒命中：照常執行
              └── 命中：trigger debug exception，CPU halt
```

優點：

- **不需修改記憶體**：flash 區也能設
- **準確**（fetch 比對，不像 software 要先寫指令）

缺點：

- **數量極少**：Cortex-M3/M4 通常 **6 個**，Cortex-A53 **6 個**，高端 Cortex-A78 **16 個**

## Cortex-M：FPB

**FPB (Flash Patch and Breakpoint Unit)** 提供：

- **6–8 個 instruction comparator**（看 chip）
- **2 個 literal comparator**（match data load）

```c
// 設一個 hardware breakpoint
FPB->FP_COMP[0] = (target_addr & 0x1FFFFFFE) | 1;   // bit[0] enable
FPB->FP_CTRL    |= 1;                                // FPB enable
```

OpenOCD 自動管理：你 `b 0x08000100`，OpenOCD 看 0x08000100 是 flash → 用 FPB → 占用 1 slot。`info breakpoints` 看到的列表後面要寫程式追加。

Comparator 不夠時 GDB 報：「Note: breakpoint -1 also set at pc=...」或直接拒絕。實務上**一次 debug 不要超過 4 個硬體斷點**比較保險。

## Cortex-A：break-comparator

Cortex-A 有獨立的 **debug architecture**（External Debug Architecture）：

- DBGBVR / DBGBCR (BreakPoint Value/Control Register) × 6-16 對
- DBGWVR / DBGWCR (Watch...) × 4-16 對

設定方式類似但 register 在 debug bus 上（不是普通 system register），透過 DAP 從 host 設定。

Cortex-A debug 還有 **Halting Debug Mode** vs **Self-hosted Debug Mode**：
- Halting：外部 debugger（OpenOCD）控制，CPU stop
- Self-hosted：on-target software（kgdb）抓自己的斷點，不需外部 probe

## Watchpoint：看記憶體存取

「**當 0x20000040 被寫時停下來**」 — 這是 watchpoint。

GDB 命令：

```
(gdb) watch x          # 寫到 x 的位址停
(gdb) rwatch x         # 讀 x 停
(gdb) awatch x         # 讀或寫
```

實作：硬體 **watchpoint comparator**（DWT 在 Cortex-M、DBGW* 在 Cortex-A）監測 data bus，匹配位址 + read/write 條件 → trigger。

優點：抓**「誰改了我這個變數」這種頭痛 bug**快速。手動加 print 要把整個程式跑遍每個寫入點，watchpoint 一次就行。

缺點：

- **數量少**：Cortex-M DWT 4 個、Cortex-A 通常 4 個
- **address-sized**：能設 32 / 64-bit access；對「記憶體區段」要靠 mask（見下）

## DWT：Cortex-M 的 watchpoint + 多功能

**DWT (Data Watchpoint and Trace)** 是 Cortex-M 的 watchpoint unit，但功能比純 watch 多：

- **4 個 comparator**：每個能設 watchpoint
- **cycle counter (CYCCNT)**：32-bit cycle 計數
- **Exception trace**：記每個 exception entry/exit
- **PC sampling**：定期 sample PC（statistical profile）
- **Sleep counter / Folded inst counter / LSU counter**

profile 與 watchpoint 共用 4 個 comparator，**設 watchpoint 多了會搶 profile 用**。

範例：watch 一個 4 KB region：

```c
DWT->COMP[0] = 0x20000000;            // 起始位址
DWT->MASK[0] = 12;                    // mask 12 bits → 4 KB
DWT->FUNCTION[0] = (1 << 0) | (5 << 4);  // watchpoint on R/W
```

mask 是 **2^N region** — 不能自由大小，但 4 KB / 1 KB / 64 KB 等夠 cover 常見 array。

## Conditional breakpoint：軟體 emulate

GDB:

```
(gdb) b foo if x > 100
```

實作：硬體 breakpoint 仍然 unconditional，**GDB 自己每次命中後檢查條件**：

```
hit breakpoint → packet T05swbreak 回 GDB → GDB 評估條件 → if false → 自動 c
```

每次評估要往返一次 packet。**conditional breakpoint 在 hot loop 中性能災難**，IRQ-disabled 區甚至可能 hang。

更高效寫法：用 GDB Python 寫 hook（Ch 30）。

## 多核 breakpoint

每個核有自己一套 breakpoint comparator。SMP 系統上 GDB 的「在 main 上設斷點」對應的硬體斷點要**設到每個核**，否則只有一個核會停。

OpenOCD SMP target 自動處理（複製 breakpoint 到所有核），但 software breakpoint 只設一份（**所有核共享 memory，trap 是任意核命中**）。

## 那個「斷點突然消失」的故事

寫 firmware 常碰：「我設了 5 個斷點，第 6 個 GDB 默默不告訴我設失敗了」。

原因：FPB 滿了，GDB 嘗試降級為 software → 但目標在 flash → 失敗，但**有些 OpenOCD 版本不告知**。

debug 方式：

```
(gdb) info breakpoints
Num     Type           Disp Enb Address    What
1       breakpoint     keep y   0x08000400 in main
2       hw breakpoint  keep y   0x08000510 in foo
3       hw breakpoint  keep y   0x08000620 in bar
... 直到 6 個 hw bp
4       hw breakpoint  keep y   <PENDING>     ← 沒設成
```

Pending 是 GDB 警告。或者 OpenOCD log 印「Cannot insert hw breakpoint, no slots available」。

實務 tip：刪掉沒用的 breakpoint（`d 4`），只留會命中的。

## hard breakpoint 數量上限怎麼查

GDB:

```
(gdb) maint info breakpoints
```

OpenOCD：

```
> arm dap apreg 1 0xff8     # 讀 ROM table 找 BP comp register
```

或直接看 chip technical reference manual 的 「Debug Architecture」 章節。

## Print without stopping：dprintf

GDB 9+ 有 `dprintf`（dynamic printf）：

```
(gdb) dprintf foo, "x = %d, y = %d\n", x, y
```

每次到 foo 印一行，不停下來。**比手動 watchpoint + print 高效**。底層仍是斷點 + auto-continue，hot loop 一樣慢。

更好的方案：**ITM printf**（Ch 29），不用 GDB 介入。

## 一個常見誤解

「watchpoint 是不是每次 memory access 都軟體 check？」

**不是**。**全硬體**：comparator 在 memory subsystem 比對，命中才 trap CPU。沒命中 zero overhead。

**但** Cortex-A 上的 「sticky」watchpoint（armv8.4 加的）會 trap 進 EL1 後讓 software 決定要不要 stop — 那部分有軟體開銷。普通 GDB watch 走純硬體路。

## 自我檢核

- [ ] 我能說出 software vs hardware breakpoint 的實作差異
- [ ] 我能列出 software breakpoint 在 flash 上不能用的原因
- [ ] 我能解釋 FPB 的 6 個 comparator 怎麼分配
- [ ] 我能寫一個 DWT watchpoint cover 4 KB 區段
- [ ] 我能解釋 conditional breakpoint 為什麼在 hot loop 下慢
- [ ] 我看得懂 GDB `info breakpoints` 中 Pending 的意思

下一章看 semihosting — Cortex-M 上「沒有 OS 但能 printf」的魔法機制。

→ [Ch 28 Semihosting](./28-semihosting.md)
