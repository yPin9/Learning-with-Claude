# Ch 0 — 環境搭建：編一顆能下斷點的 QEMU，兩端 gdb 同時 attach

> **目標**：把 VM escape 練功台一次搭好——自己編一顆帶 symbol、可下斷點的 debug QEMU，準備一個能編 kernel module / 觸發 device 的 guest，並學會在 host（QEMU 行程）與 guest（guest kernel）兩端同時掛 gdb。

> **環境**：QEMU 9.0.x / x86-64 / Linux（Ubuntu 22.04 或 24.04 / Debian 12；WSL2 亦可，但 KVM 加速在 WSL 內受限，本課多數練習關掉 KVM 也能跑）

打瀏覽器你要自己編一顆 V8，打 kernel 你要自己編一顆 kernel。打 hypervisor 也一樣：**你的靶就是 host 上那個 `qemu-system-x86_64` 行程**，它是一個用 C 寫的、幾十萬行的 userland 程式。要打它，你得能在它裡面下斷點、看 heap、單步走 device callback。發行版套件裝的 QEMU 是 release 編譯、strip 過 symbol、開了一堆你不想要的最佳化——拿它練功等於閉著眼睛開刀。所以第一件事：自己編。

這章不談任何漏洞，只把台子架穩。台子沒架好，後面每一章你都會卡在「我斷點下不下去」「符號跑掉了」這種蠢事上。

## 為什麼需要這個？

VM escape 的除錯有一個 kernel/browser pwn 沒有的特殊性：**你要同時盯兩個世界**。

- **guest 世界**：你在 guest 裡跑一支 trigger（一個 kernel module 或 userspace 程式），對某個虛擬 device 送 I/O。這一端你想知道「我送出去的 command 長什麼樣、guest 這邊有沒有觸發到我要的路徑」。
- **host 世界**：QEMU 收到那個 I/O，dispatch 到對應 device 的 C 函式（例如某個 `mmio_write` callback）。這一端才是漏洞真正發生的地方——buffer overflow、OOB、UAF 全在這裡。你要斷在 host QEMU 的 C 函式上，看它的 heap、看它有沒有把你的 guest 資料當成長度/指標。

一條 VM escape 的完整因果鏈是「guest 送 I/O → host QEMU callback → host heap 被破壞 → host 行程被控」。**你必須能在這條鏈的每一段停下來觀察**，否則你只會看到「guest 送了東西、QEMU 崩了」，中間全是黑箱。兩端 gdb 就是把黑箱打開的工具。

歷史上第一批公開的 QEMU escape writeup（例如 CVE-2015-3456 VENOM、以及後來 0xKira 對 CVE-2019-6778 的 QEMU escape 系列）都建立在「host 端 gdb attach 到 QEMU、對 device 函式下斷點」這個基本功上。這章就是把這個基本功變成肌肉記憶。

## 先建立直覺

先在腦中畫出這張圖，後面所有除錯都是在這張圖上移動：

```
   ┌─────────────────────────── Host (Linux) ───────────────────────────┐
   │                                                                     │
   │   gdb #1  ──attach──►  qemu-system-x86_64  (一個 userland 行程)      │
   │   (打 host)             │                                           │
   │                         │  裡面跑著：                                │
   │                         │   - 主 vCPU thread（KVM_RUN 迴圈 or TCG）  │
   │                         │   - device 模擬程式碼（hw/ 下的 C）        │
   │                         │   - 一大塊 host heap（malloc 出來的）       │
   │                         │                                           │
   │        ┌────────────────┴────────────────┐                          │
   │        │      Guest 的 RAM = QEMU 行程     │                          │
   │        │      裡 mmap 出來的一段記憶體      │                          │
   │        │  ┌───────────────────────────┐   │                          │
   │        │  │  Guest kernel + userspace  │   │                          │
   │        │  │                            │◄──┼── gdb #2 (打 guest)      │
   │        │  │  你的 trigger 在這裡跑       │   │    透過 -s -S            │
   │        │  └───────────────────────────┘   │    (gdbstub :1234)       │
   │        └─────────────────────────────────┘                          │
   └─────────────────────────────────────────────────────────────────────┘
```

