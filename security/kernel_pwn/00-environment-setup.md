# Ch 0 — 環境搭建：QEMU + initramfs + kernel build + gdb remote

> **目標**：在你自己的機器上，從零組出一套「可開可關 mitigation、可單步除錯」的 Linux kernel 實驗環境——編出一顆 kernel、打一個 initramfs、用 QEMU 開機、用 gdb 接上去看記憶體與暫存器。這套環境是全書所有後續章節的地基，後面每一章的 vulnerable module 都在這裡面編譯、載入、觸發。

> **環境**：QEMU 8.2（`qemu-system-x86_64`），目標 kernel 為 **Linux 6.6 LTS**（6.6 是 2023 年 10 月釋出、官方標記為長期支援的版本），host 建議 Ubuntu 22.04/24.04 或任何能跑 Docker/WSL2 的 Linux。架構固定 x86-64。凡有版本差異的地方（尤其是 KPTI 的 cmdline 參數名）會在對應段落標注。

## 為什麼需要這一章？

User-space pwn 的環境很輕：一個 binary、一個 libc、一個 `pwntools` 腳本就能打本機或遠端。Kernel pwn 不一樣——你打的目標是**作業系統本身**，一旦打歪，輕則 kernel panic 重則整台機器凍結，不可能像 user-space 那樣直接在自己的真實系統上試錯。

所以 kernel pwn 的標準做法是：在**虛擬機裡跑一顆你自己編的 kernel**，犧牲一點真實度（你跑的 kernel 不是你机器上那顆），换到三個關鍵能力：

1. **弄壞了可以隨時重開**。QEMU 開機幾秒內就能重來，panic 了也不傷害 host。
2. **可以自由切換 mitigation 開關**。SMEP、SMAP、KASLR、KPTI 任意開關組合，同一顆 kernel、同一個 exploit，練出「每層防禦擋了什麼」的直覺——這是 Part 2、Part 4 的核心練習手法。
3. **可以用 gdb 接進 kernel 內部單步**。user-space 下你會很自然地用 gdb 看 crash；kernel 下如果沒有這套環境，你唯一的回饋是 `dmesg` 裡一行 oops，除錯效率差一個量級。

在 flex/bison 那門課裡，Ch 0 只需要裝好三個編譯器工具；這裡的「環境」本身就是一個小型作業系統工程，值得花一整章講清楚每一塊怎麼拼起來。

## 先建立直覺：你到底在組什麼

把整套環境想成四個零件，缺一個都開不了機：

```
┌─────────────────────────────────────────────────────────┐
│  Host 機器（你的 Linux / WSL2）                           │
│                                                           │
│   bzImage  ──┐                                           │
│   (壓縮過    │                                           │
│    的 kernel)│     ┌────────────────────────────────┐    │
│              ├────▶│   QEMU (qemu-system-x86_64)     │    │
│   initrd.cpio│     │   模拟一台虛擬 PC               │    │
│   (rootfs,   │     │   -kernel / -initrd / -append   │    │
│   含 init +  ┘     │   把三者載入，開始跑 boot        │    │
│   vulnerable │     └────────────────────────────────┘    │
│   module)           │                                    │
│                      │ gdbstub (-s -gdb tcp::1234)        │
│   vmlinux    ───────▶│ 帶 symbol 的 ELF，gdb 用它        │
│   (未壓縮,            │ 對照位址找函式名/變數               │
│    含除錯符號)         ▼                                   │
│                  gdb -ex 'target remote :1234'            │
└─────────────────────────────────────────────────────────┘
```

四個零件：

