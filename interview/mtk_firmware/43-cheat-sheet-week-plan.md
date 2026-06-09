# Ch 43 — 一週衝刺計畫與 cheat sheet

> **目標**：把整門課濃縮成一週讀書計畫 + 一頁速查表。考前一晚不要再看全部章節，看這一章——所有「必拿分」的結論集中在這裡。

> **環境**：複習收尾。前置：理想上你已讀過全課；沒讀完也能用這章抓重點。

## 一週衝刺計畫

假設你有 7–8 天、每天 4–6 小時。按**面試權重**（Ch 0）分配，C 和嵌入式最重。

### Day 1–2：C 語言（Part 1，Ch 1–12）★★★★★

最高權重，先攻。重點：
- volatile / const / static（Ch 2–3）——必問，務必能舉「為什麼」的例子。
- 複雜宣告 + 函式指標（Ch 5）——右左法則練熟。
- bit manipulation（Ch 7）——set/clear/toggle/test、判 2 的次方、數 bit，會手寫。
- struct 對齊 + sizeof 計算（Ch 8）——會算 padding。
- 有號/無號轉換、整數提升（Ch 9）——`-20+6u` 那類陷阱。
- 記憶體模型（Ch 11）+ 手寫 strlen/strcpy/memcpy（Ch 12）。
- **做練習 A**（C 上機模擬）驗收。

### Day 3：嵌入式 / 韌體（Part 2，Ch 13–19）★★★★

韌體職的差異化重點：
- memory-mapped I/O：`*(volatile unsigned int*)0xADDR`（Ch 13）。
- ISR 設計規則（Ch 14）+ reentrancy（Ch 15）——高頻。
- endianness 偵測（Ch 16）、ARM 基礎（Ch 17）、RTOS + priority inversion（Ch 18）。
- **做練習 B**（嵌入式情境）驗收。

### Day 4–5：作業系統（Part 3，Ch 20–28）★★★★

- process vs thread（Ch 20）、排程算 Gantt/等待時間（Ch 21）。
- mutex/semaphore/spinlock（Ch 22）、deadlock 四條件（Ch 23）。
- 生產者消費者（Ch 24）、virtual memory + page replacement 計算（Ch 25–26）。
- IPC（Ch 27）、user/kernel mode + syscall（Ch 28）。
- **做練習 C**（OS 綜合）驗收，尤其計算題。

### Day 6：計算機組織（Part 4，Ch 29–35）★★★

- 二補數/浮點（Ch 29）、cache 拆位址 + 平均存取時間計算（Ch 30）——必拿分計算。
- pipeline 五階段 + hazard（Ch 31）、gcc 四階段 + 連結（Ch 33）、DMA（Ch 34）。
- **做練習 D**（cache + pipeline 計算）驗收。

### Day 7：資料結構演算法（Part 5，Ch 36–41）★★★

- linked list 反轉/找環、走訪、quicksort、binary search——**手寫到肌肉記憶**。
- 排序複雜度表（Ch 40）背熟、BST/heap/hash 概念（Ch 38–39）、BFS/DFS（Ch 41）。
- **做練習 E**（手寫題）驗收。

### Day 8（考前）：行為面試 + 總複習

- 準備自我介紹、STAR 故事、MTK 功課（Ch 42）。
- 看這章的 cheat sheet + 做 final 模擬面試。

> 時間不夠？砍計組（Part 4）和部分 DS 概念題，**絕不砍 C 和嵌入式**——那是韌體職的命脈。

---

## 一頁速查表（cheat sheet）

考前最後看這個。

### C 語言

| 主題 | 必記結論 |
|---|---|
| volatile | 告訴編譯器「這變數隨時會變，別優化掉讀取」。用於：MMIO、ISR 共享變數、多執行緒旗標 |
| const | 唯讀承諾。`const int*` 指向不可改、`int* const` 指標不可改 |
| static | 區域→保值跨呼叫；全域/函式→internal linkage（限本檔） |
| 右左法則 | 從變數名出發，先右後左，碰括號轉向 |
| set/clear/toggle/test bit | `x\|=(1u<<n)` / `x&=~(1u<<n)` / `x^=(1u<<n)` / `(x>>n)&1u` |
| 判 2 的次方 | `x && !(x&(x-1))` |
| 有號+無號 | 有號被轉成無號（`-20+6u` 變大正數） |
| sizeof('A') | C 裡是 4（'A' 是 int）；C++ 是 1 |
| char* "hi" vs char arr[] | 字串字面量在唯讀區（改→crash）；陣列在 stack（可改） |

### 嵌入式

| 主題 | 必記結論 |
|---|---|
| MMIO | `*(volatile unsigned int*)0xADDR = val` |
| ISR 規則 | void/無參數、快進快出、不 printf/不 malloc/不浮點、共享變數加 volatile |
| reentrant | 不用靜態/全域、不呼叫非 reentrant 函式（strtok/malloc） |
| reentrant ≠ thread-safe | 用鎖的 thread-safe 在 ISR 會死鎖；reentrant 才安全 |
| endian 偵測 | `int x=1; *(char*)&x==1` → little-endian |
| priority inversion | 低優先持鎖擋高優先；解法 priority inheritance |

