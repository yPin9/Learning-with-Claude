# Ch 27 — D-cache + 與 pipeline 整合、miss stall

> **目標**：I-cache 只讀很單純，D-cache 要**又讀又寫**，複雜度全在寫策略上。這章你會做出一個 direct-mapped D-cache，實作 write-through + no-write-allocate，真跑一遍「store 100 → write-through 寫穿記憶體 → load 回來得到 100」的完整流程；學會 D-cache miss 怎麼接回 Ch 17 的 stall 機制把 MEM 級卡住；認識 write buffer 為什麼能藏住 write-through 的延遲；並淺提多核 cache coherence 這頭巨獸留給進階。這是深挖章。
> **環境**：WSL + verilator 4.038。所有 load/store 行為與 cycle 數皆真跑。本章 `dcache` 沿用 Ch 26 `dm_icache` 的骨架，加上寫路徑。

## 為什麼 D-cache 比 I-cache 麻煩

Ch 26 的 I-cache 只做一件事：給位址、回指令。指令流唯讀，不存在「寫進去要不要同步到下層」「寫的時候 miss 怎麼辦」這些問題。

D-cache 不一樣。`sw`（store）要**寫**資料，這一寫就引出一串設計決策：

1. **寫命中時**，只寫 cache 就好，還是連下層記憶體一起寫？（write-through vs write-back）
2. **寫 miss 時**，要不要先把那條 line 搬進 cache 再寫（write-allocate），還是直接寫穿下層不進 cache（no-write-allocate）？
3. **寫和讀的一致性**：store 完馬上 load 同一位址，要拿到剛寫的值——cache 得保證這件事。

而且 D-cache 接在 pipeline 的 **MEM 級**（Ch 14 的第四級），miss 時要把整條 pipeline 從 MEM 往前卡住（沿用 Ch 17 的 stall）。I-cache miss 卡 IF、D-cache miss 卡 MEM，卡的位置不同、要協調的東西也不同。

這章我們選最容易講清楚、也最常見於教學/嵌入式 core 的組合：**write-through + no-write-allocate**。理由：write-through 讓 cache 和記憶體永遠一致（不必維護 dirty bit、替換時不必寫回），no-write-allocate 讓 store miss 不觸發 refill（直接寫穿，邏輯最單純）。代價是每次 store 都碰下層——這正是 write buffer 要來救的（本章後半）。

## 先建立直覺：影印本與正本

把 cache 想成你桌上的**影印本**，下層記憶體是**檔案室的正本**。

- **write-through（寫穿）**：你改了影印本，**立刻跑一趟檔案室把正本也改了**。好處：正本永遠是最新，任何人（其他核、DMA）去看正本都對。壞處：每次改都要跑一趟檔案室（慢）。
- **write-back（寫回）**：你只改影印本，在上面貼張「已改動（dirty）」的標籤，**等這份影印本要被丟掉（替換）時，才一次拿去更新正本**。好處：改十次只跑一趟檔案室。壞處：正本在被寫回前是舊的（多核/DMA 要小心），而且要維護 dirty 標籤、替換時多一道寫回手續。

**no-write-allocate（寫 miss 不配置）**：你要改的那份文件桌上沒有影印本（write miss），你**不影印一份下來**，直接跑檔案室改正本。相對的 write-allocate 是先影印一份下來再改（之後可能還會用到）。對「寫了就不太會馬上再讀」的模式，no-write-allocate 省得多。

本章的組合＝「每次改都跑檔案室（write-through）＋ 桌上沒影印本就直接改正本不影印（no-write-allocate）」。

## 核心概念：三條路徑

D-cache 要處理三種存取，各走不同路徑：

```
   load  hit  ─→ 一拍：從 data_arr 讀出回 CPU               （最快）
   load  miss ─→ REFILL：向下層搬整條 line 回來，再回資料    （多拍）
   store      ─→ WRITE_THRU：寫穿下層記憶體（一拍模型）
                 若命中，順便更新 cache 副本；若 miss，不 refill（no-write-allocate）
```

