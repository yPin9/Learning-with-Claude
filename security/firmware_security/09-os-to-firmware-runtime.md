# Ch 9 — 從 OS 打回韌體：runtime 信任邊界

> **目標**：搞清楚 OS 開機之後韌體並沒有消失——`gRT`（EFI Runtime Services）仍然活著、SW SMI 仍然可觸發、S3 boot script 仍然等著下次 resume。從 OS ring 0 能往韌體打的路徑是哪些、每條路徑的威脅模型是什麼。

> **環境**：WSL（`/sys/firmware/efi` 不存在，已驗證；efivarfs 格式解析 demo 可真跑）。真實 Linux 上的命令給驗證方法。

---

## 為什麼需要這章？

課程前八章都在講「DXE 階段裡」的漏洞——開機過程中的攻擊面。但有一個根本問題沒有回答：**OS 起來之後，韌體在哪？**

答案是：它沒有消失。

UEFI 規範定義了兩個服務集合：
- `EFI Boot Services`（`gBS`）：ExitBootServices() 之後**立刻失效**，OS 不能用。
- `EFI Runtime Services`（`gRT`）：ExitBootServices() 之後**仍然存活**，OS 可以呼叫。

這個設計讓 OS 在開機後還能讀寫韌體 variable、送 capsule 更新、同步時鐘。代價是：OS 與韌體之間的信任邊界沒有完全斷開，而是從「任何程式都能呼叫 boot services」縮減成「特定的 runtime 呼叫仍然有效」。攻擊者在 ring 0 的韌體攻擊面，就藏在這個「仍然存活」的接口裡。

---

## 先建立直覺：ExitBootServices 之後的記憶體地圖

```
ExitBootServices() 之前（DXE 環境）：
┌────────────────────────────────┐
│  gBS（Boot Services）          │  ← 所有人都能呼叫
│  gRT（Runtime Services）       │  ← 所有人都能呼叫
│  gST（System Table）           │
│  Protocol Database             │
│  DXE driver pool               │
└────────────────────────────────┘

ExitBootServices() 之後（OS 環境）：
┌────────────────────────────────┐
│  OS kernel 空間                │
│                                │
│  gRT 仍存在（UEFI Runtime 區） │  ← 虛擬地址重映射後仍可呼叫
│  （透過 SetVirtualAddressMap 告知韌體新的虛擬位址）
│                                │
│  gBS → NULL（或廢棄）          │  ← 不能再用
│  Protocol DB → 消失            │
└────────────────────────────────┘
```

gRT 的記憶體區域用 `SetVirtualAddressMap()` 告知韌體新的位址後，仍然用特殊的 UEFI Runtime 記憶體類型（`EfiRuntimeServicesCode` / `EfiRuntimeServicesData`）保留在記憶體裡，直到下次開機。

---

## EFI Runtime Services：OS 可呼叫的接口

UEFI 規範定義的 Runtime Services（`EFI_RUNTIME_SERVICES`）：

| 函數 | 功能 | 攻擊者視角 |
|------|------|---------|
| `GetVariable()` | 讀 NVRAM variable | 可讀出 Secure Boot keys、BootOrder |
| `SetVariable()` | 寫 NVRAM variable | **寫入惡意 variable 觸發 DXE 端漏洞** |
| `UpdateCapsule()` | 送 firmware update | **送惡意 capsule → 觸發 capsule parser 漏洞** |
| `QueryCapsuleCapabilities()` | 查 capsule 支援 | 偵測目標 |
| `GetTime()` / `SetTime()` | 讀寫硬體時鐘 | 低風險 |
| `GetNextHighMonotonicCount()` | 單調計數器 | 低風險 |
| `ResetSystem()` | 重啟 | 可用於 S3 sleep |
| `SetVirtualAddressMap()` | 告知虛擬地址映射 | 僅開機時呼叫一次 |

### OS 如何呼叫

Linux 核心通過 `arch/x86/platform/efi/efi.c` 包裝所有 gRT 呼叫，提供給 userspace：
- `/sys/firmware/efi/efivars/` — efivarfs，每個 variable 是一個檔案
- `efivar -l` — 列出所有 variable
- `efivar -n PK-8be4df61-...` — 讀取特定 variable

---

## 攻擊路徑 1：SetVariable() 觸發韌體 Bug

OS ring 0 呼叫 `SetVariable()` 寫入一個 variable → 韌體端的 variable storage 驅動（`VariableRuntimeDxe`）在 runtime 處理這個請求。

