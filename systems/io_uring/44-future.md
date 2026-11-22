# Ch 44 — 未來：zero-copy rx、ublk、Rust for Linux

> **目標**：看 io_uring 的前沿往哪長。你會搞懂 zero-copy receive（zcrx）怎麼把收包路徑的拷貝也消掉、ublk 怎麼用 io_uring 在 user space 實作一整個 block device、`IORING_OP_URING_CMD` 怎麼變成「任意裝置命令的非同步通道」（NVMe passthrough 是第一個大客戶）、以及「io_uring 作為通用 async syscall 介面」這個最大的願景。全程誠實標注哪些已穩定、哪些還在演進。

> **環境**：本章談前沿機制，許多需要 6.15+ 的 kernel、特定硬體或仍在變動；**不含 WSL 實測**，功能狀態以查證結果標注（查證時間 2026-11），不確定處明確降級措辭。

## 為什麼要讀「未來」

一門厚書的最後一章，不該是許願清單。讀前沿的真正目的是**看出演進的方向性**——io_uring 這幾年每個新機制看似零散（zcrx、ublk、uring_cmd、各種 register），但它們指向同一件事：**把「completion 模型」從 I/O 擴張到整個 kernel 介面**。看懂這個方向，你就能預判它下一步往哪走，也能判斷哪些前沿值得你現在就投入、哪些該再等等。

這一章也是對全課的收束：前 43 章學的 ring 機制、kernel 內部、安全面，會在這些前沿裡全部重新出現——zcrx 是 provided buffer 的延伸、ublk 是 uring_cmd + 完整生命週期、每個新機制都帶來新的攻擊面。

## 先建立直覺：一條「消除拷貝、消除 syscall、消除 kernel 中介」的主線

把前沿機制排成一條線，方向就出來了：

```
 普通 read  → registered buffer → zero-copy send → zero-copy receive(zcrx)   ：一路消除「拷貝」
 每 IO 一 syscall → batching → SQPOLL → multishot                            ：一路消除「syscall」
 固定 opcode → provided buffer → URING_CMD(任意命令) → ublk(整個裝置)        ：一路把 kernel 中介讓給 user
```

io_uring 的演進就是沿這三條線往前推。zcrx 推第一條到終點（連收包拷貝都沒了），ublk/uring_cmd 推第三條到終點（user space 直接定義裝置行為）。記住這三條線，下面每個機制你都知道它在補哪一塊。

## zero-copy receive（zcrx）：連收包的拷貝都消掉

我們在 Part 2 學過 zero-copy **send**（`IORING_OP_SEND_ZC`）：送出時不拷貝 user buffer。但**收**一直是難題——傳統收包，NIC DMA 進 kernel 的 skb，再拷貝一份到 user buffer。這個拷貝在高頻寬下很貴。

**zcrx（io_uring zero-copy Rx）在 6.15 進入 kernel**（kernel.org 有 v6.15 的 `iou-zcrx` 文件）。它的做法：

- **header/data split**：封包標頭留在 kernel 記憶體，**payload 直接落進 user space 預先提供的記憶體區**，省掉 kernel→user 的拷貝。
- **前提很硬**：kernel 的 API **不**負責設定 NIC——使用者得自己設好 **flow steering**（把目標流量導到對的佇列），還需要支援的 NIC、對的驅動、對的佇列設定。

**狀態：已進 mainline（6.15）但前沿、硬體與設定門檻高、仍在演進。** 這是「消除拷貝」那條線的當前終點：send 和 receive 兩端的拷貝都能消了。它和 registered buffer、provided buffer ring 是同一個家族——都在解「buffer 的所有權怎麼在 user 和 kernel 之間高效流轉」。**本章未在 WSL 實測 zcrx**（WSL 虛擬 NIC 無法示範）；要實作請對照 kernel 的 `iou-zcrx` 文件與支援的實體 NIC。

## 網路：io_uring 不斷往外長的那條戰線

