# Ch 56 冷讀實戰三：網路收包路徑的一段

> **目標**：冷讀一段跨越好幾個非同步邊界的路徑：一個封包從 e1000 網卡的中斷進來，經過 NAPI、GRO，到 `__netif_receive_skb_core()` 分派給 IP 層為止。先靜態讀出一個假說，再用 QEMU guest 裡的 ftrace 驗證，找出靜態閱讀**錯在哪裡**，最後用 git 考古找到「為什麼會錯」的那個 commit。
> **鴻溝**：本章主要練**間接鴻溝**（NAPI poll、`packet_type->func` 兩層函式指標）與**領域鴻溝**（NAPI、GRO、`sk_buff`、`packet_type`），順帶練**慣用法鴻溝**（`pt_prev` 延遲投遞）與**脈絡鴻溝**（listified RX 的歷史）。

> **環境**：Linux v6.18（`~/code_reading/build`，x86_64_defconfig + kvm_guest.config + ftrace，`CONFIG_E1000=y`），QEMU 6.2 TCG 模式（WSL 沒有可用的 KVM），guest 只有 busybox。QEMU 沒指定網卡時，x86 預設給一張 e1000 接 user-mode 網路（guest 10.0.2.15、gateway 10.0.2.2）。guest 腳本用 [Ch 51](./51-dynamic-tracing.md) 的 `~/code_reading/qemu/run-script.sh` 執行，放在 `~/code_reading/scratch/56/`。本章所有 trace 輸出都是實際跑出來的；TCG 模式下的時間數字只能看相對大小，不代表真實硬體的效能。

## 為什麼需要這個？

[Ch 54](./54-cold-read-random-function.md) 的函式 25 行，一次讀完；[Ch 55](./55-cold-read-driver-probe.md) 的 probe 一百多行，但都在同一個檔案、同一個執行脈絡裡。網路收包完全不一樣：

- **它跨越四種執行脈絡**：硬體中斷（hardirq）→ softirq → NAPI poll → 協定層。每跨一次，靜態的呼叫鏈就斷一次。
- **它有兩層函式指標**：driver 的 poll 函式是 `napi_struct->poll`，協定層的入口是 `packet_type->func`。[Ch 28](./28-ops-tables-indirect-calls.md) 的方法要用兩次。
- **它為了效能做了批次處理**：封包不一定是「收到一個、往上送一個」。這是本章的重點，因為**批次處理會讓靜態閱讀得出錯誤的結論**。
- **它的領域知識很密**：`sk_buff`、NAPI、GRO、`packet_type`、tap、`rx_handler`，每一個都是一個小主題。

[Ch 32](./32-async-irq-softirq-workqueue.md) 說過 NAPI 是 softirq 最重要的使用者，並預告本章會走一次。本章不追完整條路徑（那是一本書的份量），只冷讀**中間一段**：從 driver 的中斷 handler 開始，到 IP 層的入口為止。

## 先建立直覺：郵件分揀中心

```
   卡車到站（中斷）        卸貨（NAPI poll）      併箱（GRO）         分揀（core）          各部門
  ┌────────────┐      ┌──────────────┐     ┌────────────┐    ┌──────────────┐    ┌──────────┐
  │ e1000_intr │ ───► │ e1000_clean  │ ──► │ 同一批的小  │──► │ __netif_     │──► │ ip_rcv   │
  │ 只按門鈴   │ 叫醒 │ 從 RX ring   │     │ 包裹合成大  │    │ receive_skb_ │    │ arp_rcv  │
  │ 關掉門鈴   │ soft │ 一個個拿出來 │     │ 箱；合不了  │    │ core         │    │ ipv6_rcv │
  └────────────┘ irq  └──────────────┘     │ 的先堆著    │    │ 看 protocol  │    └──────────┘
                                           └────────────┘    │ 欄位決定送誰  │    ┌──────────┐
                                                             │ 抄送稽核室   │──► │ packet_rcv│
                                                             └──────────────┘    │ (tcpdump) │
                                                                                 └──────────┘
```

- **按門鈴**：中斷 handler 什麼都不搬，只關掉中斷、排一個 NAPI poll，然後返回。
- **卸貨**：softirq 裡的 poll 函式一次最多搬 `budget` 個封包，搬完重新打開中斷。
- **併箱**：GRO 嘗試把同一條 TCP 連線的連續封包合成一個大封包，減少往上送的次數。**合不了的也不馬上送，先堆在一個 list 上。**
- **分揀**：core 看封包的 protocol 欄位，交給對應的 `packet_type` handler；有人在「抄送名單」上（例如 tcpdump 開的 `AF_PACKET` socket），就先抄送一份。

比喻的限制：郵件中心的「堆著」是為了等卡車滿載，網路的「堆著」是為了**批次往上送**，讓協定層一次處理一串封包、少付幾次函式呼叫和 cache miss 的成本。堆到什麼時候送？這個問題的答案，靜態閱讀很容易讀錯。

## 範圍與問題

**入口**：`e1000_intr()`（`drivers/net/ethernet/intel/e1000/e1000_main.c:3748`）
**出口**：IP 層的 `ip_rcv()` 或 `ip_list_rcv()`（`net/ipv4/af_inet.c:1879–1883` 註冊的 `ip_packet_type`）
**重點段**：`__netif_receive_skb_core()`（`net/core/dev.c:5849–6069`，約 220 行）

**問題**：一個 ICMP echo reply 從 e1000 進來，**在哪一個函式的哪一行**被交給 IP 層？中間在哪裡等過？

## 靜態閱讀：先讀出一個假說

### 第一段：中斷 → softirq

`e1000_intr()`（`e1000_main.c:3748–3791`）的關鍵幾行：

