# Ch 45 — socket layer 與送包路徑、qdisc

> **目標**：從使用者空間一個 `send(fd, buf, len)` 出發，追它一路往下——穿過 socket 抽象（socket 也是 file）、協定的 `sendmsg`、TCP/UDP 的 L4、IP 的路由查找與 L3、鄰居子系統（ARP）的 L2、qdisc 排隊層，最後到 driver 的 `ndo_start_xmit` 放進 TX ring 由網卡 DMA 送出。學完你能在腦中畫出這條完整下行路徑，並用 `ss`/`tc`/`strace`/`bpftrace` 在真機上把每一層看出來。

Ch 43 給了我們兩個網路核心物件：`sk_buff`（封包在 kernel 裡的載體）和 `net_device`（一張網卡的抽象）。Ch 44 追了**收**的路徑——封包從網卡 DMA 進來、NAPI poll、往上交到協定堆疊。這一章是它的鏡像：**送**。但送比收多一個東西——收是硬體叫我們（中斷驅動），送是**我們主動**發起，起點在使用者空間的一個 syscall。所以這章從 socket 抽象講起，再一路往下追到硬體。

## 為什麼需要 socket 這層抽象？

網路協定有很多種：TCP、UDP、raw IP、UNIX domain socket、AF_PACKET、AF_XDP……每種的語意天差地別（TCP 是有序可靠位元組流、UDP 是不可靠資料報、UNIX socket 根本不碰網卡）。但使用者空間只想用**一組**API 操作它們全部：`socket()`、`bind()`、`connect()`、`send()`、`recv()`、`close()`。

這正是抽象要解決的問題：**同一組 syscall，背後接不同協定的實作**。這是典型的多型（polymorphism）——和你在 Ch 33 看過的 VFS 一模一樣。VFS 讓 `read()` 對 ext4、tmpfs、procfs 都能用；socket layer 讓 `send()` 對 TCP、UDP、UNIX socket 都能用。事實上這兩個抽象在 kernel 裡是**接在一起的**：

> **socket 就是 file**。`socket()` 回傳的是一個 file descriptor（Ch 4、linux_commands 的 fd 章講過的那個 fd）。既然是 fd，它在 kernel 裡就有一個 `struct file`，有一組 `file_operations`。你對 socket fd 呼叫 `read()`/`write()`/`close()`/`poll()`，走的就是 VFS 那套分派——只是那組 `file_operations`（`socket_file_ops`，在 `net/socket.c`）把操作轉給 socket 層。這就是為什麼 `epoll` 可以同時等一個檔案和一個 socket：對 VFS 來說它們都是 file。

## 先建立直覺：三層物件與一條路

送包路徑牽涉的物件分兩組。第一組是**socket 的三層身分**——同一個東西在不同抽象層有不同的 struct：

```
  使用者空間            kernel 空間
  ┌────────┐          ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
  │  fd 5  │──VFS────►│ struct file  │──►│ struct socket │──►│  struct sock │
  └────────┘          │ f_op =       │   │ 協定無關的    │   │ 協定核心狀態  │
   send(5,...)        │ socket_file_ │   │ 通用外殼      │   │ (TCP 序號、   │
                      │  ops         │   │ ops = proto_  │   │  壅塞窗、     │
                      └──────────────┘   │  ops (多型)   │   │  send buffer)│
                                         └──────────────┘   └──────────────┘
```

- `struct file`：VFS 層的身分，`f_op` 指向 `socket_file_ops`，`private_data` 指向 `struct socket`。
- `struct socket`（`include/linux/net.h`）：**協定無關**的通用外殼，記 socket 的類型（`SOCK_STREAM`/`SOCK_DGRAM`）、狀態、以及最關鍵的 `ops`——一個 `struct proto_ops`，這就是多型的分派表。
- `struct sock`（`include/net/sock.h`）：**協定核心**。所有協定共用的狀態都在這個基底結構裡：接收/傳送 buffer（`sk_receive_queue`/`sk_write_queue`）、buffer 大小上限（`sk_sndbuf`/`sk_rcvbuf`）、等待佇列（`sk_wq`）、以及一個指向協定自己操作的 `sk_prot`。TCP 會把 `struct sock` 當第一個成員嵌進更大的 `struct tcp_sock`（`include/linux/tcp.h`），存序號、壅塞窗、RTT 這些 TCP 專屬狀態。

> **`socket` vs `sock` 為什麼要拆兩個？** `socket` 是「使用者看得到的那個端點」——它偏 VFS/BSD API 那側。`sock` 是「網路堆疊內部的那個連線狀態」——它偏協定/封包那側。堆疊往下送、往上收的程式碼幾乎只碰 `sock`，不碰 `socket`。這個拆分讓網路核心不必知道上面掛的是不是一個 file。

第二組物件是路徑本身。一個 TCP `send()` 往下走的**完整下行路徑**：

