# Ch 22 — Raft ③：Safety 與選舉限制

> **目標**：這章是 Raft 正確性的核心，也是全課最需要嚴謹的一章。我們要證明一件最要命的事——**已經回覆 client「成功」的 entry 永遠不會丟、不會被覆蓋**。這靠兩條 safety 規則：**選舉限制（Election Restriction）**（只有 log 夠新的候選人能當選）與 **只 commit 當前 term 的 entry**（不能靠計數 commit 舊 term entry）。我們逐步走一遍 **Figure 8**，看清楚少了任一條、已 commit 的資料會怎麼被覆蓋。最後給 Leader Completeness 與 State Machine Safety 的證明直覺。

> **環境**：Go 1.18.1, WSL2/Linux x86-64。選舉限制部分在 `dsim` 真跑；Figure 8 的反例情境是精心構造的時序，本章明確標注「理論預期」的部分。

## 為什麼需要這個？

Ch 20 選出了 leader，Ch 21 複製了 log。看起來 Raft 已經能動了——那為什麼還要一整章談 safety？

因為**「能動」和「永遠不出錯」之間隔著一條深淵**，而這條深淵只在極端時序下才現形：leader 頻繁更迭、網路分區反覆切換、訊息任意延遲。在這些魔鬼時序裡，一個天真的 Raft 會做出災難性的事——**把一個已經回覆 client「成功」、client 以為板上釘釘的 entry，覆蓋掉、弄丟**。

這不是假想。這正是 Raft 論文用了整個 §5.4 加一張 Figure 8 來處理的問題，也是 Ongaro 在博士論文裡花最多篇幅、用 TLA+ 機器驗證的部分。**共識演算法 99% 的難度都在這 1% 的時序裡**。Paxos 難懂、Raft 好懂，差別很大程度就在「Raft 把這些 safety 論證攤開講清楚」。

分散式系統的第一鐵律：**safety 永遠不能違反，liveness 可以暫時犧牲**。寧可暫時選不出 leader（系統卡住、不可用），也**絕不能**丟掉已提交的資料或選出兩個 leader。這章講的就是「怎麼在最壞的時序下也守住 safety」。

## 先建立直覺：committed 的定義與威脅

先把「committed」的定義釘死。一個 entry 是 committed，意思是：**leader 已確認它被存在多數節點的 log 裡，於是 leader 敢 apply 它、回覆 client 成功。**

Raft 要保證的最終性質叫 **State Machine Safety**：

> 若某個節點已經把某個 index 的某個 entry apply 到狀態機了，那麼**沒有任何其他節點**會在同一個 index apply 一個**不同**的 entry。

換句話說，所有節點的狀態機看到的是**同一串命令、同一個順序**。這就是線性一致的地基。

威脅來自哪裡？來自**leader 更迭**。新 leader 是靠選舉上來的，它的 log 可能不是最全的。如果 Raft 允許一個「log 缺了某些已 commit entry」的節點當選 leader，這個新 leader 會用它殘缺的 log 去覆蓋別人——**已 commit 的 entry 就這樣蒸發了**。

```
       危險的世界（沒有 safety 規則）
   ┌──────────────────────────────────────────┐
   │ term 2: leader n2 把 entry E 複製到多數    │
   │         → E committed → 回覆 client 成功    │
   │                                            │
   │ term 3: n0（它的 log 剛好沒有 E！）當選     │
   │         → n0 用自己的 log 覆蓋大家          │
   │         → E 被抹掉                          │
   │         → client 被騙了：它以為成功的寫入不見了│
   └──────────────────────────────────────────┘
```

要堵死這個，Raft 從兩個方向下手：**(A) 不讓「缺 committed entry」的節點當選**（選舉限制），以及 **(B) 對「什麼時候能宣告 commit」加限制**（只 commit 當前 term）。兩條缺一不可，下面分別看。

## 規則一：選舉限制（Election Restriction）

**核心規則：候選人的 log 必須「至少和收到投票請求的節點一樣新」，該節點才投票給它。**

「一樣新」怎麼比？Raft 用 (lastLogTerm, lastLogIndex) 這個 pair 做字典序比較：

1. 先比 **lastLogTerm**：term 大的比較新（它跟過更晚的 leader）。
2. term 相同才比 **lastLogIndex**：index 大的比較新（它的 log 比較長）。

