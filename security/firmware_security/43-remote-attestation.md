# Ch 43 — 遠端證明落地

> **目標**：理解 remote attestation 的完整流程——從 TPM quote 的簽章機制到 verifier 的 appraisal policy，搞清楚 AK/EK 的信任建立、DICE 給 IoT 的無 TPM 替代方案、以及 RATS 標準的術語架構；動手在 swtpm 上跑 `tpm2_createak + tpm2_quote`，用 nonce 防重放，`tpm2_checkquote` 驗證，再示範改 PCR 後驗失敗。

---

## Remote Attestation 要解決什麼問題

Ch 42 的掃描策略有一個根本漏洞：**你用一個可能已被攻陷的 OS 去讀韌體狀態，然後把結果回報給你自己**。攻擊者如果在 SMM 層，可以讓讀 SPI flash 的操作回傳假的 golden 數據。

Remote attestation 切斷這條欺騙路徑：

```
沒有 attestation 的世界：
  機器：「我的韌體 hash 是 0xABCD（正常）」
  驗證者：「好，我信你」
  ← 機器可以說謊

有 attestation 的世界：
  機器：「我的 PCR[0-7] 值是 X，由 TPM 的 AK（Attestation Key）簽署，
         nonce 是你給的 N，timestamp 是 T」
  TPM 硬體：把這句話用只有 TPM 知道的私鑰簽名
  驗證者：用 AK 公鑰驗簽 + 確認 nonce + 比對預期 PCR 值
  ← 攻擊者想偽造這個簽章，需要拿到 TPM 的 AK 私鑰
     AK 私鑰永遠不離開 TPM 晶片，無法偽造
```

這是 zero-trust 的基礎：不信任聲明，只信任由硬體根信任背書的加密證明。

---

## RATS 標準術語（RFC 9334）

先把術語說清楚，避免後面混亂。RATS（Remote ATtestation procedureS）是 IETF 標準化的 attestation 框架：

```
┌────────────────────────────────────────────────────────────┐
│  RATS 三個角色（RFC 9334）                                   │
│                                                            │
│  Attester（被驗方）                                         │
│  ← 就是你的伺服器/裝置                                       │
│  ← 持有 TPM，能產生 Evidence（PCR quote）                    │
│                                                            │
│  Verifier（驗證方）                                         │
│  ← 收到 Evidence，比對 Reference Values，判斷可信度          │
│  ← 可以是企業自建的 Keylime server，或 Intel TDX Portal      │
│                                                            │
│  Relying Party（依賴方）                                     │
│  ← 使用 Verifier 的 Attestation Result 來做存取決策          │
│  ← 例如：「只有 attestation 通過的機器才能取 secrets」        │
└────────────────────────────────────────────────────────────┘
```

**Evidence**：Attester 產生的原始證明資料（PCR quote + AK 簽名 + EK 憑證）

**Endorsement**：Trust anchor 對 AK 的背書（TPM 製造商的 EK 憑證，證明「這個 AK 真的在真實 TPM 裡」）

**Reference Values**：Verifier 知道「正常狀態應該有哪些 PCR 值」（golden PCR log）

**Attestation Result**：Verifier 產出的判斷（PASS/FAIL，含信心分數）

RATS 刻意把這三個角色分開，讓架構可以組合：Verifier 可以是專門的第三方服務（Intel Amber、ARM CCA），Relying Party 自己不需要懂 PCR 就能使用 Attestation Result。

---

## TPM Quote 機制

TPM Quote 是 attestation 的核心原語。先看它的結構：

```
TPM2_Quote 輸出結構：
  TPMS_ATTEST：
    │
    ├── magic:          TPM_GENERATED (0xff544347)  ← 防止域外 blob
    ├── type:           TPM_ST_ATTEST_QUOTE
    ├── qualifiedSigner: AK 的 Name（public key hash）
    ├── extraData:      ← 你提供的 nonce（Qualifying Data）
    ├── clockInfo:      TPM 時鐘、重置計數
    ├── firmwareVersion: TPM 韌體版本
    └── attested.quote:
          ├── pcrSelect:  被量測的 PCR 集合（bitmap）
          └── pcrDigest:  所選 PCR 的聯合 hash（SHA-256 over 所有 PCR 值）
    
  signature:
    ├── sigAlg: TPM_ALG_RSASSA / TPM_ALG_ECDSA
    └── sig:    用 AK 私鑰對 TPMS_ATTEST 的 SHA-256 簽名
```

