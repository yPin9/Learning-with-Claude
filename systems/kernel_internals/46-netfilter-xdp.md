# Ch 46 — netfilter/nftables hook 與 XDP

> **目標**：搞懂 kernel 網路堆疊在哪些位置預留了「掛鉤子」的點，讓防火牆、NAT、負載平衡、監控這些子系統能在封包流經時攔它、改它、丟它。學完你能指出 netfilter 五個經典 hook point 各自落在收/送/轉發路徑的哪一格、解釋 iptables 與 nftables 的機制差異、說清楚 XDP 為什麼比 netfilter 快一個數量級，並用 `nft`、`conntrack`、`ip link set xdp`、`tc filter` 在真機上把這些鉤子掛起來、看封包被擋。

> **前置**：Ch 44（收包路徑：NAPI、softirq NET_RX、`__netif_receive_skb`）、Ch 45（送包路徑：`ip_output`、qdisc）、Ch 43（`sk_buff`）。這章是把「封包在堆疊裡怎麼流」這件事，換成「別人怎麼在流動途中攔它」的視角。跨課請對照 **networking 課**（iptables/nftables/NAT 的使用者與協定視角）與 **bpf 課**（XDP / tc BPF 的程式撰寫深入）——本章講的是這些工具在 **kernel 端**掛鉤的機制，那兩門課講你怎麼用它。

## 為什麼需要這個？

Ch 44、Ch 45 把收送路徑追完了：封包從網卡 DMA 進來、經 NAPI poll 往上交到 IP 層、再到 socket；或反過來從 socket 一路下到 driver 送出。這條路徑是**寫死的**——`ip_rcv` 之後一定接路由查找，路由完一定接 local deliver 或 forward。問題是：現實世界的網路遠不只「把封包送到該去的地方」。你要做防火牆（某些封包不准進）、要做 NAT（改寫來源/目的位址讓一堆內網機器共用一個公網 IP）、要做負載平衡（把送給 VIP 的封包分派到後端一堆真實伺服器）、要做流量監控（數封包、抽樣、記 log）。

這些需求有個共同點：**都得在封包流經堆疊時攔下它，看一眼、也許改一改、也許直接丟掉**。如果每加一個這種需求就去改 `ip_rcv` 的原始碼，那 IP 層很快會變成一坨誰都不敢動的義大利麵。正確的解法是：kernel 在收/送/轉發路徑的**關鍵位置預留「掛鉤點（hook point）」**，讓各個子系統把自己的 callback 掛上去。封包流到那個點時，kernel 就依序呼叫掛在上面的所有 callback，每個 callback 回傳一個裁決（放行 / 丟棄 / 我接手了），kernel 照裁決決定封包接下來的命運。

這就是 **netfilter** 幹的事——它是 Linux 防火牆與 NAT 的地基，iptables、nftables 這些你在 `networking` 課用過的工具，底下全是 netfilter hook。而 **XDP** 是另一條路子：與其等封包爬到 IP 層才攔，不如在**最早的地方**——網卡 driver 剛把封包從 DMA ring 拿出來、連 `sk_buff`（Ch 43）都還沒配好——就跑一段 BPF 程式攔它。這章把這兩套機制以及它們之間的 tc BPF 一起講清楚。

## 先建立直覺

先把「一個封包在 kernel 裡會被攔幾次、在哪些地方被攔」畫出來。這張圖是本章的骨架，其他都是細節：

```
                    ┌──────────────────── 本機收/送/轉發三條路徑 ────────────────────┐
                    │                                                                │
  網卡 driver       │   IP 層路由查找決定：這封包是給「本機」還是「要轉發出去」？        │
  ─────────         │                                                                │
  收: DMA ring      │        ┌───────────┐        ┌──────────┐        給本機         │
   │                │        │PREROUTING │───────►│  routing │──────► ┌────────┐    │
   ▼                │        │  (nf)     │        │  decision│        │ INPUT  │──► 本機 socket
  [XDP]  ◄── 最早   │   ────►│           │        └────┬─────┘        │ (nf)   │       │
   │      (driver)  │        └───────────┘             │              └────────┘       │
   ▼                │                                  │ 要轉發                        │
  alloc skb (Ch43)  │                                  ▼                               │
   │                │                             ┌─────────┐        ┌────────────┐    │
   ▼                │                             │ FORWARD │───────►│POSTROUTING │──► 送出網卡
  __netif_receive   │                             │  (nf)   │        │   (nf)     │    │
   │  [tc ingress]  │                             └─────────┘        └────────────┘    │
   ▼                │                                                      ▲           │
  ip_rcv ───────────┼──────────────────────────────────────────┐         │           │
                    │   本機主動送包（Ch 45）：socket → ...       ▼         │           │
                    │                             ┌────────┐  ┌──────────┐ │           │
                    │                             │ OUTPUT │─►│ routing  │─┘           │
                    │                             │  (nf)  │  │ decision │             │
                    │                             └────────┘  └──────────┘             │
                    │                                (tc egress 在更靠近 driver 處)     │
                    └────────────────────────────────────────────────────────────────┘
```

