# Ch 15 — SMP、load balancing、CPU affinity、NUMA

> **目標**：理解一台多核機器上，kernel 怎麼把 task 分散到各個 CPU、什麼時候把 task 從忙的 CPU 搬到閒的 CPU、為什麼「搬」這件事要照硬體拓撲分層來做。學完你能讀懂 `sched_balance_rq()` 的骨架、用 `taskset`/`numactl` 手動控制 task 落點、在 `/proc` 和 `perf` 裡觀察 task 在 CPU 間遷移，並理解為什麼手機的大小核（big.LITTLE）需要一套完全不同的排程邏輯（EAS）。

前面四章（Ch 11–14）都在一顆 CPU 的視角裡打轉：一個 run queue 裡有一堆 task，排程器怎麼挑下一個（CFS/EEVDF）、怎麼切過去（context switch）。這章把鏡頭拉遠——你的機器有 8 核、16 核、手機有 8 個異質核心。**這麼多顆 CPU，task 要放哪一顆？放上去之後要不要搬？** 這就是 SMP 排程與 load balancing 的問題。

## 為什麼需要這個？

回想 Ch 11：Linux 的 run queue 是 **per-CPU** 的。每顆 CPU 有自己的 `struct rq`（`kernel/sched/sched.h`），裡面掛著自己的 CFS 紅黑樹、自己的 `nr_running`。這個設計是刻意的——如果全系統共用一個 run queue，每次排程都要搶同一把鎖，核心一多就變成一個巨大的競爭熱點，根本擴不上去。per-CPU rq 讓每顆 CPU 大部分時間只碰自己的資料，這是 SMP 可擴展性的地基。

但天下沒有白吃的午餐。per-CPU rq 帶來一個新問題：**負載會不均**。

想像一個場景：你 `fork` 出 8 個 CPU 密集的工作，如果它們全被塞進 CPU 0 的 run queue，而 CPU 1–7 的 run queue 是空的，那 CPU 0 上的 8 個 task 輪流跑、每個只拿到 1/8 的算力，另外 7 顆 CPU 卻在睡覺。這台 8 核機器實際只發揮了 1 核的效能。

```
   沒有 load balancing：                有 load balancing：
   CPU0: [t0 t1 t2 t3 t4 t5 t6 t7]      CPU0: [t0]   CPU4: [t4]
   CPU1: [ 空 ]                          CPU1: [t1]   CPU5: [t5]
   CPU2: [ 空 ]                          CPU2: [t2]   CPU6: [t6]
   ...                                   CPU3: [t3]   CPU7: [t7]
   有效算力 ≈ 1 核                        有效算力 ≈ 8 核
```

**load balancing 就是負責在 per-CPU rq 之間搬 task，讓各 CPU 的負載盡量均衡的機制。** 它不改變「每顆 CPU 自己挑下一個跑誰」這件事（那是 CFS/EEVDF 的工作），它只回答「哪些 task 該待在哪顆 CPU 上」。

但「搬 task」不是免費的。搬走一個 task，它在原 CPU 上暖好的 L1/L2 cache 全部作廢，到新 CPU 上要重新把資料抓進 cache（cache miss 是幾十到幾百個 cycle）；如果搬到另一個 NUMA node，連它的記憶體都在遠端，每次存取都要跨 node。所以 load balancing 的核心張力是：**均衡負載的好處 vs. 破壞 cache/記憶體局部性的代價**。整章的設計都是在這條線上找平衡。

## 先建立直覺：搬到「近」的 CPU 便宜，搬到「遠」的 CPU 貴

在讀源碼前，先把硬體拓撲的心智模型建起來，因為整個 load balancing 的分層設計都是它的倒影。

現代 CPU 不是一堆對等的核心平鋪在一起，而是有層次的：

```
             ┌──────────────────── NUMA node 1 ────────────────────┐
   ┌──────── NUMA node 0 ────────┐   （自己的記憶體控制器 + DRAM）    │
   │                             │   │                              │
   │  ┌──── LLC / L3 共享 ────┐   │   │  ┌──── LLC / L3 共享 ────┐    │
   │  │ ┌ core0 ┐  ┌ core1 ┐ │   │   │  │ ┌ core4 ┐  ┌ core5 ┐ │    │
   │  │ │HT0 HT1│  │HT2 HT3│ │   │   │  │ │HT8 HT9│  │  ...  │ │    │
   │  │ └───────┘  └───────┘ │   │   │  │ └───────┘  └───────┘ │    │
   │  │   L1/L2      L1/L2   │   │   │  │                      │    │
   │  └──────────────────────┘   │   │  └──────────────────────┘    │
   └─────────────────────────────┘   └──────────────────────────────┘

   距離越往外，「搬 task 過去」的代價越大：
   同 core 的兩個 HT（超執行緒）── 共享 L1/L2，搬過去幾乎不痛
   同 LLC 的不同 core         ── 共享 L3，L1/L2 要重建，中等痛
   同 NUMA node 不同 LLC       ── 連 L3 都不共享，較痛
   跨 NUMA node               ── cache 全冷 + 記憶體變遠端，最痛
```

- **SMT / 超執行緒（Simultaneous Multi-Threading，Intel 叫 Hyper-Threading）**：一個實體核心對作業系統呈現成兩個邏輯 CPU，它們共用同一套執行單元、L1、L2。兩個 HT 之間搬 task，cache 幾乎不用重建。
- **LLC（Last-Level Cache，通常是 L3）**：一群 core 共享同一塊 L3。同一個 LLC 內搬 task，L3 裡的資料還在，只有 L1/L2 要重建。
- **NUMA node（Non-Uniform Memory Access）**：一台大機器有多個 CPU socket，每個 socket 有自己直連的記憶體控制器和 DRAM。CPU 存自己 node 的記憶體快，存別的 node 的記憶體要繞過 interconnect（Intel 的 UPI、AMD 的 Infinity Fabric），慢得多。跨 node 搬 task，不只 cache 全冷，連它的記憶體都變成「遠端記憶體」。