- **load hit**：跟 I-cache 一樣，`valid && tag_match`，當拍回資料。
- **load miss**：跟 I-cache 一樣，進 REFILL 把整條 line 搬回，設 valid，回資料。
- **store**（不論命中）：write-through，把資料寫穿到下層記憶體。如果剛好命中（那條 line 在 cache），**同時更新 cache 裡的副本**（否則副本會變舊，下次 load 讀到舊值）；如果 miss，no-write-allocate 不搬 line 進來，寫完就算。

第三條路徑的「命中就更新副本」是正確性關鍵——write-through 不只寫記憶體，還得維護 cache 副本的一致，不然你 store 之後 load 同位址會拿到 cache 裡的舊值。

## 底層機制：dcache 的實作

介面比 I-cache 多了 `req_we`（1=store 0=load）、`req_wdata`（要寫的資料），下層記憶體側多了 `mem_we`/`mem_wdata`（write-through 要寫下層）：

```systemverilog
module dcache #(
    parameter int CACHE_SIZE = 256,
    parameter int BLOCK_SIZE = 16
) (
    input  logic        clk, rst,
    input  logic        req_valid,
    input  logic        req_we,        // 1=store 0=load
    input  logic [31:0] req_addr,
    input  logic [31:0] req_wdata,
    output logic [31:0] resp_rdata,
    output logic        resp_ready,
    output logic        stall,
    // 記憶體側
    output logic        mem_req, mem_we,
    output logic [31:0] mem_addr, mem_wdata,
    input  logic [31:0] mem_rdata,
    input  logic        mem_valid,
    output logic [31:0] hit_count, miss_count
);
```

位址切位、命中判定跟 Ch 26 一模一樣（`index`/`tag`/`word_off`、`hit = valid && tag_match`），不再重複。差別在三條路徑的分類與狀態機。分類（只在 IDLE 有意義）：

```systemverilog
    logic load_hit, load_miss, store_req;
    assign load_hit   = req_valid && !req_we && hit  && state == IDLE;
    assign load_miss  = req_valid && !req_we && !hit && state == IDLE;
    assign store_req  = req_valid &&  req_we          && state == IDLE;  // store 不分命中，都要寫穿
```

狀態機三態：`IDLE`（load hit 當拍完成）、`REFILL`（load miss 搬 line）、`WRITE_THRU`（store 寫穿下層）。`resp_ready` 是組合輸出，在「操作完成的那一拍」拉起：

```systemverilog
    typedef enum logic [1:0] {IDLE, REFILL, WRITE_THRU} state_t;
    state_t state;
    logic [WORD_OFF_BITS:0] refill_cnt;
    logic [IDX_BITS-1:0]    refill_index;
    logic [TAG_BITS-1:0]    refill_tag;
    logic [31:0]            refill_base, st_addr, st_data;

    // 多週期操作在「完成的那一拍」給 resp_ready（組合，不用延遲暫存器，避免脈衝殘留）
    logic refill_last, wt_done;
    assign refill_last = (state == REFILL)     && mem_valid && (refill_cnt == WORDS_PER_BLK-1);
    assign wt_done     = (state == WRITE_THRU) && mem_valid;
    assign stall       = (state != IDLE && !refill_last && !wt_done) || load_miss || store_req;
    assign resp_ready  = load_hit || refill_last || wt_done;
    // refill 完成那拍若要的正是這拍收到的 word 用 mem_rdata，否則從已填好的 data_arr 讀
    assign resp_rdata  = (refill_last && (word_off == refill_cnt[WORD_OFF_BITS-1:0]))
                         ? mem_rdata : data_arr[index][word_off];
```

