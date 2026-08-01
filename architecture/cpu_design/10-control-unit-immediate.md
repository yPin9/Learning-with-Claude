# Ch 10 — Control Unit + immediate generator

> **目標**：親手做出 CPU 的「大腦」——control unit（控制單元），從一條指令的 opcode/funct3/funct7 產生**全部**控制訊號（reg_write/mem_read/mem_write/mem_to_reg/alu_src/branch/jump/lui/alu_op），並實作 immediate generator（立即數產生器）把 I/S/B/U/J 五種型別的立即數從指令 bit 裡「重排」出來、正確符號延伸。每一塊都寫 C++ testbench 真跑，立即數 decode 拿 objdump 對照驗證。
> **環境**：WSL + verilator 4.038 + riscv64-unknown-elf-gcc 10.2.0。本章所有輸出都是真跑出來的。

## 為什麼需要 control unit？

前三章我們做了三塊硬體：fetch 會抓指令、regfile 會存值、ALU 會算。但它們是**啞的**——ALU 收到 `alu_op` 才知道要加還是減，regfile 收到 `rd_we` 才知道要不要寫。這些訊號從哪來？

答案：從**指令本身**解讀出來。`add x3,x1,x2` 這條指令的 32 個 bit 裡，藏著「這是 R-type、要寫暫存器、ALU 做加法、不碰記憶體」這些資訊。把 bit 翻譯成一組控制訊號的工作，就是 control unit。

沒有 control unit，datapath 上每個 mux 該選哪邊、每個 write enable 該開該關，全是未定義。它是「指揮」——datapath 是通用的樂器，control unit 是看譜下指令的指揮，決定這一 cycle 每塊硬體做什麼。

同時我們還缺一塊：**immediate generator**。`addi x1,x0,5` 的那個 `5`、`lw x2,8(x3)` 的那個 `8`、`beq` 的跳轉距離——這些常數（立即數）也編碼在指令 bit 裡，但**位置很亂**（等下會看到 B-type 的 bit 是打散重排的）。imm_gen 專門負責把它們拼回一個乾淨的 32-bit 值。

## 先建立直覺：指揮 + 翻譯官

```
              一條 32-bit 指令 instr
        ┌────────────────────────────────┐
        │ funct7 │rs2│rs1│f3│ rd │ opcode │
        └───┬─────────────────┬───────┬───┘
            │ (funct7[5])      │(funct3)│(opcode)
            ▼                  ▼        ▼
        ┌───────────────────────────────────┐
        │        control unit (指揮)         │
        │  opcode 決定「大類」                │
        │  funct3/funct7 決定「細節」         │
        └───────────────────────────────────┘
            │ reg_write mem_read mem_write ...
            │ alu_src branch jump lui alu_op
            ▼
        （去指揮 datapath 上的每個 mux / write enable）

        instr ──▶┌─────────────┐──▶ imm[31:0]
                 │  imm_gen     │   （把散落的立即數 bit
                 │ (翻譯官)     │    重排、符號延伸成乾淨的 32-bit）
                 └─────────────┘
```

兩塊都是**純組合邏輯**：指令一進來，控制訊號和立即數瞬間出來，沒有 clock、沒有記憶。它們是 decode 階段的核心。

## 核心概念：控制訊號是什麼、各管什麼

我們的 control unit 產生這些訊號（全課約定，之後 core 整合就照這組）：

| 訊號 | 意義 | 管什麼 |
|---|---|---|
| `reg_write` | 是否寫回暫存器 | regfile 的 `rd_we` |
| `mem_read` | 是否讀記憶體 | dmem 讀致能（load 才開） |
| `mem_write` | 是否寫記憶體 | dmem 寫致能（store 才開） |
| `mem_to_reg` | 寫回值來源 | writeback mux：1=記憶體資料，0=ALU 結果 |
| `alu_src` | ALU 第二運算元來源 | ALU b 端 mux：1=立即數，0=rs2 |
| `branch` | 是否為分支指令 | next-PC mux：配合比較結果決定跳不跳 |
| `jump` | 是否為跳轉指令 | next-PC mux + writeback（JAL/JALR 寫 pc+4） |
| `lui` | 是否為 LUI | ALU a 端清零（讓 ALU 算 `0 + imm`） |
| `alu_op` | ALU 要做哪種運算 | ALU 的 4-bit 功能選擇 |

