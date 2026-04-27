# Ch 15 — VSIDS、phase saving、Luby restart

> 目標：把 CDCL 的骨架（learning + backjumping）配上 **現代 solver 的四條命脈 heuristic** — VSIDS 變數選擇、phase saving、restart 策略、clause deletion（LBD）。這些不影響正確性，但 **它們才是 MiniSat/CaDiCaL 碾壓 DPLL 的真正原因**。

## Branching：挑錯變數是多貴

Ch 11 v1 用「第一個未指派」當 branching heuristic。實際效果？隨機 3-SAT 150 變數會跑 20 秒。

同一個 instance 換成 **好的 heuristic**（VSIDS），0.05 秒。**400 倍**。

**Branching 順序對 SAT 搜索是指數級敏感**。一個爛 heuristic 讓樹形變胖；一個好 heuristic 讓樹頻繁命中 conflict、學到精華 clause。

## 歷史：從 Random 到 VSIDS

| 年代 | Heuristic | 原則 |
|---|---|---|
| 1960s DPLL | 任意 / random | 沒想過 |
| 1990s DLIS | Literal with most unsatisfied clauses | 局部貪心 |
| 1990s MOMS | Maximum Occurrences in Minimum Size | 偏向短 clause |
| 2001 VSIDS | Variable State Independent Decaying Sum | Moskewicz Chaff 原創 |
| 2013+ LRB | Learning Rate Branching | 根據「學到 clause」打分 |
| 2019+ Kissat | VSIDS + target phase + ... | 現代混合 |

DLIS 和 MOMS 的問題：**每次 branching 都要掃 CNF** 算 counter，expensive。VSIDS 的破局點：**用「變數最近在 conflict analysis 中的參與頻率」當 score**，O(1) 查詢。

## VSIDS 機制

**V**ariable **S**tate **I**ndependent **D**ecaying **S**um。每個變數一個浮點 score（稱 activity）。

```
每次 conflict：
    for lit in learned_clause (或 conflict analysis 路徑經過的 lit):
        activity[var(lit)] += inc    # inc 是當前增量
    inc *= (1 / decay_factor)        # 增量本身逐漸變大
```

Decay factor 通常 0.95。**不是直接衰減 activity，是放大新的增量** — 數學上等價但避免浮點 overflow。每隔 N 次 conflict，若 activity 都太大就 rescale。

**Pick branching 變數**：取 **activity 最高的 unassigned 變數**。用 priority queue / heap 維護。

### 為什麼 VSIDS 有效

- **最近參與 conflict 的變數 score 高** → 傾向挑這些變數 → 更可能再碰上 conflict → 學更多 clause
- **Decay** 讓 score 「忘記」很久以前的事 → 跟著當前搜索區域走
- **SAT solver 的自我強化**：發現「難題」區域後反覆挖，直到搞定

**VSIDS 本質是 locality 原則**：最近用過的東西很可能再用。跟 CPU cache 同一個哲學。

### EVSIDS / CHB / LRB

- **EVSIDS (Exponential VSIDS)**：前面講的「放大 inc」版。MiniSat、CaDiCaL 用
- **CHB (Conflict History Based)**：根據 conflict 次數排名，不用浮點。CaDiCaL 可選
- **LRB (Learning Rate Branching)**：每個變數記錄「它被 assign 時有多少後續 conflict」，打分。2016 Liang 提出，某些 instance 上比 VSIDS 強

現代 solver（Kissat）常 **混用** — 依 instance 特性切換。

## Phase Saving

Branching 挑完變數，還要選 phase（true 或 false）。笨的選擇：

- 永遠 true
- Random
- Based on literal count

**Phase saving** (Pipatsrisawat & Darwiche 2007)：**記住每個變數上次被 assign 的 phase，下次 branching 優先用同一個 phase**。

直覺：當搜索來到新區域、被 backjump 回來，變數上次的 phase **曾經是可行的**（或至少沒立刻衝突）— 再試一次很可能繼續可行，尤其在 SAT instance。

