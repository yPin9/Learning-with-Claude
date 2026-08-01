# Ch 35 — 讓 core 跑得動 trap handler：CSR / trap / 中斷全整合

> **目標**：把 Ch 31~34 做的零件——CSR file、trap 進出、privilege check、CLINT timer 中斷——**全部接進一顆能取指、執行的 core**，然後親手寫一支**真正可跑的 trap handler**（`.S`：進 handler → 處理 → `mret`），讓 core 端到端跑得動它。你會學：一顆帶 trap 的 core 內部 CSR / trap 決策 / next-PC 怎麼串成一條資料流、一支 timer interrupt handler 的骨架（重排鬧鐘、`mret`）、以及**用 mscratch 換 stack、存/還原 context** 這個所有真 handler 都在做的動作。示範一個**完整最小系統**：`core` + 內建 CLINT，跑一支主程式 + timer handler，真跑看主程式做事做到一半被 timer 打斷、handler 進來、`mret` 後主程式從斷點繼續——週期性重複。這是深挖章，也是整個 Part 5（trap/中斷/特權）的收尾與驗收。
> **環境**：WSL + verilator 4.038 + riscv64-unknown-elf-gcc 10.2.0。core 真跑、handler 用真 assembler 組譯，每一拍的 PC / mtime / mcause / priv / 暫存器都貼真實輸出。
> 這章對接 `architecture/riscv` 課的 privileged ISA：那門課從軟體視角講「handler 該怎麼寫、SBI/OS 怎麼用這些 CSR」；這章我們把它跑的那顆硬體做出來、讓它真的動。兩邊對照，軟硬體介面就完整了。

## 為什麼需要「整合」這一步？

Ch 31~34 我們一塊一塊把零件做出來：Ch 31 的 `csr_file` 會讀寫 CSR、Ch 32 的 trap 邏輯會存 context 跳 mtvec、Ch 33 的 privilege check 會擋越權、Ch 34 的 CLINT 會產 timer 中斷。但這些**到目前為止都是分開驗的**——csr_file 用獨立 testbench 測、trap 用單一 ECALL 程式測、timer 用一根 `timer_irq` 線餵。

真實的 CPU 不是這樣用的。真實情況是：**一顆 core 一邊取指執行主程式、一邊隨時可能被中斷或 exception 打斷、跳進 handler、handler 動了一堆 CSR、`mret` 回來繼續**——這一切要在**同一顆硬體、同一條資料流**裡無縫發生。零件各自對，接起來不一定對：

- trap 進入寫 mepc/mcause、和一般 CSR 指令寫 CSR，**同一拍可能都想寫 mstatus**——誰優先？
- 主程式正在 `csrw mtvec` 設 handler 入口，**還沒設完就來中斷**——跳到哪？
- handler 要用暫存器工作，但那些暫存器**裝著主程式的活資料**——怎麼借用又不弄壞？
- timer 每次響完要**重排下一次鬧鐘**，不然 mret 出去 MTIP 還在、立刻又被打斷——handler 怎麼清中斷？

這些「接縫」問題，只有把零件真的接成一顆 core、跑一支真 handler 才會浮現、才能驗。**整合不是把檔案拼在一起，是讓四個機制在同一條時間軸上正確協作。** 這章我們把 core 立起來，跑一個「主程式被 timer 週期性打斷」的完整最小系統——這正是作業系統 time-slice 的硬體骨架。

## 先建立直覺：一個人邊工作邊接電話

把整合後的 core 想成一個在辦公桌前工作的人，這個人要同時處理三件事：

```
   ┌─────────────────────────────────────────────────────────┐
   │  正在做的工作（主程式）：一條一條指令往下算              │
   │        │                                                 │
   │        │  ← 電話隨時可能響（timer 中斷 / exception）      │
   │        ▼                                                 │
   │  每做完一件事，抬頭看一眼：有電話嗎？該接嗎？            │
   │   （每個指令邊界檢查 trap：MIE & MTIE & MTIP？illegal？）│
   │        │ 沒有 → 繼續下一件工作                            │
   │        │ 有   → 1. 書籤夾住現在做到哪（mepc）             │
   │        │        2. 記下誰打來（mcause）                   │
   │        │        3. 掛勿擾牌（MIE←0）                      │
   │        │        4. 走到電話機（PC←mtvec）                 │
   │        ▼                                                 │
   │  接電話（handler）：先把桌上的東西挪開（存 context）、    │
   │   處理事情、重設鬧鐘、把東西擺回去（還原 context）        │
   │        │                                                 │
   │        ▼  講完（mret）：拿掉勿擾牌、翻回書籤、接著工作    │
   └─────────────────────────────────────────────────────────┘
```

整合的關鍵，就是把這一整套「工作 → 檢查 → 接電話 → 回來」做進**同一顆會取指的硬體**，而且每個環節的時序都對：

- **「每做完一件事抬頭看一眼」** = core 每一拍在算 next-PC 時，先問「這拍要不要進 trap」，要就跳 mtvec、不要才走正常流程。這是 trap 和一般控制流（branch/jump）在同一個 next-PC 多工器裡競爭，trap 優先。
- **「先把桌上東西挪開」** = handler 開頭用 mscratch 換一塊自己的 stack、把要用到的暫存器存進去；結束前還原。這是所有真 handler 的頭尾骨架，也是這章的重點動手處。
- **「重設鬧鐘」** = timer handler 必須把 mtimecmp 往後推，否則 MTIP 一直是高、mret 出去馬上又被打斷（Ch 34 雷 3 的中斷風暴）。

