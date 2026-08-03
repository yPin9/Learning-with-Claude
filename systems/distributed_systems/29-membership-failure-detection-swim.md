# Ch 29 — 成員關係與失敗偵測：SWIM / Gossip

> **目標**：搞懂一個分散式叢集怎麼知道「現在有哪些節點活著」——這件事沒有神的視角，只能靠彼此互探。我們從最笨的 all-to-all 心跳出發，看它為什麼在大叢集炸掉，再逐步推到 **SWIM**：隨機探測、間接探測（ping-req）繞過丟包誤判、suspicion 機制降誤報、把成員更新搭在探測訊息上以 O(log N) 輪擴散。最後在 `dsim` 上真跑一個簡化 SWIM，看一個節點 crash 後多久被全體標成 dead。

> **環境**：Go 1.18.1, WSL2 / Linux x86-64。純標準庫。

## 為什麼需要這個？

前面幾章我們把 Raft（[練習 C](./practice-c-build-raft.md)）、分片（[Ch 27](./27-sharding-partitioning.md)）、一致性雜湊（[Ch 28](./28-consistent-hashing.md)）都建起來了。但這些系統全都偷偷假設了一件事：**它們知道叢集裡有哪些節點、哪些還活著**。

Raft 的 leader 要對「多數派」複製 log——它得知道成員總數是多少、誰失聯了。一致性雜湊的環要把 key 對應到節點——某個節點死了，它負責的 key 得轉交給後繼者，但**「死了」這件事誰來認定、多久認定、認定錯了怎麼辦**？分片控制器要決定把 shard 搬到哪——它得有一份「當前活著的節點」清單。

這份「誰活著」的清單，就是**成員關係（membership）**。維護它的機制，就是**失敗偵測器（failure detector）**。這是所有上層協定共同踩的地基，卻常被當成理所當然。

它難在哪？回到 [Ch 1](./01-why-distributed-is-hard.md) 的核心：分散式系統的本質困難是**部分失敗（partial failure）**——而失敗偵測就是部分失敗最直接的化身。你 ping 一個節點沒回應，它是**死了**（crash）？還是**只是慢**（GC pause、網路擁塞、它自己在等別人）？還是**你們之間的網路斷了**、它對別人還活得好好的？這三種在你這一端**看起來一模一樣**。

FLP 不可能定理（[Ch 16](./16-flp-impossibility.md)）已經告訴我們：在純非同步網路裡，你**無法**完美區分「當機」與「很慢」。所以任何實務的失敗偵測器都是在賭——賭一個 timeout。賭太短，把慢節點誤判成死的（false positive，誤報），觸發沒必要的重新配置、資料搬遷、甚至 Raft 重選舉，整個叢集抖動。賭太長，真死的節點拖很久才被發現，服務一直往黑洞送請求。**失敗偵測的全部工程，就是在這個 false positive vs 偵測延遲的天平上找平衡。**

歷史上第一版方案都很笨：每個節點對其他所有節點週期性發心跳（heartbeat），N 個節點就是 O(N²) 條心跳。100 個節點還好，10000 個節點光心跳就把網路塞爆——而且一個節點的網路卡稍微抖一下，就被一堆 peer 同時誤判。SWIM 這篇 2002 年的論文，就是來解這兩個問題：**把負載從 O(N²) 壓到 O(N)，把誤報用「間接探測 + 懷疑」壓下去。**

## 先建立直覺

先把「叢集怎麼知道誰活著」這件事的兩個獨立子問題分開：

```
   失敗偵測（failure detection）          成員傳播（dissemination）
   ┌──────────────────────────┐         ┌──────────────────────────┐
   │ 「node3 還活著嗎？」      │         │ 「我發現 node3 死了，     │
   │  我去探它、等回應          │  ───►   │   怎麼讓其他 N-1 個都知道」│
   │  沒回應 → 我懷疑它死了      │         │  一個一個廣播 O(N)？      │
   └──────────────────────────┘         │  還是像病毒一樣傳染？       │
        （這是「偵測」）                  └──────────────────────────┘
                                              （這是「傳播」）
```

SWIM 這個名字就是把兩件事都塞進去：**S**calable **W**eakly-consistent **I**nfection-style process group **M**embership。「infection-style」指的就是右邊那半——**用 gossip（八卦／流行病式擴散）傳成員更新**，而不是誰廣播給所有人。

### 直覺一：gossip 為什麼是 O(log N) 輪

先想右半邊。你知道一個秘密，想讓全班 N 個人都知道。做法：每一輪，每個已經知道的人，隨機挑一個同學告訴他。

```
輪 0:  ●○○○○○○○  （1 人知道）
輪 1:  ●●○○○○○○  （知道的人各傳 1 個 → 大約翻倍）
輪 2:  ●●●●○○○○
輪 3:  ●●●●●●●○  （接近全體）
       └── 每輪知道的人數大約翻倍 → 全體感染約 log₂(N) 輪
```

只要每輪感染人數大致翻倍，達到全體就需要 **O(log N)** 輪。N=10000 也不過二十幾輪。這就是 gossip 的魔力：**傳播時間對節點數只是對數成長，而且沒有任何中心點、任何單點瓶頸**。等一下我們會真的跑數字驗證這個對數關係。