```go
// 選舉限制：候選人 log 至少和我一樣新，才給票
func (r *Raft) candidateLogOK(lastTerm, lastIndex int) bool {
    myTerm, myIndex := r.lastLogTerm(), r.lastLogIndex()
    if lastTerm != myTerm {
        return lastTerm > myTerm      // 先比最後一格的 term
    }
    return lastIndex >= myIndex        // term 平手才比長度
}
```

**為什麼比 term 優先於比 index？** 這是最容易搞錯的地方。一個 log 可能**更長但更舊**——它尾巴掛著一堆某個短命 leader 塞進去、從沒被 commit 的 entry。那些 entry 注定要被覆蓋，長度長不代表資訊新。真正代表「新」的是**最後一格的 term**——它反映「這個節點跟過的最晚的 leader 是誰」。term 越大，越接近叢集的真實共識。所以 term 優先。

**這條規則為什麼能保住已 commit 的 entry？** 直覺論證：

- 一個 entry E 被 commit ⟹ E 存在於**多數**節點的 log 裡（committed 的定義）。
- 任何候選人要當選 ⟹ 需要**多數**節點投它票。
- 兩個多數集合**必定相交**（鴿籠原理：兩個過半集合不可能不相交）。
- 所以候選人的票倉裡，**至少有一個節點擁有 E**。
- 那個節點會用選舉限制檢查候選人：「你的 log 有比我新嗎？」候選人若缺 E，它的 log 在 E 那個位置之後就短了、或 term 舊了——**過不了那個持有 E 的節點的檢查，拿不到那張票**。
- 拿不到那張關鍵票 ⟹ 湊不到多數 ⟹ **選不上**。

結論：**任何缺少已 commit entry 的節點，不可能當選 leader。** 於是新 leader 一定擁有所有已 commit 的 entry，它去覆蓋別人時，絕不會覆蓋掉 committed 的東西。這就是 **Leader Completeness Property**——後面會再收緊成正式陳述。

### 真跑：log 太舊的候選人選不上

`dsim` 上驗證（WSL, Go 1.18.1，seed 固定）：

```
=== 選舉限制：log 太舊的候選人選不上 ===
leader=n2，先 commit 三筆讓多數 log 變新
  n0 log(term)=[1 1 1] commit=3
  n1 log(term)=[1 1 1] commit=3
  n2 log(term)=[1 1 1] commit=3

人工把 n0 的 log 砍短成 log(term)=[1] commit=1（模擬長期落後）
>>> 讓 n0 發起選舉（term 會很高，但 log 舊）
結果：n0 role=follower（若 log 舊，其他人不投票，選不上）
n0 當上 leader 了嗎？ false

=== 對照：log 夠新的候選人正常當選 ===
log 完整的 n0 發起選舉：role=leader（true）
```

我們把 n0 的 log 人工砍成只剩 1 筆（模擬它長期落後、缺了已 commit 的 entry），然後**強迫它發起選舉**。即使它的 term 很高，n1 和 n2（log 完整）用選舉限制一檢查——「你 log 沒我新」——**拒絕投票**。n0 湊不到多數，選舉失敗、退回 follower，**沒當上 leader**。對照組裡 log 完整的候選人則順利當選。這就是選舉限制在守門：**它把「會弄丟資料的候選人」擋在門外。**

## 規則二：只 commit 當前 term 的 entry

選舉限制保證「新 leader 擁有所有 committed entry」。但這裡藏著一個更陰險的問題：**leader 怎麼判斷一個舊 term 的 entry 已經 committed？**

天真的想法（Ch 21 那行 `if r.log[idx].Term != r.currentTerm { continue }` 擋掉的正是它）：「這個 entry 被多數節點複製了，那它就 committed 了吧？」——**這在跨 term 時是錯的，而且錯得會弄丟資料。**

問題在於：一個**舊 term 的 entry，即使現在被多數複製，也還可能被之後的 leader 覆蓋。** 為什麼？因為那個舊 term 的 entry 沒被它自己那個 term 的 leader commit（那個 leader 早掛了），它是被**現在這個新 leader** 從某個 follower 那複製開的。而在它「剛好被多數複製」的那個瞬間、新 leader 還沒把自己 term 的東西壓上去之前，另一個候選人可能帶著「更高 term 但缺這個 entry」的 log 選上，然後覆蓋它。

