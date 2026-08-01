# Ch 25 — virtio 架構：virtqueue、vring、descriptor chain

> **目標**：讀完這章，你能在 QEMU 原始碼裡追一次完整的 virtio I/O 資料流——從 guest driver 填 desc 到 host device 取元素再回 used ring——並說清楚每個環節哪裡可能出問題。

> **環境**：QEMU 9.0 / x86-64 / Linux host

---

## 為什麼需要這個？

傳統完整硬體模擬的效能問題值得先量化一下再繼續。一次 guest 對 e1000 網卡的 I/O 寫入，大致走這條路：

```
guest userspace write → guest kernel driver → 
PIO/MMIO 寫入 → VM-EXIT（VT-x 攔截）→ KVM 丟回 userspace → 
QEMU main loop 喚醒 → device .write callback → 處理 → 回 guest
```

每次 VM-EXIT 的成本在 5,000–50,000 ns 之間（視 TLB 狀態、context switch、VMCS 操作而異）。高吞吐的網路或磁碟場景每秒可以有數百萬次 I/O，這個成本直接吃掉 VM 的 I/O 效能。

virtio（Virtual I/O）的核心思路是**讓 guest driver 知道自己在 VM 裡（paravirtualization，半虛擬化）**，然後用一塊共享記憶體做 ring buffer 來傳資料——guest 把要做的事情批次放進 ring，device 端從 ring 取出來做，大幅減少 trap 次數。kick（通知 device 有工作）可以用 eventfd，不一定需要 VM-EXIT。

歷史背景：IBM 研究員 Rusty Russell 在 2005 年前後開始推動這個概念，2008 年他把 virtio driver 合進了 Linux 主線（Linux 2.6.24），同年在 ACM SIGOPS 發表論文《VirtIO: Towards a De-Facto Standard For Virtual I/O Devices》。現在 virtio 規格由 OASIS 維護，目前最新版本是 virtio 1.2（virtio-v1.2-csd01），定義了 split ring、packed ring、以及各種 device class（net、blk、scsi、console、gpu…）。

站在攻擊者的角度：virtio 比 e1000 更現代，但也代表**更大的攻擊面**。共享記憶體的 ring、DMA 翻譯、多個 descriptor 串成的 chain——每個環節都是 guest 可控的資料進入 host 端程式碼的路徑。Ch 26–28 會系統性地追這些路徑上的 bug。

---

## 先建立直覺

先不看細節，用圖把共享記憶體的佈局印在腦子裡：

```
guest 實體記憶體（GPA 空間）

  ┌──────────────────────────────────────────────────────────┐
  │                    vring（共享 ring）                      │
  │                                                          │
  │  ① Descriptor Table（desc table）                        │
  │  ┌────┬────┬────┬────┐                                  │
  │  │ D0 │ D1 │ D2 │ D3 │  ... （VRING_SIZE 個 entry）      │
  │  └────┴────┴────┴────┘                                  │
  │   每個 entry：{ addr(GPA), len, flags, next }            │
  │                                                          │
  │  ② Avail Ring（guest → device 的通知）                    │
  │  ┌───────────┬──────────────────────┐                   │
  │  │  flags    │  idx（寫到哪了）      │                   │
  │  ├───────────┴──────────────────────┤                   │
  │  │  ring[0], ring[1], ... （desc head idx）              │
  │  └──────────────────────────────────┘                   │
  │                                                          │
  │  ③ Used Ring（device → guest 的回條）                     │
  │  ┌───────────┬──────────────────────┐                   │
  │  │  flags    │  idx（處理到哪了）    │                   │
  │  ├───────────┴──────────────────────┤                   │
  │  │  ring[0], ring[1], ... （{id, len} pair）             │
  │  └──────────────────────────────────┘                   │
  └──────────────────────────────────────────────────────────┘

  guest driver（kernel module）        device（QEMU hw/ 裡的 C 程式）
       │                                          │
       │──── 填 desc table ──────────────────────▶│
       │──── 更新 avail ring.idx ────────────────▶│
       │──── kick（eventfd 或 PIO write）─────────▶│ virtqueue_pop()
       │                                          │（取出 elem，做 I/O）
       │◀─── 更新 used ring.idx ─────────────────│ virtqueue_push()
       │◀─── guest interrupt（MSI-X）────────────│
```

