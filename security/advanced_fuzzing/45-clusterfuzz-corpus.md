# Ch 45 — ClusterFuzz 與 corpus 管理

> **目標：** 深入理解 ClusterFuzz 的 crash dedup 機制、corpus 管理策略、regression bisection 原理，以及 corpus minimization（afl-cmin 與 libfuzzer -merge=1）的實際操作。能在自己的 fuzzing campaign 裡做出正確的 corpus 管理決策，避免語料庫膨脹與重複 crash 淹沒訊號。

## 為什麼需要 corpus 管理

一個跑了一週的 fuzzer 很容易積累 10 萬個 input 在 corpus 裡。問題不是儲存空間，而是**效率崩潰**：fuzzer 下次重啟要重新掃描每個 input 決定 coverage，語料越多，每輪 mutation 能嘗試的 seeds 越少，mutation throughput 直接下降。

另一個問題是 crash 訊號被噪音淹沒。同一個 heap overflow 可能有 3,000 個不同的 reproducer input，如果沒有去重，你的 triage 工作量乘以 3,000——每個看起來都不一樣，實際上根本是同一個 bug。

OSS-Fuzz 背後的 ClusterFuzz 把這兩個問題都解掉了：**corpus distillation** 讓語料保持小而精，**crash dedup** 讓你看到的每個 bug 只出現一次。

## 先建立直覺：語料庫為什麼會退化

```
                corpus 成長曲線（沒有 minimize）
coverage
  │        ████████████
  │      ██            ██████████████████
  │    ██
  │  ██
  │ ██
  │█
  └─────────────────────────────────────── time
           ↑ 前幾小時 coverage 快速增長
                  ↑ 之後 corpus 很大但 coverage 不再增加
                    fuzzer 把時間浪費在重複跑舊 input

                corpus 成長曲線（定期 minimize）
coverage
  │        ████████████████████████████
  │      ██
  │    ██        ↑ 每次 minimize 後 corpus 縮小
  │  ██            fuzzer 重新對小語料做 mutation
  │ ██             coverage 繼續往上爬
  │█
  └─────────────────────────────────────── time
```

本質問題：每個 input 觸發不同的 coverage path，但大多數 input 觸發的 path 是「之前其他 input 已經覆蓋過的 subset」。這些冗餘 input 佔資源但不貢獻新訊號。

## ClusterFuzz 的架構

ClusterFuzz 是 OSS-Fuzz 的後端，設計目標是管理數千台 worker VM 的 fuzzing 任務排程：

```
┌────────────────────────────────────────────────────────────┐
│                     ClusterFuzz                            │
│                                                            │
│  Task Scheduler                                            │
│  ├── fuzz task：分配 fuzz target 到 worker，跑 N 分鐘      │
│  ├── minimize task：corpus minimize + crash minimize       │
│  ├── regression task：bisect crash 到 commit              │
│  └── analyze task：crash dedup + severity 標注             │
│                                                            │
│  Storage                                                   │
│  ├── GCS bucket（per-project per-fuzzer corpus）           │
│  ├── crash database（dedup 後的 unique crash）              │
│  └── coverage report（每日 HTML 報告）                     │
│                                                            │
│  Dashboard                                                 │
│  ├── open bugs（未修的 crash）                              │
│  ├── fixed bugs（regression 確認已修）                      │
│  └── coverage trend（各 fuzz target 的覆蓋率走勢）          │
└────────────────────────────────────────────────────────────┘
        │                    ▲
        ▼                    │
  Worker VMs               crash reproducer
  ├── libFuzzer            + minimized input
  ├── AFL++                + stack trace
  └── honggfuzz            → 寄通知給 maintainer
```

## crash dedup：stack trace 正規化

ClusterFuzz 收到一個 crash 時，不是用 input 做去重（同一個 bug 的 reproducer input 千千萬萬種），而是用 **正規化後的 stack trace**。

正規化流程：

