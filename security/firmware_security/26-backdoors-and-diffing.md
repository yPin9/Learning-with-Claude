# Ch 26 — 找後門與韌體 diffing

> **目標**：掌握韌體 diffing 的完整方法論——從兩版 firmware 的模組級比對，到 BinDiff/Diaphora 的 binary diff，到「看更新包反推廠商靜默修了什麼洞」的 Binarly 風格 patch-diffing。同時建立找隱藏後門的系統性搜查方法：magic value、硬編碼憑證、恆真驗簽分支、供應鏈植入。

---

## 為什麼 diffing 是研究者的第一工具

逆向一個完整韌體是 O(N) 工作量，N 通常很大。**diffing 把 N 壓縮到「兩版之間變了的部分」**，讓你以 O(delta) 找到最值得關注的地方——要嘛是廠商剛修的洞，要嘛是新增的功能（攻擊面），要嘛是不應該存在的後門。

Binarly 的研究方式是典型的：他們不逐行讀 vendor BIOS，而是比較「上一版 vs 這一版」，從差異反推 CVE。LogoFAIL、PKfail 等多個重大漏洞都是這樣找到的。

```
研究者流程：
  廠商釋出更新 BIOS vN+1
       │
       ▼
  binwalk / uefi_firmware 分別解包 vN 和 vN+1
       │
       ▼
  模組層級比對：哪些 GUID 消失了？哪些新增？哪些大小變了？
       │
       ▼
  對有 delta 的模組跑 BinDiff / Diaphora
       │
       ▼
  找到差異函式 → 逆向「修了什麼」→ 從修法反推「原本的 bug 是什麼」
       │
       ▼
  在舊版韌體（或有漏洞的機器）驗證 bug 是否可利用
```

這個流程讓「找 N-day」比「找 0-day」快十倍。

---

## 工具準備

```bash
# WSL Ubuntu 22.04 真跑環境
sudo apt-get install -y binwalk python3-pip git
pip3 install uefi_firmware

# BinDiff：Zynamics（Google）的 binary diffing 工具
# 免費版：https://github.com/google/bindiff
# 需要 Ghidra 或 IDA 作為前端
# Diaphora 是開源替代品，接 IDA（免費）

# 取得 Diaphora
git clone https://github.com/joxeankoret/diaphora.git

# 準備兩個版本的 OVMF（或目標 BIOS）
# OVMF 版本差異用於練習
```

---

## 第一步：模組層級比對

### binwalk 解包兩版韌體

```bash
# ── 真跑 ──
# 假設有兩個 BIOS image：bios_v1.bin 和 bios_v2.bin
# 這裡用 OVMF 的兩個 build 作為練習目標

# 解包 v1
mkdir -p /tmp/fw_diff/v1 /tmp/fw_diff/v2
binwalk -e --directory=/tmp/fw_diff/v1 bios_v1.bin

# 解包 v2
binwalk -e --directory=/tmp/fw_diff/v2 bios_v2.bin

# 查看輸出目錄結構
ls /tmp/fw_diff/v1/
# 典型輸出：BIOS 裡的 FV（Firmware Volume）會被辨認
# 0x000000       UEFI PI Firmware Volume
# 0x100000       UEFI PI Firmware Volume
# ...
```

### uefi_firmware 做 GUID 層級解析

binwalk 對 UEFI 的解析粒度不夠細。`uefi_firmware` 能解到 FFS（Firmware File System）的模組層級：

```bash
# ── 真跑 ──
# 解包 v1，輸出 GUID 清單
python3 -c "
import uefi_firmware
with open('bios_v1.bin', 'rb') as f:
    data = f.read()
parser = uefi_firmware.AutoParser(data)
firmware = parser.parse()
firmware.dump('/tmp/fw_v1')
"

python3 -c "
import uefi_firmware
with open('bios_v2.bin', 'rb') as f:
    data = f.read()
parser = uefi_firmware.AutoParser(data)
firmware = parser.parse()
firmware.dump('/tmp/fw_v2')
"

# 取得所有模組的 GUID 清單
find /tmp/fw_v1 -name '*.efi' | sort > /tmp/v1_modules.txt
find /tmp/fw_v2 -name '*.efi' | sort > /tmp/v2_modules.txt

# 比較
diff /tmp/v1_modules.txt /tmp/v2_modules.txt
```

