# Ch 34 — notifier 與 callback 註冊模式

> **目標**：看到一個只被「註冊」、從來沒被直接呼叫的 callback（`foo_netdev_event`、`tts_notify_reboot`、`sk->sk_data_ready`），能在幾分鐘內反查出：它掛在哪條 chain 或哪個欄位上、誰在什麼時候呼叫它、在什麼 context 呼叫、它的回傳值會被怎麼解讀。並且分得清 notifier chain 的四種型別與它們對 callback 的限制。

> **鴻溝**：本章主要練**間接鴻溝**（callback 的呼叫點是一個通用迴圈，靜態閱讀斷在 `nb->notifier_call(nb, val, v)`），附帶**領域鴻溝**（不知道 `NETDEV_UP` 什麼時候發生，就看不懂 `arp_netdev_event` 的 switch）。

> **環境**：Linux kernel v6.18 原始碼（`~/code_reading/linux`）；動態驗證用同版本 x86_64 build 在 QEMU（TCG）裡開 `notifier:notifier_register`、`notifier:notifier_run` 兩個 tracepoint。user-space 範例用 gcc 11.4 在 WSL Ubuntu 22.04 上跑。

## 為什麼需要這個？

前兩章其實已經碰到這個模式兩次：[Ch 32](./32-async-irq-softirq-workqueue.md) 的 `INIT_WORK(&w, fn)` 把函式存進 `work->func`，[Ch 33](./33-wait-queues-completion.md) 的 wait queue entry 帶一個 `func`，wakeup 時逐一呼叫。這一章把它當成一個獨立的讀碼主題，因為 kernel 裡**「先註冊、事件發生時被呼叫」的結構多到可以單獨成一類**：

- **notifier chain**：一條按 priority 排序的 callback list，事件發生時從頭呼叫到尾。網路介面上下線、系統重開機、CPU hotplug、panic、module 載入……都是。
- **單一 callback 欄位**：一個 struct 裡的一個函式指標，例如 `timer.function`、`sk->sk_data_ready`、`work->func`。它和 [Ch 28](./28-ops-tables-indirect-calls.md) 的 ops table 很像，差別是只有一個指標、而且**常常在執行期被換掉**。

`include/linux/notifier.h` 檔頭留著 Alan Cox 的一段話（`include/linux/notifier.h:2-8（v6.18）`）：

```c
/*
 *	Routines to manage notifier chains for passing status changes to any
 *	interested routines. We need this instead of hard coded call lists so
 *	that modules can poke their nose into the innards. The network devices
 *	needed them so here they are for the rest of you.
 *
 *				Alan Cox <Alan.Cox@linux.org>
 */
```

這句話把設計動機講完了：在 notifier 出現之前，「網卡 up 了要通知 ARP、路由、IPv6」只能在 `dev_open()` 裡寫死三行呼叫。每多一個關心這件事的子系統，就要改核心程式碼；如果關心者是可載入的 module，寫死根本做不到。notifier 把「誰關心」從呼叫端拿走，改成關心者自己來登記。

這正是 GoF 的 observer pattern，Spinellis 原書第 9 章談架構時也把這類「事件／訊息分派」視為主要的架構模式之一。代價也一樣：**呼叫端不知道會呼叫到誰，讀者也不知道**。這就是本章要處理的間接鴻溝。

## 先建立直覺

想像一份電子報：

- **訂閱**（`register_*_notifier(&nb)`）：你把自己的 email（`notifier_block`）交給發行者，可以要求排在前面（`priority`）。
- **發報**（`*_notifier_call_chain(&head, event, data)`）：發行者照名單一封封寄，信裡寫事件種類（`val`，例如 `NETDEV_UP`）和附件（`v`，例如 `struct net_device *`）。
- **回信**：每個訂閱者回一個狀態。「沒興趣」（`NOTIFY_DONE`）、「收到」（`NOTIFY_OK`）、「到此為止，別再寄給後面的人」（`NOTIFY_STOP`）、「我反對」（`NOTIFY_BAD`）。
- **發行者的規矩**決定訂閱者能做什麼：有的發報在半夜緊急狀況下進行（atomic chain，訂閱者不能睡），有的在白天慢慢寄（blocking chain，可以睡）。

```
 註冊端（散在各子系統的 init 函式）              呼叫端（事件發生的地方）
 ────────────────────────────────               ─────────────────────────
 arp_init():                                     netif_open():
   register_netdevice_notifier(&arp_nb) ──┐        ...
 devinet_init():                          │        call_netdevice_notifiers(NETDEV_UP, dev)
   register_netdevice_notifier(&ip_nb) ───┤                │
 addrconf_init():                         ▼                ▼
   register_netdevice_notifier(&ipv6_nb)  netdev_chain: [nb]─▶[nb]─▶[nb]─▶ ... ─▶ NULL
                                                         │      │      │
                                          notifier_call_chain() 逐一呼叫 nb->notifier_call()
```

**讀碼時你通常站在左下角**：手上有一個 `arp_netdev_event`，想知道它什麼時候跑。路線是「callback → notifier_block → register 呼叫 → chain 變數 → call_chain 呼叫點 → 事件來源」，本章後面會實際走一次。

## 資料結構與四種 chain

一個訂閱者就是一個 `notifier_block`（`include/linux/notifier.h:51-57（v6.18）`）：

```c
typedef	int (*notifier_fn_t)(struct notifier_block *nb,
			unsigned long action, void *data);

struct notifier_block {
	notifier_fn_t notifier_call;
	struct notifier_block __rcu *next;
	int priority;
};
```

注意它是**單向鏈結串列**，`next` 直接放在 block 裡（侵入式，[Ch 23](./23-list-head-hlist.md)），而且標了 `__rcu`（[Ch 18](./18-annotations-and-sparse.md)、[Ch 31](./31-reading-rcu-code.md)）：讀取端可以在 RCU 保護下走訪。

四種 chain head 的差別全在「保護 list 的是什麼」（`include/linux/notifier.h:60-79（v6.18）`）：

