# Ch 33 — socket / skb 家族：AF_PACKET、AF_UNIX、pgv、各種 skb

> **目標**：搞懂 `struct sk_buff`（skb）這個「網路資料的萬用容器」長什麼樣、為什麼它的記憶體佈局（head/data/tail/end、`skb_shared_info`）天生就是指標運算密集的地方；認得 `AF_PACKET`、`AF_UNIX`、raw socket 這幾個攻擊面各自怎麼把 user 可控的資料灌進 kernel；能講出 1–2 個公開 CVE 的 root cause，並知道 skb 為什麼既是 [Ch 19](./19-spray-objects.md) 牌庫裡的常客，也是 bug 本身的高發地。

> **環境**：Linux 6.6 LTS、x86-64。skb 的欄位 offset、`skb_shared_info` 的確切佈局會隨 config（`CONFIG_NET`、各種 `offload`/`XFRM` 選項）與版本飄動，本章給的是概念與相對位置，實際 offset 請用你的 kernel 搭 `pahole` 確認。CVE 的 root cause 描述已用 WebSearch 查證官方公告與研究者的 root cause analysis（Red Hat Bugzilla、oss-security、Project Zero 系列的方法論一致），commit hash 凡查到就附上，查不到就只描述模式。未實測處會標注。

## 為什麼這一章要單獨講

[Ch 30](./30-netfilter-nftables.md) 講的是 netfilter 規則引擎本身，[Ch 31](./31-io-uring.md) 講 io_uring 的 async ring。這一章要講的是更底層、更橫向的一層：**所有網路資料，不管走哪個 socket family，最終都裝在同一種容器裡**——`sk_buff`。理解 skb 的佈局和各 socket family 怎麼把資料塞進 kernel，等於拿到一張能橫跨 TCP/IP、netlink、AF_PACKET、AF_UNIX 的通用地圖。

這也是為什麼 skb 在 [Ch 19 — heap spray 物件大全](./19-spray-objects.md) 的牌庫表裡占了一格：它 size 可調、內容可控、配置方式多樣（`send()`、`sendmsg()`、`socket()` 本身）。這一章要把「為什麼 skb 好用」講到底層機制這一層，而不是停在「牌庫表上有它」。

## 先建立直覺

把 socket 想成「郵局窗口」，skb 想成「郵局用的標準信封」。不管你要寄的是 TCP 封包、UDP 封包、還是 kernel 內部模組之間傳的訊息，郵局都把內容裝進同一種信封格式，貼上地址（protocol header）再送出。問題是：**這個信封的尺寸不是固定的**——它會視內容大小，在前面留一段「可以往前塞資料」的空間（headroom），後面留「可以往後長」的空間（tailroom），而且信封末端還藏了一張「共享資訊卡」（`skb_shared_info`），記著這封信被複製了幾次、有沒有被切成分片（fragment）。

```
一個 sk_buff 的記憶體佈局（概念圖，不含 struct sk_buff 本身的 metadata）

         head                                         end
          │                                            │
          ▼                                            ▼
          ┌──────────┬───────────────────┬─────────────┬──────────────────┐
          │ headroom │  實際資料 (data)    │  tailroom   │ skb_shared_info  │
          └──────────┴───────────────────┴─────────────┴──────────────────┘
                     ▲                   ▲
                    data                tail

  skb->head  = 整塊配置的起點
  skb->data  = 目前「看得到」的資料起點（push header 會讓它往左移）
  skb->tail  = 目前資料結束的位置（append 資料會讓它往右移）
  skb->end   = 這塊配置可用空間的結束（超過這裡就是 OOB）
```

這張圖的重點：**`data`、`tail` 都是會移動的指標，而移動的依據（要 push 多少 header、要 put 多少資料）經常來自封包本身的欄位**。如果某段 code 算錯了該往前推多少、該往後長多少，而且沒有在移動前驗證「移動後還在 `head`~`end` 之間」，就是一個 skb OOB 的典型現場。

> 如果你對 OOB / UAF 的分類詞彙還不熟，先回看 [Ch 10 — kernel 漏洞類型學](./10-vulnerability-taxonomy.md)。

## 物件模型：你要認得的結構

### `struct sk_buff` 本身

`sk_buff`（定義在 `include/linux/skbuff.h`）是一個**大結構**（包含雙向鏈結指標、`dev`、各層 header 的 offset 快取、checksum 狀態、一堆 bitfield 標記），但攻擊面主要落在它管理的**資料緩衝區**，而不是這些 metadata 欄位本身。關鍵指標四個：`head`、`data`、`tail`、`end`，如上圖。

資料緩衝區的配置方式（`__alloc_skb()` → `kmalloc_reserve()`）就我所知走一般 `kmalloc`，size 由 `len + headroom reserve`（網路層預留的 `NET_SKB_PAD` 等）決定，所以**它落在一般 kmalloc-N cache**，可以和其他 kmalloc 物件打對台——這正是 [Ch 19](./19-spray-objects.md) 把它列為可調 size 牌的原因。

