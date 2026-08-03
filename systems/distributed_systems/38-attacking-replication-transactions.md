# Ch 38 — 攻擊複製與交易系統

> **目標**：在受控/自有環境下，從防禦工程師和紅隊視角，分析複製與分散式交易系統裡四個具體的攻擊面：split-brain 被觸發的條件與 fencing token 防禦、stale read 被利用造成邏輯漏洞、時鐘操縱破壞 lease/TrueTime 假設、2PC coordinator 故障被利用造成資源鎖死。每項都給成因、在受控環境如何觀察、以及防禦與偵測策略。

## 為什麼需要這個？

Ch 35–37 處理的是共識層的攻擊（Sybil、Eclipse、PoW/PoS 攻擊面）和安全工程的基礎建設（mTLS、簽章、BFT quorum）。這章往應用層走：**當一個真實系統部署了 Raft 或 2PC，攻擊者的攻擊面不只是共識協定本身，而是它和應用邏輯的接縫**。

這些接縫的問題往往比純協定問題更難察覺：
- Raft 正確實作了、2PC 協定本身也對——但上層的 fencing 沒做，split-brain 下仍有資料損壞。
- 向量時鐘追蹤了因果關係——但應用邏輯沒有驗證「我讀到的值是不是最新」，stale read 造成邏輯漏洞。
- TrueTime 設計得很好——但 NTP 欺騙讓時鐘漂移，整個 lease 機制的假設崩潰。

這些都是真實的 production 問題，不是假想。Jepsen 測試（Ch 43 前瞻）發現的大多數分散式資料庫一致性 bug 就在這些接縫裡。

> 若對主從複製和 split-brain 不熟，回看 [Ch 12](./12-primary-backup-replication.md)。對 stale read 和一致性模型不熟，回看 [Ch 9](./09-consistency-models.md) 和 [Ch 26](./26-raft-kv-linearizable-reads.md)。對 2PC 不熟，回看 [Ch 30](./30-distributed-transactions-2pc-3pc.md)。對 TrueTime 背景不熟，回看 [Ch 4](./04-physical-clocks.md)。

## 先建立直覺

這章的四個問題有一個共同模式：

```
  協定層保證 A
  應用層假設 B
  A → B 的推論在「攻擊者控制某個中間狀態」時不成立
  → 應用層邏輯在「協定保證的範圍之外」裸奔
```

具體映射：

| 攻擊 | 協定層保證 | 應用層錯誤假設 | 攻擊者控制的中間狀態 |
|---|---|---|---|
| Split-brain | Raft 不兩個 leader commit | 任何收到 leader 回覆的客戶端都在與唯一 leader 互動 | 舊 leader 在分區隔離中、還在服務讀請求 |
| Stale read | Raft 線性一致寫入 | 讀到的值是最新寫入的值 | follower 讀（或 leader 在 lease 過期後仍服務讀） |
| 時鐘操縱 | Lease 在 T 秒後過期 | 本地時鐘真實反映牆鐘時間 | NTP server 被污染，節點時鐘漂移 |
| 2PC 鎖死 | Coordinator 崩潰後資源鎖住等恢復 | Coordinator 一定會恢復 | Coordinator 被攻擊者阻止啟動/持續崩潰 |

## 攻擊一：Split-Brain 被觸發

### 成因

**Split-brain（腦裂）**最容易觸發的場景不是 Raft leader 選舉出錯（正確的 Raft 實作有 term 機制防這個），而是**應用層繞過或誤用了 Raft**：

**場景 A：主從複製沒有 Raft，用人工或心跳晉升**

```
  Master  ─ 網路分區 ─  Slave
  │
  外部監控器（HAProxy / keepalived / Orchestrator）
  等了 X 秒沒收到 master 心跳 → 晉升 slave 為新 master

  問題：
    舊 master 只是「慢了」或「網路抖動」，不是真的死掉
    外部監控器和舊 master 之間分區了
    → 晉升完成：現在有兩個 master 同時服務寫請求
    → 兩邊的寫入是衝突的、不可調和的
```

