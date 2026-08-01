# Ch 12 — 單週期 RV32I 完整整合

> **目標**：Part 1 的高潮。把 Ch 7~11 做的所有零件——fetch、regfile、ALU、control_unit、imm_gen、dmem、branch_unit——接成一個完整的 `core` top module，理清每個 mux 的選擇、每條資料流的走向。然後**組一支真的 RV32I 程式（費氏數列迴圈）**灌進去跑，dump 暫存器和記憶體對照手算的預期值，看整顆單週期 CPU 活起來。再用第二支程式驗證 load 寫回、有號/無號延伸、JAL/JALR 呼叫返回。
> **環境**：WSL + verilator 4.038 + riscv64-unknown-elf-gcc 10.2.0。本章所有輸出都是真跑出來的。

## 為什麼整合是獨立一章？

零件各自測過不代表接起來就對。單週期 CPU 的難點從來不在單一模組，而在**它們之間的接線**：

- ALU 的 b 端該接 rs2 還是立即數？（`alu_src` 選）
- 寫回暫存器的值該是 ALU 結果、記憶體資料、還是 pc+4？（三選一 mux）
- PC 下一步該 +4、跳 branch target、還是跳 JALR target？（四選一 mux）
- LUI 怎麼在不動 ALU 的前提下把 imm 放進暫存器？

這些 mux 的選擇訊號來自 control unit，但**接線是整合章的事**。一根線接錯，單一模組測都過、整體卻跑出垃圾，而且沒有 error message——只有 dump 出來一個錯掉的暫存器值。這章就是把接線講清楚、接對、然後用真程式證明整體正確。這也是你第一次看到「一顆能跑真程式的 CPU」，值得慎重。

## 先建立直覺：一張完整的單週期資料流圖

把 Ch 6 的全景圖填滿所有 mux 和訊號：

```
                        ┌──────────────── pc+4 ───────────────┐
                        │                                      │
     ┌────┐  pc    ┌────┴─────┐                                │
 ┌──▶│ PC │───┬───▶│ imem     │──▶ instr ──┐                   │
 │   └────┘   │    └──────────┘            │                   │
 │  pc_next   │                            ▼ (decode 抽欄位)   │
 │            │                    ┌───────────────┐           │
 │            │        ┌───────────│ control_unit  │           │
 │            │        │ 控制訊號   │  imm_gen      │──▶ imm    │
 │            │        ▼           └───────────────┘     │     │
 │            │  ┌───────────┐                           │     │
 │            │  │ regfile   │─rs1_data─┐   ┌── lui?0:rs1─┼──┐  │
 │            │  │  (2R1W)   │─rs2_data─┼─┐ │  ┌alu_src?  │  ▼  │
 │            │  └─────▲─────┘          │ │ ▼  │ imm:rs2  │ ┌──────┐
 │            │        │ rd_data        │ │┌──────┐       └▶│ +imm │→target
 │            │        │                │ ││ ALU  │─result─┐ └──────┘
 │            │        │                │ │└──────┘        │    │
 │            │        │                │ │  │zero         │    │
 │            │        │        ┌───────┘ │  ▼             │    │
 │  ┌─────────┴──┐     │        │ ┌──────────────┐         │    │
 │  │writeback   │◀────┼────────┼─│ branch_unit  │─take─┐  │    │
 │  │ mux        │◀─mem_rdata   │ └──────────────┘      │  │    │
 │  │ jump?pc+4  │     │        ▼                       │  ▼    ▼
 │  │ m2r?mem    │     │  ┌───────────┐            ┌──────────────┐
 │  │ else alu   │     └──│  dmem     │            │ next-PC mux  │
 │  └────────────┘  wdata │ (rs2_data)│            │ branch&take? │
 │                        └───────────┘            │ jalr? jal?   │──┐
 │                          rdata                  │ else pc+4    │  │
 └─────────────────────────────────────────────── └──────────────┘◀─┘
```

看起來密，但拆成五個階段就清楚了（單週期裡它們全在一個 cycle 內組合完成）：

