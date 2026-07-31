# Ch 13 — EEVDF：為什麼 6.6 換掉 CFS

> **目標**：理解 6.6 起取代 CFS 的 **EEVDF（Earliest Eligible Virtual Deadline First，最早合格虛擬截止時間優先）**——它為什麼要換掉一個用了十五年的排程器、lag / eligibility / virtual deadline 三個核心概念在直覺上是什麼、以及它怎麼沿用 Ch 12 的 vruntime + 權重 + 紅黑樹，只把「挑誰」的規則從「vruntime 最小」換成「合格者中 virtual deadline 最早」。你 6.12（含你桌機/伺服器上跑的任何 6.6+ kernel）跑的 fair class 就是它。

## 為什麼需要這個？

Ch 12 的 CFS 把「公平」做得很漂亮：它保證**長期公平**——誰累積跑得少（vruntime 最小），下次就先跑，時間拉長看每個 task 拿到的 CPU 比例正好等於它的權重比例。這個性質很乾淨，也很好證明。

但「長期公平」對一整類 task 來說不夠好：**latency 敏感的 task**。想想這兩種：

- 一個音訊 thread：每隔幾毫秒醒來，把一小塊 buffer 填好就睡。它一次只要**很短**的 CPU 時間，但要求**很快**拿到——晚了就爆音（xrun）。
- 一個互動式 GUI thread：你點一下，它要在你察覺延遲之前（~幾十毫秒）回應。它同樣是「小片、但要快」。

CFS 對這種需求無能為力，因為 **CFS 的模型裡根本沒有「deadline」這個概念**。它只知道 vruntime。一個剛醒來的音訊 thread，它的 vruntime 可能比別人小（睡了很久沒累積），所以會被優先挑——這碰巧幫到它。但這是**副作用，不是保證**。當系統忙、一堆 task 的 vruntime 都差不多時，CFS 沒有任何機制能表達「我這個 task 願意少拿總量，但每次都要快」。

於是這些年 CFS 上長了一堆補丁想補這個洞：`sched_min_granularity`、`sched_wakeup_granularity`、`GENTLE_FAIR_SLEEPERS`、各種 wakeup preemption 的 heuristic、社群反覆提的 **latency-nice** 提案……每一個都是在「vruntime 最小者先跑」這個單一維度上硬塞 latency 語意，互相打架、難調、行為難預測。CFS 的作者 Peter Zijlstra 最後的結論是：與其繼續在 CFS 上貼補丁，不如換一個**理論上本來就有 deadline 概念**的演算法。

那個演算法就是 EEVDF——一篇 1995 年的論文（Stoica & Abdel-Wahab），比 CFS 還老。它的核心洞見是：**公平（誰該拿多少總量）和 latency（誰該多快拿到）是兩個獨立維度，應該分開表達**。CFS 只有一個旋鈕（vruntime / nice）；EEVDF 給你兩個：一個管**比例**（還是 nice/權重），一個管**時間片大小 / latency**（新的 request size）。6.6 把 fair class 的挑選演算法整個換成它，vruntime、權重、紅黑樹這些基礎設施（Ch 12）保留，只換「選誰」的規則。

換句話說，CFS 這十五年的困境可以一句話概括：**它想在「vruntime 最小者先跑」這一根軸上，同時塞下「公平」和「低延遲」兩件互相拉扯的事**。sleeper bonus 幫了 latency 卻損了公平的精確性；wakeup granularity 想壓抑過度搶佔卻讓互動變鈍——每個補丁都在同一根軸上按下葫蘆浮起瓢。EEVDF 的解法不是「調得更好」，而是**多給一根軸**：公平走 vruntime/lag 這根，latency 走 request size/deadline 那根，兩者正交，不再互相拖累。這是「換演算法」而非「再貼一個補丁」的根本理由。

## 先建立直覺

EEVDF 三個概念，用直覺講，別堆數學。想像一個「理想公平分配器」：它在每個瞬間，把 CPU 按權重比例**同時**分給所有 runnable task（像把一條水管接到多個出水口，出水量按權重）。這個理想值只存在於數學裡，真實 CPU 一次只能跑一個 task，EEVDF 要做的是**盡量逼近**這個理想。

### 概念一：lag（欠賬）

**lag = 這個 task「按理想公平分配器它應得的 CPU 時間」減「它實際拿到的」。**

- `lag > 0`：它被**虧待**了（應得的比拿到的多）——欠它的。
- `lag < 0`：它**超領**了（拿得比應得多）——它欠系統的。
- `lag = 0`：不多不少，正好公平。

公平的定義，在 EEVDF 裡就是一句話：**讓所有 task 的 lag 趨近 0**。誰被虧待最多（lag 最大），系統就該補償它。這其實和 CFS「誰 vruntime 最小誰先跑」是同一件事的兩種說法——vruntime 小 ≈ 拿得少 ≈ lag 大。lag 只是把「欠賬」這件事顯式地量化出來，之後好用它做別的判斷。