### `skb_shared_info`：藏在資料緩衝區尾端的另一個結構

skb 的資料緩衝區末端（`skb_end_pointer(skb)` 之後）疊著 `struct skb_shared_info`，記錄：

- `dataref`：這塊資料被幾個 skb 共享（clone 時只複製 `sk_buff` 本身，資料區共享引用計數）
- `nr_frags` / `frags[]`：分片資訊，每個分片是一個 `struct skb_frag_t`（指向某個 page + offset + size）
- `frag_list`：鏈結到其他 skb（用於 fragmentation / GSO）

這個結構本身不在一般 kmalloc 的「可見」範圍內——它緊貼著資料區尾端，所以任何讓 `tail`/`end` 計算錯誤的 bug，都有機會順手把 `skb_shared_info` 也覆寫掉，殺傷力比單純覆寫資料區更大：覆寫 `dataref` 可以製造錯誤的共享計數，進而導向 UAF；覆寫 `frags[]` 可以讓後續的分片讀寫指向任意 page。

### `skb_clone()` vs `skb_copy()`：共享還是複製

- `skb_clone()`：複製一份新的 `struct sk_buff`，但**資料區共享**（`dataref` 加一）。便宜，但代表兩個 skb 的資料區生命週期綁在一起——其中一個釋放資料區的時機算錯，另一個就拿著懸空指標。
- `skb_copy()`：資料區也整塊複製，各自獨立，貴但安全。

這個「共享 vs 複製」的設計決策和 glibc 的 copy-on-write 思路類似：高效能網路堆疊大量用 clone 省記憶體複製的成本，代價是**生命週期管理變複雜**，這也是歷史上 skb UAF bug 反覆出現的根源之一。

### 指標運算錯在哪：一個通用的 bug 模式

skb 相關的 OOB bug，幾乎都能還原成同一個形狀：某個函式要往 `data`/`tail` 的方向移動一段 user 可控的長度，卻漏檢查移動後是否還落在 `head`~`end` 之間。

```c
/* 偽碼，說明模式；不是任何一個 kernel 版本的原文 */
unsigned int len = get_unaligned_be16(ptr);     /* 來自封包欄位，攻擊者控制 */

/* skb_push：把 data 往左移，通常用來疊加 header */
skb_push(skb, len);
/* skb_push 內部等同：
 *   skb->data -= len;
 *   if (skb->data < skb->head) ... 【這裡的檢查如果漏了或算錯，就是 OOB】
 */

memcpy(skb->data, header_template, len);        /* 寫到已經往左移過的 data 指標 */
```

或是反方向：

```c
/* skb_put：把 tail 往右移，通常用來 append 資料 */
unsigned char *p = skb_put(skb, len);
/* 等同：
 *   if (skb->tail + len > skb->end) ... 【同樣的漏檢模式，只是方向相反】
 *   skb->tail += len;
 */
memcpy(p, payload, len);
```

主線 kernel 裡 `skb_push()`/`skb_put()` 本身有 `BUG_ON`/`WARN_ON` 等級的邊界檢查（就我所知，請以你版本的 `include/linux/skbuff.h` 為準），所以**真正會出事的，往往不是呼叫 `skb_push`/`skb_put` 本身算錯，而是呼叫者在呼叫它們之前,自己先用另一條路徑算出了一個錯誤的 `len`**——例如某個協議的欄位長度計算有整數下溢（對照 [Ch 10](./10-vulnerability-taxonomy.md) 的整數類 bug），把一個本來該被拒絕的巨大長度,偽裝成一個通過檢查的小數字,再交給 `skb_put`。這也是為什麼讀 skb 相關 CVE 時,真正的 root cause 常常在呼叫端的長度運算，而不是 skb 核心 API 本身。

### socket family 如何把資料灌進 skb

```
                         socket(2) / sendmsg(2) / 其他 syscall
                                        │
          ┌─────────────┬───────────────┼───────────────┬─────────────┐
          ▼             ▼               ▼               ▼             ▼
      AF_INET/6      AF_UNIX         AF_PACKET       AF_NETLINK    AF_ALG / 其他
     TCP/UDP stack   本機 IPC        raw L2 封包      kernel<->user  crypto 等
          │             │               │               │
          ▼             ▼               ▼               ▼
     走完整協議     skb 直接進對方      skb 直通/mmap     skb 包著 netlink
     堆疊，層層     socket 的接收佇列   ring buffer       message
     push header      （+ SCM_RIGHTS）  （pgv）
```

三個 family 各自的攻擊面性質差很多，分別看。

## AF_PACKET：pgv 與 packet ring，L2 的直接通道

`AF_PACKET`（`socket(AF_PACKET, SOCK_RAW, htons(ETH_P_ALL))`）讓 user process 直接收發 L2 封包，需要 `CAP_NET_RAW`。它最有名的攻擊面特徵是 **pgv（packet-get-vector）/ packet ring**：用 `setsockopt(fd, SOL_PACKET, PACKET_TX_RING/PACKET_RX_RING, ...)` 要求 kernel 配一塊 ring buffer，再用 `mmap()` 把這塊記憶體映射進 user space。