**nonce 的關鍵作用**：`extraData` 欄位放的是 verifier 隨機產生的 nonce。簽名覆蓋了 nonce，所以攻擊者無法把舊的 quote（好的 PCR 值）回放給 verifier（防 replay attack）。

---

## AK 與 EK 的信任建立

這是初學者最常搞混的部分：

```
EK（Endorsement Key）
  ├── 每個 TPM 出廠時燒入的唯一金鑰
  ├── EK 私鑰永遠不離開 TPM
  ├── EK 公鑰有對應的 EK Certificate（由 TPM 製造商 CA 簽發）
  └── 用途：「向外界證明這是一個真實的 TPM 晶片」
  
AK（Attestation Key）
  ├── 在 TPM 內部產生的用途鍵（tpm2_createak）
  ├── AK 私鑰永遠不離開 TPM
  ├── 用於簽 PCR quote
  └── 問題：verifier 怎麼知道這個 AK 真的在一個真實的 TPM 裡？
             而不是軟體模擬的？

信任建立流程（Privacy CA 模型）：
  1. TPM 產生 AK（AK_pub, AK_priv）
  2. Client 把 (AK_pub, EK_pub, EK_cert) 送給 Privacy CA
  3. Privacy CA 驗 EK_cert（由 TPM 廠商 CA 簽發，是真 TPM）
  4. Privacy CA 用 EK_pub 加密一個 credential（含 AK_pub 的 hash）
  5. 只有真實 TPM 的 EK 私鑰能解密這個 credential（TPM2_ActivateCredential）
  6. Client 解密成功 → 向 Privacy CA 證明 AK 確實在這個 EK 對應的 TPM 裡
  7. Privacy CA 發出 AK Certificate

簡化版（swtpm 測試用，跳過 Privacy CA）：
  ← 沒有真正的信任建立，AK 是 local-only trusted
  ← 適合開發測試，不適合生產環境
```

---

## Verifier 的 Appraisal Policy

Verifier 拿到 quote 和 PCR log 後要做三件事：

### 1. 驗簽名

```
tpm2_checkquote 做的事：
  verify(TPMS_ATTEST, signature, AK_pub)
  確認 nonce 在 extraData 裡匹配
  確認 pcrDigest = SHA256(PCR[0] || PCR[1] || ... || PCR[7])
```

### 2. Replay PCR Log 驗算

PCR 值是一連串 extend 操作的結果：
```
PCR[0] 初始 = 0x000...000
extend(SHA256("CRTM Measurement"))   → PCR[0] = SHA256(prev || measurement)
extend(SHA256("UEFI Firmware"))      → PCR[0] = SHA256(prev || measurement)
...
```

Verifier 需要 TPM 的 event log（記錄每次 extend 的值），把 log replay 一遍，確認 replay 結果和 PCR 值吻合。如果 log 和 PCR 不一致，代表有人竄改 log（PCR 無法竄改，但 log 在 DRAM 裡）。

### 3. 比對 Reference Values

```
Appraisal Policy 範例：

IF:
  PCR[0] == sha256(CRTM_GOLDEN_VERSION_1.2)     // CRTM 版本正確
  AND PCR[4] == sha256(BOOTMGFW_SIGNED_V10_0_17) // Boot Manager 未被替換
  AND PCR[7] == sha256(SECUREBOOT_ENABLED_POLICY) // Secure Boot 啟用
THEN:
  RESULT = TRUSTWORTHY
ELSE:
  RESULT = UNTRUSTED (含詳細失敗原因)
```

