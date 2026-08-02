# Ch 23 — UEFI 韌體解析：UEFITool 與 FV/FFS

> **目標**：從拿到的 ROM image 一路拆到單個 DXE 模組的 EFI binary，理解 Firmware Volume/File System 的層次結構，掌握 UEFITool（GUI + CLI）和 `uefi_firmware` Python lib 的使用，為後面 Ghidra 逆向（Ch 24）做好前置。

Ch 22 解決「怎麼拿到 image」，本章解決「拿到了怎麼拆」。UEFI ROM 不是單個 ELF，它是多個壓縮過的 Firmware Volume 疊在 SPI 佈局上的嵌套結構，每層都有獨立的 GUID 索引。不懂這個結構就沒辦法在裡面找 SMM handler、找簽章驗證邏輯、找後門。

---

## 複習：FV / FFS 結構

這裡快速複習 Ch 6 的核心概念，以解析工具的視角重新理解。

### SPI flash 佈局

```
SPI Flash（典型 16MB，從 offset 0 開始）：
┌──────────────────────────────────────────────┐  ← 0x0000000
│  Flash Descriptor（4KB）                     │  Intel ME 定義的描述符
│  Flash regions 的邊界 + master access rights  │
├──────────────────────────────────────────────┤  ← 0x0001000
│  Intel ME Region（因平台而異，通常 2-8MB）   │  ME 固件，不透明 binary
├──────────────────────────────────────────────┤
│  GbE Region（可選，128KB）                   │  網卡 MAC + NVM
├──────────────────────────────────────────────┤
│  BIOS Region（剩下的空間，通常 8-12MB）      │  UEFI firmware 在這裡
│  ┌────────────────────────────────────────┐  │
│  │ Firmware Volume 0 (FV_BB / SEC stage) │  │
│  ├────────────────────────────────────────┤  │
│  │ Firmware Volume 1 (FV_MAIN / DXE)     │  │
│  ├────────────────────────────────────────┤  │
│  │ Firmware Volume 2 (FV_MAINFV2 / more) │  │
│  ├────────────────────────────────────────┤  │
│  │ NVRAM Region（UEFI Variables）         │  │
│  └────────────────────────────────────────┘  │
└──────────────────────────────────────────────┘  ← 0xFFFFFF
```

### Firmware Volume（FV）

每個 FV 以 `EFI_FIRMWARE_VOLUME_HEADER` 開頭：

```c
// UEFI Spec §3.3（EFI_FIRMWARE_VOLUME_HEADER，理論，勿直接 memcpy 真 image）
typedef struct {
    UINT8     ZeroVector[16];       // 通常全 0
    EFI_GUID  FileSystemGuid;       // EFI_FIRMWARE_FILE_SYSTEM2_GUID 最常見
    UINT64    FvLength;             // 整個 FV 的大小
    UINT32    Signature;            // '_FVH'（0x4856465F）
    EFI_FVB_ATTRIBUTES_2 Attributes;
    UINT16    HeaderLength;
    UINT16    Checksum;
    UINT16    ExtHeaderOffset;
    UINT8     Reserved[1];
    UINT8     Revision;
    EFI_FV_BLOCK_MAP_ENTRY BlockMap[1]; // 可變長度
} EFI_FIRMWARE_VOLUME_HEADER;
```

Magic signature `_FVH`（`0x5F 0x46 0x56 0x48`）是你在 hex editor 裡認出 FV 的標誌。

### Firmware File System（FFS）

FV 內部是 FFS：一堆 `EFI_FFS_FILE_HEADER` 開頭的 file，每個 file 包含一個 UEFI 模組（DXE driver、PEIM、protocol、SEC binary 等）。

```
Firmware Volume 內部：
  [EFI_FFS_FILE_HEADER_0]  ← GUID + type + size + state
    [EFI_COMMON_SECTION_HEADER]  ← section type
      [section data]             ← DXE_DRIVER binary（可能還有壓縮）
  [EFI_FFS_FILE_HEADER_1]  ...
  ...
```

FFS file GUID 是找模組的主要索引。每個 DXE driver 都有唯一 GUID，EDK2 source 和 CHIPSEC 的 guid database 都有記錄。

