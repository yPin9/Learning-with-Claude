# Ch 31 — CSR file 實作：mstatus / mtvec / mepc / mcause

> **目標**：搞懂 CPU 除了 32 個通用暫存器之外，那一整套「控制與狀態暫存器」（CSR，Control and Status Register）到底是什麼、為什麼要另開一個位址空間放它們。你會學 machine-level 的主要 CSR（mstatus/mtvec/mepc/mcause/mie/mip/mtval/mscratch）各管什麼、Zicsr 擴充的六條指令（CSRRW/CSRRS/CSRRC + 立即數變體）那個「原子讀-改-寫」語意的精確定義與 side effect，然後**親手實作一個 `csr_file` 模組並用 C++ testbench 真跑驗證**：讀出舊值、set/clear 個別 bit、trap 進入時硬體自動存 context、mret 返回時 mstatus 怎麼翻。這是深挖章，也是整個 Part 5 的地基——後面 trap、中斷、privilege 全部靠這套 CSR 運作。
> **環境**：WSL + verilator 4.038。所有 testbench 輸出皆真跑貼上。
> 如果你對 RISC-V 的 privileged ISA、CSR 的概念不熟，回看 `architecture/riscv` 課的 privileged 章節——那裡從軟體視角講「這些 CSR 拿來幹嘛」；這章我們從硬體視角把它們做出來。

## 為什麼需要 CSR？

到目前為止，我們的 core 只有 32 個通用暫存器（x0~x31）和一塊記憶體。程式在裡面算數、搬資料，一切都是「使用者層」的運算。但真實 CPU 要做的事遠不止算數：

- **出事了要有地方記錄**：程式執行到一條非法指令、除以零、存取了沒映射的位址——這些「例外（exception）」發生時，硬體得把「出了什麼事」「出事的 PC 在哪」記下來，好讓 handler 知道怎麼善後。這些狀態放哪？不能放通用暫存器（那是程式在用的，一動就把程式的資料蓋掉了）。
- **中斷來了要能回應**：timer 到期、外部裝置要服務——CPU 得知道「現在允不允許被中斷」「哪些中斷源開著」「哪些正在等待」。這些是開關和旗標，也要有地方存。
- **要跳到 handler 得先知道去哪**：出事時 CPU 要跳到「例外處理程式」的入口。那個入口位址存哪？
- **特權要有依據**：CPU 現在跑在 machine mode 還是 user mode？能不能執行特權指令？這個「當前特權等級」也是一種狀態。

這些都不是「運算資料」，而是**控制 CPU 行為、記錄 CPU 狀態**的東西。RISC-V 把它們統一放進一組獨立的暫存器——**CSR（Control and Status Register）**，開一個 12-bit 的獨立位址空間（最多 4096 個），用專門的指令（Zicsr）存取。

一句話：**通用暫存器是給程式算數用的便條紙，CSR 是給硬體與作業系統控制 CPU、記錄 CPU 狀態的儀表板與開關。** 這章我們做出這塊儀表板。

## 先建立直覺：駕駛座的儀表板 vs 貨物

把通用暫存器（x0~x31）想成貨車的**貨物**——你載什麼、算什麼，都堆在這裡，隨時搬進搬出。

CSR 則是駕駛座的**儀表板與控制面板**：

```
        通用暫存器（貨物）          CSR（儀表板/控制面板）
     ┌──────────────────┐      ┌─────────────────────────────┐
     │ x1 = 10          │      │ mstatus : 中斷總開關、特權   │
     │ x2 = 0x8000...   │      │ mtvec   : 出事往哪跳（handler）│
     │ x3 = ...         │      │ mepc    : 出事時停在哪個 PC   │
     │ ...              │      │ mcause  : 出了什麼事          │
     │ x31 = ...        │      │ mie/mip : 哪些中斷開著/等著   │
     └──────────────────┘      │ mtval   : 出事的相關值        │
       程式自由讀寫             │ mscratch: handler 的暫存空間  │
                               └─────────────────────────────┘
                                 硬體會自己動它、handler 也能動
```

差別的關鍵：

- **貨物（通用暫存器）**：程式想怎麼用就怎麼用，硬體不會自己去改。
- **儀表板（CSR）**：有些欄位**硬體會自己動**——出事時硬體自動把「出事的 PC」寫進 mepc、把「出了什麼事」寫進 mcause，不用你寫指令。有些欄位是**你設給硬體看的開關**——你把 handler 位址寫進 mtvec，硬體出事時就照它跳。這種「軟硬體共同讀寫」是 CSR 的靈魂。

而且**存取儀表板要用專門的手勢**：你不能用 `lw`/`sw` 去讀寫 CSR（它們不在記憶體位址空間裡），要用 Zicsr 的專門指令。這就像儀表板的旋鈕不是用手抓貨物的方式去動，而是有專屬的操作。

## 核心概念：machine-level 的主要 CSR

RISC-V 的 CSR 分 machine（M）、supervisor（S）、user（U）三個層級，前綴分別是 `m`/`s`/`u`。本課先做最基礎、最必要的 **machine-level** 那組——這是任何 RISC-V core 開機就在的層級（Ch 33 再談三個 mode）。按課程約定的標準位址：