zcrx 只是 io_uring 網路擴張的最前沿一步，背後是一條持續數年、仍在推進的戰線。把這條線的里程碑排出來，方向就清楚了：

- **基本網路 op**（accept/connect/send/recv，Part 2 的 [Ch 12](./12-network-io.md)）：讓網路 I/O 也走 completion 模型。
- **multishot**（[Ch 13](./13-multishot.md)）：一次提交、多次完成——一個 `accept`/`recv` 持續回報新連線/新資料，省掉「每次重新 arm」的開銷。這是 io_uring 在網路側相對 epoll 的關鍵武器。
- **provided buffer ring**（[Ch 14](./14-provided-buffers.md)）：收資料時不綁定 buffer，kernel 從你提供的 ring 挑一塊——解決 multishot recv「資料來了卻沒 buffer」的難題。這也是 CVE-2024-0582 的所在。
- **zero-copy send**（`SEND_ZC`）→ **zero-copy receive（zcrx，6.15）**：兩端的拷貝都消掉。
- **send/recv bundle**：把多個 buffer 的收發併成一次操作，進一步攤平 per-op 成本。

這條線的意義，是 io_uring **從 storage 起家、一路把網路也納入同一個 completion 介面**。對照 [Ch 43](./43-comparison.md) 的版圖：io_uring 在 storage 上已吃掉 libaio，在網路上正從 epoll 手裡一塊塊搶——multishot + provided buffer + zcrx 這套組合，正是它在網路側的進攻主力。

**誠實標注**：網路這條線**仍在演進**，每個 kernel release 幾乎都加東西（新的 bundle、新的 zc 變體、busy poll 整合）。本課釘在 6.18，但你讀到時很可能又多了機制——查 LWN 的 io_uring 系列與 liburing wiki 的 networking 整理，是跟上這條線最可靠的方式。

## ublk：用 io_uring 在 user space 實作一整個 block device

ublk 是「把 kernel 中介讓給 user」那條線最漂亮的成果。它讓你**在 user space 寫一個程式，對外呈現成一個真的 `/dev/ublkbN` 區塊裝置**——核心的讀寫請求，透過 io_uring 送到你的 user-space 程式去處理。

**ublk 自 6.0 進 mainline**（kernel.org 有 v6.0 的 ublk 文件）。架構：

- 它是一個 **blk-mq 的 request-based 驅動**，對外暴露 `/dev/ublkbN`；另有一個控制節點 `/dev/ublk-control` 管理裝置生命週期。
- **I/O 命令透過 io_uring passthrough（`URING_CMD`）在 kernel 與 user daemon 之間傳遞**——這是關鍵：ublk 不是又發明一套介面，它**就是**建在 io_uring 的 uring_cmd 上。kernel 收到對 `/dev/ublkbN` 的讀寫，打包成 uring_cmd 丟給你的 daemon，daemon 處理完回報完成。
- 演進：6.6 加了 `UBLK_F_UNPRIVILEGED_DEV`（非特權使用者也能建裝置）；6.18 的文件描述了新的 user-recovery 功能（daemon 掛了能復原）。

ublk 的意義：**把「寫一個 block device driver」從 kernel 模組（難寫、崩了拖垮整機、攻擊面大）搬到 user space**。網路儲存（把遠端 storage 呈現成本地 block device）、自訂加密/壓縮層、測試用的故障注入裝置——這些以前要寫 kernel 驅動，現在用 ublk + io_uring 在 user space 就能做，崩了只是那個 daemon 掛掉。這是 io_uring「通用 async 介面」價值的最佳展示之一。

具體看一次 ublk 的 I/O 怎麼流動，你就懂它為什麼「就是建在 io_uring 上」：

```
  應用程式 read(/dev/ublkbN)                     你的 user-space daemon
        │                                              ▲   │
        ▼                                              │   ▼ (真正去做：讀遠端/解密/…)
   blk-mq 收到 request ──打包成 uring_cmd──▶ io_uring CQE ──┘   處理完
        ▲                                                        │
        └────────────── uring_cmd 回報完成（再提交一個 uring_cmd）◀┘
```

