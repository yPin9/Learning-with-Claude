# Ch 12 — CFS 深入：vruntime 與紅黑樹

> **目標**：搞懂 CFS（Completely Fair Scheduler / 完全公平排程器）用什麼機制把「每個 task 拿到公平一份 CPU」這件事做出來——核心是 **vruntime（virtual runtime / 虛擬執行時間）** 這個記帳量、以及用**紅黑樹**按 vruntime 排序「下一個誰跑」。學完你能算出「兩個 nice 值不同的 task 會怎麼分 CPU」，能在 gdb 裡看 `cfs_rq` 的紅黑樹、讀 `se.vruntime`，並理解為什麼 nice 值差 1 大約是 1.25 倍 CPU。

> **版本警告，先講清楚**：你在 6.12 上跑的 fair class **不是 CFS，是 EEVDF**（Earliest Eligible Virtual Deadline First）——CFS 在 **6.6（2023 年 10 月）被 EEVDF 取代**，`kernel/sched/fair.c` 裡已經沒有純 CFS 那套「永遠挑 vruntime 最小」的邏輯了。那為什麼還花一整章講 CFS？因為 **EEVDF 沿用了 CFS 的地基**：vruntime、紅黑樹、weight（權重）、nice→weight 對應表這些概念，EEVDF 一個都沒丟，只是在「挑誰」這一步加了 lag 與 virtual deadline 兩個新維度（Ch 13 主題）。不先把 CFS 的 vruntime 模型吃透，看 EEVDF 只會覺得它憑空冒出一堆術語。**這章講的是「公平性怎麼被量化」這個到今天仍成立的核心思想**；Ch 13 再看 EEVDF 怎麼修 CFS 的短板。本章談到「6.6 前的實際實作細節」時會標注，別把它們當 6.12 的現況背。

## 為什麼需要這個？

Ch 11 我們停在一個問題上：`fair_sched_class` 佔了系統 99% 的 task，`pick_next_task_fair` 到底**怎麼**從一堆 runnable 的一般 process 裡挑出下一個？

先看 CFS 之前的世界。2.6 早期的 **O(1) scheduler** 用的是「140 條優先權 queue + 兩個 active/expired 陣列」的設計：每個 task 依 nice 值算出一個固定**時間片（time slice）**，用完就丟到 expired 陣列，等 active 陣列空了兩邊交換。它快（挑下一個是 O(1)），但公平性是**用一堆啟發式規則湊出來的**——為了讓互動式 task 反應快，它得靠「偵測一個 task 是不是互動式」的複雜 heuristic（看它睡多久、醒多久）給獎懲。這些 heuristic 難調、有一堆 corner case、而且「到底怎樣才算公平」根本沒有一個乾淨的定義，全靠工程師憑感覺塞規則。

Ingo Molnár 在 2007 年（2.6.23）用 CFS 換掉它，靠的是一個**乾淨到近乎數學**的想法：不要湊規則，直接去逼近一個理想模型。

那個理想模型是**「完美多工的 CPU」**：假設 CPU 可以無限細地切分，N 個 runnable task 就讓每個**同時**各拿 `1/N` 的 CPU。現實 CPU 一次只能跑一個 task，做不到「同時」，但 CFS 可以**追蹤每個 task 相對於這個理想拿到了多少**，然後**永遠優先補償那個落後最多的**。這樣一來公平性不是靠 heuristic，而是「誰吃虧最多就先給誰」這個單一規則的自然結果。

問題只剩一個：怎麼量化「落後多少」？答案就是 vruntime。

## 先建立直覺

想像四個人排隊用一台**只有一個水龍頭**的飲水機，但規則是「誰累積喝到的水最少，下一個就給誰喝」。你只要在每個人身上掛一個計數器，記他喝了多少毫升，每次都挑計數器最小的那個，飲水機自然就公平了——不用管誰先來、誰口渴、誰喝得快。

CFS 就是這台飲水機。CPU 是水龍頭，task 是排隊的人，每個 task 身上掛的計數器就是 **vruntime**：跑得越久，vruntime 漲得越多；排程時**永遠挑 vruntime 最小的 task 上場**。落後的 task vruntime 小 → 被優先挑中 → 追上進度；領先的 task vruntime 大 → 排到後面等。系統自然趨向「大家 vruntime 差不多」，也就是「大家拿到差不多的 CPU」。

```
   四個 runnable task 的 vruntime（值越小 = 吃虧越多 = 越該先跑）

   task A  vruntime = 100  ◄── 最小！下一個挑它
   task B  vruntime = 115
   task C  vruntime = 120
   task D  vruntime = 130

   A 跑了一陣子，vruntime 漲到 122：

   task B  vruntime = 115  ◄── 現在換 B 最小，下一個挑 B
   task A  vruntime = 122
   task C  vruntime = 120       （重新排序：B < C < A < D）
   task D  vruntime = 130
```

