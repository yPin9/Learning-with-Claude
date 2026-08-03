# Ch 18 — Crash Recovery：ARIES

> **目標**：理解資料庫在 crash 後如何把狀態恢復到一致、所有已提交交易的修改都保留、所有未提交的修改都抹除——這就是 ARIES 演算法三個階段的任務。不只是「看懂流程」，而是能在紙上走查一份 WAL log 並算出 recovery 後的結果。

## 問題的根本：Crash 後資料庫在哪個狀態？

假設有三筆交易在 crash 前執行：

```
T1: BEGIN → UPDATE page 5 → COMMIT
T2: BEGIN → UPDATE page 7 → UPDATE page 8 → （crash 時還沒 COMMIT）
T3: BEGIN → UPDATE page 9 → COMMIT → UPDATE page 10 → （COMMIT 已落盤）
```

Crash 之後，磁碟上的狀態可能是：

- page 5：已更新（T1 的修改，T1 已提交）✓
- page 7：可能已更新也可能沒有（T2 的修改，T2 未提交，應被 undo）✗
- page 8：可能已更新也可能沒有（T2，同上）✗
- page 9：已更新（T3 的一部分）✓
- page 10：可能已更新（T3 另一部分，T3 已提交，應確保存在）？

Buffer pool 的 No-Force 政策讓 dirty page 的落盤時機不可預測；Steal 政策讓未提交的修改可能提早落盤。Recovery 必須解決這個混亂。

---

## ARIES 的核心思想

ARIES（Algorithm for Recovery and Isolation Exploiting Semantics，1992）由 IBM Almaden Research Center 的 C. Mohan 等人提出，是現代資料庫 recovery 的工業標準。

三個核心原則：

1. **Write-Ahead Logging**：ch 17 的規則，log 先 fsync 才能改 page。
2. **Repeating History（重播歷史）**：Redo 階段**不管**交易是否提交，無條件把 log 裡的所有修改都重播一遍，先讓資料庫回到 crash 前瞬間的狀態。
3. **Undo-only uncommitted（只 Undo 未提交）**：之後才逐一 Undo 未提交的交易。

「Repeating History」是 ARIES 最反直覺的地方——為什麼要重播未提交交易的修改？因為這樣最簡單：redo 完之後，資料庫狀態與 crash 前完全一樣，Undo 就能用與正常 Rollback 完全相同的程式碼。如果 Redo 階段跳過未提交的修改，需要特殊邏輯處理 Undo 的起點。

---

## 三個階段詳解

```
WAL 時間線：
  checkpoint
      │
      ▼
  ┌───────────────────────────────────────────────┐  crash
  │  [BEGIN T1][UPD T1 p5][BEGIN T2][UPD T2 p7]  │────▶
  │  [COMMIT T1][UPD T2 p8][BEGIN T3][UPD T3 p9] │
  │  [COMMIT T3]                                  │
  └───────────────────────────────────────────────┘

Recovery 三個階段：

  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
  │  Analysis   │ →  │    Redo     │ →  │    Undo     │
  │ 找 dirty    │    │ 重播所有    │    │ 回滾未提交  │
  │ page 與     │    │ 修改        │    │ 交易        │
  │ active txn  │    │             │    │             │
  └─────────────┘    └─────────────┘    └─────────────┘
```

### 階段一：Analysis（分析）

從最後一個 checkpoint 的 WAL 位置往前掃描到 log 末尾，建立兩個表：

**ATT（Active Transaction Table，活躍交易表）**：
- 收錄所有在 checkpoint 後 BEGIN 但尚未 COMMIT 或 ABORT 的交易
- 每個 entry：`txn_id, status, lastLSN（該交易最後一筆 log 的 LSN）`

**DPT（Dirty Page Table，髒頁表）**：
- 收錄所有在 checkpoint 後被修改但可能還沒寫回磁碟的 page
- 每個 entry：`page_id, recLSN（最早讓這個 page 變髒的 LSN）`
- `recLSN` 是 Redo 需要從哪裡開始的指引

Analysis 結束後：
- ATT 中還在的交易 → Undo 的對象
- DPT 中 `recLSN` 的最小值 → Redo 的起始點

### 階段二：Redo（重做）

從 DPT 中所有 `recLSN` 的最小值開始，掃描到 log 末尾，對**每一筆 update record**：