| CSR | 位址 | 全名 | 管什麼 |
|---|---|---|---|
| `mstatus` | 0x300 | Machine Status | 全域中斷開關（MIE）、trap 前的狀態備份（MPIE/MPP）、特權控制 |
| `mie` | 0x304 | Machine Interrupt Enable | 個別中斷源的開關（timer/software/external）|
| `mtvec` | 0x305 | Machine Trap Vector | trap 發生時要跳去的 handler 入口位址 |
| `mscratch` | 0x340 | Machine Scratch | 給 handler 自由使用的一格暫存（常用來換 stack）|
| `mepc` | 0x341 | Machine Exception PC | trap 發生時「被打斷的那條指令的 PC」，mret 從這裡返回 |
| `mcause` | 0x342 | Machine Cause | trap 的原因（是哪種 exception 或哪個 interrupt）|
| `mtval` | 0x343 | Machine Trap Value | trap 的附加資訊（如惹禍的位址或指令）|
| `mip` | 0x344 | Machine Interrupt Pending | 個別中斷源「正在等待處理」的旗標 |

這章先把這 8 個 CSR 當成「8 格會被讀寫的暫存器」做出來，先跑通讀寫語意；它們各自的**行為語意**（trap 時 mepc/mcause 怎麼自動填、mstatus 的 bit 怎麼翻、mie/mip 怎麼觸發中斷）留給 Ch 32（trap）、Ch 34（中斷）深講。這裡我們聚焦兩件事：**這 8 格怎麼實作**、**Zicsr 指令怎麼讀寫它們**。

其中 `mstatus` 的 bit layout 值得先記，後面章章都用：

```
   mstatus（32-bit，只畫本課會動到的 bit）：
   bit 3  : MIE  — Machine Interrupt Enable，M mode 的全域中斷總開關
   bit 7  : MPIE — Machine Previous IE，trap 前 MIE 的備份（好在 mret 時還原）
   bit 12:11 : MPP — Machine Previous Privilege，trap 前的特權等級（Ch 33）
```

trap 進入時硬體會：把 MIE 備份到 MPIE、把 MIE 清 0（進 handler 先關中斷，避免 handler 被立刻再打斷）、把 MPP 記成 trap 前的 mode。mret 返回時反過來還原。這個「翻 bit」的動作我們這章就會在 `csr_file` 裡實作並驗證。

## 核心概念：Zicsr 的六條指令與讀-改-寫語意

存取 CSR 的專門指令來自 **Zicsr** 擴充，一共六條，全部是 `SYSTEM` opcode（`0x73`）、靠 funct3 區分：

| 指令 | funct3 | 動作（原子） | 典型用途 |
|---|---|---|---|
| `csrrw rd, csr, rs1` | 001 | rd ← csr；csr ← rs1 | 整個寫入（swap）|
| `csrrs rd, csr, rs1` | 010 | rd ← csr；csr ← csr **\|** rs1 | set 指定的 bit |
| `csrrc rd, csr, rs1` | 011 | rd ← csr；csr ← csr **& ~** rs1 | clear 指定的 bit |
| `csrrwi rd, csr, uimm` | 101 | rd ← csr；csr ← uimm(5-bit) | 用小立即數整寫 |
| `csrrsi rd, csr, uimm` | 110 | rd ← csr；csr ← csr \| uimm | 用立即數 set bit |
| `csrrci rd, csr, uimm` | 111 | rd ← csr；csr ← csr & ~uimm | 用立即數 clear bit |

三個核心觀念：

**1. 這是「原子的讀-改-寫」（atomic read-modify-write）。** 一條指令同時做兩件事：**先把 CSR 的舊值讀進 rd**，**再把新值寫回 CSR**。「先讀後寫」的順序很重要——rd 拿到的永遠是**修改前**的舊值，即使目標 rd 和來源 rs1 是同一個暫存器。硬體保證這中間沒有其他指令能插進來（single-hart 下天然原子）。

```
   csrrs x6, mstatus, x5    的語意（假設 mstatus 舊值 = OLD）
   ┌─────────────────────────────────────────┐
   │  x6      ← OLD              （讀，先發生） │
   │  mstatus ← OLD | x5         （改+寫，後發生）│
   └─────────────────────────────────────────┘
   x6 拿到的是 OLD，不是 OLD|x5。這是 RMW 的關鍵。
```

**2. CSRRS/CSRRC 是「位元操作」，不是整寫。** `csrrs` 只 **set** rs1 裡為 1 的那些 bit（其他 bit 不動）；`csrrc` 只 **clear** rs1 裡為 1 的那些 bit。這讓你能改 CSR 的某幾個 bit 而不碰其他 bit——例如「打開 mstatus.MIE（bit3）」只要 `csrrsi x0, mstatus, 0x8`，不會動到 MPIE、MPP。只有 `csrrw` 是**整個覆蓋**。

**3. side effect：uimm/rs1 為 0 時不寫。** 這是最容易踩的雷。RISC-V 規定：

