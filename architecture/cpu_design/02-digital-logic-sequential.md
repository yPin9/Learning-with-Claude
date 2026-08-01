# Ch 2 — 數位邏輯（二）：時序邏輯、flip-flop、時鐘與 timing

> **目標**：補上硬體世界「有記憶」的另一半——時序邏輯（sequential logic）。你會搞懂 latch 與 D flip-flop 的差別、時鐘與正緣（posedge）為什麼是硬體的心跳、`always_ff` 怎麼描述「記住一個值」、以及 setup/hold time 這對決定電路能不能正確存值的 timing 條件。最重要的是那個坑死所有新手的分野：**阻塞賦值 `=` 與非阻塞賦值 `<=` 在時序邏輯裡的關鍵差異**——我們會故意寫錯給你看，讓你親眼看到它塌掉。

## 為什麼需要記憶：組合邏輯動不了 CPU

Ch 1 的組合邏輯很強大，但它有個致命限制：**沒有記憶，輸入一變舊值就消失**。

想想 CPU 要做什麼。它要「這一拍算 `a+b`，把結果存進 `x1`，下一拍再拿 `x1` 去做別的事」。這裡有個「存起來、留到下一拍」——組合邏輯做不到。純組合邏輯就是一團「輸入流到輸出」的水管，關掉輸入什麼都不剩。

CPU 的本質是「一個狀態機，每個時鐘週期從當前狀態推進到下一個狀態」。當前狀態是什麼？是所有暫存器的值、PC 指到哪、記憶體內容。這些**狀態必須被記住**，跨越一個又一個時鐘週期。負責記住的元件，就是時序邏輯的主角——**flip-flop（正反器）**。

先建立心智圖。時序電路的標準結構是「組合邏輯算下一個狀態，flip-flop 在時鐘上緣把它存下來」：

```
          ┌───────────────────────────────┐
          │                               │
   輸入 ──►│  組合邏輯   ├─ next ─►│ D  Q ├─┴─► 目前狀態（輸出）
          │ （算下一步） │        │  FF  │
          └─────────────┘        └──▲───┘
                                    │
                                   clk（時鐘：每個上緣「拍一下」，把 next 記進 Q）
```

組合邏輯負責「算」，flip-flop 負責「在對的時間記住」。時鐘每跳一次上緣，狀態就前進一步。**這個迴路就是 CPU 的骨架**——Ch 1 的 ALU 是那團組合邏輯，暫存器是那些 flip-flop，時鐘是心跳。

## 時鐘與正緣：硬體的心跳

**時鐘（clock）** 是一條週期性 0/1 跳動的方波：0、1、0、1……固定頻率。它不帶資料，它的唯一作用是**同步**——告訴所有 flip-flop「現在，一起把新值記下來」。

一個時鐘週期有兩個「邊緣（edge）」：0→1 是**上緣 / 正緣（rising edge / posedge）**，1→0 是**下緣（falling edge）**。絕大多數設計（包含本課全部）用正緣觸發：flip-flop 只在 clk 從 0 跳到 1 的那一瞬間抓取輸入。其他時候輸入怎麼變它都不理。

```
       ┌──┐  ┌──┐  ┌──┐  ┌──┐
 clk ──┘  └──┘  └──┘  └──┘  └──
       ▲     ▲     ▲     ▲
     posedge posedge ...  ← 只有這些瞬間，flip-flop 才「拍」一下記值
```

這個「只在上緣抓值」的特性極其重要。它讓整個系統同步：不管組合邏輯算得多亂、訊號中途怎麼跳，只要在下一個上緣**之前**穩定下來，flip-flop 抓到的就是正確的最終值。中間的雜訊（glitch）被自動濾掉了。

## Latch vs D flip-flop：兩種記憶，一個能用一個是雷

「記住一個值」的元件有兩類，新手常搞混，但它們天差地遠。

**latch（鎖存器）** 是**電平敏感（level-sensitive）**的：當 enable 是高電平時，輸出「透明」地跟著輸入變；enable 一低，就鎖住當前值。問題是「enable 高的整段時間」輸入都能穿透，時序難以掌控，容易產生 glitch 和 timing 問題。**latch 幾乎總是設計失誤的產物**（Ch 1、Ch 4 講的「意外推斷 latch」就是這個）。你極少會**故意**要 latch。