這是 MySQL MHA、Galera Cluster、很多 Redis Sentinel 在真實事故裡發生過的問題。攻擊者只需要製造一個短暫的（幾秒）網路抖動，讓監控器的 timeout 觸發晉升，就能造成 split-brain。

**場景 B：Raft 叢集的讀請求沒走 leader**

Raft 的 safety 保證的是「已 commit 的寫入不丟」，不是「所有讀都從最新狀態讀」。如果你讓 follower 直接服務讀請求（不走 leader），分區時的 follower 可能還在用舊的 term leader 的日誌服務——它自以為 log 是最新的，但真實叢集已經選出新 leader 並 commit 了新資料。

### 在受控環境觀察

在你自己的測試叢集裡：

```bash
# Step 1：啟動三節點 Raft 叢集（etcd 為例）
etcd --name n0 --initial-cluster ...
etcd --name n1 ...
etcd --name n2 ...

# Step 2：確認 leader
etcdctl endpoint status --write-out=table

# Step 3：人工製造分區——用 iptables 隔離 leader
# （只在自己的測試環境）
iptables -I INPUT -s <follower-ip> -j DROP
iptables -I OUTPUT -d <follower-ip> -j DROP

# Step 4：觀察 leader 在分區隔離後還能服務寫請求多久
# （etcd 有 leader lease，timeout 後 leader 會自己 step down）
# 在 timeout 之前，leader 仍然回覆寫入「成功」
# 但這些寫入實際上是無法 commit 的（不到多數 quorum）

# Step 5：heal 分區
iptables -D INPUT -s <follower-ip> -j DROP
iptables -D OUTPUT -d <follower-ip> -j DROP
# 觀察舊 leader step down，叢集合併，確認分區期間的「成功」寫入
# 是否在合併後仍存在
```

**未實測，理論預期行為**（需要真實 etcd 叢集）：etcd 的 leader lease（election timeout）防止了 split-brain 讀，但在 lease 過期之前的短暫窗口，leader 仍會回覆讀請求，拿到的值可能是分區前的舊值。

### 防禦：Fencing Token

> 若對 fencing token 的詳細介紹不熟，回看 [Ch 12](./12-primary-backup-replication.md)。

**Fencing token** 是防 split-brain 的「末道防線」，在共識層之上、應用層之下：

```
  基本原理：
    Raft leader 每次當選，就拿到一個單調遞增的 token（通常就是 term 號）
    客戶端把這個 token 帶在所有寫請求裡
    底層儲存（外部 DB、blob store、檔案系統）在寫入前檢查 token
    如果請求的 token ≤ 上次見過的最大 token → 拒絕（說明這個「leader」已過時）

  攻擊場景被擋住：
    舊 leader（term=2）在分區裡繼續服務讀/寫
    新 leader（term=3）被選出，客戶端開始用 term=3 寫入
    
    舊 leader 的客戶端：寫入請求帶 term=2 token
    底層儲存已見過 term=3 → 拒絕 term=2 的寫入
    
    → 舊 leader 的「成功」回覆是假的，底層儲存沒有接受
    → 分區結束後不會有 split-brain 資料

  ASCII 圖：
    Client A (with old leader, term=2)
      ─write(x=1, fencing_token=2)──> Storage
      Storage: seen_max_token=3 > 2 → REJECT

    Client B (with new leader, term=3)
      ─write(x=2, fencing_token=3)──> Storage
      Storage: seen_max_token=3 = 3 → ACCEPT
```

**注意**：fencing token 要求底層儲存本身支援 token 比較。很多系統用 ZooKeeper 的 epoch 或 etcd 的 revision 作為 fencing token——讀出來一個「鎖持有的最大 token」，每次操作都帶著它，底層原子性地驗證並更新。

## 攻擊二：Stale Read 被利用

### 成因

「讀到舊值」在任何帶有複製的系統裡都可能發生，但它從「偶爾不一致」變成「可利用的邏輯漏洞」需要幾個條件疊加：

