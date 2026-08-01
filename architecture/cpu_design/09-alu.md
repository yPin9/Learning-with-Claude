# Ch 9 — ALU 與 ALU control

> **目標**：親手做出 RV32I 的算術邏輯單元（ALU，arithmetic logic unit）。你會按課程約定的 alu_op 編碼實作全部運算（ADD/SUB/SLL/SLT/SLTU/XOR/SRL/SRA/OR/AND）、產生 zero flag、搞懂 SLT vs SLTU 的有號無號差異和三種移位（SLL/SRL/SRA）的區別，用 C++ testbench 逐 op 驗證真跑。最後點一下 alu_control 怎麼從 funct3/funct7 對到 alu_op（細節留給 Ch 10）。
> **環境**：WSL + verilator 4.038。輸出皆真跑。

## 為什麼需要 ALU？

Ch 6 的資料流最終匯進一個方塊：ALU。它是 CPU 真正「算東西」的地方。看看哪些指令要靠它：

- **算術**：`add`、`sub`、`addi`——加減。
- **邏輯**：`and`、`or`、`xor`——位元運算。
- **比較**：`slt`、`sltu`——小於就給 1。分支指令 `beq`/`blt` 也靠 ALU 比較。
- **移位**：`sll`、`srl`、`sra`——左移、邏輯右移、算術右移。
- **位址計算**：`lw`/`sw` 的位址 = 基底暫存器 + 立即數，也是 ALU 做加法。
- **分支判斷**：`beq` 要判兩數是否相等，ALU 做減法看結果是不是 0（zero flag）。

幾乎每條指令都會經過 ALU。它是單一、通用的計算引擎——換一個 **alu_op**（操作碼），同一塊硬體就做不同運算。這正是 Ch 6「datapath 通用、control 選功能」的最純粹體現。

## 先建立直覺：一台會切換模式的計算機

把 ALU 想成一台有兩個輸入槽、一個功能轉盤的計算機：

```
      a ───┐
           │   ┌──────────────────┐
           ├──▶│                  │
           │   │       ALU        │──▶ result (算出來的值)
           ├──▶│  (功能轉盤選)    │
      b ───┘   │                  │──▶ zero (result 是不是 0?)
               └────────▲─────────┘
                        │
                    alu_op (轉盤：現在要做哪種運算?)
                   0000=加 0001=減 0010=左移 ...
```

- 兩個 32-bit 輸入 `a`、`b`。
- 一個 4-bit `alu_op` 當轉盤，選十種運算之一。
- 一個 32-bit `result` 輸出算出的值。
- 一個 1-bit `zero` 旗標：result 是不是 0？（分支指令要用）

轉盤轉到「加」，它就是加法器；轉到「小於」，它就是比較器。**同一塊矽，靠 alu_op 切換人格**。ALU 是純組合邏輯——輸入一給，輸出瞬間出來，沒有 clock、沒有記憶。

## 核心概念：課程約定的 alu_op 編碼

全課共用這張表（其他章的 module 也照它，不可自創）：

| alu_op | 運算 | 意義 | 對應指令舉例 |
|---|---|---|---|
| 0000 | ADD | a + b | add, addi, lw/sw 位址 |
| 0001 | SUB | a - b | sub, beq（比較） |
| 0010 | SLL | a << b[4:0] | sll, slli |
| 0011 | SLT | 有號 a < b ? 1 : 0 | slt, slti, blt |
| 0100 | SLTU | 無號 a < b ? 1 : 0 | sltu, sltiu, bltu |
| 0101 | XOR | a ^ b | xor, xori |
| 0110 | SRL | 邏輯右移 a >> b[4:0] | srl, srli |
| 0111 | SRA | 算術右移 a >>> b[4:0] | sra, srai |
| 1000 | OR | a \| b | or, ori |
| 1001 | AND | a & b | and, andi |

三個要特別想通的點：**有號/無號比較**、**三種移位**、**移位量只取低 5 bit**。下一節底層機制展開。

