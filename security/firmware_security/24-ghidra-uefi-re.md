# Ch 24 — 用 Ghidra 逆 UEFI 模組

> **目標**：能把從 OVMF 或真實 BIOS 抽出的 TE/PE UEFI 模組載入 Ghidra，正確設定 base address 與 calling convention，套上 UEFI type library，還原 gBS/gRT/gST 呼叫、解析 protocol GUID、定位 `_ModuleEntryPoint`，並識別常見模式（LocateProtocol、InstallProtocolInterface、SMI handler 註冊）。最後走一遍對真實 DXE 模組的逆向流程。
> **環境**：Ghidra 是桌面工具，安裝在讀者本機（Windows/Linux 皆可）。本章所有截圖描述均為步驟指引，不假裝在 WSL 中執行。WSL 用於前置的 binary 提取（uefi_firmware、binwalk、fiptool）。

---

## 為什麼 UEFI RE 特別難

打開一個從 BIOS 抽出的 DXE 模組丟進 Ghidra，你會立刻踩到三個坑：

**坑一：重定位表沒套**。UEFI PE/TE 是位置無關的（理論上），但模組執行時 DXE Foundation 會把它搬到動態分配的位址。Ghidra 預設把 PE 載到 `0x1000`，你在 EFI SHELL 或 QEMU GDB 看到的實際 RVA 完全不同，導致動態分析對不上靜態。

**坑二：呼叫慣例不是標準 x86-64 System V 或 Microsoft ABI 的某一個**。UEFI 全程用 Microsoft x64 calling convention（前四個整數參數 RCX/RDX/R8/R9，XMM0-3 給浮點），但 Ghidra 預設分析 x86-64 ELF 時用 gcc-cdecl。載入 UEFI PE 如果沒有手動選 `__cdecl` 或更精準地選 `Windows x86-64 calling convention`，反編譯器的參數推導會完全跑歪。

**坑三：沒有符號**。UEFI 模組是 stripped binary，gBS / gRT / gST 是全域指標，在 binary 裡以 `DAT_xxxxxxxx` 的方式呈現；protocol GUID 是 128-bit 常數，靠 EFI protocol 資料庫才能反查名稱；`LocateProtocol` 的實際目標是透過 boot services table 的 function pointer 間接呼叫，Ghidra 初始分析看到的是 `CALL [RCX+0x140]`，不知道 `0x140` 是 `gBS->LocateProtocol`。

這三個問題的解法分別是：正確設定 image base、選對 calling convention、套上輔助腳本（ghidra-firmware-utils / efiXplorer-port），以及手動建 type library。本章把這些依序解決。

---

## UEFI binary 格式快速回顧

### PE32+ vs TE（Terse Executable）

UEFI 模組有兩種格式：

| 格式 | 用途 | Header 大小 | 特點 |
|------|------|-----------|------|
| PE32+ | SEC 以外的大多數模組 | 標準 DOS + PE 頭，約 512B | 與 Windows DLL 格式相同，Ghidra 原生支援 |
| TE | SEC / PEI 早期模組（記憶體緊張） | 40 bytes | 去掉 DOS stub，只保留 stripped PE 資訊；需要 Ghidra 外掛才能正確解析 |

TE header（`include/IndustryStandard/Lzma.h` 和 PI spec Vol 3）：

```
typedef struct {
  UINT16  Signature;            // 0x5A56 ('VZ')
  UINT16  Machine;              // 0x8664 = x86-64
  UINT8   NumberOfSections;
  UINT8   Subsystem;
  UINT16  StrippedSize;        // 被移除的 bytes 數（計算真實 RVA 時需要）
  UINT32  AddressOfEntryPoint; // 相對於 TE header start
  UINT32  BaseOfCode;
  UINT64  ImageBase;           // 一般是 0（run-time 決定）
  // 緊接著 section headers（同 PE，但數量少很多）
} EFI_TE_IMAGE_HEADER;
```

TE 的 RVA 計算：`虛擬地址 = 載入基址 + RVA_in_TE - StrippedSize + sizeof(EFI_TE_IMAGE_HEADER)`。漏掉這個修正，每個地址都會差一個 offset。

### FV / FFS / Section 層

從 OVMF.fd 到模組 binary 的路徑（Ch 23 詳述，這裡只列 RE 相關部分）：