Reference Values 的維護是最難的部分：每次合法的 BIOS 更新，PCR 值都會改變，Verifier 的 golden PCR database 就要更新。這是為什麼商業 attestation 服務（Intel Amber、Keylime with UEFI DB）有價值——它們維護了廠商 firmware 的已知良好 PCR 值。

---

## Keylime 開源 Attestation 框架

Keylime 是目前最完整的開源 remote attestation 框架，Red Hat / MIT CSAIL 開發：

```
Keylime 架構：

  ┌─────────────────────────────────────────────┐
  │  Keylime Verifier（集中式，可部署多台）         │
  │  ← 儲存 PCR reference values                 │
  │  ← 持續驗證每台 agent 的 attestation          │
  │  ← 配置 policy（哪些 PCR 值可接受）             │
  └──────────────┬──────────────────────────────┘
                 │ mTLS（互相認證）
  ┌──────────────▼──────────────────────────────┐
  │  Keylime Registrar                           │
  │  ← 管理 agent 的 AK + EK 憑證               │
  │  ← 執行 privacy CA 流程（或 self-signed）      │
  └──────────────┬──────────────────────────────┘
                 │ mTLS
  ┌──────────────▼──────────────────────────────┐
  │  Keylime Agent（每台受監控機器上）              │
  │  ← 持有 TPM 存取權                           │
  │  ← 回應 verifier 的 quote 請求               │
  │  ← 也負責 IMA log 上傳（若啟用）               │
  └─────────────────────────────────────────────┘

資料流：
  Verifier 每 N 秒向 Agent 要一份 quote（帶新 nonce）
  Agent 呼叫 TPM2_Quote，回傳 evidence
  Verifier 驗簽 + replay log + 比對 reference values
  驗失敗 → 觸發 revocation action（吊銷憑證、隔離網路、告警）
```

Keylime 的 revocation 機制是生產落地的關鍵：attestation 失敗不只是「記一筆告警」，而是主動撤銷受攻陷機器的認證，讓它無法繼續存取 secrets（vault、certificate）。

---

## DICE：給沒有 TPM 的 IoT 裝置

TPM 是 PC/Server 的標配，但 MCU 上沒有 TPM 晶片（成本、面積、功耗都不允許）。DICE（Device Identifier Composition Engine，TCG 標準）用軟體 + OTP 實現類似的信任鏈：

```
DICE Layer 0（硬體根）：
  ├── Unique Device Secret（UDS）：OTP 燒入的唯一密鑰，只有 DICE Layer 0 可讀
  └── 在 secure boot 啟動時量測 Layer 1（Boot Firmware）

DICE Layer 1：
  ├── CDI_0（Compound Device Identifier）= KDF(UDS, Hash(Layer1_code))
  │   ← 結合了設備唯一性（UDS）和當前 Layer 1 的 code integrity
  ├── 產生 Alias Key Pair：(AK_pub_1, AK_priv_1) = KDF(CDI_0, "Alias")
  └── 量測 Layer 2，產生 CDI_1

DICE Layer N：
  └── 每層都量測下一層，傳遞 CDI（信任傳遞，類似 PCR extend 的邏輯）

最終：Alias Key 是整條 boot chain 的函數
  → 任何一層被替換，CDI 就不同 → Alias Key 就不同 → Remote attestation 失敗
```

DICE 的 Attestation Certificate 格式由 TCG/IETF 標準化（CBOR + COSE 格式），讓 IoT 裝置不需 TPM 也能做 remote attestation。ARM CCA（Confidential Compute Architecture）的 attestation 就是基於 DICE 精神設計的。

---

## Intel/AMD 平台 Attestation

### Intel TDX（Trust Domain Extensions）

```
Intel TDX Attestation 流程：
  TD（Trust Domain，可信虛擬機）
    │ 呼叫 TDCALL[TDG.MR.REPORT]
    ▼
  TD Report（含 TD measurement: MRTD, RTMR）
    │ 用 TDREPORT 轉換成 Quote（由 Intel QE 簽署）
    ▼
  Intel SGX Quoting Enclave
    │ ECDSA 簽名
    ▼
  TDX Quote（ECDSA-P256，由 Intel PCK 憑證鏈背書）
    │
    ▼
  Intel Amber / PCCS（Platform Certification Caching Service）
    ← verifier 向 Intel 驗證 PCK 憑證
```

