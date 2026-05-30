# Ch 13 — 寫一個 UEFI application

> **目標**：親手寫一個 UEFI application——用 gnu-efi 寫 C、理解 `.efi` 的 PE/COFF 格式、entry point 簽名、用 ConOut 印字、編譯成 `.efi`、放進 ESP、用 OVMF 在 QEMU 跑起來。這是 UEFI 線的第一個動手里程碑，對比 Ch 6 的 BIOS boot sector。

> **環境**：gnu-efi 3.0.15，gcc，OVMF，QEMU，mtools。承接 Ch 10-12（UEFI 概念、ESP、服務）。

## 為什麼 UEFI app 比 boot sector 簡單？

Ch 6 你用 16-bit assembly 寫 boot sector，要 `org 0x7c00`、搭舞台、湊 512 bytes。UEFI app 完全不同——你用 **C**，寫一個有 `efi_main` 的程式，呼叫 UEFI 服務印字，編譯成 `.efi`。沒有模式切換（已在 64-bit）、沒有 512 bytes 限制、沒有手刻 assembly。

```
對比兩條線的 "Hello World"：

BIOS（Ch 6）：              UEFI（本章）：
  16-bit assembly            64-bit C
  org 0x7c00                 efi_main()
  搭舞台（segment/stack）     直接用 system_table
  int 10h 印字               ConOut->OutputString()
  湊 512 bytes               編譯成 .efi（任意大小）
  raw binary                 PE/COFF 格式
```

這章你會體驗 UEFI 「韌體裡的小作業系統」的好處——寫 bootloader 像寫普通 C 程式。

## 先建立直覺：UEFI app 是個有特殊進入點的 C 程式

```
普通 C 程式：           UEFI application：
  int main(...)           EFI_STATUS efi_main(
                            EFI_HANDLE ImageHandle,
                            EFI_SYSTEM_TABLE *SystemTable)
  printf(...)             SystemTable->ConOut->OutputString(...)
  編譯成 ELF/PE           編譯成 PE/COFF (.efi)
  OS 載入執行             UEFI 韌體載入執行
        │
  差別：進入點簽名、用 UEFI 服務而非 libc、PE 格式
  相同：都是 C，有函式、變數、控制流
```

## gnu-efi：UEFI 開發的 C 環境

寫 UEFI app 不能用普通 libc（沒有 OS）。**gnu-efi** 提供 UEFI 的 headers、startup code、和簡化的 library：

```bash
# 確認 gnu-efi 安裝（Ch 0 裝過）
ls /usr/include/efi/          # headers: efi.h, efilib.h...
ls /usr/lib/crt0-efi-x86_64.o # UEFI startup object
ls /usr/lib/elf_x86_64_efi.lds# linker script
```

gnu-efi 提供：
- **headers**：UEFI 的型別、結構、protocol 定義（`EFI_SYSTEM_TABLE` 等）
- **crt0**：UEFI 的啟動 code（設定環境、呼叫你的 `efi_main`）
- **linker script**：產生正確的 PE/COFF 佈局
- **efilib**：簡化的工具函式（`Print` 等）

## 最簡單的 UEFI app

```c
// hello.c — 最簡單的 UEFI application
#include <efi.h>
#include <efilib.h>

// UEFI app 的進入點（gnu-efi 約定叫 efi_main）
EFI_STATUS efi_main(EFI_HANDLE ImageHandle, EFI_SYSTEM_TABLE *SystemTable)
{
    // 初始化 gnu-efi 的 library（設定全域指標）
    InitializeLib(ImageHandle, SystemTable);

    // 方法一：直接用 System Table 的 ConOut（Ch 12）
    SystemTable->ConOut->OutputString(SystemTable->ConOut,
                                       L"Hello from UEFI!\r\n");
    //                                  ↑ L"..." 是 UTF-16 字串（UEFI 用 UTF-16）

    // 方法二：用 gnu-efi 的 Print（更方便，像 printf）
    Print(L"Running as a UEFI application\r\n");
    Print(L"Firmware vendor: %s\r\n", SystemTable->FirmwareVendor);

    // 等 5 秒（用 Boot Service Stall，單位微秒）
    SystemTable->BootServices->Stall(5 * 1000 * 1000);  // 5 秒

    return EFI_SUCCESS;
}
```

