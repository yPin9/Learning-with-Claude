# Ch 6 — SSA 解構：Out-of-SSA

> 目標：理解為什麼 SSA 必須在代碼生成前解構，掌握並行複製語意和兩個經典陷阱（lost-copy、swap 問題）。

## 為什麼要解構 SSA

SSA 是中端優化的理想表示，但機器沒有 φ-function 這個指令。後端要把 SSA 轉換成真實的機器指令，φ-function 必須被消除。

解構的目標：

```
把 x = phi [a, B1], [b, B2]
轉換成：
  在 B1 的末尾插入 copy x = a
  在 B2 的末尾插入 copy x = b
```

但直接插入 copy 會遇到兩個問題。

## φ-function 的並行語意

φ-function 在語意上是**並行求值**的：同一個基本塊開頭的所有 φ-function 同時計算，互相不干擾。

```llvm
; 這兩個 phi 同時求值
x2 = phi [x1, pred1], [y1, pred2]
y2 = phi [y1, pred1], [x1, pred2]
```

把它們分別轉成兩個 copy，執行順序就有影響了：

```
; 錯誤的順序（先 x 再 y）
x2 = x1    ; 在 pred1 路徑
y2 = y1    ; 在 pred1 路徑
; 結果：x2 = x1, y2 = y1  ✓

; 在 pred2 路徑：
x2 = y1    ; 用的是舊的 y1
y2 = x1    ; 但 x2 已經變了！如果 y2 用的是 x2，就錯了
```

問題的根源：序列 copy 破壞了並行語意。

## Lost-Copy 問題

```llvm
; SSA 中的一個迴圈
loop:
  x2 = phi [x1, entry], [x3, loop]
  x3 = x2 + 1
  ...
  br loop
```

如果優化 pass 把 `x3 = x2 + 1` 消除（替換 x3 → x2 + 1），直接在 phi 引數替換後：

```llvm
loop:
  x2 = phi [x1, entry], [x2 + 1, loop]  ; 自引用！
```

這在 SSA 語意上是合理的（phi 讀的是舊值），但轉成序列代碼時，`x2 = x2 + 1` 就變成了自增，語意不同。

**解決方案**：在解構前保持一個「原始複製」（original copy），讓優化不直接消除 phi 引數的中間值。

## Swap 問題

最典型的並行複製問題：

```llvm
; 兩個變數互換
header:
  a2 = phi [a1, entry], [b2, loop]
  b2 = phi [b1, entry], [a2, loop]
```

在 loop 邊（從 loop 到 header）需要並行複製 `a2 = b2, b2 = a2`（讀舊值）。

如果序列化為：

```
a2 = b2   ; 先複製 b2 到 a2
b2 = a2   ; 再複製 a2 到 b2：但 a2 已經是 b2 了！
```

結果：`a2 = b2, b2 = b2`，b 的舊值丟失了。

**解決方案**：需要引入臨時變數，或者識別「互換環」並用 xchg 指令（或三次 xor）解決。

## 並行複製序列化算法

標準算法（Briggs et al. 1998）把一組並行複製 `{dst_i = src_i}` 轉換成序列複製：

```
分析複製的依賴圖（哪個 dst 是另一個 src）
找到所有「環（cycle）」和「鏈（chain）」

對每條鏈：從葉子往根複製（無依賴問題）
對每個環：引入臨時變數打破環
  tmp = a
  a = b
  b = c
  c = tmp
```

環的識別：如果 dst_i 又是某個 src_j（且不是同一個 copy），就形成了環。

```python
def sequentialize_parallel_copies(copies):
    # copies: list of (dst, src)
    result = []
    ready = []   # src 沒有被任何其他 copy 的 dst 佔用的 copy
    waiting = {} # dst -> src，等待 dst 被釋放
    
    # 找出所有「可以立刻執行」的複製（src 不是任何 dst）
    dst_set = {dst for dst, src in copies}
    for dst, src in copies:
        if src not in dst_set:
            ready.append((dst, src))
        else:
            waiting[dst] = src
    
    while ready:
        dst, src = ready.pop()
        result.append(f"{dst} = {src}")
        # 如果 dst 是某個 waiting 的 src，那個 copy 現在可以執行了
        for w_dst, w_src in list(waiting.items()):
            if w_src == dst:
                ready.append((w_dst, w_src))
                del waiting[w_dst]
    
    # 剩下的是環（互相依賴的 copies）
    while waiting:
        dst, src = next(iter(waiting.items()))
        tmp = fresh_temp()
        result.append(f"{tmp} = {dst}")  # 打破環
        # 沿環處理...
    
    return result
```

## LLVM 的解構時機

LLVM 在**暫存器分配之後**做 SSA 解構（phi elimination）。原因：暫存器分配器可以把 phi 的不同引數分配到同一個物理暫存器，消去 phi 就不需要插入 copy——這叫**copy coalescing**。

流程：

```
SSA IR
  ↓ 暫存器分配（盡可能讓 phi 引數和結果用同一個暫存器）
  ↓ phi elimination（剩下無法 coalesce 的 phi 插入 copy）
  ↓ 並行複製序列化
Machine IR（無 phi）
```

LLVM 相關代碼：`llvm/lib/CodeGen/PHIElimination.cpp`

```bash
# 觀察 phi elimination
clang -O1 -S -emit-llvm /tmp/phi_example.ll -o /tmp/mir.ll
llc -stop-after=phi-node-elimination /tmp/mir.ll 2>&1 | head -50
```

## 自我檢核

- [ ] φ-function 的並行語意：所有 phi 同時讀舊值，同時寫
- [ ] Lost-copy 問題：優化消除中間值後，phi 引數自引用導致語意變化
- [ ] Swap 問題：互相依賴的並行複製序列化時需要臨時變數
- [ ] LLVM 在暫存器分配後做 phi elimination，利用 copy coalescing 減少 copy

→ [Ch 7 資料流分析框架：格論與 Worklist](./07-dataflow-framework.md)
