# Ch 32 — 時間子系統：jiffies、hrtimer、tickless

> **目標**：搞懂 kernel 怎麼知道「現在幾點」與「該在什麼時候醒來」——從硬體 timer、clocksource/clock event 的分工，到 jiffies 這個粗粒度計數器、傳統週期 tick 的滴答、tickless（NO_HZ）為什麼要停掉滴答，再到兩套 timer：低精度 timer wheel（`timer_list`）與奈秒級的 hrtimer 紅黑樹。學完你能寫模組用兩套 timer 各排一個 callback、量出精度差距，並讀懂 `/proc/timer_list`。

## 為什麼需要這個？

前三章（Ch 29–31）講的是「外界打斷 kernel」——鍵盤、網卡、磁碟丟中斷進來，kernel 放下手邊的事去處理。但 kernel 還需要一種**自己主動安排的中斷**：「300 毫秒後如果這個 TCP 封包還沒被 ack，就重傳」「這個 process 的時間片用完了，該換人跑」「使用者呼叫了 `nanosleep(2)`，5 微秒後把它叫醒」。

這些都是「未來某個時間點要做某件事」。要做到，kernel 得先解決兩個更基本的問題：

1. **怎麼知道現在時間**（讀一個單調遞增、精度夠細的時間源）
2. **怎麼在指定時間被中斷**（叫硬體「到那個時刻打斷我」）

這兩件事聽起來像同一件，其實在硬體上是**兩種不同能力**，Linux 也刻意把它們拆成兩個抽象。搞不清這個分工，後面的 jiffies、tick、hrtimer 全都會混。所以我們從硬體開始。

## 先建立直覺：讀時間 vs 設鬧鐘是兩回事

想像你在廚房。你需要兩樣東西：一個**牆上的時鐘**（隨時抬頭就知道現在幾點），跟一個**廚房計時器**（設 10 分鐘，時間到會叮一聲打斷你）。

時鐘只會走、不會叫你；計時器會叫你、但你不會盯著它看現在幾點。這兩件事在硬體上對應到 Linux 的兩個抽象：

```
        讀時間（牆上時鐘）                    設鬧鐘（廚房計時器）
   ┌──────────────────────────┐        ┌──────────────────────────┐
   │      clocksource          │        │   clock event device       │
   │  kernel/time/clocksource.c│        │  （clockevents 框架）        │
   ├──────────────────────────┤        ├──────────────────────────┤
   │ 提供：單調遞增的 counter    │        │ 提供：「N ns 後給我一個中斷」 │
   │ 讀它 → 現在的時間            │        │ 設它 → 未來被打斷             │
   │ 只讀、不會產生中斷           │        │ 會產生中斷、不拿來讀時間       │
   ├──────────────────────────┤        ├──────────────────────────┤
   │ x86:  TSC / HPET / acpi_pm │        │ x86:  Local APIC timer /    │
   │ ARM64: 架構通用計時器       │        │        HPET (one-shot)      │
   │        (CNTVCT_EL0)        │        │ ARM64: 架構通用計時器        │
   │                            │        │        (CNTP_TVAL_EL0)       │
   └──────────────────────────┘        └──────────────────────────┘
              ▲                                     │
              │ ktime_get() 讀它                    │ 到期 → timer 中斷
              │                                     ▼
        時間戳、比較到期                       tick / hrtimer callback
```

有趣的是：在 ARM64 上，**同一顆硬體（架構通用計時器 / architected generic timer）同時扮演兩個角色**——它有一個一直遞增的 counter（`CNTVCT_EL0`，當 clocksource 讀），也有一個比較暫存器可以設定下次觸發（當 clock event）。x86 上通常是不同硬體：TSC 當 clocksource（讀最快），Local APIC timer 當 clock event（每顆 CPU 一個，設鬧鐘）。

**為什麼要拆成兩個抽象？** 因為「哪個硬體讀時間最好」和「哪個硬體設中斷最好」的答案常常不一樣。x86 的 TSC 讀一次只要幾個 cycle（`rdtsc` 指令），但它歷史上不能拿來產生中斷；HPET 能產生中斷但讀起來慢（要走匯流排）。Linux 讓每種硬體各自宣告「我能當 clocksource」「我能當 clock event」或兩者，然後在開機時各自挑最好的（clocksource 挑 rating 最高的，見 `__clocksource_select()`）。拆開之後，換硬體、加新平台都只要實作對應的介面，上層的 timer 邏輯完全不用動。

## 硬體 timer 與 clocksource：kernel 怎麼讀時間

clocksource 的核心是 `include/linux/clocksource.h` 的 `struct clocksource`，關鍵欄位：

- `read()`：讀出當前的原始 counter 值（cycle 數）
- `mult`、`shift`：把 cycle 換算成奈秒的定點乘法係數（`ns = (cycles * mult) >> shift`，用移位避免浮點）
- `rating`：品質評分（0–499），開機時挑分數最高的當系統 clocksource
- `mask`：counter 的有效位元（處理 wrap-around）

你可以在跑著的系統上看目前用哪個：

```bash
cat /sys/devices/system/clocksource/clocksource0/available_clocksource
# tsc hpet acpi_pm             （x86 常見）
cat /sys/devices/system/clocksource/clocksource0/current_clocksource
# tsc
```

