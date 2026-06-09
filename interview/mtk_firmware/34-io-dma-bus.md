# Ch 34 — I/O 與 DMA、匯流排

> **目標**：搞懂 CPU 怎麼和 I/O 裝置溝通——polling、interrupt-driven、DMA 三種方式的取捨，以及匯流排基礎。這串 Ch 13（memory-mapped I/O）、Ch 14（中斷），是韌體和硬體互動的計組視角。

> **環境**：概念為主。前置：Ch 13（memory-mapped I/O）、Ch 14（中斷）、Ch 30（cache 一致性）。

## 為什麼考這個

CPU 要和裝置（磁碟、網路、感測器）交換資料——怎麼做最有效率？polling 浪費 CPU、interrupt 較好、DMA 最好（CPU 不用搬資料）。這串韌體的硬體互動（Ch 13/14），面試會問三種 I/O 方式的差異與 DMA。

## 先建立直覺：CPU 怎麼搬一大塊資料

```
   情境：要從磁碟讀 1MB 資料到記憶體

   方法1 polling：CPU 一直問裝置「好了沒」，好了自己一個 byte 一個 byte 搬
      → CPU 全程被綁住，超浪費

   方法2 interrupt：CPU 去做別的，裝置好了發中斷叫 CPU 來搬
      → CPU 不用一直等，但「搬資料」還是 CPU 做（1MB 要搬很久）

   方法3 DMA：CPU 叫 DMA controller「你幫我搬」，自己去做別的
      → DMA 搬完才發一個中斷通知 CPU，搬資料完全不佔 CPU！
```

核心演進：**polling（CPU 全程綁）→ interrupt（CPU 不用等但要搬）→ DMA（CPU 連搬都不用）。** DMA 是大量資料傳輸的關鍵——讓 CPU 從「搬資料的苦工」解放。

## 三種 I/O 方式

### 1. Programmed I/O / Polling（輪詢）

CPU 主動「一直問」裝置狀態，好了就自己搬資料：

```c
while (!(STATUS_REG & READY)) { }   // busy-wait 等裝置就緒（Ch 13）
data = DATA_REG;                     // CPU 自己搬
```

- 優點：簡單、即時（一就緒馬上處理）。
- 缺點：**CPU 全程被綁住**（busy-wait 空轉，超浪費），不能做別的。
- 適合：簡單、快速、頻繁的小資料（等待時間極短時）。

### 2. Interrupt-Driven I/O（中斷驅動）

CPU 不用一直問——裝置好了發中斷（Ch 14）：

```
   CPU 發起 I/O 請求 → 去做別的事
   裝置完成 → 發中斷 → CPU 的 ISR 來搬資料（Ch 14）
```

- 優點：CPU 不用 busy-wait，可做別的（比 polling 省 CPU）。
- 缺點：**搬資料還是 CPU 做**——大量資料要很多次中斷（每次搬一點），中斷開銷大。
- 適合：偶發事件、中等資料量。

### 3. DMA（Direct Memory Access，直接記憶體存取）

專門的 **DMA controller** 幫 CPU 搬資料，CPU 不參與搬運：

```
   1. CPU 設定 DMA：來源、目的、大小，然後「交給 DMA」
   2. CPU 去做別的事（完全不管搬運）
   3. DMA controller 直接在「裝置 ↔ 記憶體」之間搬資料（不經過 CPU）
   4. 搬完，DMA 發一個中斷通知 CPU「搬好了」
```

- 優點：**CPU 完全不用搬資料**（只設定 + 收一個完成中斷）——大量資料傳輸的最佳方式。
- 缺點：需要 DMA controller 硬體；DMA 和 CPU 可能搶記憶體匯流排（bus contention）；**cache 一致性問題**（下面）。
- 適合：大量資料傳輸（磁碟、網路、音訊/視訊）。

對比：

| | polling | interrupt | DMA |
|---|---|---|---|
| CPU 等待 | busy-wait（全綁）| 不用等 | 不用等 |
| CPU 搬資料 | 是 | 是（ISR 搬）| **否（DMA 搬）** |
| 中斷次數 | 無 | 多（每次搬一點）| 少（搬完一個）|
| 適合 | 簡單/極短等待 | 偶發/中量 | **大量資料** |
| CPU 效率 | 最差 | 中 | **最好** |

