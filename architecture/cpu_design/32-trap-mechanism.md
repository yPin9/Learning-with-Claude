# Ch 32 — Trap 機制：exception / interrupt 進出、pipeline flush

> **目標**：把 Ch 31 做好的 CSR 用起來——當程式撞上「不能正常往下跑」的事件（執行非法指令、ECALL 系統呼叫、timer 中斷）時，硬體要能自動**進 trap**（存 mepc/mcause/mtval、翻 mstatus、跳 mtvec）、讓 handler 處理、再用 **mret 返回**。你會學清楚 exception 和 interrupt 的本質差別、trap 進入的六個硬體動作、mret 返回的細節、以及 trap 在 pipeline 裡為什麼一定要 flush。然後**用一顆真的 mini core 跑一支含 ECALL 的程式**：親眼看它進 handler、看 mcause=11、看 mepc 指向 ECALL、看 handler 執行完 mret 跳回。這是深挖章。
> **環境**：WSL + verilator 4.038 + riscv64-unknown-elf-gcc 10.2.0。trap 程式用真 assembler 組譯、mini core 真跑，所有輸出貼上。
> 如果你對 RISC-V trap 的軟體視角（handler 怎麼寫、ABI 怎麼保存 context）不熟，回看 `architecture/riscv` 課的 trap/exception 章——這章我們做硬體那半邊：CPU 怎麼「自動」進出 trap。

## 為什麼需要 trap 機制？

我們的 core 到現在都假設「程式一條接一條順順跑」。但真實執行充滿「不能順順跑」的時刻：

- **程式做了非法的事**：執行一條未定義的指令、除以零、存取沒映射的位址（Ch 28 的 page fault）、user mode 想碰特權暫存器（Ch 33）。CPU 不能裝沒事繼續跑（往下讀到的可能是垃圾），也不能直接當機——它要有辦法「停下手邊的、跳去處理、處理完回來」。
- **程式主動請求服務**：user 程式想讀檔案、想印東西——它不能直接碰硬體（沒權限），得透過 **ECALL（environment call）** 請作業系統代勞。ECALL 就是「我主動舉手，請 kernel 幫我」。
- **外部世界要 CPU 注意**：timer 到期了、鍵盤有輸入了、網卡收到封包了——這些**中斷（interrupt）**跟程式當下在算什麼無關，是外部非同步發生的，但 CPU 得能隨時放下手邊工作去回應。

這三類事件——**非法操作、主動請求、外部中斷**——RISC-V 統稱 **trap**。trap 機制就是「CPU 遇到這些事件時，自動地、可控地轉去執行處理程式，處理完能回到原點」的一整套硬體流程。沒有 trap，就沒有作業系統、沒有系統呼叫、沒有中斷回應——CPU 只能跑一支從不出錯的裸機程式。

一句話：**trap 是 CPU 面對「意外」與「請求」的統一應對機制——存好現場、跳去處理、回得來。** 這章我們把這套流程做進硬體。

## 先建立直覺：接電話

想像你正在專心寫程式（正常執行指令流），突然電話響了（trap 事件）。你不能假裝沒聽到繼續寫，也不能砸了電腦——你會：

```
   1. 記下「我剛寫到哪一行」          → 存 mepc（回來要接著寫）
   2. 記下「是誰打來、什麼事」        → 存 mcause（知道怎麼處理）
   3. 記下相關細節                    → 存 mtval（例如對方電話號碼）
   4. 掛個「處理中，勿擾」的牌子      → mstatus.MIE ← 0（先別再被打斷）
   5. 走到電話機那邊                  → 跳到 mtvec（handler 入口）
   6. ... 講完電話（handler 處理）...
   7. 拿掉「勿擾」牌、回到書桌接著寫  → mret：還原 MIE、跳回 mepc
```

關鍵直覺：

- **一定要先記下「寫到哪」**（mepc），不然講完電話你不知道回去接哪一行。這是 trap 能「回得來」的根本。
- **講電話時先掛勿擾牌**（關中斷），不然講到一半又有電話插進來，你會越陷越深、現場保存亂套。所以 trap 進入硬體會自動關中斷（MIE←0），handler 想被更高優先的中斷打斷才自己開。
- **exception（例外）像「你自己按到緊急按鈕」**——是你當前這條指令引起的，同步、可預測（同一條指令必觸發同一 exception）。**interrupt（中斷）像「外面打來的電話」**——跟你當下在做什麼無關，非同步、時機不定。兩者都走同一套「接電話」流程，只是來源不同。

mtvec 是「電話機的位置」（handler 在哪），mepc 是「你的書籤」（回哪），mcause 是「來電顯示」（什麼事），mstatus 是「勿擾牌 + 你剛才的狀態」。Ch 31 做的 CSR，這章全用上。

