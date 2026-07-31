# 練習 A — 寫一份 Coremark 效能報告

> **目標**：整合 Part 1（benchmark 哲學 + 統計）的知識，實際跑 Coremark、收集多組數據（不同 compiler/flag）、用嚴謹的統計分析、寫成一份**專業的效能報告**。報告格式對應 SiFive/客戶 benchmark team 的標準。完成後你能產出「可信、可重現、有統計支撐」的效能報告——這是效能工程師的核心交付物，也是和 benchmark team 對話的基礎。

## 背景與動機

效能工程師的一個核心交付是**效能報告**——測量某個 benchmark、用嚴謹的方法、寫成別人能信任和重現的報告。報告不是「測一個數字貼上去」，而是「完整地揭露條件、用統計分析、得出可信的結論」。

這個練習讓你實際跑 Coremark（Ch 3）、應用 Part 1 的方法論（控制環境 Ch 0、避陷阱 Ch 1、統計嚴謹 Ch 4）、寫成專業報告。這正是 SiFive compiler 工程師和 benchmark team 對話的基礎——你的報告要讓他們能信任你的數字、理解你的分析、重現你的測量。完成這個練習，你建立了「產出專業效能報告」的能力——這是效能工作的標準交付，也是專業 vs 業餘的重要區分。

## 任務規格

跑 Coremark 並寫一份完整的效能報告，包含：

| 部分 | 內容 | 對應章節 |
|---|---|---|
| 測量環境 | machine、CPU、頻率、governor、OS、編譯器版本 | Ch 0 |
| 測量方法 | 多次測量、控制變因、warmup | Ch 0/4 |
| 數據 | 多組（不同 compiler/flag）的 Coremark 分數 + 統計 | Ch 3/4 |
| 統計分析 | 平均、標準差、信賴區間、差異是否顯著 | Ch 4 |
| 結論 | 可信的結論（哪個 flag/compiler 最好，差異顯著嗎）| Ch 4 |
| 揭露 | 完整的條件揭露（讓人能重現）| Ch 2/3 |

**核心要求**：報告要「可信」（統計嚴謹）、「可重現」（完整揭露條件）、「誠實」（差異不顯著就說不顯著，不誇大）。

## 如果你卡住了

1. 先控制測量環境（Ch 0：governor、綁核心、關 ASLR）——否則數據變異大不可信
2. Coremark 要跑多次（至少 5-10 次）才能算統計（Ch 4）
3. 比較不同 flag（-O2/-O3/-march）要用相同的 compiler 和環境（Ch 2 的誤用）
4. 算統計：平均、標準差、信賴區間（看差異是否顯著）
5. 報告要揭露完整條件（compiler 版本、flag、machine——讓人能重現）
6. 誠實：如果 -O3 和 -O2 差異在誤差範圍內，就說「無顯著差異」（不要誇大）

## 實作步驟建議

### Step 1：控制測量環境（Ch 0）
### Step 2：跑 Coremark 多組（不同 flag）× 多次（統計）
### Step 3：統計分析（平均/標準差/CI/顯著性）
### Step 4：寫報告（環境/方法/數據/分析/結論/揭露）
### Step 5：檢查報告的可信度（可重現嗎、誠實嗎）

## 完整參考解答

**自己跑並寫一遍！** 親手做才學到「嚴謹的效能報告」。

<details>
<summary>測量流程 + 報告範本</summary>

```bash
# ===== Step 1: 控制環境 (Ch 0) =====
sudo cpupower frequency-set -g performance 2>/dev/null
echo 0 | sudo tee /proc/sys/kernel/randomize_va_space   # 關 ASLR（測完恢復）
# 記錄環境
echo "=== Environment ===" > report.md
echo "CPU: $(lscpu | grep 'Model name' | sed 's/.*: *//')" >> report.md
echo "Cores: $(nproc), Freq governor: performance" >> report.md
echo "OS: $(uname -sr)" >> report.md
echo "GCC: $(gcc --version | head -1)" >> report.md

# ===== Step 2: 跑 Coremark 多組 × 多次 =====
cd ~/perflab/coremark
for flags in "-O2" "-O3" "-O2 -march=native" "-O3 -march=native"; do
    make clean > /dev/null 2>&1
    make PORT_DIR=linux64 XCFLAGS="$flags" > /dev/null 2>&1
    echo "=== flags: $flags ==="
    # 跑 10 次，收集分數
    for run in $(seq 1 10); do
        taskset -c 2 ./coremark.exe 0x0 0x0 0x66 0 7 1 2000 2>/dev/null | \
            grep "CoreMark 1.0" | grep -oP ': \K[0-9.]+'
    done
done

# ===== Step 3: 統計分析 (Ch 4) =====
# 用 python 算統計（平均/標準差/CI）
python3 <<'PYEOF'
import statistics, math
# 假設收集到的數據（替換成你的）
data = {
    "-O2":              [11020, 11050, 11000, 11030, 11010, 11040, 11025, 11015, 11035, 11005],
    "-O3":              [11100, 11120, 11080, 11110, 11090, 11130, 11105, 11095, 11115, 11085],
    "-O2 -march=native":[11500, 11520, 11480, 11510, 11490, 11530, 11505, 11495, 11515, 11485],
}
print(f"{'Flags':<22} {'Mean':<10} {'StdDev':<8} {'95% CI':<18}")
results = {}
for flags, scores in data.items():
    mean = statistics.mean(scores)
    stdev = statistics.stdev(scores)
    ci = 1.96 * stdev / math.sqrt(len(scores))   # 95% CI
    results[flags] = (mean, ci)
    print(f"{flags:<22} {mean:<10.0f} {stdev:<8.1f} [{mean-ci:.0f}, {mean+ci:.0f}]")

# 判斷差異是否顯著（CI 重疊嗎）
print("\n=== 顯著性分析 ===")
o2_mean, o2_ci = results["-O2"]
o3_mean, o3_ci = results["-O3"]
# O2 vs O3
if abs(o3_mean - o2_mean) > (o2_ci + o3_ci):
    print(f"-O3 vs -O2: 顯著差異（O3 快 {(o3_mean/o2_mean-1)*100:.1f}%）")
else:
    print(f"-O3 vs -O2: 無顯著差異（CI 重疊，差異可能是雜訊）")
PYEOF
```

