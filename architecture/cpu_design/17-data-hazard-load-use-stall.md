# Ch 17 — Data hazard（二）：load-use hazard、stall / bubble

> **目標**：搞懂為什麼 Ch 16 的 forwarding 救不了「load 完馬上用」這一種 data hazard，親手做出偵測它的邏輯，並用 stall（凍結 PC 與 IF/ID）+ 插 bubble（清空 ID/EX 控制訊號）補上這一拍。你會看到同一支程式，沒 stall 時結果算錯、加了 stall 後算對，全部真跑對照。
> **環境**：WSL + verilator 4.038。輸出皆真跑。本章沿用 Part 2 的 pipelined `core`（含 Ch 16 forwarding unit）。

## 為什麼 forwarding 救不了 load-use？

Ch 16 我們用 forwarding（bypassing）解決了 data hazard：`add x2, x1, ...` 緊接 `sub x3, x2, ...`，x2 還沒寫回 regfile，但它的值在 EX 級末端就算好了，我們把它從 EX/MEM pipeline register 直接拉回 EX 級的 ALU 輸入。前一條指令算完的那一刻，下一條就拿得到。

forwarding 的前提是：**要 forward 的值，在你需要它的那個 cycle 之前已經產生了**。R-type 指令的結果在 EX 級末端（ALU 一算完）就有了，而下一條指令下一個 cycle 才進 EX——所以來得及。

但 load 不一樣。`lw x3, 0(x6)` 的資料不是 ALU 算出來的，是**記憶體讀出來的**。看它走過五級：

```
   lw x3, 0(x6)   IF   ID   EX   MEM   WB
                             │    │
                     ALU 只算位址   資料這裡才讀出來!
                        (x6+0)      x3 的真正值在 MEM 級末端才有
```

ALU 在 EX 級算的是**位址**（x6+0），不是 x3 的值。x3 的值要到 **MEM 級末端**（記憶體吐出資料）才存在。現在看緊接在後的指令：

```
   lw  x3, 0(x6)   IF   ID   EX   MEM   WB
   add x4, x3, x3       IF   ID   EX    MEM   WB
                                  ▲
                            add 在這個 cycle 進 EX，要 x3
                            但 lw 這個 cycle才剛進 MEM，資料還沒讀出來!
```

`add x4, x3, x3` 進 EX 級要用 x3 的時候，`lw` 才剛進 MEM 級——資料**這一拍結束才會出現**。forwarding 想拉也沒東西可拉：那條線上此刻是舊值或未定值。**這就是 load-use hazard：load 的結果被緊接的下一條指令使用，forwarding 差一拍趕不上。**

這不是把 forwarding 寫壞了，是物理上的時間差。load 的資料比 R-type 的結果晚一級才生出來，而 pipeline 的相鄰兩條指令剛好差一級——正好差這一拍。

## 先建立直覺：晚一班的快遞

把 forwarding 想成「同事算完立刻把答案喊給你」。R-type 的同事在 EX 工位算完就能喊，你在下一個工位剛好聽得到。

load 這位同事比較特別：他在 EX 工位只寫了「去幾號倉庫拿貨」的地址，真正的貨要到 **MEM 工位（下一站）** 才領到。等他領到貨能喊給你時，你已經走過你的 EX 工位了——**你需要貨的那一刻，貨還在路上**。

唯一的辦法：**你原地等一拍**。讓 load 先走到 MEM 把貨領出來，你再進 EX。這「等一拍」就是 stall。

```
   沒 stall（錯）：              加 stall（對）：
   lw  x3   EX MEM WB           lw  x3   EX MEM WB
   add x4      EX ...           add x4      ×  EX MEM WB
              ▲                          ▲   ▲
        add 進 EX 時 x3            add 卡住一拍(bubble)
        還沒讀出來 → 拿到舊值      等 lw 到 MEM 讀出 x3
                                  再 forward → 正確
```

等的這一拍，pipeline 裡插進一個「什麼都不做」的空指令，我們叫它 **bubble（氣泡）**。bubble 佔著位子往下流，但它不寫暫存器、不寫記憶體、不改任何狀態——像一個 NOP。

## 核心概念：偵測 + stall + bubble 三件事

要正確處理 load-use，硬體要同時做三件事：

1. **偵測（detect）**：發現「EX 級是 load，且它的 rd 正是 ID 級某條指令要讀的 rs1/rs2」。
2. **stall（凍結）**：讓 ID 級這條指令和它後面的（IF 級）原地不動——凍結 PC、凍結 IF/ID pipeline register。這兩級的內容下一拍保持不變，等於重跑一次。
3. **bubble（插氣泡）**：把 ID/EX pipeline register 的**控制訊號清成 0**（reg_write=0、mem_write=0…）。這樣被卡住的那條指令這一拍不會真的往下做事，等於往 EX 灌了一個 NOP。

