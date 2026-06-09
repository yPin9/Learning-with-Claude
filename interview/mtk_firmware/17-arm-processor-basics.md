# Ch 17 — ARM 與處理器基礎

> **目標**：補足韌體工程師該懂的處理器知識——ARM 架構概觀、暫存器、處理器模式與例外、韌體啟動流程、為什麼韌體用 C + 少量組語。MTK 的晶片大量用 ARM，這是背景知識題。

> **環境**：概念為主，ARM Cortex（A/M 系列）。前置：Ch 11（記憶體）、Ch 13（暫存器存取）、Ch 14（中斷）。

## 為什麼考這個

MTK 做手機/IoT 晶片，核心是 ARM。韌體工程師雖然主要寫 C，但要懂「code 跑在什麼處理器上、啟動怎麼發生、什麼時候需要組語」。面試會問 ARM 基礎、RISC vs CISC、處理器模式——展現你懂硬體這一層，不只是寫 application code。

> 認識論誠實：ARM 細節龐大（不同架構版本、Cortex-A vs Cortex-M 差很多）。本章是「面試夠用」的概觀，不是 ARM 完整教材。深入見 architecture/arm 課程（本 repo）或 ARM 官方文件。重點是建立「韌體與處理器的關係」的圖像。

## 先建立直覺：韌體站在硬體和軟體的交界

```
   應用程式（App）        ← 跑在 OS 上，不碰硬體
        │
   作業系統 / RTOS       ← 管理資源
        │
   韌體（Firmware）← 你在這！直接控制硬體、初始化系統、提供底層服務
        │
   處理器（ARM CPU）+ 周邊硬體（GPIO/UART/...）
```

韌體是「最貼近硬體的軟體」——它要懂處理器怎麼運作（暫存器、模式、例外、啟動），才能正確初始化和控制硬體。這就是為什麼韌體工程師要懂處理器基礎。

## RISC vs CISC（必考對比）

ARM 是 **RISC**（精簡指令集），x86 是 **CISC**（複雜指令集）：

| | RISC（ARM） | CISC（x86） |
|---|---|---|
| 指令 | 少、簡單、定長 | 多、複雜、變長 |
| 每指令 | 通常 1 cycle、簡單動作 | 一指令可做複雜事（多 cycle） |
| 暫存器 | 多（ARM 16+ 個通用） | 較少（傳統 x86 少） |
| 記憶體存取 | **load/store 架構**（只有 load/store 碰記憶體，運算只在暫存器）| 運算可直接對記憶體 |
| 功耗 | 低（簡單→省電）→ 行動裝置首選 | 高（傳統桌面/伺服器）|
| 編譯器 | 做較多最佳化（指令簡單）| 硬體做較多（指令複雜）|

關鍵：**ARM 是 RISC、load/store 架構、低功耗**——所以手機/IoT 用 ARM（省電）。x86 是 CISC、高效能但耗電（桌面/伺服器）。MTK 做行動晶片，用 ARM 是因為功耗。

> load/store 架構：RISC 的特色——記憶體只能用 load（讀進暫存器）和 store（暫存器寫回記憶體）存取，所有運算（加減乘）只在暫存器間做。所以「先 load 進暫存器、運算、再 store 回去」。CISC 可以一條指令直接對記憶體運算。

## ARM 暫存器（Cortex 概觀）

ARM 有一組通用暫存器 + 特殊暫存器（以 Cortex-M / AArch32 概觀）：

```
   通用暫存器：R0–R12（運算用）
   特殊用途：
   R13 = SP（Stack Pointer）  ← 指向 stack 頂
   R14 = LR（Link Register）  ← 存「函式返回位址」（呼叫函式時自動存）
   R15 = PC（Program Counter）← 下一條要執行的指令位址
   特殊：xPSR（程式狀態暫存器，存旗標 N/Z/C/V、中斷狀態等）
```

對照 x86（Ch 11/計組）：ARM 的 LR（返回位址在暫存器）和 x86（返回位址壓 stack）不同——ARM 函式呼叫先把返回位址放 LR，省一次記憶體存取（RISC 風格）。

函式參數傳遞（AAPCS ABI）：前幾個參數用 R0–R3 傳，多的用 stack；回傳值在 R0。（對照 x86-64 用 rdi/rsi...，Ch 計組。）

## 處理器模式與例外（exception）

ARM 有不同的「執行模式/特權等級」，和例外處理：

