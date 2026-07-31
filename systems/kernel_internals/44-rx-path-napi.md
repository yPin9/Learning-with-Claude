# Ch 44 — 收包路徑：NAPI、softirq NET_RX、GRO

> **目標**：追一個乙太網封包從網卡 DMA 落地，一路穿過中斷、NET_RX softirq、NAPI poll、GRO 聚合、IP/TCP 協定堆疊，最後進到 socket receive queue 並喚醒 `recv()` 的 process。學完你能在腦中畫出這條路徑的每一段、指出 top half / bottom half 界線在哪，並用 `ethtool`、`/proc/net/softnet_stat`、`bpftrace` 實際觀測它。

> **前置**：Ch 29（中斷 top/bottom half）、Ch 30（softirq / NET_RX_SOFTIRQ）、Ch 41（DMA ring buffer）、Ch 43（`sk_buff` / `net_device`）。這章把前面四章的零件組裝成一條完整的資料流。收包路徑是 kernel 裡「中斷 + softirq + DMA + per-CPU」四個機制交會最密集的地方，很適合當它們的整合演練。

## 為什麼需要這個？

先算一筆帳。一張 10 GbE 網卡，收滿最小封包（64 bytes on wire，加上 preamble/IFG 共 84 bytes）時，每秒可以收約 **1,488 萬個封包**（14.88 Mpps）。100 GbE 就是這個數字的十倍。

現在回想 Ch 29 的中斷模型：每個封包到達，網卡拉一條中斷線，CPU 停下手邊的事、儲存 context、跳進 IRQ handler、處理完再回來。一次中斷進出的固定開銷（context 儲存/還原、cache/TLB 擾動、CPU pipeline flush）在 x86_64 上通常是幾百到上千個 cycle。

把兩件事乘起來：**如果每個封包一個中斷**，14.88 M × 上千 cycle，光是中斷進出就把一顆 3 GHz 的核心吃乾——這還沒開始處理封包內容。這個現象有個名字叫 **中斷風暴（interrupt storm）** 或 **receive livelock**：CPU 忙著回應中斷，反而沒時間把已收下的封包往上送，佇列滿了、封包一樣掉，但 CPU 100% 忙碌。純中斷模型在高速網路下會自己把自己餓死。

歷史上的解法演進：

- **純中斷（1990s）**：每包一中斷。低速時很好——延遲低、CPU 閒。高速時如上所述崩潰。
- **純輪詢（polling）**：CPU 定時去問網卡「有沒有東西」。高速時很有效率（一次問拿一堆），但低速/閒置時 CPU 空轉浪費電、且輪詢間隔造成延遲。
- **NAPI（New API，2.4/2.6 引入，沿用至今）**：兩者混合。**平時中斷、爆量時退化成輪詢**。這是 Linux 至今收包路徑的骨幹，也是這章的主角。

NAPI 的核心洞見：中斷的價值在「通知有事發生」，一旦知道有事，就別再讓中斷打斷你——關掉它，改用輪詢把佇列一次抽乾。等抽乾了、確定沒事了，再把中斷打開。這樣**低流量時你享受中斷的低延遲，高流量時你享受輪詢的高吞吐**，而且切換是自動的、由流量本身驅動。

## 先建立直覺

把 NAPI 想成餐廳的送餐鈴 vs. 巡桌。

- 客人少（低流量）：客人按鈴（中斷），服務生過去。一次一桌，反應快。
- 客人爆滿（高流量）：鈴聲響個不停，服務生光跑來跑去就累死，餐反而送不出去。聰明的做法是——**把鈴關掉**，服務生開始沿著桌子一路巡（輪詢），一趟收一疊單，效率高。等巡到沒單了，再把鈴打開，回到待命狀態。

NAPI 就是這套「鈴響 → 關鈴 → 巡桌到沒單 → 開鈴」的循環。關鍵在中斷 handler（top half）**幾乎不做事**：它只做兩件事——(1) 關掉這條佇列的中斷、(2) 排程一次 poll——然後立刻返回。真正的收包工作在 NET_RX softirq（bottom half）裡的 poll 函式做。

```
        封包到達網卡
             │
             ▼
   ┌───────────────────┐   top half（硬體中斷 context，Ch 29）
   │  driver IRQ handler│   ── 只做兩件事，快進快出 ──
   │  1. 關掉本佇列中斷  │
   │  2. napi_schedule()│──┐  把這個 napi_struct 掛上本 CPU 的 poll list，
   └───────────────────┘  │  並 raise NET_RX_SOFTIRQ
                          │
   ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ │─ ─ ─ ─ top / bottom half 界線 ─ ─ ─ ─ ─ ─ ─
                          │
                          ▼
   ┌────────────────────────────────────┐  bottom half（softirq context，Ch 30）
   │  net_rx_action()  (NET_RX_SOFTIRQ) │
   │    while (還有 napi 且 budget 未用完)│◄──────┐
   │      napi->poll(napi, budget)      │        │ 佇列還沒抽乾、
   │        從 RX ring 取 skb ──────────┼──►      │ budget 也還有剩
   │        napi_gro_receive(skb) ──► 往上堆疊    │ → 繼續 poll
   │    收完了嗎?                        │────────┘
   │      是 → napi_complete() → 重開中斷 │
   │      否(用完budget) → 保持關中斷,     │
   │            下一輪 softirq 再 poll    │
   └────────────────────────────────────┘
```

