# Ch 24 — Paxos vs Raft vs VR vs Zab

> **目標**：Part 3 的總結。把共識家族的四大成員——**Multi-Paxos、Raft、Viewstamped Replication (VR)、Zab**——擺在一起對照，看清它們在解同一個問題時各自做了什麼取捨（leader 機制、log 結構、成員變更、崩潰恢復）。再各給一段給無 leader 的 **EPaxos**（衝突圖）和放寬 quorum 的 **Flexible Paxos**。學完你能一眼看穿一個新共識協定「本質上是這四個裡的哪個變體」。

> **環境**：本章是概念對照章，無新程式碼。前四章的 Raft 真跑經驗是理解本章對照的基礎。

## 為什麼需要這個？

學完 Raft，很容易產生一個錯覺：**「共識 = Raft」**。這是危險的。Raft 只是眾多共識協定裡最好懂的一個，不是唯一、也不是最早的。

事實上，這領域有個尷尬又迷人的真相：**Viewstamped Replication (VR) 比 Paxos 還早（1988 vs Paxos 論文 1990/1998），而且它長得跟 Raft 驚人地像——強 leader、view/term、log 複製。** Raft 2014 年被譽為「終於有個好懂的共識演算法」時，一批老工程師的反應是「這不就是 VR 嗎？」。理解這段歷史，你會發現**這些協定在解決同一個核心問題，只是切入角度、術語、和工程包裝不同**。

為什麼要對照？因為真實系統用的往往不是純 Raft：

- **ZooKeeper** 用 **Zab**（不是 Raft，雖然很像）。
- **etcd / Consul / TiKV / CockroachDB** 用 **Raft**。
- **Google Chubby / Spanner** 用 **Multi-Paxos**。
- **MySQL Group Replication** 用 **Paxos 變體**。

你去讀這些系統的設計文件，看到 Zab 的 epoch、Paxos 的 ballot、VR 的 view，如果你腦子裡只有 Raft 的 term，會一頭霧水。學會對照，這些術語瞬間對映起來——**它們是同一個概念的四種方言**。

## 先建立直覺：它們在解同一件事

四個協定都在解 [Ch 15](./15-consensus-problem.md) 定義的那個問題：**一群會當機的節點，在會丟訊息、延遲的網路裡，對「一連串操作的順序」達成一致**。而且都用同一個核心武器——**多數 quorum（過半數）+ 一個邏輯時間戳來排序 leader 更迭**。

差別在於**設計哲學的側重**：

```
   Paxos ─────────── 理論優先，證明漂亮，實作留白
     │                （「共識的公理」，但沒告訴你怎麼組成系統）
     │
   VR ────────────── 系統優先，1988 就給了完整 replication 系統
     │                （強 leader + view change，Raft 的精神前身）
     │
   Zab ────────────── 為 ZooKeeper 量身打造
     │                （primary-backup + FIFO 順序保證）
     │
   Raft ──────────── 可理解性優先
                      （把 VR 的精神重新包裝成人能懂的樣子）
```

一句話記住四者的靈魂：

- **Paxos**：共識的**數學本質**，但你得自己想辦法把它變成能用的系統。
- **VR**：第一個把共識做成**完整複製系統**的，Raft 的祖先。
- **Zab**：ZooKeeper 的引擎，強調**FIFO + primary-backup**。
- **Raft**：把上面這些的精華**重新設計成好懂的樣子**。

## 術語對照：同一個概念的四種方言

先建這張「翻譯表」，後面的對照才不會被術語繞暈。這是本章最實用的一張表：

| 概念 | Paxos | VR | Zab | Raft |
|---|---|---|---|---|
| 邏輯時間 / leader 世代 | ballot number | view number | epoch | **term** |
| 當家的節點 | (distinguished) proposer | primary | leader | **leader** |
| 跟隨的節點 | acceptor | replica / backup | follower | **follower** |
| leader 更替 | 換 ballot 重跑 phase 1 | **view change** | leader election + discovery | **leader election** |
| 一則操作 | a decree / value | log entry | transaction (zxid) | **log entry** |
| 提交確認 | chosen | committed | committed | **committed** |

