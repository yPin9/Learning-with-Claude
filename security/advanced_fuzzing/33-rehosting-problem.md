# Ch 33 — rehosting 問題：韌體離了硬體，怎麼 fuzz？

> **目標**：搞清楚為什麼 embedded firmware 無法像 userland binary 一樣直接丟進 afl++ 跑，理解 rehosting 光譜上各種策略的取捨，以及 P2IM、HALucinator、Fuzzware 各自解決哪段問題。

## 為什麼需要 rehosting？

x86 userland binary 丟進 afl++ 幾乎零設定就能跑：OS 提供 syscall、libc 提供堆疊、fork() 提供快速重置。整個執行環境是現成的。

embedded firmware 完全不同。一個針對 STM32F4 編譯的韌體映像做了這些假設：

- MMIO（Memory-Mapped I/O）位址如 `0x40013800`（USART1 base）可以讀寫，並且有意義地回應
- SysTick timer 每 1 ms 觸發一次，firmware 的 scheduler 依賴這個 tick
- GPIO 狀態在你寫 `GPIOA->ODR` 之後真的改變
- flash 在 `0x08000000` 開始，RAM 在 `0x20000000` 開始

這些假設在實體板子上成立。在一台沒有 STM32 的 x86 主機上，全部都是空的記憶體或直接 segfault。韌體開機第一行 code 就會卡死，更別說讓 fuzzer 把 input 餵進去。

問題的核心不是「沒有 source code」或「覆蓋率收不到」，而是**程式根本跑不起來**。這是 rehosting 要解決的本質問題。

---

## 先建立直覺

在談技術策略之前，先看清楚韌體與周邊硬體的全貌，以及 fuzzer 需要填補哪些洞。

```
 ┌────────────────────────────────────────────────────────────────┐
 │                    實體硬體板 (STM32F4)                        │
 │                                                                │
 │  ┌─────────────────┐         ┌──────────────────────────────┐ │
 │  │   MCU Core      │         │     Peripheral Bus (AHB/APB) │ │
 │  │  (Cortex-M4)    │         │                              │ │
 │  │                 │  MMIO   │  ┌──────┐ ┌──────┐ ┌──────┐ │ │
 │  │  PC / SP / LR   │◄───────►│  │USART │ │ SPI  │ │ I2C  │ │ │
 │  │  NVIC (interrupt│         │  │0x4001│ │0x4001│ │0x4000│ │ │
 │  │  controller)    │         │  │3800  │ │3000  │ │5400  │ │ │
 │  │                 │  IRQ    │  └──────┘ └──────┘ └──────┘ │ │
 │  │  SysTick timer  │◄────────│                              │ │
 │  └─────────────────┘         │  ┌──────┐ ┌──────────────┐  │ │
 │          │                   │  │ DMA  │ │ GPIO A-K     │  │ │
 │          │                   │  └──────┘ └──────────────┘  │ │
 │          ▼                   └──────────────────────────────┘ │
 │  ┌─────────────────┐                                          │
 │  │  Memory Map     │                                          │
 │  │  0x08000000 Flash (firmware image)                         │
 │  │  0x20000000 SRAM                                           │
 │  │  0x40000000 Peripheral MMIO                                │
 │  │  0xE000E000 System Control Block / NVIC                    │
 │  └─────────────────┘                                          │
 └────────────────────────────────────────────────────────────────┘

 Fuzzer 在 x86 主機上跑，需要「填補」的部分：
 ┌───────────────────────────────────────────────────────────────┐
 │  [必須模擬]  CPU 核心執行 Thumb-2 指令集                      │
 │  [必須填補]  MMIO 讀寫 → 不能 segfault，要回傳合理的值        │
 │  [必須填補]  中斷 → SysTick / USART RX IRQ / DMA 完成 IRQ    │
 │  [可能忽略]  DMA 副作用 → 把資料搬進 SRAM 的動作             │
 │  [可能忽略]  外部 sensor 協定時序 (I2C ACK/NACK 序列)         │
 └───────────────────────────────────────────────────────────────┘
```

