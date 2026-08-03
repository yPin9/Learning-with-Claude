# Ch 34 — 現代 BFT：HotStuff 與 Tendermint

> **目標**：理解 PBFT 的 O(n²) 瓶頸為什麼是架構問題而非實作問題，看 HotStuff 如何用「線性通訊 + 門限簽章 + pipelined 三階段」在保留 BFT 安全性的同時達到 O(n) 通訊複雜度，再對比 Tendermint 在 PoS 區塊鏈中的做法（鎖與解鎖）。最後給出三個協定的取捨表。

## 為什麼需要這個？

PBFT 是一個里程碑，但它有一個致命的可擴展性問題：**O(n²) 通訊複雜度**。

在 3f+1 = 4（f=1）的小叢集裡 O(n²) 是常數，不成問題。但想像一個 100 個節點的系統（f=33）：正常路徑的 Prepare 階段，每個節點廣播給其他所有人——100 × 100 = 10,000 條訊息。View change 更糟。隨著 n 增大，頻寬和延遲都爆炸，PBFT 的吞吐量在 n > 20 後幾乎線性下降。

這不是工程問題，是協定設計問題。PBFT 要讓每個 replica 都知道「每個其他 replica 的 Prepare 票」，這需要每個人廣播給所有人——O(n²) 訊息是資訊論上的下界，調不掉。

要打破這個下界，必須從根本改變「如何聚合投票」的方式。

這個問題在 2018 年有了一個漂亮的解：**HotStuff**（Yin et al., ACM PODC 2019）。它用兩個關鍵技術把通訊複雜度壓到 O(n)：

1. **星型（star）通訊拓撲**：replica 只和 leader 通訊，leader 聚合後廣播——而不是全對全廣播。
2. **門限簽章（threshold signature）**：把 n 個獨立簽章壓縮成一個固定大小的聚合簽章。

HotStuff 的 O(n) 通訊和線性 view change 讓它成為現代 BFT 的基準，被 Facebook 的 LibraBFT（後改名 DiemBFT）、Meta 的 Diem 區塊鏈採用。

同期的 **Tendermint**（Buchman et al., 2018）走了另一條路：在 gossip 網路上用「鎖與解鎖（lock and unlock）」機制達到 BFT 共識，被 Cosmos 生態系採用。

> 若還沒讀 Ch 33，先讀它——本章假設你知道 PBFT 三階段和 2f+1 quorum 的意義。

## 先建立直覺：PBFT 的通訊拓撲問題

PBFT 的 Prepare 階段是這樣的：

```
PBFT Prepare 階段（n=4，每個 replica 廣播給所有人）：
  
   Primary(0) ←──────────────────────────────── R3
      │  ↖                                ↗  │
      │     Prepare(3→0)  Prepare(3→1)       │
      │                                       │
   Prepare(0→1)                        Prepare(3→2)
      │                                       │
      ▼  ↘                               ↙   ▼
      R1  ──── Prepare(1→2) ────────────► R2
          ←──── Prepare(2→1) ────────────
  
  訊息數 = n*(n-1) = 4*3 = 12 條
  每個 replica 廣播 n-1 條，收 n-1 條
```

如果改成「所有 replica 只和 leader 通訊」（星型拓撲）：

```
HotStuff Vote 階段（n=4，replica 只傳給 leader）：

   R1 ──── Vote ────►
   R2 ──── Vote ────► Leader(0)  ← 聚合所有 vote
   R3 ──── Vote ────►
   
   Leader 廣播聚合後的 QC（Quorum Certificate）給所有人

   訊息數：n-1（收 vote）+ n-1（廣播 QC）= O(n)
```

直覺：PBFT 讓「每個人都驗證每個人的票」，HotStuff 讓「leader 代收、代聚合，廣播結果」。後者的資訊量（誰投了什麼票）仍然存在，但被壓縮到一個 QC 裡，用密碼學保證 QC 的合法性。

**星型拓撲的代價**：leader 成了通訊瓶頸（bandwidth bottleneck）。leader 收 n-1 個 vote、發 n 個 QC，流量集中在 leader。解法：**rotating leader**（每個 block 換一個 leader），把流量平均分散。

## 門限簽章：把 n 個簽章壓成 1 個

PBFT 中，Prepare quorum certificate 是「2f+1 個節點的簽章列表」——大小 O(n)，驗證時要逐一核對每個簽章，O(n) 計算。

