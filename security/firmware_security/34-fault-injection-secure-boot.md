# Ch 34 — 故障注入繞 secure boot

> **目標**：理解四種故障注入（fault injection）技術的物理原理與攻擊參數、知道 glitch 打在哪個時序能繞過 RSA 驗簽、掌握 ChipWhisperer 工具鏈的工作流程（未實測）、透過公開案例建立對 glitch 攻擊可行性的具體感知、以及理解對抗措施的設計邏輯。本章是硬體故障注入的核心概念章，所有硬體部分均未實測。

---

## 為什麼 RSA 驗簽可以被「故障」破壞？

RSA 驗簽的數學本身是嚴密的。但執行數學的**矽晶體**不是。

當你快速降低 CPU 的供電電壓或時脈頻率超過硬體容忍範圍，部分指令會執行出錯：快取錯誤讀取、ALU 計算截斷、記憶體資料位元翻轉。如果這個錯誤恰好發生在「比較驗簽結果 vs 預期結果」的那幾個時鐘週期，你可能讓一個「應該失敗」的驗簽變成「成功」。

這不是理論。這是實驗室裡可以重現的攻擊，成功率看情況從 1/10000 次到 1/10 次不等，用腳本自動重試。

```
正常執行流程：
  RSA 解密運算（慢，~milliseconds）
        │
        ▼
  compare(decrypted_hash, actual_hash)  ← 攻擊目標
        │
    [不等] ──→ reject()；停止開機
    [相等] ──→ execute()；繼續開機

故障注入介入：
  RSA 解密運算
        │
  [GLITCH 打在這個時序]
        │   電壓/時鐘異常 → compare 指令結果異常
        ▼
  compare(corrupted_result, actual_hash)
        │
    結果可能是 [相等]（即使 hash 不一致）
        ▼
  execute()；繼續開機 ← 攻擊者的惡意 image 被執行
```

---

## 四種故障注入技術

### 1. Voltage Glitching（電壓故障注入）

**原理**：短暫（nanoseconds 到 microseconds）拉低 VCC 供電電壓，使得 CPU 在一個時鐘週期內的邏輯閘不能可靠地維持電位，造成計算錯誤。

```
正常 VCC：              3.3V ────────────────────────────────
                              ││││││││││││││││││││
Glitch 注入：          3.3V ─────┐          ┌──────────────
                                 └──→ 0V  ←─┘
                                  <──寬度 w──>
                                 時間偏移 t（從觸發點計算）
```

**關鍵參數**：
- `offset`（t）：從觸發事件（例如 USB 握手、UART 送出特定字元）到 glitch 開始的延遲
- `width`（w）：電壓降低持續的時間
- `voltage`（v）：降到多低（通常 0V 到 1V 之間）

**優點**：設備便宜（ChipWhisperer Nano ~$50 USD）、對大多數 MCU 有效
**缺點**：需要接電源線、需要精準計時、對有 glitch detector 的晶片效果差

**典型攻擊電路（概念圖）**：
```
目標裝置 VCC ───┬─── 正常供電路徑
                │
               [FET 開關]  ←── ChipWhisperer GPIO 控制
                │
               GND（在 glitch 期間拉到 GND）
```

---

### 2. Clock Glitching（時鐘故障注入）

**原理**：短暫改變 CPU 的時脈頻率或插入額外的時鐘邊緣（glitch pulse），使 CPU 在一個「過短」的週期內嘗試完成一個邏輯操作，導致結果不確定。

```
正常時鐘：  ___   ___   ___   ___   ___
           |   | |   | |   | |   | |   |
           |___| |___| |___| |___| |___|

Glitch：   ___   ___  __   __   ___   ___
           |   | |   ||  | |  | |   | |   |
           |___| |___||__| |__| |___| |___|
                       ↑↑↑
                插入兩個短脈衝，CPU 看到「快很多」的時鐘
```

**優點**：不需要接電源線，只需要時鐘線（許多 MCU 的時鐘輸入是外部晶振，可截斷替換）
**缺點**：對使用片上振盪器（internal RC oscillator）的晶片無法直接應用

**典型應用場景**：MCU 的 UART bootloader 保護、ATmega/STM32 的讀保護、嵌入式 secure boot 的條件跳轉

---

### 3. EMFI（電磁故障注入）

**原理**：將一個電磁探頭（小型線圈）靠近目標 IC，在特定時序發出強電磁脈衝，誘發 IC 內部的感應電流，造成局部電路的邏輯錯誤。

```
EMFI 探頭（小線圈）
        │
        │  < 1mm 距離
        ▼
[目標 IC 表面（封裝上方）]
        │
        ▼
IC 內部感應電流 → 局部電位擾動 → 特定邏輯閘翻轉
```

