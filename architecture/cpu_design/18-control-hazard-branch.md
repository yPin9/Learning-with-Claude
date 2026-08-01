# Ch 18 — Control hazard：branch 代價、flush、提前 resolve

> **目標**：搞懂 pipeline 的第三類 hazard——control hazard。branch 要跳到哪，得等它走到後面的級才知道；在那之前，pipeline 已經照「不跳」把後面的指令抓進來了。你會看到這些「錯抓」的指令若不處理會真的執行（污染暫存器），學會用 flush 把它們清成 NOP，並把 branch 判斷從 EX 提前到 ID 級把 penalty 從 2 拍砍到 1 拍。全程真跑對照 flush 前後。
> **環境**：WSL + verilator 4.038。輸出皆真跑，沿用 Part 2 的 pipelined `core`。

## 為什麼需要處理 control hazard？

pipeline 每拍都在 IF 級抓一條新指令。抓哪一條？看 PC。平常 PC = 上一條 + 4，一條接一條循序抓，沒問題。

但遇到 branch（`beq`/`bne`）就麻煩了：`beq x1, x2, target` 要不要跳，得先**比較 x1 和 x2**才知道。比較在哪做？在 ALU（EX 級）。也就是說，branch 要走到 EX 級、ALU 算完，我們才知道下一條該抓 target 還是 PC+4。

可是 pipeline 不會等。branch 還在 IF/ID 往前爬的時候，後面幾拍 IF 級**已經照「不跳、PC+4」把接下來的指令抓進來了**：

```
   cyc:      1    2    3    4    5
   beq       IF   ID   EX  ...            <- EX 級才知道跳不跳
   inst+1         IF   ID  ...            <- 已經抓進來了(假設不跳)
   inst+2              IF   ID ...        <- 也抓進來了
                            ▲
                    branch 到這裡才 resolve
                    但 inst+1、inst+2 已經在 pipeline 裡了!
```

如果 branch 最後判定要跳（taken），那 `inst+1`、`inst+2` 根本不該執行——它們是「假設不跳」錯抓進來的。這就是 **control hazard**：**下一條該抓誰，要等 branch resolve 才知道，但 pipeline 等不及，先抓了可能錯的指令。**

錯抓進來的指令若放著不管，它們會一路走完五級、寫暫存器、寫記憶體——**污染狀態**。我們得在發現 branch taken 的那一刻，把這些錯抓的指令從 pipeline 裡清掉。

## 先建立直覺：叉路口的搶跑

把 pipeline 想成一條輸送帶，IF 級不斷往帶上放指令。branch 是個叉路口：帶子走到某處才知道要左轉還右轉。

但 IF 級這個放料的手不會停——在叉路口還沒判定方向時，它已經照「直走」的假設，往帶上多放了一兩個「直走路線」的指令。等叉路口判定「要右轉」，那些「直走」的指令已經在帶上跑了。

處理方法：判定右轉的瞬間，**把帶上那些直走的錯料抓下來丟掉**（flush），同時叫放料的手改從右轉路線放。丟掉的位子變成空檔（bubble），這就是 branch 的代價——每次跳，浪費幾個空拍。

```
   丟掉錯料 = flush：
   beq(taken)  IF ID EX
   inst+1(錯)     IF ×          <- 清成 NOP(flush)
   target            IF ID ...  <- 從正確路線重抓
                        ▲
                 浪費了 1 個空拍(penalty)
```

**penalty 幾拍，取決於 branch 多晚 resolve**。越晚知道方向，錯抓越多，要丟越多，浪費越大。所以我們有兩個努力方向：**把錯抓的清乾淨（flush）**、**讓 branch 早點 resolve（減少錯抓數）**。

## 核心概念：penalty = branch 何時 resolve

branch 在第幾級 resolve，直接決定 penalty：

```
   若 branch 在 EX 級 resolve：
   beq       IF ID EX ...
   inst+1       IF ID ×        <- 錯抓 1
   inst+2          IF ×        <- 錯抓 2
                      ▲ 這裡才知道 taken → 前面 2 條全錯 → penalty 2 拍

   若 branch 提前到 ID 級 resolve：
   beq       IF ID ...
   inst+1       IF ×           <- 錯抓 1
                   ▲ ID 就知道 taken → 只錯抓 1 條 → penalty 1 拍
```

