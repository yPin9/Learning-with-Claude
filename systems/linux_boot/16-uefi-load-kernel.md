# Ch 16 — 從 UEFI app 載入並啟動 kernel

> **目標**：把 UEFI 線串起來——UEFI bootloader 如何用檔案系統 protocol 讀取 kernel 檔案、為 kernel 準備環境、ExitBootServices、跳到 kernel entry point，以及 Linux 的 EFI stub（讓 kernel 本身就是個 `.efi`）這個現代捷徑。

> **環境**：gnu-efi，OVMF，QEMU。承接 Ch 12-15（UEFI 服務、記憶體、ExitBootServices）。

## 為什麼這章是 UEFI 線的終點？

Ch 13-15 你學了寫 UEFI app、管理記憶體、ExitBootServices。現在把它們組裝成 bootloader 的核心任務：**載入 kernel 並把控制權交給它**。

這是 UEFI bootloader 的全部意義——前面所有服務（檔案系統、記憶體、ExitBootServices）都是為了這一刻：讀進 kernel、準備好環境、交棒。完成這章，你就理解了 GRUB 的 UEFI 版（Ch 19）、systemd-boot 等所有 UEFI bootloader 在做什麼。

## 先建立直覺：bootloader 的核心三步

```
UEFI bootloader 載入 kernel 的核心：

  1. 讀 kernel 檔案進記憶體
     用 file system protocol 開 /vmlinuz，讀進 buffer
        │
  2. 準備 kernel 期待的環境
     - 取得 memory map（Ch 14）
     - 設定 kernel 需要的參數（boot params、command line）
     - ExitBootServices
        │
  3. 跳到 kernel entry point
     按 kernel 的 handover protocol（Ch 20）把控制權交過去
        │
  → kernel 接手，開始它的初始化（Part 5）
```

這三步是所有 bootloader 的共通骨架。GRUB 做得更複雜（多 OS 選單、解析多種格式），但核心就是這三步。

## 第一步：用檔案系統 protocol 讀 kernel

UEFI 提供檔案系統 protocol（Ch 12 的物件模型），能用路徑開檔讀檔：

```c
// 讀 kernel 檔案進記憶體（概念流程）
#include <efi.h>
#include <efilib.h>

EFI_STATUS load_kernel(EFI_HANDLE image, EFI_SYSTEM_TABLE *st,
                       CHAR16 *path, void **kernel_buf, UINTN *kernel_size)
{
    EFI_STATUS status;
    EFI_LOADED_IMAGE_PROTOCOL *loaded_image;
    EFI_SIMPLE_FILE_SYSTEM_PROTOCOL *fs;
    EFI_FILE_PROTOCOL *root, *kernel_file;

    // 1. 取得這個 .efi 是從哪個裝置載入的（LoadedImage protocol）
    EFI_GUID li_guid = LOADED_IMAGE_PROTOCOL;
    st->BootServices->HandleProtocol(image, &li_guid, (void**)&loaded_image);

    // 2. 取得那個裝置的檔案系統（SimpleFileSystem protocol）
    EFI_GUID fs_guid = SIMPLE_FILE_SYSTEM_PROTOCOL;
    st->BootServices->HandleProtocol(loaded_image->DeviceHandle,
                                      &fs_guid, (void**)&fs);

    // 3. 開啟根目錄
    fs->OpenVolume(fs, &root);

    // 4. 開啟 kernel 檔案
    status = root->Open(root, &kernel_file, path,
                        EFI_FILE_MODE_READ, 0);
    if (EFI_ERROR(status)) {
        Print(L"Cannot open kernel: %s\n", path);
        return status;
    }

    // 5. 取得檔案大小（讀 file info）
    // ... GetInfo 取得 FileSize ...
    UINTN size = /* kernel 檔案大小 */;

    // 6. 配置記憶體並讀進來
    st->BootServices->AllocatePool(EfiLoaderData, size, kernel_buf);
    kernel_file->Read(kernel_file, &size, *kernel_buf);
    *kernel_size = size;

    kernel_file->Close(kernel_file);
    return EFI_SUCCESS;
}
```