### 用 `uefi-firmware-parser` CLI 取 GUID 清單

```bash
# uefi_firmware 也有命令列介面
uefi-firmware-parser --brute bios_v1.bin 2>/dev/null | grep GUID > /tmp/v1_guids.txt
uefi-firmware-parser --brute bios_v2.bin 2>/dev/null | grep GUID > /tmp/v2_guids.txt

diff /tmp/v1_guids.txt /tmp/v2_guids.txt
# 輸出範例（示意）：
# < GUID: 9B680FCE-AD6B-4F3A-B60B-F59899003443 (size=0x6800)
# ---
# > GUID: 9B680FCE-AD6B-4F3A-B60B-F59899003443 (size=0x6920)
# 這個模組大小從 0x6800 增加到 0x6920，是修改的候選
```

### 自動化 delta 報告腳本

```python
#!/usr/bin/env python3
# fw_delta.py — 比較兩個韌體的模組 delta
# ── 真跑 ──

import uefi_firmware, os, hashlib, sys

def extract_modules(fw_path, out_dir):
    """解包韌體，回傳 {guid: (size, sha256)} 字典"""
    with open(fw_path, 'rb') as f:
        data = f.read()
    parser = uefi_firmware.AutoParser(data)
    result = parser.parse()
    if result is None:
        print(f"[!] 無法解析 {fw_path}")
        return {}
    result.dump(out_dir)
    
    modules = {}
    for root, dirs, files in os.walk(out_dir):
        for fname in files:
            if fname.endswith('.efi') or fname.endswith('.pe32'):
                path = os.path.join(root, fname)
                with open(path, 'rb') as f:
                    content = f.read()
                # GUID 從路徑提取（uefi_firmware 用 GUID 命名目錄）
                guid = os.path.basename(os.path.dirname(path))
                sha = hashlib.sha256(content).hexdigest()[:16]
                modules[guid] = (len(content), sha, path)
    return modules

v1 = extract_modules(sys.argv[1], '/tmp/fw_v1')
v2 = extract_modules(sys.argv[2], '/tmp/fw_v2')

all_guids = set(v1.keys()) | set(v2.keys())

print("=== 韌體 delta 報告 ===")
print(f"{'狀態':<10} {'GUID':<40} {'v1 size':<12} {'v2 size':<12} {'備注'}")
print("-" * 90)

for guid in sorted(all_guids):
    if guid not in v1:
        print(f"{'[新增]':<10} {guid:<40} {'---':<12} {v2[guid][0]:<12} 新模組")
    elif guid not in v2:
        print(f"{'[移除]':<10} {guid:<40} {v1[guid][0]:<12} {'---':<12} 模組消失")
    elif v1[guid][1] != v2[guid][1]:
        diff = v2[guid][0] - v1[guid][0]
        sign = '+' if diff >= 0 else ''
        print(f"{'[修改]':<10} {guid:<40} {v1[guid][0]:<12} {v2[guid][0]:<12} {sign}{diff} bytes")
    # else: 未變，不印

# 執行：python3 fw_delta.py bios_v1.bin bios_v2.bin
```

---

## 第二步：Binary Diff（BinDiff / Diaphora）

模組層級比對告訴你「哪個 DXE 模組被改了」，binary diff 告訴你「改了哪些函式、哪幾行邏輯」。

### 工作流程：Ghidra + BinDiff

```
Step 1：把 v1 的模組載入 Ghidra，自動分析，export .BinExport
Step 2：把 v2 的模組載入 Ghidra，自動分析，export .BinExport
Step 3：BinDiff GUI 讀兩個 .BinExport，跑 similarity matching
Step 4：看 similarity < 1.0 的函式對 → 雙擊進 diff view
Step 5：差異行 = 廠商修改的地方 → 從修法反推 bug
```

```bash
# Ghidra export BinExport（需要安裝 BinExport Ghidra plugin）
# https://github.com/google/bindiff/tree/main/tools/ghidra
# 在 Ghidra script manager 執行 ExportBinExport.java
# 或用 headless 模式：

$GHIDRA_HOME/support/analyzeHeadless /tmp/proj SecurityStubDxe_v1 \
  -import /tmp/fw_v1/SecurityStubDxe.efi \
  -postScript ExportBinExport.java \
  -scriptPath ~/diaphora
```

