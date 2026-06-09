# Ch 20 — process vs thread

> **目標**：講清楚 process（行程）和 thread（執行緒）的差異、各自有什麼、context switch 的成本、何時用哪個。這是 OS 面試的開場必考題，也是後面同步（Ch 22）的前提。

> **環境**：概念為主，一般 OS（Linux）。前置：Ch 11（記憶體模型）。

## 為什麼考這個

「process 和 thread 差在哪」幾乎是 OS 面試的標配開場題——它測你懂不懂「OS 怎麼組織執行單元、什麼是共享什麼是獨立」。答得清楚，後面的同步、race condition 才有基礎。

## 先建立直覺：公司 vs 員工

```
   process（行程）= 一間公司
   - 有自己獨立的辦公室（位址空間/記憶體）
   - 有自己的資源（開啟的檔案、資源）
   - 公司之間是隔離的（一間倒了不影響另一間）

   thread（執行緒）= 公司裡的員工
   - 同一間公司的員工「共用辦公室」（共享 process 的記憶體）
   - 每個員工有自己的桌子（各自的 stack、暫存器、PC）
   - 員工之間溝通容易（共用空間），但會搶資源（race condition）
```

核心：**process 是「資源擁有的單位」（獨立記憶體），thread 是「執行/排程的單位」（共享 process 的記憶體）。一個 process 可有多個 thread。**

## 各自擁有什麼（必背）

```
   process 擁有（每個 process 獨立）：
   - 獨立的位址空間（code/data/heap/stack，Ch 11）
   - 開啟的檔案、資源
   - PID

   thread 擁有（每個 thread 獨立）：
   - 自己的 stack（區域變數、函式呼叫）
   - 自己的暫存器、PC（program counter）
   - thread ID

   同一 process 的 threads 共享（重點！）：
   - code 段、data 段、heap（共享記憶體！→ 這是 race 的根源）
   - 開啟的檔案、全域變數
```

關鍵差異：**同一 process 的 threads 共享記憶體（heap、全域變數、code）**——所以它們溝通容易（直接讀寫共享變數），但也容易撞（race condition，要同步，Ch 22）。process 之間記憶體隔離——所以安全（一個崩不影響另一個），但溝通要透過 IPC（Ch 27）。

## process vs thread 對比表

| | process | thread |
|---|---|---|
| 記憶體 | 獨立位址空間 | 共享 process 的記憶體 |
| 建立成本 | 高（複製整個位址空間等） | 低（只建 stack/暫存器） |
| context switch | 慢（要換位址空間、TLB flush） | 快（同位址空間，不用換） |
| 溝通 | IPC（pipe/shm/...，Ch 27），較慢 | 直接讀寫共享記憶體，快 |
| 隔離/安全 | 高（一個崩不影響別的） | 低（一個 thread 崩可能拖垮整個 process） |
| 同步需求 | 較少（記憶體隔離） | **多（共享記憶體→race，要 mutex/semaphore）** |

兩個記憶口訣：
- **process 重隔離（安全但慢、溝通麻煩）；thread 重共享（快、溝通易，但要同步、不安全）。**
- **建立/切換：thread 比 process 便宜**（thread 共享記憶體，不用複製/切換位址空間）。

## context switch（上下文切換）

OS 在多個執行單元間切換 CPU 叫 context switch——保存當前的狀態、載入下一個的狀態：

```
   context switch 要做的事：
   - 保存當前的暫存器、PC、stack pointer（到 PCB/TCB）
   - 載入下一個的暫存器、PC、stack pointer

   process 之間切換（更貴）：額外要：
   - 切換位址空間（換 page table）
   - flush TLB（位址轉換快取，Ch 25）→ 之後 TLB miss 變多，更慢

   thread 之間切換（同 process）：
   - 不用換位址空間/TLB（共享）→ 便宜很多
```

**為什麼 thread 切換比 process 快**：同 process 的 threads 共享位址空間，切換時不用換 page table、不用 flush TLB（Ch 25）——省掉最貴的部分。這是「thread 輕量」的核心原因，常考。

context switch 本身是有成本的（純開銷，沒做有用的事）——所以排程要平衡「回應性」和「切換開銷」（Ch 21）。

## PCB 與 TCB

OS 用資料結構記錄每個 process/thread 的狀態：

- **PCB（Process Control Block）**：記錄一個 process 的所有資訊——PID、狀態、暫存器、page table、開啟的檔案、優先序等。context switch 時存/取它。
- **TCB（Thread Control Block）**：記錄一個 thread 的資訊——TID、暫存器、stack pointer、狀態。

process 狀態（和 RTOS task 狀態類似，Ch 18）：

```
   New → Ready ⇄ Running → Terminated
              ↓     ↑
            Blocked/Waiting（等 I/O 或資源）

   - Ready：就緒，等 CPU
   - Running：正在用 CPU
   - Blocked/Waiting：等 I/O 或資源（等到了回 Ready）
```

## 考古題詳解

### Q1：process 和 thread 差在哪？

<details>
<summary>詳解</summary>

核心：**process 是資源擁有單位（獨立位址空間），thread 是執行/排程單位（共享 process 記憶體）。**