- 應用對 `/dev/ublkbN` 的每個讀寫，被 blk-mq 包成一個 `uring_cmd`，透過 io_uring 送到你的 daemon。
- daemon 在 user space 真正處理（可以是去讀遠端 storage、解密、注入故障……隨你），處理完再用一個 `uring_cmd` 回報完成。
- 整條路**沒有新介面**——就是 io_uring 的 uring_cmd 一來一回。這就是為什麼 ublk 能又薄又快：它復用了 io_uring 已經高度優化的提交/完成機制，而不是自己發明一套。

## URING_CMD 與 NVMe passthrough：任意裝置命令的非同步通道

ublk 底下那個 `URING_CMD`，本身就是 io_uring 最具擴張性的機制。`IORING_OP_URING_CMD`（opcode 46，Ch 0 的 probe 有看到）是一個**通用的「把任意裝置/子系統命令非同步化」的通道**。

第一個大客戶是 **NVMe passthrough**（2022 年 Kanchan Joshi 的 patch 系列）：

- 透過通用字元裝置 `/dev/ngXnY`，把 **NVMe 命令直接**（繞過 block 層）以 io_uring 非同步送下去。opcode 是 `NVME_URING_CMD_IO`。
- 命令**存在 SQE 裡**：一般 SQE 64 位元組不夠放 NVMe 命令，所以要用 **`IORING_SETUP_SQE128`**（128 位元組 SQE，多出的 80 位元組放命令）。
- NVMe passthrough 要回兩個結果，所以 ring 要用 **`IORING_SETUP_CQE32`**（32 位元組 CQE）。
- 6.18 的 NVMe 驅動（`drivers/nvme/host/ioctl.c`）會檢查這兩個 flag 都在才接受命令。

機制上更深的意義：io_uring 加了 `file_operations->uring_cmd()` 這個 hook，**任何子系統都能透過它把自己的命令（本來是 `ioctl`/`fsctl` 這種同步介面）暴露成 io_uring 可非同步提交的命令**。NVMe 是第一個，但 uring_cmd 是通用的——這正是「io_uring 作為通用 async syscall 介面」願景的技術基礎。

> 把 ublk 和 NVMe passthrough 並排看就懂了：**uring_cmd 讓 io_uring 從「一組固定的 I/O op」變成「一個可擴充的非同步命令匯流排」**。新的子系統不用等 io_uring 幫它加專屬 opcode，自己掛上 `uring_cmd()` hook 就進來了。

## Rust for Linux 與 io_uring：謹慎樂觀的交會

**Rust for Linux** 是讓 kernel 能用 Rust 寫驅動/模組的長期工程（實驗性支援自 6.1 起進 mainline，之後陸續有 Rust 驅動落地）。它和 io_uring 的交會點，**目前主要是前瞻性的，請當成方向而非既成事實（降級措辭）**：

- **user-space 側已經發生**：ublk 的 user daemon、各種 io_uring target，愈來愈多用 Rust 寫（透過 `io-uring` crate）。這是最實在的交會——用 Rust 的記憶體安全來寫這些「以前要用 C 寫 kernel 驅動」的 user-space 元件。
- **kernel 側仍在早期**：io_uring 的 kernel 實作是 C（本課 Part 3 讀的全是 C）。「用 Rust 改寫 io_uring 或其周邊」目前沒有成形的上游計畫；考慮到 io_uring 是 kernel 漏洞大戶（Part 4），用 Rust 的記憶體安全來降低這類 UAF/race 的誘因，是一個被談論的長期方向，**但到本章查證時尚無具體里程碑可引用**。

誠實結論：Rust for Linux 和 io_uring 的結合，**當下實在、已落地的是 user space（寫 ublk/io_uring 程式）；kernel 側是值得關注的長期可能，不是現狀**。別因為兩個都是熱門詞就腦補它們已經深度整合。

## register 介面：io_uring 可擴張性的另一半

