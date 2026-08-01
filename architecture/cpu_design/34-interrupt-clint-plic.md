# Ch 34 — 中斷控制：CLINT（timer / software int）、PLIC 速覽

> **目標**：把 trap 的另一半——**interrupt（中斷）**——真正做出來。exception 是「當前指令自己出事」，interrupt 是「外部世界非同步地要 CPU 注意」。你會學中斷怎麼從外部訊號變成 trap：CLINT（Core Local Interruptor）用 **mtime/mtimecmp** 產生 timer interrupt、用 **msip** 產生 software interrupt；**mie（開關）× mip（等待）× mstatus.MIE（全域總開關）** 三者怎麼共同決定「這個中斷現在觸不觸發」；中斷的優先序與 mcause 編碼（最高位=1）。然後**實作一個簡易 CLINT + timer interrupt，讓 core 週期性地進出 handler**——真跑看它每隔固定 cycle 就被 timer 打斷一次。PLIC（管外部裝置中斷）速覽。這是深挖章。
> **環境**：WSL + verilator 4.038 + riscv64-unknown-elf-gcc 10.2.0。CLINT + timer 中斷真跑，週期性進 handler 的每一拍貼輸出。
> 如果你對 RISC-V 中斷架構（CLINT vs PLIC、mtime 的定位）不熟，回看 `architecture/riscv` 課的中斷章——這章做硬體那半邊。

## 為什麼需要中斷？

到現在，我們的 CPU 只會對「自己執行的指令」起反應——順順跑，出事（exception）才 trap。但真實系統得回應**外部世界**：

- **時間到了要換手**：作業系統要做**時間片輪轉**（time-slice scheduling）——每個行程跑一小段就換下一個，不能讓一個行程霸佔 CPU。怎麼知道「一小段」到了？靠一個**計時器（timer）**，時間到就**打斷**當前行程、跳進 kernel 的排程器。沒有 timer interrupt，OS 沒法搶回 CPU，一個死迴圈的 user 程式能卡死整台機器。
- **裝置有事要通知**：鍵盤有輸入、網卡收到封包、硬碟讀完了——這些**隨時可能發生**，CPU 不可能一直去問「你好了沒？」（那叫 polling，浪費 CPU）。更好的方式是裝置**主動打斷** CPU：「我好了，來處理我」。這就是 device interrupt。
- **核間通訊**：多核系統裡，一個核要叫醒另一個核——發一個 **software interrupt**（核間中斷，IPI）。

這些的共同點：**跟 CPU 當下在算什麼無關，是外部、非同步發生的，但 CPU 要能隨時放下手邊工作去回應。** 這就是 interrupt。它和 exception 走同一套 trap 流程（Ch 32 的「存現場→跳 handler→mret 返回」），但**觸發源在外部、時機不定**。

一句話：**中斷是 CPU 感知並回應外部世界的機制——沒有它，CPU 是聾的，作業系統搶不回控制權、裝置沒法通知、時間片跑不動。** 這章我們給 CPU 裝上「耳朵」。

## 先建立直覺：門鈴

exception 像你自己在廚房打翻了東西（當前動作引起，你當然知道），interrupt 像**門鈴響**：

```
   你在客廳看書（CPU 執行 user 程式）
        │
   叮咚！門鈴響（外部中斷訊號拉高）
        │
   ┌─── 你會不會去開門？取決於三件事 ───┐
   │ 1. 門鈴插頭有沒有插？（mie：這個中斷源開了嗎）│
   │ 2. 你有沒有戴耳機沒聽到？（mstatus.MIE：全域開嗎）│
   │ 3. 門鈴真的在響嗎？（mip：這個中斷正在 pending 嗎）│
   └────────────────────────────────────┘
        │ 三者都成立 → 放下書、去開門
        ▼
   開門處理（跳 handler）→ 處理完回來接著看書（mret）
```

三個開關缺一不可：

- **mip（Machine Interrupt Pending）= 門鈴正在響嗎**：某個中斷源「有事等處理」的旗標。timer 到期了、裝置有事了，對應的 mip bit 就被拉高。這是**外部硬體設的**（CLINT 設 timer bit、PLIC 設 external bit）。
- **mie（Machine Interrupt Enable）= 這個門鈴的插頭插了嗎**：每個中斷源一個開關。你可以只開 timer 中斷、關掉 external 中斷。這是**軟體設的**（OS 決定要聽哪些）。
- **mstatus.MIE（全域中斷開關）= 你有沒有戴耳機**：一個總開關，一關全聾（所有中斷都不理）。進 handler 時硬體自動關它（Ch 32：MIE←0），免得處理門鈴時又被別的門鈴打斷。

