# Ch 43 — sk_buff 與 net_device 抽象

> **目標**：從 kernel 內部看懂「一個封包」和「一張網卡」在源碼裡到底是什麼。學完你能畫出 `struct sk_buff` 的 head/data/tail/end 四指標、解釋每一層協定怎麼靠移動指標（而非複製資料）加/剝 header、看懂 `struct net_device` 與 `net_device_ops` 怎麼把一張實體網卡接進網路堆疊，並用 bpftrace / ip / ethtool / sysfs 把這兩個結構在你眼前印出來。

> **環境**：延續 Ch 0 的 QEMU + gdb。這章多數觀測可以在 host 直接做（不需要進 QEMU），因為 `ip`、`ethtool`、`/sys/class/net/`、bpftrace 用的都是你主機上正在跑的 kernel。要 gdb 停在 skb 函式上時再進 QEMU。

## 為什麼需要這個？

你在 `networking` 課學過 TCP/IP：一個封包外面包 Ethernet frame，裡面是 IP header，再裡面是 TCP header，最裡面才是 payload。那是**協定使用者**的視角——你看到的是「線上的位元組長什麼樣」。

這章換一個視角：**kernel 內部**。同一個封包，從網卡進來、往上穿過 driver → IP → TCP、最後投遞給某個 socket；或反過來從 socket 出發、往下穿過 TCP → IP → driver、最後排到網卡的傳送佇列。問題來了——這一路上，**這個封包在記憶體裡是什麼？誰持有它？每層要加自己的 header，難道每層都把整包資料複製一份、往前挪出空間再貼上 header 嗎？**

如果真的每層都複製，一個封包穿過四層就複製四次，10Gbps 網卡每秒上百萬個封包，記憶體頻寬直接被複製吃光。所以 kernel 需要一個**能被所有協定層共用、加/剝 header 不搬資料、還能被多個消費者共享**的封包表示法。這個東西叫 `sk_buff`（socket buffer，通稱 skb），是整個 Linux 網路堆疊的中心資料結構。它設計得複雜，複雜的每一分都是為了「零複製」和「跨層共用」買單。

另一半問題是：封包從哪張網卡進來、要從哪張網卡出去？`eth0`、`lo`、`wlan0` 這些介面在 kernel 裡是 `struct net_device`。它既是**抽象**（IP 層只認得「一個能收送封包的介面」，不管底下是 Intel 網卡還是 virtio），也是**驅動的掛鉤點**（Ch 40 那張 PCI 網卡的驅動，最終就是填一個 `net_device` 交給 kernel）。

這一章講**承載封包的資料結構**，是 Part 8 的地基。Ch 44 講封包怎麼**收**（NAPI、`NET_RX` softirq、GRO）、Ch 45 講怎麼**送**（socket、qdisc）、Ch 46 講怎麼被 **hook**（netfilter、XDP）——三章都在操作這章的 skb 和 net_device。

## 先建立直覺

先把一個封包在 kernel 裡的「一生」畫出來，你才知道 skb 為什麼要長那樣。

```
   收（RX）：網卡 → socket，往上穿，每層剝掉自己的 header
   ┌──────────────────────────────────────────────────────────────┐
   │  網卡 DMA 進 RAM ─► driver 包成 skb                            │
   │        │                                                       │
   │   L2 以太網層：認出 EtherType=IPv4，data 指標往後跳過 14 byte │
   │        ▼            （skb_pull，剝掉 Ethernet header）         │
   │   L3 IP 層：讀 IP header，決定 local deliver / forward         │
   │        │            data 往後跳過 20 byte（剝 IP header）      │
   │        ▼                                                       │
   │   L4 TCP 層：讀 TCP header，找到對應的 socket                  │
   │        │            data 往後跳過 TCP header                   │
   │        ▼                                                       │
   │   socket 收佇列 ─► 使用者 recv() 拿到 payload                  │
   └──────────────────────────────────────────────────────────────┘

   送（TX）：socket → 網卡，往下穿，每層加上自己的 header
   ┌──────────────────────────────────────────────────────────────┐
   │  send() 的資料 ─► 配一個 skb，前面留好 headroom                │
   │        │                                                       │
   │   L4 TCP：data 往前推，寫入 TCP header（skb_push）             │
   │        ▼                                                       │
   │   L3 IP：data 再往前推，寫入 IP header                          │
   │        ▼                                                       │
   │   L2 以太網：data 再往前推，寫入 Ethernet header               │
   │        │                                                       │
   │        ▼                                                       │
   │   netdev 的傳送佇列 ─► driver ndo_start_xmit ─► 網卡 DMA 出去 │
   └──────────────────────────────────────────────────────────────┘
```

關鍵洞察：**收是一路 `skb_pull`（data 往後、剝 header），送是一路 `skb_push`（data 往前、加 header），資料本身一個 byte 都沒搬**。這靠的就是 skb 那組指標。下面正式進源碼。

## sk_buff：kernel 眼中的「一個封包」

結構定義在 `include/linux/skbuff.h` 的 `struct sk_buff`。它非常大（幾十個欄位），但你先只需要抓住兩組東西：**指標**（描述資料在哪）和 **metadata**（描述這是什麼封包）。

