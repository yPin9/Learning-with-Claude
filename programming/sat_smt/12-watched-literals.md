# Ch 12 — Two Watched Literals

> 目標：把 unit propagation 從 Ch 11 的 `O(N × M)` 降到 **amortized O(1) per propagation**。這是 Moskewicz et al. 2001 的 **Chaff** 論文貢獻，**所有現代 SAT solver 都用這招**。理解 watched literals = 理解 CDCL 為什麼跑得動工業題。

## 痛點回顧

Ch 11 的 `unit_propagate` 每輪掃 **整個 CNF**，對每條 clause 檢查：

- 有沒有 literal 為 true（satisfied）
- 還有幾個 unassigned
- 是不是 unit 或 conflict

一條 clause 有 `k` 個 literal 要看 `k` 次。`M` 條 clause 加起來 `O(Σ kᵢ)`，近 `O(literals 總數)`。每次有變數 assign 就 rerun，百萬 clause 碰百萬 assignment，吃不消。

**但我們在做蠢事**：大多數 clause 在當前 assign 下根本沒變化，重新檢查浪費。

## 關鍵觀察

Assign 一個 literal `l` 為 ⊤ 時，**只有含 `¬l` 的 clause 可能變差**（多了一個 false literal）。含 `l` 本身的 clause 只會變 **更好**（satisfied 或保持），不用重看。

換句話說：**你只需要在變數變 false 時去檢查 clause**。但還是麻煩 — 一條 clause 可能有 10 個 literal，其中 3 個變 false 但還有 7 個活著、這條也還活著，不需要碰。

Watched literals 的主意：**一條 clause 不管多長，只盯緊兩個 literal**。只有當這兩個中一個變 false 才處理。

## 核心 Invariant

對每條 clause `C`，挑兩個 literal 叫 `watched_0`、`watched_1`。維持 **invariant**：

> 只要 `C` 裡還有 ≥ 2 個非 false 的 literal，`watched_0` 和 `watched_1` 就是其中兩個。

換句話說：**watched literals 不會雙雙為 false，除非 clause 真的 unit 或 conflict**。

```
[ l₁, l₂, l₃, l₄, l₅ ]
   ↑              ↑
  w0              w1
```

`w0 = l₁`、`w1 = l₅`。只要這兩個之中至少一個不是 false，clause 還活著、我們就不動它。

## Watch list

每個 literal `l` 維護一個 list：**所有以 `l` 當 watched literal 的 clause**。記作 `watches[l]`。

資料結構：

```cpp
// watches[l] = 所有監看 l 的 clause index
std::vector<std::vector<ClauseIdx>> watches;   // size = 2 * (num_vars + 1)
```

空間雙倍是因為 `l` 可以是正或負，兩種分開存（`watches[2*v]` 存正 literal 的 watch、`watches[2*v+1]` 存負的）。

## Assign 時怎麼處理

**Assign `l = ⊤`** 代表 `¬l` 變 false。掃 `watches[¬l]`，對每條 clause 做：

1. 如果另一個 watched literal 已 ⊤ → clause satisfied，沒事
2. 否則去 clause 裡找 **任一個非 false 的 literal**（不是 `¬l`、不是另一個 watched）：
   - 找到 → **把 watch 從 `¬l` 移到新 literal**。這條 clause 從 `watches[¬l]` 拔出、加到 `watches[new]`
   - 找不到：
     - 另一個 watched literal unassigned → **unit**，強制 propagate
     - 另一個 watched literal 已 false → **conflict**

只處理 `watches[¬l]`，不掃其他 clause。**這就是加速的來源**。

## 走一遍

Clause `C = (a ∨ b ∨ c ∨ d)`，watched = (`a`, `b`)。

```
目前：a unassigned, b unassigned, c unassigned, d unassigned
```

Assign `a = false`（即 `¬a = true`）：進 `watches[¬a]` 找到 `C`。

- 另一個 watched `b`：unassigned，沒滿足
- 找新 watcher：`c` unassigned，可以。把 watch `a` 換成 `c`

```
watched = (b, c)   // a 不再被監看
```

Assign `b = false`：進 `watches[¬b]` 找到 `C`。

- 另一個 watched `c`：unassigned
- 找新 watcher：`d` unassigned，OK。換

```
watched = (c, d)
```

