# 練習 A — trace kmalloc/kfree 的 slab 行為 + 寫 slab 觀測 module

> **目標**：把 Ch 6–9 學到的東西拼起來，親手寫一個 kernel module，實際觀測 SLUB 的行為——配置/釋放不同大小的物件、印出位址、觀察 freelist 的 LIFO 特性、對照 `/proc/slabinfo` 的數字。完成後你要能親口說出（不是背出來）：「我自己證實了 kfree 後下一個同大小 kmalloc 拿到同一塊記憶體」，以及「我知道怎麼確認一個 kmalloc size 落在哪個 cache」。

## 背景與動機

這個練習模擬的情境，是任何 kernel exploit 開發者在拿到一個新目標、新版本 kernel 時都要做的第一件事：**在開始構造任何 exploit 之前，先用一個完全無害、你自己完全控制的 module，確認目標 kernel 的 allocator 行為是否符合你的理論假設**。寫 exploit 時盲目相信「教科書上寫的行為」，碰到版本差異、config 差異，往往會卡在「明明照理論寫對了，卻打不中」的困境——這時候回頭用這種觀測 module 驗證基本假設,往往比繼續猜測 exploit 邏輯更快找到問題。

這個練習也是整本書第一次要求你完整寫一個 kernel module、編譯、載入、觀察 `dmesg` 輸出並對照 `/proc/slabinfo`——如果 Part 0 的環境還有任何沒搞定的地方，這是發現它的好時機。

## 任務規格

寫一個 kernel module `slab_trace.ko`，提供一個 `/proc` 介面（或直接在 `module_init` 時跑完觀測並印到 `dmesg`，兩種都可以，下面以後者為主，較簡單），完成以下觀測項目：

| 項目 | 要做的事 | 驗收標準 |
|---|---|---|
| 1. size → cache 對照 | 對 8、16、33、65、200、5000 這幾個 size 呼叫 `kmalloc()`，印出回傳位址 | 能用位址推斷（或額外用 `kmalloc_size(ptr)` 一類介面確認）各自落在哪個 size class |
| 2. LIFO 驗證 | 連續配置 5 個同大小物件，釋放中間一個，再配置一次，比較位址 | 新配置的位址要等於剛釋放的那個 |
| 3. 連續配置的位址間距 | 連續配置 N 個同大小物件，計算相鄰位址差 | 間距應該等於該 size class 的實際 object 大小（不是你要求的原始大小） |
| 4. 跨 slab 邊界 | 配置足夠多物件（超過一塊 slab 能裝的量），觀察位址是否出現「非固定間距」的跳躍 | 至少觀察到一次跳躍，說明跨到了不同的 slab |
| 5. 對照 `/proc/slabinfo` | module 跑完後，手動在 shell 讀 `/proc/slabinfo` 對應的 cache 行 | 數字（`active_objs`、`objsize` 等）要能解釋你 module 裡觀察到的行為 |

### 限制

- 只能用 `kmalloc()` / `kfree()`（不要用 `kmem_cache_create()` 自訂 cache，這個練習聚焦在通用 `kmalloc-N` 系列）。
- 全程在 `module_init` 做完，`module_exit` 只負責清理（如果你選擇留著任何指標沒釋放，在 exit 時要全部釋放，避免真的 leak 記憶體到你的測試 VM 裡）。
- 用 `pr_info()` 印出所有觀測結果，方便之後用 `dmesg` 查閱。

## 期望輸出範例

```
輸入：insmod slab_trace.ko
輸出（dmesg | tail -n 30，數字為示意值）：
[  123.456] slab_trace: ---- size -> cache 對照 ----
[  123.456] slab_trace: kmalloc(8)    -> ffff888012340000
[  123.456] slab_trace: kmalloc(16)   -> ffff888012340020
[  123.456] slab_trace: kmalloc(33)   -> ffff888012341000
[  123.456] slab_trace: kmalloc(65)   -> ffff888012342000
[  123.456] slab_trace: kmalloc(200)  -> ffff888012343000
[  123.456] slab_trace: kmalloc(5000) -> ffff888087650000
[  123.456] slab_trace: ---- LIFO 驗證 ----
[  123.456] slab_trace: a=ffff888012344000 b=ffff888012344040 c=ffff888012344080 d=ffff888012344100 e=ffff888012344140
[  123.456] slab_trace: kfree(c)
[  123.456] slab_trace: new kmalloc -> f=ffff888012344080 (expect == c): MATCH
[  123.456] slab_trace: ---- 跨 slab 邊界觀測 ----
[  123.456] slab_trace: allocated 2000 objects of size 64, observed 3 non-64-byte jumps
```

