# Ch 24 — 經典同步問題

> **目標**：用三個經典同步問題——producer-consumer（生產者消費者）、reader-writer（讀者寫者）、dining philosophers（哲學家就餐）——把 mutex/semaphore（Ch 22）和 deadlock（Ch 23）綜合應用。MTK 考古題會要你寫 producer-consumer 的 pseudo code。

> **環境**：概念 + pseudo code。前置：Ch 22（mutex/semaphore）、Ch 23（deadlock）。

## 為什麼考這個

這三個問題是同步的「標準考題」——它們把 race、mutex、semaphore、deadlock 整合在具體情境裡。能寫出 producer-consumer 的解法，證明你真的會用同步機制（不只背定義）。MTK 考古題直接要 pseudo code。

## 問題一：Producer-Consumer（生產者-消費者）

最重要、最常考。情境：

```
   生產者（producer）：產生資料，放進「有限大小的緩衝區（bounded buffer）」
   消費者（consumer）：從緩衝區取資料來用

   要解決：
   - buffer 滿了，生產者要等（不能塞爆）
   - buffer 空了，消費者要等（沒東西可取）
   - 生產者和消費者同時存取 buffer → 要互斥（race）
```

用**三個同步原語**解（經典解法）：

```c
   semaphore empty = N;      // 空格數（初值 = buffer 大小 N）
   semaphore full  = 0;      // 已填格數（初值 0）
   mutex     m;              // 保護 buffer 的互斥鎖

   // 生產者：
   produce(item);
   wait(empty);              // 等一個空格（buffer 滿則 block）
   wait(m);                  // 進臨界區（互斥存取 buffer）
       add item to buffer;
   signal(m);               // 出臨界區
   signal(full);            // 通知「多了一個資料」

   // 消費者：
   wait(full);              // 等一個資料（buffer 空則 block）
   wait(m);                 // 進臨界區
       remove item from buffer;
   signal(m);              // 出臨界區
   signal(empty);          // 通知「多了一個空格」
   consume(item);
```

三個原語各管什麼：
- **`empty`（counting semaphore）**：算「還有幾個空格」——生產者 `wait(empty)` 確保不塞爆。
- **`full`（counting semaphore）**：算「有幾個資料」——消費者 `wait(full)` 確保有東西取。
- **`mutex m`**：保護 buffer 本身（互斥存取，防 race，Ch 22）。

**關鍵：semaphore 的順序不能錯！** 必須**先 `wait(empty/full)`（資源計數）再 `wait(m)`（互斥鎖）**。如果反過來（先拿 mutex 再 wait empty），會 deadlock：生產者拿了 mutex 卻發現 buffer 滿要等 empty，但消費者要拿 mutex 才能取資料釋放空格——互相等（Ch 23）。順序是經典考點。

> 韌體版（Ch 14）：ISR↔主程式的 ring buffer 就是 producer-consumer 的簡化——ISR 是 producer、主程式是 consumer。單生產者單消費者時可 lock-free（各動各的指標，練習 B Q7）。

## 問題二：Reader-Writer（讀者-寫者）

情境：一份共享資料，多個 reader 可同時讀（讀不改，不衝突），但 writer 要獨佔（寫時不能有別人讀或寫）。

```
   規則：
   - 多個 reader 可同時讀（讀不互斥）
   - writer 要獨佔（寫時排除所有 reader 和其他 writer）
```

核心想法（reader 優先版）：

```c
   semaphore wrt = 1;        // 控制 writer 的獨佔存取
   mutex     m;              // 保護 read_count
   int read_count = 0;

   // writer：
   wait(wrt);                // 獨佔
       write;
   signal(wrt);

   // reader：
   wait(m); read_count++;
       if (read_count == 1) wait(wrt);   // 第一個 reader 鎖住 writer
   signal(m);
       read;                              // 多個 reader 可同時在這
   wait(m); read_count--;
       if (read_count == 0) signal(wrt); // 最後一個 reader 放開 writer
   signal(m);
```

關鍵：**用 read_count 計數 reader**——第一個 reader 進來時鎖住 writer（`wait(wrt)`），最後一個 reader 離開時才放（`signal(wrt)`）。中間多個 reader 可同時讀。

