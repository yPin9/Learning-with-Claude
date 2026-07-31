# Ch 50 — cgroup v2 實作

> **目標**：理解 namespace 只解決「看到什麼」、cgroup 才解決「用多少」；讀懂 cgroup v2 統一階層在 kernel 裡是什麼（cgroup 樹 + `cgroup_subsys_state` + task→css_set 歸屬），搞清楚 cpu/memory/io/pids/cpuset 各 controller 怎麼把 Ch 11–36 學過的排程器、memcg、block layer 綁進一組 process；最後動手用純檔案系統介面建 cgroup、限制記憶體看它被 OOM、限制 CPU 看它被節流，並對上 docker 容器實際落在哪個 cgroup。

> **前置**：Ch 49（namespace 隔離視圖、seccomp/LSM）——本章是那條線的另一半。cgroup + namespace + seccomp/LSM 三支柱合起來才是容器，本章把三支柱收口，明確接 `docker` 課的 `--memory`/`--cpus`。橫向重度依賴 Ch 11–15（排程）、Ch 22（memcg/OOM）、Ch 36（block layer）、Ch 33（VFS，因為 cgroup 的介面就是一個檔案系統）。

## 為什麼需要這個？

Ch 49 給了 namespace：一個容器裡的 process 看到自己是 PID 1、看到獨立的網路介面、看到乾淨的 mount 表。隔離做得很漂亮——**但隔離不等於限制**。

一個被 namespace 完美隔離的 process，仍然可以：

- 開 10000 個執行緒吃光 CPU，讓同機的其他容器排不上隊
- `malloc` 到把整台機器的實體記憶體吃乾，觸發全域 OOM，被殺的可能是別人
- 對磁碟狂寫，讓別的容器的 IO 延遲飆到秒級

namespace 是給每個 process **一副有色眼鏡**，讓它以為自己獨佔世界；但眼鏡不會攔住它伸手去抓真實的資源。你把十個「以為自己獨佔世界」的容器塞進一台機器，它們會為了 CPU、記憶體、IO 打成一團，這叫 **noisy neighbor（吵鬧鄰居）** 問題。

cgroup（control groups，控制群組）補的就是這一塊：把一組 process 圈起來，對這組整體**設上限、記帳、隔離資源用量**。namespace 管「看見什麼」，cgroup 管「能用多少」。兩者正交，缺一個都不是容器。

在 cgroup 出現之前，Linux 只有 `setrlimit`（`RLIMIT_AS`、`RLIMIT_NPROC` 等）能限制資源，但它是**per-process** 的、粒度粗、無法對「一群 process 當成一個整體」記帳——你 fork 出 100 個子 process，每個各自受限，但這 100 個加起來吃多少沒人管。cgroup 的核心突破就是**把「一群 process」當成資源記帳與限制的單位**。

## 先建立直覺

cgroup 的心智模型只有三件事：**一棵樹、每個節點掛著一組資源旋鈕、process 掛在節點上**。

```
   cgroup v2 統一階層（掛在 /sys/fs/cgroup，單一一棵樹）
   ─────────────────────────────────────────────────────
                    / (root cgroup)
                    │  cgroup.controllers = cpu memory io pids ...
                    │  cgroup.subtree_control = cpu memory io   ← 我把哪些 controller 下放給子節點
        ┌───────────┼────────────────────────┐
        │           │                        │
   system.slice  user.slice            docker/            ← 每個節點就是一個目錄
        │           │                        │
        │      user-1000.slice        ┌──────┴───────┐
        │                          <container-A>   <container-B>
        │                          cpu.max=50000 100000   ← 旋鈕就是這個目錄裡的檔案
        │                          memory.max=536870912
        │                          cgroup.procs: 4021 4055 ← 這些 PID 屬於這個 cgroup
        │
   sshd.service
   cpu.weight=100
   memory.max=max

   每個節點上掛著各 controller 的「狀態 + 旋鈕」：
        cpu     → cpu.weight  cpu.max  cpu.stat            (接 Ch 12/13 group sched)
        memory  → memory.max  memory.high  memory.current  (接 Ch 22 memcg)
        io      → io.max  io.weight  io.stat               (接 Ch 36 blk-cgroup)
        pids    → pids.max  pids.current
        cpuset  → cpuset.cpus  cpuset.mems                 (接 Ch 15 NUMA)
```

三個要點先記住：

1. **它是一個檔案系統**。整棵樹就是掛在 `/sys/fs/cgroup` 下的目錄樹（cgroupfs，一種特殊檔案系統，走 Ch 33 的 VFS）。**建 cgroup = `mkdir` 一個目錄**；**把 process 放進去 = 把 PID `echo` 進那個目錄的 `cgroup.procs`**；**設限 = 往 `memory.max` 之類的檔案 `echo` 一個數字**。沒有專用 syscall，全靠 VFS 讀寫。這個設計選擇很關鍵：它讓 cgroup 的操作可以用 shell、可以被 systemd/docker 這種 user space 程式輕鬆驅動，不用發明新的 API。

2. **v2 只有一棵樹**（統一階層，unified hierarchy）。這是 v2 相對 v1 最大的改變，下一節細講。

3. **旋鈕分兩類**：`.max`/`.high`/`.min` 這種是**限制（limit）**，`.current`/`.stat` 這種是**記帳/統計（accounting）**。cgroup 同時做限制與記帳，兩者都重要——你想知道「這個容器現在用了多少記憶體」，看的是記帳（`memory.current`）；你想擋住它別再要，設的是限制（`memory.max`）。

## v1 為什麼被 v2 取代

cgroup v1 的設計是：**每個 controller 各有一棵獨立的階層樹**。你可以把 memory controller 掛成一棵樹、cpu controller 掛成另一棵完全不同結構的樹，一個 process 在 memory 樹裡屬於 group X、在 cpu 樹裡屬於 group Y，兩者毫無關聯。