這張圖說明了一件事：光把 CPU 模擬起來是不夠的，真正麻煩的是右側那串 peripheral 和下方的中斷。不同的 rehosting 策略，差別在於「願意模擬多少那些東西」。

---

## 核心概念

### MMIO 是什麼，為什麼麻煩

ARM Cortex-M 上，peripheral 暫存器被映射到一段固定的實體位址。韌體用普通的 load/store 指令讀寫這些位址：

```c
// 讀 USART1 的接收資料
uint32_t ch = USART1->DR;   // 展開後是 *(volatile uint32_t *)0x40013804
```

在實體板子上，這條 load 指令會觸發 AHB 匯流排，把真正的硬體暫存器值送回來。在 x86 上跑模擬器，這個位址不存在，要嘛直接 segfault，要嘛模擬器必須攔截這次存取並給一個值。

給什麼值，差別很大：
- 回傳 0 → USART 狀態暫存器為 0 → firmware 認為 「沒有收到資料」→ 永遠在輪詢等待，卡死
- 回傳正確的 `RXNE` bit set 值 → firmware 去讀 DR，拿到 input，繼續執行

### 中斷的麻煩點

很多 firmware 不用輪詢（polling），改用中斷驅動（interrupt-driven）架構。USART 收到一個 byte 就觸發 IRQ，中斷服務程式（ISR）把資料搬進 ring buffer，main loop 再去消費。

在模擬器裡，你必須在對的時機「注射」這個 IRQ，否則 main loop 永遠看到空的 ring buffer，同樣卡死。而且 IRQ 的時序如果不對，可能讓 scheduler 行為跑偏，fuzzer 餵進去的 input 到達解析函式的路徑跟真實硬體不同。

### DMA 的副作用

DMA controller 可以在不占用 CPU 的情況下把資料從 USART FIFO 搬進 SRAM。韌體可能把一個 buffer 的位址寫進 DMA destination register，然後等 DMA 完成中斷。

如果模擬器只模擬 CPU，SRAM 裡的 buffer 永遠是空的，DMA 完成中斷也不會觸發。韌體等待 DMA 完成，同樣卡死。

---

## Rehosting 光譜

四種策略從高保真到高擴展性排列：

### 策略一：Full Emulation（全系統模擬）

用 QEMU 整個模擬目標 MCU，包含每個 peripheral 的精確行為模型。

優點：如果模型寫對，行為幾乎與真實硬體一致。firmware 能跑完整個啟動流程。

缺點：
- 每個 peripheral 都要有人寫 QEMU 插件，工程量極大
- Cortex-M 的 peripheral 生態破碎，STM32/NXP/TI 各家暫存器細節都不同
- 即使有模型，精確度也難保證（timing、DMA side effect）
- 建好後難以快速換目標

適用場景：高價值單一目標，有人力投資 peripheral 模型（例如某些廠商的產品線）。

### 策略二：Partial / Hybrid Rehosting

只模擬 CPU 核心（unicorn 或 QEMU TCG），對 MMIO 位址段統一用 hook 攔截，回傳 fuzzer 控制的值或靜態的「假」值。

```
firmware 執行到 ldr r0, [r1]  (r1 = 0x40013804)
         ↓
unicorn MMIO hook 觸發
         ↓
hook callback 回傳 fuzzer input 的下一個 byte
         ↓
firmware 繼續執行
```

優點：
- 不需要精確的 peripheral 模型
- 架設成本低，unicorn 幾百行就能把 firmware 跑起來

缺點：
- 中斷時序仍需手動注射，容易搞錯
- DMA 副作用依然被忽略
- 不同 MMIO 暫存器的語義（狀態位、資料位、控制位）混在一起回傳 fuzzer bytes，會讓 firmware 走不正常的狀態機路徑

### 策略三：HAL-level Interception（HALucinator）

大多數 firmware 使用 vendor 提供的 HAL library（STM32 HAL、NXP MCUXpresso SDK）。firmware 不直接讀寫 MMIO，而是呼叫：

