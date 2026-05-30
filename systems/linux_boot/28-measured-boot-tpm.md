# Ch 28 — Measured Boot 與 TPM

> **目標**：理解 Measured Boot 和 TPM——PCR（Platform Configuration Register）如何記錄開機每一步的「測量值」、TPM 的角色、remote attestation、與 Secure Boot 的根本差異，以及它如何用於磁碟加密的自動解鎖（TPM-sealed keys）。

> **環境**：TPM 2.0，`tpm2-tools`。承接 Ch 27（Secure Boot）。本章是進階概念，動手部分需要 TPM。

## 為什麼 Secure Boot 還不夠？

Secure Boot（Ch 27）確保「只執行簽署過的東西」。但它有個盲點：它**驗證簽署，但不記錄實際開機了什麼**。

```
Secure Boot 的盲點：
  Secure Boot 說「這個 bootloader 簽署有效，可以執行」
        │
  但它不記錄「實際上開機鏈是哪些具體的東西、什麼版本、什麼設定」
        │
  問題：兩個都「簽署有效」但「不同」的開機怎麼區分？
    - 正常 kernel vs 有後門但被偷簽的 kernel（如果簽署金鑰洩漏）
    - 開機參數被改（如加了 init=/bin/bash 救援，繞過安全）
        │
  → Secure Boot 驗證「能不能執行」，不證明「實際執行了什麼」
```

**Measured Boot** 補上這個：它不拒絕任何東西（不像 Secure Boot），而是**測量並記錄**開機每一步的精確內容（hash）。之後能驗證「開機鏈確實是預期的樣子」。這對「證明系統處於可信狀態」（remote attestation）和「只在可信狀態解鎖加密磁碟」很關鍵。

## 先建立直覺：Measured Boot 是「開機的不可竄改紀錄」

```
Secure Boot vs Measured Boot：

  Secure Boot（守門員）：
    「你的簽署有效嗎？有 → 放行，無 → 拒絕」
    主動阻擋，但不記錄細節

  Measured Boot（記錄員）：
    「不管你是誰，我把你的 hash 記進 TPM」
    不阻擋，但留下不可竄改的紀錄
        │
  Measured Boot 的紀錄（PCR）能用來：
    - 證明開機鏈是預期的（attestation）
    - 只在「開機鏈正確」時解鎖加密金鑰（sealing）
```

Measured Boot 不當守門員，當記錄員——它把開機每一步的測量值（hash）累積記進 TPM 的 PCR。這些紀錄不可竄改，之後能驗證或用於解鎖金鑰。

## TPM：可信平台模組

**TPM**（Trusted Platform Module）是個獨立的安全晶片（或韌體實作），提供：

```
TPM 提供的功能：
  - PCR（Platform Configuration Register）：
    記錄測量值的暫存器（只能「擴展」不能任意寫）
  - 金鑰儲存：安全保存金鑰（私鑰不離開 TPM）
  - 隨機數產生
  - sealing/unsealing：把資料綁定到 PCR 狀態
        │
  TPM 是獨立晶片 → 即使主 CPU 被入侵，TPM 的金鑰仍安全
```

```bash
# 看你的 TPM
ls /dev/tpm*               # /dev/tpm0, /dev/tpmrm0
sudo tpm2_getcap properties-fixed   # TPM 資訊（需 tpm2-tools）

# 看 PCR 值
sudo tpm2_pcrread          # 列出所有 PCR 的當前值
```

## PCR：測量值的累積暫存器

**PCR** 是 TPM 裡的暫存器，記錄開機測量。關鍵特性：PCR 不能任意寫，只能「擴展」（extend）：

```
PCR 的擴展機制（extend）：
  PCR 不是「設值」，是「擴展」：
    new_PCR = hash(old_PCR || new_measurement)
        │
  每測量一個開機元件，就 extend 進 PCR
    PCR = hash(hash(hash(0 || measure1) || measure2) || measure3)...
        │
  特性：
    - 順序敏感（不同順序 → 不同 PCR 值）
    - 不可逆（無法從 PCR 值反推測量）
    - 不可偽造（要得到特定 PCR 值，必須真的按那個順序測量那些東西）
```

```
PCR 的分配（慣例）：
  PCR 0：韌體（UEFI firmware）
  PCR 1：韌體設定
  PCR 2-3：option ROM
  PCR 4：bootloader（shim/GRUB）
  PCR 5：bootloader 設定
  PCR 7：Secure Boot 狀態和金鑰
  PCR 8-9：kernel、command line、initramfs
  ...
```

> PCR 的「extend」機制是巧妙的：你不能直接設 PCR 成某個值，只能把測量「累積」進去。要讓 PCR 等於某個特定值，你必須真的按正確順序測量正確的東西。這讓 PCR 成為「開機鏈的不可偽造指紋」——PCR 值唯一對應一個特定的開機序列。改了開機鏈任何一步，PCR 就不同。

## 開機測量流程

