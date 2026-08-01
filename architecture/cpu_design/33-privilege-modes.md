# Ch 33 — M/S/U mode + privilege check

> **目標**：搞懂 CPU 為什麼要分「特權等級」——不是每段程式碼都該有權碰硬體、改中斷開關、換頁表。你會學 RISC-V 的三個特權模式（M/S/U，machine/supervisor/user）各自能幹嘛、CPU 怎麼記錄「當前特權」、privilege check 怎麼在硬體裡做（user mode 執行特權指令或碰 M-level CSR → 變成 illegal exception）、ECALL/EBREAK 在不同 mode 的 mcause 為什麼不同、以及 mstatus.MPP 怎麼在 trap 進出時記住並還原特權。然後**用一顆帶 privilege 的 mini core 真跑**：從 M mode 用 mret 掉到 U mode、在 U mode 碰特權 CSR 觸發 illegal trap、ECALL from U 拿到 mcause=8（vs from M=11）。delegation（medeleg/mideleg）淺提。
> **環境**：WSL + verilator 4.038 + riscv64-unknown-elf-gcc 10.2.0。特權轉換與 check 皆真跑貼輸出。
> 如果你對 RISC-V 三個特權模式的定位、每個 mode 該跑什麼軟體（M=firmware、S=kernel、U=app）不熟，回看 `architecture/riscv` 課的 privileged 章——這章我們做硬體的特權檢查機制。

## 為什麼需要特權模式？

我們的 core 到現在，任何指令都能做任何事——改 mtvec、關中斷、（未來）換頁表。裸機單一程式這樣沒問題。但只要你想跑作業系統，馬上撞牆：

- **user 程式不該能碰硬體控制**：如果一個 user app 能隨手 `csrw mtvec, x5` 改掉 trap handler 入口，它就能劫持整個系統的 trap 處理——所有中斷、系統呼叫都被它接管。它能 `csrw satp` 改頁表，看到別的行程的記憶體（Ch 28 的隔離就白做了）。它能關中斷讓 timer 失效，霸佔 CPU 永不放手。**user 程式必須被關在一個「碰不到硬體控制」的籠子裡。**
- **但總得有人能碰**：作業系統（kernel）要設 trap handler、管頁表、調度行程——它需要這些權力。firmware（開機時最底層那段）要初始化硬體、設定 delegation——它需要更高的權力。
- **權力要分層**：不是「有權/無權」二分，而是分層——最底層 firmware 權力最大、kernel 次之、user app 最小。上層能做下層能做的一切加更多，下層碰上層的東西就違規。

RISC-V 用**三個特權模式**解決：**M mode（machine，最高權，firmware/SBI）**、**S mode（supervisor，中權，OS kernel）**、**U mode（user，最低權，app）**。CPU 隨時處在其中一個 mode，**當前 mode 決定這條指令能不能執行、能不能碰某個 CSR**。碰了不該碰的，硬體不讓它得逞——變成 illegal instruction exception（一種 trap，Ch 32）。

一句話：**特權模式是硬體實施的權力分層——user 被關在籠子裡，想越權就觸發 trap 讓上層處理。** 這是作業系統能保護自己、隔離 user 的硬體地基。

## 先建立直覺：公司的門禁卡

把三個特權模式想成公司的門禁權限：

```
   ┌─────────────────────────────────────────────────┐
   │ M mode（machine）= 大樓管理員（最高權）           │
   │   能進所有房間、改門禁系統本身、設誰能進哪         │
   │   跑：firmware / SBI（開機、最底層硬體管理）       │
   ├─────────────────────────────────────────────────┤
   │ S mode（supervisor）= 部門主管（中權）            │
   │   能進本部門所有房間、管本部門員工、但改不了門禁系統 │
   │   跑：OS kernel（管行程、頁表、系統呼叫）           │
   ├─────────────────────────────────────────────────┤
   │ U mode（user）= 一般員工（最低權）                │
   │   只能進自己的辦公室，碰不到機房、門禁室            │
   │   跑：user app（你的程式）                         │
   └─────────────────────────────────────────────────┘
```