```
user space                              kernel space
───────────                             ─────────────
setsockopt(PACKET_RX_RING, &req) ──────▶ packet_set_ring()
                                           配置 pg_vec（一組 struct page*）
mmap(fd, ..., size) ◀───────────────────  把這些 page 映射回 user
                                           （user 和 kernel 共享同一組實體頁）
讀/寫 mmap 區域 ◀──────────────────────▶ kernel 收封包時直接寫進這些 page
```

這個機制讓 user space 和 kernel **共享實體頁**，是後面 [Ch 25 — USMA 與 page-level spray](./25-usma-page-spray.md) 要講的「把 kernel page 映射進 userspace」最經典的入口之一——這裡我們只點出入口，page-level spray 的完整機制留給 Ch 25，不重複講。

### PACKET_VERSION：V1/V2/V3 差在哪，為什麼版本切換是個獨立操作

`AF_PACKET` 的 ring 協議本身有三代版本，各自的 `tp_status`/block 格式不同：

| 版本 | 特徵 | 與本章相關的重點 |
|---|---|---|
| `TPACKET_V1` | 最早期格式，固定大小的 frame | 沒有 retire-block timer |
| `TPACKET_V2` | 加入 timestamp 精度、VLAN 資訊欄位 | 同樣沒有 retire-block timer |
| `TPACKET_V3` | 引入「block」概念，一個 block 可以裝多個封包，搭配一個 **retire-block timer** 定時把尚未填滿的 block 交回 user space | CVE-2016-8655 的那個 timer 就是這裡來的 |

`PACKET_VERSION` 這個 setsockopt **可以在任何時候呼叫**，不限定在建 ring 之前；而 `PACKET_RX_RING`/`PACKET_TX_RING` 這個 setsockopt（也就是 `packet_set_ring()`）需要讀取「當前版本」來決定要不要配置 V3 專屬的 timer 物件。這兩個 setsockopt 各自是獨立的系統呼叫，kernel 沒有把它們當成一個原子操作——這正是 race 的根。CVE-2016-8655 之後的修補，本質上是把這兩個原本獨立的操作用同一把 socket lock 串起來，變成互斥。

### CVE-2016-8655：version 切換和 ring 建立之間的 race

這是 AF_PACKET 最知名的本地提權 CVE，Philip Pettersson 在 2016 年發表（oss-security 公告）。

root cause：`packet_set_ring()` 在建立 ring 時會根據 socket 目前的 packet version（V1/V2/V3，用 `PACKET_VERSION` setsockopt 切換）決定要不要配一個 retire-block timer（V3 才有）。bug 在於：

1. 執行緒 A 呼叫 `setsockopt(PACKET_RX_RING, ...)`（走 `packet_set_ring()`），先初始化好 V3 的 retire-block timer。
2. 在 `packet_set_ring()` **還沒返回之前**，執行緒 B 呼叫 `setsockopt(PACKET_VERSION, ...)` 把版本切回 V1/V2。
3. `packet_set_ring()` 原本的版本檢查只看「ring 是否已經建立」，這個時間點太早，攔不住這個切換。
4. socket 關閉時，teardown 路徑只對 V3 以上的版本清理那個 timer；版本已經被切成 V1，於是 timer 物件被釋放，卻沒有真的被 cancel ——**use-after-free**。

```
Thread A                                Thread B
────────                                ────────
setsockopt(PACKET_RX_RING)
  packet_set_ring()
    初始化 V3 retire-block timer
    ... (還沒 return) ...
                                         setsockopt(PACKET_VERSION, V1)
                                           版本檢查只看 ring 存在與否 → 通過
                                           版本切換成功
  packet_set_ring() 返回
close(fd)
  teardown：版本是 V1，不清 V3 timer
  → timer 物件被 free，但沒被正確 cancel/dereference 乾淨 → UAF
```

修補方式（seclists 公告所述）：在 `packet_setsockopt()` 切版本時、以及 `packet_set_ring()` 一開始，都拿 socket lock，讓這兩條路徑不能再交錯。

這個案例教的東西超出 AF_PACKET 本身：**「兩個 syscall 各自檢查都合理，但中間有一個沒被鎖保護的窗口」**是 race condition bug 的典型形狀，[Ch 39–41](./39-concurrency-model.md) 會系統性地講這類 bug 怎麼找、怎麼拉長窗口。觸發這個 bug 的前提是能開 AF_PACKET raw socket,也就是具備 `CAP_NET_RAW`，NVD 描述為本地提權或 DoS。

> 查證備註：oss-security 公告引用的修補 commit 是 `f6fb8f10`，但 NVD/OSV 的參照是 `84ac7260`；WebSearch 沒能確認哪個是真正合入主線的 hash，動手練習會請你自己 `git log` 核對。

## AF_UNIX：SCM_RIGHTS、垃圾回收與 skb 生命週期

