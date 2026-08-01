# Ch 8 — Register File（2R1W）

> **目標**：親手做出 RV32I 的暫存器檔案（register file）。你會實作課程約定的 **2 讀口 1 寫口**（2R1W）介面、把 x0 硬接成 0、read 用非同步、write 用同步，然後寫 C++ testbench 逐項驗證：寫入再讀出、x0 恆 0、同 cycle 讀寫的行為。全程真跑貼輸出。並回答一個關鍵設計問題：為什麼是 2 讀 1 寫？
> **環境**：WSL + verilator 4.038。輸出皆真跑。

## 為什麼需要 register file？

fetch 抓到 `add x3, x1, x2`。這條指令要「把 x1 和 x2 的值加起來」。可是 x1、x2 的值存在哪？——存在 **register file**，CPU 內部那 32 個 32-bit 暫存器（x0~x31）。它是 CPU 手邊最快的一塊儲存：

- **ALU 的兩個運算元幾乎都來自它**（rs1、rs2）。
- **運算結果幾乎都寫回它**（rd）。
- 它比記憶體快好幾個數量級，是 CPU 每個 cycle 都在讀寫的核心。

沒有 register file，每個運算元都得跑去記憶體拿、結果都得寫回記憶體，慢到不能看。register file 是 CPU「隨手可拿的便條紙」。這章我們把它做出來。

## 先建立直覺：32 格便條紙 + 兩隻讀手一隻寫手

想像一疊 32 格的便條紙（x0~x31），每格能寫一個 32-bit 數字：

```
       register file (32 格便條紙)
    ┌──────────────────────────────┐
    │ x0  = 0    (永遠是 0，寫不進) │
    │ x1  = ...                     │
    │ x2  = ...                     │
    │ ...                           │
    │ x31 = ...                     │
    └──────────────────────────────┘
        ▲          ▲            │
        │ 讀手1    │ 讀手2      │ 寫手
      rs1_addr   rs2_addr    rd_addr
        │          │            │
        ▼          ▼            ▼
     rs1_data   rs2_data     rd_data (要寫進去的值)
```

三隻手同時工作：

- **兩隻讀手（rs1、rs2）**：一條指令要同時讀兩個源暫存器（`add` 要 x1 和 x2），所以要**兩個獨立讀口**。讀手指到哪格，那格的值**立刻**出現（非同步）。
- **一隻寫手（rd）**：把結果寫回一個目標暫存器。寫手比較慢——它要**等 clock 上升沿**才落筆（同步）。
- **x0 這格特別**：它焊死是 0，寫手往它寫也沒用，讀手讀它永遠拿到 0。

為什麼讀是即時、寫要等 clock？因為在單週期裡，一條指令要在一個 cycle 內「讀源→運算→寫回」。讀必須即時（不然運算等不到運算元），但寫必須等到 cycle 結尾的 clock 邊沿（不然結果還沒算完就寫進去了，而且新舊值會打架）。這個「非同步讀、同步寫」是 register file 的靈魂，也是最容易搞錯的地方。

## 核心概念：為什麼是 2 讀口 1 寫口？

這是本章要你想通的設計問題。答案藏在指令格式裡。

看 RV32I 最典型的 R-type 指令 `add rd, rs1, rs2`：它一次要**兩個輸入**（rs1、rs2）和**一個輸出**（rd）。ALU 是雙輸入的，它需要同一個 cycle 內同時拿到兩個運算元——如果只有一個讀口，你得分兩次讀，單週期就做不完了。所以**至少要 2 個讀口**。

寫呢？RV32I 每條指令最多寫**一個**目標暫存器（rd）。沒有任何一條基本指令一次寫兩個暫存器。所以 **1 個寫口** 就夠。

```
    一條 R-type 指令的暫存器需求
    ┌───────────────────────────┐
    │  add  rd , rs1 , rs2       │
    │        │     │     │       │
    │        │     └──┬──┘       │
    │        │      2 個讀 ──────▶ 需要 2 讀口
    │        └──────── 1 個寫 ───▶ 需要 1 寫口
    └───────────────────────────┘
```