`kernel/time/clocksource.c` 裡有個 **watchdog** 機制：它會拿一個「值得信任但慢」的 clocksource（如 HPET）去定期核對「快但可能不穩」的 TSC。如果發現 TSC 跳了（某些老 CPU 在深度睡眠或跨 socket 時 TSC 會不同步），watchdog 會把 TSC 標成 unstable、踢掉、換下一個。這就是你偶爾在 dmesg 看到 `clocksource: timekeeping watchdog on CPU...: Marking clocksource 'tsc' as unstable` 的來源。

讀時間的公開 API 在 `kernel/time/timekeeping.c`，最常用的是 `ktime_get()`（回傳單調時間，不受 NTP/使用者改時鐘影響，型別 `ktime_t` 是 64-bit 奈秒）、`ktime_get_real()`（wall clock，會跳）、`ktime_get_boottime()`（含 suspend 時間）。**寫 kernel code 要量「過了多久」一律用 `ktime_get()` 這類單調源，不要用會被使用者 `settimeofday` 改掉的 real time。** 這些讀取被 `kernel/time/timekeeping.c` 裡的一個 seqlock（`tk_core.seq`，接 Ch 28）保護——讀者拿 seqcount 快照、更新者短暫持鎖，讓「一邊有中斷在更新時間、一邊有人在讀」不會讀到半更新的值。

### clock event device 那一側：clockevents 框架

讀時間那側是 clocksource，設鬧鐘那側對應的框架是 **clockevents**（`kernel/time/clockevents.c`、`include/linux/clockchips.h` 的 `struct clock_event_device`）。關鍵欄位與 clocksource 對稱：

- `set_next_event()`：設定「N 個 cycle 後觸發中斷」（one-shot 的核心）
- `set_state_periodic()` / `set_state_oneshot()`：切換週期模式與單發模式——傳統 tick 用前者、tickless 用後者
- `event_handler`：中斷來時要呼叫誰（週期模式指向 `tick_handle_periodic()`，high-res 模式指向 `hrtimer_interrupt()`）
- `rating`、`cpumask`：品質評分，與「這個裝置服務哪幾顆 CPU」

每顆 CPU 在 `tick_check_new_device()` 時各自綁一個 clock event device（Local APIC timer 是 per-CPU 的，天生每 CPU 一個；HPET 只有幾組，早期當 broadcast 用）。**深睡的 C-state 會停掉 Local APIC timer**（Ch 42），這時 kernel 需要一個「不會被睡眠停掉」的 broadcast timer（HPET）來把睡著的 CPU 叫醒——這是 `tick_broadcast` 機制存在的原因，也是 `/proc/timer_list` 裡你會看到 `Broadcast device` 一欄的由來。

### ARM64 的架構通用計時器

x86 這套「TSC 讀 + APIC timer 設」在 ARM64 上收斂成一顆硬體：**架構通用計時器（architected generic timer，`drivers/clocksource/arm_arch_timer.c`）**。它同時提供兩個能力：

- **當 clocksource**：讀系統 counter `CNTVCT_EL0`（虛擬 counter）或 `CNTPCT_EL0`（實體 counter），一直遞增，頻率由 `CNTFRQ_EL0` 宣告
- **當 clock event**：寫比較暫存器 `CNTV_CVAL_EL0`（設定到期的絕對 counter 值）或 `CNTV_TVAL_EL0`（設定相對倒數），到期時透過 GIC（Ch 29）產生一個 PPI 中斷

因為 counter 是架構定義的、由硬體保證各核同步遞增，ARM64 上不太有 x86 TSC 那種「跨 socket 不同步」的頭痛，watchdog 的角色也輕很多。虛擬化時 hypervisor 還能給 guest 一個 counter 偏移（`CNTVOFF_EL2`），讓 guest 看到的虛擬時間平滑——這也是 KVM on ARM 時間虛擬化的基礎。

## jiffies：最粗但最便宜的時間計數器

在有 clocksource 之前，Unix 就有 **jiffies**：一個全域計數器，每個 timer tick 加一。它是 kernel 裡最古老、最粗、但讀起來最便宜的時間概念。

一秒有幾個 tick 由編譯期常數 **`HZ`** 決定（`include/asm-generic/param.h` / arch 的 `param.h`）。常見值：

| HZ | 一 tick = | 特性 | 誰用 |
|---|---|---|---|
| 100 | 10 ms | tick 開銷最低、省電 | server、省電優先 |
| 250 | 4 ms | 折衷；許多桌面發行版採用 | 通用桌面/伺服器 |
| 1000 | 1 ms | 反應最快、互動最順（x86_64 defconfig 預設就是 1000） | 低延遲、桌面 |

**HZ 的取捨很直接**：HZ 越高，週期 timer 中斷越密，時間片、timer 到期的解析度越細（反應快）；但每次中斷都有固定成本（進中斷、跑 tick 處理、可能打斷正在跑的 task），HZ=1000 時光是滴答就吃掉可觀的 CPU 與電力。這也是後面 tickless 出現的動機。

jiffies 本身宣告在 `kernel/time/timer.c`（`jiffies_64`，64-bit），32-bit 的 `jiffies` 是它的低 32 位別名。**32-bit 上直接讀 `jiffies_64` 不是原子的**（要讀兩個 32-bit 字），所以有 `get_jiffies_64()` 用 seqlock（`jiffies_lock`）保護；64-bit 平台一次讀完，沒這問題。

### 別直接比大小：time_after / time_before

