# Ch 35 — Fuzzware 與 HALucinator：自動化韌體模擬的兩條路

> **目標**: 理解韌體 rehosting 的核心瓶頸（MMIO 處理），掌握 Fuzzware 的 MMIO access modeling 四種策略與自動推斷機制，以及 HALucinator 的 HAL-level function interception 設計；能在實際工作中判斷兩者各自適用的場景並正確詮釋 fuzzing 結果。

---

## 為什麼需要

韌體 fuzzing 的核心難題不是「沒有 input vector」，而是「韌體根本跑不起來」。

PC 上的 userland binary 丟進 AFL++ 就能跑，因為所有 syscall 都有 kernel 回應。嵌入式韌體不同——它直接存取硬體暫存器（MMIO），讀 UART 狀態、等 SPI 傳輸完成、查 GPIO 電平。這些存取在 PC 上完全沒有對應物。

Rehosting（重新宿主）是把韌體從裸機搬到模擬環境讓它跑起來的過程。問題在於：模擬環境裡的 MMIO 存取該怎麼辦？

**Naive 做法一：一律回傳 0**

最簡單。但 0 通常代表「未就緒」、「錯誤」、「空」。UART 的 RXNE bit 是 0 代表「沒有資料」，韌體的 `while(!(SR & RXNE)){}` 就永遠卡住。韌體會永遠停在等待迴圈或立刻走錯誤路徑，根本進不了有趣的程式碼。

**Naive 做法二：回傳固定常數**

比 0 稍好，但仍然是靜態的。可以把 status register 固定成「永遠就緒」，讓 polling loop 跑過去。但資料暫存器也回傳固定值的話，韌體只能走單一路徑。不同的 input 永遠導向同樣的行為，fuzzing 完全無效。

**Naive 做法三：每次 MMIO read 都消耗一個 fuzzer input byte**

看似解決了「資料要從哪裡來」的問題，實際上亂度太高。

考慮一個具體場景：韌體開機後先讀 RCC_CR 等待 PLL 就緒（status read，只有 1 bit 有意義），再讀 USART_SR 等待 RXNE（另一個 status read），才讀 USART_DR 拿到一個有意義的資料 byte。如果每次 read 消耗一個 fuzzer byte，那麼 AFL++ 花了大量的 input byte 在「提供 RCC_CR 的 32-bit 值」和「提供 USART_SR 的 32-bit 值」，而這些 byte 的絕大多數組合只有一個 bit 有意義。效率極差，且 AFL++ 根本無法學到「只有 RXNE bit 決定程式走向」這件事，coverage feedback 被大量的 MMIO 存取稀釋掉。

這就是為什麼需要更精密的策略——Fuzzware 和 HALucinator 各從不同角度解這個問題。

**背景：韌體 rehosting 的研究脈絡**

這個問題在學術界大約從 2015 年開始獲得系統性關注，早期的 FirmUSB（2017）針對 USB 韌體，PROSPECT（2018）嘗試把真實板子的 peripheral 轉發給模擬器。Avatar²（2018）提供了統一的框架。

P2IM（2020）和 HALucinator（2020）是同年的兩篇突破性工作，各自選擇了不同的自動化方向。Fuzzware（2022）在 P2IM 的路線上大幅提升了模型精準度。本章重點是這條演進路線上最成熟的兩個工具：Fuzzware 和 HALucinator。

從 CVE 獵人的角度看：Fuzzware 在 USENIX 2022 論文中對 9 個真實 IoT 韌體找到了 77 個 unique crash，其中包含多個之前未知的 bug；HALucinator 論文對 8 個韌體找到了數個已知 CVE 的重現和新 crash。這兩個工具在學術環境有實際戰績，是目前最值得投入學習的韌體 fuzzing 工具。

---

## 先建立直覺

在進入兩套系統的細節之前，先對 MMIO register 的「語意類型」建立清晰的分類。

Fuzzware 四種策略的命名（constant / passthrough / bitextract / set）和這四個類型完全對應。先把類型分類做對，策略選擇就是機械性的推導。以下四個 ASCII 框對應四種策略。

注意同一個 peripheral 可能同時有多個 register 屬於不同類型——STM32 USART 的 SR（狀態暫存器）是 Type A、DR（資料暫存器）是 Type B，兩者地址相鄰，策略完全不同。Fuzzware 的 model table 以地址為 key，每個地址獨立配置策略。

