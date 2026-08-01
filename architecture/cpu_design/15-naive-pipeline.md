# Ch 15 — naive pipeline：先切開，故意跑錯給你看

> **目標**：把 Ch 14 的骨架接上真正的 datapath，組出一顆完整的五級 pipelined core——但**故意不處理任何 hazard**。我們跑一段有資料相依的 code（連續 `add`，後一條要用前一條剛算的結果），用 verilator 真跑，親眼看它算出**錯誤**的答案。然後逐 cycle 追出錯在哪、為什麼錯。這是全 Part 最重要的一次「教學性失敗」——把 data hazard 從抽象名詞變成你波形裡看得到的 bug。Ch 16 才修它。

前兩章講完 pipeline 的為什麼與骨架。你可能覺得：切五級、插 pipeline register、控制訊號帶下去，這樣就有一顆能跑的 pipelined CPU 了吧？程式碼上是。但它**會算錯**。這章我們不急著修，先讓它錯給你看——因為看不到錯在哪，就不會真懂 forwarding 在救什麼。

## 為什麼要故意跑錯？

你可以直接讀 Ch 16 的 forwarding，背下「比對 rd 和 rs1/rs2、撞了就前遞」。但那是死記。真正的理解來自：**先親眼看到不 forward 會怎樣錯，錯的數字長什麼樣，逐 cycle 追出資料在哪個環節斷了。** 有了這個「錯誤現場」，forwarding 才不是天上掉下來的規則，而是「喔，就是要補這個洞」。

這門課的方法論是「故意把它弄壞」。這章是這方法論最核心的一次實踐。

## 先建立直覺：後一條指令搶跑，前一條還沒把結果放好

回想 Ch 13 的斜階梯圖，聚焦兩條有相依的指令：

```
   add x3, x1, x2   # 指令A：算出 x3
   add x4, x3, x1   # 指令B：要用 x3（A 剛算的）

     cycle→    1    2    3    4    5    6    7
   指令A:     IF   ID   EX   MEM  WB
                        └A算x3    └A把x3寫回regfile(WB)
   指令B:          IF   ID   EX   MEM  WB
                        └B在ID讀x3！
```

看 cycle 3：指令B 在 **ID 級讀 x3**。但指令A 要到 **cycle 5 的 WB** 才把 x3 寫回 register file。**B 讀 x3 的時候（cycle 3），A 根本還沒把 x3 寫進去**——A 這時才在 EX 算 x3，結果還在 EX 級的線上，沒進 register file。

所以 B 從 register file 讀到的 x3 是**舊值**（reset 後是 0）。B 拿著錯的 x3 去算 x4，x4 就錯了。

這就是 **data hazard**（資料危害）的本質：**一條指令需要的資料，前一條指令還沒把它放到該讀的地方（register file）。** pipeline 讓指令重疊，重疊得太快，後面的來讀時前面的還沒寫好。時間差是 pipeline 的紅利，也是 hazard 的根源。

## 核心概念：完整的 naive pipelined core

我們把 Ch 14 的骨架接滿 datapath。用到 Part 1 建好的 `alu`、`regfile`、`imm_gen`，加一個本 Part 前半夠用的 `control_unit`（只處理 R-type 與 I-type 算術，足以示範 data hazard）。

先看 `control_unit.sv`——它把 opcode/funct3/funct7 解成三個控制訊號：