> 這裡有個踩過的坑值得記下：早期版本用一個 `done` 暫存器在完成的下一拍拉 `resp_ready`，結果前一個 store 完成的 `done` 脈衝殘留，害緊接的 load 誤判命中（拿到 0）。教訓：**多週期完成訊號要用組合邏輯在「完成當拍」表達（`state==WRITE_THRU && mem_valid`），別用會殘留到下一拍、可能污染下一個存取的暫存器脈衝。** 這是實作 FSM 輸出時的常見陷阱。

記憶體介面（組合）：refill 時逐 word 讀，write-through 時寫下層：

```systemverilog
    always_comb begin
        mem_req = 0; mem_we = 0; mem_addr = 0; mem_wdata = 0;
        if (state == REFILL) begin
            mem_req = 1; mem_we = 0; mem_addr = refill_base + (refill_cnt << 2);
        end else if (state == WRITE_THRU) begin
            mem_req = 1; mem_we = 1; mem_addr = st_addr; mem_wdata = st_data;
        end
    end
```

狀態轉移與 cache 副本更新：

```systemverilog
    always_ff @(posedge clk) begin
        if (rst) begin
            state <= IDLE; refill_cnt <= 0; hit_count <= 0; miss_count <= 0;
            for (int i = 0; i < NUM_BLOCKS; i++) valid_arr[i] <= 1'b0;
        end else case (state)
            IDLE: begin
                if (load_hit) hit_count <= hit_count + 1;
                if (store_req) begin
                    if (hit) begin
                        hit_count <= hit_count + 1;
                        data_arr[index][word_off] <= req_wdata;   // write-through：命中也更新副本
                    end else
                        miss_count <= miss_count + 1;
                    st_addr <= req_addr; st_data <= req_wdata;
                    state <= WRITE_THRU;                          // 不論命中都寫穿下層
                end else if (load_miss) begin
                    miss_count <= miss_count + 1;
                    state <= REFILL; refill_cnt <= 0;
                    refill_index <= index; refill_tag <= tag;
                    refill_base  <= {req_addr[31:OFF_BITS], {OFF_BITS{1'b0}}};
                end
            end
            REFILL: if (mem_valid) begin
                data_arr[refill_index][refill_cnt] <= mem_rdata;
                if (refill_cnt == WORDS_PER_BLK-1) begin
                    valid_arr[refill_index] <= 1'b1;
                    tag_arr[refill_index]   <= refill_tag;
                    state <= IDLE; refill_cnt <= 0;
                end else refill_cnt <= refill_cnt + 1;
            end
            WRITE_THRU: if (mem_valid) state <= IDLE;             // 一拍寫穿完成
        endcase
    end
endmodule
```

關鍵設計點：
- **store 命中同時更新副本**（`data_arr[index][word_off] <= req_wdata`），維持 cache 與記憶體一致。
- **store 一律進 WRITE_THRU**（不論命中），因為 write-through 每次都要碰下層。
- **store miss 不 refill**（no-write-allocate）——只 `miss_count++`、寫穿，不搬 line 進來。

## 範例一：store → write-through → load 回來

testbench 用一塊 4 KiB 記憶體陣列當下層，服務 `mem_*` 請求：讀時回陣列值、寫時更新陣列（模擬 write-through 真的寫進記憶體）。跑一連串 store/load 驗證正確性。真跑：

```
=== store 100 到 0x40, 再 load 回來 ===
  store: 2 cycle, mem[0x40]=100 (write-through)
  load : 5 cycle, 讀到 100  (對)

=== 同一 block 內第二個 word：load 0x44 應命中(已 refill) ===
  load 0x44: 1 cycle (命中同 block) 讀到 0

=== store-then-load 連續，寫穿驗證 ===
  store 0xDEAD -> load 讀到 0xDEAD (對), mem[0x80]=0xDEAD

hits=1 misses=4 total_cycles=15
```

逐段看，每一行都在驗證一個機制：

