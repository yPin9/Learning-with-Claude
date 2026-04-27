# Ch 12 — 動手：用 gnu-efi 寫 minimal UEFI app

> 目標：寫一支可在 UEFI 上執行的 PE/COFF app，用 `gnu-efi` 編譯，在 OVMF 上跑出 hello world。

## 我們在哪裡

第 3 階段 (Bootloader) 的 UEFI 版。對照 Ch 6 的 hello boot sector。

## 為什麼用 gnu-efi

寫 UEFI app 有兩個主流選擇：

- **EDK2**：Intel/Tianocore 官方的 UEFI 開發套件。完整、強大，但 build system 複雜，學習曲線陡
- **gnu-efi**：一組 header + tiny lib + linker script，配 GCC 直接 build。簡單，適合教學跟小工具

我們用 gnu-efi。

## 安裝

```bash
sudo apt install gnu-efi
```

裝完後檢查：

```bash
ls /usr/include/efi/        # header
ls /usr/lib/elf_x86_64_efi.lds  # linker script
ls /usr/lib/crt0-efi-x86_64.o   # crt0
```

## 完整原始碼

`hello.c`：

```c
#include <efi.h>
#include <efilib.h>

EFI_STATUS
EFIAPI
efi_main(EFI_HANDLE ImageHandle, EFI_SYSTEM_TABLE *SystemTable)
{
    InitializeLib(ImageHandle, SystemTable);

    Print(L"Hello from UEFI app!\n");
    Print(L"Firmware vendor: %s\n", SystemTable->FirmwareVendor);
    Print(L"Firmware revision: %x\n", SystemTable->FirmwareRevision);

    Print(L"\nPress any key to exit...\n");

    EFI_INPUT_KEY key;
    SystemTable->ConIn->Reset(SystemTable->ConIn, FALSE);
    UINTN index;
    SystemTable->BootServices->WaitForEvent(1, &SystemTable->ConIn->WaitForKey, &index);
    SystemTable->ConIn->ReadKeyStroke(SystemTable->ConIn, &key);

    return EFI_SUCCESS;
}
```

## Build 流程

UEFI app 是 PE/COFF，但 GCC 直接產 PE 會麻煩。慣例做法：

1. 用 GCC 編成 ELF shared object
2. 用 `objcopy` 轉成 PE/COFF

`Makefile`：

```makefile
ARCH    = x86_64
EFI_INC = /usr/include/efi
EFI_LIB = /usr/lib

CFLAGS  = -I$(EFI_INC) -I$(EFI_INC)/$(ARCH) \
          -fno-stack-protector -fpic -fshort-wchar \
          -mno-red-zone -DEFI_FUNCTION_WRAPPER

LDFLAGS = -nostdlib -znocombreloc -T $(EFI_LIB)/elf_$(ARCH)_efi.lds \
          -shared -Bsymbolic -L$(EFI_LIB) \
          $(EFI_LIB)/crt0-efi-$(ARCH).o

LIBS    = -lefi -lgnuefi

all: hello.efi

hello.o: hello.c
	gcc $(CFLAGS) -c $< -o $@

hello.so: hello.o
	ld $(LDFLAGS) $< -o $@ $(LIBS)

hello.efi: hello.so
	objcopy -j .text -j .sdata -j .data -j .dynamic \
	        -j .dynsym -j .rel -j .rela -j .reloc \
	        --target=efi-app-$(ARCH) $< $@

clean:
	rm -f *.o *.so *.efi

.PHONY: all clean
```

build：

```bash
make
file hello.efi
# hello.efi: PE32+ executable (EFI application) x86-64 (stripped)
```

`PE32+ executable (EFI application)` 表示成功。

## 跑起來：在 OVMF 上

UEFI app 不能直接跑，要放進 ESP 然後讓 UEFI 找到。

最快的辦法：用 QEMU 的 `-drive file=fat:rw:<dir>` — QEMU 會把資料夾當 FAT32 磁碟掛上來。