```
  user:  send(fd, buf, len)
    │  syscall (Ch 4)
    ▼
  ┌─────────────────────────────────────────────────────────────┐
  │ sock_sendmsg()  → sock->ops->sendmsg()   ← proto_ops 多型分派  │  net/socket.c
  └─────────────────────────────────────────────────────────────┘
    │
    ▼  tcp_sendmsg / udp_sendmsg                              net/ipv4/tcp.c
  ┌─────────────────────────────────────────────────────────────┐
  │ L4: copy_from_user → skb；TCP 進 send buffer，等狀態機決定送   │
  │     加 TCP/UDP header、checksum                               │
  └─────────────────────────────────────────────────────────────┘
    │  tcp_transmit_skb → ip_queue_xmit
    ▼
  ┌─────────────────────────────────────────────────────────────┐
  │ L3: ip_output → 路由查找 (FIB)：從哪張 netdev 出、下一跳是誰   │  net/ipv4/ip_output.c
  │     加 IP header、算 checksum                                 │
  └─────────────────────────────────────────────────────────────┘
    │  ip_finish_output → ip_finish_output2
    ▼
  ┌─────────────────────────────────────────────────────────────┐
  │ L2: 鄰居子系統 (neighbour)：ARP 查下一跳的 MAC，加 ethernet    │  net/core/neighbour.c
  │     header。查不到 MAC → 先發 ARP request，skb 暫存等回應      │
  └─────────────────────────────────────────────────────────────┘
    │  dev_queue_xmit
    ▼
  ┌─────────────────────────────────────────────────────────────┐
  │ qdisc: 排隊/流量整形。enqueue → dequeue。pfifo_fast/fq_codel  │  net/core/dev.c
  │        mq（多硬體佇列，對應 Ch 15 的多 CPU）、tc 限速          │  net/sched/
  └─────────────────────────────────────────────────────────────┘
    │  sch_direct_xmit → netdev_start_xmit
    ▼
  ┌─────────────────────────────────────────────────────────────┐
  │ driver: ops->ndo_start_xmit(skb, dev)  ← net_device_ops(Ch43)│
  │         填 TX descriptor → 放進 TX ring                      │
  └─────────────────────────────────────────────────────────────┘
    │  DMA (Ch 41)
    ▼
  ┌──────────┐  送完 → TX 完成中斷 → 回收（free/unmap）skb
  │  網卡硬體 │  ────────────────────────────────────────►
  └──────────┘
```

記住這張圖。這一章剩下的內容就是把每個框框拆開看它的源碼。

## socket syscall 怎麼進到協定家族

`socket(AF_INET, SOCK_STREAM, 0)` 是這一切的起點。它的實作在 `net/socket.c` 的 `__sys_socket()` → `sock_create()` → `__sock_create()`。關鍵一步是**協定家族（address family）的分派**：

kernel 維護一個全域陣列 `net_families[]`（`net/socket.c`），index 是 address family 編號（`AF_INET`=2、`AF_UNIX`=1、`AF_PACKET`=17……）。每個協定家族開機時用 `sock_register()` 把自己的 `struct net_proto_family` 註冊進去。`__sock_create()` 拿 `family` 當 index 找到對應的 `create` 函式呼叫它。對 `AF_INET`，那是 `inet_create()`（`net/ipv4/af_inet.c`）。

`inet_create()` 再做**第二層分派**：根據 `type`（`SOCK_STREAM`/`SOCK_DGRAM`）和 `protocol`，在 `inetsw[]` 表裡找對應的 `struct inet_protosw`，它同時給出兩個東西：

- `ops`：填進 `socket->ops`，型別是 `struct proto_ops`。TCP 是 `inet_stream_ops`、UDP 是 `inet_dgram_ops`。這是**BSD socket API 層**的多型（`bind`/`connect`/`sendmsg`/`recvmsg`）。
- `prot`：填進 `sock->sk_prot`，型別是 `struct proto`。TCP 是 `tcp_prot`、UDP 是 `udp_prot`（`net/ipv4/tcp_ipv4.c`、`udp.c`）。這是**協定內部**的多型（連線建立、buffer 管理、往下送的 `sendmsg`）。

> **兩張多型表，不要搞混**。`proto_ops`（在 `socket->ops`）是「socket API 那一面」；`proto`（在 `sock->sk_prot`）是「協定核心那一面」。`sendmsg` 兩張表都有：`proto_ops.sendmsg`（=`inet_sendmsg`）是通用入口，它幾乎只是轉手呼叫 `sk_prot->sendmsg`（=`tcp_sendmsg`/`udp_sendmsg`）。分兩層是因為 `inet_sendmsg` 那層要處理「自動 bind」等 AF_INET 共通邏輯，真正的協定行為在 `sk_prot` 那層。

這種「陣列 + 註冊」的分派模式，你在 Ch 33 的 `file_system_type`、Ch 39 的 platform driver 都見過。kernel 到處是這一招。

## send 路徑第一段：syscall → sendmsg → 進 send buffer

使用者呼叫 `send()`（或 `write()`、`sendto()`、`sendmsg()`——都殊途同歸）。x86_64 上 syscall 入口（Ch 4）到 `net/socket.c` 的 `__sys_sendto()`，它把使用者的 buffer 包成一個 `struct msghdr`（`msg_iov` 指向使用者記憶體），呼叫 `sock_sendmsg()`：

```c
// net/socket.c，精簡示意
int sock_sendmsg(struct socket *sock, struct msghdr *msg)
{
    // security hook (LSM, Ch 48) 略
    return sock->ops->sendmsg(sock, msg, ...);   // → inet_sendmsg → tcp_sendmsg
}
```