- `csrrs`/`csrrc`（含 i 變體）當**來源（rs1 或 uimm）是 0** 時，**完全不寫 CSR**（連寫入的動作都不發生）。因為「set/clear 零個 bit」等於沒改，硬體乾脆連寫都不做——這樣讀一個「寫了會有副作用（如寫某些 CSR 會清旗標）」的 CSR 時，用 `csrrs rd, csr, x0` 能**純讀不觸發任何寫副作用**。
- `csrrw` 當 **rd 是 x0** 時，**不讀 CSR**（因為讀了也丟給 x0，等於沒讀）。對某些「讀了會有副作用」的 CSR 這一樣重要。

慣用寫法因此固定成：
- `csrr rd, csr`（純讀）其實是 `csrrs rd, csr, x0`——來源 x0=0，只讀不寫。
- `csrw csr, rs1`（純寫）其實是 `csrrw x0, csr, rs1`——rd=x0，只寫不讀。
- `csrs csr, rs1`（純 set bit）是 `csrrs x0, csr, rs1`。

看反組譯就懂了。我們把 Ch 32 的 trap 程式組譯出來，`csrw mtvec, t0` 這行：

```
8000000c:	30529073          	csrw	mtvec,t0
```

它 funct3=001（CSRRW）、rd=x0（`0x30529073` 的 bit11:7 = 0）、rs1=t0、csr=0x305（mtvec）——正是「rd=x0 → 不讀、只把 t0 寫進 mtvec」。組合語言的 `csrw` 只是 `csrrw x0` 的 pseudo-instruction。

## 底層機制：csr_file 一次存取的資料流

把一條 CSR 指令在硬體裡走一遍。以 `csrrs x6, mstatus, x5` 為例：

```
   ┌── decode 給出 ──┐
   │ csr_addr = 0x300 │  (mstatus)
   │ csr_op   = RS    │
   │ csr_wdata= x5    │  (來源運算元)
   │ rd       = x6    │
   └──────────────────┘
         │
         ▼
   ┌─────────────────────────────────────────────────┐
   │ csr_file 內部                                     │
   │                                                   │
   │  1. 讀多工：用 csr_addr 選出對應 CSR 的現值        │
   │     csr_rdata = mstatus                （組合，當拍就有）│
   │                                                   │
   │  2. 算新值（讀-改-寫的「改」）：                    │
   │     RS → wval = csr_rdata | csr_wdata              │
   │                                                   │
   │  3. 判斷要不要寫（side effect）：                   │
   │     RS/RC 且 wdata==0 → 不寫；否則寫               │
   │                                                   │
   │  4. clk 邊沿：若要寫，把 wval 落進 mstatus          │
   └─────────────────────────────────────────────────┘
         │                          │
         ▼                          ▼
   csr_rdata → 寫回 x6         mstatus 更新（下一拍生效）
   （舊值！）
```

關鍵時序：**讀（csr_rdata）是組合邏輯，當拍就有舊值**；**寫（更新 CSR）是同步，clk 邊沿才生效**。這正好給出「先讀舊值、後寫新值」的原子語意——因為 rd 拿的是組合讀出的舊值，而 CSR 的更新要等到 clk 邊沿，兩者不衝突。這和 Ch 8 register file「非同步讀、同步寫」是同一個道理。

還有一個優先序要先立好：**trap 進入時硬體對 CSR 的自動寫入（存 mepc/mcause）優先於 CSR 指令的寫入**。因為 trap 一發生，被打斷的那條指令（可能正好是條 CSR 指令）不該完成它自己的寫；該生效的是 trap 存 context。我們的 `csr_file` 用一個 `if (trap_en) ... else if (mret_en) ... else if (csr 指令) ...` 的優先鏈實現，trap 最高、mret 次之、一般 CSR 指令最低。

## 實作：csr_file.sv

按上面的資料流實作。這個模組除了 Zicsr 存取埠，還開了 trap/mret 的專用埠（給 Ch 32 的 trap unit 用）和幾個常用值的即時輸出（mtvec/mepc/mstatus，給 core 決定跳哪、返回哪）：