**D flip-flop（DFF，D 型正反器）** 是**邊緣敏感（edge-sensitive）**的：只在時鐘上緣的那一瞬間抓取輸入 D，存進 Q，然後保持到下一個上緣。中間輸入怎麼變都不影響。這才是我們要的——乾淨、可預測、好算 timing。**本課的「暫存器」全部是 D flip-flop。**

一句話記住差別：**latch 是「門開著就一直進」，DFF 是「門只在上緣開一瞬間」**。前者難管，後者好管。

## always_ff：描述一個 D flip-flop

在 SV 裡，一個 D flip-flop 就是這樣寫：

```systemverilog
module dff (input logic clk, input logic rst, input logic d, output logic q);
    always_ff @(posedge clk) begin
        if (rst) q <= 1'b0;    // 同步 reset：上緣時若 rst 高則歸零
        else     q <= d;       // 否則把 d 記進 q
    end
endmodule
```

拆解：

- `always_ff` 是 SV 專門用來寫時序邏輯的區塊，明確宣告「這是 flip-flop」。（舊寫法 `always @(posedge clk)` 也行，但 `always_ff` 讓工具幫你檢查，更安全。）
- `@(posedge clk)`：sensitivity list，**只在 clk 上緣**執行區塊內容。這就是「邊緣觸發」。
- `q <= d`：用**非阻塞賦值 `<=`**（下一節詳解為什麼一定要用它）。意思是「在這個上緣，把 d 的值排程給 q」。
- reset：這裡是**同步 reset**——只有在上緣、且 rst 為高時才歸零。本課約定用同步、active-high reset（訊號名 `rst`），跟課程約定一致。

驗證「只在上緣抓值」這個核心特性。testbench 讓 d 在拍與拍之間變化，看 q 是否只在上緣更新：

```cpp
#include "Vdff.h"
#include "verilated.h"
#include <cstdio>
int main(int c, char** v) {
    Verilated::commandArgs(c, v);
    Vdff* t = new Vdff;
    auto tick = [&]() { t->clk=0; t->eval(); t->clk=1; t->eval(); };
    t->rst = 1; t->d = 0; tick(); t->rst = 0;
    int dseq[] = {1, 1, 0, 1};
    printf("d->q per edge: ");
    for (int i = 0; i < 4; i++) {
        t->d = dseq[i]; tick();
        printf("(d=%d q=%d) ", dseq[i], t->q);
    }
    printf("\n"); delete t; return 0;
}
```

實際輸出（真跑）：

```
d->q per edge: (d=1 q=1) (d=1 q=1) (d=0 q=0) (d=1 q=1) 
```

每個上緣，q 抓到當下的 d。這裡因為我們在 tick 前設 d、tick 時發生上緣，所以看起來 q 立刻跟上——關鍵是**它是在上緣那一瞬間抓的**，不是連續跟著 d 變。把 d 在上緣「之後」才改，這一拍的 q 不會動，要等下一個上緣。這正是 flip-flop 和 latch 的分野。

## 阻塞 vs 非阻塞：新手殺手，故意寫錯給你看

這是整章、甚至整個 Part 0 最關鍵的一節。搞錯它，你的 CPU 會用「編得過、模擬跑錯、還很難看出為什麼」的方式壞掉。

SV 有兩種賦值：

- **阻塞賦值（blocking）`=`**：像 C 的 `=`。**立刻**算出右邊、**立刻**更新左邊，然後才執行下一行。同一個區塊內，後面的行讀到的是「已經被更新的新值」。
- **非阻塞賦值（non-blocking）`<=`**：先把所有右邊的值**同時**算好（用的都是「這個上緣之前的舊值」），區塊全跑完後才**一起**更新左邊。

規則（背起來，沒有例外）：**時序邏輯（`always_ff`）用 `<=`，組合邏輯（`always_comb`）用 `=`。**

為什麼？因為真實的 flip-flop 是這樣運作的：在上緣那一瞬間，**所有** flip-flop 同時讀取自己的輸入（都是上緣前的值），然後同時更新輸出。`<=` 精確地模擬這個「同時讀舊值、同時更新」的行為。`=` 則是「一個接一個、讀新值」，那是組合邏輯的行為，用在 flip-flop 上會模擬出錯誤的結果。

**故意寫錯：一個 4-bit 移位暫存器（shift register）**。移位暫存器是「每拍把資料往左推一位，最左邊擠出去、最右邊塞新的 `din`」。正確版用 `<=`：

```systemverilog
module shift_ok (input logic clk, input logic rst, input logic din, output logic [3:0] q);
    always_ff @(posedge clk) begin
        if (rst) q <= 4'd0;
        else     q <= {q[2:0], din};   // 左移一位，din 進最低位
    end
endmodule
```

