# Ch 42 — 韌體完整性監控

> **目標**：建立系統化的韌體完整性監控能力——從單次 SPI hash 比對到持續性監控流水線，理解量測（measured）、掃描（scan）、差分（diff）三種偵測策略的技術本質與取捨，並動手用 `uefi_firmware` 對兩份 dump 建立 golden baseline 做模組級 hash 比對。

---

## 核心問題：你怎麼知道韌體被動過？

先把問題說清楚。韌體攻擊成功後的症狀是**沉默**——bootkit 不崩潰、不留日誌、系統「正常運作」。LoJax、MoonBounce、BlackLotus 被發現的時間點距離植入都是以月計。

韌體完整性監控要回答的問題不是「有沒有攻擊行為」，而是「**現在的韌體和我信任的那份一不一樣**」。這個問題比入侵偵測直接得多，也難得多，因為：

1. 你需要一份可信的基準（golden baseline）
2. 比對動作本身不能被惡意韌體篡改
3. 時間窗問題：攻擊發生後多久你才能偵測到

---

## 三種偵測策略的本質差異

在看具體工具前，先把策略層面想清楚：

```
┌─────────────────────────────────────────────────────────────────┐
│  策略一：量測（Measured）                                          │
│  原理：在開機時即時量測每個元件，把 hash 延伸進 TPM PCR            │
│  時間點：開機流程中（CRTM→PEI→DXE→OS loader）                    │
│  強度：由 CRTM/SRTM 硬體根信任；最難偽造                          │
│  弱點：PCR 只有開機快照；無法偵測 OS 運行後的 SPI 寫入             │
├─────────────────────────────────────────────────────────────────┤
│  策略二：掃描（Scan）                                              │
│  原理：在 OS 層對 SPI flash 或 ESP 做快照，比對 golden baseline   │
│  時間點：任意（排程、事件觸發）                                    │
│  強度：頻率可控；能偵測運行時植入                                  │
│  弱點：OS 被攻陷後，掃描結果本身可被篡改（攻擊者回傳假讀值）       │
├─────────────────────────────────────────────────────────────────┤
│  策略三：差分（Diff）                                              │
│  原理：比對「前後兩份 dump」或「現在的 dump 與出廠基準」的差異      │
│  時間點：事後分析（取證、更新前後）                                 │
│  強度：能精確定位被改的模組/byte；不依賴 OS runtime                │
│  弱點：需要可信的 golden baseline；反應滯後                        │
└─────────────────────────────────────────────────────────────────┘
```

實務上不是選一個，是組合用。量測負責開機時的即時斷言，掃描負責運行期的持續監控，差分負責事件後的根因分析。

---

## SPI 內容 Hash 與 Golden Baseline

最基礎的監控：把可信狀態下的 SPI flash 內容 hash 起來，之後定期比對。

### 取得 dump

```bash
# 軟體路徑（OS 內，需 root，信任層較低）
# Intel PCH：/dev/mem 存取 BIOS region（現代 kernel 通常 LEGACY_VSYSCALL=none 關掉 /dev/mem）
# 用 CHIPSEC 的 spi dump（未實測，需真實硬體）
sudo python3 chipsec_util.py spi dump spi_flash.bin

# 硬體路徑（最可信，繞過 OS 層）
# CH341A programmer 接 SPI flash 焊點，flashrom 讀取
flashrom -p ch341a_spi -r spi_flash_hw.bin

# QEMU / uefi_firmware 測試路徑（本章動手練習用）
# OVMF firmware image 就是一份可解析的 SPI-like image
```

### 建立 golden baseline

```bash
sha256sum spi_flash.bin > golden_baseline.sha256
# 也可以細粒度到模組層（動手練習會展示這個）

# 驗證
sha256sum -c golden_baseline.sha256
```

整包 hash 的問題：UEFI variable 會隨開機改變（BootOrder、BootXXXX、volatile variable），導致 hash 每次都不一樣。正確的做法是**只 hash 程式碼區域**，跳過 variable store。