> gossip 的另一個名字是 **epidemic protocol（流行病協定）**，因為數學模型跟傳染病擴散一模一樣（SIR model）。這不是比喻，是同一套微分方程。

### 直覺二：SWIM 的探測不是「大家 ping 大家」

失敗偵測那半，SWIM 的關鍵反直覺是：**每個節點每一輪只探測一個隨機挑中的 peer**，不是探測全部。

```
   傳統 all-to-all 心跳                    SWIM 隨機探測
   每個節點對 N-1 個發心跳                  每個節點每輪只 ping 1 個隨機 peer
   ┌─────────────────────┐               ┌─────────────────────┐
   │  A→B A→C A→D A→E ...  │               │  A ──ping──► random(C) │
   │  B→A B→C B→D B→E ...  │               │  下一輪再挑一個         │
   │  每輪 O(N²) 條訊息     │               │  每輪每節點 O(1) 條     │
   └─────────────────────┘               └─────────────────────┘
   偵測快但負載爆炸                        負載固定，偵測略慢但可接受
```

「每輪只探一個」聽起來偵測會很慢——node3 死了，得等到有人剛好隨機挑中它才發現。但關鍵是：**N 個節點每輪各探一個，全叢集每輪就有 N 次探測在跑**，任一個死節點被某人挑中的期望等待時間是常數輪（跟 N 無關）。負載卻從 O(N²) 掉到每節點 O(1)。這是 SWIM 的第一個大勝。

## SWIM 的探測協定：direct → ping-req → suspect → dead

現在把「探一個節點」這件事拆細。天真的做法是：ping 它，設個 timeout，沒回 ack 就宣告它死。**這會誤報爆炸**——因為「沒回 ack」可能是這條路徑剛好丟了一個包、或那節點剛好在做一次 GC。SWIM 用兩個機制把誤報壓下去。

### 機制一：間接探測（indirect ping / ping-req）

A 直接 ping B，B 沒在時限內回。**先別急著判死。** A 隨機找 k 個其他節點（helper），請它們代替 A 去 ping B：

```
   A ──ping──► B         （直接：超時，沒回）
     A 不放棄，改走間接：
   A ──ping-req(B)──► C   ─┐
   A ──ping-req(B)──► D   ─┤  請 C、D 幫我探 B
                           │
   C ──ping──► B ──ack──► C ──"B alive"──► A   （只要一條通，B 就沒死）
   D ──ping──► B          （C 已回報 B 活著，D 這條可有可無）
```

為什麼這招有效？**因為它繞過了「A↔B 之間那條特定路徑」的問題。** 如果只是 A 到 B 的網路暫時抽風、或那一個包剛好被丟，只要 C 或 D 任何一條到 B 的路徑通，A 就知道 B 其實活著，避免了一次誤報。只有當 B 對**所有** k+1 條路徑（直接 + k 個間接）都沒回應，A 才進入下一步。這大幅降低了「單一路徑丟包／擁塞造成誤判」的機率。

### 機制二：suspicion（先懷疑再確認）

就算間接探測也全滅，SWIM 仍**不立刻宣告死亡**。它先把 B 標記成 **suspect（懷疑）**，並把「我懷疑 B」這條更新 gossip 出去。suspect 是一個**中間態**，給 B 一個自我辯護的機會：

```
   alive ──探測全滅──► suspect ──撐過 suspectTimeout 沒被反駁──► dead
             ▲                              │
             │      B 或任何人送來           │
             └──「B 還活著」的更新 ◄─────────┘（反駁 → 打回 alive）
```

反駁怎麼運作？靠 **incarnation number（化身編號）**——每個節點給自己維護一個單調遞增的版本號。當 B 從別人的 gossip 裡聽到「有人 suspect 我」，它知道自己明明活著，就**把自己的 incarnation +1**，然後 gossip 出「我是 alive，incarnation=新值」。因為新 incarnation 比舊的大，這條「我還活著」的更新會蓋過那條「suspect B」的舊更新，全叢集就把 B 打回 alive。

incarnation 是 SWIM 版本的邏輯時鐘：**它讓「更新的資訊」永遠壓過「過期的資訊」，而且只有 B 自己能提升自己的 incarnation**——別人不能偽造 B 復活。這解決了 gossip 天生的問題：兩條矛盾的傳言（「B 死了」vs「B 活著」）在網路裡亂飛，靠誰？靠 incarnation 誰大誰贏。

合起來，一個節點從活到被判死，要走完 **direct ping 逾時 → k 個 indirect ping 全逾時 → 標 suspect → suspect 逾時沒人反駁 → 標 dead** 這整條路。每一關都是一道濾網，濾掉一種誤報來源。

## 底層機制：一個探測週期的完整流程

把 A 探測 B 的一整輪畫成時間軸（下面的 tick 數是我們模擬器裡真跑的常數）：