## DMA 的 cache 一致性問題（韌體重點，串 Ch 30）

DMA 直接讀寫**記憶體**，但 CPU 用 **cache**（Ch 30）——這造成不一致：

```
   問題1（DMA 寫記憶體，CPU 讀到舊 cache）：
   DMA 把新資料寫進記憶體 → 但 CPU 的 cache 還是舊值 → CPU 讀 cache 得舊資料！
   解法：DMA 完成後，invalidate（作廢）對應的 cache → CPU 重新從記憶體讀

   問題2（CPU 寫 cache 還沒寫回，DMA 讀記憶體舊值）：
   CPU 寫了資料在 cache（write-back，還沒寫回記憶體，Ch 30）→ DMA 讀記憶體得舊值！
   解法：DMA 前，flush（寫回）cache 到記憶體 → DMA 讀到新值
```

**這是韌體寫 DMA 必須處理的**——DMA buffer 要做 cache 維護（flush/invalidate），否則資料錯亂。這串 Ch 30 的 write-back（cache 和記憶體不一致）。面試問「DMA 有什麼要注意」答「cache 一致性——DMA 前 flush、後 invalidate」是加分答案。

## 匯流排（bus）基礎

匯流排是「連接 CPU、記憶體、I/O 裝置的共用通道」：

```
   常見三種 bus（傳統分類）：
   - address bus（位址匯流排）：傳「要存取哪個位址」（單向，CPU→）
   - data bus（資料匯流排）：傳「資料」（雙向）
   - control bus（控制匯流排）：傳「讀/寫/中斷」等控制訊號

   bus width（匯流排寬度）：一次能傳幾 bit（如 32-bit data bus 一次傳 4 byte）
```

關鍵概念：**bus 是共用的**——CPU、DMA、裝置要用 bus 都得競爭（bus contention/arbitration，匯流排仲裁）。DMA 搬資料時佔用 bus，可能讓 CPU 等（cycle stealing）。bus 寬度影響傳輸頻寬。

（現代系統 bus 更複雜——PCIe、片上互連等，但面試多考傳統概念。）

## 考古題詳解

### Q1：polling、interrupt、DMA 三種 I/O 差在哪？

<details>
<summary>詳解</summary>

- **polling**：CPU 一直問裝置狀態（busy-wait），自己搬資料。簡單但 CPU 全綁（浪費）。
- **interrupt**：CPU 去做別的，裝置好了發中斷，CPU 的 ISR 來搬。不用等但搬資料還是 CPU 做（大量資料中斷多）。
- **DMA**：DMA controller 幫搬，CPU 只設定 + 收完成中斷。CPU 完全不搬資料（最高效）。

演進：polling（全綁）→ interrupt（不等但要搬）→ DMA（連搬都不用）。大量資料用 DMA。

**考點**：三種 I/O 對比，必考。
</details>

### Q2：DMA 是什麼？怎麼運作？

<details>
<summary>詳解</summary>

DMA（Direct Memory Access）：專門的 DMA controller 直接在「裝置↔記憶體」搬資料，不經過 CPU。

流程：CPU 設定 DMA（來源/目的/大小）→ CPU 去做別的 → DMA 自己搬（不佔 CPU）→ 搬完發中斷通知 CPU。

好處：CPU 不用搬大量資料（解放 CPU）。代價：需 DMA 硬體、搶 bus、cache 一致性問題。

**考點**：DMA 機制，必考。
</details>

### Q3：DMA 有什麼要注意的問題？（cache 一致性）

<details>
<summary>詳解</summary>

**cache 一致性**——DMA 直接讀寫記憶體，但 CPU 用 cache（Ch 30），會不一致：
1. DMA 寫記憶體後，CPU 的 cache 還是舊值 → 要 **invalidate cache**（讓 CPU 重讀記憶體）。
2. CPU 寫在 cache 還沒寫回（write-back），DMA 讀記憶體得舊值 → DMA 前要 **flush cache**（寫回記憶體）。

韌體寫 DMA 必須做 cache 維護（flush/invalidate DMA buffer）。串 Ch 30 write-back 的不一致。

**考點**：DMA cache 一致性，韌體加分題。
</details>

### Q4：什麼時候用 polling、什麼時候用 interrupt？

