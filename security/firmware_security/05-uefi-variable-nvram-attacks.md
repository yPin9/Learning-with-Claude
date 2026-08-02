# Ch 5 — UEFI Variable 與 NVRAM 攻擊

> **目標**：搞清楚 UEFI variable 的格式、屬性 bit 的意義、authenticated variable 的保護機制，以及攻擊者如何透過 variable 空間耗盡（brick）、非 authenticated variable 竄改、S3 resume 攻擊來達成 persistence 或提權。用 python 真跑 OVMF_VARS.fd 解析 variable store 結構貼出來。
>
> **環境**：WSL，python3，/usr/share/OVMF/OVMF_VARS.fd（131072 bytes）。

## 為什麼 variable 是攻擊面？

UEFI variable（NVRAM variable）存放的東西比你想像的重要：Secure Boot 的整個信任根（PK、KEK、db、dbx）都是 variable；開機順序（BootOrder、Boot0000）是 variable；S3 suspend/resume 的 boot script 存放位址也通過 variable 傳遞。

這裡有個根本矛盾：**variable 設計成 OS 也能讀寫（RT attribute），但有些 variable 的內容決定了韌體的安全策略**。設計者用 authenticated variable 機制試圖解決這個矛盾，但不是所有重要 variable 都被保護到，攻擊面仍然不小。

---

## Variable 格式：三層結構

```
OVMF_VARS.fd  (131072 bytes)
│
├── EFI_FIRMWARE_VOLUME_HEADER  (0x48 bytes)
│   GUID: fff12b8d-7696-4c8b-a985-2747075b4f50
│   Signature: _FVH
│   FvLength: 131072 bytes
│
└── EFI_VARIABLE_STORE_HEADER  (28 bytes, at offset 0x48)
    GUID: aaf32c78-947b-439a-a180-2e144ec37792
    Size: 57272 bytes (0xdfb8)   ← 可用空間上限
    Format: 0x5A                 ← formatted/healthy
    State: 0xFE                  ← healthy
    │
    ├── EFI_VARIABLE_HEADER[0]   (variable #1)
    ├── EFI_VARIABLE_HEADER[1]   (variable #2)
    ├── ...
    └── [0xFF padding to end of store]
```

每個 variable 的記憶體佈局（**non-authenticated**）：

```
Offset  Size  欄位
0       2     StartId         (0x55AA，大端看是 AA55)
2       1     State           (0x3F=added, 0x7F=in-delete-transition, 0xFF=free)
3       1     Reserved
4       4     Attributes      (NV/BS/RT/HR/AW/EXT/AT/HW bit flags)
8       4     NameSize        (UTF-16LE name 的 byte 數，含 null terminator)
12      4     DataSize        (variable data 的 byte 數)
16      16    VendorGuid      (namespace GUID)
32      N     Name[]          (UTF-16LE string)
32+N    M     Data[]          (raw data)
─────── alignment to 4 bytes ────────────────────────────
```

**Authenticated variable**（`AT` bit 為 1）在 StartId 到 Attributes 之後還有：

```
8       8     MonotonicCount  (防 replay，已被 TimeStamp 取代但欄位仍在)
16      16    TimeStamp       (EFI_TIME，time-based auth 用)
32      4     PubKeyIndex     (指向公鑰的索引，在 auth cert db)
36      4     NameSize
40      4     DataSize
44      16    VendorGuid
60      N     Name[]
60+N    M     Data[]          (帶 WIN_CERTIFICATE 簽章的 payload)
```

---

## 真實演練：解析 OVMF_VARS.fd

以下是在 WSL 實際跑出的結果：

```python
# /tmp/parse_vars.py
import struct, uuid

data = open("/usr/share/OVMF/OVMF_VARS.fd", "rb").read()

# FV header 長度存在 offset 48 的 HeaderLength 欄位
fv_hdr_len = struct.unpack_from("<H", data, 48)[0]
# Variable Store Header at offset fv_hdr_len
vs_off = fv_hdr_len
vs_guid_b = data[vs_off:vs_off+16]
vs_guid = uuid.UUID(bytes_le=bytes(vs_guid_b))
vs_size, vs_fmt, vs_state = struct.unpack_from("<IBB", data, vs_off+16)

print("FV Header length: 0x%x" % fv_hdr_len)
print("Variable Store GUID: %s" % vs_guid)
print("Variable Store Size: %d bytes (0x%x)" % (vs_size, vs_size))
print("Format: 0x%02x" % vs_fmt)
print("State:  0x%02x" % vs_state)
```

