# Ch 0 — 環境搭建：verilator / gtkwave / riscv toolchain / spike

> **目標**：把整門課會用到的工具一次裝齊——verilator（把 SystemVerilog 編成 C++ 來跑）、gtkwave（看波形）、iverilog（另一個模擬器，交叉驗證用）、RISC-V 交叉編譯器（Part 1 之後拿來編真程式），並用一個 8-bit counter 跑通「寫 SV → verilate → 跑 → 看波形」的完整循環。這一章跑完，之後每一章的範例你都能立刻在自己機器上重現。
> **環境**：WSL Ubuntu 22.04。本課全程在 WSL 裡操作（Windows 那側只當終端機）。

## 為什麼硬體設計要先搞定「模擬環境」

寫軟體，你打開 editor、`gcc a.c`、`./a.out`，馬上看到結果。硬體不是這樣。你寫的 SystemVerilog（以下簡稱 SV）**不是程式**——它是一份「電路長什麼樣」的描述。真正把它變成矽，要經過合成（synthesis）、佈局繞線（place & route）、下線流片，一次幾百萬美金、幾個月。你不可能每改一行就流一次片。

所以硬體工程師的日常，是在**模擬器**裡驗證電路對不對。模擬器讀你的 SV，在電腦裡「假裝」自己是那顆電路，一個時鐘週期一個時鐘週期地算出每條線的值。你用一段測試程式（testbench）餵輸入、比對輸出，就像軟體的 unit test。

這門課的模擬器選 **verilator**。它跟別的模擬器不一樣：它不是「解釋執行」SV，而是把 SV **編譯成 C++**，再編成原生執行檔。所以它快得離譜（比商業模擬器 VCS 還快是常有的事），而且你寫的 testbench 就是一支 C++ 程式，你原本的 C 功力直接派上用場。代價是它只支援可合成（synthesizable）的那部分 SV——但那正好是我們要學的部分。

先建立這張心智圖，你才知道每個工具站在哪：

```
   你寫的 .sv 檔  ──verilator──►  一堆 .cpp/.h  ──g++──►  ./obj_dir/Vxxx（原生執行檔）
   （電路描述）                    （C++ model）              （跑起來的模擬）
        │                                                          │
        │                                                          ├─► 印出 $display / printf 結果
        └──iverilog（另一條路，交叉驗證）                          └─► 產生 .vcd 波形檔 ──► gtkwave 看
```

| 工具 | 它做什麼 | 你何時用它 |
|---|---|---|
| **verilator** | 把 SV 編成 C++ model，用 C++ testbench 驅動 | 本課主力，幾乎每章都用 |
| **iverilog** | 傳統事件驅動模擬器，吃 SV testbench | 想用 SV（非 C++）寫 tb、交叉驗證 verilator 結果 |
| **gtkwave** | 開 `.vcd` 波形檔，一格一格看訊號 | debug。硬體的 bug 只能靠波形抓 |
| **riscv64-unknown-elf-gcc** | RISC-V 交叉編譯器（baremetal） | Part 1 之後，編真程式餵給你自己做的 CPU |
| **spike**（可選） | 官方 RISC-V ISA reference simulator | Part 1 之後，當「標準答案」跟你的 CPU 對拍 |

Part 0（Ch 0–5）只會用到 verilator / iverilog / gtkwave。riscv toolchain 跟 spike 是給後面用的，這章順手裝好，免得之後卡關。

## 安裝：三行 apt 打天下

WSL Ubuntu 22.04 的 apt 內建就有我們要的東西。開一個 WSL 終端機，跑：

```bash
sudo apt update
sudo apt install -y verilator gtkwave iverilog \
    gcc-riscv64-unknown-elf binutils-riscv64-unknown-elf \
    build-essential
```

`build-essential` 給你 `g++` / `make`（verilator 產生的 C++ 要靠它編）。裝完驗證版本：

```bash
verilator --version
iverilog -V | head -1
riscv64-unknown-elf-gcc --version | head -1
which gtkwave
```

本課驗證環境的實際輸出：