**RTMR（Runtime Measurement Registers）**：TDX 的 PCR 等價物，軟體可以在 TD 啟動後延伸 RTMR，記錄 container image、config hash 等。

### AMD SEV-SNP

```
AMD SEV-SNP Attestation：
  VM 呼叫 SNP_ATTESTATION_REPORT_REQ
    ↓
  AMD Secure Processor 產生 Attestation Report：
    ├── Measurement（VM 啟動時的 hash）
    ├── Report Data（caller 提供的 nonce/user_data）
    └── 由 VCEK（Versioned Chip Endorsement Key）ECDSA 簽署
    
  VCEK 由 AMD Root CA → AMD SEV CA → VCEK 的憑證鏈背書
  Verifier 從 AMD KDS（Key Distribution Service）取 VCEK 憑證驗證
```

x86 平台的 attestation 正在往 confidential computing 的方向整合：VM 的 attestation 讓雲端租戶能確認自己的 VM 是在正確的硬體上以正確的韌體啟動的，雲端供應商無法竄改。

---

## Attestation 接進 Zero-Trust

NIST SP 800-207 的 zero-trust 架構要求「在每次請求時驗證設備的健康狀態」，remote attestation 是實現這個要求的技術手段：

```
Zero-Trust Policy Engine 整合：

  設備請求存取資源
         │
         ▼
  Policy Engine（PEP/PDP）
         │ 查詢
         ▼
  Attestation Verifier（Keylime / Intel Amber）
         │ 回傳 Attestation Result（PASS/FAIL + 時間戳）
         ▼
  決策：
    PASS + 在 TTL 內 → 允許存取
    FAIL             → 拒絕 + 觸發事件響應
    PASS 但太舊       → 要求重新 attestation
    
  Attestation Result 的 TTL 設計：
    太短（秒級）：TPM quote 開銷大，影響效能
    太長（小時）：攻擊窗口大
    實務建議：10-30 分鐘，配合動態風險的自適應縮短
```

---

## 動手練習：swtpm 上的 TPM Quote 與驗證（真跑）

本節在 WSL 真跑。用 swtpm 建立軟體 TPM，完成 AK 建立 → PCR quote → 驗簽的完整流程，最後示範改 PCR 後驗失敗。

### 環境確認

```bash
# 確認 swtpm 和 tpm2-tools 已安裝（Ch 0/Practice F 已安裝）
which swtpm tpm2_createak tpm2_quote tpm2_checkquote
# 預期看到四個路徑

# 確認版本
tpm2_quote --version
```

### Step 1：啟動 swtpm

```bash
# 建立 swtpm 的狀態目錄
mkdir -p /tmp/swtpm-attestation

# 初始化 swtpm（建立永久狀態）
swtpm_setup --tpmstate /tmp/swtpm-attestation \
            --tpm2 \
            --create-ek-cert \
            --create-platform-cert \
            --lock-nvram \
            --not-overwrite \
            --without-ca 2>/dev/null || true
# --without-ca 跳過 Privacy CA 流程（測試用）

# 啟動 swtpm（背景）
swtpm socket --tpmstate dir=/tmp/swtpm-attestation \
             --tpm2 \
             --ctrl type=unixio,path=/tmp/swtpm-attestation.sock \
             --log level=0 &

SWTPM_PID=$!
sleep 0.5
echo "[+] swtpm started, PID=$SWTPM_PID"

# 設定 TPM 環境變數（tpm2-tools 透過 TCTI 和 TPM 通訊）
export TPM2TOOLS_TCTI="swtpm:path=/tmp/swtpm-attestation.sock"
```

### Step 2：初始化 TPM，建立 EK 和 AK

