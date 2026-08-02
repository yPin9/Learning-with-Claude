# 練習 F — swtpm + QEMU 建 measured boot

> **目標**：從零搭建 swtpm + QEMU + OVMF 的 measured boot 環境，全程真跑。讀 PCR、對照 TCG event log、建立 sealed secret、驗證 PCR 變化讓 unseal 失敗，最後選作本地 attestation（tpm2 quote）。走完這個 Part 7 綜合練習，你會親眼看到「開機狀態改變 → PCR 改變 → sealed key 失效」的完整機制。

**本練習全程真跑。** 環境需求：WSL（Ubuntu 22.04+ 或 Debian 12）、swtpm、tpm2-tools、qemu-system-x86_64、OVMF（secboot）、可選 tpm2-tss、cryptsetup（LUKS 選作）。安裝指令在 Step 0。

---

## 背景：為什麼需要這個環境

Ch 37–41 談了很多 TPM 的機制——PCR extend、sealed key、measured boot、TPM 攻擊。但理論讀再多，不如自己在 swtpm 上操作一次：

```
swtpm（軟體 TPM）
  ↓ Unix socket
QEMU（虛擬 x86 機器）
  ↓ OVMF（UEFI 韌體，支援 TPM）
UEFI 開機，對 TPM 做量測（PCR extend）
  ↓
Linux 啟動後：tpm2-tools 讀取 PCR、操作 sealed key
```

swtpm 的 PCR 行為跟真實 TPM 一致，所有 tpm2-tools 指令在真機上也適用。這個環境讓你在 WSL 裡完整體驗 measured boot 的每個環節。

---

## Step 0：安裝環境

```bash
# WSL Ubuntu 22.04 / Debian 12（真跑）

# 1. 更新 apt
sudo apt update && sudo apt upgrade -y

# 2. 安裝 swtpm 和 tpm2 工具
sudo apt install -y swtpm swtpm-tools tpm2-tools \
    tpm2-abrmd libtss2-dev \
    qemu-system-x86 ovmf \
    cryptsetup clevis clevis-luks clevis-tpm2

# 3. 確認版本
swtpm --version
# 預期：swtpm version 0.7.x 或更新
tpm2_getcap --version
# 預期：tpm2-tools 5.x

# 4. 確認 OVMF 存在
ls /usr/share/OVMF/
# 應有：OVMF.fd 或 OVMF_CODE.fd、OVMF_VARS.fd
# 若沒有，安裝：sudo apt install -y ovmf
ls /usr/share/ovmf/OVMF.fd 2>/dev/null || \
ls /usr/share/OVMF/OVMF.fd 2>/dev/null || \
find /usr -name "OVMF*.fd" 2>/dev/null | head -5
```

---

## Step 1：啟動 swtpm

swtpm 作為 Unix socket server 提供 TPM 服務，QEMU 透過這個 socket 連接。

```bash
# 建立 swtpm 狀態目錄
mkdir -p /tmp/tpm_state
chmod 700 /tmp/tpm_state

# 初始化 swtpm 狀態（只需第一次）
swtpm_setup --tpmstate /tmp/tpm_state \
    --tpm2 \
    --create-ek-cert \
    --create-platform-cert \
    --lock-nvram

# 啟動 swtpm（背景執行）
swtpm socket \
    --tpmstate dir=/tmp/tpm_state \
    --ctrl type=unixio,path=/tmp/swtpm-ctrl.sock \
    --server type=unixio,path=/tmp/swtpm.sock \
    --tpm2 \
    --flags not-need-init \
    --daemon

# 確認 swtpm 在跑
ls -la /tmp/swtpm.sock /tmp/swtpm-ctrl.sock
# 應有 socket 檔案

# 設定 TCTI（讓 tpm2-tools 連到 swtpm）
export TPM2TOOLS_TCTI="swtpm:path=/tmp/swtpm.sock"

# 測試連線
tpm2_getcap properties-fixed 2>/dev/null | head -10
# 預期看到：TPM2_PT_FIRMWARE_VERSION_1 之類的輸出
```

