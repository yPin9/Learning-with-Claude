# Ch 19 — Hazard detection unit + structural hazard 綜合

> **目標**：把散在 Ch 16–18 的東西——forwarding、load-use stall、branch flush，再加上 structural hazard——全部收進一個統一的 **hazard 控制邏輯**，講清楚每根訊號（forward / stall / flush）各自負責什麼、彼此怎麼互動、同一拍多個 hazard 撞在一起時的**優先序**。這是把「零件」變成「能自我保護的 pipeline」的關鍵一章。全程真跑，含 load-use 撞 branch 這種最難的組合。
> **環境**：WSL + verilator 4.038。輸出皆真跑，沿用 Part 2 的 pipelined `core`。這是深挖章。

## 為什麼需要一個統一的 hazard unit？

到目前為止我們解決了三類 hazard，但它們各自為政：

- **forwarding**（Ch 16）：解決「結果已算好、還沒寫回」的 data hazard，繞線把值送過去，0 penalty。
- **load-use stall**（Ch 17）：解決 forwarding 趕不上的 load data hazard，stall 一拍。
- **branch flush**（Ch 18）：解決 control hazard，把錯抓的指令清成 NOP。

問題來了：這三者會在**同一拍**撞在一起。舉個真實情境——`lw x2` 緊接 `beq x2, x1, target`：

- 這是 load-use hazard（branch 用剛 load 的 x2）→ 想 stall。
- 這也是 control hazard（beq 是 branch）→ 若 taken 想 flush。
- 而 beq 的另一個來源 x1 可能還在 EX → branch-use，也想 stall。

同一拍，stall 和 flush 兩個訊號都想動。**誰先？** 如果順序搞錯，pipeline 會用還沒到位的 x2 去判斷 branch，跳錯方向。這就是為什麼要一個**統一的 hazard detection unit**：把所有 hazard 集中判斷，訂死優先序，保證任何組合都正確。

散裝的邏輯各自看似對，湊在一起會互相打架。統一的控制單元才是 pipeline 正確性的守門員。

## 先建立直覺：pipeline 的三種「動作」

hazard unit 對 pipeline 只有三種動作，每種對應一組訊號：

```
   ┌─────────────── hazard detection unit ───────────────┐
   │  看：ID 級要讀誰、EX/MEM 級在做什麼、是不是 branch     │
   │  決定三種動作：                                       │
   │                                                      │
   │   forward ── 把後級的值繞回 EX/ID 的運算元 (0 penalty)│
   │   stall ──── 凍結 PC + IF/ID，ID/EX 插 bubble (等一拍) │
   │   flush ──── 把 IF/ID 清成 NOP (殺錯抓的指令)          │
   └──────────────────────────────────────────────────────┘
```

三種動作的哲學不同：

- **forward 是「補救而不停」**：值在別的地方，我拉過來，pipeline 照跑。優先用它——0 代價。
- **stall 是「等」**：值還沒到任何拉得到的地方，只能原地等一拍讓它到位。有代價（1 拍 bubble），但 forward 救不了時非用不可。
- **flush 是「殺」**：這條指令根本不該執行（走錯路了），把它變 NOP。這是 control hazard 專屬。

hazard unit 每一拍看 pipeline 各級的狀態，決定要 forward 哪些、要不要 stall、要不要 flush。**能 forward 就 forward，forward 不行才 stall，走錯路就 flush**——這是總原則。

## 核心概念：四種 hazard 與各自的訊號

我們把要處理的 hazard 列全，對照它用哪種動作：

