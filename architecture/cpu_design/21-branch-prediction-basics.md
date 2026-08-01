# Ch 21 — 分支預測基礎：BTB、2-bit 飽和計數器

> **目標**：搞懂為什麼 pipeline 遇到分支會付出代價（control hazard penalty），以及怎麼用「預測」把代價省下來。你會從 static prediction（always-taken / BTFN）建立直覺，再進到動態預測：1-bit 與 2-bit 飽和計數器（saturating counter）、BHT（branch history table，分支歷史表）與 BTB（branch target buffer，分支目標暫存器）的結構（tag / target / valid）。最後親手實作一個 2-bit BHT + BTB 模組，用 C++ testbench 真跑驗證它的狀態轉移。
> **環境**：WSL + verilator 4.038。所有輸出皆真跑。

## 為什麼需要分支預測？

回想 Ch 18：我們把 branch 的 resolve（判斷該不該跳、跳去哪）挪到 ID 級，比放到 EX 級省一個 cycle。但即使 resolve 在 ID，還是有代價——當一條 branch 在 ID 級決定「要跳」時，它後面**已經被 fetch 進來**的那條指令（就在 IF 級）是錯的，得 flush 掉。

用時間軸看這個代價。假設 branch 在 cycle 1 進 IF，cycle 2 進 ID 才知道要跳：

```
           cyc1   cyc2   cyc3   cyc4
  branch    IF     ID     EX     MEM
  wrong          IF   <-- 這條是 branch 的下一條 PC+4，
                          但 branch 要跳去別的地方，它是錯的 → flush
  correct        (泡)   IF     ID    <-- 真正該執行的 target，晚了 1 cycle
```

每次「跳成功」（taken）都要丟掉 1 條已 fetch 的指令，插一個泡（bubble）。這就是 **control hazard penalty**。resolve 在 ID 是 1 cycle，若當初 resolve 在 EX 就是 2 cycle，更深的 pipeline（現代 CPU 動輒 15 級以上）penalty 可以到 15–20 cycle。

代價有多痛？拿一段迴圈估算。典型迴圈每 5–7 條指令就有一條 branch，若每個 taken branch 都付 1 cycle penalty，光是分支就讓 CPI 從 1.0 掉到 1.15 上下；在 20 級 pipeline，同樣的分支密度會讓 CPI 直接翻倍。**pipeline 越深，分支越貴。** 這是為什麼所有現代 CPU 都砸重本做分支預測。

分支預測的核心賭注很單純：**在 IF 級，指令都還沒解碼，我們就猜它是不是 branch、猜它跳不跳、猜它跳去哪**。猜對了，下一個 cycle 直接 fetch 猜的 target，pipeline 不用停；猜錯了，才付 flush 的代價。只要猜對率夠高，平均代價就很低。

## 先建立直覺：賭下一步往哪走

把 fetch 想成一個人在走迷宮，每到一個分岔口（branch）都得選一條路，但要等走到下個路口（ID 級 resolve）才知道剛剛選對沒。如果他每次都停下來等確認再走，會很慢。聰明的做法是**根據過去的經驗直接猜一條先走**：

```
   到分岔口 (branch 在 IF)
        │
        ▼
   查「我以前在這個路口都往哪走?」  ← 這就是 BHT/BTB 記的東西
        │
   ┌────┴─────┐
   猜 taken   猜 not-taken
   跳去 target   走 PC+4
        │
        ▼
   繼續走 (pipeline 不停)
        │
        ▼
   ID 級真相揭曉 ── 猜對: 賺到! ── 猜錯: 退回來重走 (flush)
```

「以前在這個路口都往哪走」就是預測的依據。迴圈的回跳分支幾乎每次都 taken，猜 taken 就對；一個很少成立的 error check，幾乎每次 not-taken，猜 not-taken 就對。**過去是未來最好的預測**——這是所有動態預測器的哲學。

預測器要回答兩個獨立的問題：

1. **方向（direction）**：這條 branch 這次 taken 還是 not-taken？→ 由 **BHT + 飽和計數器**回答。
2. **目標（target）**：如果 taken，PC 要變成多少？→ 由 **BTB** 回答（IF 級還沒解碼，算不出 branch 的 immediate，只能查表）。