**看懂這張表，四個協定就打通一半。** 當你讀 ZooKeeper 論文看到 epoch、讀 VR 論文看到 view change，心裡自動翻成 Raft 的 term、leader election，就不會迷路。它們真的是同一組概念換了名字。

## 逐面向對照

### leader 機制

| | Paxos (Multi-Paxos) | VR | Zab | Raft |
|---|---|---|---|---|
| 需要 leader 嗎 | 選擇性（可無 leader，但慢） | **強制** primary | **強制** leader | **強制** leader |
| log 流向 | 理論上任意 proposer 可提案 | primary → backup 單向 | leader → follower 單向 | leader → follower 單向 |
| 選 leader 的觸發 | proposal 衝突時換 ballot | primary 疑似掛 → view change | 心跳超時 | 心跳超時 + randomized timeout |

**Paxos 是異類**：純 Paxos 沒有「leader」概念，任何節點都能對任何位置提案。Multi-Paxos 才引入一個「distinguished proposer」當事實上的 leader 來省掉每次 phase 1 的開銷——但這是優化，不是協定本身的一部分，論文沒明說怎麼選這個 leader。**這正是 Paxos「難以直接實作」的根源**：最關鍵的 leader 選舉，論文留白給你自己填。VR/Zab/Raft 都把強 leader 做成協定的一等公民、明確定義選舉，這是它們比 Paxos 好實作的核心原因。

### log 結構

| | Paxos | VR / Zab / Raft |
|---|---|---|
| log 允許有洞嗎 | **允許**（每個位置獨立決議，index 5 可以先於 index 3 chosen） | **不允許**（log 連續、無洞） |
| 好處 | 高並行——不同位置可並行決議 | 好推理——「有 index N ⟹ 有 1..N 全部」 |
| 壞處 | 難推理、apply 前要等填洞 | index 3 卡住，5/6/7 也得等 |

這是 Paxos 和「VR/Zab/Raft 三兄弟」最本質的分野。Paxos 的 log 可以有洞（各位置獨立），換來並行度，代價是狀態機 apply 前必須等洞填滿、且推理複雜。三兄弟強制 log 無洞（Log Matching，[Ch 21](./21-raft-log-replication.md)），犧牲一點並行，換來「看到 index N 就知道前面全有」的強不變量——好推理、好實作。**這個取捨是「理論優雅 vs 工程好用」的縮影。**

### 崩潰恢復 / leader 更替

這是四者差異最大、也最能看出設計哲學的地方。

- **Paxos**：新 proposer 選一個更大的 ballot，跑 phase 1（prepare）——向多數 acceptor 詢問「你們接受過的最高提案是什麼」，**必須沿用**回收到的最高值。這保證不覆蓋已 chosen 的值。恢復和正常運作是**同一套機制**（都是 prepare + accept），沒有獨立的「恢復模式」。優雅，但也因此難懂。

- **VR**：明確的 **view change** 協定。primary 疑似掛，replica 們跑一個獨立的 view change 子協定：湊多數、交換各自的 log、選出 log 最全的當新 primary、同步狀態。**恢復是獨立的一套流程**，和正常複製分開。這讓 VR 的每個模式都清晰——Raft 的 leader election 就是這個的後裔。

- **Zab**：兩階段恢復——**discovery**（新 leader 發現多數的最新狀態、確立新 epoch）+ **synchronization**（把多數同步到一致）。特別強調 **FIFO 順序**：同一個 client 的請求嚴格按送出順序處理（ZooKeeper 的 API 語意依賴這個）。恢復時要小心保住這個 FIFO 保證。

- **Raft**：**選舉限制 + 只 commit 當前 term**（[Ch 22](./22-raft-safety.md)）。新 leader 靠「log 夠新才當選」保證擁有所有 committed entry，不需要像 VR 那樣顯式交換 log 選最全的——**Raft 把「選最全的 log」內建進投票規則裡**，這是它比 VR 更精簡的地方。

