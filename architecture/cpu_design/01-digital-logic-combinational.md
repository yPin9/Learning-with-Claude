# Ch 1 — 數位邏輯（一）：boolean、邏輯閘、組合元件

> **目標**：把「組合邏輯（combinational logic）」這半個硬體世界徹底搞懂——輸入一變、輸出立刻跟著算出來、沒有任何記憶。你會從一個邏輯閘出發，用 boolean 代數與真值表理解它，學卡諾圖化簡，再用 `always_comb` 親手堆出半加器 → 全加器 → 4-bit 加法器 → MUX → 一塊簡易 ALU，每一個都 verilate 跑過驗證。最後你會懂「關鍵路徑（critical path）」——決定電路能跑多快的那條線。

## 為什麼要先分「有沒有記憶」

軟體人第一次看硬體，最大的認知斷層是這個：**電路分兩種，一種有記憶，一種沒有。**

沒記憶的叫**組合邏輯**：輸出只由「當下的輸入」決定。輸入變，輸出（經過一點點傳播延遲後）立刻跟著變。它像一個純函數 `y = f(a, b)`——同樣的輸入永遠給同樣的輸出，不管過去發生過什麼。加法器、比較器、多工器，全是組合邏輯。

有記憶的叫**時序邏輯（sequential logic）**：輸出還跟「過去的狀態」有關。它需要一個時鐘來決定「什麼時候把新值記下來」。counter、暫存器就是。這是 Ch 2 的主題。

為什麼先學組合？因為 CPU 裡「計算」的部分——ALU 算 `a+b`、比較器判斷 branch 要不要跳、decoder 把指令拆成控制訊號——**全都是組合邏輯**。時序邏輯只負責「在對的時間把結果存起來」。你把組合邏輯這半邊搞紮實，CPU 的 datapath 就懂了一大半。

先建立這張直覺圖。組合邏輯就是一團「輸入進去，值往右流，流到底就是輸出」的無記憶網路：

```
   a ──►┐
        │ 一團邏輯閘        傳播延遲後
   b ──►│（無時鐘、無記憶）──────────► y = f(a, b)
        │                              輸入一變，y 立刻重算
   c ──►┘
```

沒有時鐘、沒有 reset、沒有「上一拍」。就是輸入到輸出的一條「因果水管」。

## Boolean 代數：硬體的算術

數位電路裡只有兩個值：0 和 1（低電壓 / 高電壓）。所有運算都建立在三個基本 boolean 運算上：

- **AND**（`&`）：兩個都 1 才 1。像串聯開關，全通才通。
- **OR**（`|`）：有一個 1 就 1。像並聯開關，任一通就通。
- **NOT**（`~`）：反相，0 變 1、1 變 0。

由它們組出兩個常用的：**XOR**（`^`，相異為 1）、**NAND/NOR**（AND/OR 再反相）。

描述一個組合邏輯有兩種等價方式。**真值表（truth table）**：把所有輸入組合列出來，寫下每種組合的輸出。**boolean 運算式**：用 `&`/`|`/`~` 寫成公式。兩者可以互相轉換。

以 XOR 為例。真值表：

| a | b | a^b |
|---|---|-----|
| 0 | 0 | 0 |
| 0 | 1 | 1 |
| 1 | 0 | 1 |
| 1 | 1 | 0 |

boolean 式：`a^b = (~a & b) | (a & ~b)`（「a 是 0 且 b 是 1」或「a 是 1 且 b 是 0」）。這兩個描述同一個電路。真值表是「窮舉」，boolean 式是「公式」。

有幾條化簡用得到的定律（背起來，之後手推電路常用）：

- 交換／結合：`a&b = b&a`，`(a&b)&c = a&(b&c)`（OR 同理）。
- 分配：`a & (b|c) = (a&b) | (a&c)`。
- 吸收：`a | (a&b) = a`、`a & (a|b) = a`。
- **De Morgan**：`~(a&b) = ~a | ~b`、`~(a|b) = ~a & ~b`。這條最重要——它讓你把 AND 換成 OR（反之亦然），是化簡和「全用 NAND 實作」的關鍵。