kernel 裡這個量存在 `sched_entity` 的 `vlag` 欄位（virtual lag，經過權重歸一化的 lag，和 vruntime 同一個「虛擬時間」單位）。

### 概念二：eligibility（合格）

光有 lag 還不夠。EEVDF 多了一條規則：**只有 `lag >= 0`（沒有超領）的 task 才「有資格（eligible）」現在跑。**

為什麼要這條？想像一個 task 剛剛長時間霸佔了 CPU（`lag` 已經很負，超領一大堆）。如果沒有這條規則，它跑完之後如果 deadline 又碰巧很近，可能立刻又被挑中——別人根本插不進來。eligibility 這道閘門擋住這種「超領的還想再跑」：**你先把欠系統的還一點（讓時間流逝、lag 回升到 0），才有資格重新競爭。**

直覺上：eligible 集合 = 此刻「有資格上桌」的 task = 所有沒超領的 task。超領的 task 被暫時關在門外，等它的 lag 隨時間回正。

### 概念三：virtual deadline（虛擬截止時間）

前兩個概念決定「誰有資格」。第三個決定「合格者裡挑誰」——這是 latency 保證的來源。

每個 task 宣告一個它想要的**時間片大小（request size / slice）**：「我這一輪想跑 `r` 這麼久」。EEVDF 依這個 `r` 給它算一個**虛擬截止時間**：

> virtual deadline ≈ 這個 task 變 eligible 的那個虛擬時間點 + 把 `r` 換算成虛擬時間單位的長度

然後規則是：**在所有 eligible 的 task 裡，挑 virtual deadline 最早的那個跑。**（這就是名字 Earliest Eligible Virtual Deadline First 的字面意思。）

關鍵在於 `r` 怎麼影響 deadline：**你要的時間片 `r` 越小，算出來的 virtual deadline 越早**——於是越容易在競爭中勝出、越快被挑到。這正好給了「小片但要快」的 task（音訊、互動）它們要的東西：它們宣告一個小 `r`，換到一個近的 deadline，於是即使 lag 和別人差不多，也會因為 deadline 早而優先跑。而 CPU-bound 的 batch task 宣告一個大 `r`（它不在乎延遲，要的是一次跑久一點少切換），deadline 遠，就讓路給 latency 敏感的。

**這就是 CFS 缺的那個維度**：`r` 讓每個 task 能獨立表達「我要的 latency」，而不必動它的 nice（比例）。

### 三者合起來的一張圖

```
   一顆 CPU 的 cfs_rq，此刻有 5 個 runnable task。
   橫軸是虛擬時間 →。每個 task 有 (lag, virtual deadline)。

   lag>=0 的是 eligible（有資格），lag<0 被關在門外。

   task    lag      eligible?   virtual deadline
   ─────   ─────    ─────────   ──────────────────────────────
   A       +8       ✔ 合格       ────────●  (deadline 較晚)
   B       +3       ✔ 合格       ──●        (deadline 早！小 r)   ◄── 挑它
   C        0       ✔ 合格       ─────●
   D       -5       ✘ 超領        (不參與競爭，等 lag 回正)
   E       +1       ✔ 合格       ──────────●  (deadline 最晚)

   選擇規則：
     Step 1  篩出 eligible 集合          → {A, B, C, E}   （D 出局）
     Step 2  在集合裡挑 deadline 最早的   → B
                    ▲                        ▲
              eligibility 這道閘門      virtual deadline 這把尺
              （公平：擋住超領者）      （latency：小 r → 早 deadline → 先跑）
```

對照 Ch 12 的 CFS：CFS 只有「挑 vruntime 最小」一步（等價於「挑 lag 最大」）。EEVDF 是**兩步**：先用 eligibility 篩出合格者（守住公平），再用 virtual deadline 在合格者裡挑（給出 latency）。多的那一步和那把 deadline 尺，就是它比 CFS 強的地方。

### 一個具體的數字例子

抽象講完，跑一組數字讓它落地。兩個同權重（nice 0）的 task，X 是 CPU-bound 的 batch（要大 slice、不在乎延遲），Y 是互動 task（要小 slice、要快）。假設此刻 runqueue 的加權平均 vruntime（理想分配器的位置）在 `V = 1000`（單位是虛擬時間，隨便挑的）。slice 換算成虛擬時間長度後：X 要 `r_x = 12`，Y 要 `r_y = 3`。

```
   task   vruntime   lag = V - vruntime   eligible?   deadline = vruntime + r
   ────   ────────   ──────────────────   ─────────   ───────────────────────
   X       996        1000-996 = +4        ✔（lag≥0）  996 + 12 = 1008
   Y       998        1000-998 = +2        ✔（lag≥0）  998 +  3 = 1001   ◄── 更早
```