輸出：

```
FV Header length: 0x48
Variable Store GUID: aaf32c78-947b-439a-a180-2e144ec37792
Variable Store Size: 57272 bytes (0xdfb8)
Format: 0x5a
State:  0xfe
```

繼續掃 variable store 裡的 variable（搜尋 `0xAA 0x55` StartId）：

```python
# 掃 variable store 裡的 variable
offset = vs_off + 28   # variable store header 是 28 bytes
found = 0
while offset < vs_off + vs_size - 4:
    if data[offset:offset+2] != b"\xaa\x55":
        break
    # ... 解析 header ...
    found += 1
```

實際輸出：

```
No 0xAA55 variables found at 0x64
First 32 bytes at offset: ffffffffffffffff...  (全 0xFF)
```

**這個結果是預期的**：OVMF 發行版附的 `OVMF_VARS.fd` 是**空的 variable store**，裡面沒有預先寫入任何 variable（包含沒有 PK/KEK/db）。這代表 Secure Boot 預設是 **Setup Mode**（未部署 PK），不是 **User Mode**。

QEMU 開機後第一次把 variable 寫進去（BootOrder、Boot0000 等），它們存在 runtime 記憶體裡；如果你在 QEMU 命令列加了 `-drive if=pflash,format=raw,file=OVMF_VARS.fd` 且沒有把這個檔掛成可寫（`readonly=off`），變更不會持久化。

---

## Attributes Bit 詳解

```
Bit  名稱                         縮寫  意義
0    NON_VOLATILE                  NV    掉電保留（寫進 SPI flash）；未設則只在記憶體
1    BOOTSERVICE_ACCESS            BS    DXE/BDS phase 可存取
2    RUNTIME_ACCESS                RT    OS runtime 可存取（透過 gRT->GetVariable）
3    HARDWARE_ERROR_RECORD         HR    硬體錯誤記錄（分配獨立儲存空間）
4    AUTHENTICATED_WRITE_ACCESS    AW    已棄用的舊式驗證（現在用 AT）
5    EXTENDED_ACCESS               EXT   廠商自定義擴充
6    TIME_BASED_AUTHENTICATED_     AT    time-based authenticated write access
     WRITE_ACCESS
7    APPEND_WRITE                  HW    寫入是 append 而非覆蓋（用於 dbx 等更新）
```

常見組合：
- `NV+BS+RT`（7）：最常見，開機後到 OS runtime 都能讀寫，**無簽章保護**
- `NV+BS+RT+AT`（71 = 0x47）：authenticated variable，寫入需要有效簽章鏈
- `NV+BS`（3）：只在 boot services 期間可用，OS 起來後讀不到
- `NV+BS+RT+AW`（已棄用）：舊式 monotonic count 驗證

**重點**：`BootOrder`、`Boot0000`-`BootFFFF` 通常屬性是 `NV+BS+RT`（**沒有 AT**），OS ring 0 可以不需要任何簽章直接呼叫 `gRT->SetVariable` 修改。

---

## Secure Boot 的 Variable 本質

Secure Boot 的所有信任根都存為 authenticated variable：

```
Variable 名稱  GUID                                  屬性      內容
PK             8be4df61-93ca-11d2-aa0d-00e098032b8c  NV+BS+RT+AT  Platform Key（最高信任根）
KEK            8be4df61-93ca-11d2-aa0d-00e098032b8c  NV+BS+RT+AT  Key Exchange Key
db             d719b2cb-3d3a-4596-a3bc-dad00e67656f  NV+BS+RT+AT  Allowed signature DB
dbx            d719b2cb-3d3a-4596-a3bc-dad00e67656f  NV+BS+RT+AT  Forbidden signature DB（黑名單）
dbr            (同上)                                NV+BS+RT+AT  Recovery key
SecureBoot     8be4df61-93ca-11d2-aa0d-00e098032b8c  NV+BS+RT     當前 Secure Boot 狀態（0/1）
SetupMode      (同上)                                NV+BS+RT     是否在 Setup Mode（1=未部署 PK）
```