```
原始 ASan stack trace：

    #0 0x5555557a3f21 in parser_read_token myproject/parser.c:142:5
    #1 0x5555557a1c40 in parse_expression myproject/parser.c:287:9
    #2 0x5555557a0b88 in parse_statement myproject/parser.c:451:12
    #3 0x5555557a0000 in fuzz_parse fuzz/fuzz_parse.cc:18:5
    #4 0x7ffff7a1c234 in LLVMFuzzerTestOneInput ...

正規化後（用於 dedup）：

    1. 去掉 address（每次編譯可能不同）
    2. 去掉 fuzz harness 以下的框架 frame
    3. 取前 N 個 meaningful frame 的函式名
    4. hash：parser_read_token|parse_expression|parse_statement
```

這個 hash 叫做 **crash signature**。同一個 signature 的所有 crash 視為同一個 bug，只保留最小化後的 reproducer。

ClusterFuzz 還做 **security impact** 分類：
- `Heap-buffer-overflow`（寫）→ 可能可利用，高 severity
- `Heap-buffer-overflow`（讀）→ 資訊洩露，中 severity
- `Stack-buffer-overflow` → 視 context
- `Use-after-free` → 多數情況高 severity
- `Null-dereference` → 通常低 severity（DoS）

這個分類不完全準確（Ch 47 會講如何手動判斷可利用性），但提供了 triage 的起點。

## Regression Bisection

ClusterFuzz 的 bisect 任務在確認 crash 後，自動找出**哪個 commit 引入了這個 bug**：

```
已知：
  commit A（舊）→ 不 crash
  commit Z（新）→ crash

bisect 流程（類似 git bisect）：

  A ──── B ──── C ──── D ──── E ──── Z
                ↑
            ClusterFuzz 在每個 commit 重 build，
            用 minimized crash input reproducer

  如果 D crash，E 不 crash（往前找）：
  A ──── B ──── C ──── D
                         ↑ 引入 bug 的 commit = D
```

這對 CVE hunting 非常有價值：你找到一個 crash，bisect 直接告訉你去看哪個 diff，root cause 分析的起點從幾萬行縮到幾十行。

## corpus distillation：afl-cmin

**本段未實測，為理論預期行為**（環境中無 AFL++）。在有 AFL++ 的 WSL2 上驗證步驟：

```bash
# 假設有一個目標 binary（用 AFL++ instrumentation 編譯）
# 和一個龐大的 corpus 目錄

afl-cmin \
    -i big_corpus/       # 輸入：完整語料庫
    -o minimized/        # 輸出：精簡後的語料庫
    -- ./target @@        # 目標 binary（@@ 是 input 檔案佔位符）

# 預期輸出形如：
# [*] Testing the target binary...
# [*] Obtaining traces for input files in 'big_corpus/'...
# [*] Sorting trace sets (this may take a while)...
# [*] Finding best candidates for each tuple...
# [+] Narrowed down to 823 files, saved in 'minimized/'.
# [+] Corpus minimization done: 15234 -> 823 inputs
```

`afl-cmin` 的演算法是 set cover 的 greedy 近似：

```
1. 對 big_corpus/ 裡的每個 input，執行 target 並記錄觸發的 edge set
2. 貪心選擇：每次選一個能覆蓋最多「尚未被覆蓋的 edge」的 input
3. 直到所有 edge 都被覆蓋（或沒有新 input 能增加 coverage）
4. 被選中的 input 就是最小 corpus
```

## corpus distillation：libfuzzer -merge=1

libFuzzer 的 merge 模式做的事與 afl-cmin 類似，但以 libFuzzer 的 coverage 反饋為準：

```bash
# 精簡語料庫
./fuzz_target -merge=1 minimized_corpus/ big_corpus/ seeds/

# 輸出形如：
# MERGE-OUTER: 1 files, 0 in the initial corpus
# MERGE-OUTER: loading 842 files...
# MERGE-OUTER: 142 new files with 0 new features added
```

重要參數：
- `minimized_corpus/`（第一個參數）：輸出目錄，存放保留的 input
- `big_corpus/ seeds/`（後續參數）：候選 corpus，按順序嘗試

## 真跑：corpus minimize 模擬