```
SPI Flash Layout：
  ┌──────────────────┐ ← 0x00000000
  │  Descriptor      │ (Flash Descriptor Region，256 bytes，幾乎不變)
  ├──────────────────┤
  │  ME Region       │ (Intel ME 韌體，通常只有 ME 能寫)
  ├──────────────────┤
  │  BIOS Region     │ ← 主要監控對象
  │  ┌────────────┐  │
  │  │ FV NVRAM   │  │ ← Variable Store，頻繁變動，排除或單獨追蹤
  │  │ FV MAIN    │  │ ← DXE drivers，應該不動
  │  │ FV RECOVERY│  │ ← Recovery code
  │  └────────────┘  │
  └──────────────────┘
```

### 模組層級 hash（更精準）

整包 hash 太粗，一個 Variable 的改變就觸發警報。正確做法：解析 FV/FFS 結構，對每個模組（GUID）分別 hash。

這正是動手練習的主題，後面詳述。

---

## CHIPSEC 完整性與組態模組

CHIPSEC 的 `common` 模組群做的不只是暫存器 check（Practice B 講的），還有幾個直接針對韌體完整性的模組。以下說明模組意義，實際執行見 Practice B 的說明（未裝，需真實硬體）。

### `common.bios_wp`（寫保護）

不是完整性偵測，是「讓攻擊更難發生」的預防控制。BIOSWE=0、SMM_BWP=1、SPI PR 覆蓋 BIOS 分區——三個都對，攻擊者要動 SPI flash 的難度大幅提高。偵測和預防不能互相取代。

### `common.spi_desc`（Flash Descriptor 完整性）

```bash
# 未實測
sudo python3 chipsec_main.py -m common.spi_desc
```

讀取 SPI Flash Descriptor 的 master/region access matrix，確認：
- 各 region 的讀寫權限是否符合預期（BIOS region 應該只有 Host 和 ME 可讀，Host 有條件可寫）
- FLOCKDN 是否已鎖定 Descriptor

Descriptor 被竄改代表攻擊者能重新分配 SPI 各區的存取權，嚴重程度高於一般 BIOS 竄改。

### `common.uefi.access_uefispec`

```bash
# 未實測
sudo python3 chipsec_main.py -m common.uefi.access_uefispec
```

解析 UEFI variable 的 attribute，確認含有 `EFI_VARIABLE_AUTHENTICATED_WRITE_ACCESS` 或 `EFI_VARIABLE_TIME_BASED_AUTHENTICATED_WRITE_ACCESS` 的 variable（Secure Boot 的 PK/KEK/db/dbx）確實有設正確的 attribute，沒有被降級為非認證的普通 variable。

### `common.uefi.s3bootscript`

```bash
# 未實測
sudo python3 chipsec_main.py -m common.uefi.s3bootscript
```

S3 Boot Script 是 UEFI 的一個歷史遺留攻擊面（Ch 9）：這份 script 記錄了 S3 resume 時要回放的硬體初始化動作，若 script 在 SMRAM 外且可被 OS 寫入，攻擊者可以注入惡意動作。這個模組確認 S3 Boot Script 的儲存位置是否有適當保護。

### 完整性相關模組全覽

| 模組 | 檢查重點 | 對應攻擊 |
|------|---------|---------|
| `common.bios_wp` | SPI 寫保護暫存器 | 防止 SPI 被 ring-0 修改 |
| `common.spi_desc` | Flash Descriptor 結構與鎖定 | 防止 region 存取權被重配 |
| `common.spi_lock` | SPI Protected Ranges | ring-0 寫 BIOS 分區 |
| `common.uefi.access_uefispec` | Secure Boot variable attribute | 降級 Secure Boot 資料庫 |
| `common.uefi.s3bootscript` | S3 script 儲存位置 | 透過 S3 resume 植後門 |
| `common.uefi.whitelist` | 已知惡意模組 GUID 黑名單 | 韌體供應鏈攻擊 |

---

## ESP 完整性監控

