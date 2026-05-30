# Ch 1 — 從電源到 shell：開機全景圖

> **目標**：建立整個開機流程的全景心智圖——五大階段（firmware → bootloader → kernel → initramfs → init）各做什麼、控制權如何一棒接一棒傳遞、BIOS 和 UEFI 兩條路徑的分歧與匯合點，讓後面每一章都能對應到全圖的某個位置。

## 為什麼要先看全景？

開機流程很長、很多階段、很多術語（MBR、GRUB、bzImage、initramfs、systemd...）。如果一開始就鑽進某個細節，你會迷失在「這個東西在整體的哪裡」。

這章不深入任何一個階段，只建立**全圖**——讓你之後讀每一章時，都知道「我現在在開機流程的哪一步、上一棒是誰、下一棒交給誰」。開機的本質是**控制權的接力傳遞**，每一棒做完自己的事就把控制權交給下一棒。

## 先建立直覺：開機是一場接力賽

```
按下電源
   │
   ▼
┌──────────────┐  我：初始化硬體，找到可開機裝置
│  1. 韌體      │  交棒給：開機裝置上的 bootloader
│  Firmware     │  （BIOS 或 UEFI）
└──────┬───────┘
       ▼
┌──────────────┐  我：找到 kernel，載入記憶體，準備環境
│  2. Bootloader│  交棒給：kernel
│  (GRUB 等)    │
└──────┬───────┘
       ▼
┌──────────────┐  我：解壓自己，初始化 CPU/記憶體/裝置，
│  3. Kernel    │      掛載初始檔案系統
│               │  交棒給：第一個 userspace process
└──────┬───────┘
       ▼
┌──────────────┐  我：（臨時 rootfs）載入真正 rootfs 需要的
│ 4. initramfs  │      驅動，掛載真正的 root，然後換過去
│ (early        │  交棒給：真正 root 上的 init
│  userspace)   │
└──────┬───────┘
       ▼
┌──────────────┐  我：（PID 1）啟動所有系統服務，
│  5. init      │      最後給你登入畫面 / shell
│  (systemd)    │
└──────┬───────┘
       ▼
   你的 shell prompt
```

五棒接力。每一棒的核心問題都是：「我做完我的事，怎麼把控制權乾淨地交給下一棒？」交棒的細節（位址、暫存器狀態、約定的資料結構）就是本課大量篇幅在講的東西。

## 五大階段逐一概覽

### 階段 1：韌體（Firmware）

電源一開，CPU 從一個固定位址（reset vector）開始執行——那裡是主機板上韌體晶片的 code。韌體做的事：

- **POST**（Power-On Self-Test）：檢查 CPU、記憶體、基本硬體
- **初始化硬體**：把硬體帶到可用狀態
- **找開機裝置**：照設定的順序找可開機的磁碟/USB/網路
- **載入並執行 bootloader**：把控制權交給開機裝置上的 bootloader

韌體有兩種：**BIOS**（傳統，1981 年 IBM PC 沿用至今）和 **UEFI**（現代，2000 年代取代 BIOS）。這是本課的兩條主線——它們在這個階段分歧（Part 2 vs Part 3）。

### 階段 2：Bootloader

Bootloader 是介於韌體和 kernel 之間的橋樑。為什麼需要它？因為韌體不懂 Linux kernel 的格式、不懂檔案系統、不懂「使用者想開哪個 OS」。Bootloader 補上這些：

- 提供開機選單（多個 OS / 多個 kernel 版本）
- 從檔案系統讀取 kernel 和 initramfs
- 把 kernel 載入記憶體，設定好 kernel 期待的環境
- 把控制權交給 kernel（按 kernel 約定的 handover protocol）

最常見的是 **GRUB**（Part 4 深入）。其他：systemd-boot、U-Boot（嵌入式）、syslinux。

### 階段 3：Kernel

Kernel 拿到控制權時，自己通常是壓縮的（`bzImage`）。它要：

- **解壓自己**：bzImage 的前段是解壓 stub，把真正的 kernel（vmlinux）解壓到記憶體
- **早期初始化**：設定 CPU 到正確模式、建立頁表、初始化中斷、偵測硬體
- **掛載初始檔案系統**：掛載 initramfs（在記憶體裡的臨時 rootfs）
- **啟動第一個 process**：執行 initramfs 裡的 `/init`，這是第一個 userspace process（PID 1）

Part 5 深入 kernel 啟動。

### 階段 4：initramfs（early userspace）

這一棒常被忽略但很關鍵。問題：kernel 要掛載真正的 root 檔案系統（你的硬碟），但**掛載它需要驅動**（磁碟控制器驅動、檔案系統驅動、可能還要解密 LUKS、組 RAID/LVM）。這些驅動如果全編進 kernel，kernel 會肥大且不靈活。