### head / data / tail / end：四個指標與零複製的核心

封包的實際位元組放在一塊**線性 buffer** 裡（`kmalloc` 出來的 data area）。skb 用四個指標框住這塊 buffer：

```
   一塊 data area（kmalloc 出來的連續記憶體）：

   head          data              tail            end
    │             │                 │               │
    ▼             ▼                 ▼               ▼
    ┌─────────────┬─────────────────┬───────────────┐
    │  headroom   │   封包資料       │   tailroom    │
    │ （可加 hdr） │ （data..tail）  │ （可加 data） │
    └─────────────┴─────────────────┴───────────────┘
    │◄───────────────  end - head  ───────────────►│

   len   = tail - data      有效資料長度
   headroom = data - head   前面還能塞多少 header
   tailroom = end  - tail   後面還能追加多少資料
```

- `head`：整塊 buffer 的起點，配置後就固定不動
- `end`：整塊 buffer 的終點，也固定不動
- `data`：**有效資料的起點**，會移動——這是玄機所在
- `tail`：**有效資料的終點**，追加資料時往後移

> 實作細節：`sk_buff` 裡 `head`/`data` 是真指標，`tail`/`end` 在 64 位元架構上是相對 `head` 的偏移量（`sk_buff_data_t` 是 `unsigned int`），省 8 byte × 上百萬個 skb 就是可觀的記憶體。要取真位址用 `skb_tail_pointer(skb)` / `skb_end_pointer(skb)`，別直接把 `skb->tail` 當指標印。這是很多人第一次讀 skb 源碼的困惑點。

四個操作指標的函式（都在 `include/linux/skbuff.h`）：

| 函式 | 動作 | 用在哪 |
|---|---|---|
| `skb_reserve(skb, n)` | `data` 和 `tail` 同時往後移 n，**撐出 headroom** | 剛配好 skb、還沒放資料時，先預留給下層 header 的空間 |
| `skb_put(skb, n)` | `tail` 往後移 n，`len += n`，回傳原 `tail` | 往資料尾巴**追加** n bytes（例如 driver 收到 payload） |
| `skb_push(skb, n)` | `data` 往前移 n，`len += n`，回傳新 `data` | **加 header**：往前挪出 n bytes 給你寫 header（送包每層做這件事） |
| `skb_pull(skb, n)` | `data` 往後移 n，`len -= n` | **剝 header**：跳過前面 n bytes（收包每層做這件事） |

看一次送包時 header 怎麼長出來（ASCII，data 指標往前爬）：

```
   ① 剛配好，skb_reserve 撐出 headroom，資料在 data..tail：
      head        data                tail       end
       │    (headroom) │  TCP payload  │          │
       └──────────────►│───────────────│◄─────────┘

   ② TCP 層 skb_push(20)：data 往前跳 20，寫入 TCP header
      head    data                    tail
       │  │ TCP │  TCP payload  │
       └─►│─────│───────────────│    ← data 左移，len 變大

   ③ IP 層 skb_push(20)：data 再往前跳 20，寫入 IP header
      head data
       │ │ IP │ TCP │  payload  │
       └►│────│─────│───────────│    ← 又左移

   ④ 以太網層 skb_push(14)：data 再往前，寫入 Ethernet header
     head/data
       │ ETH │ IP │ TCP │ payload │   ← headroom 幾乎用完，
       └─────────────────────────┘      這就是為什麼配 skb 要預留足夠 headroom
```

收包剛好反過來：每層 `skb_pull` 把 `data` 往右推、跳過自己那層的 header，`data` 最後停在 payload 開頭，交給 socket。**整段記憶體從頭到尾沒被複製、沒被搬移，只有 `data` 這個游標在爬。** 這就是 skb 設計的靈魂。

> 為什麼收包要預留 headroom？網卡驅動配收包 buffer 時會呼叫類似 `netdev_alloc_skb`，它預設保留 `NET_SKB_PAD`（一個對齊過的 headroom 常數，`include/linux/skbuff.h`）。這樣萬一封包要被**轉發**（forward）或加隧道封裝（VXLAN/GRE 再包一層外層 header），有空間 `skb_push` 而不必重配 buffer。headroom 不足時 kernel 得 `skb_cow_head` 重新配一塊更大的、複製過去——那就是你想避免的複製。

### metadata：這是什麼封包、從哪來、header 在哪

skb 不只裝資料，還帶一堆 metadata（同樣在 `struct sk_buff`）：