- 記憶體：process 獨立、thread 共享（同 process）。
- 建立/切換：thread 便宜（不用複製/切換位址空間）。
- 溝通：process 用 IPC（慢）、thread 直接讀共享記憶體（快）。
- 隔離：process 高（一個崩不影響別的）、thread 低（一個崩拖垮 process）。
- 同步：thread 需求多（共享記憶體→race）。

口訣：process 重隔離、thread 重共享。

**考點**：OS 開場必考。
</details>

### Q2：為什麼 thread 的 context switch 比 process 快？

<details>
<summary>詳解</summary>

因為同一 process 的 threads **共享位址空間**——切換時不用換 page table、不用 flush TLB（Ch 25）。而 process 之間切換要換位址空間 + flush TLB（之後 TLB miss 增加，更慢）。thread 省掉了這個最貴的部分。

**考點**：thread 輕量的原因（位址空間/TLB），高頻。
</details>

### Q3：同一 process 的 threads 共享什麼、各自擁有什麼？

<details>
<summary>詳解</summary>

**共享**：code 段、data 段（全域變數）、heap、開啟的檔案。
**各自擁有**：stack、暫存器、PC、thread ID。

關鍵：共享 heap/全域變數 → 溝通容易但**會 race**（要同步，Ch 22）；各自有 stack → 區域變數互不干擾。

**考點**：thread 共享 vs 獨立，連結 race condition。
</details>

### Q4：什麼時候用 process、什麼時候用 thread？

<details>
<summary>詳解</summary>

**用 thread**：需要頻繁共享資料、要快速溝通、要輕量並行（同一程式內的多工，如 worker threads）。

**用 process**：需要隔離/安全（一個崩不影響別的，如瀏覽器每個分頁一個 process）、需要獨立的資源、安全邊界。

取捨：thread 快但不安全（共享記憶體、一崩全崩）；process 安全但重（隔離、溝通要 IPC）。

**考點**：取捨判斷。
</details>

### Q5：process 有哪些狀態？

<details>
<summary>詳解</summary>

- **New**：剛建立。
- **Ready**：就緒，等 CPU 排程。
- **Running**：正在用 CPU。
- **Blocked/Waiting**：等 I/O 或資源（等到了回 Ready）。
- **Terminated**：結束。

轉換：Ready ⇄ Running（被排程/被搶占）、Running → Blocked（等 I/O）、Blocked → Ready（資源就緒）。

（和 RTOS task 狀態 Ch 18 類似。）

**考點**：process 狀態圖。
</details>

## 踩雷集錦

1. **以為 thread 也有獨立記憶體**：同 process 的 threads 共享記憶體（heap/全域），只有 stack/暫存器各自獨立。
2. **以為 process 切換和 thread 切換一樣快**：process 切換要換位址空間 + flush TLB（貴）；thread 不用（便宜）。
3. **忘了 thread 共享 → 要同步**：共享記憶體是 race 的根源。多 thread 改共享變數要 mutex（Ch 22）。
4. **以為 thread 一定比 process 好**：thread 快但一個崩拖垮整個 process、要小心同步。要隔離/安全用 process。
5. **混淆 stack 和 heap 的共享**：threads 共享 heap、各自有 stack。區域變數（stack）thread 間不共享，heap/全域共享。

## 速記

- **process = 資源擁有單位（獨立位址空間）；thread = 執行/排程單位（共享 process 記憶體）。** 一個 process 多個 thread。
- 同 process threads **共享** code/data/heap/檔案，**各自有** stack/暫存器/PC。
- thread context switch 比 process 快（共享位址空間，不用換 page table/flush TLB）。
- process 重隔離（安全、溝通用 IPC、慢）；thread 重共享（快、溝通易、但要同步、不安全）。
- 共享記憶體 → race → 要同步（Ch 22）。

## 自我檢核

- [ ] process 和 thread 的根本差異是什麼（誰擁有資源、誰是執行單位）？
- [ ] 同一 process 的 threads 共享什麼、各自有什麼？
- [ ] 為什麼 thread 的 context switch 比 process 快？（提示：位址空間/TLB）
- [ ] 什麼時候該用 process、什麼時候用 thread？
- [ ] 為什麼多 thread 需要同步、process 較不需要？

## 延伸閱讀

### 書籍

- **《Operating System Concepts (恐龍書)》** — Ch 3 Processes、Ch 4 Threads
  - **讀哪幾章**：3.1（process 概念/狀態/PCB）、4.1（thread 概念）。
  - **和本章的關聯**：process/thread 的標準教材，本章的權威。

- **《Operating Systems: Three Easy Pieces (OSTEP)》** — [免費線上](https://pages.cs.wisc.edu/~remzi/OSTEP/)
  - **讀哪幾章**：Process（4）、Threads 概論（26）。
  - **為什麼值得讀**：比恐龍書好讀，process/thread 講得很清楚。

### 文章

- **[面試紀錄 & 練習（聯發科）— HackMD](https://hackmd.io/@chiangkd/interview)**
  - **讀哪裡**：process/thread OS 題。
  - **和本章的關聯**：MTK 面試的 OS 考點。

process/thread 是基礎，下一章是排程——OS 怎麼決定先跑誰，FCFS/SJF/RR 與計算題。

→ [Ch 21 process 排程](./21-scheduling.md)