這就是 **2R1W** 的由來——它剛好對上「單週期執行一條 RV32I 指令」的最小需求。多做讀口/寫口是浪費硬體（每個 port 都要成本，見本章末進階），少做則單週期跑不動。2R1W 是精準的最小配置。

> 到了 superscalar（一 cycle 發多條指令）才需要更多 port——兩條指令同時執行就要 4 讀 2 寫。但那是 Part 6 的事，本課單發射，2R1W 到底。

## 底層機制：非同步讀 + 同步寫 + x0 硬接 0

按課程約定實作。`regfile.sv`：

```systemverilog
// regfile.sv — 2R1W register file，x0 硬接 0，async read / sync write
module regfile (
    input  logic        clk,
    input  logic        rd_we,       // 寫致能 write enable
    input  logic [4:0]  rd_addr,     // 寫目標
    input  logic [31:0] rd_data,     // 要寫的值
    input  logic [4:0]  rs1_addr,    // 讀口 1 位址
    input  logic [4:0]  rs2_addr,    // 讀口 2 位址
    output logic [31:0] rs1_data,    // 讀口 1 資料
    output logic [31:0] rs2_data     // 讀口 2 資料
);
    // 32 個 32-bit 暫存器。x0 不存實體，讀 addr 0 直接給 0。
    logic [31:0] regs [1:31];

    // 非同步讀：位址一變，資料立刻出來（不等 clk）
    assign rs1_data = (rs1_addr == 5'd0) ? 32'd0 : regs[rs1_addr];
    assign rs2_data = (rs2_addr == 5'd0) ? 32'd0 : regs[rs2_addr];

    // 同步寫：clk 上升沿寫入，且不寫 x0
    always_ff @(posedge clk) begin
        if (rd_we && rd_addr != 5'd0)
            regs[rd_addr] <= rd_data;
    end
endmodule
```

拆解三個機制：

- **x0 硬接 0**：我們刻意把陣列宣告成 `regs[1:31]`——**根本沒有 index 0 的實體**。讀口用三元運算子攔截：位址是 0 就直接吐 `32'd0`，不查陣列。寫口也守一道 `rd_addr != 5'd0`，就算致能開著、目標是 x0，也不寫。x0 從硬體層面保證恆 0，不靠「小心不要寫它」的軟體紀律。
- **非同步讀**：用 `assign`（組合邏輯）。rs1_addr 一變，rs1_data 在同一瞬間跟著變，不等 clock。這樣單週期才能「讀完馬上餵給 ALU」。
- **同步寫**：用 `always_ff @(posedge clk)`。只有在 clock 上升沿、且 `rd_we` 開著、且不是 x0，才把 rd_data 落進去。寫入被 clock 邊沿嚴格對齊。

`rd_we`（write enable）為什麼要有？因為不是每條指令都寫暫存器——`beq`、`sw` 就不寫。control unit（Ch 10）會依指令決定 rd_we 開或關。開才寫，關就這個 cycle 不動 register file。

## 範例：C++ testbench 逐項驗證

我們要驗四件事：寫入再讀出正確、兩讀口能同時讀不同暫存器、x0 恆 0、同 cycle 讀寫的先後行為。`regfile_tb.cpp`：