`AF_UNIX`（unix domain socket）是本機 IPC 的主力，特色是能用 `SCM_RIGHTS` 把一個檔案描述符（fd）當訊息內容送給另一個 process。這個機制在 kernel 裡的實作方式，正好展示 skb 生命週期管理的複雜度。

### 為什麼 fd 傳遞需要垃圾回收（GC）

一個 unix socket 傳送的訊息是裝在一個 skb 裡，排進對方的接收佇列（`sk_receive_queue`）。如果這個訊息帶著 `SCM_RIGHTS`（一個或多個 fd），這些 fd 所指的檔案（可能就是另一個 unix socket 本身）的引用計數會隨著這個 skb 一起活著。

**問題**：兩個 unix socket 可以互相把對方的 fd 塞進 `SCM_RIGHTS` 傳給對方，形成一個環（cycle）。如果兩邊都關閉、而這個帶 fd 的訊息都還沒被 `recvmsg()` 收走，一般的引用計數永遠不會降到 0——這是經典的垃圾回收問題，kernel 用 `unix_gc()`（在 `net/unix/garbage.c`）掃描所有 unix socket 建的圖，找出孤立的環，強制釋放裡面排隊的 skb。

```
  socket A ──(SCM_RIGHTS 傳遞 fd_B)──▶ 排進 A 的 skb，skb 裡存著指向 B 的參照
     ▲                                                         │
     │                                                         ▼
  (SCM_RIGHTS 傳遞 fd_A)──────────────────────────────── socket B

  兩邊都 close() 之後：
    一般的 file refcount：A 的 fd 表不再直接持有 B（已關閉），
    但「A 排隊的那個 skb」還握著 B 的參照，反之亦然
    → 兩者互相撐住對方的引用計數，誰都降不到 0
    → 一般的「用完就 put」邏輯處理不了這種環，需要專門掃描
```

這和 user-space 常見的「兩個物件互相持有 `shared_ptr` 造成記憶體洩漏」是同一個結構性問題，只是這裡洩漏的是 kernel 物件而不是 user-space 記憶體，所以 kernel 自己得跑一套類似 tracing GC 的機制來解決。

### CVE-2023-4622：`unix_stream_sendpage()` 與 GC 的競態

這個 CVE 展示了 unix socket 的 skb 生命週期管理和垃圾回收路徑之間缺乏鎖保護的競態：

- `unix_stream_sendpage()` 這條路徑（`sendfile`/`splice` 類操作使用）會嘗試把資料附加（append）進對方接收佇列裡「最後一個」skb，前提是這個 skb 還能被合併。
- 與此同時，`unix_gc()` 的垃圾回收路徑可能判定這個 socket 的某段訊息屬於一個孤立環，把對應的 skb 釋放掉。
- 兩者對同一個 skb 的存取**沒有用同一把鎖保護**：`unix_stream_sendpage()` 在 append 之前沒有鎖住對方的接收佇列，GC 卻可能已經把那個 skb 釋放——結果是對已釋放記憶體的存取，use-after-free。

```
Process A (sendfile/splice 走到 unix_stream_sendpage)        unix_gc()（垃圾回收，背景觸發）
───────────────────────────────────────────────            ─────────────────────────────
拿到對方 peer socket 的最後一個 skb 指標
（沒有鎖住 peer->sk_receive_queue）
                                                              判定某個 fd 環已孤立
                                                              釋放其中排隊的 skb
嘗試把新資料 append 進剛才拿到的 skb
→ 存取已釋放的 skb → UAF
```

修補 commit `790c2f9d15b5`（WebSearch 查到）在 6.4.12 修復，做法是讓 `unix_stream_sendpage()` 也要鎖住對方的接收佇列才能操作。Red Hat 評為 CVSS 6.6，NVD 評 7.0。這個 CVE 同系列還有 CVE-2023-53469（同一函式的 null pointer dereference，同一位研究者回報）與 CVE-2023-6531（GC 刪除 skb 與 `unix_stream_read_generic()` 之間另一條競態）——同一塊程式碼在短時間內出了三個相關問題，說明「skb 生命週期 + 垃圾回收」這塊的鎖設計本身就是脆弱帶。

> 查證備註：6.5+ 之後 `sendpage()` 介面經過重構，這條特定路徑按 WebSearch 結果所述已不存在；舊版 kernel 的 backport 修補仍適用這個分析。

## raw socket 與 netlink：簡短提一下