- `dev`（`struct net_device *`）：這個 skb 綁在哪張網卡上。收包時是進來的介面，送包時是要出去的介面
- `protocol`（`__be16`）：L3 協定，例如 `ETH_P_IP`（0x0800）。L2 層剝完 Ethernet header 後填這欄，IP 層才知道要不要收
- `sk`（`struct sock *`）：這個 skb 屬於哪個 socket（本機發送/接收時）
- **各層 header 的位置**：`transport_header`、`network_header`、`mac_header`（都是相對 `head` 的偏移）。搭配 `tcp_hdr(skb)`、`ip_hdr(skb)`、`eth_hdr(skb)` 這些 inline 函式，各層可以直接拿到自己 header 的指標，不必自己算偏移
- `csum` / `ip_summed`：checksum 狀態。網卡若支援 checksum offload，kernel 就不自己算，`ip_summed` 標成 `CHECKSUM_UNNECESSARY`（收）或 `CHECKSUM_PARTIAL`（送，交給網卡算）
- `hash`：流的 hash 值（RSS/RPS 用來把同一條流固定分到同一個 CPU/佇列，接下面多佇列）
- `tstamp`：時間戳（tcpdump 的時間、`SO_TIMESTAMP` 都靠它）
- `queue_mapping`：對應到 netdev 的哪個傳送佇列（多佇列網卡用）

這解釋了為什麼 skb 那麼肥：它是**所有協定層共用的一張工作台**，每層都在上面記自己需要的東西。分層乾淨的代價，就是這個結構欄位多。

### 非線性 skb：分片、frags、與零複製

前面說封包資料在「一塊線性 buffer」——那是簡化。真實世界的 skb 常常是**非線性（nonlinear）**的：一部分資料在線性 buffer（`head..end`），其餘散在一堆 page 裡。

管這些額外 page 的結構是 `struct skb_shared_info`，它就**貼在線性 buffer 的尾巴**（`end` 之後），用 `skb_shinfo(skb)` 取得。裡面關鍵欄位：

- `nr_frags` + `frags[]`：一個 `skb_frag_t` 陣列，每個指向一個 page（接 Ch 17 buddy allocator 的 page）+ 頁內 offset + 長度
- `frag_list`：把多個 skb 串成一串（IP 分片重組、GSO 用）
- `gso_size` / `gso_segs` / `gso_type`：GSO（見下）用的分段資訊

為什麼要搞非線性？兩個核心理由：

1. **零複製（zero-copy）**：`sendfile()` 把檔案送出去時，page cache 裡的 page（Ch 21）可以**直接**掛進 skb 的 `frags[]`，不必先複製到一塊線性 buffer 再送。省一次大複製。`MSG_ZEROCOPY` 送使用者資料也是同理
2. **大封包聚合**：現代網卡一次收/送的資料量遠大於一個 MTU（1500 byte）。硬把它塞進一塊連續 `kmalloc` buffer 既浪費又難配（連續大塊記憶體難求，Ch 17 講過碎片化）。拆成一堆 page 掛在 `frags[]` 就沒這問題

這牽出 **GSO/GRO**（Ch 44/45 深入，這裡先點）：

- **GSO（Generic Segmentation Offload）**：送包時，TCP 層先做一個**超大**的 skb（例如 64KB），一路往下傳，到最後（driver 或網卡硬體）才切成一堆 MTU 大小的封包。好處是協定堆疊只跑一次、切割延到最後——切得越晚、每個封包攤到的 per-packet 成本越低
- **GRO（Generic Receive Offload）**：收包時反過來，把同一條流的多個小封包**合併**成一個大 skb 再往上送，讓上層協定堆疊少跑幾趟。合併的那些封包就掛在 `frags[]` / `frag_list` 裡

所以「非線性 skb」不是邊角案例，而是高效能收送的常態。你在 Ch 44/45 會看到 GRO/GSO 怎麼實際操作這些 frags。

### clone vs copy：多個 skb 共享同一塊資料

一個封包常常需要「被看很多次」：tcpdump 要抓一份、netfilter 可能要複製、轉發時原 skb 還在。全都深複製太貴。skb 提供兩種層次的共享：

```
   skb_clone()：複製 sk_buff 結構（那些指標和 metadata），
                但兩個 sk_buff 指向【同一塊 data area】
                data area 用 skb_shared_info.dataref 這個 refcount 管（接 Ch 24）

     skb_A ──┐
             ├──► [ 同一塊 data area ]   dataref = 2
     skb_B ──┘

   誰想改資料，得先 skb_unshare / pskb_copy 把 data area 拆開來自己一份
   （copy-on-write 的味道，接 Ch 20 的 CoW）
```

- `skb_clone(skb, ...)`：只複製 `sk_buff` 這個殼，資料共享。快。但誰都不准改共享的 data。tcpdump/`AF_PACKET` 抓包就是 clone
- `skb_copy(skb, ...)`：連資料一起深複製，各玩各的
- `pskb_copy` / `skb_copy_expand`：中間路線，複製線性部分、frags 共享或擴充 headroom

skb 本身也有一個 refcount：`skb->users`（`refcount_t`）。`kfree_skb()` 遞減，歸零才真正釋放。這正是 Ch 24 講的 refcount 模式，在 skb 上有兩層——`skb->users`（誰持有這個 sk_buff）和 `skb_shinfo->dataref`（誰共享這塊 data area）。搞混這兩層是讀網路源碼常見的坑。

> 認識論誠實：上面把 clone/copy 講成乾淨兩層，實際 API 更多（`skb_get`、`consume_skb` vs `kfree_skb` 的語意差別、`skb_orphan` 斷開 socket 歸屬…）。你先抓住「clone 共享資料、copy 不共享、兩層 refcount」這個骨架，細節在 `net/core/skbuff.c` 裡對照函式讀。

