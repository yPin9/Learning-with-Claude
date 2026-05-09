# Ch 5 — φ-node 插入：Cytron 算法

> 目標：掌握完整的 SSA 構造算法——先插入 φ-function，再做變數重命名。

## SSA 構造的兩個階段

把普通 IR 轉成 SSA 的過程分兩步：

```
Phase 1：φ-function 插入
  在每個需要的基本塊開頭插入 phi 指令（佔位）

Phase 2：變數重命名
  遍歷支配者樹，給每個定義和使用加上唯一的下標
```

兩個階段缺一不可：光插入 phi 但不重命名，定義還是不唯一；光重命名但沒有 phi，跨路徑的合流點就會缺少定義。

## Phase 1：φ-function 插入

輸入：CFG + 每個基本塊中被定義的變數集合。

對**每個變數** `v`：

```
1. 計算 S = {所有定義了 v 的基本塊}
2. 計算 DF+(S)（迭代支配邊界，見 Ch 4）
3. 在 DF+(S) 的每個基本塊開頭插入 v 的 phi 指令
   phi 的引數個數 = 該基本塊的前驅個數（內容先留空）
```

**重要優化**：在計算 DF+(S) 的過程中加入一個集合 `hasPhi[v]` 避免重複插入：

```
worklist = S
visited = {}

while worklist 非空:
    取出 x from worklist
    for each y in DF(x):
        if y not in hasPhi[v]:
            在 y 插入 v 的 phi
            hasPhi[v].add(y)
            if y not in visited:   // y 現在也有了定義（phi 本身就是定義）
                visited.add(y)
                worklist.add(y)
```

## Phase 2：變數重命名

重命名算法在支配者樹上做 DFS，維護一個**版本棧（version stack）**：

```
對每個變數 v，維護 stack[v]（版本號棧）

DFS 從 entry 開始，進入基本塊 B 時：
  1. 處理 B 開頭的每個 phi(v)：
     - 創建新版本 v_n（壓入 stack[v]）
     - phi 的 LHS 改成 v_n

  2. 處理 B 的每條普通指令：
     - 對每個使用的變數 v：
         把 v 替換成 stack[v].top()（當前版本）
     - 對每個定義的變數 v：
         創建新版本 v_n（壓入 stack[v]）
         LHS 改成 v_n

  3. 填入後繼基本塊的 phi 引數：
     對每個後繼 Y，對 Y 的每個 phi(v)：
         phi 的對應引數填入 stack[v].top()

  4. 遞迴 DFS 支配者樹的子節點

  5. 退出 B 時：彈出在 B 中壓入的所有版本（恢復棧）
```

關鍵：退出時恢復棧，保證 DFS 不同分支看到的是「當前支配路徑上最新的定義」，而非其他路徑的定義。

## 完整走例

```
    entry: x = 1
      |
      A: if cond
     / \
    B   C
    x=2 x=3
     \ /
      D: use x
```

**Phase 1**：

- x 的定義出現在 entry、B、C
- DF(entry) = {}，DF(B) = {D}，DF(C) = {D}
- DF+({entry, B, C}) = {D}
- 在 D 插入 `x = phi(?, ?)` （兩個引數對應前驅 B 和 C）

**Phase 2**：DFS 支配者樹（entry → A → B, A → C, A → D）

進入 entry：
- `x = 1` → 定義 x_1，stack[x] = [x_1]

進入 A：
- `if cond` → 無定義，使用無

進入 B（A 的子節點）：
- `x = 2` → 定義 x_2，stack[x] = [x_1, x_2]
- 填 D 的 phi：D.phi 的 B 引數 = x_2
- 退出 B：彈出 x_2，stack[x] = [x_1]

進入 C（A 的子節點）：
- `x = 3` → 定義 x_3，stack[x] = [x_1, x_3]
- 填 D 的 phi：D.phi 的 C 引數 = x_3
- 退出 C：彈出 x_3，stack[x] = [x_1]

進入 D（A 的子節點）：
- `x = phi(x_2, x_3)` → phi 的 LHS 定義 x_4，stack[x] = [x_1, x_4]
- `use x` → 替換成 x_4

結果：

```llvm
entry:
  x_1 = 1
  br A

A:
  br cond, B, C

B:
  x_2 = 2
  br D

C:
  x_3 = 3
  br D

D:
  x_4 = phi [x_2, B], [x_3, C]
  use x_4
```

## 正確性直覺

重命名算法為什麼正確？

每個使用點 `u` 用到的版本 `stack[v].top()`，正好是在支配者樹的 DFS 路徑上**最近一個定義**的版本。

這個「最近的定義」對應的是：**在 CFG 中，從 entry 到 u 的每條路徑上，最近一個必然執行到的定義**。

這正是「哪個定義到達哪個使用」的語意，而 SSA 用唯一的版本號把它明確化了。

## Mem2Reg 的簡化

LLVM 的 `mem2reg` 是 SSA 構造的特殊版本，只處理「`alloca` 變數的 load/store」轉換成 SSA 暫存器。它用了幾個優化：

1. **Single-block alloca**：如果一個 alloca 的所有 load/store 都在同一個基本塊，不需要 phi，直接替換。

2. **Single predecessor**：如果一個基本塊只有一個前驅，不需要 phi，直接從前驅傳播。

3. **Pruned SSA**：在插入 phi 前先做 liveness 檢查，避免插入「沒有任何使用者的 phi」。

```bash
# 觀察 mem2reg 的完整效果
cat > /tmp/ssa_construct.c << 'EOF'
int f(int a, int b, int c) {
    int x = a;
    int y = b;
    if (c > 0) {
        x = x + y;
    } else {
        y = x - y;
    }
    return x + y;
}
EOF

clang -O0 -S -emit-llvm /tmp/ssa_construct.c -o /tmp/before.ll
opt -S -passes=mem2reg /tmp/before.ll -o /tmp/after.ll

echo "=== Before ===" && cat /tmp/before.ll
echo "=== After ===" && cat /tmp/after.ll
```

觀察：哪些 alloca 消失了？phi 出現在哪個基本塊？phi 的引數對應哪些前驅？

## 自我檢核

- [ ] Phase 1（φ 插入）和 Phase 2（重命名）各自的輸入輸出是什麼
- [ ] 重命名算法中版本棧的作用：「進入壓入，退出彈出」保證作用域正確
- [ ] 能手動對小例子完整跑一遍兩個階段
- [ ] mem2reg 為什麼比完整 SSA 構造更快（三個特殊情況優化）

→ [Ch 6 SSA 解構：Out-of-SSA](./06-ssa-deconstruction.md)