兩個 gdb 打的是**不同層**：gdb #2 透過 QEMU 內建的 gdbstub（`-s` 開在 tcp:1234）打的是「被虛擬化的那顆 guest CPU」，看的是 guest 的虛擬位址；gdb #1 用 `gdb -p` attach 的是「host 上真實的 QEMU 行程」，看的是 host 的真實位址。**漏洞在 gdb #1 那一層。** 這個分層搞混，你會一直對錯位址下斷點。

## 編一顆 debug QEMU

### 選版本：釘定，不要用 `master`

本課主線釘在 **QEMU 9.0**。理由：它夠新（涵蓋現代 device 與 mitigation），又已經穩定發行、writeup 生態成熟。`master` 每天在變，device 結構佈局、函式名說改就改，你照著教材下的斷點過幾週就對不上。想重現某個 CVE 時，你會 checkout 到「該 CVE 修補之前」的某個 tag，那是另一回事，用到再說。

QEMU 的 release tag 命名是 `vX.Y.Z`，例如 `v9.0.0`、`v9.0.2`（穩定分支的修補版）。

```bash
# 抓原始碼，checkout 釘定的 tag
git clone https://gitlab.com/qemu-project/qemu.git
cd qemu
git checkout v9.0.2          # 釘定；本課所有斷點/偏移以此為準

# 拉子模組（部分 firmware / 依賴以 submodule 提供）
git submodule update --init --recursive
```

> **未實測，理論預期**：作者的環境為 Windows，未在本機實跑以下編譯流程。指令依 QEMU 官方 build 文件（<https://www.qemu.org/download/#source> 與 `docs/devel/build-system.rst`）整理，請在你的 Linux 上實際執行。以下所有「編譯輸出」「gdb session」皆為依 QEMU 行為描述的**理論預期**，非貼上的真實 log。

### 裝依賴（Debian/Ubuntu）

```bash
sudo apt update
sudo apt install -y git build-essential ninja-build meson \
    libglib2.0-dev libpixman-1-dev pkg-config python3-venv \
    libslirp-dev zlib1g-dev flex bison
# pahole（看結構佈局用，下面會用到）
sudo apt install -y dwarves
```

### configure + build

QEMU 9.0 用 meson/ninja。`configure` 是它自己包的 wrapper，接受傳統的 `--` flag：

```bash
mkdir build && cd build

../configure \
    --target-list=x86_64-softmmu \   # 只編 x86-64 全系統模擬，省時間
    --enable-debug \                 # 關最佳化 + 開 debug info（關鍵）
    --enable-slirp                   # 使用者態網路，guest 上網用

# 編（-j 用你的核心數）
make -j$(nproc)
```

`--enable-debug` 做兩件對我們最重要的事：**（1）把最佳化降到 `-O0`**，這樣 gdb 單步時變數不會被最佳化掉、行號對得上；**（2）保留完整 DWARF debug info**，這樣 host 端 gdb 掛上去能看到函式名、結構成員、下 `break memory_region_dispatch_write` 這種符號斷點。它同時會開 `--enable-debug-tcg` 等除錯輔助。代價是慢——但我們要的是能看清楚，不是跑得快。

編完，你的靶在 `build/qemu-system-x86_64`。確認 symbol 在：

```bash
file ./qemu-system-x86_64
# 預期（未實測）：... ELF 64-bit ... not stripped
nm ./qemu-system-x86_64 | grep memory_region_dispatch_write
# 預期能看到該符號位址；若空白代表 symbol 被 strip，configure 有問題
```

## 準備 guest：小、可控、能觸發 device

guest 只是拿來「從裡面戳 host」的發射台，不需要花俏。兩條路：

**路線 A：buildroot 自製最小 rootfs（推薦，最可控）**
buildroot 幫你編一顆小 kernel + busybox rootfs，開機幾秒、乾淨、你能自己塞 kernel module 進去。

