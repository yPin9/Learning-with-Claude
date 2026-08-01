# 練習 C — 實作 BHT + BTB（或 gshare），量測 CPI 改善

> **目標**：把 Part 3 從「讀懂」變成「做出來、量得出來」。你會親手實作一個分支預測器（2-bit BHT + BTB，或進階的 gshare），接上一個帶 performance counter 的 pipeline 記帳 harness，跑同一段 benchmark，量出「靜態 not-taken vs 動態預測」的 CPI 改善，並解釋數字。做完你會有一個能量測、能對照、能歸因的完整實驗環境。
> **環境**：WSL + verilator 4.038 + g++。所有 CPI 皆真跑量測。
> **前置**：Ch 21（2-bit BHT / BTB）、Ch 22（gshare / GHR）、Ch 23（CPI 分解與 performance counter）。

## 這個練習在做什麼

你要交付三樣東西：

1. **一個分支預測器 RTL**：`bimodal.sv`（2-bit BHT）為基本要求；`gshare.sv`（加 global history）為進階要求。含 BTB 的完整版（`bpred.sv`）為挑戰。
2. **一個 CPI 量測 harness（C++ testbench）**：內含 pipeline 記帳邏輯（forwarding + load-use stall + branch flush，規則同 Ch 16–23）和 performance counter，把預測器接進來，跑 benchmark 數 cycle / instret / stall / flush。
3. **一份數字對照 + 歸因**：至少三種配置（static-NT / bimodal / gshare）的 CPI，並用 Ch 23 的分解方法解釋每個數字。

**為什麼這樣設計練習？** 直接把預測器焊進完整的 20 章 pipeline RTL、再想辦法量 CPI，會被無數 corner case 淹沒，抓不到重點。這個練習用**記帳 harness**隔離出「預測器 → flush → CPI」這條因果鏈，讓你專注在預測器本身和它對效能的影響。把預測器焊進真 pipeline 的完整整合，是 Ch 20/final project 的事；這裡先把「預測器怎麼影響 CPI」搞到能量測。

## 完整規格

### 預測器介面（兩組必守的 port）

所有預測器都遵守 Ch 21 約定的雙埠介面：

```
   查詢埠（IF 級，組合輸出，當拍就要用）：
     input  [31:0] pc_f            要查的 PC
     output        predict_taken   預測 taken?
     output [31:0] predict_target  若 taken 跳去哪（含 BTB 時才有意義）

   更新埠（ID 級 resolve 後，同步寫入）：
     input         update_en       這 cycle 有 branch resolve
     input  [31:0] update_pc        那條 branch 的 PC
     input         update_taken     實際 taken?
     input  [31:0] update_target    實際 target（含 BTB 時才用）
```

**硬性要求**：

- 查詢必須是**組合**（`assign`），更新必須是**同步**（`always_ff @(posedge clk)`）。搞反就是沒預測（晚一拍）。
- 2-bit 飽和計數器：taken +1 飽和於 11、not-taken -1 飽和於 00。**最高位決定方向**。
- reset 後計數器初始 `2'b01`（weakly NT），含 BTB 時 valid 全清 0（冷啟動 miss）。
- index 取 `pc[IDX+1:2]`（**跳過 byte offset 低 2 bit**）。
- 含 BTB 時**必須有 tag**（防別名），且**只在 taken 時記 target**。

### harness（CPI 量測）規格

記帳規則和 Ch 23 一致（**假設有 forwarding**，這是現代 pipeline 的常態）：

- **stall**：只有 **load-use**（前一條是 LOAD 且它的 rd 被這條的 rs1/rs2 用到）stall 1 cycle。其他 RAW 被 forwarding 消掉。
- **flush**：branch 在 ID resolve，`predict_taken != actual_taken` 就 flush 1 cycle。
- **counter**：`cycles`、`instret`、`stalls`、`flushes` 四個，`cycles = instret + 4 + stalls + flushes`（+4 = pipeline fill）。
- **CPI = cycles / instret**。

### benchmark 規格

至少要有一段含 **load-use** 和 **迴圈回跳 branch** 的程式軌跡（讓 stall 和 flush 都非零，才量得出兩者貢獻）。基本要求是單層迴圈；進階做**巢狀迴圈**（內外層 branch 規律不同，能凸顯預測器差異）。

