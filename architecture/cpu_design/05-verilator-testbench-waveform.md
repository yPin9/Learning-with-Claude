# Ch 5 — verilator + testbench + 波形：把設計跑起來

> **目標**：把「寫電路 → 跑 → 看波形 → 修」這條 edit-run-debug 循環徹底走順，這是你之後打造整顆 CPU 的日常工作流。你會學到 verilator 的完整流程、C++ testbench 的標準結構、怎麼用 `--trace` 產 VCD 波形給 gtkwave 看、SV testbench（`$dumpfile`）與 C++ testbench 的對照、以及**自我檢查（self-checking / assertion）**——讓 testbench 自己判斷對錯，而不是你肉眼盯輸出。全程用 Ch 0 的 counter，走一遍「寫對 → 故意寫錯 → 波形抓 bug → 修回」的完整循環。
> **環境**：WSL Ubuntu 22.04，verilator 4.038 / iverilog 11 / gtkwave（Ch 0 已裝）。

## 為什麼 testbench 是硬體設計的一切

軟體出錯，會噴 exception、印 stack trace、gdb 一步步走。**硬體不會。** 你的電路錯了，它不會報錯——它就是某條線在某個時鐘週期值錯了，然後這個錯值往下傳，最後輸出不對。沒有例外、沒有堆疊、沒有紅字。

所以硬體工程師驗證設計的唯一手段，是**testbench**：一段負責「餵輸入、收輸出、比對是否正確」的程式碼。它就是硬體的 unit test。而當 testbench 說「錯了」，你 debug 的唯一工具是**波形**——把每條線隨時間的值畫出來，一格一格找哪裡開始不對。

這章要建立的，是這條循環的肌肉記憶：

```
   寫/改 .sv  ──►  verilate + 編譯  ──►  跑 testbench
      ▲                                      │
      │                                      ├─ testbench 自我檢查說 PASS → 完成
      │                                      └─ 說 FAIL → 開 gtkwave 看波形 → 找到錯的那條線/那一拍
      └──────────────────── 改 .sv 修 bug ◄───────────────┘
```

軟體人熟悉的「改 code → 跑 test → 看哪裡紅 → 修」，硬體版本就是這個，只是「看哪裡紅」多了一層「開波形定位」。把這條循環走順，你就有了做 CPU 的全部工作環境。

## verilator 完整流程回顧

Ch 0 走過，這裡當基準線收束。三步：

```bash
verilator --cc counter.sv --exe tb.cpp --Mdir obj_dir   # 1. SV → C++ model
make -C obj_dir -f Vcounter.mk Vcounter                 # 2. C++ → 原生執行檔
./obj_dir/Vcounter                                       # 3. 跑
```

加波形要多一個 `--trace`：

```bash
verilator --cc --trace counter.sv --exe tb.cpp --Mdir obj_dir
```

`--trace` 讓 verilator 產生支援 VCD dump 的 model，你的 testbench 才能呼叫 `trace()`/`dump()`（下面詳講）。這是唯一的差別。

## C++ testbench 的標準結構

一個 verilator testbench（C++）有固定骨架，理解它你就能寫任何複雜度的 tb。拆解：

```cpp
#include "Vcounter.h"           // (1) verilator 產生的 model header
#include "verilated.h"          //     verilator runtime
#include "verilated_vcd_c.h"    // (2) 波形支援（要 --trace）
#include <cstdio>

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    Verilated::traceEverOn(true);            // (3) 開全域 trace
    Vcounter* top = new Vcounter;            // (4) 建立 DUT 實例

    VerilatedVcdC* tfp = new VerilatedVcdC;  // (5) 波形物件
    top->trace(tfp, 99);                     //     綁定 DUT，記錄 99 層深
    tfp->open("counter.vcd");                //     開輸出檔

    vluint64_t sim_time = 0;
    auto tick = [&]() {                      // (6) 一個時鐘週期 = 兩次 eval + 兩次 dump
        top->clk = 0; top->eval(); tfp->dump(sim_time++);
        top->clk = 1; top->eval(); tfp->dump(sim_time++);
    };

    // (7) 刺激（stimulus）：餵輸入、翻時鐘
    top->rst = 1; tick();
    top->rst = 0;
    for (int i = 0; i < 5; i++) tick();

    // (8) 檢查與收尾
    printf("q=%d\n", top->q);
    tfp->close();                            // 一定要 close，否則 VCD 可能不完整
    delete top;
    return 0;
}
```