1. **Fetch**：PC → imem → instr。
2. **Decode**：instr 抽欄位 → control_unit 產訊號、imm_gen 產立即數、regfile 讀 rs1/rs2。
3. **Execute**：ALU 算（運算元經 `lui`/`alu_src` 兩個 mux 選），branch_unit 判 take。
4. **Memory**：dmem 讀/寫（位址 = ALU 結果）。
5. **Writeback**：三選一 mux 決定寫回值，同步寫進 regfile；next-PC mux 決定 pc_next。

## 核心概念：三個關鍵 mux

整合的靈魂是三個 mux，每個由 control 訊號選：

### mux 1：ALU 運算元選擇

```
   alu_a = lui ? 0 : rs1_data          （LUI 把 a 清零，算 0+imm）
   alu_b = alu_src ? imm : rs2_data     （用立即數還是 rs2）
```

- `alu_src=1`（I-type、load、store、JALR、LUI）→ b 接立即數。
- `alu_src=0`（R-type、branch）→ b 接 rs2。
- `lui=1` → a 清零，讓 ALU 的 ADD 算出 `0 + imm = imm`（Ch 10 的技巧，不動凍結的 ALU）。

### mux 2：writeback 值選擇（三選一）

```
   rd_data = jump      ? pc+4       :   （JAL/JALR 寫返回位址）
             mem_to_reg? mem_rdata  :   （load 寫記憶體資料）
                         alu_result     （其餘寫 ALU 結果）
```

優先序有講究：`jump` 要排在最前，因為 JALR 同時 `alu_src=1`（ALU 算 target）但**寫回的是 pc+4 不是 target**。若 `mem_to_reg` 排前面也沒事（JALR 的 mem_to_reg=0），但 `jump` 優先最清楚。

### mux 3：next-PC 選擇（四選一）

```
   pc_next = (branch && take)     ? pc + imm            :  （分支成立）
             (jump && opcode=JALR)? (rs1+imm) & ~1       :  （JALR）
              jump                ? pc + imm             :  （JAL）
                                    pc + 4                  （正常前進）
```

這是 PC 改道的全部邏輯。branch 要**同時** `branch=1` 且 `take=1` 才跳（branch_unit 算 take）；JALR 用 `rs1+imm` 且清最低位；JAL 用 `pc+imm`。

## 底層機制：core top module 完整實作

`core.sv`。它把所有子模組實例化並接線，對外只露 clk/rst 和方便觀察的 pc/instr：