Assign `c = false`：進 `watches[¬c]` 找到 `C`。

- 另一個 watched `d`：unassigned
- 找新 watcher：`a`、`b` 都 false、`c` 正在變 false。**找不到**
- `d` unassigned → **unit**，強制 `d = true`

**在 `d = true` 之前 SAT solver 完全沒碰過 `C` 的其他 literal**。

## Backtrack 幾乎 free

DPLL 的 chronological backtrack 要 restore 整個 assignment。Naive 實作還得重跑 unit propagation。但 **watched literals 不用復原**：

Invariant 是「不存在兩個都 false」。當你 backtrack、某變數變 unassigned，`false` literal **變少不變多**，invariant **自動繼續成立**。

所以 backtrack 只需：

1. 把那條 undo 掉的 assignment 從 trail 移掉
2. **完全不動 watch lists**

這讓 backtrack 複雜度從 `O(literals undone × clause 檢查)` 降到 `O(literals undone)`。工業題裡這個差距是兩個數量級。

## 為什麼是 two，不是 three

1 個不夠：一條 `(a ∨ b)` clause 只 watch `a`，`a` 變 false 時你不知道 `b` 是否也 false、要不要 unit。

3 個可以（甚至偵測得更早），**但成本 > 收益**：每條 clause 維護 3 個 watcher，assign 時要碰 3 個 watch list，memory traffic 變 1.5 倍。兩個 watcher 是最佳折衷。

## 資料結構細節

MiniSat/Chaff 的標準實作：

```cpp
struct Clause {
    std::vector<Lit> lits;   // lits[0], lits[1] 是 watched
    // 沒有 size 欄位之外的額外 metadata（clean design）
};

// watches[lit_index(l)] = 所有以 l 當 watched 的 clause 指標
std::vector<std::vector<Clause*>> watches;

// lit_index: +k → 2k, -k → 2k+1（或其他雙向對應）
inline int lit_index(Lit l) { return l > 0 ? 2*l : 2*(-l)+1; }
```

**Watched literals 永遠放在 `lits[0]`、`lits[1]`**，這樣：

- 換 watcher 只需 `std::swap(lits[0], lits[j])` 把新 watcher 挪到位置 0
- Code 裡檢查「另一個 watched」直接 `lits[0]` / `lits[1]`，不用搜

## Propagate 的完整 pseudo-code

```cpp
bool propagate(Lit assigned) {
    // assigned 剛被 assign 為 true，掃 watches[¬assigned]
    auto& ws = watches[lit_index(-assigned)];
    for (size_t i = 0; i < ws.size(); ) {
        Clause* c = ws[i];

        // 確保 lits[1] 是「剛變 false 的那個」，lits[0] 是「另一個 watcher」
        if (c->lits[0] == -assigned) std::swap(c->lits[0], c->lits[1]);
        Lit other = c->lits[0];

        // case 1：另一個 watcher 已 true → clause sat，不動
        if (lit_value(other, assignment) == Value::True) {
            i++; continue;
        }

        // case 2：找新 watcher（位置 2 以後）
        bool found_new = false;
        for (size_t k = 2; k < c->lits.size(); k++) {
            if (lit_value(c->lits[k], assignment) != Value::False) {
                std::swap(c->lits[1], c->lits[k]);
                // 從當前 watch list 拿掉，加到新的
                ws[i] = ws.back(); ws.pop_back();
                watches[lit_index(c->lits[1])].push_back(c);
                found_new = true;
                break;
            }
        }
        if (found_new) continue;

        // case 3：找不到，看另一個 watcher
        if (lit_value(other, assignment) == Value::Unassigned) {
            // unit: force other = true
            enqueue(other, c);   // 塞進 propagation queue，reason 是 c
            i++;
        } else {
            // conflict：other 也 false
            return false;
        }
    }
    return true;
}
```

**三種 case** 的取名在 SAT 論文裡是標準。你看 MiniSat 0.4 的 `propagate()` 就是這個骨架、大約 40 行 C++。

## 效能比較

Ch 11 的 v1 跑 `uf100-01.cnf`（100 變數 3-SAT）：

```
v1 (naive prop):     decisions: ~45000, time: ~5s
```

換成 watched literals（v1.5）：

```
v1.5 (watched):      decisions: ~45000, time: ~0.3s
```

