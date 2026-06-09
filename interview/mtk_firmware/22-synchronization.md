# Ch 22 — 同步：mutex/semaphore/critical section

> **目標**：搞懂 race condition、critical section、mutex、semaphore（binary vs counting）、spinlock 的差異與用途。這是多執行緒/並行的核心，OS 與韌體面試都高頻，也串 Ch 14/15（ISR/reentrancy）。

> **環境**：概念為主。前置：Ch 20（thread 共享記憶體）、Ch 15（reentrancy）。

## 為什麼考這個

多個執行緒共享記憶體（Ch 20）→ 同時改同一個變數 → 結果不確定（race condition）。同步機制（mutex/semaphore）就是解這個的工具。面試必考「race 是什麼」「mutex vs semaphore」——這直接關係到能不能寫對並行程式（韌體的 ISR 與 RTOS task 都會遇到）。

## 先建立直覺：兩個人同時改一個數

```
   共享變數 count = 0，兩個 thread 各做 count++

   count++ 其實是三步（非原子！）：
   1. 讀 count 到暫存器
   2. 暫存器 +1
   3. 寫回 count

   交錯執行（race）：
   Thread A 讀 count=0 ──┐
   Thread B 讀 count=0   │  ← 兩個都讀到 0
   A: 0+1=1, 寫回 count=1│
   B: 0+1=1, 寫回 count=1│  ← B 用它讀到的舊值 0
   結果 count=1（不是 2！）一次更新被覆蓋（lost update）
```

**race condition**：多個執行緒同時存取共享資源，結果取決於執行的交錯順序（不確定、不可重現）。根源是「`count++` 不是原子的」——讀-改-寫三步之間可能被打斷。這是 Ch 15 reentrancy、Ch 16 多執行緒題的核心。

## critical section（臨界區）

**臨界區**：存取共享資源的那段 code（如 `count++`）。同一時間**只能有一個執行緒進入臨界區**——這叫**互斥（mutual exclusion）**。

```c
   // 臨界區要被「保護」起來
   lock();         // 進入前上鎖
   count++;        // ← 臨界區：一次只一個執行緒
   unlock();       // 離開後解鎖
```

正確的臨界區保護要滿足（經典三條件）：
1. **互斥（mutual exclusion）**：同時只一個在臨界區。
2. **進展（progress）**：沒人在臨界區時，想進的能進（不無故擋）。
3. **有限等待（bounded waiting）**：想進的不會無限等（不餓死）。

mutex/semaphore 就是實現「互斥進入臨界區」的工具。

## mutex（互斥鎖）

**mutex（mutual exclusion lock）**：一把鎖，同時只有一個執行緒能持有。要進臨界區先 `lock`（拿鎖），出來 `unlock`（還鎖）；別人想拿被持有的鎖會 **block（阻塞等待）**。

```c
mutex_t m;
mutex_lock(&m);      // 拿鎖（被持有就 block 等）
count++;             // 臨界區
mutex_unlock(&m);    // 還鎖（喚醒等待者）
```

關鍵性質：
- **擁有權（ownership）**：誰 lock 就誰 unlock（mutex 有「持有者」概念）。
- 用於**互斥**（保護共享資源）。
- RTOS 的 mutex 常支援**優先序繼承**（防優先序反轉，Ch 18）。

## semaphore（信號量）

**semaphore**：一個計數器 + 兩個原子操作 `wait`（P，計數 -1，若變負則 block）和 `signal`（V，計數 +1，喚醒一個等待者）。

```
   wait(S):   S--; if (S < 0) block;       // 想用一個資源
   signal(S): S++; if (有人等) wake one;    // 釋放一個資源
```

兩種：

```
   binary semaphore（二元，0/1）：類似 mutex，用於互斥或「事件通知」
   counting semaphore（計數，0~N）：管理「N 個相同資源」（如 5 個緩衝格、3 個連線）
      初值 N → 可同時 N 個執行緒通過，第 N+1 個 block
```

用途：
- **互斥**（binary semaphore，但少了 mutex 的擁有權）。
- **資源計數**（counting，如限制同時 N 個存取）。
- **事件通知/同步**（一個執行緒 signal、另一個 wait——如 ISR signal、task wait，Ch 18）。
- **生產者-消費者**的緩衝管理（Ch 24）。

## mutex vs semaphore（最常考的對比）