### Diaphora（開源替代）接 IDA

```python
# 在 IDA 中：File → Script File → diaphora.py
# 它會 export 一個 .sqlite，包含所有函式的 feature vector
# 然後對 v1.sqlite 和 v2.sqlite 跑 diff

# Diaphora 的 similarity score 說明：
# 1.0 = 完全相同（pass，不看）
# 0.9–1.0 = 微小改動（看 patch candidate）
# 0.5–0.9 = 中等改動（優先看）
# < 0.5 = 大幅重寫或新函式（最可疑）
```

### 讀 diff 結果：Binarly 風格

```
真實研究策略（Binarly 公開的方法論）：

1. 先看「函式數量大量增加」的模組：可能是新增程式碼路徑（新功能 or 後門）
2. 找「相似度 0.6-0.95」的函式：這是局部修改，最可能是 patch
3. 差異部分：找 bounds check 被加入、error return 被加入、memcpy size 限制
   → 這些通常是 OOB/overflow 的修補
4. 如果 v1 在對應位置「沒有 check」 → v1 就是有 bug 的版本
5. 在 v1 二進位構造 PoC，驗證 bug
```

---

## 第三步：Patch-diffing 反推 CVE

### 案例：LogoFAIL（2023）

Binarly 的 LogoFAIL 是 patch-diffing 的教科書案例。

```
發現過程重建：
  Binarly 拿到廠商更新的 BIOS（HP/Lenovo/AMI）
       │
       ▼
  模組層級 diff：BMP/GIF/PNG 解析相關的 DXE 模組有 delta
       │
       ▼
  binary diff：image parsing 函式出現新的 bounds check
  具體：PNG parser 的 chunk size 讀取從 unchecked 變成有 size validation
       │
       ▼
  在舊版重建：傳入畸形 PNG（chunk size = 0xFFFFFFFF）
       │
       ▼
  BMP logo parsing DXE（在 DXE_CORE 下面執行，privilege 極高）
  crash → OOB write → 控制 DXE 執行 → 繞過 Secure Boot
```

```c
// 修補前（示意，從 diff 推出）：
UINT32 chunk_size = ReadU32(data + offset);   // 攻擊者控制
UINT8 *buf = AllocatePool(chunk_size);        // chunk_size=0xFFFF → 大量分配 or 整數溢位
CopyMem(buf, src, actual_size);              // 但實際 src 遠小於 chunk_size

// 修補後：
UINT32 chunk_size = ReadU32(data + offset);
if (chunk_size > MAX_CHUNK_SIZE || chunk_size == 0) {
    return EFI_COMPROMISED_DATA;             // 新增 bounds check
}
UINT8 *buf = AllocatePool(chunk_size);
CopyMem(buf, src, chunk_size);
```

從「新增的 check」就能推出「check 之前的 code path 是 vulnerable 的」。

---

## 第四步：找隱藏後門

### 後門 pattern 一：Magic Value / Magic Knock

「某個特殊輸入讓裝置進入特殊模式」——這在嵌入式裝置和 UEFI 都存在。

```bash
# 用 strings 搜尋可疑 magic value
strings firmware.bin | grep -iE 'debug|backdoor|magic|password|admin|secret|factory|unlock'

# 尋找 0xDEAD、0xBEEF、0xCAFE 等典型 magic bytes
python3 -c "
import re, sys
data = open(sys.argv[1], 'rb').read()
# 找 32bit magic patterns
patterns = [b'\xDE\xAD\xBE\xEF', b'\xCA\xFE\xBA\xBE', b'\xDE\xAD\xC0\xDE',
            b'\xFE\xED\xFA\xCE', b'\x13\x37\x00\x00', b'\xBA\xAD\xF0\x0D']
for pat in patterns:
    offsets = [m.start() for m in re.finditer(re.escape(pat), data)]
    if offsets:
        print(f'{pat.hex()}: {offsets[:5]}')
" firmware.bin
```

**真實案例：Cisco IOS 後門（2015）**

研究者在 Cisco IOS firmware 找到一個 SSH daemon 後門：當特定 SSH key（硬編碼在 binary 裡）被用於認證時，繞過正常認證流程。在 binary 中的特徵是一個「永遠為 true」的 compare branch，或一個「比對特定 byte sequence 就 return AUTH_OK」的路徑。