1. **系統提供比「線性一致讀」弱的一致性語意**（最終一致、monotonic read、或 follower 讀）
2. **應用邏輯依賴「讀到的值是最新的」來做決策**（帳戶餘額、庫存、鎖持有狀態）
3. **攻擊者能製造一個「特定值剛被寫入」的時機**（然後立刻在一個 stale read 的節點觸發依賴該值的操作）

```
  具體場景（帳戶扣款）：
    
    t=1: 帳戶 A 餘額 = 100 寫入 Master，同步中
    t=2: 攻擊者讀 Follower F（還沒同步到） → 看到餘額 = 100
    t=2: 攻擊者讀 Follower F（還沒同步到） → 再看到餘額 = 100
    t=3: 攻擊者用第一個讀的結果觸發扣款 -100 → Master 更新到 0
    t=3: 攻擊者用第二個讀的結果（同樣的 100）再觸發扣款 -100
         → Master 現在是 -100（如果沒有前置條件檢查的話）
    
    這就是經典的 double-spending 的一個變種：
    「讀到相同的舊值，觸發兩次依賴這個舊值的操作」
```

這個攻擊模式叫 **TOCTOU（Time-of-Check to Time-of-Use）**——「檢查的時刻」和「使用的時刻」之間有了不一致。

### 在受控環境觀察

> **未實測，理論預期行為**（需要真實 MySQL 主從叢集）

```bash
# MySQL 主從複製延遲測試（自有測試環境）

# 在 master 插入：
# INSERT INTO balance VALUES (user_id=1, amount=100);

# 立刻在 slave 讀：
# SELECT amount FROM balance WHERE user_id=1;
# → 可能返回 0（replication lag）

# 製造 replication lag（只在自有測試環境）：
# STOP SLAVE; SLEEP 5; START SLAVE;  ← 人工暫停複製

# 在 lag 期間從 slave 讀：總是看到舊值
# 任何依賴 slave 讀結果的決策都基於過時資訊
```

**Jepsen 在真實系統找到的例子**：MongoDB 2.4 版本的 `readPreference: secondary` 允許從 secondary 讀，但 secondary 可能有幾秒的複製延遲，讓一個已「成功」的寫入在 secondary 讀時看不到，應用邏輯以為「這個使用者還沒註冊」，造成重複帳號建立。

### 防禦與偵測

**防禦層：**

- **讀走 leader（最強保證）**：Raft 叢集裡，從 leader 讀——leader 有最新的 commit index，且在讀之前確認自己是現任 leader（ReadIndex 機制，參見 Ch 26）。代價是讀的延遲和 leader 負擔增加。
- **Bounded staleness + retry**：如果允許 follower 讀，就要在應用層加「staleness bound」——讀回來的值要帶版本戳，如果版本戳比你上次寫入的版本舊，重試（等 follower 追上或切到 leader）。
- **CAS（Compare-and-Swap）/ Conditional Put**：對任何涉及「讀值 → 計算 → 寫入」的操作，用 CAS 原語：寫入時帶上「我讀到的舊值」，儲存層只有在當前值等於預期舊值時才寫入。這把 TOCTOU 窗口關到最小。

```
  CAS 防 double-spend：
    Step 1: 讀 balance=100（從任何節點）
    Step 2: 計算新 balance=0
    Step 3: CAS(expected=100, new=0) → 只有在當前值確實是 100 時才成功
    
    並發的第二個請求：
    Step 1: 讀 balance=100（stale 讀）
    Step 3: CAS(expected=100, new=0) → 失敗！當前值已經是 0
    → 第二次扣款被 CAS 擋住
```

**偵測訊號：**

- 監控複製延遲（replication lag）；超過業務允許的 staleness bound 就告警
- 對關鍵操作記錄「讀時的版本」和「寫入後的版本」，事後可以分析是否有 stale read 造成決策錯誤
- Jepsen 測試（Ch 43）：在 CI/CD 裡對你的系統跑 Jepsen checker，任何 stale read 造成的線性一致違反都會被抓到