| | mutex | semaphore |
|---|---|---|
| 本質 | 鎖（0/1，有擁有權） | 計數器（0~N） |
| 用途 | **互斥**（保護共享資源） | 互斥 + **資源計數** + **事件同步** |
| 擁有權 | 有（誰 lock 誰 unlock） | 無（任何執行緒可 signal） |
| 計數 | 只有鎖定/未鎖定 | 可計數多個資源 |
| 典型場景 | 保護臨界區 | 限制 N 個並行、ISR 通知 task、生產者消費者 |

核心區別記法：
- **mutex 是「鎖」**——有擁有權（誰拿誰還）、用於互斥（一個資源一次一人）。
- **semaphore 是「計數器」**——無擁有權、能管 N 個資源、也能做「通知/同步」（一邊 signal 一邊 wait）。

關鍵差異：**mutex 有擁有權（lock 和 unlock 必須同一執行緒），semaphore 沒有（signal 和 wait 可以是不同執行緒）**——所以「ISR signal、task wait」這種跨執行緒通知用 semaphore 不用 mutex。

## spinlock（自旋鎖）

**spinlock**：拿不到鎖時不 block（睡眠），而是**忙等（busy-wait，一直迴圈檢查）**直到拿到。

```
   mutex：拿不到鎖 → block（睡眠，讓出 CPU，被喚醒才醒）
   spinlock：拿不到鎖 → spin（一直迴圈問「好了沒」，不讓出 CPU）
```

對比：

| | mutex（block） | spinlock（spin） |
|---|---|---|
| 拿不到時 | 睡眠（讓出 CPU） | 忙等（佔著 CPU 空轉） |
| 適合 | 鎖會被持有較久 | 鎖只持有極短（多核、臨界區很小）|
| 開銷 | context switch（睡/醒）| 浪費 CPU 空轉，但省 context switch |

spinlock 適合「**多核 + 臨界區極短**」——因為睡眠/喚醒的 context switch 開銷比「短暫空轉」還大。但「單核 + spinlock」是災難（拿鎖的跑不了，等鎖的空轉佔著 CPU，deadlock）。kernel 裡常用 spinlock 保護極短的臨界區。

## 考古題詳解

### Q1：什麼是 race condition？舉例

<details>
<summary>詳解</summary>

race condition：多個執行緒同時存取共享資源，結果取決於執行的交錯順序（不確定、不可重現）。

例：兩 thread 各做 `count++`（讀-加-寫三步非原子）。兩個都讀到舊值 0、各 +1 寫回 1 → 一次更新丟失（應該 2 變成 1）。

根源：`count++` 不是原子操作，讀-改-寫之間可被打斷。

**考點**：race condition 定義 + 例子，必考（串 Ch 16 多執行緒）。
</details>

### Q2：mutex 和 semaphore 差在哪？

<details>
<summary>詳解</summary>

- **mutex**：鎖（0/1），**有擁有權**（誰 lock 誰 unlock），用於**互斥**（保護共享資源，一次一人）。
- **semaphore**：計數器（0~N），**無擁有權**（任何執行緒可 signal），用於互斥 + **資源計數**（N 個資源）+ **事件同步**（signal/wait 可不同執行緒）。

關鍵：mutex 有擁有權（用於互斥），semaphore 無擁有權（能做跨執行緒通知，如 ISR signal、task wait）。binary semaphore 雖類似 mutex 但少了擁有權。

**考點**：mutex vs semaphore，超高頻必考。
</details>

### Q3：critical section 的正確保護要滿足什麼？

<details>
<summary>詳解</summary>

三條件：
1. **互斥（mutual exclusion）**：同時只一個執行緒在臨界區。
2. **進展（progress）**：沒人在臨界區時，想進的能進（不無故擋）。
3. **有限等待（bounded waiting）**：想進的不會無限等（不餓死）。

**考點**：臨界區三條件。
</details>

### Q4：mutex 和 spinlock 差在哪？各適合什麼？

<details>
<summary>詳解</summary>

- **mutex**：拿不到鎖就 **block（睡眠，讓出 CPU）**，被喚醒才醒。適合鎖會被持有「較久」的情況。
- **spinlock**：拿不到鎖就 **spin（忙等，一直迴圈檢查，佔著 CPU）**。適合「多核 + 臨界區極短」——因為短暫空轉比睡眠/喚醒的 context switch 開銷小。