## 卡諾圖：把式子變簡單

同一個電路可以有很多等價的 boolean 式，但**閘用得越少、越快越省**。手動化簡容易出錯，卡諾圖（Karnaugh map，K-map）是一個視覺化的化簡工具。

概念：把真值表畫成一個格子圖，讓「只差一個變數」的組合相鄰。相鄰的 1 可以圈在一起消掉那個變數。舉例，這個函數 `f(a,b,c)`：

```
        bc=00  bc=01  bc=11  bc=10
a=0  [   0      1      1      0   ]
a=1  [   0      1      1      0   ]
```

上下兩排一模一樣（`a` 不影響結果），中間四個 1 圈成一塊 → 只跟 `b` 有關 → `f = b`。原本可能寫成一長串 `(~a&~b&c)|(~a&b&c)|...`，化簡後就是 `b`。少了一堆閘。

**但實務上你幾乎不會手畫卡諾圖。** 現代綜合工具（synthesis tool）會自動幫你化簡到最佳。你寫 `always_comb y = ...;`，工具負責變成最少的閘。卡諾圖的價值是**建立直覺**：讓你懂「為什麼工具能把我寫的複雜式子變簡單」、以及「相鄰項可以合併」這件事的本質。這門課我們基本上放手讓 verilator/綜合工具化簡，你只要會寫清楚的行為描述。

## 邏輯閘與 always_comb

在 SV 裡描述組合邏輯，最直接的方式是 `assign`（連續賦值）或 `always_comb`（組合 always 區塊）。先看幾個閘：

```systemverilog
module gates (input logic a, input logic b, output logic g_and, output logic g_or,
              output logic g_xor, output logic g_nand);
    assign g_and  = a & b;
    assign g_or   = a | b;
    assign g_xor  = a ^ b;
    assign g_nand = ~(a & b);
endmodule
```

`assign` 適合單一運算式。邏輯複雜（多分支、case）時用 `always_comb`——它是一個「每當輸入變、就重跑一次」的區塊。`always_comb` 是 SV 的組合邏輯專用寫法，比舊的 `always @(*)` 好：它會自動涵蓋所有輸入到 sensitivity list，還會在你不小心漏賦值（會意外變成 latch）時警告你。這個雷 Ch 4 深講，現在記住：**組合邏輯用 `always_comb`，每條路徑都要給輸出賦值。**

## 動手一：半加器 → 全加器 → 4-bit 加法器

加法器是 CPU 最核心的組合元件（ALU 的心臟）。我們從最小單位一路堆上去，這是硬體「模組化組合」的經典示範。

**半加器（half adder）**：加兩個 1-bit，輸出 sum 和 carry。真值表：

| a | b | sum | carry |
|---|---|-----|-------|
| 0 | 0 | 0 | 0 |
| 0 | 1 | 1 | 0 |
| 1 | 0 | 1 | 0 |
| 1 | 1 | 0 | 1 |

看出來了嗎？`sum = a^b`（XOR），`carry = a&b`（AND）。這就是把真值表化成 boolean 式。

**全加器（full adder）**：多吃一個進位輸入 `cin`（加三個 1-bit）。它可以用兩個半加器 + 一個 OR 組出來。

**4-bit ripple-carry adder**：把四個全加器串起來，每一位的 carry 進到下一位——像十進位直式加法那樣「進位往左傳」。

一次寫完：

