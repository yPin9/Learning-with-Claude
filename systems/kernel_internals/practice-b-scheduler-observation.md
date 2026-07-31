# 練習 B — CFS/EEVDF 觀測模組

> **這是 Part 2（Ch 9–15）的整合練習。** 這七章你把「一個 process 在 kernel 裡是什麼」到「排程器怎麼挑下一個、怎麼在多核間搬動」整條線走完了：`task_struct`（Ch 9）、fork/clone 怎麼生出 task（Ch 10）、scheduler class 與 runqueue 的框架（Ch 11）、CFS 的 vruntime/紅黑樹（Ch 12）、EEVDF 的 lag/eligibility/deadline（Ch 13）、context switch 怎麼換人（Ch 14）、SMP 怎麼負載均衡（Ch 15）。這個練習把它們拼成一件事：**寫一個核心模組，把排程器每一次「換人上場」的決定攔下來記帳，再用你記的帳去驗證 Ch 12/13 教的理論到底成不成立。**

## 背景與動機：為什麼是「觀測」而不是「改排程器」

學排程器最想做的事，是動手改它——改個 `pick_next` 的規則、調個 vruntime 的漲速，看系統行為怎麼變。但這條路對初學者是災難：排程器是 kernel 最熱、最緊耦合的路徑之一，`pick_next_task` 在每次 tick、每次 wakeup、每次 syscall 返回前都可能被呼叫，一個 CPU 上的錯誤決定會立刻透過 load balancer 傳染到別的 CPU，改錯輕則系統卡死、重則整個 runqueue 損壞 panic，而且**壞在哪很難用 gdb 慢慢停**——你一停下來，被你觀測的那個排程決定的時序就變了（觀測者效應在 kernel 排程這種微秒級路徑上特別明顯）。

所以這個練習走另一條路：**不碰排程器的決定，只在它做完決定的那一刻，把「它選了誰、為什麼」抄一份下來。** 這就是 tracepoint 與 kprobe 的用途——kernel 在關鍵路徑上預埋了掛鉤點（`sched_switch` tracepoint 正是其中最重要的一個），你的模組掛上去，排程器每換一次人，就順手呼叫一次你的 handler，你在 handler 裡讀 `task_struct`、讀 `se.vruntime`、讀當前 CPU，累加進統計。排程器本身的邏輯一行都沒動，你卻拿到了它每一個決定的完整記錄。

**觀測學到的東西一點不比改的少**：你會親手驗證「nice 差 5 大約是 3 倍 CPU」（Ch 12 的 1.25 倍規則）、親眼看到 EEVDF 怎麼讓互動 task 的 latency 壓下來（Ch 13）、看到 load balancer 怎麼把 task 在 CPU 之間搬（Ch 15）。而且你在 `bpf` 課裡是**從 user 空間**用 bcc/bpftrace 掛 `sched_switch`——這次你**從 kernel 內部**、用原生的 `register_trace_sched_switch` 掛同一個點，看清楚 bpf 幫你包掉的那層到底是什麼。

**全程在 Ch 0 的 QEMU + gdb 環境驗證，且這次一定要 `-smp 4`**——排程器的很多行為（load balancing、per-CPU runqueue、task 在 CPU 間遷移）只有多核才看得到。單核跑這個練習會少掉一半的觀察。

## 先建立心智模型

動手前先把「你的模組掛在哪、資料怎麼流」在腦中畫清楚：

```
   排程器（kernel/sched/core.c 的 __schedule）每次換人上場：
   ─────────────────────────────────────────────────────────
       pick_next_task() 選出 next
              │
              ▼
       trace_sched_switch(preempt, prev, next, prev_state)  ← kernel 預埋的 tracepoint
              │  （排程器照常繼續做 context_switch，Ch 14）
              │
              ├──────────────► 你的 probe 被呼叫（掛在這個 tracepoint 上）
              │                 sched_obs 模組內：
              ▼                 ┌─────────────────────────────────────────┐
       context_switch()        │ probe_sched_switch(data, preempt,        │
       換暫存器/位址空間/stack   │                    prev, next, prev_state)│
                               │   cpu = smp_processor_id()               │
                               │   next->se.vruntime / se.deadline 讀出   │
                               │   this_cpu → per-CPU 統計 +1、累加 runtime │
                               │   查/建這個 pid 的統計節點（hash 或 list）│
                               └─────────────────────────────────────────┘
                                          │
   user space                             ▼
   ──────────                    /proc/sched_obs（或 debugfs）
   cat /proc/sched_obs ◄──── seq_file 走一遍統計，印出：
        每個 pid 被排到幾次、累積跑多久、最後在哪顆 CPU、最後的 vruntime/deadline
        每顆 CPU 總共發生幾次 switch
```

四個關鍵認知，對上 Part 2 的章節：

- **`sched_switch` 給你的 `prev`/`next` 就是 `task_struct *`**（Ch 9）。你在 probe 裡拿到的 `next` 正是排程器**剛選出來要上場**的那個 task，`next->se`（Ch 12 的 `sched_entity`）裡就有 `vruntime`、`deadline`、`vlag`、`load.weight`。你不用自己去 runqueue 撈，tracepoint 直接把 task 遞到你手上。
- **統計要 per-CPU**（Ch 7）。`sched_switch` 在**每顆 CPU 上各自獨立、可能同時**觸發——CPU 0 在換人的同時 CPU 3 也在換人。如果你用一個全域計數器 `count++`，四顆 CPU 同時 `++` 就是 race，還會製造 cache line bouncing（Ch 7 的核心痛點）。正解是每顆 CPU 一份計數（`DEFINE_PER_CPU`），各加各的，`cat` 時再加總。
- **probe 跑在很嚴苛的 context**——排程路徑上，可能持有 runqueue 的 rq lock、關著 preemption、甚至關著中斷。所以你的 probe 裡**絕對不能睡**：不能 `kmalloc(GFP_KERNEL)`、不能 `mutex_lock`、不能 `copy_from_user`。這正是 Ch 7 說的「這裡能不能睡」判斷的實戰——而且是比練習 A 更嚴苛的版本（練習 A 的 `.proc_write` 跑在能睡的 process context，這裡的 probe 不能睡）。
- **輸出介面（`/proc` 或 debugfs）跑在 process context**（是你 `cat` 時 read syscall 進來的），那裡能睡、能 `mutex_lock`——所以「更新統計」（probe，不能睡）和「讀出統計」（seq_file，能睡）是兩個不同 context，保護方式也不同。這個「寫路徑在原子 context、讀路徑在 process context」的分裂，是真實 kernel 統計設施（如 `/proc/stat`、`/proc/schedstat`）天天在處理的事。