關鍵點：
- **`efi_main`**：進入點，收到 `ImageHandle`（這個 app 的 handle）和 `SystemTable`（服務入口，Ch 12）
- **`InitializeLib`**：gnu-efi 的初始化，設定全域指標讓 `Print` 等函式能用
- **`L"..."`**：UEFI 字串是 UTF-16（寬字元），不是 ASCII。字串前綴 `L`
- **`\r\n`**：UEFI 控制台要 `\r\n`（不只 `\n`）
- **`ConOut->OutputString`**：Ch 12 講的控制台輸出 protocol

## 編譯成 .efi

UEFI app 的編譯比普通 C 複雜——要產生 PE/COFF 格式。流程：C → ELF（特殊選項）→ PE/COFF（`.efi`）：

```makefile
# Makefile for UEFI app
ARCH      := x86_64
EFIINC    := /usr/include/efi
EFIINCS   := -I$(EFIINC) -I$(EFIINC)/$(ARCH) -I$(EFIINC)/protocol
LIB       := /usr/lib
EFILIB    := /usr/lib
EFI_CRT_OBJS := $(EFILIB)/crt0-efi-$(ARCH).o
EFI_LDS   := $(EFILIB)/elf_$(ARCH)_efi.lds

CFLAGS    := $(EFIINCS) -fno-stack-protector -fpic -fshort-wchar \
             -mno-red-zone -Wall -DEFI_FUNCTION_WRAPPER
LDFLAGS   := -nostdlib -znocombreloc -T $(EFI_LDS) -shared -Bsymbolic \
             -L $(EFILIB) $(EFI_CRT_OBJS)

hello.efi: hello.so
	# 從 ELF shared object 轉成 PE/COFF (.efi)
	objcopy -j .text -j .sdata -j .data -j .dynamic -j .dynsym \
	        -j .rel -j .rela -j .reloc \
	        --target efi-app-$(ARCH) $< $@

hello.so: hello.o
	ld $(LDFLAGS) $< -o $@ -lefi -lgnuefi

hello.o: hello.c
	gcc $(CFLAGS) -c $< -o $@

clean:
	rm -f hello.o hello.so hello.efi
```

關鍵編譯選項：
- `-fpic`：position-independent（UEFI 載到任意位址）
- `-fshort-wchar`：`wchar_t` 是 16-bit（UEFI 用 UTF-16）
- `-mno-red-zone`：關掉 red zone（UEFI 中斷可能破壞它）
- `objcopy --target efi-app`：把 ELF 轉成 PE/COFF（`.efi`）

> 編譯流程繞是因為 gnu-efi 用「先編 ELF 再轉 PE」的方式（GNU 工具鏈不直接出 PE）。現代替代方案是用 **clang/lld**（能直接出 PE）或 **EDK II**（UEFI 官方 SDK）。gnu-efi 較簡單適合學習。各選項的意義照範本用即可，不用每個都背。

## 放進 ESP 並用 OVMF 跑

`.efi` 編好後，放進 FAT 格式的 ESP，用 OVMF 開機：

```bash
# 建立 FAT image 當 ESP
dd if=/dev/zero of=esp.img bs=1M count=64
mkfs.vfat esp.img

# 把 .efi 放到後備開機路徑 /EFI/BOOT/BOOTX64.EFI（Ch 10）
mmd -i esp.img ::/EFI
mmd -i esp.img ::/EFI/BOOT
mcopy -i esp.img hello.efi ::/EFI/BOOT/BOOTX64.EFI
#   韌體找不到別的開機項時，會執行這個後備路徑

# 用 OVMF 開機
cp /usr/share/OVMF/OVMF_VARS.fd .
qemu-system-x86_64 \
    -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE.fd \
    -drive if=pflash,format=raw,file=OVMF_VARS.fd \
    -drive format=raw,file=esp.img \
    -net none
# OVMF 韌體啟動 → 找到 /EFI/BOOT/BOOTX64.EFI → 執行 → 印 "Hello from UEFI!"
```