## net_device：kernel 眼中的「一張網卡」

結構在 `include/linux/netdevice.h` 的 `struct net_device`。一個 `net_device` 代表一個網路介面——不一定是實體網卡：`lo`（loopback）、`eth0`（實體）、`wlan0`（無線）、`docker0`（bridge）、`tun0`（VPN 虛擬介面，接 `networking` 課的 VPN）全都是 `net_device`。這正是**抽象的價值**：IP 層只跟「一個能收送 skb 的介面」打交道，底下是什麼一律不管。

`net_device` 裡的關鍵欄位：

- `name`（`eth0`）、`ifindex`（介面編號，`ip link` 看到的那個數字）
- `dev_addr`（MAC 位址）、`mtu`（最大傳輸單元，預設 1500）
- `netdev_ops`（`struct net_device_ops *`）：**這張網卡的操作函式表**（見下，Ch 37 的 ops 多型在網路子系統的化身）
- `ethtool_ops`：`ethtool` 指令背後呼叫的操作表
- `stats` / `tstats`：收送封包/byte/error 的統計（`ip -s link` 印的就是這些）
- `_tx[]`（`struct netdev_queue`）+ `num_tx_queues`、`_rx[]` + `num_rx_queues`：**多個收送佇列**（見下多佇列）
- `flags`（`IFF_UP`、`IFF_LOOPBACK`、`IFF_PROMISC`…）：介面狀態旗標

### net_device_ops：驅動填、kernel 呼叫的操作表

`struct net_device_ops`（`include/linux/netdevice.h`）是一組 function pointer，網卡驅動實作它們、填進 `net_device->netdev_ops`，kernel 需要對這張網卡做事時就呼叫對應的 `ndo_*`（**n**et **d**evice **o**peration）。這跟 Ch 37 device model 的 ops 表、Ch 33 VFS 的 `file_operations` 是同一套「多型靠 function pointer 表」的設計哲學。

最核心的幾個：

| ndo | 何時被呼叫 | 驅動要做什麼 |
|---|---|---|
| `ndo_open` | `ip link set eth0 up` | 配 DMA ring、開中斷、啟動網卡、啟動佇列 |
| `ndo_stop` | `ip link set eth0 down` | 停佇列、關中斷、釋放資源 |
| `ndo_start_xmit` | **kernel 要送一個 skb 出去** | 把 skb 塞進網卡的 TX ring、觸發 DMA 傳送。這是送包路徑（Ch 45）的終點 |
| `ndo_get_stats64` | `ip -s link` | 回報收送統計 |
| `ndo_set_rx_mode` | 群播/promiscuous 變更 | 設定網卡的收包過濾 |
| `ndo_change_mtu` | `ip link set eth0 mtu 9000` | 改 MTU |

`ndo_start_xmit` 是整張表的主角：**送包路徑的所有努力，最後都收斂到「呼叫這張網卡的 `ndo_start_xmit(skb, dev)`」**。它回傳 `NETDEV_TX_OK`（收下了）或 `NETDEV_TX_BUSY`（佇列滿，稍後重試）。Ch 40 你列舉出來的那張 PCI 網卡，它的驅動最主要的工作就是實作這個函式，把 skb 的資料（線性部分 + frags）設定成網卡看得懂的 descriptor。

一張實體網卡的驅動（例如 Intel 的 `igb`、`ixgbe`，或 QEMU 常用的 `virtio_net`）在 probe（Ch 40 PCI probe）時大致做：`alloc_etherdev()` 配一個 `net_device` → 填好 `netdev_ops`、`ethtool_ops`、MAC、佇列數 → `register_netdev()` 註冊進 kernel。註冊完，這張網卡就出現在 `ip link` 裡、能被指定 IP、能收送封包了。

### napi_struct 與多佇列（Ch 44 深入，這裡點一下）

兩個和效能直接相關、Ch 44/45 會展開的東西，先在這裡建立座標：

- **`struct napi_struct`**（`include/linux/netdevice.h`）：NAPI 是「收包時，第一個封包用中斷通知，之後**關中斷改用輪詢（poll）**批次收」的機制。為什麼？10Gbps 網卡每秒上百萬封包，每個都觸發一次中斷，CPU 會被中斷風暴淹死（interrupt storm）。NAPI 讓網卡在忙時切成輪詢模式，一次收一批，攤平中斷成本。每個 `napi_struct` 綁一個收包佇列，Ch 44 是它的主場

- **多佇列 netdev（multiqueue）**：現代網卡有多個硬體收送佇列（`num_rx_queues` / `num_tx_queues`）。收包時網卡用 **RSS（Receive Side Scaling）** 按封包的流 hash 把不同的流分到不同佇列，每個佇列的中斷可以綁到不同 CPU（接 Ch 15 的 IRQ affinity / RSS）。這樣多核心才能並行處理收包，不會全擠在 CPU 0。`skb->queue_mapping` 和 `skb->hash` 就是為此存在。你用 `ethtool -l eth0` 看得到佇列數、`ethtool -x eth0` 看得到 RSS 的分流表

