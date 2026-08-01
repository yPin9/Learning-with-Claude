# Ch 20 — 找可劫持的 function pointer：MemoryRegionOps、timer、QOM vtable

> **目標**：在 QEMU 堆上找出哪些結構體帶有 function pointer、哪個最適合當劫持標的，並理解觸發條件與 opaque 控制能力。

> **環境**：QEMU 9.0/x86-64/Linux

---

## 為什麼需要這個？

Ch 19 我們已經掌握寫語語（write primitive）——可以在 QEMU 堆上任意位置寫入數值。問題是：寫在哪裡才能讓 RIP 跳到我們要的地址？

「任意寫→控制 RIP」的橋梁就是 function pointer（函式指標）。QEMU 是事件驅動架構，heap 上散落著大量指向函式的指標，等著 QEMU 主迴圈在特定事件發生時呼叫。只要覆蓋其中一個，下次事件觸發時 RIP 就飛到我們指定的位置。

這個技巧不是新創——2015 年以前的 VENOM（CVE-2015-3456）exploit 就是覆蓋 floppy controller 的 DMA handler。到了現代 QEMU，ops table 和 timer callback 仍是主流路線，Pwn2Own 2024 的多份 QEMU writeup 都圍繞這兩個結構。

關鍵限制：QEMU 9.0 啟用了 CFI（Control Flow Integrity）時，間接呼叫必須落在合法目標集合內。競賽環境的 QEMU 通常是 debug build，不啟用 CFI，但要先確認。本章假設無 CFI。

---

## 先建立直覺

### 直覺 1：QEMU 是個大型回調機器

QEMU 幾乎所有 I/O 都透過回調完成：
- Guest 寫 MMIO → `ops->write(opaque, addr, data, size)`
- Timer 到期 → `timer->cb(timer->opaque)`
- Virtio descriptor 處理完 → coroutine entry 回調

每個回調都是一個「如果我能覆蓋這裡，CPU 就會跳去我想要的地方」的機會。

### 直覺 2：opaque 是控制 RDI 的關鍵

x86-64 System V ABI 第一個參數放在 RDI。幾乎所有 QEMU 回調的第一個參數是 `void *opaque`，這個值來自結構體裡的欄位。如果我們能同時控制 function pointer 和 opaque，就能做到：

```
RDI = controlled value
RIP = controlled address
```

這等同於 `arbitrary_func(controlled_arg)`，對於 `system("/bin/bash")` 這類 one-gadget 已經足夠。

### 直覺 3：距離決定難度

OOB write 從溢出點出發，能走多遠取決於堆的佈局。距離溢出點越近的 function pointer，越容易到達，也越可靠。

---

## 底層機制：五種候選結構

### 候選 1：MemoryRegionOps（最確定的觸發點）

定義在 `include/exec/memory.h`：

```c
struct MemoryRegionOps {
    uint64_t (*read)(void *opaque, hwaddr addr, unsigned size);
    void     (*write)(void *opaque, hwaddr addr, uint64_t data, unsigned size);
    /* valid, impl, endianness 等欄位 */
};
```

`MemoryRegion` 結構裡有個指向它的指標：

```c
struct MemoryRegion {
    Object parent_obj;           /* ~32 bytes：QOM 物件頭 */
    bool romd_mode;
    bool ram;
    bool subpage;
    /* ... 多個 bool/enum 欄位 ... */
    const MemoryRegionOps *ops;  /* << 目標：指向 ops 結構的指標 */
    void *opaque;                /* << 目標：傳給 ops 函式的 opaque */
    /* ... MemoryRegionSection、RAMBlock 等 ... */
};
```

`ops` 和 `opaque` 的確切偏移因 QEMU 版本和編譯選項而異。**必須用 pahole 或 GDB `ptype /o MemoryRegion` 取得實際偏移，不要硬編碼。**

觸發路徑：guest 在 MMIO 範圍內執行任何讀寫 → QEMU 查 `mr->ops` → 呼叫 `ops->write(mr->opaque, addr, data, size)` 或 `ops->read(...)`。

劫持策略有兩種：
1. **覆蓋 `mr->ops` 指標**：把它指向我們偽造的 ops 結構（位於已知地址，例如堆上我們控制的區域）。偽造的 ops 裡填入目標函式地址。
2. **覆蓋 ops struct 本身**：如果 ops 是在可寫記憶體，直接改 `write` 欄位。但標準 ops 是 `const` 靜態資料，通常在 `.rodata`，無法直接覆寫。

所以路徑 1 是正解：我們需要一個已知地址放假 ops，然後把 `mr->ops` 改成指向那裡。