用三個比喻幫你記：

- **Descriptor Table = I/O 申請單存檔**：每張申請單（desc entry）記錄「在記憶體哪裡、多長、方向（讀/寫）」，可以用 `next` 指標串成一份多頁申請單（descriptor chain）。
- **Avail Ring = 收件匣**：guest 把「這份申請單的 head index」丟進收件匣，device 依序取出來做。
- **Used Ring = 完成回條**：device 做完後把「處理了哪份申請單、實際完成了多少 byte」放回條，guest 收到 interrupt 就來查。

---

## 核心概念

### VirtQueue：QEMU 端的控制結構

QEMU 的 `VirtQueue` struct（`hw/virtio/virtio.c`）是 host 端管理一條 virtqueue 的核心物件。每個 virtio device 可以有多條 queue（virtio-net 典型有三條：receiveq、transmitq、controlq）。

`VirtQueue` 裡你最需要認識的欄位（QEMU 9.0，`include/hw/virtio/virtio.h`）：

```c
struct VirtQueue {
    VRing vring;              /* 實際的三環（desc/avail/used）的 GPA 與大小 */
    VirtQueueElement *used_elems;
    uint16_t last_avail_idx;  /* device 端的讀指標：下次從 avail ring 哪個位置取 */
    uint16_t last_used_idx;   /* used ring 上次更新到哪 */
    uint16_t signalled_used;
    uint16_t signalled_used_valid;
    bool notification;
    uint16_t queue_index;     /* 這是第幾條 queue */
    unsigned int inuse;       /* 目前有多少個 elem 被 device 持有中（已 pop 未 push） */
    /* ... 其他欄位省略 */
    VirtIODevice *vdev;
    EventNotifier guest_notifier;   /* guest → device 的 eventfd（kick 用） */
    EventNotifier host_notifier;    /* device → guest 的 eventfd（interrupt 用） */
};
```

`VRing` 存放三個環的 **GPA（Guest Physical Address）**，QEMU 在需要時會透過 `address_space_map` 把 GPA 翻成 host 端可存取的 HVA（Host Virtual Address）。

### vring 三組成

**① Descriptor Table**

每個 entry 16 bytes：

```c
/* include/standard-headers/linux/virtio_ring.h */
struct vring_desc {
    __virtio64 addr;   /* 8 bytes：buffer 的 GPA */
    __virtio32 len;    /* 4 bytes：buffer 長度（byte） */
    __virtio16 flags;  /* 2 bytes：VRING_DESC_F_NEXT | VRING_DESC_F_WRITE | ... */
    __virtio16 next;   /* 2 bytes：chain 的下一個 desc index */
};
```

關鍵 flags：
- `VRING_DESC_F_NEXT = 1`：這個 desc 後面還有下一個（next 欄位有效）
- `VRING_DESC_F_WRITE = 2`：這塊 buffer 是 device 可寫的（對 guest 來說是 read buffer，device 把資料填進來）
- `VRING_DESC_F_INDIRECT = 4`：這個 desc 的 addr 不是資料 buffer，而是另一張 desc table 的 GPA（indirect descriptor）

沒有 `VRING_DESC_F_WRITE` 的 desc 是 device 只讀的（guest 填資料，device 讀）。

**② Avail Ring**

```c
struct vring_avail {
    __virtio16 flags;      /* 0x1 = VRING_AVAIL_F_NO_INTERRUPT（不要打斷我） */
    __virtio16 idx;        /* guest 寫進來的下一個空位（單調遞增，uint16_t wrap） */
    __virtio16 ring[];     /* VRING_SIZE 個 entry，每個是 desc chain 的 head index */
    /* 後面還有 used_event（如果 feature VIRTIO_RING_F_EVENT_IDX 開了） */
};
```

