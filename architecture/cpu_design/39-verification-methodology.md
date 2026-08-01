# Ch 39 — 驗證方法學：formal / riscv-formal / cocotb / UVM 速覽

> **目標**：這是本課最後一章正文。我們花了三十幾章「設計」一顆 core，但業界一個殘酷事實是：**驗證（verification）花的時間和人力，通常超過設計本身**。這章講為什麼驗證這麼貴、本課一路用的 **verilator + spike 對拍**屬於哪一類驗證、**directed vs constrained-random** 兩種測法的取捨、**functional coverage / covergroup** 怎麼量「測夠了沒」、**UVM** 速覽（工業標準驗證框架長怎樣）、**formal 形式驗證**（尤其 **riscv-formal** 怎麼對 core 做 ISA 一致性的**數學證明**）、以及 **cocotb**（用 Python 寫 testbench）。我們會用**真跑的 assertion** 示範自檢，把本課用過的手法對回這張方法學地圖。這是深挖章。
>
> **環境**：WSL + **iverilog**（SystemVerilog immediate assertion）。本章的 assertion 通過 / 失敗兩種輸出**皆真跑**。UVM（要商用模擬器）、riscv-formal 完整跑（要 SymbiYosys + solver，環境重）標「原理說明 / 未在本課環境跑完整證明」。

## 為什麼需要：驗證是設計的影子，而且更大

你做完 core，跑了 riscv-tests、跑了自己寫的 testbench，都過了——這代表 core 對了嗎？**不代表。** 它只代表「你測到的那些情況對了」。沒測到的角落（corner case）——某個罕見的 hazard 組合、某個對齊邊界的 load、某個 trap 發生在 branch delay 的瞬間——可能藏著 bug，而你的 testbench 從沒踩到。

業界的數字很殘酷：一個複雜 SoC 專案，**驗證的工程師人數和工時常常是設計的 2～3 倍**。原因是：

1. **狀態空間爆炸**：一顆 core 的內部狀態（暫存器、pipeline latch、cache、預測器）組合起來是天文數字，你不可能窮舉。
2. **bug 越晚發現越貴**：RTL 階段抓到改幾行；流片後（Ch 38 GDSII 送出去）抓到，是幾個月 + 百萬美元重做。所以要在模擬 / formal 階段把 bug 逼出來。
3. **「測過了」不等於「測夠了」**：跑一百個 test pass 很爽，但你**測到了設計的百分之幾**？沒有量化，你不知道還有多少沒碰過的角落。

這章就是講「怎麼系統性地把 bug 逼出來、怎麼知道測夠了」——這套學問叫**驗證方法學**。本課一路其實已經在用它的一部分（對拍、self-check），這章把它放進完整地圖，讓你知道自己站在哪、往上還有什麼。

## 先建立直覺：驗證像考試出題

把「設計 core」想成寫一篇論文，「驗證」就是**找一群人拿各種刁鑽問題來考它，直到考不倒**：

```
   directed test（指定題）    ： 出題老師手寫每一道題
     「1+1=? 2*3=? 邊界 0xFFFF+1=?」
     優點：針對已知重點、可讀
     缺點：你想不到的角落就不會出到

   constrained-random（隨機出題機）：定好規則，讓機器亂出海量題
     「隨機兩個數、隨機運算子，但排除除以 0」
     優點：撞出你想不到的組合
     缺點：得有辦法自動判對錯 + 知道「出夠了沒」

   coverage（改考卷統計）    ： 統計「這批題涵蓋了哪些考點」
     「加法考了、溢位考了、但『負數移位』一次都沒考到！」

   formal（數學證明）        ： 不出題，直接證明「這題型永遠對」
     「證明:對任意 a,b，這個加法器 = a+b。用邏輯推演，不試數字。」
```

四種手法不是互斥，是搭配用：directed 打已知重點、random 掃未知角落、coverage 告訴你還缺哪、formal 對關鍵性質給出「窮舉級」的保證。本課主要用了 directed（自己寫 test）+ 一種特殊的 self-check（對拍），這章帶你認識其餘。

## 核心概念:本課的對拍屬於哪一類——co-simulation / self-checking

本課的驗證主力是**verilator 模擬 core + 和 spike 逐指令對拍**（Final Project 的核心）。這在方法學裡叫幾件事的組合：