`sock->ops->sendmsg` 這一行就是多型分派。TCP 落到 `tcp_sendmsg()`（`net/ipv4/tcp.c`）。這裡發生兩件重要的事：

**(1) 資料從 user 複製進 skb。** `tcp_sendmsg`（實際幹活的是 `tcp_sendmsg_locked`）從 socket 的 send buffer 尾端拿一個還沒填滿的 skb（或配一個新的），用 `copy_from_user`（Ch 4 講過的 user→kernel 安全複製，這裡走 `skb_add_data` / `sk_msg` 那套）把使用者資料抄進 skb 的 payload。抄多少受 MSS（最大 segment 大小）和 send buffer 剩餘空間限制。

**(2) 資料進 send buffer，不一定馬上送。** 這是 TCP 和 UDP 最大的差別。TCP 是**位元組流 + 可靠傳輸 + 壅塞控制**，`tcp_sendmsg` 把資料掛進 `sk->sk_write_queue`（send buffer）後，**由 TCP 狀態機決定何時真的送**：

- **壅塞控制（congestion control）**：現在網路允許在途多少未確認位元組（`cwnd`，壅塞窗）？滿了就得等 ACK 回來才能再送。
- **流量控制**：對端 advertise 的接收窗（`rwnd`）還剩多少？
- **Nagle 演算法**：小封包會被攢一攢，湊夠一個 MSS 或等前一個小封包被 ACK 再送，減少「一個位元組配一整個 header」的浪費。`TCP_NODELAY`（`setsockopt`）關掉它——低延遲場景（遊戲、互動式）常要關。

所以 `send()` 回傳成功**只代表資料被複製進 kernel 的 send buffer 了**，不代表已經上線、更不代表對端收到。真正把 skb 往下推的是 `tcp_write_xmit()` → `tcp_transmit_skb()`，它可能在 `send()` 的 context 裡同步發生，也可能稍後由 ACK 觸發、由 timer 觸發。

> UDP 沒有這些。`udp_sendmsg()`（`net/ipv4/udp.c`）把資料包成一個 skb 就**直接往下丟**（`udp_send_skb` → `ip_send_skb`），沒有 send buffer 排隊、沒有壅塞控制、沒有 Nagle。這正是 UDP「不可靠但低延遲」的來源。TCP 的複雜幾乎全在這個「何時送」的決策上——networking 課的 TCP 部分講的就是這套狀態機，這裡我們只認得它在源碼裡的入口。

`tcp_transmit_skb()` 會 clone 一份 skb（原本那份要留著等 ACK，可能要重傳），填好 TCP header（來源/目的 port、序號、ACK 號、window、flags、checksum——checksum 常 offload 給網卡算），然後呼叫 `ip_queue_xmit()` 交給 L3。

## send 路徑第二段：L3 路由查找

`ip_queue_xmit()`（`net/ipv4/ip_output.c`）要回答一個問題：**這個封包該從哪張網卡出、下一跳是誰？** 這叫**路由查找（route lookup）**，查的是 FIB（Forwarding Information Base，轉發資訊庫）。

TCP 連線已建立時，路由結果被快取在 socket 上（`sk->sk_dst_cache`），不必每個封包重查。新連線或快取失效時走 `ip_route_output_*` → `fib_lookup()`（`net/ipv4/fib_*.c`），拿目的 IP 去 FIB（實作是 LPC-trie，一種壓縮 trie）做最長前綴匹配，得到一個 `struct rtable`/`struct dst_entry`：它記著**出口 netdev**、**下一跳 IP**（gateway，若目的不在同網段）、以及往下要呼叫的 `output` 函式。

`dst_entry` 是路由結果的抽象，掛在 skb 上（`skb_dst()`）。它有個 `output` 函式指標，L3 往下就是呼叫它——通常是 `ip_output()`。`ip_output()` 填好 IP header（版本、TTL、協定號、來源/目的 IP、header checksum），過 netfilter 的 `NF_INET_POST_ROUTING` hook（Ch 46 的主題——iptables/nftables 的 SNAT、防火牆規則就掛在這），然後 `ip_finish_output()` → `ip_finish_output2()`。

## send 路徑第三段：L2 鄰居子系統與 ARP

L3 知道「下一跳的 IP」，但網卡送的是 ethernet frame，需要「下一跳的 **MAC** 位址」。IP→MAC 的翻譯就是 **ARP**（Address Resolution Protocol），在 kernel 裡由**鄰居子系統（neighbour subsystem）**管理（`net/core/neighbour.c`，ARP 特化在 `net/ipv4/arp.c`）。

`ip_finish_output2()` 拿下一跳 IP 去查鄰居表（`struct neighbour`，本質是一個 hash 表）：

- **命中且狀態是 `NUD_REACHABLE`**：直接拿到 MAC，`neigh_output` → `dev_hard_header()` 填好 ethernet header（來源/目的 MAC、ethertype），呼叫 `dev_queue_xmit()` 送下去。
- **未命中或狀態過期**：skb **暫存**在這個 `neighbour` 的 `arp_queue` 裡，kernel 發一個 **ARP request**（廣播「誰有 IP x.x.x.x？」）。ARP reply 回來後（走收包路徑上來，`arp_process`），填好鄰居表，暫存的 skb 才被放行往下送。