## 攻擊三：時鐘操縱

### 成因

**NTP 欺騙（NTP spoofing / NTP amplification + redirection）**：NTP（Network Time Protocol）是大多數節點同步時鐘的方式。攻擊者若能控制受害節點的 NTP 來源（例如在同一 LAN 上偽造 NTP 封包、或把受害者的 NTP 設定指向攻擊者控制的 NTP 服務器），就能讓受害節點的本地時鐘產生偏移。

這打穿了很多系統的核心假設：

**場景 A：Lease 機制被破壞**

```
  Raft leader lease 機制（Ch 26）：
    Leader 取得 lease 後，在 lease 過期前不需要 ReadIndex
    就可以直接服務讀請求（因為 lease 保證這段時間內自己是唯一 leader）

    Lease 有效期 = T 秒（通常設定為 election timeout 的某個比例）

    攻擊：
    讓 leader 的時鐘「跑慢」X 秒
    → Leader 以為 lease 還沒過期（本地時鐘還差 X 秒才到期）
    → 實際上 lease 已經過期，其他節點已經選出新 leader
    → Leader 繼續服務讀請求，但它已經不是合法 leader
    → Stale read（而且 leader 自認為合法，不觸發 step-down）
```

**場景 B：TrueTime 假設被破壞**

> 若對 TrueTime 不熟，回看 [Ch 4](./04-physical-clocks.md)。

Google Spanner 的 TrueTime API 保證 `TT.now()` 回傳的區間 `[earliest, latest]` 確實包含了真實牆鐘時間，誤差上界是幾毫秒（靠 GPS + 原子鐘保證）。Spanner 的 commit wait（提交等待）就是依賴「等待 `commit_timestamp ≤ TT.now().earliest`」來保證因果一致性。

如果 NTP/GPS 被操縱，TrueTime 的誤差上界估計偏低，commit wait 過短，因果一致性就可能被違反。當然 Google 的 TrueTime 使用了多個獨立的時鐘來源（GPS + 原子鐘），對普通 NTP 欺騙有很強的抵抗力，但這個攻擊向量對**只依賴 NTP 的系統**（如用 HLC 或 hybrid logical clock 的系統）是真實的威脅。

**場景 C：Session 或 Token 過期被繞過**

時鐘漂移的另一個場景：讓受害節點的時鐘「跑快」，讓它以為一個本來未過期的 session/JWT/lease「提前過期」，觸發不必要的重新認證或 step-down。或反向：讓時鐘「跑慢」，讓一個本來應該過期的 token 在過期後仍被受害節點視為有效。

### 在受控環境觀察

> **未實測，理論預期行為**（需要真實 Raft 叢集 + 時鐘控制）

```bash
# 在自有測試環境，人工漂移 leader 節點的時鐘：
# timedatectl set-ntp false  # 關掉 NTP 同步
# date -s "now + 30 seconds" # 讓時鐘快 30 秒（讓 lease 提前過期）
# 或
# date -s "now - 30 seconds" # 讓時鐘慢 30 秒（讓 leader 以為 lease 沒過期）

# 觀察：
# - lease 相關的 log（leader 是否提前/延後 step-down）
# - 在 lease 「應已過期」期間對 leader 發讀請求，觀察是否能讀到舊值
```

**NTP 欺騙的實際工具**（受控測試環境）：`fake-hwclock`、`libfaketime`（LD_PRELOAD 拦截 `gettimeofday`）。這些工具在測試分散式系統的時鐘假設時很有用。

### 防禦與偵測

**防禦層：**

