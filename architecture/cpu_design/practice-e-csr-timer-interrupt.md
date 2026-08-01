# 練習 E — CSR + CLINT timer interrupt：讓 core 週期性進出 trap handler

> **目標**：把 Part 5（Ch 31~35）學的東西親手做一遍——在帶 CSR / trap 的 core 上，配好 CLINT timer，寫一支**會存 context、重排鬧鐘、mret** 的 timer interrupt handler，讓 core **週期性地進出 trap handler**：每 N 個 tick 被 timer 打斷一次、handler 累加計數再返回，主程式從斷點繼續。你要親眼看到那個穩定的週期，並用實驗驗證三件事：中斷真的週期性發生、主程式的活資料沒被 handler 弄壞、關掉全域中斷（MIE）後 timer 就進不來。這是 Part 5 的動手驗收，把「讀懂」變成「做過」。
> **環境**：WSL + verilator 4.038 + riscv64-unknown-elf-gcc 10.2.0。用 Ch 32~35 共用的 `core_trap.sv`（帶內建 CLINT 與教學 CSR 存取口），你只寫 `.S` handler 和 C++ testbench。所有參考輸出皆真跑貼上。
> **前置**：務必先讀完 Ch 31（csr_file）、Ch 34（CLINT/中斷三開關）、Ch 35（整合 + handler 頭尾骨架）。這練習就是把 Ch 35 範例一/二自己重做一遍加變化。

---

## 任務規格

你要交出三個檔：一支 timer handler 組語（`pe.S`）、一個每拍觀測的 testbench（`pe_tb.cpp`）、以及跑出來的三組驗證輸出。功能規格如下：

**主程式（開機在 M mode）要做的 setup（順序重要）**：
1. 設 `mtvec` = 你的 handler 入口。
2. 設 `mscratch` = 一塊 handler 專用的 stack top（例如 `0x80001000`）。
3. 設第一次鬧鐘：`mtimecmp = 某個初值`（用教學 CSR `0x7C0` 寫低 32 bit、`0x7C1` 寫高 32 bit）。
4. 開 timer 中斷源：`mie.MTIE`（bit 7）= 1。
5. 把「handler 進入次數」計數器暫存器歸零、「主程式工作進度」計數器歸零。
6. **最後**才開全域中斷：`mstatus.MIE`（bit 3）= 1。
7. 進一個 work loop：一直累加「工作進度」暫存器（模擬主程式做事），等 timer 打斷。

**handler（timer interrupt）要做的事**：
1. 用 `csrrw sp, mscratch, sp` 換到 handler stack、把你要用到的暫存器存進 stack（存 context）。
2. 「handler 進入次數」計數器 +1。
3. 讀當前 `mtime`（教學 CSR `0x7C2`）、算 `mtime + N`、寫回 `mtimecmp`（`0x7C0`）——**重排下一次鬧鐘、清 MTIP**。
4. 還原剛才存的暫存器、換回主程式 sp。
5. `mret` 返回（**interrupt 不動 mepc**）。

**你要跑出並解讀的三組結果**：
- **測試 1（週期性）**：跑 ~130 拍，看 handler 週期性進入至少 3 次，且主程式工作進度在中斷之間持續前進（斷點續跑）。
- **測試 2（context 完整）**：主程式在某個暫存器放一個「魔術值」（如 `0x5555`），跑完檢查它**沒被 handler 弄壞**。
- **測試 3（MIE 總開關）**：把 setup 第 6 步（開 mstatus.MIE）拿掉，其他不變，驗證 timer 明明到期但**中斷完全不進**（handler 次數 = 0，主程式一路跑）。

**約定沿用全課**：RV32I、reset PC=0x80000000、CSR 位址 mstatus 0x300 / mie 0x304 / mtvec 0x305 / mscratch 0x340；教學 CSR：讀 mtime `0x7C2`、寫 mtimecmp 低/高 `0x7C0`/`0x7C1`。timer 中斷 mcause = `0x80000007`。