三件事缺一不可：只偵測不 stall，等於白偵測；stall 了不插 bubble，被卡的那條指令的舊控制訊號會往下流，重複執行一次副作用（例如寫兩次記憶體）。

### 偵測邏輯：比對 EX 級 load 的 rd 與 ID 級的 rs

偵測條件寫成一行布林式：

```
load_use_hazard =  (EX 級指令是 load)          // id_ex_mem_read == 1
                && (它的 rd != x0)              // load 到 x0 沒意義，不算
                && ( EX 級的 rd == ID 級的 rs1
                  || EX 級的 rd == ID 級的 rs2 ) // 命中任一來源
```

- `id_ex_mem_read`：ID/EX register 裡帶著的「這是 load」旗標。EX 級此刻在跑的指令是不是 load。
- `id_ex_rd`：EX 級指令要寫的目標暫存器。
- `id_rs1` / `id_rs2`：ID 級此刻在解碼的指令要讀的兩個來源。

只要 EX 是 load、目標非 x0、而且 ID 級有人要讀這個目標，就是 load-use hazard。注意**只要偵測 EX 級的 load 就夠**——如果 load 已經走到 MEM 級（再下一條要用），那時 forwarding（MEM/WB → EX）就接得到了，不用 stall。真正趕不上的只有「緊貼在 load 後面」這一條。

## 底層機制：凍結 PC/IF-ID 與插 bubble 怎麼接線

我們的 `core` 有三根控制線負責這件事：

- `pc_write`：PC 的寫致能。=0 時 PC 這一拍不更新（凍結）。
- `if_id_write`：IF/ID pipeline register 的寫致能。=0 時 IF/ID 這一拍不更新（凍結，維持同一條指令在 ID）。
- `id_ex_bubble`：ID/EX register 的清零致能。=1 時把 ID/EX 的控制訊號全清 0（插 bubble）。

接線邏輯：

```systemverilog
// hazard detection：EX 級 load 的 rd 命中 ID 級 rs → stall 一拍
logic load_use_hazard;
always_comb begin
    load_use_hazard = id_ex_mem_read && id_ex_rd != 0 &&
                      ((id_ex_rd == id_rs1) || (id_ex_rd == id_rs2));
end

always_comb begin
    pc_write     = 1'b1;   // 預設：正常往前
    if_id_write  = 1'b1;
    id_ex_bubble = 1'b0;

    if (load_use_hazard) begin
        pc_write     = 1'b0;   // 凍結 PC（同一 PC 再抓一次）
        if_id_write  = 1'b0;   // 凍結 IF/ID（同一條指令留在 ID）
        id_ex_bubble = 1'b1;   // ID/EX 插 bubble（這拍往 EX 灌 NOP）
    end
end
```

而 PC 與 pipeline register 要聽這些致能：

```systemverilog
// PC：pc_write=0 就不更新
always_ff @(posedge clk) begin
    if (rst)            if_pc <= RESET_PC;
    else if (pc_write)  if_pc <= if_pc_next;
end

// IF/ID：if_id_write=0 就凍結（維持原內容）
always_ff @(posedge clk) begin
    if (rst || if_id_flush) begin
        if_id_pc <= 0; if_id_inst <= 32'h0000_0013;   // NOP
    end else if (if_id_write) begin
        if_id_pc <= if_pc; if_id_inst <= if_inst;
    end
end

// ID/EX：id_ex_bubble=1 就把控制訊號清 0（插 bubble）
always_ff @(posedge clk) begin
    if (rst || id_ex_bubble) begin
        id_ex_reg_write <= 0; id_ex_mem_read <= 0; id_ex_mem_write <= 0;
        // ...其餘控制訊號一律清 0
    end else begin
        id_ex_reg_write <= id_reg_write; /* ...正常搬 */
    end
end
```

一拍之內同時發生：PC 不動（下一拍重抓同一條）、IF/ID 不動（被卡的指令留在 ID 再解碼一次）、ID/EX 灌 NOP（被卡的指令這拍不往下做事）。下一拍 load 走到 MEM 讀出資料，被卡的指令終於能進 EX，靠 MEM/WB forwarding 拿到正確的值。**stall 一拍，換來正確結果。**

> 為什麼是「凍結 PC 和 IF/ID」而不是凍結全部？因為只有 ID 級這條指令（和它後面還沒進來的）需要等。EX/MEM/WB 三級的指令跟這個 hazard 無關，它們要照常往前走完，不能一起卡住——否則會製造新的錯誤。stall 只凍結「hazard 點以前」的兩級。