**中斷真正觸發，要 `mip & mie` 有交集（某個源既 pending 又 enabled）且 `mstatus.MIE=1`（全域開）。** 三者的邏輯與，就是「現在要不要進中斷」。

## 核心概念：mip / mie 的 bit 佈局與中斷種類

RISC-V machine-level 有三種標準中斷，mip 和 mie 用**相同的 bit 位置**表示：

```
   mip / mie（同樣的 bit 位置）：
   bit 3  : MSIP / MSIE — Machine Software Interrupt（核間中斷）
   bit 7  : MTIP / MTIE — Machine Timer Interrupt（計時器中斷）
   bit 11 : MEIP / MEIE — Machine External Interrupt（外部裝置中斷）
```

- **software interrupt（bit 3）**：核間中斷（IPI）。一個核寫另一個核的 msip 暫存器，對方的 MSIP bit 拉高。單核用不太到（本課帶過）。
- **timer interrupt（bit 7）**：計時器中斷，本章主角。`mtime >= mtimecmp` 時 MTIP 自動拉高。
- **external interrupt（bit 11）**：外部裝置中斷（鍵盤、網卡、UART...），經過 PLIC 匯集後拉高 MEIP。本章末速覽 PLIC。

要觸發某個中斷，`mip.X & mie.X` 都要是 1。例如開 timer 中斷：`mie.MTIE=1`（bit7），等 `mip.MTIP` 被 CLINT 拉高，且 `mstatus.MIE=1`——三者齊了才進。

**mip 多數 bit 是唯讀的（軟體不能直接寫）**——它反映硬體狀態。你不能用 `csrw mip` 把 MTIP 清掉；要清 timer interrupt，得去**改造成它的原因**：把 mtimecmp 設到比 mtime 大（讓 `mtime >= mtimecmp` 不再成立），MTIP 自然落下。這是本章 handler 清中斷的關鍵手法。

## 核心概念：CLINT 與 mtime / mtimecmp

**CLINT（Core Local Interruptor）** 是一個**記憶體映射（memory-mapped）** 的硬體單元，管兩種 core-local 中斷：timer 和 software。它不是 CSR，而是掛在記憶體位址空間的一塊（真晶片常在 `0x0200_0000`），CPU 用普通 `lw`/`sw` 存取。CLINT 的主要暫存器：

| 暫存器 | 典型位址（相對 CLINT base）| 作用 |
|---|---|---|
| `msip` | 0x0000 | 寫 1 → 拉高本核 MSIP（software int）|
| `mtimecmp` | 0x4000 | timer 比較值（64-bit）|
| `mtime` | 0xBFF8 | 全域遞增計時器（64-bit，所有核共用）|

**timer interrupt 的產生機制極簡單**：

```
   CLINT 內部（每個 tick）：
   ┌────────────────────────────────────────┐
   │ mtime 持續遞增（每個固定時間 +1）         │
   │                                          │
   │ if (mtime >= mtimecmp)                    │
   │     MTIP（mip.bit7）← 1   （拉高 timer 中斷）│
   │ else                                      │
   │     MTIP ← 0                              │
   └────────────────────────────────────────┘
```

`mtime` 是一個一直往上數的時鐘，`mtimecmp` 是「鬧鐘時間」。當 `mtime` 追上 `mtimecmp`，MTIP 拉高——鬧鐘響了。**要設定「N 個 tick 後中斷一次」**：把 `mtimecmp = mtime + N`。中斷發生後，handler 要「重設鬧鐘」——再把 `mtimecmp = 當前 mtime + N`（排下一次），順便這動作也把 MTIP 清掉（因為 `mtime < 新 mtimecmp` 了）。**這一設一清，就讓 timer 週期性地響——OS time-slice 的心跳。**

本章為了聚焦「中斷怎麼進出 core」，把 CLINT 的 mtime/mtimecmp 邏輯放在 **testbench 端**建模（一個真硬體 CLINT 該做的事，用 C++ 模擬），core 只收一根 `timer_irq` 線（= MTIP 的訊號）。這樣 core 的中斷邏輯乾淨、CLINT 的 timer 邏輯清楚，兩者職責分明。練習 E 會讓你把 CLINT 做成真的 SV 模組。

## 核心概念：中斷的 mcause 與優先序

中斷觸發後進 trap，mcause 怎麼填？**最高位（RV32 的 bit31）= 1 表示這是中斷**（exception 是 0），低位是中斷編號（就是 mip/mie 的 bit 位置）：

| 中斷 | mcause 值（RV32）|
|---|---|
| Machine Software Interrupt | `0x80000003`（bit31=1, code=3）|
| Machine Timer Interrupt | `0x80000007`（bit31=1, code=7）|
| Machine External Interrupt | `0x8000000B`（bit31=1, code=11）|

