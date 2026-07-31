# Ch 29 — 中斷處理：IDT/GIC、top/bottom half

> **目標**：搞懂一個硬體事件（網卡收到封包、你按下鍵盤、磁碟寫完）是怎麼從裝置一路走到 kernel 的中斷處理常式的：裝置 → 中斷控制器（x86 的 APIC/IOAPIC、ARM64 的 GIC）→ CPU → 中斷向量表（x86 IDT、ARM64 exception vector）→ handler。學完你能讀懂 `/proc/interrupts`、能自己 `request_irq` 掛一個 handler、能解釋「為什麼中斷處理要拆成 top half / bottom half 兩半」、能講清楚中斷 context 為什麼不能睡（接 Ch 2 的紅線）。這是 Part 5（中斷與時間）的第一章，也是後面驅動（Ch 41 DMA）與網路收包（Ch 44 NAPI）的地基。

## 為什麼需要這個？

CPU 快，硬體慢，而且慢得沒有下限。網卡什麼時候收到下一個封包？沒人知道。使用者什麼時候按鍵盤？可能十秒，可能十分鐘。磁碟一次讀取要幾毫秒——對一顆每秒跑數十億條指令的 CPU 來說，那是一段「等到天荒地老」的時間。

那 CPU 怎麼知道硬體好了？兩條路：

- **輪詢（polling）**：CPU 反覆去問「好了沒？好了沒？」。這叫 busy-wait。問題很明顯——CPU 全部時間耗在問，什麼別的事都幹不了；而且你問的頻率太低會延遲，太高會燒 CPU。對一個「十分鐘才按一次」的鍵盤用輪詢，等於派一個人整天盯著門看你回不回家。
- **中斷（interrupt）**：反過來，硬體好了**主動打斷 CPU** 通知它。CPU 平時去做別的事（或睡），硬體一有事就用一條實體訊號線（或訊息）把 CPU 拉回來處理。這是門鈴——你在家做事，有人來按鈴你才去開門。

中斷是現代 OS 的地基。沒有它，CPU 只能靠輪詢，多工根本跑不動。這一章講的就是這套「硬體打斷 CPU」的機制在 Linux 裡怎麼運作。

但輪詢沒有死透。在**極高頻率**的場景下中斷反而是負擔——想像 10 Gbps 網卡每秒收上百萬個封包，每個封包一次中斷，光是「進中斷、存暫存器、跳 handler、返回」的固定開銷就把 CPU 吃垮，這叫**中斷風暴（interrupt storm）**。所以高速網路收包（Ch 44 的 NAPI）玩了一手漂亮的：中斷來了先**關掉這條中斷**，然後改用輪詢一次撈一批封包，撈完再開回中斷。這是「中斷 + 輪詢」的混合，兩者各取所長。這一章先把純中斷路徑講透，Ch 44 你會看到它怎麼被 NAPI 反過來利用。

## 先建立直覺

先把整條路徑放在一張圖裡。從裝置發出訊號，到你的 handler 被呼叫，中間隔著一個**中斷控制器**和一張**向量表**：

```
   裝置（網卡 / 鍵盤 / 磁碟）
      │  拉高中斷線，或送一個 MSI 訊息
      ▼
   中斷控制器                            ← x86: IOAPIC → Local APIC
   （決定送給哪顆 CPU、優先權仲裁）        ← ARM64: GIC (Distributor + Redistributor + CPU interface)
      │  在目標 CPU 上 assert 一個中斷向量號
      ▼
   CPU 收到中斷
      │  1. 硬體自動：存部分狀態、關本地中斷、切到 kernel 特權層
      │  2. 用「向量號」查向量表 → 拿到入口位址
      ▼
   中斷向量表                            ← x86:   IDT（Interrupt Descriptor Table）
   （向量號 → 入口位址）                  ← ARM64: exception vector table（VBAR_EL1 指向它）
      │  跳到低階組語 stub
      ▼
   低階入口 stub（存完整暫存器現場）
      │  進入 C 世界，找到這個 IRQ 的 irq_desc
      ▼
 ┌────────────────────────── TOP HALF（hardirq context）────────────────────┐
 │  你 request_irq 註冊的 handler                                           │
 │  關著中斷跑，要快：ack 硬體、抓走緊急資料，其餘丟給 bottom half           │
 │  ✘ 不能睡  ✘ 不能 GFP_KERNEL / mutex  ✔ 只能 spin_lock_irqsave           │
 └───────────────────────────────┬─────────────────────────────────────────┘
                                 │ raise / schedule
                                 ▼
 ┌────────────────────── BOTTOM HALF（延後執行，Ch 30）─────────────────────┐
 │  softirq / tasklet（仍在 atomic context，不能睡）                        │
 │  workqueue / threaded IRQ（在 process context，能睡 ✔）                  │
 │  做耗時的活：協定堆疊處理、喚醒等待的 process、更新資料結構               │
 └──────────────────────────────────────────────────────────────────────────┘
```

兩個核心概念先立起來，後面每一節都在展開它們：

1. **IRQ number 是 Linux 的抽象，vector 是硬體的東西。** 硬體只認向量號（x86 的 0–255、GIC 的 INTID），Linux 上面包了一層跨架構的「Linux IRQ number」。中間那層轉換叫 **irq domain**。你 `request_irq(irq, ...)` 傳的是 Linux IRQ number，不是硬體向量。
2. **top half / bottom half 分離。** 中斷 handler 要快——它關著中斷跑，跑太久會丟掉後面的中斷、把延遲拉爛。所以拆兩半：緊急的（ack 硬體、抓資料）在 top half 立刻做，耗時的延後到 bottom half。這條界線是這一章的靈魂。