- **員工（U）刷卡想進機房（碰 M-level CSR）**：門不開，警報響（illegal exception）——保全（trap handler，跑在 M mode）過來處理。這正是本章要做的 privilege check。
- **員工想找主管辦事（ECALL from U）**：他按對講機（ecall），主管（kernel）來處理。這是系統呼叫。ECALL 從不同層按，接電話的人不同——所以 mcause 不同（from U=8、from S=9、from M=11），好讓上層知道「誰在請求」。
- **升降權**：員工升成主管（U→S）或降回員工（S→U）不是自己說了算——要透過正式流程（trap 往上升、mret 往下降），而且 CPU 用 **MPP** 這個欄位記住「你原本是什麼身分」，事後好還原。

門禁的核心是「當前身分（current privilege）決定你能碰什麼」。CPU 用一個 2-bit 暫存器記當前 mode（`priv`），每條指令、每次 CSR 存取都拿它來檢查。

## 核心概念：三個模式的權力與編碼

| 模式 | 編碼 | 全名 | 跑什麼 | 權力 |
|---|---|---|---|---|
| U | `2'b00` | User | user app | 最低：只能算數、load/store 自己的記憶體、ecall/ebreak |
| S | `2'b01` | Supervisor | OS kernel | 中：+ 管頁表（satp）、S-level CSR、S-level 中斷 |
| M | `2'b11` | Machine | firmware / SBI | 最高：+ 所有 CSR、設 delegation、直接碰硬體 |

（沒有 `2'b10`——保留。）

三個模式是**巢狀的權力**：M ⊃ S ⊃ U。高權能做低權的一切加更多。RISC-V 規定**只有 M mode 是必須實作的**——最小的 RISC-V core 只有 M mode（我們前 32 章就是純 M mode 在跑）。加 U mode 能跑「受限的 user 程式」（M+U 常見於嵌入式）；再加 S mode 才能跑帶頁表隔離的完整 OS（Linux 需要 M+S+U 三個）。

本課主線是 **M + U 兩個模式**（最能講清 privilege check 的核心，又不用實作 S mode 一整套 CSR）。S mode 的機制（stvec/sepc/scause/satp、delegation）在關鍵處對照提及，讓你知道怎麼延伸。

**當前特權（current privilege）** 是硬體的一個 2-bit 狀態暫存器（我們叫 `priv`）：
- reset 後 `priv = M`（`2'b11`）——CPU 開機在最高權（firmware 先跑）。
- **trap 一律進 M mode**（本課簡化；有 delegation 時可進 S）——出事就升到最高權處理。
- **mret 降回 mstatus.MPP 記的 mode**——handler 處理完，還原到 trap 前的身分。

## 核心概念：privilege check 怎麼做進硬體

privilege check 的本質：**在執行一條指令前，用「當前 priv」和「這條指令/CSR 要求的最低特權」比對，不夠就變 illegal exception。** 兩個主要檢查點：

**1. 特權指令的檢查。** 某些指令只有夠高的 mode 能執行：
- `mret`：只有 M mode 能執行（它操作 machine 狀態）。U/S mode 執行 `mret` → **illegal exception**。
- `sret`：M/S 能執行，U 不能。
- `sfence.vma`（刷 TLB）：M/S 能，U 不能。
- `wfi`：可配置。

我們的 core 檢查很直接：`priv == U && is_mret` → illegal。

**2. CSR 存取的檢查——藏在 CSR 位址裡。** 這是 Ch 31 進階延伸埋的伏筆：**CSR 的 12-bit 位址，bit[9:8] 編了「存取所需的最低特權」**：

```
   CSR 位址 [11:10] = 讀寫屬性（11=唯讀）
            [9:8]  = 最低特權（00=U、01=S、11=M）
   例：
   mstatus = 0x300 → bit[9:8] = 0b11 → 要 M mode 才能存取
   sstatus = 0x100 → bit[9:8] = 0b01 → 要 S mode（含以上）
   cycle   = 0xC00 → bit[9:8] = 0b00 → U mode 也能讀（但 [11:10]=11 唯讀）
```