```c
	u32 icr = er32(ICR);                 /* 讀中斷原因暫存器（讀了就清掉） */

	if (unlikely((!icr)))
		return IRQ_NONE;  /* Not our interrupt */
	...
	/* disable interrupts, without the synchronize_irq bit */
	ew32(IMC, ~0);                       /* 關掉網卡的所有中斷 */
	E1000_WRITE_FLUSH();

	if (likely(napi_schedule_prep(&adapter->napi))) {
		...
		__napi_schedule(&adapter->napi);   /* 排一個 NAPI poll，觸發 NET_RX_SOFTIRQ */
	}
```

這是標準的 NAPI 中斷 handler：關中斷、排 poll、返回。一個封包都沒搬。`__napi_schedule()` 把 `napi_struct` 掛到這個 CPU 的 `softnet_data.poll_list`，並 raise `NET_RX_SOFTIRQ`。

### 第二段：softirq → driver 的 poll（間接鴻溝第一層）

`NET_RX_SOFTIRQ` 的 handler 是 `net_rx_action()`（`net/core/dev.c:7745`）。它的骨架：

```c
	unsigned long time_limit = jiffies +
		usecs_to_jiffies(READ_ONCE(net_hotdata.netdev_budget_usecs));
	int budget = READ_ONCE(net_hotdata.netdev_budget);
	...
	for (;;) {
		...
		n = list_first_entry(&list, struct napi_struct, poll_list);
		budget -= napi_poll(n, &repoll);          /* :7784 */
		if (unlikely(budget <= 0 ||
			     time_after_eq(jiffies, time_limit))) {
			WRITE_ONCE(sd->time_squeeze, sd->time_squeeze + 1);
			break;
		}
	}
```

兩個上限：封包數 `netdev_budget` 和時間 `netdev_budget_usecs`，任一個用完就讓出 CPU（`time_squeeze` 計數會出現在 `/proc/net/softnet_stat`）。`napi_poll()` → `__napi_poll()`（`:7580`）裡呼叫 `n->poll(n, weight)`，**函式指標**。

誰是 `n->poll`？用 [Ch 28](./28-ops-tables-indirect-calls.md) 的方法找賦值點：

```
$ rg -n 'netif_napi_add' drivers/net/ethernet/intel/e1000/e1000_main.c
1009:	netif_napi_add(netdev, &adapter->napi, e1000_clean);
```

`e1000_clean()`（`:3798`）裡又有一層：`adapter->clean_rx(adapter, &adapter->rx_ring[0], &work_done, budget)`。再找賦值點：

```
$ rg -n 'adapter->clean_rx = ' drivers/net/ethernet/intel/e1000/e1000_main.c
1862:		adapter->clean_rx = e1000_clean_jumbo_rx_irq;
1867:		adapter->clean_rx = e1000_clean_rx_irq;
```

MTU 大於標準值才用 jumbo 版本，我們的 ping 走 `e1000_clean_rx_irq()`（`:4350`）。

### 第三段：driver → GRO

`e1000_clean_rx_irq()` 對每個 RX descriptor 做完長度調整、checksum 之後，呼叫 `e1000_receive_skb()`（`:4464`）：

```c
static void e1000_receive_skb(struct e1000_adapter *adapter, u8 status,
			      __le16 vlan, struct sk_buff *skb)
{
	skb->protocol = eth_type_trans(skb, adapter->netdev);
	...
	napi_gro_receive(&adapter->napi, skb);
}
```

`eth_type_trans()` 讀 Ethernet header 的 type 欄位，填進 `skb->protocol`，這是等一下 core 分揀的依據。`napi_gro_receive()` 是 `include/linux/netdevice.h:4190` 的 static inline，直接轉呼叫 `gro_receive_skb()`（`net/core/gro.c:624`）：

```c
	ret = gro_skb_finish(gro, skb, dev_gro_receive(gro, skb));
```

`dev_gro_receive()` 試著把封包併進已有的 flow。ICMP 沒有 GRO handler 可以合併，結果是 `GRO_NORMAL`。`gro_skb_finish()`（`gro.c:598`）對 `GRO_NORMAL` 的處理：

```c
	case GRO_NORMAL:
		gro_normal_one(gro, skb, 1);
		break;
```

`gro_normal_one()`（`include/net/gro.h:538–545`）：

```c
static inline void gro_normal_one(struct gro_node *gro, struct sk_buff *skb,
				  int segs)
{
	list_add_tail(&skb->list, &gro->rx_list);
	gro->rx_count += segs;
	if (gro->rx_count >= READ_ONCE(net_hotdata.gro_normal_batch))
		gro_normal_list(gro);
}
```

**這裡是關鍵。** 封包被加進 `rx_list`，只有累積到 `gro_normal_batch` 個才呼叫 `gro_normal_list()` 往上送。`gro_normal_batch` 的預設值在 `net/core/hotdata.c:12`：

```c
	.gro_normal_batch = 8,
```

一個 ping 只有一個封包，`rx_count` 是 1，小於 8，**不會在這裡往上送**。那它什麼時候送？

靜態閱讀到這裡，大多數人（包括第一次讀的我們）會先假設「反正 poll 結束前總會送」，然後繼續往下讀 core，把這個問題放進「未解問題」清單。這是合理的選擇性閱讀（[Ch 4](./04-selective-reading.md)），但要記得回來收。

### 第四段：重點段 `__netif_receive_skb_core()`

`gro_normal_list()` 呼叫 `netif_receive_skb_list_internal()`，一路到 `__netif_receive_skb_list_core()`（`net/core/dev.c:6131`），它對 list 上的每個 skb 呼叫 `__netif_receive_skb_core()`。

這個函式 220 行，有五個 label、六個 `goto another_round`。先只看 label 和主要區塊，畫出骨架：