八個部位，每個都有它的角色：

1. **model header**：`V` + module 名。這是 verilator 幫你生的 C++ class。
2. **波形 include**：只有要 trace 才需要。
3. **`traceEverOn(true)`**：全域開關，忘了它 trace 不會動。
4. **建立 DUT**：`new Vcounter`，這就是你的電路實例。
5. **波形物件**：`VerilatedVcdC` + `trace()` + `open()` 三件套。
6. **`tick` lambda**：**最關鍵的抽象**。verilator model 不自跑時鐘，你手動翻 `clk=0→1` 造一個上緣，每次 `eval()` 後 `dump()` 記錄那個時間點。把它包成 lambda，之後 stimulus 只要呼叫 `tick()`。
7. **刺激**：設輸入、呼叫 tick 推進時間。這是 tb 的主體邏輯。
8. **收尾**：檢查結果、`tfp->close()`（**不 close 波形檔可能截斷**）、釋放。

這個骨架你會複製一輩子。CPU 的 testbench 只是把「刺激」和「檢查」變複雜（載入程式、跑幾千拍、跟 spike 對拍），骨架不變。

## 自我檢查：讓 testbench 自己判對錯

上面那版 tb 只 `printf("q=%d")`，靠**你肉眼**判斷 5 對不對。這在小例子還行，跑 CPU 幾千條指令時，你不可能肉眼盯。正確做法是 **self-checking testbench**——tb 自己知道期望值，自己比對，自己報 PASS/FAIL。

加一個 `check` 輔助函數：

```cpp
#include "Vcounter.h"
#include "verilated.h"
#include "verilated_vcd_c.h"
#include <cstdio>

static int errors = 0;
void check(const char* what, int got, int exp) {
    if (got != exp) { printf("[FAIL] %s: got %d exp %d\n", what, got, exp); errors++; }
    else            { printf("[ OK ] %s = %d\n", what, got); }
}

int main(int c, char** v) {
    Verilated::commandArgs(c, v);
    Verilated::traceEverOn(true);
    Vcounter* t = new Vcounter;
    VerilatedVcdC* tf = new VerilatedVcdC;
    t->trace(tf, 99); tf->open("counter.vcd");
    vluint64_t tm = 0;
    auto tick = [&]() { t->clk=0; t->eval(); tf->dump(tm++); t->clk=1; t->eval(); tf->dump(tm++); };

    t->rst = 1; tick();
    check("after reset", t->q, 0);          // reset 後應為 0
    t->rst = 0;
    for (int i = 1; i <= 5; i++) tick();
    check("after 5 ticks", t->q, 5);        // 數 5 拍後應為 5

    tf->close();
    printf("errors=%d\n", errors);
    delete t;
    return errors ? 1 : 0;                  // 有錯回非零 exit code（給 CI/腳本判斷）
}
```

正確的 counter 跑起來（真跑）：

```
[ OK ] after reset = 0
[ OK ] after 5 ticks = 5
errors=0
```

`exit=0`。兩個檢查都過。**注意 `return errors ? 1 : 0`**——這讓 exit code 反映結果，之後你能用 shell 腳本批次跑一堆測試、靠 exit code 判斷全過沒。

## 完整循環：故意寫錯 → 波形抓 → 修回

現在示範這章的核心——完整的 debug 循環。把 counter 的 reset 故意寫錯：

```systemverilog
module counter (input logic clk, input logic rst, output logic [7:0] q);
    always_ff @(posedge clk) begin
        if (rst) q <= 8'd1;    // BUG！reset 應該歸 0，這裡錯寫成 1
        else     q <= q + 8'd1;
    end
endmodule
```

同一個 self-checking tb 跑（真跑）：

```
[FAIL] after reset: got 1 exp 0
[FAIL] after 5 ticks: got 6 exp 5
errors=2
```

