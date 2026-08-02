# 練習 D — UEFITool 拆 + Ghidra 逆一個 DXE 模組

> **目標**：完成 Part 4（韌體逆向）的綜合實作。用 `binwalk` + `uefi_firmware` 從 OVMF 拆出 FV/FFS 結構、列出所有模組 GUID；選定一個 DXE driver（SecurityStubDxe 或其他 protocol driver）；載入 Ghidra 還原 entry point 和 `gBS` protocol 呼叫；分析該模組的功能與潛在攻擊面；最後用提供的模板輸出一份正式逆向工程報告。

---

## 前置知識

- 讀完 Ch 22（韌體取得與解包）、Ch 23（UEFITool 與 FV/FFS）、Ch 24（Ghidra 逆 UEFI）
- 知道 UEFI DXE driver 的基本結構：entry point `EFI_STATUS EFIAPI DriverEntry(EFI_HANDLE, EFI_SYSTEM_TABLE*)` 和 `gBS`/`gRT` 的用途
- WSL Ubuntu 22.04 環境、已安裝 QEMU + OVMF（`sudo apt-get install -y ovmf qemu-system-x86`）
- Ghidra 已安裝（`https://ghidra-sre.org`）

---

## 工具清單

| 工具 | 版本 / 來源 | 用途 |
|------|-----------|------|
| `binwalk` | `sudo apt-get install binwalk` | 初步掃描韌體結構 |
| `uefi_firmware` | `pip3 install uefi_firmware` | FFS 層級解包，取 GUID |
| `uefi-firmware-parser` | 同上（CLI） | 命令列 GUID 列表 |
| `UEFITool NE` | GitHub `LongSoft/UEFITool` | GUI 觀察 FV 樹狀結構（選用）|
| Ghidra | 10.x 或更新 | 反組譯 DXE driver |
| efiSeek plugin | GitHub `DSecurity/efiSeek` | Ghidra 的 UEFI-aware 分析（選用）|
| `python3` | WSL 內建 | 輔助腳本 |

---

## 階段一：binwalk + uefi_firmware 拆出 FV/FFS 結構

### Step 1：確認 OVMF 存在

```bash
# ── 真跑 ──
ls -lh /usr/share/OVMF/OVMF_CODE.fd
# -rw-r--r-- 1 root root 2.0M ... OVMF_CODE.fd
# 若沒有：sudo apt-get install -y ovmf

# 複製到工作目錄
mkdir -p ~/fw_re_lab
cp /usr/share/OVMF/OVMF_CODE.fd ~/fw_re_lab/OVMF.fd
ls -lh ~/fw_re_lab/OVMF.fd
```

### Step 2：binwalk 初步掃描

```bash
# ── 真跑 ──
cd ~/fw_re_lab
binwalk OVMF.fd

# 預期輸出（節錄）：
# DECIMAL   HEXADECIMAL   DESCRIPTION
# -------   -----------   -----------
# 0         0x0           UEFI PI Firmware Volume, volume size: 282624 (0x45000)
# 282624    0x45000       UEFI PI Firmware Volume, volume size: 0x ...
# ...
# binwalk 識別 FV 區塊，但不展開 FFS 內容

# 做 entropy 掃描（識別壓縮 / 加密區域）
binwalk -E OVMF.fd -o entropy.png
# 生成 entropy 圖，高熵區域可能是壓縮的 FV 或 PE image
```

### Step 3：uefi_firmware 解包（取 GUID）

```bash
# ── 真跑 ──
pip3 install uefi_firmware

# 列出所有 GUID（不解包，只掃描）
uefi-firmware-parser --brute OVMF.fd 2>/dev/null | grep -E 'GUID|File|Volume' | head -60

# 完整解包（輸出到目錄）
mkdir -p ~/fw_re_lab/ovmf_unpacked
python3 << 'EOF'
import uefi_firmware

with open('/root/fw_re_lab/OVMF.fd', 'rb') as f:
    data = f.read()

parser = uefi_firmware.AutoParser(data)
firmware = parser.parse()
if firmware is None:
    print("[!] 解析失敗，嘗試 brute-force 掃描")
else:
    firmware.dump('/root/fw_re_lab/ovmf_unpacked')
    print("[+] 解包完成")
EOF

# 列出所有解包出來的 .efi 和 .pe32 檔案
find ~/fw_re_lab/ovmf_unpacked -name '*.efi' -o -name '*.pe32' | sort
```