```bash
git clone https://gitlab.com/buildroot.org/buildroot.git
cd buildroot
make qemu_x86_64_defconfig      # 內建的 x86-64 QEMU 目標設定
make menuconfig                 # 開 module 支援、加 gcc/gdb 到 target（選用）
make -j$(nproc)
# 產出 output/images/ 下的 bzImage 與 rootfs.ext2
```

**路線 B：現成 cloud image（快，但較大）**
抓一顆 Debian/Ubuntu 的 `.qcow2` cloud image，用 cloud-init 設好登入。好處是裡面有完整 toolchain，能直接在 guest 裡 `gcc trigger.c`；壞處是大、開機慢、雜訊多。

不論哪條路，**guest 裡你需要能做兩件事**：（1）編一個 kernel module 或 userspace 程式去對目標 device 送 PIO/MMIO/DMA（後面 Part 2 會教怎麼寫）；（2）guest 有 root——本課威脅模型假設你在 guest 內已是 root（見 Ch 1），所以不必在 guest 裡先提權。

### 啟動 guest（buildroot 產物為例）

```bash
./qemu-system-x86_64 \
    -kernel bzImage \
    -drive file=rootfs.ext2,if=virtio,format=raw \
    -append "root=/dev/vda console=ttyS0" \
    -nographic \
    -m 512M \
    -device edu \                # 之後練習用的教學 device（QEMU 內建 hw/misc/edu.c）
    -s -S                        # 開 guest gdbstub，開機即暫停等你接
```

`-device edu` 是 QEMU 原始碼樹 `hw/misc/edu.c` 裡那個**故意留給學習用的假 PCI device**，它有 MMIO BAR、DMA、中斷，是我們 Part 2 第一個下手對象。`-s` 等於 `-gdb tcp::1234`，`-S` 讓 CPU 開機就停在第一條指令等 gdb（打 guest kernel 用）。

> 說明：`-s -S` 的 gdbstub 是拿來打 **guest** 的。要打 **host QEMU** 本身，不用這兩個 flag，改用下面的 `gdb -p`。

## 核心手法：兩端 gdb 同時 attach

### host 端：attach 到 QEMU 行程，斷在 device callback

QEMU 跑起來後（在另一個 terminal）：

```bash
# 找到 QEMU 的 PID 並掛 gdb
sudo gdb -p $(pidof qemu-system-x86_64)
```

掛上去後，因為你編的是 debug 版、symbol 齊全，可以直接對 device dispatch 路徑下符號斷點。MMIO 寫入的通用入口是：

```gdb
(gdb) break memory_region_dispatch_write
(gdb) break pci_dma_read
(gdb) break pci_dma_write
# 想斷特定 device，例如 edu：
(gdb) break edu_mmio_write
(gdb) continue
```

現在回到 guest，讓 trigger 對 device 送一次 MMIO write——host 端 gdb 就會停在 `edu_mmio_write`，你能看 `addr`、`val`、`size` 三個參數，那就是 guest 送過來的原始資料。**這一刻你就站在漏洞現場了。**

幾個 host 端立刻要會的 gdb 動作：

```gdb
(gdb) bt                    # 看是從哪條 dispatch 路徑進來的
(gdb) p/x val               # guest 送的值
(gdb) p *(EDUState*)opaque  # 看 device 的狀態結構（成員值一目了然，因為沒 strip）
(gdb) finish                # 跑完這個 callback，看 device 狀態怎麼變
```

### guest 端：另一個 gdb 接 gdbstub

```bash
gdb ./vmlinux                       # buildroot 的 vmlinux（帶 symbol 那顆）
(gdb) target remote :1234           # 接 QEMU -s 開的 gdbstub
(gdb) break start_kernel            # 打 guest kernel 的符號
(gdb) continue
```

這一端你看的是 guest 的世界：你的 kernel module 載入沒、trigger 有沒有走到你要的 `iowrite32`。**兩個 gdb 各管一層，合起來你就有了整條因果鏈的全視野。**

