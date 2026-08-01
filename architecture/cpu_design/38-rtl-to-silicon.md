# Ch 38 — 從 RTL 到晶片：synthesis、STA、FPGA 流程原理

> **目標**：我們一路寫 SystemVerilog、用 verilator 模擬——但模擬跑的是 **RTL 的行為**，沒有一個真的閘、沒有一條真的線、沒有真的時間。這章走完 **RTL → synthesis → gate netlist → placement & routing → GDSII** 的完整實體流程，搞懂 **STA（static timing analysis）** 和 setup/hold、timing closure 是什麼、**FPGA 流程**（synth/map/place/route/bitstream）和 ASIC 差在哪、以及面積 / 功耗 / 時脈的三角取捨。我們會用 **yosys 對本課的 ALU 真跑一次合成**，看它從一個 `case` 變成幾百個邏輯閘，貼真實輸出。這是深挖章。
>
> **環境**：WSL + **yosys 0.9**（`apt-get install yosys` 裝好）。yosys 合成本課 ALU 的輸出**皆真跑**。商用 ASIC 流程（真 P&R、真 GDSII、真製程 STA）與上 FPGA **未實測**，標「原理說明」——那要商用 EDA 工具（Synopsys/Cadence）或 FPGA 板，非本課零成本環境。

## 為什麼需要：模擬跑的是「假設」，不是矽

verilator 模擬告訴你「這段 RTL 的行為對不對」——`add` 有沒有算對、pipeline 有沒有 forward 對。但它**不知道**：

- 你這個 `a + b` 在真晶片裡是多少個閘、佔多大面積？
- 訊號從暫存器出來、穿過 ALU、到下一個暫存器，要花幾奈秒？能不能在一個時脈週期內到？
- 這顆晶片跑起來耗多少電？

這些問題模擬答不了，因為模擬裡沒有物理——沒有閘的延遲、沒有線的電阻電容、沒有製程。要回答它們，RTL 必須經過一連串工具，一步步「落到物理」：先變成閘（synthesis）、閘擺到晶片上哪個位置（placement）、閘之間用金屬線連起來（routing）、算真實延遲確認時序過關（STA）、最後產出製造用的圖檔（GDSII）。

**這章的價值**：讓你知道你寫的每一行 RTL 最後變成什麼、時脈為什麼有上限（Ch 24 的 critical path 在這裡變成真數字）、面積 / 功耗 / 頻率為什麼不能全都要。不懂這層，你寫 RTL 時對「這樣寫合成出來會怎樣」是瞎的。

## 先建立直覺：從食譜到蓋房子

把 RTL 想成**建築的設計圖（藍圖）**，把做出真晶片想成**真的蓋一棟樓**：

```
   RTL（.sv）           = 藍圖：「這裡要一間房、那裡要條走廊」（行為/結構意圖）
      │  synthesis        ↓ 把藍圖翻成「用哪些標準建材」（磚、樑、梁 = 標準邏輯閘）
   gate netlist         = 材料清單 + 接線圖：「用 479 個 AND、404 個 MUX...怎麼接」
      │  floorplan/place  ↓ 決定每塊磚擺在工地哪個座標
   placed design        = 每個閘的實體位置
      │  routing          ↓ 拉電線把磚跟磚接起來（金屬層佈線）
   routed design        = 完整的實體佈局 + 佈線
      │  STA              ↓ 量「電流從這頭到那頭要多久」，確認趕得上時脈
   timing-closed         = 時序收斂：所有路徑都在週期內
      │  sign-off         ↓ 產出製造圖檔
   GDSII                = 交給晶圓廠的「施工圖」，光罩據此做
```

每一步都在「把抽象往物理推」：藍圖不管磚多大，材料清單開始有尺寸，擺放開始有座標，佈線開始有真實的線長（線長 = 延遲 = 為什麼位置很重要）。模擬停在「藍圖行為對不對」，這章講藍圖之後的整條路。

## 核心概念：synthesis——RTL 變成閘

