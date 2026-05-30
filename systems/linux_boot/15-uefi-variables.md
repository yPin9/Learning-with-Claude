# Ch 15 — UEFI 變數與開機項管理

> **目標**：理解 UEFI 變數（NVRAM 中的鍵值儲存）——變數的命名空間（GUID + name）、屬性、開機相關變數（BootOrder/Boot####）、如何用 `efibootmgr` 和 `efivar` 在 Linux 裡管理開機項，以及變數背後的 Runtime Service 機制。

> **環境**：Linux（UEFI 開機）、`efibootmgr`、`efivar`、OVMF。承接 Ch 12（Runtime Services）、Ch 10（boot manager）。

## 為什麼 UEFI 變數這麼重要？

BIOS 的開機設定鎖在 BIOS 設定畫面裡，作業系統改不了。UEFI 不同——開機項是存在 NVRAM 的**變數**，作業系統能透過 Runtime Service（`GetVariable`/`SetVariable`，Ch 12）讀寫它們。

這意味著你能在 Linux 裡用 `efibootmgr` 新增、刪除、排序開機項，不用進韌體設定畫面。安裝作業系統時，安裝程式就是用這個機制把自己加進開機選單。理解 UEFI 變數，你就懂現代系統怎麼管理開機。

## 先建立直覺：UEFI 變數是韌體的鍵值資料庫

```
UEFI 變數 = 存在主機板 NVRAM 的鍵值對：

  鍵（key） = namespace GUID + 變數名稱
  值（value） = 任意 bytes + 屬性（attributes）
        │
  例：
    (GUID=8BE4DF61..., name="BootOrder") = 0001,0000,0002
    (GUID=8BE4DF61..., name="Boot0001")  = <描述 ubuntu 開機項的 bytes>
    (GUID=..., name="SecureBoot")         = 1（Secure Boot 開啟）
        │
  作業系統透過 Runtime Service 讀寫這些變數
  （或在 Linux 透過 /sys/firmware/efi/efivars/）
```

UEFI 變數是個持久化的鍵值儲存（存在 NVRAM，斷電不丟）。開機設定、Secure Boot 狀態、各種韌體配置都存成變數。

## 變數的命名：GUID + Name

每個變數由兩部分識別：

```
變數識別 = (Vendor GUID, Variable Name)

  Vendor GUID：命名空間（避免不同廠商的變數名衝突）
    8BE4DF61-93CA-11D2-AA0D-00E098032B8C = EFI_GLOBAL_VARIABLE
      （UEFI 標準變數，如 BootOrder）
    其他 GUID = 廠商/OS 自訂變數
        │
  Variable Name：變數名（UTF-16 字串）
    "BootOrder", "Boot0001", "SecureBoot", "PK", "KEK"...
```

用 GUID 當命名空間，讓不同來源的變數（UEFI 標準、廠商、OS）不會撞名。標準開機變數都在 `EFI_GLOBAL_VARIABLE` 這個 GUID 下。

## 開機相關變數

最重要的是開機管理變數（Ch 10 提過）：

```
開機變數（在 EFI_GLOBAL_VARIABLE GUID 下）：

  BootOrder    = 開機嘗試順序的清單
                 如 0001,0000,0002（先試 Boot0001，再 Boot0000...）

  Boot0000     = 一個開機項（EFI_LOAD_OPTION 結構）：
  Boot0001       - 屬性（active/hidden...）
  Boot0002       - 描述（"ubuntu", "Windows Boot Manager"...）
  ...            - 裝置路徑（哪個磁碟、哪個分區）
                 - .efi 路徑（/EFI/ubuntu/grubx64.efi）
                 - 可選參數

  BootCurrent  = 這次從哪個開機項開機的（唯讀）
  BootNext     = 下次開機用哪個（一次性，覆蓋 BootOrder）
  Timeout      = boot manager 等待選擇的秒數
```

開機流程（Ch 10）就是：韌體讀 `BootOrder`，照順序試每個 `Boot####`，第一個成功的開機。

## efibootmgr：管理開機項

`efibootmgr` 是 Linux 管理 UEFI 開機項的工具：

```bash
# 列出所有開機項
efibootmgr -v
# BootCurrent: 0001
# Timeout: 1 seconds
# BootOrder: 0001,0000,0002
# Boot0000* Windows Boot Manager  HD(1,GPT,...)/File(\EFI\Microsoft\Boot\bootmgfw.efi)
# Boot0001* ubuntu                HD(1,GPT,...)/File(\EFI\ubuntu\shimx64.efi)
# Boot0002* UEFI: USB ...
#       │   │                     └─ 裝置路徑 + .efi 路徑
#       │   └─ 描述
#       └─ 開機項編號

# 新增一個開機項
sudo efibootmgr --create \
    --disk /dev/sda --part 1 \          # ESP 在 sda1
    --label "My Linux" \                # 顯示名稱
    --loader '\EFI\mylinux\grubx64.efi' # .efi 路徑（注意反斜線）

# 改開機順序
sudo efibootmgr --bootorder 0003,0001,0000

# 設下次開機用哪個（一次性）
sudo efibootmgr --bootnext 0002

# 刪除開機項
sudo efibootmgr --bootnum 0003 --delete-bootnum

# 設 timeout
sudo efibootmgr --timeout 5
```