注意那條界線：界線**之上**是硬體中斷 context（不能睡、要極快），界線**之下**是 softirq context（仍不能睡，但可以做較久的活、可被硬體中斷插隊）。這正是 Ch 29「把工作從 top half 推到 bottom half」原則的教科書級應用，而 NET_RX_SOFTIRQ 就是 Ch 30 講的那些 softirq 向量之一。

## 一、NAPI 的資料結構與狀態機

核心結構是 `include/linux/netdevice.h` 的 `struct napi_struct`。一張網卡的**每一條 RX 佇列**通常對應一個 `napi_struct`（多佇列見後面 RSS 一節）。關鍵欄位：

- `poll_list`：把這個 napi 掛到 per-CPU 的 poll list 上（就是 `net_rx_action` 要走的那張清單）。
- `state`：一組 bit flag，最重要的是 `NAPI_STATE_SCHED`——標記「這個 napi 已排程、正在或即將被 poll」。它同時扮演一把鎖：確保同一個 napi 不會被兩個 CPU 同時 poll。
- `poll`：函式指標，指向 driver 提供的 poll 函式（例如 Intel ixgbe 的 `ixgbe_poll`）。**這是每個 driver 各自實作、把 NAPI 框架接到自家硬體的地方**。
- `weight`：這個 napi 單次 poll 的 budget 上限，傳統預設 64。
- `dev`：反指回 `net_device`（Ch 43）。

driver 在 probe 時用 `netif_napi_add()`（`net/core/dev.c`）註冊：告訴 kernel「這條佇列的 poll 函式是誰、weight 多少」。

NAPI 的狀態機很小但要看懂：

```
   [IDLE] ──napi_schedule()──► [SCHEDULED]  (NAPI_STATE_SCHED 被設起)
     ▲                              │
     │                             poll 被 net_rx_action 呼叫
     │                              │
     │                              ▼
     │                          [POLLING]  ── 從 ring 收 skb、往上送 ──
     │                              │
     │         ┌────────────────────┴────────────────────┐
     │      收完了(work < budget)                   沒收完(work == budget)
     │         │                                         │
     └─napi_complete_done()──┐                    保持 SCHEDULED，
       清掉 SCHED、重開中斷    │                    這個 napi 留在 poll list，
                             │                    下一輪 net_rx_action 再處理
                             ▼
                          [IDLE]
```

一句話總結：**`napi_schedule` 把你放進佇列並關中斷，`napi_complete` 把你拿出佇列並重開中斷**，中間 `poll` 反覆被呼叫直到收完或撞到 budget。

## 二、收包完整路徑：從 DMA 到 socket

現在把整條路走一遍。假設一個 TCP segment 到達一張支援 NAPI 的網卡。

### 階段 1：網卡 DMA 把封包寫進 RX ring（Ch 41）

開機時 driver 已經在 RX ring buffer 裡預先掛好一批空 `sk_buff`（Ch 43），每個 descriptor 記著一塊 DMA-mapped buffer 的位址。封包到達時，網卡的 DMA engine **不經過 CPU**，直接把封包內容寫進下一個空 descriptor 指向的 buffer，然後標記該 descriptor 為「已填」。這一步 CPU 完全沒參與——這是 DMA 的意義（Ch 41）。

### 階段 2：網卡發中斷 → driver IRQ handler → `napi_schedule`

DMA 寫完後，網卡拉起中斷。CPU 跳進 driver 註冊的 IRQ handler（top half）。以典型 driver 為例，handler 裡的關鍵動作：

```c
/* driver IRQ handler 的骨架（示意，各家細節不同）*/
static irqreturn_t nic_irq(int irq, void *data)
{
    struct nic_queue *q = data;

    /* 關掉本佇列的 RX 中斷——接下來改用輪詢 */
    nic_disable_rx_irq(q);

    /* 把這個 napi 排上本 CPU 的 poll list，並 raise NET_RX_SOFTIRQ */
    napi_schedule(&q->napi);

    return IRQ_HANDLED;
}
```

`napi_schedule()`（`net/core/dev.c`）內部走 `__napi_schedule()`：把 `napi_struct` 用 `list_add_tail` 掛到 per-CPU 變數 `softnet_data.poll_list`，然後 `__raise_softirq_irqoff(NET_RX_SOFTIRQ)` 標記這個 softirq pending（Ch 30）。**handler 到此返回**——它沒碰任何一個封包內容，快進快出。這就是 top half 該有的樣子。

> `softnet_data` 是 `include/linux/netdevice.h` 定義的 **per-CPU** 結構（Ch 7 的 per-CPU 觀念）。每顆 CPU 有自己的 poll list、自己的 backlog 佇列。per-CPU 化避免了跨核鎖競爭——收包路徑的高吞吐很大一部分建立在「盡量不跨核共享」上。

