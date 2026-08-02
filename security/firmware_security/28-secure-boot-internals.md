# Ch 28 — Secure Boot 深入：db/dbx/KEK/PK

> **目標**：完整理解 x86 UEFI Secure Boot 的金鑰階層（PK→KEK→db/dbx），掌握 authenticated variable 的簽章格式與時間戳防重放機制，搞清楚 PE/COFF Authenticode 驗簽流程（怎麼算 hash、哪些欄位被跳過），理解 Setup Mode 與 User Mode 的狀態機，以及 shim 的 MOK 擴展機制。動手用 OVMF secboot 環境跑一遍 key enrollment + 簽章驗證，親眼看未簽 EFI 被擋下來。

---

## 為什麼要重新深挖 Secure Boot？

Ch 5 已經講過 authenticated variable 的格式和攻擊面。但那裡的重心是 NVRAM 攻擊（brick、BootOrder 竄改、Setup Mode 濫用），沒有展開 Secure Boot 驗簽鏈本身。Part 5 的重心是「怎麼繞」，所以先要把「怎麼驗」搞清楚——你不理解防線長什麼形狀，就不知道從哪裡打。

本章的目標是讓你能夠：
- 給一個 EFI binary，手動追蹤 Secure Boot 的驗證路徑直到通過或失敗
- 知道 shim 在 Linux 生態的角色，以及為什麼它比你想的複雜
- 跑完 OVMF snakeoil key enrollment，簽一個 EFI 開得起來、未簽的被擋

---

## 金鑰階層：四個角色與信任傳遞

Secure Boot 的信任模型是一條嚴格的單向鏈：

```
┌─────────────────────────────────────────────────────────────┐
│                   PK（Platform Key）                         │
│   ■ 硬體廠商（OEM/IBV）持有私鑰                              │
│   ■ 最高信任根，只有 PK 私鑰能更新 PK 或 KEK                 │
│   ■ 存在 NVRAM，屬性 NV+BS+RT+AT                            │
└────────────────────────────┬────────────────────────────────┘
                             │ PK 私鑰簽
                             ▼
┌─────────────────────────────────────────────────────────────┐
│                  KEK（Key Exchange Key）                      │
│   ■ OEM + OS 廠商（Microsoft）各持一把 KEK                   │
│   ■ KEK 私鑰能更新 db 和 dbx（但不能更新 PK）                │
│   ■ 存在 NVRAM，屬性 NV+BS+RT+AT                            │
└─────────────────┬────────────────────┬──────────────────────┘
                  │ KEK 私鑰簽          │ KEK 私鑰簽
                  ▼                    ▼
┌─────────────────────┐   ┌──────────────────────────────────┐
│   db（白名單）        │   │  dbx（黑名單 / 撤銷清單）         │
│ 可包含：             │   │ 可包含：                          │
│  ・Certificate       │   │  ・Certificate（整張憑證拒絕）     │
│  ・SHA-256 hash      │   │  ・SHA-256 hash（單一 binary）    │
│  ・Authenticode hash │   │  ・Authenticode hash              │
└─────────────────────┘   └──────────────────────────────────┘
         │                           │
         ▼ 用 db 驗 EFI binary       ▼ dbx 中任何命中 → 拒絕
┌─────────────────────────────────────────────────────────────┐
│            UEFI firmware 在 BDS 階段驗每個 Boot Option       │
│            驗通 → 跳入執行；驗失敗 → EFI_SECURITY_VIOLATION  │
└─────────────────────────────────────────────────────────────┘
```

### 四個 variable 的識別

| Variable 名 | GUID | 更新需要 | 典型持有者 |
|------------|------|---------|-----------|
| PK | `8be4df61-...` | PK 私鑰（或 Setup Mode） | OEM/平台廠商 |
| KEK | `8be4df61-...` | PK 私鑰 | OEM + Microsoft |
| db | `d719b2cb-...` | KEK 私鑰 | Microsoft（Windows）、Linux Foundation（shim）|
| dbx | `d719b2cb-...` | KEK 私鑰 | UEFI Forum / Microsoft（dbxupdate.bin）|

GUID `8be4df61-93ca-11d2-aa0d-00e098032b8c` 是 `gEfiGlobalVariableGuid`，PK 和 KEK 都在這個 namespace。`d719b2cb-3d3a-4596-a3bc-dad00e67656f` 是 Image Security Database GUID，db/dbx 在這裡。

---

## EFI_VARIABLE_AUTHENTICATION_2：防重放的設計

