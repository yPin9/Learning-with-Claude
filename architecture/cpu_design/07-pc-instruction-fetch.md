# Ch 7 — PC 與 instruction fetch

> **目標**：親手做出 CPU 的第一個動作——抓指令。你會實作 PC 暫存器（always_ff）、PC+4 邏輯、用 `$readmemh` 載入程式的指令記憶體 imem，把它們接成最小 fetch datapath，reset vector 設在 `0x80000000`，然後組一支真的 RISC-V 程式讓它連續抓出來，逐條印出驗證。
> **環境**：WSL + verilator 4.038 + riscv64-unknown-elf-gcc 10.2.0。本章所有輸出都是真跑出來的。

## 為什麼需要 fetch？

Ch 6 那張全景圖，資料流的**源頭**是 PC。CPU 什麼都還沒算之前，得先知道「現在要執行哪一條指令」。這件事就是 fetch：

- 用 **PC（program counter）** 當位址。
- 去**指令記憶體 imem** 抓那個位址上的 32-bit 指令。
- 順便算出**下一條**的位址（正常情況就是 PC+4）。

沒有 fetch，後面 decode、execute 全是空談——它們拿不到指令。所以我們從這裡開刀。fetch 也是最容易「看起來對其實錯」的地方：位址算錯一個 bit、reset 沒設對、記憶體 index 沒對齊，你抓出來的就是垃圾，而且不會有任何 error message，只有波形裡一個錯掉的值。

## 先建立直覺：翻書的手指

把程式想成一本書，每一頁是一條指令。PC 就是你**指著當前這頁的手指**：

```
       ┌─────────────────────────────┐
       │  imem (一本指令書)          │
       │                             │
  PC   │  0x80000000: addi x1,x0,1  │ ◀── 手指現在指這裡
 ────▶ │  0x80000004: addi x2,x0,2  │
       │  0x80000008: add  x3,x1,x2 │
       │  0x8000000c: sub  x4,x2,x1 │
       │       ...                   │
       └─────────────────────────────┘

  每個 cycle：
    1. 看手指指的那頁 → 這就是抓到的指令 (instr)
    2. 手指往下移一頁 (PC = PC + 4)
```

兩個關鍵：

- **手指指哪，就抓哪**（imem 依 PC 吐指令，這是**組合**動作，位址一給資料就出來）。
- **手指每 cycle 往下移一格**（PC 在 clock 上升沿更新成 PC+4，這是**時序**動作，有記憶）。

為什麼是 +4 不是 +1？因為 RV32I 每條指令是 **4 個 byte**（32 bit），而記憶體位址是按 byte 編號的。指向下一條，位址要跳 4。

## 核心概念：fetch 由三塊組成

```
     ┌──────────────────────────────────────┐
     │                                        │
     │   ┌────────┐                           │
     │   │  +4    │◀──────────┐               │
     │   └───┬────┘           │ pc            │
     │       │ pc_next        │               │
     │       ▼                │               │
     │   ┌────────┐  pc   ┌────┴───┐  instr   │
     └──▶│  PC    │──────▶│  imem  │─────────▶  給後面 decode 用
  rst ──▶│ (ff)   │       │(唯讀陣列)│
  clk ──▶└────────┘       └────────┘
```

1. **PC 暫存器**：一個 32-bit flip-flop（`always_ff`）。reset 時被設成 reset vector `0x80000000`；否則每個 clock 上升沿更新成 pc_next。
2. **PC+4 加法器**：純組合，`pc_next = pc + 4`。
3. **imem**：一塊唯讀記憶體，內容用 `$readmemh` 從 hex 檔載入。給它位址（PC），組合吐出該處的 32-bit word。

三塊裡只有 PC 有「記憶」（時序）。imem 的讀和 +4 都是組合邏輯——位址一變，輸出立刻跟著變。

## 底層機制：reset vector 與位址對齊

兩個實作細節，是 fetch 最容易踩雷的地方，先講清楚。

### reset vector：CPU 開機從哪開始