jiffies 會 wrap。HZ=1000、32-bit 的 `jiffies` 大約 49.7 天就繞回 0。如果你寫 `if (jiffies > deadline)`，在 wrap 附近會判斷錯誤。正確做法是用 `include/linux/jiffies.h` 的巨集：

```c
unsigned long deadline = jiffies + msecs_to_jiffies(500);  // 500ms 後
// ... 一段時間後 ...
if (time_after(jiffies, deadline)) {
    // 超過 deadline 了
}
```

`time_after(a, b)` 的定義是 `((long)(b) - (long)(a) < 0)`——把無號差轉成有號，靠 two's complement 的環繞語意正確處理 wrap，只要兩個時間點相差不超過約 24.8 天（半個 wrap 週期）就永遠正確。**看到手寫 `jiffies` 直接比大小的 code，幾乎都是 bug。**

## 傳統 tick：週期性心跳

最經典的時間模型：讓 clock event device 設成**週期模式**，每 `1/HZ` 秒產生一次 timer 中斷。這個中斷（tick）是 kernel 的心跳，每次跳動做這些事（進入點在 `kernel/time/tick-common.c` 的 `tick_periodic()` → `update_process_times()`）：

```
     timer 中斷（每 1/HZ 秒一次）
              │
              ▼
      tick_periodic()
        ├── do_timer(1)           → jiffies_64 += 1；更新 wall time
        ├── update_process_times()
        │     ├── account_process_tick()  → 記帳：這 tick 花在 user/sys/idle？
        │     ├── run_local_timers()      → 觸發 TIMER_SOFTIRQ，跑到期的 timer_list
        │     ├── sched_tick()        → Ch 11 的 task_tick：扣時間片、可能觸發搶佔
        │     └── rcu_sched_clock_irq()   → 推進 RCU grace period（Ch 27）
        └── profile_tick()
```

一次 tick 幹了很多活：推進 jiffies、CPU 時間記帳、跑到期的低精度 timer、餵排程器（讓 CFS/EEVDF 有機會搶佔正在跑的 task）、推進 RCU。這解釋了為什麼「時間」和「排程」在 kernel 裡綁得這麼緊——排程器的搶佔決定就掛在時間心跳上。

**傳統 tick 的問題：閒置時還在滴答。** 一台機器就算完全沒事做，每顆 CPU 每秒還是被叫醒 HZ 次，每次醒來 CPU 就沒法待在深層睡眠省電狀態（C-state，Ch 42）。對筆電是耗電、對資料中心是電費、對虛擬化 host 是白白喚醒一堆 idle 的 guest vCPU。這個浪費催生了 tickless。

## Tickless / NO_HZ：把沒必要的滴答停掉

核心邏輯在 `kernel/time/tick-sched.c`。它把 clock event 從「週期模式」改成 **one-shot 模式**（設一次、響一次），這樣 kernel 就能自己決定「下一次 tick 排在什麼時候」，而不是被硬體每 `1/HZ` 秒無條件叫醒。這套機制叫 **dynticks（動態 tick）**。

有兩個層級：

**NO_HZ_IDLE（`CONFIG_NO_HZ_IDLE`，現代發行版預設開）**：只在 CPU **閒置**時停 tick。當一顆 CPU 進 idle，`tick_nohz_idle_enter()` 會問：「我下一個非停不可的 tick 是什麼時候？」——答案是最近的到期 timer（timer wheel 裡最早那個）。它把 one-shot clock event 設到那個時間點，然後 CPU 就能一路睡到那時，中間不再有無謂的心跳。CPU 一有事做（有中斷、有 task 被喚醒）就 `tick_nohz_idle_exit()` 恢復。

```
   傳統 tick（idle 時）：            NO_HZ_IDLE（idle 時）：
   │ │ │ │ │ │ │ │ │ │  ← 每 1ms 醒    │           │      ← 只在有 timer 到期時醒
   ▼ ▼ ▼ ▼ ▼ ▼ ▼ ▼ ▼ ▼               ▼           ▼
   醒醒醒醒醒醒醒醒醒醒 (白白耗電)       睡───────────睡──── (深層 C-state 省電)
```

**「下次該醒的時間」怎麼算？** `tick_nohz_next_event()` 會取兩個來源的最小值：(1) timer wheel 裡最近的 `timer_list` 到期時間（`get_next_timer_interrupt()`），(2) hrtimer 紅黑樹最左節點的到期時間。取小的那個當「這顆 CPU 至少得在這時醒來」，把 one-shot clock event 設到那裡。有時它還會設一個上限（`timekeeping_max_deferment()`，避免 jiffies/timekeeping 太久沒更新導致 counter wrap 誤差）。所以停 tick 不是「睡到天荒地老」，而是「睡到下一件非做不可的事之前一刻」。

**NO_HZ_FULL（`CONFIG_NO_HZ_FULL`）**：更激進。就算 CPU **正在跑一個 task**，只要那顆 CPU 的 runqueue 上**只有一個可跑的 task**（沒有搶佔競爭、不需要靠 tick 來分時間片），就把 tick 停掉。這是給**即時運算與 HPC** 用的：一段對延遲極度敏感的 user code 獨佔一顆 CPU 時，你不希望每毫秒被 timer 中斷打斷一次（cache 被弄髒、抖動 jitter）。搭配 Ch 15 的 `isolcpus` / `nohz_full=` 開機參數把某幾顆 CPU 隔離出來專門跑這種工作。