- **bzImage**：壓縮過的、可開機的 kernel image。QEMU 用 `-kernel` 載入它當開機對象。
- **initrd / initramfs**：一個 cpio 壓縮檔，解開後是一個迷你根目錄（`/bin`、`/lib`、一個 `init` 腳本）。開機後 kernel 把它整個放進記憶體當根檔案系統，不需要掛磁碟映像——這是 kernel pwn 實驗室的標準做法，因為可以瞬間重建、瞬間重開。
- **vmlinux**：**未壓縮**、保留完整 debug symbol 的 kernel ELF。`bzImage` 是給 QEMU 開機用的，`vmlinux` 是給 **gdb** 讀 symbol 用的——兩者是同一次編譯的兩個輸出,一個開機、一個除錯。
- **QEMU**：軟體模擬的 x86-64 PC，把前三者兜起來實際跑一次開機流程，並且可以選擇性地開一個 gdbstub（QEMU 內建的 remote debug server），讓外部 gdb 連進去控制這台虛擬機的 CPU 與記憶體。

你接下來要做的事情，就是依序把這四塊組出來。

## 拿到 kernel：下載原始碼、設定 .config

### 下載 6.6 LTS 原始碼

```bash
wget https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.6.tar.xz
tar xf linux-6.6.tar.xz
cd linux-6.6
```

也可以用 git 直接 clone 官方樹再 checkout 對應 tag，之後要用 `git log`/`git blame` 追 commit（Ch 2、Ch 42）時 git 版比 tarball 方便：

```bash
git clone --depth 1 --branch v6.6 https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git
```

### 設一份「刻意保留除錯資訊、刻意關掉一些 hardening」的 config

真實發行版（Ubuntu、Debian）的 kernel config 會把能開的 hardening 全部打開（KASLR、KPTI、STACKPROTECTOR、各種 SLAB hardening），這對第一次學習是阻力而非助力。我們從最小 config 出發，之後再一層層加回去：

```bash
make x86_64_defconfig      # 官方預設 config，涵蓋大多數常見硬體
make kvm_guest.config      # 疊加 KVM guest 需要的選項（virtio 等）
```

再手動調整幾個關鍵選項，用 `scripts/config`（比手動改 `.config` 文字檔安全，它會處理相依選項）：

```bash
# 開除錯資訊,之後 gdb 才認得 symbol
./scripts/config --enable DEBUG_INFO
./scripts/config --enable DEBUG_INFO_DWARF4
./scripts/config --enable GDB_SCRIPTS

# Part 0 先關掉這些,之後 Part 2/4 再逐一打開並觀察差異
./scripts/config --disable RANDOMIZE_BASE        # KASLR
./scripts/config --disable STACKPROTECTOR_STRONG

# 允許 insmod 自己編的、沒簽章的 module
./scripts/config --disable MODULE_SIG
./scripts/config --disable MODULE_SIG_FORCE

make olddefconfig    # 補上新增選項相依的預設值
```

> **未實測，理論預期行為**：以上 `scripts/config` 選項名稱、`x86_64_defconfig` 與 `kvm_guest.config` 的存在與行為，依 6.6 原始碼樹的 `scripts/kconfig/` 與 `arch/x86/configs/` 慣例推斷為正確，但完整的 `make olddefconfig` 互動結果因硬體/編譯環境而異，請在你的機器上實際跑一遍並核對 `.config` 裡對應選項確實生效（`grep CONFIG_RANDOMIZE_BASE .config` 應該輸出 `# CONFIG_RANDOMIZE_BASE is not set`）。

Ch 2 會細講 config 系統本身（defconfig、menuconfig、怎麼確認一個 `CONFIG_X` 有沒有開）；這裡先知道「這一步在幹什麼」就夠用。

### 編譯

```bash
make -j$(nproc)
```

全新編譯在現代多核機器上大約 10–30 分鐘不等（視核心數與磁碟速度，這個時間本身因機器而異,不要把它當作固定基準)。編完之後兩個關鍵產物：

```
arch/x86/boot/bzImage      # 給 QEMU -kernel 用
vmlinux                     # 給 gdb 用（在 repo 根目錄)
```

## 打包 initramfs：一個最小 rootfs

initramfs 本質是一個 cpio 封存檔。我們用 `busybox` 當全部使用者空間工具的來源——它把 `ls`、`cat`、`sh`、`mount` 等上百個指令塞進一個靜態連結的 binary 裡，換出一個幾 MB 就能跑的最小 Linux 環境。