- **self-checking testbench（自檢測試平台）**：testbench 不是印出波形讓你人眼看，而是**自己判對錯**——把 core 每條指令執行後的架構狀態（PC、暫存器）和一個「黃金參考」比，不一致就報錯。人眼看波形是最原始、最不可靠的驗證；self-check 是所有現代方法的地基。
- **reference model / golden model（參考模型）**：spike 是 RISC-V 官方的 ISA 模擬器，它「絕對正確地」執行指令。拿它當**黃金參考**，你的 core 只要每步和 spike 一致就對。這叫 **co-simulation（協同模擬）** 或 **lockstep comparison（逐步對拍）**。
- **directed test 為主**：riscv-tests、你自己編的 ELF——這些是**人指定**的測試程式，屬 directed。

所以本課的驗證定位：**directed test + self-checking + 對 golden model 逐步對拍**。這是很扎實的一套，工業界也大量用（拿 spike / QEMU 當 golden model 對拍 RTL core 是 RISC-V 驗證的標準做法）。你已經在做真正的驗證方法，只是還沒補上 constrained-random、coverage、formal 這幾塊。

## 核心概念：directed vs constrained-random

**directed test**：人手寫每個測試情境。「測 ADD 溢位、測 load-use hazard、測 branch mispredict」——你**明確知道**在測什麼。

**constrained-random verification（CRV，約束隨機驗證）**：定義**約束**（constraint），讓工具**隨機生成**海量合法激勵。例如「隨機生成合法 RV32I 指令流，但 register 用 x1~x31、位址對齊、不含未定義 opcode」，然後灌幾百萬條進 core，配 golden model 對拍。

```
   directed:                          constrained-random:
   人寫：test_add_overflow()          定約束：
        test_load_use()                 rand op ∈ {合法 opcode}
        test_branch()                   rand rs1,rs2 ∈ x1..x31
        ...（想得到的都寫）              constraint: addr 4-byte 對齊
                                       → 工具生成 1,000,000 條隨機指令流
   優點：可讀、針對重點                 優點:撞出人想不到的組合
   缺點:只測到你想得到的               缺點:要 self-check(不能人眼看百萬條)
                                            + 要 coverage 知道測夠沒
```

兩者的關鍵取捨：directed **可讀、聚焦**，但**只覆蓋你想得到的**——最陰的 bug 往往在你沒想到的組合裡。constrained-random **能撞出意外角落**，但前提是**必須有 self-check**（百萬條沒法人眼看）**和 coverage**（否則不知道隨機到底掃到哪、還缺什麼）。實務上兩者並用：directed 打已知風險點、CRV 掃廣度。

本課用 directed（riscv-tests + 自寫程式）。要進 CRV，你需要的兩塊拼圖是 self-check（本課對拍已有）+ 隨機指令生成器 + coverage——RISC-V 有現成的隨機指令生成器（如 riscv-dv），配你的對拍框架就能做 CRV。

## 核心概念：functional coverage——「測夠了沒」的量化

跑了一百萬條隨機指令，怎麼知道**測到位了**？答案是 **coverage（覆蓋率）**。兩大類：

- **code coverage（程式碼覆蓋率）**：RTL 的每一行 / 每個分支 / 每個 FSM 狀態有沒有被執行到。工具自動算。「有 5% 的 RTL 從沒被任何 test 執行過」——那 5% 完全沒驗。
- **functional coverage（功能覆蓋率）**：**你定義的功能點**有沒有被測到。這要人寫 **covergroup**（覆蓋群組）宣告「我在乎哪些情境」。

covergroup 的直覺（SystemVerilog 語法示意）：

```systemverilog
   covergroup alu_cov @(posedge clk);
     op:  coverpoint alu_op { bins all_ops[] = {[0:9]}; }   // 10 種運算都測到？
     // cross：組合覆蓋——負運算元 × 移位 這種組合有沒有測到
     signs: coverpoint a[31];                               // a 是不是負數
     cross op, signs;                                       // 每種 op × 正/負 都測到？
   endgroup
```

跑完 test，工具報「op=SRA 且 a 為負」這個 cross bin **命中 0 次**——這正是 Ch 9 那個算術右移踩雷點！coverage 告訴你「你從沒測過負數的 SRA」，逼你補一個 directed test 或調整隨機約束去打它。