代價：NO_HZ_FULL 那顆 CPU 不跑 tick，就沒人幫它做記帳、推進 RCU 等雜務，這些活得由別的「housekeeping」CPU 遠端代勞，實作複雜、且不是零成本。所以 NO_HZ_FULL 是**特化工具不是通用預設**——一般機器用 NO_HZ_IDLE 就好。

看你系統的 NO_HZ 狀態：

```bash
cat /sys/devices/system/clocksource/clocksource0/current_clocksource
grep . /sys/kernel/debug/tracing/... # (視情況)
# 更直接：
journalctl -k | grep -i nohz         # 開機時會印 NO_HZ 模式
cat /proc/timer_list | grep -i nohz  # 每 CPU 的 tick 裝置狀態
```

## 低精度 timer：timer_list 與 timer wheel

現在講「排一個未來的工作」。第一套是 **`timer_list`**（`include/linux/timer.h`、`kernel/time/timer.c`），精度只到 jiffies（毫秒級），但便宜、量大。TCP 重傳逾時、socket 逾時、驅動的「N 秒沒回應就放棄」——這些**逾時（timeout）** 幾乎全用它。逾時的特性是：你其實**希望它不要到期**（封包在逾時前就 ack 了），到期是例外，所以精度不重要、能大量塞進去又能快速取消才重要。

用法：

```c
#include <linux/timer.h>

static struct timer_list my_timer;

static void my_callback(struct timer_list *t)
{
    pr_info("timer_list fired at jiffies=%lu\n", jiffies);
    // 在 softirq context（TIMER_SOFTIRQ）跑，不能睡（不能 kmalloc(GFP_KERNEL)、不能 mutex）
}

// init 時：
timer_setup(&my_timer, my_callback, 0);
mod_timer(&my_timer, jiffies + msecs_to_jiffies(200));  // 200ms 後到期
// exit 時務必：
del_timer_sync(&my_timer);  // 等 callback 跑完再回來，避免 use-after-free
```

**底層資料結構：timer wheel（分層時間輪）。** 為什麼不用一棵排序好的樹？因為 timer 的操作特徵是「插入/取消極頻繁（每個網路連線都在不停 mod_timer）、真正到期的比例極低」。時間輪把時間切成一格一格的 bucket，timer 依到期時間丟進對應 bucket，每個 tick 只需要看「當前這一格」有沒有 timer 到期——插入、取消都是 **O(1)**（算出 bucket 直接掛進去），不用維護全序。

分層是為了涵蓋大範圍到期時間：越近的用細粒度 bucket（每格 1 個 tick），越遠的用粗粒度 bucket（一格涵蓋很多 tick），像時鐘的秒針/分針/時針。遠處的 timer 精度差沒關係——反正逾時本來就不要求精確。6.x 的 timer wheel（`kernel/time/timer.c` 的 `struct timer_base`、`calc_wheel_index()`）刻意做成**到期時「向上取整」到 bucket 邊界**，換取更好的批次處理與 NO_HZ 相容性；需要精確就別用它，用下面的 hrtimer。

`calc_wheel_index()` 依「還有多久到期」（`expires - clk`）落在哪個範圍，決定進第幾層（level）、哪一格：近的進 level 0（每格 1 tick、精度最好），越遠的層級越高、每格涵蓋的 tick 數呈幾何級數放大，最遠的一層一格可以涵蓋好幾天。這個設計讓「插入一個 timer」永遠是算一次 index + 掛 list，不需要跟其他 timer 比較排序——這就是 O(1) 攤銷的來源，也是為什麼 timer wheel 扛得住網路堆疊每秒幾十萬次的 mod_timer。舊版（4.8 之前）的 timer wheel 每個 tick 要做 cascade（把高層 bucket 的 timer 往低層搬），是效能痛點；新版靠「到期向上取整」消掉了 cascade。

## hrtimer：奈秒精度的高精度 timer

第二套是 **hrtimer（high-resolution timer，`include/linux/hrtimer.h`、`kernel/time/hrtimer.c`）**。它不依附 jiffies，用 `ktime_t`（64-bit 奈秒）表示到期時間，理論精度到硬體 clock event 能給的極限。`nanosleep(2)` 的睡眠、排程的 deadline（Ch 13 的 SCHED_DEADLINE）、音訊/多媒體的精確喚醒、`itimers`、futex 的 timeout——這些「真的需要準時」的都走 hrtimer。

**資料結構：每 CPU 一棵紅黑樹（timerqueue，包一層在 rbtree 上，Ch 5）**，按到期時間排序。最左邊的節點就是「最快到期的那個」。跟 timer wheel 相反，hrtimer 選擇維護全序，好處是隨時能 O(1) 拿到「下一個到期時間」——這正是 NO_HZ 決定 CPU 能睡多久所需要的資訊。

```
      timer_list（低精度）              hrtimer（高精度）
   ┌───────────────────────┐      ┌───────────────────────┐
   │   timer wheel（時間輪）  │      │  per-CPU 紅黑樹（rbtree）│
   │                        │      │                        │
   │  [t] [t][t] [ ] [t] ...│      │          (5ms)          │
   │   ▲ 每格一堆 timer       │      │         /    \          │
   │   插入/取消 O(1)         │      │     (2ms)    (12ms)     │
   │   精度：jiffies (ms)     │      │      /                  │
   │   用途：逾時（不求準）    │      │  (1ms)←最左=下一個到期    │
   │                        │      │   精度：ns   用途：準時喚醒 │
   └───────────────────────┘      └───────────────────────┘
```