**synthesis（邏輯合成）** 把 RTL 翻譯成 **gate-level netlist（閘級網表）**：一堆標準邏輯閘（AND/OR/NOT/MUX/DFF...）+ 它們怎麼連。它做兩件事：

1. **翻譯**：`a + b` → 一串加法器邏輯（全加器鏈或更聰明的結構）；`case` → 一棵 mux 樹；`always_ff` → D flip-flop。
2. **最佳化**：常數摺疊、消死碼、共用子表達式、依目標（面積 / 速度）重組邏輯。

合成的輸出從「你寫的 RTL」變成「純結構的閘連接」——沒有 `if`、沒有 `case`、沒有 `+`，只剩閘和線。這正是 Ch 37 說「Chisel 生成 Verilog、最終還是進 synthesis」的那個 synthesis。

我們**真跑一次**。拿本課 Ch 9 的 `alu.sv`，用 yosys 合成。先看合成的**兩個層次**。

**第一層：粗粒度 RTL cell**（`proc; opt` 之後，運算子還在，尚未拆成基本閘）——真實 yosys 輸出：

```
$ yosys -p "read_verilog -sv alu.sv; hierarchy -top alu; proc; opt; stat"

=== alu ===
   Number of wires:                 28
   Number of wire bits:            438
   Number of public wires:           6
   Number of public wire bits:     106
   Number of cells:                 24
     $add                            1     ← 你寫的 a + b
     $and                            1     ← a & b
     $eq                             9     ← 那些 == 比較（含 zero flag 和 case 選擇）
     $logic_not                      2
     $lt                             2     ← SLT / SLTU 的 < 比較
     $mux                            2
     $or                             1     ← a | b
     $pmux                           1     ← case 的多路選擇（priority mux）
     $shl                            1     ← a << shamt
     $shr                            1     ← a >> shamt（SRL）
     $sshr                           1     ← $signed(a) >>> shamt（SRA）
     $sub                            1     ← a - b
     $xor                            1     ← a ^ b
```

看這個對照——你在 `case` 裡寫的每個運算，合成器都認出來變成一個對應的 RTL cell：`a+b`→`$add`、`a<<shamt`→`$shl`、`$signed(a)>>>shamt`→`$sshr`（**注意它正確辨識成算術右移 `$sshr` 而非邏輯右移 `$shr`**——這就是 Ch 9 那個 `$signed` 踩雷點在合成層的體現，寫錯這裡會變 `$shr`，硬體行為就錯了）。那個 `$pmux` 是整個 `case` 的多路選擇器。10 種運算全在，一個不漏。

**第二層：拆成基本閘**（`techmap` 之後，所有運算子攤成 AND/OR/NOT/MUX/XOR）——真實 yosys 輸出：

```
$ yosys -p "read_verilog -sv alu.sv; hierarchy -top alu; proc; opt; techmap; opt; stat"

=== alu ===
   Number of wires:                375
   Number of wire bits:           1864
   Number of cells:               1485
     $_AND_                        479
     $_MUX_                        404
     $_NOT_                         44
     $_OR_                         425
     $_XOR_                        133
```

**這就是你的 ALU 的真身**：那個看起來人畜無害的 32 行 `case`，攤成 **1485 個基本邏輯閘**（479 AND + 425 OR + 404 MUX + 133 XOR + 44 NOT）、375 條內部線。你寫 `a + b` 一個符號，合成器展開成一整排全加器的 AND/OR/XOR；`case` 的十選一變成 404 個 MUX 的選擇網路。

**這是本章最重要的一次「親眼看見」**：RTL 的一行運算 ≠ 一個閘。抽象和物理之間隔著 synthesis 這一層展開。你寫 code 時要有這個感覺——多一個運算、多一個 case 分支，底下是幾十上百個閘的面積和延遲。

（真實 ASIC 流程還會再一步：把這些通用閘 map 到**特定製程的標準單元庫**（standard cell library，如台積電 7nm 的 `AND2_X1`、`DFF_X2`），這步要商用工具 + 廠商的 cell 庫，**未實測，本課到通用閘為止**。yosys 也能接 `abc` + Liberty 庫做映射，但沒有真 cell 庫時數字沒意義，故不編。）