Ch 5 已講格式，這裡深入「為什麼這樣設計」。

寫入 db/dbx/KEK/PK 時，`SetVariable` 的 Data 參數不是直接的新 variable 內容，而是這個結構：

```
EFI_VARIABLE_AUTHENTICATION_2
├── EFI_TIME TimeStamp         (16 bytes)
│   ■ UEFI_SPEC 規定：TimeStamp 的 Pad1/Nanosecond/TimeZone/Daylight/Pad2 必須為 0
│   ■ 驗章通過後，firmware 更新 variable 的「已儲存 TimeStamp」為這個值
│   ■ 下次 SetVariable 的 TimeStamp 必須 >= 已儲存值（防 replay）
│
└── WIN_CERTIFICATE AuthInfo
    ├── dwLength           (4 bytes) ← 整個 WIN_CERTIFICATE 結構的長度
    ├── wRevision          (2 bytes) = 0x0200
    ├── wCertificateType   (2 bytes) = 0x0EF1 (WIN_CERT_TYPE_EFI_GUID)
    ├── CertType GUID               = EFI_CERT_TYPE_PKCS7_GUID
    └── CertData                    = DER-encoded CMS SignedData
        ├── Content type: pkcs7-signedData
        ├── SignerInfo:
        │   ├── SignerIdentifier: 簽章者的 Certificate（或 issuer/serial）
        │   ├── digestAlgorithm: SHA-256
        │   └── signature: 對以下 concat 的 RSA-2048/RSA-4096 簽章：
        │       TimeStamp(16 bytes) ‖ VariableName(UTF-16LE) ‖
        │       VendorGuid(16 bytes) ‖ Attributes(4 bytes) ‖ NewData
        └── Certificate chain（用來驗 SignerInfo 的憑證鏈）
```

防重放的核心：**簽章覆蓋了 TimeStamp，而 TimeStamp 必須單調遞增**。攻擊者截到一個合法的 `SetVariable("db", ...)` 封包，不能重放它：因為 TimeStamp 已更新，舊值 `<` 新值，firmware 拒絕。

**攻擊面**：如果系統 RTC（Real Time Clock）可以被從 OS 回撥（e.g., `hwclock --set` 回到過去），某些韌體的 TimeStamp 比對可能失效。UEFI spec 建議實作不依賴 wall clock，但並非所有廠商都做到。

---

## db/dbx 的內容格式：Signature List

db 和 dbx 不只存一個簽章，而是一個 `EFI_SIGNATURE_LIST` 的陣列，每個 list 有一個 SignatureType：

```
EFI_SIGNATURE_LIST #1
├── SignatureType: EFI_CERT_X509_GUID          (0xa5c059a1...)
│   ■ 整張 X.509 DER certificate
│   ■ 驗 EFI binary 的 Authenticode 簽章時，
│     binary 的 code signing cert 必須鏈結到 db 裡的某張 cert
├── SignatureListSize
├── SignatureHeaderSize
├── SignatureSize
└── Signatures[]:
    └── EFI_SIGNATURE_DATA
        ├── SignatureOwner GUID  (誰擁有/放這個 entry)
        └── SignatureData[]     (DER 格式的 X.509)

EFI_SIGNATURE_LIST #2
├── SignatureType: EFI_CERT_SHA256_GUID        (0xc1c41626...)
│   ■ 32-byte SHA-256 hash（單一 EFI binary 的 Authenticode hash）
│   ■ 整個 binary 的 hash 在 db → 白名單
│   ■ 整個 binary 的 hash 在 dbx → 黑名單（比 cert-based 更精確）
└── Signatures[]:
    └── EFI_SIGNATURE_DATA
        ├── SignatureOwner GUID
        └── SignatureData[32]   (SHA-256 hash)
```

**實務上 Microsoft 的 db 裡放的是 certificate（"Microsoft Corporation UEFI CA 2011"），不是單一 binary 的 hash**。這讓一張 cert 能授權數以萬計的 EFI binary，但代價是：任何用這張 cert 簽的 EFI binary 都被信任，包括有漏洞的舊版 GRUB（BootHole）。

**dbx 的設計**：dbx 裡既有 cert 也有 SHA-256 hash。Cert-based 黑名單能一口氣撤銷整個 cert chain，但副作用是把所有用那張 cert 簽的 binary 都撤銷（可能太激進）；Hash-based 黑名單只撤銷特定 binary，但更新成本高（每個有漏洞的版本都要加一個 hash）。BootHole 的 dbx 更新因為有漏洞的 GRUB 版本太多，最後 Microsoft 發了一個巨大的 dbxupdate.bin 帶了幾千個 hash。