### 候選 2：QEMUTimer.cb（最易到達的目標）

定義在 `include/qemu/timer.h`：

```c
struct QEMUTimer {
    int64_t      expire_time;   /* 奈秒，到期絕對時間 */
    QEMUTimerList *timer_list;
    QEMUTimerCB  *cb;           /* void cb(void *opaque)，劫持目標 */
    void         *opaque;       /* 傳給 cb 的第一個參數 */
    QEMUTimer    *next;
    int          attributes;
    int          scale;
};
```

`timer->cb` 被呼叫的時機：`qemu_clock_run_timers()` 在主迴圈每輪檢查是否有到期 timer，若 `expire_time <= qemu_clock_get_ns(...)` 則呼叫 `t->cb(t->opaque)`。

關鍵優勢：`QEMUTimer` 常常 **嵌入在設備狀態結構內**，不是獨立的 malloc chunk。以 edu 設備（`hw/misc/edu.c`）為例，`EduState` 裡有 `dma_timer` 欄位緊鄰 DMA buffer。OOB write 從 DMA buffer 溢出，直接覆蓋 `dma_timer.cb`——不需要跨 chunk 邊界。

觸發控制：guest 可以透過寫設備暫存器排程 timer 到期（或等待 expire_time 到來）。時間上不像 MMIO 那麼即時，但仍可預測。

OtterSec 2024 virtio-snd CTF challenge 使用了這個路徑：heap overflow → timer.cb 覆蓋 → trigger by waiting for expiry。（本資訊來自公開 writeup，讀者應自行核對細節）

### 候選 3：QOM class vtable（難以到達）

QOM（QEMU Object Model）每個類別有一個 class object，內含 method pointer：

```c
/* DeviceClass.reset、PCIDeviceClass.realize 等 */
typedef struct DeviceClass {
    ObjectClass parent_class;
    /* ... */
    DeviceReset *reset;
    /* ... */
} DeviceClass;
```

問題：class object 是全域單例（`type_info.class_init` 初始化），儲存在獨立的 heap chunk，與 device instance 不在同一塊記憶體。要從 `VulnState` 的 OOB write 到達這個 chunk，需要堆風水（heap feng shui）讓它們相鄰。理論可行，實作複雜，不是首選。

### 候選 4：IOHandler（現代版幾乎不用）

`qemu_set_fd_handler()` 在 fd 可讀/寫時呼叫指定函式。這個機制在現代設備實作裡不常見，且觸發需要外部 fd 事件，攻擊者控制性差。列入清單僅供完整性。

### 候選 5：Coroutine entry（複雜觸發路徑）

`Coroutine.entry` 是 coroutine 入口 function pointer，virtio-blk、virtio-net 等設備大量使用。劫持 `co->entry` 需要知道 coroutine 的 heap 地址，且觸發依賴 coroutine 調度，路徑較長。在有更簡單目標時不優先考慮。

---

## ASCII 圖：堆上的結構佈局

### VulnState 內的 MemoryRegion.ops

```
QEMU heap（單一 malloc chunk，g_malloc0 by QEMU）

┌─────────────────────────────────────────────────────────┐  ← VulnState *s
│  PCIDevice pdev                    (~248 bytes)          │
│    Object parent_obj                                     │
│    PCIDeviceClass *class                                 │
│    uint8_t config[256]                                   │
│    ...                                                   │
├─────────────────────────────────────────────────────────┤
│  MemoryRegion mmio                 (~200+ bytes)         │
│    Object parent_obj     (+0x00)                         │
│    bool romd_mode        (+0x20)                         │
│    ...                                                   │
│    const MemoryRegionOps *ops ◄── 我們要覆蓋這裡        │  ← offset: 用 pahole 查
│    void *opaque           ◄── 也要覆蓋（控制 RDI）       │
│    ...                                                   │
├─────────────────────────────────────────────────────────┤
│  char buf[0x100]          ← Ch 16 OOB 溢出起點          │
│  [0x00 ~ 0xff]                                           │
├─────────────────────────────────────────────────────────┤
│  uint32_t status                                         │
└─────────────────────────────────────────────────────────┘

OOB 方向：buf 向「低地址方向（往 mmio 走）」或「高地址方向（往 status 走）」
取決於溢出是往前還是往後。MMIO region 通常在 buf 之前（低地址）。
→ 需要往前溢出，或透過 UAF 讀回再精確寫入。
```

**注意**：`MemoryRegion` 嵌在 `VulnState` 裡，`buf` 在它之後。要從 `buf` 的 OOB 覆蓋 `mmio.ops`，必須是「往前（低地址）溢出」。若你的漏洞只能往後寫，需要換目標或換策略。