## 測試計畫（你的實作要通過這些）

| # | 測什麼 | 期望 |
|---|---|---|
| T1 | 預測器狀態轉移 | 走完 00→01→10→11 飽和、遲滯（連錯兩次才翻）正確 |
| T2 | BTB tag 防別名（含 BTB 版） | 同 index 不同 tag 的 PC 不命中彼此 target |
| T3 | static-NT 基準 CPI | flush = taken branch 數（每個 taken 都猜錯） |
| T4 | 動態預測降 flush | bimodal/gshare 的 flush 遠低於 static-NT |
| T5 | stall 不受預測影響 | 三種配置 stalls 相同（預測不碰 data hazard） |
| T6 | CPI 歸因 | 能把 CPI 差拆到 flush 減少上 |

## 分段實作

分四階段，每階段都能獨立跑、獨立驗證。別想一次做完。

### 階段 1：2-bit bimodal 預測器 + 狀態轉移測試

先做最簡單的純 PC 索引 2-bit 預測器，用 Ch 21 的狀態轉移測試驗證飽和與遲滯。這是地基，先確保它對。

**你要寫**：`bimodal.sv`（介面如上，只有方向、無 BTB）+ 一個 testbench 走過所有狀態。

驗證點：從 weakly NT 開始，連兩次 taken 才翻成猜 T；從 strongly T 吃一次 NT 仍猜 T（遲滯）；連續 NT 飽和在 00 不 underflow。

### 階段 2：CPI 量測 harness + static-NT 基準

寫記帳 harness，先跑 **static-NT**（不接預測器，一律猜 NT），量出基準 CPI。這確立「沒有預測時 flush 有多痛」。

**你要寫**：一個 benchmark（指令陣列）+ 記帳迴圈 + performance counter 輸出。

驗證點：static-NT 的 flush 數 = benchmark 裡 taken branch 的數量（每個 taken 都被猜錯）。

### 階段 3：把 bimodal 接進 harness，對照 CPI

把階段 1 的 bimodal 接進階段 2 的 harness，跑同一 benchmark，對照 static-NT vs bimodal 的 CPI。

驗證點：bimodal 的 flush 遠低於 static-NT（迴圈回跳幾乎全同方向，熱身後穩定猜對）；stalls 兩者相同。

### 階段 4（進階）：gshare + 巢狀迴圈對照

實作 gshare（加 GHR + XOR index），做巢狀迴圈 benchmark，對照 static-NT / bimodal / gshare 三者。觀察 gshare 是否、以及何時勝過 bimodal——**這裡藏著 Ch 22 的重要一課**（見卡點提示）。

## 參考解

先自己做，卡住再看。每階段的參考解都真跑驗證過。

<details>
<summary>階段 1：bimodal.sv + 狀態轉移測試</summary>

`bimodal.sv`：

```systemverilog
// bimodal.sv — 純 PC 索引的 2-bit 飽和計數器
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
    assign predict_taken = pht[idx_f][1];      // 組合查詢
    integer i;
    always_ff @(posedge clk) begin             // 同步更新
        if (rst) for (i=0;i<N;i=i+1) pht[i] <= 2'b01;
        else if (update_en) begin
            if (update_taken) begin if (pht[idx_u]!=2'b11) pht[idx_u]<=pht[idx_u]+2'b01; end
            else               begin if (pht[idx_u]!=2'b00) pht[idx_u]<=pht[idx_u]-2'b01; end
        end
    end
endmodule
```

狀態轉移測試（節錄核心，完整版見 Ch 21 的 `bpred_tb.cpp`）：連兩次 taken 才翻、遲滯、飽和三個關鍵點都要覆蓋。Ch 21 的測試已完整走過這些並真跑 `ALL PASSED`，直接沿用即可（去掉 BTB 相關的 target/tag 檢查）。

</details>

<details>
<summary>階段 2+3：CPI 量測 harness（含 bimodal）</summary>

`practice_ref.cpp`（harness + benchmark + 三配置對照）：