`lui` 這個訊號是為了不動 Ch 9 已凍結的 ALU（它只認 0000~1001 十種 op）。LUI 要「把 imm 直接放進 rd」，我們用一個小技巧：讓 ALU 做 ADD，但把它的 `a` 端強制清零，於是 `result = 0 + imm = imm`。這樣 LUI 免費搭上 ADD，不用給 ALU 加新 op。這是「datapath 微調換取 ALU 不動」的典型取捨。

## 底層機制：兩段式產生控制訊號

control unit 拆成兩段組合邏輯，好讀也好維護。

**第一段：主控制訊號，只看 opcode。** opcode（`instr[6:0]`）決定指令的**大類**——R-type、I-type 算術、load、store、branch、jump、LUI。每一大類的 reg_write/mem_read/... 是固定的。例如所有 load 都是「寫暫存器 + 讀記憶體 + 用立即數當位址偏移 + 寫回值來自記憶體」。

**第二段：alu_op，看 opcode + funct3 + funct7 的 bit 30。** ALU 要做什麼運算，光看 opcode 不夠：

- R-type / I-type 算術：由 **funct3** 選運算（000=add/sub、010=slt、100=xor...）。其中 `add` vs `sub`、`srl` vs `sra` funct3 相同，要靠 **funct7 的 bit 30**（我們叫 `funct7_5`，因為它是 funct7 的第 5 位、也就是 `instr[30]`）分家。
- load / store / JALR：ALU 一律做 **ADD**（算位址 = base + offset，或 JALR 的 target = rs1 + imm）。
- branch：ALU 做 **SUB**（`beq` 靠 zero flag 判相等，是減法的副產品）。
- LUI：做 ADD（配合 `lui` 訊號把 a 清零）。

```
   alu_op 決策樹
   ├─ R/I 算術 → 看 funct3
   │            ├ 000 → funct7_5? SUB : ADD   （R-type 才分；I-type addi 恆 ADD）
   │            ├ 001 → SLL
   │            ├ 010 → SLT
   │            ├ 011 → SLTU
   │            ├ 100 → XOR
   │            ├ 101 → funct7_5? SRA : SRL
   │            ├ 110 → OR
   │            └ 111 → AND
   ├─ load/store/JALR → ADD
   ├─ branch          → SUB
   └─ LUI             → ADD (a 清零)
```

一個容易錯的細節：I-type 算術的 `funct3=000`（`addi`）**永遠是 ADD**，沒有 subi 這條指令。只有 R-type 的 `funct3=000` 才靠 funct7_5 分 add/sub。但 I-type 的**移位**（`slli`/`srli`/`srai`，funct3=001/101）**確實**用 instr[30] 區分 srl/sra——因為 shift-immediate 把移位型別編在 instr[30]。所以判斷 add/sub 要限定 R-type，判斷 srl/sra 兩者皆看 funct7_5。下面的實作精準處理了這點。

## 範例 1：control unit 完整實作

`control_unit.sv`，輸出全課約定的控制訊號：