`SecureBoot` 和 `SetupMode` 這兩個 variable 屬性是 `NV+BS+RT`，**沒有 AT**。但韌體把它們宣告為 read-only，`SetVariable` 對它們的寫入會被 firmware（`VariableRuntimeDxe`）直接拒絕，即使沒有 AT 保護。

PK/KEK/db/dbx 有 AT 保護：寫入時需要 payload 附帶用 KEK 私鑰（或 PK 私鑰，取決於要改什麼）簽署的 `EFI_VARIABLE_AUTHENTICATION_2` 結構，裡面包含時間戳記和 CMS SignedData，韌體驗簽後才接受寫入。

---

## 攻擊一：NVRAM 空間耗盡（Brick）

### 機制

Variable store 有固定大小（OVMF 預設 57,272 bytes 可用）。每次 `SetVariable` 寫入新 variable 或更新既有 variable，實際上是 append（舊版本標記為 deleted，新版本 append 到後面）。`FaultTolerantWriteDxe` + `VariableRuntimeDxe` 負責 garbage collection（GC），把 deleted variable 回收空間。

如果攻擊者快速寫入大量小 variable，在 GC 跟上之前把 store 塞滿：

```c
// OS ring 0 的攻擊者
for (int i = 0; i < 10000; i++) {
    CHAR16 name[32];
    swprintf(name, 32, L"AttackVar%d", i);
    UINT8 data[512] = {0xAA};
    SetVariable(name, &AttackerGuid, EFI_VARIABLE_NON_VOLATILE |
                EFI_VARIABLE_BOOTSERVICE_ACCESS |
                EFI_VARIABLE_RUNTIME_ACCESS,
                sizeof(data), data);
}
```

結果：下一次開機，`VariableRuntimeDxe` 嘗試從 store 讀 `BootOrder` 時空間已滿，讀取失敗，BDS 找不到開機項，系統可能停在 UEFI Shell 或直接無法開機（取決於廠商實作）。

### 影響

- 輕則：開不了機，但重新刷韌體可修復
- 重則：如果韌體 recovery 路徑也用 NVRAM（某些平台的 recovery URL 存在 variable 裡），recovery 也失效，變成真磚

### 防禦

- 現代韌體對 `NV` variable 有 quota 限制，每個 GUID 能用的空間有上限
- `HardwareErrorRecord` 類 variable 有獨立儲存區，不佔用一般 variable store
- 廠商通常在 `SetVariable` 裡驗使用者身份（但 RT attribute 代表 OS ring 0 就能呼叫）

---

## 攻擊二：非 Authenticated Variable 竄改

### BootOrder 竄改（最常見）

```c
// OS ring 0，竄改開機順序
// 把 Boot0099（攻擊者的 EFI）插到最前面
UINT16 NewBootOrder[] = {0x0099, 0x0000, 0x0001, 0x0002};
gRT->SetVariable(
    L"BootOrder",
    &gEfiGlobalVariableGuid,          // 8be4df61-93ca-11d2-aa0d-00e098032b8c
    EFI_VARIABLE_NON_VOLATILE | EFI_VARIABLE_BOOTSERVICE_ACCESS | EFI_VARIABLE_RUNTIME_ACCESS,
    sizeof(NewBootOrder), NewBootOrder
);

// 建立 Boot0099 指向攻擊者放在 ESP 的 EFI
EFI_LOAD_OPTION BootEntry = { ... };  // 包含 \EFI\attacker\malware.efi 的 device path
gRT->SetVariable(
    L"Boot0099",
    &gEfiGlobalVariableGuid,
    EFI_VARIABLE_NON_VOLATILE | ...,
    sizeof(BootEntry), &BootEntry
);
```

這不需要 Secure Boot 關閉——只要攻擊者的 `malware.efi` 有合法簽章（或 Secure Boot 沒開），就能開機。這是持久化的常見手法，重裝 OS 後 BootOrder 不會被清除（variable 存在 NVRAM，不在硬碟上）。

