# Ch 37 — 分散式系統的安全工程

> **目標**：在有對手存在的前提下，把分散式系統的安全工程具體化——節點間的認證與加密通道（mTLS）、拜占庭 quorum 系統（Malkhi-Reiter 的 Byzantine quorum systems）、可歸責性與行為證明（PeerReview）、以及訊息認證碼（MAC）在拜占庭環境下的局限與數位簽章的必要性。最後給一份可操作的防禦清單。

## 為什麼需要這個？

Ch 2 定義了「拜占庭失敗」：節點不只可能崩潰，還可能主動說謊、發送矛盾訊息、選擇性靜默。Ch 33 的 PBFT 在這個模型下讓共識仍成立。但 PBFT 解的是「達成一致」的問題；它沒有告訴你：

- 節點之間的**連線**是否有人在竊聽或竄改？
- 如果一個節點確實說謊了，你能**事後舉證**它說謊了嗎？
- 分散式儲存系統（不是共識，是讀寫）在有拜占庭節點時能有多強的保證？
- Ch 6 的版本向量（vector clock）防的是「誰先發」，但有對手存在時它能防**偽造**嗎？

這章把安全工程的視角加進來：**不只讓系統在失敗模型裡「仍能跑」，而是讓它在有主動攻擊者的世界裡「仍能信任」**。

> 若對拜占庭失敗模型不熟，回看 [Ch 2](./02-failure-and-network-models.md)。對 PBFT 不熟，回看 [Ch 33](./33-pbft.md)。對版本向量不熟，回看 [Ch 6](./06-vector-clocks.md)。

## 先建立直覺

誠實的分散式世界和有對手的分散式世界差在哪裡？

```
  誠實的世界（Ch 0–24 的假設）：
    節點可能崩潰，但不會主動欺騙
    訊息可能延遲、丟失，但不會被竄改
    「我收到 node A 的訊息」= 確實是 node A 發的

  有對手的世界（Ch 32 後的假設）：
    節點可能主動偽造訊息、發矛盾訊息
    中間人可能竊聽、竄改、重放訊息
    「我收到宣稱來自 node A 的訊息」≠ 確實是 node A 發的

  必須加入的工具：
    通道加密 + 認證 → 防竊聽/竄改/中間人
    數位簽章 → 防偽造（某個宣稱的發送者）+ 防否認
    訊息序號/nonce → 防重放
    quorum 設計 → 容忍有限數量的拜占庭節點
    可歸責性記錄 → 事後能舉證誰說謊了
```

## 節點間認證與加密通道

### 為什麼 TCP + 應用層協定不夠

Raft 或 Paxos 的訊息——心跳、投票請求、AppendEntries——如果在不加密的通道上傳輸：

- **竊聽**：中間人看到所有 raft 訊息，包括 client 請求的內容。
- **竄改**：中間人把 leader 的 AppendEntries 裡的 entries 換掉，follower 複製到假資料。
- **注入**：攻擊者偽造一條合法格式的 RequestVote 讓節點觸發選舉。
- **重放**：攻擊者重放舊的 commit 訊息，讓節點以為某個已撤回的值是被確認的。

這些攻擊在「誠實但可能崩潰」的模型下不存在，但一旦把系統部署到有潛在對手的網路（公雲 VPC、多租戶資料中心、有 insider threat 的環境），它們就全都成立。

### mTLS：雙向 TLS 認證

**mTLS（mutual TLS）**是最常見的節點間安全通道方案。標準 TLS 只驗證伺服器端（你連向的對方是誰），mTLS 雙向驗證——客戶端也必須提供憑證。

```
  標準 TLS（單向）：
    Client → Server: "你是 etcd-node-2.cluster.local 嗎？"
    Server → Client: （出示憑證）"是的，CA 簽名在此"
    Client 驗證成功 → 建立加密通道

  mTLS（雙向）：
    Client → Server: "你是 etcd-node-2 嗎？我是 etcd-node-0，（出示我的憑證）"
    Server 驗證 Client 憑證（是否由叢集 CA 簽發？是否在節點清單裡？）
    Server → Client: "確認。我是 etcd-node-2，（出示我的憑證）"
    Client 驗證 Server 憑證
    雙向驗證通過 → 建立加密通道

  結果：
    - 只有擁有叢集 CA 簽發憑證的節點能接入叢集
    - 所有訊息加密傳輸（防竊聽）
    - 憑證綁定節點身份（防偽造/中間人）
    - 每條 TLS session 有唯一會話金鑰（防重放）
```

