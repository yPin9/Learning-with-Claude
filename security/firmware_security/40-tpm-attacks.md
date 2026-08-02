# Ch 40 — TPM 攻擊

> **目標**：系統性地拆解 TPM 的攻擊面——從物理層的匯流排竊聽到軟體層的 spec 漏洞，理解「有 TPM ≠ 安全」的根本原因。掌握 dTPM LPC/SPI 明文匯流排竊聽、PCR replay、fTPM 韌體漏洞（faulTPM/AMD PSP）、TPM 2.0 規格層 buffer overflow（CVE-2023-1017/1018），以及為什麼 TPM 只保護金鑰、不保護量測邏輯。

---

## 威脅模型：TPM 能保護什麼、不能保護什麼

先把威脅模型說清楚，才能理解後面每種攻擊針對的是哪個假設破洞：

```
TPM 保護：
  ✓ 金鑰在 TPM 內部生成，私鑰從不以明文離開 TPM 晶片
  ✓ Sealed object 只有 policy 符合才能 unseal
  ✓ PCR 值只能 extend（hash 累積），不能被軟體直接寫入
  ✓ 抗 OS-level 攻擊：ring-0 無法讀取 TPM 內部的 key material

TPM 不保護：
  ✗ 主機板匯流排（dTPM 的 LPC/SPI 通訊是明文）
  ✗ 量測邏輯的正確性（誰來量測、何時量測，TPM 不管）
  ✗ PCR 值被重放（同樣的 extend 序列 → 同樣的 PCR 值）
  ✗ fTPM 的韌體本身（跑在 PSP/TXE，有自己的漏洞面）
  ✗ 實體存取後的側信道或故障注入
```

---

## 攻擊一：dTPM 匯流排竊聽（LPC / SPI）

### 原理

**dTPM**（discrete TPM）是獨立晶片，透過匯流排與主機板 PCH（Platform Controller Hub）通訊。老機器用 LPC（Low Pin Count）匯流排，新機器用 SPI 匯流排。**這兩種匯流排都是明文傳輸**——沒有加密，沒有認證。

```
CPU ←→ PCH ←─────────── LPC/SPI（明文）───────────→ dTPM chip
         │                                             │
         │  unseal request                             │
         │ ─────────────────────────────────────────→ │
         │                                             │ 解密 sealed key
         │  unsealed plaintext secret                  │
         │ ←───────────────────────────────────────── │
         │                                             │
         ↓
    攻擊者用邏輯分析儀接在 LPC/SPI 總線上，
    直接讀到 unsealed plaintext ！
```

這是 dTPM 架構的根本缺陷：**TPM 保護了 key 不離開晶片，但 unsealed 後的 secret 傳回 CPU 這條路是暴露的**。

### BitLocker VMK 竊聽實例（未實測，需真硬體）

2021 年研究者 Denis Andzakovic 展示了用 Raspberry Pi Pico 抓 BitLocker VMK 的流程，2023 年 YouTube 頻道 stacksmashing 把這個攻擊普及化，拍成影片吸引大量關注：

```
工具：
  - 邏輯分析儀（Saleae Logic Pro 8 或 $20 的便宜替代品）
  - 主機板的 LPC/SPI debug 測試點（通常在 TPM 晶片周邊）
  - Python 腳本解析 LPC 協定，找 TPM read/write transaction

步驟（概念流程，未實測）：
  1. 拆開目標 Windows 機器，找 TPM 晶片（24 腳 SOIC/QFN 封裝）
  2. 找 LPC 或 SPI 接腳（LPC：CLK, AD[0-3], FRAME#, LDRQ#）
  3. 連接邏輯分析儀到匯流排
  4. 開機讓 BitLocker 啟動，此時 Windows bootloader 向 TPM unseal 請求
  5. 在 LPC 流量裡找 TPM_PCR_Read 和 TPM_Unseal 命令的回應
  6. 回應封包內含 VMK（Volume Master Key）明文

攻擊成立的前提：
  - BitLocker 設定為 TPM-only 模式（無 PIN）
  - 攻擊者有實體存取（拆主機板）
```

### TPM Genie

TPM Genie（Henry Crall, 2018, DEF CON 26）是一個硬體中間人裝置，插在 TPM 的 I2C/LPC 匯流排上，透明地代理所有 TPM 通訊，同時把 unsealed secret 複製給攻擊者：

