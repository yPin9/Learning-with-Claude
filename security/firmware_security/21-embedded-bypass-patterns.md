# Ch 21 — 嵌入式 Secure Boot 繞過模式

> **目標**：把 Part 3 所有章節（ARM TF-A、U-Boot、AVB、MTK SoC）的繞過手法收斂成有系統的類型學，建立一張 x86 vs 嵌入式的繞過模式並列總表，作為後面 Part 5（Secure Boot 繞過鏈）的知識地基。

## 為什麼需要類型學？

研究者看到的每個繞過案例都是具體的，某個 CVE、某個廠商、某個命令。但真正能快速找到新目標的攻擊面，靠的是類型學思維：

- 看到一個新裝置，你問的不是「這個裝置有沒有 CVE」
- 你問的是「這個裝置屬於哪種類型？fuse 燒了嗎？有沒有 debug port？下載模式開放嗎？」

類型學讓你把對已知漏洞的理解轉成對未知目標的攻擊假設，再用工具驗證。

本章把嵌入式 secure boot 繞過分成六大類型，每類配一個真實例子，最後給跨平台的類型學總表。

---

## 類型一：Fuse 未燒（SBC_EN=0 / 開發板洩漏到量產）

### 機制

SoC 的 secure boot 功能由 efuse（OTP，One-Time Programmable）控制。efuse 燒 1 後不可逆，但**燒的動作需要人為執行**，量產流程的疏失或開發板出廠，就可能讓 SBC_EN 保持 0。

```
efuse 狀態：SBC_EN = 0
               │
               ▼
    BROM 跳過 DA/Preloader 簽章驗證
               │
               ▼
    任何 image 都被接受並執行
               │
               ▼
    攻擊者完整控制開機鏈
```

### 真實案例：MTK 開發板洩漏

部分 MTK 參考設計板（EVB）以「工程機」名義流入二手市場，SBC_EN 未燒。研究者可以用 mtkclient 直接讀寫 eMMC，不需要任何 exploit：

```bash
# SBC_EN=0 的裝置，mtkclient 不需要 exploit payload
python mtk.py rf --filename full_dump.bin   # 完整 eMMC dump
python mtk.py efuse                          # 讀 efuse，確認 SBC_EN=0
```

另一個例子：某品牌路由器（基於 MTK MT7621）的量產版本，因工廠誤用開發韌體，BROM 未鎖，UART console 完全開放。研究者接 UART 即得 root shell。

### 偵測方法

```bash
# 嘗試 BROM 模式下讀 efuse（若裝置支援）
python mtk.py efuse

# 或從 Preloader log（UART）找關鍵字
# 正常量產機：[SEC] SBC enabled
# 未燒機：[SEC] SBC disabled / [SEC] bypass
```

---

## 類型二：Debug Port 開著（JTAG / UART / EDL / BROM）

### 機制

硬體除錯介面（JTAG、SWD）讓你在任意點暫停 CPU、讀寫記憶體、修改寄存器。量產裝置通常應該鎖閉這些介面，但：

- 電路板 layout 仍然有測試點（TP）
- 對應的 fuse 可能未燒（`JTAG_DISABLE=0`）
- UART console 輸出仍然開著（只讀，但 bootloader 可能有互動式介面）

```
                ┌──── JTAG（OpenOCD → 暫停 CPU → 改 PC → 跳過驗簽）
Debug Port ─────┤
                ├──── UART（U-Boot shell → set bootargs → init=/bin/sh）
                │
                └──── EDL/BROM USB（不是 JTAG，但同樣提供直接存取）
```

### 真實案例：Amazon Echo（ARM Cortex-A7）

2018 年研究者（Mark Barnes / Context IS）在 Echo 二代電路板找到 UART 測試點，接線後取得 U-Boot 的 serial console，設定 `bootargs` 注入 `init=/bin/sh`，獲得 root shell——即使 Secure Boot 已啟用。原因：**Secure Boot 驗 kernel image，但不驗 kernel commandline**，`init=/bin/sh` 是合法的 commandline 參數。

