# 練習 B — UEFI app（memory map + 讀檔 + ExitBootServices）

> **目標**：整合 Ch 10–16 的 UEFI 知識，寫一個完整的 UEFI application，完成 bootloader 的所有核心動作（除了真的跳 kernel）：印出韌體資訊、列出記憶體地圖、用 file system protocol 讀一個檔案、正確執行 ExitBootServices 迴圈。完成後你掌握了 UEFI bootloader 的全部技術環節。

## 背景與動機

練習 A 你用 BIOS 線寫了完整的兩階段 bootloader。這個練習是 UEFI 線的對應——但因為 UEFI 提供豐富服務，你不用切模式、不用寫磁碟驅動，而是用 UEFI 的 protocol 和服務。

完成後對比練習 A 和 B，你會深刻體會兩條線的差異：BIOS 線是「赤裸硬體求生」，UEFI 線是「在韌體 OS 上寫應用程式」。這個練習涵蓋了真實 UEFI bootloader（GRUB UEFI、systemd-boot）的所有技術環節。

## 任務規格

寫一個 UEFI app，依序完成：

| 功能 | 要求 | 對應章節 |
|---|---|---|
| 1. 韌體資訊 | 印出 FirmwareVendor、FirmwareRevision、UEFI 版本 | Ch 12-13 |
| 2. 記憶體地圖 | GetMemoryMap，印出所有 descriptor（type/起始/頁數），統計可用 RAM 總量 | Ch 14 |
| 3. 讀檔案 | 用 file system protocol 讀 ESP 上的一個檔案（如 `\config.txt`），印出內容 | Ch 16 |
| 4. ExitBootServices | 正確的迴圈（取 map → exit → 失敗重試），成功後印一個訊息（用 Runtime Service，因為 Boot Service 已失效）| Ch 14 |

**驗收標準**：
- 在 OVMF 跑，依序印出四項
- 記憶體地圖統計的可用 RAM 接近 QEMU 給的 `-m` 大小
- 能讀出 ESP 上放的檔案內容
- ExitBootServices 成功（之後不能用 Print，因為它走 Boot Service 的 ConOut——要改用別的方式或就停住）
- 故意在取 map 和 ExitBootServices 之間 Print，能重現 map_key 過期

**技術限制**：
- gnu-efi，C
- ExitBootServices 後不呼叫任何 Boot Service
- 用 desc_size 跨步遍歷 memory descriptor（不用 sizeof）

## 期望輸出範例

```
=== UEFI Bootloader Practice ===
Firmware: EDK II rev 0x10000
UEFI version: 2.70

Memory Map (143 entries):
  Type=7 (Conventional) Start=0x0        Pages=159
  Type=7 (Conventional) Start=0x100000   Pages=...
  Type=4 (BootServData) Start=0x...      Pages=...
  ...
Total usable RAM: 2032 MB

Reading \config.txt:
  hello from the ESP file!

Exiting boot services...
Boot services exited successfully.
（之後系統停住——正常，因為我們沒跳 kernel）
```

## 如果你卡住了

1. 分步驗證：先做韌體資訊（最簡單，Ch 13），再加 memory map，再加讀檔，最後 ExitBootServices
2. GetMemoryMap 要兩次呼叫（第一次問大小，配 buffer，第二次取）—Ch 14
3. 遍歷 descriptor 用 `desc_size` 跨步，不是 `sizeof(EFI_MEMORY_DESCRIPTOR)`—Ch 14 的雷
4. 讀檔要先 HandleProtocol 取 LoadedImage → 取 SimpleFileSystem → OpenVolume → Open → Read—Ch 16
5. ExitBootServices 後 `Print` 不能用（它走 ConOut，是 Boot Service）。成功後要嘛用 Runtime Service，要嘛就停住
6. ExitBootServices 失敗（map_key 過期）要重試—Ch 14 的迴圈

## 實作步驟建議

### Step 1：韌體資訊（暖身，確認環境）
### Step 2：記憶體地圖 + 統計
### Step 3：讀 ESP 檔案
### Step 4：ExitBootServices 迴圈
### Step 5：整合 + 故意弄壞測試（map_key 過期）

## 完整參考解答

**寫完再看！**

<details>
<summary>bootloader.c</summary>