- **在 EX resolve**：branch 走到第 3 級才知道方向，前面 IF/ID 兩級各有一條錯抓的 → **penalty 2 拍**。
- **在 ID resolve**：branch 走到第 2 級就知道方向，只有 IF 級那一條錯抓 → **penalty 1 拍**。

我們的 `core` 選擇**把 branch 判斷提前到 ID 級**：在 ID 級就比較 rs1、rs2、算出 target，當拍就知道跳不跳。代價是要在 ID 級多放一個比較器（不能重用 EX 的 ALU），換來 penalty 從 2 拍砍到 1 拍。對 branch 密集的程式，這個省下的差距很可觀。

> 還有更激進的做法：**branch prediction**（分支預測，Part 3）。與其 resolve 後才修正，不如一開始就「猜」方向，猜對就 0 penalty，猜錯才 flush。本章先做「不預測、resolve 後 flush」的基礎版，把機制打穩，Part 3 再上預測器。

## 底層機制：ID 級提前 resolve + flush 接線

### 在 ID 級判斷跳不跳

ID 級解碼出這是 branch 後，直接比較兩個來源暫存器：

```systemverilog
// ID 級：branch 提前 resolve
always_comb begin
    id_branch_taken = 1'b0;
    if (id_branch) begin
        unique case (id_funct3)
            3'b000: id_branch_taken = (id_cmp_a == id_cmp_b);  // BEQ：相等就跳
            3'b001: id_branch_taken = (id_cmp_a != id_cmp_b);  // BNE：不等就跳
            default: id_branch_taken = 1'b0;
        endcase
    end
end
assign id_branch_target = if_id_pc + id_imm;   // PC-relative 目標位址
```

`id_cmp_a`、`id_cmp_b` 是 branch 的兩個來源（可能經 forwarding，見下一段陷阱）。`id_funct3` 分 BEQ（000）和 BNE（001）。target 是 `branch 的 PC + 立即數`（B-type 的 PC-relative offset）。

### taken 時：重導 PC + flush IF/ID

```systemverilog
// 下一個 PC：taken 就跳 target，否則 PC+4
assign if_pc_next = id_branch_taken ? id_branch_target : (if_pc + 32'd4);

// hazard 控制：taken 時 flush IF/ID（把錯抓的下一條變 NOP）
always_comb begin
    if_id_flush = 1'b0;
    if (id_branch_taken) if_id_flush = 1'b1;
end

// IF/ID register：flush=1 就把內容清成 NOP
always_ff @(posedge clk) begin
    if (rst || if_id_flush) begin
        if_id_pc   <= 0;
        if_id_inst <= 32'h0000_0013;   // NOP = addi x0,x0,0
    end else if (if_id_write) begin
        if_id_pc   <= if_pc;
        if_id_inst <= if_inst;
    end
end
```

當 branch 在 ID 判定 taken：
1. `if_pc_next` 立刻指向 target，下一拍 IF 從正確路線重抓。
2. `if_id_flush=1`，把**這一拍抓進 IF/ID 的那條錯指令**清成 NOP。它下一拍進 ID 時是個 NOP，不做任何事。

一拍 flush 掉一條錯指令，penalty 剛好 1 拍。**flush 不是「刪除」指令，是把它變成無害的 NOP**——pipeline 結構不變（它還是佔著級數往下流），只是控制訊號全 0，不寫任何狀態。

## 範例一：flush 前後——錯抓的指令會不會執行

程式：branch 一定 taken，跳過兩條「毒指令」。若 flush 有效，毒指令不該執行；若沒 flush，會執行污染 x7：

```asm
_start:
    addi x1, x0, 5
    addi x2, x0, 5
    beq  x1, x2, taken     # 5==5 → taken，跳過下面兩條毒指令
    addi x7, x0, 999       # POISON：不該執行
    addi x7, x0, 888       # POISON2：不該執行
taken:
    addi x3, x0, 42        # x3 = 42（正確路徑）
done:
    beq  x0, x0, done      # halt
```

**先看沒 flush 的版本**（強制 `if_id_flush=0`）：