```
__netif_receive_skb_core(&skb, pfmemalloc, &pt_prev)        net/core/dev.c:5849
│ orig_dev = skb->dev; 重設 network/transport header
│
├─ another_round:                                           :5880
│    generic XDP（有掛才跑）            → 不是 PASS 就 out
│    VLAN tag 還在封包裡 → skb_vlan_untag
│    pfmemalloc → 跳過 taps
│    ┌ taps：ptype_all（netns 的、device 的）  :5911、:5918
│    │   每個 tap 都可能拿到一份（tcpdump 在這裡）
├─ skip_taps:                                               :5924
│    tc ingress / netfilter ingress（有啟用才跑）  → 可能 another_round
├─ skip_classify:                                           :5943
│    VLAN 處理（vlan_do_receive）       → 找到 VLAN device 就 another_round
│    rx_handler（bridge／bond／macvlan…） :5960
│       CONSUMED → out；ANOTHER → another_round；EXACT／PASS → 繼續
│    type = skb->protocol                                   :6019
│    ┌ 協定分派：ptype_base[hash]、netns 的 ptype_specific、
│    │          orig_dev 的 ptype_specific、skb->dev 的 ptype_specific
│    │                                                      :6023–6042
│    if (pt_prev) *ppt_prev = pt_prev;   ← 不在這裡呼叫最後一個 handler！
│    else drop:                                             :6047
├─ out:                                                     :6060
│    *pskb = skb; return ret;
```

`another_round` 是一個用 `goto` 寫的迴圈：每當封包「換了一個 device」（剝掉 VLAN tag 後交給 VLAN device、bridge 的 `rx_handler` 把它轉給 bridge device、tc 把它 redirect），就從頭再跑一次分揀，因為新的 device 可能有自己的 taps 和 handler。

### 慣用法鴻溝：`pt_prev` 延遲投遞

整個函式最難讀的是 `pt_prev` 這個變數。看 taps 那段（`:5911–5922`）：

```c
	list_for_each_entry_rcu(ptype, &dev_net_rcu(skb->dev)->ptype_all,
				list) {
		if (pt_prev)
			ret = deliver_skb(skb, pt_prev, orig_dev);
		pt_prev = ptype;
	}
```

它不是「找到一個就送一個」，而是「**找到下一個時，才把上一個送出去**」。迴圈結束時，最後一個符合的 handler 還留在 `pt_prev` 裡，沒有被呼叫。

為什麼要這樣寫？看 `deliver_skb()`（`net/core/dev.c:2465–2473`）：

```c
static inline int deliver_skb(struct sk_buff *skb,
			      struct packet_type *pt_prev,
			      struct net_device *orig_dev)
{
	if (unlikely(skb_orphan_frags_rx(skb, GFP_ATOMIC)))
		return -ENOMEM;
	refcount_inc(&skb->users);
	return pt_prev->func(skb, skb->dev, pt_prev, orig_dev);
}
```

每送一次就 `refcount_inc(&skb->users)`：handler 拿到的是一個「多一個 reference」的 skb，用完自己 free，原本的 reference 還留在 core 手上。如果有 N 個 handler，前 N−1 個都這樣送；**最後一個直接把 core 手上那個 reference 交出去**，省一次 inc 和一次 dec。對每秒數百萬個封包的路徑，這個省法是值得的。

而且最後一個不在 core 裡呼叫，是透過 `*ppt_prev` 交回呼叫者。呼叫者有兩種：

- 單一封包版 `__netif_receive_skb_one_core()`（`:6071`）：拿到 `pt_prev` 就立刻 `INDIRECT_CALL_INET(pt_prev->func, ipv6_rcv, ip_rcv, ...)`（`:6079`）。
- list 版 `__netif_receive_skb_list_core()`（`:6131`）：把 `pt_prev` 相同的連續封包收成一個 sublist，**整串交給** `pt_prev->list_func`（IP 的是 `ip_list_rcv`）。

所以 `pt_prev` 延遲投遞有兩個目的：省 refcount，以及讓 list 版可以把「最後一個 handler」批次化。`deliver_ptype_list_skb()`（`:2475–2491`）是同一個 pattern 加上 type 過濾的版本。

`INDIRECT_CALL_INET` 是另一個小慣用法：它展開成「如果 `func == ip_rcv` 就直接呼叫 `ip_rcv`，否則才走函式指標」，避開 retpoline 的成本（[Ch 28](./28-ops-tables-indirect-calls.md)）。冷讀時看到 `INDIRECT_CALL_*`，可以直接讀成「呼叫這個函式指標，最可能是後面列的那幾個」。

### 間接鴻溝第二層：`ip_rcv` 是怎麼掛上去的

`ptype_base` 和 `ptype_specific` 裡的東西從哪來？找 `dev_add_pack` 的呼叫：

```c
/* net/ipv4/af_inet.c:1879–1883 */
static struct packet_type ip_packet_type __read_mostly = {
	.type = cpu_to_be16(ETH_P_IP),
	.func = ip_rcv,
	.list_func = ip_list_rcv,
};
```

`inet_init()` 在第 2007 行 `dev_add_pack(&ip_packet_type)`。`ptype_head()`（`net/core/dev.c:575–590`）決定它放哪條 list：type 不是 `ETH_P_ALL`、沒綁 device、沒綁 netns，所以放進 `ptype_base[ntohs(ETH_P_IP) & PTYPE_HASH_MASK]`，一個 16 格的 hash table。`ETH_P_ALL` 的（例如 `AF_PACKET` socket）放進 `ptype_all`，也就是 taps。

### 靜態閱讀的結論（假說）