兩個都 eligible（都沒超領）。CFS 會挑 vruntime 最小的 **X**（996 < 998）。EEVDF 比 deadline：Y 的 1001 < X 的 1008 → 挑 **Y**。原因全在 slice：Y 宣告的 `r_y=3` 小，deadline 被拉近，即使它 vruntime 稍大也贏。等 Y 跑完它的 3，vruntime 追到 1001、lag 變負（暫時超領、不 eligible），X 才接手跑它的 12。整體上 X 和 Y 拿到的 CPU **總量**仍按權重 1:1（公平不變），但 Y 的**每次等待**被壓短了——這就是那把 deadline 尺換到的 latency。

## 和 CFS 的關係：換的是「選擇規則」，不是地基

要打消一個誤解：EEVDF **不是**把 CFS 整個推倒重來。fair class 的地基幾乎原封不動：

- **還是用 vruntime**：`sched_entity->vruntime` 照舊，還是「按權重歸一化的已執行虛擬時間」（Ch 12）。
- **還是用權重（weight）**：nice 值照舊透過 `sched_prio_to_weight[]` 換成權重，權重照舊決定 vruntime 走多快、決定 CPU 比例。nice 語意**完全保留**——nice -20 還是拿最多、nice +19 最少。
- **還是用紅黑樹**：`cfs_rq` 裡照舊有一棵按 vruntime 排序的紅黑樹（`tasks_timeline`），task enqueue/dequeue 照舊是 O(log n)。

換掉的只有一件事：**從樹裡「挑下一個」的規則**。

| | CFS（≤6.5） | EEVDF（6.6+） |
|---|---|---|
| 挑選規則 | 挑 vruntime 最小 = 紅黑樹**最左節點**，O(1) 拿到 | 在 eligible（lag≥0）的節點裡，挑 virtual deadline 最早 |
| 需要的資訊 | 只需要每個節點的 vruntime | 額外需要每個 task 的 `deadline`、`slice`，以及判斷 eligible 要知道全樹的**平均 vruntime** |
| 資料結構 | 一棵按 vruntime 排的紅黑樹 | 同一棵樹，但**augmented（增廣）**：每個節點額外快取「以它為根的子樹的 min vruntime」，用來 O(log n) 找 eligible 者 |

為什麼需要「augmented 紅黑樹」？因為判斷一個 task eligible（`lag >= 0`）需要知道**整個 runqueue 的加權平均 vruntime**（那是「理想公平分配器此刻的位置」的代表值，程式碼裡以 `avg_vruntime` / `avg_load` 累加維護）。而「在 eligible 者中找 deadline 最早」這個查詢，要能一邊沿樹往下走、一邊剪掉「整個子樹都不 eligible」的分支——這需要每個節點快取子樹的 min vruntime。CFS 只挑最左節點，不需要這些；EEVDF 的兩步挑選，就得把樹增廣。這棵樹和挑選邏輯在 `kernel/sched/fair.c`。

`sched_entity`（`include/linux/sched.h`）為 EEVDF 新增/改動了幾個欄位（6.6 起）：

```c
// include/linux/sched.h，struct sched_entity（欄位為 v6.12，型別以源碼為準）
struct sched_entity {
    struct load_weight   load;        // 權重（來自 nice），CFS 就有
    struct rb_node       run_node;    // 掛在 cfs_rq 紅黑樹上的節點
    u64                  vruntime;    // 虛擬執行時間，CFS 就有
    s64                  vlag;        // ★ 6.6 新增：virtual lag（欠賬，可正可負）
    u64                  slice;       // ★ 6.6 新增：request size r（想要的時間片）
    u64                  deadline;    // ★ 6.6 新增：virtual deadline（挑選的尺）
    u64                  min_vruntime; // 子樹 min vruntime 快取（augmented 用）
    // ... prev_sum_exec_runtime、sum_exec_runtime 等記帳欄位照舊
};
```

> **一句話記住**：Ch 12 學的 vruntime / weight / 紅黑樹全部還在。EEVDF 只是在同一棵樹上，把「挑最左（vruntime 最小）」換成「挑 eligible 中 deadline 最早」，並為此在每個 `sched_entity` 上多帶了 `vlag`、`slice`、`deadline` 三個欄位、把樹增廣成能算子樹 min vruntime。

## 底層機制：pick_eevdf 怎麼挑

核心函式是 `kernel/sched/fair.c` 的 **`pick_eevdf()`**，被 `pick_next_entity()` 呼叫，最終服務於 Ch 11 的 `pick_next_task_fair`。它做的就是前面那張圖的兩步。分三塊看。

### 1. 記帳：update_deadline 決定 slice 用完沒、要不要重算 deadline

task 每跑一段，`update_curr()`（`kernel/sched/fair.c`，Ch 12 就有）照舊更新 vruntime。EEVDF 在它裡面多做一件事：呼叫 `update_deadline()`——檢查「這個 task 這一輪的 `slice`（request `r`）跑完了沒」。

