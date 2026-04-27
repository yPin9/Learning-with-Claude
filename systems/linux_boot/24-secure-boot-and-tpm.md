# Ch 24 — Secure Boot、TPM、measured boot

> 目標：搞懂 Secure Boot 怎麼驗 bootloader、TPM 跟 measured boot 是什麼、為什麼這套對伺服器 / 機密運算重要。

## 我們在哪裡

橫跨第 2、3、4 階段的安全機制。

## 為什麼要這些

開機流程是「root of trust」 — 它跑的東西最特權。如果攻擊者：

- 改你的 bootloader → 之後所有事情他控制
- 改你的 kernel → root 是他的
- 改你的 initramfs → 偷你的 LUKS 密碼

實體碰得到機器的人本來就贏（拆磁碟讀檔），但攻擊鏈也包含**遠端改 bootloader 或 kernel image**（透過 root exploit）、**evil maid attack**（在你不在時短暫實體存取）等。

防禦：

- **Secure Boot**：firmware 驗證 bootloader/kernel 簽章
- **TPM + measured boot**：硬體量測整個 boot chain，OS 解密 disk 的 key 綁在量測值上
- **Trusted Path**：UEFI shell / setup 自身可信

## Secure Boot 機制

UEFI 的功能。設計很簡單（理論上）：

1. firmware 內建幾把公鑰（Microsoft 等）
2. 載 bootloader 前先驗 PE 簽章
3. 驗失敗 → 拒絕 boot

四種 key/cert：

| 名稱 | 用途 |
|---|---|
| **PK** (Platform Key) | 主公鑰，OEM 出廠或使用者設定，控制其他 key 的修改 |
| **KEK** (Key Exchange Key) | 中介 key，用來簽 db / dbx 的更新 |
| **db** (signature database) | 允許開機的 bootloader 簽章 |
| **dbx** (forbidden) | 禁止的簽章（被廢的 bootloader / known-bad） |

開機流程：

```
firmware 載 .efi
   ↓ 驗 signature 對 db
   通過 → 跑
   失敗 → 拒絕（或進 setup）
```

## Microsoft 的角色

幾乎所有 OEM 韌體**只內建 Microsoft 的 key**（PK + KEK）。原因：

- Windows 必須能 boot
- 所以 Windows bootloader 用 MS key 簽
- 所以 firmware 必須有 MS key

對 Linux 影響：要在 Secure Boot 開的機器上開機，**bootloader 必須被 Microsoft 簽過** — 不然進不了。

但 Microsoft 不直接簽 GRUB，因為 GRUB 太複雜、bug 多。所以走 **shim** 機制。

## Shim 是什麼

**`shim.efi`**：一個小的 UEFI app，由 Microsoft 簽。它的任務：

1. 自己被 Secure Boot 驗（用 MS 簽章）
2. 載入下一個 bootloader（通常是 GRUB）
3. 驗那個 bootloader 用 distro 的 key（不是 MS key）

所以實際的 chain：

```
firmware (內建 MS key)
   ↓ 驗 shim.efi 簽章 = MS key ✓
shim.efi (內建 distro key, 例如 Canonical)
   ↓ 驗 grubx64.efi 簽章 = distro key ✓
grubx64.efi
   ↓ 驗 vmlinuz 簽章 = distro key ✓
vmlinuz
```

每家 distro（Ubuntu, Fedora, Debian, ...）的 shim 內建自家 key，可以簽自己的 GRUB / kernel。

## MOK (Machine Owner Key)

如果你想 boot 自己編譯的 kernel / driver（DKMS 編的 NVIDIA 等）：

- 你的 binary 沒被 distro 簽
- shim 不會放行

解法：**MOK** — 使用者自己的 key，shim 也接受。

註冊 MOK：