## 硬體中斷路徑：從裝置到 CPU

### 中斷控制器：誰負責把中斷送到哪顆 CPU

裝置不會直接連到 CPU 的中斷腳。中間隔一個**中斷控制器**，它負責收集所有裝置的中斷、做優先權仲裁、決定送給哪顆 CPU（這就是後面 IRQ affinity 的硬體基礎）。

**x86 的 APIC 體系**分兩層：

- **IOAPIC**：主機板上的一顆，收集外部裝置的中斷線，把它們轉成訊息路由到某顆 CPU 的 Local APIC。
- **Local APIC（LAPIC）**：每顆 CPU 核心內建一個。它接收 IOAPIC 或其他 CPU（IPI，inter-processor interrupt）送來的中斷，遞給這顆核心。它也管本地的 timer 中斷。

現代 PCIe 裝置多半走 **MSI/MSI-X（Message Signaled Interrupt）**——不再拉一條實體中斷線，而是往一個特定記憶體位址寫一個值，這個「寫入」就被 APIC 解讀成一次中斷。好處是每個裝置可以有多個獨立向量（多佇列網卡靠這個給每條 rx 佇列一個中斷，接 Ch 44），也不用跟別人共享中斷線。

**ARM64 的 GIC（Generic Interrupt Controller）**是 ARM 的統一設計，v6.12 主流是 GICv3/v4。它分三塊：

- **Distributor**：全域的，管所有 SPI（Shared Peripheral Interrupt，共享外設中斷）的仲裁與路由。
- **Redistributor**（GICv3 起）：每顆 CPU 一個，管該 CPU 私有的 PPI（Private Peripheral Interrupt）和 SGI（Software Generated Interrupt，等同 x86 的 IPI）。
- **CPU interface**：把中斷實際遞給 CPU 核心，透過系統暫存器（`ICC_*_EL1`）存取。

GIC 的中斷用 **INTID** 編號，分幾段：SGI（0–15）、PPI（16–31）、SPI（32 起）。這個分段是理解 ARM64 中斷的關鍵——per-CPU 的 timer 是 PPI，外設是 SPI。

### CPU 收到中斷後：查向量表

CPU 一收到中斷，硬體會自動做幾件事（不用你的 code 插手）：存一部分狀態、**關掉本地中斷**（避免立刻被下一個中斷再打斷）、切到 kernel 特權層，然後用「向量號」去查一張表，拿到處理常式的入口位址。

**x86 的 IDT（Interrupt Descriptor Table）**：一張 256 項的表，向量 0–255。前 32 個（0–31）保留給 CPU 例外（exception，如 page fault 是 14、除以零是 0）；32 以上給外部中斷用。IDT 的建立在 `arch/x86/kernel/idt.c`——看 `idt_setup_early_traps()`、`idt_setup_traps()`、`idt_setup_apic_and_irq_gates()` 這幾個函式，它們在開機早期把各向量的入口填進 IDT。CPU 靠 `lidt` 指令載入 IDT 的基底位址（存在 IDTR 暫存器）。每一項是一個 gate descriptor，指向一段低階組語 stub（在 `arch/x86/entry/entry_64.S` 那一帶），stub 存好完整暫存器現場後才跳進 C 的共同分派函式。

**ARM64 的 exception vector table**：ARM64 沒有 x86 那種「一個向量一個 entry」的 256 項表。它的 vector table 只有 16 個 entry，按「來源特權層 × 執行狀態 × 例外類型（同步例外 / IRQ / FIQ / SError）」分類。`VBAR_EL1`（Vector Base Address Register）這顆系統暫存器指向這張表的基底。表在 `arch/arm64/kernel/entry.S`（巨集 `kernel_ventry` 展開），IRQ 的入口最終走到 `arch/arm64/kernel/irq.c` 的 `handle_arch_irq`，再由 GIC driver 讀出到底是哪個 INTID。

差別的本質：**x86 靠向量號直接分派**（256 個向量各自對應一個入口，向量號本身就是身分），**ARM64 靠一個統一的 IRQ 入口，再回去問中斷控制器「剛才是哪個 INTID」**。這個差異會一路影響到 irq domain 的映射方式。

### IRQ number vs vector：兩個不同的號

這裡最容易搞混。硬體的向量號（x86 vector、GIC INTID）是**架構特定**的，一個 x86 的向量 33 跟 ARM64 的 INTID 33 沒有任何關係。Linux 為了讓上層驅動不用管底層是哪家中斷控制器，發明了一層跨架構的 **Linux IRQ number**（也叫 virtual IRQ / virq）。

你在驅動裡看到的 `irq`、`/proc/interrupts` 左邊那一欄、`request_irq(irq, ...)` 傳的——全是 Linux IRQ number。它和硬體向量號的對應，由下一節的 irq domain 負責。

## Linux 的 IRQ 抽象層

### irq_desc：每個 IRQ 的檔案

Linux 為每一個 IRQ number 維護一個 `struct irq_desc`（定義在 `include/linux/irqdesc.h`）。它是這個中斷的「檔案」，裝著：

- `handle_irq`：high-level 的 flow handler（例如 edge-triggered 用 `handle_edge_irq`、level-triggered 用 `handle_level_irq`），負責 ack、mask/unmask 的正確時序。
- `action`：一條 `struct irqaction` 鏈——你 `request_irq` 註冊的 handler 就掛在這裡。共享中斷（下面會講）時這條鏈有多個節點。
- `irq_data`：連到底層 irqchip 和 irq domain 的資料，含硬體中斷號。
- `kstat_irqs`：per-CPU 的計數器，`/proc/interrupts` 的數字就是讀它。