## 核心概念：STA——時脈為什麼有上限

Ch 24 講過 critical path 決定最高時脈。**STA（static timing analysis，靜態時序分析）** 就是**不跑模擬、純用延遲數字算出「所有路徑最慢那條要多久」**的工具。「靜態」= 不餵測資、不模擬，純看電路結構 + 每個閘的延遲，窮舉所有路徑算時序。

STA 檢查兩個約束：

```
   一段時序路徑：
   [ FF_A ]──► 組合邏輯（一堆閘）──► [ FF_B ]
      ↑ clk                              ↑ clk
      └──────────── 同一個時脈 ──────────┘

   setup 檢查（能不能趕上）：
     資料必須在下一個 clk 邊緣「之前」穩定到達 FF_B
     T_clk ≥ T_clk-to-Q + T_邏輯延遲 + T_setup + T_線延遲
            （若不滿足 → setup violation → 得降頻或優化路徑）

   hold 檢查（會不會太快）：
     資料不能「太早」變，把同一拍 FF_B 還沒鎖住的舊值蓋掉
     T_clk-to-Q + T_邏輯延遲 ≥ T_hold
            （若不滿足 → hold violation → 得加 buffer 拖慢）
```

- **setup violation（趕不上）**：組合邏輯太慢，資料在時脈邊緣前來不及穩定。修法：降頻（拉長週期）、或把 critical path 上的邏輯優化 / 拆級（pipeline，Ch 13 的動機在此變成硬約束）。
- **hold violation（太快）**：資料變太快，在時脈邊緣「當下」就把接收暫存器還沒鎖住的舊值污染了。修法：在快路徑上塞 buffer（delay cell）拖慢它。hold 違規不能靠降頻解決（它跟週期長度無關），只能改電路。

**timing closure（時序收斂）** = 反覆調整（重新合成、換擺放、加 buffer、拆 pipeline）直到**所有路徑的 setup 和 hold 都滿足**。這是晶片設計最磨人的階段之一——改一個地方，可能拉壞另一條路徑，來回幾十輪。「setup 靠降頻或拆級，hold 靠加延遲，兩者要同時滿足」是 timing closure 的核心張力。

把 Ch 24 接起來：你在 Ch 24 手算的 critical path（最慢的組合路徑）決定的最高時脈，STA 就是那個「窮舉所有路徑、算出真數字、逐條檢查 setup/hold」的自動化工具。真數字要有真製程的閘延遲（Liberty 檔），**本課 verilator 沒有時序、算不出 ns，這步 ASIC STA 未實測，原理說明**。

## 核心概念：placement & routing——閘擺哪、線怎麼拉

netlist 只說「哪些閘、怎麼連」，沒說**擺在晶片哪個位置**。**P&R（placement and routing）** 幹這個：

- **floorplan**：規劃晶片大區塊——core 放哪、cache SRAM 放哪、I/O pad 沿邊擺。
- **placement**：把每個標準單元（閘）放到具體座標。放得好壞直接影響線長。
- **clock tree synthesis（CTS）**：建時脈分佈網路，讓時脈同時到達所有 FF（否則 clock skew 會製造時序問題）。
- **routing**：用多層金屬線把閘之間、按 netlist 連起來。現代晶片十幾層金屬。

**為什麼位置這麼重要？** 因為**線也有延遲**——線越長，電阻電容越大，訊號傳越慢。兩個要快速溝通的閘擺太遠，那條線的延遲就可能讓路徑 setup violation。在先進製程（7nm 以下），**線延遲常常比閘延遲還大**——所以 P&R 不是「擺完了事」，它直接決定時序過不過。這是為什麼 STA 要在 P&R 之後重跑（有真實線長才有真延遲），叫 post-layout timing。

