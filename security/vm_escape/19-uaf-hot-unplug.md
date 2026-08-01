# Ch 19 — UAF：hot-unplug 與狀態機錯誤

> **目標**：理解 QEMU device 中 use-after-free（UAF）的三大根源——PCI 熱插拔、timer callback 引用已釋放狀態、以及 MMIO reentrancy——以及如何從 guest 端利用 UAF 佔位（reclaim）被釋放的 chunk，為控制流劫持做準備。

> **環境**：QEMU 9.0 / x86-64 / Linux host（Ubuntu 22.04/24.04），自編帶 debug symbol 的 QEMU。真實 CVE 章節（e1000e CVE-2023-3019）以真實 QEMU 版本為準。

---

## 為什麼需要這個？

Ch 16-18 走的是「OOB → leak → heap overflow → 覆蓋指標」這條路。UAF 是另一條完全不同但同樣高效的路徑：

**OOB 路線**：需要 `buf` 和目標物件相鄰 → 需要 grooming → grooming 需要可控分配介面

**UAF 路線**：需要 device 釋放了某個物件但還保留著指標 → 用 guest 可觸發的分配填入偽造資料 → 等 device 用那個懸空指標時跳到我們控制的地址

UAF 的優點是不需要 OOB 的精確 offset 計算——只要能把釋放的 chunk 用我們控制的資料填滿，整個 struct 都是可控的，包括所有 function pointer。

QEMU device 的 UAF 特別容易出現，因為 device 的生命週期管理是手動的（沒有 GC），而 device 的狀態通常被多個地方引用：timer callback 引用 device state、中斷 handler 引用 device state、DMA 引用 device state。只要任何一個引用忘記清掉就 free，下次那個 callback 觸發就是 UAF。

---

## 先建立直覺

```
正常的 device 生命週期：

  realize()            device 使用中              unrealize()
  ┌──────┐    ┌────────────────────────────────┐    ┌──────┐
  │ alloc│───▶│ VulnState（g_malloc）           │───▶│ free │
  │      │    │  .timer → arm(cb=dma_cb, s)    │    │      │
  │      │    │  .dma_in_progress = true        │    │      │
  └──────┘    └────────────────────────────────┘    └──────┘
                             │                           │
                             │ 正常：unrealize 先 disarm │
                             │ timer，再 free state       │
                             └───────────────────────────┘

UAF 的情況（狀態機錯誤）：

  realize()     hot-unplug 觸發     free()       timer 火了！
  ┌──────┐   ┌──────────────────┐  ┌──────┐   ┌──────────────────┐
  │alloc │──▶│ EduState         │─▶│ free │   │ timer_cb(opaque) │
  │      │   │  dma_timer 掛著  │  │(state│   │ opaque → dangling│
  │      │   │  in-flight DMA   │  │freed)│   │ ptr！            │
  └──────┘   └──────────────────┘  └──────┘   └──────────────────┘
                                       ↑              │
                                 已釋放的 chunk        │
                                 可能被 reclaim 填入   │
                                 我們控制的資料  ←─────┘
                                                UAF：讀到我們寫的值

關鍵：「釋放」和「最後一個引用」之間的時間窗口 = 可利用的 UAF 窗口
```

---

## UAF 根源一：PCI 熱插拔（hot-unplug）

PCI 熱插拔讓 guest OS 可以在運行時移除一個 device。這個流程是：

```
guest:  echo 0 > /sys/bus/pci/devices/0000:00:04.0/remove
        ↓（或 QEMU QMP: device_del）
host:   QEMU 呼叫 device 的 unrealize() callback
        → unrealize() 應該：
            1. 停止所有 timer（timer_del）
            2. 等待 in-flight DMA 完成（或強制取消）
            3. 取消所有 interrupt handler 的引用
            4. 釋放資源（memory_region_del_subregion 等）
            5. g_free(state)（通常由 QOM 框架自動做）
```

如果 unrealize() 少做了步驟 1（忘記 disarm timer），就會留下一個 `QEMUTimer` 仍掛在 QEMU main loop 的 timer list 上，但 `cb` 和 `opaque` 指向已被 free 的 device state。等 timer 火，就是 UAF。

### 真實案例：QXL 顯示 device hot-unplug UAF