`efibootmgr` 背後就是透過 Runtime Service `SetVariable` 修改 `BootOrder`/`Boot####` 變數。這就是為什麼裝雙系統時，安裝程式能把自己加進開機選單——它呼叫這個機制。

## /sys/firmware/efi/efivars：直接存取變數

Linux 把 UEFI 變數暴露在 sysfs，能直接讀寫：

```bash
# UEFI 變數在這個目錄（每個變數一個檔案）
ls /sys/firmware/efi/efivars/
# BootOrder-8be4df61-93ca-11d2-aa0d-00e098032b8c
# Boot0001-8be4df61-93ca-11d2-aa0d-00e098032b8c
# SecureBoot-8be4df61-...
#  變數名-GUID

# 用 efivar 工具看變數（比直接讀 sysfs 友善）
efivar -l                          # 列出所有變數
efivar -p -n 8be4df61-93ca-11d2-aa0d-00e098032b8c-BootOrder  # 看 BootOrder

# 直接讀 sysfs（前 4 bytes 是屬性，之後是值）
sudo hexdump -C /sys/firmware/efi/efivars/BootOrder-8be4df61-*
```

> **危險警告**：`/sys/firmware/efi/efivars/` 是真的 NVRAM 變數。亂刪/亂改可能讓系統開不了機，甚至（歷史上有案例）某些有 bug 的韌體被刪光變數後變磚。**不要 `rm -rf /sys/firmware/efi/efivars/`**。用 `efibootmgr`/`efivar` 這種知道在做什麼的工具，不要直接操作 sysfs 檔案。

## 變數屬性

每個變數有屬性 bits，控制它的行為：

```c
// UEFI 變數屬性
#define EFI_VARIABLE_NON_VOLATILE        0x01  // 持久（存 NVRAM，斷電不丟）
#define EFI_VARIABLE_BOOTSERVICE_ACCESS  0x02  // Boot Services 能存取
#define EFI_VARIABLE_RUNTIME_ACCESS      0x04  // Runtime（OS）能存取
#define EFI_VARIABLE_AUTHENTICATED_WRITE_ACCESS 0x10  // 需要簽署才能寫
// ...
```

關鍵屬性：
- `NON_VOLATILE`：持久（如 BootOrder，斷電要記得）；非 volatile 的變數重開機就消失
- `RUNTIME_ACCESS`：OS 能不能存取（BootOrder 有這個，所以 efibootmgr 能改）
- `AUTHENTICATED_WRITE`：需要數位簽署才能寫（用於 Secure Boot 的金鑰變數，Ch 27）

> Secure Boot 的金鑰變數（PK、KEK、db）有 `AUTHENTICATED_WRITE` 屬性——改它們需要正確的數位簽署，防止惡意軟體偷改 Secure Boot 設定。這是 Ch 27 的基礎。一般開機變數（BootOrder）沒這限制，所以 efibootmgr 能直接改。

## 故意弄壞：刪掉 BootOrder

```bash
# 危險示範（不要在真機做！用 VM）
# 刪掉 BootOrder 變數
sudo efibootmgr  # 先看現狀
# 如果亂刪開機項：
sudo efibootmgr --bootnum 0001 --delete-bootnum  # 刪掉 Linux 開機項
# 重開機 → 韌體找不到 Linux 開機項
#  → 可能掉到後備路徑 /EFI/BOOT/BOOTX64.EFI（如果有）
#  → 或進韌體設定畫面，或無法開機
```

刪掉或弄亂開機變數，韌體找不到正確的 `.efi`，可能開不了機。救援方式：進 UEFI shell 手動執行 `.efi`、用 live USB 的 `efibootmgr` 重建開機項、或靠後備路徑 `/EFI/BOOT/BOOTX64.EFI`。這也是為什麼後備路徑重要——它是「開機項都壞了」時的最後防線。

## 踩雷集錦

1. **直接 rm efivars 的檔案**：可能讓系統無法開機，甚至磚化有 bug 的韌體。用 efibootmgr/efivar，不要直接操作 sysfs

