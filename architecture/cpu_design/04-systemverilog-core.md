# Ch 4 — SystemVerilog 語法核心：module / logic / always 與常見雷

> **目標**：把前三章零散用到的 SystemVerilog 語法系統性地整理成一套可靠的工作知識。你會搞清楚 `module`/port 怎麼寫、`logic` vs `reg` vs `wire` 的歷史包袱與現代用法、`always_comb`/`always_ff` 的正確使用、阻塞/非阻塞的規則（Ch 2 講過，這裡收束成鐵律）、`parameter`/`localparam`、`case`/`if`、以及 `$readmemh`/`$display`/`$finish` 這些 testbench 常用系統任務。重點放在三個會讓你 debug 到崩潰的雷：**意外推斷 latch、不完整的 sensitivity list、多重驅動（multiple drivers）**——每個都真跑給你看它怎麼壞。

## 為什麼要專門講語法

前三章你已經在用 SV 了——`module`、`always_ff`、`logic`、`case` 都出現過。那為什麼還要一整章講語法？

因為 SystemVerilog 是一個**背著三十年歷史包袱**的語言。它從 Verilog（1984）演化來，為了向後相容，保留了一堆過時、危險、容易誤用的東西（`reg`、`wire`、`always @`）。同時它又加了現代、安全的替代品（`logic`、`always_comb`、`always_ff`）。結果就是：**同一件事有好幾種寫法，有些是雷。** 網路上的舊教學、舊 code 到處是地雷寫法，你若不知道哪些該用哪些該避開，會踩得滿頭包。

這章的目的是給你一套**明確的「該用什麼、不該用什麼」規則**，讓你之後寫 CPU 時語言不絆你。核心心法就一句：**用現代子集（`logic` + `always_comb` + `always_ff`），避開所有舊寫法。**

## module 與 port：硬體的封裝單位

`module` 是 SV 的基本封裝單位——一塊有明確輸入輸出介面的電路。你在 Ch 1 就用過 module 實例化（把小 module 拼成大的）。標準寫法（ANSI-style port 列表，本課一律用這種）：

```systemverilog
module alu (
    input  logic [31:0] a,         // 輸入 port
    input  logic [31:0] b,
    input  logic [3:0]  alu_op,
    output logic [31:0] result,    // 輸出 port
    output logic        zero
);
    // ... 內部邏輯 ...
endmodule
```

- `input` / `output`：方向。（還有 `inout` 雙向，本課幾乎不用。）
- `logic [31:0]`：型別與位寬。`[31:0]` 是 32-bit，MSB 在左。
- port 名用 snake_case，這是本課約定（跨全課一致）。

實例化時用 **named connection**（`.port(訊號)`），永遠別用位置對應：

```systemverilog
    alu my_alu (
        .a(src1), .b(src2), .alu_op(op),
        .result(alu_out), .zero(is_zero)
    );
```

named connection 清楚、抗改動（改 port 順序不會默默接錯線）。位置對應（`alu my_alu (src1, src2, op, ...)`）在 port 一多就是災難。

## logic vs reg vs wire：歷史包袱與現代解法

這是 Verilog 老手都會踩、新手更暈的一團。先講歷史再講結論。

**Verilog 時代（舊）**，訊號分兩種型別，而且規則反直覺：

- `wire`：用在**連續賦值**（`assign`）和 module 連接。它是「線」，不儲存值。
- `reg`：用在 `always` 區塊裡被賦值的訊號。名字叫 "reg" 但**它不一定是暫存器**！`reg` 只是「能在程序區塊裡被賦值的變數」，用在 `always @(*)` 裡它就是組合邏輯。這個命名是 Verilog 最坑人的設計——無數人以為 `reg` = flip-flop，錯得離譜。

**SystemVerilog 的解法**：引入 `logic`，它**同時能用在 `assign` 和 `always`**，取代大部分 `reg`/`wire` 的場合。規則簡化成：

**幾乎所有訊號都用 `logic`。** 只有一個例外——如果一條線被**多個來源驅動**（例如三態匯流排，本課極少見），才需要用 `wire`。

| 型別 | 何時用 | 本課建議 |
|---|---|---|
| `logic` | 單一驅動的訊號（絕大多數情況） | **預設一律用這個** |
| `wire` | 多重驅動的 net（三態、匯流排） | 本課幾乎不用 |
| `reg` | Verilog 舊寫法 | **不要用**，一律 `logic` 取代 |