所以 privilege check 一行搞定：**存取一個 CSR 時，若 `當前 priv < csr_addr[9:8]`，就是特權不足 → illegal exception。** 硬體不必列舉每個 CSR，只看位址高幾位。

我們的 core 這樣寫（M=11、U=00，所以「U mode 碰 [9:8]=11 的 CSR」= 越權）：

```systemverilog
// privilege check：
//  - U mode 執行 mret（特權指令）→ illegal
//  - U mode 存取 M-level CSR（位址 bit[9:8]==11）→ illegal
logic priv_fault;
assign priv_fault = (priv == U_MODE) &&
                    ( is_mret ||
                      (is_csr && (csr_a[9:8] == 2'b11)) );
logic is_illegal;
assign is_illegal = !known_op || priv_fault;   // 未知 opcode 或 越權，都算 illegal
```

一旦 `priv_fault`，這條指令就當 illegal instruction 觸發 trap（mcause=2、mtval=惹禍指令），**不執行它的原本效果**——CSR 沒被讀寫、mret 沒生效。籠子關住了。

## 核心概念：ECALL/EBREAK 與 mcause 的 mode 依賴

`ecall`（environment call）和 `ebreak`（breakpoint）是 U mode 也能執行的指令——它們是**主動請求上層服務**的正當手段（不是越權）。但它們**觸發的 exception cause 依當前 mode 而不同**：

| 指令 | from U mode | from S mode | from M mode |
|---|---|---|---|
| `ecall` | mcause = **8** | mcause = 9 | mcause = **11** |
| `ebreak` | mcause = 3 | mcause = 3 | mcause = 3 |

為什麼 ECALL 要依 mode 分不同 cause？因為**接電話的人要知道是誰打來的**。一個 M-mode 的 machine handler 收到 trap，讀 mcause：
- 看到 8 → 「是 user 程式的系統呼叫」→ 轉給對應處理。
- 看到 11 → 「是 S mode（kernel）在呼叫 M mode 的 SBI 服務」→ 走 firmware 路徑。

同一條 `ecall` 指令，在不同特權層執行，代表不同層級的請求。硬體用 mcause 把「請求者的身分」編進去，讓 handler 分流。這是 Ch 32 範例一（M mode ecall → 11）和本章範例（U mode ecall → 8）的差別根源。

`ebreak` 一律 cause 3（breakpoint），不分 mode——它是給 debugger 用的斷點，語意單一。

## 核心概念：mstatus.MPP 記住並還原特權

trap 會改變特權（升到 M），mret 要還原——中間得記住「trap 前是什麼 mode」。這靠 **mstatus.MPP（Machine Previous Privilege，bit[12:11]）**：

```
   trap 進入（Ch 32 六動作 + 特權部分）：
     mstatus.MPP ← 當前 priv      （記住 trap 前是誰）
     priv        ← M              （升到最高權處理）
     ... 加上存 mepc/mcause、MPIE←MIE、MIE←0 ...

   mret 返回：
     priv        ← mstatus.MPP    （還原到 trap 前的身分）
     mstatus.MPP ← U              （備份位重設為最低，安全預設）
     ... 加上 PC←mepc、MIE←MPIE ...
```

所以「掉到 U mode」的標準手法是：**在 M mode 把 mstatus.MPP 設成 U（00）、mepc 設成 user 程式入口、然後 mret**。mret 一執行，priv 就變成 MPP 記的 U，PC 跳到 mepc——CPU 就「降權跳進 user 程式」了。這是 OS 啟動 user 行程的硬體動作（設好 user 的 PC 和 MPP=U，mret 進去）。

反過來，user 程式出事（trap）或主動 ecall，就自動升回 M（MPP 記住 U），handler 處理完 mret 又降回 U。**特權在 U↔M 之間的每次往返，都是一次 trap 上去、一次 mret 下來，MPP 當書籤。**