**coverage 的價值**：把「我覺得測夠了」變成「還有 X% 功能點沒碰、具體是哪些」。CRV + coverage 是絕配——隨機灌激勵、coverage 盯著還缺哪、缺的補 directed 或調約束去打，反覆逼近 100%。這是工業驗證收斂的核心迴圈（coverage-driven verification）。

本課沒用 covergroup（verilator 4.038 對 SV coverage 支援有限，且教學上 directed 對拍已夠說明概念）。但你要知道:真專案「簽收」一個模組，看的不是「pass 幾個 test」，是「coverage 到幾 %」。

## 核心概念：UVM 速覽——工業標準驗證框架

當驗證變大（一整顆 SoC、多個介面），手寫 testbench 會失控。**UVM（Universal Verification Methodology，通用驗證方法學）** 是業界標準的 SystemVerilog 驗證框架——一套**類別庫 + 方法論**，把 testbench 拆成標準化、可重用的元件：

```
   UVM testbench 的標準結構：
   ┌─────────────────────────────────────────────┐
   │ test        （選哪個情境、設參數）            │
   │  └ environment                               │
   │      ├ agent（管一個介面）                    │
   │      │   ├ sequencer（產生交易序列）          │
   │      │   ├ driver   （把交易變成腳位訊號 →DUT)│
   │      │   └ monitor  （從 DUT 觀察訊號→交易）  │
   │      ├ scoreboard（比對 DUT 輸出 vs 參考）     │
   │      └ coverage collector（收 functional cov）│
   └─────────────────────────────────────────────┘
                    ↕ 對接 DUT（你的 core）
```

UVM 的價值是**標準化 + 可重用**：driver / monitor / scoreboard 各司其職、介面可換、sequence 可組合，讓大團隊協作驗大晶片。它重度依賴 SystemVerilog 的 OOP（class、繼承、constrained-random、covergroup），且**幾乎只能在商用模擬器（VCS / Questa / Xcelium）上跑**——verilator 對 UVM 支援極有限。

**本課不用 UVM，也不需要**：UVM 是給「大、多介面、多人團隊」的重型框架，對一顆教學 core 是殺雞用牛刀。但你要認得它——面試 IC 驗證職位，UVM 是共同語言；看真專案的 testbench，八成是 UVM 結構。你本課用的「driver 灌激勵、monitor 看輸出、scoreboard 對拍」的分工，其實就是 UVM 概念的手工簡化版——你已經在用它的思想。**UVM 完整範例要商用模擬器，本課環境未實測，原理說明。**

## 核心概念：formal 驗證與 riscv-formal——不試數字的證明

前面所有手法（directed / random）都是**試**——餵激勵、看反應。再多也是有限的樣本，測不到的永遠可能藏 bug。**formal verification（形式驗證）** 走完全不同的路：**不試任何具體數字，用數學（SAT/SMT solver）證明某個性質對「所有可能輸入」都成立**。

直覺對比：

```
   模擬（試）：a=5,b=3 → 8 ✓  a=1,b=1 → 2 ✓  ...試一百萬組，都對
              （但第一百萬零一組呢？不知道，沒試到）

   formal（證）：對「任意」32-bit a,b，證明 adder(a,b) == a+b
              solver 窮舉邏輯空間（不是逐一試值），要嘛給出證明、
              要嘛吐一個反例（counterexample）：「a=0x8..,b=0x8.. 時錯」
```

formal 的兩種主力：

- **property checking / assertion-based**：寫 assertion（性質斷言），如「這個 FIFO 永遠不會 overflow」「grant 之前一定先有 request」，solver 證明它在所有可達狀態下成立，或給反例。
- **equivalence checking（等價檢查）**：證明兩個設計（如 RTL vs 合成後的 netlist、優化前 vs 後）**功能完全等價**——Ch 38 合成後常做這個，確保 synthesis 沒改變行為。

**riscv-formal** 是專為 RISC-V core 做的 formal 框架，幹一件極有力的事：**證明你的 core 每條指令的行為符合 RISC-V ISA 規範**。它的核心叫 **RVFI（RISC-V Formal Interface）**——你在 core 裡拉出一組訊號（每條 retire 的指令是什麼、讀寫了哪些暫存器、PC 怎麼變），riscv-formal 用 solver 證明「不存在任何指令序列，讓 core 的行為偏離 ISA 定義」。它能抓出對拍**可能漏掉**的 bug——因為它不靠你餵的測試程式，它證明**所有**指令序列。

