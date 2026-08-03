# Ch 39 — Google Spanner

> **目標**：把前面 38 章的原理拼成一個真實系統的全景。Spanner 是「用硬體解決時鐘問題」的里程碑——它把 [Ch 4](./04-physical-clocks.md) 的 TrueTime、[Ch 18-19](./18-paxos-single-decree.md) 的 Paxos、[Ch 30](./30-distributed-transactions-2pc-3pc.md) 的兩階段提交（2PC）縫成一個全球分佈、強一致、還支援 SQL 交易的資料庫。搞懂它最核心的一招：**commit-wait**——故意等過時鐘的不確定區間，換來「時間戳的順序就是真實發生的順序」這個看似不可能的保證（外部一致性 / external consistency）。讀完你能看懂 Spanner 論文，並知道它每筆交易多付了什麼代價。

> **環境**：本章是真實系統剖析，無 Go code。所有數字標明來源（Spanner OSDI 2012 論文為主）。

## 為什麼需要這個？

在 Spanner 之前，業界有一條被當成鐵律的取捨：**要全球規模就得放棄強一致，要強一致就別想全球規模**。這條律來自 [Ch 10](./10-cap-theorem.md) 的 CAP 和 [Ch 11](./11-pacelc.md) 的 PACELC——跨資料中心的複製，光是光速就讓你每筆一致寫入付出幾十到上百毫秒延遲，於是大家紛紛退到最終一致（eventual consistency）：Dynamo、Cassandra、早期的 BigTable 都是。開發者被迫在應用層自己處理「讀到舊值」「兩個寫入衝突」這些髒事。

Google 內部被這件事咬得很痛。他們的廣告系統 F1 原本跑在手動分片的 MySQL 上，每次重新分片是一場「持續兩年多、動員整個團隊」的災難（F1 論文語）。他們要的是一個**能跨洲複製、能扛住整個資料中心失效、又能像單機資料庫一樣寫 SQL 交易**的東西。最終一致做不到這件事——你沒辦法在最終一致的系統上安全地跑「從 A 帳戶轉錢到 B 帳戶」。

Spanner 的賭注是：**CAP 說的「不可能」是針對「一般時鐘」的**。如果你能讓時鐘的不確定性**有界且被明確回報**，就能重新拿回「用時間戳排序全球事件」這個能力——而這正是強一致交易缺的最後一塊拼圖。Google 願意為此在每個資料中心裝 GPS 接收器和原子鐘。這是本章的靈魂：**用一筆硬體投資，買回一個被認為不可能的一致性保證。**

> 若對「為什麼實體時間戳不能拿來排序分散式事件」還沒有肌肉記憶，強烈建議先回看 [Ch 4](./04-physical-clocks.md)。本章是那章 TrueTime 段落的正式展開。

## 先建立直覺

先講清楚 Spanner 到底承諾了什麼。它叫這個保證 **外部一致性（external consistency）**，其實就是分散式版的線性一致性（linearizability，[Ch 9](./09-consistency-models.md)）套用到交易上：

> 如果交易 T1 在真實世界裡「先完成」（T1 commit 回覆客戶端之後，T2 才開始），那麼 Spanner 分給 T1 的時間戳一定小於 T2 的時間戳。

聽起來像廢話，但它極難。難點在「真實世界裡先完成」這句——它牽涉的是**真實的、絕對的時間先後**，而分散式系統偏偏沒有一個可信的全域時鐘（[Ch 4](./04-physical-clocks.md) 花了一整章講這件事）。兩個交易可能發生在地球兩端的資料中心，各看各的時鐘，怎麼保證分出來的時間戳順序跟真實先後一致？

Spanner 的整個設計就是繞著這一個問題轉。把它想成一個三層蛋糕：

```
   ┌──────────────────────────────────────────────────────┐
   │  外部一致的 SQL 交易（客戶端看到的）                   │
   │  「T1 先於 T2 完成 ⇒ ts(T1) < ts(T2)」                 │
   └──────────────────────────────────────────────────────┘
                          ▲ 靠這個實現
   ┌──────────────────────────────────────────────────────┐
   │  TrueTime + commit-wait                                │
   │  時鐘回傳有界區間 [earliest, latest]，                  │
   │  交易「等過」不確定區間才釋放鎖                          │
   └──────────────────────────────────────────────────────┘
                          ▲ 跑在這個之上
   ┌──────────────────────────────────────────────────────┐
   │  Paxos group（複製）+ 2PC（跨 group 交易）             │
   │  每份資料一個 Paxos group 撐容錯，跨 group 靠 2PC 原子  │
   └──────────────────────────────────────────────────────┘
```