```c
HAL_UART_Receive(&huart1, buf, len, timeout);
```

HALucinator 的做法：不模擬 MMIO，直接在 HAL 函式的入口點設 hook，把整個函式替換成一個 「把 fuzzer input 塞進 buf」的 stub。

```
firmware 呼叫 HAL_UART_Receive()
         ↓
HALucinator hook 攔截
         ↓
stub 直接把 fuzzer 控制的 bytes 填入 buf，回傳 HAL_OK
         ↓
firmware 認為 UART 收到資料，繼續執行
```

優點：
- 完全跳過 MMIO 和中斷問題
- 韌體能跑到真正的業務邏輯（input parser、協定狀態機）
- 擴展性好，不同 firmware 只要用同一個 HAL，stub 可以複用

缺點：
- 依賴 firmware 使用 HAL，裸機直接操作暫存器的 firmware 沒用
- 需要識別 HAL function 的位址（有 symbol 簡單，stripped binary 需要 flirt/signature matching）
- Stub 的行為是「理想化」的，不會觸發 HAL 裡某些邊界情況（buffer not ready、DMA error）

### 策略四：Automatic MMIO Modeling（Fuzzware）

Fuzzware 的核心觀察：不同類型的 MMIO 暫存器有不同的「存取模式」。

```
狀態暫存器（status register）：firmware 反覆讀直到某個 bit 變 1
資料暫存器（data register）：firmware 只讀一次，直接使用
控制暫存器（control register）：firmware 只寫不讀，或設定後讀回確認
```

Fuzzware 先用空 stub（回傳 0）跑 firmware，追蹤每個 MMIO 位址的存取模式，然後用 fuzzer 輸入的 bytes 驅動這些位址，但套上符合模式的約束（例如狀態暫存器自動模擬「第 N 次讀才回傳 flag set」）。

優點：
- 不需要人工寫 peripheral 模型
- 不依賴 firmware 有沒有用 HAL
- 開箱即用的自動化程度最高

缺點：
- 初始分析階段的 overhead 較高
- 對複雜的 stateful peripheral（如 USB 協定）推斷可能不準確
- 仍然沒有處理 DMA 副作用

---

## 底層機制

### unicorn 的運作方式

```
┌──────────────────────────────────────────────────────┐
│  unicorn engine                                      │
│                                                      │
│  ┌──────────┐    ┌───────────────────────────────┐  │
│  │ TCG JIT  │    │  Hook 系統                    │  │
│  │ (QEMU    │    │                               │  │
│  │  subset) │    │  UC_HOOK_MEM_READ  → callback │  │
│  │          │    │  UC_HOOK_MEM_WRITE → callback │  │
│  │  Thumb-2 │    │  UC_HOOK_INSN     → callback  │  │
│  │  decode  │    │  UC_HOOK_INTR     → callback  │  │
│  └──────────┘    └───────────────────────────────┘  │
│                                                      │
│  記憶體：你自己 map，不存在的位址 → segfault         │
└──────────────────────────────────────────────────────┘
         │
         │ 你必須在 harness 裡：
         │  1. uc_mem_map(0x08000000, flash_size)   Flash
         │  2. uc_mem_map(0x20000000, sram_size)    SRAM
         │  3. uc_mem_map(0x40000000, periph_size)  MMIO ← 這塊要 hook
         │  4. uc_mem_write(0x08000000, firmware_bytes, ...)
         │  5. 設 SP/PC 從 vector table 讀
         │  6. uc_hook_add(MMIO 段, mmio_callback, ...)
```

unicorn 本身不知道「中斷」是什麼。ARM NVIC 的行為、pending interrupt 的 preemption、ISR vector table dispatch——這些全部要你在 `UC_HOOK_INTR` 或定時注射的方式自己實作。

### Fuzzware 的 MMIO 分類邏輯