```
   riscv-formal 檢查的性質（一部分）：
   - reg 一致性：寫進 rd 的值符合該指令的 ISA 定義
   - PC 前進：每條指令後 PC 按規範更新（含 branch/jump）
   - x0 恆為 0、對齊、非法指令觸發 trap ...
   → solver 對「所有可能的指令流」證明，不存在違反的情況
```

這比對拍更強：對拍證明「你跑的這些程式 core 和 spike 一致」，riscv-formal 證明「**任何**程式 core 都符合 ISA」。picorv32、多個開源 core 都用 riscv-formal 驗過。**代價**：要把 core 接上 RVFI、要 solver（SymbiYosys + boolector/yices）、且 formal 有「狀態爆炸」極限（深 pipeline、大狀態可能證不動，要 bounded 到有限步數）。**完整跑 riscv-formal 對本課 core 做 ISA 證明環境重（SymbiYosys + solver），本章原理說明、未在本課環境跑完整證明；但 assertion 的概念我們真跑一個小的。**

## 底層機制：本課風格的 assertion——真跑一個

formal 和 simulation 之間有座橋：**assertion（斷言）**。你在設計裡寫「這個條件必須成立」，模擬時它被**當場檢查**（違反就報錯），送進 formal 工具時它變成**要被證明的性質**。同一個 assertion，兩種用法。

本課其實一路在用 assertion 的精神——testbench 裡「got != expected 就報錯」就是最樸素的 assertion。我們把它寫成 SystemVerilog 的 **immediate assertion**，對本課 ALU 真跑一次。testbench（`assert_demo.sv`）：

```systemverilog
  task check(input [31:0] ta, input [31:0] tb, input [3:0] top, input [31:0] exp);
    a = ta; b = tb; alu_op = top;
    #1;                                    // 等組合邏輯穩定
    assert (result == exp)                 // ← immediate assertion：條件必須成立
      else begin                           //   不成立就執行 else（報錯 + 計數）
        $display("FAIL: a=%h b=%h op=%b  got=%h  exp=%h", ta, tb, top, result, exp);
        errors++;
      end
  endtask
  // ... check(5,3,ADD,8); check(0x80000000,4,SRA,0xF8000000); ...
```

用 iverilog 跑（**真實輸出**）：

```
$ iverilog -g2012 -o asim assert_demo.sv alu_iv.sv && vvp asim
alu_iv.sv:11: vvp.tgt sorry: Case unique/unique0 qualities are ignored.
ALL ASSERTIONS PASSED
```

全過。（那行 `sorry: unique/unique0 ... ignored` 是 iverilog 提示它不強制檢查 `unique case` 的「唯一性」——這本身是個線索：`unique case` 也是一種 assertion，在支援的工具裡它會**斷言「不會有多個分支同時符合、不會漏 case」**，違反就報 runtime 錯。iverilog 忽略它，商用工具和 formal 會拿它當性質檢查。）

現在故意把一組**期望值寫錯**（ADD 5+3 期望寫成 9），看 assertion 抓出來（**真實輸出**）：

```
$ vvp afail
FAIL: a=00000005 b=00000003 op=0000  got=00000008  exp=00000009
TOTAL FAILURES: 1
```

assertion 當場抓到「got=8 但我斷言應該是 9」——這就是 assertion 的價值：**把「正確性條件」寫進程式，讓工具當場替你查**，而不是靠人眼掃波形。把這個 immediate assertion 換成 SVA（SystemVerilog Assertions）的時序斷言（`assert property (@(posedge clk) req |-> ##[1:3] gr)`），再送進 formal 工具，就從「模擬時抽查」升級成「數學上對所有情況證明」——這是 assertion 一路通到 formal 的階梯。

## 核心概念：cocotb——用 Python 寫 testbench

**cocotb（coroutine-based cosimulation testbench）** 讓你**用 Python 而非 SystemVerilog 寫 testbench**。它透過標準介面（VPI/VHPI/FLI）驅動任何模擬器（含 iverilog、verilator）裡的 DUT——你的 RTL 不變，testbench 從 SV / C++ 換成 Python。