選擇：鎖持有久 → mutex（別空轉浪費 CPU）；多核 + 極短臨界區 → spinlock（省 context switch）。**單核別用 spinlock**（拿鎖的跑不了、等的空轉 → deadlock）。

**考點**：mutex vs spinlock（block vs spin），高頻。
</details>

### Q5：怎麼用 semaphore 限制「最多 3 個執行緒同時存取某資源」？

<details>
<summary>詳解</summary>

用 **counting semaphore，初值 = 3**：

```c
semaphore_t s = 3;        // 初值 3
// 每個執行緒：
wait(&s);                 // 計數 -1，前 3 個通過，第 4 個 block
access_resource();        // 最多 3 個同時在這
signal(&s);               // 計數 +1，喚醒一個等待者
```

初值 N 的 counting semaphore → 最多 N 個同時通過。這是 counting semaphore 的典型用途（管理 N 個相同資源）。

**考點**：counting semaphore 資源計數應用。
</details>

## 踩雷集錦

1. **以為 `count++` 是原子的**：是讀-改-寫三步，多執行緒會 race。要保護。
2. **mutex/semaphore 不分**：mutex 有擁有權（互斥）；semaphore 計數器（無擁有權，能計數+通知）。
3. **ISR 用 mutex 通知 task**：mutex 有擁有權（要同執行緒 lock/unlock），跨執行緒通知用 semaphore（Ch 18）。且 ISR 不能 block（Ch 14/15）。
4. **單核用 spinlock**：拿鎖的被搶占跑不了、等鎖的空轉佔 CPU → deadlock。
5. **臨界區太大**：鎖住太多 code → 並行度低（大家都在等鎖）。臨界區越小越好。
6. **忘了 unlock / signal**：lock 了沒 unlock → 別人永遠等（deadlock，Ch 23）。
7. **以為 volatile 能解 race**：volatile 只保證重讀，不保證原子性/互斥（Ch 3/15）。要 mutex/atomic。

## 速記

- **race condition**：多執行緒同時存取共享資源，結果依交錯順序（`count++` 非原子）。
- **臨界區**：存取共享資源的 code，要互斥進入；三條件：互斥/進展/有限等待。
- **mutex**：鎖（有擁有權，誰 lock 誰 unlock），用於互斥。
- **semaphore**：計數器（無擁有權），binary（互斥/通知）/ counting（管 N 個資源）；signal/wait 可跨執行緒（ISR↔task）。
- **mutex vs semaphore**：mutex 有擁有權（互斥）、semaphore 無（計數+跨執行緒通知）。
- **spinlock**：拿不到鎖忙等（不睡）；適合多核+極短臨界區；單核別用。
- volatile ≠ 同步（Ch 3）；要原子/互斥用 mutex/atomic。

## 自我檢核

- [ ] race condition 是什麼？用 `count++` 解釋為什麼會發生。
- [ ] mutex 和 semaphore 的核心差異是什麼（擁有權、用途）？
- [ ] 為什麼「ISR 通知 task」用 semaphore 不用 mutex？
- [ ] mutex 和 spinlock 差在哪？各適合什麼？單核能用 spinlock 嗎？
- [ ] 怎麼用 semaphore 限制最多 N 個同時存取？
- [ ] volatile 能解決 race condition 嗎？為什麼？

## 延伸閱讀

### 書籍

- **《Operating System Concepts (恐龍書)》** — Ch 6 Synchronization Tools
  - **讀哪幾章**：6.1–6.6（race、critical section、mutex、semaphore）。
  - **和本章的關聯**：同步的標準教材，本章權威。

- **《OSTEP》** — Locks（28）、Semaphores（31）
  - **讀哪幾章**：28（鎖、spinlock）、31（semaphore）。
  - **為什麼值得讀**：把鎖/semaphore 的實作與取捨講得很透。

### 文章

- **[面試紀錄 & 練習（聯發科）— HackMD](https://hackmd.io/@chiangkd/interview)**
  - **讀哪裡**：synchronization/mutex/semaphore 題。
  - **和本章的關聯**：MTK 面試的同步考點。

同步工具有了，下一章是同步的著名陷阱——deadlock，四條件與預防，OS 面試最高頻之一。

→ [Ch 23 deadlock](./23-deadlock.md)