```
 Fuzzware 觀察到的 MMIO 存取模式 → 自動分配 fuzzer input 的方式

  Pattern A: 輪詢等待
  ┌──────────────────────────────────┐
  │  loop: ldr r0, [MMIO_STATUS]    │
  │         tst r0, #FLAG           │
  │         beq loop                │
  └──────────────────────────────────┘
  → 這個位址是「狀態暫存器」
  → Fuzzware 讓它在第 N 次回傳 FLAG set，
    N 由 fuzzer input 的 1 byte 控制

  Pattern B: 單次讀取
  ┌──────────────────────────────────┐
  │  ldr r0, [MMIO_DATA]           │
  │  str r0, [r1, #offset]         │
  └──────────────────────────────────┘
  → 這個位址是「資料暫存器」
  → Fuzzware 直接用 fuzzer input 的 1-4 bytes 填入
```

---

## 進階用法

### 結合 afl++ 的 persistent mode

在 partial rehosting 架構下，可以用 unicorn 的 harness 搭配 afl++ 的 `AFL_USE_FORKSERVER=0` 或是 unicorn-mode（`afl-unicorn`）：

```bash
# afl++ 內建的 unicorn mode
# 把 unicorn harness 編譯進去，用 AFL_UNICORN_MODE
export AFL_UNICORN_MODE=1
afl-fuzz -i corpus/ -o out/ -- ./firmware_harness @@
```

harness 的設計要注意：每次 afl++ 呼叫後，要把 SRAM 和 MMIO hook 的狀態 reset 到初始值，否則 firmware 的全域變數會污染下一輪執行。

### 用 P2IM 的 field type 推斷提升 stub 品質

P2IM（Peripheral Interface Model）的方法是在 partial emulation 基礎上，透過動態分析推斷每個 MMIO field 的型別（狀態位、資料位、唯讀、唯寫），再根據型別決定給 fuzzer 多少控制權。

這個思路可以嫁接到自製 harness：先跑一輪 dry-run，記錄每個 MMIO 位址的讀寫頻率和 bit mask 使用模式，再客製化 MMIO hook 的回傳策略。

### snapshot + rehosting 結合

Ch 28–31 討論的 snapshot fuzzing 和 rehosting 是可以結合的。流程是：

1. 先用 rehosting 把 firmware 跑到「解析 input 的入口」（例如 `parse_packet(buf, len)` 的開頭）
2. 在那個點做 snapshot（unicorn 的記憶體 dump + 暫存器狀態）
3. 之後每輪 fuzzing 只從 snapshot restore，不需要重新走 firmware 的啟動流程

這讓 rehosting 的低速啟動開銷只付一次，fuzzing 速度接近 snapshot fuzzing 的水準。

---

## 對比取捨表

| 策略 | 保真度 | 建置成本 | 擴展性 | 適用條件 |
|---|---|---|---|---|
| Full QEMU emulation | 高 | 極高（需寫 peripheral 模型） | 低 | 高價值單一目標、有廠商支援 |
| Partial / unicorn hook | 中低 | 低 | 高 | 快速 PoC、單一函式 harness |
| HALucinator | 中（業務邏輯層） | 中（需識別 HAL symbol） | 中高 | firmware 使用標準 vendor HAL |
| Fuzzware | 中（自動推斷） | 低（自動化） | 高 | 通用黑盒，無 HAL symbol |
| Snapshot + rehosting | 取決於入口選擇 | 中 | 高（snapshot 後速度快） | 已知 parse 入口的場景 |

---

## 踩雷

### 「QEMU 跑起來了，代表模擬正確」

錯。QEMU 沒有目標 MCU 的 peripheral 模型時，會把整段 MMIO 位址段當作普通 RAM，讀出來全是零或上次寫進去的值。

零值的 USART status register 代表「沒有資料可讀」，firmware 的輪詢迴圈永遠拿不到 `RXNE` flag，卡在 `while(!(USART1->SR & USART_SR_RXNE));`。