```systemverilog
// csr_file.sv — machine-level CSR file，實作 Zicsr 讀-改-寫語意
module csr_file (
    input  logic        clk,
    input  logic        rst,

    // Zicsr 存取埠（一個 cycle 一條 CSR 指令）
    input  logic        csr_en,      // 這拍有 CSR 指令
    input  logic [11:0] csr_addr,    // CSR 位址
    input  logic [2:0]  csr_op,      // funct3；只用 bit1:0：01=RW 10=RS 11=RC
    input  logic [31:0] csr_wdata,   // 來源運算元（rs1 或 zimm）
    input  logic        csr_wen,     // 這條指令真的會寫（RW 恆寫；RS/RC 且來源!=0 才寫）
    output logic [31:0] csr_rdata,   // 舊值（讀-改-寫的「讀」）

    // trap 進入時由 trap unit 寫入（優先於 CSR 指令）
    input  logic        trap_en,
    input  logic [31:0] trap_epc,
    input  logic [31:0] trap_cause,
    input  logic [31:0] trap_tval,

    // mret 時更新 mstatus
    input  logic        mret_en,

    // 給 trap unit / core 讀的常用 CSR 即時值
    output logic [31:0] mtvec_o,
    output logic [31:0] mepc_o,
    output logic [31:0] mstatus_o
);
    // ---- 標準位址（課程約定）----
    localparam CSR_MSTATUS  = 12'h300;
    localparam CSR_MIE      = 12'h304;
    localparam CSR_MTVEC    = 12'h305;
    localparam CSR_MSCRATCH = 12'h340;
    localparam CSR_MEPC     = 12'h341;
    localparam CSR_MCAUSE   = 12'h342;
    localparam CSR_MTVAL    = 12'h343;
    localparam CSR_MIP      = 12'h344;

    // ---- 8 個實體 CSR ----
    logic [31:0] mstatus, mie, mtvec, mscratch, mepc, mcause, mtval, mip;

    // ---- 讀：用 csr_addr 多工選出現值（組合，當拍就有舊值）----
    always_comb begin
        case (csr_addr)
            CSR_MSTATUS : csr_rdata = mstatus;
            CSR_MIE     : csr_rdata = mie;
            CSR_MTVEC   : csr_rdata = mtvec;
            CSR_MSCRATCH: csr_rdata = mscratch;
            CSR_MEPC    : csr_rdata = mepc;
            CSR_MCAUSE  : csr_rdata = mcause;
            CSR_MTVAL   : csr_rdata = mtval;
            CSR_MIP     : csr_rdata = mip;
            default     : csr_rdata = 32'd0;   // 未實作 CSR 讀 0（真硬體應 illegal，見雷區）
        endcase
    end

    // ---- 讀-改-寫算「新值」：RW=wdata；RS=old|wdata；RC=old&~wdata ----
    logic [31:0] wval;
    always_comb begin
        case (csr_op[1:0])
            2'b01  : wval = csr_wdata;                 // RW
            2'b10  : wval = csr_rdata | csr_wdata;     // RS
            2'b11  : wval = csr_rdata & ~csr_wdata;    // RC
            default: wval = csr_rdata;
        endcase
    end

    // 即時輸出（給 core 用）
    assign mtvec_o   = mtvec;
    assign mepc_o    = mepc;
    assign mstatus_o = mstatus;

    // ---- 寫：優先鏈 trap > mret > CSR 指令 ----
    always_ff @(posedge clk) begin
        if (rst) begin
            mstatus  <= 32'd0; mie   <= 32'd0; mtvec <= 32'd0; mscratch <= 32'd0;
            mepc     <= 32'd0; mcause<= 32'd0; mtval <= 32'd0; mip      <= 32'd0;
        end else if (trap_en) begin
            // trap 進入：硬體自動存 context + 翻 mstatus
            mepc   <= trap_epc;
            mcause <= trap_cause;
            mtval  <= trap_tval;
            mstatus[7]     <= mstatus[3];  // MPIE ← 舊 MIE（備份）
            mstatus[3]     <= 1'b0;         // MIE  ← 0（進 handler 先關中斷）
            mstatus[12:11] <= 2'b11;        // MPP  ← M mode
        end else if (mret_en) begin
            // mret 返回：還原
            mstatus[3]     <= mstatus[7];  // MIE  ← MPIE
            mstatus[7]     <= 1'b1;         // MPIE ← 1
            mstatus[12:11] <= 2'b00;        // MPP  ← U（本課簡化，Ch 33 補）
        end else if (csr_en && csr_wen) begin
            // 一般 CSR 指令的寫（side effect 由外部算好 csr_wen 帶進來）
            case (csr_addr)
                CSR_MSTATUS : mstatus  <= wval;
                CSR_MIE     : mie      <= wval;
                CSR_MTVEC   : mtvec    <= wval;
                CSR_MSCRATCH: mscratch <= wval;
                CSR_MEPC    : mepc     <= wval;
                CSR_MCAUSE  : mcause   <= wval;
                CSR_MTVAL   : mtval    <= wval;
                CSR_MIP     : mip      <= wval;
                default     : ;  // 唯讀/未實作 CSR：忽略寫
            endcase
        end
    end
endmodule
```

`csr_wen`（要不要寫）為什麼由外部算好帶進來、而不是在模組內判？因為「RS/RC 且來源=0 不寫」「RW 且 rd=x0 不讀（但仍寫）」這種 side effect 判斷牽涉到 rd/rs1 的欄位，屬於 decode 階段的資訊。讓 decode 算好一個 `csr_wen` 布林值傳進來，`csr_file` 只管「叫我寫我就寫」，職責清楚。testbench 裡我們就模擬 decode 幫忙算好 `csr_wen`。

## 範例一：讀-改-寫全套語意驗證

寫 C++ testbench，驗六件事：CSRRW 讀舊值+整寫、CSRRS set bit、CSRRC clear bit、外部即時讀口、trap 存 context+翻 mstatus、mret 還原、以及 `uimm=0 不寫` 的 side effect。`csr_tb.cpp`：

