# Ch 23 — deadlock

> **目標**：徹底搞懂死鎖（deadlock）——四個必要條件、四種處理方式（預防/避免/偵測/復原）、banker's algorithm、以及和 livelock/starvation 的區別。這是 OS 面試最高頻的題目之一，幾乎必考。

> **環境**：概念為主。前置：Ch 22（mutex/semaphore）。對照 Ch 18（RTOS 優先序反轉）。

## 為什麼考這個

deadlock 是並行系統的經典災難——兩個執行緒互相等對方的鎖，永遠卡死。它測你懂不懂「資源競爭怎麼導致系統凍結」。面試幾乎必問「deadlock 的四個條件」「怎麼預防」——答得出四條件 + 破解方式 = 標準答案。

## 先建立直覺：兩個人搶兩支筷子

```
   兩個人吃飯，桌上兩支筷子，每人要兩支才能吃：
   人 A 拿了左筷子，等右筷子
   人 B 拿了右筷子，等左筷子
   → A 等 B 的右筷子、B 等 A 的左筷子 → 互相等、永遠吃不到 = deadlock

   程式版：
   Thread A: lock(m1); ... lock(m2);   // 拿了 m1，等 m2
   Thread B: lock(m2); ... lock(m1);   // 拿了 m2，等 m1
   → A 等 m2（B 持有）、B 等 m1（A 持有）→ 互相等、永遠卡死
```

deadlock：**一組執行緒，每個都在等「被組內其他執行緒持有」的資源，形成循環等待，全部卡死。** 這就是著名的 dining philosophers（哲學家就餐）問題（Ch 24）。

## 四個必要條件（必背，缺一不可）

deadlock 發生**必須同時**滿足四個條件（Coffman 條件）——只要破壞任一個，就不會 deadlock：

```
   1. 互斥（Mutual Exclusion）：資源一次只能一個執行緒持有（不可共享）
   2. 持有並等待（Hold and Wait）：持有資源的同時，又等待其他資源
   3. 不可剝奪（No Preemption）：資源不能被強制奪走，只能持有者自願釋放
   4. 循環等待（Circular Wait）：存在一個環——A 等 B、B 等 C、...、最後等回 A
```

記法（口訣）：**互斥、持有並等待、不可剝奪、循環等待**——四個都成立才會 deadlock。**破壞任一個就能預防**（下面）。「循環等待」是最直觀的——形成等待的環。

## 四種處理 deadlock 的方式

### 1. 預防（Prevention）—— 破壞四條件之一

讓四條件至少一個永遠不成立：

```
   破壞互斥：讓資源可共享（多數資源做不到，如鎖本質要互斥）
   破壞持有並等待：要嘛一次拿全部資源、要嘛沒拿到全部就不拿（不持有時等）
   破壞不可剝奪：拿不到就釋放已持有的（之後重試）
   破壞循環等待：給資源編號，規定「只能按編號遞增順序拿」← 最實用！
```

**最實用的是破壞循環等待——資源排序（lock ordering）**：規定所有執行緒「按固定順序拿鎖」（如永遠先 m1 再 m2）。這樣不可能形成環（不會有人先 m2 再 m1）。上面的筷子例子，若規定「都先拿左再拿右」就不會 deadlock。**這是實務上防 deadlock 最常用的方法**，面試愛問。

### 2. 避免（Avoidance）—— banker's algorithm

執行期動態判斷「給這個資源會不會導致 unsafe state（可能 deadlock）」，不安全就不給。

**Banker's algorithm（銀行家演算法）**：像銀行放貸——每次有人要資源，先檢查「給了之後，是否仍存在一個順序能讓所有人都拿到所需、跑完、還資源」（safe sequence）。存在就給（safe state），不存在就讓它等（避免進入可能 deadlock 的狀態）。

需要預知「每個執行緒最多需要多少資源」（max claim）。實務上少用（要預知、開銷大），但面試常考概念。

### 3. 偵測 + 復原（Detection & Recovery）