```
cyc | PC        | stall flush | WB
----+-----------+-------------+------------------
  3 | 0x8000000c |   1     0   | -                        <- stall(見下方 branch-use)
  4 | 0x8000000c |   0     0   | x1  <= 5 (0x00000005)
  5 | 0x80000014 |   0     0   | x2  <= 5 (0x00000005)    PC 已跳到 target 0x80000014
  6 | 0x80000018 |   0     0   | -
  7 | 0x8000001c |   0     0   | -
  8 | 0x80000018 |   0     0   | x7  <= 999 (0x000003e7)  <- 毒指令執行了!
  9 | 0x8000001c |   0     0   | x3  <= 42 (0x0000002a)
```

PC 有跳到 target（0x80000014，跳過毒指令的抓取沒問題），但**在 branch resolve 之前那一拍已經抓進 IF/ID 的第一條毒指令沒被清掉**，一路走到 WB，`x7 <= 999`。狀態被污染。

**再看有 flush 的正確版本**：

```
cyc | PC        | stall flush | WB
----+-----------+-------------+------------------
  3 | 0x8000000c |   1     0   | -
  4 | 0x8000000c |   0     1   | x1  <= 5 (0x00000005)    <- flush=1!
  5 | 0x80000014 |   0     0   | x2  <= 5 (0x00000005)
  6 | 0x80000018 |   0     0   | -
  7 | 0x8000001c |   0     1   | -
  8 | 0x80000018 |   0     0   | -                        毒指令 x7 從未寫回!
  9 | 0x8000001c |   0     1   | x3  <= 42 (0x0000002a)
```

- **cyc 4：`flush=1`** — branch 在 ID 判定 taken，把錯抓進 IF/ID 的毒指令清成 NOP。
- **後面沒有任何 `x7 <= ...`** — 毒指令被清掉了，從未執行。
- **x3 <= 42** — 正確路徑正常執行。

跑到最後 dump 暫存器，對照最清楚：

```
=== WITH flush,    final regs === x1=5 x2=5 x3=42 x7=0     <- x7 乾淨
=== WITHOUT flush, final regs === x1=5 x2=5 x3=42 x7=999   <- x7 被污染
```

同一支程式，差一根 flush 線，狀態從乾淨變污染。**flush 是 branch 正確性的必要條件，不是效能最佳化。**

## 範例二：branch-use hazard——branch 提前到 ID 帶來的新問題

把 branch 提前到 ID resolve 省了 penalty，但引入一個新麻煩：branch 在 ID 級就要 rs1、rs2 的值，如果這兩個值是**緊貼在前的指令**剛算出來的，那條指令此刻還在 EX 級（結果還沒進 EX/MEM），ID 級的 forwarding 拉不到。看範例一的 cyc 3：

```
  3 | 0x8000000c |   1     0   | -     <- stall=1
```

`beq x1, x2` 在 ID 要 x1、x2，而 `addi x2` 剛好是它前一條、此刻在 EX 級——**branch-use hazard**。這和上一章的 load-use 是同一類問題（要用的值還沒到得了的地方），解法也一樣：**stall 一拍**，等那條指令走到 EX/MEM，ID forwarding 就接得到。所以 cyc 3 先 stall（`stall=1`），cyc 4 才 resolve + flush。

偵測邏輯（Ch 19 會和其他 hazard 綜合，這裡先看 branch 這一路）：

```systemverilog
// branch 的來源還在 EX 級（結果沒進 EX/MEM）→ stall 一拍
logic branch_use_hazard;
always_comb begin
    branch_use_hazard = id_branch && id_ex_reg_write && id_ex_rd != 0 &&
                        ((id_ex_rd == id_rs1) || (id_ex_rd == id_rs2));
end
```

這是「branch 提前到 ID」的取捨代價：省了 1 拍 branch penalty，但當 branch 緊貼相依指令時多付 1 拍 stall。多數情況 branch 的來源不是緊鄰算出的（隔了幾條），branch-use 不觸發，提前 resolve 淨賺。**若你把 branch resolve 留在 EX，就沒有 branch-use（EX 的 forwarding 都接得到），但每次 taken 固定 penalty 2 拍**——這是設計權衡，沒有免費的午餐。

## 對比取捨

