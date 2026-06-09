# Ch 21 — process 排程

> **目標**：搞懂 CPU 排程演算法——FCFS、SJF、Round Robin、Priority、MLFQ，它們的取捨、preemptive vs non-preemptive、以及計算 waiting/turnaround time 的題目。排程是 OS 面試常考的計算題。

> **環境**：概念為主，一般 OS。前置：Ch 20（process 狀態）。對照 Ch 18（RTOS 排程）。

## 為什麼考這個

CPU 一次只能跑一個 process（單核），但有很多 process 等著——**排程器決定先跑誰**。不同演算法有不同的取捨（回應快 vs 公平 vs 吞吐量）。面試愛考「比較這些演算法」和「算 waiting time」的計算題，測你懂不懂排程的本質。

## 先建立直覺：餐廳怎麼安排客人

```
   FCFS（先到先服務）：排隊，先來先點餐 → 公平，但前面一個點很久後面全等
   SJF（最短工作優先）：先服務「點得快」的 → 平均等待短，但點很久的可能餓死
   Round Robin（輪流）：每人先給 5 分鐘，沒好就排到後面 → 公平、回應快
   Priority（優先序）：VIP 先 → 重要的先，但低優先可能餓死
```

排程的核心矛盾：**沒有完美的演算法**——回應快、公平、吞吐量高、不餓死，難以兼顧。每個演算法是不同的取捨。

## 關鍵名詞（計算題要用）

```
   Arrival Time（到達時間）：process 進入 ready queue 的時間
   Burst Time（執行時間）：process 需要的 CPU 時間
   Completion Time（完成時間）：process 跑完的時間
   Turnaround Time（周轉時間）= Completion - Arrival（從到達到完成總共多久）
   Waiting Time（等待時間）= Turnaround - Burst（在 ready queue 等了多久）
   Response Time（回應時間）= 第一次開始執行 - Arrival（等多久才第一次跑）
```

計算題通常求「平均 waiting time」或「平均 turnaround time」。記住：**Waiting = Turnaround - Burst = Completion - Arrival - Burst**。

## preemptive vs non-preemptive

```
   non-preemptive（非搶占）：process 開始跑就跑到完成或自己讓出（I/O）
      → 簡單，但一個長 process 卡住所有人

   preemptive（搶占）：OS 可以強制打斷正在跑的，換別的（時間到/更高優先來了）
      → 回應快、公平，但有 context switch 開銷
```

現代 OS 多用 preemptive（回應性重要）。RTOS（Ch 18）也是 preemptive（高優先要能搶）。

## 五個排程演算法

### FCFS（First-Come, First-Served）

先到先服務，non-preemptive。簡單、公平，但有 **convoy effect（護航效應）**——一個長 process 在前面，後面的短 process 全部等它，平均等待時間爆增。

### SJF（Shortest Job First）

最短工作優先——選 burst time 最短的先跑。**平均等待時間最短（理論最佳）**，但：
- 需要「預知 burst time」（實際做不到，只能估計）。
- **starvation（飢餓）**：長 process 可能永遠輪不到（一直有短的插隊）。
- 有 preemptive 版：**SRTF（Shortest Remaining Time First）**——新來的更短就搶占。

### Round Robin（RR）

每個 process 分一個 **time quantum（時間片）**，輪流跑；時間到沒跑完就排到隊尾。preemptive。
- **公平、回應快**（每個 process 很快得到一輪）。
- 關鍵是 **time quantum 大小**：太大 → 退化成 FCFS；太小 → context switch 開銷大（一直切換）。
- 適合分時系統（互動式）。

### Priority Scheduling

每個 process 有優先序，選最高優先的跑。可 preemptive（高優先來了搶占）或 non。
- 重要的先做。RTOS 用這個（Ch 18）。
- **starvation**：低優先可能餓死。
- 解法：**aging（老化）**——等越久優先序越高，避免餓死。

### MLFQ（Multi-Level Feedback Queue）