```
韌體執行時的 MMIO 存取類型分布

  MMIO Read 的實際語意（以 STM32 為例）：

  Type A: Status / Flag register
  ┌──────────────────────────────────────────────────────────┐
  │  USART1_SR (0x40011000):                                  │
  │  [31:10] reserved                                         │
  │  [9] CTS  [8] LBD  [7] TXE  [6] TC  [5] RXNE             │
  │  [4] IDLE [3] ORE  [2] NF   [1] FE  [0] PE               │
  │                                                           │
  │  典型程式邏輯：while(!(USART1_SR & (1<<5))) {}            │
  │  只有 bit 5 (RXNE) 影響控制流                             │
  │  其餘 bit 對執行路徑無影響                                │
  │                                                           │
  │  → Fuzzware 策略：constant，設 RXNE=1 讓 polling 通過    │
  └──────────────────────────────────────────────────────────┘

  Type B: Data register
  ┌──────────────────────────────────────────────────────────┐
  │  USART1_DR (0x40011004):                                  │
  │  [31:9] reserved  [8:0] DR (接收到的資料)                 │
  │                                                           │
  │  典型程式邏輯：c = USART1_DR; buf[i++] = c; parse(buf)   │
  │  每個 bit 的組合都可能走不同的 parse 路徑                 │
  │  這才是 fuzzer 要控制的值                                 │
  │                                                           │
  │  → Fuzzware 策略：passthrough，消耗 fuzzer input          │
  └──────────────────────────────────────────────────────────┘

  Type C: Mixed register（flag + data 共存）
  ┌──────────────────────────────────────────────────────────┐
  │  某個自訂 peripheral 的 STATUS_DATA_REG:                  │
  │  [31:16] error_flags                                      │
  │  [15:8]  status_bits                                      │
  │  [7:0]   received_byte                                    │
  │                                                           │
  │  程式邏輯：                                               │
  │    val = REG;                                             │
  │    if (val & 0xFF000000) handle_error();                  │
  │    c = val & 0xFF;   // 實際資料                          │
  │                                                           │
  │  [31:8] 要固定成「無錯誤」，[7:0] 要 fuzz                │
  │                                                           │
  │  → Fuzzware 策略：bitextract，mask = 0xFF                 │
  └──────────────────────────────────────────────────────────┘

  Type D: Enum-like register
  ┌──────────────────────────────────────────────────────────┐
  │  RCC_CFGR (0x40023808):                                   │
  │  [3:2] SWS (System Clock Switch Status):                  │
  │        00 = HSI oscillator used                           │
  │        01 = HSE oscillator used                           │
  │        10 = PLL used                                      │
  │        11 = not applicable                                │
  │                                                           │
  │  程式邏輯：                                               │
  │    switch((RCC_CFGR >> 2) & 3) {                          │
  │      case 0: ... case 1: ... case 2: ...                  │
  │    }                                                      │
  │                                                           │
  │  只有 3 個合法值，亂填 11 讓韌體走 undefined 路徑         │
  │                                                           │
  │  → Fuzzware 策略：set，vals = {0, 1, 2}（對 SWS 欄位）   │
  └──────────────────────────────────────────────────────────┘
```

這四種類型直接對應 Fuzzware 的四種策略。分類對了，策略選擇就是水到渠成。

---

## 核心概念：兩套系統並排

### Fuzzware 的四種 MMIO Modeling 策略

**策略一：constant**

永遠回傳同一個預先決定的值，不消耗 fuzzer input。這個值通常是「讓韌體正常繼續執行」的最小條件。

適用對象：
- 晶片 ID / 版本 register（永遠是固定值）
- 時鐘就緒旗標（PLLRDY、HSERDY 之類——設成 1 讓初始化通過）
- 任何「只有一個合法值且程式假設它永遠是那個值」的 register

判斷依據：Fuzzware 靜態分析 MMIO read 後的 value usage——若該值只用於與單一常數比較、且分支結果決定「繼續或 hang」，推斷為 constant 策略最佳。關鍵指標：讀出來的值如果通過 AND mask 之後，只有一個 bit 決定控制流走向，就是 constant 候選。

**策略二：passthrough**

每次 MMIO read 從 fuzzer input byte stream 取出對應寬度（8/16/32-bit）的資料直接回傳，完整消耗 fuzzer input。對 input 無任何限制，讓 AFL++ 完全掌控這個 register 的值。

適用對象：
- 接收緩衝區對應的資料暫存器（UART DR、SPI RXDR）
- ADC 轉換結果（每個值都有語意意義）
- 任何「讀出來的值直接進入解析邏輯」的 register

判斷依據：MMIO read 的結果被傳遞進函式參數、作為陣列索引、在多個不同常數之間做比較——明顯是「資料」而非「狀態」。更強的信號：讀出來的值沒有先做 AND mask 就直接使用。

**策略三：bitextract**

從 fuzzer input 取出一個完整的值，但只把其中由 bit mask 指定的 bit 送回（其餘置 0 或設定成固定值）。消耗 fuzzer input，但只有指定的 bit 是「可變的」。

適用對象：同一個實體 register 裡混有 flag bit 和 data bit 的情況。這在自製 peripheral 或較複雜的 SoC 設計中很常見。

判斷依據：分析 MMIO read 後緊接著的 AND 操作，找出哪些 bit 被程式碼實際使用；這些 bit 的 OR 就是 bitextract 的 mask。剩下的 bit 固定成零或特定值。

舉例：某 register 讀出 32-bit 值，程式碼做 `val & 0x0000FFFF` 取低 16 bit 用，高 16 bit 從未被程式存取。bitextract 的 mask = 0x0000FFFF，fuzzer input 只填這 16 bit，其餘固定 0。

**策略四：set**

從一組有限的合法值中選一個回傳，選擇本身由 fuzzer 決定（消耗少量 input 來選 index）。

適用對象：
- 時鐘來源選擇（幾個固定的時鐘選項）
- 電源管理狀態機（幾個合法的電源模式）
- DMA 傳輸狀態（BUSY/HALF_COMPLETE/COMPLETE 等幾個固定狀態）

判斷依據：MMIO read 的結果被用於 switch/case，或被連續與一組已知常數比較（`if (x == A) ... else if (x == B) ... else if (x == C)`）。這組常數就是合法值集合，Fuzzware 從靜態分析中枚舉它們。

---

### MMIO Model 自動推斷：具體流程

Fuzzware 不要求工程師手動標記每個 MMIO register 的策略。推斷是自動的，但可以被手動覆蓋。