**核心直覺一句話**：搬 task 的代價隨拓撲距離增加。所以 kernel 的策略是——**優先在近的層級平衡（便宜），只有近處實在擺不平才往遠處搬（貴，且要更明顯的不平衡才值得）**。這就是為什麼 load balancing 要按拓撲分層，也就是接下來的 scheduling domains。

## 怎麼量「負載」：PELT

在談「搬」之前得先定義「負載」。最天真的定義是「run queue 裡有幾個 runnable task」（`nr_running`）。但這太粗糙：一個 99% 時間在睡、偶爾醒來跑 1ms 的 task，和一個把 CPU 吃滿的 task，都算「1 個 runnable task」，但它們對 CPU 的實際壓力天差地遠。

Linux 用 **PELT（Per-Entity Load Tracking，per-entity 負載追蹤）** 來量。核心程式在 `kernel/sched/pelt.c`，資料結構是 `include/linux/sched.h` 裡 `struct sched_entity` 內的 `struct sched_avg`。

PELT 的想法：把時間切成 **1024 微秒（約 1ms）** 一個週期，記錄每個 entity（task 或 task group）在每個週期裡「有多少比例在 runnable / running」，然後對歷史做**幾何衰減加權**——越近的週期權重越高，越舊的越不重要。衰減係數選得讓大約 **32 個週期（約 32ms）前的貢獻衰減到一半**（half-life ≈ 32ms，對應 `y^32 = 0.5`，`y ≈ 0.978`）。

`struct sched_avg` 裡兩個最關鍵的量：

- **`util_avg`（utilization，使用率）**：這個 entity 實際「用了多少 CPU 算力」。範圍 0 ~ `SCHED_CAPACITY_SCALE`（值為 1024，代表一顆滿載 CPU）。一個把單核吃滿的 task，`util_avg` 會趨近 1024。這是 CPU 頻率調節（cpufreq，Ch 42）和大小核選核的主要依據。
- **`load_avg`（load，負載）**：`util_avg` 再乘上這個 entity 的權重（weight，由 nice 值決定，見 Ch 12）。load balancing 比較各 CPU 忙不忙時看的是 `load_avg`——因為一個 nice -20 的高優先權 task 即使 util 不高，也應該「佔更多份量」。

為什麼要幾何衰減而不是簡單平均？因為排程要反應**最近**的行為。一個 task 五秒前很忙、現在閒下來了，你不該還把它算成很忙。幾何衰減讓「久遠的過去自動淡出」，用固定大小的 `struct sched_avg` 就能維護一個「偏重近期的移動平均」，不用存整段歷史。這是 PELT 相對於早期「瞬時 `nr_running`」的關鍵進步（2.6 時代的 O(1) scheduler 沒有這種細緻的負載量測）。

> 每顆 CPU 的 rq 也有自己的 `sched_avg`（`rq->cfs.avg`），是掛在它上面所有 entity 的 PELT 匯總。load balancing 就是比較各 rq 的這些匯總量。

## Scheduling domains 與 groups：把拓撲寫進資料結構

前面說「按拓撲分層平衡」，kernel 怎麼知道拓撲、又怎麼分層？答案是開機時建一棵 **scheduling domain（排程域）** 樹，把硬體拓撲固化成資料結構。相關程式在 `kernel/sched/topology.c`，結構定義在 `include/linux/sched/topology.h` 的 `struct sched_domain`。

每顆 CPU 都有一疊（由下而上）的 `sched_domain`，每一層對應拓撲的一個層級：

```
   CPU0 的 sched_domain 疊（由下往上，涵蓋範圍越來越大）：

   ┌─ NUMA domain ─────────────────────────────┐  涵蓋所有 CPU（跨 node）
   │  group0 = node0 的 CPU   group1 = node1..  │
   │  ┌─ MC domain (LLC) ──────────────────┐    │  涵蓋同一個 LLC 的 CPU
   │  │  group0=core0  group1=core1  ...   │    │
   │  │  ┌─ SMT domain ──────────────┐     │    │  涵蓋同一實體核心的 HT
   │  │  │  group0 = HT0  group1=HT1 │     │    │
   │  │  └───────────────────────────┘     │    │
   │  └────────────────────────────────────┘    │
   └────────────────────────────────────────────┘

   每一層 domain 內部再切成若干 sched_group（虛線框），
   平衡是「把負載在 group 之間搬平」。
```

兩個關鍵字：

- **`sched_domain`**：一個平衡的「範圍」。SMT domain 只涵蓋一個實體核心的兩個 HT；MC（Multi-Core）domain 涵蓋一個 LLC 內的所有 core；NUMA domain 涵蓋跨 node 的所有 CPU。每一層都有自己的平衡參數（多久平衡一次、要多不平衡才值得搬）。
- **`sched_group`**：一個 domain 內部又切成若干 group，平衡是在 group **之間**進行的。例如 MC domain 內，每個 core 是一個 group；平衡就是看「哪個 core group 太忙、哪個太閒」，然後從忙的 group 拉 task 到閒的 group。

為什麼要 group 而不是直接比 CPU？因為在上層，你要比較的是「整群 CPU」的總負載。在 NUMA domain 裡，一個 group 是「整個 node」——你先決定「node 0 比 node 1 忙」，才在 node 內部往下層 domain 細分。這個「先粗後細、由近而遠」的結構，正是前面直覺的實作。

