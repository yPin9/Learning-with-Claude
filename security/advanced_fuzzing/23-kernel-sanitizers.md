# Ch 23 — KASAN/KMSAN/KCSAN 當 oracle

> **目標**：理解 KASAN、KMSAN、KCSAN、KFENCE、UBSAN 各自偵測什麼類型的 bug，會輸出什麼樣的報告，以及在 kernel fuzzing 工作流裡它們扮演「oracle」的角色。讀完能看懂一份真實的 KASAN splat，知道從哪幾行定位 root cause。

## oracle 的概念：fuzzer 怎麼知道「找到 bug 了」？

在 userland fuzzing 裡，這個問題的答案很直接：程式 crash 了（SIGSEGV），或者 ASAN 輸出了 ERROR。

Kernel fuzzing 的難點是：很多記憶體錯誤在 kernel 裡**不產生立即的 crash**。一個 heap out-of-bounds write 可能只是破壞了旁邊的資料，kernel 繼續運行幾秒鐘後因為不相關的原因才 panic，或者根本不 panic——這個錯誤就永遠沒被偵測到。

這是「oracle 問題」：你需要一個機制，能在 bug 發生的瞬間（而不是幾秒後）偵測到它，並且給出有用的診斷資訊。

Kernel sanitizer 家族（KASAN / KMSAN / KCSAN / KFENCE / UBSAN）就是 kernel fuzzing 的 oracle。它們在 kernel 裡插入額外的檢查，一旦偵測到可疑操作，立即輸出詳細報告並（通常）觸發 panic。

```
syscall 序列
     │
     ▼
kernel 執行路徑
     │
     ├── 正常路徑 ──→ 正常回傳
     │
     └── 觸發 bug ──→ Sanitizer 攔截
                            │
                       ┌────▼───────────────────────┐
                       │  BUG: KASAN: use-after-free │
                       │  Read at addr 0xffff... by  │
                       │  task fuzzer/1234           │
                       │  [stack trace]              │
                       └────────────────────────────┘
                            │
                       kernel panic / warn
                            │
                       syz-manager 收到 crash log
                            │
                       存入 crash DB
```

沒有這些 sanitizer，大量的記憶體錯誤會靜默通過，fuzzer 只能靠最終的 panic 發現問題，那時候 root cause 早就被稀釋掉了。

## KASAN：記憶體安全 oracle

### 機制：shadow memory

KASAN（Kernel Address Sanitizer）使用 **shadow memory** 技術。每 8 bytes 的 kernel 記憶體，對應 1 byte 的 shadow memory：

```
kernel heap（被監控的記憶體）
0xffff888001000000  [8 bytes] → shadow[0] = 0x00  (全部可存取)
0xffff888001000008  [8 bytes] → shadow[1] = 0x04  (前4 byte 可存取，後4 byte 是紅區)
0xffff888001000010  [8 bytes] → shadow[2] = 0xfa  (全部是紅區，heap 紅區)
0xffff888001000018  [8 bytes] → shadow[3] = 0xfb  (redzone，kmalloc 的 padding)
```

Shadow byte 的值編碼：
- `0x00`：全部 8 bytes 可存取
- `1–7`：前 N bytes 可存取，後面的不行（適用於奇數大小的 allocation）
- `0xfa`：heap 左紅區（kasan redzone before object）
- `0xfb`：heap 右紅區（kasan redzone after object）
- `0xfd`：stack 左/右紅區
- `0xfe`：freed memory（UAF 偵測）

每次 kernel 執行 load/store 操作，KASAN 插入的 inline check 都先查 shadow：

```c
/* KASAN 插入的 inline check（Compile-time 插樁）*/
/* 對應一次 4-byte read 的檢查 */
void kasan_check_read(const void *addr, size_t size) {
    u8 shadow = *(u8 *)kasan_mem_to_shadow(addr);
    if (shadow != 0 && shadow < size) {
        kasan_report(addr, size, false, _RET_IP_);
    }
}
```

這個 check 非常輕量（一次記憶體讀 + 一次比較），但能在問題發生的瞬間抓到。

### KASAN 能抓的 bug 類型