**預期輸出：**
```
TPM2_PT_FAMILY_INDICATOR:
  raw: 0x322E3000
  value: "2.0"
TPM2_PT_LEVEL:
  raw: 0
TPM2_PT_REVISION:
  raw: 138
```

若無輸出，確認 swtpm 是否啟動（`ps aux | grep swtpm`）。

---

## Step 2：QEMU 接 swtpm + OVMF 開機

### 準備 OVMF 可寫副本

OVMF 的 VARS（UEFI 變數）分區需要可寫副本，每個 VM 一份：

```bash
# 建立 VM 工作目錄
mkdir -p /tmp/vm

# 找 OVMF 路徑（不同發行版路徑不同）
OVMF_CODE=""
OVMF_VARS=""
for path in \
    "/usr/share/OVMF/OVMF_CODE.fd" \
    "/usr/share/ovmf/OVMF_CODE.fd" \
    "/usr/share/qemu/OVMF_CODE.fd"; do
    [ -f "$path" ] && OVMF_CODE="$path" && break
done
for path in \
    "/usr/share/OVMF/OVMF_VARS.fd" \
    "/usr/share/ovmf/OVMF_VARS.fd" \
    "/usr/share/qemu/OVMF_VARS.fd"; do
    [ -f "$path" ] && OVMF_VARS="$path" && break
done

# 若只有 OVMF.fd（合併版）
if [ -z "$OVMF_CODE" ]; then
    for path in \
        "/usr/share/OVMF/OVMF.fd" \
        "/usr/share/ovmf/OVMF.fd"; do
        [ -f "$path" ] && OVMF_CODE="$path" && OVMF_VARS="" && break
    done
fi

echo "OVMF_CODE: $OVMF_CODE"
echo "OVMF_VARS: $OVMF_VARS"

# 複製 VARS 供 VM 使用
[ -n "$OVMF_VARS" ] && cp "$OVMF_VARS" /tmp/vm/OVMF_VARS.fd

# 建立一個小的空白磁碟（讓 QEMU 有東西開機嘗試）
qemu-img create -f qcow2 /tmp/vm/disk.qcow2 1G
```

### 啟動 QEMU（無 guest OS，觀察 UEFI + TPM 量測）

```bash
# 停止舊的 swtpm（如果有）
pkill swtpm 2>/dev/null; sleep 1

# 重新啟動 swtpm（確保 PCR 乾淨）
rm -f /tmp/swtpm.sock /tmp/swtpm-ctrl.sock
swtpm socket \
    --tpmstate dir=/tmp/tpm_state \
    --ctrl type=unixio,path=/tmp/swtpm-ctrl.sock \
    --server type=unixio,path=/tmp/swtpm.sock \
    --tpm2 \
    --flags not-need-init \
    --daemon
sleep 1

# 建構 QEMU 指令
QEMU_CMD="qemu-system-x86_64"
QEMU_CMD="$QEMU_CMD -m 512"
QEMU_CMD="$QEMU_CMD -nographic"

# OVMF 韌體
if [ -n "$OVMF_VARS" ]; then
    QEMU_CMD="$QEMU_CMD -drive if=pflash,format=raw,readonly=on,file=$OVMF_CODE"
    QEMU_CMD="$QEMU_CMD -drive if=pflash,format=raw,file=/tmp/vm/OVMF_VARS.fd"
else
    QEMU_CMD="$QEMU_CMD -bios $OVMF_CODE"
fi

# 磁碟（無 OS，UEFI 會進 shell 或顯示錯誤）
QEMU_CMD="$QEMU_CMD -drive file=/tmp/vm/disk.qcow2,format=qcow2"

# TPM 接 swtpm
QEMU_CMD="$QEMU_CMD -chardev socket,id=chrtpm,path=/tmp/swtpm.sock"
QEMU_CMD="$QEMU_CMD -tpmdev emulator,id=tpm0,chardev=chrtpm"
QEMU_CMD="$QEMU_CMD -device tpm-tis,tpmdev=tpm0"

# 啟動 QEMU（背景），等待 UEFI 做完量測
echo "啟動 QEMU..."
echo $QEMU_CMD
$QEMU_CMD &
QEMU_PID=$!
echo "QEMU PID: $QEMU_PID"
sleep 8   # 等 UEFI 開機完成量測

# 讀取 PCR（此時 UEFI 已做完 measured boot 的 extend）
export TPM2TOOLS_TCTI="swtpm:path=/tmp/swtpm.sock"
echo "=== QEMU 開機後的 PCR 值 ==="
tpm2_pcrread sha256:0,1,2,3,4,5,6,7

# 停止 QEMU
kill $QEMU_PID 2>/dev/null
```