---

## PE/COFF Authenticode 驗簽：怎麼算 hash

Secure Boot 驗 EFI binary 用的是 **Authenticode**，這是 Microsoft 的 PE/COFF 程式碼簽章標準（也用於 Windows .exe/.dll）。

### PE/COFF 結構快速回顧

```
DOS Header (0x40 bytes)
  └── e_lfanew: offset to PE signature
PE Signature (4 bytes) = "PE\0\0"
COFF File Header (20 bytes)
  ├── Machine (0x8664 = x86-64, 0xAA64 = ARM64)
  ├── NumberOfSections
  └── ...
Optional Header (96 bytes for PE32+)
  ├── Magic (0x20B = PE32+)
  ├── AddressOfEntryPoint
  ├── ImageBase
  ├── ...
  ├── CheckSum                    ← ① 跳過這個欄位
  ├── ...
  └── Data Directories[16]
      ├── [0] Export Table
      ├── [1] Import Table
      ├── ...
      ├── [4] Certificate Table   ← ② 這個欄位（RVA+Size）跳過內容，但記錄 offset/size
      └── ...
Section Table (N * 40 bytes)
  ├── .text section header
  ├── .data section header
  └── ...
Section Data
  ├── .text (code)
  ├── .data
  └── ...
Certificate Table                 ← ③ 整個 Certificate Table 段不算入 hash
  └── WIN_CERTIFICATE
      └── CMS SignedData（這就是簽章本身，不能算入 hash，否則循環）
```

### Authenticode Hash 計算步驟

UEFI spec 引用 Microsoft Authenticode spec，計算步驟如下：

```
1. 讀整個 PE image 到記憶體

2. 把以下欄位置零（排除在 hash 之外）：
   ■ Optional Header 的 CheckSum 欄位（4 bytes）
   ■ Certificate Table Data Directory 的 VirtualAddress 和 Size（共 8 bytes）

3. 從 image 開頭到 CheckSum 欄位前，算 hash
   from_start_to_checksum = data[0 : checksum_offset]

4. 跳過 CheckSum（4 bytes），繼續到 Certificate Table Data Directory 前
   from_after_checksum_to_cert_dir = data[checksum_offset+4 : cert_dir_offset]

5. 跳過 Certificate Table Data Directory（8 bytes），繼續到 Certificate Table 開頭
   after_cert_dir_to_cert_table = data[cert_dir_offset+8 : cert_table_offset]

6. Certificate Table 本身完全跳過（它存的是簽章，不算入 hash）

7. Certificate Table 之後的資料（如果有）也算入

8. 對以上所有 chunk 的 concat 做 SHA-256
```

用 Python 示意：

```python
import hashlib, struct, sys

def authenticode_hash(pe_data: bytes) -> bytes:
    """計算 PE/COFF 的 Authenticode SHA-256 hash（簡化版）"""
    d = bytearray(pe_data)
    
    # 找 PE header offset
    e_lfanew = struct.unpack_from('<I', d, 0x3c)[0]
    pe_off = e_lfanew
    # Optional header offset = PE sig (4) + COFF header (20) = +24
    opt_off = pe_off + 24
    magic = struct.unpack_from('<H', d, opt_off)[0]
    assert magic == 0x20b, "Not PE32+"
    
    # CheckSum 在 Optional Header offset 64
    checksum_off = opt_off + 64
    # Certificate Table Data Directory 在 offset 144（PE32+ 中 DataDirectory[4]）
    cert_dir_off = opt_off + 144  # DataDirectory 從 offset 112, [4] = 4*8 = +32 → 112+32=144
    cert_dir_rva  = struct.unpack_from('<I', d, cert_dir_off)[0]
    cert_dir_size = struct.unpack_from('<I', d, cert_dir_off+4)[0]
    cert_table_off = cert_dir_rva  # 假設 RVA = file offset（簡化，實際需要 section mapping）
    
    h = hashlib.sha256()
    # chunk 1: 開頭到 checksum 前
    h.update(d[0:checksum_off])
    # chunk 2: checksum 後到 cert dir 前
    h.update(d[checksum_off+4 : cert_dir_off])
    # chunk 3: cert dir 後到 cert table 前
    h.update(d[cert_dir_off+8 : cert_table_off])
    # chunk 4: cert table 後的尾巴（若有）
    h.update(d[cert_table_off + cert_dir_size :])
    
    return h.digest()
```

