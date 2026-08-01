# Ch 15 — 把 MMIO/DMA 當原語來源：從 guest 觸發 host 讀寫

> **目標**：理解如何把 MMIO OOB 和 DMA 操作組合成可控的「讀寫原語」，為 Part 3 的 leak → 劫持 → ROP 完整利用鏈搭建概念橋樑。

> **環境**：QEMU 9.0 / x86-64 / Linux（Ubuntu 22.04/24.04，需自編帶 symbol 的 debug QEMU）

---

## 為什麼需要這個？

Ch 13 你看清楚了 DMA 的資料流；Ch 14 你知道 device 物件住在 glibc heap 上、function pointer 就在那裡。但「知道 bug 存在」和「能把 bug 轉成可重複執行的攻擊原語」之間，有一道必須跨越的鴻溝：你需要把任意觸發的行為精煉成**讀原語**（能讀 host 任意位址的內容）和**寫原語**（能寫 host 任意位址的內容）。有了這兩個原語，後面的 leak → 劫持 → ROP 就是 `binary_exploitation` 的標準流程。

這章是 Part 2 的最後一塊，也是 Part 3 的入口。

---

## 先建立直覺：原語階梯

```
Guest 的能力（你能控制的）
        │
        ▼
┌──────────────────────────────────────────────────────┐
│ Tier 0：MMIO 讀寫                                    │
│   guest mmap BAR0 → 寫 MMIO offset → 觸發 device 動作│
│   guest mmap BAR0 → 讀 MMIO offset → 拿 device 回傳值│
└──────────────────┬───────────────────────────────────┘
                   │ 如果有 OOB
                   ▼
┌──────────────────────────────────────────────────────┐
│ Tier 1：相對讀寫原語（heap 上的 OOB）                │
│   MMIO write OOB → 寫 buf 之後的 heap 內容           │
│   MMIO read  OOB → 讀 buf 之後的 heap 內容           │
│   位移 = MMIO offset - buf_base_offset               │
└──────────────────┬───────────────────────────────────┘
                   │ 用 OOB read leak 位址
                   ▼
┌──────────────────────────────────────────────────────┐
│ Tier 2：位址 leak（破 ASLR）                         │
│   讀出鄰居 chunk 的指標值 → heap/PIE base 計算        │
│   知道 base 之後，所有偏移量可計算                    │
└──────────────────┬───────────────────────────────────┘
                   │ 用 OOB write + leaked addr
                   ▼
┌──────────────────────────────────────────────────────┐
│ Tier 3：任意寫（function pointer 蓋寫）              │
│   OOB write → MemoryRegion.ops / QEMUTimer.cb        │
│   下次 guest 觸發 MMIO 或 timer tick →              │
│   host 跳到你選的 function pointer                  │
└──────────────────┬───────────────────────────────────┘
                   │ 控制流 → ROP
                   ▼
┌──────────────────────────────────────────────────────┐
│ Tier 4：任意程式碼執行（host userspace）             │
│   ROP chain → system("cmd") / execve("/bin/sh")     │
│   = VM escape                                        │
└──────────────────────────────────────────────────────┘
```

這章專注 Tier 0→1→2→3 的概念和實作方式；Tier 4 的 ROP 細節在 Ch 22。

---

## Tier 0：MMIO 讀寫的基本操作

guest 要存取 PCI device 的 BAR0，標準方式是 mmap `/sys/bus/pci/devices/<addr>/resource0`：

```c
/* guest 端程式碼（未實測，理論預期）*/
#include <sys/mman.h>
#include <fcntl.h>
#include <stdint.h>

#define RESOURCE_PATH "/sys/bus/pci/devices/0000:00:04.0/resource0"
#define BAR_SIZE      0x1000   /* BAR0 大小，依 device 設定 */

int fd = open(RESOURCE_PATH, O_RDWR | O_SYNC);
volatile uint8_t *bar = mmap(NULL, BAR_SIZE, PROT_READ | PROT_WRITE,
                              MAP_SHARED, fd, 0);

/* 寫 offset 0x10：觸發 device 的 .write callback，addr=0x10 */
*(volatile uint32_t *)(bar + 0x10) = 0xdeadbeef;

/* 讀 offset 0x10：觸發 device 的 .read callback，addr=0x10 */
uint32_t val = *(volatile uint32_t *)(bar + 0x10);
```

