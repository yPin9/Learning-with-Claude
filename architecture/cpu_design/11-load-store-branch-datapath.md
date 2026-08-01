# Ch 11 — Load/Store/Branch/Jump datapath

> **目標**：把單週期 datapath 的最後幾塊拼齊——**資料記憶體 dmem**（byte 定址，支援 LB/LH/LW/LBU/LHU 的有號/無號延伸與 SB/SH/SW 的部分寫入）、**branch unit**（六條分支 BEQ/BNE/BLT/BGE/BLTU/BGEU 的比較）、以及 **branch/jump 的 target 計算與 next-PC mux**（JAL/JALR 的 pc+imm 與 rs1+imm）。每塊寫 C++ testbench 真跑，涵蓋 sign/zero extend、部分寫入合併、有號無號分支的邊界。
> **環境**：WSL + verilator 4.038。本章所有輸出都是真跑出來的。

## 為什麼需要這幾塊？

到 Ch 10 為止，datapath 能做的只有「暫存器 → ALU → 寫回暫存器」的算術指令，而且 PC 只會一直線 +4。真實程式離不開三件事：

- **存取記憶體**：`lw`/`sw` 把資料在暫存器和記憶體間搬。C 語言的每個指標解參考、每次陣列存取，底層都是 load/store。
- **條件跳轉**：`if`、`for`、`while` 編譯成 `beq`/`blt` 這類分支——依比較結果決定 PC 是 +4 還是跳去別處。
- **函式呼叫返回**：`jal`（呼叫）存返回位址、`jalr`（返回）跳回去。

這三類指令的共通點是：它們要**改變 datapath 的資料流向或 PC 走向**。load/store 多接一塊 dmem；branch 要一個比較器 + 一個 target 加法器 + 一個 PC mux；jump 要算 target 並把 pc+4 存進暫存器。這章把這些補上，單週期 datapath 就完整了。

## 先建立直覺：三條新的資料流

Ch 6 的全景圖裡，PC 前面有個 mux、ALU 後面接記憶體。這章就是把那幾條線畫實：

```
                    ┌──── pc+4 ────┐
                    │              │
   ┌────┐  pc   ┌───┴───┐          ▼
   │ PC │──────▶│ +imm  │──▶ target ┌──────┐
   └─▲──┘       └───────┘           │next- │──▶ pc_next
     │       branch_unit take ─────▶│PC mux│
     │       jump / jalr target ───▶└──────┘
     │
  （branch 成立或 jump → 換 target；否則 pc+4）

   rs1 ─┐
        ├─ALU(ADD)─▶ addr ─┐
   imm ─┘                  ▼
                     ┌───────────┐
   rs2 ─────────────▶│   dmem    │──▶ rdata ─(延伸)─▶ 寫回暫存器
                     │(byte定址) │
                     └───────────┘
                     funct3 決定 byte/half/word + 有號無號
```

三個關鍵動作：

- **位址計算**：load/store 的位址 = `rs1 + imm`，借用 ALU 的 ADD（Ch 10 control unit 已讓它們 alu_op=ADD、alu_src=imm）。
- **記憶體存取**：dmem 依 funct3 決定搬幾個 byte、讀出來要不要符號延伸。
- **PC 改道**：branch 成立或 jump，把 pc_next 從 pc+4 換成 target。

## 核心概念：dmem 為什麼要 byte 定址

Ch 7 的 imem 我們做成「一格一個 word」，因為指令一律 4-byte 對齊、每次抓整條。但 dmem 不行——RV32I 有 **byte（LB/SB）、half（LH/SH）、word（LW/SW）** 三種粒度的存取。`lb x1, 0(x2)` 只讀 1 個 byte，`lh` 讀 2 個，`lw` 讀 4 個。所以 dmem 要能精準定位到 byte，並依 funct3 決定：

- 讀幾個 byte（1/2/4）。
- 讀出來要不要**符號延伸**（LB/LH 有號，補符號位；LBU/LHU 無號，補 0）。
- 寫的時候只改動對應的 byte/half，其餘 byte **保留不變**（SB 不能把整個 word 覆蓋掉）。