## 核心概念：exception vs interrupt

trap 分兩大類，搞清楚差別是這章的地基：

| 面向 | exception（例外/同步 trap）| interrupt（中斷/非同步 trap）|
|---|---|---|
| 觸發來源 | **當前這條指令**本身 | **外部事件**（timer、裝置）|
| 時機 | 同步——由某條指令觸發 | 非同步——任何時候都可能來 |
| 可重現 | 是（同一條指令必觸發同一 exception）| 否（時機不定）|
| mepc 存的 PC | **觸發的那條指令**（handler 決定要不要跳過）| **下一條還沒執行的指令**（回來要接著跑）|
| mcause 最高位 | 0 | **1**（區分是中斷）|
| 例子 | illegal instruction、ECALL、page fault、misaligned | timer int、software int、external int |

最關鍵的三個差別：

**1. mcause 的最高位（bit31 for RV32）區分兩者。** interrupt 的 mcause bit31=1，exception 的 bit31=0。所以：
- illegal instruction 的 mcause = `2`（bit31=0）
- M-mode ECALL 的 mcause = `11`（bit31=0）
- machine timer interrupt 的 mcause = `0x80000007`（bit31=1，低位 7）

handler 讀 mcause 第一件事就是看最高位：是中斷（外部事件、通常回去重試被打斷的指令）還是例外（當前指令出事、通常要處理或跳過它）。

**2. mepc 存的 PC 意義不同。** exception 存的是**觸發的那條指令**的 PC——因為 handler 可能要「跳過它」（例如 ECALL：處理完系統呼叫，要回到 ECALL 的**下一條**，所以 handler 得自己把 mepc+4）；也可能要「重試它」（例如 page fault：把頁換回來後重跑同一條）。interrupt 存的是**下一條還沒執行的指令**——因為被打斷的指令已經（或即將）完成，回來從下一條繼續。這個差別讓 handler 對 mepc 的處理不同（下面範例會看到 ECALL handler 要 +4）。

**3. exception 由指令「引起」，interrupt「插入」指令之間。** exception 是某條指令執行到一半發現不對（例如 decode 發現 opcode 非法）；interrupt 是在兩條指令的邊界插進來（當前指令乾淨完成或乾淨丟棄後才進 trap）。這影響 pipeline 怎麼 flush（下面談）。

本章的真跑範例聚焦 exception（ECALL、illegal instruction，最容易觸發和觀察），interrupt 的完整機制留 Ch 34（需要 CLINT 產生 timer int）。但兩者共用同一套「進 trap / mret 返回」硬體流程——這章把流程做通，Ch 34 只是換個觸發源。

## 核心概念：trap 進入的六個硬體動作

當 core 決定「這一拍要進 trap」（偵測到 exception 或有 pending 且 enabled 的 interrupt），硬體在**一個 clk 邊沿**同時做這六件事（全部自動，不需要任何指令）：

```
   trap 進入（一拍內硬體全做完）：
   ┌──────────────────────────────────────────────────────┐
   │ 1. mepc   ← 出事的 PC                                  │
   │      exception：觸發指令的 PC；interrupt：下一條的 PC   │
   │ 2. mcause ← 原因碼                                     │
   │      exception：bit31=0 + 例外碼；interrupt：bit31=1+碼 │
   │ 3. mtval  ← 附加資訊                                   │
   │      illegal：惹禍指令；page fault：出錯位址；其他：0   │
   │ 4. mstatus.MPIE ← mstatus.MIE   （備份舊的中斷開關）    │
   │ 5. mstatus.MIE  ← 0             （關中斷，掛勿擾牌）    │
   │    mstatus.MPP  ← 當前特權       （備份特權，Ch 33）    │
   │ 6. PC     ← mtvec               （跳到 handler 入口）   │
   └──────────────────────────────────────────────────────┘
```

第 4、5 步是關鍵配對：**把舊 MIE 備份到 MPIE、再把 MIE 清 0**。為什麼？進 handler 要先關中斷（不然 handler 存 context 存到一半又被中斷打斷，現場會亂）；但關之前要記住「原本開沒開」，好在 mret 時還原。這正是 Ch 31 我們在 csr_file 裡實作、範例一驗過的「mstatus after trap = 0x1880」那個動作。

（mtvec 有 Direct 和 Vectored 兩種 MODE：Direct 所有 trap 都跳同一個入口，handler 自己讀 mcause 分流；Vectored 讓 interrupt 依 cause 跳不同入口。本課用最單純的 Direct，跳 `{mtvec[31:2], 2'b00}`。）

## 核心概念：mret 返回

