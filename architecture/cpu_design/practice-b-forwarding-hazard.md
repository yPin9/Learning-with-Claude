# 練習 B — 自刻 forwarding unit + hazard detection unit

> **目標**：不看前面的參考實作，自己從頭把 **forwarding unit** 和 **hazard detection unit** 刻出來，讓一顆「hazard 邏輯被挖空」的五級 pipelined core 通過三組刻意設計的 hazard 測試——RAW（forwarding 解決）、load-use（stall 解決）、branch（flush 解決）。這是把 Ch 16–19「看得懂」變成「寫得出」的關卡。你會親手體驗：挖空的 core 怎麼算錯，補上邏輯後怎麼算對。
> **環境**：WSL + verilator 4.038 + riscv toolchain。全程真跑。

## 這個練習在練什麼

Ch 16–19 我們一路把 forwarding、stall、flush 讀懂了。但讀懂和寫得出是兩回事——hazard 邏輯的偵測條件、優先序、rd!=x0 這些細節，不自己踩一遍不會真的內化。

這個練習給你一顆**故意把 hazard 邏輯挖空**的 core：datapath（IF/ID/EX/MEM/WB、pipeline register、ALU、regfile、記憶體）都接好了，但 forwarding unit 永遠回 `00`（不 forward）、hazard unit 永遠不 stall 不 flush。這顆 core 跑無 hazard 的程式會對，一遇到 hazard 就算錯。你的任務是把三塊挖空補回來，讓它通過測試。

**評分標準**：三組測試的最終暫存器狀態，全部和手算預期一致。不准改 datapath，只准動 forwarding unit 和 hazard detection unit 這兩塊（就像真實工作裡「datapath 別人搭好，你負責 hazard control」）。

## 任務規格

給你的檔案：

- `core_skeleton.sv` — 挖空的 pipelined core（datapath 完整，hazard 邏輯是 TODO）。
- `alu.sv` — 課程約定的 ALU（Ch 9，直接用）。
- `pb_tb.cpp` — 測試 harness，跑程式並 dump 最終暫存器。
- 三個測試程式的 `.S` 和組譯出的 `.hex`。

你要填的三塊（core_skeleton.sv 裡標 `// TODO`）：

1. **EX 級 forwarding unit**：算 `fwd_a`/`fwd_b`（2-bit select），解決 RAW hazard。
2. **load-use hazard detection**：算 `load_use_hazard`，觸發 stall。
3. **hazard control + branch flush**：綜合出 `pc_write`/`if_id_write`/`id_ex_bubble`/`if_id_flush`，含優先序。

（本練習把 branch resolve 放在 **EX 級**簡化——這樣不用處理 ID forwarding 和 branch-use，聚焦在最核心的三塊。branch 提前到 ID 是延伸挑戰。）

## 挖空的 core：core_skeleton.sv

datapath 都接好了，你只要填三個 `// TODO` 區塊。完整檔案：

