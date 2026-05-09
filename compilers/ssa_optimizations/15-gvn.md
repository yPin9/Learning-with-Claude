# Ch 15 — 全局值編號（GVN）

> 目標：理解值編號的基本思想，掌握支配樹 DFS 的 GVN 算法，以及 LLVM NewGVN 的 RPO-based 方法。

## 什麼是值編號

**值編號（Value Numbering）** 的核心問題：

> 程式中的哪些計算結果一定相同？

```c
int a = x + y;
int b = x + y;  // b 和 a 一定相同，不需要重新計算
```

如果 `x + y` 在兩個地方計算，且中間 x 和 y 都沒有被修改，第二個 `x + y` 是**冗餘的（redundant）**，可以用第一個的結果替換。

這就是**冗餘計算消除（Redundant Expression Elimination）**，GVN 是最強的全局版本。

## 局部值編號

在**單個基本塊**內，值編號很簡單：

```
維護一個 hash map：{(opcode, val_num1, val_num2) → val_num}

對每條指令 I = op(v1, v2)：
  lookup (op, vn(v1), vn(v2)) in map
  if found: I 是冗餘的，用已知結果替換
  if not found: 分配新 val_num，插入 map
```

問題：跨基本塊時，map 的有效範圍怎麼確定？

## 支配樹 DFS 的全局值編號

**思路**：在支配者樹上做 DFS。進入基本塊時，可以把「父節點的值編號表」繼承過來，因為支配者的所有計算在到達子節點時一定已執行。

```
DFS_GVN(B, scoped_map):
  對每條指令 I = op(v1, v2) in B（按順序）：
    key = (op, lookup(v1, scoped_map), lookup(v2, scoped_map))
    if key in scoped_map:
      replace I with scoped_map[key]
    else:
      scoped_map[key] = I
      assign val_num to I
  
  for each child C of B in dominator tree:
    // 子節點繼承父節點的 scoped_map（在 DFS 結束後撤銷新增的條目）
    DFS_GVN(C, scoped_map.scope_push())
    scoped_map.scope_pop()
```

進入子節點時 push 一個 scope，退出時 pop，保證不同分支不互相干擾（類似 Ch 5 的重命名算法）。

## 例子

```llvm
entry:
  %a = add i32 %x, %y    ; key=(add, x, y), val=a
  %b = mul i32 %a, 2     ; key=(mul, a, 2), val=b
  br %cond, then, else

then:                     ; 繼承 entry 的 map
  %c = add i32 %x, %y    ; key=(add, x, y)，found! → 替換成 %a
  %d = mul i32 %c, 2     ; 替換後 key=(mul, a, 2)，found! → 替換成 %b
  ...

else:                     ; 獨立的 scope，繼承 entry 的 map
  %e = add i32 %x, %y    ; 同樣命中，替換成 %a
  ...
```

`then` 中的兩次計算完全被消除，替換成 `entry` 中已計算的結果。

## phi 節點的處理

GVN 對 phi 節點需要特殊處理：如果 phi 的所有引數的值編號相同，phi 本身是冗餘的。

```llvm
; 兩條路徑都計算了 x+y，phi 沒有必要
%phi = phi [%a, then], [%e, else]  ; 如果 a 和 e 的值編號相同 → 替換成 %a
```

這讓 GVN 能消除「不同路徑計算了同樣值後合流」的冗餘。

## LLVM 的兩種 GVN

### 舊 GVN（基於支配樹 DFS）

`llvm/lib/Transforms/Scalar/GVN.cpp`

- 傳統方法，支持 load 的 PRE（Partial Redundancy Elimination）
- 處理 load 的「可用性」需要 MemorySSA
- 穩定，LLVM 的默認 O2 pipeline 一直在用

### NewGVN（RPO-based，基於等價類）

`llvm/lib/Transforms/Scalar/NewGVN.cpp`

- 用 RPO 遍歷 + congruence class 替代支配樹 DFS
- 更精確：能發現舊 GVN 遺漏的等價關係
- 實作更複雜，某些場景下比舊 GVN 慢

NewGVN 的核心思想：維護**等價類（congruence class）**——所有值相同的 SSA 值在同一個類中。反覆傳播直到不動點。

```bash
# 比較兩種 GVN 的效果
opt -S -passes="mem2reg,gvn" input.ll -o out_gvn.ll
opt -S -passes="mem2reg,newgvn" input.ll -o out_newgvn.ll
diff out_gvn.ll out_newgvn.ll
```

## GVN 的範疇

GVN 能處理：

```
✓ 純量運算的冗餘（add, mul, cmp, etc.）
✓ 已知相等的 phi 節點
✓ load 冗餘（需要 MemorySSA 確認中間無 store clobber）
✗ store 冗餘（Dead Store Elimination 另有 pass）
✗ 部分冗餘（PRE）——需要代碼插入，GVN 的 load PRE 有限做
```

**PRE（Partial Redundancy Elimination）** 是 GVN 的超集：

```
      B1         B2
      |           |
  x+y 計算     沒有 x+y
      |           |
      +───────────+
          B3
       x+y（PRE：在 B2 插入 x+y 的計算，讓 B3 的 x+y 變成完全冗餘）
```

LLVM 的 GVN 做有限的 load PRE，完整的表達式 PRE 代價較高。

## 自我檢核

- [ ] 值編號的核心：相同 (opcode, operands_val_num) → 相同值
- [ ] 支配樹 DFS 的 GVN：進入繼承，退出撤銷（scoped map）
- [ ] 為什麼支配樹 DFS 保證正確：父節點的計算在子節點前必定執行
- [ ] phi 節點的冗餘條件：所有引數值編號相同
- [ ] 舊 GVN vs NewGVN：前者穩定，後者更精確但更複雜

→ [Ch 16 複製傳播（Copy Propagation）](./16-copy-propagation.md)