```systemverilog
// core.sv — 單週期 RV32I CPU，整合 fetch/decode/execute/memory/writeback
module core #(
    parameter RESET_PC   = 32'h8000_0000,
    parameter IMEM_WORDS  = 256,
    parameter DMEM_WORDS  = 256
) (
    input  logic        clk,
    input  logic        rst,
    output logic [31:0] pc,        // 對外露出方便觀察
    output logic [31:0] instr
);
    // ---------- Fetch ----------
    logic [31:0] imem [0:IMEM_WORDS-1];
    initial $readmemh("prog.hex", imem);

    logic [31:0] pc_next;
    logic [31:0] iword;
    assign iword = (pc - RESET_PC) >> 2;
    assign instr = imem[iword[$clog2(IMEM_WORDS)-1:0]];

    always_ff @(posedge clk) begin
        if (rst) pc <= RESET_PC;
        else     pc <= pc_next;
    end

    // ---------- Decode 欄位 ----------
    logic [6:0] opcode; logic [2:0] funct3; logic funct7_5;
    logic [4:0] rs1_addr, rs2_addr, rd_addr;
    assign opcode   = instr[6:0];
    assign funct3   = instr[14:12];
    assign funct7_5 = instr[30];
    assign rs1_addr = instr[19:15];
    assign rs2_addr = instr[24:20];
    assign rd_addr  = instr[11:7];

    // ---------- Control ----------
    logic reg_write, mem_read, mem_write, mem_to_reg, alu_src, branch, jump, lui;
    logic [3:0] alu_op;
    control_unit u_ctrl (
        .opcode(opcode), .funct3(funct3), .funct7_5(funct7_5),
        .reg_write(reg_write), .mem_read(mem_read), .mem_write(mem_write),
        .mem_to_reg(mem_to_reg), .alu_src(alu_src), .branch(branch),
        .jump(jump), .lui(lui), .alu_op(alu_op)
    );

    // ---------- Immediate ----------
    logic [31:0] imm;
    imm_gen u_imm (.instr(instr), .imm(imm));

    // ---------- Register file ----------
    logic [31:0] rs1_data, rs2_data, rd_data;
    regfile u_rf (
        .clk(clk), .rd_we(reg_write), .rd_addr(rd_addr), .rd_data(rd_data),
        .rs1_addr(rs1_addr), .rs2_addr(rs2_addr),
        .rs1_data(rs1_data), .rs2_data(rs2_data)
    );

    // ---------- ALU ----------
    logic [31:0] alu_a, alu_b, alu_result; logic alu_zero;
    assign alu_a = lui ? 32'd0 : rs1_data;          // LUI 的 a 端清零：0 + imm
    assign alu_b = alu_src ? imm : rs2_data;        // alu_src=1 用立即數
    alu u_alu (.a(alu_a), .b(alu_b), .alu_op(alu_op),
               .result(alu_result), .zero(alu_zero));

    // ---------- Branch 判斷 ----------
    logic take_branch;
    branch_unit u_br (.rs1(rs1_data), .rs2(rs2_data),
                      .funct3(funct3), .take(take_branch));

    // ---------- Data memory ----------
    logic [31:0] mem_rdata;
    dmem #(.WORDS(DMEM_WORDS)) u_dmem (
        .clk(clk), .addr(alu_result), .funct3(funct3),
        .mem_read(mem_read), .mem_write(mem_write),
        .wdata(rs2_data), .rdata(mem_rdata)
    );

    // ---------- Writeback mux ----------
    // jump 寫回 pc+4（return address）；load 寫回 mem；其餘寫回 alu_result
    logic [31:0] pc_plus4;
    assign pc_plus4 = pc + 32'd4;
    always_comb begin
        if (jump)            rd_data = pc_plus4;
        else if (mem_to_reg) rd_data = mem_rdata;
        else                 rd_data = alu_result;
    end

    // ---------- Next-PC mux ----------
    // branch 成立 → pc + imm；JAL → pc + imm；JALR → (rs1 + imm) & ~1；否則 pc+4
    logic [6:0] op_jalr; assign op_jalr = 7'b1100111;
    always_comb begin
        if (branch && take_branch)      pc_next = pc + imm;
        else if (jump && opcode == op_jalr) pc_next = (rs1_data + imm) & ~32'd1;
        else if (jump)                  pc_next = pc + imm;       // JAL
        else                            pc_next = pc_plus4;
    end
endmodule
```

幾個接線重點：

- **decode 欄位** 直接從 instr 切：`funct7_5 = instr[30]`（只要這一 bit）、rs1/rs2/rd addr 是固定位置。這些位置在所有指令格式一致（RISC-V 刻意設計），所以無腦切即可，用不到的欄位（如 store 沒有 rd）control unit 會關掉 reg_write，切出來的垃圾 rd_addr 不會造成寫入。
- **dmem 的 wdata 接 rs2_data**：store 要寫的值來自 rs2（`sw x5, 0(x4)` 寫的是 x5）。位址是 alu_result（`x4 + 0`）。
- **imem 和 dmem 分開**（Harvard 架構）：單週期要同 cycle 既抓指令又存取資料，兩塊記憶體分開才不撞。imem 在 core 內直接放（fetch 邏輯簡單），dmem 是獨立模組（load/store 邏輯複雜）。
- **pc/instr 對外露出**：純為了觀察方便。真晶片不會把這些拉出來，但模擬時 testbench 能盯著它們看 CPU 在跑哪條。

## 範例 1：組一支費氏數列程式

不寫偽代碼、不手打 magic number——寫真的 RV32I 組語 `prog.S`：