錯誤版：把四級拆開，用**阻塞 `=`** 串起來（這是新手很自然會犯的寫法）：

```systemverilog
module shift_bug (input logic clk, input logic rst, input logic din, output logic [3:0] q);
    logic q0, q1, q2, q3;
    always_ff @(posedge clk) begin
        if (rst) begin q0=0; q1=0; q2=0; q3=0; end
        else begin
            q0 = din;   // 阻塞：q0 立刻變成 din
            q1 = q0;    // 讀到的是「剛剛更新的 q0」= din，不是上一拍的 q0！
            q2 = q1;    // 連鎖：q2 也 = din
            q3 = q2;    // q3 也 = din
        end
    end
    assign q = {q3, q2, q1, q0};
endmodule
```

錯在哪？我們想要的是「這一拍每一級接收**上一拍**前一級的值」（真正的移位）。但阻塞 `=` 是「立刻更新、往下讀新值」，所以 `q0=din` 之後，`q1=q0` 讀到的是**剛被改掉的 q0**（也就是 din），一路連鎖——四級全部塌成同一個值 din。移位暫存器變成「四個 bit 一起等於 din」。

同一組輸入（依序送 din = 1, 0, 1, 1）餵給兩個版本：

```cpp
// tb_ok.cpp（shift_ok）與 tb_bug.cpp（shift_bug）結構相同，只換 module 名
int seq[4] = {1, 0, 1, 1};
t->rst = 1; t->din = 0; tick(); t->rst = 0;
for (int i = 0; i < 4; i++) { t->din = seq[i]; tick(); printf("%X ", t->q); }
```

實際輸出（真跑，兩個各自 verilate）：

```
shift_ok : 1 2 5 B 
shift_bug: F 0 F F 
```

看 `shift_ok`：`1`(0001) → `2`(0010) → `5`(0101) → `B`(1011)。資料確實一位一位往左推，`din` 從右邊進。這是正確的移位。

再看 `shift_bug`：din=1 時 q=`F`(1111，四位全 1)、din=0 時 q=`0`(0000)、din=1 又全 1……**四個 bit 永遠一起等於 din**。移位功能完全消失，塌成一個 1-bit 的並聯 DFF。

這就是阻塞 vs 非阻塞的災難級差異。code 編得過、模擬跑得動、沒有任何錯誤訊息——它就是**默默算錯**。而且錯得很隱蔽：如果你的 CPU pipeline 用錯了，可能十條指令裡才錯一條，你會 debug 到天亮。**規則就一句：`always_ff` 一律 `<=`，永遠不要在時序區塊裡對狀態用 `=`。** 背下來，比什麼都值錢。

## setup / hold time：flip-flop 存值的物理條件

flip-flop 在上緣抓值，但它不是「瞬間」抓的——真實電路裡，輸入 D 必須在上緣**前後一小段時間內保持穩定**，flip-flop 才能正確存進去。兩個條件：

- **setup time（建立時間）**：上緣「之前」，D 必須穩定至少這麼久。
- **hold time（保持時間）**：上緣「之後」，D 還要再穩定這麼久。

```
              setup    hold
             ├────┤   ├────┤
   D ────────╳═══════════════╳──────   D 在這段窗口內必須穩定
                     ▲
                  posedge clk
```

若 D 在這個窗口內還在變（違反 setup 或 hold），flip-flop 可能存進一個「介於 0 和 1 之間」的不確定值——這叫 **metastability（亞穩態）**，是真實硬體的災難。

這對「時鐘週期要多長」的意義是什麼？回顧 Ch 1 的關鍵路徑。組合邏輯算出 next 值需要時間（關鍵路徑延遲），這個值還必須在下一個上緣「前 setup time」就穩定好。所以：

```
   時鐘週期 ≥ 關鍵路徑延遲 + setup time（+ 一點時鐘偏移等餘量）
```

這就是「關鍵路徑越長、時脈越低」的完整版。**verilator 是功能模擬，不算真實延遲，所以 setup/hold 違反它抓不到**——這些是合成後靜態時序分析（STA）的事（Part 6 Ch 38）。但你現在就要有這個直覺：flip-flop 要正確存值，輸入得在上緣前後穩住。

## 動手：4-bit counter 與波形

把 flip-flop 拿來做一個會「記住並累加」的 counter，順便產波形看它一拍拍爬升：