如果 variable service 在 runtime 階段有 bug（如：沒有驗 name length、data length 允許超大值），ring 0 寫一個特製 variable 可觸發：
- Runtime 端 heap overflow
- NVRAM flash 操作的 OOB write

**2022 的實際案例**：Insyde H2O 的 `VariableRuntimeDxe` 在 runtime 接受的 variable data 長度沒有 enforce，CVE-2021-41837/41838/41841 系列。

### 攻擊前提

- 攻擊者在 ring 0（kernel exploit 或 root + disable lockdown）
- 目標有 writable efivarfs（通常需要 root + `CAP_SYS_ADMIN`）
- 韌體 runtime variable handler 有 bug

---

## 攻擊路徑 2：UpdateCapsule() 送惡意 Capsule

OS 用 `UpdateCapsule()` 送 firmware update capsule。capsule 被複製到記憶體，下次重啟時在 PEI/DXE 階段解析。

前八章學過的 capsule parser bug（整數溢位、格式解析錯誤）在這裡用上：OS 組一個特製 capsule payload，呼叫 `UpdateCapsule()`，重啟後韌體自動解析，觸發 code exec。

**前提**：capsule update 通常需要有效的廠商簽章（capsule authentication protocol）。若廠商的簽章驗證本身有 bug，或測試機沒有啟用驗證，這條路才能走。

---

## 攻擊路徑 3：從 OS 觸發 SW SMI

透過 port I/O 寫入 port `0xB2`（APMC port）觸發 Software SMI，進入 SMM 執行 SMI handler。

這是 OS→韌體最直接的呼叫路徑，SMM handler 是在 ring -2 執行的。

```
OS ring 0:
  outb(0xB2, smi_code);  /* 觸發 SW SMI */
         │
         ▼
  CPU 進入 SMM mode（ring -2）
         │
         ▼
  SMM 執行對應的 SmiHandler
  （從 comm buffer 讀 OS 傳的參數）
         │
         ▼
  返回 OS（RSM 指令）
```

Linux 核心的 `acpi_os_write_port()` 可以寫 port（需要 `ioperm(0xB2, 1, 1)` 或 `/dev/port`）。

**這條路對應 Ch 10–12 的 SMM 攻擊面**，這裡只是點出它是 OS→韌體的一條 runtime 路徑。

---

## 攻擊路徑 4：S3 Sleep 後的 Boot Script 竄改

OS ring 0 在 S3 sleep 之前：
1. 找出 S3 boot script 在 DRAM 的位置
2. 寫入惡意 boot script 指令（關閉 SPI write protect）
3. 呼叫 `ResetSystem(EfiResetShutdown)` 或觸發 S3 sleep
4. S3 resume 時 boot script 以高權限 replay

這條路只在 S3 boot script 未正確保護時成立（Ch 7 已討論）。

---

## Linux 介面：從 OS 看到的韌體

### /sys/firmware/efi

掛載條件：系統以 UEFI 開機，Linux kernel 有 `CONFIG_EFI=y`。

```
/sys/firmware/efi/
├── efivars/            ← efivarfs（每個 variable 是一個 binary 檔案）
│   ├── BootOrder-8be4df61-93ca-11d2-aa0d-00e098032b8c
│   ├── Boot0000-8be4df61-...
│   ├── PK-8be4df61-...
│   └── SecureBoot-8be4df61-...
├── vars/               ← 舊介面（efivars sysfs）
├── systab              ← EFI System Table 位址
└── fw_platform_size    ← 32 or 64
```

**在 WSL 環境下**：

```bash
$ ls /sys/firmware/efi
ls: cannot access '/sys/firmware/efi': No such file or directory
```

WSL 不以 UEFI 方式開機（它是 Hyper-V 半虛擬化，UEFI runtime services 不暴露給 WSL guest），所以這個路徑不存在。這是預期行為。

**在真實 Linux 主機（UEFI 開機）上的預期輸出**：
```bash
$ ls /sys/firmware/efi/efivars/ | head -10
AcpiGlobalVariable-c020489e-6db2-4ef2-9aa5-ca06fc11d36a
BootCurrent-8be4df61-93ca-11d2-aa0d-00e098032b8c
BootOrder-8be4df61-93ca-11d2-aa0d-00e098032b8c
Boot0000-8be4df61-93ca-11d2-aa0d-00e098032b8c
PK-8be4df61-93ca-11d2-aa0d-00e098032b8c
KEK-8be4df61-93ca-11d2-aa0d-00e098032b8c
db-d719b2cb-3d3a-4596-a3bc-dad00e67656f
dbx-d719b2cb-3d3a-4596-a3bc-dad00e67656f
SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c

$ efivar -n "BootOrder-8be4df61-93ca-11d2-aa0d-00e098032b8c"
# 輸出: 4 bytes attributes + 原始 data (hex)
```

