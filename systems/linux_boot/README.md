# Linux 開機流程學習筆記：從按下電源到 shell prompt

> 給懂一點 C、想徹底搞懂「電腦開機時到底發生什麼」的工程師。

這系列把開機這個黑盒子完全拆開：x86 BIOS 與 UEFI 兩條路徑都走一遍、親手寫 16-bit boot sector、親手寫 UEFI application、親手打包 initramfs，全程在 QEMU 裡動手、用 gdb remote debug。讀完你能解釋從 reset vector 到 `/sbin/init` 的每一步，能 debug 任何開機卡死，能自己組一個可開機的最小系統。

## 為什麼學這個？

- **開機是系統知識的試金石**：開機流程串起 CPU 模式切換、記憶體佈局、韌體、分區、檔案系統、kernel 初始化、process 模型——搞懂它，你對整個系統的理解會跳一級
- **debug 能力**：伺服器開不了機、kernel panic 在 initramfs、Secure Boot 擋住自製 kernel——這些問題只有懂開機流程的人能救
- **職涯角度**：嵌入式、韌體、kernel、雲端基礎設施、安全（Secure Boot/TPM）——開機知識是這些領域的硬底子，面試常考

## 先修知識

- **C 語言**（程度：會指標、struct、能讀懂 function；不需要寫過 kernel）
- **一點點 assembly 概念**（程度：知道暫存器、指令是什麼；x86 細節課程會教）
- **Linux 基礎**（程度：會用 shell、知道 process 和檔案系統是什麼）
- 不需要：組合語言實戰經驗、kernel 開發經驗、韌體經驗（課程從零補）

## 課程地圖

### Part 1 — 開機全貌與環境（Ch 0–3）
- [Ch 0 環境搭建](./00-environment-setup.md)
- [Ch 1 從電源到 shell：開機全景圖](./01-boot-overview.md)
- [Ch 2 x86 啟動時的 CPU 狀態](./02-cpu-startup-state.md)
- [Ch 3 開機時的記憶體佈局](./03-early-memory-layout.md)

### Part 2 — BIOS 線：傳統開機（Ch 4–9）
- [Ch 4 BIOS 韌體做什麼](./04-bios-firmware.md)
- [Ch 5 MBR 與 boot sector](./05-mbr-boot-sector.md)
- [Ch 6 寫一個 boot sector](./06-write-boot-sector.md)
- [Ch 7 從 real mode 到 protected mode](./07-real-to-protected-mode.md)
- [Ch 8 進入 long mode（64-bit）](./08-long-mode.md)
- [Ch 9 兩階段 bootloader 與磁碟讀取](./09-two-stage-bootloader.md)
- [練習 A：64-bit 兩階段 bootloader](./practice-a-64bit-bootloader.md)

### Part 3 — UEFI 線：現代開機（Ch 10–16）
- [Ch 10 UEFI 是什麼、為什麼取代 BIOS](./10-uefi-overview.md)
- [Ch 11 GPT 分區表](./11-gpt-partition.md)
- [Ch 12 UEFI Boot Services 與 Runtime Services](./12-uefi-services.md)
- [Ch 13 寫一個 UEFI application](./13-write-uefi-app.md)
- [Ch 14 UEFI 的記憶體與 ExitBootServices](./14-uefi-memory.md)
- [Ch 15 UEFI 變數與開機項管理](./15-uefi-variables.md)
- [Ch 16 從 UEFI app 載入並啟動 kernel](./16-uefi-load-kernel.md)
- [練習 B：UEFI app（memory map + 讀檔 + ExitBootServices）](./practice-b-uefi-app.md)

### Part 4 — Bootloader：GRUB 與其他（Ch 17–20）
- [Ch 17 Bootloader 的角色與生態](./17-bootloader-ecosystem.md)
- [Ch 18 GRUB 深入：架構與模組](./18-grub-internals.md)
- [Ch 19 GRUB 在 BIOS 與 UEFI 的差異](./19-grub-bios-uefi.md)
- [Ch 20 Multiboot 與 kernel handover protocol](./20-multiboot-handover.md)

### Part 5 — Kernel 啟動（Ch 21–25）
- [Ch 21 bzImage 結構與 kernel 解壓](./21-bzimage-decompress.md)
- [Ch 22 kernel 早期初始化](./22-kernel-early-init.md)
- [Ch 23 從 kernel 到第一個 process](./23-kernel-to-init.md)
- [Ch 24 initramfs / initrd 機制](./24-initramfs.md)
- [Ch 25 寫一個自製 initramfs](./25-custom-initramfs.md)
- [練習 C：自製 initramfs + switch_root](./practice-c-initramfs.md)

### Part 6 — init 與進階主題（Ch 26–30）
- [Ch 26 init 系統：從 SysV 到 systemd](./26-init-systemd.md)
- [Ch 27 Secure Boot：簽署鏈](./27-secure-boot.md)
- [Ch 28 Measured Boot 與 TPM](./28-measured-boot-tpm.md)
- [Ch 29 開機問題診斷](./29-boot-debugging.md)
- [Ch 30 ARM 與其他架構的開機差異](./30-arm-boot.md)

### Final Project
- [Final Project：從零組一個可開機系統](./final-project-boot-system.md)

## 學習方式建議

1. **每章都在 QEMU 跑起來**：開機是動手的學問。每個 boot sector、UEFI app、initramfs 都要真的在 QEMU 跑，看到輸出
2. **用 gdb 追執行**：QEMU + gdb remote 能單步追蹤從 reset vector 開始的每一條指令。這是理解開機最強的工具（Ch 0 教設定）
3. **故意弄壞看現象**：把 boot signature 改掉、跳過 A20、ExitBootServices 後用 boot service——看它怎麼壞，比看它怎麼成功更有教育意義

## 精選資料庫

### 必讀基礎

- **[OSDev Wiki](https://wiki.osdev.org/)**
  - 自製 OS / bootloader 的社群聖經；Boot Sequence、UEFI、GDT、Long Mode 等條目是本課多章的權威參考
- **[UEFI Specification](https://uefi.org/specifications)**
  - UEFI 的最終仲裁；Boot/Runtime Services、Protocol、變數的精確定義；當作查閱手冊

- **[Linux kernel: Documentation/x86/boot.rst](https://www.kernel.org/doc/html/latest/arch/x86/boot.html)**
  - Linux boot protocol 的權威來源；bzImage 結構、boot_params、bootloader 怎麼把控制權交給 kernel

### 推薦部落格 / 文章

- **[The Linux Boot Process series (0xax: Linux Insides)](https://0xax.gitbooks.io/linux-insides/content/Booting/)** — 0xax
  - 逐行讀 Linux kernel 開機原始碼的系列；Part 5（kernel 啟動）的最佳深度補充
- **[Writing an OS in Rust: bootloader posts](https://os.phil-opp.com/)** — Philipp Oppermann
  - 雖然用 Rust，但對 boot、long mode、UEFI 的解釋極清晰，概念跨語言通用

### 工具與規格

- **[QEMU documentation](https://www.qemu.org/docs/master/)**
  - 本課的主要實驗平台；-s -S（gdb stub）、OVMF 設定
- **[TianoCore / EDK II](https://github.com/tianocore/edk2)**（UEFI 參考實作，OVMF 來自這裡）

### 讀完本課之後

- **《Understanding the Linux Kernel》** — Bovet & Cesati（kernel 內部，開機之後的世界）
- **[coreboot documentation](https://doc.coreboot.org/)**（開源韌體，取代專有 BIOS/UEFI 韌體本身）