**門限簽章（threshold signature / BLS signature aggregation）** 讓 k-of-n 的「k 個人簽了」這個聲明被壓縮成一個**固定大小**的簽章，無論 n 多大：

```
傳統方式（PBFT）：
  QC = {sig_1, sig_2, ..., sig_{2f+1}}   大小 O(n)，驗 O(n)

門限簽章（HotStuff）：
  QC = threshold_combine(sig_1, ..., sig_{2f+1})  大小 O(1)，驗 O(1)
```

技術上最常見的是 **BLS 簽章**（Boneh-Lynn-Shacham，基於橢圓曲線的雙線性配對）：多個簽章可以直接在群元素上「加法」合成，驗章只需一次配對運算。

有了門限簽章，leader 可以把「我收到了 2f+1 個人的 vote」這件事壓成一個固定大小的 QC 廣播，接收者只需驗一個 QC 而非 2f+1 個簽章。這讓 HotStuff 的訊息大小也是 O(1)（每則訊息大小不隨 n 增長）。

> 注意：本章不要求你實作 BLS 簽章，這是密碼學功能。理解「QC 是一個緊湊的 2f+1 票的合法性證明」就夠了。真實的 HotStuff/LibraBFT 實作（如 Diem 的 Rust 程式碼）用 minSig BLS12-381 曲線。

## HotStuff：三階段 + Pipelining

HotStuff 的正常路徑是三個投票階段（但和 PBFT 的三個階段用途不同）：

```
Prepare → Pre-Commit → Commit
```

每個階段：
1. Replica 把自己的 vote 傳給 leader
2. Leader 收到 2f+1 票 → 組成 QC（Quorum Certificate）
3. Leader 廣播 QC → 進入下一階段

三個 QC 對應的不變式：

- **prepareQC**：「2f+1 個節點同意在此 view 以此值進入 prepare」
- **precommitQC**：「2f+1 個節點看到了 prepareQC，同意進入 pre-commit」——這意味全網都知道「有 quorum 看到 prepare」
- **commitQC**：「2f+1 個節點看到了 precommitQC」——safety committed，可以執行

為什麼要三個 QC 而非兩個？這對應 PBFT 的 Commit 階段的功能：確保在 view change 後，新 leader 能透過看 precommitQC 知道「有個 prepare 已經被 2f+1 人看到」，不能繞過它選不同的值。

```
HotStuff 正常路徑流程（view v，leader L）：

Leader L:
  ① Propose(block b, view v, highQC)
       │
       ▼
  所有 replica 驗證 proposal，送 vote(b, v, phase=PREPARE)

  ② 收到 2f+1 个 prepare vote → 組 prepareQC(b,v)
     Broadcast prepareQC
       │
       ▼
  所有 replica 送 vote(b, v, phase=PRE-COMMIT)

  ③ 收到 2f+1 个 pre-commit vote → 組 precommitQC(b,v)
     Broadcast precommitQC
     replica 本地記 lockedQC = precommitQC（鎖定！）
       │
       ▼
  所有 replica 送 vote(b, v, phase=COMMIT)

  ④ 收到 2f+1 个 commit vote → 組 commitQC(b,v)
     Broadcast commitQC → 所有節點執行 b
```

### Pipelining：三個 block 同時在流水線上

等每個 block 跑完三個階段才開始下一個 block，吞吐量很差（一個 RTT 只推進 1/3 個 block）。HotStuff 的設計讓**不同 block 的不同階段重疊（pipeline）**：

```
        view  1     view  2     view  3     view  4
Block A: Prepare → PreCmt → Commit
Block B:          Prepare → PreCmt → Commit
Block C:                    Prepare → PreCmt → Commit

在 view 3 時，同時有：
  Block A 的 Commit 在進行
  Block B 的 PreCommit 在進行
  Block C 的 Prepare 在進行
```

每個新的 proposal 帶著對前一個 block 的「間接 QC」（b 的 proposal 裡帶著 b-1 的 prepareQC），形成自然的 pipelining。這讓 HotStuff 的延遲是 3 個 round-trip，而不是 PBFT 的 2 個——但吞吐量更高，因為 leader 每個 view 都在持續推進。

### Leader 輪替與安全性

HotStuff 在每個 view（或每個 block）更換 leader：

```
leader(v) = v mod N
```

每次 leader 提案時，必須包含它所見到的最高 QC（highQC）。這讓新 leader 無法提出一個「低於之前 precommitQC」的值，保證 safety：

