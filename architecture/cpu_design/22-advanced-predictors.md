# Ch 22 — 進階預測器：gshare / tournament、RAS

> **目標**：突破 2-bit BHT「只看單條 branch 自己歷史」的天花板。你會理解 branch 之間的相關性（correlation）、用 correlating predictor 記全域歷史、進到 gshare（把 global history 和 PC 用 XOR 混起來索引 PHT）、再看 tournament predictor（local + global + chooser 三表投票）怎麼兩全其美，最後補上專門對付 return 的 RAS（return address stack）。核心動手：實作 gshare，用一段有全域相關性的 branch trace 量它的 mispredict rate，和 2-bit bimodal 真跑對照，看數字差多少。
> **環境**：WSL + verilator 4.038。所有 mispredict rate 皆真跑量測。

## 為什麼 2-bit BHT 還不夠？

Ch 21 的 2-bit BHT 對每條 branch 記一個計數器，猜「這條 branch 自己以前都往哪跳」。這對迴圈很好，但真實程式有一類模式它完全抓不到——**branch 之間互相相關**。

看一段很常見的 C code：

```c
if (a == 0)      // branch B1
    a = 1;
if (b == 0)      // branch B2
    b = 1;
if (a != b)      // branch B3  ← B3 的結果完全由 B1、B2 決定！
    ...
```

B3（`a != b`）跳不跳，取決於 B1、B2 剛剛怎麼走。若 B1 taken（a 本來是 0，被設成 1）而 B2 not-taken（b 本來非 0），那 a 和 b 大概不等，B3 的結果就被前兩條 branch **決定了**。這種相關性，2-bit BHT 看不到——它只記 B3 自己的歷史，而 B3 自己的歷史時好時壞（因為它其實跟著 B1/B2 變），計數器在中間搖擺，命中率上不去。

洞見：**要預測 B3，不能只看 B3，要看「最近幾條 branch 整體走了什麼 pattern」**。這就是 correlating predictor 的出發點——引入 **global history（全域歷史）**。

## 先建立直覺：看整體氣氛，不只看單一習慣

2-bit BHT 像一個只記「我在這個路口以前都往左」的導航。correlating predictor 像一個會看「我剛剛連過三個路口都往左，那接下來這個路口大概往右」的導航——它記的是**最近一連串選擇構成的情境**，用情境去查該怎麼走。

```
   2-bit BHT（local）：
      只問「這條 branch 的 index，計數器是多少?」
      pc ──▶ [BHT] ──▶ 方向

   correlating / gshare（global）：
      問「最近 N 條 branch 走的 pattern + 這條 branch，該猜什麼?」
      pc ──┐
           ├─XOR/拼接──▶ [PHT] ──▶ 方向
      GHR ─┘  (最近 N 條 branch 的 T/N 記錄)
```

**GHR（global history register，全域歷史暫存器）** 是一個移位暫存器，每條 branch resolve 後把它的結果（1=taken, 0=not-taken）從右邊移進來。它就是「最近 N 條 branch 的 T/N 序列」。用這個序列去區分「同一條 branch 在不同情境下的行為」，就能抓到 B3 那種相關性。

## 核心概念一：correlating predictor（兩層設計）

最直接的做法：PHT（pattern history table，樣式歷史表）用 **global history 當 index**。GHR 有 h bit，就有 `2^h` 個 pattern，每個 pattern 配一個 2-bit 計數器。

```
   (h, m) correlating predictor：
      h = global history bits
      m = 每個 counter 的 bit 數（通常 2）

   純 global（GAg 類）：
      index = GHR        ← 完全不看 PC，只看歷史 pattern
```

純 global 的問題：不同 PC 的 branch 共用同一組 pattern 表，會嚴重別名（很多不相關的 branch 擠在同一個 GHR pattern 上互相污染）。修法有兩個方向：

- **拼接（concatenation）**：`index = {PC 低位, GHR}`——PC 和歷史各占 index 一部分。缺點是表要 `2^(pc_bits + h)` 大，指數爆炸。
- **XOR 混合**：`index = PC 低位 ^ GHR`——用同樣大小的表，把 PC 和歷史「攪」在一起。這就是 **gshare**。

## 核心概念二：gshare（本課主角）

gshare = **g**lobal history **share**d index，McFarling 1993 提出。一句話：**把 PC 低位和 GHR 做 XOR，用結果去索引一張 PHT。**