| 設計選擇 | penalty（taken） | 代價 | 本課選擇 |
|---|---|---|---|
| branch 在 EX resolve | 2 拍 | 無 branch-use，但每次 taken 貴 | 否 |
| branch 在 ID resolve | 1 拍 | 多一個 ID 比較器 + branch-use stall | **是** |
| 不 flush | — | 錯，狀態被污染 | 絕不 |
| predict-not-taken + flush | taken 時 penalty，not-taken 0 | 本章基礎版就是這個 | 是（Part 3 再加預測器） |
| branch prediction（Part 3） | 猜對 0、猜錯才 penalty | BTB/BHT 硬體 | Part 3 |

要點：flush 是**正確性**（一定要有），提前 resolve 和 prediction 是**效能**（減少 penalty）。我們的基礎版 = 「假設 not-taken 繼續抓 + ID 提前 resolve + taken 時 flush 一拍」。

## 踩雷區

**雷 1：以為「PC 跳對了就沒事」，忘了 flush 錯抓的指令。**
- 錯誤直覺：「branch taken 我把 PC 改成 target 就好了，pipeline 自然走對」。
- 正確認識：改 PC 只保證**之後**抓對，但在 resolve 之前**已經抓進 pipeline 的錯指令還在裡面**，會照樣執行寫狀態（範例一 x7=999）。必須額外 flush 掉那些錯抓的。改 PC 管未來，flush 管已經進來的錯——兩件事都要做。

**雷 2：以為 flush 是「把指令刪掉」。**
- 錯誤直覺：「flush 就是從 pipeline 移除那條指令，後面往前遞補」。
- 正確認識：pipeline 是固定級數的硬體，沒有「移除 + 遞補」這種操作。flush 是把那條指令的 pipeline register **內容清成 NOP**（控制訊號全 0）——它還是佔著級數一級級往下流，只是不做任何事（不寫暫存器、不寫記憶體）。結果上等於它不存在，但結構上它是一個往下流的 bubble。

**雷 3：把 branch 提前到 ID，卻忘了 branch-use hazard。**
- 錯誤直覺：「branch 在 ID 比較，forwarding 會把值送過來」。
- 正確認識：ID 級的 forwarding 只接得到 EX/MEM 和 MEM/WB（已經過 EX 的結果）。如果 branch 的來源是**緊貼在前、此刻還在 EX 級**的指令算的，那值還沒進 EX/MEM，ID 拉不到——必須 stall 一拍（範例二 cyc 3）。忘了這個，branch 會拿舊值比較，跳錯方向。提前 resolve 省 penalty 的代價就是要處理 branch-use。

**雷 4：branch target 算錯——用錯 PC 或忘了 B-type 立即數格式。**
- 錯誤直覺：「target = 當前 PC + imm」。
- 正確認識：target = **branch 那條指令自己的 PC** + imm（PC-relative），不是 IF 級當前 PC（那是後面幾條的 PC 了）。在我們的 core 裡是 `if_id_pc + id_imm`（ID 級帶著 branch 的 PC）。而 B-type 立即數的 bit 拼法很怪（imm[12|10:5|4:1|11]，最低位恆 0），解碼時要照 B-type 格式重組，拼錯 target 就跳到錯地方。這在 Ch 10/11 immediate generator 已處理，但接 branch 時務必確認用的是 branch 的 PC。

## 進階延伸

- **branch penalty 的 CPI 代價**：每個 taken branch = penalty 拍數的浪費（我們是 1 拍）。程式裡 branch 佔比可觀（迴圈、if），假設 20% 指令是 branch、其中 60% taken、每次 penalty 1，光 branch 就讓 CPI 增加 0.2 × 0.6 × 1 = 0.12。這是 Part 3 branch prediction 的動機——把「每次 taken 都 penalty」變成「只有猜錯才 penalty」。
- **為什麼不 stall 到 branch resolve 就好**：最笨的解法是遇到 branch 就 stall pipeline 直到 resolve（predict nothing）。但那樣每個 branch 都固定付 penalty，不管 taken 與否。我們的「predict not-taken」聰明一點：not-taken 時繼續抓的指令剛好是對的，0 penalty；只有 taken 才 flush。對「多數 not-taken」的 branch（例如錯誤處理分支）這是淨賺。
- **flush 的範圍隨 resolve 位置變**：branch 在 ID resolve → flush 1 級（IF/ID）。若在 EX resolve → 要 flush 2 級（IF/ID 和 ID/EX）。resolve 越晚，flush 越多級，硬體越複雜。這也是提前 resolve 的另一個好處——flush 邏輯更精省。Ch 32 trap 機制要一次 flush 整條 pipeline（例外發生時後面全錯），是 flush 的極端版。
- **jump（JAL/JALR）的 control hazard**：`jal`/`jalr` 是無條件跳，方向確定（一定跳），但**目標**要算（JALR 還要讀暫存器）。它們也有 control hazard——目標算出來前抓的下一條要 flush。JAL 目標在 ID 就能算（PC+imm），JALR 要 rs1（可能 branch-use stall）。本課主線 branch，jump 的處理同理，Ch 20 整合時會一起接。