```
   cgroup v1（多重階層，每個 controller 一棵樹，彼此無關）
   ──────────────────────────────────────────────────────
   /sys/fs/cgroup/memory/          /sys/fs/cgroup/cpu/         /sys/fs/cgroup/blkio/
        └── groupX                      └── groupY                  └── groupZ
             procs: 4021                     procs: 4021                 procs: 4021
   ↑ 同一個 PID 4021 在三棵樹裡分屬三個不相干的 group，組合爆炸、語意打架
```

這在當年看似靈活，實務上是災難：

- **controller 之間無法協作**。memory 和 io 兩個 controller 想合作做 writeback 節流（髒頁寫回時同時受記憶體壓力與 IO 頻寬約束，見 Ch 21/22），但它們在不同的樹裡，一個 process 在兩棵樹的位置不一致，根本沒法對齊。writeback 的記帳長期是 v1 的痛點。
- **語意混亂**。「這個 process 屬於哪個 cgroup」沒有唯一答案，取決於你問哪個 controller。工具、文件、使用者全被搞糊塗。
- **root 附近的行為不一致**，各 controller 對「把 process 放在中間節點還是葉節點」的規則不統一。

v2（unified hierarchy）的決定很硬：**所有 controller 共用同一棵樹**。一個 process 在整個系統裡只屬於**一個** cgroup 節點，這個節點上同時掛著它受到的所有 controller 約束。語意乾淨了，controller 之間能協作了（memory+io 的 writeback 節流就是在 v2 才做對的）。

v2 也引入兩條紀律：

- **no internal process constraint（葉節點才放 process）**：一旦一個 cgroup 啟用了某些 controller 給子節點，它自己就不能直接掛 process，process 只能在葉節點。這消除了「中間節點的 process 和它的子 cgroup 怎麼競爭資源」這個 v1 從沒講清楚的問題。
- **subtree_control 顯式下放**：父節點要在 `cgroup.subtree_control` 裡寫 `+cpu +memory`，子節點才「看得到」這些 controller。controller 是一層層往下授權的，不是預設全開。

今天 systemd（PID 1）、docker/containerd、Kubernetes 全部預設 v2。Ubuntu 22.04 起、Fedora 更早，開機就是純 v2（`cgroup2fs` 單獨掛在 `/sys/fs/cgroup`，沒有 v1 的一堆子目錄）。本章之後只講 v2。

> 怎麼確認你的機器是 v2？`stat -fc %T /sys/fs/cgroup`，回 `cgroup2fs` 就是純 v2；回 `tmpfs` 且底下有 `memory/`、`cpu/` 等子目錄就是 v1 或混合模式。QEMU 裡跑我們自編的 6.12（Ch 0），開機參數預設就走 v2。

## 核心資料結構

原始碼主要在 `kernel/cgroup/`（`cgroup.c` 是核心框架、`cpuset.c`、`pids.c` 等是各 controller），型別定義在 `include/linux/cgroup-defs.h` 與 `include/linux/cgroup.h`。memory controller 例外，它住在 `mm/memcontrol.c`（因為它和 mm 子系統綁太深）；cpu controller 的 group scheduling 在 `kernel/sched/`（`core.c`、`fair.c`）。

四個結構撐起整個機制：

**1. `struct cgroup`（`include/linux/cgroup-defs.h`）——樹上的一個節點。**

對應檔案系統裡的一個目錄。它記錄自己在樹裡的位置（`level`、指向父節點）、啟用了哪些 controller、以及一個關鍵欄位：一個 `cgroup_subsys_state` 的指標陣列 `subsys[]`，每個 controller 在這個 cgroup 上的狀態各佔一格。

**2. `struct cgroup_subsys_state`（簡稱 css）——「某個 controller 在某個 cgroup 上的狀態」。**

這是 cgroup 設計裡最容易繞暈、也最核心的抽象。cgroup 是節點，controller（`cgroup_subsys`，如 `memory_cgrp_subsys`）是「維度」，**css 是節點 × 維度的交會點**。

```
                cpu 維度    memory 維度   io 維度
   root cgroup   css        css          css
       │
   docker/       css        css          css     ← 每個格子是一個 cgroup_subsys_state
       │
   container-A   css ──────► 這個 css 裡存 cpu.weight/cpu.max 的實際值、
                             以及排程器用的 task_group 指標
```

css 裡有引用計數（`refcount`）、指回所屬 `cgroup` 和 `cgroup_subsys` 的指標、以及父 css 指標（讓 controller 能沿樹往上找）。memory controller 把 css **內嵌**在自己的 `struct mem_cgroup`（`include/linux/memcontrol.h`）第一個欄位裡，用 `container_of` 從 css 拿回 `mem_cgroup`——這是 kernel 慣用手法（Ch 5 的 `list_head` 內嵌同理）。

**3. `struct css_set`（`include/linux/cgroup-defs.h`）——task 怎麼歸屬 cgroup。**

這裡有個效能設計。一個 task（Ch 9 的 `task_struct`）需要知道自己在**每個** controller 維度上屬於哪個 css。最直白的做法是 task 裡放一個 css 陣列，但系統上成千上萬個 task，其中絕大多數的 cgroup 歸屬**完全相同**（都在 root、或都在同一個 service）。為每個 task 各存一份是浪費。

所以 kernel 引入 `css_set`：把「一組 css 的組合」抽出來共享。`task_struct->cgroups` 指向一個 `css_set`，很多 task 共享同一個 `css_set`（它有引用計數）。當你把一個 task 搬到別的 cgroup，其實是讓它的 `task_struct->cgroups` 改指向另一個 `css_set`。

```
   task 4021 ─┐
   task 4055 ─┼─► css_set A ─► { cpu:cssX, memory:cssY, io:cssZ, ... }
   task 4099 ─┘                （這三個 task cgroup 歸屬相同，共享一份）

   task 5001 ───► css_set B ─► { cpu:cssP, memory:cssQ, ... }
```

