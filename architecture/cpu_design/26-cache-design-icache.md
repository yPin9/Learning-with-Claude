# Ch 26 — Cache 設計：direct-mapped / set-associative，實作 I-cache

> **目標**：把 Ch 25 的「cache」從概念變成能跑的 SystemVerilog。你會學會怎麼把一個 32-bit 位址切成 tag / index / block offset 三段、direct-mapped 與 set-associative 的差別、block size 與 replacement policy 的取捨，然後**親手做出一個 direct-mapped I-cache**，灌真實取指 trace 進去，真跑量到 hit rate——看到冷啟動的 compulsory miss 之後全部命中（96.88%），也看到 block size 變大命中率跳到 99.22%、working set 超過容量時掉到 75%。這是深挖章。
> **環境**：WSL + verilator 4.038。所有 hit rate 皆真跑量測。本章的 `dm_icache` 是 Ch 27 D-cache 的基礎，也是練習 D 的起點。

## 為什麼從 I-cache 開始

Ch 25 說 L1 通常拆成 I-cache 和 D-cache。我們先做 I-cache（指令 cache），因為它**單純**：指令流只讀不寫（程式不會改自己的指令，self-modifying code 是特例），沒有 write policy 的麻煩，可以先把「怎麼查一個 cache」這件核心事情弄乾淨，Ch 27 再把寫的複雜度加上去。

而且 I-cache 的存取模式對 cache 極友善：PC 通常 +4 連續前進（強 spatial locality），迴圈反覆執行同一段（強 temporal locality）。它是展示「locality → 高 hit rate」最漂亮的場景。

回想 Ch 7 的 instruction fetch：我們一直假設 `imem` 一拍回指令。現在把 `imem` 想成「慢的 DRAM」，在它前面插一個快的 I-cache。命中時一拍給 CPU，miss 時 stall pipeline（沿用 Ch 17 的 stall 機制）去 `imem` 搬一整塊回來。

## 先建立直覺：一排有編號的信箱

把 cache 想成公寓大廳一排信箱，每個信箱能放一份文件。你家住在某個很長的地址（32-bit），但信箱只有幾個。怎麼決定你的文件放哪個信箱？

**用地址的一部分當信箱號碼。** 假設有 64 個信箱，就取地址的某 6 個 bit（2^6=64）當信箱號。這叫 **index**。

但這樣會撞：很多不同地址算出同一個信箱號。所以每個信箱除了放文件，還要貼一張標籤寫「這份文件原本的完整地址是哪個」——這叫 **tag**。你來拿文件時，先算出信箱號（index）找到信箱，再核對標籤（tag）是不是你要的那個地址。對得上＝**hit**，對不上＝**miss**（信箱裡是別人的文件）。

```
   32-bit 位址： [ tag | index | block offset ]
                    │      │          │
                    │      │          └─ 這條 line 內的第幾個 byte（一次搬 line）
                    │      └─ 放哪個信箱（cache set）
                    └─ 標籤，核對是不是這個地址
```

- **block offset**：一條 line 有多大（例如 16 B），需要幾個 bit 定位 line 內的位置。16 B → 4 bit。
- **index**：有幾個信箱（set），需要幾個 bit。64 set → 6 bit。
- **tag**：剩下的高位，用來核對身分。32 − 6 − 4 = 22 bit。

這三段切法是所有 cache 的共同骨架。下面把它變成真的位元運算。

## 核心概念：位址怎麼切

以我們要做的 `dm_icache` 為例，參數 `CACHE_SIZE=1024` bytes、`BLOCK_SIZE=16` bytes：

```
NUM_BLOCKS    = CACHE_SIZE / BLOCK_SIZE = 1024 / 16 = 64 個 block
WORDS_PER_BLK = BLOCK_SIZE / 4          = 16 / 4    = 4 個 word/block
OFF_BITS      = log2(BLOCK_SIZE)        = log2(16)  = 4  bit（block offset）
IDX_BITS      = log2(NUM_BLOCKS)        = log2(64)  = 6  bit（index）
TAG_BITS      = 32 - IDX_BITS - OFF_BITS = 32-6-4   = 22 bit（tag）
```