兩個問題兩張表，這是本章的骨架。

## 核心概念一：靜態預測（static prediction）

最簡單的預測器不記歷史，永遠給同一個答案。有三種經典策略：

| 策略 | 規則 | 適合 | 命中率（典型） |
|---|---|---|---|
| always-not-taken | 一律猜不跳，繼續 fetch PC+4 | 硬體最省（根本不用查表） | 差，約 30–40% |
| always-taken | 一律猜跳 | 迴圈多的程式 | 約 60–70% |
| BTFN | Backward-Taken-Forward-Not-taken：往回跳（負 offset）猜 taken，往前跳猜 not-taken | 抓住「迴圈回跳=taken、if 前跳=not-taken」的統計規律 | 約 70–80% |

**BTFN**（backward taken, forward not-taken）是靜態預測的甜蜜點。它的洞見是：branch 的 offset 方向本身就洩漏了意圖——

```
   loop:
       ...
       bne  x10, x11, loop   ← offset 是負的（往回跳），大概率 taken
                               這是迴圈，會跳很多次

       beq  x5, x0, skip      ← offset 是正的（往前跳），大概率 not-taken
       ...                      這是 if/error check，通常不成立
   skip:
```

迴圈回跳的 offset 是負數，if 的跳過 offset 是正數。BTFN 只看 offset 的符號位就能做到 70–80% 命中，而且 IF 級解不出完整 immediate 沒關係——**offset 的符號位在指令 encoding 裡位置固定，IF 級抽得出來**。這是「幾乎不花硬體就拿到不錯命中率」的漂亮設計，很多入門級 core 就停在 BTFN。

靜態預測的天花板：它對「同一條 branch，這次跳下次不跳」的情況束手無策，因為它根本不記每條 branch 的個別行為。要突破，得記歷史——動態預測登場。

## 核心概念二：1-bit 動態預測

最小的動態預測器：每條 branch 記 **1 個 bit**，記「它上次 taken 還是 not-taken」，這次就猜跟上次一樣。

```
   狀態: 1 bit  ── 0 = 上次 NT，猜 NT
                   1 = 上次 T，猜 T
   resolve 後：把這 bit 更新成這次的實際結果
```

對「連跳很多次」的迴圈，1-bit 表現不錯。但它有個致命弱點：**每次方向反轉都錯，而且錯兩次**。看一個巢狀迴圈的內層分支，pattern 是 `T T T ... T N`（跑 n 次然後退出）：

```
   實際:  T  T  T  T  N  T  T  T  T  N  ...   (每輪迴圈)
   1-bit
   狀態:  T  T  T  T  T  N  T  T  T  T  ...
   預測:  ?  T  T  T  T  N  T  T  T  T  ...
                         ↑        ↑
                    退出時錯(猜T實N)  下輪進入時又錯(猜N實T)
```

退出迴圈那次錯一次（狀態被改成 N），下一輪重新進迴圈第一次又錯一次（因為狀態還停在 N）。一次退出造成**兩次 mispredict**。內層迴圈跑 100 次、外層跑很多輪的話，每輪固定賠 2 次，很浪費。問題根源：1-bit 沒有「遲滯」，一次反常就翻臉。

## 核心概念三：2-bit 飽和計數器（本課主角）

修法：給每條 branch **2 個 bit**，做成一個飽和計數器（saturating counter），需要**連續錯兩次**才改變預測方向。四個狀態：

```
        taken              taken              taken
   ┌──────────▶┌──────────▶┌──────────▶┌──────────┐
   │  00       │  01       │  10       │  11      │
   │strongly NT│weakly NT  │weakly T   │strongly T│
   │  猜 NT    │  猜 NT    │  猜 T     │  猜 T    │
   └◀──────────└◀──────────└◀──────────└◀─────────┘
        NT                 NT                 NT

   最高位 = 預測方向：  bit1=0 → 猜 NT,  bit1=1 → 猜 T
   taken 就 +1（飽和在 11），not-taken 就 -1（飽和在 00）
```