```
   特權等級（簡化）：
   - User mode（非特權）：跑應用程式，不能存取某些系統資源
   - Privileged mode（特權）：跑 OS/韌體，能完整控制硬體

   例外（exception）：打斷正常執行的事件
   - Reset：開機/重啟
   - 中斷（IRQ/FIQ）：硬體中斷（Ch 14）
   - Fault：錯誤（非法存取、未定義指令等）
   - SVC（Supervisor Call）：軟體主動觸發（system call，Ch 28）
```

例外發生時，CPU 切到對應的 handler（透過例外向量表，類似中斷向量表 Ch 14）。中斷（Ch 14）就是例外的一種。這串到 OS 的 user/kernel mode（Ch 28）——特權等級的切換是 OS 保護的基礎。

## 韌體啟動流程（boot）

開機到跑你的 code，大致：

```
   1. 上電 / Reset
        │
   2. CPU 從固定位址（reset vector）開始執行 ← 通常是 ROM/flash 的啟動碼
        │
   3. 啟動碼（startup，常是組語）：
      - 設定 stack pointer（SP）
      - 初始化 .data 段（從 flash 複製初值到 RAM）
      - 清零 .bss 段（Ch 11）
      - 設定時脈、基本硬體
        │
   4. 跳到 C 的 main()  ← 你的 C code 從這開始
        │
   5. main 初始化周邊、進主迴圈 / 啟動 RTOS
```

關鍵：**在 main() 之前，有一段組語啟動碼**做了 C 跑起來的前置（設 SP、初始化 data/bss）。這解釋為什麼「韌體用 C + 少量組語」——啟動最前面、和某些底層操作需要組語，其餘用 C。

## 為什麼韌體用 C + 少量組語

```
   用 C（90%+）：
   - 可讀、可維護、可攜（換 CPU 改編譯器即可）
   - 夠底層（能存取記憶體位址、bit 操作、指標）
   - 開發效率高

   需要組語（少量）的場合：
   - 啟動碼（main 之前，設 SP、初始化）
   - 例外/中斷向量表的最底層處理
   - 存取特殊暫存器（C 碰不到的 CPU 特殊指令，如關中斷、記憶體屏障）
   - 極致效能/時序要求的關鍵段
```

韌體選 C 是「夠底層又可維護」的甜蜜點——比組語好寫好移植，又比高階語言貼近硬體。組語只用在「C 做不到或不夠的最底層」。這是經典面試問題「為什麼韌體用 C」的答案。

## 考古題詳解

### Q1：RISC 和 CISC 差在哪？ARM 是哪個？為什麼手機用 ARM？

<details>
<summary>詳解</summary>

RISC（ARM）：指令少而簡單、定長、load/store 架構、暫存器多、低功耗。CISC（x86）：指令多而複雜、變長、可直接對記憶體運算、高效能但耗電。

ARM 是 **RISC**。手機/IoT 用 ARM 因為**低功耗**（RISC 簡單指令省電，行動裝置電池有限）。MTK 做行動晶片所以用 ARM。

**考點**：RISC vs CISC + 為什麼行動裝置用 ARM，高頻。
</details>

### Q2：ARM 的 LR、PC、SP 各是什麼？

<details>
<summary>詳解</summary>

- **PC（R15）**：Program Counter，下一條要執行的指令位址。
- **SP（R13）**：Stack Pointer，指向 stack 頂。
- **LR（R14）**：Link Register，存函式呼叫的返回位址（呼叫時自動存進 LR，返回時跳回）。

ARM 用 LR 存返回位址（暫存器，省記憶體存取）是 RISC 特色，和 x86「返回位址壓 stack」不同。

**考點**：ARM 特殊暫存器。
</details>

### Q3：韌體啟動到跑 main() 之前發生了什麼？

<details>
<summary>詳解</summary>

1. Reset → CPU 從 reset vector（固定位址）開始執行啟動碼。
2. 啟動碼（組語）：設 SP、初始化 .data（從 flash 複製初值到 RAM）、清零 .bss、設時脈/基本硬體。
3. 跳到 C 的 main()。

關鍵：**main 之前有組語啟動碼做 C runtime 的前置**（設 SP、初始化 data/bss，Ch 11）。沒有這步，C 變數的初值、stack 都還沒就緒。

**考點**：boot 流程，解釋「為什麼需要組語啟動碼」。
</details>

