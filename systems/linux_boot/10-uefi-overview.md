# Ch 10 — UEFI 是什麼、為什麼取代 BIOS

> **目標**：理解 UEFI 的設計動機與架構——它如何解決 BIOS 的根本限制、UEFI 的開機流程（韌體→ESP→`.efi`）、boot manager 的角色、以及和 BIOS 開機的本質差異，建立 UEFI 線的全圖。

> **環境**：UEFI 2.x spec，OVMF（QEMU 的 UEFI 韌體）。本章是 UEFI 線的入口，概念為主，Ch 13 開始動手。

## 為什麼 BIOS 必須被取代？

你走完 BIOS 線（Part 2）就知道它有多繞：16-bit real mode 起步、512 bytes 的 boot sector、要爬三個模式才到 64-bit、CHS 磁碟限制、A20 line 包袱。這些都是 1981 年的設計，撐了四十年，但每個限制都成了現代系統的絆腳石。

```
BIOS 的根本限制（你在 Part 2 都踩過）：
  - 16-bit real mode 起步 → 要爬 real→protected→long（Ch 7-8 那一堆）
  - boot code 只有 446 bytes → 要兩階段（Ch 9）
  - 磁碟 CHS 定址 → ~8GB 限制
  - MBR 分區 → 4 個分區、2TB 上限（Ch 5）
  - 沒有標準化擴充機制 → 每個韌體廠商各搞各的
  - 沒有安全開機 → 任何 code 都能開機
```

UEFI（Unified Extensible Firmware Interface）從頭重新設計韌體介面，解決全部這些。理解 UEFI 為什麼這樣設計，你就懂現代電腦開機的實際樣貌。

## 先建立直覺：UEFI 是「韌體裡的小作業系統」

```
BIOS 的世界觀：
  韌體 = 一堆 16-bit 中斷服務（int 13h 等），原始、受限
  bootloader = 自己搞定一切（讀磁碟、切模式、解析檔案系統）

UEFI 的世界觀：
  韌體 = 一個小型作業系統環境，提供：
    - 已經在 64-bit 模式（不用爬模式！）
    - 檔案系統支援（能讀 FAT，用路徑開檔）
    - 豐富的服務（記憶體管理、裝置存取、變數儲存）
    - 標準化的程式格式（PE/COFF，像 Windows .exe）
        │
  bootloader = 一個跑在這個環境裡的「應用程式」（.efi）
        │
  → UEFI 把 BIOS bootloader 要自己做的苦工，變成韌體提供的服務
```

關鍵心智轉變：BIOS 下，bootloader 是「赤裸硬體上的求生者」，什麼都要自己來。UEFI 下，bootloader 是「跑在韌體 OS 上的應用程式」，能呼叫豐富的服務。這讓 UEFI bootloader 簡單太多——不用切模式（已在 64-bit）、不用寫磁碟驅動（韌體提供檔案系統）。

## UEFI 開機流程

```
UEFI 開機流程：

  按下電源
        │
  UEFI 韌體初始化（POST 等，類似 BIOS 但更現代）
        │
  韌體讀取 NVRAM 裡的 BootOrder 變數（開機順序）
        │
  對每個開機項（Boot####）：
    - 找到對應的開機裝置和 .efi 路徑
    - 預設路徑：ESP 上的 /EFI/BOOT/BOOTX64.EFI（後備）
      或變數指定的路徑（如 /EFI/ubuntu/grubx64.efi）
        │
  韌體載入那個 .efi（PE/COFF 格式）到記憶體
        │
  呼叫 .efi 的 entry point，傳入 UEFI 服務表
        │
  .efi（bootloader）跑起來，用 UEFI 服務載入 kernel...
```

對比 BIOS 的「讀 MBR 第一個 sector」，UEFI 是「讀 ESP 上的 `.efi` 檔案」。UEFI 不再有「512 bytes boot sector」的概念——`.efi` 可以任意大，因為韌體有檔案系統支援，能讀完整檔案。

## ESP：EFI System Partition

UEFI 從一個特殊分區開機——ESP（EFI System Partition）：

```
ESP（EFI System Partition）：
  - GPT 分區（Ch 11），type GUID = C12A7328-F81F-11D2-BA4B-00A0C93EC93B
  - 格式化成 FAT32（UEFI 韌體一定懂 FAT）
  - 掛載點通常是 /boot/efi
  - 裡面放 .efi 開機程式：
        /EFI/BOOT/BOOTX64.EFI       ← 後備開機路徑（韌體找不到別的時）
        /EFI/ubuntu/grubx64.efi     ← Ubuntu 的 GRUB
        /EFI/Microsoft/Boot/bootmgfw.efi  ← Windows
        ...
```

```bash
# 看你系統的 ESP（如果是 UEFI 開機）
ls /boot/efi/EFI/
# BOOT  ubuntu  Microsoft ...（各 OS 的開機程式）

mount | grep efi
# /dev/sda1 on /boot/efi type vfat ...   ← ESP 是 FAT32
```

