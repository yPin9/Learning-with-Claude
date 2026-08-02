# Ch 38 — Measured Boot 全鏈

> **目標**：理解 Measured Boot 的完整量測鏈——從 SRTM/CRTM 起點，走過 PCR[0-7] 的分配規範（誰量測進哪個 PCR），掌握 extend 的累積數學和 TCG event log 格式，釐清 measured boot vs verified/secure boot 的本質差異，再往上接 Linux IMA kernel 量測，以及 DRTM（Intel TXT/AMD SKINIT）的動態量測機制。動手用 swtpm + tpm2_pcrextend 模擬開機各階段量測，讀 event log。

---

## 從 Ch 37 接過來

上一章建立了 TPM 的組件模型：PCR bank、hierarchy、命令結構。這一章把 TPM 放回開機流程裡，回答一個核心問題：

**「開機時，誰測什麼，放進哪個 PCR，這些數字最後用來幹嘛？」**

Measured Boot 的完整回答是：每個開機階段把下一階段的雜湊「延伸進」對應的 PCR，累積成一份不可篡改的開機歷史，再透過 sealed key 或遠端 attestation 把這份歷史轉成有意義的安全保障。

---

## SRTM：靜態信任根量測

**SRTM（Static Root of Trust for Measurement）**：量測從 CPU 重置後第一段執行的程式碼開始，這段程式碼本身不被量測（它是量測的起點），後面每一個階段量測下一個。

```
CPU RESET
  │
  ▼
┌─────────────────────────────────────────────┐
│ CRTM（Core Root of Trust for Measurement）  │
│ = BIOS/UEFI 最初始的不可變程式碼（ROM）      │
│ 通常是 IBB（Initial Boot Block）             │
│ 這段程式碼本身「不被量測」——它是信任起點      │
└────────────────┬────────────────────────────┘
                 │ 量測 → PCR[0]（CRTM version）
                 ▼
            早期 BIOS 初始化
                 │ 量測後續 BIOS code/config → PCR[0,1,2,3]
                 ▼
            Option ROM / 擴充卡 BIOS
                 │ 量測 → PCR[2,3]
                 ▼
            OS Loader（GRUB / bootmgfw.efi）
                 │ 量測 → PCR[4,5]
                 ▼
            OS Kernel
                 │ IMA 量測（可選）→ PCR[10]
                 ▼
            User Space
```

**CRTM 的特殊地位**：CRTM 不被量測，所以它必須是「所有人同意相信的物理起點」。在 dTPM 架構裡，CRTM 通常是 SPI flash 裡最靠近 CPU 重置向量的程式碼。如果攻擊者能竄改 CRTM（Ch 35 的 SPI 竄改），整個 SRTM 的量測鏈就從起點就失去意義——竄改的 CRTM 可以偽造後續所有 PCR 值（TPM 只是被動接受 extend 命令，不驗 extend 的內容是否真實）。

這就是為什麼需要 DRTM（本章後面會講）。

---

## PCR[0-7] 分配：TCG PC Client 規範

TCG PC Client Platform Firmware Profile Specification 定義了哪些東西量測進哪個 PCR：

```
PCR[0]  — CRTM、BIOS code（firmware code）
          量測內容：
            - CRTM 版本字串
            - 所有 BIOS/UEFI 韌體 volume（FV）程式碼
            - 每個 UEFI driver 的 image hash（Secure Boot 相關）
            - Host Platform Manufacturer code

PCR[1]  — Host Platform Configuration
          量測內容：
            - BIOS/UEFI 設定（UEFI variable、NVRAM 設定）
            - 主機板設定資料
            - UEFI 表格（ACPI/SMBIOS）

PCR[2]  — Option ROM Code
          量測內容：
            - Option ROM（PCIe 卡的擴充 BIOS）的程式碼
            - UEFI driver 從外部裝置載入的程式碼

PCR[3]  — Option ROM Configuration and Data
          量測內容：
            - Option ROM 的設定資料

PCR[4]  — IPL Code（Initial Program Loader）= OS Loader
          量測內容：
            - Master Boot Record（MBR）或 EFI Boot Services（在 UEFI 上）
            - OS loader（bootmgfw.efi / grub*.efi）的 image hash
            - 用於 IPL 的 Boot Manager code

PCR[5]  — IPL Configuration and Data
          量測內容：
            - GPT（GUID Partition Table）
            - Boot Manager 設定資料（EFI Boot####, BootOrder variable）
            - MBR partition table

PCR[6]  — State Transitions and Wake Events
          量測內容：
            - S3 resume（從睡眠喚醒）事件
            - Platform wake events（廠商特定）

PCR[7]  — Secure Boot State
          量測內容：
            - Secure Boot 啟用/停用狀態
            - PK、KEK、db、dbx 的內容 hash
            - 每個被驗過的 EFI image 的簽章狀態
```