```
自動推斷流程：

  輸入：firmware.bin + memory_map.yml（flash/SRAM/peripheral 地址範圍）

  Step 1: Bootstrap Run
  ┌──────────────────────────────────────────────────────────┐
  │  所有 MMIO 暫時用 passthrough 策略                        │
  │  跑 N 次（用隨機 input）                                  │
  │  收集：每個 MMIO 地址的 (PC, access_width, value_used)   │
  │  其中 value_used = 讀出值如何被後續指令使用               │
  └────────────────────┬─────────────────────────────────────┘
                       │  MMIO access trace
                       ▼
  Step 2: Value Usage Analysis（每個 MMIO 地址獨立分析）
  ┌──────────────────────────────────────────────────────────┐
  │  for each mmio_addr:                                      │
  │    collect all (PC, usage_pattern) pairs                  │
  │    if usage = compare_with_single_const → constant        │
  │    if usage = direct_passthrough_to_logic → passthrough   │
  │    if usage = and_then_use(mask) → bitextract(mask)       │
  │    if usage = compare_with_set(S) → set(S)               │
  └────────────────────┬─────────────────────────────────────┘
                       │  model assignments
                       ▼
  Step 3: Model Table Generation
  ┌──────────────────────────────────────────────────────────┐
  │  輸出 config.yml 的 mmio_models 區段                      │
  │  每個 MMIO 地址 → 策略 + 參數（val / mask / vals）        │
  └────────────────────┬─────────────────────────────────────┘
                       │  refined model
                       ▼
  Step 4: Guided Fuzzing
  ┌──────────────────────────────────────────────────────────┐
  │  AFL++ 生成 input                                         │
  │  Unicorn 執行韌體，MMIO read → 套用 model                 │
  │  Coverage bitmap 回饋給 AFL++                             │
  │  Input 使用率大幅提升（status register 不再消耗 input）   │
  └──────────────────────────────────────────────────────────┘
```


Fuzzware 的整體 pipeline 就是上面四步的串接：`firmware.bin + memory_map.yml` → Bootstrap Emulation（全 passthrough）→ Model Inference（value usage 分析）→ 產出帶 `mmio_models` 的 `config.yml` → AFL++ 導引的 Unicorn fuzzing loop，輸出 `crashes/`、`queue/`、`stats/`。

---

### HALucinator 的核心思想

Fuzzware 的問題在於它仍然在模擬硬體——只是更聰明地模擬。遇到沒有明顯 value usage pattern 的 MMIO（例如 DMA descriptor table、複雜的 multi-register 協議），自動推斷就失效了。

HALucinator 的思路根本不同：**不模擬 peripheral，攔截 HAL library 的函式呼叫**。

這個想法的成立前提是觀察：大多數商業韌體不直接操作 MMIO register。現代韌體開發幾乎都依賴 HAL（Hardware Abstraction Layer）：

- STM32CubeF4 提供 `HAL_UART_Receive()`、`HAL_SPI_TransmitReceive()`、`HAL_I2C_Master_Receive()` 等
- ESP-IDF 提供 `uart_read_bytes()`、`spi_device_transmit()` 等
- NXP SDK 提供 `UART_ReadBlocking()` 等

這些函式的介面語意非常清楚——輸入是 buffer 和 size，輸出是填充好的 buffer 和狀態碼。和底層的 MMIO 存取序列完全解耦。

如果能攔截這些函式，讓它們「直接從 fuzzer input buffer 取資料回傳」，就等同於完整模擬了它們背後的所有硬體行為，且不需要了解任何 MMIO 細節、不需要建立任何 MMIO model。

**Function Handler 詳解**

Handler 是 Python 函式，由 Avatar² 在韌體執行到特定地址時呼叫。Handler 的工作是：

1. 讀取函式的輸入參數（從 ARM register R0-R3，遵循 AAPCS）
2. 從 fuzzer input buffer 取出適當數量的資料
3. 將資料寫入韌體的記憶體（通常是 buffer 指標指向的位置）
4. 設定回傳值（寫入 R0）
5. 通知 Avatar² 跳過原函式、直接返回呼叫者

HALucinator 預建的 handler 庫涵蓋 STM32 HAL 的主要 IO 函式：UART、SPI、I2C、ADC、GPIO 等。

**LibMatch：從 stripped binary 辨識 HAL function**

實際韌體 binary 幾乎都沒有 symbol（strip 掉了）。HALucinator 需要知道「`HAL_UART_Receive` 在這個 binary 的哪個地址」才能掛 hook。LibMatch 解決這個問題。

LibMatch 的核心觀察：同一個版本的 STM32 HAL 編譯出來的函式，在不同韌體裡 byte pattern 高度相似（除了 branch target 等 relocation 相關的欄位）。

具體步驟：
1. 從 STM32CubeF4/F3/L4 等 SDK 取得 HAL library 的 compiled object files（有 symbol）
2. 對每個 HAL function，提取 byte pattern，遮蔽所有 relocation 相關的欄位（branch offset、PC-relative load address 等）
3. 建立 {function_name: masked_byte_pattern} 的 signature 資料庫
4. 對目標 stripped binary 做滑動視窗比對，計算每個對齊點的 similarity score
5. Score 超過閾值的點視為匹配，輸出 {binary_address: hal_function_name}

LibMatch 對大函式（> 30 指令）效果好，因為 mask 後的 pattern 夠長、夠獨特。短函式（< 10 指令）的 pattern 太短，假陽性率高。

---

### HALucinator 整體架構