## 任務規格

### 主線任務：sched_switch 觀測模組

寫一個核心模組 `sched_obs.ko`，行為如下。

**掛載點**：用 tracepoint 掛在 `sched_switch` 上（推薦），或用 kprobe 掛在 `pick_next_task_fair` 上（替代，難度與陷阱不同，見卡關提示 4）。推薦 tracepoint 的理由：`sched_switch` 是**穩定的 ABI**（tracepoint 是 kernel 對外承諾的介面），`prev`/`next`/`prev_state` 直接遞給你；kprobe 掛內部函式則脆弱（函式可能被 inline、改名，v6.12 上 `pick_next_task_fair` 是否能被 kprobe 掛到取決於它有沒有被 inline，見卡關提示 4）。

**每次 switch 要記的**：在 probe 裡，對「被選中要上場的 `next` task」記錄：

- 它的 `pid`（`next->pid`）與 `comm`（`next->comm`，執行檔名，Ch 9）
- 它的 `se.vruntime`、`se.deadline`、`se.vlag`（Ch 12/13 的 EEVDF 三件套；`deadline`/`vlag` 是 6.6 起才有的欄位）
- 它在**哪顆 CPU** 上被選中（`smp_processor_id()`）
- 累加：這個 pid「被排到幾次」、以及一個「累積執行時間」的估計（用 `next->se.sum_exec_runtime` 的差值，見卡關提示 5）

**per-CPU 統計**（Ch 7）：每顆 CPU 維護一份「這顆 CPU 上總共發生幾次 switch」的計數，用 `DEFINE_PER_CPU`，probe 裡用 `this_cpu_inc` 更新。

**per-pid 統計**：維護一張「pid → 統計」的表（被排到次數、最後的 vruntime/deadline、最後所在 CPU、累積 runtime）。因為 probe 不能睡（不能 `kmalloc(GFP_KERNEL)`），這張表的實作有講究——見卡關提示 3 給你三條路（固定大小陣列 / `GFP_ATOMIC` / 預配置池）。

**輸出介面**：用 `/proc/sched_obs`（或 debugfs 的 `sched_obs/stats`）以 seq_file 輸出：

- 每個 pid 一行：`pid comm 被排次數 累積runtime 最後CPU 最後vruntime 最後deadline`
- 一段 per-CPU 摘要：每顆 CPU 的 switch 總次數
- 一行總計

**生命週期**：`insmod` 時 `register_trace_sched_switch(probe, NULL)` 掛上、建立 `/proc` 檔；`rmmod` 時**先 `unregister_trace_sched_switch` 再 `tracepoint_synchronize_unregister()`**（等所有正在跑的 probe 跑完，避免卸載到一半 probe 還在跑 → UAF，見卡關提示 2），再移除 `/proc`、釋放統計表。

### 進階任務：用你的工具做兩個實驗

模組寫完只是工具。真正學到東西是用它去**驗證 Ch 12/13 的理論**。

**實驗一：驗證 nice 值的 1.25 倍規則（Ch 12）。** 起兩個 CPU-bound 的 busy loop，`taskset` 綁**同一顆 CPU**（不綁的話 load balancer 會把它們分到兩顆 CPU 各拿 100%，你就看不到競爭——Ch 12/15 都強調過的翻車點），一個 `nice 0`、一個 `nice 5`。跑一段固定時間後 `cat /proc/sched_obs`，用你記的「累積 runtime」算兩者 CPU 時間比，驗證是否約 `1.25^5 ≈ 3:1`。再換 nice 差 1（應約 1.25:1）、差 10（應約 9:1）各驗一次。

**實驗二：觀察 EEVDF 的 latency 行為（Ch 13）。** 起一個「互動型」task（週期性睡一下醒一下，例如每 10ms 醒來做極少工作再睡）和一個「batch 型」task（純 CPU-bound busy loop），綁同一顆 CPU。用你的模組看兩件事：(a) 互動 task 每次醒來後，是不是**很快**就被排到（它 `slice` 小、EEVDF 給它近 deadline）；(b) 對照兩者的 `se.deadline`——互動 task 的 deadline 應該持續比 batch task 早。這驗證 Ch 13 的核心結論：**EEVDF 不犧牲長期公平（batch 總 runtime 不會少拿），卻把互動 task 的 per-wakeup latency 壓下來**。

### 驗收標準

| # | 檢查項 | 怎麼驗 |
|---|---|---|
| 1 | 模組 `insmod` 成功、`dmesg` 有載入訊息、`/proc/sched_obs` 出現 | `insmod sched_obs.ko; dmesg \| tail; ls /proc/sched_obs` |
| 2 | `cat /proc/sched_obs` 看得到多個 pid 的統計，`comm` 正確（能認出 `sh`、你的 busy loop 等） | 手動 `cat` |
| 3 | per-CPU switch 計數：`-smp 4` 下四顆 CPU 都有非零計數（證明真的多核在跑） | `cat` 看 CPU 0–3 摘要 |
| 4 | 記到的 `se.vruntime` 隨時間單調增、`se.deadline` 是 6.6+ 的 EEVDF 欄位（非零） | 連續 `cat` 兩次對照 |
| 5 | `rmmod` 乾淨、無 crash、無 KASAN 報告、`/proc/sched_obs` 消失 | `rmmod sched_obs; ls /proc/sched_obs`（應 No such file） |
| 6 |（進階一）nice 0 vs nice 5 綁同 CPU，runtime 比約 3:1 | 跑實驗一，算比值 |
| 7 |（進階二）互動 task 的 `se.deadline` 持續早於 batch task | 跑實驗二，對照 deadline 欄 |