**優點**：
- 不需要電氣連接（非接觸式）
- 可以攻擊 BGA 封裝（引腳在底部）的 SoC
- 可以在部分情況下穿透 JTAG fuse（探頭對準特定功能區域）
- 探頭定位移動，可以找到「對哪個功能最有效」的位置

**缺點**：
- 比電壓 glitch 更難精準控制（電磁場會擴散）
- 設備成本較高（Riscure Inspector 等商業工具 $3k+）
- 需要知道目標 IC 的 die 佈局（哪個區域對應哪個功能）

**DIY 入門**：原則上可以用 FPC 線圈 + 高電壓脈衝電路自製，研究文獻中有設計圖，但不建議初學者直接嘗試（電壓脈衝可能損壞 PCB 或傷人）。

---

### 4. Laser Fault Injection（雷射故障注入）

**原理**：用雷射光照射 IC 的矽基板，光子的能量讓矽基板產生局部的電子電洞對，相當於在特定電晶體上注入電荷，造成 bit flip 或邏輯錯誤。精度可以到**單一邏輯閘**。

```
去蓋後的 IC（砂輪/HF 蝕刻去除封裝）
        │
        ▼
矽基板（die）暴露
        │
雷射（近紅外，~1064nm 穿透矽，或可見光 ~532nm 從背面照射）
        │
        ▼
局部電子電洞對 → 特定電晶體翻轉 → 精確的 bit flip
```

**優點**：
- 精度最高：可以打到單一位元或單一指令
- 可以對抗幾乎所有 glitch 防護（voltage detector、redundancy check）
- 從 IC 正面或背面（thinned die）都可以攻擊

**缺點**：
- 設備昂貴（$10k–$100k，含雷射源、XY 精密平台、顯微鏡）
- 需要去蓋（decapsulation）——化學或機械方式移除 IC 封裝，有破壞性
- 需要知道 die 佈局（反向工程 IC 電路圖）
- 操作需要光電工程背景

**適用場景**：最高安全等級的攻擊（銀行 HSM、SIM 卡、政府認證裝置）、漏洞研究（精確找到觸發 bit flip 的位置）

---

## 攻擊目標：glitch 打在哪裡最有效？

故障注入的成功，取決於「glitch 在正確的時序打到正確的指令」。對於 secure boot 繞過，有三個高價值目標：

### 目標 1：RSA 驗簽的比較指令

```c
// 典型的驗簽結構（偽代碼）
int verify_signature(uint8_t *image, size_t len, uint8_t *sig) {
    uint8_t computed_hash[32];
    uint8_t expected_hash[32];

    sha256(image, len, computed_hash);
    rsa_decrypt(sig, expected_hash);         // 慢操作，~10-100ms

    return memcmp(computed_hash, expected_hash, 32);  // ← 目標指令
    //                                                      ↑
    //                            攻擊：讓 memcmp 回傳 0（相等）
    //                            即使兩個 hash 不同
}

// 呼叫端
if (verify_signature(image, len, sig) == 0) {
    execute(image);  // ← 目標：到達這裡
}
```

`memcmp` 本質上是一個逐位元組比較的迴圈。如果 glitch 讓迴圈提前退出（錯誤判定 `i == len`），或讓第一次比較的結果被截斷為 0，驗簽就「通過」了。

### 目標 2：驗簽後的條件跳轉

即使不能破壞 `memcmp` 本身，也可以破壞**決策指令**：

```asm
; 驗簽回傳後的組語（ARM Thumb-2）
BL   verify_signature    ; 呼叫驗簽，回傳值在 r0
CMP  r0, #0              ; 比較 r0 和 0
BNE  reject_label        ; 不等（驗簽失敗）跳走  ← 目標：glitch 這個 BNE
BL   execute_image       ; 繼續執行
```

如果 glitch 讓 `BNE` 指令「變成」NOP（或讓 CPU 跳過這條指令），不管 `r0` 是什麼，下一步都是 `execute_image`。

這個目標比 `memcmp` 本身更容易定位——你只需要找到驗簽函式回傳後的那幾條指令的時序。

### 目標 3：金鑰或 hash 的記憶體讀取

更精細的攻擊：讓 CPU 在讀取「儲存在安全區域的預期 hash」時發生錯誤，讀到的值是 0 或與 computed_hash 相同的值：

```
正常：  expected_hash = [從 eFuse/OTP 讀取的正確 hash]
攻擊：  在讀取 eFuse/OTP 的記憶體操作時 glitch
        → expected_hash = [0x00...00] 或隨機值
        → 如果攻擊者能讓 image 的 hash 也是 0x00...00...
          （特殊建構的 image）→ memcmp 通過
```