心法：**看到 `reg` 或 `wire`，先想「這裡能不能改成 `logic`」**，答案幾乎永遠是能。`logic` 是不是暫存器，由「你把它放進 `always_ff` 還是 `always_comb`」決定，跟型別名無關。

## always_comb vs always_ff：型別由用法決定

Ch 1–3 已經用過，這裡收束成規則：

- **`always_comb`**：組合邏輯。自動涵蓋所有輸入到 sensitivity list（不會漏），賦值用 `=`（阻塞），**每條路徑都要給輸出賦值否則推斷 latch**。
- **`always_ff @(posedge clk)`**：時序邏輯（flip-flop）。只在時鐘上緣執行，賦值用 `<=`（非阻塞）。

為什麼用 `always_comb`/`always_ff` 而非舊的 `always @(*)`/`always @(posedge clk)`？因為前者**帶語意檢查**：`always_comb` 若你不小心推斷出 latch，工具會警告；`always_ff` 若你在裡面寫出組合邏輯，工具也會抓。舊的 `always @` 什麼都不檢查，錯了你自己去猜。

**阻塞/非阻塞鐵律**（Ch 2 詳細示範過，這裡當結論記住）：

```
always_comb  →  用 =   （組合）
always_ff    →  用 <=  （時序）
```

混用會製造出模擬跟硬體不符、或默默算錯的電路（Ch 2 的 `shift_bug`）。

## parameter / localparam：可調參數

`parameter` 讓 module 可以參數化——同一份 code，實例化時給不同寬度/大小。`localparam` 是內部常數，不能從外面改（用來給 magic number 命名）。

```systemverilog
module pcnt #(parameter WIDTH = 4) (
    input  logic clk, input logic rst,
    output logic [WIDTH-1:0] q
);
    localparam [WIDTH-1:0] MAXV = {WIDTH{1'b1}};   // 全 1，即最大值。命名取代 magic number
    always_ff @(posedge clk) begin
        if (rst)           q <= '0;         // '0 = 全 0（不用寫寬度）
        else if (q == MAXV) q <= '0;         // 到頂回捲
        else               q <= q + 1'b1;
    end
endmodule
```

- `#(parameter WIDTH = 4)`：可調參數，預設 4。實例化時 `pcnt #(.WIDTH(8)) u1 (...)` 就變 8-bit。
- `localparam MAXV = {WIDTH{1'b1}}`：`{WIDTH{1'b1}}` 是「重複 WIDTH 次 1」，即全 1。用 localparam 給它名字 `MAXV`，比在邏輯裡直接寫 `4'hF` 清楚——這是消除 magic number 的正確做法。
- `'0` / `'1`：SV 語法，全 0 / 全 1，自動配合左邊寬度。

跑一下（WIDTH=4，數到 15 後回捲）：

```cpp
Vpcnt* t = new Vpcnt;
auto tick = [&](){ t->clk=0; t->eval(); t->clk=1; t->eval(); };
t->rst = 1; tick(); t->rst = 0;
printf("WIDTH=4 count: ");
for (int i = 0; i < 18; i++) { tick(); printf("%d ", t->q); }
```

實際輸出（真跑）：

```
WIDTH=4 count: 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 0 1 2 
```

數到 15（4-bit 最大）後回捲到 0，`MAXV` 生效。改 `#(.WIDTH(8))` 就會數到 255 才回捲，同一份 code。這種參數化在 CPU 裡到處用——register file 的位址寬度、cache 的行數，全靠 parameter 調。

## case 與 if：組合選擇

`case` 和 `if-else` 是 `always_comb` 裡描述選擇的兩大工具，本質都綜合成 MUX（Ch 1）。

```systemverilog
always_comb begin
    result = 32'd0;                  // 預設值：防 latch 的關鍵
    case (alu_op)
        4'b0000: result = a + b;     // ADD
        4'b0001: result = a - b;     // SUB
        4'b1000: result = a | b;     // OR
        4'b1001: result = a & b;     // AND
        default: result = 32'd0;     // 沒列到的 op 給定值
    endcase
end
```

兩個防雷習慣（下一節詳講為什麼）：