```
e1000_intr → __napi_schedule → [NET_RX_SOFTIRQ] → net_rx_action → __napi_poll
  → e1000_clean → e1000_clean_rx_irq → e1000_receive_skb → napi_gro_receive
  → gro_receive_skb → gro_normal_one → （湊滿 8 個才）gro_normal_list
  → netif_receive_skb_list_internal → __netif_receive_skb_list_core
  → __netif_receive_skb_core（決定 pt_prev = ip_packet_type）
  → ip_list_rcv
未解：只有 1 個封包時，gro_normal_list 由誰呼叫？
```

## 動態驗證：讓 kernel 自己說

### 實驗一：誰呼叫了 `ip_list_rcv`

guest 腳本 `g1.sh`：設定網路、先 ping 一次讓 ARP 表有 gateway，然後用 function tracer 加 `func_stack_trace`，在 IP 層入口抓呼叫堆疊。filter 同時放 `ip_rcv`、`ip_list_rcv`、`arp_rcv`，因為還不確定會走單一版還是 list 版：

```sh
#!/bin/sh
B=/bin/busybox
$B ifconfig lo up
$B ifconfig eth0 10.0.2.15 netmask 255.255.255.0 up
$B route add default gw 10.0.2.2
$B ping -c 1 -W 2 10.0.2.2 > /dev/null 2>&1   # 先暖身：讓 ARP 表有 gateway
cd /sys/kernel/tracing
echo 0 > tracing_on
echo function > current_tracer
echo "ip_rcv ip_list_rcv arp_rcv" > set_ftrace_filter
echo 1 > options/func_stack_trace
echo > trace
echo 1 > tracing_on
$B ping -c 1 -W 2 10.0.2.2
echo 0 > tracing_on
echo 0 > options/func_stack_trace
echo "=== stack ==="
grep -v "^#" trace
```

實際輸出：

```
$ ~/code_reading/qemu/run-script.sh g1.sh
...
64 bytes from 10.0.2.2: seq=0 ttl=255 time=3.698 ms
...
=== stack ===
          <idle>-0       [000] ..s1.    11.411000: ip_list_rcv <-__netif_receive_skb_list_core
          <idle>-0       [000] ..s1.    11.412245: <stack trace>
 => [FTRACE TRAMPOLINE]
 => ip_list_rcv
 => __netif_receive_skb_list_core
 => netif_receive_skb_list_internal
 => napi_complete_done
 => e1000_clean
 => __napi_poll
 => net_rx_action
 => handle_softirqs
 => irq_exit_rcu
 => common_interrupt
 => asm_common_interrupt
 => pv_native_safe_halt
 ...
```

逐行和假說比對：

| 堆疊 | 假說 | 對嗎 |
|---|---|---|
| `common_interrupt` → `irq_exit_rcu` → `handle_softirqs` → `net_rx_action` | 中斷結束時跑 softirq | ✓（[Ch 32](./32-async-irq-softirq-workqueue.md)：hardirq 結束時順手跑 softirq） |
| `__napi_poll` → `e1000_clean` | `n->poll` 是 `e1000_clean` | ✓ |
| `e1000_clean` → **`napi_complete_done`** → `netif_receive_skb_list_internal` | `e1000_clean_rx_irq` → … → `gro_normal_list` | **✗** |
| `ip_list_rcv`，不是 `ip_rcv` | list 版 | ✓ |
| `<idle>-0`、`..s1.` | CPU 閒置時被中斷，在 softirq 脈絡（`s`） | ✓ |

封包**不是**在 `e1000_clean_rx_irq` 裡往上送的。它在 `e1000_clean_rx_irq` 返回之後，由 `e1000_clean` 呼叫 `napi_complete_done()` 時才送。這就是未解問題的答案。

### 實驗二：function_graph 看完整的時間順序

`g2.sh` 用 function_graph，從 `e1000_clean` 往下 4 層（只列關鍵部分）：

```
 0)               |  e1000_clean() {
 0)               |    e1000_unmap_and_free_tx_resource() {      ← 先清 TX（ping 的 request）
 ...
 0) ! 409.952 us  |    }
 0)               |    e1000_clean_rx_irq() {
 0)               |      napi_alloc_skb() {
 0) + 34.625 us   |        __alloc_skb();
 0) + 50.305 us   |      }
 0)   1.413 us    |        irq_enter_rcu();                      ← 中途被 timer 中斷打斷
 0) + 81.895 us   |        __sysvec_apic_timer_interrupt();
 0) + 32.922 us   |        irq_exit_rcu();
 0)   1.614 us    |      skb_put();
 0)   6.933 us    |      skb_trim();
 0) + 14.959 us   |      eth_type_trans();                      ← 填 skb->protocol
 0)               |      gro_receive_skb() {
 0) + 31.910 us   |        dev_gro_receive();
 0) + 46.007 us   |      }                                      ← 回來了，沒有往上送
 0) + 48.402 us   |      e1000_alloc_rx_buffers();
 0) ! 358.245 us  |    }
 0)               |    napi_complete_done() {
 0)               |      netif_receive_skb_list_internal() {     ← 在這裡才往上送
 0)   1.312 us    |        __rcu_read_lock();
 0) ! 601.824 us  |        __netif_receive_skb_list_core();
 0)   1.323 us    |        __rcu_read_unlock();
 0) ! 637.522 us  |      }
 0) ! 668.319 us  |    }
 0)   1.323 us    |    e1000_update_itr();
 0)   0.671 us    |    e1000_update_itr();
 0) # 1856.979 us |  }
```

`gro_receive_skb()` 只呼叫了 `dev_gro_receive()` 就返回，`gro_normal_one()` 把封包放進 list（它是 inline，所以 graph 裡看不到）。真正往上送是在 `napi_complete_done()` 裡。

另外幾個讀 trace 時要知道的事：