### 用 busybox 建骨架

```bash
mkdir -p rootfs && cd rootfs
mkdir -p bin sbin etc proc sys dev lib lib64

# 從 Ubuntu/Debian 套件庫裝靜態版 busybox,或自己編
# apt 版本通常是動態連結,這裡建議自己編靜態版避免 libc 版本問題
wget https://busybox.net/downloads/busybox-1.36.1.tar.bz2
tar xf busybox-1.36.1.tar.bz2
cd busybox-1.36.1
make defconfig
./scripts/config --enable STATIC     # 靜態連結,initramfs 裡不需要再帶 libc
make -j$(nproc)
cp busybox ../bin/
cd ..

# busybox 支援「一個 binary、多個 applet」,靠檔名/symlink 分派
for cmd in sh ls cat mount insmod rmmod dmesg mkdir cp mv chmod sleep poweroff; do
    ln -sf busybox bin/$cmd
done
```

### 寫 init 腳本

initramfs 根目錄下的 `init` 是 kernel 開機後第一個執行的程式（PID 1）。它必須存在、必須可執行，否則 kernel 會 panic 並印出 `No working init found`。

```bash
cat > init << 'EOF'
#!/bin/sh
mount -t proc none /proc
mount -t sysfs none /sys
mount -t devtmpfs none /dev 2>/dev/null

echo "=== kernel pwn lab booted ==="

# 把編好的 vulnerable module 放進 /root,這裡先占位
# insmod /root/vuln.ko

setsid cttyhack /bin/sh   # 給一個有 controlling tty 的 interactive shell

poweroff -f               # exit shell 後直接關機,避免卡在 kernel panic 畫面
EOF
chmod +x init
```

`cttyhack` 是 busybox 特有的 applet，負責把目前的終端設成 controlling tty——沒有它，`sh` 跑起來會沒有 job control，Ctrl-C 等按鍵行為會很奇怪。

### 打包成 cpio

```bash
find . -print0 | cpio --null -ov --format=newc | gzip -9 > ../initramfs.cpio.gz
```

`-ov` 是 verbose + create；`--format=newc` 是 Linux kernel 認得的 cpio 格式（new ASCII format，帶較大的 inode number 欄位）；gzip 壓縮是選項，不壓縮也能開機（但 `-initrd` 傳入未壓縮的 `.cpio` 即可，kernel 兩種都認得）。

> 日常開發會頻繁重新打包 initramfs（每次改 module、改 init 腳本都要重打）。把上面這段寫成一個 `build_rootfs.sh`，比每次手動敲指令可靠。

## 寫 run.sh：QEMU 啟動腳本逐行解釋

```bash
#!/bin/bash
qemu-system-x86_64 \
    -m 512M \
    -kernel ./linux-6.6/arch/x86/boot/bzImage \
    -initrd ./initramfs.cpio.gz \
    -append "console=ttyS0 nokaslr nosmep nosmap panic=1 oops=panic" \
    -nographic \
    -cpu qemu64,+smep,+smap \
    -smp 1 \
    -s \
    -monitor /dev/null \
    -no-reboot
```

逐個拆：

