# Ch 20 — 迴圈識別：Natural Loop 與 Loop Nesting Forest

> 目標：定義 natural loop 和 back edge，掌握 reducible CFG 的概念，理解 loop nesting forest 的結構及其在 LLVM LoopInfo 中的表示。

## 為什麼要識別迴圈

迴圈是程式中運行時間最集中的地方（阿姆達爾定律）。大多數程式 90% 的時間在 10% 的代碼（通常是迴圈）裡。

迴圈優化（LICM、unrolling、vectorization）的前提是：正確識別哪些指令在迴圈內、哪些在迴圈外、迴圈的邊界在哪裡。

## Back Edge 和 Natural Loop

**Back edge**：CFG 中，從後代節點指向祖先節點的邊。

形式地說：邊 `(n, d)` 是 back edge，當且僅當 `d dom n`（d 在支配者樹中是 n 的祖先）。

每條 back edge 定義一個 **natural loop**：

```
對 back edge (n, d)：
  d 是迴圈的 header（入口點）
  迴圈的節點集合 = {所有能到達 n 的節點，且 d 在其到 n 的路徑上} ∪ {d}
  等價地：從 n 出發反向遍歷 CFG，能到達的節點（不越過 d），加上 d
```

直覺：header `d` 支配迴圈所有節點，back edge `(n, d)` 是「回頭的邊」（n 是迴圈的 latch）。

```
例：
  1 (entry)
  |
  2  ← header（被 3 指向，形成 back edge (3, 2)）
  |
  3  ← latch（back edge 的起點）
  |
  4 (exit)

Natural loop = {2, 3}
```

## 識別 Back Edge 的算法

利用 DFS 的訪問狀態：

```
節點有三種顏色：
  WHITE：未訪問
  GRAY：在當前 DFS 路徑上（DFS 棧中）
  BLACK：已完全訪問

DFS 中，當遇到一條邊 (u, v) 且 v 是 GRAY：
  → (u, v) 是 back edge（v 在當前路徑上，是 u 的祖先）
```

等價地，在 DFS spanning tree 中，back edge 就是指向祖先的邊（區分於 forward edge 指後代、cross edge 指已完成的子樹）。

## 計算 Natural Loop 的節點集合

從 back edge `(n, d)` 計算 natural loop：

```python
def natural_loop(n, d, cfg):
    loop = {d}
    worklist = [n]
    while worklist:
        m = worklist.pop()
        if m not in loop:
            loop.add(m)
            worklist.extend(cfg.predecessors(m))
    return loop
```

從 `n` 開始，往前驅方向遍歷，直到遇到 `d`（不越過它）。

## Loop Nesting Forest

當有多個嵌套迴圈時，它們形成一個**迴圈嵌套森林（Loop Nesting Forest）**：

```c
for (int i = 0; i < n; i++) {      // 外迴圈
    for (int j = 0; j < m; j++) {  // 內迴圈（嵌套在外迴圈中）
        // ...
    }
}
```

- 內迴圈的節點集合 ⊆ 外迴圈的節點集合
- 或者兩個迴圈完全不相交（兄弟迴圈）

Loop nesting forest 的性質：

```
根節點：沒有被任何其他迴圈包含的迴圈
子節點：直接嵌套的迴圈（innermost 是葉子）
```

LLVM 的 `LoopInfo` 就是這個結構的 C++ 表示。

## Reducible vs Irreducible CFG

**Reducible CFG**：所有 back edge 都是 natural loop 的 back edge（即：所有回頭的邊都指向支配者）。

大多數高級語言生成的 CFG 都是 reducible 的，因為 `for/while/do-while` 只產生 natural loop。

**Irreducible CFG**：存在不指向支配者的回頭邊——這種邊形成「irreducible loop」，沒有唯一的 header。

```
Irreducible 例子（兩個節點互相跳轉）：
  A → B → A（A 和 B 互相是對方的前驅，但都不支配對方）
```

C 的 `goto` 可以產生 irreducible CFG。LLVM 可以處理 irreducible CFG，但某些優化（如 LICM）在 irreducible loop 上會保守地不做。

## LLVM LoopInfo

```cpp
#include "llvm/Analysis/LoopInfo.h"

LoopInfo &LI = FAM.getResult<LoopAnalysis>(F);

// 遍歷頂層迴圈
for (Loop *L : LI) {
    errs() << "Top-level loop header: " << L->getHeader()->getName() << "\n";
    errs() << "Loop depth: " << L->getLoopDepth() << "\n";
    
    // 迴圈的所有基本塊
    for (BasicBlock *BB : L->blocks())
        errs() << "  Block: " << BB->getName() << "\n";
    
    // 子迴圈（嵌套）
    for (Loop *SubLoop : *L)
        errs() << "  Sub-loop: " << SubLoop->getHeader()->getName() << "\n";
}

// 查詢某個基本塊屬於哪個迴圈
Loop *L = LI.getLoopFor(SomeBlock);
if (L) {
    errs() << "Block is in loop at depth " << L->getLoopDepth() << "\n";
}
```

### LoopInfo 的常用 API

```cpp
// 迴圈結構
L->getHeader()              // 迴圈 header（唯一入口）
L->getLoopPreheader()       // preheader（如果存在，是 header 的唯一前驅）
L->getExitBlocks(SmallVectorImpl<BasicBlock*>&)  // 退出的基本塊
L->getExitingBlocks(...)    // 有出迴圈邊的基本塊

// 迴圈性質
L->isLoopSimplifyForm()     // 是否已是 simplified 形式（有 preheader、單個 latch）
L->isLoopInvariant(Value*)  // 某個值是否是迴圈不變的
```

### Loop Simplified Form

LLVM 的很多迴圈優化要求迴圈先轉成 simplified form：

```
1. 有唯一的 preheader（迴圈 header 的唯一前驅，且只有一條邊進入 header）
2. 有唯一的 latch（唯一的有 back edge 的基本塊）
3. 所有出迴圈的邊都有 dedicated exit block（迴圈外的基本塊，只從迴圈進入）
```

```bash
# 轉換成 simplified form
opt -passes="loop-simplify" /tmp/loop.ll -o /tmp/loop_simplified.ll
```

## 動手：觀察 LoopInfo

```bash
cat > /tmp/nested_loop.c << 'EOF'
int f(int n, int m) {
    int s = 0;
    for (int i = 0; i < n; i++) {
        for (int j = 0; j < m; j++) {
            s += i * j;
        }
    }
    return s;
}
EOF

clang -O0 -S -emit-llvm /tmp/nested_loop.c -o /tmp/nested_loop.ll
opt -passes="print<loops>" /tmp/nested_loop.ll -o /dev/null 2>&1
```

輸出顯示迴圈的嵌套結構、header、latch。

## 自我檢核

- [ ] Back edge 的定義：`(n, d)` 是 back edge iff `d dom n`
- [ ] Natural loop：back edge 確定一個 header + 節點集合（反向 BFS）
- [ ] Loop Nesting Forest：嵌套包含關係形成樹
- [ ] Reducible CFG：所有回頭邊都是 natural loop back edge
- [ ] `L->getLoopPreheader()` 和 `L->isLoopSimplifyForm()` 的用途

→ [Ch 21 Loop-Closed SSA Form](./21-loop-closed-ssa.md)