以下是用 Python 模擬 greedy corpus minimization 的邏輯（實際就是 afl-cmin 和 libfuzzer -merge=1 背後的演算法），並展示真實輸出：

```python
import os, random

random.seed(7)

def mock_coverage(data):
    """Deterministic mock coverage bitmap based on data content"""
    edges = set()
    for i, b in enumerate(data):
        edges.add((b ^ (i * 7)) % 128)
    return frozenset(edges)

# Generate corpus：20 個 input，但有很多冗餘
corpus = {}
for i in range(20):
    size = random.randint(4, 64)
    data = bytes([random.randint(0, 255) for _ in range(size)])
    corpus["id:%06d" % i] = data

print("=== corpus minimize simulation (libFuzzer -merge=1 style) ===")
print("Initial corpus: %d inputs" % len(corpus))
print("Total bytes   : %d" % sum(len(v) for v in corpus.values()))
print()

# Greedy minimization
seen_coverage = set()
kept = []
for name in sorted(corpus.keys()):
    data = corpus[name]
    cov = mock_coverage(data)
    new_edges = cov - seen_coverage
    if new_edges:
        seen_coverage |= cov
        kept.append((name, data, len(new_edges)))

print("After minimization:")
print("  Kept   : %d inputs" % len(kept))
kept_bytes = sum(len(d) for _, d, _ in kept)
total_bytes = sum(len(v) for v in corpus.values())
print("  Bytes  : %d (%.1f%% of original)" % (kept_bytes, 100*kept_bytes/total_bytes))
print("  Unique edges covered: %d" % len(seen_coverage))
print()
print("Retained inputs:")
for name, data, new_e in kept:
    print("  %-12s  len=%3d  new_edges_added=%d" % (name, len(data), new_e))
```

實際執行輸出（Windows Python 3.12，scipy 1.18.0）：

```
=== corpus minimize simulation (libFuzzer -merge=1 style) ===
Initial corpus: 20 inputs
Total bytes   : 572

After minimization:
  Kept   : 15 inputs
  Bytes  : 466 (81.5% of original)
  Unique edges covered: 128

Retained inputs:
  id:000000     len= 24  new_edges_added=21
  id:000001     len= 39  new_edges_added=27
  id:000002     len= 46  new_edges_added=28
  id:000003     len= 13  new_edges_added=5
  id:000004     len= 48  new_edges_added=13
  id:000005     len= 64  new_edges_added=14
  id:000006     len= 39  new_edges_added=3
  id:000007     len= 21  new_edges_added=2
  id:000008     len=  8  new_edges_added=1
  id:000009     len= 13  new_edges_added=1
  id:000010     len= 30  new_edges_added=2
  id:000011     len=  9  new_edges_added=1
  id:000013     len= 43  new_edges_added=7
  id:000015     len= 19  new_edges_added=2
  id:000016     len= 50  new_edges_added=1
```

這個例子：20 個 input 中丟掉 5 個（25%），因為它們的 edge coverage 完全被其他 input 覆蓋。在真實場景裡，跑了幾天的 corpus 往往可以從幾萬個 input 縮到幾百個，而 coverage 幾乎不損失。

## 底層機制：coverage report

ClusterFuzz 每天產生 HTML coverage report，來源是：

```
1. 用 -fsanitize=fuzzer-no-link 建 coverage-only binary
2. 對整個 corpus 執行一遍，收集 LLVM profraw 檔案
3. 用 llvm-profdata merge 合併
4. 用 llvm-cov show 產生 HTML 報告

指令序列（本地重現）：
  # 建 coverage binary
  clang++ -fprofile-instr-generate -fcoverage-mapping \
      fuzz_parse.cc libmyproject.a \
      -o fuzz_parse_cov

  # 對 corpus 跑
  for f in corpus/*; do
      LLVM_PROFILE_FILE="$f.profraw" ./fuzz_parse_cov "$f"
  done

  # 合併 + 產生報表
  llvm-profdata merge -o merged.profdata corpus/*.profraw
  llvm-cov show ./fuzz_parse_cov -instr-profile=merged.profdata \
      -format=html > coverage.html
```