## 期望輸出範例

```
/ # insmod /sched_obs.ko
[   14.2] sched_obs: hooked sched_switch, /proc/sched_obs ready
/ # taskset -c 0 nice -n 0 sh -c 'while :; do :; done' &
/ # taskset -c 0 nice -n 5 sh -c 'while :; do :; done' &
   （跑約 10 秒）
/ # cat /proc/sched_obs
== per-pid schedule stats ==
  pid  comm             picks   runtime_ns    lastcpu   vruntime         deadline
   72  sh                8801   7523110000          0    4611...123        4611...900
   73  sh                2955   2498330000          0    4610...880        4611...455
   41  sh                  12      3410000          1    ...
    1  init                 4       210000          2    ...
== per-cpu switch counts ==
  cpu0: 11821   cpu1: 340   cpu2: 155   cpu3: 98
  total switches: 12414
/ #
```

pid 72（nice 0）拿到 7.52s、pid 73（nice 5）拿到 2.50s，比值 `7523/2498 ≈ 3.01`——正好是 `1.25^5 ≈ 3.05`，Ch 12 的 weight 表在你眼前被驗證了。兩者 `lastcpu` 都是 0（`taskset -c 0` 綁對了），所以它們真的在同一顆 CPU 上競爭。

卸載：

```
/ # rmmod sched_obs
[   96.7] sched_obs: unhooked, 12414 switches observed
/ # cat /proc/sched_obs
cat: can't open '/proc/sched_obs': No such file or directory
```

## 卡關提示

1. **tracepoint 的註冊 API 不是你想的那樣**。掛一個 tracepoint 不是用 `register_kprobe`，而是每個 tracepoint 有它專屬的 `register_trace_<名字>()` 函式，由 `TRACE_EVENT` 巨集自動產生。掛 `sched_switch` 用 **`register_trace_sched_switch(probe_fn, data)`**（宣告在 `include/trace/events/sched.h`，要 `#include` 它）。你的 probe 函式簽章**必須完全對上** tracepoint 的 `TP_PROTO`，前面多一個 `void *data`：v6.12 上是

   ```c
   void probe_sched_switch(void *data, bool preempt,
                           struct task_struct *prev,
                           struct task_struct *next,
                           unsigned int prev_state);
   ```

   （`prev_state` 這個參數是 5.14 起加的；更舊的 kernel 沒有它，抄舊教材會簽章不符——這是版本斷層。）簽章對不上會編過但行為錯亂或直接 warn。卸載對應 `unregister_trace_sched_switch(probe_fn, data)`，`data` 要和註冊時傳的一致。

2. **卸載時的 UAF 陷阱：一定要 `tracepoint_synchronize_unregister()`**。`unregister_trace_sched_switch` 回傳時，**可能還有別的 CPU 正在你的 probe 函式裡跑**（它剛好在你 unregister 的同一瞬間觸發了 switch）。如果你 unregister 完就馬上 `rmmod`（模組程式碼被卸載），那個還在跑的 probe 會執行到一半程式碼消失 → panic。正確順序是：`unregister_trace_sched_switch(...)` → **`tracepoint_synchronize_unregister()`**（它內部是 `synchronize_rcu()`，等所有 in-flight 的 probe 跑完）→ 才移除 `/proc`、釋放記憶體、讓模組真正卸載。漏了這步，你的模組在高頻 switch 下 `rmmod` 遲早 crash，而且是偶發的、難復現的——正是 Ch 7 說的那種 race。

3. **probe 裡不能睡 → per-pid 表怎麼配記憶體**。probe 跑在原子 context（可能持 rq lock、關 preemption），**不能 `kmalloc(GFP_KERNEL)`、不能 `mutex_lock`**。三條路，難度遞增：
   - **(a) 固定大小陣列 + pid 取模當 hash**（最簡，推薦入門）：`static struct pid_stat table[1024];`，用 `pid % 1024` 當 index，衝突就覆蓋或線性探測。零動態配置、零睡眠、實作最省事，缺點是 pid 衝突會混統計——但對這個練習夠用，且能讓你先把主線跑通。
   - **(b) `GFP_ATOMIC` 動態配置**：probe 裡真要新配節點，用 `kmalloc(sizeof(*p), GFP_ATOMIC)`（不睡，Ch 6）。`GFP_ATOMIC` 從緊急保留池拿、可能失敗，要處理 `NULL`。搭配一個**spinlock**（不是 mutex！spinlock 不睡，Ch 25）保護 hash 表。
   - **(c) 預配置物件池**：`insmod` 時（能睡的 init context）先 `kmalloc` 一大塊當池，probe 裡從池裡拿（無鎖或 spinlock）。最接近真實 kernel 的做法（很多子系統這樣避免熱路徑配置）。

   保護統計表若跨 CPU 共享，用 **spinlock**（`spin_lock_irqsave`，因為 probe 可能在關中斷的 context，見 Ch 25）。per-CPU 計數（`this_cpu_inc`）則本來就不用鎖。

4. **選 kprobe 掛 `pick_next_task_fair` 的話**：用 `register_kprobe`，`kp.symbol_name = "pick_next_task_fair"`，在 `.pre_handler` 裡讀 `regs`。**兩個陷阱**：(i) v6.12 上 `pick_next_task_fair` 有可能被 inline 進 `__pick_next_task` / `__schedule`，那樣 kprobe 找不到符號會註冊失敗（`register_kprobe` 回 `-EINVAL`/`-ENOENT`）——先 `grep pick_next_task_fair /proc/kallsyms` 確認它是個獨立符號才掛得上。(ii) kprobe 是掛在**函式入口**，那時 `next` 還沒被選出來（函式還沒跑完），你拿不到「選了誰」——要拿結果得用 **kretprobe**（`.handler` 在函式返回時跑，`regs_return_value(regs)` 是回傳的 `task_struct *`）。這就是為什麼推薦 tracepoint：`sched_switch` 在 next 已選定後才觸發，直接把 `next` 遞給你，省掉這些麻煩。