```c
struct atomic_notifier_head {
	spinlock_t lock;
	struct notifier_block __rcu *head;
};
struct blocking_notifier_head {
	struct rw_semaphore rwsem;
	struct notifier_block __rcu *head;
};
struct raw_notifier_head {
	struct notifier_block __rcu *head;
};
struct srcu_notifier_head {
	struct mutex mutex;
	struct srcu_usage srcuu;
	struct srcu_struct srcu;
	struct notifier_block __rcu *head;
};
```

檔頭的註解是最權威的規格（`include/linux/notifier.h:18-46（v6.18）`，節錄）：

```c
/*
 * Notifier chains are of four types:
 *
 *	Atomic notifier chains: Chain callbacks run in interrupt/atomic
 *		context. Callouts are not allowed to block.
 *	Blocking notifier chains: Chain callbacks run in process context.
 *		Callouts are allowed to block.
 *	Raw notifier chains: There are no restrictions on callbacks,
 *		registration, or unregistration.  All locking and protection
 *		must be provided by the caller.
 *	SRCU notifier chains: A variant of blocking notifier chains, with
 *		the same restrictions.
 ...
 * atomic_notifier_chain_unregister(), blocking_notifier_chain_unregister(),
 * and srcu_notifier_chain_unregister() _must not_ be called from within
 * the call chain.
```

整理成讀碼用的表：

| chain | 保護 | call_chain 時拿什麼 | callback 能睡？ | 典型例子（v6.18） |
|---|---|---|---|---|
| atomic | spinlock（寫）+ RCU（讀） | `rcu_read_lock()` | **不能** | `panic_notifier_list`（`kernel/panic.c:77`） |
| blocking | rw_semaphore | `down_read(&nh->rwsem)` | 可以 | `reboot_notifier_list`（`kernel/reboot.c:82`） |
| raw | **無**，呼叫者自己負責 | 什麼都不拿 | 依呼叫者的 context | `netdev_chain`（`net/core/dev.c:449`，靠 RTNL）、`pvclock_gtod_chain`（`kernel/time/timekeeping.c:607`） |
| srcu | mutex（寫）+ SRCU（讀） | `srcu_read_lock()` | 可以 | `lease_notifier_chain`（`fs/locks.c`）、clk notifier |

**raw chain 是最難讀的一種**：它的 callback 能不能睡，要看呼叫 `raw_notifier_call_chain` 的地方在什麼 context。`netdev_chain` 的呼叫者都拿著 RTNL mutex，所以 netdevice notifier 可以睡；`pvclock_gtod_chain` 的呼叫者在 timer 中斷裡（下面會在 trace 裡看到），所以它的 callback 不能睡。**同一個 API，兩種完全不同的限制**。

## 底層機制：register 與 call_chain

註冊是一個「按 priority 插入」的迴圈（`kernel/notifier.c:17-37（v6.18）`）：

```c
static int notifier_chain_register(struct notifier_block **nl,
				   struct notifier_block *n,
				   bool unique_priority)
{
	while ((*nl) != NULL) {
		if (unlikely((*nl) == n)) {
			WARN(1, "notifier callback %ps already registered",
			     n->notifier_call);
			return -EEXIST;
		}
		if (n->priority > (*nl)->priority)
			break;
		if (n->priority == (*nl)->priority && unique_priority)
			return -EBUSY;
		nl = &((*nl)->next);
	}
	n->next = *nl;
	rcu_assign_pointer(*nl, n);
	trace_notifier_register((void *)n->notifier_call);
	return 0;
}
```

- `struct notifier_block **nl` 是 [Ch 10](./10-pointer-idioms.md) 講的「指標的指標」走訪法：不需要特別處理插在頭的情況。
- `n->priority > (*nl)->priority` 用**嚴格大於**：priority 大的排前面；priority 相同時新來的排在既有者後面（先來先服務）。大部分 notifier 不設 priority（預設 0），所以**呼叫順序 ≈ 註冊順序 ≈ initcall 順序**。
- `rcu_assign_pointer`：先把 `n->next` 填好，再發布 `n`，讓同時在走訪的 reader 看到一致的 list（[Ch 31](./31-reading-rcu-code.md)）。
- `trace_notifier_register`：一個 tracepoint，**這是我們反查 callback 的動態利器**，下面會用到。

呼叫端是整個機制的「斷點」（`kernel/notifier.c:65-95（v6.18）`）：

```c
static int notifier_call_chain(struct notifier_block **nl,
			       unsigned long val, void *v,
			       int nr_to_call, int *nr_calls)
{
	int ret = NOTIFY_DONE;
	struct notifier_block *nb, *next_nb;

	nb = rcu_dereference_raw(*nl);

	while (nb && nr_to_call) {
		next_nb = rcu_dereference_raw(nb->next);
		...
		trace_notifier_run((void *)nb->notifier_call);
		ret = nb->notifier_call(nb, val, v);

		if (nr_calls)
			(*nr_calls)++;

		if (ret & NOTIFY_STOP_MASK)
			break;
		nb = next_nb;
		nr_to_call--;
	}
	return ret;
}
```

兩個容易忽略的語意：

1. **先取 `next_nb` 再呼叫**：callback 執行期間如果把自己 unregister（不建議，註解也說 must not），迴圈仍然能往下走。
2. **回傳值是最後一個被呼叫者的回傳值**，不是彙總。只有帶 `NOTIFY_STOP_MASK` 的值會中斷迴圈。

回傳值的定義（`include/linux/notifier.h:185-193（v6.18）`）：

```c
#define NOTIFY_DONE		0x0000		/* Don't care */
#define NOTIFY_OK		0x0001		/* Suits me */
#define NOTIFY_STOP_MASK	0x8000		/* Don't call further */
#define NOTIFY_BAD		(NOTIFY_STOP_MASK|0x0002)
						/* Bad/Veto action */
/*
 * Clean way to return from the notifier and stop further calls.
 */
#define NOTIFY_STOP		(NOTIFY_OK|NOTIFY_STOP_MASK)
```

緊接著還有 `notifier_from_errno()` / `notifier_to_errno()`：把 `-errno` 編進回傳值（`NOTIFY_STOP_MASK | (NOTIFY_OK - err)`），讓 callback 可以用一個錯誤碼否決事件。讀到 `return notifier_from_errno(err);` 就知道：這個 callback 失敗時會**中止整條 chain**。

### 把它搬到 user space 跑一次