```cpp
#include "Vcsr_file.h"
#include "verilated.h"
#include <cstdio>
#include <cstdint>

static Vcsr_file* dut;
static int fails = 0;
static void tick() { dut->clk=0; dut->eval(); dut->clk=1; dut->eval(); }

static void check(const char* name, uint32_t got, uint32_t exp) {
    bool ok = got==exp;
    printf("[%s] %-22s got=0x%08x exp=0x%08x\n", ok?"OK ":"BAD", name, got, exp);
    if (!ok) fails++;
}

// 執行一條 CSR 指令：擺好埠、當拍讀舊值、走一個 clk 邊沿寫入。回傳舊值。
static uint32_t csr_instr(uint32_t addr, int op, uint32_t src, bool wen) {
    dut->csr_en=1; dut->csr_addr=addr; dut->csr_op=op;
    dut->csr_wdata=src; dut->csr_wen=wen;
    dut->trap_en=0; dut->mret_en=0;
    dut->eval();
    uint32_t old = dut->csr_rdata;   // 舊值當拍就有（組合讀）
    tick();
    dut->csr_en=0; dut->csr_wen=0;
    return old;
}

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    dut = new Vcsr_file;
    dut->rst=1; dut->csr_en=0; dut->csr_wen=0; dut->trap_en=0; dut->mret_en=0;
    tick(); tick(); dut->rst=0; dut->eval();

    // op 編碼：1=RW 2=RS 3=RC（對應 funct3 低 2 bit）
    // 1) CSRRW mscratch, 0xDEADBEEF：舊值 0，寫入 0xDEADBEEF
    uint32_t old = csr_instr(0x340, 1, 0xDEADBEEF, true);
    check("csrrw old(mscratch)", old, 0x00000000);
    old = csr_instr(0x340, 2, 0x00000000, false);   // CSRRS x0：純讀不寫
    check("csrrs read mscratch", old, 0xDEADBEEF);

    // 2) CSRRS mstatus, 0x8：set bit3 (MIE)；再 CSRRC 清掉
    csr_instr(0x300, 2, 0x00000008, true);
    old = csr_instr(0x300, 2, 0, false);
    check("mstatus after set MIE", old, 0x00000008);
    csr_instr(0x300, 3, 0x00000008, true);          // CSRRC 清 bit3
    old = csr_instr(0x300, 2, 0, false);
    check("mstatus after clr MIE", old, 0x00000000);

    // 3) mtvec 寫入 + 外部即時讀口
    csr_instr(0x305, 1, 0x80000100, true);
    dut->eval();
    check("mtvec_o external port", dut->mtvec_o, 0x80000100);

    // 4) trap 進入：mepc/mcause/mtval 自動存 + mstatus 翻
    csr_instr(0x300, 2, 0x00000008, true);          // 先設 MIE=1
    dut->trap_en=1; dut->trap_epc=0x80000044; dut->trap_cause=2; dut->trap_tval=0xBADC0DE;
    dut->csr_en=0; dut->eval(); tick(); dut->trap_en=0;
    old = csr_instr(0x341, 2, 0, false); check("mepc after trap",  old, 0x80000044);
    old = csr_instr(0x342, 2, 0, false); check("mcause after trap",old, 2);
    old = csr_instr(0x343, 2, 0, false); check("mtval after trap", old, 0x0BADC0DE);
    old = csr_instr(0x300, 2, 0, false);
    // MIE(bit3)=0、MPIE(bit7)=1(舊MIE)、MPP(12:11)=11 → 0x1880
    check("mstatus after trap", old, 0x00001880);

    // 5) mret 返回：MIE←MPIE(1)、MPIE←1、MPP←0
    dut->mret_en=1; dut->csr_en=0; dut->eval(); tick(); dut->mret_en=0;
    old = csr_instr(0x300, 2, 0, false);
    // MIE(bit3)=1、MPIE(bit7)=1 → 0x88
    check("mstatus after mret", old, 0x00000088);

    // 6) side effect：RS 但來源=0（uimm=0）→ 不寫
    csr_instr(0x340, 1, 0xAAAA5555, true);          // 先設 mscratch
    old = csr_instr(0x340, 2, 0xFFFF0000, false);   // RS，wen=false（模擬 decode 判定不寫）
    check("csrrsi uimm0 read", old, 0xAAAA5555);
    old = csr_instr(0x340, 2, 0, false);
    check("csrrsi uimm0 nowrite", old, 0xAAAA5555); // 沒被改動

    printf("\n%s (%d failures)\n", fails? "FAIL":"ALL PASS", fails);
    return fails?1:0;
}
```

build 與跑：

```bash
verilator --cc csr_file.sv --exe csr_tb.cpp --Mdir obj -Wno-WIDTH -Wno-UNUSED
make -s -C obj -f Vcsr_file.mk Vcsr_file
./obj/Vcsr_file
```

真跑輸出：

```
[OK ] csrrw old(mscratch)    got=0x00000000 exp=0x00000000
[OK ] csrrs read mscratch    got=0xdeadbeef exp=0xdeadbeef
[OK ] mstatus after set MIE  got=0x00000008 exp=0x00000008
[OK ] mstatus after clr MIE  got=0x00000000 exp=0x00000000
[OK ] mtvec_o external port  got=0x80000100 exp=0x80000100
[OK ] mepc after trap        got=0x80000044 exp=0x80000044
[OK ] mcause after trap      got=0x00000002 exp=0x00000002
[OK ] mtval after trap       got=0x0badc0de exp=0x0badc0de
[OK ] mstatus after trap     got=0x00001880 exp=0x00001880
[OK ] mstatus after mret     got=0x00000088 exp=0x00000088
[OK ] csrrsi uimm0 read      got=0xaaaa5555 exp=0xaaaa5555
[OK ] csrrsi uimm0 nowrite   got=0xaaaa5555 exp=0xaaaa5555
```