funct3 的編碼（RV32I load/store）：

| funct3 | load | store | 粒度 | 延伸 |
|---|---|---|---|---|
| 000 | LB | SB | byte | 有號 |
| 001 | LH | SH | half | 有號 |
| 010 | LW | SW | word | — |
| 100 | LBU | — | byte | 無號 |
| 101 | LHU | — | half | 無號 |

我們把 dmem 內部仍以 word 陣列存放（好對齊、好 `$readmemh`），但用 **byte offset** 從 word 裡切出要的部分。位址的低 2 bit（`addr[1:0]`）就是「這個 word 裡的第幾個 byte」。

## 底層機制：little-endian 的 byte 選取

RV32I 是 **little-endian**（小端）——一個 word 的最低位 byte 存在最小位址。所以位址 `0` 是 word 的 bit[7:0]、位址 `1` 是 bit[15:8]、位址 `2` 是 bit[23:16]、位址 `3` 是 bit[31:24]。

```
   一個 word = 0x12345678 存在位址 0~3：

   位址:    0     1     2     3
   byte:  0x78  0x56  0x34  0x12     ◀── little-endian，低 byte 在低位址
   bit:  [7:0] [15:8][23:16][31:24]

   lbu x1, 0(x0)  → 讀位址 0 那個 byte = 0x78
   lbu x1, 3(x0)  → 讀位址 3 那個 byte = 0x12
```

從 word 裡切 byte，用 `word[boff*8 +: 8]`（SystemVerilog 的 part-select：從 `boff*8` 開始取 8 bit）。切 half 則看 `boff[1]`（0=低半 bit[15:0]、2=高半 bit[31:16]）。

**部分寫入**（SB/SH）用 mask 合併：讀出原 word、把要改的 byte/half 位置清掉、填入新值、寫回。公式是 `(舊word & ~mask) | (新值 & mask)`。

## 範例 1：dmem 完整實作

`dmem.sv`：

```systemverilog
// dmem.sv — 資料記憶體，byte 定址，支援 LB/LH/LW/LBU/LHU 與 SB/SH/SW
module dmem #(
    parameter WORDS = 256               // 256 words = 1 KiB
) (
    input  logic        clk,
    input  logic [31:0] addr,           // byte 位址（已算好 = rs1 + imm）
    input  logic [2:0]  funct3,         // 決定 byte/half/word 與有號無號
    input  logic        mem_read,
    input  logic        mem_write,
    input  logic [31:0] wdata,          // 要寫的資料（來自 rs2）
    output logic [31:0] rdata           // 讀出並延伸後的資料
);
    // 內部以 word 為單位存放
    logic [31:0] mem [0:WORDS-1];
    initial $readmemh("data.hex", mem);

    logic [$clog2(WORDS)-1:0] windex;   // word index
    logic [1:0]               boff;     // byte offset within word
    assign windex = addr[$clog2(WORDS)+1:2];
    assign boff   = addr[1:0];

    logic [31:0] word;                  // 這個位址所在的整個 word
    assign word = mem[windex];

    // === 讀取：依 funct3 抽出 byte/half/word 並延伸 ===
    logic [7:0]  rb;   // 選出的 byte
    logic [15:0] rh;   // 選出的 half
    assign rb = word[boff*8 +: 8];
    assign rh = boff[1] ? word[31:16] : word[15:0];

    always_comb begin
        unique case (funct3)
            3'b000: rdata = {{24{rb[7]}},  rb};   // LB  有號
            3'b001: rdata = {{16{rh[15]}}, rh};   // LH  有號
            3'b010: rdata = word;                 // LW
            3'b100: rdata = {24'b0, rb};          // LBU 無號
            3'b101: rdata = {16'b0, rh};          // LHU 無號
            default: rdata = word;
        endcase
    end

    // === 寫入：同步，依 funct3 只改動對應 byte/half，其餘保留 ===
    logic [31:0] wmask;    // 要寫的位元遮罩
    logic [31:0] wval;     // 對齊後要寫入的值
    always_comb begin
        unique case (funct3)
            3'b000: begin // SB
                wmask = 32'hFF        << (boff*8);
                wval  = (wdata & 32'hFF)   << (boff*8);
            end
            3'b001: begin // SH
                wmask = 32'hFFFF      << (boff[1]*16);
                wval  = (wdata & 32'hFFFF) << (boff[1]*16);
            end
            default: begin // SW
                wmask = 32'hFFFFFFFF;
                wval  = wdata;
            end
        endcase
    end

    always_ff @(posedge clk) begin
        if (mem_write)
            mem[windex] <= (word & ~wmask) | (wval & wmask);
    end

    // mem_read 目前沒改變組合讀行為（單週期組合讀），保留埠以備擴充
    logic _unused;
    assign _unused = mem_read;
endmodule
```