```cpp
// practice_ref.cpp — 把預測器接上 pipeline 記帳模型，量 CPI
#include "Vbimodal.h"
#include "Vgshare.h"
#include "verilated.h"
#include <cstdint>
#include <cstdio>
#include <vector>

template <class DUT> static void tick(DUT *d){ d->clk=0; d->eval(); d->clk=1; d->eval(); }
template <class DUT> static void rst(DUT *d){ d->rst=1; d->update_en=0; tick(d); tick(d); d->rst=0; d->eval(); }

enum Kind { ALU, LOAD, BRANCH };
struct Insn { Kind kind; int rd, rs1, rs2; uint32_t pc; int br_taken; };

// d == nullptr 表示 static-NT（一律猜 NT）
template <class DUT>
static void measure(const char *name, DUT *d, const std::vector<Insn> &prog) {
    if (d) rst(d);
    long instret=(long)prog.size(), stalls=0, flushes=0;
    int prev_rd=-1, prev_kind=-1;
    for (const Insn &in : prog) {
        // forwarding：只 load-use stall 1
        bool loaduse = (prev_kind==LOAD) && (prev_rd>0) &&
                       (prev_rd==in.rs1 || prev_rd==in.rs2);
        if (loaduse) stalls += 1;
        if (in.kind==BRANCH) {
            int pred;
            if (!d) pred = 0;                          // static NT
            else { d->pc_f = in.pc; d->eval(); pred = d->predict_taken; }
            if (pred != in.br_taken) flushes += 1;     // mispredict → flush
            if (d) { d->update_en=1; d->update_pc=in.pc; d->update_taken=in.br_taken;
                     tick(d); d->update_en=0; d->eval(); }
        }
        prev_rd=in.rd; prev_kind=in.kind;
    }
    long cycles = instret + 4 + stalls + flushes;      // +4 = pipeline fill
    printf("%-16s cycles=%6ld instret=%5ld stalls=%5ld flushes=%5ld CPI=%.3f\n",
           name, cycles, instret, stalls, flushes, (double)cycles/instret);
}

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    // 巢狀迴圈：外層 M 輪，內層 K 次。內外層各一條回跳 branch。
    std::vector<Insn> prog;
    const int M = 50, K = 20;
    const uint32_t PC_INNER = 0x80000120, PC_OUTER = 0x80000160;
    for (int m=0;m<M;m++){
        for (int k=0;k<K;k++){
            prog.push_back({LOAD,5,10,-1,0x80000100,0});
            prog.push_back({ALU, 6, 5,-1,0x80000104,0});     // load-use（用剛 load 的 x5）
            prog.push_back({ALU, 7, 7, 6,0x80000108,0});
            prog.push_back({ALU,10,10,-1,0x8000010c,0});
            prog.push_back({BRANCH,-1,10,11,PC_INNER,(k!=K-1)?1:0}); // 內層回跳
        }
        prog.push_back({BRANCH,-1,12,13,PC_OUTER,(m!=M-1)?1:0});     // 外層回跳
    }
    measure<Vgshare>("static-NT", nullptr, prog);
    Vbimodal *bi = new Vbimodal; measure("bimodal", bi, prog); delete bi;
    Vgshare  *gs = new Vgshare;  measure("gshare",  gs, prog); delete gs;
    return 0;
}
```

build（兩個 top module 手動連結，同 Ch 22）：

```bash
verilator --cc bimodal.sv --Mdir obj_prac
verilator --cc gshare.sv  --Mdir obj_prac
cd obj_prac
make -f Vbimodal.mk Vbimodal__ALL.a
make -f Vgshare.mk  Vgshare__ALL.a
VINC=$(verilator --getenv VERILATOR_ROOT)/include
g++ -I. -I$VINC -c ../practice_ref.cpp -o practice_ref.o
g++ -c $VINC/verilated.cpp -o verilated_lib.o
g++ practice_ref.o verilated_lib.o Vgshare__ALL.a Vbimodal__ALL.a -o Vprac
./Vprac
```

真實輸出：

```
static-NT        cycles=  7053 instret= 5050 stalls= 1000 flushes=  999 CPI=1.397
bimodal          cycles=  6107 instret= 5050 stalls= 1000 flushes=   53 CPI=1.209
gshare           cycles=  6117 instret= 5050 stalls= 1000 flushes=   63 CPI=1.211
```

</details>

<details>
<summary>階段 4：gshare.sv</summary>