```systemverilog
// control_unit.sv — 從 opcode/funct3/funct7 解出控制訊號
// 本課 Part 2 前半只做 R-type 與 I-type 算術（足以示範 data hazard）
module control_unit (
    input  logic [6:0] opcode,
    input  logic [2:0] funct3,
    input  logic [6:0] funct7,
    output logic       reg_write,   // 是否寫回 regfile
    output logic       alu_src,     // ALU 第二運算元：0=rs2_data, 1=imm
    output logic [3:0] alu_op       // 餵給 ALU 的 4-bit op
);
    // funct3 → alu_op（R/I 算術共用），add/sub 與 srl/sra 靠 funct7[5] 分家
    logic [3:0] arith_op;
    always_comb begin
        unique case (funct3)
            3'b000: arith_op = (opcode == 7'b0110011 && funct7[5]) ? 4'b0001 : 4'b0000; // SUB/ADD
            3'b001: arith_op = 4'b0010; // SLL
            3'b010: arith_op = 4'b0011; // SLT
            3'b011: arith_op = 4'b0100; // SLTU
            3'b100: arith_op = 4'b0101; // XOR
            3'b101: arith_op = funct7[5] ? 4'b0111 : 4'b0110; // SRA/SRL
            3'b110: arith_op = 4'b1000; // OR
            3'b111: arith_op = 4'b1001; // AND
            default: arith_op = 4'b0000;
        endcase
    end

    always_comb begin
        reg_write = 1'b0; alu_src = 1'b0; alu_op = 4'b0000; // 預設安全值
        unique case (opcode)
            7'b0110011: begin reg_write = 1'b1; alu_src = 1'b0; alu_op = arith_op; end // R-type
            7'b0010011: begin reg_write = 1'b1; alu_src = 1'b1; alu_op = arith_op; end // I-type 算術
            default: ; // 其他指令本半 Part 不處理
        endcase
    end
endmodule
```

接著是核心 `core_naive.sv`。它把五級全接起來、四個 pipeline register 就位，指令記憶體用 `$readmemh` 從 `prog.hex` 載入。**注意：它沒有任何 forwarding、沒有任何 stall——純切級。**