---

## 先建立直覺：這練習在驗什麼

Ch 35 已經把整套跑給你看了。這練習要你自己搭一遍，重點在**親手體會三個「缺一不可」**：

```
   主程式做事 ─────────────────────────────────▶ 時間
      x11: 1  2  3  4  5 │        │ 6  7  8 │        │ 9 ...
                         │handler │         │handler │
                         │(x10++) │         │(x10++) │
                         ▼        ▲         ▼        ▲
                     timer 響   mret     timer 響  mret
                     (mtime 追上 mtimecmp)  (重排後的鬧鐘又到)

   要讓這圖成立，三件事缺一不可：
   1. 三開關全開：MIE(總) & MTIE(源) & MTIP(pending)  → 中斷才進（測試 3 驗）
   2. handler 重排 mtimecmp                          → 不然中斷風暴，主程式停擺
   3. handler 存/還原 context                        → 不然主程式活資料被弄壞（測試 2 驗）
```

你會踩到的坑，多半就是這三條之一沒做對：忘了開某個開關（中斷不進）、忘了重排鬧鐘（中斷風暴）、忘了存暫存器（資料被弄壞）。這練習用三個測試把它們一一逼出來。

---

## 你要用的 core 介面（不用改 SV，只用它）

這練習**不需要你改 `core_trap.sv`**——它（Ch 32~35 共用）已經把 CSR file、trap 邏輯、privilege check、內建 CLINT 全做好了。你只寫 `.S` 和 C++ testbench。先把這顆 core 對外的介面看清楚，省得反覆猜。

**debug 觀測埠**（testbench 用來看內部狀態，不影響行為）：

| 埠 | 型別 | 讀到什麼 |
|---|---|---|
| `dbg_reg_sel` / `dbg_reg_data` | 輸入 5-bit / 輸出 32-bit | 設 sel=i，data 給出 x[i]（x0 恆 0）|
| `dbg_pc` | 32-bit | 當前 PC（這拍要執行的指令位址）|
| `dbg_mcause` | 32-bit | 最近一次 trap 的 mcause |
| `dbg_mepc` | 32-bit | 當前 mepc |
| `dbg_priv` | 2-bit | 當前特權（3=M、0=U）|
| `dbg_mtime` | 32-bit | 內建 CLINT 的 mtime 低 32 bit |

**你會用到的 CSR**（標準 + 教學橋接）：

| CSR | 位址 | 這練習拿來做什麼 |
|---|---|---|
| `mstatus` | 0x300 | bit3=MIE 全域中斷總開關（setup 最後一步開）|
| `mie` | 0x304 | bit7=MTIE，開 timer 中斷源 |
| `mtvec` | 0x305 | handler 入口位址 |
| `mscratch` | 0x340 | 放 handler 專用 stack top，換 sp 用 |
| `mepc` | 0x341 | 被打斷點——timer handler **不要動它** |
| 讀 mtime | 0x7C2 | `csrr` 讀當前 mtime，好算 mtime+N（教學橋接，真硬體是 memory-mapped）|
| 寫 mtimecmp 低 | 0x7C0 | `csrw` 設下一次鬧鐘低 32 bit |
| 寫 mtimecmp 高 | 0x7C1 | `csrw` 設高 32 bit（本課用 0）|

`0x7C0`/`0x7C1`/`0x7C2` 是教學取巧——真晶片的 CLINT 是掛在記憶體位址空間（`0x0200_0000` 一帶），handler 用 `lw`/`sw` 存取，語意一模一樣（讀 mtime、寫 mtimecmp），只是走 bus 不走 CSR。這裡用 CSR 是為了不牽進整套 bus，聚焦中斷本身。

**core 內部中斷觸發條件**（你要滿足的，就是這一行）：

```systemverilog
assign interrupt_taken = mstatus[3] && mie[7] && (mtime >= mtimecmp);
//                       └ MIE(總) ┘  └ MTIE(源)┘  └─── MTIP(pending) ───┘
```