```bash
mkdir -p esp/EFI/BOOT
cp hello.efi esp/EFI/BOOT/BOOTX64.EFI

qemu-system-x86_64 -m 256 \
  -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE.fd \
  -drive if=pflash,format=raw,file=/tmp/OVMF_VARS_test.fd \
  -drive format=raw,file=fat:rw:esp \
  -nographic
```

第一次跑前先複製一份 OVMF_VARS：

```bash
cp /usr/share/OVMF/OVMF_VARS.fd /tmp/OVMF_VARS_test.fd
```

跑起來會看到：

```
Hello from UEFI app!
Firmware vendor: EDK II
Firmware revision: 10000
```

按任意鍵 exit，掉回 UEFI shell。

## 逐段解說

### `efi_main` 簽名

```c
EFI_STATUS EFIAPI efi_main(EFI_HANDLE, EFI_SYSTEM_TABLE *)
```

- `EFI_STATUS`：UEFI 統一回傳碼，0 是 success，bit 31 set 是 error
- `EFIAPI`：Microsoft x64 ABI calling convention（不是 SystemV）。重要 — UEFI 韌體用這個 ABI 呼叫你
- `EFI_HANDLE ImageHandle`：你這個 image 的 handle
- `EFI_SYSTEM_TABLE *SystemTable`：所有 service 的入口（Ch 10 講過）

`EFIAPI` 在 GCC 上展開成 `__attribute__((ms_abi))`。如果不寫，GCC 用 SystemV ABI 傳參，UEFI 韌體 call 進來會傳錯參數。

### `InitializeLib`

gnu-efi 的初始化。把全域變數 `ST` (SystemTable)、`BS` (BootServices)、`RT` (RuntimeServices) 設好，後面 `Print` 等函式才能用。

### `Print(L"...")`

`Print` 是 gnu-efi 提供的 wrapper，類似 `printf`。`L"..."` 是 wide string (UTF-16) — UEFI console 全部用 UTF-16，不能用 ASCII。

`%s` 印 wide string，`%a` 印 ASCII string，`%x` 印 hex，`%d` 印整數。

### 等鍵盤

```c
SystemTable->ConIn->Reset(...);
SystemTable->BootServices->WaitForEvent(...);
SystemTable->ConIn->ReadKeyStroke(...);
```

`ConIn` 是 `EFI_SIMPLE_TEXT_INPUT_PROTOCOL`。`WaitForEvent` 等一個 event 觸發；`WaitForKey` 是 protocol 預先建好的 event，按鍵時觸發。

不等鍵就直接 return，UEFI 會繼續找下一個 boot entry，畫面瞬間閃過。等鍵是讓你看訊息。

## CFLAGS 重要 flag 解說

| Flag | 為什麼 |
|---|---|
| `-fno-stack-protector` | UEFI 沒有 `__stack_chk_fail`，開了會 link 失敗 |
| `-fpic` | UEFI app 載入位址不固定，必須是 position-independent |
| `-fshort-wchar` | `wchar_t` 變 16-bit（UEFI 要 UTF-16） |
| `-mno-red-zone` | x86_64 SystemV 的 red zone 在 UEFI 中斷不安全，關掉 |
| `-DEFI_FUNCTION_WRAPPER` | 用 EFI ABI wrapper |

少寫一個都 link 不過或 runtime crash。

## 加碼：列舉所有 boot entry

實用版本：印出所有 NVRAM 裡的 boot entry。