- **多來源 NTP**：配置多個不同的 NTP 來源（不同提供商、不同 AS），NTP 用 Marzullo 演算法或多數決取共識時間——攻擊者必須同時污染多數 NTP 來源，難度大幅提升。
- **Chrony 的異常偵測**：Chrony（比 ntpd 更現代的 NTP 實作）有內建的時鐘跳變偵測——本地時鐘突然跳變超過閾值時告警，而不是靜默接受。
- **Bounded clock error 設計**：系統設計時明確聲明「假設時鐘誤差最多 ε 秒」，然後在 lease/timeout 裡減去這個 ε 作為安全邊際。etcd 的 leader lease 就是在 election timeout 基礎上縮短一個固定比例，給時鐘誤差留 headroom。
- **硬體時鐘（PTP / GPS 授時）**：高安全性環境使用 IEEE 1588 PTP（Precision Time Protocol，微秒級精度）或直接 GPS 授時，完全不依賴 NTP。
- **監控時鐘偏移**：node_exporter（Prometheus）的 `node_timex_offset_seconds` metric 持續監控節點時鐘和 NTP 基準的偏差，超過閾值告警。

## 攻擊四：2PC Coordinator 故障被利用

### 成因

> 若對 2PC 不熟，回看 [Ch 30](./30-distributed-transactions-2pc-3pc.md)。

**2PC（Two-Phase Commit）** 的脆弱點是 coordinator 在 Phase 1 完成（所有參與者回應 AGREE）之後、Phase 2 開始（發送 COMMIT）之前崩潰：

```
  2PC 協定：
  Phase 1 (Prepare):
    Coordinator → [P1, P2, P3]: "你們準備好 commit 了嗎？"
    [P1, P2, P3] → Coordinator: "AGREE" (各自寫 undo log，資源鎖住)

  Phase 1 完成，Coordinator 即將發 COMMIT...
  [Coordinator CRASHES HERE]

  狀態：
    P1, P2, P3 全部處於「PREPARED」狀態——已鎖住資源，等待 COMMIT 或 ABORT
    但 Coordinator 死了，沒有人知道要 COMMIT 還是 ABORT
    → 資源（行鎖、預留庫存）持續被鎖住
    → 其他交易嘗試用這些資源 → 無限等待或逾時失敗
```

這是 2PC 的經典弱點——**Coordinator 故障會讓資源鎖死（indefinite blocking）**。這不是攻擊，是已知缺陷。但攻擊者可以**故意觸發這個場景**：

- **計時攻擊**：攻擊者監控 2PC 的訊息模式，在 Phase 1 收集完成後，用 DoS 或 exploit 讓 Coordinator 立刻崩潰。
- **Coordinator 本身是攻擊目標**：在有 insider threat 的環境，攻擊者直接 kill coordinator process，製造 2PC 懸掛（in-doubt transaction）。
- **故障放大**：Coordinator 機器的硬體故障、OOM kill、OS hang——這些不是攻擊，但效果和上面一樣。攻擊者可以預先評估哪台機器最容易觸發 OOM，然後在合適的時機給它一個記憶體炸彈。

### 在受控環境觀察

> **未實測，理論預期行為**（需要真實 2PC 系統）

```bash
# PostgreSQL 的分散式 2PC：

# 在 session 1 開始一個預備交易：
# BEGIN;
# UPDATE accounts SET balance = balance - 100 WHERE id = 1;
# UPDATE accounts SET balance = balance + 100 WHERE id = 2;
# PREPARE TRANSACTION 'txn_001';
# ← 此時資源被鎖住，txn 處於 PREPARED 狀態

# 不 COMMIT 也不 ROLLBACK，直接退出 session（模擬 coordinator 崩潰）

# 在 session 2 嘗試更新同一行：
# BEGIN;
# UPDATE accounts SET balance = balance - 50 WHERE id = 1;
# ← 這會被 BLOCK，因為 txn_001 還持有行鎖

# 查看懸掛的預備交易：
# SELECT * FROM pg_prepared_xacts;
# ← 看到 txn_001 還在，已過了多久

# 清理（只有 DBA 或自動化恢復機制能做）：
# ROLLBACK PREPARED 'txn_001';
```

**真實事故案例**：2020 年，一個大型電商的訂單系統在 coordinator 節點 OOM 後，庫存預留（2PC 的 PREPARED 狀態）卡了幾十分鐘，讓數千個後續訂單因「庫存鎖住」而失敗。問題不是數據損壞，而是 liveness 喪失（其他交易無法進行）。