```
OVMF.fd
  └── Firmware Volume (FV)        ← 可能多個 FV，用 UEFITool 找
        └── FFS File              ← 每個 driver 一個 FFS，由 GUID 識別
              ├── PE32 Section    ← 實際的 .efi 模組 binary
              ├── USER_INTERFACE  ← 模組名稱字串（有的沒有）
              └── VERSION         ← 版本資訊
```

用 UEFITool 或 `uefi_firmware` (Python) 抽出 PE32 Section 的 raw bytes，就是丟進 Ghidra 的 target。

---

## 前置：從 OVMF 抽出 DXE 模組（WSL 操作）

這段是真跑段落，在 WSL Ubuntu 22.04 執行：

```bash
# 安裝工具
pip3 install uefi-firmware
sudo apt-get install -y binwalk

# 取得 OVMF（若尚未有）
sudo apt-get install -y ovmf
cp /usr/share/OVMF/OVMF_CODE.fd ./OVMF_CODE.fd

# 用 uefi_firmware 列出 OVMF 內容
python3 -m uefi_firmware.analysis OVMF_CODE.fd --output ./ovmf_extract --extract

# 找一個有趣的 DXE 模組（例如 SecurityStubDxe）
find ./ovmf_extract -name "*.pe" -o -name "*section*" | head -30

# 也可以先用 binwalk 確認 PE/TE offset
binwalk -e OVMF_CODE.fd
```

更精準的方式是用 `fwhunt-scan` 或直接用 UEFITool NE（GUI 工具，在 Windows/Linux 本機執行）：

1. 開啟 UEFITool → File → Open image file → `OVMF_CODE.fd`
2. Ctrl+F → 搜尋模組名稱（如 `SecurityStubDxe` 或 GUID `AD608272-D07F-4964-801E-7BD3B7888652`）
3. 右鍵選中的 PE32 Section → Extract body → 存成 `SecurityStubDxe.efi`

這個 `.efi` 就是下面要分析的目標。

---

## Ghidra 載入設定

### 安裝輔助外掛：ghidra-firmware-utils

ghidra-firmware-utils（`https://github.com/al3xtjames/ghidra-firmware-utils`）提供：

- TE image loader（Ghidra 原生不支援 TE）
- FV / FFS 直接解析（不用手動抽 binary）
- UEFI helper 腳本整合

安裝方式：

1. 從 Releases 頁面下載最新 `.zip`
2. Ghidra → File → Install Extensions → 選 `.zip` → OK → 重啟 Ghidra

安裝後 Ghidra 就能直接把整個 `OVMF_CODE.fd` 當作 FV 開啟並列出所有 module，不需要先用 UEFITool 抽取。

### 新建 Project，Import PE32 模組

1. **File → New Project** → Non-Shared Project，命名 `uefi-re`
2. **File → Import File** → 選 `SecurityStubDxe.efi`
3. Import 對話框：
   - **Language**：選 `x86:LE:64:default:gcc` 或 `x86:LE:64:default:windows`
     - 重要：選 `windows` variant，Ghidra 才會預設使用 Microsoft x64 calling convention
   - **Format**：`Portable Executable (PE)` — Ghidra 應該自動偵測，確認沒有選到 ELF
4. **Options**：
   - `Load External Libraries`：不勾，UEFI 沒有 import table（所有 protocol 呼叫透過 function pointer）
   - `Apply Relocation Table`：勾，讓 Ghidra 套上重定位資訊

### 設定正確的 Image Base

UEFI PE 的 `ImageBase` 欄位通常是 `0` 或預設值（如 `0x10000000`），但執行時 DXE Foundation 會將模組重定位到實際位址。為了讓靜態分析與動態分析（GDB on QEMU）的地址能對上，有兩個選擇：

**選項 A：維持 0x10000（Ghidra 預設）**
適合純靜態分析，不需要對 GDB。缺點：要對上 QEMU GDB 的地址，每次都要手動計算 offset。

**選項 B：設成 QEMU 執行時的實際載入地址**
在 QEMU UEFI shell 中執行 `dmem` 或用 GDB 中斷後 `maintenance info sections`，找到模組被載入的實際 base address。Ghidra 裡：**Window → Memory Map → 點 Header** → 修改 `Base Address`。

動手逆一個模組時，推薦先用選項 A，等逆到某個有趣函式再切換到 QEMU GDB 模式做動態驗證。

### Calling Convention 設定

**Edit → Tool Options → Decompiler → Analysis → Prototype Evaluation**：設為 `__cdecl` 或更好地用 Ghidra 提供的 `windows` language 讓它自動選 Microsoft x64。