**預期輸出：**
```
sha256:
  0 : 0xXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
  1 : 0xXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
  ...
  7 : 0xXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
```

PCR[0] 應該是非零值（UEFI 韌體的量測結果）。若全為零，說明 UEFI 沒有正確連接 TPM，檢查 QEMU 的 tpm-tis 裝置設定。

---

## Step 3：讀 PCR、Dump TCG Event Log

TCG event log 是 UEFI 寫給 OS 的「量測日誌」，記錄了每個 PCR extend 的來源和內容。

### 從 QEMU guest 內部讀 event log（需要 Linux guest）

若你有一個 Linux 的 ISO 或 minimal image，可以把它開進 QEMU 後從裡面讀 event log：

```bash
# 在 QEMU guest 內（若有 Linux）：
# TCG event log 的位置
ls /sys/kernel/security/tpm0/binary_bios_measurements 2>/dev/null
# 或者（舊版 kernel）
ls /sys/class/tpm/tpm0/binary_bios_measurements 2>/dev/null

# 用 tpm2_eventlog 解析（tpm2-tools 提供）
tpm2_eventlog /sys/kernel/security/tpm0/binary_bios_measurements 2>/dev/null | head -100
```

### 用 swtpm 直接觀察（不需要 guest）

swtpm 有個 `--log level=20` 選項可以輸出詳細的 TPM 命令日誌，但這很冗長。更實用的做法是在 QEMU 開機後直接讀 PCR 並對照已知的量測內容：

```bash
# 重新啟動 swtpm（乾淨狀態，PCR 全零）
pkill swtpm 2>/dev/null; sleep 1
rm -f /tmp/swtpm.sock /tmp/swtpm-ctrl.sock
swtpm socket \
    --tpmstate dir=/tmp/tpm_state \
    --ctrl type=unixio,path=/tmp/swtpm-ctrl.sock \
    --server type=unixio,path=/tmp/swtpm.sock \
    --tpm2 --flags not-need-init --daemon
sleep 1

export TPM2TOOLS_TCTI="swtpm:path=/tmp/swtpm.sock"

# 讀開機前的 PCR（應全為零）
echo "=== 開機前（PCR 全零）==="
tpm2_pcrread sha256:0,1,7 | tee /tmp/pcr_before_boot.txt

# 啟動 QEMU（背景）
$QEMU_CMD &
QEMU_PID=$!
sleep 8

# 讀開機後的 PCR
echo ""
echo "=== OVMF 開機後（PCR 有量測值）==="
tpm2_pcrread sha256:0,1,7 | tee /tmp/pcr_after_boot.txt

kill $QEMU_PID 2>/dev/null

# 比較
echo ""
echo "=== 差異（哪些 PCR 被 UEFI 量測了）==="
diff /tmp/pcr_before_boot.txt /tmp/pcr_after_boot.txt
```

**觀察重點：**
- `PCR[0]`：UEFI 韌體本身的量測，開機後一定非零
- `PCR[1]`：UEFI 設定，若 OVMF 有量測就非零
- `PCR[7]`：Secure Boot 狀態，OVMF 的 Secure Boot 實作會量測這裡

---

## Step 4：建立綁 PCR 的 sealed secret，unseal 成功

這一步不需要 QEMU，直接在 swtpm 上操作。