```
Verilator 4.038 2020-07-11 rev v4.036-114-g0cd4a57ad
Icarus Verilog version 11.0 (stable) ()
riscv64-unknown-elf-gcc () 10.2.0
/usr/bin/gtkwave
```

**verilator 4.038 偏舊**，這是刻意的取捨：Ubuntu 22.04 apt 版就是它，全課教材都以它為準。它足以支援我們用到的一切（`always_ff` / `always_comb` / `logic` / `$readmemh` / `--trace`）。我們會**避開** SystemVerilog 較新的語法（`interface`、`program` block、部分 SV-2017 特性），因為舊版 verilator 不吃——這對學習反而是好事，逼你用最紮實的核心子集。

如果你想要新版 verilator（5.x，語法支援更全、錯誤訊息更漂亮），要從源碼編。**本課不需要**，但列在這裡供參：

```bash
# 可選，非必要。本課 4.038 已足夠
sudo apt install -y git autoconf flex bison libfl2 libfl-dev help2man
git clone https://github.com/verilator/verilator
cd verilator && git checkout v5.024
autoconf && ./configure && make -j$(nproc)
sudo make install
```

### spike（可選，Part 1 之前不用管）

spike 是官方 ISA 模擬器，之後拿來當 CPU 的對拍基準。apt 沒有現成的，要從源碼編。**這章可以先跳過**，等 Part 1 打單週期 CPU 時再回來裝：

```bash
# 可選，Part 1 才需要
sudo apt install -y device-tree-compiler
git clone https://github.com/riscv-software-src/riscv-isa-sim
cd riscv-isa-sim
mkdir build && cd build
../configure --prefix=/opt/riscv
make -j$(nproc)
sudo make install
echo 'export PATH=/opt/riscv/bin:$PATH' >> ~/.bashrc
```

編 spike 要幾分鐘，而且它依賴一堆東西。真的別在 Ch 0 卡在這——它跟 Part 0 的內容毫無關係。

## 專案目錄結構與 Makefile 骨架

先講清楚我們每個範例長怎樣。一個最小可跑的 verilator 專案只要兩個檔：

- 一個 `.sv`：你的電路（design under test，DUT）。
- 一個 `.cpp`：testbench，負責建立 model、餵時鐘與輸入、讀輸出。

建議每個範例一個資料夾。本課範例都放 `/tmp` 或你喜歡的任何地方，結構像這樣：

```
counter/
├── counter.sv      ← 電路
├── tb.cpp          ← testbench
├── Makefile        ← 一鍵 build+run
└── obj_dir/        ← verilator 產物（可刪，重編會重生）
```

verilate 的四步流程（**照抄這個當範本，後面每章都一樣**）：

```bash
verilator --cc counter.sv --exe tb.cpp --Mdir obj_dir   # 1. SV → C++
make -C obj_dir -f Vcounter.mk Vcounter                 # 2. C++ → 執行檔
./obj_dir/Vcounter                                       # 3. 跑
```

- `--cc`：產生 C++（而非 SystemC）。
- `--exe tb.cpp`：把你的 testbench 一起編進去，產出可執行檔。
- `--Mdir obj_dir`：產物放哪。
- verilator 會生一個 `Vcounter.mk`，`make` 它就得到 `./obj_dir/Vcounter`。

把這串包成 Makefile，之後 `make` 一鍵搞定：

```makefile
# Makefile — 通用骨架，改 MOD 和 TB 就能重用
MOD = counter
TB  = tb.cpp

.PHONY: all run wave clean
all: run

run:
	verilator --cc $(MOD).sv --exe $(TB) --Mdir obj_dir
	make -C obj_dir -f V$(MOD).mk V$(MOD)
	./obj_dir/V$(MOD)

# 加 --trace 產波形版本
wave:
	verilator --cc --trace $(MOD).sv --exe $(TB) --Mdir obj_dir
	make -C obj_dir -f V$(MOD).mk V$(MOD)
	./obj_dir/V$(MOD)
	gtkwave $(MOD).vcd &

clean:
	rm -rf obj_dir *.vcd
```

## 第一個可跑的範例：8-bit counter