CPU 一上電（或 reset），PC 得有個確定的初值，否則它不知道從哪抓第一條指令。這個初值叫 **reset vector**。我們全課約定是 `0x80000000`——這也是 QEMU/spike 等 RISC-V 平台常見的 RAM 起始位址，之後組程式也用 `-Ttext=0x80000000` 對齊。

我們用**同步 reset**：reset 訊號要在 clock 上升沿才生效。

```systemverilog
always_ff @(posedge clk) begin
    if (rst)
        pc <= RESET_PC;      // reset 時強制回到 reset vector
    else
        pc <= pc + 32'd4;    // 否則正常前進
end
```

### 位址對齊：從 byte 位址到 word index

這裡有個容易錯的轉換。PC 是**按 byte 編號**的位址（`0x80000000`、`0x80000004`、...），但 imem 我們把它做成**一格一個 word 的陣列**（`imem[0]`、`imem[1]`、...）。所以要把 byte 位址轉成 word index：

```
word_index = (pc - RESET_PC) >> 2
```

- 先減掉 base（`0x80000000`），因為陣列從 0 開始，但 PC 從 `0x80000000` 開始。
- 再右移 2 位（等於除以 4），因為每條指令佔 4 個 byte，位址每跳 4 對應陣列跳 1。

跳過這步、直接拿 PC 當陣列 index，你會存取到天文數字的位址，抓出全 X（未定義）。這是新手第一大坑。

## 範例 1：最小 fetch 模組

把上面拼起來。`fetch.sv`：

```systemverilog
// fetch.sv — PC 暫存器 + instruction memory，最小 fetch datapath
module fetch #(
    parameter RESET_PC = 32'h8000_0000,
    parameter IMEM_WORDS = 256          // 256 words = 1 KiB
) (
    input  logic        clk,
    input  logic        rst,        // active-high 同步 reset
    output logic [31:0] pc,         // 目前 PC
    output logic [31:0] instr       // 這個 PC 抓到的指令
);
    // instruction memory：每格一個 32-bit word，$readmemh 從檔案載入
    logic [31:0] imem [0:IMEM_WORDS-1];
    initial $readmemh("prog.hex", imem);

    // PC 暫存器：同步 reset 回 reset vector，否則每 cycle +4
    logic [31:0] pc_next;
    assign pc_next = pc + 32'd4;

    always_ff @(posedge clk) begin
        if (rst)
            pc <= RESET_PC;
        else
            pc <= pc_next;
    end

    // 位址轉 word index：程式從 RESET_PC 開始，減 base 再除以 4
    logic [31:0] word_index;
    assign word_index = (pc - RESET_PC) >> 2;

    // 非同步讀指令（imem 是組合讀）
    assign instr = imem[word_index[$clog2(IMEM_WORDS)-1:0]];
endmodule
```

幾個要點：

- `$readmemh("prog.hex", imem)` 在模擬啟動時把 hex 檔的每一行（一個 8 位 16 進位 = 一個 word）依序填進 `imem[0]`、`imem[1]`...。
- `word_index[$clog2(IMEM_WORDS)-1:0]` 只取低位當 index，把它夾在陣列範圍內。`$clog2(256)=8`，所以取低 8 bit。這避免超出陣列邊界（verilator 會對越界很敏感）。
- imem 的讀是 `assign`（組合），PC 是 `always_ff`（時序）。兩種元件的分工在這裡一目了然。

## 範例 2：組一支真程式，餵給 fetch

我們需要真的指令，不是手打的 magic number。寫一支 RV32I 小程式 `prog.S`：

```asm
    .section .text
    .globl _start
_start:
    addi x1, x0, 1        # x1 = 1
    addi x2, x0, 2        # x2 = 2
    add  x3, x1, x2       # x3 = 3
    sub  x4, x2, x1       # x4 = 1
    xor  x5, x1, x2       # x5 = 3
    or   x6, x1, x2       # x6 = 3
```

用 toolchain 組譯，轉成 imem 要的 hex 格式。這條流程你之後每章都會用，值得記熟：