`gshare.sv`（GHR + XOR index，見 Ch 22）：

```systemverilog
// gshare.sv — global history XOR PC 索引 PHT
module gshare #(parameter HIST_BITS = 6)(
    input  logic clk, rst,
    input  logic [31:0] pc_f,
    output logic predict_taken,
    input  logic update_en,
    input  logic [31:0] update_pc,
    input  logic update_taken
);
    localparam N = (1 << HIST_BITS);
    logic [HIST_BITS-1:0] ghr;
    logic [1:0] pht [N-1:0];
    logic [HIST_BITS-1:0] idx_f, idx_u;
    assign idx_f = pc_f[HIST_BITS+1 : 2]      ^ ghr;
    assign idx_u = update_pc[HIST_BITS+1 : 2] ^ ghr;
    assign predict_taken = pht[idx_f][1];
    integer i;
    always_ff @(posedge clk) begin
        if (rst) begin ghr <= '0; for (i=0;i<N;i=i+1) pht[i] <= 2'b01; end
        else if (update_en) begin
            if (update_taken) begin if (pht[idx_u]!=2'b11) pht[idx_u]<=pht[idx_u]+2'b01; end
            else               begin if (pht[idx_u]!=2'b00) pht[idx_u]<=pht[idx_u]-2'b01; end
            ghr <= {ghr[HIST_BITS-2:0], update_taken};   // 移入最新結果
        end
    end
endmodule
```

</details>

<details>
<summary>結果解讀（三配置 CPI 歸因）</summary>

用 Ch 23 的分解讀這三行：

- **static-NT（CPI 1.397）**：flush = 999。benchmark 有 `50×20 + 50 = 1050` 條 branch，其中 taken 的是「內層前 19 次 × 50 輪 + 外層前 49 輪」= `19×50 + 49 = 999`。static 一律猜 NT，每個 taken 都猜錯，flush 剛好 999。stalls = 1000 是每輪內層那條 load-use（`50×20 = 1000`）。
- **bimodal（CPI 1.209）**：flush 從 999 崩到 **53**。迴圈回跳幾乎全同方向，2-bit 熱身後穩定猜 taken，只在每次迴圈退出（內層每 20 次錯 1 次 × 50、外層退出 1 次）附近猜錯。CPI 從 1.397 降到 1.209——**降的 0.188 幾乎全來自 flush 減少**（(999-53)/5050 ≈ 0.187）。stalls 不變（預測不碰 data hazard）。
- **gshare（CPI 1.211）**：flush = **63**，比 bimodal 的 53 **略差**！這不是 bug——見卡點提示的關鍵一課。

歸因鏈：`CPI 1.397 → 1.209`，這 0.188 的改善**全記在 flush 頭上**（stall 沒動）。這就是 Part 3 分支預測的量化價值。

</details>

## 卡點提示

**卡點 1：預測器接上去 flush 沒下降，甚至比 static 還多。**
- 檢查查詢是不是**組合**。若你把 `predict_taken` 寫進 `always_ff`，它會晚一拍——你在用「上一次查詢的結果」判這次，等於沒預測。查詢一定 `assign`。
- 檢查更新時機。resolve 後才 update，且 update 用的是**這條 branch 自己的 pc/taken**，別拿錯條。

**卡點 2：static-NT 的 flush 數對不上 taken branch 數。**
- static-NT 是「一律猜 NT」，所以每個 **taken** branch 都 mispredict、每個 **not-taken** branch 都猜對。flush 應該剛好等於 taken 的數量。若對不上，先數清楚 benchmark 裡到底幾條 taken（巢狀迴圈容易數錯——內層退出、外層退出都是 NT）。

