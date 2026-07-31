# Ch 0 — 環境搭建：build kernel、QEMU、gdb

> **目標**：從源碼 build 一顆帶除錯資訊的 Linux 6.12、用 QEMU 把它開起來、用 gdb 停在任何一個 kernel 函式上、載入你的第一個核心模組。這套環境是後面 53 章每一次「讀源碼 → 停下來看它真的怎麼跑」的基礎。

> **環境**：本課全程以 **Linux 6.12 LTS** 為對象，host 用 **Ubuntu 24.04（x86_64）**。Windows 使用者請在 **WSL2** 或一台 Linux VM 裡操作——kernel build 與 QEMU 都需要 Linux host。ARM64 的差異章節（Ch 14/16/23）會另外給 `qemu-system-aarch64` 的指令。

## 為什麼需要這個？

你不能靠讀而已學會 kernel。排程器的源碼你可以讀懂七成，但「`pick_next_task` 到底被誰呼叫、什麼時候呼叫、`rq` 裡那時有幾個 task」這種問題，讀源碼給不了答案——你得**停在那個函式上，用 gdb 看**。

問題是：kernel 不是普通程式。它沒有 `main()`、跑在特權層、崩潰會帶走整台機器。你不會想在自己的筆電上直接 debug 一顆會 panic 的 kernel。所以標準做法是：

- **在 QEMU 裡跑目標 kernel**——崩了就崩虛擬機，host 毫髮無傷，重開只要幾秒
- **QEMU 內建一個 gdb server**（GDB stub），host 上的 gdb 透過它停 kernel、看記憶體、單步——就像 JTAG 接實體板子，但全在軟體裡

這章把這套「QEMU 當標靶 + gdb 當狙擊鏡」的環境架起來。架好之後，你對任何一段 kernel 源碼的疑問，都能用「設個中斷點看它」來回答。

## 先建立直覺

先看清楚這套環境裡有哪幾個角色、它們怎麼串起來：

```
   ┌─────────────────────── 你的 Linux host（Ubuntu / WSL2）──────────────────────┐
   │                                                                              │
   │   build 出兩個產物：                                                          │
   │     bzImage   ── 壓縮過、可開機的 kernel 映像（餵給 QEMU 的 -kernel）          │
   │     vmlinux   ── 未壓縮 ELF，帶符號與除錯資訊（餵給 gdb，QEMU 不吃它）         │
   │                                                                              │
   │   ┌────────────── QEMU（虛擬機，跑目標 kernel）──────────┐      ┌──────────┐  │
   │   │  bzImage 在這裡開機 → 執行你要 debug 的那顆 kernel   │◄─────┤   gdb    │  │
   │   │  內建 GDB stub，監聽 tcp:1234                        │ :1234│ 讀 vmlinux│  │
   │   │  -S 讓它開機前先凍住，等 gdb 說「跑」                 │─────►│ 下中斷點  │  │
   │   └─────────────────────────────────────────────────────┘      └──────────┘  │
   └──────────────────────────────────────────────────────────────────────────────┘
```

關鍵是**同一顆 kernel 有兩個檔案**：QEMU 吃 `bzImage`（能開機但沒符號），gdb 吃 `vmlinux`（有符號但不能開機）。兩者從同一次 build 出來，位址對得上，所以 gdb 的中斷點才會準確落在 QEMU 跑的那顆 kernel 上。

> 如果你上過本 repo 的 `linux_boot`，這套 QEMU + gdb 你已經熟；差別在那門課關注「開機到 kernel 接手」，這門課關注「kernel 接手之後每個子系統怎麼跑」，所以我們的 kernel config 要**特別為除錯而開**（符號、gdb scripts、關掉會干擾除錯的隨機化）。

## Step 1：裝工具鏈

```bash
sudo apt update
sudo apt install -y \
    build-essential bc bison flex \
    libssl-dev libelf-dev \
    qemu-system-x86 gdb \
    busybox-static cpio \
    dwarves         # 提供 pahole，DEBUG_INFO_BTF 需要
```

- `build-essential bc bison flex libssl-dev libelf-dev`：編 kernel 的必要工具
- `qemu-system-x86`：我們的標靶虛擬機
- `busybox-static cpio`：等下做一個最小 rootfs（initramfs）用
- `dwarves`（`pahole`）：產生 BTF 除錯資訊要用；沒裝的話某些 config 會 build 失敗