三件事先記住：

1. **netfilter 五個 hook 的位置是圍著「路由查找」這件事定義的**。PREROUTING 在路由**前**（此時還不知道封包給誰），路由完分兩路：給本機走 INPUT，要轉發走 FORWARD 再走 POSTROUTING。本機自己送的封包從 OUTPUT 進、經路由、走 POSTROUTING 出。這五個名字你在 `iptables -t nat` 或 `nft` 裡見過，現在知道它們對應堆疊裡的哪一格了。

2. **XDP 站在整條路徑的最前端**——在 driver 層、在 `sk_buff` 配置**之前**。它看到的是還沒被 kernel 包裝的裸封包。位置越早，能省的工越多（不用配 skb、不用爬堆疊），但能拿到的 context 越少（還沒路由、還沒 conntrack）。

3. **tc BPF（ingress/egress）夾在中間**——比 XDP 晚（已有 skb），但比 netfilter 早或平行，且收送兩側都能掛。它拿得到完整 skb metadata，代價是每個封包都已經付了配 skb 的成本。

「越早攔越省、但越早 context 越少」——這條軸線就是本章所有機制的取捨主軸。

## netfilter：五個 hook point 的機制

netfilter 的核心程式碼在 `net/netfilter/core.c`，公開介面在 `include/linux/netfilter.h`。它的設計是一個經典的 **hook 框架**：協定堆疊在關鍵位置埋下 `NF_HOOK()` 呼叫，各子系統用 `nf_register_net_hook()` 把 callback 註冊進去。

### 五個 hook 是列舉值

hook point 不是抽象概念，是 `include/uapi/linux/netfilter.h` 裡的一組列舉：

```c
enum nf_inet_hooks {
        NF_INET_PRE_ROUTING,    /* 封包剛進堆疊、路由查找之前 */
        NF_INET_LOCAL_IN,       /* 路由判定「給本機」之後、進 socket 之前 */
        NF_INET_FORWARD,        /* 路由判定「要轉發」的封包 */
        NF_INET_LOCAL_OUT,      /* 本機自己產生的封包、剛離開協定往下送 */
        NF_INET_POST_ROUTING,   /* 封包即將離開本機、送給 driver 之前 */
        NF_INET_NUMHOOKS
};
```

對照收包路徑（Ch 44）：`ip_rcv()`（`net/ipv4/ip_input.c`）在做完基本檢查後，會呼叫 `NF_HOOK(NFPROTO_IPV4, NF_INET_PRE_ROUTING, ...)`，把封包交給掛在 PREROUTING 的所有 callback。callback 全放行後，控制權回到 `ip_rcv_finish()`，這才做路由查找（`ip_route_input`）。路由結果決定下一個 hook：給本機的封包在 `ip_local_deliver()` 觸發 `NF_INET_LOCAL_IN`，要轉發的在 `ip_forward()` 觸發 `NF_INET_FORWARD`。送包側（Ch 45）：`__ip_local_out()` 觸發 `NF_INET_LOCAL_OUT`，`ip_output()` 觸發 `NF_INET_POST_ROUTING`。

**為什麼是這五個而不是三個或十個**？因為這五個點正好切在「封包命運的分岔口」上。路由查找是最關鍵的分岔（本機 vs 轉發），所以以它為界前後各設一個(PRE/POST_ROUTING)，分岔後的兩條路各設一個(LOCAL_IN/FORWARD)，本機主動送的獨立一條(LOCAL_OUT)。iptables 的 `filter`/`nat`/`mangle` 三張表就是把不同功能掛在這五個點的不同組合上——例如 SNAT 只在 POSTROUTING 有意義（位址改寫要在路由之後、送出之前），DNAT 只在 PREROUTING 有意義（要在路由**之前**改目的位址，路由才會把它導對地方）。

### NF_HOOK 怎麼跑

`NF_HOOK()`（`include/linux/netfilter.h`）展開後呼叫 `nf_hook()`，後者走訪該 hook point 上註冊的 callback 陣列（`struct nf_hook_entries`），依**優先級**順序一個個呼叫。每個 callback 回傳一個 verdict：

```c
#define NF_DROP   0   /* 丟棄，封包到此為止，釋放 skb */
#define NF_ACCEPT 1   /* 放行，繼續呼叫下一個 hook / 回堆疊 */
#define NF_STOLEN 2   /* 我把封包接走了，堆疊別再管它（例如排隊到 userspace） */
#define NF_QUEUE  3   /* 交給 nfqueue 送到使用者空間處理 */
#define NF_REPEAT 4   /* 重跑這個 hook（改完 skb 想重新過一遍） */
```