允許 deadlock 發生，定期**偵測**（找資源分配圖裡的環），發生了再**復原**：

```
   偵測：建資源分配圖（resource allocation graph），找環
   復原：
   - 終止執行緒（殺掉環裡的一個，釋放它的資源）
   - 資源剝奪（強制奪走某個資源給別人）
   - rollback（回到 deadlock 前的 checkpoint）
```

### 4. 忽略（Ostrich algorithm，鴕鳥演算法）

**假裝沒這回事**——deadlock 很少發生，處理成本高，乾脆不管，真卡死就重啟。聽起來荒謬，但**大多數通用 OS（Linux/Windows）就是這樣**——deadlock 預防/避免的開銷不值得，靠重啟解決。（韌體不行——埋在裝置裡不能隨便重啟，所以韌體更重視預防 + watchdog Ch 19。）

## deadlock vs livelock vs starvation（容易混）

```
   deadlock：互相等，全部「卡住不動」（沒人前進）
   livelock：互相讓，一直「動但沒進展」（如兩人走廊相讓，一直往同邊閃，誰都過不去）
   starvation：某個執行緒一直得不到資源（被別人插隊，餓死）——別人有前進，就它沒有
```

區別：
- **deadlock**：大家都不動（卡死）。
- **livelock**：大家都在動（不斷重試/相讓），但沒有人真正前進。
- **starvation**：別人在前進，但某個特定執行緒一直被忽略（Ch 21 SJF/Priority 的問題）。

面試可能問三者區別——deadlock 卡死、livelock 空忙、starvation 餓死。

## 考古題詳解

### Q1：deadlock 的四個必要條件是什麼？

<details>
<summary>詳解</summary>

四個（Coffman 條件），**同時**成立才 deadlock：
1. **互斥**：資源一次只一個執行緒持有。
2. **持有並等待**：持有資源時又等別的。
3. **不可剝奪**：資源不能被強奪，只能自願釋放。
4. **循環等待**：形成等待的環（A 等 B、...、等回 A）。

破壞任一個就能預防。

**考點**：四條件，OS 最高頻必考，要能背。
</details>

### Q2：怎麼預防 deadlock？最實用的方法是什麼？

<details>
<summary>詳解</summary>

預防 = 破壞四條件之一：
- 破壞互斥：資源可共享（多數做不到）。
- 破壞持有並等待：一次拿全部、或沒拿全就不拿。
- 破壞不可剝奪：拿不到就釋放已持有的。
- **破壞循環等待：資源排序（lock ordering）——規定按固定順序拿鎖** ← 最實用。

最實用的是 **lock ordering**：所有執行緒按固定順序（如資源編號遞增）拿鎖，就不可能形成環。實務防 deadlock 的首選。

**考點**：預防方法，尤其 lock ordering，高頻。
</details>

### Q3：什麼是 banker's algorithm？

<details>
<summary>詳解</summary>

deadlock **避免（avoidance）**的演算法——每次有執行緒要資源，先判斷「給了之後系統是否仍在 safe state」（存在一個順序能讓所有執行緒都拿到所需資源、跑完、還資源）。safe 就給，unsafe 就讓它等。

需要預知每個執行緒的最大資源需求（max claim）。像銀行放貸——確保放了還能讓所有人最終都還得起。實務少用（要預知、開銷），但概念常考。

**考點**：banker's algorithm（避免），概念題。
</details>

### Q4：處理 deadlock 有哪幾種策略？通用 OS 用哪個？

<details>
<summary>詳解</summary>

四種：
1. **預防（prevention）**：破壞四條件之一（如 lock ordering）。
2. **避免（avoidance）**：banker's algorithm 動態判斷 safe state。
3. **偵測+復原（detection & recovery）**：允許發生、找環、殺執行緒/剝奪/rollback。
4. **忽略（ostrich）**：假裝沒事，真卡死就重啟。