底下兩層你其實都學過了：Paxos 是 [Ch 18-19](./18-paxos-single-decree.md)，2PC 是 [Ch 30](./30-distributed-transactions-2pc-3pc.md)。Spanner 真正的原創在中間那層——**用 TrueTime 給交易分時間戳，用 commit-wait 保證時間戳順序不說謊**。我們就從架構的底層往上講。

## 架構：資料怎麼被切、被複製

Spanner 的資料組織，由下往上是這幾層：

```
   universe（一整個 Spanner 部署，Google 內部就一個 production universe）
     └── zone（≈ 一個資料中心的一份部署，故障隔離單位）
           └── spanserver（一台伺服器，管上百到上千個 tablet）
                 └── tablet（一段 key range 的資料，狀態存在 Colossus/GFS 上）
                       └── 每個 tablet 由一個 Paxos group 複製到多個 zone
```

關鍵是 **tablet 與 Paxos group 的對應**。一段連續的 key range（一個 tablet）不是只存一份，而是透過一個 **Paxos group**（[Ch 19](./19-multi-paxos.md) 的 Multi-Paxos）複製到分佈在不同 zone、甚至不同洲的多個副本上。每個 Paxos group 有一個 leader，寫入走 leader → Paxos 複製到多數派 → commit。這就是 Spanner 的容錯地基：一整個資料中心掛掉，只要多數派副本還活著，資料就還在、還能服務。

```
   一個 tablet（key range [k1, k2)）的複製：

        zone US-east          zone US-central        zone EU-west
     ┌──────────────┐      ┌──────────────┐      ┌──────────────┐
     │ Paxos leader │◄────►│ Paxos replica│◄────►│ Paxos replica│
     │   （寫入口）  │      │              │      │              │
     └──────────────┘      └──────────────┘      └──────────────┘
            │  一筆寫入：leader propose → 多數派 ack → commit
            ▼
     多數派 = 這個 Paxos group 的多數，跨 zone，扛得住整個 zone 失效
```

**跨 group 的交易怎麼辦？** 一筆交易若只碰一個 Paxos group 的 key，那走單一 Paxos 就搞定了（single-Paxos-group transaction）。但如果一筆交易要原子地改動落在**不同 tablet、不同 Paxos group** 的資料——例如「A 帳戶（在 group 1）扣款、B 帳戶（在 group 2）加款」——就需要 **2PC**（[Ch 30](./30-distributed-transactions-2pc-3pc.md)）：

```
   跨 group 交易 = 2PC，其中每個「參與者」本身是一個 Paxos group

     coordinator（其中一個 Paxos group 的 leader 兼任）
          │  prepare
          ├──────────────► participant group 1 leader
          │                    （把 prepare 記錄透過 Paxos 複製到自己 group）
          ├──────────────► participant group 2 leader
          │                    （同上）
          │  收齊所有 prepare-ok 後
          │  選定 commit timestamp s（下面詳談）
          │  commit
          └──────────────► 所有 participant
```

這裡有個漂亮的設計：2PC 最為人詬病的弱點是**coordinator 單點故障會讓交易卡死**（[Ch 30](./30-distributed-transactions-2pc-3pc.md) 講過 blocking 問題）。Spanner 把 coordinator 的狀態也透過 Paxos 複製了——coordinator 不是一台機器，是一個 Paxos group。coordinator leader 掛了，同 group 的新 leader 接手，交易繼續。**Spanner 用 Paxos 把 2PC 的單點故障補掉了。** 這是「把前面學的積木疊起來解決各自弱點」的教科書範例。

`director`/placement driver 負責決定 tablet 放哪些 zone、什麼時候搬動（負載均衡、跟隨使用者地理位置），這層對一致性沒有直接影響，知道它存在即可。

## TrueTime：把「幾點」變成「幾點到幾點之間」

現在進到核心。一般時鐘的 API 是 `now() → 一個時間點`，它假裝精確，其實在說謊（[Ch 4](./04-physical-clocks.md)）。TrueTime 的 API 誠實得多：