關鍵在**最高位（bit 1）決定預測方向**，而狀態要走兩格才能翻越 01↔10 的中線。回到剛剛 `T T T T N` 的迴圈：

```
   實際:  T   T   T   T   N   T   T   T   T   N
   狀態: 10→ 11→ 11→ 11→ 11→ 10→ 11→ 11→ 11→ 11→10
   預測:  T   T   T   T   T   T   T   T   T   T
                          ↑錯一次
```

退出迴圈時猜 T 實 N，錯一次，但狀態只從 11 掉到 10（還是猜 T），**下一輪重新進迴圈時仍猜 T，對了**。一次退出只賠 1 次 mispredict，比 1-bit 省一半。這個「遲滯」（hysteresis）就是 2-bit 的價值：**偶爾的反常不動搖預測，只有真的連續反轉才改口**。

2-bit 飽和計數器是效能/成本的甜蜜點，實測命中率約 85–93%，幾乎所有真實 CPU 的基礎預測都用它（或它的變種）。本課就實作它。

## 底層機制：BHT 與 BTB 怎麼協作

現在把「方向」和「目標」兩張表接起來。IF 級拿到 `pc_f`，同時查兩張表：

```
        pc_f (IF 級的 PC)
          │
     ┌────┴──────────────────────────┐
     │ 取 index bits (跳過 byte offset 低 2 bit) │
     └────┬──────────────────────────┘
          │ idx
   ┌──────┴──────┐            ┌──────────────┐
   │    BHT      │            │     BTB      │
   │ [idx] 2-bit │            │ [idx]:       │
   │  計數器      │            │  valid       │
   └──────┬──────┘            │  tag         │
          │ bit1               │  target(32b) │
          │(方向)              └──────┬───────┘
          │                          │
          │                   valid && tag==pc_f 高位 ? → btb_hit
          │                          │
          ▼                          ▼
   predict_taken = btb_hit && bht[idx].bit1
   predict_target = btb_target[idx]
```

三個設計要點，每個都有原因：

**為什麼 index 跳過低 2 bit？** RV32I 指令都 4-byte 對齊，PC 的低 2 bit 恆為 0，拿它們當 index 只會浪費一半的表。所以 `idx = pc[IDX+1:2]`。

**為什麼 BTB 要 tag？** BHT/BTB 的表很小（本課 64 條），用 PC 低位當 index 一定會**別名（aliasing）**：兩條相距 `64*4=256` byte 的 branch 會映到同一條目。BHT 別名頂多讓預測方向髒一點（還是 1 bit，錯了也就 penalty 1），但 **BTB 存的是 32-bit target，別名會給出完全錯的跳轉位址**——那比不預測還糟（fetch 到亂七八糟的地方）。所以 BTB 多存一個 **tag**（PC 的高位），查詢時比對 tag 相符才算命中。tag 就是「這條目現在裝的真的是你要查的那條 branch 嗎」的身分證。

**為什麼 BTB 只在 taken 時記 target？** not-taken 的 branch 下一條就是 PC+4，IF 級自然會 fetch，不需要 target。只有 taken 才需要記「跳去哪」。所以更新時：taken → 寫 BTB（valid=1, tag, target）；not-taken → 不動 BTB。

**valid bit** 標記這條目有沒有被寫過。reset 後全部 valid=0，任何查詢都 miss（fallback 到 not-taken，繼續 fetch PC+4）。這是**冷啟動**（cold start）行為——預測器要「熱身」看過幾次分支才準。

更新時機：branch 在 ID 級 resolve（Ch 18 的約定），這時真相揭曉，把 `update_pc / update_taken / update_target` 送回預測器，同步更新 BHT 計數器和 BTB。所以預測器有兩個埠：**查詢埠**（IF 級，組合輸出，當下就要用）和**更新埠**（ID 級 resolve 後，同步寫入）。

## 範例一：2-bit BHT + BTB 模組實作

`bpred.sv`。port 分查詢（`pc_f / predict_taken / predict_target`）和更新（`update_*`）兩組：