```systemverilog
// core_naive.sv — 五級 pipeline，故意「不處理任何 hazard」
module core_naive (
    input  logic        clk,
    input  logic        rst,
    input  logic [4:0]  dbg_addr,   // debug 觀測口：直接看 regfile
    output logic [31:0] dbg_data
);
    // ---------------- IF ----------------
    logic [31:0] if_pc, if_pc_next, if_instr;
    logic [31:0] imem [0:255];
    always_ff @(posedge clk) begin
        if (rst) if_pc <= 32'h8000_0000;
        else     if_pc <= if_pc_next;
    end
    assign if_pc_next = if_pc + 32'd4;
    assign if_instr   = imem[(if_pc - 32'h8000_0000) >> 2];  // 字組定址取 ROM index

    // ---- IF/ID pipeline register ----
    logic [31:0] if_id_pc, if_id_instr;
    always_ff @(posedge clk) begin
        if (rst) begin
            if_id_pc    <= 32'd0;
            if_id_instr <= 32'h0000_0013;   // NOP = addi x0,x0,0
        end else begin
            if_id_pc    <= if_pc;
            if_id_instr <= if_instr;
        end
    end

    // ---------------- ID ----------------
    logic [6:0] id_opcode; logic [2:0] id_funct3; logic [6:0] id_funct7;
    logic [4:0] id_rs1, id_rs2, id_rd;
    logic       id_reg_write, id_alu_src; logic [3:0] id_alu_op;
    logic [31:0] id_rs1_data, id_rs2_data, id_imm;
    assign id_opcode = if_id_instr[6:0];
    assign id_funct3 = if_id_instr[14:12];
    assign id_funct7 = if_id_instr[31:25];
    assign id_rs1 = if_id_instr[19:15];
    assign id_rs2 = if_id_instr[24:20];
    assign id_rd  = if_id_instr[11:7];

    control_unit u_ctrl (.opcode(id_opcode), .funct3(id_funct3), .funct7(id_funct7),
                         .reg_write(id_reg_write), .alu_src(id_alu_src), .alu_op(id_alu_op));
    imm_gen u_imm (.instr(if_id_instr), .imm(id_imm));

    logic wb_reg_write; logic [4:0] wb_rd; logic [31:0] wb_data;   // WB 級寫回訊號（見下）
    regfile u_rf (.clk(clk), .rd_we(wb_reg_write), .rd_addr(wb_rd), .rd_data(wb_data),
                  .rs1_addr(id_rs1), .rs2_addr(id_rs2),
                  .rs1_data(id_rs1_data), .rs2_data(id_rs2_data));

    // ---- ID/EX pipeline register（控制訊號一路帶下去）----
    logic id_ex_reg_write, id_ex_alu_src; logic [3:0] id_ex_alu_op;
    logic [31:0] id_ex_rs1_data, id_ex_rs2_data, id_ex_imm; logic [4:0] id_ex_rd;
    always_ff @(posedge clk) begin
        if (rst) begin
            id_ex_reg_write <= 1'b0; id_ex_alu_src <= 1'b0; id_ex_alu_op <= 4'b0000;
            id_ex_rs1_data <= 32'd0; id_ex_rs2_data <= 32'd0; id_ex_imm <= 32'd0; id_ex_rd <= 5'd0;
        end else begin
            id_ex_reg_write <= id_reg_write; id_ex_alu_src <= id_alu_src; id_ex_alu_op <= id_alu_op;
            id_ex_rs1_data <= id_rs1_data; id_ex_rs2_data <= id_rs2_data; id_ex_imm <= id_imm; id_ex_rd <= id_rd;
        end
    end

    // ---------------- EX ----------------
    logic [31:0] ex_alu_a, ex_alu_b, ex_alu_result; logic ex_zero;
    assign ex_alu_a = id_ex_rs1_data;
    assign ex_alu_b = id_ex_alu_src ? id_ex_imm : id_ex_rs2_data;
    alu u_alu (.a(ex_alu_a), .b(ex_alu_b), .alu_op(id_ex_alu_op),
               .result(ex_alu_result), .zero(ex_zero));

    // ---- EX/MEM pipeline register ----
    logic ex_mem_reg_write; logic [31:0] ex_mem_alu_result; logic [4:0] ex_mem_rd;
    always_ff @(posedge clk) begin
        if (rst) begin ex_mem_reg_write <= 1'b0; ex_mem_alu_result <= 32'd0; ex_mem_rd <= 5'd0;
        end else begin ex_mem_reg_write <= id_ex_reg_write; ex_mem_alu_result <= ex_alu_result; ex_mem_rd <= id_ex_rd; end
    end

    // ---------------- MEM（本半 Part 無 load/store，直通）----------------
    logic mem_reg_write; logic [31:0] mem_result; logic [4:0] mem_rd;
    assign mem_reg_write = ex_mem_reg_write;
    assign mem_result    = ex_mem_alu_result;
    assign mem_rd        = ex_mem_rd;

    // ---- MEM/WB pipeline register ----
    logic mem_wb_reg_write; logic [31:0] mem_wb_result; logic [4:0] mem_wb_rd;
    always_ff @(posedge clk) begin
        if (rst) begin mem_wb_reg_write <= 1'b0; mem_wb_result <= 32'd0; mem_wb_rd <= 5'd0;
        end else begin mem_wb_reg_write <= mem_reg_write; mem_wb_result <= mem_result; mem_wb_rd <= mem_rd; end
    end

    // ---------------- WB ----------------
    assign wb_reg_write = mem_wb_reg_write;
    assign wb_rd        = mem_wb_rd;
    assign wb_data      = mem_wb_result;

    assign dbg_data = (dbg_addr == 5'd0) ? 32'd0 : u_rf.regs[dbg_addr];  // debug 讀口
    initial $readmemh("prog.hex", imem);
endmodule
```

這顆 core 完全照 Ch 14 的骨架，資料與控制訊號逐級傳。它**編得過、跑得動、時序完全合法**——就是會算錯。錯不在語法，在微架構少了 hazard 處理。

## 底層機制：跑一段有相依的 code

我們寫一段每條都依賴前一條的 code：

```asm
    addi x1, x0, 5      # x1 = 5           （不依賴任何前面的結果）
    addi x2, x0, 10     # x2 = 10          （不依賴）
    add  x3, x1, x2     # x3 = 5 + 10 = 15 （依賴 x1, x2）
    add  x4, x3, x1     # x4 = 15 + 5 = 20 （依賴 x3——前一條剛算的！）
    add  x5, x4, x4     # x5 = 20+20 = 40  （依賴 x4——前一條剛算的！）
    nop ×5              # 讓 pipeline 排空
```

