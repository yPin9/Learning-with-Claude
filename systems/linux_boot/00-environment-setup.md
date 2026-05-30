# Ch 0 — 環境搭建

> **目標**：建立完整的開機實驗環境——QEMU（虛擬機）、nasm/gcc（組譯與編譯）、gnu-efi + OVMF（UEFI 開發）、以及最重要的 QEMU + gdb remote debug（能單步追蹤從 reset vector 開始的每一條指令），並驗證能跑起第一個 boot sector。

> **環境**：Ubuntu 22.04 / Debian 12，QEMU 7.x+，nasm 2.15+，gcc 12，gnu-efi 3.0.15，OVMF（edk2）。在 macOS/Windows 可用，但本課指令以 Linux host 為準。

## 為什麼開機開發需要這套環境？

你不可能拿真機器來反覆實驗開機——每改一次 boot sector 就重開機、燒 USB，太慢，而且改壞了開不了機。開機開發的核心工具是**虛擬機 + remote debugger**：

- **QEMU**：模擬一台完整的 x86 電腦，能從你的 disk image 開機。改一行 code、重編、`qemu` 一下就看到結果，秒級迭代
- **gdb remote**：QEMU 能在「CPU 執行第一條指令之前」暫停，讓 gdb 連進去單步執行。你能看著 CPU 從 `0xFFFFFFF0`（reset vector）一條一條跑——這是理解開機最強的工具，真機做不到

開機是少數「能完全在虛擬環境裡精確觀察」的系統主題。善用 QEMU + gdb，你能看到每一個暫存器、每一次模式切換。

## 先建立直覺：開機實驗的工具鏈

```
你寫的 boot code（assembly / C）
        │
   nasm / gcc 組譯編譯
        ▼
   disk image（.img / .iso / FAT 分區）
        │
   ┌────┴─────────────────────────────┐
   │  QEMU（模擬整台 x86 電腦）         │
   │   - BIOS 模式：用 SeaBIOS         │
   │   - UEFI 模式：用 OVMF 韌體        │
   │                                  │
   │   -s -S：開 gdb stub + 開機即暫停  │
   └────┬─────────────────────────────┘
        │  gdb remote 連進去
        ▼
   gdb：單步追蹤每條指令、看暫存器、看記憶體
```

兩種開機模式對應本課兩條線：BIOS 線用 QEMU 內建的 SeaBIOS，UEFI 線用 OVMF（開源 UEFI 韌體）。

## Step 1：安裝核心工具

```bash
sudo apt update
sudo apt install -y \
    qemu-system-x86 \
    nasm \
    gcc \
    make \
    gdb \
    xxd \
    ovmf \
    gnu-efi \
    mtools \
    dosfstools \
    parted

# 確認版本
qemu-system-x86_64 --version    # QEMU 7.x+
nasm --version                  # 2.15+
gcc --version                   # 12.x
gdb --version
ls /usr/share/OVMF/             # OVMF_CODE.fd, OVMF_VARS.fd（UEFI 韌體）
ls /usr/include/efi/            # gnu-efi 的 headers
```

各工具角色：

| 工具 | 角色 |
|---|---|
| `qemu-system-x86` | x86 系統模擬器，本課的實驗平台 |
| `nasm` | x86 組譯器，寫 boot sector（16-bit assembly）|
| `gcc` | 編譯 C（UEFI app、kernel-side code）|
| `gdb` | remote debug QEMU |
| `ovmf` | 開源 UEFI 韌體（UEFI 線用）|
| `gnu-efi` | 寫 UEFI application 的 C library/headers |
| `mtools` / `dosfstools` | 操作 FAT 檔案系統（UEFI 的 ESP）|
| `xxd` | hex dump，檢視 binary（如 boot sector 的 0x55AA）|

## Step 2：第一個 boot sector（驗證 BIOS 線）

不解釋細節（Ch 6 詳講），先確認環境能跑。建立 `boot.asm`：

```asm
; boot.asm — 最小的 boot sector，印一個字元
bits 16              ; 16-bit real mode（BIOS 開機時的 CPU 模式）
org 0x7c00           ; BIOS 把 boot sector 載入到記憶體位址 0x7c00

start:
    mov ah, 0x0e     ; BIOS teletype 功能（int 10h, AH=0Eh：印字元）
    mov al, 'B'      ; 要印的字元
    int 0x10         ; 呼叫 BIOS video service
    jmp $            ; 無限迴圈（停在這，不繼續執行垃圾）

; 填充到 510 bytes，最後 2 bytes 是 boot signature
times 510-($-$$) db 0   ; 用 0 填滿到第 510 byte
dw 0xaa55               ; boot signature（小端序：0x55, 0xAA）
                        ; BIOS 檢查這 2 bytes 確認這是可開機的 sector
```