handler 處理完，用 **mret**（machine return，一條特權指令）返回。mret 的硬體動作是 trap 進入的逆操作：

```
   mret 返回（一拍內硬體全做完）：
   ┌──────────────────────────────────────────┐
   │ 1. mstatus.MIE  ← mstatus.MPIE  （還原中斷開關）│
   │ 2. mstatus.MPIE ← 1             （備份位重設）  │
   │ 3. 特權         ← mstatus.MPP    （還原特權，Ch 33）│
   │ 4. PC           ← mepc          （跳回被打斷處）│
   └──────────────────────────────────────────┘
```

第 1 步還原 MIE：trap 進入時關掉的中斷，mret 時打開（如果 trap 前是開的）。第 4 步跳回 mepc——這就是 trap「回得來」的落實。

一個常見混淆：**mret 跳的是 mepc 的當前值，不是 trap 進入時存的值。** 如果 handler 在返回前修改了 mepc（例如 ECALL 要跳過自己，handler 做 `mepc += 4`），mret 就跳到修改後的位址。這給了 handler 決定「重試 vs 跳過」的能力——重試就別動 mepc，跳過就 +4。下面範例正是這樣。

## 底層機制：trap 為什麼一定要 flush pipeline

前面把 trap 當「單週期」動作講（一拍做完），但我們的 core 是五級 pipeline。pipeline 裡同時有五條指令在飛，trap 一發生，**那些「排在出事指令後面、已經進了 pipeline 但還不該執行」的指令必須全部作廢**——這就是 flush。

```
   pipeline 中 EX 級的指令觸發 illegal exception：

   拍 N：  IF        ID       EX(illegal!)  MEM      WB
          instr+2   instr+1  BAD           ...      ...
                    ↑那些後面的指令已經進來了，但它們不該執行！

   trap 決定進入 → 必須：
   ┌─────────────────────────────────────────────┐
   │ 1. flush IF/ID/EX 裡「BAD 之後」的指令（塞 bubble）│
   │ 2. mepc ← BAD 的 PC                            │
   │ 3. PC   ← mtvec                                │
   │ 下一拍 handler 從 IF 開始重新灌入 pipeline      │
   └─────────────────────────────────────────────┘
```

為什麼一定要 flush？因為 pipeline 是「猜著往下抓指令」——BAD 還在 EX 級時，`instr+1`、`instr+2` 已經被 fetch/decode 進 pipeline 了。但既然 BAD 觸發 trap 要跳走，這些後續指令**根本不該執行**（它們是 BAD 的下一條、下下條，不是 handler）。若不 flush，它們會繼續往 WB 走、寫壞暫存器和記憶體——就像 Ch 18 branch taken 時要 flush 掉 fall-through 的錯誤指令，道理一模一樣。**trap 就是一種「非預期的控制轉移」，和 branch/jump 共用同一套 flush 機制。**

exception 和 interrupt 的 flush 時機略有不同：
- **exception**：由某條指令引起，flush 掉它**後面**的指令；出事指令本身通常被**壓制**（不讓它寫回，因為它出事了）。
- **interrupt**：插在指令邊界，通常讓當前 pipeline 的某條指令乾淨完成、把它的**下一條**當作「被打斷點」（mepc 存下一條），flush 掉更後面的。

本章的 mini core 用單週期式的簡化模型（一拍一條指令、沒有真的 pipeline 級間 flush），把 trap 的**語意**（存 context、跳 mtvec、mret 返回）做對、驗清楚。真正的 pipeline flush 整合留到 Ch 35——那時我們把這套語意接進五級 core，處理級間 flush 的接縫。先在乾淨的模型裡把「進 trap / 返回」搞懂，再進 pipeline 才不會被 flush 的時序細節淹沒。

## 實作：mini core 的 trap 邏輯

我們用一顆教學用的單週期 mini core（支援 RV32I 常用子集 + Zicsr + trap/mret）跑真程式。它的 trap 相關邏輯核心是這幾段（完整 `minicore.sv` 在本課倉庫；這裡摘 trap 決策部分）：