## 底層機制：完整的特權轉換流程

把「M 啟動 user、user 越權、trap 回 M、返回 user」串成一張圖：

```
   priv=M ┌──────────────────────────────────────────┐
          │ firmware/kernel：設 mtvec、設 MPP=U、      │
          │ 設 mepc=user_entry                         │
          │              mret ──────────────┐          │
          └─────────────────────────────────│──────────┘
                                            ▼ priv←MPP(=U)、PC←mepc
   priv=U ┌──────────────────────────────────────────┐
          │ user 程式跑                                │
          │  碰 M-level CSR（csrr mstatus）→ priv_fault│
          │              illegal trap ──────┐          │
          └─────────────────────────────────│──────────┘
                                            ▼ MPP←U、priv←M、mepc←惹禍PC、PC←mtvec
   priv=M ┌──────────────────────────────────────────┐
          │ machine handler：讀 mcause=2（illegal）    │
          │ 處理（模擬指令 / 殺行程 / 跳過）           │
          │              mret ──────────────┐          │
          └─────────────────────────────────│──────────┘
                                            ▼ priv←MPP(=U)、PC←mepc
   priv=U （回到 user，繼續或被終止）
```

這張圖是本章的骨架，也是「OS 保護自己、user 越權被抓」的完整硬體流程。下面用真 core 跑出來。

## 實作與範例一：M→U 降權、U 越權觸發 illegal、返回

我們用一顆帶 privilege 的 mini core（`minicore_priv.sv`，在 Ch 32 mini core 上加了 `priv` 暫存器和 privilege check），跑一支「M 掉到 U、U 碰特權 CSR 觸發 trap」的程式：

```asm
_start:                        # 開機在 M mode
    la    x5, mtrap
    csrw  mtvec, x5            # 設 machine trap handler
    # 準備從 M 掉到 U：MPP=U(00)、mepc=user_code
    csrr  x6, mstatus
    li    x7, 0xffffe7ff       # mask 清掉 MPP(bit12:11)
    and   x6, x6, x7
    csrw  mstatus, x6          # MPP = 00 (U)
    la    x8, user_code
    csrw  mepc, x8
    li    x10, 1               # x10=1：標記「M mode 執行過」
    mret                       # 降權：跳 user_code、priv 變 U

    .align 2
user_code:                     # 這裡 priv=U
    li    x11, 2               # x11=2：標記「進了 U mode」
    csrr  x9, mstatus          # U mode 讀 M-level CSR → 越權！illegal trap
after_bad_csr:
    li    x12, 3               # 若 handler 跳過壞指令，這行才跑
uhalt:
    j     uhalt

    .align 2
mtrap:                         # machine handler（priv 升回 M）
    csrr  x13, mcause          # 看 trap 種類
    csrr  x14, mepc
    addi  x14, x14, 4          # 跳過惹禍指令（csrr mstatus）
    csrw  mepc, x14
    li    x15, 0x123           # handler 標記
    mret                       # 返回 user（priv 降回 U）
```

build 跑（每拍印 PC、**priv**、trap、mcause）：

```bash
riscv64-unknown-elf-gcc -march=rv32i -mabi=ilp32 -nostdlib -Ttext=0x80000000 -o priv.elf priv.S
riscv64-unknown-elf-objcopy -O binary --only-section=.text priv.elf priv.bin
od -An -tx4 -w4 -v priv.bin | sed 's/ //g' > prog_priv.hex
verilator --cc minicore_priv.sv --exe priv_tb.cpp --Mdir obj_pv \
    -Wno-WIDTH -Wno-UNUSED -Wno-UNOPTFLAT -GINIT_FILE='"prog_priv.hex"'
make -s -C obj_pv -f Vminicore_priv.mk Vminicore_priv
./obj_pv/Vminicore_priv 30
```

真跑輸出（節錄關鍵拍）：

