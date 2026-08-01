# Final Project — 完整 pipelined RV32I core，工程化驗證

> **目標**：把全課 40 章接成一顆你能真正拿去跑程式的東西——完整五級 pipelined RV32I core，帶 EX+ID forwarding、load-use / branch-use stall、branch/jump 在 ID resolve 配 BTB（靜態 + 動態 2-bit 預測），通過 riscv-tests 風格的指令自檢，跑你自己 toolchain 編出的排序 / 費氏 / 費氏真程式，最後產一份能歸因的 CPI 報告。不是玩具展示，是一顆有驗收標準、能量測、能對照的 core。
> **環境**：WSL + verilator 4.038 + riscv64-unknown-elf-gcc 10.2.0。本檔所有波形、暫存器 dump、CPI 數字**全部真跑產生**，不是手抄的預期值。參考解在最後用 `<details>` 摺疊，完整可 copy-paste 跑。

這是整門課的兌現點。前面每一章你都做了一塊——ALU、regfile、五級切分、forwarding、hazard unit、BTB。這裡把它們接成一顆完整的 core，並且用工業界驗真實硬體的方法（自製程式對手算、riscv-tests 自檢慣例、performance counter 量 CPI）驗到你敢說「這顆是對的」。做完你手上會有一份約 500 行的 SystemVerilog + C++，是一顆真的執行 RV32I 的矽的模型。

---

## 一、專案定位與驗收標準

我們要交付的不是「看起來會動」，是**能通過明確驗收條件**的一顆 core。驗收分必做與加分兩層。

### 必做（做到才算完成）

| # | 驗收項 | 通過標準 | 本檔實測 |
|---|---|---|---|
| A1 | 完整 RV32I datapath | R/I 算術、LOAD/STORE（含 LB/LH/LBU/LHU）、所有 branch、JAL/JALR、LUI/AUIPC 全實作 | ✅ 自檢程式全過 |
| A2 | 五級 pipeline + hazard 全處理 | EX+ID forwarding、load-use stall、branch-use stall、branch mispredict flush | ✅ |
| A3 | 分支預測（至少 static + BTB） | 靜態 not-taken 為基準；BTB + 2-bit BHT 動態預測，猜錯才 flush | ✅ 兩者都做，CPI 對照見第五節 |
| A4 | 通過指令自檢 | riscv-tests 風格：測試程式寫 `tohost`，PASS=1、FAIL=(gp<<1)\|1 | ✅ 自檢程式 `tohost=1` |
| A5 | 跑自編 toolchain 真程式 | 用你自己 `riscv64-unknown-elf-gcc` 組出的排序 / 費氏，結果對手算一致 | ✅ 排序 [1..9]、fib(21)=10946 |
| A6 | 產 CPI 報告 | tb 有 performance counter，量 cycle/instret/stall/flush，算 CPI 並歸因 | ✅ 見第五節 |

### 加分（有餘力再做，第七節給路徑）

- **M 擴充**：MUL/DIV（多週期或 pipelined multiplier）。
- **I-cache**：把 imem 換成練習 D 的 direct-mapped I-cache，量 miss stall 對 CPI 的影響。
- **CSR + timer 中斷**：Part 5 的 CSR file + CLINT，讓 core 跑得動 trap handler。

**這份文件的立場**：必做六項我全部在 WSL 真跑驗證過，程式碼與數字都貼真實輸出。加分項給你明確的接法與地基指引（Part 5 的 CSR/trap 教材與 `_scratch` 已備），但不硬塞進主線——把主線做紮實，比堆功能重要。一顆通過 A1–A6 的 core，已經是能讀懂 Rocket 的門票。

### 涵蓋的章節概念（超過全課 70%）

這顆 core 直接動用了：Ch 1–3（邏輯/時序/FSM 的心智）、Ch 4–5（SystemVerilog + verilator 流程）、Ch 6–12（datapath：PC/fetch/regfile/ALU/control/imm/load-store/branch，單週期語意當黃金參考）、Ch 13–20（五級切分、pipeline register、forwarding、load-use stall、control hazard flush、hazard unit、完整整合）、Ch 21–24（BTB + 2-bit 飽和計數器、CPI 分解、performance counter、關鍵路徑取捨）。Part 4（cache/VM）、Part 5（CSR/trap）作為加分接口。真正沒碰的只有 Part 6 的純概念章（superscalar/OoO 只講不做）——那本來就是「不涵蓋」的範圍。

---

## 二、系統架構總覽

### module 階層

```
core (top)
├── btb            分支預測器：IF 級組合查詢 + ID resolve 後同步更新
│                  （BTB target + 2-bit BHT 方向，valid+tag 防別名）
├── alu            全課約定 ALU（Ch 9，a/b/alu_op → result/zero）
├── imem[4096]     指令記憶體（$readmemh 載入）
├── dmem[4096]     資料記憶體（含 tohost 位址攔截）
└── regs[32]       register file（x0 硬 0、async 讀 sync 寫、write-first bypass）

五級（在 core 內以 always_ff pipeline register 串接）：
   IF ── if_id_reg ── ID ── id_ex_reg ── EX ── ex_mem_reg ── MEM ── mem_wb_reg ── WB

橫跨全 pipeline 的組合控制（都在 core 內的 always_comb）：
   forwarding_unit  ：fwd_a/fwd_b（EX 級）+ ID 級 forwarding（給 branch 比較器）
   hazard_unit      ：load_use_hazard / branch_use_hazard / id_mispredict
                      → pc_write / if_id_write / id_ex_bubble / if_id_flush
```

我們把 forwarding、hazard、control 這些邏輯**內聯在 top `core`** 裡（用具名 `always_comb` 區塊），不拆成獨立 module。原因：這些邏輯需要讀遍所有 pipeline register，拆出去反而要拉一堆 port，教學上更難看清資料流。工業 core（如 Rocket）也常這樣——control 集中在一處。BTB 和 ALU 因為介面乾淨、可獨立驗證，才拆成 module。

### 訊號流全景

```
        pred_taken/pred_target                      id_mispredict → if_id_flush
        ┌──────── btb ◄─────update(pc,taken,target)─────┐          │
        ▼                                                │          ▼
   ┌─IF──┐  if_id  ┌─ID─────────────┐ id_ex ┌─EX──────┐ ex_mem ┌─MEM──┐ mem_wb ┌─WB─┐
   │ PC  │───────▶ │ decode/imm      │──────▶│ ALU     │──────▶ │ dmem │──────▶ │ mux│──▶ regs 寫
   │imem │  pred   │ regfile 讀      │  fwd  │ fwd mux │        │ 讀寫 │  fwd   │    │
   └──▲──┘         │ ID forward      │       └─────────┘        │tohost│        └─┬──┘
      │            │ branch resolve  │           ▲                  │             │
   if_pc_next      │ (BTB 比對)      │           │                  │           wb_data
   （mispredict 修正│─────────────────┘  ex_mem_fwd_val（load 遞     │             │
     > 預測 taken   │                     mem_rdata，非位址）────────┘             │
     > PC+4）       │◄──── write-first bypass（WB 這拍寫的值，ID 同拍讀得到）──────┘
                    │
        ┌───────────┴─────────── HAZARD / FORWARDING（always_comb）──────────────┐
        │ load-use / branch-use → stall（凍 PC/IF-ID + 插 bubble）                │
        │ id_mispredict → flush IF/ID（branch 在 ID resolve，只清一條）           │
        │ 優先序：stall > flush                                                   │
        └────────────────────────────────────────────────────────────────────────┘
```

三個設計決策定調整顆 core，值得先講清楚：

1. **branch/jump 在 ID resolve**（不是 EX）。好處：mispredict penalty 只有 1 拍（IF 級那條被 flush），比 EX resolve 的 2 拍省。代價：要在 ID 級做 branch 比較，需要 **ID forwarding**（來源剛在 EX/MEM 算出）和 **branch-use stall**（來源還在 EX、值還沒到 EX/MEM 時等一拍）。這是 Ch 18–19 的完整版。

