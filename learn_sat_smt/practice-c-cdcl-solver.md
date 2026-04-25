# 練習 C — CDCL + two watched literals

> 目標：寫一個完整 **mini-CDCL solver**：two watched literals、1UIP conflict analysis、VSIDS branching、phase saving、Luby restart、LBD clause deletion。這是 Ch 16 的 code 整理成練習版、配測試 benchmark。做完你有真正的 CDCL 能力，看 MiniSat 能對照每一段。

## 任務規格

| 項目 | 規格 |
|---|---|
| 輸入 | DIMACS CNF |
| 輸出 | SAT competition 格式 (`s` + `v`) |
| Propagation | Two watched literals |
| Conflict analysis | 1UIP 停止條件 |
| Branching | VSIDS heap |
| Phase selection | Phase saving |
| Restart | Luby sequence, unit = 512 conflicts |
| Clause deletion | 每 2000 conflicts reduce，保留 LBD ≤ 2 |
| Proof output | DRAT (選配) |
| 效能 | 200 變數 random 3-SAT ≤ 5 秒；`uf250` ≤ 60 秒 |
| 驗證 | 至少 50 個 instance 跟 MiniSat 結論一致 |

## 跟 Ch 16 v2 的差別

Ch 16 是**教學版完整 code**。這個練習是**你自己從零寫一次**，用 Ch 16 當參考但不要直接抄。親手踩坑才學得到。

強烈建議的自我約束：

1. **先把 Ch 16 讀懂**，關掉不看
2. **寫 solver.hpp 前先畫架構圖**（trail、watch list、heap、reason clause 怎麼連）
3. **每個 function 寫完立刻小題測**，不要一口氣寫完才跑
4. **遇到 bug 先猜是哪個 invariant 被破壞**，再 print assert 證明

## 實作步驟建議

### Step 1：資料結構

跟 Ch 16 相同。`Var` 含 `value / level / reason / saved_phase / activity`、`Clause` 含 `lits / learned / lbd`、`Watcher` 含 `clause / blocker`。

```cpp
struct Var { Value value; int level; Clause* reason; bool saved_phase; double activity; };
struct Clause { std::vector<Lit> lits; bool learned; int lbd; };
struct Watcher { Clause* clause; Lit blocker; };
```

### Step 2：Propagate

按 Ch 12 的 3 個 case 寫。**關鍵 invariant**：

```
assert(c->lits.size() >= 2);
assert(c->lits[0] != -p || c->lits[1] != -p);  // 不能兩個 watcher 都指向剛變 false 的
// propagate 後：
assert(lit_val(c->lits[0]) != Value::False || lit_val(c->lits[1]) != Value::False);
```

Debug build 把這些 assertion 開，catch 掉 90% 的實作 bug。

### Step 3：Conflict Analysis (1UIP)

Ch 14 的演算法 + Ch 16 的 code。寫完立刻測：

```cpp
// 小測試：手寫 3 個 decision level、propagate 到 conflict、手算 1UIP
// 對比 analyze() 輸出的 learned clause 和 backjump level
```

### Step 4：VSIDS + Heap

Ch 16 的 binary heap。**記得** backtrack 時 reinsert。**記得** rescale activity 避免 overflow。

### Step 5：Main Loop

```cpp
bool solve() {
    while (true) {
        conflict = propagate();
        if (conflict) {
            if (level == 0) return UNSAT;
            learn + backjump
            decay_activity
            if (restart_due) backtrack_to(0)
            if (reduce_due) reduce_db()
        } else {
            if (all_assigned) return SAT;
            branch = pick_vsids()
            new_level + enqueue(branch)
        }
    }
}
```

### Step 6：Test Harness

```bash
#!/bin/bash
# run-bench.sh
BENCH_DIR=$1
FAIL=0
for f in "$BENCH_DIR"/*.cnf; do
    ours=$(./sat-mini "$f" 2>/dev/null | grep "^s " | awk '{print $2}')
    theirs=$(minisat "$f" 2>/dev/null | tail -1)
    if [[ "$ours" != "$theirs" ]]; then
        echo "MISMATCH $f  ours=$ours theirs=$theirs"
        FAIL=1
    fi
done
exit $FAIL
```

跑 `uf100`、`uf125`、`uf150`、`uf200`、`uf250`。

## 期望輸出範例

```
$ ./sat-mini uf200-01.cnf
c variables: 200, clauses: 860
c conflicts: 1843, decisions: 2100, propagations: 187345
c restarts: 3, reduce_db: 0, time: 0.8s
s SATISFIABLE
v 1 -2 ... 0
```

## 完整參考實作

**寫完再看**。Ch 16 的 code 是完整參考，不另外再貼了。你寫完的版本應該跟 Ch 16 語意等價，細節可能差一些（好壞都 OK，重點是 correct）。

<details>
<summary>參考：Ch 16 的哪幾段對應這個練習的哪個步驟</summary>

