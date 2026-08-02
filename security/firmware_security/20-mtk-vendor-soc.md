# Ch 20 — MTK / Vendor SoC 韌體

> **目標**：理解 MediaTek SoC 的完整開機鏈（BROM → Preloader → LK → Kernel），掌握 Download Mode 的運作原理，以及 BROM 漏洞（Kamakiri）和 mtkclient 工具如何在 Secure Boot 生效前獲得任意讀寫能力。

## 為什麼 MTK SoC 值得單獨研究？

MediaTek 是全球出貨量最大的行動 SoC 廠商之一，覆蓋低中端 Android 手機、IoT 裝置、路由器、機上盒。MTK 的開機鏈有幾個特性讓它成為嵌入式安全研究的重要目標：

- **BROM 是 ROM**：直接燒在晶片裡，無法韌體更新。BROM 裡的漏洞一旦被發現，理論上影響整個 SoC 世代的所有量產裝置，且無法修補。
- **Download Mode 的設計哲學**：MTK 為了工廠燒錄設計了完整的 USB 下載協定，這個功能在市售裝置上仍然部分暴露——它是攻擊面，也是研究者的入口。
- **接 mtk_firmware 面試線**：MTK SoC 的開機鏈知識是嵌入式韌體工程師面試的核心考點，也是 Android 安全研究的前置知識。

---

## MTK 開機鏈全景

```
┌─────────────────────────────────────────────────────────────────┐
│  Power On / Reset                                               │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  BootROM（BROM）                                                │
│  • 燒死在晶片，無法更新                                         │
│  • 初始化 CPU 最小集合（cache off、MMU off、時脈最低）           │
│  • 偵測開機模式（正常 / Download Mode / Factory Mode）           │
│  • 若 Secure Boot 啟用：驗 Preloader 的 RSA 簽章               │
│  • 載入 Preloader 到 ISRAM 並跳入                               │
│  程式碼：~100 KB，儲存在 SoC 內部 ROM                           │
└──────────────────────────┬──────────────────────────────────────┘
                           │ 驗簽（或跳過）後跳入
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  Preloader（PL）                                                │
│  • 儲存在 eMMC 的 boot0 分區（或 UFS 對應位置）                  │
│  • 主要工作：EMI（External Memory Interface）初始化 → DRAM 起來 │
│  • 初始化 UART console（早期 log 在這裡）                       │
│  • 初始化 security 子系統（TEE/TZ 早期設定）                    │
│  • 載入並驗簽 LK                                                │
│  • 進入 META mode / ADB 早期支援（廠商客製）                    │
│  工具對應：SP Flash Tool 的 Preloader Download Agent 就是更新這裡│
└──────────────────────────┬──────────────────────────────────────┘
                           │ DRAM 可用後跳入
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  LK（Little Kernel / aboot 精神繼承者）                         │
│  • 儲存在 eMMC 的 lk / aboot 分區                               │
│  • 完整的 UART/USB 介面、fastboot 協定在這裡                    │
│  • 執行 AVB 2.0 驗證（vbmeta → boot → system dm-verity 參數）   │
│  • 處理 bootloader unlock、fastboot 命令                        │
│  • 跳入 Linux kernel                                            │
│  工具對應：fastboot、mtkclient 的 DA 模式就是繞過 LK             │
└──────────────────────────┬──────────────────────────────────────┘
                           │ 跳入 kernel entry point
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  Linux Kernel（ARM64）                                          │
│  • 掛載 system（dm-verity active）                              │
│  • 啟動 init → Android runtime                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 各層責任對照表

| 層次 | 儲存位置 | 主要安全責任 | 可被更新 |
|------|---------|------------|---------|
| BROM | SoC 內部 ROM | 驗 Preloader 簽章（若 SBC 啟用） | 否（ROM） |
| Preloader | eMMC boot0/1 | 初始化 DRAM、驗 LK 簽章 | 是（需 Download Mode） |
| LK | eMMC lk 分區 | fastboot、AVB 2.0 驗 boot/system | 是（fastboot 或 Download Mode） |
| Boot.img | eMMC boot 分區 | kernel + initrd | 是（fastboot） |

---

## 開機模式偵測

BROM 啟動時根據 GPIO 狀態和特定條件決定進入哪種模式：

```
                 ┌─ 正常開機 ─────────────── → Preloader → LK → Kernel