組譯並用 QEMU 開機：

```bash
# 組譯成 raw binary（正好 512 bytes）
nasm -f bin boot.asm -o boot.img

# 確認大小是 512 bytes，且結尾是 55 aa
ls -l boot.img          # 512 bytes
xxd boot.img | tail -1   # 最後應該是 ...55 aa

# QEMU 從這個 image 開機
qemu-system-x86_64 -drive format=raw,file=boot.img
# 應該看到 QEMU 視窗左上角印出 'B'
```

看到 `B`，BIOS 線環境正確。

> 為什麼 `org 0x7c00`、為什麼 `0xaa55`、為什麼 510+2=512——這些 magic number 在 Ch 5/6 會完整解釋。現在只是驗證環境。

## Step 3：QEMU + gdb remote debug（最重要的技能）

這是本課最強的工具。讓 QEMU 在執行第一條指令前暫停，用 gdb 單步：

```bash
# -s：在 port 1234 開 gdb stub
# -S：開機後立刻暫停（freeze CPU at startup），等 gdb 連入
qemu-system-x86_64 -drive format=raw,file=boot.img -s -S &

# 另開一個 terminal，用 gdb 連進去
gdb
```

在 gdb 裡：

```gdb
(gdb) target remote localhost:1234
(gdb) set architecture i8086        # 開機時是 16-bit real mode
# 看 CPU 即將執行的位址（reset vector 之後，BIOS 跑完，準備跳 0x7c00）
(gdb) break *0x7c00                 # 在 boot sector 載入位址下中斷點
(gdb) continue
# 命中後，單步執行
(gdb) si                            # step instruction（單步一條指令）
(gdb) info registers                # 看所有暫存器
(gdb) x/8i $pc                      # 看 PC 處的 8 條指令
```

> `-s -S` 是開機 debug 的咒語。`-s` 開 gdb stub（等於 `-gdb tcp::1234`），`-S` 讓 CPU 開機就凍結等 gdb。這讓你能從「BIOS 把控制權交給你的 code 的那一刻」開始單步——真機絕對做不到這件事。

## Step 4：UEFI 環境（驗證 UEFI 線）

UEFI 線需要 OVMF 韌體和一個 FAT 格式的 ESP（EFI System Partition）。先建一個最小的 UEFI 開機磁碟結構（不寫 app，只驗證 OVMF 能起來）：

```bash
# 建立一個 FAT image 當 ESP
dd if=/dev/zero of=esp.img bs=1M count=64
mkfs.vfat esp.img

# 用 OVMF 韌體開機（還沒有 .efi，會進 UEFI shell 或報找不到 boot）
# 複製 OVMF VARS（可寫的變數儲存）
cp /usr/share/OVMF/OVMF_VARS.fd .

qemu-system-x86_64 \
    -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE.fd \
    -drive if=pflash,format=raw,file=OVMF_VARS.fd \
    -drive format=raw,file=esp.img \
    -net none
# 應該看到 TianoCore 的開機畫面（OVMF 的 UEFI 韌體 logo），
# 然後因為沒有可開機的 .efi 而停在 UEFI shell 或 boot manager
```

看到 TianoCore logo，UEFI 線環境正確。Ch 13 會寫真正的 UEFI app 放進這個 ESP。

> `if=pflash` 把 OVMF 當作韌體 flash（兩個檔案：CODE 唯讀的韌體本體、VARS 可寫的 NVRAM 變數）。這模擬真實 UEFI 主機板的 SPI flash 佈局。

## Step 5：gnu-efi 編譯測試

確認能編譯 UEFI application（細節 Ch 13）：

```bash
# 確認 gnu-efi 的關鍵檔案存在
ls /usr/lib/crt0-efi-x86_64.o      # UEFI 的 startup object
ls /usr/lib/elf_x86_64_efi.lds     # linker script
ls /usr/include/efi/efi.h          # 主 header
# 有這些就能編 UEFI app（Ch 13 會用）
```

## 一個方便的實驗 Makefile 範本

把這個存起來，之後實驗都改它：

```makefile
# Makefile — boot sector 實驗
ASM    := nasm
QEMU   := qemu-system-x86_64

boot.img: boot.asm
	$(ASM) -f bin $< -o $@

# 直接跑
run: boot.img
	$(QEMU) -drive format=raw,file=boot.img

# 開 gdb debug 模式（搭配另一個 terminal 的 gdb）
debug: boot.img
	$(QEMU) -drive format=raw,file=boot.img -s -S

clean:
	rm -f boot.img

.PHONY: run debug clean
```