| hazard 種類 | 觸發情境 | 動作 | penalty |
|---|---|---|---|
| RAW（EX 用）| 前一兩條的 ALU 結果被 EX 級用 | **forward**（EX/MEM、MEM/WB → EX） | 0 |
| RAW（ID 用，branch）| branch 來源是前面已過 EX 的結果 | **forward**（EX/MEM、MEM/WB → ID） | 0 |
| load-use | load 的 rd 緊接被 ID 級指令用 | **stall** 1 拍 + forward | 1 |
| branch-use | branch 來源還在 EX（或 load 在 EX/MEM）| **stall** 1~2 拍 | 1~2 |
| control（branch taken）| branch 在 ID 判定 taken | **flush** IF/ID | 1 |
| structural | 兩個單元同一拍搶同一資源 | 設計上避免（見後） | — |

重點觀察：**forward 和 stall 是搭配的**，不是二選一。load-use stall 一拍後，被卡的指令進 EX 時還是靠 forward（MEM/WB → EX）拿到 load 資料。stall 只是「爭取時間讓值到達可 forward 的位置」。flush 則完全獨立——它處理的是「這指令不該存在」，跟資料在不在無關。

## 底層機制（一）：forwarding unit——EX 與 ID 兩套

forwarding 有兩套，因為我們有兩個地方要用值：EX 級的 ALU，和 ID 級的 branch 比較器。

### EX 級 forwarding（Ch 16）

EX 級 ALU 的兩個運算元，各自可能要從後級繞回來：

```systemverilog
// EX forwarding：EX/MEM(2'b10)、MEM/WB(2'b01) 兩路
always_comb begin
    fwd_a = 2'b00; fwd_b = 2'b00;
    if (ex_mem_reg_write && ex_mem_rd != 0 && ex_mem_rd == id_ex_rs1) fwd_a = 2'b10;
    else if (wb_reg_write && wb_rd != 0 && wb_rd == id_ex_rs1)        fwd_a = 2'b01;
    if (ex_mem_reg_write && ex_mem_rd != 0 && ex_mem_rd == id_ex_rs2) fwd_b = 2'b10;
    else if (wb_reg_write && wb_rd != 0 && wb_rd == id_ex_rs2)        fwd_b = 2'b01;
end
```

**優先序寫在 `else if` 裡**：EX/MEM（較新的值）優先於 MEM/WB（較舊）。因為如果兩個都命中同一個 rd，較晚產生的（EX/MEM，剛出 ALU）才是最新的正確值，較早的（MEM/WB）是過期的。這個「新值蓋舊值」是 forwarding 的第二個關鍵優先序。

### ID 級 forwarding（給 branch 比較，Ch 18）

branch 在 ID 比較，它的來源也可能要 forward。但這裡有一個 EX 級沒有的陷阱：

```systemverilog
// ID forwarding：EX/MEM、MEM/WB → ID。
// 關鍵：EX/MEM 若是 load，ex_mem_alu 是「位址」不是資料，不能 forward!
always_comb begin
    fwd_id_a = 2'b00; fwd_id_b = 2'b00;
    if (ex_mem_reg_write && !ex_mem_mem_read && ex_mem_rd != 0 && ex_mem_rd == id_rs1) fwd_id_a = 2'b10;
    else if (wb_reg_write && wb_rd != 0 && wb_rd == id_rs1)                             fwd_id_a = 2'b01;
    if (ex_mem_reg_write && !ex_mem_mem_read && ex_mem_rd != 0 && ex_mem_rd == id_rs2) fwd_id_b = 2'b10;
    else if (wb_reg_write && wb_rd != 0 && wb_rd == id_rs2)                             fwd_id_b = 2'b01;
end
```

`!ex_mem_mem_read` 這個條件：EX/MEM register 帶的 `ex_mem_alu` 對 R-type 是 ALU 結果（能 forward），對 **load 是記憶體位址**（不能 forward，load 的資料還在 MEM 級沒讀出來）。若不排除，branch 會拿 load 的「位址」去比較，錯得離譜。這種 load→branch 的相依，改由 hazard unit stall 到 load 資料進 MEM/WB 再 forward（下面詳述）。

## 底層機制（二）：stall——三種來源合流

stall 有三個來源，合流到同一組凍結訊號：