BROM 偵測        │
(GPIO + USB)     ├─ Download Mode ─────────── → 等待 SP Flash Tool / mtkclient
                 │  (BROM USB stack active)
                 │
                 └─ META Mode / Factory Mode ─ → Preloader 的廠測功能
```

**BROM Download Mode（或稱 BROM mode）觸發方法**：
- 按住 Vol+ 或 Vol- 同時接 USB（不同 SoC 不同）
- 電池完全沒電時的 cold boot 某些 SoC 會觸發
- 拔除 eMMC 或 eMMC 損壞時 BROM fallback
- 部分舊裝置可從 ADB 觸發：`adb reboot download`（進 Preloader download mode，不是 BROM level）

**Preloader Download Mode 與 BROM Download Mode 的區別**：
- BROM mode：BROM USB stack 直接運行，下載的是完整 DA（Download Agent）
- Preloader mode：Preloader 已執行（DRAM 已初始化），只接受 Preloader-level 的下載命令

這個區別在攻擊中至關重要：BROM mode 的攻擊面遠大於 Preloader mode。

---

## SP Flash Tool 與 Download Agent 架構

SP Flash Tool（Smart Phone Flash Tool）是 MTK 官方提供的 PC 端燒錄工具。其運作依賴 **Download Agent（DA）**：

```
PC（SP Flash Tool）
    │
    │ USB（MTK BROM 自訂協定）
    │
    ▼
BROM（裝置端）
    │
    │ BROM 接受並執行 DA
    ▼
DA（Download Agent）
    │  DA 現在在 ISRAM 或 DRAM（若 Preloader 已跑）執行
    │  實作完整的燒錄命令：讀/寫 eMMC、讀 efuse、設定分區表
    ▼
eMMC（被 DA 直接讀寫）
```

DA 本身是 MTK 簽章的可執行 image，只有 MTK 或獲得授權的廠商能產生有效 DA。這就是 Secure Boot（SBC）的核心保護：即使攻擊者能接觸 BROM 的 USB stack，沒有有效的 DA，BROM 不會執行任意程式碼。

**關鍵問題**：BROM 在執行 DA 之前如何驗簽？這個驗證邏輯在 ROM 裡，是漏洞的主要分析目標。

---

## Secure Boot Control（SBC）與 Fuse

MTK SoC 的 Secure Boot 狀態由 efuse 控制：

```
efuse 位元（一次性可程式，無法清除）：
  SBC_EN（Secure Boot Control Enable）
    = 0  → BROM 跳過 DA 簽章驗證（開發/量產前狀態）
    = 1  → BROM 必須驗通 DA 簽章才執行（安全量產狀態）

  JTAG_DISABLE
    = 0  → JTAG 開著（研究者的金礦）
    = 1  → JTAG 關閉

  ANTICLONE（各 SoC 實作名稱不同）
    → 鎖定 SoC 與特定裝置硬體繫結的 key
```

**SBC_EN = 0 的裝置**：這包括所有市售量產前的工程機、部分疏失導致 SBC_EN 未燒的量產裝置（少見但存在），以及開發者手上的測試板。對這類裝置，BROM 接受任意 DA，可以直接用 mtkclient 讀寫 eMMC 的任何位置。

**SBC_EN = 1 的裝置（大多數市售機）**：需要繞過 DA 簽章驗證——這就是 BROM 漏洞的用途。

---

## Kamakiri 與 MTK BROM 漏洞

**本段為教育性原理說明，所述行為基於公開研究與漏洞報告。需真機特定 SoC 才能驗證實際效果，所有「攻擊步驟」標記為未實測。**

### 漏洞歷史

Kamakiri（及後續相關漏洞）是針對 MTK BootROM 的 USB 協定漏洞，由安全研究者發現並公開（2019-2021 年間）。影響的 SoC 包括 MT6571、MT6580、MT6582 等數十款中低端 SoC。

漏洞的核心概念（基於公開論文和逆向分析）：

```
正常 DA 下載流程：
  1. PC 發送 DA image
  2. BROM 計算 DA 的 RSA 簽章
  3. 驗通 → 搬到 ISRAM → 跳入執行

