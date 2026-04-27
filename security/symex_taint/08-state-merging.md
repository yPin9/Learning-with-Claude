# Ch 8 — State merging：降爆炸的代價與時機

> 目標：把 state merging 從「一個模糊的優化」升級到工程判斷題。講完你要知道什麼時候 merge 真的有效、什麼時候反而讓你更慘。

## 為什麼要 merge

回到 Ch 3 的 path explosion。每條 branch 多開一條 state，active state 數指數成長。最簡單的優化之一：**相鄰的 state 如果「幾乎一樣」，合成一條**。

考慮：

```c
if (x > 0) {
    y = 10;
} else {
    y = 20;
}
// 在這裡 state 分成兩條，其他 σ 都一樣，只有 y 不同
z = y + 5;
```

沒 merge：兩條 state，A 的 y=10、PC=(x>0)；B 的 y=20、PC=(x≤0)。後面每個 instruction 都在雙份執行。

Merge 之後：

```
merged state:
  σ = {
    x ↦ α,
    y ↦ ite(α > 0, 10, 20),
    z ↦ ite(α > 0, 10, 20) + 5
  }
  PC = (α > 0) ∨ (α ≤ 0)  ==  true
```

兩條變一條。之後的 instruction 只需要跑一次。

這就是 state merging 的核心：**用 `ite` 把 branch 後的差異合進同一個 state**。

## Merge 什麼、什麼不能 merge

兩個 state 要 merge，必須：

1. **pc 相同**（都停在同一個 instruction）
2. **可 merge 的 components 對應**：register file 的每個 slot、memory 的每個 address，要嘛兩邊一樣、要嘛不同但可以用 `ite` 表達

實際合成的公式：

```
merged.σ[v] = ite(state_A.PC, state_A.σ[v], state_B.σ[v])      # 對每個變數
merged.PC   = state_A.PC ∨ state_B.PC                          # disjunction
```

對 memory：一樣 byte 一樣跳過；不同 byte 用 `ite` 接起來。

## 代價：formula 變大

merge 的好處是 state 數少了，代價是 **每個 variable 的 formula 變深**。

最糟的情況：兩個 state 的 `y` 差很多、merge 之後 `y = ite(PC_A, v_A, v_B)`，後面每次用到 y 都要帶這個 ite。多 merge 幾次：

```
y = ite(c₁, v₁, ite(c₂, v₂, ite(c₃, v₃, v₄)))
```

SMT 對 ite-heavy formula 也不是 free 的。每個 `ite(c, a, b)` 相當於 `(c ∧ y=a) ∨ (¬c ∧ y=b)`，clause 翻倍。

### 實測趨勢

KLEE 論文（2008）的結論：
- **短 branch 後立刻匯合**（diamond 結構）：merge 明顯有利
- **長分歧 branch**（兩邊各走幾千 instruction 才匯合）：merge 經常反而慢，因為累積的 ite 太深

經驗法則：**branch-to-merge distance 超過幾十 instruction，考慮不 merge**。

## 兩種 merge 策略

### 1. Static merge（編譯期決定）

compiler / instrumenter 先分析 CFG，找出 **merge points**（通常是 post-dominator），在那些點強制 merge。

- KLEE 的 `use-merge` 實驗 option
- Veritesting（Avgerinos et al., ICSE 2014）是這派的代表作

優點：可預測、對 diamond pattern 最佳。
缺點：需要 CFG 分析，對 binary target 不一定準。

### 2. Dynamic merge（runtime 決定）

runtime 時每當兩條 state 停在同一個 pc，試著 merge。不 merge 的情況：ite 成本太高、constraint 結構太不同。

- angr 的 `DreamMerger` 策略
- 一般 concolic 工具偏好這個

優點：彈性、可跟搜索策略結合。
缺點：判斷「要不要 merge」本身有 overhead。

## Veritesting：混合手法

Veritesting（CMU，Avgerinos et al.）在 ICSE 2014 提出一個重要觀察：**整段沒有 syscall / external call 的 code 可以先用 static analysis 合成一個 formula，再當作整塊丟進 symex**。

流程：
1. 分析 CFG，找出**靜態區塊**（syscall-free + loop-free 的 basic block 群）
2. 對整塊靜態區塊算出一個 **encoded formula**：表達「從入口進、出口出」的所有路徑的 semantic
3. symex 在遇到這塊時，不展開、直接用這個 formula

這叫 **static symbolic execution** embedded 在 dynamic symex 中。Veritesting 在其 paper 的 benchmark 上有 4× 加速。

為什麼有效：靜態分析對 loop-free / external-free 的 code 是**精確**的 — 不需要 runtime fork。動態 symex 只在真的需要分叉時（syscall、external call、loop）才上場。

後面 KLEE-VST 等工具延續這個思路。

## 什麼情況 merge 特別有效

### 1. Diamond pattern（短分歧）

```
        ┌── if A ──┐
   pc ──┤          ├──> pc'
        └── if !A ─┘
```