```
HALucinator Pipeline

  ┌──────────────────────────────────────────────────────────┐
  │  Inputs:                                                  │
  │    firmware.bin         (stripped 韌體)                   │
  │    hal_library/         (STM32CubeF4 等 SDK 的 .a/.o)    │
  │    hal_handlers/        (Python handler 函式庫)           │
  └────────────────────┬─────────────────────────────────────┘
                       │
                       ▼
  ┌──────────────────────────────────────────────────────────┐
  │  LibMatch                                                 │
  │    - 建立 HAL function signature DB                       │
  │    - 滑動視窗比對 firmware.bin                            │
  │    - 輸出：function_map.yaml                              │
  │             {0x08001234: "HAL_UART_Receive",              │
  │              0x08001890: "HAL_SPI_TransmitReceive", ...}  │
  │    - 人工審查（確認高分匹配，剔除明顯誤報）               │
  └────────────────────┬─────────────────────────────────────┘
                       │  function_map.yaml（已審查）
                       ▼
  ┌──────────────────────────────────────────────────────────┐
  │  Avatar² + Unicorn 初始化                                 │
  │    - 載入 firmware.bin 到 Unicorn 記憶體                  │
  │    - 依 function_map.yaml 在每個地址插入 breakpoint hook  │
  │    - 設定 fuzzer input buffer（共享記憶體或 pipe）         │
  └────────────────────┬─────────────────────────────────────┘
                       │
                       ▼
  ┌──────────────────────────────────────────────────────────┐
  │  Fuzzing Execution Loop                                   │
  │                                                           │
  │  韌體執行 ─────────────────────────────────────────────→  │
  │       │                                                   │
  │       ├─ 正常指令：Unicorn 直接執行                       │
  │       │                                                   │
  │       └─ call HAL_UART_Receive (地址命中 hook)            │
  │              │                                            │
  │              └→ Python handler 被呼叫                     │
  │                    ├ 讀 R1 (pData buffer ptr)             │
  │                    ├ 讀 R2 (Size)                         │
  │                    ├ 從 fuzzer input 取 Size bytes        │
  │                    ├ write_memory(R1, bytes)              │
  │                    ├ 寫 R0 = HAL_OK (0)                   │
  │                    └ 返回呼叫者（跳過真實 HAL 實作）      │
  │       │                                                   │
  │  繼續執行 ───────────────────────────────────────────────→ │
  │       │                                                   │
  │       └─ 若發生 crash (invalid memory access 等)          │
  │              → 記錄 crash + 當時的 fuzzer input           │
  │                                                           │
  │  Coverage bitmap 回饋給 fuzzer                            │
  └──────────────────────────────────────────────────────────┘

  攔截點在韌體的角度：

  main()
   └→ init_uart()
   └→ while(1) {
         HAL_UART_Receive(...)    ← hook 在這裡攔截
              │
              └→ [handler 取 fuzzer input，直接返回]
              │
         process_received_data(buf)  ← fuzzer 控制 buf 的內容
              │
              └→ parse logic（這裡才是我們要 fuzz 的地方）
      }
```

---

## 底層機制深挖

### Fuzzware 的 Unicorn Hook 實作細節

Fuzzware 使用 Unicorn 的 `UC_HOOK_MEM_READ` hook。每次韌體執行 load 指令讀取記憶體時，hook 觸發並判斷地址是否在 MMIO 範圍。

Hook callback 的邏輯：

```
on_mmio_read(uc, access, address, size, value, user_data):
    if address not in mmio_range:
        return  # 正常記憶體 read，不干預

    model = get_model(address)

    if model.type == CONSTANT:
        result = model.val

    elif model.type == PASSTHROUGH:
        result = fuzzer_input.read(size)  # 消耗 size bytes

    elif model.type == BITEXTRACT:
        raw = fuzzer_input.read(size)
        result = raw & model.mask  # 只保留 mask 指定的 bit

    elif model.type == SET:
        idx = fuzzer_input.read(1) % len(model.vals)
        result = model.vals[idx]

    # 把 result 寫回 Unicorn 的記憶體，讓 load 指令讀到它
    uc.mem_write(address, result.to_bytes(size, 'little'))
```

關鍵細節：hook 在 load 指令執行之前觸發，Fuzzware 把 `result` 寫入模擬的記憶體，然後 Unicorn 正常執行 load 指令讀出它。韌體看不到 hook 的存在，行為和真實硬體上一致（從它的角度看）。

### Avatar² 的 Function Hook 機制

Avatar² 在指定地址設置 breakpoint，當 Unicorn 執行到該地址時暫停，並呼叫 Python callback（即 HALucinator handler）。Handler 執行完後，Avatar² 把 PC 設成 link register（LR）的值，讓 Unicorn 繼續執行——效果等同於 `BX LR`（函式返回）。

這個機制完全在模擬器外部實作，不需要修改韌體 binary 或 Unicorn 本身。代價是每次 hook 觸發都有 Python 呼叫的 overhead，比純 Unicorn 執行慢一個數量級。

### 兩者的 Coverage Feedback 差異

Fuzzware 和 HALucinator 都需要 coverage feedback 讓 AFL++ 做有效的 mutation。

Fuzzware 用 Unicorn 的 `UC_HOOK_BLOCK` hook，在每個 basic block 開始時記錄 PC，產生 AFL++ 格式的 coverage bitmap。這和 AFL++ 的 instrumented binary 語意等價。

HALucinator 也透過 Avatar² 掛 block coverage hook，但因為 Python callback overhead，整體執行速度比 Fuzzware 慢。HALucinator 的原始論文用 LibFuzzer 而非 AFL++，透過 persistent mode 減少 fork overhead。

### Unicorn 執行速度的限制與含義

Unicorn Engine 是純軟體的 CPU 模擬器，沒有 JIT 加速（不像 QEMU 的 TCG）。Cortex-M 韌體在 Unicorn 上的執行速度大約是原生速度的 1/100 到 1/1000，取決於指令類型和 hook 數量。

對 fuzzing 的影響：
- Fuzzware：每秒大約 500–5000 executions（取決於韌體複雜度和 MMIO hook 頻率）
- HALucinator：因為 Python handler 呼叫 overhead，每秒大約 100–500 executions