解法：**initramfs**——一個小小的、在記憶體裡的臨時根檔案系統，裡面有「掛載真正 root 需要的驅動和工具」。流程：

- kernel 掛載 initramfs 當臨時 root
- initramfs 的 `/init` 載入需要的驅動、組 LVM/RAID、解密
- 掛載真正的 root 檔案系統
- `switch_root` 切換到真正的 root，執行真正的 init

Part 5 的 Ch 24-25 深入，你會親手做一個 initramfs。

### 階段 5：init（PID 1）

切到真正的 root 後，執行真正的 init——現代 Linux 幾乎都是 **systemd**（PID 1）。它：

- 啟動所有系統服務（網路、log、各種 daemon）
- 按依賴關係並行啟動（systemd 的 target/unit 機制）
- 最後給你登入畫面（getty / display manager）

到這裡，你看到 shell prompt 或登入畫面，開機完成。Ch 26 講 init/systemd。

## BIOS 與 UEFI 兩條路徑

本課最重要的結構：firmware 階段分成兩條路徑，後面匯合。

```
                    按下電源
                       │
          ┌────────────┴────────────┐
          ▼                         ▼
    ┌──────────┐              ┌──────────┐
    │  BIOS     │              │  UEFI    │
    │ (傳統)    │              │ (現代)   │
    └────┬─────┘              └────┬─────┘
         │                         │
   讀 MBR 第一個                 讀 GPT 的 ESP，
   sector (512B)               執行 .efi 程式
   到 0x7c00                   (UEFI application)
         │                         │
   16-bit real mode            已在 64-bit 環境
   要自己切到 64-bit            UEFI 提供豐富服務
         │                         │
         └──────────┬──────────────┘
                    ▼
              Bootloader (GRUB)
              （GRUB 有 BIOS 版和 UEFI 版）
                    │
                    ▼
              Kernel → initramfs → init
              （這之後兩條路徑完全一樣）
```

| 面向 | BIOS | UEFI |
|---|---|---|
| 年代 | 1981（IBM PC）| 2000s+ |
| 開機 code 位置 | MBR 第一個 sector（512B）| ESP 上的 `.efi` 檔案 |
| 載入到 | 記憶體 `0x7c00` | UEFI 載入 PE 格式到它分配的記憶體 |
| CPU 模式 | 16-bit real mode（要自己切換）| 已在 64-bit，有完整服務 |
| 分區表 | MBR | GPT |
| 大小限制 | boot code 只有 446 bytes 可用 | `.efi` 可以任意大 |
| 服務 | BIOS interrupt（int 13h 等）| UEFI Boot/Runtime Services |

> **認識論誠實**：現代電腦幾乎都用 UEFI，BIOS 正在淘汰（很多新主機板只剩 UEFI 或 CSM 相容模式）。那為什麼還學 BIOS？因為 BIOS 線更**簡單、更貼近硬體**——你親手寫 16-bit boot sector、親手切換 CPU 模式，這個過程把「CPU 開機時的赤裸狀態」教得最透徹。UEFI 幫你做了太多事，反而看不到底層。學 BIOS 是為了理解硬體本質；學 UEFI 是為了理解現代實務。兩條都走，理解最完整。

## 控制權交接：本課的主軸

注意上面每一棒交棒時的「約定」——這是本課反覆出現的主題：

```
firmware → bootloader：
  BIOS：載入 512B 到 0x7c00，CPU 在 real mode，jmp 過去
  UEFI：載入 .efi，呼叫它的 entry point，傳 UEFI 服務表

bootloader → kernel：
  按 Linux boot protocol（Ch 20）：
  填好 boot_params 結構、kernel 載到約定位址、跳到 entry point

kernel → init：
  kernel 掛好 initramfs，execve("/init")

initramfs → real init：
  switch_root，execve 真正的 /sbin/init
```

每個箭頭都是一個精確的「介面契約」——交棒方要把記憶體、暫存器、資料結構準備成接棒方期待的樣子。搞懂這些契約，就搞懂了開機。

## 踩雷集錦

1. **以為 bootloader 和 kernel 是同一個東西**：它們是分開的。bootloader（GRUB）載入 kernel，然後**交棒**消失。kernel 接手後 bootloader 不再執行

2. **以為 initramfs 是「可選的小東西」**：現代發行版幾乎都用 initramfs，它是掛載真正 root 的必要橋樑（尤其 root 在 LVM/LUKS/RAID 上時）。沒有它，kernel 可能掛不上你的 root

3. **混淆 BIOS/UEFI 是「設定畫面」**：很多人以為「BIOS」就是開機按 Del 進的那個藍色設定畫面。那是韌體的**設定介面**；BIOS/UEFI 是整個韌體系統，設定畫面只是它的一小部分