### Step 4：建立 GUID 清單並尋找目標模組

```bash
# ── 真跑 ──
# 生成 GUID 清單（含 path 和 size）
python3 << 'EOF'
import os, hashlib

base = os.path.expanduser('~/fw_re_lab/ovmf_unpacked')
print(f"{'GUID / 路徑':<60} {'大小':>8}  {'SHA256[:12]'}")
print("-" * 85)
count = 0
for root, dirs, files in os.walk(base):
    for f in sorted(files):
        if f.endswith(('.efi', '.pe32', '.te')):
            path = os.path.join(root, f)
            size = os.path.getsize(path)
            with open(path, 'rb') as fh:
                sha = hashlib.sha256(fh.read()).hexdigest()[:12]
            rel = os.path.relpath(path, base)
            print(f"{rel:<60} {size:>8}  {sha}")
            count += 1
print(f"\n共 {count} 個模組")
EOF

# 找特定模組：SecurityStubDxe
find ~/fw_re_lab/ovmf_unpacked -iname '*security*'
# 或用 GUID 搜尋（SecurityStubDxe 的 GUID = f80697e9-7fd6-4665-8646-88e33ef71dfc）
find ~/fw_re_lab/ovmf_unpacked -path '*f80697e9*'
```

---

## 階段二：選定目標 DXE 模組

### 推薦目標：SecurityStubDxe

`SecurityStubDxe` 是 edk2 的一個「stub」實作，提供 `EFI_SECURITY_ARCH_PROTOCOL` 和 `EFI_SECURITY2_ARCH_PROTOCOL`。這兩個 protocol 是 UEFI Secure Boot 的核心驗簽 hook——DXE Core 在 load 每個 PE image 前會呼叫這些 protocol。

為什麼選它：
- 和 Secure Boot 直接相關（攻擊面明確）
- 體積小（容易完整分析）
- 有 edk2 source code 對照（`MdeModulePkg/Universal/SecurityStubDxe/SecurityStub.c`）

替代目標（若 SecurityStubDxe 找不到）：
- `VariablePolicyDxe`：管理 UEFI variable 的存取策略，也和 Secure Boot 有關
- `UefiBootManagerLib`：管理 Boot Order，是 bootkit 最常 hook 的地方

### 確認目標模組

```bash
# ── 真跑 ──
# 假設找到路徑（依你的系統調整）
TARGET=$(find ~/fw_re_lab/ovmf_unpacked -iname '*security*stub*' 2>/dev/null | head -1)
echo "目標模組: $TARGET"
ls -lh "$TARGET"

# 確認是 PE32
file "$TARGET"
# 預期：PE32+ executable (EFI application) x86-64

# 複製到工作目錄，命名清楚
cp "$TARGET" ~/fw_re_lab/SecurityStubDxe.efi
```

---

## 階段三：Ghidra 逆向——還原 entry point 與 gBS 呼叫

**注意**：Ghidra 是 GUI 工具，以下為步驟說明與預期觀察重點。Ghidra 操作在 WSL 的 GUI 或 Windows 原生都可以；若在 WSL 無法開啟 GUI，把 `SecurityStubDxe.efi` 複製到 Windows 側用 Ghidra 分析。

### Step 1：建立 Ghidra 專案，匯入 .efi

```
1. 啟動 Ghidra → New Project → Non-Shared Project → 命名 "uefi_re_lab"
2. File → Import File → 選 SecurityStubDxe.efi
3. Format 自動偵測為 "Portable Executable (PE)"，Language 選 x86:LE:64:default:windows
4. OK → 點兩下開啟 CodeBrowser
5. 詢問是否自動分析 → Yes（用預設設定即可）
   分析時間：小模組通常 5-30 秒
```

