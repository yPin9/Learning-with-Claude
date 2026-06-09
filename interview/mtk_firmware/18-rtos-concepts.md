# Ch 18 — RTOS 概念

> **目標**：搞懂 RTOS（即時作業系統）是什麼、和一般 OS 的差別、task/scheduler/priority、即時性與優先序反轉。韌體常跑在 RTOS 上，這章是嵌入式角度的 OS，和 Part 3 的一般 OS 互相對照。

> **環境**：概念為主，FreeRTOS 等 RTOS。前置：Ch 14（中斷）、Ch 15（reentrancy）。Part 3（OS）會深入一般 OS 概念。

## 為什麼考這個

很多韌體跑在 RTOS（如 FreeRTOS）上——它提供 task 排程、同步機制，讓你能寫多工的韌體。面試會問「RTOS 和一般 OS 差在哪」「什麼是即時性」「優先序反轉」。這些測你懂不懂「即時系統」的特殊要求——這是嵌入式有別於一般軟體的地方。

## 先建立直覺：RTOS 重點不是「快」，是「準時」

```
   一般 OS（Linux/Windows）的目標：吞吐量、公平、平均回應快
      → 但「最壞情況」可能很慢（偶爾卡頓沒關係，使用者忍受）

   RTOS 的目標：即時性 = 在「保證的時間內」回應
      → 重點是「可預測的最壞情況」，不是平均快
      → 安全氣囊的控制器：碰撞後必須在 X 毫秒內引爆，慢了人會死
      → 寧可平均慢一點，也要保證最壞情況不超時
```

關鍵：**RTOS 的「即時（real-time）」不是「很快」，是「可預測、有時間保證」（deterministic）。** 一個平均很快但偶爾卡 1 秒的系統，對需要「永遠在 10ms 內回應」的場景是不合格的。這是 RTOS vs 一般 OS 的根本差異。

## RTOS vs 一般 OS（必考對比）

| | 一般 OS（GPOS） | RTOS |
|---|---|---|
| 目標 | 吞吐量、公平、平均效能 | **可預測的即時回應（最壞情況保證）** |
| 排程 | 公平（time-sharing）、複雜 | **優先序為主、可搶占、可預測** |
| 大小 | 大（MB~GB） | 小（KB~MB，韌體記憶體有限） |
| 確定性 | 低（GC、swap、複雜排程→不可預測延遲）| **高（行為可預測、延遲有上限）** |
| 中斷延遲 | 較大、不保證 | 小且有保證 |
| 適用 | 桌面、伺服器、手機 App | 控制系統、感測器、即時裝置 |

核心差異：**RTOS 用「優先序+可搶占」的排程保證高優先 task 能即時執行；犧牲一般 OS 的「公平」換取「可預測」。**

## hard real-time vs soft real-time

```
   hard real-time（硬即時）：錯過 deadline = 系統失敗/災難
      例：安全氣囊、引擎控制、心律調節器
      → deadline 是絕對的，不能超

   soft real-time（軟即時）：錯過 deadline = 品質下降但可接受
      例：影音串流（偶爾掉幀）、UI 回應
      → deadline 是目標，偶爾超可容忍
```

面試可能問「hard vs soft real-time」——hard 是「超時就完蛋」，soft 是「超時降級但可忍」。

## task（任務）與排程

RTOS 的執行單位是 **task**（類似 thread，但更輕量）。每個 task 有：

```
   - 自己的 stack
   - 優先序（priority）
   - 狀態：Running（執行中）/ Ready（就緒待跑）/ Blocked（等資源/事件）/ Suspended
```

RTOS 的 **scheduler（排程器）** 決定「現在跑哪個 task」。最常見是**優先序搶占式排程（priority-based preemptive）**：

```
   規則：永遠跑「就緒的 task 中優先序最高的」
   搶占（preemptive）：高優先 task 一就緒，立刻搶占正在跑的低優先 task

   task A（高優先）在等資料 → Blocked
   task B（低優先）在跑
   資料來了（中斷）→ task A 變 Ready → 立刻搶占 B，A 開始跑
   A 跑完/再 Blocked → B 繼續
```

這保證**高優先的 task（緊急的事）能即時得到 CPU**——即時性的核心。對比一般 OS 的「公平輪流」（Ch 21），RTOS 是「優先序絕對優先」。

