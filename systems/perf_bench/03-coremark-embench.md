# Ch 3 — Coremark / Embench：RISC-V 與嵌入式主力

> **目標**：理解 Coremark 和 Embench 兩個嵌入式/RISC-V 常用 benchmark 的結構、差異、跑法（這章能實際動手跑）、以及 Coremark 的爭議（為什麼它被批評、Embench 為何誕生）。這兩個在 RISC-V 社群比 SPEC 更常被引用——它們免費、輕量、適合嵌入式，是嵌入式效能討論的共同語言。

> **環境**：Linux，gcc（可跑在 x86 或 RISC-V/QEMU）。Coremark 是開源的（Ch 0 已 clone）。

## 為什麼嵌入式用 Coremark/Embench 而非 SPEC？

SPEC CPU（Ch 2）是業界標準，但它**太重**——一份 license 幾千美元、跑一次要幾小時、需要不少記憶體。對嵌入式（微控制器、小型 SoC）和 RISC-V 社群，這不實際——嵌入式裝置可能只有幾 KB 記憶體，跑不動 SPEC；RISC-V 社群是開源的，不會買付費 license。

所以嵌入式/RISC-V 用 **Coremark**（最常被引用）和 **Embench**（更現代）——它們**免費、開源、輕量**（能在小裝置上跑）、聚焦嵌入式 workload。當你看到「這個 MCU 的 Coremark 分數是 X」「這個 RISC-V core 的 Coremark/MHz 是 Y」，那就是嵌入式效能的共同語言。這章講它們的結構和跑法（能實際動手），以及 Coremark 的爭議（為什麼有 Embench）。

## 先建立直覺:輕量的嵌入式 benchmark

```
Coremark / Embench 的定位（vs SPEC）：

  SPEC（Ch 2）：重、付費、跑幾小時、要不少記憶體
    → 評估桌面/伺服器 CPU
        │
  Coremark：輕、免費、跑幾秒、極小記憶體
    單一程式，含：list 處理、矩陣運算、state machine、CRC
    → 評估嵌入式 MCU、簡單的 core
    指標：Coremark 分數、Coremark/MHz（每 MHz 的分數，比較核心效率）
        │
  Embench：輕、免費、更現代、一籃子小程式
    多個真實的嵌入式程式（不是單一合成程式）
    → 改善 Coremark 的缺點（更代表真實嵌入式 workload）
        │
  → 嵌入式/RISC-V 用 Coremark（最常引用）和 Embench（更現代）
    輕量、免費、適合小裝置
    Coremark/MHz 是比較「核心微架構效率」的常見指標
```

關鍵心智：Coremark/Embench 是**輕量、免費、適合嵌入式**的 benchmark（vs SPEC 的重、付費）。**Coremark** 是單一合成程式（list/矩陣/state machine/CRC），最常被引用，指標是 **Coremark/MHz**（每 MHz 的分數，比較核心效率）。**Embench** 是更現代的一籃子小程式（改善 Coremark 的缺點）。它們是嵌入式效能的共同語言。

> Coremark/Embench 是 Ch 1 的 benchmark（介於 micro 和 macro 之間——比 micro 完整，比 SPEC 輕）。分數計算也用 geomean（Ch 4）。

## 跑 Coremark

```bash
cd ~/perflab/coremark    # Ch 0 clone 的

# === 編譯並跑 Coremark ===
# Coremark 在不同平台有不同的 port（porting layer）
# Linux 用 linux64 port
make PORT_DIR=linux64 ITERATIONS=100000
# 或直接 make（用預設 port）
make

# 跑（會印出 Coremark 分數）
./coremark.exe 0x0 0x0 0x66 0 7 1 2000 > run.log
cat run.log
# CoreMark 1.0 : 12345.67 / GCC ... / Heap
#   12345.67 = Coremark 分數（每秒迭代數的相關值）
# Total ticks, Total time, Iterations 等

# === Coremark/MHz（比較核心效率的關鍵指標）===
# Coremark 分數 / CPU 頻率(MHz) = 每 MHz 的 Coremark
# 例：12345 分 @ 3000 MHz → 4.1 Coremark/MHz
# → 這個比較「核心微架構的效率」（不受頻率影響）
#   RISC-V core 常報 Coremark/MHz 來比較設計（如 3.5 vs 4.5）
echo "Coremark/MHz = 分數 / 頻率MHz"
```