```systemverilog
// 例外偵測
logic is_ecall, is_mret, known_op, is_illegal;
assign is_ecall = (opcode==7'b1110011) && (f3==3'b000) && (instr[31:20]==12'h000);
assign is_mret  = (opcode==7'b1110011) && (f3==3'b000) && (instr[31:20]==12'h302);
// known_op：列舉所有合法 opcode；沒中就是 illegal
assign is_illegal = !known_op;

// 中斷 pending（Ch 34 詳談；這裡先列出）：全域開 && timer 開 && timer 拉高
logic irq_pending;
assign irq_pending = mstatus[3] & mie[7] & timer_irq;

// trap 決策：三種來源任一成立就進 trap
logic take_trap;
assign take_trap = is_ecall | is_illegal | irq_pending;

// mcause 依來源給碼（interrupt 最高位=1）
logic [31:0] trap_cause;
always_comb begin
    if (irq_pending)     trap_cause = 32'h80000007; // machine timer interrupt
    else if (is_illegal) trap_cause = 32'd2;         // illegal instruction
    else                 trap_cause = 32'd11;        // M-mode ECALL
end

// next-PC：trap 跳 mtvec、mret 跳 mepc，優先於一般流程
logic [31:0] next_pc;
always_comb begin
    if (take_trap)                          next_pc = {mtvec[31:2], 2'b00}; // 進 trap
    else if (is_mret)                       next_pc = mepc;                 // mret 返回
    else if (opcode==7'b1101111)            next_pc = pc + immJ;            // jal
    /* ...其他一般流程... */
    else                                    next_pc = pc + 4;
end

// clk 邊沿：進 trap 存 context 翻 mstatus；mret 還原
always_ff @(posedge clk) begin
    if (rst) begin /* ...清零... */ end
    else begin
        pc <= next_pc;
        if (take_trap) begin
            mepc   <= pc;                       // exception：存觸發指令的 PC
            mcause <= trap_cause;
            mtval  <= is_illegal ? instr : 32'd0; // illegal 存惹禍指令
            mstatus[7]     <= mstatus[3];        // MPIE ← MIE
            mstatus[3]     <= 1'b0;               // MIE  ← 0（關中斷）
            mstatus[12:11] <= 2'b11;              // MPP  ← M
        end else if (is_mret) begin
            mstatus[3]     <= mstatus[7];        // MIE  ← MPIE（還原）
            mstatus[7]     <= 1'b1;
            mstatus[12:11] <= 2'b00;
        end else begin
            /* ...一般寫回：CSR 指令、regfile、store... */
        end
    end
end
```

這就是 Ch 31 csr_file 那套 trap/mret 邏輯，內嵌進一顆能取指、執行的 core。注意 **mepc 存的是 `pc`（觸發指令自己的 PC）**——這是 exception 的定義。ECALL handler 想跳過它就自己 +4。

## 範例一：ECALL 進 handler、mret 返回

寫一支 assembly，設好 mtvec、執行 ECALL 觸發 trap，handler 處理後 mret 回來：

```asm
    .section .text
    .globl _start
_start:
    lui   x2, 0x80001        # x2 = 0x80001000（stack/data base）
    la    x5, trap_handler   # 載入 handler 位址
    csrw  mtvec, x5          # mtvec = trap_handler（電話機位置）
    li    x10, 111           # x10 = 111（ECALL 前的標記）
    ecall                    # 觸發 environment call → trap
after_ecall:
    li    x12, 222           # 返回後執行：x12 = 222（證明回得來）
halt:
    j     halt

    .align 2
trap_handler:
    csrr  x6, mcause         # x6 = mcause（應為 11 = M-mode ECALL）
    csrr  x7, mepc           # x7 = mepc（指向 ecall 那條）
    addi  x7, x7, 4          # +4：跳過 ecall（否則 mret 回來又觸發）
    csrw  mepc, x7           # 寫回修改後的 mepc
    li    x11, 333           # x11 = 333（handler 執行過的標記）
    mret                     # 返回（跳到修改後的 mepc = after_ecall）
```

組譯（真 assembler，看 ECALL 和 mret 的 encoding）：

```bash
riscv64-unknown-elf-gcc -march=rv32i -mabi=ilp32 -nostdlib -Ttext=0x80000000 -o trap.elf trap.S
riscv64-unknown-elf-objdump -d trap.elf
```

```
80000000 <_start>:
80000000:	80001137          	lui	sp,0x80001
80000004:	00000297          	auipc	t0,0x0
80000008:	01c28293          	addi	t0,t0,28 # 80000020 <trap_handler>
8000000c:	30529073          	csrw	mtvec,t0
80000010:	06f00513          	li	a0,111
80000014:	00000073          	ecall
80000018 <after_ecall>:
80000018:	0de00613          	li	a2,222
8000001c <halt>:
8000001c:	0000006f          	j	8000001c <halt>
80000020 <trap_handler>:
80000020:	34202373          	csrr	t1,mcause
80000024:	341023f3          	csrr	t2,mepc
80000028:	00438393          	addi	t2,t2,4
8000002c:	34139073          	csrw	mepc,t2
80000030:	14d00593          	li	a1,333
80000034:	30200073          	mret
```