```
Ghidra 找法：
  搜字串 "authorized" / "password" / "auth"
  找含有這些字串的函式
  分析 xref：哪個 branch 在正常流程之外
  特別注意：if (compare(input, hardcoded_key) == 0) goto auth_success
```

### 後門 pattern 二：硬編碼憑證

```bash
# Strings 搜尋明文憑證
strings firmware.bin | grep -E '(password|passwd|user|root|admin)' | sort -u

# 搜尋 PEM-style key/cert
strings firmware.bin | grep -E 'BEGIN (RSA|EC|CERTIFICATE|PRIVATE)'

# 熵值分析（高熵 = 可能是加密 key 或壓縮資料）
python3 -c "
import math, sys

def entropy(data):
    if not data:
        return 0
    freq = {}
    for b in data:
        freq[b] = freq.get(b, 0) + 1
    total = len(data)
    return -sum((c/total) * math.log2(c/total) for c in freq.values())

data = open(sys.argv[1], 'rb').read()
block = 256
threshold = 7.5  # 接近 8 = 高熵

high_entropy_regions = []
for i in range(0, len(data) - block, block):
    e = entropy(data[i:i+block])
    if e > threshold:
        high_entropy_regions.append((i, e))

for offset, e in high_entropy_regions[:20]:
    print(f'0x{offset:08X}: entropy={e:.2f}')
" firmware.bin

# 高熵區域可能是：RSA key, AES key, 加密 payload, 壓縮資料
# 配合 binwalk -E 做視覺化熵值圖
binwalk -E firmware.bin
```

### 後門 pattern 三：恆真驗簽分支（永遠通過的驗章）

這是最危險的後門類型，因為它讓整個 secure boot 信任鏈失效。

```
Ghidra 分析步驟：
  1. 找驗簽函式：搜 "VerifySignature" / "EFI_IMAGE_EXECUTION_AUTH_*" / "AuthVariableHandle"
  2. 找呼叫點，追蹤回傳值如何使用：
     if (VerifySignature(...) == EFI_SUCCESS) → 正常路徑
  3. 尋找以下 pattern：

     Pattern A（恆真）：
       MOV EAX, 0       ; EFI_SUCCESS = 0
       RET              ; 永遠回傳「成功」，根本沒驗
     
     Pattern B（短路分支）：
       CMP byte [flag], 1
       JE skip_verify
       CALL VerifySignature
     skip_verify:
       ; flag 是 hardcoded 或可從 NVRAM 設定
     
     Pattern C（return value 誤判）：
       CALL VerifySignature
       TEST EAX, EAX
       JNZ execute      ; != 0 當作「成功」，但 EFI_SUCCESS = 0
       ; 驗失敗時反而執行
```

```bash
# 在 Ghidra 的 Script Manager 用 Python 找恆真 return（pattern A）
# 搜尋 "MOV EAX,0x0; RET" 這個 pattern
# 實際上在 Ghidra Python：
# from ghidra.program.model.listing import CodeUnitIterator
# 搜尋 bytes: B8 00 00 00 00 C3  (MOV EAX,0; RET in x86)

python3 -c "
data = open('SecurityStubDxe.efi', 'rb').read()
# MOV EAX, 0 ; RET  (x86-64 可能是 XOR EAX,EAX ; RET)
patterns = [
    bytes([0xB8, 0x00, 0x00, 0x00, 0x00, 0xC3]),  # MOV EAX,0; RET
    bytes([0x31, 0xC0, 0xC3]),                      # XOR EAX,EAX; RET
    bytes([0x33, 0xC0, 0xC3]),                      # XOR EAX,EAX; RET (另一形式)
]
import re
for pat in patterns:
    for m in re.finditer(re.escape(pat), data):
        print(f'可疑 pattern @0x{m.start():X}: {pat.hex()}')
"
```

### 後門 pattern 四：Debug Backdoor（開發遺留）

```bash
# 找 debug 相關字串
strings firmware.bin | grep -iE '(debug|test|dev|engineering|prototype|factory)' | head -30

# 找可疑的 UEFI variable 名稱（可以從 OS 設定的 backdoor）
strings firmware.bin | grep -E '^[A-Z][a-zA-Z]{3,20}$' | grep -iE '(dbg|test|eng|dev)'

# 找可疑的 SMI handler（SW SMI 可從 OS 觸發）
# 在韌體中搜 SmiHandlerRegister 或 EFI_SMM_SW_DISPATCH2_PROTOCOL 的 xref
```

