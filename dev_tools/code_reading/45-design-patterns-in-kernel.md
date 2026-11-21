# Ch 45 — 辨識 kernel 裡的設計模式

> **目標**：在 kernel 程式碼裡認出十種反覆出現的設計模式（strategy、observer、template method、factory、iterator、adapter、registry、RAII、引用計數、null object），每一種都能指出 v6.18 的真實例子；更重要的是，認出模式之後能**預測**其餘部分長什麼樣，於是可以有把握地跳讀。

> **鴻溝**：本章主要練**慣用法鴻溝**（模式是放大尺度的慣用法）與**目標鴻溝**（知道模式，才知道哪些部分不用讀）。

> **環境**：Linux kernel v6.18 原始碼；WSL2 Ubuntu 22.04（WSL kernel `6.18.58.1-microsoft-standard-WSL2`）、gcc 11.4；Redis 7.2.17 原始碼作為 user-space 對照。

## 為什麼需要這個？

[Ch 2](./02-program-comprehension-models.md) 介紹過兩個認知科學的概念：**programming plan**（程式設計計畫，Soloway 與 Ehrlich 1984 年的研究提出：熟手腦中存著大量「做某件事的典型寫法」，例如「累加迴圈」「搜尋直到找到」）與 **beacon**（信標，Brooks 1983：一小段一眼就能認出的程式碼，暗示了周圍整片程式碼的功能）。熟手讀碼快，不是因為他讀得快，而是因為他看到一個 beacon，就從記憶裡叫出整個 plan，然後**只驗證和 plan 不同的地方**。

設計模式（design pattern）是同一件事在架構尺度上的版本。Gamma 等人 1994 年的《Design Patterns》（俗稱 GoF）把物件導向系統裡反覆出現的結構命名、編目。Spinellis 在原書 Ch 9.4「Architecture Reuse」也談到這點：框架、程式碼巫師、設計模式、領域特定架構，都是讓讀者「認出已知結構」的工具，認出來之後就能依賴對那個結構的既有知識去理解新程式碼。

kernel 沒有用 C++，GoF 的 23 個模式也不是每個都出現。但其中一部分以 C 的形式大量存在，而且 kernel 有幾個 GoF 沒收錄、卻同樣固定的模式（RAII、引用計數、registry）。本章的重點**不是**背模式定義，而是建立這個反射：

```
看到 beacon  ──►  叫出模式  ──►  預測其餘部分的長相  ──►  只讀「和預測不同」的地方
 .ops = &xxx       strategy        一定有一張 const 表、       哪些欄位是 NULL？
                                   一個註冊函式、呼叫端         哪個是新加的？
                                   會檢查 NULL
```

## 先建立直覺

想像你第一次走進一家陌生的超市。你不需要逐一走過每條走道才知道牛奶在哪：冷藏櫃在牆邊、收銀台在出口、生鮮在入口附近。你腦中有「超市」這個模式，它讓你**預測**佈局，只在預測失敗時才停下來找。

讀 kernel 也是。第一次看到 `struct notifier_block` 時你要從頭讀；第十次看到時，你瞄一眼它的 `notifier_call` 指向哪個函式、那個函式的 `switch (event)` 處理哪些事件，其他都是你已經知道的骨架。

下面每一個模式都用同一個格式：**beacon（怎麼認）→ v6.18 實例 → 預測清單（認出後就知道的事）→ 可以跳過什麼**。

## 1. Strategy：ops table

**Beacon**：一個全是函式指標的 struct，被宣告成 `static const`（或 `__read_mostly`）的實例；物件上有一個指向它的指標；呼叫端寫 `x->ops->method(x, ...)`。

這是 kernel 最普遍的模式，Ch 28、Ch 41、Ch 42 已經從不同角度讀過 `file_operations`。這裡用一個更符合 GoF 原意的例子：**可替換的演算法**。TCP 壅塞控制演算法可以在執行期逐 socket 切換：

```c
/* include/net/tcp.h:1230（v6.18，節錄） */
struct tcp_congestion_ops {
	...
	u32 (*ssthresh)(struct sock *sk);
	void (*cong_avoid)(struct sock *sk, u32 ack, u32 acked);
	void (*set_state)(struct sock *sk, u8 new_state);
	void (*cwnd_event)(struct sock *sk, enum tcp_ca_event ev);
	...
	u32  (*undo_cwnd)(struct sock *sk);
	...
	char 			name[TCP_CA_NAME_MAX];
	struct module		*owner;
	struct list_head	list;
	...
```

```c
/* net/ipv4/tcp_cubic.c:478（v6.18） */
static struct tcp_congestion_ops cubictcp __read_mostly = {
	.init		= cubictcp_init,
	.ssthresh	= cubictcp_recalc_ssthresh,
	.cong_avoid	= cubictcp_cong_avoid,
	.set_state	= cubictcp_state,
	.undo_cwnd	= tcp_reno_undo_cwnd,
	.cwnd_event	= cubictcp_cwnd_event,
	.pkts_acked     = cubictcp_acked,
	.owner		= THIS_MODULE,
	.name		= "cubic",
};
```

呼叫端（context）只知道介面：`icsk->icsk_ca_ops->cong_avoid(sk, ack, acked);`（`net/ipv4/tcp_input.c:3297`）。註冊時，`tcp_validate_congestion_control`（`net/ipv4/tcp_cong.c:77`）檢查必要方法：

```c
	/* all algorithms must implement these */
	if (!ca->ssthresh || !ca->undo_cwnd ||
	    !(ca->cong_avoid || ca->cong_control)) {
		pr_err("%s does not implement required ops\n", ca->name);
		return -EINVAL;
	}
```