```bash
# 啟動 TPM（發送 Startup 命令）
tpm2_startup --clear

# 建立 EK（Endorsement Key）
# EK 在 swtpm 初始化時已存在（swtpm_setup 建立），但我們也可手動建
tpm2_createek \
    --ek-context ek.ctx \
    --key-algorithm rsa \
    --public ek_pub.pem
echo "[+] EK created: ek.ctx, ek_pub.pem"

# 建立 AK（Attestation Key）
# AK 是用於 quote 的簽章金鑰，需要在 EK 的 hierarchy 下建立
tpm2_createak \
    --ek-context ek.ctx \
    --ak-context ak.ctx \
    --key-algorithm rsa \
    --hash-algorithm sha256 \
    --signing-algorithm rsassa \
    --public ak_pub.pem \
    --private ak_priv.key \
    --format pem
echo "[+] AK created: ak.ctx, ak_pub.pem"

# 載入 AK 到 TPM（讓 TPM 可以用它簽名）
tpm2_loadexternal \
    --key-algorithm rsa \
    --public ak_pub.pem \
    --key-context ak_loaded.ctx \
    --hierarchy null 2>/dev/null || true
# 如果 AK 是從 createak 來的，用 ak.ctx 即可，不需 loadexternal
```

### Step 3：查看當前 PCR 值

```bash
# 讀取 PCR[0-9]（SHA-256 bank）
echo "[*] Current PCR values (SHA-256 bank):"
tpm2_pcrread sha256:0,1,2,3,4,5,6,7

# 儲存 PCR 快照
tpm2_pcrread sha256:0,1,2,3,4,5,6,7 --output pcr_baseline.bin 2>/dev/null || \
tpm2_pcrread sha256:0,1,2,3,4,5,6,7 | tee pcr_values_before.txt
```

swtpm 初始化的 PCR 值取決於 `swtpm_setup` 時有沒有模擬 measured boot。如果沒跑 QEMU + OVMF，PCR[0-7] 通常全是 0（未量測狀態）。在 Practice F（`swtpm + QEMU measured boot`）裡 PCR 才會有真實的量測值。本練習重點不在 PCR 的具體值，而在 quote/verify 的機制。

### Step 4：產生 Nonce（由 Verifier 端給出）

```bash
# 模擬 verifier 產生 nonce（32 bytes 隨機，hex 格式）
NONCE=$(openssl rand -hex 32)
echo "[+] Verifier nonce: $NONCE"

# 把 nonce 寫入檔案（tpm2_quote 的 --qualification 參數用）
echo -n "$NONCE" > nonce.hex
```

### Step 5：產生 PCR Quote

```bash
# 用 AK 簽 PCR[0-7] 的 quote，附帶 verifier 的 nonce
tpm2_quote \
    --key-context ak.ctx \
    --pcr-list sha256:0,1,2,3,4,5,6,7 \
    --message quote.msg \
    --signature quote.sig \
    --qualification "$NONCE" \
    --hash-algorithm sha256 \
    --pcr pcr_during_quote.bin

echo "[+] Quote generated:"
echo "    quote.msg  (TPMS_ATTEST structure)"
echo "    quote.sig  (RSASSA-PKCS1v15 signature)"
echo "    pcr_during_quote.bin (PCR values at quote time)"

# 顯示 quote 的摘要資訊
tpm2_print --type TPMS_ATTEST quote.msg 2>/dev/null || \
    echo "[*] (tpm2_print not available, skip display)"
```

### Step 6：驗證 Quote（Verifier 端操作）

```bash
# tpm2_checkquote 完整驗證：
# 1. 驗 AK 簽名
# 2. 確認 nonce 在 quote 裡
# 3. 確認 PCR 值和 quote 一致

echo "[*] Verifying quote..."
tpm2_checkquote \
    --public ak_pub.pem \
    --message quote.msg \
    --signature quote.sig \
    --qualification "$NONCE" \
    --pcr pcr_during_quote.bin

if [ $? -eq 0 ]; then
    echo "[+] VERIFICATION PASSED: Quote is valid"
    echo "    AK signature verified"
    echo "    Nonce matches"
    echo "    PCR values are consistent with quote"
else
    echo "[-] VERIFICATION FAILED"
fi
```

