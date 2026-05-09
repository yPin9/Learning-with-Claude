# Ch 1 — 為什麼需要 SSA

> 目標：理解 SSA 之前的 IR 有什麼根本性的問題，以及 SSA 的核心定義和它帶來的三個關鍵性質。

## 從一段程式開始

```c
int x = 1;
if (cond) {
    x = 2;
}
return x;
```

問題：`return x` 的 `x` 是哪個 `x`？

這個問題聽起來簡單，但對編譯器而言，「x 是哪個定義傳過來的」（def-use 關係）是所有優化算法的核心。如果我們不能快速、精確地回答這個問題，任何優化都做不了。

## 三地址碼（Three-Address Code）的局限

傳統的中間表示用**三地址碼**：每條指令最多兩個運算元、一個結果。

把上面那段程式翻譯成三地址碼：

```
    x = 1
    if cond goto L1
    goto L2
L1: x = 2
L2: return x
```

現在問：`L2` 的 `return x` 用的 `x` 是哪一個？

```
可能是 x = 1（如果 cond 為 false）
可能是 x = 2（如果 cond 為 true）
```

答案取決於執行路徑，靜態分析時兩個都可能。

### 這帶來什麼問題

**常數傳播失效**：假設 `cond` 是 `true`（靜態可知），我們想把 `return x` 變成 `return 2`。但要做到這件事，分析器必須追蹤「哪條路徑的定義會到達這個使用」。在三地址碼中，這需要**到達定義分析（reaching definitions）**——一個需要迭代不動點的資料流分析，複雜度高。

**死碼消除失效**：我們想知道 `x = 1` 之後有沒有被用到。如果 `cond` 永遠為 true，那 `x = 1` 是死碼。但判斷「x 的這個定義有沒有活躍的使用者」同樣需要完整的 def-use 鏈分析。

**核心問題**：在三地址碼中，同一個變數名 `x` 可以被**多次賦值**，每次賦值都可能到達不同的使用點。這讓任何對 def-use 關係的分析都變成全局問題。

## SSA 的定義

**靜態單賦值形式（Static Single Assignment，SSA）**：IR 中每個變數**只被定義一次**。

同一段程式在 SSA 下：

```
    x1 = 1
    if cond goto L1
    goto L2
L1: x2 = 2
L2: x3 = φ(x1, x2)
    return x3
```

每個 `x` 都有獨立的下標，每個下標只有一個定義點。

`L2` 的那個奇怪的 `x3 = φ(x1, x2)` 叫做 **φ-function（phi function）**：它根據控制流從哪條邊進來，選擇對應的值。從 `L1` 進來就選 `x2`，從條件為 false 的路徑進來就選 `x1`。

φ-function 是 SSA 的核心創意。它把「控制流匯合點的值選擇」顯式地表達出來，讓每個使用點都只對應到一個明確的定義。

## SSA 帶來的三個性質

### 1. Use-Def 鏈是常數複雜度

在三地址碼中，找一個使用點的所有可能定義需要資料流分析。

在 SSA 中：每個變數只有一個定義，所以任何使用點的 def 是**唯一的**，直接從變數名就能找到。

```
x3 = φ(x1, x2)   → x3 的定義就是這行，不需要任何分析
return x3          → 找 x3 的定義：直接是上面那行 φ
```

Use-Def 鏈從資料流問題變成了純粹的語法查找。

### 2. Def-Use 鏈也簡單了

每個定義的所有使用者（即：x1 被誰用）在 SSA 下可以用一個簡單清單記錄，不需要反向資料流分析。這讓「這個值有沒有人用到」（用於死碼消除）變成 O(1) 查詢。

### 3. 優化算法可以是 sparse 的

在三地址碼上做常數傳播，需要對整個程式的每個基本塊維護一個「x 的當前可能值集合」，在基本塊之間傳遞這個資訊。每次迭代都要掃描所有基本塊，直到不動點。

在 SSA 上，常數傳播只需要沿著 def-use 鏈傳播：如果 `x1 = 1`（常數），就找出所有用到 `x1` 的地方，把它們替換成 `1`，然後再看那些指令是否也變成了常數。這是**稀疏（sparse）**算法，只碰到真正相關的指令，不掃描整個程式。

Ch 10 的 SCCP 就是這個想法的完整實作。

## LLVM IR 就是 SSA

打開任何一個 `-O1` 以上的 LLVM IR，你會看到：

```llvm
define i32 @add(i32 %a, i32 %b) {
entry:
  %result = add i32 %a, %b
  ret i32 %result
}
```

所有的 `%名字` 都只有一個定義點——這不是巧合，LLVM IR 規格就要求 SSA 形式。`%result` 被定義一次，被 `ret` 使用一次，def-use 關係一目了然。

用 `-O0` 生成的 IR 是例外——Clang 刻意不構造 SSA，而是用 `alloca`（棧上記憶體）模擬變數，之後再由 `mem2reg` pass 把它轉成 SSA：

```llvm
; -O0 的 IR（非 SSA）
define i32 @add(i32 %a, i32 %b) {
entry:
  %a.addr = alloca i32          ; 在棧上分配 a 的空間
  %b.addr = alloca i32
  store i32 %a, i32* %a.addr    ; 存入
  store i32 %b, i32* %b.addr
  %0 = load i32, i32* %a.addr   ; 讀出
  %1 = load i32, i32* %b.addr
  %add = add i32 %0, %1
  ret i32 %add
}
```

`mem2reg` 的工作就是：分析哪些 `alloca` 可以被 SSA 暫存器替代，然後插入必要的 φ-function。這個過程就是 Ch 4–5 要推導的算法。

## φ-function 的語意精確定義

φ-function `x = φ(x_pred1 : B1, x_pred2 : B2, ...)` 的語意：

- 只存在於基本塊的**開頭**
- 每個引數對應一條前驅邊（predecessor edge）
- 執行時選擇**從哪條邊進來**對應的引數值

注意：φ-function 是**同時求值**的。如果同一個基本塊有多個 φ-function，它們的引數是舊值，不是同一塊裡其他 φ-function 的結果。這個「並行語意」在 SSA 解構時（Ch 6）會很重要。

## 動手：觀察 `mem2reg` 的效果

```bash
cat > /tmp/phi_example.c << 'EOF'
int f(int cond) {
    int x = 1;
    if (cond) {
        x = 2;
    }
    return x;
}
EOF

# -O0：alloca 形式
clang -O0 -S -emit-llvm /tmp/phi_example.c -o /tmp/phi_O0.ll
cat /tmp/phi_O0.ll

# mem2reg：轉換成 SSA，應該可以看到 phi node
opt -S -passes=mem2reg /tmp/phi_O0.ll -o /tmp/phi_ssa.ll
cat /tmp/phi_ssa.ll
```

觀察重點：
- `-O0` 的 IR 裡有幾個 `alloca`？
- SSA 版本的 `phi` node 出現在哪個基本塊？
- `phi` 的兩個引數分別來自哪兩個基本塊？

## 自我檢核

- [ ] 能說清楚為什麼三地址碼讓常數傳播需要全局資料流分析
- [ ] SSA 的定義：每個變數只有一個靜態定義點
- [ ] φ-function 的作用：在控制流匯合點選擇來自不同路徑的值
- [ ] SSA 讓 use-def 鏈從資料流問題變成語法查找
- [ ] 跑過 `mem2reg` 並能在 IR 裡找到 `phi` node

→ [Ch 2 支配關係（Dominance）](./02-dominance.md)