```
   task 在跑 → update_curr() 累加它的 vruntime
                     │
                     ▼
              update_deadline(cfs_rq, se)：
                 vruntime 有沒有到 deadline？
                 ├── 還沒 → 什麼都不做，繼續跑（不被打斷，除非更早 deadline 的醒來）
                 └── 到了 → 這一輪 request 用完：
                          se->deadline = se->vruntime + calc_delta(slice)
                          （算下一輪的新 deadline，往後推一個 slice 的虛擬長度）
                          並要求重新排程（設 resched）
```

這就是 EEVDF 的「時間片」語意：一個 task 拿到 CPU 後，會一直跑到它這輪的 `slice` 用完（vruntime 追上 deadline），或被一個 deadline 更早的 task 搶佔。**`slice` 越小，deadline 推得越近、越常需要重算、越容易被別人的近 deadline 打斷你——但也代表你自己每次都能很快搶到**。這是 latency 旋鈕的直接體現。

### 2. eligibility：entity_eligible

`entity_eligible()`（`kernel/sched/fair.c`）判斷一個 se 現在 eligible 不 eligible。直覺版：**比較這個 se 的 vruntime 和整個 runqueue 的加權平均 vruntime**——沒超領（vruntime 沒跑到平均前面太多，等價於 `vlag >= 0`）就 eligible。

平均值不是每次現算，而是靠 `cfs_rq` 上累加維護的 `avg_vruntime`（加權 vruntime 總和）和 `avg_load`（權重總和）湊出來：`avg = avg_vruntime / avg_load`。enqueue/dequeue 時增量更新，查詢是 O(1)。這是「用空間（多存兩個累加值）換時間（免得掃全樹算平均）」的典型手法。

### 3. 挑選：pick_eevdf 在增廣樹上找「eligible 且 deadline 最早」

```c
// kernel/sched/fair.c，pick_eevdf() 概念骨架（省略邊界處理）
static struct sched_entity *pick_eevdf(struct cfs_rq *cfs_rq)
{
    struct rb_node *node = cfs_rq->tasks_timeline.rb_root.rb_node;
    struct sched_entity *best = NULL;

    // 特例：如果 curr 還 eligible 且 deadline 夠早，可能直接續跑（省切換）
    // ...

    // 在紅黑樹上走，利用每個節點快取的「子樹 min vruntime」
    // 剪掉「整棵子樹都不 eligible」的分支，
    // 在 eligible 的節點裡挑 deadline 最小（最早）的。
    while (node) {
        struct sched_entity *se = __node_2_se(node);

        // 左子樹若含 eligible 節點，先往左找（vruntime 較小者較可能 eligible）
        if (left_subtree_has_eligible(node) ...) {
            node = node->rb_left;
            continue;
        }
        // 這個 se 若 eligible，拿它和目前 best 比 deadline
        if (entity_eligible(cfs_rq, se)) {
            if (!best || deadline_gt(best, se))   // se 的 deadline 更早
                best = se;
        }
        node = node->rb_right;   // 往 deadline 可能更早的方向繼續
    }
    return best;
}
```

**注意**：這比 CFS 的「拿最左節點」貴——CFS 是 O(1)（最左節點有快取），EEVDF 的 `pick_eevdf` 是 O(log n) 的樹走訪（靠增廣的子樹 min vruntime 剪枝才做到 log n，而不是掃全樹的 O(n)）。這是為 latency 語意付的代價：多一點挑選成本，換「能表達 deadline」。實務上 n（一顆 CPU 上 runnable 的 fair task 數）通常很小，這個 log n 不痛。

> 上面的骨架是**概念版**。真實 `pick_eevdf` 有一堆邊界處理（curr 是否參與、`slice` 保護避免過度切換、6.x 陸續加的修正）。抓住主幹：**沿增廣樹走、用子樹 min vruntime 剪掉不 eligible 的分支、在 eligible 者中選最早 deadline**。細節去讀源碼，別背。

### 一個 CFS 沒有、EEVDF 必須處理的難題：睡了又醒的 lag

前面都在講「都 runnable」的情況。但 fair task 常常**睡一下又醒**（等 I/O、等鎖、`sleep`）。這裡藏著一個 CFS 沒有、EEVDF 卻繞不開的問題：**一個 task 睡覺時，它的 lag 該怎麼辦？**

CFS 的處理很粗暴：task 醒來時，把它的 vruntime 拉到接近當前 `min_vruntime`（給一點 sleeper bonus），基本上「既往不咎」。這正是 CFS 那些 sleeper heuristic（`GENTLE_FAIR_SLEEPERS` 之類）的來源，也是它 latency 行為難預測的一部分原因。

EEVDF 因為把 lag 顯式化了（`se->vlag`），必須認真回答：

- 一個 task 睡前如果 `lag > 0`（被虧待、系統欠它），醒來時這筆賬**該不該還**？如果直接抹掉，它就白白被虧待了；如果無條件保留，一個 task 可以「故意睡一下累積正 lag，醒來一次性討回、猛插隊」，變成攻擊面。
- 睡前如果 `lag < 0`（超領、它欠系統），醒來時這筆債**該不該賴掉**？若抹掉，等於「跑爽了就去睡一下洗白」，破壞公平。