中斷來的時候，低階 stub 找到對應的 `irq_desc`，呼叫它的 `handle_irq`，flow handler 再走過 `action` 鏈依序呼叫每個註冊的 handler。核心分派邏輯在 `kernel/irq/handle.c` 的 `handle_irq_event()` / `__handle_irq_event_percpu()`。

### irqchip：中斷控制器的驅動

每種中斷控制器（IOAPIC、GIC、還有各家 SoC 的中斷控制器）都有一個 **irqchip driver**，提供一組 callback（`struct irq_chip`，`include/linux/irq.h`）：怎麼 mask/unmask 這個中斷、怎麼 ack、怎麼設定觸發方式（edge/level）、怎麼設 affinity。這層抽象讓上面的 `irq_desc` 邏輯不用管底下是哪顆晶片。GIC 的 driver 在 `drivers/irqchip/irq-gic-v3.c`，x86 的 APIC 相關在 `arch/x86/kernel/apic/`。

### irq domain：硬體號 → Linux 號的翻譯

一台機器可能有好幾個中斷控制器串接（一個 GIC 下面掛一個 GPIO 控制器，GPIO 又當另一組中斷的來源）。每個控制器的硬體中斷號都從 0 開始，會撞號。**irq domain**（`kernel/irq/irqdomain.c`）解決這個：每個中斷控制器有自己的 domain，負責把「這個控制器的硬體中斷號（hwirq）」映射到「全域唯一的 Linux IRQ number」。

映射從哪來？在有 **device tree**（ARM64、嵌入式，接 Ch 39）的系統上，`.dts` 裡每個裝置節點的 `interrupts = <...>` 屬性描述它接在哪個控制器的哪條線、什麼觸發方式。開機時 `irq_of_parse_and_map()` / `platform_get_irq()` 讀這些屬性，透過對應 domain 的 `irq_domain_ops->xlate` 把它翻成 hwirq，再 `irq_create_mapping()` 配一個 Linux IRQ number 給你。x86 上沒有 device tree，走 ACPI + MSI 那套，但 irq domain 的抽象是一樣的。

一句話串起來：**device tree 說「這裝置接在 GIC 的 SPI 42」→ GIC 的 irq domain 把 SPI 42 映射成 Linux IRQ number 例如 58 → 你 `platform_get_irq()` 拿到 58 → `request_irq(58, ...)`**。

### request_irq：把 handler 掛上去

驅動註冊中斷 handler 的入口是 `request_irq()`（`kernel/irq/manage.c`，實際走 `request_threaded_irq()`）：

```c
int request_irq(unsigned int irq, irq_handler_t handler,
                unsigned long flags, const char *name, void *dev);
```

- `irq`：Linux IRQ number（上面那層翻譯給你的）。
- `handler`：你的 top half，型別 `irqreturn_t (*)(int irq, void *dev_id)`。
- `flags`：`IRQF_SHARED`（允許共享）、`IRQF_TRIGGER_*`（觸發方式）等。
- `name`：顯示在 `/proc/interrupts` 右邊那一欄。
- `dev`：一個 cookie，會原封不動傳回給你的 handler；**共享中斷時必須非 NULL**，用來區分是哪個裝置的 handler、以及 `free_irq` 時比對要拆哪一個。

handler 回傳兩種值：`IRQ_HANDLED`（是我的裝置，我處理了）或 `IRQ_NONE`（不是我的，共享線上別人的中斷）。共享中斷靠這個回傳值讓 flow handler 知道有沒有人認領。

## 底層機制：top half / bottom half 為什麼要拆

這是整章的靈魂。先講清楚問題，再講解法。

### 問題：中斷 handler 跑太久會出人命

回到硬體那一步：CPU 收到中斷時，硬體**自動關掉了本地中斷**。這是必要的——不然你的 handler 執行到一半又被同一個或更高優先權的中斷打斷，現場會亂成一團。但代價是：**你的 top half handler 執行期間，這顆 CPU 對（至少同級的）中斷是聾的**。

於是問題來了。假設你的網卡 handler 花 5 毫秒把封包完整處理完（過協定堆疊、複製資料、喚醒等待的 process）。這 5 毫秒裡：

- 這顆 CPU 收不到別的中斷。第二個封包來了？中斷被擋著。第三個？如果硬體 buffer 滿了，**封包直接丟掉**。
- 系統的中斷延遲（interrupt latency）飆高。一個高優先權的即時任務等著某個中斷，卻被你這 5 毫秒卡死。

結論：**中斷 handler 必須快，快到接近「只做非它不可、且必須立刻做」的最小集合**。但收封包這件事本質上就是耗時的，怎麼辦？

### 解法：拆成兩半

把工作切成兩段：

- **top half（上半部 / hardirq handler）**：關著中斷跑，只做**最緊急、且必須在中斷 context 立刻做**的事——跟硬體打招呼（ack 中斷、清硬體狀態，免得它一直重觸發）、把易失的資料從硬體 buffer 搶出來（DMA 描述符、封包指標），然後**登記一個 bottom half** 說「剩下的等會兒做」，就返回。目標是把 top half 壓到微秒等級。
- **bottom half（下半部）**：延後到「中斷不再被關著」的時機才執行耗時工作——跑協定堆疊、更新大資料結構、喚醒 process。Linux 有三種 bottom half 機制（下一章 Ch 30 專門講）：
  - **softirq**：最底層、per-CPU、高效能，但機制固定（編譯期定死那幾種），仍在 atomic context 不能睡。網路收發（`NET_RX_SOFTIRQ`/`NET_TX_SOFTIRQ`）就是 softirq。
  - **tasklet**：架在 softirq 上的動態版，同一個 tasklet 不會在多顆 CPU 並行，用起來簡單，一樣不能睡。
  - **workqueue** / **threaded IRQ**（Ch 31）：跑在 kernel thread（process context）裡，**能睡**——需要睡眠（等鎖、配大塊記憶體、慢速 I/O）的收尾工作放這裡。