### Step 7：示範 PCR 被改後驗失敗

這是整個練習最重要的部分——模擬「韌體被竄改 → PCR 值改變 → attestation 失敗」的完整路徑。

```bash
echo ""
echo "======================================"
echo "TAMPERING SIMULATION"
echo "======================================"

# 方法 A：用 tpm2_pcrextend 模擬 PCR 被新量測延伸
# （這模擬的是「系統在 quote 之後又量測了某個東西」，
#  PCR 值和 quote 裡記的不再一樣）
echo "[*] Extending PCR[8] to simulate a new measurement after quoting..."
tpm2_pcrextend 8:sha256=0000000000000000000000000000000000000000000000000000000000000001

# 現在 PCR[8] 改變了，但我們的 quote 是 PCR[0-7]，
# 如果改的是我們 quote 的範圍，就會驗失敗
# 讓我們改 PCR[0] 來讓 quote 驗失敗
echo "[*] Extending PCR[0] (this is in our quoted range)..."
tpm2_pcrextend 0:sha256=cafecafecafecafecafecafecafecafecafecafecafecafecafecafecafecafe

# 取出竄改後的 PCR 值
tpm2_pcrread sha256:0,1,2,3,4,5,6,7 --output pcr_tampered.bin 2>/dev/null || \
    tpm2_pcrread sha256:0,1,2,3,4,5,6,7 | tee pcr_values_after.txt

# 方法 B：直接竄改 PCR output 檔案（模擬攻擊者偽造 PCR 值）
# 把 quote 時存的 PCR binary 複製一份，隨機改一個 byte
cp pcr_during_quote.bin pcr_forged.bin
python3 -c "
data = bytearray(open('pcr_forged.bin','rb').read())
if len(data) > 0:
    data[len(data)//2] ^= 0xFF  # flip a byte in the middle
    open('pcr_forged.bin','wb').write(bytes(data))
    print('[*] Forged PCR file: byte flipped at offset', len(data)//2)
"

echo ""
echo "[*] Attempting checkquote with forged PCR values..."
tpm2_checkquote \
    --public ak_pub.pem \
    --message quote.msg \
    --signature quote.sig \
    --qualification "$NONCE" \
    --pcr pcr_forged.bin

RESULT=$?
if [ $RESULT -ne 0 ]; then
    echo "[+] EXPECTED FAILURE: Forged PCR values correctly rejected"
    echo "    The verifier detected that PCR values don't match the quote"
    echo "    In a real scenario: firmware was tampered, PCR[0] changed,"
    echo "    attestation fails, secrets are not released."
else
    echo "[-] UNEXPECTED PASS: This should not happen"
fi
```

### 預期輸出摘要

```
[+] swtpm started, PID=1234
[+] EK created: ek.ctx, ek_pub.pem
[+] AK created: ak.ctx, ak_pub.pem
[+] Verifier nonce: a3f2e1b8c9d047e5...
[+] Quote generated
[+] VERIFICATION PASSED: Quote is valid
    AK signature verified
    Nonce matches
    PCR values are consistent with quote

======================================
TAMPERING SIMULATION
======================================
[*] Extending PCR[0] (this is in our quoted range)...
[*] Forged PCR file: byte flipped at offset 16
[*] Attempting checkquote with forged PCR values...
ERROR:esapi:src/tss2-esys/...
[+] EXPECTED FAILURE: Forged PCR values correctly rejected
```

### 清理

```bash
kill $SWTPM_PID 2>/dev/null
rm -f ek.ctx ek_pub.pem ak.ctx ak_pub.pem ak_priv.key ak_loaded.ctx
rm -f quote.msg quote.sig pcr_during_quote.bin pcr_tampered.bin pcr_forged.bin nonce.hex
echo "[*] Cleanup done"
```

---

## Replay Attack 的防護分析

為什麼 nonce 是必要的？沒有 nonce 的 attestation 系統有什麼漏洞？

