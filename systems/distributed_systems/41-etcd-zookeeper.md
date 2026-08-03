# Ch 41 — etcd / ZooKeeper

> **目標**：搞懂**協調服務（coordination service）**這一類系統存在的理由——當你需要的不是搬大量資料（那是 [Ch 40](./40-kafka-log.md) Kafka 的事），而是讓一群節點對**少量關鍵狀態**（誰是 leader、鎖歸誰、設定是什麼、誰還活著）達成強一致時，用 etcd 或 ZooKeeper。我們會看它們怎麼把 [Ch 20-24](./20-raft-leader-election.md) 的共識包裝成好用的原語（一致 KV、watch、lease/TTL、分散式鎖、leader election、服務發現），以及它們最陰險的陷阱——**分散式鎖不是真的鎖**：鎖過期加上 GC pause 能讓兩個 client 都以為自己持鎖，非得靠 fencing token 才救得回來（接 [Ch 38](./38-attacking-replication-transactions.md)）。最後解釋為什麼「別自己實作共識，用 etcd/ZK」是這門課最實用的一句工程建議。

> **環境**：本章是系統剖析，以 etcd/ZooKeeper 的實際行為與 API 為主，無需自寫 Go。

## 為什麼需要這個？

想像你手上有一組無狀態的 web 服務、一組資料庫副本、一組背景 worker。它們要協調一些事：

- **誰是主？** 一組副本裡必須有唯一一個 primary 負責寫入（[Ch 12](./12-primary-backup-replication.md)）。怎麼選、怎麼在它掛掉後重選、怎麼保證**不會同時有兩個都以為自己是主**（split-brain）？
- **這個任務歸誰做？** 一個定時任務不能被兩個 worker 同時跑。誰能拿到「執行權」這把鎖？
- **設定放哪？** feature flag、資料庫連線字串、路由表這些設定，怎麼讓所有節點看到**一致**的版本，且改了能**即時通知**？
- **誰還活著？** 一個節點加入/離開叢集，其他節點怎麼知道？

這些問題有一個共通點：**都需要一份「所有節點都同意、且強一致」的少量關鍵狀態**。你會想：那我自己拿 Raft（[Ch 20-23](./20-raft-leader-election.md)）寫一個不就好了？

**別。這正是本章要勸退你的事。** 共識演算法「看懂」和「寫對到能上生產」之間隔著一條血河——membership change 的 corner case、log compaction、snapshot、網路分區下的活性、時鐘假設……[Ch 22](./22-raft-safety.md) 那些 safety 規則少一條就 split-brain 丟資料。Google 的 Chubby 論文（ZooKeeper 的思想源頭）講得很直白：他們發現**大量工程師其實只是需要「選個 leader」或「存個一致的小設定」，卻各自去實作 Paxos，錯誤百出**。於是 Google 做了 Chubby、Yahoo 做了 ZooKeeper、CoreOS 做了 etcd——**把共識這件難事做對一次，包成服務，讓所有人共用**。

一句話：協調服務就是「**共識即服務（consensus-as-a-service）**」。你不碰 Raft，你碰的是它幫你實作好的鎖、選舉、KV、watch。

## 先建立直覺

先把協調服務放在你已知的地圖上：

```
   Kafka（Ch 40）            協調服務（etcd/ZK）
   ─────────────            ──────────────────
   搬「大量」資料            存「少量」關鍵狀態
   高吞吐、事件流            低延遲、強一致的小 KV
   TB 級                    通常 < 幾百 MB（全放記憶體）
   「這串事件是什麼」        「現在的真相是什麼」（誰是主、鎖歸誰、設定）
```

它們底層都是一份**用共識複製的、強一致的 KV store**，但暴露給你的不是裸 KV，而是幾個精心設計的原語。核心心智圖像：

```
         你的應用節點們
    ┌──────┬──────┬──────┐
    │ app1 │ app2 │ app3 │   ← 都連到協調服務
    └───┬──┴───┬──┴───┬──┘
        │      │      │  put/get/watch/lock/campaign
        ▼      ▼      ▼
    ┌────────────────────────────────┐
    │  etcd / ZooKeeper 叢集（通常 3 或 5 台）│
    │  ┌────┐  ┌────┐  ┌────┐         │
    │  │ ld │◄─┤ fl │◄─┤ fl │  Raft(etcd)  │
    │  └────┘  └────┘  └────┘  / Zab(ZK)   │
    │  一份強一致 KV，靠共識複製，扛得住少數派失效 │
    └────────────────────────────────┘
```