**本段「真實 Linux 上的輸出」未實測，為理論預期行為。** 驗證方法：在非 WSL、以 UEFI 方式開機的 Linux（Ubuntu 22.04 等）上執行 `ls /sys/firmware/efi/efivars/` 與 `sudo efivar -l`。

---

## 真跑：efivarfs Variable 格式解析

efivarfs 的每個「檔案」開頭是 4 bytes 的 `EFI_VARIABLE_ATTRIBUTES`（little-endian），後面才是實際 variable data。以下程式解析這個格式：

```c
/* efi_attr.c
 * 解析 efivarfs variable 的 attribute header 格式
 * 來源：UEFI Spec 2.10 §7.2，Table 7-1
 *
 * gcc -o efi_attr efi_attr.c && ./efi_attr
 */
#include <stdio.h>
#include <stdint.h>
#include <string.h>

#define EFI_VARIABLE_NON_VOLATILE                          0x00000001
#define EFI_VARIABLE_BOOTSERVICE_ACCESS                    0x00000002
#define EFI_VARIABLE_RUNTIME_ACCESS                        0x00000004
#define EFI_VARIABLE_HARDWARE_ERROR_RECORD                 0x00000008
#define EFI_VARIABLE_TIME_BASED_AUTHENTICATED_WRITE_ACCESS 0x00000020
#define EFI_VARIABLE_APPEND_WRITE                          0x00000040
#define EFI_VARIABLE_ENHANCED_AUTHENTICATED_ACCESS         0x00000080

void decode_attrs(uint32_t attrs)
{
    printf("  Attributes: 0x%08x\n", attrs);
    if (attrs & EFI_VARIABLE_NON_VOLATILE)
        puts("    [x] NON_VOLATILE (survives reboot)");
    if (attrs & EFI_VARIABLE_BOOTSERVICE_ACCESS)
        puts("    [x] BOOTSERVICE_ACCESS");
    if (attrs & EFI_VARIABLE_RUNTIME_ACCESS)
        puts("    [x] RUNTIME_ACCESS (OS 可讀寫)");
    if (attrs & EFI_VARIABLE_TIME_BASED_AUTHENTICATED_WRITE_ACCESS)
        puts("    [x] TIME_BASED_AUTH_WRITE (需簽章才能寫)");
    if (attrs & EFI_VARIABLE_APPEND_WRITE)
        puts("    [x] APPEND_WRITE");
    if (attrs & EFI_VARIABLE_ENHANCED_AUTHENTICATED_ACCESS)
        puts("    [x] ENHANCED_AUTH_ACCESS");
}

int main(void)
{
    /* 模擬 efivarfs 讀出的 BootOrder: attrs=NV|BS|RT，data=三個 entry */
    uint8_t mock_bootorder[] = {
        0x07, 0x00, 0x00, 0x00,  /* attrs: NV|BS|RT */
        0x00, 0x00,              /* Boot0000 */
        0x01, 0x00,              /* Boot0001 */
        0x02, 0x00,              /* Boot0002 */
    };

    printf("=== efivarfs variable 格式解析示範 ===\n");
    printf("原始 bytes (hex):");
    for (size_t i = 0; i < sizeof(mock_bootorder); i++)
        printf(" %02x", mock_bootorder[i]);
    puts("");

    uint32_t attrs;
    memcpy(&attrs, mock_bootorder, 4);
    decode_attrs(attrs);

    uint8_t *data = mock_bootorder + 4;
    size_t dlen = sizeof(mock_bootorder) - 4;
    printf("  BootOrder (%zu entries):", dlen / 2);
    for (size_t i = 0; i < dlen; i += 2) {
        uint16_t entry;
        memcpy(&entry, data + i, 2);
        printf(" Boot%04X", entry);
    }
    puts("\n");

    /* 典型 Secure Boot variable attributes */
    printf("=== 典型 Secure Boot variable attributes ===\n");
    struct { const char *name; uint32_t attrs; } vars[] = {
        { "PK",         0x27 },  /* NV|BS|RT|TIME_AUTH */
        { "KEK",        0x27 },
        { "db/dbx",     0x27 },
        { "BootOrder",  0x07 },  /* NV|BS|RT */
        { "BootXXXX",   0x07 },
        { "SecureBoot", 0x06 },  /* BS|RT */
    };
    for (size_t i = 0; i < sizeof(vars)/sizeof(vars[0]); i++) {
        printf("\n[%s]\n", vars[i].name);
        decode_attrs(vars[i].attrs);
    }
    return 0;
}
```