```bash
# 確保 swtpm 在執行
export TPM2TOOLS_TCTI="swtpm:path=/tmp/swtpm.sock"
tpm2_getcap properties-fixed 2>/dev/null | grep -c "TPM2_PT" || {
    echo "swtpm 未執行，重啟中..."
    swtpm socket \
        --tpmstate dir=/tmp/tpm_state \
        --ctrl type=unixio,path=/tmp/swtpm-ctrl.sock \
        --server type=unixio,path=/tmp/swtpm.sock \
        --tpm2 --flags not-need-init --daemon
    sleep 1
}

# 查看當前 PCR[7] 的值
echo "=== 當前 PCR[7] ==="
tpm2_pcrread sha256:7

# Step A：建立 policy（綁 PCR[7] 當前值）
tpm2_startauthsession -S /tmp/seal_session.ctx --policy-session
tpm2_policypcr -S /tmp/seal_session.ctx -l sha256:7
tpm2_policygetdigest -S /tmp/seal_session.ctx -o /tmp/seal.policy
tpm2_flushcontext /tmp/seal_session.ctx

echo "Policy digest 建立：$(xxd /tmp/seal.policy | head -2)"

# Step B：建立 primary key（SRK 等效）
tpm2_createprimary -C o -g sha256 -G rsa \
    -c /tmp/primary.ctx \
    2>/dev/null && echo "Primary key 建立成功"

# Step C：建立 sealed object
SECRET="Part7_Firmware_Security_Secret_2026"
echo -n "$SECRET" > /tmp/seal_data.bin

tpm2_create \
    -C /tmp/primary.ctx \
    -i /tmp/seal_data.bin \
    -u /tmp/sealed.pub \
    -r /tmp/sealed.priv \
    -L /tmp/seal.policy \
    -a "fixedtpm|fixedparent|adminwithpolicy|noda" \
    2>/dev/null && echo "Sealed object 建立成功"

# Step D：載入 sealed object
tpm2_load \
    -C /tmp/primary.ctx \
    -u /tmp/sealed.pub \
    -r /tmp/sealed.priv \
    -c /tmp/sealed.ctx \
    2>/dev/null && echo "Sealed object 載入成功"

# Step E：Unseal（PCR[7] 相符，應成功）
echo ""
echo "=== Unseal 測試（PCR 未變，預期成功）==="
tpm2_startauthsession -S /tmp/unseal_session.ctx --policy-session
tpm2_policypcr -S /tmp/unseal_session.ctx -l sha256:7

tpm2_unseal \
    -c /tmp/sealed.ctx \
    -p "session:/tmp/unseal_session.ctx" \
    -o /tmp/unsealed.bin 2>&1
tpm2_flushcontext /tmp/unseal_session.ctx 2>/dev/null

if [ -f /tmp/unsealed.bin ]; then
    RESULT=$(cat /tmp/unsealed.bin)
    echo "Unseal 成功！Secret = $RESULT"
    [ "$RESULT" = "$SECRET" ] && echo "✓ 內容驗證通過" || echo "✗ 內容不符"
else
    echo "Unseal 失敗（unexpected）"
fi
```

**預期輸出：**
```
=== 當前 PCR[7] ===
sha256:
  7 : 0xXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
Policy digest 建立：...
Primary key 建立成功
Sealed object 建立成功
Sealed object 載入成功

=== Unseal 測試（PCR 未變，預期成功）===
Unseal 成功！Secret = Part7_Firmware_Security_Secret_2026
✓ 內容驗證通過
```

---

## Step 5：改開機組態讓 PCR 變化，示範 unseal 失敗

```bash
echo ""
echo "=== 模擬韌體更新：extend PCR[7]（改變開機量測）==="

# 模擬一次「韌體更新」事件——extend 一個任意 digest 進 PCR[7]
FAKE_FIRMWARE_UPDATE="deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
tpm2_pcrextend 7:sha256=$FAKE_FIRMWARE_UPDATE

echo "PCR[7] 更新後："
tpm2_pcrread sha256:7

echo ""
echo "=== Unseal 測試（PCR 已變，預期失敗）==="
tpm2_startauthsession -S /tmp/unseal_fail_session.ctx --policy-session
tpm2_policypcr -S /tmp/unseal_fail_session.ctx -l sha256:7

tpm2_unseal \
    -c /tmp/sealed.ctx \
    -p "session:/tmp/unseal_fail_session.ctx" \
    -o /tmp/unsealed_fail.bin 2>&1
EXIT_CODE=$?
tpm2_flushcontext /tmp/unseal_fail_session.ctx 2>/dev/null

if [ $EXIT_CODE -ne 0 ]; then
    echo ""
    echo "✓ Unseal 失敗（符合預期）"
    echo "  原因：PCR[7] 的值已改變，policy 不 match"
    echo "  對應真實場景：BitLocker/LUKS 在韌體更新後需要 recovery key"
else
    echo "（Unseal 不預期地成功了，檢查 policy 設定）"
fi

echo ""
echo "=== 信任邊界展示完成 ==="
echo "sealed secret 的保護依賴開機狀態的不變性："
echo "  - 開機狀態正常 → PCR 符合 → sealed key 自動解鎖"
echo "  - 開機狀態改變 → PCR 不符 → sealed key 拒絕解鎖"
echo "這就是 BitLocker / LUKS 加密磁碟的保護機制"
```