實務上一個順手的配置：兩個 terminal 各開一個 gdb，或用 tmux 分割。host 那個負責「漏洞現場」，guest 那個負責「確認我的 trigger 真的送出去了」。

## 底層機制：symbol、heap、結構佈局怎麼被看見

```
   你在 guest 送出 iowrite32(val, bar0 + off)
            │  (guest 虛擬位址 → EPT/影子分頁 → 這是一段 MMIO 區)
            ▼
   CPU VMEXIT，KVM 判定是 MMIO，把控制權交回 host QEMU
            │
            ▼
   QEMU: address_space_write → memory_region_dispatch_write
            │  查 MemoryRegion 的 ops 表，找到你這個 BAR 註冊的 write handler
            ▼
   edu_mmio_write(opaque, addr, val, size)   ◄── gdb #1 斷在這
            │  opaque 就是那個 device 的狀態結構 (EDUState*)
            ▼
   在這函式裡對 host heap 上的 buffer 做操作 ← 漏洞在此
```

**為什麼 debug 版看得清、release 版看不清？** 三點：

1. **DWARF debug info**：debug 版把「函式名、行號、每個結構的成員名與偏移」都嵌進 ELF。gdb 靠它把裸位址翻譯成 `EDUState->dma.dst`。release strip 掉這些，你只剩一堆數字。
2. **`-O0` 不最佳化**：release 的 `-O2` 會 inline 掉函式、把區域變數塞進暫存器再重用、重排指令。你單步時「行號亂跳、變數 `<optimized out>`」就是它。debug 版一行 C 對一段連續機器碼，單步乾淨。
3. **可讀的 heap**：QEMU 的 device 狀態與 buffer 大多是 `g_malloc`（底層還是 glibc malloc）出來的。你在 `binary_exploitation` 學的 glibc heap 直覺（chunk、tcache、bin）在這裡**原封不動適用**——host QEMU 的 heap 就是一個普通的 glibc heap，只是住在裡面的是 device 結構。

### 用 pahole 看結構佈局

要打一個 device，你得先知道它的狀態結構長怎樣、哪個成員在哪個偏移、buffer 有多大、buffer 旁邊坐著什麼（可能是你要蓋的函式指標）。`pahole` 直接從 debug info 把結構佈局印出來：

```bash
pahole -C EDUState ./qemu-system-x86_64
```

```c
/* 未實測，理論預期輸出（實際偏移以你編的 v9.0.2 為準）：*/
struct EDUState {
    PCIDevice       pdev;                 /*     0  ... */
    MemoryRegion    mmio;                 /*   ...      */
    QemuThread      thread;               /*            */
    ...
    uint64_t        dma_mask;             /*            */
    char            dma_buf[4096];        /*  <偏移>  4096 */  /* ← 有沒有溢位就看它 */
    ...
};
```

`pahole` 是打 device 的地圖。看到 `dma_buf[4096]` 旁邊坐了什麼、離下一個函式指標多遠——這決定你的溢位能蓋到什麼。Part 2、Part 3 會反覆用它。

## 對比與取捨

| 選擇 | debug QEMU（本課） | 發行版 release QEMU |
|---|---|---|
| symbol / 下斷點 | 完整，可下符號斷點 | strip，只能靠偏移硬幹 |
| 單步除錯 | `-O0`，行號變數乾淨 | `-O2`，變數常 optimized out |
| 執行速度 | 慢（可接受，練功用） | 快 |
| 貼近真實靶 | 佈局可能與生產環境略異 | 就是雲端/伺服器實際跑的 |
| 適用階段 | 學習、找洞、開發 exploit | 最後對真實 target 微調偏移 |

| 選擇 | KVM 加速 | 純 TCG（`-accel tcg`） |
|---|---|---|
| 速度 | 快（近原生） | 慢（解釋執行） |
| 需求 | host 要有 `/dev/kvm`（WSL2 受限） | 到處能跑 |
| device 模擬路徑 | 一樣走 QEMU 的 hw/ | 一樣走 QEMU 的 hw/ |
| 對本課影響 | device bug 的觸發與利用**不受影響** | 同左 |