在 WSL 上可以看到目前可用的 strategy：

```
$ cat /proc/sys/net/ipv4/tcp_available_congestion_control
reno cubic
$ cat /proc/sys/net/ipv4/tcp_congestion_control
cubic
```

**預測清單**：
- 有一個**註冊函式**（`tcp_register_congestion_control`），把實例掛進全域串列 → 這同時是 registry 模式（第 7 節）。
- 有一個**驗證函式**或文件說明哪些方法必填、哪些可為 `NULL`。
- 呼叫可選方法前會檢查 `NULL`，或通用層提供預設（`tcp_reno_undo_cwnd` 這種「借用別人的實作」很常見）。
- 有 `.owner = THIS_MODULE`，代表實作可以在模組裡，使用期間要持有模組 refcount。

**可以跳過**：其他 strategy 實作。讀懂 cubic 的骨架後，看 BBR、Vegas 時比較「哪些方法不同」就夠了。

user-space 對照：Redis 7.2 的連線層也是 strategy。`ConnectionType` 讓 TCP、Unix socket、TLS 共用一套介面，`socket.c:405`、`unix.c:174`、`tls.c:1122` 各填一份 `.accept_handler`。

## 2. Observer：notifier chain

**Beacon**：`struct notifier_block`、`register_*_notifier()`／`unregister_*_notifier()` 成對出現、handler 函式簽名是 `(struct notifier_block *, unsigned long event, void *data)`。

```c
/* include/linux/notifier.h:54（v6.18） */
struct notifier_block {
	notifier_fn_t notifier_call;
	struct notifier_block __rcu *next;
	int priority;
};
```

發布者呼叫 `*_notifier_call_chain`，最後都落在這個迴圈：

```c
/* kernel/notifier.c:65（v6.18，節錄） */
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
		ret = nb->notifier_call(nb, val, v);
		...
		if (ret & NOTIFY_STOP_MASK)
			break;
		nb = next_nb;
		nr_to_call--;
	}
	return ret;
}
```

訂閱者的長相幾乎一模一樣。6LoWPAN 訂閱網路裝置事件：

```c
/* net/6lowpan/core.c:108（v6.18，節錄） */
static int lowpan_event(struct notifier_block *unused,
			unsigned long event, void *ptr)
{
	struct net_device *dev = netdev_notifier_info_to_dev(ptr);
	...
	if (dev->type != ARPHRD_6LOWPAN)
		return NOTIFY_DONE;
	...
	switch (event) {
	case NETDEV_UP:
	case NETDEV_CHANGE:
		...
		break;
	case NETDEV_DOWN:
		...
		break;
	default:
		return NOTIFY_DONE;
	}

	return NOTIFY_OK;
}

static struct notifier_block lowpan_notifier = {
	.notifier_call = lowpan_event,
};
/* core.c:156: register_netdevice_notifier(&lowpan_notifier); */
/* core.c:176: unregister_netdevice_notifier(&lowpan_notifier); */
```

**預測清單**（看到 `notifier_block` 就成立）：
- handler 開頭先**過濾**：不是我關心的物件就 `return NOTIFY_DONE`。
- 接著 `switch (event)`，每個 case 對應一種事件常數（`NETDEV_UP`……），`default` 回 `NOTIFY_DONE`。
- `void *data` 要先轉型成事件專屬的資訊 struct（`netdev_notifier_info_to_dev`）。
- module init 裡有 register，exit 裡有對稱的 unregister。
- handler 跑在**發布者的執行脈絡**裡：blocking chain 可以睡，atomic chain 不行。這是唯一需要去查的事（看註冊函式的名字或發布點）。

**可以跳過**：`notifier_call_chain` 本身——讀過一次就不用再讀。細節見 [Ch 34](./34-notifiers-and-callbacks.md)。

## 3. Template method：通用骨架 + 可覆寫的 hook

**Beacon**：一個通用函式裡寫著「如果 ops 有提供 X 就呼叫它，否則做預設的事」；或者具體實作「先做自己的事，再呼叫 `generic_*`」。

GoF 的 template method 用繼承實現：基底類別定義演算法骨架，子類別覆寫其中幾個步驟。kernel 用 ops 欄位的「有或沒有」來實現同一件事：

```c
/* fs/namei.c:521（v6.18） */
static inline int do_inode_permission(struct mnt_idmap *idmap,
				      struct inode *inode, int mask)
{
	if (unlikely(!(inode->i_opflags & IOP_FASTPERM))) {
		if (likely(inode->i_op->permission))
			return inode->i_op->permission(idmap, inode, mask);

		/* This gets set once for the inode lifetime */
		spin_lock(&inode->i_lock);
		inode->i_opflags |= IOP_FASTPERM;
		spin_unlock(&inode->i_lock);
	}
	return generic_permission(idmap, inode, mask);
}
```

骨架是「做權限檢查」，hook 是 `i_op->permission`，預設實作是 `generic_permission`。這裡還有一個實作細節值得欣賞：第一次發現某個 inode 沒有自訂 `permission` 時，設定 `IOP_FASTPERM` 旗標，之後直接走預設路徑，連 `i_op->permission` 這次記憶體讀取都省掉。

同一個模式在前幾章出現過好幾次：`alloc_inode`（`fs/inode.c:340`，有 `s_op->alloc_inode` 就用，否則用通用 cache）、`vfs_read`（`->read` 優先、`->read_iter` 次之、都沒有就 `-EINVAL`）、`call_driver_probe`（`bus->probe` 優先，否則 `drv->probe`）、`device_release` 的三層解析（Ch 42）。另一種長相是**反向的**：具體實作包住通用版本，例如 Ch 41 的 `ext4_file_read_iter` 只處理 DAX 與 direct I/O，其餘交給 `generic_file_read_iter`。