`bar + offset` 的每一次讀寫，都是：guest page fault → KVM VMEXIT → QEMU 的 MMIO dispatch → device 的 `.read`/`.write` callback（Ch 11 講的路徑）。

QEMU 端 device 的 callback 拿到的 `addr` 就是你寫的 `offset`——這是你的第一個可控輸入。

---

## Tier 1a：MMIO OOB Read → 相對讀原語

Ch 12 的 vuln device 的 `mmio_read` 大概是：

```c
static uint64_t vuln_mmio_read(void *opaque, hwaddr addr, unsigned size)
{
    VulnState *s = opaque;
    /* 無邊界檢查：addr 可以是任意值 */
    return *(uint64_t *)(s->buf + addr);
}
```

當 guest 讀 `bar + offset`，device 執行 `*(uint64_t *)(s->buf + offset)`。

如果 `offset >= 0x100`（buf 大小），就讀到 `buf` 後面的 struct 欄位——在 host 的 QEMU heap 上做相對 OOB read：

```
s->buf 的起始 host VA（假設是 0x555557a01020）

guest 讀 bar+0x100 → device 讀 host VA 0x555557a01120
guest 讀 bar+0x108 → device 讀 host VA 0x555557a01128
guest 讀 bar+0x110 → device 讀 host VA 0x555557a01130
...
```

你看到的值就是 `s->buf` 之後的 heap 內容。如果那裡有一個 function pointer（例如 `MemoryRegion.ops` 的值，指向 QEMU PIE 的某個位址），你就**洩漏了一個 PIE 內部位址**。

**這個 OOB read 有多強取決於 struct layout**。如果 `buf` 後面緊接 `MemoryRegion mmio`，你能讀出 `mmio.ops`（PIE base + offset）；如果後面是一個 heap 指標（例如 `QEMUTimer *dma_timer`），你能讀出 heap 位址。

### Linux 驗證步驟（未實測，理論預期）

1. 在 host 用 gdb attach QEMU，`print s->buf` 看 `buf` 的 host VA。
2. 在 guest 讀 `bar + 0x100`，記錄回傳值。
3. 在 host gdb `x/8xg s->buf + 0x100` 對比——應該一致。
4. 如果回傳值看起來像 `0x555555xxxxxx`，那是 PIE 位址；`0x7f...` 可能是 heap 或 library；`0x000055...` 是 heap 低位址。

---

## Tier 1b：MMIO OOB Write → 相對寫原語

vuln device 的 `mmio_write`：

```c
static void vuln_mmio_write(void *opaque, hwaddr addr, uint64_t data, unsigned size)
{
    VulnState *s = opaque;
    /* 無邊界檢查 */
    *(uint64_t *)(s->buf + addr) = data;
}
```

guest 寫 `bar + offset`，device 寫 host VA `s->buf + offset`。`offset >= 0x100` 就 OOB 寫入 heap。

這個「相對寫」的優勢：**不需要知道 host 絕對位址**。你只要知道「目標欄位在 `buf` 後面 X bytes」，就能直接蓋到那個欄位。這和 ASLR 完全無關——只要 struct layout 固定，相對 offset 就固定。

**限制**：你寫的值（`data`）是 64-bit 數字，從 guest 端來的——你能完全控制它的值，只要知道要填什麼就行。如果你要填一個 function pointer，你需要先 leak PIE base（用 OOB read），然後計算 `gadget_addr = pie_base + gadget_offset`。

---

## Tier 2：用 OOB Read Leak 破 ASLR

典型的 leak 路徑（依 struct layout 而定）：