**預期輸出：**
```
=== 模擬韌體更新：extend PCR[7]（改變開機量測）===
PCR[7] 更新後：
sha256:
  7 : 0xYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYYY
  （不同於之前的值）

=== Unseal 測試（PCR 已變，預期失敗）===
ERROR: Esys_Unseal(0x18B) - tpm:session(1):the policy was not satisfied

✓ Unseal 失敗（符合預期）
  原因：PCR[7] 的值已改變，policy 不 match
  對應真實場景：BitLocker/LUKS 在韌體更新後需要 recovery key
```

---

## Step 6（選作）：tpm2 quote 做本地 Attestation

Attestation 讓一個外部驗證者確認這台機器的 PCR 狀態。本地 attestation 示範建立 AK（Attestation Key）並 quote PCR 值：

```bash
echo ""
echo "=== 選作：tpm2 quote 本地 attestation ==="

# 重置 swtpm（清掉被 extend 的 PCR，重新建立乾淨狀態）
pkill swtpm 2>/dev/null; sleep 1
rm -f /tmp/swtpm.sock /tmp/swtpm-ctrl.sock
swtpm socket \
    --tpmstate dir=/tmp/tpm_state \
    --ctrl type=unixio,path=/tmp/swtpm-ctrl.sock \
    --server type=unixio,path=/tmp/swtpm.sock \
    --tpm2 --flags not-need-init --daemon
sleep 1

export TPM2TOOLS_TCTI="swtpm:path=/tmp/swtpm.sock"

# Step A：建立 EK（Endorsement Key）
tpm2_createek -c /tmp/ek.ctx -G rsa -u /tmp/ek.pub \
    2>/dev/null && echo "EK 建立成功"

# Step B：建立 AK（Attestation Key）
tpm2_createak \
    -C /tmp/ek.ctx \
    -c /tmp/ak.ctx \
    -u /tmp/ak.pub \
    -r /tmp/ak.priv \
    -s rsassa \
    -g sha256 \
    2>/dev/null && echo "AK 建立成功"

# Step C：建立 nonce（模擬 verifier 的挑戰）
# 在真實 remote attestation 中，nonce 由驗證者提供，防止 replay
openssl rand -hex 20 > /tmp/attestation_nonce.txt
NONCE=$(cat /tmp/attestation_nonce.txt)
echo "Attestation nonce: $NONCE"

# 先做一些量測（simulate measured boot）
tpm2_pcrextend 0:sha256=$(echo -n "OVMF_v2024_1" | sha256sum | cut -d' ' -f1)
tpm2_pcrextend 7:sha256=$(echo -n "SecureBoot_Enabled" | sha256sum | cut -d' ' -f1)

echo ""
echo "模擬開機後的 PCR 狀態："
tpm2_pcrread sha256:0,7

# Step D：產生 quote（TPM 對 PCR 值簽章）
tpm2_quote \
    -c /tmp/ak.ctx \
    -l sha256:0,7 \
    -q "$NONCE" \
    -m /tmp/quote_message.bin \
    -s /tmp/quote_signature.bin \
    -o /tmp/quote_pcr.bin \
    -g sha256 \
    2>&1

echo ""
echo "Quote 產生完成"

# Step E：驗證 quote（用 AK public key 驗簽章）
tpm2_checkquote \
    -u /tmp/ak.pub \
    -m /tmp/quote_message.bin \
    -s /tmp/quote_signature.bin \
    -f /tmp/quote_pcr.bin \
    -q "$NONCE" \
    2>&1 && echo "✓ Quote 驗證成功（PCR 值完整性確認）" \
           || echo "Quote 驗證失敗"

echo ""
echo "=== Attestation 原理摘要 ==="
echo "1. Verifier 發送 nonce（防止 replay）"
echo "2. TPM 用 AK 對 PCR 值 + nonce 簽章 → quote"
echo "3. Verifier 用 AK public key 驗 quote"
echo "4. 若驗通，verifier 知道："
echo "   a. 這個 TPM 確實有 AK 對應的私鑰（只在 TPM 裡）"
echo "   b. PCR 值是 TPM 真實報告的（不能偽造）"
echo "   c. nonce 防止了 replay（舊的 quote 無法重用）"
```