```systemverilog
// core_skeleton.sv — 練習 B：hazard 邏輯挖空版（branch 在 EX resolve）
// datapath 完整，你只需填三個 TODO 區塊。約定沿用全課。
module core #(parameter INIT_FILE = "") (
    input  logic        clk,
    input  logic        rst,
    input  logic [4:0]  dbg_reg_sel,
    output logic [31:0] dbg_reg_data
);
    localparam logic [31:0] RESET_PC = 32'h8000_0000;
    logic [31:0] imem [0:1023];
    logic [31:0] dmem [0:1023];
    initial if (INIT_FILE != "") $readmemh(INIT_FILE, imem);

    // ---- IF ----
    logic [31:0] if_pc, if_pc_next, if_inst;
    logic        pc_write, if_id_write, if_id_flush, id_ex_bubble;
    always_ff @(posedge clk) begin
        if (rst) if_pc <= RESET_PC; else if (pc_write) if_pc <= if_pc_next;
    end
    assign if_inst = imem[(if_pc - RESET_PC) >> 2];

    // ---- IF/ID ----
    logic [31:0] if_id_pc, if_id_inst;
    always_ff @(posedge clk) begin
        if (rst || if_id_flush) begin
            if_id_pc <= 0; if_id_inst <= 32'h0000_0013;   // NOP
        end else if (if_id_write) begin
            if_id_pc <= if_pc; if_id_inst <= if_inst;
        end
    end

    // ---- ID ----
    // 注意：這些是「衍生訊號」，必須用 assign（持續驅動），不能寫成
    // `logic x = expr;`——後者是宣告時的一次性初始化，之後 if_id_inst 變了也不會更新，
    // 你的 decode 會永遠卡在 reset 值。這是 SystemVerilog 常見陷阱。
    logic [6:0] id_opcode; logic [4:0] id_rd; logic [2:0] id_funct3;
    logic [4:0] id_rs1; logic [4:0] id_rs2; logic [6:0] id_funct7;
    assign id_opcode = if_id_inst[6:0];
    assign id_rd     = if_id_inst[11:7];
    assign id_funct3 = if_id_inst[14:12];
    assign id_rs1    = if_id_inst[19:15];
    assign id_rs2    = if_id_inst[24:20];
    assign id_funct7 = if_id_inst[31:25];
    localparam OP_R=7'b0110011, OP_I=7'b0010011, OP_LW=7'b0000011,
               OP_SW=7'b0100011, OP_BR=7'b1100011, OP_LUI=7'b0110111;
    logic id_reg_write, id_mem_read, id_mem_write, id_alu_src, id_branch, id_mem_to_reg;
    logic [3:0] id_alu_op; logic [31:0] id_imm;
    always_comb begin
        unique case (id_opcode)
            OP_I,OP_LW: id_imm = {{20{if_id_inst[31]}}, if_id_inst[31:20]};
            OP_SW:  id_imm = {{20{if_id_inst[31]}}, if_id_inst[31:25], if_id_inst[11:7]};
            OP_BR:  id_imm = {{20{if_id_inst[31]}}, if_id_inst[7], if_id_inst[30:25],
                              if_id_inst[11:8], 1'b0};
            OP_LUI: id_imm = {if_id_inst[31:12], 12'b0};
            default: id_imm = 0;
        endcase
    end
    always_comb begin
        id_reg_write=0; id_mem_read=0; id_mem_write=0;
        id_alu_src=0; id_branch=0; id_mem_to_reg=0; id_alu_op=4'b0000;
        unique case (id_opcode)
            OP_R: begin id_reg_write=1;
                unique case (id_funct3)
                    3'b000: id_alu_op = id_funct7[5] ? 4'b0001 : 4'b0000;
                    3'b111: id_alu_op=4'b1001; 3'b110: id_alu_op=4'b1000;
                    3'b100: id_alu_op=4'b0101; 3'b010: id_alu_op=4'b0011;
                    3'b001: id_alu_op=4'b0010; default: id_alu_op=4'b0000;
                endcase end
            OP_I: begin id_reg_write=1; id_alu_src=1;
                unique case (id_funct3)
                    3'b111: id_alu_op=4'b1001; 3'b110: id_alu_op=4'b1000;
                    default: id_alu_op=4'b0000;
                endcase end
            OP_LW: begin id_reg_write=1; id_mem_read=1; id_alu_src=1; id_mem_to_reg=1; end
            OP_SW: begin id_mem_write=1; id_alu_src=1; end
            OP_BR: begin id_branch=1; id_alu_op=4'b0001; end
            OP_LUI:begin id_reg_write=1; id_alu_src=1; end
            default: ;
        endcase
    end
    // LUI 的 rs1 欄位是 imm，當 x0
    logic [4:0] id_rs1_eff;
    assign id_rs1_eff = (id_opcode==OP_LUI) ? 5'd0 : id_rs1;

    logic [31:0] regs [0:31];
    // 後級 WB 訊號（提前宣告，供 write-first bypass 引用）
    logic        wb_reg_write; logic [4:0] wb_rd; logic [31:0] wb_data;
    // regfile 讀（async），含 write-first bypass：WB 這一拍要寫的值，ID 同拍就讀得到。
    // 這化解「距離 3」的 RAW（producer 在 WB、consumer 在 ID 同拍讀），讓 forwarding
    // 只需管 EX/MEM 與 MEM/WB 兩路。少了它，branch 用到 3 條之前算的值會拿到舊值而跳錯。
    logic [31:0] id_rs1_data, id_rs2_data;
    assign id_rs1_data = (id_rs1_eff==0) ? 0 :
                         (wb_reg_write && wb_rd==id_rs1_eff) ? wb_data : regs[id_rs1_eff];
    assign id_rs2_data = (id_rs2==0) ? 0 :
                         (wb_reg_write && wb_rd==id_rs2) ? wb_data : regs[id_rs2];

    // ---- ID/EX ----
    logic id_ex_reg_write,id_ex_mem_read,id_ex_mem_write,id_ex_alu_src,id_ex_mem_to_reg,id_ex_branch;
    logic [3:0] id_ex_alu_op;
    logic [31:0] id_ex_rs1_data,id_ex_rs2_data,id_ex_imm,id_ex_pc;
    logic [4:0] id_ex_rs1,id_ex_rs2,id_ex_rd; logic [2:0] id_ex_funct3;
    always_ff @(posedge clk) begin
        if (rst || id_ex_bubble) begin
            id_ex_reg_write<=0; id_ex_mem_read<=0; id_ex_mem_write<=0; id_ex_alu_src<=0;
            id_ex_mem_to_reg<=0; id_ex_branch<=0; id_ex_alu_op<=0;
            id_ex_rs1_data<=0; id_ex_rs2_data<=0; id_ex_imm<=0;
            id_ex_rs1<=0; id_ex_rs2<=0; id_ex_rd<=0; id_ex_funct3<=0; id_ex_pc<=0;
        end else begin
            id_ex_reg_write<=id_reg_write; id_ex_mem_read<=id_mem_read;
            id_ex_mem_write<=id_mem_write; id_ex_alu_src<=id_alu_src;
            id_ex_mem_to_reg<=id_mem_to_reg; id_ex_branch<=id_branch; id_ex_alu_op<=id_alu_op;
            id_ex_rs1_data<=id_rs1_data; id_ex_rs2_data<=id_rs2_data; id_ex_imm<=id_imm;
            id_ex_rs1<=id_rs1_eff; id_ex_rs2<=id_rs2; id_ex_rd<=id_rd;
            id_ex_funct3<=id_funct3; id_ex_pc<=if_id_pc;
        end
    end

    // 後級訊號（forwarding 來源）。wb_reg_write/wb_rd/wb_data 已於上方 ID 段宣告。
    logic ex_mem_reg_write; logic [4:0] ex_mem_rd; logic [31:0] ex_mem_alu;

    // ==================== TODO 1：EX 級 forwarding unit ====================
    // 目標：算 fwd_a / fwd_b（2-bit）。
    //   2'b10 = 從 EX/MEM 前遞(ex_mem_alu)
    //   2'b01 = 從 MEM/WB 前遞(wb_data)
    //   2'b00 = 不 forward，用 id_ex_rs1_data / id_ex_rs2_data
    // 條件：後級 reg_write=1、rd!=0、rd == id_ex_rs1/rs2。EX/MEM 優先於 MEM/WB。
    logic [1:0] fwd_a, fwd_b;
    always_comb begin
        fwd_a = 2'b00;
        fwd_b = 2'b00;
        // TODO: 填 fwd_a、fwd_b 的判斷邏輯
    end

    // forwarding mux（這段已接好，讀 fwd_a/fwd_b 選來源）
    logic [31:0] ex_fwd_a, ex_fwd_b, ex_alu_b, ex_alu_result; logic ex_zero;
    always_comb begin
        unique case (fwd_a)
            2'b10: ex_fwd_a = ex_mem_alu; 2'b01: ex_fwd_a = wb_data;
            default: ex_fwd_a = id_ex_rs1_data;
        endcase
        unique case (fwd_b)
            2'b10: ex_fwd_b = ex_mem_alu; 2'b01: ex_fwd_b = wb_data;
            default: ex_fwd_b = id_ex_rs2_data;
        endcase
    end
    assign ex_alu_b = id_ex_alu_src ? id_ex_imm : ex_fwd_b;
    alu u_alu (.a(ex_fwd_a), .b(ex_alu_b), .alu_op(id_ex_alu_op),
               .result(ex_alu_result), .zero(ex_zero));

    // branch 在 EX resolve（本練習簡化：EX 級判斷跳不跳）
    logic ex_branch_taken;
    always_comb begin
        ex_branch_taken = 0;
        if (id_ex_branch) unique case (id_ex_funct3)
            3'b000: ex_branch_taken = ex_zero;   // BEQ：SUB==0
            3'b001: ex_branch_taken = !ex_zero;  // BNE
            default: ex_branch_taken = 0;
        endcase
    end
    logic [31:0] ex_branch_target;
    assign ex_branch_target = id_ex_pc + id_ex_imm;

    // ---- EX/MEM ----
    logic ex_mem_mem_read,ex_mem_mem_write,ex_mem_mem_to_reg; logic [31:0] ex_mem_store;
    always_ff @(posedge clk) begin
        if (rst) begin
            ex_mem_reg_write<=0; ex_mem_mem_read<=0; ex_mem_mem_write<=0;
            ex_mem_mem_to_reg<=0; ex_mem_alu<=0; ex_mem_store<=0; ex_mem_rd<=0;
        end else begin
            ex_mem_reg_write<=id_ex_reg_write; ex_mem_mem_read<=id_ex_mem_read;
            ex_mem_mem_write<=id_ex_mem_write; ex_mem_mem_to_reg<=id_ex_mem_to_reg;
            ex_mem_alu<=ex_alu_result; ex_mem_store<=ex_fwd_b; ex_mem_rd<=id_ex_rd;
        end
    end

    // ---- MEM ----
    logic [31:0] mem_rdata;
    always_ff @(posedge clk) if (ex_mem_mem_write) dmem[(ex_mem_alu-RESET_PC)>>2] <= ex_mem_store;
    assign mem_rdata = dmem[(ex_mem_alu-RESET_PC)>>2];

    // ---- MEM/WB ----
    logic mem_wb_reg_write,mem_wb_mem_to_reg; logic [31:0] mem_wb_alu,mem_wb_rdata; logic [4:0] mem_wb_rd;
    always_ff @(posedge clk) begin
        if (rst) begin mem_wb_reg_write<=0; mem_wb_mem_to_reg<=0; mem_wb_alu<=0; mem_wb_rdata<=0; mem_wb_rd<=0; end
        else begin
            mem_wb_reg_write<=ex_mem_reg_write; mem_wb_mem_to_reg<=ex_mem_mem_to_reg;
            mem_wb_alu<=ex_mem_alu; mem_wb_rdata<=mem_rdata; mem_wb_rd<=ex_mem_rd;
        end
    end

    // ---- WB ----
    assign wb_reg_write = mem_wb_reg_write;
    assign wb_rd = mem_wb_rd;
    assign wb_data = mem_wb_mem_to_reg ? mem_wb_rdata : mem_wb_alu;
    always_ff @(posedge clk) if (wb_reg_write && wb_rd!=0) regs[wb_rd] <= wb_data;

    // ==================== TODO 2：load-use hazard detection ====================
    // 目標：算 load_use_hazard。
    // 條件：EX 級是 load(id_ex_mem_read)、rd!=0、rd 命中 ID 級 rs1_eff 或 rs2。
    logic load_use_hazard;
    always_comb begin
        load_use_hazard = 1'b0;
        // TODO: 填 load-use 偵測條件
    end

    // ==================== TODO 3：hazard control + branch flush ====================
    // 目標：算 pc_write / if_id_write / id_ex_bubble / if_id_flush，含優先序。
    //   - load_use_hazard → 凍結 PC/IF-ID + 插 bubble
    //   - ex_branch_taken → flush（branch 在 EX resolve，要 flush IF/ID 和 ID/EX 兩級；
    //     本練習為簡化，只 flush IF/ID，並靠下一拍再 flush 涵蓋——先做 IF/ID flush 即可，
    //     不夠再想第二級。提示見下方。）
    //   - 優先序：stall 先於 flush。
    always_comb begin
        pc_write     = 1'b1;
        if_id_write  = 1'b1;
        id_ex_bubble = 1'b0;
        if_id_flush  = 1'b0;
        // TODO: 填 hazard control 邏輯
    end

    // 下一個 PC（已接好）：branch taken 就跳 target
    assign if_pc_next = ex_branch_taken ? ex_branch_target : (if_pc + 32'd4);

    // 觀測
    assign dbg_reg_data = (dbg_reg_sel==0) ? 0 : regs[dbg_reg_sel];
endmodule
```