```c
/* minichain.c: 把 kernel/notifier.c 的 register/call_chain 邏輯搬到 user space */
#include <stdio.h>

#define NOTIFY_DONE      0x0000
#define NOTIFY_OK        0x0001
#define NOTIFY_STOP_MASK 0x8000
#define NOTIFY_BAD       (NOTIFY_STOP_MASK | 0x0002)
#define NOTIFY_STOP      (NOTIFY_OK | NOTIFY_STOP_MASK)

struct notifier_block {
	int (*notifier_call)(struct notifier_block *nb, unsigned long val, void *v);
	struct notifier_block *next;
	int priority;
};

/* 同 notifier_chain_register()：priority 大的在前，同 priority 先來先排 */
static int chain_register(struct notifier_block **nl, struct notifier_block *n)
{
	while (*nl) {
		if (*nl == n) return -17;            /* -EEXIST */
		if (n->priority > (*nl)->priority) break;
		nl = &(*nl)->next;
	}
	n->next = *nl;
	*nl = n;
	return 0;
}

/* 同 notifier_call_chain()：逐一呼叫，遇到 STOP_MASK 就停 */
static int call_chain(struct notifier_block **nl, unsigned long val, void *v)
{
	int ret = NOTIFY_DONE;
	for (struct notifier_block *nb = *nl; nb; nb = nb->next) {
		ret = nb->notifier_call(nb, val, v);
		if (ret & NOTIFY_STOP_MASK) break;
	}
	return ret;
}

#define EV_UP   1
#define EV_DOWN 2
static int log_cb(struct notifier_block *nb, unsigned long ev, void *v)
{ printf("  log    (prio %d) saw event %lu\n", nb->priority, ev); return NOTIFY_DONE; }
static int arp_cb(struct notifier_block *nb, unsigned long ev, void *v)
{ printf("  arp    (prio %d) saw event %lu\n", nb->priority, ev); return NOTIFY_OK; }
static int veto_cb(struct notifier_block *nb, unsigned long ev, void *v)
{
	printf("  veto   (prio %d) saw event %lu\n", nb->priority, ev);
	return ev == EV_DOWN ? NOTIFY_BAD : NOTIFY_DONE;   /* 不准 DOWN */
}

static struct notifier_block *chain;
static struct notifier_block nb_log  = { .notifier_call = log_cb };
static struct notifier_block nb_arp  = { .notifier_call = arp_cb };
static struct notifier_block nb_veto = { .notifier_call = veto_cb, .priority = 10 };

int main(void)
{
	chain_register(&chain, &nb_log);   /* 註冊順序：log, arp, veto */
	chain_register(&chain, &nb_arp);
	chain_register(&chain, &nb_veto);
	printf("register twice -> %d\n", chain_register(&chain, &nb_arp));
	printf("call_chain(EV_UP):\n");
	int r = call_chain(&chain, EV_UP, NULL);
	printf("  -> 0x%04x\n", r);
	printf("call_chain(EV_DOWN):\n");
	r = call_chain(&chain, EV_DOWN, NULL);
	printf("  -> 0x%04x (stopped=%d)\n", r, !!(r & NOTIFY_STOP_MASK));
	return 0;
}
```

```
$ gcc -O2 -Wall -Wno-unused-parameter -o minichain minichain.c && ./minichain
register twice -> -17
call_chain(EV_UP):
  veto   (prio 10) saw event 1
  log    (prio 0) saw event 1
  arp    (prio 0) saw event 1
  -> 0x0001
call_chain(EV_DOWN):
  veto   (prio 10) saw event 2
  -> 0x8002 (stopped=1)
```

三個觀察，每一個都對應一個讀碼陷阱：

- `veto` 最後註冊，卻第一個被呼叫——priority 10 讓它插隊。**不能用註冊程式碼的位置推斷呼叫順序**，要看 priority。
- `EV_UP` 的回傳值是 `0x0001`，那是**最後一個**（`arp`）的回傳值，`log` 的 `NOTIFY_DONE` 被蓋掉了。
- `EV_DOWN` 時 `veto` 回 `NOTIFY_BAD`，`log` 和 `arp` 完全沒被呼叫。在 kernel 裡，這代表後面的子系統**根本不知道這個事件發生過**——如果呼叫端不處理這個回傳值，系統狀態就會不一致。這就是 `*_call_chain_robust()` 存在的理由：它在有人否決時，用另一個事件把已經通知過的人「回滾」。

## 四種 chain 的鎖怎麼套

atomic 的 call_chain（`kernel/notifier.c:217-227（v6.18）`）：

```c
int atomic_notifier_call_chain(struct atomic_notifier_head *nh,
			       unsigned long val, void *v)
{
	int ret;

	rcu_read_lock();
	ret = notifier_call_chain(&nh->head, val, v, -1, NULL);
	rcu_read_unlock();

	return ret;
}
```

blocking 的 call_chain（`kernel/notifier.c:368-383（v6.18）`）：

```c
int blocking_notifier_call_chain(struct blocking_notifier_head *nh,
		unsigned long val, void *v)
{
	int ret = NOTIFY_DONE;

	/*
	 * We check the head outside the lock, but if this access is
	 * racy then it does not matter what the result of the test
	 * is, we re-check the list after having taken the lock anyway:
	 */
	if (rcu_access_pointer(nh->head)) {
		down_read(&nh->rwsem);
		ret = notifier_call_chain(&nh->head, val, v, -1, NULL);
		up_read(&nh->rwsem);
	}
	return ret;
}
```

`down_read` 會睡，所以 blocking chain 只能從 process context 呼叫——這反過來保證了 callback 在 process context。atomic chain 只拿 `rcu_read_lock()`，可以從中斷呼叫；unregister 時要 `synchronize_rcu()` 等所有 reader 走完（`kernel/notifier.c:186-197（v6.18）`），所以 **atomic chain 的 unregister 本身會睡**，不能在 atomic context 呼叫。

還有一個開機期的特例：`__blocking_notifier_chain_register()` 在 `system_state == SYSTEM_BOOTING` 時直接呼叫 `notifier_chain_register()`，不拿 rwsem，註解說那時「task switching is not yet working and interrupts must remain disabled」。讀到 `system_state` 判斷時，通常就是這種「開機太早，常規機制還不能用」的處理。