5. **「累積執行時間」怎麼算才對**。別自己用 tick 數估——`task_struct` 裡已經有 `next->se.sum_exec_runtime`（Ch 12），是這個 task 累積的**真實**執行時間（奈秒）。但你要的是「觀測期間這個 task 跑了多久」，所以記錄**差值**：每次這個 pid 被 switch 進來（`next`）時記下當時的 `sum_exec_runtime`，下次它被 switch 出去（作為 `prev`）或再次被選中時算差。最省事的近似：直接記錄每個 pid**最新看到的** `sum_exec_runtime`，`cat` 時它就是「這個 task 出生到現在的總 CPU 時間」——實驗一比兩個同時起跑的 task 的比值，用總量比就夠準（它們幾乎同時開始，總量比 ≈ 觀測期間比）。想更精確就記首次看到與最新看到的差。

## 分步實作建議

1. **先讓「空 probe + 空 `/proc`」通**。`module_init` 裡 `register_trace_sched_switch(probe, NULL)`，probe 裡先只 `this_cpu_inc(switch_count)`（per-CPU 計數，Ch 7），`/proc/sched_obs` 的 seq_file 先只印每顆 CPU 的計數總和。`module_exit` 裡照卡關提示 2 的順序卸載。`insmod`、`cat`、`rmmod` 跑一遍，確認 tracepoint 掛得上、per-CPU 計數在動、卸載不 crash。這步不碰 per-pid 表，先把最容易出錯的「掛載/卸載時序」弄對。

2. **加 per-pid 統計表**。用卡關提示 3 的路 (a)（固定陣列 + `pid % N`）起步。probe 裡：算 `idx = next->pid % N`，填 `table[idx].pid = next->pid`、`memcpy(comm, next->comm, ...)`、`picks++`、記 `next->se.sum_exec_runtime`。seq_file 走一遍陣列印非空項。先不管 pid 衝突，把資料流打通。

3. **記 EEVDF 欄位**。在 probe 裡加讀 `next->se.vruntime`、`next->se.deadline`、`next->se.vlag`（Ch 12/13）、`smp_processor_id()` 存進統計。`cat` 對照：vruntime 應隨時間增、deadline 應非零（證明你跑的是 EEVDF 而非老 CFS）。

4. **做實驗一（nice 1.25 倍）**。QEMU 用 `-smp 4` 開機。起兩個 `taskset -c 0` 綁同顆 CPU、不同 nice 的 busy loop，跑約 10 秒，`cat` 算 runtime 比。對照 `1.25^Δnice`。若比值明顯不對，先檢查是不是 `taskset` 沒綁成功（看兩者 `lastcpu` 是否都是 0）。

5. **做實驗二（EEVDF latency）**。寫一個互動型 task（`while: usleep(10000); 做極少事`）和一個 batch busy loop，綁同顆 CPU。`cat` 對照兩者 `se.deadline`（互動的應較早）和 `picks`（互動的被排次數多但每次跑很短，batch 被排次數少但每次跑久）。這就看到 EEVDF 的兩根軸：公平（總量）與 latency（deadline）。

## 完整參考解答

<details>
<summary>點開看完整可編譯解答（sched_obs.c + Makefile + 實驗腳本）</summary>

### `sched_obs.c`（主線任務，tracepoint 版）