### 階段 3：NET_RX softirq → `net_rx_action` → driver 的 poll

softirq 在中斷返回時、或由 `ksoftirqd` 執行緒（Ch 30）處理。NET_RX_SOFTIRQ 綁定的 handler 是 `net/core/dev.c` 的 **`net_rx_action()`**。它的骨架邏輯：

```c
/* net/core/dev.c, net_rx_action() 的核心迴圈（簡化）*/
static __latent_entropy void net_rx_action(struct softirq_action *h)
{
    struct softnet_data *sd = this_cpu_ptr(&softnet_data);
    unsigned long time_limit = jiffies + usecs_to_jiffies(netdev_budget_usecs);
    int budget = netdev_budget;   /* 全域總預算，預設 300 */

    for (;;) {
        struct napi_struct *n;

        if (list_empty(&sd->poll_list))
            break;

        n = list_first_entry(&sd->poll_list, struct napi_struct, poll_list);
        budget -= napi_poll(n, &repoll);   /* 呼叫 driver 的 poll，扣掉它做的 work */

        /* 兩道煞車：總 budget 用完，或吃掉太多 CPU 時間 */
        if (budget <= 0 || time_after_eq(jiffies, time_limit)) {
            sd->time_squeeze++;            /* 記一筆「被時間掐斷」*/
            break;
        }
    }
    /* 沒處理完的 napi 留在 poll list，raise 下一輪 NET_RX_SOFTIRQ */
}
```

兩個 budget 要分清楚：

| 名稱 | 誰的預算 | 預設 | 意義 |
|---|---|---|---|
| `weight`（每 napi） | 單一佇列單次 poll | 64 | driver 的 poll 一次最多收幾個封包 |
| `netdev_budget`（全域） | 一輪 `net_rx_action` 跨所有 napi | 300 | 整個 softirq 這一輪最多收幾個封包 |
| `netdev_budget_usecs` | 一輪 `net_rx_action` 的時間 | 2000 µs | 就算 budget 沒用完，超時也讓出 CPU |

為什麼要有這些 budget？因為 softirq 雖然是 bottom half，但它仍會佔用 CPU、延後 user process 和其他 softirq。**budget 是一道公平性煞車**：確保收包不會無限期霸佔 CPU，超過就中止、把剩下的留到下一輪，讓排程器和其他工作有機會插進來。如果封包真的多到一輪吃不完，NET_RX_SOFTIRQ 會被反覆重排，最終若 softirq 一直排不完，會交給 `ksoftirqd` 這個 kernel thread 在正常排程下處理（Ch 30 講過這個 fallback）——這也是為什麼高負載時你會在 `top` 看到 `ksoftirqd/N` 吃 CPU。

`napi_poll` 最終呼叫 driver 的 `poll`（如 `ixgbe_poll`）。driver 的 poll 做的事：

```c
/* driver poll 骨架（示意）*/
static int nic_poll(struct napi_struct *napi, int budget)
{
    int work_done = 0;

    while (work_done < budget && ring_has_packet(rx_ring)) {
        struct sk_buff *skb = nic_fetch_one_skb(rx_ring);  /* 從 ring 取出已填的 skb */
        nic_refill_ring(rx_ring);                          /* 補一個新的空 buffer 回 ring */
        napi_gro_receive(napi, skb);                       /* 往上送（先過 GRO）*/
        work_done++;
    }

    if (work_done < budget) {
        /* 佇列抽乾了：完成 NAPI、重開中斷 */
        napi_complete_done(napi, work_done);
        nic_enable_rx_irq(...);
    }
    /* 若 work_done == budget：不 complete、不重開中斷，
       回傳 budget 讓 net_rx_action 知道「還沒完」，下一輪繼續 poll */

    return work_done;
}
```

看懂這個 `if (work_done < budget)` 是理解 NAPI 的關鍵：

- **收到的比 budget 少** → 代表 ring 抽乾了、當下沒更多封包 → `napi_complete` 退出輪詢、**重開中斷**、回到中斷模式待命。
- **收滿 budget** → 代表可能還有更多 → **不重開中斷**、回傳 budget，讓 `net_rx_action` 下一輪再 poll 這條佇列。持續高流量下，中斷一直維持關閉，系統穩定運作在**純輪詢**模式。

這就是「流量自動決定中斷 or 輪詢」的機制所在——沒有任何地方在「切換模式」，模式是 `work_done < budget` 這個比較的自然結果。

### 階段 4：GRO——進堆疊前先聚合

`napi_gro_receive()`（`net/core/gro.c`）是封包進入協定堆疊前的第一站。**GRO（Generic Receive Offload）** 做一件事：把**同一條流**的多個相鄰小封包，在丟進 L3 之前，聚合成一個大的 `sk_buff`。

為什麼要聚合？因為封包穿越協定堆疊（IP → TCP → socket）的成本，很大一部分是**per-packet 固定開銷**而非 per-byte。10 個 1500-byte 的 TCP segment 各自走一趟 `ip_rcv` → `tcp_v4_rcv`，要付 10 次查 routing、10 次 socket lookup、10 次鎖。如果先把它們合成一個 15000-byte 的大 skb（用 Ch 43 的**非線性 skb**——`skb_shinfo(skb)->frags[]` 掛多個 page），就只走一趟堆疊，per-packet 開銷攤掉 90%。