```
OOB read 的 offset → 讀到的值 → 計算 base

buf + 0x??  : MemoryRegion.ops    → QEMU PIE 位址 → pie_base = ops_val - ops_rva
buf + 0x??  : QEMUTimer *         → heap 位址    → heap_base ≈ timer_val - known_offset
buf + 0x??  : MemoryRegion.opaque → device struct → struct_addr（heap VA）
```

一旦拿到 `pie_base`，你知道 QEMU binary 裡每個 gadget、每個函式、每個 `libc` import 的 GOT 地址。一旦拿到 `heap_base`，你知道 heap 上每個物件的精確位址（配合 `pahole` 量測的 offset）。

有了這兩個值，寫 ROP chain 就和 userland heap exploit 完全一樣：用 `ROPgadget` / `pwntools.ROP` 找 gadget，計算 `pie_base + gadget_rva`，填進 payload。

### 一個具體的 leak 序列（未實測，理論預期）

```c
/* guest 端 */
/* 假設 buf+0x1c0 就是 MemoryRegion.ops 的位置（由 pahole 得知 struct layout）*/
#define OPS_OFFSET 0x1c0

uint64_t ops_val = *(volatile uint64_t *)(bar + OPS_OFFSET);
/* ops_val 是 QEMU PIE 的某個位址 */

/* 在 Linux 的 debug QEMU 上，用 nm 或 readelf 找 vuln_mmio_ops 的 RVA */
/* 例如：nm qemu-system-x86_64 | grep vuln_mmio_ops → 0x123456 */
uint64_t pie_base = ops_val - 0x123456ULL;  /* 計算 PIE base */

/* 後續：用 pie_base 計算所有 gadget 位址 */
uint64_t system_plt = pie_base + SYSTEM_PLT_RVA;  /* 從 nm 得知 */
```

---

## Tier 3a：把 DMA 變成「guest 物理記憶體讀寫原語」

DMA 的讀寫原語語義和 MMIO OOB 不同：

- **MMIO OOB** = 對 **host heap** 做相對讀寫（位移以 `buf` 為基準）。
- **DMA** = 對 **guest 物理記憶體（GPA 空間）** 做讀寫（位移以 GPA 0 為基準，可達任意 GPA）。

把 DMA 當原語，典型的用法是：

**讀 guest 任意物理記憶體** → `pci_dma_write(dev, target_gpa, device_buf, len)` 把 device buf 的內容搬到你控制的 GPA（你在 guest 裡 mlock 的頁面），然後 guest 從那個頁面讀。

等等，這個方向反了——更有用的是：

**把資料搬進 guest** = `pci_dma_write(dev, my_gpa, src_buf, len)`：device 把 `src_buf`（device 內部 buffer，你剛剛透過 MMIO 寫入的資料）搬到 guest 物理記憶體的 `my_gpa`。

**配合 OOB 的實際用途**：你用 MMIO OOB read 讀出了 PIE base，但這個 leak 只在 host 端——guest 讀 MMIO 的回傳值直接就是那個值，不需要 DMA 搬運。DMA 的真正價值在另一個方向：

**DMA + OOB write 組合 = 任意內容到 host heap 的任意位置**：你先 MMIO 寫資料進 device 的 `buf`（合法 offset）；然後用 DMA 的 device→guest 方向，把 device buf 搬進 guest 記憶體；或者反過來，設計 DMA 從 guest 讀大塊資料，在 host 端做 `pci_dma_read(dev, evil_gpa, device_buf, huge_len)`，如果 `huge_len` 超出 `device_buf` 大小，就是 host heap 的 OOB write——這是 VENOM 類漏洞的本質。