要點：

- **讀是組合、寫是同步**——和 regfile 一樣的分工。單週期一 cycle 內要「算位址 → 讀/寫記憶體 → 寫回暫存器」，讀必須即時。
- **符號延伸**：LB 用 `{{24{rb[7]}}, rb}`（複製 byte 的最高位 rb[7] 24 份），LBU 用 `{24'b0, rb}`（補 0）。這是 Ch 9 學過的符號 vs 零延伸，換到記憶體讀取。
- **部分寫入**：`(word & ~wmask) | (wval & wmask)`——先把 word 上要改的位清掉，再蓋上新值。SB 到 byte 1 只動 bit[15:8]，其餘三個 byte 原封不動。
- `mem_read` 這個埠本課組合讀時沒實際用到（讀永遠算好放 rdata），但保留它對齊 control unit 的訊號，也方便之後改同步記憶體時派上用場。留個 `_unused` assign 讓 verilator 不警告未用輸入。

## 範例 2：dmem testbench，涵蓋所有 load/store 變體

`dmem_tb.cpp`，特別測 sign/zero extend 的分野和部分寫入的合併：

```cpp
#include "Vdmem.h"
#include "verilated.h"
#include <cstdint>
#include <cstdio>
static Vdmem *dut;
static int fails = 0;
static void tick() { dut->clk = 0; dut->eval(); dut->clk = 1; dut->eval(); }
static void store(uint32_t addr, uint32_t f3, uint32_t data) {
    dut->addr = addr; dut->funct3 = f3; dut->wdata = data;
    dut->mem_write = 1; dut->mem_read = 0; dut->eval();
    tick(); dut->mem_write = 0;
}
static uint32_t load(uint32_t addr, uint32_t f3) {
    dut->addr = addr; dut->funct3 = f3; dut->mem_read = 1; dut->mem_write = 0; dut->eval();
    return dut->rdata;
}
static void chk(const char *n, uint32_t g, uint32_t e) {
    bool ok = g == e;
    printf("[%s] %-8s got=0x%08x (exp 0x%08x)\n", ok ? "OK " : "BAD", n, g, e);
    if (!ok) fails++;
}
int main(int c, char **v) {
    Verilated::commandArgs(c, v);
    dut = new Vdmem;
    dut->mem_write = 0; dut->mem_read = 0;

    // SW 0x12345678 到位址 0，讀回 LW
    store(0, 0b010, 0x12345678);
    chk("SW/LW", load(0, 0b010), 0x12345678);
    // LBU 位址 0 => 0x78; 位址 3 => 0x12 (little-endian)
    chk("LBU@0", load(0, 0b100), 0x78);
    chk("LBU@3", load(3, 0b100), 0x12);
    // LB 位址 0 => 0x78 (正)
    chk("LB@0", load(0, 0b000), 0x78);
    // 寫一個低位元組是 0xF0 的 word，測 LB 有號延伸
    store(4, 0b010, 0x000000F0);
    chk("LB-neg", load(4, 0b000), 0xFFFFFFF0); // 0xF0 = -16 有號延伸
    chk("LBU-pos", load(4, 0b100), 0x000000F0);
    // LH / LHU：寫 0x8001，LH 應延伸為負
    store(8, 0b010, 0x00008001);
    chk("LH-neg", load(8, 0b001), 0xFFFF8001);
    chk("LHU-pos", load(8, 0b101), 0x00008001);
    // SB：只改一個 byte，其餘保留。先鋪 0xAAAAAAAA，再 SB 0xBB 到 byte 1
    store(12, 0b010, 0xAAAAAAAA);
    store(13, 0b000, 0xBB);
    chk("SB-merge", load(12, 0b010), 0xAAAABBAA);
    // SH：改高半，保留低半
    store(16, 0b010, 0x11112222);
    store(18, 0b001, 0x3333);
    chk("SH-merge", load(16, 0b010), 0x33332222);

    printf("\n%s (%d fail)\n", fails ? "FAILED" : "ALL PASSED", fails);
    delete dut;
    return fails ? 1 : 0;
}
```