```
cyc12 pc=80000030 priv=3 trap=0 mcause=00000000 mepc=80000034 x10=1 x11=0 x12=0
cyc13 pc=80000034 priv=0 trap=0 mcause=00000000 mepc=80000034 x10=1 x11=0 x12=0
cyc14 pc=80000038 priv=0 trap=1 mcause=00000000 mepc=80000034 x10=1 x11=2 x12=0
cyc15 pc=80000044 priv=3 trap=0 mcause=00000002 mepc=80000038 x10=1 x11=2 x12=0
...
cyc21 pc=8000003c priv=0 trap=0 mcause=00000002 mepc=8000003c x10=1 x11=2 x12=0
cyc22 pc=80000040 priv=0 trap=0 mcause=00000002 mepc=8000003c x10=1 x11=2 x12=3
FINAL priv=0 x10=1 x11=2 x12=3 mcause=00000002
```

一拍一拍看特權怎麼流動：

- **cyc0~12（priv=3）**：M mode 執行 setup——設 mtvec、清 MPP=U、設 mepc=user_code、x10=1（M 標記）。到 cyc12 準備執行 `mret`。
- **cyc13（priv 從 3 變 0）**：`mret` 生效！**priv 從 M(3) 降到 U(0)**，PC 跳到 0x34（user_code）。CPU 降權進 user 程式了。
- **cyc14（priv=0, trap=1）**：user 程式跑到 `csrr x9, mstatus`——這是讀 M-level CSR（mstatus=0x300, bit[9:8]=11）。當前 priv=U(0) < 要求的 M(11) → **priv_fault**！這一拍 trap=1，決定進 illegal trap。x11=2 顯示 user 的第一條 `li x11, 2` 已跑（越權的是第二條）。
- **cyc15（priv 從 0 變 3）**：trap 進入！**priv 升回 M(3)**、mcause=**2**（illegal instruction）、mepc=0x38（惹禍的 csrr）、PC 跳到 mtvec（0x44，handler）。籠子生效——user 想碰 mstatus 被硬體擋下、變成 trap 交給 M mode。
- **cyc15~20**：machine handler 跑，讀 mcause、mepc+4、寫回。
- **cyc21（priv 從 3 變 0）**：handler 的 `mret` 生效，**priv 降回 U(0)**（MPP 記的），PC 跳回 mepc（0x3c，after_bad_csr）。
- **cyc22**：`li x12, 3` 生效，x12=3——回到 user 繼續跑。

最終 `priv=0, x10=1, x11=2, x12=3`：M 標記、U 標記、返回後標記全在，且最後停在 U mode。**這就是完整的特權保護循環：M 啟動 user、user 越權被抓、trap 回 M 處理、mret 還原回 U。** 一顆能保護自己的 CPU 的雛形。

## 範例二：ECALL from U 給 mcause=8（vs from M=11）

把範例一 user 的越權 `csrr` 換成合法的 `ecall`（U mode 主動請求，不是越權），看 mcause 變什麼：

```asm
user_code:
    li    x11, 2
    ecall                      # ECALL from U → mcause 應為 8
after_ecall:
    li    x12, 3
```

真跑輸出（節錄）：

```
cyc13 pc=80000034 priv=0 trap=1 mcause=00000000 mepc=80000030 x10=0 x11=2 x12=0
cyc14 pc=80000040 priv=3 trap=0 mcause=00000008 mepc=80000034 x10=0 x11=2 x12=0
...
cyc20 pc=8000003c priv=0 trap=0 mcause=00000008 mepc=80000038 x10=0 x11=2 x12=3
FINAL priv=0 x10=0 x11=2 x12=3 mcause=00000008
```

- **cyc13（priv=0, trap=1）**：U mode 執行 `ecall`——這是**合法**的主動請求（不是 priv_fault），觸發 trap。
- **cyc14**：trap 進入，priv 升回 M，mcause=**8**（Environment call from **U**-mode）。