### Section 類型

```
EFI_SECTION_TYPE 常見值：
  0x01  EFI_SECTION_COMPRESSION      壓縮（通常是 LZMA，需要解壓）
  0x02  EFI_SECTION_GUID_DEFINED     GUID 定義的 section（Intel Tiano 壓縮）
  0x10  EFI_SECTION_PE32             PE32 executable（DXE driver 主體）
  0x12  EFI_SECTION_TE               TE（Terse Executable）格式（見下面）
  0x13  EFI_SECTION_DXE_DEPEX        DXE 依賴表達式
  0x15  EFI_SECTION_USER_INTERFACE   模組名稱字串
  0x19  EFI_SECTION_RAW              原始資料
```

### TE vs PE 格式

這個差異在逆向時很常遇到，要記住：

```
PE32（標準 Windows PE）：
  [DOS header: 0x40 bytes]
  [PE header: ~0x18 bytes]
  [Optional header: ~0xF0 bytes]
  [Section table]
  [Section data ...]

TE（Terse Executable）：
  [TE header: 0x28 bytes]  ← 精簡版，去掉了 DOS stub 和 NT headers
  [Section data ...]

TE header：
  UINT16 Signature;         // 'VZ'（0x5A56）
  UINT16 Machine;           // 0x8664（x64）或 0x014C（x86）
  UINT8  NumberOfSections;
  UINT8  Subsystem;
  UINT16 StrippedSize;      // 從 PE 轉 TE 時移除了多少 bytes（重要：影響 load address 計算）
  UINT32 AddressOfEntryPoint;
  UINT32 BaseOfCode;
  UINT64 ImageBase;
```

TE 的 `ImageBase` 在加載到記憶體前沒有意義，逆向時需要根據 FV 佈局計算實際 load address，這是 Ghidra 載入 TE 模組的常見痛點（見 Ch 24）。

---

## UEFITool

UEFITool 是目前最完整的 UEFI firmware 解析工具，有 GUI（Qt）和 CLI（UEFIExtract）兩個版本。

### 取得與安裝

```bash
# GitHub release 頁面有預編譯版本
# https://github.com/LongSoft/UEFITool/releases

# Linux（WSL）：下載 UEFIExtract（CLI 版本）
wget https://github.com/LongSoft/UEFITool/releases/download/A68/UEFIExtract_NE_A68_linux_x86_64.zip
unzip UEFIExtract_NE_A68_linux_x86_64.zip
chmod +x UEFIExtract
mv UEFIExtract /usr/local/bin/

# 驗證
UEFIExtract --help
```

注意版本分歧：
- **UEFITool 0.x**：舊版，支援 Section 修改（用於 patch）
- **UEFITool NE（New Engine）A 系列**：新版，解析更正確，但移除了部分修改功能

研究用途兩個都裝，解析用 NE，patch 用 0.x。

### UEFIExtract CLI 用法

```bash
# 對整個 ROM 做 dump（提取所有模組）
UEFIExtract /usr/share/ovmf/OVMF_CODE.fd

# 輸出放在 OVMF_CODE.fd.dump/
ls OVMF_CODE.fd.dump/

# 典型輸出結構：
# OVMF_CODE.fd.dump/
#   Volume_0_GUID.../            ← 第一個 FV
#     File_GUID_xxx.../          ← FFS file（一個 DXE module）
#       Section_0_PE32.efi       ← 實際 EFI binary
#       Section_1_DXE_DEPEX.bin  ← 依賴表
#       Section_2_UI.txt         ← 模組名稱

# 用 all 選項輸出所有 section
UEFIExtract /usr/share/ovmf/OVMF_CODE.fd all

# 用 file 選項只提取特定 GUID 的模組
# 例如提取 DxeCore（GUID: D6A2CB7F-6A18-4E2F-B43B-9920A733700A）
UEFIExtract /usr/share/ovmf/OVMF_CODE.fd D6A2CB7F-6A18-4E2F-B43B-9920A733700A
```

### GUID 對照

UEFI 模組的 GUID 在以下地方有記錄：