裁決邏輯在 `nf_hook_slow()`（`net/netfilter/core.c`）：只要有一個 callback 回 `NF_DROP`，`nf_hook_slow` 立刻回錯誤、上層 `NF_HOOK` 的 `okfn`（正常後續函式，如 `ip_rcv_finish`）就不會被呼叫，封包等於當場消失。回 `NF_ACCEPT` 才繼續走訪下一個 callback；全部放行後 `NF_HOOK` 才呼叫 `okfn` 讓封包回到堆疊主流程。這就是防火牆「預設放行、命中規則就 DROP」的機制底層。

### 註冊一個 hook

子系統這樣掛鉤（這也是本章動手部分要寫的最小模組雛型）：

```c
static unsigned int my_hookfn(void *priv, struct sk_buff *skb,
                              const struct nf_hook_state *state)
{
        struct iphdr *iph = ip_hdr(skb);      /* skb 已配好，可讀 header */
        if (iph->protocol == IPPROTO_ICMP)
                return NF_DROP;               /* 擋掉所有 ICMP */
        return NF_ACCEPT;
}

static struct nf_hook_ops my_ops = {
        .hook     = my_hookfn,
        .pf       = NFPROTO_IPV4,
        .hooknum  = NF_INET_PRE_ROUTING,
        .priority = NF_IP_PRI_FIRST,          /* 越小越早跑 */
};
/* module_init 裡：nf_register_net_hook(&init_net, &my_ops); */
```

注意 `nf_register_net_hook()` 的第一個參數是 `struct net *`——hook 是 **per network namespace** 的（接 Ch 49）。每個 netns 有自己的一份 hook 表，容器裡的防火牆規則不會漏到 host。這是 Docker/K8s 網路隔離的一塊地基。

## iptables vs nftables：規則怎麼被執行

netfilter 提供 hook 框架，但「規則長什麼樣、怎麼比對」是另一層。這裡是 iptables 與 nftables 的分水嶺。

**iptables（舊路線）**：每張表（filter/nat/mangle/raw）在每個 hook 上掛一個 callback，callback 裡是一條**線性規則鏈**——封包來了就從第一條規則開始逐條比對（match），命中就執行 target（ACCEPT/DROP/DNAT…）。問題有幾個：規則是線性掃描，幾千條規則時每個封包都要掃一遍很慢；IPv4/IPv6/arp/eb 各有一套獨立工具（iptables/ip6tables/arptables/ebtables），程式碼大量重複；每次改規則要把整張表 dump 出來、改、再整張灌回去，不是原子的。

**nftables（新路線，`net/netfilter/nf_tables_*.c`）**：把規則編譯成一套 **kernel 內的虛擬機（nft VM）位元組碼**。你寫的 `nft add rule ... ip protocol icmp drop` 被 userspace 的 libnftnl 翻成一串 VM 指令（load、cmp、verdict…），灌進 kernel，封包來時由 `nft_do_chain()`（`net/netfilter/nf_tables_core.c`）這個直譯器執行。好處：一套 VM 統管所有協定家族（不再分 iptables/ip6tables）；支援 map/set 做 O(1) 查表取代線性掃描（幾萬條規則也快）；規則更新是原子交易（transaction）。這就是為什麼現代發行版預設 backend 已換成 nftables（`iptables` 指令多半是 `iptables-nft` 這個相容層，底下還是走 nft VM）。

> 兩者本質差別：iptables 是「固定結構 + 線性掃描」，nftables 是「一個通用 VM 直譯位元組碼」。這跟 Ch 52 要講的 eBPF「kernel 內的通用 VM」是同一個思想的不同實例——與其為每種需求寫死一套 C 程式碼，不如提供一個受控的 VM 讓 userspace 灌邏輯進來。nftables 的 VM 比 eBPF 專用（只做封包分類），eBPF 的 VM 更通用。

### conntrack：狀態防火牆與 NAT 的地基

上面講的都是「無狀態」比對——看一個封包的 header 決定放行或丟棄。但真正好用的防火牆是**有狀態**的：「允許本機主動發起的連線的回包進來，但拒絕外面主動發起的」。要做到這個，kernel 得記住「有哪些連線正在進行」——這就是 **連線追蹤（connection tracking / conntrack）**，程式碼在 `net/netfilter/nf_conntrack_core.c`。

conntrack 自己也是掛在 netfilter hook 上（很早的優先級，在 PREROUTING/OUTPUT）。它為每條連線（由五元組：協定、來源 IP/port、目的 IP/port 標識）建一個 `struct nf_conn`，記錄狀態（NEW / ESTABLISHED / RELATED / INVALID），存在一張 hash 表裡。之後同一條連線的封包進來，conntrack 認出它、貼上 `ctstate` 標籤，nftables/iptables 規則就能用 `ct state established,related accept` 這種寫法放行回包。