### 驗簽決策邏輯

```
EFI binary 載入後，BDS（Boot Device Selection）呼叫 Security2 Architecture Protocol：

驗簽(binary):
    1. 計算 binary 的 Authenticode hash H
    
    2. 先查 dbx（黑名單）：
       ■ 若 binary 的簽章 cert 在 dbx 的 cert list → 拒絕
       ■ 若 H 在 dbx 的 hash list → 拒絕
    
    3. 再查 db（白名單）：
       ■ 若 binary 帶有嵌入簽章（WIN_CERTIFICATE）：
           - 驗 CMS SignedData 的簽章
           - 驗 signing cert 鏈結到 db 裡的某張 cert
           - 兩者都通過 → 允許
       ■ 若 H 在 db 的 hash list → 允許（無需簽章，直接 hash 白名單）
    
    4. 否則 → EFI_SECURITY_VIOLATION，拒絕載入

關鍵順序：dbx 先於 db。即使 binary 在 db 白名單，
若同時在 dbx 黑名單，dbx 優先，仍然拒絕。
```

---

## Setup Mode 與 User Mode：狀態機

Secure Boot 的金鑰生命週期用兩個狀態描述：

```
                    ┌─────────────┐
                    │  Setup Mode │  PK 未部署
          出廠預設 → │  SetupMode=1│  ← 任何人都能寫 PK（不需簽章）
                    │  SecureBoot=0│    也能直接寫 KEK/db/dbx
                    └──────┬──────┘
                           │
                           │  SetVariable("PK", ...) 成功
                           │  （PK 被部署，此後 PK 只有 PK 私鑰能改）
                           ▼
                    ┌─────────────┐
                    │  User Mode  │  PK 已部署
                    │  SetupMode=0│  ← db/dbx 更新需要 KEK 私鑰簽
                    │  SecureBoot=1│   PK 更新需要 PK 私鑰簽
                    └──────┬──────┘
                           │
                           │  從 BIOS 設定 UI 刪除 PK
                           │  （這個操作本身不需簽章，因為有物理訪問）
                           ▼
                    回到 Setup Mode
```

**Setup Mode 的安全含義**：

1. 全新未設定的系統處於 Setup Mode，Secure Boot 無效。攻擊者若能讓系統回到 Setup Mode（例如從 BIOS UI 刪 PK，或找到 SetupMode 被設為 1 的 bug），就能部署自己的 PK，然後部署自己的 KEK/db，讓自己的 EFI binary 被信任。

2. `SecureBoot` variable（read-only）和 `SetupMode` variable（read-only）這兩個 variable 是 firmware 根據 PK 的存在狀態動態計算的，不能被 OS 直接改寫。

3. BIOS UI 裡的「Restore to Default Keys」或「Delete All Keys」功能，對應的是刪除 NVRAM 裡的 PK，讓系統回到 Setup Mode。這個功能受物理訪問控制（要在 BIOS UI 操作），但某些廠商有遠端管理介面（如 IPMI）可以觸發，就有可能成為攻擊面。

---

## Key Enrollment 流程

實際部署 Secure Boot 金鑰的順序是有規定的，**順序錯誤會導致操作失敗**：

```
正確順序（Setup Mode 下）：
  1. 部署 db（白名單），建立要信任的 EFI binary 的 cert/hash
  2. 部署 dbx（黑名單，可選）
  3. 部署 KEK（Key Exchange Key）
  4. 最後部署 PK → 觸發 Setup Mode → User Mode 轉換

為什麼順序重要？
  ■ 一旦部署了 PK，系統進入 User Mode
  ■ User Mode 下更新 db 需要 KEK 私鑰簽章
  ■ 如果你先部署 PK 才部署 db，你就需要 KEK 私鑰來更新 db
  ■ 在 Setup Mode 下，可以不帶簽章直接部署 db/KEK（但仍需要 EFI_VARIABLE_AUTHENTICATION_2 格式）
```

---

## shim 的角色：MOK 擴展信任鏈

標準 Secure Boot 的問題：Microsoft 持有唯一的 KEK（在主流廠商出貨的系統上），也就是說只有 Microsoft 能更新 db。Linux 發行版想讓自己的 kernel 被信任，有兩個選擇：

1. **請 Microsoft 簽**：Fedora/Ubuntu 的 shim.efi 就是被 "Microsoft Corporation UEFI CA 2011" 簽章，放進 db。但這讓 Linux 生態依賴 Microsoft 的 CA。