> 這解釋了一個常見現象：一台機器**第一個**打到某 IP 的封包常有幾毫秒延遲，之後就快了——第一個封包在等 ARP 解析，之後鄰居表命中直接送。鄰居狀態機（`NUD_INCOMPLETE`/`REACHABLE`/`STALE`/`DELAY`/`PROBE`）就是在管這個快取的新鮮度。networking 課的 ARP 章講協定細節，這裡我們看到它在送包路徑上是**加 L2 header 前的最後一步**。

ethernet header 填好後，skb 現在是一個**完整的 frame**（L2+L3+L4 header 齊全，payload 就位）。`dev_queue_xmit()` 接手，進入 qdisc。

## send 路徑第四段：qdisc——送到網卡前的排隊層

這是這一章相對於「收」最獨有的一層。收包不需要排隊（封包來了就往上處理），但**送**需要——因為你可能在 1 微秒內 `send()` 一萬個封包，而網卡一次只能送一個，中間需要一個緩衝/排序/整形的機制。這就是 **qdisc（queueing discipline，排隊規則）**。

每張網卡（更精確說每個 TX 佇列）掛一個 root qdisc（`netdev_queue->qdisc`）。`dev_queue_xmit()`（`net/core/dev.c`，核心是 `__dev_queue_xmit`）的骨架：

```c
// net/core/dev.c，__dev_queue_xmit 精簡骨架
q = rcu_dereference(txq->qdisc);      // 拿這條 TX 佇列的 qdisc
if (q->enqueue) {
    rc = __dev_xmit_skb(skb, q, dev, txq);   // enqueue → 觸發 dequeue → 往下送
    return rc;
}
// 無 qdisc（如 loopback）：跳過排隊，直接 dev_hard_start_xmit
```

`__dev_xmit_skb` 做兩件事：`q->enqueue(skb, q)` 把 skb 排進 qdisc，然後 `qdisc_run(q)` 觸發 **dequeue**——把 qdisc 裡的 skb 一個個取出、透過 `sch_direct_xmit()` 送給 driver。enqueue 和 dequeue 之間，qdisc 可以做任何事：排序、限速、丟包、分類。

**預設 qdisc 是什麼？**

- **`pfifo_fast`**（`net/sched/sch_generic.c`）：老預設。三個優先權 band 的 FIFO，依 skb 的 ToS/priority 分 band，高優先權先送。簡單、快、但不防 bufferbloat。
- **`fq_codel`**（`net/sched/sch_fq_codel.c`）：多數現代 distro（systemd 設 `net.core.default_qdisc`）的預設。FQ（fair queueing，每條流公平分佇列，防一條大流餓死小流）+ CoDel（Controlled Delay，主動偵測佇列延遲過高就丟包，對抗 **bufferbloat**——緩衝太大導致延遲爆炸的老問題）。

**其他你會遇到的：**

- **`mq`**（multiqueue）：不是一個真的排隊演算法，而是一個「容器」——現代網卡有**多個硬體 TX 佇列**（對應多 CPU，Ch 15 的 SMP），`mq` 給每個硬體佇列各掛一個子 qdisc，讓不同 CPU 送包走不同佇列不互相搶鎖。這是高吞吐的關鍵。
- **TC（traffic control）自訂 qdisc**：`htb`（分層 token bucket，做頻寬分配）、`tbf`（token bucket，限速）、`netem`（網路模擬，加延遲/丟包/亂序——測試用神器）。這些就是 networking 課 QoS/限速那套，`tc` 指令配的就是這一層。
- **BPF qdisc / `clsact`**：tc BPF program 掛在這裡（`net/sched/cls_bpf.c`、`act_bpf.c`）。你在 **bpf 課**寫的 tc ingress/egress BPF 就是掛在這一層對每個封包做決策——這是 XDP（Ch 46，更靠近網卡）之外另一個 BPF 網路掛載點。

dequeue 出來的 skb 經 `sch_direct_xmit()` → `dev_hard_start_xmit()` → `netdev_start_xmit()`，終於呼叫 driver。

## send 路徑最後一段：driver、TX ring、DMA、回收

`netdev_start_xmit()` 呼叫 `dev->netdev_ops->ndo_start_xmit(skb, dev)`——這就是 Ch 43 的 `net_device_ops` 裡那個「送一個封包」的方法，每個 driver 自己實作。它做的事（以典型 NIC driver 為例）：

1. 從 skb 取出資料的實體位址，建立 **DMA mapping**（Ch 41 的 `dma_map_single`，讓網卡能直接讀這塊記憶體）。
2. 填一個或多個 **TX descriptor**（描述「從這個 DMA 位址讀這麼多 bytes」），放進 **TX ring**（driver 和網卡共享的環形 descriptor 陣列，Ch 43 講過 ring buffer）。
3. 更新 ring 的 tail 指標、敲一下網卡的暫存器（"doorbell"）告訴硬體「有新東西要送」。
4. 回傳 `NETDEV_TX_OK`。