```
正常路徑：  [Host PCH] ←──LPC──→ [TPM]

TPM Genie：  [Host PCH] ←──LPC──→ [TPM Genie] ←──LPC──→ [TPM]
                                         │
                                         └──→ 側錄 unsealed secret
```

TPM Genie 可以做到 passive 監聽（只讀），也可以做到 active 竄改（修改 TPM 回傳值）。

### 緩解方向

- **fTPM**（firmware TPM）：TPM 跑在 CPU die 內部的 TXE（Intel）或 PSP（AMD）enclave，沒有外部匯流排，消除了 LPC/SPI 竊聽向量
- **BitLocker TPM + PIN 模式**：即使攻擊者竊聽到 VMK，沒有 PIN 就無法通過 policy，unseal 不會發生（因為 PolicyPCR + PolicyPassword 組合下，需要使用者輸入 PIN 才觸發 unseal）
- **Microsoft Pluton**（ARM TrustZone 整合進 CPU）：把 TPM 直接整進 CPU，徹底消除外部匯流排

---

## 攻擊二：TPM Reset 與 PCR Replay

### TPM Reset

TPM 重置後所有 PCR 歸零。如果攻擊者能讓 TPM 重置，然後 replay 正確的 extend 序列，就能重建目標 PCR 值。

```
攻擊前提：
  1. 知道原始 extend 序列（從 TCG event log 讀取，通常存在 UEFI NVRAM 或 /sys/kernel/security/tpm0/binary_bios_measurements）
  2. 能讓 TPM 在開機流程外重置（電源干預、實體攻擊）

Replay 流程（swtpm 可示範，見動手練習）：
  1. 讀取目標系統的 TCG event log
  2. 解析每個 event 的 digest
  3. 對重置後的 TPM 重放同樣的 extend 序列
  4. PCR 值恢復為目標值
  5. Unseal 成功
```

### 為什麼 replay 理論上可行？

PCR extend 的計算是確定性的：

```
PCR_new = SHA256(PCR_old || digest)

初始值 PCR = 0x000...000（20/32 bytes 全零）

如果知道每次 extend 的 digest（從 event log 讀取），
就能重建整個 extend 歷史，最終 PCR 值相同。
```

關鍵是 **event log 不是秘密**：TCG 規格要求把 event log 存在可讀取的地方，以便 remote attestation 驗證。攻擊者可以讀到 event log，然後在一個被他控制的 swtpm 上重放，建立一個「看起來合法」的 TPM 狀態。

### 現實中的困難

純軟體 PCR replay 面對幾個障礙：
- 需要真實的 TPM hardware（或者 swtpm），但 swtpm 的 TPM2 handle 不等同於系統的 TPM
- sealed object 是用系統 TPM 的 SRK 加密的，換一個 TPM（即使 PCR 相同）也無法解密
- **結論：PCR replay 本身不能直接攻破 sealed key**，除非同時配合匯流排竊聽或其他取得 sealed private area 的手法

PCR replay 的真實威脅場景是：攻擊者先用匯流排竊聽取得 sealed private area，再用 PCR replay 嘗試在另一個 TPM 上 unseal——而這取決於 sealed private area 是否綁定到特定 TPM（`fixedtpm` attribute）。

### 動手：swtpm 上 PCR replay 概念示範（真跑）