Kamakiri 利用的缺陷（概念層）：
  USB 協定的某個命令（0x19 或對應 SoC 的特定命令）
  在驗簽完成「之前」就可以觸發記憶體讀寫
  或者：長度/偏移欄位驗證不足，導致可以修改 BROM ISRAM 的特定區域
  → 在 RSA 驗證邏輯執行前，將驗證結果位置覆蓋為「驗通」
  → BROM 誤以為 DA 已通過驗簽，執行任意 DA payload
```

這個類型的漏洞屬於 **驗證邏輯的競態或狀態機缺陷**，不是純記憶體安全漏洞，也不是 RSA 被破解。

### mtkclient

mtkclient（`https://github.com/bkerler/mtkclient`）是開源工具，整合了針對多款 MTK SoC 的 BROM exploit 和 DA 協定實作：

```bash
# 以下命令為示意，需要特定 SoC 的裝置，本段未實測
# 安裝
pip install mtkclient

# 偵測 SoC 並嘗試 BROM exploit
python mtk.py stage1

# 若成功（SoC 支援且 exploit 生效）：
# 直接讀取整個 eMMC（含 boot0/1 分區，即 Preloader）
python mtk.py rf --filename full_dump.bin

# 讀特定分區（例如 vbmeta）
python mtk.py r vbmeta vbmeta.img

# 寫分區（繞過 AVB、fastboot 鎖）
python mtk.py w boot patched_boot.img

# 讀取 efuse（確認 SBC_EN 狀態）
python mtk.py efuse
```

**驗證方法**（不需要真機）：
1. 用 `avbtool info_image` 分析公開 MTK 機型的 vbmeta dump，驗證 rollback_index、descriptor 結構
2. 用 USB wireshark 捕捉 SP Flash Tool 和 BROM 之間的通訊，對照 mtkclient 原始碼理解協定

**本段未實測，為理論預期行為。**

---

## Qualcomm EDL（Emergency Download Mode）對照

| 面向 | MTK Download Mode | Qualcomm EDL |
|------|------------------|-------------|
| 協定 | MTK 自訂 USB 協定 | Sahara / Firehose 協定 |
| PC 端工具 | SP Flash Tool、mtkclient | QPST、edl（開源） |
| 主要 payload | Download Agent（DA） | Firehose programmer（.elf） |
| 簽章驗證 | BROM 驗 DA 的 RSA 簽章 | BROM 驗 Firehose 的 RSA 簽章 |
| 漏洞研究 | Kamakiri / mtkclient | 多個 Sahara protocol bug |
| Fuse 控制 | SBC_EN | JTAG/USB debug fuse |
| 若 fuse 未燒 | 接受任意 DA | 接受任意 Firehose programmer |
| 市售機暴露程度 | 部分裝置可觸發 BROM mode | EDL mode 在最終量產通常封閉 |

兩者的攻擊哲學完全相同：找到在執行簽章驗證之前可被觸發的協定命令，用它繞過簽章，載入自訂 payload 獲得對 eMMC 的直接讀寫。

---

## 攻擊鏈完整流程（概念）

```
攻擊者目標：在 SBC_EN=1 的 MTK 裝置上繞過 AVB，刷入 root 的 boot.img

Step 1: 觸發 BROM Download Mode
   → 裝置進入 BROM USB stack

Step 2: 傳送 BROM exploit payload
   → 利用 BROM 協定漏洞繞過 DA 簽章驗證
   → BROM 執行攻擊者的自訂 DA

Step 3: 自訂 DA 執行
   → 擁有對 eMMC 的完整讀寫（比 fastboot 權限更高）
   → 讀取原始 vbmeta、boot 分區
   → 寫入 patched boot.img（含 Magisk）
   → 修改 vbmeta flags（disable-verity）

Step 4: 正常開機
   → AVB 驗證被繞過（vbmeta flags 設為停用）
   → boot.img 含 root
```

這條鏈的每一步都有防禦對應：
- Step 2 被防禦：修補 BROM（不可能，已燒死）→ 改為更新 DA 簽章 key（可以，但舊 BROM 仍能被用舊 key 繞）
- Step 3 被防禦：SBC_EN + JTAG fuse 鎖閉，限制 DA 的寫入範圍
- Step 4 被防禦：LK 做 AVB 驗證，vbmeta flags 在 LOCKED bootloader 下被拒絕

**本段攻擊鏈為理論預期行為，未實測。驗證方法：mtkclient GitHub 的 issue tracker 有成功/失敗的具體機型回報。**

---

## 防禦措施：廠商如何堵