GRO 怎麼判斷「同一條流可以合併」？它比對封包的 header（source/dest IP、port、TCP seq 是否連續、TCP flags 是否相容等）。`gro_list` 裡維護幾條「正在聚合中」的流。合併發生在：

- 收滿一定數量、或
- `napi_complete` 時（一輪 poll 結束）、或
- 遇到不能合併的封包（如帶 PSH flag、或流不同）時 flush。

GRO 是 **LRO（Large Receive Offload）** 的軟體、通用化版本：

| | LRO | GRO |
|---|---|---|
| 實作位置 | 網卡硬體 | kernel 軟體（`net/core/gro.c`） |
| 通用性 | 綁特定硬體、規則寬鬆 | 對所有網卡一致、規則嚴謹 |
| forwarding/bridging 安全性 | 差（可能改變封包、無法還原）| 好（GRO 記錄足夠資訊，可被 GSO 反向拆回，Ch 45） |
| 現況 | 大多已被 GRO 取代 | 預設開啟 |

GRO 之所以贏過 LRO，關鍵在**可逆**：一個被 GRO 聚合的大 skb，如果這台機器是 router 要把它轉發出去，送包路徑的 GSO（Generic Segmentation Offload，Ch 45）能把它精確拆回原本的封包序列。硬體 LRO 做不到這點，所以在 forwarding 場景會壞事，這也是它被淘汰的主因。

> 你可以用 `ethtool -K eth0 gro off` 關掉 GRO。關掉後單流吞吐通常明顯下降、CPU 上升——這是驗證 GRO 價值最直接的實驗。

### 階段 5：`netif_receive_skb` → L3 → L4 → socket → 喚醒 process

GRO flush 出來的 skb（不論有沒有合併）進入 **`__netif_receive_skb_core()`**（`net/core/dev.c`）。這裡是「離開網路裝置層、進入協定分派」的分水嶺，做幾件事：

1. 跑 **XDP generic** 與 **tc ingress**（如果掛了 eBPF/qdisc filter，Ch 46、跨連到 bpf 課）。
2. 交給 **packet taps**（`AF_PACKET`，這是 `tcpdump` 抓包的點——所以 tcpdump 看到的是這一層的封包）。
3. 依 `skb->protocol`（乙太類型，如 `ETH_P_IP`）查 `ptype_base` 雜湊表，找到對應的 L3 handler，呼叫它。對 IPv4 就是 **`ip_rcv()`**（`net/ipv4/ip_input.c`）。

之後就是協定堆疊往上：

```
   __netif_receive_skb_core()          ← 網路裝置層出口（tcpdump 抓這裡）
        │  依 skb->protocol 分派
        ▼
   ip_rcv()  →  ip_rcv_finish()  →  ip_local_deliver()      (net/ipv4/ip_input.c)
        │  檢查 checksum、查 routing、跑 netfilter PREROUTING/INPUT (Ch 46)
        │  依 IP protocol 欄位（TCP=6）分派
        ▼
   tcp_v4_rcv()                                             (net/ipv4/tcp_ipv4.c)
        │  用 (saddr,sport,daddr,dport) 四元組做 socket lookup
        │  找到對應的 struct sock
        ▼
   tcp_rcv_established() → 資料放進 sk->sk_receive_queue
        │
        ▼
   sk->sk_data_ready()  →  喚醒睡在這個 socket 上的 process
        │
        ▼
   等在 recv()/read() 的 process 被喚醒（Ch 26 wait queue、Ch 9 task 狀態）
   → 從 sk_receive_queue 把資料 copy 到 user buffer → recv() 返回
```

最後這步值得停一下。當初呼叫 `recv()` 但 socket 還沒資料的 process，會被掛到這個 socket 的 wait queue 上、進入 `TASK_INTERRUPTIBLE`（Ch 9 的 task 狀態、Ch 26 的 wait queue/completion）。`sk_data_ready` 觸發 `wake_up`，把它設回 `TASK_RUNNING` 丟回 runqueue（Ch 11），排程器下次挑到它，`recv()` 才真正把資料 copy 出去返回。

**整條路徑跨越三個執行 context**：DMA（無 CPU）→ 硬體中斷（top half）→ softirq（bottom half，收包 + GRO + 協定堆疊大多在這）→ process context（`recv` 的 copy-to-user）。看懂這條「context 接力」，就看懂了收包路徑的骨架。

## 三、RSS / RPS / RFS：把收包攤到多核

單一 `net_rx_action` 在單顆 CPU 上跑。一顆核處理不完 100 GbE，得把收包平行化到多核。