下面我們把這顆 core 的內部資料流攤開，再跑真 handler。

## 核心概念：一顆帶 trap 的 core 內部長什麼樣

整合後的 core（我們叫它 `core_trap`，Ch 32~35 共用的教學單週期核）內部，把四個機制串成一條資料流。用一張圖看它一拍內發生什麼：

```
   一拍（clk 上升沿前是組合、上升沿落值）：

   PC ──▶ imem[pc] ──▶ inst
                        │
        ┌───────────────┼────────────────────────────┐
        ▼               ▼                             ▼
     decode          CSR 存取                    trap 偵測
   (opcode/rd/       csr_addr=inst[31:20]      is_ecall / is_ebreak
    rs1/rs2/imm)     csr_rdata（組合讀舊值）    is_mret / illegal
        │            csr_new（RW/RS/RC 算新值）  irq(MIE&MTIE&MTIP)
        ▼               │                          │
   ALU / branch         │                    trap_taken?
   / load-store         │                     ┌────┴────┐
        │               │                     │  是      │ 否
        ▼               ▼                     ▼          ▼
   ┌────────────────────────────────────────────────────────┐
   │ next-PC 多工器（優先序）：                              │
   │   trap_taken → mtvec   ＞ mret → mepc                   │
   │   ＞ jal/jalr/branch ＞ pc+4                            │
   ├────────────────────────────────────────────────────────┤
   │ clk 邊沿寫回（優先序）：                                 │
   │   trap → 存 mepc/mcause/mtval、翻 mstatus、priv←M       │
   │   ＞ mret → 還原 mstatus/priv                           │
   │   ＞ csr_write_en → 寫該 CSR                            │
   │   （regfile / dmem 在 trap 時壓制不寫）                 │
   └────────────────────────────────────────────────────────┘
```

三條關鍵接縫，都靠**優先序**解決（這正是 Ch 31 立下的「trap > mret > CSR 指令」原則，現在落進真 core）：

1. **next-PC 的優先序**：`trap_taken` 最高。哪怕這條指令是個 branch、是個 CSR 指令，只要它同時觸發了 trap（例如它是非法指令、或這拍有中斷 pending），next-PC 就走 mtvec，不理它「本來」要跳哪。中斷/exception 是「非預期的控制轉移」，壓過一切正常控制流。

2. **寫回的優先序**：trap 進入的 CSR 寫（存 mepc/mcause、翻 mstatus）優先於這條指令自己的 CSR 寫。因為觸發 trap 的那條指令**不該完成它自己的效果**——它被 trap 打斷了。

3. **commit 壓制**：trap 那拍，這條指令對 regfile 和 dmem 的寫**全部壓掉**（`reg_we=0`、store 不發生）。這是 precise exception 的最小落實：出事指令不留下任何架構副作用，好讓 mepc 精確、handler 修好後回來重跑或跳過。

我們的 core 這幾段的實際 SV（摘自 `core_trap.sv`，命名沿用全課約定）：

```systemverilog
// ---- trap 偵測 ----
logic is_ecall, is_ebreak, is_mret, illegal_inst, interrupt_taken;
assign interrupt_taken = mstatus[3] && mie[7] && (mtime >= mtimecmp); // MIE&MTIE&MTIP
logic trap_taken;
assign trap_taken = interrupt_taken || illegal_inst || is_ecall || is_ebreak;

// ---- next-PC 多工器：trap 最高優先 ----
always_comb begin
    if (trap_taken)              pc_next = {mtvec[31:2], 2'b00}; // 跳 handler（direct）
    else if (is_mret)            pc_next = mepc;                 // mret 返回
    else if (opcode == OP_JAL)   pc_next = jal_target;
    else if (opcode == OP_JALR)  pc_next = jalr_target;
    else if (take_branch)        pc_next = br_target;
    else                         pc_next = pc + 4;
end

// ---- commit 壓制：trap 時這條指令不寫 regfile ----
always_comb begin
    /* ...依 opcode 算 reg_we / wb_data... */
    if (trap_taken) reg_we = 1'b0;   // 出事指令不 commit
end

// ---- clk 邊沿：trap > mret > CSR 指令 的寫回優先鏈 ----
always_ff @(posedge clk) begin
    /* ...regfile / store 寫回（trap 時已被壓制）... */
    if (trap_taken) begin
        mepc   <= trap_epc & 32'hFFFF_FFFC;
        mcause <= trap_cause;
        mtval  <= trap_tval;
        mstatus[7]     <= mstatus[3];   // MPIE ← MIE
        mstatus[3]     <= 1'b0;          // MIE  ← 0（掛勿擾牌）
        mstatus[12:11] <= priv;          // MPP  ← 當前特權
        priv <= 2'b11;                   // 升到 M
    end else if (is_mret) begin
        mstatus[3]     <= mstatus[7];   // MIE  ← MPIE（還原）
        mstatus[7]     <= 1'b1;
        priv <= mstatus[12:11];          // 還原特權
        mstatus[12:11] <= 2'b00;
    end else if (csr_write_en) begin
        /* ...一般 Zicsr 寫該 CSR... */
    end
end
```

這就是 Ch 31~34 那些機制**接在同一顆會取指、會執行的 core 裡**的樣子。零件沒變，變的是它們現在共享一個 PC、一條時間軸，靠優先序協調。

## 核心概念：內建 CLINT——讓 timer 能被程式設定