```cpp
#include "Vregfile.h"
#include "verilated.h"
#include <cstdint>
#include <cstdio>

static Vregfile *dut;
static int fails = 0;

static void tick() {
    dut->clk = 0; dut->eval();
    dut->clk = 1; dut->eval();
}

// 寫入一個暫存器：擺好寫埠，走一個 clock 邊沿
static void write_reg(int addr, uint32_t data) {
    dut->rd_we = 1;
    dut->rd_addr = addr;
    dut->rd_data = data;
    tick();
    dut->rd_we = 0;
}

// 非同步讀 rs1
static uint32_t read_rs1(int addr) {
    dut->rs1_addr = addr;
    dut->eval();
    return dut->rs1_data;
}

static void check(const char *name, uint32_t got, uint32_t exp) {
    bool ok = got == exp;
    printf("[%s] %-16s got=0x%08x exp=0x%08x\n", ok ? "OK " : "BAD", name, got, exp);
    if (!ok) fails++;
}

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    dut = new Vregfile;
    dut->rd_we = 0; dut->rd_addr = 0; dut->rd_data = 0;
    dut->rs1_addr = 0; dut->rs2_addr = 0;

    // 1) 寫入 x5=0xDEADBEEF，再讀回
    write_reg(5, 0xDEADBEEF);
    check("write-then-read", read_rs1(5), 0xDEADBEEF);

    // 2) 兩個讀口可同時讀不同暫存器
    write_reg(6, 0x11112222);
    dut->rs1_addr = 5; dut->rs2_addr = 6; dut->eval();
    check("2R-port-rs1", dut->rs1_data, 0xDEADBEEF);
    check("2R-port-rs2", dut->rs2_data, 0x11112222);

    // 3) x0 恆為 0：就算試圖寫也讀出 0
    write_reg(0, 0xFFFFFFFF);
    check("x0-stays-zero", read_rs1(0), 0x00000000);

    // 4) 同 cycle 讀寫：async read 讀到的是「舊值」，寫入下個沿才生效
    write_reg(7, 0xAAAA0000);          // 先讓 x7 有舊值
    dut->rd_we = 1; dut->rd_addr = 7; dut->rd_data = 0xBBBB1111;
    dut->rs1_addr = 7; dut->eval();     // clk 尚未上升沿
    check("same-cycle-read-old", dut->rs1_data, 0xAAAA0000); // 讀到舊值
    tick();                             // 這個沿才寫入新值
    dut->rd_we = 0;
    check("after-edge-new", read_rs1(7), 0xBBBB1111);

    printf("\n%s (%d fail)\n", fails ? "FAILED" : "ALL PASSED", fails);
    delete dut;
    return fails ? 1 : 0;
}
```

編譯執行：

```bash
verilator --cc regfile.sv --exe regfile_tb.cpp --Mdir obj_dir
make -C obj_dir -f Vregfile.mk Vregfile
./obj_dir/Vregfile
```

真實輸出：

```
[OK ] write-then-read  got=0xdeadbeef exp=0xdeadbeef
[OK ] 2R-port-rs1      got=0xdeadbeef exp=0xdeadbeef
[OK ] 2R-port-rs2      got=0x11112222 exp=0x11112222
[OK ] x0-stays-zero    got=0x00000000 exp=0x00000000
[OK ] same-cycle-read-old got=0xaaaa0000 exp=0xaaaa0000
[OK ] after-edge-new   got=0xbbbb1111 exp=0xbbbb1111

ALL PASSED (0 fail)
```

逐項解讀：

- **write-then-read**：寫 x5=0xDEADBEEF 後讀回一致，寫入路徑通。
- **2R-port**：同一 `eval()`（不走 clock）下，rs1 讀 x5、rs2 讀 x6，兩個獨立值同時出來。兩個讀口確實各自獨立。
- **x0-stays-zero**：對 x0 寫 0xFFFFFFFF，讀回還是 0。硬接生效——就算 rd_we 開著、目標是 x0，也寫不進。
- **same-cycle-read-old / after-edge-new**：這組是重點。x7 舊值 0xAAAA0000。在**還沒走 clock 邊沿**時，同時擺好「要寫 0xBBBB1111」和「讀 x7」——讀口拿到的是**舊值** 0xAAAA0000，因為同步寫要等邊沿。`tick()` 走過邊沿後再讀，才是新值 0xBBBB1111。這證明了「非同步讀看到的是這個 cycle 開始時的值，同步寫在 cycle 結尾才落地」。

## 這個「同 cycle 讀寫」行為為什麼重要？

第 4 項不是為了炫技。它決定了單週期 datapath 的一個時序假設：

在單週期裡，一條指令在 cycle 內先**讀**源暫存器餵 ALU，ALU 算完在 cycle 結尾**寫**回 rd。這一讀一寫是同一個 cycle。我們的 regfile「讀舊值、cycle 尾寫新值」正好對——這條指令讀的是**上一條指令留下的值**（正確），寫的是**自己算的結果**（要給下一條用）。