每一層 domain 帶不同的 **flags**（`SD_*`，見 `include/linux/sched/sd_flags.h`），控制這一層的行為。幾個關鍵：

- `SD_BALANCE_NEWIDLE`：這一層允許 newidle balance（CPU 快閒下來時主動找活）。近的層級（SMT/MC）通常開，遠的（NUMA）可能關——因為跨 node 拉活太貴，不值得為了一瞬間的 idle 去做。
- `SD_SHARE_CPUCAPACITY`：這一層的 CPU 共享算力（SMT 層）。
- `SD_SHARE_LLC`（6.x 起，舊稱 `SD_SHARE_PKG_RESOURCES`）：這一層共享 LLC。wake_affine（後面講）用它判斷「喚醒者和被喚醒者是否共享 cache」。
- `SD_NUMA`：這是 NUMA 層，平衡時要考慮記憶體局部性，門檻更高。

你可以直接在跑著的系統上看這棵樹（需 `CONFIG_SCHED_DEBUG`，6.12 常態開啟於 debugfs）：

```bash
# 每個 CPU 每一層 domain 的名字與 flags
cat /sys/kernel/debug/sched/domains/cpu0/domain0/name    # 例如 SMT
cat /sys/kernel/debug/sched/domains/cpu0/domain1/name    # 例如 MC
cat /sys/kernel/debug/sched/domains/cpu0/domain0/flags
```

## 底層機制：什麼時候搬、怎麼搬

負載會不均，但 kernel 不會時時刻刻檢查（那本身就是負擔）。平衡在**四個時機**被觸發，各對應不同情境：

### 1. 週期性平衡（periodic / tick-driven）

每次時鐘中斷（scheduler tick，Ch 32），`sched_tick()`（`kernel/sched/core.c`）會呼叫目前排程類別的 `task_tick`；對 CFS 是 `task_tick_fair()`，它會呼叫 `sched_balance_trigger()`。這個函式不直接做平衡（中斷上下文不宜做重活），而是判斷「這顆 CPU 到了該平衡的時間」就**觸發一個 `SCHED_SOFTIRQ` softirq**（Ch 30）。

softirq 的處理函式是 `sched_balance_softirq()`（`kernel/sched/fair.c`），它呼叫 `sched_balance_domains()`，由下而上走過這顆 CPU 的每一層 `sched_domain`，每一層到了它的間隔（`sd->balance_interval`，近的層間隔短、遠的長）就對那一層呼叫核心函式 **`sched_balance_rq()`**。

`sched_balance_rq()` 是整章的主角（`kernel/sched/fair.c`；這套負載均衡函式在 6.9 前叫 `load_balance`/`find_busiest_group` 等，6.12 已統一改名為 `sched_balance_*` 系列），骨架是：

1. `sched_balance_find_src_group()`：在這一層的所有 sched_group 裡，找出**最忙的 group**（用 PELT 匯總的 `load_avg` 加上一堆啟發式：group 是否 overloaded、是否有 imbalance）。若各 group 已經夠平衡，直接返回，不搬。
2. `sched_balance_find_src_rq()`：在最忙的 group 裡，找出**最忙的那顆 CPU 的 rq**。
3. `detach_tasks()` / `move_task`：從最忙的 rq 上，挑一些 task 搬到「本 CPU（正在做平衡的這顆，通常是相對閒的）」的 rq。挑哪些 task 有講究——`can_migrate_task()` 會擋掉三種不該搬的：正被 pin 住（affinity 不允許，見下節）的、cache 還很熱（`task_hot()`，剛跑過還沒多久）的、正在跑的。
4. 若怎麼都搬不動（例如唯一能搬的 task 正在忙的 CPU 上跑），會設 `active_balance`，喚醒忙 CPU 上的 **migration/N kernel thread**（`stop_machine` 機制），強制把那顆 CPU 上正在跑的 task 頂下來搬走。

> `sched_balance_rq()` 是**「拉」（pull）模型**：由相對閒的 CPU 主動去忙的 CPU 拉 task 過來，而不是忙的 CPU 主動推。這樣做的好處是——正在忙的 CPU 不用分心去管別人，做平衡工作的永遠是那顆「反正也沒事幹」的 CPU。

### 2. Newidle balance（CPU 快閒下來，先找活幹）

當一顆 CPU 的 run queue 空了、`__schedule()`（Ch 14）發現沒有 task 可跑、即將進 idle 之前，會先呼叫 **`sched_balance_newidle()`**（6.x 的函式名，早期叫 `idle_balance()`，在 `kernel/sched/fair.c`）。它會由下而上在各層 domain 上試著拉一個 task 過來——**與其閒著，不如去別人那裡搬個活來做**。

這是延遲敏感的路徑：CPU 即將 idle，這幾百奈秒的平衡若能拉到活，就避免了一次「進 idle 又馬上被喚醒」的浪費。但它也有代價（做平衡本身要花時間、可能破壞 cache 局部性），所以有 `sysctl_sched_migration_cost`（預設 500000 ns = 0.5ms）等啟發式來決定「值不值得為 newidle 做平衡」。近層（SMT/MC）通常放行，NUMA 層因為太貴常被 `SD_BALANCE_NEWIDLE` 關掉。

### 3. Fork / exec 時選 CPU（select_task_rq）

一個新 task 被 `fork` 出來、或 `exec` 換了程式，kernel 要決定它第一次放哪顆 CPU 的 rq。入口是 **`select_task_rq()`**（`kernel/sched/core.c`），對 CFS 轉呼叫 **`select_task_rq_fair()`**（`kernel/sched/fair.c`）。fork 的情境（`SD_BALANCE_FORK`）傾向找一顆「最閒的 CPU」放，因為新 task 沒有 cache 局部性可言（它還沒跑過），純粹追求負載均衡。