```asm
    .section .text
    .globl _start
_start:
    # 算費氏數列 fib(0..9)，把 fib(9) 留在 x5，並把每步存進記憶體
    addi x1, x0, 0        # x1 = fib(n-2) = 0
    addi x2, x0, 1        # x2 = fib(n-1) = 1
    addi x3, x0, 9        # x3 = 迴圈次數 (要算到 fib(9))
    addi x4, x0, 0        # x4 = 記憶體寫入位址 offset
loop:
    add  x5, x1, x2       # x5 = fib(n) = fib(n-2)+fib(n-1)
    sw   x5, 0(x4)        # mem[x4] = x5
    addi x4, x4, 4        # 位址 +4
    add  x1, x0, x2       # x1 = 舊 x2
    add  x2, x0, x5       # x2 = 新 fib
    addi x3, x3, -1       # 次數 -1
    bne  x3, x0, loop     # 還沒算完就跳回
done:
    jal  x0, done         # 原地自旋，方便 tb 收尾
```

這支程式故意用滿本 Part 學的東西：`addi`（I-type 立即數）、`add`（R-type）、`sw`（store）、`bne`（分支迴圈）、`jal`（跳轉）。手算預期：迴圈跑 9 次，每次算一個新 fib 並存進記憶體。序列從 `0+1=1` 開始：`1, 2, 3, 5, 8, 13, 21, 34, 55`（存進 mem[0..8]）。最終 x5=55、x2=55、x1=34、x3=0、x4=36（9×4）。

組譯並轉 hex（Ch 7 的流程）：

```bash
riscv64-unknown-elf-gcc -march=rv32i -mabi=ilp32 -nostdlib \
    -Ttext=0x80000000 -o prog.elf prog.S
riscv64-unknown-elf-objcopy -O binary prog.elf prog.bin
python3 -c '
import struct
d = open("prog.bin","rb").read()
w = [struct.unpack("<I", d[i:i+4])[0] for i in range(0, len(d), 4)]
open("prog.hex","w").write("\n".join("%08x" % x for x in w) + "\n")
'
```

反組譯確認組出來的是我們要的（也順便驗 Ch 10/11 的 decode 該怎麼解）：

```
$ riscv64-unknown-elf-objdump -d prog.elf | grep -A20 _start
80000000 <_start>:
80000000:	00000093          	li	ra,0
80000004:	00100113          	li	sp,1
80000008:	00900193          	li	gp,9
8000000c:	00000213          	li	tp,0

80000010 <loop>:
80000010:	002082b3          	add	t0,ra,sp
80000014:	00522023          	sw	t0,0(tp)
80000018:	00420213          	addi	tp,tp,4
8000001c:	002000b3          	add	ra,zero,sp
80000020:	00500133          	add	sp,zero,t0
80000024:	fff18193          	addi	gp,gp,-1
80000028:	fe0194e3          	bnez	gp,80000010 <loop>

8000002c <done>:
8000002c:	0000006f          	j	8000002c <done>
```

`bnez gp,80000010` 就是我們的 `bne x3,x0,loop`——分支往回跳到 `0x80000010`，偏移是負的（`0x80000010 - 0x80000028 = -0x18`），imm_gen 的 B-type 符號延伸要對才跳得回去。`j 8000002c` 是 `jal x0,done` 原地自旋。

## 範例 2：core testbench，dump 暫存器與記憶體

我們要看 CPU 跑完後暫存器和記憶體的值。verilator 讓我們用 `verilator public` pragma 把內部陣列露出來給 C++ 讀。先在 regfile 和 dmem 的陣列宣告加 pragma：

```systemverilog
// regfile.sv 內
logic [31:0] regs [1:31] /*verilator public*/;

// dmem.sv 內
logic [31:0] mem [0:WORDS-1] /*verilator public*/;
```

加了 pragma，verilator 就不會把這兩個子模組 inline 掉，會生出 `Vcore_regfile.h`、`Vcore_dmem.h`，我們能透過階層存取。`core_tb.cpp`：