```systemverilog
// (1) load-use：EX 級 load 的 rd 命中 ID 級 rs
logic load_use_hazard;
always_comb begin
    load_use_hazard = id_ex_mem_read && id_ex_rd != 0 &&
                      ((id_ex_rd == id_rs1) || (id_ex_rd == id_rs2));
end

// (2) branch-use：branch 來源還在 EX(2a)，或是還在 EX/MEM 的 load(2b)
logic branch_use_hazard;
always_comb begin
    branch_use_hazard =
        id_branch && (
            (id_ex_reg_write && id_ex_rd != 0 &&
                ((id_ex_rd == id_rs1) || (id_ex_rd == id_rs2))) ||        // 2a
            (ex_mem_mem_read && ex_mem_rd != 0 &&
                ((ex_mem_rd == id_rs1) || (ex_mem_rd == id_rs2)))         // 2b
        );
end
```

- **(1) load-use**：EX 是 load、rd 命中 ID 級任一 rs → stall 一拍。
- **(2a) branch-use（EX）**：branch 的來源正被 EX 級指令算（結果還沒進 EX/MEM）→ stall 一拍，等它進 EX/MEM，ID 就能 forward。
- **(2b) branch-use（load 在 EX/MEM）**：branch 的來源是一個已經進到 EX/MEM 但還是 load 的指令（資料還在 MEM 沒讀出）→ 再 stall 一拍，等資料進 MEM/WB。

(2a) 和 (2b) 合起來，讓 **load 緊接 branch 自然 stall 兩拍**：第一拍 load 在 EX（load-use 或 2a 觸發），第二拍 load 進 EX/MEM 但還是 load（2b 觸發），第三拍 load 到 MEM/WB，ID forward 得到正確資料，branch 才 resolve。這是全課最刁鑽的 hazard——branch 提前到 ID，卻要用一個比它晚兩級才出資料的 load，中間差兩拍，非 stall 兩拍不可。

## 底層機制（三）：優先序——stall 壓過 flush

現在三種動作湊在一起，看 hazard unit 最終怎麼仲裁：

```systemverilog
always_comb begin
    pc_write     = 1'b1;   // 預設：一切正常
    if_id_write  = 1'b1;
    id_ex_bubble = 1'b0;
    if_id_flush  = 1'b0;

    // 優先序：先處理 stall（load-use / branch-use），再處理 branch flush
    if (load_use_hazard || branch_use_hazard) begin
        pc_write     = 1'b0;   // 凍結 PC
        if_id_write  = 1'b0;   // 凍結 IF/ID
        id_ex_bubble = 1'b1;   // ID/EX 插 bubble
    end else if (id_branch_taken) begin
        if_id_flush  = 1'b1;   // flush 錯抓的下一條
    end
end
```

**`else if` 就是優先序**：只要有任何 stall，就 stall，**不看 branch**；只有在不 stall 的前提下，才處理 branch flush。為什麼 stall 必須壓過 flush？

想想 `lw x2` → `beq x2, x1, target`。beq 的 x2 還沒 load 好。如果這一拍就讓 branch resolve（flush），它會拿**還沒到位的 x2**（可能是舊值）去比較，判斷出錯誤的 taken/not-taken，跳錯地方。正確做法是**先 stall**，把 x2 等到位，branch 的比較才用得到正確值，然後才 resolve、才 flush。

**順序反了會怎樣**：若讓 flush 優先，branch 用錯的 x2 判斷方向，就算後面 x2 到位也來不及——方向已經錯了，pipeline 已經往錯的路抓指令。所以鐵律：**資料 hazard（stall）永遠先於控制 hazard（flush）**。先確保 branch 的輸入正確，再談 branch 的輸出（跳哪）。