handler 讀 mcause，先看 bit31：
- **bit31=1** → 是中斷，低位是哪種（3=software、7=timer、11=external）。
- **bit31=0** → 是 exception，低位是哪種（2=illegal、11=ecall-from-M...，Ch 32/33）。

**多個中斷同時 pending 時的優先序**（RISC-V 標準）：MEI > MSI > MTI（external > software > timer）——外部裝置最急、timer 最不急（timer 晚一點處理沒差）。本章單一 timer 源，不涉及仲裁；多源時 core 要挑最高優先的填 mcause。

還有一個關鍵差別（Ch 32 提過，這裡強化）：**interrupt 的 mepc 存的是「下一條還沒執行的指令」**，不是「觸發的指令」（interrupt 沒有「觸發的指令」——它插在指令邊界）。所以 timer interrupt handler **不該對 mepc +4**——被打斷的指令還沒執行，回去要接著執行它。這和 ECALL handler 要 +4 正好相反（Ch 33 踩雷 1）。

## 底層機制：一次 timer interrupt 的完整生命

把 timer interrupt 從產生到清除走一遍：

```
   1. OS 設定：
      mie.MTIE  ← 1        （開 timer 中斷源）
      mstatus.MIE ← 1      （開全域中斷）
      mtimecmp  ← mtime+N  （設鬧鐘：N 個 tick 後）
      → 然後跑 user 程式

   2. 時間流逝：mtime 一直漲... 直到 mtime >= mtimecmp
      → CLINT 拉高 MTIP（mip.bit7 ← 1）

   3. core 每拍檢查：mstatus.MIE & mie.MTIE & mip.MTIP 都 1？
      → 是！在下一個指令邊界進 trap：
        mepc ← 被打斷指令的 PC（下一條要執行的）
        mcause ← 0x80000007（timer int）
        MPIE←MIE、MIE←0（關中斷）、跳 mtvec

   4. handler 跑：
      讀 mcause（看到 0x80000007 → timer int）
      重設鬧鐘：mtimecmp ← 當前 mtime + N（排下一次 + 清 MTIP）
      （做該做的：排程、更新系統時間...）
      mret → MIE←MPIE（重開中斷）、跳回 mepc（被打斷處，繼續）

   5. 回到 user 跑，直到下一次 mtime 追上新 mtimecmp → 回到步驟 2
```

這個循環**週期性重複**——每 N 個 tick，user 被打斷一次、進 handler、回來。這就是「時間片」的硬體實現。下面用真 core 跑出這個週期性循環。

## 實作：core 端的中斷邏輯

core 收一根 `timer_irq`（= MTIP），中斷觸發判斷是三個開關的與：

```systemverilog
// 中斷 pending：全域開(MIE) && timer 源開(MTIE=mie.bit7) && timer 訊號拉高
logic irq_pending;
assign irq_pending = mstatus[3] & mie[7] & timer_irq;

// trap 決策：exception 或 interrupt 任一
logic take_trap;
assign take_trap = is_ecall | is_illegal | irq_pending;

// mcause：interrupt 最高位=1
logic [31:0] trap_cause;
always_comb begin
    if (irq_pending)     trap_cause = 32'h80000007; // machine timer interrupt
    else if (is_illegal) trap_cause = 32'd2;
    else                 trap_cause = 32'd11;
end
```

`irq_pending = mstatus[3] & mie[7] & timer_irq`——這一行就是「門鈴、插頭、耳機」三者的與。三個都 1 才 `irq_pending`，才可能進中斷。注意進 trap 後硬體自動 `mstatus.MIE←0`（Ch 32），所以**handler 執行期間 irq_pending 天然是 0**（全域中斷關了）——不會在 handler 裡又被同一個 timer 打斷，直到 mret 重開 MIE。

trap 進入的存 context 部分（Ch 32 那套）對中斷一樣適用，只是這裡 mepc 存的是**被打斷指令的 PC**（我們的 mini core 單週期，pc 就是當前要執行的那條，天然是「下一條要執行的」，符合 interrupt 語意）。

## 實作：testbench 端的 CLINT 模型

CLINT 的 timer 邏輯在 tb 建模：`mtime` 每拍 +1，`mtime >= mtimecmp` 就拉 `timer_irq`；偵測到「進 timer trap」那拍，把 `mtimecmp += PERIOD`（模擬 handler 重設鬧鐘、排下次、清 MTIP）：

