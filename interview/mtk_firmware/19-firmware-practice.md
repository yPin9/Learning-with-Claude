# Ch 19 — 低功耗、debug 與韌體開發實務

> **目標**：補足韌體開發的實務面——低功耗（power state、sleep）、watchdog、debug 手段（JTAG/SWD、printf debug、LED）、以及韌體開發常見的情境與面試會問的實務問題。這些展現你有沒有真的做過韌體。

> **環境**：概念為主，嵌入式韌體。前置：Ch 13-18。

## 為什麼考這個

技術面除了考觀念，常會問「你怎麼 debug 韌體」「怎麼省電」「watchdog 是什麼」——這些實務問題篩出「真的做過韌體」vs「只讀過書」。MTK 做手機/IoT，**低功耗是核心需求**（電池），所以特別愛問省電。答得出實務細節，展現你能上手。

## 低功耗（power management）

手機/IoT 靠電池，**省電是韌體的核心任務**。原理：CPU 和周邊「沒事就睡，有事才醒」。

```
   功耗的關鍵觀念：
   - CPU 全速跑很耗電；閒置時應進入低功耗狀態
   - 周邊（螢幕、無線、感測器）不用時關掉
   - 用「中斷喚醒」而非「輪詢」（polling 一直跑 CPU = 耗電，Ch 14）
```

常見的 power state（由耗電到省電）：

```
   Active / Run     ← CPU 全速執行（最耗電）
   Idle / Sleep     ← CPU 停（時脈關），周邊還在，中斷可立即喚醒（省電，喚醒快）
   Deep Sleep / Stop← 更多東西關（時脈、部分 RAM），喚醒較慢，更省電
   Standby / Off    ← 幾乎全關，最省電，喚醒最慢（近似重啟）
```

韌體的省電策略：

- **主迴圈沒事就睡**：`while(1){ if(!work) enter_sleep(); }`——用中斷喚醒（事件來了 CPU 才醒），不要 busy-loop（一直空轉超耗電）。
- **動態調整時脈/電壓（DVFS）**：負載低時降頻降壓（功耗 ∝ 頻率 × 電壓²）。
- **關閉不用的周邊**：不用的 UART/感測器/無線模組關掉（clock gating / power gating）。
- **批次處理**：累積工作一次做完再睡，比頻繁醒睡好（喚醒有開銷）。

面試重點：**「中斷驅動 + 沒事就睡」是省電的核心思路**——對比 polling（一直跑 CPU）。這串到 Ch 14（中斷 vs polling）。

## Watchdog（看門狗計時器）

韌體可能因 bug 卡死（無窮迴圈、deadlock）——沒有人能手動重啟一個埋在裝置裡的晶片。**Watchdog** 是「自動偵測卡死並重啟」的保命機制：

```
   Watchdog = 一個倒數計時器：
   - 正常時，韌體要定期「餵狗（kick/feed/reset watchdog）」重置計時器
   - 如果韌體卡死（沒餵狗），計時器倒數到 0 → 硬體自動重啟系統

   程式碼：
   while (1) {
       do_work();
       watchdog_kick();    // 定期餵狗，證明「我還活著」
   }
   // 若 do_work() 卡死 → 沒餵狗 → watchdog 超時 → 自動 reset
```

關鍵：**watchdog 是「最後防線」**——當韌體卡死、無法自我恢復時，靠硬體自動重啟讓系統復活（總比永遠當機好）。但它治標不治本（重啟不修 bug），且餵狗位置要設計好（別在卡死的路徑上餵）。

面試問「watchdog 是什麼」答「偵測卡死自動重啟的計時器，靠定期餵狗證明存活」。

## Debug 手段

韌體 debug 比一般軟體難——沒有方便的 debugger UI、可能沒有螢幕、卡在中斷裡難追。常見手段：

