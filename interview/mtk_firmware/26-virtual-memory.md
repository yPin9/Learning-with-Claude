# Ch 26 — virtual memory 與置換

> **目標**：搞懂 demand paging（需求分頁）、page fault、page 置換演算法（FIFO/LRU/OPT）、Belady's anomaly、thrashing。置換演算法計算題（給 reference string 算 page fault 數）是 OS 經典計算題。

> **環境**：概念 + 計算。前置：Ch 25（paging）。

## 為什麼考這個

virtual memory 讓「程式比實體 RAM 大」成為可能——靠把暫時不用的 page 換到磁碟。但「要用時不在記憶體（page fault）怎麼辦」「記憶體滿了換誰出去（置換演算法）」是核心。面試愛考「給 reference string 用 LRU/FIFO 算 page fault 數」的計算題，和「thrashing 是什麼」。

## 先建立直覺：書桌放不下所有書

```
   你的書桌（實體 RAM）放不下所有書（程式的所有 page），但書架（磁碟/swap）可以。
   - 要用的書才放桌上（demand paging：用到才載入）
   - 桌上滿了要拿新書 → 把一本暫時不用的放回書架（page replacement）
   - 要用的書不在桌上 → 去書架拿（page fault，慢）
   - 如果一直在「拿書放書」（書一直換進換出）→ 沒時間讀（thrashing）
```

核心：**virtual memory 用「磁碟當記憶體的延伸」——只把需要的 page 放實體 RAM，其餘在磁碟。** 這讓程式能比實體 RAM 大，代價是「要用的不在 RAM 時要從磁碟載入（page fault，慢）」。

## demand paging 與 page fault

**demand paging**：page 不是一開始全載入，而是「**用到才載入**」（lazy）。

```
   存取一個 page：
   1. 查 page table，該 page 在 RAM（valid）→ 直接存取（快）
   2. 該 page 不在 RAM（invalid，在磁碟）→ page fault！
        a. OS 接管，從磁碟把該 page 載入一個空閒 frame
        b. （若沒空閒 frame → 用置換演算法選一個換出去）
        c. 更新 page table
        d. 重新執行那條指令
   → page fault 很慢（磁碟存取比 RAM 慢千萬倍）
```

**page fault** 不是錯誤——是「要的 page 不在 RAM，要從磁碟載入」的正常事件（只是慢）。page fault 率高 = 效能差（一直等磁碟）。

## page 置換演算法（計算題核心）

當 RAM 滿了、要載入新 page 時，要「換出」一個現有的 page。換誰？置換演算法決定：

### FIFO（First-In First-Out）

換出「最早載入」的 page（像佇列）。簡單，但**可能換掉常用的**（最早載入不代表不常用）。有 **Belady's anomaly**（下面）。

### LRU（Least Recently Used）

換出「最久沒被使用」的 page。基於 locality（最近用的可能再用、最久沒用的可能不用了）——**效果好、最常用**。但實作要追蹤每個 page 的使用時間（開銷），實際用近似（如 clock algorithm）。

### OPT（Optimal）

換出「**未來最久才會用到**」的 page。**理論最佳（page fault 最少）**，但**做不到**（要預知未來）——只當「最佳基準」來比較其他演算法有多接近。

### 計算範例：給 reference string 算 page fault

```
   reference string（存取的 page 序列）：1 2 3 4 1 2 5 1 2 3 4 5
   frame 數 = 3（RAM 只能放 3 個 page）

   FIFO（換最早載入的）：
   1 → [1]          fault
   2 → [1,2]        fault
   3 → [1,2,3]      fault
   4 → [2,3,4]      fault（換掉最早的 1）
   1 → [3,4,1]      fault（換掉 2）
   2 → [4,1,2]      fault（換掉 3）
   5 → [1,2,5]      fault（換掉 4）
   1 → [1,2,5]      hit
   2 → [1,2,5]      hit
   3 → [2,5,3]      fault（換掉 1）
   4 → [5,3,4]      fault（換掉 2）
   5 → [5,3,4]      hit
   → 9 faults
```

