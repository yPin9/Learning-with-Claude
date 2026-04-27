# Ch 0 — 環境搭建

> 目標：把 QEMU、組譯器、UEFI 韌體、debug 工具備齊，後面每一章都靠這套。

## 為什麼用 QEMU

學 boot 一定要在虛擬機裡學。原因：

- **真機不能 step**：你沒辦法在自己 ThinkPad 的 reset vector 那一條指令下中斷點，但 QEMU 可以
- **真機壞了會痛**：自己寫的 boot sector 能直接讓 NVMe 變磚，QEMU 壞了 `rm` 一下重來
- **firmware 可換**：BIOS / UEFI 在 QEMU 裡只是一個檔案（SeaBIOS / OVMF），想換就換

QEMU 是 Linux boot 教學最好的沙盒，沒有之一。

## 工具清單

我們會用到這些（Ubuntu / Debian 為例，Arch 把 apt 換 pacman 都裝得到）：

```bash
sudo apt install \
  qemu-system-x86 \
  ovmf \
  nasm \
  gcc \
  gdb \
  xxd \
  cpio \
  busybox-static \
  gnu-efi
```

每個工具的用途：

| 工具 | 用途 |
|---|---|
| `qemu-system-x86_64` | 跑 x86_64 虛擬機，模擬 BIOS / UEFI 兩種開機 |
| `ovmf` | 開源 UEFI 韌體 (Open Virtual Machine Firmware)，QEMU 跑 UEFI 用這個 |
| `nasm` | x86 組譯器，寫 boot sector / mode switch 用 |
| `gcc` | C 編譯器，寫 UEFI app / mini kernel 用 |
| `gdb` | 配合 `qemu -s -S` 在 16/32/64-bit 模式 step debug |
| `xxd` | 看 binary，boot sector 出問題第一個工具 |
| `cpio` | 打包 initramfs |
| `busybox-static` | 一個 binary 解決所有 userspace 工具，initramfs 必備 |
| `gnu-efi` | 不用 EDK2 也能寫 UEFI app 的輕量 lib |

## 快速驗證

裝完跑這幾條，沒錯就 OK：

```bash
qemu-system-x86_64 --version
nasm --version
gdb --version

# OVMF 路徑，後面 UEFI 章節會直接用
ls /usr/share/OVMF/OVMF_CODE.fd /usr/share/OVMF/OVMF_VARS.fd

# busybox 必須是 static
file /bin/busybox     # 應該寫 statically linked
```

OVMF 路徑各 distro 不同，記下你機器上的：

- Debian / Ubuntu: `/usr/share/OVMF/OVMF_CODE.fd`
- Arch: `/usr/share/edk2-ovmf/x64/OVMF_CODE.fd`
- Fedora: `/usr/share/edk2/ovmf/OVMF_CODE.fd`

## 第一個 sanity check：跑一台空的 QEMU

```bash
qemu-system-x86_64 -m 256 -nographic
```

`-nographic` 把畫面導到 terminal，省得開 GUI 視窗。你會看到 SeaBIOS（QEMU 預設的 BIOS）跑起來，找不到開機裝置最後 hang 在 iPXE 或 `Booting from Hard Disk` 的訊息。

按 `Ctrl-A` 然後 `X` 離開 QEMU。**這個快捷鍵後面每一章都會用到，先記起來**。

換 UEFI 試試：

```bash
qemu-system-x86_64 -m 256 -nographic \
  -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE.fd
```

這次跑的是 UEFI 韌體，最後會掉到 UEFI shell（如果 OVMF 含 shell）或 `BdsDxe: failed to load Boot0001`。

兩種都能跑就過關。

## QEMU + GDB：本系列最重要的組合

開另一個 terminal，QEMU 加兩個 flag：

```bash
qemu-system-x86_64 -s -S -m 256 -nographic
```

- `-s`：開 GDB server 在 `localhost:1234`
- `-S`：開機後**先暫停**，等 GDB 連上才開始跑

然後在另一個 terminal：

```bash
gdb
(gdb) target remote :1234
(gdb) set architecture i8086
(gdb) x/10i $cs*16 + $pc
(gdb) si    # step instruction
```

第一條指令會出現在 `0xFFFF0`（reset vector，後面 Ch 4 會講為什麼是這個位址）。

你能 step 到這條指令，整個系列的 debug 環境就準備好了。**這個能力比任何文字教材都值錢**。

## 一個常見踩雷：`-nographic` 與 serial console

`-nographic` 等同於：

```
-display none -serial stdio
```

也就是「不開視窗，並把 serial port 0 接到你的 terminal」。後面 Ch 6 自製 boot sector 我們會用 BIOS INT 10h 寫螢幕，看不到輸出時想想：是寫到 VGA 還是 serial？兩個是不同的東西。

要 debug 自己的 boot code，建議改用：

```
-display none -serial mon:stdio
```

`mon:stdio` 把 QEMU monitor 也接過來，按 `Ctrl-A C` 可以切到 monitor 看暫存器、記憶體。**這是 boot 寫崩時最後的救命稻草**。

## 自我檢核

- [ ] QEMU 能跑起來，按 `Ctrl-A X` 能離開
- [ ] OVMF 路徑找得到，能用 UEFI 跑 QEMU
- [ ] 用 `-s -S` 配 GDB 連得上、能 step 第一條指令
- [ ] 知道 `mon:stdio` 跟 `stdio` 差在哪

工具齊了，接下來看一張地圖：按下電源到登入畫面，整個流程到底有幾段。

→ [Ch 1 從電源到登入畫面的全景](./01-boot-panorama.md)