### 作業系統

| 主題 | 必記結論 |
|---|---|
| process vs thread | process 資源單位（獨立位址空間）；thread 執行單位（共享）；thread 切換便宜 |
| deadlock 4 條件 | 互斥、持有並等待、不可剝奪、循環等待（全成立才死鎖）|
| mutex vs semaphore | mutex 有 ownership（誰鎖誰解）；semaphore 計數、無 ownership |
| spinlock vs mutex | spinlock 忙等（短臨界區/中斷脈絡）；mutex 睡眠（長） |
| 排程等待時間 | 周轉=完成-到達；等待=周轉-執行 |
| page replacement | FIFO 有 Belady's anomaly；LRU/OPT 沒有 |
| page fault vs segfault | page fault 正常（換頁）；segfault 非法存取（錯誤） |
| 生產者消費者 | empty/full/mutex 三號誌；P 的順序：先 P(資源) 再 P(mutex)，不可反（死鎖） |

### 計算機組織

| 主題 | 必記結論 |
|---|---|
| cache 拆位址 | offset(line=2^off) / index(set 數=行數/way) / tag(剩下) |
| 平均存取時間 | hit率×快 + miss率×慢 |
| pipeline | 5 階段 IF/ID/EX/MEM/WB；k 階段 n 指令 = k+(n-1) cycle |
| hazard | data（forwarding，load-use 要 stall）/ control（分支預測）/ structural |
| gcc 四階段 | 預處理→編譯→組譯→連結 |
| DMA 一致性 | 軟體 flush/invalidate；多核是硬體 MESI（不同） |
| 浮點比較 | 不能 `==`，用 `fabs(a-b)<epsilon` |

### 資料結構演算法

| 主題 | 必記結論 |
|---|---|
| array vs list | array 隨機存取 O(1)/插刪 O(n)/cache 好；list 反之 |
| BST | 平均 O(log n)，排序資料退化 O(n)；中序=排序 |
| heap | 完全二元樹，array 實作，取最值 O(1) peek / O(log n) extract |
| hash table | 平均 O(1)、最壞 O(n)；chaining vs open addressing |
| 排序 | quick 平均 O(n log n) 最壞 O(n²)；merge O(n log n) 穩定但 O(n) 空間；heap O(n log n) 原地 |
| 穩定排序 | merge/insertion/bubble/counting；不穩定 quick/heap/selection |
| binary search | 已排序，O(log n)；陷阱 `<=`、`lo+(hi-lo)/2`、±1 |
| BFS/DFS | BFS=queue=最短路；DFS=stack/遞迴=連通/找環；都 O(V+E)、要 visited |
| Big-O | O(1)<O(log n)<O(n)<O(n log n)<O(n²)<O(2ⁿ) |

### 手寫題清單（練到不看也對）

1. strlen / strcpy / memcpy（Ch 12）
2. set/clear/toggle/test bit（Ch 7）
3. linked list 反轉 + Floyd 找環（Ch 36）
4. 二元樹中序走訪（Ch 38）
5. quicksort（Ch 40）
6. binary search（Ch 41）
7. circular queue（Ch 37）

---

## 考前心法

- **C + 嵌入式是命脈**，時間不夠先保這兩塊。
- **計算題必拿分**（排程、cache、page fault、pipeline）——這些有標準答案，練熟就是分。
- **手寫題練到肌肉記憶**——上機/白板現場想不出來就是 0 分。
- **履歷上的每個字都要扛得住問**（Ch 42）。
- 上機考用 C，注意**邊界**（NULL、空、溢位）和**編譯通過**——很多人栽在 off-by-one。

## 自我檢核

- [ ] 我有一份適合自己時間的衝刺計畫，且 C/嵌入式排在最前嗎？
- [ ] cheat sheet 裡每個結論我都能展開解釋（不是死背），而非只認得字？
- [ ] 七題手寫題我都練到不看也能寫對嗎？
- [ ] 四類計算題（排程/cache/page/pipeline）我都能算嗎？
- [ ] 我準備好做最後的 final 模擬面試了嗎？

## 延伸閱讀

### 文章

- **[發哥(聯發科)上機考題目整理 — HackMD](https://hackmd.io/@Rance/SkSJL_5gX)**
  - **讀哪裡**：整份，對照你的弱點章節。
  - **和本章的關聯**：MTK 上機考真實題庫，衝刺最後幾天拿來自測。

- **[NTU / 各校 OS·計組·DS 考古題共筆](https://hackmd.io/@chiangkd/interview)**
  - **讀哪裡**：OS/計組/DS 各段。
  - **和本章的關聯**：補充更多考古題練手感。

整門課的知識都濃縮在這了。最後用 final 模擬面試——一場完整的、計時的自我演練，驗證你真的準備好了。

→ [Final Project：完整模擬面試](./final-project-mock-interview.md)