```
   放得好：                     放得差：
   [A]─[B]  短線，快              [A]········[B]  長線，慢 → 可能 violation
```

**GDSII** 是最後的產出——描述每一層（每種金屬、每種摻雜區）幾何形狀的檔案，晶圓廠據此做光罩、蝕刻。到 GDSII 就是「sign-off」，交給 fab 製造。**真 P&R + GDSII 要商用工具（Cadence Innovus / Synopsys ICC2）或開源 OpenROAD，本課未實測，原理說明。**

## 核心概念：FPGA 流程——和 ASIC 差在哪

ASIC 是「訂做一顆晶片」（貴、慢、量產才划算）。**FPGA（field-programmable gate array）** 是「一顆已經做好、可反覆重新配置」的晶片——裡面滿是 **LUT（look-up table，查表，可實作任意小邏輯函式）**、**FF**、**DSP block**（乘法器）、**block RAM**，用**燒 bitstream** 決定它們怎麼連、每個 LUT 存什麼真值表。

FPGA 流程對照 ASIC：

```
   ASIC:  RTL → synth → 標準單元 netlist → place → route → GDSII → 送 fab 製造
   FPGA:  RTL → synth → 映射到 LUT/FF/DSP → place → route → bitstream → 燒進 FPGA
                          ↑ map 這步是關鍵差異
```

| 步驟 | ASIC | FPGA |
|---|---|---|
| synthesis | 映射到標準邏輯閘 | 映射到 **LUT / FF / DSP / BRAM**（FPGA 內建資源）|
| technology map | 對到製程 cell 庫 | 對到 FPGA 廠商的 LUT 架構（Xilinx/Altera 不同）|
| place | 閘擺到晶片座標 | 邏輯擺到 FPGA 上固定的 LUT/slice 格點 |
| route | 拉金屬線（可任意佈） | 用 FPGA **固定的**佈線資源（switch box）連 |
| 產出 | GDSII → 光罩 → 製造 | **bitstream**（配置檔）→ 燒進 FPGA |
| 改設計 | 重跑全流程 + 重新流片（幾個月、百萬美元）| 重跑工具 + 重燒（幾分鐘）|
| 頻率 | 高（GHz）| 低（本課這種核大概數十～上百 MHz）|
| 每顆成本 | 量產極低 | 每顆貴（但沒有一次性流片費）|

**核心差異在 map 那步**：ASIC 把邏輯做成真的閘，FPGA 把邏輯**塞進已經存在的 LUT**——一個 6 輸入 LUT 能實作任意 6 輸入布林函式，你的邏輯被切成一塊塊塞進 LUT。routing 也不同：ASIC 想怎麼拉線就怎麼拉，FPGA 只能用晶片上**預先做好的**佈線通道和 switch box，所以 FPGA 慢（多繞、經過可配置開關）。

**FPGA 的意義**：讓你不流片就能把 RTL 跑在**真硬體**上（比 verilator 快幾個數量級、能接真周邊、能跑真 Linux）。本課刻意不上板（零成本、verilator 夠學），但你手上這顆 core 的 RTL，理論上跑得起 FPGA 流程（Vivado / Quartus / 開源的 yosys+nextpnr）。**實際上板未實測，原理說明**——你已有足夠基礎自己接。

## 核心概念:面積 / 功耗 / 時脈的三角取捨

實體設計的鐵三角——三個你想要的，通常只能挑兩個往極端推：

```
              時脈（速度）
                 ╱╲
                ╱  ╲
               ╱    ╲
          面積 ──────── 功耗
```

| 你想要 | 代價 |
|---|---|
| **更高時脈** | 拆更多 pipeline 級（面積↑、latency↑）、加驅動大的閘（功耗↑）、平行結構取代序列（面積↑）|
| **更小面積** | 邏輯重用（時脈↓，序列化）、少 cache / 少功能（效能↓）|
| **更低功耗** | 降頻降壓、clock gating、關掉不用的區塊（速度↓、控制複雜度↑）|

幾個具體對照：

