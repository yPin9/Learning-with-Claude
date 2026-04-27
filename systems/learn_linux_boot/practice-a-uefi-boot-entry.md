# 練習 A — 在 OVMF 上加自製 boot entry

> 目標：把 Ch 10–13 整合：寫 UEFI app、放進 ESP、用 efibootmgr/bcfg 註冊、改 BootOrder、reboot 觀察。

## 任務規格

完成下列子任務：

| # | 任務 | 驗收標準 |
|---|---|---|
| 1 | 寫一個有 menu 的 UEFI app | 能用方向鍵選 `Linux` / `Reboot` / `Exit` |
| 2 | 建一個持久的 OVMF 環境 | NVRAM 變數能跨 QEMU 重啟保留 |
| 3 | 把 app 註冊成 NVRAM boot entry | OVMF 開機選單看得到你的 entry |
| 4 | 改 BootOrder 讓你的 entry 預設 | 不選 fallback 也能進你的 app |
| 5 | 觀察 efivars | 看到剛剛建立的 BootXXXX 變數 |

## 環境準備

```bash
mkdir -p uefi-practice && cd uefi-practice

# 複製 OVMF firmware code（read only） + 一份獨立的 vars
cp /usr/share/OVMF/OVMF_VARS.fd ./OVMF_VARS.fd

# 準備 ESP
mkdir -p esp/EFI/BOOT
mkdir -p esp/EFI/mine
```

## 期望輸出範例

QEMU 啟動後（按 ESC 或 F2 進 menu）：

```
EFI Boot Manager
────────────────
* MyMenu                <-- 你的 entry
  UEFI QEMU HARDDISK
  EFI Internal Shell
```

選 `MyMenu`：

```
=== My UEFI Bootloader ===

  > Boot Linux
    Reboot
    Exit

Use ↑/↓ to choose, Enter to select.
```

## 實作步驟建議

### Step 1：擴充 hello UEFI app 為 menu

從 Ch 12 的 `hello.c` 開始，加：

- 用 `EFI_INPUT_KEY` 讀方向鍵
- 維護一個 `selected` 變數
- 每次按鍵 redraw

### Step 2：build 出 `mymenu.efi`

複用 Ch 12 的 Makefile，把 `hello` 換成 `mymenu`。

### Step 3：放進 ESP

```bash
cp mymenu.efi esp/EFI/mine/MyMenu.efi
# 同時複製到 fallback 位置
cp mymenu.efi esp/EFI/BOOT/BOOTX64.EFI
```

### Step 4：跑 QEMU 確認 fallback 能看到

```bash
qemu-system-x86_64 -m 256 \
  -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE.fd \
  -drive if=pflash,format=raw,file=./OVMF_VARS.fd \
  -drive format=raw,file=fat:rw:esp \
  -nographic
```

開機時 `EFI/BOOT/BOOTX64.EFI` 會自動跑（fallback path）。看到你的 menu 就過第一關。

### Step 5：用 UEFI shell 註冊正式 entry

進 UEFI shell（OVMF 預設有），執行：

```
Shell> map -r        # 看磁碟編號
Shell> fs0:
FS0:\> bcfg boot add 5 fs0:\EFI\mine\MyMenu.efi "MyMenu"
FS0:\> bcfg boot dump
FS0:\> bcfg boot mv 5 0     # 把它移到第 0 位（最高優先）
FS0:\> exit
```

這時候 OVMF firmware UI 應該能看到 `MyMenu` entry。

### Step 6：reboot，confirm 從你的 entry 開機

```
Shell> reset
```

QEMU 會 reset 但不會結束。回到 firmware menu，預設應該選 MyMenu，跑你寫的 app。

### Step 7：把 app 退出後從 Linux 看 efivars

退出 menu 進入 UEFI shell，再 `exit` 回 BDS。

或者：用 Linux 直接 mount `OVMF_VARS.fd`：

```bash
# 不容易，OVMF_VARS.fd 是特殊格式
# 比較簡單的方法：用 efibootmgr 在 QEMU 跑的 Linux 裡看
```

如果你能在 QEMU 裡跑 Linux（後面 Final Project 會做），你會看到剛剛 bcfg 設的 `Boot0005` 出現在 NVRAM。

## 完整參考解答

**寫完再看！不要偷看**，否則學不到東西。

<details>
<summary>點開參考實作</summary>

`mymenu.c`：