```
   1. JTAG / SWD（硬體除錯介面）：
      - 透過 debug probe（J-Link、ST-Link）連到晶片
      - 能下斷點、單步、看暫存器/記憶體（像 GDB 但透過硬體）
      - 韌體 debug 的主力（呼應 security/gdb 課的 remote/embedded）

   2. printf / UART log：
      - 透過 UART 印訊息到電腦終端（最常用的「印 log」debug）
      - 注意：printf 慢、不能在 ISR 用（Ch 14）；ISR 要 log 用 ring buffer

   3. LED / GPIO 燈號：
      - 沒有 UART/螢幕時，用 LED 閃爍表示狀態（最原始但有效）
      - 「卡在哪就點哪顆燈」

   4. 邏輯分析儀 / 示波器：
      - 看實際的電氣訊號時序（debug 通訊協定、時序問題）

   5. core dump / 暫存器快照：
      - 當機時保存暫存器/stack 狀態事後分析
```

面試問「韌體怎麼 debug」答：JTAG/SWD（下斷點單步）、UART printf log、LED 燈號、邏輯分析儀——依情境選。展現你知道韌體 debug 的多種手段（不只 printf）。

> 韌體 debug 的難處：(1) 即時性——下斷點會停住系統，但有些時序敏感的 bug 一停就不重現；(2) ISR 難 debug（非同步、不能 printf）；(3) 可能沒有螢幕/檔案系統。所以韌體很依賴 JTAG + 硬體工具。

## 韌體開發常見情境

面試可能問的實務情境：

- **記憶體有限**：韌體常只有幾十 KB~幾 MB RAM。要省記憶體（static 配置 > 動態、注意 struct 對齊 Ch 8、避免遞迴爆 stack）。
- **沒有 OS（裸機 bare-metal）**：直接在硬體上跑，自己管一切（主迴圈 + 中斷），或用 RTOS（Ch 18）。
- **flash 與 RAM 分離**：code/常數在 flash（唯讀、慢）、變數在 RAM（讀寫、快）。啟動時 .data 從 flash 複製到 RAM（Ch 17 boot）。
- **OTA 更新**：韌體要能遠端更新（bootloader + 雙 bank 防更新失敗變磚）。
- **時序要求**：某些操作有嚴格時序（通訊協定、硬體控制）——影響你能不能用 printf/中斷。

## 考古題詳解

### Q1：韌體怎麼省電？

<details>
<summary>詳解</summary>

核心：**沒事就睡，有事才醒（中斷驅動）**。具體：
1. 主迴圈閒置時進低功耗狀態（sleep），用中斷喚醒——別 busy-loop（一直空轉耗電）。
2. 動態調整時脈/電壓（DVFS，負載低時降頻降壓）。
3. 關閉不用的周邊（clock/power gating）。
4. 用中斷而非 polling（polling 一直跑 CPU = 耗電，Ch 14）。
5. 批次處理工作（喚醒有開銷，少醒睡）。

對行動/IoT（電池）這是核心任務。

**考點**：低功耗策略，MTK 特別愛問（手機晶片）。
</details>

### Q2：什麼是 watchdog？怎麼用？

<details>
<summary>詳解</summary>

Watchdog 是一個倒數計時器，用來偵測韌體卡死並自動重啟。正常時韌體要定期「餵狗」（重置計時器）；如果韌體卡死（無窮迴圈/deadlock）沒餵狗，計時器倒數到 0，硬體自動重啟系統。

它是「最後防線」——當韌體無法自我恢復時，靠硬體重啟讓埋在裝置裡的系統復活。餵狗位置要設計好（別在可能卡死的路徑上餵，否則卡死也餵了，失去意義）。

**考點**：watchdog 機制，實務必考。
</details>

### Q3：韌體怎麼 debug？

<details>
<summary>詳解</summary>

多種手段依情境：
1. **JTAG/SWD**：透過 debug probe 下斷點、單步、看暫存器/記憶體（主力，像 GDB）。
2. **UART printf log**：印訊息到終端（最常用；但 printf 慢、不能在 ISR，Ch 14）。
3. **LED/GPIO 燈號**：沒 UART/螢幕時用燈表狀態。
4. **邏輯分析儀/示波器**：看實際訊號時序（通訊/時序 bug）。

難處：即時性（下斷點停住系統，時序 bug 一停就不重現）、ISR 難 debug、可能沒螢幕。

**考點**：韌體 debug 手段，展現實務經驗。
</details>

### Q4：為什麼韌體偏好靜態記憶體配置、少用 malloc？

<details>
<summary>詳解</summary>