> 注意：本練習 branch 在 **EX resolve**（`ex_branch_taken`），比正課的 ID resolve 更輕量——沒有 branch-use、沒有 ID forwarding，聚焦三塊核心。代價是 branch penalty 是 2 拍、flush 要清兩級（見 TODO 3 提示）。

## 測試 harness：pb_tb.cpp

跑 80 拍讓程式流完，dump 你指定的暫存器：

```cpp
#include "Vcore.h"
#include "verilated.h"
#include <cstdio>
static Vcore *dut;
static void tick(){ dut->clk=0; dut->eval(); dut->clk=1; dut->eval(); }
int main(int argc,char**argv){
    Verilated::commandArgs(argc,argv);
    dut=new Vcore; dut->rst=1; tick(); tick(); dut->rst=0;
    for(int c=0;c<80;c++) tick();
    printf("=== final registers ===\n");
    for(int i=1;i<=9;i++){ dut->dbg_reg_sel=i; dut->eval();
        printf("x%-2d = %d\n", i, (int32_t)dut->dbg_reg_data); }
    delete dut; return 0;
}
```

編譯執行（每組測試換 `-GINIT_FILE`）：

```bash
verilator --cc core_skeleton.sv alu.sv --exe pb_tb.cpp \
    -GINIT_FILE='"test1.hex"' --Mdir obj_pb -Wno-WIDTH -Wno-UNOPTFLAT --top-module core
make -s -C obj_pb -f Vcore.mk Vcore
./obj_pb/Vcore
```

