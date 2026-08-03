# Ch 21 — 並發控制（一）2PL

> **目標**：理解 lock-based 並發控制的核心——two-phase locking（2PL）如何保證 serializable，掌握 strict 2PL、lock manager 實作、死鎖偵測，並用 Rust 寫出一個可運作的簡化 lock manager。

## 為什麼需要並發控制

上一章（Ch 20）告訴我們隔離級別是「要什麼保證」，這章回答「怎麼做到」。

沒有並發控制時，兩個交易同時跑會撞出各種異常：

```
T1: read(A)  →  A=100
                        T2: write(A, 200)   commit
T1: write(A, A+10)  →  A=110    ← 把 T2 的更新蓋掉了
```

這是 lost update。要防這類問題，最直觀的思路是「用鎖」——讀之前拿讀鎖、寫之前拿寫鎖、衝突就等待。但光「用鎖」還不夠，鎖的**釋放時機**才是關鍵。

## 先建立直覺：一個反例

假設規則是「用完馬上放鎖」：

```
T1: lock(A), read(A)=100, unlock(A)
                          T2: lock(A), write(A,0), unlock(A), commit
T1: lock(B), read(B)=200, unlock(B)
T1: write(A+B)... ← A 讀到 100 但 A 已被改成 0，不一致
```

交替拿放鎖會讓一個交易看到「部分更新」的世界。2PL 的解法是：**在 growing phase 只拿鎖，在 shrinking phase 只放鎖，兩個 phase 不交疊**。

## 2PL 的兩個 Phase

```
時間 →

Growing phase（只拿鎖）   Shrinking phase（只放鎖）
────────────────────────  ──────────────────────────
lock(A) lock(B) lock(C)   unlock(A) unlock(B) unlock(C)
                        ↑
                     lock point
                  （最後一個 lock 的那一刻）
```

**定理（可不用背，但要懂）**：若所有交易都遵守 2PL，任何並發執行都等價於某個串行（sequential）執行——也就是 conflict serializable。

直覺：在 lock point 的那一刻，你的「視野」已經完全圈定。你看到的任何 T2 更新，要麼整個在你前面（你才能拿鎖），要麼整個在你後面（你先拿鎖它等你）。不會有「半看到」。

## Shared / Exclusive Lock

| 鎖種類 | 縮寫 | 相容性 |
|---|---|---|
| Shared（讀鎖） | S | S-S 相容、S-X 不相容 |
| Exclusive（寫鎖） | X | X-X 不相容、X-S 不相容 |

相容性矩陣：

```
持有者 →    S      X
請求者↓
   S      ✓      ✗
   X      ✗      ✗
```

Lock upgrade：若 T1 持有 S lock，想升級成 X lock，只有在沒有其他 S lock 持有者時才能升，否則等待（注意升級不算「新拿一個鎖」，timing 上還在 growing phase）。

## Strict 2PL：為什麼要一直握到 commit

基本 2PL 允許在 shrinking phase 放鎖，但這帶來 **cascading abort（連鎖回滾）**：

```
T1 寫了 A 然後放 X lock  →  T2 讀了 T1 寫的 A
T1 abort  →  T2 也必須 abort（讀到了 T1 的骯髒資料）
            T3 讀了 T2... T3 也要 abort
```

**Strict 2PL**：所有 X lock（寫鎖）必須撐到 commit/abort 才一起放。

**Rigorous 2PL**：所有鎖（S + X）都撐到 commit/abort。

實務資料庫（Postgres, MySQL InnoDB）用的是 Rigorous 2PL（等同 Strict，且更簡單實作）。好處：不會有 cascading abort、讀到的資料必然已提交。

## Lock Granularity 與 Lock Escalation

鎖的粒度：

```
資料庫 (DB)
  └─ 表 (Table)
       └─ 頁 (Page)
            └─ 列 (Row/Tuple)
```

**細粒度**（row-level）：並發度高，但 lock manager 管理的鎖數量爆炸。
**粗粒度**（table-level）：管理簡單，但並發度低。

**Lock escalation（鎖升級）**：當一個交易持有的 row lock 超過閾值（如 1000 個），自動升級成 table lock，減少記憶體壓力。代價是並發度下降。

**Intention lock（意向鎖）**：為了讓 table-level 的鎖判斷快速，引入 IS（intention shared）和 IX（intention exclusive）：

```
要拿 row S lock 前，先在 table 上放 IS
要拿 row X lock 前，先在 table 上放 IX
```

