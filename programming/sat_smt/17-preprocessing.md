# Ch 17 — Preprocessing

> 目標：在丟 CNF 給 solver 之前，先**簡化它**。Preprocessing 能讓 CNF 縮 2–10 倍、solve time 降 2–50 倍，而且絕大多數技巧都是 **線性或準線性** 時間。現代 solver（MiniSat SatELite、CaDiCaL、Kissat）都內建完整的 preprocessor。這章講最重要的五招。

## 為什麼 preprocessing

一個從 CBMC 出來的 CNF 通常長這樣：

- 10 萬變數、30 萬 clause
- 很多 clause 互相 **subsume**（一條蘊含另一條）
- 很多變數其實 **pure**（只以一個 polarity 出現）
- 很多變數可以 **消掉**（resolve 一輪 clause 不增反減）

**Preprocessor 做的就是在 CDCL 開始前，把這些廢話清乾淨**。SatELite (Eén & Biere 2005) 論文標題就叫 "Effective Preprocessing in SAT Through Variable and Clause Elimination"。

## 技巧一：Level-0 Unit Propagation

這是最 trivial 也最基本的。原 CNF 若有 unit clause `(p)`，設 `p = ⊤`、化簡所有 clause。`p = ⊥` 同理。

**效果**：某些工業 instance 光做這一步就把 10% 變數消掉。

實作：Ch 16 的 `add_clause` 裡已經做了（level-0 unit 被 enqueue）。所以嚴格說這不是 preprocessing，是 solver 天然行為。

## 技巧二：Pure Literal Elimination

**變數 `x` 只以正極性（或只以負極性）出現** → 設 `x = ⊤`（或 `⊥`）、所有含它的 clause 滿足、直接移除。

```cpp
void pure_literal(CNF& cnf) {
    std::vector<int> pos_count(cnf.num_vars + 1, 0);
    std::vector<int> neg_count(cnf.num_vars + 1, 0);
    for (const auto& c : cnf.clauses)
        for (Lit l : c) (l > 0 ? pos_count : neg_count)[var_of(l)]++;
    std::vector<int> fixed;  // pure literal 對應的 assigned var
    for (int v = 1; v <= cnf.num_vars; v++) {
        if (pos_count[v] > 0 && neg_count[v] == 0) fixed.push_back(v);
        else if (pos_count[v] == 0 && neg_count[v] > 0) fixed.push_back(-v);
    }
    // 按 fixed 化簡 CNF
    ...
}
```

現代 CDCL **通常不做 pure literal**：VSIDS 會自動避開不平衡變數，加上 pure literal 的偵測成本超過收益。但作為 preprocessing 的一輪掃描仍然有用，特別對小規模 instance。

## 技巧三：Subsumption

Clause `C` **subsumes** clause `D` 當 `C ⊆ D`（集合意義）。例如：

```
C = (a ∨ b)
D = (a ∨ b ∨ c)
```

`C ⊆ D`，所以 `C → D`（`C` 成立時 `D` 自動成立）。**`D` 是冗餘的**，可以刪。

**Subsumption 是安全化簡**：刪 `D` 不改 SAT 性質。

### 怎麼找 subsumption

naive：兩兩比對 `C_i` 和 `C_j`，檢查 `C_i ⊆ C_j`。`O(m² × k)`，k 是平均 clause 長。對 10 萬 clause 是 10^10 次檢查，不可行。

**SatELite 的加速**：為每條 clause 算一個 **signature**（literal bit bloom filter）：

```cpp
uint64_t signature(const Clause& c) {
    uint64_t sig = 0;
    for (Lit l : c.lits) sig |= 1ULL << (hash(l) & 63);
    return sig;
}
```

`C` subsume `D` 的**必要條件**是 `(sig_C & sig_D) == sig_C`。**快速排除 99% 的候選**，剩下才做精確檢查。