```python
   # cocotb testbench 示意（Python 驅動本課 ALU）
   import cocotb
   from cocotb.triggers import Timer

   @cocotb.test()
   async def test_add(dut):
       dut.a.value = 5
       dut.b.value = 3
       dut.alu_op.value = 0    # ADD
       await Timer(1, units="ns")
       assert dut.result.value == 8, f"got {dut.result.value}"
```

cocotb 的吸引力：**Python 生態全部能用**——用 numpy 算黃金參考、用 pytest 組織、用 Python 的 random / constraint 函式庫做 constrained-random、把 golden model（甚至直接 import spike 的 Python binding）寫在 testbench 裡。對「軟體背景轉硬體驗證」的人，cocotb 的門檻遠低於 SystemVerilog + UVM。它能配 verilator 跑，跟本課環境相容。

**本課用 C++ testbench（verilator 原生）而非 cocotb**——因為 verilator 的 C++ 介面最直接、最快、和本課「一路 C++ tb + spike 對拍」一致。但 cocotb 是你會 Python、想快速搭驗證環境時極值得的工具，尤其做 CRV（用 Python 的隨機和約束）比純 SV 順手。**cocotb 可在本課 iverilog/verilator 上跑（要 pip install cocotb），本章示意其定位，未在本課逐字跑。**

## 對比取捨表：驗證手法全景

| 手法 | 怎麼運作 | 抓得到 | 抓不到 / 代價 | 本課用了嗎 |
|---|---|---|---|---|
| 人眼看波形 | 印波形人工檢查 | 明顯錯 | 主觀、不可規模化 | 除錯時偶爾 |
| **directed test** | 人手寫測試情境 | 已知風險點 | 想不到的角落 | **是**（riscv-tests + 自寫）|
| **self-check + 對拍** | 每步比 golden model | 任何和參考的偏差 | golden 沒跑到的路徑 | **是**（spike lockstep）|
| **constrained-random** | 隨機生成合法激勵 | 意外組合 | 需 self-check + coverage | 否（可加 riscv-dv）|
| **functional coverage** | covergroup 量功能點 | 「還缺哪沒測」 | 不抓 bug，只量覆蓋 | 否 |
| **assertion (SVA)** | 條件寫進設計、當場查 | 違反不變量 | 要人想到寫哪些性質 | **是**（本章真跑）|
| **UVM** | 標準化可重用 testbench 框架 | 大型多介面驗證 | 重、要商用模擬器 | 否（原理說明）|
| **formal / riscv-formal** | solver 證明所有情況 | 對拍漏掉的角落 bug | 狀態爆炸、要接 RVFI + solver | 否（原理說明）|
| **cocotb** | Python 寫 testbench | 同上，換語言 | 效能略遜原生 C++ | 否（示意）|

**取捨邏輯**：沒有單一手法夠。工業實務是**分層**——directed 打已知重點、CRV + coverage 掃廣度、assertion 埋不變量、formal 對關鍵性質（尤其 ISA 一致性）給窮舉級保證。本課選了「directed + self-check 對拍 + 一點 assertion」這個**性價比最高的子集**：它扎實、免費、教學清楚，且正是工業驗證的地基。往上補 CRV / coverage / formal 是深化，不是推翻你學的。

## 踩雷區

**雷 1：以為「所有 test 都 pass」= core 沒 bug。**
- 錯誤直覺：「跑了一百個 test 全過，core 對了」。
- 正確認識：pass 只代表**你測到的那些情況**對。沒測到的角落可能全是 bug。要問的是「我測到了設計的百分之幾」（coverage），不是「過了幾個 test」。工業界簽收模組看 coverage %，不看 test 數。「測過」和「測夠」是兩回事。

**雷 2：以為 constrained-random 灌越多隨機就越安心。**
- 錯誤直覺：「隨機灌一億條指令，一定測透了」。
- 正確認識：隨機**沒有方向**——它可能反覆撞同幾個容易到的情境，某些角落永遠隨機不到（機率極低的組合）。沒有 **coverage** 盯著，你不知道那一億條到底掃到哪、還有哪些功能點是 0 次。CRV **必須配 coverage** 才有意義，否則是「跑很久但不知道測到啥」。而且隨機必須配 self-check（一億條沒法人眼看）。