**預測清單**：
- 通用函式在 hook 為 `NULL` 時有合理預設，所以**大多數實作會留空**。
- 文件（例如 `vfs.rst`）對每個 hook 會寫「如果不提供，VFS 會……」。
- 具體實作覆寫 hook 時，常在函式最後呼叫通用版本。

**可以跳過**：沒有覆寫 hook 的檔案系統或 driver 的那一部分——它的行為就是通用預設。

## 4. Factory：依參數決定建立哪種物件

**Beacon**：一個建立函式接收「類型」參數，查表找到對應的 ops 與大小，配置物件後把 ops 指標填上。

`socket(AF_INET, SOCK_STREAM, 0)` 怎麼變成一個 TCP socket？`__sock_create` 依 family 找到 `inet_family_ops`，呼叫 `pf->create`（`net/socket.c:1605`），也就是 `inet_create`（`net/ipv4/af_inet.c:254`）。`inet_create` 再依 type 與 protocol 查第二張表：

```c
/* net/ipv4/af_inet.c:1155（v6.18，節錄） */
static struct inet_protosw inetsw_array[] =
{
	{
		.type =       SOCK_STREAM,
		.protocol =   IPPROTO_TCP,
		.prot =       &tcp_prot,
		.ops =        &inet_stream_ops,
		.flags =      INET_PROTOSW_PERMANENT |
			      INET_PROTOSW_ICSK,
	},
	...
```

```c
/* net/ipv4/af_inet.c（v6.18），inet_create() 內 */
274:	list_for_each_entry_rcu(answer, &inetsw[sock->type], list) {
	...
320:	sock->ops = answer->ops;
321:	answer_prot = answer->prot;
	...
328:	sk = sk_alloc(net, PF_INET, GFP_KERNEL, answer_prot, kern);
```

`sk_alloc` 依 `answer_prot->obj_size` 配置（Ch 42 讀過的 `sk_prot_alloc`），配出來的就是一個 2304 bytes 的 `tcp_sock`。兩層查表、一次配置、兩個 vtable 指標被填上——這就是工廠。

另一種常見長相是**以 setup callback 參數化的工廠**：

```c
/* net/ethernet/eth.c:379（v6.18） */
struct net_device *alloc_etherdev_mqs(int sizeof_priv, unsigned int txqs,
				      unsigned int rxqs)
{
	return alloc_netdev_mqs(sizeof_priv, "eth%d", NET_NAME_ENUM,
				ether_setup, txqs, rxqs);
}
```

`alloc_netdev_mqs` 是通用工廠，`ether_setup` 是「把這個 net_device 設定成乙太網路裝置」的 callback；Wi-Fi、bonding、tun 各自傳入自己的 setup。`sizeof_priv` 讓工廠在 `net_device` 後面多配一塊給 driver 私有資料，driver 用 `netdev_priv()` 取得。

**預測清單**：
- 有一張或多張「類型 → (ops, 大小)」的表，可能是靜態陣列，也可能是 registry。
- 建立後的物件，其 ops 指標在工廠裡被設定，之後不太會改（`struct socket` 的 `ops` 註解特別寫了 `Might change with IPV6_ADDRFORM or MPTCP`，正因為那是例外）。
- 配置大小來自表，所以「衍生類別」的完整物件是一次配好的。

**可以跳過**：工廠本身的查表與錯誤處理；你真正關心的是「這次查到了哪一筆」。

## 5. Iterator：`for_each` 與 seq_file

**Beacon（小尺度）**：`list_for_each_entry`、`for_each_possible_cpu`、`xa_for_each`、`hash_for_each`——所有 `for_each_*` 巨集。[Ch 22](./22-kernel-macro-decoding.md) 與 Part 4 已經讀過它們怎麼展開，這裡只強調：**看到 `for_each_*` 就是一個迴圈，主體裡的變數是目前元素**，迴圈怎麼走是巨集的事，你不用讀。

**Beacon（大尺度）**：`struct seq_operations` 的四個方法 `start`／`next`／`stop`／`show`。

```c
/* include/linux/seq_file.h:31（v6.18） */
struct seq_operations {
	void * (*start) (struct seq_file *m, loff_t *pos);
	void (*stop) (struct seq_file *m, void *v);
	void * (*next) (struct seq_file *m, void *v, loff_t *pos);
	int (*show) (struct seq_file *m, void *v);
};
```

這是把 iterator 做成介面：子系統提供「怎麼走訪我的資料」，`seq_read_iter`（`fs/seq_file.c:171`）負責「怎麼把結果分批複製給 user space，buffer 不夠時怎麼加倍重來」。`/proc/modules` 是典型的實作：

```c
/* kernel/module/procfs.c:49（v6.18） */
static void *m_start(struct seq_file *m, loff_t *pos)
{
	mutex_lock(&module_mutex);
	return seq_list_start(&modules, *pos);
}

static void *m_next(struct seq_file *m, void *p, loff_t *pos)
{
	return seq_list_next(p, &modules, pos);
}

static void m_stop(struct seq_file *m, void *p)
{
	mutex_unlock(&module_mutex);
}
/* procfs.c:112 */
static const struct seq_operations modules_op = {
	.start	= m_start,
	.next	= m_next,
	.stop	= m_stop,
	.show	= m_show
};
```