```
   TT.now()  → TTinterval{ earliest, latest }
              保證：真正的絕對時間 t_abs 一定滿足 earliest ≤ t_abs ≤ latest

   另外兩個衍生方法：
   TT.after(t)  → true 若 t 一定已經過去了（即 t < TT.now().earliest）
   TT.before(t) → true 若 t 一定還沒到（即 t > TT.now().latest）
```

區間的半寬度叫 **ε（epsilon）**，`latest - earliest = 2ε`。TrueTime 的全部價值在於：**ε 是有界的，而且它的值被明確回報給你**。你拿到的不是一個假裝精確的點，而是一個誠實的區間，加上「真值一定在裡面」的保證。

**ε 靠什麼壓小？** 每個資料中心部署一組 **time master** 伺服器，每台配 **GPS 接收器**或**原子鐘（Armageddon master）**兩種其中之一——兩種刻意混用，因為它們的失效模式不同（GPS 怕天線故障、電離層干擾、欺騙；原子鐘怕頻率漂移）。GPS 給的是「跟 UTC 對齊的絕對時間」，原子鐘給的是「短期極穩的頻率」。每台機器上的 **timeslave daemon** 定期去問多台 time master，用類似 Marzullo 演算法的方式排除說謊的來源，並校正自己的本地時鐘。

ε 的組成，照論文的描述：

```
   兩次向 time master 校正之間，本地石英時鐘會漂移（drift）。
   Spanner 保守假設最壞漂移率 = 200 μs/s（微秒每秒）。

   ε 隨時間鋸齒狀變化：
   剛校正完 ── ε 最小（約 1 ms，主要是網路往返不確定 + master 本身誤差）
        │         ／
        │       ／   ← 石英以最壞 200μs/s 漂移，ε 線性上升
        │     ／
   校正前 ── ε 最大（校正週期 30 秒 × 200μs/s ≈ 6 ms 的漂移貢獻 + 底噪）
        └──────────────► 時間
   下次校正把 ε 打回最小，如此鋸齒循環
```

論文報告的 ε：**平均約 4 ms，鋸齒的低點約 1 ms、高點約 6~7 ms**（Spanner OSDI 2012, Section 3 及 Figure 6）。校正週期是 30 秒，最壞漂移假設 200μs/s。這幾個數字是論文明確給的，記住量級即可：**ε 是個位數毫秒**。

> **這些數字的前提**：這是 Google 2012 年論文報的生產環境數據，且是「未偵測到 time master 或網路故障」的正常情況。故障時（例如某台機器 GPS 天線壞了、跟 master 斷線）ε 會飆高，Spanner 的策略是寧可讓 ε 變大（交易變慢）也不說謊——ε 大到一定程度伺服器會自己下線，絕不回傳一個「其實真值不在裡面」的假區間。**保守是 TrueTime 的核心設計原則。**

## 底層機制：commit-wait 怎麼實現外部一致性

這是全章最該記住的一段。問題：怎麼保證「T1 真實上先於 T2 完成 ⇒ ts(T1) < ts(T2)」？

Spanner 給每個讀寫交易分配一個 **commit timestamp** `s`。分配規則加上一個等待，合起來就實現了外部一致性。看一筆讀寫交易在 coordinator 上的提交流程：

```
   一筆讀寫交易 T 的提交（simplified，單一或跨 group 都適用）：

   ① 交易執行期間，對讀到/要寫的資料上鎖（two-phase locking，2PL）
   ② 準備提交，coordinator 選 commit timestamp：
         s = TT.now().latest        ← 取「不確定區間的上界」當時間戳
      （跨 group 2PC 時 s 取所有 participant 提議的最大值，且 ≥ 各 Paxos 已用時間戳）
   ③ ★ commit-wait ★：
         等待，直到 TT.after(s) 為真
         也就是等到 TT.now().earliest > s
         意義：等到「絕對時間確定已經越過 s 這一刻」才繼續
      這一等，平均等 ≈ 2ε（因為 s 取的是 latest，要等 earliest 也越過它）
   ④ commit-wait 結束後，才：透過 Paxos 把 commit 記錄寫下、釋放鎖、回覆客戶端
```

第 ③ 步是魔法所在。**為什麼「等過不確定區間」就能保證時間戳順序 = 真實順序？** 用反證法感受一下：

