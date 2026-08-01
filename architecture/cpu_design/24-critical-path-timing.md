# Ch 24 — 關鍵路徑、時脈與 hazard 的量化代價

> **目標**：搞懂 clock 到底能拉多快由什麼決定——**關鍵路徑（critical path）**。你會理解 setup / hold time 怎麼定死時脈週期、pipeline 為什麼能縮短關鍵路徑進而拉高 Fmax、以及「加深 pipeline 換更高 Fmax vs 更多 hazard penalty」的取捨。最後用**效能鐵律（iron law of performance）**：`time = IC × CPI × cycle_time`，把前三章的 CPI、本章的 cycle time、和指令數三者合起來，看什麼才是真正的效能。
> **環境**：WSL。本章以概念 + 量化估算為主。**沒有做真實合成（synthesis）與 STA（static timing analysis），所有 ns / gate-delay 數字皆為教學用的階數估算，明確標「理論估算，未實測」**；真實數字需 yosys + sta 或 FPGA 工具（Ch 38 講流程）。

## 為什麼需要談 critical path？

前三章我們拼命降 CPI——forwarding、分支預測，把「每指令幾 cycle」壓下來。但效能不只是 CPI。一個致命的問題被我們一直迴避：**一個 cycle 到底有多長？clock 能拉多快？**

答案不是你說了算，是**硬體裡最慢的那條組合路徑**說了算。信號從一顆 flip-flop 出發，穿過一堆邏輯閘，要在下一個 clock edge**之前**穩定地到達下一顆 flip-flop。這條「最慢的、決定 clock 上限的路徑」就是**關鍵路徑（critical path）**。clock 週期必須 ≥ 關鍵路徑的延遲，否則信號還沒到就被 latch，資料錯。

這就帶出一個殘酷的權衡：**降 CPI 的手段（更多 forwarding、更複雜的預測、更寬的 ALU）往往讓某條路徑變長，拉長 cycle time，把 clock 拖慢**。你可能贏了 CPI 卻輸了 clock，整體更慢。要判斷值不值得，必須把 CPI 和 cycle time 放進同一把尺——那把尺就是效能鐵律。這章補上效能拼圖的最後一塊。

## 先建立直覺：一節課的長度由最慢的人決定

把 pipeline 的一級想成一節課，下課鐘（clock edge）一響全班一起交卷（資料 latch 進 pipeline register）。這節課要多長？由**做得最慢的那個人**決定——只要有一個人還沒寫完，鐘就不能響，否則他交白卷（資料錯）。

```
   pipeline register        組合邏輯（一級的工作）        pipeline register
        (FF)                                                  (FF)
         │                                                     │
    Q ───┼──▶ [ gate ]─[ gate ]─[ ... ]─[ gate ]──────────────┼─▶ D
         │    └──────────── 傳播延遲 t_logic ─────────────┘    │
       clk↑                                                  clk↑
         └──────────────── clock 週期 T ──────────────────────┘

   要正確：T ≥ t_clk_to_q + t_logic + t_setup
                └FF 吐出資料的延遲┘  └邏輯延遲┘  └FF 要求資料提前穩定的時間┘
```

- **t_clk_to_q**：clock edge 後，FF 把 Q 吐出來要一點時間。
- **t_logic**：信號穿過這一級所有邏輯閘的傳播延遲——**這是設計者能控制的主角**。
- **t_setup**：下一顆 FF 要求資料在 clock edge **之前** t_setup 就穩定，不然 latch 不準。

三者之和就是這條路徑的最短週期。**所有路徑裡最長的那條 = 關鍵路徑，它定死了 clock 週期下限，也就定死了 Fmax = 1 / T_min。** 一節課的長度由最慢的人決定；一顆 CPU 的 clock 由最長的組合路徑決定。

## 核心概念一：setup / hold 與 clock 週期

flip-flop 對「資料何時穩定」有兩個要求，一頭一尾把資料鎖住：