- `-m 512M`：給虛擬機 512 MiB 記憶體。kernel pwn 實驗不需要大記憶體，小一點 heap spray 相關的位址空間反而更好觀察。
- `-kernel <bzImage>`：直接把壓縮 kernel 當開機對象載入，不需要真的裝一個 bootloader（GRUB 之類）。這是 QEMU 給 kernel 開發者的捷徑。
- `-initrd <cpio>`：把 initramfs 放進記憶體，kernel 開機後掛成根檔案系統。
- `-append "..."`：**kernel command line**，等同實體機 GRUB 傳給 kernel 的參數。這一行是整個實驗室「開關 mitigation」的主要入口，細節見下一節。
- `-nographic`：不開圖形視窗，所有 console 輸出導到目前的終端（搭配 `console=ttyS0`），這樣你可以直接在原本的 shell 裡看 kernel log 並互動。
- `-cpu qemu64,+smep,+smap`：指定 QEMU 模擬的 CPU 型號與擴充旗標。`qemu64` 是一個抽象、相容性好的 CPU model；`+smep`、`+smap` 顯式打開這兩個 CPU 層級的保護特性（CPU 要支援，kernel 才有東西可以用——這是跟 `-append` 裡的 `nosmep`/`nosmap` 不同層次的開關，見下一節的表格）。
- `-smp 1`：單核。多核會引入真正的並發（Part 6 的 race condition 練習才需要），Part 0–5 先用單核避免無謂的時序干擾。
- `-s`：等同 `-gdb tcp::1234`，開一個 gdbstub，監聽本機 1234 port 等 gdb 連進來。
- `-monitor /dev/null`：關掉 QEMU monitor（本來預設會跟 console 搶同一個 stdio），避免輸出互相干擾。
- `-no-reboot`：kernel panic 時直接讓 QEMU 進程結束，不要自動重開——寫 exploit 腳本時方便偵測「這次打 panic 了」。

### `-append` 裡的 mitigation 開關：一張對照表

cmdline 參數 vs CPU 旗標 vs kernel build-time config，三者是不同層次的開關，容易混在一起：

| 想關掉的東西 | QEMU `-cpu` 旗標（CPU 層，硬體要不要支援） | kernel cmdline 參數（boot 層，kernel 要不要啟用） | kernel `.config`（編譯層，程式碼要不要編進去） |
|---|---|---|---|
| KASLR | 不涉及 | `nokaslr` | `CONFIG_RANDOMIZE_BASE` |
| SMEP | `-smep` / `+smep` | `nosmep` | 無獨立 config（硬體特性，kernel 偵測到就用） |
| SMAP | `-smap` / `+smap` | `nosmap` | 無獨立 config（同上） |
| KPTI | 不涉及（純軟體 mitigation） | `pti=off`（6.6 的正式參數；**不是** `kpti=`，見下方說明） | `CONFIG_PAGE_TABLE_ISOLATION` |

> **關於 KPTI 的 cmdline 名稱，特別澄清**：很多舊文章與課程口語上會說「`kpti=` 開關」，但 x86 上正式的 kernel 參數是 **`pti=`**（可帶 `on`/`off`/`auto`），對應到 `Documentation/admin-guide/kernel-parameters.txt` 裡 `pti=` 這一條；`nopti` 是等同 `pti=off` 的捷徑寫法。ARM64 上才有獨立叫 `kpti=`（`on`/`off`）的參數,因為 arm64 的實作歷史和 x86 分開進來。本書之後提到「關 KPTI」，x86 環境一律用 `pti=off`。這點請在你自己的 6.6 樹上用 `grep -A3 "^\s*pti=" Documentation/admin-guide/kernel-parameters.txt` 核對一次，kernel 參數命名偶有跨版本調整。

`panic=1`：panic 後 1 秒自動重啟（配合 `-no-reboot`，QEMU 進程會在那之前先退出，這個參數主要是讓 log 裡清楚印出 panic 訊息後有機會 flush）。`oops=panic`：把本來只是印一行警告、kernel 還能硬撐著繼續跑的 oops，升級成直接 panic——寫 vulnerable module 練習時，這個設定能讓你**立刻**知道踩到問題，而不是讓 kernel 帶著損壞的狀態繼續跑導致後面更難除錯的二次崩潰。

跑起來：

```bash
chmod +x run.sh
./run.sh
```

你應該會看到一串 kernel boot log，最後停在我們 `init` 腳本印出的 `=== kernel pwn lab booted ===`，接著進入一個 interactive shell。

## gdb 接上去：target remote 與常用 gdbinit

開另一個終端（QEMU 那個視窗保持跑著，因為 `-s` 讓它在等 gdb 連線，不影響已經開機的虛擬機繼續跑）：

```bash
gdb ./linux-6.6/vmlinux
```

在 gdb 裡：

```gdb
target remote :1234
```