位址 `0x80000014`（二進位低 12 bit：`0000 0001 0100`）怎麼切：

```
   位址 0x80000014
   bit: [31 ................ 10][9 ...... 4][3 .. 2][1 0]
        └───── tag (22) ───────┘└─index(6)─┘└word┘└byte┘
                                              off    (對齊)

   block offset = addr[3:0]  = 0x4   （line 內第 4 個 byte）
     └ word_off = addr[3:2]  = 1     （line 內第 1 個 word，因為一 word=4 B）
   index        = addr[9:4]  = 1     （放第 1 號 block）
   tag          = addr[31:10]        （核對用）
```

注意 block offset 內部又分「哪個 word（addr[3:2]）」和「word 內哪個 byte（addr[1:0]）」。取指是 word 對齊的，所以我們只在乎 `word_off = addr[3:2]`。在 SystemVerilog 裡用 part-select 一次切出來：

```systemverilog
assign index    = req_addr[OFF_BITS +: IDX_BITS];   // addr[9:4]，6 bit
assign tag      = req_addr[31 -: TAG_BITS];         // addr[31:10]，22 bit
assign word_off = req_addr[2 +: WORD_OFF_BITS];     // addr[3:2]，2 bit
```

`[OFF_BITS +: IDX_BITS]` 是 Verilog 的 indexed part-select：「從 bit `OFF_BITS` 開始，往上取 `IDX_BITS` 個」。用參數表示就不必寫死位元位置，改 cache 大小時自動跟著變。

## 核心概念：direct-mapped vs set-associative

**direct-mapped（直接映射）**：每個位址只能放**唯一一個**信箱（index 決定，沒得選）。查起來最快（算 index、比一個 tag、完）。缺點：兩個常用位址若 index 相同，就永遠互踢——這叫 **conflict miss**，即使 cache 沒滿也發生。

```
   direct-mapped：index → 唯一 block
   addr A (index=5) ──┐
                      ├─→ block[5]   ← A 和 B index 都是 5，
   addr B (index=5) ──┘                互相踢，雖然其他 block 空著
```

**set-associative（組相聯）**：每個 index 對應一「組（set）」有 N 個 way（信箱位），位址可以放進該組的任一個 way。N=2 叫 2-way、N=4 叫 4-way。查的時候同時比對該組 N 個 tag（平行）。好處：同 index 的多個位址能共存（只要不超過 N 個），大幅減少 conflict miss。代價：要 N 個 tag 比較器（更多硬體）、還要決定滿了踢誰（replacement policy）。

```
   2-way set-associative：index → 一組 2 個 way
   addr A (index=5) ─→ set[5] way0  ← A 放這
   addr B (index=5) ─→ set[5] way1  ← B 放這，不再互踢
```

**fully-associative（全相聯）**：極端情形，N = 全部 block，任何位址可放任何位置，沒有 conflict miss，但要比對全部 tag（極貴）。只有 TLB 這種很小的結構才這樣做（Ch 29）。

取捨的一句話：**associativity 愈高，conflict miss 愈少，但硬體愈貴、命中路徑愈長（要比更多 tag、選更多 way）。** direct-mapped 是 associativity=1 的特例。真實 L1 通常 4~8 way，是速度與命中率的平衡點。本章先做最單純的 direct-mapped，練習 D 會帶你改成 set-associative。

## 核心概念：write policy 與 replacement（先建立詞彙）

這兩個對 I-cache 用不到（只讀、direct-mapped 不用選替換誰），但你得先有詞彙，Ch 27 D-cache 會全用上：

**write policy（寫策略）**——寫命中時怎麼同步到下層：
- **write-through（寫穿）**：每次寫，cache 和下層記憶體**同時**更新。簡單、一致，但每次寫都要碰慢的下層（可用 write buffer 緩解，Ch 27）。
- **write-back（寫回）**：只寫 cache，標記這條 line 為 dirty，等它被替換出去時才一次寫回下層。少很多下層存取，但要維護 dirty bit、替換時要多一次寫。