**雷 3：以為 formal 驗證能全自動證任何 core 全對。**
- 錯誤直覺：「有 formal 就不用寫 test 了，一鍵證明 core 全對」。
- 正確認識：formal 有**狀態爆炸**的硬極限——深 pipeline、大 cache、複雜狀態的完整證明可能**跑不動**（solver 爆記憶體 / 不收斂），常只能 bounded（證有限步數內對）。而且 formal 只證**你寫出來的性質**——沒寫到的性質它不管。riscv-formal 強在「ISA 一致性」這個定義好的性質，但它不會自己知道你在乎什麼別的。formal 是**補**模擬的角落、給關鍵性質窮舉級保證，不是取代所有測試。

**雷 4：把對拍當成「終極驗證」，以為過了 spike 對拍就萬無一失。**
- 錯誤直覺：「和 spike 逐指令一致，core 完美了」。
- 正確認識：對拍只證明「**你跑的這些程式**，core 和 spike 一致」。它受限於你餵的測試程式的覆蓋——沒跑到的指令組合、沒觸發的 hazard / trap 時序，對拍照樣漏。這正是 riscv-formal 補的：它證「**任何**程式都符合 ISA」，不靠你餵什麼。對拍很強、是本課地基，但它是 directed 家族，天花板是你的測試集覆蓋率。

**雷 5：以為 UVM / formal 這些「高級」手法一定比本課的對拍好、該全上。**
- 錯誤直覺：「業界用 UVM 和 formal，我這對拍太 low 該換掉」。
- 正確認識：手法要配規模和目標。UVM 是給大型多介面團隊協作的重型框架，對一顆教學 core 是過度工程；formal 對某些性質無敵、對另一些跑不動。本課的「directed + self-check 對拍」是**驗一顆單核最扎實、最經濟的地基**，工業界驗 RISC-V core 也大量用 spike/QEMU 對拍。對的做法是**分層搭配**（對拍打地基、formal 補 ISA 角落、coverage 量廣度），不是「用最貴的取代最基本的」。

## 進階延伸

- **coverage-driven verification 的收斂迴圈**：真專案的驗證是個迴圈——跑 CRV → 收 coverage → 看還缺哪些 bin → 補 directed test 或調隨機約束去打那些 bin → 再跑,直到 coverage 到目標（常要 95%+ functional + 100% code）。這個迴圈怎麼收斂、怎麼判斷「夠了」是驗證工程師的核心手藝。
- **riscv-dv：RISC-V 的官方隨機指令生成器**：Google/CHIPS 開源的 riscv-dv 用 SystemVerilog constraint 生成合法隨機 RV 指令流，配 spike 當 golden、你的 core 對拍，就組出完整的 CRV 環境。想把本課對拍升級成 constrained-random，這是現成的一塊拼圖。
- **SVA（SystemVerilog Assertions）的時序斷言**：本章真跑的是 immediate assertion（組合、當場查）。SVA 還有 concurrent assertion，能寫**跨多個週期**的性質（`req |-> ##[1:3] ack`：req 之後 1~3 拍內必有 ack）。這是驗 pipeline / 協定 / handshake（如 Ch 30 AXI 的 valid/ready）不變量的利器，也是 formal 的主要輸入語言。
- **equivalence checking 在合成流程的角色**：Ch 38 合成後，工業界會做 RTL vs netlist 的 formal 等價檢查，確保 synthesis 沒偷改行為。yosys 也有 `equiv` 相關 pass。這是「synthesis 可信」的保證機制，把 Ch 38 和本章接起來。
- **bug 的經濟學與 shift-left**：驗證方法學的商業動機是「越早抓 bug 越便宜」——所以業界拼命把驗證「左移（shift-left）」到設計早期（甚至和 RTL 同步寫 assertion）。理解這個經濟學，就懂為什麼驗證人力常多於設計：不是驗證比較笨，是把 bug 擋在流片前的價值極高。

## 本章重點整理

