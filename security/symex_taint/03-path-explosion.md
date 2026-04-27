# Ch 3 — 路徑爆炸這個病

> 目標：把 path explosion 從直覺的「branch 很多」升級到工程層級的理解。看完你要能對任何 symex target 預估它的 path 數量級，並知道該派哪種策略去救。

## 一個簡單例子爆給你看

```c
void f(int buf[32]) {
    for (int i = 0; i < 32; i++) {
        if (buf[i] & 1) {
            acc += buf[i];
        }
    }
}
```

看起來很簡單。但每個 iteration 都有一個 branch（`buf[i] & 1` 真 / 假），32 個 iteration =
```
2^32 ≈ 4.3 billion paths
```

每條 path 可能要走幾百 instruction、每次 branch 做 SMT call。算 SMT 一次 1ms、一條 path 100 個 branch，總計：

```
4.3 × 10^9 paths × 100 branches × 1 ms
  = 4.3 × 10^14 ms
  = 13000 年
```

**十三千年**。你用 4096 核 cluster 跑也要 3 年。

這是 path explosion 的第一堂課：**branch 的乘積不是線性，是指數**。

## Path explosion 的四個來源

真實程式的 path 爆炸不只來自 branch，有四個獨立來源，而且會互相乘：

### 1. Loop

上面例子就是 loop。如果 loop bound 是 symbolic 的，那更慘 — 原則上是**無窮** path。

實務上工具用 **loop bound**：超過 N 次就 bail out（KLEE 預設、angr 也是）。代價是可能錯過需要更多次 iteration 才能觸發的 bug。

### 2. 資料結構走訪

```c
Node* p = head;
while (p) {
    if (p->flag) do_something();
    p = p->next;
}
```

linked list 長度 N → 2^N path。symex engine 不知道 N 多長，也不知道 `p->next` 什麼時候 null — 全部當 symbolic。

### 3. Parser

```c
while (*input) {
    switch (*input) {
        case 'a': ...; break;
        case 'b': ...; break;
        case 'c': ...; break;
        case 'd': ...; break;
        default:  return ERR;
    }
    input++;
}
```

每個 byte 有 5 種可能（4 個 case + default），input 長度 L → 5^L path。JSON parser、state machine、任何 byte-by-byte 處理的東西都是這種 profile。

### 4. External interaction

每次 syscall / library call 可能回傳多種 result：

```c
ssize_t n = read(fd, buf, 1024);
if (n < 0) error();
else if (n == 0) eof();
else if (n < 512) partial();
else complete();
```

讀一次就有 4 條後續 path。read 十次就 4^10 = 100 萬。

## 真實案例的數量級

幾個 benchmark 參考：

| Target | Path 數量 (觀察上界) | 備註 |
|--------|--------------------|------|
| CTF crackme (check 10 byte) | 10^2 ~ 10^4 | symex 的甜蜜區 |
| `cat` | 10^4 ~ 10^5 | KLEE 第一篇論文示範 |
| `gzip` | 10^6+ | KLEE 論文跑不完整 |
| OpenSSL ASN.1 parser | 10^10+ | 純 symex 投降 |
| 一個 Chrome renderer | 10^100+ | 不可能 |

**10^6 是 pure symex 的實用上限**。超過就一定要配 state merging、concolic、或直接放棄改 fuzzing。

## 五個工程武器

路徑爆炸你殺不死，你只能選適當的武器把它壓在可控範圍。

### 武器 1：Loop bounding / unrolling limit

最粗暴也最有效。

```
--max-loop-iterations = 16
```

超過就丟棄該 path。代價：錯過 high iteration bug。實務上多數 bug 在前 10 次 iteration 就會出來，這個取捨值得。

**何時用**：永遠。預設就開。

### 武器 2：State merging

Ch 8 專章講。核心：兩條 state 走過不同 branch 後，如果 `pc_next` 相同，就把它們合併成一條，PC 變成 `cond_A ∨ cond_B`、不同變數值用 `ite` 接起來。

**何時用**：branch 後很快匯合的程式結構（diamond pattern）。

**代價**：formula 複雜度大幅上升，SMT call 變慢。有時反而更慢。

### 武器 3：Concolic execution

Ch 6 專章講。核心：只跑一條 concrete path，同時 trace symbolic；下次翻轉 PC 裡某個 clause 重跑。

**何時用**：總 path 數可承受（< 10^6）、你有 concrete seed input。

**優點**：SMT 壓力小、環境互動用 concrete 跳過。

### 武器 4：Path prioritization (search heuristic)

不要 BFS/DFS 所有 state，**挑**有價值的探索：

- **Coverage-guided**：優先走沒走過的 block（angr `ExplorationTechnique.DFS`、KLEE `--search=random-path`）
- **Depth-limited**：不走太深
- **Distance to target**：往目標 basic block 靠近（Driller、BORG）