```cpp
// tb 端 CLINT 模型
const unsigned long long PERIOD = 15;   // 每 15 個 tick 一次 timer 中斷
unsigned long long mtime = 0, mtimecmp = 10;

for (int c = 0; c < maxc; c++) {
    dut->timer_irq = (mtime >= mtimecmp) ? 1 : 0;   // CLINT：mtime>=mtimecmp → MTIP
    dut->eval();
    // 偵測「這拍要進 timer trap」→ 重設鬧鐘（= handler 寫 mtimecmp 清中斷+排下次）
    if (dut->trap_taken_o && dut->timer_irq) { mtimecmp = mtime + PERIOD; }
    /* ...印狀態... */
    tick();
    mtime++;
}
```

真硬體裡「重設 mtimecmp」是 handler 用 `sw` 寫 CLINT 的 mtimecmp 位址做的；這裡 tb 代勞（在進 trap 那拍直接更新 mtimecmp），效果一樣——把 timer_irq 拉低、排下一次。

## 範例一：週期性 timer interrupt 進出 handler

程式：設好 mtvec、開 mie.MTIE、開 mstatus.MIE，然後進一個 idle 迴圈空轉，等 timer 週期性打斷：

```asm
_start:
    la    x5, trap_handler
    csrw  mtvec, x5           # 設 handler
    li    x6, 0x80            # bit7 = MTIE
    csrw  mie, x6             # mie.MTIE = 1（開 timer 中斷源）
    li    x6, 0x8             # bit3 = MIE
    csrw  mstatus, x6         # mstatus.MIE = 1（開全域中斷）
    li    x10, 0
loop:
    addi  x10, x10, 0         # idle 空轉（等中斷）
    j     loop

    .align 2
trap_handler:
    csrr  x6, mcause          # x6 = mcause（應 0x80000007 timer int）
    addi  x11, x11, 1         # x11++：記錄進 handler 幾次
    li    x12, 1              # x12=1（通知 tb 清 mtimecmp——本 tb 用 trap 偵測代勞）
    mret                      # 返回（不動 mepc！interrupt 回去重跑被打斷指令）
```

注意 handler **沒有對 mepc +4**——interrupt 的被打斷指令還沒執行，回去要接著跑（對比 ECALL handler 要 +4）。

build 跑（每拍印 PC、mtime、mtimecmp、irq、trap、mcause、x11=進 handler 次數）：

```bash
riscv64-unknown-elf-gcc -march=rv32i -mabi=ilp32 -nostdlib -Ttext=0x80000000 -o timer.elf timer.S
riscv64-unknown-elf-objcopy -O binary --only-section=.text timer.elf timer.bin
od -An -tx4 -w4 -v timer.bin | sed 's/ //g' > prog_timer.hex
verilator --cc minicore.sv --exe timer_tb.cpp --Mdir obj_tm \
    -Wno-WIDTH -Wno-UNUSED -Wno-UNOPTFLAT -GINIT_FILE='"prog_timer.hex"'
make -s -C obj_tm -f Vminicore.mk Vminicore
./obj_tm/Vminicore 60
```

真跑輸出（PERIOD=15；節錄看清三次中斷）：

```
cyc 9 pc=80000024 mtime= 9 mtimecmp=10 irq=0 trap=0 mcause=00000000 x11=0
cyc10 pc=80000020 mtime=10 mtimecmp=25 irq=1 trap=1 mcause=00000000 x11=0
cyc11 pc=80000028 mtime=11 mtimecmp=25 irq=0 trap=0 mcause=80000007 x11=0
cyc12 pc=8000002c mtime=12 mtimecmp=25 irq=0 trap=0 mcause=80000007 x11=0
cyc13 pc=80000030 mtime=13 mtimecmp=25 irq=0 trap=0 mcause=80000007 x11=1
cyc14 pc=80000034 mtime=14 mtimecmp=25 irq=0 trap=0 mcause=80000007 x11=1
cyc15 pc=80000020 mtime=15 mtimecmp=25 irq=0 trap=0 mcause=80000007 x11=1
...（idle 迴圈空轉，等下一次鬧鐘）...
cyc25 pc=80000020 mtime=25 mtimecmp=40 irq=1 trap=1 mcause=80000007 x11=1
cyc26 pc=80000028 mtime=26 mtimecmp=40 irq=0 trap=0 mcause=80000007 x11=1
cyc28 pc=80000030 mtime=28 mtimecmp=40 irq=0 trap=0 mcause=80000007 x11=2
...
cyc40 pc=80000020 mtime=40 mtimecmp=55 irq=1 trap=1 mcause=80000007 x11=2
cyc43 pc=80000030 mtime=43 mtimecmp=55 irq=0 trap=0 mcause=80000007 x11=3
FINAL x11(handler entries)=4 irq-taken=4
```

一拍一拍看週期性中斷：