uring_cmd 讓「新的裝置命令」能進來，而 `io_uring_register`（[Ch 3](./03-three-syscalls.md)）這條線，則讓 io_uring 能不斷「註冊新的資源種類」而不必動 ABI。回看這幾年加的東西，幾乎都是以 `IORING_REGISTER_*` 的形式長出來的：

- registered files / buffers（省 per-I/O 前置，[Ch 15](./15-registered-files-buffers.md)）
- provided buffer ring（`IORING_REGISTER_PBUF_RING`，[Ch 14](./14-provided-buffers.md)）
- registered ring fd、NAPI busy poll（`IORING_REGISTER_NAPI`，[Ch 41](./41-performance-engineering.md)）
- zcrx 的區域註冊

這是 io_uring 向前相容的核心設計：**新能力透過 register 一個新「opcode 類別」加入，舊程式完全不受影響。** 它和 uring_cmd 是同一個哲學的兩面——一個擴充「要提交什麼命令」，一個擴充「要註冊什麼資源」。理解這兩條擴充軸，你就能預判 io_uring 下一個新機制大概會以什麼形式出現：不是改 syscall，而是加一個 op 或加一個 register 類別。

**代價也要誠實講**：每多一個 register 類別，就多一塊「註冊的資源怎麼在請求生命週期裡被引用、何時釋放」的狀態——這正是 Part 4 一堆 UAF 的來源（provided buffer ring 的 CVE-2024-0582 就是註冊資源的生命週期出錯）。擴充性和攻擊面，又一次是同一枚硬幣。

## io_uring 作為「通用 async syscall 介面」的願景

把前面所有前沿收束成一句話：io_uring 想從「一個快的 I/O 介面」長成「**Linux 的通用非同步系統呼叫介面**」。

- 一開始是檔案讀寫（取代 libaio）。
- 然後是網路（accept/send/recv/zc）、metadata（openat/statx）、同步原語（futex）、訊息（msg_ring）。
- 然後是 uring_cmd——**任意子系統的命令**都能掛進來。
- 於是 ublk 把「整個裝置」搬進來、NVMe passthrough 把「裸硬體命令」搬進來。

方向性很清楚：**愈來愈多本來要靠同步 syscall / ioctl 做的事，被改造成「提交一個請求、非同步完成」的 io_uring op。** 如果這條路走到底，io_uring 會變成 Linux 上做幾乎任何系統操作的非同步入口——一個 ring，提交各種 syscall，批次、零拷貝、可達零 syscall 地完成。

**但要誠實標注這是願景，不是已完成的現實**：很多 syscall 還沒、也未必會有 io_uring 版本；每加一個 op 就多一分攻擊面（Part 4），這讓擴張有安全上的天花板；而且「被加固環境停用」的現實（[Ch 39](./39-mitigations-detection.md)）意味著它永遠不能假設自己一定可用。io_uring 會繼續擴張，但「取代所有 syscall」是方向，不是終點預言。

## 一條演進時間軸：io_uring 這幾年加了什麼

把前沿放進時間軸，「它往哪長」會更立體（版本為該機制進 mainline 的大致節點，細節以 LWN 與 kernel 文件為準）：

- **5.1**：io_uring 誕生（基本讀寫）
- **5.4**：single mmap；**5.5–5.6**：更多 op、SQPOLL 改進
- **5.7**：provided buffers（舊式）；**5.11**：SQPOLL 不再強制 root
- **5.19**：provided buffer **ring**、`COOP_TASKRUN`
- **6.0**：zero-copy send、`SINGLE_ISSUER`、**ublk**、NVMe passthrough（uring_cmd）成形
- **6.1**：`DEFER_TASKRUN`（LTS）；**6.7**：futex op
- **6.9**：NAPI busy poll 註冊
- **6.15**：**zero-copy receive（zcrx）**