- **驗證常比設計花更多時間 / 人力**——因為狀態空間爆炸、bug 越晚越貴（流片後百萬美元）、且「測過 ≠ 測夠」。
- 本課的 **verilator + spike 對拍** = **directed test + self-checking + 對 golden model 逐步對拍（co-simulation）**，是工業界驗 RISC-V core 的標準地基之一。
- **directed**（人寫、聚焦、漏想不到的角落）vs **constrained-random**（隨機掃廣度，但必須配 self-check + coverage）。
- **functional coverage / covergroup** 把「測夠了沒」量化成「哪些功能點還是 0 次」——CRV + coverage 是工業收斂的核心迴圈。
- **UVM** 是標準化可重用的重型 testbench 框架（大型多介面 + 商用模擬器）；本課的 driver/monitor/scoreboard 分工是它的手工簡化版。
- **formal / riscv-formal** 用 solver **證明所有情況**——riscv-formal 靠 RVFI 證 core 符合 ISA，比對拍強（不靠你餵的程式），代價是狀態爆炸極限 + 要接 solver。
- **assertion** 是 simulation 和 formal 的橋：同一條斷言，模擬時當場查、formal 時被證明。本章真跑 immediate assertion，pass 和 fail 兩種輸出皆真。
- **cocotb** 用 Python 寫 testbench，對軟體背景 + CRV 特別順手。
- 正解是**分層搭配**（對拍地基 + assertion 埋不變量 + coverage 量廣度 + formal 補 ISA 角落），不是用最貴的取代最基本的。

## 自我檢核

- [ ] 我能解釋為什麼「所有 test pass」不等於「core 沒 bug」，以及該用什麼量化（coverage）。
- [ ] 我能說出本課 verilator + spike 對拍在方法學裡的定位（directed + self-check + co-sim）。
- [ ] 我能講清 directed vs constrained-random 的取捨，以及為什麼 CRV 必須配 self-check 和 coverage。
- [ ] 我能解釋 functional coverage / covergroup 在量什麼，舉一個「cross bin 命中 0 次」暴露漏測的例子。
- [ ] 我能說出 formal 和 simulation 的本質差異（證所有情況 vs 試有限樣本），以及 formal 的狀態爆炸極限。
- [ ] 我能解釋 riscv-formal 靠 RVFI 證什麼、為什麼它比對拍更強、以及它的代價。
- [ ] 我能寫一個 immediate assertion 自檢一個模組，並說明 assertion 怎麼從模擬用法升級成 formal 性質。

## 延伸閱讀

- **Chris Spear & Greg Tumbush, 《SystemVerilog for Verification》**：驗證方法學的標準教科書。從 self-check、constrained-random、covergroup 到 UVM 的基礎（OOP / class / randomize）逐章建立，本章所有 SV 驗證概念的完整版在這。想把本章的「速覽」變成能動手寫的技能，這是主教材，尤其 coverage 和 randomization 兩章。
- **riscv-formal GitHub（github.com/YosysHQ/riscv-formal）**：本章 formal 那段的真身。讀它的 README 和 RVFI 規範文件，看「怎麼把一顆 core 接上 RVFI、solver 證明哪些 ISA 性質」。它附了對 picorv32 等真 core 的完整範例——想親手對本課 core 做 ISA 一致性證明，這是唯一的開源起點（配 SymbiYosys）。
- **cocotb 官方文件（docs.cocotb.org）**：想用 Python 搭 testbench（尤其你 Python 比 SV 熟、或想做 CRV）。讀它的 quickstart 和 "Coroutines and Triggers"，配 verilator 或 iverilog 就能對本課 ALU / core 跑起來。是把本課驗證環境從 C++ 換成 Python 生態的入口。
- **《Computer Architecture: A Quantitative Approach》第 1 章的 dependability / verification 與 fallacies 段落**：從系統角度談「為什麼正確性難、為什麼要量化」以及常見的驗證謬誤。把本章「測過 ≠ 測夠」的直覺放進更大的可靠性框架，理解驗證在整個設計流程裡的位置。
- **yosys / SymbiYosys 的 formal 文件（yosyshq.readthedocs.io，SymbiYosys）**：想真的跑 formal（equivalence check、property check、bounded model check）而不花錢，SymbiYosys 是開源前端（配 boolector/yices solver）。讀它的 tutorial，對本章真跑的 assertion 從「模擬抽查」升級成「formal 證明」，親手體驗 solver 給反例是什麼感覺。

這是本課最後一章正文。你從 boolean 閘一路做到 pipelined RV32I core，補齊了 ISA 課和 compiler 課之間的 RTL / 微架構斷層，又在 Part 6 看清了往上（OoO、真 core、矽、驗證）的整片地景。剩下的是把一切收攏成一顆能跑真 ELF、和 spike 對拍的完整 core——那就是 Final Project。

→ [Final Project：完整 pipelined RV32I core + spike 對拍](./final-project-pipelined-rv32i-core.md)