`/EFI/BOOT/BOOTX64.EFI` 是 UEFI 的「後備開機路徑」——韌體找不到 NVRAM 變數指定的開機項時，會試這個固定路徑（Ch 10）。把你的 app 放這，韌體就會執行它。

## 用 UEFI shell 手動執行（另一種方式）

也可以進 UEFI shell 手動跑 `.efi`：

```bash
# 把 .efi 放 ESP 任意位置（不一定要 BOOTX64.EFI）
mcopy -i esp.img hello.efi ::/hello.efi

# 開機（OVMF 進 UEFI shell）
qemu-system-x86_64 ... -drive format=raw,file=esp.img
# 在 UEFI shell：
# Shell> fs0:           ← 切到 ESP（檔案系統 0）
# FS0:\> hello.efi      ← 執行你的 app
# Hello from UEFI!
```

UEFI shell 是個互動環境，能手動執行 `.efi`、看記憶體地圖（`memmap`）、看變數（`dmpstore`）——對 debug UEFI app 很有用。

## 故意弄壞：忘記 -fshort-wchar

```c
// 用普通 char 字串而非 UTF-16
SystemTable->ConOut->OutputString(SystemTable->ConOut, "Hello");
//                                                       ↑ 沒有 L 前綴
// 編譯可能過，但 UEFI 把它當 UTF-16 解讀 ASCII bytes
// → 印出亂碼或只印一個字（ASCII 'H'=0x48，後面的 0x00 被當字串結尾）
```

UEFI 字串是 UTF-16。用普通 `char` 字串（ASCII），UEFI 把 ASCII bytes 當 UTF-16 解讀——`"Hello"` 的 `H`(0x48) `e`(0x65) 被當成一個 UTF-16 字元 0x6548，且 ASCII 字串裡的 0x00 會被當字串結尾。結果是亂碼。一定用 `L"..."`（UTF-16）和 `-fshort-wchar`。

## 踩雷集錦

1. **字串忘記 `L` 前綴**：UEFI 用 UTF-16，ASCII 字串會被當亂碼。所有 UEFI 字串用 `L"..."`

2. **編譯漏 `-fshort-wchar`**：`wchar_t` 預設 32-bit，但 UEFI UTF-16 要 16-bit。漏這選項，寬字串大小錯

3. **進入點名稱錯**：gnu-efi 約定 `efi_main`，EDK II 用 `UefiMain`。和你的 crt0/linker script 約定一致，否則韌體找不到進入點

4. **`.efi` 放錯路徑**：後備路徑是 `/EFI/BOOT/BOOTX64.EFI`（x86-64）。放錯位置韌體找不到（除非用 UEFI shell 手動跑或設 boot 變數）

5. **ESP 不是 FAT**：UEFI 韌體讀 FAT。ESP 格式成其他檔案系統，韌體讀不到 `.efi`

6. **用 `\n` 而非 `\r\n`**：UEFI 控制台要 `\r\n` 才正確換行。只用 `\n` 可能游標不回到行首

## 進階：UEFI app 的 PE/COFF 格式

`.efi` 是 PE/COFF 格式——和 Windows `.exe`/`.dll` 同源：

```
PE/COFF（.efi）的結構：
  DOS header（歷史相容，"MZ" magic）
  PE header（"PE\0\0" magic）
    - machine type（x86-64）
    - subsystem（EFI_APPLICATION / EFI_BOOT_SERVICE_DRIVER...）
    - entry point（efi_main 的位址）
  sections（.text, .data, .reloc...）
        │
  UEFI 韌體解析 PE header，載入 sections，跳 entry point
```