`ecall` 的 encoding 是 `0x00000073`（全 0 的 SYSTEM 指令），`mret` 是 `0x30200073`。這兩個 encoding 我們的 core 就是靠 `instr[31:20]==0x000`（ecall）和 `==0x302`（mret）辨識的。

把 `.text` 抽成 hex 餵進 mini core，每拍印出 PC、trap 訊號、mcause/mepc、和標記暫存器 x10/x11/x12：

```bash
riscv64-unknown-elf-objcopy -O binary --only-section=.text trap.elf trap.bin
od -An -tx4 -w4 -v trap.bin | sed 's/ //g' > prog_trap.hex
verilator --cc minicore.sv --exe mc_tb.cpp --Mdir obj_mc \
    -Wno-WIDTH -Wno-UNUSED -Wno-UNOPTFLAT -GINIT_FILE='"prog_trap.hex"'
make -s -C obj_mc -f Vminicore.mk Vminicore
./obj_mc/Vminicore 20
```

真跑輸出（每行是「這一拍 core 準備執行的指令」的觀測；`trap=1` 那拍表示這一拍決定進 trap）：

```
cyc 4 pc=80000010 trap=0 mcause=00000000 mepc=00000000 x10=0   x11=0   x12=0
cyc 5 pc=80000014 trap=1 mcause=00000000 mepc=00000000 x10=111 x11=0   x12=0
cyc 6 pc=80000020 trap=0 mcause=0000000b mepc=80000014 x10=111 x11=0   x12=0
cyc 7 pc=80000024 trap=0 mcause=0000000b mepc=80000014 x10=111 x11=0   x12=0
cyc 8 pc=80000028 trap=0 mcause=0000000b mepc=80000014 x10=111 x11=0   x12=0
cyc 9 pc=8000002c trap=0 mcause=0000000b mepc=80000014 x10=111 x11=0   x12=0
cyc10 pc=80000030 trap=0 mcause=0000000b mepc=80000018 x10=111 x11=0   x12=0
cyc11 pc=80000034 trap=0 mcause=0000000b mepc=80000018 x10=111 x11=333 x12=0
cyc12 pc=80000018 trap=0 mcause=0000000b mepc=80000018 x10=111 x11=333 x12=0
cyc13 pc=8000001c trap=0 mcause=0000000b mepc=80000018 x10=111 x11=333 x12=222
...
FINAL x10=111 x11=333 x12=222 mcause=0000000b mepc=80000018
```

一拍一拍讀懂整個 trap 生命週期：

- **cyc4→5**：正常執行到 `li x10, 111`（PC 0x10 那條的效果讓 x10=111），下一條 PC 走到 0x14（`ecall`）。
- **cyc5（trap=1）**：core 準備執行 PC=0x14 的 `ecall`，偵測到 `is_ecall`，決定進 trap。這一拍不執行 ecall 的「正常」動作，而是：存 mepc←0x14（ecall 自己的 PC）、mcause←11、跳 mtvec。
- **cyc6**：PC 跳到 **0x20（trap_handler）**！mcause 顯示 **0x0b=11**（M-mode ECALL）、mepc 顯示 **0x80000014**（指向 ecall）。trap 進入完成，handler 開跑。
- **cyc6~9**：handler 執行 `csrr mcause`、`csrr mepc`、`addi mepc+4`、`csrw mepc`。
- **cyc10**：mepc 從 0x14 **變成 0x18**（handler 做了 +4，跳過 ecall）。
- **cyc11**：handler `li x11, 333` 生效，x11=333（證明 handler 真的跑了）。
- **cyc12（mret）**：執行 PC=0x34 的 `mret`，next_pc ← mepc = 0x18。
- **cyc12→13**：PC 跳回 **0x18（after_ecall）**！回到 ECALL 的下一條。
- **cyc13**：`li x12, 222` 生效，x12=222——**證明 trap 完整返回，程式接著往下跑。**

最終狀態 `x10=111, x11=333, x12=222`：三個標記全在，代表「ECALL 前跑了、handler 跑了、返回後跑了」——trap 進出一氣呵成。**這就是系統呼叫的硬體骨架**：user 程式 ecall（舉手）、跳進 kernel handler（處理）、mret 回到 user 下一條（繼續）。

## 範例二：illegal instruction，看 mcause=2 和 mtval

換一個 exception 來源：執行一條非法指令（未定義 opcode）。這示範 exception 的另一個特性——**mtval 存下惹禍的指令**。程式在 ECALL 位置改放一條垃圾 `.word`：