Ch 34 我們把 CLINT 的 mtime/mtimecmp 邏輯放 testbench 端（core 只收一根 `timer_irq`）。這章要跑**真 handler**，handler 得能用指令**讀 mtime、寫 mtimecmp**（重排鬧鐘），所以我們把 CLINT 內建進 core：

```systemverilog
// ---- 內建 CLINT：mtime 每拍 +1、mtime>=mtimecmp → MTIP ----
logic [63:0] mtime, mtimecmp;
always_ff @(posedge clk) begin
    if (rst) begin
        mtime <= 64'd0;
        mtimecmp <= 64'hFFFF_FFFF_FFFF_FFFF;  // 永不到期
    end else begin
        mtime <= mtime + 1;
        mip[7] <= (mtime >= mtimecmp);        // MTIP 反映 timer pending
        /* ...其餘寫回... */
    end
end
```

真晶片的 CLINT 是 **memory-mapped**——handler 用 `lw`/`sw` 存取 `0x0200_0000` 一帶的 mtime/mtimecmp。我們的教學 core 為了不牽進整套 bus，用**自訂 CSR** 當存取口（這是教學取巧，真硬體是 memory-mapped）：

| 自訂 CSR | 位址 | 作用 |
|---|---|---|
| 讀 mtime 低 32 bit | `0x7C2` | handler `csrr t4, 0x7C2` 拿到當前 mtime，好算 `mtime + N` |
| 寫 mtimecmp 低 32 bit | `0x7C0` | handler `csrw 0x7C0, t4` 設下一次鬧鐘 |
| 寫 mtimecmp 高 32 bit | `0x7C1` | RV32 下 64-bit 值的高半（本課用 0）|

這樣 handler 就能做「讀 mtime → 加 N → 寫 mtimecmp」重排鬧鐘。位址 `0x7C0` 系列的 bit[9:8]=`11`（M-level）、bit[11:10]=`01`（可讀寫），所以它們是 M mode 專屬——這正符合「CLINT 是特權硬體，user 碰不到」。**記住這是教學橋接**：真程式對真晶片是 `sw` 寫 CLINT 的記憶體位址，語意一模一樣（讀 mtime、寫 mtimecmp），只是走 bus 不走 CSR。

## 底層機制：一次「主程式被打斷、handler 處理、返回」的完整生命

把整合後的完整流程走一遍（這是下面範例一會逐拍驗的）：

```
   1. 開機（priv=M）：主程式 setup
      csrw mtvec, handler      設 handler 入口
      csrw 0x7C0, 20           mtimecmp=20（第一次鬧鐘）
      csrw mie, 0x80           mie.MTIE=1（開 timer 源）
      csrw mstatus, 0x8        mstatus.MIE=1（開全域中斷）
      → 進主迴圈做事（x1 累加）

   2. mtime 一直漲... mtime 追上 mtimecmp(20) → MTIP=1
      core 每拍檢查 MIE(1)&MTIE(1)&MTIP(1)=1 → interrupt_taken

   3. 下一個指令邊界進 trap（一拍硬體全做）：
      mepc ← 被打斷指令的 PC（主迴圈某條，回來重跑）
      mcause ← 0x80000007（bit31=1 中斷、code=7 timer）
      MPIE←MIE、MIE←0（關中斷）、MPP←M、priv←M、PC←mtvec

   4. handler 跑：
      x2++（記錄進 handler 次數）
      csrr t4, 0x7C2           讀 mtime
      addi t4, t4, N           算下一次鬧鐘
      csrw 0x7C0, t4           mtimecmp = mtime+N（排下次 + 清 MTIP）
      mret

   5. mret：MIE←MPIE（重開中斷）、PC←mepc（跳回被打斷處）
      → 主程式從斷點繼續做事，x1 接著漲

   6. 直到 mtime 再追上新 mtimecmp → 回到步驟 2（週期性重複）
```

注意步驟 4 handler **沒有動 mepc**——interrupt 的 mepc 是「下一條還沒執行的指令」，回去要接著跑（Ch 34 雷 2）。這和 ECALL handler 要 `mepc += 4` 正好相反。下面兩個範例，一個跑 timer interrupt（不動 mepc），一個回顧 ECALL（動 mepc），對照著看。

## 範例一：完整最小系統——主程式被 timer 週期性打斷

寫一支主程式 + timer handler，讓 core 端到端跑。主程式設好一切後進迴圈累加 x1（模擬「做事」），timer 每隔一段打斷它一次：

```asm
    .section .text
    .globl _start
_start:
    la    t0, trap_handler
    csrw  mtvec, t0            # 設 trap 入口

    li    t1, 20
    csrw  0x7C0, t1            # mtimecmp[31:0] = 20（第一次鬧鐘，教學 CSR）
    csrw  0x7C1, x0           # mtimecmp[63:32] = 0

    li    t2, 0x80            # bit7 = MTIE
    csrw  mie, t2             # 開 timer 中斷源
    li    t3, 0x8            # bit3 = MIE
    csrw  mstatus, t3        # 開全域中斷

    li    x1, 0              # 主程式的工作進度計數器
main_loop:
    addi  x1, x1, 1          # 主程式一直做事（累加）
    beq   x0, x0, main_loop  # 等 timer 打斷

    .align 2
trap_handler:                # timer interrupt handler
    addi  x2, x2, 1          # x2 = 進 handler 次數
    csrr  t4, 0x7C2          # t4 = 當前 mtime（教學 CSR 讀）
    addi  t4, t4, 15         # 下一次鬧鐘 = mtime + 15
    csrw  0x7C0, t4          # mtimecmp = mtime + 15（排下次 + 清 MTIP）
    mret                     # 返回（interrupt 不動 mepc！）
```