| Bug 類型 | 觸發條件 | 報告關鍵字 |
|---------|---------|----------|
| Heap OOB read | 讀超過 allocation 邊界 | `out-of-bounds read` |
| Heap OOB write | 寫超過 allocation 邊界 | `out-of-bounds write` |
| Heap UAF read | 讀已 free 的記憶體 | `use-after-free read` |
| Heap UAF write | 寫已 free 的記憶體 | `use-after-free write` |
| Stack OOB read/write | 超出 stack variable 邊界 | `stack-out-of-bounds` |
| Global OOB | 超出全域變數邊界 | `global-out-of-bounds` |

### Generic KASAN vs SW_TAGS vs HW_TAGS

KASAN 有三種模式：

- **Generic KASAN**：純軟體，shadow memory 開銷約 1/8（每 8 bytes kernel 記憶體需要 1 byte shadow），執行速度慢約 2–3×，但能在任何 x86 或 arm64 上跑。syzkaller 用這個。
- **SW_TAGS KASAN**：用 arm64 TBI（Top-Byte Ignore）的上位 byte 存 tag，shadow 開銷較小，只在 arm64 上有。
- **HW_TAGS KASAN**：利用 arm64 MTE（Memory Tagging Extension）硬體，幾乎零額外開銷，需要 ARMv8.5 以上硬體。

## 真實 KASAN splat 解讀

以下是一個真實 KASAN 報告範例（基於 CVE-2022-1048，`snd_pcm_hw_refine_user()` 的 UAF 簡化版）。這是在 KASAN kernel 上跑 syzkaller 時會看到的格式：

```
==================================================================
BUG: KASAN: use-after-free in snd_pcm_hw_params_user+0x3f2/0x500 [snd_pcm]
Write of size 4 at addr ffff888079b24e10 by task syz-executor.0/1847

CPU: 1 PID: 1847 Comm: syz-executor.0 Not tainted 5.17.0 #1
Hardware name: QEMU Standard PC (i440FX + PIIX, 1996)
Call Trace:
 <TASK>
 dump_stack_lvl+0x45/0x5a
 print_address_description.constprop.0+0x1f/0x160
 kasan_report+0xf8/0x130
 kasan_check_range+0x100/0x1d0
 snd_pcm_hw_params_user+0x3f2/0x500 [snd_pcm]
 snd_pcm_common_ioctl+0x52/0x80 [snd_pcm]
 snd_pcm_ioctl+0x56/0x70 [snd_pcm]
 __x64_sys_ioctl+0xb4/0x100
 do_syscall_64+0x35/0x80
 entry_SYSCALL_64_after_hwframe+0x6e/0xd8

Allocated by task 1847:
 kasan_save_stack+0x1b/0x40
 __kasan_kmalloc+0xa9/0xc0
 kmalloc_trace+0x26/0x100
 snd_pcm_hw_params_user+0xf5/0x500 [snd_pcm]
 ...

Freed by task 1847:
 kasan_save_stack+0x1b/0x40
 kasan_set_free_track+0x18/0x30
 kasan_save_free_info+0x2b/0x40
 ____kasan_slab_free+0xd3/0x120
 kfree+0xac/0x2b0
 snd_pcm_hw_params_user+0x1e8/0x500 [snd_pcm]
 ...

The buggy address belongs to the object at ffff888079b24e00
 which belongs to the cache kmalloc-256 of size 256
The buggy address is located 16 bytes inside of the 256-byte region
                [ffff888079b24e00, ffff888079b24f00)

Memory state around the buggy address:
 ffff888079b24d00: 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
 ffff888079b24e00: fa fb fb fb fb fb fb fb fb fb fb fb fb fb fb fb
                   ^^
 ffff888079b24f00: fc fc fc fc fc fc fc fc fc fc fc fc fc fc fc fc
==================================================================
```

### 逐段解讀

**第 1 行**：bug 類型和發生位置
```
BUG: KASAN: use-after-free in snd_pcm_hw_params_user+0x3f2/0x500 [snd_pcm]
```
- `use-after-free`：存取已 free 的記憶體
- `snd_pcm_hw_params_user`：觸發的函式（`+0x3f2` 是函式內偏移，`/0x500` 是函式大小）
- `[snd_pcm]`：所在的 kernel module

**第 2 行**：操作細節
```
Write of size 4 at addr ffff888079b24e10 by task syz-executor.0/1847
```
- `Write of size 4`：是一個 4-byte 的**寫**操作（這比讀更嚴重，可能被利用）
- 地址 `ffff888079b24e10`
- `task syz-executor.0/1847`：哪個 process / PID 觸發