問題：這個「reader 優先」版本會讓 **writer starvation**（一直有 reader 進來，writer 永遠等不到）。有「writer 優先」和「公平」的變體。面試知道「reader-writer 要區分讀/寫鎖、reader 可共享、有 starvation 問題」即可。

> 實務對應：讀寫鎖（rwlock）——讀多寫少的場景（如快取）用它比 mutex 好（reader 不互斥，並行度高）。

## 問題三：Dining Philosophers（哲學家就餐）

deadlock 的經典示範（Ch 23）。情境：

```
   5 個哲學家圍圓桌，每兩人之間一支筷子（共 5 支），每人要「左+右兩支」才能吃。

   天真解法（每人先拿左、再拿右）→ deadlock！
   如果 5 個人同時都拿了左筷子 → 每人都等右筷子（被右邊的人拿了）→ 全部卡死（循環等待，Ch 23）
```

解法（破壞 deadlock 四條件，Ch 23）：

```
   方法1（破壞循環等待，最常用）：資源排序——筷子編號，規定「先拿編號小的」
        → 最後一個哲學家會先拿右（編號小的）→ 打破環

   方法2（破壞持有並等待）：要嘛同時拿兩支、要嘛都不拿（用 mutex 保護「拿筷子」這動作）

   方法3：限制最多 4 人同時上桌（counting semaphore 初值 4）
        → 至少一人能拿到兩支（鴿籠原理）

   方法4：奇數哲學家先拿左、偶數先拿右 → 打破對稱、破壞環
```

dining philosophers 是「deadlock 怎麼發生 + 怎麼破解四條件」的具體化（Ch 23）。面試考它通常是考 deadlock 的應用——能說出「天真解法為什麼 deadlock（循環等待）+ 一種破解（資源排序）」即可。

## 考古題詳解

### Q1：寫 producer-consumer 的 pseudo code

<details>
<summary>詳解</summary>

```c
semaphore empty = N;   // 空格
semaphore full  = 0;   // 資料
mutex m;

// producer:
produce(item);
wait(empty);           // 等空格
wait(m);               // 互斥
  buffer_add(item);
signal(m);
signal(full);          // 通知有資料

// consumer:
wait(full);            // 等資料
wait(m);               // 互斥
  item = buffer_remove();
signal(m);
signal(empty);         // 通知有空格
consume(item);
```

三原語：empty（counting，空格數）、full（counting，資料數）、m（mutex，保護 buffer）。**順序關鍵：先 wait(empty/full) 再 wait(m)**——反了會 deadlock。

**考點**：producer-consumer pseudo code，MTK 直接考（社群心得提到）。
</details>

### Q2：producer-consumer 裡，如果把 `wait(m)` 和 `wait(empty)` 順序對調會怎樣？

<details>
<summary>詳解</summary>

**會 deadlock。** 假設生產者先 `wait(m)`（拿到 mutex）再 `wait(empty)`，但此時 buffer 滿（empty=0）→ 生產者拿著 mutex 卡在等 empty。而消費者要 `wait(m)` 才能取資料釋放空格（signal empty），但 mutex 被生產者持有 → 消費者拿不到 mutex → 永遠不會 signal empty → 生產者永遠等 → deadlock（互相等，Ch 23）。

所以**必須先 wait 資源計數（empty/full）再 wait 互斥鎖（m）**。

**考點**：semaphore 順序與 deadlock，經典陷阱。
</details>

### Q3：reader-writer 問題的核心是什麼？有什麼問題？

<details>
<summary>詳解</summary>

核心：**多個 reader 可同時讀（讀不互斥），writer 要獨佔。** 用 read_count 計數 reader——第一個 reader 鎖住 writer、最後一個放開，中間多 reader 並行讀。

問題：「reader 優先」版本會 **writer starvation**（一直有 reader，writer 等不到）。有 writer 優先/公平的變體。

實務對應：讀寫鎖（rwlock），讀多寫少場景比 mutex 好。

**考點**：reader-writer 概念 + starvation。
</details>

### Q4：dining philosophers 為什麼會 deadlock？怎麼解？

<details>
<summary>詳解</summary>

deadlock 原因：每人「先拿左再拿右」，若 5 人同時拿左 → 每人都等右（被右鄰拿走）→ 循環等待（Ch 23 四條件之一）→ 全卡死。