**replacement policy（替換策略）**——set-associative 一組滿了要踢誰：
- **LRU（Least Recently Used）**：踢最久沒用的。最符合 temporal locality，但要記錄使用順序（2-way 只要 1 bit，way 多了成本上升）。
- **random**：隨機踢。硬體極簡，命中率通常只比 LRU 差一點點，很多真實設計用它或用 pseudo-LRU 近似。
- **FIFO**：踢最早進來的。實作簡單（一個環狀指標），但不看使用頻率，Ch 29 的 TLB 會用它。

direct-mapped 不需要 replacement（每個 index 只有一個位置，該踢就踢它，沒得選）——這也是它「簡單」的一部分。

## 底層機制：dm_icache 的完整實作

現在把上面全部組成一個能跑的 module。介面：CPU 側給位址要指令（`req_valid`/`req_addr`），命中當拍回 `resp_data` 並 `resp_ready`，miss 時拉 `stall` 讓 pipeline 等；下層記憶體側（`mem_*`）在 miss 時一拍要一個 word，收滿一整個 block 才回 IDLE。

```systemverilog
module dm_icache #(
    parameter int CACHE_SIZE = 1024,   // total data bytes
    parameter int BLOCK_SIZE = 16      // bytes per block
) (
    input  logic        clk, rst,
    // CPU 側
    input  logic        req_valid,
    input  logic [31:0] req_addr,
    output logic [31:0] resp_data,
    output logic        resp_ready,
    output logic        stall,
    // 下層記憶體側（一拍一 word）
    output logic        mem_req,
    output logic [31:0] mem_addr,
    input  logic [31:0] mem_data,
    input  logic        mem_valid,
    // 觀測計數器
    output logic [31:0] hit_count, miss_count
);
    localparam int NUM_BLOCKS    = CACHE_SIZE / BLOCK_SIZE;
    localparam int WORDS_PER_BLK = BLOCK_SIZE / 4;
    localparam int OFF_BITS      = $clog2(BLOCK_SIZE);
    localparam int IDX_BITS      = $clog2(NUM_BLOCKS);
    localparam int TAG_BITS      = 32 - IDX_BITS - OFF_BITS;
    localparam int WORD_OFF_BITS = $clog2(WORDS_PER_BLK);

    // 三個平行陣列：valid / tag / data。這就是「cache 的儲存」。
    logic                 valid_arr [NUM_BLOCKS];
    logic [TAG_BITS-1:0]  tag_arr   [NUM_BLOCKS];
    logic [31:0]          data_arr  [NUM_BLOCKS][WORDS_PER_BLK];

    // 位址切位
    logic [IDX_BITS-1:0]      index;
    logic [TAG_BITS-1:0]      tag;
    logic [WORD_OFF_BITS-1:0] word_off;
    assign index    = req_addr[OFF_BITS +: IDX_BITS];
    assign tag      = req_addr[31 -: TAG_BITS];
    assign word_off = req_addr[2 +: WORD_OFF_BITS];

    // 命中判定：這個 block valid 且 tag 對得上
    logic hit;
    assign hit = req_valid && valid_arr[index] && (tag_arr[index] == tag);
```

命中判定是 cache 的心臟，就三件事：這個 index 的 block **有效**（`valid_arr[index]`）、而且它的 **tag 對得上**（`tag_arr[index] == tag`）、而且 CPU 真的在要（`req_valid`）。三者皆真＝hit。

miss 時進 refill 狀態機，向下層一拍要一個 word，收滿 `WORDS_PER_BLK` 個才把整條 line 標成 valid：