組譯，看真實 encoding 和位址：

```bash
riscv64-unknown-elf-gcc -march=rv32i -mabi=ilp32 -nostdlib -Ttext=0x80000000 -o sys.elf sys_timer.S
riscv64-unknown-elf-objdump -d sys.elf
```

```
80000000 <_start>:
80000000:	00000297          	auipc	t0,0x0
80000004:	03428293          	addi	t0,t0,52 # 80000034 <trap_handler>
80000008:	30529073          	csrw	mtvec,t0
8000000c:	01400313          	li	t1,20
80000010:	7c031073          	csrw	0x7c0,t1
80000014:	7c101073          	csrw	0x7c1,zero
80000018:	08000393          	li	t2,128
8000001c:	30439073          	csrw	mie,t2
80000020:	00800e13          	li	t3,8
80000024:	300e1073          	csrw	mstatus,t3
80000028:	00000093          	li	ra,0
8000002c <main_loop>:
8000002c:	00108093          	addi	ra,ra,1
80000030:	fe000ee3          	beqz	zero,8000002c <main_loop>
80000034 <trap_handler>:
80000034:	00110113          	addi	sp,sp,1
80000038:	7c202ef3          	csrr	t4,0x7c2
8000003c:	00fe8e93          	addi	t4,t4,15
80000040:	7c0e9073          	csrw	0x7c0,t4
80000044:	30200073          	mret
```

`csrr t4, 0x7C2` 組成 `7c202ef3`（讀 mtime）、`mret` 是 `30200073`。主迴圈在 0x2c~0x30，handler 在 0x34~0x44。把 `.text` 抽 hex 餵進 core，用一個每拍印狀態的 testbench 跑：

```cpp
#include "Vcore_trap.h"
#include "verilated.h"
#include <cstdio>
static Vcore_trap* dut;
static void tick(){ dut->clk=0; dut->eval(); dut->clk=1; dut->eval(); }
static uint32_t rd(int i){ dut->dbg_reg_sel=i; dut->eval(); return dut->dbg_reg_data; }
int main(int argc,char**argv){
    Verilated::commandArgs(argc,argv);
    dut=new Vcore_trap;
    dut->rst=1; tick(); tick(); dut->rst=0;
    int N=(argc>1)?atoi(argv[1]):70;
    for(int c=0;c<N;c++){
        dut->eval();
        printf("cyc%-2d pc=%08x mtime=%2u mcause=%08x priv=%d x1=%-3d x2=%d\n",
            c, dut->dbg_pc, dut->dbg_mtime, dut->dbg_mcause, dut->dbg_priv,
            (int)rd(1), (int)rd(2));
        tick();
    }
    printf("FINAL x1(main work)=%d  x2(handler entries)=%d\n",(int)rd(1),(int)rd(2));
    delete dut; return 0;
}
```

```bash
riscv64-unknown-elf-objcopy -O binary --only-section=.text sys.elf sys.bin
od -An -tx4 -w4 -v sys.bin | sed 's/ //g' > prog_sys.hex
verilator --cc core_trap.sv --exe trace_tb.cpp --Mdir obj_sys \
    -Wno-WIDTH -Wno-UNUSED -Wno-UNOPTFLAT -GINIT_FILE='"prog_sys.hex"'
make -s -C obj_sys -f Vcore_trap.mk Vcore_trap
./obj_sys/Vcore_trap 60
```

真跑輸出（節錄看清前兩次中斷；`mcause` 欄顯示的是**上一次** trap 存進去的值）：

```
cyc0  pc=80000000 mtime= 0 mcause=00000000 priv=3 x1=0   x2=0
...（cyc1~10 做 setup：設 mtvec、mtimecmp=20、開 MTIE、開 MIE）...
cyc11 pc=8000002c mtime=11 mcause=00000000 priv=3 x1=0   x2=0
cyc12 pc=80000030 mtime=12 mcause=00000000 priv=3 x1=1   x2=0
cyc13 pc=8000002c mtime=13 mcause=00000000 priv=3 x1=1   x2=0
...（主迴圈空轉，x1 一路漲）...
cyc20 pc=80000030 mtime=20 mcause=00000000 priv=3 x1=5   x2=0
cyc21 pc=80000034 mtime=21 mcause=80000007 priv=3 x1=5   x2=0
cyc22 pc=80000038 mtime=22 mcause=80000007 priv=3 x1=5   x2=1
cyc23 pc=8000003c mtime=23 mcause=80000007 priv=3 x1=5   x2=1
cyc24 pc=80000040 mtime=24 mcause=80000007 priv=3 x1=5   x2=1
cyc25 pc=80000044 mtime=25 mcause=80000007 priv=3 x1=5   x2=1
cyc26 pc=80000030 mtime=26 mcause=80000007 priv=3 x1=5   x2=1
cyc27 pc=8000002c mtime=27 mcause=80000007 priv=3 x1=5   x2=1
cyc28 pc=80000030 mtime=28 mcause=80000007 priv=3 x1=6   x2=1
...（主迴圈續跑到 mtime 追上新 mtimecmp=37）...
cyc38 pc=80000034 mtime=38 mcause=80000007 priv=3 x1=10  x2=1
cyc39 pc=80000038 mtime=39 mcause=80000007 priv=3 x1=10  x2=2
...
cyc55 pc=80000034 mtime=55 mcause=80000007 priv=3 x1=16  x2=2
...
FINAL x1(main work)=16  x2(handler entries)=3
```

一拍一拍讀懂整個系統怎麼跑：