```asm
_start:
    la    x5, trap_handler
    csrw  mtvec, x5
    li    x10, 111
    .word 0xffffffff        # 非法指令（未定義 opcode）→ illegal instruction trap
after_bad:
    li    x12, 222
halt:
    j     halt
    .align 2
trap_handler:
    csrr  x6, mcause         # 應 = 2
    csrr  x7, mtval          # 應 = 0xffffffff（惹禍指令）
    csrr  x8, mepc
    addi  x8, x8, 4          # 跳過惹禍指令
    csrw  mepc, x8
    li    x11, 333
    mret
```

真跑輸出（節錄關鍵拍）：

```
cyc 4 pc=80000010 trap=1 mcause=00000000 mepc=00000000 x10=111 x11=0   x12=0
cyc 5 pc=8000001c trap=0 mcause=00000002 mepc=80000010 x10=111 x11=0   x12=0
...
cyc11 pc=80000034 trap=0 mcause=00000002 mepc=80000014 x10=111 x11=333 x12=0
cyc12 pc=80000014 trap=0 mcause=00000002 mepc=80000014 x10=111 x11=333 x12=0
cyc13 pc=80000018 trap=0 mcause=00000002 mepc=80000014 x10=111 x11=333 x12=222
FINAL x10=111 x11=333 x12=222 mcause=00000002 mepc=80000014
```

- **cyc4（trap=1）**：core 準備執行 PC=0x10 的 `.word 0xffffffff`，偵測 `is_illegal`（opcode 不在合法列表），決定進 trap。
- **cyc5**：PC 跳到 0x1c（handler，這版 handler 從 0x1c 開始）、mcause=**2**（illegal instruction，注意 bit31=0，是 exception 不是 interrupt）、mepc=**0x80000010**（惹禍指令的 PC）。
- handler 讀 mtval 會拿到 `0xffffffff`（惹禍的那條指令原封不動存進 mtval，讓 handler 知道「是哪條指令非法」，可用來模擬指令、報錯訊息等）。
- **cyc12→13**：mret 跳回 mepc（handler +4 後 = 0x14），x12=222，返回成功。

對比範例一（ECALL, mcause=11）和範例二（illegal, mcause=2）：**同一套 trap 硬體流程，只是 mcause 不同、mtval 有沒有值不同**。這印證了「trap 是統一機制、來源決定 cause」。

## 對比取捨：trap 設計的幾個選擇

| 面向 | 本課 mini core | 真實 pipeline core（Ch 35+）|
|---|---|---|
| trap 偵測級 | 單週期，一拍決定 | 多級都可能偵測（decode 抓 illegal、MEM 抓 page fault）|
| pipeline flush | 無（單週期不需要）| 必須，flush 出事指令之後的所有級 |
| mepc 精確性 | 天然精確 | 要保證「precise exception」：出事前的指令全完成、之後的全作廢 |
| mtvec MODE | Direct（都跳同一入口）| Direct 或 Vectored（中斷依 cause 分流）|
| 多個 trap 同拍 | 不會發生 | 要定優先序（同拍有 exception 又有 interrupt，通常 exception 先）|

**precise exception（精確例外）** 是 pipeline core 最重要的性質：trap 發生時，架構狀態必須「乾淨」——出事指令**之前**的指令全部完成、出事指令**及之後**的全部沒生效。這樣 mepc 才能精確指向「該從哪重來」，handler 修好後 mret 回去才對。單週期天然精確（一次只一條指令在飛）；pipeline 要靠精心的 flush + 壓制寫回才做到，這是 Ch 35 的核心難點。本章先在單週期把語意做對，Ch 35 再處理 pipeline 的精確性。

## 踩雷區

**雷 1：以為 exception 和 interrupt 的 mepc 意義一樣。**
- 錯誤直覺：「mepc 就是出事的 PC，兩種 trap 都一樣」。
- 正確認識：**exception 的 mepc 是「觸發的那條指令」，interrupt 的 mepc 是「下一條還沒執行的指令」**。差別來自語意：exception 是當前指令出事（handler 可能要重試它或跳過它，所以存它自己）；interrupt 是插在指令間（被打斷的指令已完成，回來從下一條繼續，所以存下一條）。範例一 ECALL 存的是 ecall 自己的 PC，handler 得自己 +4 跳過；若你在 timer interrupt handler 也 +4，會**跳過一條被打斷的合法指令**，程式行為錯亂。搞清楚你在處理哪種 trap，再決定要不要動 mepc。

**雷 2：忘記 handler 要自己 +4（對 ECALL 這類）。**
- 錯誤直覺：「mret 會自動跳到下一條」。
- 正確認識：mret 跳的是 **mepc 的當前值**，硬體不會自動 +4。ECALL 的 mepc 存的是 ecall 自己，若 handler 不 `mepc += 4` 就 mret，會**跳回 ecall 再次執行 → 又觸發 trap → 無窮迴圈**。範例一 handler 明確做了 `addi x7, x7, 4; csrw mepc, x7`。這是「跳過 vs 重試」的軟體決策：ECALL、illegal 通常跳過（+4）；page fault 通常重試（不動 mepc，修好頁再跑同一條）。把「該不該動 mepc」交給 handler，是 trap 機制的彈性所在。