```
直觀記憶圖：
PCR[0]  韌體程式碼
PCR[1]  韌體設定
PCR[2]  擴充卡程式碼
PCR[3]  擴充卡設定
PCR[4]  OS loader 程式碼  ← BitLocker 最常綁這個
PCR[5]  OS loader 設定
PCR[6]  系統狀態事件
PCR[7]  Secure Boot 狀態  ← Windows 11 強制需要這個 PCR 在 BitLocker 綁定裡
```

**PCR[8-15]**：由 OS 和 bootloader 自由定義，Linux 用 PCR[8-9] 放 kernel commandline 和 initrd，Windows 用 PCR[8-11,12-14]。

**PCR[16]**：Debug PCR，OS 可以 reset，通常用來做實驗。

**PCR[17-23]**：DRTM 保留，只能在 Locality 3/4 被 reset——OS 無法清掉它們。

---

## Extend 運算的精確數學

```
PCR extend 的不可逆累加（單一 bank）：

輸入：
  PCR[i]_old  — 目前的 PCR 值（256 bits for SHA-256）
  measurement — 被量測物件的 hash（同一 bank 的 hash algorithm）

操作：
  PCR[i]_new = SHA256( PCR[i]_old || measurement )
                ↑ concat，不是 XOR
                ↑ SHA256 對 512-bit 輸入

性質：
  - 無法從 PCR[i]_new 倒推 PCR[i]_old 或 measurement（單向）
  - 無法清掉（除非 TPM reset 或 PCR_Reset 在允許的 PCR 上）
  - 確定性：相同的量測序列→ 相同的最終 PCR 值
  - 完整性：任何一個量測改變，最終 PCR 值就不同
```

具體例子（模擬 PCR[0] 的兩次 extend）：

```
初始值：PCR[0] = 0000...0000（32 bytes 全 0）

第一次 extend（CRTM 版本字串 "EDK2 2024"）：
  measurement1 = SHA256("EDK2 2024") = a3f2...（假設值）
  PCR[0] = SHA256( 0000...0000 || a3f2... )
         = 8b91...（新值）

第二次 extend（UEFI firmware volume hash）：
  measurement2 = SHA256( <uefi_fv_binary> ) = 7c4e...（假設值）
  PCR[0] = SHA256( 8b91... || 7c4e... )
         = d2a7...（最終值）

任何 measurement 不同 → 最終 PCR[0] 不同
```

---

## TCG Event Log：量測的完整記錄

PCR 只儲存累積值，你沒辦法從 PCR 值直接知道「哪些東西被量測了」。這份資訊存在 **TCG Event Log** 裡。

Event Log 是一個線性表格，每個 entry 描述一次 extend 事件：

```
TCG Event Log 結構（EFI_TCG2_PROTOCOL 格式，EV_EFI_*）：

Event Entry：
  PCRIndex   (4 bytes)  — 量測進哪個 PCR
  EventType  (4 bytes)  — 事件類型（EV_EFI_VARIABLE_DRIVER_CONFIG、
                            EV_IPL、EV_SEPARATOR、...）
  Digests    (variable) — 每個 hash bank 的摘要（SHA1 + SHA256 + ...）
  EventSize  (4 bytes)  — EventData 長度
  EventData  (variable) — 人類可讀的描述（例如：UEFI variable 名稱、
                          EFI image path、"calling EFI Application"）

Separator Event（EV_SEPARATOR）：
  每個 PCR 在韌體和 OS 移交控制時插入一個 separator，
  用來區分「韌體量測的部分」和「OS 量測的部分」
```