QEMU 的 QXL 顯示 device（`hw/display/qxl.c`）在修補前，當 secondary QXL adapter 被熱移除時，有多個 VM state-change handler 和 bottom half（BH，QEMU 的延遲執行機制）仍然掛著，指向已釋放的 `QXLState`。下次 VM 的 stop/continue/migrate 動作觸發這些 handler，就發生 UAF。（此 bug 的修補 commit 可在 QEMU repository 搜尋 `qxl: unregister handlers on hot-unplug` 找到。）

### 為何 hot-unplug 比 cold-unplug 更難搞

Cold-unplug（關機後拔除）：QEMU 整個行程結束，所有記憶體一起釋放，沒有懸空指標問題。

Hot-unplug：QEMU 行程仍在運行，只有被移除 device 的 state 被 free，但 QEMU 的其他組件（timer list、interrupt handlers、event notifiers）仍在跑。任何遺留的引用都會成為懸空指標。

---

## UAF 根源二：timer callback 引用已釋放狀態

QEMU 的 `QEMUTimer` 是一個非常常見的懸空指標來源：

```c
/* QEMU timer 的典型用法（device realize 時） */
s->dma_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL,
                             vuln_dma_timer_cb, s);
timer_mod(s->dma_timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + 1000000);

/* 正確的 unrealize 應做： */
static void vuln_unrealize(PCIDevice *pdev)
{
    VulnState *s = VULN_PCI(pdev);
    timer_del(s->dma_timer);    /* disarm：取消待觸發的 timer */
    timer_free(s->dma_timer);   /* 釋放 QEMUTimer 物件本身 */
    /* 然後 QOM 框架 free VulnState */
}

/* 有 bug 的 unrealize（忘記 disarm）： */
static void vuln_unrealize_buggy(PCIDevice *pdev)
{
    VulnState *s = VULN_PCI(pdev);
    /* BUG：忘記 timer_del(s->dma_timer)！ */
    timer_free(s->dma_timer);  /* 釋放了 timer 物件，但 callback 中的 opaque 仍懸空 */
    /* QOM 框架 free VulnState（s 指標失效） */
}
/*
 * 後果：QEMU main loop 繼續 check timer list，
 * 一旦到了觸發時間，呼叫 vuln_dma_timer_cb(opaque)，
 * opaque 指向已 free 的 VulnState → UAF。
 */
```

### edu device 的 DMA timer 模式

官方 edu device（`hw/misc/edu.c`）有一個 `dma_timer`，在 DMA 傳輸完成後觸發 callback。這個模式在真實研究中被用作 UAF 的示範（雖然 edu.c 本身是正確的，但如果 unrealize 路徑不完整，就是 bug）。

在實際 CTF 題型的 device 中，這個漏洞模式很常見：device 有一個「啟動後就一直跑的 timer」，但 unrealize 沒有妥善清掉它。

---

## UAF 根源三：MMIO reentrancy（CVE-2023-3019 e1000e 案例）

這是最隱蔽的 UAF 來源，也是最技術性的。

### 什麼是 MMIO reentrancy？

QEMU 的 MMIO handler（`vuln_mmio_write` 之類）本應是不可重入（non-reentrant）的——也就是說，一個 MMIO 操作在完成之前，不應再次觸發同一個 handler。但 QEMU 的事件機制允許在某些情況下，一個 MMIO 操作**在執行中途**觸發另一個 MMIO 操作。這叫 reentrancy。

具體路徑（以 e1000e 為例）：

```
guest: write TX descriptor → 觸發 e1000e_write_packet_to_guest()
           │
           │ e1000e 開始傳送封包，呼叫 address_space_read（DMA 讀取）
           │
           ▼
       DMA 讀取 guest 記憶體（address_space_rw）
           │
           │（如果 guest 的 TX descriptor 指向另一個 MMIO 範圍）
           │ guest 的 MMIO 訪問再次觸發 e1000e 的某個 handler！
           ▼
       reentrancy：e1000e handler 被再次呼叫
           │
           │ 如果這個 reentrant 呼叫釋放了 device state（或修改了共享結構）
           ▼
       外層呼叫繼續用舊的指標（或已修改的結構）→ UAF / Use-After-free
```

### CVE-2023-3019：e1000e DMA Reentrancy UAF

**CVE 編號**：CVE-2023-3019  
**影響版本**：QEMU（多個版本，修補時間 2023 年中）  
**嚴重性**：可能造成 host DoS，理論上可能 RCE（取決於 reclaim 能力）