EEVDF 的做法是 **dequeue（睡）時把 lag 存進 `vlag`，requeue（醒）時按當前狀態把它還原回 vruntime**，讓賬跨越睡眠被保存下來，而不是像 CFS 那樣一睡就清零。具體怎麼衰減、怎麼防止「睡覺累積 lag 再插隊」的濫用，是 6.6 之後 `update_entity_lag()` / `place_entity()`（`kernel/sched/fair.c`）一路在調的地方——這也是 EEVDF「仍在演進」最集中的一塊。

> 抓住結論就好：**CFS 對 sleeper 是 heuristic 洗白，EEVDF 是把 lag 當賬本跨睡眠保存**。後者理論上更一致（公平不因睡眠而漏），但把「睡眠的 lag 語意」做對很難，是 6.x 反覆修正的熱區。你讀不同 6.x 版本的 `place_entity` 會看到差異，正常。

## 動手：gdb 看 EEVDF 的欄位與挑選

把 Ch 0 的 QEMU + gdb 開起來，這次直接看 EEVDF 在一個 task 身上留下的痕跡。

### 看某個 task 的 EEVDF 欄位

```gdb
(gdb) target remote :1234
(gdb) source vmlinux-gdb.py
(gdb) break pick_eevdf
(gdb) continue
```

停下後，看當前 cfs_rq 和一個 se 的三個新欄位：

```gdb
(gdb) print cfs_rq->avg_vruntime      # 加權 vruntime 總和（算平均用）
(gdb) print cfs_rq->avg_load          # 權重總和
(gdb) print cfs_rq->min_vruntime      # runqueue 的 min vruntime 基準

# 挑一個 task，看它的 EEVDF 三件套
(gdb) print $lx_current().se.vruntime
(gdb) print $lx_current().se.vlag      # ★ 欠賬：>0 被虧待，<0 超領
(gdb) print $lx_current().se.slice     # ★ request size r（想要的時間片）
(gdb) print $lx_current().se.deadline  # ★ virtual deadline（挑選的尺）
```

`finish` 出 `pick_eevdf`，看它挑了誰、那個 se 的 deadline 是不是真的比同 runqueue 別人早：

```gdb
(gdb) finish
$1 = (struct sched_entity *) 0xffff...   # 被挑中的 se
(gdb) print ((struct sched_entity *)$1)->deadline
```

### 用 sched_setattr 設 latency 需求，看效果

EEVDF 讓「request size / latency」變成一個**可設定**的東西。使用者空間透過 `sched_setattr(2)` 的 `sched_attr` 結構表達：

- `sched_attr.sched_util_min` 之外，6.6 起 fair task 可用 **`sched_runtime`** 欄位（配 `SCHED_FLAG_KEEP_POLICY` 等 flag）當作 request size 的提示——它對應到 `se->slice`：你告訴 kernel「我一次想要這麼長的 slice」。slice 設小 → deadline 早 → 更快被挑到（低延遲、但更常被切換）；設大 → deadline 遠 → 傾向少切換、一次跑久（吞吐優先）。
- 另有 **latency-nice** 提案（`sched_attr` 裡的 latency 欄位）在 6.x 陸續成形，語意是「在同權重下，我的 latency 偏好」。介面在 6.x 仍在調整，具體欄位名以你手上 kernel 的 `uapi/linux/sched/types.h` 為準。

一個能跑的最小示範（在 QEMU 的 busybox 裡可能缺 `chrt`/工具，通常在真實 host 上試）：

```c
// set_slice.c —— 用 sched_setattr 給自己一個小 slice（低延遲傾向）
#define _GNU_SOURCE
#include <sched.h>
#include <linux/sched/types.h>   // struct sched_attr
#include <unistd.h>
#include <sys/syscall.h>
#include <stdio.h>

int main(void)
{
    struct sched_attr attr = {0};
    attr.size          = sizeof(attr);
    attr.sched_policy  = SCHED_NORMAL;      // 還是 fair class
    attr.sched_nice    = 0;                 // 比例不變（nice 照舊）
    attr.sched_runtime = 1 * 1000 * 1000;   // 提示：想要 ~1ms 的 slice（低延遲）

    if (syscall(SYS_sched_setattr, 0, &attr, 0) < 0)
        perror("sched_setattr");            // 舊 kernel/不支援會失敗
    else
        printf("slice hint set, check /proc/self/sched\n");

    /* 之後對照 cat /proc/self/sched 看 se.slice / se.deadline 的變化 */
    return 0;
}
```

跑完 `cat /proc/self/sched`，找 `se.slice`、`se.vruntime`、`se.deadline`、`se.vlag` 這幾行（`CONFIG_SCHED_DEBUG` 開啟時 `/proc/<pid>/sched` 會印 EEVDF 欄位），對照你設的 `sched_runtime`。

### 概念對比：同一組負載，CFS vs EEVDF 選誰

不必真的跑兩個 kernel，用前面那張圖的思路手推一次就懂差別。設想一顆 CPU 上三個 fair task：