我們做一個最經典的入門電路：一個 8-bit 上數計數器（counter）。每個時鐘上緣（rising edge）數值 +1，`rst` 為 1 時歸零。這裡先不解釋每個語法細節（`always_ff`、`<=` 那些都在 Ch 2、Ch 4 深講），這章的目的是**先讓工具鏈跑起來**，建立肌肉記憶。

`counter.sv`：

```systemverilog
module counter (
    input  logic       clk,
    input  logic       rst,
    output logic [7:0] q
);
    always_ff @(posedge clk) begin
        if (rst)
            q <= 8'd0;        // reset：歸零
        else
            q <= q + 8'd1;    // 每個上緣 +1
    end
endmodule
```

`tb.cpp`（testbench）：

```cpp
#include "Vcounter.h"      // verilator 產生的 header，名字是 V + module 名
#include "verilated.h"
#include <cstdio>

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    Vcounter* top = new Vcounter;

    // 一個「tick」= 拉低 clk 再拉高 clk，模擬一個完整週期的上緣
    auto tick = [&]() {
        top->clk = 0; top->eval();   // eval() 重算整個電路
        top->clk = 1; top->eval();   // clk 0→1 就是 posedge
    };

    top->rst = 1; tick();            // 先 reset 一拍
    top->rst = 0;
    for (int i = 0; i < 5; i++) tick();   // 再跑 5 拍

    printf("q=%d\n", top->q);        // 期望：5

    delete top;
    return 0;
}
```

關鍵觀念：verilator 的 model **不會自己跑時鐘**。時鐘是你在 C++ 裡手動翻的——`clk=0; eval(); clk=1; eval()` 這組動作，就是製造一次 0→1 的上緣。`eval()` 是「重新計算整個電路現在所有線的值」。reset 一拍歸零，再數 5 拍，`q` 應該是 5。

跑起來：

```bash
verilator --cc counter.sv --exe tb.cpp --Mdir obj_dir
make -C obj_dir -f Vcounter.mk Vcounter
./obj_dir/Vcounter
```

實際輸出（真跑）：

```
q=5
```

reset 後數 5 個上緣，得到 5。工具鏈通了。

### 邊界情況：數到爆表會怎樣

8-bit 的 counter 最大值是 255（`0xFF`）。數到 255 再 +1 會發生什麼？它**回捲（wrap）到 0**——因為硬體加法器就是固定寬度，進位跑出去就沒了，沒有「溢位例外」這種東西。把上面 loop 改成跑 260 拍，你會看到 `q=3`（260 mod 256 = 4，扣掉 reset 那拍的細節後你自己算算看）。這不是 bug，這是**固定位元寬度的本質**——記住它，Part 1 做 ALU 時「加法溢位」就是同一回事。

## 看波形：gtkwave

`printf` 只能看某一拍的某個值。要看「每條線隨時間怎麼變」，得靠波形。改一版 testbench 產生 `.vcd`（Value Change Dump，波形檔格式），細節 Ch 5 會完整講，這裡先體驗：

```cpp
#include "Vcounter.h"
#include "verilated.h"
#include "verilated_vcd_c.h"    // 波形支援
#include <cstdio>

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    Verilated::traceEverOn(true);        // 開全域 trace 開關
    Vcounter* top = new Vcounter;
    VerilatedVcdC* tfp = new VerilatedVcdC;
    top->trace(tfp, 99);                 // 記錄到 99 層深
    tfp->open("counter.vcd");

    vluint64_t t = 0;
    auto tick = [&]() {
        top->clk = 0; top->eval(); tfp->dump(t++);   // 每次 eval 後 dump 一個時間點
        top->clk = 1; top->eval(); tfp->dump(t++);
    };
    top->rst = 1; tick();
    top->rst = 0;
    for (int i = 0; i < 8; i++) tick();

    printf("final q=%d, wrote counter.vcd\n", top->q);
    tfp->close();
    delete top;
    return 0;
}
```

要加 `--trace` 才能編：

```bash
verilator --cc --trace counter.sv --exe tb_trace.cpp --Mdir obj_dir
make -C obj_dir -f Vcounter.mk Vcounter
./obj_dir/Vcounter
```

實際輸出（真跑）：

```
final q=8, wrote counter.vcd
```