拆的界線怎麼抓？一句話：**top half 只放「不做會出錯 + 現在不做就來不及」的事，其餘全部丟下半部**。ack 硬體不能拖（拖了它一直重觸發），搶 DMA buffer 不能拖（拖了硬體覆寫掉），這兩類進 top half；至於這個封包要送給哪個 socket、要不要喚醒某個 process，這些晚幾十微秒沒差，全進 bottom half。

一個具體對照，網卡收包（Ch 44 會展開）：

```
   封包到 → 網卡發中斷
   ┌─────────────── top half（hardirq）──────────────┐
   │  ack 網卡中斷、關掉這條中斷（NAPI 的手法）        │
   │  __napi_schedule() 登記一個 NET_RX softirq       │  ← 微秒級，立刻返回
   └──────────────────────┬───────────────────────────┘
                          ▼
   ┌─────────── bottom half（NET_RX_SOFTIRQ）─────────┐
   │  poll 收一批封包、過 GRO、丟進協定堆疊、喚醒 socket │  ← 耗時，但中斷已開，不擋別人
   └───────────────────────────────────────────────────┘
```

## 中斷 context 的限制（接 Ch 2）

Ch 2 立過紅線：不同 context 能做的事不一樣。中斷（hardirq）context 是限制最嚴的，因為它**借用了被打斷那個 task 的 kernel stack 在跑，但邏輯上不屬於那個 task**——它不代表任何 process，`current` 指向的是剛好被打斷的倒楣鬼，跟你的中斷邏輯毫無關係。

由此推出中斷 context 的鐵律：

- **不能睡，不能被排程走。** 中斷 context 不是一個可被排程的實體，你「睡」下去（讓出 CPU）之後，沒有東西能把它喚醒排回來，系統就掛了。而且你借的是別人的 stack，睡下去等於劫持了那個 task。
- **不能呼叫任何可能睡的函式。** 這是上一條的推論，而且更陰險——你不能只避開 `msleep`，還要避開**所有內部可能睡**的東西：
  - `kmalloc(..., GFP_KERNEL)`：`GFP_KERNEL` 允許為了拿記憶體而睡（去 reclaim）。中斷裡要用 `GFP_ATOMIC`——不睡、拿不到就失敗（Ch 6）。
  - `mutex_lock`：mutex 拿不到會睡。中斷裡只能用 **spinlock**（Ch 25）。
  - 任何 `copy_from_user`/`copy_to_user`：可能觸發 page fault 而睡，而且中斷 context 根本沒有合法的使用者位址空間（`current->mm` 不可信）。
- **鎖要用 `spin_lock_irqsave`。** 如果一個 spinlock 同時被 process context 和中斷 handler 搶，光用 `spin_lock` 會死鎖：process context 拿著鎖時被中斷打斷，中斷 handler 也去搶同一把鎖，它 spin 等待——但持鎖的 process 被它自己打斷了，永遠不會釋放。解法是 process context 那側用 `spin_lock_irqsave`（拿鎖前先關本地中斷），中斷就進不來，死鎖打破（Ch 25 詳解這個經典場景）。

怎麼在 code 裡判斷「我現在是不是在中斷 context」？`in_irq()`（硬體中斷 context）、`in_softirq()`、`in_interrupt()`（兩者任一）這組巨集（`include/linux/preempt.h`，靠 `preempt_count` 裡的 bit 判斷）。很多會睡的 kernel 函式內部就用 `might_sleep()` 埋了檢查——在 atomic context 呼叫它，開了 `CONFIG_DEBUG_ATOMIC_SLEEP` 會噴一坨 warning 告訴你「你在不能睡的地方睡了」。這是你調試中斷 code 的好朋友。

## 中斷親和性（IRQ affinity）

前面說中斷控制器決定「送給哪顆 CPU」——這個決定是可以調的，叫 **IRQ affinity**。每個中斷有一個 CPU 遮罩，指定它能被送到哪些 CPU：

```bash
# 看 IRQ 24 目前的 affinity（bitmask，每個 bit 一顆 CPU）
cat /proc/irq/24/smp_affinity          # 例如 f 代表 CPU 0-3
cat /proc/irq/24/smp_affinity_list     # 人類可讀：0-3

# 把 IRQ 24 綁到 CPU 2（bit 2 = 4）
echo 4 > /proc/irq/24/smp_affinity
# 或用 list 格式
echo 2 > /proc/irq/24/smp_affinity_list
```

為什麼要調？兩個典型場景：

- **快取局部性 / 隔離。** 把某個高頻中斷固定在一顆 CPU，它處理相關資料時 cache 命中率高（接 Ch 15 的 NUMA/快取話題）；反過來也能把中斷從你想留給即時任務的 CPU 上趕走，讓那顆 CPU 專心跑計算。
- **多佇列網卡的橫向擴展。** 一張支援 RSS（Receive Side Scaling）的網卡有多條 rx 佇列，每條佇列一個 MSI-X 中斷。把這些中斷分散到不同 CPU（每佇列一顆），收包就能真正並行，不會全塞在 CPU 0（接 Ch 44）。這是高效能網路的標準操作。

有個 daemon 叫 `irqbalance` 會自動幫你分散中斷。想手動精調（例如網路調優、即時系統）就先把它關掉，自己寫 `smp_affinity`，否則你剛設好它又給你改回去。

> 注意：不是每個中斷都能任意綁。有些中斷（例如 per-CPU 的 timer、GIC 的 PPI）本質上就綁死在特定 CPU，寫 `smp_affinity` 對它們無效或被拒。