```systemverilog
module counter (input logic clk, input logic rst, output logic [3:0] q);
    always_ff @(posedge clk) begin
        if (rst) q <= 4'd0;
        else     q <= q + 4'd1;    // q 依賴自己的舊值 → 需要記憶 → 時序邏輯
    end
endmodule
```

注意 `q <= q + 1`：右邊的 `q` 是**上一拍的舊值**，左邊 `q` 是這一拍要存的新值。因為用 `<=`，這裡「讀舊值、算、存新值」乾淨俐落。如果誤用 `=`（`q = q + 1`）在單一 counter 上其實結果相同（只有一級、無連鎖），但這是巧合——一旦有多個互相依賴的狀態就會出事，所以永遠 `<=`，不賭這種巧合。

testbench 加 trace 產 `.vcd`：

```cpp
#include "Vcounter.h"
#include "verilated.h"
#include "verilated_vcd_c.h"
#include <cstdio>
int main(int c, char** v) {
    Verilated::commandArgs(c, v);
    Verilated::traceEverOn(true);
    Vcounter* t = new Vcounter; VerilatedVcdC* tf = new VerilatedVcdC;
    t->trace(tf, 99); tf->open("counter.vcd");
    vluint64_t tm = 0;
    auto tick = [&]() {
        t->clk=0; t->eval(); tf->dump(tm++);
        t->clk=1; t->eval(); tf->dump(tm++);
    };
    t->rst=1; tick(); t->rst=0;
    for (int i = 0; i < 6; i++) tick();
    printf("counter q=%d wrote counter.vcd\n", t->q);
    tf->close(); delete t; return 0;
}
```

實際輸出（真跑）：

```
counter q=6 wrote counter.vcd
```

reset 後跑 6 拍，`q=6`。開 `gtkwave counter.vcd`，把 `clk` 和 `q` 拖進去，你會看到 `q` 在每個 clk 上緣階梯狀 +1：0→1→2→3→4→5→6。**注意 q 的每次變化都精準對齊 clk 上緣**——這視覺上就是「flip-flop 只在上緣更新」的鐵證。這種「訊號對齊時鐘上緣」的畫面，是你之後看 CPU 波形時判斷「這條線是不是暫存器輸出」的直覺依據。

## 對比：latch / DFF / always_comb 一次看清

| 元件 / 寫法 | 觸發方式 | SV | 賦值 | 用途 | 危險度 |
|---|---|---|---|---|---|
| 組合邏輯 | 無時鐘，輸入變就算 | `always_comb` | `=` | 計算（ALU、MUX） | 漏賦值 → 意外 latch |
| D flip-flop | 邊緣（posedge） | `always_ff` | `<=` | 暫存器、狀態、counter | 誤用 `=` → 邏輯塌掉 |
| latch | 電平（level） | （通常是誤推斷出來的） | — | 極少故意用 | 幾乎總是 bug |

心法：**你想要的時序元件永遠是 DFF（`always_ff` + `<=`）。看到 latch 出現，先當它是 bug。組合用 `=`、時序用 `<=`，兩者絕不混用。**

## 踩雷集錦

1. **「在 `always_ff` 裡用 `=` 也編得過，應該沒差」** — 錯誤直覺：以為 `=` 和 `<=` 只是風格差異。正確認識：它們模擬出**完全不同的電路**。`shift_bug` 就是鐵證——`=` 讓四級移位塌成一個值，而且沒有任何錯誤訊息，默默算錯。時序邏輯永遠用 `<=`，這不是建議，是規則。

2. **「latch 也是記憶元件，跟 flip-flop 差不多」** — 錯誤直覺：把 latch 當成 flip-flop 的近親隨便用。正確認識：latch 是電平敏感（門開著就一直透明），DFF 是邊緣敏感（上緣一瞬間）。latch 的 timing 極難掌控、易生 glitch，在同步設計裡幾乎總是意外推斷出來的 bug。本課要的記憶元件一律是 DFF。

3. **「時鐘會帶資料 / 時鐘越快 CPU 一定越好」** — 錯誤直覺：以為時鐘是某種資料訊號、或以為時脈可以無限拉高。正確認識：時鐘只帶「同步」不帶資料；而時鐘週期有下限——必須 ≥ 關鍵路徑延遲 + setup time。拉太快，flip-flop 抓到還沒穩定的值，電路就錯了（或亞穩態）。速度是被關鍵路徑鎖死的，不是想快就快。