網卡硬體讀 descriptor，透過 **DMA** 直接從記憶體把封包資料搬進自己的 FIFO，送上線。**送完之後**，網卡發一個 **TX 完成中斷**（或在收包的同一個 NAPI poll 裡順便處理 TX 完成，Ch 44 的 `napi_poll` 常同時清 RX 和 TX），driver 的完成處理常式做**回收**：`dma_unmap_single`（解除 DMA mapping）、`napi_consume_skb`/`dev_kfree_skb` 釋放 skb（skb 的生命週期到此結束，Ch 43）。

> **為什麼要「送完中斷才回收」？** skb 的記憶體在網卡 DMA 讀完之前不能釋放——提早 free 會讓網卡讀到已被別人重用的記憶體（一個經典的 use-after-free）。TX 完成中斷是硬體告訴我們「這塊記憶體我讀完了，你可以回收了」的訊號。這和 Ch 41 的 DMA 生命週期、Ch 20 的記憶體所有權是同一個道理。

## socket buffer 與流控：send() 什麼時候會擋住

每個 socket 有一個 send buffer 上限 `sk->sk_sndbuf`（和收方的 `sk_rcvbuf` 對稱）。目前已用量記在 `sk->sk_wmem_alloc`/`sk_wmem_queued`。當 send buffer **滿了**（已排隊未送出的資料達到 `sk_sndbuf`），`send()` 的行為取決於 fd 是不是 non-blocking：

- **阻塞模式（預設）**：`send()` 這個 task 進入睡眠（`sk_stream_wait_memory`，走 Ch 26 的等待佇列機制），掛在 `sk->sk_wq` 上。等資料被送出、buffer 空出來時，TX 完成路徑呼叫 `sk->sk_write_space()` 喚醒它，`send()` 才繼續把剩下的資料塞進 buffer。這就是為什麼一個往慢速連線狂寫的程式會**卡在 `send()`**——它在等 send buffer 排空。
- **非阻塞模式（`O_NONBLOCK` / `MSG_DONTWAIT`）**：不睡，立刻回 `-EAGAIN`（`EWOULDBLOCK`）。使用者程式得自己稍後重試——這正是 event loop（epoll + non-blocking）的基本盤。

`sk_sndbuf` 的大小可用 `setsockopt(SO_SNDBUF)` 調（kernel 會把值乘 2 當上限，並夾在 `net.core.wmem_min`/`wmem_max` 之間；TCP 另有 `tcp_wmem` 的 autotuning，會依連線動態調整，通常比你手設更聰明）。buffer 太小 → 吞吐受限（塞不下一個 BDP 的資料，管線填不滿）；太大 → 佔記憶體、且加劇 bufferbloat 延遲。

## TSO / GSO：把切 segment 的活推給硬體

TCP 是位元組流，但實體網路一個 frame 最多裝 MTU（通常 1500 bytes）的 payload。若上層要送 64 KB 資料，理論上得切成約 45 個 MSS 大小的 segment，**每個都跑一遍整條下行路徑**（各自加 header、路由、qdisc……）——CPU 負擔很重。

**offload** 的想法是：**讓堆疊只處理一個大 segment，切成 MTU 大小的活留到最後、甚至交給網卡硬體做**：

- **TSO（TCP Segmentation Offload）**：堆疊把一個大到 64 KB 的 skb（帶一份 TCP header 模板 + `gso_size` 說明每片多大）一路送到 driver，**網卡硬體**自己把它切成多個 MTU 大小的封包、複製並調整每片的 TCP header（序號、checksum）。整條 kernel 路徑只跑一次，切割的 CPU 成本轉嫁給網卡。
- **GSO（Generic Segmentation Offload）**：硬體不支援 TSO 時的軟體版——堆疊一樣用大 skb 走完大部分路徑，但在**送出前的最後一刻**（`dev_hard_start_xmit` 裡的 `skb_gso_segment`）才由 kernel 軟體切開。省的是「重複跑上層路徑」的成本（雖然切割仍在 CPU）。
- 這是 Ch 44 **GRO（收方合併）的對稱操作**：GRO 收的時候把多個小封包合併成一個大 skb 往上交，TSO/GSO 送的時候把一個大 skb 切成多個小封包往下發。一收一送，都是「堆疊處理大塊、線上跑小塊」的同一個省 CPU 哲學。

用 `ethtool -k eth0` 看哪些 offload 開著（`tcp-segmentation-offload`、`generic-segmentation-offload`……），`ethtool -K eth0 tso off` 關掉。除錯詭異的封包問題時，關掉 offload 是常見的第一步——因為開著 offload 時 `tcpdump` 在本機看到的是**沒被切開的大封包**（切割發生在它之後），容易誤判。

## epoll 與網路：大量 socket 怎麼高效等

一個伺服器要同時服務上萬個連線，不可能每個連線開一個 thread 去 blocking `recv()`。答案是 **epoll**（linux_commands / networking 都碰過的使用者空間視角）+ non-blocking socket。從 kernel 角度看，這一切建立在 socket 的 **`poll` 機制**上：

`socket_file_ops.poll` → `sock_poll` → `sk_prot->poll`（TCP 是 `tcp_poll`）。`poll` 做兩件事：(1) 把呼叫者（epoll）**登記到 socket 的等待佇列** `sk->sk_wq`（Ch 26 的 wait queue）；(2) 回報目前的就緒狀態（`EPOLLIN` 有資料可讀、`EPOLLOUT` send buffer 有空間可寫……）。