Raft 的解法乾脆俐落：**leader 只能透過「commit 一個自己當前 term 的 entry」來間接 commit 它前面的所有 entry**（靠 Log Matching，commit 當前 term 的 entry 時，它前面的舊 entry 都被連帶 commit 了）。**leader 永遠不直接靠「被多數複製」來 commit 一個舊 term 的孤立 entry。**

```go
func (r *Raft) advanceCommit(net *Net) {
    for idx := r.lastLogIndex(); idx > r.commitIndex; idx-- {
        if r.log[idx].Term != r.currentTerm {
            continue                 // ★ 舊 term 的 entry：不直接 commit
        }
        count := 1
        for _, p := range r.peers {
            if r.matchIndex[p] >= idx { count++ }
        }
        if count >= r.majority() {
            r.commitIndex = idx      // 只在「當前 term 的 entry」過半時推進
            // 一旦推進到 idx，它前面所有更舊的 entry 也被連帶 commit（Log Matching）
            break
        }
    }
}
```

這行 `continue` 看似保守到多餘，卻是 Raft safety 的命根子。**為什麼必要？看 Figure 8 就懂了。**

## Figure 8：逐步走一遍那個致命時序

這是 Raft 論文最有名的一張圖。我們用五個時刻，走一遍「若沒有規則二會發生什麼」。五個節點 S1~S5。

**(a) term 2：S1 是 leader，把 index=2 的 entry（term 2，記作 `2`）複製到 S1、S2。**

```
     idx:  1    2
   S1 (L) [1] [2]      ← S1 是 term 2 的 leader
   S2     [1] [2]      ← 複製到 S2
   S3     [1]
   S4     [1]
   S5     [1]
```

此時 index=2 的 `2` 只在 S1、S2（兩個節點，**還沒過半**，5 個節點需要 3 個）。**還沒 committed。**

**(b) term 3：S1 掛了。S5 靠 S3、S4、S5 的票當選 term 3 的 leader，在 index=2 寫了自己的 entry（term 3，記作 `3`）。**

```
     idx:  1    2
   S1     [1] [2]      ← 掛了（虛線）
   S2     [1] [2]
   S3     [1]
   S4     [1]
   S5 (L) [1] [3]      ← S5 是 term 3 leader，index=2 是它自己的 `3`
```

注意 S5 為什麼能當選：它 log 是 `[1]`，S3/S4 也是 `[1]`，選舉限制下 S5 拿得到 S3/S4 的票（一樣新）。此時 index=2 出現**分歧**：S1/S2 是 `2`，S5 是 `3`。

**(c) term 4：S5 又掛了。S1 重新當選（term 4），繼續把它的 index=2 的 `2` 複製給更多節點。**

```
     idx:  1    2    3
   S1 (L) [1] [2] [4]   ← S1 term 4 leader，複製 index=2 的 `2` 到多數，並在 index=3 寫 `4`
   S2     [1] [2]
   S3     [1] [2]       ← index=2 的 `2` 現在被複製到 S1/S2/S3 = 過半！
   S4     [1]
   S5     [1] [3]       ← 掛了
```

**這是關鍵時刻。** index=2 的 `2`（term 2 的舊 entry）現在被 S1/S2/S3 三個節點持有——**過半了**。

天真的 Raft（沒有規則二）會想：「`2` 被多數複製了，commit 它！」——**於是 S1 回覆某個 client『index=2 的操作成功了』。** 記住這句。

**(d) term 5：S1 又掛了。S5 靠選舉限制再次當選（term 5）——注意它做得到！**

```
     idx:  1    2
   S5 (L) [1] [3]      ← S5 的 log 尾巴是 term 3，比 S2/S3 的尾巴 term 2 新！
   S2     [1] [2]
   S3     [1] [2]
   S4     [1] [3]      ← S5 把自己的 `3` 複製出去
   S1     [1] [2] [4]  ← 掛了
```

S5 的 lastLogTerm=3，S2/S3 的 lastLogTerm=2。選舉限制比 term——**3 > 2，S5 更新**——S5 拿得到 S2/S3/S4 的票，當選 term 5 leader。然後它用自己 index=2 的 `3` **覆蓋掉 S2/S3 的 `2`**。

**災難發生了：剛才在 (c) 被「commit」、回覆過 client 成功的 `2`，現在被 `3` 覆蓋、永遠消失了。** State Machine Safety 破了。client 被騙了。