## 動手：把 skb 和 net_device 印出來

這節全部可以在**你的 host**（正在跑的 kernel）上做，不必進 QEMU。

### 用 ip / ethtool / sysfs 看 net_device

```bash
# 列出所有 net_device（每一行就是一個 struct net_device）
ip link show
# 1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 ...   ← lo 也是 net_device
# 2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 ...

# 帶統計（印的是 net_device->stats）
ip -s link show eth0

# 網卡的收送佇列數（num_rx_queues / num_tx_queues）
ethtool -l eth0

# 網卡支援的 offload 特性（GSO/GRO/checksum offload 開了沒）
ethtool -k eth0 | grep -E 'gro|gso|tx-checksum|rx-checksum'

# RSS 分流表（多佇列怎麼把流分到不同佇列）
ethtool -x eth0

# sysfs 視角：每個 net_device 在這裡有一個目錄（接 Ch 37 sysfs）
ls /sys/class/net/
cat /sys/class/net/eth0/mtu
cat /sys/class/net/eth0/address        # MAC，對應 dev_addr
ls /sys/class/net/eth0/queues/         # rx-0/ tx-0/ ... 就是多佇列
```

`/sys/class/net/eth0/` 這個目錄是 Ch 37 device model 的直接產物——`register_netdev` 時 kernel 幫每個 `net_device` 建了一個 kobject，sysfs 就是它的鏡像。你在這裡看到的每個檔案，背後都是 `net_device` 的一個欄位。

### 用 bpftrace 印 skb 內容（接 bpf 課）

skb 每經過一個關鍵函式，我們就可以用 kprobe 攔下來看它。`net/core/dev.c` 的 `__netif_receive_skb_core`（收包核心，Ch 44 主角）是個好觀測點——每個收進來的封包都經過它：

```bash
# 每收一個封包，印出它從哪張網卡進來、protocol、長度
sudo bpftrace -e '
kprobe:__netif_receive_skb_core {
    $skb = (struct sk_buff *)arg0;
    printf("dev=%s proto=0x%x len=%d\n",
           $skb->dev->name, $skb->protocol, $skb->len);
}'
```

跑起來後在另一個終端 `ping 8.8.8.8`，你會看到 ICMP 封包一個個被印出來，`dev` 是你的網卡名、`proto` 是 0x0008（`ETH_P_IP` 的網路位元組序）。這就是 `bpf` 課的 XDP/tc BPF 在做的事——**skb 是它們的操作對象**（tc BPF 直接改 skb、XDP 更早在 skb 生成前於 `xdp_buff` 上動手，Ch 46/52 展開）。

想看 headroom/tailroom：

```bash
sudo bpftrace -e '
kprobe:ip_rcv {        // net/ipv4/ip_input.c，L2 剛把封包交給 IP 層
    $skb = (struct sk_buff *)arg0;
    printf("len=%d headroom(data-head)=%d\n",
           $skb->len, $skb->data - $skb->head);
}'
```

`data - head`（headroom）此刻通常還留著給下層轉發用的空間。這把前面 ASCII 圖裡的抽象指標變成你螢幕上的具體數字。

### 用 gdb 停在 skb（進 QEMU）

要看結構全貌，進 QEMU + gdb（Ch 0 的環境）：

```gdb
(gdb) break __netif_receive_skb_core
(gdb) continue
# 在 QEMU 裡製造流量（例如 ping loopback），觸發中斷點
(gdb) print *skb                    # 印整個 sk_buff
(gdb) print skb->data - skb->head   # headroom
(gdb) print/x skb->protocol
(gdb) print skb->dev->name          # 從哪張 net_device 進來
```

### 寫模組：用 netdev notifier 監聽介面上下線

kernel 提供一個通知鏈（notifier chain），介面狀態變化（up/down、註冊/註銷、改名…）時會通知所有註冊者。這是驅動之外的程式（例如 bridge、bonding、你的模組）得知網路拓撲變化的標準管道。註冊函式 `register_netdevice_notifier`（`net/core/dev.c`）：

```c
// netdev_watch.c —— 監聽網路介面上下線
#include <linux/module.h>
#include <linux/netdevice.h>
#include <linux/notifier.h>

static int netdev_event(struct notifier_block *nb,
                        unsigned long event, void *ptr)
{
    // ptr 帶著發生事件的 net_device
    struct net_device *dev = netdev_notifier_info_to_dev(ptr);
    const char *what;

    switch (event) {
    case NETDEV_UP:         what = "UP";          break;
    case NETDEV_DOWN:       what = "DOWN";        break;
    case NETDEV_REGISTER:   what = "REGISTER";    break;
    case NETDEV_UNREGISTER: what = "UNREGISTER";  break;
    case NETDEV_CHANGEMTU:  what = "CHANGEMTU";   break;
    default:                return NOTIFY_DONE;   // 不關心的事件放行
    }

    pr_info("netdev_watch: %s %s (ifindex=%d, mtu=%u)\n",
            what, dev->name, dev->ifindex, dev->mtu);
    return NOTIFY_OK;
}

static struct notifier_block nb = { .notifier_call = netdev_event };

static int __init nw_init(void)
{
    // 註冊當下，kernel 會對【已存在】的每個 net_device 補送一次
    // NETDEV_REGISTER + NETDEV_UP，所以你會先看到現有介面被列出來
    return register_netdevice_notifier(&nb);
}
static void __exit nw_exit(void)
{
    unregister_netdevice_notifier(&nb);
}
module_init(nw_init);
module_exit(nw_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Watch net_device up/down events");
```