### 4. Wake 時（wake_affine：利用 cache 熱度）

這是最微妙的一個。當 task B 被 task A 喚醒（例如 A `write` 到一個 pipe 把等在 `read` 的 B 叫醒），kernel 面對一個選擇：**B 放回它上次跑的 CPU（cache 可能還熱），還是放到 A 附近（生產者-消費者常互相存取同一份資料，放一起能共享 cache）？**

`select_task_rq_fair()` 在 wakeup 情境會呼叫 **`wake_affine()`**（`kernel/sched/fair.c`）來裁決。核心啟發式：如果 A 和 B 共享 LLC（`SD_SHARE_LLC`），把 B 拉到 A 這邊通常划算——因為它們八成在傳資料，放同一個 LLC 能命中彼此的 cache。這對 IPC 密集的 workload（大量小訊息來回的 client/server、pipe chain）影響很大。

> **wake_affine 是把雙面刃**。它假設「喚醒關係 = 資料共享關係」，對 producer/consumer 成立；但對「A 只是叫醒一堆彼此無關的 worker」的情境，硬把它們全拉到 A 附近反而造成本地過載。這類「wakeup 全擠在一顆 CPU」的病態，是實務上排程調校的常見主題。

把四個時機串起來看：**fork/exec/wake 決定 task 的「初始落點」和「醒來落點」（在放進 rq 的那一刻就選好，成本低）；週期性和 newidle balance 是「事後修正」（task 已經在跑，發現不均了再搬，成本高）。** 前者能省事就別留給後者。

## CPU affinity：手動把 task 釘在指定 CPU

上面全是 kernel 自動決定。但有時你比 kernel 更懂你的 workload，想手動指定「這個 task 只准跑在某幾顆 CPU」。這叫 **CPU affinity（CPU 親和性）**，底層是每個 task 的一個 **cpumask**（`struct cpumask`，接 Ch 7 的 per-CPU 與 bitmask 基礎）。

`task_struct`（`include/linux/sched.h`）裡有 `cpumask_t *cpus_ptr`（指向）與 `cpumask_t cpus_mask`（實際的位元圖），每一個 bit 對應一顆邏輯 CPU，bit 為 1 代表「允許跑在這顆」。所有選核路徑（`select_task_rq`、`can_migrate_task`）都會檢查這個 mask——**affinity 是硬約束，load balancing 再想搬也不能搬到 mask 外的 CPU**。

使用者空間的介面：

```bash
# taskset：查看/設定 affinity
taskset -pc 1234              # 查 PID 1234 目前允許的 CPU（-c 用列表格式，如 0-3）
taskset -pc 2,3 1234          # 把 PID 1234 限制到只能跑 CPU 2 和 3
taskset -c 0 ./myprog         # 直接啟動一個只跑在 CPU 0 的程式

# 對應的 syscall：sched_setaffinity(2) / sched_getaffinity(2)
```

在 kernel 這一側，`sched_setaffinity()`（`kernel/sched/core.c`）改的就是 `cpus_mask`，然後若當前 CPU 已不在新 mask 內，會立刻把 task 遷走。

驗證 affinity 生效，直接讀 `/proc`：

```bash
grep -i cpus_allowed /proc/1234/status
# Cpus_allowed:       0c            ← bitmask（十六進位），0xc = 0b1100 = CPU 2,3
# Cpus_allowed_list:  2-3           ← 人類可讀格式
```

### 為什麼有時手動 pin 比讓 kernel 自動平衡好

kernel 的 load balancer 是**通用**的啟發式，它不知道你的 workload 特性。幾個手動 pin 勝出的經典情境（接 `perf_bench` 課的效能思維）：

- **避免 migration 抖動**：延遲敏感的 task（低延遲交易、即時音訊、DPDK 網路處理）最怕被搬——一搬就是幾百 cycle 的 cache 重建 + 可能的 NUMA 遠端存取。把它 pin 死在一顆 CPU，`util` 曲線平滑得多。
- **NUMA 局部性**：如果你已經用 `numactl` 把一個 task 的記憶體綁在 node 0，那就該把 task 也 pin 在 node 0 的 CPU，別讓 balancer 好心把它搬到 node 1（記憶體就變遠端了）。
- **隔離 CPU 給關鍵工作**：`isolcpus=` 開機參數把某幾顆 CPU 從 kernel 的 load balancing **完全排除**——這些 CPU 平常不會被塞任何 task，你手動 pin 上去的即時工作獨佔它，不會被隨機的背景 task 干擾。這是 `-rt`（Ch 31）與低延遲部署的常規手法。搭配 `nohz_full=`（Ch 32 的 tickless）可以連時鐘中斷都幾乎消掉，讓那顆 CPU 幾乎不被打斷。

> pin 也會踩雷：pin 太死會反過來害了自己。如果你把 8 個 task 全 pin 在同一顆 CPU，load balancer 想幫你也搬不走（affinity 擋著），那顆 CPU 就爆炸而其他 CPU 閒著。affinity 是「我確定我比 kernel 懂」時才用的工具，不是預設就該開的東西。

## NUMA：讓 CPU 和它的記憶體待在同一個 node

到目前為止,「負載」只算了 CPU 時間。但在 NUMA 機器上還有第二個維度：**記憶體在哪個 node**。一個 task 就算 CPU 負載平衡得完美，如果它跑在 node 0 而資料全在 node 1，每次記憶體存取都要跨 interconnect，效能一樣爛。