> ESP 是 FAT32 是個刻意的設計：FAT 簡單、無專利爭議、所有韌體都能實作。UEFI 韌體保證能讀 FAT，所以 `.efi` 放 FAT 格式的 ESP，韌體就能用路徑載入它。這比 BIOS「讀固定 sector」靈活太多——你能在 ESP 放多個 `.efi`，韌體用 boot 變數選要開哪個。

## Boot Manager 與開機項

UEFI 韌體內建一個 **boot manager**，管理「有哪些開機選項」。這些選項存在 NVRAM 變數裡（Ch 15 詳述）：

```
UEFI 變數（存在主機板 NVRAM）：
  BootOrder    = 開機順序，如 0001,0003,0000（先試 Boot0001...）
  Boot0000     = 一個開機項（描述：名稱 + 裝置路徑 + .efi 路徑）
  Boot0001     = 另一個開機項
  ...
        │
  韌體照 BootOrder 依序嘗試，第一個成功的就開機
```

```bash
# 用 efibootmgr 看開機項（Ch 15 詳述）
efibootmgr
# BootCurrent: 0001
# BootOrder: 0001,0000,0002
# Boot0000* Windows Boot Manager
# Boot0001* ubuntu
# Boot0002* UEFI: USB ...
```

這是 UEFI 比 BIOS 強大的地方：開機選項是**結構化的、可程式化管理的變數**，不像 BIOS 的開機順序只是 BIOS 設定畫面裡的一個簡單列表。`efibootmgr` 能從作業系統裡修改開機項（BIOS 做不到）。

## UEFI vs BIOS：完整對照

| 面向 | BIOS | UEFI |
|---|---|---|
| CPU 起始模式 | 16-bit real mode（要爬到 64-bit）| 已在 64-bit long mode |
| 開機 code 位置 | MBR 第一個 sector（512B）| ESP 上的 `.efi` 檔案（任意大）|
| 開機 code 格式 | raw machine code | PE/COFF（像 Windows .exe）|
| 磁碟存取 | int 13h（CHS/LBA）| 韌體提供 block I/O + 檔案系統 |
| 檔案系統 | 無（bootloader 自己實作）| 韌體內建 FAT（能用路徑開檔）|
| 分區表 | MBR（4 分區、2TB）| GPT（128 分區、ZB 級）|
| 開機項管理 | BIOS 設定畫面（不可程式化）| NVRAM 變數（efibootmgr 可改）|
| 服務 | 16-bit 中斷（int 10h/13h）| Boot/Runtime Services（64-bit 函式）|
| 安全開機 | 無 | Secure Boot（Ch 27）|
| 擴充性 | 無標準 | 標準化 protocol/driver 模型 |

## 故意對照：BIOS 線 vs UEFI 線的「印字 Hello」

最能體現兩者差異的是「印一行字」這件最簡單的事：

```
BIOS 線（Part 2）印 "Hello"：
  - 16-bit real mode
  - org 0x7c00、搭舞台、int 10h
  - 一堆 assembly

UEFI 線（Ch 13）印 "Hello"：
  - 已在 64-bit
  - 寫 C，呼叫 UEFI 的 ConOut->OutputString()
  - 像寫普通 C 程式（gnu-efi 提供環境）
```

UEFI 下印字像寫普通 C 程式——因為 UEFI 提供了「控制台輸出」服務（ConOut protocol）。對比 BIOS 線要 `mov ah, 0x0e; int 0x10` 那套 assembly，UEFI 高階太多。這就是「韌體裡的小作業系統」的具體體現。Ch 13 你會親手寫這個 UEFI Hello。

## 踩雷集錦

1. **以為 UEFI 就是「圖形化的 BIOS 設定畫面」**：UEFI 設定畫面（漂亮的滑鼠介面）只是韌體的一個介面。UEFI 是整套韌體架構（服務、protocol、變數、`.efi` 模型），不是那個畫面

2. **以為 UEFI 不能開 MBR 磁碟**：UEFI 主要配 GPT，但多數 UEFI 韌體也能透過 CSM（相容模式，Ch 4）開 MBR/BIOS bootloader。純 UEFI（無 CSM）則要 GPT + ESP

3. **ESP 格式錯誤**：ESP 必須是 FAT（通常 FAT32）。格式成 ext4 或其他，UEFI 韌體讀不到，找不到 `.efi`

4. **`.efi` 路徑錯誤**：韌體找後備路徑 `/EFI/BOOT/BOOTX64.EFI`，或變數指定的路徑。`.efi` 放錯位置，韌體找不到就跳過這個開機項

5. **混淆 UEFI 和 Secure Boot**：UEFI 是韌體介面；Secure Boot（Ch 27）是 UEFI 的一個功能（驗證 `.efi` 簽署）。可以有 UEFI 但關閉 Secure Boot

## 進階：UEFI 的爭議與 coreboot

UEFI 不是沒有批評：