```
             ┌── setup ──┐   ┌── hold ──┐
   data ─────┤ 必須穩定  │   │ 必須穩定 │──────
                         clk↑
                    (clock edge)

   setup 違例 (setup violation)：資料太晚到（關鍵路徑太長）
              → clock 太快，資料還沒穩就被 latch → 提高週期 / 降 clock 可修
   hold 違例 (hold violation)：資料太早變（路徑太短）
              → 前一筆還沒 latch 完資料就被下一筆蓋掉 → 加 buffer 延遲可修，跟 clock 快慢無關
```

兩種違例天差地別：

- **setup violation**：關鍵路徑跑太慢，資料趕不上 clock edge。**把 clock 放慢（週期加大）就能修**——這是 Fmax 的直接來源。你要拉高 clock，就是在跟 setup 賽跑。
- **hold violation**：某條路徑太**短**，新資料在舊資料還沒 latch 穩就衝過來把它蓋掉。這**跟 clock 快慢無關**（放慢 clock 修不了），要靠在短路徑插 buffer 拖延。hold 違例是佈局繞線階段的惡夢，但不影響 Fmax。

本章關心的是 **setup**——因為它決定 Fmax。「這顆 CPU 能跑多快」= 「關鍵路徑的 setup 什麼時候開始違例」。

**用一個具體時序預算（timing budget）把 Fmax 算出來**（**以下數字為教學用假設值，未實測**）。假設某製程下：

```
   t_clk_to_q = 0.05 ns   (FF 吐出 Q)
   t_logic    = 0.60 ns   (穿過這一級所有邏輯閘)
   t_setup    = 0.05 ns   (下一顆 FF 要求提前穩定)
   ────────────────────────
   T_min      = 0.70 ns   → Fmax = 1/0.70 ns ≈ 1.43 GHz

   若這一級的 t_logic 因為加了 forwarding mux 增到 0.68 ns：
   T_min = 0.78 ns → Fmax ≈ 1.28 GHz  ← clock 掉了約 10%
```

這個算式攤開兩件事：(1) **clock 週期是被三段延遲加總「頂」出來的下限**，不是自由參數——設 T < 0.70 ns 就 setup violation；(2) **t_logic 是唯一設計者能大幅動的一項**（t_clk_to_q / t_setup 由 FF 本身決定），所以縮短關鍵路徑 = 縮短那條最長路徑的 t_logic。加一顆 mux 讓 t_logic 從 0.60 漲到 0.68，Fmax 就掉 10%——這就是 Ch 23「forwarding 降 CPI 但拉長 T」的具體量。要用 iron law 判斷值不值得，你得能算出「T 漲多少」，這個預算就是算法。

## 核心概念二：pipeline 怎麼縮短關鍵路徑

回到 Part 1 的**單週期** CPU：一條指令在一個 cycle 內從 fetch 一路做到 write-back。那條 cycle 的關鍵路徑有多長？把整條 datapath 串起來：

```
   單週期關鍵路徑（最壞情況，如 lw）：
   PC → I-mem 讀指令 → regfile 讀 → ALU 算位址 → D-mem 讀 → 寫回 mux → regfile 寫
   └────────────────── 全部串在一個 cycle 裡 ──────────────────────┘
```

這條路徑極長——它是「所有階段的延遲相加」。單週期 CPU 的 clock 被最慢的那條指令（通常是 load，要過記憶體兩次）綁死，Fmax 很低。

**pipeline 的核心價值就在這裡**：把長路徑用 pipeline register 切成 5 段，每段只做原來的 1/5，關鍵路徑變成「最長的那一段」而非「全部相加」：

```
   pipelined 關鍵路徑 = max(每一級的延遲)
   IF: PC→I-mem        ┐
   ID: regfile 讀+decode │ 每級各自是一段獨立的組合路徑，
   EX: ALU              │ clock 只需容納「最長的一級」
   MEM: D-mem 讀        │
   WB: 寫回 mux         ┘
```