**預測清單**（看到 `seq_operations` 就成立）：
- `start` 拿鎖、`stop` 放鎖——鎖的範圍是一次 `read()` 系統呼叫，不是整個檔案的開啟期間。所以兩次 `read()` 之間資料可能改變，`start` 要能依 `*pos` 重新定位。
- `next` 一定要遞增 `*pos`。`seq_read_iter` 會檢查這件事：`fs/seq_file.c:264` 印出 `"buggy .next function %ps did not update position index\n"`。
- `show` 只用 `seq_printf` 系列輸出，不碰 user 指標。
- 若資料只有「一筆」，會改用 `single_open`／`proc_create_single`，四個方法全部省略，只寫一個 `show`。

最後一點本身是 adapter，見下一節。

## 6. Adapter：把一個介面轉成另一個

**Beacon**：一個很短的函式，參數是介面 A 的形狀，函式體把參數重新包裝後呼叫介面 B。

Ch 41 讀過的 `new_sync_read`（`fs/read_write.c:481`）就是 adapter：把舊式的 `(buf, len, ppos)` 包成 `(kiocb, iov_iter)` 呼叫 `->read_iter`。另一個例子把「一個 show 函式」轉接成完整的 `seq_operations`：

```c
/* fs/seq_file.c:572（v6.18，節錄） */
int single_open(struct file *file, int (*show)(struct seq_file *, void *),
		void *data)
{
	struct seq_operations *op = kmalloc(sizeof(*op), GFP_KERNEL_ACCOUNT);
	int res = -ENOMEM;

	if (op) {
		op->start = single_start;
		op->next = single_next;
		op->stop = single_stop;
		op->show = show;
		res = seq_open(file, op);
```

`/proc/filesystems` 就是這樣建出來的：`fs/filesystems.c:259` 的 `proc_create_single("filesystems", 0, NULL, filesystems_proc_show)`，整個檔案的實作就是一個 show 函式。

user-space 對照：Redis 用**編譯期 adapter** 讓同一個 event loop 跑在不同 OS 的多工 I/O API 上。`ae.c` 第 53–61 行依平台 `#include "ae_evport.c"`、`"ae_epoll.c"`、`"ae_kqueue.c"` 或 `"ae_select.c"`，每個檔案都提供同一組 `aeApiCreate`／`aeApiAddEvent`／`aeApiPoll`。這是 adapter 和 strategy 的混合：介面相同、實作在編譯時選定。

**預測清單**：adapter 函式很短、沒有自己的業務邏輯；讀懂一次就可以當它不存在，直接把呼叫鏈接到被轉接的那一端。

## 7. Registry（singleton 式的全域登錄表）

**Beacon**：一個 `static` 的全域串列頭或陣列，加上 `register_*()`／`unregister_*()`／`find_*()` 三件組，以及保護它的一把鎖。

GoF 的 singleton 在 kernel 裡沒有特別意義（C 的全域變數本來就是唯一的），真正常見的是**全域 registry**：一個子系統維護「目前有哪些實作」的唯一清單。檔案系統類型是最乾淨的例子：

```c
/* fs/filesystems.c:34（v6.18） */
static struct file_system_type *file_systems;

/* fs/filesystems.c:49 */
static struct file_system_type **find_filesystem(const char *name, unsigned len)
{
	struct file_system_type **p;
	for (p = &file_systems; *p; p = &(*p)->next)
		if (strncmp((*p)->name, name, len) == 0 &&
		    !(*p)->name[len])
			break;
	return p;
}

/* fs/filesystems.c:72（節錄） */
int register_filesystem(struct file_system_type * fs)
{
	...
	write_lock(&file_systems_lock);
	p = find_filesystem(fs->name, strlen(fs->name));
	if (*p)
		res = -EBUSY;
	else
		*p = fs;
	write_unlock(&file_systems_lock);
	return res;
}
```

`find_filesystem` 回傳的是**指向指標的指標**（Ch 10 的慣用法）：找到時指向那個節點的 `next` 欄位，找不到時指向串列尾端的 `NULL` 指標，`register_filesystem` 於是可以直接 `*p = fs` 接在尾端，不需要特別處理空串列。

串列的內容可以直接從 user space 看到，順序就是註冊順序：

```
$ grep -c . /proc/filesystems
39
$ head -8 /proc/filesystems
nodev	sysfs
nodev	tmpfs
nodev	proc
nodev	cgroup
nodev	cgroup2
nodev	cpuset
nodev	devtmpfs
nodev	binfmt_misc
```

輸出它的 `filesystems_proc_show`（`fs/filesystems.c:241`）就是一個走訪 `file_systems` 串列的 while 迴圈，`nodev` 表示該類型沒有設定 `FS_REQUIRES_DEV`。

**預測清單**：register 檢查重複名稱並回 `-EBUSY`／`-EEXIST`；unregister 從串列移除；查找函式可能會觸發 `request_module()` 自動載入模組（`get_fs_type` 就是）；串列由 rwlock、spinlock 或 RCU 保護。TCP 壅塞控制（`tcp_cong_list`）、網路協定 family、crypto 演算法都是同一個形狀。

## 8. RAII：cleanup.h

**Beacon**：變數宣告上的 `__free(...)`、`guard(...)(...)`、`scoped_guard(...)`、`CLASS(...)`，以及 `no_free_ptr()`／`return_ptr()`。