還有一個隱含優先序：`id_branch_taken` 本身依賴 `id_cmp_a`/`id_cmp_b`，而這兩個值經過 ID forwarding。所以「forward → 得到正確比較值 → 判斷 taken → flush」是一條資料流依賴鏈。stall 保證這條鏈的輸入（load 資料）先到位，整條鏈才成立。

## 範例：load-use 撞 branch，優先序實戰

這支程式故意讓 `lw` 的結果馬上被 `beq` 用——load-use 和 branch 撞在同一條指令上：

```asm
_start:
    lui  x6, 0x80000
    addi x1, x0, 7
    sw   x1, 0(x6)
    lw   x2, 0(x6)          # x2 = 7            <-- load
    beq  x2, x1, tgt        # 用剛 load 的 x2 判斷！7==7 → taken
    addi x9, x0, 111        # POISON：不該執行
tgt:
    addi x3, x0, 55         # x3 = 55（正確路徑）
halt:
    beq x0,x0,halt
```

手算預期：branch taken（7==7），跳過毒指令，x9 保持 0，x3=55。逐 cycle：

```
cyc | PC        | stall flush | WB
----+-----------+-------------+------------------
  4 | 0x80000010 |   0     0   | x6  <= -2147483648 (0x80000000)
  5 | 0x80000014 |   1     0   | x1  <= 7 (0x00000007)   <-- stall 第 1 拍(load 在 EX)
  6 | 0x80000014 |   1     0   | -                        <-- stall 第 2 拍(load 在 EX/MEM)
  7 | 0x80000014 |   0     1   | x2  <= 7 (0x00000007)   <-- 資料到位，resolve+flush!
  8 | 0x80000018 |   0     0   | -
  9 | 0x8000001c |   0     0   | -
 10 | 0x80000020 |   0     1   | -
 11 | 0x8000001c |   0     0   | -
 12 | 0x80000020 |   0     1   | x3  <= 55 (0x00000037)
```

跑到最後 dump 暫存器：

```
prio final: x1=7 x2=7 x3=55 x9=0
```

逐點對照優先序在做什麼：

- **cyc 5、6：連續 stall 兩拍**（PC 凍在 0x80000014）。第一拍 load 在 EX（阻止 branch 用未到位的 x2），第二拍 load 進 EX/MEM 但還是 load、資料在 MEM（`ex_mem_mem_read` 觸發 2b）。**這兩拍 `flush=0`**——優先序讓 stall 壓過任何 branch 動作，branch 根本還沒 resolve。
- **cyc 7：stall 結束，`flush=1`**。此刻 x2 的資料已進 MEM/WB，ID forwarding 拿到 x2=7，beq 比較 7==7 判定 taken，flush 掉錯抓的毒指令。
- **x9 = 0**：毒指令從未執行。**x3 = 55**：正確路徑執行。

如果優先序反了（flush 先於 stall），cyc 5 就會用還沒 load 的 x2（此時是舊值/0）判斷 branch，7==0 為 false，判成 not-taken，繼續執行毒指令 `x9 <= 111`——結果就錯了。**這支程式就是優先序正確性的活證明。**

## 底層機制（四）：structural hazard——本課如何避免

**structural hazard（結構冒險）**：兩條在不同級的指令，同一拍要用**同一個硬體資源**，撞車。經典例子是**記憶體只有一個 port**：

```
   lw   x3, 0(x6)   IF   ID   EX   MEM   WB
   add  x4, ...          IF   ID   EX    MEM
   sub  x5, ...               IF   ID    EX
   ...                             IF ← 這拍要讀 imem 抓指令
                                   ▲    而 lw 這拍要讀 dmem 拿資料
                              同一個記憶體 port 被兩邊搶 → structural hazard!
```

IF 級每拍都要讀記憶體抓指令，而 MEM 級遇到 load/store 時也要讀寫記憶體。如果指令和資料共用**一塊單 port 記憶體**，這兩個存取同一拍撞車——這就是 structural hazard。