```
   (c) 天真 Raft 說「2 committed」→ 回覆 client 成功
   (d) 2 被 3 覆蓋 → client 的成功寫入蒸發
   ═══════════════════════════════════════════════
   結論：不能因為「舊 term entry 被多數複製」就 commit 它
```

**規則二怎麼堵死這個？** 在 (c)，`2` 是 term 2 的 entry，但當前 leader 是 term 4。規則二說：**leader 不准直接 commit 非當前 term 的 entry**。所以在 (c)，S1 **不會** commit `2`，也就**不會**回覆 client 成功。`2` 要等到 S1 在 index=3 寫的 `4`（當前 term 4 的 entry）被多數複製、S1 commit 了 `4`，才「連帶」把它前面的 `2` 一起 commit（靠 Log Matching）。而一旦 `4` 被多數複製並 commit，選舉限制就保證 S5 再也選不上了（S5 缺 `4`，log 不夠新）——`2` 就安全了。

**兩條規則是配套的**：選舉限制擋住「缺 committed entry 的候選人」，但它需要「什麼算 committed」的定義夠嚴格——規則二就是把 commit 的定義收緊，讓「committed」這個標籤只貼在真正安全的 entry 上。少了規則二，選舉限制守護的是一個被污染的 committed 定義，照樣出事。

> **這段 Figure 8 是精心構造的時序，屬「理論預期」**。在我們的 `dsim` 上要精準重現 (a)~(d) 這串「特定節點在特定時刻當選/掛掉」的編排，需要對每一步做手術式的 crash/partition/強制選舉控制，能做但可讀性差、且容易變成「為了跑而跑」。這裡我們選擇把時序講透（對照論文 Figure 8 逐格核對過），而把可乾淨真跑的「選舉限制」部分留在上面真跑了。這是誠實的取捨：**反例情境理論推演，正例機制實測。**

## 底層機制：五大性質怎麼環環相扣

Raft 論文 Figure 3 列了五條性質，它們像骨牌一樣互相支撐。理解它們的依賴關係，就理解了整個 safety 論證：

```
Election Safety（一 term 一 leader）
   └─靠─> 每 term 一票 + 多數決（Ch 20）

Leader Append-Only（leader 從不刪改自己的 log，只 append）
   └─靠─> 強 leader 設計

Log Matching（同 index+term ⟹ 前綴全同）（Ch 21）
   └─靠─> 一致性檢查 + (term,index) 唯一對應 command

Leader Completeness（新 leader 擁有所有已 commit entry）
   └─靠─> 選舉限制 + 只 commit 當前 term  ← 這章兩條規則
   └─用到─> 多數集合必相交

State Machine Safety（同 index apply 同 entry）  ← 最終目標
   └─靠─> Leader Completeness + Log Matching
```

**Leader Completeness** 是承上啟下的樞紐，正式陳述：

> 若一個 entry 在 term T 被 commit，則它會出現在所有 term > T 的 leader 的 log 裡。

證明骨架（反證法，論文 §5.4.3 的精華）：假設某個 committed 的 entry E（term T 被 commit）不在某個後續 leader 的 log 裡。取 T 之後**第一個**缺 E 的 leader，設它是 term U 的 leader L_U。

- E 被 commit ⟹ E 在多數節點的 log 裡（term T 時）。
- L_U 當選 ⟹ 多數投它。兩個多數相交 ⟹ 存在一個節點 voter，它既有 E，又投給了 L_U。
- voter 投給 L_U ⟹ 選舉限制通過 ⟹ L_U 的 log 至少和 voter 一樣新。
- 但 L_U 缺 E，voter 有 E……推下去會得出 L_U 的 lastLogTerm 必須 ≥ E 的 term，且沿著 Log Matching 一路推，最終導出 L_U 其實**必須**擁有 E——與假設矛盾。

（完整證明要仔細處理「voter 的 log 在 E 之後是否還有更新的 entry」等情況，論文 §5.4.3 有完整版；這裡給的是骨架直覺。**規則二在證明裡的作用**：它保證「被 commit 的 E 的 term 就是當時 leader 的 term」，讓上面的 term 比較能成立，否則 Figure 8 的漏洞會讓這個反證法本身失效。）

一旦有了 Leader Completeness，**State Machine Safety** 幾乎白送：所有 leader 都有全部 committed entry，Log Matching 保證同 index 同 term ⟹ 同 command，所以沒有兩個節點會在同一 index apply 不同 entry。