| 防禦層 | 機制 | 限制 |
|--------|------|------|
| SBC_EN fuse | 強制 BROM 驗 DA 簽章 | BROM 本身有 bug 就繞過 |
| BROM exploit 修補 | 硬體改版（新 stepping/tapeout） | 舊 SoC 永久無法修補 |
| DA 簽章更新 | 新 DA key，舊 exploit 的 fake DA 無效 | 若 exploit 在 DA 載入前就劫持 → 無效 |
| JTAG/UART fuse | 關閉物理除錯介面 | 若 BROM exploit 仍可，等同沒關 |
| RPMB 保護 rollback | TEE 管的 rollback counter | DA level exploit 可繞 TEE |
| eMMC 加密（FBE/FDE） | 使用者資料加密 | DA 能讀加密後的 block，但無 key |

---

## BROM 逆向入門

如果你想分析特定 SoC 的 BROM（研究目的），標準流程是：

```
1. 取得 BROM image
   方法 A：若 SBC_EN=0，用 mtkclient 的 BROM dump 功能直接讀出
   方法 B：從 SP Flash Tool 安裝目錄找（某些版本附有 BROM image）
   方法 C：從 Preloader 二進位反推（Preloader 初始化時會參照 BROM 常數）

2. 用 Ghidra 載入
   架構：ARM（Thumb/Thumb-2），小端，無作業系統
   Load address：通常 0x00000000 或 0x00100000（看 SoC datasheet）

3. 找 USB 協定處理函式
   搜字串："USB"、"DA"、"READY"
   找 switch/case 處理命令碼的大型函式

4. 定位簽章驗證
   搜 SHA-256 常數（0x6a09e667 等）或 RSA exponent
   追蹤驗證回傳值的使用 → 找 "if (verify_result != 0) { error }" 的邏輯
```

---

## 踩雷

1. **「mtkclient 支援我的機型」不等於「exploit 成功」**：mtkclient 的 SoC 支援列表很長，但每個 exploit 路徑都依賴特定 BROM 版本。同樣是 MT6765，不同批次的 tapeout 可能已修補漏洞。執行前先確認 `python mtk.py stage1` 能正確識別 SoC（**未實測**）。

2. **Preloader Download Mode ≠ BROM Download Mode**：進了 Preloader 的 Download Mode，攻擊面只剩 Preloader 的 USB 命令集，不是 BROM 的完整 DA 下載協定。大多數 `adb reboot download` 觸發的是 Preloader level。真正的 BROM mode 需要物理操作（按鍵或移除 eMMC）。

3. **DA 寫分區後 bootloader 可能鎖回**：某些 MTK 裝置在重啟後，LK 檢測到 vbmeta 被竄改會自動恢復 bootloader lock state（從 secure storage），不是刷完就永久解鎖。

4. **SBC_EN=0 的設備 UART log 很豐富，不要忽略**：工程機或開發板的 UART console 在 Preloader 和 LK 階段輸出大量除錯資訊，包括記憶體布局、分區表、安全狀態——這些是研究的起點，比盲目逆向效率高很多。接 115200 8N1 就能讀。

5. **mtkclient 的 auth 繞過和 DA patch 是分開的**：`--auth` 選項和 `--loader` 選項控制不同階段。auth 繞過處理 BROM 的握手，loader 指定自訂 DA。搞錯了，兩個都失效。

6. **eMMC dump 後要保留原始映像**：DA level 讀出的 eMMC 是原始 block dump，包含隱藏分區（boot0/boot1/RPMB）。做任何修改前先 `dd` 備份，RPMB 的 anti-rollback counter 損毀後裝置可能拒絕開機。

---

## 進階延伸

- **Preloader 漏洞研究**：BROM 之後下一個目標是 Preloader，因為 BROM 逐漸加強但 Preloader 仍然是廠商自訂程式碼。尋找 Preloader 的 USB META 模式命令集中的解析漏洞。

- **TrustZone 在 MTK 開機鏈中的位置**：MTK 使用 ARM TrustZone，TEE OS（通常是 OPTEE 或 Trusty，或廠商自訂）在 LK 階段載入。TZ 的初始化時序決定了 rollback counter 何時被信任——這個時序是繞過 rollback protection 的研究點。

