# Ch 23 — Raft ④：Membership 與 Snapshot

> **目標**：真實叢集要能滾動升級、加減節點、換掉壞掉的機器——但**變更成員本身**是共識裡最危險的操作之一。搞懂為什麼「直接換配置」會腦裂（新舊多數不相交的重疊期）、Raft 怎麼用 **joint consensus（C-old,new 過渡）** 安全變更、以及後來大家更愛的 **single-server change**（一次只加/減一個）。再處理 log 無限成長的問題——**snapshot（狀態快照 + 丟棄前綴）** 與落後太多的 follower 直接裝快照的 **InstallSnapshot RPC**。

> **環境**：Go 1.18.1, WSL2/Linux x86-64。腦裂情境在 `dsim` 用分區模擬真跑；joint consensus 與 snapshot 的完整機制屬本章標注的「理論預期」。

## 為什麼需要這個？

前三章的 Raft 有個隱藏假設：**節點集合永遠不變**。5 個節點就是這 5 個，從開機到關機。真實世界不是這樣：

- 一台機器硬碟壞了，要換一台新的頂上。
- 業務成長，3 節點叢集要擴到 5 節點。
- 滾動升級，要一台台換版本，過程中叢集不能停。

這些都需要**動態變更成員（membership change）**。聽起來像「改個設定檔的事」，但它是 Raft 裡**最容易寫錯、最容易釀成災難**的部分。原因很簡單也很致命：**Raft 的一切正確性都建立在「多數（majority）」上，而變更成員會改變「多數」的定義。**當「多數」這個地基在腳下移動時，前一章辛苦證明的所有 safety 性質瞬間失效。

第二個問題是 log 會**無限成長**。Raft 靠 log 記住所有操作，但一個跑了幾個月的 KV store，log 可能有幾千萬筆——不可能永遠留著。要能把「已經 apply 進狀態機的舊 log」丟掉，換成一個狀態的**快照（snapshot）**。這章把這兩個「讓 Raft 能長期真實運行」的工程問題一次解決。

## 先建立直覺：為什麼直接換配置會腦裂

先看**錯誤**的做法：一聲令下，所有節點同時從舊配置 C_old 切到新配置 C_new。問題在於——**切換不可能真正同時發生**。訊息有延遲，總有一段「有些節點已經用 C_new、有些還在用 C_old」的重疊期。

假設 C_old = {n0, n1, n2}（3 節點，多數是 2），要變成 C_new = {n2, n3, n4}（3 節點，多數是 2）。在重疊期：

```
        重疊期（致命）
   ┌────────────────────────────────────────┐
   │  舊配置 C_old = {n0,n1,n2}               │
   │    它的一個多數：{n0, n1}                 │
   │                                          │
   │  新配置 C_new = {n2,n3,n4}               │
   │    它的一個多數：{n3, n4}                 │
   │                                          │
   │  {n0,n1} ∩ {n3,n4} = ∅  ← 兩個多數不相交！ │
   └────────────────────────────────────────┘
      n0,n1 可以用「舊多數」選出 leader A
      n3,n4 可以用「新多數」選出 leader B
      → 同一時刻兩個 leader = 腦裂
```

**兩個不相交的多數，可以在同一個 term 各自選出一個 leader。** Ch 20 的「多數相交 ⟹ 一 term 一 leader」保證，在配置變更的重疊期直接崩塌。兩個 leader 各接受寫入、各自 commit，資料就分岔了——這是共識系統能發生的最嚴重故障。

### 真跑：腦裂長什麼樣

我們在 `dsim` 上模擬這個重疊期——讓 n0/n1 用舊配置 {0,1,2}、n3/n4 用新配置 {2,3,4}，然後用分區把 {0,1} 和 {2,3,4} 切開，模擬「兩個配置各自的多數落在不同側」（WSL, Go 1.18.1）：

```
=== 直接換配置為何腦裂：兩個不相交的多數各選一個 leader ===
舊配置側 {0,1,2}（其多數 {0,1} 在左島）認定的 leader: [1]
新配置側 {2,3,4}（其多數 {3,4} 在右島）認定的 leader: [3]
全叢集同時存在的 leader: [1 3]
>>> 兩個 leader 同時存在 = 腦裂。
```

**n1 和 n3 在同一時刻都當上了 leader。** 左島用舊配置的多數 {0,1} 選出 n1，右島用新配置的多數 {3,4} 選出 n3。這正是「直接換配置」在重疊期會發生的災難——兩個多數不相交，就有兩個合法 leader。這個真跑把 Raft 論文 Figure 10 的警告變成了眼前的輸出。