## 底層機制：三個容易錯的細節

### 細節 1：SLT vs SLTU——有號還是無號

`slt`（set less than）是有號比較，`sltu` 是無號比較。同樣兩個 bit pattern，解讀成有號還是無號，大小關係可能相反：

```
   a = 0xFFFFFFFF, b = 0x00000001

   有號 (SLT)：a = -1, b = +1  →  -1 < 1 為真 → result = 1
   無號 (SLTU)：a = 4294967295, b = 1  →  4294967295 < 1 為假 → result = 0
```

同一組 bit，有號說「a 比較小」，無號說「a 比較大」。硬體上：SLTU 直接拿兩個 32-bit 當無號比；SLT 要把它們當**二補數**比。SystemVerilog 用 `$signed()` 強制有號比較：

```systemverilog
4'b0011: result = ($signed(a) < $signed(b)) ? 32'd1 : 32'd0; // SLT 有號
4'b0100: result = (a < b) ? 32'd1 : 32'd0;                    // SLTU 無號
```

`logic` 預設無號，所以 `a < b` 就是無號比；要有號得包 `$signed()`。搞錯這個，你的 `blt`（有號分支）會在遇到負數時判斷相反，是很難抓的 bug。

### 細節 2：三種移位——SLL / SRL / SRA

```
   SLL (邏輯左移):   1011_0000 << 2  =  1100_0000   (右邊補 0)
   SRL (邏輯右移):   1011_0000 >> 2  =  0010_1100   (左邊補 0)
   SRA (算術右移):   1011_0000 >>>2  =  1110_1100   (左邊補「符號位」1)
                     ^ 符號位是 1，所以補 1 進來，保持負數
```

- **SLL / SRL** 是邏輯移位，空出來的位補 0。
- **SRA** 是算術右移，空出來的位補**原本的符號位**（最高位）。這樣對負數（二補數）右移才等於「除以 2 取整」的正確結果。若對負數用邏輯右移（補 0），數值會突然變成大正數，算術意義就錯了。

SystemVerilog 的算術右移運算子是 `>>>`，而且**運算元必須是有號的**才會補符號位。所以 SRA 要包 `$signed()`：

```systemverilog
4'b0010: result = a << shamt;              // SLL 補 0
4'b0110: result = a >> shamt;              // SRL 補 0（a 是 logic 無號，>> 補 0）
4'b0111: result = $signed(a) >>> shamt;    // SRA 補符號（要 $signed 才生效）
```

注意：對無號的 `a` 用 `>>>` 一樣補 0（等同 `>>`），所以 SRA 一定要先 `$signed(a)`，`>>>` 才會補符號位。這是 SystemVerilog 一個經典陷阱。

### 細節 3：移位量只取低 5 bit

RV32I spec 規定：移位量只看 rs2（或 shamt 欄位）的**低 5 bit**。因為 32-bit 的數移超過 31 位沒意義（全移光了），5 bit 剛好表示 0~31。

```systemverilog
logic [4:0] shamt;
assign shamt = b[4:0];   // 只取低 5 bit
```

如果不取低 5 bit、直接用整個 b 當移位量，那 `sll x1, x2, x3` 當 x3=36 時，硬體行為會和 spec 不符（spec 說 36 & 31 = 4，移 4 位）。這是符合 ISA 的必要細節。

## 範例：完整 ALU 實作

`alu.sv`，port 嚴格照課程約定（`a, b, alu_op, result, zero`）：