```bash
# 產 key
openssl req -new -x509 -newkey rsa:2048 -keyout MOK.priv -outform DER -out MOK.der -nodes -days 36500 -subj "/CN=MyMOK/"

# 註冊（reboot 進 MOK Manager UI 確認）
sudo mokutil --import MOK.der

# 簽 kernel module
/usr/src/linux-headers-$(uname -r)/scripts/sign-file sha256 MOK.priv MOK.der mymodule.ko
```

reboot 後 MOK Manager (`mmx64.efi`) 跳出來要你輸入剛剛 mokutil 的密碼確認。確認後 key 寫進 MokList NVRAM 變數。

## 關 / 開 Secure Boot

進 BIOS setup → Secure Boot → Enable / Disable。

關掉的影響：

- 任何 .efi 都能開機（Linux 自製 bootloader、Linux Live USB 等）
- 失去 boot chain 完整性保證
- Windows 11 要求 Secure Boot 開（不開拒絕安裝）

## TPM 是什麼

**TPM (Trusted Platform Module)**：主機板上的安全晶片，提供：

- **PCR (Platform Configuration Register)**：23 個 register，**只能 extend 不能 reset**
- **NVRAM**：給 secure key 用
- **加密原語**：RNG、hash、AES、RSA、ECC
- **attestation**：簽出 PCR 值給 remote verifier

PCR extend 操作：

```
new_PCR = SHA256(old_PCR || measurement)
```

只進不退。要重置 PCR 必須 reboot 機器。

## Measured Boot

每段 boot 把下一段 hash 後 extend 到對應 PCR：

| PCR | 內容 |
|---|---|
| 0 | UEFI firmware code |
| 1 | UEFI firmware config |
| 2 | Option ROM (顯卡 firmware 等) |
| 3 | Option ROM config |
| 4 | MBR / bootloader code |
| 5 | bootloader config |
| 6 | manufacturer state |
| 7 | Secure Boot state, MOK, db, dbx |
| 8-9 | bootloader + cmdline + kernel + initramfs |

每段做的事：

```
firmware: hash(自己) → extend PCR 0
firmware: hash(bootloader) → extend PCR 4
bootloader: hash(grub.cfg) → extend PCR 5
bootloader: hash(kernel) → extend PCR 8
bootloader: hash(initramfs) → extend PCR 9
bootloader: hash(cmdline) → extend PCR 8 / 9
```

**結果**：開機完成後 PCR 值代表整個 boot chain 的 hash。任何一個檔案改了，PCR 不一樣。

## TPM 解密磁碟

實用場景：用 PCR 綁定 LUKS key。

```bash
# 用 systemd-cryptenroll 把 PCR 0,2,4,7 綁 LUKS
sudo systemd-cryptenroll /dev/sda3 --tpm2-device=auto --tpm2-pcrs=0,2,4,7
```

之後：

- 正常開機 → PCR 值對 → TPM 釋放 key → LUKS 自動解鎖、不用輸入密碼
- 攻擊者把磁碟拔出 → PCR 不對（因為他用別的硬體 boot） → TPM 不放 key
- 攻擊者改 bootloader → PCR 4 改變 → TPM 不放 key

這就是 BitLocker 的原理。Linux 也能做。

## attestation

TPM 可以「簽出」目前 PCR 值，讓 remote server 驗證。

工作流：

1. server 給你一個 nonce
2. 你的 TPM `quote(nonce, PCR_indices)` → 簽過的 blob
3. 你回給 server
4. server 用 TPM 出廠的公鑰驗簽、檢查 PCR 是否符合預期

這是 **remote attestation**。雲廠商用這個證明「你跑在 confidential VM 裡」。

## measured boot 跟 secure boot 的差別

很多人混。差別：

| 項目 | Secure Boot | Measured Boot |
|---|---|---|
| 機制 | 簽章驗證，**fail 拒絕** | 量測 + 記錄，**fail 不拒絕** |
| 控制者 | firmware（驗就停） | 系統 / TPM（讓你選擇怎麼用 PCR） |
| 阻擋攻擊 | 直接（不開機） | 間接（開機但 TPM 不放 secret） |
| 必要硬體 | UEFI | UEFI + TPM |