## Joint Consensus：C-old,new 過渡

Raft 原始論文的解法是 **joint consensus（聯合共識）**。核心洞見：**別讓叢集直接從 C_old 跳到 C_new，中間插一個「同時屬於新舊兩個配置」的過渡狀態 C_old,new。**

在 C_old,new 這個過渡配置下，**任何決策（選舉、commit）都必須同時取得 C_old 的多數「和」C_new 的多數**——兩個多數都要湊到，缺一不可。

```
   C_old ──────> C_old,new ──────> C_new
              (過渡：雙重多數)

   在 C_old,new 下當選 leader / commit entry，要求：
     ✓ 拿到 C_old 的多數
     ✓ 且拿到 C_new 的多數
   （兩個都要，不是二選一）
```

**為什麼這樣就安全？** 因為在 C_old,new 期間，「同時取得兩個多數」這個要求，讓不可能有兩個 leader：

- 任何 C_old 的多數彼此相交（Ch 20）。
- 任何 C_new 的多數彼此相交。
- 要當 leader 必須「同時是 C_old 多數 + C_new 多數」——這比單一多數更嚴格。
- 兩個候選人都滿足這個雙重條件時，它們在 C_old 側相交、在 C_new 側也相交 ⟹ **不可能同時當選**。

過渡的完整流程（配置本身也是一個 log entry，透過正常的 log 複製機制傳播）：

```
1. leader 收到「換成 C_new」的請求
2. leader 把 C_old,new 當一個 config entry append 到 log、複製出去
   → 一旦某節點看到 C_old,new entry，它立刻用這個新規則做決策
     （不等 commit！config entry 一寫進 log 就生效——這點很反直覺）
3. C_old,new entry 在「雙重多數」下被 commit
   → 此後保證：任何 leader 都必然包含 C_old,new，不可能有純 C_old 的 leader
4. leader 再 append C_new entry、複製出去
5. C_new commit 後，不在 C_new 裡的節點（如 n0,n1）可以下線
```

第 2 步那個「config entry 一進 log 就生效，不等 commit」是 joint consensus 最容易搞錯的細節。理由：如果等 commit 才生效，而這個 config entry 後來被覆蓋了，就會有節點用了一個從沒生效過的配置——反而更亂。Raft 的規則是**配置變更用「最新看到的 config entry」（不管 commit 沒），這樣覆蓋時配置也跟著回退，保持一致**。

> **joint consensus 的完整實作屬「理論預期」**。要在 `dsim` 上真跑它，需要在 `raft.go` 裡加上「config entry 型別 + 雙重多數的 majority 計算 + 兩階段切換狀態機」，這是練習 C（手刻 Raft）的進階任務，超出本章骨架。我們把「為什麼需要它」用腦裂真跑證明了，機制本身對照論文 §6 講清楚。

## Single-Server Change：更簡單的後來者

joint consensus 是對的，但**複雜**——雙重多數、config entry 未 commit 就生效、過渡期的各種邊界情況，實作起來 bug 叢生。Ongaro 在博士論文裡提出並推薦一個更簡單的替代：**一次只加或減一個節點（single-server membership change）**。

關鍵洞見：**如果一次只改變一個節點，新舊配置的多數「必然相交」，就不需要 joint consensus 那套雙重多數了。**

```
   C_old = {n0,n1,n2} (多數 2)   加一個 n3
   C_new = {n0,n1,n2,n3} (多數 3)

   C_old 的任意多數（2 個）與 C_new 的任意多數（3 個）
   在 4 個節點裡必然相交（2+3 > 4）
   → 不可能有兩個不相交多數 → 不可能腦裂
```

一次加一個、或減一個，新舊配置的多數在數學上保證重疊，所以可以**直接單階段切換**，不用過渡配置。要從 3 節點變 5 節點？加兩次，一次一個：3→4→5。每一步都安全。

這個方法簡單到 **etcd、Consul 等主流實作都採用它、而非 joint consensus**。它的代價是「大規模改配置要多做幾步」，但換來實作大幅簡化，非常划算。