```
DMA 原語的兩條線：

線 1：讀 guest 任意 GPA → device buf
  guest: 設定 DMA_SRC=任意GPA, DMA_DST=device_buf_offset, DMA_CNT=len, DMA_CMD=from_guest
  host:  pci_dma_read(dev, any_gpa, device_buf, len)
         → 把 guest 任意物理記憶體讀進 device buf

線 2：寫 device buf → guest 任意 GPA
  guest: 設定 DMA_SRC=device_buf_offset, DMA_DST=任意GPA, DMA_CNT=len, DMA_CMD=to_guest
  host:  pci_dma_write(dev, any_gpa, device_buf, len)
         → 把 device buf 寫到 guest 任意物理記憶體

如果 cnt 無上界，線 1 的 pci_dma_read(dev, gpa, device_buf, huge_cnt) 
= device_buf 的 OOB write = host heap overflow
```

---

## Tier 3b：函式指標蓋寫——MMIO 觸發 vs Timer 觸發

有了 OOB write 原語和 leaked base，選擇劫持哪個 function pointer：

### 選項 A：蓋 MemoryRegion.ops

```
OOB write → 把 MemoryRegion.ops 從 真實指標 改成 &fake_ops
fake_ops 在 guest 控制的記憶體（或 heap 上的某個你能寫的地方）
fake_ops.write = gadget_addr

觸發：guest 下次寫 BAR0 的任意 offset
     → QEMU: memory_region_dispatch_write → ops->write(opaque, addr, val, size)
     → 跳到 gadget_addr
```

難點：`fake_ops` 要放在一個你知道 host VA 的地方，且 `MemoryRegionOps` 結構的每個欄位要填正確（`endianness`、`valid.max_access_size` 等，否則 dispatch 邏輯可能在呼叫 `.write` 前就 crash）。

### 選項 B：蓋 QEMUTimer.cb

```
OOB write → 把 QEMUTimer.cb 從 真實 callback 改成 gadget_addr

觸發：等下一個 timer tick（最快 1ms，QEMU_CLOCK_VIRTUAL）
     → QEMU main loop: timerlistgroup_run_timers → timer->cb(timer->opaque)
     → 跳到 gadget_addr
```

優點：觸發不需要 guest 再做任何 MMIO，只要等時間——更簡單，且可以在寫完 payload 後立刻等觸發。
難點：需要 groom 讓 QEMUTimer chunk 緊接 VulnState，OOB 才能碰到它。

Ch 20 會詳細分析所有可劫持指標的位置和觸發方式；Ch 21 會把「蓋到指標→跳到 ROP chain 第一個 gadget」的細節全展開。

---

## 整合：一個完整的原語階梯序列

以下是把所有 tier 串起來的概念流程（未實測，理論預期；實際 exploit 在 Ch 16–21 分章展開）：

```
Step 1：讀 struct layout（host gdb / pahole）
  → 得到 buf_offset、MemoryRegion_ops_offset（相對 struct 起點）
  → 計算 OOB_read_offset = ops_offset - buf_offset

Step 2：OOB Read → Leak
  guest 讀 bar + OOB_read_offset
  → 得到 ops_val（QEMU PIE 位址）
  → pie_base = ops_val - vuln_mmio_ops_rva

Step 3：算出目標位址
  gadget_addr = pie_base + pop_rdi_ret_rva   # 第一個 ROP gadget
  system_plt  = pie_base + system_plt_rva
  binsh_str   = pie_base + binsh_str_rva

Step 4：Groom（如需要）
  透過 guest 動作讓 QEMUTimer 緊接 VulnState

Step 5：OOB Write → 蓋 function pointer
  計算 OOB_write_offset = timer_cb_offset - buf_offset（或直接蓋 ops 指標）
  guest 寫 bar + OOB_write_offset = gadget_addr

Step 6：觸發
  等 timer tick，或 guest 做 MMIO 觸發 ops
  → host 執行 gadget_addr
  → ROP chain 展開（Ch 22）
  → system("/bin/sh") 在 host 執行
```

---

## 底層機制：為什麼 MMIO offset 直接映射到 heap offset

```
guest 寫 bar + X
  ↓
KVM VMEXIT（EPT violation 或 MMIO exit）
  ↓
QEMU: memory_region_dispatch_write(mr, addr=X, data, size, attrs)
  ↓
device callback: vuln_mmio_write(opaque=s, addr=X, data, size)
  ↓
s->buf + X  (直接算術，無任何截斷或掩碼)

如果 X >= sizeof(s->buf)：
  s->buf + X 指向 s 的其他欄位（仍在同一個 malloc chunk 內）
  或超出 malloc chunk 邊界（進入下一個 chunk）
```