```
UART 接線 → U-Boot console → setenv bootargs "... init=/bin/sh" → boot → root
```

類似案例在路由器、機上盒研究中每年都有，UART 測試點是最常見的入口。

### 偵測與利用

```bash
# 找 UART 測試點：電路板上的 GND/TX/RX/VCC，通常 3.3V
# 工具：USB-UART 轉接器（CH340/FT232/CP2102）
# 波特率：常見 115200、57600、38400、9600（全部試一遍）
minicom -s   # 設定波特率，接上後觀察 bootlog

# 若有 U-Boot console（按任意鍵中斷自動開機）：
printenv            # 看 bootargs
setenv bootargs "... init=/bin/sh"
boot
```

---

## 類型三：簽章驗證邏輯錯誤

這一類是最廣泛的，問題不在「有沒有驗」而在「怎麼驗」。

### 子類型 3a：只驗 Header，不驗 Body

```
Image 格式：
  [header: 256 bytes]  ← 驗簽對象
  [body: N bytes]      ← 沒驗
```

攻擊者把 header 保留合法，把 body 替換成惡意 payload。Bootloader 讀到「header 驗通」就跳入執行，執行的是替換過的 body。

**真實案例**：某些舊版 Android `boot.img` 的 bootloader 只驗 Android header 的前 N bytes，沒有驗整個 image。刷入「header 合法但 kernel 部分被竄改」的 boot.img 可以執行任意 kernel。（具體影響廠商不公開，CVE 存在於 2016-2018 年間的多個 SoC vendor）。

### 子類型 3b：Length 混淆（Integer Overflow / Truncation）

```c
// 有問題的驗簽實作
uint32_t image_size = header->image_size;  // 攻擊者控制
verify_signature(image, image_size);        // 驗 image 前 image_size bytes
execute(image, actual_file_size);           // 執行整個檔案

// 若 image_size 被設為很小（只涵蓋 header），
// 驗簽通過，但執行的是整個檔案（含後面的惡意部分）
```

### 子類型 3c：TOCTOU（Time-of-Check to Time-of-Use）

在驗簽完成（check）到實際執行（use）之間存在時間窗，攻擊者替換 image：

- 多核心 SoC：core 0 驗簽，core 1 修改記憶體（需要 DMA 或共享記憶體攻擊）
- DMA 設備對 DRAM 的竄改（DMA attack）

**真實案例**：Thunderspy（Thunderbolt DMA attack on UEFI）的精神。ARM 世界的 CMA（Contiguous Memory Allocator）可能在特定條件下被 DMA 設備修改驗過的 kernel buffer。

### 子類型 3d：回傳值誤判

```c
// 有問題的實作
int result = verify_signature(image);
if (result)  // 攻擊者期待：result=0 是失敗，非零是成功
    execute(image);

// 但驗簽函式實作：0=成功，非零=失敗（POSIX 慣例）
// → 結果反了，驗失敗時 result!=0 → 判定驗通 → 執行惡意 image
```

這類 bug 在移植驗簽庫時很常見，不同的 convention 被混用。

### 偵測方法

逆向 bootloader，找驗簽呼叫後的條件跳轉：

```
Ghidra / IDA 流程：
  搜字串 "verify" / "signature" / "image"
  找呼叫驗簽函式的位置
  分析回傳值如何被使用（比較、條件跳轉）
  用錯誤的 image 在 QEMU 上跑，看是否意外執行
```

---

## 類型四：Downgrade / Rollback 未防護

### 機制

Rollback protection 需要三個條件同時成立：
1. image 帶有版本號（rollback index）
2. 有安全儲存（RPMB/TEE fuse）記著最小允許版本
3. 開機時確實比較兩者

缺少任一條件就有降級空間。

