# Ch 28 — 函式內聯（Inlining）

> 目標：理解內聯的 cost model，掌握 LLVM inliner 的決策因子，以及遞迴函式和 tail call 的特殊處理。

## 為什麼內聯

**函式呼叫的開銷**：

```
1. 保存/恢復 caller-saved 暫存器
2. 設置參數（放到特定暫存器/棧上）
3. 跳轉到被呼叫函式（icache miss 風險）
4. 建立 stack frame（push ebp, sub esp）
5. return 時的逆向過程
```

對於小函式，這些開銷可能比函式體本身還重。內聯把函式體複製到呼叫點，消除呼叫開銷。

更重要的是，內聯**打開了跨函式優化的大門**：內聯後，常數參數可以在被呼叫函式內傳播，死碼可以被消除，迴圈可以合並。

```c
// 沒內聯：clamp 的邊界條件在呼叫方是常數，但 clamp 看不到
int clamp(int x, int lo, int hi) { return x < lo ? lo : x > hi ? hi : x; }
int r = clamp(val, 0, 255);

// 內聯後：lo=0, hi=255 變成常數，SCCP 能消除多餘的比較
```

## Cost Model

內聯不是免費的，主要代價是**代碼膨脹**（code size 增加）：

```
每個呼叫點都複製一份函式體
→ icache 壓力增加
→ 二進位大小增加（對嵌入式很關鍵）
```

LLVM 的 inliner 計算一個 **inline cost**：

```
cost = 被呼叫函式的估計指令數
       × 每條指令的代價因子
       - 內聯帶來的優化收益（常數參數折疊、dead argument 消除等）

threshold = 根據 -O 等級和 caller/callee 的 hot/cold 狀態確定

if cost < threshold → 內聯
```

常數參數特別重要：如果呼叫點的參數是常數，內聯後 SCCP 能消除條件分支，被呼叫函式的一半代碼可能消失，實際 cost 比估計低得多。

## 啟發式因子

LLVM inliner 考慮多個因子：

```
函式大小：越小越值得內聯（小函式的呼叫開銷相對高）
呼叫次數：只呼叫一次的函式（one-call-site）強烈傾向內聯
Hot/Cold：在熱路徑（PGO 資訊）上的呼叫更值得內聯
常數參數：有常數參數的呼叫點，內聯後 SCCP 帶來額外收益
always_inline / noinline：程式碼中的強制指令
norecurse：不遞迴的函式更容易安全內聯
```

## 遞迴函式的處理

直接遞迴不能無限內聯，否則會死循環。

LLVM 的策略：

1. 不內聯直接遞迴（`f → f`）
2. 間接遞迴（`f → g → f`）在 SCC 中，也避免形成環

特例：**尾遞迴（Tail Recursion）** 可以轉換成迴圈（不是內聯，是優化：

```c
int factorial(int n, int acc) {
    if (n == 0) return acc;
    return factorial(n - 1, n * acc);   // 尾呼叫
}
// 轉換成：
// loop: if (n == 0) return acc; acc *= n; n--; goto loop;
```

LLVM 的 `TailCallElim` pass 做這個轉換。

## 內聯的優先順序

哪些呼叫點先內聯？

**always_inline 屬性**：立即內聯，不管 cost。

```c
__attribute__((always_inline)) static inline int square(int x) { return x*x; }
```

**One-call-site 優化**：如果一個函式只在一個地方被呼叫，直接內聯（函式可以消失），不看 cost。

**Hot path 優先**：PGO 資訊告訴 inliner 哪些呼叫是熱的，優先內聯。

## LLVM Inliner 的實作

LLVM 有兩個 inliner：

- `InlinerPass`（NPM）：主 inliner，SCC-based，處理整個 module
- `AlwaysInliner`：只處理 `always_inline` 標記的函式

```bash
# 觀察 inliner 的決策
clang -O2 -Rpass=inline -Rpass-missed=inline /tmp/inline_test.c -o /dev/null

# 禁用內聯
clang -O2 -fno-inline /tmp/test.c -o test

# 強制內聯（在 IR 上）
opt -passes="always-inline" input.ll -o output.ll
```

## 過度內聯的危害

內聯過多會導致：

- 代碼膨脹 → icache miss 增加
- 暫存器壓力增加（大函式需要更多暫存器同時 live）
- 編譯時間增加（更多代碼要優化）

`-Os`（優化代碼大小）會使用比 `-O2` 更保守的 threshold；`-Oz` 更激進地犧牲速度換大小。

## 自我檢核

- [ ] 內聯的兩個收益：消除呼叫開銷 + 打開跨函式優化機會
- [ ] Cost model：估計指令數 vs threshold（根據優化等級和 hot/cold 調整）
- [ ] 常數參數使內聯收益增加（SCCP 消除死分支）
- [ ] 遞迴函式不直接內聯；尾遞迴用 TailCallElim 轉迴圈
- [ ] `-Rpass=inline` 診斷哪些呼叫被內聯，哪些被拒絕及原因

→ [Ch 29 過程間常數傳播（IPCP）與函式特化](./29-ipcp.md)