## Step 2：拿到 6.12 源碼

```bash
# 方法一：淺 clone（省頻寬，但沒有完整 git 歷史）
git clone --depth 1 --branch v6.12 \
    https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git
cd linux

# 方法二：直接抓 tarball
# wget https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.12.tar.xz
# tar xf linux-6.12.tar.xz && cd linux-6.12
```

確認版本：

```bash
make kernelversion
# 6.12.0
```

> **為什麼釘死 6.12**：kernel 每 9~10 週一個大版，函式改名、結構欄位增減、整個子系統重寫（CFS→EEVDF 就是一例）是常態。本課每一章給的**檔案路徑、函式名、行號都對應 v6.12**。你用別的版本大方向不會錯，但細節對不上時，以 6.12 為準。6.12 是 LTS，會維護到 2026 年以後，是穩定的學習基準。

## Step 3：為「除錯」而生的 kernel config

從 `defconfig`（x86_64 的合理預設）出發，再手動打開除錯選項：

```bash
make defconfig

# 用 scripts/config 打開除錯相關選項（比手動 menuconfig 快）
./scripts/config \
    --enable  DEBUG_INFO_DWARF_TOOLCHAIN_DEFAULT \
    --enable  GDB_SCRIPTS \
    --enable  DEBUG_KERNEL \
    --enable  DEBUG_INFO \
    --enable  FRAME_POINTER \
    --enable  KGDB \
    --disable DEBUG_INFO_REDUCED \
    --disable RANDOMIZE_BASE      # 關掉 KASLR，位址才固定、gdb 中斷點才準

# 把上面的改動正規化進 .config
make olddefconfig
```

每個選項為什麼開：

| 選項 | 作用 | 不開的後果 |
|---|---|---|
| `DEBUG_INFO` + `DWARF_TOOLCHAIN_DEFAULT` | 把 DWARF 除錯資訊編進 vmlinux | gdb 看不到變數名、型別、行號 |
| `GDB_SCRIPTS` | 產生 `vmlinux-gdb.py`，提供 `lx-*` 指令 | 沒有 `lx-symbols`（自動載模組符號）、`lx-ps` 等便利指令 |
| `FRAME_POINTER` | 保留 frame pointer | backtrace 可能斷掉、不完整 |
| `DEBUG_INFO_REDUCED`（**關**） | 預設會砍掉大半除錯資訊省空間 | 開著的話 gdb 看不到大多數區域變數 |
| `RANDOMIZE_BASE`（**關 KASLR**） | 每次開機隨機化 kernel 基底位址 | 開著的話 gdb 的符號位址對不上實際載入位址，中斷點失準 |

> **踩雷預告**：KASLR（Kernel Address Space Layout Randomization）是正式環境的安全機制，但它會讓 gdb 的靜態符號位址和 kernel 實際跑的位址錯開。**除錯時一定要關**——要嘛 config 關掉 `RANDOMIZE_BASE`，要嘛開機參數加 `nokaslr`（我們兩個都做，雙保險）。這正是 `kernel_pwn` 課裡你要繞過的那個 KASLR，現在你從防禦方看到它為什麼存在。

## Step 4：編譯

```bash
make -j"$(nproc)"
```

第一次全編依機器快則 5 分鐘、慢則半小時。編完你會得到兩個關鍵產物：

```bash
ls -lh arch/x86/boot/bzImage   # 餵給 QEMU 的可開機映像（約 12 MB）
ls -lh vmlinux                 # 餵給 gdb 的 ELF（帶符號，約數百 MB）

file vmlinux
# vmlinux: ELF 64-bit LSB executable, x86-64, ..., with debug_info, not stripped
```

看到 `with debug_info, not stripped` 就對了——這代表 Step 3 的除錯設定生效。

## Step 5：做一個最小 rootfs（initramfs）

kernel 開機到最後會去執行 `/init`（使用者空間第一個程式，見 Ch 3）。我們用 busybox 做一個最小的 initramfs，讓 kernel 有東西可以跑、給我們一個 shell：