`task_struct` 裡是 `struct css_set __rcu *cgroups`——用 RCU 保護（Ch 27），因為讀「某 task 屬於哪個 cgroup」極其頻繁（排程、記帳到處在讀），寫（搬 cgroup）極其罕見，正是 RCU 的甜蜜點。

**4. `struct cgroup_subsys`（`include/linux/cgroup-subsys.h` 列出所有 controller）——一個 controller 的 vtable。**

每個 controller 註冊一組回呼：`css_alloc`/`css_free`（建/毀 cgroup 時配置該 controller 的 css）、`css_online`、`can_attach`/`attach`（一個 task 要加入這個 cgroup 時，controller 有沒有意見、加入後做什麼）。`kernel/cgroup/cgroup.c` 的框架在建目錄、搬 process 時，遍歷所有啟用的 controller 呼叫這些回呼。這就是**框架與 controller 解耦**的介面：cgroup 核心只管樹和檔案系統，具體「限制 CPU 怎麼實現」交給 cpu controller 自己。

## 底層機制：mkdir 與 attach 走過的源碼路徑

把上面四個結構串起來，看兩個最常發生的動作在 `kernel/cgroup/cgroup.c` 裡實際跑什麼——這是「cgroup 是一個檔案系統」這句話的兌現。

**動作一：`mkdir /sys/fs/cgroup/mygroup`（建一個 cgroup）。** 因為 cgroupfs 是掛在 VFS 上的檔案系統（Ch 33），`mkdir` 這個 syscall 最後會走到 cgroupfs 註冊的 `mkdir` inode operation，也就是 `cgroup_mkdir()`。它做的事：

```
   mkdir("mygroup")  ── VFS 層（Ch 33）── cgroupfs 的 .mkdir op
        │
        ▼
   cgroup_mkdir()                          kernel/cgroup/cgroup.c
        ├─ cgroup_create()  配一個 struct cgroup、掛進父節點的孩子鏈、設 level
        ├─ 對父節點 subtree_control 裡每個啟用的 controller：
        │      css_create() → subsys->css_alloc()   ← 呼叫該 controller 的 alloc 回呼
        │                      （memory 走 mem_cgroup_css_alloc，配一個 mem_cgroup）
        │      把新 css 掛進 cgroup->subsys[ssid]
        ├─ 對每個 css 呼叫 subsys->css_online()
        └─ 在這個目錄裡生出旋鈕檔（cpu.max/memory.max/...）＝各 controller 註冊的 cftype
```

關鍵：**目錄裡那些 `memory.max`、`cpu.max` 檔案不是憑空的**，是每個 controller 用 `cftype`（control file type）陣列向框架註冊的，框架在建目錄時替每個啟用的 controller 生成對應檔案。你 `cat memory.max` 讀到的值、`echo` 進去寫的值，背後是 controller 註冊的 `seq_show`/`write` 回呼在讀寫該 css 裡的 `page_counter`。

**動作二：`echo $$ > mygroup/cgroup.procs`（把 task 搬進去）。** 寫 `cgroup.procs` 這個檔案觸發框架的 `cgroup_procs_write()` → `cgroup_attach_task()`：

```
   write(cgroup.procs, "4021")
        │
        ▼
   cgroup_attach_task()                    kernel/cgroup/cgroup.c
        ├─ 對每個啟用 controller：subsys->can_attach()   ← 問「這 task 能進來嗎」
        │      （pids 在這裡檢查 pids.max，滿了就回 -EAGAIN 擋下）
        ├─ 找/建對應目標 cgroup 的 css_set（可能新建組合，也可能重用既有的）
        ├─ 把 task_struct->cgroups 從舊 css_set 改指到新 css_set（RCU，Ch 27）
        └─ 對每個 controller：subsys->attach()          ← 「進來了，做善後」
               （cpu 把 task 從舊 task_group 的 cfs_rq 挪到新的、memory 更新記帳歸屬）
```

`can_attach` / `attach` 這對回呼是 controller 的兩段式協議：先問所有 controller「都同意嗎」（有一個否決就整個回滾），全同意才真正搬、再通知所有 controller「搬完了」。這保證遷移是原子的——不會出現「cpu 同意了 pids 卻超額」的半途狀態。這正是 Ch 11 排程器 runqueue 增刪、和本章 css_set 換指標的交會點。

## controller 逐個看

### cpu — 把排程器綁進 cgroup

cpu controller 提供兩種正交的控制，對應 Ch 11–13 學過的排程器：

**`cpu.weight`（比例分享，對應 group scheduling）。** 預設 100，範圍 1–10000。它不是硬上限，而是**在有競爭時按比例分配 CPU**。cgroup A weight=200、B weight=100，兩者都想跑滿時，A 拿 2/3、B 拿 1/3；但 B 閒著時 A 可以拿滿——這是 work-conserving（不浪費）的比例調度。

底層它接的是 CFS/EEVDF 的 **group scheduling**（Ch 12/13）。回想 Ch 12：排程器排的不是 task 而是 `sched_entity`，而 `sched_entity` 可以是**一個 task，也可以是一整個 task_group**。cpu cgroup 的每個 css 對應一個 `struct task_group`（`kernel/sched/sched.h`），它在每個 CPU 的 runqueue 上有自己的 `cfs_rq`（子 runqueue）。排程器先在頂層 runqueue 選中「哪個 group 的 entity」，再下潛到那個 group 的 `cfs_rq` 裡選 task——階層式排程。`cpu.weight` 就是設這個 group entity 的權重（換算成 EEVDF 的 lag/weight，見 `kernel/sched/fair.c` 的 `set_load_weight`/group sched 相關程式碼）。

```
   頂層 CPU runqueue
        ├─ sched_entity(task_group docker/A)  weight ∝ cpu.weight   ← 先選 group
        │       └─ cfs_rq of A
        │            ├─ sched_entity(task 4021)                     ← 再下潛選 task
        │            └─ sched_entity(task 4055)
        └─ sched_entity(task_group system.slice)
```

