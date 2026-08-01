# 練習 D — 實作 direct-mapped I-cache，量 hit rate

> **目標**：這是 Part 4 的動手大關。你要**從規格開始**做出一個參數化的 direct-mapped I-cache，寫 testbench 灌 access trace 量 hit/miss、算 hit rate，然後系統性地掃 block size 和 cache size 看它們怎麼影響命中率——用真跑的數字重現 Ch 26 講的 compulsory / capacity / conflict miss。最後挑戰把它改成 2-way set-associative，親眼看 conflict miss 減少。全程 verilator 真跑，每個實驗都要貼你自己量到的數字。
> **環境**：WSL + verilator 4.038。預估 3~5 小時（含改 set-associative 挑戰）。做完你會對「cache 參數怎麼換命中率」有肌肉記憶，這是效能工程和微架構設計的共同底層。
> **前置**：Ch 25（locality/AMAT）、Ch 26（cache 設計、位址切法）。建議先讀完再動手。

## 為什麼做這個練習

Ch 26 我給了你一個做好的 `dm_icache` 和一堆 hit rate 數字。但**看別人跑出的數字**和**自己切位、自己接狀態機、自己灌 trace 量出斷崖**是兩回事。這個練習逼你：

1. 親手把 32-bit 位址切成 tag/index/offset——切錯了 hit rate 會很怪，你得回去對位元。
2. 親手接 miss → refill 狀態機——少設一個 valid、refill 沒收齊就命中，你會看到讀出亂資料。
3. 親手掃參數——你會發現「block 變大命中率升」有極限、「working set 超容量斷崖」是真的、「加關聯度治 conflict」有效。

這些只有動手才進得了腦子。做完你對 cache 的理解會從「聽過」變成「摸過」。

## 規格

實作一個 module `dm_icache`，參數化 cache 大小與 block 大小，介面如下：

### 參數

| 參數 | 意義 | 預設 | 約束 |
|---|---|---|---|
| `CACHE_SIZE` | cache 總資料容量（bytes） | 1024 | 2 的次方 |
| `BLOCK_SIZE` | 每個 block 大小（bytes） | 16 | 2 的次方，≥ 8（至少 2 word） |

由參數推出的 localparam（你要自己算並宣告）：`NUM_BLOCKS`、`WORDS_PER_BLK`、`OFF_BITS`、`IDX_BITS`、`TAG_BITS`、`WORD_OFF_BITS`。

### 介面

```systemverilog
module dm_icache #(
    parameter int CACHE_SIZE = 1024,
    parameter int BLOCK_SIZE = 16
) (
    input  logic        clk, rst,
    // CPU 側
    input  logic        req_valid,   // CPU 發出取指
    input  logic [31:0] req_addr,    // byte address
    output logic [31:0] resp_data,   // 命中/refill 後回的 word
    output logic        resp_ready,  // 這拍 resp_data 有效（命中）
    output logic        stall,       // miss 中，要 CPU 等
    // 下層記憶體側（一次一 word）
    output logic        mem_req,     // 向記憶體要資料
    output logic [31:0] mem_addr,    // 要的 word 位址
    input  logic [31:0] mem_data,    // 記憶體回的 word
    input  logic        mem_valid,   // mem_data 有效
    // 觀測計數器
    output logic [31:0] hit_count, miss_count
);
```

### 行為要求

1. **命中判定**：`hit = req_valid && valid_arr[index] && (tag_arr[index] == tag)`。命中當拍 `resp_ready=1`、`resp_data` 給出該 word。
2. **miss**：進 REFILL 狀態，向下層一拍要一個 word（`mem_addr` 從 block 起始位址逐 word 遞增），收滿 `WORDS_PER_BLK` 個才把該 block 的 `valid` 設 1、`tag` 存好。REFILL 期間 `stall=1`。
3. **reset**：所有 `valid_arr` 清 0（冷啟動空 cache）。
4. **計數器**：`hit_count`/`miss_count` 供 tb 讀取（或你在 tb 端自己數，見下）。

> **Verilator 4.038 的 reset 坑**：你可能會直覺寫 `if (rst) for (i...) valid_arr[i] <= 0;`。Verilator 4.038 **不支援 always_ff 迴圈內對陣列做 non-blocking 賦值**（會噴 `BLKLOOPINIT` error）。小 cache（≤64 blocks）它可能放行，大 cache 就爆。穩健做法：加一個 `CLEAR` 狀態，reset 後**逐格清 valid**（一拍清一個 index），清完 `NUM_BLOCKS` 拍才進 IDLE。tb 在 reset 後要等 cache 清完（`stall` 拉低）再開始灌 trace。卡點提示會給範例。