2. **load 的 forwarding 值是 `mem_rdata` 不是 `ex_mem_alu`**。這是全 project 最容易踩的坑（我自己就踩了，見第四節的除錯實錄）。load 在 EX/MEM 級時，`ex_mem_alu` 存的是**位址**，真正 load 出來的資料是 MEM 級組合算出的 `mem_rdata`。forwarding 時必須遞 `mem_rdata`，遞成位址就整個算錯。

3. **BTB 查詢組合、更新同步**。IF 級當拍就要拿到預測（組合 `assign`），resolve 後下一拍才更新計數器與 target（同步 `always_ff`）。搞反就是「晚一拍才預測」，等於沒預測。

---

## 三、分階段實作建議

別想一次寫完 500 行然後 debug——那會淹死你。照這六步，每步都能獨立跑、獨立驗證，錯了範圍小。

### 步驟 1：datapath 骨架（無 hazard，先跑無相依程式）

先把五級接起來、pipeline register 串好、ALU/regfile/decode/imm 接上，**forwarding 全回 0、hazard 全不觸發**。跑一支**刻意每條指令都隔開、沒有 RAW/hazard** 的程式（每個結果算完隔 3 條再用），確認 datapath 本身對。這步驗的是「線接對了嗎」，不碰 hazard。

**子目標**：無相依程式的暫存器 dump 對手算一致。

### 步驟 2：pipeline register + WB 寫回 + write-first bypass

補上 regfile 的 write-first bypass（WB 這拍要寫的值，ID 同拍讀得到），化解「距離 3」的 RAW。這步後，相依距離 ≥3 的程式會對，距離 1–2 仍錯（等 forwarding）。

**子目標**：距離 3 的 RAW 對，並理解為什麼還差近距離。

### 步驟 3：forwarding（EX + ID）

寫 EX 級 `fwd_a`/`fwd_b`（EX/MEM 優先於 MEM/WB），和 ID 級 forwarding（給 branch 比較器）。**關鍵：load 遞 `mem_rdata`**。這步後，純算術的近距離 RAW 全解，branch 用剛算的值也對。

**子目標**：連鎖 forwarding（`a=b+c; d=a+e; f=d+a`）全對。

### 步驟 4：hazard detection（load-use + branch-use stall）

寫 `load_use_hazard`（EX 是 load、rd 命中 ID 來源）和 `branch_use_hazard`（ID 是控制轉移、來源還在 EX），觸發 stall（凍 PC/IF-ID + 插 bubble）。優先序：stall 先於 flush。

**子目標**：`lw` 緊接用它、`add` 緊接 `beq` 用它，都算對（stall 補上）。

### 步驟 5：branch prediction（static → BTB）+ flush

先做 **static not-taken**（永遠猜 fall-through），mispredict（實際 taken）就 flush IF/ID 並修正 PC。跑通後再加 **BTB + 2-bit BHT**：IF 組合查詢、ID resolve 更新，只有猜錯才 flush。

**子目標**：帶迴圈回跳的程式結果對；BTB 版的 flush 數遠低於 static-NT。

### 步驟 6：驗證框架 + CPI 量測

寫 tb：載 hex、跑到 `tohost` 或上限、dump 暫存器與記憶體、performance counter 記 cycle/stall/flush、反推 instret、算 CPI。跑排序/費氏/自檢三支，對手算與 riscv-tests 慣例驗證，產 CPI 報告。

**子目標**：三支真程式全過 + 一份能歸因的 CPI 表。

---

## 四、驗證方法（核心，全程真跑）

驗證是這個 project 的一半。一顆沒驗過的 core 等於沒做完。我們用三層驗證，信心逐層升高。

### 4.1 組譯 + 載入流程

用你自己的 toolchain 把 `.S` 變成 core 吃的 hex：

```bash
# asm.sh <in.S> <out.hex>
riscv64-unknown-elf-gcc -march=rv32i -mabi=ilp32 -nostdlib -Ttext=0x80000000 -o t.elf "$1"
riscv64-unknown-elf-objcopy -O binary t.elf t.bin
python3 -c '
import struct
d=open("t.bin","rb").read(); d+=b"\x00"*((4-len(d)%4)%4)
f=open("'"$2"'","w")
for i in range(0,len(d),4): f.write("%08x\n"%struct.unpack("<I",d[i:i+4])[0])
'
```

core 用 `$readmemh` 把 hex 載進 imem（reset PC = 0x8000_0000，`imem[(pc-RESET_PC)>>2]`）。

### 4.2 第一層：混合 hazard 程式對手算（Ch 20 的驗收升級版）

一支同時踩 RAW / load-use / control 的程式，結果對手算逐暫存器比。這是 Ch 20 那支，這裡確認整合後的完整 core 仍全中：

```asm
    lui  x10, 0x80000       # x10 = 0x80000000
    addi x1, x0, 10         # 10
    addi x2, x1, 5          # 15  RAW forward
    add  x3, x2, x1         # 25  兩來源 forward
    sw   x3, 0(x10)         # mem[base]=25
    lw   x4, 0(x10)         # 25
    add  x5, x4, x4         # 50  load-use stall
    addi x6, x0, 7
    beq  x1, x6, skip       # 10==7? not-taken
    addi x7, x0, 1          # 1
skip:
    addi x8, x0, 99         # 99
    beq  x1, x1, done       # taken -> flush poison
    addi x9, x0, 555        # POISON（不該執行）
done:
    addi x9, x0, 2          # 2
    lui  x11, 0x80001; addi x12, x0, 1; sw x12, 0(x11)   # tohost=1
```

真跑輸出：

```
=== final register state ===
x1  =          10  (0x0000000a)
x2  =          15  (0x0000000f)
x3  =          25  (0x00000019)
x4  =          25  (0x00000019)
x5  =          50  (0x00000032)
x6  =           7  (0x00000007)
x7  =           1  (0x00000001)
x8  =          99  (0x00000063)
x9  =           2  (0x00000002)
x10 = -2147483648  (0x80000000)
x11 = -2147479552  (0x80001000)
=== tohost result ===
PASS (tohost=1)
```

十個暫存器全中，含 **x9=2**（毒指令被 flush，沒變 555）——control hazard 的試金石過了。`x10=-2147483648` 就是 `0x80000000` 的有號十進位，值正確。

### 4.3 第二層：riscv-tests 風格自檢

真實硬體不靠人挑 case，靠**每指令 corner case 的自檢**。riscv-tests 的慣例：測試程式把子測試編號放 `gp`(x3)，任何一項失敗就寫 `tohost = (gp<<1)|1`，全過寫 `tohost = 1`。tb 監看 `tohost` 位址（我們約定 `0x8000_1000`），非零即停。

我們寫一支自檢，涵蓋 forwarding 鏈、SLT/SLTU 有號無號、SRA/SRL 算術 vs 邏輯右移、JAL/JALR 連結與返回：

```asm
_start:
    lui  x31, 0x80001        # TOHOST 位址
    addi x3, x0, 1           # --- test 1：forwarding 鏈 ---
    addi x5, x0, 20
    addi x6, x5, -5          # 15
    addi x7, x6, 100         # 115（連鎖 forward）
    addi x8, x0, 115
    bne  x7, x8, fail
    addi x3, x0, 2           # --- test 2：SLT/SLTU ---
    addi x5, x0, -1
    addi x6, x0, 1
    slt  x7, x5, x6          # -1<1 有號 -> 1
    addi x8, x0, 1
    bne  x7, x8, fail
    sltu x7, x5, x6          # 0xffffffff<1 無號 -> 0
    bne  x7, x0, fail
    addi x3, x0, 3           # --- test 3：SRA 保號 ---
    lui  x5, 0xfffff
    srai x7, x5, 4
    srli x9, x7, 31          # 符號位應=1
    bne  x9, x6, fail
    addi x3, x0, 4           # --- test 4：JAL/JALR ---
    jal  x1, subr
    addi x8, x0, 42
    bne  x10, x8, fail
    beq  x0, x0, allpass
subr:
    addi x10, x0, 42
    jalr x0, 0(x1)
fail:
    slli x4, x3, 1; ori x4, x4, 1; sw x4, 0(x31); beq x0,x0,halt
allpass:
    addi x4, x0, 1; sw x4, 0(x31)                    # tohost=1
halt:
    beq  x0, x0, halt
```