## 三組測試程式

組譯流程（每個 `.S` 都這樣轉 hex）：

```bash
riscv64-unknown-elf-gcc -march=rv32i -mabi=ilp32 -nostdlib -Ttext=0x80000000 -o t.elf test.S
riscv64-unknown-elf-objcopy -O binary t.elf t.bin
python3 -c 'import struct;d=open("t.bin","rb").read();d+=b"\x00"*((4-len(d)%4)%4);
[print("%08x"%struct.unpack("<I",d[i:i+4])[0]) for i in range(0,len(d),4)]' > test.hex
```

### 測試 1：RAW（forwarding 解決）—— test1.S

```asm
_start:
    addi x1, x0, 10       # x1 = 10
    addi x2, x1, 5        # x2 = 15   [用剛算的 x1：EX/MEM forward]
    add  x3, x2, x1       # x3 = 25   [x2 用 EX/MEM、x1 用 MEM/WB：兩路 forward]
    sub  x4, x3, x2       # x4 = 10   [x3 用 EX/MEM、x2 用 MEM/WB]
halt:
    beq  x0, x0, halt
```

**手算預期**：x1=10, x2=15, x3=25, x4=10。
只填 TODO 1（forwarding）就該全對。**沒填時**，x2/x3/x4 會拿到舊值（regfile 還沒寫回），全錯。