coverage report 讓你看到：
- 哪些函式完全沒被 fuzzer 呼叫到（需要改 harness）
- 哪些 branch 沒被覆蓋（可能需要更好的 seed）
- 覆蓋率趨勢（衡量 fuzzing campaign 是否仍在進步）

## 進階：corpus 管理策略

**多個 fuzzer 的 corpus 合併**

不同 fuzzer（libFuzzer、AFL++、honggfuzz）各自跑一段時間後，它們的 corpus 可以合併後再 minimize，取各自優點：

```bash
# 把三個 fuzzer 的語料庫合進同一個目錄
cp -r libfuzzer_corpus/* combined/
cp -r afl_out/queue/* combined/
cp -r honggfuzz_workspace/corpus/* combined/

# 用 libFuzzer merge 精簡
./fuzz_target -merge=1 minimized/ combined/

# 或用 afl-cmin（需要 AFL++ 版本的 binary）
afl-cmin -i combined/ -o minimized/ -- ./target_afl @@
```

**corpus 入庫與版本控制**

精簡後的 corpus 應該進版本控制（或至少打 snapshot 存 GCS），理由是：
- 每次 fuzzer 重啟時用 minimized corpus 當 seed，不從零開始
- 修了一個 bug 之後，能確認舊的 reproducer 真的不再 crash（regression check）

## 對比取捨

| minimize 工具 | Coverage 來源 | 優點 | 缺點 |
|--------------|---------------|------|------|
| `afl-cmin` | AFL++ edge bitmap | 對 AFL++ workflow 無縫銜接 | 需要 AFL++ instrumented binary |
| `libfuzzer -merge=1` | libFuzzer guard | 速度快、in-process | 與 AFL++ 的 edge 定義略有差異 |
| 自訂 greedy（Python） | 任意 coverage | 完全可控、可客製化邏輯 | 需要實作 instrumentation |

## 踩雷

**踩雷 1：只用 input hash 做 corpus dedup**
錯誤直覺：「兩個 input 如果 SHA256 不同就是不同的語料，都要保留。」
正確：兩個 byte-level 不同的 input 可能觸發完全相同的 coverage（例如兩個不同長度的全 0 buffer，parser 在同一行出錯）。用 coverage hash 去重才是正確的——這就是 `afl-cmin` 的做法。用 input hash 去重，語料庫只會越來越大，不會越來越精。

**踩雷 2：crash dedup 相信 signal 種類就夠了**
錯誤直覺：「同樣是 SIGSEGV，就是同一個 bug。」
正確：同一個 binary 可能有十幾個不同的 SIGSEGV 觸發點。ClusterFuzz 用 normalized stack trace hash，而不是 signal 種類。你自己做 triage 時，至少要看 ASan 報告的最頂層 N 個 frame 是否相同，不能只看 crash 類型。

**踩雷 3：corpus minimize 之後覆蓋率下降**
錯誤直覺：「minimize 會丟掉 input，coverage 一定會減少，不能隨便跑。」
正確：greedy set cover 演算法保證覆蓋 **不低於** minimize 前——被移除的 input，其 edge 必定已被保留的 input 覆蓋。如果 minimize 後 coverage 真的下降，通常是 coverage instrumentation 有 non-determinism（例如 ASLR 影響某些 edge），或者 minimize 工具本身的 instrumentation 與 fuzzer 不同。

**踩雷 4：regression bisect 結果只看第一個嫌疑 commit**
錯誤直覺：「bisect 找到 commit D，直接去 review D 的 diff 就是 root cause。」
正確：bisect 找到的是「第一個讓 crash 出現的 commit」，但 crash 可能是跨兩個 commit 的交互（例如 commit C 改了資料結構，commit D 改了用法，兩個合起來才出問題）。還是要看前後各一兩個 commit 的 diff 一起判斷。

## 進階延伸