這個方法更難（需要配合特殊建構的 image），但在某些 MCU 上 OTP 讀取電路對電壓特別敏感。

---

## Glitch 參數 Characterization：如何找到正確的參數

從「理論上可以 glitch」到「真的 glitch 成功」，需要一個**參數掃描**過程。這是故障注入研究最耗時的部分。

### 三維參數空間

```
        offset（觸發後延遲）
          ↑
          │       ███  ← 成功區域（glitch 成功但裝置不崩潰）
          │    ████████
          │   ██████████
          │  ████████████
          │   ██████████
          │    ████████
          │       ███
          └──────────────────→ width（glitch 持續時間）

（第三維是 voltage 降幅，通常做 2D 掃描時固定一個值）
```

**成功的 glitch** 落在「夠強讓指令出錯」但「不強到讓整個 SoC 重置或損壞」的甜蜜點。

### 掃描策略

```python
# ChipWhisperer Python API 的掃描邏輯（概念，未實測）
import chipwhisperer as cw

scope = cw.scope()
target = cw.target(scope)

# 設定 glitch 模組
scope.glitch.clk_src = 'clkgen'
scope.glitch.output = 'enable_only'  # 電壓 glitch 模式

results = []
for offset in range(0, 50000, 100):      # offset：0–50ms，步進 100μs
    for width in range(1, 50, 1):        # width：1–50 個時鐘週期
        scope.glitch.offset = offset
        scope.glitch.width = width

        # 觸發攻擊：讓裝置執行驗簽
        target.simpleserial_write('g', bytearray([0x41]*16))
        response = target.simpleserial_read('r', 1)

        result_type = classify_result(response)
        # 分類：NORMAL（正常失敗）/ RESET（裝置重置）/ SUCCESS（驗簽被繞過）
        results.append((offset, width, result_type))

# 找 SUCCESS 的區域
successes = [(o, w) for o, w, r in results if r == 'SUCCESS']
```

**典型掃描時間**：一個 50×50 的 2D 掃描，每次攻擊嘗試約 10ms，總共 25,000 次 × 10ms = 4 分鐘。實際上 offset 範圍可能是幾十萬個採樣點，完整掃描可能需要幾小時到幾天。

### 觸發信號的取得

最難的部分之一是**取得精準的觸發信號**，知道「驗簽什麼時候開始」：

- **電源觸發**：用電流探頭觀察 SoC 的電源消耗，RSA 運算的大量乘法操作有特殊的電流特徵（Power Analysis），可以從中確認驗簽開始的時序
- **IO 觸發**：觀察 UART 輸出（bootloader 在驗簽前後通常有 log），用 log 的時序當觸發點
- **時間固定**：如果每次開機到驗簽的時間固定（deterministic boot），可以用開機後固定延遲作為觸發

---

## ChipWhisperer 工具鏈

（全部未實測，以下為基於公開文件的描述）

ChipWhisperer 是 NewAE Technology 開發的開源故障注入和旁路分析平台，是研究者入門硬體安全最常用的工具之一。

### 硬體系列

| 型號 | 價格 | 功能 | 目標 |
|------|------|------|------|
| CW-Nano | ~$50 | 電壓 glitch + 簡單 oscilloscope | 入門 MCU（ATmega、STM32 低端） |
| CW-Lite | ~$300 | 電壓 + 時鐘 glitch + 50MS/s ADC | STM32、NXP、一般 MCU |
| CW-Pro | ~$1000 | 高精度 glitch + 更好 ADC + FW glitch | 複雜目標、SoC |
| CW-Husky | ~$500 | 高速 glitch（125MS/s）+ 更多 IO | 需要高時序精度的目標 |

### 軟體架構

```
ChipWhisperer Python 套件
    │
    ├── chipwhisperer.scope   — 控制 CW 硬體（ADC、glitch timing）
    ├── chipwhisperer.target  — 與目標裝置通訊（UART、SPI）
    ├── chipwhisperer.capture — 波形捕獲（用於 power analysis）
    └── Jupyter Notebook 環境 — 官方教學全用 Notebook，邊掃邊看結果
```

### 典型工作流（概念）