### 測試 2：load-use（stall + forwarding 解決）—— test2.S

```asm
_start:
    lui  x6, 0x80000      # x6 = 0x80000000（資料基底）
    addi x1, x0, 100      # x1 = 100
    sw   x1, 0(x6)        # mem[base] = 100
    lw   x3, 0(x6)        # x3 = 100   [load]
    add  x4, x3, x3       # x4 = 200   [緊接用 x3：load-use → stall 1 拍]
    addi x5, x4, 1        # x5 = 201
halt:
    beq  x0, x0, halt
```

**手算預期**：x1=100, x3=100, x4=200, x5=201。
要填 TODO 1 + 2 + 3。**只填 forwarding 不填 stall**：x4 會拿到還沒 load 好的 x3（=0），x4=0、x5=1，錯。

### 測試 3：branch（flush 解決）—— test3.S

```asm
_start:
    addi x1, x0, 5
    addi x2, x0, 0        # 分隔，避免 branch-use（branch 在 EX resolve 也建議隔開）
    addi x2, x0, 5
    beq  x1, x2, taken    # 5==5 → taken
    addi x7, x0, 999      # POISON1：不該執行
    addi x7, x0, 888      # POISON2：不該執行
taken:
    addi x3, x0, 42       # x3 = 42
halt:
    beq  x0, x0, halt
```

