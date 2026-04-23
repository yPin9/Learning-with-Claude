# Ch 9 — Zc 擴充與 code size

> 目標：理解 C 擴充已經做了壓縮，為什麼 Zc 家族還要繼續擠。看懂 `Zca` / `Zcb` / `Zcd` / `Zcf` / `Zcmp` / `Zcmt` 各自填補什麼空缺、為什麼嵌入式領域為了省 1 KB flash 會這麼拼。

## 為什麼再做一組「更省」的擴充

2020 年前 RISC-V 社群的問題：

- ARM Cortex-M 有 Thumb-2，code size 比 RV32GC 還小 5–10%。嵌入式客戶（Bluetooth 晶片、感應器 SoC）拿 ARM 壓 RISC-V。
- C 擴充雖然省，但還有很多 "common pattern" 沒壓縮版。例：一條存/載多個暫存器的指令（ARM `push {r0-r7, lr}` 一條搞定，RV 要 9 條 `sw`）。

2023 年 Zc 擴充家族 ratify，目標**在小面積 MCU 場景下拉平甚至擊敗 Thumb-2**。

## Zc 擴充的六個子集

```
Zca     等於 base C 擴充 (為了重新組織，獨立命名)
Zcb     Additional code-size instructions          (byte/half load/store、mul/min/max 的 compressed 版)
Zcd     C 擴充中的雙精度 float load/store (D 配套)
Zcf     C 擴充中的單精度 float load/store (F 配套)  (僅 RV32)
Zcmp    Push/Pop multiple reg + stack frame 整合
Zcmt    Table jump (jump via table, 省 code space)
```

Zca 只是把舊 C 重新命名（向前相容）。真正「新」的是 Zcb / Zcmp / Zcmt。

## Zcb：補缺的常用壓縮指令

C 擴充裡沒有 byte / half 的 load/store。Zcb 補：

```
c.lbu  rd', 0/1/2/3(rs1')       # load byte unsigned, 16-bit encoded
c.lhu  rd', 0/2(rs1')
c.lh   rd', 0/2(rs1')           # load half signed
c.sb   rs2', 0/1/2/3(rs1')
c.sh   rs2', 0/2(rs1')
```

注意：

- 只能用 **`x8..x15` 的 3-bit 編碼**（rd'/rs1'/rs2'，典型 C 擴充限制）。
- 立即數 offset 只有幾個選擇（byte 0–3、half 0 或 2）。
- 超過範圍就退回 32-bit `lb`/`sw` 等。

另外 Zcb 還加了：

```
c.zext.b / c.sext.b / c.zext.h / c.sext.h
c.zext.w     (RV64 only)
c.not
c.mul        (需要 M 擴充)
```

這些都是 byte 處理、sign extension 的常用操作。以前每個要 2 byte，現在 1 byte。`c.not` 取代 `xori rd, rd, -1`（3 byte）的常用情境。

### Zcb 實測省多少

編譯 SPEC2006 或類 MIPS 的控制 code，Zcb 可以再省 **2–5% 的 code size**（相對已有 C 擴充）。對 32 KB flash 的 MCU 來說，5% = 1.6 KB，**夠放幾十個函式**。商業產品很計較這個。

## Zcmp：stack frame 一條指令搞定

傳統 RV32 / RV64 的 prologue / epilogue：

```asm
# prologue: 存 ra, s0, s1, s2, s3
addi sp, sp, -24
sw   ra, 20(sp)
sw   s0, 16(sp)
sw   s1, 12(sp)
sw   s2,  8(sp)
sw   s3,  4(sp)
```

6 條指令。ARM Cortex-M 只要一條 `push {r0-r3, lr}`。Zcmp 的 `cm.push` 補這個：

```asm
cm.push {ra, s0-s3}, -32
```

一條 16-bit 指令，硬體展開成 6 條 store + sp 調整。對稱的 `cm.pop` 做 epilogue。

還有額外 variants：

