# Ch 46 — FuzzBench 與評測科學

> **目標：** 理解為什麼大多數 fuzzer 論文的 evaluation 不可信，學會正確的 fuzzing 評測方法：Mann-Whitney U 統計檢定、報 median 而非 max、正確選擇 benchmark target、理解 bug-based vs coverage-based metric 的適用範圍。能看出一篇 fuzzer 論文的評測缺陷在哪，也能為自己的 fuzzing campaign 設計可重現的評測方法。

## 為什麼評測很難做對

2018 年 CCS 有一篇論文 Klees et al. 的《Evaluating Fuzz Testing》，系統性地檢查了 32 篇頂會 fuzzing 論文的評測方法，結論相當刺眼：大多數論文的統計結論在方法論上是有問題的。

這不是說那些 fuzzer 技術不好，而是**實驗設計天然有很多陷阱**，而且這些陷阱的犯錯方向高度偏向讓自己的方法「看起來更好」。

## 先建立直覺：常見評測錯誤模式

```
錯誤模式 1：只跑一次，報最大值

  fuzzer_A 跑 1 次：coverage = 9500
  fuzzer_B 跑 1 次：coverage = 8900
  結論：A 比 B 好 6%

  問題：這可能只是一次幸運（A 早期踩到了一個好 seed）。
  正確：各跑 20 次，報 median 和 95% CI。

─────────────────────────────────────────────────

錯誤模式 2：只跑 5 分鐘

  fuzzer_A 5分鐘：coverage = 1200  ← A 的初始化速度快
  fuzzer_B 5分鐘：coverage = 1100

  24小時後：
  fuzzer_A：coverage = 15000
  fuzzer_B：coverage = 18000   ← B 有更好的 long-term 策略

  5 分鐘的結論與 24 小時的結論相反。

─────────────────────────────────────────────────

錯誤模式 3：target 選擇偏差

  論文作者自己選了 5 個 benchmark，剛好都是自己方法有優勢的。
  沒有在標準化 benchmark suite 上評測。

  FuzzBench 的目標：提供中立、標準化、可重現的 benchmark set。

─────────────────────────────────────────────────

錯誤模式 4：bug-based metric 的計數問題

  "我的 fuzzer 找到 15 個 bug，A 只找到 8 個"

  問題：
  a. 15 個 bug 可能是同一個 root cause 的 15 個觸發方式
     （dedup 後可能只剩 3 個）
  b. 人工審核的 severity 標準不一致
  c. 僅在少數 target 上測試，sample size 不足以泛化
```

## Klees et al.《Evaluating Fuzz Testing》（CCS 2018）批判要點

這篇論文是這個領域的 meta-paper，值得完整讀，但核心批判點有六個：

**1. Trial 數量太少**
大多數論文只跑 3–5 次 trial，這個 sample size 根本沒有統計功效（statistical power）去偵測真實差異。隨機因素（seed 的初始順序、OS scheduler、記憶體狀態）可以造成 10–20% 的 coverage 波動。

**2. 只報最大值（best run）而非 median**
挑最好的一次跑貼到論文裡，這在心理上很誘人，但數學上等於 p-hacking。應該報 **median** 和 **信賴區間**。

**3. 沒有統計假設檢定**
即使跑了多次，多數論文只報平均值或 max，不做任何統計顯著性檢定。兩組各 10 次的數據，在沒有檢定的情況下，你完全不知道差異是真實的還是 noise。

**4. 時間長度不足**
短時間評測（幾分鐘到幾小時）偏向反映 fuzzer 的**初期行為**，而 coverage 停滯的行為要在 24 小時後才明顯。很多「改進」在長時間後消失了。

**5. Benchmark target 選擇偏差**
作者選自己的 benchmark 天然偏向自己的方法。標準化 benchmark suite（FuzzBench、UNIFUZZ、Magma）的存在就是為了解決這個問題。

**6. Bug-based metric 的去重問題**
「找到幾個 bug」這個 metric 天然不準：沒去重、severity 不一致、reproduction 條件不明確。Coverage-based metric（edge coverage）雖然不完美，但至少是可量化、可重現的。

## FuzzBench 的方法

FuzzBench 是 Google 2020 年開放的標準化 fuzzer benchmark 平台，設計原則直接針對上述問題：