```
攻擊路徑分析：
  ① 沒有版本號     → 直接刷舊版 image，無任何阻攔
  ② 版本號在普通 storage（eMMC misc 分區）
       → OS root 後清掉，或直接寫入假版本號
  ③ 有 RPMB 但 TEE 初始化失敗時 fallback 為「不驗」
       → 讓 TEE 初始化失敗（timing attack、截斷電源）
```

**真實案例：某品牌 Android 路由器**（2020）

路由器跑 Android，rollback_index 存在 misc 分區而非 RPMB，因為廠商移植 AVB 時沒有接 TEE 的 rollback API。研究者取得 adb root 後：

```bash
adb shell "dd if=/dev/zero of=/dev/block/by-name/misc bs=4096 count=4"
adb reboot
# 重啟後 rollback counter 歸零，可刷任意舊版 image
```

然後刷含有已知 CVE（kernel uaf）的舊版本，取得更高權限。

---

## 類型五：未驗證的載入路徑（Recovery / DFU / Download Mode）

### 機制

主開機路徑（正常開機）有嚴格驗證，但「替代路徑」往往是事後追加的，驗證邏輯較鬆或完全沒有：

```
主路徑：  BROM → Preloader → LK（驗 AVB）→ Kernel  ✓ 驗完整
替代路徑：
  Recovery Mode → 接受來自外部 storage 的 OTA package → 驗章邏輯不同 ？
  DFU Mode      → 接受 USB 傳入的 firmware update     → 驗章邏輯不同 ？
  Download Mode → 廠商工具專用                         → 有時完全不驗 ✗
  Factory Mode  → 廠測功能                             → 通常沒有驗章 ✗
```

**真實案例：Kindle Fire HD（2012）**

Amazon Kindle Fire HD 的 Recovery mode 接受 OTA zip，但 Recovery 的 RSA key 不同於 bootloader 的 key，研究者發現可以簽一個 Recovery 認可但 bootloader 沒預期的 OTA zip，透過 Recovery 刷入修改過的 system 分區。

**真實案例：任天堂 Switch（2018）—— Fusée Gelée**

Switch 的 BROM 有 USB recovery mode（RCM mode），存在 length overflow 漏洞，讓攻擊者可以傳入超長 payload 覆蓋 BROM 的堆疊，劫持控制流。這個 BROM 漏洞影響所有初版 Switch（Tegra X1），無法修補。

---

## 類型六：公開的測試 / 預設金鑰

### 機制

簽章的強度等於金鑰的保密性。以下情況讓金鑰等同公開：

- **測試金鑰留存量產**：開發期間用測試 RSA key，量產時忘記換
- **金鑰嵌在公開的二進位**：bootloader 的驗簽邏輯直接包含公鑰，而對應私鑰在某次 leak 中曝光
- **預設金鑰廣泛使用**：SoC 廠商的 reference design 用同一把 key，多個 ODM 都用同一把

```
攻擊者動作：
  1. 從 bootloader 二進位提取公鑰（Ghidra 或 avbtool info_image）
  2. 在已知 leak 的私鑰資料庫比對
  3. 找到對應私鑰 → 可以簽任意 image
  4. LOCKED 裝置也接受攻擊者的 image（因為 key 相同）
```

**真實案例：多家 Android 廠商的 test signing key leak（2022-2023）**

Samsung（Android 13 時代）、LG、Mediatek 的平台 signing key 被發現泄漏，這些 key 原本用於簽 platform apps（獲得高權限），洩漏後惡意 APK 可以用同樣的 key 簽章，被 Android 信任為系統 app。雖然這是 APK signing key 而非 bootloader key，邏輯完全相同。

**真實案例：U-Boot Secure Boot 測試 key**

U-Boot 的官方文件和 example 使用 `test/` 目錄下的測試 key 對。研究者在某些嵌入式產品找到 U-Boot 仍使用 test key，且 private key 就在 U-Boot source 樹裡（`test/keys/dev.key`）：

```bash
# 找 U-Boot 是否用 test key
strings u-boot.bin | grep -i "test\|dev.key\|example"
# 若 bootloader 的 public key 和 U-Boot source 的 test key 匹配：
openssl rsa -in test/keys/dev.key -pubout | diff - extracted_pubkey.pem
```