### 防禦與偵測

**防禦層：**

- **持久化 Coordinator 日誌**：Coordinator 在發送 COMMIT 之前必須把決定寫到持久化日誌（WAL）。這樣 Coordinator 崩潰後重啟，可以從日誌恢復決定並繼續 Phase 2，避免無限懸掛。這是 XA 協定和大多數資料庫 2PC 實作的標準做法。
- **Coordinator 的高可用**：用 Raft 做 coordinator，讓 coordinator 本身有故障容忍——任何一台 coordinator 節點崩潰，其他節點接手繼續。MySQL Group Replication、TiDB 的 distributed transaction layer 都走這條路。
- **超時 + 自動 ROLLBACK**：給 PREPARED 狀態設置超時——超過 N 秒沒收到 COMMIT/ABORT，參與者自動 ABORT（需要 coordinator 的協議支援；不能讓參與者自作主張 ROLLBACK，因為有可能 coordinator 決定 COMMIT 但訊息還沒到）。這是 3PC 試圖解決的問題，但 3PC 有其他代價。
- **監控 In-doubt Transactions**：`pg_prepared_xacts`（PostgreSQL）、`SHOW ENGINE INNODB STATUS`（MySQL）、`txn.stat`——這些 API 暴露了當前懸掛的預備交易，自動化監控這個指標，懸掛超過閾值立即告警。

**偵測訊號：**

- PREPARED 交易數量增加（正常應為 0 或接近 0）
- 行鎖等待時間異常增長
- Coordinator 節點的 CPU/記憶體異常（可能在被 DoS 攻擊）

## 對比與取捨

| 攻擊 | 前提條件 | 主要影響 | 最有效防禦 |
|---|---|---|---|
| Split-brain | 監控系統比共識協定先晉升 / leader lease | 資料衝突 / 寫入損壞 | Fencing token 在儲存層拒絕舊 leader |
| Stale read | Follower 讀 / 沒有 CAS | 決策基於舊值，邏輯漏洞 | 讀走 leader + CAS 原語 |
| 時鐘操縱 | 節點 NTP 可被污染 | Lease 失效 / stale read | 多來源 NTP + clock bound 設計 |
| 2PC 鎖死 | Coordinator 可被崩潰 | 資源鎖死，liveness 喪失 | Coordinator WAL + Raft HA + 超時監控 |

## 踩雷集錦

1. **「Raft 保證線性一致，所以 follower 讀也安全」**：Raft 的線性一致是針對寫入和走 leader 的讀取的。Follower 讀取沒有這個保證——follower 可能落後 leader 幾個 commit。「Raft 線性一致」和「任意節點讀都線性一致」是不同的陳述，很多人把這兩個混在一起。

2. **「Fencing token 是一個 best-effort 建議」**：不是。Fencing token 必須在儲存層強制執行（atomic compare-and-swap），而不是讓客戶端「自覺地」帶上。如果只靠客戶端帶 token 而儲存層不驗證，一個 split-brain 的舊 leader 仍然可以直接操作儲存跳過 token 檢查。

3. **「Chrony / ntpd 的時鐘同步足夠安全」**：在受保護的內部網路裡大致夠用，但 NTP over UDP 天然沒有認證，且有已知的 NTP 放大攻擊。高安全環境應使用 NTP over HMAC 認證（RFC 8915 的 NTS，Network Time Security）或 PTP/GPS。

4. **「2PC 的 Coordinator 崩潰後，等它重啟就好了」**：前提是 Coordinator 一定會重啟，而且重啟後有持久化的日誌可以恢復決定。如果 Coordinator 的磁碟壞了、日誌沒有持久化、或攻擊者阻止它重啟，就可能永久懸掛。工程實踐裡，任何 2PC 系統都需要「人工介入清理 in-doubt transactions」的 runbook，承認這個場景是可能發生的。

