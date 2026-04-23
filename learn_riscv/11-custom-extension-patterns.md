# Ch 11 — Custom extension 的設計範式

> 目標：這章對應 SiFive 職缺最核心的工作內容 — 「add new RISC-V extensions」。理解一個 custom extension 從「想加一條新指令」到「塞進 toolchain + hardware + 產品線」的完整流程。不是每一步你都要做，但你要聽得懂跨團隊討論。

## 什麼叫 custom extension

RISC-V 的「modular」不只在標準 ISA。**spec 保留了 opcode 空間**給任何人加自己的指令。你可以：

- 在廠商內部加（SiFive / T-Head / Andes 都有）
- 公司間協議加（客戶要某個加速，你幫他做）
- 學術研究加（論文裡的原型）

這類「非標準」指令叫 custom instruction，一組相關的叫 **custom extension**。命名慣例是 `X<vendor><feature>`，例如：

```
XSfVector           SiFive 的 vector custom
XTHeadBb            T-Head (XuanTie) 的 bitmanip 類擴充
XAndesPerf          Andes 自家的 perf 指令
Xmynew              (任意自訂)
```

首字母 `X` 表示 **e**X**tension by vendor**（非 spec 組織 ratify 的）。

## spec 保留給你的 opcode 空間

RV32/64 的 opcode 分在 `[6:2]` 五位（`[1:0]` 固定 `11` 表 32-bit 指令）。spec 把其中幾塊畫為 **"reserved for custom use"**：

```
opcode[6:2]   意義
00010         custom-0
01010         custom-1
10110         custom-2 (RV32 only)
11110         custom-3 (RV32 only)
```

RV64 少兩個，因為 RV128 spec 保留了 10110 / 11110。

這四塊 opcode 在 spec 的保證：**未來 RV 官方永遠不會用**。所以你放心加自己的指令進去。

## 設計一個 custom extension 的 checklist

以 SiFive 工程師的日常視角，加一個 extension 要走：

### 1. 定義指令語意

- 指令格式（R-type / I-type / ...）
- 操作的 register / immediate
- Side effect（有沒有寫 CSR、觸發 exception、跨 cache line）
- Rounding / overflow 行為

**這階段的產出是 spec 文件**。SiFive 會先寫 Markdown / LaTeX，走內部 review。

### 2. 決定 opcode 編碼

從 `custom-0..3` 裡挑一塊、設計 funct3 / funct7 的 subcode。這是**一次性、不可回頭**的決定 — 將來撞到其他 extension 的編碼就炸了。所以通常內部會維護 "opcode registry"。

### 3. 實作硬體模擬

在 spike 或 SAIL 這類 reference model 裡寫 semantic。目的：
- 讓 compiler 工程師不用等硬體就能測試
- 作為「硬體實作正確性」的比對基準（硬體 verification team 的 golden model）

### 4. 實作 RTL

Chisel / Verilog / SpinalHDL 寫真實硬體。這步在硬體團隊，compiler 工程師不碰。但你要看得懂 timing spec（this instruction has latency 3 cycles, fully pipelined）來寫 scheduling model。

### 5. 加 assembler 支援

binutils / LLVM-MC 支援：
- `gas` 的 `opcodes/riscv-opc.c` 加入 encoding table
- LLVM 的 `lib/Target/RISCV/AsmParser/RISCVAsmParser.cpp` 加 parse rule

這樣 `.S` 檔可以寫你的新指令名、assembler 組得起來。

### 6. 加 intrinsic / builtin

讓 C 程式可以用：

```c
int result = __builtin_riscv_xmynew_add(a, b);
```

對應 LLVM 的 `include/llvm/IR/IntrinsicsRISCV.td`、GCC 的 `riscv-builtins.cc`。

### 7. 加 codegen pattern（可選）

更進階：讓 compiler 看到某種 C pattern 自動產生你的指令。例：

```c
int x = a * 3 + b;    // 某 ARM core 的 MLA 類指令
```

在 LLVM 對應到 TableGen pattern match。這是 `learn_compiler_backend` 的課題，但設計 extension 時要想到這層。

### 8. 加 scheduling model

TableGen 描述你的指令 latency / resource usage：

```tablegen
def : WriteRes<WriteXMyNew, [MyCore_ALU]> {
    let Latency = 2;
    let ResourceCycles = [1];
}
```

這讓 scheduler 能做合理的排程。**沒有 scheduling model 的 extension 會造成性能迴歸** — 一個「正確但 unscheduled」的指令可能比軟體 emulation 還慢。

### 9. 加 tests

binutils / LLVM / GCC 各有 test infrastructure：
- encoding tests（assembler 產生的 bytes 對不對）
- disassembler tests
- codegen tests（C 程式經過 pipeline 後 IR / asm 對不對）
- hardware simulation tests（spike run）

### 10. Yocto 整合