**本課怎麼避免**：我們用**分離的指令記憶體（imem）和資料記憶體（dmem）**——各自獨立的 port：

```systemverilog
logic [31:0] imem [0:1023];   // 指令記憶體：IF 級專用
logic [31:0] dmem [0:1023];   // 資料記憶體：MEM 級專用
```

IF 讀 imem、MEM 讀寫 dmem，兩者不共用資源，**結構上根本不會撞**。這對應真實 CPU 的 **Harvard 架構**（指令、資料分開的 cache）——L1 分成 I-cache 和 D-cache，正是為了消掉這個 structural hazard。所以本課的 hazard unit **不需要處理 structural hazard**，設計時就用分離記憶體把它消滅在源頭。

其他潛在 structural hazard 也都被設計避開了：regfile 有獨立的 2 讀口 1 寫口（Ch 8），WB 級寫、ID 級讀不撞（甚至同拍讀寫我們用「寫優先」化解，見進階）；ALU 只有 EX 級用，不共享。**避免 structural hazard 的最好方法是設計時給足資源，而不是事後 stall**——這是和 data/control hazard 很不同的哲學。

## 對比取捨

| 動作 | 處理的 hazard | 代價 | 何時用 |
|---|---|---|---|
| forward | RAW（值已產生） | 0 | 一律優先 |
| stall + forward | load-use、branch-use | 1~2 拍 | forward 趕不上 |
| flush | control（branch taken） | 1 拍 | 走錯路 |
| 分離資源 | structural | 硬體面積 | 設計時給足，不事後補救 |

| 優先序決策 | 本課選擇 | 反過來的後果 |
|---|---|---|
| stall vs flush | **stall 先** | flush 先 → branch 用未到位的值判斷，跳錯 |
| EX/MEM vs MEM/WB forward | **EX/MEM 先（新蓋舊）** | 反了 → 拿到過期值 |
| structural hazard | 分離 imem/dmem 避免 | 單記憶體 → IF 和 MEM 撞、要 stall |

## 踩雷區

**雷 1：把 stall 和 flush 的優先序搞反。**
- 錯誤直覺：「branch taken 最急，先 flush 把錯指令殺掉」。
- 正確認識：branch 的**判斷本身**要正確的輸入。若這一拍 branch 的來源還沒到位（load-use/branch-use），必須先 stall 把值等到位，branch 才判斷得對，然後才 flush。stall 先於 flush 是鐵律：先保證輸入對，再談輸出。範例中 cyc 5、6 stall（flush=0），cyc 7 才 flush，就是這個順序。反了會判錯方向、跳錯地方。

**雷 2：ID forwarding 忘了排除 load（`!ex_mem_mem_read`）。**
- 錯誤直覺：「EX/MEM 有值就 forward 給 branch」。
- 正確認識：EX/MEM 的 `ex_mem_alu` 對 R-type 是 ALU 結果（能 forward），對 **load 是位址**（不能 forward，資料還沒讀出來）。branch 若拿 load 的位址去比較會全錯。要嘛加 `!ex_mem_mem_read` 排除、要嘛靠 hazard unit stall 到 load 資料進 MEM/WB——本課兩者都做（雙重保險）。這是 load→branch 這條刁鑽路徑的核心。

**雷 3：以為 load-use 永遠只 stall 一拍。**
- 錯誤直覺：「load-use hazard = stall 1 拍，記住就好」。
- 正確認識：load-use stall 一拍**只在使用者於 EX 級用**時成立。若使用者是 **branch（在 ID 用，比 EX 早一級）**，load 的資料要多一拍才到得了 ID forward 得到的位置——**load→branch 要 stall 兩拍**（範例 cyc 5、6）。stall 幾拍取決於「生產者出資料的級」和「消費者要用的級」差幾級，不是死記數字。