### edu-like 設備的 QEMUTimer 佈局

```
假想的 EduState heap chunk（類 edu 設備）

┌─────────────────────────────────────────────────────────┐  ← EduState *s
│  PCIDevice pdev                    (~248 bytes)          │
│  MemoryRegion mmio                 (~200 bytes)          │
│  MemoryRegion dma_mem              (~200 bytes)          │
├─────────────────────────────────────────────────────────┤
│  QEMUTimer dma_timer               (~48 bytes)           │
│    int64_t expire_time   (+0x00)                         │
│    QEMUTimerList *timer_list (+0x08)                     │
│    QEMUTimerCB *cb        (+0x10) ◄── 覆蓋目標          │
│    void *opaque           (+0x18) ◄── 控制 RDI          │
│    QEMUTimer *next        (+0x20)                        │
│    int attributes         (+0x28)                        │
│    int scale              (+0x2c)                        │
├─────────────────────────────────────────────────────────┤
│  struct { dma_addr_t src; ... } dma  ← DMA buffer       │
│  [向高地址 OOB 溢出，走回 dma_timer]                    │
│  注意：dma_timer 在 dma 之前，往前溢出才能到達          │
└─────────────────────────────────────────────────────────┘

觸發：guest 寫 DMA cmd register → QEMU 呼叫 timer_mod(dma_timer, ...)
     → 主迴圈偵測到期 → dma_timer.cb(dma_timer.opaque)
```

**誠實聲明**：以上 offset 和相對順序是根據 QEMU source 推導，**未在 QEMU 9.0 上實測**。實際偏移必須用 `pahole hw/misc/edu.o` 或 GDB `p &s->dma_timer` 驗證。不同 QEMU 版本 struct 大小有異動，請在目標環境重新量測。

---

## 對比與取捨

| 目標 | 到達難度 | 觸發控制性 | opaque 可控 | 備註 |
|------|---------|-----------|------------|------|
| `QEMUTimer.cb` | ★★（嵌在 device state，OOB 直接到達） | ★★★（寫 timer 暫存器即可排程） | ✅（`timer.opaque` 相鄰） | 首選；OtterSec 2024 實例 |
| `MemoryRegionOps *ops`（覆蓋指標） | ★★★（mmio 在 buf 之前，需往前寫） | ★★★（做 MMIO 立即觸發） | ✅（`mr->opaque` 可一起覆蓋） | 需要一塊已知地址放假 ops |
| `MemoryRegionOps.write`（直接改 ops） | ✗（ops 在 `.rodata`，不可寫） | — | — | 不可行，除非有 KASLR 洞 |
| QOM class vtable | ★★★★（class object 獨立 chunk） | ★★（reset 等少數路徑） | 視情況 | 需要堆風水，複雜 |
| `IOHandler.fd_read` | ★★★（需找到 fd handler chunk） | ★（依賴外部 fd 事件） | 部分 | 現代設備少用 |
| Coroutine.entry | ★★★★（需知 coroutine 地址） | ★★（virtio 調度依賴） | 部分 | 路徑長，不優先 |

**推薦優先順序**：

1. **`QEMUTimer.cb`**：如果設備有 embedded timer（edu、virtio-blk 等），這是最直接的路線。
2. **`MemoryRegionOps *ops`**：通用路線，VulnPCI 設備必有 MemoryRegion，問題是能不能往前溢出到 `mmio.ops`。
3. 其餘候選在前兩者無法到達時考慮。

---

## 踩雷集錦

**錯誤直覺 1：ops 結構可以直接覆寫**

`MemoryRegionOps` 通常宣告為靜態 const：
```c
static const MemoryRegionOps vuln_mmio_ops = {
    .read  = vuln_mmio_read,
    .write = vuln_mmio_write,
};
```
它住在 `.rodata` 段，不可寫。要劫持必須覆蓋 `mr->ops` 這個「指向 ops 的指標」，讓它指向一個我們偽造的可寫 ops struct。

**錯誤直覺 2：覆蓋 ops 指標後 opaque 自動對了**

覆蓋 `mr->ops` 改變了函式指標，但 `opaque`（RDI）還是原來的值（通常是 `VulnState *`）。如果目標是 `system`，RDI 必須指向 `"/bin/sh"` 字串。所以要**同時覆蓋 `mr->opaque`**，或找一個 first argument 不重要的 one-gadget。`mr->opaque` 緊跟在 `mr->ops` 後面，確認實際偏移後一起覆蓋。

**錯誤直覺 3：timer.cb 觸發是瞬間的**