> **一個著名的坑**：single-server change 有個微妙的 bug（Ongaro 論文原始版本有，後來 errata 修正）——在新節點還沒追上 log 時就算進配置，可能在特定時序下短暫破壞可用性甚至 safety。修法是**新加入的節點先當「learner / non-voting member」**，不算進多數、只默默同步 log，等它追上了再正式升為 voting member。etcd 的 learner 機制就是幹這個。加節點前先加 learner，是實務標準做法。

## Snapshot：log 不能無限長

第二個問題：log 會無限成長。一個線上跑三個月的 Raft KV store，log 可能累積上千萬筆 entry。問題：

- **佔空間**：全留著，磁碟撐爆。
- **重啟慢**：節點崩潰重啟要 replay 整個 log 重建狀態，上千萬筆要跑很久。
- **回補慢**：一個新節點或落後太多的節點要從頭複製整個 log。

解法是 **snapshot（快照）**：既然 log 的作用是「重建狀態機的狀態」，那我**直接把當前狀態機的狀態存下來**，就可以把它之前的 log 全部丟掉。

```
   snapshot 前：
   log: [1][2][3][4][5][6][7][8]  ← 全部保留，越來越長
             │
             ▼ 前 5 筆已 apply 到狀態機，做 snapshot
   snapshot 後：
   snapshot{ lastIncludedIndex:5, lastIncludedTerm:1, state: {x=1,y=2,...} }
   log: [6][7][8]   ← index 1~5 的 log 丟掉，只留 snapshot + 之後的 log
```

snapshot 要記三樣東西：

- **state**：狀態機當時的完整狀態（KV store 的話就是整個 map）。
- **lastIncludedIndex**：這個 snapshot 涵蓋到哪個 log index（丟棄這之前的 log）。
- **lastIncludedTerm**：那個 index 的 term——保留它是為了讓一致性檢查（Ch 21 的 `prevLogTerm`）還能運作。丟了 log 但這格的 term 資訊必須留著，否則 snapshot 之後第一筆 `AppendEntries` 的一致性檢查會失敗。

每個節點**各自獨立**做 snapshot（不需要協調）——因為每個節點的狀態機在同一個 apply 點的狀態必然相同（RSM 決定性），各自存各自的沒問題。這是 snapshot 能簡單的關鍵。

## InstallSnapshot RPC：回補落後太多的 follower

snapshot 引入一個新問題：leader 要複製 log 給某個落後的 follower，但**leader 需要的那段 log 已經被 snapshot 丟掉了**怎麼辦？

```
   leader 的 log 從 index 6 開始（1~5 已被 snapshot 丟棄）
   follower n0 落後到只有 index 2

   leader 想送 index 3 給 n0 → 但 index 3 已經被丟了，送不出來！
```

這時 leader 改用 **InstallSnapshot RPC**：不送 log，直接把整個 snapshot 傳給 follower，follower 拿它替換掉自己的狀態、丟掉舊 log，一步到位追上。

```
   leader ──InstallSnapshot{lastIncludedIndex:5, state:{...}}──> follower n0
   n0 收到：
     1. 用 snapshot 的 state 替換自己的狀態機
     2. 丟掉 index <= 5 的 log
     3. lastApplied = commitIndex = 5
     4. 之後 leader 再用正常 AppendEntries 從 index 6 繼續複製
```

判斷用哪個：leader 對每個 follower 的 `nextIndex[f]`，如果它指向的 index **已經被 leader snapshot 掉了**（< leader 的 lastIncludedIndex + 1），就送 InstallSnapshot；否則正常送 AppendEntries。

實務上 snapshot 可能很大（幾 GB），要分塊（chunked）傳，InstallSnapshot RPC 帶 offset/done 欄位分多次傳完——這是工程細節，論文 Figure 13 有完整定義。

> **snapshot 與 InstallSnapshot 的完整實作也屬「理論預期」**——它需要在 `raft.go` 加狀態機序列化、log 截斷、InstallSnapshot 訊息型別。這是練習 C 的進階部分。本章把機制與邊界（lastIncludedTerm 為何要留、何時用 InstallSnapshot）講清楚。

## 底層機制：配置變更與 snapshot 的互動

一個容易忽略的細節：**配置變更 entry 和 snapshot 會互動**。config 本身是 log 裡的一個 entry，如果它被 snapshot 涵蓋了，snapshot 必須也記住「當時生效的配置是什麼」——否則節點裝完 snapshot 後不知道叢集成員是誰。所以完整的 snapshot 除了狀態機資料，還要包含**當前配置**。