- **cyc0~9**：setup（設 mtvec、開 MTIE、開 MIE）然後進 idle 迴圈。mtime 一直漲，還沒到 mtimecmp（10），irq=0。
- **cyc10（mtime=10 追上 mtimecmp=10，irq=1、trap=1）**：**鬧鐘響了！** `mtime >= mtimecmp` → CLINT 拉高 timer_irq。core 這拍 `irq_pending = MIE(1) & MTIE(1) & timer_irq(1) = 1`，決定進 trap。同時 tb 偵測到進 trap，把 mtimecmp 重設成 `10+15=25`（排下一次）。
- **cyc11**：進 handler！PC 跳到 0x28（trap_handler），mcause=**0x80000007**（bit31=1 = 中斷、code=7 = timer）。irq 已回 0（mtimecmp 被推到 25，mtime=11<25）。
- **cyc11~14**：handler 跑，讀 mcause、x11++（→1）、`mret`。
- **cyc15**：mret 跳回 mepc（idle 迴圈 0x20），繼續空轉。此時 x11=1（進過一次 handler）。
- **cyc15~24**：idle 空轉，等 mtime 追上新 mtimecmp（25）。
- **cyc25（mtime=25 追上 25，irq=1、trap=1）**：**第二次鬧鐘！** 又進 trap，mtimecmp→40。
- **cyc28**：x11=2（進過兩次）。
- **cyc40**：**第三次**（mtime=40 追上 40），mtimecmp→55，之後 x11=3。
- 最終 `x11=4`：60 拍內 timer 週期性打斷了 4 次（cyc10、25、40、55），每次進 handler x11++。**每 15 個 tick 一次，穩定週期。**

這就是 OS time-slice 的心跳——**timer 每隔固定時間打斷 CPU 一次，讓 kernel 有機會做排程**。把 handler 裡的 `x11++` 換成「切換到下一個行程」，就是搶佔式多工的核心。中斷讓 CPU 不再被單一程式霸佔。

## 範例二：關掉 MIE，中斷不進（驗證總開關）

驗證「戴耳機沒聽到」——把 `csrw mstatus, x6`（開 MIE）那行拿掉，其他不變。mie.MTIE 開著、timer_irq 照樣拉高（mtime 追上 mtimecmp），但全域 MIE=0：

真跑輸出（節錄）：

```
cyc10 pc=80000020 mtime=10 mtimecmp=10 irq=1 trap=0 mcause=00000000 x11=0
cyc11 pc=80000024 mtime=11 mtimecmp=10 irq=1 trap=0 mcause=00000000 x11=0
cyc12 pc=80000020 mtime=12 mtimecmp=10 irq=1 trap=0 mcause=00000000 x11=0
...（irq 一直是 1，但 trap 永遠 0，x11 永遠 0）...
FINAL x11(handler entries)=0 irq-taken=0
```

`irq=1`（timer 確實在響、MTIP 拉高了）但 **`trap=0`、x11=0**——中斷**沒進**。因為 `irq_pending = mstatus.MIE(0) & mie.MTIE(1) & timer_irq(1) = 0`。**全域 MIE=0 一票否決**，不管 timer 多急都不理。這證明了 mstatus.MIE 是總開關——OS 在關鍵區段（critical section）想「暫時不被中斷」時，就清 MIE，做完再開。中斷被擱置（pending 著，mip 保持），MIE 一開就立刻進。

> 這也解釋了為什麼進 handler 硬體自動 MIE←0（Ch 32）：handler 存 context 期間不能被打斷，靠的就是這個總開關。範例一的 handler 期間（cyc11~14）irq 是 0 是因為 mtimecmp 被推走了；就算沒推走，MIE=0 也會擋住——雙保險。

## 核心概念：PLIC 速覽——管外部裝置中斷

CLINT 管 timer 和 software（core-local，每核一份）。但**外部裝置**（鍵盤、UART、網卡、硬碟...）可能有幾十上百個，而 mip 只有一個 MEIP（external interrupt）bit——怎麼把上百個裝置中斷塞進一個 bit？靠 **PLIC（Platform-Level Interrupt Controller）**。

```
   多個裝置                PLIC                    CPU
   ┌────────┐          ┌─────────────┐
   │ UART   │─irq─────▶│             │
   │ 網卡   │─irq─────▶│ 優先級仲裁  │──MEIP──▶ mip.bit11
   │ 硬碟   │─irq─────▶│ + 遮罩      │        （一根線）
   │ ...    │─irq─────▶│             │
   └────────┘          └─────────────┘
```