這樣 table lock 請求只需檢查 table 上有沒有衝突的 IS/IX，不需掃描所有 row lock。

## 死鎖（Deadlock）與偵測

```
T1: lock(A)...  等待 lock(B)
T2: lock(B)...  等待 lock(A)
```

T1 等 T2、T2 等 T1，循環等待，誰都無法繼續——這就是死鎖。

### Wait-For Graph（等待圖）

把「T1 等待 T2」建成有向邊 T1 → T2。死鎖 ↔ 等待圖中有環。

```
T1 ──→ T2
↑       │
└── T3 ←┘

T1→T2→T3→T1：有環，死鎖
```

偵測演算法：週期性跑 DFS/BFS 偵測環（小系統）或用 Chandy-Misra-Haas 演算法（分散式）。偵測到死鎖後，選一個 victim 回滾（通常選 cost 最小的：undo 操作最少、或最年輕的交易）。

### 死鎖預防

另一條路是設計成根本不讓死鎖發生：

| 策略 | 方法 | 特性 |
|---|---|---|
| Wait-Die | 老交易等待年輕交易；年輕交易遇到老交易持有的鎖就 die（abort） | 非搶占 |
| Wound-Wait | 老交易 wound（搶占）年輕交易；年輕交易等老交易 | 搶占 |
| No-Wait | 拿不到就立刻 abort | 最激進，無死鎖但 abort 多 |

## Lock Manager 結構

Lock manager 核心是兩張表：

```
lock_table: HashMap<ResourceId, LockEntry>
txn_table:  HashMap<TxnId, Vec<ResourceId>>  // 記錄每個 txn 持有的鎖
```

每個 `LockEntry`：

```
LockEntry {
    granted:  Vec<(TxnId, LockMode)>  // 已拿到鎖的 txn
    waiting:  VecDeque<(TxnId, LockMode, Waker)>  // 等待佇列
}
```

### Rust 實作：簡化 Lock Manager

```rust
// 未編譯驗證：概念結構，需要 tokio 或 std::sync
use std::collections::{HashMap, VecDeque};
use std::sync::{Arc, Condvar, Mutex};

#[derive(Clone, Copy, PartialEq, Debug)]
pub enum LockMode { Shared, Exclusive }

impl LockMode {
    fn compatible(&self, other: &LockMode) -> bool {
        matches!((self, other), (LockMode::Shared, LockMode::Shared))
    }
}

#[derive(Default)]
struct LockEntry {
    granted: Vec<(u64, LockMode)>,   // (txn_id, mode)
    waiting: VecDeque<(u64, LockMode)>,
}

impl LockEntry {
    fn can_grant(&self, mode: &LockMode) -> bool {
        self.waiting.is_empty()
            && self.granted.iter().all(|(_, m)| m.compatible(mode))
    }
}

pub struct LockManager {
    inner: Mutex<HashMap<u64, LockEntry>>,  // resource_id → entry
    cvar: Condvar,
}

impl LockManager {
    pub fn new() -> Arc<Self> {
        Arc::new(Self {
            inner: Mutex::new(HashMap::new()),
            cvar: Condvar::new(),
        })
    }

    /// 拿鎖，拿不到就阻塞等待
    pub fn lock(&self, txn_id: u64, resource_id: u64, mode: LockMode) {
        let mut table = self.inner.lock().unwrap();
        let entry = table.entry(resource_id).or_default();

        if entry.can_grant(&mode) {
            entry.granted.push((txn_id, mode));
            return;
        }

        // 放入等待佇列
        entry.waiting.push_back((txn_id, mode));
        drop(entry);  // borrow checker：需要重新 lookup

        // 等待直到可以拿鎖
        table = self.cvar.wait_while(table, |t| {
            let e = t.get(&resource_id).unwrap();
            // 我還在 waiting 佇列的第一位且還不能 grant
            e.waiting.front() == Some(&(txn_id, mode))
                && !e.can_grant_front()
        }).unwrap();

        // 從 waiting 移到 granted
        let entry = table.get_mut(&resource_id).unwrap();
        entry.waiting.pop_front();
        entry.granted.push((txn_id, mode));
    }

    /// 釋放一個 txn 在某 resource 上的所有鎖
    pub fn unlock(&self, txn_id: u64, resource_id: u64) {
        let mut table = self.inner.lock().unwrap();
        if let Some(entry) = table.get_mut(&resource_id) {
            entry.granted.retain(|(tid, _)| *tid != txn_id);
        }
        self.cvar.notify_all();
    }
}
```

