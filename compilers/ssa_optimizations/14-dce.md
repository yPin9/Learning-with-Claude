# Ch 14 — 死碼消除（DCE 與 ADCE）

> 目標：掌握簡單 DCE 的 Mark-Sweep 算法，理解 ADCE 如何用控制依賴找到更多死代碼，區分兩者的代價和適用場景。

## 什麼是死碼

**死碼（Dead Code）**：其結果永遠不會影響程式輸出（return 值、I/O、volatile 寫）的指令。

```llvm
%dead = add i32 %x, 1   ; 如果 %dead 沒有任何使用者，這是死碼
store i32 %val, i32* %p  ; store 有副作用，永遠不是死碼（除非 p 是本地 alloca）
```

分類：

```
局部死碼：在一個基本塊內，指令的結果在同一塊內沒有後續使用
全局死碼：在整個函式中，指令的結果沒有任何使用者
無用控制流：永遠不會執行到的基本塊
```

## 簡單 DCE

SSA 讓簡單 DCE 變得非常容易：每條指令的使用者列表是現成的（DU chain）。

```cpp
// 簡單 DCE：逆序掃描，刪除 use count 為 0 的純指令
bool eliminateDeadCode(Function &F) {
    bool Changed = false;
    SmallVector<Instruction*> Worklist;
    
    for (auto &BB : F)
        for (auto &I : BB)
            if (I.use_empty() && !I.mayHaveSideEffects())
                Worklist.push_back(&I);
    
    while (!Worklist.empty()) {
        Instruction *I = Worklist.pop_back_val();
        if (!I->use_empty()) continue;
        
        // 刪除前，把操作數的使用計數減 1
        // 如果操作數也變成 use_empty，也加入 Worklist
        for (Use &U : I->operands()) {
            if (auto *OpI = dyn_cast<Instruction>(U)) {
                if (OpI->use_empty() && !OpI->mayHaveSideEffects())
                    Worklist.push_back(OpI);
            }
        }
        
        I->eraseFromParent();
        Changed = true;
    }
    return Changed;
}
```

`mayHaveSideEffects()` 排除了 store、call、fence 等有副作用的指令——這些即使 use count 為 0 也不能刪。

## 激進死碼消除（ADCE）

簡單 DCE 只看「值有沒有使用者」，但有一種更隱蔽的死碼：

```c
if (complicated_condition()) {
    expensive_computation();  // 這裡有 store
    // 但 store 的目標位置之後從未被讀取
}
```

整個 if 分支——包括條件判斷、昂貴的計算、store——都是死的，但 store 有副作用，簡單 DCE 不敢刪。

**ADCE** 從「必定影響輸出」的指令開始，反向追蹤真正需要的指令，把其他的都刪除。

### 控制依賴（Control Dependence）

**節點 Y 控制依賴（control-dependent）於節點 X**：

> X 是一個條件跳轉，且 Y 的執行取決於 X 走哪條邊。
> 精確定義：Y 後支配 X 的某個後繼，但 Y 不後支配 X 本身。

控制依賴告訴我們：「要讓 Y 執行到，X 的條件必須是特定值」。

換方向說：「如果 Y 是死的，那麼控制 Y 是否執行的 X 也可能是死的（如果 X 的其他後繼也是死的）」。

### ADCE 算法

```
1. 標記「必要指令（live）」：
   所有有副作用的指令（store, call, ret）

2. 從 live 指令逆向追蹤：
   - 數據依賴：live 指令的所有操作數 → 也標記為 live
   - 控制依賴：如果 live 指令 Y 在基本塊 B 中，
               找所有 B 控制依賴的條件跳轉 X → 標記 X 為 live

3. Worklist 迭代直到不動點

4. 刪除所有未標記的指令
   （無用的基本塊用 unreachable 替換，讓後續 pass 清理）
```

ADCE 能發現 DCE 找不到的死碼：整個只寫不讀的計算路徑、只影響被刪除值的條件分支。

## 後支配樹與控制依賴圖

計算控制依賴需要**後支配者樹（Post-Dominator Tree）**：

- 和支配者樹相同的構造，但方向相反（從 exit 出發）
- 後支配者樹可以用同樣的 Lengauer-Tarjan 算法計算（反轉 CFG 後跑一次）

**控制依賴圖（CDG）** = 所有控制依賴關係的圖，從後支配者樹推導。

```bash
# LLVM 中計算後支配樹
opt -passes="print<post-dom-tree>" /tmp/test.ll -o /dev/null 2>&1
```

## SSA 讓 DCE 特別簡單

在 SSA 中，`instruction->use_empty()` 就是完整的「dead」判定（對無副作用指令）。

不需要資料流分析，不需要 Gen/Kill。這是 SSA 帶來的最直接的工程優化。

比較：

| | 非 SSA | SSA |
|---|---|---|
| 判斷死碼 | 到達定義分析 + 活躍性分析 | `use_empty()` 即可 |
| 刪除後的傳播 | 重跑分析 | 從操作數的 use count 自動減少 |
| 複雜度 | O(n × 迭代次數) | O(被刪指令的操作數個數) |

## LLVM 的實作

- 簡單 DCE：`llvm/lib/Transforms/Scalar/DCE.cpp`
- ADCE：`llvm/lib/Transforms/Scalar/ADCE.cpp`（包括 unreachable block 消除）

```bash
clang -O0 -S -emit-llvm /tmp/dead_test.c -o /tmp/dead.ll
opt -S -passes="mem2reg,dce" /tmp/dead.ll -o /tmp/dce_out.ll
opt -S -passes="mem2reg,adce" /tmp/dead.ll -o /tmp/adce_out.ll
diff /tmp/dce_out.ll /tmp/adce_out.ll  # 看 ADCE 多刪了什麼
```

## 自我檢核

- [ ] 簡單 DCE：`use_empty() && !mayHaveSideEffects()` 的判定條件
- [ ] 刪除指令時逆向傳播：操作數可能也變成 dead
- [ ] ADCE 的兩種依賴：數據依賴 + 控制依賴
- [ ] 控制依賴的定義：後支配者樹的「邊界」
- [ ] SSA 讓 DCE 是 O(1) 判定，而非 O(n) 資料流分析

→ [Ch 15 全局值編號（GVN）](./15-gvn.md)