- **raw socket**（`AF_INET`/`AF_INET6` + `SOCK_RAW`）讓 user space 自己組 IP/ICMP header，需要 `CAP_NET_RAW`。攻擊面集中在「kernel 信任 user 填的 header 欄位到什麼程度」——這類 bug 多半發生在特定協議的 raw 處理函式裡，不是 skb 佈局本身的問題，這裡不展開。
- **netlink**（`AF_NETLINK`）是 kernel 與 user space 之間結構化通訊的主幹道（取代舊式的 `ioctl` 大雜燴），[Ch 30](./30-netfilter-nftables.md) 的 nf_tables 就是透過 netlink message 設定規則。netlink message 本身也裝在 skb 裡，但 bug 通常落在「某個 netlink attribute 的 parser」而不是 skb 層，這部分留給各子系統自己的章節。
- **AF_ALG**（crypto user API）、**AF_VSOCK**（虛擬機之間的通訊）這類較冷門的 family，同樣是基於 skb 的訊息傳遞，歷史上也各自出過獨立的 CVE——它們的共同點是「使用者數量較少、被 fuzz 的強度較低」，value 落在「新/冷門 family」這個維度，而不是 skb 本身的機制有什麼不同。評估一個冷門 family 的風險時，先確認它是否需要特殊 capability、是否預設啟用（`CONFIG_` 選項），這兩點比「它用不用 skb」更決定實際可達性。

### 把本章的 bug 對回 Ch 10 的分類

| 案例 | [Ch 10](./10-vulnerability-taxonomy.md) 分類 | 升級路徑 |
|---|---|---|
| skb `tail`/`end` 算錯（概念模式） | OOB write（通常源自上游的整數類 bug） | 覆寫 `skb_shared_info` 或相鄰物件 → 控流或資料篡改 |
| CVE-2016-8655（AF_PACKET） | race condition → UAF | 典型「free 掉帶 callback 的物件、spray 佔位」路徑，見 [Ch 18](./18-uaf-double-free.md) |
| CVE-2023-4622（AF_UNIX） | race condition（缺鎖）→ UAF | 同上，差別在觸發時機由 GC 的背景掃描決定，較不受攻擊者直接控制 |

這張表的用意是：**本章沒有引入新的 bug 分類，只是把 Ch 10 的詞彙套在「網路/IPC 子系統」這個具體場景上**。你讀到一個新的 socket 相關 CVE 時，先問它屬於表格左欄哪一類，再問升級路徑，而不是從零開始理解。

## 為什麼 skb 同時是好牌也是地雷

```
skb 的兩面
──────────
好用的 spray/佔位物件：
  · size 可調（送多長的訊息就配多大）
  · 內容可控（資料區就是你 send 的 bytes）
  · 配置便宜、user 觸發容易（send/sendmsg 就能配）
  · 落一般 kmalloc cache，能跟其他物件打對台

本身是 bug 來源：
  · head/data/tail/end 四個指標的運算貫穿整個網路堆疊，算錯一處就是 OOB
  · skb_shared_info 緊貼資料區尾端，OOB 常順手波及它
  · clone 製造的「資料區共享」讓生命週期管理變複雜，UAF 溫床
  · 多個 socket family 共用同一套 skb 基礎設施，一個底層 bug 可能橫跨多個攻擊面
```

這個雙重身分不是巧合：**正是因為 skb 被設計成「通用、輕量、可以快速 clone」，它才同時具備「user 容易大量配置可控內容」和「生命週期與邊界管理複雜」兩個特質**——前者讓它成為好牌，後者讓它成為地雷。

### 用 UDP socket 把 skb 當成通用牌來 spray

接續 [Ch 19](./19-spray-objects.md) 的牌庫表，這裡示範一個概念骨架：用 `connect()` 過的 UDP socket 送資料，讓每次 `send()` 都在 kernel 配一個資料區可控、size 可調的 skb。

```c
/* 概念骨架，省略錯誤處理；size 要扣掉 UDP/IP header 的開銷才會精確落在目標 cache */
int spray_skb_via_udp(size_t payload_len, char fill_byte, int count) {
    int fds[count];
    struct sockaddr_in dst = {
        .sin_family = AF_INET,
        .sin_port   = htons(9),              /* 任何一個會被丟棄的 port 即可，不需要真的有人收 */
        .sin_addr.s_addr = htonl(INADDR_LOOPBACK),
    };
    char *buf = malloc(payload_len);
    memset(buf, fill_byte, payload_len);

    for (int i = 0; i < count; i++) {
        fds[i] = socket(AF_INET, SOCK_DGRAM, 0);
        connect(fds[i], (struct sockaddr *)&dst, sizeof(dst));
        send(fds[i], buf, payload_len, 0);   /* 這次 send 配置的 skb 資料區就是 buf 的內容 */
        /* 故意不 close(fds[i])：要讓 skb 留在對方（loopback）的接收佇列裡，
           否則資料一旦被對方收走/丟棄,skb 就釋放了,佔位瞬間即逝 */
    }
    return 0;
}
```

這裡的關鍵限制（也是最容易忽略的踩雷點）：**UDP 封包一旦被目的地處理掉（即使是被丟棄），對應的 skb 就釋放了**。要讓 skb 真正「佔住」某塊記憶體一段時間，通常要讓它停留在某個尚未被讀走的接收佇列裡（例如故意不 `recv()` 的 loopback UDP、或前面講的 AF_UNIX 排隊 skb），這個限制和 [Ch 19](./19-spray-objects.md) 裡 `setxattr` 的「用完就釋放」是同一類問題：**配置到手不代表佔住到手，佔位時間才是重點**。

## 利用思路（概念層級）