PLIC 的工作：
- **匯集（aggregate）**：把 N 個裝置的中斷線收進來，只要有任一個 pending 且 enabled，就拉高 CPU 的 MEIP（一根線）。
- **優先級仲裁（priority）**：每個中斷源可設優先級，PLIC 挑最高優先的先報告。
- **遮罩（enable/threshold）**：每個 CPU（context）可設「只聽哪些源、優先級門檻多少」。
- **claim/complete**：CPU 收到 MEIP 進 handler 後，讀 PLIC 的 `claim` 暫存器問「是哪個裝置？」（PLIC 回最高優先的中斷 ID），處理完寫 `complete` 告訴 PLIC「這個處理好了」。

所以外部中斷的流程比 timer 多一層：`裝置 → PLIC 匯集/仲裁 → MEIP → CPU trap → handler 讀 PLIC claim 問是誰 → 處理 → 寫 complete`。PLIC 也是 memory-mapped（真晶片常在 `0x0C00_0000`），CPU 用 lw/sw 存取。

本課不實作 PLIC（它是一大塊、且中斷語意的核心已在 CLINT/timer 講清）。知道它的定位即可：**CLINT = core-local 的 timer/software 中斷；PLIC = 匯集外部裝置中斷成一根 external 線。** 兩者一起，CPU 就能回應「時間」和「所有外部裝置」。

## 對比取捨：CLINT vs PLIC、interrupt vs exception

| 面向 | CLINT | PLIC |
|---|---|---|
| 管什麼 | timer（MTIP）、software（MSIP）| external 裝置中斷（MEIP）|
| 範圍 | core-local（每核一份 mtime/msip）| platform-level（全系統共用，多核仲裁）|
| 對應 mip bit | bit3（MSIP）、bit7（MTIP）| bit11（MEIP）|
| 複雜度 | 簡單（mtime 比大小）| 複雜（優先級、遮罩、claim/complete）|
| 需要 claim 嗎 | 否（handler 直接處理）| 是（讀 claim 問是哪個裝置）|

| 面向 | interrupt（本章）| exception（Ch 32）|
|---|---|---|
| 觸發 | 外部、非同步 | 當前指令、同步 |
| mcause bit31 | **1** | 0 |
| mepc | 下一條（沒執行的）| 觸發的指令 |
| handler 動 mepc | **不動**（回去重跑被打斷指令）| 常 +4（跳過）或不動（重試）|
| 能被延遲嗎 | 能（MIE/mie 關就擱置）| 不能（指令執行就發生）|

最關鍵的取捨思維：**interrupt 可以「延遲處理」（關中斷擱著），exception 不行（指令一執行就發生）。** 這讓 OS 能在 critical section 短暫關中斷保護資料結構，中斷擱著等一下再處理——不影響正確性（timer 晚幾拍處理沒差）。exception 沒這彈性（除以零就是當場出事）。

## 踩雷區

**雷 1：以為中斷觸發只看 timer_irq 一個訊號。**
- 錯誤直覺：「timer 到期拉高 irq，中斷就進了」。
- 正確認識：中斷觸發要 **`mstatus.MIE & mie.MTIE & mip.MTIP` 三者全 1**。缺任一都不進——範例二證明了：irq（MTIP）拉高但 MIE=0，中斷完全不進。三個開關各有用：mie 選「聽哪些源」、MIE 是「總開關」（critical section 用）、mip 是「現在有沒有事」。把中斷當「訊號一來就進」，你會搞不懂為什麼 timer 明明響了 handler 卻沒跑（多半是 MIE 或 mie 沒開），或為什麼關中斷的區段能保護資料（就是靠 MIE 擋住）。

**雷 2：timer interrupt handler 也對 mepc +4。**
- 錯誤直覺：「trap handler 返回前都要 mepc+4 跳過惹禍指令」（把 Ch 32/33 的 ECALL 習慣套過來）。
- 正確認識：**interrupt 的 mepc 是「被打斷、還沒執行的指令」，handler 絕不能 +4**——+4 會跳過一條合法指令，程式漏執行一條，行為錯亂。範例一 handler 沒有 +4，mret 直接回被打斷處重跑。只有 exception（ECALL/illegal，指令已「發生」）才可能要 +4。**判準：看 mcause bit31——是中斷（1）就別動 mepc，是 exception（0）才考慮 +4。** 這是 interrupt 和 exception handler 最容易寫錯的差別。