---

## 如果卡住

### swtpm 啟動失敗

```bash
# 確認沒有殘留的 swtpm 程序
pkill -9 swtpm
rm -f /tmp/swtpm*.sock

# 確認 swtpm_setup 已執行（建立初始狀態）
ls /tmp/tpm_state/
# 應有：tpm2-00.permall 或類似檔案

# 重新 setup（若 tpm_state 是空的）
rm -rf /tmp/tpm_state && mkdir -p /tmp/tpm_state
swtpm_setup --tpmstate /tmp/tpm_state --tpm2 --create-ek-cert \
    --create-platform-cert --lock-nvram
```

### tpm2_pcrread 沒有輸出

```bash
# 確認 TCTI 設定
echo $TPM2TOOLS_TCTI

# 試用 device TCTI（若有真 TPM）
export TPM2TOOLS_TCTI="device:/dev/tpm0"
tpm2_pcrread sha256:0 2>&1

# 回到 swtpm
export TPM2TOOLS_TCTI="swtpm:path=/tmp/swtpm.sock"

# 確認 swtpm 版本支援 tpmstate 格式
swtpm socket --help | grep tpmstate
```

### QEMU 無法啟動

```bash
# 確認 KVM 是否可用（WSL 2 可能需要設定）
ls /dev/kvm 2>/dev/null && echo "KVM 可用" || echo "無 KVM，純軟體模擬（慢）"

# 不用 KVM，純軟體模擬
QEMU_CMD="${QEMU_CMD//-enable-kvm/}"

# 確認 OVMF 路徑
find /usr -name "OVMF*.fd" 2>/dev/null

# 最簡測試（不用磁碟，只看 UEFI 能否啟動）
qemu-system-x86_64 -m 256 -nographic \
    -bios /usr/share/OVMF/OVMF.fd \
    -chardev socket,id=chrtpm,path=/tmp/swtpm.sock \
    -tpmdev emulator,id=tpm0,chardev=chrtpm \
    -device tpm-tis,tpmdev=tpm0 &
sleep 5 && tpm2_pcrread sha256:0
```

### tpm2_unseal 回傳 0x18B（policy 不符）

這是**預期的行為**（Step 5 就是故意讓它失敗）。如果 Step 4 的 unseal 也失敗：
```bash
# 確認 sealed.ctx 還在
ls -la /tmp/sealed.ctx

# 若 swtpm 重啟了（PCR 歸零），sealed object 的 policy 就不 match 了
# 解法：重新從 Step 4 開始，在同一個 swtpm session 內建立並 unseal
```

---

## 驗收 Checklist

完成本練習後，對照以下清單確認每個環節都驗證到了：

- [ ] **環境建立**：swtpm 啟動成功，`tpm2_getcap` 回傳 TPM 2.0 資訊
- [ ] **QEMU + OVMF + TPM**：QEMU 啟動後，PCR[0] 非零（UEFI 做了量測）
- [ ] **PCR 觀察**：能對照開機前/後的 PCR 差異，識別哪些 PCR 被 UEFI extend 了
- [ ] **Sealed object 建立**：`tpm2_create` + `tpm2_load` 完成，sealed.ctx 存在
- [ ] **Unseal 成功**：PCR 未變，`tpm2_unseal` 成功取得 secret
- [ ] **Unseal 失敗**：extend PCR[7] 後，`tpm2_unseal` 回傳 policy 不符錯誤
- [ ] **理解邏輯**：能解釋為什麼同樣的 sealed.ctx 在 PCR 不同時無法 unseal
- [ ] **（選作）Quote**：`tpm2_quote` + `tpm2_checkquote` 完成，理解 nonce 防 replay 的機制