理想狀態是 **CPU 和它常存取的記憶體在同一個 node**。達成這件事有兩條路：

- **搬 task**：把 task 移到它記憶體所在的 node。
- **搬 page**：把 task 常存取的記憶體頁遷移到 task 所在的 node（接 Ch 17 的 zone / node 概念、Ch 20 的 page migration）。

Linux 的 **automatic NUMA balancing（自動 NUMA 平衡）**（`mm/`、`kernel/sched/fair.c` 裡的 `task_numa_*` 系列，由 `sysctl kernel.numa_balancing` 控制，多數發行版預設開）兩條路都走：

1. **NUMA hinting fault**：週期性地把 task 的一些 page table entry 標成「不可存取」（PROT_NONE 的變體）。當 task 下次碰到這些頁，觸發一個特殊的 minor page fault，kernel 藉此**記錄「這顆 CPU（在哪個 node）存取了這一頁（在哪個 node）」**。累積這些統計，kernel 就知道每個 task 主要在存取哪個 node 的記憶體。
2. **依統計決策**：若 task 大多存取遠端 node，就嘗試把 task 搬到那個 node（`task_numa_migrate()`），或把那些頁搬到 task 這邊（`migrate_misplaced_folio()`）。還有 **NUMA task grouping**——把「常存取同一批記憶體」的 task 群聚到同一個 node。

這套是**盡力而為的自動最佳化**，有量測開銷（那些故意製造的 fault 本身要花時間），所以它是啟發式、會漸進收斂而非瞬間到位。對延遲極度敏感、你又確知記憶體佈局的 workload，通常關掉自動 NUMA balancing、改用手動 `numactl` 硬綁：

```bash
numactl --hardware                      # 看有幾個 node、各有哪些 CPU 和多少記憶體
numactl --cpunodebind=0 --membind=0 ./myprog
#   ↑ 把程式的 CPU 和記憶體都綁在 node 0：CPU 和記憶體保證同 node，零跨 node

lscpu                                    # 也會列 NUMA node 對應的 CPU 範圍（NUMA node0 CPU(s): ...）
```

`numactl --membind` 走的是 mempolicy（`mm/mempolicy.c`，`set_mempolicy`/`mbind` syscall），跟排程的 affinity 是兩套獨立機制——一個管記憶體從哪個 node 配、一個管 task 在哪顆 CPU 跑。在 NUMA 機器上調效能，兩個要一起設才有意義。

## 動手：觀察 task 在 CPU 間遷移

這節全在使用者空間就能做，不用進 QEMU/gdb（雖然你也可以 `b sched_balance_rq` 停下來看）。目標是親眼看到 task 被搬、以及被 pin 之後不再被搬。

### 看拓撲

```bash
lscpu                          # 概覽：核心數、每核 thread 數、LLC、NUMA node
lscpu -e                       # 逐邏輯 CPU 列出它屬於哪個 core / socket / node（CPU-CORE-SOCKET-NODE-...）
cat /sys/devices/system/cpu/cpu0/topology/thread_siblings_list   # cpu0 的 SMT 兄弟
```

### 看一個 task 現在跑在哪顆 CPU

`/proc/<pid>/stat` 的**第 39 欄**（`processor`）是這個 task 上次跑在哪顆 CPU。寫個小迴圈追蹤某個 task 的落點：

```bash
# 起一個吃 CPU 的背景 task
yes > /dev/null &
PID=$!

# 每 0.2 秒印一次它現在在哪顆 CPU（第 39 欄）
while kill -0 $PID 2>/dev/null; do
    awk '{print "cpu=" $39}' /proc/$PID/stat
    sleep 0.2
done
```

在一台空閒機器上單一 `yes`，你會看到它大致固定（cache 熱、balancer 不想動它）。現在製造壓力，逼 balancer 出手：

```bash
# 開比核心數更多的忙 task，逼 load balancer 把它們攤開
for i in $(seq 1 $(( $(nproc) * 2 ))); do yes > /dev/null & done
# 再追蹤某個 PID 的第 39 欄，這次你會看到它偶爾跳到別的 CPU（被搬了）
```

### pin 之後不再被搬

```bash
yes > /dev/null &
PID=$!
taskset -pc 2 $PID             # 釘死在 CPU 2
grep Cpus_allowed_list /proc/$PID/status   # 應顯示 2
# 再追第 39 欄，現在無論怎麼壓測，它都黏在 cpu=2 不動
```

### 用 perf 量遷移次數

`perf` 有一個直接的 counter：`sched:sched_migrate_task` tracepoint，以及 `cpu-migrations` 這個軟體事件（接 `observability_tools` 課）。

```bash
# 統計一段命令期間發生幾次 CPU migration
perf stat -e sched:sched_migrate_task -a sleep 5

# 或針對某個 workload，看它自己的 migration 數
perf stat -e cpu-migrations ./myprog

# 想看是誰被搬、從哪搬到哪：
perf record -e sched:sched_migrate_task -a -- sleep 5
perf script          # 每一行是一次遷移：comm/pid、orig_cpu → dest_cpu
```

把 pin 前後的 `cpu-migrations` 數字對比一下，你就有了「affinity 確實壓掉了遷移」的量化證據——這正是 `perf_bench` 課教的「用實測破除迷思」，而不是嘴上說 pin 有用。

## x86 vs ARM64：big.LITTLE 與 EAS（MTK 手機晶片的核心）