---

## 跨平台繞過模式類型學總表

| 類型 | x86 UEFI 對應 | ARM / 嵌入式對應 | 偵測關鍵問題 | 工具 |
|------|--------------|----------------|-------------|------|
| **T1: Fuse 未燒** | UEFI Secure Boot 從 BIOS 介面停用（等效） | SBC_EN=0 / efuse 未燒 | 裝置 efuse 狀態？ | mtkclient efuse read、CHIPSEC |
| **T2: Debug Port** | 無 x86 直接對應（PCH debug、ME JTAG） | JTAG/SWD/UART boot console | 電路板測試點？波特率？ | OpenOCD、minicom、邏輯分析儀 |
| **T3a: Only-Header Verify** | UEFI 只驗 PE header signature | boot.img header 驗完不驗 body | objdump 看 image 結構；刷竄改 body 看是否開機 | avbtool、binwalk |
| **T3b: Length Confusion** | Capsule update 長度解析 bug（PixieFail） | DA/Preloader image size 欄位混淆 | 靜態逆向 length 欄位使用 | Ghidra、IDA |
| **T3c: TOCTOU** | DXE driver 載入後被 SMM callout 修改 | DMA 修改已驗通的 kernel buffer | 多核心時序、DMA 路徑 | CHIPSEC DMA test |
| **T3d: Return Value** | UEFI 驗簽 API 回傳 convention 混用 | BROM/Preloader verify 回傳值判斷相反 | 逆向 if(verify()) 條件 | Ghidra control flow |
| **T4: Rollback 未防** | dbx 沒更新、舊版 GRUB 仍可開機 | rollback_index 存普通 eMMC | 查 rollback 儲存後端（RPMB？fuse？） | avbtool、fastboot getvar |
| **T5: 替代路徑** | UEFI Shell、Option ROM 繞 db 檢查 | Recovery mode、DFU、Download mode | 所有載入路徑的驗章邏輯是否一致？ | SP Flash Tool、adb reboot recovery |
| **T6: 公開金鑰** | db 使用廣泛 shared key（BIOS Connect） | U-Boot test key、廠商 ref design key | 提取 public key 比對已知 leak | openssl、avbtool、strings |

---

## 類型的組合與利用鏈

真實攻擊很少只用一個類型，通常是組合：

```
範例利用鏈 A（路由器）：
  T2（UART console 開著）
    → 進 U-Boot shell（T5 的變形：UART 是另一種替代入口）
    → 發現 T1（SBC 未啟用，可刷任意 image）
    → 刷自訂 Linux → 完整控制

範例利用鏈 B（Android 手機）：
  T6（從 stock ROM 逆向取得簽章 key hash，但私鑰未洩漏，只確認是否 test key）
    → T4（rollback 存 misc 分區）→ 清 rollback counter
    → T5（Download mode 繞過 fastboot 限制）→ 刷降級 ROM
    → 使用舊版 kernel CVE 提權

範例利用鏈 C（Switch Fusée Gelée）：
  T5（Recovery/RCM mode 暴露在 USB）
    → T3b（BROM 的 length 欄位缺乏驗證）
    → Stack overflow → 執行任意 payload → 永久 BROM exploit
```

---

## 防禦對應清單

每個類型都有對應的防禦，審計嵌入式裝置時用這份清單逐項確認：

| 類型 | 防禦措施 | 確認方法 |
|------|---------|---------|
| T1 | SBC_EN 在量產前燒錄，由 QA 流程稽核 | mtkclient efuse read、CHIPSEC |
| T2 | JTAG fuse 燒閉；UART console 在量產版停用；測試點移除或填膠 | 電路板探測、邏輯分析儀掃描 |
| T3a-d | 驗簽覆蓋整個 image；convention 統一（0=成功）；用 RTOS/TrustZone 隔離驗簽記憶體 | code review、靜態分析、fuzz |
| T4 | rollback index 用 RPMB 或 fuse 存；失敗時不 fallback 而是拒絕開機 | 嘗試刷舊版 image，確認被拒 |
| T5 | 所有載入路徑統一驗章邏輯；Recovery OTA 和正常 OTA 用同一套驗證 | 逐一測試 Recovery/DFU/Factory 路徑 |
| T6 | 量產前換 key；私鑰不進 source control；用 HSM 保護簽章私鑰 | 從 image 提取 public key，查 known-leaked-key 資料庫 |