```
邊界情況：在記憶體壓力很高的系統上跑這個 module
輸出：部分 kmalloc() 可能回傳 NULL（GFP_KERNEL 配置失敗，
      理論上在正常測試 VM 不該發生，除非你故意把 VM 記憶體調得很小）
預期行為：module 要檢查每個 kmalloc 的回傳值，NULL 時要印警告並安全中止，
         不能直接解參考 NULL（否則會讓你的測試 VM kernel panic）
```

## 如果你卡住了

1. **編不出來 / `modules` 目錄找不到**：先確認 Part 0（Ch 0 環境搭建）的 kernel headers / build 目錄是否正確設置，`uname -r` 對應的 `/lib/modules/$(uname -r)/build` 要存在且指向你實際跑的 kernel。
2. **`dmesg` 裡看不到你的 `pr_info`**：檢查 `dmesg` 的 log level 過濾（`dmesg -n 8` 調高顯示等級），或確認 `insmod` 是否真的成功（`lsmod | grep slab_trace`）。
3. **LIFO 驗證沒有如預期命中（新配置位址 != 剛釋放的位址）**：想一想「同一個 CPU」這個前提——如果你的 module 程式碼在兩次 `kmalloc` 之間被排程到了別的 CPU（多核心 QEMU 下可能發生），per-cpu 的 fast path freelist 就不是同一份了。嘗試在單核 QEMU（`-smp 1`）上重跑，或在程式碼裡用 `preempt_disable()` / `get_cpu()` 包住這段觀測（注意要正確配對 `put_cpu()`，否則會讓系統長時間停用搶占）。
4. **跨 slab 邊界觀測完全沒看到跳躍**：你配置的物件數量可能還不夠多，想一想一塊 slab 大概能裝多少個你選擇大小的物件（參考 `/proc/slabinfo` 的 `objperslab` 欄位），把配置數量調到明顯超過這個值的好幾倍。
5. **`/proc/slabinfo` 裡找不到你預期的 cache 名字**：記住 Ch 8 強調過的——96、192 這類特例尺寸是否存在依版本而異，直接用 `grep kmalloc /proc/slabinfo` 列出你這台機器實際有的所有 cache 名稱，用實際存在的名字反推你的 size 落在哪裡，不要假設書上的表格在你的機器上一定分毫不差。

## 實作步驟建議

### Step 1：骨架與基本 I/O

先寫一個最小的 module，`module_init` 裡只做一次 `kmalloc(64, GFP_KERNEL)` 並印出位址，確認編譯、載入、`dmesg` 輸出的流程全部正常。

### Step 2：size → cache 對照觀測

擴充成對一組不同大小（8/16/33/65/200/5000）各配置一次，分別印出位址。先不用急著解讀，能印出來就是這一步的目標。

### Step 3：LIFO 驗證邏輯

配置 5 個同大小物件存進陣列，`kfree()` 中間一個，再配置一次，比較位址是否相等，印出 `MATCH` 或 `MISMATCH`。這一步建議先用 `get_cpu()`/`put_cpu()` 包住，減少被排程到別的 CPU 的干擾（細節見下方參考解答）。

### Step 4：間距與跨 slab 觀測

配置一個足夠大的陣列（例如 2000 個 64-byte 物件），用迴圈計算相鄰位址差，統計「差值不等於 64」的次數。這一步要注意：大量配置會占用不少記憶體，`module_exit`（或配置失敗時的清理路徑）一定要把每一個都 `kfree()` 乾淨。

### Step 5：整合、清理、對照 `/proc/slabinfo`

把前面幾步串成一個完整的 `module_init`，所有配置的指標在流程結束前都要釋放（除了你故意要留著觀察的那幾個，也要在 `module_exit` 時釋放）。跑完後手動 `cat /proc/slabinfo | grep kmalloc` 對照。

## 完整參考解答

**寫完再看！不要偷看**，否則學不到東西。

<details>
<summary>點開參考實作</summary>