```bash
mkdir -p initramfs/bin
cp "$(which busybox)" initramfs/bin/busybox      # 用 busybox-static，不依賴 host 的 libc

# 寫一個 /init（PID 1）
cat > initramfs/init <<'EOF'
#!/bin/busybox sh
/bin/busybox mkdir -p /proc /sys /dev
/bin/busybox mount -t proc none /proc
/bin/busybox mount -t sysfs none /sys
/bin/busybox mount -t devtmpfs none /dev
echo ">>> Hello from kernel_internals initramfs <<<"
exec /bin/busybox sh          # 丟一個 shell 給我們
EOF
chmod +x initramfs/init

# 打包成 cpio + gzip（initramfs 的格式）
( cd initramfs && find . | cpio -H newc -o | gzip ) > ../initramfs.cpio.gz
cd ..
```

> `-H newc` 是 initramfs 要求的 cpio 格式（new ASCII format）。用錯格式 kernel 會 mount 失敗然後 panic。這個細節在 `linux_boot` 講過，這裡當前置條件用。

## Step 6：QEMU 開機

```bash
qemu-system-x86_64 \
    -kernel arch/x86/boot/bzImage \
    -initrd initramfs.cpio.gz \
    -append "console=ttyS0 nokaslr" \
    -nographic \
    -m 512M
```

參數解讀：

- `-kernel bzImage`：直接載 kernel，跳過 GRUB（QEMU 自己當 bootloader）
- `-initrd initramfs.cpio.gz`：把我們的 rootfs 當 initramfs 交給 kernel
- `-append "console=ttyS0 nokaslr"`：kernel 命令列。`console=ttyS0` 把 console 導到序列埠（配 `-nographic` 直接顯示在你的終端機）；`nokaslr` 再次確保 KASLR 關閉
- `-nographic`：不開圖形視窗，全部走終端機
- `-m 512M`：給虛擬機 512 MB RAM

順利的話你會看到 kernel 開機訊息刷過，最後停在：

```
>>> Hello from kernel_internals initramfs <<<
/ #
```

這個 `/ #` 就是你在 QEMU 裡跑的那顆自編 6.12 kernel 給你的 shell。**要離開 QEMU**：`Ctrl-a` 放開再按 `x`（`-nographic` 模式的退出組合鍵）。

## Step 7：接上 gdb（本課的核心工具）

現在把狙擊鏡裝上。開機指令加兩個旗標：

```bash
qemu-system-x86_64 \
    -kernel arch/x86/boot/bzImage \
    -initrd initramfs.cpio.gz \
    -append "console=ttyS0 nokaslr" \
    -nographic -m 512M \
    -S -s
```

- `-s`：等同 `-gdb tcp::1234`，在 1234 埠開一個 GDB stub
- `-S`（大寫）：開機前先**凍住** CPU，等 gdb 下令才跑——讓你能在 kernel 執行第一條指令之前就設好中斷點

QEMU 會卡住不動（因為被 `-S` 凍住了）。**另開一個終端機**，在 kernel 源碼目錄跑 gdb：

```bash
gdb vmlinux
```

在 gdb 裡：

```gdb
(gdb) target remote :1234          # 連上 QEMU 的 GDB stub
(gdb) source vmlinux-gdb.py        # 載入 CONFIG_GDB_SCRIPTS 產生的輔助腳本
(gdb) break start_kernel           # 停在 kernel C 語言的第一個函式（Ch 3 主角）
(gdb) continue
```

QEMU 那邊會開始開機，然後在 `start_kernel` 停下。回到 gdb：

```gdb
(gdb) backtrace
#0  start_kernel () at init/main.c:880
(gdb) lx-version                   # lx- 指令來自 vmlinux-gdb.py
Linux version 6.12.0 ...
```

你現在**站在 kernel 開機的最源頭，手裡握著單步、看記憶體、印變數的能力**。這就是後面每一章的工作方式。試幾個：

```gdb
(gdb) next                         # 單步（跨過函式呼叫）
(gdb) step                         # 單步（進入函式呼叫）
(gdb) print init_task              # 印出 PID 0 的 task_struct（Ch 9 主角）
(gdb) lx-ps                        # 列出目前所有 task（此刻只有 init_task）
```

## Step 8：你的第一個核心模組

最後，確認模組工具鏈能用。寫一個最小模組：

```c
// hello.c
#include <linux/init.h>
#include <linux/module.h>
#include <linux/kernel.h>

static int __init hello_init(void)
{
    pr_info("kernel_internals: hello, kernel!\n");
    return 0;                       // 回傳 0 = 載入成功
}

static void __exit hello_exit(void)
{
    pr_info("kernel_internals: goodbye\n");
}

module_init(hello_init);            // 註冊「載入時呼叫誰」
module_exit(hello_exit);            // 註冊「卸載時呼叫誰」
MODULE_LICENSE("GPL");              // 少了它 kernel 會標記 tainted 並拒絕某些符號
MODULE_DESCRIPTION("First module for kernel_internals");
```