## 例子一：reboot notifier（blocking chain）

`kernel/reboot.c:82（v6.18）`：

```c
static BLOCKING_NOTIFIER_HEAD(reboot_notifier_list);
```

註冊 API（`kernel/reboot.c:118-122（v6.18）`）：

```c
int register_reboot_notifier(struct notifier_block *nb)
{
	return blocking_notifier_chain_register(&reboot_notifier_list, nb);
}
EXPORT_SYMBOL(register_reboot_notifier);
```

呼叫點有兩個：restart 路徑（`kernel/reboot.c:100-106（v6.18）`）

```c
void kernel_restart_prepare(char *cmd)
{
	blocking_notifier_call_chain(&reboot_notifier_list, SYS_RESTART, cmd);
	system_state = SYSTEM_RESTART;
	usermodehelper_disable();
	device_shutdown();
}
```

和 halt / power off 路徑（`kernel/reboot.c:302-305（v6.18）`）：

```c
	blocking_notifier_call_chain(&reboot_notifier_list,
		(state == SYSTEM_HALT) ? SYS_HALT : SYS_POWER_OFF, NULL);
```

`val` 是 `SYS_RESTART` / `SYS_HALT` / `SYS_POWER_OFF`，`v` 是 restart 命令字串或 NULL。v6.18 tree 裡有 103 處呼叫 `register_reboot_notifier(`（`rg -n 'register_reboot_notifier\(' -g '*.c' . | grep -v unregister | wc -l`）。挑一個在我們的 QEMU guest 裡真的會註冊的：ACPI 的 `tts_notifier`（`drivers/acpi/sleep.c:50-61（v6.18）`）：

```c
static int tts_notify_reboot(struct notifier_block *this,
			unsigned long code, void *x)
{
	acpi_sleep_tts_switch(ACPI_STATE_S5);
	return NOTIFY_DONE;
}

static struct notifier_block tts_notifier = {
	.notifier_call	= tts_notify_reboot,
	.next		= NULL,
	.priority	= 0,
};
```

註冊在 `drivers/acpi/sleep.c:1138`：`register_reboot_notifier(&tts_notifier);`。它不看 `code`，任何形式的關機都通知 ACPI 韌體要進 S5 狀態，回 `NOTIFY_DONE`（不關心後續）。

> 演進註記：v5.19 起 kernel/reboot.c 多了 `register_sys_off_handler()`（`git show v5.18:kernel/reboot.c` 裡沒有），把「關機時要做的事」分成更細的階段與 priority，內部仍然用 notifier chain 實作（`sys_off_notify()`，`kernel/reboot.c:341（v6.18）`）。新 driver 傾向用它；舊的 `register_reboot_notifier` 仍大量存在。下面的 trace 裡就能看到三個 `sys_off_notify` 註冊。

## 例子二：netdevice notifier（raw chain + RTNL）

這是 notifier 的元祖。chain 在 `net/core/dev.c:449（v6.18）`：

```c
static RAW_NOTIFIER_HEAD(netdev_chain);
```

為什麼是 raw？看註冊函式（`net/core/dev.c:1947-1974（v6.18）`，節錄）：

```c
int register_netdevice_notifier(struct notifier_block *nb)
{
	struct net *net;
	int err;

	/* Close race with setup_net() and cleanup_net() */
	down_write(&pernet_ops_rwsem);

	/* When RTNL is removed, we need protection for netdev_chain. */
	rtnl_lock();

	err = raw_notifier_chain_register(&netdev_chain, nb);
	if (err)
		goto unlock;
	if (dev_boot_phase)
		goto unlock;
	for_each_net(net) {
		__rtnl_net_lock(net);
		err = call_netdevice_register_net_notifiers(nb, net);
		...
```

保護 `netdev_chain` 的是網路子系統的大鎖 RTNL（一個 mutex），所以 chain 本身不需要自己的鎖。那行註解「When RTNL is removed, we need protection for netdev_chain」是作者留給未來的提醒——網路子系統正在把 RTNL 拆成 per-netns 的鎖（`__rtnl_net_lock`）。另一個重點：註冊時會**對所有已存在的網卡補發一次事件**（`call_netdevice_register_net_notifiers`），晚註冊的 module 也能看到開機時就存在的 `lo`、`eth0`。

呼叫端（`net/core/dev.c:2214-2230（v6.18）`）：

```c
int call_netdevice_notifiers_info(unsigned long val,
				  struct netdev_notifier_info *info)
{
	struct net *net = dev_net(info->dev);
	int ret;

	ASSERT_RTNL();

	/* Run per-netns notifier block chain first, then run the global one.
	 * Hopefully, one day, the global one is going to be removed after
	 * all notifier block registrators get converted to be per-netns.
	 */
	ret = raw_notifier_call_chain(&net->netdev_chain, val, info);
	if (ret & NOTIFY_STOP_MASK)
		return ret;
	return raw_notifier_call_chain(&netdev_chain, val, info);
}
```

`ASSERT_RTNL()` 是讀 raw chain 時最寶貴的一行：它明文寫出「呼叫者必須持有 RTNL」，所以所有 netdevice notifier callback 都在拿著 mutex 的 process context 執行，**可以睡**。

### 反查：從 `arp_netdev_event` 走到事件來源

假設你在讀 `net/ipv4/arp.c`，看到 `arp_netdev_event()`（`net/ipv4/arp.c:1322-1354（v6.18）`，節錄）：

```c
static int arp_netdev_event(struct notifier_block *this, unsigned long event,
			    void *ptr)
{
	struct net_device *dev = netdev_notifier_info_to_dev(ptr);
	struct netdev_notifier_change_info *change_info;
	struct in_device *in_dev;
	bool evict_nocarrier;

	switch (event) {
	case NETDEV_CHANGEADDR:
		neigh_changeaddr(&arp_tbl, dev);
		rt_cache_flush(dev_net(dev));
		break;
	case NETDEV_CHANGE:
		...
	default:
		break;
	}

	return NOTIFY_DONE;
}
```

**第一步：callback → notifier_block**

```
$ rg -n "= arp_netdev_event" net/
net/ipv4/arp.c:1357:	.notifier_call = arp_netdev_event,
```