Linux 讓 OS 可以讀 firmware 留下的 event log：

```bash
# 韌體 event log（UEFI 把它傳給 OS 的版本）
cat /sys/kernel/security/tpm0/binary_bios_measurements | hexdump -C | head -60
# 或用工具解析
tpm2_eventlog /sys/kernel/security/tpm0/binary_bios_measurements 2>/dev/null | head -100
```

Event Log 的重要性：
- 重建 PCR 值：把 event log 裡的所有 digest 按順序 extend，最後的值應該和 PCR 一致
- 找出「哪次量測改變了 PCR」：如果 PCR 值不符預期，逐行查 event log 找出哪個 event 不對
- 遠端 attestation 的依據：Verifier 不只看 PCR 值，還要看 event log 驗證量測的「故事」合理

---

## Measured Boot vs Verified/Secure Boot：本質差異

這是本課最常被混淆的概念對，要說清楚：

```
Secure Boot（Verified Boot 的 x86 實作）：
  ─ 每個階段執行前驗章
  ─ 驗失敗 → 停止執行，系統無法繼續開機
  ─ 目標：阻止未簽名或被竄改的程式碼執行
  ─ 強制性：主動阻擋
  ─ 不需要 TPM

Measured Boot：
  ─ 每個階段執行前量測（extend 進 PCR），無論是否合法都繼續執行
  ─ 量測到異常 → 系統仍然開機，但 PCR 值不同
  ─ 目標：記錄開機路徑，讓事後 attestation 或 sealed key 失效
  ─ 強制性：被動記錄
  ─ 需要 TPM（PCR 是 TPM 的組件）
```

```
兩者組合的正確理解：

  開機流程：
    ┌─────────────────────────────────────────────┐
    │  UEFI Secure Boot（Verified Boot）          │
    │  → 在執行前驗章，驗失敗就停               │
    │  → 只讓「已知合法」的程式碼繼續            │
    └──────────────────┬──────────────────────────┘
                       │ 並行發生，不互相干擾
    ┌──────────────────▼──────────────────────────┐
    │  Measured Boot                              │
    │  → 量測每個執行的程式碼                     │
    │  → 即使 Secure Boot 通過，量測仍然記錄      │
    │  → PCR 值反映「實際執行了什麼」             │
    └─────────────────────────────────────────────┘
    
  兩者搭配才是完整的防禦：
    Secure Boot → 阻止已知惡意的東西執行
    Measured Boot → 記錄「什麼確實執行了」供事後審計
    
  只有 Secure Boot 沒有 Measured Boot：
    你知道「執行前有驗章」，但不知道「哪個版本的 bootloader 在跑」
    
  只有 Measured Boot 沒有 Secure Boot：
    惡意 bootloader 可以執行，只是 PCR 值會不同
    → 如果你的 sealed key 綁了 PCR[4]，惡意 bootloader 打不開 BitLocker
    → 但如果沒有 sealed key，惡意 bootloader 照常跑
```

**量測≠阻擋**是最關鍵的認知。Measured Boot 本身不阻止任何東西執行，它的安全保障完全依賴「PCR 值異常時，sealed key 打不開 / 遠端 attestation 拒絕」這個後置機制。

---

## 往上接 IMA：Linux Kernel 層量測

IMA（Integrity Measurement Architecture）是 Linux kernel 的量測子系統，把 Measured Boot 往上延伸到 OS 層：

```
UEFI Measured Boot（量測到 OS loader）
  │
  └─→ PCR[4] = SHA256 of grub*.efi
               PCR[7] = Secure Boot state
  
IMA（啟動後由 kernel 接管量測）：
  → kernel 每次從 disk 讀取並執行 ELF binary / shared library / 設定檔
    都觸發 IMA 量測
  → 量測結果 extend 進 PCR[10]
  → 可以設定 policy：只量測哪些路徑、哪些 label（SELinux label）
```

```bash
# 確認 IMA 是否啟用（需要 kernel 編譯時有 CONFIG_IMA=y）
cat /proc/sys/kernel/ima_measure_pcr_idx    # 應該是 10
cat /sys/kernel/security/ima/runtime_measurements | head -5

# 看 IMA 量測過哪些檔案
cat /sys/kernel/security/ima/ascii_runtime_measurements | head -20
# 格式：PCR# template-hash template-name file-hash filename
```