```
1. 硬體接線
   CW ─── SMA 電纜 ──→ 目標板的 VOUT 測量點（shunt resistor 兩端）
   CW ─── GPIO ──────→ 目標板的 RESET、IO0（觸發信號）
   CW ─── 電源線 ───→ 目標板 VCC（取代原電源，受 CW 控制）

2. 基礎通訊測試（不 glitch）
   確認 target.simpleserial_write/read 正常運作

3. Power trace 捕獲
   捕獲正常開機的電流波形
   在波形中識別「驗簽操作」的特徵（通常是高電流的長持續期操作）

4. Glitch 參數初估
   根據波形確認 offset 的大致範圍（驗簽在開機後第幾毫秒）
   根據目標 SoC 時鐘頻率估算 width（通常 1–20 個週期有效）

5. 網格掃描（Grid scan）
   自動化掃描 offset × width 矩陣，分類每次結果

6. 成功點確認
   找到一個成功 glitch 後，縮小範圍確認可重現性
   計算成功率（成功次數 / 嘗試次數）
```

**官方教學資源**：ChipWhisperer 的 Read the Docs 文件有 20+ 個完整實驗（Lab），從入門到進階，每個 Lab 都有 Jupyter Notebook。初學者建議從 Lab 1（Power Analysis Intro）和 Lab 2（Clock Glitching）開始。

---

## 公開案例

### 案例一：Trezor One 硬體錢包 glitch（Ledger Donjon，2019）

**目標**：Trezor One，STM32F205 MCU，有讀保護（RDP Level 2）
**攻擊**：電壓 glitch 繞過 STM32 的讀保護機制，讀取 flash 中的 seed（主私鑰）

**技術細節**：
- STM32F205 在開機時從 BootROM 讀取 RDP 設定（Level 0/1/2）
- Ledger Donjon 用電壓 glitch 在讀取 RDP byte 的時序讓讀取值異常
- 讓 BootROM 誤判 RDP Level 為 1（而非 2），使得 SRAM 可被讀取
- 透過 SRAM 分析提取 seed（Trezor 在 DFU 期間把 seed 放在 SRAM 中）

**影響**：Trezor One 和 Trezor Model T 均受影響，攻擊需要實體持有裝置，約 5–15 分鐘

**修復**：Trezor 在後續韌體中增加了軟體側的對策（啟動時自我檢查），但 STM32 BootROM 本身無法 patch，RDP 讀取的硬體漏洞仍在。從此 Trezor 建議用戶啟用 PIN 保護並信任 passphrase（第 25 個詞）作為第二因子。

```
實體攻擊流程（概念）：
  目標：Trezor One PCB
  工具：ChipWhisperer（或類似），示波器
       ↓
  找到 STM32 VCC 引腳（PCB 上）
  插入 glitch 電路（FET 拉 GND）
       ↓
  重複開機 + glitch，觀察 DFU 模式下的 USB 回應
       ↓
  找到正確 offset/width → 成功進入「以為是 RDP Level 1」的模式
       ↓
  透過 DFU debug 命令讀取 SRAM 內容
       ↓
  從 SRAM 提取 mnemonic seed
```

### 案例二：任天堂 Switch（Tegra X1）BootROM glitch 補充

Ch 21 T3b 已提及 Fusée Gelée 是 BROM USB overflow，但有研究者（SciresM 等）同期也發現：即使 Tegra X1 有部分 glitch 防護，也可以在 USB 列舉期間用電壓 glitch 繞過某些檢查。Fusée Gelée 最終以純軟體路徑（overflow）被採用，因為成功率 100%、不需要硬體工具——但硬體路徑作為備案同樣有效。

這個案例說明：**當軟體漏洞存在時，硬體路徑作為備案有意義，但不一定是首選**。

### 案例三：Xbox 360 / PS3 時代的 glitch 攻擊（2008–2011）

遊戲機安全研究的黃金時代大量使用 clock glitch：

**Xbox 360 King Kong glitch**：2006 年 anonymous 研究者發現用時鐘 glitch 可以讓 Xenon CPU 在驗簽後接受修改的遊戲映像。後來 RGH（Reset Glitch Hack）更成熟，用 CPLD 精確控制時鐘，讓 CPU 在 POST-boot 驗簽的最後 CMP 指令時發生 glitch，使 CPU 讀到相反的比較結果。成功率接近 100%。

**意義**：遊戲機研究推動了 glitch 技術的大幅普及——大量研究者在家裡建立了 glitch 工作台，積累的知識後來被用於更嚴肅的安全研究。

### 案例四：TF-A / BootROM 驗簽 glitch（學術文獻）

2022-2024 年數篇學術論文（TCHES、CHES、USENIX Security）展示了針對 ARM Cortex-A SoC 的 TF-A BL1/BL2 驗簽的 glitch 攻擊：

- **攻擊原理**：在 BL2 image 的 RSA-2048 驗簽的 final hash comparison 時電壓 glitch
- **目標 SoC**：NXP i.MX 6、STM32MP1（ARM Cortex-A7/A5）
- **成功率**：在最佳參數下每次嘗試 ~0.1–1%，自動化 1000 次嘗試約 1–10 次成功
- **對策缺失**：未實作 glitch detector 的 SoC 幾乎沒有防護