三者相與，缺一不進——這就是上面「三開關全開」的硬體真身。測試 3 就是把第一項打成 0，驗證它一票否決。

---

## 分段實作建議

別想一次寫完。分五步，每步都能單獨驗證，錯了好定位。

### 第 1 步：確認 core 和工具鏈能跑

先不寫 handler，寫一支最簡單的程式（只累加一個暫存器），確認 `core_trap.sv` 能被 verilator 編、能跑、hex 灌得進去。這一步是驗環境，不是驗功能。

```asm
_start:
    li   x11, 0
loop:
    addi x11, x11, 1
    beq  x0, x0, loop
```

編譯灌進 core 跑幾拍，看 x11 有沒有在漲。跑得動，環境就 OK，進第 2 步。（`core_trap.sv` 在 `.scratch_p5/`，或用 Ch 35 的版本——它已含內建 CLINT 和教學 CSR 讀寫口。）

### 第 2 步：setup + 一個「什麼都不做就 mret」的 handler

先讓中斷能進、能返回，handler 裡先只放最少的東西（計數 + 重排 + mret，**還不存 context**）。這一步驗「中斷進得來、mret 回得去、週期成立」。setup 記得**順序**：mtvec → mtimecmp → mie → 最後 mstatus.MIE。handler 先用範例一那種「直接用暫存器」的偷懶寫法，把週期跑出來再說。

驗證點：handler 進入次數（你的計數器）會週期性地 +1、主程式工作進度在中斷之間前進。這步過了，代表三開關和重排鬧鐘都對。

### 第 3 步：加上 context 存/還原

把 handler 改成「真 handler」——開頭 `csrrw sp, mscratch, sp` 換 stack、`sw` 存你要用的暫存器，結尾 `lw` 還原、`csrrw` 換回。記得 setup 要先 `csrw mscratch, stack_top`。這步驗「handler 對主程式透明」——加測試 2（魔術值）。

### 第 4 步：寫每拍觀測的 testbench

C++ testbench 每拍印狀態，或只在「handler 次數變了」那拍印（看週期更清楚）。用 `dbg_reg_sel`/`dbg_reg_data` 讀暫存器、`dbg_mtime`/`dbg_mcause`/`dbg_pc` 讀狀態。

### 第 5 步：跑三組測試、解讀

跑測試 1/2/3，對照下面參考輸出。特別是測試 3（拿掉開 MIE）——你會看到 timer 明明到期，但 handler 一次都沒進。這是 Part 5 最該內化的一課：**中斷是「被允許才發生」的，不是訊號一來就進**。

---

## 卡點提示

<details>
<summary>提示 1：handler 進了一次之後，主程式就再也跑不動了（幾乎每拍都在 handler 裡）</summary>

你多半是**沒重排 mtimecmp**，或**重排的 N 太小**。timer 中斷來源是 `mtime >= mtimecmp`：如果 handler 不把 mtimecmp 往後推，mret 出去時 MTIP 還是高（mtime 仍 >= 舊 mtimecmp），下一拍立刻又進 handler——中斷風暴。

就算你有重排，**N 必須大於 handler 自己的執行長度**。你的 handler 有十幾條指令，跑完 mtime 已經漲了十幾。若 N=5，`mtimecmp = mtime + 5` 在 handler 還沒跑完就又被 mtime 追上，mret 出去馬上再觸發。實測 N=5 時 handler 狂進、主程式工作進度卡死不動（見下面「延伸挑戰/踩坑實測」）。把 N 設成 30 這種明顯大於 handler 長度的值就乾淨了。
</details>

<details>
<summary>提示 2：中斷一次都不進，handler 次數永遠 0</summary>