```systemverilog
// control_unit.sv — 從 opcode/funct3/funct7 產生所有控制訊號 + alu_op
module control_unit (
    input  logic [6:0]  opcode,
    input  logic [2:0]  funct3,
    input  logic        funct7_5,   // instr[30]，區分 add/sub、srl/sra
    output logic        reg_write,
    output logic        mem_read,
    output logic        mem_write,
    output logic        mem_to_reg,
    output logic        alu_src,     // 0=rs2, 1=imm
    output logic        branch,
    output logic        jump,
    output logic        lui,         // 1=LUI，ALU 的 a 端強制吃 0
    output logic [3:0]  alu_op
);
    // opcode 常數，讀起來比裸 7'b... 清楚
    localparam OP_RTYPE  = 7'b0110011;
    localparam OP_ITYPE  = 7'b0010011; // addi 等
    localparam OP_LOAD   = 7'b0000011;
    localparam OP_STORE  = 7'b0100011;
    localparam OP_BRANCH = 7'b1100011;
    localparam OP_JAL    = 7'b1101111;
    localparam OP_JALR   = 7'b1100111;
    localparam OP_LUI    = 7'b0110111;

    // alu_op 編碼（與 alu.sv 一致）
    localparam ALU_ADD  = 4'b0000;
    localparam ALU_SUB  = 4'b0001;
    localparam ALU_SLL  = 4'b0010;
    localparam ALU_SLT  = 4'b0011;
    localparam ALU_SLTU = 4'b0100;
    localparam ALU_XOR  = 4'b0101;
    localparam ALU_SRL  = 4'b0110;
    localparam ALU_SRA  = 4'b0111;
    localparam ALU_OR   = 4'b1000;
    localparam ALU_AND  = 4'b1001;

    // === 第一段：主控制訊號（看 opcode） ===
    always_comb begin
        // 預設全關，避免 latch
        reg_write  = 1'b0;
        mem_read   = 1'b0;
        mem_write  = 1'b0;
        mem_to_reg = 1'b0;
        alu_src    = 1'b0;
        branch     = 1'b0;
        jump       = 1'b0;
        lui        = 1'b0;
        unique case (opcode)
            OP_RTYPE:  begin reg_write = 1; end
            OP_ITYPE:  begin reg_write = 1; alu_src = 1; end
            OP_LOAD:   begin reg_write = 1; alu_src = 1; mem_read = 1; mem_to_reg = 1; end
            OP_STORE:  begin alu_src = 1; mem_write = 1; end
            OP_BRANCH: begin branch = 1; end
            OP_JAL:    begin reg_write = 1; jump = 1; end
            OP_JALR:   begin reg_write = 1; jump = 1; alu_src = 1; end
            OP_LUI:    begin reg_write = 1; alu_src = 1; lui = 1; end
            default:   ; // 全關
        endcase
    end

    // === 第二段：alu_op（看 opcode + funct3 + funct7_5） ===
    always_comb begin
        unique case (opcode)
            OP_RTYPE, OP_ITYPE: begin
                unique case (funct3)
                    3'b000: alu_op = (opcode == OP_RTYPE && funct7_5) ? ALU_SUB : ALU_ADD;
                    3'b001: alu_op = ALU_SLL;
                    3'b010: alu_op = ALU_SLT;
                    3'b011: alu_op = ALU_SLTU;
                    3'b100: alu_op = ALU_XOR;
                    3'b101: alu_op = funct7_5 ? ALU_SRA : ALU_SRL;
                    3'b110: alu_op = ALU_OR;
                    3'b111: alu_op = ALU_AND;
                    default: alu_op = ALU_ADD;
                endcase
            end
            OP_LOAD, OP_STORE, OP_JALR: alu_op = ALU_ADD; // 位址計算
            OP_BRANCH:                  alu_op = ALU_SUB;  // 比較用減法（beq 看 zero）
            OP_LUI:                     alu_op = ALU_ADD;  // a 端被 lui 訊號清零，等於 0+imm
            default:                    alu_op = ALU_ADD;
        endcase
    end
endmodule
```

要點：

- 兩段各自 `always_comb`，第一段開頭把所有訊號**預設清 0** 再用 case 覆寫該開的——這是避免 latch 的標準寫法（漏掉某條路徑時，訊號有確定值而非「保持上次」）。
- `funct7_5` 只是 `instr[30]`。core 整合時我們會從 instr 拉這一 bit 進來，不必傳整個 funct7。
- `add/sub` 的判斷寫成 `opcode == OP_RTYPE && funct7_5`，明確限定「只有 R-type 的 000 才看 funct7_5」，I-type 的 addi 不會誤判成 sub。

## 範例 2：control unit 真值表 testbench

`control_tb.cpp`，涵蓋每個大類、以及 add/sub、srl/sra 靠 funct7_5 分家的關鍵案例：