## 共享中斷與中斷風暴

### 共享中斷（IRQF_SHARED）

在中斷線稀缺的年代（傳統 PCI 只有幾條 INTx 線），多個裝置得**共用一條中斷線**。線一被拉高，掛在這條線上**每一個** handler 都會被依序呼叫，各自去問自己的硬體「剛剛是不是我發的？」——是就處理、回 `IRQ_HANDLED`，不是就立刻回 `IRQ_NONE`。

要參與共享，`request_irq` 要帶 `IRQF_SHARED`，而且 `dev`（那個 cookie）**必須非 NULL**（用來在 `free_irq` 時認出拆哪一個 handler）。同一條線上所有 handler 要嘛都帶 `IRQF_SHARED`，要嘛都不帶——混著來 `request_irq` 會失敗回 `-EBUSY`。

寫共享 handler 有個紀律：**進來第一件事是判斷「這中斷是不是我的」**（讀你裝置的 status 暫存器），不是就馬上 `return IRQ_NONE`。因為線上可能有好幾個裝置，你不快速排除自己，會拖慢整條鏈。

MSI/MSI-X 出現後共享中斷少了很多——每個裝置（甚至每條佇列）可以有自己的獨立向量，不必再共用線。但老硬體和某些 SoC 上共享中斷還在。

### 中斷風暴

**中斷風暴**指某個裝置（通常是壞了或驅動有 bug）以極高頻率狂發中斷，CPU 幾乎所有時間都耗在進出中斷、什麼正事都幹不了。徵兆：`/proc/interrupts` 裡某個中斷的計數以恐怖速度暴增，系統卡到爆但 CPU 又看似「很忙」。

常見成因與防線：

- **level-triggered 中斷沒被正確 ack。** level 觸發是「線只要維持高電位就一直算有中斷」。你的 top half 如果忘了清硬體的中斷狀態（沒 ack），handler 返回後線還是高的，馬上又觸發——無限迴圈。這是新手寫驅動最容易踩的風暴。
- kernel 有個保護：一個中斷若持續大量出現但沒人認領（一直回 `IRQ_NONE`），`kernel/irq/spurious.c` 的機制會偵測到「spurious interrupt」並在累積夠多後**自動關掉這條中斷線**，避免整台機器被拖死，並在 dmesg 印警告。你在 log 看到 `Disabling IRQ #N` 就是它。

## 動手：觀察與掛一個 handler

### 看 /proc/interrupts

第一手工具。每一列一個中斷，每一欄一顆 CPU 的計數：

```
           CPU0       CPU1       CPU2       CPU3
  0:         42          0          0          0   IO-APIC   2-edge      timer
  1:          9          0          0          0   IO-APIC   1-edge      i8042
  9:          0          0          0          0   IO-APIC   9-fasteoi   acpi
 24:     130221          0          0          0   PCI-MSI   524288-edge  eth0
...
NMI:          3          2          2          1   Non-maskable interrupts
LOC:    9482910    9120334    ...                  Local timer interrupts
RES:       ...                                     Rescheduling interrupts
```

怎麼讀：

- 左欄是 Linux IRQ number；右欄是 irqchip（`IO-APIC` / `PCI-MSI` / GIC 上會是 `GICv3`）、觸發方式、和 `request_irq` 時給的 name。
- 中間各 CPU 的計數告訴你這個中斷實際落在哪顆 CPU——配合前面的 affinity，你能驗證你的綁定生效沒（例如 `eth0` 是不是全落在 CPU0，該不該分散）。
- 下面 `NMI`/`LOC`/`RES` 那幾列是架構特定的特殊中斷：`LOC` 是每顆 CPU 的 local timer（Ch 32），`RES` 是排程用的 reschedule IPI（Ch 14），`NMI` 是不可遮罩中斷。

連續看兩次相減，就知道哪個中斷正在狂跳（抓中斷風暴的第一步）：

```bash
watch -n1 'cat /proc/interrupts'
```

### 寫一個共享中斷 handler 模組

最容易在 QEMU 裡驗證、又不需要真實硬體的做法：`request_irq` 掛上一條**已存在的共享中斷線**（例如 timer 或某個平台裝置），只印個訊息證明我們被呼叫到，然後回 `IRQ_NONE`（我們不是真的裝置，不認領這個中斷，讓真正的 handler 繼續處理）。

```c
// irqwatch.c —— 掛上一條共享中斷線，觀察它被呼叫
#include <linux/init.h>
#include <linux/module.h>
#include <linux/interrupt.h>

static int irq = 1;                       // 預設觀察 IRQ 1（i8042 鍵盤）；可用模組參數換
module_param(irq, int, 0444);
MODULE_PARM_DESC(irq, "IRQ line to attach a shared handler to");

static atomic_t hits = ATOMIC_INIT(0);
static void *cookie = &cookie;            // 共享中斷要求 dev_id 非 NULL 且唯一，拿自己的位址當 cookie

static irqreturn_t irqwatch_handler(int irq, void *dev_id)
{
    // 這裡就是 top half，跑在 hardirq context：不能睡、不能 GFP_KERNEL、不能 mutex。
    // 我們只做最輕的事——原子地加個計數。真正的 handler 在鏈上其他節點。
    if ((atomic_inc_return(&hits) % 100) == 0)
        pr_info("irqwatch: IRQ %d seen %d times (in_irq=%lu)\n",
                irq, atomic_read(&hits), in_irq());
    return IRQ_NONE;                      // 不是我的裝置，不認領，讓鏈上其他 handler 繼續
}

static int __init irqwatch_init(void)
{
    int ret = request_irq(irq, irqwatch_handler,
                          IRQF_SHARED,     // 必須共享——這條線本來就有主人
                          "irqwatch", cookie);
    if (ret) {
        pr_err("irqwatch: request_irq(%d) failed: %d "
               "(該線可能不允許共享，換一條試試)\n", irq, ret);
        return ret;
    }
    pr_info("irqwatch: attached to IRQ %d\n", irq);
    return 0;
}

static void __exit irqwatch_exit(void)
{
    free_irq(irq, cookie);               // cookie 要和 request_irq 時一致，才知道拆哪個
    pr_info("irqwatch: detached, total hits = %d\n", atomic_read(&hits));
}

module_init(irqwatch_init);
module_exit(irqwatch_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Attach a shared handler to observe an IRQ line");
```