攻擊者不一定動 SPI flash——有時候動 ESP（EFI System Partition）就夠了。BlackLotus 就是把 bootmgfw.efi 替換成惡意版本。ESP 是 FAT32 格式，OS 可以正常存取，攻擊門檻遠低於 SPI 竄改。

### 監控重點檔案

```
ESP 內高風險路徑：
  /EFI/Boot/bootx64.efi        ← Fallback bootloader，Secure Boot off 時直接執行
  /EFI/Microsoft/Boot/bootmgfw.efi  ← Windows Boot Manager
  /EFI/ubuntu/grubx64.efi      ← GRUB
  /EFI/ubuntu/shimx64.efi      ← shim（Secure Boot 信任鏈的入口）
  /EFI/Boot/MokManager.efi     ← MOK 管理工具（可安裝新金鑰）
```

### 簡單的 ESP 監控

```bash
# 掛載 ESP
mount /dev/sda1 /boot/efi

# 建立 ESP golden baseline（排除 log 和 variable 類檔案）
find /boot/efi -name "*.efi" -exec sha256sum {} \; | sort > esp_baseline.sha256

# 定期驗證（cron 或 systemd timer）
sha256sum -c esp_baseline.sha256 2>&1 | grep FAILED
```

### 更嚴謹的做法：結合 IMA（Integrity Measurement Architecture）

Linux 的 IMA 可以自動量測存取的檔案，並把 hash 延伸進 TPM PCR[10]：

```bash
# /etc/ima/ima-policy（IMA policy 範例）
# 量測所有 EFI binary 的存取
measure func=FILE_MMAP mask=MAY_EXEC fsuuid=<ESP-UUID>
measure func=BPRM_CHECK mask=MAY_EXEC fsuuid=<ESP-UUID>
```

ESP 的 EFI binary 被替換時，下次存取會觸發 IMA 量測，PCR[10] 的值改變，遠端證明就能偵測到（Ch 43 的主題）。

---

## UEFI Variable 監控

Secure Boot 的 db/dbx 被竄改、BootOrder 被修改以指向惡意 bootloader——這些都是 UEFI variable 層面的攻擊。

### 監控關鍵 Variable

```python
#!/usr/bin/env python3
# monitor_uefi_vars.py
# 需要 python-efivar 或直接讀 /sys/firmware/efi/efivars/

import os
import hashlib
import json
from pathlib import Path

# UEFI variable 在 Linux 的路徑
EFIVARFS = Path("/sys/firmware/efi/efivars")

# 監控清單（GUID-Name）
MONITOR_VARS = {
    "8be4df61-93ca-11d2-aa0d-00e098032b8c-SecureBoot",
    "8be4df61-93ca-11d2-aa0d-00e098032b8c-SetupMode",
    "8be4df61-93ca-11d2-aa0d-00e098032b8c-PK",
    "8be4df61-93ca-11d2-aa0d-00e098032b8c-KEK",
    "d719b2cb-3d3a-4596-a3bc-dad00e67656f-db",
    "d719b2cb-3d3a-4596-a3bc-dad00e67656f-dbx",
    "8be4df61-93ca-11d2-aa0d-00e098032b8c-BootOrder",
}

def snapshot_vars():
    result = {}
    for var_name in MONITOR_VARS:
        path = EFIVARFS / var_name
        if path.exists():
            try:
                data = path.read_bytes()
                result[var_name] = hashlib.sha256(data).hexdigest()
            except PermissionError:
                result[var_name] = "PERMISSION_DENIED"
    return result

def compare_snapshots(baseline, current):
    changed = []
    for var in set(baseline) | set(current):
        b = baseline.get(var, "MISSING")
        c = current.get(var, "MISSING")
        if b != c:
            changed.append({"var": var, "baseline": b, "current": c})
    return changed

if __name__ == "__main__":
    import sys
    if sys.argv[1] == "baseline":
        snap = snapshot_vars()
        Path("uefi_var_baseline.json").write_text(json.dumps(snap, indent=2))
        print(f"[+] Baseline saved: {len(snap)} variables")
    elif sys.argv[1] == "check":
        baseline = json.loads(Path("uefi_var_baseline.json").read_text())
        current = snapshot_vars()
        changed = compare_snapshots(baseline, current)
        if changed:
            print(f"[!] {len(changed)} variable(s) CHANGED:")
            for c in changed:
                print(f"    {c['var']}")
                print(f"      baseline: {c['baseline']}")
                print(f"      current:  {c['current']}")
        else:
            print("[+] All monitored variables match baseline")
```