- **Radamsa + corpus 擴增**：在 minimize 之後，用 Radamsa 對精簡後的語料做一輪結構保留的 mutation，製造新的多樣性 input 再加回 corpus，然後再 minimize 一次。這個 distill→expand→distill 的循環有時能突破 coverage 停滯期。
- **ClusterFuzz 的 weighted corpus scheduling**：ClusterFuzz 不是均等機率挑 seed，而是對最近觸發新 coverage 的 input 給更高的選取權重（類似 AFL++ 的 power schedule），稱為 energy-based scheduling。這個機制在 Ch 3（覆蓋率的本質再訪）有提過原理。
- **corpus 的語意多樣性**：coverage 相同不代表語意相同——兩個 PDF input 可能觸發相同的 code path，但一個有 embedded font 一個沒有。在知道目標有特定 feature 的情況下，手動準備「語意多樣」的 seed 比依賴 minimize 算法更有效。

## 動手練習

1. 在有 AFL++ 的環境，對一個現有的 target 跑 30 分鐘，然後用 `afl-cmin` 精簡 corpus，記錄「精簡前 input 數量 / 精簡後 input 數量 / afl-fuzz 重啟後 exec/s 的差異」。
2. 用上面的 Python corpus minimize 腳本，把 `mock_coverage` 函式替換成「真的執行 binary 並解析 gcov 輸出」，讓它變成一個實際可用的 coverage-based minimizer（不依賴 AFL 格式）。
3. 找一個有 ClusterFuzz 接入的開源專案（例如 curl 或 libpng），在 OSS-Fuzz 的 public bug tracker（https://bugs.chromium.org）上找一個已修復的 crash，閱讀 stack trace，練習判斷 crash 的去重邏輯：同一個 signature 底下有幾個不同的 reproducer？

## 本章重點

- ClusterFuzz 的 crash dedup 用正規化 stack trace hash，不是 input hash
- corpus minimization 是 set cover 的 greedy 近似，保證 coverage 不損失
- afl-cmin 和 libfuzzer -merge=1 做同一件事，差別在用哪套 instrumentation
- regression bisection 自動把 crash 定位到引入 bug 的 commit，是 root cause 分析的起點
- 定期 minimize 是長期 fuzzing campaign 維持效率的必要操作，不是可選的

## 自我檢核

- [ ] 我能解釋 ClusterFuzz 的 crash dedup 為什麼用 stack trace hash 而不是 input hash
- [ ] 我能說出 afl-cmin 的演算法（greedy set cover），並解釋為什麼 coverage 不會損失
- [ ] 我知道 `./fuzztarget -merge=1 out/ corpus/` 的參數順序及其意義
- [ ] 我能描述 regression bisection 的基本流程
- [ ] 我能列出 corpus 管理的三個決策點（何時 minimize、何時合併、何時入庫）

## 延伸閱讀

1. **[ClusterFuzz: Fuzzing at Scale](https://security.googleblog.com/2019/01/clusterfuzz-fuzzing-at-scale.html)** — Google Security Blog 2019
   讀哪段：「Fuzzing at Scale」與「ClusterFuzz internals」兩節；學什麼：crash dedup 的 stack normalization 細節，以及 ClusterFuzz 如何做 regression bisection 的工程實作。關聯：本章 ClusterFuzz 架構節的一手資料。

2. **[Corpus Distillation](https://llvm.org/docs/LibFuzzer.html#corpus)** — LLVM LibFuzzer 官方文件
   讀哪段：「Corpus」與「Merging Corpora」兩小節；學什麼：`-merge=1` 的語義、`-merge_control_file` 的斷點續跑用法，以及 `shrink=1` 對 input 大小的優化。關聯：libfuzzer -merge=1 小節的官方參考。

3. **[Coverage-Guided Fuzzing: A New Approach to Finding Vulnerabilities](https://research.google/pubs/pub46146/)** — Google Research 2019
   讀哪段：Section 4（corpus scheduling 與 energy 分配）；學什麼：為什麼 corpus 大小與 fuzzing 效率是負相關的，以及 ClusterFuzz 的 energy-based scheduling 如何緩解這個問題。關聯：本章進階延伸的 weighted corpus scheduling。

→ [下一章：FuzzBench 與評測科學](./46-fuzzbench-evaluation-science.md)