**真實執行輸出**（WSL gcc）：
```
=== efivarfs variable 格式解析示範 ===
原始 bytes (hex): 07 00 00 00 00 00 01 00 02 00
  Attributes: 0x00000007
    [x] NON_VOLATILE (survives reboot)
    [x] BOOTSERVICE_ACCESS
    [x] RUNTIME_ACCESS (OS 可讀寫)
  BootOrder (3 entries): Boot0000 Boot0001 Boot0002

=== 典型 Secure Boot variable attributes ===

[PK]
  Attributes: 0x00000027
    [x] NON_VOLATILE (survives reboot)
    [x] BOOTSERVICE_ACCESS
    [x] RUNTIME_ACCESS (OS 可讀寫)
    [x] TIME_BASED_AUTH_WRITE (需簽章才能寫)

[KEK]
  Attributes: 0x00000027
    [x] NON_VOLATILE (survives reboot)
    [x] BOOTSERVICE_ACCESS
    [x] RUNTIME_ACCESS (OS 可讀寫)
    [x] TIME_BASED_AUTH_WRITE (需簽章才能寫)

[db/dbx]
  Attributes: 0x00000027
    [x] NON_VOLATILE (survives reboot)
    [x] BOOTSERVICE_ACCESS
    [x] RUNTIME_ACCESS (OS 可讀寫)
    [x] TIME_BASED_AUTH_WRITE (需簽章才能寫)

[BootOrder]
  Attributes: 0x00000007
    [x] NON_VOLATILE (survives reboot)
    [x] BOOTSERVICE_ACCESS
    [x] RUNTIME_ACCESS (OS 可讀寫)

[BootXXXX]
  Attributes: 0x00000007
    [x] NON_VOLATILE (survives reboot)
    [x] BOOTSERVICE_ACCESS
    [x] RUNTIME_ACCESS (OS 可讀寫)

[SecureBoot]
  Attributes: 0x00000006
    [x] BOOTSERVICE_ACCESS
    [x] RUNTIME_ACCESS (OS 可讀寫)
```

### 關鍵讀法

- `PK/KEK/db/dbx` 有 `TIME_BASED_AUTH_WRITE`（bit 5）：寫入需要有效的 EFI_VARIABLE_AUTHENTICATION_2 結構（包含時間戳和簽章），不能直接用 `SetVariable()` 覆寫。這是防止 OS ring 0 直接竄改 Secure Boot keys 的防線。
- `SecureBoot` 是 `0x06`（BS|RT），**沒有 NON_VOLATILE**：這個 variable 的實際值由韌體在每次開機時根據 PK 是否存在計算，OS 無法寫入（寫入會被 variable service 拒絕），只能讀。
- `BootOrder` 是 `0x07`（NV|BS|RT），**沒有 TIME_BASED_AUTH_WRITE**：OS root 可以直接改 BootOrder 和 BootXXXX（只要有 `CAP_SYS_ADMIN`），這是「OS 能影響下次開機行為」的合法路徑，也是 bootkit 持久化的一條路。

---

## 底層機制：gRT 呼叫在 64-bit 長模式下的實作

```
OS 呼叫 gRT->SetVariable():

  1. OS kernel 持有 gRT 指標（虛擬地址，透過 SetVirtualAddressMap 更新）
  2. 呼叫 gRT->SetVariable(Name, Guid, Attrs, DataSize, Data)
           │
           ▼
  3. CPU 進入 UEFI Runtime 程式碼（仍在 ring 0，不是 ring -2）
     （UEFI Runtime 不是 SMM！它只是特殊保留的記憶體區域）
           │
           ▼
  4. UEFI variable service 實作執行
     (通常最終透過 SMM comm buffer 送到 SMM 裡的 variable handler)
           │
           ▼
  5. 如果需要寫 SPI flash → 透過 SMM 的 spi_flash_write()
           │
           ▼
  6. 返回 OS
```

重點：`gRT->SetVariable()` 本身跑在 ring 0，但很多廠商實作最終透過 SMM comm buffer 把寫 flash 委託給 SMM handler——這就是為什麼 SetVariable() 漏洞可能升級到 ring -2。