多個優先序佇列，process 在佇列間移動：
- 新 process 進高優先佇列；用完時間片沒做完就降到低優先佇列（懲罰 CPU-bound）。
- I/O-bound（常讓出 CPU）留高優先（獎勵互動式）。
- 兼顧回應性（互動式優先）和吞吐量，且不用預知 burst time——接近實際 OS（Linux CFS 是類似思路的演化）。

## 對比表

| 演算法 | 搶占 | 優點 | 缺點 |
|---|---|---|---|
| FCFS | 非 | 簡單、公平 | convoy effect（長的卡住短的）|
| SJF | 非 | 平均等待最短 | 要預知 burst、starvation |
| SRTF | 搶占 | 平均等待更短 | 要預知、starvation、切換多 |
| RR | 搶占 | 公平、回應快 | quantum 難調、切換開銷 |
| Priority | 可 | 重要的先 | starvation（用 aging 解）|
| MLFQ | 搶占 | 兼顧回應+吞吐、不用預知 | 複雜、要調參數 |

## 計算題範例

### 範例：FCFS 算平均 waiting time

```
   process  Arrival  Burst
   P1       0        5
   P2       1        3
   P3       2        8

   FCFS（按到達順序 P1→P2→P3）：
   P1: 0-5    完成5
   P2: 5-8    完成8
   P3: 8-16   完成16

   Waiting = Completion - Arrival - Burst:
   P1: 5-0-5 = 0
   P2: 8-1-3 = 4
   P3: 16-2-8 = 6
   平均 waiting = (0+4+6)/3 = 3.33
```

### 同一組用 SJF（non-preemptive）

```
   t=0: 只有 P1 到（選 P1）→ P1 跑 0-5
   t=5: P2(burst3), P3(burst8) 都到了，選短的 P2 → P2 跑 5-8
   t=8: P3 → 8-16

   Waiting: P1:0, P2:8-1-3=4, P3:16-2-8=6 → 平均 3.33（這組剛好同 FCFS）
   （換一組數字 SJF 通常比 FCFS 短）
```

計算題步驟：**畫 Gantt chart（時間軸）→ 算每個的 Completion → Waiting = Completion - Arrival - Burst → 平均。** 練熟畫 Gantt chart 是關鍵。

## 考古題詳解

### Q1：比較 FCFS、SJF、RR 的優缺點

<details>
<summary>詳解</summary>

- **FCFS**：先到先服務，簡單公平；但 convoy effect（長 process 卡住後面短的），平均等待可能很長。
- **SJF**：最短先做，平均等待最短（理論最佳）；但要預知 burst time（做不到）、長 process starvation。
- **RR**：輪流給時間片，公平、回應快（互動式好）；但 quantum 難調（太大像 FCFS、太小切換開銷大）。

沒有完美的——FCFS 簡單但不公平於短工作、SJF 最佳但不實際且餓死長工作、RR 公平但有開銷。

**考點**：排程演算法比較，必考。
</details>

### Q2：什麼是 starvation？哪些演算法有？怎麼解？

<details>
<summary>詳解</summary>

**starvation（飢餓）**：某個 process 永遠（或很久）得不到 CPU。

有 starvation 的：**SJF/SRTF**（長 process 一直被短的插隊）、**Priority**（低優先一直被高優先搶）。

解法：**aging（老化）**——process 等越久，優先序逐漸提升，最終一定輪到，避免餓死。

**考點**：starvation + aging，高頻。
</details>

### Q3：Round Robin 的 time quantum 怎麼選？太大太小各會怎樣？

<details>
<summary>詳解</summary>

- **太大**：每個 process 一次跑很久才換 → 退化成 **FCFS**（失去 RR 的回應性）。
- **太小**：頻繁 context switch → **切換開銷佔比過大**（CPU 都花在切換，沒做正事）。

要選一個平衡：通常讓 quantum 「比多數 process 的一次互動 burst 稍大」——回應快又不過度切換。

**考點**：RR quantum 取捨。
</details>

### Q4：算這組的平均 waiting time（FCFS）

```
P1: Arrival 0, Burst 4
P2: Arrival 1, Burst 3
P3: Arrival 2, Burst 1
```

