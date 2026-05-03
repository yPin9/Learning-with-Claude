# Ch 35 — State Machine：字串解析、遊戲邏輯

> 目標：識別需要「狀態追蹤」的問題，能用 State Machine 模型清楚地定義狀態和轉移。

## State Machine 的思維

State Machine（狀態機）：系統在任意時刻處於某個「狀態」，接收輸入後，根據轉移規則切換到新狀態。

**三個要素**：
- **States**：所有可能的狀態集合
- **Transitions**：從狀態 A 在輸入 X 下切換到狀態 B
- **Accept states**：代表「成功」的狀態

## 題目 1：Best Time to Buy and Sell Stock with Cooldown（LeetCode 309）

**題目**：股票，賣出後需要冷卻一天才能再買。求最大利潤。

**狀態定義**：
- `held`：持有股票時的最大利潤
- `sold`：剛賣出股票（昨天賣的）時的最大利潤
- `rest`：不持有、且不是剛賣出（冷卻期結束或一直沒有股票）時的最大利潤

**轉移**：

```
每天的轉移：

held → held（繼續持有）或 rest → held（買入，rest 代表今天可以買）
sold → rest（冷卻中，明天 rest）
held → sold（賣出）
rest → rest（繼續休息）
```

```cpp
int maxProfit(vector<int>& prices) {
    int held = INT_MIN, sold = 0, rest = 0;

    for (int price : prices) {
        int prevHeld = held, prevSold = sold, prevRest = rest;
        held = max(prevHeld, prevRest - price);  // 繼續持有，或從 rest 狀態買入
        sold = prevHeld + price;                  // 從持有變賣出
        rest = max(prevRest, prevSold);           // 繼續 rest，或從 sold 冷卻完
    }
    return max(sold, rest);
}
```

**關鍵**：每次更新必須用「上一輪的舊值」（`prevHeld` 等），否則順序不同會影響結果。

## 題目 2：Valid Number（LeetCode 65）

**題目**：判斷字串是否是合法數字（整數、小數點、科學記數法 e/E）。

狀態機比 if-else 堆疊更清晰：

```
狀態：
  START → 開始
  SIGN  → 看到符號 +/-
  INT   → 整數部分
  DOT_NO_INT → 小數點（前面沒有數字）
  DOT   → 小數點（前面有數字）
  FRAC  → 小數點後的數字
  E     → 看到 e/E
  E_SIGN → e 後面的符號
  E_INT → e 後面的整數

Accept states：INT, DOT, FRAC, E_INT
```

這類題不需要記，面試中能把狀態畫清楚就夠了。

## 題目 3：Implement strStr()（LeetCode 28）

查找子字串的 KMP 算法也是一種狀態機：

- 狀態 = 已匹配的前綴長度
- 失敗函數（`fail[]` 或 `next[]`）定義了「匹配失敗時回退到哪個狀態」

```cpp
int strStr(string haystack, string needle) {
    int n = haystack.size(), m = needle.size();
    if (m == 0) return 0;

    // 建立 KMP failure function
    vector<int> fail(m, 0);
    for (int i = 1; i < m; i++) {
        int j = fail[i-1];
        while (j > 0 && needle[i] != needle[j]) j = fail[j-1];
        if (needle[i] == needle[j]) j++;
        fail[i] = j;
    }

    // 匹配
    int j = 0;
    for (int i = 0; i < n; i++) {
        while (j > 0 && haystack[i] != needle[j]) j = fail[j-1];
        if (haystack[i] == needle[j]) j++;
        if (j == m) return i - m + 1;
    }
    return -1;
}
```

KMP 面試不常考，但原理值得理解。

## 何時用 State Machine？

- 需要追蹤「當前處於哪個階段」
- 輸入序列有複雜的合法 / 非法規則
- 不同狀態下同樣的輸入有不同的行為
- 股票問題（持有 / 未持有 / 冷卻等狀態）

## State Machine 的實作方式

**方式一（推薦）**：直接用變數表示每個狀態的最優值，每輪更新。（Stock 問題的做法）

**方式二**：用 `enum` 定義狀態，`switch` 或 map 定義轉移。（字串解析的做法）

**方式三**：DP 陣列 `dp[i][state]`（當狀態多時）。

## 自我檢核

- [ ] 能說出 Stock with Cooldown 的三個狀態分別代表什麼
- [ ] 能解釋為什麼更新 `held` 時必須用 `prevRest` 而不是當輪的 `rest`
- [ ] 能識別哪類問題適合用 State Machine 建模
- [ ] 知道 KMP 的「狀態 = 已匹配前綴長度」的含義

→ [Ch 36 位元操作（Bit Manipulation）](./36-bit-manipulation.md)