IMA + Measured Boot 的組合讓你可以：
- 在遠端 attestation 裡確認「這台機器執行的不只開機鏈正確，連執行的 binary 都是預期的版本」
- 把 PCR[10] 的 sealed key 綁定讓「執行了未預期的 binary 就打不開這把 key」

---

## DRTM：動態信任根量測

SRTM 的根本問題：量測從 CRTM 開始，如果韌體本身被竄改（BootHole、BlackLotus），整個 SRTM 量測鏈從起點就可能被偽造。

**DRTM（Dynamic Root of Trust for Measurement）**：利用 CPU 的特殊指令，在任意時間點重新建立一個乾淨的量測起點，不依賴 BIOS/UEFI 的 SRTM 鏈：

```
Intel TXT（Trusted Execution Technology）：

OS 已啟動後，執行 SENTER 指令
  │
  ▼
CPU 硬體行為：
  1. 停止所有其他 CPU core
  2. Flush cache
  3. 用硬體（不用 BIOS）載入並驗證 SINIT ACM
  4. SINIT ACM 重置 PCR[17-23]（Locality 3，OS 無法清掉這些 PCR）
  5. SINIT 量測 MLE（Measured Launch Environment）→ PCR[17,18]
  6. 把控制權交給 MLE（通常是 Hypervisor 或 SGX-like 隔離環境）
  
結果：PCR[17-23] 反映 MLE 的狀態，
      與 BIOS 的 SRTM（PCR[0-7]）完全獨立
      即使 BIOS 被打爛，DRTM 的 PCR 仍然可信
```

```
AMD SKINIT（Secure Kernel Init and Late Launch）：

類似 SENTER，但更簡單：
  執行 SKINIT 指令 + SLB（Secure Loader Block）位址
  │
  ▼
  CPU 停止其他 core，flush cache
  CPU 硬體計算 SLB 的 hash → extend 進 PCR[17]（Locality 4）
  SLB 從乾淨狀態開始執行（建立 Dynamic OS 或 Hypervisor）
```

```
DRTM vs SRTM 的 PCR 分工：

SRTM（PCR[0-7]）：
  韌體負責量測，OS 可以讀，代表「開機路徑的歷史」
  → 如果 BIOS 被 compromised，這些值可能不可信
  
DRTM（PCR[17-23]）：
  CPU 硬體負責觸發，只有 Locality 3/4 才能 reset
  OS 無法清掉（PCR_Reset 需要 locality 3/4 才有效）
  → 不依賴 BIOS 的誠實性，提供更強的信任保障
```

**DRTM 的實際部署現狀**：TXT/SKINIT 需要 CPU 支援、BIOS 啟用、SINIT ACM 匹配，部署複雜。Windows BitLocker 主要用 SRTM；VMware 的 vTPM、Xen 的 measured launch 才需要 DRTM。現實中多數 PC 的 PCR[17-23] 都是 full 0（從未用 DRTM）。

---

## 動手：模擬開機量測序列

### 步驟 1：確認 swtpm 在跑

```bash
# 若上一章的 swtpm 已停了，重新啟動
pgrep swtpm || swtpm socket \
    --tpmstate dir=/tmp/swtpm-state \
    --tpm2 \
    --ctrl type=tcp,port=2322 \
    --server type=tcp,port=2321 \
    --flags not-need-init,startup-clear \
    --daemon

export TPM2TOOLS_TCTI="swtpm:host=127.0.0.1,port=2321"

# 確認 PCR 全 0 的初始狀態
echo "=== 初始 PCR 狀態 ==="
tpm2_pcrread sha256:0,1,2,3,4,5,6,7
```

### 步驟 2：模擬 CRTM 量測（PCR[0]）

```bash
# CRTM 版本字串的量測
CRTM_VERSION="EDK2_2024_Q1"
CRTM_HASH=$(echo -n "$CRTM_VERSION" | sha256sum | awk '{print $1}')

echo "CRTM version string: $CRTM_VERSION"
echo "SHA256 of CRTM version: $CRTM_HASH"

# Extend PCR[0]（模擬 CRTM EV_S_CRTM_VERSION event）
tpm2_pcrextend 0:sha256=$CRTM_HASH

echo "=== PCR[0] after CRTM version extend ==="
tpm2_pcrread sha256:0
```