- **加法器**：ripple-carry（漣波進位）面積小但慢（進位一級級傳）；carry-lookahead / prefix adder 快但面積大。你 ALU 那個 `$add`，合成器選哪種取決於時序目標。
- **乘法器**：一個 32×32 乘法可以「一拍算完」（巨大組合邏輯、面積爆炸、拖長 critical path）或「多拍序列算」（面積小、但佔多個週期）。這是本課沒實作 M 擴充的一個現實理由——乘除的面積 / 時序取捨很重。
- **cache 大小**：大 cache 命中率高（效能↑）但面積、漏電功耗都大。Ch 25-27 的 cache 大小選擇，在物理層就是面積 / 功耗 / 效能的取捨。

**pipeline（Ch 13）的物理意義在這裡收口**：拆 pipeline 級 = 把長組合路徑切短 → critical path 縮短 → 時脈能拉高（吞吐↑）。代價是面積（多的 pipeline 暫存器）+ 每條指令的延遲(latency)變長 + hazard 邏輯。你在 Ch 13 為「吞吐」拆的 pipeline，在物理層就是「用面積和 latency 換時脈」的取捨。

## 對比取捨表：三種「跑 RTL」的方式

| 面向 | verilator 模擬（本課） | FPGA | ASIC |
|---|---|---|---|
| 跑的是 | RTL 行為 | 真實可配置硬體 | 真晶片 |
| 有沒有真時序 | 無（0 延遲） | 有（可跑真頻率） | 有（真製程延遲） |
| 速度 | 慢（軟體模擬） | 快（真硬體，數十 MHz） | 最快（GHz） |
| 改設計成本 | 秒級（重編） | 分鐘級（重燒 bitstream） | 月級 + 百萬美元（重流片） |
| 抓得到的問題 | 功能 bug | 功能 + 真實時序 + 周邊互動 | 全部 + 真實功耗/良率 |
| 成本 | 零（開源工具） | FPGA 板（數千～數萬） | 天價（流片費） |
| 本課用嗎 | 是 | Ch 38 講原理不上板 | 講原理，不做 |

**取捨邏輯**：功能對不對 → verilator（快、免費、可波形）；要真時序 / 接真周邊 / 跑真 OS → FPGA；量產 / 要極致效能功耗 → ASIC。本課停在 verilator 是因為它學習性價比最高——功能全在這驗，物理原理靠這章建立概念，真上板 / 流片是另一筆成本和另一組技能。

## 踩雷區

**雷 1：以為 RTL 裡一行運算對應一個閘。**
- 錯誤直覺：「`a + b` 就是一個加法器閘，`case` 就是一個選擇器」。
- 正確認識：本章真跑給你看了——本課 ALU 的 32 行 `case` 合成出 **1485 個基本閘**。一個 `a + b` 展開成一整排全加器的 AND/OR/XOR，一個 `case` 是幾百個 MUX 的網路。RTL 是行為 / 結構意圖，synthesis 把它展開成海量的閘。寫 RTL 時要對「這行底下是多少面積和延遲」有感覺。

**雷 2：以為 setup 和 hold 是同一種問題，都能靠降頻解決。**
- 錯誤直覺：「時序不過就把時脈調慢」。
- 正確認識：**setup（趕不上）**靠降頻或優化 / 拆級能解，因為它跟週期長度有關；**hold（太快）跟週期長度無關**——降頻對它毫無幫助，只能在快路徑上加 buffer 拖慢。timing closure 難就難在要**同時**滿足這兩個方向相反的約束，改一邊常拉壞另一邊。

**雷 3：以為位置 / 佈線不影響時序，netlist 對就好。**
- 錯誤直覺：「邏輯合對了，擺哪、線怎麼拉是細節」。
- 正確認識：**線也有延遲，且先進製程下線延遲常大於閘延遲**。兩個要快速溝通的閘擺太遠，那條線就可能讓路徑 setup violation。所以 STA 要在 P&R 後重跑（post-layout），synthesis 前的時序估計只是預估。placement 好壞直接決定時序過不過，不是「細節」。