```systemverilog
// alu.sv — RV32I ALU，按課程約定 alu_op 編碼
module alu (
    input  logic [31:0] a,
    input  logic [31:0] b,
    input  logic [3:0]  alu_op,
    output logic [31:0] result,
    output logic        zero
);
    // 移位量只取低 5 bit（RV32 shamt）
    logic [4:0] shamt;
    assign shamt = b[4:0];

    always_comb begin
        unique case (alu_op)
            4'b0000: result = a + b;                                     // ADD
            4'b0001: result = a - b;                                     // SUB
            4'b0010: result = a << shamt;                                // SLL
            4'b0011: result = ($signed(a) < $signed(b)) ? 32'd1 : 32'd0; // SLT
            4'b0100: result = (a < b) ? 32'd1 : 32'd0;                   // SLTU
            4'b0101: result = a ^ b;                                     // XOR
            4'b0110: result = a >> shamt;                                // SRL
            4'b0111: result = $signed(a) >>> shamt;                      // SRA
            4'b1000: result = a | b;                                     // OR
            4'b1001: result = a & b;                                     // AND
            default: result = 32'd0;
        endcase
    end

    assign zero = (result == 32'd0);
endmodule
```

要點：

- 純 `always_comb`——ALU 沒有 clock、沒有記憶，是組合邏輯。
- `unique case` 告訴工具「這些分支互斥且覆蓋預期輸入」，幫抓漏、也讓合成不生多餘 priority 邏輯。加 `default` 避免 latch。
- `zero` 是「result 全 0 嗎」的直接比較。它給分支邏輯用：`beq` 令 ALU 做 SUB，若 zero=1 表示兩數相等，該跳。

## 逐 op 驗證：testbench 真跑

`alu_tb.cpp`，每個運算都選了會踩到有號/無號、移位補位、溢位邊界的輸入：

```cpp
#include "Valu.h"
#include "verilated.h"
#include <cstdint>
#include <cstdio>

static Valu *dut;
static int fails = 0;

static uint32_t eval_alu(uint32_t a, uint32_t b, int op) {
    dut->a = a; dut->b = b; dut->alu_op = op;
    dut->eval();
    return dut->result;
}

static void check(const char *name, uint32_t got, uint32_t exp,
                  uint32_t zero_got, uint32_t zero_exp) {
    bool ok = (got == exp) && (zero_got == zero_exp);
    printf("[%s] %-6s result=0x%08x (exp 0x%08x) zero=%u (exp %u)\n",
           ok ? "OK " : "BAD", name, got, exp, zero_got, zero_exp);
    if (!ok) fails++;
}

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    dut = new Valu;
    uint32_t r;

    r = eval_alu(5, 7, 0x0); check("ADD", r, 12, dut->zero, 0);
    r = eval_alu(9, 9, 0x1); check("SUB", r, 0, dut->zero, 1);          // 相等→zero
    r = eval_alu(1, 4, 0x2); check("SLL", r, 16, dut->zero, 0);
    r = eval_alu(1, 36, 0x2); check("SLLmask", r, 16, dut->zero, 0);    // 36&31=4
    r = eval_alu(0xFFFFFFFF, 1, 0x3); check("SLT", r, 1, dut->zero, 0); // -1<1 有號
    r = eval_alu(0xFFFFFFFF, 1, 0x4); check("SLTU", r, 0, dut->zero, 1);// 大數 無號
    r = eval_alu(0xF0F0F0F0, 0x0FF00FF0, 0x5); check("XOR", r, 0xFF00FF00, dut->zero, 0);
    r = eval_alu(0x80000000, 4, 0x6); check("SRL", r, 0x08000000, dut->zero, 0); // 補0
    r = eval_alu(0x80000000, 4, 0x7); check("SRA", r, 0xF8000000, dut->zero, 0); // 補符號
    r = eval_alu(0xF0, 0x0F, 0x8); check("OR", r, 0xFF, dut->zero, 0);
    r = eval_alu(0xFF, 0x0F, 0x9); check("AND", r, 0x0F, dut->zero, 0);
    r = eval_alu(0xFFFFFFFF, 1, 0x0); check("ADDwrap", r, 0, dut->zero, 1); // 溢位回繞

    printf("\n%s (%d fail)\n", fails ? "FAILED" : "ALL PASSED", fails);
    delete dut;
    return fails ? 1 : 0;
}
```

編譯執行：

