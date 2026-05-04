# Ch 34 — OpenSBI → Linux 啟動流程解析：從 M-mode 到 S-mode Kernel

> 目標：能說出 RISC-V 三段式啟動流程；理解 OpenSBI 的職責和 SBI call 機制；能用 QEMU 跑起一個最小的 RV64 Linux 環境到 shell prompt。

---

## 34.1 三段式啟動流程

```
上電
  |
  v
ZSBL（Zero Stage Bootloader）
  硬體廠商固定在 ROM 裡的 code
  M-mode，極小，通常只做：
    - 初始化最基本的 DRAM 控制器
    - 從 flash/SD card 載入下一階段
    - 跳到 OpenSBI 或其他 firmware
  QEMU 不需要 ZSBL，直接從 -bios 指定的 OpenSBI 開始
  |
  v
OpenSBI（Open Source Supervisor Binary Interface）
  M-mode，RISC-V 社群維護的開源 firmware
  職責：
    - 設定 medeleg/mideleg（把 trap 委派給 S-mode）
    - 設定 PMP（Physical Memory Protection）
    - 初始化 timer（CLINT）
    - 提供 SBI call 介面給 S-mode
    - 啟動各 hart（多核初始化）
    - 跳到 Linux kernel（mret 到 S-mode）
  |
  v
Linux kernel（S-mode）
  head.S → start_kernel()
```

---

## 34.2 SBI Call 機制

SBI（Supervisor Binary Interface）是 S-mode 和 M-mode 之間的介面規範，類似 S-mode 的「系統呼叫」。

呼叫方式：

```asm
# S-mode 執行 ecall → M-mode OpenSBI
li a7, SBI_EXT_ID        # extension ID
li a6, SBI_FID           # function ID
# a0–a5 是參數
ecall
# 返回後：a0 = error code, a1 = return value
```

主要 SBI extension：

| Extension 名稱 | Extension ID | 說明                       |
|-------------|------------|--------------------------|
| Base         | 0x10        | 基本查詢（get spec version 等）|
| Timer        | 0x54494D45  | 設定 timer（sbi_set_timer）  |
| IPI          | 0x735049    | 發送 IPI 給其他 hart         |
| RFENCE       | 0x52464E43  | 遠端 sfence.vma             |
| HSM          | 0x48534D    | Hart State Management（start/stop hart）|
| SRST         | 0x53525354  | System Reset（reboot/shutdown）|
| PMU          | 0x504D55    | Performance Monitor         |

Linux 的 SBI call wrapper（arch/riscv/include/asm/sbi.h）：

```c
// 設定下次 timer 中斷的時間
static inline int sbi_set_timer(uint64_t stime_value) {
    struct sbiret ret;
    ret = sbi_ecall(SBI_EXT_TIME, SBI_EXT_TIME_SET_TIMER,
                    stime_value, 0, 0, 0, 0, 0);
    return ret.error;
}
```

---

## 34.3 Device Tree（FDT）傳遞

OpenSBI 跳到 Linux kernel 時，按照 RISC-V calling convention 傳遞兩個參數：

```asm
# OpenSBI 跳到 kernel 前：
a0 = hartid      # 當前 hart 的 ID（0-based）
a1 = fdt_addr    # Flattened Device Tree 的物理地址

# 然後：
mret              # 降到 S-mode，跳到 kernel 入口
```

Linux kernel 的 `_start`（arch/riscv/kernel/head.S 的入口）收到這兩個值，把 FDT 地址保存起來，後續讀取硬體描述。

FDT（Flattened Device Tree / DTB）描述了系統的硬體拓撲：CPU 個數、頻率、記憶體範圍、UART 地址、PLIC/CLINT 地址等。

---

## 34.4 Linux 的 _start 前幾步（head.S 簡化）

```asm
_start:
    # 1. 只讓 hart 0 繼續，其他 hart 等待
    csrr a2, mhartid          # 讀 hart ID（注意：此時還在 S-mode，用 mhartid 是 legacy）
    # Linux 實際用 a0（OpenSBI 傳來的 hartid）
    bnez a0, .secondary_hart_loop

    # 2. 清零 BSS
    la   a3, __bss_start
    la   a4, __bss_stop
    # ...

    # 3. 設定早期頁表（setup_vm）
    call setup_vm             # 建立最小 identity mapping + kernel high-address mapping
    call relocate             # 切換 satp，跳到 kernel 虛擬地址空間

    # 4. 設定 kernel stack
    la   sp, init_thread_union + THREAD_SIZE

    # 5. 跳到 C code
    call start_kernel
```

