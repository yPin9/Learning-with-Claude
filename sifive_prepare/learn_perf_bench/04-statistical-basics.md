# Ch 4 — 統計基本功：geomean / CI / noise 控制

> 目標：讓你的 benchmark 數字 scientifically 有意義。理解 arithmetic mean vs geometric mean、confidence interval、outlier 處理、如何判斷「改進是 real 還是 noise」。

## 為什麼統計重要

典型的糟糕 benchmark report：

> "After my change, performance improved by 2.3%."

問題：

- 跑幾次？
- 同一 machine？
- 時間尺度？
- 確信區間？

沒有統計框架，「2.3% 改進」可能就是 noise。

真實 compiler 工作中，**最大的痛是 regression noise**：改一個 pattern、某 benchmark 正負 1% 、每天花時間分辨是改對還是改錯。嚴謹統計讓你事半功倍。

## Arithmetic mean vs Geometric mean

### Arithmetic mean（算術平均）

$$\text{AM} = \frac{x_1 + x_2 + \ldots + x_n}{n}$$

適合：**absolute 數量**（seconds, bytes, cycles）。

### Geometric mean（幾何平均）

$$\text{GM} = \sqrt[n]{x_1 \cdot x_2 \cdot \ldots \cdot x_n}$$

適合：**ratio、normalized score**。

### 為什麼 SPEC 用 geomean

SPEC 對每個 benchmark 算 ratio（reference / yours）。若用 arithmetic mean：

```
benchmark A: ratio 10
benchmark B: ratio 0.1
AM = 5.05 (明顯被 A dominate)
GM = 1.0  (合理：一個快 10×、一個慢 10×，平均 0)
```

Arithmetic mean 被 outlier 拉偏。Geometric mean 對 ratio 更公平。

**記憶法**：「比例用幾何、絕對用算術」。

## Median vs Mean

Mean 對 outlier 敏感。10 次 run：

```
9 runs 都 1.0 秒
1 run 因 OS 干擾 10 秒

Mean = 1.9 秒
Median = 1.0 秒
```

benchmark run 常有 outlier（GC、page fault、interrupt 等），**median 較穩健**。

### 實務建議

- 5 次 run：用 median
- 10+ 次：用 mean of middle N （丟前後 10% outlier）
- 100+ 次：mean + stddev 就夠

## 標準差（Standard Deviation）

$$\sigma = \sqrt{\frac{\sum (x_i - \bar{x})^2}{n}}$$

衡量分散程度。常看 **coefficient of variation (CV)**：

$$CV = \frac{\sigma}{\bar{x}}$$

- CV < 1%：benchmark 乾淨
- CV = 1-3%：正常 noise
- CV > 5%：系統有問題（turbo、thermal、background）

SPEC 官方要求 CV < 2% 才 reportable。

## Confidence Interval

95% CI 的粗估：

$$CI = \bar{x} \pm 1.96 \cdot \frac{\sigma}{\sqrt{n}}$$

例：10 次 run、mean=1000ms、stddev=20ms：

```
CI = 1000 ± 1.96 × 20/√10 = 1000 ± 12.4
95% CI = [987.6, 1012.4]
```

意思：真實 mean 有 95% 機率在這範圍內。

**用途**：比較兩組 benchmark，CI 重疊 → 改進可能是 noise。

## A/B 比較：t-test

我改了 compiler，測 N=10 runs before + N=10 runs after。怎麼判斷 real improvement？

**Welch's t-test**：

```python
import scipy.stats as stats

before = [1002, 1005, 998, 1001, 1003, 999, 1004, 1002, 1000, 1003]
after = [985, 987, 990, 988, 986, 989, 987, 984, 988, 986]

t, p = stats.ttest_ind(before, after, equal_var=False)
print(f"t = {t:.3f}, p = {p:.6f}")
```

- `p < 0.05`：95% 有統計意義 → 可能 real
- `p >= 0.05`：可能是 noise

**不是 magic**。`p < 0.05` 是 convention、不保證 causation。

## Noise 的來源與控制

### 來源

1. **OS**：scheduler、page fault、ASLR、interrupt
2. **CPU**：turbo、thermal throttle、PEU (perf event units) conflict
3. **Memory**：huge pages、NUMA
4. **Branch predictor**：cold start
5. **Other process**：cron、systemd、background

### 控制方法

```bash
# 1. 固定 CPU 頻率（禁 turbo）
sudo cpupower frequency-set --governor performance
echo 0 | sudo tee /sys/devices/system/cpu/cpufreq/boost

# 2. Isolate CPU core
# 開機 kernel command line: isolcpus=3
taskset -c 3 ./benchmark

# 3. 禁 ASLR
setarch $(uname -m) -R ./benchmark

# 4. 用 cgroup 限制其他 process
# (advanced)

# 5. Disable SMT/HT
echo off | sudo tee /sys/devices/system/cpu/smt/control

# 6. Huge pages (prevent TLB noise)
echo always | sudo tee /sys/kernel/mm/transparent_hugepage/enabled
```