這個實作省略了：wait-for graph 偵測（需要額外 thread 週期跑）、lock upgrade、timeout。真實系統的 lock manager 還要處理 lock ordering（避免 livelock）和 priority（防 starvation）。

## 踩雷清單

1. **2PL ≠ 用鎖就好**。很多人以為「我有加鎖」就夠了，但如果在 growing phase 就開始放鎖，serializable 保證就沒了。Growing / shrinking phase 不能交疊是硬性規則。

2. **Strict 2PL 不是可選的**。基本 2PL 理論上正確，但實務上幾乎不用——cascading abort 讓整個系統不穩定。直接用 rigorous 2PL，在 commit 時一次放所有鎖。

3. **Lock upgrade 的死鎖**。T1 和 T2 都持有 S lock，然後都想升級成 X lock——T1 等 T2 放 S，T2 等 T1 放 S，卡死。升級請求要特殊處理（通常排在等待佇列最前面）。

4. **Wait-for graph 要週期跑，不是即時更新**。即時維護 wait-for graph 代價太高（每次 lock/unlock 都要更新），通常是 background thread 每 N ms 跑一次 cycle detection。這段時間的死鎖會繼續等著。

5. **Lock escalation 的時機很微妙**。太早升級表鎖 → 並發度崩潰；太晚 → lock manager 記憶體爆炸。SQL Server 預設每個物件超過 5000 個 lock 才 escalate。

## 進階延伸

- **Multi-granularity locking（MGL）**：完整的 IS/IX/S/X/SIX 五種鎖模式，Postgres 用的就是 MGL。
- **Predicate locking**：對「滿足某條件的所有 row（包括未來插入的）」加鎖，防 phantom read，代價極高，幾乎沒有系統真的實作。
- **Next-key locking**（InnoDB 用法）：用 gap lock + record lock 組合模擬 predicate locking，是 InnoDB 防 phantom 的實際方案。
- **Deadlock 的 timeout 降級**：Google Spanner 同時用 wait-for graph 和 timeout，兩者取先者。

---

## 本章重點整理

- **2PL 兩個 phase**：growing phase 只拿鎖（到 lock point）、shrinking phase 只放鎖。不交疊 → conflict serializable。
- **Strict 2PL**：X lock 撐到 commit 才放，避免 cascading abort。Rigorous 2PL 連 S lock 也撐。
- **死鎖偵測**：wait-for graph，有環就選 victim 回滾。
- **Lock granularity**：細粒度並發好但 overhead 高；意向鎖讓 multi-granularity 判斷高效。
- **Lock manager**：`lock_table`（resource → entry）＋ `txn_table`（txn → resources），每個 entry 有 granted 佇列和 waiting 佇列。

## 自我檢核

1. 2PL 的 growing phase 和 shrinking phase 分別只能做什麼？為什麼這樣設計能保證 serializable？
2. Strict 2PL 和基本 2PL 的差異？cascading abort 是怎麼發生的？
3. 畫出以下場景的 wait-for graph，判斷是否有死鎖：T1 持有 A 等 B；T2 持有 B 等 C；T3 持有 C 等 A。
4. Lock upgrade 為什麼會造成死鎖？怎麼解決？
5. Intention lock（IS/IX）解決了什麼問題？

## 延伸閱讀

1. **CMU 15-445 Lecture 18: Two-Phase Locking**（Andy Pavlo）— 最清楚的 2PL 教材，附詳細的 conflict graph 推導，直接搜 `cmu 15445 lecture 18 slides`。
2. **《Database System Concepts》Ch 15**（Silberschatz）— 教科書級的 lock protocol 說明，MGL 完整講解在這。
3. **InnoDB Locking**（MySQL 官方文件）— Next-key lock 與 gap lock 的實際細節，看完你懂 InnoDB 的 REPEATABLE READ 怎麼防 phantom。關聯：Ch 20 隔離級別。
4. **Postgres Source: `lock.c`**（`src/backend/storage/lmgr/lock.c`）— 工業級 lock manager 實作，意向鎖的 `LOCKMODE` 陣列與相容性表。
5. **Wait-Die vs Wound-Wait**（Jim Gray, 1976）— 死鎖預防兩種策略的原始論文，理解 aging 設計的直覺。

---

→ [Ch 22 並發控制（二）MVCC](./22-concurrency-mvcc.md)
