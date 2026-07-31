# Ch 42 — 電源管理：cpuidle/cpufreq/runtime PM/suspend-resume

> **目標**：理解 kernel 在「效能」與「省電」之間動態權衡的四層機制——**cpufreq**（跑多快、電壓多高）、**cpuidle**（閒下來睡多深）、**runtime PM**（單一裝置不用就關）、**system suspend**（整機睡眠）——以及它們如何互相咬合。學完你能自己看/調 `/sys` 下的 governor 與 C-state、用 `turbostat`/`powertop` 讀真實功耗、讀懂驅動的 `.suspend`/`.runtime_suspend` 回呼，並在腦中畫出「排程器的負載訊號怎麼一路驅動到 CPU 調頻」。這章對做手機 SoC 韌體的人是核心中的核心。

## 為什麼需要這個？

先算一筆帳。一顆手機 SoC 全速跑起來可以吃掉數瓦，電池只有十幾瓦時——不做電源管理，你的手機亮著螢幕滑個把小時就沒電。資料中心另一個尺度：一排機櫃的電費和冷氣費是營運成本的大頭，CPU 少吃 10% 電，一年省的是實打實的錢。還有散熱：手機沒有風扇，SoC 一熱就得降頻，不然燙手、甚至觸發保護關機。

這三件事——**電池、電費、散熱**——把同一個問題推到 kernel 面前：**CPU 和裝置不該永遠全速運轉，但也不能無腦省電到卡頓**。kernel 得在「使用者按下去要立刻有反應」和「沒事就別燒電」之間，**動態**權衡。這個權衡不是一個開關，而是四個不同時間尺度、不同粒度的子系統疊在一起：

- **有工作要跑時，跑多快？** — cpufreq（DVFS，動態調頻調壓）。尺度：毫秒級。
- **沒工作跑時，CPU 睡多深？** — cpuidle（C-states）。尺度：微秒到毫秒。
- **某個裝置（網卡、感測器、GPU）現在沒人用，要不要關掉它的電？** — runtime PM。尺度：秒級，逐裝置。
- **整台機器沒人用了（螢幕關了、闔上蓋子），要不要整機睡？** — system suspend/resume。尺度：整機，一次全下。

在這些機制出現前，「省電」是各家 BIOS/廠商韌體各自為政的黑盒，kernel 幾乎插不上手。現代 Linux 把這四層都收進統一框架，讓排程器（Ch 11/15）、tickless（Ch 32）、device model（Ch 37）的資訊能餵進來做決策。這章就是把這四層拆開，看它們各自怎麼運作、又怎麼咬合。

## 先建立直覺

四層機制不是平行的四個開關，而是**不同粒度、不同時間尺度**的一組疊層。先把它們在腦中擺成一張圖：

```
                    ┌──────────────────────────────────────────────┐
   整機層           │  system suspend / resume  (kernel/power/)      │
   （最粗）          │  s2idle / S3(suspend-to-RAM) / S4(hibernate)  │
                    │  凍結所有 process → 逐一 suspend 所有裝置       │
                    └───────────────────┬──────────────────────────┘
                                        │ 整機睡下去時，底下三層都停擺
                    ┌───────────────────┴──────────────────────────┐
   逐裝置層          │  runtime PM  (drivers/base/power/runtime.c)    │
                    │  單一裝置閒置 → 自動 suspend；要用 → resume     │
                    │  pm_runtime_get/put 引用計數，沿 device 樹傳播  │
                    └───────────────────┬──────────────────────────┘
                                        │ CPU 也是一種「裝置」，但它有專門子系統：
        ┌───────────────────────────────┴───────────────────────────┐
        │                                                            │
   ┌────┴─────────────────────────┐            ┌───────────────────┴────────┐
   │ cpufreq (drivers/cpufreq/)   │            │ cpuidle (drivers/cpuidle/) │
   │ 有活幹時：跑多快、電壓多高      │            │ 沒活幹時：睡多深（C-state）  │
   │ governor 決策：schedutil ...  │            │ governor 決策：menu / teo   │
   │ 訊號來自排程器的 util（PELT）  │            │ 訊號來自 tickless 的下次事件 │
   └──────────────────────────────┘            └────────────────────────────┘
         ▲                                              ▲
         │ Ch 11/15 排程器把「這個 rq 有多忙」餵上來       │ Ch 32 tickless 說「下次 timer 還有多久」
```

抓住一個核心對稱：**CPU 要嘛在跑（cpufreq 管它多快），要嘛在閒（cpuidle 管它多省）**，兩者互斥、輪流上場。而它們做決策靠的訊號，剛好是你前面章節學過的東西——cpufreq 靠排程器的負載訊號（Ch 15 的 PELT util），cpuidle 靠 tickless 告訴它「下一個 timer 什麼時候到」（Ch 32）。電源管理不是憑空猜，它是**把排程器和時間子系統已經算好的資訊拿來用**。

上面兩層（runtime PM、system suspend）則是「關電」的粗粒度手段：一個逐裝置關，一個整機關，兩者都建立在 Ch 37 的 device 樹上——因為關電有順序，parent 要等 children 都關了才能關。

## cpufreq：有工作時，跑多快、電壓多高

源碼在 `drivers/cpufreq/`，核心是 `drivers/cpufreq/cpufreq.c`。

### DVFS 的物理基礎

CPU 的動態功耗大致正比於 `C · V² · f`——電容、電壓平方、頻率。頻率降一半、功耗大約減半；但**電壓能一起降**才是關鍵，因為那是平方項。低頻可以搭配低電壓穩定運作，於是「降頻 + 降壓」（Dynamic Voltage and Frequency Scaling, **DVFS**）能把功耗砍到遠低於線性。代價是：跑得慢，同一份工作花更久。