```bash
# 看 .efi 的 PE 結構
objdump -h hello.efi          # 看 sections
# 或用 file 確認格式
file hello.efi
# hello.efi: PE32+ executable (EFI application) x86-64
```

為什麼 UEFI 用 PE 而非 ELF？歷史原因——UEFI 由 Intel 主導，受 Windows 影響（Windows 用 PE）。PE 的 subsystem 欄位能標記「這是 EFI application」，韌體據此處理。理解 `.efi` = PE 能解釋為什麼編譯流程要 `objcopy --target efi-app`（從 ELF 轉 PE）。

EDK II（UEFI 官方 SDK）和現代 clang 能直接產 PE，不用 gnu-efi 的「ELF 轉 PE」繞路。但 gnu-efi 較輕量，適合學習單一 app。

## 動手練習

1. 跑通本章的 hello.efi，在 OVMF 看到 "Hello from UEFI!"。改成印出韌體廠商（`SystemTable->FirmwareVendor`）和 UEFI 版本

2. 用 UEFI shell 方式執行：把 `.efi` 放 ESP 根目錄，進 shell `fs0:` 後手動跑。試 `memmap` 看記憶體地圖

3. 故意弄壞：把 `L"..."` 改成 `"..."`（去掉 L），重編跑，看印出亂碼。加回 L 修復

4. 用 `file hello.efi` 和 `objdump -h hello.efi` 確認它是 PE32+ EFI application，看它的 sections

## 本章重點整理

- UEFI app 是有 `efi_main` 進入點的 C 程式，編譯成 PE/COFF（`.efi`）；比 BIOS boot sector 簡單（C、64-bit、無模式切換）
- gnu-efi 提供 UEFI 的 headers、crt0、linker script；`InitializeLib` 初始化，`Print`/`ConOut->OutputString` 印字
- UEFI 字串是 UTF-16（`L"..."` + `-fshort-wchar`），控制台換行用 `\r\n`
- 編譯流程：C → ELF（特殊選項）→ PE/COFF（`objcopy --target efi-app`）
- `.efi` 放 FAT 格式 ESP 的後備路徑 `/EFI/BOOT/BOOTX64.EFI`，OVMF 自動執行；或進 UEFI shell 手動跑

## 自我檢核

- [ ] 能寫一個最簡單的 UEFI app（efi_main + ConOut 印字）並編譯成 .efi
- [ ] 知道 UEFI app 的進入點簽名和 BIOS boot sector 的根本差異
- [ ] 知道為什麼 UEFI 字串要 UTF-16（`L"..."` + `-fshort-wchar`）
- [ ] 知道 `.efi` 放 ESP 的哪個路徑能被韌體自動執行
- [ ] 知道 `.efi` 是 PE/COFF 格式，以及為什麼編譯要 ELF 轉 PE

## 延伸閱讀

### 官方文件

- **[gnu-efi README and apps/](https://sourceforge.net/projects/gnu-efi/)**
  - **讀哪裡**：`apps/` 目錄的範例（t.c, printenv.c...）
  - **學什麼**：gnu-efi 的各種範例，從 hello 到複雜的 protocol 使用
  - **前提**：本章

### 部落格 / 文章

- **[Programming for EFI: Creating a "Hello, World" Program](https://www.rodsbooks.com/efi-programming/hello.html)** — Rod Smith
  - **這篇說什麼**：用 gnu-efi 寫第一個 UEFI app 的完整教學，含編譯細節
  - **讀哪裡**：整篇
  - **為什麼值得讀**：把編譯流程的每個選項解釋清楚，本章的最佳補充

- **[UEFI programming - First Steps](https://x86asm.net/articles/uefi-programming-first-steps/)** — x86asm.net
  - **這篇說什麼**：從零寫 UEFI app，含 PE 格式、entry point 細節
  - **讀哪裡**：整篇
  - **為什麼值得讀**：對 PE/COFF 和進入點的底層解釋深入

→ [Ch 14 UEFI 的記憶體與 ExitBootServices](./14-uefi-memory.md)