```bash
# 環境準備（接練習 F 的 swtpm）
export TPM2TOOLS_TCTI="swtpm:path=/tmp/swtpm.sock"

# Step 1：建立初始 PCR 狀態（模擬開機量測）
tpm2_pcrextend 7:sha256=$(echo -n "firmware_measurement_1" | sha256sum | cut -d' ' -f1)
tpm2_pcrextend 7:sha256=$(echo -n "firmware_measurement_2" | sha256sum | cut -d' ' -f1)
tpm2_pcrread sha256:7 | tee pcr7_target.txt

# Step 2：記錄 extend 序列（模擬 event log）
DIGEST1=$(echo -n "firmware_measurement_1" | sha256sum | cut -d' ' -f1)
DIGEST2=$(echo -n "firmware_measurement_2" | sha256sum | cut -d' ' -f1)
echo "Extend sequence: $DIGEST1, $DIGEST2" | tee event_log.txt

# Step 3：建立 sealed object 在這個 PCR[7] 值下
tpm2_startauthsession -S session.ctx --policy-session
tpm2_policypcr -S session.ctx -l sha256:7
tpm2_policygetdigest -S session.ctx -o seal.policy
tpm2_flushcontext session.ctx

tpm2_createprimary -C o -g sha256 -G rsa -c primary.ctx
echo -n "secret_vmk_data" > secret.bin
tpm2_create -C primary.ctx -i secret.bin \
    -u sealed.pub -r sealed.priv -L seal.policy \
    -a "fixedtpm|fixedparent|adminwithpolicy"
tpm2_load -C primary.ctx -u sealed.pub -r sealed.priv -c sealed.ctx

# Step 4：模擬 TPM reset（清除 PCR）
# 注意：swtpm 上用 tpm2_pcrreset 清 PCR（實際 TPM 需電源循環）
tpm2_pcrreset 7  # 若 tpm2-tools 支援

# 或者重啟 swtpm 實現 PCR 歸零
# kill swtpm，重啟，PCR 回零
# （這裡示意，實際操作見練習 F）

# Step 5：重放 extend 序列（replay）
tpm2_pcrextend 7:sha256=$DIGEST1
tpm2_pcrextend 7:sha256=$DIGEST2

# Step 6：驗證 PCR[7] 值恢復
tpm2_pcrread sha256:7 | tee pcr7_replay.txt
diff pcr7_target.txt pcr7_replay.txt && echo "PCR replay 成功" || echo "PCR 值不同"

# Step 7：嘗試 unseal（使用 replay 後的 PCR 值）
tpm2_startauthsession -S session.ctx --policy-session
tpm2_policypcr -S session.ctx -l sha256:7
tpm2_unseal -c sealed.ctx -p "session:session.ctx" -o replayed_secret.bin 2>&1
tpm2_flushcontext session.ctx

# 注意：這在同一個 swtpm 上可以成功（因為 SRK 相同）
# 真實場景：攻擊者要拿到另一台機器的 sealed.priv，在自己的 TPM 上不行
cat replayed_secret.bin 2>/dev/null && echo "（同 TPM 上 replay 成功）"
```

---

## 攻擊三：fTPM 韌體漏洞

fTPM（firmware TPM）解決了 dTPM 匯流排竊聽問題，但把攻擊面轉移到了 CPU 廠商的安全 enclave 韌體。

### faulTPM：AMD PSP fTPM 攻擊

2023 年，Hans Niklas Jacob 等人（ETH Zurich）發表 faulTPM，對 AMD 平台的 fTPM（運行在 AMD PSP 上）實施電壓故障注入攻擊：

```
攻擊目標：AMD PSP（Platform Security Processor）
  - PSP 是 ARM Cortex-A5，跑 AMD 的安全韌體
  - fTPM 就是 PSP 上的一個 TEE 應用
  - PSP 的 NVSeed（用於生成 TPM 的 endorsement key）存在 NOR flash

攻擊流程（未實測，需硬體）：
  1. AMD Zen 2/3 平台的 VCC_CORE 供電存在注入點
  2. 在特定時序下對電壓施加脈衝，讓 PSP 在處理某個 command 時發生 glitch
  3. 利用 glitch 讓 PSP 洩露 fTPM 的 seed 或跳過認證
  4. 取得 seed 後可以在任意平台重建相同的 TPM identity 和 sealed key

影響：
  - 攻擊者能讀取 BitLocker VMK（如果用 AMD + fTPM + BitLocker TPM-only 模式）
  - CVE-2023-20588（相關 AMD 漏洞）提供了額外攻擊上下文
  - AMD 在後來的 AGESA 更新中加強了 PSP 的故障注入防護
```

faulTPM 的關鍵意義：**fTPM 消除了匯流排竊聽，但換來了對 CPU supply chain 的信任要求**——PSP 韌體有 bug，fTPM 的所謂「隔離」就無意義了。

### Intel TXE / PTT fTPM

Intel 的 fTPM 叫 PTT（Platform Trust Technology），運行在 ME（Management Engine）的 TXE（Trusted Execution Engine）分區：

```
Intel ME ──→ TXE Partition ──→ PTT（fTPM 實作）
                 ↑
                 │ 有自己的 SRAM 和 NOR flash 分區
                 │ ring -3（SMM 都無法存取）

已知問題：
  - ME 韌體本身有 CVE（見 Ch 14）
  - 2017 年 Intel SA-00086（ME multiple vulnerabilities）
    影響了 ME 的隔離性，間接影響 PTT 的信任模型
```

---