```
cm.pop     {reg_list}, frame_size     # epilogue (load + sp 恢復)
cm.popret  {reg_list}, frame_size     # pop + ret 一條做完
cm.popretz {reg_list}, frame_size     # pop + 設 a0=0 + ret   (常用於 void function)
cm.mva01s  s0', s1'                   # mv {a0, a1} ← {s0', s1'}
cm.mvsa01  s0', s1'                   # mv {s0', s1'} ← {a0, a1}
```

`cm.popretz` 特別聰明：C 的 `void f() { ... }` 隱式 return 0。epilogue 時一條指令做 pop + zero + ret。

### Zcmp 的 reg_list 編碼

```
reg_list: ra (必選), s0, s1, s2, s3, s4, s5, s6, s7, s8, s9, s11
```

注意**沒 `s10`**（編碼 slot 不夠），compiler 會避免 allocate `s10`。這是擴充跟 register allocator 互動的經典範例。

## Zcmt：table jump（switch-case 優化）

C 的 `switch (x)` 大 table 版本：

```c
switch (x) {
    case 0: foo(); break;
    case 1: bar(); break;
    ...
    case 7: baz(); break;
}
```

傳統 compile 後：每個 case 一條 compare + branch（8 個 case = 16 條指令），或 jump table（load pointer + jalr，至少 3 條）。

Zcmt 的 `cm.jt` / `cm.jalt` 指令：**硬體內建 jump table**。

```
cm.jt   table_index        # jump to table[index]
cm.jalt table_index        # jump + link
```

table 存在 `jvt` CSR 指向的記憶體區域。硬體自動 load + jump。

**省多少？** 大 switch 可以從 20+ 條指令縮到 1 條。對有大量 switch 的 kernel / protocol stack 有 meaningful impact。

代價：需要硬體實作 jvt CSR、需要 linker 知道怎麼排 jump table（新的 relocation type）。生態還在建。

## Zcf / Zcd：浮點 load/store 壓縮

這兩個簡單：

- `Zcf`：RV32 的 `c.flw` / `c.fsw` / `c.flwsp` / `c.fswsp`
- `Zcd`：`c.fld` / `c.fsd` / `c.fldsp` / `c.fsdsp`

前提是有 F / D 擴充。浮點 load/store 頻繁的 code 能省 1–2%。

## code size 的商業價值

**為什麼嵌入式市場這麼在乎？** 假設一顆 Cortex-M4 級別的 SoC：

- Flash 32 KB = 製造成本幾毛美分
- 一個 BLE 協議 stack + application 典型 20–25 KB
- 省 10% code size = 可以塞更多 feature、或用更小的 flash 型號
- 每顆省 5 美分 × 年產 1 億顆 = 500 萬美元

這是為什麼 ARM 願意設計 Thumb-2 那麼複雜的變長 encoding、為什麼 RISC-V 社群拼命跟。Zc 家族的終極目標：**RV32EC + Zcb + Zcmp + Zcmt 的 code size ≤ Thumb-2**。

2024 年的結果：**幾乎打平**。Zc 還在成熟中，下一代 RISC-V MCU 會是主戰場。

## Zc 跟 toolchain

### GCC

GCC 14+ 支援 Zcb / Zcmp。開法：

```
-march=rv32imc_zca_zcb_zcmp_zcmt
```

compile 時 compiler 自動判斷何時用 `cm.push`、何時用個別 `sw`。heuristic 是「存的 reg ≥ 某閾值才用 cm.push」。

### LLVM

類似。LLVM 17 開始 support，但某些 pass（特別是 stack slot allocation）還在優化。

### 不要混著開沒用的子集

`-march=rv32imc_zcmp` 是合法的（因為 Zcmp 依賴 Zca = C），但不開 Zcb 可能 miss 一些 code-size savings。通常一起開：

```
-march=rv32imc_zca_zcb_zcmp_zcmt
```

## 為什麼 compiler 的角色變重

Zcmp 讓 prologue 變成「一條指令對應 N 個 register save」。compiler 的 register allocator 要考慮：