**根本原因**：`e1000e_write_packet_to_guest()`（`hw/net/e1000e_core.c`）在 DMA 操作過程中沒有防止 reentrancy。如果 guest 構造了特殊的網路封包，使得封包傳送路徑觸發 DMA，而 DMA 讀取的目標地址恰好是 e1000e 的 MMIO 空間，就會造成遞歸呼叫。在特定的遞歸路徑上，device state 可能在外層調用仍持有引用時被修改或釋放，造成 UAF。

**漏洞函式**（精簡示意，非原始碼直接引用）：

```c
/* hw/net/e1000e_core.c（精簡示意，未實測）
 * 真實原始碼在 QEMU repository 的 e1000e_core.c */
static void e1000e_write_packet_to_guest(E1000ECore *core, ...)
{
    /* BUG（修補前）：沒有 reentrancy guard */
    
    for each rx_descriptor in ring {
        /* address_space_write 把封包資料寫入 guest 記憶體 */
        /* 如果 guest 的 descriptor 指向 e1000e MMIO → reentrancy */
        address_space_write(&core->owner->bus_master_as, ...);
        
        /* reentrant 呼叫可能修改 ring 狀態 */
        /* 外層 for loop 繼續使用已被修改的 descriptor pointer → UAF */
    }
}

/* 修補後：加 reentrancy guard */
static void e1000e_write_packet_to_guest(E1000ECore *core, ...)
{
    if (core->rx_in_progress) {
        return; /* 防止 reentrancy */
    }
    core->rx_in_progress = true;
    /* ... 原本的邏輯 ... */
    core->rx_in_progress = false;
}
```

> **未實測，理論預期**。上面的精簡程式碼是根據 CVE-2023-3019 的公開描述和 QEMU patch 的語意重構的，非原始碼的直接引用。真實複現請在受影響版本上對照 `hw/net/e1000e_core.c` 的 git log 找到修補 commit 並反向 apply。

**影響範圍**：CVE-2023-3019 被評為「可能 DoS，潛在 RCE」。DoS（QEMU crash）是直接的；RCE 需要 reclaim——被釋放的 e1000e core 物件要能被 guest 控制的分配填入偽造資料。這在理論上可行，但需要精確的 heap 佈局控制。

---

## 從 guest 觸發 UAF：時序控制

UAF 的利用分兩步：**觸發釋放**（free the object）和**佔位重新分配**（reclaim the freed chunk）。

```
時序：
  T0: device realize → VulnState 分配
  T1: 啟動 DMA timer → timer 掛在 main loop，opaque = &VulnState
  T2: guest 觸發 hot-unplug → VulnState 被 free（但 timer 仍掛著）
                                ↑
                                釋放後，這塊 heap chunk 進入 tcache/bin
  T3: guest 觸發另一個 g_malloc（大小相同）→ 拿到原 VulnState 的 chunk
      在這個新 chunk 填入偽造的 device state（含偽 function pointer）
  T4: timer 觸發 → 呼叫 cb(opaque)
      opaque 指向 T3 填入的偽資料 → 跳到我們控制的地址
```

時序控制在 guest 端必須是精確的——T2（free）和 T3（reclaim）之間不能讓 QEMU 的其他路徑分走那個 chunk（它會被 tcache 緩存，但 QEMU 的任何 g_malloc(same_size) 都可能取走它）。

---

## reclaim：用 guest 可控的分配佔位

reclaim 的核心問題：找到一個 guest 可觸發、能讓 QEMU 做 `g_malloc(N)` 的路徑，其中 N 和被 free 的 chunk 大小相同。

常見的 reclaim 來源：

| 方法 | 觸發方式 | 資料控制程度 |
|------|---------|------------|
| 另一個 device 的 realize | guest `echo 1 > /sys/bus/pci/...` 或 QMP | 有限（struct layout 固定）|
| device 的「alloc」MMIO 命令（CTF 題設計） | 寫 MMIO | 完全可控 |
| DMA buffer 分配 | guest 觸發 DMA，讓 device g_malloc DMA buffer | 有限（size 可控，內容是 DMA 資料）|
| virtqueue descriptor 的 g_malloc | guest 提交 virtio 請求 | 有限 |
| SGX / balloon driver 的 memory allocation | guest 調整 balloon | 受限 |

在 CTF 題型中，最常見的設計是：device 有一個「噴射」（spray）的 MMIO 命令，guest 可以分配任意大小的 chunk 並填入任意資料。