`data.hex` 先全填 0（256 行 `00000000`）當初值。編譯執行：

```bash
python3 -c 'open("data.hex","w").write(("00000000\n")*256)'
verilator --cc dmem.sv --exe dmem_tb.cpp --Mdir obj_dir
make -C obj_dir -f Vdmem.mk Vdmem
./obj_dir/Vdmem
```

真實輸出：

```
[OK ] SW/LW    got=0x12345678 (exp 0x12345678)
[OK ] LBU@0    got=0x00000078 (exp 0x00000078)
[OK ] LBU@3    got=0x00000012 (exp 0x00000012)
[OK ] LB@0     got=0x00000078 (exp 0x00000078)
[OK ] LB-neg   got=0xfffffff0 (exp 0xfffffff0)
[OK ] LBU-pos  got=0x000000f0 (exp 0x000000f0)
[OK ] LH-neg   got=0xffff8001 (exp 0xffff8001)
[OK ] LHU-pos  got=0x00008001 (exp 0x00008001)
[OK ] SB-merge got=0xaaaabbaa (exp 0xaaaabbaa)
[OK ] SH-merge got=0x33332222 (exp 0x33332222)

ALL PASSED (0 fail)
```

逐項核對：

- **LBU@0 vs LBU@3**：同一個 word `0x12345678`，位址 0 讀到 `0x78`（最低 byte）、位址 3 讀到 `0x12`（最高 byte）。little-endian 的 byte 選取正確。
- **LB-neg vs LBU-pos**：同一 byte `0xF0`（最高位是 1），LB 有號延伸成 `0xFFFFFFF0`（-16），LBU 零延伸成 `0x000000F0`（240）。有號/無號分野正確。
- **LH-neg vs LHU-pos**：同一 half `0x8001`（最高位是 1），LH 延伸成 `0xFFFF8001`，LHU 成 `0x00008001`。half 的延伸也對。
- **SB-merge**：word 原是 `0xAAAAAAAA`，SB `0xBB` 到位址 13（word 的 byte 1），結果 `0xAAAABBAA`——只有 byte 1 變 `0xBB`，其餘三個 byte 保留。部分寫入正確。
- **SH-merge**：word 原 `0x11112222`，SH `0x3333` 到位址 18（高半），結果 `0x33332222`——高半變、低半保留。

dmem 全過。load/store 的所有粒度和延伸都對了。

## 核心概念：六條分支的比較

RV32I 有六條分支指令，全是 B-type，靠 funct3 區分。它們比較 rs1 和 rs2，成立就跳（`pc + imm`），不成立就 `pc+4`：

| funct3 | 指令 | 條件 | 比較型別 |
|---|---|---|---|
| 000 | BEQ | rs1 == rs2 | — |
| 001 | BNE | rs1 != rs2 | — |
| 100 | BLT | rs1 < rs2 | 有號 |
| 101 | BGE | rs1 >= rs2 | 有號 |
| 110 | BLTU | rs1 < rs2 | 無號 |
| 111 | BGEU | rs1 >= rs2 | 無號 |

