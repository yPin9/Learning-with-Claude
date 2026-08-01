# Ch 20 — pipeline 完整整合 + 打穿 riscv-tests

> **目標**：把 Ch 13–19 的所有東西——五級切分、pipeline register、forwarding、load-use stall、branch flush、hazard detection unit——接成一顆完整、能自我保護的 pipelined `core`。跑一支同時混合 RAW / load-use / control hazard 的程式，把最終暫存器狀態和**手算預期**、以及**單週期版**逐項對照，確認「結果一模一樣、cycle 數不同」——這正是 pipeline 的本質：同樣的語意，更高的吞吐。並談怎麼延伸去打穿官方 riscv-tests。
> **環境**：WSL + verilator 4.038。輸出皆真跑。這是深挖章，Part 2 的收尾與驗收。

## 為什麼需要「完整整合」這一章？

前面六章我們一塊一塊做：切五級、加 pipeline register、接 forwarding、補 load-use stall、處理 branch flush、綜合成 hazard unit。每一塊都單獨驗過。但**單獨對不代表湊起來對**——pipeline 的 bug 幾乎都出在「多個機制同拍互動」的接縫處。

這一章的任務是驗收：把全部接成一顆 core，餵一支**故意把三種 hazard 混在一起**的程式，看它能不能全對。驗收的標準不是「看起來會動」，而是**和一個你完全信得過的參考對照**——手算的預期值，以及邏輯上等價的單週期 core。三者逐暫存器一致，才叫真的做完。

pipeline 正確性的黃金定律：**pipelined core 對任意程式的最終架構狀態（暫存器 + 記憶體），必須和單週期 core 完全相同**。差別只准出現在「花幾個 cycle」，不准出現在「算出什麼」。這一章就是拿一支程式把這條定律驗給你看。

## 先建立直覺：同一份食譜，兩種廚房

單週期 core 像一個廚師從頭到尾做完一道菜才做下一道：洗菜、切、炒、擺盤，全做完，才碰下一道。一道菜要一個「完整週期」，週期很長（要塞下最慢的一道工序）。

pipelined core 像流水線廚房：洗菜工、切菜工、炒菜工、擺盤工各站一個工位，五道菜同時在不同工位進行。每個工位只做一件事，所以「一拍」很短。但菜之間會互相卡（切菜工要等炒菜工的鍋——hazard），所以要有領班（hazard unit）協調：該傳的傳（forward）、該等的等（stall）、該丟的丟（flush）。

```
   單週期：一道菜一個長週期
   菜1 [====== 洗切炒擺 ======]
   菜2                        [====== 洗切炒擺 ======]

   pipeline：五工位並行，週期短，但要協調
   菜1 [洗][切][炒][擺]
   菜2    [洗][切][炒][擺]
   菜3       [洗][切][炒][擺]   ← 三道同時在不同工位
```

**關鍵**：兩種廚房**做出來的菜必須一模一樣**（同樣的食譜、同樣的成品）。流水線只是更快出菜（吞吐高），不能把菜做錯。這一章要驗的就是這件事——pipeline 的成品（暫存器/記憶體狀態）和單週期一致，只是出菜節奏不同。

## 核心概念：完整 core 的資料流全景

把整顆 core 攤開，資料從 IF 流到 WB，hazard unit 從旁監看每一級：

```
   ┌─IF─┐   ┌─ID──────┐   ┌─EX───────┐   ┌─MEM──┐   ┌─WB──┐
   │ PC │──▶│ decode  │──▶│ ALU      │──▶│ dmem │──▶│ mux │──▶ regfile 寫
   │imem│   │ regfile │   │ forward  │   │ 讀寫 │   │     │
   └────┘   │ 讀 rs1/2│   │ mux      │   └──────┘   └─────┘
      ▲     │ branch  │   └──────────┘       │         │
      │     │ resolve │        ▲             │         │
      │     └─────────┘        │             │         │
   pc_write  if_id_write   fwd_a/fwd_b    ex_mem     wb_data
      │         │              │          (forward 來源)
   ┌──┴─────────┴──────────────┴──────────────┴─────────┐
   │            HAZARD DETECTION UNIT                    │
   │  forward(EX/ID) · load-use stall · branch flush     │
   │  優先序：stall > flush；EX/MEM > MEM/WB             │
   └────────────────────────────────────────────────────┘
```