真跑：

```
=== tohost result ===
PASS (tohost=1)
```

四組子測試全過，代表 SLT/SLTU 的有號無號、SRA/SRL 的算術/邏輯、JAL/JALR 的連結與間接跳轉都對。這就是把「自己驗」升級到「官方合規慣例」的入口——把這支換成 riscv-tests 官方 `rv32ui/*.S` 就是正式合規測試（見延伸閱讀）。

### 4.4 第三層：跑自編 toolchain 的真程式

自檢驗指令正確性，真程式驗「湊在一起跑一段有意義的計算」。我們跑 bubble sort（8 元素）和 iterative fibonacci，兩支都是 gcc 從 `.S` 組出來的。

**Bubble sort**：把 `[5,2,8,1,9,3,7,4]` 排序，巢狀迴圈 + 每輪多次 load/store/branch，是壓力測試。真跑後 dump dmem：

```
=== dmem[0..7] ===
dmem[0] = 1
dmem[1] = 2
dmem[2] = 3
dmem[3] = 4
dmem[4] = 5
dmem[5] = 7
dmem[6] = 8
dmem[7] = 9
=== tohost result ===
PASS (tohost=1)
```

排序完全正確 `[1,2,3,4,5,7,8,9]`。這支同時驗了：位址計算（`slli`+`add` 算 `&a[j]`）、store→load 記憶體來回、branch（`bge`/`blt`）在巢狀迴圈的方向與回跳、以及最刁鑽的 **load 結果餵給 branch 比較器**的 forwarding。

**Fibonacci**（迭代 20 次，起始 a=0,b=1）：

```
x1 (a) =  6765  (fib 20)
x2 (b) = 10946  (fib 21)
=== tohost result ===
PASS (tohost=1)
```

`fib(21)=10946`、`fib(20)=6765`，對。

### 4.5 除錯實錄：一個真實的坑

寫這個 project 時我第一版排序跑出 `[4,7,3,9,1,8,2,5]`——完全沒排好。但 fib、混合 hazard、單獨的 store/load swap 測試全過。用 ID 級 PC trace 一路追，發現排序的 `bge a[j+1], a[j]` **永遠判 not-taken**（永遠 swap），值明顯錯。

根因：`bge` 的兩個來源是剛 `lw` 出來的 `a[j]`、`a[j+1]`。我的 forwarding 遞的是 `ex_mem_alu`——但對 **load** 而言，`ex_mem_alu` 是**位址**（`0x80000000+offset`），不是 load 出來的資料！位址是個大數，`bge` 拿兩個大位址比，方向當然錯。

修法：forwarding 遇到 EX/MEM 是 load 時，遞 MEM 級組合算出的 `mem_rdata`：

```systemverilog
assign ex_mem_fwd_val = ex_mem_mem_read ? mem_rdata : ex_mem_alu;
```

EX 和 ID 兩處 forwarding 都改用 `ex_mem_fwd_val`。改完排序立刻對。

**這個坑值得你也踩一次**：它解釋了為什麼 forwarding 不能只看「rd 命中」，還要看「這個 rd 的值到底存在哪個訊號」。load 和 ALU 指令的結果來源不同級、不同訊號，forwarding mux 要選對。這是 Ch 16 forwarding 沒細講、但真做才會撞到的細節。

---

## 五、CPI 報告

效能是 pipeline 存在的理由。沒有 CPI 量測，你不知道 hazard 和 mispredict 到底吃掉多少。

### 記帳模型

穩態下 pipeline 的 cycle 帳：

```
cycles = instret + fill(4) + stalls + flushes
```

- `instret`：真正 retire 的指令數。
- `fill = 4`：5 級 pipeline 的填充延遲（第一條要 5 拍走完 WB，穩態前的固定開銷）。
- `stalls`：load-use + branch-use 插的 bubble 數（各 +1 拍）。
- `flushes`：branch mispredict flush 數（各 +1 拍）。

tb 用 core 對外的 `perf_stall` / `perf_flush` 訊號逐拍記帳，反推 `instret = cycles - 4 - stalls - flushes`，再算 `CPI = cycles / instret`。

### 靜態 vs 動態預測對照（真跑數字）

同一支程式跑兩種配置：`USE_BP=0`（靜態 not-taken，每個 taken 都猜錯）vs `USE_BP=1`（BTB + 2-bit BHT）。三支 benchmark 的真實量測：

| benchmark | 配置 | cycles | instret | flushes | **CPI** |
|---|---|---|---|---|---|
| sum(1..100) | static-NT | 507 | 303 | 100 | **1.673** |
| sum(1..100) | **BTB**   | 410 | 303 | 3   | **1.353** |
| fib (20 迭代) | static-NT | 147 | 103 | 20  | **1.427** |
| fib (20 迭代) | **BTB**   | 130 | 103 | 3   | **1.262** |
| sort (8 元素) | static-NT | 387 | 273 | 45  | **1.418** |
| sort (8 元素) | **BTB**   | 362 | 273 | 20  | **1.326** |

### 歸因分析

**看 `instret`：三種配置完全相同**（sum=303、fib=103、sort=273）。這是正確性的鐵證——預測器只影響「花幾拍」，不影響「retire 幾條指令」。預測改變 CPI 但不改變架構語意，正是 pipeline 正確性黃金定律。

**看 flushes 的暴跌**：sum 從 100 → 3。sum 是單層迴圈跑 100 次，每次回跳都 taken。static-NT 每次都猜 not-taken、每次都錯 → 100 次 flush。BTB 熱身兩次後 2-bit 計數器飽和到 strongly-taken，之後每次猜對，只剩迴圈**進入**和**離開**的 mispredict → 3 次。這就是分支預測的全部價值：把「規律的 branch」從每次 penalty 變成幾乎零 penalty。

**sum 的 CPI 拆解（BTB 版）**：

```
CPI = 1.353 = ideal(1.0) + stall/inst(0.330) + flush/inst(0.010) + fill/inst(0.013)
```

sum 的 CPI 主要被 **stall** 吃掉（0.330），不是 flush。為什麼？sum 的迴圈是 `add / addi / blt`——`blt` 用剛 `addi` 出來的 `i`，是 **branch-use hazard**（branch 在 ID resolve，來源 `i` 還在 EX），每次迭代 stall 1 拍。100 次迴圈 → 100 次 stall。這是「branch 提前到 ID resolve」的代價：省了 flush（penalty 2→1），但換來 branch-use stall。

這揭示一個真實的微架構取捨：**branch resolve 越早，mispredict penalty 越小，但 branch-use hazard 越容易發生**。sum 這種「算完 i 立刻拿去比」的 pattern，branch-use stall 反而成主導。編譯器的指令排程（在 branch 和它的來源之間塞別的指令）能消掉這個 stall——這就是為什麼 `-O2` 的 code 在同一顆 core 上 CPI 更低。

**flushes 沒歸零的原因**：BTB 是**冷啟動**的（valid 全 0），每個 branch 第一次遇到必 miss（沒 entry、猜 not-taken）。迴圈進入時第一次回跳、離開時方向反轉，都會 mispredict。3 次 flush = 冷啟動 miss + 離開 miss，是理論下限，不是 bug。

### 這份報告告訴你什麼

1. **預測有效**：三支程式 CPI 都降（sum 降最多，因為迴圈次數最多、taken 最規律）。
2. **瓶頸因程式而異**：sum 是 branch-use stall 主導，sort 是 flush（巢狀迴圈方向較亂）主導。優化方向不同。
3. **正確性與效能正交**：instret 恆定，CPI 隨微架構變。這是你能大膽改微架構的底氣——只要 instret 和暫存器狀態不變，你怎麼加速都不會改變程式語意。

