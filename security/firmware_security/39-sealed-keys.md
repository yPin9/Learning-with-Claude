# Ch 39 — Sealed key：BitLocker / LUKS 綁 PCR

> **目標**：理解 TPM sealing 的核心概念——把 secret 綁到 PCR policy，只有開機量測序列完全一致才能 unseal。掌握 BitLocker 綁 PCR[7]/PCR[11] 的實務邏輯、LUKS 透過 systemd-cryptenroll/clevis 綁 TPM 的操作，以及為什麼韌體更新會讓加密磁碟變磚、sealed key 暴露哪些攻擊面。

---

## Sealing 是什麼

TPM sealing（封印）解決了一個根本問題：**金鑰存在哪裡才安全？**

傳統做法是把加密金鑰存在磁碟或記憶體，攻擊者只要把磁碟搬走或做 cold boot attack 就拿到了。Sealing 的想法不同：

```
傳統：  secret  →  加密存 USB/磁碟  →  攻擊者實體存取 = 拿到 secret

Sealing：secret  →  TPM 加密 + 綁到 PCR policy
                                     │
                          只有當 PCR[0..N] = 預期值
                          （亦即：開機鏈沒被竄改）
                          TPM 才肯 unseal 並吐出 secret
```

TPM sealing 的關鍵：**secret 從未以明文離開 TPM**，只有在 PCR 值符合 policy 時，TPM 內部解密後才把 secret 傳出去。攻擊者把磁碟搬走，沒有 TPM 和正確的開機狀態，一樣拿不到 secret。

---

## Sealed Object 的資料結構

TPM 2.0 把 sealed key 存成一個 TPM2B_SENSITIVE 結構，外面用 TPM 的 storage key（通常是 SRK，Storage Root Key）再加密：

```
┌─────────────────────────────────────────────┐
│  Sealed Object (persisted in NVRAM or file) │
│                                             │
│  public area:                               │
│    type = TPM_ALG_KEYEDHASH                 │
│    authPolicy = <PCR digest 或 session policy>│
│    objectAttributes = ...                   │
│                                             │
│  private area (SRK 加密後):                 │
│    sensitive: <你的 secret>                 │
│    authValue  (optional PIN)                │
└─────────────────────────────────────────────┘
```

`authPolicy` 記了 policy digest，定義了「什麼條件下允許 unseal」。最常用的是 PolicyPCR——把一組 PCR 的當前值雜湊進去，PCR 變了，policy 就不 match，unseal 失敗。

---

## TPM2 Policy 體系

TPM2 的 policy engine 設計得很彈性，是一個「policy session 累積 state」的模型：

### PolicyPCR

```
tpm2_startauthsession --policy-session -S session.ctx
tpm2_policypcr -S session.ctx -l "sha256:0,1,2,7" -f pcr_values.bin
tpm2_policygetdigest -S session.ctx -o policy.digest
tpm2_flushcontext session.ctx
```

`tpm2_policypcr` 把 **當前** PCR[0,1,2,7] 的值納入 policy digest。之後 unseal 時，TPM 會重新計算這組 PCR 的現值，跟 policy digest 比對——不同就拒絕。

這意味著：**sealed object 在建立時快照了一個「合法的開機狀態」**，之後只要開機鏈有任何改動（BIOS 更新、Secure Boot 設定變更、改 boot order），PCR 就會不同，unseal 失敗。

### PolicyAuthorize

PolicyPCR 有個大問題：合法的系統更新（BIOS 升版、kernel 升版）也會讓 unseal 失敗，必須重新 seal。

PolicyAuthorize 解決了這個問題：

```
概念：
  1. 有個「授權者」持有一對 RSA key（authKey）
  2. sealed object 的 policy 是：
     「如果收到一個被 authKey 私鑰簽過的 policy ticket，就接受」
  3. 合法更新後，授權者（廠商/IT）用 authKey 私鑰簽一個新的 policy ticket
  4. 使用者用這個 ticket unseal，不需要重新 seal
```

這是 BitLocker 等商業產品在企業環境中實際採用的機制，讓 IT 管理員能推 BIOS 更新而不需要輪換所有機器的 seal。

### PolicyPassword / PolicyAuthValue

最簡單的 policy：輸入 PIN 才能 unseal。可以跟 PolicyPCR 組合（AND 關係）：既要 PCR 對，又要 PIN 對。

---

## BitLocker 綁 PCR 的實務

Windows BitLocker 是 Windows 全磁碟加密，金鑰稱為 VMK（Volume Master Key）。TPM-only 模式下 VMK 就是一個 sealed object，sealing policy 預設使用以下 PCR：