guest 每次想送一個新的 I/O 請求，就：
1. 取得一個（或多個）desc entry，填好 addr/len/flags/next
2. 把 head index 寫到 `avail.ring[avail.idx % VRING_SIZE]`
3. 做一個 store-store memory barrier（`smp_wmb()`）
4. 遞增 `avail.idx`
5. 觸發 kick

**③ Used Ring**

```c
struct vring_used_elem {
    __virtio32 id;     /* 完成的 desc chain 的 head index */
    __virtio32 len;    /* device 實際寫入的 byte 數（對 read 有意義） */
};

struct vring_used {
    __virtio16 flags;       /* 0x1 = VRING_USED_F_NO_NOTIFY（別 kick 我） */
    __virtio16 idx;         /* device 更新的下一個空位 */
    struct vring_used_elem ring[];
    /* 後面還有 avail_event */
};
```

### Descriptor Chain：一次 I/O 的骨架

單一 I/O 請求幾乎不會只有一個 desc——通常是一條 chain，最典型的 virtio-blk read 長這樣：

```
head desc (NEXT)          body desc (WRITE|NEXT)      status desc (WRITE)
┌──────────────────┐     ┌──────────────────────┐    ┌──────────────────┐
│ addr: req header │ ──▶ │ addr: data buffer    │ ──▶│ addr: status byte│
│ len:  16         │     │ len:  sector_size    │    │ len:  1          │
│ flags: NEXT      │     │ flags: WRITE|NEXT    │    │ flags: WRITE     │
│ next: idx_body   │     │ next: idx_status     │    │ next: (invalid)  │
└──────────────────┘     └──────────────────────┘    └──────────────────┘
   guest → device              device → guest            device → guest
（告訴 device 要讀哪個 sector）（device 填入讀到的資料） （成功/失敗 0/1/2）
```

`VRING_DESC_F_WRITE` 的語意是站在 **device** 視角：「device 可以寫這塊 buffer」。所以 data buffer 和 status desc 都帶 `WRITE`，header 不帶（device 只讀 header，不寫它）。

### guest 完整流程

```
guest driver
    │
    ▼
1. 分配 buffer（kmalloc 或 dma_alloc，取得 GPA）
    │
    ▼
2. 填 desc table 中對應的 entry（addr = GPA, len, flags, next）
    │
    ▼
3. 更新 avail ring：
      avail.ring[avail.idx % VRING_SIZE] = head_desc_idx
      smp_wmb()   ← 關鍵：先讓 desc 對 device 可見
      avail.idx++
    │
    ▼
4. kick（告訴 device 有新工作）
      選項 A：eventfd_signal（vhost-net 常用，無需 VM-EXIT）
      選項 B：PIO/MMIO write 到 Queue Notify 暫存器（傳統 virtio）
    │
    ▼（切換到 host side）

QEMU device（e.g. hw/block/virtio-blk.c）
    │
    ▼
5. virtqueue_pop(vq, sizeof(VirtQueueElement))
      讀 avail ring，取出 head_desc_idx
      追 desc chain，建立 VirtQueueElement（iov_in / iov_out 陣列）
      DMA 翻譯：GPA → HVA（address_space_map）
    │
    ▼
6. 實際 I/O 處理（讀 file、呼叫 AIO、…）
    │
    ▼
7. virtqueue_push(vq, elem, written_len)
      把 {head_desc_idx, written_len} 寫進 used ring
      smp_wmb()
      used.idx++
    │
    ▼
8. virtio_notify(vdev, vq)
      觸發 guest interrupt（MSI-X 或 legacy IRQ）
    │
    ▼（切換回 guest）

guest driver
    │
    ▼
9. interrupt handler 被喚醒
      讀 used.idx 確認有新完成項目
      取出 used_elem，釋放 desc，處理結果
```

### QEMU 端的關鍵函式

`virtqueue_pop()`（`hw/virtio/virtio.c`）是你在追 bug 時最常下斷點的函式。它做了這幾件事：