---

## 對比取捨：不同 OS→韌體路徑的攻擊難度

| 攻擊路徑 | 需要的權限 | 攻擊面大小 | 現代緩解 |
|---------|---------|---------|---------|
| SetVariable() 觸發漏洞 | ring 0 + CAP_SYS_ADMIN | 中（只有 runtime variable handler） | variable size/attr 驗證 |
| UpdateCapsule() 送惡意 capsule | ring 0 | 大（capsule parser 全部） | capsule auth（簽章）|
| SW SMI 觸發（port 0xB2） | ring 0 + ioperm | 大（所有 SMM handler） | SMM 鎖定（HSMI、SmmCode Check）|
| S3 boot script 竄改 | ring 0 + DRAM 不保護 | 中 | SMRR、boot script 加密 |
| efivarfs BootOrder 寫入 | root | 小（BDS 解析） | Secure Boot 防 unsigned boot |

---

## 防禦視角：如何縮小 Runtime 攻擊面

1. **Lockdown kernel**（Linux 5.4+）：`CONFIG_SECURITY_LOCKDOWN_LSM`，啟動後禁止 ring 0 存取 MMIO 和 ioport（包含 port 0xB2），也限制 direct capsule delivery。
2. **Variable Policy（SMM-based variable lock）**：edk2 的 `VarCheckLib` / `VariablePolicy`：開機時用 `RegisterVariablePolicy()` 鎖定特定 variable（如 `PK`、`BootOrder`），OS 的 SetVariable() 呼叫到了 SMM 裡就被拒絕。
3. **MemoryProtectionLib（NX for Runtime）**：UEFI Runtime 記憶體區域設為 NX，即使有 overflow，shellcode 無法執行。
4. **SPI flash 保護**：BIOS_CNTL.WPD=0 + PRx（Protected Range Registers），讓 runtime 的 flash 寫入只能透過 SMM，而 SMM 再驗簽章。

---

## 踩雷

1. **以為 ExitBootServices 之後韌體記憶體就被回收**：UEFI Runtime 記憶體類型（`EfiRuntimeServicesCode/Data`）永遠不被 OS 回收，它在 OS 的記憶體映射裡有自己的區段，直到下次開機。忘了這點就會漏掉整個 runtime 攻擊面。

2. **直接在 WSL 用 efivarfs 相關工具**：WSL 沒有 `/sys/firmware/efi`，`efivar`、`efibootmgr` 等工具不報錯但會靜默失敗或找不到路徑。要測試必須在真實 UEFI 開機的 Linux 上。

3. **以為 TIME_BASED_AUTH_WRITE 能完全保護 Secure Boot keys**：保護強度取決於簽章驗證實作有沒有 bug（ch 7 的類型 6 就是打這裡）。PKfail 案例告訴我們：如果私鑰洩漏，這個防禦形同虛設。

4. **混淆 UEFI Runtime 和 SMM**：兩者都在 ring 0 範疇之外的某種意義，但 UEFI Runtime code 是跑在 ring 0（OS 的 privilege level），SMM 才是 ring -2（CPU 的 SMM mode）。很多 SetVariable() 實作把兩者橋接起來。

5. **以為 UpdateCapsule() 直接跑 capsule**：UpdateCapsule() 的 immediate capsule 只有 `CAPSULE_FLAGS_POPULATE_SYSTEM_TABLE` 的情況才是當場處理；韌體更新 capsule（`CAPSULE_FLAGS_PERSIST_ACROSS_RESET`）是存到記憶體，下次重啟才在 PEI 階段解析。攻擊的 payload 執行點在重啟後，不是 SetVariable 呼叫時。

---

## 進階延伸

- **BootHole（CVE-2020-10713）**：GRUB2 的 parser bug，從 OS/boot loader 層觸發，繞過 Secure Boot 的一個完整鏈。這不是「從 OS 打韌體」，而是「韌體信任 GRUB，GRUB 被打」，信任鏈的不同切入點，與本章 runtime 路徑對照來看能更清楚。
- **Insyde VariableSmm 系列漏洞**：CVE-2021-41837 等，runtime `SetVariable()` 觸發 SMM 端漏洞的真實案例，可以在 Binarly blog 找到完整分析。
- **Linux EFI capsule delivery**：`/sys/firmware/efi/capsule/` 是 Linux 核心支援的 capsule delivery 介面，root 可以把 firmware binary 寫進去；研究這個介面的格式驗證有助於理解真實的 UpdateCapsule() 攻擊面。