```
   完整 snapshot 內容：
   ┌─────────────────────────────────┐
   │ lastIncludedIndex / Term         │  ← 一致性檢查需要
   │ 狀態機資料 (KV map / ...)          │  ← 重建狀態
   │ 當前叢集配置 (成員列表)            │  ← 裝完知道成員是誰
   └─────────────────────────────────┘
```

這就是為什麼「把配置也當成狀態機的一部分」是乾淨的設計——snapshot 一存，配置自動跟著存下來。etcd 就是這麼做的。

## 對比與取捨

| 方案 | 安全機制 | 複雜度 | 誰在用 |
|---|---|---|---|
| 直接換配置 | **無**——重疊期腦裂 | 最低 | **沒人敢用**（錯的） |
| Joint consensus | 過渡期雙重多數 | 高（雙多數、未commit生效、多邊界） | Raft 原論文、少數實作 |
| Single-server change | 新舊多數必相交（一次改一個） | 低 | **etcd / Consul（主流）** |
| + learner 預同步 | 新節點先不算多數 | 中 | etcd / TiKV 生產標配 |

| log 管理 | 空間 | 重啟/回補速度 | 代價 |
|---|---|---|---|
| 純 log（不 snapshot） | 無限成長 | 越來越慢 | 撐不了長期運行 |
| 定期 snapshot | 有界 | 快 | snapshot 時的 CPU/IO 尖峰 |

主流選擇很明確：**single-server change +（大配置變更前先加 learner）+ 定期 snapshot**。joint consensus 理論漂亮但實作太重，除非有特殊需求（一次要換一大批節點），否則 single-server change 完勝。

## 踩雷集錦

1. **錯誤直覺：「配置變更就是改個成員列表、廣播一下」→ 正確：它會改變『多數』的定義，重疊期可能腦裂**。這是本章最核心的警告。變更成員動搖了 Raft 一切 safety 的地基（多數相交）。必須用 joint consensus 或 single-server change 保證新舊多數相交，不能天真直接切。我們真跑了兩個 leader 同時存在的腦裂。

2. **錯誤直覺：「config entry 要等 commit 才生效」→ 正確：一看到就生效（用最新的 config entry，不管 commit 沒）**。這反直覺但必要——若等 commit 才生效、而 entry 後來被覆蓋，會有節點用了從沒生效的配置。規則是配置隨 log 走，被覆蓋就跟著回退。

3. **錯誤直覺：「加新節點就直接算進多數」→ 正確：新節點 log 空，先當 learner 不算多數，追上再升 voting**。新節點 log 落後一大截，若立刻算進多數，會拖慢甚至短暫破壞可用性（多數裡有個永遠追不上的拖油瓶）。先當 non-voting learner 默默同步，追上了再正式加入。

4. **錯誤直覺：「snapshot 需要所有節點協調一起做」→ 正確：每個節點各自獨立做**。RSM 的決定性保證所有節點在同一個 apply index 的狀態相同，所以各存各的 snapshot 內容一致，不需協調。硬要協調反而引入不必要的同步點。

5. **錯誤直覺：「snapshot 只要存狀態機資料就好」→ 正確：還要存 lastIncludedIndex/Term 和當前配置**。少了 lastIncludedTerm，snapshot 之後第一筆 AppendEntries 的一致性檢查沒有比對基準會失敗；少了配置，裝完 snapshot 不知道成員是誰。snapshot 是「能獨立重建節點」的完整快照，不只是資料。

6. **錯誤直覺：「follower 落後就一直用 AppendEntries 慢慢回補」→ 正確：落後超過 leader 已 snapshot 的部分，必須改用 InstallSnapshot**。leader 需要的舊 log 已被丟棄時，AppendEntries 送不出那段，只能整包 snapshot 傳過去。判斷點是 `nextIndex[f] < lastIncludedIndex+1`。

## 進階：再往深一層

- **membership change 的 liveness 陷阱**：Ongaro 論文原版 single-server change 有個時序 bug——剛加入的節點在配置切換瞬間可能導致一段時間選不出 leader。errata 和 etcd 的修法是強制「一次配置變更完成（新 config commit）前，不允許發起下一次」。做 membership change 一定要串行化，別並發。

- **snapshot 的寫時複製（copy-on-write）**：做 snapshot 時狀態機還在被寫入，直接序列化會拿到不一致的狀態。生產系統用 COW（如 RocksDB 的 snapshot、fork + CoW 記憶體）在一個一致的時間點凍結狀態、背景慢慢寫出，不阻塞前台。這是 snapshot 從「概念」到「不卡線上服務」的關鍵工程。