**RSS（Receive Side Scaling）——硬體多佇列**。現代網卡有多條 RX 佇列，每條有自己的 `napi_struct`、自己的中斷（MSI-X vector，Ch 40/41）。網卡用一個雜湊函式（通常 Toeplitz hash，吃四元組）決定每個封包進哪條佇列。不同佇列的中斷用 **IRQ affinity（Ch 15）** 綁到不同 CPU——於是不同流的封包 DMA 到不同佇列、觸發不同 CPU 的 softirq、**真正平行收包**。

RSS 的一個重要副作用：**同一條流的封包永遠進同一條佇列**（雜湊只吃四元組，同流四元組相同）。這保證了同一連線的封包不會被拆到多核亂序處理，維持了 TCP 需要的順序性，也讓 per-CPU 的 GRO 能正確聚合。

**RPS（Receive Packet Steering）——RSS 的軟體版**（`net/core/dev.c`，`get_rps_cpu()`）。網卡只有單佇列、或佇列數少於 CPU 數時，RPS 在 `netif_receive_skb` 早期用軟體算流的雜湊，把封包**丟到另一顆 CPU 的 backlog 佇列**去處理（透過 IPI 觸發那顆 CPU 的 NET_RX_SOFTIRQ）。犧牲一點跨核成本，換取把協定堆疊處理攤開。

**RFS（Receive Flow Steering）——RPS 的「跟著 app 走」版**。RPS 用雜湊決定 CPU，但那顆 CPU 未必是 `recv()` 這條流的 process 所在的 CPU，導致資料要跨核搬、cache 不友善。RFS 記錄「哪條流的 app 跑在哪顆 CPU」，把該流的收包處理導到**同一顆 CPU**，讓 softirq 收完的資料剛好在 app 要讀它的那顆核的 cache 裡。這是 cache locality 的優化，效果在 latency-sensitive 服務上明顯。

```
   ┌─ RSS（硬體）─────────────────────────────────┐
   │  流A ─hash─► RXQ0 ─IRQ─► CPU0 ─► napi0 poll  │  多佇列 + IRQ affinity
   │  流B ─hash─► RXQ1 ─IRQ─► CPU1 ─► napi1 poll  │  → 真並行、零跨核
   │  流C ─hash─► RXQ2 ─IRQ─► CPU2 ─► napi2 poll  │
   └───────────────────────────────────────────────┘

   ┌─ RPS/RFS（軟體，補單佇列網卡）──────────────┐
   │  單一 RXQ ─IRQ─► CPU0 ─► 算 hash            │
   │       ├─流A─► 丟 CPU3 backlog ─IPI─► softirq│  RPS: 攤開處理
   │       └─流B─► 丟 CPU5(app 所在) ────────────│  RFS: 跟著 app 的核走
   └───────────────────────────────────────────────┘
```

MTK 這類 SoC 的網路子系統，多佇列與中斷分佈到多核的調校（哪條佇列綁哪顆核、GRO 開關、budget 調整）是實務效能工作的日常，這節的觀念是那些調校的底層依據。

## 四、XDP：在最早的地方攔截（點一下）

到目前為止，最早能碰到封包的 kernel 點是 `__netif_receive_skb_core`——但那時 `sk_buff` 已經配好了。**XDP（eXpress Data Path）** 讓 eBPF 程式在**更早、`sk_buff` 還沒配置之前**就攔截封包：driver 從 ring 拿到原始 DMA buffer 後，先跑掛在該 napi 上的 XDP program，程式回傳 `XDP_DROP` / `XDP_PASS` / `XDP_TX` / `XDP_REDIRECT`。

`XDP_DROP` 之所以快到能扛 DDoS，正因為它在**配 skb 之前**就把封包丟了——省掉了 `sk_buff` 配置與整條堆疊。這是 DDoS 過濾、L4 load balancer（如 Katran）能用軟體達到硬體級吞吐的原因。XDP 的完整內容是 Ch 46 與本 repo 的 **bpf 課**（那裡從使用者視角寫 XDP program、講 verifier/JIT，對應本課 Ch 52）。這裡只需記住它的**位置**：在 NAPI poll 內、GRO 之前、skb 配置之前的最前線。

## 五、為什麼收包路徑是效能關鍵

收包路徑是 kernel 裡少數「每秒被執行上百萬次」的熱路徑，任何 per-packet 的浪費都被流量放大。三個效能支點：

1. **per-packet 成本**：每個封包固定要付的開銷（skb 配置/釋放、descriptor 處理、鎖、函式呼叫鏈）。GRO 攤掉協定堆疊那段、XDP 在最前面砍掉不要的封包、RSS 平行化——全都在打這個成本。
2. **cache locality**：14 Mpps 下，cache miss 是致命的。RFS 把處理導到 app 所在核、per-CPU 的 `softnet_data` 避免跨核 cache line 彈跳（Ch 23 cache coherence），都是在保護 cache。
3. **中斷合併（interrupt coalescing）**：網卡可設定「累積 N 個封包 or 等 T 微秒才發一次中斷」（`ethtool -c/-C`）。它與 NAPI 互補：coalescing 減少中斷**進入 NAPI 的頻率**，NAPI 減少中斷**打斷收包的次數**。調 coalescing 是典型的 **吞吐 vs. 延遲** 取捨——調大 `rx-usecs` 省 CPU、增吞吐，但每個封包多等一點、延遲升高。