理想化地說，把一條 t 長的路徑切成 k 級，每級約 t/k，clock 週期從 t 降到約 t/k，Fmax 提升近 k 倍——**這是 pipeline 提升 throughput 的根本原因**（Ch 13 講的 throughput vs latency，現在有了時序層面的解釋）。當然不會剛好 k 倍：pipeline register 本身有 t_clk_to_q + t_setup 的固定開銷，切越多級這開銷佔比越大，收益遞減。

**估算對照（理論估算，未實測；用「閘延遲階數 gate-delay」當單位，1 閘 ≈ 若干 ps）：**

| 設計 | 關鍵路徑（哪一段） | 估算延遲（gate 階） | 相對 Fmax |
|---|---|---|---|
| 單週期 | PC→Imem→RF→ALU→Dmem→WB 全串 | ~40 階 | 1.0× |
| 5 級 pipeline | 最慢級（常是 EX 的 ALU 或 MEM 的 D-mem） | ~10 階 | ~4× |

> 上表是**教學用階數估算**，未經合成/STA。真實數字取決於製程、記憶體型別、加法器結構，需 Ch 38 的工具鏈量。這裡要抓的是**趨勢**：pipeline 把「全部相加」變「取最大」，關鍵路徑大幅縮短。

**pipeline 裡誰是關鍵路徑？** 通常是這幾個常客（Ch 9 末尾預告過）：

- **EX 級的 ALU 加法器**：32-bit 進位鏈（carry chain）很長，`a+b` 的最高位要等所有低位進位傳完。這是最經典的關鍵路徑，真設計用 carry-lookahead / carry-select 加速。
- **MEM 級的記憶體存取**：cache 讀（Part 4）延遲高。
- **forwarding 的 mux**：EX 級輸入前要過 forwarding mux 選來源（Ch 16）。**forwarding 消 stall 的代價就是給 EX 級關鍵路徑加了 mux 延遲**——降 CPI 換來 cycle time 變長的活生生例子。

## 核心概念三：deeper pipeline 的取捨

既然切 5 級能提升 Fmax，那切 10 級、20 級不是更快？現代高頻 CPU 確實這麼幹（Pentium 4 曾到 31 級，現代大核 15–20 級）。但這裡有個殘酷的反作用力——**pipeline 越深，hazard penalty 越大**。

```
   pipeline 加深的兩面：
   ┌─────────────────────────────┬──────────────────────────────┐
   │ 好處：Fmax ↑                 │ 壞處：penalty ↑ → CPI ↑        │
   │  每級更短 → cycle time 更短   │  branch mispredict 要 flush     │
   │  → clock 更快                 │  更多級（resolve 更晚）→ 每次     │
   │                              │  mispredict 賠更多 cycle         │
   │                              │  load-use 等更久 → stall 更多    │
   └─────────────────────────────┴──────────────────────────────┘
```

Ch 23 的 flush penalty 在 5 級（resolve 在 ID）是 **1 cycle**。同樣的 mispredict，在 20 級 pipeline（resolve 可能在第 12 級）是 **12 cycle penalty**。假設 mispredict rate 一樣是 5%、每 5 條指令一個 branch：

```
   flush 對 CPI 的貢獻 ≈ (branch 比例) × (mispredict rate) × (penalty)
   5 級：  0.2 × 0.05 × 1  = 0.010   （幾乎不痛）
   20 級： 0.2 × 0.05 × 12 = 0.120   （痛了 12 倍）
```

> 上式是**理論估算**（未實測），用來顯示 penalty 隨深度線性放大。這解釋了兩件事：

1. **深 pipeline 必須配更強的分支預測**：penalty 放大 12 倍，就得把 mispredict rate 從 5% 壓到 <1%（TAGE 等級）才能把 flush 貢獻拉回可接受。這就是為什麼高頻 CPU 願意砸巨大面積做預測器——不是它們錢多，是 iron law 逼的。
2. **pipeline 深度有甜蜜點**：Fmax 的收益隨深度遞減（pipeline register 開銷佔比升高），penalty 的傷害隨深度遞增。兩條曲線交會處就是最佳深度。Pentium 4 的 31 級被證明衝過頭了（NetBurst 的教訓），現代收斂在 15–20 級。