1. 讀 `vq->vring.avail`（透過 `vring_avail_idx(vq)`）取得 head index
2. 呼叫 `virtqueue_split_pop()` 或 `virtqueue_packed_pop()`（分別對應 split/packed ring）
3. 追 desc chain（`vring_desc_addr()`、`vring_desc_flags()`、`vring_desc_next()`）
4. 對每個 desc，呼叫 `address_space_map()` 把 GPA 翻成 HVA，填入 `VirtQueueElement` 的 `iov_in[]` 或 `iov_out[]`
5. 更新 `vq->last_avail_idx`

`VirtQueueElement`（`include/hw/virtio/virtio.h`）是 device 拿到的 I/O 描述：

```c
typedef struct VirtQueueElement {
    unsigned int index;     /* desc chain 的 head index */
    unsigned int len;
    unsigned int ndescs;    /* chain 裡有幾個 desc */
    unsigned int out_num;   /* iov_out 的數量（guest → device，device 只讀） */
    unsigned int in_num;    /* iov_in  的數量（device → guest，device 可寫） */
    hwaddr *in_addr;        /* 每個 in buffer 的 HVA */
    hwaddr *out_addr;       /* 每個 out buffer 的 HVA */
    struct iovec *in_sg;    /* iov_in 陣列（device 寫入用） */
    struct iovec *out_sg;   /* iov_out 陣列（device 讀取用） */
} VirtQueueElement;
```

device 操作 buffer 全靠 `in_sg` / `out_sg`，長度來自 `iov_in[i].iov_len` / `iov_out[i].iov_len`——這些值直接來自 guest 填的 `vring_desc.len`。長度可信嗎？不可信。這是 Ch 26 要講的事。

---

## 底層機制：三環詳解

### 完整 vring 記憶體佈局

```
GPA: vring_base
┌────────────────────────────────────────────────────────────────┐
│  Descriptor Table  （VRING_SIZE × 16 bytes）                    │
│  offset 0x000:  desc[0]  { addr:8, len:4, flags:2, next:2 }   │
│  offset 0x010:  desc[1]  { addr:8, len:4, flags:2, next:2 }   │
│  ...                                                           │
│  offset VRING_SIZE*16 - 0x10:  desc[VRING_SIZE-1]             │
├────────────────────────────────────────────────────────────────┤
│  Avail Ring  （4 + VRING_SIZE*2 bytes，加 padding 到 4096 對齊）│
│  +0x000:  flags  (uint16_t)                                    │
│  +0x002:  idx    (uint16_t)                                    │
│  +0x004:  ring[0..VRING_SIZE-1]  (uint16_t each)              │
│  +0x004+VRING_SIZE*2:  used_event  (uint16_t, 若開 feature)   │
├────────────────────────────────────────────────────────────────┤
│  Used Ring  （4 + VRING_SIZE*8 bytes）                          │
│  +0x000:  flags  (uint16_t)                                    │
│  +0x002:  idx    (uint16_t)                                    │
│  +0x004:  ring[0..VRING_SIZE-1]  ({ id:4, len:4 } each)       │
│  +0x004+VRING_SIZE*8:  avail_event  (uint16_t, 若開 feature)  │
└────────────────────────────────────────────────────────────────┘
```

VRING_SIZE 必須是 2 的冪次（常見值：128、256、1024）。

avail ring 和 used ring 之間有 padding 讓 used ring 起始對齊到 4096 byte（page）邊界，目的是讓兩個環可以各自 mmap 到不同的記憶體保護屬性（guest 寫 avail，host 寫 used）。

### split ring vs packed ring

| 特性 | Split Ring（virtio 1.0/1.1） | Packed Ring（virtio 1.1+） |
|------|------------------------------|----------------------------|
| 記憶體結構 | 三個獨立的陣列（desc/avail/used） | 單一個 desc ring 包含所有資訊 |
| 同步方式 | avail.idx / used.idx 分開更新 | desc 裡的 flags 帶 AVAIL/USED bit |
| Cache 效率 | avail 和 used 在不同 cache line，bounce 嚴重 | 讀寫集中在同一個 ring entry，cache 友善 |
| 順序保證 | 不保證有序（需要 VIRTIO_F_IN_ORDER） | 可選有序模式 |
| QEMU 支援 | `virtio.c` 中 `virtqueue_split_*` | `virtio.c` 中 `virtqueue_packed_*` |
| Spec | virtio 1.0+ | virtio 1.1+，Linux 5.0+ guest driver 支援 |
| 攻擊複雜度 | 較直觀，研究較多 | 相對較新，公開 bug 分析較少 |