檢查**三個開關是不是都開了**：`mstatus.MIE`（bit3，全域）、`mie.MTIE`（bit7，timer 源）、`mtimecmp` 有沒有設成一個 mtime 追得到的值（別忘了 reset 後 mtimecmp 是全 1、永不到期，你要主動寫小）。三者任一沒到位，`interrupt_taken = MIE & MTIE & (mtime>=mtimecmp)` 就是 0。

最常見是漏了開 `mstatus.MIE`（那正是測試 3 故意驗的），或 mtimecmp 只寫了低 32 bit、高 32 bit 忘了清 0（reset 是全 1，高位沒清的話 64-bit 值仍是天文數字，mtime 追不到）。記得 `csrw 0x7C1, x0` 清高位。
</details>

<details>
<summary>提示 3：主程式的某個暫存器莫名其妙變了值 / 資料被弄壞</summary>

你的 handler **借用了某個暫存器卻沒先存起來**。handler 打斷的是主程式，主程式每個暫存器都可能有活資料。你在 handler 裡 `csrr t4, 0x7C2` 用了 t4——如果主程式也在用 t4，它的值就被你蓋了。解法：handler 開頭把要用的暫存器（t4/t5 等）`sw` 進 stack、結尾 `lw` 還原。

而且**連 sp 都是主程式的活資料**，你不能直接拿 sp 當 handler stack。所以要先 `csrrw sp, mscratch, sp`——把 sp 換成 mscratch 裡預存的 handler stack，同時把主程式 sp 藏進 mscratch；handler 結尾再換回。這是 Ch 35 範例二的頭尾骨架，也是這個坑的標準解。
</details>

<details>
<summary>提示 4：verilator 編不過，或 hex 灌不進去</summary>

- verilator 4.038 較舊：確認 `core_trap.sv` 用的是 `always_ff`/`always_comb`/`logic`/`$readmemh`，沒有 SV interface/program block。
- 灌 hex：`riscv64-unknown-elf-objcopy -O binary --only-section=.text x.elf x.bin` 再 `od -An -tx4 -w4 -v x.bin | sed 's/ //g' > prog.hex`，然後 verilator 帶 `-GINIT_FILE='"prog.hex"'`（注意那層引號，要傳給 SV 一個字串常數）。
- 編譯記得 `-Wno-WIDTH -Wno-UNUSED -Wno-UNOPTFLAT`，否則 warning 可能被當 error 擋下。
</details>

<details>
<summary>提示 5：handler 返回後主程式從錯誤的地方繼續（跳過了一條指令）</summary>

你多半在 handler 裡對 mepc 做了 `+4`。**timer interrupt 絕不能動 mepc**——中斷的 mepc 存的是「被打斷、還沒執行的指令」，回去要接著跑。+4 會跳過一條合法指令。只有 exception（ECALL/illegal）才可能要 +4。判準：mcause bit31=1（中斷）就別碰 mepc。你的 timer handler 應該完全不出現 `csrw mepc, ...`。
</details>

---

## 參考解

先自己做到能跑再看。這是一個完整通過三組測試的版本。

<details>
<summary>完整參考解（handler 組語 pe.S + testbench pe_tb.cpp + 三組測試 + 真跑輸出）</summary>

### handler 組語 `pe.S`（測試 1/2 共用：週期性 + context）