**NAT 完全建立在 conntrack 之上**：DNAT/SNAT 在連線的第一個封包上決定要怎麼改位址，把對應關係記在那條連線的 `nf_conn` 裡；之後這條連線的所有封包（含回程）自動套用同一組位址改寫。這也解釋了為什麼一台 NAT 閘道器的 conntrack 表滿了（`nf_conntrack: table full, dropping packet`）會開始丟包——每條連線都要佔一個表項。這個現象你在 `networking` 課的 NAT/防火牆章節從協定視角看過，這裡是它的 kernel 資料結構視角。

## XDP：在最早的地方攔封包

netfilter 的鉤子最早也要到 `ip_rcv`，那時 `sk_buff` 早已配好、封包已經爬進協定堆疊。對「每秒要擋幾百萬個 DDoS 封包」這種場景，光是為每個註定要丟的封包配一個 skb 就是巨大浪費。**XDP（eXpress Data Path）** 的答案是：把攔截點推到不能再早的地方——**網卡 driver 剛從 DMA ring 拿到封包、還沒配 skb、還沒進堆疊**，就跑一段 BPF 程式決定它的命運。

### XDP 在路徑上的位置

對比 netfilter/tc 與 XDP 的攔截時機：

```
  網卡收到封包，DMA 進 ring buffer
        │
        ▼
  driver 的 NAPI poll（Ch 44）從 ring 拿出封包的 raw buffer（DMA 記憶體，還沒 skb）
        │
        ▼
  ┌───────────────────┐
  │  [XDP hook]        │  ◄── 最早！只有一塊 raw packet buffer（struct xdp_buff）
  │  跑 BPF 程式        │       沒有 skb、沒有協定解析、沒有 conntrack
  │  回 XDP_DROP/PASS/  │       DROP 在這裡 = 幾乎零成本（連 skb 都沒配）
  │     TX/REDIRECT    │
  └─────────┬─────────┘
            │ XDP_PASS
            ▼
  build skb（Ch 43，這裡才付配置成本）
            │
            ▼
  ┌───────────────────┐
  │  [tc ingress hook] │  ◄── 已有 skb，能存取完整 metadata，但已付 skb 成本
  │  (clsact qdisc)    │
  └─────────┬─────────┘
            ▼
  __netif_receive_skb → ip_rcv
            │
            ▼
  ┌───────────────────┐
  │  [netfilter        │  ◄── 最晚，封包已爬到 L3，context 最完整
  │   PREROUTING]      │
  └───────────────────┘
```

XDP 的 hook 點在 `net/core/dev.c` 附近——真正跑 BPF 的入口是 `netif_receive_generic_xdp()` / driver 專屬的 `bpf_prog_run_xdp()`（`include/linux/filter.h`）。程式看到的封包是 `struct xdp_buff`（`include/net/xdp.h`）——就是一塊 raw memory 加上 data/data_end 指標，沒有 skb 的任何 metadata。

### 四種 verdict

XDP BPF 程式回傳（`include/uapi/linux/bpf.h` 的 `enum xdp_action`）：

```c
XDP_ABORTED  /* 出錯，丟包並觸發 tracepoint（可觀測） */
XDP_DROP     /* 丟棄。這是 XDP 的殺手級用途——DDoS 過濾，成本極低 */
XDP_PASS     /* 放行，繼續走正常路徑（build skb、進堆疊） */
XDP_TX       /* 從「同一張網卡」把（可能改過的）封包彈回去——L4 LB 常用 */
XDP_REDIRECT /* 轉發到另一個網卡 / CPU / AF_XDP socket——快速轉發、封包處理 */
```

`XDP_DROP` 為什麼是關鍵應用：DDoS 洪水來時，你要在最便宜的地方把垃圾封包擋掉。XDP_DROP 發生在配 skb **之前**，被丟的封包幾乎沒消耗 kernel 資源——這是 Cloudflare 等公司用 XDP 扛大流量 DDoS 的原因。`XDP_TX` / `XDP_REDIRECT` 則是 L4 負載平衡的地基：Facebook/Meta 的 **Katran** 就是一個 XDP 程式，收到送給 VIP 的封包，查一致性 hash 選出後端伺服器，改寫封裝後直接 `XDP_TX`/`REDIRECT` 出去，全程不進 TCP/IP 堆疊，所以能用少數幾台機器扛巨量流量。

### 三種執行模式

同一支 XDP 程式，依「跑在哪」分三種模式，性能差很多：

| 模式 | 跑在哪 | 性能 | 需求 |
|---|---|---|---|
| **native / driver** | driver 的 NAPI poll 裡，skb 配置之前 | 快（真正省掉 skb） | driver 要實作 `ndo_bpf` XDP 支援 |
| **generic / skb** | `netif_receive_generic_xdp()`，skb **已配好**之後 | 慢（沒省到 skb，只是相容 fallback） | 任何 driver 都能跑 |
| **offload** | 卸載到**網卡硬體**執行 | 最快（CPU 完全不碰） | 網卡（如某些 Netronome NFP）支援 |