---

## 六、常見卡點提示

**卡點 1：排序/查表類程式結果亂掉，但單條指令測試都過。**
九成是 **load 的 forwarding 遞成位址**（第四節除錯實錄那個坑）。load 在 EX/MEM 級時 `ex_mem_alu` 是位址不是資料，forwarding 要遞 `mem_rdata`。症狀是「用到 load 結果的 branch/算術方向或值錯，但純算術鏈對」。先查這個。

**卡點 2：branch 用剛算的值跳錯，但隔開就對。**
branch 在 ID resolve，來源若剛在 EX（還沒到 EX/MEM），ID forwarding 補不到——要 **branch-use stall** 等一拍。沒做這個 stall，branch 會拿到舊值判錯方向。檢查 `branch_use_hazard` 有沒有涵蓋「ID 是控制轉移 + 來源命中 `id_ex_rd`」。

**卡點 3：迴圈回跳的程式，第一次對、後面錯（或 flush 數異常）。**
BTB 的**查詢是組合、更新是同步**這件事搞反了。查詢寫成 `always_ff` 會晚一拍拿到預測（等於沒預測）；更新寫成 `assign` 會競態。還有：BTB entry 一定要有 **tag**，否則不同 PC 別名到同一 index 會拿到別人的 target，跳到亂七八糟的地方。

**卡點 4：verilator 編不過，報 BLKLOOPINIT。**
verilator 4.038 不吃「`always_ff` 迴圈內對陣列做 non-blocking 賦值」（例如在 reset 裡 `for` 清 BTB 陣列）。改用 `initial` 區塊初始化陣列（本課純模擬，`initial` 完全夠用），reset 迴圈裡別碰陣列。BTB 的 `pht`/`valid` 就是這樣初始化的。

**卡點 5：CPI 算出來是負的或小於 1。**
`instret = cycles - 4 - stalls - flushes` 反推時，如果程式太短（cycles 還沒攤平 fill 的 4 拍），或你把 stall/flush 重複計數，會反推出荒謬的 instret。確認：perf_stall/perf_flush 每個 hazard 事件只 assert 一拍（stall 期間 PC 凍住，下一拍同一條指令再判斷時 hazard 已解除，不會重複計）；程式要夠長讓穩態成立。短程式 fill 佔比高，CPI 會偏高但不該 < 1。

---

## 七、完整參考解

卡住再看。以下三個檔 + tb + 組譯腳本是**完整、可 copy-paste 真跑**的版本，本檔所有輸出都由它們產生。核心約 340 行、tb 約 90 行、BTB 約 60 行、ALU 27 行，總計約 500 行，符合 final project 篇幅。

<details>
<summary>alu.sv（全課約定，Ch 9，直接用）</summary>

```systemverilog
// alu.sv — 全課約定的 ALU（Ch 9）
module alu (
    input  logic [31:0] a,
    input  logic [31:0] b,
    input  logic [3:0]  alu_op,
    output logic [31:0] result,
    output logic        zero
);
    logic signed [31:0] as, bs;
    assign as = a; assign bs = b;
    always_comb begin
        unique case (alu_op)
            4'b0000: result = a + b;                       // ADD
            4'b0001: result = a - b;                       // SUB
            4'b0010: result = a << b[4:0];                 // SLL
            4'b0011: result = (as < bs) ? 32'd1 : 32'd0;   // SLT
            4'b0100: result = (a < b)   ? 32'd1 : 32'd0;   // SLTU
            4'b0101: result = a ^ b;                       // XOR
            4'b0110: result = a >> b[4:0];                 // SRL
            4'b0111: result = as >>> b[4:0];               // SRA
            4'b1000: result = a | b;                       // OR
            4'b1001: result = a & b;                       // AND
            default: result = 32'd0;
        endcase
    end
    assign zero = (result == 32'd0);
endmodule
```
</details>

<details>
<summary>btb.sv（BTB + 2-bit BHT 分支預測器）</summary>

```systemverilog
// btb.sv — BTB + 2-bit BHT 分支預測器
// 查詢埠 IF 級組合輸出；更新埠 ID resolve 後同步寫入（Ch21 約定）。
// 陣列初始化用 initial（本課純模擬）；不在 reset 迴圈裡清陣列（避免 verilator BLKLOOPINIT）。
module btb #(parameter IDX_BITS = 6) (
    input  logic        clk,
    input  logic        rst,
    input  logic [31:0] pc_f,
    output logic        predict_taken,
    output logic [31:0] predict_target,
    input  logic        update_en,
    input  logic [31:0] update_pc,
    input  logic        update_taken,
    input  logic [31:0] update_target
);
    localparam int N = (1 << IDX_BITS);
    localparam int TAG_BITS = 32 - IDX_BITS - 2;

    logic [1:0]          pht   [0:N-1];   // 2-bit 飽和計數器
    logic                valid [0:N-1];
    logic [TAG_BITS-1:0] tag   [0:N-1];
    logic [31:0]         target[0:N-1];

    integer i;
    initial begin
        for (i = 0; i < N; i = i + 1) begin
            pht[i] = 2'b01; valid[i] = 1'b0; tag[i] = '0; target[i] = '0;
        end
    end

    logic [IDX_BITS-1:0] idx_f, idx_u;
    logic [TAG_BITS-1:0] tag_f, tag_u;
    assign idx_f = pc_f[IDX_BITS+1:2];       // 跳過 byte offset 低 2 bit
    assign tag_f = pc_f[31:IDX_BITS+2];
    assign idx_u = update_pc[IDX_BITS+1:2];
    assign tag_u = update_pc[31:IDX_BITS+2];

    // 查詢（組合）：方向看 pht 最高位，target 看 BTB 命中
    logic btb_hit;
    assign btb_hit        = valid[idx_f] && (tag[idx_f] == tag_f);
    assign predict_taken  = btb_hit && pht[idx_f][1];
    assign predict_target = target[idx_f];

    // 更新（同步）：2-bit 飽和計數器；只有 taken 才記 target（有 tag 防別名）
    always_ff @(posedge clk) begin
        if (!rst && update_en) begin
            if (update_taken) pht[idx_u] <= (pht[idx_u] == 2'b11) ? 2'b11 : pht[idx_u] + 2'b01;
            else              pht[idx_u] <= (pht[idx_u] == 2'b00) ? 2'b00 : pht[idx_u] - 2'b01;
            if (update_taken) begin
                valid[idx_u] <= 1'b1; tag[idx_u] <= tag_u; target[idx_u] <= update_target;
            end
        end
    end
endmodule
```
</details>

<details>
<summary>core.sv（完整五級 pipelined RV32I core，~340 行）</summary>