**雷 3：以為清中斷是寫 mip。**
- 錯誤直覺：「timer 中斷處理完，`csrw mip, 0` 清掉 MTIP」。
- 正確認識：**mip 的 MTIP/MEIP 是唯讀的（軟體寫不動）**——它反映硬體狀態。要清 timer 中斷，得**消除產生它的原因**：把 mtimecmp 設到比 mtime 大（`mtime < mtimecmp` → CLINT 自動落下 MTIP）。範例一的 handler（真硬體版）就是 `sw` 寫 CLINT 的 mtimecmp 位址來清+排下次。你若 `csrw mip` 想清 MTIP，寫不進去，中斷會**立刻再次觸發**（mret 出去 → MTIP 還在 → 又進）→ 無窮中斷風暴。清中斷要對症下藥：改硬體狀態，不是寫旗標。（software int 例外——MSIP 是可寫的，寫 CLINT 的 msip=0 清。）

**雷 4：以為 handler 執行期間會被同一個 timer 再打斷。**
- 錯誤直覺：「handler 跑的時候 timer 又到期，會巢狀中斷、堆疊爆掉」。
- 正確認識：進 trap 時硬體自動 **MIE←0（關全域中斷）**（Ch 32），所以 handler 執行期間中斷是關的——不會被打斷（除非 handler 自己重開 MIE 允許巢狀）。範例一 handler（cyc11~14）期間就算 timer 又響（mip.MTIP 拉高），MIE=0 也擋住，等 mret 重開 MIE（MIE←MPIE=1）才可能再進。所以正常 handler 不會被自己巢狀打斷——這是 MIE 自動關的用意。中斷擱著（pending），不會丟（mip 保持），mret 後若還 pending 就再進——一次一個，不堆疊。只有 handler 刻意重開 MIE（為了讓更高優先中斷插隊）才有巢狀，那要小心 mepc/mcause 只有一份、得先存進 stack。

## 進階延伸

- **mtime 是全域的、mtimecmp 是每核的**：多核系統裡 `mtime` 只有一份（全系統共用的時鐘，CLINT 提供），但 `mtimecmp` 每核一個（每核設自己的鬧鐘）。這樣所有核看到同一個時間，但各自決定何時被 timer 打斷。OS 排程器給每核設不同的 mtimecmp，實現每核獨立的 time-slice。本課單核，這差別不明顯，但多核 OS 這是基礎。真硬體 mtime/mtimecmp 是 64-bit（RV32 要用兩次 32-bit 存取讀寫，還有跨 32-bit 邊界的原子性問題）。
- **timer 中斷的頻率與 OS tick**：Linux 傳統用固定頻率 timer 中斷（HZ，如 100/250/1000 Hz），每次 tick 更新系統時間、檢查 time-slice。現代 Linux 用 **tickless（NO_HZ）**——idle 時不設 timer 中斷（省電），有事才設。這靠動態調整 mtimecmp（下一個事件才設鬧鐘，不是固定週期）。本章範例一是固定週期（PERIOD=15），是傳統 tick 模型；tickless 就是讓 handler 依「下一個要做的事」設 mtimecmp，而非固定 +N。
- **software interrupt（IPI）與多核喚醒**：msip（bit3）是核間中斷——核 A 寫核 B 的 CLINT msip 位址，核 B 的 MSIP 拉高、進中斷。這是多核 OS 的核間通訊基礎：喚醒 idle 的核、要求別的核刷 TLB（TLB shootdown）、重新排程。單核用不到，但這是本章沒細講的第三種中斷。實作和 timer 類似（一個可寫的 mip bit + memory-mapped 觸發暫存器）。
- **中斷延遲（interrupt latency）與即時系統**：從「中斷訊號拉高」到「handler 第一條指令執行」的時間叫中斷延遲。它包含：等當前指令到邊界、pipeline flush、跳 mtvec、（Vectored 模式省掉軟體分流）。即時系統（RTOS）在意這個——延遲要小且可預測。影響因素：有沒有關中斷的長 critical section、Direct vs Vectored mtvec、pipeline 深度。SiFive Interrupt Cookbook 有詳細的延遲分析。本課的單週期 mini core 延遲是 1 拍（下個邊界就進），真 pipeline core 要幾拍（flush + 重新灌 handler）。

## 本章重點整理

- **中斷是 CPU 感知外部世界的機制**：timer（time-slice 心跳）、external（裝置通知）、software（核間）。非同步、時機不定，走 trap 流程但觸發源在外部。用「門鈴」記憶。
- **三個開關決定中斷觸不觸發**：`mstatus.MIE`（全域總開關）× `mie.X`（每源開關）× `mip.X`（每源 pending）。三者與，缺一不進。範例二證明 MIE=0 一票否決。
- **CLINT 產生 timer int**：memory-mapped，`mtime` 遞增、`mtime >= mtimecmp` → MTIP 拉高。設 `mtimecmp = mtime + N` 定週期；handler 重設 mtimecmp 清中斷+排下次。
- **中斷 mcause bit31=1**：timer=0x80000007、software=0x80000003、external=0x8000000B。優先序 MEI > MSI > MTI。
- **interrupt 的 mepc 是「下一條」，handler 不 +4**（對比 exception）。清中斷靠改硬體狀態（推 mtimecmp）不是寫 mip（唯讀）。進 handler 硬體自動 MIE←0，不會被自己巢狀打斷。
- **PLIC 匯集外部裝置中斷**成一根 MEIP 線，帶優先級仲裁 + claim/complete。CLINT = core-local timer/software；PLIC = platform-level external。
- **真跑驗證**：timer 每 15 tick 週期性進 handler（cyc10/25/40/55，x11=1→4）；關 MIE 後 irq 拉高但 trap=0（總開關否決）。