**`cpu.max`（頻寬硬上限，quota/period）。** 格式是兩個數字 `$QUOTA $PERIOD`（微秒），例如 `50000 100000` 意思是「每 100ms 這個 cgroup 最多跑 50ms 的 CPU 時間」= 上限 0.5 顆 CPU。就算整台機器閒著，它也不能超過——這是**非** work-conserving 的硬牆，用來賣「你買 0.5 core 就只能用 0.5 core」的雲端配額。實作是 CFS bandwidth control（`kernel/sched/fair.c` 的 `__account_cg_runtime`/`throttle_cfs_rq`）：每個 period 補一次 quota，用完就把整個 group 的 `cfs_rq` **節流（throttle）**——把它從 runqueue 摘掉直到下個 period。這對應 Ch 11 排程器框架裡 runqueue 的增刪。

> docker 的 `--cpus=0.5` 就是幫你算好 `cpu.max = 50000 100000`；`--cpu-shares=512` 對應舊的 `cpu.weight`（v2 下會被 runc 換算）。你在 docker 課下的參數，落到 kernel 就是這兩個檔案。

### memory — memcg 記帳與 OOM

memory controller（memcg，memory cgroup）是 controller 裡最複雜的，因為它要對**每一個 page** 記帳到某個 cgroup。原始碼 `mm/memcontrol.c`，核心結構 `struct mem_cgroup`（`include/linux/memcontrol.h`）。

**旋鈕：**

- `memory.max`：硬上限。超過且無法回收時，觸發**這個 cgroup 內的 OOM**（cgroup-level OOM，只殺這個 cgroup 裡的 process，不動全機——接 Ch 22 的 OOM killer，但範圍限縮）。
- `memory.high`：軟上限。超過不會殺 process，而是**強制節流**——分配記憶體的 process 會被拖進 direct reclaim（Ch 22）並被人為延遲，逼它慢下來、逼記憶體被回收。這是比 `.max` 溫和的壓力閥門。
- `memory.min`/`memory.low`：**保護下限**。保證這個 cgroup 至少留住這麼多記憶體不被回收（`.min` 是硬保證、`.low` 是盡力），避免重要服務在全機記憶體壓力下被搶光 page cache。
- `memory.current`：記帳讀數，這個 cgroup 現在用了多少（含 anon、page cache、部分 kernel 記憶體）。
- `memory.stat`：細分（anon/file/slab/sock 各多少），除錯神器。

**page 怎麼歸屬到 cgroup？** 這是 memcg 的靈魂。當一個 process 觸發 page fault 配到新 page（Ch 19/20），或讀檔案填 page cache（Ch 21），kernel 要決定「這個 page 記在哪個 cgroup 帳上」。做法：page 的 `struct folio`（Ch 21 的 folio）關聯到一個 `mem_cgroup`。分配路徑上 `mm/memcontrol.c` 的 `charge` 函式（如 `__mem_cgroup_charge`/`mem_cgroup_charge`）在 page 加入時對當前 task 的 memcg 記一筆（`page_counter` 累加），page 釋放時 `uncharge` 扣回。超過 `memory.max` 且回收不出空間時 `mem_cgroup_out_of_memory` 動手。

```
   process 觸發 page fault → 配一個 page
        │
        ▼
   mem_cgroup_charge(folio, mm)   ← mm/memcontrol.c
        │   找出 mm 屬於哪個 memcg（走 task->cgroups→css_set→memory css）
        │   page_counter_try_charge(&memcg->memory, nr_pages)
        │        ├─ 沒超 max → 成功，folio 記到這個 memcg
        │        └─ 超了     → try_to_free_mem_cgroup_pages() 回收（Ch 22）
        │                       回收夠了 → 成功
        │                       還不夠   → cgroup OOM，殺這個 cgroup 內的 process
```

memcg 的回收是**per-cgroup 的 reclaim**：Ch 22 的 kswapd/direct reclaim 在 memcg 模式下只掃這個 cgroup 的 LRU list（每個 memcg 有自己的 `lruvec`），不是全機掃。這讓「A 容器記憶體壓力大就回收 A 的 page，別動 B」成為可能。

> docker `--memory=512m` → `memory.max = 536870912`。容器裡程式 `malloc` 到超過，你會在 `dmesg` 看到 `Memory cgroup out of memory: Killed process ...`——被殺的是容器內的 process，host 其他容器安然無恙。這正是 cgroup 限制 vs Ch 22 全機 OOM 的差別。

### io — blk-cgroup 綁 block layer

io controller 把 Ch 36 的 block layer（bio、request queue、blk-mq）綁進 cgroup，原始碼在 `block/blk-cgroup.c` 與 `block/blk-iolatency.c`/`block/blk-iocost.c`。

- `io.weight`：比例分享磁碟頻寬（類比 cpu.weight），底層可用 **BFQ** I/O scheduler 或 `io.cost` model。
- `io.max`：對某個裝置設 IOPS/頻寬硬上限，格式 `MAJ:MIN riops=... wbps=...`（讀 IOPS、寫 bytes/s 等），對應 Ch 36 裡 bio 提交路徑上的節流檢查。
- `io.stat`：per-cgroup 的 IO 記帳。

io controller 和 memory controller 的協作點是 **writeback（Ch 21）**：髒頁寫回時，這些 IO 要記在「當初弄髒 page 的那個 cgroup」帳上，而不是「執行寫回的 kernel flusher thread」帳上。這個歸屬（cgroup writeback）在 v1 做不對（memory/io 在不同樹），是 v2 統一階層才解決的招牌案例。`mm/page-writeback.c` 與 blk-cgroup 在 v2 下協同，把髒頁的 IO 正確歸給發起的 cgroup。

### pids 與 cpuset