```c
// sched_obs.c — CFS/EEVDF 排程觀測模組（練習 B 主線）
// 掛 sched_switch tracepoint，記錄每次被選中的 task，經 /proc/sched_obs 輸出統計。
#include <linux/init.h>
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/proc_fs.h>
#include <linux/seq_file.h>
#include <linux/percpu.h>          // DEFINE_PER_CPU / this_cpu_inc
#include <linux/spinlock.h>        // spinlock（probe 不能睡，只能用 spinlock 不能 mutex）
#include <linux/sched.h>           // struct task_struct / sched_entity
#include <linux/smp.h>             // smp_processor_id
#include <linux/tracepoint.h>      // tracepoint_synchronize_unregister

// 掛 sched_switch 要 include 它的 TRACE_EVENT 定義，才有 register_trace_sched_switch
#include <trace/events/sched.h>

#define NB_SLOTS 1024              // per-pid 表大小（固定陣列，pid % NB_SLOTS 當 index）

// ---- per-pid 統計（用固定陣列，避開 probe 內動態配置/睡眠）----
struct pid_stat {
    int   pid;                     // 0 = 空槽
    char  comm[TASK_COMM_LEN];     // TASK_COMM_LEN = 16（Ch 9）
    u64   picks;                   // 被 switch 進來幾次
    u64   last_runtime;            // 最新看到的 se.sum_exec_runtime（奈秒，真實時間）
    u64   first_runtime;           // 首次看到的 sum_exec_runtime（算觀測期間差用）
    u64   last_vruntime;           // 最新 se.vruntime（Ch 12）
    u64   last_deadline;           // 最新 se.deadline（EEVDF，Ch 13）
    s64   last_vlag;               // 最新 se.vlag（EEVDF 欠賬，Ch 13）
    int   last_cpu;                // 最後在哪顆 CPU 被選中
};

static struct pid_stat pstat[NB_SLOTS];
static DEFINE_SPINLOCK(pstat_lock);     // 保護 pstat（probe 跨 CPU 併發，只能 spinlock）

// ---- per-CPU：每顆 CPU 的 switch 總次數（Ch 7，各加各的免 race + 免 cache bouncing）----
static DEFINE_PER_CPU(u64, switch_count);

// ---- probe：每次排程換人時被呼叫（跑在原子 context，絕對不能睡）----
static void probe_sched_switch(void *data, bool preempt,
                               struct task_struct *prev,
                               struct task_struct *next,
                               unsigned int prev_state)
{
    struct sched_entity *se = &next->se;
    int cpu = smp_processor_id();
    int idx = next->pid % NB_SLOTS;    // 極簡 hash；衝突就覆蓋（練習夠用）
    unsigned long flags;

    // per-CPU 計數：無鎖，各 CPU 加自己那份
    this_cpu_inc(switch_count);

    // 忽略 idle task（pid 0），它的統計沒意義且量很大
    if (next->pid == 0)
        return;

    // 進 per-pid 表要鎖。用 irqsave：probe 可能在關中斷的 context 跑（Ch 25）
    spin_lock_irqsave(&pstat_lock, flags);

    if (pstat[idx].pid != next->pid) {
        // 新槽或被別的 pid 佔用 → 重設（衝突時會覆蓋，接受）
        pstat[idx].pid           = next->pid;
        memcpy(pstat[idx].comm, next->comm, TASK_COMM_LEN);
        pstat[idx].picks         = 0;
        pstat[idx].first_runtime = se->sum_exec_runtime;
    }
    pstat[idx].picks++;
    pstat[idx].last_runtime  = se->sum_exec_runtime;   // 真實累積執行時間（Ch 12）
    pstat[idx].last_vruntime = se->vruntime;           // 排程 key（Ch 12）
    pstat[idx].last_deadline = se->deadline;           // EEVDF virtual deadline（Ch 13）
    pstat[idx].last_vlag     = se->vlag;               // EEVDF lag（Ch 13）
    pstat[idx].last_cpu      = cpu;

    spin_unlock_irqrestore(&pstat_lock, flags);
}

// ---- seq_file 輸出（跑在 process context，這裡能睡、能拿 spinlock）----
static int obs_show(struct seq_file *m, void *v)
{
    int i, cpu;
    u64 total = 0;
    unsigned long flags;

    seq_printf(m, "== per-pid schedule stats ==\n");
    seq_printf(m, "%6s %-16s %8s %14s %8s %18s %18s\n",
               "pid", "comm", "picks", "runtime_ns", "lastcpu",
               "vruntime", "deadline");

    spin_lock_irqsave(&pstat_lock, flags);
    for (i = 0; i < NB_SLOTS; i++) {
        struct pid_stat *p = &pstat[i];
        if (p->pid == 0)
            continue;
        // 觀測期間跑的時間 = 最新 - 首次看到的 sum_exec_runtime
        seq_printf(m, "%6d %-16s %8llu %14llu %8d %18llu %18llu\n",
                   p->pid, p->comm, p->picks,
                   p->last_runtime - p->first_runtime,
                   p->last_cpu, p->last_vruntime, p->last_deadline);
    }
    spin_unlock_irqrestore(&pstat_lock, flags);

    seq_printf(m, "== per-cpu switch counts ==\n");
    for_each_possible_cpu(cpu) {
        u64 c = per_cpu(switch_count, cpu);
        total += c;
        seq_printf(m, "  cpu%d: %llu\n", cpu, c);
    }
    seq_printf(m, "  total switches: %llu\n", total);
    return 0;
}

static int obs_open(struct inode *inode, struct file *file)
{
    return single_open(file, obs_show, NULL);
}

static const struct proc_ops obs_pops = {
    .proc_open    = obs_open,
    .proc_read    = seq_read,
    .proc_lseek   = seq_lseek,
    .proc_release = single_release,
};

static struct proc_dir_entry *obs_entry;

static int __init obs_init(void)
{
    int ret;

    // 掛 tracepoint。probe 簽章必須完全對上 sched_switch 的 TP_PROTO（+ void *data）
    ret = register_trace_sched_switch(probe_sched_switch, NULL);
    if (ret) {
        pr_err("sched_obs: register_trace_sched_switch failed: %d\n", ret);
        return ret;
    }

    obs_entry = proc_create("sched_obs", 0444, NULL, &obs_pops);
    if (!obs_entry) {
        unregister_trace_sched_switch(probe_sched_switch, NULL);
        tracepoint_synchronize_unregister();
        return -ENOMEM;
    }

    pr_info("sched_obs: hooked sched_switch, /proc/sched_obs ready\n");
    return 0;
}

static void __exit obs_exit(void)
{
    u64 total = 0;
    int cpu;

    // ★ 卸載順序關鍵（卡關提示 2）：
    // 1) 先解除 tracepoint，之後不再有新的 probe 觸發
    unregister_trace_sched_switch(probe_sched_switch, NULL);
    // 2) 等所有「正在跑」的 probe 跑完（內部是 synchronize_rcu），否則 UAF
    tracepoint_synchronize_unregister();
    // 3) 現在保證沒人在 probe 裡了，才能安全移除 /proc、釋放資源
    proc_remove(obs_entry);

    for_each_possible_cpu(cpu)
        total += per_cpu(switch_count, cpu);
    pr_info("sched_obs: unhooked, %llu switches observed\n", total);
}

module_init(obs_init);
module_exit(obs_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Practice B: CFS/EEVDF scheduler observation module");
MODULE_AUTHOR("kernel_internals");
```

**幾個設計決定的理由**：

- **為什麼 probe 裡用 spinlock 不用 mutex**：probe 跑在排程路徑，可能持 rq lock、關 preemption/中斷，**不能睡**。mutex 會睡（拿不到鎖時讓出 CPU），在這裡用 mutex = `scheduling while atomic` 甚至死鎖（你在排程器裡呼叫排程器）。spinlock 忙等、不睡，是這裡唯一選擇（Ch 25）。而且用 `spin_lock_irqsave` 版本，因為 probe 可能已在關中斷的 context，普通 `spin_lock` 不夠。
- **為什麼 per-CPU 計數用 `this_cpu_inc` 而非全域 `count++`**：四顆 CPU 同時 switch，全域 `count++` 是 race（要嘛用 atomic 但有 cache line bouncing，Ch 7），per-CPU 各加各的、零競爭、`cat` 時才加總。這是 kernel 統計計數的標準手法。
- **為什麼固定陣列而非動態 list**：probe 不能 `kmalloc(GFP_KERNEL)`。固定陣列零配置、零睡眠，最簡單能跑。代價是 `pid % NB_SLOTS` 衝突會混統計——對這個練習可接受，要精確請用卡關提示 3 的 (b)/(c)。
- **為什麼忽略 pid 0**：pid 0 是每顆 CPU 的 idle task（swapper），CPU 空閒時一直被「排到」，量大且對你的實驗無意義。
- **卸載三步順序**：unregister → `tracepoint_synchronize_unregister` → 釋放。少了中間那步，高頻 switch 下偶發 crash（見卡關提示 2）。