我們不給 turnkey exploit，只講骨架思路，對應 [Ch 10](./10-vulnerability-taxonomy.md) 的升級路徑：

1. **把 skb OOB write 轉成堆布局攻擊**：如果能透過某條協議路徑的欄位錯誤讓 `tail` 多推進幾個 byte，先確認覆寫到的是「資料區尾端的 padding」還是「`skb_shared_info`」還是「下一個 heap object」。落在 `skb_shared_info` 的話，`frags[]` 是高價值目標（偽造一個指向任意 page 的 frag，之後讀寫這個 skb 就等於讀寫那個 page）。
2. **把 AF_PACKET ring 的 UAF 轉成控流**：CVE-2016-8655 這類「free 掉一個帶函式指標或 callback 的物件、再用別的配置把同一塊記憶體佔住」的模式，後續手法和 [Ch 18 — UAF/Double Free](./18-uaf-double-free.md) 完全一致：用 [Ch 19](./19-spray-objects.md) 的牌去佔那個被釋放的 slot，再觸發對「假裝還是原物件」的操作。
3. **AF_UNIX 的 fd cycle + GC race**：這類 bug 的利用價值通常是 UAF 讀寫原語，拿到之後走 [Ch 21](./21-heap-to-rip.md)（劫控流）或 [Ch 29](./29-data-only-attack.md)（data-only）皆可，取決於當前 kernel 的 CFI 狀態。
4. **用 skb 當任意 size 的可控載體，墊在 victim 周圍**：和 [Ch 20 — heap grooming](./20-heap-grooming.md) 的思路完全一致，差別只是「配置手段」換成 `send()`/`sendmsg()`。skb 的優勢是**你可以精確控制送出的 byte 數，進而精確控制配置的 size**，這點比某些 size 階梯較粗的物件更好用。

```
概念骨架（不是完整 exploit）：

  1. 先用大量 sendmsg() 把目標 kmalloc-N cache 填到快滿（墊片）
  2. 觸發 bug（free 掉一個 victim 物件 / 或製造 OOB write 的時機）
  3. 緊接著用 sendmsg() 送出內容可控、size 對齊的 skb 資料區
     期望它落進剛被 free 的那個 slot，或落在 OOB write 能碰到的位置
  4. 後續對這個 socket 的操作（read/再次 send/close）
     會把 kernel 當成在操作原本的 victim 物件，實際上在操作你塞的 bytes
```

## 對比取捨表

| socket family | 誰能用 | 攻擊面性質 | 典型 bug 位置 | 作為 spray 牌的價值 |
|---|---|---|---|---|
| AF_INET/6（TCP/UDP） | 一般 user（部分需權限） | 完整協議堆疊，面大但被研究最多 | 擁塞控制、offload、特定 socket option 處理 | 低（間接，不好精準控制 size/時機） |
| AF_PACKET | 需要 `CAP_NET_RAW` | pgv/ring 的 page 級共享、version 切換 race | `packet_set_ring`、ring teardown | 高（page 級 spray，見 Ch 25） |
| AF_UNIX | 一般 user | fd 傳遞、GC、skb 生命週期 | `unix_gc`、`sendpage`/`splice` 路徑 | 中（skb 本身可控、但 GC 時機不易操控） |
| AF_NETLINK | 視子系統（nf_tables 等需權限） | attribute parser，落在各子系統自己 | 各子系統的 `nla_parse` 使用方式 | 低（攻擊面在子系統，不在 skb 層） |

## 踩雷集錦

1. **「skb OOB 一定能馬上控流」**：錯。多數 skb OOB 一開始只是讀寫到相鄰記憶體的幾個 byte，離「拿到函式指標」還有好幾步升級，中間常要靠 [Ch 20](./20-heap-grooming.md) 的布局把正確的 victim 物件放到正確位置。
2. **「AF_PACKET 一般 user 就能打」**：不對，開 raw packet socket 需要 `CAP_NET_RAW`；但注意 [Ch 36 — user namespace](./36-user-namespace.md) 會講這個 capability 在某些場景下怎麼變得「不需要真的是 root」也能拿到。
3. **「skb_clone 之後兩個 skb 完全獨立」**：錯，clone 只複製 `struct sk_buff` 本身，**資料區共享**（`dataref` 引用計數）。以為兩者獨立去推論生命週期，會算錯 UAF 的觸發條件。
4. **「checksum/metadata_csum 類機制能擋 skb 層的構造攻擊」**：skb 沒有這類完整性機制，這條踩雷點是跟 [Ch 34](./34-filesystem-attack-surface.md) 的 fs checksum 誤解相對應提醒：網路層的 bug 完全不靠「構造資料能不能通過 checksum」這種防線，protocol 自己的 checksum（如 TCP checksum）檢查的是傳輸錯誤不是惡意構造。
5. **「version 切換這種 race 現在的 kernel 應該都修了」**：單一 CVE 修的是單一函式對的鎖，不代表整類「兩個 syscall 路徑共用一個沒鎖好的狀態」的問題已經絕種——AF_UNIX 那三個相關 CVE 在同一塊程式碼短期內連出三次就是證據。
6. **把 `skb_push`/`skb_put` 當成「有邊界檢查就安全」**：這兩個 helper 本身的檢查只保證「這次呼叫沒有超出目前的 `head`~`end`」，它們**完全不驗證傳入的 `len` 本身合不合理**。如果呼叫端算出的 `len` 本身就是錯的（整數下溢、溢出成一個看起來很小的正數），helper 會乖乖地用這個錯誤的 `len` 去移動指標——邊界檢查防的是「同一次呼叫內的越界」，防不了「呼叫端給錯參數」。