配 Ch 0 的 Makefile 編出 `irqwatch.ko`，放進 initramfs，在 QEMU 裡：

```
/ # insmod /irqwatch.ko irq=1
irqwatch: attached to IRQ 1
/ # cat /proc/interrupts        # 現在 IRQ 1 那列右邊會多出 "irqwatch"
/ # rmmod irqwatch
irqwatch: detached, total hits = 0
```

（`-nographic` 的 QEMU 沒有真鍵盤中斷，`hits` 可能是 0。想看它真的被呼叫，把 `irq` 換成一個 QEMU 裡確實有活動的中斷——先 `cat /proc/interrupts` 找一個計數在跳的、且能共享的線。重點是驗證 `request_irq`/`free_irq` 的流程和 handler 掛上鏈的機制。）

> **紀律提醒**：這個 handler 故意寫得極輕（一個 atomic 加法）。永遠不要在真實 top half 裡 `pr_info` 印一大串——printk 本身有成本，高頻中斷裡狂印會讓你的「觀察」變成中斷風暴的幫兇。上面用 `% 100` 節流就是這個原因。

### 為什麼 gdb 很難停中斷

你會想「那我 `break irqwatch_handler` 用 gdb 停下來看現場不就好了」。可以停，但要理解代價：**gdb 停下的那個瞬間，整顆（甚至整台）CPU 凍住了**。中斷 context 對時間極度敏感——你在 handler 裡一停就是幾秒（你在看變數），這期間：

- 硬體的中斷可能堆積、level 中斷可能被判定為 spurious 而被 kernel 自動關掉。
- 依賴 timer 中斷的一切（排程、jiffies、watchdog）全停擺，`continue` 之後系統的時間觀已經錯亂，可能觸發 soft-lockup / hard-lockup watchdog 直接 panic。
- 你停在 hardirq context，這裡本來就不能睡不能被排程——gdb 的單步在這種 context 行為詭異。

所以觀察中斷的正解通常**不是 gdb 停點**，而是低干擾的追蹤：`trace_printk`（進 ftrace ring buffer，比 printk 輕）、ftrace 的 `irq` events、或 `perf`（接 Ch 51、observability_tools 課）。gdb 適合停在 process context 的慢路徑，不適合停在中斷這種「你一停世界就崩」的地方。這是 Ch 0 就埋下、到這裡才完全兌現的一個原則。

## 對比與取捨

### x86 vs ARM64 中斷機制對照

| 面向 | x86_64 | ARM64 |
|---|---|---|
| 向量表 | IDT，256 項固定表（`arch/x86/kernel/idt.c`），向量號直接分派 | exception vector table，16 項按來源分類，`VBAR_EL1` 指向（`arch/arm64/kernel/entry.S`） |
| 分派方式 | 向量號本身即身分，硬體查表直達入口 | 統一 IRQ 入口，再回問 GIC「哪個 INTID」 |
| 中斷控制器 | Local APIC（每核）+ IOAPIC（主機板），MSI/MSI-X | GIC（GICv3/v4）：Distributor + Redistributor（每核）+ CPU interface |
| 中斷編號 | vector 0–255（0–31 保留給 exception） | INTID：SGI 0–15、PPI 16–31、SPI 32+ |
| 控制器存取 | MMIO（IOAPIC）+ MSR（LAPIC 部分） | 系統暫存器 `ICC_*_EL1`（GICv3 起，比 MMIO 快） |
| IPI（跨核中斷） | LAPIC 送 IPI | GIC 的 SGI |
| 不可遮罩中斷 | NMI（獨立向量 2，連 `cli` 都擋不住，走 IDT 特殊入口） | FIQ（Fast Interrupt，獨立於 IRQ 的一條例外線，常保留給 secure world / TrustZone，見 arm 課） |
| 中斷來源描述 | ACPI + MSI（無 device tree） | device tree 的 `interrupts` 屬性（Ch 39） |

上層的 `irq_desc` / irqchip / irq domain / `request_irq` 抽象**兩邊完全一樣**——這正是 Linux 這層抽象的價值：驅動程式碼跨架構不用改。差異全被壓在 irqchip driver 和 vector 入口那一層。

### 輪詢 vs 中斷 vs 混合

| 策略 | 適用 | 優點 | 缺點 |
|---|---|---|---|
| 純輪詢 | 極高頻、可預期的裝置；或超低延遲要求 | 無中斷開銷、延遲可控 | 燒 CPU、低頻時浪費 |
| 純中斷 | 一般裝置（鍵盤、一般磁碟、低速網路） | 閒時不佔 CPU | 高頻時中斷開銷成瓶頸（風暴） |
| 混合（NAPI） | 高速網卡（Ch 44） | 低頻走中斷省 CPU，高頻切輪詢批次撈 | 實作複雜 |

## 踩雷集錦