- **pids**（`kernel/cgroup/pids.c`）：`pids.max` 限制這個 cgroup 能有幾個 process/thread。防 fork bomb 的最後一道牆——就算別的沒限，pids.max 讓一個容器炸不出無限 process。docker `--pids-limit` 對應它。
- **cpuset**（`kernel/cgroup/cpuset.c`）：`cpuset.cpus` 把 cgroup 綁到指定的 CPU 核心（親和性，接 Ch 15 的 CPU affinity），`cpuset.mems` 綁到指定的 NUMA node（接 Ch 15 NUMA）。用在「這個延遲敏感服務獨佔 CPU 4–7、只用 node 0 的記憶體」這種場景。它和排程器的 load balancing（Ch 15）互動——cpuset 限縮了 task 能被搬去的 CPU 集合。

## 底層機制：三支柱如何組成容器

現在把 Ch 49 和本章收口。一個容器（docker/runc 起一個 container）在 kernel 裡就是三件事的疊加，缺一不可：

```
   ┌──────────────────────────── 一個「容器」= 三支柱疊加 ────────────────────────────┐
   │                                                                                  │
   │   支柱 1：namespace（Ch 49）── 隔離「看到什麼」                                   │
   │     clone(CLONE_NEWPID|NEWNET|NEWNS|NEWUTS|NEWIPC|NEWUSER)                        │
   │     → 容器內 PID 從 1 起、獨立網卡、獨立 mount 表、獨立 hostname                  │
   │                                                                                  │
   │   支柱 2：cgroup v2（本章）── 限制「用多少」                                      │
   │     mkdir /sys/fs/cgroup/docker/<id>；echo $PID > cgroup.procs                    │
   │     cpu.max / memory.max / io.max / pids.max                                      │
   │     → 吃不光 CPU、記憶體超額被 cgroup-OOM、IO 有頻寬牆                            │
   │                                                                                  │
   │   支柱 3：seccomp-BPF + LSM（Ch 47/48/49）── 約束「能做什麼」                     │
   │     seccomp filter 擋掉危險 syscall（Ch 49）                                      │
   │     capabilities 砍權（Ch 47）、SELinux/AppArmor label（Ch 48）                   │
   │     → 就算跑起來，也 call 不到不該 call 的 syscall、碰不到不該碰的物件            │
   │                                                                                  │
   │   runc 幫你把三支柱一次架好：讀 OCI config.json → clone 建 namespace →            │
   │   把新 process 塞進 cgroup → 套 seccomp/capabilities/LSM → exec 你的程式          │
   └──────────────────────────────────────────────────────────────────────────────────┘
```

三者正交、互補，對應三個不同的動詞：

| 支柱 | 管什麼 | kernel 機制 | 沒有它會怎樣 |
|---|---|---|---|
| namespace | 看到什麼（隔離視圖） | `nsproxy`、各 namespace（Ch 49） | 容器看得到 host 的 process/網路/掛載 |
| cgroup | 用多少（資源限制） | cgroup 樹 + css（本章） | 一個容器吃光資源拖垮全機 |
| seccomp/LSM/cap | 能做什麼（權限約束） | seccomp filter、LSM hook、cred（Ch 47/48/49） | 容器逃逸、call 危險 syscall |

Kubernetes 的 Pod resource `requests`/`limits`、docker 的 `--memory`/`--cpus`/`--pids-limit`，最終全都變成往某個 cgroup 目錄裡寫檔案。你在 docker 課下的每一個資源旗標，現在你知道它落在 kernel 的哪個結構、走哪條源碼路徑了。

## PSI — cgroup 級的壓力指標

限制設好了，但你怎麼知道一個 cgroup 現在「有多喘」？光看 `memory.current` 只告訴你用量，不告訴你**因為缺資源卡了多久**。這就是 **PSI（Pressure Stall Information，壓力停滯資訊）** 要回答的。

PSI（`kernel/sched/psi.c`，Facebook 貢獻，4.20 進主線）量化「因為等某個資源而無法前進的時間比例」。三個檔案：`cpu.pressure`、`memory.pressure`、`io.pressure`，每個 cgroup 目錄裡都有（root 也有全機的在 `/proc/pressure/`）。內容像：

```
some avg10=0.00 avg60=1.23 avg300=2.10 total=1234567
full avg10=0.00 avg60=0.50 avg300=0.90 total=456789
```

- `some`：**至少一個** task 因為缺這資源而卡住的時間比例
- `full`：**所有** task 都因為缺這資源而卡住（完全停擺）的時間比例
- `avg10/60/300`：過去 10/60/300 秒的百分比

為什麼比 `memory.current` 有用？記憶體用到 99% 但沒人在等，其實很健康；用到 60% 但一直在 thrashing（換頁抖動，Ch 22），`memory.pressure` 的 `some` 會飆高告訴你「這裡在受苦」。PSI 讓 systemd-oomd、Kubernetes 的資源壓力驅逐能**在 OOM 真正發生前**就介入。它記在 cgroup 級，直接關聯本章的限制與 Ch 22 的 reclaim。

## 動手：手建 cgroup 看它限制與 OOM

在 QEMU 裡跑我們自編的 6.12（Ch 0）。確認是 v2：

```bash
stat -fc %T /sys/fs/cgroup      # cgroup2fs = 純 v2
```

**Step 1：建一個 cgroup 並把自己丟進去。**

```bash
cd /sys/fs/cgroup
mkdir mygroup                   # 建 cgroup = mkdir 一個目錄（框架自動生出裡面的旋鈕檔）
ls mygroup                      # cgroup.procs cpu.max cpu.weight memory.max memory.current io.max ...

# 父節點要先把 controller 下放給子節點，子節點才有 cpu.max/memory.max
cat cgroup.controllers          # 這台機器有哪些 controller
echo "+cpu +memory +pids" > cgroup.subtree_control   # 下放（若已下放會報 already enabled，無妨）

echo $$ > mygroup/cgroup.procs  # 把當前 shell（$$）搬進 mygroup
cat /proc/self/cgroup           # 確認：0::/mygroup（v2 只有一行，就一棵樹）
```