**手算預期**：x1=5, x2=5, x3=42, **x7=0**（毒指令被 flush）。
要填 TODO 3 的 flush。**沒填 flush**：branch 雖跳對 PC，但已抓進 pipeline 的毒指令會執行，x7=999 或 888，錯。

## 分段實作：建議照這個順序

### 步驟 1：先跑挖空版，看它怎麼錯

別急著填。先原封不動 verilate 挖空的 core，跑三組測試，親眼看它錯在哪：

- test1：x2/x3/x4 全錯（RAW 沒 forward）。
- test2：x4=0（load-use）。
- test3：x7=999（沒 flush）。

看懂「錯在哪、為什麼」，比直接抄答案有用十倍。

### 步驟 2：填 TODO 1（forwarding），過 test1

寫 `fwd_a`/`fwd_b` 的偵測。兩個要點：後級 `reg_write && rd!=0 && rd==id_ex_rsX`；EX/MEM 優先於 MEM/WB（用 `else if`）。填完 test1 應全對，test2 的 x3 讀出 100 但 x4 仍錯（forwarding 救不了 load-use），test3 仍錯（沒 flush）。

### 步驟 3：填 TODO 2 + 3 的 stall，過 test2

寫 `load_use_hazard` 偵測，並在 TODO 3 讓它凍結 PC/IF-ID + 插 bubble。填完 test2 全對（x4=200）。

### 步驟 4：填 TODO 3 的 flush，過 test3

branch 在 EX resolve，taken 時要 flush。先試只 flush IF/ID，跑 test3 看 x7——若還是被污染，想想 branch 在 EX resolve 時**有幾條錯抓的指令在 pipeline 裡**（提示：不只一條）。

### 步驟 5：三組全過，回頭跑一次確認沒回歸

三個都填完後，把三組測試都再跑一次，確認前面的沒被後面的改動弄壞。

## 參考解

每一塊都自己試過、卡住了再看。

<details>
<summary>TODO 1：EX 級 forwarding unit 參考解</summary>

```systemverilog
always_comb begin
    fwd_a = 2'b00;
    fwd_b = 2'b00;
    // rs1：EX/MEM 優先，其次 MEM/WB
    if (ex_mem_reg_write && ex_mem_rd != 0 && ex_mem_rd == id_ex_rs1) fwd_a = 2'b10;
    else if (wb_reg_write && wb_rd != 0 && wb_rd == id_ex_rs1)        fwd_a = 2'b01;
    // rs2：同理
    if (ex_mem_reg_write && ex_mem_rd != 0 && ex_mem_rd == id_ex_rs2) fwd_b = 2'b10;
    else if (wb_reg_write && wb_rd != 0 && wb_rd == id_ex_rs2)        fwd_b = 2'b01;
end
```