確認：開啟 Decompiler 視窗，如果看到函式簽名是 `undefined8 FUN_xxxxxxx(undefined8 param_1, undefined8 param_2, ...)`，且四個參數名稱後要加上來自 RCX/RDX/R8/R9 的標記，就是正確的。如果只看到 `param_1` 從 stack 取，就是 calling convention 設錯了。

---

## 套上 UEFI Type Library

type library 讓 Ghidra 知道 `EFI_BOOT_SERVICES`、`EFI_RUNTIME_SERVICES`、`EFI_SYSTEM_TABLE` 以及所有 protocol interface 的結構定義。有了這些，`CALL [RCX+0x140]` 就能被 Ghidra 反編譯成 `gBS->LocateProtocol(...)。

### 方法一：ghidra-firmware-utils 附帶的 GDT 檔

ghidra-firmware-utils 包含預建的 `efi.gdt`（Ghidra Data Type Archive），包含 MdePkg 的大量型別。

載入：**File → Parse C Source（或 Data Type Manager → Open File Archive）** → 選 `efi.gdt`。載入後在 Data Type Manager 左側展開 `efi`，可以看到 `EFI_BOOT_SERVICES`、`EFI_STATUS`、`EFI_GUID` 等型別都在。

### 方法二：手動匯入 EDK2 標頭

若需要最新的型別（新 protocol、PPI），從 edk2 source 的標頭手動 parse：

**File → Parse C Source Files**：

```
Include 路徑（按順序加入）：
  /path/to/edk2/MdePkg/Include/
  /path/to/edk2/MdePkg/Include/X64/
  /path/to/edk2/MdePkg/Include/Uefi/

Parse 檔案：
  /path/to/edk2/MdePkg/Include/Uefi/UefiSpec.h
  /path/to/edk2/MdePkg/Include/Protocol/BlockIo.h
  ... （視目標模組使用的 protocol 而定）