正確答案：x1=5, x2=10, x3=15, x4=20, x5=40。

用 RISC-V toolchain 組譯、轉成 verilator 要的 hex：

```bash
riscv64-unknown-elf-gcc -march=rv32i -mabi=ilp32 -nostdlib -Ttext=0x80000000 -o prog.elf prog.S
riscv64-unknown-elf-objcopy -O binary prog.elf prog.bin
python3 -c 'import struct; d=open("prog.bin","rb").read(); \
  w=struct.unpack("<%dI"%(len(d)//4),d); \
  open("prog.hex","w").write("\n".join("%08x"%x for x in w)+"\n")'
```

反組譯確認（`li` 是 `addi ...,x0,...` 的組合語言別名，`nop` 是 `addi x0,x0,0`）：

```
80000000: 00500093  li   ra,5        # addi x1,x0,5
80000004: 00a00113  li   sp,10       # addi x2,x0,10
80000008: 002081b3  add  gp,ra,sp    # add  x3,x1,x2
8000000c: 00118233  add  tp,gp,ra    # add  x4,x3,x1
80000010: 004202b3  add  t0,tp,tp    # add  x5,x4,x4
80000014: 00000013  nop
...
```

（`ra/sp/gp/tp/t0` 分別是 x1/x2/x3/x4/x5 的 ABI 名，objdump 用 ABI 名顯示，指令本體一樣。）

testbench 讓它跑 20 拍（足夠所有指令 retire），再讀出 x1~x5：

```cpp
#include "Vcore_naive.h"
#include "verilated.h"
#include <cstdint>
#include <cstdio>
static Vcore_naive *dut;
static void tick() { dut->clk = 0; dut->eval(); dut->clk = 1; dut->eval(); }
static uint32_t peek(int a) { dut->dbg_addr = a; dut->eval(); return dut->dbg_data; }

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    dut = new Vcore_naive;
    dut->rst = 1; tick(); dut->rst = 0;         // reset 一拍
    for (int i = 0; i < 20; i++) tick();         // 跑到全部 retire

    struct { int a; uint32_t exp; const char *n; } chk[] = {
        {1,5,"x1"},{2,10,"x2"},{3,15,"x3"},{4,20,"x4"},{5,40,"x5"} };
    int fails = 0;
    for (auto &c : chk) {
        uint32_t got = peek(c.a);
        bool ok = (got == c.exp);
        printf("[%s] %-3s = %2u  (exp %2u)\n", ok ? "OK " : "BAD", c.n, got, c.exp);
        if (!ok) fails++;
    }
    printf("\n%s (%d wrong)\n", fails ? "WRONG RESULT" : "ALL CORRECT", fails);
    delete dut;
    return 0;   // 這章就是要看它算錯，永遠回 0
}
```

編譯執行：

```bash
verilator --cc core_naive.sv alu.sv regfile.sv control_unit.sv imm_gen.sv \
  --exe core_tb.cpp --top-module core_naive --Mdir obj_naive
make -C obj_naive -f Vcore_naive.mk Vcore_naive
./obj_naive/Vcore_naive
```

**真實輸出（這就是那次教學性失敗）**：

```
[OK ] x1  =  5  (exp  5)
[OK ] x2  = 10  (exp 10)
[BAD] x3  =  0  (exp 15)
[BAD] x4  =  0  (exp 20)
[BAD] x5  =  0  (exp 40)

WRONG RESULT (3 wrong)
```

x1、x2 對，x3、x4、x5 全是 **0**。這不是隨機錯——是有規律的錯，規律就藏著 hazard 的真相。

## 逐 cycle 追：資料在哪斷了

為什麼 x1、x2 對，x3 之後全 0？因為 x1、x2 用的是立即數（`addi ...,x0,...`），只依賴 x0（恆 0），**沒有相依**；x3、x4、x5 都要用前面剛算的暫存器，**有相依**，就中招了。

我們在 ID 級印出「當前指令讀的 rs1/rs2 位址與讀出的值」和「WB 級正在寫回誰」，逐 cycle 看資料流：