這是這個漏洞類型最簡潔的表述：**MMIO 的 `addr` 參數直接被當成 heap 上的相對位移使用，沒有邊界限制**。你從 guest 端控制 MMIO 的 offset，就是在 host heap 上滑動讀寫窗口。

---

## 對比與取捨

| 原語 | 觸發方式 | 讀/寫 | 位址空間 | 需要 groom | 需要 leak |
|------|---------|------|---------|-----------|---------|
| MMIO OOB read | guest 讀 BAR0 | 讀 | host heap（相對） | 不需要 | 不需要（得到 leak）|
| MMIO OOB write | guest 寫 BAR0 | 寫 | host heap（相對） | 視 struct layout | 需要（填值要算 base）|
| DMA read（無 bound check）| 設定 DMA 暫存器 + 觸發 | 寫（向 device buf 寫）| guest GPA 空間 | 不需要 | 不需要 |
| DMA write（無 bound check）| 設定 DMA 暫存器 + 觸發 | 讀（從 device buf 讀）| guest GPA 空間 | 不需要 | 不需要 |
| DMA cnt OOB | 設定大 cnt + 觸發 DMA | 寫（host heap）| host heap | 視佈局 | 需要 |

---

## 踩雷集錦

**錯誤直覺 1：有 OOB read 就夠了，不需要特別 leak function pointer**

不夠。你讀到的是原始 heap 資料，可能是任何東西。你需要知道「這個偏移量的值是什麼語義」（函式指標？heap 指標？大小字段？），才能用它計算 base。不理解 struct layout，讀出來的數字毫無意義。`pahole` + gdb 是你讀這些值的前置工作。

**錯誤直覺 2：OOB write 可以一次蓋到任意遠的地方**

不一定。MMIO 的一次 write callback 通常是 1/2/4/8 bytes（`size` 參數決定），你每次呼叫只寫一個 slot。要蓋到很遠的地方需要多次寫、不同 offset，整個過程不是原子的——中間 QEMU 仍在跑其他 event，heap 狀態可能變化。設計 payload 時要考慮原子性和競爭條件。

**錯誤直覺 3：DMA 原語和 MMIO 原語能做的事是一樣的**

差異很大。MMIO OOB 操作的是 **host heap 的相對位置**（相對於 device struct）；DMA 操作的是 **guest 物理記憶體**（可以是任意 GPA）。DMA 更常被用來「從 device buf 搬資料到 guest 的已知位址」（讓 guest 能讀到 leak），或「把 guest 的大塊資料送進 device buf，造成 OOB」。兩者組合才是完整武器庫。

**錯誤直覺 4：蓋到 MemoryRegion.ops 指標後，下一個 MMIO 一定能觸發**

不一定立刻觸發。QEMU 的 MMIO dispatch 在呼叫 `.read`/`.write` 之前，會先通過 `memory_region_access_valid` 驗證（`ops->valid.min_access_size` / `max_access_size`）。如果你的偽 `MemoryRegionOps` 的這些欄位是 0 或無效值，dispatch 可能直接跳過 callback 或 log error。偽造 ops 時需要填合理的 valid 欄位。

**錯誤直覺 5：Tier 0→1→2→3 一定按這個順序**

現實的 exploit 未必。有些洞直接給你任意寫（跳過 read），你用別的方式 leak（例如利用 QEMU 的 `-monitor` 或 `/proc/qemu/maps` 側路）。有些洞先給你 DMA OOB（直接 heap OOB write），不需要 MMIO read。把原語階梯看成工具箱，根據具體 bug 選用組合，不要死記順序。

---

## 進階：再往深一層

### 多次 OOB write 的 atomicity 問題