### Step 2：efiSeek plugin（選用，大幅提升分析質量）

```
若有安裝 efiSeek Ghidra plugin：
  Script Manager → 搜 "efiSeek" → 執行
  它會自動：
  ① 識別 gBS / gRT（從已知 GUID 比對）
  ② 把 gBS->xxx 呼叫標上正確的函式名稱
  ③ 識別 EFI protocol GUID

若沒有 efiSeek，手動做：（下面的步驟說明手動方法）
```

### Step 3：找 entry point

```
Window → Functions → 搜 "entry"
或：在 Symbol Table 找 entry（PE 的 .entry）

SecurityStubDxe 的 entry point 簽名：
  EFI_STATUS EFIAPI SecurityStubInitialize(
    IN EFI_HANDLE        ImageHandle,
    IN EFI_SYSTEM_TABLE  *SystemTable
  )

預期 Ghidra 顯示（偽代碼）：
  EFI_STATUS SecurityStubInitialize(longlong ImageHandle, longlong *SystemTable)
  {
    // 通常是 InitializeLib 或 直接從 SystemTable 取 gBS
    ...
    Status = gBS->InstallMultipleProtocolInterfaces(&gImageHandle, ...);
    ...
    return EFI_SUCCESS;
  }
```

### Step 4：識別 gBS（EFI_BOOT_SERVICES）

```
方法一（手動）：
  UEFI entry point 的第二個參數是 EFI_SYSTEM_TABLE*
  EFI_SYSTEM_TABLE 的 offset 0x60 = EFI_BOOT_SERVICES*（BootServices field）
  
  在 Ghidra 的 Data Type Manager 匯入 UEFI 的 .gdt 資料類型定義檔
  （edk2 有 Ghidra 用的 gdt 檔，或使用 efiSeek 自動匯入）
  
  手動標型別：選取 SystemTable 參數 → 右鍵 → Set Data Type → 輸入 EFI_SYSTEM_TABLE
  Ghidra 會自動展開結構，gBS 欄位就出來了

方法二（從 global offset 觀察）：
  edk2 的 MdePkg 在 entry point 之後通常有：
    gST = SystemTable;
    gBS = SystemTable->BootServices;
    gRT = SystemTable->RuntimeServices;
  在 Ghidra 的偽代碼裡找 `*(SystemTable + 0x60)` 的賦值
  那個 global 就是 gBS
```

### Step 5：追蹤 protocol 安裝

```
SecurityStubDxe 的核心動作是安裝兩個 protocol：
  EFI_SECURITY_ARCH_PROTOCOL_GUID
  EFI_SECURITY2_ARCH_PROTOCOL_GUID

在 Ghidra 偽代碼中，找到 gBS->InstallMultipleProtocolInterfaces 的呼叫：
  (*gBS->InstallMultipleProtocolInterfaces)(
    &handle,
    &gEfiSecurityArchProtocolGuid,     // GUID 是 a46423e3-4617-49f1-b9ff-d1bfa9115839
    &gSecurityStub,                     // protocol instance
    &gEfiSecurity2ArchProtocolGuid,    // GUID 是 94ab2f58-1438-4ef1-9152-18941a3a0e68
    &gSecurity2Stub,
    NULL
  );

找到 gSecurityStub 和 gSecurity2Stub 指向的結構：
  這兩個是 EFI_SECURITY_ARCH_PROTOCOL 和 EFI_SECURITY2_ARCH_PROTOCOL 的實例
  它們各有一個 function pointer（FileAuthenticationState 和 FileAuthentication）
```

### Step 6：分析 FileAuthentication 函式