```

這會花幾分鐘，但 parse 完後的 data type 非常完整，特別是所有 GUID 結構。

---

## 定位 _ModuleEntryPoint

UEFI DXE driver 的入口是 `_ModuleEntryPoint`，由 EDK2 的 CRT 在 linker 層設定。PE header 的 `AddressOfEntryPoint` 就指向它。

Ghidra 操作步驟：

1. **Symbol Tree → Functions**：找 `entry` 或以 PE entry point 位址開頭的函式（通常叫 `FUN_<entry_rva>`）。
2. 把它 rename 成 `_ModuleEntryPoint`。
3. **典型 DXE driver entry 的長相**（Decompiler 輸出）：

```c
EFI_STATUS
_ModuleEntryPoint(EFI_HANDLE ImageHandle, EFI_SYSTEM_TABLE *SystemTable)
{
    // 初始化 global gST, gBS, gRT
    gST = SystemTable;
    gBS = SystemTable->BootServices;
    gRT = SystemTable->RuntimeServices;

    // 可能有 library constructor 呼叫
    // 最後呼叫真正的 driver 主邏輯
    return DxeEntry(ImageHandle, SystemTable);
}
```

如果看到函式一開始做兩三次間接指標賦值（`DAT_xxxxxxxx = param_2->...`），幾乎可以確定是在初始化 `gBS/gRT/gST`。

---

## 還原 gBS / gRT / gST 呼叫

這是 UEFI RE 最關鍵的一步。gBS（EFI_BOOT_SERVICES *）是一個全域指標，存在 data segment 的某個位址。所有 Boot Service 呼叫都是 `CALL [gBS + offset]`，Ghidra 要看懂這個 pattern。

### 手動操作流程

1. 在 `_ModuleEntryPoint` 找到 `gST = SystemTable` 的賦值（`MOV [DAT_1234], RDX` 或類似）。記下 `DAT_1234` 的位址，這是 gST 的存放位置。

2. 接下來通常是 `MOV [DAT_5678], RAX`（把某個指標存入全域），配合反編譯器看到 `DAT_5678 = (EFI_BOOT_SERVICES*)SystemTable->BootServices`。`DAT_5678` 就是 gBS。

3. 在 **Data Type Manager** 裡選 `EFI_BOOT_SERVICES *`，到 Symbol Tree 的 `DAT_5678`，右鍵 **Data → EFI_BOOT_SERVICES ***，把它的型別設成 `EFI_BOOT_SERVICES *`，並 rename 成 `gBS`。

4. 同樣操作 gRT（`EFI_RUNTIME_SERVICES *`）、gST（`EFI_SYSTEM_TABLE *`）。

5. 設完之後，重新分析（**Analysis → Auto Analyze**），Ghidra 會自動把 `CALL [gBS+0x140]` 反編譯成 `(*gBS->LocateProtocol)(...)`，因為它現在知道 `EFI_BOOT_SERVICES` 在 `0x140` offset 的欄位名叫 `LocateProtocol`。

### Boot Services Table 的關鍵 Offset

| Offset | 函式名稱 | 用途 |
|--------|---------|------|
| 0x018 | `RaiseTPL` | 提升 Task Priority Level |
| 0x020 | `RestoreTPL` | 恢復 TPL |
| 0x040 | `AllocatePool` | 動態記憶體分配 |
| 0x048 | `FreePool` | 釋放記憶體 |
| 0x098 | `InstallProtocolInterface` | 安裝 protocol，最重要的之一 |
| 0x0B0 | `HandleProtocol` | 取得 handle 上的 protocol |
| 0x0C8 | `LocateProtocol` | 在所有 handle 中找 protocol |
| 0x100 | `InstallMultipleProtocolInterfaces` | 批次安裝 |
| 0x108 | `UninstallMultipleProtocolInterfaces` | 批次卸載 |
| 0x130 | `LocateHandleBuffer` | 找所有實作特定 protocol 的 handle |
| 0x140 | `LocateProtocol` | （特定 Ghidra struct 版本的 offset，依 spec 版本可能略不同） |
| 0x148 | `InstallMultipleProtocolInterfaces` | — |
| 0x1D8 | `RegisterProtocolNotify` | 註冊 protocol 安裝通知回呼 |

實際 offset 以 `MdePkg/Include/Uefi/UefiSpec.h` 裡的 struct 為準，Ghidra 一旦套上型別就不需要查表。

---

## 解析 Protocol GUID

GUID 是 128-bit 的常數，在 binary 裡以 `{0xAAAAAAAA, 0xBBBB, 0xCCCC, {0xDD, ...}}` 的格式存在 data segment。Ghidra 找到一個 16-byte 常數，如果不知道它是哪個 GUID，逆向就卡在這裡。

### 方法一：ghidra-firmware-utils GUID 資料庫

ghidra-firmware-utils 附帶 GUID 資料庫腳本（`ApplyEfiSymbols.java`），能自動掃描 data segment，對照 MdePkg 和常見 Protocol 的已知 GUID，把所有匹配的 `DAT_` 自動 rename 成 GUID 名稱（如 `gEfiBlockIoProtocolGuid`）。

執行：**Script Manager → ApplyEfiSymbols → Run**。

完成後你會看到所有 GUID 常數都有了名字，`LocateProtocol` 的第一個參數也從 `&DAT_abcdef00` 變成 `&gEfiBlockIoProtocolGuid`。

### 方法二：手動查 GUID

```bash
# 從 binary 提取 GUID bytes（假設找到 offset 0x1234）
python3 -c "
data = open('SecurityStubDxe.efi','rb').read()
offset = 0x1234
guid = data[offset:offset+16]
# GUID layout: {uint32, uint16, uint16, uint8[8]}
import struct
p1 = struct.unpack_from('<I', guid, 0)[0]
p2 = struct.unpack_from('<H', guid, 4)[0]
p3 = struct.unpack_from('<H', guid, 6)[0]
p4 = guid[8:16].hex().upper()
print(f'{{{p1:#010x},{p2:#06x},{p3:#06x},{{{p4}}}}}\n')
"
# 然後去 edk2 source grep 這個值
grep -r "AAAAAAAA" /path/to/edk2/MdePkg/Include/Protocol/
```

---

## 辨識常見 Pattern

### Pattern 1：LocateProtocol

```c
// 典型呼叫（套上型別後的 Decompiler 輸出）
EFI_STATUS status;
EFI_BLOCK_IO_PROTOCOL *BlockIo = NULL;

status = gBS->LocateProtocol(
    &gEfiBlockIoProtocolGuid,  // param1: GUID
    NULL,                       // param2: Registration（一般 NULL）
    (VOID **)&BlockIo           // param3: 輸出指標
);
if (!EFI_ERROR(status)) {
    // 使用 BlockIo
    BlockIo->ReadBlocks(BlockIo, MediaId, Lba, BufferSize, Buffer);
}
```

逆向時識別 LocateProtocol 的特徵：
- 三個參數，第一個是 GUID 指標
- 第三個參數常常是 local 變數的位址（LEA + pass）
- 回傳值用 TEST/JNZ 或 CMP EAX, 0 判斷

### Pattern 2：InstallProtocolInterface

SMM 相關模組或核心服務模組，常見安裝自己的 protocol：

```c
// gHandle 是 EFI_HANDLE（可能為 NULL，DXE Foundation 會分配新 handle）
EFI_HANDLE gHandle = NULL;