```systemverilog
// core.sv — Final Project：完整五級 pipelined RV32I core
// 五級 IF/ID/EX/MEM/WB；EX+ID forwarding；load-use / branch-use stall；
// branch/jump 在 ID resolve 配 BTB 靜態+動態預測，猜錯才 flush。完整 RV32I。
// tohost 機制 + performance counter 對外。dbg_* 埠供觀測。
module core #(parameter INIT_FILE = "", parameter USE_BP = 1) (
    input  logic        clk,
    input  logic        rst,
    input  logic [4:0]  dbg_reg_sel,
    output logic [31:0] dbg_reg_data,
    input  logic [11:0] dbg_mem_sel,
    output logic [31:0] dbg_mem_data,
    output logic [31:0] dbg_id_pc,     // 觀測：ID 級 PC（trace 用）
    output logic [31:0] dbg_id_inst,
    output logic        dbg_id_valid,
    output logic        tohost_we,
    output logic [31:0] tohost_data,
    output logic        retire_valid,
    output logic        perf_stall,
    output logic        perf_flush
);
    localparam logic [31:0] RESET_PC = 32'h8000_0000;
    localparam logic [31:0] TOHOST   = 32'h8000_1000;   // tohost 約定位址
    logic [31:0] imem [0:4095];
    logic [31:0] dmem [0:4095];
    initial if (INIT_FILE != "") $readmemh(INIT_FILE, imem);

    logic        pc_write, if_id_write, if_id_flush, id_ex_bubble;
    logic        wb_reg_write; logic [4:0] wb_rd; logic [31:0] wb_data;
    logic        ex_mem_reg_write; logic [4:0] ex_mem_rd; logic [31:0] ex_mem_alu;
    logic        ex_mem_mem_read;

    // ==================== IF ====================
    logic [31:0] if_pc, if_pc_next, if_inst;
    always_ff @(posedge clk) begin
        if (rst) if_pc <= RESET_PC;
        else if (pc_write) if_pc <= if_pc_next;
    end
    assign if_inst = imem[(if_pc - RESET_PC) >> 2];

    logic        pred_taken_raw, pred_taken;
    logic [31:0] pred_target;
    logic        bp_update_en, bp_update_taken;
    logic [31:0] bp_update_pc, bp_update_target;
    btb #(.IDX_BITS(6)) u_btb (
        .clk(clk), .rst(rst),
        .pc_f(if_pc), .predict_taken(pred_taken_raw), .predict_target(pred_target),
        .update_en(bp_update_en), .update_pc(bp_update_pc),
        .update_taken(bp_update_taken), .update_target(bp_update_target)
    );
    // USE_BP=0：靜態 not-taken，用來對照動態預測的 CPI 改善
    assign pred_taken = USE_BP ? pred_taken_raw : 1'b0;

    logic [31:0] if_id_pc, if_id_inst, if_id_pred_target;
    logic        if_id_pred_taken, if_id_valid;
    always_ff @(posedge clk) begin
        if (rst || if_id_flush) begin
            if_id_pc <= 0; if_id_inst <= 32'h0000_0013;   // NOP=addi x0,x0,0
            if_id_pred_taken <= 0; if_id_pred_target <= 0; if_id_valid <= 0;
        end else if (if_id_write) begin
            if_id_pc <= if_pc; if_id_inst <= if_inst;
            if_id_pred_taken <= pred_taken; if_id_pred_target <= pred_target;
            if_id_valid <= 1'b1;
        end
    end

    // ==================== ID ====================
    logic [6:0] id_opcode, id_funct7; logic [4:0] id_rd, id_rs1, id_rs2; logic [2:0] id_funct3;
    assign id_opcode = if_id_inst[6:0];
    assign id_rd     = if_id_inst[11:7];
    assign id_funct3 = if_id_inst[14:12];
    assign id_rs1    = if_id_inst[19:15];
    assign id_rs2    = if_id_inst[24:20];
    assign id_funct7 = if_id_inst[31:25];

    localparam OP_R=7'b0110011, OP_I=7'b0010011, OP_LOAD=7'b0000011,
               OP_STORE=7'b0100011, OP_BR=7'b1100011, OP_LUI=7'b0110111,
               OP_AUIPC=7'b0010111, OP_JAL=7'b1101111, OP_JALR=7'b1100111,
               OP_SYSTEM=7'b1110011;

    logic [31:0] id_imm;
    always_comb begin
        unique case (id_opcode)
            OP_I, OP_LOAD, OP_JALR:
                id_imm = {{20{if_id_inst[31]}}, if_id_inst[31:20]};
            OP_STORE:
                id_imm = {{20{if_id_inst[31]}}, if_id_inst[31:25], if_id_inst[11:7]};
            OP_BR:
                id_imm = {{20{if_id_inst[31]}}, if_id_inst[7], if_id_inst[30:25],
                          if_id_inst[11:8], 1'b0};
            OP_LUI, OP_AUIPC:
                id_imm = {if_id_inst[31:12], 12'b0};
            OP_JAL:
                id_imm = {{12{if_id_inst[31]}}, if_id_inst[19:12], if_id_inst[20],
                          if_id_inst[30:21], 1'b0};
            default: id_imm = 0;
        endcase
    end

    logic id_reg_write, id_mem_read, id_mem_write, id_alu_src, id_mem_to_reg;
    logic id_is_branch, id_is_jal, id_is_jalr, id_use_pc, id_link;
    logic [3:0] id_alu_op;
    always_comb begin
        id_reg_write=0; id_mem_read=0; id_mem_write=0; id_alu_src=0; id_mem_to_reg=0;
        id_is_branch=0; id_is_jal=0; id_is_jalr=0; id_use_pc=0; id_link=0; id_alu_op=4'b0000;
        unique case (id_opcode)
            OP_R: begin id_reg_write=1;
                unique case (id_funct3)
                    3'b000: id_alu_op = id_funct7[5] ? 4'b0001 : 4'b0000; // SUB/ADD
                    3'b001: id_alu_op = 4'b0010;                          // SLL
                    3'b010: id_alu_op = 4'b0011;                          // SLT
                    3'b011: id_alu_op = 4'b0100;                          // SLTU
                    3'b100: id_alu_op = 4'b0101;                          // XOR
                    3'b101: id_alu_op = id_funct7[5] ? 4'b0111 : 4'b0110; // SRA/SRL
                    3'b110: id_alu_op = 4'b1000;                          // OR
                    3'b111: id_alu_op = 4'b1001;                          // AND
                    default: id_alu_op = 4'b0000;
                endcase end
            OP_I: begin id_reg_write=1; id_alu_src=1;
                unique case (id_funct3)
                    3'b000: id_alu_op = 4'b0000;                          // ADDI
                    3'b010: id_alu_op = 4'b0011;                          // SLTI
                    3'b011: id_alu_op = 4'b0100;                          // SLTIU
                    3'b100: id_alu_op = 4'b0101;                          // XORI
                    3'b110: id_alu_op = 4'b1000;                          // ORI
                    3'b111: id_alu_op = 4'b1001;                          // ANDI
                    3'b001: id_alu_op = 4'b0010;                          // SLLI
                    3'b101: id_alu_op = id_funct7[5] ? 4'b0111 : 4'b0110; // SRAI/SRLI
                    default: id_alu_op = 4'b0000;
                endcase end
            OP_LOAD:  begin id_reg_write=1; id_mem_read=1; id_alu_src=1; id_mem_to_reg=1; end
            OP_STORE: begin id_mem_write=1; id_alu_src=1; end
            OP_BR:    begin id_is_branch=1; end
            OP_LUI:   begin id_reg_write=1; id_alu_src=1; end             // 0 + imm
            OP_AUIPC: begin id_reg_write=1; id_alu_src=1; id_use_pc=1; end // pc + imm
            OP_JAL:   begin id_reg_write=1; id_is_jal=1; id_link=1; end
            OP_JALR:  begin id_reg_write=1; id_is_jalr=1; id_link=1; id_alu_src=1; end
            default: ; // SYSTEM(ecall/fence) 當 NOP
        endcase
    end

    // regfile：async 讀 / sync 寫，x0 硬 0，write-first bypass（化解距離 3 的 RAW）
    logic [31:0] regs [0:31];
    logic [31:0] id_rs1_raw, id_rs2_raw;
    assign id_rs1_raw = (id_rs1==0) ? 0 :
                        (wb_reg_write && wb_rd==id_rs1) ? wb_data : regs[id_rs1];
    assign id_rs2_raw = (id_rs2==0) ? 0 :
                        (wb_reg_write && wb_rd==id_rs2) ? wb_data : regs[id_rs2];

    // 從 EX/MEM 前遞的「正確值」：load 遞 MEM 級組合資料 mem_rdata（ex_mem_alu 是位址！），其餘遞 ex_mem_alu
    logic [31:0] mem_rdata;   // 前向宣告（MEM 段定義）
    logic [31:0] ex_mem_fwd_val;
    assign ex_mem_fwd_val = ex_mem_mem_read ? mem_rdata : ex_mem_alu;

    // ID forwarding（給 branch/jalr 比較器與 target）：從 EX/MEM 前遞
    logic [31:0] id_rs1_data, id_rs2_data;
    always_comb begin
        id_rs1_data = id_rs1_raw;
        if (id_rs1 != 0 && ex_mem_reg_write && ex_mem_rd==id_rs1) id_rs1_data = ex_mem_fwd_val;
        id_rs2_data = id_rs2_raw;
        if (id_rs2 != 0 && ex_mem_reg_write && ex_mem_rd==id_rs2) id_rs2_data = ex_mem_fwd_val;
    end

    logic id_branch_cond;
    always_comb begin
        unique case (id_funct3)
            3'b000: id_branch_cond = (id_rs1_data == id_rs2_data);                   // BEQ
            3'b001: id_branch_cond = (id_rs1_data != id_rs2_data);                   // BNE
            3'b100: id_branch_cond = ($signed(id_rs1_data) <  $signed(id_rs2_data)); // BLT
            3'b101: id_branch_cond = ($signed(id_rs1_data) >= $signed(id_rs2_data)); // BGE
            3'b110: id_branch_cond = (id_rs1_data <  id_rs2_data);                   // BLTU
            3'b111: id_branch_cond = (id_rs1_data >= id_rs2_data);                   // BGEU
            default: id_branch_cond = 1'b0;
        endcase
    end
    logic        id_actual_taken;
    logic [31:0] id_actual_target;
    logic        id_ctrl_xfer;
    assign id_ctrl_xfer = id_is_branch || id_is_jal || id_is_jalr;
    assign id_actual_taken  = if_id_valid &&
                              ((id_is_branch && id_branch_cond) || id_is_jal || id_is_jalr);
    assign id_actual_target = id_is_jalr ? ((id_rs1_data + id_imm) & ~32'd1)
                                         : (if_id_pc + id_imm);

    // mispredict：方向或目標猜錯
    logic id_mispredict;
    always_comb begin
        id_mispredict = 1'b0;
        if (if_id_valid && id_ctrl_xfer) begin
            if (id_actual_taken != if_id_pred_taken) id_mispredict = 1'b1;
            else if (id_actual_taken && (id_actual_target != if_id_pred_target)) id_mispredict = 1'b1;
        end
    end

    assign bp_update_en     = if_id_valid && id_ctrl_xfer && !id_ex_bubble;
    assign bp_update_pc     = if_id_pc;
    assign bp_update_taken  = id_actual_taken;
    assign bp_update_target = id_actual_target;

    // ---- ID/EX ----
    logic id_ex_reg_write,id_ex_mem_read,id_ex_mem_write,id_ex_alu_src,id_ex_mem_to_reg;
    logic id_ex_use_pc,id_ex_link;
    logic [3:0] id_ex_alu_op; logic [2:0] id_ex_funct3;
    logic [31:0] id_ex_rs1_data,id_ex_rs2_data,id_ex_imm,id_ex_pc;
    logic [4:0] id_ex_rs1,id_ex_rs2,id_ex_rd;
    always_ff @(posedge clk) begin
        if (rst || id_ex_bubble) begin
            id_ex_reg_write<=0; id_ex_mem_read<=0; id_ex_mem_write<=0; id_ex_alu_src<=0;
            id_ex_mem_to_reg<=0; id_ex_use_pc<=0; id_ex_link<=0; id_ex_alu_op<=0;
            id_ex_rs1_data<=0; id_ex_rs2_data<=0; id_ex_imm<=0; id_ex_pc<=0;
            id_ex_rs1<=0; id_ex_rs2<=0; id_ex_rd<=0; id_ex_funct3<=0;
        end else begin
            id_ex_reg_write<=id_reg_write; id_ex_mem_read<=id_mem_read;
            id_ex_mem_write<=id_mem_write; id_ex_alu_src<=id_alu_src;
            id_ex_mem_to_reg<=id_mem_to_reg; id_ex_use_pc<=id_use_pc; id_ex_link<=id_link;
            id_ex_alu_op<=id_alu_op; id_ex_funct3<=id_funct3;
            id_ex_rs1_data<=id_rs1_data; id_ex_rs2_data<=id_rs2_data; id_ex_imm<=id_imm;
            id_ex_rs1<=id_rs1; id_ex_rs2<=id_rs2; id_ex_rd<=id_rd; id_ex_pc<=if_id_pc;
        end
    end

    // ==================== EX ====================
    logic [1:0] fwd_a, fwd_b;
    always_comb begin
        fwd_a = 2'b00; fwd_b = 2'b00;
        if (ex_mem_reg_write && ex_mem_rd!=0 && ex_mem_rd==id_ex_rs1) fwd_a = 2'b10;
        else if (wb_reg_write && wb_rd!=0 && wb_rd==id_ex_rs1)        fwd_a = 2'b01;
        if (ex_mem_reg_write && ex_mem_rd!=0 && ex_mem_rd==id_ex_rs2) fwd_b = 2'b10;
        else if (wb_reg_write && wb_rd!=0 && wb_rd==id_ex_rs2)        fwd_b = 2'b01;
    end
    logic [31:0] ex_fwd_a, ex_fwd_b, ex_alu_a, ex_alu_b, ex_alu_result; logic ex_zero;
    always_comb begin
        unique case (fwd_a)   // load 遞 ex_mem_fwd_val（=mem_rdata），非位址
            2'b10: ex_fwd_a = ex_mem_fwd_val; 2'b01: ex_fwd_a = wb_data;
            default: ex_fwd_a = id_ex_rs1_data;
        endcase
        unique case (fwd_b)
            2'b10: ex_fwd_b = ex_mem_fwd_val; 2'b01: ex_fwd_b = wb_data;
            default: ex_fwd_b = id_ex_rs2_data;
        endcase
    end
    assign ex_alu_a = id_ex_use_pc ? id_ex_pc : ex_fwd_a;   // AUIPC 用 PC
    assign ex_alu_b = id_ex_alu_src ? id_ex_imm : ex_fwd_b;
    alu u_alu (.a(ex_alu_a), .b(ex_alu_b), .alu_op(id_ex_alu_op),
               .result(ex_alu_result), .zero(ex_zero));
    logic [31:0] ex_result;
    assign ex_result = id_ex_link ? (id_ex_pc + 32'd4) : ex_alu_result;  // JAL/JALR 寫 PC+4

    // ---- EX/MEM ----
    logic ex_mem_mem_write,ex_mem_mem_to_reg; logic [2:0] ex_mem_funct3; logic [31:0] ex_mem_store;
    always_ff @(posedge clk) begin
        if (rst) begin
            ex_mem_reg_write<=0; ex_mem_mem_read<=0; ex_mem_mem_write<=0;
            ex_mem_mem_to_reg<=0; ex_mem_alu<=0; ex_mem_store<=0; ex_mem_rd<=0; ex_mem_funct3<=0;
        end else begin
            ex_mem_reg_write<=id_ex_reg_write; ex_mem_mem_read<=id_ex_mem_read;
            ex_mem_mem_write<=id_ex_mem_write; ex_mem_mem_to_reg<=id_ex_mem_to_reg;
            ex_mem_alu<=ex_result; ex_mem_store<=ex_fwd_b; ex_mem_rd<=id_ex_rd;
            ex_mem_funct3<=id_ex_funct3;
        end
    end

    // ==================== MEM ====================
    logic [31:0] mem_word;
    logic [11:0] mem_widx;
    assign mem_widx = (ex_mem_alu - RESET_PC) >> 2;
    assign mem_word = dmem[mem_widx];
    assign tohost_we   = ex_mem_mem_write && (ex_mem_alu == TOHOST);  // tohost 攔截
    assign tohost_data = ex_mem_store;
    always_ff @(posedge clk) begin
        if (ex_mem_mem_write) dmem[mem_widx] <= ex_mem_store;
    end
    always_comb begin   // load 依 funct3 選位元組/半字/字（含符號延伸）
        unique case (ex_mem_funct3)
            3'b000: mem_rdata = {{24{mem_word[7]}},  mem_word[7:0]};   // LB
            3'b001: mem_rdata = {{16{mem_word[15]}}, mem_word[15:0]};  // LH
            3'b100: mem_rdata = {24'b0, mem_word[7:0]};                // LBU
            3'b101: mem_rdata = {16'b0, mem_word[15:0]};               // LHU
            default: mem_rdata = mem_word;                            // LW
        endcase
    end

    // ---- MEM/WB ----
    logic mem_wb_reg_write,mem_wb_mem_to_reg; logic [31:0] mem_wb_alu,mem_wb_rdata; logic [4:0] mem_wb_rd;
    logic mem_wb_valid;
    always_ff @(posedge clk) begin
        if (rst) begin
            mem_wb_reg_write<=0; mem_wb_mem_to_reg<=0; mem_wb_alu<=0; mem_wb_rdata<=0;
            mem_wb_rd<=0; mem_wb_valid<=0;
        end else begin
            mem_wb_reg_write<=ex_mem_reg_write; mem_wb_mem_to_reg<=ex_mem_mem_to_reg;
            mem_wb_alu<=ex_mem_alu; mem_wb_rdata<=mem_rdata; mem_wb_rd<=ex_mem_rd;
            mem_wb_valid<=1'b1;
        end
    end

    // ==================== WB ====================
    assign wb_reg_write = mem_wb_reg_write;
    assign wb_rd = mem_wb_rd;
    assign wb_data = mem_wb_mem_to_reg ? mem_wb_rdata : mem_wb_alu;
    always_ff @(posedge clk) if (wb_reg_write && wb_rd!=0) regs[wb_rd] <= wb_data;

    // ==================== HAZARD / FLUSH ====================
    logic id_uses_rs1, id_uses_rs2;
    assign id_uses_rs1 = (id_opcode==OP_R)||(id_opcode==OP_I)||(id_opcode==OP_LOAD)||
                         (id_opcode==OP_STORE)||(id_opcode==OP_BR)||(id_opcode==OP_JALR);
    assign id_uses_rs2 = (id_opcode==OP_R)||(id_opcode==OP_STORE)||(id_opcode==OP_BR);
    // load-use：EX 是 load、rd 命中 ID 來源
    logic load_use_hazard;
    assign load_use_hazard = id_ex_mem_read && id_ex_rd!=0 &&
                             ((id_uses_rs1 && id_ex_rd==id_rs1) ||
                              (id_uses_rs2 && id_ex_rd==id_rs2));
    // branch-use：ID 是控制轉移、來源還在 EX（值還沒到 EX/MEM，ID forward 補不到）
    logic branch_use_hazard;
    assign branch_use_hazard = id_ctrl_xfer && id_ex_reg_write && id_ex_rd!=0 &&
                               ((id_uses_rs1 && id_ex_rd==id_rs1) ||
                                (id_uses_rs2 && id_ex_rd==id_rs2));

    always_comb begin
        pc_write=1'b1; if_id_write=1'b1; id_ex_bubble=1'b0; if_id_flush=1'b0;
        if (load_use_hazard || branch_use_hazard) begin
            pc_write=1'b0; if_id_write=1'b0; id_ex_bubble=1'b1;   // stall（優先）
        end else if (id_mispredict) begin
            if_id_flush=1'b1;                                     // flush IF/ID 一條
        end
    end

    // 下一 PC：mispredict 修正 > 預測 taken > PC+4
    always_comb begin
        if (id_mispredict)      if_pc_next = id_actual_taken ? id_actual_target : (if_id_pc + 32'd4);
        else if (pred_taken)    if_pc_next = pred_target;
        else                    if_pc_next = if_pc + 32'd4;
    end

    assign perf_stall  = load_use_hazard || branch_use_hazard;
    assign perf_flush  = id_mispredict && !(load_use_hazard || branch_use_hazard);
    assign retire_valid = mem_wb_valid;

    assign dbg_reg_data = (dbg_reg_sel==0) ? 0 : regs[dbg_reg_sel];
    assign dbg_mem_data = dmem[dbg_mem_sel];
    assign dbg_id_pc = if_id_pc; assign dbg_id_inst = if_id_inst; assign dbg_id_valid = if_id_valid;
endmodule
```
</details>