### BootNext 單次劫持

```c
UINT16 NextBoot = 0x0099;   // 下次開機只跑一次攻擊者的 image
gRT->SetVariable(
    L"BootNext",
    &gEfiGlobalVariableGuid,
    EFI_VARIABLE_NON_VOLATILE | ...,
    sizeof(NextBoot), &NextBoot
);
// 然後呼叫 gRT->ResetSystem(EfiResetWarm, EFI_SUCCESS, 0, NULL)
```

`BootNext` 只影響下一次開機，自動清除，更難被發現。

### OsIndications 竄改

`OsIndicationsSupported` 和 `OsIndications` variable 控制平台能力旗標，例如 `EFI_OS_INDICATIONS_BOOT_TO_FW_UI`（重開機進 UEFI 設定）、`EFI_OS_INDICATIONS_START_OS_APPLICATION`（直接跳某個 EFI app）。

```c
UINT64 Indications = EFI_OS_INDICATIONS_BOOT_TO_FW_UI;
gRT->SetVariable(L"OsIndications", &gEfiGlobalVariableGuid, ..., 8, &Indications);
// 觸發 warm reset → 下次開機直接進 UEFI 設定 UI（可配合 UI 中的 Secure Boot 關閉選項）
```

---

## 攻擊三：S3 Resume 相關攻擊

### S3 Boot Script 的問題

S3 suspend/resume 流程中，PEI 階段需要重新初始化硬體（chipset registers），但不像冷開機那樣執行完整的 DXE。硬體初始化的指令序列存放在 **S3 Boot Script**（實作為 `BootScriptSave`），它在冷開機時被 DXE driver 寫入，存放位置的指標通常通過 `LockBoxSave`/`LockBoxRestore` 機制傳給 S3 resume 路徑。

問題：這個 boot script 儲存在 **正常 DRAM**（而非 SMRAM），OS 可以讀寫。如果攻擊者在 S3 sleep 後、系統 resume 前，把 boot script 裡的指令改掉（例如加一條「寫 SMRAM 控制暫存器」），S3 resume 時 PEI 會乖乖執行被篡改的 boot script，可能導致 SMRAM 保護被解除。

### CVE-2015-3613（Lenovo/AMI）類型

具體攻擊流程：
1. OS sleep → S3 suspend，boot script 寫入 DRAM
2. 攻擊者在 OS 仍在 S3 前改寫 boot script（或在 resume 後 race）
3. S3 resume，PEI 執行篡改後的 boot script，解除 SMRAM 保護
4. 攻擊者從 ring 0 讀寫 SMRAM，寫入惡意 SMM handler
5. 之後所有 SMI 都跑攻擊者控制的程式碼（Ring -2 完整控制）

### 現代緩解

- UEFI 2.5+ 引入 `LockBox` 機制，把 boot script 存在 SMRAM-protected 區域
- Intel SMM Protected Range Register（SMRR）確保 SMRAM 在 S3 resume 期間不可讀寫
- CHIPSEC 的 `smm_lock` 模組就是在驗這些鎖是否有效

---

## Authenticated Variable 驗證流程

當 `SetVariable` 被呼叫且 `Attributes` 包含 `EFI_VARIABLE_TIME_BASED_AUTHENTICATED_WRITE_ACCESS` 時，`VariableRuntimeDxe` 的處理流程：

```
SetVariable("db", VendorGuid, NV+BS+RT+AT, DataSize, Data)
    │
    ▼
Data 的格式是：EFI_VARIABLE_AUTHENTICATION_2
    ├── EFI_TIME TimeStamp     (必須 >= 現有 variable 的 TimeStamp，防 replay)
    └── WIN_CERTIFICATE AuthInfo
        └── CMS SignedData
            ├── signer: KEK certificate (或 PK，取決於改的是什麼)
            ├── signature: 對 TimeStamp || VariableName || VendorGuid || Attr || NewData 的簽章
            └── certificate chain

    ▼  驗章失敗 → EFI_SECURITY_VIOLATION
    ▼  TimeStamp 舊於現有值 → EFI_SECURITY_VIOLATION
    ▼  驗章成功 → 正常寫入，更新 TimeStamp
```