```systemverilog
// bpred.sv — 2-bit saturating BHT + BTB，IF 級分支預測器
module bpred #(
    parameter IDX_BITS = 6,           // 條目數 = 2^IDX_BITS = 64
    parameter TAG_BITS = 8            // BTB tag 寬度
)(
    input  logic        clk,
    input  logic        rst,
    // 查詢埠（IF 級，組合輸出）
    input  logic [31:0] pc_f,
    output logic        predict_taken,
    output logic [31:0] predict_target,
    // 更新埠（分支在 ID 級 resolve 後回寫）
    input  logic        update_en,
    input  logic [31:0] update_pc,
    input  logic        update_taken,
    input  logic [31:0] update_target
);
    localparam N = (1 << IDX_BITS);

    // BHT：每條目一個 2-bit 飽和計數器
    // 00=strongly NT, 01=weakly NT, 10=weakly T, 11=strongly T
    logic [1:0]  bht [N-1:0];
    // BTB：valid + tag + target
    logic        btb_valid  [N-1:0];
    logic [TAG_BITS-1:0] btb_tag [N-1:0];
    logic [31:0] btb_target [N-1:0];

    logic [IDX_BITS-1:0] idx_f, idx_u;
    logic [TAG_BITS-1:0] tag_f, tag_u;
    assign idx_f = pc_f[IDX_BITS+1 : 2];                 // 跳過 byte offset
    assign tag_f = pc_f[IDX_BITS+1+TAG_BITS : IDX_BITS+2];
    assign idx_u = update_pc[IDX_BITS+1 : 2];
    assign tag_u = update_pc[IDX_BITS+1+TAG_BITS : IDX_BITS+2];

    // 查詢（組合）：BHT 最高位=1 且 BTB 命中才預測 taken
    logic btb_hit;
    assign btb_hit = btb_valid[idx_f] && (btb_tag[idx_f] == tag_f);
    assign predict_taken  = btb_hit && bht[idx_f][1];
    assign predict_target = btb_target[idx_f];

    integer i;
    always_ff @(posedge clk) begin
        if (rst) begin
            for (i = 0; i < N; i = i + 1) begin
                bht[i]       <= 2'b01;   // 初始 weakly NT
                btb_valid[i] <= 1'b0;
            end
        end else if (update_en) begin
            // 2-bit 飽和計數器狀態轉移
            if (update_taken) begin
                if (bht[idx_u] != 2'b11) bht[idx_u] <= bht[idx_u] + 2'b01;
            end else begin
                if (bht[idx_u] != 2'b00) bht[idx_u] <= bht[idx_u] - 2'b01;
            end
            // BTB 只在 taken 時記 target
            if (update_taken) begin
                btb_valid[idx_u]  <= 1'b1;
                btb_tag[idx_u]    <= tag_u;
                btb_target[idx_u] <= update_target;
            end
        end
    end
endmodule
```

幾個實作要點：

- **查詢是純組合**（`assign`），IF 級當下就要 `predict_taken`，不能等 clock。
- **更新是同步**（`always_ff`），ID 級 resolve 後下個 posedge 寫入。
- **飽和邏輯**：taken 時「不是 11 才 +1」，not-taken 時「不是 00 才 -1」，這就是「飽和」——撞到上下限就停，不會 overflow 回繞。
- **初始 weakly NT（01）**：偏保守，因為 BTB 一開始 valid=0，就算 BHT 猜 T 也因 btb_hit=0 而輸出 NT。第一次看到某 branch 一定預測 NT。

## 範例二：真跑驗證狀態轉移

`bpred_tb.cpp` 精心設計輸入，逐步走過飽和計數器的每個狀態與 BTB 命中/別名：