> 這一整套（per-packet 成本、cache、coalescing、budget 調校）是本 repo **perf_bench 課** 與 **networking 課** 的實務主題。這章給的是「這些旋鈕背後 kernel 到底在做什麼」的機制底層。

## 動手：觀測收包路徑

以下都在你的 host（或 QEMU guest，Ch 0）上跑，不用改 kernel。

### 1. 看網卡統計與佇列

```bash
ethtool -S eth0        # 網卡驅動層統計：rx_packets、rx_dropped、
                       #   rx_missed_errors（ring 滿了 DMA 沒地方放）、
                       #   每佇列的 rx_queue_N_packets
ethtool -l eth0        # 看 RX/TX 佇列數（RSS 幾條佇列）
```

`rx_missed_errors` / `rx_no_buffer_count` 上升代表 NAPI 追不上流量、ring 被塞爆——這是收包側 drop 的第一現場。

### 2. 看中斷合併設定

```bash
ethtool -c eth0        # 目前的 coalescing 設定
                       #   rx-usecs: 收到封包後等幾微秒才發中斷
                       #   rx-frames: 累積幾個封包才發中斷

# 調大 rx-usecs → 省 CPU、增吞吐、增延遲（實驗看看）
sudo ethtool -C eth0 rx-usecs 100
```

### 3. 看 NAPI / softnet 統計（收包路徑的體檢表）

```bash
cat /proc/net/softnet_stat
```

每一行對應**一顆 CPU**，是 16 進位。前幾欄的意義（見 `net/core/net-procfs.c` 的 `softnet_seq_show`）：

| 欄位（第 N 欄） | 意義 |
|---|---|
| 第 1 欄 | `processed`：這顆 CPU 處理過的封包數 |
| 第 2 欄 | `dropped`：backlog 佇列滿了被丟的封包數（**非 0 就是警訊**）|
| 第 3 欄 | `time_squeeze`：`net_rx_action` 因 budget/時間用完被中止的次數（**經常增加代表 budget 太小或流量太大**）|

若第 3 欄狂漲，可以調 `sysctl net.core.netdev_budget`（預設 300）與 `netdev_budget_usecs`（預設 2000）。若第 2 欄非 0，RPS 的 backlog（`net.core.netdev_max_backlog`）可能太小。

### 4. 看網卡中斷怎麼分佈到各 CPU（RSS 驗證）

```bash
cat /proc/interrupts | grep -i eth
# 或看某網卡：
grep -E "eth0|ens" /proc/interrupts
```

多佇列網卡你會看到多行（每條佇列一個 MSI-X 中斷），每行顯示該中斷在各 CPU 上被觸發的次數。**如果所有中斷全落在 CPU0**，代表 IRQ affinity（Ch 15）沒散開，可以用 `set_irq_affinity.sh`（driver 附的）或寫 `/proc/irq/N/smp_affinity` 調整。

### 5. 用 bpftrace 追收包（跨連到 bpf 課）

在收包路徑的關鍵函式上掛 kprobe，親眼看它每秒被呼叫幾次、封包多大：

```bash
# 每秒有多少封包穿過 netif_receive_skb（進協定堆疊的門）
sudo bpftrace -e '
  kprobe:netif_receive_skb { @pps = count(); }
  interval:1s { print(@pps); clear(@pps); }'

# GRO 聚合後 skb 的長度分布——看 GRO 有沒有在合併
sudo bpftrace -e '
  kprobe:napi_gro_receive {
    $skb = (struct sk_buff *)arg1;
    @len = hist($skb->len);
  }'

# net_rx_action 每次跑處理了多少（配合 tracepoint 更準）
sudo bpftrace -l 'tracepoint:napi:*'
sudo bpftrace -e 'tracepoint:napi:napi_poll { @[args.dev_name] = count(); }'
```

`napi_gro_receive` 那條若 hist 顯示大量 > 1500 的 skb，代表 GRO 確實在把多個 MTU-sized 封包合成大 skb。把 `ethtool -K eth0 gro off` 後再跑一次對比，長度分布會塌回 1500 附近——這是「看見 GRO 在工作」最直接的方式。

## 對比與取捨

| 方案 | 低流量表現 | 高流量表現 | 為什麼 |
|---|---|---|---|
| 純中斷 | 好（低延遲） | 崩潰（中斷風暴 / livelock） | 每包一中斷，CPU 被進出開銷吃光 |
| 純輪詢 | 差（空轉浪費 CPU/電、輪詢延遲） | 好（一次抽一堆） | 沒有事件驅動，靠定時問 |
| **NAPI** | 好（中斷模式） | 好（自動退化成輪詢） | budget 比較自動切換，兩全 |
| + GRO | 略增延遲（等聚合） | 更好（攤掉 per-packet 堆疊成本） | 合併同流封包，少走幾趟堆疊 |
| + RSS | — | 更好（多核並行） | 硬體多佇列 + IRQ affinity |
| + XDP | — | 極好（DDoS 場景） | 在 skb 配置前就 DROP，成本最低 |