- **cyc0~10（setup）**：主程式設 mtvec=0x34、mtimecmp=20、開 MTIE、開 MIE。mtime 一路漲，還沒到 20，沒中斷。
- **cyc11~20（主迴圈做事）**：PC 在 0x2c↔0x30 之間跳（`addi x1,x1,1` + `beq` 回頭），**x1 一路累加**（cyc12 x1=1、cyc14 x1=2...cyc20 x1=5）。這是主程式「做事」，還沒被打斷。
- **cyc21（mtime=21，追上 mtimecmp=20，進 trap）**：**鬧鐘響了！** `MIE(1)&MTIE(1)&MTIP(1)=1` → interrupt_taken。這一拍 PC 跳到 **0x34（trap_handler）**，硬體存好 mepc（被打斷的主迴圈 PC）、mcause=**0x80000007**（bit31=1 中斷、code=7 timer）、MIE←0、priv 保持 M。
- **cyc21~25（handler 跑）**：`addi x2,x2,1`（x2 從 0→1，cyc22 顯示）、`csrr t4,0x7C2`（讀 mtime≈22）、`addi t4,t4,15`、`csrw 0x7C0,t4`（mtimecmp ← 22+15=**37**，排下次 + 清 MTIP）、`mret`。
- **cyc26（mret 返回）**：PC 跳回 **0x30**（mepc，被打斷的主迴圈點），MIE 重開。x1 還是 5（handler 期間主程式凍結），**但沒丟**——cyc28 起 x1 從 6 接著漲。**主程式從斷點無縫繼續。**
- **cyc26~37（主迴圈續跑）**：x1 從 6 漲到 10，等 mtime 追上新 mtimecmp（37）。
- **cyc38（mtime=38 追上 37）**：**第二次中斷！** 又進 handler，x2→2（cyc39），mtimecmp 重排成 39+15=54。
- **cyc55**：**第三次**（mtime 追上 54），x2→3。
- 最終 `x1=16, x2=3`：60 拍內主程式做了 16 單位的工作（x1）、被 timer 打斷 3 次（x2），**每次都精確回到斷點繼續**。

這就是一顆能處理中斷的 CPU 端到端跑起來的樣子——**主程式做事、timer 週期性打斷、handler 處理完精確返回、主程式續做**。把 handler 裡的「x2++」換成「切換到下一個行程」，這就是搶佔式多工（preemptive multitasking）的硬體骨架。Ch 31~34 的四個零件，在這一支程式裡全部同時運轉、正確協作。

## 範例二：真 handler 的頭尾——用 mscratch 存/還原 context

範例一的 handler 偷懶了：它直接用 x2、t4 工作，**假設弄壞這些暫存器沒關係**。真 handler 不能這樣——它被中斷打斷的是主程式，主程式的每個暫存器都可能裝著活資料，handler **借用任何暫存器前都得先存起來、用完還原**，否則 mret 回去主程式的資料就被 handler 蓋掉了。

問題是：handler 要「存暫存器到 stack」，但**連 stack pointer（sp）本身都是主程式的活資料**——handler 沒有自己乾淨的暫存器可用，怎麼開始？RISC-V 的標準解法是 **mscratch**：開機時先在 mscratch 放一塊 handler 專用的 stack top，handler 開頭用 `csrrw sp, mscratch, sp` **一次交換**——sp 拿到 handler stack、mscratch 存住主程式的 sp。這是所有真 handler 的第一個動作。

```asm
_start:
    la    t0, trap_handler
    csrw  mtvec, t0
    li    t1, 0x80001000
    csrw  mscratch, t1        # mscratch = handler 專用 stack top

    li    t1, 20
    csrw  0x7C0, t1           # 第一次鬧鐘
    csrw  0x7C1, x0
    li    t2, 0x80
    csrw  mie, t2
    li    t3, 0x8
    csrw  mstatus, t3

    li    x1, 0xAAAA          # 主程式的活資料（handler 絕不能弄壞）
    li    x5, 0x5555          # 另一個活資料
    li    x6, 0              # handler 進入次數歸零
main_loop:
    addi  x1, x1, 1
    beq   x0, x0, main_loop

    .align 2
trap_handler:
    # --- 存 context：用 mscratch 換到 handler stack，存要用到的暫存器 ---
    csrrw sp, mscratch, sp    # swap：sp ← handler stack，mscratch ← 主程式 sp
    addi  sp, sp, -8
    sw    t4, 0(sp)           # 存 t4（handler 待會要用）
    sw    t5, 4(sp)           # 存 t5
    # --- 處理 timer：重排鬧鐘 + 計數 ---
    csrr  t4, 0x7C2           # t4 = mtime
    addi  t4, t4, 40          # 週期 40（大於 handler 長度，避免立刻再觸發）
    csrw  0x7C0, t4           # mtimecmp = mtime + 40
    addi  x6, x6, 1           # x6 = handler 進入次數
    # --- 還原 context：把借走的暫存器擺回、換回主程式 sp ---
    lw    t4, 0(sp)
    lw    t5, 4(sp)
    addi  sp, sp, 8
    csrrw sp, mscratch, sp    # swap 回：sp ← 主程式 sp，mscratch ← handler stack
    mret
```

handler 的 encoding（objdump 節錄）：