與 AFL++ 跑 native binary（每秒數萬 executions）相比，韌體 fuzzing 的速度劣勢非常顯著。這意味著需要更長的 fuzzing 時間（數天而非數小時）才能達到有意義的 coverage，且 seed corpus 的品質對最終結果的影響遠比 native fuzzing 大。

這也是為什麼 MMIO model 的精準度如此重要——每次 MMIO read 消耗的 input byte 越少、每個 fuzzer execution 探索的路徑就越集中，有限的執行次數才能產生有意義的 coverage。

---

## 進階用法

### Fuzzware：手動補充 MMIO model

自動推斷不是萬能的。當韌體某個 peripheral 在 bootstrap emulation 階段完全沒被執行到，自動推斷就不會產生該 peripheral 的 model——它的 MMIO read 會使用預設策略（通常是 passthrough），造成不必要的 input 消耗和錯誤行為。

在 `config.yml` 手動補充：

```yaml
# fuzzware config 片段
mmio_models:
  # USART1 STATUS register：讓 TXE 和 TC 永遠就緒
  - addr: 0x40011000
    model: constant
    val: 0x000000C0   # TXE=1 (bit 7), TC=1 (bit 6)

  # USART1 DATA register：從 fuzzer input 取資料
  - addr: 0x40011004
    model: passthrough

  # RCC CLOCK CONTROL：PLL 和 HSE 就緒旗標永遠置位
  - addr: 0x40023800
    model: constant
    val: 0x03035083   # PLLRDY=1, HSERDY=1, HSIRDY=1

  # RCC CLOCK CONFIGURATION：從合法時鐘來源中選
  - addr: 0x40023808
    model: set
    vals: [0x00000008, 0x00000009, 0x0000000A]  # HSI/HSE/PLL selected

  # SPI STATUS REGISTER：BSY=0, TXE=1, RXNE=1
  - addr: 0x40013000
    model: constant
    val: 0x00000003

  # SPI DATA REGISTER：從 fuzzer input 取
  - addr: 0x40013008
    model: passthrough
```

判斷「需要手動補充哪些 MMIO」的方法：

1. 先跑 Fuzzware 並觀察 coverage 成長曲線
2. Coverage 在很低的水位就不再成長 → 韌體被卡在某個地方
3. 在 coverage 停止成長前的最後幾個 basic block，看它們在讀哪個 MMIO
4. 對那個 MMIO 加 constant model（猜「就緒」狀態），再觀察 coverage 是否回升

### HALucinator：擴充 handler 庫

遇到預設 handler 沒覆蓋的 HAL 函式時，按以下模板自己寫（以 `HAL_I2C_Master_Receive` 為例，AAPCS：R0=hi2c, R1=DevAddr, R2=pData, R3=Size）：

```python
class STM32I2C(BpHandler):
    @bp_handler(['HAL_I2C_Master_Receive'])
    def hal_i2c_master_receive(self, qemu, bp_addr):
        pData = qemu.regs.r2
        size  = qemu.regs.r3
        data  = self.get_peripheral_data(size)   # 從 fuzzer input 取
        qemu.write_memory(pData, size, data, raw=True)
        return False, 0x00  # HAL_OK
```

注意：查 HAL 標頭確認結構 layout（`__packed__` 或 compiler padding 會影響 offset）；考慮加入 20% 機率回傳 `HAL_TIMEOUT`（0x03）以覆蓋錯誤處理路徑；R4 以後的參數在 stack 上。

### 結合兩者：分層覆蓋策略

實際韌體 fuzzing 工作流程中，兩者通常是互補的：

```
韌體執行層次：

  [ 應用邏輯層 ]         ← HALucinator 主戰場
        │                  （parse、處理 HAL 回傳的資料）
        ▼
  [ HAL 函式層 ]         ← HALucinator 攔截點
        │                  （HAL_UART_Receive 等）
        ▼
  [ 驅動/HAL 實作層 ]    ← 初始化序列，Fuzzware 的領域
        │                  （設置 MMIO register、等待 flag）
        ▼
  [ 硬體 MMIO 層 ]       ← Fuzzware 的 model 層
```

建議流程：
1. 先跑 HALucinator，快速 fuzz 應用層邏輯，找 parser bug
2. 若 HALucinator 找不到足夠的 coverage（HAL 識別失敗或 handler 缺失），切換 Fuzzware
3. 找到 crash 後，在 QEMU + 完整 peripheral 模型（或真實板子）上驗證可重現性

---

## 對比取捨表

| 維度 | Fuzzware | HALucinator |
|---|---|---|
| 需要 HAL 知識 | 不需要 | 需要（確認韌體用哪個 HAL 及版本）|
| 需要知道 MMIO 位址 | 不需要（自動偵測記憶體範圍）| 不需要 |
| 對 stripped binary 的適應性 | 直接可用，不需要 symbol | 需 LibMatch，短函式誤報率高 |
| 覆蓋範圍 | 以 MMIO access path 為主 | 以 HAL call site 以上的邏輯為主 |
| 底層初始化程式碼覆蓋 | 可覆蓋（直接操作 MMIO 的程式碼）| 通常無法攔截（HAL 呼叫之前）|
| 自製 HAL / 裸機驅動 | 可處理（只要有 MMIO access）| 無法處理（沒有標準 HAL 可 match）|
| 人工介入量 | 低（只需要 memory map）| 中（LibMatch 結果需人工審查）|
| 模型準確度 | MMIO 值層面的近似 | HAL 函式語意層面的近似 |
| 假陽性主要來源 | MMIO model 允許了不可能的值 | Handler 只實作成功路徑，漏掉錯誤語意 |
| 執行速度（execs/sec）| 中等（Unicorn hook overhead）| 較慢（Python handler 呼叫 overhead）|
| 適合目標 | 任意 Cortex-M 裸機韌體 | 廣泛使用 STM32 HAL / ESP-IDF 的韌體 |
| 不適合目標 | 幾乎沒有 MMIO 的韌體 | 自製 HAL 或直接裸機操作的韌體 |
| 環境依賴 | Docker + Python 3.8 | Avatar² + Python 3.7+ |
| 已知 CVE 發現成果 | 77 unique crashes on 9 IoT firmware（論文）| 多個 CVE 重現 + 新 crash（論文）|
| 適合 Cortex-A（Linux 韌體）| 不適合（設計針對裸機 Cortex-M）| 不適合（HAL 概念不同，handler 庫不覆蓋 Linux HAL）|
| 社群活躍度 | 有 GitHub，論文後持續維護 | 較舊（2020），維護較少 |