status = gBS->InstallProtocolInterface(
    &gHandle,                        // param1: handle（inout）
    &gMyDriverProtocolGuid,          // param2: GUID
    EFI_NATIVE_INTERFACE,            // param3: interface type（固定值 0）
    &gMyDriverProtocolInstance       // param4: protocol 實作指標
);
```

識別特徵：第三個參數幾乎永遠是立即值 `0`（`EFI_NATIVE_INTERFACE`），第四個參數是全域 struct 的位址。

### Pattern 3：SMI Handler 註冊

SMM 模組（FFS type = SMM_DRIVER，或 DXE 中用 SmmAccess 進入 SMRAM 的模組）會呼叫 SMM handler 相關 protocol：

```c
// 取得 EFI_SMM_BASE2_PROTOCOL（EFI_SMM_BASE2_PROTOCOL_GUID）
// 然後呼叫 SmmBase->InSmm(SmmBase, &InSmm) 確認是否在 SMRAM
// 若在 SMRAM，用 EFI_SMM_SYSTEM_TABLE2 的 SmiHandlerRegister 安裝 handler

EFI_SMM_SYSTEM_TABLE2 *gSmst;
gSmst->SmiHandlerRegister(
    MySmiHandler,           // 處理 SMI 的函式指標
    &gMySwSmiGuid,         // 觸發用的 GUID（software SMI）或 NULL（catch-all）
    &DispatchHandle         // 輸出 handle
);
```

逆向識別：找 `EFI_SMM_SYSTEM_TABLE2` 的使用（`gSmst->SmiHandlerRegister` 在 offset 約 `0xE8`）。SMM 模組的 SMI handler 函式是攻擊面的核心（Ch 11-13 的主題），逆向時最優先找這裡。

**ASCII 示意圖：SMM 模組初始化流程**

```
_ModuleEntryPoint(ImageHandle, SystemTable)
    │
    ├─► LocateProtocol(&gEfiSmmBase2ProtocolGuid, ..., &SmmBase2)
    │
    ├─► SmmBase2->InSmm(SmmBase2, &InSmm)
    │       ├─ InSmm=FALSE → 不在 SMRAM，僅做 DXE 初始化，return
    │       └─ InSmm=TRUE  → 進入 SMRAM 初始化路徑
    │
    ├─► SmmBase2->GetSmstLocation(SmmBase2, &gSmst)
    │
    └─► gSmst->SmiHandlerRegister(MySmiHandler, &SmiGuid, &Handle)
              │
              └─► MySmiHandler() 在每次符合 SmiGuid 的 SMI 觸發時被呼叫
```

---

## efiXplorer 對照介紹（IDA Pro 外掛）

efiXplorer（`https://github.com/binarly-io/efiXplorer`）是 Binarly 開源的 IDA Pro 外掛，功能上比 ghidra-firmware-utils 更完整：

| 功能 | ghidra-firmware-utils | efiXplorer (IDA) |
|------|----------------------|-----------------|
| 自動識別 gBS/gRT/gST | 半自動（需手動套 type） | 全自動 |
| GUID 資料庫 | 涵蓋 MdePkg 基本集 | 涵蓋 MdePkg + EDK2 全套 + vendor 擴充 |
| SMI handler 識別 | 需要手動找 | 自動標記，列出報告 |
| Protocol dependency graph | 無 | 有（可視化 protocol 相依） |
| callXrefs 圖形化 | Ghidra 原生提供 | IDA 原生 + efiXplorer 加強 |
| 價格 | 免費（Ghidra 免費） | IDA Pro 商業授權（貴） |

對資安研究者的建議：**Ghidra + ghidra-firmware-utils 足以完成 95% 的 UEFI RE 工作**。efiXplorer 的優勢是自動化程度更高、在大型 BIOS image（含幾十個模組）時效率顯著，適合需要快速掃描整個 BIOS 找攻擊面的場景（例如 LogoFAIL 類的大規模掃描）。

---

## 動手：逆 OVMF 的 SecurityStubDxe

以下是完整的步驟化流程，假設已依前面章節完成 OVMF 環境搭建（Ch 0）。

### 步驟 1：抽取模組（WSL）