<details>
<summary>詳解</summary>

- **polling**：等待時間極短、頻繁、簡單的情況（busy-wait 一下就好，中斷的開銷反而更大）。或非常即時的需求。
- **interrupt**：偶發事件、等待時間長（CPU 該去做別的，別 busy-wait 浪費）。省電場景（沒事睡眠，中斷喚醒，Ch 19）。

判斷：等很久 → interrupt（別空轉）；等極短/頻繁 → polling（省中斷開銷）。串 Ch 14 interrupt vs polling。

**考點**：polling vs interrupt 選擇，串 Ch 14。
</details>

### Q5：address bus、data bus、control bus 各傳什麼？

<details>
<summary>詳解</summary>

- **address bus**：傳「要存取的位址」（單向，CPU 發出）。寬度決定可定址範圍（32-bit 位址 → 4GB）。
- **data bus**：傳「資料」（雙向）。寬度決定一次傳幾 byte（頻寬）。
- **control bus**：傳「控制訊號」（讀/寫/中斷請求等）。

bus 是共用的 → CPU/DMA/裝置要競爭（bus arbitration）。

**考點**：三種 bus，基礎題。
</details>

## 踩雷集錦

1. **以為 DMA 不用 CPU 完全參與**：CPU 還是要「設定 DMA」+「收完成中斷」，只是不用「搬資料」。
2. **忘了 DMA 的 cache 一致性**：DMA 直接讀寫記憶體，CPU cache 會不一致——要 flush/invalidate。韌體 DMA 必處理（Ch 30）。
3. **polling 一律當壞的**：等待極短/頻繁時 polling 反而比中斷好（省中斷開銷）。看情境。
4. **interrupt 以為 CPU 不用搬資料**：interrupt-driven 還是 CPU（ISR）搬；DMA 才不用 CPU 搬。
5. **混淆三種 I/O 的「CPU 做什麼」**：polling（等+搬）、interrupt（不等+搬）、DMA（不等+不搬）。
6. **以為 DMA 沒缺點**：要硬體、搶 bus（cycle stealing）、cache 一致性。

## 速記

- 三種 I/O：**polling**（busy-wait + CPU 搬，全綁/浪費）、**interrupt**（不等 + CPU/ISR 搬，省等但大量資料中斷多）、**DMA**（不等 + **DMA 搬**，CPU 只設定+收完成中斷，大量資料最佳）。
- **DMA**：controller 直接在裝置↔記憶體搬，CPU 解放。代價：要硬體、搶 bus、**cache 一致性**（DMA 前 flush、後 invalidate，串 Ch 30）。
- polling vs interrupt：等極短/頻繁用 polling、等久/偶發/省電用 interrupt（Ch 14）。
- bus：**address**（位址,單向）、**data**（資料,雙向）、**control**（控制訊號）；共用要仲裁。

## 自我檢核

- [ ] polling、interrupt、DMA 三種 I/O 各「CPU 做什麼」？演進邏輯是什麼？
- [ ] DMA 怎麼運作？CPU 還要做什麼（不是完全不管）？
- [ ] DMA 的 cache 一致性問題是什麼？韌體怎麼處理（flush/invalidate）？
- [ ] 什麼時候用 polling、什麼時候用 interrupt？
- [ ] address/data/control bus 各傳什麼？

## 延伸閱讀

### 書籍

- **《Operating System Concepts (恐龍書)》** — Ch 12 I/O Systems
  - **讀哪幾章**：12.2（I/O 硬體：polling/interrupt/DMA）。
  - **和本章的關聯**：I/O 方式的標準教材。

- **《Computer Organization and Design》** — Patterson & Hennessy — I/O 章
  - **讀哪幾章**：I/O、DMA、bus 章。
  - **為什麼值得讀**：計組視角的 I/O，含 DMA 與 bus。

### 本 repo

- **[embedded/protocols](../../embedded/protocols/README.md)**
  - **這門課的定位**：ESP32 嵌入式通訊協議（SPI/I2C/UART...register-level）。本章 I/O 的實作面（真的操作裝置）在這門深入。

I/O 懂了，Part 4 最後一章補多核時代的硬體並行——cache coherence 與 memory barrier，串 volatile/atomic。

→ [Ch 35 並行硬體基礎](./35-concurrency-hardware.md)