## 對比與取捨

| 若拿掉這條規則 | 會發生什麼 |
|---|---|
| 拿掉**選舉限制** | 缺 committed entry 的節點能當選，用殘缺 log 覆蓋大家 → **丟已提交資料** |
| 拿掉**只 commit 當前 term** | Figure 8：舊 term entry「被多數複製」就 commit，之後被覆蓋 → **丟已提交資料** |
| 兩條都有 | Leader Completeness 成立 → State Machine Safety 成立 → **已 commit 永不丟** |
| **代價** | liveness 打折：極端時序下可能要多幾輪選舉才收斂（但 safety 絕不違反） |

這張表就是這章的全部：**兩條規則各堵一個丟資料的洞，缺一不可，代價是 liveness 的機率性延遲**。這完全符合分散式第一鐵律——寧可慢，不可錯。

## 踩雷集錦

1. **錯誤直覺：「log 比較長 = 比較新」→ 正確：先比 lastLogTerm，term 大才新**。長 log 可能尾巴掛著一堆未 commit、注定被覆蓋的 term 舊 entry。選舉限制**先比最後一格的 term**，term 平手才比長度。搞反這個順序，你的 Raft 會讓錯的候選人當選，Figure 8 立刻復現。

2. **錯誤直覺：「entry 被多數複製 = committed」→ 正確：跨 term 不成立**。只有**當前 term** 的 entry 被多數複製才能直接 commit。舊 term 的 entry 要靠「commit 一個當前 term 的 entry」連帶 commit。這是整章最反直覺、也最重要的一點。

3. **錯誤直覺：「commit 定義搞對就好，選舉限制是額外保險」→ 正確：兩條是配套，缺一必出事**。選舉限制守護的「committed」必須是規則二定義的那個嚴格版本。單有選舉限制、commit 定義卻鬆（允許 commit 舊 term entry），Figure 8 照樣弄丟資料。它們不是主副關係，是聯立方程式。

4. **錯誤直覺：「safety 和 liveness 可以都保證」→ 正確：FLP 說不行，Raft 選擇犧牲 liveness**。Raft 的兩條 safety 規則有時讓叢集「明明有節點想當 leader 卻選不上」（它 log 不夠新），系統暫時卡住。這**不是 bug**，是刻意的——寧可暫時不可用，也不丟資料。理解這個，你才不會把「Raft 有時要選好幾輪」當成缺陷。

5. **錯誤直覺：「新 leader 上任要主動同步、修復舊 term 的 entry」→ 正確：新 leader 絕不主動 commit 舊 entry，只透過複製自己 term 的新 entry 帶著 commit**。有些人會想「新 leader 一上任，先把之前疑似 committed 的都補 commit 一遍」——這正是 Figure 8 的陷阱。正確做法是新 leader 儘快提出一個自己 term 的 entry（實務上是一個 no-op entry），一旦它被 commit，前面的舊 entry 就都安全連帶 commit 了。

## 進階：再往深一層

- **no-op entry 加速安全**：新 leader 剛上任時，前面可能堆著一批「疑似 committed 但因規則二還不能 commit」的舊 term entry。它們要等到有新 client 命令進來、被 commit 才連帶安全。為避免這個空窗拖太久，實務上新 leader 一當選就立刻提一個 **no-op（空操作）entry**，用它當「當前 term 的 entry」儘快把前面的舊 entry 帶進 committed。etcd/TiKV 都這麼做。