- **IF**：PC 選下一位址（PC+4 或 branch target），讀 imem 抓指令。`pc_write` 控制凍不凍。
- **ID**：解碼、讀 regfile、算立即數；**branch 在這裡提前 resolve**（含 ID forwarding）。`if_id_write`/`if_id_flush` 控制凍/清。
- **EX**：ALU 計算，兩個運算元經 **forwarding mux**（`fwd_a`/`fwd_b`）選正確來源。`id_ex_bubble` 控制插不插泡。
- **MEM**：load/store 存取 dmem。
- **WB**：選 ALU 結果或記憶體資料寫回 regfile。

hazard unit 橫跨所有級，每拍決定 forward 哪些、要不要 stall、要不要 flush。這張圖你要能在腦中默畫——它是 Part 2 的全部。

## 底層機制：三種 hazard 在同一支程式裡

要驗收，得有一支同時踩三種 hazard 的程式。我們精心設計這支：

```asm
_start:
    lui  x10, 0x80000       # x10 = 0x80000000（資料區基底）
    addi x1, x0, 10         # x1 = 10
    addi x2, x1, 5          # x2 = x1+5 = 15   [RAW：用剛算的 x1，EX/MEM forward]
    add  x3, x2, x1         # x3 = 15+10 = 25  [RAW：兩個來源都要 forward]
    sw   x3, 0(x10)         # mem[base] = 25
    lw   x4, 0(x10)         # x4 = 25          [load]
    add  x5, x4, x4         # x5 = 50          [load-use：緊接用 x4 → stall 1 拍]
    addi x6, x0, 7
    beq  x1, x6, skip       # 10==7? 否 → not-taken
    addi x7, x0, 1          # x7 = 1           [not-taken，正常執行]
skip:
    addi x8, x0, 99         # x8 = 99
    beq  x1, x1, done       # 10==10 → taken   [control：flush]
    addi x9, x0, 555        # POISON：taken 應跳過，不該執行
done:
    addi x9, x0, 2          # x9 = 2           [branch target]
halt:
    beq  x0, x0, halt
```

這支程式每一種 hazard 都踩到：

- **RAW（forward 解決）**：`addi x2, x1` 用剛算的 x1、`add x3, x2, x1` 兩個來源都要 forward。這些靠 EX forwarding，0 penalty。
- **load-use（stall 解決）**：`lw x4` 緊接 `add x5, x4, x4`——forwarding 趕不上，stall 一拍。
- **control（flush 解決）**：兩個 beq。第一個 not-taken（照常走）；第二個 taken，要 flush 掉毒指令 `addi x9, x0, 555`。

**先手算全部預期值**（這是我們的黃金參考）：

| 暫存器 | 預期值 | 怎麼來的 |
|---|---|---|
| x1 | 10 | addi |
| x2 | 15 | 10 + 5（RAW forward x1）|
| x3 | 25 | 15 + 10（RAW forward x2、x1）|
| x4 | 25 | load mem[base]（sw 存了 25）|
| x5 | 50 | 25 + 25（load-use stall 後 forward x4）|
| x6 | 7 | addi |
| x7 | 1 | 第一個 beq not-taken，正常執行 |
| x8 | 99 | addi |
| x9 | 2 | 第二個 beq taken，跳過毒指令，執行 target |
| x10 | 0x80000000 | lui |

特別注意 **x9=2**：如果 branch flush 沒做好，毒指令 `addi x9, x0, 555` 會執行，x9 變 555——這是 control hazard 有沒有處理對的試金石。

## 範例：完整 core 真跑，逐暫存器對照

把上面程式組譯成 hex，餵進完整 `core`，跑 60 拍讓所有指令流完，dump 最終暫存器：