| task | 角色 | nice | vruntime | slice (r) | 誰被虧待（lag） |
|---|---|---|---|---|---|
| BUILD | `cc1` 編譯 | 0 | 較小 | 大（不在乎延遲） | lag 大（睡少、跑多、剛好在追） |
| AUDIO | 音訊 buffer | 0 | 中等 | 小（要快） | lag 略正（常睡） |
| BULK | 檔案壓縮 | 0 | 中等 | 大 | lag 略正 |

- **CFS 會挑誰**：vruntime 最小的 BUILD——因為 CFS 只看 vruntime。AUDIO 要等 BUILD 的 vruntime 追上來才輪到，可能晚幾毫秒 → 爆音風險。
- **EEVDF 會挑誰**：先篩 eligible（三個都 lag≥0，都合格），再比 deadline。AUDIO 的 `slice` 小 → deadline 最早 → **挑 AUDIO**。BUILD 雖然 vruntime 最小，但它 `slice` 大、deadline 遠，讓路給 AUDIO。長期公平仍成立（BUILD 總量不會少拿，它 deadline 遠只是被延後、不是被剝奪），但 AUDIO 的**每次延遲**被壓下來了。

這一步差異，就是 6.6 換 EEVDF 想要的效果：**不犧牲長期公平，換到可控的 per-wakeup latency**。

## 對比與取捨

| 維度 | CFS（≤6.5） | EEVDF（6.6+） |
|---|---|---|
| 核心不變量 | vruntime 正比於權重（長期公平） | 同上 + 每個 task 的 lag 趨近 0 |
| 挑選規則 | vruntime 最小（樹最左節點）O(1) | eligible 中 deadline 最早，O(log n) |
| latency 表達 | 沒有原生機制，靠一堆 heuristic 補丁 | 原生：request size `slice` 直接映射 latency |
| 旋鈕數 | 一個（nice/權重，管比例） | 兩個（nice 管比例、slice 管 latency） |
| 挑選成本 | O(1)（最左快取） | O(log n)（增廣樹走訪 + 剪枝） |
| 資料結構 | vruntime 紅黑樹 | 同樹 + augmented（子樹 min vruntime）+ avg_vruntime 累加 |
| 主要收益 | 簡單、公平、好證明 | 公平之外多了可控 latency，砍掉一堆調參補丁 |

換 EEVDF 的代價是真實的：挑選從 O(1) 變 O(log n)（雖然 n 小，多半無感）；`sched_entity` 變胖了幾個欄位；某些**依賴 CFS 舊 heuristic 精確行為**的工作負載換過來後 benchmark 數字變了（有的變好、有的短期變差，社群在 6.7~6.12 陸續調校）。這是拿「一個乾淨的理論 + 可調的 latency」換「十五年調出來的 CFS 手感」——長期方向對，但過渡期有摩擦。

## 踩雷集錦

1. **以為 EEVDF 把 vruntime 丟掉了**——沒有。vruntime、權重、紅黑樹全留著（Ch 12 沒白學）。換的只是「從樹裡挑誰」的規則：CFS 挑 vruntime 最小，EEVDF 挑 eligible 中 deadline 最早。地基不動，只換選擇器。

2. **以為 virtual deadline 是硬性即時 deadline**——不是。它是**虛擬時間軸上**的一個排序鍵，用來在 fair task 之間比「誰該先」，錯過它不會有任何懲罰或告警。真正有硬性 deadline、錯過即失效的是 `SCHED_DEADLINE`（`dl_sched_class`，Ch 11 的另一個 class，EDF+CBS）——兩者名字都有 deadline 但完全不同層次，別混。

3. **以為 slice（request size）改的是 nice/比例**——不。`slice` 管的是 **latency 維度**，不動 CPU 總量比例。比例還是 nice/權重管。把 `slice` 設小，你不會拿到更多 CPU 總量，你只是**更頻繁地、每次更快地**拿到你那份——代價是 context switch 變多。想拿更多總量請調 nice，不是調 slice。

4. **以為 eligibility 只是「效能優化」可以忽略**——不。eligibility 是 EEVDF **公平性的核心閘門**：沒有它，一個超領的 task 若 deadline 又近，會反覆插隊、把別人餓住。`lag>=0 才有資格` 這條規則，是「先還債才能再借」的公平保證，不是可有可無的加速。

5. **以為換了 EEVDF 就一定比 CFS 快 / 每種負載都更好**——不。EEVDF 的目標是**latency 可控 + 公平**，不是「更快」。有些吞吐導向、依賴 CFS 舊行為的負載，換過來 benchmark 可能持平甚至短期倒退。它是「更對的抽象」，不是「無腦更好的數字」。這正是社群在 6.x 持續調校的原因。

## 進階：再往深一層