一個完整的 ROP chain 可能有 10–20 個 qword（`gadget_addr`、`arg1`、`arg2`……），你需要多次 MMIO write 才能全填進去。在這 N 次 write 之間，如果 QEMU 發生 timer 觸發、IRQ 處理，而你的 payload 還寫到一半（ops 指標已蓋但 ROP chain 未完整），就可能在錯誤狀態下觸發控制流。

解法：設計一個「安全的中間態」——先把假 ops 的 `.read`/`.write` 指向一個只做 `return` 的 gadget（NOP），讓中間觸發不造成傷害；等整個 payload 填好後，最後一步才把 `.write` 改成真正的 ROP 入口。

### Relative Write → Absolute Write 的升級

有了 MMIO 相對寫，如果你想蓋到 heap 上任意絕對位址（而不是相對 struct 的固定 offset）——例如蓋掉一個你 leak 出來的 heap 指標指向的物件——你需要：

1. 用相對寫把某個指標（例如 `MemoryRegion.opaque`，它的值是 device struct 的位址）改成你的目標絕對位址。
2. 之後 device callback 用 `opaque` 做操作時，它在你選的絕對位址上做——這就升級成了有限的「任意寫」。

這是 Ch 21 會詳細展開的「偽造物件」技術。

### 與 browser_pwn 的對比

如果你同時在學 `browser_pwn`（V8 那條線），可以對比：

| | VM escape（QEMU）| Browser（V8）|
|---|---|---|
| 讀原語 | MMIO OOB read | `ArrayBuffer` length confusion |
| 寫原語 | MMIO OOB write / DMA | `ArrayBuffer` write OOB |
| Function pointer | MemoryRegionOps、QEMUTimer.cb | JSFunction code pointer、wasm table |
| Groom | guest MMIO 動作 | JS 分配特定大小物件 |
| Heap | glibc ptmalloc | V8 heap（partition alloc / Orinoco）|

兩條線的哲學完全相同：讀 → leak base → 寫 → 劫持 function pointer → ROP/shellcode。具體 API 和物件不同而已。

---

## 動手練習

（以下步驟需在 Linux 環境，搭配 Ch 12 的自訂 vuln device）

1. 用 `pahole` 和 gdb 確認 `VulnState.buf` 後面第一個指標型欄位的 offset（hint：可能是 `MemoryRegion.ops` 或 `QEMUTimer *`）。

2. 在 guest 寫一個 C 程式，對 `bar + confirmed_offset` 做 MMIO read，把讀出的值用十六進位 print 出來。在 host gdb 驗證：讀出的值和 `print *(uint64_t *)(s->buf + confirmed_offset)` 是否一致。

3. 在 host 用 `readelf -s qemu-system-x86_64 | grep vuln_mmio_ops`（或 `nm`）找 `vuln_mmio_ops` 的 RVA，計算 `pie_base = leaked_val - RVA`。用 gdb `info proc mappings` 驗證算出的 base 是否在 QEMU binary 的映射範圍內。

4. 用 OOB write 把 `buf + some_offset` 蓋成 `0xdeadbeef00000000`，在 gdb `print` 確認蓋入成功，然後嘗試觸發那個欄位（讀 MMIO 或等 timer），觀察 QEMU 的行為（crash？SIGSEGV？gdb 在哪裡停？）。

5. 整理一份你自己的「原語對照表」：列出 vuln device 每個可讀/可寫的 struct 欄位、它的 offset、它的語義、以及用它能做什麼。

---

## 本章重點整理

- 原語階梯：MMIO 基本讀寫 → MMIO OOB（heap 相對讀寫）→ leak（破 ASLR）→ 任意寫（function pointer 蓋寫）→ 控制流。
- MMIO OOB 操作的是 **host heap**（相對於 device struct 的 offset）；DMA 操作的是 **guest 物理記憶體**（GPA 空間）。
- OOB read 的首要目的是 leak：讀出 `MemoryRegion.ops`（PIE 位址）或 heap 指標，算出 ASLR base。
- OOB write 的首要目的是蓋 function pointer：`MemoryRegion.ops`（下次 MMIO 觸發）或 `QEMUTimer.cb`（下次 timer tick 觸發）。
- 多次 OOB write 的原子性問題：需設計安全中間態，避免 payload 半寫狀態下觸發。
- Part 3（Ch 16–21）會把每一個 tier 的實作細節完整展開。

