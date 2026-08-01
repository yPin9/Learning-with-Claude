# 練習 C — 讀懂一段硬核 code

> 目標：把 Part 4（Ch 21–28）學到的所有讀法綜合起來，攻堅一段真正硬核的真實 code——redis `dict.c` 的**漸進式 rehash（incremental rehashing）**。這是「一個高頻使用中的 hash table，要怎麼在不停止服務、不一次卡住整個伺服器的前提下，把所有 entry 從舊表搬到新表」的經典難題。你要完整解釋它的機制、畫出資料結構圖、說清楚它維持的不變式（invariant），並找出一個 edge case。做完你就有底氣說「我能讀懂任何一段硬核 code」。

## 為什麼選這段

redis 是單執行緒的（Ch 25 講過命令處理單執行緒），這帶來一個殘酷約束：**任何一個命令都不能花太久，否則整個伺服器卡住。** 但 hash table 滿了要擴容（resize），擴容意味著「把幾百萬個 key 重新雜湊、搬到一張兩倍大的新表」——如果一次做完，這個 `SET` 命令會卡住伺服器好幾秒。使用者的 redis 突然沒回應幾秒，是災難。

redis 的解法是**漸進式 rehash**：不一次搬完，而是把搬移工作**攤到後續成千上萬個操作裡，每次只搬一點點**。搬到一半時，兩張表同時存在，查詢要同時查兩張。這個「同時維護兩張表、逐步搬移、期間正常服務」的設計，把 Ch 21–28 的多個主題濃縮在一個檔案裡：狀態機（rehash 進行中 vs 未進行）、indirection（`ht_table[2]` 兩張表）、精妙的不變式（`rehashidx` 之前的桶保證空）、以及「為什麼這樣寫」的效能取捨。這是讀碼教材裡少有的、又難又漂亮的真實案例。

沙包：`~/reading_code_lab/redis`（redis 7.4.0），主檔 `src/dict.c` 與 `src/dict.h`。

## 任務規格

讀 `src/dict.c` 與 `src/dict.h`，交出以下四份產物（這就是「讀懂了」的定義）：

### 任務 1：機制解釋

用你自己的話，講清楚以下每一個問題（不是抄註解，是理解後重述）：

1. `struct dict` 裡的 `ht_table[2]`、`ht_used[2]`、`ht_size_exp[2]`、`rehashidx` 各是什麼、為什麼是「2」個一組。
2. rehash 是「什麼時候開始」的？（追 `dictExpand` / `_dictExpandIfNeeded`）
3. 「漸進」具體怎麼發生——`_dictRehashStep` 在哪些操作裡被呼叫、一次搬多少？
4. rehash 進行中，`dictFind`（查詢）為什麼、以及怎麼**同時查兩張表**？
5. rehash 進行中，`dictAdd`（新增）新的 entry 加到**哪一張表**？為什麼？
6. rehash 什麼時候「結束」？結束時 `ht_table[0]` 和 `ht_table[1]` 發生什麼？

### 任務 2：資料結構圖

畫一張 ASCII 圖，呈現「rehash 進行到一半」的狀態：兩張 hash table、`rehashidx` 指到哪、哪些桶已搬空、哪些還沒搬、一個查詢怎麼在兩張表間找。

### 任務 3：不變式（invariant）

列出至少三條這套機制**必須維持**的不變式，並解釋「如果某條被違反會發生什麼壞事」。不變式是硬核 code 的靈魂——它們是沒寫成 assert、但作者心裡一直在維護的規則。

### 任務 4：edge case

找出並解釋**至少一個** edge case——一個「乍看會出錯、但 code 有特別處理」或「容易被讀者忽略」的邊界情況。說明 code 在哪裡、怎麼處理它。

## 卡住提示

按這個順序讀，不要一開始就鑽進最難的函式：