### 案例五：Fusée Gelée 精神對照（Ch 21 T3b）

Fusée Gelée 不是 glitch 攻擊，但把它的思路對照 glitch 很有啟發：

```
Fusée Gelée（軟體路徑）：
  Tegra X1 USB DFU 的 length 欄位被攻擊者設到超大值
  → BootROM 把 DMA buffer 之外的記憶體複製進去
  → 覆蓋堆疊 → 劫持 PC → 執行 payload
  成功率：100%（純軟體，無 glitch）

假設的 glitch 替代路徑：
  在 BootROM 呼叫 verify_signature() 後的 CMP 指令時 glitch
  → 讓 CMP 結果反轉 → 驗簽「成功」
  成功率：~0.01–1%（需要精確 glitch）
  缺點：需要硬體工具

結論：當軟體路徑存在且成功率 100% 時，優先軟體路徑。
     硬體 glitch 是軟體路徑不存在時的替代選項。
```

---

## 對抗手段

故障注入是一個攻防博弈。防禦側有若干有效手段：

### 對抗手段 1：Redundant Check（重複驗證）

```c
// 有防護的實作
int verify_with_redundancy(uint8_t *image, size_t len, uint8_t *sig) {
    int result1 = internal_verify(image, len, sig);
    // 加入隨機延遲，讓攻擊者無法預測第二次比較的時序
    random_delay();
    int result2 = internal_verify(image, len, sig);

    // 兩次結果必須一致，且必須都是成功
    if (result1 == 0 && result2 == 0 && result1 == result2) {
        return 0;  // 成功
    }
    // 任何其他情況：視為失敗
    return -1;
}
```

攻擊者現在需要同時 glitch 兩次驗證，難度成倍增加。

### 對抗手段 2：Random Delay（隨機延遲）

在驗簽前後插入隨機延遲，讓 offset 參數掃描的有效範圍大幅擴大：

```c
// 使用硬體隨機數（TRNG）
uint32_t delay = get_trng_value() & 0xFFFF;  // 0–65535 個週期的隨機延遲
busy_wait(delay);
result = verify_signature(image, len, sig);
busy_wait(get_trng_value() & 0xFFFF);
```

對攻擊者的影響：offset 掃描範圍從「幾毫秒」變成「幾十毫秒」，成功率降低 10–100 倍，但不會到零。

### 對抗手段 3：Glitch Detector（電壓/頻率偵測器）

許多現代 SoC（尤其是安全等級較高的 MCU）內建電壓監控和頻率監控電路：

```
電壓監控：
  VCC < 2.8V（通常設定） → 立即觸發 RESET 或 TAMPER 中斷
  → 清除敏感 register
  → 進入安全狀態

頻率監控：
  時鐘頻率超出 [f_min, f_max] 範圍 → 同樣觸發
```

對攻擊者的影響：
- 電壓 glitch 必須降到「夠破壞指令但不觸發偵測器」的甜蜜點
- 甜蜜點存在與否取決於 SoC 的偵測器精確度
- 高精度偵測器（< 50mV resolution）幾乎可以擋住所有簡單的電壓 glitch

### 對抗手段 4：雙重驗簽（Different Keys/Algorithms）

```
攻擊者 glitch 掉第一次 RSA 驗簽 →
仍需通過第二次用不同 key 的驗簽 →
需要同時 glitch 兩個不同時序的驗簽
```

這個防禦對雷射攻擊效果有限（可以分別打兩次），但對時鐘/電壓 glitch 成本成倍增加。

### 對抗手段 5：Secure Element / HSM

最根本的解法：把驗簽運算放到獨立的 Secure Element（SE）晶片內。SE 本身有全套的故障注入防護（屏蔽層、感測器、冗餘電路），對外只暴露「驗簽請求/結果」的高層介面。攻擊 SE 需要的雷射攻擊能力遠超一般研究者。

代表性 SE：Apple Secure Enclave、Google Titan、ST33 系列、Infineon SLx9 系列

---

## 動手：模擬單 bit fault 讓驗簽比較被跳過

以下是一個用 C 語言和 gcc 真正可以執行的「思想實驗」程式，**模擬**當 fault injection 讓比較指令的條件跳轉被翻轉時會發生什麼。這不是真正的 glitch——是用軟體模擬硬體 fault 的效果，讓你在 WSL/Linux 上真跑，理解 glitch 的邏輯。