**雷 4：以為 hazard unit 要處理 structural hazard。**
- 錯誤直覺：「四大 hazard 都要在 hazard unit 裡偵測處理」。
- 正確認識：data 和 control hazard 靠 hazard unit 的 forward/stall/flush 動態處理；**structural hazard 靠設計時給足資源靜態避免**（分離 imem/dmem、2R1W regfile）。本課 hazard unit 裡**沒有一行**在處理 structural hazard——因為我們用 Harvard 架構把它消滅在源頭。到 Part 4 做 cache 時，若 I/D 共用某層記憶體，才會重新面對它。

**雷 5：forwarding 的新舊值優先序寫成 `if...if` 而非 `if...else if`。**
- 錯誤直覺：「兩個 forward 條件分開寫，都判一下」。
- 正確認識：當 EX/MEM 和 MEM/WB 都命中同一個 rd（例如連續三條都寫 x1），必須用 `else if` 讓 **EX/MEM（新）優先**，因為它是較晚產生的最新值。寫成兩個獨立 `if`，後面的 MEM/WB（舊值）會蓋掉前面的 EX/MEM（新值），forward 到過期資料。優先序必須靠 `else if` 的短路來保證。

## 進階延伸

- **同拍 regfile 讀寫的 write-first**：WB 級在 clk 上升沿寫 regfile，ID 級同拍要讀。如果讀到的是舊值（寫還沒生效），又是一個 hazard。本課 regfile 用「非同步讀」，且我們讓 WB 的寫在半拍前生效（或在 forwarding 涵蓋），實務上很多設計直接在 regfile 做 **write-first bypass**（寫的值同拍就能被讀到）。這消掉了「距離 3」的 RAW，讓 forwarding 只需管 EX/MEM 和 MEM/WB 兩路。你可以試著把 forwarding 的 MEM/WB 路拔掉，看哪些程式因此算錯，就懂 regfile write-first 幫你省了什麼。
- **hazard unit 是關鍵路徑的一部分**：偵測邏輯（一堆比較器 + 優先序 mux）夾在 ID/EX 之間，它算得慢會拉長 clock 週期。真設計會把偵測邏輯簡化、平行化，甚至把 forwarding mux 的 select 提前算好。這是 Part 3（Ch 24 關鍵路徑）的量化主題——hazard 處理不是免費的，它吃時脈。
- **越深的 pipeline，hazard 越兇**：我們五級，load-use 差一拍、load→branch 差兩拍。若 pipeline 拉到 10 級、15 級（真高頻 core），每個 hazard 的 penalty 都變大，forwarding 路徑變多變長，hazard unit 複雜度爆炸。這是「深 pipeline 換高時脈」的隱藏代價，也是 branch prediction（Part 3）和 out-of-order（Ch 36）變得不可或缺的原因——penalty 太大，非得預測和亂序來遮掩。
- **形式化驗證 hazard 正確性**：hazard 的 corner case 多到手寫測試難窮盡（各種 forward 距離 × load/branch × 優先序組合）。工業界用 **riscv-formal** 之類的 formal 工具，數學證明「pipelined core 對任意指令序列，執行結果和單週期參考模型一致」。Ch 39 驗證方法學會講。本章我們用「精心設計的程式 + 和手算對照」是教學版，真做產品要上 formal。

## 本章重點整理

- hazard unit 對 pipeline 只有三種動作：**forward**（補救不停，0 代價）、**stall**（等值到位，1~2 拍）、**flush**（殺錯抓指令，1 拍）。總原則：能 forward 就 forward，不行才 stall，走錯路才 flush。
- **forwarding 兩套**：EX 級（給 ALU）、ID 級（給 branch 比較）。ID 級要排除 load（`ex_mem_alu` 是位址不是資料）。兩路命中同 rd 時 EX/MEM（新）優先於 MEM/WB（舊），靠 `else if` 保證。
- **stall 三來源**：load-use、branch-use（EX）、branch-use（load 在 EX/MEM）。load→branch 因此自然 stall 兩拍。
- **優先序鐵律：stall 壓過 flush**。branch 的輸入要先正確（stall 等到位），才能正確判斷方向（flush）。反了會跳錯。範例中 cyc 5、6 stall、cyc 7 才 flush 是實證。
- **structural hazard 靠設計避免**，不靠 hazard unit：分離 imem/dmem（Harvard）、2R1W regfile。給足資源，不事後 stall。
- 全部組合真跑驗證：load-use 撞 branch，stall 兩拍後正確 taken，毒指令 x9=0 未執行，x3=55 正確。