到目前為止我們默認**所有 CPU 算力相同**（SMP，Symmetric Multi-Processing，對稱多處理）。桌面/伺服器的 x86 大致成立。但手機 SoC——尤其 MTK 天璣、高通這類 ARM64 晶片——是 **big.LITTLE / DynamIQ 異質多核**：幾顆高效能大核（跑得快、耗電高）配幾顆節能小核（慢、省電），甚至三叢集（prime + big + little）。這時「對稱」的假設崩了，排程要多考慮兩件 SMP 不管的事：**每顆 CPU 算力不同、而且要省電**。

### CPU capacity（每顆核心的算力不等）

kernel 給每顆 CPU 一個 **capacity** 值（`rq->cpu_capacity`，滿值 `SCHED_CAPACITY_SCALE = 1024`）。小核的 capacity 可能只有 400，大核是 1024。這來自 device tree 的 `capacity-dmips-mhz` 屬性（接 `arm` 課的 device tree、Ch 39 platform driver）。有了 capacity，前面所有「負載」的判斷都要改成「相對於這顆 CPU 的 capacity 有多滿」——一個 `util_avg=500` 的 task 放小核（cap 400）會爆，放大核（cap 1024）綽綽有餘。這叫 **capacity-aware scheduling**。

### EAS（Energy Aware Scheduling，能耗感知排程）

手機的排程目標和伺服器根本不同：伺服器要**吞吐/公平**，手機要**在滿足效能需求的前提下最省電**（電池就那麼大）。**EAS** 就是為此而生（`kernel/sched/fair.c` 的 `find_energy_efficient_cpu()`，靠 **Energy Model** `drivers/opp/` + `kernel/power/energy_model.c` 提供每顆 CPU 在各頻率下的功耗數據）。

EAS 改寫了 wakeup 時的選核邏輯：不再單純找「最閒的 CPU」，而是**估算「把這個 task 放到候選 CPU 上，整個系統的能耗增量是多少」，選增量最小的那顆**。實務結果就是：

- 輕量、背景的 task（低 `util_avg`）→ 塞小核，大核能維持低頻甚至休眠，省電。
- 重量、前景的 task（滑動、遊戲、相機，高 `util_avg`）→ 放大核，跑得快、及時完成再讓 CPU 降頻（race-to-idle）。

> **EAS 只在「系統沒有 overloaded」時生效**。一旦所有 CPU 都忙翻（`overutilized`），省電讓位給效能，排程退回傳統 SMP 的「攤平負載」邏輯——因為此時每顆核都要用，沒有省電空間可談。這個「平時省電、忙時搶效能」的切換，是理解手機為什麼有時很省電、有時風扇（如果有的話）狂轉的關鍵。

對 MTK 韌體工程師，這一段是日常：BSP 裡 device tree 的 CPU capacity、cluster 拓撲、OPP（Operating Performance Points，頻率/電壓對）表、Energy Model，全都餵給這套排程器。調不好——大核該醒沒醒（掉幀）、小核跑重活（卡頓）、或該睡不睡（燒電）——都是這裡的鍋。x86 世界近年也開始碰到類似問題（Intel 的 P-core/E-core + Thread Director），但 ARM 手機是 big.LITTLE 的原生戰場，EAS 的動力來源。

## 對比與取捨

| 機制 | 誰決定落點 | 觸發時機 | 主要好處 | 主要代價 / 風險 |
|---|---|---|---|---|
| 週期性 load balance | kernel（pull） | scheduler tick → SCHED_SOFTIRQ | 修正累積的不均，全域攤平 | 搬 task 破壞 cache 局部性 |
| newidle balance | kernel（pull） | CPU 即將 idle | 避免 CPU 閒著、減少 idle 抖動 | 平衡本身有延遲，NUMA 層太貴常關 |
| select_task_rq（fork/exec） | kernel | 新 task 誕生 | 一開始就放對地方，省後續搬動 | fork 情境無 cache 可依，純看負載 |
| wake_affine（wakeup） | kernel | task 被喚醒 | producer/consumer 共享 cache | 誤判時把無關 task 擠在一起過載 |
| CPU affinity（taskset） | 使用者手動 | 明確設定 | 消除 migration、鎖定 NUMA 局部性 | pin 錯造成人為不均，喪失彈性 |
| isolcpus | 開機參數 | 開機固定 | 完全隔離、獨佔關鍵 CPU | 該 CPU 退出通用排程，利用率可能下降 |
| automatic NUMA balancing | kernel | 週期 hinting fault | 自動修正 CPU/記憶體錯位 | fault 開銷、收斂慢、非最佳 |
| numactl（手動 NUMA） | 使用者手動 | 明確綁定 | 確定性的局部性 | 需人工掌握記憶體佈局 |
| EAS（big.LITTLE） | kernel | wakeup + capacity/energy model | 省電、大小核各司其職 | 需正確的 device tree/energy model；overload 時失效 |

## 踩雷集錦

1. **「per-CPU run queue = 每顆 CPU 自己排自己」就以為沒有全域協調**。錯。per-CPU rq 只是讓「挑下一個跑誰」不搶全域鎖；跨 CPU 的**負載均衡**是另一套獨立機制（load balancing）在背後把 task 搬來搬去。兩者分工：CFS/EEVDF 管「這顆 CPU 挑誰」，load balancer 管「誰該在哪顆 CPU」。

2. **以為「負載」就是 `nr_running`（runnable task 數）**。真正的度量是 PELT 的 `load_avg`/`util_avg`——幾何衰減的使用歷史，還乘上 nice 權重。一個一直睡的 task 和一個吃滿 CPU 的 task 都是「1 個 runnable」，但 PELT 給它們天差地遠的 load。看錯度量，就理解不了 balancer 為什麼那樣搬。