1. **`always_comb` 開頭給所有輸出一個預設值**（`result = 0;`）。
2. **`case` 一定加 `default`**、**`if` 儘量配 `else`**。

這兩個習慣的目的都是「保證每條執行路徑都給輸出賦了值」，避免意外推斷 latch。

## 系統任務：$display / $finish / $readmemh

這些是 testbench / 初始化用的「系統任務」（以 `$` 開頭），不合成成硬體，只在模擬時作用：

- **`$display("...", args)`**：像 `printf`，印訊息（SV testbench 用，C++ tb 用 `printf`）。
- **`$finish`**：結束模擬。
- **`$readmemh("file.hex", mem)`**：從 hex 文字檔載入資料進記憶體陣列。**這是 CPU 載入程式的標準做法**——你把編好的機器碼寫成 hex，`$readmemh` 讀進 instruction memory。

`$readmemh` 實測（Ch 0 驗過，這裡重述用法）：

```systemverilog
module mem (input logic [3:0] addr, output logic [31:0] data);
    logic [31:0] rom [0:15];             // 16 個 32-bit 字的記憶體
    initial $readmemh("prog.hex", rom);  // 開機時載入 hex 檔
    assign data = rom[addr];             // async read
endmodule
```

`prog.hex` 內容（每行一個 hex 字）：

```
deadbeef
00000013
cafebabe
```

實際輸出（真跑）：

```
rom[0]=deadbeef
rom[1]=00000013
rom[2]=cafebabe
```

`00000013` 其實是 RISC-V 的 `nop`（`addi x0,x0,0`）——Part 1 之後你就是這樣把真程式餵進 CPU 的。

## 雷一：意外推斷 latch

**最常見、最隱蔽的 SV 雷。** 組合邏輯（`always_comb`）如果某條路徑沒給輸出賦值，硬體必須「保住舊值」——這只能靠一個 latch（Ch 2 講過 latch 是難管的記憶元件）。你本來要純組合，卻多冒出一個時序元件，行為就錯了。

故意寫錯——`case` 沒涵蓋所有情況又沒 default：

```systemverilog
module latch_bug (input logic [1:0] sel, input logic [3:0] a, output logic [3:0] y);
    always_comb begin
        case (sel)
            2'b00: y = a;
            2'b01: y = a + 1;
            // 缺 2'b10、2'b11，也沒 default → sel=10/11 時 y 該是多少？
            // → 硬體推斷 latch「保住上次的 y」
        endcase
    end
endmodule
```

verilator 4.038 lint 會直接報錯攔下（真跑）：

```
%Warning-CASEINCOMPLETE: latch_bug.sv:3:9: Case values incompletely covered (example pattern 0x2)
    3 |         case (sel)
      |         ^~~~
%Error: Exiting due to 1 warning(s)
```

它告訴你 `case` 沒蓋全（連 `0x2` 這個沒蓋到的例子都幫你舉出來了）。修正——加 default 或開頭給預設：

```systemverilog
module latch_fixed (input logic [1:0] sel, input logic [3:0] a, output logic [3:0] y);
    always_comb begin
        y = 4'd0;                 // 開頭預設，保證每條路徑都有值
        case (sel)
            2'b00: y = a;
            2'b01: y = a + 1;
            default: y = 4'd0;
        endcase
    end
endmodule
```

`verilator --lint-only latch_fixed.sv` 乾乾淨淨、沒有警告（真跑無輸出）。**兩個習慣防死這個雷：開頭給輸出預設值、`case` 一定 `default`。** 這也是 Ch 3 FSM 次態邏輯開頭寫 `next = state;` 的原因。

## 雷二：不完整的 sensitivity list

這是舊寫法 `always @(...)` 專屬的雷，也是為什麼本課堅持用 `always_comb`。

```systemverilog
module incomplete (input logic a, input logic b, output logic y);
    always @(a) y = a & b;   // 只列 a！漏了 b
endmodule
```

sensitivity list 只寫 `@(a)`，意思是「只有 a 變才重算 y」。但 y 依賴 b——b 變了 y 卻不更新，**模擬結果錯，而且和實際合成出的硬體不一致**（硬體不管你 sensitivity 寫什麼，b 一變閘就跟著動）。這種「模擬 vs 硬體不符」的 bug 極難抓。

