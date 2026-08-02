# Ch 37 — TPM 2.0 架構

> **目標**：拆解 TPM 2.0 的內部組成——PCR bank、四個 hierarchy、NV storage、RNG、crypto engine——理解物件模型（primary key / sealed object / EK / SRK / AK），掌握 TPM2_ 命令結構與 session/authorization 機制，以及 dTPM vs fTPM vs Microsoft Pluton 在信任邊界上的本質差異。動手用 swtpm + tpm2-tools 真跑，觀察 hierarchy 與 PCR 初始狀態。

---

## TPM 在信任鏈裡的位置

回到 Ch 2 的全課地圖：measured boot 是「被動記錄」的信任架構——韌體不攔截，只把每個開機階段的量測值寫進 TPM 的 PCR。TPM 是這個架構裡**唯一的物理信任根**：

```
韌體（UEFI / BIOS）
  │  tpm2_hash / PCR extend
  ▼
TPM 晶片  ←────── 硬體隔離，CPU 無法直接讀寫內部狀態
  ├── PCR bank（唯讀累加，無法被 OS 清空）
  ├── sealed key（綁 PCR 值，PCR 變了就打不開）
  └── 證明金鑰（AK，簽 quote，遠端 attestation 的依據）
```

TPM 的定位很明確：**它是一個防竄改的密鑰保管箱加上雜湊累加器**。它不執行程式碼、不能被 DMA 覆蓋、不能被 OS root 清掉。攻擊者打爛了整個 OS，PCR 的歷史紀錄仍然在。

---

## TPM 2.0 內部組成

### PCR Bank（Platform Configuration Register）

PCR 是 TPM 2.0 最核心的原語，整個 measured boot 都建立在它上面。

**結構**：
- 每個 PCR 是固定長度的寄存器（SHA-256 bank 就是 256 bits）
- 重置後全為 0（除了某些 PCR 初始值由規範定義）
- TPM 2.0 支援多個 **hash algorithm bank**，預設至少 SHA-1 和 SHA-256，現代裝置通常也開 SHA-384

```
TPM 2.0 PCR Banks：
  SHA-1   bank：PCR[0] … PCR[23]   （各 160 bits）
  SHA-256 bank：PCR[0] … PCR[23]   （各 256 bits）
  SHA-384 bank：PCR[0] … PCR[23]   （各 384 bits，選配）

共 24 個 PCR index（0–23），每個 bank 各自獨立維護
```

**Extend 操作（不可逆累加）**：

```
PCR_extend(pcr_index, hash_alg, new_measurement):
  PCR[i]_new = Hash_alg( PCR[i]_old || new_measurement )
```

關鍵性質：
- 只能 extend，不能直接寫入任意值（除非 TPM_CC_PCR_Reset，只對允許的 PCR 有效）
- 任何竄改都「留下痕跡」在 PCR 值裡
- 同樣的開機序列，每次 PCR 值相同——這是 sealed key 綁 PCR 的基礎

### 四個 Hierarchy（層級）

TPM 2.0 的物件（金鑰、secret、NV index）全部活在四個 hierarchy 下，每個 hierarchy 有獨立的授權策略：

```
┌─────────────────────────────────────────────────────────┐
│  Endorsement Hierarchy（EH）                            │
│  seed: 出廠燒入，不可更改                               │
│  用途: EK（Endorsement Key）——TPM 身份的「出廠憑証」     │
│  授權: hierarchyAuth（通常由 TPM manufacturer 設定）     │
├─────────────────────────────────────────────────────────┤
│  Storage Hierarchy（SH）                                │
│  seed: 可由擁有者重置（tpm2_changeauth）                │
│  用途: SRK（Storage Root Key）——一般用途主金鑰           │
│       sealed object、encryption key 掛在這裡            │
│  授權: ownerAuth（通常由 OS/使用者設定）                 │
├─────────────────────────────────────────────────────────┤
│  Platform Hierarchy（PH）                               │
│  seed: 由平台韌體（UEFI）控制                           │
│  用途: 韌體階段的操作（UEFI 量測、NV provision）         │
│  授權: platformAuth（OS 啟動後 UEFI 通常鎖定這個）       │
├─────────────────────────────────────────────────────────┤
│  Null Hierarchy（NH）                                   │
│  seed: 每次 TPM reset 重新隨機                          │
│  用途: 臨時金鑰（ephemeral key），不需持久化             │
│  授權: 空（任何人都能用，但 seed 重置後物件失效）         │
└─────────────────────────────────────────────────────────┘
```