用法：

```c
#include <linux/hrtimer.h>

static struct hrtimer my_hrtimer;

static enum hrtimer_restart hr_callback(struct hrtimer *t)
{
    pr_info("hrtimer fired, now=%lld ns\n", ktime_get());
    // 在硬中斷 context 跑（見下），絕對不能睡
    return HRTIMER_NORESTART;   // 或 hrtimer_forward_now() + HRTIMER_RESTART 做週期性
}

// init：
hrtimer_init(&my_hrtimer, CLOCK_MONOTONIC, HRTIMER_MODE_REL);
my_hrtimer.function = hr_callback;
hrtimer_start(&my_hrtimer, ms_to_ktime(200), HRTIMER_MODE_REL);  // 200ms 後
// exit：
hrtimer_cancel(&my_hrtimer);  // 等 callback 跑完
```

**執行 context 是關鍵差異。** hrtimer 的 callback 在 clock event 的**硬中斷 context**（`hrtimer_interrupt()`）裡直接跑——為的就是精度：從中斷觸發到 callback 執行的路徑越短越準。代價是 callback 受硬中斷 context 的全部限制（接 Ch 2）：**不能睡、不能拿會睡的鎖、不能 `GFP_KERNEL` 配記憶體**，而且要跑很短——你在裡面拖太久，會延誤同一顆 CPU 上其他 hrtimer。真的需要在 timer 到期後做會睡的重活，callback 裡只能喚醒一個 workqueue（Ch 30），把重活丟到 process context 做。

`hrtimer_interrupt()` 觸發時會把紅黑樹上所有已到期的節點依序 expire、跑它們的 callback，再看下一個最左節點、把 clock event 設到那個時間、離開中斷——這就是 hrtimer 驅動 one-shot clock event 的循環，也是 NO_HZ 高精度模式（`hrtimer` 化的 tick）能運作的基礎：連 tick 本身在 high-res 模式下都變成一個 hrtimer。

**hrtimer 也有省電旋鈕：slack（range timer）。** `hrtimer_start_range_ns()` 讓你給一個「軟到期」與「硬到期」的區間，kernel 可以在區間內把它跟附近的別的 hrtimer **合併（coalescing）** 在同一次中斷一起 expire，減少喚醒次數。`nanosleep` 這種對絕對精度沒那麼苛刻的睡眠會吃 process 的 timer slack（`/proc/<pid>/timerslack_ns`，預設 50 微秒），拉大 slack = 省電但精度變差。SCHED_DEADLINE 那種真要準的則把 slack 設 0。這是「精度 vs 省電」在 hrtimer 這層的旋鈕。

**high-res 模式下連 tick 都是 hrtimer。** 前面說傳統 tick 靠 clock event 的週期模式；開了 `CONFIG_HIGH_RES_TIMERS`（現代發行版預設）後，clock event 切成 one-shot，**tick 本身變成一個掛在紅黑樹上的 hrtimer**（`kernel/time/tick-sched.c` 的 `tick_setup_sched_timer()`）。這樣「排程 tick」和「使用者的 hrtimer」共用同一個 one-shot 硬體與同一棵樹，NO_HZ 才能自然地把 tick 這個 hrtimer 往後推、跳過。理解這點，前面 tickless 那節「怎麼決定下次醒來」就閉環了：下次醒來時間 = 紅黑樹最左節點，而 tick 只是樹上眾多節點之一。

**順帶一提 delayed_work（Ch 30）**：`schedule_delayed_work()` 的底層就是一個 `timer_list`（低精度）——timer 到期後才把 work 排進 workqueue。所以 delayed work 精度也只到 jiffies，需要精確延遲的重活要自己組 hrtimer + workqueue。

## 底層機制：一次 timer 中斷的全景

把前面所有零件串成一張圖。假設 high-res 模式、NO_HZ_IDLE，一顆 CPU 從 idle 睡到某個 hrtimer 到期：

```
  (1) CPU 進 idle
        tick_nohz_idle_enter()
          └─ tick_nohz_next_event(): 掃 timer wheel 最近到期 + 紅黑樹最左節點
             取 min → 例如 300us 後有個 hrtimer
          └─ clockevents set_next_event(300us 後的 counter 值)  ← 設鬧鐘
        CPU 進深層 C-state（Local APIC timer 停；必要時 HPET broadcast 待命）
              │
              │  ...睡 300us，中間沒有任何多餘 tick...
              ▼
  (2) 硬體 clock event 到期 → timer 中斷
        hrtimer_interrupt()                       ← 硬中斷 context
          ├─ ktime_get(): 讀 clocksource 得知 now  ← 讀時間
          ├─ while (紅黑樹最左節點.expires <= now):
          │      移出節點 → 跑它的 function()       ← 你的 hrtimer callback（不能睡）
          │      (若含 tick 這個 hrtimer → 順帶做 do_timer/sched_tick/rcu)
          ├─ 重設 set_next_event(下一個最左節點)     ← 設下一個鬧鐘
          └─ 若有 timer_list 到期 → raise TIMER_SOFTIRQ
              │
              ▼
  (3) 離開硬中斷 → 跑 softirq
        run_timer_softirq() → expire_timers(): 跑到期的 timer_list callback
                                                ← softirq context（可被硬中斷，但仍不能睡）
```