連上之後，gdb 看到的是**當前這一刻** QEMU 虛擬 CPU 的真實狀態——如果虛擬機已經開機完成停在 shell，你會看到 CPU 停在某個 idle loop 或者 syscall 等待裡。這時候下斷點、單步，都是對這台「正在跑」的虛擬機直接操作，效果等同你用 `gdbserver` 接 user-space 程式。

### 一份可用的 `.gdbinit`

把下面存成 `~/.gdbinit`（或專案目錄下的 `.gdbinit`，要先 `set auto-load safe-path /` 允許 gdb 讀取非 home 目錄的 init 檔）：

```gdb
# 關掉分頁輸出,避免長輸出被打斷
set pagination off

# kernel 是 PIE/位址隨機化相關的東西在 nokaslr 時不需要,但保留這行無妨
set disassembly-flavor intel

# 定義一個指令:連上本機 1234 的 QEMU gdbstub
define qconnect
    target remote :1234
end

# 定義一個指令:印出目前的暫存器與下一條指令,kernel pwn 除錯常用
define regs
    info registers
    x/5i $pc
end

# 常用的 kernel 函式直接下斷點的捷徑(存在才會成功,不存在 gdb 只是報錯不影響其他指令)
# break commit_creds
# break prepare_kernel_cred
```

接著你可以：

```gdb
(gdb) qconnect
(gdb) break do_sys_openat2
(gdb) continue
```

在另一個終端（跑在 QEMU shell 裡）執行 `ls`，gdb 這邊就會在 `do_sys_openat2` 斷下來——這證明了 remote debug 真的接上了「活著」的 kernel，不是靜態分析。

### KASLR 開著時怎麼辦

如果你的 config 保留了 `CONFIG_RANDOMIZE_BASE`、cmdline 也沒加 `nokaslr`，vmlinux 裡的靜態符號位址和實際跑起來的位址會差一個**開機時才決定的隨機位移（slide）**。這種情況下 `break <函式名>` 在 gdb 裡下斷點仍然有效——gdb 透過 remote protocol 查詢到的是執行中的實際位址，不是 vmlinux 裡的靜態位址（gdb 會自動處理 base 位移，前提是你 `target remote` 時 vmlinux 和正在跑的 kernel 是同一次編譯的產物）。但如果你要手動計算位址（例如之後 Ch 14 的 info leak 練習），記得減掉這個 slide 才能對回 vmlinux 裡的靜態符號。Part 0 我們統一用 `nokaslr` 排除這個變數，Ch 14 開始才正式處理它。

## 改 config 重編的日常流程

後面每一章大致都會要求你重編一次 kernel（開一個 mitigation、關一個 mitigation）。固定流程：

```bash
cd linux-6.6
./scripts/config --enable  CONFIG_XXX     # 或 --disable
make olddefconfig                          # 處理新增的相依選項提示
make -j$(nproc)                             # 只重編變動影響到的部分,不是每次都全新編譯
```

`make` 有良好的增量編譯機制——改一個 config 選項通常不需要重編全部檔案，幾分鐘內能跑完。真正的全新編譯（第一次、或者 `make mrproper` 清過的情況）才需要前面說的 10–30 分鐘量級。

## 整理成可重用腳本

把這章做的事情收斂成三個檔案，放在你實驗目錄的根目錄：

- `build_kernel.sh`：`make olddefconfig && make -j$(nproc)`，重編 kernel。
- `build_rootfs.sh`：重新打包 initramfs（每次 vulnerable module 改了都要跑）。
- `run.sh`：本章給的 QEMU 啟動腳本，之後各章會在 `-append` 那行依需求調整 mitigation 開關。

後面每一章的「怎麼驗證」段落，預設都是「用這三個腳本跑起這套環境」，不會每次重講一次怎麼搭。

## 踩雷集錦