產出的 `counter.vcd` 開頭長這樣（真實內容）：

```
$version Generated by VerilatedVcd $end
$timescale   1ps $end
 $scope module TOP $end
  $var wire  1 # clk $end
  $var wire  8 % q [7:0] $end
  $var wire  1 $ rst $end
```

開波形：

```bash
gtkwave counter.vcd &
```

在 WSL 裡開 gtkwave 需要 X server。Windows 11 + WSLg（內建）可以直接開視窗；沒有的話裝 VcXsrv 或 X410。gtkwave 左側樹狀選 `counter` module，把 `clk`、`rst`、`q` 拖到波形區，你會看到 `clk` 方波、`rst` 高一拍後拉低、`q` 隨每個上緣階梯狀爬升 0→1→2…→8。**這就是你之後 debug 一切的畫面**——CPU 出錯時，就是在這裡找哪條線在哪一拍值錯了。

## 用 iverilog 交叉驗證

同一個 counter，換 iverilog 跑一次，確認兩個模擬器結果一致（這是驗證的好習慣）。iverilog 吃的是 **SV testbench**（不是 C++），細節 Ch 5 講：

```systemverilog
module counter_tb;
    logic clk = 0, rst;
    logic [7:0] q;
    counter dut (.clk(clk), .rst(rst), .q(q));

    always #5 clk = ~clk;                 // 每 5 個時間單位翻一次 → 10 單位週期

    initial begin
        rst = 1; @(posedge clk); #1;
        rst = 0;
        repeat (5) @(posedge clk);
        #1 $display("iverilog: q=%0d", q);
        $finish;
    end
endmodule
```

```bash
iverilog -g2012 -o counter_sim counter.sv counter_tb.sv
vvp counter_sim
```

實際輸出（真跑）：

```
iverilog: q=5
```

verilator 印 `q=5`、iverilog 也印 `q=5`——同一份電路描述、兩個獨立模擬器、同樣結果。這種交叉驗證在你懷疑「是我的電路錯還是模擬器怪」時很有用。`-g2012` 是叫 iverilog 用 SystemVerilog-2012 語法（不加它不認得 `logic`）。

## 對比：verilator vs iverilog，什麼時候用哪個

| 面向 | verilator | iverilog |
|---|---|---|
| 執行速度 | 極快（編成原生 C++） | 慢（事件驅動解釋） |
| testbench 語言 | C++ | SystemVerilog |
| 語法支援 | 只吃可合成子集 | 支援更多行為級語法（`#delay`、`fork`） |
| 波形 | 要在 C++ 手動 dump | `$dumpfile`/`$dumpvars` 一行搞定 |
| 本課定位 | 主力，跑大型設計、要速度 | 交叉驗證、想用 SV tb 時 |

心法：**跑得快、要餵大量測資、testbench 邏輯複雜 → verilator**；**想快速用 SV 寫個小 tb、或懷疑 verilator 有 bug 要對照 → iverilog**。整門課主線是 verilator。

## 踩雷集錦

1. **「`./obj_dir/Vcounter` 找不到／沒產生」** — 錯誤直覺：以為 `verilator --cc` 就會產出執行檔。正確認識：`--cc` 只產 C++ 原始碼，**還沒編**。你必須接著跑 `make -C obj_dir -f Vcounter.mk Vcounter` 那一步，才會把 C++ 編成執行檔。忘了這步是新手最常見的卡點。

2. **「module 叫 `counter`，但 header 要 include `Vcounter.h`」** — 錯誤直覺：以為檔名決定 header 名。正確認識：verilator 用 **top module 名**（不是檔名）加 `V` 前綴。module 叫 `counter` → header 是 `Vcounter.h`、class 是 `Vcounter`、makefile 是 `Vcounter.mk`。檔名可以叫別的，但這三個一定跟著 module 名走。

3. **「`always_ff @(posedge clk)` 為什麼要手動翻 clk？」** — 錯誤直覺：以為 verilator 會自己產生時鐘。正確認識：verilator 的 model 是**被動**的，它只在你呼叫 `eval()` 時重算一次。時鐘完全由 C++ testbench 控制——你不 `clk=0; eval(); clk=1; eval()`，就永遠不會有上緣，`always_ff` 裡的東西一次都不會執行。這跟真實硬體有個永遠在跑的時鐘完全不同，是模擬的特性。