<details>
<summary>fp_tb.cpp（testbench + performance counter + CPI 報告）</summary>

```cpp
// fp_tb.cpp — 跑到 tohost 或上限，dump 暫存器 + 記憶體 + CPI 報告
#include "Vcore.h"
#include "verilated.h"
#include <cstdio>
#include <cstdint>
static Vcore *dut;
static void tick() { dut->clk=0; dut->eval(); dut->clk=1; dut->eval(); }

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    dut = new Vcore;
    dut->rst = 1; tick(); tick(); dut->rst = 0;

    uint64_t cycles=0, stalls=0, flushes=0;
    int pass=-1; const int MAX=200000;
    for (int c=0; c<MAX; c++) {
        dut->eval();
        if (dut->perf_stall) stalls++;
        if (dut->perf_flush) flushes++;
        if (dut->tohost_we && dut->tohost_data!=0) { pass=(int)dut->tohost_data; cycles=c; tick(); break; }
        tick(); cycles=c+1;
    }
    printf("=== final register state ===\n");
    for (int i=1;i<=31;i++){ dut->dbg_reg_sel=i; dut->eval();
        printf("x%-2d = %11d  (0x%08x)\n", i,(int32_t)dut->dbg_reg_data,(uint32_t)dut->dbg_reg_data); }
    printf("\n=== dmem[0..7] ===\n");
    for (int i=0;i<8;i++){ dut->dbg_mem_sel=i; dut->eval();
        printf("dmem[%d] = %d\n", i,(int32_t)dut->dbg_mem_data); }
    printf("\n=== tohost result ===\n");
    if (pass==1)      printf("PASS (tohost=1)\n");
    else if (pass>1)  printf("FAIL, sub-test #%d (tohost=0x%x)\n", pass>>1, pass);
    else              printf("TIMEOUT\n");

    const uint64_t FILL=4;
    uint64_t instret = (cycles>FILL+stalls+flushes) ? cycles-FILL-stalls-flushes : 0;
    printf("\n=== performance counters ===\n");
    printf("cycles  = %llu\ninstret = %llu\nstalls  = %llu\nflushes = %llu\n",
        (unsigned long long)cycles,(unsigned long long)instret,
        (unsigned long long)stalls,(unsigned long long)flushes);
    if (instret) {
        double cpi=(double)cycles/(double)instret;
        printf("CPI     = %.4f\n", cpi);
        printf("  ideal(1.0) + stall/inst %.4f + flush/inst %.4f + fill/inst %.4f\n",
            (double)stalls/instret,(double)flushes/instret,(double)FILL/instret);
    }
    delete dut; return 0;
}
```
</details>