**真實案例：AMI AptioV 的 AMI_DEBUG_PORT（2024 Binarly 發現）**

某 AMI 韌體在 DXE 階段有一個透過特定 UEFI variable 啟用的 debug console，原意是 AMI 自己的工程診斷工具。問題是：這個功能在量產版沒有被停用，而且可以透過 OS root 設定該 variable，下次開機就得到一個在 UEFI 下運行的 debug shell（privilege 比 SMM 低但仍在 DXE，高於任何 OS component）。

---

## 供應鏈植入的偵測角度

供應鏈後門比開發遺留後門更難找，因為植入者會盡量讓 code 看起來「正常」。但有幾個異常指標：

```
偵測異常指標：

1. 模組 GUID 存在於 UEFI 標準列表，但 code 內容與公開 reference 不同
   → 查 edk2 source 對應 GUID 的原始 .c 檔比對

2. 模組 binary 和 OEM 官方 SDK 版本不同，但版本號相同
   → 版本號相同但 hash 不同 = 植入跡象

3. 有額外的 protocol 安裝或 handle 註冊，但功能描述裡沒有
   → DXE driver 安裝了額外 EFI_PROTOCOL，在原始 edk2 不存在

4. EFI_IMAGE_SECURITY_DATABASE（db）中有不明來源的憑證
   → CHIPSEC 或直接讀 db variable 檢查

5. 在正常功能之外有「看起來無關」的網路存取、NVRAM 讀取、SMI 觸發
   → 找控制流 xref 追「在正常入口之外誰呼叫了這個函式」
```

```bash
# 比對模組和 edk2 參考 build 的 hash
# 從 OVMF 官方 build 提取 SecurityStubDxe.efi
# 和目標韌體的同 GUID 模組比對

sha256sum /path/to/reference/SecurityStubDxe.efi
sha256sum /path/to/target/SecurityStubDxe.efi
# 若 GUID 相同但 hash 不同，且廠商版本號也相同 → 可疑
```

---

## 完整 diffing 工具鏈對照表

| 工具 | 作用 | 輸入 | 輸出 |
|------|------|------|------|
| `binwalk -e` | 解包韌體（FV/PE/壓縮） | .bin | 解壓縮目錄 |
| `uefi_firmware` | GUID 層級 FFS 解析 | .bin | 依 GUID 分類的 .efi 檔案 |
| `uefi_firmware` delta script | 模組 hash 比對 | 兩個 .bin | delta 報告（新增/移除/修改） |
| BinDiff | Function-level binary diff | 兩個 .BinExport | 函式相似度表 + diff view |
| Diaphora | 同上（開源，接 IDA） | 兩個 IDA DB | .sqlite diff 結果 |
| `strings` + entropy | 靜態後門搜尋 | .bin / .efi | 可疑字串 + 高熵區域 |
| Ghidra scripting | pattern 搜尋（恆真 return） | .efi | 可疑 function 清單 |

---

## 動手：對兩版 OVMF 或 U-Boot 做模組級 diff

### 環境準備

```bash
# ── 真跑 ──
# 取得兩版 OVMF（用 apt 安裝的 current 版，和從 tianocore release 取舊版）

# 安裝當前版 OVMF
sudo apt-get install -y ovmf
ls /usr/share/OVMF/OVMF_CODE.fd   # 這是 v_current

# 下載舊版 OVMF（tianocore release，以 202311 vs 202402 為例）
wget https://github.com/tianocore/edk2/releases/download/edk2-stable202311/OVMF.fd \
     -O /tmp/OVMF_v202311.fd
# current 版作為 v2
cp /usr/share/OVMF/OVMF_CODE.fd /tmp/OVMF_v_current.fd

pip3 install uefi_firmware
```

### 執行模組層級比對