```
EFI_SECURITY2_ARCH_PROTOCOL.FileAuthentication 是 UEFI 驗簽的核心 hook
  每次 DXE Core 載入一個 PE image 前呼叫這個函式
  安全的實作應該：驗證簽章 → 拒絕未授權的 image

SecurityStubDxe 的實作（stub 版）：
  EFI_STATUS EFIAPI Security2StubAuthenticate(
    IN CONST EFI_SECURITY2_ARCH_PROTOCOL *This,
    IN CONST EFI_DEVICE_PATH_PROTOCOL    *File,
    IN VOID                              *FileBuffer,
    IN UINTN                             FileSize,
    IN BOOLEAN                           BootPolicy
  )
  {
    return EFI_SUCCESS;   // ← 恆真！永遠允許！
  }

這就是 Ch 26 提到的「恆真驗簽分支（Pattern A）」的真實案例：
  SecurityStubDxe 故意設計成「不驗，全部放行」（因為是 stub）
  但如果量產 BIOS 用 SecurityStubDxe 而不是真正的 SecurityDxe，
  整個 Secure Boot 信任鏈就是空的
```

---

## 階段四：逆向報告撰寫

### 報告模板

```markdown
# DXE 模組逆向分析報告

**分析日期**：YYYY-MM-DD  
**分析人**：（你的名字）  
**報告版本**：1.0  

---

## 1. 目標說明

| 項目 | 內容 |
|------|------|
| 模組名稱 | SecurityStubDxe |
| GUID | f80697e9-7fd6-4665-8646-88e33ef71dfc |
| 來源 | OVMF_CODE.fd（Ubuntu 22.04 apt-get 版本）|
| 檔案大小 | xxx bytes |
| SHA256 | xxxxxxxxxxxx |
| 對應 edk2 源碼 | MdeModulePkg/Universal/SecurityStubDxe/SecurityStub.c |

---

## 2. 分析方法

**工具**：
- `uefi_firmware` 解包 OVMF，取 GUID 列表
- `binwalk` 初步結構掃描
- Ghidra 10.x 反組譯 + 偽代碼還原
- （選用）efiSeek Ghidra plugin 自動 UEFI 類型識別

**分析流程**：
1. binwalk 掃描 OVMF → 識別 FV 邊界
2. uefi_firmware 解包 → 取出所有模組 GUID
3. 定位 SecurityStubDxe（GUID 比對）
4. Ghidra 載入 → 自動分析 → 人工標注型別
5. 追蹤 entry point → 識別 protocol 安裝 → 分析 handler 實作

---

## 3. 模組功能發現

### 3.1 Entry Point 行為

**函式名稱**：`SecurityStubInitialize`（由 Ghidra 根據 edk2 symbol 或人工標注）  
**位於 VA**：（填入 Ghidra 顯示的虛擬地址）

**主要動作**：
1. 從 `SystemTable->BootServices` 取 gBS
2. 呼叫 `gBS->InstallMultipleProtocolInterfaces` 安裝兩個 protocol
3. 回傳 `EFI_SUCCESS`

### 3.2 安裝的 Protocol

| Protocol | GUID | 安裝 Handler |
|---------|------|------------|
| EFI_SECURITY_ARCH_PROTOCOL | a46423e3-4617-49f1-b9ff-d1bfa9115839 | `SecurityStubAuthenticate` |
| EFI_SECURITY2_ARCH_PROTOCOL | 94ab2f58-1438-4ef1-9152-18941a3a0e68 | `Security2StubAuthenticate` |

### 3.3 Handler 行為分析

**Security2StubAuthenticate（核心發現）**：
- 輸入：file path、file buffer、file size、boot policy
- 實作：直接 `return EFI_SUCCESS`，無任何驗簽邏輯
- 這是 stub 設計：故意放行所有 image

**SecurityStubAuthenticate**：
- 類似，直接 return EFI_SUCCESS

### 3.4 其他發現

（填入你在分析中發現的其他行為：全域變數使用、callback 安裝、NVRAM 讀取等）

---

## 4. 攻擊面評估

### 4.1 直接風險（本模組）

| 風險 | 嚴重度 | 說明 |
|------|--------|------|
| 若被部署在量產 BIOS | **Critical** | 任何未簽章的 PE image 都能被載入，Secure Boot 完全無效 |
| SecurityStubDxe 被後門替換 | High | 後門版本的 stub 可以 log 所有被載入的 image、植入惡意 callback |
| Protocol handler pointer 被 SMM exploit 替換 | High | gSecurityStub 的 function pointer 若能被 SMM 覆寫，等同後門 |

### 4.2 攻擊情境

**情境 A：誤用 SecurityStubDxe 在量產**  
BIOS 開發商在 debug build 中用 SecurityStubDxe（方便測試），忘記在量產 build 換成真正的 `SecurityDxe`（含完整驗簽邏輯）。結果：量產機的 Secure Boot 是假的。

**情境 B：供應鏈替換**  
攻擊者在 OEM 的 BIOS 供應鏈中，把真正的 `SecurityDxe.efi` 替換成修改過的版本，讓 `FileAuthentication` 在特定 magic 條件下 return `EFI_SUCCESS`（後門 magic knock）。外觀上 GUID 相同、功能基本相同，只多了一個 if 分支。

### 4.3 防禦建議

| 建議 | 類型 |
|------|------|
| 量產 build 確認用 `SecurityDxe` 而非 `SecurityStubDxe` | 流程 |
| CI/CD pipeline 的 firmware 驗測：確認 `EFI_SECURITY2_ARCH_PROTOCOL.FileAuthentication` 不是 stub（測試注入未簽章 EFI 應被拒絕）| 自動化測試 |
| firmware hash 簽章（整個 OVMF.fd 的 signature）進版本控制 | 供應鏈 |

---

## 5. 與 edk2 源碼比對

**源碼位置**：`edk2/MdeModulePkg/Universal/SecurityStubDxe/SecurityStub.c`

比對確認的重點：
- entry point 函式名稱和邏輯與源碼一致
- GUID 值與 `SecurityStubDxe.inf` 的 `ENTRY_POINT` 和 `FILE_GUID` 一致
- `FileAuthentication` 的 `return EFI_SUCCESS` 在源碼確認
- 沒有發現額外的「不在源碼裡的邏輯」→ 排除後門植入嫌疑

**diff 方法**：
- 從 edk2 GitHub 取得對應版本（依 OVMF build date 對應 edk2 tag）
- `gcc -o SecurityStubDxe_ref.efi ...`（edk2 build）
- SHA256 比對：`sha256sum SecurityStubDxe.efi SecurityStubDxe_ref.efi`
- 若 hash 不同但 edk2 版本相同 → 可疑（可能有 patch 或供應鏈植入）

---

## 6. 結論

SecurityStubDxe 是一個設計上「故意不驗簽」的 stub 模組，用途是在 UEFI 開發/測試環境中讓 image loading 不受 Secure Boot 阻擋。其 `FileAuthentication` 的恆真 return 是設計意圖，非 bug。

**本次分析的實際意義**：
- 確認了「stub 模組的存在本身是一個攻擊面」——任何用 stub 替換 real security driver 的情形都值得 flag
- 建立了 UEFI DXE driver 逆向的基本流程（entry point → gBS 取得 → protocol 安裝 → handler 分析）
- 驗證了 edk2 參考 build 與 OVMF apt 版本的模組一致性
```