```
struct Var {
    ...
    bool saved_phase = false;  // 預設 false（MiniSat 傳統）
};

Lit pick_branch() {
    int v = variable_heap.top();   // VSIDS
    return (vars[v].saved_phase) ? v : -v;
}

// Backtrack 時
void unassign(int v) {
    vars[v].saved_phase = (vars[v].value == Value::True);
    vars[v].value = Value::Unassigned;
}
```

**簡單到有點不可思議**，但對結構化 instance 有 2–5× 加速。現代 solver 全開。

### Target Phase（Kissat 2019+）

更新的變體：**除了 saved phase 之外**，solver 記一個 "best" phase（最長滿足 trail 的那個 phase），周期性切換使用。某些 hard instance 能再加速。

## Restart：為什麼要「放棄」

Restart = **把 assignment 全部回到 level 0，但保留 learned clause 和 VSIDS activity**。

聽起來是浪費 — 剛搜到一半扔掉？但：

- **Learned clause 是搜索的真實收穫**，它們永久保留
- **VSIDS activity** 反映了學到的「重要變數」情報
- **當前 assignment** 可能卡在爛的搜索區域
- Restart 把變數順序重洗（VSIDS 挑最新 hot 的）但保留情報

**Heavy-tailed behavior**：SAT 搜索時間分布是重尾的 — 大部分時候快、偶爾非常慢（陷入爛區域）。Restart 是對抗重尾的標準招式。

## Restart Policy

幾種策略：

### Geometric

```
restart_limit[i] = initial_limit * factor^i
e.g., 100, 150, 225, 337, ...
```

指數增長。舊版 MiniSat 預設。

### Luby Sequence

**Luby 1993**：定義序列 `1, 1, 2, 1, 1, 2, 4, 1, 1, 2, 1, 1, 2, 4, 8, ...`

```
luby(i):
    if i == 2^k - 1:
        return 2^(k-1)
    if 2^(k-1) <= i < 2^k - 1:
        return luby(i - 2^(k-1) + 1)
```

**Luby 序列有 O(1) 競爭比**（證明出來的最佳序列）— 對於未知分布，這是數學上最優的 restart schedule。MiniSat 2.2+、CaDiCaL 預設用 Luby。

Restart threshold = `Luby(i) * unit`（unit 通常 512 個 conflicts）。

### Glucose Restart

**Audemard & Simon 2009**（Glucose）的動態策略：**當 recent learned clause 的 LBD 品質變差，就 restart**。基於「學的 clause 差 = 搜索區域爛 = 該換地方」。

現代 solver 混用：Luby 保基線、Glucose 動態觸發。

## LBD 與 Clause Deletion

Learned clause 會無限增長 — 需要清。但清掉哪些？

**LBD (Literal Block Distance)** — Glucose 2009：**learned clause 中不同 decision level 的數量**。

- LBD = 1：clause 的所有 literal 在同一 level → 非常強（下次碰到 propagate）
- LBD = 2：兩個 level → 通常 unit 在上一 level
- LBD 大：clause 橫跨多 level，一般不強

**Clause deletion**：LBD ≤ threshold（通常 2–6）的 clause **永久保留**，其他定期清一半（按 LBD 由大到小砍）。

MiniSat 舊版用 **clause activity**（類似 VSIDS、每次使用 +1、定期衰減）。Glucose 的 LBD 打爆活動分數，現在是主流。

## Reduce 時機

- 每 N 次 restart 觸發一次 `reduceDB()`
- 保留 LBD ≤ k 的所有 clause
- 其餘按 LBD 排序、丟後一半

CaDiCaL 的 `reduceDB` 一次砍 70%，只留「核心」clause。

## 這四個 heuristic 結合起來