> 關鍵認識：**device emulation 的漏洞路徑，KVM 與 TCG 走的是同一份 QEMU C 程式碼**。KVM 只加速「跑 guest 指令」這件事，device I/O 一律 VMEXIT 回 QEMU 用同一套 `hw/` 程式處理。所以 WSL2 沒 KVM 也能練本課絕大多數 device 攻擊——慢一點而已。

## 踩雷集錦

- **錯誤直覺**：「裝發行版的 `qemu-system-x86`，`apt install gdb` 就能打了。」→ **正確認識**：release QEMU strip 過、`-O2`，符號斷點下不了、變數看不到。務必自己 `--enable-debug` 編一顆。這是本課能不能開始的門檻。
- **錯誤直覺**：「`-s -S` 掛的 gdb 就是拿來打 QEMU 漏洞的。」→ **正確認識**：`-s -S` 的 gdbstub 打的是 **guest CPU**，看 guest 虛擬位址。漏洞在 **host QEMU 行程**，要另開一個 `gdb -p $(pidof ...)`。兩者是不同層，別搞混。
- **錯誤直覺**：「host gdb attach 不上，是 QEMU 的問題。」→ **正確認識**：多半是權限（要 `sudo`）或 ptrace_scope 限制。`echo 0 | sudo tee /proc/sys/kernel/yama/ptrace_scope` 放寬後再試。
- **錯誤直覺**：「WSL2 沒 KVM，這課沒法練。」→ **正確認識**：device 攻擊面用 `-accel tcg` 一樣能跑，只是慢。真正需要 KVM 的是 Part 1 硬體虛擬化那幾章的 `KVM_RUN` 實驗，那部分才需要真 Linux + `/dev/kvm`。
- **錯誤直覺**：「結構偏移背教材的就好。」→ **正確認識**：偏移跟編譯選項、版本、甚至 struct packing 有關。永遠用 `pahole -C <Struct> ./qemu-system-x86_64` 對**你自己編的那顆**確認，別信別人的數字。

## 進階：再往深一層

- **core dump 事後分析**：當你的 exploit 讓 QEMU 崩了但你來不及斷，可以 `ulimit -c unlimited` 讓它產 core，再 `gdb ./qemu-system-x86_64 core`。搭配 `set follow-fork-mode` 處理 QEMU 多執行緒（device 常在獨立 thread 跑 DMA）。
- **ASan 版 QEMU**：找洞階段，加 `--extra-cflags="-fsanitize=address"`（或 configure 的 sanitizer 選項）編一顆 AddressSanitizer 版。它會在 heap OOB/UAF 發生的**當下**就報，並印出精確的越界位址與 backtrace——比等 QEMU 隨機崩再回推快得多。Part 3、Part 4 找洞會回頭用它。
- **pwntools 寫 exploit**：guest 端的 trigger 最終你會用 C 寫（要精準控 I/O）；但 host 端如果需要跟 QEMU 的 monitor 或某個 socket 互動、或做 ROP chain 計算，`pwntools`（`from pwn import *`）的 `p64`/`u64`/`fit`/`ROP` 這些工具照樣好用。你在 userland pwn 那套 pwntools 肌肉記憶不用丟。
- **`-monitor` 與 QMP**：QEMU 的 monitor（`-monitor stdio` 或 QMP socket）能在執行期熱插拔 device、看 device 狀態，是 Ch 19「UAF via hot-unplug」的關鍵入口，先知道有這東西。

## 動手練習