2. **用 shim + MOK（Machine Owner Key）**：shim 是一個很小的 first-stage bootloader，它本身被 Microsoft CA 簽，但它有自己的一套金鑰 DB（MOK DB），可以讓使用者/發行版在不需要 Microsoft 的情況下信任自己的 EFI binary。

```
Microsoft CA（在 firmware db 裡）
    │
    │ 簽章
    ▼
shim.efi（Microsoft CA 簽章，firmware 信任它）
    │
    │ shim 有自己的驗簽邏輯，查：
    ├── db（firmware 的原始 db）
    ├── MOK DB（Machine Owner Key Database，存在 NVRAM）
    └── vendor_cert（shim 編譯時燒入的發行版 cert）
    │
    ▼
grubx64.efi（被 shim 信任的 GRUB）
    │
    ├── GRUB 再驗 kernel（vmlinuz）
    └── kernel 再驗 modules（lockdown 模式）
```

**MOK 的 NVRAM variable**：

| Variable | 內容 | 屬性 |
|----------|------|------|
| MokList | 信任的 cert/hash 列表（如發行版 CA）| NV+BS |
| MokListRT | MokList 的 runtime 可見版本 | NV+BS+RT |
| MokNew | 待確認的新 MOK（需要在下次開機時在 MokManager UI 確認）| NV+BS |
| MokSBState | shim 的 Secure Boot 狀態 | NV+BS |

**MOK 的設計哲學**：shim 把「信任哪些 binary 的決定權」從 firmware/Microsoft 層下放到 OS/使用者層，但代價是多了一個需要保護的攻擊面（MokList 是 NV+BS，沒有 AT，OS root 可以改）。這是 Ch 29/30 的攻擊素材之一。

---

## 動手：OVMF secboot + snakeoil key 環境

### 環境確認

```bash
# WSL Ubuntu 22.04
# 確認工具存在
which sbsign sbverify cert-to-efi-sig-list sign-efi-sig-list efi-updatevar
# 若無：
sudo apt install sbsigntool efitools openssl qemu-system-x86 ovmf

# 確認 OVMF secboot 版本存在（帶 Secure Boot 支援的 OVMF）
ls /usr/share/OVMF/OVMF_CODE.secboot.fd
ls /usr/share/OVMF/OVMF_VARS.fd
```

### 步驟一：產生 snakeoil 金鑰對

```bash
mkdir -p ~/secboot-lab && cd ~/secboot-lab

# 產生 Platform Key (PK)
openssl req -newkey rsa:2048 -nodes -keyout PK.key -new -x509 -sha256 \
  -days 3650 -subj "/CN=Test PK/" -out PK.crt

# 產生 KEK
openssl req -newkey rsa:2048 -nodes -keyout KEK.key -new -x509 -sha256 \
  -days 3650 -subj "/CN=Test KEK/" -out KEK.crt

# 產生 db 用的 signing cert（用來簽 EFI binary）
openssl req -newkey rsa:2048 -nodes -keyout DB.key -new -x509 -sha256 \
  -days 3650 -subj "/CN=Test DB/" -out DB.crt
```

### 步驟二：轉換成 EFI Signature List 格式

```bash
# 取得一個 GUID 作為 owner（隨機產生或用固定值）
OWNER_GUID=$(python3 -c "import uuid; print(uuid.uuid4())")
echo "Owner GUID: $OWNER_GUID"

# 轉換 certs 到 EFI Signature List (.esl)
cert-to-efi-sig-list -g "$OWNER_GUID" PK.crt PK.esl
cert-to-efi-sig-list -g "$OWNER_GUID" KEK.crt KEK.esl
cert-to-efi-sig-list -g "$OWNER_GUID" DB.crt DB.esl

# 用 PK 私鑰簽 PK 本身（PK 是用 PK 自簽的）
sign-efi-sig-list -g "$OWNER_GUID" -k PK.key -c PK.crt PK PK.esl PK.auth

# 用 PK 私鑰簽 KEK（更新 KEK 需要 PK 私鑰）
sign-efi-sig-list -g "$OWNER_GUID" -k PK.key -c PK.crt KEK KEK.esl KEK.auth

# 用 KEK 私鑰簽 db（更新 db 需要 KEK 私鑰）
sign-efi-sig-list -g "$OWNER_GUID" -k KEK.key -c KEK.crt db DB.esl DB.auth

ls -la *.auth
# PK.auth KEK.auth DB.auth → 這三個是 EFI_VARIABLE_AUTHENTICATION_2 格式
```