**雷 4：以為 FPGA 和 ASIC 流程一樣，FPGA 就是「便宜的 ASIC」。**
- 錯誤直覺：「FPGA 跟 ASIC 一樣跑 synth/place/route，只是便宜」。
- 正確認識：關鍵差異在 **map**——ASIC 做成真閘、任意佈線；FPGA 把邏輯**塞進已存在的 LUT**、只能用**固定的**佈線資源。所以 FPGA 頻率低（繞路 + 過可配置開關）、每顆貴，但改設計是重燒 bitstream（分鐘級）不是重流片（月級）。同一份 RTL 兩邊都能跑，但落到的硬體本質不同。

**雷 5：以為時脈、面積、功耗可以同時最佳化。**
- 錯誤直覺：「好好設計就能又快又小又省電」。
- 正確認識：這是鐵三角，通常挑兩個推極端。拉時脈要拆 pipeline（面積↑ latency↑）或加大驅動（功耗↑）；縮面積要序列化（時脈↓）；省功耗要降頻降壓（速度↓）。你在 Ch 13 為吞吐拆 pipeline，物理上就是「用面積 + latency 換時脈」。設計是選擇要哪兩個角，不是全都要。

## 進階延伸

- **開源 ASIC 流程（OpenROAD / OpenLane + SkyWater 130nm）**：想真的走一遍 RTL→GDSII 而不花百萬，Google + SkyWater 開放了 130nm PDK，配 OpenLane（yosys + OpenROAD）能免費把你的 RTL 流到 GDSII、甚至透過 shuttle 真流片。你這顆 core 理論上流得動。是把本章「原理說明」段落親手做一遍的唯一免費路。
- **yosys + nextpnr 的開源 FPGA 流程**：對某些 FPGA（Lattice iCE40/ECP5），yosys（synth）+ nextpnr（place & route）是全開源工具鏈，能把 RTL 一路做到 bitstream 燒板。想上板又想全開源、看得見每一步，這是路。
- **power 分析的三種功耗**：dynamic（開關翻轉，∝ 頻率 × 電壓²）、short-circuit、leakage（漏電，先進製程越來越大且跟頻率無關）。clock gating（不用時關時脈）、power gating（關整塊區域電源）是省 dynamic / leakage 的主力手段，也是為什麼手機 SoC 大部分時間大部分電路是關著的。
- **DFT / 可測試性**：真晶片要能出廠測試（有沒有製造缺陷），得插 scan chain（把 FF 串成移位暫存器）、BIST。這是 RTL→矽 流程裡本章沒展開但量產必做的一大塊。
- **abc 與邏輯最佳化**：yosys 背後的 `abc` 是做組合邏輯最佳化的引擎（技術映射、面積 / 深度最佳化）。你上面看到的 1485 個閘，換不同最佳化目標（`abc -g` 面積 vs 速度）數字會變。想深入合成器怎麼「聰明地少用閘」，abc 是核心。

## 本章重點整理

- RTL→矽 全流程：**synthesis**（→ gate netlist）→ **placement & routing**（閘擺哪 / 線怎麼拉）→ **STA**（時序驗證）→ **GDSII**（送 fab）。模擬只驗行為，物理靠這條流程落實。
- **synthesis 把 RTL 展開成海量基本閘**——真跑證實：本課 ALU 的 32 行 `case` = **1485 個閘**（479 AND / 425 OR / 404 MUX / 133 XOR / 44 NOT）。一行運算 ≠ 一個閘。yosys 也正確把 `$signed(a)>>>` 辨識成算術右移 `$sshr`。
- **STA** 純用延遲數字窮舉所有路徑，檢查 **setup（趕不上，降頻/拆級可解）** 和 **hold（太快，只能加 buffer）**；**timing closure** 是同時滿足兩者的反覆過程。Ch 24 的 critical path 在此變成自動化的真數字。
- **P&R** 的位置決定線長、線長決定延遲；先進製程線延遲常大於閘延遲，故 STA 要在 P&R 後重跑。**GDSII** 是送 fab 的最終施工圖。
- **FPGA 流程**差在 **map**：邏輯塞進固定的 LUT、只能用固定佈線資源，產出 bitstream（重燒分鐘級）；ASIC 做真閘、任意佈線、產出 GDSII（重流片月級）。
- **面積 / 功耗 / 時脈是鐵三角**，通常只能挑兩個。pipeline（Ch 13）在物理層 = 用面積 + latency 換時脈。
- 商用 ASIC P&R / GDSII / 真 STA 數字、實際上 FPGA 板 **本課未實測**，原理說明；yosys 合成本課 ALU 的 gate 統計**皆真跑**。