```systemverilog
    typedef enum logic [1:0] {IDLE, REFILL} state_t;
    state_t state;
    logic [WORD_OFF_BITS:0] refill_cnt;
    logic [IDX_BITS-1:0]    refill_index;
    logic [TAG_BITS-1:0]    refill_tag;
    logic [31:0]            refill_base;

    assign stall      = (state == REFILL) || (req_valid && !hit && state == IDLE);
    assign resp_ready = hit;                        // 命中當拍就給
    assign resp_data  = data_arr[index][word_off];
    assign mem_req    = (state == REFILL);
    assign mem_addr   = refill_base + (refill_cnt << 2);  // 逐 word 要

    always_ff @(posedge clk) begin
        if (rst) begin
            state <= IDLE; refill_cnt <= 0; hit_count <= 0; miss_count <= 0;
            for (int i = 0; i < NUM_BLOCKS; i++) valid_arr[i] <= 1'b0;  // 冷啟動全 invalid
        end else begin
            case (state)
                IDLE: if (req_valid) begin
                    if (hit) hit_count <= hit_count + 1;
                    else begin                       // miss：記下要 refill 的 block，進 REFILL
                        miss_count   <= miss_count + 1;
                        state        <= REFILL;
                        refill_cnt   <= 0;
                        refill_index <= index;
                        refill_tag   <= tag;
                        refill_base  <= {req_addr[31:OFF_BITS], {OFF_BITS{1'b0}}}; // block 起始位址
                    end
                end
                REFILL: if (mem_valid) begin
                    data_arr[refill_index][refill_cnt] <= mem_data;
                    if (refill_cnt == WORDS_PER_BLK-1) begin  // 最後一個 word 到齊
                        valid_arr[refill_index] <= 1'b1;      // 現在整條 line 有效
                        tag_arr[refill_index]   <= refill_tag;
                        state <= IDLE; refill_cnt <= 0;
                    end else refill_cnt <= refill_cnt + 1;
                end
            endcase
        end
    end
endmodule
```

幾個設計要點：
- **reset 時全 invalid**：`valid_arr` 全清 0，代表冷啟動 cache 空的。第一次碰任何 block 必然 miss——這就是 compulsory miss。
- **refill 逐 word 搬**：`mem_addr = refill_base + refill_cnt*4`，一拍要一個 word，收 `WORDS_PER_BLK` 拍。`refill_base` 是把位址對齊到 block 邊界（低 `OFF_BITS` 位清零）。真實 cache 通常整條 burst 一次搬，這裡拆成逐 word 是為了教學清楚。
- **valid 最後才設**：整條 line 沒收齊之前 `valid` 保持 0，避免半條 line 被誤判命中。這是正確性關鍵。

## 範例一：迴圈取指，量到 compulsory miss 後全命中

我們用一個 testbench（C++）灌 access trace：一支 16-word 的迴圈（`0x80000000` 到 `0x8000003c`，共 64 B）跑 8 次。下層記憶體模型任何位址回 `addr ^ 0xC0DE`（值不重要，我們量的是 hit/miss）。tb 用「第一拍 `resp_ready` 有沒有起來」判定每個 access 是 hit 還是 miss。

trace = 8 圈 × 16 word = 128 次取指。cache 1 KiB、block 16 B（64 個 block，4 word/block）。真跑：

```
=== default: 8 loops x 16 words, cache 1KiB/16B block ===
accesses    = 128
hits        = 124
misses      = 4
hit rate    = 96.88%
total cycles= 148  (AMAT proxy = 1.16 cyc/access)
```

**hit rate = 96.88%，miss 恰好 4 次。** 為什麼是 4？64 B 的迴圈 = 4 個 block（每 block 16 B）。第一圈第一次碰每個 block 各 miss 一次（4 個 compulsory miss），把 4 條 line 搬進來。之後 7 圈全部命中（block 都在 cache 裡，且沒有別的位址來踢它們）。124/128 = 96.88%。

這就是 I-cache 對迴圈的漂亮表現：**冷啟動付一次 compulsory miss 的錢，之後迴圈跑幾千圈都命中。** temporal locality（迴圈重複）＋ spatial locality（一次搬 4 word 進來）雙雙兌現。`AMAT proxy = 1.16 cyc/access` 也印證：平均每次取指只花 1.16 拍，非常接近理想的 1 拍。

## 範例二：block size 變大，hit rate 跳升

同樣的迴圈 trace，只改 `BLOCK_SIZE`（用 verilator 的 `-GBLOCK_SIZE=` 覆寫參數），看 block size 對命中率的影響：

```
block=16B  hit rate    = 96.88%
block=64B  hit rate    = 99.22%
```