- **先讀 `dict.h` 的 `struct dict`**：一切從資料結構開始。看懂那幾個 `[2]` 和 `rehashidx` 的註解，機制的骨架就有了。Ch 8（data flow）的精神：先搞清楚狀態長什麼樣，再追誰改它。
- **用 cscope/grep 找 `rehashidx` 被誰讀寫**（Ch 12/14）：`rg rehashidx src/dict.c`。它被改的地方就是 rehash 狀態機的轉移點。
- **`dictIsRehashing` 是狀態機的判斷式**：`#define dictIsRehashing(d) ((d)->rehashidx != -1)`。全檔搜它，每個用到的地方都是「rehash 進行中要特別處理」的分支——這就是 Ch 24 的狀態機讀法。
- **`dictRehash(d, n)` 是搬移主體**，`_dictRehashStep(d)` 是「搬一步」的包裝（它呼叫 `dictRehash(d, 1)`）。先讀 step，再讀主體。
- **`rehashEntriesInBucketAtIndex` 是真正搬一個桶的地方**：一個桶裡的 entry 鏈，逐個重算在新表的位置、掛過去。
- **別怕 `no_value`/`keys_are_odd` 那些分支**：那是 redis 針對「只有 key 沒 value 的 dict」（如 set 型別）的記憶體優化，跟 rehash 主邏輯無關，第一遍**直接跳過**，當它是 `dictSetNext(de, ...)` 那條 else 就好。Ch 4 掃讀的精神：先抓主幹，枝節後補。

## 實作步驟建議

1. 讀 `struct dict`，把每個欄位標註用途，特別是 `[2]` 的兩個元素分別代表舊表 `[0]` 和新表 `[1]`。
2. `rg -n 'rehashidx|dictIsRehashing|_dictRehashStep|dictRehash\b' src/dict.c`，列出所有相關點，建一張「誰在什麼時候碰 rehash 狀態」的表。
3. 讀 `dictFind`，逐行理解那個 `for (table = 0; table <= 1; table++)` 迴圈和 `if (table == 0 && idx < rehashidx) continue;` 這一句——這是「同時查兩張表」與「跳過已搬空的桶」的核心。
4. 讀 `dictInsertAtPosition`（`dictAddRaw` 的下半），找到 `int htidx = dictIsRehashing(d) ? 1 : 0;`——這決定新 entry 加到哪張表。
5. 讀 `dictRehash` 主迴圈 + `rehashEntriesInBucketAtIndex`，理解「搬一個桶」的動作和 `rehashidx++` 的推進。
6. 讀 `dictCheckRehashingCompleted`，看 rehash 收尾時兩張表怎麼交換。
7. 邊讀邊在紙上/檔案裡畫圖、記不變式、標可疑邊界。這是 Ch 35「外化理解」的實踐——腦中讀不算讀。
8. （動手驗證）用 gdb attach 一個真跑的 redis，在 `dictRehash` 下斷點，塞大量 key 觸發 rehash，`print *d`（或 `print d->rehashidx`、`print d->ht_used`）看 rehash 中途的真實狀態。這是 Ch 18 debugger-driven reading 的收尾應用。

## 完整參考解答

**先自己讀、自己畫、自己找 edge case，再看下面。** 這段 code 值得你花兩小時親自攻堅，直接看解答等於沒練。

<details>
<summary>點開參考解答（我真讀 redis 7.4.0 dict.c 拆解的結果）</summary>

### 任務 1：機制解釋

**（1）為什麼所有東西都是 `[2]`**

`struct dict` 的核心欄位（真實 source，`dict.h`）：

```c
struct dict {
    dictType *type;
    dictEntry **ht_table[2];      /* 兩張 hash table：[0]=舊, [1]=新 */
    unsigned long ht_used[2];     /* 兩張表各自的 entry 數 */
    long rehashidx;               /* rehashing not in progress if rehashidx == -1 */
    unsigned pauserehash : 15;    /* >0 時暫停 rehash（迭代器持有期間） */
    signed char ht_size_exp[2];   /* 兩張表的大小指數，size = 1<<exp */
    ...
};
```