```bash
# 1. 組譯成 ELF，text 段落在 reset vector
riscv64-unknown-elf-gcc -march=rv32i -mabi=ilp32 -nostdlib \
    -Ttext=0x80000000 -o prog.elf prog.S

# 2. 抽出純機器碼（去掉 ELF header）
riscv64-unknown-elf-objcopy -O binary prog.elf prog.bin

# 3. 每 4 byte 一個 little-endian word，轉成一行一個 8 位 hex
python3 -c '
import struct
data = open("prog.bin","rb").read()
words = [struct.unpack("<I", data[i:i+4])[0] for i in range(0, len(data), 4)]
open("prog.hex","w").write("\n".join("%08x" % w for w in words) + "\n")
'
```

跑完，看 `prog.hex` 和反組譯對照，確認轉換沒錯：

```
$ cat prog.hex
00100093
00200113
002081b3
40110233
0020c2b3
0020e333

$ riscv64-unknown-elf-objdump -d prog.elf | grep -A6 _start
80000000 <_start>:
80000000:	00100093          	li	ra,1
80000004:	00200113          	li	sp,2
80000008:	002081b3          	add	gp,ra,sp
8000000c:	40110233          	sub	tp,sp,ra
80000010:	0020c2b3          	xor	t0,ra,sp
80000014:	0020e333          	or	t1,ra,sp
```

hex 檔每一行的機器碼，跟 objdump 左邊那欄**一模一樣**（`00100093`、`00200113`...）。轉換正確。

> `objdump` 把 `addi x1,x0,1` 印成 `li ra,1` 是因為它用 ABI 暫存器名（x1=ra）並認出 `addi rd,x0,imm` 是 `li` 的慣用寫法（pseudo-instruction）。機器碼完全相同，只是反組譯器的顯示習慣。

## 範例 3：C++ testbench 驅動 fetch，逐條印出

`fetch_tb.cpp`：

```cpp
#include "Vfetch.h"
#include "verilated.h"
#include <cstdint>
#include <cstdio>

static Vfetch *dut;

static void tick() {
    dut->clk = 0; dut->eval();
    dut->clk = 1; dut->eval();
}

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    dut = new Vfetch;

    // 同步 reset：rst=1 走一個沿把 PC 設回 reset vector
    dut->rst = 1;
    tick();
    dut->rst = 0;
    dut->eval();  // 讓組合邏輯（instr）依 reset 後的 PC 更新

    // 連續抓 6 條指令
    for (int i = 0; i < 6; i++) {
        printf("cycle %d: PC=0x%08x  instr=0x%08x\n", i, dut->pc, dut->instr);
        tick();
        dut->eval();
    }

    delete dut;
    return 0;
}
```

`tick()` 走一個完整 clock 週期（低→高）。reset 那一 tick 把 PC 鎖成 `0x80000000`，之後每 tick PC 前進 4，imem 吐出對應指令。

編譯執行：

```bash
verilator --cc fetch.sv --exe fetch_tb.cpp --Mdir obj_dir
make -C obj_dir -f Vfetch.mk Vfetch
./obj_dir/Vfetch
```

真實輸出：

```
cycle 0: PC=0x80000000  instr=0x00100093
cycle 1: PC=0x80000004  instr=0x00200113
cycle 2: PC=0x80000008  instr=0x002081b3
cycle 3: PC=0x8000000c  instr=0x40110233
cycle 4: PC=0x80000010  instr=0x0020c2b3
cycle 5: PC=0x80000014  instr=0x0020e333
```

逐項核對：

- PC 從 `0x80000000` 起步（reset vector 正確），每 cycle +4（`...0`→`...4`→`...8`→`...c`→`...10`→`...14`），對齊正確。
- 每個 PC 抓到的 instr，跟 `prog.hex`／objdump 完全吻合：`00100093`（addi x1）、`00200113`（addi x2）、`002081b3`（add x3）... 一路對到 `0020e333`（or x6）。

fetch 通了。CPU 學會抓指令了。

## 範例 4（邊界）：忘了減 base，抓出垃圾

故意示範坑。把 word_index 改成直接用 PC（新手常犯）：