## 測試計畫

你要用同一個 tb（參數化 trace）跑三組實驗，每組貼你量到的數字：

- **實驗 1（baseline）**：cache 1 KiB、block 16 B，灌「16-word 迴圈跑 8 圈」的 trace（64 B footprint）。預期：4 個 compulsory miss 後全命中。
- **實驗 2（block size 掃描）**：固定 cache、固定 trace，block 掃 16/32/64 B，看 hit rate 怎麼變。
- **實驗 3（capacity 斷崖）**：固定 cache 1 KiB，trace 的迴圈長度掃 16/64/256/512 word（footprint 64 B ~ 2 KiB），看 working set 超過容量時 hit rate 斷崖。

## 分段實作

### 第 1 步：位址切位與參數

先把 localparam 算出來、位址切好。這步最容易錯，先單獨驗證。

<details>
<summary>卡點提示：localparam 怎麼算</summary>

```systemverilog
localparam int NUM_BLOCKS    = CACHE_SIZE / BLOCK_SIZE;
localparam int WORDS_PER_BLK = BLOCK_SIZE / 4;
localparam int OFF_BITS      = $clog2(BLOCK_SIZE);    // block offset bits
localparam int IDX_BITS      = $clog2(NUM_BLOCKS);    // index bits
localparam int TAG_BITS      = 32 - IDX_BITS - OFF_BITS;
localparam int WORD_OFF_BITS = $clog2(WORDS_PER_BLK);
```
位址切位用 indexed part-select（不寫死位元位置）：
```systemverilog
assign index    = req_addr[OFF_BITS +: IDX_BITS];   // 中間段選 block
assign tag      = req_addr[31 -: TAG_BITS];          // 高位核對
assign word_off = req_addr[2 +: WORD_OFF_BITS];      // block 內第幾個 word
```
自己驗證：`CACHE_SIZE=1024, BLOCK_SIZE=16` 時，`NUM_BLOCKS=64, OFF_BITS=4, IDX_BITS=6, TAG_BITS=22, WORD_OFF_BITS=2`。位址 `0x80000014` 應切出 index=1、word_off=1。
</details>

### 第 2 步：儲存陣列與命中判定

宣告 `valid_arr`、`tag_arr`、`data_arr` 三個陣列，寫命中判定的組合邏輯。

<details>
<summary>卡點提示：三個陣列與 hit</summary>

```systemverilog
logic                 valid_arr [NUM_BLOCKS];
logic [TAG_BITS-1:0]  tag_arr   [NUM_BLOCKS];
logic [31:0]          data_arr  [NUM_BLOCKS][WORDS_PER_BLK];

logic hit;
assign hit = req_valid && valid_arr[index] && (tag_arr[index] == tag);
assign resp_ready = (state == IDLE) && hit;
assign resp_data  = data_arr[index][word_off];
```
記住命中三要素缺一不可：`req_valid`（真的在要）、`valid_arr[index]`（這格有真資料）、`tag_arr[index]==tag`（是你要的位址）。少了 valid，reset 後的垃圾 tag 可能碰巧命中回亂資料。
</details>

### 第 3 步：miss → refill 狀態機（含 CLEAR reset）

接三態機：`CLEAR`（reset 後逐格清 valid）→ `IDLE`（命中/發現 miss）→ `REFILL`（搬 block）。

<details>
<summary>卡點提示：狀態機骨架</summary>