```
   恢復哲學光譜：
   Paxos ──── 恢復=正常運作（統一機制，難懂）
   VR ─────── 顯式 view change 交換 log 選最全的
   Zab ────── 顯式 discovery + sync，保 FIFO
   Raft ───── 選舉限制內建「選最全」，統一進投票（精簡）
```

### 成員變更

| | Paxos | VR | Zab | Raft |
|---|---|---|---|---|
| 機制 | 把配置也當一個決議值（reconfiguration 是特殊 decree） | view change 時可換成員 | reconfiguration 協定 | joint consensus / single-server change（[Ch 23](./23-raft-membership-snapshot.md)） |
| 成熟度 | 論文有述，實作各異 | 有述 | ZooKeeper 3.5+ 支援 dynamic | Raft 講得最清楚、最多實作 |

四者都能變更成員，核心難點一樣（多數定義會變、重疊期腦裂風險）。Raft 因為把它拆成 joint consensus / single-server change 講得最透，反而成了業界學習成員變更的標準教材。

## 實戰：怎麼看穿一個陌生共識協定

對照表和逐面向比較的真正用處，是給你一套**拆解任何新協定的框架**。下次你翻開一篇沒見過的共識論文（或某資料庫的複製設計文件），別從頭讀到尾——按這五個問題快速定位它「本質上是哪個家族的變體」：

```
   1. 有沒有固定 leader？
        有 ── 往 VR/Zab/Raft 那邊想（強 leader 家族）
        無/選擇性 ── 往 Paxos/EPaxos 想

   2. 它的「邏輯時間戳」叫什麼？
        term/view/epoch/ballot ── 這就是它排序 leader 更迭的鑰匙，先抓它

   3. log 允許有洞嗎？
        無洞 ── 用了 Log Matching 類的不變量，好推理
        有洞 ── Paxos 血統，並行度換複雜度

   4. leader 崩潰後怎麼恢復？
        內建進投票（選最全 log）── Raft 式
        顯式交換 log 選 primary ── VR 式
        discovery+sync 保序 ── Zab 式
        prepare 沿用最高提案 ── Paxos 式

   5. quorum 是純多數，還是可調（Q1/Q2 分離）？
        可調 ── 有 Flexible Paxos 的影子，注意它的讀寫延遲取捨
```

舉個實例：你去讀 **MongoDB 的複製協定（Raft-like，稱 pv1）**——問這五題會發現它有固定 primary、用 term、log 無洞、選舉限制內建、純多數 quorum——**它就是換皮的 Raft**，你已經懂它了。再讀 **Kafka 的 KRaft**（Ch 40 會碰）——同樣一套框架，你會發現它是 Raft 的一個裁剪變體。這套五問法讓你「讀一個共識協定」從幾天縮到幾小時。

## EPaxos：拿掉 leader，用衝突圖

前面四個都（至少實務上）依賴單一 leader，寫入吞吐卡在 leader。**Egalitarian Paxos (EPaxos)** 走另一條路：**沒有固定 leader，任何節點都能提交命令。**

核心洞見：**不相衝突的命令，順序無所謂。** `set x=1` 和 `set y=2` 誰先誰後不影響最終狀態（它們操作不同 key），只有衝突的命令（都碰 x）才需要定序。EPaxos 追蹤命令間的**依賴關係**，構成一張**衝突圖（conflict graph）**：

```
   命令進來時，提交者附上「我依賴哪些先前命令」
   → 建一張有向圖，邊 = 依賴
   → 執行時對圖做拓撲排序，無依賴的可並行、有環的按確定規則定序

   set x=1  ──依賴──> set x=2   （都碰 x，要定序）
   set y=5              （不碰 x，跟上面無依賴，可並行）
```