```
$ verilator --cc core.sv alu.sv --exe core_dump_tb.cpp \
      -GINIT_FILE='"mixed.hex"' --Mdir obj_mx -Wno-WIDTH -Wno-UNOPTFLAT --top-module core
$ make -s -C obj_mx -f Vcore.mk Vcore
$ ./obj_mx/Vcore
```

真實輸出：

```
=== final register state after 60 cycles ===
x1  (ra  ) = 10
x2  (sp  ) = 15
x3  (gp  ) = 25
x4  (tp  ) = 25
x5  (t0  ) = 50
x6  (t1  ) = 7
x7  (t2  ) = 1
x8  (s0  ) = 99
x9  (s1  ) = 2
x10 (a0  ) = -2147483648
```

（`x10 = -2147483648` 就是 `0x80000000` 的有號十進位——最高位是 1，printf 用 `%d` 印成負數，值完全正確。）

**逐項對照手算表——十個暫存器全中**：

- x2=15、x3=25：RAW forwarding 正確（用到剛算的 x1、x2）。
- x4=25、x5=50：sw/lw 記憶體來回正確（x4 讀出剛存的 25），load-use stall 後 x5=25+25=50 正確。
- x7=1：第一個 beq（10≠7）not-taken，正常執行。
- **x9=2**：第二個 beq（10==10）taken，**毒指令 555 沒執行**，target 的 2 生效。**branch flush 正確**。

三種 hazard 混在一起，最終狀態和手算**一模一樣**。這顆 pipelined core 通過驗收。

### 記憶體也要對

x4=25 這一項其實同時驗了記憶體：`sw x3, 0(x10)` 把 25 寫進 dmem，`lw x4, 0(x10)` 讀回來得到 25。如果 store/load 的位址計算或 MEM 級時序錯了，x4 就不會是 25。所以這一項是「記憶體 round-trip + load-use」的雙重驗證。

## 對照單週期：結果同、cycle 數不同

pipeline 正確性的定義是「和單週期結果一致」。上面我們已經用**手算**（單週期的語意就是逐條循序執行，手算等於在腦中跑單週期）對照過，十個暫存器全中。現在看**差別**在哪——cycle 數。

這支程式**執行了 14 條指令**（15 條裡有 1 條毒指令被 flush，不算）。統計跑到 halt 之前的 hazard：

```
pre-halt: stalls=2 flushes=1
```

- **stall = 2 拍**：一拍是 `lw x4` → `add x5` 的 load-use；一拍是某個 branch 的 branch-use（branch 的來源剛算出、還在 EX）。
- **flush = 1 拍**：第二個 beq taken，flush 掉毒指令那一拍。

算 cycle 數（忽略 5 級 pipeline 的填充延遲，看穩態）：

```
   單週期：每條指令 1 個(長)週期
     14 條指令 → 14 個長週期

   pipeline：每條指令 1 個(短)週期 + hazard 的 bubble
     14 條 + 2 stall + 1 flush = 17 個短週期(+ 5 級填充)
```

**CPI（cycles per instruction）**：

- 單週期 CPI = 1，但週期很長（要塞下 IF+ID+EX+MEM+WB 全部工序的關鍵路徑）。
- pipeline 理想 CPI = 1，但實際 = (14+3)/14 ≈ **1.21**（hazard 讓它超過 1），週期短（只要塞下最慢的**一級**）。

關鍵在**週期長度**：pipeline 一拍只做一級，關鍵路徑短很多，時脈可以快好幾倍。就算 CPI 從 1 升到 1.21，只要時脈快 3~4 倍，總執行時間（= 指令數 × CPI × 週期）還是大勝。**這就是 pipeline 的本質交易：用「CPI 略升（hazard bubble）」換「時脈大升（關鍵路徑變短）」，淨賺吞吐。**

```
   總時間 = 指令數 × CPI × 週期長度
   單週期：14 × 1.00 × T_long
   pipeline：14 × 1.21 × T_short   (T_short ≈ T_long / 4)
            → pipeline 約快 3 倍
```

Ch 23（CPI 分析）、Ch 24（關鍵路徑）會把這筆帳算到底。這裡你先建立直覺：**結果一定要一樣（十個暫存器對照），差別只准在 cycle 數（CPI）和週期長度**。

