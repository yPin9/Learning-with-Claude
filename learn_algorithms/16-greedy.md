# Ch 16 — Greedy:什麼時候可以貪

> 目標:建立「能否貪」的判斷力。多數人用貪婪純靠直覺,直覺錯了就死不知道。

## 病:直覺貪婪,無法證明

面試常這樣:「感覺貪這個應該對」,寫完過了 sample test,提交掛一堆 edge case。為什麼?**貪婪沒證明就是賭博**。

這章目標是讓你寫貪婪時**能說出為什麼對**。面試官愛問「why does this work?」,答不出來等於沒寫。

## 貪婪 work 的兩個充分條件

學術上證貪婪正確通常靠:

### 1. Greedy Choice Property

**每一步做「當下最優」的選擇,能通往全局最優**。

典型:活動選擇(Activity Selection)——選最早結束的活動,剩下時間永遠夠多。

### 2. Exchange Argument(交換論證)

**假設有一個最優解沒採用貪婪選擇,證明把它替換成貪婪選擇後答案不會變差**。

典型:Huffman coding——把最不頻繁的兩個合併,交換論證證明這樣得到最短編碼。

## 面試實用版:三個啟發式信號

面試不要求嚴格證明,但要能用「白話證明」。找這三個訊號:

### 訊號 1:排序後掃一遍

先 sort,然後一次掃描就搞定——絕大多數貪婪題的外觀。

**範例**:

- Meeting Rooms (252):按 start 排序,看有沒有重疊
- Non-overlapping Intervals (435):按 end 排序,貪心保留結束最早的
- Minimum Number of Arrows (452):按 end 排序,打最早結束的氣球群

### 訊號 2:局部最優 = 全局最優

特例是「能換則換」的直覺——**把不是貪婪選擇的答案替換成貪婪選擇,結果不變差**。

### 訊號 3:具有「無後效性」

做了這步選擇,後續子問題不依賴「我是怎麼來到這」。貪婪跟 DP 的共同前提——不同的是 DP 要記所有子問題,貪婪只需要一個當下最優。

---

## 經典題:排序+掃

### Non-overlapping Intervals (435)

> 最少移除幾個區間能讓剩下互不重疊。

```python
def erase_overlap_intervals(intervals):
    intervals.sort(key=lambda x: x[1])    # 按 end 排
    kept = 0
    prev_end = float('-inf')
    for start, end in intervals:
        if start >= prev_end:
            kept += 1
            prev_end = end
    return len(intervals) - kept
```

**為什麼按 end 排**:每次選 end 最早的,留最多空間給後續。

**交換論證**:假設最優解沒選 end 最早的 A,而選了 B。既然 A 結束更早,把最優解中的 B 換成 A,不影響後續(A 結束更早,剩下空間 ≥ B 的情況),答案數量不變。所以選 A 至少同樣好。

這段白話要能在面試說出。

### Jump Game (55)

> `arr[i]` 是從 i 能跳的最大步數,能不能到 n-1。

```python
def can_jump(arr):
    reach = 0
    for i in range(len(arr)):
        if i > reach: return False
        reach = max(reach, i + arr[i])
    return True
```

**心法**:線性維持「目前能到達的最遠位置」,只要還在這範圍內就更新。

### Gas Station (134)

> 環形加油站,從哪個站出發能繞完一圈。

```python
def can_complete_circuit(gas, cost):
    total, tank, start = 0, 0, 0
    for i in range(len(gas)):
        diff = gas[i] - cost[i]
        total += diff
        tank += diff
        if tank < 0:
            start = i + 1   # 從下一站重試
            tank = 0
    return start if total >= 0 else -1
```

**心法**:
1. 若 `sum(gas) < sum(cost)`,無解。
2. 若 `sum(gas) >= sum(cost)`,**必存在**一個起點。
3. 從 0 開始,tank 變負數時把起點設為下一個——因為中間任何點出發也到不了這裡。

第三點是關鍵洞察,能說出就算懂貪婪。

---

## 經典題:優先選最優(heap)

### Task Scheduler (621)

> 有 cooldown 的任務調度,每種任務兩次之間隔 n。

**貪婪**:每輪選當前「剩餘數最多」的任務(heap)。

### Reorganize String (767)

> 重排字串使相鄰字元不同。

上一章(heap)已寫過。每次取剩餘最多的字元,但要等一輪才能 push 回。

### Minimum Cost to Hire K Workers (857)

Fix 一個 worker 的「wage/quality 比」為底,然後貪婪選 quality 最小的 k-1 人(max-heap 維持 top k-1 最小)。略。

---

## 經典題:看起來像貪婪但不是

### Coin Change (322)

> 最少枚硬幣湊出 amount。

**常見誤解**:「用最大面值貪就好」。

**反例**:硬幣 `[1, 3, 4]`,amount = 6。貪婪選 4 + 1 + 1 = 3 枚,但最優是 3 + 3 = 2 枚。

所以 Coin Change **不是貪婪題,是 DP**(Ch 19)。

**什麼時候可以貪**:硬幣系統有「matroid 結構」(例如美國硬幣 1, 5, 10, 25)。題目沒保證,就不能貪。

### Longest Increasing Subsequence

看起來能貪,但反例打死:`[4, 2, 4, 5, 3, 7]` 貪心選當前能接就接,會漏更優解。是 DP。

---

## 陷阱

### 陷阱 1:不 sort 就貪

90% 的貪婪題要先 sort。有人忘記 sort 直接掃。

### 陷阱 2:sort 的 key 錯

Non-overlapping 按 start 排 vs 按 end 排,結果完全不同。要能**解釋為什麼這個 key**。

### 陷阱 3:覺得「直覺對」就下手

直覺貪婪通常對一半。寫前嘗試一個小反例,看能不能推翻。推不翻再寫。

### 陷阱 4:DP 題硬貪

Coin Change、LIS 這類看似貪婪實際 DP。訊號:「最優選擇依賴於後續狀態」→ DP。

---

## 面試時的貪婪話術

寫完貪婪解,**主動**講這段:

> "My greedy choice is: at each step, pick X.
> To justify, I'd use an exchange argument: suppose the optimal doesn't pick X but picks Y. Swapping Y for X doesn't hurt because [具體理由]. So picking X is at least as good."

面試官聽完會覺得你懂。

---

## 自我檢核

- [ ] 貪婪 work 的兩個學術條件分別是什麼?
- [ ] Non-overlapping Intervals 為什麼按 end 排?能不能按 start 排?
- [ ] Gas Station 為什麼 tank 變負就從下一站重試?
- [ ] 寫一個能用貪婪解的題,然後換個變形使它不能貪。
- [ ] Coin Change 為什麼不能用貪婪?舉一個反例。

→ [Ch 17 Backtracking:剪枝才是關鍵](./17-backtracking.md)