## 進階：再往深一層

- **XDP / eBPF 與 skb 的關係**：現代高效能封包處理（XDP）在 skb 建立之前就攔截封包，用 `struct xdp_buff` 這個更輕量的結構。這條路徑繞過了本章講的 skb 佈局，但引入了自己的攻擊面（[Ch 32 — eBPF](./32-ebpf.md) 會談 verifier/JIT 層，這裡不重複）。
- **GSO（Generic Segmentation Offload）與 skb_shared_info**：大型封包的分段資訊存在 `skb_shared_info` 裡，GSO 相關的 bug 歷史上出現過（某些 CVE 的描述句型是「crafted GSO packet」），root cause 經常也是 `frags[]`/`nr_frags` 的計數錯誤，和本章講的模式一致。
- **從 netfilter 回看 skb**：[Ch 30](./30-netfilter-nftables.md) 的 nf_tables target/match 處理的也是 skb；CVE-2021-22555（`xt_compat_target_from_user()` 的 OOB write，走 32/64-bit compat 轉換路徑的 `IPT_SO_SET_REPLACE` setsockopt）本質上是 netfilter 子系統的 bug，但觸發路徑是 socket 的 setsockopt——這再次說明「攻擊面」常常橫跨章節邊界，讀 kernelCTF writeup 時要習慣這種跨子系統的視角。
- **`skb_shared_info` 的 hardening 方向**：近年對 `frags[]`/`nr_frags` 這類欄位的驗證逐步加嚴（各版本的 stable fix 持續在修），但結構本身「緊貼資料區尾端」的設計沒有變——這是效能考量（少一次指標追蹤）換來的結構性風險，短期內不會改。讀新的 skb 相關 syzbot 報告時，留意是不是又是這個老地方。
- **容器網路命名空間放大攻擊面**：容器化環境裡,每個網路 namespace 都有自己的一套 socket/skb 流量,這代表「攻擊面」隨容器數量線性增加,也代表一個 namespace 內的 bug 不一定能跨到 host——這個邊界問題留給 [Ch 36](./36-user-namespace.md) 與 [Ch 47](./47-container-vm-escape.md) 細談。

## 動手練習

> 以下在**你自己擁有的 Linux VM**（Ch 0 的 QEMU 環境）內進行，以下描述**未實測**，為理論預期行為。

1. 寫一個最小的 C 程式，開一個 TCP socket，`send()` 不同長度的資料，用 `bpftrace`/`ftrace` 追 `__alloc_skb()` 的呼叫，觀察配置的 size 和你送的資料長度的關係（headroom 的額外開銷）。
2. 用 `pahole -C sk_buff vmlinux`（需要你的 kernel 有 debug info）印出你的 kernel 上 `sk_buff` 的實際大小與欄位 offset，和本章圖對照。
3. 開兩個 unix domain socket，互相用 `SCM_RIGHTS` 傳對方的 fd 形成一個環，然後都關閉，搭配 `ftrace` 觀察 `unix_gc()` 什麼時候被觸發、回收了什麼。
4. 查 CVE-2016-8655 的兩個候選修補 commit（`f6fb8f10` vs `84ac7260`），用 `git log --grep` 在你本地的 kernel source clone 裡找出哪個是真正合入主線的 hash，修正本章可能的描述誤差。
5. 讀 `net/unix/garbage.c` 的 `unix_gc()` 實作，畫出它怎麼判定一個 fd 環是「孤立」的（提示：SCC / 圖的可達性分析思路）。
6. 寫一對 AF_UNIX socket，一邊用 `sendmsg()` 送大量訊息把對方的接收佇列塞滿，另一邊故意不 `recv()`，觀察 `SO_SNDBUF`/`SO_RCVBUF` 的限制什麼時候讓 `send()` 開始阻塞或回傳 `EAGAIN`——這能幫你直覺理解「skb 排在接收佇列裡」這件事在資源上的真實代價。

## 本章重點整理