block 從 16 B 變 64 B，hit rate 從 96.88% 升到 99.22%。原因：block 愈大，一次搬進來的連續資料愈多（64 B = 16 個 word），冷啟動要搬的 block 數愈少（64 B 迴圈只需 1 個 64 B block，compulsory miss 從 4 降到 1）。**大 block 更能吃 spatial locality。**

但別以為 block 愈大愈好——這是雷 4 要講的取捨。大 block 的代價：(1) 一次 miss 要搬更多資料，miss penalty 變大；(2) 同樣 cache 容量下 block 數變少，index bit 變少，更容易 conflict；(3) 若程式空間局部性不強（亂跳），搬進來的大 block 大半用不到，純浪費頻寬。真實 L1 選 64 B 是長期經驗的甜蜜點。

## 範例三：working set 超過容量，掉進 capacity miss

前兩例迴圈很小（64 B），塞得進 1 KiB cache。現在把迴圈拉長，看 working set 超過 cache 容量會怎樣。cache 固定 1 KiB（64 個 16 B block），trace 改成「迴圈掃 N 個 word、跑 8 圈」：

```
cache=1KiB (64 blocks x 16B). loop over N words:
    16 words (   64 B footprint): hit rate    = 96.88%
    64 words (  256 B footprint): hit rate    = 96.88%
   256 words ( 1024 B footprint): hit rate    = 96.88%
   512 words ( 2048 B footprint): hit rate    = 75.00%
```

看最後一行的斷崖：footprint 從 1024 B（= cache 容量，剛好塞滿）到 2048 B（= 兩倍容量），hit rate 從 96.88% 掉到 75%。**working set 一旦超過 cache 容量，每圈都有 block 被後面的存取踢掉，下一圈回來又 miss——這就是 capacity miss。**

具體說：2048 B 的迴圈掃過時，前半段搬進來的 block 被後半段的存取（同 index、不同 tag）踢掉；等下一圈回到前半段，那些 block 已經不在了，重新 miss。footprint = 容量時（1024 B）剛好每個 block 各佔一格不互踢，還能維持 96.88%；一超過就開始互踢。

這正是 Ch 25 範例二那條延遲曲線的階梯在 RTL 層級的重現：**working set ≤ 容量 → 高命中；> 容量 → 命中率斷崖式下跌。** 解法是加大 cache（更多 block）或加關聯度（減少同 index 互踢），這是練習 D 要你動手驗證的。

## 對比取捨

| 設計選擇 | 選項 A | 選項 B | 取捨 |
|---|---|---|---|
| associativity | direct-mapped（本章） | set-associative | DM 查得快、硬體省，但 conflict miss 多；SA 反之 |
| block size | 小（16 B） | 大（64 B） | 大 block 吃 spatial locality（96.88%→99.22%），但 miss penalty 大、block 數少易 conflict |
| cache 容量 | 小（省面積） | 大（省 miss） | 大容量減 capacity miss，但面積/功耗/命中延遲都上升 |
| write policy | write-through | write-back | 見 Ch 27（I-cache 只讀用不到） |
| replacement | LRU | random/FIFO | 見 Ch 27/29（direct-mapped 用不到） |

一句話：**cache 設計沒有免費午餐，每個參數都在「命中率」和「面積/延遲/功耗/penalty」之間拉扯。** 你在範例二三看到的數字（block size 換 3% 命中率、容量換 22% 命中率）就是這些拉扯的實測值。

## 踩雷區

**雷 1：把 valid bit 忘了，或整條 line 沒收齊就設 valid。**
- 錯誤直覺：「tag 對上就算命中，valid 可有可無」。
- 正確認識：reset 後 cache 是空的，`tag_arr` 裡是垃圾值——如果不看 `valid`，垃圾 tag 可能碰巧「對上」某個位址，回一堆亂資料當指令，pipeline 直接跑飛。`valid` bit 就是「這格到底有沒有真資料」的旗標，命中判定**必須**是 `valid && tag_match`。而且 refill 途中（line 只收了一半）絕不能設 valid，否則會命中半條 line 的舊/未定資料。本章 `valid_arr[refill_index] <= 1'b1` 放在「最後一個 word 到齊」那一拍，就是為此。