---

## 動手練習

1. 把上面的 `efi_attr.c` 改成「從 stdin 讀入 hex bytes 並解析」，模擬真實 efivarfs 讀出的二進位資料流。測試：輸入 `27 00 00 00` 確認輸出有 `TIME_BASED_AUTH_WRITE`。

2. 在真實 Linux（非 WSL）上執行：
   ```bash
   sudo cat /sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c | xxd | head
   ```
   前 4 bytes 是 attributes，第 5 byte 是 SecureBoot 的值（0=Off, 1=On）。

3. 查 edk2 `SecurityPkg/VariableAuthentication2/` 的 `VerifyTimeBasedPayload()` 函數，找出「如果時間戳驗證有什麼條件可以繞過」（hint：時間戳 monotonic counter check）。

4. 查 Linux kernel 原始碼 `drivers/firmware/efi/vars.c`，找 `efivar_store_raw()` 函數——它是把 userspace write 轉換成 `SetVariable()` 呼叫的地方，看看有什麼 userspace-facing validation。

---

## 本章重點

- `gRT`（EFI Runtime Services）在 ExitBootServices 之後仍然存活，OS 可透過 efivarfs、直接呼叫等方式使用。
- OS→韌體的 runtime 路徑：`SetVariable()` 漏洞、`UpdateCapsule()` 惡意 capsule、SW SMI（port 0xB2）、S3 boot script 竄改。
- efivarfs 每個 variable 檔案的前 4 bytes 是 `EFI_VARIABLE_ATTRIBUTES`，`TIME_BASED_AUTH_WRITE` bit 保護 Secure Boot keys。
- `SecureBoot` variable 沒有 NON_VOLATILE，由韌體每次開機計算，OS 無法寫入。
- WSL 沒有 UEFI runtime，`/sys/firmware/efi` 不存在，測試 efivarfs 需要真實 UEFI Linux。
- 防禦：Linux lockdown、VariablePolicy/VarCheckLib、SPI write protect。

---

## 自我檢核

- [ ] 解釋為什麼 ExitBootServices 之後 gRT 仍然有效？
- [ ] 列出 OS ring 0 打韌體的四條路徑？
- [ ] `SecureBoot` variable 的 attributes 是什麼、為什麼 OS 不能寫？
- [ ] 知道 efivarfs 的檔案格式（前 4 bytes 的含義）？
- [ ] 解釋 `TIME_BASED_AUTH_WRITE` 和 `NON_VOLATILE` 各自保護什麼？

---

## 延伸閱讀

1. **UEFI Spec 2.10, §7.2 Variable Services**（uefi.org）— `GetVariable()`/`SetVariable()` 的完整規範，包含每個 attribute bit 的定義和 variable storage 的存取控制規則；是理解「為什麼 PK 不能被 OS 直接改」的唯一正確來源。[https://uefi.org/specifications](https://uefi.org/specifications)

2. **Insyde VariableSmm Vulnerabilities Analysis**（Binarly, 2022）— CVE-2021-41837 等的完整成因分析，從 OS 呼叫 SetVariable() 到觸發 SMM 端 bug 的完整路徑；是本章「SetVariable() 觸發韌體 bug」攻擊路徑的真實案例。[https://binarly.io/posts/](https://binarly.io/posts/)

3. **Linux EFI Runtime Services**（kernel.org documentation）— `Documentation/admin-guide/efi-stub.rst` 和 `drivers/firmware/efi/` 原始碼；理解 Linux 核心如何把 gRT 呼叫包裝成 userspace API（efivarfs），看防禦機制在哪一層。[https://www.kernel.org/doc/html/latest/admin-guide/efi-stub.html](https://www.kernel.org/doc/html/latest/admin-guide/efi-stub.html)

4. **BootHole: There's a Hole in the Boot**（Eclypsium, 2020）— GRUB2 CVE-2020-10713 的完整分析；雖然不是「OS 打韌體」，但它完整展示了「韌體信任鏈上的任一環節被打，後果就是整條鏈失效」，是學完本章後最好的補充案例。[https://eclypsium.com/2020/07/29/theres-a-hole-in-the-boot/](https://eclypsium.com/2020/07/29/theres-a-hole-in-the-boot/)

---

→ [練習 A：寫一個攔改開機流程的 DXE driver](./practice-a-dxe-boot-hook.md)