```cpp
#include "Vcontrol_unit.h"
#include "verilated.h"
#include <cstdint>
#include <cstdio>
static Vcontrol_unit *dut;
static int fails = 0;

static void run(uint32_t op, uint32_t f3, uint32_t f75) {
    dut->opcode = op; dut->funct3 = f3; dut->funct7_5 = f75; dut->eval();
}
static void chk(const char *n, int got, int exp) {
    bool ok = got == exp;
    if (!ok) { printf("[BAD] %s got=%d exp=%d\n", n, got, exp); fails++; }
}
static void line(const char *name) {
    printf("%-8s rw=%d mr=%d mw=%d m2r=%d asrc=%d br=%d jmp=%d alu_op=%2d\n",
        name, dut->reg_write, dut->mem_read, dut->mem_write, dut->mem_to_reg,
        dut->alu_src, dut->branch, dut->jump, dut->alu_op);
}
int main(int c, char **v) {
    Verilated::commandArgs(c, v);
    dut = new Vcontrol_unit;

    run(0b0110011, 0b000, 0); line("add");
    chk("add.rw", dut->reg_write, 1); chk("add.aluop", dut->alu_op, 0); chk("add.asrc", dut->alu_src, 0);
    run(0b0110011, 0b000, 1); line("sub");
    chk("sub.aluop", dut->alu_op, 1);
    run(0b0110011, 0b101, 1); line("sra");
    chk("sra.aluop", dut->alu_op, 7);
    run(0b0010011, 0b000, 0); line("addi");
    chk("addi.asrc", dut->alu_src, 1); chk("addi.aluop", dut->alu_op, 0);
    run(0b0010011, 0b101, 1); line("srai");
    chk("srai.aluop", dut->alu_op, 7); // I-type shift 也用 funct7_5 分 srl/sra
    run(0b0000011, 0b010, 0); line("lw");
    chk("lw.mr", dut->mem_read, 1); chk("lw.m2r", dut->mem_to_reg, 1); chk("lw.aluop", dut->alu_op, 0);
    run(0b0100011, 0b010, 0); line("sw");
    chk("sw.mw", dut->mem_write, 1); chk("sw.rw", dut->reg_write, 0);
    run(0b1100011, 0b000, 0); line("beq");
    chk("beq.br", dut->branch, 1); chk("beq.aluop", dut->alu_op, 1); chk("beq.rw", dut->reg_write, 0);
    run(0b1101111, 0b000, 0); line("jal");
    chk("jal.jmp", dut->jump, 1); chk("jal.rw", dut->reg_write, 1);
    run(0b1100111, 0b000, 0); line("jalr");
    chk("jalr.jmp", dut->jump, 1); chk("jalr.asrc", dut->alu_src, 1);
    run(0b0110111, 0b000, 0); line("lui");
    chk("lui.rw", dut->reg_write, 1); chk("lui.lui", dut->lui, 1); chk("lui.aluop", dut->alu_op, 0);

    printf("\n%s (%d fail)\n", fails ? "FAILED" : "ALL PASSED", fails);
    delete dut;
    return fails ? 1 : 0;
}
```

編譯執行：

```bash
verilator --cc control_unit.sv --exe control_tb.cpp --Mdir obj_dir
make -C obj_dir -f Vcontrol_unit.mk Vcontrol_unit
./obj_dir/Vcontrol_unit
```

真實輸出：

```
add      rw=1 mr=0 mw=0 m2r=0 asrc=0 br=0 jmp=0 alu_op= 0
sub      rw=1 mr=0 mw=0 m2r=0 asrc=0 br=0 jmp=0 alu_op= 1
sra      rw=1 mr=0 mw=0 m2r=0 asrc=0 br=0 jmp=0 alu_op= 7
addi     rw=1 mr=0 mw=0 m2r=0 asrc=1 br=0 jmp=0 alu_op= 0
srai     rw=1 mr=0 mw=0 m2r=0 asrc=1 br=0 jmp=0 alu_op= 7
lw       rw=1 mr=1 mw=0 m2r=1 asrc=1 br=0 jmp=0 alu_op= 0
sw       rw=0 mr=0 mw=1 m2r=0 asrc=1 br=0 jmp=0 alu_op= 0
beq      rw=0 mr=0 mw=0 m2r=0 asrc=0 br=1 jmp=0 alu_op= 1
jal      rw=1 mr=0 mw=0 m2r=0 asrc=0 br=0 jmp=1 alu_op= 0
jalr     rw=1 mr=0 mw=0 m2r=0 asrc=1 br=0 jmp=1 alu_op= 0
lui      rw=1 mr=0 mw=0 m2r=0 asrc=1 br=0 jmp=0 alu_op= 0

ALL PASSED (0 fail)
```

逐項核對：

- **add vs sub**：opcode/funct3 相同，只 funct7_5 由 0→1，alu_op 從 0（ADD）變 1（SUB）。分家正確。
- **sra**：funct3=101、funct7_5=1，alu_op=7（SRA）。**addi vs sub**：addi 的 funct3 也是 000，但 alu_op 是 0（ADD）不受 funct7_5 影響——因為它是 I-type，我們限定只有 R-type 看 funct7_5。
- **srai**：I-type 的 funct3=101、funct7_5=1，alu_op=7（SRA），證明 shift-immediate 確實靠 instr[30] 分 srl/sra。
- **lw**：mr=1、m2r=1（讀記憶體、寫回值來自記憶體）、asrc=1（位址偏移用立即數）、alu_op=0（ADD 算位址）。
- **sw**：mw=1、rw=0（store 不寫暫存器）。
- **beq**：br=1、alu_op=1（SUB 比較）、rw=0（分支不寫暫存器）。
- **lui**：`line()` 沒印 `lui` 訊號欄，但 testbench 的 `chk("lui.lui", dut->lui, 1)` 通過，代表 `lui`=1。它的 `alu_op=0`（ADD）——LUI 不需要特殊 op，靠 datapath 把 ALU a 端清零得 `0+imm`。