1. **編出來並驗 symbol**：在你的 Linux（或 WSL2）clone QEMU、checkout `v9.0.2`、`--enable-debug` 編。編完 `nm build/qemu-system-x86_64 | grep memory_region_dispatch_write`，確認符號在。記錄編譯耗時與磁碟用量。
2. **兩端 gdb 都掛上**：用 buildroot 產物開一個帶 `-device edu -s -S` 的 guest。一個 gdb `target remote :1234` 打 guest kernel、斷 `start_kernel`；另一個 `gdb -p $(pidof qemu-system-x86_64)` 打 host、斷 `edu_mmio_write`。兩個都成功停下才算過關。
3. **pahole 讀地圖**：`pahole -C EDUState build/qemu-system-x86_64`，畫出這個 struct 的佈局圖，標出 `dma_buf`（或等價 buffer）的偏移與大小，以及它後面第一個指標型成員。這張圖 Part 2 會用到。
4. **摸一次現場**：guest 裡對 edu 的 MMIO BAR 隨便寫一個 32-bit 值（`devmem` 或簡單 C 程式），確認 host gdb 停在 `edu_mmio_write`，`p/x val` 印出你寫的值。體會「guest 資料如何原樣抵達 host callback」。

## 本章重點整理

- VM escape 的靶是 host 上的 **QEMU 行程**（userland C 程式）；要自己 `--enable-debug` 編一顆帶 symbol、`-O0` 的 debug QEMU 才能有效除錯。
- 本課釘定 **QEMU 9.0**（tag `v9.0.x`）；所有斷點與偏移以你自己編的這顆為準。
- 除錯要**兩端 gdb**：`gdb -p $(pidof qemu-system-x86_64)` 打 host（漏洞現場），`-s -S` + `target remote :1234` 打 guest（確認 trigger）。兩者是不同層，位址空間不同。
- device dispatch 的通用符號斷點：`memory_region_dispatch_write`、`pci_dma_read/write`，特定 device 則斷它的 `*_mmio_write` / `*_mmio_read`。
- host QEMU 的 heap 就是普通 glibc heap，你的 userland heap 直覺照用；`pahole -C <Struct>` 印結構佈局是打 device 的地圖。
- device 漏洞路徑 KVM 與 TCG 共用同一份 QEMU C 碼，所以無 KVM 的環境（WSL2）用 `-accel tcg` 也能練絕大部分本課內容。

## 自我檢核

- [ ] 我能說出為什麼要自己編 debug QEMU，而不是用發行版套件（symbol、`-O0`、可讀 heap 三點）。
- [ ] 我能講清楚 `gdb -p` 打的層和 `-s -S` gdbstub 打的層差在哪，各看什麼位址空間。
- [ ] 我知道 device dispatch 的通用入口符號至少三個。
- [ ] 我知道用什麼指令印出一個 device 狀態結構的佈局，以及為什麼要看它。
- [ ] 我理解沒有 KVM 為什麼不影響本課多數 device 攻擊練習。

## 延伸閱讀

- **QEMU 官方 build 文件**（`docs/devel/build-system.rst` 與 <https://www.qemu.org/download/#source>）——configure/meson 的權威來源。讀「如何加 debug/sanitizer flag」那段，本章 configure 指令即以此為據。
- **QEMU 原始碼 `hw/misc/edu.c` 與 `docs/specs/edu.rst`**——那顆教學 device 的完整規格與實作。它是你 Part 2 第一個下手的 device，先讀一遍它的 MMIO/DMA 介面，本課後面反覆用它。
- **0xKira, "qemu-vm-escape"（GitHub: 0xKira/qemu-vm-escape）**——一份完整、公開、可讀的 QEMU escape 專案（基於 CVE-2019-6778 等）。看它的 README 怎麼描述「host 端 attach QEMU、對 device 函式下斷點」的除錯流程，正是本章手法的實戰版。
- **`pahole` / dwarves 專案文件**——`man pahole`。學 `-C`（指定結構）與 `--hex`（偏移用 16 進位）。打 device 前用它讀結構佈局，會貫穿整個 Part 2/3。
- **QEMU `docs/devel/` 目錄整體**——尤其 `memory.rst`（MemoryRegion/AddressSpace 模型）。現在略讀混個臉熟，Ch 10 會逐項拆解，但先知道這份文件存在。

環境架好了，接著我們先把「什麼是 VM escape、它為什麼值錢、在整個 pwn 天梯的哪一階」講清楚，建立打這門課的動機與威脅模型。

→ [Ch 1 什麼是 VM Escape](./01-what-is-vm-escape.md)