1. **錯誤直覺：initramfs 裡隨便塞一個 ELF 就能跑。** 正確認識：你用 host 上的 `gcc` 編譯出的 user-space 程式預設動態連結 host 的 libc（`/lib/x86_64-linux-gnu/libc.so.6` 之類），initramfs 裡沒有這些 `.so`，執行時會報 `No such file or directory`（錯誤訊息本身會誤導你以為是檔案不存在，其實是動態連結器 `/lib64/ld-linux-x86-64.so.2` 找不到）。之後各章的 user-space 觸發程式一律用 `gcc -static` 編譯，或者手動把需要的 `.so` 一起塞進 initramfs 並設好 `LD_LIBRARY_PATH`。

2. **錯誤直覺：`-append` 裡的 `nosmep nosmap` 關掉了 CPU 的 SMEP/SMAP 硬體特性。** 正確認識：`-append` 的參數是傳給**開機中的 kernel**，只影響 kernel 要不要**使用**它偵測到的 CPU 特性；CPU 本身是否支援 SMEP/SMAP 是 `-cpu` 旗標那一層決定的（硬體層）。如果你 `-cpu qemu64,+smep,+smap` 又 `-append "nosmep nosmap"`，實際效果是「CPU 有能力但 kernel 選擇不用」——這正是 Ch 13 練習「nosmep nosmap 跑一次、全開跑一次」要操控的那個開關，兩層要分清楚。

3. **錯誤直覺：initramfs 打包完，裡面的檔案權限不重要。** 正確認識：`init` 腳本與 busybox 的執行權限（`chmod +x`）如果在打包前漏設，kernel 開機後會因為 `/init` 不可執行而直接 panic，訊息類似 `Kernel panic - not syncing: No working init found.`——這條錯誤訊息常被誤讀成「initramfs 沒打包成功」，但多半是權限問題，用 `cpio -tv < initramfs.cpio` 可以檢查封存內的權限位。

4. **錯誤直覺：`console=ttyS0` 可以隨便換成 `console=tty0` 也一樣。** 正確認識：`tty0` 是（模擬的）VGA 顯示輸出，配合 `-nographic` 不會顯示在你的終端上——你會看到 QEMU 卡住沒有任何輸出，實際上 kernel 可能已經開機完成甚至已經 panic，只是你看不到。`-nographic` 模式下必須用 `ttyS0`（模擬的序列埠），才會導到目前終端的 stdio。

5. **錯誤直覺：gdb 連上 gdbstub 後，`continue` 很慢代表 gdb 設定有問題。** 正確認識：QEMU 的軟體模擬（TCG，Tiny Code Generator，沒有硬體虛擬化加速時的執行方式）比原生執行慢一到兩個量級；如果你的 `qemu-system-x86_64` 沒有吃到 KVM 加速（host 不是 Linux、或沒裝 `qemu-kvm`、或虛擬化層層疊了巢狀虛擬化），開機與單步都會明顯變慢。這不是 gdb 的問題,是加速層沒生效——可以加 `-enable-kvm`（僅在 host 本身支援 KVM 時可用）或接受軟體模擬的速度。

## 進階：再往深一層

- **把這套環境容器化**：把 `build_kernel.sh`/`build_rootfs.sh`/`run.sh` 包進一個 Dockerfile，固定 `linux-6.6` 原始碼與 busybox 版本，讓環境可重現、可分享。kernelCTF 官方 repo 的做法正是如此——每個 target 都附完整的 build 腳本與固定版本的 rootfs。
- **用 `virtme-ng` 取代手動打包 initramfs**：這是一個社群工具，直接拿你編好的 kernel 搭配 host 現有的 rootfs 跑起來，省掉手動 busybox 打包的步驟，開發迴圈更快。本書 Part 0–2 用手動流程是為了讓你理解每個零件在幹什麼，熟悉之後可以換這類工具加速日常迴圈。
- **固定 `vmlinux` 與執行中 kernel 的對應關係**：之後處理 KASLR（Ch 14）時，你會需要在**沒有** gdb attach 的情況下，純用 exploit 計算出 slide。先在這章用 `nokaslr` 把一切釘死，等 Ch 14 再拿掉這根拐杖。

## 動手練習