**第二步：notifier_block → register 呼叫**（block 叫 `arp_netdev_notifier`）

```
$ rg -n "arp_netdev_notifier\)" net/
net/ipv4/arp.c:1522:	register_netdevice_notifier(&arp_netdev_notifier);
```

**第三步：register 函式 → chain 變數 → call_chain 呼叫點**

```
$ rg -n "netdev_chain" net/core/dev.c
449:static RAW_NOTIFIER_HEAD(netdev_chain);
1955:	/* When RTNL is removed, we need protection for netdev_chain. */
1958:	err = raw_notifier_chain_register(&netdev_chain, nb);
1983:	raw_notifier_chain_unregister(&netdev_chain, nb);
2010:	err = raw_notifier_chain_unregister(&netdev_chain, nb);
2033:	err = raw_notifier_chain_register(&net->netdev_chain, nb);
2046:	raw_notifier_chain_unregister(&net->netdev_chain, nb);
2055:	err = raw_notifier_chain_unregister(&net->netdev_chain, nb);
2226:	ret = raw_notifier_call_chain(&net->netdev_chain, val, info);
2229:	return raw_notifier_call_chain(&netdev_chain, val, info);
2254:	return raw_notifier_call_chain_robust(&net->netdev_chain,
12680:	RAW_INIT_NOTIFIER_HEAD(&net->netdev_chain);
```

call_chain 只在 2226、2229、2254 三行——全在 `call_netdevice_notifiers_info*()` 裡。**chain 變數是 `static` 的好處**：所有呼叫點都被關在一個檔案裡。

**第四步：事件來源**。`arp_netdev_event` 關心 `NETDEV_CHANGEADDR` 與 `NETDEV_CHANGE`，搜尋誰發這兩個事件就找到觸發者（例如 `dev_set_mac_address` 路徑）。同樣的方法找 `NETDEV_UP`：

```
$ rg -n 'call_netdevice_notifiers\(NETDEV_UP,' -g '*.c' .
./net/core/dev.c:1710:	call_netdevice_notifiers(NETDEV_UP, dev);
./net/core/dev.c:9676:			call_netdevice_notifiers(NETDEV_UP, dev);
```

第 1710 行在 `netif_open()` 裡（`net/core/dev.c:1698-1713（v6.18）`），也就是 `ip link set dev X up` 最終走到的地方。

四步走完，間接鴻溝填平：`arp_netdev_event` 在「網卡狀態改變」時，由持有 RTNL 的 process context 經 `call_netdevice_notifiers_info` 呼叫，可以睡，回傳值不影響其他 callback。

## 動態反查：讓 kernel 告訴你誰註冊了什麼

靜態反查有一個盲點：priority 相同時的實際順序取決於 initcall 順序，而 Kconfig 決定哪些 callback 根本有沒有被編進去（[Ch 36](./36-kconfig-kbuild-ifdef.md)）。`notifier_chain_register()` 和 `notifier_call_chain()` 裡那兩個 tracepoint 能直接回答「這台機器上實際有誰」。

開機參數 `trace_event=` 可以讓 tracepoint 從開機就啟用。我們複製一份 `run-script.sh`，在 `-append` 後面加上 `trace_event=notifier:notifier_register,notifier:notifier_run`，guest 腳本：

```sh
#!/bin/sh
T=/sys/kernel/tracing
echo 0 > $T/tracing_on
echo "== boot-time registrations: total =="
grep -c notifier_register $T/trace
echo "== runs at boot: top callbacks =="
grep notifier_run $T/trace | sed 's/.*notifier_run: //' | sort | uniq -c | sort -rn | head -10
echo > $T/trace
echo 1 > $T/tracing_on
/bin/busybox ifconfig lo 127.0.0.1 up
echo 0 > $T/tracing_on
echo "== notifier_run during: ifconfig lo up =="
grep -v '^#' $T/trace | head -40
```

真實輸出（節錄；註冊總數那次另外列了全部名稱）：

```
== boot-time registrations: total ==
73
== runs at boot: top callbacks ==
     27 cryptomgr_notify
     16 iommu_bus_notifier
      7 i8042_kbd_bind_notifier
      6 rtnetlink_event
      6 pci_notify
      6 dev_map_notification
      4 xfrm_dev_event
      4 sel_netif_netdev_notifier_handler
      4 page_pool_netdevice_event
      4 nh_netdev_event
== notifier_run during: ifconfig lo up ==
         busybox-81      [000] .....     2.245381: notifier_run: fib_inetaddr_event
         busybox-81      [000] .....     2.247896: notifier_run: rtnetlink_event
         busybox-81      [000] .....     2.247902: notifier_run: dev_map_notification
         busybox-81      [000] .....     2.247904: notifier_run: netdev_genl_netdevice_event
         busybox-81      [000] .....     2.247906: notifier_run: page_pool_netdevice_event
         busybox-81      [000] .....     2.247908: notifier_run: fib_rules_event
         busybox-81      [000] .....     2.247910: notifier_run: netprio_device_event
         busybox-81      [000] .....     2.247912: notifier_run: ethnl_netdev_event
         busybox-81      [000] .....     2.247922: notifier_run: nh_netdev_event
         busybox-81      [000] .....     2.247925: notifier_run: netdev_notify
         busybox-81      [000] .....     2.247927: notifier_run: netlbl_unlhsh_netdev_handler
         busybox-81      [000] .....     2.247928: notifier_run: arp_netdev_event
         busybox-81      [000] .....     2.247930: notifier_run: inetdev_event
         busybox-81      [000] .....     2.247950: notifier_run: fib_netdev_event
         busybox-81      [000] .....     2.247952: notifier_run: xfrm_dev_event
         busybox-81      [000] .....     2.247954: notifier_run: igmp_netdev_event
         busybox-81      [000] .....     2.247955: notifier_run: ipmr_device_event
         busybox-81      [000] .....     2.247957: notifier_run: cfg80211_netdev_notifier_call
         busybox-81      [000] .....     2.247959: notifier_run: sel_netif_netdev_notifier_handler
         busybox-81      [000] .....     2.247960: notifier_run: failover_event
         busybox-81      [000] .....     2.247962: notifier_run: addrconf_notify
         busybox-81      [000] .....     2.247972: notifier_run: ipv6_mc_netdev_event
         busybox-81      [000] .....     2.247974: notifier_run: packet_notifier
         busybox-81      [000] .....     2.247977: notifier_run: netconsole_netdev_event
         busybox-81      [000] .....     2.247979: notifier_run: ndisc_netdev_event
         busybox-81      [000] .....     2.247980: notifier_run: ip6_route_dev_notify
         busybox-81      [000] .....     2.248306: notifier_run: rtnetlink_event
         ...
```