```
        pc_f[h+1:2]  (PC 低位 h bit)
             │
             ▼  XOR
        ┌─────────┐
        │   GHR   │  (最近 h 條 branch 的 T/N)
        └────┬────┘
             │ idx = pc_low ^ ghr
             ▼
        ┌─────────────────┐
        │      PHT        │  2^h 個 2-bit 計數器
        │  [idx] 2-bit    │
        └────────┬────────┘
                 │ bit1
                 ▼
           predict_taken
```

XOR 的妙處：**同一條 branch（同 PC）在不同歷史情境下，落到不同的 PHT 條目**。B3 在「B1 taken, B2 not-taken」情境和「B1 not-taken, B2 taken」情境下，GHR 不同，`PC^GHR` 不同，用不同計數器記錄——各自學各自的，互不干擾。這就抓到了相關性。同時 XOR 保持表的大小為 `2^h`（不像拼接那樣爆炸），用最少的別名換到 PC 和歷史都參與 index。

gshare 是「單一預測器」裡 cost/performance 最好的之一，很多真 CPU（早期 Alpha 21264 的一部分、許多嵌入式核）都用它或它的變種。

更新時的一個細節要想清楚：**PHT 該用「預測當下的 GHR」還是「resolve 當下的 GHR」來索引更新？** 答案是**預測當下的**——因為你要更新的是「當初做這次預測用的那個計數器」。在本課這種「in-order、一次只有一條 branch 在飛」的簡化下，branch 從 IF 查詢到 ID resolve 之間 GHR 沒被別的 branch 改過，所以更新時用當下 GHR 就等於預測時的 GHR。真實亂序 core 要把預測時的 GHR 快照存進 branch 一起帶下去 resolve，這裡不深挖。

## 範例一：gshare 實作

`gshare.sv`。比 Ch 21 的 bpred 簡單（只做方向，不含 BTB，target 沿用 Ch 21 的 BTB 即可），核心就是 GHR + XOR index：

```systemverilog
// gshare.sv — global history XOR PC 索引 PHT（2-bit 飽和計數器）
module gshare #(
    parameter HIST_BITS = 6            // GHR 寬度 = PHT 索引寬度
)(
    input  logic        clk,
    input  logic        rst,
    input  logic [31:0] pc_f,
    output logic        predict_taken,
    input  logic        update_en,
    input  logic [31:0] update_pc,
    input  logic        update_taken
);
    localparam N = (1 << HIST_BITS);

    logic [HIST_BITS-1:0] ghr;          // global history register
    logic [1:0]           pht [N-1:0];  // pattern history table

    // 索引 = PC 低位 XOR GHR
    logic [HIST_BITS-1:0] idx_f, idx_u;
    assign idx_f = pc_f[HIST_BITS+1 : 2]      ^ ghr;
    assign idx_u = update_pc[HIST_BITS+1 : 2] ^ ghr;

    assign predict_taken = pht[idx_f][1];

    integer i;
    always_ff @(posedge clk) begin
        if (rst) begin
            ghr <= '0;
            for (i = 0; i < N; i = i + 1) pht[i] <= 2'b01;
        end else if (update_en) begin
            // 更新 2-bit 飽和計數器
            if (update_taken) begin
                if (pht[idx_u] != 2'b11) pht[idx_u] <= pht[idx_u] + 2'b01;
            end else begin
                if (pht[idx_u] != 2'b00) pht[idx_u] <= pht[idx_u] - 2'b01;
            end
            // GHR 左移併入最新結果
            ghr <= {ghr[HIST_BITS-2:0], update_taken};
        end
    end
endmodule
```

對照組是純 PC 索引的 2-bit（bimodal，就是 Ch 21 BHT 去掉 BTB 那部分）：

```systemverilog
// bimodal.sv — 純 PC 索引的 2-bit 飽和計數器，當 gshare 對照組
module bimodal #(parameter IDX_BITS = 6)(
    input  logic clk, rst,
    input  logic [31:0] pc_f,
    output logic predict_taken,
    input  logic update_en,
    input  logic [31:0] update_pc,
    input  logic update_taken
);
    localparam N = (1 << IDX_BITS);
    logic [1:0] pht [N-1:0];
    logic [IDX_BITS-1:0] idx_f, idx_u;
    assign idx_f = pc_f[IDX_BITS+1 : 2];
    assign idx_u = update_pc[IDX_BITS+1 : 2];
    assign predict_taken = pht[idx_f][1];
    integer i;
    always_ff @(posedge clk) begin
        if (rst) for (i=0;i<N;i=i+1) pht[i] <= 2'b01;
        else if (update_en) begin
            if (update_taken) begin if (pht[idx_u]!=2'b11) pht[idx_u]<=pht[idx_u]+2'b01; end
            else               begin if (pht[idx_u]!=2'b00) pht[idx_u]<=pht[idx_u]-2'b01; end
        end
    end
endmodule
```