- **inline 的函式不會出現**。`napi_gro_receive`、`gro_normal_one`、`gro_normal_list`、`gro_flush_normal` 都是 static inline，在 graph 裡「消失」了；`e1000_receive_skb` 在這個 build 裡也被編譯器 inline 進 `e1000_clean_rx_irq`，所以 `eth_type_trans` 和 `gro_receive_skb` 直接掛在 `e1000_clean_rx_irq` 底下。
- **函式名可能被改**。再往下一層會看到 `__netif_receive_skb_core.constprop.0`，這是 GCC 常數傳播產生的特化版本（[Ch 50](./50-compiler-as-reading-tool.md)）。
- **想知道某個函式能不能 trace**，查 `available_filter_functions`。實驗二的腳本先跑了一次 grep，`gro_normal_list`、`napi_gro_receive`、`__netif_receive_skb_core` 都不在清單裡，`__netif_receive_skb_list_core`、`gro_receive_skb` 在。

### 回到原始碼：`napi_complete_done` 裡的那一行

知道答案在 `napi_complete_done()` 之後，回去讀它（`net/core/dev.c:6649`），找到：

```c
	/*
	 * When the NAPI instance uses a timeout and keeps postponing
	 * it, we need to bound somehow the time packets are kept in
	 * the GRO layer.
	 */
	gro_flush_normal(&n->gro, !!timeout);
```

`gro_flush_normal()`（`include/net/gro.h:529–533`）先 `gro_flush()` 把 GRO 裡還沒合併完的封包吐出來，再 `gro_normal_list()` 把 `rx_list` 整串往上送：

```c
static inline void gro_flush_normal(struct gro_node *gro, bool flush_old)
{
	gro_flush(gro, flush_old);
	gro_normal_list(gro);
}
```

靜態閱讀時這一行很容易漏掉：它在一個名叫 `napi_complete_done`（「NAPI 完成」）的函式中間，名字完全沒提到「送封包」。這是一個典型的 **beacon 失效**：函式名描述的是它的主要職責（結束 polling、清狀態位元），順手做的事不會出現在名字裡。`__napi_poll()` 在 budget 用完的路徑也有一個 `gro_flush_normal`（`:7631`），那是另一個出口。

### 實驗三：改一個參數，預測結果

如果解釋正確，把 `gro_normal_batch` 改成 1，`gro_normal_one()` 每收到一個封包就會立刻 `gro_normal_list()`，堆疊應該變成 `e1000_clean_rx_irq → gro_receive_skb → netif_receive_skb_list_internal`。

`g4.sh` 是 `g1.sh` 在 `cd /sys/kernel/tracing` 之前多一行：

```sh
echo 1 > /proc/sys/net/core/gro_normal_batch; cat /proc/sys/net/core/gro_normal_batch
```

實際輸出：

```
1
...
=== stack ===
          <idle>-0       [000] ..s1.    12.039079: ip_list_rcv <-__netif_receive_skb_list_core
          <idle>-0       [000] ..s1.    12.040602: <stack trace>
 => [FTRACE TRAMPOLINE]
 => ip_list_rcv
 => __netif_receive_skb_list_core
 => netif_receive_skb_list_internal
 => gro_receive_skb
 => e1000_clean_rx_irq
 => e1000_clean
 => __napi_poll
 => net_rx_action
 ...
```

預測成立。`napi_complete_done` 從堆疊裡消失，`gro_receive_skb` → `netif_receive_skb_list_internal` 出現（中間的 `gro_skb_finish`、`gro_normal_one`、`gro_normal_list` 都被 inline 了）。**能預測一個參數改變後的行為，才算真的懂了**，這比「讀懂了每一行」更強的證據。

### 實驗四：看 `pt_prev` 延遲投遞

最後驗證 `pt_prev` 的讀法。如果有一個 tap，它應該在 `__netif_receive_skb_core` **裡面**被呼叫（`deliver_skb`），而 IP handler 是最後一個 `pt_prev`，應該在 core **返回之後**才由呼叫者呼叫。

寫一個最小的 tap 程式 `tap.c`，開一個 `AF_PACKET`／`ETH_P_ALL` socket（kernel 會在 `ptype_all` 掛一個 `packet_rcv`），撐 3 秒：

```c
/* tap.c：開一個 AF_PACKET/ETH_P_ALL socket，讓 kernel 在 ptype_all 掛一個 tap，撐 3 秒 */
#include <arpa/inet.h>
#include <linux/if_ether.h>
#include <stdio.h>
#include <sys/socket.h>
#include <unistd.h>
int main(void)
{
	int fd = socket(AF_PACKET, SOCK_RAW, htons(ETH_P_ALL));
	if (fd < 0) { perror("socket"); return 1; }
	printf("tap fd=%d\n", fd); fflush(stdout);
	sleep(3);
	return 0;
}
```

```
$ gcc -static -O2 -o tap tap.c
```

`g3.sh` 在背景跑 `/bin/tap`，然後對 `__netif_receive_skb_list_core` 做 3 層的 function_graph：

```
tap fd=3
=== graph with tap ===
 0)               |  __netif_receive_skb_list_core() {
 0)               |    __netif_receive_skb_core.constprop.0() {
 0) ! 151.386 us  |      packet_rcv();                ← tap：在 core 裡面，經由 deliver_skb
 0) ! 387.740 us  |    }
 0)               |    ip_list_rcv() {                ← 最後一個 pt_prev：core 返回後才呼叫
 0) + 10.249 us   |      ip_rcv_core();
 0) ! 638.394 us  |      ip_sublist_rcv();
 0) ! 682.528 us  |    }
 0) # 1506.018 us |  }
```

對照沒有 tap 的同一個 graph（實驗二的第二段）：

```
 0)               |  __netif_receive_skb_list_core() {
 0)   7.715 us    |    __netif_receive_skb_core.constprop.0();
 0)               |    ip_list_rcv() {
 0)   2.655 us    |      ip_rcv_core();
 0) ! 185.449 us  |      ip_sublist_rcv();
 0) ! 200.849 us  |    }
 0) ! 244.711 us  |  }
```