A 跟 !A 之間只差幾個 instruction，後面完全相同。**強制 merge、ite 成本低**。

### 2. Loop unrolling 的 tail

```
for (i = 0; i < N; i++) {
    if (cond[i]) a += x;
    else         a += y;
}
// 每個 iteration 分叉、tail 匯合
```

每個 iteration 的 branch 都是短 diamond。對每個 iteration 做 merge，最後 `a` 是一堆巢狀 ite，但 state 數從 2^N 回到 1。

### 3. error handling

```c
int result = do_work();
if (result < 0) {
    log_error();
    return -1;
}
// main path continues
```

error branch 走幾步就 return。不用 merge，可以直接讓 error state 自己 deadend — 這比 merge 更好（error state 走完就沒了，不佔資源）。

## 什麼情況 merge 反而害你

### 1. 長分歧

```c
if (complex_condition) {
    // 走 1000 條 instruction、存 100 次 memory、做了 20 次 function call
    foo();
} else {
    bar();
}
// 這裡 merge?
```

兩條各自產生大量 symbolic memory 寫入。merge 時 memory 的每個 byte 都要 ite，**整個 memory 都變成 ite 樹**。之後任何一次 load 都要穿透這棵樹。

這種 case，**讓兩條 state 各自跑下去**、不要 merge。

### 2. 不同的 external call 結果

```c
int fd = open("/tmp/x", O_RDONLY);
if (fd < 0) { ... }
else        { read(fd, ...); ... }
```

一條走 error、一條走 read。merge 之後的 state 裡 `fd` 是 ite、後續 syscall 要處理兩種 case。syscall model 通常沒寫這個，直接炸掉。

### 3. Constraint 太不同

```
state A: PC_A = (x > 10) ∧ (y = "hello")
state B: PC_B = (x < 0)  ∧ (z % 7 == 3)
```

PC 幾乎完全不相交。merge 之後 PC = PC_A ∨ PC_B — solver 要處理 disjunction，CDCL 要多很多回溯。

## 結論：merge 不是萬靈丹

跟 Ch 3 的 path explosion 武器庫一起看：

| 情境 | 選什麼 |
|------|--------|
| Diamond pattern | Merge |
| Long divergent branches | 不 merge，讓兩條跑 |
| Error handling | 不 merge，讓 error 自己 deadend |
| Loop inside static block | Veritesting-style merge |
| Branch 後馬上 syscall | 不 merge |
| 純 arithmetic、沒 memory write | Merge 通常有效 |

KLEE 預設**不**開 merge（`--use-merge` 是 experimental flag）。這告訴你一件事：state merging 在 generic 用法裡**不是 free lunch**，作者們判斷預設不開比較穩。

## 實務：angr 怎麼用 merge

angr 的 simulation manager 支援手動 merge：

```python
import angr

proj = angr.Project('./target')
state = proj.factory.entry_state()
simgr = proj.factory.simulation_manager(state)

simgr.explore(find=0x400800, num_find=5)
# 假設你找到 5 條 path，要 merge 它們

merged = simgr.found[0].merge(*simgr.found[1:])[0]
print(merged.solver.constraints)
```

或用 `LoopSeer` 技術自動在 loop tail 做 merge。

現實是：大多數 angr 工程師**不主動用 merge**。角色通常是 fuzzer + angr 的 hybrid（Driller），state 爆炸由 fuzzing 那邊的 generation 控制、symex 只拿來 rescue 個別 stuck point。

## 學術前沿：bucketed merging

近年（2020+）的 symex 研究關注 **selective merge**：

- **MergePoint**：在 function return / post-dominator 處 merge，其他地方不 merge
- **Bucket by hash**：給 state 算個 summary hash，相似 hash 才 merge

這些基本思想是**想辦法知道「兩條 state 差有多大」**，太大就不合。一般 production 工具還沒廣泛採用。

## 心法

Merge 是工程判斷：**把 state count 壓低 vs 把 formula 複雜度推高**，兩個壞處之間你選哪個。

很多 symex 新手聽到「state explosion 有個東西叫 state merging」，就想開滿。結果 SMT timeout 更頻繁、整體更慢、又搞不懂為什麼。

**預設不開**。你發現 state 爆但 formula 每個都小、結構一致（diamond、short loop）再開。

## 自我檢核

- [ ] 解釋 merge 的公式：`σ[v] ← ite(PC_A, A[v], B[v])`，`PC ← PC_A ∨ PC_B`
- [ ] 能舉出三種 merge 有效的結構、三種 merge 有害的結構
- [ ] 知道 Veritesting 是 static + dynamic 的混合
- [ ] 理解「KLEE 預設不開 merge」代表的工程訊息

下一章講更本質的難點 — symbolic memory。當 array index 是 symbolic 時，address concretization 跟 fully symbolic memory 的真實取捨。

→ [Ch 9 — Symbolic memory：address concretization vs fully symbolic](./09-symbolic-memory.md)
