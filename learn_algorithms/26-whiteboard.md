# Ch 26 — 白板流程:clarify → example → brute → optimize → code → test

> 目標:把解題從「靈光乍現」變成「可預測的五步流程」。面試官評分重點不在 code 對錯,在流程。

## 為什麼要有流程

隨機應變的解題在面試中很危險:

- 題意搞錯卻以為懂了,寫一半才發現
- 想到一個解就開始 code,沒想過更優
- 沒跑 example,錯了也不知道
- 時間到了還沒寫完

**面試官要看的是你面對陌生題的穩定思考能力**。流程是給你和面試官共同的錨點——卡住了也能回到流程的下一步,而不是僵住。

---

## 五步流程

### Step 1: Clarify(2–3 分鐘)

**不 clarify 直接寫 code 的人被當場扣分**。

要 clarify 什麼:

- **輸入型別**:array of int? string 含哪些字元? 2D grid 有 `0/1` 還是其他值?
- **輸入範圍**:n 大約多大?有沒有上限?(這決定目標複雜度)
- **Edge case**:空輸入? n=1? 全相同? 負數? 溢位?
- **輸出格式**:回傳 bool / index / value? 多答案要任一還是全部?
- **假設**:輸入保證合法嗎?需要 validate?

**對話範例**:

> Me: "Just to clarify — the input is an array of integers, possibly negative, length up to 10^5. I need to return the indices of the pair, not the values. Is that right?"
>
> Interviewer: "Correct. And assume there's always exactly one valid pair."

這段對話花 30 秒,但避免 10 分鐘後才發現搞錯的災難。

### Step 2: Example(1–2 分鐘)

**自己造一個小 example,走一遍你的理解**。

```
Input: [2, 7, 11, 15], target = 9
Expected output: [0, 1]   # 2 + 7 = 9
```

**邊走邊講給面試官聽**。這一步常常幫你發現:「我剛才理解的跟題目不同」。

造 edge case:

```
Empty: []           → ???
One element: [5]    → ???
All same: [3, 3, 3] → ???
```

### Step 3: Brute Force(1–2 分鐘)

**先講最笨的解**,不要直接衝最優。

```
"Brute force: for each i, for each j > i, check if arr[i] + arr[j] == target.
That's O(n^2) time, O(1) space. Want me to code this first, or try optimizing?"
```

**為什麼要講 brute force**:

1. 證明你理解題意
2. 給你思考最優解的時間
3. 面試官會引導:有時他只要你寫 brute force + 講優化想法

### Step 4: Optimize(2–4 分鐘)

從 brute force 出發,問自己:

- **哪個操作重複了?** → hash / memo
- **搜尋空間有結構?** → 二分 / sort
- **子問題有重疊?** → DP
- **可以一次掃描?** → 雙指針 / sliding window / 前綴和
- **能「從答案倒推」?** → 二分答案 / 貪婪

**邊想邊講**:

> "The O(n²) comes from checking all pairs. For each i, I want to find if target - arr[i] exists. A hash set can do that in O(1). So if I build a hash while scanning, I can get O(n)."

### Step 5: Code + Test(剩下時間)

**Code 的三個原則**:

1. **命名要自解釋**:`prev`、`curr`、`seen`、`best`,不用 `a`、`x`、`temp`。
2. **一邊寫一邊說**:講你正在寫什麼、為什麼。
3. **邊界先處理**:`if not arr: return`。

**寫完不是完事,一定要 test**:

```
"Let me trace through with [2, 7, 11, 15], target = 9:
  i=0: arr[0]=2, looking for 7 — not in seen. Add 2.
  i=1: arr[1]=7, looking for 2 — found at index 0! Return [0, 1]. ✓"
```

**主動找 edge case**:

```
"Let me also check edge cases:
  - Empty array: my loop doesn't execute, returns None (or should I raise?).
  - Duplicates: [3, 3], target = 6 → finds 3 at index 0 when i=1. ✓"
```

---

## 常見翻車場景與應對

### 翻車 1:卡住沒想法

