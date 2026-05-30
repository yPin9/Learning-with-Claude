# Ch 22 — kernel 早期初始化

> **目標**：追蹤解壓後的 kernel 從 `startup_64` 到 `start_kernel` 的早期初始化——建立 kernel 自己的頁表、設定 CPU、初始化核心子系統（記憶體管理、排程器、中斷），理解這段「從赤裸 64-bit 到功能完整的 kernel」的關鍵過程。

> **環境**：Linux kernel 6.x，x86-64。承接 Ch 21（解壓）。原理深挖章，涉及 kernel 原始碼概念。

## 為什麼這段這麼關鍵又這麼難看到？

kernel 解壓後（Ch 21），它從 `startup_64` 開始執行——此時 kernel 處於一個尷尬狀態：在 64-bit，但很多東西還沒設好（kernel 自己的頁表、記憶體管理、排程器都還不存在）。它要在這片「半成品」上把自己一步步建立成功能完整的 OS kernel。

這段過程很難觀察（kernel 還沒有 printk 輸出、沒有除錯介面），但它是「kernel 怎麼把自己拉拔起來」的核心。理解它，你就懂 kernel 啟動的真正內幕——從 bootloader 交棒的那一刻，到能執行第一個 process 之間發生了什麼。

## 先建立直覺：kernel 自己拉拔自己

```
kernel 早期初始化像「自己拉自己的鞋帶站起來」（bootstrap）：

  解壓後的 kernel（startup_64）：
    - 在 64-bit（bootloader/解壓 stub 設好的）
    - 但用的是「臨時」的環境（bootloader 的頁表等）
        │
  kernel 要建立「自己的」環境：
    1. 建立 kernel 自己的頁表（不再用 bootloader 的）
    2. 設定 per-CPU 資料、GDT、IDT（中斷表）
    3. 初始化記憶體管理（buddy allocator、slab）
    4. 初始化排程器、時鐘、中斷處理
    5. 初始化各子系統
        │
  → 從「能跑 64-bit code」到「功能完整的 OS kernel」
```

「bootstrap」這個詞就來自「拉自己的鞋帶」（pull oneself up by bootstraps）——kernel 在沒有完整環境的情況下，用最小的初始狀態一步步建立完整功能。這是系統程式設計最迷人的部分之一。

## startup_64：解壓後的入口

kernel 解壓後從 `startup_64`（`arch/x86/kernel/head_64.S`）開始：

```
startup_64 做的事（組合語言，arch/x86/kernel/head_64.S）：
  1. 確認在 long mode、設定基本的 segment
  2. 建立 kernel 的早期頁表（identity map + kernel 的虛擬位址映射）
     - bootloader/解壓 stub 用的是臨時頁表
     - kernel 建立自己的 early page table
  3. 設定 kernel 的 stack
  4. 清 BSS（未初始化的全域變數歸零）
  5. 載入 kernel 自己的 GDT
  6. 跳到 C code：x86_64_start_kernel → start_kernel
        │
  這段是 assembly，因為要在「C runtime 還沒設好」時設好環境
```

`startup_64` 是 assembly——因為 C 程式需要 stack、需要全域變數初始化（BSS 清零），而這些是 `startup_64` 才設好的。所以最早期必須用 assembly，把 C runtime 需要的環境建好，才能跳進 C code。

## 從 assembly 到 C：start_kernel

設好環境後，kernel 跳進 C 的 `start_kernel`（`init/main.c`）——這是 kernel 初始化的主函式：

```c
// init/main.c — kernel 初始化的核心（極度簡化）
asmlinkage __visible void __init start_kernel(void)
{
    // 早期設定
    setup_arch(&command_line);     // 架構相關設定（解析 boot_params、e820...）
    setup_per_cpu_areas();         // per-CPU 資料
    
    // 核心子系統初始化
    build_all_zonelists(NULL);     // 記憶體 zone
    page_alloc_init();             // 頁分配器（buddy allocator）
    
    // 排程器
    sched_init();                  // 排程器初始化
    
    // 中斷
    early_irq_init();
    init_IRQ();                    // 中斷處理
    
    // 時鐘
    time_init();
    timekeeping_init();
    
    // 記憶體管理完整初始化
    mm_init();                     // slab allocator、vmalloc...
    
    // ... 數十個子系統初始化 ...
    
    // 最後：啟動第一個 process（Ch 23）
    arch_call_rest_init();         // → rest_init() → 建立 init process
}
```

`start_kernel` 是一長串子系統初始化的呼叫——記憶體管理、排程器、中斷、時鐘、各種驅動子系統。每個 `*_init()` 把一個子系統從無到有建立起來。這是 kernel 從「半成品」到「功能完整」的過程。

## 關鍵子系統初始化