讀這份輸出：

- 第一行 `fib_inetaddr_event` 屬於另一條 chain（inetaddr chain，設定 IP 位址觸發），之後從 `rtnetlink_event` 到 `ip6_route_dev_notify` 是一次完整的 netdev chain 走訪，`ifconfig ... up` 觸發了不止一個 netdev 事件，所以 chain 被走了好幾次。
- 旗標是 `.....`、TASK 是 `busybox-81`：process context，符合 `ASSERT_RTNL` 的推論。
- **順序就是註冊順序**：開機時的 `notifier_register` 紀錄裡，`netdev_genl_netdevice_event`、`page_pool_netdevice_event`、`fib_rules_event`……`arp_netdev_event`、`inetdev_event`、`fib_netdev_event` 的出現順序和這裡的呼叫順序一致（`rtnetlink_event`、`dev_map_notification` 更早註冊，排在最前面）。它們都是 priority 0，排序完全由 initcall 決定。
- 在我們的 kernel config 下，netdev chain 上有約 25 個 callback。**這個數字無法從原始碼讀出來**——它取決於 `.config`。

注意：`trace_event=` 在開機很早就生效，但未必早於所有 notifier 註冊；這份 73 筆的清單是「tracepoint 生效之後」的註冊，不能保證完整。要拿完整清單，靜態搜尋仍然必要。

### 空的 chain 也會出現在 trace 裡

[Ch 32](./32-async-irq-softirq-workqueue.md) 和練習 E 的 function_graph 裡，timer 中斷底下出現過這幾行：

```
 1)               |                    raw_notifier_call_chain() {
 1)   1.964 us    |                      notifier_call_chain();
 1)   4.769 us    |                    } /* raw_notifier_call_chain */
```

它來自 `timekeeping_update_from_shadow()`（`kernel/time/timekeeping.c:708` 起，呼叫點在第 734 行）裡呼叫的 `update_pvclock_gtod()`（`kernel/time/timekeeping.c:607-612（v6.18）`）：

```c
static RAW_NOTIFIER_HEAD(pvclock_gtod_chain);

static void update_pvclock_gtod(struct timekeeper *tk, bool was_set)
{
	raw_notifier_call_chain(&pvclock_gtod_chain, was_set, tk);
}
```

`notifier_call_chain` 底下沒有任何子呼叫——chain 是空的。誰會註冊它？

```
$ rg -n 'pvclock_gtod_register_notifier\(' -g '*.c' .
./kernel/time/timekeeping.c:618:int pvclock_gtod_register_notifier(struct notifier_block *nb)
./arch/arm/xen/enlighten.c:525:		pvclock_gtod_register_notifier(&xen_pvclock_gtod_notifier);
./arch/x86/xen/time.c:560:		pvclock_gtod_register_notifier(&xen_pvclock_gtod_notifier);
./arch/x86/kvm/x86.c:10094:	pvclock_gtod_register_notifier(&pvclock_gtod_notifier);
```

只有 Xen 和 KVM **host**（`arch/x86/kvm/` 是 hypervisor 端）會註冊。我們的 guest kernel 只開了 `CONFIG_KVM_GUEST=y`，沒有 `CONFIG_KVM`，所以這條 chain 永遠是空的。這條 raw chain 的呼叫者在 timer 中斷裡（`pvclock_gtod_register_notifier` 還在 `guard(raw_spinlock_irqsave)(&tk_core.lock)` 下註冊），所以 KVM 的 callback 必定不能睡——**同一個 raw chain API，這裡的限制和 netdev chain 完全相反**。

## 單一 callback 欄位：不是 chain 的 notifier

很多「事件通知」只有一個接收者，用不著 chain，直接在 struct 裡放一個函式指標。讀法和 [Ch 28](./28-ops-tables-indirect-calls.md) 的 ops table 相同（找賦值點），但有兩個額外的難處：**賦值常在執行期發生，而且會被換掉**。

### `sk->sk_data_ready`

`struct sock` 裡的欄位（`include/net/sock.h:441（v6.18）`）：

```c
	void			(*sk_data_ready)(struct sock *sk);
```

預設值在 `sock_init_data` 系列裡設定（`net/core/sock.c:3662（v6.18）`）：`sk->sk_data_ready = sock_def_readable;`。預設實作（`net/core/sock.c:3542-3555（v6.18）`）：

```c
void sock_def_readable(struct sock *sk)
{
	struct socket_wq *wq;

	trace_sk_data_ready(sk);

	rcu_read_lock();
	wq = rcu_dereference(sk->sk_wq);
	if (skwq_has_sleeper(wq))
		wake_up_interruptible_sync_poll(&wq->wait, EPOLLIN | EPOLLPRI |
						EPOLLRDNORM | EPOLLRDBAND);
	sk_wake_async_rcu(sk, SOCK_WAKE_WAITD, POLL_IN);
	rcu_read_unlock();
}
```

這個函式把前三章串起來了：

- **呼叫者**是 TCP 收包路徑，例如 `tcp_data_ready()`（`net/ipv4/tcp_input.c:5352-5356（v6.18）`）裡的 `sk->sk_data_ready(sk);`。TCP 收包跑在 `NET_RX_SOFTIRQ`（[Ch 32](./32-async-irq-softirq-workqueue.md)），所以 **`sk_data_ready` 的任何實作都必須能在 softirq 執行、不能睡**。
- **動作**是 `wake_up_interruptible_sync_poll(&wq->wait, ...)`——[Ch 33](./33-wait-queues-completion.md) 的 waker。睡在 `recv(2)` 裡的 task 就掛在 `wq->wait` 上。
- 它本身就是一個 callback 欄位（本章），**而且可以被換掉**：