但「永遠挑最小的」在資料結構上有個要求：你需要一個容器，能**快速拿出最小值**、又能**快速在中間插入/刪除**（task 睡醒、被喚醒、跑完一輪都要重新入列）。排序好的陣列拿最小值 O(1) 但插入 O(n)；沒排序的鏈結串列插入 O(1) 但找最小 O(n)。CFS 的選擇是**紅黑樹（red-black tree）**（Ch 5 講過的自平衡二元搜尋樹）：插入/刪除 O(log n)，而「最小值」就是**最左邊的節點**——CFS 還把最左節點的指標快取起來，拿它是 O(1)。這棵樹按 vruntime 當 key 排序，就是 CFS 的核心資料結構。

還有一個關鍵沒解決：**公平不等於平等**。你想讓編譯任務讓步給音訊播放，就得能調「誰更重要」。CFS 用 **weight（權重）** 做這件事——但它不是直接給重要的 task 更多時間片，而是**動手腳在 vruntime 漲的速度上**：權重高的 task，實際跑了同樣久，vruntime 卻漲得慢，於是它更常是「最小的那個」，自然拿到更多 CPU。這個「用 vruntime 漲速的快慢來實現加權公平」的巧思，是 CFS 最漂亮的地方，下面細看。

## vruntime 怎麼算：把「加權」藏進時間的漲速裡

vruntime 不等於 task 實際跑的時間（那叫 `sum_exec_runtime`，真實掛鐘時間）。vruntime 是**按權重縮放過的虛擬時間**。公式（概念版）：

```
   Δvruntime  =  Δ實際執行時間  ×  ( NICE_0_WEIGHT / task 的 weight )
                                     └──────────┬──────────┘
                                        權重高 → 這個比值 < 1 → vruntime 漲得慢
                                        權重低 → 這個比值 > 1 → vruntime 漲得快
```

`NICE_0_WEIGHT` 是 nice 值為 0（預設優先權）的 task 的權重，值是 **1024**。所以：

- **nice = 0 的 task**：weight 剛好 1024，比值 = 1024/1024 = 1，vruntime **就等於**實際執行時間，1:1。
- **權重高於 1024 的 task**（nice < 0，更高優先權）：比值 < 1，跑 1ms 實際時間，vruntime 只漲不到 1ms。因為它 vruntime 漲得慢，它會**更頻繁地成為「vruntime 最小」的那個**，所以更常被挑中 → 拿到**更多** CPU。
- **權重低於 1024 的 task**（nice > 0，更低優先權）：比值 > 1，跑 1ms，vruntime 漲超過 1ms，很快變成「最大的」，被排到後面 → 拿到**更少** CPU。

這就是 CFS 的核心手法：**它從不直接分配時間片，而是操縱每個 task 的 vruntime 漲速；快慢決定了它多常被挑中，多常被挑中就決定了它拿多少 CPU。** 公平（大家 vruntime 拉平）和加權（不同 weight 漲速不同）用同一個機制一次搞定。

源碼在 `kernel/sched/fair.c` 的 `update_curr()`：它算出 curr 這次跑了多少實際時間（`delta_exec`），呼叫 `calc_delta_fair()` 把它換算成 vruntime 增量，加到 `curr->vruntime` 上。`calc_delta_fair` 內部呼叫 `__calc_delta`，用的正是上面那個「乘以 NICE_0_WEIGHT / weight」的縮放（實際實作為避免除法用了預先算好的乘法+位移，數學上等價）。`update_curr` 是整個 fair class 的記帳核心，每次 tick、每次 enqueue/dequeue 前都會先呼叫它把「到目前為止跑的帳」結清。

### nice 值 → weight：`sched_prio_to_weight` 表

nice 值範圍 −20（最高優先權）到 +19（最低），對應的 weight 不是線性算的，而是查一張**寫死的表** `sched_prio_to_weight[]`（`kernel/sched/core.c`）。這張表的設計原則是：**nice 每差 1，CPU 份額約差 1.25 倍**。

```c
// kernel/sched/core.c，sched_prio_to_weight[40]，index 0 對應 nice=-20
const int sched_prio_to_weight[40] = {
 /* -20 */  88761,  71755,  56483,  46273,  36291,
 /* -15 */  29154,  23254,  18705,  14949,  11916,
 /* -10 */   9548,   7620,   6100,   4904,   3906,
 /*  -5 */   3121,   2501,   1991,   1586,   1277,
 /*   0 */   1024,    820,    655,    526,    423,   // nice=0 → 1024 = NICE_0_WEIGHT
 /*   5 */    335,    272,    215,    172,    137,
 /*  10 */    110,     87,     70,     56,     45,
 /*  15 */     36,     29,     23,     18,     15,
};
```

為什麼是 1.25？這是設計選擇，理由是**「感知上有意義」**：`1.25^1 ≈ 1.25`，相鄰兩個 nice 的 CPU 比例約 1.25:1；而 nice 差 10 → `1.25^10 ≈ 9.3`，差不多 10 倍。這讓 nice 值有直覺可循：「調高一級 nice，這個 task 大約多拿/少拿 25% CPU」。你可以驗證表格：`1024 / 820 ≈ 1.249`、`1024 / 1277 ≈ 0.802`（也就是 nice=−1 的 1277/1024 ≈ 1.247 倍），比例確實維持在 1.25 附近。

**用 nice 算 CPU 份額**：兩個 task 在同一顆 CPU 上競爭，各自 CPU 份額 = `自己的 weight / 所有 runnable task 的 weight 總和`。舉例：一個 nice=0（weight 1024）和一個 nice=5（weight 335）：