**攻擊視角**：
- Endorsement Hierarchy 的 seed 不可改→ EK 的 public key 出廠即定，是 attestation 的信任根
- Storage Hierarchy 可以被 TPM owner reset（清掉所有 SH 物件，BitLocker 就失效了）→ 這是 TPM 遷移攻擊的目標
- Platform Hierarchy 在 UEFI 關手之前由韌體控制→ 若攻擊者打穿 UEFI，可以在 OS 之前操作 PH

### NV Storage（Non-Volatile Storage）

TPM 內建少量 NV RAM（通常 1–8 KB），用 NV Index 定址：

```
NV Index 用途：
  0x01C10002  — EK certificate（廠商憑證）
  0x01C00002  — EK template
  0x01C10100  — EK cert for RSA-2048
  自訂 index   — BitLocker/LUKS 可以把某些 metadata 存這裡
               — Platform rollback counter（UEFI secure boot 反降級）
               — 遠端 attestation 的 AK cert
```

NV Index 有獨立的讀寫授權策略（可以綁 PCR、密碼、或 policy session），是 TPM 裡「比 sealed object 更靈活但也更危險」的儲存機制。

### RNG（Random Number Generator）

TPM 內建硬體 RNG，是唯一一個通過 NIST SP 800-90B 認證的亂數源（通常）。

```bash
# 真跑：從 TPM 拿 32 bytes 亂數
tpm2_getrandom 32 --hex
```

TPM 的 RNG 是很多系統的「高品質種子」來源。Infineon TPM 的 RSA key generation bug（CVE-2017-15361，ROCA）是 RNG 實作問題，證明這裡一旦出問題後果多嚴重。

### Crypto Engine

TPM 2.0 支援的演算法（依 TCG Mandatory Algorithm 規範）：

```
Asymmetric：RSA-2048、ECC P-256（NIST）、ECC P-384
Hash：SHA-1、SHA-256、SHA-384、SM3（中國標準）
Symmetric：AES-128、AES-256（CFB/XOR 模式）
KDF：HMAC-KDF（SP 800-108）、MGF1
簽章：RSASSA-PKCS1v1.5、RSAPSS、ECDSA、ECDAA（匿名證明）
```

---

## 物件模型：金鑰怎麼組織

TPM 2.0 用樹狀結構管理物件，每個節點是一個「crypto object」：

```
Hierarchy (EH / SH / PH)
  └── Primary Key（由 hierarchy seed + template 確定性推導）
        ├── Child Key（普通非對稱金鑰、symmetric 金鑰）
        ├── Sealed Object（data blob 被密封，解封需符合 policy）
        └── Derived Key（HMAC/KDF 子金鑰）
```

### Primary Key

Primary key 的獨特性：**不需要儲存**。TPM 用固定公式從 hierarchy seed 和 template 重建它，重開機後呼叫 `tpm2_createprimary` 用同樣的 template 就能重現同一把 key。

```bash
# 在 Storage Hierarchy 建立 Primary key（RSA-2048）
tpm2_createprimary -C o -g sha256 -G rsa -c primary.ctx
# -C o = owner hierarchy（Storage Hierarchy）
# 輸出 primary.ctx 是 TPM 回傳的 handle，不是 key 本身（key 在 TPM 裡）
```

### EK（Endorsement Key）

- 活在 Endorsement Hierarchy
- **出廠由 OEM 燒入**，private key 永遠不離開 TPM
- Public key 對應的 EK certificate 存在 NV index 0x01C10002
- 用途：遠端 attestation 時，Verifier 用 EK pub 驗「這把 AK 真的是這顆 TPM 產生的」