```
for each log record with LSN = L:
    if record is UPDATE:
        p = record.page_id
        if p ∈ DPT and DPT[p].recLSN ≤ L:
            if page.page_lsn < L:  ← 這個 page 還沒含有這筆修改
                apply record.after_image to page
                page.page_lsn = L
            else:
                skip（這個 page 已經包含此修改，不需重做）
```

注意跳過已更新 page 的邏輯：如果 crash 前這個 page 已經成功寫回磁碟（`page_lsn ≥ L`），就不需要 redo。這讓 ARIES 的 Redo 是冪等的（idempotent）——跑兩次結果相同。

### 階段三：Undo（回滾）

Undo 階段把 ATT 中所有未提交交易的修改反向撤銷。

這裡有一個重要細節：Undo 必須**從 log 末尾往前**進行，且每個 Undo 操作本身也要寫入一筆 **CLR（Compensation Log Record，補償日誌記錄）**。

CLR 記錄「這個 Undo 動作已執行」，並且帶有 `undoNextLSN`（指向下一個需要 undo 的 LSN）。CLR 的用途：如果 Undo 階段也 crash 了，重新 Recovery 時不會重複 Undo 已 Undo 過的操作。

```
Undo 演算法：

  ToUndo = { ATT 中每個交易的 lastLSN }

  while ToUndo 不為空:
      取出最大的 LSN = L（從最新往舊 undo）
      record = read_log(L)

      if record.type == CLR:
          下一個要 undo 的是 record.undoNextLSN
          把 record.undoNextLSN 加入 ToUndo

      else if record.type == UPDATE:
          apply record.before_image to page   ← 撤銷這個修改
          write CLR(txn_id=record.txn_id,
                    undoNextLSN=record.prev_lsn,
                    page=record.page_id)
          把 record.prev_lsn 加入 ToUndo

      else if record.type == BEGIN:
          write ABORT(txn_id)
          從 ATT 移除此交易
```

---

## 完整走查範例

我們來走一遍具體的 log，算出 recovery 後的狀態。

### 初始 WAL（crash 前）

```
LSN  | Type   | TxnID | PageID | before  | after  | prev_lsn
-----|--------|-------|--------|---------|--------|----------
10   | BEGIN  | T1    | -      | -       | -      | 0
20   | UPDATE | T1    | P5     | "A"     | "B"    | 10
30   | BEGIN  | T2    | -      | -       | -      | 0
40   | COMMIT | T1    | -      | -       | -      | 20
50   | UPDATE | T2    | P7     | "X"     | "Y"    | 30
60   | BEGIN  | T3    | -      | -       | -      | 0
70   | UPDATE | T2    | P8     | "M"     | "N"    | 50
80   | UPDATE | T3    | P9     | "C"     | "D"    | 60
90   | COMMIT | T3    | -      | -       | -      | 80
```

Checkpoint 在 LSN=30 之後記錄（簡化：checkpoint 說當時 ATT={T1,T2}、DPT={P5（recLSN=20）}）。

**Crash** 發生在 LSN=90 之後，T2 沒有 COMMIT。

---

### 階段一：Analysis

從 checkpoint 往後掃描（LSN 30 之後）：

| LSN | 動作 | ATT 狀態 | DPT 狀態 |
|---|---|---|---|
| checkpoint | — | {T1(lastLSN=20), T2(lastLSN=0)} | {P5(recLSN=20)} |
| 40（COMMIT T1） | T1 從 ATT 移除 | {T2} | {P5} |
| 50（UPDATE T2 P7） | T2 lastLSN=50；P7 加入 DPT | {T2(lastLSN=50)} | {P5, P7(recLSN=50)} |
| 60（BEGIN T3） | T3 加入 ATT | {T2, T3} | {P5, P7} |
| 70（UPDATE T2 P8） | T2 lastLSN=70；P8 加入 DPT | {T2(lastLSN=70), T3} | {P5, P7, P8(recLSN=70)} |
| 80（UPDATE T3 P9） | T3 lastLSN=80；P9 加入 DPT | {T2(lastLSN=70), T3(lastLSN=80)} | {P5, P7, P8, P9(recLSN=80)} |
| 90（COMMIT T3） | T3 從 ATT 移除 | {T2(lastLSN=70)} | {P5, P7, P8, P9} |