```
沒有 nonce 的攻擊場景：
  T=0：機器是乾淨的，verifier 收到合法 quote Q0
  T=1：攻擊者植入 bootkit，PCR[0] 改變
  T=2：verifier 要求新的 quote
  T=3：攻擊者的惡意 SMM 代碼回放 Q0（上次的 quote）
  T=4：verifier 看到「合法的 quote」→ PASS
  ← 攻擊者成功欺騙了 verifier

有 nonce 的防護：
  T=2：verifier 要求 quote，附帶 nonce N1（隨機）
  T=3：攻擊者嘗試回放 Q0，但 Q0 裡的 nonce 是 N0 ≠ N1
  T=4：verifier 看到 nonce 不匹配 → FAIL
  ← replay attack 失敗
```

nonce 的強度要求：必須不可預測（CSPRNG 產生），長度至少 128 bits（16 bytes），每次 attestation 換新。

---

## 踩雷

1. **swtpm 沒有真實的 EK Certificate Chain**：swtpm_setup 的 `--without-ca` 產生的是 self-signed EK cert，沒有 TPM 製造商 CA 的背書。這意味著你無法用標準的 Privacy CA 協議驗證「這是真實的 TPM」。測試用 OK，生產環境需要真實 TPM 晶片。

2. **AK 的 Name vs 公鑰**：TPM 裡的物件用 Name 而非 handle 識別，Name = hash(public_area)。tpm2_checkquote 的 `--public ak_pub.pem` 參數是 AK 的公鑰，工具自己算 Name 去比對 quote 裡的 `qualifiedSigner`。搞混了會一直出現「signature verification failed」但原因是找錯了 AK。

3. **PCR bank 要一致**：quote 時指定的 bank（`sha256:`）要和 checkquote 時的一致。如果 TPM 同時支援 SHA-1 和 SHA-256 bank，混用會讓驗證失敗且錯誤訊息不直覺。

4. **swtpm 的 PCR 值在 Startup --clear 後重置**：如果你 kill swtpm 再重啟，PCR 值重置為 0，但之前拿的 quote 用的是舊 PCR，checkquote 自然失敗。要測試持久化場景，swtpm 的狀態檔要保留，且不能 `--clear`。

5. **`tpm2_checkquote` 的 PCR 參數語義**：`--pcr` 傳入的是「我預期 TPM 在 quote 時的 PCR 值」（binary 格式），不是「現在讀 TPM 的值」。這個檔案是 quote 操作時 tpm2_quote 輸出的，不能用現在的 `tpm2_pcrread` 結果替換，否則測試流程就沒意義了。

6. **Keylime 在 VM 環境的限制**：Keylime 的 Agent 需要存取 TPM 裝置（`/dev/tpm0` 或 `tpmrm0`）。在 VM 裡要麼直通物理 TPM（vTPM passthrough），要麼用 swtpm 模擬 + QEMU 的 `-tpmdev` 參數把 TPM 接到 VM 裡。單純的 swtpm socket 不足以讓 VM 內的 Keylime Agent 存取。

---

## 進階延伸

- **Intel TDX 和 AMD SEV-SNP 的 attestation API**：兩者的 quote/report 結構和 TPM quote 邏輯相似（都是 nonce + measurements + 簽名），但硬體層不同。TDX 的 `TDG.MR.REPORT` syscall 和 TPM 的 `TPM2_Quote` command 是等價物。懂了 TPM quote，理解 TDX attestation 只需要對齊術語差異。

- **Keylime 整合 IMA**：Keylime 能把 Linux IMA 的 measurement log 作為 attestation evidence 的一部分上傳，讓 verifier 不只看 PCR 值，還能逐檔案驗證 OS 和 application 層的完整性。這是 zero-trust 端點驗證的最完整開源實現。

- **FIDO 裝置認證（Device Attestation）**：WebAuthn/FIDO2 的平台認證（platform authenticator）底層也是同樣的原理——TPM 或 Secure Enclave 簽署 credential，verifier（網站）驗 attestation statement。FIDO 的 attestation 是 remote attestation 概念在身份認證領域的直接應用。