```c
// slab_trace.c
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/slab.h>
#include <linux/smp.h>

#define BIG_COUNT 2000

static void *size_probe_ptrs[6];
static const size_t sizes[] = { 8, 16, 33, 65, 200, 5000 };

static void do_size_cache_probe(void)
{
    int i;

    pr_info("slab_trace: ---- size -> cache 對照 ----\n");
    for (i = 0; i < ARRAY_SIZE(sizes); i++) {
        size_probe_ptrs[i] = kmalloc(sizes[i], GFP_KERNEL);
        if (!size_probe_ptrs[i]) {
            pr_warn("slab_trace: kmalloc(%zu) failed\n", sizes[i]);
            continue;
        }
        pr_info("slab_trace: kmalloc(%-5zu) -> %px\n",
                sizes[i], size_probe_ptrs[i]);
    }
}

static void undo_size_cache_probe(void)
{
    int i;
    for (i = 0; i < ARRAY_SIZE(sizes); i++) {
        kfree(size_probe_ptrs[i]);
        size_probe_ptrs[i] = NULL;
    }
}

static void do_lifo_probe(void)
{
    void *a, *b, *c, *d, *e, *f;
    int cpu;

    pr_info("slab_trace: ---- LIFO 驗證 ----\n");

    /* 盡量把這段觀測釘在同一個 CPU 上執行，減少被排程打斷、
       落到別的 CPU 的 per-cpu freelist 導致觀測失真的機率。
       注意：get_cpu() 會停用搶占（preempt disable），
       段落要短，配對的 put_cpu() 絕對不能漏掉。 */
    cpu = get_cpu();

    a = kmalloc(64, GFP_KERNEL);
    b = kmalloc(64, GFP_KERNEL);
    c = kmalloc(64, GFP_KERNEL);
    d = kmalloc(64, GFP_KERNEL);
    e = kmalloc(64, GFP_KERNEL);

    pr_info("slab_trace: a=%px b=%px c=%px d=%px e=%px (cpu=%d)\n",
            a, b, c, d, e, cpu);

    if (!a || !b || !c || !d || !e) {
        pr_warn("slab_trace: LIFO probe allocation failed, skipping\n");
        put_cpu();
        goto cleanup;
    }

    kfree(c);
    f = kmalloc(64, GFP_KERNEL);
    pr_info("slab_trace: kfree(c); new kmalloc -> f=%px (expect == c=%px): %s\n",
            f, c, (f == c) ? "MATCH" : "MISMATCH");

    put_cpu();

    kfree(f);
cleanup:
    kfree(a);
    kfree(b);
    kfree(d);
    kfree(e);
}

static void do_spacing_and_boundary_probe(void)
{
    void **ptrs;
    int i, jumps = 0;
    const size_t obj_size = 64;

    pr_info("slab_trace: ---- 間距與跨 slab 邊界觀測 ----\n");

    ptrs = kmalloc_array(BIG_COUNT, sizeof(void *), GFP_KERNEL);
    if (!ptrs) {
        pr_warn("slab_trace: failed to allocate tracking array\n");
        return;
    }

    for (i = 0; i < BIG_COUNT; i++) {
        ptrs[i] = kmalloc(obj_size, GFP_KERNEL);
        if (!ptrs[i]) {
            pr_warn("slab_trace: kmalloc #%d failed, stopping early\n", i);
            break;
        }
    }

    for (i = 1; i < BIG_COUNT && ptrs[i] && ptrs[i - 1]; i++) {
        long diff = (long)ptrs[i] - (long)ptrs[i - 1];
        if (diff != (long)obj_size && diff != -(long)obj_size)
            jumps++;
    }

    pr_info("slab_trace: allocated up to %d objects of size %zu, observed %d non-%zu-byte jumps\n",
            BIG_COUNT, obj_size, jumps, obj_size);

    for (i = 0; i < BIG_COUNT; i++)
        kfree(ptrs[i]);
    kfree(ptrs);
}

static int __init slab_trace_init(void)
{
    pr_info("slab_trace: module loaded, starting observation\n");

    do_size_cache_probe();
    do_lifo_probe();
    do_spacing_and_boundary_probe();

    undo_size_cache_probe();

    pr_info("slab_trace: observation complete, see analysis with "
            "`cat /proc/slabinfo | grep kmalloc`\n");
    return 0;
}

static void __exit slab_trace_exit(void)
{
    pr_info("slab_trace: unloaded\n");
}

module_init(slab_trace_init);
module_exit(slab_trace_exit);

MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Ch6-9 practice: observe SLUB allocator behavior");
MODULE_AUTHOR("kernel_pwn course");
```