### 步驟三：建立要測試的 EFI binary

```bash
# 方法一：用 OVMF 附帶的 HelloWorld.efi（如果有）
# 方法二：自己寫一個最小的 EFI app（需要 gnu-efi 或 edk2 環境）
# 方法三：用 efitools 的 hello.efi

# 取 efitools 的 hello.efi
dpkg -L efitools | grep hello
# 通常在 /usr/share/doc/efitools/examples/
cp /usr/lib/efitools/x86_64/HelloWorld.efi . 2>/dev/null || \
  cp /usr/share/efitools/x86_64/HelloWorld.efi . 2>/dev/null || \
  echo "找不到，用 efi-readvar 測試替代"

# 簽章（用 DB 私鑰簽 EFI binary）
sbsign --key DB.key --cert DB.crt --output HelloWorld.signed.efi HelloWorld.efi

# 驗章（本地驗，不需要 QEMU）
sbverify --cert DB.crt HelloWorld.signed.efi && echo "驗章通過" || echo "驗章失敗"
sbverify --cert DB.crt HelloWorld.efi && echo "原版通過" || echo "原版（未簽）失敗，預期"
```

### 步驟四：準備 OVMF 環境並 enroll keys

```bash
cd ~/secboot-lab

# 複製 OVMF vars（讓它可寫）
cp /usr/share/OVMF/OVMF_VARS.fd ./OVMF_VARS_lab.fd

# 建立 ESP 目錄結構
mkdir -p esp/EFI/BOOT esp/EFI/tools

# 把 key enrollment 工具和 signed EFI 放進 ESP
cp HelloWorld.signed.efi esp/EFI/BOOT/BOOTX64.EFI       # 主 boot target（已簽）
cp /usr/share/efitools/x86_64/KeyTool.efi esp/EFI/tools/ # key enrollment 用
cp PK.auth KEK.auth DB.auth esp/                          # key files

# 也放一份未簽的，待會用來測試「被擋」
cp HelloWorld.efi esp/EFI/BOOT/UNSIGNED.EFI

# 建立 ESP FAT32 image
truncate -s 64M esp.img
mkfs.vfat esp.img
mcopy -i esp.img -s esp/* ::
# 或用 mtools: mformat -i esp.img ::; mcopy -i esp.img -s esp/* ::

# 啟動 QEMU（Secure Boot 的 OVMF）
qemu-system-x86_64 \
  -machine q35,smm=on \
  -global driver=cfi.pflash01,property=secure,value=on \
  -drive if=pflash,format=raw,unit=0,file=/usr/share/OVMF/OVMF_CODE.secboot.fd,readonly=on \
  -drive if=pflash,format=raw,unit=1,file=OVMF_VARS_lab.fd \
  -drive file=esp.img,format=raw,if=virtio \
  -net none \
  -nographic \
  -serial mon:stdio
```

### 步驟五：在 OVMF 裡 enroll keys（UEFI Shell）

```
# QEMU 啟動後，按 ESC 進 UEFI 設定，或等 UEFI Shell

# 在 UEFI Shell 裡，確認目前在 Setup Mode：
Shell> dmpstore SetupMode
# 應該看到 Value 01（Setup Mode）

Shell> dmpstore SecureBoot
# 應該看到 Value 00（Secure Boot 未啟用）

# 切到 ESP（virtio 通常是 blk0 或 fs0）
Shell> fs0:
Shell> ls

# 用 efi-updatevar 或 KeyTool 部署 keys
# 方法 A：efi-updatevar（若有 efi-updatevar.efi）
Shell> efi-updatevar -e -f DB.auth db
Shell> efi-updatevar -e -f KEK.auth KEK
Shell> efi-updatevar -e -f PK.auth PK    ← 部署 PK 後進入 User Mode

# 方法 B：用 KeyTool.efi（互動式 UI）
Shell> EFI\tools\KeyTool.efi
# 在 UI 裡選 Edit Keys → The db → Enroll New Key → 從 ESP 選 DB.auth
# 依序 db → KEK → PK

# 部署完後確認：
Shell> dmpstore SetupMode
# 應該變成 00（User Mode）

Shell> dmpstore SecureBoot
# 應該變成 01（Secure Boot 已啟用）
```

### 步驟六：觀察驗簽行為