> `$$` 是當前 shell 的 PID。把它寫進 `cgroup.procs`，之後這個 shell fork 出來的所有子 process 都繼承在 mygroup 裡。這就是「一群 process 當一個單位」——你不用一個個搬。

**Step 2：限制記憶體，看 cgroup-OOM 動手。**

```bash
echo 50M > mygroup/memory.max               # 上限 50 MB
echo 0   > mygroup/memory.swap.max          # 不准用 swap（不然它會先換出去而非被殺）

# 在 mygroup 裡跑一個狂吃記憶體的程式
cat <<'EOF' > /tmp/eat.c
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
int main(void){
    for(size_t n=0;;n+=10*1024*1024){       // 每次多要 10 MB 並寫進去（逼實際配 page）
        char *p = malloc(10*1024*1024);
        if(!p) { write(2,"malloc failed\n",14); return 1; }
        memset(p, 1, 10*1024*1024);          // 寫入才會真的觸發 page fault 佔實體記憶體
        write(2,".",1);
    }
}
EOF
cc -O0 /tmp/eat.c -o /tmp/eat
/tmp/eat                                     # 這個 shell 已在 mygroup，子 process 繼承
# 印幾個點後被 Killed
dmesg | tail
# Memory cgroup out of memory: Killed process NNNN (eat) ...
```

被殺的是 `eat`，且是**因為 cgroup 上限**（不是全機記憶體不夠——你的 QEMU 有 512 MB，遠不止 50 MB）。這就是 cgroup-level OOM，範圍限縮在 mygroup。對照 Ch 22 的全機 OOM，機制同源（`out_of_memory`），但受害範圍被 cgroup 框住。

**Step 3：限制 CPU，看節流。**

```bash
echo "10000 100000" > mygroup/cpu.max        # 每 100ms 最多 10ms = 上限 0.1 顆 CPU
# 在 mygroup 裡跑一個死迴圈燒 CPU
yes > /dev/null &                            # 這個 shell 在 mygroup，yes 繼承
top -p $!                                    # 看它的 %CPU 被壓在 ~10% 上不去
cat mygroup/cpu.stat                         # nr_throttled / throttled_usec 在漲 = 被節流的證據
kill %1
```

`cpu.stat` 裡的 `nr_throttled`（被節流幾次）、`throttled_usec`（累計被節流多久）就是 CFS bandwidth control 動手的痕跡，對應 `kernel/sched/fair.c` 的 `throttle_cfs_rq`。

**Step 4：看 systemd 的階層與 docker 的對應（在有 systemd/docker 的正常 host，不是 QEMU initramfs）。**

```bash
systemd-cgls                    # 樹狀印出整棵 cgroup v2 階層（system.slice/user.slice/...）
systemd-cgtop                   # 像 top，但按 cgroup 排，看哪個 slice 吃 CPU/記憶體/IO

# 跑一個帶限制的 docker 容器
docker run -d --name demo --memory=256m --cpus=0.5 --pids-limit=100 nginx
# 找到它的 cgroup（現代 docker/containerd 路徑）
cat /sys/fs/cgroup/system.slice/docker-*.scope/memory.max   # 268435456 = 256 MB
cat /sys/fs/cgroup/system.slice/docker-*.scope/cpu.max      # 50000 100000 = 0.5 CPU
cat /sys/fs/cgroup/system.slice/docker-*.scope/cgroup.procs # 容器內 process 的 host PID
```

`--memory=256m` 精準落成 `memory.max=268435456`，`--cpus=0.5` 落成 `cpu.max=50000 100000`。docker 課教你下的旗標，在這裡看到它就是往 cgroupfs 寫檔案。

**Step 5：清理。**

```bash
echo $$ > /sys/fs/cgroup/cgroup.procs   # 把 shell 搬回 root（不然目錄還有 process，刪不掉）
rmdir /sys/fs/cgroup/mygroup            # 刪 cgroup = rmdir（目錄必須空、無 process 才刪得掉）
```

> `rmdir` 失敗通常是「裡面還有 process」或「還有子 cgroup」。cgroup 目錄不能用 `rm -r`，只能 `rmdir`，且要求空——這是 cgroupfs（Ch 33 的特殊檔案系統）刻意的限制，防你誤刪一整棵含活 process 的子樹。

## 對比與取捨

| 主題 | 選項 A | 選項 B | 取捨 |
|---|---|---|---|
| 階層模型 | v1 多重階層（每 controller 一樹） | **v2 統一階層** | v2 語意乾淨、controller 能協作（writeback），代價是失去 v1「不同 controller 不同結構」的靈活（實務上沒人真的需要那靈活） |
| CPU 限制 | `cpu.weight`（比例、work-conserving） | `cpu.max`（硬上限、非 work-conserving） | weight 不浪費閒置 CPU 但無法保證上限；max 給硬配額但機器閒著也用不滿。雲端計費用 max，同機共享優先用 weight |
| 記憶體壓力 | `memory.max`（超額就 OOM 殺） | `memory.high`（超額節流+回收，不殺） | max 是硬牆會殺 process；high 是軟閥門逼它慢下來、通常配 PSI 監控。生產常設 high 略低於 max 當緩衝 |
| 資源限制手段 | cgroup | `setrlimit`（RLIMIT_*） | rlimit 是 per-process、粒度粗、不能對「一群」記帳；cgroup 對一群整體記帳限制，是容器的正解。兩者可疊加 |
| 監控指標 | `memory.current`（用量） | `memory.pressure`（PSI，卡多久） | 用量高不代表在受苦；PSI 才反映真實痛感。生產驅逐決策看 PSI 比看用量準 |

## 踩雷集錦