三種底層比較就夠組出全部六條：**相等**（eq）、**有號小於**（lt）、**無號小於**（ltu）。BEQ=eq、BNE=~eq、BLT=lt、BGE=~lt、BLTU=ltu、BGEU=~ltu。有號/無號的差異和 Ch 9 的 SLT/SLTU 一模一樣——同一組 bit，有號無號可能給相反的大小關係。

> 我們讓 branch unit **獨立**做比較，而不是硬要用 ALU 的 zero flag。Ch 9 提過 ALU 的 zero 只能判 BEQ/BNE，BLT 這些需要「小於」。與其讓 ALU 多吐一個 less 訊號、再在外面拼六種條件，不如做一個專職的 branch unit 直接吃 rs1/rs2/funct3 吐一個 `take`。這樣 datapath 更清爽，branch 條件的邏輯集中一處。

## 範例 3：branch unit 實作與驗證

`branch_unit.sv`：

```systemverilog
// branch_unit.sv — 依 funct3 判斷六種分支條件是否成立
module branch_unit (
    input  logic [31:0] rs1,
    input  logic [31:0] rs2,
    input  logic [2:0]  funct3,
    output logic        take        // 分支條件成立嗎
);
    logic eq, lt, ltu;
    assign eq  = (rs1 == rs2);
    assign lt  = ($signed(rs1) < $signed(rs2)); // 有號
    assign ltu = (rs1 < rs2);                    // 無號

    always_comb begin
        unique case (funct3)
            3'b000: take =  eq;   // BEQ
            3'b001: take = ~eq;   // BNE
            3'b100: take =  lt;   // BLT  有號
            3'b101: take = ~lt;   // BGE  有號
            3'b110: take =  ltu;  // BLTU 無號
            3'b111: take = ~ltu;  // BGEU 無號
            default: take = 1'b0;
        endcase
    end
endmodule
```

`$signed()` 是關鍵——`lt` 有號比較要包它，`ltu` 不包（`logic` 預設無號）。這和 Ch 9 的 SLT/SLTU 同源。

`branch_tb.cpp`，用「-1 vs 1」這組經典輸入測有號無號的相反結果：

```cpp
#include "Vbranch_unit.h"
#include "verilated.h"
#include <cstdint>
#include <cstdio>
static Vbranch_unit *dut;
static int fails = 0;
static int take(uint32_t a, uint32_t b, uint32_t f3) {
    dut->rs1 = a; dut->rs2 = b; dut->funct3 = f3; dut->eval();
    return dut->take;
}
static void chk(const char *n, int g, int e) {
    bool ok = g == e;
    printf("[%s] %-14s take=%d (exp %d)\n", ok ? "OK " : "BAD", n, g, e);
    if (!ok) fails++;
}
int main(int c, char **v) {
    Verilated::commandArgs(c, v);
    dut = new Vbranch_unit;
    chk("BEQ eq",     take(5, 5, 0b000), 1);
    chk("BEQ neq",    take(5, 6, 0b000), 0);
    chk("BNE neq",    take(5, 6, 0b001), 1);
    // -1 vs 1：有號 -1<1 成立；無號 0xFFFFFFFF>1 不成立
    chk("BLT signed", take(0xFFFFFFFF, 1, 0b100), 1);
    chk("BGE signed", take(0xFFFFFFFF, 1, 0b101), 0);
    chk("BLTU unsig", take(0xFFFFFFFF, 1, 0b110), 0); // 大數不小於 1
    chk("BGEU unsig", take(0xFFFFFFFF, 1, 0b111), 1);
    chk("BGE equal",  take(7, 7, 0b101), 1);          // >= 含等於
    printf("\n%s (%d fail)\n", fails ? "FAILED" : "ALL PASSED", fails);
    delete dut;
    return fails ? 1 : 0;
}
```

編譯執行：

```bash
verilator --cc branch_unit.sv --exe branch_tb.cpp --Mdir obj_dir
make -C obj_dir -f Vbranch_unit.mk Vbranch_unit
./obj_dir/Vbranch_unit
```

真實輸出：