看這條軸，方向一目了然：**早期補齊「基本 async I/O」（修 libaio），中期猛攻「網路」與「省開銷的提交/完成模型」，近期往「zero-copy 兩端」與「把整個裝置/任意命令搬進來」（ublk、uring_cmd）延伸。** 每個 kernel release 幾乎都往這三條線（消拷貝、消 syscall、讓出 kernel 中介）加東西。你讀到本章時版本很可能又前進了——但只要抓住這三條線，新機制會落在哪條線上、解什麼問題，你大概都猜得到。

## 對比與取捨表：前沿機制的成熟度與門檻

| 機制 | 進 mainline | 補哪條線 | 硬體/設定門檻 | 成熟度（2026-11） |
|---|---|---|---|---|
| zero-copy send | 6.0 | 消拷貝（送） | 支援的 NIC | 穩定 |
| **zcrx（zero-copy rx）** | **6.15** | 消拷貝（收） | NIC + flow steering（高） | 前沿、演進中 |
| **ublk** | **6.0** | 讓出 kernel 中介 | 低（純軟體） | 漸成熟、功能持續加 |
| **NVMe passthrough（uring_cmd）** | 2022（6.x） | 任意裝置命令 | NVMe + SQE128/CQE32 | 可用、特定場景 |
| uring_cmd（通用 hook） | 持續擴充 | 通用非同步命令匯流排 | 各子系統自行支援 | 擴張中 |
| Rust for Linux × io_uring | user 側已落地 | 安全性 | — | **kernel 側仍早期（降級）** |

## 踩雷集錦

1. **以為 zcrx「開了就零拷貝」。** 它要支援的 NIC + 使用者自己設好 flow steering，kernel API 不幫你配 NIC。沒有這些前提，zcrx 根本起不來。這是「前沿機制門檻高」的典型。
2. **把 ublk 當成「慢的 loopback」。** ublk 的 I/O 走 io_uring uring_cmd，不是又慢又重的舊式 NBD；它的設計目標就是高效能的 user-space block device。但它畢竟多一次 kernel↔user 往返，延遲敏感到極致時仍不如 kernel 內驅動或 SPDK。
3. **以為 uring_cmd 是只給 NVMe 的。** 它是**通用**的 `file_operations->uring_cmd()` hook，NVMe 只是第一個客戶。看到 uring_cmd 要想到「任意子系統的非同步命令通道」，不是「NVMe 專用」。
4. **腦補 Rust 已經重寫了 io_uring。** 到本章查證時，io_uring 的 kernel 實作仍是 C；Rust 的實在交會在 user space（寫 io_uring 程式）。別把「兩個熱門詞」當成「已深度整合」。
5. **把願景當現狀。** 「io_uring 是通用 async syscall 介面」是**方向**。很多 syscall 沒有 io_uring 版本、安全天花板限制擴張、加固環境會停用它。報告現狀時要把「已穩定 / 演進中 / 願景」分清楚——這正是本章反覆示範的誠實標注。

## 進階：每個新機制都帶來新攻擊面

用 Part 4 的眼睛回看這些前沿，會發現一個不安的規律：**io_uring 每擴張一步，攻擊面就長一塊**。provided buffer ring 帶來了 [Ch 36](./36-cve-case-three.md) 那類 OOB/UAF（CVE-2024-0582 就是 PBUF_RING 的 UAF）；zcrx 把 user 記憶體更深地接進收包路徑，生命週期更複雜；ublk 把 kernel 請求送到不可信的 user daemon，是一條新的信任邊界；uring_cmd 讓更多子系統的命令進到 io_uring 的非同步生命週期裡，每一個都是潛在的 race/UAF 溫床。

這不是說這些機制不該做，而是說：**io_uring 的「通用化」和它的「漏洞大戶」身份是同一枚硬幣的兩面。** 愈通用、生命週期愈複雜、非同步路徑愈多，就愈難證明正確。這也是為什麼本課把安全面（Part 4）放得和機制、效能一樣重——理解 io_uring 的未來，必然包含理解它不斷長出的攻擊面。而這，正好把我們帶到 Final Project：親手重現一個 io_uring CVE，把前 44 章的機制、kernel 內部、安全直覺，在一個真實漏洞上全部兌現。