所以問題變成：**什麼時候該高頻高壓（快、費電），什麼時候該低頻低壓（慢、省電）？** 這個決策由 **governor**（調速器）做。

### governor：決策策略

cpufreq 把「機制」（怎麼真的改頻率——這是各家 SoC 驅動的事，例如 `drivers/cpufreq/intel_pstate.c`、ARM 的 `cpufreq-dt.c`）和「策略」（什麼時候改到多少）分開。策略就是 governor：

| governor | 策略 | 適用 |
|---|---|---|
| `performance` | 永遠鎖最高頻 | 要極致延遲、不在乎電（benchmark、即時性）|
| `powersave` | 永遠鎖最低頻 | 極省電、不在乎慢 |
| `ondemand` | 週期性取樣 CPU 使用率，忙就升頻、閒就降 | 舊預設，現已多半被 schedutil 取代 |
| `conservative` | 像 ondemand 但升頻較保守、漸進 | 電池裝置要更平滑 |
| `schedutil` | **由排程器直接驅動**，用 PELT util 訊號即時調頻 | 現代主流預設 |
| `userspace` | 頻率完全交給 user 空間程式設定 | 特殊用途、實驗 |

`performance` / `powersave` 的源碼小到一看就懂（`drivers/cpufreq/cpufreq_performance.c` 幾十行），因為它們不做決策，只鎖端點。真正有內容的是 `ondemand` 和 `schedutil`。

### 為什麼 schedutil 比 ondemand 好

`ondemand`（`drivers/cpufreq/cpufreq_ondemand.c`）的做法是**自己另開一個週期性取樣**：每隔一段時間（預設幾十毫秒）醒來，看「上一段時間 CPU 的 idle 佔比多少」，忙就跳高頻、閒就降。它的問題有三個：

1. **它是外人**。ondemand 從外部觀察 CPU 忙不忙，靠的是「過去這段時間有多忙」——這是**滯後**的資訊。等它取樣到「變忙了」再升頻，工作已經在低頻上慢跑了一陣。
2. **取樣週期是個尷尬的 magic number**。太短則自己耗電、抖動；太長則反應遲鈍。怎麼調都不對。
3. **它和排程器各算各的**。排程器其實**早就知道**負載——它每次 enqueue/dequeue task、每個 tick 都在更新每個 task 和每個 runqueue 的 PELT（Per-Entity Load Tracking，Ch 15）util 訊號。ondemand 卻無視這份現成資料，自己重新估一遍。

`schedutil`（`kernel/sched/cpufreq_schedutil.c`——注意它住在 `kernel/sched/` 而非 `drivers/cpufreq/`，這本身就說明它是排程器的一部分）反過來：**排程器每次更新 runqueue 的 util 訊號時，順手呼叫 `cpufreq_update_util()`**，把「這個 CPU 現在需要多少算力」即時推給 cpufreq。決策公式概念上是：

```
   目標頻率 ≈ 1.25 × max_freq × (util / max_capacity)
```

那個 `1.25`（`sugov` 裡的 headroom）是刻意留的餘裕：如果剛好把頻率調到「勉強夠用」，一旦負載再漲一點就會延遲，所以多給 25% 空間。這比 ondemand 好在：

- **即時**：util 一漲，同一條排程路徑上就調頻，不等下次取樣。
- **同源**：用的是排程器已經維護好的訊號，不重複計算、不需要自己的取樣週期。
- **和 EAS 一致**：ARM 的 Energy Aware Scheduling（EAS，Ch 15）在決定「這個 task 放哪顆核」時，用的能量模型和 schedutil 調頻用的 util 是**同一套**，於是「放哪跑」和「跑多快」的決策不會打架。

> 這是「把已有訊號拿來用，別重算」設計哲學的漂亮案例。schedutil 之所以成為預設，不是因為演算法多聰明，而是因為它**站在排程器的資訊源頭**，而 ondemand 站在下游猜。

### P-state 與硬體自主

x86 的 Intel/AMD 把 DVFS 包成 **P-state**（Performance state，P0 最快、P1、P2… 越大越慢省電），由 `intel_pstate` / `amd-pstate` 驅動接管。現代 x86 甚至有 **HWP（Hardware-managed P-states）**：kernel 只給一個「偏好範圍與提示」，實際頻率由 CPU 內部的功耗控制器**每毫秒級自主調**——因為硬體比 kernel 更快看到自己的溫度和電流。這時 kernel 的角色從「精確控制」退成「給邊界和 hint」。ARM 世界則多半是 kernel 透過 `cpufreq-dt`（device tree 描述 OPP，Operating Performance Point 表）直接設頻率點。

### schedutil 掛在排程器哪裡

具體看它怎麼「順手」被呼叫：排程器在更新 runqueue 的 util 訊號時（`kernel/sched/fair.c` 的 `update_load_avg()` 等路徑，以及 enqueue/dequeue task、tick）會呼叫 `cpufreq_update_util(rq, flags)`（`include/linux/sched/cpufreq.h`）。這個函式透過一個 per-CPU 的 hook 指標，最終落到 schedutil 的 `sugov_update_single_freq()` / `sugov_update_shared()`（`kernel/sched/cpufreq_schedutil.c`）。也就是說**沒有額外的執行緒或計時器在「輪詢 CPU 忙不忙」——調頻決策就長在排程器每次算負載的那條路徑上，這是 schedutil 零額外開銷的來源**。多顆 CPU 共享一個頻率域（policy）時（同叢集只能一起調頻），schedutil 取該域內所有 CPU 的 util 最大值來決定共同頻率。