```
cyc | if_id_instr | id_rs1 id_rs2 | rs1_data rs2_data | wb_rd wb_data we
 0  | 00000013    | x0     x0     |        0        0 | x0          0  0
 1  | 00500093    | x0     x5     |        0        0 | x0          0  0
 2  | 00a00113    | x0     x10    |        0        0 | x0          0  0
 3  | 002081b3    | x1     x2     |        0        0 | x0          0  1   ← add x3,x1,x2 在 ID
 4  | 00118233    | x3     x1     |        0        0 | x1          5  1   ← add x4,x3,x1 在 ID；x1 此刻才寫回
 5  | 004202b3    | x4     x4     |        0        0 | x2         10  1   ← add x5,x4,x4 在 ID；x2 此刻才寫回
 6  | 00000013    | x0     x0     |        0        0 | x3          0  1   ← x3 寫回，但值是 0（錯的）
 7  | 00000013    | x0     x0     |        0        0 | x4          0  1
 8  | 00000013    | x0     x0     |        0        0 | x5          0  1
```

盯著 cycle 3 那行（`add x3,x1,x2` 在 ID 讀 x1、x2）：

- `id_rs1=x1, id_rs2=x2`，但 `rs1_data=0, rs2_data=0`。
- 為什麼？看 `wb` 欄：x1 要到 **cycle 4** 才寫回（那行 `wb_rd=x1, wb_data=5`），x2 要到 **cycle 5**。而 `add x3` 在 cycle 3 就讀了——**比 x1、x2 寫回早了 1~2 個 cycle**。register file 裡 x1、x2 還是初始的 0。
- 於是 x3 = 0 + 0 = 0。cycle 6 x3 被寫回，值就是這個錯的 0。

同理 cycle 4 的 `add x4,x3,x1`：讀 x3、x1 都得 0（x3 根本還沒算完更沒寫回，x1 這個 cycle 才剛寫）。cycle 5 的 `add x5,x4,x4` 讀 x4 也是 0。錯誤像骨牌一路傳下去。

**這就是 data hazard 的完整現場**：`add x3` 在 cycle 3 讀 register file 時，它依賴的 x1/x2 還躺在前面指令的 pipeline 裡（在 EX 或 EX/MEM register），**沒進 register file**。ID 級只會從 register file 讀，讀到的自然是舊值 0。

用時序圖看 `add x3,x1,x2` 對 x1 的相依（x1 由 `addi x1` 產生）：

```
   addi x1,x0,5:   IF₁ ID₁ EX₁ MEM₁ WB₁       ← WB₁ 在 cycle 4 才寫 x1
   addi x2,x0,10:      IF₂ ID₂ EX₂ MEM₂ WB₂    ← WB₂ 在 cycle 5 才寫 x2
   add x3,x1,x2:          IF₃ ID₃ ...          ← ID₃ 在 cycle 3 就要讀 x1,x2
                              ↑
                    cycle 3：ID₃ 讀 x1，但 x1 要 cycle 4(WB₁)才寫好
                             讀 x2，但 x2 要 cycle 5(WB₂)才寫好
                             → 讀到舊值 0，x3 算成 0
```

x1 的寫回（cycle 4）比 x3 的讀取（cycle 3）**晚一個 cycle**，x2 更晚兩個 cycle。這個「寫回晚於讀取」的時間差，就是 hazard 的量化本質。

## 對比取捨：naive 到底錯在哪、有哪些修法

| 面向 | naive pipeline（本章） | 可能的修法 | 評價 |
|---|---|---|---|
| 正確性 | **錯**（相依指令讀到舊值） | 見下三列 | naive 不可用 |
| 修法一：塞 NOP | 靠 compiler 在相依指令間插夠多 NOP | 軟體補洞 | 簡單但浪費 cycle、且需重編 |
| 修法二：stall | 硬體偵測到相依就凍住後面指令等資料寫回 | 硬體卡管 | 對，但每次相依都卡 2~3 cycle，慢 |
| 修法三：forwarding | 資料在 EX/MEM 算好就直接遞給需要的 EX，不等寫回 | 硬體前遞 | **最優，不卡管**（Ch 16 主角） |

