# Ch 23 — 進階並發：SSI / OCC

> **目標**：理解 Snapshot Isolation 的 write skew 弱點、Serializable Snapshot Isolation（SSI）如何用危險結構偵測升級成 serializable、以及 Optimistic Concurrency Control（OCC）的讀/驗證/寫三階段設計，掌握樂觀與悲觀兩種路徑的取捨。

## 問題：SI 差最後一哩

Ch 22 說 MVCC 自然提供 Snapshot Isolation。SI 防住了 dirty read、non-repeatable read、phantom——但防不住 write skew。

Write skew 的根本：**兩個 txn 各自讀了一些資料、各自寫了不同的資料，每個 txn 在自己的 snapshot 下都合法，但組合起來違反全域不變量**。

```
不變量：A + B ≥ 0

T1 讀到 (A=100, B=0) → 寫 A = A - 100 = 0    合法（0+0=0）
T2 讀到 (A=100, B=0) → 寫 B = B - 100 = -100  合法（100-100=0）
並發執行最終：A=0, B=-100 → A+B=-100 違反不變量
```

兩個 txn 都沒讀到對方寫的東西（寫在不同 key），SI 無法偵測到衝突。

要達到真正的 Serializability，需要更強的機制。

## 選項一：Serializable Snapshot Isolation（SSI）

SSI 是 Postgres 9.1 引入的技術（基於 Cahill et al. 2008 的論文）。它不阻止 txn 執行，而是在執行過程中**追蹤危險結構（dangerous structure）**，一旦偵測到就 abort 其中一個 txn。

### 危險結構：Anti-Dependency Cycle

Write skew 的本質是「rw anti-dependency」形成環。定義：

- **rw anti-dependency（T1 →rw T2）**：T1 讀了某個版本，T2 後來寫了那個 key（T2 的寫對 T1 不可見，但 T1 讀到的版本是 T2 寫之前的）。

Write skew 的結構一定是：

```
T1 →rw T2
T2 →rw T1

（或更長的環）
```

如果有兩個連續的 rw anti-dependency 形成環，就是 serializable 危險結構。

### SSI 的追蹤方式

Postgres 的 SSI 用 **SIREAD lock**（謂詞讀鎖）追蹤讀集合：

1. 每次 txn 讀一個 key，加一個 SIREAD lock（不阻塞任何人，只是記錄「我讀過這個」）。
2. 每次 txn 寫一個 key，檢查：有沒有其他 txn 持有這個 key 的 SIREAD lock？如果有 → 建立一條 rw 邊。
3. 如果一個 txn 同時是兩條連續 rw 邊的中間節點（或環的一部分），abort 其中一個 txn（通常是較後寫入的那個）。

```
追蹤表（概念）：

SIREAD locks: { key → [txn_id, ...] }
rw_edges:     { txn_id → [txn_id, ...] }  // T1 →rw T2 表示 T2 寫了 T1 讀的 key

提交時：
  檢查 rw_edges 中有無環（或 pivot：同時出現在兩條 rw 邊）
  → 有 → abort 這個 txn
  → 無 → 提交成功
```

### SSI 的代價

- **False abort（誤殺）**：SSI 是保守的——有些它偵測到「危險結構」但實際執行是 serializable 的場景，一樣會 abort。論文估計誤殺率在低衝突工作負載下很低（< 5%）。
- **記憶體**：SIREAD lock 比普通 lock 多存資訊，且提交後的 txn 的 SIREAD 資訊要保留一段時間（直到不會有新的 rw 邊出現）。
- **效能**：比純 SI 慢 5-15%（Postgres 官方數字），但比 S2PL（Strict 2PL）在讀多寫少場景下快得多。

### 關鍵程式碼方向

Postgres SSI 的核心在 `src/backend/storage/lmgr/predicate.c`，超過 5000 行。關鍵函數：

```c
// 讀時記錄 SIREAD lock
PredicateLockTuple(relation, tid, snapshot)

// 寫時檢查 rw anti-dependency
CheckForSerializableConflictOut(relation, tid, snapshot)
CheckForSerializableConflictIn(relation, tid)

// 提交時做最終檢查
PreCommit_CheckForSerializationFailure()
```

這邊不打算用 Rust 重新實作完整 SSI——那需要一整個課程的工程量。重點是理解機制：**追蹤讀集合、偵測 rw 邊、在環形成前 abort**。

## 選項二：Optimistic Concurrency Control（OCC）

OCC（Kung & Robinson, 1981）的哲學正好相反：**假設衝突很少，先讓交易跑完，在 commit 前才驗證有沒有衝突**。

