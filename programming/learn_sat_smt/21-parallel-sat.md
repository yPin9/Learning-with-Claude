# Ch 21 — 並行 SAT：portfolio、clause sharing

> 目標：認識 SAT solver 的並行化路線 — **portfolio**（多個不同 solver 同跑）、**clause sharing**（thread 間共享 learned clause）、**cube-and-conquer**（分治）。Part 1 的收尾章節：知道 SAT 實務不只單線程，了解現代 parallel SAT 的架構。

## 為什麼 SAT 難並行

直覺上並行化 easy — 開 N 個 thread、每個搜索一部分。**實務上 brutal**：

1. **CDCL 是 sequential by nature**：每個 conflict 依賴當前 trail，trail 又依賴上個 conflict 的 learned clause。data dependency 太深。
2. **Branch 分叉不均勻**：把搜索樹切成 N 塊丟 N 個 thread，某些 thread 5 分鐘做完、某些 5 小時，**load imbalance 嚴重**。
3. **非確定性**：多 thread 下 solve time 變異極大，對 debug 和 benchmark 都麻煩。

所以 parallel SAT 不是從 single-thread 直接「平行化」，而是**重新設計架構**。

## 路線一：Portfolio

**最簡單也最有效**：開 N 個不同 solver，**同一個 instance 各自跑**。任何一個先找到答案就 kill 其他。

```
Thread 1: MiniSat with VSIDS decay 0.95
Thread 2: MiniSat with VSIDS decay 0.999
Thread 3: MiniSat + Luby 512
Thread 4: MiniSat + Glucose restart
...
```

**為什麼有用**：SAT instance 對 heuristic 敏感，不同 solver 的時間分布獨立。N 個 solver 的**最小值分布**比單個短很多（類似 race to the bottom）。

### 具體：ManySAT, Plingeling, CryptoMiniSat

- **ManySAT 2009**：8 個 MiniSat 變體 portfolio
- **Plingeling**：Biere 的 C 實作，SAT competition parallel track 多次奪冠
- **CryptoMiniSat**：portfolio + CDCL + SLS + Gauss 結合，crypto 題特化
- **HordeSAT / Mallob**：分散式 portfolio，跑百核

**效能**：4 thread 通常 2–3× 加速（不是 4×），8 thread 3–5×。diminishing returns 快。

## 路線二：Clause Sharing

Portfolio 升級。每個 thread 學的 learned clause **互相分享**：

```
Thread A learned: (¬a ∨ ¬b)   → broadcast to B, C, D
Thread B currently has this in its CNF, can use immediately
```

**好處**：A 的學習成果 B 立即受益，減少重複工作。
**問題**：共享太多會癱瘓通訊；哪條 clause 值得共享要過濾。

### 篩選條件

通常只共享 **LBD ≤ threshold** 的 clause（例 LBD ≤ 2 或 3）。這些是「強」clause，跨 thread 都有用。太大的 LBD clause 可能只對本地搜索有意義，共享反而干擾。

### 資料結構

**Lock-free ring buffer**：每個 thread 有一個 export buffer、其他 thread 週期性 poll 抓。避免 mutex 開銷。

```cpp
struct ClauseSharing {
    struct ThreadBuf {
        std::atomic<size_t> write_idx;
        std::atomic<size_t> read_idx;
        std::vector<Clause> slots;
    };
    std::vector<ThreadBuf> bufs;   // per thread
};
```

CaDiCaL 和 Kissat 都有 clause sharing 支援，具體叫 `lglshare`、`message passing shared library`。

## 路線三：Cube-and-Conquer

**Heule, Kullmann, Biere 2011**。把 SAT problem split 成**幾個子問題**，每個子問題獨立解。

### Cube = partial assignment

挑幾個變數、窮舉它們的 assignment。例如變數 `x₁, x₂, x₃` 的 8 種組合，每種得一個 **cube**。原 instance 的 UNSAT = 所有 cube 的 UNSAT（全部都 UNSAT）。

```
Cube 1: x₁=T, x₂=T, x₃=T, 解 CNF
Cube 2: x₁=T, x₂=T, x₃=F, 解 CNF
...
Cube 8: x₁=F, x₂=F, x₃=F, 解 CNF
```

8 個 cube 可以 **並行** 跑，互相獨立。

### Lookahead 選擇 cube 變數

不是隨便挑。**lookahead solver** (e.g. march_cu) 做:

1. 對每個候選變數做 probing（assume T 和 F 各 propagate 一次）
2. 看哪個變數會 **產生最多 implication**（最多 propagation 後 literal）
3. 挑這種變數做 cube

**intuition**：高 lookahead score 變數 split 後兩邊的子問題都會快速簡化，最大化 parallelism 效益。

### 實戰：Pythagorean Triples