```
8000004c <trap_handler>:
8000004c:	34011173          	csrrw	sp,mscratch,sp   # 換到 handler stack
80000050:	ff810113          	addi	sp,sp,-8
80000054:	01d12023          	sw	t4,0(sp)
80000058:	01e12223          	sw	t5,4(sp)
8000005c:	7c202ef3          	csrr	t4,0x7c2          # 讀 mtime
80000060:	028e8e93          	addi	t4,t4,40
80000064:	7c0e9073          	csrw	0x7c0,t4          # 重排鬧鐘
80000068:	00130313          	addi	t1,t1,1          # x6++（t1 是 x6 的 ABI 名）
8000006c:	00012e83          	lw	t4,0(sp)
80000070:	00412f03          	lw	t5,4(sp)
80000074:	00810113          	addi	sp,sp,8
80000078:	34011173          	csrrw	sp,mscratch,sp   # 換回主程式 sp
8000007c:	30200073          	mret
```

跑 130 拍，最後檢查主程式的活資料有沒有被 handler 弄壞、handler 進了幾次：

```bash
./obj_ctx/Vcore_trap 130
```

真跑輸出：

```
after 130 cycles:
x1 (main data, expect 0xAAAA+work) = 0xaace
x5 (main data, expect 0x5555)      = 0x5555
x6 (handler entries)               = 3
mcause = 0x80000007  priv=3
```

三個結果全對，逐個看：

- **x5 = 0x5555（原封不動）**：主程式在 x5 放的活資料，被 timer 中斷了 3 次、handler 進出 3 回，**x5 完全沒被弄壞**。因為 handler 只借用 t4/t5（且先存後還原）、只碰 sp（用 mscratch 換走再換回），**沒動 x5**。這證明 context 保存正確——handler 對主程式是「透明」的，打斷過但沒留下副作用（除了它該做的計數）。
- **x1 = 0xaace**：主程式的 x1 從 0xAAAA 開始一路 `addi x1,x1,1`，被打斷 3 次期間凍結、返回後接著漲，最後到 0xaace（= 0xAAAA + 0x24 = 主迴圈跑了 36 圈）。**斷點續跑，工作進度沒丟。**
- **x6 = 3**：130 拍內 timer 週期性打斷 3 次（週期 40：第一次鬧鐘 20、之後 mtime+40）。

**mscratch 換 stack + 存/還原 context，是每一支真 handler 的頭尾骨架**——xv6 的 `kernelvec.S`、Linux 的中斷入口，開頭都在做這件事（存的暫存器更多、更完整）。範例一的「直接用暫存器」只在「確定沒有活資料會被弄壞」時能用（例如 handler 用的暫存器主程式恰好沒用）；真系統一律走範例二這套。這也是為什麼 Ch 31 要做 mscratch——它就是為了這個「handler 沒有乾淨暫存器可用」的雞生蛋問題而存在的。

## 對比取捨：兩種 handler 寫法、以及整合的設計選擇

| 面向 | 範例一（直接用暫存器）| 範例二（mscratch 存 context）|
|---|---|---|
| 存/還原暫存器 | 不存（假設弄壞沒關係）| 存進 handler stack、還原 |
| 安全性 | 只在「不弄壞活資料」時對 | 對主程式透明，永遠安全 |
| 開銷 | 小（少幾條指令）| 大（swap + 多個 sw/lw）|
| 真實性 | 教學示意 | 真 OS 的做法（kernelvec.S）|
| 適用 | demo、極簡 bare-metal | 任何跑真程式的系統 |

整合這顆 core 時，還有幾個設計點值得對照真 pipeline core（Ch 20 那顆五級核）：

| 面向 | 本章 core_trap（單週期）| 真 pipeline core |
|---|---|---|
| trap 偵測 | 一拍決定，天然精確 | 多級偵測（decode 抓 illegal、MEM 抓 fault），要 flush |
| pipeline flush | 不需要（一次一條指令）| 必須：flush 出事指令之後的所有級 |
| CSR 讀寫級 | 單一拍完成 | 要定在哪一級（多在 MEM/WB），可能要 CSR forwarding |
| 中斷插入點 | 任何指令邊界 | 要挑一個「乾淨」的邊界（某級 commit 後）|
| mepc 精確性 | 天然精確 | 靠 flush + 壓制寫回維持 precise exception |

**本章刻意用單週期 core 做整合**，是為了把「四個機制怎麼協作」的**語意**做對、驗清楚，不被 pipeline flush 的時序細節淹沒。真正把這套接進五級 pipeline（處理 flush 接縫、precise exception、CSR hazard）是工程量很大的一步——final project 會帶你走一部分。先在乾淨的單週期模型裡確認「trap 進出 + handler + 中斷週期性」全對，再上 pipeline，才不會一次面對太多變數。這是「先把功能做對、再把時序做對」的正確順序。

## 踩雷區

**雷 1：以為 handler 可以隨便用暫存器，不用存。**
- 錯誤直覺：「handler 也是我的程式碼，用哪個暫存器我說了算」。
- 正確認識：handler 打斷的是**主程式**，主程式的每個暫存器都可能裝著它的活資料（範例二的 x5=0x5555）。handler 借用任何暫存器前**必須先存起來、用完還原**（範例二存 t4/t5），否則 mret 回去主程式的資料被蓋，行為錯亂且極難查（因為主程式「莫名其妙」少了個值）。範例一能不存，只因為它是刻意設計的 demo（確定沒有活資料在那些暫存器）；真系統一律要存。而且**連 sp 都是活資料**，所以要先用 mscratch 換一塊 handler 自己的 stack 才有地方存——這是 `csrrw sp, mscratch, sp` 存在的理由。