**decisions 一樣，time 降 15 倍**。演算法沒變，資料結構換了。這就是 Chaff 2001 拿下那年 SAT competition 的原因。

到 Ch 16 加上 CDCL 再配 VSIDS，decisions 數本身會降 100 倍以上，兩件事疊起來就是 MiniSat 級別。

## 實作的地雷

1. **Clause literal 順序會被改**：如果你 code 別處有「clause 是不可變的 literal 序列」這個假設，會炸。多數實作讓 `Clause` 就是個 `vector<Lit>`，order 本來就不保證。
2. **Watch list 用 `vector` 別用 `list`**：cache 友好。現代 cpu 上 linked list 比 vector 慢 5–10 倍。
3. **Clause 位置不能動**：watch list 存的是 pointer / index，clause 搬家後指標失效。要麼 `std::vector<Clause>` reserve 好不 resize，要麼用 indirect allocator。
4. **Delete clause 時要從 watch list 移掉**：Ch 17 preprocessing 會刪 clause，記得 unwatch。
5. **Enqueue 後才 assign，別反過來**：`enqueue(other, c)` 內部會 assign `other` 並把它放 propagation queue。Assign 順序錯會讓 trail 和 assignment 不一致。

## Lazy vs Eager 的哲學

Watched literals 是 **lazy data structure** 的經典案例：

- **Eager**：每次有變化立刻更新所有相關狀態
- **Lazy**：推遲更新，只在真的需要時才碰

DPLL naive 是 eager — 每次 assign 後把所有 clause 重算狀態。Watched literals 是 lazy — 大多數 clause 靜靜躺著，沒人理它們。

**SAT solver 的效能秘訣很大部分是 laziness**：watched literals、lazy reason clause（Ch 13）、lazy theory propagation（Ch 23 SMT）— 你會一再看到這個哲學。

## 動手練習

1. **把 v1 改成 v1.5**：Ch 11 的 solver 保留骨架，把 `unit_propagate` 換成 watched literals 版。Smoke test 的 SAT/UNSAT 結論要一致。decisions 一致、time 降至少 5 倍。
2. **印 watch 搬家次數**：在 `std::swap(c->lits[1], c->lits[k])` 後計數。你會看到搬家次數 << propagation 次數 — 多數 propagate 根本沒換 watcher，只是 case 1 跳過。這是 **amortized O(1)** 的實證。
3. **故意做錯**：在 `propagate` 裡忘記 `swap(lits[0], lits[1])` 那步，正確 literal 順序壞了，某些 unit 會被當 conflict。跑 UNSAT test 應該仍 UNSAT（錯的方向），但跑 SAT test 會看到假 UNSAT。這是實作最常見的 bug，印 clause literal 就能抓到。
4. **對照 MiniSat 的 propagate()**：讀一下 `minisat-2.2.0/core/Solver.cc` 的 `Solver::propagate()`，大概 40 行。和你寫的比對，你會發現 MiniSat 用 `OccLists` + watcher metadata 讓 case 1 更快。

## 常見誤解

- **「Watched literals 讓 propagation 演算法不一樣」** — **不一樣是錯誤的說法**。演算法本身還是 unit propagation。變的是**怎麼找到哪些 clause 需要處理**。語義上完全等價。
- **「三個 watched 一定比兩個準確」** — 偵測會更早，但 overhead 更大。兩個是實務最佳點，MiniSat、CaDiCaL、Kissat 全都用兩個。
- **「backtrack 必須 rollback watch list」** — 不用。這就是 watched literals 最漂亮的性質，別加多餘的 restore code。

## 自我檢核

- [ ] 說得出 watched literals 的 invariant
- [ ] 寫得出 `propagate` 的 case 1/2/3 三種狀況
- [ ] 知道為什麼 backtrack 不用 restore watch list
- [ ] 懂 lazy data structure 的哲學
- [ ] 寫得出改造後的 v1.5 solver，跟 v1 結論一致但快 5 倍以上

Unit propagation 的效率問題解決了。下一章進入 **CDCL 的本體** — 當衝突發生時，不只是 backtrack，而是 **分析衝突**、**學一條新 clause**，下次再遇到類似情況能直接避開。這是 SAT solver 從 DPLL 演化到能跑百萬變數的關鍵一步。

→ [Ch 13 — CDCL 核心：implication graph、conflict analysis](./13-cdcl-core.md)