Analysis 結束：
- **ATT** = { T2（lastLSN=70）} → T2 需要 Undo
- **DPT** = { P5(20), P7(50), P8(70), P9(80) }
- **Redo 起始 LSN** = min(recLSN) = 20

---

### 階段二：Redo

從 LSN=20 掃描到 log 末尾：

| LSN | Record | 動作 | 說明 |
|---|---|---|---|
| 20 | UPDATE T1 P5 | check P5：若 P5.page_lsn < 20，apply "B" | P5 在 DPT，recLSN=20 ≤ 20 |
| 40 | COMMIT T1 | 跳過（非 update） | |
| 50 | UPDATE T2 P7 | check P7：若 P7.page_lsn < 50，apply "Y" | P7 在 DPT，recLSN=50 ≤ 50 |
| 70 | UPDATE T2 P8 | check P8：若 P8.page_lsn < 70，apply "N" | P8 在 DPT，recLSN=70 ≤ 70 |
| 80 | UPDATE T3 P9 | check P9：若 P9.page_lsn < 80，apply "D" | P9 在 DPT，recLSN=80 ≤ 80 |
| 90 | COMMIT T3 | 跳過 | |

Redo 結束後，資料庫狀態 = crash 前瞬間的狀態：
- P5 = "B"（T1 已提交，正確）
- P7 = "Y"（T2 未提交，待 undo）
- P8 = "N"（T2 未提交，待 undo）
- P9 = "D"（T3 已提交，正確）

---

### 階段三：Undo

ToUndo = { 70（T2 的 lastLSN）}

| 步驟 | 取出 LSN | 動作 | 寫入 CLR | 下一個 ToUndo |
|---|---|---|---|---|
| 1 | 70（UPDATE T2 P8，before="M"） | P8 ← "M" | CLR(T2, undoNextLSN=50) | {50} |
| 2 | 50（UPDATE T2 P7，before="X"） | P7 ← "X" | CLR(T2, undoNextLSN=30) | {30} |
| 3 | 30（BEGIN T2） | 寫 ABORT(T2) | — | {} |

Undo 結束：
- P5 = "B"（T1 已提交）✓
- P7 = "X"（T2 已 undo）✓
- P8 = "M"（T2 已 undo）✓
- P9 = "D"（T3 已提交）✓

資料庫回到一致狀態。

---

## Checkpoint：縮短 Recovery 時間

如果每次 recovery 都從 WAL 最開頭掃描，代價太高。**Checkpoint** 的目的是定期記錄當前狀態，讓 Redo 只需從 checkpoint 之後開始。

### Fuzzy Checkpoint（模糊檢查點）

Simple checkpoint 的做法是在 checkpoint 時暫停所有交易，flush 所有 dirty page，然後記錄。但這會暫停服務。

**Fuzzy checkpoint**（ARIES 使用）允許 checkpoint 與正在進行的交易重疊：

1. 在 WAL 寫入 `BEGIN_CHECKPOINT` record（記錄 ATT 與 DPT 的快照）
2. 不暫停任何交易，繼續接受新交易
3. 逐漸 flush DPT 中的 dirty page
4. 當所有在 `BEGIN_CHECKPOINT` 時的 dirty page 都 flush 完，寫入 `END_CHECKPOINT`

```
WAL 時間線：

[BEGIN_CKPT: ATT={T2}, DPT={P5(20)}]
                    ↕ 交易繼續執行
[..records..]
                    ↕ P5 flush 完
[END_CKPT]
```

Recovery 時從最後一個 `END_CHECKPOINT` 往前找對應的 `BEGIN_CHECKPOINT`，用裡面的 ATT 和 DPT 作為 Analysis 的起點，只需掃描 `BEGIN_CHECKPOINT` 之後的 log。

**Master record**：WAL 的最後一個位置存放一個指標（master record / control file），指向最後一個 `BEGIN_CHECKPOINT` 的 LSN。Recovery 第一步是讀這個指標。

---

## Recovery 流程圖（完整）