---

## 自我檢核

- [ ] 我能不看筆記，從 Tier 0 到 Tier 4 畫出完整的原語階梯，說明每一層的輸入和輸出。
- [ ] 我能解釋為什麼「MMIO offset 直接映射到 heap offset」——從 guest MMIO write 到 `s->buf + addr` 的完整呼叫路徑。
- [ ] 我能說出 MMIO OOB 和 DMA OOB 的本質差異（它們分別操作哪個位址空間）。
- [ ] 我能說出偽造 `MemoryRegionOps` 時至少需要填哪些欄位，才能讓 QEMU 正確呼叫 `.write` callback。
- [ ] 我能解釋「多次 OOB write 的原子性問題」，並說出一種緩解策略。

---

## 延伸閱讀

1. **Practice B（本課練習 B）**：[practice-b-pci-device-oob.md](./practice-b-pci-device-oob.md)
   讀哪裡：完整作業題，從 guest 觸發 Ch 12 vuln device 的 OOB，含 gdb 觀察步驟。
   學什麼：把本章概念從「理解」落地到「動手做」，沒有比直接觸發一次 OOB 更好的固化方式。
   關聯：本章是 Practice B 的理論基礎，做完 Practice B 後本章的每個 tier 都應該能對號入座。

2. **CTFtime QEMU escape writeup 集**（搜尋 "QEMU escape CTF writeup" 篩 2020–2024）
   讀哪裡：找到有明確「Step 1: leak via MMIO OOB read → Step 2: write ops → Step 3: trigger」結構的 writeup。
   學什麼：真實 CTF 環境的原語序列——比本課的抽象描述更具體，各題 struct layout 不同，看多幾篇才有感覺。
   關聯：Ch 16–21 的前置視角；做 Practice C 之前最好先讀 2–3 篇。

3. **"A New Class of Vulnerability" — DMA Reentrancy（Alexander Bulekov, Security 2022）**
   讀哪裡：paper 的 Section 3「DMA Reentrancy」和 Section 5「Exploitation」。
   學什麼：DMA 作為 host 記憶體讀寫原語的最嚴格學術分析，包含 MMIO 和 DMA 交互觸發的複雜場景。
   關聯：本章「DMA 原語」一節的深度延伸，以及 Ch 19 UAF 的關聯背景。

4. **pwntools ROP 模組文件**（`docs.pwntools.com`，搜尋「ROP」）
   讀哪裡：`ROP()` 物件的 `chain()`、`gadgets` 屬性、`find_gadget()` 方法。
   學什麼：本章 Tier 3 往後需要的 ROP gadget 搜尋和 chain 構建，pwntools 比手動 ROPgadget 更方便。
   關聯：Ch 22 ROP in QEMU 的核心工具；現在看一遍，做 Practice C 時就不陌生。

5. **"VENOM" CVE-2015-3456 技術報告**（CrowdStrike, 2015）+ **QEMU git patch commit**
   讀哪裡：報告的 FDC DMA buffer overflow 章節；git log 找「CVE-2015-3456」的 fix commit，看一行 `if` 的增減。
   學什麼：DMA cnt 未驗證 → host heap OOB write 的第一個廣為人知的真實案例。把本章的「DMA cnt OOB」那一欄對照到真實 CVE。
   關聯：Ch 23 VENOM 復刻的必讀前置；理解了本章再看 Ch 23 會快非常多。

---

> 原語階梯建好了。接下來 Part 3 從 Ch 16 開始，帶你拿著真實的 vuln device，一步一步把「有洞」變成「能跑 `/bin/sh` 在 host 上」。

→ [Ch 16](./16-first-mmio-oob.md)