> LUI 的 alu_op 是 ADD（0）而非某個特殊碼。ALU 保持凍結不動，LUI 靠 datapath 的 a 清零達成，這正是 Ch 12 整合時 `assign alu_a = lui ? 32'd0 : rs1_data;` 那一行的由來。

## 核心概念：immediate 的五種型別與 bit 重排

RV32I 的立即數依指令格式分五型（I/S/B/U/J），每型立即數在指令裡的**擺放位置不同**，有些還被**打散重排**。為什麼要打散？因為 RISC-V 刻意讓不同格式的**同名欄位盡量對齊同一 bit 位置**（例如 rs1、rs2、opcode 在所有格式都在固定位置），代價就是立即數的 bit 被切碎散落。imm_gen 的工作就是把它們**拼回**一個正確符號延伸的 32-bit 值。

先看五型的立即數怎麼從 instr 拼出來（`instr[31]` 恆為符號位，拼完向上符號延伸）：

```
  I-type (addi/lw/jalr):  imm = instr[31:20]                （12 bit，連續）
  S-type (sw/sh/sb):      imm = instr[31:25] . instr[11:7]  （高低兩段拼）
  B-type (beq...):        imm = instr[31].instr[7].instr[30:25].instr[11:8].0
                          （最低位恆 0，因分支目標對齊 2；bit 被打散重排）
  U-type (lui/auipc):     imm = instr[31:12] . 12'b0        （高 20 bit，低補 0）
  J-type (jal):           imm = instr[31].instr[19:12].instr[20].instr[30:21].0
                          （最低位恆 0，同樣打散）
```

三個要想通的點：

1. **B/J 的最低位恆為 0**：分支和跳轉的目標一定是偶數位址（指令 2-byte 對齊），所以立即數最低位不用存，硬體補一個 0。這讓 12-bit 的 B 立即數實際能表示 ±4KiB 的**偶數**偏移。
2. **符號延伸**：I/S/B/J 都是有號偏移（可往前也可往後跳），最高位（`instr[31]`）是符號位，要向上複製填滿到 bit 31。U-type 不符號延伸（它是「高 20 位」，低 12 位補 0）。
3. **B 和 J 為什麼那樣重排**：這是 RISC-V 為了讓 immediate 的**符號位永遠在 instr[31]**、且各格式間 bit 盡量共用而設計的。你不用背，照 spec 的表接線即可——下面的 code 就是那張表的直譯。

## 範例 3：immediate generator 實作

`imm_gen.sv`：

```systemverilog
// imm_gen.sv — 依 opcode 判斷指令型別，抽出並符號延伸立即數
module imm_gen (
    input  logic [31:0] instr,
    output logic [31:0] imm
);
    logic [6:0] opcode;
    assign opcode = instr[6:0];

    always_comb begin
        unique case (opcode)
            // I-type: addi/andi/.../lw/jalr
            7'b0010011,
            7'b0000011,
            7'b1100111: imm = {{20{instr[31]}}, instr[31:20]};
            // S-type: sw/sh/sb
            7'b0100011: imm = {{20{instr[31]}}, instr[31:25], instr[11:7]};
            // B-type: beq/bne/...
            7'b1100011: imm = {{19{instr[31]}}, instr[31], instr[7],
                               instr[30:25], instr[11:8], 1'b0};
            // U-type: lui/auipc
            7'b0110111,
            7'b0010111: imm = {instr[31:12], 12'b0};
            // J-type: jal
            7'b1101111: imm = {{11{instr[31]}}, instr[31], instr[19:12],
                               instr[20], instr[30:21], 1'b0};
            default:    imm = 32'd0;
        endcase
    end
endmodule
```

拆解幾個拼接：

- `{{20{instr[31]}}, instr[31:20]}`：取 12-bit 立即數 `instr[31:20]`，前面補 20 份 `instr[31]`（符號延伸），湊成 32 bit。
- B-type 那行：把 `instr[31]`（bit 12）、`instr[7]`（bit 11）、`instr[30:25]`（bit 10:5）、`instr[11:8]`（bit 4:1）依序拼，最低位補 `1'b0`（bit 0），前面 19 份符號延伸。拼出來剛好是分支偏移。
- U-type：`{instr[31:12], 12'b0}`，高 20 bit 放上位、低 12 bit 補 0，不符號延伸。