**雷 3：以為進 trap 不用關中斷。**
- 錯誤直覺：「進 handler 中斷還開著沒差」。
- 正確認識：trap 進入硬體會自動 **MIE←0（關中斷）**，這是刻意的。若不關，handler 存 context（把暫存器存到 stack）存到一半，又來一個中斷，第二個 trap 會覆蓋 mepc/mcause（它們只有一份！），第一個 trap 的現場就**永久遺失**，回不去了。所以進 handler 先關中斷、把 context 存進 stack 後，handler 若允許巢狀中斷才自己重開 MIE。範例一雖然沒巢狀，但硬體照樣自動關了（mstatus.MIE→0），這是 Ch 31 驗過的 mstatus=0x1880。低估這點，你的 handler 在中斷密集時會神秘地丟失現場。

**雷 4：以為 trap 在 pipeline 裡不用 flush。**
- 錯誤直覺：「trap 就是跳個 PC，和 branch 一樣簡單，不用特別處理後面的指令」。
- 正確認識：trap 是**非預期的控制轉移**，出事指令後面那些「已經進 pipeline 但不該執行」的指令**必須 flush**（塞 bubble），否則它們會繼續往 WB 走、寫壞暫存器/記憶體——這叫破壞了 precise exception。這和 branch taken 要 flush fall-through 是同一套機制（Ch 18）。本章 mini core 是單週期沒這問題，但別把「單週期沒事」誤當「pipeline 也沒事」。Ch 35 接進 pipeline 時，flush 的接縫（哪些級要清、出事指令怎麼壓制寫回、同拍多 trap 怎麼排序）是主要難點。單週期跑通只是把語意做對，pipeline 的時序正確性是另一關。

## 進階延伸

- **delegation：把 trap 委派給 S mode（medeleg/mideleg）**：真實系統有 M/S/U 三個特權（Ch 33）。預設所有 trap 都進 M mode（我們現在這樣），但這對 OS 很浪費——user 程式的 page fault 應該直接進 kernel（S mode）處理，不必先繞到 M mode 的 machine handler 再轉。RISC-V 用 `medeleg`（exception delegation）和 `mideleg`（interrupt delegation）兩個 CSR，讓 M mode 把特定 trap **委派**給 S mode 直接處理——委派後那類 trap 直接進 stvec（S mode 的 trap vector），存 sepc/scause 而非 mepc/mcause。這是 Ch 33 淺提、真跑 Linux 必備的機制。
- **vectored mtvec：中斷依 cause 分流**：mtvec 低 2 bit 是 MODE。MODE=0（Direct）所有 trap 都跳同一入口，handler 讀 mcause 用一堆 if/else 分流——慢。MODE=1（Vectored）讓 **interrupt** 跳到 `base + 4*cause`（每個中斷源一個入口），省掉軟體分流，中斷延遲更低（exception 仍跳 base）。高效能中斷系統用 Vectored。本課用 Direct 求簡單，但知道有這選項。
- **WFI（Wait For Interrupt）**：real core 常有 `wfi` 指令——CPU 執行到它就進入低功耗待命，直到有中斷 pending 才醒來繼續。這是 timer interrupt 驅動的 tickless idle、省電的基礎。我們的 mini core 沒實作（idle loop 用 busy `j loop` 空轉），但真實 OS 的 idle task 就是一個 `wfi` 迴圈。加它不難：`wfi` 時凍住 PC，等 `irq_pending` 拉高才解凍。
- **trap 的 hazard：CSR 讀寫和 trap 同拍**：接進 pipeline 後有個微妙 hazard——若一條 CSR 指令正在寫 mtvec，同一拍又發生 trap 要讀 mtvec 跳過去，讀到的是舊值還是新值？precise exception 要求：trap 看到的是「出事點之前」所有指令的效果。所以正在寫 mtvec 的那條若排在出事指令之前且已完成，trap 該用新值；若它就是被 flush 的指令之一，該用舊值。這種 CSR-vs-trap 的時序是 pipeline trap 整合（Ch 35）最容易出錯的角落，工業 core 有專門的邏輯處理。

## 本章重點整理