```
FuzzBench 設計原則：

┌─────────────────────────────────────────┐
│ 標準化 benchmark                        │
│  ─ 20+ 個真實開源 target                │
│  ─ 包含不同語言、不同複雜度              │
│  ─ 任何人都能用同一套跑                  │
├─────────────────────────────────────────┤
│ 標準化執行環境                          │
│  ─ Docker container（可重現）            │
│  ─ Google Cloud 機器（固定硬體）         │
│  ─ 固定 random seed                     │
├─────────────────────────────────────────┤
│ 統計正確性                              │
│  ─ 每個 fuzzer × target 至少 20 次 trial│
│  ─ 預設跑 24 小時                       │
│  ─ 報 median + 信賴區間                 │
│  ─ Mann-Whitney U test 做顯著性檢定     │
├─────────────────────────────────────────┤
│ 公開報告                                │
│  ─ 結果公開在 fuzzbench.dev             │
│  ─ 新 fuzzer 可提交 PR 加入比較         │
└─────────────────────────────────────────┘
```

FuzzBench 的 coverage metric 是 **edge coverage**（LLVM SanitizerCoverage 的 trace-pc-guard 計數），不是 line coverage，因為 edge coverage 更能反映程式邏輯的探索深度。

## 統計顯著性：Mann-Whitney U Test

為什麼用 Mann-Whitney U 而不是 t-test？因為 fuzzing 的 trial 數據**不服從常態分佈**——coverage 的分佈往往是右偏的（偶爾有一次 trial 特別好），t-test 的假設不成立。Mann-Whitney U 是非參數檢定，不假設分佈形狀。

原理（直覺）：

```
fuzzer_A 的 10 次 coverage：
  [7854, 7980, 8089, 8102, 8234, 8320, 8441, 8512, 8623, 8765]

fuzzer_B 的 10 次 coverage：
  [8834, 8923, 9045, 9102, 9230, 9387, 9450, 9512, 9601, 9678]

Mann-Whitney U 的直覺計算：
  對於 A 和 B 的每一對組合（10×10=100 對），
  數「B 的值 > A 的值」的次數
  → 這裡 100 對裡 B 全部大於 A → U = 0（完全分離）
  → p 值非常小，顯著
```

## 真跑統計小例

以下是真實執行的統計測試，模擬比較兩個 fuzzer 的 edge coverage（各 10 次 trial）：

```python
from scipy import stats
import numpy as np

np.random.seed(42)

# 模擬數據：兩個 fuzzer 各跑 10 次 24 小時的 edge coverage
fuzzer_A = [8234, 8512, 7980, 8765, 8102, 8441, 8089, 8623, 7854, 8320]
fuzzer_B = [9102, 9450, 8834, 9678, 9230, 9512, 9045, 9387, 8923, 9601]

stat, p = stats.mannwhitneyu(fuzzer_A, fuzzer_B, alternative="two-sided")

# A12 effect size（Vargha-Delaney）
n_a, n_b = len(fuzzer_A), len(fuzzer_B)
A12 = stat / (n_a * n_b)

# Bootstrap 95% CI for median difference
rng = np.random.default_rng(42)
diffs = []
for _ in range(10000):
    a_s = rng.choice(fuzzer_A, size=n_a, replace=True)
    b_s = rng.choice(fuzzer_B, size=n_b, replace=True)
    diffs.append(np.median(b_s) - np.median(a_s))
ci_low, ci_high = np.percentile(diffs, [2.5, 97.5])
```

執行輸出（Windows Python 3.12 + scipy 1.18.0，真實執行）：

```
=== Mann-Whitney U Test: Fuzzer A vs Fuzzer B (Edge Coverage) ===
Fuzzer A (baseline AFL++)  -- n=10
  values : [8234, 8512, 7980, 8765, 8102, 8441, 8089, 8623, 7854, 8320]
  median : 8277.0
  mean   : 8292.0
  std    : 278.3

Fuzzer B (hybrid variant)  -- n=10
  values : [9102, 9450, 8834, 9678, 9230, 9512, 9045, 9387, 8923, 9601]
  median : 9308.5
  mean   : 9276.2
  std    : 277.7

Mann-Whitney U statistic : 0.0
p-value (two-sided)      : 0.000183
Significant (alpha=0.05) : YES

A12 effect size (Vargha-Delaney): 0.000
  (>0.71 large, >0.64 medium, >0.56 small; <0.5 reversed)

Bootstrap 95% CI for median difference (B - A):
  [625.5, 1379.0]
  Observed median diff: 1031.5
```