## 範例二：真跑量 mispredict rate，gshare vs bimodal

關鍵實驗。我們構造一段**有全域相關性**的 branch trace，餵給兩個預測器，數各自猜錯幾次。trace 設計：兩條 branch A、B，B 的 outcome 和「A 剛剛的 outcome」反相關——這正是 bimodal 抓不到、gshare 抓得到的模式。

```cpp
// predcmp_tb.cpp — 同一 branch trace 餵 bimodal vs gshare，比 mispredict rate
#include "Vbimodal.h"
#include "Vgshare.h"
#include "verilated.h"
#include <cstdint>
#include <cstdio>
#include <vector>

template <class DUT> static void tick(DUT *d) { d->clk=0; d->eval(); d->clk=1; d->eval(); }
template <class DUT> static void do_reset(DUT *d) {
    d->rst=1; d->update_en=0; tick(d); tick(d); d->rst=0; d->eval();
}

struct Br { uint32_t pc; int taken; };

template <class DUT>
static int run(DUT *d, const std::vector<Br> &trace) {
    do_reset(d);
    int miss = 0;
    for (const Br &b : trace) {
        d->pc_f = b.pc; d->eval();          // IF 級查詢
        int pred = d->predict_taken;
        if (pred != b.taken) miss++;
        d->update_en = 1;                    // resolve 更新
        d->update_pc = b.pc; d->update_taken = b.taken;
        tick(d);
        d->update_en = 0; d->eval();
    }
    return miss;
}

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);

    // A 的 pattern = T,T,N 循環；B 與 A 的 outcome 反相關（A taken→B not-taken）
    // bimodal 只看各自 PC，抓不到「B 跟著 A」；gshare 靠 GHR 抓得到。
    const uint32_t A = 0x80000100, B = 0x80000140;
    std::vector<Br> trace;
    int a_pat[3] = {1, 1, 0};
    for (int iter = 0; iter < 2000; iter++) {
        int at = a_pat[iter % 3];
        trace.push_back({A, at});
        trace.push_back({B, at ? 0 : 1});
    }
    int total = (int)trace.size();

    Vbimodal *bi = new Vbimodal;
    Vgshare  *gs = new Vgshare;
    int miss_bi = run(bi, trace);
    int miss_gs = run(gs, trace);

    printf("trace branches      = %d\n", total);
    printf("bimodal  mispredict = %d  rate = %.2f%%\n", miss_bi, 100.0*miss_bi/total);
    printf("gshare   mispredict = %d  rate = %.2f%%\n", miss_gs, 100.0*miss_gs/total);
    delete bi; delete gs;
    return 0;
}
```

兩個 top module 要一起 build，verilator 4.038 分開 verilate、各自出 `.a`、再手動連結：

```bash
verilator --cc bimodal.sv --Mdir obj_cmp
verilator --cc gshare.sv  --exe predcmp_tb.cpp --Mdir obj_cmp
cd obj_cmp
make -f Vbimodal.mk Vbimodal__ALL.a
make -f Vgshare.mk  Vgshare__ALL.a
VINC=$(verilator --getenv VERILATOR_ROOT)/include
g++ -I. -I$VINC -c ../predcmp_tb.cpp -o predcmp_tb.o
g++ -c $VINC/verilated.cpp -o verilated_lib.o
g++ predcmp_tb.o verilated_lib.o Vgshare__ALL.a Vbimodal__ALL.a -o Vcmp
./Vcmp
```

真實輸出：

```
trace branches      = 4000
bimodal  mispredict = 1333  rate = 33.33%
gshare   mispredict = 6  rate = 0.15%
```

這個對照非常戲劇化，值得逐步拆解為什麼：