naive 的錯，本質是「ID 只從 register file 讀，而 register file 的資料落後於實際算出的時間」。修法三 forwarding 的洞見是：**x1 在 cycle 2 的 EX 就算出來了（`addi` 的 ALU 結果），根本不用等到 cycle 4 寫回——直接把 EX/MEM register 裡那個值遞給需要它的 EX 級就好**。這是 Ch 16 的核心。

## 踩雷區

**雷 1：以為 pipeline 切好、控制訊號帶對，就會算對。**
- 錯誤直覺：「datapath 接滿、pipeline register 都在、時序合法，功能就對了」。
- 正確認識：時序合法 ≠ 功能正確。naive core 語法零錯、時序零違規，但因為 pipeline 讓指令重疊，相依指令在 ID 讀 register file 時，前一條的結果還沒寫回。**hazard 是微架構層的正確性問題，不是語法或時序問題**。你得額外加 forwarding/stall 才對。

**雷 2：把「x1、x2 對」當成「pipeline 大致沒問題」。**
- 錯誤直覺：「前兩個對，代表 pipeline 基本能動，只是小 bug」。
- 正確認識：x1、x2 對純粹因為它們**沒有 data hazard**（`addi ...,x0,...` 只依賴恆 0 的 x0）。一旦指令依賴前面剛算的暫存器（x3/x4/x5），立刻全錯。對的是「無相依」的指令，錯的是「有相依」的——這恰好精準定位了 hazard 的觸發條件。

**雷 3：想用「多跑幾個 cycle」讓它自己算對。**
- 錯誤直覺：「是不是 cycle 不夠？多跑幾拍值就正確了」。
- 正確認識：不會。x3 在 cycle 6 就以錯的 0 寫回 register file，之後再多跑也不會重算——指令只執行一次，錯就錯定了。錯誤在「執行的當下」就鑄成，時間不會修復它。看 trace：x3 在 cycle 6 寫回 0，x4、x5 隨後也寫回 0，跑到 cycle 20 值還是 0。

**雷 4：以為塞 NOP 是「正解」。**
- 錯誤直覺：「相依指令間插幾個 NOP 就對了，這樣最簡單」。
- 正確認識：插 NOP 確實能讓 register file 有時間寫回（`add x3` 前插 3 個 NOP，等 x1/x2 寫回再讀就對）。但這是**軟體補硬體的洞**：浪費 cycle、綁死指令排程、換 pipeline 深度就得重編。真正的硬體解法是 forwarding——讓硬體自己把算好的資料遞過去，程式不用改、不浪費 cycle。NOP 是應急，不是正解。

## 進階延伸

- **三種 data hazard，naive 只暴露了 RAW**：本章看到的是 RAW（Read After Write，讀在寫之後、卻讀太早）。理論上還有 WAR（Write After Read）與 WAW（Write After Write）。但在**循序、單發射**的五級 pipeline 裡，指令按程式順序流過各級、每條指令只在 WB 寫一次且順序固定，WAR/WAW 天生不會發生——它們是亂序（out-of-order）執行才要對付的（Ch 36 概念會提）。所以本 Part 只需處理 RAW。
- **為什麼把 `regfile.regs` 開成 debug 讀口不影響設計**：`assign dbg_data = u_rf.regs[dbg_addr]` 用 hierarchical reference（階層引用）直接窺看子模組內部，只給 testbench 驗證用，不算 core 的功能路徑，合成時會被剝掉。這是驗證常用手法——給自己開一扇「上帝視角」的窗看內部狀態，比從外部埠一個個接出來省事。
- **naive core 其實在某些 code 上會「碰巧對」**：如果相依指令間本來就隔了夠遠（≥3 條無關指令），前一條早寫回了，naive 也會對。所以 naive 的 bug 是「間歇性」的——只在相依距離近時發作。這種「有時對有時錯」的 bug 最難抓，正是為什麼要有系統的 hazard 處理，而不是靠 code 剛好排得夠鬆。Ch 16 的 forwarding 讓相依距離 1、2 的情況也永遠對。