### `Makefile`

```makefile
# 注意：recipe 行首是 Tab 不是空白（make 老陷阱）
obj-m += sched_obs.o

KDIR := /path/to/your/linux-6.12      # 指向你 Ch 0 build 的那棵源碼樹

all:
	$(MAKE) -C $(KDIR) M=$(PWD) modules
clean:
	$(MAKE) -C $(KDIR) M=$(PWD) clean
```

```bash
make
ls sched_obs.ko
cp sched_obs.ko initramfs/     # 放進 Ch 0 的 initramfs，重打包 cpio
```

> **編譯可能遇到的坑**：`register_trace_sched_switch` 是由 `trace/events/sched.h` 裡的 `TRACE_EVENT` 巨集展開產生的行內函式，`#include <trace/events/sched.h>` 就有。若編譯報 `register_trace_sched_switch` undefined，多半是沒 include 這個 header，或你的 kernel 沒開 `CONFIG_TRACEPOINTS`（一般 defconfig 都有開，`CONFIG_FTRACE`/`CONFIG_TRACING` 拉進來）。Ch 0 的 config 建議加 `--enable FTRACE --enable TRACEPOINTS` 確保萬無一失。

### QEMU 開機（這次一定要 -smp 4）

```bash
qemu-system-x86_64 \
    -kernel arch/x86/boot/bzImage \
    -initrd initramfs.cpio.gz \
    -append "console=ttyS0 nokaslr" \
    -nographic -m 512M \
    -smp 4                       # ★ 四核，才觀測得到 SMP/per-CPU/遷移（Ch 15）
```

單核（不加 `-smp` 或 `-smp 1`）你會看到所有 switch 都在 cpu0，per-CPU 統計失去意義，load balancing 完全看不到。這個練習的價值一半在多核。

### 實驗腳本

**實驗一：驗 nice 1.25 倍規則（`exp1.sh`，在 QEMU 的 busybox shell 裡跑）**

```sh
#!/bin/sh
# 兩個 busy loop 綁同一顆 CPU（CPU 0），不同 nice，看 runtime 比
# 理論：nice 差 5 → CPU 比 1.25^5 ≈ 3.05:1
insmod /sched_obs.ko

taskset -c 0 nice -n 0 sh -c 'while :; do :; done' &
P0=$!
taskset -c 0 nice -n 5 sh -c 'while :; do :; done' &
P5=$!

sleep 10                        # 跑十秒累積統計

cat /proc/sched_obs             # 找 pid=$P0 和 pid=$P5 兩行，比 runtime_ns
echo "nice0 pid=$P0  nice5 pid=$P5  （runtime 比應約 3:1）"

kill $P0 $P5
```

跑完把 pid=$P0（nice 0）與 pid=$P5（nice 5）的 `runtime_ns` 相除，應約 3。重點：**兩者 `lastcpu` 都要是 0**，證明 `taskset` 綁對了、它們真的在同一顆 CPU 上競爭；若一個在 cpu0 一個在 cpu1，是綁失敗，比值會接近 1:1（各拿滿一顆）。改 `nice -n 1` 應約 1.25:1，`nice -n 10` 應約 9:1。

**實驗二：觀察 EEVDF latency（`exp2.sh` + 一個小 C 程式）**

互動型 task 用一個週期睡醒的迴圈：

```c
// interactive.c —— 互動型：每 10ms 醒來做極少事再睡（小 slice、要低延遲）
#include <unistd.h>
int main(void) {
    volatile long x = 0;
    for (;;) {
        for (int i = 0; i < 1000; i++) x += i;   // 極少工作
        usleep(10000);                            // 睡 10ms
    }
    return 0;
}
```

```sh
#!/bin/sh
# exp2.sh：互動 task vs batch task 綁同一顆 CPU，對照 se.deadline
insmod /sched_obs.ko

taskset -c 0 ./interactive &                       # 互動型（小 slice → 近 deadline）
IA=$!
taskset -c 0 sh -c 'while :; do :; done' &         # batch 型（CPU-bound）
BATCH=$!

sleep 8
cat /proc/sched_obs
echo "interactive pid=$IA  batch pid=$BATCH"
echo "看：interactive 的 deadline 應持續早於 batch；"
echo "    但 batch 的 runtime 不會被餓死（EEVDF 保長期公平）"

kill $IA $BATCH
```

編互動程式：`gcc -static -o interactive interactive.c`，放進 initramfs。跑完對照兩行：互動 task 的 `deadline` 欄應較小（較早）、`picks` 較多（常被喚醒排到）但單次跑很短；batch 的 `deadline` 較晚、`picks` 較少但累積 `runtime` 仍拿到它該有的份。這就是 Ch 13 那張圖的實測版。

### 進階：kprobe/kretprobe 掛 `pick_next_task_fair`（替代做法，供對照）

若你想走 kprobe 路線（掛內部函式）看清 tracepoint 幫你包掉什麼：

```c
// 片段：kretprobe 掛 pick_next_task_fair，在返回時拿被選中的 task
#include <linux/kprobes.h>

static int ret_handler(struct kretprobe_instance *ri, struct pt_regs *regs)
{
    struct task_struct *next = (struct task_struct *)regs_return_value(regs);
    if (next && next->pid) {
        int cpu = smp_processor_id();
        // ...同樣記 next->se.vruntime / deadline / cpu...
    }
    return 0;
}

static struct kretprobe krp = {
    .kp.symbol_name = "pick_next_task_fair",
    .handler        = ret_handler,
    .maxactive      = 20,
};
// init: register_kretprobe(&krp);  若回 -EINVAL/-ENOENT → 該函式被 inline 了，掛不上
// exit: unregister_kretprobe(&krp);
```

**為什麼還是推薦 tracepoint**：(1) `pick_next_task_fair` 在 v6.12 可能被 inline，`register_kretprobe` 直接失敗（先 `grep pick_next_task_fair /proc/kallsyms` 確認）；(2) 必須用 kretprobe（不是 kprobe）才拿得到回傳的 `next`，`.pre_handler` 在入口跑時 next 還沒選出來；(3) tracepoint 是穩定 ABI、跨版本可靠，kprobe 綁函式名脆弱。這段留給你對照兩種 hook 機制的差異（Ch 51 會深入 kprobe/tracepoint 的底層）。