**通用 OS（Linux/Windows）用「忽略」**——deadlock 罕見，預防/避免開銷不值得，靠重啟。韌體不能隨便重啟，更重視預防 + watchdog（Ch 19）。

**考點**：四種策略 + 為什麼通用 OS 用鴕鳥，高頻。
</details>

### Q5：deadlock、livelock、starvation 差在哪？

<details>
<summary>詳解</summary>

- **deadlock**：互相等，全部卡死不動。
- **livelock**：互相讓/重試，一直在動但沒進展（如兩人走廊相讓一直閃同邊）。
- **starvation**：別人在前進，但某執行緒一直得不到資源（餓死，如 SJF 的長工作 Ch 21）。

差別：deadlock 大家不動、livelock 大家空忙、starvation 別人動就它不動。

**考點**：三者區別，進階題。
</details>

## 踩雷集錦

1. **四條件背不全**：互斥、持有並等待、不可剝奪、循環等待——四個都要。面試直接問，背熟。
2. **以為要全部破壞四條件**：破壞**任一個**就能預防 deadlock（四條件是「同時」成立才發生）。
3. **混淆 prevention 和 avoidance**：prevention 破壞條件（靜態設計）；avoidance（banker's）執行期動態判斷 safe state。
4. **混淆 deadlock/livelock/starvation**：卡死 vs 空忙 vs 餓死。
5. **不知道通用 OS 其實「忽略」deadlock**：Linux/Windows 多用鴕鳥（重啟）。但韌體要預防（watchdog 兜底）。
6. **lock ordering 沒遵守**：實務防 deadlock 靠「所有人按同順序拿鎖」，一個人不遵守就可能 deadlock。
7. **混淆 deadlock 和優先序反轉**（Ch 18）：deadlock 是互相等（環）；優先序反轉是高優先被低優先卡（不是環，是優先序問題）。

## 速記

- **deadlock**：一組執行緒互相等對方持有的資源，形成環，全部卡死。
- **四條件（必背，同時成立才發生）**：互斥、持有並等待、不可剝奪、循環等待。破壞任一個就預防。
- **四種處理**：預防（破條件，**lock ordering 最實用**）、避免（**banker's**，判 safe state）、偵測+復原（找環/殺執行緒）、忽略（鴕鳥，通用 OS 用，重啟）。
- **deadlock**（卡死）vs **livelock**（空忙不進展）vs **starvation**（餓死）。
- 韌體不能隨便重啟 → 重視預防 + watchdog（Ch 19）。

## 自我檢核

- [ ] deadlock 的四個必要條件是什麼？（要能完整背出）
- [ ] 怎麼預防 deadlock？最實用的方法（lock ordering）怎麼運作？
- [ ] banker's algorithm 在做什麼？屬於哪種策略（預防/避免/偵測）？
- [ ] 處理 deadlock 的四種策略？為什麼通用 OS 多用「忽略」？
- [ ] deadlock、livelock、starvation 差在哪？

## 延伸閱讀

### 書籍

- **《Operating System Concepts (恐龍書)》** — Ch 8 Deadlocks
  - **讀哪幾章**：8.1–8.6（四條件、prevention/avoidance/detection、banker's）。
  - **和本章的關聯**：deadlock 的標準教材，本章權威。

- **《OSTEP》** — Common Concurrency Problems（32）
  - **讀哪幾章**：32（deadlock 的四條件、預防、實務）。
  - **為什麼值得讀**：把 deadlock 預防（lock ordering）的實務講得好。

### 文章

- **[面試考古題 + 面試經驗 — HackMD](https://hackmd.io/@accdlab/HkBANw4PP)**
  - **讀哪裡**：deadlock 相關題。
  - **和本章的關聯**：MTK 面試愛考 deadlock（社群心得提到）。

deadlock 是同步陷阱，下一章用經典同步問題（生產者-消費者等）把 mutex/semaphore/deadlock 綜合應用。

→ [Ch 24 經典同步問題](./24-classic-sync-problems.md)