計算題步驟：**逐個存取，畫出 frame 的內容變化，標 hit/fault，數 fault 數。** 不同演算法（FIFO/LRU/OPT）換出的 page 不同，fault 數不同。LRU 要記「最久沒用」、OPT 要看「未來最久才用」。練熟畫表是關鍵。

## Belady's Anomaly（FIFO 的反常）

直覺：**frame 越多（RAM 越大），page fault 應該越少**（能放更多 page）。但 **FIFO 在某些 reference string 下，frame 增加反而 fault 增加**——這違反直覺，叫 Belady's anomaly。

```
   FIFO 可能：3 個 frame → 9 faults，但 4 個 frame → 10 faults！（反而變多）
```

**LRU 和 OPT 沒有 Belady's anomaly**（它們是 stack algorithm——frame 多時的 page 集合一定包含 frame 少時的，所以 fault 不會變多）。FIFO 不是 stack algorithm 所以會反常。面試問「Belady's anomaly」答「FIFO 特有，frame 增加 fault 反增；LRU/OPT 沒有」。

## thrashing（顛簸）

**thrashing**：系統花在「page 換進換出」的時間多於「真正執行」的時間——一直在 page fault、載入、又換出，幾乎沒進展。

```
   原因：同時跑太多 process、每個分到的 frame 太少
        → 每個 process 的 working set（正在用的 page 集合）放不下
        → 不斷 page fault → CPU 大半時間在等磁碟換頁 → 吞吐量崩潰

   現象：CPU 使用率低（都在等磁碟）、但磁碟超忙、系統超慢
```

解法：
- **working set model**：追蹤每個 process「最近用的 page 集合（working set）」，確保它放得下（給夠 frame）。
- **減少多工程度**：暫停/換出一些 process，讓剩下的有足夠 frame。
- 加實體 RAM。

面試問「thrashing 是什麼」答「過度換頁，執行時間都花在 page fault，靠 working set/降低多工解」。

## 考古題詳解

### Q1：什麼是 demand paging 和 page fault？

<details>
<summary>詳解</summary>

**demand paging**：page 用到才載入（lazy），不是一開始全載入。

**page fault**：存取的 page 不在 RAM（在磁碟）→ OS 從磁碟載入（必要時換出一個）→ 重執行指令。它不是錯誤，是正常事件，但很慢（磁碟比 RAM 慢千萬倍）。page fault 率高 = 效能差。

**考點**：demand paging + page fault，必考。
</details>

### Q2：FIFO、LRU、OPT 各換出哪個 page？哪個最好？

<details>
<summary>詳解</summary>

- **FIFO**：換最早載入的。簡單，但可能換掉常用的，有 Belady's anomaly。
- **LRU**：換最久沒用的（基於 locality）。效果好、最常用，但要追蹤使用時間（用 clock 近似）。
- **OPT**：換未來最久才用的。理論最佳（fault 最少），但要預知未來，做不到——只當基準。

最好（實際可用）：**LRU**（OPT 不可實作）。

**考點**：三置換演算法，必考。
</details>

### Q3：給 reference string `1 2 3 4 1 2 5 1 2 3 4 5`、3 frames，用 LRU 算 page fault 數

<details>
<summary>詳解</summary>

LRU（換最久沒用的）：
```
1 → [1]            fault
2 → [1,2]          fault
3 → [1,2,3]        fault
4 → [2,3,4]        fault（1 最久沒用，換 1）
1 → [3,4,1]        fault（2 最久沒用，換 2）
2 → [4,1,2]        fault（3 最久沒用，換 3）
5 → [1,2,5]        fault（4 最久沒用，換 4）
1 → [2,5,1]        hit（1 在，更新為最近用）
2 → [5,1,2]        hit
3 → [1,2,3]        fault（5 最久沒用，換 5）
4 → [2,3,4]        fault（1 最久沒用，換 1）
5 → [3,4,5]        fault（2 最久沒用，換 2）
→ 10 faults
```