```bash
# EDK2 source 裡（如果你有 clone）
grep -r "DxeCore" edk2/MdeModulePkg/*.dec

# CHIPSEC 的 GUID 資料庫
# https://github.com/chipsec/chipsec/blob/main/chipsec/hal/uefi_common.py
# 搜尋 EFI_GUID_MAP

# UEFITool NE 內建 GUID 資料庫（解析時自動顯示名稱）

# 常用 GUID 速查：
# D6A2CB7F-6A18-4E2F-B43B-9920A733700A  DxeCore
# A3527D16-E6CC-4AA7-A1B0-B28C6F38C7EF  SecurityStub
# B601F8C4-43B7-4784-95B1-F4226CB40CEE  RuntimeDxe
# 13AC6DD1-73D0-11D4-B06B-00AA00BD6DE7  EFI_SMM_BASE_PROTOCOL (old)
```

---

## python uefi_firmware（真跑）

`uefi_firmware` 是純 Python 的 UEFI 解析庫，適合腳本自動化和快速分析。

### 安裝

```bash
pip3 install uefi_firmware
# 驗證
python3 -c "import uefi_firmware; print(uefi_firmware.__version__)"
```

### 真跑：對 OVMF 解析

```bash
# 方法一：CLI 工具 uefi-firmware-parser
uefi-firmware-parser -b /usr/share/ovmf/OVMF_CODE.fd

# 輸出（節錄）：
# Found 2 Firmware Volumes
# Firmware Volume: guids/EDKII_FIRMWARE_FILE_SYSTEM3_GUID (offset 0x0, length 0xE0000)
# Firmware Volume: guids/EDKII_FIRMWARE_FILE_SYSTEM2_GUID (offset 0xE0000, length 0x2FF000)
#   Found 89 Firmware Files

# 提取所有模組（-e 選項）
mkdir -p /tmp/ovmf_uefi_fw
uefi-firmware-parser -e /tmp/ovmf_uefi_fw -b /usr/share/ovmf/OVMF_CODE.fd

# 查看提取出的模組
ls /tmp/ovmf_uefi_fw/
find /tmp/ovmf_uefi_fw/ -name "*.efi" | head -20
```

### Python API 腳本

```python
#!/usr/bin/env python3
"""
解析 OVMF 並列出所有 DXE 模組 GUID + 名稱
在 WSL 下可以直接執行：
  python3 /tmp/parse_ovmf.py
"""
import uefi_firmware
import os

OVMF_PATH = "/usr/share/ovmf/OVMF_CODE.fd"

with open(OVMF_PATH, "rb") as f:
    data = f.read()

print(f"[*] Loaded {len(data):,} bytes from {OVMF_PATH}")

parser = uefi_firmware.AutoParser(data)
result = parser.parse()

if not result:
    print("[!] Parse failed")
    exit(1)

def walk_firmware(obj, depth=0):
    indent = "  " * depth
    # 印出有 GUID 的節點
    if hasattr(obj, 'guid') and obj.guid:
        name = getattr(obj, 'name', '')
        guid_str = str(obj.guid).upper()
        print(f"{indent}[{obj.__class__.__name__}] {guid_str}  {name}")

    # 遞迴子節點
    children = getattr(obj, 'objects', []) or []
    for child in children:
        walk_firmware(child, depth + 1)

walk_firmware(parser)
```

```bash
# 執行
python3 /tmp/parse_ovmf.py 2>&1 | head -50
```

預期輸出（節錄）：

```
[*] Loaded 2,097,152 bytes from /usr/share/ovmf/OVMF_CODE.fd
[FirmwareVolume] 9E21FD93-9C72-4C15-8C4B-E77F1DB2D792
  [FirmwareFile] DF1CCEF6-F301-4A63-9661-FC6030DCC880
  [FirmwareFile] A3527D16-E6CC-4AA7-A1B0-B28C6F38C7EF
  [FirmwareFile] D6A2CB7F-6A18-4E2F-B43B-9920A733700A  DxeCore
  ...
```

---

## fiano/utk（對照工具）

Google 開發的 fiano 套件提供 Go 語言的 UEFI 解析工具，其中 `utk` 是最易用的 CLI：