### OCC 三個階段

```
┌─────────────────────────────────────────────────────────────┐
│ Phase 1: Read                                               │
│   把所有讀取結果存到 local read set                          │
│   把所有寫入先放到 local write set（不寫主儲存）             │
└───────────────────────────┬─────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│ Phase 2: Validation（串行化臨界區，要加全域鎖）              │
│   取得 commit timestamp（ts_commit）                         │
│   對 read set 中每個 key 確認：                              │
│     有沒有 ts_read < 其他 txn commit ts ≤ ts_commit 的寫？   │
│   沒有 → 通過驗證                                           │
│   有   → abort，重試                                        │
└───────────────────────────┬─────────────────────────────────┘
                            │ 通過驗證
                            ▼
┌─────────────────────────────────────────────────────────────┐
│ Phase 3: Write                                              │
│   把 local write set 寫入主儲存，設定 commit timestamp      │
└─────────────────────────────────────────────────────────────┘
```

### Rust 概念示意

```rust
// 概念示意，未編譯驗證
struct OccTransaction {
    read_ts: u64,
    read_set: HashMap<String, i64>,   // key → 讀到的值
    write_set: HashMap<String, i64>,  // key → 要寫的值
}

impl OccTransaction {
    fn read(&mut self, store: &GlobalStore, key: &str) -> i64 {
        // 直接讀 store，記到 read_set
        let val = store.read(key);
        self.read_set.insert(key.to_string(), val);
        val
    }

    fn write(&mut self, key: &str, value: i64) {
        // 只寫到 local write_set
        self.write_set.insert(key.to_string(), value);
    }

    fn commit(self, store: &mut GlobalStore) -> Result<(), ()> {
        // Phase 2: Validation（這段需要全域鎖）
        let commit_ts = store.next_ts();
        for (key, expected_val) in &self.read_set {
            // 檢查 read 期間有沒有別人改過這個 key
            if store.was_modified_between(key, self.read_ts, commit_ts) {
                return Err(());  // abort
            }
        }
        // Phase 3: Write
        for (key, value) in self.write_set {
            store.write(&key, value, commit_ts);
        }
        Ok(())
    }
}
```

Validation 必須在全域鎖（或 latch）保護下進行，否則「驗證通過」和「實際提交」之間還是可能有新 txn 修改 read set。這個全域鎖是 OCC 在高衝突下的瓶頸。

### OCC 的串行化保證

OCC 的 validation 保證：若我通過驗證，我的執行等價於在所有「commit ts 小於我的 txn」都完成後才開始執行。這是一個合法的 serial order。

## 樂觀 vs 悲觀：取捨分析

| 特性 | 2PL（悲觀） | OCC（樂觀） | SSI（中間路線） |
|---|---|---|---|
| 假設 | 衝突很多 | 衝突很少 | 衝突少但要 serializable |
| 等待 | 有（鎖等待） | 無（但 abort 後重試） | 幾乎無（但可能被 abort） |
| Abort 原因 | 死鎖回滾 | 驗證失敗 | 危險結構偵測 |
| 最適情境 | 高衝突、長 txn | 低衝突、短讀多 txn | 中低衝突、需要 serializable |
| 吞吐量（低衝突） | 中 | 高 | 高（比 2PL） |
| 吞吐量（高衝突） | 中（等待） | 低（大量 abort 重試） | 中 |
| 實作複雜度 | 中 | 低（基本實作簡單） | 高 |

**何時選 OCC**：write-mostly workload 幾乎不適合 OCC（因為幾乎所有 txn 都有機會衝突）。OCC 最適合「大量讀、偶爾寫、讀寫期間幾乎不衝突」的 OLTP 場景，如訂票系統（讀座位圖多、寫座位少）。

**何時選 2PL**：高衝突、希望行為可預期（不想有大量重試）、或 txn 很長（長 txn 在 OCC 下 abort 代價極高）。

**何時選 SSI**：需要真正的 Serializable Isolation（ACID 中的 I）、但又不想用 S2PL 拖累讀性能。Postgres 12+ 的預設 SERIALIZABLE 級別就是 SSI。

## 一些工業級系統的選擇

| 系統 | 主要並發控制 | Serializable 實作 |
|---|---|---|
| Postgres | MVCC + SSI | SSI（`predicate.c`） |
| MySQL InnoDB | MVCC + 2PL（next-key lock） | S2PL（`lock_sys`） |
| CockroachDB | MVCC + 2PL | 類 SSI + write pipeline |
| FoundationDB | OCC | 嚴格 OCC + 全序 commit |
| SQLite（WAL mode） | 寫全域鎖 | 等同 S2PL（單 writer） |