```systemverilog
// 半加器
module half_adder (input logic a, input logic b, output logic sum, output logic carry);
    always_comb begin
        sum   = a ^ b;
        carry = a & b;
    end
endmodule

// 全加器：用兩個半加器 + OR
module full_adder (input logic a, input logic b, input logic cin,
                   output logic sum, output logic cout);
    logic s1, c1, c2;
    half_adder ha0 (.a(a),  .b(b),   .sum(s1),  .carry(c1));
    half_adder ha1 (.a(s1), .b(cin), .sum(sum), .carry(c2));
    assign cout = c1 | c2;
endmodule

// 4-bit ripple-carry adder：四個全加器，carry 一路往左傳
module adder4 (input logic [3:0] a, input logic [3:0] b, input logic cin,
               output logic [3:0] sum, output logic cout);
    logic [3:0] c;
    full_adder fa0 (.a(a[0]), .b(b[0]), .cin(cin),  .sum(sum[0]), .cout(c[0]));
    full_adder fa1 (.a(a[1]), .b(b[1]), .cin(c[0]), .sum(sum[1]), .cout(c[1]));
    full_adder fa2 (.a(a[2]), .b(b[2]), .cin(c[1]), .sum(sum[2]), .cout(c[2]));
    full_adder fa3 (.a(a[3]), .b(b[3]), .cin(c[2]), .sum(sum[3]), .cout(cout));
endmodule
```

這裡出現一個關鍵語法：**module 實例化（instantiation）**。`half_adder ha0 (.a(a), ...)` 是「放一個 half_adder 進來，取名 ha0，把它的 port 接到這些線」。`.a(a)` 是「這個實例的 port `a`，接到外層的線 `a`」（named port connection，強烈建議永遠用這種，別用位置對應）。這就是硬體的「函數呼叫」——但它不是「呼叫」，而是**放一個實體電路進去**。四個 `full_adder` 就是四塊真的加法器電路並排。

testbench（C++，窮舉全部 256 種輸入驗證）：

```cpp
#include "Vadder4.h"
#include "verilated.h"
#include <cstdio>

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    Vadder4* top = new Vadder4;
    int fails = 0;
    for (int a = 0; a < 16; a++)
      for (int b = 0; b < 16; b++) {
        top->a = a; top->b = b; top->cin = 0; top->eval();
        int got = (top->cout << 4) | top->sum;    // 5-bit 結果
        int exp = a + b;
        if (got != exp) { printf("FAIL %d+%d got %d exp %d\n", a,b,got,exp); fails++; }
      }
    printf("adder4: 256 cases, fails=%d\n", fails);

    // 邊界：4-bit 加到爆表
    top->a = 7; top->b = 8; top->cin = 1; top->eval();
    printf("7+8+cin1 = sum=%d cout=%d\n", top->sum, top->cout);

    delete top; return 0;
}
```

跑（注意這裡要加 `-Wno-UNOPTFLAT`，下面解釋）：

```bash
verilator --cc alu_blocks.sv --top-module adder4 -Wno-UNOPTFLAT --exe tb.cpp --Mdir obj_dir
make -C obj_dir -f Vadder4.mk Vadder4
./obj_dir/Vadder4
```

實際輸出（真跑）：

```
adder4: 256 cases, fails=0
7+8+cin1 = sum=0 cout=1
```

256 種輸入組合全對。邊界那組 `7+8+1 = 16`，但 4-bit 的 sum 只能裝 0–15，所以 `sum=0`（16 mod 16）、進位跑到 `cout=1`。**這就是固定位元寬度的溢位**——16 = `1_0000`，低 4 位是 `0000`（sum），第 5 位 `1` 就是 cout。跟 Ch 0 counter 數到爆一模一樣的道理。CPU 的 ALU 靠這個 cout（和其他旗標）判斷有沒有溢位。

### 失敗案例：那個 UNOPTFLAT 警告

上面刻意加了 `-Wno-UNOPTFLAT`。如果不加，verilator 4.038 會直接報錯停下來：

```
%Warning-UNOPTFLAT: alu_blocks.sv:21:17: Signal unoptimizable:
    Feedback to clock or circular logic: 'adder4.c'
```

這是**假警報**，但值得懂為什麼。verilator 把 `c[3:0]` 看成一整條線，發現「`c` 有些 bit 被讀、有些被寫，而且互相牽連」，就懷疑有組合迴路（combinational loop，一條線繞回自己，會震盪，是真正的 bug）。但我們的 ripple carry 其實是 `c[0]→c[1]→c[2]` 線性往前傳，**沒有迴路**——只是 verilator 以 vector 為單位分析，看不出來。加 `-Wno-UNOPTFLAT` 告訴它「我確定沒迴路，別煩我」。