`insmod netdev_watch.ko` 後，另一個終端 `sudo ip link set lo down` / `up`，`dmesg` 會即時印出事件。注意 `nw_init` 一註冊就會收到現有介面的 `REGISTER`/`UP`——這是 notifier 的貼心設計，讓你不會漏掉註冊前就存在的介面。這個模組是理解「kernel 內部誰在關心網路拓撲」的最小範例，也預告了 Ch 46 netfilter 用類似機制掛 hook。

## 對比與取捨

| 議題 | 做法 A | 做法 B | 取捨 |
|---|---|---|---|
| 加/剝 header | 移動 `data` 指標（push/pull） | 每層複製整包資料 | A 零複製、快，代價是要預留 headroom；B 直觀但每層一次複製，高速下不可行。kernel 選 A |
| 大封包表示 | 線性 `kmalloc` 一大塊 | 非線性：frags 掛 page | 線性簡單但要連續大記憶體（難配、浪費）；非線性省記憶體、支援零複製，代價是所有存取都要判斷「這段在線性區還是 frags」 |
| 讓多方看同一封包 | `skb_clone`（共享資料） | `skb_copy`（各一份） | clone 快、省記憶體，但誰都不准改共享資料；copy 貴但獨立可改。抓包用 clone、要改的路徑用 copy |
| 收包通知 | 純中斷（每包一個 IRQ） | NAPI（中斷起頭、輪詢批收） | 低流量下中斷延遲低；高流量下中斷會淹死 CPU。NAPI 兩者兼顧，是現代標配（Ch 44） |
| 多核心收包 | 單佇列（全進 CPU 0） | 多佇列 + RSS（按流分佇列） | 單佇列簡單但 CPU 0 成瓶頸；多佇列並行但要硬體支援、要調 IRQ affinity（Ch 15） |

## 踩雷集錦

1. **錯誤直覺：「`data` 指標指向封包最外層 header」。** 正確：`data` 指到**當前這層看到的開頭**，會隨處理層層移動。剛從網卡進來時 `data` 指 Ethernet header；到了 TCP 層，`data` 已被前面各層 `skb_pull` 推到 TCP header（甚至 payload）。要拿特定層的 header，用 `eth_hdr()` / `ip_hdr()` / `tcp_hdr()`，它們讀的是 `mac_header`/`network_header`/`transport_header` 這些**記錄好的偏移**，不是靠 `data`。

2. **錯誤直覺：「skb 的資料一定在 `data..tail` 這塊連續記憶體裡」。** 正確：非線性 skb 的資料一部分在 frags 的 page 裡，`skb->len`（總長）會大於 `skb_headlen(skb)`（線性部分長度）。想安全讀取全部資料，用 `skb_copy_bits()` 或先 `skb_linearize()`（會複製、變慢，非必要別用）。直接 `memcpy(skb->data, ..., skb->len)` 在非線性 skb 上會讀過頭、拿到垃圾。

3. **錯誤直覺：「`skb->tail` 是指標，我 print 它就是位址」。** 正確：64 位元上 `tail`/`end` 是相對 `head` 的偏移量。要真位址用 `skb_tail_pointer(skb)`。直接把偏移量當指標印會得到一個很小的數字，一頭霧水。

4. **錯誤直覺：「clone 出來的 skb 我可以直接改 payload」。** 正確：`skb_clone` 兩個 skb 共享 data area（`dataref > 1`）。你一改就污染了另一個持有者（例如正在抓包的 tcpdump）。要改先 `skb_unshare` / `skb_ensure_writable` 把資料拆成自己一份。這是 CoW（接 Ch 20）在 skb 上的體現。

5. **錯誤直覺：「headroom 不夠就自動長出來」。** 正確：`skb_push` 若超過 headroom 會踩到 `head` 之前、是 bug（debug kernel 會 `BUG()`）。轉發/加封裝前要先 `skb_cow_head()` 確保 headroom 夠，不夠就重配 + 複製。這也是為什麼收包 buffer 一開始就預留 `NET_SKB_PAD`——把重配的機率壓低。

6. **錯誤直覺：「`kfree_skb` 和 `consume_skb` 一樣」。** 兩者都釋放 skb，但語意不同：`kfree_skb` 表示「這個封包被**丟棄**」（drop，會被 `drop_monitor` / dropwatch 記錄，`net/core/skbuff.c`），`consume_skb` 表示「這個封包**正常處理完了**」。用錯會讓 drop 監控的統計失真。debug 封包神秘消失時，`kfree_skb` 的 tracepoint（`skb:kfree_skb`，帶 drop reason）是第一個要看的地方。