```bash
# ── 真跑 ──
# 解包兩版
mkdir -p /tmp/ovmf_diff/old /tmp/ovmf_diff/new

python3 << 'EOF'
import uefi_firmware, os, hashlib

def dump_fw(fw_path, out_dir):
    with open(fw_path, 'rb') as f:
        data = f.read()
    parser = uefi_firmware.AutoParser(data)
    result = parser.parse()
    if result:
        result.dump(out_dir)
        print(f"[+] 解包 {fw_path} → {out_dir}")
    else:
        print(f"[!] 解包失敗：{fw_path}")

dump_fw('/tmp/OVMF_v202311.fd', '/tmp/ovmf_diff/old')
dump_fw('/tmp/OVMF_v_current.fd', '/tmp/ovmf_diff/new')
EOF

# 計算各 .efi 模組的 sha256，比對差異
python3 << 'EOF'
import os, hashlib

def get_modules(base_dir):
    modules = {}
    for root, dirs, files in os.walk(base_dir):
        for f in files:
            if f.endswith(('.efi', '.pe32', '.te')):
                path = os.path.join(root, f)
                rel = os.path.relpath(path, base_dir)
                with open(path, 'rb') as fh:
                    content = fh.read()
                sha = hashlib.sha256(content).hexdigest()[:12]
                modules[rel] = (len(content), sha)
    return modules

old = get_modules('/tmp/ovmf_diff/old')
new = get_modules('/tmp/ovmf_diff/new')

print("=== OVMF 模組 delta ===")
all_keys = set(old.keys()) | set(new.keys())
changed = 0
for k in sorted(all_keys):
    if k not in old:
        print(f"[新增] {k}  size={new[k][0]}")
        changed += 1
    elif k not in new:
        print(f"[移除] {k}  size={old[k][0]}")
        changed += 1
    elif old[k][1] != new[k][1]:
        diff = new[k][0] - old[k][0]
        print(f"[修改] {k}  {old[k][0]} → {new[k][0]} ({'+' if diff>=0 else ''}{diff})")
        changed += 1
print(f"\n共 {changed} 個模組有差異")
EOF
```

### 對有差異的模組做 Ghidra 分析

```bash
# ── 真跑 ──
# 假設發現 SecurityStubDxe.efi 有差異，提取它
# （路徑從上面的 delta 報告確認）

# 把兩個版本的 .efi 拿出來
OLD_MODULE=$(find /tmp/ovmf_diff/old -name '*SecurityStub*' 2>/dev/null | head -1)
NEW_MODULE=$(find /tmp/ovmf_diff/new -name '*SecurityStub*' 2>/dev/null | head -1)

echo "舊版: $OLD_MODULE"
echo "新版: $NEW_MODULE"

# 查看 size diff
wc -c "$OLD_MODULE" "$NEW_MODULE"

# 用 strings 找可疑差異
strings "$OLD_MODULE" | sort > /tmp/strings_old.txt
strings "$NEW_MODULE" | sort > /tmp/strings_new.txt
diff /tmp/strings_old.txt /tmp/strings_new.txt
# 新版多了什麼字串？少了什麼？
```

---

## 踩雷

1. **binwalk 對 UEFI FV 的 GUID 解析不可靠**：binwalk 做的是 binary signature 掃描，不是 UEFI FFS 結構解析。複雜的 FV（尤其是嵌套 FV）用 binwalk 很容易漏掉模組。一定要用 `uefi_firmware` 或 `UEFITool NE`（command line 版）做第二次確認。

2. **模組路徑相同不代表是同一個 GUID**：`uefi_firmware` dump 的目錄名稱取自模組在 FFS 的相對位置，兩版韌體的同位置可能已經是不同 GUID 的模組（廠商調整過 FV layout）。比對要用 GUID，不要用路徑。

3. **BinDiff/Diaphora 對 stripped binary 效果差**：UEFI binary 通常是 stripped PE（沒有 symbol table），BinDiff 的函式 matching 全靠 call graph topology 和 basic block hash，accuracy 大概 60-80%。少數函式會 match 錯。看 diff 時對「相似度特別低但你覺得奇怪」的 match 要手動回頭逆向確認。

4. **恆真 return 不一定是後門**：`SecurityStubDxe` 本身就是 edk2 的一個「stub 模組」，它的許多函式就是直接 return EFI_SUCCESS 或 EFI_UNSUPPORTED。找到 XOR EAX,EAX; RET 不要急著報 backdoor，要看是哪個 protocol 的哪個函式、這個 function 應不應該做事。

5. **供應鏈植入的 GUID 會是合法 GUID**：供應鏈攻擊者不會用一個奇怪的隨機 GUID（那太好找了）。他們會複製一個合法模組的 GUID，替換它的 body。比對的關鍵是「same GUID，same version，different hash」。