```c
#include <efi.h>
#include <efilib.h>

#define NUM_ITEMS 3

static const CHAR16 *items[NUM_ITEMS] = {
    L"Boot Linux",
    L"Reboot",
    L"Exit"
};

static void draw_menu(EFI_SYSTEM_TABLE *st, UINTN selected)
{
    st->ConOut->ClearScreen(st->ConOut);
    Print(L"=== My UEFI Bootloader ===\n\n");

    for (UINTN i = 0; i < NUM_ITEMS; i++) {
        if (i == selected) {
            Print(L"  > %s\n", items[i]);
        } else {
            Print(L"    %s\n", items[i]);
        }
    }

    Print(L"\nUse Up/Down to choose, Enter to select.\n");
}

static EFI_STATUS wait_key(EFI_SYSTEM_TABLE *st, EFI_INPUT_KEY *key)
{
    UINTN index;
    st->BootServices->WaitForEvent(1, &st->ConIn->WaitForKey, &index);
    return st->ConIn->ReadKeyStroke(st->ConIn, key);
}

EFI_STATUS EFIAPI efi_main(EFI_HANDLE ImageHandle, EFI_SYSTEM_TABLE *SystemTable)
{
    InitializeLib(ImageHandle, SystemTable);

    UINTN selected = 0;
    EFI_INPUT_KEY key;

    SystemTable->ConIn->Reset(SystemTable->ConIn, FALSE);
    draw_menu(SystemTable, selected);

    while (1) {
        wait_key(SystemTable, &key);

        if (key.ScanCode == 0x01) {  // Up
            if (selected > 0) selected--;
            draw_menu(SystemTable, selected);
        }
        else if (key.ScanCode == 0x02) {  // Down
            if (selected < NUM_ITEMS - 1) selected++;
            draw_menu(SystemTable, selected);
        }
        else if (key.UnicodeChar == L'\r') {
            switch (selected) {
                case 0:
                    Print(L"\n[would chainload Linux here]\n");
                    return EFI_SUCCESS;
                case 1:
                    SystemTable->RuntimeServices->ResetSystem(
                        EfiResetCold, EFI_SUCCESS, 0, NULL);
                    break;
                case 2:
                    return EFI_SUCCESS;
            }
        }
    }
}
```

`Makefile`（同 Ch 12，把 hello 改 mymenu）：

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

mymenu.efi: mymenu.so
	objcopy -j .text -j .sdata -j .data -j .dynamic \
	        -j .dynsym -j .rel -j .rela -j .reloc \
	        --target=efi-app-$(ARCH) $< $@

mymenu.so: mymenu.o
	ld $(LDFLAGS) $< -o $@ $(LIBS)

mymenu.o: mymenu.c
	gcc $(CFLAGS) -c $< -o $@

clean:
	rm -f *.o *.so *.efi
```

build + 跑：

```bash
make
mkdir -p esp/EFI/BOOT
cp mymenu.efi esp/EFI/BOOT/BOOTX64.EFI
qemu-system-x86_64 -m 256 \
  -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE.fd \
  -drive if=pflash,format=raw,file=./OVMF_VARS.fd \
  -drive format=raw,file=fat:rw:esp
```

</details>

## 常見錯誤

| 症狀 | 原因 |
|---|---|
| QEMU 跑了但畫面一片黑 | 用了 `-nographic` 但 ConOut 走 graphics — 拿掉 `-nographic` |
| 按方向鍵沒反應 | 沒檢查 ScanCode，只看 UnicodeChar |
| 進 menu 後立刻 return | 漏了 `wait_key`，`ReadKeyStroke` 在沒按鍵時會立刻回 NOT_READY |
| `bcfg` 找不到指令 | OVMF 版本沒含 shell — 換新版 ovmf 或加 `EnhancedFatPkg` |
| reboot 後 entry 消失 | OVMF_VARS.fd 沒寫權限 — 確認 `-drive ... file=...` 沒 `readonly=on` |

## 測試用例

| 動作 | 預期 |
|---|---|
| 按上鍵當 selected = 0 | 不變動 |
| 按下鍵當 selected = 2 | 不變動 |
| 按 Enter on Reboot | QEMU 重新開機 |
| 按 Enter on Exit | 回 firmware UI / UEFI shell |
| 沒按任何鍵等 30 秒 | 持續顯示，不會 timeout |

## 進階挑戰（選做）

- 加倒數計時：5 秒後自動選 default
- 把 menu 寫成從 `loader.conf` 讀進來的（學 systemd-boot）
- 真的把 `Boot Linux` 那項實作 chainload 一個 Linux kernel image（要參考 Ch 14）

## 自我檢核

- [ ] 自己的 UEFI app 在 OVMF 跑起來
- [ ] 知道 `Up/Down` 用 ScanCode 不是 UnicodeChar
- [ ] 用 `bcfg boot add` 註冊過 entry
- [ ] 改過 BootOrder
- [ ] 知道 `OVMF_VARS.fd` 是 NVRAM 持久化的檔案

下個 Part 進入 kernel 載入 — 看 GRUB / systemd-boot 把 `vmlinuz` 載進記憶體之後到底發生什麼。

→ [Ch 14 bzImage / vmlinuz 結構與 Linux boot protocol](./14-bzimage-structure.md)