**用 iron law 算「加深值不值得」的 break-even**（**理論估算**）。把一條總延遲固定的 pipeline 從 k 級加深到 2k 級，理想上 cycle time 減半（T → 0.5T），但 mispredict penalty 大約翻倍（CPI 的 flush 那項變 2 倍）。假設原本 CPI=1.2（其中 flush 貢獻 0.1）：

```
   k 級：   time ∝ CPI × T = 1.20 × 1.0   = 1.20
   2k 級：  CPI = 1.2 - 0.1 + 0.1×2 = 1.30   (flush 那項翻倍)
            time ∝ 1.30 × 0.55 = 0.715       (T 沒剛好減半，register 開銷讓它只到 0.55)
                                             → 淨快約 40%
```

只要 T 的降幅蓋得過 CPI 的漲幅，加深就贏。但注意 flush 貢獻若原本就大（預測器爛），CPI 漲幅會吃掉 T 的收益——**這就是「深 pipeline 必須配強預測器」的量化根據**：預測器把 flush 那項壓得夠低，加深才划算。Pentium 4 的教訓正是深度衝過頭、預測跟不上，CPI 漲幅蓋過 Fmax 收益。

## 核心概念四：效能鐵律（iron law）

前面所有討論——CPI、cycle time、pipeline 深度——要用**一條式子**串起來才有意義。這就是效能鐵律：

```
   CPU time = Instruction Count × CPI × Cycle Time
              └──── IC ────┘  └CPI┘  └── T ──┘
                (指令數)    (每指令cycle) (每cycle多長)
                  ↑軟體/ISA/    ↑微架構     ↑微架構/製程
                   compiler 決定  決定        決定 (=1/Fmax)
```

三個因子**各自獨立、缺一不可**，優化任一項都要看它有沒有拖累另外兩項：

| 因子 | 誰決定 | 怎麼改善 | 陷阱 |
|---|---|---|---|
| **IC**（指令數） | ISA、compiler、演算法 | 更好的 ISA/編譯優化 | CISC 一條抵多條但每條更慢 |
| **CPI** | 微架構 | forwarding、預測、cache | 降 CPI 的邏輯可能拉長 T |
| **T**（cycle time） | 微架構、製程 | pipeline 加深、更快製程 | 加深 pipeline 抬高 CPI |

**這三者互相拉扯，才是效能設計的全部難處。** 舉三個真實的兩難：

- **加 forwarding**：CPI ↓（消 stall），但 EX 級多 mux → T ↑。淨效果通常正（Ch 23 量到 1.86× 加速），但不是白拿。
- **加深 pipeline**：T ↓（Fmax ↑），但 CPI ↑（penalty 放大）。淨效果看預測器夠不夠強。
- **換 CISC/複雜指令**：IC ↓（一條做更多），但 CPI ↑ 且 T ↑（複雜指令解碼慢）。RISC 的賭注就是「IC 高一點沒關係，換 CPI 和 T 都低」。

**用 iron law 重看 Part 2–3 的成果**（結合 Ch 23 真跑的 CPI）：

```
   假設同一段程式 IC 固定、把 cycle time 也一起考慮（T 為相對值，估算）：

   配置              CPI(實測)   T(相對,估算)   time = CPI×T (相對)
   no-fwd/static     2.600       1.00          2.600
   fwd/static        1.401       ~1.05(多mux)   ~1.471   ← CPI 大贏，T 小輸，淨大贏
   fwd/2-bit BHT     1.214       ~1.05          ~1.275   ← 再贏
```

> CPI 是 Ch 23 **真跑實測**；T 的相對值是**理論估算（未實測）**，用來示範 iron law 怎麼把兩者合起來。重點：forwarding 讓 T 略升（多了 mux），但 CPI 降幅遠大於 T 的升幅，`CPI × T` 淨降——**這才是「值不值得」的正確算法**，只看 CPI 會高估收益，只看 T 會看不到全局。

## 動手：用「邏輯層數」粗估關鍵路徑