### 步驟 3：模擬 UEFI Firmware Volume 量測

```bash
# 模擬 UEFI FV hash（用一個假的 256 bytes 代表 firmware volume）
python3 -c "import os; open('/tmp/fake_fv.bin','wb').write(os.urandom(4096))"
FV_HASH=$(sha256sum /tmp/fake_fv.bin | awk '{print $1}')
echo "Fake FV hash: $FV_HASH"

# Extend PCR[0] 再一次（UEFI firmware code）
tpm2_pcrextend 0:sha256=$FV_HASH

echo "=== PCR[0] after FV extend ==="
tpm2_pcrread sha256:0
```

### 步驟 4：模擬 Secure Boot 狀態量測（PCR[7]）

```bash
# 模擬量測 Secure Boot 啟用狀態
SECBOOT_ENABLED="SecureBoot=01"
SB_HASH=$(echo -n "$SECBOOT_ENABLED" | sha256sum | awk '{print $1}')

tpm2_pcrextend 7:sha256=$SB_HASH
echo "Measured Secure Boot enabled state → PCR[7]"

# 模擬量測 db（signature database）內容
DB_CONTENT="MicrosoftDB2024"
DB_HASH=$(echo -n "$DB_CONTENT" | sha256sum | awk '{print $1}')

tpm2_pcrextend 7:sha256=$DB_HASH
echo "Measured db content → PCR[7]"

echo "=== PCR[7] after Secure Boot state extend ==="
tpm2_pcrread sha256:7
```

### 步驟 5：模擬 OS Loader 量測（PCR[4]）

```bash
# 模擬 grub*.efi 的 hash
python3 -c "import os; open('/tmp/fake_grub.efi','wb').write(os.urandom(512*1024))"
GRUB_HASH=$(sha256sum /tmp/fake_grub.efi | awk '{print $1}')
echo "Fake grub.efi hash: $GRUB_HASH"

# Extend PCR[4]（OS loader = IPL code）
tpm2_pcrextend 4:sha256=$GRUB_HASH

echo "=== PCR[4] after grub.efi extend ==="
tpm2_pcrread sha256:4
```

### 步驟 6：觀察所有 PCR 的最終狀態

```bash
echo "=== 模擬開機序列後所有 PCR 值 ==="
tpm2_pcrread sha256:0,1,2,3,4,5,6,7

echo ""
echo "=== 解讀 ==="
echo "PCR[0]: 有值（量測了 CRTM 版本 + UEFI FV）"
echo "PCR[1]: 全 0（本次模擬未量測韌體設定）"
echo "PCR[4]: 有值（量測了 grub.efi）"
echo "PCR[7]: 有值（量測了 Secure Boot 狀態 + db 內容）"
```

### 步驟 7：驗證「改了量測值 PCR 就不同」

```bash
# 保存目前 PCR[4] 的值
PCR4_BEFORE=$(tpm2_pcrread sha256:4 | grep "4 :" | awk '{print $3}')
echo "PCR[4] before: $PCR4_BEFORE"

# 模擬「換了一個不同的 grub.efi」
python3 -c "import os; open('/tmp/fake_grub_tampered.efi','wb').write(os.urandom(512*1024))"
TAMPERED_HASH=$(sha256sum /tmp/fake_grub_tampered.efi | awk '{print $1}')

tpm2_pcrextend 4:sha256=$TAMPERED_HASH

PCR4_AFTER=$(tpm2_pcrread sha256:4 | grep "4 :" | awk '{print $3}')
echo "PCR[4] after extend with tampered grub: $PCR4_AFTER"
echo "→ 值不同，不同的 OS loader 會讓 BitLocker/sealed key 打不開"
```

### 步驟 8：模擬讀 event log（基礎格式）