- 如果需要存 5 個 reg，用 cm.push 嗎？
- 需要存的 reg 不是連續的（例：只存 `s3`、`s5`），cm.push 不支援 → 退回 `sw` 方案
- 函式夠熱 / 夠大才值得用 cm.push（超小函式 prologue 本身就是 overhead）

這些 heuristic 調整是 compiler 工程師的日常工作。**SiFive job spec 中的「效能分析與優化」大量就在這種層面**。

## Zc 跟其他擴充的互動

- **Zcmp + ABI**：callee-saved 的順序被 Zcmp 影響。compiler 為了 push 時 reg list 連續，可能優先 allocate `s0, s1, s2, s3`，避開 `s10`。
- **Zcmt + linker**：jump table 要放在特定 section、jvt 要 linker 設定。有新的 linker script 語法。
- **Zcb + 原有的 C**：Zcb 指令的 opcode 重用了 C 擴充保留給「未來擴充」的 slot，所以硬體可以輕鬆加。

## 常見誤會

1. **「Zc 擴充會搶 C 擴充」**：不。Zca = C 的別名，向前相容。真正新指令都放到新 opcode。
2. **「cm.push 總是比個別 sw 快」**：不一定。硬體內部可能拆成 micro-op，latency 類似。**code size 是主要收益**，不是 speed。
3. **「Zcmt 取代 jump table」**：補充關係。switch 少 case 還是用 compare + branch。Zcmt 是「大 switch」的工具。
4. **「Zcb 是可有可無」**：對嵌入式 critical。2–5% code size 直接影響 BOM。
5. **「RV 終於追上 Thumb-2」**：近似打平，但 ARM 的 Thumb-2 還有 IT (if-then) block 這種 conditional execution 機制，code density 在某些 pattern 上還贏 RV。

## 跟面試的關係

SiFive 多數 customer 是嵌入式（他們的 core 不只是 server 用）。**面試被問 Zcb / Zcmp 很常見**，特別是「你怎麼決定 compile 時開不開某個子集」這種 trade-off 題。

準備一套論述：

- Zcb：code size -2% 到 -5%、幾乎沒 perf penalty → **應該開**
- Zcmp：code size -3% 到 -8%（prologue 重的 code）、可能影響 scheduling → 熱函式可能關
- Zcmt：大 switch 才有收益、需要 runtime 安排 jvt → 看 workload

能講出這種結構的回答，你已經贏了 80% 的候選人。

## 動手練習

1. 寫一支有 5 個 callee-saved 的 function（故意用 `s0..s4`），`-march=rv32imc` vs `-march=rv32imc_zcmp` 編譯，比 objdump 的 prologue。
2. 寫一個 100-case switch，`-Os` 編，看 compiler 有沒有生成 jump table。再開 Zcmt（如果 toolchain 支援）看有沒有變 `cm.jt`。
3. 統計兩份 Coremark binary 的 size 差異：一份 `rv32imc`、一份 `rv32imc_zcb_zcmp`。算百分比。
4. 找一段你寫過的 MCU firmware（或 FreeRTOS binary），改 `-march` 重編後看 flash section 大小變化。
5. 讀 LLVM 的 `RISCVMachineFunctionInfo`，找「decide whether to use cm.push」的 heuristic。一半是寫在 `RISCVFrameLowering.cpp`。

## 自我檢核

- [ ] 我能列 Zc 家族的六個子集以及各自解決什麼
- [ ] 我能解釋 cm.push 的 reg_list 限制以及對 register allocator 的影響
- [ ] 我知道 Zcmt 的 jvt CSR + jump table 怎麼運作
- [ ] 我能在面試中講出「什麼擴充對你的 workload 值得開、trade-off 是什麼」
- [ ] 我知道 Zcb 為什麼是嵌入式市場的「必備」

下一章收尾 Part 3 的最後一站 — Hypervisor extension。這是 RISC-V 給虛擬化跑 hypervisor（type-1 / type-2）的支援，你不一定要深入實作，但面試可能被問「RISC-V 怎麼跑 KVM」。

→ [Ch 10 Hypervisor extension 速覽](./10-hypervisor-extension.md)