```cpp
bool subsumes(const Clause& c, const Clause& d) {
    if ((c.sig & d.sig) != c.sig) return false;
    for (Lit l : c.lits) {
        if (std::find(d.lits.begin(), d.lits.end(), l) == d.lits.end()) return false;
    }
    return true;
}
```

**實務加速**：只對「比 `C` 長的 clause」做檢查（`|D| > |C|` 才可能 subsume）。

## 技巧四：Self-Subsuming Resolution

比 subsumption 強。考慮：

```
C = (a ∨ ¬b)
D = (a ∨ b ∨ c)
```

對 `b` 做 resolution：

```
resolvent = (a ∨ ¬b ∨ a ∨ b ∨ c 去掉 ±b) = (a ∨ c)
```

這個 resolvent **subsumes `D`**（`{a} ⊂ {a, b, c}`）。所以 `D` 可以替換成 resolvent `(a ∨ c)`。

**效果**：從 `D` 砍掉 `b`。CNF 沒變條數但變小了。

**self-subsuming resolution 是線性時間**：掃過每對 clause，若能 resolve 出 subsume 的 resolvent 就替換。SatELite 的主要輪子之一。

## 技巧五：Bounded Variable Elimination (BVE)

DP 的 variable elimination（Ch 9）會爆，但我們可以 **受限地用**。對變數 `x`：

- `n_x` = 含 `x` 的 clause 數、`n_{¬x}` = 含 `¬x` 的 clause 數
- 消掉 `x` 會產生最多 `n_x × n_{¬x}` 條 resolvent
- **若 resolvent 數 ≤ 原 clause 數**（`n_x × n_{¬x} ≤ n_x + n_{¬x}`），消掉 `x` 是 net gain → 做

這叫 **Bounded Variable Elimination (BVE)**：只在 **clause 數不增** 時才消。

```cpp
bool should_eliminate(int v, const CNF& cnf) {
    int np = 0, nn = 0;
    for (const auto& c : cnf.clauses) {
        bool hp = false, hn = false;
        for (Lit l : c.lits) {
            if (l == v) hp = true;
            if (l == -v) hn = true;
        }
        if (hp) np++; else if (hn) nn++;
    }
    // 預估 resolvent 數並扣掉 tautology
    int estimated = count_non_taut_resolvents(v, cnf);
    return estimated <= np + nn;
}
```

實務 threshold 可放寬（允許 clause 數 +10%），換取更多變數消除。

### BVE 的限制

BVE 消掉變數後，要**記住它的 reconstruction function**：如果原公式 SAT，solver 給的 model 只覆蓋剩下的變數，消掉的那些得用原 clause 回推。

```cpp
// 消掉 x 時記下：
//   occurrence clauses 的 literal，用 pure literal 方向推
//   就是「把剩下變數的值代入、看 x 該選什麼 phase」
// model reconstruction 時按反向順序套用
```

**不做好 reconstruction，model 輸出錯、UNSAT 還對**。這是 BVE 實作最常見的 bug。

## 技巧六：Blocked Clause Elimination (BCE)

**Blocking literal**：clause `C` 中某 literal `l`，如果所有含 `¬l` 的 clause 跟 `C` resolve 出來都是 tautology，那 `l` 叫 blocking literal。**有 blocking literal 的 clause 叫 blocked clause**，可以直接刪（Järvisalo–Biere–Heule 2010）。

**BCE 比 subsumption 更強，可以刪 subsumption 刪不掉的 clause**。代價：model reconstruction 複雜。

## 技巧七：Failed Literal Probing

**Probe**：暫時 assign `x = ⊤`，跑 unit propagation、看是否衝突。若衝突，`x = ⊤` 不可能 → 強制 `x = ⊥`。反之亦然。

兩邊都衝突：UNSAT。兩邊都不衝突、但推出同樣的 literal：那個 literal 必為真（**failed literal lemma**）。

**這東西暴力**：對每個變數試兩次，`O(n × propagation cost)`。對中型 CNF 可承受，常在 inprocessing 裡用（Ch 18）。

## 完整 Preprocess Pipeline