---

## 驗收 Checklist

完成本練習後，確認以下項目已完成：

**Phase 1：解包**
- [ ] `binwalk OVMF.fd` 跑通，識別出至少 2 個 UEFI Firmware Volume
- [ ] `uefi_firmware` 成功解包，在 `~/fw_re_lab/ovmf_unpacked` 有 `.efi` 檔案
- [ ] 能輸出 GUID 清單（至少 20 個模組）
- [ ] 找到 SecurityStubDxe.efi（或替代目標模組）

**Phase 2：Ghidra**
- [ ] 成功匯入 SecurityStubDxe.efi，Ghidra 完成自動分析
- [ ] 在 Functions 視窗找到 entry point 函式
- [ ] 在偽代碼視窗識別出 `gBS->InstallMultipleProtocolInterfaces` 的呼叫
- [ ] 找到 `Security2StubAuthenticate` 函式，確認它直接 return EFI_SUCCESS

**Phase 3：報告**
- [ ] 填寫報告的「目標說明」欄（GUID、大小、SHA256）
- [ ] 填寫「安裝的 Protocol」表（兩個 GUID 和對應 handler）
- [ ] 填寫「攻擊面評估」（至少一個攻擊情境）
- [ ] 嘗試在 edk2 GitHub 找到 SecurityStub.c，比對 handler 邏輯
- [ ] 完成一份完整的 RE 報告（參照模板，填滿所有欄位）