```systemverilog
assign word_index = pc >> 2;   // 錯！沒減 RESET_PC
```

`pc = 0x80000000`，`pc >> 2 = 0x20000000` = 536870912，遠超 256 格的陣列。取低 8 bit後 index 幾乎都落在同一格或 0，抓出的指令會全錯或全是 reset 值。verilator 在有 `--Wall` 時甚至會警告陣列越界。

教訓：**位址空間的 base 和陣列的 base 是兩回事**，中間差一個減法。這個 bug 不會 crash，只會讓你抓到莫名其妙的指令，是波形除錯的常客。

## 對比取捨：fetch 的幾個設計選擇

| 選擇 | 本課做法 | 替代方案 | 為什麼這樣選 |
|---|---|---|---|
| reset 型別 | 同步（`if(rst)` 在 always_ff 內） | 非同步（`always_ff @(posedge clk or posedge rst)`） | 同步 reset 對 FPGA/合成更友善、時序更好推理，教學也單純 |
| imem 讀取 | 組合讀（`assign instr = imem[...]`） | 同步讀（clock 後才出資料） | 單週期要同 cycle 抓完，必須組合讀。pipeline 才會改同步（Ch 14） |
| imem 大小 | 參數 256 words | 固定值 | 參數化好調整；用 `$clog2` 自動算 index 寬度 |
| 位址寬度 | 全 32-bit | 只留用到的低位 | 32-bit 對齊 XLEN，好對照；index 才截低位 |
| PC+4 來源 | 專用加法器 | 共用 ALU | 單週期 ALU 忙著算指令，PC 前進要獨立加法器，不能搶 |

## 踩雷區

**雷 1：以為 imem 讀取需要一個 clock cycle。**
- 錯誤直覺：「給位址後要等下個 cycle 資料才出來」。
- 正確認識：本課 imem 是**組合讀**——位址一變（PC 一更新），instr 在**同一個 cycle 內**立刻跟著變。這是單週期能在一 cycle 做完的前提。「讀記憶體要一 cycle」是真實 SRAM 或 pipeline 的同步記憶體行為，那要到 Ch 14、Ch 26 才登場。別把它提前套進來。

**雷 2：混淆 byte 位址和 word index。**
- 錯誤直覺：「PC 是 `0x80000004`，那就是 imem 的第 4 格」。
- 正確認識：PC 是 byte 位址。`0x80000004` 是第 **1** 格（`(0x80000004 - 0x80000000) >> 2 = 1`）。差了 base 減法和除以 4 兩步。搞錯這個，你抓的指令會全部偏移或越界。

**雷 3：reset 只設值卻沒走 clock 邊沿。**
- 錯誤直覺：「`dut->rst = 1; dut->eval();` 就能把 PC 設成 reset vector」。
- 正確認識：同步 reset **要 clock 上升沿才生效**。testbench 裡必須 `rst=1` 之後 `tick()` 走一個完整週期，PC 才會被鎖成 reset vector。只 `eval()` 不 tick，PC 還是初始的未定義值（或 0）。這也是為什麼範例裡 reset 後緊接一個 `tick()`。

**雷 4：以為 PC+4 是「加 1」。**
- 錯誤直覺：「下一條指令，PC 加 1」。
- 正確認識：RV32I 每條指令 4 byte，位址按 byte 編號，所以是 **+4**。加 1 會指到指令**中間**的第二個 byte，抓出的是兩條指令拼接的垃圾。（RISC-V 有壓縮指令 C 擴充是 2 byte，但本課純 RV32I，一律 +4。）

## 進階延伸