關鍵設計：**redis 的 dict 永遠準備好「同時持有兩張表」。** 平常（沒 rehash）只用 `[0]`，`[1]` 是空的、`rehashidx == -1`。一旦要擴容，就配一張新表放進 `[1]`，開始逐步把 `[0]` 的 entry 搬到 `[1]`。用 `ht_size_exp` 存「指數」而非大小，是因為 hash table 大小永遠是 2 的冪（方便用 `& (size-1)` 取模），存指數更省空間、算 mask 也方便（`DICTHT_SIZE_MASK(exp) = (1<<exp)-1`）。

`rehashidx` 是這整套機制的靈魂：它是**下一個要搬移的桶（bucket）在舊表 `[0]` 裡的索引**。`-1` 代表「沒在 rehash」。搬移就是「從 `rehashidx` 指的桶開始，一個桶一個桶往後搬，搬一個 `rehashidx++`，搬到舊表空了為止」。

**（2）rehash 什麼時候開始**

新增 entry 時（`dictAddRaw` → `dictFindPositionForInsert`）會呼叫 `_dictExpandIfNeeded`，當「entry 數 / 桶數」超過門檻（負載因子，預設 used >= size 即 1:1，強制門檻是 `dict_force_resize_ratio = 4`）就呼叫 `dictExpand` → `_dictExpand`。`_dictExpand` 配一張約兩倍大的新表放進 `ht_table[1]`、設定 `ht_size_exp[1]`、把 `rehashidx = 0`——**這一刻 rehash 就「開始」了，但一個 entry 都還沒搬。** 從此 `dictIsRehashing(d)` 為真。

**（3）「漸進」怎麼發生**

搬移不是一次做完，而是靠 `_dictRehashStep`（真實 source）：

```c
static void _dictRehashStep(dict *d) {
    if (d->pauserehash == 0) dictRehash(d,1);   /* 一次只搬 1 個「桶」 */
}
```

它被 `dictAddRaw`、`dictFind`、`dictGenericDelete`（新增/查詢/刪除）等常用操作在開頭呼叫。也就是說：**使用者每對 dict 做一次操作，就順手幫忙搬一個桶。** 操作越頻繁，rehash 越快完成；沒操作時，還有 `serverCron` 定時呼叫 `dictRehashMicroseconds` 做限時批量搬移（`dictRehash(d, 100)` 直到用完時間預算）。這就是「漸進」——把 O(n) 的搬移攤成 n 次 O(1)，任何單一操作都不會卡住。

`dictRehash(d, n)` 搬 `n` 個「非空桶」（有 `empty_visits` 上限防止一直遇到空桶空轉），`_dictRehashStep` 傳 `n=1`。搬一個桶用 `rehashEntriesInBucketAtIndex`：把該桶鏈上每個 entry 重算在新表的桶位、掛過去，`ht_used[0]--; ht_used[1]++;`，最後 `ht_table[0][idx] = NULL`（舊桶清空），`rehashidx++`。

**（4）查詢為什麼要查兩張表、怎麼查**

rehash 進行中，一個 key 可能還在舊表 `[0]`（還沒搬到）、也可能已經在新表 `[1]`（搬過去了）。所以 `dictFind` 必須**兩張都查**。核心迴圈（真實 source）：

```c
for (table = 0; table <= 1; table++) {
    if (table == 0 && (long)idx < d->rehashidx) continue;   /* ← 關鍵優化 */
    idx = h & DICTHT_SIZE_MASK(d->ht_size_exp[table]);
    he = d->ht_table[table][idx];
    while(he) { ...比對 key，中就回傳... }
    if (!dictIsRehashing(d)) return NULL;   /* 沒 rehash 就只查一張 */
}
```