## 進階：再往深一層

- **`drop reason`**：6.4 起 `kfree_skb_reason()` 帶一個 `enum skb_drop_reason`（`include/net/dropreason-core.h`），精確說明封包為什麼被丟（checksum 錯、no socket、被 netfilter 擋…）。`perf trace -e skb:kfree_skb` 或 `retis` 工具能把「封包死在哪、為什麼死」看得清清楚楚。這是生產環境查「封包神秘消失」的利器，比以前只知道「drop 了」強太多。

- **skb 的記憶體來源**：`sk_buff` 結構本身從一個專用 slab cache（v6.12 是 `net_hotdata.skbuff_cache`，`net/core/skbuff.c`；6.8 前叫 `skbuff_head_cache`）配出來，data area 從一般 kmalloc slab 配（Ch 18）。高速路徑用 per-CPU 的 `napi_alloc_cache` 做批次配置與 skb 回收（recycle），避免每個封包都走一趟完整的 slab 配置。這是 Ch 7（per-CPU）+ Ch 18（slub）在網路熱路徑的具體應用。

- **`sk_buff` 為什麼不用 folio**：mm 子系統在推 folio（Ch 21），但 skb 的 frags 仍以 page 為單位。網路對「一段連續資料」的需求和 page cache 不同，兩邊演進步調不一致——這是「kernel 各子系統各有歷史包袱」的一個實例，面試被問到 skb 與 mm 的關係時值得提。

- **面試常問**：「一個 TCP 封包從 `send()` 到網卡，skb 經歷什麼？」——答題骨架：socket 層配 skb 並預留 headroom（給下面各層 header）→ TCP 層 `skb_push` 寫 TCP header、算 checksum（或標 `CHECKSUM_PARTIAL` 交網卡）→ IP 層 `skb_push` 寫 IP header、查路由決定 `dev` → 鄰居子系統 `skb_push` 寫 Ethernet header → 進 qdisc 排隊（Ch 45）→ driver 的 `ndo_start_xmit` 把 skb 設成網卡 descriptor → DMA 送出、送完 `consume_skb`。能把「每層 `skb_push` 加自己的 header、資料不複製」講清楚就贏一半。

## 動手練習

1. **看四指標的實際數字**：用上面的 bpftrace 一行式，在 `ip_rcv` 印 `skb->len`、`skb->data - skb->head`（headroom）、`skb_tail_pointer(skb) - skb->head`（到 tail 的偏移）。ping 一個位址，記下數字，對照本章 ASCII 圖。想想：為什麼 headroom 不是 0？

2. **證明非線性 skb 存在**：`ethtool -K eth0 gro on` 開 GRO，用 `bpftrace` 在收包函式印 `skb->len` vs `skb_headlen(skb)`（線性部分長度），拉一個大檔案（`curl` 下載）製造流量。你會看到 `len > headlen` 的 skb——那就是被 GRO 合併、資料掛在 frags 的非線性 skb。關掉 GRO（`gro off`）再看，差別很明顯。

3. **netdev notifier 模組**：把本章的 `netdev_watch.ko` 編出來、`insmod`，然後 `ip link add dummy0 type dummy` / `ip link set dummy0 up` / `ip link del dummy0`，看 `dmesg` 印出 REGISTER/UP/DOWN/UNREGISTER。加一個 case 印出 `NETDEV_CHANGEMTU`，然後 `ip link set dummy0 mtu 9000` 觸發它。

4. **gdb 追一個封包的 data 指標**：QEMU 裡 `break ip_rcv` 和 `break tcp_v4_rcv`（`net/ipv4/tcp_ipv4.c`），比較同一個封包在這兩個中斷點的 `skb->data - skb->head`——後者應該更大（IP 層已經 `skb_pull` 過了）。這把「收包一路 pull」變成你眼前的證據。

5. **弄壞它**：寫個小模組，clone 一個 skb 後直接改它的 data（跳過 `skb_unshare`），觀察 dmesg（在 debug kernel + `SKB` 相關 check 下可能觸發警告）。體會為什麼共享 data 不准直接改。

## 本章重點整理

- `sk_buff` 是 kernel 表示「一個封包」的核心結構，用 `head`/`data`/`tail`/`end` 四指標框住一塊 data area；加/剝 header 靠移動 `data`（`skb_push`/`skb_pull`），**資料一個 byte 都不搬**，這就是零複製的骨幹。
- skb 帶大量 metadata（`dev`、`protocol`、各層 header 偏移、csum、hash、tstamp），因為它是所有協定層共用的工作台；非線性 skb 把額外資料掛在 `skb_shared_info.frags[]` 的 page 上，支撐零複製與 GSO/GRO。
- clone 共享 data area（`dataref` refcount）、copy 深複製；skb 有兩層 refcount（`skb->users` 管 sk_buff、`dataref` 管 data area），改共享資料前要先拆開（CoW）。
- `net_device` 是一個網路介面的 kernel 表示（實體或虛擬皆是），驅動填 `net_device_ops`（`ndo_start_xmit` 送包、`ndo_open`/`ndo_stop`…）、kernel 呼叫——與 VFS/device model 同一套 ops 多型；多佇列 + RSS + NAPI 是高速收送的三根支柱（Ch 44/45 展開）。