```
$ rg -n 'sk_data_ready\s*=' -g '*.c' . | wc -l
80
$ rg -n 'sk_data_ready\s*=' -g '*.c' . | head -8
./net/rds/tcp.c:123:	sock->sk->sk_data_ready = tc->t_orig_data_ready;
./net/rds/tcp.c:212:	if (sock->sk->sk_data_ready == rds_tcp_listen_data_ready)
./net/rds/tcp.c:213:		sock->sk->sk_data_ready = sock->sk->sk_user_data;
./net/rds/tcp.c:222:	sock->sk->sk_data_ready = rds_tcp_data_ready;
./net/rds/tcp_listen.c:274:	sock->sk->sk_data_ready = rds_tcp_listen_data_ready;
./net/rds/tcp_listen.c:324:		sk->sk_data_ready = sk->sk_user_data;
./net/tipc/topsrv.c:484:		newsk->sk_data_ready = tipc_conn_data_ready;
./net/tipc/topsrv.c:525:	sk->sk_data_ready = tipc_topsrv_listener_data_ready;
```

（第 212 行是比較，不是賦值；regex 的 `\s*=` 會吃到 `==`，看結果時要人工過濾。）

RDS、TIPC、NFS、iSCSI 這類 kernel 內的 socket 使用者，會把 `sk_data_ready` 換成自己的函式：資料進來時不叫醒 user task，而是直接在 kernel 裡處理（通常是 queue 一個 work）。`net/rds/tcp.c:123` 的 `t_orig_data_ready` 顯示了慣用法：**換掉之前先存下原本的指標，還原時放回去**。讀到某個 socket 的 `sk_data_ready(sk)` 呼叫時，你必須先知道這個 socket 是誰建的，才知道實際會跑哪個函式。

### 其他常見的單一 callback

| 欄位 | 賦值點 | 呼叫點 | 呼叫 context |
|---|---|---|---|
| `timer_list.function` | `timer_setup()` | `call_timer_fn()` 的 `fn(timer)` | softirq |
| `work_struct.func` | `INIT_WORK()` | `process_one_work()` 的 `worker->current_func(work)` | kworker |
| `wait_queue_entry.func` | `init_wait_entry()` / 手動賦值 | `__wake_up_common()` 的 `curr->func(...)` | waker 的 context |
| `bio->bi_end_io` | 提交 bio 的人 | `bio_endio()` | 通常是 irq/softirq 完成路徑 |
| `rcu_head.func` | `call_rcu(&p->rcu, fn)` | RCU callback 處理 | softirq 或 rcuo kthread |

`bio->bi_end_io` 值得多說一句：練習 E 的 ext4 讀取路徑上，`ext4_mpage_readpages()` 把它設成 `mpage_end_io`，I/O 完成時在中斷相關的路徑呼叫；`mpage_end_io` 若需要解密，就 `INIT_WORK` 把工作丟到 workqueue（`fs/ext4/readpage.c:116-143（v6.18）`）。一個 callback 欄位、一次 context 切換，三章的內容在同一段 code 裡出現。

## 對比與取捨

| 機制 | 接收者數量 | 呼叫順序 | 能否否決 | 讀碼的反查起點 |
|---|---|---|---|---|
| ops table（[Ch 28](./28-ops-tables-indirect-calls.md)） | 1（每個物件一組） | — | 靠回傳 errno | `.read_iter =` 等 designated initializer |
| 單一 callback 欄位 | 1，可在執行期替換 | — | 視 API | `欄位名\s*=` 的所有賦值點 |
| notifier chain | 0..N | priority，然後註冊順序 | `NOTIFY_BAD` / `NOTIFY_STOP` | `.notifier_call = fn` → `register_*` → chain → `call_chain` |
| wait queue | 0..N | 頭到尾（exclusive 在尾） | 不行 | `wait_queue_head` 成員 |
| tracepoint / BPF | 0..N，執行期掛載 | — | 不行 | `trace_xxx()` 呼叫點 |

notifier chain 的缺點在 kernel 社群裡常被討論：呼叫順序隱式、回傳值語意鬆散、出錯時難以回滾、callback 的 context 限制要靠文件約定。所以新子系統常改用更明確的介面（例如 sys-off handler、netdev 的 per-netns chain），但既有的 notifier 數量龐大，讀碼時一定會碰到。

## 踩雷集錦

1. **「用 `rg arp_netdev_event` 就能找到呼叫者」**：只會找到定義和 `.notifier_call =` 那一行。callback 的呼叫者是 `notifier_call_chain()` 裡的通用迴圈，要走「block → register → chain → call_chain」四步。
2. **「raw chain 的 callback 能不能睡，看 notifier.h 就知道」**：notifier.h 只說 raw chain「no restrictions」。限制來自呼叫者：`netdev_chain` 在 RTNL mutex 下（可睡），`pvclock_gtod_chain` 在 timer 中斷裡（不可睡）。一定要找到 `raw_notifier_call_chain` 的呼叫點。
3. **「回 `NOTIFY_OK` 和 `NOTIFY_DONE` 差不多」**：對 `notifier_call_chain` 確實一樣（都不中斷），但部分呼叫者會檢查回傳值做判斷，例如 `notifier_to_errno(ret)`。callback 回什麼要看那條 chain 的呼叫者怎麼解讀，不能只看 notifier.h 的註解。
4. **「我在 callback 裡 unregister 自己沒問題」**：notifier.h 明文說 atomic / blocking / srcu 的 unregister「_must not_ be called from within the call chain」——blocking chain 的 call_chain 拿著 `down_read`，unregister 要 `down_write`，自己等自己；atomic 的 unregister 要 `synchronize_rcu()`，在 RCU read-side 裡呼叫會死鎖。
5. **「`sk->sk_data_ready(sk)` 一定是 `sock_def_readable`」**：80 個賦值點說明它常被替換。讀網路 code 時看到這行，先確認 socket 的建立者。

## 進階：再往深一層