---

## 踩雷

**踩雷一：「Fuzzware 會自動建模所有 MMIO」**

只有在 bootstrap emulation 階段被執行路徑碰到的 MMIO 才會產生 access trace，才會被建模。

這個限制比想像中嚴重。許多韌體的架構是：先完成所有 peripheral 初始化，再進入主循環。如果初始化序列中某個 peripheral 的 status check 沒有通過（因為 model 不存在或 model 值錯誤），初始化就卡死——後面所有依賴這個 peripheral 的程式碼永遠不會執行，它們的 MMIO 也永遠不會被建模。

更隱蔽的情況：韌體成功完成初始化、進入主循環、開始等待網路封包——但 Bluetooth PHY 的 MMIO 從未被建模，因為 bootstrap run 的隨機 input 從未讓韌體進入 Bluetooth 接收路徑。

解法：定期檢查 coverage bitmap，辨識「有哪些 function 從未被 fuzz 到」，追溯到對應的 MMIO 地址，手動加 model。

**踩雷二：「HALucinator handler 的語意和真實 HAL 一致」**

Handler 是人寫的抽象，不是真實 HAL 的模擬。真實 `HAL_UART_Receive` 有以下行為，預設 handler 完全沒有：

- Timeout 超時時回傳 `HAL_TIMEOUT`（0x03），並把已接收的 byte 數寫回 `pSize`
- 發生 overrun error 時回傳 `HAL_ERROR`（0x01），設定 `hrtc->ErrorCode`
- DMA 模式下的資料傳輸是非同步的，實際資料到位時透過 callback 通知

預設 handler 永遠回傳 `HAL_OK`（0x00）、永遠填滿整個 buffer。韌體的所有錯誤處理路徑永遠不會被 fuzz 到——這些路徑裡的 bug 對 HALucinator 是盲點。

解法：review handler 庫的實作，在關鍵 handler 裡加入錯誤路徑模擬。至少要覆蓋 timeout 和 partial receive 兩種情況。

**踩雷三：「跑 Fuzzware 找到 crash = 真實 bug」**

MMIO model 是近似值，近似值可能產生真實硬體上不可能出現的 MMIO 值組合，進而觸發只在模擬器裡存在的 crash。

以 bitextract 策略為例：Fuzzware 分析出某 register 的 [7:0] 是有意義的資料，[31:8] 應該固定為 0。但如果分析錯了，把某個實際上只能是 0 的 bit 也放進 fuzzer 控制範圍，就可能產生「bit X 為 1 同時 bit Y 為 1」的值——而真實硬體保證這個狀態永遠不存在。

韌體的防禦性程式碼可能對這種「不可能的狀態」沒有保護（因為工程師預設硬體永遠不會產生它），導致 crash。但這個 crash 在真實板子上永遠無法重現。

驗證流程：
1. 拿到 Fuzzware 的 crash input，重放並記錄所有 MMIO read 的回傳值序列
2. 對每個 MMIO read，查 datasheet 確認「這個地址在這個時間點」可能回傳的值集合
3. 若 crash-triggering 的 MMIO 值不在可能的集合裡，這個 crash 是假陽性
4. 修正 MMIO model（縮小 passthrough 範圍，改用 set 或 bitextract）後重新 fuzz

在沒有真實板子的情況下，可以用 QEMU + 更完整的 peripheral 模型（如 qemu-system-arm 的 STM32 machine）做二次驗證。

**踩雷四：「LibMatch 識別成功就不用管了」**

LibMatch 輸出 function map 後，工程師常常直接使用、沒有逐一驗證。問題在於：

- 高分匹配（score > 0.9）幾乎都是正確的，但低分匹配（0.6–0.8）可能是不同版本的同名函式、或完全不同的函式恰好有相似的 byte pattern
- 如果 hook 掛在錯誤的地址（LibMatch 把地址 A 識別為 `HAL_UART_Receive`，但實際上那裡是 `HAL_CRC_Calculate`），handler 會把 fuzzer input 寫入 CRC buffer 而非 UART buffer，韌體的行為完全不可預測
- 更危險的是：hook 掛在錯誤地址可能讓韌體看起來「正常運行」（沒有 crash），但 UART 路徑從未被 fuzzer 覆蓋，等於浪費了所有 fuzzing 時間

解法：對每個匹配逐一驗證。方法是在 handler 裡加 log，確認 handler 被觸發的頻率和時機是否符合預期（例如 `HAL_UART_Receive` 應該在每次主循環迭代都被呼叫一次，若呼叫頻率異常就是掛錯了）。

---

## 安裝與驗證

**本段未實測，為理論預期行為。** 以各工具 GitHub 的最新 README 為準。

**Fuzzware**（依賴複雜，Docker 是最可靠入口）：