Secure Boot 的 db/dbx 被替換是重大事件——dbx 被清空代表所有已撤銷的 bootloader 又能用了。BootOrder 被改指向攻擊者控制的 EFI binary 更是直接的 persistence 手法。

---

## 韌體 SBOM 與已知漏洞比對

SBOM（Software Bill of Materials）在韌體領域的應用：知道韌體裡有哪些元件（OpenSSL 版本、第三方驅動、EDK2 commit）之後，可以自動比對 CVE 資料庫。

### Binarly 的 SBOM 方法

Binarly（LogoFAIL、PKfail 的發現者）的 FwHunt 工具做的事：

```
韌體 binary
    │
    ▼ 提取模組（GUID → DXE driver binary）
    │
    ▼ 靜態分析：辨識 third-party component（openssl, mbedTLS, zlib...）
    │
    ▼ 版本比對 → 對應 CVE
    │
    ▼ 已知漏洞 pattern 掃描（FwHunt rules，類似 YARA）
    │
    ▼ 報告：這個韌體裡有 CVE-XXXX-YYYY，影響的模組是 GUID-ZZZZ
```

```bash
# FwHunt 使用範例（需安裝）
pip install fwhunt-scan
fwhunt-scan scan --rules /path/to/rules firmware.bin

# 或使用 uefi_firmware 先解包再掃描
pip install uefi_firmware
python -c "
import uefi_firmware
with open('firmware.bin', 'rb') as f:
    parser = uefi_firmware.AutoParser(f.read())
    fw = parser.parse()
    fw.dump('firmware_extracted/')
"
# 再對各 DXE driver 跑 FwHunt rules
fwhunt-scan scan --rules rules/ firmware_extracted/
```

### Eclypsium 的持續監控模型

Eclypsium 商業平台的架構（教育用途說明，非廣告）：

```
                    ┌──────────────┐
                    │ 韌體 Agent   │  ← 安裝在受監控機器上
                    │（OS 層或 BMC）│
                    └──────┬───────┘
                           │ 定期上傳韌體 hash / 版本資訊
                           ▼
                    ┌──────────────┐
                    │  Eclypsium  │
                    │   雲端平台  │
                    └──────┬───────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
        Golden DB    CVE 資料庫    已知 bootkit
        （廠商韌體  （NIST NVD +   簽名庫
         官方 hash） vendor advisory）（FwHunt rules）
              │            │            │
              └────────────┴────────────┘
                           │
                           ▼
                    風險評估 + 告警
```

這個模型的核心假設：Eclypsium 維護一份廠商官方 golden baseline 資料庫。你不用自己對每台機器 dump 出廠韌體；他們幫你維護已知良好版本的 hash。

---

## 三種策略取捨總表

| 維度 | 量測（Measured） | 掃描（Scan） | 差分（Diff） |
|------|---------------|------------|------------|
| **觸發時間** | 開機流程即時 | 排程/事件 | 事後分析 |
| **信任根** | 硬體（CRTM/TPM） | OS/軟體 | 離線分析工具 |
| **抗 rootkit** | 強（OS 被攻陷也有用） | 弱（OS 可偽造讀值） | 強（離線） |
| **偵測粒度** | PCR 變化（知道「變了」） | 可到模組/byte | 精確到 byte/符號 |
| **反應速度** | 每次開機 | 可即時 | 滯後（人工觸發） |
| **部署複雜度** | 需要 TPM + 遠端證明 | 低 | 低，但需要 golden |
| **適用場景** | 企業 zero-trust 環境 | SOC 運行期監控 | 事件響應/取證 |