- nice=0 拿 `1024 / (1024+335) ≈ 75.4%`
- nice=5 拿 `335 / (1024+335) ≈ 24.6%`

比例約 3:1，正好是 `1.25^5 ≈ 3.05`。這個算法（`__cfs_rq_removed_weight` 之類的加總，核心是 `cfs_rq->load.weight` 累計所有 se 的 weight）就是「動手」那節你要用 `stress` + `nice` 親眼看到的分配。

## struct sched_entity：被排程的到底是什麼

這裡有個容易誤會的點：**CFS 排程的單位不是 `task_struct`，是 `struct sched_entity`（簡稱 se）。** 定義在 `include/linux/sched.h`：

```c
// include/linux/sched.h，struct sched_entity（節選 fair 相關欄位）
struct sched_entity {
    struct load_weight    load;        // 這個 entity 的 weight（來自 nice）
    struct rb_node        run_node;    // 掛進 cfs_rq 紅黑樹的節點（Ch 5 的 rbtree）
    unsigned int          on_rq;       // 現在在不在 rq 上（runnable？）

    u64                   exec_start;      // 本次上場的起始時間戳
    u64                   sum_exec_runtime;// 累計實際執行時間（真實時間）
    u64                   vruntime;        // ★ 虛擬執行時間——CFS 排序的 key
    u64                   prev_sum_exec_runtime;

    struct sched_entity  *parent;      // group scheduling：上一層 entity
    struct cfs_rq        *cfs_rq;      // 我掛在哪個 cfs_rq 上
    struct cfs_rq        *my_q;        // 若我代表一個 group，這是我底下那個 cfs_rq
    // ... EEVDF 之後還加了 deadline / vlag / slice（Ch 13）
};
```

`task_struct`（Ch 9）裡**內嵌**一個 `struct sched_entity se`（不是指標，是整個結構體嵌進去）。給一個 `se`，用 `container_of` 反推回外層的 `task_struct`（就是 `task_of(se)`）——這是 Ch 5 講的 kernel「內嵌 + container_of」慣用法在排程器裡的又一次現身。

**為什麼要多這一層 `sched_entity` 抽象，不直接排 `task_struct`？** 因為 **group scheduling（群組排程）**。想像你有兩個使用者 alice 和 bob，alice 開了 1 個 task、bob 開了 99 個。如果 CFS 直接排 task，bob 會拿到 99% CPU——這不公平（對「使用者」這個層級而言）。group scheduling 的做法是：先把 CPU 在 alice 和 bob 兩個**組**之間公平分（各 50%），再在每組**內部**的 task 之間公平分。

實作上，每個 group 也是一個 `sched_entity`（它的 `my_q` 指向該 group 專屬的一個 `cfs_rq`，裡面裝這個 group 的 task），於是 cfs_rq 變成**樹狀階層**：頂層 cfs_rq 裡的 se 可能是「一個真 task」也可能是「一個 group」；是 group 的話，挑中它之後再往它底下的 cfs_rq 遞迴挑一次。CFS 的挑選邏輯（`pick_next_task_fair` 裡的迴圈）對「task 的 se」和「group 的 se」一視同仁，因為它們都是 `sched_entity`——**這就是這層抽象存在的理由：讓同一套 vruntime + 紅黑樹演算法能遞迴套用在「task」和「一群 task」上。**

這套 group scheduling 正是 **cgroup v2 的 cpu controller**（你在 `docker` 課裡設 `--cpus` / cpu.weight 限制容器 CPU 時用的東西）的 kernel 側實作，完整機制是 **Ch 50** 的主題。這裡你只要記住：**se 是排程的原子單位，task 和 group 都用它表示，所以演算法能統一。**

## 底層機制：cfs_rq 的紅黑樹怎麼運作

現在把資料結構拼起來。每顆 CPU 的 `struct rq` 裡有一個 `struct cfs_rq cfs`（Ch 11 見過），它的核心就是那棵**按 vruntime 排序的紅黑樹**：

```c
// kernel/sched/sched.h，struct cfs_rq（節選）
struct cfs_rq {
    struct load_weight  load;          // 這個 cfs_rq 上所有 se 的 weight 總和
    unsigned int        nr_running;    // 上面掛了幾個 se

    u64                 min_vruntime;  // ★ 這個 cfs_rq 的 vruntime 基準線（下面詳解）

    struct rb_root_cached tasks_timeline;  // ★ 紅黑樹本體，_cached = 有快取 leftmost

    struct sched_entity *curr;         // 現在正在跑的 se
    // ...
};
```

`rb_root_cached` 是 Ch 5 講過的「帶 leftmost 快取的紅黑樹」：它在普通 `rb_root` 之外多存一個指向**最左節點**的指標。因為 CFS 每次要的就是 vruntime 最小的 se（= 最左節點），有了這個快取，「拿下一個要跑的」是 **O(1)**（直接讀快取指標），不用每次從樹根走到最左葉。插入/刪除仍是 O(log n)（要維持平衡），但那只在 task 入列/離列時發生，不是每次排程的熱路徑。