testbench **立刻抓到**兩個 FAIL：reset 後是 1 不是 0，連帶數 5 拍變成 6（因為起點錯了）。這就是 self-checking 的價值——你不用肉眼看，它直接告訴你「reset 後值錯了」。

接著**開波形定位**。`gtkwave counter.vcd`，把 `clk`、`rst`、`q` 拖進去，你會看到：

```
        ┌──┐  ┌──┐  ┌──┐  ┌──┐  ┌──┐  ┌──┐
 clk  ──┘  └──┘  └──┘  └──┘  └──┘  └──┘  └─
 rst  ──────┐
            └──────────────────────────────
 q    ══════╪══ 1 ══╪══ 2 ══╪══ 3 ══...
            ▲
        rst 為高的那個上緣，q 卻是 1（該是 0）← bug 就在這一拍
```

你一眼看到「rst 拉高的那個時鐘上緣過後，q 是 1 而不是 0」——bug 精確定位到「reset 那條路徑」。回去看 code，`q <= 8'd1` 一眼揪出，改回 `8'd0`，重跑 → `errors=0`。

**這就是硬體 debug 的完整循環**：self-checking tb 告訴你「哪個檢查點錯」，波形告訴你「哪條線、哪一拍開始錯」，你回 code 修那一處。CPU debug 也是這套，只是波形上的線從 3 條變成幾百條，你要會用 gtkwave 的 search/marker 快速跳到出錯的 cycle（延伸閱讀有指路）。

## SV testbench 對照：$dumpfile / $dumpvars

verilator 用 C++ tb，但你也會遇到 SV 寫的 testbench（很多開源專案、教科書用這種，iverilog 也吃）。它的波形是用系統任務 `$dumpfile`/`$dumpvars` 產生，比 C++ 那套 `VerilatedVcdC` 簡潔。看一個等價的 SV tb：

```systemverilog
module counter_tb;
    logic clk = 0, rst;
    logic [7:0] q;
    counter dut (.clk(clk), .rst(rst), .q(q));   // 實例化 DUT

    always #5 clk = ~clk;                          // 每 5 單位翻一次 → 10 單位週期

    initial begin
        $dumpfile("counter_sv.vcd");               // 波形檔名
        $dumpvars(0, counter_tb);                  // dump 這個 module 全部訊號（0=所有層）

        rst = 1; @(posedge clk); #1;
        assert (q == 0) else $error("reset failed, q=%0d", q);   // 內建 assertion
        rst = 0;
        repeat (5) @(posedge clk); #1;
        assert (q == 5) else $error("count failed, q=%0d", q);
        $display("SV tb done: q=%0d", q);
        $finish;
    end
endmodule
```

用 iverilog 跑（真跑）：

```bash
iverilog -g2012 -o sim counter.sv counter_tb.sv
vvp sim
```

輸出：

```
VCD info: dumpfile counter_sv.vcd opened for output.
SV tb done: q=5
```

比對兩種 tb 的關鍵差異：

- **時鐘**：SV 用 `always #5 clk = ~clk;` 自動跑時鐘（`#5` 是延遲 5 個時間單位）；C++ 要手動 `clk=0;eval();clk=1;eval()`。SV 的 `#delay` 更接近「真實時間」，C++ 的 tick 是「事件驅動」。
- **等時鐘**：SV 用 `@(posedge clk)` 阻塞等上緣；C++ 用呼叫 `tick()`。
- **波形**：SV 一行 `$dumpvars` 搞定；C++ 要 `VerilatedVcdC` 三件套。
- **assertion**：SV 有內建 `assert (...) else $error(...)`；C++ 靠你自己寫 `check()`。

`assert (q == 0) else $error(...)` 是 SV 的**immediate assertion（立即斷言）**——條件不成立就報錯。這是 self-checking 的語言內建版，比手寫 `if` 簡潔。verilator 也支援 SV assertion（要加 `--assert`），但本課 C++ tb 主線用手寫 `check()`，概念一樣。

## 對比：C++ testbench vs SV testbench