如果攻擊者沒有 KEK 或 PK 的私鑰，就無法偽造這個簽章，也就無法改 db/dbx/PK/KEK。

**但有個例外**：如果平台處於 **Setup Mode**（PK 未部署，`SetupMode = 1`），PK 可以被任何人寫入（因為還沒有 PK 可以驗簽），**此時不需要簽章**。這是為了讓 OEM 在出廠時部署 PK 設計的，但如果使用者沒有自己部署 PK，平台就永遠在 Setup Mode，Secure Boot 形同虛設。

---

## 真實案例：PKfail（2024）

Binarly 發現多家 OEM 在出廠時使用了 edk2 reference 實作裡的測試 PK 私鑰（`TestCert.pk12`，公開在 edk2 repo 裡）。這意味著任何人都能用這把「秘密」私鑰簽 Secure Boot variable，等於 PK 保護失效。

受影響廠商包含 AMI、Phoenix、Insyde（合計覆蓋全球約 10% 裝置）。修復方式是部署廠商自己的 PK；但 NVRAM 的 PK 是 authenticated variable，需要用舊 PK 私鑰簽才能更新——而既然舊 PK 私鑰已洩露，攻擊者也能自己更新 PK，所以修復不是只換 variable，還要重新設計 trust anchor 部署流程。

---

## 對比取捨：Variable 保護機制

| 保護方式 | 能防什麼 | 防不了什麼 | 典型應用 |
|---|---|---|---|
| 無保護（NV+BS+RT） | 掉電不丟失 | OS ring 0 可任意改 | BootOrder、Boot0000 |
| AT（authenticated） | 無私鑰不能寫 | 私鑰洩露（PKfail）、Setup Mode | PK/KEK/db/dbx |
| Read-only in firmware | OS 寫入被拒 | 韌體本身的 bug | SecureBoot、SetupMode |
| LockBox (SMRAM) | S3 路徑保護 boot script | SMRR 沒啟用時物理記憶體攻擊 | S3 Boot Script 位址 |
| Quota 限制 | 防 DoS 耗盡 | OS 仍可對自己 GUID 的 var 攻擊 | 防 NVRAM brick |

---

## 踩雷集錦

**1. 以為 BootOrder 有 AT 保護**：BootOrder 沒有 `EFI_VARIABLE_TIME_BASED_AUTHENTICATED_WRITE_ACCESS`，OS ring 0 可以直接改。這是最常見的誤解，也是持久化攻擊最常用的入口。

**2. Setup Mode 被忽略**：「Secure Boot 有開」不等於「PK 已部署」。在 Setup Mode 下，任何人都能換 PK，換完就能控制整個 Secure Boot 信任鏈。部署了 PK 但 PK 私鑰是 edk2 測試鑰匙，等於 PKfail。

**3. Variable store GC 不即時**：寫入 variable 之後，舊版本不是立刻消失，而是標記 deleted 等待 GC。forensic 調查時，dump NVRAM 可以看到已刪除 variable 的殘骸（State byte 不是 0x3F）。攻擊者用的 variable 即使被刪，可能留有跡象。

**4. AuthInfo 的 TimeStamp 防 replay 有缺口**：time-based auth 的時間戳比對是 `>=` 現有值。如果攻擊者能在系統時鐘不前進的情況下重放，理論上舊的 auth 資料仍可用。部分老韌體的時鐘實作有問題，實際上沒有防重放。

**5. OVMF_VARS.fd 空檔案≠Secure Boot 沒開**：OVMF_VARS.fd 是空 store 只是因為沒有預部署 PK，不代表 Secure Boot 一定「關著」——UEFI 在 Setup Mode 下 Secure Boot 的狀態取決於 `SecureBootEnable` variable 和韌體設定，QEMU 開機後去看 `SecureBoot` variable 的值才知道真實狀態。

---

## 進階延伸