```cpp
#include "Vcore.h"
#include "Vcore_core.h"       // 存取內部階層（core 子模組指標）
#include "Vcore_regfile.h"    // regfile 內的 regs 陣列
#include "Vcore_dmem.h"       // dmem 內的 mem 陣列
#include "verilated.h"
#include <cstdint>
#include <cstdio>
static Vcore *dut;
static void tick() { dut->clk = 0; dut->eval(); dut->clk = 1; dut->eval(); }

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    dut = new Vcore;

    // 同步 reset
    dut->rst = 1; tick();
    dut->rst = 0; dut->eval();

    // 跑足夠多 cycle 讓迴圈跑完（9 次迴圈 * 每次 7 條 + 前置 ≈ 70）
    for (int i = 0; i < 80; i++) {
        tick();
        dut->eval();
    }

    // dump 暫存器 x1..x6。SV 的 regs[1:31] 對到 C 陣列 regs[0..30]，
    // 所以 x_r 存在 regs[r-1]（x0 不存實體）。
    printf("=== 暫存器 ===\n");
    for (int r = 1; r <= 6; r++)
        printf("x%-2d = %d (0x%08x)\n", r,
               dut->core->u_rf->regs[r - 1], dut->core->u_rf->regs[r - 1]);

    // dump 記憶體前 9 個 word（費氏數列存這裡）
    printf("=== 記憶體 (dmem word 0..8) ===\n");
    for (int m = 0; m < 9; m++)
        printf("mem[%d] = %d\n", m, dut->core->u_dmem->mem[m]);

    delete dut;
    return 0;
}
```

一個容易錯的細節（在 tb 註解裡也點了）：SystemVerilog 宣告 `regs[1:31]`（31 個元素，index 1~31）對到 verilator 生成的 C 陣列 `regs[31]`（index 0~30）。所以 **C 的 `regs[r-1]` 才是 x_r**。存取 x1 要用 `regs[0]`，不是 `regs[1]`。搞錯會整排偏移一格。

編譯執行（把所有 .sv 一起餵給 verilator，`--top-module core`）：

```bash
python3 -c 'open("data.hex","w").write(("00000000\n")*256)'   # dmem 初值全 0
verilator --cc core.sv control_unit.sv imm_gen.sv regfile.sv alu.sv \
    dmem.sv branch_unit.sv --top-module core --exe core_tb.cpp --Mdir obj_dir
make -C obj_dir -f Vcore.mk Vcore
./obj_dir/Vcore
```

真實輸出：

```
=== 暫存器 ===
x1  = 34 (0x00000022)
x2  = 55 (0x00000037)
x3  = 0 (0x00000000)
x4  = 36 (0x00000024)
x5  = 55 (0x00000037)
x6  = 0 (0x00000000)
=== 記憶體 (dmem word 0..8) ===
mem[0] = 1
mem[1] = 2
mem[2] = 3
mem[3] = 5
mem[4] = 8
mem[5] = 13
mem[6] = 21
mem[7] = 34
mem[8] = 55
```

**這就是一顆單週期 CPU 跑完一支真程式的結果。** 逐項對照手算預期：

- **mem[0..8] = 1,2,3,5,8,13,21,34,55**：費氏數列一字不差。`sw` 每次把新算的 fib 存進去，位址 `x4` 每次 +4，9 個值剛好落在 word 0~8。store 的位址計算（`x4+0`）、資料來源（rs2=x5）、記憶體寫入全對。
- **x5 = 55**：最後一次迴圈算的 fib(9)（這裡的編號從 fib 序列第 2 項起算），是最終結果。ALU 的 add、寫回暫存器全對。
- **x2 = 55、x1 = 34**：迴圈尾把 x2 更新成新 fib、x1 更新成舊 x2，滾動正確。
- **x3 = 0**：迴圈計數器從 9 減到 0，`bne x3,x0` 在 x3=0 時不再跳，迴圈正確結束。分支的比較（BNE）、負偏移的 target 計算全對。
- **x4 = 36 = 9×4**：位址累加 9 次，每次 +4。

整顆 core 通了。fetch 抓對指令、decode 產對訊號、regfile 讀寫對、ALU 算對、dmem 存對、branch 跳對——**所有零件接起來協同工作**。

## 範例 3：驗證 load 寫回、有號/無號延伸、JAL/JALR

Fibonacci 沒用到 load 寫回、sign extend、JALR。補一支 `t2.S` 專測這些：