**Call Trace 段**：觸發時的完整 call stack，從底層往上讀（`entry_SYSCALL_64` → `ioctl` → `snd_pcm_ioctl` → `snd_pcm_hw_params_user`）。這就是 root cause 在哪個函式的路徑。

**Allocated by task 段**：這塊記憶體是什麼時候、在哪裡分配的（`kmalloc` 在 `snd_pcm_hw_params_user+0xf5` 呼叫）。

**Freed by task 段**：這塊記憶體是什麼時候、在哪裡 free 掉的（`kfree` 在 `snd_pcm_hw_params_user+0x1e8` 呼叫）。

**Memory state 段**：地址周圍的 shadow memory 狀態：
```
ffff888079b24e00: fa fb fb fb ...
                  ^^
```
- `fa`：heap left redzone（kasan redzone before object）
- `fb`：已 free 的物件內部

這裡 `fa` 代表 `ffff888079b24e00` 是 object 的起始（但前面是 redzone），`fb` 代表物件裡面全是 "freed" 標記。出問題的地址 `+0x10`（16 bytes in）正好落在 `fb` 區域——印證了 UAF。

**調查邏輯**：
1. 看第 1 行確認 bug 類型
2. 看 Call Trace 找**觸發點**（`snd_pcm_hw_params_user+0x3f2`）
3. 看 Freed by 找**什麼時候 free**（同一個函式的 `+0x1e8`，代表 allocate-free-access 在同一個函式路徑內——可能是 TOCTOU 或 race condition）
4. 看 Allocated by 確認物件的生命週期

## KMSAN：未初始化記憶體 oracle

KMSAN（Kernel Memory Sanitizer）偵測的是「使用未初始化記憶體」的 bug。這類 bug 在 KASAN 下看不到，因為存取位址是合法的，只是值是垃圾。

**機制**：KMSAN 用 shadow memory 記錄每個 byte 是否被初始化（1 bit per byte）。讀到未初始化的 shadow byte 時報告。

```
BUG: KMSAN: uninit-value in copy_to_user+0x50/0x90
...
Uninit was created at:
  __kmalloc+0x1e5/0x340
  drivers/net/wireless/example/driver.c:423
```

**在 fuzzing 中的用處**：未初始化記憶體洩漏到 userland 是資訊洩漏 bug（infoleak），可以用於 bypass KASLR。這類 bug 對 UAF exploit chain 非常重要，但 KASAN 完全偵測不到（地址合法，只是值不對）。KMSAN 是 kernel fuzzing 裡偵測 infoleak 的主要 oracle。

**開銷**：比 KASAN 更重，shadow 是 1:1（每個 byte 有對應的 shadow byte），執行速度慢約 3–5×。通常和 KASAN 分開跑（兩個 kernel 分別啟用），而不是同時啟用。

## KCSAN：Data race oracle

KCSAN（Kernel Concurrency Sanitizer）偵測**並發資料競爭**（data race）——兩個 CPU 同時存取同一塊記憶體，其中至少一個是寫操作，沒有適當的同步保護。

**機制**：Watch point based。KCSAN 隨機抽樣 memory access，對被抽到的 access 設定一個 "watch point"（硬體 breakpoint），短暫延遲後檢查是否有其他 CPU 也在存取同一地址。

```
BUG: KCSAN: data-race in netif_receive_skb_core / dev_queue_xmit_nit
Write of size 4 at 0xffff88813e3e0480 by task ksoftirqd/0 (preempted):
 skb->dev = ...
 netif_receive_skb_core+0x...

Concurrent read of size 4 at 0xffff88813e3e0480 by interrupt:
 dev_queue_xmit_nit+0x...
```

**在 fuzzing 中的用處**：KCSAN 對需要並發的 fuzzing 特別重要。syzkaller 可以設定多個執行緒同時跑 syscall，試圖觸發 race condition。沒有 KCSAN，很多 race-induced UAF 就算被觸發也看不到（因為時序問題，往往不立即 crash）。

**開銷**：比 KASAN 輕（watch point 是抽樣的，不是全 coverage），約 10–15% 執行速度下降，但**偵測率不是 100%**——一次執行可能抓不到，需要多次跑才能看到。

## KFENCE：低開銷 sampling oracle

KFENCE（Kernel Electric Fence）是 KASAN 的輕量替代方案。它不是插樁每個 memory access，而是**抽樣少量 allocation**，把它們放在帶有 guard page 的特殊位置：