## cpuidle：沒工作時，睡多深

源碼在 `drivers/cpuidle/`，核心是 `drivers/cpuidle/cpuidle.c` 與 `drivers/cpuidle/governors/`。

### C-states：越深越省，喚醒越慢

當一顆 CPU 沒有 runnable task 時，排程器會挑 **idle task**（Ch 3/11 的 idle class，每 CPU 一個 PID 0 的 swapper）來跑。idle task 做的事就是——**讓 CPU 進入低功耗閒置狀態（C-state）**。

C-state 是一個階梯，越深越省電，但**喚醒延遲越大**：

```
   C0   ── 正常執行（不是閒置狀態，這是「在跑」）
   C1   ── halt，停時脈，喚醒最快（奈秒～微秒級）
   C2   ── 停更多時脈，快取還在
   C3   ── 開始 flush 部分快取
   C6   ── 電壓降到近乎關核，快取內容丟掉，喚醒最慢（幾十～上百微秒）
   ...   （實際名稱、層數由各 SoC 定義，見 /sys/.../cpuidle/stateN/）
```

深睡省更多電，但**進去和出來都要花時間與能量**（要 flush 快取、要重建電壓）。所以有個核心權衡：**如果 CPU 只會閒 5 微秒，卻進了一個「進出要花 80 微秒」的深 C-state，你不但沒省到電，還虧了——喚醒的能量成本超過睡覺省的**。這叫 break-even：只有「預期閒置時間 > 該 state 的進出成本」才值得進去。

### idle governor：預測會閒多久

於是問題變成一個**預測問題**：CPU 現在要閒了，它會閒多久？該進哪個 C-state？這由 idle governor 決定，兩個主流實作：

- **`menu`**（`drivers/cpuidle/governors/menu.c`）：綜合多個訊號猜下次喚醒時間——**下一個 timer 什麼時候到**（這正是 Ch 32 tickless 提供的資訊：`tick_nohz_get_sleep_length()`）、最近幾次實際閒置時間的模式、以及有沒有 I/O 在等。它還會依「這段時間有多可能被中斷提前叫醒」打折扣。
- **`teo`**（Timer Events Oriented，`drivers/cpuidle/governors/teo.c`）：較新，更專注於「以 timer 事件為主的預測」，用最近幾次的實際 residency（真的睡了多久）做統計修正，避免 menu 在某些負載下過度保守。多數新系統預設 teo。

無論哪個，**核心輸入都是「下一個已排定的 timer 事件還有多久」——這就是為什麼 tickless（Ch 32）是深睡的前提**：如果每 1ms 有一個週期性 tick 硬要叫醒 CPU，它永遠睡不超過 1ms，深 C-state 根本進不去。tickless（`CONFIG_NO_HZ_IDLE`）把「閒置時的週期 tick」關掉，CPU 才可能一睡數十毫秒直到真正有事——這時深 C-state 才有意義。**tickless 和 cpuidle 是綁在一起的一對**：沒有 tickless，cpuidle 的深睡形同虛設。

### 一次進 idle 的流程

把它串起來，一顆 CPU 從「沒事做」到「睡下去」的路徑：

```
   排程器 pick_next_task 選不到 runnable task
        │
        ▼
   跑 idle task（idle class，Ch 11）→ do_idle()  (kernel/sched/idle.c)
        │
        ▼
   cpuidle_idle_call()  (drivers/cpuidle/cpuidle.c)
        │
        ├─► governor->select()   ← menu/teo 預測「會閒多久」，選一個 state index
        │        （輸入：tick_nohz_get_sleep_length() 下次 timer、歷史 residency）
        │
        ├─► cpuidle_enter()      ← 真的執行進入該 C-state 的動作（各 SoC 的 enter 回呼）
        │        （x86 多半是 mwait/monitor；ARM 是 WFI/PSCI cpu_suspend）
        │
        ▼  ...CPU 睡著，直到中斷/IPI 叫醒...
        │
        ▼
   醒來，回到 do_idle 迴圈 → 重新 pick_next_task
```

governor 選 state → 硬體真的進 state → 睡 → 中斷喚醒 → 回排程器。cpuidle 只負責「選多深 + 進去」，「什麼時候醒」由中斷決定，「醒來做什麼」交回排程器。

## runtime PM：單一裝置閒置就關

源碼在 `drivers/base/power/runtime.c`（介面宣告在 `include/linux/pm_runtime.h`）。

cpufreq/cpuidle 管的是 CPU。但一台機器上還有一堆**別的裝置**：GPU、網卡、USB 控制器、I2C 上的感測器、SoC 內建的一堆 IP block。這些東西「當下沒人用」時，也該關電——但關的粒度是「單一裝置」，時機是「系統照常運行、只是這個裝置閒著」。這就是 **runtime PM（runtime power management）**。

### 引用計數：pm_runtime_get / pm_runtime_put

runtime PM 的核心是一個**每裝置的使用計數（usage_count）**。概念和 Ch 37 kobject 的引用計數同構：

```c
   pm_runtime_get_sync(dev);   // usage_count++；若裝置在睡，同步喚醒它，等它醒
   /* ... 用這個裝置做事，例如發一次 I2C transfer ... */
   pm_runtime_put(dev);        // usage_count--；歸零就（可能）安排 suspend
```

規則：**只要 `usage_count > 0`，裝置保持 active（開電）；歸零，runtime PM 框架就可以呼叫驅動的 `.runtime_suspend()` 把它關掉**。驅動作者的責任是：每次要碰硬體前 `get`、用完 `put`，剩下的省電決策交給框架。這樣「裝置閒就關、要用自動醒」就自動發生，驅動不用自己寫狀態機。