6. **熵值分析的假陽性很高**：壓縮資料、真正的 RSA key、加密後的 PE image 都是高熵。找到高熵區域後要搭配 context 判斷：周圍是什麼？有沒有對應的 key loading 程式碼？不要只看熵值就下結論。

---

## 進階延伸

- **Bindiff plugin for Ghidra**：官方 Google BinDiff 有 Ghidra 版，比 Diaphora+IDA 組合更容易取得（IDA 需授權）。GitHub `google/bindiff` 的 release 裡有 Ghidra extension zip。
- **Binarly FACT（Firmware Analysis and Comparison Tool）**：Binarly 的雲端韌體分析平台（部分免費），upload 韌體後自動做 GUID diff、SBOM、已知 CVE 比對。是研究者的快速掃描入口。
- **UEFI Symbol 重建**：對 stripped UEFI binary 做 BinDiff 之前，先用 `tianocore_efitools` 或 Ghidra UEFI plugin（`efiSeek`）嘗試恢復 EFI protocol 的函式名稱，大幅提升 BinDiff 的 matching quality。
- **patch-diffing 的 CVE 資料庫對齊**：找到修改後，去 Binarly 的 GitHub（`binarly-research`）和 UEFI firmware CVE database（`https://www.kb.cert.org`）比對，確認是否已有對應 CVE 編號或已知修補。

---

## 本章重點

- 模組層級 diff（GUID + hash）是 patch-diffing 的第一步，讓你從整個韌體定位到有差異的幾個模組
- `uefi_firmware` 比 `binwalk` 更適合 UEFI FFS 結構解析
- BinDiff / Diaphora 做 function-level diff，從相似度低的函式對找修補點
- patch-diffing 的核心：從「新增的 check」推出「check 之前的 bug」
- 後門四大 pattern：magic value、硬編碼憑證、恆真驗簽分支、debug 遺留
- 供應鏈植入特徵：same GUID + same version + different hash
- 熵值分析和 strings 是粗篩，不是確證；粗篩後要逆向確認

---

## 自我檢核

- [ ] 能用 `uefi_firmware` 解包兩版 BIOS，輸出 GUID 清單，並自動比對差異
- [ ] 知道 BinDiff 和 Diaphora 的輸入格式和基本 workflow（.BinExport / .sqlite）
- [ ] 能解釋 patch-diffing 的邏輯：「新增 bounds check → 推出舊版 bug」
- [ ] 知道後門的四個 pattern，並能說出每個在逆向工具裡的搜尋方法
- [ ] 能用 `strings + entropy` 做初步後門掃描，知道假陽性很高需要後續逆向
- [ ] 能說出供應鏈植入和開發遺留後門的主要差異，以及偵測時的關鍵指標

---

## 延伸閱讀

1. **"LogoFAIL: Security Implications of Image Parsing During System Boot" — Binarly（2023）**
   讀哪裡：Binarly blog（`binarly.io/blog`）和 Black Hat Europe 2023 白皮書
   學什麼：patch-diffing 的完整實戰案例：怎麼從 BIOS 更新 delta 找到 image parser 的 OOB bug
   關聯：直接對應本章「patch-diffing 反推 CVE」一節，是目前最好的教學案例

2. **Diaphora 官方文件與 Joxean Koret 的相關演講**（GitHub `joxeankoret/diaphora`）
   讀哪裡：repo 的 `docs/` 目錄和 `USAGE.md`；作者在 ZeroNights/Recon 的演講錄影
   學什麼：Diaphora 的 feature vector 設計（structural、opcode-based、CFG hash）；如何在 stripped binary 上提升 match quality
   關聯：直接接本章 binary diff 一節，是 BinDiff 的開源替代方案

3. **"Firmware Supply Chain Integrity" — NIST SP 800-193（BIOS/UEFI Protection Guideline）**
   讀哪裡：`nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-193.pdf`，重點看 Section 4（Firmware Resilience）
   學什麼：供應鏈植入的風險模型和偵測建議；NIST 定義的「detect」層（對應本章後門偵測角度）
   關聯：把本章的攻擊視角對接防守文件框架，對寫安全報告或參加 PSIRT 工作很有用

→ [下一章](./27-firmware-emulation.md)