```bash
# 讀出 EK certificate（swtpm 可能沒有，需要 tpm2_getekcertificate 或廠商工具）
tpm2_nvread 0x01C10002 -o ek_cert.der 2>/dev/null || echo "swtpm: no EK cert pre-provisioned"

# 建立 EK（重現出廠 EK 的 public key）
tpm2_createek -c ek.ctx -G rsa -u ek_pub.pem
```

### SRK（Storage Root Key）

- 活在 Storage Hierarchy，是 BitLocker / LUKS / 一般應用放 sealed object 的根
- 現代 Windows 用 SRK 2048 或 ECC P-256
- 也是 `tpm2_createprimary -C o` 默認產生的那把 key

### AK（Attestation Key）

- 普通非對稱金鑰（ECC 或 RSA），掛在 SRK 下
- 用途：簽 PCR quote，讓 Verifier 驗「這些 PCR 值是這台機器真實開機後的值」
- AK 的 private key 也永遠不離開 TPM，但 public key 可以被 CA 簽成 AK certificate

---

## 命令結構：TPM2_ 命令解剖

TPM 2.0 透過 TIS/CRB 介面收發 command/response 封包，格式統一：

```
Command 封包：
  ┌─────────────────┐
  │ TPM_ST (2B)     │  session tag：NO_SESSIONS / SESSIONS
  │ commandSize (4B)│  完整封包長度
  │ commandCode (4B)│  TPM_CC_xxx（例如 TPM_CC_PCR_Extend = 0x182）
  ├─────────────────┤
  │ handles[]       │  操作目標（PCR index、物件 handle...）
  ├─────────────────┤
  │ sessions[]      │  HMAC session / policy session（如果有）
  ├─────────────────┤
  │ parameters      │  命令參數
  └─────────────────┘

Response 封包：
  ┌─────────────────┐
  │ TPM_ST (2B)     │
  │ responseSize(4B)│
  │ responseCode(4B)│  TPM_RC_SUCCESS = 0x000
  ├─────────────────┤
  │ sessions[]      │  HMAC 回應（用於驗證 TPM 沒被竄改）
  ├─────────────────┤
  │ parameters      │  回應資料
  └─────────────────┘
```

### Authorization 三種形式

| 形式 | 用途 | 說明 |
|------|------|------|
| **Password** | 最簡單，明文密碼 | 適合開發測試，不適合高安全場景（密碼明文傳輸） |
| **HMAC Session** | 加密+完整性保護通道 | 防 bus sniffing（Ch 40 的 TPM eavesdropping 攻擊） |
| **Policy Session** | 條件式授權 | 「PCR 值是 X 才能解封」、「counter 未過期才能授權」——sealed key 綁 PCR 用這個 |

Policy 是 TPM 2.0 最強大也最複雜的授權機制：

```
Policy 語言（可以組合）：
  TPM2_PolicyPCR      — 要求特定 PCR 值
  TPM2_PolicyAuthValue— 要求密碼
  TPM2_PolicyNV       — 要求 NV index 的值
  TPM2_PolicyCommandCode — 只允許特定命令
  TPM2_PolicyCounterTimer — 要求 clock/timer 在範圍內
  TPM2_PolicyOR       — 多個 policy 的 OR 組合
```

BitLocker 的 PCR 綁定本質就是一條 `PolicyPCR` 敘述：「只有在 PCR[0,2,4,11] 值等於預期值時，才允許 unseal 這個 volume master key」。

---

## dTPM vs fTPM vs Microsoft Pluton

三者都聲稱是「TPM 2.0」，但信任邊界完全不同：