### autosuspend：別關太急

有個現實問題：如果 `put` 一歸零就立刻 suspend，而下一毫秒又有人 `get`，你就在瘋狂地開開關關——每次開關都有延遲和能量成本，得不償失。解法是 **autosuspend delay**：`put` 歸零後不立刻關，而是啟動一個延遲計時器（例如「閒 100ms 才真的關」），這段時間內若又被 `get` 就取消關閉。

```c
   pm_runtime_use_autosuspend(dev);
   pm_runtime_set_autosuspend_delay(dev, 100);   // 閒 100ms 才 suspend
   ...
   pm_runtime_mark_last_busy(dev);   // 標記「剛用過」
   pm_runtime_put_autosuspend(dev);  // 歸零後等 delay 再關
```

這個 delay 是省電與反應速度的旋鈕：delay 短則省更多電但可能頻繁開關，delay 長則平滑但省得少。你在 `/sys/devices/.../power/autosuspend_delay_ms` 能看到、也能調它。

### 沿 device 樹傳播（接 Ch 37）

裝置不是孤島——它們掛在 Ch 37 的 device 樹上，有 parent/child 關係。一個 I2C 感測器的 parent 是 I2C 控制器，控制器的 parent 是某條 bus。**runtime PM 的關鍵約束：parent 不能比 child 先睡**。如果 I2C 控制器睡了，掛在它下面的感測器就沒法通訊了。

runtime PM 框架自動處理這個：每個 child active 時會對 parent 隱式 `get`（`pm_runtime_get`），於是「只要還有一個 child 醒著，parent 就被計數頂著不能睡」。等所有 child 都 suspend、parent 自己的 usage_count 也歸零，parent 才能睡。這條「由下而上、children 先睡 parent 後睡」的順序，是 Ch 37 device 樹在電源管理上的直接應用。

## system suspend/resume：整機睡眠

源碼在 `kernel/power/`（`suspend.c`、`hibernate.c`、`process.c` 等）。

前面三層都是「系統照常運行中的局部省電」。system suspend 是另一回事：**整台機器都不用了**（螢幕關了、闔上筆電、手機黑屏一段時間），把整機推進睡眠，只留最低限度的電維持能被喚醒。

### 三種睡法：s2idle / S3 / hibernate

| 睡法 | 別名 | 保住什麼 | 喚醒速度 | 耗電 |
|---|---|---|---|---|
| **s2idle** | suspend-to-idle | 幾乎全部（CPU 進深 idle，裝置 runtime-suspend） | 最快（幾乎即時） | 最多（RAM 全保、部分裝置仍供電）|
| **S3** | suspend-to-RAM、STR | 只保 RAM 供電，CPU/多數裝置斷電 | 快（一兩秒） | 少（只有 RAM self-refresh 的電）|
| **S4** | hibernate、suspend-to-disk | RAM 內容存到磁碟後**全關**，等同關機 | 慢（要從磁碟讀回整份記憶體） | 幾乎零（真的斷電）|

`s2idle` 是純軟體方案：不依賴 BIOS/ACPI 的 S3 支援，只是把所有 CPU 塞進最深 idle、裝置盡量 runtime-suspend。現代很多筆電（尤其 ACPI 只宣告 s2idle 的 "Modern Standby" 機器）走這條。**S3** 靠平台韌體真的切斷電源軌，省更多但依賴硬體/BIOS 正確實作。**hibernate（S4）** 把整份 RAM 映像寫進 swap 分區然後關機，開機時 bootloader/kernel 再把映像讀回來「續跑」——省電最徹底但慢，且需要夠大的 swap。

hibernate 的技術細節值得單獨點一下（`kernel/power/hibernate.c` + `snapshot.c`）：它要把「當下整份記憶體狀態」凍結成一個一致的快照寫進磁碟，但寫的過程本身也在用記憶體。做法是先 `freeze_processes`、`dpm_suspend`，做出一份記憶體快照映像（snapshot），把它寫進 swap 上的 hibernation image，再真正關機。開機時 bootloader 正常載入 kernel，早期發現「有 hibernation image」就走 resume 路徑，把映像讀回原本的物理頁、復原 CPU 狀態，跳回睡前那一刻。這也是為什麼 hibernate 對 kernel/硬體版本敏感——映像是「當時那顆 kernel 的記憶體佈局」，換了 kernel 就對不上。用 `echo disk > /sys/power/state` 觸發。

### suspend 的流程（接 Ch 37 的裝置順序）

整機 suspend 不是一個指令就下去，它是一條有嚴格順序的流水線（`kernel/power/suspend.c` 的 `suspend_devices_and_enter()` 一路下去）：

```
   使用者/policy 觸發 suspend（echo mem > /sys/power/state）
        │
        ▼
   1. freeze_processes()         凍結所有 userspace 行程（kernel/power/process.c）
        │                        （送 fake signal 讓它們停在安全點，不再跑）
        ▼
   2. dpm_suspend_start()        逐一 suspend 所有裝置，呼叫各驅動的 .suspend
        │                        順序：沿 Ch 37 device 樹「children 先、parent 後」
        │                        （葉子裝置先睡，bus/controller 後睡）
        ▼
   3. suspend_enter()            關掉 nonboot CPU（只留一顆），停 timer，
        │                        最後把最後一顆 CPU 也放進 suspend/深睡
        ▼
   ...整機睡著，等 wakeup source（電源鍵、RTC、網路封包...）...
        │
        ▼
   resume：完全反向——喚醒 CPU → dpm_resume（parent 先、children 後）→ thaw_processes
```