- **TLA+ 形式化驗證**：這章的論證再嚴謹，人腦還是會漏掉時序。Ongaro 用 **TLA+** 把 Raft 的 safety 性質寫成形式規格，用 TLC model checker 窮舉小規模狀態空間，機器驗證了 Log Matching / Leader Completeness / State Machine Safety。規格在 [raft.github.io](https://raft.github.io) 有連結。這是「證明分散式協定正確」的業界標準做法，值得專門學（本課 Ch 43 會碰）。

- **和 Paxos 的 safety 對照**：Paxos 的 safety 靠「proposer 選 proposal number 前要先讀取多數已接受的最高提案，並沿用其值」——本質也是「多數相交 + 不覆蓋已決議值」，只是 Paxos 把它藏在 phase 1 的 prepare 裡，不像 Raft 拆成兩條明確規則。理解 Raft 的選舉限制後，回頭看 Paxos 的 prepare 會豁然開朗：它們在防同一件事。

- **membership change 期間的 safety**：這章假設節點集合固定。當叢集加減節點時，「多數」的定義會變，兩條規則的推理會出現新漏洞（新舊多數可能不相交）——這是下一章 joint consensus 要處理的，safety 論證要重做一遍。

## 本章重點整理

- Raft safety 的最終目標是 **State Machine Safety**：所有節點在每個 index apply 同一個 entry ⟹ 已 commit 的資料永不丟、永不被覆蓋。
- **規則一 選舉限制**：候選人 log 必須至少和投票者一樣新（先比 lastLogTerm、平手比 lastLogIndex）才拿得到票。靠多數相交，保證缺 committed entry 的節點選不上。真跑驗證了 log 舊的候選人拿不到票。
- **規則二 只 commit 當前 term**：leader 不直接 commit 舊 term 的 entry，只透過 commit 一個當前 term 的 entry 連帶帶 commit 前面的。堵死 Figure 8 的覆蓋漏洞。
- **Figure 8** 逐步走一遍證明：少了規則二，一個「被多數複製過、被回覆成功」的舊 term entry 會被後來的 leader 覆蓋。兩條規則是配套、缺一必丟資料。
- 五大性質環環相扣：Election Safety + Log Matching + 選舉限制 + 只 commit 當前 term ⟹ **Leader Completeness** ⟹ **State Machine Safety**。
- 代價是 liveness 的機率性延遲——完全符合「寧可暫時卡住，絕不丟已提交資料」的第一鐵律。

## 自我檢核

- [ ] 我能解釋選舉限制為什麼「先比 term、平手才比 index」，而不是直接比 log 長度
- [ ] 我能用「兩個多數必相交」論證：缺 committed entry 的節點為什麼選不上 leader
- [ ] 不看圖，我能複述 Figure 8 的 (a)~(d)，指出天真 Raft 在哪一步弄丟了資料
- [ ] 我能解釋「只 commit 當前 term」為什麼必要，以及它怎麼和選舉限制配套堵住漏洞
- [ ] 我能說出 no-op entry 為什麼能加速讓舊 term entry 變安全
- [ ] 我能陳述 Leader Completeness 的正式定義，並複述它反證法證明的骨架
- [ ] 我能解釋為什麼「這章犧牲 liveness」不是缺陷而是刻意設計

## 延伸閱讀

- **[In Search of an Understandable Consensus Algorithm (Raft)](https://raft.github.io/raft.pdf)** — Ongaro & Ousterhout, USENIX ATC 2014
  - **讀哪裡**：§5.4（Safety）整節，Figure 8 與其配文逐字讀、§5.4.3 的 Leader Completeness 證明。這章就是它的精讀導讀
  - **為什麼值得讀**：Figure 8 是整個 Raft 最精妙的部分，論文的講法無可取代，本章對照它逐格核對

- **[Consensus: Bridging Theory and Practice](https://github.com/ongardie/dissertation)** — Diego Ongaro 博士論文（2014）
  - **讀哪裡**：Ch 3 的 safety 完整證明（比論文更詳盡）、附錄的 TLA+ 規格
  - **前提**：先讀完論文 §5.4，這裡是它的加長嚴謹版

- **[Raft TLA+ Specification](https://github.com/ongardie/raft.tla)** — Ongaro
  - **讀哪裡**：`raft.tla`，尤其 `LeaderCompleteness`、`StateMachineSafety` 這兩個 invariant 的定義
  - **學什麼**：safety 性質怎麼用形式邏輯精確表達、機器怎麼驗證。看不懂 TLA+ 語法也值得看 invariant 名字對應本章哪條性質

- **[Students' Guide to Raft](https://thesquareplanet.com/blog/students-guide-to-raft/)** — Jon Gjengset
  - **讀哪裡**：關於「committing entries from previous terms」那段（對應規則二）
  - **學什麼**：實作 6.824 lab 時這條規則最常被寫錯，作者用踩坑角度講為什麼、怎麼寫對

safety 論證假設了節點集合固定。但真實系統要能滾動升級、加減節點——一旦「多數」的定義會變，這章的 safety 推理就出現新漏洞。下一章看 Raft 怎麼用 joint consensus 安全地變更成員，順帶解決 log 無限成長的 snapshot 問題。

→ [Ch 23 Raft ④：Membership 與 Snapshot](./23-raft-membership-snapshot.md)