```
guard page  |  object  |  guard page
(不可存取)  |  （正常） |  （不可存取）
```

任何超出 object 邊界的存取都會觸發 page fault，KFENCE 攔截這個 page fault 並產生報告。

**和 KASAN 的差異**：

| | KASAN | KFENCE |
|---|---|---|
| 覆蓋率 | 所有 allocation | 隨機抽樣（預設 1/512）|
| 偵測時機 | 立即（inline check）| 立即（page fault）|
| OOB 大小 | 任何大小 | 只要到下一個 guard page |
| UAF 偵測 | 有（free 後 shadow 標記）| 有（free 後放回 guard zone）|
| 執行開銷 | 2–3x | <1%（幾乎無感）|
| 記憶體開銷 | 1/8 額外（shadow）| 固定 pool（預設 2MB）|
| 適合場景 | fuzzing kernel | **生產 kernel**（預設開在部分 distro）|

KFENCE 的設計目標是「能在生產機器上常態開啟」，Fedora、Ubuntu 的部分版本預設啟用。它不夠「靈敏」，每次執行只有抽樣到的 allocation 被保護，但幾乎沒有效能代價。Syzkaller 在跑 fuzz 時用 KASAN 取得完整覆蓋；在生產環境裡 KFENCE 是補充。

## UBSAN：Undefined Behavior oracle

UBSAN（Undefined Behavior Sanitizer）偵測 C/C++ 的未定義行為：

- 整數溢位（signed integer overflow）
- 移位越界（shift >= type width）
- 空指標解參考（null pointer dereference）
- Misaligned access（對齊需求不滿足的存取）
- Array index OOB（有些情況）

Kernel 裡的 UBSAN 比 userland 版本啟用的 check 少，因為 kernel 大量使用 UB-on-purpose 的 trick（例如 `container_of` 的 negative offset pointer arithmetic）。

在 fuzzing 裡，UBSAN 是 KASAN 的補充——KASAN 追記憶體安全，UBSAN 追算術 UB：

```
UBSAN: signed-integer-overflow in drivers/gpu/drm/amdgpu/amdgpu_vm.c:1234
-2 * 9223372036854775807 cannot be represented in type 'long'
```

## 開銷對比表

| Sanitizer | 偵測類型 | CPU 開銷 | 記憶體開銷 | 適合場景 |
|-----------|---------|---------|-----------|---------|
| KASAN (Generic) | OOB + UAF + stack | 2–3× | +12.5% (shadow) | Fuzzing |
| KASAN (HW_TAGS) | OOB + UAF | ~10% | +~0% | arm64 fuzzing |
| KMSAN | Uninit memory / infoleak | 3–5× | +100% (1:1 shadow) | Infoleak 專項 fuzz |
| KCSAN | Data race | +10–15% | 少量 | 並發 fuzzing |
| KFENCE | OOB + UAF（抽樣） | <1% | 固定 2MB | 生產機器 |
| UBSAN | 算術 UB | <10% | 少 | 配合 KASAN |

**Fuzzing 常用配置**：

- **標準配置**：`CONFIG_KASAN=y` + `CONFIG_KASAN_INLINE=y`（inline check 比 outline 快 50%）
- **Infoleak 專項**：`CONFIG_KMSAN=y`（單獨，不和 KASAN 同時用）
- **Race 專項**：`CONFIG_KCSAN=y` + syzkaller 的 multi-thread 模式
- **生產監控**：`CONFIG_KFENCE=y`

不要試圖同時開 KASAN + KMSAN——兩者的 shadow memory 機制衝突。

## kernel sanitizer 在 fuzzing 工作流裡的位置

```
syz-manager
     │
     ├── 分配 VM
     │     VM 跑 KASAN + KCOV 的 kernel
     │
     ├── syz-executor 執行 syscall 序列
     │     KASAN: 即時偵測記憶體錯誤
     │     KCOV:  收集 coverage
     │
     ├── 如果 KASAN 報告 ──→ kernel panic
     │     syz-manager 從序列埠收到 panic log
     │     ── 包含完整 KASAN splat ──→ crash DB
     │
     ├── 自動最小化
     │     把 syscall 序列 bisect 到最小仍能觸發 KASAN 的版本
     │
     └── 生成 C reproducer
           讓開發者能獨立重現
```

沒有 KASAN，fuzzer 只能靠最終的 NULL deref 或 kernel panic 判斷 bug，大量 OOB/UAF 都會漏掉。有了 KASAN，幾乎所有記憶體安全 bug 在觸發瞬間就被抓到，crash rate 大幅提升。