**解法就是不要用 `always @(*)` 手寫 sensitivity——用 `always_comb`。** `always_comb` 自動把所有讀到的訊號放進 sensitivity list，你不可能漏。上面若寫成 `always_comb y = a & b;`，a 或 b 任一變都會重算，永遠正確。這是 `always_comb` 存在的核心理由之一。

## 雷三：多重驅動（multiple drivers）

一條訊號**只能有一個驅動來源**。兩個地方同時寫同一條線，硬體上等於兩個輸出短接——邏輯衝突。

最明確會被 verilator 抓到的版本，是一個訊號同時被 `always_ff`（非阻塞）和 `always_comb`（阻塞）驅動：

```systemverilog
module md3 (input logic clk, input logic a, output logic y);
    always_ff @(posedge clk) y <= a;   // 一個驅動（時序）
    always_comb              y = a;    // 另一個驅動（組合）— 衝突！
endmodule
```

verilator 直接報錯（真跑）：

```
%Error-BLKANDNBLK: md3.sv:1:58: Unsupported: Blocked and non-blocking assignments to same variable: 'y'
```

它抓到「同一個變數 y 同時被阻塞和非阻塞賦值」——這是典型的多重驅動，物理上無意義。

**要注意的陷阱**：verilator 4.038 對「兩個 `always_comb` 都寫同一個 y」或「兩個 `assign` 都寫同一條線」這種**同性質**的多重驅動，**不一定報錯**——它可能默默用「後寫的贏」（last-write-wins）幫你合併掉，模擬照跑但那不是你要的電路。這是舊版 verilator 的寬鬆之處，別依賴它。**規則自己守好：每條訊號只在一個地方賦值。** 想在多個條件下設同一訊號，就在**同一個** `always_comb`／`always_ff` 裡用 `if/case` 處理，不要拆成多個區塊各寫一次。

## 對比：舊寫法 vs 現代寫法