```
        cfs_rq 的紅黑樹（按 vruntime 排序，key = se.vruntime）

                          [ vr=120 ]
                         /          \
                  [ vr=115 ]      [ vr=130 ]
                   /       \
            [ vr=100 ]  [ vr=118 ]
                 ▲
                 │
        leftmost（快取起來）── 最左節點 = vruntime 最小 = 下一個上場的 se
        pick_next_task_fair 直接讀這個快取指標，O(1)

     ┌─────────────────────────────────────────────────────────┐
     │ 二元搜尋樹性質：左子樹全部 < 節點 < 右子樹全部            │
     │  → 一路往左走到底 = 最小 vruntime                        │
     │  → CFS 把「往左走到底」的結果快取成 leftmost，省掉這趟走   │
     └─────────────────────────────────────────────────────────┘
```

一輪排程的資料流（6.6 前的**純 CFS** 版本，幫你建立心智模型）：

```
   1. tick 到 / task 要讓出 → update_curr(cfs_rq)
        算 curr 這次跑的 delta_exec，換算成 vruntime 加到 curr->vruntime
        （curr 的 vruntime 漲了，它在「該不該繼續跑」的競爭中位置往後退）

   2. pick_next_task_fair → 讀紅黑樹的 leftmost 快取
        = 拿到 vruntime 最小的 se = 下一個該跑的

   3. 若挑中的不是 curr：
        - curr 若還 runnable → 用它更新後的 vruntime 重新插回紅黑樹（O(log n)）
        - 把選中的 se 從樹上摘下，設為新 curr，set_next_task
        - context_switch 切過去（Ch 14）

   4. 選中的 se 開始跑，累積 exec time，下次 tick 回到步驟 1
```

> **EEVDF 改了步驟 2**：6.6 起，「挑誰」不再是無腦選 leftmost（vruntime 最小），而是「在所有 **eligible**（lag ≥ 0，還沒超支的）的 se 裡，挑 **virtual deadline 最早**的」。紅黑樹還在、vruntime 還在、weight 還在——變的是排序的 key 和「誰有資格被挑」的過濾條件。所以你上面建立的這套「紅黑樹 + vruntime 記帳」心智模型，Ch 13 會直接拿來用，只換挑選規則。

### min_vruntime：紅黑樹的浮動基準線

`cfs_rq->min_vruntime` 是這棵樹的**單調遞增基準線**，大致追蹤「這個 cfs_rq 上最小的 vruntime」。它有兩個關鍵作用：

1. **防止 vruntime 溢位 / 保持數值可比**：vruntime 是絕對值一直漲的 u64，直接比大小在極端情況下有溢位風險。CFS 實際比較時常用「相對 min_vruntime 的差值」，`min_vruntime` 當浮動原點。
2. **當新 task / 睡醒 task 的 vruntime 基準**——這是下一節的重點。

`min_vruntime` **只增不減**（單調遞增），即使樹上最小的 se 被摘走了它也不倒退——這保證了「時間不會倒流」，用它當基準塞進來的 task 不會拿到一個比現有 task 都小很多的 vruntime 而獨佔 CPU。

## 新 task 與睡醒 task 的 vruntime：防止「餓很久回來報復」

這是 vruntime 模型最容易出漏洞、也最能看出設計功力的地方。考慮兩個場景：

**場景一：一個 task 睡了 10 分鐘才醒。** 這 10 分鐘它 vruntime 完全沒漲，而其他一直在跑的 task vruntime 漲了一大截。如果醒來直接拿它 10 分鐘前的舊 vruntime 去比，它會是**遠遠最小**的那個——於是它一醒來就霸佔 CPU，把別人餓死，直到它的 vruntime 追平大家。一個 `sleep` 完的 task 反而能獨佔 CPU，這顯然是災難。

**場景二：一個新 `fork` 出來的 task，vruntime 初值該給多少？** 給 0 的話它是全場最小，新 process 一出生就搶佔一切——一個瘋狂 `fork` 的程式能靠「每個新 child 都 vruntime=0」餓死所有老 task。

CFS 對兩者的解法都圍繞 `min_vruntime`：

- **睡醒的 task（`place_entity` 在 enqueue 時處理）**：不用它睡前的舊 vruntime，而是**拉到接近當前 `cfs_rq->min_vruntime`**。因為 min_vruntime 在它睡覺期間一路漲上來了，這相當於說「你睡覺這段時間的帳一筆勾銷，但你也別想拿補償——從現在大家的基準線重新排隊」。實作上還會**減掉一個「睡眠獎勵」上限**（讓互動式 task 醒來能稍微優先一點點，回應快），但這個獎勵有嚴格上限，不會讓它獨佔。
- **新 task（`fork` 時，`task_fork_fair` → `place_entity`）**：初始 vruntime 也設在 `min_vruntime` 附近（而非 0），所以新 task 從「當前大家的進度線」開始排隊，不會一出生就搶佔全場。6.6 前 CFS 還會給新 task 的 vruntime 加一點點懲罰，避免 fork 炸彈占便宜。

> **一句話抓住原則**：**vruntime 不是絕對計時器，而是「相對 min_vruntime 的排隊位置」。** 任何「憑空冒出來」或「消失很久回來」的 se，都要先被 `place_entity` 拉到 min_vruntime 附近才准進紅黑樹——這就是 CFS（和 EEVDF）防止各種餓死/獨佔攻擊的統一防線。EEVDF 用 lag 把這件事做得更精確（Ch 13），但思路一脈相承。