這帶出一個重要區分：**真正的組合迴路是嚴重錯誤**（例如 `assign x = x & y;`，x 依賴自己，會邏輯震盪或鎖死）。verilator 的 UNOPTFLAT 有時是真迴路警告、有時是這種 vector 誤判。看到它先問自己「我的邏輯裡有沒有一條線繞回自己」，沒有的話才安心關掉。

## 動手二：多工器（MUX）與簡易 ALU

**多工器（multiplexer, MUX）** 是 CPU 裡出現頻率最高的組合元件：它是「用一個選擇訊號，從多個輸入挑一個輸出」。2-to-1 MUX：`sel=0` 選 `d0`，`sel=1` 選 `d1`。

```systemverilog
module mux2 #(parameter WIDTH = 8) (
    input  logic [WIDTH-1:0] d0,
    input  logic [WIDTH-1:0] d1,
    input  logic             sel,
    output logic [WIDTH-1:0] y
);
    assign y = sel ? d1 : d0;    // 三元運算子就是一個 MUX
endmodule
```

`sel ? d1 : d0` 這個三元運算子，綜合出來就是一個 MUX。CPU 裡到處是 MUX：「ALU 的第二個運算元要用暫存器還是立即數？」是一個 MUX；「PC 下一步要 +4 還是跳到 branch target？」是一個 MUX；「寫回暫存器的值來自 ALU 還是記憶體？」又是一個 MUX。**MUX 就是硬體的 `if-else` / `switch`。**

現在把 MUX 和加法器組成一塊簡易 ALU——它能做 add / sub / and / or，用 2-bit `op` 選要哪個結果。這就是把「算出所有可能結果，再用 MUX 挑一個」這個 ALU 的核心模式，具體而微地做一次：

```systemverilog
module alu_mini (
    input  logic [7:0] a,
    input  logic [7:0] b,
    input  logic [1:0] op,      // 00 add, 01 sub, 10 and, 11 or
    output logic [7:0] y,
    output logic       zero     // 結果是否為 0（給 branch 判斷用）
);
    always_comb begin
        case (op)
            2'b00: y = a + b;
            2'b01: y = a - b;
            2'b10: y = a & b;
            2'b11: y = a | b;
            default: y = 8'd0;
        endcase
    end
    assign zero = (y == 8'd0);
endmodule
```

`case` 語句本質上就是一個大 MUX（用 `op` 選）。`zero` 旗標是 CPU 判斷 `beq`/`bne`（相等就跳）時要用的——兩數相減若為 0 代表相等。

testbench：

```cpp
#include "Valu_mini.h"
#include "verilated.h"
#include <cstdio>
int main(int c, char** v) {
    Verilated::commandArgs(c, v);
    Valu_mini* t = new Valu_mini;
    struct { int a, b, op; } cs[] = {{5,3,0},{5,5,1},{0xF0,0x0F,2},{0xF0,0x0F,3}};
    const char* nm[] = {"add","sub","and","or"};
    for (auto& x : cs) {
        t->a = x.a; t->b = x.b; t->op = x.op; t->eval();
        printf("%3d %s %3d = %3d  zero=%d\n", x.a, nm[x.op], x.b, t->y, t->zero);
    }
    delete t; return 0;
}
```

實際輸出（真跑）：

```
  5 add   3 =   8  zero=0
  5 sub   5 =   0  zero=1
240 and  15 =   0  zero=1
240 or  15 = 255  zero=0
```

`5-5=0` 讓 `zero=1`（相等）；`0xF0 & 0x0F = 0`（沒有共同的 1 bit）也讓 `zero=1`；`0xF0 | 0x0F = 0xFF = 255`。這塊 `alu_mini` 就是 Part 1 Ch 9 那顆完整 10-op ALU 的縮小版原型。