兩個精妙處：
- `if (table == 0 && idx < rehashidx) continue;`——如果這個 key 在舊表的桶位 `idx` **小於** `rehashidx`（代表這個桶早就被搬空了），就跳過查舊表，直接查新表。這是靠不變式「`rehashidx` 之前的桶保證是空的」做的優化，省掉查一個保證空的桶。
- `if (!dictIsRehashing(d)) return NULL;`——如果沒在 rehash（只有一張表），查完 `[0]` 沒找到就直接回 NULL，不查 `[1]`（它是空的）。

**（5）新增加到哪張表**

`dictInsertAtPosition`（真實 source）：

```c
int htidx = dictIsRehashing(d) ? 1 : 0;   /* rehash 中 → 一律加到新表 [1] */
```

**rehash 進行中，所有新 entry 直接加到新表 `[1]`。** 原因：如果加到舊表，等一下還要再搬一次，白費工；而且更重要——這保證了**舊表 `[0]` 只減不增**，`ht_used[0]` 單調遞減，rehash 一定會結束（不會邊搬邊往舊表加、永遠搬不完）。

**（6）rehash 什麼時候結束**

每次 `dictRehash` 搬完會呼叫 `dictCheckRehashingCompleted`，它檢查 `if (d->ht_used[0] != 0) return 0;`——舊表還有 entry 就沒完。舊表清空（`ht_used[0] == 0`）時（真實 source）：

```c
zfree(d->ht_table[0]);            /* 釋放舊表的桶陣列 */
d->ht_table[0] = d->ht_table[1];  /* 新表「升格」成 [0] */
d->ht_used[0] = d->ht_used[1];
d->ht_size_exp[0] = d->ht_size_exp[1];
_dictReset(d, 1);                 /* [1] 重置為空 */
d->rehashidx = -1;                /* 回到「沒 rehash」狀態 */
```

新表變成唯一的表 `[0]`，`[1]` 清空，`rehashidx = -1`，回到平常狀態。整個過程沒有任何一刻「複製一大塊」——搬移早就分散在前面成千上萬次操作裡了。

### 任務 2：資料結構圖（rehash 進行到一半）

```
                    rehashidx = 3
                         │
                         ▼（下一個要搬的桶）
  ht_table[0] (舊, size=8, ht_size_exp[0]=3)
  ┌─────┬─────┬─────┬─────┬─────┬─────┬─────┬─────┐
  │  0  │  1  │  2  │  3  │  4  │  5  │  6  │  7  │
  ├─────┼─────┼─────┼─────┼─────┼─────┼─────┼─────┤
  │NULL │NULL │NULL │ ●─►k9│ ●─►k4│NULL │ ●─►k7│NULL│
  └─────┴─────┴─────┴─────┴─────┴─────┴─────┴─────┘
   已搬空(桶 < rehashidx)  │  尚未搬移(桶 >= rehashidx)
   查詢直接跳過這段 ───────┘

  ht_table[1] (新, size=16, ht_size_exp[1]=4)
  ┌──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┐
  │0 │1 │2 │3 │4 │5 │6 │7 │8 │9 │10│11│12│13│14│15│
  ├──┼──┼──┼──┼──┼──┼──┼──┼──┼──┼──┼──┼──┼──┼──┼──┤
  │  │●►k1│  │  │  │●►k6│ │  │●►k2│ │  │  │  │●►k8│ │  │
  └──┴──┴──┴──┴──┴──┴──┴──┴──┴──┴──┴──┴──┴──┴──┴──┘
   桶 0,1,2 的舊 entry 已搬來這 + 所有「rehash 期間新增」的也在這

  查詢 dictFind(key) 的路徑：
    h = hash(key)
    idx0 = h & 7    (在舊表的桶位)
    ┌─ idx0 <  3 (rehashidx)?  → 舊桶保證空，跳過，只查新表 idx1 = h & 15
    └─ idx0 >= 3?              → 先查舊表 idx0，沒中再查新表 idx1 = h & 15

  不變式：ht_used[0] 只減不增；新增一律進 [1]；
          舊表桶 [0..rehashidx-1] 全部 NULL。
```