三個 context 層層而下：**讀時間（clocksource）**貫穿全程、**設鬧鐘（clock event）**在進睡與離開中斷時各設一次、**hrtimer 在硬中斷跑**（要準）、**timer_list 降級到 softirq 跑**（不急）。這張圖把本章所有名詞的相對位置定死——之後看 `/proc/timer_list` 或 gdb backtrace，你都能對回這張圖的某一步。

## 動手：兩套 timer 排 callback，量精度差

寫一個模組，同時用 `timer_list` 和 hrtimer 各排一個「200 ms 後到期」的 callback，各自記下實際到期時的 `ktime_get()`，比對誤差。

```c
// timerdemo.c
#include <linux/init.h>
#include <linux/module.h>
#include <linux/timer.h>
#include <linux/hrtimer.h>
#include <linux/ktime.h>

#define DELAY_MS 200

static struct timer_list lp_timer;
static struct hrtimer    hp_timer;
static ktime_t lp_start, hp_start;

static void lp_cb(struct timer_list *t)
{
    s64 err_us = ktime_us_delta(ktime_get(), lp_start) - DELAY_MS * 1000;
    pr_info("timer_list: 誤差 %lld us\n", err_us);
}

static enum hrtimer_restart hp_cb(struct hrtimer *t)
{
    s64 err_us = ktime_us_delta(ktime_get(), hp_start) - DELAY_MS * 1000;
    pr_info("hrtimer:    誤差 %lld us\n", err_us);
    return HRTIMER_NORESTART;
}

static int __init td_init(void)
{
    lp_start = ktime_get();
    timer_setup(&lp_timer, lp_cb, 0);
    mod_timer(&lp_timer, jiffies + msecs_to_jiffies(DELAY_MS));

    hp_start = ktime_get();
    hrtimer_init(&hp_timer, CLOCK_MONOTONIC, HRTIMER_MODE_REL);
    hp_timer.function = hp_cb;
    hrtimer_start(&hp_timer, ms_to_ktime(DELAY_MS), HRTIMER_MODE_REL);

    pr_info("timerdemo: 兩個 timer 都排在 %d ms 後\n", DELAY_MS);
    return 0;
}

static void __exit td_exit(void)
{
    del_timer_sync(&lp_timer);
    hrtimer_cancel(&hp_timer);
    pr_info("timerdemo: 卸載\n");
}

module_init(td_init);
module_exit(td_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("compare timer_list vs hrtimer precision");
```

Makefile 照 Ch 0，`insmod timerdemo.ko` 後 `dmesg | tail`。典型結果：hrtimer 的誤差在幾十微秒內，`timer_list` 因為向上取整到 jiffies 邊界、又在 timer wheel 裡被批次處理，誤差常常到數毫秒。**這個數量級差距就是「為什麼要有兩套 timer」的實測答案。**

觀測硬體與 timer 狀態：

```bash
# 目前所有 timer 的到期時間、每 CPU 的 clock event / tick 裝置
cat /proc/timer_list

# 系統的 clocksource
cat /sys/devices/system/clocksource/clocksource0/current_clocksource
cat /sys/devices/system/clocksource/clocksource0/available_clocksource

# hrtimer 中斷次數（LOC = local timer interrupts；每 CPU 一欄）
cat /proc/interrupts | grep -E 'LOC|Local timer'
```

`/proc/timer_list`（由 `kernel/time/timer_list.c` 產生）是這章最值得盯著看的檔案：它列出每顆 CPU 的 clock event device（模式是 periodic 還是 oneshot）、`.resolution`、每個 clock base（MONOTONIC/REALTIME/...）上排隊的 hrtimer 與它們的到期時間。想確認自己模組的 hrtimer 真的進了樹，這裡看得到。

## 時間 namespace 點一下

Ch 49 會講 namespace。這裡先知道有 **time namespace**（`CLONE_NEWTIME`）：它讓一個容器/一組 process 看到的 `CLOCK_MONOTONIC` 和 `CLOCK_BOOTTIME` 有各自的偏移量（offset），主要用途是 **checkpoint/restore（CRIU）**——把一個 process 存下來、搬到另一台機器、幾小時後還原，它讀到的單調時間得能接續原本的值，不能突然倒退。實作是每個 time namespace 存一組 offset，讀時間時（甚至在 VDSO 裡）加上去。它**不虛擬 wall clock**（real time 仍全機一致），只動單調系時鐘。細節留給 Ch 49。

## 對比與取捨

| 面向 | `timer_list`（低精度） | hrtimer（高精度） |
|---|---|---|
| 時間表示 | jiffies（HZ 決定，ms 級） | `ktime_t`（ns） |
| 資料結構 | timer wheel（分層時間輪） | 每 CPU 紅黑樹（timerqueue） |
| 插入/取消 | O(1) 攤銷 | O(log n) |
| 拿「下一個到期」 | 較貴（要掃輪） | O(1)（最左節點） |
| callback context | softirq（TIMER_SOFTIRQ） | 硬中斷（`hrtimer_interrupt`） |
| 精度 | 到 jiffies，且向上取整 | 到硬體 clock event 極限 |
| 典型用途 | 逾時（TCP 重傳、驅動 timeout） | 準時喚醒（nanosleep、SCHED_DEADLINE、音訊） |
| 心態 | 「希望它別到期」 | 「一定要準時到期」 |