```bash
# 安裝（需要 Go）
go install github.com/linuxboot/fiano/cmds/utk@latest

# 基本使用
utk /usr/share/ovmf/OVMF_CODE.fd dump | head -100
utk /usr/share/ovmf/OVMF_CODE.fd find DxeCore
utk /usr/share/ovmf/OVMF_CODE.fd extract DxeCore ./DxeCore.efi
```

fiano/utk 的優勢：對 NVRAM region 的解析比 UEFITool 更好，適合分析 UEFI variable 存儲格式（見 [Ch 5](./05-uefi-variable-nvram-attacks.md)）。

---

## 抽出 DXE 模組與 GUID 對照

實際研究中最常做的操作：找到特定功能的 DXE driver，把它抽出來丟進 Ghidra。

### 依功能找模組

```bash
# 目標：找 SmmSwDispatch2 相關模組（Ch 13 的逆向起點）

# 方法一：UEFIExtract + grep GUID
UEFIExtract target_bios.bin all
find target_bios.bin.dump/ -name "*.txt" | xargs grep -l "SmmSwDispatch\|EE9B8D90"

# 方法二：uefi_firmware 搜字串
uefi-firmware-parser -b target_bios.bin -o /tmp/dump/
find /tmp/dump/ -name "*.efi" -exec strings {} \; | grep -i "swdispatch\|smi"

# 方法三：binwalk + grep（粗暴但快）
binwalk -e target_bios.bin
find _target_bios.bin.extracted/ -exec strings {} \; 2>/dev/null | grep -i "smm\|swdispatch" | sort -u
```

### GUID 資料庫整合

建立自己的 GUID→名稱映射：

```python
#!/usr/bin/env python3
"""
從 EDK2 .dec 和 .inf 檔案建立 GUID 資料庫
假設你已 clone 了 edk2 到 ~/edk2/
"""
import re
import glob

guid_db = {}
# 掃描所有 .dec 檔案
for dec_file in glob.glob('/path/to/edk2/**/*.dec', recursive=True):
    try:
        content = open(dec_file).read()
        for m in re.finditer(
            r'(\w+)\s*=\s*\{\s*0x([0-9A-Fa-f]+),\s*0x([0-9A-Fa-f]+)',
            content
        ):
            name = m.group(1)
            g1 = m.group(2).zfill(8)
            guid_db[g1.upper()] = name
    except:
        pass

print(f"Loaded {len(guid_db)} GUIDs")
# 查詢
target = "D6A2CB7F"
print(f"{target}: {guid_db.get(target.upper(), 'Unknown')}")
```

---

## 辨識 NVRAM / ME / Flash Descriptor region

除了 BIOS region 裡的 FV，你還需要認出其他 region：

### Flash Descriptor

```
Flash Descriptor（永遠在 offset 0，4KB）：
  0x00: 5A A5 F0 0F  ← Descriptor Signature
  0x10: FLMAP0        → 各 region 的起始 offset / 長度
  0x14: FLMAP1
  0x18: FLMAP2

解析：
python3 -c "
data = open('bios_full.bin','rb').read()
if data[0:4] == b'\x5A\xA5\xF0\x0F':
    print('Flash Descriptor found')
    flmap0 = int.from_bytes(data[0x10:0x14], 'little')
    # bit 23:16 = BIOS region base（以 4KB 為單位）
    bios_base = ((flmap0 >> 12) & 0xFFF0) << 8
    print(f'BIOS region approx base: 0x{bios_base:X}')
"
```

### NVRAM Region 識別

```bash
# NVRAM 以 VSS 格式（Variable Store Signature）開頭
# Signature: 'dd9f3b72-6e89-4c62-9bfd-fd4b3a7d3df8'（FFS GUID）
# 在 NVRAM section 開頭：
#   0x00: GUID（16 bytes）
#   0x10: Format（0x5A = FORMATTED）
#   0x11: State（0xFE = HEALTHY）

# 用 binwalk 找 NVRAM
binwalk -y "nvram" bios.bin
# 或手動找 VSS signature
python3 -c "
import struct
data = open('bios.bin','rb').read()
VSS_GUID = bytes.fromhex('72 3b 9f dd 89 6e 62 4c 9b fd fd 4b 3a 7d 3d f8'.replace(' ',''))
idx = 0
while True:
    idx = data.find(VSS_GUID, idx)
    if idx == -1: break
    print(f'NVRAM signature at 0x{idx:X}')
    idx += 1
"
```