```c
/*
 * reclaim 骨架（概念示意，未實測，理論預期）
 * 假設 CTF device 提供一個 spray 介面
 */
#define REG_SPRAY_ALLOC    0x200   /* 寫 size → g_malloc(size) */
#define REG_SPRAY_WRITE    0x204   /* 寫入資料到最近 alloc 的 chunk */
#define VULN_STATE_SIZE    0x390   /* pahole 量到的 VulnState 大小（含 padding） */

static void spray_alloc(volatile uint8_t *bar0, size_t size) {
    *(volatile uint32_t *)(bar0 + REG_SPRAY_ALLOC) = (uint32_t)size;
}

static void spray_write(volatile uint8_t *bar0, uint64_t offset, uint64_t val) {
    *(volatile uint64_t *)(bar0 + REG_SPRAY_WRITE + offset) = val;
}

/* reclaim 目標：VulnState 內 dma_timer.cb 的 offset（假設值，需 pahole 確認） */
#define OFFSET_DMA_TIMER_CB    0x2d8   /* VulnState 內 QEMUTimer.cb 的 offset */

void reclaim_vuln_state(volatile uint8_t *bar0, uint64_t target_fn_ptr)
{
    /* 1. 分配一個和 VulnState 大小相同的 chunk */
    spray_alloc(bar0, VULN_STATE_SIZE);

    /* 2. 在 dma_timer.cb 的 offset 寫入偽函式指標 */
    spray_write(bar0, OFFSET_DMA_TIMER_CB, target_fn_ptr);

    /*
     * 3. 此時，reclaim 的 chunk 的 cb 欄位已是 target_fn_ptr。
     * 當 QEMU main loop 觸發 timer，呼叫 cb(opaque)，
     * 就會跳到 target_fn_ptr 指向的地址。
     * opaque = VulnState 本身（整個偽結構），
     * 可以在偽結構的 buf 區域放命令字串，讓 system(opaque+off) 執行。
     */
}
```

> **未實測，理論預期**。上面的 spray 介面是概念示意，真實 CTF 題的介面設計各有不同。在實際做 reclaim 時，必須用 host gdb 確認：(a) VulnState 的精確大小；(b) `dma_timer.cb` 在 VulnState 內的精確 offset。

---

## 底層機制：QEMUTimer 結構與 timer 觸發路徑

```c
/* include/qemu/timer.h（精簡，QEMU 9.0） */
struct QEMUTimer {
    int64_t expire_time;     /* 觸發時間（ns，virtual clock） */
    QEMUTimerList *timer_list; /* 所屬 timer list */
    QEMUTimerCB *cb;         /* callback function pointer ← UAF 目標 */
    void *opaque;            /* 傳給 cb 的第一個參數 ← 也是 UAF 目標 */
    QEMUTimer *next;         /* 鏈表指標 */
    int attributes;
    int scale;
};

/* QEMU main loop 觸發 timer 的路徑（大幅簡化）：
 *
 * main_loop_wait()
 *   → timerlist_run_timers(main_loop_tlg.tl[QEMU_CLOCK_VIRTUAL])
 *       → for each expired timer:
 *             ts->cb(ts->opaque);   ← 如果 opaque 是 dangling pointer → UAF
 */
```

```
QEMUTimer 在記憶體裡的位置：

方案 A：QEMUTimer 嵌在 device state 裡（最常見）
  VulnState {
    PCIDevice pdev;
    MemoryRegion mmio;
    char buf[0x100];
    uint32_t status;
    QEMUTimer dma_timer;   ← 嵌入 VulnState
  }
  → free(VulnState) 同時 free 了 QEMUTimer
  → 但 QEMU main loop 的 timerlist 仍持有 &s->dma_timer 的指標！
  → 下次 timerlist 觸發時，dereference 已 free 的記憶體 → UAF

方案 B：QEMUTimer 用 timer_new 獨立分配（另一種常見模式）
  s->dma_timer = timer_new_ns(...)   ← g_malloc(sizeof(QEMUTimer))
  free(VulnState)                    ← opaque（= s）失效
  timer 觸發 → cb(opaque) → opaque 是 dangling pointer → UAF
  （但 timer 物件本身還存在，是合法的記憶體）
```

---

## 對比與取捨