```
 A 的視角，探測 B：
 t0        發 direct ping ─────────► B
 |         等 ackTimeout=4 tick
 t0+4      B 沒回 ack
 |         ├─► 隨機挑 2 個 helper，各發 ping-req(B)
 |         │      helper 收到 → 代發 ping ─► B
 |         │      B 若活著 → ack 給 helper → helper 回 "B alive" 給 A → A 收到就取消探測
 |         等 pingReqTimeout=6 tick
 t0+4+6    間接也全滅
 |         └─► A 把 B 標成 suspect，並 gossip「suspect B」
 |         等 suspectTimeout=12 tick
 t0+22     還是沒人送來「B alive」反駁
           └─► A 宣告 B DEAD，gossip「dead B」
```

同時，每一則 ping / ack / ping-req 都**搭載（piggyback）**當前的成員更新——這就是 infection-style dissemination。A 探測 B 時順便告訴 B「node5 我看是 alive、node3 我看是 suspect」，B 也把它知道的塞進 ack 回來。**成員資訊不需要獨立的廣播通道，它寄生在原本就要發的探測流量上**，幾乎零額外成本地擴散出去。這是 SWIM 把偵測與傳播縫在一起的巧思。

> 我們的模擬把「整張成員表」都塞進每則訊息（`gossip()` 回傳全表）。真正的 SWIM 只挑**最近變動的幾筆**、每筆限制搭載次數（λlog N 次後就不再帶），避免訊息無限膨脹。我們簡化掉這層，因為叢集小、看得清楚就好——這是本章第一處明確的簡化。

## 真跑：dsim 上的簡化 SWIM

我在 `dsim` 上實作了一個簡化 SWIM（`swim.go`），六個節點，跑到叢集穩定後 crash 掉 node3，看它多久被全體標成 dead。完整程式碼在本章末的附錄，這裡先看跑出來的東西。

常數（都在 `swim.go` 頂端，說得出來源）：`ackTimeout=4`（直接 ping 等 4 個 tick，涵蓋延遲 1-2 的一次往返還有餘裕）、`pingReqTimeout=6`（間接多給一點，因為多一跳）、`suspectTimeout=12`（suspect 撐這麼久才判死，給反駁充分時間）、`pingReqFanout=2`（找 2 個 helper）、探測週期 `period=5`。

真跑（WSL, Go 1.18.1，`go run .`，seed=7）：

```
=== Demo 1：SWIM 偵測 crash 節點 ===
  --- t=20：叢集穩定，全員互看 alive ---
  --- t=20：node3 CRASH（不再回任何 ping）---
  t= 34  node1：直接 ping node3 逾時，改發 2 個 ping-req 間接探測
  t= 40  node1 開始 SUSPECT node3（直接與間接都沒回 ack）
  t= 42  node0 得知 node3 現在是 suspect (incarn=0)
  t= 44  node4：直接 ping node3 逾時，改發 2 個 ping-req 間接探測
  t= 46  node4 得知 node3 現在是 suspect (incarn=0)
  t= 46  node5 得知 node3 現在是 suspect (incarn=0)
  t= 48  node2 得知 node3 現在是 suspect (incarn=0)
  t= 52  node1 宣告 node3 為 DEAD（suspect 逾時未被反駁）
  t= 54  node0 宣告 node3 為 DEAD（suspect 逾時未被反駁）
  t= 56  node2 得知 node3 現在是 dead (incarn=0)
  t= 57  node4 得知 node3 現在是 dead (incarn=0)
  t= 58  node5 得知 node3 現在是 dead (incarn=0)
  --- 結果：5/5 個存活節點已將 node3 標記為 dead ---
```

一步一步讀這條時間軸，SWIM 的每個機制都在裡面現形：

- **t=20 crash，t=34 才有人開始懷疑**：不是立刻。node1 得先剛好在某輪隨機挑中 node3 去探（探測週期 + 隨機挑選的等待），direct ping 逾時（+4）、ping-req 逾時（+6），才走到懷疑。這 14 個 tick 的延遲就是「隨機探測 + 兩層濾網」的成本——換來的是不誤報。
- **t=34→t=40 是 ping-req 那一段**：node1 在 t=34 發間接探測，因為 node3 真的死了（沒有任何路徑能通），間接也全滅，t=40 才升 suspect。如果 node3 只是那條路徑抽風，這裡 helper 就會回報「它活著」，探測取消，不會誤報——這正是 ping-req 存在的理由。
- **t=40 起 suspect 靠 gossip 擴散**：node1 標 suspect 後，t=42 node0、t=46 node4/node5、t=48 node2 陸續**透過搭載在探測訊息上的更新**得知，沒有誰去廣播。這就是 infection-style dissemination。
- **t=52 第一個判死，t=58 全體收斂**：suspect 撐過 `suspectTimeout=12` 沒被反駁（node3 真死了，發不出反駁），node1 在 t=52 宣告 dead，一路 gossip 到 t=58 全部 5 個存活節點都標 dead。**從 crash 到全體確認，約 38 個 tick。**

注意 dead 的擴散（t=52→t=58，6 tick）比 suspect 慢，因為它是從單點（node1）開始 gossip；而 suspect 擴散得快一點是因為當時 node1 和 node4 都各自在探 node3、多個源頭同時擴散。這種「多源 gossip 更快」的現象，跟前面 O(log N) 的直覺一致。