**在 Go 裡設置 mTLS**：

```go
// 伺服器端：要求客戶端提供憑證，並驗證它由我們的 CA 簽發
tlsConfig := &tls.Config{
    ClientAuth: tls.RequireAndVerifyClientCert,
    ClientCAs:  clusterCACertPool,          // 叢集自簽 CA
    Certificates: []tls.Certificate{serverCert}, // 本節點的憑證
    MinVersion: tls.VersionTLS13,
}
listener, _ := tls.Listen("tcp", ":8443", tlsConfig)

// 客戶端：提供自己的憑證，驗證對方的 CA
tlsConfig := &tls.Config{
    Certificates: []tls.Certificate{clientCert},
    RootCAs:      clusterCACertPool,
    ServerName:   "etcd-node-2.cluster.local",
    MinVersion:   tls.VersionTLS13,
}
conn, _ := tls.Dial("tcp", "etcd-node-2:8443", tlsConfig)
```

etcd、Consul、Kafka、Kubernetes API Server 全部都用 mTLS 做節點間通訊的根基。

### 訊息層的數位簽章

mTLS 保護的是「傳輸通道」——訊息在通道裡傳輸期間是安全的。但如果你在 PBFT 裡把某個 PRE-PREPARE 訊息**轉發**給另一個節點，轉發者能偽造或竄改這條訊息嗎？

答案是：在純粹 mTLS 保護下可以——mTLS 只證明「這條訊息來自建立這條 TLS 連線的那個節點」，但沒有對訊息的**原始作者**做認證。

**解法：訊息層加數位簽章**——原始發送者對訊息內容簽名，接收者驗簽。無論訊息被誰轉發，偽造者沒有原始作者的私鑰，無法偽造合法簽章。

```
  PBFT PRE-PREPARE 帶簽章：
    Primary 發送：
      msg = {view=0, seq=1, value="TX:A->B:100"}
      sig = Sign(primary_private_key, hash(msg))
      broadcast(msg || sig)

  Replica 轉發給遲到的 Replica：
      relayed_msg = msg || sig   ← 不是 forwarder 重新簽名，是原始簽章
      任何人都能驗 primary 的公鑰 → 確認這確實是 primary 發的

  拜占庭 Forwarder 若試圖竄改：
      修改 msg.value → hash(msg) 變了 → 舊 sig 驗證失敗
      重新用 forwarder 私鑰簽 → 但 primary 公鑰驗證失敗 → 被偵測
```

> 回看 [Ch 6](./06-vector-clocks.md)：Vector Clock 追蹤「誰在誰之後發」（causal ordering）。但向量時鐘本身沒有認證——一個拜占庭節點可以偽造另一個節點的向量時鐘值。如果你在有對手的環境裡用向量時鐘，你需要對向量時鐘值加簽章，否則任何節點都能宣稱「我代表 node A 把這個值設成 5」。

### 防重放：序號與 Nonce

數位簽章防竄改，但它不防重放（replay）——攻擊者錄下一條合法的已簽名訊息，之後重發。

**解法**：

1. **單調遞增序號**（PBFT 的 `seq` 欄位）：每個訊息帶唯一序號，接收者追蹤「已看過的最大序號」，低於或等於這個序號的訊息直接丟棄。
2. **Nonce**：每次新的互動生成一個隨機數 nonce，接收者驗證回應帶著它傳過去的 nonce。
3. **Timestamp 視窗**：帶時間戳的訊息，超過允許窗口（如 5 分鐘）就拒絕——需要時鐘大致同步（Ch 4 的問題）。

## 拜占庭 Quorum 系統

### 共識 vs. 儲存

PBFT 解的是「在拜占庭環境下達成共識（log replication）」。但分散式系統裡另一個重要原語是**分散式儲存**：多個副本存同一份資料，客戶端讀寫任意副本，保證讀到的值不比寫入的值舊。

> 若對傳統 quorum 讀寫協定不熟，回看 [Ch 13](./13-quorum-replication.md)。

傳統 quorum 系統（Ch 13）在崩潰容錯模型下工作：用 `W + R > N`（寫 quorum + 讀 quorum > 總副本數）保證讀寫重疊。但在拜占庭模型下，崩潰容錯的 quorum 設計失效——拜占庭節點可以對讀請求回應假值，僅憑「多數回覆」無法確認正確性。