```makefile
# Makefile
obj-m += slab_trace.o

KDIR ?= /lib/modules/$(shell uname -r)/build

all:
	$(MAKE) -C $(KDIR) M=$(PWD) modules

clean:
	$(MAKE) -C $(KDIR) M=$(PWD) clean

load:
	sudo insmod slab_trace.ko

unload:
	sudo rmmod slab_trace

log:
	dmesg | grep slab_trace | tail -n 40
```

**解答說明**：

- `do_lifo_probe()` 裡用 `get_cpu()`/`put_cpu()` 把關鍵的「釋放後立刻重新配置」這段釘在同一個 CPU 上，這是呼應 Ch 8「per-cpu fast path」機制的直接應用——LIFO 行為的保證範圍本來就是「同一個 CPU 的 freelist」，觀測時不處理這一點，在多核心機器上很容易得到 `MISMATCH` 而誤以為理論錯了。
- `do_spacing_and_boundary_probe()` 故意配置 2000 個物件而不是幾十個，是因為一塊 order-0 的 kmalloc-64 slab 通常只能裝幾十個 object（實際數字看你機器的 `/proc/slabinfo` 的 `objperslab`），配置量不夠大根本不會跨到第二塊 slab，觀測不到任何跳躍。
- 所有配置路徑都檢查了 `NULL` 回傳值——這不是防禦性程式設計的裝飾，是因為一旦某次 `kmalloc` 真的失敗又沒檢查，後面對 `NULL` 解參考會直接讓你的測試 VM kernel panic，徒增除錯麻煩。
- `size_probe_ptrs` 的 6 個指標故意留到 `do_size_cache_probe()` 跑完、觀測完位址之後才統一在 `undo_size_cache_probe()` 釋放——這是為了讓你有機會在它們還「活著」的時候用額外工具（例如 crash/drgn，如果你的環境有裝）去對照它們實際屬於哪個 `kmem_cache`。

</details>

> **未實測，理論預期行為**：以上 module 在本環境無法編譯、載入、執行驗證。所有 `dmesg` 輸出範例為根據 Ch 8 / Ch 9 機制推演出的**理論預期行為**，不是真實擷取的輸出。驗證方式：在你自己的 QEMU（Linux 6.6 LTS 或你手邊能跑的版本，x86-64）上完整走一次 `make && insmod slab_trace.ko && dmesg | grep slab_trace`，並與 `cat /proc/slabinfo | grep kmalloc` 的結果交叉比對。

## 測試用例

| 情境 | 預期結果 | 說明 |
|---|---|---|
| 正常單核 QEMU（`-smp 1`），記憶體充裕 | LIFO 驗證顯示 `MATCH`；間距觀測有少量（但非零）跳躍 | 符合 Ch 8/9 理論 |
| 多核 QEMU（`-smp 4` 以上），LIFO 驗證沒有用 `get_cpu()` 保護 | 偶爾出現 `MISMATCH` | 驗證「per-cpu fast path」的邊界——LIFO 保證只在同一 CPU 上近似成立 |
| 故意把 `sizes[]` 其中一項改成 0 | `kmalloc(0, ...)` 的行為需要你自己查證（部分版本視為合法呼叫並回傳一個特殊非 NULL 指標，不保證跨版本一致） | 邊界輸入；**未實測，理論預期行為**，請在你自己的環境實測後更新這一條的結論 |
| 刻意把 `BIG_COUNT` 調到遠超過系統可用記憶體 | 某個 `kmalloc` 開始回傳 `NULL`，module 應該安全中止並清理已配置的部分 | 驗證錯誤處理路徑，而不是讓它直接造成 kernel panic |
| 載入後不卸載，直接重複 `insmod`（先 `rmmod` 一次） | 每次都應該看到新的位址（不同次載入，slab 狀態不保證延續） | 驗證你的 module 沒有殘留任何跨載入週期的全域狀態造成 double free |

## 分析：你觀察到的數字，可能被這些因素影響

跑完這個練習之後，如果你的輸出和「期望輸出範例」不完全一致，不要馬上假設自己的程式碼有錯——先排查這幾個環境變因，它們全部呼應 Ch 6–9 講過的機制：

1. **CPU 核心數與排程**：Ch 8 強調過，fast path 的 LIFO 保證只在「同一個 CPU」範圍內近似成立。如果你的 QEMU 開了多核心，而 `do_lifo_probe()` 裡的 `get_cpu()`/`put_cpu()` 保護沒有正確包住整段配置-釋放-再配置的邏輯，你可能在某次重跑時看到 `MISMATCH`——這不代表 SLUB 的行為和書上講的不一樣，代表這次觀測被排程打斷了。多跑幾次、統計 `MATCH` 的比例，比單次結果更能說明問題。