**不要僵住**。主動說:

> "I'm stuck on the optimization. Let me re-state the problem to make sure I understand..."
> "Let me try a different angle — what if I sort first?"

**承認卡住並描述卡在哪**,面試官可能給提示。

### 翻車 2:想到的解寫到一半發現錯

**不要硬改小 bug**,退出來重新審視:

> "Wait, this approach doesn't handle the case where... let me back up."

**刪掉重寫比硬補好**。面試官欣賞認錯能力。

### 翻車 3:複雜度分析被問住

寫完後主動分析:

> "Time complexity is O(n) — single pass through the array.
> Space is O(n) for the hash map in the worst case (all unique elements)."

如果不確定,誠實說:

> "The outer loop is n, the inner work is... let me think... yeah, each element is processed constant times amortized, so overall O(n)."

### 翻車 4:試著讓自己看起來很聰明

**不要背題解炫技**。「我看過這題,答案是 ...」——面試官會覺得你沒面試價值。

面對看過的題,要裝不認識,按流程走一遍:

- 照 clarify 問一次
- 照 example 跑一次
- 照 brute → optimize 推一次

面試官通常看得出有沒有實際思考。硬裝反而扣分。

---

## 時間分配建議(45 分鐘 1 題)

| 步驟 | 時間 |
|---|---|
| Clarify | 3 min |
| Example + brute force | 3 min |
| Optimize 討論 | 4 min |
| Code | 20 min |
| Test | 8 min |
| Complexity 討論 + Q&A | 7 min |

**Code 佔一半時間左右**。超過就是前面想得不夠或寫太慢。

兩題 45 分鐘的話,每題大約 22 min,上面每項對半。

---

## 口語劇本(記一些話術)

**Clarify 時**:

> "Before I dive in, let me make sure I understand. We have ... [重述]. Is that right?"
> "What's the expected range for n?"
> "Should I worry about [edge case]?"

**提出 brute force**:

> "Let me start with a brute force to confirm the logic, then we'll optimize."
> "The naive solution is O(n²)..."

**提出優化**:

> "I notice we're repeating [某種計算]. What if we cache / sort / use a hash to avoid that?"
> "Since the input is sorted, maybe binary search can help here."

**寫到一半修正**:

> "Hmm, actually this doesn't cover the case where ... Let me adjust."
> "Thinking again, I should handle [某情況] before this loop."

**自己 test**:

> "Let me walk through this with [範例] to verify..."
> "Edge cases I want to check: empty input, single element, duplicates, all negative."

**完成**:

> "That should be the solution. Time complexity is O(...), space is O(...)."
> "If you'd like, I can also think about a follow-up — what if the input is a stream instead of an array?"

---

## 溝通細節

- **不要沉默超過 30 秒**。想到一半也要講:「I'm trying to see if sliding window applies here, but I'm not sure because...」
- **卡住先說「我卡在 X」**,不要假裝還在思考。
- **面試官給提示要配合**。他說「有沒有可能 O(n)?」,你該反應「嗯那我想想能不能用 hash 避免重複比較」,而不是堅持原思路。
- **複雜度自己算自己提**,不要等問。

---

## 白板特性(線上 vs 實體)

**實體白板**:

- 寫大,字醒目
- 留空間改 code
- 用不同區域放:題目 / 思考 / code / test

**線上 IDE(Codility / Hackerrank / Coderpad)**:

- 沒語法高亮的要小心 typo
- 可以執行(有些平台)——但不要倚賴
- 複製貼上有時被禁

**實體比線上容易**:實體可以畫、可以亂寫。線上要全部 typed 出來,壓力大。提前熟悉平台。

---

## 自我檢核

- [ ] Clarify 階段至少問什麼五個問題?
- [ ] 為什麼要先講 brute force,不直接衝最優?
- [ ] 卡住 30 秒要做什麼動作?
- [ ] 寫完後自己 test 至少要跑什麼 case?
- [ ] 面對看過的題怎麼表現?

→ [Ch 27 常見坑](./27-pitfalls.md)