```
啟動（檢測到 crash）
  │
  ▼
讀 master record → 找最後一個 BEGIN_CHECKPOINT 的 LSN
  │
  ▼
Analysis Phase
  從 BEGIN_CHECKPOINT LSN 掃描到 log 末尾
  重建 ATT（哪些交易還活著）
  重建 DPT（哪些 page 可能是 dirty）
  │
  ▼
Redo Phase
  從 min(DPT.recLSN) 掃描到 log 末尾
  對每個 update record：若 page.pageLSN < record.LSN → apply after_image
  （已寫回磁碟的 page 跳過）
  │
  ▼
Undo Phase
  對 ATT 中每個未提交交易，從其 lastLSN 往前 undo
  每個 undo 動作寫入 CLR
  直到 ATT 清空
  │
  ▼
寫入新的 BEGIN_CHECKPOINT + END_CHECKPOINT
啟動完成
```

---

## ARIES 的幾個關鍵正確性保證

**為什麼 Redo 是安全的（冪等性）**：Redo 前先檢查 `page_lsn`。如果 `page_lsn ≥ record.lsn`，代表這個修改已在 page 上（可能在 crash 前已 flush），直接跳過。多次 Redo 同一 record，結果相同。

**為什麼 Undo 過程中再 crash 是安全的**：Undo 每個操作都寫 CLR，CLR 指向下一個需要 undo 的 LSN。重新 Recovery 時，Redo 階段會重播所有 CLR（CLR 本身也是 redo 的目標），這樣 Undo 階段就能從 CLR.undoNextLSN 繼續，跳過已 undo 的部分。這是 ARIES 最精巧的設計。

**為什麼已提交交易不需要特別處理**：Redo 把它們的修改全部重播；Undo 只處理 ATT 中的（未提交的）交易，不碰已提交的。已提交交易在 log 中有 COMMIT record，Analysis 階段看到 COMMIT 就把交易從 ATT 移除。

---

## 實作考量與邊界情況

### Log 末尾截斷

Crash 可能發生在 log record 寫到一半。Recovery 需要能偵測截斷的 record：

- 用 checksum 在每個 record 結尾驗證完整性
- 用 `total_len` 欄位：讀到的 bytes 不夠 `total_len`，就是截斷的 record

截斷的 record 直接忽略（就像它不存在），這等同於這筆 log 從未寫入。

### Large Transaction 的 Undo

一個長交易可能有幾百萬筆 log record。Undo 需要從 `lastLSN` 沿著 `prev_lsn` 鏈結往前走——如果 log file 很大，這可能要讀取大量 I/O。

緩解：定期強制 checkpoint（限制 WAL 長度），或使用 savepoint 讓應用層可以部分 rollback。

### Partial Page Write（撕裂寫入）

WAL 解決了「哪個 page 要改」的問題，但沒解決 page 本身在 crash 時被半寫的問題。如果一個 8KB page 在寫回磁碟時只寫了 4KB 就 crash：

- PostgreSQL 用 **full page write**：第一次修改 checkpoint 後的 page 時，把整個 page 的舊內容寫入 WAL，這樣 Redo 時用 WAL 中的完整舊 page 作為基底再 apply 修改。
- SQLite 用 **double-write buffer**：先把 page 寫到一個固定位置的 journal，成功後才寫到真正的位置。

---

## 不同 DB 的 Recovery 實作對比

| | PostgreSQL | MySQL InnoDB | SQLite |
|---|---|---|---|
| 演算法 | ARIES 變體 | ARIES 變體 | 類 Undo-only |
| Log 格式 | WAL（.wal 檔案） | redo log（ib_logfile） | journal file |
| Checkpoint | Fuzzy checkpoint | Fuzzy checkpoint | Checkpoint = commit |
| Full page write | 是（first write after ckpt） | Double-write buffer | Double-write buffer（WAL mode） |
| Undo storage | Heap page（可見給 MVCC） | Undo tablespace | WAL 中反向記錄 |
| CLR | 有 | 有（稱 compensation） | 沒有（undo-only 不需要） |

---

## 踩雷

### 1. Redo 跳過「已提交但 page 未 flush」的 record

常見誤解：「T1 已 COMMIT，Redo 可以跳過它。」錯。No-Force 政策讓 T1 的 page 可能還在 buffer pool 中沒 flush，Redo 必須把它重播進去。只有當 `page_lsn ≥ record.lsn` 時才跳過（表示 page 已是最新狀態）。

### 2. Undo 順序錯誤

Undo 必須從**最新**往最舊。如果反過來（從舊往新 undo），可能 undo 了一個已被後來的操作覆蓋的值，導致資料錯誤。`ToUndo` 用最大堆（max-heap）按 LSN 排序。