```
┌──────────────────────────────────────────────────────────────┐
│  dTPM（discrete TPM）                                        │
│  實體：獨立晶片（Infineon SLB9670、STMicro ST33...）         │
│  介面：LPC bus 或 SPI（TIS 協議）                            │
│  隔離：物理隔離，CPU 透過 bus 通訊                           │
│  弱點：bus 可被 sniffing（Ch 40）；LPC 速度慢                │
│  優點：完全獨立的信任根，CPU 被打爛也不影響 TPM 內部狀態     │
├──────────────────────────────────────────────────────────────┤
│  fTPM（firmware TPM）                                        │
│  實體：在主 CPU 的 TEE 裡（Intel PTT = TXT SGX enclave 前身 │
│        AMD fTPM = AMD PSP 的 TrustZone 安全世界）            │
│  介面：CRB（Command Response Buffer），記憶體映射             │
│  隔離：靠 TEE 隔離，不是物理獨立晶片                         │
│  弱點：TEE 被打穿就等於 TPM 被打穿（AMD PSP fTPM CVE-2021   │
│        -26333 直接讀 TPM secret）                             │
│  優點：無需額外晶片，軟體更新可 patch；速度快                │
├──────────────────────────────────────────────────────────────┤
│  Microsoft Pluton                                            │
│  實體：CPU die 裡的獨立安全處理器（類似 ARM TrustZone）      │
│  介面：CPU internal bus（比 LPC/SPI 快，無 bus sniffing）    │
│  隔離：物理上在 CPU die，但有獨立韌體（Pluton firmware）     │
│  弱點：Pluton firmware 本身可被更新→ 更新管道是攻擊面        │
│  優點：消除 LPC/SPI sniffing；可 OTA patch crypto 演算法     │
└──────────────────────────────────────────────────────────────┘
```

**攻擊者視角的差異**：

| 攻擊手法 | dTPM | fTPM | Pluton |
|----------|------|------|--------|
| Bus sniffing（LPC/SPI 攔截） | 有效（Ch 40 主角） | 無效（無外部 bus） | 無效（晶片內） |
| TEE/PSP 漏洞讀 secret | 無效（物理獨立） | **有效** | 部分（需 Pluton FW bug） |
| 韌體更新注入惡意 TPM FW | 無效（ROM） | 有效（PSP FW 更新） | **需研究 Pluton FW 更新鏈** |
| 冷啟動 / DRAM 讀 PCR | 無效（PCR 在晶片裡） | **理論上可能**（TEE DRAM） | 無效 |

---

## TIS vs CRB 介面

主機和 TPM 通訊有兩個標準協議：

**TIS（TPM Interface Specification）**：
- 使用 LPC bus 或 Low-Pin-Count 介面
- 暫存器映射到固定實體位址（0xFED40000 開頭）
- 速度慢（LPC 最快 33 MHz），適合 dTPM

**CRB（Command Response Buffer Interface）**：
- ACPI 描述的記憶體映射 buffer
- 主機寫命令到 CRB memory region，TPM 讀取後執行，寫回結果
- fTPM / Pluton 用 CRB，速度快
- Linux kernel 有 `tpm_crb` 和 `tpm_tis` 兩個驅動對應兩種介面

```bash
# 查系統用哪種介面
lsmod | grep tpm
# tpm_crb → CRB（fTPM/Pluton）
# tpm_tis → TIS（dTPM LPC）
# tpm_tis_spi → TIS over SPI（常見於嵌入式 dTPM）
```

### Locality（局部性）

TPM 定義 5 個 locality（0–4），決定哪些命令可以執行：

```
Locality 0: OS / 一般應用程式（大多數命令）
Locality 1: ACPI / 平台軟體（有限命令）
Locality 2: 保留（規範未廣泛用）
Locality 3: TXT-authorized（Intel TXT 動態測量用）
Locality 4: 只有 Intel TXT ACM 能用（最高特權，DRTM 必需）
```

DRTM（Ch 2 提到的 Intel TXT / AMD SKINIT）的 PCR[17-23] 只能在 Locality 3/4 被重置。OS 跑在 Locality 0，不能清掉 DRTM 的 PCR——這是 DRTM 的關鍵設計。

---

## 動手：swtpm + tpm2-tools 真跑

### 環境確認

```bash
# WSL Ubuntu 22.04，確認工具已裝
which swtpm tpm2_getcap tpm2_pcrread tpm2_createprimary
# 應該全部有輸出

# 版本確認
swtpm --version
tpm2_getcap --version
```

### 步驟 1：啟動 swtpm 軟 TPM（socket 模式）