```c
#include <efi.h>
#include <efilib.h>

EFI_STATUS EFIAPI efi_main(EFI_HANDLE ImageHandle, EFI_SYSTEM_TABLE *SystemTable)
{
    InitializeLib(ImageHandle, SystemTable);

    UINT16 *boot_order;
    UINTN size = 0;
    EFI_GUID global = EFI_GLOBAL_VARIABLE;

    // 第一次呼叫，size = 0，會回 BUFFER_TOO_SMALL，把需要的大小寫進 size
    EFI_STATUS s = uefi_call_wrapper(RT->GetVariable, 5,
        L"BootOrder", &global, NULL, &size, NULL);

    boot_order = AllocatePool(size);
    s = uefi_call_wrapper(RT->GetVariable, 5,
        L"BootOrder", &global, NULL, &size, boot_order);

    UINTN count = size / sizeof(UINT16);
    Print(L"Boot order has %d entries:\n", count);
    for (UINTN i = 0; i < count; i++) {
        Print(L"  Boot%04x\n", boot_order[i]);
    }

    FreePool(boot_order);
    return EFI_SUCCESS;
}
```

`uefi_call_wrapper` 是 gnu-efi 的呼叫慣例 wrapper，第二個參數是 function 的參數個數。

## 一個常見踩雷：忘了 EFIAPI

```c
EFI_STATUS efi_main(EFI_HANDLE handle, EFI_SYSTEM_TABLE *st)  // ❌
```

GCC 用 SystemV ABI 編譯這個 function。UEFI 韌體用 MS x64 ABI 傳參數 — `handle` 跟 `st` 在錯的 register。實際看到的症狀：`Print` 印鬼字，或 segfault。

修：加上 `EFIAPI`。

## 一個常見踩雷：用 ASCII string

```c
Print("Hello\n");      // ❌ 缺 L
Print(L"%s", "ASCII"); // ❌ %s 期待 wide
```

UEFI console 是 UTF-16。沒前綴 `L` 的字串是 1 byte 一個字元，UEFI 解讀成 2 byte 會印鬼字。

修：所有字串前加 `L`。`%a` 印 ASCII。

## 一個常見踩雷：忘了 ExitBootServices

我們這個 hello app **不用** call ExitBootServices — 因為它是個 app，不是 OS bootloader。Return 之後控制權回給 UEFI。

但實作 bootloader 時必須：

```c
EFI_STATUS s = gBS->ExitBootServices(ImageHandle, mapKey);
// 之後 boot service 全部 invalid
```

漏掉的話 OS 會以為自己接管硬體了，但 UEFI 還在背景跑，互相打架。Ch 14 載 kernel 時會碰。

## 動手練習

**1. 跑出來**

按上面 build + 跑，看到 hello 訊息。

**2. 改字串**

加你自己的 banner、顯示記憶體大小（`gST->BootServices->GetMemoryMap`）、印 ImageHandle 的數字。

**3. 寫一個簡單 menu**

```c
Print(L"1) Boot Linux\n2) Boot Windows\n3) Exit\nChoice: ");
```

讀一個按鍵，根據選擇 print 不同訊息。為下一章 chainload 做準備。

**4. 用 efibootmgr 註冊你的 app（在虛擬機）**

把 `hello.efi` 放到 `esp/EFI/BOOT/BOOTX64.EFI`（fallback 路徑），不需註冊；或放 `esp/EFI/mine/hello.efi`，進 UEFI shell：

```
Shell> bcfg boot add 5 fs0:\EFI\mine\hello.efi "MyApp"
Shell> bcfg boot dump
```

`bcfg` 是 UEFI shell 的 boot config 工具。

## 自我檢核

- [ ] 寫出一支 UEFI app，用 gnu-efi build 出 PE/COFF
- [ ] 知道 `EFIAPI` / `L"..."` / `-fpic` / `-mno-red-zone` 各自為什麼
- [ ] 在 OVMF + QEMU 跑起來
- [ ] 看到 `Print("Firmware vendor")` 印出 EDK II
- [ ] 用 `gST->ConIn` 等鍵盤

下一章看現實世界：UEFI 模式下 GRUB2 跟 systemd-boot 怎麼運作。

→ [Ch 13 UEFI 下的 GRUB2 與 systemd-boot](./13-uefi-bootloaders.md)