```bash
docker pull fuzzware/fuzzware
docker run -it --rm fuzzware/fuzzware \
  fuzzware pipeline /fuzzware/targets/arduino-usbserial/config.yml --run-for 2h
# 驗證：跑後 config.yml 應出現自動生成的 mmio_models 區段
# Repo: https://github.com/fuzzware-fuzzer/fuzzware
# 注意：Python 3.8 依賴，Ubuntu 22.04+ 需手動安裝；Apple Silicon 加 --platform linux/amd64
```

**HALucinator**（需 Avatar²  + STM32CubeF4 SDK）：

```bash
pip3 install avatar2
git clone https://github.com/embedded-sec/halucinator && cd halucinator && pip3 install -e .
# 建 LibMatch DB（需要 STM32CubeF4 SDK 的 .c source）
python3 -m halucinator.lib_match.build_db \
  --hal-dir /opt/stm32cubef4/Drivers/STM32F4xx_HAL_Driver/Src \
  --output stm32f4_hal.pkl
# 識別目標韌體的 HAL 函式
python3 -m halucinator.lib_match --binary target.bin --db stm32f4_hal.pkl --output function_map.yaml
# 驗證：Avatar² log 應顯示 "Hooked HAL_UART_Receive at 0x08001234"
```

---

## 進階延伸

**多韌體 corpus 共享**：同廠商多版本（v1.0/v1.1/v1.2）的 MMIO model 通常跨版本兼容，可共用同一份 `config.yml`，三個版本的 fuzzing corpus 透過 AFL++ 的 `-S`/`-M` distributed mode 互相餵食。同一個 parsing library（如某 BLE parser）被多個廠商韌體嵌入時，對一個建好 corpus 再移植到其他目標，大幅節省啟動成本。

**Ghidra FID 替代 LibMatch**：Ghidra 的 Function ID（FID）資料庫比 LibMatch 的 byte-level 滑動視窗更準確，因為它用函式呼叫圖特徵，對 -O2/-O3 優化下的 inlining 更穩健。建立 STM32 HAL 的 FidDb 後直接匯出 {地址: function name}，跳過 LibMatch 的整個流程。

**Coverage 停滯診斷**：依照「最後停住的 PC 在哪裡」診斷根因：
- 停在 peripheral 初始化程式碼 → 缺 MMIO model，手動加 constant
- 停在 HAL 函式內部 → LibMatch 誤報，hook 掛在錯誤地址
- 停在 parse 函式外 → 缺 seed corpus（合法封包），fuzzer 無法突破格式關
- 停在 main loop idle → MMIO/HAL hook 根本沒觸發，確認 hook 地址

**Taint Analysis 輔助 crash triage**：在 Fuzzware 的 Unicorn 層對 passthrough MMIO read 的結果加 taint 標記，追蹤到 crash 點，輸出「crash 由 input 的第 N–M byte 觸發」——這個資訊對 PoC 精簡和 root cause analysis 都非常有用。具體實作可在 `UC_HOOK_MEM_READ` callback 裡標記 tainted range，在 `UC_HOOK_MEM_WRITE/READ_INVALID` callback 裡檢查觸發地址是否 tainted。

---

## 動手練習

**練習 1：MMIO model 策略分類**

以下是幾個 STM32 暫存器，根據 STM32F4 Reference Manual 的說明，判斷應使用哪種 Fuzzware 策略，並說明理由：

- `TIM2->CNT`（32-bit timer 計數器，韌體讀它來計算時間差，與固定的 deadline 比較）
- `ADC1->DR`（12-bit ADC 轉換結果，讀出後直接進入閾值比較邏輯）
- `ADC1->SR`（bit 1 = EOC，轉換完成旗標，韌體 polling 等待它變 1）
- `FLASH->SR`（busy bit + 錯誤旗標，韌體在 flash 寫入後 polling 等待 busy 清零）
- `RNG->DR`（32-bit 硬體亂數，每次讀取回傳新的亂數值，用於初始化某個 seed）

對每個 register，說明你選擇的策略，以及「哪些 bit 決定了控制流、哪些 bit 對 fuzzing 有意義」。

**練習 2：手寫 HALucinator handler**

為以下函式寫 HALucinator handler：

```c
HAL_StatusTypeDef HAL_I2C_Master_Receive(
    I2C_HandleTypeDef *hi2c,  // R0: 不需要
    uint16_t DevAddress,      // R1: 目標裝置地址（不需要）
    uint8_t *pData,           // R2: 接收 buffer
    uint16_t Size,            // R3: 要接收的 byte 數
    uint32_t Timeout          // stack: 超時（模擬器中忽略）
);
```

要求：
1. 從 fuzzer input 取 `Size` bytes 填入 `*pData`
2. 有 20% 機率回傳 `HAL_TIMEOUT`（0x03）而非 `HAL_OK`（0x00），以觸發錯誤處理路徑
3. 說明 `Size` 參數從 R3 取得（不是 stack，因為 AAPCS 前四個參數用 R0-R3）

**練習 3：假陽性分析**

Fuzzware 在某 STM32F4 韌體上找到 crash，重放時的 MMIO read 序列：

```
時間點 1: 0x40011000 (USART1_SR) 讀取 → 模型回傳 0x0000007F
          （7 個 flag bit 全部為 1：ORE=1, NF=1, FE=1, PE=1 等）

時間點 2: 0x40011004 (USART1_DR) 讀取 → 模型回傳 0x41 ('A')

韌體行為：if (SR & ORE) { clear_error(); }  // 清除 overrun
          c = DR;                             // 讀資料
          if (c == 'A') { ... crash ... }
```

問題：
1. `USART1_SR = 0x7F` 代表 ORE、NF、FE、PE 同時為 1。查 STM32F4 RM，這四個錯誤能否同時出現？
2. 假設這個值在真實硬體上不可能出現，這個 crash 是假陽性嗎？
3. 應該如何修正 MMIO model，讓 `SR` 只產生硬體上可能的值組合？