關鍵流程：
- **LoadedImage protocol**：取得「我這個 `.efi` 是從哪個裝置載入的」，從而知道去哪個裝置找 kernel
- **SimpleFileSystem protocol**：那個裝置的檔案系統，能 OpenVolume、Open、Read
- 用路徑（`L"\\vmlinuz"`）開檔，讀進 AllocatePool 配的 buffer

對比 BIOS 線（Ch 9）用 int 13h 讀固定 sector——UEFI 用路徑開檔，因為韌體提供檔案系統。這是「韌體裡的小作業系統」的好處（Ch 10）。

## 第二步：準備環境 + ExitBootServices

讀進 kernel 後，準備 kernel 期待的環境，然後 ExitBootServices（Ch 14）：

```c
// 準備並交棒（概念）
EFI_STATUS boot_kernel(EFI_HANDLE image, EFI_SYSTEM_TABLE *st,
                       void *kernel_buf, UINTN kernel_size)
{
    // 1. 解析 kernel header，找 entry point 和載入位址
    //    （Linux bzImage 有 setup header，Ch 20/21）
    //    把 kernel 搬到它要求的位址、填好 boot_params...

    // 2. 設定 kernel command line（如 "root=/dev/sda2 ro"）
    //    寫進 kernel 期待的位置（boot_params 的某欄位）

    // 3. ExitBootServices（Ch 14 的迴圈）
    //    取 memory map → ExitBootServices（失敗重試）
    EFI_MEMORY_DESCRIPTOR *map;
    UINTN map_size, map_key, desc_size;
    UINT32 desc_ver;
    EFI_STATUS status;
    do {
        // 取最新 memory map
        map_size = 0; map = NULL;
        st->BootServices->GetMemoryMap(&map_size, map, &map_key, &desc_size, &desc_ver);
        map_size += 2 * desc_size;
        st->BootServices->AllocatePool(EfiLoaderData, map_size, (void**)&map);
        st->BootServices->GetMemoryMap(&map_size, map, &map_key, &desc_size, &desc_ver);
        status = st->BootServices->ExitBootServices(image, map_key);
    } while (EFI_ERROR(status));

    // 4. 把 memory map 傳給 kernel（kernel 需要它，Ch 14）
    //    （寫進 boot_params 的 EFI 相關欄位）

    // 5. 跳到 kernel entry point（不返回！）
    void (*kernel_entry)(void) = /* kernel 的 entry point */;
    kernel_entry();   // 交棒，永不返回

    return EFI_SUCCESS;  // 到不了這裡
}
```

ExitBootServices 之後，bootloader 不能再用 Boot Services（Ch 14），只能準備好一切後直接跳 kernel。跳過去後 kernel 接手，bootloader 的使命結束。

## 第三步：handover protocol

「跳到 kernel entry point」不是隨便 jmp——要按 kernel 約定的 **handover protocol**（Ch 20 詳述）。kernel 期待：
- 自己被載到特定位址
- 某些暫存器/結構填好特定資訊（boot params、memory map、command line）
- 從特定 entry point 進入

```
Linux 的 handover（簡化，Ch 20 詳述）：
  - kernel（bzImage）有 setup header，描述它要載到哪、entry point 在哪
  - bootloader 填好 boot_params（zero page）：
    - 記憶體地圖、command line、initramfs 位址、framebuffer 資訊...
  - 跳到 kernel 的 64-bit entry point，rsi 指向 boot_params
        │
  kernel 從 boot_params 讀取所有它需要的資訊，開始初始化（Part 5）
```

這個 handover 的精確細節（boot_params 結構、entry point、暫存器約定）是 Ch 20 的主題。這裡的重點是：bootloader 和 kernel 之間有個嚴格的「介面契約」，bootloader 必須準確遵守，kernel 才能正確接手。

## Linux EFI stub：現代捷徑

上面的流程（寫 bootloader 讀 kernel、處理 handover）很繁瑣。Linux 提供一個聰明的捷徑——**EFI stub**：把 kernel 自己編譯成一個 `.efi`，韌體能直接執行 kernel，不需要中間的 bootloader！