兩個和前面章節咬合的點：

- **凍結行程（freeze）** 是為了讓裝置 suspend 時沒有 userspace 還在戳硬體。它靠給每個行程送一個特殊 signal，讓它們停在 `try_to_freeze()` 的檢查點——這用到 Ch 16 signal 機制的變體。
- **裝置 suspend/resume 的順序完全由 Ch 37 的 device 樹決定**。suspend 時葉子先睡（否則 parent 先斷電，child 就沒法乾淨關閉）；resume 反過來，parent 先醒（child 得靠 parent 才能通訊）。這就是 Ch 37 說「device 樹決定 suspend/resume 順序」的兌現。

## 裝置電源回呼：dev_pm_ops

不管是 runtime PM 還是 system suspend，最終都要呼叫**驅動自己實作的回呼**——因為只有驅動知道自家硬體怎麼安全地關電、關前要存什麼狀態。這些回呼集中在 `struct dev_pm_ops`（`include/linux/pm.h`）：

```c
static const struct dev_pm_ops my_pm_ops = {
    /* system suspend/resume（整機睡）*/
    .suspend         = my_suspend,          // 整機要睡：存狀態、關硬體
    .resume          = my_resume,           // 整機醒：復原狀態、重開硬體
    .freeze          = my_freeze,           // hibernate 專用：存映像前
    .restore         = my_restore,          // hibernate 專用：從映像復原後

    /* runtime PM（單裝置閒置）*/
    .runtime_suspend = my_runtime_suspend,  // 這個裝置閒了：關它
    .runtime_resume  = my_runtime_resume,   // 要用了：開它
    .runtime_idle    = my_runtime_idle,     // 「看起來閒了」的 hint
};

/* 常用捷徑：system suspend 和 runtime 用同一組實作 */
static const struct dev_pm_ops my_pm_ops = {
    SET_RUNTIME_PM_OPS(my_rpm_suspend, my_rpm_resume, NULL)
    SET_SYSTEM_SLEEP_PM_OPS(my_suspend, my_resume)
};
```

把它掛到 driver 上（platform driver 為例，接 Ch 39）：

```c
static struct platform_driver my_driver = {
    .driver = {
        .name = "my-device",
        .pm   = &my_pm_ops,      // ← device model 電源框架從這裡找回呼
        .of_match_table = my_of_match,
    },
    .probe = my_probe,
};
```

`.suspend`（整機睡）和 `.runtime_suspend`（單裝置閒）常常做**類似**的事（都是關硬體），但語意不同：整機 suspend 是「全系統要睡了，無條件關」；runtime suspend 是「就這個裝置閒著，系統還在跑」。寫驅動時最容易出的錯就是把兩者搞混、或忘了在 resume 時把 suspend 存的狀態復原——關電是雙向的，關了要能一模一樣地開回來。

## thermal：太熱就降頻（接 cpufreq）

源碼在 `drivers/thermal/`。

前面談的都是「主動省電」。thermal 是**被動保護**：溫度超過門檻時，強制降低功耗，避免燙壞或觸發硬體關機。手機上這件事**極其常見**——玩遊戲/開相機幾分鐘就會摸到 SoC 發燙，這時 kernel 的 thermal 框架正在偷偷降你的頻率（俗稱 thermal throttling）。

三個核心概念：

- **thermal zone**（`struct thermal_zone_device`）：一個溫度感測點（例如「CPU 大核附近的溫度」），周期性讀 sensor，和一組 **trip point**（門檻溫度）比較。
- **trip point**：門檻，例如「80°C 開始限制、95°C 緊急、110°C 直接關機」。
- **cooling device**（`struct thermal_cooling_device`）：能降溫的「手段」。最常見的 cooling device 就是 **cpufreq**——當 zone 溫度過 trip，thermal 框架**限制 cpufreq 能用的最高頻率**（透過 `cpufreq_cooling`，`drivers/thermal/cpufreq_cooling.c`），把 CPU 頻率壓下去，功耗降、溫度回落。其他 cooling device 還有降 GPU 頻率、開風扇（有風扇的話）、甚至限制某些核完全關閉。

zone 和 cooling device 之間由 **governor**（thermal 也有自己的 governor，如 `step_wise`、`power_allocator`）決定「過門檻多少、該降多少」。`power_allocator`（IPA，Intelligent Power Allocation）更進一步：給整個 SoC 一個功耗預算，在 CPU/GPU 之間動態分配——這在手機遊戲場景很關鍵，決定了「掉幀還是燙手」的取捨。

> thermal 和 cpufreq 是一組相愛相殺：cpufreq 想升頻求效能，thermal 在它太熱時把上限壓回來。你在 benchmark 看到「跑一開始很快、幾分鐘後掉下來」的曲線，多半就是 thermal throttling 在起作用——這也是 perf_bench 課裡「效能測試要跑夠久才看得到穩態」的物理原因。

## MTK / ARM 實例：手機 SoC 的電源現實

做手機晶片韌體，上面每一層你都會實際碰到，而且 ARM/Android 有幾個 x86 桌機看不到的重點：

### big.LITTLE 與 EAS（接 Ch 15）

手機 SoC 是**異質多核**：幾顆高效能大核（性能好、費電）+ 幾顆節能小核（慢、超省電），甚至三叢集（prime/big/little）。省電的關鍵決策是：**這個 task 該放大核還是小核？** 這由 **EAS（Energy Aware Scheduling，Ch 15）** 決定——排程器帶一個 **能量模型（Energy Model，`drivers/opp/` + `kernel/power/energy_model.c`）**，描述每顆核在每個頻率點的功耗，於是排程器能算「把這個 task 放小核 vs 大核，總能量各是多少」，挑省電的那個放。