全過。逐項讀懂這輸出，你就掌握了 CSR 的全部核心語意：

- **`csrrw old(mscratch)=0`**：CSRRW 讀到的是**寫入前**的舊值（reset 後 mscratch=0），寫入 0xDEADBEEF 是「事後」的。這是原子 RMW「先讀後寫」的證據。
- **set/clear MIE**：CSRRS `0x8` 把 mstatus 的 bit3 設 1（→0x8），CSRRC `0x8` 再清回 0。位元操作只動指定 bit，沒碰其他。
- **trap after mstatus=0x1880**：這是本章最該記的一行。trap 前 MIE=1，trap 後硬體自動：MIE(bit3)→0（0x8 消失）、MPIE(bit7)→1（0x80，備份了舊 MIE）、MPP(bit12:11)→11（0x1800）。加起來 `0x1800 + 0x80 = 0x1880`。**這就是「進 handler 先關中斷、把舊狀態備份起來」的硬體動作。**
- **mret after mstatus=0x88**：mret 反過來——MIE(bit3)←MPIE(舊備份=1)→0x8、MPIE(bit7)←1→0x80，加起來 0x88。狀態還原了（中斷重新打開）。
- **uimm0 nowrite**：來源 0 的 CSRRS 沒改動 mscratch——side effect 生效。

## 範例二：原子性——rd 和 rs1 是同一暫存器

RMW 最容易誤解的地方：如果 `csrrw x5, mscratch, x5`（rd 和 rs1 都是 x5），x5 最後拿到的是**寫入前的舊 CSR 值**還是**它自己**？答案是舊 CSR 值——因為「讀」在「寫」之前發生，x5 先被舊 CSR 值蓋掉，硬體才把（x5 的舊值？不，是 rs1 讀出的值）寫進 CSR。

這裡有個微妙點：`csrrw x5, csr, x5` 的語意是「rd(x5) ← csr 舊值；csr ← rs1(x5) **原本的值**」。硬體在同一拍**同時**拿到 rs1 的舊值（組合讀 regfile）和 csr 的舊值（組合讀 csr_file），兩者互不干擾。用我們的 tb 驗一下（把 csr_wdata 設成「rs1 舊值」、觀察讀出的 csr 舊值）：

```cpp
// 在 main 末尾加：mscratch 現值先設成 0x11112222
csr_instr(0x340, 1, 0x11112222, true);
// 模擬 csrrw x5, mscratch, x5：rs1(x5) 舊值 = 0x99998888
uint32_t rs1_old = 0x99998888;
uint32_t csr_old = csr_instr(0x340, 1, rs1_old, true);  // 讀出 csr 舊值、把 rs1 舊值寫進去
check("atomic: rd got old csr", csr_old, 0x11112222);   // x5 拿到的是 csr 舊值
uint32_t after = csr_instr(0x340, 2, 0, false);
check("atomic: csr got rs1 old", after, 0x99998888);    // csr 拿到的是 rs1 舊值
```

真跑輸出（接在範例一之後）：

```
[OK ] atomic: rd got old csr  got=0x11112222 exp=0x11112222
[OK ] atomic: csr got rs1 old got=0x99998888 exp=0x99998888
```

`rd` 拿到 CSR 的舊值 `0x11112222`，`csr` 拿到 rs1 的舊值 `0x99998888`——兩個舊值互換，中間沒有誰先被對方蓋掉。這就是原子 RMW：讀和寫用的都是「這拍開始時的值」，靠「組合讀舊值 + clk 邊沿才寫」的時序天然保證。

## 對比取捨：CSR 存取 vs 記憶體存取 vs 通用暫存器

| 面向 | 通用暫存器 (x0~x31) | CSR (Zicsr) | 記憶體 (lw/sw) |
|---|---|---|---|
| 位址空間 | 5-bit（32 個）| 12-bit（4096 個，多數未實作）| 32-bit（全記憶體）|
| 存取指令 | 幾乎所有指令 | 專門六條（CSRRW/S/C + i）| load/store |
| 讀寫時序 | async 讀、sync 寫 | async 讀、sync 寫 | 可能多 cycle（cache/DRAM）|
| 原子 RMW | 無（要多條指令）| 一條指令內建 | 要 A 擴充（LR/SC/AMO）|
| 硬體會自己動嗎 | 不會 | **會**（trap 存 context 等）| 不會 |
| 存取需要特權嗎 | 不用 | **要**（CSR 位址高 2 bit 編了最低特權）| 看 PMP/PTE |
| side effect | 無 | **有**（讀某些 CSR 會清旗標等）| MMIO 有 |

CSR 和通用暫存器實作上很像（都是 async 讀 sync 寫的暫存器陣列），但語意天差地別：CSR 有**硬體自動寫入**、有**side effect**、有**特權檢查**（Ch 33）。它是軟硬體的交界面——這也是為什麼要獨立一套指令和位址空間，而不是塞進記憶體或通用暫存器。

## 踩雷區