這門課主線用 split ring（資料更豐富），packed ring 在「進階」一節補充。

---

## 對比與取捨

| | 傳統模擬（e1000） | virtio-net（split ring） | vhost-net（kernel offload） |
|---|---|---|---|
| **VMEXIT 頻率** | 每次 PIO/MMIO 都 exit | kick 時才 exit，可批次 | data path 在 kernel，幾乎不 exit |
| **資料複製** | 多次（MMIO buffer → kernel → 使用者） | 共享記憶體直取，少一次複製 | 零複製（kernel 直接操作 guest 記憶體） |
| **吞吐量（參考值）** | ~1–3 Gbps（軟體瓶頸） | ~5–10 Gbps | ~20–40 Gbps（接近硬體極限） |
| **實作複雜度** | 高（完整硬體狀態機） | 中（共享記憶體協議） | 高（分裂到 kernel + userspace） |
| **攻擊面範圍** | MMIO/PIO handler | vring + DMA + feature negotiation | 擴展到 kernel vhost module |
| **Guest driver 感知** | 不感知（以為自己有真實硬體） | 感知（virtio pci driver） | 感知（同 virtio-net） |
| **代表 CVE** | VENOM (CVE-2015-3456) | CVE-2019-14835 (V-gVisor) | CVE-2021-3501 (vhost-net OOB) |

未實測，吞吐量數字為理論參考，實際受 host 環境影響極大。

---

## 踩雷集錦

**1. `desc.addr` 是 HPA（Host Physical Address）**

錯。`vring_desc.addr` 存的是 **GPA（Guest Physical Address）**。device 端要用它之前，QEMU 必須呼叫 `address_space_map(vdev->dma_as, desc.addr, &len, MEMTXATTRS_UNSPECIFIED)` 做 DMA 翻譯，把 GPA 透過 EPT 對應轉成 HVA（Host Virtual Address），才能直接解參考。直接把 `desc.addr` 當指標用是找 segfault 的方式。

**2. avail.idx 不會 wrap 回零**

錯。`avail.idx` 是 `uint16_t`，最大值 65535，下一步就是 0——wrap 是設計的一部分。正確取 ring 位置要用 `avail.idx % VRING_SIZE`，或 `avail.idx & (VRING_SIZE - 1)`（VRING_SIZE 是 2 的冪）。如果你在 bug 分析裡看到有程式碼在比較 idx 時沒有 mask，那通常是個 bug（guest 可以利用 wrap 製造混淆）。

**3. 一個 desc 就是一次完整的 I/O 請求**

錯。絕大多數 virtio device 的 I/O 是 **descriptor chain**：至少一個 out desc（guest 提供的指令/header）+ 一個或多個 in desc（device 寫入結果的 buffer）+ 一個 status desc。`virtqueue_pop()` 追完整條 chain 才回傳一個 `VirtQueueElement`。你在讀 device 程式碼時看到 `elem->out_sg[0]` 是命令 header、`elem->in_sg[0]` 才是資料 buffer，這就是典型的 chain 結構。

**4. kick 就是 VM-EXIT**

錯。現代 virtio 設定下，kick 走的是 **eventfd**。guest driver 對 eventfd 做 `write()`，KVM 在 kernel 空間直接透過 ioeventfd 機制通知 vhost（如果用 vhost-net/vhost-blk，整個 data path 都在 kernel，不需要回到 QEMU userspace）。只有傳統 virtio PIO kick（寫 Queue Notify 暫存器）才強制 VM-EXIT。兩種 kick 路徑在分析效能或利用 TOCTOU 時行為截然不同。

**5. virtio-net 沒有額外 header，就是直接傳 packet**