## 時間片：CFS 為什麼「沒有固定時間片」

O(1) scheduler 給每個 task 算一個**固定時間片**（比如 100ms），用完就換。CFS **刻意不這樣**——固定時間片在 task 數量變化時會出問題：runnable task 從 2 個變成 200 個，若每個都給固定 100ms，那「輪一圈」要 20 秒，互動延遲爆炸。

CFS 的做法是**動態算「這一輪每個 task 該跑多久」**，圍繞兩個可調參數（`kernel/sched/fair.c`，可透過 `/sys/kernel/debug/sched/` 或舊的 sysctl 調）：

- **`sysctl_sched_latency`**（targeted latency，目標延遲，預設約 6ms×`(1+log2(ncpu))`）：CFS 想保證「每個 runnable task 至少被排到一次」的一個目標週期。task 少時，每個 task 這一輪能分到的時間 = `sched_latency / nr_running`（再按 weight 加權）。
- **`sysctl_sched_min_granularity`**（最小粒度，預設約 0.75ms）：單個 task 一次上場的**最短**時間。當 task 多到 `sched_latency / nr_running` 會小於這個值時，改用 min_granularity 當每個 task 的時間片（此時「輪一圈」會超過 sched_latency，是刻意的取捨——寧可拉長週期也不要 context switch 頻繁到 overhead 吃掉一切）。

`sched_slice()`（純 CFS 的函式）就是算「curr 這一輪該跑多久」的：按 curr 的 weight 佔 cfs_rq 總 weight 的比例，去分 `sched_latency` 這塊「時間預算」。

**這個「時間片」怎麼生效？** 靠 tick。`task_tick_fair`（`kernel/sched/fair.c`，就是 Ch 11 說的 `task_tick` 對 fair class 的實作）每個 scheduler tick 被呼叫一次，它呼叫 `entity_tick` → `check_preempt_tick`（純 CFS）：檢查 curr 這次連續跑的實際時間有沒有超過它這一輪該有的 `sched_slice`，超過了就設 `TIF_NEED_RESCHED`（Ch 11 的旗標），下一個搶佔檢查點就會把它換下來。**所以 CFS 的「時間片」不是預先發的配額，是 tick 時動態算、動態判斷「你是不是跑夠了該讓」。**

> **EEVDF 這裡也改了**：EEVDF 引入明確的 `slice`（可由 task 透過 `sched_setattr` 用 `sched_runtime` 請求的「我想要多長的一次上場時間」）與 request/virtual deadline 機制，取代 CFS 這套 `sched_latency / min_granularity` 的隱式時間片計算。但「用 tick 檢查該不該搶佔 curr」這個結構不變。

## 動手：改 nice 看 CPU 分配、gdb 看紅黑樹與 vruntime

三個實驗，把上面的理論全部落到你能親眼看到的東西上。用 Ch 0 的 QEMU + gdb 環境（QEMU 裡若沒有 `stress`，用 busybox 的無窮迴圈或裝一個，下面給不依賴 stress 的替代法）。

### 實驗 A：nice 值真的按 1.25 倍分 CPU 嗎

在 QEMU 的 shell（或任何你有兩顆以上 task 競爭同一顆 CPU 的環境）裡，把兩個 busy loop 綁到**同一顆 CPU**，給不同 nice，看 CPU 佔比：

```sh
# 綁到 CPU 0，避免它們各跑一顆 CPU（那樣看不出競爭）
# 一個 nice=0，一個 nice=5，理論比例 1.25^5 ≈ 3:1
taskset -c 0 nice -n 0 sh -c 'while :; do :; done' &
taskset -c 0 nice -n 5 sh -c 'while :; do :; done' &

top    # 或 ps -o pid,ni,pcpu,comm；看兩者 %CPU 是不是約 75% : 25%
```

如果環境有 `stress`：`taskset -c 0 stress --cpu 1 &` 起兩個、分別 `renice` 成 0 和 5，效果一樣。看到約 3:1 就驗證了 weight 表。改成 nice 差 1（0 vs 1），比例應約 1.25:1；差 10（0 vs 10）應約 9:1。

> **注意**：一定要 `taskset` 綁同一顆 CPU。不綁的話 load balancer（Ch 15）會把兩個 task 分到不同 CPU，各拿 100%，你就看不到「競爭同一顆 CPU 時 vruntime 怎麼分」——這也是初學觀測 CFS 最常見的翻車點。

### 實驗 B：gdb 看 se.vruntime 一路漲

停在 fair class 的記帳核心 `update_curr` 上，看 vruntime 增長：

```gdb
(gdb) target remote :1234
(gdb) source vmlinux-gdb.py
(gdb) break update_curr
(gdb) continue
(gdb) print $lx_current().comm                 # 現在在跑誰
(gdb) print $lx_current().se.vruntime          # 它現在的 vruntime
(gdb) print $lx_current().se.sum_exec_runtime  # 對照：真實累積執行時間
(gdb) continue                                  # 放它跑一輪
(gdb) print $lx_current().se.vruntime          # 再看：漲了
```