```c
/* fault_sim.c — 模擬故障注入翻轉驗簽結果
 *
 * 編譯：gcc -O0 -o fault_sim fault_sim.c
 * 執行：./fault_sim normal   → 正常流程（驗簽失敗，image 被拒絕）
 *       ./fault_sim faulted  → 模擬 fault（驗簽「成功」，惡意 image 被執行）
 *
 * 原理：真實 glitch 讓 CMP/BNE 指令出錯。
 *       這裡用 fault_mode flag 模擬同樣的邏輯效果。
 */

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <stdint.h>

// ─── 模擬的 image 與簽章 ─────────────────────────────────────────────

// 「合法」image（bootloader 信任）
static const uint8_t LEGIT_IMAGE[]   = { 0xAA, 0xBB, 0xCC, 0xDD };
static const uint8_t LEGIT_HASH[4]   = { 0x11, 0x22, 0x33, 0x44 };  // 正確 hash

// 「惡意」image（攻擊者建構，hash 不匹配）
static const uint8_t EVIL_IMAGE[]    = { 0xDE, 0xAD, 0xBE, 0xEF };
static const uint8_t EVIL_HASH[4]    = { 0xDE, 0xAD, 0xBE, 0xEF };  // evil image 的真實 hash

// 儲存在 OTP/fuse 中的「正確 hash」（bootloader 信任這個）
static const uint8_t TRUSTED_HASH[4] = { 0x11, 0x22, 0x33, 0x44 };

// ─── 驗簽函式 ─────────────────────────────────────────────────────────

/* 模擬 RSA 驗簽：比較 image_hash 和 trusted_hash
 * 回傳 0 = 通過，非 0 = 失敗（POSIX 慣例）
 */
int verify_signature(const uint8_t *image_hash, const uint8_t *trusted_hash, int fault_mode) {
    printf("  [verify] 開始驗簽...\n");
    printf("  [verify] image  hash: %02x %02x %02x %02x\n",
           image_hash[0], image_hash[1], image_hash[2], image_hash[3]);
    printf("  [verify] expect hash: %02x %02x %02x %02x\n",
           trusted_hash[0], trusted_hash[1], trusted_hash[2], trusted_hash[3]);

    int cmp_result = memcmp(image_hash, trusted_hash, 4);

    if (fault_mode) {
        /* ─── 模擬 fault 效果 ───
         * 真實 glitch：CPU 執行 CMP r0, #0 後的 BNE 指令時，
         *              電壓 glitch 讓 BNE 沒有跳轉（相當於 NOP）
         * 模擬效果：強制回傳 0，無論 memcmp 結果是什麼
         */
        printf("  [FAULT]  glitch 觸發！CMP 結果被翻轉\n");
        printf("  [FAULT]  原始 cmp_result = %d → 強制變成 0\n", cmp_result);
        return 0;  // 模擬：glitch 讓這裡永遠回傳「成功」
    }

    return cmp_result;  // 正常路徑
}

/* 模擬 bootloader 的開機決策 */
void bootloader_decision(const uint8_t *image, size_t image_len,
                         const uint8_t *image_hash, int fault_mode) {
    printf("\n=== Bootloader Secure Boot ===\n");

    int result = verify_signature(image_hash, TRUSTED_HASH, fault_mode);

    /* ↓ 這條 if 的條件跳轉，是真實 glitch 的主要攻擊目標 */
    if (result == 0) {
        printf("  [BOOT]  驗簽通過！執行 image（%zu bytes）\n", image_len);
        printf("  [EXEC]  image[0..3] = %02x %02x %02x %02x\n",
               image[0], image[1], image[2], image[3]);
        if (image[0] == 0xDE) {
            printf("  [EXEC]  *** 惡意 image 正在執行！攻擊者取得控制 ***\n");
        } else {
            printf("  [EXEC]  正當 image 執行中\n");
        }
    } else {
        printf("  [BOOT]  驗簽失敗（result=%d），拒絕執行\n", result);
        printf("  [BOOT]  系統停止\n");
    }
}

int main(int argc, char *argv[]) {
    if (argc < 2) {
        fprintf(stderr, "用法：%s [normal|faulted]\n", argv[0]);
        return 1;
    }

    int fault_mode = (strcmp(argv[1], "faulted") == 0) ? 1 : 0;

    if (fault_mode) {
        printf("情境：攻擊者提交惡意 image，故障注入啟用\n");
        printf("      （模擬 glitch 翻轉了 CMP/BNE 的結果）\n");
        bootloader_decision(EVIL_IMAGE, sizeof(EVIL_IMAGE), EVIL_HASH, 1);
    } else {
        printf("情境一：合法 image（應通過）\n");
        bootloader_decision(LEGIT_IMAGE, sizeof(LEGIT_IMAGE), LEGIT_HASH, 0);

        printf("\n情境二：攻擊者提交惡意 image，無 fault（應失敗）\n");
        bootloader_decision(EVIL_IMAGE, sizeof(EVIL_IMAGE), EVIL_HASH, 0);
    }

    return 0;
}
```