### gossip 擴散的 O(log N) 驗證

同一支程式的第二個 demo，純資訊擴散模型（push gossip：每輪每個已感染節點隨機挑一個 peer 傳染），量測不同 N 下全體感染需要幾輪：

```
=== Demo 2：gossip 擴散輪數（O(log N)）===
  N=   10：全體感染需要  7 輪  (log2(N)=3.3)
  N=  100：全體感染需要 11 輪  (log2(N)=6.6)
  N= 1000：全體感染需要 17 輪  (log2(N)=10.0)
  N=10000：全體感染需要 23 輪  (log2(N)=13.3)
```

看關鍵：**N 每放大 10 倍（多一個數量級），輪數只增加大約 6 輪**——這正是對數成長的簽名（10 倍 = log₂10 ≈ 3.3 倍的 log₂ 值，乘上 push gossip 約 1.7 的常數係數 ≈ 6）。實測輪數約是 `log₂(N)` 的兩倍上下，這跟理論一致：純 push gossip 完全覆蓋需要約 `log₂(N) + ln(N) ≈ 1.7·log₂(N)` 輪（最後幾個沒被感染的節點要靠運氣被挑中，尾巴拖長了）。真正的 SWIM 用 push-pull（雙向交換）會更快，接近 `log₂(N)`。**重點是：N=10000 也不過二十幾輪就傳遍全體，這就是 gossip 撐得起大叢集的原因。**

## φ-accrual failure detector：把「死沒死」變成連續值

SWIM 的判死是二元的：timeout 到就 suspect。但 timeout 該設多少？網路狀況會變，寫死一個值不是太敏感就是太遲鈍。**φ-accrual failure detector**（Hayashibara et al., 2004，Cassandra 和 Akka 用它）換個思路：不輸出「死/活」，而輸出一個**懷疑程度 φ**——一個連續的實數。

它的做法：記錄每個節點過去心跳到達的**時間間隔分布**，假設服從常態分布（或指數分布）。當一個心跳「遲到」了，就算此刻**在這個分布下、比目前更晚才到的機率有多低**，取負對數當 φ：

```
   φ = -log₁₀( P(下一個心跳比現在還晚才到) )

   心跳準時到 → 這機率高 → φ ≈ 0（幾乎不懷疑）
   心跳遲很久 → 這機率極低 → φ 飆高（越來越懷疑）
```

上層自己選閾值：φ > 8 才判死（代表誤判機率約 10⁻⁸）。好處是**它自適應網路抖動**——網路平常就慢、間隔變異大，分布會變寬，同樣的遲到不會馬上讓 φ 爆掉；反之穩定網路裡一點點遲到就拉高 φ。它把「timeout 設多少」這個死參數，換成「你能接受多少誤判機率」這個更有意義的旋鈕。SWIM 和 φ-accrual 不衝突，可以組合：用 SWIM 的隨機探測 + 間接探測當偵測骨架，用 φ 取代寫死的 timeout 判斷。

## 對比與取捨

| 機制 | 每輪負載 | 偵測延遲 | 誤報率 | 傳播 | 適用 |
|---|---|---|---|---|---|
| all-to-all 心跳 | O(N²) | 低（1 個 timeout） | 高（單路徑丟包即誤判） | 通常搭全廣播 O(N²) | 小叢集（<數十節點） |
| 中心化（如 ZooKeeper session） | O(N)（都連中心） | 低 | 中 | 中心點推播 | 已有協調服務時 |
| **SWIM** | **O(N)（每節點 O(1)）** | 中（多層濾網） | **低（ping-req+suspicion）** | **gossip O(log N) 輪** | **大叢集、去中心** |
| φ-accrual | 同上（配 gossip） | 自適應 | 可調（選 φ 閾值） | 需搭傳播機制 | 網路抖動大、要自適應 |

沒有絕對最好。小叢集（Raft 常見的 3/5 節點）用 all-to-all 心跳就夠了，SWIM 的複雜度是浪費——事實上 Raft 的 AppendEntries 心跳本身就兼任了失敗偵測。SWIM 是給**成百上千節點**的場景：Consul（HashiCorp 的 memberlist 庫就是 SWIM 變體）、Cassandra（用 gossip + φ-accrual）、ScyllaDB 都在生產環境用它。

## 踩雷集錦

1. **「沒回應 = 死了」**——錯得最離譜也最常見。沒回應可能是慢、是網路分區、是那一個包丟了、是對方在做 stop-the-world GC。真死跟這些在你這端**完全無法區分**（FLP 已證明）。所以失敗偵測器**永遠只能給機率性的判斷**，任何把它當成「確定知道誰死了」來用的上層邏輯，遲早會在一次 GC pause 或網路抖動時誤觸發災難性的重新配置。正確認識：失敗偵測是「suspicion」不是「certainty」，設計上層時要能容忍誤報（例如 suspect 態、能被反駁）。