| PCR | 量測內容 | BitLocker 是否使用 |
|-----|---------|------------------|
| PCR[0] | UEFI 韌體（BIOS 本體） | 是（預設） |
| PCR[1] | UEFI 設定（NVRAM 部分） | 否（預設） |
| PCR[2] | Option ROM / 擴充卡韌體 | 否（預設） |
| PCR[3] | UEFI 設定（其餘） | 否（預設） |
| PCR[4] | Boot Manager (bootmgr) | 是（預設） |
| PCR[5] | GPT 磁碟分割表 | 否（預設） |
| PCR[6] | Resume from suspend 事件 | 否（預設） |
| PCR[7] | Secure Boot 狀態（PK/KEK/db/dbx）| **是，最重要** |
| PCR[8-11] | OS bootloader (winload.exe 等) | PCR[11]（Win 8+） |

**PCR[7] 是核心**：只要 Secure Boot 是啟用且設定未變，PCR[7] 就穩定。關掉 Secure Boot 或更換 db 憑證，PCR[7] 就變。這讓 BitLocker 的威脅模型變成：「如果 Secure Boot 保證了 bootloader 是合法的，那 VMK 就安全」。

**PCR[11]** 是 Windows 自己 extend 進去的「BitLocker state」，用來確認 BitLocker driver 本身沒被竄改。

### 為什麼更新韌體會要求 BitLocker 恢復金鑰

```
BIOS 更新前後：
  更新前 PCR[0] = hash(BIOS_v1.2)  →  sealed VMK 存的 policy 針對這個值
  更新後 PCR[0] = hash(BIOS_v1.3)  →  PCR 不同 → unseal 失敗 → 要求 recovery key

Windows 的解決方案：
  1. 大多數廠商的 BIOS 更新工具會在更新前暫停 BitLocker
     （讓 BitLocker 以「suspended」模式開機，不驗 TPM）
  2. 更新後 BitLocker 自動重新 seal 到新的 PCR 值
```

這個「先 suspend 再更新」的流程如果被攻擊者模擬（讓系統以 suspended 模式開機），就可以在沒有正確 PCR 的情況下讀到磁碟——這是 BitLocker suspended 模式的已知弱點。

---

## LUKS + systemd-cryptenroll 綁 TPM

Linux 這邊對應的工具鏈：LUKS 加密磁碟，金鑰由 TPM sealed object 保護。

### systemd-cryptenroll（現代做法）

systemd v248+ 直接支援把 LUKS 金鑰 enroll 到 TPM：

```bash
# 把當前 PCR 值 seal 進 LUKS slot（真跑）
systemd-cryptenroll --tpm2-device=auto \
    --tpm2-pcrs=0+2+4+7 \
    /dev/sda2

# 開機時自動 unseal（/etc/crypttab 加 tpm2-device=auto）
echo "luks-home UUID=... none tpm2-device=auto,tpm2-pcrs=0+2+4+7" \
    >> /etc/crypttab

# 查看現有 token
systemd-cryptenroll /dev/sda2
```

`--tpm2-pcrs=0+2+4+7` 用的 PCR 跟 BitLocker 很像，其中 PCR[7] 確保 Secure Boot 狀態沒被竄改。

### clevis（企業/舊版做法）

clevis 是個更老的工具，支援 TPM2 和 Tang（網路 key server）：

```bash
# 安裝 clevis（Debian/Ubuntu）
sudo apt install clevis-luks clevis-tpm2

# 把 LUKS 金鑰綁到 TPM2，PCR[7] 和 PCR[11]
sudo clevis luks bind -d /dev/sda2 tpm2 \
    '{"pcr_bank":"sha256","pcr_ids":"7,11"}'

# 開機自動解鎖（需要 clevis-initramfs）
sudo update-initramfs -u
```

clevis 把 sealed key 的 JSON handle 存在 LUKS token slot，開機時 initramfs 中的 clevis-luks 自動 unseal 並傳給 cryptsetup。

### LUKS + swtpm 示範（真跑，見動手練習）

動手練習用 swtpm 模擬 TPM，LUKS 操作流程完全真實——只是 TPM 是軟體模擬的。見本章動手練習。

---

## PCR 一變 Unseal 失敗：信任邊界全景

這張圖說明哪些操作會改變哪些 PCR，進而讓 sealed secret 失效：

