# Ch 12 — Stack 基礎：括號、計算器類題

> 目標：認識 Stack 在括號匹配和表達式計算中的應用，建立「後進先出解決配對問題」的直覺。

## Stack 的本質

Stack（堆疊）是 LIFO（Last In First Out）的資料結構。

最適合解決的問題：**「最近未配對的 X」**。

當你需要「記住最近看到的、還沒有被處理的東西」時，Stack 就是答案。

## 場景 1：括號匹配

**題目（LeetCode 20）**：判斷括號字串是否合法。合法的括號：每個左括號都有對應的右括號，且順序正確。

**直覺**：遇到左括號，push 進去等待。遇到右括號，pop 出最近的左括號，看看是否匹配。

```
input: "({[]})"

( → push '('
{ → push '{'
[ → push '['
] → pop '[', 匹配 ✓
} → pop '{', 匹配 ✓
) → pop '(', 匹配 ✓
stack 空 → 合法
```

```cpp
bool isValid(string s) {
    stack<char> st;

    for (char c : s) {
        if (c == '(' || c == '{' || c == '[') {
            st.push(c);
        } else {
            if (st.empty()) return false;  // 沒有對應的左括號
            char top = st.top(); st.pop();
            if (c == ')' && top != '(') return false;
            if (c == '}' && top != '{') return false;
            if (c == ']' && top != '[') return false;
        }
    }

    return st.empty();  // stack 不空代表有未配對的左括號
}
```

## 場景 2：每日溫度（Next Greater Element）

**題目（LeetCode 739）**：`temperatures[i]` 是第 i 天的溫度。對每天，找「幾天後才會遇到更高的溫度」。

這是 Monotonic Stack 的入門版，但先用基本 stack 思考：

維護一個 stack 存「還沒找到更高溫度的日子的 index」。
當當天溫度比 stack top 的溫度更高，就 pop 並計算天數差。

```cpp
vector<int> dailyTemperatures(vector<int>& t) {
    int n = t.size();
    vector<int> ans(n, 0);
    stack<int> st;  // 存 index

    for (int i = 0; i < n; i++) {
        while (!st.empty() && t[i] > t[st.top()]) {
            int idx = st.top(); st.pop();
            ans[idx] = i - idx;
        }
        st.push(i);
    }
    return ans;
}
```

Stack 裡存的是 index 不是溫度值，因為計算天數差需要 index。

## 場景 3：基本計算器（含括號）

**題目（LeetCode 224）**：計算包含 `+`、`-`、括號和整數的表達式。

Stack 用來處理括號的「符號翻轉」：

```
"1-(2+3)" = 1 - (2+3) = 1 - 5 = -4

進入 ( 之前，把當前的符號方向（正/負）和當前結果存入 stack
離開 ) 時，把 stack 的累積結果和符號方向取出，繼續計算
```

```cpp
int calculate(string s) {
    stack<int> st;
    int result = 0, num = 0, sign = 1;

    for (char c : s) {
        if (isdigit(c)) {
            num = num * 10 + (c - '0');
        } else if (c == '+') {
            result += sign * num;
            num = 0; sign = 1;
        } else if (c == '-') {
            result += sign * num;
            num = 0; sign = -1;
        } else if (c == '(') {
            st.push(result);   // 存之前的結果
            st.push(sign);     // 存進括號前的符號
            result = 0; sign = 1;
        } else if (c == ')') {
            result += sign * num;
            num = 0;
            result *= st.top(); st.pop();  // 乘以進括號前的符號
            result += st.top(); st.pop();  // 加上之前的結果
        }
    }
    return result + sign * num;
}
```

## Stack 的應用辨識

看到這些就考慮 Stack：
- 括號 / 配對問題（最近的未配對項）
- 表達式求值（記錄中間狀態）
- 「下一個更大 / 更小的元素」→ Monotonic Stack（下一章）

## 常見錯誤

- pop 前忘記檢查 `st.empty()`，導致 undefined behavior
- 括號題最後忘記檢查 `return st.empty()`（有未配對的左括號）
- Stack 存 index 還是值要想清楚（通常存 index 更靈活）

## 自我檢核

- [ ] 能從頭寫出括號匹配（不看筆記）
- [ ] 理解 Daily Temperatures 為什麼存 index 而不是溫度值
- [ ] 能解釋括號計算器中 Stack 存放的兩層資訊是什麼
- [ ] 知道什麼問題特徵暗示要用 Stack

→ [Ch 13 Monotonic Stack：核心思維](./13-monotonic-stack.md)