沒有 tap 時 core 裡面什麼 handler 都沒呼叫（7.7 us 就返回），因為唯一的 handler（IP）就是最後一個 `pt_prev`，被留給呼叫者。有 tap 時，tap 是「前一個」，在找到 IP handler 時被送出；IP 仍然是最後一個。這正是 `pt_prev` 的讀法。（`deliver_skb` 是 inline，所以 graph 裡 `packet_rcv` 直接掛在 core 底下。）

## 脈絡：為什麼會有這個批次？

靜態閱讀錯在「以為封包在 `gro_receive_skb` 裡往上送」。這個設計是什麼時候、為什麼出現的？用 commit message 搜尋（不需要下載 blob，在 blobless clone 上也很快）：

```
$ git log --oneline v6.18 --grep="gro_normal_batch" | tail -3
6570bc79c0df net: core: use listified Rx for GRO_NORMAL in napi_gro_receive()
61552d2ce861 Merge branch 'net-batched-receive-in-GRO-path'
323ebb61e32b net: use listified RX for handling GRO_NORMAL skbs
```

| commit | 版本 | 作者 | 做了什麼 |
|---|---|---|---|
| `7da517a3bc52` net: core: Another step of skb receive list processing | v4.19 | Edward Cree | `__netif_receive_skb_list_core` 與 sublist 分派，list 版收包的基礎 |
| `323ebb61e32b` net: use listified RX for handling GRO_NORMAL skbs | v5.4 | Edward Cree | `GRO_NORMAL` 不再馬上送，先放 list，在 flush 時批次送；新增 `gro_normal_batch`，預設 8 |
| `6570bc79c0df` net: core: use listified Rx for GRO_NORMAL in napi_gro_receive() | v5.5 | Alexander Lobakin | 把上面的做法擴大到所有用 `napi_gro_receive()` 的 driver（e1000 就是） |
| `b167191e2a85` net: wireless: intel: iwlwifi: fix GRO_NORMAL packet stalling | v5.5 | Alexander Lobakin | 修 `6570bc79c0df` 造成的 iwlwifi regression |

`323ebb61e32b` 的 message 直接說明了設計：

```
When GRO decides not to coalesce a packet, in napi_frags_finish(), instead
 of passing it to the stack immediately, place it on a list in the napi
 struct.  Then, at flush time (napi_complete_done(), napi_poll(), or
 napi_busy_loop()), call netif_receive_skb_list_internal() on the list.
...
Because a full 64 packets is an inefficiently large batch, also consume the
 list whenever it exceeds gro_normal_batch, a new net/core sysctl that
 defaults to 8.
```

「at flush time (napi_complete_done(), napi_poll(), ...)」：實驗一看到的就是這個。

最有教育意義的是 `b167191e2a85`：

```
However, this change has led to a regression in iwlwifi driver [1][2] as
it is required for NAPI users to call napi_complete_done() or
napi_complete() and the end of every polling iteration, whilst iwlwifi
doesn't use NAPI scheduling at all and just calls napi_gro_flush().
In that particular case, packets which have not been already flushed
from napi->rx_list stall in it until at least next Rx cycle.
```

iwlwifi 的作者當年讀 `napi_gro_receive()` 時，得出的結論和我們的靜態假說一樣：「交給 `napi_gro_receive` 就送出去了」。2019 年批次化之後，這個假設不再成立，封包卡在 `rx_list` 裡，直到下一次收包才送出。**靜態閱讀的錯誤，在真實世界裡就是一個 regression。** 本章的實驗一花了兩分鐘就看出來的事，當時是靠使用者回報才發現的。

## 寫報告

```
[冷讀報告] e1000 收包 → IP 層（v6.18）

三句話答案：
1. e1000_intr 只關中斷、排 NAPI；softirq 的 net_rx_action → __napi_poll 呼叫 n->poll
   （e1000_clean），e1000_clean_rx_irq 逐個 descriptor 建 skb，經 napi_gro_receive 交給 GRO。
2. ICMP 不能合併（GRO_NORMAL），gro_normal_one 只把它放進 gro->rx_list；要湊滿
   gro_normal_batch（預設 8）或在 napi_complete_done 的 gro_flush_normal 時才批次往上送
   （ftrace 實驗一、三證實；設計來自 323ebb61e32b / 6570bc79c0df，v5.4–v5.5）。
3. __netif_receive_skb_core 以 another_round 迴圈處理 device 轉換，用 pt_prev 延遲投遞：
   taps 經 deliver_skb 各拿一個 reference，最後一個 handler（ip_packet_type）交回呼叫者，
   由 list 版整串呼叫 ip_list_rcv（實驗四證實）。

鴻溝：間接兩層（n->poll、packet_type->func）→ 找賦值點；
     領域（NAPI、GRO、ptype）；慣用法（pt_prev）；
     脈絡（listified RX）→ 靜態閱讀的錯誤由它造成

靜態閱讀錯在哪：以為 gro_receive_skb 會往上送；漏掉 napi_complete_done 裡的
gro_flush_normal（函式名沒有提示它會送封包）

未解問題：
- 有 TCP 流量、GRO 真的合併時，gro_flush 何時把合併好的大封包吐出來？
  → 下次：用 TCP 流量重跑實驗二，加 trace napi_gro_receive_entry/exit tracepoint
- rx_handler（bridge）存在時路徑怎麼變？→ 下次：guest 裡建一個 bridge 重跑實驗四
```

## 對比與取捨