```
   假設 T1 真實上先於 T2 完成：
     T1 在真實時間 t1_real 回覆了客戶端（此時 T1 早已 commit-wait 完畢）
     T2 在真實時間 t2_real 才開始，且 t2_real > t1_real

   要證 ts(T1) < ts(T2)：

   ① T1 做完 commit-wait ⇒ TT.after(ts(T1)) 在 T1 回覆前就為真
        ⇒ 真實時間已越過 ts(T1)
        ⇒ ts(T1) < t1_real                          ...(a)

   ② T2 選時間戳時 ts(T2) = TT.now().latest ≥ 真實當下時間
        而 T2 開始於 t2_real ⇒ ts(T2) ≥ t2_real     ...(b)

   ③ 由前提 t2_real > t1_real，串起 (a)(b)：
        ts(T2) ≥ t2_real > t1_real > ts(T1)
        ⇒ ts(T1) < ts(T2)  ✓ 得證
```

一句話總結這招：**T1 靠 commit-wait 保證「我的時間戳已成過去式」，T2 靠取 latest 保證「我的時間戳不會早於現在」。前者的過去式撞不上後者的現在，順序就不會反。** 這就是為什麼 ε 越小越好——commit-wait 要等的正是這個區間，ε 小則等得短、吞吐高。

TrueTime 也讓 Spanner 能做一件狠事：**外部一致的無鎖快照讀（snapshot read）**。讀取交易可以指定一個時間戳 `t_read`，Spanner 保證讀到「在 `t_read` 這一刻的一致快照」，且完全不上鎖、不擋寫入（因為 Spanner 保留多版本，MVCC）。跨全球的分析查詢可以在一個一致的時間切面上跑，不干擾線上寫入——這在最終一致系統裡是做不到的。

## commit-wait 的代價：每筆交易多等 2ε

天下沒有白吃的午餐。commit-wait 直接的代價是**每筆讀寫交易的提交延遲多了約 2ε**：

```
   一筆讀寫交易的延遲組成（粗略）：
     2PL 上鎖 + 執行  +  Paxos 複製一輪（跨 zone RTT，數 ms 到數十 ms）
       +  commit-wait（平均 ≈ 2ε ≈ 8 ms 量級，用 ε≈4ms 估）
       +  回覆客戶端
```

用論文的 ε≈4ms 估，commit-wait 平均等約 **8ms 上下**（2ε）。這是**設計文件層級的數字，來自論文報告的 ε 均值**，實際隨鋸齒波動、隨 time master 健康度變化。

值得注意的是這個代價的性質：commit-wait 的等待常常能跟 Paxos 複製的等待**重疊**——你本來就要等一輪跨 zone 的 Paxos 複製（那也是數毫秒），commit-wait 可以塞在同一段等待裡，實際淨增延遲往往比純 2ε 小。論文也指出：**只讀交易（read-only）完全不需要 commit-wait**，它們用快照讀，走 TrueTime 選一個安全的讀時間戳即可，不上鎖不等待。所以 commit-wait 的成本只落在**讀寫交易**上，而真實負載讀遠多於寫。

一句話評估：**Spanner 用「每筆寫交易多等個位數毫秒」換來「全球外部一致 + SQL 交易」**。對 Google 的廣告、Play、Cloud Spanner 這些場景，這筆交易划算到不行——它讓開發者能像用單機 MySQL 一樣寫程式，卻跑在跨洲、扛得住資料中心失效的系統上。

## 對比與取捨

| 系統 | 一致性 | 全球規模 | 時鐘依賴 | 寫入額外延遲 | 代價來源 |
|---|---|---|---|---|---|
| Dynamo / Cassandra | 最終一致 | 是 | 無（或 LWW，會丟資料） | 無 | 一致性丟給應用層扛 |
| 傳統單機 RDBMS | 強一致 | 否 | 無（單時鐘） | 無 | 沒有容錯/規模 |
| Raft/Paxos KV（[Ch 26](./26-raft-kv-linearizable-reads.md)） | 線性一致 | 單 region 為主 | 無 | 一輪共識 | 跨 region 延遲高 |
| **Spanner** | **外部一致 + SQL 交易** | **是** | **重（要 GPS+原子鐘）** | **≈ 2ε（個位數 ms）** | **硬體錢 + commit-wait** |
| CockroachDB | 序列化（serializable） | 是 | 中（HLC，無 TrueTime） | 無固定 wait，但可能重試/uncertainty restart | 用 HLC + uncertainty interval 近似，偶爾要重試交易 |