```bash
# 安裝 Python 工具
pip3 install uefi-firmware

# 解析 OVMF
python3 -c "
import uefi_firmware
with open('/usr/share/OVMF/OVMF_CODE.fd', 'rb') as f:
    data = f.read()
parser = uefi_firmware.AutoParser(data)
firmware = parser.parse()
# 遞迴 dump 所有 object
firmware.dump('./ovmf_dump')
"

# 找 SecurityStubDxe（或任何感興趣的 DXE driver）
find ./ovmf_dump -type f -name "*.object" | xargs file 2>/dev/null | grep PE
# 或直接 grep GUID（SecurityStubDxe 的 GUID 末段是 ...0a72）
find ./ovmf_dump -name "*.object" -exec sh -c '
    hexdump -C "$1" | head -1 | grep -q "MZ" && echo "$1"
' _ {} \;
```

另一個更直接的方式：從 UEFITool（Windows 上跑）搜 "SecurityStub" → Extract body → `SecurityStubDxe.efi`。

### 步驟 2：載入 Ghidra

**截圖描述 2-1**：開啟 Ghidra → Import `SecurityStubDxe.efi`。Import 對話框中確認 Format 為 `Portable Executable (PE)`，Language 為 `x86:LE:64:default`。點 Options，勾選 `Perform library name analysis`。

**截圖描述 2-2**：Double-click 開啟新匯入的檔案 → Auto Analyze 對話框，勾選所有分析器，特別確認 `Non-Returning Functions - Discovered` 和 `Decompiler Parameter ID` 都勾上。點 Analyze，等待分析完成（進度條在底部）。

### 步驟 3：套 type library 並識別 entry point

**截圖描述 3-1**：File → Parse C Source，或從 Data Type Manager → Open File Archive，選 `efi.gdt`。載入完成後，展開 Data Type Manager 確認看到 `EFI_BOOT_SERVICES`、`EFI_SYSTEM_TABLE` 等型別。

**截圖描述 3-2**：Symbol Tree → Functions → 找最後一個（或按 entry point RVA 跳轉）。按 'G' 輸入 entry RVA，跳到 PE Entry Point。

**截圖描述 3-3**：Decompiler 視窗顯示 entry 函式：看到兩個參數（`undefined8 param_1, undefined8 param_2`），函式體開頭有對 `DAT_xxx` 的賦值（存 `param_2->...`）。

### 步驟 4：識別並標記 gST / gBS / gRT

```
操作序列（以 Decompiler 視窗為主要工作介面）：

1. 在 Decompiler 中找到類似：
      DAT_00011234 = param_2;                      // gST = SystemTable
      DAT_00011238 = *(EFI_BOOT_SERVICES**)...;    // gBS = SystemTable->BootServices
      DAT_0001123c = *(EFI_RUNTIME_SERVICES**)...; // gRT = SystemTable->RuntimeServices

2. 點擊 DAT_00011238 → 右鍵 → Data → Pointer → EFI_BOOT_SERVICES
   再 右鍵 → Rename → 輸入 gBS

3. 點擊 DAT_0001123c → 同樣操作，rename 成 gRT

4. 回到 Decompiler → 右鍵任意處 → Refresh → 看到 gBS->xxx 呼叫現在有名稱了
```

**截圖描述 4-1**：套上型別前：`CALL qword ptr [DAT_00011238 + 0x98]`，看不出意義。
**截圖描述 4-2**：套上 `EFI_BOOT_SERVICES *` 型別並 rename 後：`(*gBS->InstallProtocolInterface)(&local_handle, &DAT_...guid, 0, &protocol_impl)`，語義清楚。

### 步驟 5：執行 GUID 識別腳本

**截圖描述 5-1**：Window → Script Manager → 在搜尋框輸入 `ApplyEfi` → 找到 `ApplyEfiSymbols.java` → 點 Run。

**截圖描述 5-2**：腳本執行完成後，原本的 `DAT_00012000`（16 bytes）現在叫 `gEfiSecurityArchProtocolGuid`，LocateProtocol 呼叫的第一個參數也跟著更新。

### 步驟 6：追蹤 main 邏輯

SecurityStubDxe 的工作是安裝 `EFI_SECURITY_ARCH_PROTOCOL`，提供一個 stub（空實作）讓其他模組能在 BDS 階段找到這個 protocol。逆向後應該看到：