```bash
make run      # 直接跑
make debug    # debug 模式（再開 gdb 連 localhost:1234）
```

## 踩雷集錦

1. **boot.img 不是正好 512 bytes**：`times 510-($-$$) db 0` 填充寫錯，或多了東西，導致 signature 不在第 511-512 byte。BIOS 找不到 `0x55AA` 就不開機。`ls -l` 確認 512 bytes

2. **gdb 連不上**：QEMU 要加 `-s`（開 stub）；gdb 端 `target remote localhost:1234`。如果 QEMU 沒加 `-S`，CPU 已經跑過你想看的地方了。debug 開機一定要 `-s -S` 一起

3. **gdb 架構不對顯示亂碼**：開機是 16-bit real mode，gdb 預設可能用 64-bit 解讀指令，顯示錯誤。`set architecture i8086`（real mode）或之後切到 `i386:x86-64`（long mode）

4. **OVMF 路徑因發行版而異**：Ubuntu 在 `/usr/share/OVMF/`，其他發行版可能在 `/usr/share/ovmf/` 或 `/usr/share/edk2/`。`dpkg -L ovmf` 找實際路徑

5. **OVMF_VARS 唯讀導致變數寫不了**：直接用 `/usr/share/OVMF/OVMF_VARS.fd`（系統的）會因唯讀失敗。複製一份到工作目錄用（如 Step 4）

## 動手練習

1. 跑通 Step 2 的 boot sector，看到 `B`。改成印你名字的第一個字母

2. 練 gdb debug：用 `make debug` + gdb，在 `0x7c00` 下中斷點，單步執行你的 boot sector，每步看 `info registers`，觀察 `mov ah, 0x0e` 執行後 `ax` 怎麼變

3. 故意弄壞：把 `dw 0xaa55` 改成 `dw 0x1234`（錯誤 signature），重編開機，看 QEMU 報「No bootable device」之類的錯誤。改回來

4. 跑通 Step 4 的 OVMF，看到 TianoCore logo。進 UEFI shell（如果有）打 `help` 看有什麼指令

## 本章重點整理

- 開機開發的核心：QEMU（模擬整台 x86）+ gdb remote（單步追蹤每條指令，真機做不到）
- BIOS 線用 QEMU 內建 SeaBIOS；UEFI 線用 OVMF（開源 UEFI 韌體，`if=pflash` 載入）
- `qemu -s -S` 是 debug 開機的咒語：開 gdb stub + 開機即凍結等 gdb
- boot sector 必須正好 512 bytes，結尾 `0x55AA`（小端序 `55 aa`），否則 BIOS 不開機
- gdb 要 `set architecture` 對應當前 CPU 模式（real mode i8086 / long mode i386:x86-64）

## 自我檢核

- [ ] 能用 QEMU 從一個 raw disk image 開機
- [ ] 能用 `qemu -s -S` + gdb 在 `0x7c00` 下中斷點並單步執行
- [ ] 知道 BIOS 模式（SeaBIOS）和 UEFI 模式（OVMF）在 QEMU 怎麼指定
- [ ] 知道 boot sector 為什麼必須是 512 bytes 結尾 0x55AA（先記住，Ch 5 解釋為什麼）

## 延伸閱讀

### 官方文件

- **[QEMU: GDB usage](https://www.qemu.org/docs/master/system/gdb.html)**
  - **讀哪裡**：整頁，特別是 `-s -S` 和 architecture 設定
  - **學什麼**：QEMU + gdb 的完整 debug 工作流；本課最重要的工具
  - **前提**：基本 gdb 使用

- **[OSDev Wiki: QEMU](https://wiki.osdev.org/QEMU)** 和 **[GDB](https://wiki.osdev.org/GDB)**
  - **讀哪裡**：QEMU 的 boot 相關選項、GDB 的 real mode debug 技巧
  - **學什麼**：自製 OS 社群累積的 QEMU/gdb 開機 debug 經驗
  - **前提**：無

### 部落格 / 文章

- **[Debugging the bootloader with QEMU and GDB](https://wiki.osdev.org/Real_Mode#Debugging)** — OSDev
  - **這篇說什麼**：real mode 下用 gdb debug 的細節（架構設定、位址計算）
  - **讀哪裡**：debugging 那節
  - **為什麼值得讀**：real mode 的 gdb debug 有坑（segment:offset 位址），這裡講清楚

→ [Ch 1 從電源到 shell：開機全景圖](./01-boot-overview.md)