```bash
# swtpm 本身不維護 event log（那是 UEFI 的工作），
# 但我們可以手動建立一個最小的 event log 結構來理解格式

python3 << 'EOF'
import struct, hashlib

def sha256(data):
    return hashlib.sha256(data).digest()

# TCG EFI Protocol Spec 的 Event Log 格式（簡化）
# 每個 entry：PCRIndex(4) + EventType(4) + SHA256Digest(32) + EventSize(4) + EventData(N)

EV_EFI_VARIABLE_DRIVER_CONFIG = 0x80000001
EV_S_CRTM_VERSION            = 0x00000008
EV_IPL                       = 0x0000000D
EV_SEPARATOR                 = 0x00000004

def make_event(pcr_index, event_type, measurement, event_data_str):
    data = event_data_str.encode('utf-8')
    digest = sha256(measurement.encode('utf-8') if isinstance(measurement, str) else measurement)
    entry = struct.pack('<II', pcr_index, event_type)
    entry += digest
    entry += struct.pack('<I', len(data))
    entry += data
    return entry, digest.hex()

events = [
    (0, EV_S_CRTM_VERSION,            "EDK2_2024_Q1",    "CRTM Version: EDK2_2024_Q1"),
    (7, EV_EFI_VARIABLE_DRIVER_CONFIG, "SecureBoot=01",   "SecureBoot EFI Variable"),
    (7, EV_EFI_VARIABLE_DRIVER_CONFIG, "MicrosoftDB2024", "db EFI Variable"),
    (4, EV_IPL,                        "grub.efi_hash",   "EFI Boot Application: \\grub.efi"),
    (4, EV_SEPARATOR,                  "separator4",      "SEPARATOR"),
]

print(f"{'PCR':<5} {'Type':<35} {'Digest[:16]':<18} EventData")
print("-" * 85)
for pcr, etype, measurement, desc in events:
    _, digest = make_event(pcr, etype, measurement, desc)
    type_names = {
        EV_S_CRTM_VERSION: "EV_S_CRTM_VERSION",
        EV_EFI_VARIABLE_DRIVER_CONFIG: "EV_EFI_VARIABLE_DRIVER_CONFIG",
        EV_IPL: "EV_IPL",
        EV_SEPARATOR: "EV_SEPARATOR",
    }
    print(f"PCR[{pcr}] {type_names[etype]:<35} {digest[:16]+'...':<18} {desc}")

print()
print("Event Log 讓你知道「哪個 extend 事件改了哪個 PCR 的值」")
print("實際 UEFI 的 event log 比這個複雜，但結構相同")
EOF
```

---

## 踩雷

1. **Measured Boot 不阻止惡意 bootloader 執行**：這是最常見的誤解。惡意的 GRUB 一樣能開機，只是 PCR[4] 值會不同。如果你沒有把 sealed key 綁 PCR 或部署 remote attestation，measured boot 等於沒有防護效果——只是有一份詳細的「入侵記錄」。

2. **PCR[0] 不等於「整個 UEFI firmware 的 hash」**：PCR[0] 是多次 extend 的累積，量測了 CRTM 版本 + 每個 UEFI FV + Boot Manager code + 其他 firmware 元件。一個 UEFI 版本更新（哪怕只更新一個 module）就會改變 PCR[0]。BitLocker 不綁 PCR[0] 正是因為這樣——正常更新也會讓 key unseal 失敗。

3. **PCR[7] 的內容和 Secure Boot db 強耦合**：每次 db / dbx 更新，PCR[7] 值就變。如果你把 sealed key 綁 PCR[7]，打了 Microsoft dbx 更新（每個週二都可能有），key 就打不開。這就是為什麼 BitLocker 在 Windows 更新前要先 suspend（`manage-bde -protectors -disable`）。

4. **Event Log 和 PCR 值可以不一致**：Event Log 是軟體（UEFI）維護的，PCR 是 TPM 硬體維護的。如果 UEFI 有 bug 或被 compromise，它可以把「假的 event log」傳給 OS（PCR 值因為是 TPM 的所以不能假），但 event log 顯示的是一套、PCR 的實際值是另一套。遠端 attestation 的 Verifier 必須驗「replaying event log 計算出的 PCR 值等於 TPM quote 裡的 PCR 值」才能確信 event log 沒被偽造。