```asm
    .section .text
    .globl _start
_start:
    addi x1, x0, 0x55
    sw   x1, 0(x0)       # mem[0]=0x55
    lw   x2, 0(x0)       # x2 = 0x55  (load 寫回)
    lbu  x3, 0(x0)       # x3 = 0x55
    addi x6, x0, -1
    sb   x6, 4(x0)       # mem[4] byte0 = 0xff
    lb   x4, 4(x0)       # x4 = 0xffffffff (sign ext)
    lbu  x5, 4(x0)       # x5 = 0x000000ff (zero ext)
    jal  x7, sub1        # x7 = 返回位址
    addi x8, x0, 99      # jal 返回後執行這條
    jal  x0, done
sub1:
    addi x9, x0, 7
    jalr x0, 0(x7)       # 回到 jal 的下一條 (addi x8)
done:
    jal  x0, done
```

組譯轉 hex（同流程，蓋掉 prog.hex）後跑。tb 只改成 dump x1~x9：

```cpp
    const char *names[] = {"", "x1", "x2(lw)", "x3(lbu)", "x4(lb)",
                           "x5(lbu)", "x6", "x7(ret)", "x8", "x9"};
    for (int r = 1; r <= 9; r++)
        printf("%-8s = 0x%08x\n", names[r], dut->core->u_rf->regs[r - 1]);
```

真實輸出：

```
x1       = 0x00000055
x2(lw)   = 0x00000055
x3(lbu)  = 0x00000055
x4(lb)   = 0xffffffff
x5(lbu)  = 0x000000ff
x6       = 0xffffffff
x7(ret)  = 0x80000024
x8       = 0x00000063
x9       = 0x00000007
```

逐項核對，這幾條線在 Fibonacci 沒被考到，這裡補齊：

- **x2 = 0x55（lw 寫回）**：sw 存 0x55、lw 讀回寫進 x2。**mem_to_reg** 這條 writeback 線通了（值來自記憶體而非 ALU）。
- **x4 = 0xffffffff（lb sign ext）vs x5 = 0x000000ff（lbu zero ext）**：同一 byte 0xff，LB 有號延伸成 -1、LBU 零延伸成 255。dmem 的 sign/zero extend 在 core 裡也對。
- **x7 = 0x80000024（jal 返回位址）**：`jal x7,sub1` 把 pc+4（jal 在 0x80000020，+4=0x80000024）寫進 x7。**jump 的 writeback 走 pc+4** 這條線對了。
- **x8 = 0x63 = 99**：jalr 從 sub1 跳回 0x80000024（x7 指的位址），執行了 `addi x8,x0,99`。**JAL 呼叫、JALR 返回** 的完整往返成立——若 JALR 的 target 算錯（沒清最低位、或沒用 rs1+imm），x8 會是 0（沒執行到）。
- **x9 = 7**：跳進 sub1 執行了 `addi x9,x0,7`，證明 JAL 確實跳到了子程式。

JAL/JALR 的呼叫返回是 CPU 能跑函式的基礎，這裡實測往返正確。加上 Fibonacci 驗過的算術/store/branch，本 Part 的 RV32I 指令主線全部在真硬體上跑通。

## 對比取捨

| 設計選擇 | 本課做法 | 替代方案 | 理由 |
|---|---|---|---|
| imem 放哪 | core 內直接放 | 獨立模組 | fetch 邏輯簡單，內嵌省事；dmem 複雜才拆模組 |
| 記憶體架構 | Harvard（imem/dmem 分開） | von Neumann（統一） | 單週期要同 cycle 抓指令 + 存資料，分開才不撞 |
| 內部觀察 | `verilator public` pragma 露陣列 | 拉一堆 debug 輸出埠 | pragma 不污染 RTL 介面，只在模擬時可見 |
| writeback 優先序 | jump > mem_to_reg > alu | 任意序 | jump 優先最清楚（JALR 同時 alu_src 但寫 pc+4） |
| LUI 實作 | `lui` 訊號清 ALU a 端 | 給 ALU 加 op | 不動凍結的 ALU；datapath 一根 mux 解決 |
| PC mux 判斷 | branch&take 分開判 | branch 訊號直接當 take | branch 要「是分支」且「條件成立」兩者，分開才對 |

## 踩雷區