好處：**無衝突時只要一輪 RTT（fast path），無 leader 瓶頸，寫入可分散到所有節點、且離 client 近的節點延遲低**（地理分散場景大勝）。代價：實作複雜得多（依賴追蹤、圖上的環處理、慢路徑），衝突多時退化。EPaxos 在學界很受推崇，工程落地少（複雜度勸退），但它代表了「共識能不能不要 leader 瓶頸」這個方向的重要探索。

## Flexible Paxos：quorum 不必是多數

一個 2016 年的漂亮結果，**顛覆了「共識必須用多數 quorum」的直覺**。

回想共識為什麼要多數：因為 leader 選舉的 quorum（Q1）和複製的 quorum（Q2）**必須相交**，才能保證新 leader 看得到舊 leader 已提交的值。多數是「保證任兩個 quorum 相交」最簡單的方式（`⌈n/2⌉+1`，任兩個必相交）。

**Flexible Paxos 的洞見：真正需要的不是「多數」，而是「Q1 和 Q2 相交」（Q1 ∩ Q2 ≠ ∅）——但 Q1 和 Q2 彼此不必相交，也不必各自是多數。**

```
   傳統：Q1 = Q2 = 多數（⌈n/2⌉+1），保證任兩個相交
   Flexible Paxos：只要 |Q1| + |Q2| > n 就保證 Q1∩Q2≠∅

   例：n=5，選 Q1=4（選舉要 4 個）、Q2=2（複製只要 2 個）
      4 + 2 = 6 > 5  ✓ 相交保證成立
      → 正常複製只要 2 個節點確認！寫入更快
      → 代價：選舉要 4 個節點（選舉變貴、容錯變差）
```

這打開了一個**調參空間**：如果你的系統「寫入頻繁、leader 很少換」，可以把 Q2（複製 quorum）調小、Q1（選舉 quorum）調大——**用「選舉變貴」換「每次寫入變快」**。反之亦然。多數只是 Q1=Q2 的那個特例。這個結果讓共識的 quorum 設計從「固定多數」變成「可依 workload 調的旋鈕」，影響了後來一批系統的設計（如 WPaxos、某些地理分散的 Raft 變體調整讀寫 quorum）。

## 對比與取捨（總表）

| 面向 | Multi-Paxos | VR | Zab | Raft |
|---|---|---|---|---|
| 年代 | 1990/1998 | **1988（最早）** | 2008 | 2014 |
| leader | 選擇性 | 強制 primary | 強制 leader | 強制 leader |
| log 有洞 | 允許 | 不允許 | 不允許 | 不允許 |
| 恢復 | 同正常機制 | 顯式 view change | discovery+sync (FIFO) | 選舉限制內建 |
| 特色 | 理論本質 | 完整系統先驅 | FIFO / primary-backup | 可理解性 |
| 代表系統 | Chubby, Spanner | (學界，影響 Raft) | ZooKeeper | etcd, TiKV, Consul |
| 可理解性 | 難 | 中 | 中 | **高** |

| 進階變體 | 核心創新 | 買到什麼 | 代價 |
|---|---|---|---|
| EPaxos | 無 leader + 衝突圖 | 無 leader 瓶頸、低延遲 | 實作複雜、衝突多時退化 |
| Flexible Paxos | Q1∩Q2≠∅ 即可 | quorum 可依 workload 調 | 調偏了容錯/延遲會失衡 |

**怎麼選？** 絕大多數場景選 **Raft**——好懂、實作成熟、生態最好。已經在 ZooKeeper 生態就繼續 **Zab**。要極致地理分散、能吃得下複雜度，看 **EPaxos** 或 **Flexible Paxos** 調參。純 Paxos 除非你在寫論文或維護 Chubby，否則沒理由從頭選它——它的價值是「理解共識本質」，不是「拿來實作」。

## 踩雷集錦

1. **錯誤直覺：「共識 = Raft」→ 正確：Raft 只是最好懂的一個，VR 比 Paxos 還早且更像 Raft**。把 Raft 當成共識的全部，會讓你看不懂 ZooKeeper 的 Zab、Spanner 的 Paxos。它們是同一問題的不同方言，術語對照表是你的解碼器。