- **store 100 到 0x40（2 cycle）**：`mem[0x40]=100` 證明 write-through 真的把值寫穿到下層記憶體。store 花 2 拍（判定 + 寫穿）。此時 0x40 不在 cache（no-write-allocate 不搬）。
- **load 0x40（5 cycle）讀到 100**：0x40 不在 cache → load miss → REFILL 從記憶體搬整條 line 回來，讀到剛才寫穿的 100。**write-through 保證了 load 看得到 store 的結果**——即使中間 store 沒把值留在 cache，值也在記憶體裡，refill 時撈回來。5 拍（判定 + 4 word refill）。
- **load 0x44（1 cycle）讀到 0**：0x44 和 0x40 在**同一條 16 B line**（word offset 不同）。上一步 load 0x40 已經把整條 line refill 進 cache，所以 0x44 直接命中，1 拍。讀到 0（記憶體那個位置本來就是 0）。**這證明了 spatial locality：搬一條 line 進來，同 line 的鄰居跟著命中。**
- **store 0xDEAD → load 讀到 0xDEAD**：連續 store-then-load 到 0x80，load 讀回 0xDEAD，`mem[0x80]=0xDEAD`。write-through 一致性再次成立。

整段沒有一次 load 讀到錯值，證明 write-through + no-write-allocate 的一致性正確。`hits=1`（那次 0x44 同 block 命中）、`misses=4`（其餘冷啟動/寫穿）符合預期。

## 核心概念：miss 時怎麼 stall pipeline

D-cache 接在 MEM 級。命中時 1 拍拿到資料，pipeline 照常走。miss（load refill 或 store write-through）要好幾拍，這期間**整條 pipeline 從 MEM 級往前必須凍結**——這正是 Ch 17 學過的 stall 機制，只是觸發源從「load-use hazard」換成「D-cache miss」。

```
   D-cache miss stall（沿用 Ch 17）：
   凍結 PC          （pc_write     = 0）
   凍結 IF/ID       （if_id_write  = 0）
   凍結 ID/EX       （id_ex_write  = 0）  ← 比 load-use 多凍這幾級
   凍結 EX/MEM      （ex_mem_write = 0）
   MEM 級原地等 dcache.stall 拉低
   WB 級照常走完（不能卡，否則資料流出錯）
```

和 load-use stall 的差別：load-use 只凍前兩級（PC、IF/ID），因為 hazard 點在 ID；D-cache miss 的 hazard 點在 MEM，所以要凍到 EX/MEM，把 MEM 級之前全部卡住，只讓 WB 級（miss 這條指令的後面那條、已經算完的）流出去。接線上就是把 `dcache.stall` 或進 hazard detection unit（Ch 19）當成一個新的 stall 來源：

```systemverilog
// 在 core 頂層，MEM 級接 dcache
assign mem_stall = dcache_stall;              // D-cache 沒好就 stall
assign pc_write     = !load_use_hazard && !mem_stall;
assign if_id_write  = !load_use_hazard && !mem_stall;
assign id_ex_write  = !mem_stall;             // MEM stall 要多凍這幾級
assign ex_mem_write = !mem_stall;
```

**優先序**：D-cache miss stall 和 load-use stall 可能同時發生，處理原則是「哪個在後面的級（更接近完成）優先」——MEM 級的 miss 得先解決（它擋著後面的指令流出），所以 `mem_stall` 通常蓋過 `load_use_hazard`。這種多 stall 來源的仲裁就是 Ch 19 hazard detection unit 的工作，這裡先知道 D-cache miss 是它要納入的一個新輸入。

## 核心概念：write buffer——藏住 write-through 的延遲

write-through 每次 store 都碰下層記憶體，若下層慢（DRAM ~100 拍），store 就變成每次卡 100 拍——這比 write-back 慘很多。救星是 **write buffer（寫緩衝）**。

概念：cache 和下層記憶體之間插一個小 FIFO。store 時把「位址+資料」丟進 buffer 就**當作完成**（CPU 不必等下層真的寫完），buffer 在背景慢慢把資料排空到記憶體。