**雷 2：tag / index / offset 的位元切錯。**
- 錯誤直覺：「index 用高位、tag 用低位」或隨手切。
- 正確認識：切法是固定的——**offset 在最低位（line 內定位）、index 在中間（選 set）、tag 在最高位（核對）**。為什麼 index 用中間位而非高位？因為連續的位址（同一段程式、同一個陣列）高位相同、中間位不同，用中間位當 index 才能把它們**分散**到不同 set，避免全擠在同一格。若拿高位當 index，一大段連續位址會全映到同一個 set，conflict miss 爆表。這個位元順序不能隨便換。

**雷 3：以為 direct-mapped 沒滿就不會 miss。**
- 錯誤直覺：「cache 有 64 個 block，我只用了 2 個位址，怎麼會一直 miss？」
- 正確認識：direct-mapped 下，兩個位址只要 **index 相同**（中間那幾個 bit 一樣）就映到同一格，互相踢——即使其他 63 個 block 全空著也沒用。這叫 conflict miss，是 direct-mapped 的先天病。範例三 footprint 超容量時的斷崖，一部分就是 conflict 造成。set-associative（練習 D）給每個 index 多幾個 way，就是為了治這個。

**雷 4：以為 block size 愈大愈好。**
- 錯誤直覺：「範例二 block 變大 hit rate 就升，那我開 4 KiB block 不就更高？」
- 正確認識：block 過大有三個反效果——(1) 每次 miss 要搬一整個大 block，**miss penalty 暴增**（搬 4 KiB 比搬 64 B 慢 64 倍）；(2) 同容量下 block 數變少，index bit 變少，**conflict miss 上升**；(3) 若程式空間局部性不強，大 block 搬進來大半用不到就被踢，**純浪費頻寬還污染 cache**。所以 hit rate 對 block size 是先升後降的曲線，甜蜜點通常 32~64 B。範例二只看到升的那半段，是因為我們的迴圈 trace 空間局部性極強（連續取指）；換個亂跳的 trace 就會看到大 block 反而更差。

## 進階延伸

- **改成 set-associative（練習 D 的挑戰）**：把 `valid_arr`/`tag_arr`/`data_arr` 各加一個 way 維度（`[NUM_SETS][WAYS]`），命中判定改成平行比對該 set 的所有 way 的 tag，再加一個 replacement policy（2-way 用 1 個 LRU bit 最簡單）。範例三那條斷崖會因為 conflict miss 減少而變緩。這是把本章 direct-mapped 升級的自然下一步。
- **VIPT / PIPT（cache 和虛擬記憶體的交互）**：真實 L1 用虛擬位址還是實體位址查？若用實體位址（PIPT），得先做 TLB 轉譯（Ch 29）才能查 cache，兩者串接變慢。折衷是 VIPT（Virtually Indexed, Physically Tagged）：用虛擬位址的低位（page offset，轉譯前後不變）當 index 平行查 cache，同時做 TLB 轉譯得到實體 tag 再比對。這要求 index 落在 page offset 內（限制了 cache 大小），是 Ch 28~29 之後才看得懂的設計約束。
- **cache 怎麼接進 pipeline 的 IF 級**：本章 `dm_icache` 是獨立 module，真接進 core 時，IF 級的 `imem` 存取換成對 I-cache 的 `req`，命中一拍拿到指令照常走，miss 時拉 `stall`（沿用 Ch 17 的 `pc_write=0`/`if_id_write=0` 凍結前端）等 refill。整合的細節（尤其和 branch flush 的優先序）Ch 27 會用 D-cache 完整示範一次，I-cache 同理。
- **多 word block 的 critical-word-first**：refill 一整個 block 要好幾拍，但 CPU 只等其中一個 word（它要取的那條指令）。最佳化叫 critical-word-first：先搬 CPU 要的那個 word 讓它先跑，其餘 word 在背景繼續填。本章為教學簡單是「收齊整條才 resp」，真實 core 會做 critical-word-first 把 miss penalty 的一部分藏起來。