3. **以為 load balancer 會把負載搬到「絕對平均」**。不會，而且不該。搬 task 有代價（cache/NUMA），所以 balancer 有 imbalance 門檻——**不夠不平衡就不搬**。你看到各 CPU 負載不完全相等是正常的，那是「搬的代價 > 均衡的收益」的刻意結果，不是 bug。

4. **pin 了 task 卻沒 pin 記憶體，或反過來**。在 NUMA 機器上只用 `taskset` 把 task 綁 node 0 的 CPU，但記憶體被配到 node 1（或反之），效能反而可能更差。affinity（CPU）和 mempolicy（記憶體）是兩套獨立機制，NUMA 調校要**兩個一起設**（`numactl --cpunodebind --membind` 一次搞定）。

5. **在手機/異質核心上套用 x86 的直覺**。x86 SMP 假設所有 CPU 對等，big.LITTLE 不成立。「把 task 均勻攤到所有核」在手機上是錯的目標——你會把重活丟給撐不住的小核、或讓大核為了輕活整天醒著燒電。手機排程的目標是 capacity-aware + 省電（EAS），不是攤平。

6. **以為 automatic NUMA balancing 一定讓事情更好**。它有量測開銷（故意製造的 hinting fault），對記憶體存取模式亂跳、或本來就綁好的 workload，反而是純負擔。延遲敏感、佈局已知的場景，常見做法是**關掉自動、改手動 numactl**。

## 進階：再往深一層

- **`sched_balance_rq()` 的 imbalance 計算細節**：`calculate_imbalance()`（`kernel/sched/fair.c`）依 group 的類型（`group_overloaded`、`group_imbalanced`、`group_has_spare` 等 `group_type`）算出「該搬多少負載」。這套 6.x 重寫過（LWN 的「The load balancer rework」系列），值得對照源碼讀。
- **`sched_migration_cost` 這個旋鈕**：`/proc/sys/kernel/sched_migration_cost_ns`（預設 500000）決定「task 跑了多久之內算 cache 還熱、不該搬」。調大 = 更黏（少遷移、cache 友善）、調小 = 更愛搬（更均衡）。這是效能調校常碰的旋鈕。
- **`stop_machine` 與 active balance**：當 task 正在忙 CPU 上跑、pull 模型搬不動它時，靠 per-CPU 的 `migration/N` stopper thread（最高優先權）把它頂下來搬。這也是 CPU hotplug（下線一顆 CPU 時把它的 task 全部撤走）用的機制。
- **面試常問**：「per-CPU run queue 為什麼比全域 run queue 好？代價是什麼？」（答：擴展性 vs. 負載不均，後者用 load balancing 補）。「wake_affine 在解決什麼問題？什麼時候會害了效能？」「big.LITTLE 上為什麼不能用 x86 的排程策略？EAS 的目標函數是什麼？」——這些對 MTK / 手機 SoC 職位是高頻題。
- **`schedutil` cpufreq governor 與 PELT 的關係**（接 Ch 42）：`util_avg` 不只餵給選核，也餵給頻率調節——CPU 該跑多快由 `util` 決定。排程和調頻在 6.x 是深度耦合的（schedutil 直接讀 PELT），這是「race to idle」省電策略的實作基礎。

## 動手練習

1. **畫出你機器的 domain 樹**：`lscpu -e` 加上 `cat /sys/kernel/debug/sched/domains/cpu0/domain*/name`，把你這台機器 CPU 0 的每一層 domain（SMT？MC？NUMA？）和涵蓋範圍畫出來。有 NUMA 就用 `numactl --hardware` 對照。畫完你就把本章的拓撲圖具體化到自己的硬體了。

2. **量化 pin 的效果**：跑一個會被搬來搬去的 workload（`stress-ng --cpu $(nproc)` 或多開 `yes`），用 `perf stat -e cpu-migrations` 量遷移次數；再把它們全 `taskset` pin 到固定 CPU，重量一次。用數字說明 affinity 壓掉了多少遷移。

3. **看 wake_affine 動作**：寫一對 producer/consumer（一個 `write` pipe、一個 `read` pipe），用 `perf record -e sched:sched_migrate_task` 或直接追兩個 PID 的 `/proc/<pid>/stat` 第 39 欄，觀察它們是否被排到同一顆/相鄰 CPU（共享 LLC）。

4. **gdb 停在 `sched_balance_rq`**（QEMU，接 Ch 0）：`b sched_balance_rq`，在 QEMU 裡壓測製造不均，停下來後 `p sd->name`、`bt` 看是哪一層 domain 觸發、被誰呼叫進來的。對照本章「四個觸發時機」認出你停在哪一條路徑上。

5. **（有 NUMA 才做）故意製造錯位**：`numactl --membind=1 --cpunodebind=0 ./mem_heavy_prog`（記憶體在 node 1、CPU 在 node 0），量它的執行時間；再改成 `--membind=0 --cpunodebind=0`（同 node）重量。跨 node 的記憶體懲罰有多大，你會親眼看到。

## 本章重點整理

- per-CPU run queue 解決了排程的可擴展性，但帶來負載不均；**load balancing 在 rq 之間搬 task 來補**，核心張力是「均衡收益 vs. cache/NUMA 局部性代價」。
- kernel 按硬體拓撲建 **scheduling domain 樹**（SMT → MC/LLC → NUMA），**優先在近層平衡（便宜），遠層要更不平衡才搬（貴）**。負載用 **PELT** 的 `util_avg`/`load_avg` 量（幾何衰減的使用歷史，非瞬時計數）。
- 平衡在**四個時機**觸發：週期性（tick→SCHED_SOFTIRQ→`sched_balance_rq`）、newidle、fork/exec 選核（`select_task_rq`）、wakeup（`wake_affine`）。`sched_balance_rq()` 是 pull 模型，由閒 CPU 主動拉。
- **CPU affinity**（`taskset`/cpumask/`isolcpus`）和 **NUMA**（`numactl` + automatic NUMA balancing）是手動控制落點與記憶體局部性的工具，延遲敏感/已知佈局的 workload 常勝過自動平衡。
- **big.LITTLE（ARM64，MTK 手機晶片）**打破 SMP 對稱假設：CPU capacity 不等、目標是省電，靠 **EAS + Energy Model** 選核；overload 時退回傳統 SMP 攤平邏輯。