</details>

## 測試用例表

| 測試 | 操作 | 期望結果 | 對應驗收 |
|---|---|---|---|
| 載入 | `insmod sched_obs.ko` | 回 0；`dmesg` 有 `hooked sched_switch` | #1 |
| 空讀 | 剛載入就 `cat /proc/sched_obs` | 已有若干 pid（shell、init 等），不 crash | #2 |
| comm 正確 | 起一個好認的程式（如 `sleep 100 &`）再 `cat` | 表裡看得到 `sleep` 那行 | #2 |
| 多核活著 | `-smp 4` 下 `cat` per-cpu 段 | cpu0–3 都有非零計數 | #3 |
| EEVDF 欄位 | 連續 `cat` 兩次 | 同一 pid 的 `vruntime` 增、`deadline` 非零 | #4 |
| nice 比例 | 跑 `exp1.sh`（nice 0 vs 5 綁 CPU0） | runtime 比約 3:1，兩者 lastcpu=0 | #6 |
| nice 差 1/10 | 改 nice 差 1、差 10 各跑 | 約 1.25:1、約 9:1 | 邊界 |
| EEVDF latency | 跑 `exp2.sh` | 互動 task deadline 早於 batch、batch runtime 不被餓死 | #7 |
| 綁錯 CPU 對照 | 故意不 `taskset`，兩 busy loop 自由跑 | 兩者跑不同 CPU 各拿 ~100%，比值 ≈1:1（反例，證明必須綁） | 邊界 |
| 高頻卸載 | busy loop 狂跑時 `rmmod` | 不 crash（`tracepoint_synchronize_unregister` 生效） | #5 |
| leak/UAF 檢查 | 開 KASAN 跑一輪載入-觀測-卸載 | 無 KASAN 報告 | #5 |

> **要看到「卸載 race」得先弄壞它**：想確認 `tracepoint_synchronize_unregister()` 真的有用，把它從 `obs_exit` 註解掉重編，在 busy loop 狂觸發 switch 時反覆 `insmod`/`rmmod`——沒有那步同步，偶爾會在 `rmmod` 瞬間 panic（probe 執行到一半模組沒了）。加回去就穩。這正是 Ch 7 「race 偶發、難復現」的又一次現身，也是為什麼 tracepoint 卸載有固定儀式。

## 卡關時的 gdb 用法

延續 Ch 0 的 QEMU + gdb（記得 QEMU 加 `-s -smp 4`）。`insmod` 後 `lx-symbols` 載模組符號，停進你的 probe：

```gdb
(gdb) lx-symbols
(gdb) break probe_sched_switch
(gdb) continue
```

回 QEMU 讓系統跑（隨便 `ls` 一下就會觸發 switch），gdb 停進 probe。看你手上的 `next`：

```gdb
(gdb) print next->comm                 # 被選中要上場的 task 名字
(gdb) print next->pid
(gdb) print next->se.vruntime          # Ch 12 的排程 key
(gdb) print next->se.deadline          # Ch 13 的 EEVDF virtual deadline
(gdb) print next->se.vlag              # Ch 13 的 lag
(gdb) print prev->comm                 # 被換下去的是誰
(gdb) print $lx_current().comm         # 對照：現在的 current（應該還是 prev）
```

把這個和 Ch 12/13 的 gdb 實驗接起來：那兩章你是**主動** `break update_curr`/`pick_eevdf` 去看排程器內部；這裡你是**被動**在 `sched_switch` 收排程器的決定。兩個視角合起來，你就完整看到「排程器怎麼決定 + 決定了什麼」。停在 probe 裡多 `continue` 幾次，觀察 `next` 在不同 CPU（`print $lx_per_cpu(cpu_number, ...)` 或直接看 `smp_processor_id()`）上的變化——這就是 Ch 15 的 task 遷移在你眼前發生。

## 踩雷集錦

1. **probe 裡呼叫會睡的東西**（`kmalloc(GFP_KERNEL)`、`mutex_lock`、`copy_from_user`、`msleep`）——這是本練習最核心的陷阱。probe 跑在排程路徑，可能持 rq lock、關 preemption。在裡面睡 = `scheduling while atomic`、死鎖（你在排程器裡呼叫排程器），或直接 lockup。統計表配置只能 `GFP_ATOMIC` 或預配置，鎖只能 spinlock（Ch 7/Ch 25）。這比練習 A 的 mutex 場景更嚴苛：練習 A 的寫路徑是能睡的 process context，這裡的 probe 是不能睡的原子 context。

2. **卸載漏 `tracepoint_synchronize_unregister()`**——`unregister_trace_sched_switch` 回來時可能還有別的 CPU 正在你的 probe 裡跑。馬上 `rmmod`，那個 probe 執行到一半程式碼被卸載 → panic。高頻 switch 下偶發、難復現。順序必須是 unregister → synchronize → 才釋放資源。這是所有 tracepoint 模組的固定儀式，不是可選優化。

3. **probe 簽章和 `TP_PROTO` 對不上**——`sched_switch` 的 probe 簽章是 `(void *data, bool preempt, struct task_struct *prev, struct task_struct *next, unsigned int prev_state)`。`void *data` 第一個、`prev_state` 最後一個（5.14 起才有）都容易漏。抄舊教材（沒有 `prev_state` 的版本）會編過但參數錯位、讀到垃圾。以你 v6.12 的 `include/trace/events/sched.h` 為準。

4. **全域計數器數 switch 而非 per-CPU**——`-smp 4` 下四顆 CPU 同時 switch，全域 `count++` 是 race（漏數）且 cache line bouncing 拖慢整個排程路徑（你的觀測工具反而干擾了被觀測的系統，觀測者效應）。用 `DEFINE_PER_CPU` + `this_cpu_inc`，`cat` 時加總。