1. 照本章流程，完整跑出一次「編 kernel → 打 initramfs → QEMU 開機 → gdb target remote 連上」。在開機完成的 shell 裡跑 `cat /proc/version`，確認版本字串裡的 commit/編譯時間是你剛剛編的那次。
2. 把 `-append` 裡的 `nokaslr` 拿掉，重開機兩次，用 `cat /proc/kallsyms | grep ' commit_creds$'`（需要 `CONFIG_KALLSYMS` 開啟）看 `commit_creds` 兩次開機的位址是否不同——親眼確認 KASLR 真的在動。
3. 故意把 `init` 腳本的執行權限拿掉（`chmod -x`)，重新打包、重開機，截圖/記錄下 panic 訊息,驗證踩雷集錦第 3 條說的現象。

## 本章重點整理

- 一套可重複、可摧毀重建的 kernel 實驗環境由四塊組成：`bzImage`（開機用）、`initramfs`（根檔案系統）、`vmlinux`（除錯符號）、QEMU（執行與 gdbstub）。
- mitigation 開關分三層——CPU 硬體特性（`-cpu` 旗標）、kernel boot 時決策（`-append` cmdline）、kernel 編譯時決策（`.config`）——三層不能混著想。
- x86 上關 KPTI 的正式 cmdline 參數是 `pti=off`，不是口語常說的 `kpti=`；這點查證過，但建議你在自己的樹上用 `grep` 複核一次（kernel 參數命名偶有調整）。

## 自我檢核

- [ ] 不看本章，能說出 `bzImage`、`initramfs`、`vmlinux` 分別是什麼、給誰用
- [ ] 能解釋為什麼 `-cpu +smep` 和 `-append nosmep` 是兩個不同層次的開關，關掉前者 vs 關掉後者對 exploit 的影響一樣嗎？
- [ ] 能說出至少兩個「kernel 開機失敗」的常見原因，以及怎麼從徵狀判斷是哪一種
- [ ] 知道為什麼 kernel pwn 的標準做法是在虛擬機裡跑自編 kernel，而不是直接在真實系統上練

## 延伸閱讀

### 官方文件 / Spec

- **[The kernel's command-line parameters](https://www.kernel.org/doc/html/latest/admin-guide/kernel-parameters.html)**
  - **讀哪裡**：搜尋 `nosmep`、`nosmap`、`nokaslr`、`pti=` 四個關鍵字對應的條目
  - **為什麼讀**：這是本章「mitigation 開關對照表」的權威來源；本章內容已經核對過，但 kernel 版本之間命名偶有變動，遇到你的版本行為不符預期時，這裡是最終仲裁
- **[Miscellaneous Devices (driver-api)](https://kernel.org/doc/html/v6.6/driver-api/misc_devices.html)**
  - **讀哪裡**：整頁不長，直接讀完
  - **和本章的關聯**：Ch 5 會用 `misc_register()` 註冊我們的第一個 vulnerable module，這裡先知道這個 API 存在

### 部落格 / 技術文章

- **[The Linux Kernel Module Programming Guide](https://sysprog21.github.io/lkmpg/)**
  - **這篇說什麼**：從 Hello World module 到 `/proc` 介面的完整入門，社群長期維護
  - **讀哪裡**：「Building Modules」與「Debugging」兩章，和本章的 initramfs/module 載入流程互補
  - **為什麼值得讀**：目前最完整、持續更新的開源 module 教學,本書 Part 0 多處會引用它

### 工具文件

- **[QEMU Documentation — Invocation](https://www.qemu.org/docs/master/system/invocation.html)**
  - **讀哪裡**：`-kernel`、`-initrd`、`-append`、`-s`/`-gdb`、`-cpu` 幾個選項的條目
  - **前提知識**：看完本章的 `run.sh` 逐行解釋後再讀會更有感，這裡補每個選項的完整參數形式（本章只用到常用子集）

這套環境跑起來之後，下一步是建立整張地圖——ring 0/3 的權限模型、為什麼 kernel bug 等於 game over，以及本書各個 Part 的技術落在攻擊者視角的哪個位置。

→ [Ch 1 kernel pwn 全景](./01-kernel-pwn-landscape.md)