最後一列值得多看一眼：CockroachDB 是「開源界想要 Spanner 但沒有 Google 的 GPS/原子鐘」的答案。它用 **HLC（Hybrid Logical Clock，[Ch 4](./04-physical-clocks.md) 進階段提過）** 加一個「不確定區間」來近似——它不做 commit-wait，而是在讀到落在不確定區間內的寫入時**重啟交易**（uncertainty restart）。這是「沒有專用硬體時，怎麼逼近 TrueTime 效果」的工程折衷，代價從「固定等 2ε」變成「偶爾要重試」。

## 踩雷集錦

1. **「commit-wait 是等網路或等複製」→ 錯**。commit-wait 跟網路、跟 Paxos 複製都無關，它是**純粹的本地等待**——等本地 TrueTime 的 `earliest` 越過已選定的時間戳 `s`。就算你的網路零延遲、複製瞬間完成，commit-wait 該等的 2ε 一秒不少。它換的不是資料到位，是**時間戳的可信排序**。

2. **「TrueTime 消滅了時鐘不確定性」→ 錯**。TrueTime 不消滅不確定，它**量化並回報**不確定（回傳區間而非點）。時鐘還是不準，只是 Spanner 誠實面對這個不準、把它壓進有界的 ε、然後用 commit-wait 等過它。「把謊言關進有界的籠子」不是「消滅謊言」。

3. **「ε 是固定值」→ 錯**。ε 是**鋸齒狀波動**的：剛跟 time master 校正完最小（約 1ms），越接近下次校正越大（因石英漂移累積，可到 6~7ms）；time master 或網路出問題時會飆更高。你的交易延遲因此會隨 ε 抖動。設計依賴 Spanner 延遲的系統要考慮這個抖動，不能當它是常數。

4. **「Spanner 靠 TrueTime 就不需要共識了」→ 錯**。TrueTime 只解決「時間戳排序」問題，它**不提供複製、不提供容錯**。Spanner 的複製與容錯完全靠 Paxos（[Ch 19](./19-multi-paxos.md)），跨 group 原子性靠 2PC（[Ch 30](./30-distributed-transactions-2pc-3pc.md)）。TrueTime 是疊在共識之上的一層，不是替代品。搞混這點會以為 Spanner 是「用時鐘取代共識」，完全反了。

5. **「既然這麼強，我也上 TrueTime 就好」→ 你多半沒有那個硬體**。TrueTime 需要每個資料中心部署 GPS 接收器和原子鐘，並維護 time master 基礎設施——這是 Google 規模才攤得平的投資。一般團隊要類似效果，走 CockroachDB 的 HLC 近似路線，或直接用 Cloud Spanner（Google 幫你出硬體錢）。**別以為 TrueTime 是個軟體函式庫可以 import。**

## 進階：再往深一層

- **為什麼是 GPS「加」原子鐘，不是只用一種？** 因為兩者的失效模式互補且**不相關**。GPS 全域對齊 UTC，但天線故障、電離層擾動、甚至 GPS 欺騙（spoofing）會讓它系統性偏掉；原子鐘短期極穩但會慢慢漂、無法自己對齊 UTC。混用讓 Spanner 能在 GPS 出包時靠原子鐘頂住短期、在原子鐘漂移時靠 GPS 拉回。這是「用不相關的獨立來源對抗共模故障」的經典安全思維——跟你在容錯設計裡追求「故障獨立性」是同一個道理。

- **Spanner 與 CAP 的關係**：Spanner 常被誤傳為「打破了 CAP」。它沒有。它在**網路分區時仍然選擇一致性（CP）**——分區時少數派側無法湊齊 Paxos 多數，寫入會被拒（不可用），一致性不讓步。Spanner 的作者 Eric Brewer（CAP 定理提出者本人）寫過一篇 "Spanner, TrueTime and the CAP Theorem" 澄清：Spanner 是 CP 系統，只是它的可用性在實務上極高（Google 的網路夠好，分區罕見），高到「感覺像 CA」，但形式上它在分區時放棄的是 A。

- **schema 變更也是外部一致的**：Spanner 能做**非阻塞的、全球一致的 schema change**。它給 schema 變更分配一個未來的時間戳 `t_schema`，所有交易在 `t_schema` 之前看到舊 schema、之後看到新 schema——靠的正是 TrueTime 讓「未來某一刻」這件事在全球有一致的意義。這是 F1 團隊夢寐以求的能力（還記得那個「兩年的重新分片災難」嗎），也是「有了可信全域時間戳，很多難題突然變簡單」的又一例證。

## 本章重點整理