```bash
# 建立 swtpm 狀態目錄
mkdir -p /tmp/swtpm-state

# 初始化 swtpm
swtpm_setup --tpm-state /tmp/swtpm-state \
            --tpm2 \
            --createek \
            --create-ek-cert \
            --create-platform-cert \
            --allow-signing \
            --overwrite \
            2>&1 | head -20

# 啟動 swtpm（socket 模式，背景執行）
swtpm socket \
    --tpmstate dir=/tmp/swtpm-state \
    --tpm2 \
    --ctrl type=tcp,port=2322 \
    --server type=tcp,port=2321 \
    --flags not-need-init,startup-clear \
    --log level=1 \
    --daemon

# 確認 swtpm 在跑
pgrep -a swtpm
```

### 步驟 2：讓 tpm2-tools 連 swtpm

```bash
# 設定環境變數指向 swtpm socket
export TPM2TOOLS_TCTI="swtpm:host=127.0.0.1,port=2321"

# 驗證連線：讀 TPM capability
tpm2_getcap properties-fixed 2>/dev/null | head -30
```

預期看到類似：
```
TPM2_PT_FAMILY_INDICATOR:
  raw: 0x322E3000
  value: "2.0"
TPM2_PT_REVISION:
  raw: 0x74
  value: 116
TPM2_PT_MANUFACTURER:
  raw: 0x49424d20
  value: "IBM "
```

### 步驟 3：讀 PCR 初始狀態

```bash
# 讀所有 PCR bank 的值
tpm2_pcrread
```

預期輸出（全 0，因為 swtpm 啟動時做了 TPM2_Startup(CLEAR)）：

```
sha1:
  0 : 0x0000000000000000000000000000000000000000
  1 : 0x0000000000000000000000000000000000000000
  ...
sha256:
  0 : 0x0000000000000000000000000000000000000000000000000000000000000000
  1 : 0x0000000000000000000000000000000000000000000000000000000000000000
  ...
```

### 步驟 4：建立 Primary Key 觀察 Hierarchy

```bash
# 在 Storage Hierarchy（owner）建立 RSA-2048 Primary key
tpm2_createprimary \
    -C o \
    -g sha256 \
    -G rsa2048 \
    -c /tmp/primary-sh.ctx \
    -a "restricted|decrypt|fixedtpm|fixedparent|sensitivedataorigin|userwithauth"

echo "Storage Hierarchy Primary key created"

# 在 Endorsement Hierarchy 建立 Primary key（模擬 EK）
tpm2_createprimary \
    -C e \
    -g sha256 \
    -G rsa2048 \
    -c /tmp/primary-eh.ctx

echo "Endorsement Hierarchy Primary key created"

# 在 Null Hierarchy 建立 Primary key（ephemeral）
tpm2_createprimary \
    -C n \
    -g sha256 \
    -G ecc \
    -c /tmp/primary-nh.ctx

echo "Null Hierarchy Primary key created"

# 讀 Storage Hierarchy Primary 的 public key
tpm2_readpublic -c /tmp/primary-sh.ctx -o /tmp/srk_pub.pem -f pem
echo "=== SRK public key ==="
cat /tmp/srk_pub.pem
```

### 步驟 5：觀察 Hierarchy 的 handle 範圍

```bash
# 查看目前 TPM 的持久性 handle（loaded objects）
tpm2_getcap handles-persistent

# 查 transient handles
tpm2_getcap handles-transient

# 列出 NV index（swtpm_setup 已預置 EK cert 等）
tpm2_getcap handles-nv-index
```

---

## 踩雷

1. **swtpm socket 和 qemu socket 衝突**：如果你同時跑 QEMU（練習 F 用的），swtpm 的 `--ctrl port=2322` 和 `--server port=2321` 要確認沒有被佔用。用 `ss -tlnp | grep 232` 確認。

2. **fTPM 和 dTPM 在 `/dev/tpm0` 看起來一樣**：Linux tpm_crb / tpm_tis 驅動把兩種介面統一成 `/dev/tpm0` 和 `/dev/tpmrm0`（resource manager）。你看不到底層是什麼晶片——要查 ACPI DSDT 或 `dmesg | grep tpm` 找 "using driver tpm_crb" 還是 "tpm_tis"。