- **bimodal 33.33%**：A 的 pattern 是 `T,T,N` 三循環，bimodal 對 A 大致能追（但週期性 pattern 讓它在 N 那次常錯）。真正被打爆的是 B——B 的 outcome 完全跟著 A，而 bimodal 只看 B 自己 PC 的計數器，看到的是一串「不跟自己歷史相關」的序列，計數器一直被拉來拉去，命中率極差。整體 1/3 的分支猜錯。
- **gshare 0.15%**：GHR 記下了「A 剛剛 taken 還是 not-taken」，`PC_B ^ GHR` 把 B 在不同 A-情境下分到不同 PHT 條目。gshare 學了幾次就發現「這個情境下 B 一定 not-taken、那個情境下 B 一定 taken」，之後幾乎全對。那 6 次 miss 是冷啟動（warmup）——GHR 和 PHT 一開始是空的，要看過幾輪才學會。

**同一段 trace，命中率從 66.67% 拉到 99.85%。** 這就是把 global history 拉進 index 的威力——它能抓到 bimodal 結構上不可能抓到的相關性。當然，這條 trace 是特意設計來凸顯 correlation 的；真實程式沒這麼極端，但 gshare 在真 workload 上相對 bimodal 通常也有 2–5% 的命中率提升（在命中率已經很高的區間，這幾個百分點對 deep pipeline 的 CPI 影響很可觀）。

## 核心概念三：tournament predictor（兩全其美）

gshare 也不是萬能——有些 branch 就是「只跟自己相關」（比如純迴圈計數），對它們 global history 反而是雜訊，local（bimodal）預測更準。反過來 correlation 強的 branch gshare 更準。**能不能對每條 branch 各用最適合的預測器？** 這就是 tournament predictor（Alpha 21264 的著名設計）。

```
   ┌─────────────┐   ┌─────────────┐
   │ local pred  │   │ global pred │   ┌──────────────┐
   │ (per-PC 歷史)│   │  (gshare)   │   │  chooser     │
   └──────┬──────┘   └──────┬──────┘   │ (2-bit 計數器) │
          │ pred_L          │ pred_G   │ 學「這條 branch │
          │                 │          │  哪個預測器準」│
          └────────┬────────┘          └──────┬───────┘
                   │  ◀── chooser 選一個 ──────┘
                   ▼
              final predict
```

**chooser**（選擇器）本身也是一張 2-bit 計數器表，per-PC，學的是「對這條 branch，local 和 global 哪個最近比較準」。每次 resolve 後：如果 local 對 global 錯，chooser 往 local 偏；反之往 global 偏；兩個都對或都錯，chooser 不動。於是每條 branch 自動被路由到最適合它的預測器。tournament 命中率通常比單一 gshare 再高 1–2%，代價是三張表 + 更新邏輯。本課不實作 tournament（結構清楚但 code 冗長），但你要能畫出這張圖、說清 chooser 學什麼。

## 核心概念四：RAS（return address stack）

有一類「branch」方向預測和 target 預測都幫不上忙：**函式返回 `ret`（RV 裡是 `jalr x0, 0(ra)`）**。

問題在哪？同一個函式可能被很多地方呼叫，每次 `ret` 要跳回的位址都不同（回到各自的 caller）。BTB 記的是「上次這條 ret 跳去哪」，但這次的 caller 可能不是上次那個——BTB 對 ret 幾乎必錯。

但 return 的 target 其實**完全可預測**，只要你記得對應的 call。函式呼叫（`jal`/`jalr` 寫 `ra`）和返回是嚴格的**後進先出**（LIFO）配對。所以用一個小 stack：

```
   call (jal ra, func)：把「返回位址 = call 的 PC + 4」push 進 RAS
   ret  (jalr x0, 0(ra))：從 RAS pop 出來，那就是預測的 target

        call A ──push──▶ ┌──────────┐
        call B ──push──▶ │ retB     │ ◀── ret 時 pop 這個
                         │ retA     │
                         └──────────┘  RAS (小 stack, 8~16 深)
```

RAS 對 return 的命中率極高（>95%），因為它直接利用了呼叫的 LIFO 結構，不是靠猜。要注意兩個實作點：

- **怎麼認出 call 和 ret？** RV 有慣例：`jal`/`jalr` 目的暫存器是 `x1`(ra) 或 `x5` 就當 call（push）；`jalr` 且 rs1 是 `x1`/`x5`、rd 是 `x0` 就當 return（pop）。這些 RISC-V ABI 有明確的 hint 規則。
- **RAS 會 overflow/underflow**：stack 深度有限（比如 16），遞迴超過深度就頂掉最舊的，返回時對不上——這時退回 BTB。深遞迴是 RAS 的天敵，但一般程式呼叫深度遠小於 16，命中率仍很高。