```
# 在 User Mode + Secure Boot = 1 的狀態下重開機

# 情境一：執行已簽章的 EFI
Shell> \EFI\BOOT\BOOTX64.EFI
# 應該正常執行，顯示 HelloWorld

# 情境二：執行未簽章的 EFI
Shell> \EFI\BOOT\UNSIGNED.EFI
# 應該看到：
# Access Denied
# 或：
# Security Violation

# 情境三：從 Boot 選項開機，觀察 BDS 驗簽
# 在 BIOS UI 新增開機選項指向 UNSIGNED.EFI，
# 開機時 BDS 應該顯示 Security Violation 並跳到下一個開機選項
```

### 觀察 Secure Boot 的 log（OVMF debug build）

```bash
# 若想看詳細的 Secure Boot 驗簽 log，用 OVMF debug build：
sudo apt install ovmf    # 通常包含 debug 版本
ls /usr/share/OVMF/OVMF_CODE.fd          # release
ls /usr/share/OVMF/OVMF_CODE.secboot.fd  # secboot release

# debug build 的 log 會顯示：
# [Security] Image is OK: <path>
# [Security] Image is rejected: <path>
# [Secure Boot] Verification: ...
```

---

## 踩雷

1. **OVMF_CODE.secboot.fd 和 OVMF_CODE.fd 不是同一個**：前者是啟用 Secure Boot 支援的版本，後者沒有。用 `fd` 版本怎麼 enroll PK 都沒效果，因為 SecureBoot variable 的值被固定為 0。確認用的是 `.secboot.fd`。

2. **Key 部署順序很嚴格**：必須先 db，再 KEK，最後 PK。先部署 PK 進 User Mode 後，再部署 KEK 就需要 PK 私鑰簽章；再部署 db 就需要 KEK 私鑰簽章。如果順序搞反，會卡在「需要簽章但沒有工具」的死結。

3. **dbx 的 hash 和 cert 都能擋**：新手常以為 dbx 只有 hash。實際上 dbx 裡放一張 cert，等於把所有用那張 cert 簽的 binary 全部撤銷。這是 BootHole 應對方案的爭議所在（Microsoft 的 "Microsoft Corporation UEFI CA 2011" 不能直接撤銷，否則幾乎所有 Linux 開機都會失敗）。

4. **sbverify 本地驗和 OVMF 驗是不同的**：`sbverify --cert DB.crt` 只驗 DB.crt 簽了這個 binary，不驗 dbx。OVMF 的驗簽邏輯是先查 dbx 再查 db。所以本地 sbverify 過了，不代表 OVMF 一定接受（可能 dbx 把它擋下來）。

5. **shim 的 MOK 不受 firmware Secure Boot 保護**：MokList 是 NV+BS（沒有 RT，沒有 AT），OS root 可以修改。如果攻擊者取得 OS root，可以加一個自己的 cert 到 MokList，在下次開機的 MokManager UI 裡「確認」（雖然需要物理在場按確認鍵，但某些實作有繞過方式）。

6. **Authenticode hash 計算要留意 overlay 和 debug 資訊**：PE image 的 Certificate Table 之後若有額外資料（overlay），這些資料不算在 Authenticode hash 裡。某些 bug 利用這個特性在 overlay 藏 payload，而 Authenticode hash 仍然驗通（因為 hash 不涵蓋 overlay）。

---

## 進階延伸

- **edk2 SecurityPkg 的實作**：`SecurityPkg/Library/DxeImageVerificationLib/DxeImageVerificationLib.c` 是 Secure Boot 驗簽的核心，`DxeImageVerificationHandler()` 函式就是那個「先查 dbx、再查 db」的邏輯。搭配 `Pkcs7Verify` 看 CMS 驗簽的細節。

- **Authenticode 的 overlap/gap**：PE image 的 section 之間可能有 gap（未被任何 section 覆蓋的 byte）。Authenticode 規範的處理是把 gap 算入 hash，但某些實作跳過了 gap，造成 hash 不一致的 bug（CVE-2019-1388 等 Windows Authenticode bug 的根源之一）。

- **Secure Boot 驗簽的 timing 攻擊面**：BDS 驗 EFI binary 的流程中，hash 計算和 db 查詢之間是否有 TOCTOU 窗口？理論上 binary 已被載入記憶體，記憶體在 DXE 階段不受 SMM 以外的東西修改，TOCTOU 很難利用。但結合 DMA attack（Ch 12 的 SMM callout 概念）理論上可行。

---

## 動手練習