**Safety 核心不變式**：若一個值 v 在 view v0 取得了 precommitQC，那麼在任何 view > v0 中，誠實 leader 只能提出 v（或 v 的延伸）——因為它的 highQC ≥ precommitQC(v,v0)，而誠實 replica 只會對「proposal 帶 highQC ≥ lockedQC」的提案投票。

### Responsiveness

一個 BFT 協定的 **responsiveness（響應性）** 是指：它能否在訊息延遲變小時，「立刻」推進——而不是必須等一個預設的 timeout？

- **PBFT**：有 responsiveness。Prepare 和 Commit 只等 2f+1 個回覆，一到就繼續，不用等 timeout。
- **Tendermint**（見下節）：沒有 responsiveness（optimistic path 除外）——每個 step 都有固定 timeout，即使 2f+1 個回覆早就到了，也要等 timeout 期滿才能推進。
- **HotStuff**：有 responsiveness。每個階段等到 2f+1 票就推進。

Responsiveness 很重要：在 WAN（廣域網路）部署中，大多數時候訊息延遲比最壞情況小得多，有 responsiveness 的協定能充分利用「好網路」帶來的加速，而沒有 responsiveness 的協定每個 step 都要等 timeout 帶來的最壞延遲。

## Tendermint：Gossip + 鎖與解鎖

Tendermint（Buchman et al. 2018，被 Cosmos 使用）是另一個主流 BFT 協定，設計目標不同：它從一開始就為 **gossip 網路**（節點不直接互連，靠閒聊散播訊息）和 **PoS（Proof of Stake）** 場景設計。

### 三個步驟：Propose、Prevote、Precommit

```
Round r 的流程（proposer P，其他 replica V）：

Step 1 — Propose（超時 timeoutPropose）
  P：廣播 Proposal(block, round, polka?) 
  V：若超時未收到 proposal → 廣播 Prevote(nil)

Step 2 — Prevote（超時 timeoutPrevote）
  V：
    若收到合法 proposal（且未鎖定不同 block，或 proposal 帶能解鎖的 polka）
      → 廣播 Prevote(block)
    否則 → 廣播 Prevote(nil)
  
  收到 2f+1 個 Prevote(block) → 稱為 polka（保準票 quorum）

Step 3 — Precommit（超時 timeoutPrecommit）
  V：
    若收到 polka(block) → 鎖定 block，廣播 Precommit(block)
    若收到 polka(nil) → 解鎖，廣播 Precommit(nil)
    否則（未收到任何 polka）→ 廣播 Precommit(nil)
  
  收到 2f+1 個 Precommit(block) → commit block

若未 commit → 進入 round r+1（timeoutPropose 加大）
```

### 鎖（lock）與解鎖（unlock）

Tendermint 的核心 safety 機制是**鎖定**：

- 節點在收到 polka(block) 後「鎖定」那個 block
- 鎖定後，節點只對「帶 polka 的解鎖信號」或「同一 block 的新 round」投 Prevote
- 如果後來看到 polka(nil)（2f+1 個 Prevote(nil)），才能解鎖，允許在下一 round 投給不同 block

```
鎖定狀態機：

  [未鎖定] ──── 收到 polka(block B) ────► [鎖定 B]
               
  [鎖定 B] ──── 收到 polka(nil)    ────► [未鎖定]
  [鎖定 B] ──── 收到 polka(B)（確認）──► [鎖定 B]（重確認）
  [鎖定 B] ──── 其他 polka(B')    ────► 拒絕（不投票）
```