本課主線不實作 RAS（留給練習 C 的延伸挑戰），但你要理解：**return 不該用一般 BTB 預測，要用 RAS**；現代 CPU 的 frontend 一定有這塊。

## 對比取捨

| 預測器 | index 依據 | 抓得到 correlation? | 額外硬體 | 典型命中率 |
|---|---|---|---|---|
| bimodal（2-bit BHT） | 只 PC | 否 | 一張 PHT | 85–93% |
| correlating（拼接） | PC + GHR 拼接 | 是 | 表指數變大 | 90–95% |
| **gshare** | PC ^ GHR | 是 | 一張 PHT + GHR | **93–96%** |
| tournament | local + gshare + chooser | 是（自動選最佳） | 三張表 | 94–97% |
| RAS | call/ret LIFO 配對 | —（專治 return） | 小 stack | return >95% |

實驗數字回顧（本章 trace，特意凸顯 correlation）：

| 預測器 | mispredict rate |
|---|---|
| bimodal | 33.33% |
| gshare | 0.15% |

## 踩雷區

**雷 1：以為 gshare 一定比 bimodal 好。**
- 錯誤直覺：「加了 global history 資訊更多，只會更準」。
- 正確認識：對「只跟自己相關」的 branch（純迴圈計數），global history 是**雜訊**——它讓同一條 branch 的穩定行為被打散到不同 PHT 條目（因為 GHR 一直在變），反而學不穩。這正是 tournament 存在的理由：有些 branch local 更好。gshare 是**平均**比 bimodal 好，不是每條都好。本章 trace 特意設計成 correlation 極強，才有 33%→0.15% 的懸殊；換一段純迴圈 trace，兩者會很接近甚至 bimodal 略勝。

**雷 2：GHR 更新時機或索引 GHR 搞錯。**
- 錯誤直覺：「更新 PHT 就用 resolve 當下的 GHR」。
- 正確認識：要更新的是「當初做這次預測時用的那個 PHT 條目」，所以該用**預測當下的 GHR**。in-order 單分支在飛時兩者剛好相同（本課成立），但亂序 core 中間會有別的 branch 改 GHR，必須把預測時的 GHR 快照跟著 branch 帶下去。搞錯會更新到錯的計數器，越學越歪。

**雷 3：用 BTB 預測 return 的 target。**
- 錯誤直覺：「ret 也是跳轉，BTB 記上次 target 就好」。
- 正確認識：同一個函式被多處呼叫，每次 ret 的 target 不同，BTB 記的「上次 target」大概率是別的 caller 的——對 ret 幾乎必錯。return 要用 **RAS**，靠 call/ret 的 LIFO 配對直接算出正確 target。少了 RAS，函式呼叫密集的程式（幾乎所有程式）frontend 會被 return mispredict 拖垮。

**雷 4：把 tournament 的 chooser 當成「哪個準用哪個」的即時判斷。**
- 錯誤直覺：「chooser 每次看哪個對就選哪個」。
- 正確認識：預測發生在 IF 級，那時**還不知道哪個對**（要等 resolve）。chooser 是一張**學歷史**的 2-bit 表——它記「這條 branch 過去 local vs global 哪個比較常對」，用過去的統計在當下投票。它是「事後根據對錯調整偏好」，不是「當下看答案選」。這和飽和計數器學方向是同一種「用歷史賭當下」的思路。

## 進階延伸

- **gshare 的後繼者 TAGE**：現代高階 CPU（近十年的 Intel/AMD/ARM 大核）的方向預測主力是 **TAGE**（TAgged GEometric history length）。它同時用**多種長度**的 global history 索引多張帶 tag 的表，讓「需要長歷史才能區分的 branch」和「短歷史就夠的 branch」各取所需，命中率逼近理論極限。它是 gshare「單一固定歷史長度」思路的極致化。做完本課想再深入，TAGE 論文是下一站。
- **perceptron predictor**：另一條路線，用類似單層感知器的線性模型，把 global history 每個 bit 加權求和判方向。對「線性可分」的 correlation 特別強，且能用很長的歷史。AMD 早期大核用過。它證明分支預測本質上是個機器學習問題。
- **indirect branch 的 target 預測**：`jalr`（非 return 的那種，如 switch jump table、虛擬函式呼叫）target 會變，光 BTB 不夠，需要專門的 **indirect target predictor**（用 global history 索引 target，類似 gshare 但存的是 target 不是方向）。這是 C++ 虛函式、直譯器 dispatch 迴圈的效能關鍵。
- **預測器也是攻擊面**：Spectre v2 就是污染 BTB/間接分支預測器，誘導 victim 推測執行到攻擊者選的 gadget。分支預測器在核心/使用者、甚至跨行程間共享狀態，成了側通道與推測執行攻擊的溫床。這是硬體安全的大主題，本課不深挖，但你要知道「預測器記的歷史」本身是可被觀測、可被污染的。