| 要做的事 | 舊 Verilog 寫法（避開） | 現代 SV 寫法（用這個） |
|---|---|---|
| 訊號型別 | `reg` / `wire` 分場合 | 一律 `logic`（多驅動才 `wire`） |
| 組合邏輯 | `always @(*)` | `always_comb`（自動 sensitivity + 防 latch 檢查） |
| 時序邏輯 | `always @(posedge clk)` | `always_ff @(posedge clk)`（工具檢查） |
| 組合賦值 | `=` | `=`（不變） |
| 時序賦值 | `<=` | `<=`（不變） |
| 常數命名 | `` `define `` 巨集 | `localparam`（有型別、有 scope） |

心法：**永遠選右欄。** 現代寫法帶語意檢查，能在編譯期幫你抓下本章三個雷的大部分。

## 踩雷集錦

1. **「`reg` 就是 register（暫存器）」** — 錯誤直覺：看到 `reg` 以為它是 flip-flop。正確認識：`reg` 只是「能在 `always` 裡被賦值的變數」，它是組合還是時序，由你放進 `always_comb` 還是 `always_ff` 決定，跟型別名毫無關係。這是 Verilog 最坑人的命名。本課一律用 `logic` 避開整個混淆。

2. **「`always_comb` 漏賦值，那條路徑輸出就是 0」** — 錯誤直覺：以為沒賦值等於預設 0。正確認識：沒賦值會**推斷 latch 保住舊值**，變成意外的時序元件，行為不可預測。防法是開頭給輸出預設值 + `case` 加 `default`。verilator 會用 CASEINCOMPLETE 警告你——別忽略它。

3. **「`always @(a)` 只寫我在乎的訊號就好，反正結果會對」** — 錯誤直覺：以為 sensitivity list 是「效能提示」。正確認識：漏列訊號會讓**模擬結果和實際硬體不一致**（模擬不更新、硬體會更新），製造出「模擬過了、上板錯了」的鬼 bug。永遠用 `always_comb`，讓工具自動補完 sensitivity，根本別手寫。

4. **「兩個 always_comb 寫同一個訊號，verilator 沒報錯就沒事」** — 錯誤直覺：以為工具沒抱怨就代表合法。正確認識：verilator 4.038 對同性質多重驅動可能默默 last-write-wins 幫你合併，模擬照跑但那是碰運氣的電路，且真正的綜合工具會報錯。多重驅動是設計錯誤，規則自己守：一條訊號只在一個區塊賦值，多條件用 `if/case` 在同一區塊內處理。

## 本章重點整理

- SystemVerilog 背著 Verilog 的歷史包袱，同一件事有好幾種寫法。心法：只用現代子集——`logic` + `always_comb` + `always_ff`，避開 `reg`/`wire`/`always @`。
- 型別一律用 `logic`（是不是暫存器由放進哪種 always 決定，跟型別名無關）；只有多重驅動的 net 才用 `wire`。`reg` 別用。
- `always_comb`（組合，`=`，自動 sensitivity + 防 latch）、`always_ff @(posedge clk)`（時序，`<=`）。用它們而非 `always @` 是為了拿到工具的語意檢查。
- `parameter`（可調）/`localparam`（內部常數、消 magic number）。參數化 counter 真跑，WIDTH=4 數到 15 回捲。
- `$readmemh` 從 hex 載入記憶體——CPU 載入程式的標準法（`00000013` 是 nop）。
- 三大雷：①意外 latch（`always_comb` 漏賦值 → 開頭給預設 + `default`）②不完整 sensitivity（`always @` 漏列 → 改用 `always_comb`）③多重驅動（一條訊號多處賦值 → 只在一個區塊寫，多條件用 if/case）。verilator 分別會用 CASEINCOMPLETE、BLKANDNBLK 幫你抓一部分。

## 自我檢核

- [ ] 我能解釋為什麼 `reg` 不等於暫存器，以及本課為什麼一律用 `logic`。
- [ ] 我能說出 `always_comb` 相對於 `always @(*)` 的兩個安全優勢。
- [ ] 我能寫出一個防 latch 的 `always_comb`（講出兩個防雷習慣）。
- [ ] 我能解釋不完整 sensitivity list 為什麼會造成「模擬和硬體不符」，以及正確解法。
- [ ] 我能講出什麼是多重驅動、為什麼非法，以及 verilator 4.038 對它的處理有什麼陷阱。
- [ ] 我能說明 `parameter` 和 `localparam` 的差別，並舉一個各自的用途。
- [ ] 我知道 `$readmemh` 在 CPU 設計裡拿來做什麼。

## 延伸閱讀

- **Verilator 官方文件 — Warnings / Errors 章節（verilator.org/guide/latest/warnings.html）**：本章三個雷對應的 verilator 訊息（CASEINCOMPLETE、BLKANDNBLK、UNOPTFLAT、LATCH 等）都在這裡有解釋與修法。每次 verilate 報警告，來這查它是什麼意思、該不該理。這是你之後最常回訪的一頁。
- **Stuart Sutherland, "SystemVerilog for Design"（或其 SNUG 論文 "Standard Gotchas"）**：業界最權威的 SV 陷阱清冊。`logic`/`reg`/`wire` 的歷史、`always_comb` vs `always @(*)` 的精確差異、多重驅動規則，講得比任何教科書細。想把本章的規則背後的「為什麼」吃透，讀它。
- **《Digital Design and Computer Architecture, RISC-V Edition》(Harris & Harris) 第 4 章（Hardware Description Languages）**：HDL 的紙本主線，SystemVerilog 版本。module/always/賦值/參數化都有紮實範例，且每個語法都對應到「這會綜合成什麼電路」——這是理解 SV 的正確視角（永遠想著硬體，不是想著程式）。
- **HDLBits — Verilog Language 章節（尤其 Procedures、Combinational/Sequential logic 子節）**：把本章語法練成肌肉記憶的地方。特別做「找出並修正 latch」「修正 sensitivity list」那類除錯題，親手體驗本章三個雷。互動即時對拍，錯了馬上知道。

Ch 4 我們把語言磨利了——你現在知道該用哪個子集、避開哪些雷。前四章的一切（組合、時序、FSM、語法）都是「怎麼描述電路」。下一章我們把焦點轉到「怎麼把電路跑起來、看清楚、debug」：完整走一遍 **verilator + C++ testbench + 波形** 的工作流。你會學到 testbench 的標準結構、怎麼用 `--trace` 產波形、gtkwave 怎麼看、SV testbench（`$dumpfile`）與 C++ testbench 的對照、以及基礎的自我檢查（assertion）。把這條 edit-run-debug 循環走順，你就有了打造整顆 CPU 的完整工作環境。

→ [Ch 5 verilator + testbench + 波形：把設計跑起來](./05-verilator-testbench-waveform.md)