```
   CPU ─store─→ [ cache ] ─write-through─→ [ write buffer (FIFO) ] ─背景排空─→ DRAM
                                                    │
                            store 丟進 buffer 就算完成，CPU 繼續跑
```

好處：把 write-through 的「每次寫都等下層」變成「丟進 buffer 就走」，藏住下層延遲。要處理的細節：
- **buffer 滿了**：FIFO 塞滿時 store 得等（stall），但正常情況 store 沒那麼密集，buffer 幾格就夠。
- **load 撞上 buffer 裡還沒排空的資料**：load 一個位址時，若那個位址的最新值還在 write buffer 裡（還沒寫進記憶體），load 必須從 buffer 拿（或等 buffer 排空），否則讀到記憶體的舊值。這叫 store-to-load forwarding（記憶體層面的 forwarding），要比對 load 位址和 buffer 裡的位址。

本章的 `dcache` 為教學簡單，把 write-through 建模成一拍完成（`WRITE_THRU: if (mem_valid) state <= IDLE`），沒做 write buffer。真實 core 一定有 write buffer，否則 write-through 慢到不能用。這是本章模型和真實硬體的主要簡化，你要知道它在真實設計裡不可或缺。

## 對比取捨

| 決策 | 選項 | 好處 | 壞處 | 本章選 |
|---|---|---|---|---|
| 寫命中同步 | write-through | 記憶體永遠最新、無 dirty bit、替換不寫回 | 每次寫碰下層（需 write buffer 救） | ✓ |
| | write-back | 少很多下層寫、快 | 要 dirty bit、替換多一道寫、正本會舊 | |
| 寫 miss 配置 | no-write-allocate | 寫 miss 不 refill，邏輯簡 | 寫後馬上讀同 line 會 miss | ✓ |
| | write-allocate | 寫 miss 也把 line 拉進來，之後讀命中 | 每次寫 miss 多一次 refill | |
| 下層延遲隱藏 | write buffer | 藏住 write-through 延遲 | 要處理滿 / store-to-load forwarding | （真實必備，本章略） |

真實高效能 core 多半用 **write-back + write-allocate**（省下層存取），配合 coherence 協定處理「正本會舊」的多核問題。教學/嵌入式小 core 常用 **write-through + no-write-allocate + write buffer**（邏輯簡單、單核夠用）。沒有絕對優劣，看你要省硬體還是省頻寬、單核還是多核。

## 踩雷區

**雷 1：store 命中卻忘了更新 cache 副本。**
- 錯誤直覺：「write-through 就是寫記憶體嘛，寫下層就好」。
- 正確認識：write-through 命中時**同時**要寫記憶體**和** cache 裡的副本。若只寫記憶體、不更新副本，那條 line 的 cache 副本就變舊了——下次 load 同位址命中（valid 還在、tag 還對），讀到的是 cache 裡的舊值，不是你剛寫的新值。本章 `if (hit) data_arr[index][word_off] <= req_wdata` 就是為此。一致性靠這一行。

**雷 2：把 no-write-allocate 誤做成「store miss 也 refill」。**
- 錯誤直覺：「store miss 了，先把 line 搬進來再寫比較完整」。
- 正確認識：那是 write-allocate，不是本章選的 no-write-allocate。no-write-allocate 的定義就是 store miss **不**搬 line 進 cache，直接寫穿下層。兩者不是對錯而是取捨：對「寫完不會馬上讀」的模式（例如初始化一大塊記憶體），no-write-allocate 省掉一堆沒必要的 refill；對「寫完馬上讀」的模式，write-allocate 較好。搞混兩者，你的 cache 行為和你以為的不一樣（例如以為 store 後 load 會命中，結果 no-write-allocate 下是 miss——範例一 load 0x40 花 5 拍正是這樣）。