如果 SiFive 要出 BSP（Board Support Package），Yocto meta layer 裡的 GCC recipe 要 patch 你的 extension。這就是 job spec 提到的「integrate GNU toolchain recipes in Yocto/OE」。Ch 19 會帶到。

## 兩個範式：「加值」vs「加加速」

大部分 custom extension 落在兩類：

### A. Value-add（補功能）

目的：做到 base ISA 做不到或很慢的事。

例：
- 加一條 `xcrc32` 指令給某通訊協議用
- 加一組 DSP 指令做 saturating arithmetic
- 加一條 `xbit_reverse` 給特定 FFT 用

設計要點：**正交性 + 可組合**。新指令要能跟現有 ALU / pipeline 自然融合、不破壞 register allocator 假設。

### B. Accelerator（加速引擎）

目的：用一塊獨立硬體 block 做某個 domain 的工作，用特殊指令觸發。

例：
- AI 加速器：`xmatmul` 指令，後面接一塊 systolic array
- Crypto：`xaes_enc` 指令，接硬體 AES engine
- String search：`xstrfind` 指令，接 pattern matching engine

設計要點：**跟 pipeline 解耦 + 合理的 latency 隱藏**。通常走 co-processor 模式、可能有專屬 state（cop CSR）。

SiFive 的 "Intelligence" 家族偏 B 類。

## 一個具體例子：設計「mask-based XOR」

假設你要加一條 `xmxor rd, rs1, rs2, mask_reg` — 對 rs1 與 rs2 做 XOR，但只有 mask bit=1 的 byte 才做，其他 byte 保持 rs1 的值。

### 語意定義

```
for each byte i in 0..XLEN/8:
    if mask[i] == 1:
        rd.byte[i] = rs1.byte[i] ^ rs2.byte[i]
    else:
        rd.byte[i] = rs1.byte[i]
```

### 格式選擇

需要 3 個 register + 一個 mask register = 4 個 operand。標準 R-type 只給 3 個。選項：

- 固定 mask 在 `x0` 或某個特殊 reg（省 bit）— 不靈活
- 用 R4-type（funct2 空間容納 rs3） — 但 rs3 通常是第 3 個 integer reg
- 加一個新 CSR 存 mask，指令前先設 mask — 類似 RVV 的 vtype

**選項決定依賴你的 pipeline 假設**。這類 trade-off 是 custom extension 設計的日常。

### Opcode 分配

假設你選 R-type，放在 `custom-0` (opcode = 0001011)：

```
 31        25 24  20 19  15 14 12 11  7 6      0
┌───────────┬──────┬──────┬─────┬─────┬────────┐
│ funct7    │ rs2  │ rs1  │f3  │ rd  │0001011│  R-type custom-0
└───────────┴──────┴──────┴─────┴─────┴────────┘
```

挑一組沒被其他 custom extension 用的 funct3 + funct7（公司內部 registry）。

### Intrinsic 命名

```c
uint64_t __riscv_xmxor(uint64_t a, uint64_t b, uint64_t mask);
```

## `learn_compiler_backend` 的預告

實作 Step 5–8 是 compiler backend 的主題。關鍵檔案：

**LLVM:**
- `llvm/lib/Target/RISCV/RISCV.td` — 宣告新 extension 的 SubTargetFeature
- `llvm/lib/Target/RISCV/RISCVInstrInfoXMyNew.td` — 新指令的 TableGen 定義
- `llvm/lib/Target/RISCV/AsmParser/RISCVAsmParser.cpp` — 解析語法
- `llvm/lib/Target/RISCV/Disassembler/RISCVDisassembler.cpp` — 反組譯

**GCC:**
- `gcc/config/riscv/riscv-opts.h` — extension flag
- `gcc/config/riscv/riscv.md` — machine description
- `gcc/config/riscv/riscv-builtins.cc` — intrinsic 定義

這裡先建立概念，細節留到 `learn_compiler_backend`。

## Extension 的命名與 versioning

自 2022 spec 後，extension 命名規範：

```
format:  <prefix><name><major>p<minor>
examples:
  Zbb1p0           標準 Zbb v1.0 (ratified)
  Zvfh0p3          Zvfh v0.3 (draft)
  XSfVector2p5     SiFive XSfVector v2.5 (vendor)
```

小數版本 = draft / preview。整數版本 = ratified（標準）或 released（vendor）。

`-march=` 可以帶版本：

```
-march=rv64gc_zbb1p0_xsfvector2p5
```

通常省略，toolchain 用預設。版本號只在向下相容出問題時才需要指定。

## 跟硬體團隊的介面

作為 compiler 工程師，跟硬體設計師會反覆討論的幾個議題：

### 1. Register clobbers

「這條新指令會動到哪些 register？」如果動到 `ra` 或某個 CSR，compiler 要知道不能假設它們跨 call 保留。

### 2. Exception behavior

「這條指令會不會 trap？什麼情況？」如果可能 trap，compiler 做 speculation 就要小心。例：某些 atomic variants 在特定對齊會 trap。