當封包到達（收包路徑，Ch 44）或 send buffer 空出（送包完成），kernel 呼叫 `sk->sk_data_ready()` / `sk->sk_write_space()`，這些函式**喚醒等待佇列上的 epoll**。epoll 於是把這個 fd 標記為就緒，`epoll_wait()` 返回，使用者的 event loop 才去對這個 fd 做 non-blocking 的 `recv`/`send`。整條鏈是：**收包 softirq → `sk_data_ready` → 喚醒 wait queue → epoll ready → `epoll_wait` 返回**。這把「等 I/O」從「每個連線一個睡著的 thread」變成「一個 thread 等一堆 fd」，是高並發伺服器（nginx、Redis）的基石。

## 動手：把每一層看出來

在真機（或你的 QEMU guest，需有網路：QEMU 加 `-netdev user,id=n -device e1000,netdev=n`）上，用工具把上面每一層對到真實狀態。

**看 socket 狀態與 buffer（`ss`）：**

```bash
ss -tanp                 # 所有 TCP socket、狀態、對端、佔用它的 process
ss -tim                  # -i 顯示 TCP 內部資訊：cwnd、rtt、send buffer 等
# 輸出裡的 cwnd:10 rtt:0.3/0.1 就是上面講的壅塞窗與 RTT
ss -tm                   # -m 顯示記憶體用量：skmem(r...,w...) 就是 sk_rmem/sk_wmem
```

**看 qdisc（`tc`）：**

```bash
tc qdisc show dev eth0            # 這張網卡掛的是 fq_codel？pfifo_fast？mq？
tc -s qdisc show dev eth0         # -s 帶統計：送了多少、丟了多少（dropped/overlimits）
# 動手加一個限速的 tbf，親眼看 qdisc 整形流量：
tc qdisc add dev eth0 root tbf rate 1mbit burst 32kbit latency 400ms
#（測完 tc qdisc del dev eth0 root 還原）
```

**看 send syscall（`strace`）：**

```bash
strace -e trace=network -f curl -s http://example.com > /dev/null
# 你會看到 socket() → connect() → sendto()/write() → recvfrom()
# 對慢連線 strace 一個大上傳，會看到 send() 回傳的 byte 數 < 你給的 len，或 EAGAIN
```

**追 `tcp_sendmsg`（`bpftrace`，接 bpf 課）：**

```bash
# 每次有人呼叫 tcp_sendmsg，印出 process 名與送出 byte 數
bpftrace -e 'kprobe:tcp_sendmsg { printf("%-16s send %d bytes\n", comm, arg2); }'

# 統計哪個 process 送最多（arg2 是 size 參數）
bpftrace -e 'kprobe:tcp_sendmsg { @bytes[comm] = sum(arg2); }'
```

`arg2` 是 `tcp_sendmsg(sk, msg, size)` 的 `size`。你可以再掛 `kprobe:ip_queue_xmit`、`kprobe:dev_queue_xmit`、`kprobe:__dev_queue_xmit`，一路看同一個封包穿過各層——這就是把本章那張 ASCII 圖用 bpftrace 在跑的 kernel 上驗證出來。

**看 offload（`ethtool`）：**

```bash
ethtool -k eth0 | grep -E 'segmentation|gso|tso|gro'
# tcp-segmentation-offload: on   ← TSO 開著
ethtool -K eth0 tso off gso off  # 關掉，再 tcpdump 看封包是否變成 MTU 大小
```

## 對比與取捨

| 面向 | TCP send | UDP send |
|---|---|---|
| 入口 | `tcp_sendmsg` | `udp_sendmsg` |
| send buffer 排隊 | 有（`sk_write_queue`），等狀態機 | 幾乎沒有，包好直接下送 |
| 何時真的送 | 壅塞窗/rwnd/Nagle 決定 | 立刻 |
| `send()` 成功語意 | 資料進 kernel buffer（未必上線） | 封包已交給 IP 層 |
| 可靠性/順序 | 有（重傳、序號） | 無 |

| qdisc | 演算法 | 適合 | 代價 |
|---|---|---|---|
| `pfifo_fast` | 3-band 優先權 FIFO | 極簡、低 CPU | 不防 bufferbloat |
| `fq_codel` | 公平佇列 + CoDel 主動丟包 | 通用預設、抗 bufferbloat | 稍複雜 |
| `mq` + 子 qdisc | 每硬體佇列一個 qdisc | 多核高吞吐 NIC | 需硬體多佇列 |
| `htb`/`tbf` | token bucket 限速/分配 | QoS、頻寬管理 | 手動配置、可能成瓶頸 |
| `netem` | 人為加延遲/丟包 | 測試/模擬 | 僅測試用 |

| 切 segment 的方式 | 誰切 | 在哪切 | CPU 省在哪 |
|---|---|---|---|
| 無 offload | kernel | 上層就切成 MSS | 不省，路徑跑 N 次 |
| GSO | kernel 軟體 | 送出前最後一刻 | 省重複跑上層 |
| TSO | 網卡硬體 | 網卡裡 | 連切割都省 |

## 踩雷集錦