步驟：逐個存取、追蹤「最久沒用」、換它、數 fault。

**考點**：LRU 計算題，必考（會給數字要你算）。
</details>

### Q4：什麼是 Belady's anomaly？哪些演算法有？

<details>
<summary>詳解</summary>

**Belady's anomaly**：frame 增加（RAM 變大）反而 page fault 增加——違反「記憶體越多越好」的直覺。

**只有 FIFO 有**。LRU 和 OPT 沒有（它們是 stack algorithm——frame 多時的 page 集合包含 frame 少時的，fault 不會變多）。

**考點**：Belady's anomaly，FIFO 特有，常考。
</details>

### Q5：什麼是 thrashing？怎麼解？

<details>
<summary>詳解</summary>

**thrashing**：系統花在 page 換進換出的時間多於執行——一直 page fault、載入、換出，幾乎沒進展。現象：CPU 使用率低（都在等磁碟）、磁碟超忙、系統超慢。

原因：同時跑太多 process、每個分到的 frame 太少，working set 放不下 → 不斷換頁。

解法：working set model（給夠 frame）、減少多工程度（暫停一些 process）、加 RAM。

**考點**：thrashing 定義 + 解法，常考。
</details>

## 踩雷集錦

1. **以為 page fault 是錯誤**：是正常事件（page 不在 RAM 要載入），只是慢。
2. **LRU 計算追蹤錯**：LRU 換「最久沒被存取」的——每次存取要更新該 page 為「最近用」。算錯多半是沒更新使用順序。
3. **以為 frame 越多 fault 一定越少**：FIFO 有 Belady's anomaly（反常）。LRU/OPT 才保證。
4. **OPT 以為可實作**：要預知未來，做不到，只當基準。實際用 LRU。
5. **混淆 thrashing 和一般慢**：thrashing 是「過度換頁佔掉執行時間」——CPU 閒但磁碟忙是特徵。
6. **page fault 和 segfault 搞混**：page fault 是 OS 正常處理（載入 page）；segmentation fault 是程式存取非法位址（錯誤、crash）。完全不同！

## 速記

- **demand paging**：用到才載入（lazy）。**page fault**：page 不在 RAM → 從磁碟載入（正常但慢），≠ segfault（非法存取，crash）。
- **置換演算法**：FIFO（最早載入/有 Belady）、**LRU（最久沒用/最常用/基於 locality）**、OPT（未來最久才用/最佳但不可實作/當基準）。
- **計算題**：逐個存取畫 frame 變化、標 hit/fault、數 fault。
- **Belady's anomaly**：frame 增加 fault 反增——**只有 FIFO 有**，LRU/OPT 沒有。
- **thrashing**：過度換頁、執行時間都耗在 page fault（CPU 閒磁碟忙）；解：working set/降多工/加 RAM。

## 自我檢核

- [ ] demand paging 和 page fault 是什麼？page fault 和 segfault 差在哪？
- [ ] FIFO/LRU/OPT 各換哪個？哪個最佳、哪個實際可用？
- [ ] 給 reference string 和 frame 數，你能算 FIFO/LRU 的 page fault 數嗎？
- [ ] Belady's anomaly 是什麼？哪些演算法有、哪些沒有、為什麼？
- [ ] thrashing 是什麼？現象和解法？

## 延伸閱讀

### 書籍

- **《Operating System Concepts (恐龍書)》** — Ch 10 Virtual Memory
  - **讀哪幾章**：10.2（demand paging）、10.4（置換演算法）、10.6（thrashing/working set）。
  - **和本章的關聯**：virtual memory 的標準教材 + 計算範例，本章權威。

- **《OSTEP》** — Swapping/Policies（21–22）
  - **讀哪幾章**：22（置換政策 FIFO/LRU/OPT/clock）。
  - **為什麼值得讀**：把置換演算法和 thrashing 講得最清楚。

記憶體管理完整了，下一章是 process 之間怎麼溝通——IPC（process 隔離後的橋樑）。

→ [Ch 27 IPC](./27-ipc.md)