**雷 1：writeback mux 漏了 jump 那條，JAL/JALR 寫錯值。**
- 錯誤直覺：「寫回不是 ALU 結果就是記憶體資料，兩選一」。
- 正確認識：JAL/JALR 要把 **pc+4**（返回位址）寫進 rd，是第三個來源。少了這條，`jal x7,sub1` 會把 ALU 結果（JALR 是 target、JAL 是垃圾）寫進 x7，返回時 `jalr x0,0(x7)` 跳到錯的地方，函式回不來。writeback 是**三選一**，jump 那條不能漏。

**雷 2：branch 訊號直接當「要跳」，沒 and 上 take。**
- 錯誤直覺：「control unit 說 branch=1 就跳」。
- 正確認識：`branch=1` 只代表「這是一條分支指令」，跳不跳還要看 **branch_unit 算出的 take**（比較成立嗎）。next-PC mux 必須 `branch && take_branch` 才換 target。只看 branch 會讓所有分支都跳（等於無條件跳），迴圈永遠出不來或跑錯。

**雷 3：verilator 存取 regs 陣列時 index 差一。**
- 錯誤直覺：「SV 宣告 regs[1:31]，那 C 裡 regs[1] 就是 x1」。
- 正確認識：verilator 把 `regs[1:31]`（31 元素）生成成 C 陣列 `regs[31]`（index 0~30）。所以 **C 的 regs[0] 是 x1、regs[r-1] 是 x_r**。直接用 regs[r] 會整排偏一格，dump 出來全錯還以為 CPU 壞了。這是純模擬觀察的陷阱，不影響硬體本身，但 debug 時會害你找錯方向。

**雷 4：跑的 cycle 數不夠，dump 到迴圈中間的值。**
- 錯誤直覺：「跑個幾十 cycle 應該夠了」。
- 正確認識：單週期一 cycle 執行一條指令。Fibonacci 迴圈 9 次、每次 7 條指令加前置，約需 70 cycle 才跑完。tb 若只跑 20 cycle，dump 到的是迴圈跑一半的中間值（x5 可能才 3、mem 只填幾格）。要嘛跑「夠多」cycle（本例 80），要嘛偵測 `jal done` 的自旋（PC 不再變）當結束條件。dump 前先確認程式真的跑完了。

## 進階延伸

- **這顆 CPU 的關鍵路徑**：單週期的 clock 週期必須 ≥ 最慢那條指令的組合延遲。最慢的通常是 load：PC → imem → decode → regfile 讀 → ALU 算位址 → dmem 讀 → writeback mux → regfile 寫，一整串串在一個 cycle 裡。這條路徑決定了時脈上限，是單週期效能差的根源——所有指令都被最慢的那條拖累。Part 3（Ch 24）會量它、Part 2 的 pipeline 就是為了打破它。
- **為什麼單週期真晶片幾乎不用**：它簡單、好懂、好驗證（就像我們這章），但效能糟——時脈被最慢指令綁死、每個 cycle 只做一條、硬體利用率低（大部分模組大部分時間閒著）。它的價值在**教學**和**當 pipeline/多週期的正確性參考模型**。你之後做 pipeline，會拿它的 dump 當黃金標準對照。
- **加一條新指令要動哪裡**：這是驗收你懂不懂整合的好問題。假設要加 `auipc`（U-type，`rd = pc + imm`）：imm_gen 已支援 U-type（改個 opcode 判斷）；control unit 要為 auipc 的 opcode 產訊號（reg_write=1、要把 ALU 的 a 接 PC）；datapath 要加一個 mux 讓 ALU 的 a 端能選 PC（像 lui 選 0 一樣）。走一遍「decode→control→datapath mux→writeback」就知道每條新指令的成本落在哪。
- **形式化驗證與 riscv-tests**：我們用手算預期值對照，這對教學夠，但真 core 驗證會用 **riscv-tests**（官方的每指令自檢測試組，寫 tohost 位址表示 pass/fail）跑幾百個 case，或用 **riscv-formal** 做形式化驗證（數學證明 datapath 對所有輸入都符合 ISA）。練習 A 會帶你用一組涵蓋各指令的自檢測試打穿這顆 core，是往「真驗證」靠近的第一步。

## 本章重點整理