## KASAN config 設定

自 build kernel 用於 fuzzing 的最小 KASAN config：

```bash
# 必要：開 KASAN
scripts/config --enable CONFIG_KASAN
scripts/config --set-val CONFIG_KASAN_GENERIC y
scripts/config --enable CONFIG_KASAN_INLINE    # inline check 比 outline 快
scripts/config --enable CONFIG_DEBUG_FS
scripts/config --enable CONFIG_DEBUG_KMEMLEAK  # 可選：記憶體洩漏偵測

# 強烈建議開的
scripts/config --enable CONFIG_KCOV            # coverage（Ch 22）
scripts/config --enable CONFIG_DEBUG_INFO      # addr2line 需要
scripts/config --enable CONFIG_KALLSYMS_ALL    # 完整 symbol 解析

# 如果要追 race condition
scripts/config --enable CONFIG_KCSAN
scripts/config --enable CONFIG_KCSAN_STRICT

# 如果要追 infoleak（不能和 KASAN 同時）
# scripts/config --enable CONFIG_KMSAN
```

## 踩雷

**錯誤直覺 1**：「KASAN 報告說是 `use-after-free`，代表一定能找到 exploit primitive。」

UAF 是否可利用取決於：物件的大小和類型（是不是 function pointer？）、free 後到存取中間的視窗有多長、能不能在這個視窗裡控制 heap layout。KASAN 只告訴你「有 UAF 存在」，它不告訴你可利用性。很多 UAF 在特定的 kernel 版本或 config 下沒有直接的 exploit primitive。分析可利用性是 kernel_pwn 課的主題。

**錯誤直覺 2**：「KASAN 開著就可以不用擔心 data race 了。」

KASAN 偵測記憶體安全（OOB/UAF），KCSAN 偵測並發競爭。兩者正交。一個 data race 導致的 UAF，race window 可能很窄——KASAN 在 UAF 發生的那一刻能抓到（前提是 race 真的出現），但如果 race 時序太緊，run 一次不一定觸發。這是為什麼 race-induced bug 要搭配 KCSAN 做「靜態」偵測（只要兩個 CPU 有並發的 un-synchronized access，就報告），而不只靠 KASAN。

**錯誤直覺 3**：「KASAN 報告裡 Allocated by / Freed by 的 stack trace 就是 root cause。」

Allocated by 和 Freed by 的 stack trace 告訴你記憶體生命週期，但 root cause 是「為什麼 free 之後還有人持有指標」。真正的 root cause 通常在更上層的 reference counting 或 lock discipline 裡，需要讀程式碼才能找到。KASAN 的報告是起點，不是終點。

**錯誤直覺 4**：「開了 KASAN 的 kernel，bug 一定在第一次發生時就被抓到。」

KASAN 的 inline check 覆蓋 kmalloc/vmalloc 等主要配置器，但有些 kernel 記憶體路徑不經過這些配置器（例如 per-CPU 資料、DMA 緩衝區、某些 IO mapping）。這些路徑上的 OOB/UAF，KASAN 抓不到。KFENCE 可以覆蓋 kmalloc，但同樣有限制。完整的記憶體安全需要多種 oracle 組合使用。

## 進階延伸

- **KASAN + syzkaller 的 crash dedup**：syzkaller 用 crash 的 call stack hash 去重（避免同一個 bug 的不同觸發路徑被當成不同 bug）。理解 KASAN 報告格式，能幫助你手動 debug dedup 失敗的 case。
- **KASAN 誤報**：極少情況下 KASAN 會誤報（通常是 kernel 的 intentional "unsafe" 操作沒有正確標注 `kasan_disable_current()`）。遇到可疑的 KASAN 報告，先看有沒有 `__no_sanitize_address` 標記。
- **Hardware-assisted KASAN on ARM64**：MTE 提供 16 bytes 的顆粒度，硬體 tag 比對，幾乎零額外開銷。未來 ARM64 的 kernel fuzzing 會廣泛用這個。

## 動手練習