```asm
    .section .text
    .globl _start
_start:
    la    t0, timer_handler
    csrw  mtvec, t0            # 1. 先設 handler 入口

    li    t1, 0x80001000
    csrw  mscratch, t1         # 2. handler 專用 stack top

    li    t1, 25
    csrw  0x7C0, t1            # 3. 第一次鬧鐘 mtimecmp[31:0]=25
    csrw  0x7C1, x0           #    高 32 bit 清 0（別讓 reset 的全 1 留著）

    li    t2, 0x80            # bit7 = MTIE
    csrw  mie, t2             # 4. 開 timer 中斷源
    li    x10, 0             # handler 進入次數（歸零）
    li    x11, 0             # 主程式工作進度（歸零）
    li    t3, 0x8            # bit3 = MIE
    csrw  mstatus, t3        # 6. 最後才開全域中斷

work_loop:
    addi  x11, x11, 1        # 主程式做事
    beq   x0, x0, work_loop  # 等 timer 打斷

    .align 2
timer_handler:               # timer interrupt handler
    csrrw sp, mscratch, sp    # 換到 handler stack（同時藏主程式 sp）
    addi  sp, sp, -8
    sw    t4, 0(sp)          # 存 context：借用的暫存器先存
    sw    t5, 4(sp)
    addi  x10, x10, 1        # 進 handler 次數 +1
    csrr  t4, 0x7C2          # 讀當前 mtime
    addi  t4, t4, 30         # 週期 30（明顯 > handler 長度，避免風暴）
    csrw  0x7C0, t4          # 重排鬧鐘 = mtime + 30（排下次 + 清 MTIP）
    lw    t4, 0(sp)          # 還原 context
    lw    t5, 4(sp)
    addi  sp, sp, 8
    csrrw sp, mscratch, sp    # 換回主程式 sp
    mret                     # interrupt：不動 mepc
```

### 每拍觀測 testbench `pe_tb.cpp`

只在「handler 進入次數變了」那拍印，最能看清週期：

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
    int N=(argc>1)?atoi(argv[1]):120;
    int last_x10=0;
    for(int c=0;c<N;c++){
        dut->eval();
        int x10=rd(10);
        if(x10!=last_x10){   // 進 handler 次數變了 → 印，看週期
            printf("cyc%-3d ENTER handler #%d  mtime=%u mcause=%08x x11(work)=%d\n",
                c, x10, dut->dbg_mtime, dut->dbg_mcause, (int)rd(11));
            last_x10=x10;
        }
        tick();
    }
    printf("FINAL handler_entries(x10)=%d  work(x11)=%d\n",(int)rd(10),(int)rd(11));
    delete dut; return 0;
}
```

### build 與跑

```bash
riscv64-unknown-elf-gcc -march=rv32i -mabi=ilp32 -nostdlib -Ttext=0x80000000 -o pe.elf pe.S
riscv64-unknown-elf-objcopy -O binary --only-section=.text pe.elf pe.bin
od -An -tx4 -w4 -v pe.bin | sed 's/ //g' > prog_pe.hex
verilator --cc core_trap.sv --exe pe_tb.cpp --Mdir obj_pe \
    -Wno-WIDTH -Wno-UNUSED -Wno-UNOPTFLAT -GINIT_FILE='"prog_pe.hex"'
