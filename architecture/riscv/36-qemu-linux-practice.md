# Ch 36 — QEMU virt 跑 Linux：讀 /proc/cpuinfo、dmesg、/proc/interrupts

> 目標：能用 QEMU 跑起一個完整的 RV64 Linux 系統；能從 /proc 和 /sys 讀出有意義的 RISC-V 硬體資訊；掌握 RV64 Linux 上的基本除錯工具。

---

## 36.1 建立最小 RV64 Linux 環境

**最快的路線：buildroot**

```bash
# 1. 取得 buildroot
wget https://buildroot.org/downloads/buildroot-2024.02.tar.gz
tar xf buildroot-2024.02.tar.gz && cd buildroot-2024.02

# 2. 載入 RISC-V QEMU virt 預設設定
make qemu_riscv64_virt_defconfig

# 3. 可選：調整設定（加 gdb、perf 等工具）
make menuconfig
#   Target packages → Debugging, profiling and benchmark → gdb
#   Target packages → System tools → perf

# 4. 編譯（約 15–40 分鐘，取決於機器）
make -j$(nproc)

# 5. 執行
./output/images/start-qemu.sh
# 或手動：
qemu-system-riscv64 \
  -M virt \
  -m 512M \
  -bios output/images/fw_dynamic.bin \
  -kernel output/images/Image \
  -drive file=output/images/rootfs.ext2,format=raw,id=hd0 \
  -device virtio-blk-device,drive=hd0 \
  -append "root=/dev/vda rw console=ttyS0" \
  -nographic
```

登入後應看到 shell prompt（buildroot 預設 root 沒密碼）。

---

## 36.2 /proc/cpuinfo 解讀

```bash
cat /proc/cpuinfo
```

輸出範例：

```
processor       : 0
hart            : 0
isa             : rv64imafdch_zicntr_zicsr_zifencei_zihpm_zba_zbb_zbc_zbs
mmu             : sv48
uarch           : sifive,u74-mc    # 或 unknown（QEMU 不一定填）
```

各欄位說明：

| 欄位        | 含義                                         |
|-----------|---------------------------------------------|
| `processor`| Linux 的邏輯 CPU 編號（從 0 開始）             |
| `hart`     | RISC-V hardware thread ID（和 processor 不一定相同）|
| `isa`      | 硬體支援的 ISA extension 列表                 |
| `mmu`      | 使用的分頁模式（sv39/sv48/sv57）               |
| `uarch`    | 微架構識別字串（廠商自填，QEMU 可能是 unknown）   |

多核系統上每個 hart 一個段落：

```bash
grep -c processor /proc/cpuinfo   # 有幾個 CPU
grep isa /proc/cpuinfo | head -1  # ISA extensions
grep mmu /proc/cpuinfo | head -1  # 分頁模式
```

---

## 36.3 dmesg 的 RISC-V 相關訊息

```bash
dmesg | grep -i riscv
dmesg | grep -i sbi
dmesg | grep -i mmu
```

關鍵訊息解讀：

```
[    0.000000] riscv: ISA extensions acdfhimoqsu
# 這是 kernel 偵測到的 ISA extension（字母排序）

[    0.000000] riscv: ELF capabilities acdfim
# kernel binary 本身使用了哪些 extension

[    0.000000] SBI specification v1.0 detected
[    0.000000] SBI implementation ID=0x1 Version=0x10004
# OpenSBI 版本資訊

[    0.000000] Zone ranges:
[    0.000000]   DMA      [mem 0x0000000080000000-0x000000009fffffff]
[    0.000000]   Normal   empty
[    0.000000] Movable zone start for each node
[    0.000000] Early memory node ranges
[    0.000000]   node   0: [mem 0x0000000080000000-0x000000009fffffff]
# 物理記憶體佈局（QEMU virt 的 RAM 從 0x80000000 開始）
```

---

## 36.4 /proc/interrupts

```bash
cat /proc/interrupts
```

輸出範例：

```
           CPU0
  1:          0  SiFive PLIC  10  ttyS0
  2:        142  SiFive PLIC   8  virtio0
  7:       1234  RISC-V INTC   5  riscv-timer
IPI0:        56               Rescheduling interrupts
IPI1:        12               Function call interrupts
```

各欄位說明：
- 第一欄：Linux IRQ 號碼
- `CPU0`：這個 IRQ 在各 CPU 上的觸發次數
- `SiFive PLIC`：interrupt controller 名稱
- `10`：PLIC 的 interrupt source ID
- `ttyS0`：對應的設備