### 任務 3：不變式

1. **「`rehashidx` 之前的桶保證是空的」**：舊表 `ht_table[0][0 .. rehashidx-1]` 全部是 `NULL`（都搬走了）。這條讓 `dictFind`/`dictGenericDelete` 能用 `if (idx < rehashidx) continue;` 安全跳過查舊表。**若被違反**（某個 `< rehashidx` 的桶還有 entry），查詢會跳過它、找不到明明存在的 key——資料「憑空消失」。搬移函式尾端的 `d->ht_table[0][idx] = NULL` 就是在維護這條。

2. **「rehash 期間，舊表 `ht_used[0]` 單調遞減，永不增加」**：新 entry 一律進新表 `[1]`（靠 `htidx = dictIsRehashing ? 1 : 0`）。**若被違反**（rehash 中還往舊表加），`ht_used[0]` 可能永遠不歸零，rehash 永遠完不成，兩張表無限共存——記憶體與查詢成本雙倍且不收斂。

3. **「一個 key 在任一時刻只存在於一張表」**：搬移是「從舊表摘下、掛到新表」的原子移動（單執行緒下天然原子），不會有「兩張表都有同一個 key」的中間態。**若被違反**（搬移中途一個 key 同時在兩表），刪除只刪一張表的、另一張的變成幽靈 entry，或查詢拿到舊值。`rehashEntriesInBucketAtIndex` 先把整條鏈摘完再清空舊桶，保證這點。

4. **（進階）「rehash 進行中，dict 大小 `dictSize` = `ht_used[0] + ht_used[1]` 保持正確」**：搬一個 entry 就 `ht_used[0]--; ht_used[1]++;`，兩者之和不變。**若被違反**，`dictSize` 回報錯誤數量，影響負載因子計算與後續 resize 決策。

### 任務 4：edge case

**Edge case：迭代器存在時，rehash 必須暫停（`pauserehash`）。**

如果有人正在**迭代**這個 dict（`dictGetIterator` 拿到一個 safe iterator），同時 rehash 又在搬 entry，會出大事：迭代器記著「我走到舊表第幾個桶」，但 rehash 把桶裡的 entry 搬走了，迭代器要嘛**漏掉**還沒走到就被搬走的 entry、要嘛**重複**已經走過又被搬到前面的 entry。

redis 的處理：`struct dict` 有個 `pauserehash` 計數器，safe iterator 建立時 `dictPauseRehashing(d)`（`pauserehash++`）、釋放時 `dictResumeRehashing(d)`（`pauserehash--`）。而 `_dictRehashStep` 開頭就是 `if (d->pauserehash == 0) dictRehash(d,1);`——**只有 `pauserehash == 0`（沒人在迭代）才搬**。這樣迭代期間 rehash 凍結，迭代器看到的兩張表結構穩定，不會漏也不會重。

這是容易被忽略的邊界：讀主流程時你看到 `_dictRehashStep` 每次操作都搬，很自然以為「rehash 一定持續推進」；但 `pauserehash` 這個守衛揭示了一個隱藏狀態——**rehash 可以被暫停**，而觸發暫停的是「有迭代器存在」這個看似不相關的事件。找到這種「看似無關的狀態耦合」，就是讀懂硬核 code 的標誌。

（另一個可提的 edge case：`dictRehash` 裡的 `empty_visits = n*10` 上限——如果舊表前面一長串全是空桶，`while (ht_table[0][rehashidx] == NULL) rehashidx++;` 會一直空轉，`empty_visits` 用完就提前 return，避免單次 rehash step 掃過太多空桶而變慢。這保證了「搬一步」的成本有上界，維護了「不卡住」的初衷。）

</details>

## 測試用例（驗證你真的懂了）

