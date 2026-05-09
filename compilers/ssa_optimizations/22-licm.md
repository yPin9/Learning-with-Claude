# Ch 22 — LICM：Loop-Invariant Code Motion

> 目標：理解 LICM 的 hoisting 和 sinking 兩個方向，掌握正確性條件（不是所有不變式都能提），以及 MemorySSA 如何讓 load 的 LICM 變得高效。

## 什麼是迴圈不變式

**迴圈不變式（Loop-Invariant）**：在迴圈執行過程中，每次迭代都計算出相同值的計算。

```c
for (int i = 0; i < n; i++) {
    int len = strlen(s);  // 每次都計算相同的值！
    a[i] = a[i] + len;
}
```

`strlen(s)` 不依賴 `i`，結果不會改變。把它提到迴圈外：

```c
int len = strlen(s);  // 只計算一次
for (int i = 0; i < n; i++) {
    a[i] = a[i] + len;
}
```

## Hoisting（上提）

**Hoisting**：把迴圈不變式移到迴圈的 preheader。

SSA 中，如何判斷一條指令是迴圈不變的？

```
指令 I 是迴圈 L 不變的，當且僅當：
  1. I 的所有操作數都定義在 L 外
  OR
  2. I 的所有操作數也是迴圈 L 不變的（遞迴）
```

注意：指令的**定義**在迴圈外，不等於定義的值在迴圈中不改變（迴圈內有 phi 可能每次不同）。

### Hoisting 的安全條件

並不是所有迴圈不變式都能 hoist：

**條件 1：指令不能有副作用**（大多數純計算滿足，store 不行，call 通常不行）

**條件 2：指令一定執行（必定執行，不是「可能執行」）**

```c
for (int i = 0; i < n; i++) {
    if (i == 0) {
        x = a + b;  // a+b 是不變式，但只在 i==0 時執行
    }
}
```

如果 `n == 0`，迴圈根本不執行，`a + b` 從未計算。如果 hoist 到 preheader，就在迴圈前執行了，語意不同。

判斷「必定執行」：指令所在基本塊必須**支配所有迴圈出口**。

**條件 3：對 load 指令，迴圈內沒有可能 clobber 對應地址的 store**

這需要別名分析 + MemorySSA（見下文）。

## Sinking（下沉）

**Sinking**：把只在迴圈某條出路上使用的不變式，移到對應的出口塊。

```c
for (...) {
    x = expensive_computation();  // 不變式
    if (exit_condition) break;
}
use(x);  // 只在 break 後使用
```

Sinking 不要求「必定執行」：只要在實際執行到的路徑上計算即可。

LLVM 的 LICM 會同時做 hoisting 和 sinking：

- 能 hoist 的優先 hoist（到 preheader）
- 不能 hoist 的嘗試 sink（到出口塊）

## MemorySSA-based LICM

對 load 的 LICM，需要判斷「迴圈內有沒有對應地址的 store」。

沒有 MemorySSA：掃描迴圈內所有 store，對每個做別名查詢。複雜度 O(loads × stores)。

有 MemorySSA：

```
對 load %ptr：
  1. 找它的 MemoryUse 的 clobber（getClobberingMemoryAccess）
  2. 如果 clobber 在迴圈外（liveOnEntry 或 preheader 的 MemoryDef）
     → 迴圈內沒有 store 可能 clobber 這個 load
     → load 是迴圈不變的，可以 hoist
```

從 O(loads × stores) 降到 O(沿 MemorySSA 鏈的長度)。

## LLVM 的 LICM 實作

```bash
# 完整的 LICM 前置條件
opt -passes="loop-simplify,lcssa,licm" input.ll -o output.ll

# 或者用 O2 的 pipeline
opt -O2 input.ll -o output.ll
```

LICM pass 在 `llvm/lib/Transforms/Scalar/LICM.cpp`。

```cpp
// Pass 中查詢 LICM 需要的分析
auto &LI = FAM.getResult<LoopAnalysis>(F);
auto &AA = FAM.getResult<AAManager>(F);
auto &MSSA = FAM.getResult<MemorySSAAnalysis>(F).getMSSA();
auto &DT = FAM.getResult<DominatorTreeAnalysis>(F);
```

## 實際效果觀察

```bash
cat > /tmp/licm_test.c << 'EOF'
int arr[1000];
int n;

void f(int m) {
    for (int i = 0; i < n; i++) {   // n 是全局變量
        arr[i] = arr[i] + m * 3;    // m*3 是不變式
    }
}
EOF

clang -O0 -S -emit-llvm /tmp/licm_test.c -o /tmp/licm_in.ll
opt -S -passes="mem2reg,loop-simplify,lcssa,licm" /tmp/licm_in.ll -o /tmp/licm_out.ll
diff /tmp/licm_in.ll /tmp/licm_out.ll
# m*3 應該被提到迴圈外
```

## 常見誤解

**「不變式 = 沒有用到迴圈變數」** — 不完全對。一個指令可以依賴迴圈變數，但那個依賴的值也是不變的（遞迴定義）。

**「所有不變式都能提」** — 錯。必須執行條件（支配出口）是關鍵限制，違反它會引入空迴圈的副作用。

**「load 可以隨意 hoist」** — 錯。需要確認迴圈內沒有對應地址的 store（別名分析 + MemorySSA）。

## 自我檢核

- [ ] 迴圈不變的判定：操作數全在迴圈外（遞迴定義）
- [ ] Hoisting 安全條件：無副作用 + 必定執行（支配所有出口）
- [ ] Sinking：不滿足「必定執行」的不變式的後備選項
- [ ] MemorySSA 讓 load 的不變性判斷從 O(loads×stores) 降到 O(鏈長)
- [ ] 迴圈 pass 的依賴：loop-simplify → lcssa → licm

→ [Ch 23 Scalar Evolution（SCEV）](./23-scev.md)