## 攻擊四：TPM 2.0 規格層 Buffer Overflow（CVE-2023-1017/1018）

2023 年 Quarkslab 研究者 Francisco Falcon 等人發現 TPM 2.0 規格本身的緩衝區溢位漏洞：

### 漏洞原理

TPM 2.0 規格 1.59 和更早版本在處理 `TPM2_ExecuteCommand` 的 SessionContext 時，沒有正確驗證 CpHashA 和 Policy 的長度：

```c
// 簡化的有問題的偽代碼（根據 Quarkslab 報告）
// TPM 規格第 3.3 部分，Command/Response Buffer 處理

// 攻擊者傳入一個特製的 TPM command
// 其中 SessionContext.cpHashAlg 指示一個 hash size > buffer 大小
// 導致在解析 session 參數時寫越界

UINT16 cpHashSize = GetHashSize(cpHashAlg);  // 可能被操控到大值
memcpy(cpHash, input, cpHashSize);           // 越界寫入
```

### 影響範圍

- **CVE-2023-1017**：OOB（越界）讀取，TPM memory 中 2 bytes 可洩漏
- **CVE-2023-1018**：OOB 寫入，2 bytes 可寫入 TPM 記憶體
- **影響**：任何實作 TPM 2.0 規格 1.59 的 TPM 韌體，包括 libtpms（swtpm 的基礎）

```bash
# 驗證 swtpm / libtpms 版本是否受影響（真跑）
swtpm --version
# 受影響版本：libtpms 0.9.x before 0.9.6，0.8.x before 0.8.10

# 修補後版本確認
dpkg -l | grep libtpms
# 確認 >= 0.9.6 或 >= 0.8.10
```

這個漏洞的嚴重性在於：**攻擊者不需要實體存取，只需要能發送 TPM command 就可以觸發**（但 ring-0 才能直接發 TPM command，ring-3 不行）。

### 實際利用條件

在真實環境中，CVE-2023-1017/1018 的利用需要：
1. 攻擊者已取得 OS ring-0 權限（可以發 TPM command）
2. 目標使用受影響的 TPM 韌體
3. 用 2 bytes OOB 寫入造成有用的 memory corruption

這讓它在「已有 ring-0」的情境下變成額外的 TPM 提權向量，但不是獨立的遠端攻擊面。

---

## 攻擊五：PCR[7]-Only 綁定的設計弱點

這不是一個 CVE，是架構設計層面的弱點，值得單獨說明。

### 問題所在

BitLocker TPM-only 模式預設主要依賴 PCR[7]：

```
PCR[7] 量測什麼：
  - Secure Boot 是否啟用（一個 bit）
  - PK 的 SHA256 指紋（摘要）
  - KEK 的 SHA256 指紋（摘要）
  - db 的 SHA256 指紋（所有條目的總 digest）
  - dbx 的 SHA256 指紋

PCR[7] 不告訴你：
  - db 裡面有哪些憑證、哪些是合法的、哪些是攻擊者加的
  - Secure Boot 有沒有被 bypass（只告訴你「有在驗」）
```

### 攻擊場景

攻擊者取得了 Windows 機器的 UEFI 設定存取權（比如透過 BIOS 設定介面，或透過 UEFI shell）：

```
攻擊步驟（不需要 TPM 漏洞）：
  1. 進 UEFI 設定，在 Secure Boot db 加入攻擊者的自簽 CA 憑證
  2. db 變了，但 Secure Boot 還是啟用的
  3. PCR[7] 包含了新的 db digest，值確實不同了
  4. BitLocker 第一次開機要求 recovery key
  5. 攻擊者有 recovery key（透過社交工程或 AD 竊取）
  6. 輸入 recovery key，BitLocker suspended 讓系統開機
  7. BitLocker 重新 seal 到新的 PCR[7]（含攻擊者的 CA）
  8. 之後攻擊者用自簽憑證的惡意 bootloader 開機，PCR[7] match，BitLocker 自動解鎖
```

這個攻擊的核心是：**BitLocker 的 recovery key 讓系統有「繞過 TPM」的合法路徑**，而這條路徑的安全性完全不依賴 TPM。

---

## 為什麼「有 TPM ≠ 安全」：TPM 只保護 Key，不保護量測邏輯

這是本章最重要的認知框架：

