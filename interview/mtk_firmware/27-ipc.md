# Ch 27 — IPC（行程間通訊）

> **目標**：搞懂 process 之間怎麼溝通——pipe、shared memory、message queue、socket、signal，各自的機制與取捨。process 記憶體隔離（Ch 20），所以要 IPC 才能交換資料。

> **環境**：概念為主，類 Unix。前置：Ch 20（process 隔離）、Ch 22（同步）。

## 為什麼考這個

Ch 20 說 process 記憶體隔離——這是安全，但也意味「process 之間不能直接讀寫對方的變數」。要溝通就得用 **IPC（Inter-Process Communication）**。面試會問「有哪些 IPC 方式、各的取捨」——測你懂不懂「隔離的 process 怎麼合作」。

## 先建立直覺：隔離的兩間公司怎麼合作

```
   thread（同公司員工）：共用辦公室 → 直接在白板上寫字溝通（共享記憶體，快但要同步）

   process（不同公司）：辦公室隔離，看不到對方的東西 → 要靠：
   - 傳紙條（pipe / message queue）
   - 租一間共用會議室（shared memory）
   - 打電話（socket）
   - 拉警報（signal）
```

核心：**process 記憶體隔離 → 要透過 OS 提供的機制（IPC）交換資料。** 不同 IPC 方式 = 不同的「溝通管道」，各有速度、彈性、複雜度的取捨。

## 主要 IPC 方式

### 1. Pipe（管道）

```
   單向的資料流：一端寫、一端讀（像水管）
   - 匿名 pipe：只能在「有親屬關係的 process」間（如 父子，fork 後）
                 shell 的 `ls | grep` 就是 pipe（ls 的輸出接 grep 的輸入）
   - 命名 pipe（FIFO）：有名字（檔案系統裡），不相關的 process 也能用
```

特點：簡單、單向（雙向要兩個 pipe）、像位元組流（byte stream，沒有訊息邊界）。適合「一個 process 的輸出餵給另一個」。

### 2. Shared Memory（共享記憶體）

```
   兩個 process 把「同一塊實體記憶體」映射進各自的位址空間
   → 兩邊都能直接讀寫這塊記憶體（像共享變數）
```

特點：**最快的 IPC**（直接讀寫記憶體，不經過 kernel 複製）。但**要自己做同步**（兩個 process 同時改 = race，要用 semaphore/mutex，Ch 22）——shared memory 只提供「共享的記憶體」，不提供同步。適合「大量資料、高效能」的 IPC。

### 3. Message Queue（訊息佇列）

```
   OS 維護一個訊息佇列：process A 送訊息進去、process B 取出
   - 有訊息邊界（一則一則的訊息，不像 pipe 的位元組流）
   - 非同步（送了就走，不用等對方收）
```

特點：有結構（訊息有型別/邊界）、OS 管理（自帶緩衝、某種同步）。比 pipe 結構化，比 shared memory 慢（經過 kernel）。

### 4. Socket（套接字）

```
   網路通訊的抽象，但也能用於「同一台機器的 process 間」（Unix domain socket）
   - 雙向、可跨機器（網路 socket）或本機（Unix socket）
```

特點：**最通用**（本機 + 跨機器都行）、雙向。但開銷較大（經過網路堆疊）。適合「可能跨機器」或「需要網路語意」的 IPC（client-server）。

### 5. Signal（訊號）

```
   一個 process 送一個「訊號」（一個小整數，如 SIGTERM/SIGKILL/SIGUSR1）給另一個
   → 通知「發生某事」（不傳資料，只傳「事件」）
```

特點：**只傳事件不傳資料**（很輕量）——像「拉警報」通知對方「發生了 X」。`kill -9 pid`（送 SIGKILL）就是 signal。process 可註冊 signal handler 處理（類似中斷，Ch 14）。適合「簡單通知」（如「請結束」「設定改了」）。

## IPC 方式對比

| 方式 | 方向 | 資料量 | 速度 | 跨機器 | 同步 |
|---|---|---|---|---|---|
| pipe | 單向 | 中（byte stream）| 中 | 否 | OS 管 |
| shared memory | 雙向 | **大** | **最快** | 否 | **要自己做** |
| message queue | 雙向 | 中（訊息）| 中 | 否 | OS 管 |
| socket | 雙向 | 大 | 較慢 | **是** | OS 管 |
| signal | 單向 | **無（只事件）** | 快 | 否 | — |

選擇判斷：
- **大量資料、要快** → shared memory（但要自己同步）。
- **簡單的輸出接輸入** → pipe。
- **結構化訊息** → message queue。
- **跨機器 / client-server** → socket。
- **只是通知事件** → signal。

## 考古題詳解

### Q1：有哪些 IPC 方式？為什麼 process 需要 IPC（thread 不太需要）？

<details>
<summary>詳解</summary>

IPC 方式：pipe、shared memory、message queue、socket、signal（還有 semaphore 也算）。

為什麼 process 需要 IPC：process **記憶體隔離**（Ch 20），不能直接讀寫對方的記憶體 → 要透過 OS 的 IPC 機制溝通。

thread 不太需要：同 process 的 threads **共享記憶體**，直接讀寫共享變數就能溝通（但要同步，Ch 22）——不用 IPC。