```
Coremark 的組成（單一程式的幾個部分）：

  1. Linked list 處理：指標追逐、排序（測記憶體存取、分支）
  2. 矩陣運算：乘法、加法（測整數運算）
  3. State machine：解析輸入（測分支、控制流）
  4. CRC：循環冗餘檢查（測位元運算）
        │
  → 涵蓋嵌入式常見的運算類型（list/矩陣/狀態機/位元）
    單一程式、極小、可重現
    設計目標：代表「嵌入式 CPU 的一般運算能力」
```

> **Coremark/MHz（每 MHz 的分數）是比較「核心微架構效率」的關鍵指標——它排除頻率，純比設計**。Coremark 是單一合成程式，含嵌入式常見的運算：linked list 處理（指標追逐、排序——測記憶體存取和分支）、矩陣運算（測整數運算）、state machine（測分支控制流）、CRC（測位元運算）。跑它得到 **Coremark 分數**。但最重要的指標是 **Coremark/MHz**——分數除以 CPU 頻率，得到「每 MHz 的 Coremark」。為什麼這個重要？因為它**排除了頻率的影響，純比較核心的微架構效率**。一個 5GHz 的 CPU 和 1GHz 的 CPU，Coremark 分數當然前者高（頻率高），但這不代表前者的「設計」更好——可能只是頻率高。**Coremark/MHz** 讓你比較「同樣頻率下，哪個核心設計更有效率」——這對 CPU 設計和 RISC-V core 的比較很關鍵（RISC-V core 常報 Coremark/MHz，如「我們的 core 是 4.5 Coremark/MHz，比競品的 3.5 好」——這是純設計效率的比較，不靠拉高頻率）。對 compiler 工作也相關——compiler 優化能提升 Coremark（同頻率下跑更快），這也反映在 Coremark/MHz。理解這個指標，你能參與「核心效率」的討論（Coremark/MHz 是 SiFive 等 RISC-V 廠商常用的）。能實際跑 Coremark（開源、輕量），這章建議動手跑一次，體會嵌入式 benchmark。

## Coremark 的爭議

```
Coremark 為什麼被批評（理解 Embench 為何誕生）：

  1. 太小、太合成：
     單一小程式，不代表真實的嵌入式 workload 多樣性
        │
  2. 容易被 compiler「特殊優化」（gaming）：
     Coremark 的某些迴圈有已知的 idiom
     compiler 可以針對性優化（甚至有人質疑「為 Coremark 調」）
     → 高 Coremark 不一定代表真實效能好
        │
  3. 沒有記憶體/cache 壓力：
     workload 小，幾乎全在 cache 裡
     → 不反映真實程式的記憶體行為
        │
  4. 單一分數，不夠細：
     一個數字，看不出「在不同 workload 的表現」
        │
  → Embench 為了改善這些而誕生：
    一籃子真實的嵌入式程式（多樣）
    更難被單一 idiom gaming
    更代表真實嵌入式 workload
        │
  → Coremark 仍廣泛使用（歷史、簡單、可比），但要知道它的限制
    嚴謹的嵌入式效能評估該用 Embench（更現代）
```

> **Coremark 被批評「太小、易被 compiler gaming、沒記憶體壓力」——Embench 為改善這些而生**。Coremark 雖然廣泛使用，但有真實的缺點：(1) **太小太合成**——單一小程式，不代表真實嵌入式 workload 的多樣性；(2) **容易被 compiler gaming**——Coremark 的某些迴圈有已知的 idiom（pattern），compiler 可以針對性優化（業界甚至有「為 Coremark 調 compiler」的質疑），所以**高 Coremark 不一定代表真實效能好**（同 Ch 2 的 gaming 問題）；(3) **沒有記憶體/cache 壓力**——workload 小，幾乎全在 cache 裡，不反映真實程式的記憶體行為（真實嵌入式程式可能有 cache miss）；(4) **單一分數不夠細**——一個數字看不出不同 workload 的表現。**Embench** 為改善這些而誕生（由 RISC-V 社群和學界推動）——它是**一籃子真實的嵌入式程式**（多樣，不是單一合成程式），更難被單一 idiom gaming，更代表真實嵌入式 workload。**現狀**：Coremark 仍廣泛使用（歷史悠久、簡單、有大量可比的歷史數據），但**嚴謹的嵌入式效能評估該用 Embench**（更現代、更代表真實）。理解這個——當你看到 Coremark 分數，知道它的限制（可能被 gaming、不反映記憶體行為）；當你要做嚴謹評估，考慮 Embench（或多個 benchmark）。這呼應 Ch 2 的核心教訓——**任何單一 benchmark 都有限制，要理解它測什麼、不測什麼、能不能被 gaming**。沒有完美的 benchmark，理解每個的限制才能正確使用。