## 自我檢核

- [ ] 不看筆記，能解釋為什麼要 per-CPU run queue，以及它為什麼**必然**需要一套 load balancing 來配套
- [ ] 能說出 scheduling domain 為什麼要按 SMT/LLC/NUMA 分層——「近處搬便宜、遠處搬貴」這句話你能展開成具體的 cache/記憶體代價
- [ ] 能區分 `nr_running`、`util_avg`、`load_avg` 三者，並說明 PELT 為什麼用幾何衰減
- [ ] 面試被問「load balancing 什麼時候被觸發」，你能一口氣說出四個時機並各舉一個情境
- [ ] 能用 `taskset` pin 一個 task、用 `/proc/<pid>/status` 的 `Cpus_allowed` 和 `/proc/<pid>/stat` 第 39 欄驗證，並用 `perf` 量遷移次數
- [ ] 面試被問「手機的大小核為什麼不能用桌面 x86 的排程策略」，你能解釋 capacity-aware + EAS 的目標函數，以及 EAS 在 overload 時為什麼失效

## 延伸閱讀

### 官方文件

- **[Documentation/scheduler/sched-domains.rst](https://www.kernel.org/doc/html/latest/scheduler/sched-domains.html)**
  - **讀哪裡**：整篇，短。設計者親自解釋 `sched_domain`/`sched_group` 的結構與 flags，本章「domain 樹」一節就是它的白話版
  - **和本章關聯**：讀完回頭看 `cat /sys/kernel/debug/sched/domains/...` 的輸出就全懂了

- **[Documentation/scheduler/sched-energy.rst](https://www.kernel.org/doc/html/latest/scheduler/sched-energy.html)** 與 **[sched-capacity.rst](https://www.kernel.org/doc/html/latest/scheduler/sched-capacity.html)**
  - **讀哪裡**：sched-energy 講 EAS 的決策與 Energy Model，sched-capacity 講異質核心的 capacity 概念
  - **為什麼值得讀**：MTK / 手機 SoC 方向的必讀；本章 big.LITTLE 一節的權威依據

- **[Documentation/admin-guide/mm/numa_memory_policy.rst](https://www.kernel.org/doc/html/latest/admin-guide/mm/numa_memory_policy.html)** 與 `numa_balancing`（`Documentation/admin-guide/sysctl/kernel.rst`）
  - **讀哪裡**：mempolicy 的種類（bind/preferred/interleave）與 automatic NUMA balancing 的 sysctl
  - **前提**：手邊有 NUMA 機器（或雲端多 socket 實例）邊讀邊 `numactl` 試

### 文章

- **[LWN: The load balancer rework](https://lwn.net/Articles/793427/)** 系列 — Jonathan Corbet
  - **讀哪裡**：這篇及其後續，講 6.x 之前 load balancer 被重寫的來龍去脈（`group_type` 分類、imbalance 計算）
  - **為什麼值得讀**：`sched_balance_rq()`/`calculate_imbalance()` 的源碼直接對照這套設計，先讀它再讀碼省一半力氣

- **[LWN: Energy-aware scheduling](https://lwn.net/Articles/762043/)** — LWN
  - **讀哪裡**：EAS 進主線時的解說，Energy Model 怎麼餵、`find_energy_efficient_cpu()` 怎麼決策
  - **和本章關聯**：本章 EAS 一節的一手來源

### 書籍

- **《Understanding the Linux Kernel, 3rd Ed.》** — Bovet & Cesati，SMP 排程與 run queue 章
  - **定位**：架構骨架的地圖；講的是舊 kernel（沒有 PELT/EAS），但「per-CPU rq + 週期性平衡」的骨架至今適用，細節以 6.12 源碼與上面的 LWN 為準

### 源碼入口（配 [Bootlin v6.12](https://elixir.bootlin.com/linux/v6.12/source)）

- `kernel/sched/fair.c`：`sched_balance_rq()`、`sched_balance_find_src_group()`、`can_migrate_task()`、`select_task_rq_fair()`、`wake_affine()`、`find_energy_efficient_cpu()`、`task_numa_*`
- `kernel/sched/topology.c`：domain 樹的建構（`build_sched_domains()`）
- `kernel/sched/pelt.c` + `include/linux/sched.h` 的 `struct sched_avg`：PELT 的 `util_avg`/`load_avg`
- `kernel/sched/core.c`：`sched_setaffinity()`、`select_task_rq()`、`sched_tick()`

你現在懂了「多顆 CPU 之間怎麼把 task 擺平、怎麼手動干預落點、異質核心怎麼算」。排程這條線（Ch 11–15）到此收尾——從一顆 CPU 挑誰、怎麼切，到多顆 CPU 之間怎麼分。練習 B 會讓你寫一個模組把 CFS/EEVDF 和本章的負載量測真的印出來看。之後我們轉向記憶體管理：一個 task 看到的虛擬位址，怎麼一路走到實體記憶體。

→ [練習 B：CFS/EEVDF 觀測模組](./practice-b-scheduler-observation.md) ／ [Ch 16 虛擬位址空間與 page table walk](./16-virtual-memory-page-tables.md)