| 面向 | clocksource | clock event device |
|---|---|---|
| 能力 | 讀當前時間（單調 counter） | 設定「N ns 後中斷」 |
| 讀/寫 | 只讀 | 設定 + 產生中斷 |
| 選擇依據 | rating 最高者當系統時鐘 | 每 CPU 各自綁一個 |
| x86 代表 | TSC（快）/ HPET / acpi_pm | Local APIC timer / HPET |
| ARM64 代表 | 架構通用計時器 counter | 架構通用計時器比較暫存器 |

## 踩雷集錦

1. **手寫 `if (jiffies > deadline)` 判逾時**：錯直覺是「時間就是數字，直接比大小」。正確認識：jiffies 會 wrap（32-bit、HZ=1000 約 49.7 天繞回），wrap 附近直接比會判反。一律用 `time_after()` / `time_before()`。

2. **以為 `timer_list` 準**：錯直覺是「我設 10 ms 它就 10 ms 到」。正確認識：`timer_list` 精度只到 jiffies，且 6.x timer wheel 會**向上取整到 bucket 邊界**，實際可能晚好幾毫秒。要準用 hrtimer。

3. **在 hrtimer callback 裡睡 / 拿 mutex / `kmalloc(GFP_KERNEL)`**：錯直覺是「callback 也是我寫的 C code，想幹嘛幹嘛」。正確認識：hrtimer callback 跑在**硬中斷 context**，不能睡、不能拿會睡的鎖、要極短。要做會睡的重活，callback 裡喚醒 workqueue（Ch 30）丟出去做。

4. **卸載模組用 `del_timer()` / `hrtimer_cancel()` 卻沒等 callback 跑完**：錯直覺是「取消了就安全」。正確認識：`del_timer()` 只是把 timer 從佇列拔掉，但**此刻 callback 可能正在別的 CPU 上跑**，你把 `struct timer_list` 所在的記憶體 free 掉就 use-after-free。用 `del_timer_sync()`（會等 callback 跑完）；hrtimer 用 `hrtimer_cancel()`（本身就同步）。

5. **以為 NO_HZ = 完全沒有 timer 中斷**：錯直覺是「tickless 就是沒 tick」。正確認識：NO_HZ 只停掉「不必要」的週期 tick——閒置沒事時、或單一 task 獨佔時。一有 timer 到期、有 task 被喚醒、需要記帳，tick 立刻回來。NO_HZ_FULL 那顆 CPU 的 RCU/記帳雜務還得別的 housekeeping CPU 遠端代做，不是免費的。

## 進階：再往深一層

- **VDSO 讀時間不進 kernel**：`clock_gettime(CLOCK_MONOTONIC, ...)` 這種高頻呼叫，走 VDSO（`arch/x86/entry/vdso/`）在使用者空間直接讀 clocksource（TSC）算出時間，完全不觸發 syscall。前提是 clocksource 支援 VDSO（TSC 支援、HPET 不支援）——這也是「current_clocksource 是不是 tsc」會實質影響時間讀取效能的原因之一。面試常問「`gettimeofday` 為什麼快」，答案就是 VDSO + TSC。

- **timer 的 CPU 親和性與 pinned/deferrable**：`timer_list` 有 `TIMER_DEFERRABLE`（可延後、不為它喚醒 idle CPU，省電）與 `TIMER_PINNED`（釘在本 CPU）旗標。NO_HZ 之所以能長睡，很大程度靠一堆非緊急 timer 標成 deferrable，不逼 idle CPU 醒來。

- **時間片其實不靠 jiffies 精確計**：現代 CFS/EEVDF（Ch 12–13）用的是 `ktime_get()`/`sched_clock()` 的 ns 級 vruntime，不是數 tick。tick 的角色退化成「定期給排程器一個檢查搶佔的機會」，真正的時間量測走高精度源。這是理解「HZ 降低為何不太影響排程公平性」的關鍵。

- **面試常問**：clocksource 和 clock event 差在哪（讀 vs 設中斷）？為什麼 TCP 重傳用 timer_list 而 nanosleep 用 hrtimer（逾時不求準 vs 睡眠要準）？NO_HZ_IDLE 和 NO_HZ_FULL 分別解決什麼（省電 vs 減少 jitter）？hrtimer callback 為什麼不能睡（硬中斷 context）？

## 動手練習

1. **量兩套 timer 的精度**：跑上面的 `timerdemo`，把 `DELAY_MS` 換成 1、5、50、200，各記錄 `timer_list` 與 hrtimer 的誤差。畫出「設定延遲 vs 實際誤差」，觀察 `timer_list` 的誤差怎麼跟 `1000/HZ`（一個 tick 的毫秒數）相關。

2. **改 HZ 重編**：`make menuconfig` 把 HZ 從 250 改成 100 再改 1000（`Processor type and features → Timer frequency`），各重編、各跑練習 1，確認 `timer_list` 的誤差量級跟 HZ 對得上。

3. **gdb 停在 hrtimer 到期**：QEMU + gdb（Ch 0），`insmod timerdemo` 前先 `b hrtimer_interrupt`，`continue`，看它被觸發時的 backtrace——確認它真的在中斷 context。再 `b` 你自己的 `hp_cb`，看它從 `hrtimer_interrupt` 一路被呼叫進來。

4. **讀 `/proc/timer_list`**：`insmod` 前後各 `cat /proc/timer_list`，找出你排進去的那個 hrtimer（看到期時間），確認它掛在某顆 CPU 的 `clock-monotonic` base 上。