如果 regfile 做成「同 cycle 讀就看到剛寫的新值」（write-through），單週期反而會出問題：那等於允許同一 cycle 內既讀又寫同一格並看到新值，破壞了「一 cycle 一條獨立指令」的乾淨模型。到了 pipeline（Part 2），這個「讀舊值」特性又會變成 data hazard 的根源之一，要靠 forwarding 補救——那是 Ch 16 的伏筆。現在你先確認：**本課 regfile 是讀舊值型**。

## 對比取捨

| 設計選擇 | 本課做法 | 替代方案 | 理由 |
|---|---|---|---|
| x0 實作 | 陣列宣告 `[1:31]`，讀寫都攔 addr 0 | 存實體但每次 reset 成 0 | 不存實體最省、最不可能被寫壞 |
| 讀取型別 | 非同步（組合 assign） | 同步（clock 後才出） | 單週期要同 cycle 讀完餵 ALU |
| 寫入型別 | 同步（always_ff） | 非同步（latch） | 寫要對齊 clock 邊沿，latch 難時序推理 |
| port 數 | 2R1W | 4R2W（superscalar） | 單發射 RV32I 精準最小需求 |
| 同 cycle 讀寫 | 讀舊值 | write-through 讀新值 | 保單週期模型乾淨；hazard 留給 pipeline 處理 |

## 踩雷區

**雷 1：以為 x0 靠「軟體不去寫它」就好。**
- 錯誤直覺：「x0 是 0，那我約定好不要寫它不就得了」。
- 正確認識：x0 是**硬體保證**恆 0，不靠約定。編譯器產生的 code 常會故意寫 x0（例如 `addi x0, x0, 0` 當 nop，或把不要的結果丟給 x0）。硬體必須讓這些寫入無效、讀出恆 0。我們用「不存實體 + 讀寫都攔 addr 0」雙保險做到。

**雷 2：把讀做成同步。**
- 錯誤直覺：「暫存器是時序元件，讀寫都該用 always_ff」。
- 正確認識：**寫**是時序（要記住、要對齊 clock），但**讀**必須組合。單週期一個 cycle 內要「讀源→ALU→寫回」，讀若也要等一個 clock，這一 cycle 就塞不下了。讀用 `assign`（組合），寫用 `always_ff`（時序），兩者分開，這是 register file 最關鍵的結構。

**雷 3：忘了 write enable，導致每 cycle 亂寫。**
- 錯誤直覺：「有 rd_addr 和 rd_data 就能寫了」。
- 正確認識：必須有 `rd_we` 守門。`beq`、`sw` 這類指令不寫暫存器，如果沒有 write enable，它們的 rd 欄位（可能是垃圾）會每 cycle 污染 register file。control unit 依指令決定 rd_we，關著就這 cycle 不寫。

**雷 4：以為同 cycle 讀寫同一暫存器會讀到新值。**
- 錯誤直覺：「這個 cycle 寫 x7，同 cycle 讀 x7 應該拿到剛寫的值」。
- 正確認識：本課是**讀舊值**型。同步寫要等 clock 上升沿才落地，而非同步讀在邊沿**之前**就把當下（舊）值送出去了。範例第 4 項證明了這點。想拿到新值得等下個 cycle。這個特性在 pipeline 會變成 hazard，是後面 forwarding 存在的理由之一。

## 進階延伸

- **port 的硬體成本**：每個讀口是一組從 32 選 1 的大 mux，每個寫口是一組 decoder + write enable 分配。port 越多，這些邏輯越大、佈線越擠、時序越難收。所以 port 數不是越多越好，是「剛好夠用」。2R1W 對單發射就是剛好。superscalar 的多 port register file 是晶片上最耗面積、最難設計的模組之一，甚至要拆 bank 或加 replica 來緩解。
- **register file 通常不是 flip-flop 陣列**：教學上我們用 32 個 flip-flop 想像它，但真晶片裡 register file 常用特製的多口 SRAM cell 或 latch array 實作，密度和速度都更好。RTL 層我們不管這個，合成/後端工具會把 `logic [31:0] regs[...]` 對應到適當的實體結構。
- **x0 在 ISA 設計上的妙用**：把 x0 硬接 0 讓很多 pseudo-instruction 免費出現——`mv rd, rs` 就是 `addi rd, rs, 0`、`li rd, 0` 就是 `addi rd, x0, 0`、`j label` 用 x0 當丟棄的 return address。ISA 花一個暫存器名額換來大量指令簡化，是 RISC 哲學的經典取捨。
- **pipeline 的伏筆**：本課「讀舊值」在單週期是對的，但 pipeline 裡前一條指令的結果還沒寫回 register file，下一條就來讀，會讀到過期值（data hazard）。Ch 16 的 forwarding 就是繞過 register file、把還在管線裡的新值直接抄給後面指令。有些 pipeline 設計還會讓 register file「前半 cycle 寫、後半 cycle 讀」來省一級 forwarding——那是 Ch 16 的細節，現在知道有這回事即可。