配一個 `Makefile`（注意：recipe 行首必須是 **Tab 不是空白**）：

```makefile
obj-m += hello.o
KDIR := /path/to/your/linux        # 指向你 build 的那棵源碼樹

all:
	$(MAKE) -C $(KDIR) M=$(PWD) modules
clean:
	$(MAKE) -C $(KDIR) M=$(PWD) clean
```

```bash
make
ls hello.ko        # 編出來的核心模組
```

要在 QEMU 裡載入它，把 `hello.ko` 一起放進 initramfs（`cp hello.ko initramfs/`，重打包），開機後：

```
/ # insmod /hello.ko
kernel_internals: hello, kernel!
/ # rmmod hello
kernel_internals: goodbye
/ # dmesg | tail
```

看到 `pr_info` 的訊息，代表你的模組真的跑在 kernel 裡了。模組載入的**底層機制**（符號怎麼解析、`module_init` 怎麼被呼叫、簽署檢查）是 Ch 8 的主題，這裡先確認工具鏈通。

## 踩雷集錦

1. **`bzImage` 餵給 gdb**：gdb 要吃的是 `vmlinux`（未壓縮、帶符號的 ELF），不是 `bzImage`（壓縮過、給 QEMU 開機用）。餵錯 gdb 會抱怨沒符號或格式不對。記住：**QEMU 吃 bzImage，gdb 吃 vmlinux**。

2. **忘了關 KASLR，中斷點永遠不觸發**：如果你的中斷點設了卻從不停下，八成是 KASLR 沒關。確認 config 關了 `RANDOMIZE_BASE`，且開機參數有 `nokaslr`。

3. **`gdb` 先開、QEMU 沒加 `-S`**：沒有 `-S`，kernel 在你 gdb 連上前就跑過 `start_kernel` 了，你會停不到早期程式碼。除錯開機流程一定要 `-S`（凍住等你）。

4. **模組的 `KDIR` 指到系統 headers 而非你的源碼樹**：`M=$(PWD)` 編出來的模組必須對應你 QEMU 裡跑的那顆 kernel。如果 `KDIR` 指到 host 的 `/lib/modules/$(uname -r)/build`，編出來的模組版本對不上，QEMU 裡 `insmod` 會報 `version magic` 不符而拒載。

5. **Makefile 用空白縮排**：make 的 recipe 行首必須是 Tab。用空白會得到 `missing separator` 錯誤。這是 make 的老陷阱，不是 kernel 特有，但第一次寫模組很容易中。

## 進階：再往深一層

- **每次改 kernel 不用全編**：改一個 `.c` 檔後 `make -j$(nproc)` 只會重編動到的部分，通常幾秒到一分鐘。真正慢的只有第一次全編。
- **用 `-s` 除錯早期開機以外的程式碼**：如果你只想 debug 某個 syscall 或模組、不在乎開機流程，可以不加 `-S`，讓 kernel 正常開機到 shell，再從 gdb `Ctrl-C` 中斷、下中斷點、`continue`。
- **`lx-symbols` 自動載模組符號**：`insmod` 你的模組後，在 gdb 裡跑 `lx-symbols`，它會自動載入模組的符號，你就能 `break your_module_function` 停在模組函式裡（Ch 8 會用到）。
- **compile_commands.json 給編輯器**：`./scripts/clang-tools/gen_compile_commands.py` 產生 `compile_commands.json`，讓 VS Code / clangd 能跳轉 kernel 源碼——讀五千萬行的 kernel 沒有跳轉會很痛苦。
- **加速編譯**：裝 `ccache`，第二次編同一份程式碼會快很多。想更快可以只編你需要的子系統（但初學建議全編，避免符號缺失）。

## 動手練習