2016 年 Heule 用 cube-and-conquer **在 cluster 上跑 2 天、200 TB proof**、解開 Pythagorean Triples Problem（Schur 5 / Boolean Pythagorean Triples）：**7825 是最大的可染色數**。

這是 SAT 史上最大的單個 instance，到今天還沒被超越。**Cube-and-conquer 讓 SAT 打入數學研究**。

## Painless 框架

**Painless** (Le Frioux et al. 2017)：parallel SAT 的通用框架。把 solver 當**模組**插入：

```
[Sequential Solver 1] -- export buffer --
[Sequential Solver 2] -- export buffer --
[Sequential Solver 3] -- export buffer --    ---> Sharing Strategy ---> Import to each
```

- 隨便換 sequential engine（Glucose、MapleSAT、MiniSat）
- 隨便換 sharing strategy（按 LBD、按 literal 頻率、按 generation）
- 隨便換 diversification（portfolio、cube、混合）

**研究 parallel SAT 的標準實驗平台**，paper 寫 "based on Painless" 的很多。

## 並行的挑戰細節

### 1. 非確定性

每次 run 結果不同、bench 不穩。Solution：固定 thread scheduling、rand seed。工業上難做到絕對 deterministic。

### 2. Memory 壓力

Portfolio 每個 solver 獨立記憶體 copy。8 個 solver × 1 GB = 8 GB。heap 衝突、cache thrashing。Kissat 的 `--mab` 模式試圖共享 read-only 狀態。

### 3. Shared CNF

所有 thread 讀同一 CNF 節省記憶體。**但 learned clause 必須各自**（每個 thread 學的不一樣）。搞清楚什麼可共享、什麼不行是 implement 的關鍵。

### 4. Load balancing

Cube-and-conquer 的 cube 難度不均。解法：**dynamic cube stealing** — 快解完的 thread 從慢的那邊拿 sub-cube。Mallob 做得最激進。

## 現代 Parallel Solver 排名

SAT Competition 2023 Parallel Track 前三：

1. **Mallob** (Schreiber, Sanders) — distributed, cloud-native
2. **PKis-Sbva** (Kissat 的 parallel 版)
3. **PaInleSS-Mab** (Painless 框架上的 MAB 學習)

這些都在百核以上跑。個人 workstation 級別一般用 CaDiCaL 或 Kissat 單執行緒，因為工業題他們已經夠快。

## 什麼時候選 parallel SAT

| 情境 | 建議 |
|---|---|
| 小/中 instance (< 1000 變數) | 單執行緒 |
| 大工業 instance | Portfolio 2–4 thread |
| Crypto / planning (多變數、結構規則) | Portfolio + SLS |
| Research-level hard (PHP, Ramsey, combinatorics) | Cube-and-conquer + cluster |
| 連續解多個 instance | 分配 thread 給每個 instance 比 parallelize 單個好 |

## 動手練習

1. **最簡 portfolio**：用 bash 平行跑 MiniSat、Glucose、CaDiCaL 同個 instance，誰先完成就 kill 其他。寫個 timing 對比純單執行緒。
2. **Cube generator**：對 100 變數 instance 挑前 10 個 VSIDS-active 變數，enumerate 2^10 = 1024 cube，隨機挑 8 個跑 v2 看時間分布。
3. **Clause sharing 試玩**：CaDiCaL 的 `-w` 選項啟用共享模式（內建 worker pool），用同樣 instance 跑 1/2/4/8 worker 對比。

## 常見誤解

- **「N 個 thread 就有 N× 加速」** — 絕無可能。Parallel SAT 典型 3–5× on 8 thread。
- **「Cube-and-conquer 一定比 portfolio 好」** — 不對。結構化 hard instance 贏，但一般工業題 portfolio 勝（cube 選擇 overhead 不值）。
- **「Clause sharing 越多越好」** — 錯。過度共享癱瘓 thread、污染 learning。threshold 通常 LBD ≤ 2 或 3。
- **「Lock-free 是唯一正解」** — 多數 SAT 共享不需 lock-free（延遲容忍高）。Mutex + back-off 夠用。

## 自我檢核

- [ ] 懂 portfolio 為何有用（heuristic 敏感性 + race to bottom）
- [ ] 說得出 clause sharing 的篩選條件 (LBD)
- [ ] 懂 cube-and-conquer 的分治原則
- [ ] 知道 Pythagorean Triples 這個著名 parallel SAT case
- [ ] 了解 Painless 框架的模組化設計
- [ ] 會估 parallel SAT 的實際加速比（3–5× on 8-thread 常見）

Part 1 核心章節寫完了。剩**兩個練習**讓你把所學打包成實作。練習 B 是完整 DPLL solver（比 v1 強的教學版），練習 C 是 CDCL + watched literals（比 v2 精簡但完整）。做完這兩個你就是 SAT 實作入門。

→ [練習 B — 完整 DPLL solver](./practice-b-dpll-solver.md)