```
start_kernel 初始化的關鍵子系統：

  setup_arch：
    - 解析 bootloader 傳的 boot_params（Ch 20）
    - 建立記憶體地圖（從 e820/EFI memory map）
    - 設定 kernel 的記憶體佈局
        │
  記憶體管理（mm_init 等）：
    - buddy allocator（頁級分配）
    - slab/slub allocator（物件級分配，kmalloc）
    - kernel 現在能動態配置記憶體
        │
  排程器（sched_init）：
    - 建立 run queue
    - 準備好「能排程 process」
        │
  中斷（init_IRQ）：
    - 設定 IDT（中斷描述符表）
    - 註冊中斷處理常式
    - kernel 現在能處理硬體中斷
        │
  時鐘（time_init）：
    - 設定 timer
    - kernel 現在有時間概念、能做 time-based 排程
```

這些子系統有依賴順序——例如記憶體管理要先建好，後面的子系統才能配置記憶體。`start_kernel` 的呼叫順序就是這個依賴順序的體現。

## printk 與早期輸出

kernel 早期就能 `printk`（kernel 的 print），但輸出機制隨初始化進展而變：

```
kernel 輸出的演進：
  最早期：early printk（直接寫 serial port 或 video memory）
        │
  console 初始化後：寫進 kernel log buffer
        │
  完整 console：能輸出到螢幕、serial、netconsole...
        │
  這些 log 就是 dmesg 看到的東西
```

```bash
# 看 kernel 早期初始化的 log
sudo dmesg | head -50
# [    0.000000] Linux version 6.1.0 ...
# [    0.000000] Command line: ...        ← bootloader 傳的 cmdline（Ch 20）
# [    0.000000] BIOS-provided physical RAM map: ...  ← e820（Ch 3）
# [    0.000000] ... setup_arch 的輸出 ...
# [    0.xxxxxx] ... 各子系統初始化 ...
```

`dmesg` 的最早幾行就是 `start_kernel` 各階段的輸出——你能看到 command line（bootloader 傳的）、記憶體地圖（e820）、各子系統初始化。這是觀察 kernel 早期初始化最直接的方式。

## __init：只用一次的初始化 code

kernel 的初始化函式標記 `__init`——這些 code 只在開機時用一次，之後 kernel 釋放它們的記憶體：

```c
// __init 標記的函式，初始化後記憶體被釋放
static int __init my_subsystem_init(void) {
    // 一次性初始化
    return 0;
}

// __initdata 標記的資料同理
static int my_init_data __initdata = 42;
```

```
__init 的設計：
  初始化 code 只開機時用一次
  → 標記 __init，放在特殊的記憶體區段
  → 初始化完成後，kernel 釋放這些區段的記憶體
        │
  dmesg 會看到：
  "Freeing unused kernel image (initmem) memory: ... K"
  ↑ 釋放 __init 的記憶體
```

```bash
sudo dmesg | grep -i "freeing unused"
# Freeing unused kernel image (initmem) memory: 2880K
#   ↑ 初始化用完，釋放這些一次性 code 的記憶體
```

> `__init` 是個聰明的記憶體優化：初始化 code 開機後就沒用了，留著浪費記憶體。kernel 把它們集中放，初始化完釋放。這就是 dmesg 裡「Freeing unused kernel memory」的意思——不是 kernel 有問題，是正常的初始化記憶體回收。理解它能解釋這個常見的 dmesg 訊息。

## 故意對照：早期 init 失敗的後果

```
kernel 早期初始化失敗（如記憶體偵測錯誤、頁表建立失敗）：
        │
  此時還沒有完整的錯誤處理、可能還沒有 console
        │
  → 通常是「early panic」或直接當機（黑屏、無輸出）
  → 比後期的 kernel panic 更難 debug（沒有錯誤訊息）
        │
  常見原因：
    - bootloader 傳的 boot_params 錯（Ch 20）
    - 記憶體地圖損壞
    - CPU/硬體不相容
```

早期初始化失敗特別難 debug——kernel 還沒有完整的錯誤輸出機制。如果開機「黑屏無反應」（連 kernel log 都沒有），可能是早期初始化掛了。這時 `earlyprintk` kernel 參數能讓最早期的輸出走 serial port，幫助 debug。

## 踩雷集錦

1. **以為 kernel 解壓後就「完全可用」**：解壓後只是能跑 64-bit code，記憶體管理、排程器都還沒建。要走完 start_kernel 才功能完整

2. **混淆 startup_64 和 start_kernel**：startup_64 是 assembly（建環境），start_kernel 是 C（初始化子系統）。前者為後者鋪路

3. **以為 "Freeing unused kernel memory" 是錯誤**：那是正常的 __init 記憶體回收。不是問題

4. **早期 panic 沒有輸出就放棄**：早期初始化失敗可能無輸出。用 `earlyprintk=serial` 等參數讓最早期輸出走 serial，能看到更多

5. **以為 printk 從第一刻就完整**：kernel 輸出機制隨初始化進展。最早期是 early printk（直接寫硬體），console 初始化後才完整