沒有合成工具，也能對關鍵路徑做**數量級估算**——數信號從輸入到輸出要穿過幾層邏輯閘（logic levels）。每層閘有大致固定的延遲，層數越多路徑越長。這不是 STA 的精確值，但足以比較「哪一級更慢、哪個改動讓路徑變長」。

拿 Ch 9 的 ALU 當例子，估它的邏輯層數（**以下皆理論估算，未合成/未 STA**）：

```
   ALU 的關鍵路徑（最壞是加法/比較，走 32-bit 進位鏈）：
   輸入 a,b ─▶ [alu_op decode] ─▶ [32-bit 加法器進位鏈] ─▶ [結果 mux] ─▶ result
                  ~2 層              ~ log2(32)*2 ≈ 10 層        ~2 層
                                     (carry-lookahead 樹)
   估算層數 ≈ 2 + 10 + 2 ≈ 14 層閘

   若加法器用最笨的 ripple-carry（進位一位一位傳）：
   進位鏈 ≈ 32*2 = 64 層 —— 慢非常多，這就是為什麼真設計不用 ripple
```

單靠這個估算就能得到兩個真結論：

1. **加法器結構直接決定 ALU 這一級的關鍵路徑**：ripple-carry（~64 層）vs carry-lookahead（~14 層）差 4 倍以上。這印證了 Ch 9 末尾「ALU 加法慢 = clock 慢」——加法器是 EX 級關鍵路徑的核心，選對結構是拉高 Fmax 的關鍵。
2. **加一顆 forwarding mux 的代價可估**：一個 3-to-1 mux 約 2 層。把它插在 ALU 輸入前，EX 級路徑從 ~14 層變 ~16 層，約 +14%。這就是 Ch 23「forwarding 降 CPI 但 T 略升」的層數版——現在你能粗估那個「略升」大概多少。

**怎麼用工具驗證這個估算（未在本課實測，Ch 38 帶）**：`yosys` 合成成 gate netlist 後可以報 `longest path` 的邏輯深度，`OpenSTA` 給每層真實延遲（ns）算出關鍵路徑和 Fmax。我們的 WSL 環境沒裝合成器，所以本章停在層數估算；但這個「數層數」的直覺，就是 STA 工具在做的事的簡化版——你已經能在腦中對任何一段 RTL 說出「這條路徑大概幾層、哪個改動會讓它變長」。

## 對比取捨

| 決策 | 對 IC | 對 CPI | 對 T (cycle time) | 淨效果 |
|---|---|---|---|---|
| 單週期 → 5 級 pipeline | 不變 | ↑（引入 hazard） | ↓↓（路徑切短） | T 大降勝出 |
| 加 forwarding | 不變 | ↓（消 stall） | ↑（EX 多 mux） | 通常淨勝 |
| 加分支預測 | 不變 | ↓（消 flush） | ↑（IF 多查表，可平行藏） | 淨勝 |
| pipeline 5 → 20 級 | 不變 | ↑（penalty 放大） | ↓（每級更短） | 看預測器強度 |
| 更快製程 | 不變 | 不變 | ↓ | 純贏（但貴） |
| RISC → CISC | ↓ | ↑ | ↑ | 歷史證明 RISC 賭贏 |

## 踩雷區

**雷 1：以為 clock 想拉多快就多快。**
- 錯誤直覺：「clock 是設計者設的參數，設高一點就快」。
- 正確認識：clock 週期有**硬性下限** = 關鍵路徑延遲（t_clk_to_q + t_logic + t_setup）。設超過就 **setup violation**，資料還沒穩就被 latch，功能直接錯。Fmax 不是你想要的數字，是硬體算出來的極限。要更高 clock，得先縮短關鍵路徑（切 pipeline、換更快加法器），不是調參數。