## 自我檢核

- [ ] 我能按順序講出 RTL→GDSII 每一步在幹嘛（synth / place / route / STA / sign-off）。
- [ ] 我能解釋為什麼本課 ALU 一個 `case` 合成出上千個閘，並說出「一行運算 ≠ 一個閘」。
- [ ] 我能區分 setup 和 hold violation，說出各自的修法、以及為什麼降頻對 hold 沒用。
- [ ] 我能解釋為什麼 placement 影響時序（線長→延遲），以及 STA 為何要在 P&R 後重跑。
- [ ] 我能講出 FPGA 和 ASIC 流程的關鍵差異（map 到 LUT / 固定佈線 vs 真閘 / 任意佈線 / GDSII）。
- [ ] 我能用面積 / 功耗 / 時脈鐵三角解釋「為什麼拆 pipeline 能提時脈但要付面積和 latency」。

## 延伸閱讀

- **yosys 官方文件（yosyshq.readthedocs.io，Yosys Manual）**：本章真跑的合成工具。讀它的 "Synthesis Starter" 和各 pass（`proc`/`opt`/`techmap`/`abc`）說明，你能自己對本課任何模組（regfile、control unit、甚至整條 pipeline）跑合成看 gate 統計。想把「一行 RTL 變幾個閘」從本章的 ALU 推廣到你做的每個模組，這是工具手冊。
- **《Computer Architecture: A Quantitative Approach》(Hennessy & Patterson) 附錄 A / B 與第 1 章的 cost/power 段落**：把面積 / 功耗 / 時脈的取捨、Amdahl 定律、以及製程對效能的影響講清楚。本章鐵三角的量化版在這，讀它能把「挑兩個角」變成能算的權衡。
- **OpenLane / OpenROAD 文件（openlane.readthedocs.io）**：想親手走完本章「原理說明」的 ASIC 流程而不花錢——用 SkyWater 130nm 開源 PDK 把你的 RTL 流到 GDSII。讀 quickstart，跑一次你會親眼看到 floorplan / placement / CTS / routing / STA report 每個階段的真實產出，補上本章 yosys 之後未實測的那半段。
- **Harris & Harris, 《Digital Design and Computer Architecture》的 timing 與 CMOS 章節**：從電晶體 / CMOS 層講閘延遲、setup/hold 的物理來源、以及功耗公式的出處。想理解「setup/hold 為什麼存在、延遲從哪來」到電路層，這本比純架構書講得更底。
- **nextpnr GitHub（github.com/YosysHQ/nextpnr）+ 任一 iCE40 上板教學**：想真的把本課 core 跑上 FPGA 且全開源，yosys+nextpnr 是路。讀它 README 的流程（synth→pack→place→route→bitstream），對照本章 FPGA 那張表，會把「原理說明」的上板段落變成你能自己做的事。

我們把 RTL 一路推到了矽，也真跑合成看見了閘。但整條路的起點——RTL 本身——怎麼確定是對的？下一章談驗證方法學：為什麼驗證比設計還花時間、本課一路用的 verilator + 對拍屬於哪一類、以及 constrained-random / coverage / formal / cocotb 這些工業手法。

→ [Ch 39 驗證方法學：formal / riscv-formal / cocotb / UVM 速覽](./39-verification-methodology.md)