### Malkhi-Reiter Byzantine Quorum Systems

**Dahlia Malkhi 和 Michael Reiter 在 1998 年**提出拜占庭 quorum 系統（Byzantine quorum systems）。核心設計思路：

**崩潰容錯 quorum 的保證**：任何兩個 quorum 集合 Q₁ 和 Q₂ 必須相交（`Q₁ ∩ Q₂ ≠ ∅`），相交節點攜帶最新值，讀端從最新的回覆取值即可。

**拜占庭 quorum 的問題**：如果相交節點是拜占庭的，它可以對 Q₁ 說「值是 A」、對 Q₂ 說「值是 B」，讓讀者得到矛盾回應。

**解法：更大的相交**

定義：`f` 個拜占庭節點，`N = 3f + 1` 個總副本，quorum 大小 `Q = 2f + 1`。

**關鍵性質**：任意兩個 quorum 集合相交後，誠實節點數 ≥ `f + 1`（至少一個誠實節點，但更強）：

```
  |Q₁ ∩ Q₂| = |Q₁| + |Q₂| - N = (2f+1) + (2f+1) - (3f+1) = f+1
  其中 ≥ f+1 個節點相交，最多 f 個是拜占庭的
  → 至少 1 個相交節點是誠實的
```

但「至少 1 個誠實」是否夠？如果對每個讀請求有 f+1 個相交，其中 f 個都可能說謊，我們不能憑多數決找出哪個說真話。

**解法：讀端取多數**

```
  讀協定（帶簽章）：
    1. 向所有 N 個副本發讀請求
    2. 等待 2f+1 個回覆
    3. 要求相同值的回覆達到 f+1 個，且帶有合法簽章
       （f 個拜占庭節點無法偽造 f+1 個不同公鑰的簽章）
    4. 取出現 f+1 次的值為合法值

  寫協定：
    1. 讀出當前最新值（帶時間戳 / 版本號）
    2. 遞增版本號，對新值簽名，發送給所有副本
    3. 等待 2f+1 個確認
    4. 視為寫入完成
```

```
  N=4 (f=1) 的例子：
    副本：r0(誠實)  r1(誠實)  r2(誠實)  r3(拜占庭)
    
    寫 v=42，版本=1：
      r0: (42, v=1, sig_writer) ✓
      r1: (42, v=1, sig_writer) ✓
      r2: (42, v=1, sig_writer) ✓
      r3: 收到但回應說「我存了 v=1 但值是 99」

    讀：
      r0 回應：(42, v=1, sig_r0)
      r1 回應：(42, v=1, sig_r1)
      r2 回應：(42, v=1, sig_r2)
      r3 回應：(99, v=1, sig_r3)  ← 謊話

      讀端收到 4 個回覆，其中 (42, v=1) 出現 3 次 > f+1=2
      → 輸出 42（正確值）
      → 注意到 r3 的回覆不一致，可以記錄為可疑節點
```

**關鍵**：這個設計要求「副本對自己存的值簽名，且寫端也對值簽名」——沒有簽章，拜占庭副本可以假冒誰說的都行。簽章讓「f+1 個相同簽名的值」成為可查驗的證據。

## 可歸責性與 PeerReview

### 為什麼光「防住」不夠

PBFT 保證了在 f 個拜占庭節點存在時共識仍成立。但它沒有解決：**如果有節點偏離協定，我能事後舉證嗎？**

這在以下場景很重要：
- **許可制聯盟鏈**：你知道哪些組織各跑了幾個節點，若他們作弊，你需要有簽名的鐵證去追責（法律責任、取消資格）。
- **多方計算**：協議結束後某方聲稱另一方作弊，需要客觀可查驗的證明。
- **安全審計**：一個懷疑遭受攻擊的節點想向其他節點展示攻擊者的行為紀錄。

這就是**可歸責性（accountability）**：不只要讓攻擊失效（BFT 共識），還要能在事後拿出**對任意第三方都可查驗的、不可否認的**行為偏離證明。

### PeerReview

**PeerReview**（Haeberlen et al., SOSP 2007）是分散式可歸責性的代表系統。核心思想：

