# Ch 27 — Call Graph 與 SCC

> 目標：理解 call graph 的構造與限制，掌握 Tarjan SCC 算法，以及 bottom-up SCC 順序在過程間優化中的作用。

## 什麼是 Call Graph

**Call Graph（呼叫圖）**：程式中所有函式和它們之間的呼叫關係。

- 節點：每個函式
- 邊：`f → g` 表示 f 可能呼叫 g

```c
void leaf() { /* ... */ }

void middle() {
    leaf();    // middle → leaf
}

void root() {
    middle();  // root → middle
    leaf();    // root → leaf
}
```

Call graph：

```
root → middle → leaf
root → leaf
```

## Call Graph 的限制：間接呼叫

靜態 call graph 無法精確處理**間接呼叫（indirect call）**：

```c
void (*fp)(int) = get_function_pointer();
fp(42);   // 呼叫哪個函式？靜態分析不確定
```

保守處理：把所有「簽名匹配的函式」都加入 call graph，產生大量誤報。

更精確：使用指向分析（Points-to analysis）確定 `fp` 可能指向哪些函式。LLVM 的 `CallGraph` 對間接呼叫保守處理（標記為「呼叫外部節點」）。

## Strongly Connected Components（SCC）

**SCC（強連通分量）**：有向圖中，任意兩個節點互相可達的最大子集。

Call graph 中的 SCC：互相遞迴呼叫的函式群。

```c
void even(int n);
void odd(int n);

void even(int n) { if (n != 0) odd(n - 1); }   // even → odd
void odd(int n)  { if (n != 0) even(n - 1); }  // odd → even
// even 和 odd 形成一個 SCC
```

**SCC DAG**：把每個 SCC 壓縮成一個節點，SCC 之間的邊不形成環（否則這些 SCC 應該合成一個）。SCC DAG 是有向無環圖（DAG）。

```
SCC1 = {even, odd}
SCC2 = {leaf}
SCC3 = {root}

SCC DAG：SCC3 → SCC1 → SCC2
```

## Tarjan SCC 算法

Tarjan 算法在一次 DFS 中找出所有 SCC，時間複雜度 O(V + E)。

核心思想：維護 DFS 棧和每個節點的 `lowlink`（從它出發能到達的最早 DFS 序號）。

```python
index_counter = 0
stack = []
lowlinks = {}
index = {}
on_stack = {}
sccs = []

def strongconnect(v):
    global index_counter
    index[v] = lowlinks[v] = index_counter
    index_counter += 1
    stack.append(v)
    on_stack[v] = True
    
    for w in successors(v):
        if w not in index:              # 未訪問
            strongconnect(w)
            lowlinks[v] = min(lowlinks[v], lowlinks[w])
        elif on_stack[w]:               # 在棧上：是 back edge 或 cross edge 到同 SCC
            lowlinks[v] = min(lowlinks[v], index[w])
    
    if lowlinks[v] == index[v]:         # v 是 SCC 的根
        scc = []
        while True:
            w = stack.pop()
            on_stack[w] = False
            scc.append(w)
            if w == v:
                break
        sccs.append(scc)
```

當 `lowlinks[v] == index[v]`，說明 v 是某個 SCC 的根（沒有更早的節點從它的 SCC 出發能到達）。

## Bottom-Up SCC 順序

SCC DAG 是 DAG，可以做拓撲排序。**Bottom-up SCC 順序**：

> 在 SCC DAG 的拓撲排序中，先處理沒有後繼（或後繼已處理）的 SCC。

等價地：先處理「葉子函式」，再處理「呼叫者」。

```
SCC2（{leaf}）先處理
SCC1（{even, odd}）次之
SCC3（{root}）最後
```

## 為什麼過程間優化要用 Bottom-Up 順序

**函式摘要（Function Summary）**：過程間分析通常為每個函式計算一個「摘要」，說明它對外的行為（比如「只讀第一個參數，返回值是第一個參數的常數倍」）。

Bottom-up 確保：計算 `root` 的摘要時，`leaf` 的摘要已計算完成，可以直接用（而不是保守地說「不知道」）。

```
過程間常數傳播（IPCP）：
  1. bottom-up 遍歷 SCC
  2. 對每個 SCC，分析函式的返回值行為
     → 如果 leaf() 的返回值總是 42，記錄到摘要
  3. 上層函式 middle() 呼叫 leaf() 時，可以直接用摘要：leaf() = 42
  4. 在 middle() 的呼叫點把 leaf() 的返回值替換成 42
```

## LLVM 中的 Call Graph

```cpp
#include "llvm/Analysis/CallGraph.h"

CallGraph &CG = MAM.getResult<CallGraphAnalysis>(*M);

// 遍歷所有函式的 call graph 節點
for (auto &P : CG) {
    CallGraphNode *Node = P.second.get();
    Function *F = Node->getFunction();
    if (!F) continue;
    
    errs() << "Function: " << F->getName() << " calls:\n";
    for (auto &Edge : *Node) {
        if (Function *Callee = Edge.second->getFunction())
            errs() << "  " << Callee->getName() << "\n";
    }
}

// SCC 遍歷（bottom-up）
for (auto &SCC : llvm::make_range(scc_begin(&CG), scc_end(&CG))) {
    errs() << "SCC: ";
    for (CallGraphNode *Node : SCC)
        if (Function *F = Node->getFunction())
            errs() << F->getName() << " ";
    errs() << "\n";
}
```

```bash
# 觀察 call graph
opt -passes="print<call-graph>" /tmp/call_test.ll -o /dev/null 2>&1
```

## 自我檢核

- [ ] Call graph：節點是函式，邊是呼叫關係；間接呼叫必須保守處理
- [ ] SCC：互相遞迴的函式群，SCC DAG 是 DAG
- [ ] Tarjan 算法：DFS + lowlink，O(V+E)，一次 DFS 找所有 SCC
- [ ] Bottom-up SCC 順序：先處理被呼叫者，再處理呼叫者
- [ ] 函式摘要：過程間優化的基礎，bottom-up 保證摘要計算時依賴已就緒

→ [Ch 28 函式內聯（Inlining）](./28-inlining.md)