關鍵：`else if` 保證 EX/MEM（較新值）蓋過 MEM/WB（較舊值）。`rd != 0` 排除 x0（x0 恆 0，無真相依）。
</details>

<details>
<summary>TODO 2：load-use hazard detection 參考解</summary>

```systemverilog
always_comb begin
    load_use_hazard = id_ex_mem_read && id_ex_rd != 0 &&
                      ((id_ex_rd == id_rs1_eff) || (id_ex_rd == id_rs2));
end
```

關鍵：EX 級是 load（`id_ex_mem_read`）、目標非 x0、且 ID 級某個來源要用它。注意用 `id_rs1_eff`（LUI 已修正成 x0 的那個），不是原始 `id_rs1`。
</details>

<details>
<summary>TODO 3：hazard control + branch flush 參考解</summary>

```systemverilog
always_comb begin
    pc_write     = 1'b1;
    if_id_write  = 1'b1;
    id_ex_bubble = 1'b0;
    if_id_flush  = 1'b0;

    if (load_use_hazard) begin
        // stall：凍結 PC/IF-ID，ID/EX 插 bubble
        pc_write     = 1'b0;
        if_id_write  = 1'b0;
        id_ex_bubble = 1'b1;
    end else if (ex_branch_taken) begin
        // branch 在 EX resolve taken：清 IF/ID 那條，
        // 且 ID/EX 那條（ID 級已抓的下一條）也要清 → 插 bubble
        if_id_flush  = 1'b1;
        id_ex_bubble = 1'b1;
    end
end
```

關鍵：
- **優先序**：`if (load_use_hazard) ... else if (ex_branch_taken)`——stall 先於 flush。
- **branch 在 EX resolve 要清兩級**：branch 到 EX 才知道 taken，這時 IF 級和 ID 級各有一條錯抓的指令（penalty 2 拍）。所以同時 `if_id_flush=1`（清 IF/ID 那條）和 `id_ex_bubble=1`（清 ID/EX 那條，等於把 ID 級正要進 EX 的那條變 NOP）。只清一級的話，test3 的第二條毒指令會漏網——這就是步驟 4 提示要你想的。
</details>

## 卡點提示

**test1 填了 forwarding 還是錯？**
- 檢查偵測條件用的是 `id_ex_rs1`/`id_ex_rs2`（EX 級指令的來源），不是 `id_rs1`/`id_rs2`（ID 級的）。forwarding 是給**正在 EX 級**的指令補值，比對對象是 EX 級的來源。
- 檢查 `else if` 有沒有寫成兩個獨立 `if`——後者會讓 MEM/WB 蓋掉 EX/MEM。

**test2 的 x4 還是 0？**
- 確認 stall 三件事都做了：`pc_write=0`、`if_id_write=0`、`id_ex_bubble=1`。少一件就不對。
- 確認偵測條件的 rs 用 `id_rs1_eff`/`id_rs2`（ID 級要讀的），rd 用 `id_ex_rd`（EX 級 load 的目標）。

**test3 的 x7 只清掉一個毒指令（888 沒了但 999 還在，或反過來）？**
- 這就是「branch 在 EX resolve 要清兩級」的坑。branch 到 EX 才 resolve，IF 和 ID 兩級都有錯抓的指令。只 flush IF/ID 會漏掉 ID/EX 那級的。參考解同時 `if_id_flush` 和 `id_ex_bubble`。
- 如果你把 branch 改到 ID resolve（延伸挑戰），就只需清一級，但要處理 branch-use——取捨。

**stall 和 branch 同拍打架？**
- 本練習的三組測試沒故意製造這個組合，但如果你的 `if/else if` 優先序寫對（stall 先），就算撞上也不會錯。順序反了（flush 先）遲早出事。

## 延伸挑戰

做完基本三塊，想再進階：

1. **branch 提前到 ID resolve**：把 branch 判斷從 EX 移到 ID，penalty 從 2 拍降到 1 拍。你會發現要新增 **ID forwarding**（給 branch 比較器）和 **branch-use stall**（來源還在 EX 時等一拍）。這是正課 Ch 18–19 的完整版，做完你就把教學 core 的 branch 處理補齊了。自己設計一支「branch 緊接算它來源」的測試驗證 branch-use stall。