<details>
<summary>驗證 script（組譯 + verilate + 跑，真跑確認可行）</summary>

```bash
#!/bin/bash
# run.sh — 組譯所有測試、build 兩種預測配置、跑並對照 CPI
set -e
ASM() { # <in.S> <out.hex>
  riscv64-unknown-elf-gcc -march=rv32i -mabi=ilp32 -nostdlib -Ttext=0x80000000 -o t.elf "$1"
  riscv64-unknown-elf-objcopy -O binary t.elf t.bin
  python3 -c "
import struct
d=open('t.bin','rb').read(); d+=b'\x00'*((4-len(d)%4)%4)
open('$2','w').write(''.join('%08x\n'%struct.unpack('<I',d[i:i+4])[0] for i in range(0,len(d),4)))"
}
BUILD_RUN() { # <hex> <use_bp> <mdir>
  verilator --cc core.sv btb.sv alu.sv --exe fp_tb.cpp --Mdir "$3" \
    -Wno-WIDTH -Wno-UNOPTFLAT -Wno-CASEINCOMPLETE --top-module core \
    -GINIT_FILE="\"$1\"" -GUSE_BP=$2 >/dev/null 2>&1
  make -s -C "$3" -f Vcore.mk Vcore >/dev/null 2>&1
  ./"$3"/Vcore
}
ASM selftest.S selftest.hex; ASM sort.S sort.hex; ASM fib.S fib.hex; ASM sum.S sum.hex
echo "=== 自檢 ==="; BUILD_RUN selftest.hex 1 obj_st | grep -A1 tohost
echo "=== 排序 ===";  BUILD_RUN sort.hex 1 obj_s | grep -A9 dmem
echo "=== CPI 對照 ==="
for b in sum fib sort; do
  echo "[$b static-NT]"; BUILD_RUN $b.hex 0 obj_p | grep "CPI "
  echo "[$b BTB]";       BUILD_RUN $b.hex 1 obj_p | grep "CPI "
done
```
</details>