3. **`-C o` vs `-C 0x40000001`**：`tpm2-tools` 裡 `-C o` 是 owner hierarchy（Storage），`-C e` 是 endorsement，`-C p` 是 platform，`-C n` 是 null。也可以用數字 handle：`0x40000001`（owner）、`0x4000000B`（endorsement）、`0x4000000C`（platform）、`0x40000007`（null）。看文件要認出兩種寫法。

4. **Primary key 的確定性**：相同的 hierarchy seed + 相同的 template → 永遠產生同一把 key。但 swtpm 的 seed 在 `swtpm_setup` 時隨機產生，存在 `swtpm-state/` 裡。如果你刪了 state 重新 setup，seed 不同，Primary key 也不同。**不要把 swtpm state 目錄當成 key backup**——它保存的是 TPM 整個內部狀態，比一把 key 貴重得多。

5. **Null Hierarchy 的 Primary key 每次 TPM reset 失效**：Null Hierarchy 的 seed 在每次 `TPM2_Startup(CLEAR)` 後重新隨機。`/tmp/primary-nh.ctx` 保存的 handle 重啟 swtpm 就失效。Null Hierarchy 只適合 ephemeral 操作。

6. **swtpm socket vs chardev**：QEMU 接 swtpm 用 `chardev`（`--ctrl type=unixio,path=...`）；直接用 `tpm2-tools` 接要用 `socket`（`type=tcp,...`）。兩種模式不能同時共用同一個 swtpm instance——不同 session 要開不同 swtpm。

---

## 進階延伸

- **TPM 2.0 命令協議的 fuzz 空間**：TPM2_ 命令有 100+ 個，很多解析邏輯複雜，歷史上有 tpm2-tss library 的 OOB read（CVE-2020-24455）和 Intel TXT ACM 的 parsing bug。`tpm2-tools` 底層呼叫 `tpm2-tss`，fuzz `tpm2-tss` 的 marshal/unmarshal 函式是找 TPM 協議 bug 的入口。

- **Policy 語言的組合爆炸**：TCG 的 Policy 設計讓你可以建構出複雜的授權條件組合（PolicyOR、PolicyAND 透過 session nest）。這個靈活性同時也是理解難點——讀 `tpm2_policypcr` + `tpm2_policypassword` 組合的範例，再去看 BitLocker 的實際 policy session 封包（Wireshark 抓 LPC bus 做 TPM protocol analysis）。

- **ECDAA（DAA 匿名證明）**：TPM 2.0 支援 ECDAA，讓裝置可以向 verifier 證明「我有合法的 EK，來自某個製造商」而不洩露 EK public key（保護隱私）。Intel EPID 是 x86 的對應概念。理解 ECDAA 對研究 remote attestation 的隱私保護設計非常關鍵。

---

## 動手練習

完成以下步驟後對照預期結果：

1. 用 `swtpm socket` 起一顆 swtpm，設定 `TPM2TOOLS_TCTI` 環境變數，跑 `tpm2_getcap properties-fixed` 確認 TPM 2.0 版本和廠商字串（IBM）。

2. 跑 `tpm2_pcrread sha256:0,1,2,3,4,5,6,7`，確認初始值全 0，理解為什麼是全 0（Startup(CLEAR) 的行為）。

3. 在 Storage Hierarchy 用 `tpm2_createprimary -C o -G rsa2048` 建立 SRK，用 `tpm2_readpublic` 把 public key 印出，確認它是 RSA 2048。

4. 在 Endorsement Hierarchy 用 `tpm2_createprimary -C e` 建立 EK，用 `tpm2_getcap handles-transient` 查目前 transient handles，理解為什麼同一次 session 跑兩個 `createprimary` 會產生兩個 transient handle。

5. 思考題：如果攻擊者能在 OS 啟動後執行任意程式碼，他能清掉 SHA-256 bank 的 PCR[7] 嗎？為什麼（不）？

---

## 本章重點