1. **把整套跑通一次**：從 clone 到 gdb 停在 `start_kernel`，確認每一步都通。這是後面 53 章的前提，卡住的地方現在解決。
2. **停在一個你認得的地方**：`break __x64_sys_write`（write syscall 的 x86_64 入口），`continue`，然後在 QEMU 的 shell 裡 `echo hi`——gdb 應該會停下來。用 `backtrace` 看 `echo` 的 write 是怎麼一路呼叫進來的。這預告了 Ch 4（syscall）和 Ch 34（read/write 路徑）。
3. **故意弄壞**：把模組的 `hello_init` 改成 `return -EINVAL;`（回傳非 0），重編、`insmod`，看會發生什麼。（提示：非 0 回傳值代表初始化失敗，模組會被拒絕載入——Ch 8 解釋這個約定的底層。）

## 本章重點整理

- 一次 kernel build 產出兩個檔：`bzImage`（QEMU 開機）與 `vmlinux`（gdb 符號），兩者位址一致所以中斷點才準。
- 除錯用的 kernel config 三要點：開 `DEBUG_INFO` + `GDB_SCRIPTS`、關 `DEBUG_INFO_REDUCED`、關 KASLR。
- QEMU 的 `-s`（開 gdb stub）+ `-S`（凍住等 gdb）是除錯開機流程的關鍵組合。
- 核心模組用 `module_init`/`module_exit` 註冊進入點，`M=$(PWD)` 對著你的源碼樹編，版本要對得上才能 `insmod`。

## 自我檢核

- [ ] 不看筆記，能說出為什麼 gdb 要吃 `vmlinux` 而不是 `bzImage`
- [ ] 能解釋除錯時為什麼**必須**關 KASLR，以及它平常（正式環境）為什麼要開
- [ ] 能獨立 build kernel、用 QEMU 開機、用 gdb 停在任一函式上
- [ ] 知道 `-S` 和 `-s` 各做什麼，什麼情況需要 `-S`
- [ ] 能寫出、編出、載入一個最小核心模組，並在 `dmesg` 看到它的輸出

## 延伸閱讀

### 官方文件

- **[Documentation/dev-tools/gdb-kernel-debugging.rst](https://www.kernel.org/doc/html/latest/dev-tools/gdb-kernel-debugging.html)**
  - **讀哪裡**：整篇，很短。這是 kernel 官方教你怎麼用 gdb + QEMU 除錯的權威說明，`lx-symbols`/`lx-ps`/`lx-dmesg` 等指令都在這裡
  - **和本章的關聯**：本章 Step 7 的流程就是照這篇；遇到 gdb 行為不符預期時回來查這篇

- **[Documentation/admin-guide/README.rst](https://www.kernel.org/doc/html/latest/admin-guide/README.html)**
  - **讀哪裡**：「Configuring the kernel」和「Compiling the kernel」兩節
  - **能學到什麼**：kernel build 流程的官方說明，補充本章沒展開的 config 系統細節

### 部落格 / 指南

- **[The Linux Kernel Module Programming Guide (LKMPG)](https://sysprog21.github.io/lkmpg/)** — sysprog21 維護
  - **讀哪裡**：前三章（Introduction、Hello World、Preliminaries）
  - **為什麼值得讀**：目前維護最勤、對應新 kernel 的模組入門指南；本章 Step 8 的模組是它的最小版，想多看幾個模組範例從這裡開始
  - **前提**：會 C、跟完本章的工具鏈設定

- **[Bootlin 的 Elixir Cross Referencer](https://elixir.bootlin.com/linux/v6.12/source)** — Bootlin
  - **這是什麼**：線上 kernel 源碼交叉索引，選 v6.12，任何函式/結構點一下就跳到定義、列出所有呼叫點
  - **為什麼值得用**：本課每章都給檔案路徑與函式名，配這個網站可以邊讀邊跳轉，不用在本機 grep 五千萬行程式碼

### 書籍

- **《Linux Kernel Development, 3rd Ed.》** — Robert Love（Addison-Wesley, 2010）
  - **這本書的定位**：最好讀的 kernel 入門；第 1–2 章（Introduction、Getting Started）談 build 與源碼樹佈局，和本章互補
  - **注意**：講的是較舊 kernel，build 指令以本章的 6.12 為準，但概念仍適用

環境架好了，你手上有一顆能停、能看、能改的 kernel。下一章我們拉遠鏡頭，先看清 kernel 這個「五千萬行的單一程式」整體長什麼樣、user/kernel 邊界在哪、以及怎麼在這麼大的源碼樹裡找到你要的東西。

→ [Ch 1 Kernel 全貌：monolithic 設計與怎麼讀源碼](./01-kernel-overview.md)