**卡點 3（最重要）：gshare 竟然輸給 bimodal，是不是我寫錯了？**
- **沒寫錯，這是真結果，也是 Ch 22 最重要的一課。** 這個 benchmark 的 branch 是**簡單迴圈回跳**——它們**只跟自己的歷史相關**（連跳很多次然後退出），沒有「branch 之間互相相關」的模式。對這種 branch，gshare 的 **GHR 是雜訊**：它把同一條穩定的迴圈 branch，因為 GHR 一直在變，打散到不同 PHT 條目，每個條目都要重新熱身，反而多錯幾次。bimodal 只看 PC、條目穩定，對純迴圈更準。
- 這正是 Ch 22 踩雷區「以為 gshare 一定比 bimodal 好」的**活體驗證**。gshare 的優勢要在**有 cross-branch correlation** 的 workload 才顯現（見 Ch 22 那段 33.33% vs 0.15% 的 trace——那是特意設計 B 跟著 A 的相關性）。你這裡量到 gshare 略輸，恰恰證明你懂了：**預測器沒有絕對優劣，看 workload**。這也是 tournament predictor 存在的理由——讓每條 branch 用最適合它的預測器。

**卡點 4：兩個 top module 連結失敗（undefined reference）。**
- verilator 4.038 一個 `--exe` 只綁一個 top 的 makefile，另一個 module 的 `.o` 沒被包進去。照參考解那樣：兩個 module 各自 `make ...__ALL.a` 出靜態庫，再手動 `g++ ... Vgshare__ALL.a Vbimodal__ALL.a` 一起連。

## 延伸挑戰

做完基本要求後，挑一兩個往深處走：

1. **改用完整 BTB 版（`bpred.sv`）並量 target 命中**：把 Ch 21 帶 tag/valid/target 的 `bpred.sv` 接進 harness，除了方向 mispredict，額外統計 **BTB miss**（predict_taken 但 btb_hit=0，或 target 錯）。觀察冷啟動時 BTB 要幾次才熱。

2. **設計一段能讓 gshare 打敗 bimodal 的 benchmark**：模仿 Ch 22 的 correlated trace——造兩條 branch，讓第二條的 outcome 由第一條決定（`B_taken = !A_taken`）。跑你的 harness，證明在這種 workload 上 gshare 的 flush 明顯低於 bimodal。這會讓你真正理解「correlation」是什麼、gshare 何時值得。

3. **實作 RAS（return address stack）並對照**：造一段有函式呼叫的 trace（多處 call 同一函式，每次 ret 回不同 caller）。先用純 BTB 預測 ret 的 target，量 target mispredict；再加一個 8 深的 RAS（call push 返回位址、ret pop），對照 target 命中率。你會看到 BTB 對 return 幾乎必錯、RAS 幾乎全中——把 Ch 22 的 RAS 從概念變成數字。

4. **掃 predictor 大小 vs 命中率**：把 `IDX_BITS`/`HIST_BITS` 從 4 掃到 10，畫 mispredict rate 對表大小的曲線。你會看到收益遞減——超過某個大小，別名已經很少，再加表沒用。這是「多大的預測器才夠」的實測答案。

5. **把 penalty 改成深 pipeline**：把 flush penalty 從 1 改成 10（模擬 resolve 在第 11 級的深 pipeline，Ch 24），重跑三配置。觀察同樣的 mispredict 數下，CPI 差距被放大幾倍——親手驗證 Ch 24「深 pipeline 讓分支預測更值錢」的結論。

## 完成檢核

- [ ] `bimodal.sv` 通過狀態轉移測試（飽和 + 遲滯 + 連兩次才翻）。
- [ ] harness 的 `cycles = instret + 4 + stalls + flushes` 恆等式成立。
- [ ] static-NT 的 flush 數 = benchmark 裡 taken branch 的數量。
- [ ] bimodal / gshare 的 flush 遠低於 static-NT，且三配置 stalls 相同。
- [ ] 我能把 CPI 改善（1.397 → 1.209）**歸因**到 flush 減少，並算出數字對得上。
- [ ] 我能解釋為什麼在這個純迴圈 benchmark 上 gshare 略輸 bimodal（GHR 是雜訊），並說出 gshare 何時才會贏。
- [ ] （進階）我做出一段讓 gshare 勝出的 correlated benchmark，或實作 RAS 對照 return 預測。

做到這裡，你已經能**實作預測器、接進效能模型、量測並歸因 CPI**——這是效能微架構工程的核心迴圈。Part 4 我們進記憶體階層，cache miss 會在 CPI 分解裡加上一大坨 memory 貢獻，那時這套「量測 → 歸因 → 優化」的方法會發揮更大威力。

→ [Ch 25 為什麼要 cache：memory wall、locality](./25-why-cache.md)