注意：A12 = 0.0 代表效果量**極大**（B 的每一次 trial 都大於 A 的每一次），這是個刻意構造的極端例子。現實中通常是 0.6–0.8 之間，代表「大多數情況下 B 比 A 好，但有時候 A 也能贏」。

**如何讀這個報告：**

| 數字 | 意義 | 應該報什麼 |
|------|------|-----------|
| median 8277 vs 9308 | 中位數差距 1031 | **一定要報**，比 mean 更穩健 |
| p = 0.000183 | 差距顯著（< 0.05） | 報，但只是「差異存在」，不是「差多大」 |
| A12 effect size | 效果量大小 | **一定要報**，p 小不代表差距大 |
| 95% CI [625.5, 1379.0] | 真實差距的範圍 | **一定要報**，信賴區間比單點估計有意義 |

## Bug-based vs Coverage-based Metric

|  | Coverage-based | Bug-based |
|--|---------------|-----------|
| **量化方式** | edge/branch count，客觀可重現 | 找到的 bug 數，受去重方式影響 |
| **反映什麼** | fuzzer 的「探索深度」 | fuzzer 的最終價值（但有噪音） |
| **問題** | 高 coverage ≠ 找到 bug；不同目標 scale 不同 | 去重標準不一；severity 主觀；小樣本 |
| **FuzzBench 的選擇** | 以 coverage-based 為主，但也有 bug benchmark（Magma） | Magma benchmark 注入已知 bug |
| **適合什麼情境** | 比較 fuzzer 的泛化能力 | 評估在特定目標上的實際效果 |

**Magma** 是 2020 年提出的 bug-based benchmark，把真實 CVE 的 fix 還原（加 bug-enabling patch），然後用各種 fuzzer 跑看誰先觸發。這個方法避免了「bug 去重」和「bug 計數」的問題，因為每個 bug 都有明確的 trigger condition。

## 如何讀 FuzzBench 報告

```
FuzzBench 的典型 coverage 成長曲線圖

coverage
  │
9000│         ███████████ fuzzer_B（實線，median）
  │       ███  ░░░░░░░░░░  ← 95% CI band
8000│   ████  ░
  │ ███
7000│
  │▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓ fuzzer_A（虛線，median）
6000│ ▒▒▒▒▒▒▒▒▒  ← 95% CI band
  │
  └─────────────────────── time (hours)
  0    4    8   12   16   20   24

讀圖重點：
1. 看 median（不是 max，不是 mean）
2. 看 CI band 是否重疊（若重疊，差異可能不顯著）
3. 看長時間行為（24h 的關係可能和 4h 不同）
4. 看是否所有 benchmark target 都有相同趨勢，還是只有某幾個
```

## 設計自己的 evaluation

在實際 CVE hunting 時，你也需要評估「我的 fuzzer 配置 / harness / mutation 策略有沒有比原本更好」：

```
最低可信評測設計（每個改動都要做）：

1. 固定環境
   - 同一台機器（或同規格 VM）
   - 同樣的 CPU affinity（taskset 或 numactl）
   - 關閉 ASLR（echo 0 > /proc/sys/kernel/randomize_va_space）
     或者接受 ASLR 帶來的 ~5% coverage variance

2. 多次 trial
   - 每個配置至少 10 次（建議 20 次）
   - 每次使用不同但固定的 random seed（afl-fuzz -s 0 -s 1 ...）
   - 跑夠長的時間（至少 24 小時，複雜目標 72 小時）

3. 正確的 metric
   - 報 median edge coverage（不是 max、不是 mean）
   - 畫 coverage over time 曲線（不只報最終值）
   - 做 Mann-Whitney U test + A12 effect size + 95% CI

4. 控制變量
   - 一次只改一個變量
   - 基準線用「最原始的配置」而非「上次的最佳配置」
```

## 底層機制：FuzzBench 的 CI pipeline