## 本章重點整理

- 我們把 Ch 14 骨架接滿 datapath，組出完整五級 `core_naive`——**故意不加任何 forwarding/stall**。它語法、時序全合法，但會算錯。
- 跑相依 code（連續 `add`），真實輸出：x1=5、x2=10 對，但 **x3=x4=x5=0 全錯**。對的是無相依指令，錯的是依賴前面剛算結果的指令。
- 逐 cycle trace 定位：`add x3,x1,x2` 在 **cycle 3 的 ID 讀 x1/x2**，但 x1 要 **cycle 4（WB）**、x2 要 **cycle 5** 才寫回 register file——**讀取早於寫回**，讀到舊值 0。錯誤像骨牌傳給 x4、x5。
- 這就是 **data hazard（RAW）** 的完整現場：後面指令要的資料，還躺在前面指令的 pipeline register 裡沒進 register file，而 ID 只會從 register file 讀。
- 修法有三：塞 NOP（軟體補洞、浪費）、stall（硬體卡管、慢）、**forwarding（硬體前遞、最優）**。forwarding 是 Ch 16 主角——它把 EX 早就算好的值直接遞給需要的 EX，不等寫回。

## 自我檢核

- [ ] 我能解釋為什麼 naive core 語法、時序都合法，卻會算錯。
- [ ] 我能說出跑相依 code 後 x1~x5 分別是什麼、為什麼 x1/x2 對而 x3/x4/x5 全 0。
- [ ] 我能對著 trace 指出 `add x3,x1,x2` 在第幾 cycle 讀 x1/x2、x1/x2 又在第幾 cycle 才寫回，並算出時間差。
- [ ] 我能用自己的話定義 data hazard（RAW），並說明它的根源是 pipeline 的哪個特性。
- [ ] 我能列出三種修法並說出各自的代價，解釋為什麼 forwarding 最優、塞 NOP 只是應急。
- [ ] 我能解釋為什麼「多跑幾個 cycle」修不好這個錯。

## 延伸閱讀

- **《Computer Organization and Design, RISC-V Edition》(Patterson & Hennessy) 第 4.7 節「Data Hazards: Forwarding versus Stalling」開頭**：它同樣先用一段相依 code 展示「不處理會怎樣」，再導向 forwarding。本章的失敗案例正對應它開頭那組相依指令的分析，讀它把「為什麼會錯」的教科書論述補齊。
- **《Computer Architecture: A Quantitative Approach》(Hennessy & Patterson) Appendix C.2 的「Data Hazards」小節**：把 RAW/WAR/WAW 三種 data hazard 的定義講最嚴謹，並說明為什麼循序 pipeline 只會遇到 RAW。想搞懂本章「進階延伸」裡 WAR/WAW 為何不發生，讀它。
- **[Sodor 五級 core 的 hazard 相關程式碼](https://github.com/ucb-bar/riscv-sodor)**：對照一顆「已處理 hazard」的教學 core，反推本章 naive 版少了什麼。把它的 forwarding/stall 邏輯拿掉，你就會得到和本章一樣會算錯的 core——這是驗證你理解的好練習。
- **[RISC-V Unprivileged ISA Spec](https://riscv.org/technical/specifications/) 第 2 章關於指令循序語意的敘述**：ISA 保證程式「看起來」是一條接一條循序執行的。本章的 hazard 正是「硬體重疊執行」與「ISA 承諾的循序語意」之間的落差——forwarding/stall 就是為了在重疊執行下維持循序語意的假象。讀它理解「我們到底在維護什麼正確性」。

我們親眼看到 data hazard 把答案算成一堆 0。下一章開始修：forwarding 讓算好的資料不必等寫回，直接遞給需要它的那一級——同一段 code，我們讓它真跑出 15、20、40。

→ [Ch 16 Data hazard（一）：forwarding / bypassing](./16-data-hazard-forwarding.md)