`riscv-timer` 是 S-mode timer interrupt，每個 tick 觸發一次（通常 250 Hz 或 1000 Hz），用於 scheduler time slice。

---

## 36.5 /proc/iomem

```bash
cat /proc/iomem
```

典型 QEMU virt 輸出：

```
08000000-08ffffff : PLIC
10000000-100000ff : 10000000.uart (ttyS0)
80000000-9fffffff : System RAM
  80000000-80dfffff : Kernel code
  80e00000-80ffffff : reserved
  81000000-81ffffff : Kernel data
  ...
```

這直接對應 QEMU virt 的 address map。`0x10000000` 是你在 baremetal code 裡直接寫 UART 的地址。

---

## 36.6 /sys/kernel/debug/riscv/

```bash
ls /sys/kernel/debug/riscv/   # 需要 debugfs mounted
# 或
mount -t debugfs none /sys/kernel/debug
```

可能的內容（kernel 版本不同有差異）：
- `mmu_pt`：當前程序的頁表 dump（若有）
- `perf_event_counters`：效能計數器資訊

---

## 36.7 讀 CSR Counter

在 Linux 使用者空間，直接讀 CSR 需要 U-mode 允許（mcounteren.CY=1 and scounteren.CY=1，Linux 預設已開）：

**方法 1：直接 inline asm**

```c
#include <stdint.h>
#include <stdio.h>

int main() {
    uint64_t cycle0, cycle1;
    __asm__ volatile ("csrr %0, cycle" : "=r"(cycle0));
    // do some work
    volatile long sum = 0;
    for (int i = 0; i < 1000000; i++) sum += i;
    __asm__ volatile ("csrr %0, cycle" : "=r"(cycle1));
    printf("elapsed cycles: %lu\n", cycle1 - cycle0);
    return 0;
}
```

**方法 2：用 perf stat**

```bash
perf stat ls
# 輸出：
# Performance counter stats for 'ls':
#       xxx,xxx      cycles
#       xxx,xxx      instructions
#       xxx         cache-misses
```

---

## 36.8 常用工具速查

```bash
# 查看 ELF 的 machine type（確認是 RV64）
file my_binary
# 輸出：my_binary: ELF 64-bit LSB executable, UCB RISC-V, RVC, double-float ABI

readelf -h my_binary | grep Machine
# 輸出：  Machine:                           RISC-V

# 反組譯
riscv64-linux-gnu-objdump -d my_binary | less
riscv64-linux-gnu-objdump -d -M no-aliases my_binary  # 不用偽指令

# 看符號表
readelf -s my_binary
nm my_binary

# 看 dynamic linking 資訊
readelf -d my_binary
ldd my_binary   # 在 target 系統上

# 看 .rodata/.data 的內容
readelf -x .rodata my_binary

# 追蹤系統呼叫
strace ./my_binary
strace -e trace=mmap,mprotect,brk ./my_binary  # 只看記憶體相關

# 確認 ABI
readelf -A my_binary
# 輸出：Attribute Section: riscv
#         RV64, double-float ABI
```

---

## 36.9 用 GDB 遠端除錯 QEMU

```bash
# QEMU 啟動時加 -S -gdb tcp::1234
qemu-system-riscv64 -M virt ... -S -gdb tcp::1234

# 本機 GDB（需要 riscv64-linux-gnu-gdb 或帶 RISC-V 支援的 gdb）
gdb ./my_binary
(gdb) set architecture riscv:rv64
(gdb) target remote :1234
(gdb) hbreak main
(gdb) continue
(gdb) info registers
(gdb) x/10i $pc          # 看 PC 附近的指令
(gdb) x/8gx $sp          # 看 stack（8個 64-bit 值）
```

---

## 自我檢核

- [ ] 能用 buildroot 或 busybox 跑起一個 RV64 Linux QEMU 環境
- [ ] 能從 `/proc/cpuinfo` 讀出 ISA extensions 和 MMU 模式
- [ ] 知道 `/proc/interrupts` 的格式（IRQ 號、CPU 計數、controller、設備名）
- [ ] 能用 `readelf -h` 確認一個 binary 是 RV64 ELF
- [ ] 能用 inline asm 讀 `cycle` CSR 並計算 elapsed cycles

→ [Ch 37 — arch/riscv 程式碼導覽](37-arch-riscv-code-tour.md)