```
開機事件                     改變的 PCR
─────────────────────────────────────────────────────────
更新 BIOS / UEFI 韌體       PCR[0]
改變 UEFI 設定（Security）  PCR[1], PCR[7]（Secure Boot 狀態）
插拔 PCIe 卡 / Option ROM   PCR[2]
改 boot order               PCR[4]
改 GPT 分割表               PCR[5]
更新 shim / GRUB2           PCR[4], PCR[14]（shim policy）
更換 Secure Boot db 憑證    PCR[7]
關閉 Secure Boot             PCR[7]（完全不同的值）
OS kernel 升版               PCR[8-11]
─────────────────────────────────────────────────────────

→ 以上任何一項發生後，已 sealed 的 LUKS 金鑰必須重新 seal
→ 否則磁碟無法自動解鎖，變成「加密磁碟磚」
```

這就是為什麼企業 IT 的做法是：
1. 升版前用 enterprise policy 工具（MDM/Puppet）先 unseal 並備份金鑰
2. 升版後重新 seal

或者採用 PolicyAuthorize，讓 IT 用 signing key 授權新的 policy，不需要重 seal。

---

## Sealed Key 的攻擊面

擁有 TPM 不代表萬無一失，sealed key 本身也有攻擊面：

### 1. PCR 量測邏輯被攻擊（不是 PCR 值被偽造）

PCR 值是累積 hash，攻擊者沒辦法「偽造」PCR 值讓 TPM 以為開機正常。但如果：
- 量測的動作發生在惡意程式碼執行**之後**（量測邏輯 bug）
- 惡意 bootloader 先執行，然後 extend 正確的 PCR 值

PCR 值就變得無意義。這是「**什麼時候量測**」比「**量測什麼**」更重要的原因。

### 2. PCR[7]-only 綁定的弱點

如果 LUKS 只綁 PCR[7]（Secure Boot 狀態），攻擊者只需要：
1. 保持 Secure Boot **啟用**
2. 在 db 加入攻擊者的自簽憑證
3. 用攻擊者簽章的惡意 bootloader 開機

PCR[7] 的值可能跟原來一樣（因為 Secure Boot 是啟用的，只是 db 多了一個憑證），sealed key 就被 unseal 了。這是「PCR[7] 只告訴你 Secure Boot 有沒有開，不告訴你 db 裡有什麼」的根本限制。

### 3. PCR Replay（見 Ch 40）

理論上，攻擊者如果能重放完全相同的 PCR extend 序列，就能重建相同的 PCR 值。但 TPM 重置後 PCR 歸零，而且 extend 序列是由平台固件決定的，不是攻擊者能隨意控制的。Ch 40 深入這個面向。

### 4. Sleep/Hibernate 洩漏

系統在 suspend-to-RAM 時，已解密的 LUKS key 殘留在記憶體。cold boot attack 可以在電源突然切斷後讀取殘留在 DRAM 的金鑰。TPM sealing 保護的是「磁碟靜止時的 key」，不保護「系統運行時記憶體中的 key」。

### 5. VMK 透過 recovery key 繞過

BitLocker 要求有個 48 位數的 recovery key 作為備份。這個 recovery key 如果洩漏（存在 AD 的 MDM server 被打下、存在 USB 被偷），攻擊者根本不需要攻擊 TPM。TPM sealed key 是**正常開機路徑**的保護，recovery key 是 bypass 路徑。

---

## 動手練習（真跑：swtpm）

### 前置：確認環境

```bash
# WSL 環境確認（真跑）
tpm2_getcap properties-fixed 2>/dev/null | head -5
# 若無輸出，先啟動 swtpm（見練習 F 完整流程）
# 這裡假設已有 /tmp/swtpm 的 socket

# 啟動 swtpm
mkdir -p /tmp/tpm_state
swtpm socket --tpmstate dir=/tmp/tpm_state \
    --ctrl type=unixio,path=/tmp/swtpm.sock \
    --tpm2 --daemon

# 連接 swtpm（環境變數）
export TPM2TOOLS_TCTI="swtpm:path=/tmp/swtpm.sock"
tpm2_getcap properties-fixed | grep TPM2_PT_FIRMWARE_VERSION
```

### Step 1：建立 sealed object

```bash
# 1. 讀取當前 PCR[7] 值（PCR bank sha256）
tpm2_pcrread sha256:7 -o pcr7.bin

# 2. 建立 policy session，綁定 PCR[7]
tpm2_startauthsession -S session.ctx --policy-session
tpm2_policypcr -S session.ctx -l sha256:7
tpm2_policygetdigest -S session.ctx -o seal.policy
tpm2_flushcontext session.ctx

# 3. 建立 primary（parent key），用 owner hierarchy
tpm2_createprimary -C o -g sha256 -G rsa -c primary.ctx

# 4. 建立 sealed object，secret = "mysecretkey"
echo -n "mysecretkey" > seal_data.txt
tpm2_create -C primary.ctx \
    -i seal_data.txt \
    -u sealed.pub \
    -r sealed.priv \
    -L seal.policy \
    -a "fixedtpm|fixedparent|adminwithpolicy"

# 5. 載入 sealed object
tpm2_load -C primary.ctx -u sealed.pub -r sealed.priv -c sealed.ctx

echo "=== sealed object 建立成功 ==="
```