```bash
verilator --cc alu.sv --exe alu_tb.cpp --Mdir obj_dir
make -C obj_dir -f Valu.mk Valu
./obj_dir/Valu
```

真實輸出：

```
[OK ] ADD    result=0x0000000c (exp 0x0000000c) zero=0 (exp 0)
[OK ] SUB    result=0x00000000 (exp 0x00000000) zero=1 (exp 1)
[OK ] SLL    result=0x00000010 (exp 0x00000010) zero=0 (exp 0)
[OK ] SLLmask result=0x00000010 (exp 0x00000010) zero=0 (exp 0)
[OK ] SLT    result=0x00000001 (exp 0x00000001) zero=0 (exp 0)
[OK ] SLTU   result=0x00000000 (exp 0x00000000) zero=1 (exp 1)
[OK ] XOR    result=0xff00ff00 (exp 0xff00ff00) zero=0 (exp 0)
[OK ] SRL    result=0x08000000 (exp 0x08000000) zero=0 (exp 0)
[OK ] SRA    result=0xf8000000 (exp 0xf8000000) zero=0 (exp 0)
[OK ] OR     result=0x000000ff (exp 0x000000ff) zero=0 (exp 0)
[OK ] AND    result=0x0000000f (exp 0x0000000f) zero=0 (exp 0)
[OK ] ADDwrap result=0x00000000 (exp 0x00000000) zero=1 (exp 1)

ALL PASSED (0 fail)
```

幾個關鍵驗證點：

- **SLLmask**：移位量 36，硬體只取低 5 bit（36 & 31 = 4），`1 << 4 = 16`，符合 spec。
- **SLT vs SLTU**：同輸入 `(0xFFFFFFFF, 1)`，SLT 給 1（有號 -1 < 1），SLTU 給 0（無號大數 > 1）。有號無號差異驗證成功。
- **SRL vs SRA**：同輸入 `(0x80000000, 4)`，SRL 補 0 得 `0x08000000`，SRA 補符號位 1 得 `0xF8000000`。三種移位區分正確。
- **ADDwrap**：`0xFFFFFFFF + 1 = 0x100000000`，截成 32 bit 是 0，zero=1。溢位靜默回繞（RV 沒有溢位 trap），邊界正確。

## alu_control：從 funct3/funct7 對到 alu_op（Ch 10 詳談）

ALU 收的是 4-bit `alu_op`，但指令裡沒有 alu_op 這個欄位——它藏在 opcode + funct3 + funct7 裡。中間需要一小塊組合邏輯 **alu_control**，把指令欄位翻譯成 alu_op。直覺是這樣：

```
   opcode 說「這是哪大類」（R-type? I-type 算術? load/store? branch?）
        +
   funct3 說「這大類裡哪個運算」（000=add/sub, 010=slt, 100=xor...）
        +
   funct7 的 bit30 區分 add/sub、srl/sra（同 funct3 靠這 bit 分家）
        ↓
   alu_control（一張真值表）
        ↓
   alu_op (4-bit) → 餵給 ALU
```

舉例：`add` 和 `sub` 的 opcode、funct3 都相同（`0110011` / `000`），差別只在 funct7 的 bit 30——add 是 0、sub 是 1。alu_control 看這個 bit 決定輸出 ADD（0000）還是 SUB（0001）。同理 srl/sra 靠同一 bit 分。而 load/store/branch 的位址或比較，opcode 直接決定用 ADD 或 SUB，不看 funct。

這張完整真值表和它跟 control_unit 的分工，Ch 10 展開。你現在只要知道：**ALU 本身只認 alu_op，翻譯工作外包給 alu_control**。這種分層讓 ALU 保持乾淨——它不需要懂 RISC-V encoding，只做「給我 op 我就算」。

## 對比取捨