1. 用本章的 `authenticode_hash()` Python 腳本，對一個已簽的 EFI binary 算 hash，再用 `openssl pkcs7 -in <cert> -print_certs` 提取簽章裡的 hash，兩者對比確認一致。

2. 在 Setup Mode 下，不帶 PK 私鑰，嘗試用 `SetVariable` 直接寫一個假的 PK（自製，沒有 EFI_VARIABLE_AUTHENTICATION_2 wrapping），觀察 OVMF 拒絕的錯誤碼。對比在 User Mode 下同樣操作的結果。

3. 在 enroll 完 db/KEK/PK 後，建立一個 dbx 包含已簽 EFI binary 的 Authenticode hash，用 KEK 私鑰簽 dbx，部署後觀察原本可以開機的已簽 EFI 是否被 dbx 擋下。

---

## 本章重點

- 金鑰階層四層：PK（最高信任根）→ KEK（中間層）→ db（白名單）/ dbx（黑名單）；嚴格單向，私鑰保密是整個鏈的前提
- EFI_VARIABLE_AUTHENTICATION_2 的 TimeStamp 防重放，簽章覆蓋 TS + VarName + GUID + Attr + NewData
- db/dbx 的內容是 EFI_SIGNATURE_LIST，可包含 X.509 cert（批量信任 / 批量撤銷）或 SHA-256 hash（精確控制）
- Authenticode hash 計算：跳過 CheckSum、Certificate Table Data Directory（的值）、和 Certificate Table 本身
- 驗簽決策：dbx 優先，cert chain 驗通且鏈結到 db cert，或 hash 直接在 db → 允許
- Setup Mode（PK 未部署）→ User Mode（PK 已部署）是單向轉換，但 BIOS UI 刪 PK 可逆
- shim 引入 MOK 信任擴展，解除對 Microsoft CA 的直接依賴，但 MokList 沒有 AT 保護

---

## 自我檢核

- [ ] 能畫出 PK→KEK→db/dbx 的金鑰階層圖，說清楚每層誰持有私鑰、誰能更新誰
- [ ] 能解釋 EFI_VARIABLE_AUTHENTICATION_2 的 TimeStamp 欄位如何防重放，以及什麼情況下防重放可能失效
- [ ] 知道 db 裡放 cert 和放 hash 的差異（批量 vs 精確），以及 dbx 為什麼偏好 hash
- [ ] 能說出 Authenticode hash 計算時跳過哪些欄位，並解釋為什麼要跳過 Certificate Table
- [ ] 理解 Setup Mode vs User Mode 的狀態機，知道什麼操作能讓系統回到 Setup Mode
- [ ] 知道 shim 的 MOK 是什麼，以及 MokList 沒有 AT 保護代表的攻擊含義
- [ ] 在 OVMF secboot 環境跑過 key enrollment，實際觀察到未簽 EFI 被 EFI_SECURITY_VIOLATION 擋下

---

## 延伸閱讀

1. **UEFI Specification 2.10, Section 32 "Secure Boot and Driver Signing"**（uefi.org/specifications）
   讀哪裡：Section 32.1-32.4，重點是 32.4 "EFI_VARIABLE_AUTHENTICATION_2" 的格式定義
   學什麼：authenticated variable 的精確格式、TimeStamp 防重放的 spec 要求、Setup Mode / User Mode 的官方定義
   關聯：本章所有機制的一手來源，Ch 29 的類型學分析都需要回來對照 spec

2. **"Microsoft UEFI CA Signing Policy"**（techcommunity.microsoft.com）
   讀哪裡：搜尋 "UEFI CA Signing Requirements" 或 "Secure Boot Windows requirements"
   學什麼：Microsoft 怎麼決定要不要把一個 EFI binary 放進 db（shim 審核流程）、SBAT 的引入背景
   關聯：直接解釋 Ch 30 中 BootHole 後 Microsoft 為什麼不能直接撤 UEFI CA cert 而要引入 SBAT

3. **Peter Jones, "Shim, MOK, and the Secure Boot chain of trust"（2012+系列文章）**（mjg59.dreamwidth.org 或 mjg59.com）
   讀哪裡：Matthew Garrett 的 blog 關於 shim 設計決策的文章（2012-2015 年間），搜 "shim secure boot"
   學什麼：shim 的設計哲學、MOK 為什麼設計成不需要物理重開機確認（MokNew 機制）、以及這個設計帶來的攻擊面
   關聯：直接接 Ch 29 的 T1-T3 類型學在 shim 上的具體體現

→ [下一章](./29-bypass-taxonomy.md)