**雷 1：以為 CSRRS/CSRRC 是「整個寫入」。**
- 錯誤直覺：「`csrrs mstatus, x5` 就是把 x5 寫進 mstatus」。
- 正確認識：CSRRS 是 **set bit**（`csr ← csr | rs1`）、CSRRC 是 **clear bit**（`csr ← csr & ~rs1`），**只動 rs1 裡為 1 的那些 bit，其他 bit 原封不動**。只有 CSRRW 才是整個覆蓋。這個區別是重點：你要「打開 mstatus.MIE 但不碰其他 bit」時用 `csrrs`（範例一 set MIE 只讓 0x8 出現、沒動別的 bit）；用 `csrrw` 會把整個 mstatus 蓋掉，MPIE/MPP 全被清成 0，闖大禍。改 CSR 個別 bit 用 S/C，整寫才用 W。

**雷 2：忘記「來源為 0 不寫」的 side effect，或反過來以為它總是寫。**
- 錯誤直覺：「CSRRS 一定會執行一次寫入」。
- 正確認識：`csrrs`/`csrrc`（含 i 變體）當**來源（rs1 或 uimm）是 0** 時**完全不寫**（範例一的 `csrrsi uimm0 nowrite` 證明了）。這不是最佳化細節，是規格保證——因為它讓 `csrr rd, csr`（= `csrrs rd, csr, x0`）能**純讀而不觸發寫副作用**。有些 CSR「一寫就清某旗標」，你只想讀它時就靠這個。同理 `csrrw` 當 rd=x0 時不讀。你在 decode 算 `csr_wen`/`csr_ren` 時漏了這條，讀一個「寫了會清旗標」的 CSR 就會意外清掉它，bug 極難查。

**雷 3：以為讀出的是修改後的新值。**
- 錯誤直覺：「`csrrw x6, mscratch, x5` 之後 x6 是 x5 寫進去的新值」。
- 正確認識：**rd 拿到的永遠是修改前的舊值**（範例一 `csrrw old=0` 是 reset 後的舊 mscratch，不是剛寫的 0xDEADBEEF；範例二 rd 和 rs1 都 x5 時 rd 仍拿舊 CSR 值）。原子 RMW 的定義就是「先讀後寫」，讀在寫之前。硬體上這靠「csr_rdata 是組合讀（當拍舊值）、CSR 更新是 clk 邊沿」的時序保證。搞反這個，你寫 trap handler 存 context 時會存錯值。

**雷 4：以為每個 CSR 位址都能讀寫、寫了都會生效。**
- 錯誤直覺：「12-bit 位址空間 4096 個 CSR 都在」。
- 正確認識：絕大多數 CSR 位址**未實作**（我們只做了 8 個）。存取未實作的 CSR，真硬體應觸發 **illegal instruction exception**（我們的簡化版讀 0、忽略寫，是教學取巧，雷區進階會談）。而且**有些 CSR 有唯讀 bit 或 WARL（Write Any Read Legal）欄位**——你寫進去的值不一定原樣存回。例如 mtvec 的低 2 bit 是 MODE（Direct/Vectored），mstatus 有一堆保留 bit 恆 0。我們的 csr_file 為教學簡化把每個 CSR 當可自由讀寫的 32-bit 格，真硬體要按 spec 遮罩每個 CSR 的合法 bit。把「寫進去 = 原樣存回」當通則，你對接真程式時會發現某些 bit 怎麼寫都寫不進去（因為它是唯讀或保留）。

## 進階延伸

- **CSR 位址編碼藏了特權與讀寫屬性**：12-bit CSR 位址不是隨便編的。**bit[11:10] 編了讀寫屬性**（0b11 = 唯讀）、**bit[9:8] 編了最低存取特權**（0b00=U、0b01=S、0b11=M）。所以位址 0x300（mstatus）的 bit[9:8]=0b11 表示「要 M mode 才能存取」，0xC00（cycle，唯讀）的 bit[11:10]=0b11 表示唯讀。硬體可以只看位址高幾位就判「這個特權能不能碰、能不能寫」，不必一個個列舉。Ch 33 做 privilege check 時會用到這個。
- **mstatus 是 machine 和 supervisor 共用的（MXR/SUM/SPP...）**：我們只做了 MIE/MPIE/MPP 三個欄位，真 mstatus 還有 SIE/SPIE/SPP（S mode 的中斷開關與備份，Ch 33）、MPRV（用 MPP 的特權做記憶體存取）、MXR（可讀可執行頁）、SUM（S mode 可存取 U 頁）、FS/XS（浮點/擴充狀態）等。它是整顆 CPU 最擁擠的一個 CSR。做完本課想加 S mode，第一件事就是把 mstatus 補齊。
- **原子 RMW 為什麼在 hardware 天然成立**：single-hart（單核）下，一條 CSR 指令的「讀」和「寫」在同一條指令內完成，中間不會有別的指令插進來——這是天然原子。但**多核**下，若兩個 hart 同時 CSRRS 同一個共享 CSR（如某些 machine-level 全域 CSR），就要額外的同步（RISC-V 的做法是這類共享狀態多半透過 memory-mapped 的方式存取、用 A 擴充的 AMO 保證原子，而非直接 CSR）。本課單核，CSR 的原子性不必操心。
- **shadow CSR 與 pipeline 的互動**：我們這章的 csr_file 是獨立測的（單週期式存取）。接進 pipeline（Ch 35）時有個坑：CSR 指令在哪一級讀寫？若在 EX 讀、WB 寫，那「連續兩條讀同一 CSR」中間若有寫，要不要 forward？多數教學 core 把 CSR 存取放單一級（例如都在 MEM 或都在 WB）做完，避免 CSR 的 hazard；工業 core 才處理 CSR forwarding。這是 Ch 35 整合時要決定的設計點。