```
[OK ] BEQ eq         take=1 (exp 1)
[OK ] BEQ neq        take=0 (exp 0)
[OK ] BNE neq        take=1 (exp 1)
[OK ] BLT signed     take=1 (exp 1)
[OK ] BGE signed     take=0 (exp 0)
[OK ] BLTU unsig     take=0 (exp 0)
[OK ] BGEU unsig     take=1 (exp 1)
[OK ] BGE equal      take=1 (exp 1)

ALL PASSED (0 fail)
```

關鍵驗證：**BLT vs BLTU 對同一組 `(0xFFFFFFFF, 1)`**。有號時 `0xFFFFFFFF` 是 -1，`-1 < 1` 成立，BLT 跳（take=1）；無號時 `0xFFFFFFFF` 是 4294967295，遠大於 1，BLTU 不跳（take=0）。同一組 bit，有號無號結果相反——這正是 `$signed()` 要分清楚的原因。搞錯它，你的 `blt` 遇負數會判反，迴圈跳錯。`BGE equal` 驗證 `>=` 含等於（7>=7 成立）。

## 核心概念：branch/jump target 與 next-PC mux

最後把 PC 的「改道」邏輯補上。next-PC 有四種可能來源：

```
   pc_next = ┌ branch 且 take   → pc + imm     （分支成立）
             ├ JALR             → (rs1 + imm) & ~1  （最低位清 0）
             ├ JAL              → pc + imm     （無條件跳）
             └ 其他             → pc + 4       （正常前進）
```

三個細節：

- **branch target = pc + imm**（不是 pc+4+imm）。B-type 的立即數是相對**當前指令** PC 的偏移，imm_gen 已算好（含最低位補 0）。
- **JAL target = pc + imm**，同樣相對當前 PC。JAL 還要把 **pc+4** 寫回 rd（返回位址），這在 writeback mux 做（Ch 12 整合）。
- **JALR target = (rs1 + imm) & ~1**。JALR 的目標是暫存器值加偏移（不是相對 PC），且 spec 規定**最低位強制清 0**（`& ~1`）保證對齊。這個 `rs1 + imm` 借 ALU 的 ADD 算（Ch 10 讓 JALR 的 alu_op=ADD、alu_src=imm），但清最低位在 PC mux 做。

這幾條線本章先講清楚邏輯，實際接進 `core` 的 next-PC mux 在 Ch 12。這裡先給一個獨立的迷你示範，把「branch 成立換 target、不成立走 pc+4」跑出來。因為 next-PC mux 是 core 裡的幾行組合邏輯（不是獨立模組），我們在 Ch 12 的完整 core 裡一起驗——那支 Fibonacci 程式用了 `bne` 迴圈和 `jal` 自旋，正是這套 PC 改道邏輯的真實考驗。你會在 Ch 12 看到它跑對。

## 對比取捨

| 設計選擇 | 本課做法 | 替代方案 | 理由 |
|---|---|---|---|
| dmem 內部組織 | word 陣列 + byte offset 切 | 真 byte 陣列 | word 陣列好 `$readmemh`、對齊 imem 風格；切 byte 用 offset 即可 |
| 記憶體讀取 | 組合讀 | 同步讀（真 SRAM） | 單週期要同 cycle 讀完；同步讀留給 pipeline |
| 部分寫入 | read-modify-write（mask 合併） | byte-enable 寫埠 | mask 合併在教學 RTL 最直觀；真 SRAM 用 byte-enable |
| 分支比較 | 獨立 branch_unit | 復用 ALU 的 zero + less | 專職模組讓六條分支邏輯集中、datapath 清爽 |
| JALR target | ALU 算 rs1+imm，PC mux 清最低位 | 全在一處算 | 借 ALU 加法器省硬體，清位是 PC mux 的小事 |

## 踩雷區

**雷 1：SB/SH 直接整個 word 覆蓋，沖掉隔壁 byte。**
- 錯誤直覺：「store 就是把資料寫進那個位址」。
- 正確認識：SB 只該改 1 個 byte、SH 只該改 2 個，其餘 byte **必須保留**。若你 `mem[windex] <= wdata`，SB `0xBB` 會把整個 word 變成 `0x000000BB`，沖掉同 word 的其他三個 byte。正確做法是 read-modify-write：`(word & ~mask) | (val & mask)`。C 語言裡 `char *p; p[1] = 'x';` 不該動到 `p[0]`——硬體這層就是靠 mask 保證的。