```
傳統：韌體 → bootloader(.efi) → 讀 kernel → handover → kernel
EFI stub：韌體 → kernel(.efi) 直接執行！
        │
  kernel 的前面有一段「EFI stub」code：
    - 它讓 kernel 本身成為合法的 .efi（PE/COFF header）
    - 韌體執行 kernel.efi，stub 跑起來
    - stub 用 UEFI 服務做 bootloader 的工作（讀 initramfs、取 memory map、ExitBootServices）
    - 然後跳到 kernel 真正的初始化
        │
  → kernel 自己當 bootloader，省掉中間人
```

```bash
# Linux kernel 的 bzImage 本身就是個 .efi（如果 CONFIG_EFI_STUB=y）
file /boot/vmlinuz-$(uname -r)
# Linux kernel x86 boot executable bzImage ...
# 較新的會顯示 PE32+ executable (EFI application)

# 可以直接當 .efi 放 ESP，用 efibootmgr 指向它
sudo efibootmgr --create --disk /dev/sda --part 1 \
    --label "Linux Direct" \
    --loader '\vmlinuz' \
    --unicode 'root=/dev/sda2 initrd=\initramfs.img'
#   韌體直接執行 vmlinuz（EFI stub），不需要 GRUB！
```

> EFI stub 是現代 Linux 開機的重要簡化。它讓「韌體 → kernel」變成可能，省掉 bootloader。systemd-boot 和直接 UEFI 開機都靠它。但 GRUB 仍常用——因為它提供開機選單、多 OS、進階功能（如從 LVM/加密磁碟讀 kernel）。EFI stub 適合「單一 OS、kernel 在 ESP」的簡單場景；GRUB 適合複雜場景。理解 EFI stub，你會懂為什麼有些系統「沒有 GRUB 也能開機」。

## 故意對照：BIOS 線 vs UEFI 線載入 kernel

```
BIOS 線載入 kernel（Part 2 + Ch 9）：
  - boot sector → stage2
  - stage2 用 int 13h 讀固定 sector（kernel 在哪要寫死或自己解析 FS）
  - 自己切模式（real→protected→long）
  - 自己處理 handover
        │
UEFI 線載入 kernel（本章）：
  - .efi 用 file system protocol 用路徑讀 kernel
  - 已在 64-bit（不用切模式）
  - ExitBootServices 交棒
  - 或更簡單：EFI stub，kernel 自己當 bootloader
        │
  UEFI 線少了「切模式」和「自己寫磁碟驅動」這兩大苦工
```

這個對照總結了兩條線的根本差異：UEFI 把 BIOS bootloader 的苦工（模式切換、磁碟驅動）變成韌體服務，bootloader 簡單太多。

## 踩雷集錦

1. **ExitBootServices 後還用 file system protocol**：讀 kernel 必須在 ExitBootServices **之前**（file system 是 Boot Service）。流程：讀完 kernel → 取 map → ExitBootServices → 跳 kernel

2. **沒按 handover protocol 亂跳 kernel**：kernel 期待特定的環境（boot_params 填好、載到特定位址）。隨便 jmp 到 kernel buffer 不會動（Ch 20）

3. **忘記傳 memory map 給 kernel**：kernel 接管後需要記憶體地圖。ExitBootServices 用的 map 要傳給 kernel（boot_params 欄位）

4. **EFI stub 的 initrd 路徑用正斜線**：`initrd=\initramfs.img`（反斜線，UEFI 路徑風格）。用正斜線 stub 找不到 initramfs

5. **kernel command line 沒設 root**：kernel 啟動後要掛 root 檔案系統，需要 `root=` 參數。沒設 kernel panic（找不到 root，Ch 23）

6. **LoadedImage protocol 沒用對 handle**：要用「自己這個 .efi 的 image handle」查 LoadedImage，才知道從哪個裝置載入、去哪找 kernel

## 進階：shim、GRUB、kernel 的 UEFI 開機鏈

實際的 Linux UEFI 開機通常是個鏈：