1. **以為 `send()` 回傳 = 封包已送出/對端已收到。** 錯。`send()` 成功只代表資料被複製進 kernel send buffer。它可能還躺在 `sk_write_queue` 裡等壅塞窗、等 ARP、等 qdisc。要確認送達得靠上層 ACK 或應用層確認。**正確認識**：`send()` 回傳值是「進了 buffer 幾 bytes」，可能小於你給的 `len`。

2. **`send()` 莫名卡住，以為是網路斷了。** 常見真相是 **send buffer 滿了**（對端收得慢、cwnd 縮小），阻塞模式下 `send()` 就睡在 `sk->sk_wq` 上等 buffer 排空。`ss -tm` 看那個 socket 的 `skmem` 寫側是不是頂到 `sk_sndbuf`。**正確認識**：這是 TCP 流量控制在起作用，不是 bug；要不阻塞就用 non-blocking + epoll。

3. **UDP `sendto()` 成功但封包丟了，怪 kernel。** UDP 沒有可靠性保證。封包可能在**本機 qdisc 就被丟**（佇列滿、或 tc 限速丟包，`tc -s qdisc` 看 `dropped`），更別說出了網卡在網路上丟。**正確認識**：UDP 的丟包要應用層自己處理，本機丟包先查 qdisc 統計和 `netstat -su` 的 `SndbufErrors`。

4. **tcpdump 看到「巨大」的封包（幾萬 bytes），以為 MTU 壞了。** 那是 **TSO/GSO**——tcpdump 掛的點在切割之前，看到的是還沒切開的大 skb。**正確認識**：這是正常 offload 行為；要看真實上線的封包，`ethtool -K eth0 tso off gso off` 關掉 offload 再抓。

5. **`ndo_start_xmit` 回傳後就急著 `kfree(skb)`。** 寫 driver 的經典 use-after-free：skb 記憶體正被網卡 DMA 讀，提早釋放會讓網卡讀到垃圾。**正確認識**：skb 要在 **TX 完成中斷**裡才回收（`dev_kfree_skb`/`napi_consume_skb`），這是 Ch 41 DMA 生命週期的鐵律。

## 進階：再往深一層

- **`sk_buff` 在送包路徑上的 header 是往前長的。** skb 有 `head`/`data`/`tail`/`end` 指標（Ch 43）。送包時 payload 先就位，L4 用 `skb_push` 往 `data` 前面挪出空間填 TCP header，L3 再 push IP header，L2 再 push ethernet header——每下一層在**前面**加自己的 header。所以 skb 配置時 driver 會預留 `NET_SKB_PAD`/`headroom`，讓各層有空間往前 push，不必重配。
- **XPS（Transmit Packet Steering）**：決定某個 CPU 的送包走哪條硬體 TX 佇列（`/sys/class/net/*/queues/tx-*/xps_cpus`），配合 `mq` 讓「同一 CPU 的流固定走同一佇列」，改善快取局部性與鎖爭用（Ch 15 SMP、Ch 7 per-CPU 的網路體現）。
- **Byte Queue Limits（BQL）**：動態調整每條 TX 佇列在硬體 ring 裡積壓多少 bytes，避免 driver ring 變成一個大 buffer 加劇 bufferbloat——把「該讓封包在 qdisc 排隊（可被 CoDel 管理）還是在硬體 ring 排隊（管不到）」這件事做對。
- **`sendmsg` 的零複製路徑**：`MSG_ZEROCOPY`、`sendfile`、io_uring 的送包避開 `copy_from_user`，直接讓網卡 DMA 讀使用者/page cache 的記憶體，省一次大複製——大檔傳輸/CDN 的效能關鍵。
- **面試常問**：「`send()` 回傳成功，資料一定送出去了嗎？」（不一定，進 buffer 而已）「TCP 和 UDP 送包路徑差在哪？」（send buffer 排隊 + 狀態機 vs 直送）「qdisc 是什麼、fq_codel 解決什麼？」（送前排隊層、抗 bufferbloat）「TSO 和 GSO 差別？」（硬體切 vs 軟體切、切的時機）。

## 動手練習

1. **一路追一個封包。** 用 bpftrace 同時掛 `tcp_sendmsg`、`ip_queue_xmit`、`__dev_queue_xmit`、`dev_hard_start_xmit`，然後 `curl` 一個網頁，觀察同一個送出動作依序觸發這幾個函式——把本章的 ASCII 圖用真實 kernel 印出來。（進階：加 `arg`/`stack` 印呼叫堆疊，對照源碼。）

2. **弄慢它，看 send() 阻塞。** 寫一個 client 對一個故意收得很慢（`recv` 後 `sleep`）的 server 狂 `write` 大 buffer。用 `ss -tm` 觀察 client 側 send buffer 頂到 `sk_sndbuf`，並看到 `write()` 卡住。再把 client 改 non-blocking，看它改成回 `EAGAIN`。

3. **用 qdisc 限速並丟包。** `tc qdisc add dev eth0 root netem delay 100ms loss 10%`，然後 `ping` 和 `curl` 同一台機器，親眼看延遲變 100ms、封包丟 10%。`tc -s qdisc show` 看丟包統計。測完 `tc qdisc del dev eth0 root` 還原。這把「qdisc 能整形/丟包」從概念變成手感。