## 自我檢核

- [ ] 我能說出 hazard unit 的三種動作各對應哪組訊號、各自的代價與哲學。
- [ ] 我能寫出 EX forwarding 和 ID forwarding 的差異，並解釋 ID forwarding 為什麼要 `!ex_mem_mem_read`。
- [ ] 我能列出 stall 的三個來源，並解釋為什麼 load→branch 要 stall 兩拍而非一拍。
- [ ] 我能論證為什麼 stall 必須優先於 flush，並說出優先序反了會發生什麼具體錯誤。
- [ ] 我能解釋 forwarding 兩路命中同 rd 時為什麼 EX/MEM 優先，以及 `if...if` 寫法會壞在哪。
- [ ] 我能說清楚 structural hazard 是什麼、本課如何用分離記憶體避免，以及為什麼它和 data/control hazard 的處理哲學不同。
- [ ] 我能預測範例中 cyc 5、6 為什麼 flush=0、cyc 7 為什麼才 flush=1。

## 延伸閱讀

- **《Computer Organization and Design, RISC-V Edition》(Patterson & Hennessy) 第 4.7–4.8 節整合部分（特別是 hazard detection unit 的完整 datapath 圖 4.60、以及 4.8 的 flush 整合）**：本章的教科書骨架。它把 forwarding unit 和 hazard detection unit 畫在同一張 datapath 上，訊號走向和優先序一目了然，讀它把「散裝訊號」在腦中拼成一張圖。它對 stall vs flush 交互的討論正是本章優先序的來源。
- **《Digital Design and Computer Architecture, RISC-V Edition》(Harris & Harris) 第 7.5 節「Hazards」整節**：從 HDL 角度把 forward/stall/flush 的訊號生成和 pipeline register 的 enable/clear 邏輯講到底，和我們的 `pc_write`/`if_id_write`/`id_ex_bubble`/`if_id_flush` 一一對應。它的 hazard 邏輯真值表可以拿來核對你自己的偵測條件有沒有漏。
- **[Sodor rv32_5stage 的 `cpath.scala`](https://github.com/ucb-bar/riscv-sodor)**：官方教學 5 級 core 的完整 control path 原始碼。搜 `stall`、`exe_br_type`、`ctrl.dec_stall`，對照本章的 hazard unit——你會看到工業教學 core 怎麼把所有 hazard 的偵測和優先序寫在一個集中的 control 模組裡，結構和本章一致。這是最好的「完整標準答案」。
- **[riscv-formal](https://github.com/YosysHQ/riscv-formal)**：用 formal 方法驗證 RISC-V core 正確性的框架。讀它的 README 和 checks 說明，你會理解為什麼「精心設計的測試程式」不足以保證 hazard 邏輯全對——corner case 的組合爆炸只有 formal 能窮盡。這是 Ch 39 的預習，也是你做完本課想把 core 做到「真的沒 bug」的下一步。

hazard 的偵測、forwarding、stall、flush、優先序、structural 避免——這一章全講完了。零件齊了、規則清了，下一章我們把 Ch 13–19 的所有東西接成一顆完整的 pipelined `core`，跑一支混合三種 hazard 的程式，和單週期版/手算預期逐暫存器對照，確認結果一致但 cycle 數不同。

→ [Ch 20 pipeline 完整整合 + 打穿 riscv-tests](./20-pipeline-complete.md)