```
  每個節點維護一個「已認證的行為日誌（authenticated log）」：
    - 把每一個輸入（收到的訊息 + 它的簽章）記錄下來
    - 把每一個輸出（發出的訊息）記錄下來
    - 日誌本身用 hash 鏈（每個條目 hash 前一個）防竄改
    - 日誌的最新 hash（log head）定期廣播給見證人（witnesses）

  見證人協定：
    任何節點可以向見證人提交「某節點的某個行為是偏離協定的」指控
    見證人去問被指控節點要日誌
    如果日誌裡有偏離的確鑿證據（輸入 → 協定要求輸出 X → 實際輸出 Y ≠ X）
    → 被指控節點「有罪」，因為它自己的日誌是簽名的，無法否認
    如果被指控節點拒絕提供日誌
    → 同樣「有罪」（隱藏日誌本身就是偏離協定的行為）
    如果指控不成立
    → 指控者被標記為惡意
```

**兩個關鍵性質**：

1. **誠實節點不可能被錯誤定罪**：因為誠實節點的行為嚴格遵循協定，日誌裡找不到偏離。
2. **拜占庭節點無法逃過追責**：它的日誌要麼有偏離行為（被定罪），要麼它拒絕提供（等同認罪）；它無法偽造日誌（hash 鏈 + 見證人持有歷史 head，任何竄改都被偵測）。

PeerReview 不能「阻止」拜占庭節點偏離——這是 BFT 協定的工作。PeerReview 做的是**事後追責**：給受害者一個可向任何人展示的、不可辯駁的證明。

## 拜占庭環境下的訊息認證

### MAC vs. 數位簽章

**訊息認證碼（MAC，Message Authentication Code）**和**數位簽章**都能防訊息被竄改，但在拜占庭環境下有決定性的差異：

```
  MAC（例如 HMAC-SHA256）：
    A 和 B 共享一個對稱金鑰 K_AB
    MAC = HMAC(K_AB, message)
    B 驗證 MAC → 確認訊息來自 A（或任何持有 K_AB 的人）

    問題：A 拿著 MAC 給 C 看，說「這是 B 說的！」
    C 無法驗證——C 沒有 K_AB，也無法確認 MAC 是 A 還是 B 生成的
    → MAC 不提供「第三方可查驗」的不可否認性

  數位簽章（例如 Ed25519）：
    A 有私鑰 SK_A，對應公鑰 PK_A（公開）
    sig = Sign(SK_A, message)
    任何人持有 PK_A 都能驗 sig → 確認訊息確實由持有 SK_A 的人簽發

    A 拿著 sig 給 C 看，說「這是 B 說的！」
    C 用 PK_B 驗簽 → 失敗（B 的公鑰驗不過 A 的私鑰簽的東西）
    → 不可否認性：只有 B（持有 SK_B）才能生成被 PK_B 驗通的簽章
```

**在拜占庭 quorum / PeerReview 裡，必須用數位簽章而不能只用 MAC**：

- MAC 的問題：拜占庭節點 A 和 B 共享 K_AB，A 可以對 C 宣稱「B 用 MAC 說了 X」——C 無法查驗是 B 還是 A 自己生成的 MAC。
- 簽章的保證：如果你收到一條帶有 B 的合法簽章的訊息，你能向任何第三方展示那條訊息是 B 發的，B 無法否認（除非 B 的私鑰被盜，但那是另一個問題）。

**性能代價**：數位簽章比 MAC 慢 1–3 個數量級。因此：

- **高頻、低安全性路徑**（內部 heartbeat、Raft 內部節點間通訊）：MAC 或 mTLS 即可。
- **需要第三方可查驗或不可否認性的路徑**（PBFT 的協定訊息、PeerReview 日誌、跨組織交換）：必須用數位簽章。

### Byzantine Quorum 的底層流程圖

```
  寫入 v=42（writer 對值簽名）：
  
  Writer ─── write(42, v=1, sig_writer) ──> r0, r1, r2, r3
  
  等 2f+1=3 個確認：
    r0: ACK → r0 存入 (42, v=1, sig_writer)
    r1: ACK
    r2: ACK
    r3（拜占庭）: 可能靜默或回應假確認
  
  Writer 收到 3 個 ACK → 寫入完成

  讀取（Read 重構）：
  
  Reader ─── read ──> r0, r1, r2, r3
  
  收到 4 個回覆：
    r0: (42, v=1, sig_writer, sig_r0)
    r1: (42, v=1, sig_writer, sig_r1)
    r2: (42, v=1, sig_writer, sig_r2)
    r3（拜占庭）: (99, v=1, sig_writer?, sig_r3)
         ← 但 sig_writer 不對（r3 沒有 writer 私鑰），驗簽失敗
    
  Reader：(42, v=1) 有 3 個合法簽名 ≥ f+1=2 → 輸出 42 ✓
```