這裡 EAS 和 schedutil 是同一套 util 訊號的兩個用途：**EAS 決定「放哪顆核」，schedutil 決定「那顆核跑多快」**，兩者用同一個 PELT util，所以省電決策一致。背景的音樂播放、通知輪詢這種輕載，EAS 會塞到小核，讓大核整叢集深睡（cluster idle）；前景滑動、遊戲這種重載才喚醒大核。

### 螢幕關掉後的深度睡眠

Android 手機黑屏後不會馬上 system suspend——它會先進 **doze**（螢幕關但系統還醒著處理背景），閒夠久才進更深的睡眠。這時大量裝置走 runtime PM 關電、CPU 進最深 C-state、tickless 讓 CPU 一睡數十毫秒。**闔上/長時間不用**才真的走 s2idle/S3 整機睡。

### wakelock / wakeup source（Android 特有）

Android 在 kernel 加了 **wakeup source / wakelock** 機制（`drivers/base/power/wakeup.c`）：只要有一個 wakelock 被持著，系統就**不准進 suspend**。App 想在背景做事（下載、播音樂）就持一個 wakelock 頂住。這是「省電 vs 讓 App 跑完事」的仲裁點，也是手機耗電問題的頭號嫌犯——一個沒放掉的 wakelock（wakelock 洩漏）能讓手機整夜不睡、早上沒電。`/sys/kernel/debug/wakeup_sources` 能看誰在頂著。

## 動手：讀與調電源狀態

以下在你 Ch 0 的 QEMU 環境或任何 Linux 機器上都能試（QEMU 的虛擬 CPU 對 cpufreq/cpuidle 支援有限，**在真實硬體或裝了 `cpupower`/`powertop` 的 host 上跑效果最好**）。

### 看/調 cpufreq

```bash
# 目前每顆 CPU 的 governor
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor
# 可選的 governor
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_available_governors
# 目前頻率 / 硬體支援的頻率範圍
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq
cat /sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_min_freq
cat /sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq

# 切 governor（需 root）
echo performance | sudo tee /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor

# 用 cpupower 一次看全（cpupower 屬 linux-tools 套件）
cpupower frequency-info
```

`cpupower frequency-info` 會告訴你當前 driver（intel_pstate / acpi-cpufreq / cpufreq-dt）、governor、可用頻率點——一眼看清這台機器的 cpufreq 全貌。

### 看 cpuidle（C-state residency）

```bash
# 這顆 CPU 有哪些 idle state
ls /sys/devices/system/cpu/cpu0/cpuidle/
# 某個 state 的名字、進出延遲、實際待了多久
cat /sys/devices/system/cpu/cpu0/cpuidle/state1/name
cat /sys/devices/system/cpu/cpu0/cpuidle/state1/latency      # 進出延遲(us)
cat /sys/devices/system/cpu/cpu0/cpuidle/state1/time         # 累計待在此state(us)
cat /sys/devices/system/cpu/cpu0/cpuidle/state1/usage        # 進入次數

# 目前的 idle governor
cat /sys/devices/system/cpu/cpuidle/current_governor
```

`residency`（各 state 累計待多久）能告訴你 CPU 平常都睡多深：如果幾乎都停在 C1（淺睡），代表有東西一直在把它叫醒（可能是某個高頻中斷/timer），深睡沒發揮——這正是排查耗電的起手式。

### turbostat / powertop：看真實功耗

```bash
# turbostat：每顆 CPU 的實際頻率、各 C-state 佔比、封裝功耗（需 root、真實硬體）
sudo turbostat --interval 1

# powertop：互動式看「誰在耗電、誰在頻繁喚醒 CPU」，還會建議省電設定
sudo powertop
```

`turbostat` 的 `Busy%`、`Bzy_MHz`（實際運轉頻率）、`CPU%c6`（在 C6 深睡的時間佔比）、`PkgWatt`（封裝功耗）是讀電源行為最直接的儀表。`powertop` 的 "Wakeups-from-idle per second" 排行榜直接指出「誰在阻止 CPU 深睡」——耗電偵探的第一站。

### 看 runtime PM 狀態

```bash
# 某個裝置目前的 runtime PM 狀態
cat /sys/devices/.../power/runtime_status     # active / suspended / suspending...
cat /sys/devices/.../power/control            # auto（允許 runtime PM）/ on（強制常開）
cat /sys/devices/.../power/autosuspend_delay_ms

# 強制某裝置常開（禁用它的 runtime PM，debug 用）
echo on | sudo tee /sys/devices/.../power/control
```

把一個裝置的 `control` 設成 `on` 會禁用它的 runtime PM（永遠 active），這是排查「某裝置 runtime-suspend 後行為異常」的手段——先禁掉 runtime PM 看問題會不會消失。

## 對比與取捨

| 機制 | 粒度 | 時間尺度 | 決策依據 | 主要子系統 |
|---|---|---|---|---|
| cpufreq | 每 CPU/叢集 | 毫秒 | 排程器 util（schedutil）| `drivers/cpufreq/` |
| cpuidle | 每 CPU | 微秒～毫秒 | 下次 timer（tickless）| `drivers/cpuidle/` |
| runtime PM | 每裝置 | 秒 | usage_count 引用計數 | `drivers/base/power/runtime.c` |
| system suspend | 整機 | 一次性 | policy/user/闔蓋 | `kernel/power/` |
| thermal | 每 zone | 秒 | 溫度 vs trip point | `drivers/thermal/` |