## 範例一：load-use 場景，沒 stall vs 有 stall

我們用這支程式，`lw` 之後緊接著用它的結果：

```asm
_start:
    lui  x6, 0x80000       # x6 = 0x80000000（資料區基底）
    addi x1, x0, 100       # x1 = 100
    sw   x1, 0(x6)         # mem[base] = 100
    lw   x3, 0(x6)         # x3 = mem[base] = 100      <-- load
    add  x4, x3, x3        # x4 = x3 + x3 = 200        <-- 緊接著用 x3！
    addi x5, x4, 1         # x5 = 201
loop:
    beq  x0, x0, loop      # halt
```

手算預期：x3=100，x4=200，x5=201。

**先看故意拔掉 stall 的版本**（把 `load_use_hazard` 強制成 0）。逐 cycle dump（`stall`/`flush` 是當拍 hazard 訊號，`WB` 是這拍寫回的暫存器）：

```
cyc | PC        | stall flush | WB
----+-----------+-------------+------------------
  4 | 0x80000010 |   0     0   | x6  <= -2147483648 (0x80000000)
  5 | 0x80000014 |   0     0   | x1  <= 100 (0x00000064)
  6 | 0x80000018 |   0     0   | -
  7 | 0x8000001c |   0     1   | x3  <= 100 (0x00000064)
  8 | 0x80000018 |   0     0   | x4  <= 0 (0x00000000)     <-- 錯!
  9 | 0x8000001c |   0     1   | x5  <= 1 (0x00000001)     <-- 錯!
```

x3 讀出 100 沒錯，但 **x4 = 0**（應該 200）、**x5 = 1**（應該 201）。因為 `add x4, x3, x3` 進 EX 時 x3 還沒讀出來，ALU 拿到未定/舊值（這裡是 0），算出 0；x5 = x4+1 = 1 跟著錯。**forwarding 對 load 這一拍無能為力。**

**再看加上 stall 的正確版本**：

```
cyc | PC        | stall flush | WB
----+-----------+-------------+------------------
  4 | 0x80000010 |   0     0   | x6  <= -2147483648 (0x80000000)
  5 | 0x80000014 |   1     0   | x1  <= 100 (0x00000064)   <-- stall=1!
  6 | 0x80000014 |   0     0   | -                         PC 凍結:同一個 0x80000014
  7 | 0x80000018 |   0     0   | x3  <= 100 (0x00000064)
  8 | 0x8000001c |   0     1   | -
  9 | 0x80000018 |   0     0   | x4  <= 200 (0x000000c8)   <-- 對!
 10 | 0x8000001c |   0     1   | x5  <= 201 (0x000000c9)   <-- 對!
```

看關鍵三處：

- **cyc 5：`stall=1`** — hazard detection 抓到了（`lw x3` 在 EX，`add x4` 在 ID 要讀 x3）。
- **cyc 5→6：PC 凍結** — 兩拍的 PC 都是 `0x80000014`。`add x4` 被留在原地，`lw` 趁這拍走到 MEM 讀出 x3。
- **cyc 9：x4 = 200，cyc 10：x5 = 201** — 全對。stall 換來的那一拍讓 `add` 之後能用 MEM/WB forwarding 拿到 x3=100。

同一支程式，差一段 stall 邏輯，結果從全錯變全對。這就是 load-use stall 的價值。

## 範例二：邊界——load 後隔一條才用，不需要 stall

load-use 只在「緊貼」時發生。如果 load 和使用者之間隔了一條無關指令，forwarding（MEM/WB → EX）就接得到，不用 stall：

```asm
    lw   x3, 0(x6)     # x3 = mem[base]         load
    addi x8, x0, 7     # 無關指令（把 load 和使用者隔開）
    add  x4, x3, x3    # 用 x3——但此時 lw 已在 WB，forwarding 接得到
```

當 `add x4` 進 EX 時，`lw` 已經走到 **WB 級**（中間隔了 `addi`），x3 的值在 MEM/WB register 裡，MEM/WB→EX 這條 forwarding 路徑直接拉得到。偵測邏輯此刻看 EX 級是 `addi` 不是 load（`id_ex_mem_read=0`），`load_use_hazard=0`，不 stall。

**編譯器最佳化就靠這個**：把一條無關指令排到 load 和 use 之間（叫 load delay slot filling），就能把 stall 消掉。這是 pipeline 硬體和 compiler scheduler 的分工——硬體保證正確（該 stall 就 stall），compiler 負責減少 stall（把指令重排到不用 stall）。你在 `architecture/riscv` 或 LLVM backend 學到的 instruction scheduling，最終目的之一就是餵給這個 pipeline 一串不會 hazard 的指令。