解法（破壞四條件）：
- **資源排序**：筷子編號，規定先拿編號小的（打破循環等待）。
- 限制最多 4 人上桌（counting semaphore=4，至少一人能拿兩支）。
- 奇偶哲學家拿筷順序相反（打破對稱）。
- 同時拿兩支或都不拿（破壞持有並等待）。

**考點**：dining philosophers = deadlock 應用，串 Ch 23。
</details>

### Q5：producer-consumer 用 mutex 一把鎖（不用 empty/full semaphore）行嗎？

<details>
<summary>詳解</summary>

**不行（或很差）。** mutex 只能做互斥，不能做「等到有空格/有資料」的條件等待。若只用 mutex：buffer 滿時生產者怎麼辦？只能拿著鎖 busy-wait（空轉檢查，超耗 CPU）或放鎖重試（複雜）。

empty/full 是 **counting semaphore**——它們能讓生產者/消費者在「沒空格/沒資料」時 **block（睡眠等待）**，有了才被喚醒。這是 semaphore 比 mutex 多的「計數 + 條件等待」能力（Ch 22）。所以 producer-consumer 需要 semaphore（不只 mutex）。

（也可用 mutex + condition variable 達成，但概念上需要「條件等待」機制。）

**考點**：為什麼需要 semaphore（不只 mutex）——條件等待，串 Ch 22。
</details>

## 踩雷集錦

1. **producer-consumer 的 semaphore 順序反了**：先 wait(m) 再 wait(empty) → deadlock。必須先資源計數再互斥鎖。
2. **只用 mutex 做 producer-consumer**：mutex 不能條件等待（等空格/資料），要 counting semaphore。
3. **reader-writer 以為 reader 也要互斥**：reader 之間不互斥（讀不改），可同時讀；只有 writer 獨佔。
4. **忽略 starvation**：reader 優先 → writer 餓死；SJF/Priority → 工作餓死（Ch 21）。
5. **dining philosophers 天真解法**：每人先拿左 → 循環等待 deadlock。要破壞四條件。
6. **混淆 empty/full 的初值**：empty 初值 = N（一開始全空）、full 初值 = 0（一開始沒資料）。反了就錯。

## 速記

- **producer-consumer**：`empty`(counting, 初值N) + `full`(counting, 初值0) + `mutex m`。**順序：先 wait(empty/full) 再 wait(m)**（反了 deadlock）。需要 semaphore（mutex 不能條件等待）。
- **reader-writer**：多 reader 可共享讀、writer 獨佔；用 read_count（第一個 reader 鎖 writer、最後一個放）。有 writer starvation。對應 rwlock。
- **dining philosophers**：每人拿左右兩筷，天真解法循環等待 deadlock；破解用資源排序/限人數/奇偶相反（破壞四條件，Ch 23）。

## 自我檢核

- [ ] 能寫出 producer-consumer 的 pseudo code（empty/full/mutex）嗎？順序為什麼重要？
- [ ] 為什麼 producer-consumer 需要 semaphore 而不能只用 mutex？
- [ ] reader-writer 的核心規則是什麼？有什麼 starvation 問題？
- [ ] dining philosophers 為什麼 deadlock？舉一種解法。
- [ ] empty 和 full semaphore 的初值各是多少？為什麼？

## 延伸閱讀

### 書籍

- **《Operating System Concepts (恐龍書)》** — Ch 7 Synchronization Examples
  - **讀哪幾章**：7.1（bounded-buffer/producer-consumer）、7.2（reader-writer）、7.3（dining philosophers）。
  - **和本章的關聯**：三個經典問題的標準解法，本章權威。

- **《OSTEP》** — Semaphores（31，含 producer-consumer/reader-writer）
  - **讀哪幾章**：31。
  - **為什麼值得讀**：用 semaphore 逐步推導 producer-consumer 的解法，很清楚。

### 文章

- **[聯發科 C語言測試題目 / 面試考古題 — HackMD](https://hackmd.io/@chiangkd/interview)**
  - **讀哪裡**：producer-consumer / 同步題。
  - **和本章的關聯**：MTK 考 producer-consumer pseudo code。

經典問題綜合完，下一章轉到 OS 的另一大塊——記憶體管理，paging/segmentation 與碎片。

→ [Ch 25 記憶體管理 paging/segmentation](./25-memory-management.md)