## 本章重點整理

- register file 是 CPU 最快的儲存，32 個 32-bit 暫存器，ALU 的運算元來源、結果去處。
- **2R1W** 對上單發射 RV32I 的精準需求：R-type 一次讀兩源（2 讀口）、最多寫一目標（1 寫口）。多了浪費、少了跑不動。
- 靈魂結構：**非同步讀**（組合 assign，同 cycle 讀完餵 ALU）+ **同步寫**（always_ff，對齊 clock 邊沿）。
- **x0 硬接 0** 是硬體保證：陣列不存實體 index 0，讀寫都攔位址 0。不靠軟體紀律。
- `rd_we`（write enable）守門，不寫暫存器的指令關掉它，避免污染。
- 本課 regfile 是**讀舊值**型（同 cycle 讀寫同格讀到舊值）——單週期正確，pipeline 會變 hazard，是後面 forwarding 的伏筆。

## 自我檢核

- [ ] 我能解釋為什麼是 2 讀口 1 寫口，並從 R-type 指令的輸入輸出需求推出來。
- [ ] 我能說清楚「讀非同步、寫同步」各自的原因，以及若把讀做成同步會壞掉什麼。
- [ ] 我能講出 x0 硬接 0 的兩道防線（不存實體、讀寫攔位址 0），以及為什麼不能只靠軟體不去寫它。
- [ ] 我能解釋 `rd_we` 的作用，並舉出兩條不該寫暫存器的指令。
- [ ] 我能預測「同 cycle 寫 x7 又讀 x7」讀到舊值還是新值，並說出這對單週期和 pipeline 各代表什麼。
- [ ] 我能不看講義寫出 regfile 的讀口 assign 和寫口 always_ff，包含 x0 的攔截。

## 延伸閱讀

- **《Computer Organization and Design, RISC-V Edition》(Patterson & Hennessy) 第 4.3 節「register file」小段**：看它怎麼把 register file 畫成「兩個讀 port + 一個寫 port + write enable」的方塊，以及為什麼寫要接 clock、讀不用。和本章的 RTL 一一對應。
- **《Digital Design and Computer Architecture, RISC-V Edition》(Harris & Harris) 第 5.5 節「Memory Arrays」與第 7.3 節 register file 部分**：Harris 從記憶體陣列的角度講 register file，補充 port、decoder、mux 的硬體結構，讓你懂「多一個 port 貴在哪」。
- **[RISC-V Unprivileged ISA Spec](https://riscv.org/technical/specifications/) 第 2.1 節「Programmers' Model for Base Integer ISA」**：權威定義 x0~x31 與 x0 恆 0 的語意。翻一下確認「x0 硬接 0」是 ISA 強制而非實作選擇。
- **[picorv32 原始碼](https://github.com/YosysHQ/picorv32) 搜 `cpuregs`**：看一個真 core 的 register file 怎麼寫，注意它如何處理 x0、以及它有個編譯選項在 register file 用兩塊 RAM 各出一個讀口（因為某些 FPGA 的 block RAM 只有一個讀口，要兩塊才湊出 2R）。這是「教學 RTL vs 真實硬體限制」的好對照。

register file 讓 CPU 存取暫存器，但誰來真正做加減比較移位？下一章我們做 ALU——CPU 的計算引擎。

→ [Ch 9 ALU 與 ALU control](./09-alu.md)