覆蓋 `timer.cb` 後，callback 不會立即執行。`QEMUTimer` 只在 `expire_time <= 當前時間` 且主迴圈跑到 timer 檢查點時才觸發。如果 timer 的 `expire_time` 是 `INT64_MAX`（未初始化或設備沒啟動 timer），它永遠不會到期。需要確認設備有提供「啟動 timer」的介面（例如寫 DMA command register），或自己算一個過去的 expire_time 填進去。

**錯誤直覺 4：struct 大小可以靠看 source 推算**

結構體大小受 `#ifdef` 條件編譯、對齊 padding、QEMU 版本影響。兩個 QEMU 版本之間 `MemoryRegion` 的 `ops` 偏移可能差了 8 或 16 bytes。**一定要在目標 binary 上用 `pahole` 或 GDB 確認**：

```bash
pahole --class_name=MemoryRegion /path/to/qemu-system-x86_64 | grep -A2 ops
```

或在 GDB 裡：
```
(gdb) ptype /o MemoryRegion
```

**錯誤直覺 5：QOM class object 跟 device instance 在同一個 chunk**

`PCIDeviceClass`、`DeviceClass` 等 class object 在 `type_register_static` 時分配，比 device instance 更早，通常不相鄰。用 GDB `p qdev_get_machine()` 和 `p s->parent_obj.class` 確認兩個地址的距離再決定是否值得嘗試。

---

## 進階：再往深一層

### 偽造 ops struct 放在哪裡？

需要一塊已知地址的可寫記憶體放 fake ops。常見選項：

1. **QEMU heap 上我們控制的 chunk**：Ch 16 的 `buf` 本身就是已知地址（Ch 17 infoleak 拿到了 heap base）。把 fake ops 寫在 `buf` 的起始位置，然後把 `mr->ops` 覆蓋成 `heap_base + buf_offset`。
2. **VulnState.buf 本身**：`buf` 是 `char buf[0x100]`，我們對它有完全控制。把 fake ops 放在 `buf[0..15]`，填入目標函式地址，然後覆蓋 `mr->ops = &s->buf`（地址 = `heap_base + sizeof(PCIDevice) + sizeof(MemoryRegion)`）。

### ops->write 的參數簽章

```c
void (*write)(void *opaque, hwaddr addr, uint64_t data, unsigned size);
```

RDI = opaque，RSI = mmio offset，RDX = 寫入的值，RCX = size。如果目標是 `system(cmd)`，只有 RDI 需要對。可以把 `mr->opaque` 覆蓋成指向含有 `"/bin/sh"` 字串的地址（可以放在 `buf` 尾端）。

### 為什麼優先 timer.cb 而不是 ops.write？

`QEMUTimer` 的兩個欄位 `cb` 和 `opaque` 在結構裡緊鄰（+0x10 和 +0x18），一次 8+8 bytes 的連續寫入即可同時控制函式和第一個參數。`MemoryRegion.ops` 和 `.opaque` 的距離取決於結構大小（可能相差幾十 bytes），需要更精準的多次寫入。

### QEMU 9.0 的輕微保護

QEMU 9.0 預設 build 在 Debian/Ubuntu 上通常不啟用 CFI，但有 `_FORTIFY_SOURCE` 和 stack canary。我們的目標是 heap 上的 function pointer，不觸及 stack，canary 不影響。`FORTIFY_SOURCE` 對 `memcpy`/`strcpy` 添加邊界檢查，但 QEMU 設備的 MMIO write handler 裡的 `buf` 操作不一定走這些安全版本，視 vuln device 的實作而定。

---

## 動手練習

**練習 1：用 pahole 取得真實偏移**

在你的 QEMU 9.0 build 上執行：
```bash
pahole --class_name=MemoryRegion build/qemu-system-x86_64 2>/dev/null | head -40
```
記下 `ops` 和 `opaque` 的 byte offset。接著算出從 `VulnState` 起點到 `mmio.ops` 的距離：`sizeof(PCIDevice) + offset_of(MemoryRegion, ops)`。

**練習 2：GDB 驗證 timer 結構**

啟動 QEMU + edu 設備（`-device edu`），在 GDB 裡：
```bash
(gdb) p &edu_state
(gdb) p &edu_state->dma_timer
(gdb) p &edu_state->dma_timer.cb
(gdb) p &edu_state->dma_timer.opaque
```
確認 `dma_timer` 和 `dma` buffer 的相對位置，計算 OOB 寫入需要的距離。（edu 設備只在 `hw/misc/edu.c` 存在，需要 `--enable-edu` 編譯或使用含 edu 的測試 QEMU build）