「native 才真快」是重點：generic 模式（driver 不支援 XDP 時的 fallback）其實已經配了 skb，只是在配完之後跑 BPF——它讓你能開發測試，但拿不到 XDP 該有的性能。你在 QEMU 裡用預設 virtio 網卡掛 XDP，多半跑的是 generic 模式；生產環境要的是 native driver 模式。

## tc BPF：拿得到 skb 的那個鉤子

XDP 快，但看到的是裸封包、只在收方向、拿不到 kernel 的 metadata。有時候你要的正是那些 metadata（skb 的 mark、優先級、關聯的 socket、cgroup 資訊），或需要在**送**方向掛鉤——這是 **tc BPF**（透過 `clsact` qdisc）的地盤。

Ch 45 講過 qdisc 是送包路徑上的排隊層。`clsact` 是一個特殊 qdisc（`net/sched/sch_ingress.c`），它不排隊，只提供兩個掛 BPF 的點：**ingress**（收方向，`sch_handle_ingress`，在 `__netif_receive_skb_core` 早期）和 **egress**（送方向，`sch_handle_egress`，靠近 `dev_queue_xmit` 出口）。tc BPF 程式看到的是完整的 `struct __sk_buff`（`include/uapi/linux/bpf.h`），能讀改一票 metadata，回傳 `TC_ACT_OK`（放行）/`TC_ACT_SHOT`（丟棄）/`TC_ACT_REDIRECT` 等。

XDP vs tc BPF 的取捨，就是本章開頭那條軸線的具體化：**XDP 更早更快但 context 少且只在收方向；tc BPF 稍晚（已付 skb 成本）但 context 完整且收送皆可**。Cilium（K8s 網路方案）大量用 tc BPF 正是因為它要的是 skb 級的策略（哪個 pod、哪個 service）而非純粹的封包丟棄。這兩個鉤子怎麼寫 BPF 程式、verifier 怎麼驗，是 `bpf` 課 networking part 的主題；這裡你要記住的是它們在堆疊裡的**位置**與**能拿到什麼**。

## 底層機制：hook 的順序、優先級與 per-netns

同一個 hook point 上常常掛著不只一個 callback（conntrack、你的模組、nftables 各一個），誰先跑？靠 `nf_hook_ops.priority`——**數值越小越早跑**（`net/netfilter/core.c` 的 `nf_hook_entry_head` 依 priority 排序插入）。這個順序在做 NAT + 防火牆混用時很要緊：conntrack 用很高的優先級（很小的數字）搶先跑，確保後面的規則能拿到 ct 狀態；DNAT 要在 filter 規則之前，位址才是改過的。`include/uapi/linux/netfilter_ipv4.h` 定義了一組標準優先級常數（`NF_IP_PRI_CONNTRACK`、`NF_IP_PRI_NAT_DST`、`NF_IP_PRI_FILTER`…），就是在替這些子系統排隊。

再強調一次 **per-netns**：`nf_register_net_hook(struct net *net, ...)` 把 hook 註冊到**特定** netns 的 hook 表。`struct net`（`include/net/net_namespace.h`）裡有一份 `struct netns_nf`，各 hook point 的 callback 陣列掛在這裡。開一個新的 network namespace（Ch 49，`unshare -n` 或容器啟動），它拿到一份乾淨的 hook 表——這是「容器有自己獨立防火牆規則」的機制根源。XDP 也類似：XDP 程式綁在 `net_device` 上，而 net_device 屬於某個 netns，所以隔離性天然成立。

整條裁決流程串起來（收到一個 ICMP 封包、假設你掛了 PREROUTING DROP ICMP 的規則）：

```
NAPI poll 拿到封包
  → XDP hook：沒掛程式 → 等同 PASS → 配 skb
  → tc ingress：沒掛 → 過
  → ip_rcv → NF_HOOK(PRE_ROUTING):
        依 priority 走訪：conntrack callback(記下這條連線) → NF_ACCEPT
                          nftables callback：比對 ip protocol icmp → verdict = NF_DROP
      → nf_hook_slow 見到 NF_DROP → 回錯誤，okfn(ip_rcv_finish) 不被呼叫
      → skb 被釋放，封包當場消失，ping 收不到回應
```

## 動手：掛三種鉤子看封包被擋

以下多數可在你的 host（或 Ch 0 的 QEMU）直接做。XDP 在 QEMU virtio 網卡多半是 generic 模式，能驗功能但不是 native 性能。

### 1. nftables 擋 ICMP