**節點數為什麼是奇數（3、5）？** 共識要多數派（quorum）。3 台容忍 1 台掛（多數=2），5 台容忍 2 台掛（多數=3）。4 台也只容忍 1 台掛（多數=3），卻多一台的協調成本——所以偶數台不划算，實務一律 3 或 5。這是 [Ch 13](./13-quorum-replication.md) quorum 的直接應用。

## etcd vs ZooKeeper：兩個共識、同一件事

兩者解決同一個問題，出身和底層共識不同：

```
   ZooKeeper（Yahoo, 2008）          etcd（CoreOS, 2013）
   ─────────────────────           ─────────────────
   底層共識：Zab（ZooKeeper         底層共識：Raft
     Atomic Broadcast，Ch 24）        （Ch 20-23）
   資料模型：znode 樹（像檔案系統）  資料模型：扁平的 key（但 key 可含 /）
   API：create/getData/setData/     API：Put/Get/Watch/Lease/Txn（gRPC）
        getChildren/exists/watch
   watch：一次性（觸發後要重註冊）   watch：串流式（持續推送）
   典型用戶：Hadoop、Kafka（舊）、   典型用戶：Kubernetes（存整個叢集
     HBase、Solr                      狀態）、CoreOS 生態
```

> Zab 與 Raft 的細節比較在 [Ch 24](./24-consensus-comparison.md)。粗略說，兩者都是「leader-based 的全序廣播」，Zab 更早、圍繞 primary-backup 設計，Raft 更晚、以「可理解性」為賣點。對使用者來說，**它們提供的保證幾乎一樣**：線性一致的寫入、可選的一致讀。選 etcd 還是 ZK 通常看生態（用 K8s 就是 etcd，用 Hadoop 系就是 ZK），不是看共識演算法優劣。

etcd 最重要的用戶是 **Kubernetes**——K8s 把**整個叢集的狀態**（有哪些 Pod、Node、Service、每個的規格與狀態）全部存在 etcd 裡。你 `kubectl apply` 一個 YAML，最終就是往 etcd 寫一筆強一致的 KV。K8s 的 controller 們則透過 **watch** 監聽 etcd 的變化來驅動整個系統。理解 etcd，就理解了 K8s 的心臟。

## 五個核心原語

協調服務的價值在它提供的原語。逐個看，每個都對應前面某章的思想。

### 1. 一致的 KV（linearizable KV）

最基礎的：`Put(key, value)` / `Get(key)`，且是**線性一致**的（[Ch 9](./09-consistency-models.md)）。寫入走共識，讀取可選一致讀（下面詳談）。加上 **CAS（compare-and-swap）**——「若 key 當前值/版本是 X 才寫入」，這是實作鎖和選舉的基礎。etcd 每次寫入會給一個全域單調遞增的 **revision**，這個 revision 後面會變成 fencing token 的來源。

### 2. Watch（變更通知）

`Watch(key)` 讓你**訂閱**一個 key（或一段前綴）的變化，一有變更就推送給你。這把「輪詢問設定變了沒」變成「變了主動通知我」。K8s 的所有 controller 都靠 watch 驅動——它不輪詢 etcd，它 watch etcd。

```
   config service 改了 feature_flag：
     app1 ─watch(feature_flag)─┐
     app2 ─watch(feature_flag)─┼── etcd 一收到 Put ⇒ 立刻推送給所有 watcher
     app3 ─watch(feature_flag)─┘   ⇒ 全體節點幾乎同時看到新設定
```

### 3. Lease / TTL（帶時效的租約）

**lease** 是「會過期的存在證明」。你建一個 lease（設 TTL，例如 10 秒），把 key 綁在這個 lease 上；你必須定期 **keep-alive**（續租）來續命。**一旦你停止續租（例如你當機了），TTL 到期，lease 連同綁在上面的 key 自動被刪除**。

```
   client 建 lease(TTL=10s)，綁 key "/members/app1"
     每 3 秒 keepalive 一次 ⇒ key 存在，代表「app1 還活著」
     app1 當機 ⇒ 停止 keepalive ⇒ 10 秒後 lease 過期 ⇒ key 自動消失
     ⇒ 其他節點 watch 到「/members/app1 被刪」⇒ 知道 app1 掛了
```