## 本章重點整理

- **位址三段切**：`[tag | index | block offset]`。offset 定位 line 內位置、index 選哪個 set（用中間位以分散連續位址）、tag 核對身分。命中 = `valid && tag_match`。
- **associativity**：direct-mapped（1 way，查最快但 conflict 多）→ set-associative（N way，減 conflict 但硬體貴）→ fully-associative（無 conflict 但極貴，只用於 TLB）。
- **`dm_icache` 三陣列**：`valid_arr` / `tag_arr` / `data_arr` 就是 cache 的儲存；miss 進 REFILL 逐 word 搬一整個 block，收齊才設 valid。
- **真跑數字**：64 B 迴圈跑 8 圈 → 4 個 compulsory miss 後全命中，hit rate 96.88%；block 16→64 B，hit rate 96.88%→99.22%；working set 超過容量，斷崖跌到 75%（capacity miss）。
- **沒有免費午餐**：block size、associativity、容量每個參數都在「命中率」與「面積/延遲/penalty」之間拉扯，實測數字量化了這些取捨。

## 自我檢核

- [ ] 我能把一個給定位址（例如 `0x80000014`）在 1 KiB/16 B-block 的 cache 下切出 tag/index/word_off，並說出各幾個 bit。
- [ ] 我能解釋為什麼 index 用位址的中間位而非高位（分散連續位址、避免 conflict）。
- [ ] 我能說清楚命中判定為什麼一定要 `valid && tag_match`，少了 valid 會怎樣。
- [ ] 我能區分 direct-mapped / set-associative / fully-associative，並說出各自的取捨。
- [ ] 我能解釋範例一為什麼恰好 4 次 miss（compulsory）、範例三為什麼 footprint 超容量就斷崖（capacity/conflict）。
- [ ] 我能反駁「block size 愈大愈好」，說出大 block 的三個反效果。

## 延伸閱讀

- **《Computer Organization and Design, RISC-V Edition》(Patterson & Hennessy) 第 5.3~5.4 節「Measuring and Improving Cache Performance / Associativity」**：本章的教科書版本。5.4 把 direct-mapped 到 set-associative 的演進、tag/index 切法、associativity 對 miss rate 的影響（含經典的 miss rate vs associativity 曲線）講得最清楚。讀它把本章三個實驗的數字對回教科書的定性結論。
- **《Digital Design and Computer Architecture, RISC-V Edition》(Harris & Harris) 第 8.3 節「Caches」**：從 HDL/硬體角度講 cache 的 tag array、比較器、valid bit 怎麼接成電路，和本章的 `valid_arr`/`tag_arr`/命中判定一一對應。補足「這些陣列在矽裡長什麼樣」這一層。
- **Hennessy & Patterson《Computer Architecture: A Quantitative Approach》Appendix B「Review of Memory Hierarchy」**：3C 模型（compulsory/capacity/conflict miss）的權威出處，把本章範例一（compulsory）、範例三（capacity/conflict）背後的分類講死。想精確判斷「該加大 cache 還是加關聯度」，就看它怎麼用 3C 拆解 miss 來源。
- **[picorv32 的記憶體介面](https://github.com/YosysHQ/picorv32) 與 [SiFive rocket-chip 的 `HellaCache`](https://github.com/chipsalliance/rocket-chip)**：兩個對照。picorv32 極簡（幾乎不帶 cache，看「不做 cache 的小 core 長怎樣」），rocket-chip 的 `HellaCache` 是工業級 non-blocking L1（帶 MSHR、支援多筆未完成 miss）。讀完本章的 blocking direct-mapped，去看 rocket 的 non-blocking 設計，你會懂本章 refill 時整個 cache 卡住（blocking）是最簡單但也最限制效能的做法。

下一章我們把 cache 加上「寫」的能力做成 D-cache，處理 write-through/write-back、miss 時怎麼 stall pipeline 的 MEM 級，並真跑一遍 load/store 經過 cache 的完整流程。

→ [Ch 27 D-cache + 與 pipeline 整合、miss stall](./27-dcache-pipeline-integration.md)