make -s -C obj_pe -f Vcore_trap.mk Vcore_trap
./obj_pe/Vcore_trap 130
```

### 測試 1（週期性）真跑輸出

```
cyc31  ENTER handler #1  mtime=31 mcause=80000007 x11(work)=6
cyc67  ENTER handler #2  mtime=67 mcause=80000007 x11(work)=17
cyc103 ENTER handler #3  mtime=103 mcause=80000007 x11(work)=28
FINAL handler_entries(x10)=3  work(x11)=37
```

解讀：

- **第一次進 handler 在 cyc31**：第一次鬧鐘設 mtimecmp=25，mtime 追上後在下個指令邊界進 trap，加上 setup 花的幾拍，落在 cyc31。mcause=**0x80000007**（bit31=1 中斷、code=7 timer）——確認是 timer interrupt。
- **週期穩定約 36 拍**（cyc31→67→103）：週期 = 重排的 N(30) + handler 執行長度(~6 拍)。每次 handler 讀 mtime、設 `mtimecmp = mtime + 30`，於是下次在約 36 拍後再響。
- **主程式工作持續前進**（x11: 6 → 17 → 28 → 37）：中斷之間主程式一直在 `addi x11,x11,1`。handler 進出 3 次，**每次都精確回到 work loop 續跑**，x11 沒丟、沒亂。這就是週期性進出 handler 且主程式不受損——time-slice 的骨架。

### 測試 2（context 完整）真跑輸出

改 setup，讓主程式在一個暫存器放魔術值 `0x5555`——在 `li x10, 0` 前加一行 `li x5, 0x5555`（x5 是主程式的活資料）。handler 完全不碰 x5（只借 t4/t5，且先存後還原）。testbench 尾巴改印 x5，跑 130 拍：

```
x5 (magic value, expect 0x5555) = 0x5555
x10 (handler entries) = 3
x11 (work) = 36
```

解讀：timer 打斷了主程式 3 次（x10=3）、主程式工作進到 36（x11），但 **x5 完全是 0x5555，一點沒變**。因為 handler 只借用 t4/t5（`sw` 存起來、`lw` 還原）、只用 mscratch 換 sp，**從頭到尾沒動 x5**——handler 對主程式是透明的，打斷過但沒留下副作用。這正是「存 context」的意義：handler 借了什麼、就得原樣還回什麼。

反過來驗這個坑：把 handler 裡的 `sw t4,0(sp)` / `lw t4,0(sp)` 拿掉，讓 handler 直接用 t4 而不存——如果主程式的 work loop 也在用 t4 放活資料，跑完你會發現主程式的 t4 被 handler 的 mtime 值蓋掉了。這就是為什麼真 handler（xv6 `kernelvec.S`）開頭一口氣存 31 個暫存器：它不知道主程式在用哪些，乾脆全存。

### 測試 3（MIE 總開關）真跑輸出

把 `pe.S` 的 `csrw mstatus, t3`（開全域中斷）那行**拿掉**，其他完全不變，重新編譯跑：

```
MIE off: handler_entries(x10)=0  work(x11)=59  mip=(timer pending but ignored)
mcause=00000000  (0 = never trapped)
```

解讀：mtimecmp、mie.MTIE 都設好了，mtime 也早就追上 mtimecmp（MTIP 拉高、timer 確實在響），但 **handler 一次都沒進（x10=0）**、mcause 永遠 0（從沒 trap 過）、主程式一路跑到 x11=59。因為 `interrupt_taken = mstatus.MIE(0) & mie.MTIE(1) & MTIP(1) = 0`——**全域 MIE=0 一票否決**。這證明了 mstatus.MIE 是總開關：中斷擱著（pending）等它被打開，一開就會立刻進。這是 Ch 34 範例二的自己動手版。

</details>

---

## 對比取捨：你在這練習做的幾個選擇

| 選擇 | 選項 A | 選項 B | 這練習用哪個、為什麼 |
|---|---|---|---|
| handler 存 context | 不存（直接用暫存器）| mscratch 換 stack + sw/lw | **B**：測試 2 要驗主程式活資料不被弄壞，只有 B 保證透明 |
| 重排週期 N | 小（省閒置）| 大於 handler 長度 | **大**：N < handler 長度會中斷風暴、主程式停擺（延伸挑戰 1 實測）|
| 清 timer 中斷 | 寫 mip（清 MTIP）| 推 mtimecmp | **推 mtimecmp**：MTIP 唯讀，寫不動；改「產生它的原因」才是正解（Ch 34 雷 3）|
| handler 動 mepc | +4 跳過 | 不動 | **不動**：timer 是 interrupt，mepc 是「下一條沒執行的」，+4 會漏跑一條 |
| setup 開 MIE 的時機 | 一開始 | 最後 | **最後**：先開 MIE 時 mtvec 還沒設好，來中斷就跳 mtvec=0 崩 |

這五個選擇，每一個選錯都對應一個測試會 fail 或一個延伸挑戰的坑。把它們想清楚，你就真的懂了「一支能用的 timer handler」每個動作為什麼非那樣不可。

---

## 延伸挑戰

做完基本三測試，挑一兩個往深走：

1. **踩坑實測——週期太小的中斷風暴**：把 handler 重排的 N 從 30 改成 5（`addi t4, t4, 5`），跑 130 拍看會怎樣。你會看到 handler 狂進、**主程式工作進度卡死**——因為 N 小於 handler 執行長度，mret 出去 mtime 已經超過新 mtimecmp，立刻再觸發。參考真跑：

   ```
   cyc87  ENTER handler #5  mtime=87  mcause=80000007 x11(work)=6
   cyc101 ENTER handler #6  mtime=101 mcause=80000007 x11(work)=6
   cyc115 ENTER handler #7  mtime=115 mcause=80000007 x11(work)=6
   cyc129 ENTER handler #8  mtime=129 mcause=80000007 x11(work)=6
   FINAL handler_entries(x10)=8  work(x11)=6
   ```

   注意 **x11 卡在 6 完全不動**（對比 N=30 時 x11 一路漲到 37）——系統忙於處理中斷，主程式跑不了。這就是「週期必須大於 handler 執行時間」在即時系統為什麼是硬約束。親眼看到這個，你就永遠不會忘。

2. **可變週期（tickless 雛形）**：讓 handler 依「進入次數」動態調整 N——例如前 3 次用短週期、之後拉長。這模擬真 Linux 的動態 timer（NO_HZ）：不是固定 tick，而是依「下一個要做的事」設鬧鐘。

3. **加一個 ECALL 對照**：在 work loop 裡偶爾插一條 `ecall`，讓 core 同時處理 timer interrupt（mcause=0x80000007、不動 mepc）和 ECALL exception（mcause=11、要 +4）。在 handler 開頭讀 mcause 分流：bit31=1 走 timer 路徑、bit31=0 走 ECALL 路徑（記得只有後者 +4）。這把 Ch 32（exception）和 Ch 34（interrupt）在同一支 handler 裡合流，是真 trap 分流器（xv6 `devintr()`）的雛形。

4. **Vectored mtvec**：把 mtvec 低 2 bit 設 1（Vectored 模式），在 `base + 4*7` 放 timer 專屬入口，省掉讀 mcause 分流。比較它和 Direct 模式的中斷延遲差別。

---

## 自我檢核

交作業前，對照這份清單確認你真的做到（不是抄參考解跑過就算）：

- [ ] 我的 setup 順序是 mtvec → mscratch → mtimecmp（含清高 32 bit）→ mie.MTIE → **最後** mstatus.MIE，而且我能說出為什麼 MIE 要最後開。
- [ ] 測試 1 我看到 handler 週期性進入 ≥3 次、mcause=0x80000007、主程式工作進度在中斷之間持續前進（斷點續跑），而且我能算出週期 ≈ N + handler 長度。
- [ ] 測試 2 我看到主程式的魔術值原封不動，而且我能解釋是 handler 的哪幾條指令（存/還原 + mscratch 換 sp）保證了這件事。
- [ ] 測試 3 我拿掉開 MIE 後看到 handler 一次都沒進、mcause 恆 0，而且我能用 `interrupt_taken = MIE & MTIE & MTIP` 這行解釋為什麼。
- [ ] 我的 timer handler **完全沒動 mepc**，我能說出為什麼（interrupt vs exception 的 mepc 差別）。
- [ ] 我做過延伸挑戰 1（N 太小的中斷風暴），親眼看到主程式工作進度卡住不動，理解「週期必須 > handler 長度」是硬約束。

---

做完這練習，你就親手驗過了「一顆能被 timer 週期性打斷、還能保護主程式現場的 CPU」——這正是能承載作業系統排程器的硬體地基。Part 5 到此，CSR / trap / privilege / 中斷 / 整合，從讀懂到做過，都齊了。下一章我們抬頭看更高階的 CPU 微架構：superscalar（超純量）與 out-of-order（亂序執行）——現代高效能核怎麼在一拍發射多條指令、怎麼讓指令不照順序執行卻仍保持正確。

→ [Ch 36 Superscalar 與 Out-of-Order 概念](./36-superscalar-ooo-concepts.md)