## Embench:更現代的選擇

```bash
# Embench（更現代的嵌入式 benchmark 套件）
cd ~/perflab
git clone https://github.com/embench/embench-iot
cd embench-iot

# Embench 是一籃子真實的嵌入式程式
ls src/
# aha-mont64  crc32  cubic  edn  huffbench  matmult-int  
# minver  nbody  nettle-aes  nsichneu  picojpeg  qrduino  
# sglib-combined  slre  st  statemate  ud  wikisort
# → 18 個真實的嵌入式程式（壓縮/加密/JPEG/QR碼/排序...）
#   比 Coremark 的單一程式多樣得多

# 跑 Embench（需要設定 toolchain，這裡概念示範）
# python3 build_all.py --arch native --chip default --board default
# python3 benchmark_speed.py --target-module run_native
# → 對每個子 benchmark 跑，算 geomean（Ch 4）

# Embench 的指標：相對於參考的 geomean（類似 SPEC 的方法）
# → 一籃子程式的綜合分數，比 Coremark 單一分數更代表真實
```

> **Embench 是「一籃子真實嵌入式程式」（18 個：壓縮/加密/JPEG/QR/排序…），比 Coremark 單一合成程式更代表真實 workload**。Embench 改善了 Coremark 的缺點——它是**一籃子真實的嵌入式程式**（18 個子 benchmark：CRC、加密 AES、JPEG 解碼、QR 碼、排序 wikisort、物理模擬 nbody…），每個是真實領域的程式。這比 Coremark 的單一合成程式**多樣得多**——更難被單一 idiom gaming（要 game 18 個不同的程式很難）、更代表真實嵌入式 workload 的多樣性。Embench 的指標是「相對於參考的 geomean」（類似 SPEC 的方法，Ch 4）——一籃子程式的綜合分數。它由 RISC-V 社群和 Embecosm 等推動，是嵌入式 benchmark 的現代選擇。**選擇**：Coremark（歷史、簡單、大量可比數據、快速比較）vs Embench（現代、多樣、更代表真實、更嚴謹）。對 RISC-V/嵌入式效能工作，理解兩者——Coremark 是「行業慣例的快速指標」，Embench 是「嚴謹評估的現代選擇」。實務上常兩者都報（Coremark 給快速比較和歷史對照、Embench 給嚴謹的多樣 workload 評估）。這也展示了 benchmark 的演進——從單一合成（Coremark）到一籃子真實程式（Embench/SPEC），追求更好的「代表真實效能」。理解這個演進和各 benchmark 的取捨，你能在嵌入式效能工作中選對工具、正確解讀數字。

## 故意弄壞:compiler flag 對 Coremark 的影響

```bash
cd ~/perflab/coremark
# 展示「compiler flag 對 benchmark 分數的巨大影響」（Ch 2 的誤用之一）

# 用不同 flag 編譯 Coremark，比較分數
for flags in "-O0" "-O2" "-O3" "-O3 -march=native"; do
    echo "=== flags: $flags ==="
    make clean > /dev/null 2>&1
    make PORT_DIR=linux64 XCFLAGS="$flags" > /dev/null 2>&1
    ./coremark.exe 0x0 0x0 0x66 0 7 1 2000 2>/dev/null | grep CoreMark
done
# -O0:                CoreMark 1.0 : 3000   ← 不優化，慢
# -O2:                CoreMark 1.0 : 11000  ← 優化，快很多
# -O3:                CoreMark 1.0 : 11500  ← 比 O2 略快（或差不多）
# -O3 -march=native:  CoreMark 1.0 : 12000  ← 用本機指令集，再快一點
# → 同一個 Coremark，不同 flag 分數差 4 倍！
#   這證明 Ch 2 的誤用：「比較 benchmark 分數要用相同的 flag」
#   「CPU A 的 Coremark 比 B 高」可能只是 A 用了更好的 flag/compiler

# 重點：報 Coremark 分數一定要說 compiler 和 flag
#   否則數字無法比較（compiler/flag 的影響可能比 CPU 設計還大）
```