```
對 UEFI 的批評：
  1. 複雜度爆炸：UEFI spec 幾千頁，韌體 code 量巨大
     → 「韌體裡的小作業系統」也意味著「韌體有作業系統等級的 bug 面」
  2. 專有實作：多數 UEFI 韌體（基於 EDK II 但廠商客製）是閉源的
     → 安全性無法審計，歷史上有韌體層的漏洞/後門疑慮
  3. Secure Boot 的政治：誰掌握簽署金鑰？
     → 早期擔心微軟壟斷開機（雖然後來有 shim 等機制讓 Linux 共存，Ch 27）
```

**coreboot**（Ch 30 提及）是回應：一個開源的韌體實作，目標是最小化專有 code。它能搭配 UEFI payload（TianoCore）或直接 payload（如 SeaBIOS、Linux as payload）。理解 UEFI 的複雜度和封閉性，你會懂為什麼有 coreboot 這種「把韌體開源、最小化」的運動。

> **認識論誠實**：UEFI 取代 BIOS 是事實，但「UEFI 更好」是有保留的。它解決了 BIOS 的技術限制（模式、磁碟、分區），但帶來新問題（複雜度、封閉性、安全攻擊面）。它是「不同的權衡」而非「全面更好」。本課教 UEFI 是因為它是現代實務，但也要知道它的代價。

## 動手練習

1. 判斷你的系統用 UEFI 還是 BIOS：`[ -d /sys/firmware/efi ] && echo UEFI || echo BIOS`。如果是 UEFI，`ls /boot/efi/EFI/` 看有哪些 `.efi`

2. 看 ESP 結構：`mount | grep efi` 確認 ESP 是 FAT，`sudo ls -R /boot/efi/EFI/` 看完整的 `.efi` 佈局

3. 看開機項：`efibootmgr -v`（需要 UEFI 開機），看 BootOrder 和各 Boot####，理解韌體怎麼選開機

4. 用 OVMF 在 QEMU 起一個 UEFI 環境（Ch 0 的 Step 4），進 UEFI shell，打 `map` 看 UEFI 認得的裝置，`ls fs0:` 看 ESP 內容

## 本章重點整理

- UEFI 解決 BIOS 的根本限制：16-bit 起步、512B boot code、CHS、MBR、無安全開機
- UEFI 是「韌體裡的小作業系統」：已在 64-bit、有檔案系統、提供豐富服務，bootloader 是跑在上面的 `.efi` 應用程式
- 開機流程：韌體讀 NVRAM 的 BootOrder → 找 ESP 上的 `.efi`（PE/COFF 格式）→ 載入執行
- ESP 是 FAT32 分區，放 `.efi`；boot manager 用 NVRAM 變數管理開機項（efibootmgr 可改）
- UEFI 不是全面更好——它解決技術限制但帶來複雜度和封閉性（coreboot 是回應）

## 自我檢核

- [ ] 能說出 UEFI 解決了 BIOS 的哪些限制（至少三個）
- [ ] 能解釋「UEFI 是韌體裡的小作業系統」這個比喻的具體意義
- [ ] 知道 ESP 是什麼、為什麼是 FAT、`.efi` 放哪
- [ ] 能說出 UEFI 開機流程（BootOrder → ESP → .efi）和 BIOS（MBR sector）的差異
- [ ] 知道 UEFI 不是「全面更好」，能說出它的代價（複雜度、封閉性）

## 延伸閱讀

### 官方文件

- **[UEFI Specification](https://uefi.org/specifications)**
  - **讀哪裡**：先讀 Section 2（Overview）建立全圖，細節留到後續章節
  - **學什麼**：UEFI 的權威定義；當作查閱手冊，不要從頭讀（幾千頁）
  - **前提**：本章建立的概念

- **[OSDev Wiki: UEFI](https://wiki.osdev.org/UEFI)**
  - **讀哪裡**：整頁，UEFI 的架構和開機流程概覽
  - **學什麼**：自製 OS 角度的 UEFI 入門，本章的補充
  - **前提**：無

### 部落格 / 文章

- **[Beyond BIOS: the UEFI history](https://www.happyassassin.net/posts/2014/01/25/uefi-boot-how-does-that-actually-work-then/)** — Adam Williamson
  - **這篇說什麼**：清楚解釋 UEFI 開機實際怎麼運作、ESP、boot 變數
  - **讀哪裡**：整篇
  - **為什麼值得讀**：Fedora 開發者寫的，把 UEFI 開機講得比 spec 易懂得多

### 書籍

- **《Beyond BIOS: Developing with the Unified Extensible Firmware Interface》** — Zimmer, Rothman, Marisetty（Intel Press）
  - **這本書的定位**：UEFI 的權威書（Intel 的 UEFI 設計者寫的），比 spec 有脈絡
  - **讀哪幾章**：前幾章的架構概覽
  - **前提**：本章 + UEFI spec 的基本概念

→ [Ch 11 GPT 分區表](./11-gpt-partition.md)