---

## 本章重點

- MMIO 解法光譜：回傳 0（最差）→ 一律 passthrough（低效）→ Fuzzware 自動 model（有效）→ HALucinator HAL 攔截（跳過 MMIO 問題）
- Fuzzware 四種策略（constant/passthrough/bitextract/set）由靜態 value usage pattern 自動推斷，並支援手動覆蓋
- HALucinator 洞見：HAL 函式語意比 MMIO 語意穩定，直接攔截 HAL call site 效率更高
- LibMatch 對大函式效果好，短函式誤報率高，結果需人工審查
- Fuzzware 假陽性：MMIO model 允許真實硬體不可能的值（尤其 passthrough 誤用在 status register）
- HALucinator 漏報：handler 只覆蓋成功路徑，錯誤處理（timeout/partial receive）是盲點
- 兩者互補不互斥：底層初始化用 Fuzzware，應用層 parser 用 HALucinator，crash 確認用真實板子
- Unicorn 比 native 慢 100–1000 倍；MMIO model 精準度比提升執行速度更關鍵

---

## 自我檢核

- [ ] 能說明為什麼「一律回傳 0」和「一律 passthrough」都不是好的 MMIO 策略
- [ ] 能對任意 STM32 暫存器根據 datasheet 說明應選哪種 Fuzzware 策略及理由
- [ ] 能描述 Fuzzware 的三個執行階段（bootstrap → model inference → guided fuzzing），含每階段的輸入/輸出
- [ ] 能說明 HALucinator 為什麼不需要建模 MMIO，以及對自製 HAL 韌體為什麼無效
- [ ] 能說明 LibMatch 的工作原理，以及短函式誤報率高的根本原因
- [ ] 知道 Fuzzware 假陽性的機制（MMIO model 允許不可能的值），並能描述驗證流程
- [ ] 知道 HALucinator handler 遺漏的語意（錯誤路徑、timeout），以及如何修補
- [ ] 能從 coverage 停滯的症狀推斷根因並給出解法

---

## 延伸閱讀

1. **Fuzzware 原論文：Fuzzware: Using Precise MMIO Modeling for Effective Firmware Fuzzing (USENIX Security 2022，Scharnowski et al.)**
   讀 §3（MMIO Modeling）看四種策略的形式化定義和推斷演算法的數學描述；讀 §5（Evaluation）看與 P2IM、μEmu 的覆蓋率對比，特別注意「input usage ratio」這個指標的定義——它量化了「有多少 fuzzer input 真正進入了程式邏輯，而非被 MMIO status read 浪費掉」。理解這個指標是評估 MMIO model 品質的最直接方法。

2. **HALucinator 原論文：HALucinator: Fuzz Testing of Binary-Only Embedded Firmware with Abstracted Hardware Access (USENIX Security 2020，Clements et al.)**
   讀 §4（Architecture）了解 Avatar² 整合的完整設計和 function handler 的執行模型；讀 §5（LibMatch Implementation）看 signature 建構的具體細節、已知限制，以及在 stripped binary 上的 false positive rate 數據。特別注意作者對「handler completeness」的討論——他們誠實承認 handler 是語意近似而非完整模擬，並量化了因此產生的 coverage 損失。

3. **Avatar² 論文：Avatar²: A Multi-Target Orchestration Platform (BAR Workshop @ NDSS 2018，Muench et al.)**
   HALucinator 的底層基礎設施。了解 Avatar² 的 multi-target 設計（可同時協調 QEMU、Unicorn、OpenOCD/JTAG 多個後端），以及它的「forwarding」模式（讓真實板子協助模擬器處理它搞不定的 peripheral）。理解 Avatar² 為什麼被選為 HALucinator 的框架，以及它的 Python hook 機制的實作代價（overhead 的來源）。GitHub：https://github.com/avatartwo/avatar2

4. **μEmu 論文：Automatic Firmware Emulation through Abstracted I/O Generalization (CCS 2021，Zhou et al.)**
   第三條路，和 Fuzzware 對比閱讀。μEmu 不建模 MMIO 語意，改用符號執行找出「讓執行繼續前進所需的最小 MMIO 回傳值約束」——本質上是讓符號執行器代替 fuzzer 解 MMIO 值的「讓韌體往下跑」問題。與 Fuzzware 的 value usage analysis 對比，理解兩種方法在覆蓋率和誤報率上的取捨。

5. **P2IM 論文：P2IM: Scalable and Hardware-independent Firmware Testing via Automatic Peripheral Interface Modeling (USENIX Security 2020，Feng et al.)**
   Fuzzware 的主要比較對象之一。P2IM 也做自動 MMIO modeling，但用的是「執行時動態推斷 + 隨機回傳值」而非靜態分析 value usage。讀它是為了理解 Fuzzware 的貢獻是什麼，以及為什麼靜態分析 value usage 比隨機回傳更有效。特別注意兩篇論文在 coverage 和 bug 發現數量上的對比數據。

---

**橫向路標**：security/arm（Cortex-M MMIO 架構，`volatile uint32_t *` 存取模式）；security/embedded/protocols（UART/SPI/I2C 狀態機，判斷 STATUS register 哪些值「物理上可行」的基礎）；Ch 34（Unicorn hook 機制，Fuzzware/HALucinator 的底層）；Ch 33（rehosting 問題全景，本章是那裡提出問題的兩個解答）；Ch 41（SymCC/SymQEMU，符號執行和 rehosting 結合的未來方向）。

---

→ [下一章](./36-firmware-fuzzing-practice.md)