對比 Ch 32 範例一（M mode ecall → mcause=**11**）：**同一條 `ecall`，from U 是 8、from M 是 11**。這正是「接電話的人靠 mcause 知道是誰打來」。machine handler 讀到 8 就知道「user 系統呼叫」，讀到 11 就知道「S/M 的 SBI 呼叫」。特權模式讓同一個機制承載不同層級的請求。

## 對比取捨：三個模式的組合

| 組合 | 能跑什麼 | 隔離能力 | 典型用途 |
|---|---|---|---|
| 只有 M | 裸機單一程式 | 無 | 最簡單的嵌入式、bootloader、本課前 32 章 |
| M + U | 受限 user 程式（無頁表隔離）| 弱（U 碰不到 CSR，但共用實體記憶體）| RTOS、簡單嵌入式、本課主線 |
| M + S + U | 完整 OS（頁表隔離）| 強（每行程獨立位址空間 + 特權分層）| Linux、真作業系統 |

M+U（本課）給了「特權分層」但沒給「記憶體隔離」——U 程式碰不到 CSR，但如果沒有頁表（Ch 28），它們還是共用同一片實體記憶體，一個野指標能踩到別人。要真正隔離，得 M+S+U：S mode 管頁表（satp），每個 user 行程一張頁表，物理上就看不到彼此。**特權分層（本章）和記憶體隔離（Ch 28）是兩道正交的保護，合起來才是完整的 OS 保護。** 本課把兩者分開教，真系統把它們疊在一起。

## 踩雷區

**雷 1：以為 privilege check 要一個個列舉「哪個 CSR 誰能碰」。**
- 錯誤直覺：「得寫一大張表，mstatus 要 M、sstatus 要 S...」。
- 正確認識：**CSR 位址 bit[9:8] 就編了所需最低特權**，硬體只看這兩位。`priv < csr_addr[9:8]` 就是越權，一行邏輯搞定所有 CSR，不必列舉。這是 RISC-V CSR 位址空間的精心設計（Ch 31 進階延伸提過）。你若真的去列舉每個 CSR，不但囉嗦，還會漏掉未來新增的 CSR。信位址編碼。（唯讀檢查同理看 bit[11:10]：寫一個 [11:10]=11 的 CSR 也是 illegal。）

**雷 2：以為 ECALL 越權、要 privilege check 擋。**
- 錯誤直覺：「user 不能碰特權，那 ecall（會進 M handler）也該被擋」。
- 正確認識：**ECALL 是 U mode 的正當權利**——它就是「請求上層服務」的官方管道，不是越權。它不會 priv_fault，而是正常觸發一個 exception（cause 依 mode：U=8、M=11），讓上層 handler 決定怎麼服務。把 ecall 當越權擋掉，user 程式就沒法做系統呼叫了（不能讀檔、不能印東西）——整個 OS 服務模型垮掉。要分清「越權（被硬性擋下，illegal）」和「合法請求（ecall，被正常受理）」，兩者都觸發 trap 但意義相反。

**雷 3：以為 mret 能把特權「升上去」。**
- 錯誤直覺：「mret 跳回去，可以順便把 U 升成 M」。
- 正確認識：**mret 只能降權或平權（還原到 MPP 記的 mode），不能越級上升**。而且 U/S mode 執行 mret 本身就是 illegal（本課 check 的第一條）。特權**上升只能透過 trap**（出事或 ecall 才升到 M/S）——這是刻意的：如果 user 能自己 mret 升成 M，那籠子就形同虛設（它自己開門走出去）。升權必須是「被動地因 trap 而升」，且升到哪由硬體/delegation 決定，不由 user 選。範例一 user 想碰 mstatus 被擋，正是因為它沒法自己升權去合法碰。