### Step 2：Unseal 成功（PCR 值相同）

```bash
# 新建 policy session，重新評估 PCR[7]
tpm2_startauthsession -S session.ctx --policy-session
tpm2_policypcr -S session.ctx -l sha256:7
tpm2_flushcontext session.ctx

# Unseal
tpm2_startauthsession -S session.ctx --policy-session
tpm2_policypcr -S session.ctx -l sha256:7
tpm2_unseal -c sealed.ctx -p "session:session.ctx" -o unsealed.txt
tpm2_flushcontext session.ctx

echo "Unsealed secret: $(cat unsealed.txt)"
# 預期輸出：Unsealed secret: mysecretkey
```

### Step 3：改 PCR 後 Unseal 失敗

```bash
# 模擬「韌體更新」：手動 extend PCR[7]，改變其值
tpm2_pcrextend 7:sha256=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa

# 確認 PCR[7] 已改變
tpm2_pcrread sha256:7

# 再次嘗試 unseal（應該失敗）
tpm2_startauthsession -S session.ctx --policy-session
tpm2_policypcr -S session.ctx -l sha256:7
tpm2_unseal -c sealed.ctx -p "session:session.ctx" -o unsealed2.txt 2>&1
tpm2_flushcontext session.ctx

# 預期輸出：ERROR: Esys_Unseal(0xA8) - tpm:session(1):the policy was not satisfied
echo "Unseal 失敗：PCR 已變更，sealed key 保護有效"
```

### Step 4：LUKS + clevis + swtpm（選作）

```bash
# 建立測試用 LUKS image（loopback device）
dd if=/dev/zero bs=1M count=100 of=/tmp/test_luks.img
sudo losetup /dev/loop0 /tmp/test_luks.img
sudo cryptsetup luksFormat /dev/loop0 --batch-mode \
    --key-file <(echo "initialpassword")

# 安裝 clevis（若未裝）
sudo apt install -y clevis clevis-luks clevis-tpm2 2>/dev/null

# 用 clevis 把 LUKS key 綁到 swtpm（需要設定 TCTI）
# 注意：clevis 內部呼叫 tpm2-tools，需要同樣的 TCTI 設定
export TPM2TOOLS_TCTI="swtpm:path=/tmp/swtpm.sock"
sudo -E clevis luks bind -d /dev/loop0 tpm2 \
    '{"pcr_bank":"sha256","pcr_ids":"7"}' \
    -k <(echo "initialpassword") 2>&1

# 測試自動解鎖
sudo clevis luks unlock -d /dev/loop0 -n luks-test 2>&1
# 若成功：/dev/mapper/luks-test 存在

# 清理
sudo cryptsetup luksClose luks-test 2>/dev/null
sudo losetup -d /dev/loop0
```

---

## 踩雷

1. **PCR 值快照時機**：建 sealed object 時 PCR 值是當時的快照。如果你在 OS 啟動後才 seal（PCR 已經被 OS 的量測 extend 過），之後 unseal 的條件就是「**那個時間點的 PCR 值**」。系統升版後很可能不一樣，差別在 PCR[4/8/11]。最安全的做法是在已知穩定狀態下建立 seal。

2. **swtpm 重啟 PCR 歸零**：swtpm 是模擬器，重啟後 PCR 重置為 0（跟真 TPM 一樣，但真 TPM 需要電源循環才重置）。實驗中如果不小心讓 swtpm 重啟，之前 seal 時的 PCR 值就消失了，sealed object 會 unseal 失敗——雖然跟你「改 PCR 後失敗」的實驗一樣，但原因不同。

3. **owner hierarchy 清掉後，SRK 會變**：sealed object 的 private area 是用 SRK 加密的。如果 `tpm2_clear` 清掉 TPM（重置 owner hierarchy），SRK 就換了，原本的 sealed object 永久無法解開。BitLocker 遇到這情況就只能靠 recovery key。

4. **PolicyPCR 指定的 PCR bank 要一致**：建 seal 時用 `sha256:7`，unseal 時也要用 `sha256:7`，不能混 sha1 和 sha256。tpm2-tools 不同版本的預設 bank 不同，先 `tpm2_pcrread` 確認你的 TPM 支援哪些 bank。