| 面向 | C++ tb（verilator 主線） | SV tb（iverilog / 開源常見） |
|---|---|---|
| 語言 | C++ | SystemVerilog |
| 時鐘 | 手動 `clk=0;eval();clk=1;eval()` | `always #5 clk=~clk;` 自動 |
| 等事件 | 呼叫 `tick()` | `@(posedge clk)`、`#delay` |
| 波形 | `VerilatedVcdC` 三件套 | `$dumpfile`/`$dumpvars` 一行 |
| assertion | 自己寫 `check()` | 內建 `assert...else $error` |
| 執行速度 | 極快 | 慢 |
| 表達力 | 你有整個 C++（讀檔、算期望值、對拍 spike） | SV 的行為級語法（`fork`、`#delay`） |
| 本課用途 | 主線，CPU 對拍靠它的 C++ 能力 | 對照、快速小測、讀懂別人的 tb |

心法：**本課 CPU 主線用 C++ tb**——因為之後要「用 C 讀 ELF、算期望暫存器值、跟 spike 逐指令對拍」，這些用 C++ 寫順手、跑得快。SV tb 你要看得懂（開源專案、教科書用它），能寫小的即可。

## 踩雷集錦

1. **「加了 `--trace` 但波形檔是空的 / gtkwave 打不開」** — 錯誤直覺：以為 `--trace` 就會自動產波形。正確認識：`--trace` 只是「讓 model 支援 trace」，你還要在 C++ tb 裡做三件事：`Verilated::traceEverOn(true)`、`top->trace(tfp,99)` + `tfp->open()`、每個 `eval()` 後 `tfp->dump(time)`，最後 `tfp->close()`。少任何一件波形就不完整。尤其**忘了 `close()` 會讓 VCD 截斷**——結尾的資料沒 flush 出來。

2. **「testbench 印出對的值就代表電路對」** — 錯誤直覺：靠 `printf` + 肉眼判斷。正確認識：肉眼在小例子還行，跑 CPU 幾千拍就失效。要用 **self-checking**——tb 自己存期望值、自己比對、自己報 PASS/FAIL、用 exit code 反映結果。不會自我檢查的 tb 在 CPU 規模下等於沒測。

3. **「波形上訊號在時鐘上緣『之前』就變了，是不是 bug」** — 錯誤直覺：以為所有訊號都該對齊上緣。正確認識：**組合邏輯訊號**（`always_comb`/`assign` 的輸出）本來就會在輸入一變就跟著變，不對齊時鐘；只有**暫存器輸出**（`always_ff`）才對齊上緣。看波形要先分清哪些是組合、哪些是時序，否則會把正常行為當 bug。

4. **「C++ tb 的 `tick()` 裡 `eval()` 呼叫一次就好」** — 錯誤直覺：以為設好 `clk=1` 呼叫一次 `eval()` 就是一個週期。正確認識：一個完整週期要 `clk=0; eval()` **再** `clk=1; eval()`——因為 flip-flop 是邊緣觸發，你必須製造出 `clk` 從 0 到 1 的**變化**，光設 `clk=1` 而前一刻已經是 1，就沒有上緣，`always_ff` 不會觸發。時鐘的重點是「邊緣（變化）」，不是「電平（值）」。

## 本章重點整理

- 硬體不噴 exception，驗證全靠 testbench（硬體的 unit test）+ 波形（debug 工具）。核心循環：寫/改 → verilate+編 → 跑 tb → PASS 完成 / FAIL 開波形定位 → 修。
- C++ tb 標準骨架八部位：model include、波形 include、`traceEverOn`、建 DUT、波形三件套、`tick` lambda（手動翻時鐘）、刺激、檢查+`close()`。這骨架做 CPU 也不變。
- self-checking：tb 自存期望值、`check()` 比對、`errors` 計數、exit code 反映結果。肉眼判斷在 CPU 規模下失效，一定要自我檢查。
- 完整 debug 循環真跑示範：故意把 reset 寫成 `q<=1`，tb 立刻報兩個 FAIL，波形上「rst 高的那個上緣 q 卻是 1」精確定位 bug，改回 `q<=0` → errors=0。
- `--trace` 只讓 model 支援波形；還要 tb 裡 `traceEverOn` + `trace/open/dump/close` 才真的產出 VCD。忘了 `close()` 會截斷。
- SV tb（`$dumpfile`/`$dumpvars` + `assert...else $error` + `always #5 clk=~clk`）比 C++ 簡潔，iverilog 吃它、開源常用。本課 CPU 主線用 C++ tb（要對拍 spike、算期望值），但你要看得懂 SV tb。