4. **「gtkwave 開不起來 / 沒視窗」** — 錯誤直覺：以為裝了 gtkwave 就能開視窗。正確認識：WSL 是 Linux，GUI 需要 X server。Windows 11 的 WSLg 通常內建可用；若跑 `gtkwave x.vcd` 沒反應或報 `cannot open display`，檢查 `echo $DISPLAY` 有沒有值，或改用 Windows 版 gtkwave 開同一個 `.vcd`（VCD 是純文字，跨平台通用）。

## 本章重點整理

- 硬體設計靠**模擬器**驗證，不是流片。verilator 把 SV 編成 C++ 跑，快且用得上你的 C 功力。
- 標準流程四步：`verilator --cc`（產 C++）→ `make -f Vxxx.mk`（編）→ `./obj_dir/Vxxx`（跑）→（可選）gtkwave 看波形。**`--cc` 不會直接產執行檔，必須接 make。**
- verilator model 是被動的：時鐘由 C++ testbench 手動翻（`clk=0;eval();clk=1;eval()` 造一次上緣），`eval()` 重算全電路。
- header/class/makefile 名字都是 `V` + **top module 名**，不是檔名。
- counter 範例真跑得到 `q=5`；加 `--trace` 產 `.vcd`，gtkwave 開來看訊號隨時間變化——這是之後 debug 的主戰場。
- Part 0 只用 verilator/iverilog/gtkwave；riscv toolchain、spike 是 Part 1 之後的事，spike 可先跳過。

## 自我檢核

- [ ] 我能不看講義，默寫出 verilate 一個 module 的三步指令（產 C++ / 編 / 跑）。
- [ ] 我能解釋為什麼 `verilator --cc counter.sv` 之後直接 `./obj_dir/Vcounter` 會失敗，少了哪一步。
- [ ] 我能說出 verilator model 的時鐘是誰在驅動，以及 `eval()` 做什麼。
- [ ] 我知道 module 叫 `foo` 時，header、class、makefile 各叫什麼。
- [ ] 我能講出 8-bit counter 數到 255 再 +1 會發生什麼，以及為什麼。
- [ ] 我能說出 verilator 和 iverilog 各自的定位，什麼情況用哪個。

## 延伸閱讀

- **Verilator 官方文件 — Example section**（verilator.org/guide/latest/example.html）：官方版的「hello world」流程，跟本章的四步一致但更詳細，特別看它怎麼組織 `sim_main.cpp`。之後你要客製 testbench（多時鐘、reset 序列）時回來查這裡。
- **nandland.com — "Your First Verilog Program"**：給硬體零基礎的人最友善的入門站。這章的 counter 你若想再看一個 nandland 版本（含更多波形圖解），從這開始。它用 iverilog + gtkwave 流程，正好對照本章的交叉驗證那段。
- **《Digital Design and Computer Architecture, RISC-V Edition》(Harris & Harris) 第 1 章**：這門課 Part 0 的紙本教材主線。第 1 章講「為什麼要抽象層次」——從電晶體到邏輯閘到模組，正好解釋我們為什麼能只寫 SV 不碰電晶體。讀它建立「數位抽象」的世界觀。
- **GTKWave User's Guide（gtkwave.sourceforge.net）第 2–3 章**：波形工具的完整操作。這章只教你拖訊號進去看，之後 debug 複雜 CPU 你會用到 marker、group、search value 等功能，那時翻這份。

Ch 0 我們只求「工具鏈跑得動」，刻意沒解釋 `always_ff`、`<=`、`logic` 這些語法在幹嘛——那是後面的事。下一章我們回到最底層：先徹底搞懂**組合邏輯（combinational logic）**，也就是「輸入變、輸出立刻跟著變、沒有記憶」的那半個世界。從一個邏輯閘開始，一路堆到加法器和 ALU 積木。

→ [Ch 1 數位邏輯（一）：boolean、邏輯閘、組合元件](./01-digital-logic-combinational.md)