這是 v6.5 才進入 kernel 的模式（`include/linux/cleanup.h` 由 commit `54da6a092431`「locking: Introduce __cleanup() based infrastructure」加入，`git tag --contains` 顯示第一個包含它的 tag 是 v6.5），底層是 GCC／Clang 的 `__attribute__((cleanup(fn)))`：變數離開 scope 時自動呼叫 `fn(&var)`。到了 v6.18，`guard(`／`scoped_guard(`／`__free(` 在 tree 裡已出現八千多次（`rg -c` 加總為 8052）。[Ch 17](./17-kernel-error-conventions.md) 從錯誤處理的角度介紹過，這裡從模式角度看。

```c
/* include/linux/cleanup.h:210（v6.18） */
#define DEFINE_FREE(_name, _type, _free) \
	static inline void __free_##_name(void *p) { _type _T = *(_type *)p; _free; }

#define __free(_name)	__cleanup(__free_##_name)
...
#define no_free_ptr(p) \
	((typeof(p)) __must_check_fn((__force const volatile void *)__get_and_null(p, NULL)))

#define return_ptr(p)	return no_free_ptr(p)
```

```c
/* include/linux/slab.h:512 */
DEFINE_FREE(kfree, void *, if (!IS_ERR_OR_NULL(_T)) kfree(_T))
/* include/linux/mutex.h:228 */
DEFINE_GUARD(mutex, struct mutex *, mutex_lock(_T), mutex_unlock(_T))
```

kernfs 有一個把 RAII 用得很漂亮的例子：

```c
/* fs/kernfs/inode.c:27（v6.18，節錄） */
static struct kernfs_iattrs *__kernfs_iattrs(struct kernfs_node *kn, bool alloc)
{
	struct kernfs_iattrs *ret __free(kfree) = NULL;
	struct kernfs_iattrs *attr;

	attr = READ_ONCE(kn->iattr);
	if (attr || !alloc)
		return attr;

	ret = kmem_cache_zalloc(kernfs_iattrs_cache, GFP_KERNEL);
	if (!ret)
		return NULL;
	...
	/* If someone raced us, recognize it. */
	if (!try_cmpxchg(&kn->iattr, &attr, ret))
		return READ_ONCE(kn->iattr);

	return no_free_ptr(ret);
}
```

三個 `return` 各有不同的釋放語意，但程式碼裡沒有任何 `kfree`：前兩個 return 時 `ret` 是 `NULL` 或配置失敗，cleanup 什麼都不做；**搶輸 race** 時 `return READ_ONCE(kn->iattr)`，`ret` 離開 scope 被自動釋放；搶贏時 `no_free_ptr(ret)` 把 `ret` 清成 `NULL` 再回傳原值，所有權移交給 `kn->iattr`。（注意這段用 `kmem_cache_zalloc` 配置、卻用 `__free(kfree)` 釋放，這在 slab 的實作下是允許的，`kfree` 能釋放任何 slab 物件。）

在 user space 重現這三個語意，並故意製造忘記 `no_free_ptr` 的錯誤：

```c
/* raii.c — 用 __attribute__((cleanup)) 重現 kernel cleanup.h 的 __free / guard / no_free_ptr */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>

#define __cleanup(fn) __attribute__((cleanup(fn)))

/* DEFINE_FREE(kfree, void *, if (_T) free(_T)) 的簡化版 */
static void __free_kfree(void *p) { void *v = *(void **)p; if (v) { printf("  [auto free %p]\n", v); free(v); } }
#define __free(name) __cleanup(__free_##name)
#define no_free_ptr(p) ({ __typeof__(p) __t = (p); (p) = NULL; __t; })

/* DEFINE_GUARD(mutex, ...) 的簡化版 */
typedef pthread_mutex_t *guard_mutex_t;
static void guard_mutex_unlock(guard_mutex_t *m) { pthread_mutex_unlock(*m); printf("  [auto unlock]\n"); }
#define guard_mutex(m) guard_mutex_t __g __cleanup(guard_mutex_unlock) = (pthread_mutex_lock(m), (m))

static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;

static char *make_greeting(const char *who, int fail)
{
    guard_mutex(&lock);                                 /* 離開 scope 自動 unlock */
    char *buf __free(kfree) = malloc(32);               /* 離開 scope 自動 free */
    if (!buf)
        return NULL;
    if (fail) {
        printf("  error path: early return\n");
        return NULL;                                    /* 不用 goto out_free / out_unlock */
    }
    snprintf(buf, 32, "hello, %s", who);
    return no_free_ptr(buf);                            /* 所有權移交給呼叫者 */
}

static char *make_greeting_buggy(const char *who)
{
    char *buf __free(kfree) = malloc(32);
    snprintf(buf, 32, "hello, %s", who);
    return buf;                                         /* 忘了 no_free_ptr！ */
}

int main(int argc, char **argv)
{
    (void)argv;
    printf("success path:\n");
    char *s = make_greeting("kernel", 0);
    printf("  got \"%s\"\n", s);
    free(s);

    printf("failure path:\n");
    printf("  got %p\n", (void *)make_greeting("kernel", 1));

    if (argc > 1) {
        printf("buggy path:\n");
        char *t = make_greeting_buggy("uaf");
        printf("  got \"%s\"\n", t);                    /* use-after-free */
    }
    return 0;
}
```

```
$ gcc -Wall -Wextra -O0 -g -pthread -o raii raii.c && ./raii
success path:
  [auto unlock]
  got "hello, kernel"
failure path:
  error path: early return
  [auto free 0x5896af2932b0]
  [auto unlock]
  got (nil)
$ gcc -Wall -O0 -g -pthread -fsanitize=address -o raii_asan raii.c && ./raii_asan bug
...
==324628==ERROR: AddressSanitizer: heap-use-after-free on address 0x5030000000a0 ...
    #3 0x56653c56b8a1 in main /home/ypp/code_reading/scratch/41-45/raii.c:55
...
freed by thread T0 here:
    #0 0x7c12894b4537 in __interceptor_free ...
    #1 0x56653c56b392 in __free_kfree /home/ypp/code_reading/scratch/41-45/raii.c:10
    #2 0x56653c56b769 in make_greeting_buggy /home/ypp/code_reading/scratch/41-45/raii.c:37
```