| governor（cpufreq）| 反應速度 | 省電 | 何時選 |
|---|---|---|---|
| performance | 即時最快 | 最差 | benchmark、低延遲需求 |
| schedutil | 快（排程器驅動）| 好 | 現代通用預設 |
| ondemand | 慢（取樣滯後）| 中 | 舊系統遺留 |
| powersave | — | 最省 | 極省電、可忍受慢 |

## 踩雷集錦

1. **錯誤直覺：「降頻一定省電」**。不一定。DVFS 有個 **race-to-idle** 悖論：把工作用高頻快速做完、然後整段深睡（C6），有時比用低頻慢慢磨、CPU 一直半醒著更省電——因為靜態漏電流（leakage）和「一直沒真正深睡」的成本可能超過高頻多花的動態功耗。到底哪個省，取決於工作特性和該 SoC 的漏電。別假設「頻率越低越省」。

2. **錯誤直覺：「開了 tickless 就能深睡」**。tickless 是**必要非充分**。就算關了週期 tick，只要有任何一個高頻中斷/timer（一個沒調好的網卡中斷、一個 1ms 輪詢的驅動）在叫醒 CPU，它照樣睡不深。用 `powertop` 的 wakeup 排行找出那個罪魁。

3. **錯誤直覺：「runtime PM 的 suspend 和 system suspend 的 suspend 是同一件事」**。不是。`.runtime_suspend`（單裝置閒置、系統照跑）和 `.suspend`（整機要睡）語意不同、觸發時機不同、對「還能不能被其他裝置依賴」的假設也不同。用 `SET_RUNTIME_PM_OPS` / `SET_SYSTEM_SLEEP_PM_OPS` 分開掛，別用一個函式硬扛兩種語意。

4. **忘了 resume 復原狀態**。關電是雙向的：`.suspend` 存了什麼、關了什麼，`.resume` 就得原樣復原。最常見的 bug 是 suspend 時把某個暫存器/GPIO 狀態關掉，resume 忘了設回去，結果整機醒來後那個裝置壞掉。寫任一半就要立刻寫另一半。

5. **wakelock/wakeup source 洩漏**。Android 上持了 wakelock 忘了放，系統永遠不睡，一夜掉光電。這是手機耗電問題的頭號原因。`/sys/kernel/debug/wakeup_sources` 找持有時間異常長的那個。

6. **在 QEMU 裡量電源行為**。QEMU 的虛擬 CPU 對 cpufreq/cpuidle 的支援殘缺（`scaling_available_frequencies` 可能是空的、C-state 只有 C1），拿它量功耗曲線沒意義。電源實驗要在**真實硬體**上做——這是本課少數「QEMU 不夠用」的主題之一。

## 進階：再往深一層

- **schedutil 的 rate limit**：schedutil 不會每次 util 微動就改頻率（改頻率本身有成本、要等硬體穩定），它有 `rate_limit_us`（升頻/降頻各一個門檻），限制最短改頻間隔。太小則頻繁改頻抖動、太大則反應遲鈍——又一個省電 vs 反應速度的旋鈕。
- **PSCI（ARM 的電源介面）**：ARM64 的 cpuidle 進深睡、system suspend 關核，最終都透過 **PSCI（Power State Coordination Interface）** 這個 SMC 呼叫進 firmware（EL3 的 ATF/TF-A）真的斷核電。所以 ARM 的電源管理是 kernel 和 secure firmware 協作的——這接 arm 課的 TrustZone/EL3 與 exception level 概念。
- **energy model 從哪來**：EAS 的能量模型不是憑空生的，來自 device tree 的 OPP 表（每個頻率點的電壓/功耗）+ `drivers/opp/` 解析。SoC 廠商（含 MTK）在 DTS 裡填這些數字，填錯了 EAS 就把 task 放錯核、白耗電——這是韌體工程師實際要調的東西。
- **面試常問**：「schedutil 為什麼取代 ondemand？」（答：站在排程器訊號源頭、即時、與 EAS 同源，不像 ondemand 在下游滯後取樣）、「深 C-state 為什麼需要 tickless？」（答：週期 tick 會把 CPU 週期性叫醒，睡不超過一個 tick 週期）、「big.LITTLE 上輕載為什麼放小核？」（答：EAS 用能量模型算出小核跑輕載總能量更低）。

## 動手練習

1. **切 governor 觀察頻率**：`watch -n0.5 cat .../scaling_cur_freq`，另一個終端切 `performance` vs `powersave`，跑個 `yes > /dev/null` 壓一顆核，看兩種 governor 下頻率怎麼變。體會 schedutil（若可用）如何隨負載即時調。
2. **抓深睡殺手**：`sudo powertop`，看 "Wakeups-from-idle per second" 排行，找出把 CPU 叫醒最多次的是誰。把一個會頻繁喚醒的東西（例如某個 `ping -i 0.01`）跑起來，看排行怎麼變、`turbostat` 的 `CPU%c6` 怎麼掉。
3. **讀 runtime PM 狀態**：找一個支援 runtime PM 的裝置（USB 裝置、某個 PCI 裝置），`cat .../power/runtime_status`，把 `control` 在 `auto`/`on` 之間切，觀察狀態變化。若有 turbostat/功耗計，看禁用 runtime PM 前後的功耗差。
4. **讀源碼畫流程**：到 `https://elixir.bootlin.com/linux/v6.12/source` 找 `kernel/sched/idle.c` 的 `do_idle()` 和 `drivers/cpuidle/cpuidle.c` 的 `cpuidle_idle_call()`，自己畫一遍「排程器選 idle task → 選 C-state → 進睡 → 中斷喚醒」的流程，對照本章的 ASCII 圖。
5. **（進階）寫一個帶 runtime PM 的假驅動**：在 Ch 39 的 platform driver 骨架上加 `dev_pm_ops` 的 `.runtime_suspend/.runtime_resume`，各印一行 `pr_info`，在 probe 裡 `pm_runtime_enable` + `pm_runtime_set_autosuspend_delay`，用 sysfs 觸發，`dmesg` 看回呼真的被呼叫。