1. 線上閱讀 syzbot 上的一個真實 KASAN crash（https://syzkaller.appspot.com/upstream），找一個 `KASAN: use-after-free` 的報告，按本章的「逐段解讀」方法，找出觸發函式、分配位置、free 位置，並猜測 bug 類型（race? refcount error? lifetime 問題？）。
2. 在你的 kernel 上查看是否啟用了 KASAN（`zcat /proc/config.gz | grep KASAN`）。如果是 WSL2，記錄沒有啟用的原因（通常是效能考量）。
3. 閱讀 `include/linux/kasan.h`（線上：https://elixir.bootlin.com/linux/latest/source/include/linux/kasan.h），找到 `kasan_report()` 的呼叫點。
4. 對比 KASAN 和 userland ASAN（libasan）的 shadow memory scheme：userland ASAN 用地址的低位做 shadow map（`shadow = addr >> 3 + 0x7fff8000`），KASAN 用不同的 mapping（`kasan_mem_to_shadow(addr)`）——為什麼？提示：kernel 的 virtual address space 和 userland 完全不同。

## 本章重點

- Kernel sanitizer 是 fuzzing 的「oracle」：在 bug 發生瞬間偵測，而不是等最終 crash。
- KASAN 用 shadow memory 偵測 heap/stack OOB 和 UAF，是 kernel fuzzing 最重要的 oracle，開銷約 2–3×。
- KMSAN 偵測未初始化記憶體（infoleak 類 bug），KCSAN 偵測 data race，KFENCE 是生產環境的低開銷替代。
- KASAN splat 的四個關鍵段：bug 類型、觸發 call trace、Allocated by、Freed by——按這四個段定位 root cause。
- Fuzzing 標準配置：`CONFIG_KASAN=y` + `CONFIG_KASAN_INLINE=y` + `CONFIG_KCOV=y`。

## 自我檢核

- [ ] 我能解釋 KASAN shadow memory 的 encoding（`0x00` / `0xfa` / `0xfb` / `0xfe` 各代表什麼）
- [ ] 我能從一份 KASAN splat 找出：觸發函式、bug 發生的地址、object 何時 allocated、何時 freed
- [ ] 我能說出 KASAN、KMSAN、KCSAN 各自偵測什麼，並說明三者不能互相取代的原因
- [ ] 我能解釋 KFENCE 和 KASAN 的核心差異（抽樣 vs 全覆蓋）以及適用場景
- [ ] 我知道為什麼 KASAN 和 KMSAN 不能同時啟用

## 延伸閱讀

1. **[Linux kernel documentation: KASAN](https://www.kernel.org/doc/html/latest/dev-tools/kasan.html)**
   - 讀哪段：「Implementation details」section，特別是 shadow memory encoding 的表格。
   - 學什麼：KASAN 官方文件，含 Generic / SW_TAGS / HW_TAGS 三種模式的詳細比較，是本章機制說明的權威來源。
   - 關聯：本章 KASAN shadow memory 段落。

2. **[Fuzzing the Linux Kernel（Google Security Blog, 2020）](https://security.googleblog.com/2020/03/fuzzing-linux-kernel-with-syzkaller.html)**
   - 讀哪段：「Detecting bugs with sanitizers」section。
   - 學什麼：syzkaller 的實際作者說明他們如何組合 KASAN/KMSAN/KCSAN 來最大化 bug 偵測率，包含一些在本章沒提到的實戰細節（例如如何處理 KASAN 的 false positive）。
   - 關聯：本章的 sanitizer 組合使用策略。

3. **[KMSAN: Kernel Memory Sanitizer (LWN, 2022)](https://lwn.net/Articles/888346/)**
   - 讀哪段：整篇文章，特別是「How it works」段落和 example report。
   - 學什麼：KMSAN 的設計比 KASAN 更複雜（需要追蹤 "origin" 讓你知道未初始化資料的來源）；這篇 LWN 文章是最清楚的入門，包含真實的 infoleak 案例。
   - 關聯：本章 KMSAN 段落，以及 kernel_pwn 課的 infoleak 利用技術。

4. **[CVE-2022-1048 KASAN 報告原始來源（syzbot）](https://syzkaller.appspot.com/bug?extid=9d616de01e9b9cca8f0b)**
   - 讀哪段：「Crash log」欄位的完整 KASAN splat，以及「Reproducer」欄位的 C 程式碼。
   - 學什麼：真實 CVE 的 KASAN 報告和 reproducer 長什麼樣——對比本章的解讀範例，確認你能獨立分析一份真實的報告。
   - 關聯：本章的「真實 KASAN splat 解讀」段落。

→ [下一章：syzkaller 架構](./24-syzkaller-architecture.md)