```c
#include <efi.h>
#include <efilib.h>

EFI_STATUS efi_main(EFI_HANDLE ImageHandle, EFI_SYSTEM_TABLE *ST)
{
    InitializeLib(ImageHandle, ST);
    EFI_BOOT_SERVICES *BS = ST->BootServices;
    EFI_STATUS status;

    Print(L"=== UEFI Bootloader Practice ===\r\n");

    // === Step 1: 韌體資訊 ===
    Print(L"Firmware: %s rev 0x%x\r\n",
          ST->FirmwareVendor, ST->FirmwareRevision);
    Print(L"UEFI version: %d.%d\r\n",
          ST->Hdr.Revision >> 16, ST->Hdr.Revision & 0xFFFF);

    // === Step 2: 記憶體地圖 ===
    EFI_MEMORY_DESCRIPTOR *map = NULL;
    UINTN map_size = 0, map_key, desc_size;
    UINT32 desc_ver;

    // 第一次：問大小
    BS->GetMemoryMap(&map_size, map, &map_key, &desc_size, &desc_ver);
    map_size += 2 * desc_size;
    BS->AllocatePool(EfiLoaderData, map_size, (void**)&map);
    // 第二次：真正取得
    status = BS->GetMemoryMap(&map_size, map, &map_key, &desc_size, &desc_ver);
    if (EFI_ERROR(status)) { Print(L"GetMemoryMap failed\r\n"); return status; }

    UINTN entries = map_size / desc_size;
    Print(L"\r\nMemory Map (%d entries):\r\n", entries);
    UINT64 usable_pages = 0;
    EFI_MEMORY_DESCRIPTOR *d = map;
    for (UINTN i = 0; i < entries; i++) {
        if (d->Type == EfiConventionalMemory)
            usable_pages += d->NumberOfPages;
        // 只印前幾個避免洗版
        if (i < 8)
            Print(L"  Type=%d Start=0x%lx Pages=%ld\r\n",
                  d->Type, d->PhysicalStart, d->NumberOfPages);
        // 用 desc_size 跨步！不是 sizeof
        d = (EFI_MEMORY_DESCRIPTOR*)((CHAR8*)d + desc_size);
    }
    Print(L"Total usable RAM: %ld MB\r\n", usable_pages * 4096 / 1024 / 1024);

    // === Step 3: 讀 ESP 檔案 ===
    EFI_LOADED_IMAGE_PROTOCOL *li;
    EFI_GUID li_guid = LOADED_IMAGE_PROTOCOL;
    BS->HandleProtocol(ImageHandle, &li_guid, (void**)&li);

    EFI_SIMPLE_FILE_SYSTEM_PROTOCOL *fs;
    EFI_GUID fs_guid = SIMPLE_FILE_SYSTEM_PROTOCOL;
    BS->HandleProtocol(li->DeviceHandle, &fs_guid, (void**)&fs);

    EFI_FILE_PROTOCOL *root, *file;
    fs->OpenVolume(fs, &root);
    status = root->Open(root, &file, L"config.txt", EFI_FILE_MODE_READ, 0);
    if (!EFI_ERROR(status)) {
        Print(L"\r\nReading \\config.txt:\r\n  ");
        CHAR8 buf[256];
        UINTN sz = sizeof(buf) - 1;
        file->Read(file, &sz, buf);
        buf[sz] = 0;
        // 簡單印出（ASCII 轉印，因為 Print 要 UTF-16）
        for (UINTN i = 0; i < sz; i++) {
            CHAR16 c[2] = { buf[i], 0 };
            Print(L"%s", c);
        }
        Print(L"\r\n");
        file->Close(file);
    } else {
        Print(L"\r\nconfig.txt not found\r\n");
    }

    // === Step 4: ExitBootServices 迴圈 ===
    Print(L"\r\nExiting boot services...\r\n");
    BS->FreePool(map);  // 釋放舊 map

    do {
        // 取最新 map（注意：取 map 後立刻 exit，中間不做事！）
        map_size = 0; map = NULL;
        BS->GetMemoryMap(&map_size, map, &map_key, &desc_size, &desc_ver);
        map_size += 2 * desc_size;
        BS->AllocatePool(EfiLoaderData, map_size, (void**)&map);
        BS->GetMemoryMap(&map_size, map, &map_key, &desc_size, &desc_ver);

        status = BS->ExitBootServices(ImageHandle, map_key);
        if (EFI_ERROR(status)) {
            BS->FreePool(map);  // key 過期，重來
        }
    } while (EFI_ERROR(status));

    // ExitBootServices 成功！Boot Services 已失效，不能再用 Print
    // （Print 走 ConOut，是 Boot Service）
    // 用 Runtime Service 證明還活著：等一下然後 reset
    // （或直接停住）
    for (volatile int i = 0; i < 100000000; i++) ;  // 忙等一下

    // 用 Runtime Service ResetSystem（這個 ExitBootServices 後仍可用）
    ST->RuntimeServices->ResetSystem(EfiResetShutdown, EFI_SUCCESS, 0, NULL);

    return EFI_SUCCESS;  // 到不了
}
```

</details>

<details>
<summary>Makefile + 執行</summary>