```
完整的 UEFI Linux 開機鏈（含 Secure Boot，Ch 27）：

  韌體
    │ 執行 /EFI/ubuntu/shimx64.efi
    ▼
  shim（微軟簽署的小 bootloader，Ch 27）
    │ 驗證並執行 GRUB
    ▼
  GRUB（grubx64.efi）
    │ 顯示選單，讀 grub.cfg，載入 kernel
    ▼
  kernel（vmlinuz，可能有 EFI stub）
    │ 載入 initramfs，開始初始化
    ▼
  initramfs → init（Part 5）
```

shim 的存在是為了 Secure Boot（Ch 27）——微軟簽署 shim，shim 再驗證 GRUB/kernel，讓 Linux 能在 Secure Boot 下開機而不需要每個發行版都找微軟簽署。理解這個鏈，你會懂為什麼 Ubuntu 的 ESP 有 `shimx64.efi`、`grubx64.efi`、`vmlinuz` 三層。Ch 19（GRUB）和 Ch 27（Secure Boot）會深入這條鏈的各環。

## 動手練習

1. 概念追蹤：在你的 UEFI 系統，`ls /boot/efi/EFI/ubuntu/`（或你的發行版），找出 shim、GRUB 的 `.efi`。`file /boot/vmlinuz-$(uname -r)` 看 kernel 是不是 EFI stub（PE32+）

2. 試 EFI stub 直接開機（VM 安全）：把 kernel 和 initramfs 放 ESP，用 efibootmgr 建一個直接指向 vmlinuz 的開機項（含 root= 和 initrd= 參數），跳過 GRUB 開機

3. 讀 gnu-efi 或 systemd-boot 的 source，看「用 file system protocol 讀檔」的實際 code，對照本章的概念流程

4. 進階：寫一個簡單的 UEFI bootloader，讀一個檔案（不一定是 kernel，先讀個文字檔印出來），驗證你會用 file system protocol。這是練習 B 的暖身

## 本章重點整理

- UEFI bootloader 核心三步：用 file system protocol 讀 kernel → 準備環境 + ExitBootServices → 按 handover protocol 跳 kernel
- file system protocol（透過 LoadedImage → SimpleFileSystem）讓 bootloader 用路徑讀 kernel，不像 BIOS 要自己寫磁碟驅動
- 讀 kernel 必須在 ExitBootServices 之前（file system 是 Boot Service）；交棒要傳 memory map 給 kernel
- EFI stub 讓 kernel 自己成為 `.efi`，韌體直接執行 kernel，省掉中間 bootloader（現代捷徑）
- 實際 Linux UEFI 開機常是鏈：shim → GRUB → kernel（shim 為 Secure Boot，Ch 27）

## 自我檢核

- [ ] 能說出 UEFI bootloader 載入 kernel 的核心三步
- [ ] 知道為什麼讀 kernel 要在 ExitBootServices 之前
- [ ] 能解釋 EFI stub 是什麼、它如何省掉 bootloader
- [ ] 知道 UEFI 線比 BIOS 線少了哪兩大苦工（模式切換、磁碟驅動）
- [ ] 能說出 shim → GRUB → kernel 開機鏈各環的作用

## 延伸閱讀

### 官方文件

- **[Linux kernel: EFI stub documentation](https://www.kernel.org/doc/html/latest/admin-guide/efi-stub.html)**
  - **讀哪裡**：整頁，EFI stub 怎麼用、命令列怎麼傳
  - **學什麼**：kernel 當 .efi 直接開機的完整方式
  - **前提**：本章

- **[UEFI Spec, Section 13 (Protocols - Media Access)](https://uefi.org/specifications)**
  - **讀哪裡**：Simple File System Protocol、File Protocol
  - **學什麼**：用 protocol 讀檔的精確 API
  - **前提**：Ch 12 + 本章

### 部落格 / 文章

- **[Writing a UEFI bootloader (loading a kernel)](https://krinkinmu.github.io/2020/10/12/efi-loading-kernel.html)** — Mike Krinkin
  - **這篇說什麼**：UEFI bootloader 讀 kernel、ExitBootServices、跳 kernel 的完整實作
  - **讀哪裡**：loading 和 jumping to kernel 那幾節
  - **為什麼值得讀**：把本章的概念流程變成可跑的 code

→ [練習 B：UEFI app（memory map + 讀檔 + ExitBootServices）](./practice-b-uefi-app.md)