**練習 3：偽造 ops 觸發**

在 VulnPCI exploit 框架上（Ch 16-19 的基礎），寫一個概念驗證：
1. 把 fake ops 寫入 `buf[0..15]`：`buf[0..7] = addr_of_printf`（先用 printf 測試，不直接跳 shell）
2. 計算 `mr->ops` 的 heap 地址
3. 透過 Ch 19 的 write primitive 覆蓋 `mr->ops`
4. 做一次 MMIO write，觀察是否跳入 printf

成功後將 printf 換成 system，`mr->opaque` 換成 `/bin/sh` 字串地址。

---

## 本章重點整理

- QEMU heap 上有五類 function pointer：MemoryRegionOps、QEMUTimer.cb、QOM vtable、IOHandler、Coroutine entry。
- `QEMUTimer.cb` 是首選：常嵌在設備狀態結構，OOB 最易到達，`cb` 和 `opaque` 連續，觸發可控。
- `MemoryRegionOps *ops` 是通用路線：適用所有 PCI 設備，需要往前溢出到 `MemoryRegion.ops` 欄位，還需要一塊已知地址放 fake ops struct。
- ops struct 本身在 `.rodata`，不可直接覆寫，必須覆蓋**指向 ops 的指標**。
- `opaque`（RDI）和 function pointer 要同時控制，才能達到 `arbitrary_func(controlled_arg)`。
- 所有 struct 偏移必須在目標 binary 上用 pahole / GDB 確認，不能靠閱讀 source 猜測。
- QEMU 9.0 標準 debug build 無 CFI，heap function pointer 劫持是可行路線。

---

## 自我檢核

- [ ] 我能說出 `MemoryRegionOps.write` 被呼叫時，RDI/RSI/RDX/RCX 分別對應什麼
- [ ] 我知道為什麼不能直接覆蓋 `vuln_mmio_ops.write`（`.rodata`），以及正確做法是什麼
- [ ] 我能用 `pahole` 取出 `MemoryRegion.ops` 的 byte offset
- [ ] 我能解釋 `QEMUTimer.cb` 和 `QEMUTimer.opaque` 的觸發時機與觸發條件
- [ ] 我知道 fake ops struct 可以放在哪裡，以及如何算出它的 heap 地址
- [ ] 我能說出 QOM class vtable 難到達的原因（class object 與 instance 不同 chunk）
- [ ] 我能在 GDB 裡確認 edu 設備 `dma_timer` 與 DMA buffer 的相對距離

---

## 延伸閱讀

**1. QEMU source：`include/exec/memory.h`**
讀什麼：`MemoryRegion` 完整結構定義，找 `ops` 和 `opaque` 欄位。
學到什麼：真實 struct layout，以及 `memory_region_init_io` 如何設定 opaque。
相關性：直接對應本章劫持目標，必讀。

**2. QEMU source：`include/qemu/timer.h`**
讀什麼：`QEMUTimer` 結構，`timer_mod`、`qemu_clock_run_timers` 的宣告。
學到什麼：timer 生命週期，expire_time 單位（奈秒），cb 呼叫路徑。
相關性：理解 timer 觸發機制，決定攻擊視窗。

**3. 「VENOM: Virtualized Environment Neglected Operations Manipulation」（CrowdStrike, 2015）**
讀什麼：原始 VENOM 分析，floppy controller DMA handler 的 function pointer 劫持。
學到什麼：最經典的 QEMU function pointer hijack 案例，技術原理與現代路線一致。
相關性：歷史基準，理解這個技巧的根源。

**4. OtterSec CTF writeup：virtio-snd（2024）**
讀什麼：搜尋 "ottersec virtio-snd qemu ctf 2024"，找公開 writeup。
學到什麼：heap overflow → `QEMUTimer.cb` 劫持的完整實例，含偏移計算和觸發細節。
相關性：本章 timer 路線的現代實作參考。

**5. `pahole` man page + 「Debugging with GDB」第 13 章（Type printing）**
讀什麼：`pahole --class_name` 用法；GDB `ptype /o` 指令。
學到什麼：在目標 binary 上取得精確 struct layout，包含 padding 和 alignment。
相關性：本章強調「不能靠看 source 猜偏移」，這兩個工具是必備驗證手段。

---

確認了可劫持的 function pointer 以及如何到達它之後，下一步是組合所有 primitive——把 write primitive 瞄準 `timer.cb` 或 `mr->ops`，搭配 one-gadget 或 `system("/bin/sh")`，完成從 guest userland 到 QEMU host process 的全鏈利用。

→ [Ch 21](./21-write-to-rip.md)