| 維度 | OOB overflow（Ch 18） | UAF（本章） |
|------|---------------------|------------|
| 前提 | 需要 OOB 寫的 primitive | 需要釋放後仍有引用（timer/handler）|
| grooming 難度 | 中（要把目標物件放到 buf 後方） | 中（要在 free 後 reclaim 之前精確佔位）|
| 可控程度 | 只能覆蓋 offset 範圍內的欄位 | 整個 chunk 都可控（如果 reclaim 完全可控）|
| 時序依賴 | 低（不需要精確時序） | 高（free 和 reclaim 之間有競爭窗口）|
| 穩定性 | 通常較穩定 | 有時需要多次嘗試（spray）|
| 常見 CVE 路徑 | VENOM、CVE-2019-14378 | CVE-2023-3019、QXL hot-unplug |

---

## 踩雷集錦

**雷 1：以為 hot-unplug 後 timer 自動清除**

直覺：device 移除了，所有資源自動回收。
實際：QEMU 的 QEMUTimer 是一個獨立的物件，掛在 timerlist 上；device unrealize 不會自動 disarm 它。必須在 unrealize callback 裡明確呼叫 `timer_del` + `timer_free`（或 `timer_del_and_free` 等 helper）。忘記其中一步都可能造成 UAF 或 memory leak。

**雷 2：reclaim 的大小必須和被 free 的 chunk 完全匹配**

直覺：差幾 bytes 的 chunk 也能拿到。
實際：glibc malloc 按大小把 chunk 分類（tcache 以 16-byte 為單位）。如果 reclaim 分配的大小和被 free 的 chunk 不同（落在不同的 bin），就拿不到那塊記憶體，UAF 不生效，下次 timer 觸發時 opaque 可能指向完全不同的東西，大機率 crash。

**雷 3：timer 觸發的時機不可控**

直覺：只要 reclaim 完成，等一下 timer 就會觸發。
實際：QEMU virtual clock 的推進取決於 guest OS 的 CPU 執行量。在 guest OS 暫停（如做 exploit 計算期間）時，virtual clock 可能停止推進，timer 不觸發。同時，如果用 `QEMU_CLOCK_REALTIME`（掛鐘時間），不受 guest 控制。根據 timer 使用的 clock 類型決定觸發策略。

**雷 4：reentrancy 類 UAF 難以穩定復現**

直覺：找到 reentrancy path 就能穩定觸發。
實際：MMIO reentrancy 的觸發需要 guest 的特定封包/資料格式讓 DMA 指向 MMIO 範圍。這個格式可能很脆弱——QEMU 的版本差異、guest OS 的記憶體佈局都可能讓 reentrancy path 改變。CVE-2023-3019 的 PoC 需要精確構造 e1000e TX descriptor，參數稍有不對就不會觸發 reentrancy。

**雷 5：spray 太多導致 tcache 被清空，reclaim 拿到錯誤的 chunk**

直覺：spray 多一點更穩。
實際：tcache 每個大小最多保存 7 個 chunk（glibc 2.35 預設）。spray 超過 7 個後，多出的 chunk 進 fastbin 或 unsorted bin，行為不同。在精確 reclaim 時，如果目標 chunk 已被 QEMU 其他路徑的 malloc 取走，再 spray 可能拿到不同的 chunk。建議 spray 精確的數量（通常 1-3 個），配合 gdb 確認。

---

## 進階：再往深一層

### reentrancy guard 是 QEMU 的標準修補模式

CVE-2023-3019 之後，QEMU 對多個 device 加了 reentrancy guard。標準做法：

```c
/* 在 device state 加一個 bool */
struct DeviceState {
    /* ... */
    bool in_processing;  /* reentrancy guard */
};

/* 在可能 reentrant 的函式加保護 */
static void device_process(DeviceState *s, ...) {
    if (s->in_processing) {
        return;  /* 或 queue for later */
    }
    s->in_processing = true;
    /* ... 正常處理 ... */
    s->in_processing = false;
}
```

從 exploit 角度看：reentrancy guard 把一個可連續觸發的 bug 變成「只能觸發一次」。但如果 guard 本身在 UAF 狀態下讀取（guard 的 bool 在已 free 的 struct 裡），guard 的值是我們在 reclaim 時填入的——可以填 `false` 繞過 guard。這就是 UAF 和 reentrancy guard 互動的微妙之處。

### spray + UAF 和 kernel_pwn 的 slab spray 比較

如果你走過 `kernel_pwn`，這個模式很眼熟：