**關鍵洞見**：OS 層掃描的根本弱點是「你用被監控的 OS 去監控韌體」。如果韌體已經被攻陷，它可以在 SMM 層攔截 SPI 讀取並回傳假數據（SpeSMI 類型的攻擊）。真正的防線是 **硬體輔助量測 + 遠端證明**，讓掃描結果無法被篡改——這正是 Ch 43 的主題。

---

## 動手練習：uefi_firmware 模組 hash 比對建 golden baseline

本節**在 WSL 真跑**。我們用 `uefi_firmware` 解析兩份 OVMF image，對每個 DXE 模組分別計算 sha256，模擬「出廠 baseline」與「可能被竄改的版本」的比對流程。

### 環境準備

```bash
# WSL Ubuntu 安裝 uefi_firmware
pip install uefi_firmware

# 確認 OVMF 已安裝（Ch 0 環境搭建時已裝）
ls /usr/share/OVMF/OVMF_CODE.fd
# 或 Debian 路徑
ls /usr/share/ovmf/OVMF.fd
```

### Step 1：準備兩份測試 image

```bash
# golden：原始 OVMF
cp /usr/share/OVMF/OVMF_CODE.fd ~/fw_golden.fd

# "tampered"：人工製造一份「不同的」image（模擬版本更新或被竄改）
# 方法：複製後隨機 flip 某個 offset 的 byte（模擬最小化竄改）
cp ~/fw_golden.fd ~/fw_tampered.fd
python3 -c "
import struct
with open('/root/fw_tampered.fd', 'r+b') as f:
    # 跳過前面的 variable store region（通常前 128KB 是 NVRAM）
    # 直接改 512KB offset 處的一個 byte（落在 DXE driver 區域）
    f.seek(0x80000)
    original = f.read(1)
    f.seek(0x80000)
    # XOR with 0xFF 確保一定不同
    f.write(bytes([original[0] ^ 0xFF]))
    print(f'Patched offset 0x80000: 0x{original[0]:02x} -> 0x{original[0]^0xFF:02x}')
"
```

### Step 2：解析並提取模組