| 攤負載方案 | 佇列來源 | 導向依據 | 適用 |
|---|---|---|---|
| RSS | 硬體多佇列 | 硬體雜湊四元組 | 有多佇列網卡，首選 |
| RPS | 軟體 backlog | 軟體雜湊 | 單佇列網卡補平行化 |
| RFS | 軟體 backlog | app 所在 CPU | 在 RPS 上再優化 cache locality |

## 踩雷集錦

1. **「NAPI 是輪詢模式」——不對，是混合**。NAPI 平時是中斷驅動，只在流量高到「一次 poll 收滿 budget」時才維持關中斷、退化成輪詢。它不是把中斷換成輪詢，而是**讓流量決定用哪個**。這是它勝過純中斷和純輪詢的整個重點。

2. **以為收包工作在中斷 handler 裡做**。中斷 handler（top half）只做 `napi_schedule`——關中斷 + 排程，碰都沒碰封包內容。真正的收包（取 skb、GRO、走協定堆疊）全在 NET_RX softirq（bottom half）。搞錯這條界線，就看不懂為什麼收包 CPU 時間顯示在 softirq / `ksoftirqd` 而非 IRQ。

3. **把 GRO 和 GSO 搞混**。GRO 是**收**包時聚合（多小 → 一大），GSO 是**送**包時分割（一大 → 多小，Ch 45）。它們是一對逆操作。GRO 之所以能安全用在 forwarding，正是因為聚合資訊足夠讓 GSO 精確反拆——這是它贏過硬體 LRO 的關鍵。

4. **`tcpdump` 看不到被 XDP DROP 的封包**。tcpdump 抓在 `__netif_receive_skb_core` 的 packet tap 那層，而 XDP `XDP_DROP` 發生在更早（skb 還沒配）。所以「XDP 明明在丟包但 tcpdump 看不到」不是 bug，是位置使然。要看 XDP 層要用 `xdpdump` 或 XDP 內的 bpf trace。

5. **`/proc/net/softnet_stat` 的欄位是每 CPU 一行、16 進位**。很多人把它當成全域單行讀，或忘了轉 16 進位。第 2 欄（dropped）和第 3 欄（time_squeeze）是體檢收包路徑健康度的兩個核心指標，非 0 / 狂漲各有不同病因（backlog 太小 vs. budget 太小）。

## 進階：再往深一層

- **threaded NAPI（6.x 可開）**：把 NAPI poll 從 softirq context 移到專屬 kernel thread 執行（每個 napi 一條 thread），能被排程器管理、綁 CPU、設優先權，對 latency-sensitive 與 -rt（Ch 31）場景有用。用 `/sys/class/net/eth0/threaded` 開關。這延續了 Ch 31「把中斷/softirq threaded 化」的思路。
- **busy polling（`SO_BUSY_POLL`）**：對極低延遲場景，app 可以請 kernel 在 socket 上「忙等」直接 poll 網卡，跳過中斷/softirq 的排程延遲，用 CPU 換微秒級 latency。
- **DIM（Dynamic Interrupt Moderation）**：kernel/driver 根據流量動態調 coalescing 參數，不用人手調 `rx-usecs`——低流量偏低延遲、高流量偏吞吐，自動化了本章「吞吐 vs 延遲」的取捨。
- **AF_XDP**：XDP 的一個 redirect 目標，把封包零拷貝直送 user-space（kernel-bypass），是 DPDK 之外的另一條高效能收包路徑。屬 bpf 課 / Ch 46 範疇。
- **面試常問**：「一個封包從網卡到 `recv()` 經過哪些步驟？」——照本章階段 1→5 講，能點出 top/bottom half 界線、NAPI 的 budget 邏輯、GRO 的作用、socket 喚醒，基本就是滿分答案。追問「為什麼不每包一個中斷」就是中斷風暴 + NAPI。

## 動手練習

1. **畫出並口述整條路徑**：不看本章，從「封包到達網卡」講到「`recv()` 返回」，每一段標出它在哪個 context（DMA / 硬體中斷 / softirq / process）。卡住的段落回來重讀對應階段。
2. **看見 NAPI 退化成輪詢**：用 `iperf3` 對機器灌流量，同時 `watch -n1 'grep eth0 /proc/interrupts'`。低流量時中斷數快速增加；灌滿流量時，你會看到中斷增速**變慢甚至趨緩**——因為 NAPI 關了中斷改輪詢。這是 NAPI 混合模型最直接的實證。
3. **驗證 GRO 的價值**：`bpftrace` 掛 `napi_gro_receive` 印 skb 長度 hist（見動手節）。先開著 GRO 跑 `iperf3`，再 `ethtool -K eth0 gro off` 跑一次，對比長度分布與 `iperf3` 吞吐/CPU。你應該看到關掉後大 skb 消失、CPU 上升。
4. **看 time_squeeze**：灌大流量時 `watch cat /proc/net/softnet_stat`，觀察第 3 欄（16 進位）是否增加。若增加，把 `sysctl -w net.core.netdev_budget=600`，再看它是否減緩——親手感受 budget 這道煞車。
5. **RSS 是否散開**：`cat /proc/interrupts | grep eth`，看網卡中斷是不是集中在單核。若集中，讀 driver 的 `set_irq_affinity` 腳本或手動寫 `/proc/irq/N/smp_affinity`，把佇列散到多核，再對比收包 CPU 分佈。