2. **錯誤直覺：「Paxos 有明確的 leader 選舉」→ 正確：純 Paxos 沒有，Multi-Paxos 的 leader 是優化且論文留白**。Paxos「難實作」的頭號原因就是它沒把 leader 選舉講清楚，留給你自己填。VR/Zab/Raft 把選舉做成一等公民，這才是它們好實作的關鍵。

3. **錯誤直覺：「log 有洞是缺陷」→ 正確：那是 Paxos 換並行度的刻意取捨**。Paxos 允許 log 有洞是為了讓不同位置並行決議。它不是 bug，是「理論並行度 vs 工程好推理」的權衡。三兄弟選了無洞（好推理），Paxos 選了有洞（高並行），各有代價。

4. **錯誤直覺：「多數 quorum 是共識的鐵律」→ 正確：真正的鐵律是『選舉 quorum 和複製 quorum 相交』，多數只是最簡單的達成方式**。Flexible Paxos 證明了 `|Q1|+|Q2|>n` 就夠，你可以用非多數的 quorum 換取讀寫延遲的調整空間。別把「多數」和「相交需求」劃等號。

5. **錯誤直覺：「無 leader（EPaxos）一定更好，沒有瓶頸」→ 正確：只在衝突少時更好，衝突多會退化、且實作複雜度暴增**。EPaxos 的 fast path 要求命令不衝突。衝突多時它退化到慢路徑、比 Raft 還慢，加上依賴圖的實作極其複雜——這是它學界火、工程冷的原因。沒有免費的午餐。

## 進階：再往深一層

- **HotStuff 與 BFT 家族的接軌**：本章比的都是 crash-fault-tolerant（CFT）共識——假設節點只會當機、不會說謊。Part 5（Ch 32~34）會進入 **Byzantine-fault-tolerant（BFT）** 世界，節點可能惡意。有趣的是，現代 BFT 協定 **HotStuff** 的結構和 Raft 驚人地像（leader-based、view change、三階段 commit），可以看成「Raft 的拜占庭版」。學完 Raft 再看 HotStuff 會很有感。

- **Multi-Paxos 的 leader lease 讀**：Paxos 系統（如 Spanner）怎麼做線性一致讀，和 Raft 的 ReadIndex（Ch 26）思路一致但實作不同，值得對照。Spanner 還額外用 TrueTime（原子鐘 + GPS）把時間不確定性界定住做外部一致性——這是共識之上再疊一層，Ch 39 專講。

- **為什麼 Raft 贏了工程界**：技術上 Raft 沒比 VR/Paxos 強，但它贏在**可理解性帶來的生態效應**——好懂 ⟹ 好實作 ⟹ 多人實作 ⟹ 生態成熟 ⟹ 更多人選它。這是一個「技術決策裡『人的因素』勝過『純技術優劣』」的經典案例，值得每個做系統設計的人記住。

- **形式化驗證的統一視角**：VR、Zab、Raft、Paxos 都有 TLA+ 形式化規格。把它們並排看，你會發現核心 safety invariant（不覆蓋已 chosen/committed 的值）幾乎一模一樣——這從形式邏輯層面證明了「它們是同一個東西」。進階讀者可以找這些規格對照。

## 本章重點整理

- 共識家族四大成員：**Multi-Paxos（理論本質）、VR（最早的完整系統、Raft 前身）、Zab（ZooKeeper 用、FIFO+primary-backup）、Raft（可理解性優先）**。它們是同一問題的不同方言。
- **術語對照表**是解碼器：ballot / view / epoch / **term** 是同一個「邏輯時間」，proposer / primary / leader 是同一個「當家節點」。
- 最本質的分野：**Paxos 允許 log 有洞（換並行度、難推理）；VR/Zab/Raft 強制 log 無洞（好推理、好實作）**。
- **恢復哲學**：Paxos 恢復即正常運作（統一難懂）；VR 顯式 view change 交換 log；Zab discovery+sync 保 FIFO；Raft 把「選最全 log」內建進投票規則（最精簡）。
- **EPaxos**：無 leader + 衝突圖，無瓶頸低延遲，但衝突多時退化、實作複雜。
- **Flexible Paxos**：真正需要的是 Q1∩Q2≠∅（`|Q1|+|Q2|>n`），多數只是特例——quorum 變成可依 workload 調的旋鈕。
- 工程首選 **Raft**：技術沒更強，但可理解性帶來的生態效應讓它成為事實標準。