## 本章重點整理

- **control hazard**：下一條該抓誰要等 branch resolve 才知道，pipeline 等不及先抓了可能錯的指令。
- **penalty = branch 何時 resolve**：EX resolve → 2 拍；ID resolve → 1 拍。我們把 branch 提前到 **ID** 級，penalty = 1。
- **flush 是正確性必需**：taken 時把錯抓進 IF/ID 的指令清成 NOP（控制訊號全 0），否則它會執行污染狀態（範例一 x7=999 vs 0）。
- flush 不是刪除指令，是把 pipeline register 內容變 NOP，它仍佔級數往下流（bubble）。
- **提前 resolve 的代價是 branch-use hazard**：branch 來源還在 EX 時要 stall 一拍。省 penalty 不是免費的。
- 我們的基礎版 = predict-not-taken（not-taken 繼續抓、0 penalty）+ ID 提前 resolve + taken flush 一拍。Part 3 再上 branch prediction。

## 自我檢核

- [ ] 我能解釋為什麼 branch 會造成 control hazard，畫出「錯抓的指令」在 pipeline 裡的位置。
- [ ] 我能說出 branch 在 EX vs ID resolve 的 penalty 各是幾拍、為什麼，以及我們選哪個。
- [ ] 我能說清楚 flush 做的事（清 IF/ID 成 NOP），以及它和「改 PC」是兩件不同但都要做的事。
- [ ] 我能解釋「flush 不是刪除指令」——它變成什麼、還在不在 pipeline 裡。
- [ ] 我能說出把 branch 提前到 ID 引入的新 hazard（branch-use）是什麼、怎麼 stall 解決。
- [ ] 我能預測範例一沒 flush 時 x7 為什麼是 999、有 flush 時為什麼是 0。

## 延伸閱讀

- **《Computer Organization and Design, RISC-V Edition》(Patterson & Hennessy) 第 4.8 節「Control Hazards」**：本章的教科書版本。它從「stall 到 resolve」講到「predict not-taken」再到「把 branch 提前到 ID（reducing the delay of branches）」，順序和本章完全一致，圖 4.62–4.65 把提前 resolve 的 datapath 改動畫得很清楚，讀它補全硬體圖。
- **《Digital Design and Computer Architecture, RISC-V Edition》(Harris & Harris) 第 7.5.3 節「Control Hazards」**：從 HDL/訊號角度講 flush 怎麼接線（Flush 訊號怎麼清 pipeline register），和我們的 `if_id_flush` 一一對應。它也討論 branch misprediction penalty 的量化，接得上 Part 3。
- **[riscv-boom 文件](https://docs.boom-core.org/) 的 front-end / branch prediction 章節**：看工業級 core 怎麼把 control hazard 從「resolve 後 flush」進化成「預測 + 猜錯才 flush」，以及 BTB、RAS、gshare 這些預測器（Part 3 要學的）在真 core 裡怎麼組。讀它你會懂本章的基礎版離工業級還有多遠。
- **[RISC-V Unprivileged ISA Spec](https://riscv.org/technical/specifications/) 第 2.5「Control Transfer Instructions」**：權威定義 B-type/J-type 的立即數格式和 branch 語意。算 branch target 有疑義（那個怪異的 imm bit 拼法）時的最終仲裁。搭配 `architecture/riscv` 服用。

data hazard（forwarding + load-use stall）和 control hazard（flush）我們都做過了，但它們現在散在各處、還會互相打架（同一拍既要 stall 又要 flush 怎麼辦？）。下一章我們把 forwarding、load-use stall、branch flush、structural hazard 全部收進一個 hazard detection unit，講清楚訊號互動和優先序。

→ [Ch 19 Hazard detection unit + structural hazard 綜合](./19-hazard-detection-unit.md)