**何時用**：你有明確目標（「找一個走到 L_bug 的 input」），不需要全覆蓋。

### 武器 5：Compositional / summary

對 function f 算一次 **summary**：`f: pre(args) → post(args, ret)`；以後呼叫 f 不再展開，直接用 summary。

**何時用**：function 結構清楚、可重用的 library。

**代價**：summary 本身就要算 symex 跑出來、不適合 heap-heavy code。角度很難做對。

## 直覺 rule of thumb

粗估一個 target 的 path 數量：

```
paths ≈ branches_per_input_byte ^ input_byte_count
         × 2 ^ loop_iterations
         × external_call_branches
```

例：16 byte input、5 個 switch case、無 loop、無外部 call：
```
5^16 ≈ 1.5 × 10^11
```
**放棄 pure symex，用 concolic + coverage heuristic**。

例：4 byte input、純 arithmetic、無 branch、無 loop：
```
paths = 1
```
**symex 秒解**。這種是 CTF baby 題。

## Path explosion 的反面：Under-approximation

Symex 追求 path 爆多的時候，通常想要 **sound（不漏報）**。但如果你放棄 sound，反而能用爆多 path 換到**精度**。

- **Fuzzing**：明確放棄 sound — 只跑隨機 input。
- **Concolic**：半 sound — 真的跑過某條 path 一定存在，但沒跑到的 path 不保證不存在 bug。
- **UCSE（Ch 26）**：從函式中間開始 symex、忽略 precondition — 可能產生 false positive，但能找到 deep bugs。

**知道自己在用 sound 還是 unsound 的工具**是成熟 symex 工程師的分水嶺。KLEE 是 sound（模 loop bound 之下）、angr 是 sound（模 environment model 之下）、Driller 是 unsound（fuzzing 一半）。

## State 數量不等於 path 數量

要澄清一個常見混淆：

- **Path**：執行路徑（從 entry 到某個 exit 點的一條走法）
- **State**：symex engine 當下 active 的 state 物件數

state 數量隨時都可能比 path 少（state 完成了就退場）或比 path 多（一個 path 到半途 fork 成兩個）。工具的 memory 爆掉通常是 **state 數**，不是 path 數：

```
active_states × size_per_state = RAM 消耗
```

angr 的 SimState 一個大約 1–10 MB（symbolic memory page、constraint set 都很貴）。**10000 個 active state 就 10 GB RAM**。你真實的 path 可能幾百萬條，但 engine 不會同時擁有它們 — 只要 worklist 長度控制在幾百、幾千就有辦法。

這也是為什麼 state merging 這個武器常見 — 它主要是**降 active state 數**，不是降 total path。

## 快速診斷：看到 OOM / 卡住該想什麼

有人跑 symex 結果 OOM 或 24 小時沒進展。你的診斷 checklist：

1. **有 loop 嗎？bound 是多少？** 不加 bound 直接 infinite loop
2. **input 長度幾位元組？** 超過 32 就要小心
3. **有 external syscall / library 嗎？** 每個 unmodeled call 都在爆 path
4. **state count 怎麼變化？** 穩定增長就是有新 branch，爆炸成長就是 loop
5. **SMT query 多慢？** >100ms 表示 formula 太複雜，考慮 concretization
6. **有 `unconstrained` state 嗎？** angr 特有 — PC 本身變 symbolic 代表 target 跑飛了

這些 metric 都可以從工具拿出來：
- angr: `simgr.active`, `simgr.deadended`, `state.history`
- KLEE: `--stats`、`klee-stats` CLI

## 心法

Path explosion 不是 bug，是 symex 的 **物理常數**。

你的工作不是消滅它，是**選擇性壓縮**：
- 你在乎什麼 path → 保留
- 你不在乎什麼 path → 砍掉

這個選擇**幾乎永遠是工程判斷、不是理論決定**。同一個 target 下，KLEE 的 `--search=random-path`、`--search=bfs`、`--search=dfs` 會給你截然不同的結果。你要做的是**知道哪個 heuristic 適合哪種 target**，而不是期待有一鍵最佳解。

## 自我檢核

- [ ] 能列出 path explosion 的四個獨立來源並各舉一例
- [ ] 能粗估一個 C function 的 path 數量級
- [ ] 講得出五個對付 path explosion 的工程武器、各自代價
- [ ] 區分 path 數、active state 數、SMT query 數
- [ ] 看到 symex OOM / 卡住時能做基本診斷

下一章進到 symex state 的內部 — concrete vs symbolic value 的真正區別、memory model 怎麼設計、為什麼 symbolic memory 是 symex 最髒的部分。

→ [Ch 4 — Concrete vs symbolic value 與 memory model](./04-values-and-memory.md)