**雷 2：timer handler 對 mepc +4（把 ECALL 習慣套過來）。**
- 錯誤直覺：「trap handler 返回前都要 `mepc += 4` 跳過惹禍指令」。
- 正確認識：**interrupt 的 mepc 是「被打斷、還沒執行的指令」，handler 絕不能 +4**——+4 會跳過一條合法指令，主程式漏執行一條。範例一的 timer handler 沒有動 mepc，mret 直接回被打斷處（cyc26 回 0x30）重跑。只有 **exception**（ECALL/illegal，指令已「發生」）才可能要 +4。判準：看 mcause bit31——是中斷（1）就別動 mepc，是 exception（0）才考慮。Ch 32 的 ECALL handler 做了 `mepc += 4`（因為 ecall 是 exception、要跳過自己），這章的 timer handler 沒做（因為是中斷）——同一顆 core、兩種相反的 mepc 處理，全看 trap 種類。

**雷 3：timer handler 忘記重排 mtimecmp，導致中斷風暴。**
- 錯誤直覺：「handler 進來、做完事、mret 就好了」。
- 正確認識：timer 中斷的來源是 `mtime >= mtimecmp`。若 handler 不把 mtimecmp 往後推，mret 出去時 **MTIP 還是高**（mtime 仍 >= 舊 mtimecmp）→ 下一拍立刻又 interrupt_taken → 又進 handler → **無窮中斷風暴，主程式一步都跑不了**。範例一/二的 handler 都做了 `csrr mtime → +N → csrw mtimecmp`，把鬧鐘推到未來，mret 出去 MTIP 才落下（Ch 34 雷 3）。而且 N 要**大於 handler 自己的執行長度**——範例二一開始用 N=15，但 handler 有 14 條指令、跑完 mtime 已經漲超過 mtimecmp，於是 mret 出去馬上又觸發，變成幾乎每拍進 handler（實測 handler 進了 20+ 次而非 3 次）。改成 N=40（> handler 長度）才乾淨週期。**週期必須大於 handler 執行時間**，否則系統忙於處理中斷、沒空跑主程式——這是即時系統設計的真實約束。

**雷 4：以為 setup 順序無所謂，先開 MIE 再設 mtvec 也行。**
- 錯誤直覺：「反正這些 CSR 遲早都要設，先後沒差」。
- 正確認識：**開全域中斷（mstatus.MIE）必須是 setup 的最後一步**。若你先開 MIE、mtvec 還沒設好（還是 reset 的 0），這時若剛好來一個中斷，core 會跳到 `mtvec=0`（0x00000000）——那裡沒有 handler，是垃圾指令，直接崩。範例一/二都把 `csrw mstatus, 0x8`（開 MIE）放在最後，前面先設好 mtvec、mtimecmp、mie。正確順序：**先設 mtvec（handler 在哪）→ 設好 mtimecmp/mie（哪些源、何時響）→ 最後才開 mstatus.MIE（總開關）**。這和「先接好電話線、裝好電話機，最後才把插頭插上」一個道理。真 OS 開機也是這順序：trap vector 一定先於全域中斷開。

## 進階延伸

- **接進五級 pipeline 的 precise exception**：本章單週期 core 一次一條指令，trap 天然精確。接進 Ch 20 的五級 pipeline 後，trap 一發生要 flush「出事指令之後、已進 pipeline」的所有級（IF/ID/EX 塞 bubble）、壓制出事指令的寫回，才能保證 mepc 精確、handler 修好後 mret 回來對。中斷更微妙：它插在指令邊界，要挑一個「某條指令乾淨 commit、下一條還沒生效」的點注入 trap，mepc 存下一條。工業 core 常把 trap 統一在某一級（如 MEM 或 WB）處理，好定義「精確點」。這是 final project 整合 trap 到 pipeline 的核心難點。
- **Vectored mtvec：中斷依 cause 分流**：本章用 Direct 模式（mtvec 低 2 bit=00，所有 trap 都跳同一入口，handler 讀 mcause 用 if/else 分流）。mtvec 低 2 bit=01 是 Vectored 模式：**interrupt** 跳到 `base + 4*cause`（每個中斷源一個入口），省掉軟體分流、降低中斷延遲（exception 仍跳 base）。高效能中斷系統用 Vectored。把本章 handler 改成 Vectored 只要設 `mtvec = handler | 1`、並在 `base + 4*7` 放 timer 的專屬入口。
- **WFI：idle 時省電待命**：範例一/二的主迴圈用 busy loop（`beq` 空轉）等中斷，真實浪費電。真 OS 的 idle task 是一個 `wfi`（wait for interrupt）迴圈——CPU 執行到 `wfi` 就進低功耗待命，凍住直到有中斷 pending 才醒。把主迴圈的 `addi/beq` 換成 `wfi`，配合 core 加一段「`wfi` 時凍住 pc、等 interrupt_taken 才解凍」的邏輯，就是 tickless idle 的雛形。加它不難，是 bare-metal 省電的第一步。
- **巢狀中斷與 mepc/mcause 只有一份**：本章 handler 期間 MIE=0（硬體自動關），不會被打斷。但若 handler 想讓更高優先的中斷插隊（巢狀中斷），它得**先把 mepc/mcause 存進 stack**再重開 MIE——因為 mepc/mcause 只有一份，第二個 trap 會覆蓋它們，第一個 trap 的返回點就丟了。這是範例二的 context 保存要延伸的方向（不只存 GPR，還要存 mepc/mcause）。真 OS 的中斷入口都會存這些 machine CSR 到 per-trap 的 stack frame。

## 本章重點整理