---

## 報告模板

完成後填寫：

```
# 練習 F 操作報告

**操作日期**：
**環境**：WSL Ubuntu XX.XX，swtpm X.X，tpm2-tools X.X，OVMF (from ovmf package X.X)

## Step 1：swtpm 啟動
結果：[成功/失敗]
`tpm2_getcap` 輸出關鍵行：

## Step 2：QEMU + OVMF 開機
結果：[成功/QEMU 啟動但無 TPM 量測/失敗]
PCR[0] 開機後的值：
PCR[7] 開機後的值：

## Step 3：TCG event log
觀察到的 PCR 變化（哪些 PCR 非零）：
推測 PCR[0] 量測的是什麼：

## Step 4：Sealed object
建立時 PCR[7] 的值（前 16 bytes）：
Unseal 結果：[成功/失敗]
取得的 secret：

## Step 5：Unseal 失敗驗證
extend 後 PCR[7] 的值（前 16 bytes）：
tpm2_unseal 回傳的錯誤碼：
結論（與預期是否符合）：

## Step 6（選作）：Attestation
AK 建立：[成功/略過]
Quote 驗證：[成功/略過]
Nonce 使用的值：

## 心得與遇到的問題
```

---

## 本練習重點

- swtpm 提供完整 TPM 2.0 語義，所有操作可類比真實 TPM 行為
- OVMF 開機後會對 PCR[0] 延伸韌體量測值，PCR[7] 量測 Secure Boot 狀態
- sealed object 的 PolicyPCR 在建立時快照 PCR 值，unseal 時 TPM 重新評估
- PCR 一旦改變（任何 extend），sealed secret 就無法解鎖——這正是加密磁碟保護的核心
- quote 讓外部驗證者確認 PCR 值的真實性，nonce 防止 replay 攻擊

---

## 自我檢核

- [ ] 能從頭啟動 swtpm 並用 tpm2-tools 連線
- [ ] 能啟動帶 TPM 的 QEMU + OVMF 並觀察 PCR 變化
- [ ] 能建立一個綁 PCR 的 sealed object，在 PCR 相符時 unseal 成功
- [ ] 能 extend PCR 後確認 unseal 失敗，並解釋原因
- [ ] 能解釋 quote 的三個要素（AK 簽章、PCR 值、nonce）各自的作用
- [ ] 知道這個練習的哪些部分對應真實 BitLocker/LUKS 的行為

---

## 延伸閱讀

1. **swtpm 官方文件與 man page** — https://github.com/stefanberger/swtpm  
   讀哪裡：`swtpm(8)`、`swtpm_setup(8)` man page；README 的 Socket 模式說明  
   學什麼：swtpm 的所有啟動選項、TCTI 設定方式、TPM state 的持久化機制  
   關聯：本練習所有 swtpm 指令的一手參考，排錯時最重要的文件

2. **tpm2-tools 使用手冊** — https://tpm2-tools.readthedocs.io/  
   讀哪裡：`tpm2_pcrread`、`tpm2_create`、`tpm2_unseal`、`tpm2_quote` 各自的 man page  
   學什麼：每個 tpm2 指令的 flag 語義、policy session 的建立和使用流程、TCTI 設定  
   關聯：本練習 Step 4-6 所有指令的完整說明，也是日後在真實 TPM 上操作的參考

3. **TCG PC Client Platform Firmware Profile — Section 10 (Event Log)** — Trusted Computing Group  
   讀哪裡：tcg 規格 PDF 第 10 節；配合 tpm2_eventlog 的輸出一起看  
   學什麼：event log 的格式（EventType、PCRIndex、Digest、EventSize）、每種 EFI_EVENT_TYPE 對應哪類量測  
   關聯：本練習 Step 3 的深化閱讀，讓你把 PCR 的數值跟具體的「韌體元件量測」對應起來

→ [下一章](./42-firmware-integrity-monitoring.md)