**雷 4：以為 U mode 沒了記憶體隔離也算安全。**
- 錯誤直覺：「有了 M+U，user 程式就被關住了，安全了」。
- 正確認識：M+U 只給了**特權隔離**（U 碰不到 CSR/特權指令），**沒給記憶體隔離**——沒有頁表（Ch 28）或 PMP（physical memory protection）的話，U 程式的 load/store 還是能存取整片實體記憶體，包括 kernel 的資料。要真隔離，得配 S mode + 頁表（每行程一張，物理上看不到彼此），或至少 M mode 的 PMP（限制 U 能存取的物理位址範圍）。特權分層和記憶體隔離是兩件事——本章做了前者，Ch 28 是後者，真安全要兩者都有。只做特權分層就宣稱安全，是常見的誤解。

## 進階延伸

- **delegation：medeleg / mideleg 把 trap 直接送 S mode**：本課所有 trap 都進 M mode（priv←M、跳 mtvec）。但真 OS 這樣很浪費——user 的 page fault、user 的 ecall，本該直接進 kernel（S mode）處理，不必先繞 M mode 再轉。RISC-V 用 `medeleg`（exception delegation，0x302）和 `mideleg`（interrupt delegation，0x303）兩個 CSR：M mode 開機時設定「哪些 exception/interrupt 委派給 S 直接處理」。委派後那類 trap 從 U 發生時直接進 S mode（priv←S、跳 **stvec**、存 **sepc/scause/stval** 而非 m 系列）。這是跑 Linux 必備——SBI（跑在 M）開機時把幾乎所有 user trap 都 deleg 給 kernel（S），M mode 只留最底層的少數。加 S mode 時這是第一個要做的機制。
- **S mode 的完整 CSR 組**：加 S mode 要實作一整套 s 前綴 CSR：`sstatus`（mstatus 的 S 視圖，共用底層 bit）、`stvec`/`sepc`/`scause`/`stval`（S 的 trap 現場）、`sie`/`sip`（S 的中斷開關/等待）、`satp`（S 管的頁表基址，Ch 28）、`sscratch`。它們和 m 系列平行，靠 delegation 決定 trap 進 M 還是 S。mstatus 有些 bit（SIE/SPIE/SPP）是給 S 用的，sstatus 是 mstatus 的「只露 S 相關 bit」的視圖——同一個實體暫存器，兩個名字看不同 subset。
- **PMP：M mode 的物理記憶體保護**：沒有 S mode/頁表時，M mode 仍能用 **PMP（Physical Memory Protection）** 限制低權模式能存取的物理位址範圍。PMP 是一組 CSR（pmpcfg/pmpaddr），設定「U mode 只能讀寫執行這幾段物理位址」。這是嵌入式（M+U，無頁表）做記憶體隔離的手段，比頁表輕量。踩雷 4 提的「M+U 也能有記憶體保護」就是靠 PMP。
- **WFI 與特權**：`wfi`（wait for interrupt）在不同特權有不同行為——`mstatus.TW`（Timeout Wait）bit 控制「U/S mode 執行 wfi 是否 illegal」。若 TW=1，低權 wfi 會 trap，讓 M mode 決定要不要真的待命（防止 user 用 wfi 惡意卡住）。這是特權和省電機制的交界，設計 M+U 系統時要考慮。

## 本章重點整理

- **特權模式是硬體實施的權力分層**：M（firmware，最高）⊃ S（kernel，中）⊃ U（app，最低）。編碼 U=00、S=01、M=11。只有 M 必須實作；本課主線 M+U。用「門禁卡」記憶。
- **當前特權（priv）決定能碰什麼**：一個 2-bit 硬體狀態。reset=M；trap 升到 M；mret 降回 MPP 記的 mode。
- **privilege check 兩處**：特權指令（U 執行 mret → illegal）、CSR 存取（priv < csr_addr[9:8] → illegal）。**位址 bit[9:8] 編了所需最低特權**，不必列舉。
- **ECALL 是合法請求不是越權**：mcause 依 mode（U=8、S=9、M=11），讓 handler 知道請求者身分。ebreak 一律 3。
- **mstatus.MPP 當書籤**：trap 存當前 priv 到 MPP、升 M；mret 從 MPP 還原、備份位重設 U。**升權只能靠 trap，mret 只能降/平權**——這是籠子關得住的關鍵。
- **真跑驗證**：M mret 降到 U（priv 3→0）；U 碰 mstatus 觸發 illegal（mcause=2）升回 M；handler 跳過、mret 還原回 U；ECALL from U → mcause=8（對比 from M=11）。