- 單週期 core 是把 Ch 7~11 所有零件接成一個 top module，難點在**接線和三個 mux**，不在單一模組。
- **三個關鍵 mux**：ALU 運算元選擇（`lui`/`alu_src`）、writeback 三選一（jump→pc+4 / load→mem / else→alu）、next-PC 四選一（branch&take→pc+imm / JALR→(rs1+imm)&~1 / JAL→pc+imm / else→pc+4）。
- **Harvard 架構**：imem/dmem 分開，單週期同 cycle 抓指令又存資料才不撞。
- 用**真程式**（費氏數列迴圈）驗整體：mem[0..8]=1,2,3,5,8,13,21,34,55、x5=55、x3=0，逐項對上手算。第二支程式補驗 load 寫回、sign/zero extend、JAL/JALR 呼叫返回。
- 觀察內部用 `verilator public` pragma，注意 `regs[1:31]` 對到 C 的 `regs[0..30]`（差一）。
- 這顆單週期 CPU 簡單好驗但效能差（關鍵路徑被最慢指令綁死），是 pipeline 的正確性參考模型。

## 自我檢核

- [ ] 我能不看講義畫出完整單週期 datapath 的五個階段和三個關鍵 mux，並說出每個 mux 的選擇訊號。
- [ ] 我能解釋 writeback 為什麼是三選一（而非兩選一），jump 那條寫的是什麼、漏了會怎樣。
- [ ] 我能說明 next-PC mux 為什麼 branch 要 `branch && take` 兩個條件，只看 branch 會怎樣。
- [ ] 我能複述從 `.S` 到 core 跑出結果的完整流程，並解釋為什麼要用 `verilator public` pragma。
- [ ] 我能手算費氏數列程式跑完後 x1~x5 和 mem[0..8] 的值，並對照實際輸出。
- [ ] 我能說出要加一條 `auipc` 指令需要動 imm_gen、control unit、datapath 的哪些地方。

## 延伸閱讀

- **《Computer Organization and Design, RISC-V Edition》(Patterson & Hennessy) 第 4.4 節完整版「A Simple Implementation Scheme」**：這節從零把整個單週期 datapath 拼完，含所有 mux 和 control。讀完對照本章的 `core.sv`，你會發現 RTL 就是那張圖的文字版。特別看它的 Figure 4.17（完整 datapath）和 control 真值表。
- **《Digital Design and Computer Architecture, RISC-V Edition》(Harris & Harris) 第 7.3 節「Single-Cycle Processor」全節與 7.6 節效能分析**：Harris 的單週期章節從 datapath 到 control 到效能一氣呵成，7.6 節算單週期的時脈受限於哪條路徑——正是本章進階延伸提的關鍵路徑，量化版。
- **[riscv-tests repo](https://github.com/riscv-software-src/riscv-tests) 的 `isa/rv32ui/` 目錄**：官方的 RV32I user-level 指令測試組，每個 `.S` 檔測一條指令的各種 case，用 `TEST_CASE` 巨集寫預期值、失敗跳 fail。練習 A 會用它們（或其精神）打穿你的 core。先翻幾個檔（如 `add.S`、`lw.S`、`beq.S`）看它們怎麼構造自檢測試。
- **[picorv32 原始碼](https://github.com/YosysHQ/picorv32) 的頂層 `picorv32` module**：看一個真正被人用在 FPGA 上的 RV32I core 頂層長怎樣——它不是單週期（是多週期狀態機以省面積），但你能對照「教學單週期把一切攤在一個 cycle」和「真 core 用狀態機分多 cycle、共用硬體」的根本差異。它的 memory interface 握手、指令解碼的組織方式尤其值得看。

Part 1 到此，你有了一顆能跑真程式的單週期 RV32I CPU——從一根 clock 線到費氏數列，每一塊都是自己接的、每個結果都對照過。接下來練習 A 會用一組涵蓋各指令的自檢測試，系統性地把這顆 core「打穿」，確認它不只跑對一支程式，而是對每類指令都正確。這是從「跑得動」到「真的對」的關鍵一步。

→ [練習 A 用 RV32I 指令測試打穿單週期 core](./practice-a-single-cycle-rv32ui.md)