- Spanner 的野心：**全球分佈 + 強一致（外部一致）+ SQL 交易**，打破「規模與一致性二選一」的舊律。
- 架構三層：底層 **Paxos group** 複製每個 tablet（容錯）、跨 group 交易用 **2PC**（原子性，且 coordinator 也 Paxos 複製以補單點故障）、上層 **TrueTime + commit-wait** 給交易可信時間戳。
- **TrueTime** 把時鐘 API 從「一個點」改成「有界區間 [earliest, latest]」，ε 半寬約 **1~7ms、平均 4ms**（論文數字，鋸齒波動），靠 **GPS + 原子鐘**壓小並保證真值落在區間內。
- **commit-wait** 是核心魔法：交易選 `s = TT.now().latest`，然後等到 `TT.after(s)` 才釋放鎖回覆——這保證「真實先完成的交易時間戳一定較小」，即外部一致性。
- 代價：每筆讀寫交易多等 **≈ 2ε（個位數 ms）**，且需要 GPS/原子鐘硬體。只讀交易免 commit-wait。ε 越小、代價越低，所以 Google 拼命壓 ε。
- 一句話定位：**Spanner 是「用硬體（可信時鐘）解決分散式時間問題」的里程碑**，證明了只要時鐘不確定性可被有界量化，就能重新用時間戳排序全球事件。

## 自我檢核

- [ ] 不看筆記，我能說出 Spanner 架構的三層各自負責什麼（複製 / 跨 group 原子性 / 時間戳）
- [ ] 我能解釋 TrueTime 的 API 跟一般 `now()` 的根本差異，以及 ε 是什麼、大概多大、為什麼會鋸齒波動
- [ ] 我能用「T1 先於 T2 完成」的例子，講清楚 commit-wait 為什麼能保證 ts(T1) < ts(T2)
- [ ] 我能回答「commit-wait 在等什麼」，並說明它跟等網路/等複製的差別
- [ ] 我能解釋為什麼 Spanner 在網路分區時是選一致性（CP），而不是「打破了 CAP」

## 延伸閱讀

### 原始論文

- **[Spanner: Google's Globally-Distributed Database](https://research.google/pubs/pub39966/)** — Corbett et al., OSDI（2012）
  - **讀哪裡**：Section 3（TrueTime，本章 ε 與 GPS/原子鐘的出處）、Section 4.1（讀寫交易與 commit-wait）、Section 4.2（快照讀）
  - **學什麼**：commit-wait 的正確性論證原文、ε 的實測數據（Figure 6），本章的數字全部來自這裡
  - **前提**：讀懂本章 commit-wait 的反證骨架，再回去看論文的 formal 版會很順

- **[Spanner, TrueTime and the CAP Theorem](https://research.google/pubs/pub45855/)** — Eric Brewer（Google, 2017）
  - **讀哪裡**：整篇不長，CAP 提出者親自澄清 Spanner 為何是 CP 而非「打破 CAP」
  - **學什麼**：把本章「進階」段的 CAP 討論講到位，理解「高可用」與「CA」的差別
  - **前提**：[Ch 10](./10-cap-theorem.md) 的 CAP

### 技術文章 / 相關系統

- **[F1: A Distributed SQL Database That Scales](https://research.google/pubs/pub41344/)** — Shute et al., VLDB（2013）
  - **讀哪裡**：Introduction（那個「兩年重新分片災難」的動機）+ 講它怎麼架在 Spanner 上
  - **學什麼**：Spanner 解決的真實業務痛點，理解「為什麼值得為 TrueTime 花這筆硬體錢」

- **[CockroachDB 的時鐘設計文件 / "Living Without Atomic Clocks"](https://www.cockroachlabs.com/blog/living-without-atomic-clocks/)**
  - **讀哪裡**：講它怎麼用 HLC + uncertainty interval 近似 Spanner，不用 GPS/原子鐘
  - **學什麼**：沒有專用硬體時的工程折衷，對照本章「commit-wait 固定等」vs「uncertainty restart 偶爾重試」的取捨
  - **前提**：[Ch 4](./04-physical-clocks.md) 進階段的 HLC

Spanner 展示了「有了可信全域時間戳，強一致交易就能全球化」。下一章換一個完全不同的哲學——不去追求全域時間，而是把「一條只能追加的日誌」當成整個分散式系統的核心資料結構。

→ [Ch 40 Kafka：日誌即系統核心](./40-kafka-log.md)