**關鍵設計**：writer 對值簽名（不是 replica 自己對存入的值簽名）。replica 的 `sig_r_i` 只是「我確認我存了這個 writer 簽名的值」的收據，不是對值本身的認可。拜占庭 replica 若試圖改值，就需要偽造 writer 的簽章，沒有私鑰就做不到。

## 防禦清單

在有對手存在的分散式系統裡，以下是分層防禦的可操作項目：

**層 1：通道安全**
- [ ] 所有節點間通訊走 mTLS（雙向憑證認證）
- [ ] 使用叢集專屬 CA（不共用公共 CA），叢集 CA 的私鑰離線保存
- [ ] TLS 版本最低 1.3；禁用 TLS 1.0/1.1 和弱 cipher suite
- [ ] 憑證有短效期（≤ 90 天）+ 自動輪換（手動輪換是事故溫床）

**層 2：訊息認證**
- [ ] 高安全性路徑的協定訊息加數位簽章（Ed25519 或 ECDSA P-256）
- [ ] 每條訊息帶單調遞增序號，接收方拒絕舊序號（防重放）
- [ ] 驗簽失敗的訊息不處理，且記錄告警（不要靜默丟棄）

**層 3：失敗偵測與可歸責**
- [ ] 在 BFT 叢集裡實作行為日誌（authenticated log），至少覆蓋關鍵決策路徑
- [ ] 見證人節點保存 log head 的歷史，防節點事後竄改日誌聲稱
- [ ] 異常行為（矛盾訊息、過期訊息、拒絕回應）自動記錄和告警

**層 4：讀寫安全（分散式儲存）**
- [ ] 如果對手存在，傳統 quorum 讀寫不夠——需要 Byzantine quorum（`Q = 2f+1`, `N = 3f+1`）
- [ ] 讀端從 f+1 個相同值的簽名回覆才信任，而不是多數決
- [ ] 寫端同樣需要 2f+1 個確認才算持久化

**層 5：身份與準入**
- [ ] 許可制叢集：節點加入需要管理員明確授權（不接受未簽發憑證的節點）
- [ ] 憑證撤銷機制：一個節點被懷疑妥協，能立即撤銷其憑證（CRL 或 OCSP）
- [ ] 最小權限：節點憑證只授權該節點的角色（leader 可以做的事 follower 不該能做）

## 踩雷集錦

1. **「mTLS 之後訊息就安全了」**：mTLS 保護傳輸通道，不保護訊息的語意正確性。一個拜占庭節點在 mTLS 通道裡仍然可以發合法格式但內容虛假的訊息。mTLS 防竊聽和中間人，數位簽章防偽造原始發送者。

2. **「MAC 和簽章在 BFT 裡可以互換」**：不可以。MAC 依賴共享金鑰，在拜占庭環境裡共享金鑰帶來「另一方也能偽造」的問題，讓可歸責性消失。數位簽章的不可否認性在 BFT 和 PeerReview 這類系統裡是必要的。

3. **「Byzantine quorum 只是普通 quorum 把大小調大一點」**：不是。Byzantine quorum 的協定（讀端等 f+1 個相同簽名值）和普通 quorum（多數決）在語意上是不同的。普通 quorum 讀最新值的「最新」是靠版本號/timestamp；Byzantine quorum 是靠「f+1 個獨立簽名的一致回覆」——這需要寫端先簽名，讀端才有辦法驗。

4. **「PeerReview 能阻止拜占庭攻擊」**：不能，PeerReview 是事後追責，不是實時防禦。PBFT 先讓共識在拜占庭下仍成立，PeerReview 讓壞節點事後被追究——兩者是補充關係，不是替代關係。

5. **「TLS 1.2 夠了」**：TLS 1.2 有一些已知弱點（降級攻擊、CBC 模式漏洞），而且沒有 Forward Secrecy 的強制保障。現代系統裡沒有理由不用 TLS 1.3。如果你的 Go 版本 ≥ 1.18，`tls.Config{MinVersion: tls.VersionTLS13}` 加一行搞定。

## 進階：再往深一層

**Byzantine Storage 的最優上界**：Malkhi-Reiter 的後續工作進一步分析了在不同操作語義下的最優 `N` 和 `Q` 組合，以及 Byzantine quorum 和 BFT 共識的等效性條件（什麼時候 storage 等同於 consensus）。Malkhi 後來還主導了 HotStuff 的設計。