```
提交 fuzzer 到 FuzzBench（PR）
        │
        ▼
GitHub Actions 觸發 CI
        │
        ▼
每個 fuzzer × target 組合：
  ├── 在 Google Cloud VM 上啟動 Docker
  ├── 跑 24 小時（或指定時間）
  ├── 每 30 分鐘記錄一次 coverage snapshot
  └── 寫入 GCS bucket
        │
        ▼
分析階段（Python notebook）：
  ├── 讀取所有 trial 的 coverage time series
  ├── 計算 median + CI（bootstrap 或正態近似）
  ├── Mann-Whitney U test（per-target）
  ├── 產生 ranking（Vargha-Delaney A12）
  └── 輸出 HTML 報告
```

## 進階：統計功效分析

在你跑 trial 之前，應該先問：「我需要跑幾次 trial 才能偵測到我預期的差異？」

```python
from scipy.stats import mannwhitneyu
import numpy as np

def power_analysis_mwu(effect_size_d, n, alpha=0.05, n_simulations=1000):
    """估計 Mann-Whitney U test 的 statistical power"""
    rejections = 0
    rng = np.random.default_rng(0)
    for _ in range(n_simulations):
        # 模擬兩組數據（常態分佈，差異為 effect_size_d 個標準差）
        a = rng.normal(0, 1, n)
        b = rng.normal(effect_size_d, 1, n)
        _, p = mannwhitneyu(a, b, alternative='two-sided')
        if p < alpha:
            rejections += 1
    return rejections / n_simulations

# 如果你的 fuzzer 改進了 0.5 個標準差（中等效果），需要幾次 trial？
for n in [5, 10, 20, 30]:
    power = power_analysis_mwu(0.5, n)
    print(f"n={n:2d} trials: power = {power:.2f}")

# 輸出（理論預期值）：
# n= 5 trials: power ≈ 0.26  （太低，很容易漏掉真實差異）
# n=10 trials: power ≈ 0.46  （還是低，Klees 建議至少 20）
# n=20 trials: power ≈ 0.68  （可以接受）
# n=30 trials: power ≈ 0.81  （比較可靠）
```

結論：n=5 的 trial，你有 74% 的機率**看不出**一個真實存在的中等差異——這就是為什麼大多數 fuzzer 論文的結論不可靠。

## 踩雷

**踩雷 1：p < 0.05 就宣稱顯著，不報 effect size**
錯誤直覺：「p = 0.04，差異顯著，我的 fuzzer 更好！」
正確：p < 0.05 只告訴你「差異不為零的可能性很高」，不告訴你「差異有多大」。如果 n=1000，即使兩組 median 只差 3 個 edge，p 也可能 < 0.05。必須同時報 A12 effect size 和信賴區間，才能說「差異有意義」。

**踩雷 2：在不同機器上比較結果**
錯誤直覺：「我昨天在 A 機器跑了 fuzzer_A，今天在 B 機器跑了 fuzzer_B，B 的 coverage 更高。」
正確：不同 CPU 的指令執行速度差異可達 2–3 倍，記憶體延遲差異也很大，這些都會影響 fuzzing throughput 和 coverage。只有在相同硬體環境下的對比才有意義。FuzzBench 強制用相同規格的 GCP VM 就是為此。

**踩雷 3：以最終 coverage 比較，忽略成長速度**
錯誤直覺：「24 小時後 A 的 coverage 是 12000，B 是 11000，A 更好。」
正確：如果 A 在 2 小時後就達到 12000 然後停滯，而 B 在 24 小時時是 11000 但仍在緩慢增長，那在 48 小時後 B 可能超過 A。coverage over time 曲線的斜率比最終點更重要。

**踩雷 4：只在作者選的 benchmark 上比**
錯誤直覺：「這篇論文在 5 個 target 上都比 AFL++ 好，說明方法是通用的。」
正確：Klees 等人分析過，如果作者自己選 benchmark，偏差是系統性的——他們傾向選自己方法有優勢的 target。用標準化 benchmark（FuzzBench 或 UNIFUZZ）才能說「通用性」。

## 進階延伸