`{n{x}}` 是 SystemVerilog 的 replication（複製）語法：`{20{instr[31]}}` 就是把 `instr[31]` 複製 20 份。符號延伸靠它一行搞定。

## 範例 4：imm_gen testbench，拿真指令對照

我們不手打 magic number，而是用**真實指令的機器碼**（從前幾章 objdump 抄來的、或你自己組的）當輸入，驗證解出的立即數對不對。`imm_tb.cpp`：

```cpp
#include "Vimm_gen.h"
#include "verilated.h"
#include <cstdint>
#include <cstdio>
static Vimm_gen *dut;
static int fails = 0;
static uint32_t gen(uint32_t instr) { dut->instr = instr; dut->eval(); return dut->imm; }
static void chk(const char *n, uint32_t g, uint32_t e) {
    bool ok = g == e;
    printf("[%s] %-10s imm=0x%08x (exp 0x%08x)\n", ok ? "OK " : "BAD", n, g, e);
    if (!ok) fails++;
}
int main(int c, char **v) {
    Verilated::commandArgs(c, v);
    dut = new Vimm_gen;
    chk("addi-neg", gen(0xfff00093), 0xffffffff); // addi x1,x0,-1
    chk("lw-0",     gen(0x00002283), 0x0);         // lw t0,0(zero)
    chk("sw-8",     gen(0x00502423), 0x8);         // sw t0,8(zero)
    chk("beq-8",    gen(0x00108463), 0x8);         // beq ra,ra,+8
    chk("jal-4",    gen(0x0040056f), 0x4);         // jal a0,+4
    chk("lui",      gen(0x123450b7), 0x12345000);  // lui x1,0x12345
    printf("\n%s (%d fail)\n", fails ? "FAILED" : "ALL PASSED", fails);
    delete dut;
    return fails ? 1 : 0;
}
```

這些機器碼哪來的？用 toolchain 組出來、objdump 一看便知。例如 `addi x1,x0,-1`：

```bash
$ echo 'addi x1,x0,-1' | riscv64-unknown-elf-gcc -march=rv32i -mabi=ilp32 \
    -nostdlib -Ttext=0 -x assembler - -o /tmp/a.o -c
$ riscv64-unknown-elf-objdump -d /tmp/a.o
   0: fff00093   addi ra,zero,-1
```

機器碼 `fff00093`，立即數欄 `instr[31:20] = 0xfff = -1`，符號延伸成 `0xffffffff`。這就是我們期望 imm_gen 吐的值。其餘（`00002283`=lw、`00502423`=sw、`00108463`=beq、`0040056f`=jal、`123450b7`=lui）都是同法組出、objdump 對照。

編譯執行：

```bash
verilator --cc imm_gen.sv --exe imm_tb.cpp --Mdir obj_dir
make -C obj_dir -f Vimm_gen.mk Vimm_gen
./obj_dir/Vimm_gen
```

真實輸出：

```
[OK ] addi-neg   imm=0xffffffff (exp 0xffffffff)
[OK ] lw-0       imm=0x00000000 (exp 0x00000000)
[OK ] sw-8       imm=0x00000008 (exp 0x00000008)
[OK ] beq-8      imm=0x00000008 (exp 0x00000008)
[OK ] jal-4      imm=0x00000004 (exp 0x00000004)
[OK ] lui        imm=0x12345000 (exp 0x12345000)

ALL PASSED (0 fail)
```

逐項核對：

- **addi-neg**：`0xfff` 符號延伸成 `0xffffffff`（-1）。有號延伸正確。
- **sw-8**：S-type 把 `instr[31:25]`（0）和 `instr[11:7]`（0b01000=8）拼出 8。高低兩段重組正確。
- **beq-8 / jal-4**：B/J 的打散 bit 重排後拼出 8、4，且最低位補 0（都是偶數）。重排正確。
- **lui**：`0x12345` 放進高 20 bit 得 `0x12345000`，低 12 位補 0，不符號延伸。U-type 正確。

imm_gen 五型全過。decode 的兩塊——control unit 和 imm_gen——都通了。

## 對比取捨