SatELite 的經典順序：

```
1. Simplify at level 0 (unit propagate)
2. Remove tautologies, duplicate literals
3. Subsumption + Self-Subsuming Resolution 迴圈（直到 fixpoint）
4. BVE（按 variable activity 或 occurrence 排序）
5. BCE（SatELite 後續版本加的）
6. 再跑一次 Subsumption
```

現代 preprocessor（CaDiCaL 的 `preprocess()`）跑幾十個 round，每 round 組合不同技巧，直到 fixpoint 或 budget 耗盡。

## 實作建議

把 preprocessor 寫成 **獨立工具**，輸入輸出都是 DIMACS：

```bash
./preproc < raw.cnf > simplified.cnf
./sat-v2 simplified.cnf
```

這樣：

1. 方便 debug — 可以看 preprocess 後的 CNF
2. 方便 benchmark — 對比 preprocess 前後 solver time
3. 方便組合 — preprocess 產物可丟給 MiniSat / CaDiCaL 對照

### 資料結構

```cpp
struct PrepCNF {
    int num_vars;
    std::vector<Clause> clauses;
    std::vector<std::vector<size_t>> occ;  // occ[lit_idx(l)] = 含 l 的 clause index
    std::vector<uint64_t> sigs;            // signatures
    std::vector<bool> deleted;             // 標記刪除（lazy）
};
```

**Occurrence list** 是 preprocessing 的核心。跟 watched literals 不同 — occurrence list 存**所有**含某 literal 的 clause、不只兩個。空間大但 preprocess 才用、CDCL 切回 watched。

## 效能影響

在 SAT 2002 benchmarks：

| Solver | 無 preprocess | 有 SatELite | Speedup |
|---|---|---|---|
| MiniSat 2002 | 平均 45s | 平均 8s | 5.6× |
| CDCL + preprocess | 仍 UNSAT 超時 | 2s | >100× |

**某些 industrial instance preprocess 後 clause 數降 80%**，solve time 降 2 個數量級。

## 動手練習

1. **寫簡單 subsumption**：對 Ch 16 的 v2 solver 加一個 `preprocess()` function，只做 pure literal + subsumption。跑 `uf200` 系列看 solve time 怎麼變。
2. **手算 BVE**：挑一個 10 變數 CNF，手動算每個變數的 `n_x × n_{¬x}`，選一個消掉。對比原 CNF 的 SAT 性質（應該一致）。
3. **Model reconstruction 踩坑**：BVE 消變數後，寫 reconstruction code。刻意寫錯、看 solver 回報的 model 怎麼失敗（把 model 代回原 CNF 跑 eval）。

## 常見誤解

- **「Preprocess 永遠有用」** — 不對。有些 random 3-SAT 用 preprocess 反而慢（CNF 本來就沒結構）。MiniSat 有 `-no-pre` flag 關它。
- **「BVE 一定要把每個能消的變數都消」** — 不。消太多會拉長 resolvent、增加 CNF size。BVE 就是 bounded 才叫 BVE。
- **「Subsumption 跟 BCE 一樣」** — 不一樣。Subsumption 看 clause **subset**；BCE 看 **resolvent 是否全 tautology**。BCE 更強、更難實作。

## 自我檢核

- [ ] 懂五個核心技巧的思路（unit / pure / subsumption / self-subsumption / BVE）
- [ ] 知道為什麼 CDCL 不做 pure literal 但 preprocess 做
- [ ] 理解 BVE 的 bounded 條件
- [ ] 能說出 signature 為什麼加速 subsumption
- [ ] 記得 BVE / BCE 需要 model reconstruction
- [ ] 能估 preprocess 對 solve time 的影響（2–10× 常見、工業題可到 100×）

下一章 **inprocessing** — 不是 solve 前、是 solve **中間** 插入簡化，效果比 preprocessing 更強，因為可以利用 solve 過程中已知的 unit 和 failed literal。

→ [Ch 18 — Inprocessing 與 vivification](./18-inprocessing.md)