實際生產中 SiFive 類公司有 dedicated benchmark machine，以上 tune 全開。

## Warmup

每次 run 的前幾 iteration 往往慢（cold cache、未 JIT、CPU scaling up）。丟棄：

```python
def bench():
    for _ in range(5):
        run()  # warmup
    
    results = []
    for _ in range(10):
        results.append(run())
    
    return median(results)
```

SPEC 自帶 warmup，其他 micro-benchmark 自己處理。

## Geometric mean 的陷阱

不能有 0 或負值（log 不定義）。實務：

- Ratio 全正數時 OK
- Time / throughput 都是正 → OK
- 某些 bug reveal 成 ratio=0 → 手動處理

## Amdahl's Law 跟 benchmark 解讀

改 part of program：

$$\text{Speedup} = \frac{1}{(1-P) + \frac{P}{S}}$$

- `P`: 被改部分佔總時間比例
- `S`: 被改部分的 speedup

例：某 function 佔 10% 時間、改快 10×：

```
Speedup = 1 / (0.9 + 0.1/10) = 1 / 0.91 = 1.099
```

**整體只快 9.9%**。雖然 hot function 快 10×，但非 hot path 不變。

benchmark 看到「x1.10 speedup」時想一下：可能 hot function 大幅改進（值得）、或全面小改進（可疑）。

## Benchmark noise 的 "dynamic range"

同 machine 兩次 run 的 CV 是 baseline。如果你的 change 影響 < baseline CV，**基本不可能 detect**。

```
Baseline CV: 0.8%
Your change claims: +0.5%
→ Below noise floor, not trustable
```

要先量 baseline noise、再 claim improvement > noise。

## Aggregate 多個 benchmark 的方法

你改 compiler、跑 SPEC 10 個 benchmark：

| Benchmark | Before | After | Ratio |
|-----------|--------|-------|-------|
| perl      | 100    | 98    | 0.98 |
| gcc       | 200    | 195   | 0.975 |
| mcf       | 150    | 155   | 1.033 (worse!) |
| ...       | ...    | ...   | ... |

**怎麼總結？**

- Geometric mean of ratio：顯示整體 speed-up
- 列出 regression：mcf 退 3.3% 要 explain
- Per-benchmark 5% threshold：關心 > 5% 的變化

**做 compiler 的工程師報告格式**：

```
Overall geomean: 1.02x (improvement)
Regressions: mcf (-3.3%), xz (-1.5%)
Improvements: gcc (+2.5%), leela (+4.0%)
```

## Python 快速統計 template

```python
import numpy as np
from scipy import stats

def compare(before, after):
    m1, m2 = np.mean(before), np.mean(after)
    std1, std2 = np.std(before, ddof=1), np.std(after, ddof=1)
    t, p = stats.ttest_ind(before, after, equal_var=False)
    
    print(f"Before: {m1:.2f} ± {std1:.2f} (CV {std1/m1*100:.1f}%)")
    print(f"After:  {m2:.2f} ± {std2:.2f} (CV {std2/m2*100:.1f}%)")
    print(f"Change: {(m2-m1)/m1*100:+.2f}%")
    print(f"p-value: {p:.4f} {'*' if p < 0.05 else ''}")

compare(before_times, after_times)
```

## 常見誤會

1. **「一次 run 夠」**：絕對不夠。至少 5 次。
2. **「Mean 就好」**：outlier sensitive、用 median 或 trimmed mean。
3. **「SPEC 分數沒 CI」**：SPEC 規則要求 3 次、取 median。官方沒印 CI、但 rule 隱含 variance 夠小。
4. **「改進 0.5% 在 benchmark 有意義」**：看 noise。baseline 0.3% noise 才有意義。
5. **「所有 benchmark 都提升 1% 就是 win」**：要看是否超過 noise、是否有 outlier regression。

## 動手練習

1. 寫一個簡單 C program、跑 100 次，用 Python 分析：mean、median、stddev、CV。
2. 改 CPU 頻率 scaling governor，對比 noise 差異。
3. 用 Python 的 `scipy.stats.ttest_ind` 比較兩組 benchmark 結果。
4. 對一個 SPEC benchmark 跑 3 次、10 次，比較 CI 收斂速度。
5. 讀一份 SPEC result 的原始 raw file，辨認 per-run 數字、median、final ratio。

## 自我檢核

- [ ] 我知道何時用 arithmetic mean、何時用 geometric mean
- [ ] 我能算 confidence interval 跟 CV
- [ ] 我能做 A/B comparison 的 t-test
- [ ] 我知道怎麼 tune system 降 noise
- [ ] 我能判斷 「這改進 real 還是 noise」

下一章切到硬體側 — CPU 微架構速成，讓你聽得懂 IPC、ROB、cache hierarchy 這些詞。

→ [Ch 5 CPU 微架構速成](./05-microarch-primer.md)