**執行示範（WSL / Linux，gcc 真跑）**：

```bash
$ gcc -O0 -o fault_sim fault_sim.c
$ ./fault_sim normal
情境一：合法 image（應通過）
=== Bootloader Secure Boot ===
  [verify] 開始驗簽...
  [verify] image  hash: 11 22 33 44
  [verify] expect hash: 11 22 33 44
  [BOOT]  驗簽通過！執行 image（4 bytes）
  [EXEC]  image[0..3] = aa bb cc dd
  [EXEC]  正當 image 執行中

情境二：攻擊者提交惡意 image，無 fault（應失敗）
=== Bootloader Secure Boot ===
  [verify] 開始驗簽...
  [verify] image  hash: de ad be ef
  [verify] expect hash: 11 22 33 44
  [BOOT]  驗簽失敗（result=1），拒絕執行
  [BOOT]  系統停止

$ ./fault_sim faulted
情境：攻擊者提交惡意 image，故障注入啟用
      （模擬 glitch 翻轉了 CMP/BNE 的結果）
=== Bootloader Secure Boot ===
  [verify] 開始驗簽...
  [verify] image  hash: de ad be ef
  [verify] expect hash: 11 22 33 44
  [FAULT]  glitch 觸發！CMP 結果被翻轉
  [FAULT]  原始 cmp_result = 1 → 強制變成 0
  [BOOT]  驗簽通過！執行 image（4 bytes）
  [EXEC]  image[0..3] = de ad be ef
  [EXEC]  *** 惡意 image 正在執行！攻擊者取得控制 ***
```

這個程式碼的要點：`fault_mode` flag 模擬的正是 glitch 在 `if (result == 0)` 之前的條件跳轉指令上的效果。真實 glitch 不需要 `fault_mode` 這個 flag——硬體的電氣異常直接讓那條 `BNE` 指令沒有執行跳轉。

**延伸思考（不需要提交，思考即可）**：
1. 如果加入 `redundant_check` 變式（驗簽呼叫兩次），glitch 攻擊的難度如何變化？
2. 如果 `verify_signature` 改為「成功回傳 1，失敗回傳 0」（Microsoft 慣例 vs POSIX 慣例），條件判斷的邏輯如何改寫？用錯慣例是 T3d 類型漏洞的根源——這個程式碼示範了它為何危險。

---

## 踩雷

1. **「glitch 成功」不等於「繞過 secure boot」**：glitch 讓 CMP 出錯，但如果 bootloader 有多個獨立的驗簽點（BL1 驗 BL2，BL2 再驗 BL3），你需要在每個驗簽點都 glitch 成功。只搞定一個是不夠的。

2. **Glitch detector 的觸發電壓和你期望的甜蜜點可能重疊**：某些 SoC 的 glitch detector 觸發閾值設得太保守（偵測到 -5% 電壓偏移），你幾乎沒有空間在不觸發 detector 的情況下製造夠強的 glitch。這種 SoC 只能用 EMFI 或雷射繞過。

3. **電壓 glitch 的 FET 選擇很關鍵**：FET 的 turn-on time 要在 ns 級別，否則 glitch 的邊緣太緩，對高頻 SoC（> 100MHz）根本沒效果。NMOS FET 如 STS3N3 / AO3400 是常見選擇。ChipWhisperer 的硬體已考慮到這一點，自製電路要特別注意。

4. **「成功 glitch」的重現性可能很低**：找到一個 offset/width 組合有效不代表它每次都有效。溫度變化、電路板的微小差異、甚至宇宙射線（cosmic ray induced SEU）都可能影響。生產攻擊工具時需要統計分析，不是「跑一次成功就行」。

5. **RSA-2048 的 glitch 視窗非常短**：RSA-2048 解密在 ARM Cortex-M4 @ 120MHz 上約需 200ms，但 `memcmp` 在 32 bytes 上只需要幾十個時鐘週期。glitch 的視窗在最後幾十 ns 到幾 μs。offset 要精確到 μs 級才能打中。

6. **Tamper-clearing 在 glitch 成功前可能先觸發**：某些裝置在偵測到 glitch（電壓異常）時會清除 flash 裡的 key 或讓 OTP 的驗簽公鑰讀出異常值，即使你的 glitch 成功讓 CMP 翻轉，但 key 已經被清除，後續開機仍然失敗——而且現在裝置壞了。

---

## 進階延伸