三件事從輸出可以確認：成功路徑沒有 `[auto free]`（`no_free_ptr` 把指標清掉了）；失敗路徑的釋放順序是**宣告的反序**——先 free 後 unlock，和手寫 goto 階梯的順序一致；忘記 `no_free_ptr` 時，編譯器沒有任何警告，ASan 才抓到 use-after-free。kernel 版的 `no_free_ptr` 用 `__must_check_fn` 包住，至少能在「呼叫了 `no_free_ptr` 卻丟掉回傳值」時警告，但擋不住「根本忘了呼叫」。

**預測清單**：看到 `__free(x)` 就知道這個函式裡不會有對應的手動釋放與 goto 標籤；看到 `guard(mutex)(&m)` 就知道鎖一直持有到 scope 結束（包括所有 early return）；看到 `no_free_ptr`／`return_ptr` 就是所有權轉移點，**只要檢查這些點**是否正確。

## 9. 引用計數

**Beacon**：`kref`、`refcount_t`、成對的 `*_get()`／`*_put()`，以及「最後一個 put 會呼叫 release callback」的結構。

這是 kernel 管理共享物件生命週期的唯一通用答案，[Ch 20](./20-refcount-and-lifetime.md) 已完整介紹，Ch 42 的 kobject 是它的集大成。Ch 43 的 hwrng core 也有一個小例子：`struct hwrng` 帶著 `struct kref ref`，refcount 歸零時的 `cleanup_rng`（`drivers/char/hw_random/core.c:67`）用 `container_of(kref, struct hwrng, ref)` 找回物件，再呼叫 driver 提供的 `rng->cleanup`。

**預測清單**：每一個 `get` 都有一個 `put`，可能在不同函式甚至不同執行脈絡；物件**不會**被任何人直接 `kfree`，只會在 release callback 裡釋放；release callback 一定用 `container_of` 從 kref 找回外層物件。讀碼時要檢查的只有「get/put 是否在每條路徑上都配對」。

## 10. Null object：什麼都不做的實作

**Beacon**：名字是 `noop_*`、`nop_*`、`*_no_*`、`dummy_*` 的函式或物件，被填進 ops 表。

```c
/* fs/read_write.c:324 */ loff_t noop_llseek(struct file *file, loff_t offset, int whence)
/* fs/libfs.c:1623 */     int noop_fsync(struct file *file, loff_t start, loff_t end, int datasync)
/* fs/libfs.c:224 */      ssize_t generic_read_dir(...) { return -EISDIR; }
/* fs/mnt_idmapping.c:31 */ struct mnt_idmap nop_mnt_idmap = { ... };
```

null object 讓呼叫端**不需要檢查 `NULL`**：ramfs 的 `fsync` 填 `noop_fsync`，`fsync(2)` 的路徑就不用特別判斷「這個檔案系統需不需要 sync」。Ch 41 的 `generic_read_dir` 是一個變體——不是「什麼都不做」而是「永遠回固定錯誤」，`inet_stream_ops` 的 `.socketpair = sock_no_socketpair`（`net/ipv4/af_inet.c:1060`）也是。`nop_mnt_idmap` 則是資料版本的 null object：代表「沒有 ID 映射」的物件，Ch 41 的 ramfs 程式碼裡 `inode_init_owner(&nop_mnt_idmap, ...)` 就傳了它。

這個模式和 template method 的「`NULL` = 用預設」是兩種不同的選擇：`NULL` 讓通用層決定預設行為，null object 讓實作者明確宣告「我刻意什麼都不做」。讀碼時看到 null object，可以確定作者是有意的；看到 `NULL`，要去查通用層的預設是什麼。

**預測清單**：null object 函式體一行，回 0 或固定錯誤碼；它不需要讀。

## 一張表：beacon → 模式 → 可以跳過什麼

| 看到這個 | 是什麼模式 | 預測 | 可以跳過 |
|---|---|---|---|
| `static const struct xxx_ops foo = { .a = ..., }` | strategy | 有註冊點、有必填檢查、呼叫端查 `NULL` | 同介面的其他實作 |
| `struct notifier_block`、`register_*_notifier` | observer | 過濾 → `switch (event)` → `NOTIFY_*` | `notifier_call_chain` 本身 |
| `if (ops->x) ops->x(...); else generic_x(...)` | template method | 多數實作留空 | 沒覆寫的實作 |
| `alloc_*(..., setup)`、`pf->create`、查表後 `->ops =` | factory | 表 → ops + 大小 → 一次配置 | 工廠內部的查表 |
| `for_each_*`、`seq_operations` | iterator | start 拿鎖、stop 放鎖、next 遞增 pos | 走訪機制 |
| 短函式重新包裝參數 | adapter | 沒有業務邏輯 | 整個 adapter |
| 全域串列 + register/find | registry | 重名回 `-EBUSY`、可能自動載入模組 | register/unregister 的實作 |
| `__free`、`guard`、`CLASS` | RAII | 沒有手動釋放與 goto | 錯誤路徑的清理 |
| `kref`、`*_get`/`*_put` | 引用計數 | release 用 `container_of` | 只檢查配對 |
| `noop_*`、`*_no_*` | null object | 一行，回 0 或錯誤碼 | 整個函式 |