**雷 2：只看 CPI 判斷微架構好壞。**
- 錯誤直覺：「這設計 CPI 更低，一定更快」。
- 正確認識：`time = IC × CPI × T`，CPI 只是三分之一。一個 CPI 更低但把關鍵路徑拖長（T ↑）的設計可能整體更慢。Pentium 4 vs Pentium III 就是活例：P4 深 pipeline 讓 CPI 變差、但 clock 拉超高，某些 workload 反而更快、某些更慢——**只有 `CPI × T`（甚至 × IC）才是真效能**。前三章拼命講 CPI，本章就是來補這個平衡的。

**雷 3：以為 pipeline 越深越好。**
- 錯誤直覺：「切越多級 Fmax 越高，效能越好」。
- 正確認識：加深有兩個反作用力——(a) pipeline register 的 t_clk_to_q + t_setup 是固定開銷，切越細它佔每級比例越大，Fmax 收益遞減；(b) hazard penalty 隨深度**線性放大**（20 級的 mispredict 賠 12 cycle 而非 1）。Pentium 4 的 31 級被證明過頭。深度有甜蜜點，不是單調變好。

**雷 4：把 hold violation 當成 clock 太快。**
- 錯誤直覺：「timing 過不了就是 clock 太快，放慢就好」。
- 正確認識：**setup** violation 才是 clock 太快（放慢可修）。**hold** violation 是某條路徑太**短**（新資料太早蓋掉舊的），**跟 clock 快慢無關，放慢 clock 修不了**，要在短路徑插 buffer 延遲。兩種違例混為一談會讓你朝錯方向修。Fmax 只跟 setup 有關；hold 是另一回事（佈局階段處理）。

## 進階延伸

- **STA（static timing analysis）是真實量關鍵路徑的方法**：本章的 gate 階數是估算，真設計用 STA 工具（商用 PrimeTime、開源 OpenSTA）在合成後的 netlist 上，把每條 path 的延遲精算出來，報告哪條是關鍵路徑、slack（餘裕）多少。它不模擬（不需 testbench），而是靜態分析所有路徑。Ch 38 會帶 yosys + OpenSTA 的流程，那時你能對本課的 core 真的量出 Fmax。
- **clock skew、jitter、PVT corner**：本章假設 clock 同時到所有 FF，真實 clock 分佈網路有 **skew**（到達時間差）和 **jitter**（週期抖動），會吃掉 timing 餘裕。而且延遲隨 **PVT**（process/voltage/temperature，製程角、電壓、溫度）變化，STA 要在最壞 corner（慢製程、低壓、高溫）驗 setup、最快 corner 驗 hold。這是「為什麼標稱 Fmax 要留 margin」的原因。
- **retiming 與 pipeline 平衡**：如果某一級特別慢（拖累整體 Fmax），合成器可以做 **retiming**——把邏輯跨 pipeline register 挪動，讓各級延遲更平均。理想 pipeline 每級延遲相等（balanced），這樣關鍵路徑 = 總延遲/級數。不平衡的 pipeline 有一級是瓶頸，其他級的餘裕浪費掉。手切 pipeline 時要盡量讓各級工作量均等。
- **超頻與 DVFS 是在玩 Fmax 邊界**：超頻（overclock）就是把 clock 推過標稱 Fmax，賭在你這顆晶片（製程角比最壞好）+ 這個溫度下 setup 還沒違例——推過頭就 setup violation，計算出錯（藍屏/當機）。反過來 **DVFS**（動態調頻調壓）省電時降頻降壓，因為低壓讓閘變慢、關鍵路徑變長，Fmax 跟著降。這些都是本章 setup/critical-path 原理在系統層的直接應用。

## 本章重點整理