## 進階：multi-core 的啟動（SMP）

到目前為止講的是「啟動 CPU」（boot CPU / BSP）。但現代機器有多核——其他核（AP, Application Processors）怎麼啟動？

```
SMP（對稱多處理）啟動：
  開機時只有一個 CPU 在跑（BSP, Bootstrap Processor）
  start_kernel 在 BSP 上跑，建立基本環境
        │
  smp_init()：BSP 喚醒其他 CPU（AP）
    - 透過 IPI（Inter-Processor Interrupt）發信號
    - 每個 AP 從 reset 狀態開始，跑類似的初始化
    - AP 走簡化版的啟動（trampoline → 進 long mode → 加入排程）
        │
  → 所有 CPU 都進入排程器，能並行執行 process
```

```bash
# 看 SMP 啟動
sudo dmesg | grep -i "smpboot\|CPU"
# smpboot: Booting Node 0 Processor 1 APIC ...
# smpboot: Booting Node 0 Processor 2 APIC ...
#   ↑ BSP 逐一喚醒其他核
nproc   # 看有幾個 CPU
```

> 多核啟動是 kernel 初始化的一個有趣部分：開機時只有一個 CPU（BSP）跑，它建立好環境後，用 IPI 逐一喚醒其他核（AP）。每個 AP 走簡化版的「進 long mode + 加入排程器」。這就是為什麼 dmesg 有「Booting Processor 1, 2, 3...」。理解 SMP 啟動，你會懂為什麼多核機器的開機 log 有逐核喚醒的訊息。這呼應 Ch 7-8 的模式切換——每個 AP 也要切到 long mode，只是 kernel 幫它們做。

## 動手練習

1. 讀 kernel 早期 log：`sudo dmesg | head -60`，找出 command line、記憶體地圖（e820）、各子系統初始化的訊息。對照本章的 start_kernel 流程

2. 看 __init 記憶體回收：`sudo dmesg | grep -i "freeing"`，理解這是正常的初始化記憶體釋放

3. 看 SMP 啟動：`sudo dmesg | grep -i smpboot`，看 BSP 喚醒其他核。`nproc` 確認核數

4. 概念追蹤：到 elixir.bootlin.com 讀 `init/main.c` 的 `start_kernel`，看它呼叫的子系統初始化函式（setup_arch、mm_init、sched_init...），理解初始化的依賴順序

## 本章重點整理

- 解壓後的 kernel 從 `startup_64`（assembly）開始：建立 kernel 自己的頁表、stack、清 BSS、載入 GDT，為 C code 鋪路
- 跳進 `start_kernel`（C，init/main.c）：一長串子系統初始化（記憶體管理、排程器、中斷、時鐘...），有依賴順序
- kernel「自己拉拔自己」（bootstrap）：從「能跑 64-bit」到「功能完整的 OS kernel」
- `__init` 標記的初始化 code 只用一次，初始化後釋放（dmesg 的 "Freeing unused kernel memory"）
- SMP：BSP 先啟動建環境，再用 IPI 逐一喚醒其他核（AP），所有核進排程器

## 自我檢核

- [ ] 能解釋 startup_64（assembly）和 start_kernel（C）的分工
- [ ] 知道為什麼最早期初始化必須用 assembly（C runtime 還沒設好）
- [ ] 能說出 start_kernel 初始化的幾個關鍵子系統及其依賴順序
- [ ] 知道 __init 是什麼、"Freeing unused kernel memory" 的意思
- [ ] 能解釋 SMP 多核怎麼啟動（BSP 喚醒 AP）

## 延伸閱讀

### 官方文件

- **[Linux kernel: init/main.c (start_kernel)](https://elixir.bootlin.com/linux/latest/source/init/main.c)**
  - **讀哪裡**：`start_kernel` 函式，看它呼叫的初始化函式序列
  - **學什麼**：kernel 初始化的權威來源（原始碼），子系統的初始化順序
  - **前提**：本章 + C

### 部落格 / 文章

- **[Linux Inside: Kernel initialization](https://0xax.gitbooks.io/linux-insides/content/Initialization/)** — 0xax
  - **這篇說什麼**：逐步讀 kernel 從 startup_64 到 start_kernel 的初始化原始碼
  - **讀哪裡**：從 "First steps in the kernel" 開始的幾篇
  - **為什麼值得讀**：本章的深度補充，把每個初始化步驟的原始碼講透

### 書籍

- **《Understanding the Linux Kernel, 3rd ed.》** — Bovet & Cesati
  - **這本書的定位**：kernel 內部的經典，開機後的子系統（記憶體、排程、中斷）詳解
  - **讀哪幾章**：Ch 2（記憶體定址）、Ch 7（排程）、Ch 4（中斷）——start_kernel 初始化的子系統
  - **前提**：本章建立的全圖

→ [Ch 23 從 kernel 到第一個 process](./23-kernel-to-init.md)