## 本章重點整理

- **NAPI = 中斷 + 輪詢的混合**：中斷 handler 只做「關中斷 + `napi_schedule`」快進快出；真正收包在 NET_RX softirq 的 `net_rx_action` → driver `poll` 裡，靠 `work_done < budget` 這個比較**自動**在中斷模式與輪詢模式間切換。
- **完整收包路徑五階段**：DMA 寫 ring（無 CPU）→ 中斷 + `napi_schedule`（top half）→ softirq poll 取 skb（bottom half）→ GRO 聚合 → `netif_receive_skb` → `ip_rcv` → `tcp_v4_rcv` → socket queue → 喚醒 process。跨越 DMA / 中斷 / softirq / process 四個 context。
- **GRO** 把同流小封包合成大 skb（用非線性 skb），攤掉 per-packet 的協定堆疊開銷；是 LRO 的軟體通用版，勝在可被 GSO 逆向拆回、對 forwarding 安全。
- **RSS/RPS/RFS** 把收包攤到多核（硬體多佇列 / 軟體 backlog / 跟著 app 走），**XDP** 在 skb 配置前的最前線攔截。收包是熱路徑，per-packet 成本、cache locality、中斷合併是三大效能支點。

## 自我檢核

- [ ] 不看筆記，能解釋「為什麼不能每個封包一個中斷」，以及 NAPI 如何同時要到中斷的低延遲與輪詢的高吞吐
- [ ] 能說出中斷 handler（top half）在收包路徑裡**只做哪兩件事**，以及為什麼真正的收包在 softirq
- [ ] 能講清楚 `work_done < budget` 這個判斷如何決定「重開中斷 or 繼續輪詢」
- [ ] 面試被問「封包從網卡到 `recv()` 的完整路徑」，能一路講到 socket 喚醒並標出每段的 context
- [ ] 能區分 GRO vs GSO、GRO vs LRO，並解釋 GRO 為何對 forwarding 安全
- [ ] 能用 `/proc/net/softnet_stat` 判斷收包路徑是否 drop / time_squeeze，並知道各對應調哪個旋鈕

## 延伸閱讀

### 官方文件

- **[Documentation/networking/napi.rst](https://www.kernel.org/doc/html/latest/networking/napi.html)**
  - **讀哪裡**：整篇。這是 NAPI 的官方權威說明，涵蓋 `napi_schedule`/`napi_complete`、budget、threaded NAPI、busy polling
  - **和本章的關聯**：本章第一、二節的機制以此為準；threaded NAPI 進階節也出自這裡

- **[Documentation/networking/scaling.rst](https://www.kernel.org/doc/html/latest/networking/scaling.html)**
  - **讀哪裡**：RSS、RPS、RFS、XPS 各節
  - **能學到什麼**：把封包攤到多核的完整家族與各自的設定方法，本章第三節的延伸

### 深入文章

- **[Illustrated Guide to Monitoring and Tuning the Linux Networking Stack: Receiving Data](https://blog.packagecloud.io/monitoring-tuning-linux-networking-stack-receiving-data/)** — packagecloud
  - **讀哪裡**：從網卡中斷到 socket 一路逐函式追，配大量原始碼引用與可調參數
  - **為什麼值得讀**：目前網路上把收包路徑講得最完整、最貼源碼的長文；本章是它的濃縮 + v6.12 對齊版，想看每個函式的展開讀它
  - **前提**：跟得上本章的階段 1–5

- **[LWN: The rest of the NAPI story / driver porting](https://lwn.net/Articles/833840/)** 及 LWN 上的 GRO 系列
  - **讀哪裡**：NAPI 與 GRO 的設計討論、演進脈絡
  - **能學到什麼**：這些機制**為什麼**長成現在這樣（history + trade-off），補足官方文件只講「怎麼用」的缺口

### 源碼

- **[net/core/dev.c @ v6.12](https://elixir.bootlin.com/linux/v6.12/source/net/core/dev.c)** — Bootlin
  - **看哪裡**：`net_rx_action`、`napi_schedule`/`__napi_schedule`、`napi_complete_done`、`__netif_receive_skb_core`、`get_rps_cpu`
  - **怎麼配本章**：本章給的函式名都能在這裡點開跳轉；GRO 部分在 `net/core/gro.c`，IP 在 `net/ipv4/ip_input.c`，TCP 在 `net/ipv4/tcp_ipv4.c`

收包路徑把封包送進了 socket。下一章我們走反方向——`send()` 從 socket 出發，經 TCP/IP 封裝、qdisc 排隊、GSO 分割，最後 DMA 給網卡送出去，看送包路徑和收包路徑的對稱與不對稱。

→ [Ch 45 socket 層與送包路徑：qdisc、GSO、TX](./45-socket-tx-path.md)