## 本章重點整理

- 電源管理是四層疊起來的：**cpufreq**（有活跑多快）、**cpuidle**（沒活睡多深）、**runtime PM**（單裝置閒就關）、**system suspend**（整機睡），粒度與時間尺度各不同。
- 現代 cpufreq 用 **schedutil**，直接吃排程器的 PELT util 訊號即時調頻，比舊的 ondemand（下游滯後取樣）更好、且與 ARM 的 EAS 同源。
- cpuidle 的深睡靠 **tickless（Ch 32）** 提供的「下次 timer 還有多久」來預測該睡多深；沒有 tickless，深 C-state 形同虛設。
- runtime PM 用 **usage_count 引用計數** + autosuspend delay 管單裝置電源，並沿 **Ch 37 的 device 樹**由下而上傳播（children 先睡 parent 後睡）；system suspend 的裝置順序同樣由這棵樹決定。
- 手機 SoC 把這些推到極致：**big.LITTLE + EAS（Ch 15）** 決定 task 放大核還小核、thermal throttling 過熱降頻、Android 的 wakelock 仲裁「能不能睡」——這些是 MTK 韌體工程師的日常。

## 自我檢核

- [ ] 不看筆記，能畫出 cpufreq / cpuidle / runtime PM / system suspend 四層的粒度與時間尺度差異
- [ ] 能解釋 schedutil 為什麼比 ondemand 好（訊號來源、即時性、與 EAS 的關係）
- [ ] 能說出「深 C-state 為什麼需要 tickless」以及 idle governor 靠什麼訊號預測
- [ ] 能區分 `.suspend` 與 `.runtime_suspend` 的語意差異，知道各自何時被呼叫
- [ ] 能解釋 runtime PM 的 usage_count 如何沿 device 樹傳播、為什麼 parent 不能比 child 先睡
- [ ] 面試被問「big.LITTLE 上輕載為什麼放小核、由誰決定」，你能答出 EAS + 能量模型
- [ ] 能用 `cpupower frequency-info`、`turbostat`、`powertop` 讀出一台機器當下的電源行為

## 延伸閱讀

### 官方文件

- **[Documentation/admin-guide/pm/](https://www.kernel.org/doc/html/latest/admin-guide/pm/index.html)**
  - **讀哪裡**：`cpufreq.rst`（governor 與 sysfs 介面）、`intel_pstate.rst`（x86 P-state/HWP）、`working-state.rst`（system-wide power states 總覽）
  - **和本章的關聯**：本章的 sysfs 路徑、governor 名稱都以這裡為權威；調實體機器前先讀對應那篇

- **[Documentation/driver-api/pm/](https://www.kernel.org/doc/html/latest/driver-api/pm/index.html)**
  - **讀哪裡**：`devices.rst`（`dev_pm_ops` 每個回呼何時被呼叫、順序保證）、`runtime_pm.rst`（runtime PM 完整 API 與引用計數規則）
  - **能學到什麼**：寫驅動電源回呼的權威規格；本章「動手」第 5 題的 `pm_runtime_*` 用法細節全在這

### 深入文章

- **[LWN: Schedutil, the scheduler-driven CPU frequency governor](https://lwn.net/Articles/682391/)** — LWN.net
  - **讀哪裡**：整篇，講 schedutil 的動機與它和排程器的耦合
  - **為什麼值得讀**：本章「為什麼 schedutil 比 ondemand 好」那節的一手來源，把排程器 util → 調頻的耦合講得比本章更細
  - **前提**：讀過 Ch 15（PELT/util 訊號）

- **[LWN: The teo cpuidle governor](https://lwn.net/Articles/775618/)** — LWN.net
  - **讀哪裡**：teo 的預測邏輯與它相對 menu 的改進
  - **能學到什麼**：idle governor 到底怎麼「預測會閒多久」，補足本章對 menu/teo 的概述

### 書籍 / 進階

- **《Linux Kernel Development, 3rd Ed.》** — Robert Love
  - **這本的定位**：電源管理只是點到，但它對排程器（Ch 11-15 對應）和 idle task 的講解，是理解 cpuidle 為什麼由 idle task 觸發的背景
  - **注意**：講的是較舊 kernel，schedutil/EAS 都還沒出現，這些以本章與 LWN 文章為準

- **[Energy Aware Scheduling — Documentation/scheduler/sched-energy.rst](https://www.kernel.org/doc/html/latest/scheduler/sched-energy.html)**
  - **讀哪裡**：整篇，講 EAS 的能量模型與「放哪顆核」的決策
  - **和本章 + 跨課的關聯**：本章 MTK/big.LITTLE 那節的權威來源；配 Ch 15 與 arm 課的 big.LITTLE 一起讀

電源管理讓 CPU 和裝置在該省電時省、該衝時衝。下一章我們轉進網路子系統，從最核心的兩個抽象開始——封包在 kernel 裡的載體 `sk_buff`，以及每張網卡的抽象 `net_device`。

→ [Ch 43 sk_buff 與 net_device 抽象](./43-sk-buff-netdev.md)