- **trap 是 CPU 對「意外與請求」的統一機制**：exception（當前指令引起，同步）、interrupt（外部事件，非同步），共用「存現場→跳 handler→mret 返回」的流程。用「接電話」記憶。
- **exception vs interrupt**：mcause 最高位區分（interrupt bit31=1）；mepc 存的 PC 不同（exception 存觸發指令、interrupt 存下一條）；exception 可重現、interrupt 時機不定。
- **trap 進入六動作**（一拍硬體全做）：mepc←出事PC、mcause←原因、mtval←附加資訊、MPIE←MIE、MIE←0（關中斷）、MPP←特權、PC←mtvec。
- **mret 返回**：MIE←MPIE、PC←mepc。跳的是 mepc **當前值**——handler 改了 mepc（如 +4 跳過）就跳改後的。
- **trap 一定要 flush pipeline**：出事指令後面「已進 pipeline 但不該執行」的指令必須作廢，保證 precise exception。這和 branch flush 同機制。本章單週期不需要，Ch 35 pipeline 才處理。
- **真跑驗證**：ECALL → mcause=11、mepc 指向 ecall、handler +4、mret 跳回 after_ecall（x10/x11/x12 三標記全在）；illegal → mcause=2、mtval=惹禍指令。同一套流程，不同 cause。

## 自我檢核

- [ ] 我能說出 exception 和 interrupt 的三個本質差別（來源、mcause 最高位、mepc 存什麼），並各舉兩個例子。
- [ ] 我能列出 trap 進入的六個硬體動作，並解釋為什麼第 4/5 步要「先備份 MIE 到 MPIE、再把 MIE 清 0」。
- [ ] 我能追出範例一從 cyc5（trap=1）到 cyc13（x12=222）的每一步，說明 PC、mcause、mepc 各拍怎麼變。
- [ ] 我能解釋為什麼 ECALL handler 要自己 `mepc += 4`，而 page fault handler 通常不動 mepc。
- [ ] 我能說明為什麼 trap 在 pipeline 裡一定要 flush，以及它和 branch flush 是同一套機制。
- [ ] 我能區分範例一（ECALL, mcause=11）和範例二（illegal, mcause=2, mtval=惹禍指令）用的是同一套 trap 流程。

## 延伸閱讀

- **[RISC-V Privileged Spec](https://riscv.org/technical/specifications/) 第 3.1.6～3.1.7 節（mstatus 的 trap 行為）與 3.3 節「Machine-Mode Privileged Instructions」（MRET）**：權威來源。它精確定義 trap 進入時 MPIE/MIE/MPP 怎麼變、mret 怎麼還原、mcause 的 Interrupt bit 與 Exception Code 表（哪個碼對應哪種 trap）。本章的六個進入動作和 mret 就是它的白話版，實作時以它逐條對照。特別讀 mcause 的 code 表，記住 illegal=2、ECALL from M=11、machine timer int=7(+bit31)。
- **《Computer Organization and Design, RISC-V Edition》(Patterson & Hennessy) 第 4.9 節「Exceptions」**：從 pipeline datapath 角度講 exception 怎麼進 pipeline、為什麼要 flush、precise exception 怎麼維持。它畫的「exception 在 pipeline 各級的處理」圖正是 Ch 35 整合的藍圖。本章的 flush 直覺（和 branch 同機制）在這裡有 datapath 級的細節。
- **[xv6-riscv 的 `kernel/trap.c` 和 `kernel/kernelvec.S`](https://github.com/mit-pdos/xv6-riscv/tree/riscv/kernel)**：真實教學 OS 的 trap handler。`kernelvec.S` 是純組語的 trap entry（存全部暫存器到 stack、呼叫 C handler、還原、mret）——正是本章範例 handler 的完整工業版；`trap.c` 的 `devintr()` 讀 mcause 分流 timer/外部中斷/exception，示範「讀 mcause 判斷 trap 種類」的真實寫法。看它你就懂本章的 mini handler 放大成真 OS 長什麼樣。
- **[SiFive Interrupt Cookbook](https://sifive.cdn.prismic.io/sifive/0d163928-2128-42be-a75a-464df65e04e0_sifive-interrupt-cookbook.pdf)**：SiFive 官方的中斷/trap 實務手冊，從硬體廠角度講 trap 進出、Direct vs Vectored mtvec、中斷延遲怎麼算、handler 該怎麼寫才快。它把 spec 的抽象規定連到「真晶片上怎麼配置」，是本章與 Ch 34 之間最好的實務橋樑。

下一章我們補上 trap 的另一半舞台：特權模式（M/S/U）。ECALL 為什麼在不同 mode 有不同 mcause？user mode 執行特權指令為什麼會變成 illegal exception？privilege check 怎麼做進硬體？trap 常常就是 privilege 違規的產物——搞懂特權，才真懂 trap 從哪來。

→ [Ch 33 M/S/U mode + privilege check](./33-privilege-modes.md)