- **關鍵路徑（critical path）** = 所有組合路徑中最長的那條，它定死 clock 週期下限：`T ≥ t_clk_to_q + t_logic + t_setup`，Fmax = 1/T_min。
- **setup violation** = clock 太快（放慢可修）→ 決定 Fmax；**hold violation** = 路徑太短（跟 clock 無關，插 buffer 修）。兩者別搞混。
- **pipeline 把「全部路徑相加」切成「取最大一段」**，關鍵路徑大幅縮短、Fmax 近似提升 k 倍（有 register 開銷，收益遞減）。ALU 加法器、記憶體、forwarding mux 是關鍵路徑常客。
- **deeper pipeline**：Fmax ↑ 但 hazard penalty **線性放大**（20 級的 mispredict 賠 ~12 cycle 而非 1），CPI ↑。深度有甜蜜點，需配更強預測器。
- **效能鐵律**：`time = IC × CPI × cycle_time`。三因子互相拉扯，降 CPI 的邏輯常拉長 T——**只有 `IC × CPI × T` 才是真效能**，只看 CPI 會誤判。
- 本章 ns/階數為理論估算（未合成/STA）；真數字需 Ch 38 的 yosys + STA。

## 自我檢核

- [ ] 我能寫出 `T ≥ t_clk_to_q + t_logic + t_setup`，並解釋每一項是什麼、哪一項是設計者主要能控的。
- [ ] 我能區分 setup violation 和 hold violation，說出哪個跟 clock 快慢有關、各怎麼修。
- [ ] 我能解釋 pipeline 為什麼把關鍵路徑從「相加」變成「取最大」，以及為什麼收益不是剛好 k 倍。
- [ ] 我能算出同樣 mispredict rate 下，5 級 vs 20 級 pipeline 的 flush CPI 貢獻差幾倍，並說明深 pipeline 為何要更強預測器。
- [ ] 我能寫出 iron law 三因子，並舉一個「降 CPI 卻拉長 T」的例子（如 forwarding mux）。
- [ ] 我能用 iron law 解釋為什麼「CPI 更低的設計不一定更快」。

## 延伸閱讀

- **《Digital Design and Computer Architecture, RISC-V Edition》(Harris & Harris) 第 3.5 節「Timing of Sequential Logic」與第 7.5 節 pipelined processor 的 timing**：3.5 節把 setup/hold/clock skew/critical path 從電路層講透（本章時序原理的完整版），7.5 節把它套到 RISC-V pipeline 上算各級延遲。想把本章的估算變成能算的公式，讀這兩節。
- **《Computer Organization and Design, RISC-V Edition》(Patterson & Hennessy) 第 1.6 節「Performance」的 iron law 與第 4.6 節 pipelined datapath timing**：1.6 節是 `time = IC × CPI × T` 的權威出處（課本原文與例題），第 4.6 節討論 pipeline 各級延遲不均的問題。本章 iron law 那節的母體。
- **《Computer Architecture: A Quantitative Approach》(Hennessy & Patterson) 第 1.8–1.9 節與 附錄 C 的 pipeline 深度取捨**：從量化角度討論 pipeline 深度、Fmax、CPI 的三方權衡，含 Pentium 4 深 pipeline 的實測教訓。本章「deeper pipeline 甜蜜點」的資料依據。
- **Agner Fog, "The microarchitecture of Intel, AMD and VIA CPUs" 的 pipeline 與 branch misprediction penalty 章節**：從實測逆向給出真實 CPU 各代的 pipeline 深度、mispredict penalty（cycle 數）、以及它們對 code 的影響。把本章「penalty 隨深度放大」的估算對回真硬體數字——你會看到現代大核 mispredict 真的賠十幾 cycle。
- **OpenSTA / yosys 文件（配合 Ch 38）**：想把本章的估算變成**真數字**，這是工具。yosys 合成出 gate-level netlist，OpenSTA 做靜態時序分析報出關鍵路徑和 slack。Ch 38 會帶流程，屆時可對本課的 core 真的量 Fmax，驗證本章的趨勢估算。

Part 3 的理論到此完整：預測（Ch 21–22）降 flush、CPI 分析（Ch 23）量收益、關鍵路徑（本章）管 clock，三者用 iron law 收束。接下來的練習 C，你要把 Ch 21 的 BHT + BTB（或 Ch 22 的 gshare）真的接進 pipeline，加上 performance counter，親手量出 CPI 改善——把這一整個 Part 從「讀懂」變成「做出來且量得出來」。

→ [練習 C：實作 BHT + BTB，量測 CPI 改善](./practice-c-branch-predictor.md)