---

## 類型學 vs Part 5 的銜接

Part 5（Ch 28-32）會把同樣的類型學套到 x86 UEFI Secure Boot 的完整繞過鏈：

```
Ch 29 繞過類型學  ←  本章的 T1-T6 在 x86 上的展開
Ch 30 真實利用鏈  ←  BootHole(T3b+T5), BlackLotus(T4+T6), LogoFAIL(T3+T5)
Ch 31 bootkit 構造 ← 繞過成功後如何持久化
```

BootHole（CVE-2020-10713）是 GRUB2 的 T3b（length 混淆），讓攻擊者繞過 Secure Boot 載入惡意 GRUB。BlackLotus 結合 T4（rollback 未防護的 UEFI）和 T6（已知有效的 boot loader hash 繞 dbx）。把本章的類型學記住，Part 5 的每個案例都能快速歸類。

---

## 踩雷

1. **「驗簽成功」不等於「整條信任鏈安全」**：BROM 驗通 Preloader，不代表 Preloader 驗通 LK 的邏輯沒有 T3d 問題。每層都要獨立分析。

2. **T2（UART）不一定能寫**：很多裝置的 UART TX/RX 都有，但 U-Boot 設定了 `CONFIG_DISABLE_CONSOLE` 或 bootdelay=0，console 在開機後立刻停用。見到 UART log 不要以為就有互動。真正確認要測「開機時狂按 Enter 或空白鍵能否中斷 autoboot」。

3. **T6 的 test key 只在 Secure Boot 啟用的裝置才有意義**：如果 T1（fuse 未燒），本來就接受任意 image，test key 無意義。T6 的危險是「有 secure boot 但用 leaked key」。

4. **T4 的 rollback 有多個 location**：如本課 Ch 19 說的，AVB 最多 32 個 rollback index location。清了 location 0 但 location 1 的 vendor counter 仍有效，舊版 vendor 分區還是會被擋。

5. **T5 的「替代路徑」包含硬體層**：SPI flash 直接讀寫（燒 programmer）、eMMC 焊接讀取都是替代路徑，只是需要硬體工具。不要只測試軟體層的 recovery/DFU。

6. **類型學是假設起點，不是終點**：看到一個裝置，T2 是你第一個假設，但可能是 false。花了兩小時找測試點才確認沒有開放 UART，才去試 T1。類型學給的是搜尋優先順序，不是保證。

---

## 進階延伸

- **自動化類型學掃描**：CHIPSEC 的模組化設計讓你可以對 x86 裝置自動執行 T1-T6 部分項目的測試（SPI 保護、Secure Boot 狀態、SMM 鎖閉）。嵌入式這邊 binwalk + mtkclient 的組合可以做類似的半自動化稽核。

- **韌體供應鏈的 T6 系統性問題**：SBOM（Software Bill of Materials）是解法的一部分，但 key management 才是核心。IoT 裝置的 key ceremony 實務（HSM、分段 key、key escrow）是一個完整的子領域，GSMA IoT Security Guidelines 有詳細規範。

- **Fault Injection 作為 T3 的硬體版本**：電壓故障注入（voltage glitching）可以在 RSA 驗簽的關鍵指令時造成計算錯誤，讓錯誤的 signature 通過驗證。這是 Part 6（Ch 34）的主題，是 T3 的硬體衍生版本。

---

## 動手練習

### 練習：類型判斷思維演練

拿以下三個場景，分別判斷主要類型並列出驗證步驟：