```bash
# 建一個 table + chain，掛在 input hook（等同 netfilter LOCAL_IN）
sudo nft add table inet myfw
sudo nft 'add chain inet myfw input { type filter hook input priority 0 ; }'
sudo nft add rule inet myfw input ip protocol icmp counter drop

sudo nft list ruleset          # 看你灌進去的規則
ping -c 2 127.0.0.1            # 觀察：loopback 的 ICMP 被擋（counter 會跳）
sudo nft list ruleset          # 再看一次，counter 數字增加了

sudo nft delete table inet myfw   # 收拾
```

`type filter hook input priority 0` 這行就是把這條 chain 掛到 netfilter 的 `NF_INET_LOCAL_IN`、優先級 0。你在寫的其實就是本章講的 hook 註冊，只是透過 nft 而非自己寫模組。

### 2. conntrack 看連線狀態

```bash
sudo modprobe nf_conntrack
# 產生一點連線：另開 terminal 跑 curl 或 ssh，然後
sudo conntrack -L                 # 列出 conntrack 表，看到五元組 + 狀態(ESTABLISHED…)
sudo conntrack -L | wc -l         # 目前追蹤幾條連線
cat /proc/sys/net/netfilter/nf_conntrack_max   # 表的上限
cat /proc/sys/net/netfilter/nf_conntrack_count # 目前用了幾條
```

看 `nf_conntrack_count` 逼近 `nf_conntrack_max` 就是前面說的「表滿丟包」的前兆。

### 3. 掛一個最小 XDP DROP 程式

最省事的方式是用 bpftrace 或現成工具，但要真正體會「掛 XDP」，用 `ip link` 加載一支 `.o`。這裡給概念流程（完整 BPF 撰寫見 `bpf` 課）：

```c
/* xdp_drop.c —— 用 clang -O2 -target bpf 編成 xdp_drop.o */
#include <linux/bpf.h>
#include <bpf/bpf_helpers.h>

SEC("xdp")
int xdp_drop_all(struct xdp_md *ctx)
{
        return XDP_DROP;      /* 丟掉所有進來的封包 */
}
char _license[] SEC("license") = "GPL";
```

```bash
clang -O2 -g -target bpf -c xdp_drop.c -o xdp_drop.o
sudo ip link set dev <iface> xdp obj xdp_drop.o sec xdp   # 掛上（會斷該介面的網路！）
ip link show <iface>                                       # 看到 "xdp" 標記與 prog id
sudo ip link set dev <iface> xdp off                       # 卸下，網路恢復
```

> **警告**：在你唯一的網卡上掛 `XDP_DROP` 會**立刻切斷該介面所有收包**（含你的 SSH）。在 QEMU 裡、或拿一個不重要的 veth 介面練習。`ip link show` 若顯示 `xdpgeneric` 就是 generic 模式，`xdp` 才是 native driver 模式。

### 4. tc BPF 掛在 clsact

```bash
sudo tc qdisc add dev <iface> clsact                          # 建立 clsact qdisc
sudo tc filter add dev <iface> ingress bpf da obj prog.o sec tc  # 掛 ingress BPF
sudo tc filter show dev <iface> ingress                       # 看掛上的 tc BPF
sudo tc qdisc del dev <iface> clsact                          # 收拾
```

同一個 clsact 可同時掛 `ingress` 與 `egress`，對比 XDP 只有收方向——這就是 tc BPF「收送皆可」的實證。

## 對比與取捨

| 機制 | 攔截位置 | 有 skb？ | 方向 | context | 性能 | 典型用途 |
|---|---|---|---|---|---|---|
| **XDP** | driver（最早） | 否（xdp_buff） | 僅收 | 最少（裸封包） | 最快 | DDoS 丟包、L4 LB、快速轉發 |
| **tc BPF (clsact)** | `__netif_receive`/`dev_queue_xmit` | 是 | 收+送 | 完整 skb metadata | 快 | 容器網路策略(Cilium)、流量整形 |
| **netfilter hook** | 協定堆疊各點 | 是 | 收+送+轉發 | 最完整（已路由、有 ct） | 中 | 防火牆、NAT、狀態追蹤 |
| **iptables (legacy)** | netfilter hook 上的線性鏈 | 是 | 同上 | 同上 | 規則多時慢 | 舊有部署、相容 |
| **nftables** | netfilter hook 上的 nft VM | 是 | 同上 | 同上 | 好（set/map O(1)） | 現代防火牆/NAT |

選型直覺：**只要丟包或無狀態轉發、且要極致性能 → XDP**；**要 skb 級策略、收送都管 → tc BPF**；**要狀態防火牆/NAT/成熟工具鏈 → nftables**。這三者不互斥，生產系統常疊用（XDP 擋 DDoS + tc BPF 做 pod 策略 + nftables 做 host 防火牆）。

## 踩雷集錦

1. **「XDP_DROP 只是把封包丟掉，跟 iptables DROP 一樣」——錯**。差別在**成本與時機**。iptables DROP 時封包早已配好 skb、爬到 L3；XDP_DROP 在配 skb 之前，被丟的封包幾乎沒消耗資源。對付百萬 pps 的洪水，這個差別是「扛得住」與「機器躺平」的差別。