## 對比取捨

| 手法 | 解決什麼 | 代價 | 適用 |
|---|---|---|---|
| forwarding（Ch 16） | ALU 結果的 RAW hazard | 0 cycle（純繞線） | 結果在 EX 末端已產生（R-type/ALU 類） |
| load-use stall（本章） | load 資料 forwarding 趕不上 | 1 cycle bubble | load 緊接被使用 |
| compiler 重排 | 消掉 load-use stall | 0 硬體代價，靠軟體 | load 和 use 之間排得進無關指令時 |
| 完全不 forward、全 stall | 所有 RAW | 每次 2~3 cycle | 教學對照組，真設計不用 |

要點：**能 forward 就別 stall**（0 代價），forward 趕不上的（load-use）才 stall（1 拍代價），能靠 compiler 消掉 stall 更好。這是效能優先序。

## 踩雷區

**雷 1：以為「有了 forwarding 就不用 stall」。**
- 錯誤直覺：「Ch 16 forwarding 已經把 data hazard 解決了，pipeline 不會再算錯」。
- 正確認識：forwarding 只解決「結果已產生、只是還沒寫回 regfile」的 hazard。load 的資料到 MEM 級末端才產生，比相鄰指令進 EX 的時間**晚一拍**，forwarding 拉不到。load-use 是唯一 forwarding 救不了、非 stall 不可的 data hazard。把它跟一般 RAW 混為一談，你的 pipeline 遇到 `lw` 緊接 `add` 就會靜靜算錯（如範例一 x4=0）。

**雷 2：stall 了卻忘了插 bubble，副作用執行兩次。**
- 錯誤直覺：「凍結 PC 和 IF/ID 就夠了，被卡的指令下一拍再跑就好」。
- 正確認識：只凍結不插 bubble，被卡指令的控制訊號這一拍會照樣送進 ID/EX 往下流，等於它先執行了一半（例如 `sw` 會寫一次記憶體），下一拍解凍再完整執行一次——副作用做了兩遍。必須同時把 ID/EX 的控制訊號清 0（bubble），讓被卡的這拍是個徹底不做事的 NOP。stall 三件事（凍 PC、凍 IF/ID、插 bubble）是一組，不能只做兩件。

**雷 3：偵測條件漏了 `rd != x0`。**
- 錯誤直覺：「只要 EX 是 load 且 rd 命中 ID 的 rs 就 stall」。
- 正確認識：`lw x0, 0(x6)` 這種 load 到 x0（結果被丟棄）的指令，rd=x0。如果沒排除 x0，而 ID 級剛好也在讀 x0（例如任何用到 x0 的指令），會誤判 hazard、無謂 stall 一拍。x0 恆 0，沒有真正的資料相依。偵測條件一定要帶 `id_ex_rd != 0`。這跟 forwarding 排除 x0 是同一個道理。

**雷 4：以為 stall 一拍後還要繼續 stall。**
- 錯誤直覺：「load 的資料很晚才好，可能要 stall 好幾拍」。
- 正確認識：load-use 只需 stall **一拍**。stall 這一拍讓 load 從 EX 走到 MEM（資料讀出），下一拍被卡的指令進 EX 時，load 已在 MEM/WB 邊界，MEM/WB→EX forwarding 正好接得到，不用再等。偵測邏輯下一拍看 EX 級已不是 load（是 bubble），`load_use_hazard` 自動變 0，stall 自然解除。設計成「stall 直到 load 寫回 regfile」是多等了兩拍，白白浪費效能。

## 進階延伸

- **load-use 的 CPI 代價**：每次 load-use hazard = 1 cycle penalty。真實程式裡 load 很常見，緊接使用也常見，所以這個 penalty 累積起來不小。這是 Part 3（Ch 23 CPI 分析）要量化的：CPI = 1（理想）+ load-use stall 貢獻 + branch penalty 貢獻 + ...。減少 load-use stall 是 compiler scheduling 和 out-of-order 執行（Ch 36）的重要動機——OoO core 遇到 load-use 不 stall 整條 pipeline，而是讓後面無關的指令先跑。
- **為什麼 store-load 不需要這種 stall**：`sw` 之後緊接 `lw` 同一位址，是記憶體層面的相依（store-to-load forwarding），不是暫存器 hazard，我們這裡不處理（單一週期記憶體、循序執行，`sw` 先寫進去 `lw` 才讀，自然正確）。真 OoO core 的 load/store queue 才要做 memory disambiguation，那是另一個層次的問題。
- **雙 load-use 連環**：`lw x3` → `add x4, x3` → `lw x5, 0(x4)` → `add x6, x5`。兩組 load-use 各 stall 一拍，互不干擾——偵測邏輯每拍獨立判斷 EX 級是不是 load、有沒有命中。你可以自己組一支這樣的程式驗證 stall 出現兩次。
- **和 branch 的優先序**：本章只有 load-use 一種 hazard。下一章加入 branch flush 後，若同一拍既有 load-use stall 又有 branch，要決定誰優先。答案是 **stall 優先**（先把資料 hazard 等出來，branch 的判斷才用到正確的值）。Ch 19 hazard detection unit 綜合會把這個優先序講死。