lease 是**失敗偵測（failure detection）**的基石——它把「你還活著嗎」變成「你還在續租嗎」。這跟 [Ch 29](./29-membership-failure-detection-swim.md) 的心跳偵測是同一個思想，只是由協調服務托管。ZooKeeper 的對應物是 **ephemeral node（臨時節點）**：綁在 session 上的節點，session 斷了節點自動消失。

### 4. Leader election（領導選舉）

有了 CAS + lease + watch，選 leader 就是幾行邏輯：

```
   選舉（etcd 的 election API 幫你封裝好了，原理是）：
     每個候選人建一個帶 lease 的 key，寫進一個共同前綴 "/election/"
     etcd 按 revision（建立順序）排序這些 key
     revision 最小的那個 = leader
     其他候選人 watch「排在自己前面那個 key」
       ⇒ 前面的 leader 掛了（lease 過期、key 消失）⇒ 下一個遞補
```

關鍵：**leader 的身份綁在 lease 上**。leader 一旦停止續租（當機、卡住、被分區），lease 過期，領導權自動轉移。你不用自己寫「怎麼偵測 leader 掛了、怎麼重選」——協調服務用它底層的共識保證了**同一時刻至多一個 leader**（就 etcd 自己的視角而言）。這是本章最有價值的原語之一：**把 [Ch 20](./20-raft-leader-election.md) 那整章的選舉，變成一個 API 呼叫。**

### 5. 服務發現與配置管理

前面四個湊起來就是這兩個常見應用：

- **服務發現**：每個服務實例啟動時，用帶 lease 的 key 把自己的位址註冊到 `/services/api/`；掛了 lease 過期自動下線。想找 api 服務的 client `Get` 這個前綴拿到所有活著的實例，並 `Watch` 它感知上下線。
- **配置管理**：設定存成 KV，改設定就 `Put`，所有節點 `Watch` 到即時更新。強一致保證所有節點看到同一版本，不會半新半舊。

## 底層機制：分散式鎖為什麼「不是真的鎖」

這是全章最重要、也最反直覺的一段。協調服務提供**分散式鎖**——`Lock(key)` 拿鎖、`Unlock` 放鎖，同一時刻只有一個 client 能持有。看起來就像你熟悉的 `mutex`。**但它有一個單機 mutex 絕不會有的致命問題，會讓兩個 client 同時以為自己持鎖。**

問題出在**鎖必須帶 TTL**。為什麼？因為持鎖的 client 可能當機——如果鎖沒有過期機制，它一當機鎖就永遠不放，整個系統卡死。所以分散式鎖一定綁 lease/TTL：**持鎖者必須定期續租，停止續租鎖就自動釋放**。這個「自動釋放」正是災難的種子：

```
   災難時序（經典的 lock + GC pause 問題）：

   client A 拿到鎖（lease TTL=10s）
      │  A 開始處理，準備寫入共享儲存
      │
   ★ A 發生一次長 STW GC pause（或被 OS 換出、或網路卡頓）15 秒 ★
      │  A 這 15 秒完全凍結，沒能續租
      │
   ── TTL 到期（10s）⇒ etcd 認為 A 掛了 ⇒ 自動釋放 A 的鎖 ──
      │
   client B 拿到鎖（因為鎖被釋放了）
      │  B 開始處理，寫入共享儲存 write(x, B)
      │
   ★ A 的 GC pause 結束，A「醒來」★
      │  A 完全不知道自己的鎖早過期了！
      │  A 繼續它中斷前的邏輯：write(x, A)  ← ★ 災難 ★
      │
   結果：A 和 B「同時」都以為自己持鎖，都寫了共享儲存
         鎖形同虛設，資料損壞
```

**根本原因**：分散式鎖的「持有」是一個**帶時效的租約**，而持鎖者無法保證自己在租約內一定活著、一定續得上租。GC pause、頁面換出、CPU 被搶、網路分區——任何讓 client 凍結超過 TTL 的事，都會讓「鎖已被別人拿走」和「我以為我還持鎖」這兩件事同時成立。**這不是 etcd/ZK 的 bug，是分散式鎖的本質局限**——你沒法在一個節點可能隨時凍結任意久的世界裡，保證「鎖的持有」和「持鎖者的行為」永遠同步。

> 這正是 [Ch 38](./38-attacking-replication-transactions.md) 分析的攻擊面之一：攻擊者甚至可以**主動**製造這個時序（拖慢受害者時鐘或注入 pause），讓兩個 client 同時持鎖，破壞交易完整性。

