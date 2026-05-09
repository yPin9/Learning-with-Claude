# Ch 12 — MemorySSA

> 目標：理解 MemorySSA 如何把 SSA 的稀疏性延伸到記憶體操作，以及它如何讓 LICM 等優化變得高效。

## 問題：load/store 不是 SSA 值

SSA 優美地處理了「暫存器變數」，但記憶體操作——`store` 和 `load`——在 SSA 中是「副作用指令」，沒有被納入 use-def 鏈：

```llvm
store i32 %val, i32* %ptr     ; 把值寫到記憶體，沒有 LHS
%loaded = load i32, i32* %ptr  ; 從記憶體讀，但「讀的是哪個 store」不明
```

要知道 `%loaded` 對應哪個 `store`，需要別名分析 + 遍歷所有可達的 store，複雜度高。

**MemorySSA** 為每個記憶體操作（load/store/call）創建一個「記憶體 SSA 值」，讓記憶體操作也能享受 SSA 的稀疏性。

## 三種 MemorySSA 節點

```
MemoryDef：對記憶體的寫（store、call 可能有副作用）
MemoryUse：對記憶體的讀（load）
MemoryPhi：記憶體版本的 φ-function，在匯合點合併不同路徑的記憶體狀態
```

每個 MemoryDef 創建一個新的「記憶體版本號」，每個 MemoryUse 連接到它的「最近的可能 clobber」。

## 例子

```c
void f(int *a, int *b, int *c) {
    *a = 1;    // MemoryDef 1
    *b = 2;    // MemoryDef 2
    int x = *a; // MemoryUse（clobbered by MemoryDef ?）
}
```

如果 a 和 b **NoAlias**：

```
liveOnEntry (記憶體的初始狀態)
MemoryDef 1: *a = 1    （在 liveOnEntry 之後）
MemoryDef 2: *b = 2    （在 MemoryDef 1 之後）
MemoryUse: *a（clobbered by MemoryDef 1）
  → 因為 a 和 b NoAlias，MemoryDef 2 不影響 *a
  → MemoryUse 的「最近 clobber」是 MemoryDef 1
```

這讓我們可以直接從 MemoryUse 走到對應的 MemoryDef，不需要掃描所有 store。

如果 a 和 b **MayAlias**：

```
MemoryUse: *a（clobbered by MemoryDef 2）
  → MemoryDef 2 可能 alias *a，所以 MemoryUse 的最近 clobber 是 MemoryDef 2
  → 從 MemoryDef 2 往回找，它連接到 MemoryDef 1
```

## MemoryPhi

在控制流匯合點，需要合併不同路徑的記憶體狀態：

```llvm
entry:
  %mem1 = MemoryDef [liveOnEntry]: store %val, %ptr

if.then:
  %mem2 = MemoryDef [%mem1]: store %val2, %ptr2

if.else:
  ; 沒有 store

merge:
  %mem3 = MemoryPhi [%mem2, if.then], [%mem1, if.else]
  %x = MemoryUse [%mem3]: load %ptr
```

MemoryPhi 讓 MemoryUse 能「稀疏地」找到正確的可能 clobber，而不需要遍歷所有前驅基本塊。

## MemorySSA 在 LICM 中的應用

Loop-Invariant Code Motion（LICM，Ch 22）要把迴圈不變式的 load 提到迴圈外。

問題：一個 load `*p` 是否是迴圈不變的？

沒有 MemorySSA：需要分析迴圈內所有 store，判斷有沒有可能 clobber `*p`。

有 MemorySSA：

```
1. 找 load *p 的 MemoryUse
2. 看它的 clobber 是哪個 MemoryDef
3. 如果這個 MemoryDef 在迴圈外 → load 是迴圈不變的，可以提出
4. 如果這個 MemoryDef 在迴圈內 → 需要進一步判斷
```

從 O(迴圈所有 store) 降到了 O(沿 MemorySSA 鏈的長度)。

## LLVM C++ API

```cpp
#include "llvm/Analysis/MemorySSA.h"

MemorySSA &MSSA = FAM.getResult<MemorySSAAnalysis>(F).getMSSA();
MemorySSAWalker *Walker = MSSA.getWalker();

// 找某個 load 指令的最近 clobber
auto *LoadI = ...; // LoadInst*
MemoryAccess *Clobber = Walker->getClobberingMemoryAccess(LoadI);

if (auto *Def = dyn_cast<MemoryDef>(Clobber)) {
    errs() << "Clobbered by: " << *Def->getMemoryInst() << "\n";
} else if (isa<MemoryPhi>(Clobber)) {
    errs() << "Clobbered by MemoryPhi (multiple defs)\n";
}

// 列印 MemorySSA
MSSA.print(errs());
```

```bash
# 觀察 MemorySSA
opt -passes="print<memoryssa>" /tmp/memory_test.ll -o /dev/null 2>&1
```

輸出中每個 store 前有 `; MemDef 1:` 等標注，每個 load 前有 `; MemUse(1)` 等標注。

## MemorySSA 的構造

構造過程和普通 SSA 類似：

1. 對每個基本塊，找出所有 store（創建 MemoryDef），按順序排列
2. 在 CFG 的支配邊界插入 MemoryPhi（使用 DF+ 算法，見 Ch 4）
3. 重命名：維護「當前記憶體版本」棧，給每個 MemoryUse 找到 clobber

差別：MemorySSA 是對**整個記憶體狀態**的 SSA，不是對單個變數的 SSA，所以每個基本塊最多一個 MemoryPhi（而不是每個變數一個）。

## 自我檢核

- [ ] MemoryDef / MemoryUse / MemoryPhi 各自對應什麼指令
- [ ] MemoryUse 的 clobber 鏈：找「最近的可能寫」
- [ ] MemorySSA 讓 LICM 可以稀疏地判斷 load 是否迴圈不變
- [ ] `Walker->getClobberingMemoryAccess()` 的語意
- [ ] 跑 `print<memoryssa>` 並能解讀輸出中的 MemDef/MemUse 標注

→ [Ch 13 常數折疊（Constant Folding）與 Peephole](./13-constant-folding.md)