- **PC 的下一步不總是 +4**：Ch 6 那張圖裡 PC 前面其實有個 mux——分支/跳轉指令會把 pc_next 換成分支目標而非 PC+4。本章只做 +4 這條線，Ch 11 補上分支目標那條線，PC 才算完整。今天的 fetch 是「假設程式一直線往下跑」的簡化版。
- **misaligned fetch 例外**：真實 RISC-V 若 PC 沒對齊到 4（純 RV32I 情況），會觸發 instruction-address-misaligned 例外。本課單週期先不處理例外（留到 Part 5 的 trap 機制），但你要知道真硬體這裡有一道檢查。
- **Harvard vs von Neumann**：我們把 imem（指令）和之後的 dmem（資料）做成兩塊獨立記憶體，這叫 Harvard 架構，教學型 core 常用，因為單週期要同 cycle 既抓指令又存取資料，兩塊分開才不會撞在一起。真實系統多是統一記憶體（von Neumann）+ 分離的 I-cache/D-cache 來達到類似效果，那是 Part 4 的主題。
- **`$readmemh` vs `$readmemb`**：前者讀 16 進位、後者讀 2 進位。我們的 toolchain 流程吐 hex，所以用 `$readmemh`。若檔案格式不符（多空白行、大小寫、位元數不對）會靜默載入錯誤或補 X，這也是「明明程式對卻抓到垃圾」的一個來源，值得檢查 hex 檔格式。

## 本章重點整理

- fetch 由三塊組成：**PC 暫存器**（時序，唯一有記憶）、**PC+4 加法器**（組合）、**imem**（組合讀，`$readmemh` 載入）。
- reset vector 是 CPU 開機的第一個 PC，本課約定 `0x80000000`，用**同步 reset** 設定。
- byte 位址轉 word index 要 `(pc - RESET_PC) >> 2`：先減 base、再除以 4。忘了這步是新手第一大坑，會抓到垃圾且無 error。
- 完整 toolchain 流程：`gcc -march=rv32i ... -Ttext=0x80000000` → `objcopy -O binary` → python 轉每行一 word 的 hex → `$readmemh`。之後每章都用。
- 真跑驗證：PC 從 `0x80000000` 每 cycle +4，抓出的指令逐條對上 objdump，fetch 正確。

## 自我檢核

- [ ] 我能畫出 fetch datapath 的三塊，並說出哪塊是時序、哪些是組合。
- [ ] 我能解釋 reset vector 是什麼、為什麼要用同步 reset、testbench 裡怎麼正確觸發它。
- [ ] 我能手算把 `0x8000000c` 轉成 imem 的 word index（答案：3），並說明減 base、除以 4 兩步各為什麼。
- [ ] 我能複述從 `.S` 到 `.hex` 的完整 toolchain 命令，並解釋每步在做什麼。
- [ ] 我能說明為什麼單週期的 imem 必須是組合讀，而不是同步讀。
- [ ] 我能指出至少兩個「fetch 看起來對其實錯」的坑（位址沒減 base、+1 而非 +4、reset 沒走邊沿），以及各自的症狀。

## 延伸閱讀

- **《Computer Organization and Design, RISC-V Edition》(Patterson & Hennessy) 第 4.3 節開頭**：作者從「PC → instruction memory → PC+4」這個最小片段開始搭 datapath，跟本章順序一致。看它怎麼把 PC 前進的加法器獨立出來、為什麼不共用 ALU。
- **《Digital Design and Computer Architecture, RISC-V Edition》(Harris & Harris) 第 7.3.1 節 "Single-Cycle Datapath"**：一步步加元件的畫法，fetch 那段講得比 P&H 更細，特別是記憶體 port 的組合/同步差異。
- **[Verilator 官方文件](https://verilator.org/guide/latest/) 的 "Language Support" 章節搜 `$readmemh`**：確認 verilator 對 `$readmemh` 的支援細節與檔案格式要求（空白、註解、位元數）。你之後載程式出怪問題，先來這裡查格式。
- **[picorv32 原始碼](https://github.com/YosysHQ/picorv32) 搜 `reg [31:0] reg_pc`**：看一個真 core 的 PC 怎麼管理。它的 fetch 比我們複雜（要處理 memory interface 握手），但 PC+4／reset 的核心概念一致，對照能看出「教學簡化」和「工業現實」的差距。

fetch 讓 CPU 拿到了指令，但指令要操作的資料在暫存器裡。下一章我們做暫存器檔案——CPU 的高速便條紙。

→ [Ch 8 Register File（2R1W）](./08-register-file.md)