**唯一正確的解法：fencing token（防護令牌）**。核心洞察是——**別信 client 說「我持鎖」，讓底層儲存來裁決**。每次取得鎖時，協調服務給一個**單調遞增的 token**（etcd 用 revision，ZooKeeper 用 zxid/version）。client 每次操作共享儲存都帶上這個 token，**儲存端記住「見過的最大 token」，拒絕任何 token 較小的操作**：

```
   fencing token 救場：

   A 拿鎖 ⇒ token=33，A 凍結
   ── A 的鎖過期 ──
   B 拿鎖 ⇒ token=34（單調遞增，一定比 A 的大）
   B 帶 token=34 寫入 ⇒ 儲存記錄「見過的最大 token = 34」，接受

   A 醒來，帶著過期的 token=33 寫入 ⇒
      儲存一看：33 < 34（我見過更大的了）⇒ ★ 拒絕 ★
   ⇒ A 的遲到寫入被擋掉，資料安全
```

**關鍵要求（也是最常被忽略的）**：fencing token 要有效，**底層儲存本身必須支援 token 比較**——它得能原子地「驗證 token ≥ 已見最大值，才執行操作並更新最大值」。如果你的儲存（某個 DB、某個檔案系統、某個 API）不支援這個檢查，fencing token 就是廢紙，分散式鎖就仍然不安全。這是 Martin Kleppmann 那篇著名的 "How to do distributed locking" 反覆強調的重點：**很多人用了 Redlock/ZooKeeper 鎖卻沒做 fencing，以為安全，其實隨時會被 GC pause 打穿。**

## 底層機制：線性一致讀怎麼做（etcd 的 ReadIndex）

寫入走共識天生線性一致，但**讀**呢？如果 leader 直接回本地狀態，它可能已經被取代而不自知（[Ch 26](./26-raft-kv-linearizable-reads.md) 講過這個 stale read 陷阱）。etcd 預設用 **ReadIndex** 保證線性一致讀：

```
   etcd 線性一致讀（ReadIndex，接 Ch 26）：
     leader 收到讀請求
       ① 記下當前 commitIndex
       ② 發一輪心跳，確認自己仍是多數派認可的 leader
          （被分區進少數派的假 leader 湊不到多數 ⇒ 讀被卡住，不回舊值）
       ③ 等本地狀態機 apply 到那個 commitIndex
       ④ 用該狀態服務讀 ⇒ 線性一致
```

etcd 也提供 **serializable read**（可選）：直接讀本地、不做確認、更快但可能讀到舊值——當你能容忍些微陳舊時用。**預設是線性一致（安全優先），要快才手動降級**。這個「安全預設 + 可選降級」的設計哲學值得學。ZooKeeper 的讀預設是**非線性一致**的（讀走 follower 本地，可能看到舊值），要線性一致得先呼叫 `sync()`——這是兩者一個容易踩的差異。

## 對比與取捨

| 面向 | etcd | ZooKeeper |
|---|---|---|
| 底層共識 | Raft | Zab |
| 資料模型 | 扁平 key（gRPC） | znode 樹（類檔案系統） |
| 讀預設 | 線性一致（ReadIndex） | 非線性一致（要 `sync()` 才線性） |
| Watch | 串流、持續 | 一次性、要重註冊 |
| 「活著」原語 | lease + keep-alive | ephemeral node + session |
| 旗艦用戶 | Kubernetes | Hadoop 生態、（舊）Kafka |
| MVCC / 版本 | revision（全域單調） | zxid / version |

| 「自己實作共識」 vs 「用協調服務」 | 自己寫 Raft | 用 etcd/ZK |
|---|---|---|
| 正確性風險 | 極高（safety corner case 血河） | 低（已被大規模驗證多年） |
| 開發成本 | 數月起跳、且永遠在修 bug | 幾行呼叫原語 |
| 運維 | 全自己扛 | 有成熟工具、社群、監控 |
| 何時才該自己寫 | 幾乎不該。除非共識本身就是你的產品（你在做資料庫/區塊鏈） | 絕大多數應用 |

## 踩雷集錦

1. **「分散式鎖跟 mutex 一樣安全」→ 錯，沒有 fencing token 的鎖隨時會被 GC pause 打穿**。這是本章的頭號雷。鎖必帶 TTL，持鎖者凍結超過 TTL 就會有兩個 client 同時「持鎖」。**只要有共享儲存被鎖保護，就必須用 fencing token，且底層儲存要能驗證 token**。沒做這件事的分散式鎖是一顆定時炸彈——它平時看起來好好的，只在 GC pause/分區時偶發性地損壞資料。