2. **efibootmgr 的 loader 路徑用正斜線**：UEFI 路徑用反斜線 `\`（Windows 風格），efibootmgr 要 `'\EFI\...\grubx64.efi'`（單引號避免 shell 解讀反斜線）

3. **以為刪開機項會刪 .efi 檔**：efibootmgr 刪的是 NVRAM 變數（開機項），不刪 ESP 上的 `.efi` 檔案。檔案還在，只是韌體不知道要開它

4. **新增開機項但 ESP 分區號錯**：`--part` 要是 ESP 的分區號。錯了開機項指向錯誤位置，開不了機

5. **變數的前 4 bytes 是屬性不是值**：直接讀 efivars 檔案，前 4 bytes 是屬性 flags，之後才是值。直接 parse 整個檔案當值會錯

6. **修改 SecureBoot 相關變數**：PK/KEK/db 有 AUTHENTICATED_WRITE，需要簽署。一般工具改不了（也不該改），亂試會失敗或破壞 Secure Boot

## 進階：變數儲存的限制與韌體 bug

NVRAM 變數儲存有實際限制，曾造成著名事故：

```
NVRAM 變數的現實問題：
  - NVRAM 空間有限（幾十 KB ~ 幾百 KB）
  - 寫入有次數限制（flash 的本質）
  - 某些韌體的變數處理有 bug
        │
  著名案例：2016 年的「rm -rf 變磚」事件
    某些 Linux 系統，efivars 被 mount 成可寫
    rm -rf / 會刪到 efivars 的變數
    某些 MSI/其他廠商的韌體，變數被刪光後無法開機（變磚）
        │
  教訓：efivars 後來預設 mount 成 immutable（要特別操作才能改）
        Linux 加了保護，避免意外刪除
```

這個事件改變了 Linux 對 efivars 的處理——現在預設更保守（immutable flag），避免意外。理解這個，你會懂為什麼操作 UEFI 變數要謹慎，以及為什麼有些系統 efivars 是唯讀的。

UEFI 變數空間有限也是為什麼開機項不該無限新增——每個 `Boot####` 佔 NVRAM 空間，累積太多可能填滿。安裝/移除系統時清理舊開機項是好習慣。

## 動手練習

1. 看你系統的開機項：`efibootmgr -v`，找出 BootOrder、各 Boot####、BootCurrent。理解韌體開機時怎麼用這些

2. 看 efivars：`ls /sys/firmware/efi/efivars/ | head`，`efivar -l | head`，看有哪些變數（開機、SecureBoot、廠商自訂）

3. 在 VM（不是真機！）練習 efibootmgr：新增一個開機項、改 bootorder、再刪掉。觀察 `efibootmgr -v` 的變化

4. 看 SecureBoot 狀態：`efivar -p -n 8be4df61-...-SecureBoot`（或 `mokutil --sb-state`），確認 Secure Boot 開或關（Ch 27 會深入）

## 本章重點整理

- UEFI 變數是 NVRAM 的鍵值儲存，鍵 = (Vendor GUID, Name)，OS 透過 Runtime Service 讀寫
- 開機變數：BootOrder（嘗試順序）、Boot####（開機項：描述+裝置路徑+.efi 路徑）、BootCurrent/BootNext
- `efibootmgr` 在 Linux 管理開機項（新增/刪除/排序），背後是 SetVariable；變數也暴露在 /sys/firmware/efi/efivars/
- 變數屬性：NON_VOLATILE（持久）、RUNTIME_ACCESS（OS 可存取）、AUTHENTICATED_WRITE（Secure Boot 金鑰用，需簽署）
- 操作 UEFI 變數要謹慎——亂刪可能無法開機甚至磚化韌體；用 efibootmgr 不要直接 rm efivars

## 自我檢核

- [ ] 能解釋 UEFI 變數的命名（GUID + name）和為什麼用 GUID 命名空間
- [ ] 知道 BootOrder 和 Boot#### 的關係，韌體怎麼用它們開機
- [ ] 能用 efibootmgr 新增/刪除/排序開機項
- [ ] 知道為什麼不能直接 rm efivars 的檔案
- [ ] 知道 AUTHENTICATED_WRITE 屬性的作用（Secure Boot 金鑰保護）

## 延伸閱讀

### 官方文件

- **[UEFI Spec, Section 8.2 (Variable Services), 3.1.3 (Boot Manager - Load Options)](https://uefi.org/specifications)**
  - **讀哪裡**：8.2（GetVariable/SetVariable）、3.1.3（Boot#### 的 EFI_LOAD_OPTION 結構）
  - **學什麼**：變數服務和開機項的精確格式
  - **前提**：本章

- **[efibootmgr man page](https://manpages.debian.org/efibootmgr)** 和 **[efivar](https://github.com/rhboot/efivar)**
  - **讀哪裡**：efibootmgr 的所有選項
  - **學什麼**：管理開機項的完整工具用法
  - **前提**：本章

### 部落格 / 文章

- **[UEFI variables and the boot process](https://www.rodsbooks.com/efi-bootloaders/principles.html)** — Rod Smith
  - **這篇說什麼**：UEFI 開機原理，變數如何驅動開機選擇
  - **讀哪裡**：boot variables 那節
  - **為什麼值得讀**：把變數和開機流程連起來，實務角度清晰

→ [Ch 16 從 UEFI app 載入並啟動 kernel](./16-uefi-load-kernel.md)