## 對比與取捨

| 面向 | GoF（C++／Java） | kernel（C） |
|---|---|---|
| 實現機制 | 繼承、介面、虛擬函式 | ops table、函式指標、`container_of` |
| 模式的「名字」 | 出現在類別名稱裡（`FooFactory`、`BarObserver`） | 幾乎不出現；靠命名慣例（`*_ops`、`register_*`、`noop_*`）與結構辨識 |
| 型別安全 | 編譯器檢查覆寫簽名 | 指定初始化器檢查簽名，但 `void *` 參數（notifier 的 `data`）完全不檢查 |
| 可選方法 | 預設實作或抽象方法 | `NULL` 或 null object 二選一 |
| GoF 沒收錄但 kernel 常見 | — | RAII（cleanup.h）、引用計數、registry、per-CPU 化 |

一個常見誤解是「kernel 不是物件導向，所以不用設計模式」。剛好相反：正因為 C 沒有語言層級的支援，kernel 的模式必須寫得非常一致，否則幾千個開發者無法協作。這種一致性對讀者是好消息：**模式在 kernel 裡比在多數 C++ 專案裡更可預測**。

## 踩雷集錦

1. **認出模式就停止思考**：模式是預測，不是保證。`struct socket` 的 `ops` 被註解為 `Might change with IPV6_ADDRFORM or MPTCP`——這個 strategy 的實作可以在物件生命期間被換掉，和「工廠設定後不變」的預測不同。認出模式後，要特別留意這種註解。
2. **把 notifier 的 handler 當成在自己的脈絡裡跑**：handler 跑在**發布者**的脈絡。atomic notifier chain 的 handler 裡不能睡；讀 handler 前先確認是哪一種 chain。
3. **以為 `__free` 會在 `return` 之後才清理回傳值**：cleanup 在 return 值被計算**之後**、函式真正返回之前執行。`return buf;` 回傳的是一個即將被釋放的指標，`raii.c` 的 buggy 版本就是這樣產生 use-after-free 的。必須用 `no_free_ptr()`／`return_ptr()`。
4. **把 `NULL` ops 和 null object 當成同一件事**：`NULL` 代表「交給通用層決定」，null object 代表「我明確要什麼都不做」。對 `fsync` 而言，`NULL` 會讓 `vfs_fsync_range` 回 `-EINVAL`，`noop_fsync` 則回 0，user space 看到的結果不同。
5. **硬套 GoF 名稱**：不是每個函式指標都是 strategy，不是每個全域變數都是 singleton。命名的目的是**預測**；如果套上名字之後預測不出任何東西，這個名字就沒有用。

## 進階：再往深一層

- **per-CPU 化**：把一個全域計數器或快取拆成每顆 CPU 一份（`DEFINE_PER_CPU`、`this_cpu_inc`），讀時加總。這是 kernel 特有、GoF 沒有的模式，Ch 27 有介紹。看到 `percpu` 就預測：寫入不需要鎖、讀取要走訪所有 CPU、讀到的值可能是近似值。
- **RCU 的 publish/subscribe**：`rcu_assign_pointer`／`rcu_dereference` 配對加上 grace period，是「無鎖讀取的 observer」的變體（Ch 31）。registry 若用 RCU 保護（`tcp_cong_list` 就是 `list_add_tail_rcu`），查找端不拿鎖。
- **pattern mining 的研究**：軟體工程研究裡有一支專門自動從程式碼中挖掘慣用法與 API 使用規則，例如 Engler 等人的 "Bugs as Deviant Behavior"（SOSP 2001）以 Linux 與 OpenBSD 為對象，統計推斷「這個鎖通常和那個解鎖配對」這類規則，再把偏離規則的地方當成可能的 bug。這和本章的思路一致：先認出模式，再找偏離模式的地方——讀碼與找 bug 用的是同一種能力。
- **練習 G**（[practice-g-ops-callback-finder.md](./practice-g-ops-callback-finder.md)）會把 strategy 模式的辨識寫成自動化工具。

## 動手練習

1. **模式獵人**：隨機打開 `drivers/` 底下一個 300 行左右的 driver，列出你在裡面找到的所有模式（至少三種），每一個標出 `file:line` 與它的 beacon。
2. **預測後驗證**：在 `net/` 找一個你沒讀過的 `register_netdevice_notifier` 呼叫者，**先**依本章的預測清單寫下它的 handler 應該長什麼樣，**再**打開驗證。記下預測失敗的地方，那就是這個 handler 真正特別之處。
3. **找 registry**：用 `rg -n '^int register_\w+\(' include/ kernel/ fs/ net/core/ | head -30` 找出其他 registry，挑兩個比較它們的鎖與查找方式（串列、hash table、xarray）。
4. **用 cleanup.h 改寫**：在 v6.18 找一個仍使用 goto 錯誤階梯、且只配置一兩個資源的短函式，在紙上用 `__free` 與 `no_free_ptr` 改寫，比較兩版的行數與可能的錯誤點。（`git log --oneline --grep='cleanup.h\|__free' | head` 可以找到 kernel 裡做這種轉換的真實 patch，Ch 57 會教你讀它們。）
5. **null object 盤點**：`rg -n '^(int|loff_t|ssize_t|long) noop_' fs/` 列出 VFS 提供的所有 null object，並為每一個找一個使用它的檔案系統。

## 本章重點整理