2. **「lease TTL 設短一點就安全了」→ 反而更危險**。TTL 短，持鎖者更容易「稍微卡一下就過期」，兩個 client 同時持鎖的窗口反而更頻繁被觸發。TTL 長則 client 真掛時鎖釋放慢。**調 TTL 治不了根本問題**——根本問題是「持有」和「行為」會不同步，只有 fencing token 能治。別把 fencing 的活推給調參數。

3. **「ZooKeeper 的讀是線性一致的」→ 錯，預設不是**。ZK 為了讀效能，讀走 follower 本地狀態，可能回**舊值**（follower 還沒 apply 到最新）。要線性一致讀，得先 `sync()` 再讀。etcd 預設才是線性一致（ReadIndex）。搞混這點會在 ZK 上寫出「剛寫完馬上讀卻讀到舊值」的困惑 bug。

4. **「把 etcd/ZK 當一般資料庫存大量資料」→ 會炸**。協調服務為「少量關鍵狀態」設計——通常整個資料集要能放進記憶體，etcd 預設有幾 GB 的空間上限。它的每次寫入都走共識、有 fsync，寫入吞吐遠低於一般 DB。往裡塞大量業務資料或高頻寫入，會拖垮共識、拖慢所有依賴它的協調功能（連帶讓你的 K8s 變慢）。**它存的是「真相的指標」，不是「真相的全部資料」。**

5. **「watch 保證我不漏任何一次變更」→ 有前提**。etcd watch 從一個 revision 開始，只要你連續 watch 就不漏；但如果你斷線太久，超過 etcd 的歷史保留（compaction 邊界），你要 watch 的起始 revision 可能已被壓縮掉，會收到 `ErrCompacted`，這時你得**重新 Get 當前全量狀態 + 從新 revision 續 watch**。以為「watch 一掛上去就永遠不漏」而不處理 compaction，斷線重連後就會漏事件、狀態不一致。

## 進階：再往深一層

- **Chubby：這一切的祖師爺**。Google 的 Chubby（OSDI 2006）是協調服務的原型，ZooKeeper、etcd 都受它啟發。它的論文有一段極誠實的觀察：Chubby 設計時以為大家會拿它做細粒度的鎖，結果**絕大多數用途其實是「選 leader」和「存一點點一致的 metadata」**——這直接塑造了 ZooKeeper/etcd 的原語設計。論文也記錄了「工程師濫用 Chubby 當資料庫」導致的種種災難，正是本章踩雷 #4 的出處。想理解這類系統的設計哲學，Chubby 論文是源頭。

- **etcd 的 MVCC 與 revision 為何重要**。etcd 保留每個 key 的**多版本歷史**（MVCC），每次寫入產生一個全域單調遞增的 revision。這不只是實作細節：revision 讓 etcd 能做「從某個歷史點 watch」（K8s controller 重啟後從上次的 revision 續看，不漏事件）、能做**快照一致的範圍讀**（讀某個 revision 下的一致視圖）、且天然就是完美的 **fencing token**（單調、全域唯一）。理解 revision，就理解了 etcd 很多能力的來源。

- **共識服務自己也會有可用性問題**。把共識外包給 etcd/ZK 解決了「正確性」，但沒解決「它掛了怎麼辦」。如果整個 etcd 叢集失去多數派（分區、多台同時掛），你的**所有**依賴它的協調（選舉、鎖、服務發現）會全部卡住——這是一個**中心化的失敗點**。K8s 就是這樣：etcd 掛了，控制平面就癱了（雖然已跑起來的 Pod 靠 kubelet 還能撐一陣）。所以協調服務叢集本身要用最高規格運維：奇數台、跨故障域部署、專用硬碟（共識對 fsync 延遲極敏感）、獨立監控。**把難題集中到一個地方做對，代價是這個地方變成全系統最關鍵的單點**。

## 本章重點整理