fuzzer 以為有覆蓋率（firmware 有在執行），但實際上 firmware 只走到 polling loop 就停了，真正的 input parser 完全沒有被觸達。正確做法是驗證 firmware 有走到你預期的解析函式，例如在 parser 入口設一個 print hook 或覆蓋率 hit 標記。

### 「partial emulation 只要 hook 所有 MMIO 就好」

錯。中斷的 timing 和 DMA 的副作用往往被徹底忽略，導致不同種類的卡死：

- **中斷 timing**：firmware 在 `HAL_Delay(100)` 裡等 SysTick 中斷。如果 SysTick 永遠不觸發，`HAL_Delay` 永遠不返回。你以為是 firmware hang，實際上是缺了一個每 1 ms 的 IRQ 注射。
- **DMA 副作用**：firmware 把 DMA source 設成 USART FIFO，destination 設成 SRAM buffer，然後等 DMA 完成中斷。MMIO hook 可以模擬 DMA 控制暫存器的讀寫，但 SRAM buffer 裡不會真的出現資料，DMA 完成中斷也不會被觸發。firmware 等完成中斷永遠等不到。

至少要對常見的 SysTick（0xE000E010）實作一個「每 N 條指令觸發一次 IRQ」的機制，DMA 副作用則根據目標 firmware 的依賴程度決定要不要模擬。

### 「有了 unicorn 就能直接跑 full firmware image」

unicorn 是 CPU 指令集模擬器，不是系統模擬器。它沒有：
- OS / RTOS scheduler（韌體如果跑 FreeRTOS，任務切換靠 PendSV interrupt）
- Interrupt dispatch（ARM NVIC 的 tail-chaining、preemption level）
- Memory protection unit（MPU）

把整個 firmware image 丟進 unicorn，開機向量跑完之後馬上進 RTOS scheduler，scheduler 呼叫 `__asm SVC #0` 觸發 SVC interrupt，unicorn 不知道怎麼 dispatch，直接進 `UC_ERR_EXCEPTION`。

unicorn 適合用來 harness **單一函式或小段路徑**，把 firmware 中的 parser 函式切出來，給它一個乾淨的 stack 和 heap，讓 fuzzer 反覆打這個函式。對著整個 firmware image 做 full rehosting，還是要用 QEMU 或 Fuzzware 那種有更完整基礎設施的工具。

---

## 進階延伸

### DMA 建模的現有工作

Fuzzware 和 HALucinator 都沒有完整處理 DMA。2023 年後有一批後續研究嘗試加入 DMA 建模（例如 `FirmXRay` 的靜態分析路線，`µAFL` 的 native execution 路線）。如果目標 firmware 嚴重依賴 DMA（例如 Wi-Fi/BT chip firmware），要考慮這些工具或自行實作 DMA transfer stub。

### 利用 RTOS 知識加速

如果 firmware 使用 FreeRTOS、Zephyr、RTEMS 等開源 RTOS，你可以在 harness 裡辨識 RTOS 的 `xQueueReceive`、`osMessageGet` 等 API，用 stub 直接把 fuzzer input 注射進去，完全繞過 RTOS scheduler 問題。這是 HALucinator 思路的 RTOS 版本。

### Cortex-M security extensions（TrustZone-M）

較新的 Cortex-M23/M33 帶有 TrustZone-M。Secure world 和 Non-secure world 的 memory 分離讓 rehosting 更複雜。目前公開的工具大多只處理 Non-secure world；Secure world firmware 的 rehosting 仍是開放問題。

---

## 動手練習

以下練習不需要實體板子，在 x86 Linux/WSL 上完成：