- **edk2 MdeModulePkg/Universal/Variable/RuntimeDxe/Variable.c**：`VariableServiceSetVariable` 函式是 `SetVariable` 的實際實作，看它如何驗 AT attribute、呼叫 `VerifyTimeBasedPayload`，以及 GC 的觸發時機。
- **UEFI Specification 2.10, Section 8.2**（uefi.org/specifications）：GetVariable/SetVariable 的語義、attribute 定義、authenticated variable 的完整格式（`EFI_VARIABLE_AUTHENTICATION_2` 結構），是理解攻防的一手資料。
- **Binarly PKfail 報告（2024）**（binarly.io/blog）：完整技術分析，包含如何用洩露的 PK 私鑰自己簽 variable、受影響 firmware 的識別方法，直接示範 AT 保護在私鑰管理失敗時的崩潰。

---

## 動手練習

1. 開一個 QEMU + OVMF 的 VM，進 UEFI Shell，跑 `dmpstore` 列出所有 variable，觀察 `BootOrder` 和 `SecureBoot` 的 attribute，確認前者沒有 AT、後者是 read-only。
2. 在 UEFI Shell 跑 `dmpstore BootOrder`，記下 handle；然後跑 `setvar BootOrder` 嘗試寫入，觀察是否成功（OVMF 在 UEFI Shell 裡跑時仍在 BS phase，可以寫 BS variable）。
3. 用 python 寫一個 parser，在 `qemu` 開機後 dump 到的 OVMF_VARS.fd（有開機過且掛成可寫的版本）上找出 `BootOrder`、`Boot0000` 的 attributes 和 data，驗證它們的屬性 byte 是 `0x07`（NV+BS+RT，無 AT）。

---

## 本章重點

- UEFI variable 分兩種：有 `AT` attribute 的 authenticated variable（PK/KEK/db/dbx），和普通的 `NV+BS+RT` variable（BootOrder/Boot0000）；後者 OS ring 0 可任意寫。
- Variable store 有固定大小，耗盡會導致開機失敗（brick）；現代韌體有 quota 緩解，但不完全。
- Authenticated variable 用 time-based CMS 簽章保護，私鑰管理失敗（PKfail）就等於 AT 保護無效。
- S3 Boot Script 歷史上存在 DRAM 被竄改的攻擊路徑，現代韌體用 LockBox + SMRR 緩解。
- OVMF_VARS.fd 的空 variable store 是 Setup Mode（無 PK），Secure Boot 保護形同虛設。

---

## 自我檢核

- [ ] 我能說出 `NV`/`BS`/`RT`/`AT` 各 bit 的意義
- [ ] 我能解釋為什麼 BootOrder 可以被 OS ring 0 修改，但 db 不行
- [ ] 我知道 Setup Mode 是什麼，以及為什麼它讓 Secure Boot 失效
- [ ] 我能解釋 S3 Boot Script 攻擊的完整流程（DRAM 可寫 → SMRAM 失守）
- [ ] 我能說出 PKfail 漏洞的根本原因（私鑰管理，不是技術缺陷）
- [ ] 我用 python 跑過 OVMF_VARS.fd 的解析，知道空 store 的結構

---

## 延伸閱讀

1. **UEFI Specification 2.10, Section 8.2 "UEFI Variable Services"**（uefi.org/specifications）：GetVariable、SetVariable 的完整語義定義，`EFI_VARIABLE_AUTHENTICATION_2` 結構的格式。每次遇到「variable 行為不符預期」時的最終仲裁，關聯到本課 Ch 28（Secure Boot 深入）。

2. **Binarly, "PKfail: Untrusted Platform Keys Undermine Secure Boot on UEFI Ecosystem"（2024）**（binarly.io/blog）：示範了即使技術上正確實作 authenticated variable，管理層面失誤（測試私鑰出廠）就能讓整個信任鏈崩潰。是理解 AT 保護實際邊界的最好案例研究，關聯到 Ch 32（dbx/SBAT 撤銷）。

3. **Rafal Wojtczuk & Corey Kallenberg, "Attacks on UEFI Security"（CanSecWest 2015）**（bromiumlabs.github.io）：S3 Boot Script 攻擊的原始技術報告，完整 PoC 流程圖解，解釋如何從 OS ring 0 透過 S3 path 打到 SMM Ring -2；關聯到本課 Ch 12（SMM callout）。

→ [Ch 6 capsule update 與 firmware volume/FFS](./06-capsule-update-ffs.md)