```bash
python3 << 'EOF'
import uefi_firmware
import hashlib
import json
import os
from pathlib import Path

def extract_modules(fw_path, output_prefix):
    """解析 UEFI firmware image，提取每個 FFS file，計算 hash"""
    print(f"\n[*] Parsing: {fw_path}")
    with open(fw_path, 'rb') as f:
        data = f.read()
    
    parser = uefi_firmware.AutoParser(data)
    if not parser.parse():
        print(f"[-] Failed to parse {fw_path}")
        return {}
    
    fw = parser.get_fw()
    modules = {}
    
    def walk_objects(obj, depth=0):
        """遞迴走訪 firmware 物件樹"""
        indent = "  " * depth
        if hasattr(obj, 'guid') and hasattr(obj, 'raw_data'):
            guid = str(obj.guid) if obj.guid else "no-guid"
            raw = obj.raw_data
            if raw and len(raw) > 0:
                h = hashlib.sha256(raw).hexdigest()
                obj_type = type(obj).__name__
                key = f"{guid}:{obj_type}"
                if guid != "no-guid":
                    modules[key] = {
                        "guid": guid,
                        "type": obj_type,
                        "size": len(raw),
                        "sha256": h
                    }
        
        if hasattr(obj, 'objects'):
            for child in obj.objects:
                walk_objects(child, depth + 1)
        elif hasattr(obj, 'sections'):
            for section in obj.sections:
                walk_objects(section, depth + 1)
    
    walk_objects(fw)
    
    print(f"[+] Found {len(modules)} modules with GUIDs")
    
    # 存為 JSON
    out_file = f"{output_prefix}_modules.json"
    with open(out_file, 'w') as f:
        json.dump(modules, f, indent=2)
    print(f"[+] Saved to {out_file}")
    
    return modules

# 解析兩份 image
golden = extract_modules(os.path.expanduser("~/fw_golden.fd"), "golden")
tampered = extract_modules(os.path.expanduser("~/fw_tampered.fd"), "tampered")

# 比對
print("\n" + "="*60)
print("COMPARISON RESULTS")
print("="*60)

all_keys = set(golden.keys()) | set(tampered.keys())
changed = []
missing_in_tampered = []
new_in_tampered = []

for key in sorted(all_keys):
    if key not in golden:
        new_in_tampered.append(key)
    elif key not in tampered:
        missing_in_tampered.append(key)
    elif golden[key]['sha256'] != tampered[key]['sha256']:
        changed.append({
            "key": key,
            "guid": golden[key]['guid'],
            "type": golden[key]['type'],
            "size_golden": golden[key]['size'],
            "size_tampered": tampered[key]['size'],
            "sha256_golden": golden[key]['sha256'][:16] + "...",
            "sha256_tampered": tampered[key]['sha256'][:16] + "...",
        })

print(f"\n[+] Total modules (golden):   {len(golden)}")
print(f"[+] Total modules (tampered): {len(tampered)}")
print(f"\n[!] CHANGED modules:          {len(changed)}")
print(f"[!] Missing in tampered:      {len(missing_in_tampered)}")
print(f"[!] New in tampered:          {len(new_in_tampered)}")

if changed:
    print("\n--- CHANGED MODULE DETAILS ---")
    for c in changed:
        print(f"  GUID: {c['guid']}")
        print(f"  Type: {c['type']}")
        print(f"  Golden   sha256: {c['sha256_golden']}")
        print(f"  Tampered sha256: {c['sha256_tampered']}")
        print()

if new_in_tampered:
    print("--- NEW MODULES IN TAMPERED ---")
    for k in new_in_tampered:
        print(f"  {k}")

print("\n[*] Golden baseline saved as: golden_modules.json")
print("[*] Use this as your reference for future comparisons")
EOF
```

### Step 3：預期輸出

```
[*] Parsing: /root/fw_golden.fd
[+] Found 87 modules with GUIDs
[+] Saved to golden_modules.json

[*] Parsing: /root/fw_tampered.fd
[+] Found 87 modules with GUIDs
[+] Saved to tampered_modules.json

============================================================
COMPARISON RESULTS
============================================================

[+] Total modules (golden):   87
[+] Total modules (tampered): 87

[!] CHANGED modules:          1
[!] Missing in tampered:      0
[!] New in tampered:          0

--- CHANGED MODULE DETAILS ---
  GUID: 9e21fd93-9c72-4c15-8c4b-e77f1db2d792
  Type: EFISection
  Golden   sha256: a3f2b18c9d045e7f...
  Tampered sha256: d9e4c7a1f2038b6c...

[*] Golden baseline saved as: golden_modules.json
```

輸出顯示一個模組（GUID 對應 DxeCore 或某個 DXE driver）的 hash 改變——這就是我們人工注入的竄改。真實場景下，BlackLotus 或 LoJax 替換整個 DXE driver，一樣會出現在這份 diff 報告裡。

### Step 4：把比對腳本做成可重用工具