## 自我檢核

- [ ] 不看筆記，能畫出 `head`/`data`/`tail`/`end` 四指標，並解釋送包時 `skb_push` 怎麼讓 header「長出來」而不複製資料
- [ ] 能解釋收包為什麼一路 `skb_pull`、送包為什麼一路 `skb_push`，以及 `data` 指標在各層的位置差異
- [ ] 能說出非線性 skb 是什麼、為什麼需要（零複製 + 大封包），以及 `skb->len` 和 `skb_headlen()` 的差別
- [ ] 能解釋 `skb_clone` 和 `skb_copy` 的差別，以及為什麼 clone 出來的 skb 不能直接改資料
- [ ] 面試被問「一個 net_device 怎麼被驅動掛進 kernel」，能講出 `alloc_etherdev` → 填 `netdev_ops` → `register_netdev` 這條線，並說出 `ndo_start_xmit` 的角色
- [ ] 能用 `ip`/`ethtool`/`/sys/class/net/`/bpftrace 各印出 net_device 或 skb 的一個真實屬性

## 延伸閱讀

### 官方文件與源碼

- **[`include/linux/skbuff.h`](https://elixir.bootlin.com/linux/v6.12/source/include/linux/skbuff.h)**（v6.12）
  - **讀哪裡**：`struct sk_buff` 定義、`struct skb_shared_info`，以及 `skb_put`/`skb_push`/`skb_pull`/`skb_reserve`/`skb_headroom`/`skb_tailroom` 這組 inline 函式的實作
  - **為什麼**：這章講的所有指標操作都是這裡幾行 inline 函式，讀原始碼比讀任何轉述都清楚——每個函式就是「移動哪個指標 ± n」

- **[`net/core/skbuff.c`](https://elixir.bootlin.com/linux/v6.12/source/net/core/skbuff.c)**（v6.12）
  - **讀哪裡**：`__alloc_skb`（skb 怎麼配出來、data area 與 shinfo 的佈局）、`skb_clone`、`pskb_expand_head`（headroom 不夠時怎麼重配）、`kfree_skb_reason`
  - **和本章的關聯**：把「clone 共享、headroom 不夠要重配」這些設計從抽象變成可讀的程式碼

- **[`include/linux/netdevice.h`](https://elixir.bootlin.com/linux/v6.12/source/include/linux/netdevice.h)**（v6.12）
  - **讀哪裡**：`struct net_device`、`struct net_device_ops`（把每個 `ndo_*` 的註解讀一遍）、`struct napi_struct`
  - **為什麼**：`net_device_ops` 的註解本身就是「驅動該實作什麼」的權威清單，是寫網卡驅動的入口

- **[Documentation/networking/skbuff.rst](https://www.kernel.org/doc/html/latest/networking/skbuff.html)** — kernel 官方
  - **讀哪裡**：整篇，很短。官方對 skb 記憶體佈局與 checksum offload（`ip_summed` 那幾個值）的權威說明
  - **和本章關聯**：補齊本章沒展開的 checksum 狀態機細節

### 書籍與文章

- **《Understanding Linux Network Internals》** — Christian Benvenuti（O'Reilly, 2005）
  - **定位**：把 Linux 網路堆疊拆到源碼層的經典。第 II 部分（`sk_buff`、`net_device`）正是本章主題的加深版
  - **注意**：講的是 2.6，函式名/欄位對不上 6.12，但**架構骨架**（skb 指標、net_device 抽象、ndo 表）至今適用；細節以 6.12 源碼為準

- **[LWN: "The kernel's socket buffer"（skb 系列文章）](https://lwn.net/Kernel/Index/#Networking-Socket_buffers)** — LWN.net
  - **讀哪裡**：從索引挑 skb headroom、frags、zerocopy 相關的幾篇
  - **為什麼**：GSO/GRO、`MSG_ZEROCOPY`、drop reason 這些機制進主線時，LWN 的文章是最好的一手「為什麼這樣設計」解說

- **[bpftrace 的 skb 觀測範例](https://github.com/bpftrace/bpftrace)** — 配合本 repo 的 `bpf` 課
  - **怎麼用**：把本章的 bpftrace 一行式擴充成一個小腳本，攔 `net_dev_xmit`、`netif_receive_skb` tracepoint，印 skb 的更多欄位。這是 `bpf` 課 tracing 技法在網路堆疊上的應用，也預告 Ch 46/52 的 tc/XDP BPF 直接操作 skb

你現在手裡有了「一個封包」（skb）和「一張網卡」（net_device）這兩個地基結構。下一章我們讓封包動起來——看一個封包從網卡 DMA 進 RAM 開始，怎麼透過 NAPI 輪詢、`NET_RX` softirq、GRO 聚合，一路往上爬到協定堆疊。

→ [Ch 44 收包路徑：NAPI、softirq NET_RX、GRO](./44-rx-path-napi.md)
