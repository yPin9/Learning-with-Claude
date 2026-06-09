# Ch 32 — 指令集與組語基礎

> **目標**：搞懂指令集架構（ISA）、RISC vs CISC（複習 Ch 17）、定址模式、以及看懂基本組合語言。韌體偶爾要讀組語（debug、最佳化、啟動碼），面試會問 ISA 基礎與看簡單組語。

> **環境**：概念為主，ARM / x86 組語。前置：Ch 17（ARM/RISC-CISC）、Ch 11（記憶體）。

## 為什麼考這個

韌體工程師偶爾要讀組語——debug 時看反組譯、最佳化關鍵段、寫啟動碼（Ch 17）。面試會問「ISA 是什麼」「RISC vs CISC」「看懂這段組語在做什麼」。不用會寫複雜組語，但要看得懂基本指令、懂 ISA 概念。

## 先建立直覺：ISA 是硬體和軟體的「合約」

```
   ISA（Instruction Set Architecture，指令集架構）：
   = CPU 提供給軟體的「介面合約」——有哪些指令、暫存器、定址方式、記憶體模型

   軟體（編譯器產生的機器碼）── 依 ISA 合約 ──> 硬體（CPU 實作這些指令）

   同一個 ISA（如 ARM）可以有不同的硬體實作（不同廠商的晶片），
   但都跑同樣的指令 → 軟體可攜（ARM 的 binary 能在任何 ARM CPU 跑）
```

核心：**ISA 是硬體與軟體的抽象介面**——定義「CPU 懂哪些指令」。它讓軟體和硬體解耦（同 ISA 不同實作，binary 通用）。x86、ARM、RISC-V 是不同的 ISA。

## RISC vs CISC（複習 Ch 17）

| | RISC（ARM/RISC-V/MIPS）| CISC（x86）|
|---|---|---|
| 指令 | 少、簡單、**定長** | 多、複雜、**變長** |
| 記憶體存取 | **load/store**（運算只在暫存器）| 運算可直接對記憶體 |
| 暫存器 | 多 | 較少 |
| 每指令 cycle | 通常 1（簡單，好 pipeline）| 多（複雜）|
| 功耗 | 低（行動裝置）| 高（桌面/伺服器）|

複習重點（Ch 17）：ARM 是 RISC、load/store 架構（運算前要先 load 進暫存器）、定長指令（好做 pipeline，Ch 31）、低功耗（手機用）。**定長指令對 pipeline 友善**——每條指令長度一樣，IF 階段好取（CISC 變長指令解碼複雜）。

## 定址模式（addressing modes）

指令怎麼指定「運算元在哪」——常見幾種：

```
   immediate（立即）：運算元是常數
      MOV R0, #5          ; R0 = 5（5 直接寫在指令裡）

   register（暫存器）：運算元在暫存器
      ADD R0, R1, R2      ; R0 = R1 + R2

   direct/absolute（直接）：運算元在某固定記憶體位址
      LDR R0, [0x1000]    ; R0 = 記憶體[0x1000]

   register indirect（暫存器間接）：位址在暫存器裡
      LDR R0, [R1]        ; R0 = 記憶體[R1 的值]（R1 存位址）

   indexed/displacement（變址/偏移）：暫存器 + 偏移
      LDR R0, [R1, #4]    ; R0 = 記憶體[R1 + 4]（存取 struct 成員、陣列常用）

   PC-relative（PC 相對）：相對於 PC 的偏移（跳轉、存取常數常用）
```

定址模式對應 C 的東西：register indirect = 解指標（`*p`）；indexed = 陣列/struct 成員（`arr[i]`、`s.field`）。理解定址模式 = 理解 C 的指標/陣列怎麼變成機器碼。

## 看懂基本 ARM 組語

韌體常見的 ARM 指令（看得懂即可，不用會寫複雜的）：