### ME Region 識別

```
ME Region 以 '$FPT'（0x24465054）開頭
不透明，需要 me_cleaner 或 Intel MEA（ME Analyzer）解析

# me_cleaner 識別並分析
python me_cleaner.py -s bios_full.bin
# 輸出 ME 版本、分區、是否可以 neutralize

# Intel FPT（未實測）可以分別讀出 ME region：
fptw64.exe -d me_region.bin -ME
```

---

## 真跑練習：uefi-firmware-parser 拆 OVMF

以下步驟在 WSL Ubuntu 22.04 上全部可執行：

```bash
# Step 1：確認環境
which uefi-firmware-parser || pip3 install uefi_firmware
ls /usr/share/ovmf/OVMF_CODE.fd

# Step 2：快速掃描
uefi-firmware-parser -b /usr/share/ovmf/OVMF_CODE.fd 2>&1 | head -30

# Step 3：提取所有模組
OUTPUT_DIR=/tmp/ovmf_parsed
mkdir -p $OUTPUT_DIR
uefi-firmware-parser -e $OUTPUT_DIR -b /usr/share/ovmf/OVMF_CODE.fd

# Step 4：列出所有 EFI binary
echo "=== EFI modules found ==="
find $OUTPUT_DIR -name "*.efi" | while read f; do
    echo "$(basename $(dirname $f)): $f"
done

# Step 5：用 file 確認格式（PE32 vs TE）
find $OUTPUT_DIR -name "*.efi" | head -10 | while read f; do
    echo "$(file $f)"
done

# Step 6：找 DxeCore
find $OUTPUT_DIR -path "*D6A2CB7F*" -name "*.efi"
# 如果找到，用 objdump 看 section layout
objdump -h /tmp/ovmf_parsed/.../DxeCore.efi 2>/dev/null | head -30
```

### 找 SMM 相關模組（真跑）

```bash
# 在 OVMF 裡找 SMM 相關的模組
find $OUTPUT_DIR -name "*.efi" | while read f; do
    if strings "$f" 2>/dev/null | grep -qi "smm\|smi"; then
        echo "SMM candidate: $f"
        strings "$f" | grep -i "smm\|smi" | head -5
        echo "---"
    fi
done
```

---

## UEFITool GUI 補充說明

UEFITool GUI（Windows 或 Linux Qt 版本）提供：

- 樹狀結構瀏覽 FV/FFS/Section
- 右鍵 Extract → 取出特定 section 的 binary
- 搜尋（Ctrl+F）：可搜尋 GUID、字串、hex pattern
- 右鍵 Replace：patch 特定 section（0.x 版本）
- 「Messages」面板顯示 parser 警告（checksume 錯誤、非標準結構等）

```
UEFITool 樹狀結構範例：
BIOS.bin
  └── Padding (0x0 - 0xFFF)
  └── Firmware Volume (FFV)
        └── Firmware File (SEC / PEIM)
              └── PE32 image
              └── Depex
              └── UI (name string)
        └── Firmware File (DXE / Runtime driver)
              └── Compressed section (LZMA)
                    └── PE32 image ← 真正的 driver binary
                    └── Depex
```

---

## 各工具比較

| 工具 | 格式支援 | CLI | GUI | 修改能力 | 腳本化 | 最適場景 |
|------|---------|-----|-----|---------|--------|---------|
| UEFITool NE | FV/FFS/NVRAM | ✓（UEFIExtract） | ✓ | 有限 | 不易 | 快速瀏覽 + 提取 |
| UEFITool 0.x | FV/FFS | ✓ | ✓ | ✓ | 不易 | Patch 韌體 |
| uefi_firmware | FV/FFS/Capsule | ✓ | ✗ | ✗ | ✓（Python） | 自動化分析 |
| fiano/utk | FV/FFS/NVRAM | ✓ | ✗ | ✓ | ✓（Go） | NVRAM 分析 + CI |
| binwalk | 廣泛 | ✓ | ✗ | ✗ | ✓（Python） | 第一步掃描 |