## 本章重點整理

- io_uring 的前沿沿三條線推進：**消拷貝**（zcrx）、**消 syscall**（已成熟）、**讓出 kernel 中介**（ublk/uring_cmd）。
- **zcrx**（6.15）消掉收包拷貝，但要支援的 NIC + 自設 flow steering，門檻高、演進中。
- **ublk**（6.0）用 io_uring uring_cmd 在 user space 實作整個 block device，把「寫 block 驅動」從 kernel 搬到 user space。
- **uring_cmd** 是通用的非同步命令匯流排（`file_operations->uring_cmd()` hook），**NVMe passthrough**（SQE128 + CQE32、`/dev/ngXnY`）是第一個大客戶；它是「通用 async syscall 介面」願景的技術基礎。
- **Rust for Linux × io_uring**：user 側（寫 io_uring 程式）已落地，kernel 側仍早期（降級措辭）。
- 「io_uring = 通用 async syscall 介面」是**方向不是現狀**；每次擴張都增加攻擊面，這是它「通用」與「漏洞大戶」的一體兩面。

## 自我檢核

- [ ] 我能說出 zcrx 相對 zero-copy send 補的是哪一塊，以及它的硬前提
- [ ] 我能解釋 ublk 怎麼用 io_uring 把 block device 實作搬到 user space
- [ ] 我能說出 NVMe passthrough 為什麼需要 SQE128 和 CQE32
- [ ] 我能講清楚 uring_cmd 為什麼是「通用」通道而非 NVMe 專用
- [ ] 我能分辨 io_uring 前沿裡哪些已穩定、哪些演進中、哪些只是願景

## 延伸閱讀

### kernel 官方文件

- **[io_uring zero copy Rx（iou-zcrx）](https://www.kernel.org/doc/html/v6.18/networking/iou-zcrx.html)** — kernel.org
  - **讀哪裡**：header/data split 與 flow steering 需求那段
  - **為什麼值得讀**：zcrx 門檻到底有多高，看官方文件最清楚；別信「開了就零拷貝」的二手說法

- **[Userspace block device driver（ublk）](https://kernel.org/doc/html/v6.18/block/ublk.html)** — kernel.org
  - **讀哪裡**：架構概覽與 `URING_CMD` 如何在 kernel/daemon 間傳 I/O
  - **前提**：讀過本課 uring_cmd 段再看，會看懂 ublk「就是建在 io_uring 上」

### 技術演進報導

- **[LWN 的 io_uring 系列](https://lwn.net/Articles/810414/)** — Jonathan Corbet 等
  - **這篇說什麼**：每個新機制（zcrx、ublk、uring_cmd、各種 register）進 mainline 時的第一時間深度報導
  - **讀哪裡**：想查某前沿功能「何時加入、為什麼、現在到哪了」，LWN 最準且中立

### 一手 patch / 設計

- **[io_uring passthrough for NVMe patch 系列](https://lore.kernel.org/io-uring/20220308152105.309618-1-joshi.k@samsung.com/)** — Kanchan Joshi 等
  - **讀哪裡**：SQE128/CQE32 的設計理由、`file_operations` 的 uring_cmd hook
  - **注意**：這是「uring_cmd 如何從 NVMe 專用泛化成通用命令通道」的一手材料；讀它你會看到願景是怎麼從一個具體需求長出來的

44 章走到這裡，io_uring 的機制、kernel 內部、安全、效能、生態、對比、未來都齊了。剩下的不是再學新東西，而是**把學到的全部用在一件真實的事情上**。下一站是本課的壓軸——選一個真實的 io_uring CVE，在你自己的受控環境裡從 root cause 分析到觸發、到用 KASAN 觀察、到讀懂 patch，走完一個防禦研究者的完整流程。

→ [Final Project：io_uring CVE 重現 + 分析](./final-project-cve-reproduction.md)