## 本章重點整理

- 2-bit BHT 只看單條 branch 自己的歷史，抓不到 **branch 之間的相關性**（如 B3 由 B1/B2 決定）。修法是引入 **GHR（global history register）**。
- **gshare** = `PHT[PC 低位 ^ GHR]`。XOR 讓同一條 branch 在不同歷史情境落到不同計數器，抓到 correlation，且表大小維持 `2^h`（不像拼接爆炸）。
- 真跑對照：一段強相關 trace 上，bimodal mispredict 33.33%、gshare 0.15%——global history 把結構上抓不到的模式抓了出來。
- **tournament** = local + global(gshare) + **chooser**，chooser 學「這條 branch 哪個預測器準」自動路由，兩全其美。
- **RAS（return address stack）** 用 call/ret 的 LIFO 配對預測 `ret` 的 target，命中率 >95%；BTB 對 return 幾乎必錯。
- gshare 不是每條都贏 bimodal（純迴圈時 global history 是雜訊）；更新要用預測時的 GHR。

## 自我檢核

- [ ] 我能舉一個「B3 的結果由 B1/B2 決定」的相關性例子，說明 2-bit BHT 為何抓不到。
- [ ] 我能畫出 gshare 的 `PC ^ GHR` 索引流程，並解釋 XOR 相對「拼接」省在哪。
- [ ] 我能解釋本章 trace 為何讓 bimodal 33% 而 gshare 0.15%，以及那 6 次 miss 是什麼。
- [ ] 我能畫出 tournament 的三表結構，說清 chooser 學什麼、何時往哪偏。
- [ ] 我能解釋為什麼 BTB 對 `ret` 幾乎必錯，RAS 怎麼用 LIFO 配對修好它。
- [ ] 我能說出一個 gshare **輸給** bimodal 的情境，以及為什麼。

## 延伸閱讀

- **Scott McFarling, "Combining Branch Predictors" (WRL Technical Note TN-36, 1993)**：gshare 和 tournament（combining/chooser）的**原始論文**，本章兩個主角都出自這裡。讀第 2 節 gshare 的 XOR 動機、第 3 節 combining predictor 的 chooser 設計——短短十幾頁，是分支預測的必讀經典。
- **《Computer Architecture: A Quantitative Approach》(Hennessy & Patterson) 第 3.3 節「Reducing Branch Costs」的 correlating / tournament 段落**：把 (m,n) correlating predictor、tournament、以及 Alpha 21264 的實際設計講清楚，並有各種 predictor 在 SPEC 上的命中率對照圖。本章數字的真實 workload 版就在這。
- **André Seznec, "A New Case for the TAGE Branch Predictor" (MICRO 2011)**：想知道 gshare 之後業界走去哪，讀 TAGE。它是現代高階 CPU 方向預測的實質標準，把「多歷史長度 + tag」推到極致。先讀 intro 建立 geometric history 的直覺。
- **Agner Fog, "The microarchitecture of Intel, AMD and VIA CPUs"（microarchitecture manual）的 branch prediction 章節**：從**實測逆向**的角度看真實 CPU 的 BTB 大小、RAS 深度、預測器行為，以及它們如何影響你寫的 code 的效能。是把本章理論對回真硬體的最佳橋樑，也會告訴你哪些 code pattern 會打爆預測器。

gshare、tournament、RAS——方向和目標我們能猜得很準了。但「猜得準」到底替 CPI 省了多少？下一章我們把 performance counter 加進 testbench，實測 stall / flush / mispredict 各自對 CPI 貢獻多少，把「效能提升」從感覺變成數字。

→ [Ch 23 CPI 分析與 pipeline 效能建模](./23-cpi-analysis.md)