`setup_vm` 在真正的 mm subsystem 初始化之前建立一個臨時頁表（fixmap），讓 kernel 可以在虛擬地址 0xffffffff80000000 附近跑。

---

## 34.5 QEMU 的參數說明

```bash
qemu-system-riscv64 \
  -M virt \              # 使用 virt 機器類型（通用虛擬板）
  -cpu rv64 \            # RV64 CPU
  -m 512M \              # 512 MiB RAM
  -bios opensbi-riscv64-generic-fw_dynamic.bin \  # OpenSBI firmware
  -kernel Image \        # Linux kernel（壓縮後的 Image 或 vmlinux）
  -append "root=/dev/vda console=ttyS0" \         # kernel cmdline
  -drive file=rootfs.img,format=raw,id=hd0 \      # root filesystem
  -device virtio-blk-device,drive=hd0 \
  -nographic \           # 不開 GUI，UART 輸出到 stdio
  -serial stdio
```

`-bios none` 的情況：自己控制整個啟動流程（baremetal 開發用），QEMU 直接從 -kernel 載入到物理地址 0x80000000 並跳過去，沒有 OpenSBI 的初始化。

---

## 34.6 最小 RV64 Linux 環境（Step by Step）

**方法 1：用 buildroot 一鍵建構**

```bash
# 下載 buildroot
wget https://buildroot.org/downloads/buildroot-2024.02.tar.gz
tar xf buildroot-2024.02.tar.gz
cd buildroot-2024.02

# 使用 RISC-V QEMU 預設設定
make qemu_riscv64_virt_defconfig
make -j$(nproc)   # 大約 15-30 分鐘

# 編譯完成後：
# output/images/fw_dynamic.bin   = OpenSBI
# output/images/Image             = Linux kernel
# output/images/rootfs.ext2       = root filesystem

# 執行（buildroot 附的腳本）
output/images/start-qemu.sh
```

**方法 2：手動用 busybox + initramfs**

```bash
# 1. 準備 kernel
git clone https://github.com/torvalds/linux.git --depth=1
cd linux
make ARCH=riscv CROSS_COMPILE=riscv64-unknown-linux-gnu- defconfig
make ARCH=riscv CROSS_COMPILE=riscv64-unknown-linux-gnu- -j$(nproc)
# 產出：arch/riscv/boot/Image

# 2. 準備 busybox
wget https://busybox.net/downloads/busybox-1.36.1.tar.bz2
# ... 編譯，產出靜態連結的 busybox

# 3. 製作 initramfs
mkdir -p initramfs/{bin,sbin,etc,proc,sys,dev}
cp busybox initramfs/bin/
# 建立 init script...
find initramfs | cpio -o -H newc | gzip > initramfs.cpio.gz

# 4. 執行
qemu-system-riscv64 -M virt \
  -bios fw_dynamic.bin \
  -kernel Image \
  -initrd initramfs.cpio.gz \
  -append "console=ttyS0 init=/init" \
  -nographic
```

---

## 34.7 啟動 log 解讀

正常啟動的 dmesg 開頭（節錄）：

```
OpenSBI v1.x
  ...
  MISA  : 0x800000000014112d
  ...
  Boot HART ISA Extensions  : time,sstc

[    0.000000] Linux version 6.x.x (riscv64-linux-gnu-gcc ...)
[    0.000000] OF: fdt: Ignoring memory range ...
[    0.000000] Machine: RISC-V VirtIO Board
[    0.000000] earlycon: sifive0 at MMIO ...
[    0.000000] riscv: ISA extensions acdfhimoqsu
[    0.000000] riscv: ELF capabilities acdfim
[    0.000000] SBI specification v1.0 detected
[    0.000000] SBI implementation ID=0x1 Version=0x10004
[    0.000000] Memory model: flat
[    0.001000] Built 1 zonelists ...
```

關鍵欄位：
- `MISA`：機器的 ISA 設定
- `SBI specification`：OpenSBI 版本
- `riscv: ISA extensions`：偵測到的 extension

---

## 自我檢核

- [ ] 能說出 RISC-V 三段式啟動的三個階段（ZSBL→OpenSBI→Linux）
- [ ] 知道 OpenSBI 傳給 Linux kernel 的兩個參數（hartid, fdt_addr）
- [ ] 能說出 SBI call 的呼叫方式（ecall，a7=ext ID，a6=func ID）
- [ ] 知道 `-bios none` 和 `-bios opensbi.bin` 的差別
- [ ] 能用 buildroot 或 busybox 建立一個可跑到 shell 的 RV64 QEMU 環境

→ [Ch 35 — Context Switch 實作](35-context-switch.md)