- **EEVDF 仍在演進（認識論誠實）**：6.6 只是「落地」，不是「定案」。lag 在 dequeue/requeue 時怎麼保存與衰減（`vlag` 的處理）、`slice` 的預設與保護、避免過度切換的 `RUN_TO_PARITY`、fair 的 deadline server（讓 fair task 不被 rt 餓死，Ch 11 提過）等，6.7 到 6.12 一路在調。**你讀 6.12 的 `pick_eevdf` 和你 6.16 讀到的可能不一樣**——抓住三個核心概念（lag / eligibility / virtual deadline）的直覺，具體實作以你手上版本源碼為準。這是本課少數「主線還在移動」的子系統。

- **request size 和 tick 的關係**：EEVDF 讓時間片變成 per-task 的 `slice`，而不是 CFS 那種全域算出來的動態 granularity。這也和 6.x 的 tickless / 高解析 timer 互動——一個 slice 很小的 task 可能需要更細的 timer 才能準時被打斷（Ch 32 hrtimer）。

- **`RUN_TO_PARITY`：不要過度搶佔**：純理論的 EEVDF，只要冒出一個 deadline 更早的 eligible task 就該立刻切過去。但每次 wakeup 都搶佔會製造大量 context switch，反而傷吞吐。6.6 引入的 `RUN_TO_PARITY`（一個 `sched_feat`，可在 `/sys/kernel/debug/sched/features` 開關）讓**正在跑的 curr 先把它這輪的 slice 跑完**（跑到「parity」＝它的 lag 回到 0）再讓賢，而不是被每個新來的近 deadline 打斷。這是「理論純度」和「切換成本」之間的工程折衷，也是換 EEVDF 後某些負載行為變化的來源之一。你可以在 QEMU 裡 `echo NO_RUN_TO_PARITY > /sys/kernel/debug/sched/features` 關掉它，觀察 context switch 次數怎麼變。

- **和 core scheduling / SMT 的互動**：`pick_eevdf` 選出的 se 要再過 Ch 11 提的 core scheduling（同一實體核的兩個 hyperthread 要不要一起調度）那一關。EEVDF 的挑選和 core sched 的約束是兩層，讀源碼時別把它們攪在一起。

- **面試常問**：「6.6 為什麼換掉 CFS？」——答 CFS 沒有原生的 latency/deadline 維度，只能靠一堆互相打架的 heuristic 補丁；EEVDF 用 request size 把 latency 做成第一等公民，且不犧牲長期公平。「EEVDF 三個概念是什麼？」——lag（欠賬，公平＝讓 lag 趨 0）、eligibility（lag≥0 才有資格，擋超領者）、virtual deadline（request 越小 deadline 越早，給 latency）。「它和 CFS 共用什麼？」——vruntime、權重、紅黑樹全留，只換挑選規則並把樹增廣。能把這三題串起來就抓住這章了。

## 動手練習

1. **在 gdb 看齊三個新欄位**：`break pick_eevdf`，停下後 `print` 幾個 se 的 `vlag`、`slice`、`deadline`，並 `print cfs_rq->avg_vruntime` / `avg_load` 算出平均，手動驗證「被挑中的 se 是不是 eligible（vruntime 沒超前平均太多）且 deadline 最早」。

2. **對照 `/proc/<pid>/sched`**：在 QEMU 裡（需 `CONFIG_SCHED_DEBUG`）`cat /proc/self/sched`，找 `se.vruntime`、`se.slice`、`se.deadline`、`se.vlag` 幾行。跑一個 busy loop 的 background job，再看它的這幾行怎麼變——特別看它跑久之後 `vlag` 是不是往負走（超領）。

3. **用 slice 改 latency，量切換次數**：在真實 host（QEMU 工具可能不全）寫上面的 `set_slice.c`，分別設 `sched_runtime` 為很小（如 0.5ms）和很大（如 20ms），各跑同一段固定工作，用 `cat /proc/<pid>/status` 看 `voluntary_ctxt_switches` / `nonvoluntary_ctxt_switches` 的差異——slice 小的那個切換次數應明顯多。這讓你**量到** latency/吞吐的取捨。

4. **手推 CFS vs EEVDF**：拿「對比與取捨」那張三 task 表，自己在紙上跑：CFS 會依序挑誰、EEVDF 會依序挑誰，畫出接下來幾個 tick 的排程序列。確認「EEVDF 讓 AUDIO 更早、但 BUILD 長期總量不變」這個結論你能自己推出來，而不是背下來。

## 本章重點整理