### 3. Latency / throughput

「這條指令幾 cycle 出結果？可不可以 pipeline？下一條指令用到它的結果要等多久？」這些是 scheduling model 的輸入。

### 4. Memory ordering

「它跟 memory access 的順序是什麼？」Ch 14 / 15 會深入。RVWMO 是預設，但 custom 指令可能有特殊規則。

### 5. Interrupt timing

「這條指令中間能不能被中斷？如果指令很長（vector 類），spec 是要求 precise exception 還是 delegatable？」

沒有這些資訊，**compiler 產生的 code 可能正確但慢、或甚至在特定情境下錯**。這是 SiFive 那類 job spec 「work with benchmarking team」要處理的「跟硬體對齊」的日常。

## 兩個真實的 war story

### Story 1：SiFive 某家客戶的 crypto extension

客戶要加 AES round function 一條指令。設計時忽略了「這條要不要影響 rounding mode」— 結果 compiler 把它視為 FP-neutral，scheduler 把它調到 `fcsr` 設定指令之前，數值結果對不上。修復：加 explicit dependency on `fcsr`。

教訓：**任何新指令的 side effect 都要列完整**，不只整數 register。

### Story 2：RVV 的 vsetvl 插入

LLVM 早期對 RVV，在 inline loop 裡 insert 太多 vsetvl。每次重新設 vtype → pipeline flush → 性能掉 30%。修 pass 做 cross-BB dataflow，認出哪些地方 vtype 沒變、可以省 vsetvl。

教訓：**compiler 的 pass 有時要以 ISA 特性為中心重新設計**。這類 insight 往往需要同時懂硬體跟 compiler。

## 對 custom extension 的業界態度

RISC-V 社群有兩派：

**寬鬆派**：客戶愛加就加，反正 opcode 空間夠用。生態多元、百花齊放。

**嚴謹派**：custom 太多會 fragmentation，寫 binary 的人不知道能用什麼。應該鼓勵用 profile。

SiFive 的商業模式偏寬鬆派（為客戶做客製核心是收入來源）。但他們也是 profile 的積極貢獻者。**兩邊都要懂才能在 SiFive 做事**。

## 常見誤會

1. **「custom 指令會被未來標準取代」**：不會。opcode 明確分隔。你加的永遠是你的，除非你用錯 opcode slot。
2. **「一個好 extension = 效能高的指令」**：不。效能高但 compiler 用不上 = 無意義。要能「natural integrate」到 compiler 的 pattern matching 才是好設計。
3. **「custom extension 一定 proprietary」**：不。很多 custom extension 會 open spec（例：T-Head 的 XTHead 系列全部公開），讓其他 vendor 或 compiler 支援。
4. **「加 extension 只是軟硬體實作」**：遠不止。還有 test、documentation、toolchain integration、customer training。一個商業 extension 要 0.5–2 年才成熟。
5. **「只有 CPU 廠做 custom extension」**：錯。系統廠（汽車、航太、醫療）也做，但他們通常委託 SiFive / Andes 設計。

## 動手練習

1. 想像一個你熟悉領域的加速：假設你是網路晶片工程師，設計一條 `xpktcrc` 指令做乙太網 CRC。寫 1 頁 spec。
2. 讀 SiFive 的 `XSfVector` spec（<https://github.com/sifive/sifive-intelligence-extensions-specifications>），找三條指令說明它們要解決什麼問題。
3. 在 LLVM 的 `RISCVInstrInfoXSf*.td` 看 SiFive 如何把一個 vendor extension 塞進 compiler。不求全懂，但要認出 `def` / `Sched<...>` / `PatSubst` 等關鍵字。
4. 用 spike 的 `--extension` flag 載入一個 custom extension plugin。spike 支援讓你在 C++ 寫 semantic、動態載入。（需要看 spike 源碼的 `customext/` 資料夾）。
5. 寫一個「最小可 demo」的 custom extension：在 spike 加一條 `xmyadd rd, rs1, rs2` 等於 `add rd, rs1, rs2` 的 alias。從 opcode 設計、spike plugin、到 binutils assembler parse，全部走一次。這是面試準備的最強作品之一。

## 自我檢核

- [ ] 我能列出 custom-0..3 的 opcode 空間
- [ ] 我能走完「設計一個 extension」的 10 步流程
- [ ] 我知道 value-add vs accelerator 兩種範式差異
- [ ] 我能說出 custom extension 影響 compiler 的三個面向（assembler / intrinsic / scheduling）
- [ ] 我能讀 `-march=rv64gc_xsfvector2p5` 這種字串並拆字

下一章實際看業界的廠商擴充：SiFive Intelligence、XuanTie、Andes、Vector Crypto 各自解決什麼問題、編碼風格如何。

→ [Ch 12 SiFive Intelligence / XuanTie / Vector Crypto 巡禮](./12-vendor-extensions.md)