**場景 A**：一台工業閘道器，UART 接上後發現有 U-Boot prompt，但 `printenv` 顯示 `secure_boot=yes`，`boot` 命令執行時說「image not signed」。

**場景 B**：一台家用路由器，BROM 用 mtkclient 識別為 MT7622，efuse read 顯示所有 fuse=0，但裝置是正常量產版。

**場景 C**：一台 Android 手機，`fastboot getvar all` 顯示 `unlocked: no`，但 `ro.boot.verifiedbootstate=orange`（應該 LOCKED 時是 green）。

答案思路（可展開驗證）：

- A：裝置是 T2（UART 開著），secure boot 主路徑啟用（T3 待驗）。先逆向 U-Boot 的 `do_bootm` 看驗簽邏輯，測試 T3d（回傳值）和 T3a（只驗 header）。
- B：T1（fuse 未燒），直接 `python mtk.py rf` dump eMMC，無需 exploit。
- C：`unlocked: no` 但 orange state 是矛盾的，可能是 T5（有非 fastboot 的路徑解鎖了 bootloader，但 fastboot 的 lock 變數沒同步），也可能是廠商 bug。優先查是否有 adb backdoor 或 download mode 被利用。

---

## 本章重點

- 六大類型：Fuse 未燒 / Debug Port / 驗簽邏輯錯誤（4子類）/ Rollback 未防 / 替代路徑 / 公開金鑰
- 每個類型有對應的真實案例，方便對照記憶
- 類型學是假設優先順序，不是保證——需要工具驗證每個假設
- 真實攻擊通常組合多個類型，形成利用鏈
- 防禦側的清單：每個類型都有對應的確認方法，做安全稽核時逐項核對
- 這份類型學直接銜接 Part 5 的 x86 Secure Boot 繞過鏈（Ch 28-32）

---

## 自我檢核

- [ ] 能說出六大繞過類型並各舉一個真實例子
- [ ] 知道 T3（驗簽邏輯錯誤）的四個子類型，並能解釋每個的根本原因
- [ ] 能建立一條包含至少兩個類型的假設利用鏈，並說明每步用什麼工具驗證
- [ ] 知道 T4（rollback 未防）的三個失敗條件（無版本號 / 普通 storage / TEE fallback）
- [ ] 能把 BootHole 和 BlackLotus 分別歸類到哪些類型
- [ ] 理解為什麼類型學只是起點，需要工具實際確認

---

## 延伸閱讀

1. **"Fusée Gelée: Tegra X1 BROM exploit" — Kate Temkin（2018）**
   讀哪裡：原始漏洞報告（GitHub `Qyriad/fusee-launcher` 的 README 和技術說明）
   學什麼：T3b（length 混淆）在 BROM USB 協定中的精確觸發，以及如何從 USB overflow 到任意代碼執行
   關聯：本章 T3b 和 T5 的最佳教學案例，也是 Ch 34（fault injection）的精神對照

2. **GSMA IoT Security Guidelines（CLP.11/CLP.12）**（`gsma.com/iot/iot-security/iot-security-guidelines/`）
   讀哪裡：CLP.11 Section 5（device security）的 key management 和 secure boot 建議
   學什麼：T6（公開金鑰）的系統性防禦——key ceremony、HSM、獨立的 production key
   關聯：從攻擊類型學回頭看防禦設計的工業界標準，直接對應本章防禦清單

3. **"BootHole: There's a hole in the boot" — Eclypsium（2020）**（`eclypsium.com/research/theres-a-hole-in-the-boot/`）
   讀哪裡：完整技術報告的漏洞分析章節（GRUB2 config file parsing，CVE-2020-10713）
   學什麼：T3b 在 x86 GRUB2 的實現形式，以及 SBAT 撤銷機制如何設計來對抗這類 bypass
   關聯：直接接 Part 5 的 Ch 29-30，建立從本章類型學到 x86 具體案例的連結

→ [下一章](./practice-c-uboot-analysis.md)