- **跨廠商對照**：把 MTK 的 BROM→Preloader→LK 和 Qualcomm 的 XBL(UEFI based)→ABL 對照。Qualcomm 從 SDM845 開始改用基於 UEFI 的開機鏈，和 MTK 的傳統 aboot 路線有根本差異，漏洞類型也因此不同。

---

## 動手練習

### 練習 1：分析公開 MTK Preloader 二進位

```bash
# 下載公開的 MTK 機型 Preloader（AOSP 設備樹或 XDA 論壇的 stock ROM）
# 用 strings 找關鍵字串
wsl -e bash -lc "strings preloader_X.bin | grep -i 'secure\|sbc\|da\|verify\|sign'"

# 用 binwalk 找嵌入的結構
wsl -e bash -lc "binwalk preloader_X.bin"
```

### 練習 2：理解 SP Flash Tool 的 scatter 檔

scatter 檔是 MTK 的分區表描述格式（類似 Qualcomm 的 partition.xml）：

```bash
# 從公開 ROM 包解壓，找 MT6xxx_Android_scatter.txt
# 解讀格式：每個分區的 partition_name、linear_start_addr、file_name
# 對照 /proc/partitions 或 adb shell ls /dev/block/by-name/
```

### 練習 3：用 avbtool 驗證 MTK 裝置的 vbmeta

```bash
pip install avbtool
# 從公開 ROM 包拿 vbmeta.img
python avbtool.py info_image --image vbmeta.img
# 確認 algorithm、rollback_index、各分區的 descriptor 類型
```

---

## 本章重點

- MTK 開機鏈：BROM（ROM，不可更新）→ Preloader（DRAM init）→ LK（fastboot/AVB）→ Kernel
- SBC_EN efuse 決定 BROM 是否驗 DA 簽章——未燒的裝置等同開放直接讀寫 eMMC
- Kamakiri 等 BROM 漏洞的核心：在 DA 簽章驗證完成前，找到可觸發的協定命令繞過驗證邏輯
- mtkclient 整合多款 SoC 的 exploit 和 DA 協定，能在支援的 SoC 上繞過 AVB 限制
- Qualcomm EDL/Firehose 是平行設計，攻擊哲學相同但協定和漏洞類型不同
- BROM 漏洞無法修補（ROM），廠商只能靠新 tapeout 硬體修正

---

## 自我檢核

- [ ] 能說明 BROM、Preloader、LK 各自的責任分工和儲存位置
- [ ] 知道 SBC_EN fuse 燒 0 和 1 的差異，以及為什麼開發板通常 fuse 未燒
- [ ] 能解釋 Download Agent（DA）在開機鏈中的角色，以及 SP Flash Tool 如何使用它
- [ ] 理解 Kamakiri 類型漏洞的概念：在驗簽前就觸發記憶體操作
- [ ] 知道 BROM Download Mode 和 Preloader Download Mode 的區別
- [ ] 能對照 MTK 和 Qualcomm EDL 在架構上的相似性

---

## 延伸閱讀

1. **"Bypassing Secure Boot using BROM exploits on MediaTek devices" — 多篇 XDA/Medium 研究文章（2019-2021）**
   讀哪裡：搜索「MTK BROM exploit Kamakiri」，找 bkerler（mtkclient 作者）的 GitHub issues 和 wiki
   學什麼：具體受影響 SoC 列表、exploit 的協定細節、如何分辨自己的裝置是否受影響
   關聯：直接對應本章 Kamakiri 和 mtkclient 部分

2. **mtkclient 原始碼**（`https://github.com/bkerler/mtkclient`）
   讀哪裡：`mtk/Library/Connection/` 的 BROM 協定實作；`mtk/Library/explorts/` 的各 SoC exploit 邏輯
   學什麼：BROM USB 協定的實際命令格式，exploit 的精確觸發時序
   關聯：接 Ch 25 逆 ARM bootloader 時的實作參考

3. **"All Qualcomm Snapdragon SoCs from 2006-2016 vulnerable to EDL exploits" — Aleph Research（Roee Hay）**
   讀哪裡：該論文的 Sahara 協定分析章節；對照 MTK 的 DA 協定
   學什麼：跨廠商的 Download Mode 攻擊面如何呈現相同的設計模式缺陷
   關聯：建立「vendor SoC Download Mode 攻擊面」的通用心智模型，接 Ch 21 嵌入式繞過類型學

→ [下一章](./21-embedded-bypass-patterns.md)