```
TPM 的角色：
  1. 安全儲存金鑰（endorsement key、storage key、sealed key）
  2. 執行密碼學操作（sign、verify、seal、unseal）
  3. 維護 PCR（被動接受 extend，不主動量測）
  4. 提供 attestation quote（簽章 PCR 值給驗證者）

TPM 不做：
  ✗ 決定「要量測什麼」（這是 BIOS/UEFI/OS loader 的責任）
  ✗ 驗證 extend 進來的 digest 是否代表「安全的」元件
  ✗ 阻止攻擊者在「惡意元件執行後」才 extend 正確的 digest
  ✗ 保護量測程式碼本身（BIOS 被改 → 量測邏輯被改 → PCR 值被操控）
```

```
攻擊者控制 BIOS（透過 SPI flash 竄改）：
  可以讓 BIOS 先執行惡意程式碼，
  然後 extend「看起來像正常開機」的 PCR 值，
  TPM 完全不知道發生了什麼——因為 TPM 是被動接收 extend 的。

防禦：BootGuard 和 Secure Boot 保護了「有沒有用正確的 BIOS」
     但如果 BootGuard 沒啟用（Ch 14），或 Secure Boot 被繞過（Ch 28-32），
     量測邏輯就不可信，PCR 值就不可信，sealed key 就沒有意義。
```

這就是為什麼 Ch 37/38 強調：TPM 是 measured boot 的**執行者**，但 measured boot 的**可信度**取決於信任鏈的起點（BootGuard/fuse）。信任鏈斷了，TPM 變成一個任人擺布的雜湊計算機。

---

## 動手練習小結（真跑可行部分）

```bash
# ===== 可在 swtpm 上真跑的部分 =====

# 1. PCR replay 示範（見上方 Step 1-7）
# 2. CVE-2023-1017/1018 版本確認
swtpm --version && dpkg -l libtpms0 2>/dev/null | tail -1

# 3. 觀察 TPM 拒絕「非法 extend 序列」——TPM 無法拒絕
# TPM 永遠接受任何 digest 的 extend，這本身就是威脅模型的一部分
echo "TPM 的 pcrextend 不驗 digest 內容，只是累積計算："
tpm2_pcrread sha256:8   # 看 PCR[8] 初始值
tpm2_pcrextend 8:sha256=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
tpm2_pcrread sha256:8   # PCR[8] 已變，TPM 沒有拒絕任何東西
echo "=> TPM 接受了任何 extend，它不知道 digest 代表什麼"

# ===== 未實測（需真硬體）=====
# - LPC/SPI 匯流排竊聽（需邏輯分析儀 + dTPM 平台）
# - faulTPM AMD PSP 電壓故障注入（需 ChipWhisperer + AMD Zen 2/3 平台）
# - CVE-2023-1017/1018 PoC 實際觸發（需未修補的 TPM 韌體）
```

---

## 踩雷

1. **「fTPM 比 dTPM 更安全」不是完全正確**：fTPM 消除匯流排竊聽，但引入 CPU 安全 enclave 的信任要求。AMD PSP、Intel ME 各自有 CVE，fTPM 的安全性等於 PSP/ME 的安全性。如果你的威脅模型包含「有能力攻擊 CPU 固件」的攻擊者，fTPM 不比 dTPM 好多少。

2. **TCG event log 的可信度**：event log 本身不在 TPM 內部，它存在 BIOS ACPI table 或 memory 裡。如果 BIOS 已被竄改，event log 也可以被偽造。remote attestation 驗證 quote 的方法無法獨立驗證 event log 的真實性（除非用 monotonic counter 等額外機制）。

3. **PCR reset 不等同於 TPM clear**：`tpm2_pcrreset` 只清 extend 過的 PCR，TPM 的 hierarchy 和 sealed object 還在。`tpm2_clear` 才是清掉所有 hierarchy（和所有 sealed object——因為 SRK 換了）。兩個操作的影響完全不同。

4. **CVE-2023-1017/1018 的範圍**：這是規格漏洞，但 TPM 硬體廠商（Infineon、STMicro、Nuvoton）有自己的實作，不一定 1:1 複製規格中的問題程式碼。libtpms（swtpm）是受影響的，真實 dTPM 要看各廠商是否獨立發現並修補。

5. **BitLocker 預啟動 PIN 是有效緩解**：如果 BitLocker 設定為 TPM + PIN 模式，匯流排竊聽也拿不到 VMK——因為 PIN 沒有輸入就不會觸發 unseal。這是個廉價的高效緩解，企業應該強制啟用。

---

## 進階延伸