- TPM 2.0 有四個 hierarchy（EH/SH/PH/NH），每個有獨立的 seed 和授權策略；EH 的 seed 出廠燒入不可改，是 attestation 的信任根
- 24 個 PCR（每個 bank），extend 操作不可逆，任何量測改動都永遠留在 PCR 值裡
- 物件樹：Primary key 可確定性重建，不需儲存；sealed object 掛在 Primary 下，解封需符合 Policy（通常是 PolicyPCR）
- EK/SRK/AK 三者功能各異：EK 是身份（出廠）、SRK 是儲存根（可 owner reset）、AK 是證明金鑰（簽 PCR quote）
- 命令層：TPM2_ 命令 + session（password/HMAC/policy）+ authorization，Policy 是條件式授權的核心原語
- dTPM 物理隔離但 bus 可被 sniffed；fTPM 在 TEE 裡速度快但 TEE 被打穿就全輸；Pluton 消除 bus 攻擊面但 firmware 更新鏈是新攻擊面
- TIS/CRB 是兩種介面；locality 0–4 決定哪些命令可執行，DRTM 必需 locality 4

---

## 自我檢核

- [ ] 能說出四個 hierarchy 的名稱、各自的 seed 特性、以及典型用途
- [ ] 能解釋 PCR extend 的數學公式（`Hash(old || new_measurement)`）並說明為什麼不可逆
- [ ] 知道 Primary key 的「確定性重建」意味著什麼，以及這個特性為什麼重要
- [ ] 能區分 EK / SRK / AK 的功能差異，並說明三者在遠端 attestation 流程中的角色
- [ ] 知道 Policy Session 和 HMAC Session 的差異，以及哪個用來做 PCR 綁定
- [ ] 能說出 dTPM / fTPM / Pluton 各自的核心弱點，並對應到具體攻擊手法（bus sniffing / TEE bug / FW update）
- [ ] 能用 swtpm + tpm2-tools 啟動軟 TPM，跑 `tpm2_getcap`、`tpm2_pcrread`、`tpm2_createprimary` 並解讀輸出

---

## 延伸閱讀

1. **TCG TPM 2.0 Part 1 Architecture（TPM-Rev-2.0-Part-1-Architecture）** — Trusted Computing Group
   讀哪裡：Part 1 的 Chapter 6（Hierarchy）、Chapter 8（PCR）、Chapter 17（Object）
   學什麼：四個 hierarchy 的設計意圖、Primary key 的確定性推導機制、物件授權的完整語義；這是本章所有概念的一手規範來源
   關聯：直接對應本章的 Hierarchy 和物件模型部分，Ch 38 的 PCR assign 規範也在這份文件

2. **"A Practical Guide to TPM 2.0" — Will Arthur, David Challener（Apress, 2015）**
   讀哪裡：Chapter 3（Architecture）、Chapter 7（Authorization）、Chapter 10（Attestation）
   學什麼：TPM 2.0 命令結構和 session 機制的實作細節、Policy 的組合方式、EK/AK 的 provisioning 流程；比規範更容易上手，且附大量 TSS C API 範例
   關聯：本章的命令結構和 authorization 部分直接來自這本書的框架，Ch 40 的攻擊也需要先懂 session 才能理解 HMAC session 防禦邏輯

3. **"TPM.fail: TPM meets Timing and Lattice Attacks" — Moghimi et al.（USENIX Security 2020）**
   讀哪裡：完整論文，重點看 Section 3（TPM 簽章的 timing 洩漏）和 Section 4（lattice attack 還原私鑰）
   學什麼：fTPM（Intel PTT / AMD fTPM）的 ECDSA 簽章實作存在 timing side-channel，攻擊者可以用 timing 差異還原 signing nonce 再用 lattice attack 推算私鑰——即使 key 在 TPM 裡，也不代表不可被旁路攻擊；和本章 fTPM 弱點的討論直接對應，Ch 40 會深挖
   關聯：和本章 dTPM/fTPM 信任邊界的分析互補，也是理解「TPM 的密碼正確性≠安全」的最佳案例

→ [下一章](./38-measured-boot-chain.md)