---

## 踩雷

1. **UEFIExtract 提取出的 .efi 可能是壓縮後的 wrapper，不是最終 PE**：FFS file 裡的 Section 可能是 `EFI_SECTION_COMPRESSION`，裡面又包一層 PE32 section。UEFIExtract 預設會自動解壓，但如果用 binwalk 提取的中間產物，可能只拿到壓縮層。看到提取出的 .efi 無法被 `objdump -h` 正確解析，先確認它是 PE 還是 TE，而不是壓縮 wrapper。

2. **TE 模組的 load address 是錯的**：`uefi-firmware-parser` 和 UEFIExtract 提取的 TE binary，`ImageBase` 欄位是建構時的假設值，不是實際 load 到記憶體的位址。Ghidra 載入 TE 時要手動算 base address（基於 FV offset + 鏈接時的 PCD 值），否則所有 cross-reference 和 XREF 分析都是錯的（見 Ch 24 詳解）。

3. **NVRAM region 不是 FV**：很多新手把 NVRAM region 當 FV 試圖解析，但 NVRAM 用的是 VSS 或 FTW（Fault-Tolerant Write）格式，不是 FV/FFS。UEFITool 會把它顯示為獨立節點，不是 Firmware Volume 的子節點。直接對 NVRAM region 跑 FV parser 會得到錯誤結果。

4. **Flash Descriptor 的 region 邊界很重要**：如果你拿到的是完整 SPI dump（含 Flash Descriptor + ME + BIOS），直接丟給 `uefi-firmware-parser -b` 有時會成功、有時會失敗，取決於它是否識別出 Flash Descriptor 並自動偏移到 BIOS region。失敗時試試先手動切出 BIOS region（根據 Flash Descriptor 解析出的 offset）再解析。

5. **OVMF 的 FV 佈局和真實 BIOS 不同**：OVMF 沒有 ME region、沒有 Flash Descriptor，直接從 FV 開始。用 OVMF 練習的流程在真實 BIOS（含 Descriptor）上要多一步「找 BIOS region 起始 offset」。不要把 OVMF 的解析流程直接套到廠商 BIOS 上。

6. **uefi_firmware 的 AutoParser 有時誤判壓縮格式**：`AutoParser` 會嘗試多種格式，偶爾把非 UEFI 的 binary 解析成亂的結果。看到輸出完全不合理時，改用 `FirmwareVolume(data)` 直接指定類型，或先用 UEFITool NE 確認格式再決定用哪個 parser class。

---

## 進階延伸

- **UEFI Capsule Update 簽章驗證**：UEFI spec 定義 capsule 可以有 `CapsuleCertificationPayload`，包含 RSA 簽章。解析簽章結構（`WIN_CERTIFICATE_UEFI_GUID` header）可以看到 OEM 用的 key，與 [Ch 6](./06-capsule-update-ffs.md) 的更新機制安全性分析直接相關。

- **自動化 GUID 辨識**：結合 CHIPSEC 的 `guid_db`、EDK2 的 .dec 文件、Intel 公開的 BIOS module list，可以建立一個覆蓋率較高的 GUID→功能 資料庫。公開 repo `edk2-platforms` 和 `tianocore` 有大量廠商客製化 GUID 記錄。

- **fiano 做韌體 diff**：fiano 的 `fv-nuke` 和 `utk` 支援兩個 ROM 的 diff，可以快速找出 BIOS update 前後哪些 DXE driver 被換掉，是漏洞 diffing（見 Ch 26）的利器。

---

## 動手練習

**練習 1：uefi-firmware-parser 拆 OVMF**（真跑）

```bash
mkdir -p /tmp/ovmf_p
uefi-firmware-parser -e /tmp/ovmf_p -b /usr/share/ovmf/OVMF_CODE.fd
find /tmp/ovmf_p -name "*.efi" | wc -l
# 問：找到幾個 .efi 模組？哪個 GUID 對應 DxeCore？
```

**練習 2：Python 腳本列出所有模組**（真跑）

把上面「Python API 腳本」那段存成 `/tmp/parse_ovmf.py`，執行並數有幾個 FirmwareFile 節點。

**練習 3：辨識 TE vs PE**（真跑）