- **TPM Genie 深讀**：Henry Crall 的 DEF CON 26 白皮書（PDF 可在 GitHub 找到）詳細說明了 I2C TPM 的 MITM 攻擊，包括主動竄改 TPM 回傳值的可行性。

- **AMD PSP 的公開研究**：iosifsvmd 等研究者持續在 AMD PSP 上挖洞，PSP 的 AMD Secure Loader（AGESA）有定期 CVE。跟蹤 AMD 的 PSIRT 公告。

- **Measured Boot + Remote Attestation 的完整鏈**：TPM 攻擊的最終目的是讓 remote attestation 失效（讓驗證者相信一個被攻擊的系統是正常的）。這需要偽造 quote——而 quote 是 TPM 用 EK 簽的，無法偽造。真正的問題是：即使 quote 真實，如果 event log 被篡改，驗證者看到的信息也是錯的。

- **Intel TXT（Trusted Execution Technology）**：Intel 的 DRTM（Dynamic Root of Trust for Measurement）用 SINIT ACM 做動態量測，理論上可以在系統運行中建立一個新的可信根，不依賴靜態開機鏈。這是對抗「BIOS 被改但 OS 想確認自己環境可信」問題的一個方向，但 SINIT ACM 本身也有 CVE（CVE-2020-8705）。

---

## 本章重點

- dTPM 的 LPC/SPI 匯流排是明文的——邏輯分析儀接上去就能抓 unsealed secret（BitLocker VMK）
- TPM Genie 等 MITM 裝置能透明攔截 TPM 通訊，passive 監聽或 active 竄改
- PCR replay 在同一 TPM 上可行（還原 PCR 值），跨 TPM 不可行（SRK 不同，sealed object 無法解密）
- fTPM 消除匯流排竊聽，但把安全性依賴轉移到 CPU 廠商的安全 enclave 韌體（PSP/TXE），faulTPM 展示了電壓故障注入可突破
- CVE-2023-1017/1018 是 TPM 2.0 規格層的 OOB 漏洞，ring-0 可觸發
- TPM 只保護 key，不保護量測邏輯——信任鏈起點（BootGuard/fuse）不可信，PCR 值就可被操控
- BitLocker TPM + PIN 是對匯流排竊聽最廉價的有效緩解

---

## 自我檢核

- [ ] 能解釋 dTPM 為什麼有匯流排竊聽問題，fTPM 如何解決這個問題，以及 fTPM 引入了什麼新的攻擊面
- [ ] 能說出 PCR replay 在同一個 TPM 上可行但跨 TPM 不可行的原因
- [ ] 知道 CVE-2023-1017/1018 的觸發條件和需要的前提（ring-0）
- [ ] 能用一句話說明「TPM 不保護量測邏輯」的含義，並舉一個具體場景
- [ ] 理解 PCR[7]-only 綁定為什麼不能防「攻擊者在 db 加自簽憑證」
- [ ] 能說出 BitLocker TPM + PIN 如何緩解匯流排竊聽攻擊

---

## 延伸閱讀

1. **"TPM Genie: Interposing on TPM Communications" — Henry Crall（DEF CON 26, 2018）**  
   讀哪裡：GitHub `kinibi/tpm-genie`，DEF CON 26 白皮書 PDF  
   學什麼：dTPM 匯流排 MITM 的硬體設計，I2C TPM 攔截的電路和協定細節  
   關聯：本章匯流排竊聽段落的技術細節，是 stacksmashing 影片的前身

2. **"faulTPM: Exposing AMD fTPMs' Deepest Secrets" — Jacob et al.（USENIX Security 2023）**  
   讀哪裡：USENIX Security 2023 論文集，或 arxiv 2304.11279  
   學什麼：AMD PSP 故障注入的完整攻擊鏈——電壓波形設計、時序分析、seed 提取  
   關聯：本章 fTPM 漏洞段落的一手來源，也銜接 Ch 34 故障注入的硬體技術

3. **"TPM 2.0 Library Vulnerabilities" — Quarkslab Blog（2023）**  
   讀哪裡：blog.quarkslab.com 搜尋 "TPM 2.0 CVE-2023-1017"  
   學什麼：CVE-2023-1017/1018 的完整分析，包括規格文字的問題位置、實作差異、修補方式  
   關聯：本章規格層漏洞段落的直接來源，也顯示「規格即代碼」的安全風險

→ [下一章](./41-tee-sgx-comparison.md)