**為什麼鎖定能保 safety**？假設 block B 在 round r 被 commit（2f+1 個 Precommit(B)）。這意味至少 2f+1 個節點都發了 Precommit(B)，也就是都看到了 polka(B)，也就是都鎖定了 B。在 round r+1 的 Prevote 中：這 2f+1 個節點只會投 Prevote(B) 或 Prevote(nil)（若看到解鎖信號）。而解鎖信號（polka(nil)）需要 2f+1 個 Prevote(nil)——這和「已有 2f+1 個節點鎖定 B、只投 B 的 Prevote」矛盾（quorum 交集）。因此，任何 round > r 的 polka 只能是 polka(B)，不能是 polka(B')，safety 維持。

### 沒有 Responsiveness 的代價

Tendermint 每個 step 都設 timeout（`timeoutPropose`、`timeoutPrevote`、`timeoutPrecommit`）。這是為了在 gossip 網路上運作——gossip 的訊息傳播是非同步的，不知道什麼時候算「夠多人收到了」，所以乾脆等一段固定時間。

代價：即使所有 2f+1 個 Prevote 在 1ms 內都到了，你還是要等 `timeoutPrevote`（通常幾秒）才能推進。這讓 Tendermint 的 block 時間下限是 timeout 總和的量級，而非實際網路延遲。

Cosmos 的實際 block time 是 6–7 秒，正是這個設計的直接結果。Tendermint 為此換到了「不需要點對點連線、gossip 更健壯」的優勢。

## BFT 與區塊鏈的關係預告

Part 5 到 Ch 36 會深入 Nakamoto Consensus（比特幣的 PoW 共識）。這裡先給一個框架：

```
BFT 類協定（permissioned）           Nakamoto / PoW（permissionless）
────────────────────────────────     ─────────────────────────────────
已知 validator 集合（有 PKI）          任何人都能加入（匿名）
N = 3f+1 的固定 quorum               算力/stake 決定「票重」
快速最終性（finality in seconds）      機率最終性（越多確認越安全）
通訊複雜度 O(n) 到 O(n²)             O(1)（每礦工只和鄰居通訊）
容錯 f < N/3 個惡意節點               容錯 < 50% 算力惡意（PoW）
範例：HotStuff/DiemBFT/Tendermint    範例：Bitcoin、Ethereum（PoW 時代）
```

區塊鏈世界常說「BFT 類共識」和「Nakamoto 共識」是兩個不同的設計空間：BFT 類有強最終性但需要已知節點集，Nakamoto 允許匿名加入但只有機率最終性。Tendermint/HotStuff 在「permissioned」或「半 permissioned（PoS 的 validator 集合）」場景下提供 BFT 保證。以太坊的 PoS Casper FFG 就是把 Tendermint 風格的 BFT finality 嫁接到 Nakamoto 類的 PoS 鏈上。

## 底層機制：取捨的核心

### 為什麼 HotStuff 需要三個 QC

PBFT 需要三個階段（Pre-Prepare、Prepare、Commit）。HotStuff 也需要三個階段（Prepare、Pre-Commit、Commit）。為什麼不能少一個？

**答案**（與 PBFT 的論證平行）：

1. **Prepare QC**：確保在這個 view 有 quorum 見過這個 block 的提議
2. **Pre-Commit QC**：確保有 quorum 見過 Prepare QC——也就是「大家都知道大家都見過 block」
3. **Commit QC**：確保有 quorum 見過 Pre-Commit QC——鎖定，view change 安全

少一個的問題：若只有兩個 QC（Prepare + Commit），在 view change 時，新 leader 可能不知道舊 view 裡是否有人已經「知道其他人知道」這件事，可能選出和已 committed 值不同的 block。三個 QC 是「解決拜占庭 view change safety」的最小需求。

HotStuff 的突破在於：把這三個 QC 的收集從「all-to-all 廣播」壓縮成「star 拓撲 + 一個緊湊 QC」——協定的邏輯不變，訊息結構改變。

```
訊息流比較（每個 QC 的收集）：

PBFT Prepare 階段：
  每個 replica ──── Prepare(v,n,d) ────► 每個其他 replica
  訊息數 O(n²)，訊息大小 O(1) 各

HotStuff Vote 階段：
  每個 replica ──── Vote(v,b,phase,sig_i) ────► leader
  leader ─────── QC(v,b,phase,agg_sig) ────► 每個 replica
  訊息數 O(n)，訊息大小 O(1)（門限簽章後）
```

### 為什麼 Tendermint 不用門限簽章

Tendermint 的原始設計（pre-2018）沒有門限簽章，所以 QC 是 2f+1 個簽章的列表，大小 O(n)。這在 Cosmos 的初始實作中造成了 block header 隨 validator 數量增長。

後來的 Cosmos SDK + CometBFT（原 Tendermint Core 的分叉）加入了對 BLS 聚合簽章的支援，解決了這個問題。這說明「協定設計」和「實作最佳化」是可以分開演化的。

## 對比與取捨

| 特性 | PBFT | HotStuff（DiemBFT） | Tendermint（CometBFT） |
|------|------|--------------------|-----------------------|
| 通訊複雜度（正常路徑） | O(n²) | O(n) | O(n²) 原始，O(n) 含 BLS |
| View change 複雜度 | O(n²) | O(n) | O(n²) |
| 通訊拓撲 | All-to-all | 星型（via leader） | Gossip |
| 階段數 | 3（PP/P/C） | 3（Prepare/PreCmt/Commit） | 3（Propose/Prevote/Precommit） |
| Responsiveness | 有 | 有 | 無（fixed timeout） |
| 需要門限簽章 | 不需 | 需要（BLS） | 原始不需，後加 |
| Leader 輪替 | 僅 view change 時 | 每個 block 都換 | 每個 round 都換 |
| 最終性 | 確定性，O(n²) 延遲 | 確定性，O(n) 延遲 | 確定性，timeout 下界 |
| 實際使用 | Hyperledger Fabric v0.6 | DiemBFT、Diem 區塊鏈 | Cosmos、BinanceChain |
| 適用節點規模 | n ≤ 20 | n ≤ 100（有 BLS） | n ≤ 150（Cosmos validator） |

**核心取捨總結**：

- **PBFT** 是理論基礎，理解它是前提；不適合大型現代部署
- **HotStuff** 是「可擴展 BFT」的現代答案，線性通訊讓它在 100 個節點下仍可用；但依賴 BLS 密碼學，門限簽章有實作複雜度
- **Tendermint** 為 gossip 和 PoS 場景優化，沒有 responsiveness 換來了 gossip 的健壯性；適合公鏈 validator 不完全可靠連線的場景

## 踩雷集錦

1. **把 HotStuff 的「三個 QC」和 PBFT 的「三個階段」畫等號**。雖然都是三個，用途有微妙差異。PBFT 的 Pre-Prepare 是 primary 的「排序」宣告，不是 quorum 投票；HotStuff 沒有這個分離，leader 的 Proposal 類似 Pre-Prepare，但後面三個階段全都是 quorum 投票。HotStuff 其實是「把 PBFT 的三階段重新映射成三個 all-to-leader 的投票輪」。

2. **以為 Tendermint 的 lock 是「永久的」**。鎖可以解鎖——收到 `polka(nil)`（2f+1 個 Prevote(nil)）就能解。設計上，鎖的目的是「防止你在同一輪做互相矛盾的 Precommit」，不是「永遠綁死你的投票」。否則協定在拜占庭節點讓 polka 永遠不出現時會卡死（沒有解鎖機制 = 無 liveness）。

3. **誤以為門限簽章是「信任 leader 的簽章」**。門限簽章（threshold sig）不是「leader 替大家簽」——而是「每個人先對自己的 vote 用自己的私鑰簽，leader 用公開的聚合演算法把這些簽章合起來」。聚合結果可以用公開的 aggregate public key 驗證，任何人都能驗，leader 無法偽造不存在的票。

4. **把「Responsiveness」和「快速」混淆**。Responsiveness 是「只要訊息夠快，協定就能跟著快」。沒有 responsiveness 不代表「慢」——如果 timeout 設得小（比如 100ms），系統也能快；但在網路好的時候無法比 timeout 更快。反過來，有 responsiveness 的協定在網路好時能充分利用低延遲，在網路壞時靠 view change timeout 降級。

5. **以為 HotStuff 的 Pipelining 讓延遲更低**。Pipelining 提高的是**吞吐量**（throughput），不降低**個別 block 的延遲**（latency）。一個 block 從提議到 commit 還是要等三個 round-trip（三個 QC），這個延遲不變。Pipelining 讓不同 block 重疊執行，讓系統在單位時間內 commit 更多 block。

## 進階：再往深一層

### LibraBFT / DiemBFT：HotStuff 的工程實作

Meta（Facebook）的 Diem 區塊鏈用的是 HotStuff 的一個變體，稱為 LibraBFT/DiemBFT（2019/2021）。它在 HotStuff 基礎上加了：

- **Round synchronization**：leader 在發 proposal 前先收集足夠多的 timeout certificate，確保在非同步後能達到同步
- **Pacemaker**：管理 view（round）的推進和逾時
- **FIFO 訊息排序**：對 safety 的某些分析有幫助

DiemBFT 的論文（Baudet et al. 2019）是學習「工程可用 BFT」的最佳現代讀物之一。

### Casper FFG：把 BFT 嫁接到鏈上

Ethereum 的 **Casper FFG**（Friendly Finality Gadget，Buterin & Griffith 2017）是一個有趣的架構：它把 Tendermint 風格的 BFT finality 作為「覆蓋層」加在 PoW（或 PoS beacon 鏈）上，讓底層鏈的 block 獲得確定性 finality。Casper FFG 的 Prevote = Tendermint 的 Prevote，Precommit = Tendermint 的 Precommit，「slashing condition」（罰款惡意 validator 的質押）是強化 safety 的激勵機制。

### BFT 的下界：3 個 QC 的最優性

Abraham et al. (2019)「The Case for Byzantine Fault Detection」中有一個結果：在部分同步網路中，任何在 f 個拜占庭故障下保 safety 的 BFT 協定，在正常路徑下至少需要 3 個訊息延遲（3 round-trip）。HotStuff 的三個 QC 正好達到這個下界，是最優的延遲。PBFT 也是三個相位，但通訊複雜度更高，所以 HotStuff 在通訊和延遲上都達到最優。

## 本章重點整理

- **PBFT 的 O(n²) 瓶頸**來自全對全的 Prepare 廣播，是協定結構問題不是工程問題；n > 20 後實用性急降
- **HotStuff 突破**：星型拓撲（replica→leader→broadcast）+ 門限簽章（BLS 聚合）= O(n) 通訊，固定大小 QC
- **HotStuff 三個 QC**：Prepare QC → Pre-Commit QC → Commit QC，對應「見過 block」→「見過大家見過 block」→「鎖定」的三層確認
- **Pipelining**：不同 block 的不同 QC 階段重疊，提升吞吐量；個別 block 延遲仍是 3 round-trip
- **Responsiveness**：HotStuff 有（到票就推進），Tendermint 無（每步固定 timeout）；前者更善用好網路，後者對 gossip 更健壯
- **Tendermint 的 lock/unlock**：看到 polka(block) 就鎖定，看到 polka(nil) 才解鎖；防 split-brain 同時維持 liveness
- **BFT vs. Nakamoto**：BFT 類需要已知 validator 集合，有確定性 finality；Nakamoto 允許匿名加入，只有機率 finality

## 自我檢核

- [ ] 我能解釋為什麼 PBFT 的通訊複雜度是 O(n²)，以及為什麼改變拓撲就能從根本解決
- [ ] 我能說出門限簽章做了什麼（在不理解 BLS 數學的前提下），以及它為什麼不是「信任 leader」
- [ ] 我能說出 HotStuff 三個 QC 各自的用途，以及為什麼不能少一個
- [ ] 我能解釋 Tendermint 的 lock 和 unlock，以及為什麼 unlock 對 liveness 是必要的
- [ ] 我能填出本章的三個協定取捨表的任意一欄，不看筆記

## 延伸閱讀

1. **Yin et al. (2019)「HotStuff: BFT Consensus with Linearity and Responsiveness」** — *ACM PODC 2019*
   - **讀哪裡**：§1（Introduction）清晰說明 PBFT 的 O(n²) 問題；§3（Basic HotStuff）是三個 QC 的形式定義；§4（Chained HotStuff）是 pipelining 版本
   - **學什麼**：HotStuff 的 safety/liveness 的形式化論證（比本章更嚴謹）；Chained HotStuff 的 pipelining 如何把三個階段「折疊」進 rotating leader 的提案鏈
   - **前提**：讀完 Ch 33 + 本章

2. **Buchman (2016)「Tendermint: Byzantine Fault Tolerance in the Age of Blockchains」** — 博士論文 + Cosmos whitepaper
   - **讀哪裡**：論文 Chapter 3（Tendermint Consensus），尤其是 §3.3（Safety and Liveness Proofs）；Cosmos 的 CometBFT 文件（https://docs.cometbft.com/）是現代實作版本
   - **學什麼**：lock/unlock 機制的形式化；Tendermint 和 PBFT 的 view change 機制對比
   - **前提**：本章即可

3. **Baudet et al. (2019)「State Machine Replication in the Libra Blockchain」（LibraBFT v1）** + **Diem (2021)「DiemBFT v4」**
   - **讀哪裡**：LibraBFT v1 的 §3（Protocol）；DiemBFT v4 的 §5（Pacemaker）補充工程細節
   - **學什麼**：從 HotStuff 到「工程可用的 BFT」需要哪些額外機制（round synchronization、pacemaker、FIFO）；這是 BFT 從論文到生產的最好案例
   - **前提**：HotStuff 論文（資料 1）讀完後再看

---

BFT 的機制你現在完整了。下一章從防守轉成攻擊視角：共識層的攻擊面在哪裡？已知協定的哪些假設可以被打破？

→ [Ch 35 共識層攻擊面](./35-consensus-attack-surface.md)