```
CDCL Loop with heuristics:
    while true:
        propagate()
        if conflict:
            if level == 0: return UNSAT
            (learned, bj_level, lbd) = analyze_with_minimization()
            bump_activity(vars in learned)
            decay_activity()
            update_lbd_stats(lbd)
            add_clause(learned)
            backtrack_to(bj_level)
            if restart_triggered(): restart()
            if time_to_reduce(): reduceDB()
        elif all assigned:
            return SAT
        else:
            v = vsids_top_unassigned()
            phase = saved_phase[v]
            level++
            assign(v, phase)
```

這就是 **現代 CDCL solver 的主循環**。MiniSat 這部分大約 80 行、CaDiCaL ~200 行（含更多優化）。你的 v2 會照做。

## 對 solver 效能的組合拳

同一個 benchmark（uf150 3-SAT 100 題）：

| 配置 | 平均 solve time | 相對 |
|---|---|---|
| v1（純 DPLL） | 10 s | 1× |
| + watched literals（Ch 12） | 0.8 s | 12× |
| + CDCL learning（Ch 13–14） | 0.03 s | 330× |
| + VSIDS | 0.008 s | 1250× |
| + phase saving + Luby | 0.005 s | 2000× |
| + LBD + clause deletion | 0.004 s | 2500× |

**每一個 heuristic 都是乘法加成**。這就是為什麼 SAT solver 能在過去 20 年打敗 Moore's Law — 不是硬體快，是軟體變聰明。

## 動手練習

1. **在 v2 (Ch 16) 實作 VSIDS heap**：binary heap + decrease-key。網上有 MiniSat 的 IntMap+Heap 可參考。測量 heap 操作佔 solve time 的比例（通常 < 5%）。
2. **Phase saving vs Random phase**：對一組 SAT instance 分別跑兩種策略，看 solve time 差幾倍。通常 phase saving 贏 2–5×。
3. **Luby 印出來**：寫 `luby(i)` 遞迴實作，印前 32 項、畫圖觀察。你會看到它自相似的碎形結構。
4. **關掉 restart**：Ch 16 寫完後，把 restart 停用，跑 benchmark 對比。某些題目差距 10×。

## 常見誤解

- **「VSIDS 的 decay factor 越小（衰減越快）越好」** — 不對。太快會失去 locality，太慢會卡在過去。MiniSat 預設 0.95，Glucose 0.999（慢衰減），CaDiCaL 自適應。沒有標準答案。
- **「Restart 會浪費學到的東西」** — 不會。Learned clause + VSIDS 都留著。Restart 只動 trail。
- **「LBD 一定好過 activity-based deletion」** — 對大部分 instance 是，但某些 crypto / hardware verif instance 上 MiniSat 式的 activity 更好。現代 solver 常 **雙軌**。
- **「Heuristic 可以分別獨立優化」** — 不對。四個 heuristic 相互耦合 — 改 VSIDS decay 會影響哪些 clause 被 learn → 影響 LBD → 影響 deletion。SAT competition 的參數是整套 tuning 出來的。

## 自我檢核

- [ ] 說得出 VSIDS 全名，懂為什麼它 O(1) 比 DLIS 快
- [ ] 寫得出 activity += inc、inc *= 1/decay 的更新
- [ ] 懂 phase saving 的想法（記住上次 phase）
- [ ] 會計算 Luby sequence 前 8 項
- [ ] 懂 LBD 的定義跟為什麼它是 clause 品質指標
- [ ] 理解 restart / clause deletion 為什麼 **保留 learning 的情報但扔當前 trail**
- [ ] 看到 MiniSat 的 pickBranchLit / reduceDB / restart 時能讀懂

骨架全有了、heuristic 全有了。下一章把它們全部組合起來 — **實作 v2 solver**。完成後你有的東西：比 v1 快 2000 倍、能跟 MiniSat 在 100 變數 benchmark 打平、能跑到 500 變數 real-world instance。

→ [Ch 16 — 實作 SAT solver v2：mini-CDCL](./16-implement-cdcl.md)