| 設計選擇 | 本課做法 | 替代方案 | 理由 |
|---|---|---|---|
| control 拆幾段 | 兩段（主訊號 / alu_op） | 一張大真值表全塞 | 分段好讀、alu_op 的 funct 邏輯獨立，不污染主訊號 |
| alu_op 產生 | control_unit 內直接產 | 獨立 alu_control 模組（P&H 畫法） | 本課合併省一個模組；概念上仍是「主控制 + ALU 解碼」兩層 |
| LUI 實作 | `lui` 訊號把 ALU a 清零 | 給 ALU 加 PASS_B op | 不動已凍結的 ALU（只認 0000~1001）；datapath 微調成本更低 |
| 立即數符號延伸 | imm_gen 內用 replication 一次做完 | datapath 後段再延伸 | 集中在一處，其他模組收到的 imm 已是乾淨 32-bit |
| 避免 latch | 每段開頭預設清 0 | 每條 case 補齊所有訊號 | 預設清 0 最不易漏、最好維護 |

## 踩雷區

**雷 1：add/sub 判斷沒限定 R-type，把 addi 誤判成 sub。**
- 錯誤直覺：「funct3=000 且 funct7_5=1 就是 sub」。
- 正確認識：I-type 的 `addi` 也有 funct3=000，而它的 instr[30] 是**立即數的一部分**，可能剛好是 1。若不限定 `opcode==R-type`，`addi x1,x2,-1024`（立即數某些值讓 instr[30]=1）會被誤判成 sub，算出完全錯的結果。必須寫 `(opcode == OP_RTYPE && funct7_5)`。但**移位**（funct3=101）例外——I-type 的 srai 確實靠 instr[30] 分 srl/sra，那是 spec 規定 shift-immediate 的編碼，兩者都看 funct7_5 是對的。

**雷 2：忘了預設清 0，控制訊號變成 latch。**
- 錯誤直覺：「case 裡把該開的開起來就好」。
- 正確認識：`always_comb` 裡若某條路徑沒賦值給某訊號，綜合工具會推出一個 **latch**（保持上次值），這在單週期是災難——上一條指令的 mem_write 可能殘留到這條，亂寫記憶體。標準解法是**每段開頭先把所有輸出設 0**，再用 case 覆寫。verilator 也會對可能的 latch 警告。

**雷 3：B/J 立即數忘了最低位補 0，或符號延伸方向錯。**
- 錯誤直覺：「把 immediate 欄位接起來就好」。
- 正確認識：B/J 的立即數最低位**硬體補 0**（分支目標對齊 2），少補這個 0 你的跳轉距離會差一半或跳到奇數位址。而且 B/J 的 bit 是**打散重排**的，接錯一根線跳轉就全錯。最穩的做法是照 spec 的 immediate 表逐 bit 對接，寫完用真指令（如 `beq ra,ra,+8`）驗證解出的是不是 8。

**雷 4：U-type 也做符號延伸。**
- 錯誤直覺：「立即數都要符號延伸」。
- 正確認識：U-type（lui/auipc）**不符號延伸**——它是「取高 20 位放到 bit 31:12、低 12 位補 0」。`lui x1,0x80000` 應得 `0x80000000`，若你錯誤地符號延伸，高位會被 `instr[31]` 汙染。I/S/B/J 才符號延伸，U 是補 0。

## 進階延伸

- **control unit 的兩種實作風格**：本課用 `case` 描述（behavioral），綜合工具會把它變成一堆組合閘。另一種是**微碼（microcode）/ ROM 式**——把「opcode → 控制訊號」直接燒成一張查找表 ROM，index 是 opcode，內容是控制位。RISC 因指令規整、控制訊號少，用組合邏輯就夠；CISC（x86）指令複雜，歷史上多用微碼。P&H 附錄有 ROM/PLA 實作對照，值得一看兩種思路的取捨。
- **alu_control 為什麼常被畫成獨立模組**：P&H 的經典 datapath 把「主控制（看 opcode 產 ALUOp 兩位粗分類）」和「ALU control（看 ALUOp+funct 產 4-bit alu_op）」分兩塊。本課合併成一個 control_unit 直接產 4-bit alu_op，省一層。兩種等價，分開的好處是主控制不必懂 funct 細節、ALU control 可獨立測。你讀 P&H 時會看到那個 2-bit ALUOp，別被搞混——它是中間信號，本課直接跳過。
- **auipc 和 jalr 的立即數陷阱**：`auipc` 是 U-type（本課 imm_gen 已支援），但它的語意是 `pc + imm`，datapath 要把 ALU 的 a 端接 PC 而非 rs1——本課主線沒放 auipc 進 core，但 imm_gen 已備好，Ch 12 或練習可自行補這條 datapath。`jalr` 的 target 是 `(rs1 + imm) & ~1`（最低位強制清 0），那個 `& ~1` 在 next-PC mux 做，不在 imm_gen，別搞混。
- **壓縮指令 C 擴充的 decode**：真實 RISC-V 常帶 C 擴充（16-bit 壓縮指令），decode 要先判斷 `instr[1:0]` 是不是 `11`（非壓縮）來決定指令長度，立即數格式又多好幾種。本課純 RV32I 全 32-bit，不碰這塊，但你要知道真 decoder 第一步常是「這條是 2-byte 還是 4-byte」。