拿真跑的 redis 驗證你的理解（Ch 18 動態讀碼）：

1. **觸發 rehash 並看中途狀態**：
   ```bash
   ./src/redis-server --port 7801 --save "" &
   sudo gdb -p $(pgrep -f redis-server) \
     -ex "break rehashEntriesInBucketAtIndex" -ex "continue"
   # 另開一個 terminal 灌 key：
   redis-cli -p 7801 eval "for i=1,100000 do redis.call('set', 'k'..i, i) end" 0
   ```
   斷點命中後，在 gdb 裡 `print d->rehashidx`、`print d->ht_used[0]`、`print d->ht_used[1]`。確認 `rehashidx` 在推進、`ht_used[0]` 在減、`ht_used[1]` 在增，兩者之和 = 總 key 數。這就是你畫的圖的真實對應。

2. **驗證不變式**：斷在 rehash 中途，寫個 gdb 迴圈檢查 `ht_table[0]` 的前 `rehashidx` 個桶是不是都 NULL（不變式 1）。

3. **驗證「新增進新表」**：rehash 中途 `redis-cli set newkey 1`，觀察 `ht_used[1]` 增加而 `ht_used[0]` 不變（不變式 2）。

## 延伸挑戰

- **對照 `dictScan`**：redis 的 `SCAN` 命令要在「rehash 進行中、且 rehash 可能在兩次 SCAN 之間發生」的情況下，保證「一直存在的 key 一定被掃到、不重複太多」。它用了一個反轉二進位（reverse binary）的游標推進法，是比 rehash 更燒腦的設計。讀 `dictScan` 並解釋那個 `v = rev(v); v++; v = rev(v);` 為什麼能在 table 大小變化時仍不漏 key。
- **對照別的 hash table**：拿 CPython 的 `dictobject.c`（開放定址、無漸進 rehash，一次搬完）或 Go 的 map（也是漸進式，但用 `oldbuckets`/`nevacuate`，設計和 redis 異曲同工）對照。同一個問題不同語言怎麼取捨？redis 的漸進式付出了「查詢要查兩張表」的代價換「永不卡頓」——這個取捨在你的場景成立嗎？
- **shrink（縮容）**：redis 7.4 的 dict 不只會擴容，key 刪多了也會**縮容**（`_dictShrinkIfNeeded`）。縮容時 `ht_size_exp[1] < ht_size_exp[0]`，`rehashEntriesInBucketAtIndex` 有一條 `else` 分支處理縮容的桶位計算（`h = idx & mask`）。讀懂它，說明擴容和縮容在搬移邏輯上的差異。

## 自我檢核

- [ ] 我能不能不看解答，講清楚「為什麼 dict 要有兩張表、rehashidx 是什麼」？
- [ ] 我能不能畫出 rehash 到一半的圖，並解釋一個查詢怎麼決定查一張還兩張表？
- [ ] 我能不能說出至少三條不變式，以及各自被違反會壞成什麼樣？
- [ ] 我找到的 edge case，是不是一個「乍看會出錯、但 code 有守衛」的真實邊界（如 `pauserehash`）？
- [ ] 我有沒有真的 attach gdb、看到 `rehashidx`/`ht_used` 在真跑中變化，而不只是讀 source 想像？
- [ ] 這套「先讀資料結構 → 追狀態轉移點 → 讀關鍵函式 → 畫圖記不變式 → gdb 驗證」的流程，我能不能套到下一段陌生的硬核 code？

做完這個練習，你已經證明自己能攻堅任意一段硬核 code——這正是整門課要給你的能力。Part 4 到此完整。下一 Part 進入高階策略，第一站是本課最挑戰的一種情境：**讀一種你根本不會的語言**。你會發現，當語法完全陌生時，前面練的「找結構、追資料流、猜意圖」反而變成唯一能依靠的東西。

→ [Ch 29 讀你不會的語言](./29-reading-unknown-languages.md)