```asm
   ; 資料搬移
   MOV  R0, #5         ; R0 = 5
   MOV  R0, R1         ; R0 = R1

   ; 算術
   ADD  R0, R1, R2     ; R0 = R1 + R2
   SUB  R0, R1, #1     ; R0 = R1 - 1

   ; 記憶體（load/store，RISC 只有這些碰記憶體）
   LDR  R0, [R1]       ; load: R0 = 記憶體[R1]
   STR  R0, [R1]       ; store: 記憶體[R1] = R0
   LDR  R0, [R1, #4]   ; R0 = 記憶體[R1 + 4]

   ; 比較與分支（if/迴圈）
   CMP  R0, R1         ; 比較 R0 和 R1（設旗標）
   BEQ  label          ; branch if equal（相等就跳）
   BNE  label          ; branch if not equal
   B    label          ; 無條件跳

   ; 函式呼叫
   BL   func           ; branch with link（呼叫，返回位址存 LR，Ch 17）
   BX   LR             ; 返回（跳回 LR 存的位址）
```

看一段組語 = 對應回 C：`CMP + BEQ` 是 `if`；`LDR/STR` 是讀寫記憶體（指標/變數）；`BL` 是函式呼叫；`B label`（往回跳）是迴圈。面試給組語要你說「這在做什麼」——對應回 C 結構即可。

## x86 vs ARM 組語（簡單對照）

```
   x86（CISC，AT&T 語法 src, dst）：
   movl $5, %eax       ; eax = 5
   addl %ebx, %eax     ; eax = eax + ebx
   （x86 可直接對記憶體運算：addl %eax, (%ebx)）

   ARM（RISC，dst 在前）：
   MOV R0, #5          ; R0 = 5
   ADD R0, R0, R1      ; R0 = R0 + R1
   （ARM 要先 LDR 進暫存器才能運算——load/store）
```

差異反映 RISC/CISC（Ch 17）：x86 能直接對記憶體運算（CISC）、ARM 要 load 進暫存器（RISC load/store）。語法也不同（運算元順序、暫存器命名）。

## 考古題詳解

### Q1：什麼是 ISA？

<details>
<summary>詳解</summary>

ISA（Instruction Set Architecture，指令集架構）：CPU 提供給軟體的介面合約——定義有哪些指令、暫存器、定址方式、記憶體模型。

它是硬體與軟體的抽象介面：同一 ISA（如 ARM）可有不同硬體實作，但跑同樣的指令 → binary 可攜。x86/ARM/RISC-V 是不同 ISA。

**考點**：ISA 概念。
</details>

### Q2：RISC 和 CISC 的主要差異？（複習 Ch 17）

<details>
<summary>詳解</summary>

RISC（ARM）：指令少/簡單/定長、load/store 架構（運算只在暫存器）、暫存器多、低功耗、定長指令好 pipeline。CISC（x86）：指令多/複雜/變長、可直接對記憶體運算、高效能耗電。

ARM 是 RISC（手機用，低功耗 + 定長指令利於 pipeline）。詳見 Ch 17。

**考點**：RISC vs CISC，Ch 17 複習。
</details>

### Q3：這段 ARM 組語在做什麼？

```asm
    MOV  R0, #0
    MOV  R1, #0
loop:
    ADD  R0, R0, R1
    ADD  R1, R1, #1
    CMP  R1, #10
    BNE  loop
```

<details>
<summary>詳解</summary>

對應 C：
```c
int sum = 0;           // R0
for (int i = 0; i < 10; i++)   // R1 = i
    sum += i;          // ADD R0, R0, R1
```

逐行：R0=0（sum）、R1=0（i）；loop: sum += i、i++、比較 i 和 10、不等就跳回 loop。算 0+1+...+9 = 45。

看組語的訣竅：CMP+BNE = 迴圈條件、往回 B = 迴圈、ADD = 運算——對應回 C 結構。

**考點**：看懂基本組語（對應 C），面試常給一段問「在做什麼」。
</details>

### Q4：register indirect 定址對應 C 的什麼？