- **為什麼換**：CFS 只保證長期公平、沒有原生 latency/deadline 維度，多年靠一堆互相打架的 heuristic 補丁硬撐。EEVDF（Stoica & Abdel-Wahab 1995）用更乾淨的理論把 latency 做成第一等公民，6.6 起取代 CFS 當 fair class 的演算法。
- **三個核心概念**：**lag**（應得減實得，公平＝讓大家 lag 趨 0，存在 `se->vlag`）；**eligibility**（只有 lag≥0 才有資格跑，擋住超領者反覆插隊）；**virtual deadline**（依 request size `slice` 算，`slice` 越小 deadline 越早 → 越快被挑，給出 latency 保證）。選擇規則＝eligible 者中挑 deadline 最早（`kernel/sched/fair.c` 的 `pick_eevdf`）。
- **和 CFS 的關係**：vruntime、權重、紅黑樹全留（Ch 12 的地基不動），只把「挑 vruntime 最小」換成「eligible 中 deadline 最早」，並為此把紅黑樹**增廣**（快取子樹 min vruntime）、用 `avg_vruntime`/`avg_load` 算平均判 eligible；`sched_entity` 新增 `vlag`/`slice`/`deadline`。
- **實務與誠實**：`sched_setattr` 的 `sched_runtime` 對應 `se->slice`，讓使用者表達 latency 需求；nice 語意完全保留；挑選成本從 O(1) 變 O(log n)（n 小多半無感）。EEVDF 6.x 仍在調校，讀到的實作以手上版本源碼為準。

## 自我檢核

- [ ] 不看筆記，能說出「6.6 為什麼換掉 CFS」——CFS 缺了哪個維度、EEVDF 補上什麼
- [ ] 能用直覺（不堆數學）解釋 lag、eligibility、virtual deadline 三者各是什麼、合起來怎麼挑 task
- [ ] 能說清楚 EEVDF 和 CFS 共用哪些東西（vruntime/權重/紅黑樹）、只換了什麼（挑選規則 + 樹增廣）
- [ ] 能解釋 request size（`slice`）為什麼是 latency 旋鈕而不是比例旋鈕，以及調它的代價（切換變多）
- [ ] 能用 gdb 停在 `pick_eevdf`、印出 `se.vlag`/`se.slice`/`se.deadline`，並說出被挑中的 se 為何勝出
- [ ] 面試被問「EEVDF 三個核心概念」與「它和 SCHED_DEADLINE 的 deadline 差在哪」，能分別答清楚

## 延伸閱讀

### LWN 文章（讀懂這章的最佳補充）

- **[An EEVDF CPU scheduler for Linux](https://lwn.net/Articles/925371/)** — Jonathan Corbet, LWN.net
  - **讀哪裡**：整篇。這是 EEVDF 進 mainline 前 LWN 對它最完整的白話介紹，把 lag / eligibility / virtual deadline 講得比論文好懂
  - **和本章關聯**：本章三個概念的直覺講法主要對著這篇；讀完你會對「為什麼要 eligibility」有更深體會
- **[Completing the EEVDF scheduler](https://lwn.net/Articles/969062/)** — LWN.net
  - **讀哪裡**：了解 EEVDF 落地後 6.x 還在補哪些洞（lag 保存、latency-nice 介面、deadline server）
  - **為什麼讀**：印證本章「EEVDF 仍在演進」那一節，知道哪些行為版本間會變

### 原始論文

- **["Earliest Eligible Virtual Deadline First: A Flexible and Accurate Mechanism for Proportional Share Resource Allocation"](https://citeseerx.ist.psu.edu/)** — Ion Stoica & Hussein Abdel-Wahab, 1995
  - **讀哪裡**：前半的 model 與 eligibility/deadline 定義；後半的證明可略讀
  - **注意**：這是 EEVDF 理論的源頭，比 CFS 還老。kernel 的實作是它的工程化版本，符號/近似和論文不完全一致，讀它是為了拿到「公平＝lag 趨 0、latency＝deadline 排序」這個原始直覺，不是逐行對應源碼

### 原始碼

- **[kernel/sched/fair.c 的 `pick_eevdf` / `entity_eligible` / `update_deadline`（v6.12）](https://elixir.bootlin.com/linux/v6.12/source/kernel/sched/fair.c)** — Bootlin Elixir
  - **讀哪裡**：搜 `pick_eevdf`、`entity_eligible`、`update_deadline`、`avg_vruntime`、`update_curr`
  - **怎麼讀**：對照本章「pick_eevdf 怎麼挑」三塊讀，先抓「記帳→篩 eligible→挑最早 deadline」骨架，略過 `RUN_TO_PARITY`、curr 特例等分支
- **[include/linux/sched.h 的 `struct sched_entity`（v6.12）](https://elixir.bootlin.com/linux/v6.12/source/include/linux/sched.h)** — Bootlin Elixir
  - **讀哪裡**：`struct sched_entity`，對照本章看 `vlag`/`slice`/`deadline` 三個 6.6 新增欄位和 CFS 舊有的 `vruntime`/`load` 並存

排程器的 fair class 到此告一段落：你懂了 CFS 怎麼做長期公平（Ch 12）、EEVDF 怎麼在公平之上補回 latency（本章）。但無論哪個演算法選出了「下一個 task」，真正把 CPU 從 prev 交到 next 手上——切換暫存器、切換位址空間、切換 kernel stack——都還沒發生。下一章進到 context switch 的下半場，看 x86_64 和 ARM64 各自怎麼完成這場「換人上場」。

→ [Ch 14 context switch 與 preemption（x86 vs ARM64）](./14-context-switch.md)