錯。每個 virtio-net 資料包前面都有一個 `virtio_net_hdr`（或 `virtio_net_hdr_mrg_rxbuf`，若 VIRTIO_NET_F_MRG_RXBUF feature 開啟）：

```c
/* include/standard-headers/linux/virtio_net.h */
struct virtio_net_hdr {
    uint8_t  flags;        /* VIRTIO_NET_HDR_F_NEEDS_CSUM 等 */
    uint8_t  gso_type;     /* GSO_NONE / GSO_TCPV4 / GSO_TCPV6 / … */
    uint16_t hdr_len;
    uint16_t gso_size;
    uint16_t csum_start;
    uint16_t csum_offset;
    uint16_t num_buffers;  /* 只有 mrg_rxbuf 版才有 */
};
```

這個 header 是 GSO（Generic Segmentation Offload）和 checksum offload 的控制結構。device 端如果沒有正確驗證 `gso_type` 和 `hdr_len` 的合法性，就是一個攻擊點（Ch 26 有例子）。

---

## 進階：再往深一層

### Packed Ring 解決了什麼問題

Split ring 的 cache 問題在高封包率下很明顯：desc table、avail ring、used ring 三塊記憶體分開，guest 和 device 輪流讀寫不同的 cache line，造成頻繁的 cache line invalidation（bounce）。

Packed ring（virtio 1.1，Linux 5.0 起 guest driver 支援）把所有資訊整合進一個 ring：

```c
struct vring_packed_desc {
    uint64_t addr;
    uint32_t len;
    uint16_t id;
    uint16_t flags;   /* VRING_DESC_F_AVAIL | VRING_DESC_F_USED | WRITE | NEXT | … */
};
```

用 `VRING_DESC_F_AVAIL` 和 `VRING_DESC_F_USED` 兩個 bit 的狀態機（一共四個狀態）來代替 avail/used ring 的 idx。guest 和 device 都在同一塊 ring 上操作，cache 熱點集中，理論上在高核心數環境下效能更好。

QEMU 端在 `hw/virtio/virtio.c` 裡有 `virtqueue_packed_pop()` 和 `virtqueue_packed_push()`，邏輯更複雜，公開 bug 分析相對少——這意味著可能還有未被挖到的問題。

### VIRTIO_F_IN_ORDER

若雙方協商了 `VIRTIO_F_IN_ORDER` feature，device 保證以 **嚴格的提交順序** 放回 used ring（不會有亂序完成）。這讓 guest driver 可以省掉「等哪個 id 完成」的追蹤邏輯，但也讓 device 端的實作更嚴格。如果 device 在有 in_order 的情況下亂序 push，guest 可能誤以為某些尚未完成的 I/O 已完成——這是個潛在的 race condition bug 源。

### vring 記憶體所有權與 DMA 翻譯的攻擊面

vring 所在的 GPA 記憶體是 **guest 分配並管理的**。guest driver 把 vring 的 GPA 透過 PCI virtio 的 Queue PFN（Page Frame Number）暫存器告訴 device（split ring）或透過 Queue Desc、Avail、Used 三個暫存器（virtio 1.0 modern）告訴 device。

QEMU 在每次 `virtqueue_pop()` 裡，都要把 desc 的 GPA 透過 `address_space_map()` 翻成 HVA 才能操作。這個翻譯過程的完整路徑是：

```
desc.addr (GPA)
    → address_space_map(vdev->dma_as, desc.addr, &len, ...)
    → memory_region_find()（查 AddressSpace 的 MemoryRegion tree）
    → ram_block_host_ptr() 或 memory_region_get_ram_ptr()
    → HVA（QEMU 行程的虛擬位址）
```

如果 `desc.addr` 超出合法 GPA 範圍怎麼辦？`address_space_map()` 應該回傳 NULL，device 要檢查。但如果 device code 沒有檢查 NULL（[Ch 26](./26-virtio-bug-patterns.md) 有真實案例），或者長度（`desc.len`）過大導致 map 的範圍跨出 MemoryRegion 邊界，就會有問題。