```bash
# 找一個 TE 格式的模組
find /tmp/ovmf_p -name "*.efi" | while read f; do
    # TE signature 是 'VZ'（0x5A56）
    sig=$(xxd -l 2 "$f" 2>/dev/null | awk '{print $2$3}')
    if [ "$sig" = "565a" ] || [ "$sig" = "5a56" ]; then
        echo "TE: $f"
    fi
done
# 拿一個 TE，用 python 讀 StrippedSize 欄位（offset 0x6，2 bytes LE）
```

**練習 4：找 SMM 模組名稱**（真跑）

```bash
OUTPUT_DIR=/tmp/ovmf_p
find $OUTPUT_DIR -name "*.efi" -exec strings {} \; 2>/dev/null | \
  grep -i "smm\|smbase\|smi" | sort -u | head -20
```

---

## 本章重點

- SPI flash 佈局：Flash Descriptor → ME region → BIOS region（含多個 FV）→ NVRAM region
- FV 以 `_FVH` signature 識別，FFS file 以 EFI GUID 為索引，Section 有壓縮/PE32/TE 等多種類型
- TE 格式是 PEI stage 常用的精簡 PE，load address 計算需要 `StrippedSize` 欄位，Ghidra 載入要小心
- UEFIExtract CLI 最快提取指定 GUID 模組；`uefi-firmware-parser` 最適合腳本自動化
- NVRAM 不是 FV，不要用 FV parser 對它解析
- 這章是 Ch 24（Ghidra 逆 UEFI）的前置，拆出正確格式的 .efi binary 是逆向工作的起點

---

## 自我檢核

- [ ] 能說出 SPI flash 的四個主要 region，以及 BIOS region 裡 FV 的巢狀結構
- [ ] 知道 FV 的 magic signature 是什麼，可以用 hex editor 手動識別
- [ ] 能用 `uefi-firmware-parser -e` 對 OVMF 提取並找到 DxeCore 的 EFI binary
- [ ] 知道 TE 格式和 PE32 格式的差異，以及 `StrippedSize` 欄位的作用
- [ ] 能用 Python `uefi_firmware.AutoParser` 走訪所有 FirmwareFile 並印出 GUID
- [ ] 知道 NVRAM region 為什麼不能用 FV parser 解析

---

## 延伸閱讀

1. **UEFITool 源碼與 NE parser 設計**（`github.com/LongSoft/UEFITool`）
   讀哪裡：`UEFITool/UEFIParser/` 目錄，特別是 `ffs.h`（FFS 結構定義）和 `nvram.cpp`（NVRAM 解析）
   學什麼：FV/FFS/Section 各層的 offset 計算、checksum 驗證邏輯、為何某些結構被標注為「non-standard」，直接對應本章每個 struct 欄位的實際處理方式
   關聯：Ch 24 Ghidra 逆向時，理解 load address 計算必須回頭看 UEFITool 的 TE base 計算邏輯

2. **UEFI Specification 第 3 章：Firmware Storage**（`uefi.org/specifications`，免費下載）
   讀哪裡：UEFI Spec 的 Volume 3「Platform Initialization（PI）Specification」第 3 章（Firmware Storage Support）
   學什麼：`EFI_FIRMWARE_VOLUME_HEADER`、`EFI_FFS_FILE_HEADER`、Section 類型的官方定義，以及 Authenticated Variable Store（NVRAM）格式，是所有解析工具的共同 spec 依據
   關聯：本章所有 struct 定義的一手來源，Ch 5（variable 攻擊）和 Ch 24 都需要反覆翻這份 spec

3. **fiano 專案文件與 blog**（`github.com/linuxboot/fiano`，LinuxBoot blog）
   讀哪裡：fiano README 的 `utk` 用法，以及 LinuxBoot blog 的「UEFI Firmware Parsing in Go」文章
   學什麼：NVRAM（Variable Store）的精確解析、`utk diff` 在韌體 diffing 的應用、fiano 的 JSON 輸出格式（用於自動化管線）
   關聯：本章 fiano/utk 段落的延伸，直接接 Ch 26（韌體 diffing）的工具實作

→ [下一章](./24-ghidra-uefi-re.md)