2. **系統目前的記憶體壓力與其他程序的配置行為**：你的 QEMU 如果同時跑著別的服務，它們也在呼叫 `kmalloc()`，可能使用同一個 kmalloc-64 cache。這代表「連續配置的位址間距」這項觀測,理論上不是「絕對乾淨」的——如果恰好有另一個不相關的程序在你兩次配置之間插入一次 kmalloc-64 配置,拿走了你以為會輪到你的那個格子,你看到的位址會和預期不同。這正是為什麼真實的 heap grooming（Ch 20）要花這麼多力氣去「清空」或「控制」系統狀態，而不是天真地假設自己是系統裡唯一在配置記憶體的人。

3. **是否開啟 `CONFIG_SLUB_DEBUG` 相關的 `slub_debug` 開機參數**：如果你的測試 kernel 開著 redzone/poison（Ch 9），物件之間可能被插入額外的檢查用記憶體，你觀測到的「相鄰位址間距」可能不是單純的 object size，而是 object size 加上 debug metadata 的額外開銷。用 `cat /proc/cmdline` 確認你的 kernel 開機參數，如果看到 `slub_debug=...`，這個因素要納入解讀。

4. **編譯器與 kernel 版本對 inline 函式的處理**：本練習的觀測函式都是直接呼叫 `kmalloc`/`kfree`，理論上不受編譯器優化影響太多，但如果你在非常舊或非常新的 kernel 版本上重做這個練習，size → cache 對照表（Ch 8 提過 96/192 特例尺寸的版本差異）可能和本書寫的不同，這是預期中的版本差異，不是你的程式碼錯了。

## 延伸挑戰（加分）

- 把 `do_size_cache_probe()` 擴充成自動判斷「這個 size 對應的 cache 名稱」——可以透過比較位址是否落在你另外用 `kmem_cache_size()`（如果你的 kernel 版本有匯出這類介面）取得的已知 cache 範圍內，或單純用這個練習逼自己去讀 `mm/slab_common.c` 裡 `kmalloc_slab()` 的實際邏輯，自己在 module 裡重算一次它會選哪個 cache。
- 加一個 `GFP_KERNEL_ACCOUNT` 版本的觀測——同樣的 size，一半用 `GFP_KERNEL`、一半用 `GFP_KERNEL_ACCOUNT` 配置，驗證 Ch 9 講的「兩者落在不同 cache」是否在你的系統上成立（提示：如果系統沒開 `CONFIG_MEMCG_KMEM`，兩者可能根本沒有差異，這也是一個值得你親自確認的版本依賴行為）。
- 做一個「跨 kmalloc 大小」的交叉 LIFO 測試：配置一個 kmalloc-64 物件、一個 kmalloc-128 物件，交替釋放與配置，驗證它們是否真的完全隔離（釋放 64 的物件，不會被 128 的配置請求拿走）。
- 如果你的環境有 `crash` 或 `drgn` 工具可用，嘗試在 module 跑到一半（例如用 `msleep()` 卡住）時，從另一個 shell 用這些工具檢視目標 slab 的即時狀態，對照你 module 自己印出的數字。

## 自我檢核

- [ ] 我親手編譯、載入過這個（或自己寫的等價）module，並且看過真實的 `dmesg` 輸出，不是只讀過本文的「期望輸出範例」。
- [ ] 我能解釋為什麼 LIFO 驗證要注意「同一個 CPU」這個條件，並且知道哪個 kernel API 可以幫助控制這一點。
- [ ] 我能對照 `/proc/slabinfo` 的欄位，說出我的觀測數字和哪個欄位有對應關係。
- [ ] 我能說出自己的實作和參考解答在「錯誤處理」或「觀測粒度」上的差異，並解釋各自的取捨（例如：我有沒有檢查每一次 kmalloc 的回傳值？我的跨 slab 觀測量夠不夠大？）。

完成這個練習，你已經親手驗證了 Part 1 全部的核心假設——buddy allocator 給 SLUB 連續頁面、SLUB 用固定 size class 切物件、freelist 的 LIFO 行為決定了「UAF 幾乎等於免費重拿」這個 Part 3 會反覆用到的核心機制。接下來進入 Part 2，開始建立完整的漏洞類型學，再把這些記憶體管理知識套進真正的漏洞利用場景。

→ [Ch 10 kernel 漏洞類型學](./10-vulnerability-taxonomy.md)