4. **以為 UEFI「更快開機」是因為跳過階段**：UEFI 和 BIOS 的階段數其實差不多。UEFI 通常較快是因為現代韌體優化（如平行初始化、Fast Boot 跳過某些檢查），不是因為架構上少了階段

5. **以為 systemd 從開機第一刻就在**：systemd 是 PID 1，但它在**最後一棒**才上場（kernel 和 initramfs 之後）。前面四棒都跟 systemd 無關

## 進階：固件之前還有東西

本課從「按下電源、CPU 從 reset vector 開始」講起。但嚴格說，在 x86 主韌體（BIOS/UEFI）執行之前，現代平台還有更早的階段：

- **Intel ME / AMD PSP**：主 CPU 啟動前，管理引擎（一顆獨立的小處理器）先初始化、做安全檢查。這是個有爭議的黑盒子（無法審計、有安全疑慮）
- **微碼載入**：CPU 啟動極早期載入 microcode（韌體更新 CPU 行為）
- **記憶體訓練**：UEFI 早期會「訓練」DRAM（校準時序），這是開機較慢的一個原因

這些超出本課範圍（且多是專有黑盒子），但知道「reset vector 之前還有世界」能讓你的全圖更完整。**coreboot**（Ch 30 提及）試圖用開源韌體取代這些黑盒子。

## 動手練習

1. 在你自己的機器上判斷它是 BIOS 還是 UEFI 開機：`[ -d /sys/firmware/efi ] && echo UEFI || echo BIOS`。為什麼這個判斷有效？（提示：UEFI 開機的系統，kernel 會暴露 efi 相關介面）

2. 看你系統的開機流程證據：`systemd-analyze`（總開機時間）、`systemd-analyze blame`（各服務耗時）、`systemd-analyze critical-chain`（關鍵路徑）。這些是階段 5（systemd）的視角

3. 看 initramfs 的內容：`lsinitramfs /boot/initrd.img-$(uname -r) | head -50`（Debian/Ubuntu），看裡面有什麼驅動和工具

4. 畫出你自己機器的開機接力圖：firmware（BIOS/UEFI？）→ bootloader（GRUB？）→ kernel 版本 → init（systemd？），每一棒填上具體的東西

## 本章重點整理

- 開機是五棒接力：firmware → bootloader → kernel → initramfs → init，每棒做完交棒給下一棒
- 核心是「控制權交接的契約」——交棒方把記憶體/暫存器/資料結構準備成接棒方期待的樣子
- firmware 分 BIOS（傳統，MBR/real mode）和 UEFI（現代，GPT/.efi）兩條路徑，在 bootloader 後匯合
- initramfs 是「掛載真正 root 的橋樑」——提供掛載 root 需要的驅動，然後 switch_root
- systemd 是最後一棒（PID 1），不是從開機第一刻就在

## 自我檢核

- [ ] 不看筆記，能畫出五棒接力的全圖，說出每棒做什麼、交棒給誰
- [ ] 能說出 BIOS 和 UEFI 在 firmware 階段的主要差異，以及它們在哪裡匯合
- [ ] 能解釋為什麼需要 initramfs（不是直接掛 root 就好）
- [ ] 能判斷一台機器是 BIOS 還是 UEFI 開機，並說出判斷依據
- [ ] 知道為什麼本課要學「已經淘汰的」BIOS 線（理解硬體本質）

## 延伸閱讀

### 官方文件

- **[Bootlin: Embedded Linux boot process](https://bootlin.com/doc/training/boot-time/)** 或類似的 boot overview
  - **讀哪裡**：boot sequence 概覽那幾頁
  - **學什麼**：開機階段的全圖，本章的補充視角
  - **前提**：無

- **[Arch Wiki: Arch boot process](https://wiki.archlinux.org/title/Arch_boot_process)**
  - **讀哪裡**：整頁，它把 firmware → bootloader → kernel → init 講得很清楚
  - **學什麼**：實務角度的完整開機流程，每階段有具體工具
  - **前提**：無

### 部落格 / 文章

- **[How Linux boots (a thorough walkthrough)](https://0xax.gitbooks.io/linux-insides/content/Booting/)** — 0xax (Linux Insides)
  - **這篇說什麼**：從原始碼角度逐階段拆解 Linux 開機
  - **讀哪裡**：先讀 intro 建立全圖，細節留到 Part 5
  - **為什麼值得讀**：本課 Part 5 的最佳深度補充，現在先看全貌

### 書籍

- **《How Linux Works, 3rd ed.》— Ch 5 (How the Linux Kernel Boots) & Ch 6 (How User Space Starts)** — Brian Ward
  - **這本書的定位**：用平易的方式講整個開機流程，適合本課的入門對照
  - **讀哪幾章**：Ch 5–6，和本章的全圖直接對應
  - **前提**：無

→ [Ch 2 x86 啟動時的 CPU 狀態](./02-cpu-startup-state.md)