- **`*_call_chain_robust()`**：`kernel/notifier.c:114` 起。它先發 `val_up`，若有人否決，就對已經通知過的 callback 發 `val_down` 回滾。CPU hotplug、PM（suspend/resume）大量使用。讀它能看懂 `nr_to_call` 和 `nr_calls` 這兩個參數為什麼存在。
- **unique priority**：`atomic_notifier_chain_register_unique_prio()` 讓同 priority 的第二個註冊者拿到 `-EBUSY`，用於「同一時間只能有一個 handler」的情況（例如 restart handler）。
- **srcu chain 的取捨**：notifier.h 的註解說 srcu chain 的 call 成本極低（no cache bounces、no memory barriers），但 unregister「rather expensive」。適合「經常呼叫、很少移除」的場景，cpufreq transition notifier 就是例子。
- **把 notifier 換成 BPF**：很多原本要寫 module 掛 notifier 才能觀察的事件，現在可以用 tracepoint + BPF 從外部觀察，不改變系統行為。本章用的 `notifier:notifier_run` tracepoint 就是這個思路，[Ch 51](./51-dynamic-tracing.md) 會用 bpftrace 做更多。

## 動手練習

1. 對 `panic_notifier_list` 走一次四步反查：找出三個註冊者，說明 panic 時它們在什麼 context 被呼叫（提示：`kernel/panic.c:519` 附近的程式碼在呼叫前做了什麼）。
2. 在 QEMU guest 裡用本章的 `trace_event=` 開機參數，執行 `poweroff -f` 之前把 trace 存起來，再想辦法在關機路徑上看到 `tts_notify_reboot`（提示：tracefs 在關機時已經無法讀；試試 `tp_printk` 開機參數，讓事件直接印到 console）。
3. 用 `rg -n 'register_netdevice_notifier' -g '*.c' . | wc -l` 數出 netdevice notifier 的註冊點總數，和本章 trace 裡實際被呼叫的約 25 個比較，挑三個沒出現在 trace 裡的，查出它們對應的 Kconfig 選項。

## 本章重點整理

- notifier chain = 按 priority 排序的單向 callback list；`notifier_call_chain()` 逐一呼叫，遇到 `NOTIFY_STOP_MASK` 停止，回傳最後一個 callback 的值。
- 四種 chain 的差別是保護方式：atomic（RCU，callback 不能睡）、blocking（rwsem，可睡）、raw（呼叫者負責，限制要看呼叫點）、srcu（SRCU，可睡）。
- 反查 callback 的固定路線：`.notifier_call = fn` → `register_*(&nb)` → chain 變數 → `*_call_chain` 呼叫點 → 事件來源。
- `notifier:notifier_register` / `notifier_run` tracepoint 能列出這台機器上實際註冊與執行的 callback 及順序。
- 單一 callback 欄位（`sk_data_ready`、`timer.function`、`bi_end_io`）要找所有賦值點，並注意執行期替換。

## 自我檢核

- [ ] 不看筆記，寫出從一個 netdevice notifier callback 反查到 `netif_open()` 的四個步驟，每步用什麼搜尋。
- [ ] 同事說「raw notifier 沒有限制，所以 callback 裡可以 `mutex_lock`」。舉出 v6.18 裡一個讓這句話成立、一個讓它不成立的 raw chain。
- [ ] 為什麼註冊時 priority 用嚴格大於比較？如果改成大於等於，minichain 的輸出會怎麼變？
- [ ] `notifier_call_chain` 為什麼要在呼叫 callback 之前先取 `next_nb`？
- [ ] 解釋 `sock_def_readable` 怎麼把 softirq、wait queue、callback 欄位三件事接起來。
- [ ] 一個 notifier chain 的呼叫順序可以從原始碼完全推出來嗎？哪些因素會讓靜態推論失準？

## 延伸閱讀

### 官方文件與原始碼

- **`include/linux/notifier.h` 檔頭註解（v6.18）**
  - **讀哪裡**：第 18–46 行。四種 chain 的規格、register/unregister 的 context 限制、srcu chain 的成本取捨，全部在這段。kernel 沒有獨立的 notifier 文件頁，這段註解就是規格。
- **`kernel/notifier.c`（v6.18）**
  - **讀哪裡**：全檔不到 600 行，`notifier_chain_register`、`notifier_call_chain`、`notifier_call_chain_robust` 三個函式讀完就掌握全部機制。
  - **前提**：讀過 [Ch 31](./31-reading-rcu-code.md) 的 `rcu_assign_pointer` / `rcu_dereference`。
- **[Network devices, the Kernel, and You!](https://docs.kernel.org/networking/netdevices.html)**（`Documentation/networking/netdevices.rst`）
  - **讀哪裡**：「Device management under RTNL」與「struct net_device synchronization rules」兩節，列出 `ndo_*` 回呼各自在什麼鎖下被呼叫（多數是 `rtnl_lock()`）。
  - **和本章的關聯**：文件本身不談 notifier，但它讓你看到網路子系統「以 RTNL 為中心」的同步模型；`call_netdevice_notifiers_info` 裡的 `ASSERT_RTNL()` 就是這個模型的一部分。

### 書籍

- **《Understanding Linux Network Internals》** — Christian Benvenuti（O'Reilly, 2005）
  - **讀哪幾章**：Ch 4「Notification Chains」。整本書最常被引用的一章，用網路子系統完整講解 notifier 的設計、事件種類與註冊時機。2.6.12 時代的 code，API 名稱與 RTNL 細節已變，設計理由仍然適用。
- **《Design Patterns》** — Gamma, Helm, Johnson, Vlissides（Addison-Wesley, 1994）
  - **讀哪裡**：Observer 一節。對照本章，看 C 語言怎麼用一個函式指標加一條 list 實作同一個模式，以及 GoF 提到的「更新順序不確定」「級聯更新」問題在 kernel 裡怎麼出現。

### 技術文章

- **LWN 的 notifier 相關報導**
  - **怎麼找**：[LWN Kernel Index](https://lwn.net/Kernel/Index/) 的「Notifiers」條目，以及 2022 年 sys-off handler 合併時的報導。
  - **為什麼值得讀**：能看到社群對 notifier 缺點（隱式順序、回傳值語意不清）的批評，以及新 API 想解決什麼。

到這裡，Part 5 的工具都齊了：ops table、syscall 入口、lock、RCU、非同步 context、等待與喚醒、callback 註冊。練習 E 把它們全部用上，從 `read(2)` 一路追到 page cache。

→ [練習 E：從 read(2) 一路追到 page cache](./practice-e-trace-read-syscall.md)