5. **DRTM 在現代 PC 幾乎沒人啟用**：Intel TXT 需要 BIOS 和 CPU 都支援，且 SINIT ACM 必須是 Intel 對應平台的版本。查詢：`dmesg | grep tpm | grep -i txt`，大多數機器沒有輸出。AMD SKINIT 同理。不要把 DRTM 當成「現有防護」分析——它是「理論上更強但實際上幾乎沒部署」的機制。

6. **swtpm 的 PCR extend 沒有 locality 限制**：真實 dTPM 只有在對應 locality 才能 reset PCR[17-23]，swtpm 預設允許所有操作（因為是測試工具）。不要用 swtpm 的行為推論「實際 TPM 允不允許這個操作」。

7. **IMA 和 Measured Boot 的 PCR 不重疊但有依賴**：IMA 用 PCR[10]，UEFI measured boot 用 PCR[0-7]。但 IMA 的量測只在 kernel 啟動後才開始，kernel 本身是由 bootloader 量測（PCR[8,9] 或 EFI stub 的邏輯）。鏈的連續性：PCR[4] 量測 bootloader → bootloader 量測 kernel（PCR[8,9]）→ kernel 啟動 IMA 量測（PCR[10]）。

---

## 進階延伸

- **TCG Canonical Event Log 格式（CEL）**：TCG 在 2021 年發布了新的 canonical event log 格式（CEL-JSON 和 CEL-CBOR），比舊的 binary event log 更容易解析和驗證。Linux 的 `tpm2-tools` 的 `tpm2_eventlog` 工具支援解析。如果你要自己寫 attestation verifier，研究 CEL 格式比研究舊格式更值得。

- **Measured Boot 在容器/VM 的延伸**：Kata Containers 和 Google Confidential Computing 把 measured boot 延伸到 VM 層：VM 啟動時的 vTPM 用 PCR 量測 guest kernel 和 container image，讓 workload attestation 成為可能。研究 AMD SEV-SNP 的 launch measurement（一種 DRTM 的 VM 版本）是理解這個方向最直接的路徑。

- **PCR Policy 的攻擊面**：如果 BitLocker 只綁 PCR[7]（Secure Boot 狀態），但 PCR[7] 的量測依賴 UEFI 誠實地量測 db/dbx，那麼一個有 UEFI write 能力的攻擊者（Ch 35 的 SPI 竄改）可以讓 UEFI 偽造 PCR[7] 的 extend，讓 sealed key 可以在「看起來 Secure Boot 啟用但實際上已被竄改」的狀態下打開。這是 PCR 綁定策略設計時的核心考量：你選擇綁的 PCR 必須是「攻擊者無法在不被偵測的情況下操控其 extend 序列」的 PCR。

---

## 動手練習

完成以下步驟並回答問題：

1. 跑完本章的 swtpm 模擬序列，記錄最終 PCR[0]、PCR[4]、PCR[7] 的 SHA-256 值。

2. 把「模擬開機序列」跑兩遍（先 kill swtpm 用 `--flags startup-clear` 重啟讓 PCR 歸零），確認兩次跑出來的 PCR 值完全相同——理解「確定性」是 sealed key 能成立的前提。

3. 改變第二遍的「grub hash」（換一個不同的假 binary），觀察 PCR[4] 變了，但 PCR[0] 和 PCR[7] 不變——這模擬「OS loader 被竄改，但韌體本身和 Secure Boot 設定沒動」的情境。

4. 思考題：假設攻擊者有能力在 PCR extend 之後、TPM 把 PCR 值回傳給 attestation verifier 之前，修改 PCR 值，他能做到嗎？（提示：PCR 在 TPM 晶片內部，CPU 只能透過 `tpm2_pcrextend` 命令與 TPM 通訊——他可以改 TPM 命令的內容嗎？改了 TPM 會怎樣？）

5. （進階）如果你的 Linux WSL 有 `/sys/kernel/security/tpm0/binary_bios_measurements`，用 `tpm2_eventlog` 解析它，找出哪個 event 量測了 OS loader（EV_IPL 類型），記錄它量測的 digest。

---

## 本章重點