```systemverilog
typedef enum logic [1:0] {CLEAR, IDLE, REFILL} state_t;
state_t state;
logic [WORD_OFF_BITS:0] refill_cnt;
logic [IDX_BITS-1:0]    refill_index;
logic [TAG_BITS-1:0]    refill_tag;
logic [31:0]            refill_base;
logic [IDX_BITS:0]      clr_idx;

assign stall    = (state != IDLE) || (req_valid && !hit && state == IDLE);
assign mem_req  = (state == REFILL);
assign mem_addr = refill_base + (refill_cnt << 2);

always_ff @(posedge clk) begin
    if (rst) begin
        state <= CLEAR; refill_cnt <= 0; clr_idx <= 0;
        hit_count <= 0; miss_count <= 0;
    end else case (state)
        CLEAR: begin                                   // 逐格清 valid
            valid_arr[clr_idx[IDX_BITS-1:0]] <= 1'b0;
            if (clr_idx == NUM_BLOCKS-1) state <= IDLE;
            else clr_idx <= clr_idx + 1;
        end
        IDLE: if (req_valid) begin
            if (hit) hit_count <= hit_count + 1;
            else begin
                miss_count   <= miss_count + 1;
                state        <= REFILL; refill_cnt <= 0;
                refill_index <= index; refill_tag <= tag;
                refill_base  <= {req_addr[31:OFF_BITS], {OFF_BITS{1'b0}}};
            end
        end
        REFILL: if (mem_valid) begin
            data_arr[refill_index][refill_cnt] <= mem_data;
            if (refill_cnt == WORDS_PER_BLK-1) begin
                valid_arr[refill_index] <= 1'b1;       // 收齊才設 valid！
                tag_arr[refill_index]   <= refill_tag;
                state <= IDLE; refill_cnt <= 0;
            end else refill_cnt <= refill_cnt + 1;
        end
        default: state <= IDLE;
    endcase
end
```
關鍵：`valid_arr[refill_index] <= 1'b1` 一定放在「最後一個 word 到齊」那拍，不能提早——否則半條 line 會被誤判命中。
</details>

### 第 4 步：testbench

寫 C++ tb：reset → 等 cache 清完 → 灌 trace → 每個 access 判定 hit/miss → 印 hit rate。下層記憶體回 `addr ^ 0xC0DE`（值不重要，量的是 hit/miss）。

<details>
<summary>卡點提示：tb 骨架（access 分類 + 等 CLEAR）</summary>

```cpp
#include "Vdm_icache.h"
#include "verilated.h"
#include <cstdio>
#include <vector>
static Vdm_icache* dut;
static void tick(){ dut->clk=0; dut->eval(); dut->clk=1; dut->eval(); }
static long tb_hits=0, tb_misses=0;

static int access(uint32_t addr) {
    dut->req_valid = 1; dut->req_addr = addr;
    dut->eval();
    if (dut->resp_ready) tb_hits++; else tb_misses++;   // 第一拍分類
    int c=0;
    while (!dut->resp_ready) {
        if (dut->mem_req) { dut->mem_data = dut->mem_addr ^ 0xC0DE; dut->mem_valid = 1; }
        else dut->mem_valid = 0;
        tick(); c++; dut->eval();
    }
    dut->mem_valid = 0; tick(); c++;
    dut->req_valid = 0; dut->eval(); tick();
    return c;
}

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    dut = new Vdm_icache;
    dut->rst=1; dut->req_valid=0; dut->mem_valid=0;
    tick(); tick(); dut->rst=0;
    dut->eval();
    while (dut->stall) { tick(); dut->eval(); }          // 等 CLEAR 清完

    int loops  = (argc>1) ? atoi(argv[1]) : 8;
    int nwords = (argc>2) ? atoi(argv[2]) : 16;
    std::vector<uint32_t> trace;
    for (int l=0;l<loops;l++) for (int w=0;w<nwords;w++)
        trace.push_back(0x80000000u + w*4);

    for (uint32_t a : trace) access(a);
    printf("accesses=%zu hits=%ld misses=%ld hit rate=%.2f%%\n",
           trace.size(), tb_hits, tb_misses, 100.0*tb_hits/trace.size());
    return 0;
}
```
verilator 覆寫參數用 `-GBLOCK_SIZE=32 -GCACHE_SIZE=2048`，不必改 SV 原始碼就能掃。
</details>

### 第 5 步：跑三組實驗，貼數字

build 與掃描：

```bash
# baseline
verilator --cc dm_icache.sv --exe tb.cpp --Mdir obj -Wno-fatal
make -C obj -f Vdm_icache.mk Vdm_icache && ./obj/Vdm_icache 8 16

# block size 掃描（改 -GBLOCK_SIZE，重新 verilate）
for BS in 16 32 64; do
  verilator --cc dm_icache.sv --exe tb.cpp --Mdir obj_$BS -GBLOCK_SIZE=$BS -Wno-fatal
  make -s -C obj_$BS -f Vdm_icache.mk Vdm_icache && ./obj_$BS/Vdm_icache 8 16
done

# capacity 斷崖（改 trace 長度）
for NW in 16 64 256 512; do ./obj/Vdm_icache 8 $NW; done
```

## 參考數字（你該量到接近這些）

以下是參考實作真跑的結果，你的實作應該量到一樣或很接近的數字：