5. **clevis 的 swtpm TCTI 設定**：clevis 內部 fork 子程序呼叫 `tpm2-tools`，`sudo` 後環境變數不一定繼承。用 `sudo -E` 保留環境，或在 `/etc/tpm2-tools/` 設定 TCTI 設定檔。

---

## 進階延伸

- **PolicyNV**：sealed key 不只能綁 PCR，也能綁 TPM NVRAM 的特定值。比如：只有當某個 NVRAM index 的值是 0x01 時才能 unseal。廠商可以用這個機制做更細粒度的管理。

- **Enterprise BitLocker 管理**：Windows MBAM（Microsoft BitLocker Administration and Monitoring）或 Intune 可以集中管理 BitLocker recovery key 和 seal policy，IT 更新 BIOS 後自動重新 seal。這個中心化管理本身也是攻擊面——打下 AD/MDM 就拿到所有 recovery key。

- **OPAL/TCG 自加密磁碟**：SED（Self-Encrypting Drive）把加密 key 管理在磁碟主控器裡，不依賴 TPM。但 OPAL 有著名的設計缺陷（2018 年 Radboud University 研究），用 SED + BitLocker 組合比單獨用 SED 更安全。

- **Clevis + Tang（網路 key server）**：Tang 是個網路 key release server，磁碟 unseal 需要 Tang server 回應。斷網就無法解鎖，適合不能讓磁碟帶出機房的場景。跟 TPM sealing 可以組合（AND policy：既要 TPM 對，又要 Tang 可達）。

---

## 本章重點

- Sealing 把 secret 綁到 PCR policy，PCR 值改變就無法 unseal；secret 從未以明文離開 TPM
- PolicyPCR 是最常見的 policy：建立時快照 PCR 值，之後只有 PCR 完全相符才能 unseal
- PolicyAuthorize 解決了「合法更新也會破壞 seal」的問題，用簽章授權新 policy
- BitLocker 主要綁 PCR[7]（Secure Boot 狀態）和 PCR[11]，LUKS 用 systemd-cryptenroll 或 clevis
- 韌體更新、Secure Boot 設定變更、換 db 憑證，都會讓 sealed key 失效——正是 sealed key 的信任邊界
- 攻擊面：PCR 量測邏輯被攻擊、PCR[7]-only 弱點（db 加憑證不影響 PCR[7]）、recovery key 旁路

---

## 自我檢核

- [ ] 能解釋 TPM sealing 與「把金鑰存磁碟」的本質差異
- [ ] 能說出 PolicyPCR 建立時做了什麼、unseal 時 TPM 如何驗證
- [ ] 知道 BitLocker 預設綁了哪些 PCR，PCR[7] 量測的是什麼
- [ ] 能操作 tpm2-tools 建立 sealed object，在 PCR 相符時 unseal 成功、PCR 改變後 unseal 失敗
- [ ] 理解韌體更新為什麼會造成 BitLocker 要求 recovery key
- [ ] 能說出「PCR[7]-only 綁定」的弱點（攻擊者加自簽憑證到 db）

---

## 延伸閱讀

1. **TCG PC Client Platform Firmware Profile Specification** — Trusted Computing Group  
   讀哪裡：https://trustedcomputinggroup.org/resource/pc-client-specific-platform-firmware-profile-specification/ ，第 3 節 PCR allocations  
   學什麼：PCR[0-15] 各自量測什麼，TPM 規格層面上 PCR[7] 的語義（Secure Boot Authority 量測）  
   關聯：直接決定了 BitLocker 依賴 PCR[7] 的原因，也是 Ch 38 量測鏈的一手出處

2. **"Encrypting Your Hard Drive with the TPM" — Matthew Garrett（mjg59.dreamwidth.org）**  
   讀哪裡：mjg59 的部落格，搜尋「tpm luks」或「tpm2 sealed key」  
   學什麼：LUKS + TPM seal 的實務坑，包括 PCR 選擇策略、initramfs 整合、PCR[7] 弱點的實際分析  
   關聯：本章 PCR[7]-only 弱點段落的原始分析來源，直接對應攻擊面討論

3. **"BitLocker Overview" — Microsoft Docs + "BitLocker Countermeasures"**  
   讀哪裡：https://learn.microsoft.com/en-us/windows/security/operating-system-security/data-protection/bitlocker/  
   學什麼：BitLocker TPM + PCR 組合的官方設計說明、PCR[11] 的用途、BitLocker suspended 模式的官方說明  
   關聯：對應本章 BitLocker 段落的所有設計決策，也包含 recovery key bypass 的官方緩解建議

→ [下一章](./40-tpm-attacks.md)