**Accountable Byzantine Fault Tolerance**：Civit et al. 2021 年的研究分析了「什麼程度的 BFT 保證可以同時帶來可歸責性」——有些 BFT 協定的設計讓拜占庭節點能在事後混淆責任歸屬（ambiguity），而 accountable BFT 設計要求任何偏離行為都留有清晰的、不可否認的證據。

**Signal Protocol 的 Double Ratchet**：不是分散式系統，但它解決了類似問題——雙方在不可信通道上如何達到「前向保密（forward secrecy）+ 事後逃脫保護（break-in recovery）」。理解 Double Ratchet 的設計讓你對「通道安全的多維度」有更深的感覺。

## 本章重點整理

- mTLS 是許可制分散式系統的節點間安全通道基礎，防竊聽和中間人；但它不防拜占庭節點發虛假訊息。
- 數位簽章提供不可否認性和第三方可查驗性；在 BFT、Byzantine quorum 和 PeerReview 這類需要「可歸責性」的場景裡，必須用簽章而不能只用 MAC。
- Byzantine quorum 系統（Malkhi-Reiter）用 `N=3f+1, Q=2f+1` 讓分散式儲存在 `f` 個拜占庭節點下仍能安全讀寫，但讀端需要等 `f+1` 個相同簽名的一致回覆。
- PeerReview 讓拜占庭偏離行為「不可否認」——誠實節點無法被定罪，拜占庭節點無法隱藏偏離。
- 防重放需要序號或 nonce；版本向量在有對手存在時需要加簽章保護。
- 數位簽章比 MAC 慢 1–3 個數量級，應只用在需要不可否認性的關鍵路徑上。

## 自我檢核

- [ ] 我能解釋 mTLS 和單向 TLS 的差別，以及「mTLS 後訊息就安全了」這個說法哪裡錯
- [ ] 我能說出為什麼 MAC 在拜占庭環境下不能替代數位簽章（用「拜占庭節點共享 K_AB 能偽造什麼」舉例）
- [ ] 我能描述 Byzantine quorum 讀協定（等 f+1 個相同簽名值），以及為什麼這比普通多數決更強
- [ ] 我能解釋 PeerReview 的兩個核心性質（誠實節點不被定罪 + 拜占庭節點不能逃責）的邏輯
- [ ] 不看筆記，我能說出防禦清單的五個層次分別在防什麼

## 延伸閱讀

- **[Byzantine Quorum Systems](https://link.springer.com/article/10.1007/s004460050072)** — Malkhi & Reiter, Distributed Computing 1998
  - **這篇說什麼**：拜占庭 quorum 系統的原始論文，定義了拜占庭 quorum 的性質、最優參數，以及如何在各種操作語義下實現安全讀寫
  - **讀哪裡**：§2（quorum 系統定義）、§3（拜占庭 quorum）、§4（實例）；數學推導可以跳過先看結論
  - **前提**：理解傳統 quorum 系統（Ch 13）

- **[PeerReview: Practical Accountability for Distributed Systems](https://www.sosp2007.org/papers/sosp213-haeberlen.pdf)** — Haeberlen et al., SOSP 2007
  - **這篇說什麼**：可歸責性的完整設計，包括 authenticated log、witness protocol、定罪和脫罪的算法
  - **讀哪裡**：§3（問題形式化）、§4（PeerReview 協定）；§5 的評估可以快速瀏覽
  - **前提**：理解數位簽章和基本分散式系統概念

- **[Practical Byzantine Fault Tolerance and Proactive Recovery](https://dl.acm.org/doi/10.1145/571637.571640)** — Castro & Liskov, TOCS 2002
  - **這篇說什麼**：PBFT 期刊版（比 OSDI 1999 論文更完整），包含簽章的完整協定和 proactive recovery（定期輪換金鑰，限制拜占庭節點的積累）
  - **讀哪裡**：§4（完整簽章版協定）、§6（proactive recovery）
  - **前提**：Ch 33 的 PBFT 基礎

下一章把這章的防禦知識換一個視角——從「有對手主動攻擊時，具體的複製系統和交易系統在哪裡會出問題」來分析，深入 split-brain、stale read、時鐘操縱、2PC coordinator 故障這幾個受控環境裡的攻擊面。

→ [Ch 38 攻擊複製與交易系統](./38-attacking-replication-transactions.md)