## 本章重點整理

- **control unit** 從 opcode/funct3/funct7 產生全部控制訊號，是 datapath 的指揮。兩段式：第一段看 opcode 產主訊號、第二段看 opcode+funct3+funct7_5 產 alu_op。
- **add/sub、srl/sra** 靠 `funct7_5`（instr[30]）分家；add/sub 要限定 R-type（避免 addi 誤判），但 srl/sra 兩型皆看（shift-immediate 也編在 instr[30]）。
- **每段開頭預設清 0** 是避免 latch 的標準寫法。
- **LUI** 靠 `lui` 訊號把 ALU a 端清零、走 ADD，不動已凍結的 ALU。
- **imm_gen** 把 I/S/B/U/J 五型立即數從散落的 bit 重排、拼回、符號延伸。B/J 最低位補 0（目標對齊 2）；U-type 不符號延伸（補 0）。
- 兩塊都是純組合，都用**真指令機器碼 + objdump 對照**驗證，全過。

## 自我檢核

- [ ] 我能說出全部九個控制訊號各管 datapath 的哪塊，並畫出 load 指令該開哪些訊號。
- [ ] 我能解釋為什麼 add/sub 判斷要限定 R-type，而 srl/sra 不用，各舉一個會出錯的反例。
- [ ] 我能說明「每段開頭預設清 0」在防什麼（latch），以及漏掉會怎樣。
- [ ] 我能解釋 LUI 為何不給 ALU 加新 op，而是用 `lui` 訊號清零 a 端。
- [ ] 我能背出五型立即數各從 instr 哪些 bit 拼出、哪些要符號延伸、哪些最低位補 0。
- [ ] 我能拿一條真指令機器碼（如 `beq ra,ra,+8` = 0x00108463）手算它的立即數，並說明 B-type 的 bit 怎麼重排。

## 延伸閱讀

- **《Computer Organization and Design, RISC-V Edition》(Patterson & Hennessy) 第 4.4 節「A Simple Implementation Scheme」**：讀它的主控制真值表（Figure 的 control signals 表）和 ALU control 那張表，對照本章兩段式產生法。特別看它的 2-bit ALUOp 中間信號怎麼和 funct 組出最終 alu_op——本課合併了這層，看它拆開的版本能理解為什麼有人要分兩塊。
- **《Digital Design and Computer Architecture, RISC-V Edition》(Harris & Harris) 第 7.3 節的 control unit 部分與立即數 extend 單元**：Harris 把 immediate extend 畫成一個獨立的 extend block，並列出各型立即數的 bit 對接——和本章 imm_gen 一一對應，圖比文字更好懂 B/J 的重排。
- **[RISC-V Unprivileged ISA Spec](https://riscv.org/technical/specifications/) 第 2.2「Base Instruction Formats」與 2.3「Immediate Encoding Variants」**：權威的立即數 bit 重排表（那張把 B/J immediate 每一位標得清清楚楚的圖），是你 imm_gen 接線時的最終依據。B/J 為什麼那樣排的設計理由也在這節。
- **[picorv32 原始碼](https://github.com/YosysHQ/picorv32) 搜 `decoder` 與 `decoded_imm`**：看一個真 core 怎麼 decode——它把控制訊號存成一堆 `instr_xxx` 的 reg，立即數用一個大 case 依格式組出。對照你會發現「教學把 decode 攤平成一個 case」和「真 core 為時序把 decode 拆成多個階段/暫存」的差異。

decode 把指令翻譯完了——控制訊號指揮、立即數備好。但目前 datapath 只會一直線往下跑。下一章我們補上 **load/store/branch/jump** 的資料通路：讓 CPU 能存取記憶體、能依條件跳轉、能呼叫返回，把單週期 datapath 的最後幾塊拼齊。

→ [Ch 11 Load/Store/Branch/Jump datapath](./11-load-store-branch-datapath.md)