- `sk_buff` 的核心是 `head`/`data`/`tail`/`end` 四個移動指標 + 尾端的 `skb_shared_info`；任何欄位驅動的指標運算算錯都可能是 OOB。
- `skb_clone()` 共享資料區（引用計數），`skb_copy()` 整塊複製；共享帶來效能但也帶來生命週期管理的複雜度。
- AF_PACKET 的 pgv/ring 把 kernel page 直接 mmap 給 user，是 page-level spray 的經典入口（詳見 [Ch 25](./25-usma-page-spray.md)）；CVE-2016-8655 是「兩個 setsockopt 路徑缺鎖保護」的 race UAF 典型案例。
- AF_UNIX 的 fd 傳遞需要垃圾回收（`unix_gc`）處理環狀引用；CVE-2023-4622 展示 GC 路徑和 skb 存取路徑缺乏共同鎖保護導致 UAF。
- skb 的「好用」與「危險」是同一組設計特質的兩面：通用、輕量、可快速 clone。

## 自我檢核

- [ ] 不看筆記，畫出 `head`/`data`/`tail`/`end` 的相對關係，並解釋 push header 和 append 資料各自怎麼移動哪個指標。
- [ ] 能解釋為什麼 `skb_clone()` 之後兩個 skb 的資料區生命週期是綁在一起的。
- [ ] 能說出 CVE-2016-8655 裡,「兩個 setsockopt 路徑」分別做了什麼、race window 在哪裡、修補怎麼關掉這個窗口。
- [ ] 能說出 AF_UNIX 為什麼需要垃圾回收，以及 CVE-2023-4622 的競態發生在哪兩條路徑之間。
- [ ] 給你一個新的「crafted packet」類 CVE 描述，能判斷它比較可能是 skb 佈局層的 bug，還是某個協議/子系統的邏輯 bug。
- [ ] 能解釋為什麼用 UDP socket spray skb 時，不 close fd 不代表 skb 一定還留著，決定留存時間的真正因素是什麼。
- [ ] 面試官問「kernel 裡為什麼需要一個專門的垃圾回收機制來處理 unix socket 的 fd 傳遞」，你能在一分鐘內講清楚環狀引用的問題本質。

## 延伸閱讀

### 官方文件 / 原始碼

- **[`include/linux/skbuff.h`（Bootlin Elixir, v6.6）](https://elixir.bootlin.com/linux/v6.6/source/include/linux/skbuff.h)**
  - **讀哪裡**：`struct sk_buff` 的欄位註解、`skb_end_pointer()`、`skb_shared_info` 的巨集。
  - **學什麼**：本章圖的精確欄位對應；版本間這裡常有小變動，務必對你的版本核對。
  - **前提**：讀 C struct 沒問題，知道 bitfield 的概念。
- **[`net/unix/garbage.c`（Bootlin Elixir, v6.6）](https://elixir.bootlin.com/linux/v6.6/source/net/unix/garbage.c)**
  - **讀哪裡**：`unix_gc()` 整個函式。
  - **學什麼**：為什麼需要掃描圖找孤立環，這是本章「fd cycle」說法的原始實作。

### 漏洞資料

- **[oss-security — Kernel Race Condition in packet_set_ring (CVE-2016-8655)](https://seclists.org/oss-sec/2016/q4/607)**
  - **讀哪裡**：全文，重點看它怎麼描述 version 切換和 ring 建立之間的窗口，以及修補用了哪個鎖。
  - **和本章的關聯**：本章 AF_PACKET 案例的一手來源。
- **[Red Hat Bugzilla 2237760 — CVE-2023-4622](https://bugzilla.redhat.com/show_bug.cgi?id=2237760)**
  - **讀哪裡**：描述句與引用的修補 commit `790c2f9d15b5`。
  - **和本章的關聯**：AF_UNIX GC race 案例的一手來源；同時可以順藤查到 CVE-2023-53469、CVE-2023-6531 這兩個相關問題。

### 部落格 / 其他

- **[Philip Pettersson — 個人部落格與會議投影片](https://github.com/PP-dev)**（搜尋 "af_packet chocobo" 可找到對應的 exploit 分析）
  - **這篇說什麼**：CVE-2016-8655 從 bug 到完整利用鏈的過程記錄。
  - **為什麼值得讀**：這是本章 AF_PACKET 案例背後的原始研究者視角,能看到 race window 怎麼被實際拉長到可利用的程度。

### 書籍

- **《Understanding Linux Network Internals》** — Christian Benvenuti（O'Reilly, 2006）
  - **這本書的定位**：年代較久（涵蓋到 2.6 kernel），但 skb 生命週期、網路堆疊分層的 mental model 講得完整，適合當本章的背景讀物。
  - **讀哪幾章**：涵蓋 `sk_buff` 操作的章節（書中以 Chapter 2 附近為主，不同版次編號略有差異）；協議堆疊細節章可選讀。
  - **前提知識**：基本 C、TCP/IP 分層概念。

AF_PACKET 和 AF_UNIX 展示了「網路輸入來自 syscall 參數」這一類攻擊面：bug 藏在指標運算與生命週期管理裡。下一章要換一種完全不同的輸入來源——**整塊 disk image**，看 kernel 的檔案系統 parser 怎麼在 ring 0 權限下解析不受信任的 on-disk metadata。

→ [Ch 34 檔案系統攻擊面：ext4 / f2fs / ntfs3 image parsing、fuzzing 礦區](./34-filesystem-attack-surface.md)