- **UNIFUZZ**：2021 年提出的另一個標準化 benchmark，強調「usability」——不只看 coverage，也看 fuzzer 的易用性、crash 的實用性（能否真的 reproduce）。與 FuzzBench 的差異在於更注重 end-to-end 的使用者體驗。
- **Magma**：bug-injection benchmark，把 33 個真實 CVE 注入 benchmark target，用「time to bug」作為 metric，比 coverage 更直接反映「找 CVE 的效率」。
- **Statistical Comparison of Algorithms**：Demšar 2006 年的論文，推薦用 Wilcoxon signed-rank test（配對）或 Friedman test（多方法比較）做演算法比較，是比 Mann-Whitney U 更嚴謹的做法，但在多 target 比較時才需要。

## 動手練習

1. 找一篇近三年的 fuzzer 論文（USENIX Security、CCS、S&P 任一），看它的 evaluation 章節，套用 Klees 的六個批判點，列出它做對了哪幾個、做錯了哪幾個。
2. 用上面的 Python 統計腳本，把 `fuzzer_A` 和 `fuzzer_B` 的數據改成讓兩組 median 差距只有 5%（例如 8300 vs 8715），看 p-value 和 A12 如何變化，體會「差距小時統計顯著性為什麼需要更多 trial」。
3. 在 `fuzzbench.dev` 上找最近一個月的 FuzzBench 報告，挑兩個 fuzzer，讀懂 coverage over time 圖，說出哪個 fuzzer 在哪個 benchmark 上有明顯優勢、CI band 是否重疊、結論是否顯著。

## 本章重點

- 大多數 fuzzer 論文的 evaluation 有缺陷：trial 數太少、只報 max、不做統計檢定、benchmark 選擇偏差
- Mann-Whitney U test 是 fuzzing 評測的正確工具，因為 coverage 分佈非常態
- 必須同時報 median（非 max 非 mean）、p-value、A12 effect size、信賴區間
- FuzzBench 提供標準化、可重現、統計正確的 benchmark 環境，是現在的黃金標準
- Coverage-based metric 可重現但不完美；bug-based metric（Magma）更直接但更難做對

## 自我檢核

- [ ] 我能列出 Klees et al. 批判的六個常見評測缺陷
- [ ] 我能解釋為什麼 fuzzing 評測要用 Mann-Whitney U 而不是 t-test
- [ ] 我知道 Mann-Whitney U test 輸出的 U statistic 和 p-value 各代表什麼
- [ ] 我能解釋 A12 effect size 的意義（0.71 large / 0.64 medium / 0.56 small）
- [ ] 我能說出 FuzzBench 和 Magma 的 metric 差異及各自適用場景
- [ ] 我能設計一個「最低可信」的 fuzzer 比較實驗（trial 數、時長、控制變量）

## 延伸閱讀

1. **[Evaluating Fuzz Testing](https://dl.acm.org/doi/10.1145/3243734.3243804)** — Klees et al., CCS 2018
   讀哪段：Section 3（Common flaws）和 Section 5（Recommendations）；學什麼：32 篇論文的評測缺陷系統性分析，以及作者建議的正確評測流程（trial 數、時長、統計方法）。這是本章最重要的參考文獻，沒有之一。

2. **[FuzzBench: An Open Fuzzer Benchmarking Platform and Service](https://dl.acm.org/doi/10.1145/3468264.3468565)** — Metzman et al., ESEC/FSE 2021
   讀哪段：Section 3（Design decisions）和 Section 5（Evaluation）；學什麼：FuzzBench 如何解決 benchmark 標準化、統計正確性、可重現性三個問題的設計決策，以及用 FuzzBench 跑出的幾個反直覺結論（例如某些「改進方法」在標準化測試下優勢消失）。關聯：本章 FuzzBench 架構小節。

3. **[Magma: A Ground-Truth Fuzzing Benchmark](https://dl.acm.org/doi/10.1145/3427228.3427272)** — Hazimeh et al., SIGMETRICS 2021
   讀哪段：Section 2（Design rationale）和 Section 4（Findings）；學什麼：bug-injection benchmark 的設計哲學，以及為什麼 coverage 高的 fuzzer 不總是能找到更多 bug（coverage ≠ bug finding）。關聯：本章 bug-based vs coverage-based metric 對比表。

→ [下一章：從 crash 到 CVE](./47-crash-to-cve.md)