| 設計選擇 | 本課做法 | 替代方案 | 理由 |
|---|---|---|---|
| alu_op 寬度 | 4-bit（10 種運算） | 更寬（塞 M 擴充乘除） | RV32I 基本運算 4-bit 夠；乘除是另一塊（Part 5 可選） |
| SLT/SLTU 實作 | `$signed()` 分流 | 手動處理符號位 | `$signed()` 清楚、合成器認得 |
| SRA | `$signed(a) >>> shamt` | 手動符號延伸 | `>>>` + `$signed` 是慣用法，別手刻 |
| zero flag | 位置在 ALU 內 | 放外面單獨比較 | 分支要它，放 ALU 內最近、最省線 |
| case 型別 | `unique case` + default | 普通 case | unique 幫抓漏、避免 latch，合成更好 |

## 踩雷區

**雷 1：SRA 忘了 `$signed()`，補成 0。**
- 錯誤直覺：「用 `>>>` 就是算術右移了」。
- 正確認識：SystemVerilog 的 `>>>` 只有在**運算元是有號**時才補符號位。`logic [31:0] a` 預設無號，`a >>> shamt` 一樣補 0（等同 `>>`）。必須 `$signed(a) >>> shamt` 才會補符號。這是最常見的 ALU bug，`0x80000000 >>> 4` 會錯得到 `0x08000000` 而非 `0xF8000000`。

**雷 2：SLT 忘了 `$signed()`，用無號比。**
- 錯誤直覺：「`a < b` 就是小於比較」。
- 正確認識：`logic` 預設無號，`a < b` 是無號比。SLT 要有號，必須 `$signed(a) < $signed(b)`。少了它，`slt` 遇到負數（高位是 1，無號看來是大數）會判斷相反，你的 `blt` 分支就跳錯。SLT 和 SLTU 差就差在這個 `$signed()`。

**雷 3：移位量沒取低 5 bit。**
- 錯誤直覺：「移位量就是整個 b」。
- 正確認識：RV32I 規定移位量只看低 5 bit（0~31）。`sll x1,x2,x3` 當 x3=36，spec 要求移 `36 & 31 = 4` 位。不 mask 的話，硬體用 36 去移（在某些語意下全移光變 0），和 spec 不符。永遠 `b[4:0]`。

**雷 4：以為 RV 加減法溢位會出錯或 trap。**
- 錯誤直覺：「`0xFFFFFFFF + 1` 溢位了，會不會壞掉或報錯」。
- 正確認識：RV32I 的 add/sub **靜默回繞**（wrap around），沒有溢位旗標、沒有 trap。`0xFFFFFFFF + 1` 就是 `0`（截 32 bit）。這是 ISA 設計——溢位檢查若需要，由軟體用 SLT 之類自己做。所以 ALU 加法器不用管溢位，截到 32-bit 就對了。

## 進階延伸

- **zero flag 只是分支的一半**：`beq` 用 SUB + zero 判相等，`bne` 用同樣的 zero 取反。但 `blt`/`bltu`/`bge`/`bgeu` 需要的是「小於」而非「等於」，那用 SLT/SLTU 的結果（result 的最低位），不是 zero。所以有些設計 ALU 除了 zero 還吐一個「less than」訊號。本課我們讓 branch 邏輯（Ch 11）自己用 SUB 的 zero 和 SLT 的 result 組合出各種分支條件，ALU 只提供原料。
- **加法器是關鍵路徑常客**：ALU 的加法（32-bit 進位鏈）往往是整顆單週期 CPU 最長的組合路徑之一，直接決定 clock 能多快。真設計會用 carry-lookahead、carry-select 之類加速進位。本課用 `a + b` 讓合成器自己選加法器結構，教學不深挖，但你要知道「ALU 加法慢 = clock 慢」是 Part 3 效能分析（Ch 24 關鍵路徑）的重點。
- **一個加法器做加也做減**：`a - b` 硬體上是 `a + (~b) + 1`（二補數）。真 ALU 常只放一個加法器，用一根 control bit 決定 b 要不要取反加一，加減共用。本課寫成 `a + b` 和 `a - b` 兩行讓合成器處理，但底層它會共用加法器。SLT 的有號比較其實也常靠減法的結果符號位算出來——ALU 內部運算高度共用。
- **M 擴充在哪**：乘除（`mul`/`div`/`rem`）不在 RV32I 基本集，是 M 擴充。它們比加減複雜得多（乘法要多級、除法要多 cycle），通常不塞進這個單 cycle ALU，而是獨立模組甚至多週期。本課主線純 RV32I，M 擴充留待需要時另接。