## 打穿 riscv-tests：從自製程式到官方驗證

我們用自己精心設計的程式驗收，教學夠了。但要真正確認 core 沒 bug，得上**官方 riscv-tests**——一套 RISC-V 官方的一致性測試，每條指令、每個 corner case 都有對應測試。

riscv-tests 的慣例（我們在 Part 1 練習 A 已用過）：每個測試程式跑完會把「通過/失敗」的結果寫進一個約定的記憶體位址 `tohost`。約定是：

```
   x3(gp) 存 test number
   通過 → tohost = 1
   失敗 → tohost = (失敗的 test number << 1) | 1，且 gp 指向出錯的 case
```

testbench 監看 `tohost` 位址的寫入，看到非零就停：值 = 1 代表 PASS，其他代表第幾個 sub-test 失敗。要讓我們的 pipelined core 打穿 `rv32ui`（RV32 user-level integer 測試），需要：

1. **補齊指令集**：本課教學 core 只實作了子集（R/I 算術、LW/SW、BEQ/BNE、LUI）。riscv-tests 會用到全部 RV32I——SLTI/SLTIU、所有 branch（BLT/BGE/BLTU/BGEU）、JAL/JALR、AUIPC、所有 shift。要把 decode 和 ALU 補全（Ch 9–11 的完整版）。
2. **支援 `tohost` 機制**：testbench 監看特定記憶體位址，收到寫入就判定 PASS/FAIL。
3. **處理 JAL/JALR 的 control hazard**：跳轉指令也要 flush（同 branch），JALR 還要 branch-use stall（目標算 rs1）。
4. **對拍（可選但推薦）**：讓 core 每 retire 一條指令就和 **spike**（官方 reference model）比對 PC 和暫存器狀態，差一個 bit 就停。spike 本課環境未裝（可選），沒有它就靠 riscv-tests 的自檢慣例（`tohost`）驗證。

打穿 rv32ui 的完整流程和把指令補全的細節，放在 **練習 B** 和 Final Project。這一章你先確認：**混合三種 hazard 的自製程式，pipelined core 結果和手算/單週期完全一致**——這是進入 riscv-tests 之前的必要地基。地基不穩就上官方測試，你會淹沒在「到底是指令沒實作還是 hazard 沒處理對」的混亂裡。

## 對比取捨

| 面向 | 單週期 | pipeline（本課） |
|---|---|---|
| CPI | 1（固定） | 理想 1，實際 >1（hazard bubble） |
| 週期長度 | 長（塞全部工序） | 短（塞最慢一級） |
| 吞吐 | 低 | 高（淨賺，時脈補回 CPI） |
| 複雜度 | 低（無 hazard） | 高（forward/stall/flush/優先序） |
| 最終架構狀態 | 基準 | **必須完全相同** |
| 驗證方式 | 直接對答案 | 對單週期/手算 + riscv-tests |

| 驗收層次 | 涵蓋 | 信心 |
|---|---|---|
| 手算對照（本章） | 精心設計的混合 hazard 程式 | 中（人挑的 case） |
| 單週期對拍 | 任意程式，逐暫存器比 | 高（等價性） |
| riscv-tests | 官方每指令 corner case | 很高（合規） |
| spike 逐指令對拍 | 任意程式，每拍比 PC+reg | 最高（工業標準） |
| formal（Ch 39） | 數學證明等價 | 極高（窮盡） |

## 踩雷區

**雷 1：以為「每個模組單獨測過，整合一定對」。**
- 錯誤直覺：「forwarding 測過、stall 測過、flush 測過，接起來就沒事」。
- 正確認識：pipeline 的 bug 幾乎都在**接縫**——多個機制同拍互動的地方（Ch 19 的優先序就是為此）。單獨對不保證組合對。必須用**混合 hazard 的程式**整合測，而且要和**獨立的參考**（手算/單週期/spike）對照，不能只看「有沒有 crash」。整合測是獨立的一關，不是模組測的加總。