**雷 2：LB/LH 忘了符號延伸，或 LBU/LHU 誤做了符號延伸。**
- 錯誤直覺：「讀出來補 0 到 32 bit 就好」。
- 正確認識：LB/LH 是**有號**載入，讀 `0xF0` 要延伸成 `0xFFFFFFF0`（-16）；LBU/LHU 是**無號**，補 0 成 `0x000000F0`（240）。搞反會讓 `signed char c = -16;` 讀進暫存器變成大正數。延伸方向由 funct3 的 bit 2 決定（1=無號），別接反。

**雷 3：BLT/BGE 用無號比較。**
- 錯誤直覺：「`rs1 < rs2` 就是小於」。
- 正確認識：BLT/BGE 是**有號**比較，必須 `$signed(rs1) < $signed(rs2)`。用無號比，`blt` 遇到負數（高位是 1，無號看是大數）會判斷相反——`for (i=-1; i<n; i++)` 這種迴圈會直接不進或跑爆。BLTU/BGEU 才用無號。差就差一個 `$signed()`，和 Ch 9 的 SLT/SLTU 同源。

**雷 4：JALR 的 target 忘了清最低位。**
- 錯誤直覺：「JALR target 就是 rs1 + imm」。
- 正確認識：RV spec 規定 JALR 算完 `rs1 + imm` 後要 **清最低位**（`& ~1`），保證跳到偶數位址。少了這步，某些 rs1+imm 為奇數的情況會跳到指令中間，抓出垃圾。這個清位在 PC mux 做（`(rs1_data + imm) & ~32'd1`），不在 ALU、不在 imm_gen。

## 進階延伸

- **misaligned 存取**：本課 dmem 假設位址對齊（LW 存取 4 對齊、LH 存取 2 對齊）。真實 RISC-V 若 `lw` 位址不是 4 的倍數，行為由平台決定——有的觸發 load-address-misaligned 例外、有的硬體支援非對齊存取（慢）。我們的 dmem 用 `addr[1:0]` 當 byte offset，其實隱含允許非對齊 byte 存取，但 LW 若跨 word 邊界會出錯（本課不測這種）。真 core 要嘛在這裡加對齊檢查觸發例外，要嘛做非對齊存取的硬體邏輯。留到 Part 5 的 trap。
- **store buffer 與 memory ordering**：單週期的 store 是「這 cycle 算位址、下個沿寫入」，乾淨即時。真 CPU 的 store 會先進 store buffer、之後才真正寫入記憶體/cache，還牽涉 memory ordering（別的核心何時看得到這個寫入）。RISC-V 的 memory model（RVWMO）和 `fence` 指令管這個，是多核與 pipeline 的深水區，Part 4/6 才碰。
- **分支預測的伏筆**：單週期 branch 當 cycle 就知道跳不跳（比較器組合出 take），沒有懲罰。但 pipeline 裡指令是流水的，分支結果要好幾級後才知道，這期間後面指令已經抓進來了——猜錯要清掉（branch misprediction penalty）。這就是分支預測器存在的理由。本章的 branch_unit 在 pipeline 裡會被搬到某一級，還要配預測邏輯，是 Ch 17 的主題。
- **auipc 補完 PC 相對定址**：`auipc rd, imm` 算 `pc + (imm<<12)`，配合 `jalr`/`addi` 能做 PC 相對的位址計算（position-independent code 的基礎）。它的 datapath 要把 ALU 的 a 端接 PC（像 LUI 接 0 一樣）。本課 core 主線沒放，但 imm_gen 已支援 U-type，你可在練習裡補這條 datapath。

## 本章重點整理