```makefile
ARCH := x86_64
EFIINC := /usr/include/efi
EFIINCS := -I$(EFIINC) -I$(EFIINC)/$(ARCH) -I$(EFIINC)/protocol
EFILIB := /usr/lib
EFI_CRT_OBJS := $(EFILIB)/crt0-efi-$(ARCH).o
EFI_LDS := $(EFILIB)/elf_$(ARCH)_efi.lds
CFLAGS := $(EFIINCS) -fno-stack-protector -fpic -fshort-wchar \
          -mno-red-zone -Wall -DEFI_FUNCTION_WRAPPER
LDFLAGS := -nostdlib -znocombreloc -T $(EFI_LDS) -shared -Bsymbolic \
           -L $(EFILIB) $(EFI_CRT_OBJS)

bootloader.efi: bootloader.so
	objcopy -j .text -j .sdata -j .data -j .dynamic -j .dynsym \
	        -j .rel -j .rela -j .reloc --target efi-app-$(ARCH) $< $@
bootloader.so: bootloader.o
	ld $(LDFLAGS) $< -o $@ -lefi -lgnuefi
bootloader.o: bootloader.c
	gcc $(CFLAGS) -c $< -o $@

esp.img: bootloader.efi
	dd if=/dev/zero of=esp.img bs=1M count=64
	mkfs.vfat esp.img
	mmd -i esp.img ::/EFI ::/EFI/BOOT
	mcopy -i esp.img bootloader.efi ::/EFI/BOOT/BOOTX64.EFI
	echo "hello from the ESP file!" > config.txt
	mcopy -i esp.img config.txt ::/config.txt

run: esp.img
	cp /usr/share/OVMF/OVMF_VARS.fd .
	qemu-system-x86_64 \
	  -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE.fd \
	  -drive if=pflash,format=raw,file=OVMF_VARS.fd \
	  -drive format=raw,file=esp.img -m 2048 -net none

clean:
	rm -f *.o *.so *.efi esp.img config.txt OVMF_VARS.fd

.PHONY: run clean
```

```bash
make run    # 看四項輸出
```

**解答說明**：

- **desc_size 跨步**：遍歷 memory descriptor 用 `(CHAR8*)d + desc_size`，不是 `d + 1`（後者用 sizeof）。這是 Ch 14 的關鍵雷
- **ExitBootServices 迴圈**：取 map 後**立刻** exit，中間不做事（不 Print、不額外 allocate）。失敗（key 過期）就釋放 map 重來
- **ExitBootServices 後不用 Print**：Print 走 ConOut（Boot Service），exit 後失效。所以成功後改用 Runtime Service（ResetSystem）或停住
- **讀檔的 protocol 鏈**：HandleProtocol 取 LoadedImage（知道從哪載入）→ 取 SimpleFileSystem → OpenVolume → Open → Read（Ch 16）
- **記憶體統計**：只累加 EfiConventionalMemory（可用 RAM），結果接近 QEMU 的 `-m 2048`（會略少，因韌體佔用一些）

</details>

## 測試用案例

| 操作 | 預期 | 驗證 |
|---|---|---|
| `make run` | 四項依序輸出 | 完整 bootloader 流程 |
| 記憶體統計 | 接近 2048 MB（`-m 2048`）| GetMemoryMap 正確 |
| 讀 config.txt | 印出檔案內容 | file system protocol |
| ExitBootServices | 成功（系統 reset/停住）| 交棒儀式正確 |
| 故意在取 map/exit 間 Print | ExitBootServices 失敗重試或卡住 | map_key 機制 |
| 用 sizeof 代替 desc_size | memory map 印出錯位垃圾 | desc_size 的重要性 |

## 延伸挑戰（加分）

- **挑戰一**：真的讀一個 kernel 檔案（放個假 kernel 或真的 vmlinuz 到 ESP），解析它的 PE/bzImage header，印出 entry point 位址（通往 Ch 20/21，但不真跳）

- **挑戰二**：把 memory map 印成像 `efibootmgr` 的 `memmap` 那樣的表格，並按 type 分類統計（多少 Conventional、多少 BootServices、多少 MMIO）

- **挑戰三**：用 Graphics Output Protocol（GOP）取得 framebuffer 資訊（解析度、位址），在螢幕上畫一個方塊（不用文字模式）——體驗 UEFI 的圖形 protocol

- **挑戰四**：真的跳到一個簡單的「kernel」（你自己寫的一段 64-bit code 放在記憶體），ExitBootServices 後 jmp 過去執行——這就是完整 bootloader 的最後一步（為 Final Project 鋪路）

## 自我檢核

- [ ] 能不看參考寫出 GetMemoryMap 的兩次呼叫和正確的 descriptor 遍歷（desc_size 跨步）
- [ ] 能用 file system protocol 鏈（LoadedImage → SimpleFileSystem → Open → Read）讀檔案
- [ ] 能寫出正確的 ExitBootServices 迴圈（取 map → 立刻 exit → 失敗重試）
- [ ] 理解 ExitBootServices 後為什麼不能用 Print，能用什麼（Runtime Service）
- [ ] 能對比練習 A（BIOS）和練習 B（UEFI），說出兩條線的根本差異

→ [Ch 17 Bootloader 的角色與生態](./17-bootloader-ecosystem.md)