**雷 2：只驗暫存器，忘了驗記憶體。**
- 錯誤直覺：「最終暫存器對了就是對了」。
- 正確認識：store 的效果在**記憶體**，暫存器 dump 看不到。若 `sw` 寫錯位址/時序，而後面沒 `lw` 讀回來，暫存器全對你也發現不了 bug。驗收程式要有 store→load round-trip（本章 x4=25 就是），或直接 dump 記憶體。架構狀態 = 暫存器 **+ 記憶體**，兩個都要對。

**雷 3：用 cycle 數不同來判斷「pipeline 錯了」。**
- 錯誤直覺：「pipeline 跑的 cycle 數和單週期不一樣，一定哪裡錯了」。
- 正確認識：cycle 數**本來就該不同**——那是 pipeline 的重點（CPI 和週期長度都變了）。正確性只看**最終架構狀態**（暫存器 + 記憶體）一不一致，不看花幾拍。cycle 數不同是 feature 不是 bug。你要驗的是「算出什麼」相同，不是「花多久」相同。

**雷 4：拿教學子集 core 直接衝 riscv-tests，指令沒補全。**
- 錯誤直覺：「hazard 都處理好了，直接跑官方測試看過不過」。
- 正確認識：本課教學 core 只實作 RV32I 的子集（夠展示 hazard 就好）。riscv-tests 的 `rv32ui` 會用到**全部** RV32I 指令（JAL/JALR/AUIPC/所有 branch/所有 shift/SLTI...）。沒補全就衝，會卡在「指令沒實作」而非「hazard 沒對」，混淆你的除錯方向。順序是：先補全指令（Ch 9–11 完整版）→ 加 tohost 機制 → 處理 jump 的 control hazard → 才衝 riscv-tests。

## 進階延伸

- **spike 逐指令對拍是工業標準**：本章用「跑完 dump 暫存器和手算比」是教學版，只驗最終狀態。工業做法是每 retire 一條指令，就把你的 core 狀態（PC + 32 個暫存器 + 剛做的記憶體存取）和 spike（官方 reference）**逐條比對**，差一個 bit 立刻停在那條指令。這樣 bug 一出現就抓到，不用等跑完猜哪裡錯。本課環境 spike 未裝（可選），但你裝了之後，這是驗真實 core 最有效的方法——Final Project 會用。
- **pipeline 填充與排空**：5 級 pipeline 開頭要 5 拍才第一條指令走完 WB（填充 fill），結尾最後一條也要 5 拍走完（排空 drain）。程式越短，這 5 拍的固定開銷佔比越大（CPI 被拉高）。長程式攤平後可忽略。本章跑 60 拍讓所有指令包含填充排空全流完，才 dump。真做 benchmark 要考慮這個暖機效應（Ch 23）。
- **中斷/例外會打斷這一切**：本課 Part 2 的 core 假設「指令乖乖循序流完」。真 core 遇到中斷/例外（Part 5）要在任意點**精確**停下、flush 整條 pipeline、記下正確的 PC（precise exception）。這比 branch flush 難得多——要保證「例外前的指令全 commit、之後的全丟」。pipeline 的 precise exception 是 Ch 32 的硬骨頭，本章的 branch flush 是它的簡化前身。
- **從這顆 core 到 Rocket**：你做完的這顆 5 級 in-order pipelined core，微架構上就是 SiFive **Rocket** core 的教學版本。Rocket 也是 5 級 in-order，只是多了 cache、MMU、FPU、CSR、更完整的 hazard 處理和大量 corner case。你現在去讀 rocket-chip 的 RTL，`RocketCore.scala` 裡的 forwarding、stall、flush 邏輯，你會認得——「這就是我做過的那個」。這是本課「做完能讀工業 core」承諾的兌現點。

## 本章重點整理