1. 從 [Fuzzware artifact](https://github.com/fuzzware-fuzzer/fuzzware) 取得一個 benchmark firmware（例如 `WYCINWYC` 或 `LiteOS` 的其中一個）。用 Fuzzware 跑 30 分鐘，觀察 MMIO 位址被分類成哪些 field type，對照 firmware 的 datasheet 或 HAL source，確認分類是否合理。

2. 用 unicorn-python 自己寫一個最小的 STM32 USART stub。找一個簡單的開源 STM32 firmware（不需要真正燒錄），把 `parse_uart_packet()` 函式 harness 起來，讓 afl-unicorn 能打這個函式。重點是讓 USART status register 和 data register 的 hook 正確模擬 `RXNE` flag。

3. 比較三種 MMIO hook 策略對同一個 firmware 的行為差異：(a) 所有 MMIO 讀回傳 0，(b) 所有 MMIO 讀回傳 fuzzer input 的下一個 byte，(c) 只有 data register 用 fuzzer byte，status register 自動回傳 ready。觀察 firmware 在三種策略下各自卡在哪裡，用 `uc_hook_add(UC_HOOK_CODE)` 印出 PC 軌跡輔助分析。

---

## 本章重點

- Embedded firmware 無法直接 fuzz 的根本原因是 MMIO、中斷、DMA 這些硬體假設無法在 x86 上自動滿足，不是覆蓋率工具的問題。
- Rehosting 光譜從「全系統模擬（高保真低速）」到「HAL 攔截 / 自動 MMIO 建模（低保真高速）」，沒有萬用最佳解。
- HALucinator 攔截 vendor HAL 函式，直接把 fuzzer input 注射到業務邏輯層，跳過 MMIO 問題，但依賴 firmware 使用標準 HAL。
- Fuzzware 自動推斷 MMIO field type，不需人工建模，適合黑盒 firmware 的通用起點。
- unicorn 只是 CPU 模擬器，不能用來「全跑」一個有 RTOS 的 firmware；適合 harness 單一函式。
- Snapshot + rehosting 結合可以大幅提升 throughput，startup overhead 只付一次。

---

## 自我檢核

- [ ] 我能說出韌體無法直接在 x86 fuzz 的三個具體原因（MMIO / 中斷 / DMA）
- [ ] 我能解釋 HALucinator 和 Fuzzware 各自的核心攔截點不同在哪
- [ ] 我知道 unicorn 適合什麼場景，不適合什麼場景
- [ ] 我能說出 status register 和 data register 在 MMIO hook 中應該有不同的處理方式
- [ ] 我理解為什麼「QEMU 跑起來」不等於「模擬正確」

---

## 延伸閱讀

1. **Fuzzware: Using Precise MMIO Modeling for Effective Firmware Fuzzing**（Scharnowski et al., USENIX Security 2022）
   讀 §3「MMIO Modeling」整節。學習 Fuzzware 如何用 concolic execution 分析 MMIO 存取模式並自動分類 field type，以及為什麼「讓 fuzzer 只控制 data register 的值，而不是讓 fuzzer 控制所有 MMIO 讀取」這個決策是 throughput 大幅提升的關鍵。關聯：直接對應本章「自動 MMIO 建模」策略。

2. **HALucinator: Firmware Re-hosting Through Abstraction Layer Emulation**（Clements et al., USENIX Security 2020）
   讀 §4「HAL Function Handlers」和 §5「Firmware Re-hosting」。學習 HAL-level abstraction 如何把周邊複雜性封裝掉，以及 HAL function signature matching 的方法（LibMatch 工具）。關聯：本章「HAL-level interception」策略的一手資料。

3. **P2IM: Scalable and Hardware-independent Firmware Testing via Automatic Peripheral Interface Modeling**（Feng et al., USENIX Security 2020）
   讀 §3「Peripheral Interface Model」和 §4「Peripheral Model Instantiation」。學習 field type 推斷（status/data/control field 的自動辨識）這個先驅方法，理解 Fuzzware 是在哪些點改進了 P2IM 的方法論。關聯：了解 rehosting 學術發展的脈絡，和 Fuzzware 放在一起讀效果最好。

---

本章建立了 rehosting 問題的全貌和各策略的取捨框架。下一章聚焦在 unicorn-based harness 的實作細節：如何用 unicorn API 把 firmware 中的單一函式切出來，正確設定 stack frame、MMIO hook、exception handler，讓 afl++ 能穩定地反覆打它。

→ [下一章](./34-unicorn-harnessing.md)