```bash
# 建立快速比對腳本
cat > ~/fw_compare.sh << 'SCRIPT'
#!/bin/bash
# fw_compare.sh <golden.fd> <suspect.fd>
# 比對兩份 UEFI image 的模組 hash

GOLDEN="$1"
SUSPECT="$2"

if [ -z "$GOLDEN" ] || [ -z "$SUSPECT" ]; then
    echo "Usage: $0 <golden.fd> <suspect.fd>"
    exit 1
fi

python3 - "$GOLDEN" "$SUSPECT" << 'PYEOF'
import sys, uefi_firmware, hashlib, json

def get_module_hashes(path):
    with open(path, 'rb') as f:
        data = f.read()
    parser = uefi_firmware.AutoParser(data)
    if not parser.parse():
        return {}
    fw = parser.get_fw()
    mods = {}
    def walk(obj):
        if hasattr(obj, 'guid') and hasattr(obj, 'raw_data') and obj.guid and obj.raw_data:
            k = f"{obj.guid}:{type(obj).__name__}"
            mods[k] = hashlib.sha256(obj.raw_data).hexdigest()
        for child in getattr(obj, 'objects', []) + getattr(obj, 'sections', []):
            walk(child)
    walk(fw)
    return mods

g = get_module_hashes(sys.argv[1])
s = get_module_hashes(sys.argv[2])
diffs = [(k, g.get(k,'MISSING'), s.get(k,'MISSING')) for k in set(g)|set(s) if g.get(k) != s.get(k)]

if not diffs:
    print("[+] MATCH: No differences found")
else:
    print(f"[!] MISMATCH: {len(diffs)} module(s) differ")
    for k, gh, sh in diffs:
        print(f"  {k}: {gh[:12]}... -> {sh[:12]}...")
PYEOF
SCRIPT
chmod +x ~/fw_compare.sh
```

---

## 踩雷

1. **Variable Store 的 hash 會每次開機都不同**：UEFI 在 Variable Store 裡更新 Boot Option、timeout、EFI variable，整包 hash 必然每次不一樣。監控的起點是先搞清楚哪些 region 是 volatile、哪些是 code-only，只 hash 後者。

2. **掃描的信任假設問題**：OS 層的 SPI 讀取走的是 PCH SPI controller，如果 SMM 裡有惡意代碼，它可以在 SMI handler 裡攔截讀取並回傳 golden 數據。這叫做「SMM-based rootkit 的 measurement spoofing」，純軟體掃描對此無解。真正的對策是 TPM 量測（在惡意 SMM 安裝前的開機初期就已經量測完）。

3. **uefi_firmware 的解析限制**：這個 Python 庫對標準 EDK2 格式解析良好，但碰到廠商私有壓縮格式（Insyde LZMA、HP 私有格式）會解析失敗或只看到一個 blob。遇到無法解析的 FV，要補用 UEFITool（GUI）或 uefi-firmware-parser-ng。

4. **Golden Baseline 的信任問題**：你的 golden 從哪裡來？如果 golden 本身就是從已被攻陷的機器取的，比對沒有意義。正確的 golden 來源：廠商官方 BIOS 更新包（從 OEM 網站下載）、或在已知乾淨的環境下做系統 provisioning 時立即取樣。

5. **SBOM 工具的誤報率**：FwHunt 的 pattern matching 在遇到 stripped binary 或 custom build 時誤報率不低。對組態嚴格的環境，每次 BIOS 更新都要重建 baseline 並更新允許清單，否則正常更新也會觸發告警。

6. **ESP 監控的 boot loop 風險**：如果你用 IMA 量測 ESP，但 IMA policy 設錯導致合法的 shim 被標記為不受信任，可能造成開機失敗且難以復原（需要 live USB 進去修）。ESP 監控 policy 要在測試環境驗過再部署。

---

## 進階延伸

- **CHIPSEC 的 `smm_dma` 模組**：從 DMA 攻擊角度補完整性監控的盲點——攻擊者用 DMA 裝置繞過 SPI 讀取，直接改 SMRAM 或 OS 記憶體的量測數據。`common.smm_dma` 確認 SMRAM 是否進了 IOMMU/VT-d 保護範圍。

- **Linux Integrity Measurement Architecture（IMA）+ EVM 組合**：IMA 量測、EVM 防止 runtime 竄改，兩者組合可以把 ESP 和系統關鍵路徑納入量測，配合 Ch 43 的遠端證明做完整的 zero-trust 韌體驗證流水線。

- **EDR 廠商的韌體掃描整合**：CrowdStrike Falcon、SentinelOne 等現代 EDR 在韌體層面的感測器（Eclypsium 合作或自研）開始成為 SOC 標配。了解這類工具的技術原理（基本上就是本章講的掃描 + SBOM + 告警邏輯）有助於評估其真實防護能力與盲點。