task 狀態轉換（和 Part 3 process 狀態類似，Ch 20）：

```
   Ready  ──(被排程)──> Running ──(等資源/delay)──> Blocked
     ↑                     │                          │
     └──(被搶占/時間到)─────┘      (資源就緒)──────────┘
```

## 優先序反轉（priority inversion，經典必考）

RTOS 的著名陷阱——高優先 task 反而被低優先 task 卡住：

```
   情境：task H（高優先）、task M（中）、task L（低）
   1. L 拿了一個 mutex（鎖住共享資源）
   2. H 就緒，搶占 L，但 H 也要那個 mutex → H 被擋（等 L 釋放）→ H Blocked
   3. M 就緒，搶占 L（M 比 L 高）→ M 開始跑
   4. 結果：H（最高優先）在等 L，但 L 被 M 卡著跑不了 →
      H 實際上被「比它低的 M」卡住了！這就是優先序反轉。
```

著名案例：**火星探路者號（Mars Pathfinder）** 就因優先序反轉導致系統反覆重啟。

解法：

- **優先序繼承（priority inheritance）**：當高優先 H 在等低優先 L 持有的鎖時，**暫時把 L 的優先序提升到 H**——讓 L 快點跑完釋放鎖，不被 M 卡住。釋放後 L 降回原優先序。
- **優先序天花板（priority ceiling）**：鎖有個「天花板優先序」，持鎖的 task 自動升到那個優先序。

多數 RTOS 的 mutex 支援優先序繼承。面試問「優先序反轉 + 怎麼解」答「priority inheritance」。

## RTOS 的同步機制（和 Part 3 對照）

RTOS 提供和一般 OS 類似的同步原語（Ch 22 深入）：

- **mutex**：互斥鎖（保護共享資源），RTOS 的 mutex 通常支援優先序繼承。
- **semaphore**：信號量（計數同步、task 間通知）。binary semaphore 常用於「中斷通知 task」。
- **queue / message queue**：task 間傳資料（也是 ISR 通知 task 的安全方式）。
- **event flags**：等待多個事件。

ISR 與 task 溝通（Ch 14 的 RTOS 版）：ISR 不能用會阻塞的 mutex（Ch 15 reentrancy），要用 RTOS 提供的 **ISR-safe API**（如 `xSemaphoreGiveFromISR`）——ISR 給 semaphore/queue 通知 task，task 醒來處理。

## 考古題詳解

### Q1：RTOS 和一般 OS（如 Linux）差在哪？

<details>
<summary>詳解</summary>

核心：**RTOS 追求「可預測的即時回應（最壞情況有保證）」，一般 OS 追求「吞吐量/公平/平均效能」。**

- 排程：RTOS 優先序搶占式（高優先即時跑）；一般 OS 公平分時。
- 確定性：RTOS 高（延遲有上限）；一般 OS 低（GC/swap/複雜排程→不可預測）。
- 大小：RTOS 小（KB~MB）；一般 OS 大。

關鍵：RTOS 的「即時」不是「快」，是「準時/可預測」。

**考點**：RTOS vs GPOS，必考。
</details>

### Q2：什麼是優先序反轉？怎麼解？

<details>
<summary>詳解</summary>

**優先序反轉**：高優先 task H 等一個低優先 task L 持有的鎖；此時中優先 task M 搶占 L → L 跑不了 → H 實際上被「比它低的 M」卡住。

解法：**優先序繼承（priority inheritance）**——H 等 L 的鎖時，暫時把 L 升到 H 的優先序，讓 L 快點釋放鎖（不被 M 搶占）。或優先序天花板。

著名案例：火星探路者號。

**考點**：優先序反轉 + priority inheritance，RTOS 經典題。
</details>

### Q3：hard real-time 和 soft real-time 差在哪？

<details>
<summary>詳解</summary>

- **hard real-time**：錯過 deadline = 系統失敗/災難（安全氣囊、引擎控制）。deadline 絕對不能超。
- **soft real-time**：錯過 deadline = 品質下降但可接受（影音串流掉幀、UI）。deadline 是目標。

**考點**：hard vs soft real-time。
</details>

### Q4：RTOS 的 task 有哪些狀態？

<details>
<summary>詳解</summary>

- **Running**：正在用 CPU 執行。
- **Ready**：就緒，等被排程（等更高優先的讓出 CPU）。
- **Blocked**：等資源/事件/delay（如等 mutex、等 queue 資料、vTaskDelay）。
- **Suspended**：被暫停（不參與排程）。