| 維度 | kernel UAF spray | QEMU UAF spray |
|------|----------------|----------------|
| 分配器 | slab/slub allocator | glibc malloc（tcache/bins）|
| 觸發 free | kernel 物件 free（如 inode、sk_buff）| device unrealize / reentrancy |
| reclaim 方法 | 打開大量 fd、sendmsg 等 | MMIO alloc 命令、熱插拔另一個 device |
| 佔位控制程度 | 通常 partial（struct 格式固定）| CTF 題型可以完全控制 |
| spray 穩定性 | 需要大量 spray（通常 1000+） | 通常少量即可（目標明確）|

兩者的核心思維完全相同：釋放 → 佔位 → 觸發 use → 控制流劫持。QEMU 的版本因為是 userspace，GDB 可以更精確地看每一步。

### 從 Matryoshka Trap 論文看 recursive MMIO

CanSecWest 2022 的「Matryoshka Trap: Recursive MMIO Flaws Lead to VM Escape」論文（作者 Qiuhao Liu 等，PDF 在 qiuhao.org）系統性地分析了 QEMU 裡 recursive MMIO 造成的 UAF——這正是 CVE-2023-3019 類型漏洞的學術化總結。論文指出，QEMU 有多個 device 缺乏 reentrancy protection，可以從 guest 端透過構造特殊的 DMA 請求觸發 recursive MMIO，最終造成 UAF 或 OOB。閱讀這篇論文可以系統性地理解這類 bug 的發現方法論。

---

## 動手練習

1. **製造一個 UAF（概念驗證）**：修改 vuln-pci 的 `vuln_pci_realize`，加入一個 `dma_timer`（用 `timer_new_ns`），timer callback 印出 `s->status` 的值。然後修改 `unrealize`，故意**不呼叫** `timer_del`。在 guest 端熱插拔 device，觀察 host 端是否在 timer 觸發時產生 SIGSEGV 或讀到無效值。

2. **用 gdb 觀察懸空指標**：在 `timer_free` 之後（`unrealize` 執行後），用 host gdb `p *s->dma_timer` 觀察 timer 物件的狀態——`opaque` 欄位指向已 free 的 VulnState，但 timer 本身還存在 timerlist 裡。

3. **reclaim 測試**：在 VulnState 被 free 後（但 timer 未 disarm），立刻分配一個等大小的物件（如另一個 `g_malloc(sizeof(VulnState))`），在 `opaque` 的 offset 位置填入已知 canary 值（`0x4141414141414141`）。等 timer 觸發，確認 cb 嘗試把 canary 當 opaque 使用（在 host gdb 觀察）。

4. **分析 CVE-2023-3019 的 patch**：在 QEMU repository 找到 CVE-2023-3019 的修補 commit（搜尋 `e1000e reentrancy` 或 `rx_in_progress`），閱讀 diff，確認 reentrancy guard 加在哪一行。理解這個修補為何能阻止 UAF。

5. **比較 QEMUTimer 的兩種分配模式**：在 QEMU 的 `hw/` 目錄下找三個 device，確認它們各用哪種方式管理 timer（嵌入 state vs. `timer_new` 獨立分配），以及各自的 `unrealize` 是否有正確的 `timer_del`。

---

## 本章重點整理

- QEMU device 的 UAF 有三大根源：**hot-unplug 後殘留引用**、**timer callback 引用已釋放 state**、**MMIO reentrancy**。
- **CVE-2023-3019**（e1000e）是 DMA reentrancy 造成 UAF 的真實案例——DMA 操作中觸發的遞歸 MMIO 可以修改/釋放 device 的內部狀態，導致外層呼叫 use-after-free。
- UAF 的利用流程：free object → reclaim（用 guest 可控的分配佔位）→ trigger use（讓 QEMU 透過懸空指標讀取 reclaimed chunk）→ 控制流劫持。
- **QEMUTimer.cb** 和 **QEMUTimer.opaque** 是 UAF 最常見的高價值目標——整個 reclaim chunk 都可控，cb 直接是 function pointer。
- reclaim 的大小必須和 free 的 chunk 相同（tcache 按大小索引）。
- UAF + spray 的思維和 kernel_pwn 的 slab spray 完全相同——差別只在分配器（glibc malloc vs. slab）。
- reentrancy guard（`in_processing` bool）是標準修補，但如果 guard 本身在已 free 的 struct 裡，UAF 狀態下可以控制它的值。