```c
EFI_STATUS
DxeEntry(EFI_HANDLE ImageHandle, EFI_SYSTEM_TABLE *SystemTable)
{
    EFI_STATUS status;
    EFI_HANDLE handle = NULL;

    // 安裝 Security Arch Protocol（stub 版本，不真正驗簽）
    status = gBS->InstallProtocolInterface(
        &handle,
        &gEfiSecurityArchProtocolGuid,
        EFI_NATIVE_INTERFACE,
        &mSecurityStub              // 函式指標表
    );

    return status;
}
```

`mSecurityStub` 是一個 struct，包含 `FileAuthenticationState` 函式指標（UEFI Secure Boot 驗簽的 hook point）。這個 stub 版本的函式實作永遠回傳 `EFI_SUCCESS` 且不做任何驗證，攻擊者若能替換這個 stub 為自己的實作，就能繞過 Secure Boot 的 image 驗證。

---

## 踩雷

1. **ghidra-firmware-utils 的 TE loader 要手動啟用**：即使安裝了外掛，import TE binary 時 Ghidra 不一定自動選 TE loader。如果 TE 模組被當成 raw binary 載入，所有 section offset 都會差 `StrippedSize`（TE header 欄位）。解決：Import 時在 Format 下拉選單手動選 `UEFI Terse Executable (TE)`。

2. **Auto Analyze 可能把 data 誤分析成 code**：UEFI PE 的 data segment 有很多 GUID 常數（16 bytes），Ghidra 有時會把它分析成 code（看起來像合法的指令序列）。看到一段奇怪的「code」如果內容都是 `MOV` 接奇怪常數，先 Clear Code Bytes（右鍵 → Clear），再 Define Data 設為 `EFI_GUID`。

3. **沒有套 type 直接看 offset 的陷阱**：Boot Services 的 offset 在不同 UEFI spec 版本可能略有差異（UEFI 2.9 vs 2.10）。不要硬背 offset，讓 Ghidra type system 幫你做這件事。hardcode offset 的筆記會隨 spec 版本失效。

4. **OVMF 的 Release build 有部分最佳化**：OVMF Release build 的 DXE 模組有時會把短函式 inline，導致 `_ModuleEntryPoint` 和 `DxeEntry` 合成一個大函式，找不到清晰的兩層結構。遇到這種情況，不必非要切出兩層，從 InstallProtocolInterface / LocateProtocol 呼叫往回追即可。

5. **Script Manager 的腳本需要 Java 環境匹配**：Ghidra 10.x 用 Java 17，部分社群提供的腳本是用 Ghidra 9.x 的 API 寫的，會在 Script Manager 執行時拋 exception。症狀：腳本執行沒有輸出，底部顯示 `ScriptException`。解法：查腳本 API 呼叫是否有 deprecated warning，升級腳本或改用替代腳本。

6. **SMI handler 的第二個參數是 caller 控制的**：如果你在逆向 SMM 模組，找到 `MySmiHandler(EFI_HANDLE DispatchHandle, VOID *Context, VOID *CommBuffer, UINTN *CommBufferSize)`，**`CommBuffer` 和 `CommBufferSize` 是從 NS world 傳入的**，不可信任。逆向時優先看這兩個參數的使用方式：有沒有驗 `CommBufferSize` 的上限？有沒有驗 `CommBuffer` 的地址不落在 SMRAM 範圍？這就是 CVE 藏的地方。

---

## 進階延伸

- **efiXplorer 在整 BIOS 掃描的工作流程**：拿到一台廠商 BIOS，用 UEFITool 抽出所有 DXE 模組，批次丟進 IDA + efiXplorer，自動生成所有 SMI handler 的清單和 gBS 呼叫統計。這是 Binarly 做 LogoFAIL / PKfail 這類大規模掃描的基本方法。Ghidra 版本可以用 Headless Analyzer 做類似的批次自動化。

- **Ghidra Headless 批次分析**：`support/analyzeHeadless` 腳本讓你在 CLI 批次匯入並分析多個 binary，輸出分析結果或執行自訂腳本。對 BIOS 審計很有用：一個腳本匯入所有 DXE driver，跑 ApplyEfiSymbols，輸出所有 SMI handler 的位址。

- **VSS（NVRAM Variable） parsing**：UEFI 變數（NVRAM）的 binary 格式（VSS2，定義在 MdeModulePkg）是另一個重要的 RE 目標。UEFITool 可以直接 parse VSS 分區，Ghidra 要手動定義 struct。

---

## 動手練習