- **整合 = 讓四個機制在同一條時間軸協作**：Ch 31~34 的 CSR / trap / privilege / CLINT 接進同一顆會取指的 core，靠「trap > mret > CSR 指令」的**優先序**解決寫回衝突、靠 next-PC 多工器讓 trap 壓過一切正常控制流、靠 commit 壓制維持 precise exception。
- **內建 CLINT + 教學 CSR 存取口**：mtime 每拍 +1、`mtime>=mtimecmp`→MTIP。handler 用 `csrr 0x7C2` 讀 mtime、`csrw 0x7C0` 寫 mtimecmp 重排鬧鐘（真硬體是 memory-mapped，語意同）。
- **完整最小系統真跑（範例一）**：主程式累加 x1、timer 每 15 tick 打斷一次（cyc21/38/55），handler 進 3 次（x2）、每次重排鬧鐘、mret 精確回斷點、主程式續跑。這是 time-slice 的硬體骨架。
- **真 handler 的頭尾骨架（範例二）**：`csrrw sp, mscratch, sp` 換 handler stack → 存要用的暫存器 → 處理 → 還原 → 換回 sp → mret。實測主程式活資料 x5=0x5555 完全沒被弄壞——handler 對主程式透明。
- **四大雷**：handler 用暫存器要先存（連 sp 都是活資料，靠 mscratch 換）；timer handler 不動 mepc（中斷 vs exception）；必須重排 mtimecmp 且週期 > handler 長度（否則中斷風暴）；setup 順序 mtvec 先於 MIE（否則跳 mtvec=0 崩）。

## 自我檢核

- [ ] 我能畫出整合後 core 一拍內的資料流，說出 next-PC 多工器和 clk 邊沿寫回各自的優先序，以及為什麼 trap 最高優先。
- [ ] 我能解釋為什麼 timer handler 要能讀 mtime、寫 mtimecmp，以及本章用自訂 CSR（0x7C0/0x7C2）當存取口對應真硬體的什麼機制。
- [ ] 我能追出範例一從 cyc21（第一次中斷）到 cyc26（mret 返回）的每一步，說明 mepc/mcause/x1/x2 各拍怎麼變、主程式為什麼能斷點續跑。
- [ ] 我能說出 `csrrw sp, mscratch, sp` 在做什麼、為什麼 handler 開頭需要它（連 sp 都是活資料的雞生蛋問題）。
- [ ] 我能解釋範例二為什麼 x5=0x5555 沒被弄壞，以及若 handler 不存 t4/t5 會發生什麼。
- [ ] 我能說出四大雷各自的正確做法，特別是「週期必須大於 handler 執行長度」為什麼是即時系統的真實約束。

## 延伸閱讀

- **[RISC-V Privileged Spec](https://riscv.org/technical/specifications/) 第 3.1.6~3.1.7 節（mstatus 的 trap 行為）、3.3 節（MRET）、3.2 節（mtime/mtimecmp）**：權威來源。讀它確認本章 core 的 trap 進出、mret 還原、CLINT timer 語意逐條對得上——特別是「中斷什麼時候能被 taken」（涉及 MIE 和指令邊界）那段，是本章「每個指令邊界檢查 trap」的正式定義。整合遇到「這個 bit 該不該翻、這個 CSR 該不該寫」的疑義時以它為最終仲裁。
- **[xv6-riscv 的 `kernel/kernelvec.S` 與 `kernel/trap.c`（kerneltrap / clockintr）](https://github.com/mit-pdos/xv6-riscv/tree/riscv/kernel)**：本章範例二 handler 的完整工業版。`kernelvec.S` 就是「用 mscratch 換 stack、把**全部** 31 個暫存器存進 trapframe、呼叫 C handler、還原、mret」——把範例二的「存 t4/t5」放大成存全部。`trap.c` 的 `clockintr()` 是 timer handler 的真實版（重設 mtimecmp、`yield()` 換行程），讀它就懂本章的 mini timer handler 放大成真 OS 排程器長什麼樣。對照著讀，你會發現本章每一個動作都在 xv6 裡有對應。
- **[SiFive Interrupt Cookbook](https://sifive.cdn.prismic.io/sifive/0d163928-2128-42be-a75a-464df65e04e0_sifive-interrupt-cookbook.pdf)**：SiFive 官方中斷實務手冊，從硬體廠角度講 trap 進出、Direct vs Vectored mtvec、中斷延遲怎麼算、handler 的 prologue/epilogue（存/還原 context）怎麼寫才快。它把 spec 的抽象連到「真晶片上 handler 怎麼配置」，是本章「整合後怎麼在真系統用」的最佳延伸。讀它的 handler 範本，和本章範例二對照。
- **《Computer Organization and Design, RISC-V Edition》(Patterson & Hennessy) 第 4.9 節「Exceptions」**：從 pipeline datapath 角度講 exception 怎麼進 pipeline、為什麼要 flush、precise exception 怎麼維持。本章用單週期把語意做對，這本書畫的「exception 在 pipeline 各級的處理」圖，正是把本章 core 接進五級 pipeline（進階延伸第一點、final project）的藍圖。想從單週期跨到 pipeline trap，先讀它。

Part 5 到這裡，你已經有一顆能處理例外、系統呼叫、特權違規、timer 中斷的完整 core，還會寫真 handler 讓它跑起來——這正是一顆能承載作業系統的 CPU 的雛形。接下來用練習 E 親手把這套跑一遍、加上你自己的變化，把「整合」從讀懂變成做過。

→ [練習 E CSR + timer interrupt：讓 core 週期性進出 trap handler](./practice-e-csr-timer-interrupt.md)