## 自我檢核

- [ ] 我能默寫術語對照表：把 ballot / view / epoch 對映到 term，proposer / primary 對映到 leader
- [ ] 我能解釋為什麼「Paxos 沒有明確 leader 選舉」是它難實作的核心原因
- [ ] 我能說出 Paxos 允許 log 有洞 vs 三兄弟強制無洞，各自換到什麼、付出什麼
- [ ] 我能對照 Paxos / VR / Zab / Raft 四者的崩潰恢復機制差異
- [ ] 我能解釋 EPaxos 用衝突圖做到無 leader，以及它在什麼情況下反而更慢
- [ ] 我能陳述 Flexible Paxos 的核心結論（`|Q1|+|Q2|>n`），並舉例說明它怎麼換讀寫延遲
- [ ] 我能說出為什麼 Raft 在工程界勝出，即使技術上不比其他強

## 延伸閱讀

- **[Viewstamped Replication Revisited](https://pmg.csail.mit.edu/papers/vr-revisited.pdf)** — Liskov & Cowling, MIT-CSAIL-TR 2012
  - **讀哪裡**：整篇不長，view change 那節與 Raft 的 leader election 對照著讀
  - **為什麼值得讀**：親眼確認「Raft ≈ VR」。讀完你對「Raft 是不是原創」會有更成熟的看法，也更懂 view change 的本質

- **[Paxos vs Raft: Have we reached consensus on distributed consensus?](https://arxiv.org/abs/2004.05074)** — Howard & Mortier, PaPoC 2020
  - **讀哪裡**：整篇。作者逐點論證 Multi-Paxos 和 Raft 的異同，結論是「差別比想像小」
  - **為什麼值得讀**：Heidi Howard 也是 Flexible Paxos 作者，這篇是「四者本質相同」最權威的學術論證

- **[ZooKeeper's atomic broadcast protocol: Theory and practice (Zab)](https://marcoserafini.github.io/papers/zab.pdf)** — Junqueira et al.
  - **讀哪裡**：Zab 的 discovery/sync 兩階段恢復、FIFO 保證那幾節
  - **學什麼**：Zab 為什麼不直接用 Paxos、FIFO 保證怎麼影響設計。理解你手上真在用的 ZooKeeper

- **[There Is More Consensus in Egalitarian Parliaments (EPaxos)](https://www.cs.cmu.edu/~dga/papers/epaxos-sosp2013.pdf)** — Moraru et al., SOSP 2013
  - **讀哪裡**：衝突圖與 fast/slow path 那節、依賴追蹤機制
  - **前提**：先熟 Multi-Paxos。這是無 leader 共識的代表作，難但值得

- **[Flexible Paxos: Quorum intersection revisited](https://arxiv.org/abs/1608.06696)** — Howard, Malkhi, Spiegelman, 2016
  - **讀哪裡**：核心定理（Q1∩Q2≠∅ 即可）與例子那幾頁
  - **為什麼值得讀**：短、漂亮、顛覆直覺。讀完你對「quorum 為什麼是多數」的理解會升級

Part 3 到此完整——從共識問題定義、FLP、Paxos，到 Raft 四章、再到共識家族全景。你現在有了共識這塊硬地基。但讀懂協定和寫得出來是兩回事：接下來的練習 C 要你在 Ch 0 的模擬器上**親手刻一個能過 crash/partition 測試的 Raft**。刻完再進 Part 4，把共識引擎組裝成真實系統。

→ [練習 C：手刻 Raft](./practice-c-build-raft.md)