```cpp
// bpred_tb.cpp — 驗證 2-bit 飽和計數器狀態轉移 + BTB 命中
#include "Vbpred.h"
#include "verilated.h"
#include <cstdint>
#include <cstdio>

static Vbpred *dut;
static int fails = 0;

static void tick() { dut->clk = 0; dut->eval(); dut->clk = 1; dut->eval(); }

// 對 pc 這條分支做一次 resolve 更新
static void update(uint32_t pc, int taken, uint32_t target) {
    dut->update_en = 1; dut->update_pc = pc;
    dut->update_taken = taken; dut->update_target = target;
    tick();
    dut->update_en = 0; dut->eval();
}

// 查詢 pc 的預測（組合）
static int query(uint32_t pc, uint32_t *tgt) {
    dut->pc_f = pc; dut->eval();
    if (tgt) *tgt = dut->predict_target;
    return dut->predict_taken;
}

static void check(const char *name, int got, int exp) {
    bool ok = (got == exp);
    printf("[%s] %-28s got=%d exp=%d\n", ok ? "OK " : "BAD", name, got, exp);
    if (!ok) fails++;
}

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    dut = new Vbpred;

    dut->rst = 1; dut->update_en = 0; tick(); tick();
    dut->rst = 0; dut->eval();

    const uint32_t PC = 0x80000100;
    const uint32_t TGT = 0x80000080;

    check("init predict NT", query(PC, nullptr), 0);        // BTB miss → NT

    update(PC, 1, TGT);                                     // 01->10, BTB 記 target
    uint32_t tgt = 0;
    check("after 1 taken -> T", query(PC, &tgt), 1);
    check("BTB target correct", tgt == TGT, 1);

    update(PC, 1, TGT);                                     // 10->11
    check("after 2 taken -> T (strong)", query(PC, nullptr), 1);

    update(PC, 0, TGT);                                     // 11->10，遲滯
    check("1 miss from strong -> still T", query(PC, nullptr), 1);

    update(PC, 0, TGT);                                     // 10->01
    check("2 miss -> NT", query(PC, nullptr), 0);

    update(PC, 0, TGT);                                     // 01->00
    update(PC, 0, TGT);                                     // 00->00 飽和
    check("saturate at strongly NT", query(PC, nullptr), 0);

    update(PC, 1, TGT);                                     // 00->01 仍 NT
    check("1 taken from bottom -> still NT", query(PC, nullptr), 0);
    update(PC, 1, TGT);                                     // 01->10
    check("2 taken -> T", query(PC, nullptr), 1);

    // idx=pc[7:2], tag=pc[15:8]。翻 bit8 → 同 idx、不同 tag → BTB miss
    uint32_t PC2 = PC ^ (1u << 8);
    check("aliased tag -> BTB miss -> NT", query(PC2, nullptr), 0);

    printf("\n%s (%d fail)\n", fails ? "FAILED" : "ALL PASSED", fails);
    delete dut;
    return fails ? 1 : 0;
}
```

編譯執行：

```bash
verilator --cc bpred.sv --exe bpred_tb.cpp --Mdir obj_bpred
make -C obj_bpred -f Vbpred.mk Vbpred
./obj_bpred/Vbpred
```

真實輸出：

```
[OK ] init predict NT              got=0 exp=0
[OK ] after 1 taken -> T           got=1 exp=1
[OK ] BTB target correct           got=1 exp=1
[OK ] after 2 taken -> T (strong)  got=1 exp=1
[OK ] 1 miss from strong -> still T got=1 exp=1
[OK ] 2 miss -> NT                 got=0 exp=0
[OK ] saturate at strongly NT      got=0 exp=0
[OK ] 1 taken from bottom -> still NT got=0 exp=0
[OK ] 2 taken -> T                 got=1 exp=1
[OK ] aliased tag -> BTB miss -> NT got=0 exp=0

ALL PASSED (0 fail)
```

逐條讀懂這幾個關鍵驗證點：

- **init predict NT**：reset 後 BTB valid=0，就算 BHT 是 01 也因 btb_hit=0 輸出 NT。冷啟動正確。
- **遲滯（1 miss from strong -> still T）**：從 strongly T（11）吃一次 NT 只掉到 weakly T（10），仍猜 T。這正是 2-bit 比 1-bit 強的地方——一次反常不翻臉。
- **需要兩次才翻（1 taken from bottom -> still NT）**：從 00 吃一次 taken 只到 01（還是猜 NT），要第二次 taken 才到 10 翻成猜 T。證明飽和計數器的雙格遲滯。
- **飽和（saturate at strongly NT）**：連續 NT 到 00 就停住，不會 underflow 回繞成 11。
- **BTB tag 防別名（aliased tag -> BTB miss）**：`PC2 = PC ^ (1<<8)` 和 PC 有相同 index、不同 tag。查 PC2 時 tag 對不上，btb_hit=0，正確地不拿 PC 的 target 亂用。這驗證了 tag 的防別名作用。