<details>
<summary>詳解</summary>

FCFS 按到達順序 P1→P2→P3：
```
P1: 0-4   完成 4
P2: 4-7   完成 7
P3: 7-8   完成 8
Waiting = Completion - Arrival - Burst:
P1: 4-0-4 = 0
P2: 7-1-3 = 3
P3: 8-2-1 = 5
平均 = (0+3+5)/3 = 2.67
```

注意 P3 burst 只有 1，卻因 FCFS 等到 7 才跑（convoy effect）——若用 SJF 會好很多。

**考點**：FCFS 計算 + convoy effect。
</details>

### Q5：MLFQ 怎麼兼顧回應性和吞吐量？

<details>
<summary>詳解</summary>

多級佇列 + 反饋：
- 新 process 進高優先佇列；用完整個時間片沒做完 → 降到低優先（判定為 CPU-bound，懲罰）。
- 常主動讓出 CPU（I/O-bound、互動式）的 → 留在高優先（獎勵）。

效果：**互動式/I/O-bound（要回應快）留高優先得到快回應；CPU-bound（吞吐導向）沉到低優先用剩餘 CPU**。兼顧兩者，且不用預知 burst time（靠觀察行為動態調整）。接近真實 OS 排程。

**考點**：MLFQ 設計理念。
</details>

## 踩雷集錦

1. **Waiting time 公式記錯**：Waiting = Completion - Arrival - Burst（= Turnaround - Burst）。別漏 Arrival。
2. **SJF 以為實際可用**：要預知 burst time（做不到，只能估）。實際 OS 用 MLFQ 類（不用預知）。
3. **以為有完美排程**：每個演算法是取捨（公平/回應/吞吐/不餓死難兼顧）。
4. **忘了 starvation 和 aging**：SJF/Priority 會餓死，aging 解。
5. **RR quantum 不分大小影響**：太大像 FCFS、太小切換爆。
6. **計算題不畫 Gantt chart**：直接算容易錯。先畫時間軸再算。
7. **混淆 turnaround / waiting / response**：turnaround=完成-到達；waiting=turnaround-burst；response=第一次跑-到達。

## 速記

- 名詞：Turnaround=Completion-Arrival；**Waiting=Turnaround-Burst=Completion-Arrival-Burst**；Response=首次執行-Arrival。
- **FCFS**（簡單/convoy effect）、**SJF**（平均等待最佳/要預知+starvation）、**SRTF**（SJF 搶占版）、**RR**（公平回應快/quantum 取捨）、**Priority**（重要先/starvation→aging）、**MLFQ**（兼顧+不用預知，近真實 OS）。
- preemptive（搶占，回應快有開銷）vs non（簡單但長工作卡住）。
- starvation（SJF/Priority）用 **aging** 解。
- 計算題：畫 **Gantt chart** → Completion → Waiting → 平均。

## 自我檢核

- [ ] Waiting time 怎麼算？（公式）
- [ ] FCFS/SJF/RR 各的優缺點？convoy effect 是什麼？
- [ ] 什麼是 starvation？哪些演算法有？aging 怎麼解？
- [ ] RR 的 time quantum 太大/太小各會怎樣？
- [ ] 給一組 process，你能畫 Gantt chart 算出平均 waiting time 嗎？

## 延伸閱讀

### 書籍

- **《Operating System Concepts (恐龍書)》** — Ch 5 CPU Scheduling
  - **讀哪幾章**：5.1–5.3（演算法）、5.3 的計算範例。
  - **和本章的關聯**：排程的標準教材 + 計算範例。

- **《OSTEP》** — Scheduling（7）、MLFQ（8）
  - **讀哪幾章**：7（基本排程指標與演算法）、8（MLFQ）。
  - **為什麼值得讀**：把排程的取捨和 MLFQ 講得最清楚。

排程是「誰先跑」，下一章是多執行緒的核心問題——同步，race condition 與 mutex/semaphore。

→ [Ch 22 同步：mutex/semaphore/critical section](./22-synchronization.md)