2. **timeout 設太短求快**——你以為偵測越快越好，於是把 timeout 壓到剛好覆蓋一次正常往返。結果任何一次網路抖動、一次 minor GC 都超時，節點被瘋狂誤判進進出出（flapping），每次都觸發 gossip 風暴、上層重配置。**偵測延遲和誤報率是對立的**，不能只優化一邊。正確認識：timeout 要留足夠餘裕涵蓋正常的抖動，或直接用 φ-accrual 讓它自適應。

3. **以為 gossip 要 O(N) 或 O(N²) 才能傳遍**——直覺上「讓所有人知道一件事」感覺要挨個通知。錯。gossip 每輪感染數翻倍，**O(log N) 輪**就傳遍，且每個節點每輪只發常數則訊息。這個對數是 gossip 能撐大叢集的全部理由，沒抓住這點就不會理解它為什麼比廣播好。

4. **incarnation number 給錯人提升**——反駁機制的鐵律：**只有節點自己能提升自己的 incarnation**。如果你的實作讓「A 幫 B 反駁、A 去提升 B 的 incarnation」，那就等於任何人都能偽造任何節點的復活，一個死節點會被別人硬拉回 alive 態，永遠判不了死。正確認識：反駁是「B 聽到有人 suspect 自己 → B 自己 +1 incarnation → 自己 gossip 出去」，別人只能轉傳 B 發出的更新。

5. **suspect 態直接跳過、探測全滅就判 dead**——省掉 suspicion 這個中間態看起來簡潔，但你就失去了「給節點反駁機會」的視窗。一個節點只是暫時被隔離（分區）、幾秒後就恢復，沒有 suspect 緩衝就會被硬判死、觸發資料搬遷，等它恢復又得搬回來，全是白工。正確認識：suspect 是刻意的緩衝，它把「可能誤報」的判斷延後、給世界一個修正它的機會。

6. **ping-req 的 helper 也可能死**——你請 C、D 幫忙間接探 B，但萬一 C、D 自己也掛了或跟 B 也不通呢？所以 SWIM 要求**同時**找 k 個（不只一個）helper，只要有一個能通就避免誤報。helper 只有一個時，等於把賭注全押在單一 helper 上，沒比直接 ping 好多少。

## 進階：再往深一層

- **成員關係與共識的關係**：SWIM 是 **weakly-consistent** 的——不同節點對「誰活著」的看法可以短暫不一致，它只保證最終收斂。這對很多場景夠用（負載均衡、快取路由）。但如果你要**強一致的成員視圖**（例如 Raft 的成員變更 [Ch 23](./23-raft-membership-snapshot.md)、或 Kafka 的 ISR 列表 [Ch 40](./40-kafka-log.md)），就不能用 gossip 的最終一致——你得把成員變更本身**跑一次共識**。這是兩個層次：SWIM 給你一個快速、便宜、可能過時的「誰大概活著」，共識給你一個慢、貴、但所有人都同意的「權威成員名單」。生產系統常常兩者並用：gossip 做快速偵測，把結果餵給共識層做權威決定。

- **SWIM 的兩個現代改良（Lifeguard）**：HashiCorp 在生產跑 SWIM（memberlist）多年後發現，SWIM 在節點**自己很忙**（本地 CPU 飽和、來不及回 ack）時容易誤判自己或別人。Lifeguard 這篇論文加了自我察覺（節點發現自己回應變慢就調整自己的 timeout）、以及讓 suspect 的判死時間隨獨立確認數增加而縮短。值得讀，因為它是「理論協定碰到真實生產」磨出來的補丁。

- **gossip 的 push vs pull vs push-pull**：我們 demo 用純 push（感染者主動傳）。pull 是未感染者主動去問。push 在早期快（少數感染者主動擴散），pull 在後期快（大量已感染，隨便問都問到）。**push-pull 兼取兩者**，是實務首選，收斂輪數最接近理論下界 log₂(N)。真正的 SWIM 探測其實就是雙向的（ping 帶更新過去、ack 帶更新回來），天然是 push-pull。

## 本章重點整理

- 失敗偵測的根本難處是 **FLP**：非同步網路裡「當機」與「很慢」無法完美區分，任何偵測器都只能給機率性判斷，工程就是在 **誤報率 vs 偵測延遲** 之間找平衡。
- all-to-all 心跳是 O(N²)、單路徑丟包即誤判，撐不起大叢集。**SWIM** 用兩招解決：隨機探測把負載壓到每節點 O(1)；gossip 把成員傳播壓到 O(log N) 輪。
- SWIM 的偵測管線是四層濾網：**direct ping → indirect ping（ping-req 繞過單路徑丟包）→ suspect（給反駁機會）→ dead**。每層濾掉一種誤報來源。
- **incarnation number** 是反駁的關鍵：只有節點自己能提升自己的 incarnation，讓「我還活著」的新資訊壓過「suspect 你」的舊資訊。
- 成員更新**搭載在探測訊息上（piggyback）**擴散，不需獨立廣播通道——這就是 infection-style dissemination。
- **φ-accrual** 把二元的死/活換成連續的懷疑度 φ，自適應網路抖動，用「可接受的誤判機率」取代死板的 timeout。
- SWIM 是弱一致的成員視圖；要權威成員名單得對成員變更跑共識——兩層次常並用。