## 對比取捨

| 預測器 | 記憶體 | 典型命中率 | 硬體成本 | 適用 |
|---|---|---|---|---|
| always-not-taken | 0 | 30–40% | 幾乎零 | 最陽春 / 教學 |
| always-taken | 0 | 60–70% | 零 | 迴圈多的 workload |
| BTFN（靜態） | 0 | 70–80% | 抽 offset 符號位 | 入門 core 甜蜜點 |
| 1-bit 動態 | 1 bit/entry | 80–85% | 小 | 反轉不頻繁時堪用 |
| **2-bit 飽和（本課）** | 2 bit/entry | **85–93%** | 小 | **通用基礎預測** |
| gshare / tournament | 更多 | 93–97% | 中 | 進階（Ch 22） |

BHT vs BTB 分工：

| | BHT | BTB |
|---|---|---|
| 回答 | 方向（taken?） | 目標（跳去哪?） |
| 每條目存 | 2-bit 計數器 | valid + tag + 32-bit target |
| 別名的傷害 | 小（頂多方向髒） | 大（會 fetch 到錯位址）→ 需要 tag |
| 何時更新 | 每次 resolve | 只有 taken 時 |

## 踩雷區

**雷 1：以為 1-bit 和 2-bit 差不多，省 1 bit 沒差。**
- 錯誤直覺：「都是記上次結果，差一個 bit 能差多少」。
- 正確認識：差在**遲滯**。1-bit 對迴圈退出會賠兩次 mispredict（退出時一次、下輪重進時一次），2-bit 只賠一次。對高頻迴圈這是每輪省一半分支 penalty。多花的 1 bit/entry 換到的命中率提升非常划算，這是幾乎沒人用 1-bit 的原因。

**雷 2：BTB 不做 tag，直接拿 index 命中的 target 就跳。**
- 錯誤直覺：「BHT 都能容忍別名，BTB 應該也行」。
- 正確認識：BHT 別名頂多讓方向預測髒（還是 1-bit 資訊，錯了 penalty 1）；但 **BTB 存的是完整 32-bit target**，別名會讓你 fetch 到另一條 branch 的目標——一個完全無關的位址。沒 tag 的 BTB 在別名時比不預測還糟。tag 是 BTB 的必需品，不是選配。

**雷 3：忘了 valid bit，reset 後 BTB 裡的垃圾被當成有效 target。**
- 錯誤直覺：「reset 把 target 清成 0 就好」。
- 正確認識：target=0 也是個位址（PC 可能真的跳去 0x0 附近嗎？在本課 reset PC=0x80000000 下不會，但邏輯上你不能假設）。真正該做的是 valid bit：reset 全清 0，任何條目「沒被真的寫過」就 miss，fallback 到 not-taken。valid 區分「這條目有內容」vs「這條目是冷的」，比清零 target 乾淨得多。

**雷 4：把查詢做成同步（等 clock）。**
- 錯誤直覺：「表都用 always_ff 讀寫比較一致」。
- 正確認識：預測是 IF 級**當下**要用的——這個 cycle 就得決定下個 PC。查詢必須是組合邏輯（`assign predict_taken = ...`），當拍就出結果。只有更新（寫回計數器/BTB）才是同步。搞反的話你的預測會晚一拍，等於沒預測。

## 進階延伸

- **BTB 也可以順便當「這是不是 branch」的偵測器**：IF 級指令還沒解碼，其實連「這條是不是 branch」都不知道。實務上 BTB 命中本身就當作「這 PC 是一條（之前 taken 過的）branch」的訊號——命中就代表這位址以前是條會跳的 branch，直接用它的 target。沒命中就當普通指令走 PC+4。本課把 branch 偵測簡化交給後級，但真設計常讓 BTB 一表兩用。
- **BHT 和 BTB 可以合表也可以分表**：本課用同一個 index 查兩張表。有些設計把 2-bit 方向計數器直接塞進 BTB 條目（一表存 valid/tag/target/counter），省一次查表；也有設計讓 BHT 比 BTB 大（方向便宜、可以多記），各有取捨。
- **飽和計數器不只 2-bit**：理論上可以 3-bit（更強遲滯），但實測 2-bit 已是甜蜜點，3-bit 的邊際效益低而成本翻倍，幾乎沒人用。真正的進步不在加 bit，而在「怎麼 index」——這就是 Ch 22 gshare 用 global history 的動機。
- **這章的方向預測還很笨**：2-bit BHT 只看「這條 branch 自己的歷史」，抓不到「這條 branch 的結果和別條 branch 相關」的模式（correlation）。下一章的 correlating predictor 和 gshare 就是來補這個洞，命中率能再上一個台階。