---

## 動手練習

1. 修改上面的 Step 7，改用**不同的 nonce** 去 checkquote（不動 PCR，只換 nonce），確認 nonce 不匹配也會驗失敗，理解 replay protection 的兩個獨立保護層（簽名覆蓋 nonce + PCR 值）。

2. 在 Practice F 的 swtpm + QEMU 測 measured boot 環境裡，開機後讀 PCR[0-7] 的真實值，把這些值作為 reference values，跑一次完整的 quote/checkquote 流程（PCR 值不是全零，有真實的 UEFI measured boot 結果）。

3. 研究 Keylime 的 `allowlist` 和 `excludelist` 機制（用於 IMA 整合），理解 verifier 是如何維護「允許出現在系統上的檔案 hash 清單」，以及這份清單如何和 attestation policy 結合。

---

## 本章重點

- Remote attestation 用 TPM 硬體根信任把「不可信的 OS 回報」換成「無法偽造的加密證明」
- RATS 三角色（Attester / Verifier / Relying Party）把 evidence 產生、驗證、使用分開，讓架構可以彈性組合
- nonce 是防 replay attack 的核心，必須由 verifier 在每次 attestation 前隨機產生，覆蓋在 AK 簽名裡
- AK 的信任依賴 EK Certificate Chain（TPM 製造商 CA 背書）；swtpm 沒有真實鏈，只適合測試
- DICE 是沒有 TPM 的 IoT 裝置的替代方案，用 OTP（UDS）和 CDI 實現跨 boot 層的信任傳遞
- Keylime 是最完整的開源 attestation 框架，支援持續驗證 + 違規撤銷 + IMA 整合

---

## 自我檢核

- [ ] 能解釋為什麼 OS 層掃描無法對抗 SMM rootkit，remote attestation 如何解決這個問題
- [ ] 能說出 TPMS_ATTEST 結構裡 nonce（extraData）、pcrDigest、signature 各自的角色
- [ ] 知道 EK 和 AK 的差異：EK 證明 TPM 真實性，AK 做實際的 quote 簽名
- [ ] 能解釋 Verifier 的 appraisal 三步驟：驗簽 / replay log / 比對 reference values
- [ ] 知道 DICE 的 CDI 是什麼，為什麼改了 Layer N 的 code 就會讓 Alias Key 不同
- [ ] 動手完成了 swtpm quote 流程，確認改 PCR 後 tpm2_checkquote 失敗

---

## 延伸閱讀

1. **RFC 9334 — Remote ATtestation procedureS (RATS) Architecture**（`rfc-editor.org/rfc/rfc9334`）
   讀哪裡：Section 3（術語）和 Section 5（架構模型：Background-check vs Passport）
   學什麼：RATS 三角色的精確定義，以及兩種 attestation 架構模型的取捨（Attester 直接和 Relying Party 通訊 vs 透過 Verifier 的 token）
   關聯：本章所有術語的一手來源，也是 Intel TDX / AMD SEV-SNP attestation 設計的標準參照

2. **Keylime 官方文件與架構設計**（`keylime.dev/docs`）
   讀哪裡：Architecture Overview 和 Registrar/Verifier/Agent 的互動流程圖
   學什麼：生產環境 attestation 框架的實際設計選擇：mTLS 保護通道、revocation action、IMA policy 整合
   關聯：本章 Keylime 架構一節的補完，也是 Ch 42 IMA 監控接進 attestation 的實作橋樑

3. **TCG DICE Architecture Specification**（`trustedcomputinggroup.org`，搜尋「DICE Architecture」）
   讀哪裡：Section 3（DICE 核心操作：UDS、CDI、Alias Key 的派生流程）
   學什麼：CDI 如何把 boot 層的 code measurement 和設備唯一性（UDS）結合成一個 chain；以及 DICE certificate 格式（CBOR/COSE）
   關聯：本章 DICE 一節的精確技術細節，也是 ARM CCA 和 RISC-V 平台 attestation 的設計基礎

→ [下一章](./44-vendor-mitigations.md)