幾個原因：
1. **記憶體有限**：韌體 RAM 少（KB~MB），heap 碎片化（Ch 25）會耗盡寶貴記憶體。
2. **malloc 不可重入/慢**（Ch 15）：ISR 不能用、有不確定的執行時間（破壞即時性，Ch 18）。
3. **malloc 失敗難處理**：記憶體少更容易配置失敗，嵌入式難優雅處理 OOM。
4. **可預測性**：靜態配置在編譯期決定記憶體用量，行為可預測（即時系統需要）。

所以韌體常「啟動時靜態配好所有記憶體」，避免執行期動態配置。

**考點**：韌體記憶體實務（靜態 vs 動態），串 Ch 11/15/18。
</details>

### Q5：bare-metal（裸機）和跑 RTOS 的韌體差在哪？

<details>
<summary>詳解</summary>

- **bare-metal（裸機）**：沒有 OS，韌體直接在硬體上跑。典型是「主迴圈（super loop）+ 中斷」——主迴圈輪流做事、中斷處理急事。簡單、開銷小，但多工難管理（手動排程）。
- **RTOS**：有即時作業系統（Ch 18）管理多個 task、提供排程/同步。適合複雜的多工韌體，但有 RTOS 本身的記憶體/開銷。

選擇：簡單應用用 bare-metal；複雜多工、有即時性需求用 RTOS。

**考點**：bare-metal vs RTOS，串 Ch 18。
</details>

## 踩雷集錦

1. **省電用 busy-loop 等事件**：`while(!flag);` 一直跑 CPU = 超耗電。要 sleep + 中斷喚醒。
2. **不知道 watchdog**：韌體卡死靠它自動重啟。面試常問。
3. **以為韌體 debug 只有 printf**：JTAG/SWD、LED、邏輯分析儀都是手段。且 printf 不能在 ISR。
4. **韌體大量用 malloc**：記憶體少、碎片化、不可重入、不可預測。偏好靜態配置。
5. **餵狗位置設計錯**：在會卡死的迴圈裡餵狗 = 卡死也餵了，watchdog 失效。
6. **下斷點 debug 時序敏感的 bug**：一停系統就不重現。時序 bug 用 log/邏輯分析儀，別硬下斷點。

## 速記

- **低功耗**：沒事就睡（sleep + 中斷喚醒，別 busy-loop）、DVFS 降頻降壓、關不用的周邊、中斷 > polling。MTK 手機晶片核心需求。
- **watchdog**：偵測卡死自動重啟的計時器；定期「餵狗」證明存活，沒餵到 0 就 reset。最後防線。
- **debug 手段**：JTAG/SWD（斷點單步）、UART printf log、LED 燈號、邏輯分析儀——依情境。printf 不能在 ISR。
- 韌體**偏好靜態記憶體配置**（少用 malloc）：記憶體少、防碎片、可預測、malloc 不可重入。
- bare-metal（主迴圈+中斷）vs RTOS（多 task 排程）。

## 自我檢核

- [ ] 韌體怎麼省電？核心思路是什麼（提示：中斷 vs polling）？
- [ ] watchdog 是什麼？「餵狗」是什麼意思？它解決什麼問題？
- [ ] 韌體有哪些 debug 手段？為什麼不只靠 printf？
- [ ] 為什麼韌體偏好靜態記憶體配置、少用 malloc？
- [ ] bare-metal 和 RTOS 韌體差在哪？

## 延伸閱讀

### 本 repo

- **[security/gdb](../../security/gdb/README.md)** — remote/embedded debug 章
  - **這門課的定位**：GDB 深入（含 gdbserver/JTAG/OpenOCD 遠端嵌入式 debug）。本章 debug 手段的工具面在這門深入。

### 書籍

- **《Making Embedded Systems》** — Elecia White
  - **讀哪幾章**：power management、debugging 章。
  - **為什麼值得讀**：嵌入式韌體實務的好書，把省電/debug/記憶體實務講得很實際。

- **《Programming Embedded Systems》** — Barr & Massa
  - **讀哪幾章**：watchdog、低功耗、debug 章。
  - **和本章的關聯**：韌體實務的經典教材。

Part 2（嵌入式/韌體）寫完了！用練習 B 把嵌入式考點綜合——ISR + 共享變數 + endian + 暫存器的情境題。

→ [練習 B：嵌入式情境題](./practice-b-embedded-scenario.md)