想看「權重怎麼影響漲速」，比較一個 nice=0 和一個 nice=19 的 task 的 `se.load.weight`（應分別是 1024 和 15），再各放它們跑一段、對照 vruntime 漲幅——nice=19 那個明明跑一樣久，vruntime 卻漲得凶得多。

### 實驗 C：gdb 看 cfs_rq 的紅黑樹

拿到 CPU 0 的 cfs_rq，看它的樹和 leftmost：

```gdb
(gdb) print $lx_per_cpu(runqueues, 0).cfs                      # CPU 0 的 cfs_rq
(gdb) print $lx_per_cpu(runqueues, 0).cfs.nr_running           # 樹上幾個 se
(gdb) print $lx_per_cpu(runqueues, 0).cfs.min_vruntime         # 基準線
(gdb) print $lx_per_cpu(runqueues, 0).cfs.tasks_timeline.rb_leftmost  # 最左節點（下一個要跑的 se 的 rb_node）
```

`rb_leftmost` 是個 `struct rb_node *`，它內嵌在某個 `sched_entity` 的 `run_node` 欄位裡，用 `container_of` 反推回 se，再反推回 task：

```gdb
# 把 rb_node* 轉回 sched_entity*（run_node 是 se 的成員），再看它的 vruntime
(gdb) set $leftmost = $lx_per_cpu(runqueues, 0).cfs.tasks_timeline.rb_leftmost
(gdb) print container_of($leftmost, struct sched_entity, run_node)->vruntime
# 這個 vruntime 應該是整棵樹最小的（= 下一個上場的）
```

### 實驗 D：讀 /proc/<pid>/sched（不用 gdb）

kernel 已經把每個 task 的排程內部狀態透過 `/proc/<pid>/sched` 攤開給你（`kernel/sched/debug.c`，需 `CONFIG_SCHED_DEBUG`）：

```sh
cat /proc/$$/sched     # 當前 shell 的排程資訊
```

重點看這幾行：`se.vruntime`（虛擬執行時間）、`se.sum_exec_runtime`（真實累積執行時間）、`nr_switches`（被切換幾次）、`se.statistics.wait_sum`（在 rq 裡總共等了多久——這是「排程延遲」的直接量測）。跑實驗 A 的兩個不同 nice 的 task，分別 cat 它們的 `/proc/<pid>/sched`，對照 `vruntime` 和 `sum_exec_runtime` 的比值——nice 越大的，vruntime/真實時間 的比值越大，這就是 weight 縮放的直接證據。

## 對比與取捨

| 設計選擇 | CFS 的方案 | 替代方案 | 為什麼這樣選 |
|---|---|---|---|
| 公平性怎麼定義 | 逼近「理想多工」，用 vruntime 量「落後多少」 | O(1) scheduler 的互動性 heuristic | 單一乾淨規則取代一堆難調的啟發式 |
| 排哪個 task | 永遠挑 vruntime 最小的 | 固定優先權 queue、輪流 | 落後最多的優先補償，公平自然浮現 |
| 存 runnable task | 紅黑樹（key=vruntime）+ leftmost 快取 | 排序陣列 / 鏈結串列 / 多條 queue | O(log n) 插刪 + O(1) 拿最小，平衡兩種操作 |
| 加權（優先權） | 操縱 vruntime **漲速**（weight 縮放） | 直接發不同大小的時間片 | 公平與加權用同一機制，且 task 數變動時自動適應 |
| 時間片 | 動態算（sched_latency / nr_running） | 固定時間片 | task 數暴增時仍能保住互動延遲上限 |
| 排程單位 | `sched_entity`（task 或 group 皆可） | 直接排 task_struct | 讓同套演算法遞迴套用到 group scheduling / cgroup |

## 踩雷集錦

1. **以為你 6.12 跑的是 CFS**——不。6.6 起 fair class 是 **EEVDF**，`kernel/sched/fair.c` 裡 `check_preempt_tick`、`sched_slice`、純「挑 leftmost」那套 CFS 邏輯已經被換掉。這章的 vruntime/紅黑樹/weight 是 EEVDF **繼承**的地基，但別把「永遠挑 vruntime 最小」當 6.12 現況——現況是「挑 eligible 中 virtual deadline 最早的」（Ch 13）。

2. **以為 vruntime = 實際執行時間**——只有 nice=0 的 task 才 1:1。vruntime 是**按 weight 縮放過的虛擬時間**：nice<0 漲得慢、nice>0 漲得快。`se.vruntime` 和 `se.sum_exec_runtime` 是兩個不同的量，前者是排程 key，後者是真實時間。搞混就無法理解「為什麼高優先權 task 拿更多 CPU」。

3. **以為高權重 = 更長時間片**——不。CFS 給高權重 task 更多 CPU，靠的是它 **vruntime 漲得慢 → 更常是最小的 → 更常被挑中**，不是「一次給它跑更久」。方向反了就會誤解整個機制。

4. **以為睡很久的 task 醒來會拿到補償、優先跑很久追回進度**——恰恰相反。`place_entity` 把睡醒的 task 的 vruntime **拉到 min_vruntime 附近**，它睡覺那段的帳一筆勾銷、不補償，只給一點點有上限的互動獎勵。這是**防止**「睡很久回來獨佔 CPU」的機制，不是補償機制。誤解這點，你會答錯「為什麼一個 sleep 完的 process 不會霸佔 CPU」。