```
Measured Boot 的測量鏈：

  韌體啟動
    → 測量自己，extend PCR 0
    → 測量 bootloader（shim），extend PCR 4
        │
  shim 啟動
    → 測量 GRUB，extend PCR
        │
  GRUB 啟動
    → 測量 kernel、cmdline、initramfs，extend PCR 8-9
        │
  kernel 啟動
    → 可以繼續測量（IMA：Integrity Measurement Architecture）
        │
  → 開機完成後，PCR 值是整個開機鏈的「指紋」
    如果開機鏈和預期一致，PCR = 預期值
```

每一棒測量下一棒並 extend 進 PCR——和 Secure Boot 的「驗證下一棒」平行，但 Measured Boot 是「記錄」而非「驗證」。開機完成後，PCR 值反映了「實際開機了什麼」。

## Remote Attestation：證明系統狀態

PCR 值能用於 **remote attestation**——向遠端證明「我的系統處於可信狀態」：

```
Remote Attestation 流程：
  遠端伺服器想確認「這台機器開機鏈沒被竄改」
        │
  伺服器發 challenge（隨機數）
        │
  機器的 TPM 用它的金鑰「簽署」當前 PCR 值 + challenge
    （這叫 TPM Quote）
        │
  伺服器驗證簽署，比對 PCR 值和「已知良好的值」
    PCR = 預期 → 開機鏈可信
    PCR ≠ 預期 → 開機鏈被改過（拒絕存取）
        │
  用途：企業確認員工機器可信、雲端確認 VM 完整性
```

remote attestation 讓「不在現場」的伺服器能驗證一台機器的開機完整性。這是 confidential computing、zero-trust 架構的基礎。

## TPM-sealed keys：磁碟自動解鎖

Measured Boot 最實用的應用：**把磁碟加密金鑰「封印」（seal）到 PCR 狀態**，實現「只在可信開機時自動解鎖」：

```
TPM sealing 用於磁碟加密自動解鎖：
  把 LUKS 加密金鑰 seal 到 TPM，綁定特定 PCR 值
        │
  開機時：
    - 如果 PCR = 預期（開機鏈正確）→ TPM 自動 unseal 金鑰 → 自動解鎖磁碟
    - 如果 PCR ≠ 預期（開機鏈被改）→ unseal 失敗 → 不解鎖
        │
  好處：磁碟自動解鎖（不用每次輸密碼），但只在「可信開機」時
        │
  攻擊者改了 bootloader（想插後門）→ PCR 變 → 金鑰 unseal 失敗
        → 拿不到加密金鑰，磁碟還是鎖的
```

```bash
# systemd 的 TPM 整合（systemd-cryptenroll）
# 把 LUKS 金鑰綁到 TPM PCR
sudo systemd-cryptenroll --tpm2-device=auto \
    --tpm2-pcrs=7+8 /dev/sda2
# → 開機時如果 PCR 7（Secure Boot 狀態）和 8（kernel/cmdline）正確
#   TPM 自動解鎖，不用輸密碼
```

> 這是 Measured Boot 最貼近日常的應用：BitLocker（Windows）和 systemd 的 TPM 解鎖（Linux）都用它。磁碟加密金鑰封印到 TPM，綁定開機鏈的 PCR。正常開機（PCR 對）→ 自動解鎖；被竄改（PCR 錯）→ 拿不到金鑰。這比「每次輸密碼」方便，又比「金鑰存明文」安全——金鑰只在可信開機狀態才釋放。理解這個，你會懂為什麼 Windows BitLocker 預設不用密碼（靠 TPM），以及改了開機設定後 BitLocker 為什麼要恢復金鑰（PCR 變了）。

## Secure Boot vs Measured Boot 對照

| 面向 | Secure Boot | Measured Boot |
|---|---|---|
| 機制 | 驗證簽署，拒絕未簽署 | 測量並記錄（PCR），不拒絕 |
| 角色 | 守門員（主動阻擋）| 記錄員（被動記錄）|
| 需要 | UEFI db 金鑰 | TPM |
| 防什麼 | 執行未簽署的東西 | 提供開機狀態的證明 |
| 應用 | 防 bootkit | attestation、磁碟自動解鎖 |
| 關係 | 互補（常一起用）| 互補 |

> Secure Boot 和 Measured Boot **互補**，常一起用。Secure Boot 主動擋（不執行未簽署的），Measured Boot 被動記錄（證明實際執行了什麼）。例如：Secure Boot 確保 bootloader 簽署有效，Measured Boot 記錄具體是哪個 bootloader（版本、設定）的 hash。兩者結合提供「既阻擋又可驗證」的開機安全。

## 踩雷集錦

1. **混淆 Secure Boot 和 Measured Boot**：Secure Boot 驗證並拒絕；Measured Boot 測量並記錄。不同機制、不同用途。常一起用但別搞混

2. **以為 Measured Boot 會阻擋開機**：它不阻擋（不像 Secure Boot）。它只記錄。「阻擋」是之後用 PCR 做決策時才發生（如 unseal 失敗）