更隱微的攻擊：TOCTOU（Time-of-Check Time-of-Use）。QEMU 把 GPA map 成 HVA 後，guest 在 QEMU 讀/寫這塊記憶體的過程中**仍然可以修改同一個 GPA 的內容**（因為 guest 沒有被暫停）。如果 device 對同一塊 buffer 做「先驗證再使用」，guest 就可以在兩次存取之間改資料，讓驗證通過但使用的是惡意內容。這是 TOCTOU / double-fetch bug，在 [Ch 26](./26-virtio-bug-patterns.md) 會詳細追蹤。

---

## 動手練習

**練習 1：追 `virtqueue_pop()` 的 element 建立流程**

目標：在 QEMU 9.0 原始碼 `hw/virtio/virtio.c` 裡，找到 `virtqueue_pop()` → `virtqueue_split_pop()`，手動追它是怎麼：
1. 讀 `avail.idx`（`vring_avail_idx(vq)`）
2. 取 head desc index（`vring_avail_ring(vq, vq->last_avail_idx)`）
3. 追 chain（`do { ... } while (flags & VRING_DESC_F_NEXT)`）
4. 區分 in/out desc（`flags & VRING_DESC_F_WRITE`）
5. 建立 `iov_in[]` / `iov_out[]`（`address_space_map()` 呼叫點在哪）

看懂這段程式碼後，在紙上描述：如果 guest 填了一個 `desc.len = 0xFFFFFFFF` 的 in desc，`address_space_map` 會怎麼處理？`in_sg[i].iov_len` 會是什麼？device 後來做 `iov_to_buf()`（或 `memcpy()`）時會發生什麼？

**練習 2：gdb 觀察一次真實的 virtio-blk 讀操作**

1. 啟動帶 debug symbol 的 QEMU，掛一個 virtio-blk 設備（`-drive if=virtio,file=disk.img,format=raw`）
2. `gdb -p $(pidof qemu-system-x86_64)`，下斷點：
   ```
   (gdb) b virtqueue_pop
   (gdb) b virtqueue_push
   ```
3. 在 guest 裡執行 `dd if=/dev/vda of=/dev/null bs=4096 count=1`
4. `virtqueue_pop` 命中時，`print *elem`（或手動讀 `elem->out_sg[0]`，看 header），確認 `iov_out[0]` 的內容符合 virtio_blk_req header 的格式（type=0 表示 read，sector=0）
5. `virtqueue_push` 命中時，看 `written` 參數是多少

這個練習建立「我知道 elem 裡實際放著什麼」的手感，是後面分析 bug PoC 的基礎。

**練習 3：手繪 3-descriptor chain 的完整 vring 快照**

假設 VRING_SIZE = 8，guest 剛送出了一個 virtio-blk read 請求（sector 0，4096 bytes），使用 desc index 2（header）→ 5（data buffer）→ 7（status）。

畫出此時的完整 vring 狀態，標出：
- `desc[2]`、`desc[5]`、`desc[7]` 的所有欄位（addr、len、flags、next）
- `avail.idx`（假設這是第一個請求）、`avail.ring[0]`
- `used.idx`（尚未完成，should be 0）

然後畫出 device 端 `virtqueue_pop()` 執行後，`VirtQueueElement` 的 `out_sg` 和 `in_sg` 分別對應到哪些 desc。

---

## 本章重點整理

- virtio 是 paravirtualization 框架，用共享記憶體 ring 取代傳統 MMIO trap，在高吞吐場景大幅降低 VM-EXIT 開銷。
- vring 由三個陣列組成：desc table（I/O buffer 描述）、avail ring（guest 通知 device 有新工作）、used ring（device 通知 guest 已完成）。
- 一次 I/O 是一條 descriptor chain：多個 desc 用 `VRING_DESC_F_NEXT` 串聯，`VRING_DESC_F_WRITE` 標記 device 可寫的 buffer。
- QEMU 端的 `virtqueue_pop()` 負責追 chain、做 GPA→HVA 翻譯、建立 `VirtQueueElement`；`virtqueue_push()` 把結果放回 used ring 並觸發 guest interrupt。
- avail.idx 和 used.idx 都是 `uint16_t`，wrap 是設計的一部分，存取 ring 陣列要 `idx % VRING_SIZE`。
- desc.addr 是 GPA，不是 HVA，device 端要透過 `address_space_map()` 翻譯才能存取。
- guest 完全控制 desc 的 addr、len、flags、chain 結構——這些都是不可信的輸入，device 端每個使用點都是潛在的攻擊面。