## 本章重點整理

- **load-use hazard**：load 的資料到 MEM 級末端才產生，比相鄰指令進 EX 晚一拍，forwarding 趕不上。這是唯一非 stall 不可的 data hazard。
- **偵測**：`id_ex_mem_read && id_ex_rd != 0 && (id_ex_rd == id_rs1 || id_ex_rd == id_rs2)`——EX 是 load、rd 非 x0、命中 ID 級某個 rs。
- **處理三件事**：凍結 PC（`pc_write=0`）、凍結 IF/ID（`if_id_write=0`）、ID/EX 插 bubble（清控制訊號）。三件一組，缺一出錯。
- 只 stall **一拍**：下一拍 load 到 MEM/WB 邊界，靠 MEM/WB→EX forwarding 接上，hazard 自動解除。
- 真跑對照：沒 stall 時 `x4=0`（錯），加 stall 後 `x4=200`（對）。
- load 和 use 隔一條無關指令就不用 stall——compiler instruction scheduling 靠這個消 penalty。

## 自我檢核

- [ ] 我能畫出 `lw` 的五級時序，指出 x3 的值到哪一級末端才存在，說明為什麼 forwarding 差一拍。
- [ ] 我能寫出 load-use hazard 的偵測布林式，並解釋每個條件（含為什麼要 `rd != x0`）。
- [ ] 我能說清楚 stall 要做的三件事，以及少了「插 bubble」會發生什麼（副作用執行兩次）。
- [ ] 我能解釋為什麼只 stall 一拍就夠，下一拍 hazard 怎麼自動解除。
- [ ] 我能說出 load 和 use 隔一條無關指令時為什麼不用 stall，以及 compiler 如何利用這點。
- [ ] 我能預測範例一沒 stall 時 x4、x5 為什麼分別是 0 和 1。

## 延伸閱讀

- **《Computer Organization and Design, RISC-V Edition》(Patterson & Hennessy) 第 4.7 節「Data Hazards: Forwarding versus Stalling」的 load-use 部分**：本章的教科書版本。它的 hazard detection unit 圖（比對 ID/EX.MemRead 與 rs 欄位）就是我們偵測邏輯的來源，圖 4.59–4.61 把 stall 一拍的時序畫得很清楚，讀它把時序在腦中對齊。
- **《Digital Design and Computer Architecture, RISC-V Edition》(Harris & Harris) 第 7.5.3 節「Stalls」**：從 HDL 角度講 stall 怎麼接線（Stall 訊號怎麼餵給 PC 和 pipeline register 的 enable），和我們的 `pc_write`/`if_id_write` 一一對應，補足「訊號怎麼變成 RTL」這一層。
- **[riscv-boom 文件](https://docs.boom-core.org/) 的 pipeline 章節**：看工業級 OoO core 怎麼**避免**這種 stall——它不凍結整條 pipeline，而是把 load-use 相依的指令丟進 issue queue 等資料到位、放無關指令先跑。讀它你會懂本章的 in-order stall 是最直接但也最浪費的解法，以及 Part 6 為什麼要學 OoO。
- **[Sodor 的 rv32_5stage](https://github.com/ucb-bar/riscv-sodor) 的 `cpath.scala` / `dpath.scala`**：官方教學 5 級 core 的 hazard 處理原始碼（Chisel）。搜 `stall` 和 `dec_load_use`，對照我們的 `load_use_hazard`，你會發現微架構一模一樣，只是換語言。這是你這門課的「標準答案」對照組。

load-use hazard 是 data hazard 的最後一塊。但 pipeline 還有另一大類麻煩：branch 要跳到哪，要好幾級之後才知道——在那之前抓進來的指令怎麼辦？下一章我們處理 control hazard，學 flush 和把 branch 判斷提前。

→ [Ch 18 Control hazard：branch 代價、flush、提前 resolve](./18-control-hazard-branch.md)