---

## 自我檢核

- [ ] 說出 PCI hot-unplug UAF 的具體原因（unrealize 沒做什麼）
- [ ] 解釋 MMIO reentrancy 如何在 e1000e 的 DMA 路徑上造成 UAF（不看文章說出大意）
- [ ] CVE-2023-3019 的修補加了什麼？（`rx_in_progress` flag 的作用）
- [ ] 說出 reclaim 的正確操作順序（free 之後、trigger use 之前做什麼）
- [ ] 解釋為何 tcache 讓 reclaim 更容易控制（hint：per-size LIFO）
- [ ] 比較 UAF spray 在 QEMU（glibc malloc）和 kernel（slab）情境下的主要差異

---

## 延伸閱讀

1. **CVE-2023-3019 詳細描述**（[linuxpatch.com/cve/CVE-2023-3019](https://linuxpatch.com/cve/CVE-2023-3019)、[ubuntu.com/security/CVE-2023-3019](https://ubuntu.com/security/CVE-2023-3019)）
   - 讀哪裡：漏洞描述、受影響版本、patch commit 的 link。
   - 學什麼：DMA reentrancy UAF 的官方說明；從這裡找到 QEMU git commit hash，再去 QEMU repository 看實際 diff。
   - 關聯：本章 e1000e 案例的一手資料。

2. **「Matryoshka Trap: Recursive MMIO Flaws Lead to VM Escape」—CanSecWest 2022**（[qiuhao.org/Matryoshka_Trap.pdf](https://qiuhao.org/Matryoshka_Trap.pdf)）
   - 讀哪裡：Section 3（漏洞模型）和 Section 4（QEMU 中的具體 device）。
   - 學什麼：系統性地看 QEMU 裡哪些 device 缺乏 reentrancy protection；作者的漏洞搜尋方法論（用靜態分析找「DMA 中 call address_space_rw 的 handler」）。
   - 關聯：CVE-2023-3019 正是這篇論文方法論的後續成果。

3. **`timer_del` / `timer_free` 的正確用法—QEMU 開發者文件**（[qemu.org/docs/master/devel/qdev-api.html](https://www.qemu.org/docs/master/devel/qdev-api.html)）
   - 讀哪裡：關於 device lifecycle 的部分；`realize`/`unrealize` 的說明。
   - 學什麼：QEMU 官方對 resource cleanup 的期望；哪些資源必須在 unrealize 時清掉。
   - 關聯：理解「正確的做法」才能更快看出「哪裡是 bug」。

4. **`hw/misc/edu.c`—QEMU DMA timer 設計**（[GitLab](https://gitlab.com/qemu-project/qemu/-/blob/stable-9.0/hw/misc/edu.c)）
   - 讀哪裡：`edu_dma_timer`、`edu_realize`、`edu_unrealize` 這三段。
   - 學什麼：正確的 timer 生命週期管理長什麼樣；`edu_unrealize` 如何 disarm timer（這是 bug-free 的參考實作）。
   - 關聯：把 edu.c 的 unrealize 和本章「有 bug 的 unrealize」對比，就能一眼看出差在哪一行。

5. **「HITCON CTF QUAL 2024 reEscape writeup」**（[u1f383.github.io/ctf/2024/07/18/hitcon-ctf-qual-2024-pwn-challenge-part-2-reEscape.html](https://u1f383.github.io/ctf/2024/07/18/hitcon-ctf-qual-2024-pwn-challenge-part-2-reEscape.html)）
   - 讀哪裡：整篇，重點看 UAF 的觸發方式和 reclaim 策略。
   - 學什麼：真實 CTF 題如何在 QEMU custom device 上觸發 UAF 並完成 reclaim → 控制流劫持。這是最接近「看完本章後下一步去哪」的實作參照。
   - 關聯：本章的 reclaim 概念在 CTF 實戰中的具體形態。

---

> 你現在有了兩條攻擊路線：OOB overflow（Ch 16-18）和 UAF（本章）。兩條路線的終點相同——在 host 的 QEMU 行程裡控制一個 function pointer，並讓 QEMU 呼叫它。下一章處理的問題是：QEMU 裡有哪些函式指標是可劫持的目標？它們在哪裡、怎麼觸發？

→ [Ch 20 — 找可劫持的 function pointer：MemoryRegionOps、timer、QOM vtable](./20-hijackable-pointers.md)