---

## 繳交物

1. **`~/fw_re_lab/GUID_list.txt`**：完整 GUID 清單（ `python3 fw_delta.py` 或 `uefi-firmware-parser` 輸出）
2. **`~/fw_re_lab/SecurityStubDxe.efi`**：從 OVMF 解出的目標模組
3. **`~/fw_re_lab/SecurityStubDxe_re_report.md`**：填寫完整的逆向分析報告
4. **（選用）Ghidra project export**：`.gar` 或截圖記錄分析的 key findings（偽代碼 + 標注）

---

## 如果卡住

### 問題：`uefi_firmware` 解包結果是空目錄

OVMF 的部分 FV 使用 LZMA 或 EFI 壓縮，`uefi_firmware` 的某些版本不支援自動解壓。

解法一：用 `--brute` 讓 uefi-firmware-parser 掃描更多邊界
```bash
uefi-firmware-parser --brute --extract OVMF.fd -o ~/fw_re_lab/ovmf_brute/
```

解法二：改用 UEFITool NE（更強的解壓支援）
```bash
# 下載 UEFIExtract（UEFITool NE 的 CLI 工具）
# https://github.com/LongSoft/UEFITool/releases
# 找 UEFIExtract_NE_* 版本
./UEFIExtract OVMF.fd all    # 解包所有元件
ls -la OVMF.fd.dump/         # 輸出在 .dump 目錄
```

### 問題：Ghidra 看不出 gBS 的呼叫

手動追蹤：
1. entry point 的第二個參數（`RDX`）是 `EFI_SYSTEM_TABLE*`
2. `EFI_SYSTEM_TABLE.BootServices` 在 offset `0x60`
3. 在偽代碼裡找 `*(param_2 + 0x60)` 或 `*(rdx + 0x60)` → 那就是 gBS
4. 右鍵 → Set Data Type → `EFI_BOOT_SERVICES *`
5. 之後所有透過 gBS 的呼叫都會正確顯示

### 問題：找不到 SecurityStubDxe，GUID 不匹配

不同版本的 OVMF 可能有不同的模組集合。替代方案：
- 找 `UefiBootManagerLib` 相關的模組（幾乎所有版本都有）
- 找 `VariablePolicyDxe`（GUID `DA6974A5-D9A4-4B3C-A965-4C8B82B7F51C`）
- 或直接選 GUID 清單裡最小的模組開始練習（體積小，分析快）

---

## 延伸挑戰

完成基本任務後，試試以下延伸：

### 延伸一：分析 SecurityDxe（真正有驗簽邏輯的版本）

`SecurityPkg/Library/DxeImageAuthenticationStatusLib/` 和 `SecurityPkg/Library/DxeImageVerificationLib/` 含有真正的 Secure Boot 驗簽邏輯。在 edk2 的 debug build 裡找到 `SecurityDxe.efi`，用同樣方法逆向，比對和 SecurityStubDxe 的差距——那個差距就是「Secure Boot 驗簽的核心邏輯」。

### 延伸二：用 Ghidra scripting 自動找所有 stub handler