1. **基礎：識別 gBS 的四個 protocol 呼叫**：取任意一個從 OVMF 抽出的 DXE 模組，用本章流程載入 Ghidra，找出模組用了哪幾個 Boot Service 函式，列出 offset 和對應的函式名稱（套上型別後自動可見）。

2. **進階：找 SMI handler 的 CommBuffer 驗證**：取 OVMF 中帶 `Smm` 字樣的模組（例如 `SmmCoreStandaloneMode.efi` 或類似），找到 SmiHandlerRegister 的呼叫和對應的 handler 函式，分析 handler 如何使用 `CommBuffer` 和 `CommBufferSize`，判斷是否有越界讀寫的可能。

3. **CTF 向：找 vulnerability pattern**：下載一個舊版本的 AMI BIOS blob（二手機主機板韌體，許多廠商官網可下載），用 UEFITool 抽 DXE 模組，在 Ghidra 找 `GetVariable` 呼叫，看回傳資料是否有 length 驗證。

---

## 本章重點

- UEFI PE 載入 Ghidra 必須選 **Microsoft x64 calling convention**，否則 decompiler 參數推導全錯
- TE binary 需要 ghidra-firmware-utils 的 TE loader，否則 section offset 差 `StrippedSize`
- **gBS/gRT/gST 需要手動標型別**，完成後 Decompiler 才能把 `CALL [reg+offset]` 還原成有名稱的 Boot Service 呼叫
- GUID 識別靠 `ApplyEfiSymbols.java` 腳本自動批次完成，節省逐一查表的時間
- **SMI handler 的 CommBuffer 是 NS world 傳入的，不可信**，是找 SMM 漏洞的首要分析目標
- efiXplorer（IDA Pro 外掛）在大規模掃描效率更高，但 Ghidra + ghidra-firmware-utils 是免費可行的替代方案

---

## 自我檢核

- [ ] 能解釋 TE 格式和 PE32+ 的差異，以及 TE `StrippedSize` 欄位如何影響 RVA 計算
- [ ] 能在 Ghidra 設定正確的 Language 和 Calling Convention，使反編譯器輸出正確的參數位置（RCX/RDX/R8/R9）
- [ ] 能手動識別 `_ModuleEntryPoint` 中的 gST/gBS/gRT 初始化賦值，並套上對應型別
- [ ] 能用 `ApplyEfiSymbols.java` 腳本自動識別 GUID，並說明腳本識別失敗時如何手動查 GUID
- [ ] 能識別 LocateProtocol / InstallProtocolInterface / SmiHandlerRegister 三個 pattern 的 decompiler 輸出特徵
- [ ] 能解釋為什麼 SMI handler 的 CommBuffer 在安全分析中優先關注，以及應該檢查哪些驗證

---

## 延伸閱讀

1. **ghidra-firmware-utils — Alex James（`https://github.com/al3xtjames/ghidra-firmware-utils`）**
   讀哪裡：README 的 Installation 和 Usage 章節；`src/main/java/firmware/` 裡的各 loader source
   學什麼：TE loader 的 StrippedSize 修正邏輯；GDT 型別的來源（基於哪個版本的 edk2 標頭）；擴充自己的 GUID 資料庫的方法
   關聯：本章 Ghidra 操作的核心工具，直接接 Ch 25 的 ARM binary RE（相同工具，不同 ISA）

2. **efiXplorer — Binarly（`https://github.com/binarly-io/efiXplorer`）**
   讀哪裡：Wiki 的 Usage Guide；`efiXplorer/src/efiAnalysis.cpp` 裡的 gBS offset 識別邏輯
   學什麼：自動化 UEFI RE 的設計思路——如何用 heuristic 識別 global table pointer、為什麼 SMI handler 自動識別需要追蹤跨函式的 protocol installation
   關聯：本章 efiXplorer 對照介紹；Ch 26（找後門與 diffing）大量使用 efiXplorer 的 batch analysis 能力

3. **"LogoFAIL: Security Implications of Image Parsers in UEFI Firmware" — Binarly（BHEU 2023）**
   讀哪裡：完整白皮書（binarly.io/blog）；特別看 Methodology 章節描述如何用 Ghidra headless + efiXplorer 批次掃描 logo parser
   學什麼：本章流程的實戰應用——從整個 BIOS blob 系統性識別攻擊面、如何從 decompiler 輸出找 image parsing 函式的 length 驗證缺失
   關聯：Ch 30（真實利用鏈）的 LogoFAIL 部分；說明本章 RE 工具如何直接服務於漏洞挖掘

→ [下一章](./25-arm-bootloader-re.md)