- **pipeline 正確性黃金定律**：pipelined core 對任意程式的最終架構狀態（暫存器 + 記憶體），必須和單週期完全相同。差別只准在 cycle 數（CPI）和週期長度。
- 完整 core = Ch 13–19 全部接起來：五級 + pipeline register + EX/ID forwarding + load-use stall + branch flush + hazard unit（含優先序）。
- 驗收用**混合三種 hazard**（RAW/load-use/control）的程式，和**手算 + 單週期**逐暫存器對照。本章十個暫存器全中，含 x9=2（毒指令被 flush 沒執行）這個 control hazard 試金石。
- 記憶體也要驗（store→load round-trip，本章 x4=25）。架構狀態 = 暫存器 + 記憶體。
- **cycle 數不同是 feature**：pipeline 用 CPI 略升（本例 ~1.21，2 stall + 1 flush）換時脈大升（關鍵路徑短），淨賺吞吐。
- 打穿 riscv-tests 需先補全指令集、加 tohost 機制、處理 jump 的 control hazard，再上官方測試（練習 B / Final Project）。

## 自我檢核

- [ ] 我能默畫完整 pipelined core 的五級資料流，標出 hazard unit 監看哪些訊號、控制哪些 enable/flush。
- [ ] 我能手算本章混合程式的十個暫存器預期值，並說出每個值踩到哪種 hazard。
- [ ] 我能解釋為什麼 x9 必須是 2 而非 555，以及它驗的是哪個機制。
- [ ] 我能說清楚「pipeline 正確性只看最終架構狀態、cycle 數本該不同」，並反駁「cycle 數不同 = 錯了」。
- [ ] 我能算出這支程式的 pipeline CPI（含 stall/flush），並解釋為什麼 CPI>1 仍勝過單週期。
- [ ] 我能列出把教學 core 打穿 riscv-tests 之前必須先做的四件事。
- [ ] 我能解釋為什麼「模組單獨測過」不等於「整合正確」，以及整合測為什麼要混合 hazard + 對照獨立參考。

## 延伸閱讀

- **《Computer Organization and Design, RISC-V Edition》(Patterson & Hennessy) 第 4.7 節末的完整 pipelined datapath（圖 4.60）與第 4.10 節「Parallelism via Instructions」開頭的 CPI 討論**：圖 4.60 是本章「完整 core 全景」的教科書權威版，把 forwarding unit、hazard detection unit 全畫在一張 datapath 上；4.10 開頭把 pipeline 的吞吐 vs CPI 交易講清楚，接得上本章的「結果同、cycle 數不同」。
- **[riscv-tests 官方 repo](https://github.com/riscv-software-src/riscv-tests) 的 `isa/rv32ui/` 和 `env/` 目錄**：看官方一致性測試長怎樣、`tohost` 機制怎麼運作（讀 `env/p/riscv_test.h` 和 `RVTEST_PASS`/`RVTEST_FAIL` 巨集）。這是把你的 core 從「自己驗」升級到「官方合規」的入口，練習 B 和 Final Project 會實際打穿它。
- **[Sodor rv32_5stage 完整原始碼](https://github.com/ucb-bar/riscv-sodor/tree/master/src/main/scala/rv32_5stage)**：官方教學 5 級 core 的完整整合版（Chisel）。把 `dpath.scala`（datapath）、`cpath.scala`（control/hazard）、`core.scala` 一起讀，對照你做完的這顆——同樣的五級、同樣的 forward/stall/flush，是最好的「完整標準答案」。它也附了跑 riscv-tests 的流程，可以照做。
- **[rocket-chip 的 `RocketCore.scala`](https://github.com/chipsalliance/rocket-chip)**：SiFive 工業級 5 級 in-order core 的 RTL。做完本章去搜它的 `bypass`（forwarding）、`ctrl_stalld`（stall）、`take_pc`（flush/redirect）邏輯，你會發現微架構和你做的一模一樣，只是多了海量 corner case、cache、CSR。這是本課「做完能讀工業 core」的兌現——讀得懂它，代表你真的懂 pipeline 了。

Part 2 的 pipeline 主線到這裡走完：五級切分、三種 hazard、forwarding/stall/flush、hazard unit、完整整合驗收。接下來是**練習 B**——不看參考，自己把 forwarding unit 和 hazard detection unit 從頭刻一遍，通過一組刻意設計的 RAW / load-use / branch hazard 測試。把這一章的知識從「看得懂」變成「寫得出」。

→ [練習 B：自刻 forwarding + hazard detection](./practice-b-forwarding-hazard.md)
