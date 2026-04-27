# Linux 開機流程學習筆記：從按下電源到登入畫面

> 給會一點 C 和組合語言、想徹底搞懂 Linux 怎麼從一塊冷的硬體跑起來的工程師。

這系列把 x86_64 Linux 的開機流程從頭到尾拆開：BIOS / UEFI 兩條路線並列，包含自製 boot sector、自製 UEFI app、自製 initramfs，全部在 QEMU 裡跑。讀完你會知道按下電源後的每一個 byte 跑去哪裡。

## 為什麼學這個？

- **debug 的盡頭**：看得懂 kernel panic / `dracut: emergency shell` / GRUB 噴錯，不會只能重灌
- **理解整個 stack**：systemd unit、initramfs、UEFI 變數，這些東西平常被當魔法，攤開後其實不複雜
- **embedded / SRE / kernel dev 的入場券**：要碰客製 image、PXE boot、recovery，不懂這套寸步難行
- **滿足好奇心**：「按下電源後到底發生什麼事」這題不搞清楚會癢一輩子

## 課程地圖

### Part 1 — 開機全景與 x86 模式基礎
- [Ch 0 環境搭建](./00-environment-setup.md)
- [Ch 1 從電源到登入畫面的全景](./01-boot-panorama.md)
- [Ch 2 x86 三種 CPU 模式](./02-x86-cpu-modes.md)
- [Ch 3 BIOS vs UEFI 路線總覽](./03-bios-vs-uefi.md)

### Part 2 — BIOS / Legacy 路線
- [Ch 4 BIOS POST、reset vector、INT 服務](./04-bios-post-and-int.md)
- [Ch 5 MBR 與 boot sector](./05-mbr-and-boot-sector.md)
- [Ch 6 動手：自製 hello boot sector](./06-hello-boot-sector.md)
- [Ch 7 切到 protected mode (GDT / A20 / CR0)](./07-protected-mode-switch.md)
- [Ch 8 切到 long mode (paging / PML4 / EFER)](./08-long-mode-switch.md)
- [Ch 9 GRUB 內部結構](./09-grub-internals.md)

### Part 3 — UEFI 路線
- [Ch 10 UEFI 架構：Boot Services / Runtime Services / Protocol](./10-uefi-architecture.md)
- [Ch 11 ESP、efibootmgr、NVRAM 變數](./11-esp-and-efibootmgr.md)
- [Ch 12 動手：用 gnu-efi 寫 minimal UEFI app](./12-minimal-uefi-app.md)
- [Ch 13 UEFI 下的 GRUB2 與 systemd-boot](./13-uefi-bootloaders.md)
- [練習 A：在 OVMF 上加自製 boot entry](./practice-a-uefi-boot-entry.md)

### Part 4 — Kernel 載入
- [Ch 14 bzImage / vmlinuz 結構與 Linux boot protocol](./14-bzimage-structure.md)
- [Ch 15 arch/x86/boot 從 setup 到 start_kernel](./15-arch-x86-boot.md)
- [Ch 16 kernel cmdline](./16-kernel-cmdline.md)

### Part 5 — initramfs 與 rootfs pivot
- [Ch 17 initramfs 是什麼](./17-initramfs-overview.md)
- [Ch 18 動手：自製最小 initramfs](./18-build-minimal-initramfs.md)
- [Ch 19 switch_root / pivot_root](./19-pivot-root.md)
- [練習 B：手動 pivot 到 real rootfs](./practice-b-manual-pivot.md)

### Part 6 — userspace 啟動
- [Ch 20 PID 1 簡史](./20-pid-1-history.md)
- [Ch 21 systemd unit / target / dependency](./21-systemd-units.md)
- [Ch 22 systemd 早期 boot target chain](./22-systemd-boot-targets.md)
- [Ch 23 開機排錯](./23-boot-troubleshooting.md)

### Part 7 — 進階整合
- [Ch 24 Secure Boot、TPM、measured boot](./24-secure-boot-and-tpm.md)
- [Final Project：從零組最小 Linux](./final-project-minimal-linux.md)

## 學習方式建議

1. **每章都要在 QEMU 裡跑一次**：boot 這個主題不動手等於沒讀，文字描述開機流程像看人游泳教學
2. **故意弄壞**：把 boot sector 最後兩個 byte 刪掉、把 GDT 設爛、把 initramfs 裡的 `/init` 改成 `exit 1`，看系統怎麼罵你
3. **`gdb` + `qemu -s -S`**：QEMU 可以從 `0xFFFFFFF0` 第一條指令開始 step，幾乎沒有東西比這更適合學 boot
4. **永遠對照真機**：每章學的概念，都拿你自己 Linux 機器的 `/boot`、`efibootmgr -v`、`systemctl list-units` 對照看一次

## 參考資料

- [OSDev Wiki](https://wiki.osdev.org/) — boot / UEFI / x86 模式切換的事實標準參考
- 《Linux Kernel Development》Robert Love — Ch 2 提到 boot 流程概覽
- Linux source tree `Documentation/x86/boot.rst` — boot protocol 權威來源
- UEFI Specification ([uefi.org](https://uefi.org/specifications)) — 厚但有用，當字典查
- `arch/x86/boot/` 整個目錄 — 看真實的 boot code 比看任何書都有效