## 踩雷清單

1. **SI ≠ Serializable，一定要搞清楚**。很多文件說「Postgres 的 REPEATABLE READ 就是 SI」，然後說「用 BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ 就夠」——對 write skew 不夠。要 serializable 必須用 `SERIALIZABLE` 級別（或加 `SELECT FOR UPDATE`）。

2. **OCC 的 Validation 必須是原子性的**。驗證和提交之間不能有 gap，否則「驗證通過後、提交前」的時間窗就是漏洞。實作上要用全域 mutex 或 version counter CAS。

3. **SSI 的 false abort 在讀多寫少下很少，但不是零**。有些讀者見到 SSI abort 就以為系統有 bug，其實是正常的保守偵測。應用層必須處理 txn retry。

4. **OCC 重試風暴（Thundering Herd）**。高衝突時大量 txn 同時 abort 然後同時重試，造成新一輪衝突，形成惡性循環。解法：exponential backoff + jitter（加隨機延遲）。

5. **寫偏（Write Skew）很難被應用層程式設計師意識到**。通常只在 code review 或壓測時才被發現。最安全的習慣：任何讀了某些條件然後決定寫的 txn，都要考慮是不是需要 `SELECT FOR UPDATE` 或用 SERIALIZABLE 級別。

## 進階延伸

- **Multi-Version Timestamp Ordering（MVTO）**：把 MVCC 和 TO 結合，理論上比 2PL 並發度更高，但實作複雜，學術界居多。
- **Deterministic Database**（Calvin/BOHM）：把並發移到 ordering 層，執行層完全無鎖，適合已知 read/write set 的工作負載。
- **Hardware Transaction Memory（HTM）**：用 CPU 的 TSX 指令做 OCC，把 validation 推進硬體，Intel RTM 是代表。

---

## 本章重點整理

- **SI 的 write skew 弱點**：兩個 txn 讀不同 key、寫不同 key，SI 無法偵測衝突，組合結果違反不變量。
- **SSI**：追蹤 rw anti-dependency，偵測危險結構（兩條連續 rw 邊形成環），abort 其中一個。代價：記憶體、少量 false abort。Postgres 用 SSI 實作 SERIALIZABLE。
- **OCC 三階段**：Read（local 暫存）→ Validation（串行化鎖內驗證 read set）→ Write（提交到主儲存）。低衝突高效，高衝突爛。
- **取捨**：悲觀（2PL）適合高衝突；樂觀（OCC）適合低衝突短 txn；SSI 適合需要 serializable 但衝突不高的場景。

## 自我檢核

1. Write skew 為什麼 SI 防不住？和 lost update 的差異是什麼？
2. SSI 追蹤的 rw anti-dependency 邊代表什麼語義？兩條連續 rw 邊形成環為什麼等於 write skew？
3. OCC 的 Validation phase 為什麼必須是原子性的（不能在驗證和提交中間讓別人插入）？
4. 高衝突工作負載下 OCC 的問題是什麼？如何緩解？
5. Postgres 的 `SERIALIZABLE` 和 `REPEATABLE READ` 在 write skew 上的差異？

## 延伸閱讀

1. **Serializable Isolation for Snapshot Databases**（Cahill et al., SIGMOD 2008）— SSI 的原始論文，描述 anti-dependency cycle 的數學定義與 Postgres 實作方向。要看清楚 write skew 是什麼就讀這篇。
2. **On Optimistic Methods for Concurrency Control**（Kung & Robinson, TODS 1981）— OCC 的原始論文，三階段的定義和串行化證明在這裡，8 頁可快速讀完。
3. **Postgres 文件：Transaction Isolation**（官方 docs）— 用表格清楚列出每個隔離級別在 Postgres 下哪些異常被防住，特別是 SI vs SERIALIZABLE 的 write skew 差異。
4. **《Designing Data-Intensive Applications》Ch 7（Kleppmann）** — "Serializability" 一節把 2PL、SSI、OCC 的取捨用白話說清楚，寫得比論文好懂。
5. **A Critique of ANSI SQL Isolation Levels**（Berenson et al., SIGMOD 1995）— 原始的 SQL 隔離級別定義有缺陷，這篇論文重新嚴謹地定義了各種異常現象（包括 write skew），是後續所有 SI/SSI 研究的起點。

---

→ [練習 C：WAL + MVCC 整合](./practice-c-wal-mvcc.md)