5. **在多核上看不出 nice 的效果就以為 nice 沒用**——大機率是兩個 task 被 load balancer 分到不同 CPU，各拿 100%，根本沒在同一顆 CPU 上競爭。nice/vruntime 只在**同一個 cfs_rq 內**的 se 之間比較才有意義。要看效果必須 `taskset` 綁同一顆 CPU（見實驗 A）。

## 進階：再往深一層

- **CFS 也有紅黑樹之外的東西**：本章聚焦單顆 CPU 的 cfs_rq。CFS 還有一整套 **PELT（Per-Entity Load Tracking，逐 entity 負載追蹤）**——`se.avg` 裡的 `load_avg`/`util_avg`，用幾何級數衰減估計每個 se「近期」的負載與 CPU 使用率。它是**負載均衡（Ch 15）**和 **cpufreq 調頻（schedutil，Ch 42）**的輸入，跟 vruntime 是兩套獨立的記帳（vruntime 管「這顆 CPU 上誰先跑」，PELT 管「這個 task 有多重、該不該搬走 / 要不要升頻」）。別把兩者混為一談。

- **`min_vruntime` 為什麼必須單調遞增**：如果它會倒退，那「用它當基準塞進來的新 task」就可能拿到一個比現存 task 都小很多的 vruntime，反而獨佔 CPU——正是它要防的事。單調遞增是這道防線的必要條件。gdb 裡連續讀 `cfs.min_vruntime` 應該只增不減。

- **面試常問**：「CFS 怎麼實現公平？」→ vruntime 追蹤落後、永遠挑最小、紅黑樹 O(log n)。「nice 值怎麼影響排程？」→ 查 `sched_prio_to_weight` 得 weight，weight 反比縮放 vruntime 漲速，差 1 約 1.25 倍 CPU。「為什麼 CFS 用紅黑樹不用 heap / 陣列？」→ 要同時支援 O(log n) 任意插刪和 O(1) 拿最小（leftmost 快取），紅黑樹加快取最平衡。「CFS 和 EEVDF 差在哪？」→ 都用 vruntime/weight/紅黑樹，EEVDF 加 lag（eligibility）和 virtual deadline，改善 CFS 對延遲敏感 task 的支援（Ch 13 展開）。能把這四題串起來，這章就到位了。

- **CFS 被換掉的真正原因**：CFS 的 vruntime 模型保證**長期吞吐量公平**，但對**延遲**沒有直接控制——一個只想「每次跑一小段、但要準時被排到」的互動 task，CFS 只能靠 sched_latency 和睡眠獎勵間接照顧，不夠精準。EEVDF 用 lag 顯式量化「你相對公平份額超支/欠了多少」、用 virtual deadline 讓 task 能請求「我要低延遲、切小片」，把 CFS 的隱式啟發式換成顯式模型。這是 Ch 13 的故事——但你得先有本章的 vruntime 地基才看得懂它在改什麼。

## 動手練習

1. **手算再驗證**：算 nice=−5（weight 3121）和 nice=0（weight 1024）兩個 task 在同一顆 CPU 上各拿多少 CPU（提示：`3121/(3121+1024)` 和 `1024/(3121+1024)`），得約 75.3% : 24.7% ≈ 3:1。再用實驗 A 的 `taskset` + `nice` 跑一次驗證。差多少？想想 tick 粒度、量測時間長短會帶來多少誤差。

2. **gdb 追一次 vruntime 更新**：`break update_curr`，`print` 進去前後的 `$lx_current().se.vruntime`，記下 delta。同時 `print $lx_current().se.load.weight`。換一個不同 nice 的 task 重做，對照「同樣 continue 一次，vruntime 漲幅」和 weight 的反比關係。

3. **從紅黑樹反推下一個要跑的 task**：用實驗 C 的 `container_of` 技巧，從 CPU 0 的 `tasks_timeline.rb_leftmost` 反推到 `sched_entity`、再到 `task_struct`，`print` 出它的 `comm`。然後 `finish` 出 `pick_next_task_fair`，比對它回傳的 task 是不是同一個——驗證「leftmost = 下一個上場」。（注意：6.12 是 EEVDF，pick 的不一定是 leftmost，這正好讓你看出 CFS 和 EEVDF 的差別，是通往 Ch 13 的伏筆。）

4. **弄壞公平性看看**：在 QEMU 裡起 3 個綁同一顆 CPU 的 busy loop，全 nice=0，`cat` 它們的 `/proc/<pid>/sched` 看 `sum_exec_runtime` 是不是大致三等分。然後把其中一個 `renice -n -10`，隔幾秒再看，觀察它的 `sum_exec_runtime` 增速甩開另外兩個多少（理論約 `1.25^10 ≈ 9` 倍）。

## 本章重點整理