**實驗 1（baseline，cache 1 KiB、block 16 B、16-word 迴圈 × 8 圈）：**
```
accesses=128 hits=124 misses=4 hit rate=96.88%
```
4 個 compulsory miss（64 B 迴圈 = 4 個 block，各冷啟動 miss 一次），之後全命中。

**實驗 2（block size 掃描，同 64 B 迴圈）：**
```
block=16B  hit rate=96.88%
block=32B  hit rate=98.44%
block=64B  hit rate=99.22%
```
block 愈大，冷啟動要搬的 block 數愈少（64 B 迴圈：16B block 需 4 個、64B block 只需 1 個），compulsory miss 少，hit rate 升。

**實驗 3（capacity 斷崖，cache 1 KiB block 16B，迴圈長度掃描）：**
```
  16 words (  64 B footprint): hit rate=96.88%
  64 words ( 256 B footprint): hit rate=96.88%
 256 words (1024 B footprint): hit rate=96.88%   ← footprint = 容量，剛好塞下
 512 words (2048 B footprint): hit rate=75.00%   ← 超過容量，斷崖！
```
footprint ≤ 容量（1024 B）時維持 96.88%；一超過（2048 B）就每圈互踢、下圈回來 miss，掉到 75%。

**額外驗證（cache size 治 capacity）**：把實驗 3 的 512-word 迴圈（2 KiB footprint）固定，掃 cache 大小：
```
cache= 512B  hit rate=75.00%
cache=1024B  hit rate=75.00%
cache=2048B  hit rate=96.88%   ← 容量追上 footprint，斷崖消失
cache=4096B  hit rate=96.88%
```
加大 cache 到容納整個 working set，capacity miss 就消失——這證明實驗 3 的斷崖確實是容量問題。

若你的數字對不上，回去查：位址切位對不對（index 用中間位）、valid 是不是收齊才設、tb 的 access 分類是不是看第一拍 `resp_ready`。

## 卡點提示（常見錯誤）

<details>
<summary>hit rate 100% 或異常高</summary>

多半是 valid 判定或 tb 計數錯。檢查：(1) 命中判定有沒有帶 `valid_arr[index]`？(2) tb 的 hit/miss 是不是在**第一拍 eval 後**分類（miss 的 access 之後會變 ready，別把它也算成 hit）？(3) reset 後 valid 有沒有真的清 0（CLEAR 有沒有跑完）？
</details>

<details>
<summary>hit rate 很低（該命中的沒命中）</summary>

多半是位址切錯或 refill 沒設好。檢查：(1) index 是不是用位址**中間**位（`addr[OFF_BITS +: IDX_BITS]`）而非高位？用高位會讓連續位址全擠一格，狂 conflict。(2) refill 完 `valid_arr[refill_index]` 有沒有設 1？沒設的話下次還是 miss。(3) `refill_base` 有沒有對齊 block 邊界（低 `OFF_BITS` 位清零）？
</details>

<details>
<summary>模擬卡死 / timeout</summary>

多半是 tb 等 `resp_ready` 但它永遠不來。檢查：(1) miss 時 tb 有沒有正確餵 `mem_data` + `mem_valid`（`if (mem_req)` 那段）？(2) REFILL 有沒有正常收滿回 IDLE？(3) CLEAR 有沒有卡住（`clr_idx` 遞增到 `NUM_BLOCKS-1` 有沒有轉 IDLE）？tb 迴圈加個 `if (c>1000) exit` 的 timeout 保護，避免真的無限跑。
</details>

<details>
<summary>大 cache verilate 失敗（BLKLOOPINIT error）</summary>

你在 always_ff 裡用了 `for` 迴圈對 `valid_arr` 做 `<=`。Verilator 4.038 不支援。改用 CLEAR 狀態逐格清（見第 3 步提示）。這是這個工具版本的限制，不是你的邏輯錯。
</details>

## 延伸挑戰：改成 2-way set-associative

做完 direct-mapped，最有價值的升級是改成 **2-way set-associative**——親眼看 conflict miss 減少。改動：

1. **儲存加 way 維度**：`valid_arr[NUM_SETS][2]`、`tag_arr[NUM_SETS][2]`、`data_arr[NUM_SETS][2][WORDS_PER_BLK]`。`NUM_SETS = NUM_BLOCKS / 2`，index 少 1 bit、tag 多 1 bit。
2. **命中判定平行比對兩個 way**：`hit = valid[set][0]&&tag[set][0]==tag || valid[set][1]&&tag[set][1]==tag`，記下命中哪個 way 好取資料。
3. **replacement policy**：一組滿了要踢誰。2-way 最省的是 1 個 LRU bit/set（記哪個 way 最近較少用），或先做 random/FIFO 也行。
4. **refill 時選 way**：優先填無效的 way，兩個都有效才按 replacement 踢一個。