## 自我檢核

- [ ] 我能說出 M/S/U 三個模式的編碼、各跑什麼軟體、權力如何巢狀，並用「門禁卡」類比。
- [ ] 我能解釋 privilege check 怎麼靠 CSR 位址 bit[9:8] 一行做完，不必列舉每個 CSR。
- [ ] 我能區分「越權（illegal，被擋）」和「ECALL（合法請求，被受理）」，並說出 ecall 為什麼要依 mode 給不同 mcause（U=8 vs M=11）。
- [ ] 我能追出範例一 priv 從 3→0（mret 降權）→0 越權→3（trap 升權）→0（mret 還原）的每次轉換，說明每次 MPP 怎麼用。
- [ ] 我能解釋為什麼「升權只能靠 trap、mret 不能升權」對保護 user 籠子是必要的。
- [ ] 我能說明 M+U 給了特權隔離但沒給記憶體隔離，要配 S mode+頁表或 PMP 才完整。

## 延伸閱讀

- **[RISC-V Privileged Spec](https://riscv.org/technical/specifications/) 第 1.2 節「Privilege Levels」與 3.1.6 節（mstatus 的 MPP/特權欄位）、3.1.9（Machine Trap Delegation medeleg/mideleg）**：權威來源。它定義三個模式的編碼、特權轉換規則、mcause 的 ECALL cause 表（8/9/11）、delegation 機制。本章的 priv 轉換和 check 就是它的白話版。特別讀 CSR 位址編碼（2.1 節）確認 bit[9:8] 的特權語意，和 mret 的正式定義。
- **《Computer Organization and Design, RISC-V Edition》(Patterson & Hennessy) 第 5.6 節「Protection with Virtual Memory」**：教科書把特權模式和記憶體保護放一起講（呼應本章踩雷 4：兩道正交保護）。它解釋為什麼 OS 需要 user/kernel 分離、privilege 怎麼配合頁表做隔離。讀它把本章（特權分層）和 Ch 28（記憶體隔離）串成完整的保護圖像。
- **[xv6-riscv 的 `kernel/main.c` 和 `kernel/start.c`](https://github.com/mit-pdos/xv6-riscv/tree/riscv/kernel)**：真實教學 OS 怎麼用特權模式開機——`start.c` 跑在 M mode，設好 delegation（`medeleg`/`mideleg` 全開給 S）、設 MPP=S、mepc=main，然後 mret **降到 S mode** 進 kernel。這正是本章範例一「設 MPP、mret 降權」的真實工業版，只是它降到 S 而非 U。看它就懂「firmware 怎麼把 CPU 交給 kernel」。它的 `usertrapret()` 則示範「kernel 設 MPP=U、mret 進 user 程式」——本章範例一的另一半。
- **[SiFive: The RISC-V Privileged Architecture 導讀 / RISC-V Reader 第 10 章](http://riscvbook.com/)**：《The RISC-V Reader》(Patterson & Waterman) 第 10 章用最白話的方式講 privileged architecture——三個模式、trap delegation、CSR 分層。比 spec 好讀，適合先讀它建立整體圖像再回去啃 spec 細節。它的 mode 轉換圖和 delegation 說明是本章的最佳補充。

下一章我們把 interrupt（非同步 trap）真正做出來——實作一個簡易的 CLINT（Core Local Interruptor），用 mtime/mtimecmp 產生 timer interrupt，看 mie/mip 怎麼決定中斷觸不觸發、mcause 最高位為什麼是 1、以及中斷和 exception 在 trap 流程裡的異同。特權模式是舞台、trap 是機制，中斷是讓 CPU「活起來」回應外部世界的心跳。

→ [Ch 34 中斷控制：CLINT（timer / software int）、PLIC 速覽](./34-interrupt-clint-plic.md)