## 本章重點整理

- **control hazard penalty**：branch resolve 在 ID 級，每次 taken 要 flush 已 fetch 的 1 條指令（penalty 1 cycle）；pipeline 越深越貴。分支預測就是在 IF 級提前猜方向和目標來省這代價。
- 靜態預測：always-taken / **BTFN**（往回跳猜 taken）不記歷史，只看 offset 符號位就能到 70–80%，是入門甜蜜點。
- 動態預測記每條 branch 歷史：1-bit 猜「跟上次一樣」但迴圈退出賠兩次；**2-bit 飽和計數器**有遲滯，連錯兩次才翻，賠一次，命中率 85–93%，是通用基礎。
- **BHT** 答方向（2-bit 計數器），**BTB** 答目標（valid + **tag** + 32-bit target）。BTB 一定要 tag 防別名，只在 taken 時記 target。
- 查詢是組合（IF 級當拍用），更新是同步（ID 級 resolve 後）。
- 全部狀態轉移、飽和、遲滯、BTB 別名都真跑驗證通過。

## 自我檢核

- [ ] 我能畫出 branch resolve 在 ID 級時，taken 造成 1 cycle flush 的時間軸。
- [ ] 我能解釋 BTFN 為什麼只看 offset 符號位就有 70–80% 命中，以及 IF 級為何抽得出符號位。
- [ ] 我能說出 1-bit 預測器對迴圈退出為何賠兩次 mispredict，2-bit 為何只賠一次。
- [ ] 我能畫出 2-bit 飽和計數器的四狀態圖，並說明「最高位決定方向、要走兩格才翻」。
- [ ] 我能解釋 BTB 為什麼必須有 tag，別名時沒 tag 會怎樣。
- [ ] 我能說清楚為什麼查詢要組合、更新要同步，以及 BTB 為何只在 taken 時記 target。

## 延伸閱讀

- **《Computer Organization and Design, RISC-V Edition》(Patterson & Hennessy) 第 4.8 節「Control Hazards」的 dynamic branch prediction 段**：課本用同一個迴圈例子講 1-bit 為何賠兩次、2-bit 怎麼修，圖和本章一脈相承。讀它把「遲滯」的直覺坐實。
- **《Computer Architecture: A Quantitative Approach》(Hennessy & Patterson) 第 3.3 節「Reducing Branch Costs with Advanced Branch Prediction」**：從 2-bit predictor 一路推到 correlating，是本章到 Ch 22 的橋。先讀 2-bit 那半段，correlating 留到下一章。
- **[BOOM 的 branch prediction 文件](https://docs.boom-core.org/en/latest/sections/branch-prediction/)**：看一個真教學型亂序 core 怎麼組織 BTB / BHT / 更複雜的預測器成一整套 frontend。你會發現本章的 BTB+BHT 就是它 frontend 的最底層積木。
- **[picorv32 原始碼](https://github.com/YosysHQ/picorv32)**：反例——picorv32 刻意**不做**分支預測（它是多週期、追求小而簡）。對照它你會理解「什麼時候值得做預測」：pipeline 淺、面積敏感時，不預測反而是對的取捨。

方向猜對了、目標查到了，但 2-bit BHT 只看單條 branch 的歷史，抓不到「branch 之間互相相關」的模式。下一章我們把 global history 拉進來，做 gshare 和 tournament，把命中率再推高一截。

→ [Ch 22 進階預測器：gshare / tournament、RAS](./22-advanced-predictors.md)