4. **關掉 offload 看封包變形。** `ethtool -k` 記下原狀態，`ethtool -K eth0 tso off gso off`，`tcpdump -i eth0` 抓一個大上傳，對比關前（大封包）關後（MTU 大小封包）。理解為什麼 tcpdump 有時「說謊」。

## 本章重點整理

- **socket 是 file**：`send()` 走 VFS→`struct file`→`struct socket`（協定無關外殼，`proto_ops` 多型）→`struct sock`（協定核心狀態，`sk_prot` 多型）。兩張多型表分工不同。
- **send 下行路徑**：`sock_sendmsg`→`tcp_sendmsg`（複製進 send buffer，狀態機決定何時送）→L3 `ip_output`（FIB 路由查找）→L2 鄰居子系統（ARP 查 MAC）→qdisc（`dev_queue_xmit` 排隊/整形）→driver `ndo_start_xmit`（TX ring + DMA）→送完中斷回收 skb。
- **qdisc** 是送到網卡前的排隊層，預設 `fq_codel` 抗 bufferbloat，`mq` 對應多硬體佇列，`tc`/BPF 掛在這做 QoS/限速/決策。
- **流控與 offload**：send buffer 滿 → 阻塞睡在 wait queue 或回 `EAGAIN`；TSO/GSO 讓堆疊處理大 segment、切割交硬體或延到送出前，是 GRO 的對稱操作。

## 自我檢核

- [ ] 不看筆記，能畫出 `send()` 從 socket 到網卡的完整下行路徑，說出每層加什麼 header、做什麼決策
- [ ] 能解釋 `struct socket` 和 `struct sock` 的分工，以及 `proto_ops` 和 `proto` 兩張多型表各管什麼
- [ ] 能說出為什麼 `send()` 回傳成功不代表封包已上線，以及 TCP 何時才真的送（壅塞窗/Nagle）
- [ ] 面試被問「qdisc 是什麼、fq_codel 解決什麼問題」，能答出送前排隊層 + 抗 bufferbloat
- [ ] 能解釋 TSO 和 GSO 的差別，以及它們為何是 GRO 的對稱操作
- [ ] 知道 send buffer 滿時阻塞/非阻塞 socket 各自的行為，並能用 `ss -tm` 看出來

## 延伸閱讀

### 官方文件

- **[Documentation/networking/scaling.rst](https://www.kernel.org/doc/html/latest/networking/scaling.html)**
  - **讀哪裡**：整篇，重點在 XPS（Transmit Packet Steering）與多佇列送包那幾節
  - **和本章的關聯**：本章「進階」提到的 XPS/BQL、qdisc 對應多硬體佇列的 scaling 細節在這裡；想理解高吞吐送包怎麼利用多 CPU 必讀

- **[Documentation/networking/ 的 qdisc / tc 相關](https://www.kernel.org/doc/html/latest/networking/index.html)** 與 **`man tc`、`man tc-fq_codel`**
  - **讀哪裡**：`tc-fq_codel(8)`、`tc-htb(8)`、`tc-netem(8)` man page
  - **能學到什麼**：每個 qdisc 的參數與適用場景，配本章的動手練習 3 一起做

### 深入文章

- **[Illustrated Guide to Monitoring and Tuning the Linux Networking Stack: Sending Data](https://blog.packagecloud.io/monitoring-tuning-linux-networking-stack-sending-data/)** — Joe Damato / packagecloud
  - **讀哪裡**：整篇，這是送包路徑的逐函式導覽，和本章互為表裡但更細（每個 sysctl、每個計數器都點到）
  - **前提**：跟完本章有了骨架後讀這篇補血肉；它對應的「收包版」是 Ch 44 的最佳延伸
  - **注意**：文章基於較舊 kernel，函式大方向不變，細節以 6.12 源碼為準

- **[LWN: The QUIC in the Linux kernel / bufferbloat 系列](https://lwn.net/Kernel/Index/#Networking-Bufferbloat)** — LWN.net
  - **讀哪裡**：bufferbloat 與 CoDel/fq_codel 的文章
  - **為什麼值得讀**：fq_codel 為什麼成為預設、CoDel 怎麼靠量測佇列延遲主動丟包——設計動機的一手記錄，比 man page 講得深

### 書籍

- **《Understanding Linux Network Internals》** — Christian Benvenuti（O'Reilly, 2005）
  - **這本書的定位**：網路堆疊的經典大部頭，neighbour/ARP、路由/FIB、送收路徑講得最完整
  - **注意**：講的是 2.6，`struct` 欄位與函式名有變動，但**架構骨架**（socket→sock、鄰居子系統、qdisc 定位）至今適用；當地圖用，細節回 v6.12 源碼對

送包路徑到這裡走完——封包已經上線。但在它出網卡之前、進網卡之後，還有兩個能攔截/改寫/丟棄它的強大掛載點：netfilter（iptables/nftables 的防火牆與 NAT）和 XDP（在 driver 最早期用 BPF 處理封包）。下一章我們看這兩套 hook 怎麼嵌進收送路徑。

→ [Ch 46 netfilter/nftables hook 與 XDP](./46-netfilter-xdp.md)