優先序搶占式排程：永遠跑 Ready 中優先序最高的；高優先一就緒立刻搶占。

（和 Part 3 的 process 狀態類似，Ch 20。）

**考點**：task 狀態，串 process 狀態。
</details>

### Q5：ISR 怎麼安全地通知一個 RTOS task？

<details>
<summary>詳解</summary>

ISR 不能用會阻塞的 mutex（Ch 15，ISR 不可阻塞/重入問題）。要用 RTOS 的 **ISR-safe API**——通常是 `...FromISR` 版本，如 `xSemaphoreGiveFromISR()` 或 `xQueueSendFromISR()`。ISR 給一個 binary semaphore / 送進 queue，原本 block 在那個 semaphore/queue 的 task 就被喚醒去處理。

這是 Ch 14 「ISR 設旗標、主程式處理」的 RTOS 版——ISR 快速通知、task 做耗時處理。

**考點**：ISR 與 task 溝通（ISR-safe API），串 Ch 14/15。
</details>

## 踩雷集錦

1. **以為 RTOS = 很快的 OS**：RTOS 是「可預測/準時」，不是「快」。重點是最壞情況有保證。
2. **以為 RTOS 排程也是公平輪流**：RTOS 是優先序搶占（高優先絕對優先），不是一般 OS 的公平分時。
3. **不知道優先序反轉**：高優先被低優先卡住的經典問題。解法 priority inheritance。
4. **ISR 用阻塞的 mutex 通知 task**：ISR 不可阻塞（Ch 15）。要用 `...FromISR` API。
5. **混淆 task 和 process**：RTOS task 更輕量（通常共享位址空間，無 MMU 保護），不像 OS process 各有獨立位址空間（Ch 20）。
6. **hard/soft real-time 不分**：hard 超時=災難、soft 超時=降級可忍。

## 速記

- **RTOS 重點是「可預測/準時（即時性）」不是「快」**——最壞情況有時間保證（deterministic）。
- RTOS vs GPOS：優先序搶占排程（vs 公平分時）、高確定性、小、低延遲；犧牲公平換可預測。
- hard real-time（超時=災難）vs soft（超時=降級可忍）。
- task 狀態：Running/Ready/Blocked/Suspended；優先序搶占（高優先一就緒立刻搶）。
- **優先序反轉**：高優先被低優先（被中優先卡住）擋住 → **priority inheritance**（暫升低優先）解。
- ISR 通知 task 用 **ISR-safe API**（`...FromISR`），不能用阻塞 mutex（Ch 15）。

## 自我檢核

- [ ] RTOS 和一般 OS 的根本差異是什麼？「即時」是指「快」嗎？
- [ ] 什麼是優先序反轉？怎麼解（priority inheritance 怎麼運作）？
- [ ] hard 和 soft real-time 差在哪？各舉一例。
- [ ] RTOS task 有哪些狀態？優先序搶占排程怎麼運作？
- [ ] ISR 怎麼安全地通知一個 task？為什麼不能用一般 mutex？

## 延伸閱讀

### 文件 / 書籍

- **[FreeRTOS Documentation](https://www.freertos.org/features.html)** — task、scheduler、mutex、queue
  - **讀哪裡**：Tasks、Queues、Semaphores/Mutexes、優先序繼承那幾節。
  - **和本章的關聯**：最流行的 RTOS，本章概念的具體實作參考。

- **《Real-Time Concepts for Embedded Systems》** — Qing Li
  - **讀哪幾章**：scheduling、priority inversion、同步章。
  - **為什麼值得讀**：RTOS 概念的系統教材，把即時性/優先序反轉講透。

### 案例

- **[What really happened on Mars (Pathfinder priority inversion)](http://www.cs.cmu.edu/~rajkumar/15-745/papers/pathfinder.html)** — Mike Jones
  - **這篇說什麼**：火星探路者號的優先序反轉事故與 priority inheritance 修復。
  - **為什麼值得讀**：優先序反轉最有名的真實案例，面試講這個加分。

RTOS 概念有了，Part 2 最後一章補韌體開發的實務面——低功耗、debug、watchdog。

→ [Ch 19 低功耗、debug 與韌體開發實務](./19-firmware-practice.md)