- CFS 用 **vruntime** 逼近「理想多工」：跑越久 vruntime 越大，**永遠挑 vruntime 最小的 se** 上場，落後最多的優先補償，公平自然浮現。`update_curr`（`kernel/sched/fair.c`）是記帳核心。
- vruntime 增量 = 實際執行時間 × `NICE_0_WEIGHT / weight`。nice 經 `sched_prio_to_weight[]`（`kernel/sched/core.c`）查得 weight，**nice 差 1 約 1.25 倍 CPU**。權重高 → vruntime 漲得慢 → 拿更多 CPU（不是給更長時間片）。
- runnable 的 se 存在 **`cfs_rq` 的紅黑樹**（`tasks_timeline`，`rb_root_cached`）裡，key = vruntime；**最左節點 = 最小 vruntime = 下一個要跑**，leftmost 被快取所以 O(1) 拿、O(log n) 插刪。排程單位是 `struct sched_entity`（`include/linux/sched.h`），task 和 group 都用它，撐起 group scheduling / cgroup（Ch 50）。
- 新 task / 睡醒 task 由 `place_entity` 把 vruntime 拉到 **`min_vruntime`** 附近，防止「憑空冒出」或「睡很久回來」的 se 獨佔 CPU。時間片是動態算的（`sched_latency / nr_running`，下限 `min_granularity`），`task_tick_fair` 每 tick 檢查該不該搶佔。**這一切 EEVDF 都繼承了，只改「挑誰」的規則——Ch 13 見。**

## 自我檢核

- [ ] 不看筆記，能解釋 vruntime 是什麼、為什麼「永遠挑最小的」就能達到公平
- [ ] 能算出兩個給定 nice 值的 task 在同一顆 CPU 上各拿多少 CPU，並說出「差 1 約 1.25 倍」從哪來
- [ ] 能解釋為什麼「高權重 = vruntime 漲得慢」而不是「高權重 = 更長時間片」
- [ ] 能說出 cfs_rq 為什麼用紅黑樹 + leftmost 快取，各種操作的複雜度
- [ ] 能解釋 `min_vruntime` 的作用，以及睡很久的 task 醒來為什麼**不會**獨佔 CPU
- [ ] 能說清 `sched_entity` 這層抽象為什麼存在（group scheduling），以及它怎麼讓同套演算法套用到 task 和 group
- [ ] 被問「你 6.12 跑的是 CFS 嗎」，能答「不，是 EEVDF，但它繼承 CFS 的 vruntime/weight/紅黑樹」，並說出 EEVDF 多加了什麼
- [ ] 能用 gdb 讀 `se.vruntime`、`cfs.min_vruntime`、從 `rb_leftmost` 反推 task

## 延伸閱讀

### 官方文件

- **[Documentation/scheduler/sched-design-CFS.rst](https://www.kernel.org/doc/html/latest/scheduler/sched-design-CFS.html)**
  - **讀哪裡**：整篇。這是 CFS 設計者對「理想多工 / vruntime / 紅黑樹 / nice→weight」的第一手說明，本章的思想源頭
  - **注意**：6.12 的實際實作已是 EEVDF，這份文件描述的挑選演算法（挑 leftmost）是歷史版本；但 vruntime/weight 概念仍準確，配本章與 Ch 13 對照讀

### 文章 / 論文

- **[An EEVDF CPU scheduler for Linux（LWN）](https://lwn.net/Articles/925371/)** — Jonathan Corbet
  - **讀哪裡**：前半段回顧 CFS 的 vruntime/公平模型、後半解釋 EEVDF 為何取代它
  - **為什麼讀**：是理解「CFS 有什麼不足、EEVDF 怎麼補」最好的一篇，讀完本章接著讀它，正好銜接 Ch 13
  - **前提**：本章的 vruntime/weight 模型

- **[The Linux Scheduler: a Decade of Wasted Cores](https://people.ece.ubc.ca/sasha/papers/eurosys16-final29.pdf)** — Lozi et al.（EuroSys 2016）
  - **讀哪裡**：前兩節對 CFS 資料結構（vruntime、紅黑樹、runqueue）與負載均衡的描述
  - **能學到什麼**：從「CFS 在多核上的 bug」反過來理解它的機制與弱點；也是 Ch 15 負載均衡的極佳前導。注意它批評的是實作 bug，不是 vruntime 模型本身

### 書籍

- **《Linux Kernel Development, 3rd Ed.》** — Robert Love（Addison-Wesley）
  - **讀哪裡**：Process Scheduling 章的「The Completely Fair Scheduler」「Fair Scheduling」小節
  - **為什麼讀**：對 vruntime、targeted latency、min_granularity、weight 的白話解釋最清楚，是本章概念的最佳補充
  - **注意**：書齡較舊，描述的是純 CFS，細節（函式名、EEVDF）以 v6.12 源碼與本章為準

vruntime 模型的地基打好了：你知道公平怎麼被量化、weight 怎麼加權、紅黑樹怎麼挑下一個、min_vruntime 怎麼防獨佔。但 CFS 對「延遲敏感的互動 task」照顧得不夠精準——它只保證長期吞吐公平，管不好「我要準時、每次切一小片」的需求。下一章看 EEVDF 怎麼用 lag（eligibility）和 virtual deadline 兩個新維度，在保留這整套 vruntime 地基的前提下，把延遲也納入公平模型。

→ [Ch 13 EEVDF：lag、eligibility 與 virtual deadline](./13-eevdf.md)