5. **實驗一沒 `taskset` 綁同 CPU，結論全錯**——不綁的話 load balancer（Ch 15）幾秒內就把兩個 busy loop 分到不同 CPU，各拿 100%，你算出來的比值是 1:1，然後你以為「nice 沒用」。nice/vruntime 只在**同一個 cfs_rq 內**的 se 之間比較才有意義（Ch 12 反覆強調）。一定要 `taskset -c 0` 綁同顆，並用你模組記的 `lastcpu` 確認真的綁上了。這是觀測 CFS/EEVDF 最經典的翻車點。

## 延伸挑戰

1. **記「排程延遲」而非只記次數**：`sched_switch` 給你 `prev`/`next`，配合 `sched_wakeup` tracepoint（`register_trace_sched_wakeup`），你能算「一個 task 從被喚醒到真正上 CPU 之間等了多久」——這就是 `runqueue latency`，`bpf` 課裡 `runqlat` 工具量的正是它。實驗二的 EEVDF latency 用這個量比「看 deadline」更直接。

2. **每個 pid 建 latency 直方圖**：不只記平均，記一個 log2 分桶的直方圖（`<1us, 1-2us, 2-4us...`），`cat` 印成 ASCII bar。這是生產級觀測工具（bcc 的 `runqlat`）的核心呈現方式。

3. **抓 task 遷移**：在 probe 裡比對「這個 pid 上次的 `last_cpu` 和這次的 cpu」，不同就是一次跨 CPU 遷移（Ch 15 的 load balancing 或 wakeup 放到別的 CPU）。統計每個 pid 遷移幾次，觀察「你不綁 CPU 時 task 被搬得多勤」。

4. **換 debugfs 且加可清零介面**：把 `/proc` 換成 debugfs（`debugfs_create_file`，很多 tracing 設施用它），並加一個 write handle 讓 `echo reset > .../sched_obs/control` 清零統計，方便重跑實驗不用 `rmmod`。

5. **對照 `/proc/schedstat` 與 `/proc/<pid>/sched`**：kernel 自己也有排程統計（`CONFIG_SCHEDSTATS`）。把你模組記的數字和 `/proc/<pid>/sched` 的 `se.sum_exec_runtime`、`nr_switches` 對照，驗證你的觀測和 kernel 內建計數一致——這是驗證觀測工具正確性的標準手法。

6. **無鎖化 per-pid 表**（做完 Part 4 再回來）：目前 per-pid 表用一把全域 spinlock，在高頻 switch 下這把鎖本身成為瓶頸（你的觀測工具拖慢了排程）。改成 per-CPU 的 pid 表、或用 RCU 讀路徑（Ch 27），感受「連觀測都要付並行成本」。

## 自我檢核

- [ ] 不看解答，能說出為什麼 probe 裡**不能** `kmalloc(GFP_KERNEL)`、不能 `mutex_lock`（關鍵字：probe 跑在原子 context、可能持 rq lock、不能睡）
- [ ] 能解釋卸載時 `tracepoint_synchronize_unregister()` 防的是什麼（in-flight probe 的 UAF），以及漏掉為什麼是偶發 crash
- [ ] 能說出 `sched_switch` probe 的完整簽章，並解釋 `void *data`、`prev_state` 各是什麼、`prev_state` 為何是版本斷層
- [ ] 能解釋為什麼 switch 計數要 per-CPU（`this_cpu_inc`）而非全域，牽涉 race 和 cache line bouncing 兩件事（Ch 7）
- [ ] 能說清為什麼實驗一**必須** `taskset` 綁同一顆 CPU，不綁會得到什麼錯誤結論、根因是什麼（Ch 12/15）
- [ ] 能用自己記的 runtime 數字，說明「nice 差 5 ≈ 3 倍」怎麼從 `1.25^5` 來（Ch 12 的 weight 表）
- [ ] 能解釋實驗二裡「互動 task deadline 較早但 batch runtime 不被餓死」怎麼體現 EEVDF 的兩根軸（Ch 13）
- [ ] 面試被問「你會怎麼在不改排程器的前提下觀測它的決定」，能講出 tracepoint（穩定 ABI、next 已選定）vs kprobe（脆弱、要 kretprobe 拿回傳值）的取捨
- [ ] 能用 `lx-symbols` + `break probe_sched_switch` 停進 probe，`print next->se.vruntime/deadline` 看到 EEVDF 欄位

## 這個練習把哪些章拼在了一起

- **Ch 7 per-CPU 與並行本質**：`DEFINE_PER_CPU`/`this_cpu_inc` 避 race 與 cache bouncing、「probe 這裡能不能睡」的判斷、spinlock vs mutex 的硬約束
- **Ch 9 task_struct**：probe 拿到的 `prev`/`next` 就是 `task_struct *`，`->pid`/`->comm`/`->se` 的實戰
- **Ch 11 排程器框架**：`sched_switch` 觸發點在 `__schedule` 選完 next 之後、`context_switch` 之前的位置
- **Ch 12 CFS**：讀 `se.vruntime`、驗 nice→weight 的 1.25 倍規則、`sum_exec_runtime` 是真實時間
- **Ch 13 EEVDF**：讀 `se.deadline`/`se.vlag`、驗互動 task 的 latency 行為、公平與 latency 兩根軸
- **Ch 15 SMP**：`-smp 4` 才看得到的 per-CPU runqueue、task 跨 CPU 遷移、load balancing 對「必須綁 CPU」的影響
- **前導 Ch 51**：這裡用的 tracepoint/kprobe，底層機制在 Ch 51 深入；你在 `bpf` 課從 user 空間掛過同一個點，這次從 kernel 內部看清那層

做完這個練習，你手上有一個能跑、能被 gdb 停、能實測驗證 Ch 12/13 理論的排程觀測工具——你不只讀懂了 CFS/EEVDF，還親手量到了它。Part 2 的 process 與排程到此完整。下一步整個換檔：從「CPU 時間怎麼分」轉到「記憶體怎麼定址」——每個 task 的 `mm`（你在 Ch 9 的 task_struct 圖裡看過那個欄位）背後，是一套把虛擬位址翻成實體位址的多層 page table。我們從一次 page table walk 開始拆它。

→ [Ch 16 虛擬位址空間與 page table walk](./16-virtual-memory-page-tables.md)