- **learner 的其他用途**：learner（non-voting member）不只用於加節點前的預同步，還能當**唯讀副本**——訂閱 leader 的 log、提供最終一致的讀，分攤讀流量而不影響共識多數。TiKV 的 follower read、CockroachDB 的 non-voting replica 都建在這上面。

- **和 Ch 26 線性一致讀的關聯**：配置變更期間的讀更微妙——leader 可能正處於 C_old,new，它的「多數」定義在變，ReadIndex 的多數確認要用哪個配置？這是把 membership change 和線性一致讀組合起來時的邊界，生產系統要小心處理。

## 本章重點整理

- 變更成員會**改變「多數」的定義**，動搖 Raft 一切 safety 的地基。直接換配置會在重疊期腦裂——我們真跑了兩個 leader 同時存在。
- **Joint consensus**：插入過渡配置 C_old,new，期間任何決策要**同時取得新舊兩個多數**，堵死不相交多數的漏洞。config entry 一進 log 就生效（不等 commit）。理論正確但實作重。
- **Single-server change**：一次只加/減一個節點，新舊多數**數學上必然相交**，可單階段直接切換。etcd/Consul 主流採用。配 **learner 預同步** 避免新節點拖累多數。
- **Snapshot**：把狀態機當前狀態存下、丟棄之前的 log，解決 log 無限成長。每節點獨立做，內容含 lastIncludedIndex/Term + 狀態 + 配置。
- **InstallSnapshot RPC**：follower 落後超過 leader 已丟棄的 log 時，直接整包傳 snapshot 追平，之後再回到正常 AppendEntries。
- 主流組合：**single-server change + learner 預同步 + 定期 snapshot（COW）**。

## 自我檢核

- [ ] 我能畫出「直接換配置」在重疊期怎麼產生兩個不相交多數、各選一個 leader
- [ ] 我能解釋 joint consensus 的「雙重多數」為什麼能防止腦裂
- [ ] 我能說出為什麼 config entry「一進 log 就生效、不等 commit」
- [ ] 我能論證為什麼 single-server change 不需要 joint consensus（多數必相交）
- [ ] 我知道為什麼加新節點前要先讓它當 learner
- [ ] 我能列出一個完整 snapshot 必須包含哪三類資訊，各自為什麼不能少
- [ ] 我能說出 leader 何時要用 InstallSnapshot 而非 AppendEntries

## 延伸閱讀

- **[In Search of an Understandable Consensus Algorithm (Raft)](https://raft.github.io/raft.pdf)** — Ongaro & Ousterhout, USENIX ATC 2014
  - **讀哪裡**：§6（Cluster membership changes，含 Figure 10/11 的 joint consensus）、§7（Log compaction / snapshot，Figure 13 的 InstallSnapshot）
  - **為什麼值得讀**：joint consensus 和 snapshot 的原始定義，Figure 10 的腦裂圖就是本章真跑重現的那個

- **[Consensus: Bridging Theory and Practice](https://github.com/ongardie/dissertation)** — Diego Ongaro 博士論文（2014）
  - **讀哪裡**：Ch 4（membership changes，含 single-server change 的完整論證與 learner）、Ch 5（log compaction）
  - **為什麼值得讀**：single-server change 是論文沒有、只在博論才詳述的，主流實作用的是這個版本

- **[etcd learner 設計文件](https://etcd.io/docs/latest/learning/design-learner/)**
  - **讀哪裡**：整篇，尤其「Raft learner」和它解決的可用性問題那段
  - **學什麼**：learner 在生產系統怎麼落地、為什麼加節點一定先加 learner。理論到工程的橋

- **[TiKV: The Raft library and Multi-Raft](https://tikv.org/deep-dive/scalability/multi-raft/)**
  - **讀哪裡**：membership change 與 snapshot 那幾節
  - **學什麼**：真實分片系統怎麼同時管理上萬個 Raft group 的成員變更與 snapshot，看工程規模的挑戰

Raft 四章到此完整：選舉、複製、safety、成員與 snapshot。但 Raft 不是唯一的共識演算法——它甚至不是最早的。下一章把 Paxos、Raft、VR、Zab 四大共識家族擺在一起對照，看清它們在解同一個問題時做了哪些不同的取捨，總結整個 Part 3。

→ [Ch 24 Paxos vs Raft vs VR vs Zab](./24-consensus-comparison.md)