2. **load→branch 雙 stall**：承上，若 branch 用剛 load 的值，要 stall 兩拍（Ch 19 的刁鑽 case）。加偵測「branch 來源是 EX/MEM 的 load」，再 stall 一拍。寫一支 `lw` 緊接 `beq` 用它的程式驗證。

3. **加一支「連續三寫同暫存器」測試**：`addi x1,x0,1` / `addi x1,x1,1` / `addi x1,x1,1`，最後 x1 應該是 3。這專門考 forwarding 的「EX/MEM 優先於 MEM/WB」——若優先序反了，中間那次會拿到舊值，結果錯。你的 forwarding 過得了嗎？

4. **structural hazard 實驗**：把 imem 和 dmem 改成**同一塊記憶體**（共用 port），看 IF 和 MEM 同拍存取會不會出問題，想想怎麼靠 stall 解決（或為什麼真 core 用分離 cache 避免）。這讓你親身體會 Ch 19 為什麼強調「structural hazard 靠設計避免」。

## 完成檢核

- [ ] 挖空版跑三組測試，我看懂了每組錯在哪、為什麼。
- [ ] 填完 forwarding，test1 四個暫存器全對。
- [ ] 填完 load-use stall，test2 的 x4=200、x5=201。
- [ ] 填完 branch flush，test3 的 x7=0（兩條毒指令都沒執行）。
- [ ] 三組全過，回歸跑一次沒有互相弄壞。
- [ ] 我能說出 forwarding 為什麼要 `else if`（EX/MEM 優先）、load-use 為什麼 stall 三件事、branch 在 EX resolve 為什麼要清兩級。
- [ ]（延伸）我試著把 branch 移到 ID resolve，處理了 branch-use。

## 延伸閱讀

- **《Computer Organization and Design, RISC-V Edition》(Patterson & Hennessy) 第 4.7 節的 forwarding unit 真值表（圖 4.53）與 hazard detection unit（圖 4.59）**：把本練習三塊的偵測條件用真值表列全，你填 TODO 前對一遍、填完後核一遍，確保沒漏 corner case。這是最直接的「答案對照表」。
- **[Sodor rv32_5stage 的 `cpath.scala`](https://github.com/ucb-bar/riscv-sodor)**：官方教學 core 的 control/hazard 原始碼。做完本練習去讀它，你會看到自己剛刻的 forwarding/stall/flush 在工業教學 core 裡長什麼樣——結構一致，是最好的「我做的對不對」對照組。特別看它怎麼處理 branch resolve 位置的選擇。
- **《Digital Design and Computer Architecture, RISC-V Edition》(Harris & Harris) 第 7.5 節的 HDL 範例**：它的 hazard unit HDL 和本練習的 skeleton 風格幾乎一樣（Verilog），卡在語法或訊號接線時對照它。它也有完整的 pipelined processor HDL，可當你做完延伸挑戰後的完整參考。
- **[riscv-tests rv32ui 的 branch/load 測試](https://github.com/riscv-software-src/riscv-tests/tree/master/isa/rv32ui)**（如 `beq.S`、`lw.S`、`add.S`）：官方怎麼設計 hazard 測試。讀它們的測試模式（連續相依、各種 forwarding 距離），你會學到比本練習三組更狠的 corner case，是把 core 推向「打穿官方測試」的下一步。

做完這個練習，你不只讀懂了 hazard 處理，是**親手刻出來、親眼驗證過**了。forwarding 的優先序、stall 的三件事、flush 的級數——這些細節現在長在你手上，不在紙上。Part 2 的 pipeline 到此紮實走完，接下來 Part 3 我們讓 branch 不再每次都 penalty——上 branch prediction。

→ [Ch 21 branch prediction 基礎：BTB、2-bit 飽和計數器](./21-branch-prediction-basics.md)