## 自我檢核

- [ ] 我能說出中斷觸發要 `mstatus.MIE & mie.X & mip.X` 三者全 1，並解釋每個開關的用途（總開關 / 選源 / pending）。
- [ ] 我能描述 CLINT 用 mtime/mtimecmp 產生 timer 中斷的機制，以及怎麼設定「每 N tick 一次」。
- [ ] 我能追出範例一週期性中斷（cyc10/25/40 三次進 handler），說明每次 mtimecmp 怎麼被重設、irq 怎麼落下。
- [ ] 我能解釋為什麼 timer handler 不能對 mepc +4（interrupt vs exception 的 mepc 差別）。
- [ ] 我能說明為什麼清 timer 中斷要推 mtimecmp 而非寫 mip，以及寫 mip 會導致中斷風暴。
- [ ] 我能區分 CLINT 和 PLIC 各管什麼、對應哪個 mip bit，並說出 PLIC 為什麼需要 claim/complete。

## 延伸閱讀

- **[RISC-V Privileged Spec](https://riscv.org/technical/specifications/) 第 3.1.9 節（mip/mie）與第 3.2 節「Machine Timer Registers (mtime/mtimecmp)」、第 3.1.15（中斷優先序）**：權威來源。它定義 mip/mie 的 bit 佈局、中斷觸發條件（`mip & mie` 且對應 mode 的 xIE）、mtime/mtimecmp 的語意、mcause 的 interrupt code 表、多中斷優先序（MEI>MSI>MTI）。本章的三開關邏輯和 CLINT timer 機制就是它的白話版。特別讀「中斷什麼時候能被 taken」那段（涉及 MIE 和 delegation）。
- **《Computer Organization and Design, RISC-V Edition》(Patterson & Hennessy) 第 4.9 節「Exceptions」的 interrupt 部分與 5.x 的 I/O**：教科書把中斷放在 exception 框架下講（呼應本章「同一套 trap 流程」），並解釋 polling vs interrupt 的取捨（為什麼 interrupt 比一直問裝置好）。它的 I/O 章講 device interrupt 怎麼進 CPU，是 PLIC 那節的前導。
- **[xv6-riscv 的 `kernel/trap.c`（clockintr / devintr）與 `kernel/start.c`（timerinit）](https://github.com/mit-pdos/xv6-riscv/tree/riscv/kernel)**：真實教學 OS 怎麼用 timer 中斷做排程——`start.c` 的 `timerinit()` 設 mtimecmp（第一次鬧鐘）、開 MTIE；`trap.c` 的 `clockintr()` 是 timer handler，它重設 mtimecmp（本章範例一 handler 的真實版）、`yield()` 換行程（time-slice 的落實！）；`devintr()` 讀 mcause 分流 timer/PLIC external，並示範 PLIC 的 `plic_claim()`/`plic_complete()`。看它就懂本章的 mini timer handler 放大成真 OS 排程器長什麼樣。
- **[SiFive Interrupt Cookbook](https://sifive.cdn.prismic.io/sifive/0d163928-2128-42be-a75a-464df65e04e0_sifive-interrupt-cookbook.pdf)**：SiFive 官方中斷實務手冊，詳講 CLINT、PLIC 的暫存器佈局與程式設定、中斷延遲怎麼算與優化、Direct vs Vectored、巢狀中斷怎麼做。它把 spec 的抽象連到真晶片的 memory map，是本章 CLINT/PLIC 從「概念」到「真的怎麼配置」的最佳橋樑。做練習 E（自刻 CLINT SV 模組）前先讀它的 CLINT 章。

下一章我們把這一路做的 CSR、trap、privilege、interrupt 全部**接進 pipelined core**——處理 trap 在五級 pipeline 裡的 flush、寫一支真正的 trap handler（.S：存 context → 處理 → mret）、讓 core 端到端跑得動。前四章是零件，Ch 35 是組裝——把「能處理例外與中斷的 CPU」真正立起來。

→ [Ch 35 讓 core 跑得動 trap handler](./35-trap-handler-integration.md)