```python
# Ghidra Python script（在 Script Manager 執行）
# 找所有「直接 return EFI_SUCCESS（0）」的小函式
from ghidra.program.model.listing import FunctionIterator

functions = currentProgram.getFunctionManager().getFunctions(True)
EFI_SUCCESS = 0

for func in functions:
    body = func.getBody()
    # 檢查函式是否很小（< 20 bytes）且只有 return
    if body.getNumAddresses() < 20:
        # 找到這個函式後用 decompile 確認
        print(f"小函式: {func.getName()} @ {func.getEntryPoint()}")
```

### 延伸三：QEMU 動態確認

把分析結果和動態觀察結合（接 Ch 27 的 QEMU+GDB 環境）：

```bash
# 在 QEMU 的 GDB session 裡設斷點在 Security2StubAuthenticate
# 的 return 語句，確認每次 DXE Core 載入 image 時都會經過這裡
# （證明這個函式真的是 Secure Boot 的把關點，哪怕是個 stub）

(gdb) # 先找到 Security2StubAuthenticate 的地址
(gdb) # 用本章 Phase 3 的 GDB 技巧搜尋 MZ header
(gdb) # 找到 SecurityStubDxe 的 base，加上 Ghidra 給的 RVA 算出 VA
(gdb) hbreak *0x7FXXXABC   # 在 return EFI_SUCCESS 設斷點
(gdb) continue
# 開機時每次載入 DXE driver，GDB 都應該在這裡停下
# 確認 RAX（回傳值暫存器）= 0（EFI_SUCCESS）
```

---

## 本練習重點

- UEFI DXE driver 的逆向工程有固定的 pattern：entry point → gBS 定位 → protocol 安裝 → handler 分析
- `uefi_firmware` + `binwalk` 是解包的第一步，UEFITool NE 是備援
- Ghidra 的 UEFI 分析關鍵：匯入正確的資料類型定義（UEFI .gdt），才能讓 gBS 呼叫有意義的名稱
- SecurityStubDxe 的「恆真 return」是 Ch 26 後門 pattern 的最佳教材案例——只是這次是「設計意圖」而非惡意
- 逆向報告的核心三點：功能是什麼、與源碼是否一致、攻擊面在哪
- 動態確認（Ch 27 的 GDB）是靜態分析的補充；有條件就兩者結合

---

## 延伸閱讀

1. **edk2 `MdeModulePkg/Universal/SecurityStubDxe/SecurityStub.c`**（tianocore/edk2 GitHub）
   讀哪裡：直接在 GitHub 上讀這個檔案，對照你的 Ghidra 偽代碼
   學什麼：edk2 stub 的設計邏輯；`EFI_SECURITY_ARCH_PROTOCOL` 和 `EFI_SECURITY2_ARCH_PROTOCOL` 的 interface 定義；真正的 SecurityDxe 是 `SecurityPkg` 的哪個模組
   關聯：本練習的核心對照材料，直接驗證你的逆向結果是否正確

2. **"efiSeek: Find UEFI Services in Ghidra" — DSecurity**（GitHub `DSecurity/efiSeek`）
   讀哪裡：repo README 和 wiki，了解 plugin 的能力和安裝方式
   學什麼：Ghidra plugin 如何自動識別 gBS/gRT 呼叫和 UEFI GUID；哪些分析步驟可以自動化；plugin 的原理（GUID database matching）
   關聯：大幅降低本練習 Step 4 的人工成本；是做 UEFI RE 的必裝工具

3. **"Attacking UEFI and Linux" — Black Hat USA 2015，Rafal Wojtczuk 等**
   讀哪裡：slides 在 Black Hat archive 可以搜到（搜標題 + 2015）；
   Eclypsium blog 也有後續整理文
   學什麼：SecurityDxe 和 SecurityStubDxe 的差異在真實攻擊中的意義；UEFI SMM 如何替換 `EFI_SECURITY2_ARCH_PROTOCOL.FileAuthentication` pointer；DXE protocol pointer 覆寫攻擊
   關聯：把本練習的分析結果直接連到可行的攻擊手法，完成「逆向 → 攻擊面 → 利用路徑」的閉環

→ [下一章](./28-secure-boot-internals.md)