- SRTM 從 CRTM（第一段不可更改的開機程式碼）開始量測，CRTM 本身不被量測——它是信任起點
- PCR[0-7] 按 TCG PC Client 規範分配：[0]韌體程式碼、[1]韌體設定、[2-3]Option ROM、[4-5]OS loader、[6]狀態事件、[7]Secure Boot 狀態
- Extend = `SHA256(PCR_old || new_measurement)`，不可逆、累積、確定性
- TCG Event Log 是 PCR 值的「說明書」，記錄每次 extend 的事件類型和量測對象；遠端 attestation 必須同時驗 PCR quote 和 event log
- **Measured Boot 不阻止執行**，只記錄；安全保障依賴後置的 sealed key unseal 失敗或 attestation 拒絕
- IMA 把量測延伸到 kernel 層（PCR[10]），量測每個被執行的 binary
- DRTM（Intel TXT SENTER / AMD SKINIT）繞過 BIOS 建立獨立量測起點，PCR[17-23] 只有 Locality 3/4 才能 reset，OS 無法清掉

---

## 自我檢核

- [ ] 能說出 CRTM 是什麼，以及為什麼它「不被量測」但卻是整個 SRTM 信任鏈的基礎
- [ ] 能默背 PCR[0-7] 的分配（韌體程式碼/設定/Option ROM/OS loader/狀態/Secure Boot）
- [ ] 能寫出 PCR extend 的數學公式並解釋「不可逆」和「確定性」各自的意義
- [ ] 能清楚說出 Measured Boot 和 Secure Boot 的本質差異，以及「量測≠阻擋」的含義
- [ ] 知道 TCG Event Log 的作用，以及為什麼 Verifier 需要同時驗 PCR quote 和 event log
- [ ] 能解釋 IMA 的量測在哪個 PCR，以及和 UEFI measured boot 的銜接點
- [ ] 能說出 DRTM 解決了什麼問題，以及為什麼 PCR[17-23] 比 PCR[0-7] 更不容易被偽造
- [ ] 能用 swtpm + tpm2_pcrextend 模擬開機量測序列，解讀各 PCR 的值

---

## 延伸閱讀

1. **TCG PC Client Platform Firmware Profile Specification（最新版）** — Trusted Computing Group
   讀哪裡：Section 3（Architecture）和 Section 10（Measuring Events），特別是 Table 1（PCR Usage）
   學什麼：PCR[0-7] 的完整分配規範、每種 event type 的語義、EV_SEPARATOR 在韌體/OS 移交時的作用、TCG Event Log 的 binary 格式定義
   關聯：本章所有 PCR 分配的直接來源，Ch 39（sealed key 綁哪個 PCR）和 Ch 43（遠端 attestation verifier 驗 event log）都基於這份規範

2. **"Measured Boot, Verified Boot, and Attestation" — Google Cloud Blog（2021）**（cloud.google.com/security/resources/）
   讀哪裡：技術部落格文章，重點看「Why Measured Boot Alone Is Not Enough」和「Combining Verified and Measured Boot」兩節
   學什麼：Google 從雲端基礎設施角度解釋 measured boot 的限制——不阻擋惡意執行、需要 attestation 後置機制——以及 Shielded VM 如何結合 Secure Boot + vTPM + Attestation 實現完整的開機完整性保護
   關聯：直接補充本章「量測≠阻擋」和「往上接 attestation」的部分，也是 Ch 43 遠端 attestation 實作的前導

3. **"Defeating Measured Boot Using Rowhammer" — Kovah, Wojtczuk（Black Hat 2015）**（slides 可在 BlackHat 官網找）
   讀哪裡：投影片全部，重點看 Section 3（PCR predict attack）和 Section 4（Rowhammer on TPM memory）
   學什麼：展示兩種攻擊 measured boot 的路徑——預測 PCR 值從而提前 seal key 使其通過錯誤的 PCR（prediction attack），以及用 Rowhammer 攻擊 TPM 的 DRAM（若 fTPM 把 PCR 存在 DRAM）；和本章的「DRTM PCR 不可清」和 Ch 40 的「TPM 攻擊」直接對應
   關聯：把本章的 measured boot 理論連接到具體的攻擊路徑，是最好的「防禦設計有什麼漏洞」入門材料

→ [下一章](./39-sealed-keys.md)