5. **弄壞它看後果**：把 `del_timer_sync` 改回 `del_timer`（不同步），在 callback 裡加 `mdelay(100)` 拖時間，快速反覆 insmod/rmmod——理解為什麼卸載要等 callback。（提示：這是製造 use-after-free 的教科書寫法，開 KASAN（Ch 53）能抓到。）

## 本章重點整理

- **讀時間（clocksource）和設鬧鐘（clock event device）是兩種硬體能力、兩個抽象**。x86 常用 TSC 讀、Local APIC timer 設中斷；ARM64 同一顆架構通用計時器兼任。
- **jiffies** 是每 tick 加一的粗計數器，HZ 決定粒度；比較一律用 `time_after`/`time_before` 處理 wrap，別直接比大小。
- **傳統 tick** 每 `1/HZ` 秒驅動 jiffies++、記帳、跑到期 timer、餵排程器、推 RCU；閒置時滴答浪費電，催生 **tickless**：NO_HZ_IDLE（閒置停 tick 省電）、NO_HZ_FULL（單 task 獨佔時停 tick 減 jitter）。
- **兩套 timer**：`timer_list` 用 timer wheel、精度到 jiffies、O(1) 插取、跑在 softirq，用於逾時；**hrtimer** 用每 CPU 紅黑樹、ns 精度、跑在硬中斷 context（不能睡），用於準時喚醒。

## 自我檢核

- [ ] 不看筆記，能解釋 clocksource 和 clock event device 各做什麼、為什麼要拆開
- [ ] 能說出為什麼不能寫 `if (jiffies > deadline)`，正確寫法是什麼、背後的 two's complement 原理
- [ ] 面試被問「TCP 重傳逾時和 `nanosleep` 分別該用哪套 timer、為什麼」，你能答出「逾時不求準用 timer_list，睡眠要準用 hrtimer」並講出精度差來源
- [ ] 能解釋 NO_HZ_IDLE 和 NO_HZ_FULL 分別解決什麼問題、後者為什麼不是通用預設
- [ ] 知道 hrtimer callback 跑在什麼 context、有哪些限制、要做會睡的重活該怎麼辦
- [ ] 能寫模組排一個 timer_list 與一個 hrtimer，正確用 `del_timer_sync`/`hrtimer_cancel` 卸載，並在 `/proc/timer_list` 看到它

## 延伸閱讀

### 官方文件

- **[Documentation/timers/](https://www.kernel.org/doc/html/latest/timers/index.html)** — kernel 官方 timer 文件
  - **讀哪裡**：`hrtimers.rst`（設計動機與 API）、`timers-howto.rst`（該用哪種延遲/timer 的決策指南）、`no_hz.rst`（NO_HZ 三種模式的權威說明與 housekeeping CPU 設定）
  - **和本章關聯**：本章的分工與取捨都在這裡有設計者第一手說明；`no_hz.rst` 尤其把 NO_HZ_FULL 的代價講得很清楚

- **[Documentation/core-api/timekeeping.rst](https://www.kernel.org/doc/html/latest/core-api/timekeeping.html)**
  - **讀哪裡**：整篇，列出所有 `ktime_get*()` 家族與各自語意（monotonic / real / boottime / tai）
  - **能學到什麼**：寫 kernel code 要量時間時，這張表告訴你該挑哪個 API、哪個會跳哪個不會

### 文章

- **[The high-resolution timer API (LWN)](https://lwn.net/Articles/167897/)** — Jonathan Corbet
  - **讀哪裡**：整篇。hrtimer 剛進主線時的解說，把「為什麼要跟舊 timer wheel 分開」講得最清楚
  - **前提**：讀完本章的 timer_list 部分再讀，對比最有感

- **[Clockevents and dyntick (LWN)](https://lwn.net/Articles/223185/)** — Thomas Gleixner / Jonathan Corbet
  - **這是什麼**：clockevents 框架與 dynticks（tickless）進主線的一手記錄，本章「clock event 抽象怎麼來的、one-shot 怎麼支撐 NO_HZ」的源頭
  - **為什麼值得讀**：這兩篇合起來就是本章前半的歷史脈絡

- **[（新版）timer wheel 重寫 (LWN)](https://lwn.net/Articles/646950/)** — 講 4.x timer wheel 大改的設計
  - **讀哪裡**：關注「為什麼放棄精確、換取批次處理與 NO_HZ 友善」那段——解釋本章踩雷 2（timer_list 向上取整）的來由

### 書籍

- **《Linux Kernel Development, 3rd Ed.》** — Robert Love，第 11 章「Timers and Time Management」
  - **定位**：jiffies、HZ、timer、時間管理的最佳白話入門，把本章的概念用更慢的節奏講一遍
  - **注意**：講的是舊 kernel（hrtimer 之前為主、timer wheel 是舊版），API 與資料結構細節以 6.12 源碼為準，但 jiffies/HZ/time_after 這些骨架至今不變

時間子系統搞定，Part 5（中斷與時間）到此收尾——你現在懂了 kernel 怎麼被外界打斷（Ch 29–31）、怎麼安排自己未來的工作（本章）。下一章轉入 Part 6，我們潛進檔案系統的核心：VFS 用哪四個物件（superblock/inode/dentry/file）把「所有東西都是檔案」這句話變成可運作的抽象。

→ [Ch 33 VFS 四大物件：superblock/inode/dentry/file](./33-vfs-objects.md)