## 自我檢核

- [ ] 我能不看講義，寫出一個 verilator C++ testbench 的骨架（含建 DUT、tick、刺激、檢查）。
- [ ] 我能說出 `--trace` 之外，還要在 C++ tb 裡做哪幾件事波形才會產出來（至少三件）。
- [ ] 我能解釋為什麼一個時鐘週期要 `clk=0;eval();clk=1;eval()` 兩次 eval，而不是一次。
- [ ] 我能講出 self-checking testbench 相對於 `printf` + 肉眼的兩個優勢。
- [ ] 我能描述一次完整的 debug 循環：tb 報 FAIL → 波形怎麼幫我定位到哪條線哪一拍。
- [ ] 我能說出 C++ tb 和 SV tb 在時鐘、波形、assertion 三方面各怎麼做，以及本課主線為什麼選 C++。
- [ ] 我看波形時，能分辨哪些訊號該對齊時鐘上緣、哪些不用（時序 vs 組合）。

## 延伸閱讀

- **Verilator 官方文件 — "Connecting to C++" / "Tracing (Waveforms)" 章節**：本章 C++ tb 骨架的權威來源。`VerilatedVcdC` 的完整 API、多時鐘 tb 怎麼寫、怎麼傳 command-line 參數給 tb——之後你的 CPU tb 變複雜時（多時鐘域、動態載入程式）回來查這裡。特別看它的 `examples/` 目錄，有現成可跑的完整範例。
- **GTKWave User's Guide 第 4–6 章（signal search、markers、grouping）**：本章只教你把訊號拖進去看，這幾章教你在幾百條線的 CPU 波形裡快速定位——用值搜尋跳到某訊號變成某值的那一拍、放 marker 量兩個事件間隔幾個 cycle、把相關訊號 group 起來。這是 debug pipeline hazard 時的救命技能，Part 2 前務必練熟。
- **《Digital Design and Computer Architecture, RISC-V Edition》(Harris & Harris) 第 4 章 testbench 那節**：紙本版的 testbench 寫法（SV 風格），含 self-checking 與從檔案讀測試向量。本章的 self-checking 概念，這裡有教科書級的系統講解，和本章的 C++ 版互補。
- **ChipVerify（chipverify.com）— SystemVerilog Testbench / Assertions 教學**：想把 SV testbench（`$dumpvars`、`assert`、`fork/join`、`$random`）學紮實的線上資源。本課雖以 C++ tb 為主，但你讀開源 core 的 tb 幾乎都是 SV，這裡幫你補齊看懂它們的能力。從 immediate assertion 那節開始，正好接本章的 `assert...else $error`。

Ch 5 我們把工作流走順了——你現在有能力寫電路、跑起來、產波形、self-check、開 gtkwave 定位 bug。**Part 0（數位邏輯與 HDL 地基）到此完成。** 你已經掌握了組合邏輯、時序邏輯、FSM、SystemVerilog 語言、以及完整的驗證工作流——這是打造 CPU 的全部地基。

從下一章開始進 **Part 1：單週期 RV32I CPU**。我們會先建立整顆 CPU 的心智模型（datapath + control），然後一塊一塊親手做出來——PC、instruction fetch、register file、ALU、control unit——最後湊成一顆能真正執行你自己 toolchain 編出的 RV32I 程式的 CPU。前五章學的每一樣東西，都會在那裡派上用場：ALU 是組合邏輯、register file 是 flip-flop、control unit 是 FSM、驗證靠 self-checking tb + 波形。地基打好了，開始蓋房子。

→ [Ch 6 CPU 的心智模型：datapath + control](./06-cpu-datapath-mental-model.md)