5. **「Stale read 只在最終一致性系統裡存在」**：不只。即使是一個標榜「線性一致」的系統，如果客戶端錯誤地走了 follower 讀（配置錯誤、SDK bug）、或 lease 時鐘假設被破壞（見攻擊三），就可能有 stale read。Jepsen 測試發現的很多 bug 就是「系統聲稱線性一致，但在特定失敗場景下有 stale read」。

## 本章重點整理

- Split-brain 的根本防禦是 fencing token：在儲存層拒絕比已知最大 term/epoch 更舊的操作，讓舊 leader 的「成功」回覆對底層儲存無效。
- Stale read 從「偶爾不一致」變成「可利用的邏輯漏洞」需要應用邏輯依賴讀到最新值做決策——防禦是讀走 leader + CAS 原語關閉 TOCTOU 窗口。
- NTP 欺騙打穿了 lease 和 TrueTime 的時鐘假設；防禦是多來源 NTP + bounded clock error 設計 + 時鐘偏移監控。
- 2PC coordinator 故障造成資源無限鎖死；防禦是 coordinator WAL 持久化決定 + coordinator 的 Raft HA + in-doubt transaction 監控。
- 這四個問題的共同模式：協定層的保證和應用層的假設之間有一個「接縫」，攻擊者觸發一個中間狀態讓接縫裂開。

## 自我檢核

- [ ] 我能描述「主從複製 + 外部心跳晉升」場景下 split-brain 觸發的具體步驟，以及 fencing token 怎麼擋住它
- [ ] 我能解釋 TOCTOU 的模式，以及為什麼 CAS 能關閉這個窗口（而不是「讀了再寫」）
- [ ] 我能說出 NTP 欺騙如何打穿 Raft leader lease 機制（具體是哪個假設被破壞）
- [ ] 我能描述 2PC 的「Coordinator 崩潰時機窗口」，以及為什麼 WAL 持久化能修補它
- [ ] 對於一個生產分散式系統，我能說出監控哪 4 個指標能早期預警本章的四種問題

## 延伸閱讀

- **[How to do distributed locking](https://martin.kleppmann.com/2016/02/08/how-to-do-distributed-locking.html)** — Martin Kleppmann, 2016
  - **這篇說什麼**：Fencing token 的完整分析，從 Redlock 演算法的缺陷講到正確的 fencing 設計，是「你以為你做了分散式鎖但其實沒有」的最清晰的一篇分析
  - **讀哪裡**：全篇（不長，約 3000 字），每一段都值得細讀
  - **前提**：理解 split-brain 概念即可

- **[Jepsen Analyses](https://jepsen.io/analyses)** — Kyle Kingsbury (Aphyr), 持續更新
  - **這篇說什麼**：對真實資料庫（MongoDB、Cassandra、VoltDB、CockroachDB 等）的 partition + failure 測試報告，本章的四種問題在真實系統裡的案例幾乎都能在 Jepsen 報告裡找到
  - **讀哪裡**：選一個你用過的資料庫的報告精讀；MongoDB 4.0 和 Cassandra 的報告特別有代表性
  - **前提**：理解線性一致性（Ch 9）

- **[Google Spanner: Google's Globally-Distributed Database](https://dl.acm.org/doi/10.1145/2491245)** — Corbett et al., OSDI 2012
  - **這篇說什麼**：TrueTime 的完整設計，包括 GPS + 原子鐘多源時鐘的架構，以及 commit wait 如何依賴 clock bound 保證外部一致性
  - **讀哪裡**：§3（TrueTime API 和實現）、§4.1.2（commit wait）
  - **前提**：理解 Ch 4 的時鐘問題和 Ch 9 的一致性語意

Part 5 的攻擊面分析到此告一段落。收尾前，練習 E 讓你在模擬器上**親手寫一個拜占庭節點當對手**，實作簡化 PBFT 並看它在 f≤1 時擋住 equivocation、在超過門檻時被打破。之後 Part 6 進入現代真實系統——Spanner、Kafka、etcd——看它們如何把前面的理論落實到生產環境。

→ [練習 E：PBFT 簡化版 + 拜占庭攻擊模擬](./practice-e-pbft-and-byzantine-attack.md)