1. **「namespace 就能限制資源」——錯**。namespace 只隔離視圖，一個 namespace 隔離得再乾淨的 process 照樣能吃光全機 CPU/記憶體。限制要靠 cgroup。把兩者搞混是理解容器最常見的錯誤。正確認識：namespace 管「看到什麼」，cgroup 管「用多少」，正交。

2. **在 v2 直接對中間節點掛 process——被拒**。v2 有「no internal process」規則：一旦一個 cgroup 啟用了 controller 給子節點（`subtree_control` 有東西），它自己就不能放 process，`echo PID > cgroup.procs` 會回 `EBUSY`。process 只能在葉節點。這和 v1 不同，遷移 v1 腳本常中招。

3. **忘了 `subtree_control` 下放，子 cgroup 裡沒有 cpu.max/memory.max**。你 `mkdir` 了子目錄卻找不到 `memory.max`，因為父節點沒 `echo "+memory" > cgroup.subtree_control`。v2 的 controller 是層層顯式授權，不是預設全開。

4. **限記憶體卻沒關 swap，process 不被殺只是變慢**。`memory.max` 到頂但 `memory.swap.max` 沒設 0，kernel 會先把 page 換到 swap 而非觸發 OOM，你以為限制沒生效。要看到乾脆的 cgroup-OOM，把 `memory.swap.max` 設 0。

5. **cpu.max 設了但看 top 覺得「怎麼還是跑滿一瞬間」**。`cpu.max` 是每個 period（預設 100ms）補一次 quota，period 內用完才節流。所以你會看到「跑滿→被摘掉→下 period 再跑」的鋸齒，平均下來是你設的上限。看 `cpu.stat` 的 `nr_throttled` 確認節流真的在發生，別只盯瞬時 %CPU。

6. **`rm -rf` 一個 cgroup 目錄——不會如你所願**。cgroupfs 是特殊檔案系統，目錄只能 `rmdir` 且必須空（無 process、無子 cgroup）。`rm -rf` 會失敗或行為詭異。先把 process 搬走、刪子節點，再 `rmdir`。

## 進階：再往深一層

- **cgroup v2 的 `cgroup.threads` 與 thread 模式**：預設 cgroup 以 process 為單位遷移（一個 process 的所有 thread 一起走）。但某些 controller（cpu、cpuset）支援 threaded 模式，讓同一 process 的不同 thread 分屬不同 cgroup——用在單一 process 內做 thread 級 CPU 隔離。memory/io 這種天生 process 級的 controller 不支援 threaded。

- **memcg 的 kernel memory accounting**：早期 memcg 只記 user page，slab（Ch 18）、socket buffer、page table 這些 kernel 記憶體不記帳，成為逃逸漏洞（容器狂建 dentry 吃 slab 繞過限制）。v2 起 kernel memory 記帳併入 `memory.max`（`SLAB_ACCOUNT` 的 slab、`memory.stat` 的 `slab`/`sock` 欄位），這是 v2 記帳更嚴的關鍵改進。

- **`nsdelegate` 與 rootless container**：v2 支援把一整棵子樹**委派（delegate）**給非 root user 管理（配 user namespace，Ch 49），這是 rootless docker/podman 的基礎——普通使用者也能在自己的 sub-cgroup 裡設限，不用 root。

- **面試常問**：「v1 和 v2 差在哪、為什麼要換」（統一階層、controller 協作、no-internal-process）、「docker `--memory` 到 kernel 走哪」（memcg charge/reclaim/OOM）、「cpu.weight 和 cpu.max 差別」（比例 work-conserving vs 硬上限）、「怎麼判斷一個容器記憶體真的吃緊」（PSI 而非 current）。這些都是本章正文的直接考點。

- **和 systemd 的關係**：systemd 是 PID 1，它把整棵 cgroup v2 樹當作服務管理的基礎設施——每個 `.service` 是一個 cgroup（`system.slice/xxx.service`），你在 unit 檔寫 `MemoryMax=`/`CPUQuota=` 就是 systemd 幫你設 `memory.max`/`cpu.max`。`systemctl status` 顯示的 memory/CPU 讀數就是讀該 cgroup 的記帳。

## 動手練習

1. **驗證 cgroup-OOM vs 全機 OOM**：跑 Step 2 的 `eat`，用 `dmesg` 確認訊息是 `Memory cgroup out of memory`（不是全機 `Out of memory`）。再把 `eat` 直接在 root cgroup 跑（不設 memory.max）並把 QEMU 記憶體調小到 128M，看全機 OOM 的訊息差異。對照 Ch 22。

2. **看 css_set 共享**：`cat /proc/<pid>/cgroup` 對 mygroup 裡的多個 process，確認它們都是 `0::/mygroup`。理解這些 task 的 `task_struct->cgroups` 指向同一個 `css_set`。（用 gdb 停在 `cgroup_attach_task` 或看 `/sys/kernel/debug/cgroup` 若有開 debug controller。）

3. **cpu.weight 比例驗證**：建 groupA（weight=300）和 groupB（weight=100），各跑一個 `yes > /dev/null`，把兩個 group 綁到同一顆 CPU（`cpuset.cpus` 都設 `0`，記得先 `+cpuset` 下放），用 `top` 看 A:B 的 CPU 佔比約 3:1。閒下一個時另一個能吃滿（work-conserving）。

4. **gdb 追 charge 路徑**（進階）：在 QEMU+gdb（Ch 0），`break mem_cgroup_charge`，在 mygroup 裡跑會配記憶體的程式，`backtrace` 看它從 page fault（Ch 19）一路呼叫進 memcg 記帳。這把 Ch 19/20/22 和本章串起來。

5. **讀 PSI**：Step 3 節流 CPU 時 `cat mygroup/cpu.pressure`，看 `some` 的 avg 在漲（task 因為被節流而卡）。理解 PSI 量的是「卡多久」不是「用多少」。

## 本章重點整理