2. **「我在 QEMU 掛了 XDP，怎麼沒感覺變快」——你多半跑的是 generic 模式**。virtio 網卡若不支援 native XDP，kernel 用 generic fallback：skb 已經配了才跑你的 BPF，省不到配 skb 的成本。`ip link show` 看到 `xdpgeneric` 就是這情況。native 性能要 driver 支援 XDP（`ndo_bpf`）。

3. **「conntrack 是防火牆規則的一部分」——不是，它是獨立子系統**。conntrack 自己掛在很早的 netfilter hook 上，先於你的規則跑，替每個封包貼好 ct 狀態；你的規則只是**消費**這個狀態（`ct state established accept`）。所以就算你一條防火牆規則都沒寫，只要載了 `nf_conntrack`，連線就在被追蹤（也在吃表項）。

4. **「netfilter hook 順序無所謂」——大錯**。priority 決定順序，且順序影響正確性。DNAT 必須在路由**之前**（PREROUTING）且在 filter 規則之前跑，否則規則看到的是還沒改的目的位址、路由把封包導錯地方。conntrack 必須最早跑，後面才有 ct 狀態可用。這些不是效能問題，是對錯問題。

5. **「hook 是全域的」——不是，是 per-netns**。`nf_register_net_hook` 帶 `struct net *`。在容器（獨立 netns）裡設的規則不影響 host，host 的規則也不會自動套到容器。除錯「規則怎麼沒生效」時，先確認你在對的 netns 裡（`ip netns exec <ns> nft list ruleset`）。這點接 Ch 49。

## 進階：再往深一層

- **`nf_hook_slow` 的 fast path**：多數封包其實沒掛任何 hook。kernel 用 static key（`nf_hooks_needed`，Ch 32 提過的 static branch 技術）讓「沒掛 hook 時」的 `NF_HOOK` 幾乎零成本——編譯期埋一個 nop，有人註冊 hook 才 patch 成真正的呼叫。這是「不用的功能不該付成本」在網路熱路徑上的體現。
- **AF_XDP（zero-copy 到 userspace）**：XDP_REDIRECT 可以把封包直接丟進一個 `AF_XDP` socket，userspace 用 mmap 的 ring 零複製拿到裸封包——這是 DPDK 之外、留在 kernel 內的高性能封包處理路徑。`bpf` 課會展開。
- **bpf_nf / nftables 與 BPF 融合**：新 kernel 讓 nftables 規則能呼叫 BPF、也讓 BPF 能查 conntrack。兩套「kernel 內 VM」正在互通。
- **flowtable / 硬體 offload**：nftables 的 flowtable 讓已建立的連線走一條繞過大半堆疊的捷徑（software fast path），甚至 offload 到網卡硬體轉發——概念上是把「熱連線」降級成類 XDP 的快速轉發。
- **面試常問**：「XDP 和 tc BPF 差在哪、各自何時用」「iptables 為什麼被 nftables 取代」「NAT 為什麼依賴 conntrack」「一個封包從進網卡到進 socket 會經過哪些可攔截點」——這章的圖能一次答完。

## 動手練習

1. **畫路徑圖**：不看本章，憑記憶畫出一個「本機收到的」封包、一個「要轉發的」封包、一個「本機送出的」封包各會經過哪些 netfilter hook。再標出 XDP 和 tc ingress/egress 的位置。畫完對照本章第一張圖。
2. **驗證 hook 順序**：用 `nft` 在 input 掛兩條 chain，priority 分別設 -10 和 10，各加一個 `counter log prefix`。ping 本機，從 `dmesg` 看兩條 log 出現的順序，驗證「數字小的先跑」。
3. **conntrack 表滿模擬**：把 `nf_conntrack_max` 調到很小（`sysctl net.netfilter.nf_conntrack_max=16`），用 `nmap` 或大量並發連線塞爆它，`dmesg` 看 `table full, dropping packet`。改回來。體會 NAT 閘道器的這個瓶頸。
4. **XDP DROP 選擇性丟包**：改上面的 `xdp_drop.c`，只在封包是 ICMP 時回 `XDP_DROP`、其他 `XDP_PASS`（需解析 eth + ip header）。掛到一個 veth 上，從另一端 ping（被擋）+ curl（通），驗證選擇性。
5. **gdb 停在 hook**：Ch 0 的 QEMU + gdb，`break nf_hook_slow`，在 QEMU 裡 ping，觀察 `backtrace` 看它從 `ip_rcv` 一路呼叫進來，`print state->hook` 確認是哪個 hook point。

## 本章重點整理