- 設計模式是架構尺度的 programming plan；認出 beacon → 叫出模式 → 預測其餘部分 → 只讀和預測不同的地方。
- kernel 最常見的十種：strategy（ops table）、observer（notifier）、template method（hook + `generic_*`）、factory（查表 + 配置 + 設 ops）、iterator（`for_each`、seq_file）、adapter（`new_sync_read`、`single_open`）、registry（`file_systems`）、RAII（cleanup.h，v6.5 起）、引用計數（kref）、null object（`noop_*`）。
- kernel 的模式不寫在名字裡，靠命名慣例與結構辨識；但因為 C 沒有語言支援，模式反而寫得極為一致、可預測。
- 模式是預測不是保證：特別留意和預測不符的註解與例外。

## 自我檢核

- [ ] 不看筆記，說出十種模式各自的 beacon，並各舉一個 v6.18 的例子（說得出檔案就好，行號不用背）。
- [ ] 如果有人問「`seq_operations` 的 `start` 為什麼要拿鎖、`stop` 為什麼要放鎖，而不是在 `open`／`release` 裡做？」，你能從 `seq_read_iter` 的行為解釋嗎？
- [ ] 解釋 `fs/kernfs/inode.c` 的 `__kernfs_iattrs` 在搶輸 race 時，配置的記憶體是在哪一行、被誰釋放的。
- [ ] 舉出一個「認出模式之後，預測失敗」的例子，並說明你從失敗中學到這段程式碼的什麼特點。
- [ ] 想一個你熟悉的非 kernel 專案（Redis、nginx、你自己的工作專案），在裡面找出至少三種本章的模式。

## 延伸閱讀

### 書籍

- **《Design Patterns: Elements of Reusable Object-Oriented Software》** — Gamma, Helm, Johnson, Vlissides（Addison-Wesley, 1994）
  - **這本書的定位**：設計模式的原始目錄。例子是 C++ 與 Smalltalk，但每個模式的 Intent、Motivation、Structure、Consequences 四段完全可以搬到 kernel 的語境。
  - **讀哪幾章**：Ch 1 的導論（為什麼要命名模式），以及 Strategy、Observer、Template Method、Factory Method、Iterator、Adapter 六個模式的條目。其餘模式在 kernel 裡較少見，可以之後再讀。
- **《Code Reading: The Open Source Perspective》** — Diomidis Spinellis（Addison-Wesley, 2003）
  - **讀哪幾章**：Ch 9.4「Architecture Reuse」，原書討論框架、設計模式與領域特定架構如何幫助讀者辨識已知結構；本章的「beacon → 預測 → 跳讀」流程是把這一節與 Ch 2 的認知模型接起來。

### 部落格 / 技術文章

- **[Object-oriented design patterns in the kernel, part 1](https://lwn.net/Articles/444910/)** 與 **[part 2](https://lwn.net/Articles/446317/)** — Neil Brown（LWN.net，2011）
  - **這篇說什麼**：從 kernel 開發者的角度整理 vtable、`NULL` 方法與預設行為、資料繼承等模式，並討論每一種做法的取捨（例如「`NULL` 代表預設」與「明確提供預設函式」各自的優缺點，正好對應本章第 3 與第 10 節）。
  - **為什麼值得讀**：作者是長期的 kernel 維護者，觀點來自實際 review 經驗；2011 年的函式名稱有些已變，模式不變。

### 論文

- **Empirical Studies of Programming Knowledge** — E. Soloway, K. Ehrlich，IEEE Transactions on Software Engineering（1984）
  - **核心貢獻**：以實驗證明熟手程式設計師的知識組織成 programming plans 與 rules of discourse，違反這些慣例的程式碼會讓熟手的理解表現大幅下降。
  - **和本章的關聯**：設計模式就是更大尺度的 plan；本章「預測失敗的地方就是重點」的讀法，直接來自這篇的發現。Ch 2 有更完整的介紹。
- **Bugs as Deviant Behavior: A General Approach to Inferring Errors in Systems Code** — D. Engler, D. Y. Chen, S. Hallem, A. Chou, B. Chelf，SOSP（2001）
  - **核心貢獻**：從系統程式碼中統計推斷「程式設計師相信的規則」（例如某個函式之後通常要呼叫另一個），再把偏離規則的位置回報為可能的 bug，在 Linux 與 OpenBSD 上找到數百個錯誤。
  - **讀哪裡**：Section 1–3 介紹方法；之後的檢查器細節可以略讀。
  - **和本章的關聯**：本章教你用模式預測程式碼；這篇示範了同一種能力的自動化版本，也說明了為什麼「和模式不一樣的地方」值得特別注意。

### 官方文件

- **[Scope-based Cleanup Helpers](https://docs.kernel.org/core-api/cleanup.html)**（tree 內：`Documentation/core-api/cleanup.rst`）
  - **讀哪裡**：整篇，特別是說明 `no_free_ptr`、`guard` 與宣告順序的段落；本章第 8 節的權威出處。
- **[The seq_file Interface](https://docs.kernel.org/filesystems/seq_file.html)**（tree 內：`Documentation/filesystems/seq_file.rst`）
  - **讀哪裡**：iterator 介面（start/next/stop/show）與 `single_open` 兩節，對應本章第 5、6 節。

到這裡，我們有了一套辨識架構與模式的詞彙。但詞彙只能告訴你「這段程式碼是什麼形狀」，不能告訴你「它在處理什麼問題」——page cache 為什麼要有 readahead、TCP 為什麼需要 TIME_WAIT。下一章處理最後一道、也是最花時間的鴻溝：怎麼有效率地補領域知識。

→ [Ch 46 快速補領域知識：LWN、Documentation、書與論文](./46-acquiring-domain-knowledge.md)