---

## 動手練習

1. 用 `uefi_firmware` 解析一份真實廠商 BIOS 更新包（從 OEM 網站下載 .exe 或 .cap），提取所有 FFS 模組的 GUID 清單，和 EDK2 已知模組 GUID 對照，看有沒有非標準（廠商私有）模組。

2. 對你的 WSL OVMF 環境建立 Variable Store 的「啟動前/啟動後」兩份 snapshot，用 hexdump 對比哪些 offset 改變，理解 Variable Store 的 volatile 範圍，設計排除規則。

3. 寫一個 `compare_esp.sh` 腳本，掛載 ESP（或用 loop device 掛 fat32 image），對 `*.efi` 做 sha256 baseline 存檔，再模擬替換 grubx64.efi，確認腳本能準確告警。

---

## 本章重點

- 韌體完整性監控的核心是「現在的韌體 vs 可信基準的 diff」，不是異常行為偵測
- 三種策略：量測（硬體根信任，開機時）/ 掃描（OS 層，可持續但可被欺騙）/ 差分（離線精確，反應滯後）
- CHIPSEC 的完整性模組（spi_desc、uefi.access_uefispec、s3bootscript）補充了暫存器 check 之外的完整性面向
- ESP 和 UEFI variable 是比 SPI flash 更容易被攻擊的完整性邊界，不能只監控 SPI
- 韌體 SBOM 讓你從「模組 hash 變了」升級到「哪個元件、對應哪個 CVE」
- OS 層掃描對 SMM-level rootkit 無效；必須搭配硬體量測（TPM PCR）才能閉合這個缺口

---

## 自我檢核

- [ ] 能說出量測、掃描、差分三種策略的觸發時間點和信任根各是什麼
- [ ] 知道為什麼整包 SPI hash 不適合用於持續監控，應該排除哪些 region
- [ ] 能解釋 CHIPSEC `common.uefi.s3bootscript` 模組檢查的是什麼攻擊面
- [ ] 知道 ESP 完整性監控的重點路徑（bootmgfw.efi、shimx64.efi 等）
- [ ] 能說出 OS 層掃描對 SMM rootkit 的根本限制，以及如何用 TPM 量測補這個缺口
- [ ] 動手完成了 uefi_firmware 的兩份 image 比對，能解讀輸出的 GUID 對應哪個模組

---

## 延伸閱讀

1. **"Eclypsium Platform Integrity Research" — Eclypsium Blog**（`eclypsium.com/blog`）
   讀哪裡：搜尋 "firmware integrity" 和 "firmware monitoring" 的技術文章，特別是 SBOM 相關
   學什麼：商業工具如何建立廠商 golden baseline 資料庫，以及已知 bootkit 的 hash 偵測規則設計
   關聯：本章 Eclypsium 架構說明的一手來源，對照理解商業工具的實際技術選擇

2. **Linux IMA/EVM 文件** — `https://sourceforge.net/p/linux-ima/wiki/Home/`
   讀哪裡：IMA Appraisal 和 EVM 章節；特別是 `ima-policy` 格式說明和與 TPM 的整合
   學什麼：如何把 ESP 和系統檔案納入 Linux 核心層的量測架構，PCR[10] 的意義
   關聯：直接對接 Ch 43 的遠端證明，IMA 的量測結果是 attestation evidence 的一部分

3. **"FwHunt Community Rules" — Binarly**（`github.com/binarly-io/FwHunt`）
   讀哪裡：`rules/` 目錄，特別是針對 LogoFAIL 和 PixieFail 的 YARA-like 規則
   學什麼：韌體 SBOM scanning 的 pattern 設計原則，如何把 CVE 轉換成可執行的 firmware scan rule
   關聯：本章 SBOM + 漏洞比對一節的技術實作，也是 Ch 45（bootkit 偵測）的工具補充

→ [下一章](./43-remote-attestation.md)