- netfilter 在收/送/轉發路徑圍著「路由查找」設了五個 hook point（PRE_ROUTING/LOCAL_IN/FORWARD/LOCAL_OUT/POST_ROUTING），子系統用 `nf_register_net_hook` 掛 callback，回傳 `NF_ACCEPT`/`NF_DROP`/`NF_STOLEN` 等 verdict 決定封包命運；hook 是 per-netns 的。
- iptables 是「固定表 + 線性掃描」，nftables 是「一個 kernel 內 nft VM 直譯位元組碼」，後者更快更靈活、支援原子更新，是現代預設；conntrack 是狀態防火牆與 NAT 的共同地基。
- XDP 在 driver 層、skb 配置**之前**跑 BPF，回 `XDP_DROP/PASS/TX/REDIRECT`，因為省掉 skb 與堆疊穿越所以極快（DDoS 過濾、Katran L4 LB）；三種模式 native/generic/offload 性能差很多。
- tc BPF（clsact）比 XDP 晚但拿得到完整 skb metadata 且收送皆可；「越早攔越省成本、但 context 越少」是貫穿 XDP→tc→netfilter 的取捨主軸。

## 自我檢核

- [ ] 不看筆記，能畫出五個 netfilter hook 相對於「路由查找」的位置，並說出 DNAT 為何在 PREROUTING、SNAT 為何在 POSTROUTING
- [ ] 能解釋 `nf_hook_slow` 遇到 `NF_DROP` 之後，為什麼上層的 `okfn`（如 `ip_rcv_finish`）不會被呼叫
- [ ] 能講清楚 XDP 為什麼比 netfilter 快，以及 native 與 generic 模式的差別
- [ ] 面試被問「XDP 和 tc BPF 該用哪個」，能用「時機/skb/方向/context」四點答出取捨
- [ ] 能解釋 NAT 為什麼一定要靠 conntrack，以及 conntrack 表滿會發生什麼
- [ ] 知道防火牆規則為何在容器裡不影響 host（per-netns hook）

## 延伸閱讀

### 官方文件

- **[Documentation/networking/netfilter-sysfs.rst 與 nf_conntrack 相關文件](https://www.kernel.org/doc/html/latest/networking/nf_conntrack-sysctl.html)**
  - **讀哪裡**：`nf_conntrack` 的 sysctl 說明，理解表大小、逾時、狀態怎麼調
  - **和本章的關聯**：補充 conntrack 一節，動手練習 3 的參數都在這裡

- **[Documentation/bpf/prog_xdp.rst 與 XDP 相關](https://docs.kernel.org/bpf/index.html)** — kernel BPF/XDP 文件
  - **讀哪裡**：XDP 的 program type、`xdp_md` context、redirect 機制
  - **前提**：跟完本章 XDP 一節；要真正寫 XDP 程式再配 `bpf` 課

### 論文 / 深度文章

- **[The eXpress Data Path: Fast Programmable Packet Processing in the Operating System Kernel](https://dl.acm.org/doi/10.1145/3281411.3281443)** — Høiland-Jørgensen et al., CoNEXT 2018
  - **這是什麼**：XDP 的原始論文，作者是 XDP 的主要開發者
  - **讀哪裡**：設計動機（為什麼要在 driver 層、為什麼不用 kernel bypass 如 DPDK）與性能評估
  - **為什麼值得讀**：本章「XDP 為什麼快」的權威來源，把 XDP vs DPDK vs 傳統堆疊的取捨講透

- **[Why is the kernel community replacing iptables with BPF?](https://cilium.io/blog/2018/04/17/why-is-the-kernel-community-replacing-iptables/)** — Cilium blog
  - **讀哪裡**：整篇，講 iptables 的擴充性問題與 BPF 取代它的理由
  - **和本章的關聯**：從生產角度補強 iptables vs nftables/BPF 一節的取捨

### 書籍 / 指南

- **[nftables wiki](https://wiki.nftables.org/)** — netfilter 官方
  - **讀哪裡**：「Quick reference」與 conntrack、NAT 相關頁
  - **為什麼值得用**：nftables 語法與概念的權威參考，配 `networking` 課的防火牆章一起看

- **《Linux Kernel Networking: Implementation and Theory》** — Rami Rosen（Apress, 2014）
  - **這本書的定位**：少數把 netfilter/conntrack 源碼講清楚的書；第 9 章 netfilter 一章值得讀
  - **注意**：對應較舊 kernel（3.x），hook 框架大結構仍適用，nftables/XDP 是後來才成熟的要另補

netfilter 和 XDP 讓我們看到 kernel 網路的「可程式化」地基——封包在流動途中可以被任意攔截、改寫、重導。Part 8 的網路子系統到此告一段落。下一章我們轉向另一條主軸：安全與隔離。一個 process 能做什麼、不能做什麼，最終取決於它的**身份**——它是誰、屬於哪個使用者、握有哪些權限。我們從最基礎的 credentials 與 capabilities 開始。

→ [Ch 47 credentials 與 capabilities](./47-credentials-capabilities.md)