**驗證怎麼看出差別**：設計一個**故意 conflict** 的 trace——兩個 index 相同、tag 不同的位址交替存取（direct-mapped 下它們互踢、每次 miss；2-way 下它們各佔一個 way、共存命中）。例如位址 `0x80000000` 和 `0x80000400`（若 index 相同）交替。量 direct-mapped 的 hit rate（應該很低，狂 conflict）對比 2-way（應該高很多，conflict 消失）。這個對比就是 set-associative 存在的全部理由，你會用自己的數字證明它。

<details>
<summary>卡點提示：怎麼構造 conflict trace</summary>

要讓兩個位址 index 相同、tag 不同：它們的 index 位段（`addr[OFF_BITS +: IDX_BITS]`）要一樣，但高位 tag 不同。以 cache 1 KiB、block 16 B（index=addr[9:4]、tag=addr[31:10]）為例，`0x80000000` 和 `0x80000400`：低 10 bit 都是 0（index=0），但 bit 10 不同（tag 差 1）——正好 index 撞、tag 不同。交替存取這兩個，direct-mapped 每次互踢（hit rate 趨近 0），2-way 各佔一 way（refill 一次後全命中）。這是 conflict miss 最乾淨的示範。
</details>

## 完成檢核

- [ ] `dm_icache` 通過 verilate + 三組實驗，baseline hit rate ≈ 96.88%。
- [ ] 我量到 block 16→32→64 B 時 hit rate 96.88%→98.44%→99.22%，並能解釋為什麼升（compulsory miss 減少）。
- [ ] 我量到 footprint 從 1024 B（96.88%）到 2048 B（75%）的 capacity 斷崖，並用加大 cache（→2048B 恢復 96.88%）驗證它是容量問題。
- [ ] 我能解釋 CLEAR 狀態為什麼需要（Verilator 4.038 限制 + reset 正確性）。
- [ ] （挑戰）我把它改成 2-way，用一個故意 conflict 的 trace 量到 direct-mapped 低 hit rate vs 2-way 高 hit rate，證明 associativity 治 conflict miss。

## 延伸閱讀

- **《Computer Organization and Design, RISC-V Edition》(Patterson & Hennessy) 第 5.4 節「An Example Cache: The Intrinsity FastMATH Processor」與 associativity 部分**：一個真實 cache 的完整解剖，含 tag/index 切法、set-associative 的命中路徑、replacement。你做完這個練習再讀它，會發現自己做的就是它的教學縮小版。它的 miss rate vs associativity 曲線正是你挑戰題要重現的定性結論。
- **Hennessy & Patterson《Computer Architecture: A Quantitative Approach》Appendix B.1~B.2**：3C 模型（compulsory/capacity/conflict）的精確定義與量測方法。你的三組實驗分別對應 compulsory（實驗 1 的 4 次）、capacity（實驗 3 斷崖）、conflict（挑戰題）——用它把你量到的數字歸類到正確的 miss 類型。
- **[Sodor 的 icache / rocket-chip 的 `ICache`](https://github.com/ucb-bar/riscv-sodor)**：教學型與工業型 I-cache 的對照原始碼。Sodor 的簡單直接（和你做的接近），rocket 的 `ICache` 帶 set-associative、refill 狀態機、和 pipeline 的接口——看完你的實作再讀，你會認得每一塊，只是它多了 corner case 處理。這是「我做的小 cache」升級到「真實 cache」的最佳對照。
- **Ch 26 本身的延伸閱讀**：這個練習是 Ch 26 的動手版，Ch 26 的四條延伸閱讀（P&H 5.3-5.4、H&H 8.3、Quantitative Approach Appendix B、picorv32/rocket）全部適用，尤其做挑戰題（set-associative）時回去對照 Ch 26 的 associativity 取捨表。

做完這個練習，你對 cache 的理解就從紙上談兵變成手上有數。Part 4 到此完整：cache（I/D）、虛擬記憶體（Sv32/TLB）、AXI bus，加上你親手驗證過的 cache 行為。接下來 Part 5 進入 CSR、trap、中斷——讓 core 能處理例外、跑 trap handler，真正邁向能跑作業系統的 core。

→ [Ch 31 CSR file 實作：mstatus / mtvec / mepc / mcause](./31-csr-file.md)