## 關鍵路徑：電路能跑多快由它決定

組合邏輯不是「瞬間」算出結果——訊號經過每個閘都有一點傳播延遲（propagation delay）。一個電路裡，從任何輸入到任何輸出，會有很多條路徑，其中**最長（延遲最大）的那條，叫關鍵路徑（critical path）**。

為什麼它重要？時序電路（Ch 2）在每個時鐘上緣把組合邏輯的結果存起來。那兩個上緣之間的時間（時鐘週期）**必須夠長，讓最慢那條路徑的訊號有時間穩定下來**。所以：

```
   時鐘週期 ≥ 關鍵路徑延遲   →   最高時脈 = 1 / 關鍵路徑延遲
```

關鍵路徑越長，CPU 能跑的頻率越低。以我們的 4-bit ripple adder 為例：進位要從第 0 位一路傳到第 3 位（`cin → c[0] → c[1] → c[2] → cout`），4 位就要串 4 級全加器的延遲。32-bit 的話要串 32 級——這條進位鏈就是它的關鍵路徑，長得可怕。這也是為什麼真實 CPU 不用 ripple-carry，改用 carry-lookahead 之類的快速加法器（Part 3 Ch 24 會回頭量化這件事）。

現在你只要記住直覺：**組合邏輯堆得越深（串越多層閘），關鍵路徑越長，電路越慢。** 這是你之後設計 datapath 時無形的天花板。

## 對比：組合 vs 時序（先建立分界）

| 面向 | 組合邏輯（本章） | 時序邏輯（Ch 2） |
|---|---|---|
| 輸出取決於 | 只看當下輸入 | 當下輸入 + 過去狀態 |
| 需要時鐘嗎 | 不需要 | 需要 |
| 有記憶嗎 | 沒有 | 有 |
| SV 寫法 | `assign` / `always_comb` | `always_ff @(posedge clk)` |
| 賦值符號 | `=`（阻塞） | `<=`（非阻塞） |
| 典型元件 | 加法器、MUX、decoder、ALU | 暫存器、counter、FSM 狀態 |
| CPU 裡的角色 | 「計算」 | 「在對的時間記住結果」 |

這張表是 Part 0 的骨架。Ch 2 會把右半邊填滿。

## 踩雷集錦

1. **「`always_comb` 裡漏了某個分支不賦值沒差」** — 錯誤直覺：以為漏掉的情況輸出就是 0 或不管它。正確認識：組合邏輯若某條路徑沒給輸出賦值，硬體會**推斷出一個 latch（鎖存器）來「保住舊值」**——這是意外的時序元件，通常是 bug。`always_comb` 每條路徑都要賦值（或開頭給 default）。verilator 會用 CASEINCOMPLETE / 類似警告提醒你，Ch 4 詳解。

2. **「module 實例化像函數呼叫，會執行一次就結束」** — 錯誤直覺：把 `full_adder fa0 (...)` 想成「呼叫 full_adder 函數」。正確認識：它是**放一個實體電路進去**。四個 `full_adder` 就是四塊同時存在、同時運作的硬體，不是一段被呼叫四次的程式碼。硬體是空間展開，不是時間展開——這是軟體人最難扭轉的直覺。

3. **「加法器算 7+8=16，結果就是 16」** — 錯誤直覺：以為輸出寬度會自動變大裝下結果。正確認識：4-bit 加法器的 sum 就是 4-bit，裝不下 16。結果是 sum=0（低 4 位）+ cout=1（進位）。**位元寬度是你定死的，超出就靠進位/溢位旗標**。CPU 的 ALU 靠這些旗標判斷溢位，不會自己變寬。

4. **「XOR 一定要用 `^`，不能用 AND/OR/NOT 拼」** — 錯誤直覺：以為每個運算都有唯一寫法。正確認識：`a^b` 完全等於 `(~a & b)|(a & ~b)`，綜合出來一樣。boolean 代數的重點就是「同一個電路有無數等價寫法」，工具會化簡到最省。你寫得清楚易讀就好，別為了「少打幾個字」犧牲可讀性。