1. **以為 top half 慢一點沒關係。** 錯覺：「多印幾行 log、多算兩下沒差」。真相：top half 關著中斷跑，每多一微秒都在拉高全系統的中斷延遲、逼近丟中斷。正確認識——top half 是整個 kernel 對延遲最敏感的地方之一，能丟給 bottom half 的一律丟。

2. **在中斷 handler 裡用 `GFP_KERNEL` 或 mutex。** 錯覺：「配記憶體/上鎖到處都這樣寫」。真相：`GFP_KERNEL` 允許睡、mutex 拿不到會睡，在 hardirq context 這是直接的 bug（開 `CONFIG_DEBUG_ATOMIC_SLEEP` 會 warning，沒開就是隨機 deadlock/panic）。正確——中斷裡記憶體用 `GFP_ATOMIC`、鎖用 spinlock。

3. **把硬體向量號當成 Linux IRQ number。** 錯覺：「`/proc/interrupts` 那個 24 就是硬體中斷 24」。真相：那是 Linux IRQ number，是 irq domain 翻譯出來的虛擬號，跟 x86 vector 或 GIC INTID 不是同一個東西。搞混會讓你在讀 device tree 或 debug irq domain 時對不上號。

4. **level-triggered 中斷忘記在 top half ack 硬體。** 錯覺：「handler 跑完中斷就結束了」。真相：level 觸發下，線只要還高就一直算有中斷；你不清硬體狀態，返回後立刻又觸發，變成中斷風暴，最後被 kernel 當 spurious 關掉。正確——top half 第一要務就是跟硬體「握手」把它的中斷狀態清掉。

5. **用 gdb 停在中斷 handler 裡慢慢看。** 錯覺：「跟停一般函式一樣」。真相：你一停，timer 中斷停擺、watchdog 可能開火 panic、level 中斷可能被判 spurious 關掉，`continue` 後系統狀態已錯亂。正確——中斷觀察用 ftrace / `trace_printk` / perf 這類低干擾工具，gdb 留給 process context。

6. **共享 handler 忘了先判斷「是不是我的中斷」就直接動手。** 錯覺：「進到 handler 就是我的中斷」。真相：共享線上你的 handler 對別人的中斷也會被呼叫，你不先讀 status 暫存器排除、就去操作硬體，會亂動到別人或誤報 `IRQ_HANDLED`。正確——共享 handler 第一步讀自己的 status，不是我的立刻 `return IRQ_NONE`。

## 進階：再往深一層

- **NMI（不可遮罩中斷）的特殊性。** NMI 連 `cli`（關中斷）都擋不住，走 IDT 的獨立入口（x86 向量 2）。它用在 watchdog（偵測 CPU 卡死）、profiling（`perf` 的取樣中斷）、和嚴重硬體錯誤。NMI handler 的限制比一般中斷更嚴——連某些 per-CPU 資料的存取都要小心，因為 NMI 可能打斷一個正拿著 per-CPU 鎖的普通中斷 handler。這是 kernel 裡最難寫對的 context 之一。

- **threaded IRQ 與 -rt kernel（Ch 31 展開）。** 主線 kernel 讓你用 `request_threaded_irq()` 把 handler 主體丟到一個專屬 kernel thread（process context，**能睡**），top half 只留一個超薄的 primary handler 做 ack。`PREEMPT_RT`（即時 kernel）把這推到極致——**幾乎所有中斷 handler 都變成 threaded**，讓中斷可被搶佔、可設優先權，換取可預測的延遲。這是 top/bottom half 思想的延伸，Ch 31 專講。

- **面試常問：「為什麼中斷 context 不能睡？」** 標準答案要講到骨子裡：中斷 context 不是一個可被排程的實體（沒有對應的 `task_struct` 代表它），它借用被打斷 task 的 stack 執行。「睡」意味著呼叫 `schedule()` 讓出 CPU——但沒有東西記得要把這個中斷「排回來」，而且你讓出時劫持了別人的 stack。所以睡 = 系統掛掉。能把「借 stack」和「不可排程」兩點都講出來，就是懂了。

- **`preempt_count` 是怎麼記錄 context 的。** `in_irq()`/`in_softirq()` 不是魔法，它們讀 per-CPU 的 `preempt_count`——這個整數用不同 bit 段記錄「現在疊了幾層 hardirq / softirq / 是否禁搶佔」。進中斷時 `irq_enter()` 把 hardirq 那段 +1，離開時 `irq_exit()` -1。`might_sleep()` 就是檢查這個 count 非零時警告你在 atomic context 睡了。讀 `include/linux/preempt.h` 能看到全部位段定義。

## 動手練習

1. **讀 `/proc/interrupts` 說故事。** `cat /proc/interrupts`，挑出：哪個是 timer、哪個中斷計數最高、`LOC`/`RES`/`NMI` 各是什麼。然後 `watch -n1` 動態看，敲鍵盤（若有真鍵盤）或跑點磁碟 I/O，看哪一列在跳。目標：把每一列對應到一個真實硬體事件。

2. **改 IRQ affinity 並驗證。** 在多核機器（QEMU 給 `-smp 4`）上，挑一個計數在跳的中斷，`echo` 改它的 `/proc/irq/N/smp_affinity_list` 綁到另一顆 CPU，製造該中斷的活動，再看 `/proc/interrupts` 的計數是不是換到那顆 CPU 上跳。若改不動，判斷它是不是 per-CPU 中斷（PPI / local timer）。

3. **編出 `irqwatch.ko` 跑通掛載流程。** 用上面的模組，`insmod` 後在 `/proc/interrupts` 找到多出來的 `irqwatch` 標籤，`rmmod` 後確認它消失。故意 `insmod irqwatch.ko irq=<一個不允許共享的中斷>`，看 `request_irq` 回 `-EBUSY`，讀 dmesg 的錯誤訊息——理解為什麼有些線不能共享。