- **Differential Fault Analysis（DFA）**：故障注入不只可以繞過驗簽，也可以用來提取密鑰。在 AES/RSA 的加密運算中注入 fault，比較正確輸出和 faulted 輸出，可以反推部分或全部密鑰。這是密碼學的實現攻擊分支，與 side-channel analysis 並列。

- **EM Probing（電磁探測）vs EMFI**：EMFI 是「發射電磁干擾」，EM Probing 是「接收 IC 洩漏的電磁訊號」（做旁路分析）。兩者工具類似，但攻擊方向相反。研究 EMFI 的同時學習 EM-SCA，工具投資的利用率更高。

- **Grounding 和屏蔽問題**：做 glitch 實驗時，PCB 的接地設計、USB 線的地線耦合、甚至工作台的靜電都會影響結果的重現性。閱讀 Colin O'Flynn 的 「Hardware Hacking Handbook」電路設計章節，在建立工作台前先理解這些問題。

---

## 本章重點

- 四種故障注入技術：電壓 glitch（廉價、廣泛）、時鐘 glitch（不需電源連接）、EMFI（非接觸、可穿透 fuse）、雷射（精度最高、成本最高）
- 三個高價值攻擊目標：`memcmp` 本身、驗簽後的條件跳轉（最常見）、OTP/fuse 讀取
- 參數掃描（offset × width）是 glitch 成功的關鍵，需要系統性的自動化搜尋
- ChipWhisperer 是研究者入門 glitch 的最佳平台（未實測，以公開文件為準）
- 公開案例：Trezor glitch（RDP 繞過）、遊戲機 glitch（RGH）、TF-A 驗簽 glitch（學術）
- 對抗手段：冗餘驗證、隨機延遲、Glitch Detector、Secure Element——各自降低不同攻擊的成功率
- 本章所有硬體操作均未實測；C 程式碼用軟體模擬 fault 邏輯，gcc 真跑

---

## 自我檢核

- [ ] 能描述電壓 glitch 的三個關鍵參數（offset / width / voltage）及各自的作用
- [ ] 能說出為什麼「驗簽後的條件跳轉」比「memcmp 本身」更容易成為 glitch 目標
- [ ] 能解釋 Trezor One glitch 攻擊的完整流程（STM32 RDP + SRAM seed）
- [ ] 知道 ChipWhisperer 的四個硬體型號及其大致定位
- [ ] 能說出 EMFI 相對於電壓 glitch 的兩個主要優點
- [ ] 理解為什麼「redundant check + random delay」可以降低 glitch 成功率但不能消除
- [ ] 能解釋 fault_sim.c 中 fault_mode=1 時模擬的是什麼硬體現象

---

## 延伸閱讀

1. **"Introduction to Voltage Glitching" — Colin O'Flynn（NewAE Technology）**
   讀哪裡：ChipWhisperer Read the Docs（readthedocs.io）的 Tutorial 系列，尤其 Lab 4（Clock Glitching）和 Lab 5（Voltage Glitching）
   學什麼：ChipWhisperer 工具鏈的完整實作工作流，參數掃描的程式化方法，真實 MCU（STM32）的 glitch 實驗
   關聯：對應本章 ChipWhisperer 工具鏈介紹，是未實測內容的最佳實作補充

2. **"Wallet.fail: Hacking the most popular cryptocurrency hardware wallets" — Ledger Donjon（35C3，2018）**
   讀哪裡：YouTube 錄影（35c3.chaosvideos.com）及投影片 PDF，搜尋「wallet.fail 35C3」
   學什麼：Trezor One 和 Ledger Blue 的完整攻擊流程，voltage glitch 繞過 STM32 RDP 的具體參數範圍，以及「硬體錢包的安全性不是絕對的」這個重要觀念
   關聯：本章 Trezor 案例的第一手資料，看 Donjon 如何從「想法」到「成功攻擊」的完整過程

3. **"One Glitch to Rule Them All: Fault Injection Attacks Against AMD's Secure Encrypted Virtualization" — Buhren et al.（TCHES 2021）**
   讀哪裡：TCHES 2021 論文（tches.iacr.org），搜尋 AMD SEV fault injection
   學什麼：現代企業級 CPU（AMD EPYC）的 secure boot 也能被 voltage glitch 攻擊——AMD SEV 的 PSP（Platform Security Processor，即 ARM TrustZone 核心）被 glitch 讓 PSP 韌體的驗簽被繞過，攻擊者可以運行惡意 PSP 韌體
   關聯：把本章嵌入式 MCU 的 glitch 技術連結到 x86 企業級平台，說明「不只是路由器和遊戲機」，接 Ch 14（Intel ME/AMD PSP）的主題

→ [下一章](./35-spi-tamper-cold-boot.md)