### Q4：為什麼韌體主要用 C，但還是需要一點組語？

<details>
<summary>詳解</summary>

用 C：可讀、可維護、可攜、開發快，又夠底層（指標/bit/位址存取）。

需要組語：(1) 啟動碼（main 前設 SP/初始化）；(2) 例外向量最底層；(3) C 碰不到的 CPU 特殊指令（關中斷、記憶體屏障、特殊暫存器）；(4) 極致效能/時序的關鍵段。

C 是「夠底層又可維護」的甜蜜點；組語只補 C 做不到的最底層。

**考點**：為什麼韌體用 C（+少量組語），經典題。
</details>

### Q5：例外（exception）和中斷（interrupt）的關係？

<details>
<summary>詳解</summary>

**中斷是例外的一種。** 例外（exception）泛指「打斷正常執行流的事件」，包括：reset、硬體中斷（IRQ/FIQ）、fault（錯誤）、SVC（軟體主動觸發，如 system call）。中斷（Ch 14）是「硬體事件觸發的例外」。

它們都透過向量表跳到對應 handler，都會保存現場、跳去處理、再返回。

**考點**：exception vs interrupt 的包含關係，串 Ch 14/28。
</details>

## 踩雷集錦

1. **RISC/CISC 記反**：RISC（ARM）= 精簡、低功耗；CISC（x86）= 複雜、高效能耗電。
2. **以為 ARM 運算可直接對記憶體**：ARM 是 load/store 架構——運算只在暫存器，記憶體要先 load。
3. **不知道 main 之前有啟動碼**：以為上電直接跑 main。其實有組語啟動碼設 SP、初始化 data/bss。
4. **以為韌體全用組語**：現代韌體 90%+ 是 C，組語只補最底層。
5. **混淆 exception 和 interrupt**：中斷是例外的一種（例外更廣，含 fault/reset/SVC）。
6. **以為 ARM 都一樣**：Cortex-A（應用處理器，跑 Linux/Android）和 Cortex-M（微控制器，跑裸機/RTOS）差很多。面試可問你應徵的是哪類。

## 速記

- **ARM = RISC**：指令少/簡單/定長、**load/store 架構**（運算只在暫存器）、暫存器多、**低功耗**（所以手機/IoT 用它）。x86 = CISC。
- ARM 暫存器：R0–R12 通用、**SP**(R13,stack頂)、**LR**(R14,返回位址)、**PC**(R15,下條指令)、xPSR(旗標)。
- 例外（exception）含中斷/fault/reset/SVC；**中斷是例外的一種**。
- **boot**：reset → 組語啟動碼（設SP、初始化data/bss、設時脈）→ 跳 C main()。
- **韌體用 C + 少量組語**：C 夠底層又可維護；組語補最底層（啟動碼、特殊指令、向量表）。

## 自我檢核

- [ ] RISC 和 CISC 差在哪？ARM 是哪個？為什麼行動裝置用 ARM？
- [ ] ARM 的 SP、LR、PC 各是什麼？LR 和 x86 的返回位址處理有何不同？
- [ ] main() 之前發生了什麼？為什麼需要組語啟動碼？
- [ ] 為什麼韌體主要用 C 但還需要組語？組語用在哪些地方？
- [ ] exception 和 interrupt 的關係？

## 延伸閱讀

### 本 repo

- **[architecture/arm](../../architecture/arm/README.md)**
  - **這門課的定位**：ARM 從 ISA 到 JTAG 的完整課程（Cortex-A/M 雙線）。本章只是面試概觀，想深入 ARM 讀這門。

### 官方文件 / 書籍

- **[ARM Cortex-M / Cortex-A Technical Reference Manual](https://developer.arm.com/documentation)** — ARM
  - **讀哪裡**：暫存器、例外模型、programmer's model 概觀（不用全讀，查面試會問的）。
  - **和本章的關聯**：ARM 架構的權威；面試前掃暫存器與例外那幾節。

- **《Computer Organization and Design (ARM edition)》** — Patterson & Hennessy
  - **讀哪幾章**：ARM ISA、processor 章。
  - **為什麼值得讀**：用 ARM 講計組的經典教材，連 Part 4 一起補。

處理器背景有了，下一章是嵌入式的軟體核心——RTOS 概念，和 Part 3 的一般 OS 對照。

→ [Ch 18 RTOS 概念](./18-rtos-concepts.md)