3. **改開機設定後 TPM 解鎖失敗**：改了 kernel、bootloader、Secure Boot 設定，PCR 變，TPM-sealed 金鑰 unseal 失敗（BitLocker 要恢復金鑰）。這是設計（防竄改），不是 bug

4. **PCR 選擇不當**：seal 金鑰時選的 PCR 決定「什麼變動會導致解鎖失敗」。選太多 PCR（如連 kernel 版本都綁）→ 每次 kernel 更新都要重新封印。選太少 → 安全性弱。要權衡

5. **以為 TPM 防一切**：TPM 防「開機鏈竄改」和「金鑰竊取」，但不防 OS 層攻擊（已解鎖後的系統）。它是一層防護

## 進階：IMA 與 runtime 完整性

Measured Boot 測量到 kernel 啟動，但 **IMA**（Integrity Measurement Architecture）把測量延伸到 runtime：

```
IMA（Integrity Measurement Architecture）：
  Measured Boot 測量到 kernel 啟動
        │
  IMA 繼續測量 runtime 載入的東西：
    - 執行的程式
    - 載入的 library
    - 讀取的設定檔
        │
  每個測量 extend 進 PCR（PCR 10 等）
        │
  → 不只開機鏈，連 runtime 執行了什麼都有紀錄
  → 能偵測「開機後被竄改的檔案」
```

IMA 把「測量」的概念從開機延伸到整個系統運行——任何執行的程式、載入的檔案都被測量記錄。配合 appraisal 模式，IMA 還能拒絕執行「測量值不符預期」的檔案（這時它像 runtime 的 Secure Boot）。這是 high-security 環境（如政府、金融）的進階防護。理解 IMA，你會看到「measured/trusted computing」如何從開機延伸到整個系統生命週期。

## 動手練習

1. 看你的 TPM 和 PCR：`ls /dev/tpm*`、`sudo tpm2_pcrread`（需 tpm2-tools），看 PCR 值。理解每個 PCR 對應開機的哪一步

2. 看 measured boot log：`sudo cat /sys/kernel/security/tpm0/binary_bios_measurements`（如果有，TPM event log），或用 `tpm2_eventlog` 解析，看開機測量了哪些東西

3. 概念追蹤：如果你的系統用 TPM 磁碟解鎖（如 Windows BitLocker 或 systemd-cryptenroll），理解改 BIOS 設定為什麼會觸發恢復金鑰（PCR 變了）

4. 對比 Secure Boot 和 Measured Boot：列出兩者的機制、需要的硬體、防的威脅、應用，確認你能清楚區分

## 本章重點整理

- Measured Boot 補 Secure Boot 的盲點：不拒絕（不像 Secure Boot），而是測量並記錄開機每一步（PCR）
- TPM 是獨立安全晶片：PCR（記錄測量）、金鑰儲存、sealing；PCR 只能 extend（累積 hash），不可偽造
- 開機測量鏈：每一棒測量下一棒並 extend 進 PCR，PCR 值是開機鏈的不可偽造指紋
- 應用：remote attestation（向遠端證明可信狀態）、TPM-sealed keys（磁碟自動解鎖，PCR 對才釋放金鑰）
- Secure Boot（守門員，驗證拒絕）和 Measured Boot（記錄員，測量記錄）互補，常一起用

## 自我檢核

- [ ] 能解釋 Measured Boot 補 Secure Boot 的什麼盲點
- [ ] 知道 PCR 的 extend 機制，以及為什麼它不可偽造
- [ ] 能說出 remote attestation 的流程（TPM quote、比對 PCR）
- [ ] 能解釋 TPM-sealed keys 如何實現「只在可信開機時自動解鎖磁碟」
- [ ] 能清楚區分 Secure Boot 和 Measured Boot（機制、角色、應用）

## 延伸閱讀

### 官方文件

- **[TCG TPM 2.0 Specification](https://trustedcomputinggroup.org/resource/tpm-library-specification/)**
  - **讀哪裡**：Architecture 概覽、PCR 那節（不用全讀，很長）
  - **學什麼**：TPM 和 PCR 的權威定義
  - **前提**：本章

- **[Linux kernel: IMA documentation](https://www.kernel.org/doc/html/latest/security/IMA-templates.html)**
  - **讀哪裡**：IMA overview
  - **學什麼**：runtime 完整性測量
  - **前提**：本章的 IMA 部分

### 部落格 / 文章

- **[A Tour Beyond BIOS: Measured Boot](https://www.kernel.org/doc/html/latest/security/tpm/index.html)** 或 systemd 的 TPM 文件
  - **這篇說什麼**：Measured Boot 和 TPM 的實務，systemd 的 TPM 整合
  - **讀哪裡**：systemd-cryptenroll 和 measured boot 那部分
  - **為什麼值得讀**：把 TPM 概念連到實際的磁碟加密應用

→ [Ch 29 開機問題診斷](./29-boot-debugging.md)