<details>
<summary>詳解</summary>

**register indirect（`LDR R0, [R1]`，位址在暫存器）對應 C 的解指標 `*p`**——R1 存位址（指標），`[R1]` 是解參照。

indexed（`LDR R0, [R1, #4]`）對應陣列/struct 成員（`arr[i]`、`s.field`，base + offset）。

理解定址模式 = 理解 C 的指標/陣列怎麼編譯成機器碼。

**考點**：定址模式對應 C，連結 Ch 4。
</details>

### Q5：為什麼 RISC 的定長指令對 pipeline 有利？

<details>
<summary>詳解</summary>

定長指令（每條一樣長）→ **IF（取指令）階段好取**（知道每條多長、下一條在哪）、解碼簡單、好預測——pipeline（Ch 31）順暢。

CISC 變長指令 → 取指令/解碼複雜（要先解析長度才知道下一條在哪）、pipeline 較難。這是 RISC 利於 pipeline 的原因之一（Ch 17/31 串連）。

**考點**：定長指令 vs pipeline，串 Ch 17/31。
</details>

## 踩雷集錦

1. **ISA 和微架構搞混**：ISA 是「指令集合約」（抽象介面）；微架構是「怎麼實作」（pipeline 幾級、cache 多大等具體設計）。同 ISA 可有不同微架構。
2. **以為 ARM 能直接對記憶體運算**：ARM 是 load/store（Ch 17）——運算前要 LDR 進暫存器。x86（CISC）才能直接對記憶體。
3. **看組語抓不到 C 結構**：CMP+B 條件跳是 if/迴圈、LDR/STR 是讀寫、BL 是函式呼叫——對應回 C。
4. **混淆定址模式**：register indirect（`[R1]`）= 解指標；indexed（`[R1,#4]`）= 陣列/成員。
5. **以為要會寫複雜組語**：面試多是「看懂」基本組語，不是手寫複雜的。重點是對應 C 結構。

## 速記

- **ISA**：CPU 給軟體的介面合約（指令/暫存器/定址/記憶體模型）；硬體軟體解耦，binary 可攜。x86/ARM/RISC-V 是不同 ISA。
- **RISC（ARM）**：少/簡單/定長指令、load/store、多暫存器、低功耗、定長利於 pipeline（Ch 17/31）。**CISC（x86）**：多/複雜/變長、可對記憶體運算。
- 定址模式：immediate（常數）、register、direct（固定位址）、**register indirect（`[R1]`=解指標）**、**indexed（`[R1,#4]`=陣列/成員）**、PC-relative。
- 看組語對應 C：CMP+B（if/迴圈）、LDR/STR（讀寫變數/指標）、BL（函式呼叫）、往回 B（迴圈）。

## 自我檢核

- [ ] ISA 是什麼？和微架構差在哪？
- [ ] RISC 和 CISC 主要差異？（Ch 17 複習）
- [ ] 給一段簡單組語（迴圈/if），你能對應回 C 嗎？
- [ ] register indirect 和 indexed 定址各對應 C 的什麼？
- [ ] 為什麼定長指令對 pipeline 有利？

## 延伸閱讀

### 本 repo

- **[architecture/arm](../../architecture/arm/README.md)** 與 **[architecture/riscv](../../architecture/riscv/README.md)**
  - **這些課的定位**：ARM / RISC-V 的完整 ISA 課程（含組語、定址、自寫 emulator）。本章只是面試概觀，想深入 ISA 讀這些。

### 書籍

- **《Computer Organization and Design (ARM edition)》** — Patterson & Hennessy
  - **讀哪幾章**：Ch 2（ARM ISA、定址模式、組語）。
  - **和本章的關聯**：用 ARM 講 ISA 的標準教材，本章源頭。

ISA/組語懂了，下一章是 C code 怎麼變成可執行檔——編譯/組譯/連結/載入流程。

→ [Ch 33 編譯/組譯/連結/載入](./33-compile-link-load.md)