- **dmem** byte 定址：內部 word 陣列 + `addr[1:0]` 當 byte offset。讀組合、寫同步（和 regfile 同分工）。
- **load 延伸**：LB/LH 有號（補符號位）、LBU/LHU 無號（補 0）、LW 整 word。由 funct3 決定粒度與延伸。
- **store 部分寫入**：SB/SH 用 read-modify-write（`(word & ~mask) | (val & mask)`）只改對應 byte/half，保留其餘。
- **little-endian**：低 byte 在低位址，`word[boff*8 +: 8]` 切 byte。
- **六條分支** 靠三種比較（eq/lt/ltu）組出，BLT/BGE 有號（`$signed()`）、BLTU/BGEU 無號。獨立 branch_unit 吐 `take`。
- **next-PC mux**：branch 成立或 JAL → `pc+imm`；JALR → `(rs1+imm) & ~1`（清最低位）；否則 `pc+4`。JAL/JALR 還把 `pc+4` 寫回 rd。
- dmem、branch_unit 都真跑驗證通過；PC 改道邏輯在 Ch 12 的完整 core 用 Fibonacci 迴圈實測。

## 自我檢核

- [ ] 我能解釋 dmem 為什麼要 byte 定址（imem 為何不用），並說出 funct3 怎麼決定粒度與延伸。
- [ ] 我能畫出 little-endian 下 `0x12345678` 存位址 0~3 各 byte 是什麼，並算 `lbu x1,3(x0)` 讀到什麼。
- [ ] 我能寫出 SB 的 read-modify-write 合併公式，並說明不這樣做會沖掉什麼。
- [ ] 我能說出六條分支各對應哪種底層比較，並解釋 BLT vs BLTU 對 `(-1, 1)` 為何結果相反。
- [ ] 我能列出 next-PC 的四種來源，並說明 JALR 的 target 為何要 `& ~1`、branch target 為何是 `pc+imm` 而非 `pc+4+imm`。
- [ ] 我能說明 JAL/JALR 除了改 PC，還要把什麼寫回哪個暫存器。

## 延伸閱讀

- **《Computer Organization and Design, RISC-V Edition》(Patterson & Hennessy) 第 4.4 節的 datapath 擴充部分**：看它怎麼一步步把 data memory、branch 的 target 加法器、PC 的 mux 加進 datapath。它的圖清楚標出 branch target = PC + (imm)，以及 branch 用 zero 判斷的接法——對照本章「獨立 branch_unit」的取捨。
- **《Digital Design and Computer Architecture, RISC-V Edition》(Harris & Harris) 第 7.3.2 節「Single-Cycle Datapath」load/store/branch 擴充**：Harris 把 memory 的 byte/half/word 存取和 sign-extend 單元畫得很細，還有 branch 的 PCTarget 計算，是本章 dmem 和 PC mux 的圖解版。
- **[RISC-V Unprivileged ISA Spec](https://riscv.org/technical/specifications/) 第 2.5「Control Transfer Instructions」與 2.6「Load and Store Instructions」**：權威定義 JALR 的 `& ~1`、branch 的 PC 相對定址、load/store 的對齊與 sign-extend 語意。你 datapath 行為有疑義時的最終依據。
- **[picorv32 原始碼](https://github.com/YosysHQ/picorv32) 搜 `mem_wstrb` 與 `reg_op`**：看一個真 core 怎麼處理 byte-enable（`wstrb` 是 write strobe，每 bit 對一個 byte）——它用 4-bit strobe 告訴外部記憶體哪幾個 byte 要寫，這是真硬體版的「部分寫入」，比本課的 read-modify-write 更貼近實際 SRAM/bus 介面。對照能看出教學簡化和工業做法的差距。

datapath 的每一塊都齊了——fetch、decode、regfile、ALU、control、imm_gen、dmem、branch、PC mux。下一章是 Part 1 的高潮：把它們全接成一個 `core` top module，組一支真程式（費氏數列）灌進去，dump 暫存器和記憶體對照預期值，看整顆單週期 CPU 活起來。

→ [Ch 12 單週期 RV32I 完整整合](./12-single-cycle-rv32i-complete.md)