兩者**互補**，不衝突。Secure Boot 確保 boot 的東西被授權；Measured Boot 確保 boot 的東西沒被改 + 給 remote 驗證。

## Linux 對 Secure Boot 的 caveat

- **kernel module signing**：開了 Secure Boot 後 kernel 拒絕載沒簽的 module。NVIDIA / VirtualBox driver 需要 MOK 簽
- **lockdown mode**：kernel 限制 root 修改 kernel memory（如 /dev/mem 寫入、kexec）— 為了防 root 透過 kernel 繞過 Secure Boot
- **hibernation**：跟 lockdown 衝突，多半會被 disable

## 一個常見誤解：「Secure Boot 防止 root exploit」

**錯**。Secure Boot 只驗 boot 階段的二進位，**OS 跑起來後不參與**。root 在 OS 裡能做的事還是一樣多。

Secure Boot 防的是「root 透過寫 disk image 種後門讓下次 boot 用後門 kernel」這類**持久化** + **boot-time** 攻擊，不是執行階段。

## 一個常見誤解：「TPM = 加密 disk」

TPM **不加密** disk 本身。LUKS / BitLocker 才是加密。TPM 的角色是**安全保管 LUKS key** + **綁定 boot 狀態**。

技術上：LUKS key 還是存在 disk header（被 TPM 的 wrapping key 加密過）。TPM 在開機時 unwrap 給 OS，OS 解 LUKS。

## 一個常見誤解：「我家用機需要這些」

家用桌機、不擔心 evil maid、不擔心硬體被偷 → Secure Boot **可開可關**，TPM **可有可無**。

server / laptop 帶到外面 / 處理機密資料 → 強烈建議 Secure Boot + TPM 綁 LUKS。

## 動手練習

**1. 看你機器 Secure Boot 狀態**

```bash
mokutil --sb-state
# SecureBoot enabled / disabled

# 看內建的 keys
mokutil --list-enrolled
```

**2. 看 TPM 在不在**

```bash
sudo dmesg | grep -i tpm
ls /dev/tpm0    # 在的話有 device
sudo tpm2_pcrread     # 需要 tpm2-tools
```

**3. 看 PCR**

```bash
sudo tpm2_pcrread sha256:0,1,2,4,7
# sha256:
#   0 : 0x...
#   1 : 0x...
#   ...
```

每次開機 0 / 1 應該一樣（hardware 沒換）；7 變動表示 Secure Boot 設定改了。

**4. 看 measured boot event log**

```bash
ls /sys/kernel/security/tpm0/binary_bios_measurements
sudo tpm2_eventlog /sys/kernel/security/tpm0/binary_bios_measurements | less
```

每個 event 記什麼東西被 hash 進哪個 PCR。**很長**，但有教育意義。

**5.（進階）systemd-cryptenroll 綁 TPM**

如果你機器有 TPM 2.0 + LUKS：

```bash
sudo systemd-cryptenroll /dev/sda3 --tpm2-device=auto --tpm2-pcrs=7
```

下次 boot 不問密碼自動解。**先備份 LUKS header 跟 recovery passphrase 再玩**：

```bash
sudo cryptsetup luksHeaderBackup /dev/sda3 --header-backup-file /root/luks-header.bin
```

## 自我檢核

- [ ] 知道 PK / KEK / db / dbx 是什麼
- [ ] 知道 shim 為什麼存在
- [ ] 知道 MOK 怎麼註冊、為什麼要
- [ ] 知道 PCR 0-9 各 measure 什麼
- [ ] 知道 Secure Boot 跟 Measured Boot 的差別
- [ ] 跑過 `mokutil --sb-state` 跟 `tpm2_pcrread`

最後一站：把所有東西組起來，自己 from scratch boot 一台 Linux。

→ [Final Project：從零組最小 Linux](./final-project-minimal-linux.md)