## 自我檢核

- [ ] 不看文章，我能說出「一個節點 ping 另一個沒回應」的至少三種可能原因，以及為什麼它們在探測端無法區分
- [ ] 我能解釋 ping-req（間接探測）**具體防的是哪一種誤報**，以及為什麼要找 k 個 helper 而不是一個
- [ ] 我能說出 suspect 這個中間態的存在理由，以及 incarnation number 在反駁裡扮演什麼角色、為什麼只有節點自己能提升它
- [ ] 我能解釋為什麼 gossip 傳遍全叢集是 O(log N) 輪而非 O(N)，並說出這個對數對大叢集意味著什麼
- [ ] 我能說出 SWIM 的弱一致成員視圖，和「對成員變更跑共識」的強一致視圖，各適合什麼場景
- [ ] 我能解釋 φ-accrual 的 φ 值是什麼、它比固定 timeout 好在哪

## 延伸閱讀

### 原始論文

- **[SWIM: Scalable Weakly-consistent Infection-style Process Group Membership Protocol](https://www.cs.cornell.edu/projects/Quicksilver/public_pdfs/SWIM.pdf)** — Das, Gupta, Motivala, DSN（2002）
  - **這篇說什麼**：本章的骨架就是它。定義隨機探測、ping-req、infection dissemination
  - **讀哪裡**：Section 3（協定本體）與 Section 4（suspicion 機制）是核心，數學分析在 Section 3.2
  - **前提**：讀得懂機率的期望值即可，不需要重的機率論

- **[The φ Accrual Failure Detector](https://www.researchgate.net/publication/29682135_The_ph_accrual_failure_detector)** — Hayashibara, Défago, Yared, Katayama, SRDS（2004）
  - **這篇說什麼**：把死/活二元判斷換成連續懷疑度 φ 的完整推導，Cassandra/Akka 的失敗偵測基礎
  - **讀哪裡**：Section 2（動機：為何固定 timeout 不夠）與 Section 3（φ 的計算）；實作在 Appendix
  - **前提**：需要一點常態/指數分布與累積分布函數的概念

- **[Lifeguard: Local Health Awareness for More Accurate Failure Detection](https://arxiv.org/abs/1707.00788)** — Dadgar, Phillips, Currey (HashiCorp), DSN Workshop（2018）
  - **這篇說什麼**：SWIM 在生產（Consul memberlist）跑出的誤報問題與三個補丁，理論碰現實的最佳案例
  - **讀哪裡**：整篇不長；三個機制（Self Awareness、Dogpile、Buddy System）各一節
  - **前提**：先懂本章的 SWIM

### 生產實作原始碼

- **[hashicorp/memberlist](https://github.com/hashicorp/memberlist)** — Go 寫的生產級 SWIM（Consul、Nomad 用它）
  - **讀哪裡**：`state.go` 的 `probe` / `probeNode`（探測管線）、`suspicion.go`（suspicion 計時）。跟本章的 `swim.go` 對照，看真正的實作怎麼處理我們簡化掉的部分（動態 timeout、更新搭載次數上限、TCP fallback）
  - **前提**：讀得懂 Go；本章的 demo 是它的教學骨架版

---

## 附錄：完整可跑程式碼

把 `dsim/dsim.go` 的 `package dsim` 改成 `package main` 複製到同目錄，跟下面的 `swim.go`、`main.go` 放一起 `go run .`（做法見 [Ch 0](./00-environment-setup.md)）。上面貼的輸出就是這份程式碼在 WSL Go 1.18.1 真跑出來的。

<details>
<summary>點開 swim.go（簡化 SWIM 失敗偵測器）</summary>

```go
// swim.go
package main

// 簡化版 SWIM 失敗偵測器 + infection-style dissemination。
// 探測階段：direct ping -> (逾時) ping-req 間接 ping -> (再逾時) suspect -> (再逾時) dead。
// 成員更新（含 incarnation）搭在每則 ping/ack/ping-req 上擴散（piggyback gossip）。

type memState int

const (
	alive memState = iota
	suspect
	dead
)

func (s memState) String() string {
	switch s {
	case alive:
		return "alive"
	case suspect:
		return "suspect"
	default:
		return "dead"
	}
}

// update：一筆成員狀態，用 incarnation 決定新舊。
type update struct {
	id     NodeID
	state  memState
	incarn int
}

type Ping struct{ updates []update }
type Ack struct{ updates []update }

// PingReq：請 helper 代替我去 ping target。
type PingReq struct {
	origin  NodeID
	target  NodeID
	updates []update
}

// PingReqAck：helper 探到 target 活著，回報 origin。
type PingReqAck struct {
	target  NodeID
	updates []update
}

type memberInfo struct {
	state  memState
	incarn int
}

type swimNode struct {
	id      NodeID
	peers   []NodeID
	members map[NodeID]*memberInfo // 我看到的每個 peer 狀態
	incarn  int                    // 自己的 incarnation

	period      int // 每 period tick 探一個節點
	lastProbe   int
	probeTarget NodeID
	awaitAck    bool
	sentPingReq bool
	ackDeadline int
	suspectAt   map[NodeID]int // 何時開始 suspect，用來算 suspect->dead 逾時

	// helper 代 ping 時，記住是哪些 origin 在等回報
	pendingReq map[NodeID][]NodeID

	rng *rng
	log func(now int, format string, args ...interface{})
}

// 每個節點自己的確定性 PRNG（以 id 錯開），不碰 net 的私有 rng。
type rng struct{ s uint64 }

func newRng(seed uint64) *rng { return &rng{s: seed*2862933555777941757 + 3037000493} }
func (r *rng) next() uint64 {
	r.s = r.s*6364136223846793005 + 1442695040888963407
	return r.s
}
func (r *rng) intn(n int) int {
	if n <= 0 {
		return 0
	}
	return int(r.next()>>33) % n
}

const (
	ackTimeout     = 4  // direct ping 沒 ack 就發 ping-req（涵蓋延遲 1-2 的一次往返有餘裕）
	pingReqTimeout = 6  // 間接 ping 也沒回，標 suspect（多一跳，多給時間）
	suspectTimeout = 12 // suspect 撐這麼久沒被反駁，標 dead（給反駁充分時間）
	pingReqFanout  = 2  // 找 2 個 helper 做間接 ping
)

func newSwim(id NodeID, peers []NodeID) *swimNode {
	s := &swimNode{
		id:         id,
		peers:      peers,
		members:    map[NodeID]*memberInfo{},
		period:     5,
		suspectAt:  map[NodeID]int{},
		pendingReq: map[NodeID][]NodeID{},
		rng:        newRng(uint64(id) + 1),
	}
	for _, p := range peers {
		if p != id {
			s.members[p] = &memberInfo{state: alive, incarn: 0}
		}
	}
	return s
}

// 我要散播的成員更新（簡化：打包整張表。真 SWIM 只挑最近變動、每筆限搭載次數）。
func (s *swimNode) gossip() []update {
	var us []update
	for id, mi := range s.members {
		us = append(us, update{id: id, state: mi.state, incarn: mi.incarn})
	}
	return us
}

// 合併收到的更新：新 incarnation 或更嚴重的狀態才蓋過（alive<suspect<dead）。
func (s *swimNode) merge(us []update, now int) {
	for _, u := range us {
		if u.id == s.id {
			// 有人說我 suspect/dead？我還活著 → 提高自己 incarnation 反駁。
			if u.state != alive && u.incarn >= s.incarn {
				s.incarn = u.incarn + 1
				if s.log != nil {
					s.log(now, "node%d 反駁「自己是 %s」，incarnation 拉高到 %d", s.id, u.state, s.incarn)
				}
			}
			continue
		}
		mi := s.members[u.id]
		if mi == nil {
			mi = &memberInfo{}
			s.members[u.id] = mi
		}
		if u.incarn > mi.incarn || (u.incarn == mi.incarn && u.state > mi.state) {
			prev := mi.state
			mi.incarn = u.incarn
			mi.state = u.state
			if mi.state == suspect {
				s.suspectAt[u.id] = now
			}
			if prev != mi.state && s.log != nil {
				s.log(now, "node%d 得知 node%d 現在是 %s (incarn=%d)", s.id, u.id, mi.state, mi.incarn)
			}
		}
	}
}

func (s *swimNode) aliveMembers() []NodeID {
	var out []NodeID
	for _, p := range s.peers {
		if p == s.id {
			continue
		}
		mi := s.members[p]
		if mi != nil && mi.state != dead {
			out = append(out, p)
		}
	}
	return out
}

func (s *swimNode) OnTick(now int, net *Net) {
	// suspect -> dead 逾時檢查
	for id, mi := range s.members {
		if mi.state == suspect && now-s.suspectAt[id] >= suspectTimeout {
			mi.state = dead
			if s.log != nil {
				s.log(now, "node%d 宣告 node%d 為 DEAD（suspect 逾時未被反駁）", s.id, id)
			}
		}
	}
	// direct ping 逾時 -> 發 ping-req 間接探測
	if s.awaitAck && !s.sentPingReq && now >= s.ackDeadline {
		s.sentPingReq = true
		sent := 0
		for _, h := range s.aliveMembers() {
			if h == s.probeTarget {
				continue
			}
			net.Send(Message{From: s.id, To: h, Payload: PingReq{origin: s.id, target: s.probeTarget, updates: s.gossip()}})
			sent++
			if sent >= pingReqFanout {
				break
			}
		}
		if s.log != nil {
			s.log(now, "node%d：直接 ping node%d 逾時，改發 %d 個 ping-req 間接探測", s.id, s.probeTarget, sent)
		}
	}
	// 間接 ping 也逾時 -> 標 suspect
	if s.awaitAck && s.sentPingReq && now >= s.ackDeadline+pingReqTimeout {
		s.awaitAck = false
		mi := s.members[s.probeTarget]
		if mi != nil && mi.state == alive {
			mi.state = suspect
			s.suspectAt[s.probeTarget] = now
			if s.log != nil {
				s.log(now, "node%d 開始 SUSPECT node%d（直接與間接都沒回 ack）", s.id, s.probeTarget)
			}
		}
	}
	// 週期性挑一個 alive 節點探測
	if now-s.lastProbe >= s.period && !s.awaitAck {
		targets := s.aliveMembers()
		if len(targets) > 0 {
			s.lastProbe = now
			t := targets[s.rng.intn(len(targets))]
			s.probeTarget = t
			s.awaitAck = true
			s.sentPingReq = false
			s.ackDeadline = now + ackTimeout
			net.Send(Message{From: s.id, To: t, Payload: Ping{updates: s.gossip()}})
		}
	}
}

func (s *swimNode) OnMessage(m Message, net *Net) {
	now := net.Now()
	switch msg := m.Payload.(type) {
	case Ping:
		s.merge(msg.updates, now)
		net.Send(Message{From: s.id, To: m.From, Payload: Ack{updates: s.gossip()}})
	case Ack:
		s.merge(msg.updates, now)
		if s.awaitAck && m.From == s.probeTarget {
			s.awaitAck = false // 直接探測成功
		}
		// 我曾代人 ping 這個 From？回報那些 origin。
		if origins, ok := s.pendingReq[m.From]; ok {
			for _, o := range origins {
				net.Send(Message{From: s.id, To: o, Payload: PingReqAck{target: m.From, updates: s.gossip()}})
			}
			delete(s.pendingReq, m.From)
		}
	case PingReq:
		s.merge(msg.updates, now)
		s.pendingReq[msg.target] = append(s.pendingReq[msg.target], msg.origin)
		net.Send(Message{From: s.id, To: msg.target, Payload: Ping{updates: s.gossip()}})
	case PingReqAck:
		s.merge(msg.updates, now)
		if s.awaitAck && msg.target == s.probeTarget {
			s.awaitAck = false // 間接探測成功
		}
	}
}
```

</details>

<details>
<summary>點開 main.go（driver：crash 偵測 + gossip 輪數）</summary>

```go
// main.go
package main

import (
	"fmt"
	"math"
)

func main() {
	fmt.Println("=== Demo 1：SWIM 偵測 crash 節點 ===")
	demoSWIMFailure()
	fmt.Println()
	fmt.Println("=== Demo 2：gossip 擴散輪數（O(log N)）===")
	demoGossipRounds()
}

func demoSWIMFailure() {
	net := NewNet(7)
	net.SetLatency(1, 2)
	n := 6
	ids := make([]NodeID, n)
	for i := 0; i < n; i++ {
		ids[i] = NodeID(i)
	}
	nodes := make([]*swimNode, n)
	for i := 0; i < n; i++ {
		s := newSwim(ids[i], ids)
		s.log = func(now int, format string, args ...interface{}) {
			fmt.Printf("  t=%3d  %s\n", now, fmt.Sprintf(format, args...))
		}
		nodes[i] = s
		net.Add(ids[i], s)
	}

	net.Run(20) // 先讓叢集穩定探測幾輪
	fmt.Println("  --- t=20：叢集穩定，全員互看 alive ---")

	net.Crash(NodeID(3))
	fmt.Printf("  --- t=%d：node3 CRASH（不再回任何 ping）---\n", net.Now())

	net.Run(120) // 跑到有人把 3 判死並 gossip 全體

	deadCount := 0
	for _, s := range nodes {
		if s.id == 3 {
			continue
		}
		if mi := s.members[3]; mi != nil && mi.state == dead {
			deadCount++
		}
	}
	fmt.Printf("  --- 結果：%d/%d 個存活節點已將 node3 標記為 dead ---\n", deadCount, n-1)
}

// gossip 擴散：純資訊擴散（push），每輪每個已感染節點隨機挑 1 個 peer 傳染。
func demoGossipRounds() {
	for _, n := range []int{10, 100, 1000, 10000} {
		rounds := gossipSpread(n, 12345)
		fmt.Printf("  N=%5d：全體感染需要 %2d 輪  (log2(N)=%.1f)\n", n, rounds, math.Log2(float64(n)))
	}
}

func gossipSpread(n int, seed uint64) int {
	infected := make([]bool, n)
	infected[0] = true
	count := 1
	r := newRng(seed)
	rounds := 0
	for count < n && rounds <= 1000 {
		rounds++
		newly := []int{}
		for i := 0; i < n; i++ {
			if infected[i] {
				t := r.intn(n)
				if !infected[t] {
					newly = append(newly, t)
				}
			}
		}
		for _, t := range newly {
			if !infected[t] {
				infected[t] = true
				count++
			}
		}
	}
	return rounds
}
```

</details>

搞定了成員關係，我們手上就有一份「誰活著」的清單。但活著的節點之間要協調一件跨越多個節點的操作——例如「同時扣 A 的錢、加 B 的錢，要嘛都成、要嘛都不成」——又是另一個難題。下一章進入分散式交易。

→ [Ch 30 分散式交易：2PC / 3PC](./30-distributed-transactions-2pc-3pc.md)