| 方法 | 回答得了 | 回答不了 | 本章的用法 |
|---|---|---|---|
| 靜態閱讀 | 所有**可能**的路徑、每個分支的條件、資料結構 | 這次實際走哪條、批次與時序 | 建立假說、標出未解問題 |
| ftrace function + stack | 這次實際的呼叫者鏈 | 被 inline 的函式、參數值 | 驗證假說、找出錯誤 |
| ftrace function_graph | 呼叫順序與巢狀關係、相對時間 | 同上 | 看「先返回、後送出」的時序 |
| 改參數再觀察 | 因果關係 | — | 證明解釋是對的，不只是「剛好吻合」 |
| git 考古 | 為什麼這樣設計、誰踩過坑 | 現在的行為 | 解釋靜態閱讀為什麼會錯 |

靜態和動態不是二選一。靜態閱讀先提出一個**可以被推翻**的假說，動態觀察才有東西可以驗證；沒有假說就開 trace，只會被輸出淹沒（[Ch 51](./51-dynamic-tracing.md)）。

## 踩雷集錦

- **只 filter `ip_rcv`。** 這條路徑走的是 list 版的 `ip_list_rcv`，filter 只放 `ip_rcv` 會什麼都抓不到，然後誤以為封包沒到 IP 層。不確定走哪個版本時，兩個都放。
- **在 trace 裡找 inline 函式。** `gro_normal_list`、`napi_gro_receive` 不在 `available_filter_functions` 裡，`set_ftrace_filter` 寫它們會失敗。先 grep 清單再決定 filter。
- **把 stack trace 當成完整的呼叫鏈。** 堆疊裡 `netif_receive_skb_list_internal` 直接接 `napi_complete_done`，中間的 `gro_flush_normal`、`gro_normal_list` 被 inline 了，不會出現。要對照原始碼補回來。
- **相信 TCG 下的時間數字。** QEMU TCG 是軟體模擬，`dma_unmap_phys` 花 103 us 不代表真實硬體也是這樣。只看相對大小和順序。
- **第一次 ping 就 trace。** 第一個封包前面會有 ARP 請求與回應，trace 會混進 `arp_rcv`。腳本先 ping 一次暖身，讓 ARP 表有 gateway，第二次 ping 才開 trace。
- **以為 `gro_normal_batch` 是 per-CPU 或 per-device 的。** 它是 `net_hotdata` 裡的全域值（`include/net/hotdata.h:40`），改了影響所有 NAPI instance。實驗完要記得改回 8（guest 關機就沒了，真機上要注意）。

## 進階：再往深一層

- **loopback 路徑**：`ping 127.0.0.1` 不經過網卡，`loopback_xmit` → `__netif_rx` → `enqueue_to_backlog` 把封包放進 per-CPU backlog，再由一個假的 NAPI instance（`process_backlog`，`net/core/dev.c:6522`）收。對照本章的 e1000 路徑，看哪些部分共用、哪些不同。
- **RPS**：`netif_receive_skb_list_internal()` 裡有 RPS（Receive Packet Steering）的分支，會把封包丟到別的 CPU 的 backlog。在 guest 裡設定 `/sys/class/net/eth0/queues/rx-0/rps_cpus`，重跑實驗一，堆疊會變成什麼樣？
- **XDP native**：e1000 不支援 native XDP，但 virtio_net 支援。換成 `-nic user,model=virtio-net-pci`，讀 `virtnet_poll` 的路徑，看 XDP 在 `__netif_receive_skb_core` **之前**多遠就攔截了封包。

## 動手練習

1. 先不跑實驗，只讀原始碼預測：如果把實驗腳本裡「先 ping 一次暖身」那行拿掉，第一個收到的封包會是 gateway 的 ARP 回應。它在 `__netif_receive_skb_list_core` 裡會交給哪個函式？是 list 版還是逐一呼叫？提示：看 `arp_packet_type`（`net/ipv4/arp.c:1374`）有沒有 `list_func`。預測完再寫一個不暖身的 `g5.sh`，用 2 層的 function_graph 驗證。
2. 在實驗四的 tap 程式之外再開第二個 tap（跑兩個 `/bin/tap`），預測 graph 裡 `packet_rcv` 會出現幾次、在 core 裡面還是外面，然後實際跑一次驗證。
3. 讀 `__netif_receive_skb_core` 的 `rx_handler` 那段（`net/core/dev.c:5960–5980`），列出四種回傳值各代表什麼，並找一個實際的 `rx_handler` 實作（提示：`rg -n 'netdev_rx_handler_register' net/bridge`）。

<details>
<summary>練習提示與參考答案</summary>

1. ARP 回應的 `type` 是 `ETH_P_ARP`，最後一個 `pt_prev` 是 `arp_packet_type`。它只有 `.func = arp_rcv`，沒有 `.list_func`，所以 `__netif_receive_skb_list_ptype()`（`net/core/dev.c`，緊接在 `netif_receive_skb_core` 之後）走 `else` 分支，對 sublist 裡的每個 skb 逐一呼叫 `pt_prev->func`。實際跑出來（`~/code_reading/scratch/56/g5.sh`）：

   ```
   === graph cold ARP ===
    0)               |  __netif_receive_skb_list_core() {
    0) ! 207.034 us  |    __netif_receive_skb_core.constprop.0();
    0) ! 786.498 us  |    arp_rcv();
    0) # 1556.205 us |  }
    0)               |  __netif_receive_skb_list_core() {
    0)   2.585 us    |    __netif_receive_skb_core.constprop.0();
    0) # 1763.440 us |    ip_list_rcv();
    0) # 1817.359 us |  }
   ```

   第一輪是 ARP 回應，`arp_rcv` 直接掛在 list core 底下（`__netif_receive_skb_list_ptype` 被 inline 了）；第二輪才是 ICMP echo reply，走 `ip_list_rcv`。這也說明了為什麼實驗一要先暖身。