**雷 3：D-cache miss 只凍 IF/ID（照抄 load-use 的 stall）。**
- 錯誤直覺：「stall 就是凍 PC 和 IF/ID，Ch 17 那套照搬」。
- 正確認識：load-use hazard 點在 ID 級，凍 PC/IF/ID 就夠。D-cache miss 的 hazard 點在 **MEM 級**，要把 MEM 之前的**所有**級（PC/IF/ID/ID-EX/EX-MEM）都凍住，只讓 WB 級流出。若只凍 IF/ID，中間的 ID/EX、EX/MEM 還在往前推，miss 這條指令後面的指令會覆蓋掉正在等待的狀態，pipeline 錯亂。stall 要凍到哪一級，取決於 hazard 發生在哪一級——不能無腦照抄。

**雷 4：用會殘留的暫存器脈衝表達「操作完成」。**
- 錯誤直覺：「refill/write-through 完成後，下一拍拉個 done 暫存器當 resp_ready 就好」。
- 正確認識：這正是本章實作時踩過的坑。若 `done` 是「完成後下一拍才拉高」的暫存器，它的高電位會延續到下一個存取的第一拍——如果那個新存取本該 miss，卻因為殘留的 `done=1` 被誤判成「已完成」，回了錯資料（實測是 store 完成的 done 害緊接的 load 拿到 0）。正解是用**組合邏輯在完成當拍**表達（`state==WRITE_THRU && mem_valid`），不留脈衝到下一拍。FSM 的完成訊號要精確界定在「哪一拍有效」，多一拍少一拍都是 bug。

## 進階延伸

- **write-back 的 dirty bit 與 victim 寫回**：把本章改成 write-back：每條 line 加一個 `dirty` bit，store 命中只寫 cache 副本並設 dirty（不碰下層）；某條 line 要被替換（在 set-associative 下）或被別的位址佔用時，若它 dirty，先把它整條寫回下層（victim writeback）再載新的。這省掉大量下層寫，但多了 dirty 維護和替換時的寫回狀態。是效能 core 的標配，練習後可自行改造。
- **cache coherence（多核的巨獸，本章淺提留給進階）**：一旦有多顆核，每顆核的 L1 可能各存一份同位址的副本。核 A 改了自己的副本，核 B 的副本就舊了。維持「所有核看到一致的記憶體」要靠 coherence 協定——MSI/MESI/MOESI 這類狀態機，每條 line 標記 Modified/Exclusive/Shared/Invalid，核之間用 bus snooping 或 directory 互相通知「我要寫了，你們的副本作廢」。這是《A Primer on Memory Consistency and Cache Coherence》整本書的主題，單核（本課主線）完全用不到，但你想做多核就是繞不開的第一道大關。write-through 讓正本永遠最新，稍微簡化 coherence，但不能免除它。
- **non-blocking cache 與 MSHR**：本章的 cache 是 blocking——一次 miss 整個 cache 卡住直到 refill 完。真實高效能 L1 是 non-blocking：miss 時把這筆 miss 記在一個叫 MSHR（Miss Status Holding Register）的結構裡，讓後面**命中**的存取繼續進行（甚至允許多筆未完成的 miss）。這對 out-of-order core（Part 6）尤其重要——OoO 就是要在 miss 等待期間讓別的指令跑。rocket-chip 的 `HellaCache` 就是 non-blocking 的。
- **store-to-load forwarding 與 memory disambiguation**：write buffer 那段提到 load 撞上 buffer 裡未排空的資料要轉發。在 OoO core 裡這更複雜——load 和 store 可能亂序，硬體要判斷一個 load 是否和之前某個還沒完成的 store 撞位址（memory disambiguation），撞了要轉發或等待，沒撞才能提前執行。這是 load/store queue 的核心工作，也是 memory model（記憶體一致性模型）在硬體層的體現，連回 Ch 25 提的《A Primer》。

## 本章重點整理