## 本章重點整理

- ALU 是 CPU 通用計算引擎，純組合邏輯，靠 4-bit `alu_op` 切換十種運算（ADD/SUB/SLL/SLT/SLTU/XOR/SRL/SRA/OR/AND）。
- 三大易錯細節：**SLT/SLTU** 靠 `$signed()` 分有號無號；**SRA** 必須 `$signed(a) >>> shamt` 才補符號位（否則補 0）；**移位量**只取 `b[4:0]`（低 5 bit）。
- `zero` flag 是 `result == 0`，給分支指令（`beq` 做 SUB 看 zero）用。
- RV 加減法**靜默回繞**，無溢位 trap，ALU 不用管溢位。
- ALU 只認 alu_op，不懂 RISC-V encoding；把 funct3/funct7 翻成 alu_op 的工作外包給 **alu_control**（Ch 10 詳談）。
- 全部運算逐 op 真跑驗證通過，涵蓋有號無號、三種移位、溢位邊界。

## 自我檢核

- [ ] 我能背出全課 alu_op 編碼表，並說出每個運算對應哪類指令。
- [ ] 我能解釋 SLT 和 SLTU 對同一組 bit 為何可能給相反結果，並寫出用 `$signed()` 分流的兩行 code。
- [ ] 我能說清楚 SLL/SRL/SRA 三者差在補什麼位，以及為什麼 SRA 一定要 `$signed()`。
- [ ] 我能解釋移位量為什麼取低 5 bit，並算出 `sll` 移位量 36 時實際移幾位。
- [ ] 我能說明 zero flag 給誰用、`beq` 怎麼靠它判斷。
- [ ] 我能講出 ALU 和 alu_control 的分工，以及 add/sub 靠哪個 bit 分家。

## 延伸閱讀

- **《Computer Organization and Design, RISC-V Edition》(Patterson & Hennessy) 附錄 A「The Basics of Logic Design」的 ALU 節，以及第 4.4 節 ALU control**：附錄從 1-bit ALU 疊到 32-bit，講清楚加減共用加法器、zero flag 怎麼來；4.4 節正是本章末尾 alu_control 的完整版，Ch 10 前先讀它。
- **《Digital Design and Computer Architecture, RISC-V Edition》(Harris & Harris) 第 5.2 節「Arithmetic Circuits」**：把加法器、減法器、比較器、移位器的閘級結構講透，讓你知道 `a + b` 一行 RTL 底下是什麼、為什麼加法是關鍵路徑。
- **[RISC-V Unprivileged ISA Spec](https://riscv.org/technical/specifications/) 第 2.4「Integer Computational Instructions」**：權威定義每個運算的精確語意，特別確認移位量取低 5 bit、SRA 補符號、加減回繞這些細節。你 ALU 行為有疑義時的最終仲裁。
- **[picorv32 原始碼](https://github.com/YosysHQ/picorv32) 搜 `alu_out` 附近**：看一個真 core 怎麼把 ALU 寫在一起（它甚至和 shifter 分開處理以省關鍵路徑）。對照你會發現「教學把運算全塞一個 case」和「真設計為時序把某些運算拆出去」的取捨。

ALU 會算了、regfile 會存了、fetch 會抓了。但誰指揮它們？下一章我們做 control unit 和 immediate generator——把指令翻譯成所有 control signal 的領班。

→ [Ch 10 Control Unit + immediate generator](./10-control-unit-immediate.md)