```markdown
<!-- ===== Step 4: 報告範本 ===== -->
# Coremark 效能報告

## 測量環境
- CPU: [型號], [核心數] cores
- 頻率: performance governor（固定），turbo: disabled
- OS: Linux [版本]
- 編譯器: GCC [版本]
- Coremark: [版本], ITERATIONS=2000

## 測量方法
- 每組 flag 跑 10 次（taskset 綁定 CPU 2，ASLR 關閉）
- 報告平均 ± 95% 信賴區間
- warmup: 前 2 次丟棄

## 數據

| Flags | Mean Coremark | StdDev | 95% CI |
|---|---|---|---|
| -O2 | 11023 | 16.2 | [11013, 11033] |
| -O3 | 11103 | 16.2 | [11093, 11113] |
| -O2 -march=native | 11503 | 16.2 | [11493, 11513] |

## 統計分析
- **-O3 vs -O2**: O3 快 0.7%，CI [11093,11113] vs [11013,11033] 不重疊 → **顯著**
- **-march=native vs -O2**: 快 4.4%，明顯顯著
- Coremark/MHz: [分數/頻率] = [值]（核心效率指標）

## 結論
1. -march=native 提供最大提升（4.4%，使用本機指令集）
2. -O3 比 -O2 略快（0.7%，統計顯著但幅度小）
3. 對此 workload（Coremark），-O2 -march=native 是好的選擇

## 重現
完整命令：`make PORT_DIR=linux64 XCFLAGS="-O2 -march=native"`，環境如上。

## 限制與注意
- Coremark 是合成 benchmark（Ch 3 的爭議），不完全代表真實 workload
- -march=native 的 binary 只能在同型 CPU 跑
- 結果是此特定環境/編譯器的，換環境要重測
```

**報告說明**：

- **完整的環境揭露**（Ch 0）：machine/CPU/頻率/governor/OS/編譯器——讓人能重現
- **嚴謹的方法**（Ch 4）：多次測量、控制變因、報告 CI
- **統計分析**（Ch 4）：不只報平均，報 CI 和「差異是否顯著」（CI 是否重疊）
- **誠實的結論**：-O3 只快 0.7%（雖顯著但小）——誠實呈現，不誇大
- **限制揭露**（Ch 2/3）：Coremark 的限制、march=native 的相容性、結果的特定性
- **核心**：報告「可信」（統計嚴謹）+「可重現」（完整揭露）+「誠實」（不誇大）

</details>

## 測試用案例

| 檢查項 | 標準 |
|---|---|
| 環境揭露 | machine/CPU/頻率/編譯器都有 |
| 多次測量 | 至少 5-10 次 |
| 統計 | 平均 + CI + 顯著性判斷 |
| 誠實 | 不顯著就說不顯著 |
| 可重現 | 別人照報告能重現 |

## 延伸挑戰（加分）

- **挑戰一**：加 perf 分析——對最快和最慢的版本用 perf stat，解釋「為什麼快」（IPC/cache 的差別）

- **挑戰二**：RISC-V——在 RISC-V 硬體/QEMU 上跑 Coremark，比較 -march=rv64gc vs rv64gcv（向量）

- **挑戰三**：Coremark/MHz——算並報告 Coremark/MHz（核心效率），和公開的其他 core 比較

- **挑戰四**：compiler 比較——比較 gcc vs clang 的 Coremark，分析差異（compiler 的優化差別）

- **挑戰五**：自動化——寫一個腳本自動跑多組 × 多次、算統計、產生報告（可重複使用的 benchmark harness）

## 自我檢核

- [ ] 能控制測量環境（governor/綁核心/ASLR）做可信的測量
- [ ] 能跑 Coremark 多組多次，收集統計數據
- [ ] 會做統計分析（平均/CI/顯著性）
- [ ] 能寫完整、誠實、可重現的效能報告
- [ ] 理解報告的可信度來自統計嚴謹 + 完整揭露

這個練習訓練了「產出專業效能報告」的能力。接下來練習 B——用 llvm-mca 深入分析一段 hot loop 的指令層瓶頸。

→ [練習 B：用 llvm-mca 分析一段 hot loop](./practice-b-llvm-mca-case.md)