- 協調服務 = **共識即服務**。當你需要「少量關鍵狀態的強一致」（誰是主、鎖歸誰、設定、誰活著），用 etcd/ZK，別自己寫 Raft/Paxos——把難事做對一次、共用。
- 五個核心原語：**一致 KV（+CAS）、watch（變更通知）、lease/TTL（帶時效的存在證明）、leader election、服務發現/配置**。每個都對應前面某章的思想，被協調服務封裝成 API 呼叫。
- etcd（Raft，K8s 的心臟，讀預設線性一致）與 ZooKeeper（Zab，Hadoop 生態，讀預設非線性）解決同一問題；選型多半看生態不看共識優劣（[Ch 24](./24-consensus-comparison.md)）。
- **分散式鎖不是真的鎖**：鎖必帶 TTL，持鎖者 GC pause / 分區凍結超過 TTL，就會有兩個 client 同時以為持鎖 ⇒ 資料損壞。這是本質局限，不是 bug。
- **唯一正解是 fencing token**：取鎖給單調遞增 token（etcd revision / ZK zxid），操作共享儲存必帶 token，**儲存端拒絕較小的 token**。前提是底層儲存能驗證 token，否則 fencing 形同虛設（[Ch 38](./38-attacking-replication-transactions.md)）。
- etcd 用 **ReadIndex** 做線性一致讀（接 [Ch 26](./26-raft-kv-linearizable-reads.md)）；ZooKeeper 讀預設非線性，要 `sync()`。安全預設 + 可選降級是好的設計哲學。

## 自我檢核

- [ ] 不看筆記，我能說出協調服務跟 Kafka 各自解決什麼問題（少量關鍵狀態 vs 大量資料流）
- [ ] 我能列出至少四個協調服務原語，並說出每個對應前面哪一章的思想
- [ ] 我能完整講出「lock + GC pause 導致兩個 client 同時持鎖」的時序，以及為什麼調 TTL 治不了它
- [ ] 我能解釋 fencing token 怎麼救場，以及它有效的**前提**（底層儲存要能驗證 token）
- [ ] 我能說明為什麼「不要自己實作共識，用 etcd/ZK」是正確的工程建議，以及極少數該自己寫的例外

## 延伸閱讀

### 原始論文

- **[The Chubby lock service for loosely-coupled distributed systems](https://research.google/pubs/pub27897/)** — Mike Burrows（Google, OSDI 2006）
  - **讀哪裡**：Section 2（設計）+ Section 4（實際用法與濫用觀察）
  - **學什麼**：協調服務的設計哲學源頭，以及「工程師只是要選 leader / 存小 metadata」這個塑造了整類系統的洞察，本章多處論點的出處
  - **前提**：[Ch 18-19](./18-paxos-single-decree.md) Paxos（Chubby 底層是 Paxos）

- **[ZooKeeper: Wait-free coordination for Internet-scale systems](https://www.usenix.org/legacy/event/atc10/tech/full_papers/Hunt.pdf)** — Hunt et al.（Yahoo, USENIX ATC 2010）
  - **讀哪裡**：znode 資料模型、watch、ephemeral node 三節，以及它怎麼用這些原語組出鎖與選舉
  - **學什麼**：協調原語怎麼從一個共識核心長出來，本章五原語的權威來源之一

### 必讀文章

- **[How to do distributed locking](https://martin.kleppmann.com/2016/02/08/how-to-do-distributed-locking.html)** — Martin Kleppmann（2016）
  - **讀哪裡**：整篇，尤其「Making the lock safe with fencing」一節（那張 GC pause 時序圖是本章底層機制段的原型）
  - **學什麼**：分散式鎖的本質不安全、fencing token 為何是唯一正解、以及它對底層儲存的要求——本章鎖段落的核心參考
  - **前提**：讀懂本章的 lease/TTL 原語

- **[etcd Documentation — Learning: Data model & Linearizability](https://etcd.io/docs/latest/learning/)** — etcd 官方
  - **讀哪裡**：「Data model」（MVCC/revision）與「API guarantees」（linearizable vs serializable read）
  - **學什麼**：revision 為何同時是版本、watch 起點、fencing token 來源；線性一致讀的保證與可選降級
  - **前提**：[Ch 26](./26-raft-kv-linearizable-reads.md) 的 ReadIndex

協調服務讓你把「一致的少量狀態」外包出去。下一章回到應用層設計哲學——當我們把「狀態」重新定義成「事件序列的 fold」（正是 [Ch 25](./25-replicated-state-machine.md) RSM 思想的應用層版），會得到一種以日誌為唯一真相的架構：事件溯源與 CQRS。

→ [Ch 42 事件溯源 / CQRS](./42-event-sourcing-cqrs.md)