4. **（思考題，接 Ch 30）** 把 `irqwatch_handler` 裡想像成要做一件耗時的事（例如處理一個大 buffer）。你**不能**在這裡直接做——請說出你會用 softirq、tasklet、還是 workqueue，理由是「這件事需不需要睡」。這正是下一章的入口。

## 本章重點整理

- 中斷讓 CPU 不必輪詢硬體：裝置 → 中斷控制器（x86 APIC/IOAPIC、ARM64 GIC）→ CPU → 向量表（x86 IDT、ARM64 VBAR_EL1 的 vector table）→ handler。極高頻場景（NAPI）會反過來混用輪詢。
- Linux 用三層抽象把架構差異藏起來：`irq_desc`（每個 IRQ 的檔案）、irqchip（中斷控制器驅動）、irq domain（硬體 hwirq → Linux IRQ number 的翻譯，配 device tree）。驅動只碰 `request_irq(irq, ...)`。
- **top half / bottom half 分離是核心**：top half（hardirq，關著中斷跑）只做非它不可且不能拖的事（ack 硬體、搶資料），耗時工作丟 bottom half（softirq/tasklet/workqueue，Ch 30）延後做。top half 越快越好。
- 中斷 context 的鐵律（接 Ch 2）：不能睡、記憶體用 `GFP_ATOMIC`、鎖用 `spin_lock_irqsave`、`copy_from_user` 禁用；`in_irq()` 判斷 context，gdb 難停中斷（一停世界就崩）。

## 自我檢核

- [ ] 不看筆記，能畫出「裝置 → 中斷控制器 → CPU → 向量表 → top half → bottom half」整條路徑，並標出 x86 和 ARM64 各自的向量表叫什麼
- [ ] 能解釋 top half 和 bottom half 為什麼要拆、拆的界線怎麼抓（哪些事進 top half）
- [ ] 面試被問「為什麼中斷 context 不能睡」，能講出「不可被排程 + 借用別人的 stack」兩個層次
- [ ] 能區分硬體向量號（vector/INTID）和 Linux IRQ number，並說出中間靠什麼（irq domain）翻譯
- [ ] 能說出 `spin_lock_irqsave` 相對 `spin_lock` 在中斷場景多做了什麼、為什麼不做會死鎖
- [ ] 能讀懂 `/proc/interrupts` 每一欄，並用它 + `smp_affinity` 驗證中斷落在哪顆 CPU

## 延伸閱讀

### 官方文件

- **[Documentation/core-api/genericirq.rst](https://www.kernel.org/doc/html/latest/core-api/genericirq.html)**
  - **讀哪裡**：整篇。這是 Linux generic IRQ 層的權威說明，`irq_desc`、flow handler（`handle_edge_irq`/`handle_level_irq`）、irqchip 抽象的設計理由都在這
  - **和本章的關聯**：本章「Linux 的 IRQ 抽象層」那節是它的濃縮版，想搞懂 flow handler 的 mask/ack 時序回來讀這篇

- **[Documentation/core-api/irq/irq-domain.rst](https://www.kernel.org/doc/html/latest/core-api/irq/irq-domain.html)**
  - **讀哪裡**：整篇，短。專講 hwirq → Linux IRQ number 的映射機制
  - **為什麼值得讀**：irq domain 是本章最抽象的一塊，配合 Ch 39 的 device tree 一起讀才會通

### 書籍

- **《Linux Kernel Development, 3rd Ed.》** — Robert Love，第 7 章「Interrupts and Interrupt Handlers」、第 8 章「Bottom Halves and Deferring Work」
  - **這本書的定位**：把 top/bottom half 的動機和界線講得最清楚的入門書，本章的骨架跟它一致
  - **注意**：講的是舊 kernel（tasklet 那時還是主流），softirq/workqueue 的細節以 Ch 30 的 6.12 為準，但「為什麼要拆」的道理不變

- **《Understanding the Linux Kernel, 3rd Ed.》** — Bovet & Cesati，第 4 章「Interrupts and Exceptions」
  - **讀哪裡**：x86 的 IDT、gate descriptor、中斷分派的硬體細節，比 Love 深
  - **前提**：想弄懂 x86 中斷的硬體層（IDT 每一項的格式、`iret` 怎麼返回）看這本；ARM64 的 GIC 對應細節去 arm 課

### 原始碼與線上資源

- **[ARM GICv3/v4 Architecture Specification](https://developer.arm.com/documentation/ihi0069/latest/)** — ARM 官方
  - **讀哪裡**：Chapter 1（概觀）、SGI/PPI/SPI 的分類。想搞懂 ARM64 中斷必讀，本 repo 的 arm 課也會引它
  - **前提**：先讀完本章的 GIC 那節建立框架，再進去查暫存器細節

- **[Bootlin Elixir — kernel/irq/](https://elixir.bootlin.com/linux/v6.12/source/kernel/irq)** — 選 v6.12
  - **這是什麼**：本章引用的核心檔案都在這裡：`manage.c`（`request_irq`）、`handle.c`（`handle_irq_event`）、`irqdesc.c`、`irqdomain.c`、`spurious.c`（中斷風暴保護）
  - **怎麼讀**：從 `request_threaded_irq()` 開始往下追，看一個 handler 是怎麼掛進 `irq_desc->action` 鏈的

下一章我們接著拆「bottom half」的三種實作——softirq、tasklet、workqueue——搞懂它們各自跑在什麼 context、能不能睡、什麼工作該放哪一種，把這一章「丟給 bottom half」那句話落到實處。

→ [Ch 30 softirq、tasklet 與 workqueue](./30-softirq-workqueue.md)