---

## 自我檢核

- [ ] 不查資料，能說出 vring 三個組成部分的名稱、方向（誰寫誰）、以及每個 entry 的大小。
- [ ] 對著 `vring_desc` 結構，能解釋 `flags = 0x3`（`NEXT|WRITE`）在 descriptor chain 裡代表什麼意思。
- [ ] 能描述 guest driver 從「分配 buffer」到「kick」的完整五步驟，說清楚 memory barrier 在哪裡、為什麼需要。
- [ ] 能說出 `virtqueue_pop()` 回傳的 `VirtQueueElement` 裡，`in_sg` 和 `out_sg` 各對應 chain 裡的哪種 desc。
- [ ] 能解釋為什麼 TOCTOU 在 virtio 場景是可行的攻擊手段（guest 什麼時候可以改 buffer 內容？）。
- [ ] Split ring 和 packed ring 的核心差異是什麼？packed ring 解決了哪個具體的效能問題？
- [ ] 如果一個 device 在 `virtqueue_pop()` 之後沒有檢查 `address_space_map()` 的回傳值，最壞情況會發生什麼？

---

## 延伸閱讀

**virtio OASIS 規格（virtio-v1.2-csd01）**
- 下載：https://docs.oasis-open.org/virtio/virtio/v1.2/
- 讀法：先讀 Chapter 2（Basic Facilities）—— ring、feature bit、transport 的定義在這；再讀 Chapter 5 選你關心的 device class（5.1 是 net，5.2 是 blk）。不用從頭到尾讀，把它當 reference 用。

**QEMU 原始碼**
- `hw/virtio/virtio.c`：`virtqueue_pop()`、`virtqueue_push()`、split/packed ring 的完整實作。
- `include/hw/virtio/virtio.h`：`VirtQueue`、`VirtQueueElement`、`VRing` 結構定義。
- `include/standard-headers/linux/virtio_ring.h`：`vring_desc`、`vring_avail`、`vring_used` 的 C 定義（來自 Linux kernel header，QEMU 直接 import）。
- `hw/block/virtio-blk.c`：一個乾淨的 virtio device 實作範例，`virtio_blk_handle_request()` 是很好的 `pop()` 使用範例。

**Rusty Russell 原始論文**
- 《VirtIO: Towards a De-Facto Standard For Virtual I/O Devices》（2008，SIGOPS Operating Systems Review，Vol. 42 Issue 1）
- 它比 spec 簡短，解釋了設計動機和核心 trade-off，20 分鐘讀完建立歷史背景。

**LWN 關於 packed virtqueue**
- 《Packed virtqueues》（2018-01-10，https://lwn.net/Articles/750659/）：Marc Zyngier 的分析，解釋 split ring 的 cache 問題以及 packed ring 如何解決。配合 QEMU 裡 `virtqueue_packed_pop()` 的程式碼讀，能理解設計決策。

**Red Hat virtio 技術指引**
- 《Introduction to VirtIO》（Red Hat Developer，https://developers.redhat.com/articles/2021/01/04/introduction-virtio）
- 著重 **virtio-net 的資料流** 和 feature negotiation（GRO/GSO/csum offload），對你理解 virtio-net 的攻擊面（Ch 28 會碰）很有幫助。這篇補的是 transport 層之上的 device-specific 行為。

---

split ring 的三環佈局和 descriptor chain 是 virtio 攻擊面的骨架，沒有這個基礎很難看懂後面的 bug 在哪裡出問題。下一章把這個骨架接上真實的 bug 模式。

→ [Ch 26](./26-virtio-bug-patterns.md)