2. 預測：`packet_rcv` 出現兩次，**兩次都在 core 裡面**。兩個 tap 和 IP handler 共三個 handler，前兩個（兩個 tap）都在找到下一個時經由 `deliver_skb` 送出，最後一個（IP）交回呼叫者。實際跑的方法：把 `g3.sh` 的 `/bin/tap &` 改成兩行（`g6.sh`）。實測輸出：

   ```
    0)               |  __netif_receive_skb_list_core() {
    0)               |    __netif_receive_skb_core.constprop.0() {
    0) ! 109.657 us  |      packet_rcv();
    0)   6.682 us    |      packet_rcv();
    0) ! 376.830 us  |    }
    0)               |    ip_list_rcv() {
   ```

   兩次 `packet_rcv` 都在 core 裡面，IP 仍然在外面，和預測一致。
3. `RX_HANDLER_CONSUMED`：handler 已經接手封包（例如 bridge 轉發出去了），core 直接 `out`。`RX_HANDLER_ANOTHER`：handler 把 `skb->dev` 換成另一個 device（例如 bond 的 master），回到 `another_round` 重新分揀。`RX_HANDLER_EXACT`：只投遞給「精確綁定這個 device」的 handler，設 `deliver_exact = true`，跳過 `ptype_base` 和 netns 的 `ptype_specific`。`RX_HANDLER_PASS`：當作沒有 rx_handler，繼續正常分揀。bridge 的實作是 `br_handle_frame()`，在 `net/bridge/br_if.c` 用 `netdev_rx_handler_register(dev, br_get_rx_handler(dev), p)` 註冊。

</details>

## 本章重點整理

- 跨越非同步邊界的路徑，要先畫出每一段的執行脈絡（hardirq → softirq → poll → 協定層），每一段各自找函式指標的賦值點。
- 為了效能做批次處理的路徑，靜態閱讀容易得出「收到就送」的錯誤假說；送出的時機常藏在名字不相干的函式裡（`napi_complete_done` 裡的 `gro_flush_normal`）。
- `pt_prev` 延遲投遞：找到下一個才送上一個，最後一個交回呼叫者，省 refcount 並讓 list 版能批次呼叫。
- 驗證理解最強的證據是「改一個參數，預測結果，然後成立」；git 考古則解釋了靜態閱讀為什麼會錯，以及誰在真實世界踩過同一個坑。

## 自我檢核

- [ ] 不看筆記，畫出 e1000 收包到 `ip_list_rcv` 的完整堆疊，標出每一段的執行脈絡
- [ ] 能解釋為什麼一個 ping 的封包是在 `napi_complete_done` 裡往上送，而不是在 `gro_receive_skb` 裡
- [ ] 能用三句話解釋 `pt_prev` 延遲投遞，以及它帶來的兩個好處
- [ ] 能預測把 `gro_normal_batch` 改成 1 之後堆疊的變化，並說出中間哪些函式會因為 inline 而看不到
- [ ] 能說出 iwlwifi regression（`b167191e2a85`）和本章靜態假說的關係

## 延伸閱讀

### 官方文件

- **[NAPI](https://docs.kernel.org/networking/napi.html)**（tree 內 `Documentation/networking/napi.rst`）
  - **讀哪裡**：「Datapath API」與「Control API」兩節，以及 `napi_complete_done()` 回傳值的說明
  - **學到什麼**：poll 函式與 budget 的契約、driver 什麼時候可以重新打開中斷；讀完就知道 `e1000_clean` 為什麼要檢查 `napi_complete_done` 的回傳值
- **[Segmentation Offloads](https://docs.kernel.org/networking/segmentation-offloads.html)**（`Documentation/networking/segmentation-offloads.rst`）
  - **讀哪裡**：Generic Receive Offload 相關段落
  - **學到什麼**：GRO 合併封包的條件，回答本章「ICMP 為什麼是 `GRO_NORMAL`」的領域問題
- **[Scaling in the Linux Networking Stack](https://docs.kernel.org/networking/scaling.html)**（`Documentation/networking/scaling.rst`）
  - **讀哪裡**：RSS、RPS 兩節
  - **學到什麼**：「進階」裡 RPS 實驗的背景
- **`Documentation/admin-guide/sysctl/net.rst`** 的 `gro_normal_batch`、`netdev_budget`、`netdev_budget_usecs` 條目
  - **學到什麼**：本章出現的三個調整參數的官方定義

### 論文

- **Eliminating Receive Livelock in an Interrupt-Driven Kernel** — Jeffrey C. Mogul、K. K. Ramakrishnan，USENIX ATC 1996（期刊版：ACM TOCS 15(3)，1997）
  - **核心貢獻**：指出純中斷驅動的收包在高負載下會陷入 livelock（CPU 全花在中斷上，沒有時間處理封包），提出「中斷只用來啟動輪詢、輪詢有配額」的做法
  - **讀哪裡**：描述 livelock 現象的實驗與提出的排程機制
  - **和本章的關聯**：NAPI 的思想來源；`e1000_intr` 只關中斷、排 poll，以及 `net_rx_action` 的 budget 與時間上限，都是這篇論文的直接後代

### 書籍

- **《Understanding Linux Network Internals》** — Christian Benvenuti（O'Reilly, 2005）
  - **讀哪幾章**：第 9 章（Interrupts and Network Drivers）、第 10 章（Frame Reception）、第 13 章（Protocol Handlers）
  - **定位**：NAPI、`netif_receive_skb`、`packet_type` 分派的經典說明。程式碼是 2.6 早期的，GRO 和 list 版收包都還沒出現，正好可以對照本章看「這二十年多了什麼」

三章冷讀分別練了短函式、driver 生命週期與跨脈絡的資料路徑。下一章換一種輸入：不是一段已經存在的程式碼，而是一個 diff。

→ [Ch 57 讀 patch 與 code review：讀 diff 的心法](./57-reading-patches-and-review.md)