---

## 八、延伸挑戰

做完必做六項，這顆 core 是紮實的地基。想長大，往這幾個方向：

1. **M 擴充（MUL/DIV）**：加 `mul`/`mulh`/`div`/`rem`。乘法可用單週期 DSP-style（`a*b`，verilator 直接算）或 pipelined multiplier；除法通常多週期（restoring/non-restoring），要在 EX 級加「多週期 stall」——這是你第一次遇到「一條指令佔用 EX 好幾拍」的 structural hazard，hazard unit 要擴充。裝了 `-march=rv32im` 的 toolchain 直接編 `*`、`/` 就能測。

2. **接 I-cache（練習 D 的成果）**：把 imem 從「一拍給指令」換成練習 D 的 direct-mapped I-cache——cache hit 一拍、miss 要 stall 等 memory。這讓你的 CPI 報告多一個 **i-cache miss stall** 分量，也讓 IF 級第一次會 stall。量 miss rate 對 CPI 的影響，對照第五節的表。

3. **接 D-cache（Ch 27）**：MEM 級也 cache 化，load/store miss 要 stall 整條 pipeline。這會和既有的 load-use stall 疊加，是 hazard 綜合的好練習。

4. **CSR + timer 中斷（Part 5，加分項）**：接 Part 5 的 `csr_file`（mstatus/mtvec/mepc/mcause，位址 0x300/0x305/0x341/0x342）和 CLINT timer。難點是 **precise exception**——中斷來時要在精確的指令邊界 flush 整條 pipeline、存對的 mepc。這比 branch flush 難，是 branch flush 的進階版。`_scratch` 已有 Part 5 的 CSR 骨架可接。

5. **跑 CoreMark**：終極 benchmark。要補齊 M 擴充、能載入 `.data`/`.bss`、有最小 runtime（`crt0`）。跑得動 CoreMark 的 core，微架構上已經是真的能用的 CPU。這是「玩具 → 能用」的分界線。

6. **上 FPGA（Ch 38 原理）**：把 imem/dmem 換成 BRAM、加 UART 當 I/O，過 synthesis + place & route 上板。verilator 驗過的 RTL 通常能直接合成（避開不可合成的 `initial`——BTB 陣列初始化要改成 reset 邏輯或 memory init file）。這是 Ch 38 的兌現。

---

## 九、銜接：這顆 core 之後怎麼長大

你手上這顆 5 級 in-order pipelined RV32I，是一條清楚的成長路線的起點。

- **接 Ch 37（Rocket / BOOM）**：你做的就是 **Rocket** 的教學版——同樣 5 級 in-order，同樣 forward/stall/flush。現在去讀 rocket-chip 的 `RocketCore.scala`，搜 `bypass`（=你的 forwarding）、`ctrl_stalld`（=你的 stall）、`take_pc`（=你的 mispredict redirect），你會認得每一塊，只是它多了 cache、MMU、FPU、CSR 和海量 corner case。想看**亂序**怎麼從你這顆長出來，讀 BOOM——它把你的 in-order pipeline 換成 Tomasulo + register renaming + ROB（Ch 36 的概念的工業實作）。

- **接 `architecture/riscv` emulator 對照**：你有硬體模型（這顆 core），那門課有軟體模型（RISC-V ISA 語意）。拿同一支程式在兩邊跑，逐指令對 PC + 暫存器——這正是 spike 對拍的原理（本課環境 spike 未裝，但那門課的 emulator 或你自己寫的都能當 reference model）。這是驗真實硬體最有效的方法：差一個 bit 就停在那條指令。

- **接 compiler backend（讓你自編 code 跑在自製硬體上）**：你已經能讓 gcc 編出的 `.S` 跑在這顆 core 上。下一步是理解 **compiler 怎麼為你的微架構排程指令**——為什麼 `-O2` 的 code 在同一顆 core 上 CPI 更低（答案在第五節：編譯器在 load 和 use、branch 和它的來源之間塞指令，消掉 load-use / branch-use stall）。做過硬體再回頭看 LLVM 的 instruction scheduling / register allocation，你會第一次真懂「compiler 在為誰服務」。這條線把 ISA → 微架構 → compiler 三層打通，是頂尖 toolchain / 效能工程師的完整地基。

你從邏輯閘一路做到這裡：親手做出「那顆真的執行 RISC-V 指令的矽」的模型，用工業界的方法驗證它是對的、量出它多快、知道它慢在哪。這門課的承諾——「做完能讀懂工業 core」——現在兌現。去讀 Rocket 吧，它不再是天書。

---

## 十、延伸閱讀

- **[riscv-tests 官方 repo](https://github.com/riscv-software-src/riscv-tests) 的 `isa/rv32ui/` 與 `env/p/`**：把第四節的自檢程式換成官方 `add.S`/`lw.S`/`beq.S`/`jalr.S` 等，就是正式合規測試。讀 `env/p/riscv_test.h` 的 `RVTEST_PASS`/`RVTEST_FAIL` 巨集，看官方 `tohost` 慣例（和我們用的一模一樣，只是它用 `ecall` 觸發，你可以在 SYSTEM opcode 加對應處理）。這是把「自己驗」升級到「官方合規」的實作入口。
- **[Sodor 教學 core 系列](https://github.com/ucb-bar/riscv-sodor) 的 `rv32_5stage`**：官方教學 5 級 core（Chisel）。把 `dpath.scala`（datapath）、`cpath.scala`（control/hazard）、`core.scala` 對照你的 `core.sv`——同樣五級、同樣 forward/stall/flush，是最好的「完整標準答案」。特別看它 branch resolve 放哪一級、怎麼取捨 penalty vs branch-use（和你的 ID resolve 決策對照）。
- **[rocket-chip 的 `RocketCore.scala`](https://github.com/chipsalliance/rocket-chip)**：SiFive 工業級 5 級 in-order core 的 RTL。做完這個 project，搜它的 `bypass` / `ctrl_stalld` / `take_pc`，你會看到自己剛寫的邏輯的工業版。這是第九節「銜接 Ch 37」的實際入口。
- **[picorv32](https://github.com/YosysHQ/picorv32)（Claire Wolf）**：一個真正被人用的極簡 RV32 core（Verilog）。它是**多週期**不是 pipeline，設計取捨和你這顆不同——讀它體會「同樣的 ISA，不同的微架構哲學」，也看一個能上 FPGA、能跑真程式的 core 的完整工程（Makefile、testbench、firmware）長怎樣，是你第七/八節加分項的參考範本。
- **《Computer Organization and Design, RISC-V Edition》(Patterson & Hennessy) 第 4.7–4.10 節**：4.7 的完整 pipelined datapath（圖 4.60，含 forwarding + hazard unit 全畫在一張圖）是你 `core.sv` 的教科書權威版；4.9 的分支預測、4.10 開頭的 CPI 與 ILP 討論，接得上你第五節的 CPI 報告與歸因。實作有疑義時的主要仲裁。
- **《Digital Design and Computer Architecture, RISC-V Edition》(Harris & Harris) 第 7.5–7.6 節**：完整 pipelined processor 的 HDL 範例（Verilog）與 hazard unit，風格和你的 skeleton 幾乎一樣。卡在語法或某個 hazard 的偵測條件時，對照它的 HDL 真值表最快。