| 步驟 | Ch 16 對應段落 |
|---|---|
| Types | 「src/types.hpp — 升級版」 |
| Heap | 「src/heap.hpp」 |
| add_clause | solver.hpp 段 2 |
| propagate | solver.hpp 段 2 後半 |
| analyze | solver.hpp 段 3 「analyze」 |
| backtrack / pick_branch | solver.hpp 段 3 中段 |
| solve main loop | solver.hpp 段 3 結尾 |
| luby + reduce_db | solver.hpp 段 3 最後 |

**不要逐字抄**。讀懂、自己寫、對照。

</details>

## 測試 Benchmark

最小集合（先過這批）：

- SATLIB `uf20-*` 1000 題（應該全部 ≤ 0.01s）
- SATLIB `uf50-218` 1000 題（應該 ≤ 0.1s）
- SATLIB `uuf50-218` (UNSAT) 1000 題（≤ 0.1s）

中級：

- `uf100-430` 1000 題（≤ 1s 平均）
- `uuf100-430` 1000 題（≤ 5s）

進階：

- `uf250-1065` 100 題（≤ 30s）
- `uuf250-1065` 100 題（≤ 60s）

**都要跟 MiniSat 結論一致**。MiniSat 比你快 2–10× 是正常的（它有 preprocess、Ch 17），結論絕對不能差。

## 效能調校

完成基本版後，試這些加速：

1. **Blocker optimization**：Watcher 存 blocker literal 以及 clause，propagate 時先檢查 blocker，多 20% 速度。
2. **Clause 緊湊配置**：`std::vector<Clause>` + raw index，避免 `new/delete` 碎片化。
3. **Variable activity decay 正確**：`var_inc *= 1/decay` 而不是衰減所有 activity，避免 O(n) 每 conflict。
4. **Trail queue 壓縮**：Trail 只存 lit，不存 reason（reason 存 `Var::reason` 裡）。
5. **Phase saving 在 backtrack 時設**：確保每變數的 `saved_phase` 在 unassign 時寫入當時的 value。

做完這些你的 solver 應該接近 MiniSat 0.4 的原始效能（200 變數幾秒）。

## Bonus 挑戰

1. **DRAT proof 輸出**：加 `--drat=file` flag。用 drat-trim 驗證所有 UNSAT 的 proof。
2. **Preprocessing**：加簡化版 — pure literal + binary subsumption。200 變數 instance 應加速 2–3×。
3. **Glucose restart**：加 LBD 變差觸發 restart（Ch 15）。某些 instance 勝 Luby。
4. **Clause vivification**：每 10000 conflicts 做一輪 vivify（Ch 18）。
5. **Statistics dump**：輸出 `conflicts/sec`、`propagations/conflict`、`avg learned LBD`、`reduce db` 等，寫個 gnuplot script 畫圖。

## Debug 工具

CDCL 的 bug 極難 debug，兩個核心工具：

### 1. Assertion-rich debug build

Debug CMake flag `-DCMAKE_BUILD_TYPE=Debug -fsanitize=undefined,address`，每個 function 入口出口放 invariant assert。**一個正確 CDCL 到處都該是 assertion**。

### 2. DRAT self-check

Solver 輸出 DRAT、用 drat-trim 驗證。如果你的 UNSAT 結論 drat-trim 說「INVALID」，表示 learned clause 不能從 CNF RUP 推出 → analyze 有 bug。

### 3. 跟 MiniSat 對 trace

在 `pick_branch` 和 `enqueue` 印 log：

```
d 1: +5
p 1: +3 (reason=c#7)
p 1: -1 (reason=c#2)
d 2: -2
...
```

拿你的 log 跟 MiniSat 對同一 instance 的 log 比。前幾步相似、某步分歧 → 找出你比 MiniSat 少做了什麼。**最強 debug**。

## 自我檢核

- [ ] 能在不看 Ch 16 的情況下寫出 propagate / analyze / solve 三個核心 function
- [ ] 跑 SATLIB 全部 `uf100`、`uf125`、`uf150`、`uf200` 結論跟 MiniSat 一致
- [ ] 實作了 two watched literals + blocker
- [ ] 實作了 1UIP + clause minimization（至少 basic）
- [ ] VSIDS heap + activity rescale
- [ ] Phase saving、Luby restart、LBD clause deletion
- [ ] 能印 conflicts/sec、propagations/conflict、avg LBD
- [ ] 200 變數 instance 效能 < MiniSat × 10
- [ ] DRAT proof 輸出能過 drat-trim（bonus）

**這個練習完成 = 你寫的 CDCL solver 能跑工業 benchmark**。Part 1 結束。接下來 Part 2 完全轉換視角 — 從「只管 boolean」升級到「有理論語意」的 SMT。

→ [Ch 22 — SMT 全貌與 SMT-LIB v2](./22-smt-overview.md)