**考點**：IPC 方式 + 為什麼 process 需要（隔離），串 Ch 20。
</details>

### Q2：shared memory 為什麼最快？有什麼要注意？

<details>
<summary>詳解</summary>

**最快**：兩個 process 直接讀寫同一塊實體記憶體，**不用經過 kernel 複製資料**（其他 IPC 如 pipe/message queue 要把資料從一個 process 複製到 kernel 再到另一個 process）。

要注意：**shared memory 不提供同步**——兩個 process 同時讀寫 = race condition（Ch 22）。要自己用 semaphore/mutex 保護。shared memory 只給「共享的記憶體」，同步要自己做。

**考點**：shared memory 快的原因（免複製）+ 要自己同步，高頻。
</details>

### Q3：pipe 和 message queue 差在哪？

<details>
<summary>詳解</summary>

- **pipe**：位元組流（byte stream，沒有訊息邊界——讀方自己分辨資料邊界）、單向、匿名 pipe 限親屬 process。
- **message queue**：訊息導向（一則一則有邊界/型別）、OS 管理佇列、非同步。

差別：pipe 是「水流」（連續位元組）、message queue 是「一封封信」（有結構的訊息）。需要結構化訊息用 message queue，簡單的輸出接輸入用 pipe。

**考點**：pipe vs message queue。
</details>

### Q4：signal 是什麼？和其他 IPC 差在哪？

<details>
<summary>詳解</summary>

signal：一個 process 送一個「訊號」（小整數，如 SIGTERM/SIGUSR1）給另一個，**只傳「事件」不傳資料**。process 可註冊 handler 處理（類似中斷，Ch 14）。

和其他 IPC 差別：其他 IPC 傳「資料」，signal 只傳「發生了某事」（很輕量）。像拉警報。`kill pid` 就是送 signal。適合簡單通知（結束、重載設定）。

**考點**：signal 的特性（只傳事件），和韌體中斷類比（Ch 14）。
</details>

### Q5：要傳大量資料的高效能 IPC 選哪個？跨機器呢？

<details>
<summary>詳解</summary>

- **大量資料、高效能、同機**：**shared memory**（最快，免複製；但要自己同步）。
- **跨機器**：**socket**（網路 socket，唯一能跨機器的）。

選擇看：資料量（大→shared memory）、是否跨機器（是→socket）、結構需求（訊息→message queue）、簡單通知（→signal）。

**考點**：IPC 選擇判斷。
</details>

## 踩雷集錦

1. **以為 thread 也要 IPC**：thread 共享記憶體（直接溝通），不用 IPC。IPC 是給隔離的 process。
2. **shared memory 不做同步**：它只給共享記憶體，不防 race。要自己加 semaphore/mutex（Ch 22）。
3. **以為 signal 能傳資料**：signal 只傳「事件」（一個小整數），不傳資料內容。要傳資料用別的 IPC。
4. **pipe 當雙向用**：pipe 單向，雙向要開兩個。
5. **不知道 shared memory 最快的原因**：免去 kernel 複製（其他 IPC 要複製資料進出 kernel）。
6. **匿名 pipe 用在不相關的 process**：匿名 pipe 限親屬（fork）；不相關用命名 pipe（FIFO）或其他 IPC。

## 速記

- process 記憶體隔離（Ch 20）→ 要 IPC 溝通；thread 共享記憶體不用 IPC。
- **pipe**（位元組流、單向、匿名限親屬）、**shared memory**（**最快**，免複製，但**要自己同步**）、**message queue**（結構化訊息、OS 管）、**socket**（最通用、**可跨機器**、雙向）、**signal**（只傳事件不傳資料、輕量通知）。
- 大量資料高效能 → shared memory；跨機器 → socket；結構化訊息 → message queue；通知事件 → signal。
- shared memory 快是因免 kernel 複製；代價是要自己 mutex/semaphore（Ch 22）。

## 自我檢核

- [ ] 有哪些 IPC 方式？為什麼 process 需要 IPC 而 thread 不太需要？
- [ ] shared memory 為什麼最快？為什麼要自己做同步？
- [ ] pipe 和 message queue 差在哪？
- [ ] signal 和其他 IPC 最大的不同是什麼？
- [ ] 大量資料 / 跨機器的 IPC 各選哪個？

## 延伸閱讀

### 書籍

- **《Operating System Concepts (恐龍書)》** — Ch 3.4–3.7 IPC
  - **讀哪幾章**：3.4（shared memory vs message passing）、3.6（pipe）、3.7（socket）。
  - **和本章的關聯**：IPC 的標準教材。

- **《The Linux Programming Interface (TLPI)》** — Michael Kerrisk — IPC 章
  - **讀哪幾章**：pipe、shared memory、message queue、signal 各章（深，當參考）。
  - **為什麼值得讀**：Linux IPC 的權威，實作層面最完整。

### 文章

- **[面試紀錄 & 練習（聯發科）— HackMD](https://hackmd.io/@chiangkd/interview)**
  - **讀哪裡**：IPC 題。
  - **和本章的關聯**：MTK 的 IPC 考點。

IPC 是 process 間溝通，下一章是 OS 最後一塊——system call 與 user/kernel mode，user space 怎麼請 OS 做事。

→ [Ch 28 system call、user/kernel mode](./28-syscall-kernel-mode.md)