### 3. CLR 不寫導致 Undo 無法冪等

如果 Undo 階段 crash 後重新 Recovery，Redo 階段會把 Undo 的動作也重播（CLR 被 redo），然後 Undo 從 `undoNextLSN` 繼續，不重複 undo。如果沒有 CLR，重複 undo 同一 record 會把 before_image 套兩次，資料錯誤。

### 4. Checkpoint 寫得太頻繁

每次 checkpoint 要 flush 大量 dirty page，I/O spike 會影響正常交易。Postgres 用 `checkpoint_completion_target`（預設 0.9）把 checkpoint 的 I/O 分散在兩次 checkpoint 間隔的 90% 時間內。

### 5. log 與 data file 放在同一個磁碟

WAL 的 fsync 是循序寫，data page 的 flush 是隨機 I/O。混在同一顆磁碟會互相搶 I/O bandwidth，對 HDD 影響尤其大。生產環境建議把 WAL 放獨立的高速 NVMe。

---

## 進階延伸

**ARIES/IM（Index Management）**：ARIES 原始論文的延伸，專門處理 B+tree 的 recovery。B+tree 的 split/merge 是「相對邏輯」操作，不能用 physical redo/undo——需要 redo-only CLR 和 `nested top action`。

**Parallel Recovery**：Recovery 的三個階段可以並行化——Analysis 是串行的，但 Redo 可以按 page 分區並行（不同 page 的 redo 互相獨立）。MySQL 8.0、Postgres 的 parallel recovery 都走這條路。

**Epoch-based Checkpointing**：不用 fuzzy checkpoint，改用「epoch」的概念——每個 epoch 開始時所有交易都已提交，這樣 epoch 邊界就是天然的 checkpoint 點，Recovery 只需從上一個 epoch 開始。Spanner 的 checkpoint 設計接近這個模型。

---

## 本章重點整理

- ARIES 用「Repeating History」解決 crash recovery：先把歷史無條件重播到 crash 前的瞬間，再 undo 未提交的交易
- Analysis 階段建立 ATT（找 undo 對象）和 DPT（找 redo 起點）
- Redo 階段從 min(DPT.recLSN) 開始，用 `page_lsn` 判斷是否跳過（冪等性）
- Undo 階段從最新往最舊，每個 undo 動作寫 CLR（讓 undo 過程可以再 crash 再 recovery）
- Fuzzy checkpoint 讓 checkpoint 與正常交易重疊，減少停頓
- CLR 的 `undoNextLSN` 是讓 Undo 冪等的核心機制

## 自我檢核

- [ ] 我能說出 ARIES 三個階段的名稱與各自目的
- [ ] 我能解釋為什麼 Redo 要重播未提交交易的修改（Repeating History）
- [ ] 我能在一張紙上走查一份 WAL log 並得出 recovery 後的狀態
- [ ] 我能解釋 CLR 的用途，以及為什麼沒有 CLR 時 Undo-twice 是問題
- [ ] 我能說出 Fuzzy Checkpoint 與 Simple Checkpoint 的差別

## 延伸閱讀

- **ARIES 原始論文**：Mohan et al.《ARIES: A Transaction Recovery Method Supporting Fine-Granularity Locking and Partial Rollbacks Using Write-Ahead Logging》（ACM TODS, 1992）——Section 4（Overview）和 Section 5（Algorithm）是本章的嚴格版本，值得逐字對照走查範例。
- **《Database Internals》Part I Ch 8（Recovery）**——Alex Petrov 對 ARIES 三階段的精要描述，附有 log record 格式與 CLR 說明。
- **CMU 15-445 Lecture 20（Database Recovery）**——Andy Pavlo 用投影片圖示走查 ARIES 範例，是本章走查練習的最佳影片對照。
- **《Designing Data-Intensive Applications》Ch 7（Transactions）**——DDIA 從「durability 到底保證什麼」切入，是理解本章動機的更白話版本。
- **PostgreSQL 文件 WAL Internals**（https://www.postgresql.org/docs/current/wal-internals.html）——真實系統的 full-page write、checkpoint 設計，對照本章理論看懂生產級細節。

---

→ [Ch 19 交易與 ACID](./19-transactions-acid.md)