> **同一個 Coremark，不同 compiler flag 分數差 4 倍——這證明「比較 benchmark 要用相同的 compiler/flag」**。這個實驗展示 Ch 2 的核心教訓在 Coremark 上的體現——**compiler flag 對 benchmark 分數的影響巨大**。同一個 Coremark 程式，`-O0`（不優化）和 `-O3 -march=native`（優化+本機指令集）分數差**4 倍**！這意味著：**「CPU A 的 Coremark 比 CPU B 高」可能只是 A 用了更好的 compiler 或 flag**，不是 A 的設計更好。所以報 Coremark（或任何 benchmark）分數，**一定要說清楚 compiler 版本和 flag**——否則數字無法比較（compiler/flag 的影響可能比 CPU 設計差異還大）。這是 perf_bench 反覆強調的紀律——**benchmark 數字要在「完全相同的條件」下才能比較**（Ch 2 的誤用）。對 RISC-V/嵌入式效能工作，這特別重要——比較不同 RISC-V core 的 Coremark/MHz 時，要確保用**相同的 compiler 和 flag**（否則比的是 compiler 不是 core）。這也是為什麼正式的 benchmark 報告（SPEC、Embench）都嚴格要求揭露 compiler/flag——透明度讓比較公平。對 compiler 工作，這個實驗也展示了 compiler 優化的價值（-O0 到 -O2 提升 3.6 倍）——這正是 compiler 團隊的貢獻。但要警惕 gaming（針對 benchmark 的特殊優化）。理解 compiler/flag 對 benchmark 的巨大影響，你做和解讀 benchmark 時就會謹慎地控制和揭露這些條件。

## 動手練習

1. 跑 Coremark：編譯並跑 Coremark，得到分數，算 Coremark/MHz（分數/你的頻率）

2. flag 影響：用不同 flag（-O0/-O2/-O3/-march=native）編譯 Coremark，比較分數（差幾倍）

3. 看 Coremark 組成：讀 Coremark 的原始碼，找出 list/矩陣/state machine/CRC 那幾部分

4. 跑 Embench（選做）：設定 Embench，跑幾個子 benchmark，對比 Coremark 的單一程式

5. 思考爭議：理解 Coremark 為什麼被批評、Embench 怎麼改善（多樣 vs 單一、gaming）

## 本章重點整理

- Coremark/Embench 是嵌入式/RISC-V 的主力 benchmark（vs SPEC）：免費、輕量、適合小裝置
- Coremark 是單一合成程式（list/矩陣/state machine/CRC）；Coremark/MHz 是比較核心微架構效率的關鍵指標（排除頻率）
- Coremark 被批評：太小太合成、易被 compiler gaming、沒記憶體壓力——Embench 為改善這些而生
- Embench 是一籃子真實嵌入式程式（18 個），更多樣、更難 gaming、更代表真實
- compiler flag 對 benchmark 分數影響巨大（差 4 倍）——比較一定要用相同 compiler/flag

## 自我檢核

- [ ] 知道為什麼嵌入式用 Coremark/Embench 而非 SPEC
- [ ] 能跑 Coremark，理解 Coremark/MHz 的意義（核心效率，排除頻率）
- [ ] 知道 Coremark 的爭議（gaming、太小、沒記憶體壓力）
- [ ] 知道 Embench 怎麼改善 Coremark（一籃子真實程式）
- [ ] 理解 compiler/flag 對 benchmark 分數的巨大影響

## 延伸閱讀

### 官方

- **[Coremark](https://www.eembc.org/coremark/)** + **[Embench](https://www.embench.org/)** — EEMBC / Embench
  - **讀哪裡**：Coremark 的 run rules、Embench 的設計理念
  - **為什麼值得讀**：兩個 benchmark 的權威；Embench 的網站解釋了為什麼要改善 Coremark

### 文章

- **[Coremark 的問題](https://www.sifive.com/blog/) / 各種 Coremark 批評文章**
  - **這篇說什麼**：Coremark 為什麼不夠好、Embench 的動機
  - **為什麼值得讀**：理解 benchmark 演進的脈絡

### 程式碼

- **[Coremark GitHub](https://github.com/eembc/coremark)** + **[Embench-IOT](https://github.com/embench/embench-iot)**
  - **為什麼值得讀**：實際的 benchmark 程式碼，研究它們的結構

下一章是 Part 1 的最後——統計基本功。為什麼用幾何平均、怎麼算信賴區間、怎麼判斷「差異是否顯著」。這是讓你的 benchmark 數字「可信」的基礎。

→ [Ch 4 統計基本功：geomean、CI、noise 控制](./04-statistical-basics.md)