- namespace（Ch 49）隔離「看到什麼」、cgroup 限制「用多少」、seccomp/LSM/cap（Ch 47/48/49）約束「能做什麼」——三支柱正交，合起來才是容器。
- cgroup v2 用**單一統一階層**取代 v1 的多重階層，一個 process 只屬於一個 cgroup 節點，controller 能協作（writeback 記帳終於做對）；介面是掛在 `/sys/fs/cgroup` 的檔案系統（Ch 33 VFS），`mkdir`/`echo` 就能操作。
- 核心結構：`cgroup`（樹節點）、`cgroup_subsys_state`（css，節點×controller 的交會狀態）、`css_set`（多 task 共享的 cgroup 歸屬組合，RCU 保護，接 Ch 9/27）、`cgroup_subsys`（controller 的 vtable）。
- cpu 接排程器 group scheduling（`cpu.weight` 比例、`cpu.max` 頻寬硬上限，Ch 11–13）、memory 接 memcg（`memory.max`/`high`/`min`、per-page charge、cgroup-OOM、per-cgroup reclaim，Ch 22）、io 接 blk-cgroup（Ch 36）、pids 防 fork bomb、cpuset 綁 CPU/NUMA（Ch 15）；PSI 給 cgroup 級的資源壓力（Ch 22）。

## 自我檢核

- [ ] 不看筆記，能講清楚「為什麼有 namespace 還需要 cgroup」——一句話講出隔離 vs 限制的差別
- [ ] 能說出 cgroup v1 和 v2 最本質的差異（多重 vs 統一階層），以及 v2 為何更好
- [ ] 能解釋 css（`cgroup_subsys_state`）是什麼——為什麼需要「節點 × controller」這個二維交會的抽象，而不是把限制值直接塞進 `cgroup`
- [ ] 面試被問「docker `--memory=512m` 到 kernel 做了什麼」，能從 cgroupfs 寫 `memory.max` 講到 memcg charge、超額 reclaim、cgroup-OOM（接 Ch 22）
- [ ] 能說出 `cpu.weight` 和 `cpu.max` 的差別，以及各自底層接排程器的什麼機制（group sched vs CFS bandwidth control）
- [ ] 能手動建一個 cgroup、限制它的記憶體、跑程式看它被 cgroup-OOM，並在 `dmesg` 認出那條訊息
- [ ] 能解釋 PSI 量的是什麼，為什麼它比 `memory.current` 更能反映一個 cgroup 是否在受苦

## 延伸閱讀

### 官方文件

- **[Documentation/admin-guide/cgroup-v2.rst](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html)** — kernel 官方
  - **讀哪裡**：整篇是 v2 的權威規格。先讀「Basic Operations」（掛載、建 cgroup、subtree_control、no-internal-process 規則）、再讀「Controllers」章各 controller 的旋鈕語意（cpu.weight/max、memory.max/high/min、io.max）
  - **和本章的關聯**：本章每個旋鈕的行為都以這篇為準；本章是導讀，這篇是規格書，遇到邊界行為回來查

- **[Documentation/accounting/psi.rst](https://www.kernel.org/doc/html/latest/accounting/psi.html)** — kernel 官方
  - **讀哪裡**：整篇很短。PSI 的 some/full 定義、三個檔案的格式、觸發器（trigger）機制
  - **能學到什麼**：本章 PSI 一節的完整版，特別是 some vs full 的精確定義，以及如何用 poll 監聽壓力事件（systemd-oomd 就靠它）

### LWN

- **[The cgroup2 controllers（及 unified hierarchy 系列）](https://lwn.net/Articles/679786/)** — Jonathan Corbet, LWN
  - **為什麼值得讀**：v1→v2 的設計動機一手記錄，尤其「為什麼多重階層是錯的」「writeback 記帳為何非統一階層不可」，比讀 spec 更能理解 v2 每個限制背後的取捨
  - **前提**：讀完本章「v1 為什麼被 v2 取代」一節再看，會更有共鳴

- **[Tracking pressure-stall information](https://lwn.net/Articles/759781/)** — LWN
  - **讀哪裡**：PSI 進主線時的介紹，講清楚它要解決「用量高不等於在受苦」這個問題
  - **和本章的關聯**：補強 PSI 一節的設計動機

### 書籍 / 原始碼

- **`mm/memcontrol.c`（v6.12，Bootlin）** — [elixir.bootlin.com/linux/v6.12/source/mm/memcontrol.c](https://elixir.bootlin.com/linux/v6.12/source/mm/memcontrol.c)
  - **讀哪裡**：`mem_cgroup_charge`/`charge_memcg`、`try_charge_memcg`、`mem_cgroup_out_of_memory`。這是 memcg 記帳的心臟，配本章 memory controller 一節的 charge 流程圖對著讀
  - **前提**：先懂 Ch 19/20/22（page fault、reclaim、OOM），memcg 是它們的 cgroup 版

- **`kernel/cgroup/cgroup.c` 與 `kernel/sched/fair.c`（v6.12，Bootlin）**
  - **讀哪裡**：`cgroup.c` 看框架怎麼建樹、遷移 task（`cgroup_attach_task`）；`fair.c` 看 `throttle_cfs_rq`/group scheduling 怎麼把 cpu cgroup 接進排程器
  - **能學到什麼**：本章「框架與 controller 解耦」和「cpu controller 接排程器」兩節的源碼落點

Part 9（安全子系統）到這裡收尾——你現在能從 kernel 角度講清楚一個容器由 namespace、cgroup、seccomp/LSM 三支柱組成。下一章進 Part 10，我們換到觀測與 debug 的底層：kprobes 怎麼在任意 kernel 函式上動態插樁、tracepoint 怎麼在源碼裡預埋靜態探針、uprobes 怎麼探到 user space——這是你在 `bpf` 課從使用者視角用過的東西，現在從 kernel 實作看它怎麼做到。

→ [Ch 51 kprobes/tracepoints/uprobes 底層](./51-kprobes-tracepoints.md)