- **D-cache 的複雜度全在寫**：write policy（write-through/write-back）、write-miss policy（no/write-allocate）、寫讀一致性——I-cache 都沒有。
- **本章選 write-through + no-write-allocate**：邏輯最單純（記憶體永遠最新、store miss 不 refill），代價是每次 store 碰下層（靠 write buffer 救）。
- **三條路徑**：load hit（1 拍）、load miss（REFILL 多拍）、store（WRITE_THRU 寫穿，命中順便更新副本）。store 命中更新副本是一致性關鍵。
- **真跑驗證**：store 100 寫穿記憶體 → load 回來 5 拍 refill 讀到 100；同 block 鄰居 load 1 拍命中；store 0xDEAD → load 讀回 0xDEAD。全對，write-through 一致性成立。
- **miss stall 接回 Ch 17**：D-cache miss 在 MEM 級，要凍 PC 到 EX/MEM 全部（比 load-use 多凍幾級），WB 照常走。多 stall 來源由 Ch 19 hazard unit 仲裁。
- **write buffer** 藏住 write-through 延遲（store 丟進 FIFO 就算完成），真實 core 必備，本章模型簡化為一拍。

## 自我檢核

- [ ] 我能說出 D-cache 比 I-cache 多出的三個設計決策（write policy、write-miss policy、寫讀一致性）。
- [ ] 我能解釋 write-through vs write-back、no-write-allocate vs write-allocate 各自的取捨。
- [ ] 我能追出範例一「store 0x40 → load 0x40 花 5 拍讀到 100」的完整流程，說明為什麼 load 是 miss（no-write-allocate）而值還是對（write-through）。
- [ ] 我能解釋為什麼 store 命中時要同時更新 cache 副本，少了會怎樣。
- [ ] 我能說出 D-cache miss stall 要凍到哪一級、為什麼比 load-use stall 多凍幾級。
- [ ] 我能說明 write buffer 怎麼藏住 write-through 延遲，以及它要處理的兩個問題（滿、store-to-load forwarding）。

## 延伸閱讀

- **《Computer Organization and Design, RISC-V Edition》(Patterson & Hennessy) 第 5.3~5.4 節 write policy 部分 + 5.8「A Common Framework for Memory Hierarchy」**：本章寫策略的教科書版本。它把 write-through/write-back、write-allocate/no-write-allocate 的四種組合與各自時機講清楚，5.8 給出統一的「Q1~Q4」框架（放哪、怎麼找、替換誰、寫怎麼辦）串起 cache 的所有設計維度。讀它把本章的取捨表放進一個系統框架。
- **《Digital Design and Computer Architecture, RISC-V Edition》(Harris & Harris) 第 8.3.4「Write Policy」與 8.4「Virtual Memory」前半**：從 HDL 角度講 write-through/write-back 怎麼加 dirty bit、怎麼接 write buffer 的電路，和本章的 `dcache` 狀態機對得上，補足「寫路徑在矽裡怎麼接」。
- **《A Primer on Memory Consistency and Cache Coherence》(Nagarajan, Sorin, Hill, Wood) 第 6~8 章（Coherence Protocols）**：本章淺提的 coherence 巨獸的正式教材。第 6 章先把 coherence 的抽象定義講清楚，第 7~8 章逐步建 MSI→MESI 狀態機。你單核做完想跨多核，這是 write policy 之後的下一本必讀。
- **[rocket-chip 的 `HellaCache` / `DCache`](https://github.com/chipsalliance/rocket-chip/tree/master/src/main/scala/rocket)**：工業級 non-blocking write-back L1 D-cache 的真實原始碼（Chisel）。搜 `MSHR`、`dirty`、`WritebackUnit`，對照本章的 blocking write-through 教學版，你會具體看到「真實 D-cache 為了效能多做了多少事」——non-blocking、write-back、多筆未完成 miss、victim writeback，全在裡面。

下一章我們離開 cache，進入虛擬記憶體：為什麼要 VM（隔離、relocation）、Sv32 兩層 page table 怎麼把虛擬位址翻成實體位址，並用 C 模型真跑一遍完整的 page table walk。

→ [Ch 28 虛擬記憶體與 MMU：Sv32 page walk](./28-virtual-memory-mmu-sv32.md)