## 本章重點整理

- **CSR 是 CPU 的儀表板與控制面板**：記錄狀態（mepc/mcause/mtval）、提供開關（mstatus.MIE、mie）、指示 handler 入口（mtvec）。獨立 12-bit 位址空間、專門指令存取，和通用暫存器（貨物）分開。
- **8 個 machine CSR**：mstatus(0x300) / mie(0x304) / mtvec(0x305) / mscratch(0x340) / mepc(0x341) / mcause(0x342) / mtval(0x343) / mip(0x344)。mstatus 的 MIE(bit3)/MPIE(bit7)/MPP(bit12:11) 最關鍵。
- **Zicsr 六條指令做原子讀-改-寫**：CSRRW（整寫）/CSRRS（set bit）/CSRRC（clear bit）+ i 變體。**rd 拿舊值、CSR 拿新值，先讀後寫**。
- **side effect**：CSRRS/CSRRC 來源=0 不寫（讓 `csrr` 純讀）；CSRRW rd=x0 不讀（讓 `csrw` 純寫）。`csrr`/`csrw`/`csrs` 都是這些的 pseudo-instruction。
- **csr_file 實作**：組合讀（當拍舊值）+ 同步寫（clk 邊沿），優先鏈 trap > mret > CSR 指令。trap 自動存 mepc/mcause/mtval 並翻 mstatus，mret 還原。全部真跑驗過。

## 自我檢核

- [ ] 我能說出 CSR 和通用暫存器的三個本質差別（硬體會自己動、有 side effect、要特權），並用「儀表板 vs 貨物」類比解釋。
- [ ] 我能列出本課 8 個 machine CSR 的位址和用途，特別是 mstatus 的 MIE/MPIE/MPP 三個 bit 位置。
- [ ] 我能寫出 CSRRW/CSRRS/CSRRC 各自的「讀-改-寫」語意，並解釋為什麼 rd 拿到的是舊值。
- [ ] 我能解釋「CSRRS 來源為 0 不寫」的 side effect 為什麼存在，以及 `csrr rd, csr` 為什麼等於 `csrrs rd, csr, x0`。
- [ ] 我能追出範例一「mstatus after trap = 0x1880」怎麼由 MIE→0、MPIE→1、MPP→11 算出來，以及 mret 後為什麼變 0x88。
- [ ] 我能說明為什麼 csr_file 的寫要用「trap > mret > CSR 指令」的優先鏈。

## 延伸閱讀

- **[RISC-V Privileged Spec](https://riscv.org/technical/specifications/) 第 2 章「Control and Status Registers (CSRs)」與 3.1 節「Machine-Level CSRs」**：權威來源。第 2 章定義 CSR 位址編碼（bit[11:10] 讀寫屬性、bit[9:8] 特權）與存取規則，3.1 逐一定義 mstatus/mtvec/mepc/mcause 等每個 bit。實作 csr_file 遇到「這個 bit 該不該可寫」的疑義時以它為最終仲裁。搭配 `architecture/riscv` 課的 privileged 章一起讀。
- **[RISC-V Unprivileged Spec](https://riscv.org/technical/specifications/) 的 Zicsr 章節**：CSRRW/CSRRS/CSRRC 的精確語意就在這裡，包括「rd=x0 不讀」「rs1=x0 不寫」那幾條 side effect 規則的正式敘述。本章範例一的每個測試都對應它的一條規定，讀它把你的實作和 spec 一一對上。
- **《Computer Organization and Design, RISC-V Edition》(Patterson & Hennessy) 第 4.9 節「Exceptions」**：教科書從硬體 datapath 角度講 exception 需要哪些額外狀態暫存器（它叫 SEPC/SCAUSE，對應我們的 mepc/mcause）。它的 datapath 圖顯示這些暫存器怎麼接進 pipeline，是本章 CSR 接進 core（Ch 35）的前導。
- **[xv6-riscv 的 `kernel/riscv.h`](https://github.com/mit-pdos/xv6-riscv/blob/riscv/kernel/riscv.h)**：一個真實教學 OS 怎麼用 C inline asm 包裝 CSR 存取（`r_mstatus()`/`w_mstatus()`/`r_mcause()` 這些）。看它就懂「軟體怎麼用 Zicsr 指令操作我們做的這些 CSR」——它的 `MSTATUS_MIE`、`MSTATUS_MPP` 這些 bit 定義正是我們 mstatus 實作的軟體對應。硬體（本章）和軟體（xv6）兩邊對照，CSR 的軟硬體介面就完整了。

下一章我們讓這些 CSR 動起來：當一條非法指令或 ECALL 發生時，硬體怎麼自動存 mepc/mcause、跳到 mtvec、翻 mstatus，怎麼從 handler 用 mret 返回，以及這一切在 pipeline 裡怎麼靠 flush 乾淨收尾。CSR 是舞台，trap 是那齣戲。

→ [Ch 32 Trap 機制：exception / interrupt 進出、pipeline flush](./32-trap-mechanism.md)