## 本章重點整理

- 電路分兩種：組合邏輯（無記憶，`y=f(輸入)`，本章）與時序邏輯（有記憶、要時鐘，Ch 2）。CPU 的「計算」全是組合邏輯。
- 組合邏輯用真值表或 boolean 式描述，兩者等價。De Morgan、分配、吸收等定律拿來化簡；卡諾圖是化簡的視覺工具，但實務靠綜合工具自動化簡，你只要寫清楚的 `always_comb`。
- 加法器從半加器（`sum=a^b`, `carry=a&b`）堆到全加器再到 4-bit ripple adder。256 種輸入全驗過。溢位靠 cout 表現：`7+8+1` 在 4-bit 得到 sum=0 cout=1。
- module 實例化是「放一塊實體電路進去」，不是函數呼叫。四個 full_adder 是四塊並存的硬體。
- MUX（`sel ? d1 : d0`）是硬體的 if-else，CPU 裡到處都是。簡易 ALU 用 `case`（大 MUX）選 add/sub/and/or，附 zero 旗標給 branch 用。
- 關鍵路徑 = 最長延遲路徑，決定時鐘週期下限。組合邏輯堆越深越慢。ripple adder 的進位鏈就是它的關鍵路徑。

## 自我檢核

- [ ] 我能不看表，寫出 XOR 的真值表，並把它化成 boolean 式。
- [ ] 我能說出半加器的 sum 和 carry 各是哪個運算，以及全加器為何多一個 cin。
- [ ] 我能解釋「module 實例化不是函數呼叫」到底差在哪，為什麼四個 full_adder 是四塊硬體。
- [ ] 我能講出 4-bit 加法器算 7+8+1 為什麼 sum=0 而不是 16。
- [ ] 我能用一句話說清楚 MUX 在 CPU 裡對應到軟體的什麼，並舉一個 CPU 裡用 MUX 的例子。
- [ ] 我能解釋關鍵路徑是什麼，以及它跟最高時脈的關係。
- [ ] 我看到 UNOPTFLAT 警告時，知道該先檢查什麼（有沒有真的組合迴路）。

## 延伸閱讀

- **《Digital Design and Computer Architecture, RISC-V Edition》(Harris & Harris) 第 2 章**：組合邏輯的紙本權威。真值表、boolean 代數定律、卡諾圖化簡、MUX/decoder 都在這章，比本章更完整地推導卡諾圖。你若想把卡諾圖練熟（本章刻意輕描），讀它的 2.7 節。
- **同書 第 5 章（Digital Building Blocks）**：加法器的深入版。ripple-carry 為什麼慢、carry-lookahead 怎麼加速、比較器/移位器怎麼做——本章的加法器和 ALU 積木在這裡有工業級的討論。Part 1 做完整 ALU 前值得先看。
- **HDLBits（hdlbits.01xz.net）— Combinational Logic 章節**：線上互動練習，寫一個組合電路、它自動用真值表對拍你的答案。從 `Wire`、`Gates`、`Mux` 一路做到 `Adder`，正好對應本章。這是把 `always_comb` 練到反射動作的最好方式。
- **nandland.com — "What is a Multiplexer?" / "Full Adder"**：兩篇圖解友善的短文。若本章的 MUX 和全加器你想再看一組不同角度的講解（含更多波形/電路圖），從這兩篇入手，它和本章的 SV 實作互補。

Ch 1 我們把「無記憶」的組合邏輯這半邊建好了——它會算，但算完的值留不住，輸入一變就消失。下一章補上另一半：**時序邏輯**。我們會引入 flip-flop（正緣觸發把值「記下來」的元件）、時鐘、以及那個會坑死所有新手的分野——阻塞賦值 `=` 與非阻塞賦值 `<=` 的差別。有了記憶，電路才能一拍一拍推進，CPU 才動得起來。

→ [Ch 2 數位邏輯（二）：時序邏輯、flip-flop、時鐘與 timing](./02-digital-logic-sequential.md)