4. **「`q <= q + 1` 裡兩個 q 是同一個值」** — 錯誤直覺：把它讀成 C 的 `q = q + 1`（讀新值）。正確認識：因為是 `<=`，右邊的 `q` 是**這個上緣之前的舊值**，左邊是要在這個上緣存進去的新值。所有 `<=` 的右邊都讀「上一拍」的值，全部區塊跑完才一起更新。這正是它能正確模擬多個 flip-flop「同時更新」的原因。

## 本章重點整理

- 時序邏輯 = 有記憶，輸出取決於當下輸入 + 過去狀態，需要時鐘。CPU 的狀態（暫存器、PC）全靠它跨週期保存。
- 標準結構：組合邏輯算 next → flip-flop 在上緣存下來 → 成為新狀態。這個迴路就是 CPU 骨架。
- 時鐘是週期方波，只帶同步不帶資料。flip-flop 只在正緣（0→1）抓值。
- latch（電平敏感，透明門）幾乎總是 bug；D flip-flop（邊緣敏感，上緣一瞬間）才是要用的記憶元件。SV 用 `always_ff @(posedge clk)` 描述。
- **時序用 `<=`、組合用 `=`，絕不混用。** `shift_bug` 示範了誤用 `=` 讓移位暫存器塌掉、還默默算錯。這是新手最貴的一課。
- setup/hold time：D 必須在上緣前後穩定，否則亞穩態。時鐘週期 ≥ 關鍵路徑延遲 + setup。verilator 抓不到 timing 違反，那是 STA 的事。
- counter 真跑得 `q=6`，波形上 q 每次變化都對齊 clk 上緣——這是「暫存器輸出」的視覺特徵。

## 自我檢核

- [ ] 我能不看講義，說出組合邏輯用哪種賦值、時序邏輯用哪種，以及為什麼不能混。
- [ ] 我能解釋 `shift_bug` 為什麼會塌成「四個 bit 都等於 din」，講清楚阻塞 `=` 的連鎖效應。
- [ ] 我能講出 latch 和 D flip-flop 的核心差別（電平 vs 邊緣），以及為什麼 latch 通常是 bug。
- [ ] 我能說明時鐘的作用（同步、不帶資料），以及為什麼 flip-flop 只在上緣抓值能濾掉 glitch。
- [ ] 我能解釋 setup/hold time 是什麼，以及它和「時鐘週期下限 / 關鍵路徑」的關係。
- [ ] 我能講出 `q <= q + 1` 裡右邊的 q 是哪一拍的值，以及為什麼 `<=` 能正確模擬多個 flip-flop 同時更新。

## 延伸閱讀

- **《Digital Design and Computer Architecture, RISC-V Edition》(Harris & Harris) 第 3 章**：時序邏輯的紙本主線。latch/flip-flop 的電路級原理、有限狀態機（下一章）、以及 timing（setup/hold、時鐘偏移 clock skew、亞穩態）都在這章講得很透。本章的 setup/hold 直覺，這裡有完整的時序圖與計算。
- **nandland.com — "Blocking vs Nonblocking in Verilog"**：專門講這個新手殺手的短文。它用另一組例子重現「用錯賦值導致邏輯塌掉」，和本章的 `shift_bug` 互為印證。若你對這條規則還有一絲不確定，讀它再鞏固一次——這是硬體設計最值得徹底內化的一課。
- **HDLBits — Sequential Logic 章節**：從 `Dff`、`Dff8`（8-bit 暫存器）一路到 `Shift4`、各種 counter 的互動練習。特別做 shift register 那幾題，親手體驗 `<=` 的正確行為。把 `always_ff` 練成反射動作。
- **Cliff Cummings, "Nonblocking Assignments in Verilog Synthesis, Coding Styles That Kill!"（SNUG 2000 論文，網路可搜到 PDF）**：業界最經典的一篇「為什麼一定要這樣用賦值」的技術論文。稍進階，但把阻塞/非阻塞的模擬語意講到骨子裡。等你被賦值坑過一次，讀它會有醍醐灌頂之感。

Ch 2 我們有了記憶——flip-flop 讓電路能一拍一拍推進、記住狀態。但「狀態要怎麼隨輸入有規律地轉換」還沒講。下一章的**有限狀態機（FSM）**就是答案：它把「當前狀態 + 輸入 → 下一個狀態 + 輸出」這件事系統化。而這正是 CPU 控制單元（control unit）的本質——你會發現，「取指、解碼、執行」的控制流程，骨子裡就是一台 FSM。

→ [Ch 3 有限狀態機（FSM）：控制器的本質](./03-finite-state-machine.md)
