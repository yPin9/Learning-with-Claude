# Ch 10 — 稀疏條件常數傳播（SCCP）

> 目標：理解 SCCP 的三值格，掌握它如何同時做常數傳播和不可達代碼消除，推導 Worklist 算法的兩條規則。

## 為什麼不直接用 Ch 7 的框架

普通常數傳播（Constant Propagation）用 Ch 7 的格做前向資料流分析：

```
⊥ → 常數 c → ⊤
每個定義點：如果右側全是常數 → 結果是常數；否則 ⊤
meet：如果兩條路徑都是同一個常數 c → c；否則 ⊤
```

問題：這個框架**不感知控制流**。考慮：

```c
if (true) {    // 條件是常數
    x = 1;
} else {
    x = 2;     // 這個分支不可能執行
}
return x;      // 普通分析：x 的定義有 1 和 2，取 meet = ⊤（不確定）
```

普通常數傳播得到 x 是 ⊤（不確定），但正確答案是 x = 1。

**SCCP（Sparse Conditional Constant Propagation）** 同時追蹤「哪些基本塊是可達的」，讓常數傳播能利用條件跳轉的資訊。

## SCCP 的格

三值格，對每個 SSA 值 v：

```
      ⊤（TopVal，"執行前未知" 或 "over-defined，有多個不同值"）
     /|\
    c1 c2 c3 ...  （具體常數值）
     \|/
      ⊥（BotVal，"不可達，此定義不可能執行到"）
```

meet 規則：

```
⊤ ⊓ x = x（⊤ 是最大元）

注意：SCCP 用的是從 ⊥ 往上走的分析：
  初始：所有值 = ⊥
  當發現可達時：根據指令計算值
  如果有多個不同常數匯合：升到 ⊤
```

等等，讓我更精確地說：

SCCP 的 lattice 實際上是：
- 初始值 = ⊥（可能未定義，保守起點）
- 常數 c（已知為常數）
- ⊤（已知為非常數，over-defined）

且只往上走：`⊥ → c → ⊤`（單調性）。

## 兩個 Worklist

SCCP 同時維護兩個 Worklist：

```
CFG_worklist：待處理的 CFG 邊（控制流邊）
SSA_worklist：待處理的 SSA use-def 邊（值依賴邊）
```

和一個可達標記：`Executable[edge]`：CFG 邊是否已被判定為可能執行。

## SCCP 算法

```
初始化：
  所有值 = ⊥
  所有 Executable[edge] = false
  CFG_worklist = {entry 的第一條邊}

主循環：
  while CFG_worklist 或 SSA_worklist 非空:
    
    從 CFG_worklist 取出邊 (B_pred, B):
      if Executable[(B_pred, B)] 已是 true: 跳過
      Executable[(B_pred, B)] = true
      對 B 的每個 phi：重新求值
      if B 第一次變為可達（所有前驅邊都未標記過）:
        對 B 的每條非 phi 指令：Evaluate(I)
    
    從 SSA_worklist 取出值 v：
      對所有使用 v 的指令 I：Evaluate(I)

Evaluate(I)：
  計算 I 的格值（根據操作數的格值）
  if 結果改變了：
    if I 是 phi：把 I 的結果加入 SSA_worklist
    if I 是分支：
      if 條件是常數 c：只把對應的那條邊加入 CFG_worklist
      else：把兩條邊都加入 CFG_worklist
    else：把 I 的結果加入 SSA_worklist
```

## 走例：常數條件分支

```llvm
entry:
  br true, then, else    ; 條件是常數 true

then:
  %x1 = add i32 1, 0    ; x1 = 1
  br merge

else:
  %x2 = add i32 2, 0    ; x2 = 2
  br merge

merge:
  %x3 = phi [%x1, then], [%x2, else]
  ret %x3
```

執行過程：

1. CFG_worklist = {entry→then, entry→else}（因為 br true，實際上只加 entry→then）

   等等，SCCP 初始只加 entry 這條「無條件邊」，然後 Evaluate entry 的 br：條件是 `true`（常數），所以只加 `entry → then` 到 CFG_worklist。

2. 處理 `entry → then`：
   - Executable[entry→then] = true
   - Evaluate `%x1 = add 1, 0` → x1 = 1，加入 SSA_worklist

3. 處理 SSA_worklist（x1）：
   - x1 = 1，使用了 x1 的指令是 merge 的 phi
   - Evaluate phi `%x3 = phi [x1=1, then], [x2=⊥, else]`
   - else 的邊未標記（Executable[else→merge] = false），跳過 x2 的引數
   - x3 = 1（只有一個可達引數）

4. CFG_worklist 空了，SSA_worklist 空了，結束

5. else 基本塊**從未被處理**——它的所有指令格值保持 ⊥

最終結果：
- x3 = 1（常數）
- else 基本塊不可達 → 可以刪除

這正是普通常數傳播做不到的。

## 結合 sparse（稀疏性）

SCCP 不是對每個基本塊維護一個「所有變數的狀態」表，而是沿著 **SSA use-def 鏈** 傳播。

「x1 改變了 → 找所有使用 x1 的指令」——這比「掃描所有基本塊」快得多，因為 use-def 鏈通常很短。這就是「Sparse」的含義。

## LLVM 中的 SCCP

LLVM 的實作在 `llvm/lib/Transforms/Scalar/SCCP.cpp`。

```bash
# 觀察 SCCP 的效果
cat > /tmp/sccp_test.c << 'EOF'
int f() {
    int x;
    if (1 > 0) {   // 常數條件
        x = 42;
    } else {
        x = 99;    // 死代碼
    }
    return x;
}
EOF

clang -O0 -S -emit-llvm /tmp/sccp_test.c -o /tmp/sccp_in.ll
opt -S -passes="mem2reg,sccp" /tmp/sccp_in.ll -o /tmp/sccp_out.ll
cat /tmp/sccp_out.ll   # else 分支應該消失，ret 直接返回 42
```

## 自我檢核

- [ ] SCCP 三值格：⊥ → 常數 → ⊤，只往上走
- [ ] 兩個 Worklist：CFG 邊（控制流）和 SSA 邊（值依賴）
- [ ] Executable 標記：避免處理不可達基本塊的 phi 引數
- [ ] 「稀疏」的含義：沿 use-def 鏈傳播，而非掃描所有基本塊
- [ ] 跑過 `sccp` pass 並觀察死分支被消除

→ [Ch 11 別名分析基礎（Alias Analysis）](./11-alias-analysis.md)
