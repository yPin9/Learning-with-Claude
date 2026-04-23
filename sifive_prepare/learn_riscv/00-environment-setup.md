# Ch 0 — 環境搭建

> 目標：裝齊 RISC-V 交叉編譯、ISA 模擬器（spike）、system emulator（QEMU），並用三種方式跑同一支 `hello, world` — 確保你之後每一章的範例都能立刻驗證。

## 先講清楚：三個關鍵工具各做什麼

很多人一開始會被 spike / QEMU / riscv-toolchain 搞混。先釐清：

| 工具 | 角色 | 你會在哪用 |
|---|---|---|
| **riscv64-unknown-elf-gcc** | baremetal cross compiler（沒 OS） | 跑 spike / 寫 bootloader 等無 OS 場景 |
| **riscv64-linux-gnu-gcc** | Linux cross compiler（有 glibc） | 給 qemu-user 跑 Linux 程式 |
| **spike** | 官方 ISA reference simulator | 驗證**語意**是否符合 spec、debug 指令行為 |
| **qemu-system-riscv64** | 跑整個 virtual machine（含 Linux kernel） | 跑 distro、測 driver、整合測試 |
| **qemu-riscv64**（user-mode） | 翻譯 RISC-V syscall 到 host | 直接跑 RV64 Linux binary，像 WSL 一樣 |

**心法**：要驗證「一條指令的行為對不對」找 spike；要跑 Linux 上的 RISC-V 程式找 qemu-user；要模擬整台機器找 qemu-system。

## 選擇：要 RV32 還是 RV64？

業界幾乎全面走 RV64。教材根扎在 RV32I 的邏輯，但**實際 build 的 toolchain 建議一開始就上 RV64**，這樣後面跑 Linux 相關範例不用重裝。如果你做的是 MCU 等級（RV32），後面可以再補一套 `riscv32-unknown-elf-gcc`。

本教材預設：

```
Baremetal 章節    → riscv64-unknown-elf-*  (沒 OS，跑 spike)
Linux 章節        → riscv64-linux-gnu-*    (有 glibc，跑 qemu)
```

Ch 1–3 會用 baremetal，Ch 5（privileged ISA）之後也會常回到 baremetal。

## 安裝

### Ubuntu 22.04 / 24.04（最推薦）

Ubuntu 24.04 的 `apt` 內建幾乎齊全：

```bash
sudo apt update
sudo apt install -y \
    gcc-riscv64-unknown-elf   \
    gcc-riscv64-linux-gnu     \
    g++-riscv64-linux-gnu     \
    binutils-riscv64-unknown-elf \
    binutils-riscv64-linux-gnu   \
    qemu-system-misc qemu-user   \
    gdb-multiarch                \
    build-essential device-tree-compiler
```

spike 要另外編（apt 版通常過舊）：

```bash
git clone https://github.com/riscv-software-src/riscv-isa-sim
cd riscv-isa-sim
mkdir build && cd build
../configure --prefix=/opt/riscv
make -j$(nproc)
sudo make install
echo 'export PATH=/opt/riscv/bin:$PATH' >> ~/.bashrc
```

配套也需要 proxy kernel（`pk`），讓 spike 能跑 userspace 程式：

```bash
git clone https://github.com/riscv-software-src/riscv-pk
cd riscv-pk
mkdir build && cd build
../configure --prefix=/opt/riscv --host=riscv64-unknown-elf
make -j$(nproc)
sudo make install
```

### macOS (Apple Silicon)

用 Homebrew：

```bash
brew tap riscv-software-src/riscv
brew install riscv-tools qemu
```

`riscv-tools` 會把 toolchain、spike、pk 一起裝進 `/opt/homebrew/opt/riscv-tools/`。

### Arch

```bash
sudo pacman -S riscv64-linux-gnu-gcc qemu-user qemu-system-riscv
yay -S riscv-isa-sim riscv-pk     # AUR
```

### Windows：建議 WSL2

Native Windows 搞 RISC-V toolchain 非常痛。用 WSL2 Ubuntu，照上面 apt 流程跑。**WSL2 跑 qemu-user / spike 都沒問題**，這不像 BPF 需要 Linux kernel 深度功能。

## 驗證安裝

全部要有輸出：

```bash
riscv64-unknown-elf-gcc --version   # baremetal toolchain
riscv64-linux-gnu-gcc --version     # Linux toolchain
qemu-riscv64 --version              # user-mode QEMU
qemu-system-riscv64 --version       # full-system QEMU
spike --help | head -5              # spike
pk 2>&1 | head -3                   # proxy kernel
```

`spike` 沒裝到或 `pk` 找不到是最常見的坑 — 多半是 `PATH` 沒含 `/opt/riscv/bin`。

## 三種方式跑 hello, world

### 方式 1 — spike + pk（純 ISA 模擬）

這是**最接近 spec 的跑法**，之後你想驗證任何指令行為都回到這裡。

```c
// hello.c
#include <stdio.h>
int main(void) {
    printf("hello from spike\n");
    return 0;
}
```

```bash
riscv64-unknown-elf-gcc -o hello hello.c
spike pk hello
# hello from spike
```

背後發生什麼：

```
hello.c
   │ riscv64-unknown-elf-gcc
   ▼
hello (ELF, RV64GC, 靜態連結 newlib)
   │
   ▼
spike = 直接解釋 RISC-V 指令的 C++ simulator
   │   ↑  遇到 ecall 時
   │   │
   ▼   │
   pk (proxy kernel) ← 接住 ecall，翻譯成 host syscall (write, exit...)
```

spike 逐條解釋指令，遇到 `ecall` 就把控制權給 pk，pk 把 RISC-V syscall 翻成 host 的 syscall。所以你看到的 `hello` 其實是 host 的 `write(1, ...)`。

### 方式 2 — qemu-user（跑 Linux binary）

```bash
riscv64-linux-gnu-gcc -o hello_linux hello.c
qemu-riscv64 -L /usr/riscv64-linux-gnu/ ./hello_linux
# hello from spike    ← 字串沒改，但這次是走 Linux syscall
```

`-L` 指定 target 的 sysroot（glibc 在那），QEMU 會自己載入 dynamic linker。

差在哪：這次 binary 是**動態連結 glibc**，QEMU user-mode 在 host 上直接翻譯 RISC-V syscall 成 Linux syscall，**沒有經過 kernel**。速度快，開發週期短。

### 方式 3 — qemu-system（整台虛擬機）

最貼近真機、但最慢也最繁瑣。先跳過，Ch 5（privileged ISA）會用到。基本形式：

```bash
qemu-system-riscv64 \
    -machine virt -cpu rv64 -smp 2 -m 2G -nographic \
    -bios default \
    -kernel ./your-kernel.elf
```

Ch 5 會給完整的 baremetal trap handler 範例，先把前兩種用熟。

## 看編出來的東西：objdump

之後你會天天用 `riscv64-unknown-elf-objdump`。現在先感受一下：

```bash
riscv64-unknown-elf-objdump -d hello | less
```

搜尋 `<main>`，你會看到像：

```
00000000000101a0 <main>:
   101a0: 1141        addi    sp,sp,-16
   101a2: e406        sd      ra,8(sp)
   101a4: e022        sd      s0,0(sp)
   101a6: 0800        addi    s0,sp,16
   101a8: 67c5        lui     a5,0x11
   101aa: 97878793    addi    a5,a5,-1672 # 10978
   101ae: 853e        mv      a0,a5
   101b0: 00000097    auipc   ra,0x0
   101b4: 0a6080e7    jalr    166(ra)
   101b8: 4781        li      a5,0
   101ba: 853e        mv      a0,a5
   ...
```

注意到：

- 地址是 8-byte（RV64），但每條指令**有時 2 byte（`1141`）有時 4 byte（`97878793`）** — 這是 **C 擴充**（壓縮指令）的痕跡。
- `sd`（store doubleword）、`ld` 是 RV64 專屬；RV32 對應的是 `sw` / `lw`。
- `auipc + jalr` 是呼叫 `printf` 的 **PC-relative 跳轉對**。這對指令是 RISC-V 最經典的 idiom，Ch 3 會詳細拆。

不用現在全看懂，但認得「這是一條 RISC-V 指令」就夠。

## debug：qemu + gdb-multiarch

之後章節會常用這招。QEMU 啟動時加 `-g 1234` 開 gdb stub：

```bash
qemu-riscv64 -g 1234 -L /usr/riscv64-linux-gnu/ ./hello_linux &
gdb-multiarch hello_linux
(gdb) target remote localhost:1234
(gdb) b main
(gdb) c
(gdb) layout asm
(gdb) si         # 單步執行一條指令
```

`gdb-multiarch` 會自動認得 target 是 RISC-V。沒有 multiarch 的話也可以用 `riscv64-unknown-elf-gdb`。

## 常見坑

1. **`riscv64-unknown-elf-gcc: command not found`**：Ubuntu 有時候 package 名叫 `gcc-riscv64-unknown-elf`，binary 名是帶 prefix 的，不在 `PATH` 就要自己補。
2. **`spike: command not found`**：通常是 `/opt/riscv/bin` 沒進 PATH，`.bashrc` 那行 export 沒 source。
3. **`qemu-riscv64: Could not open '/lib/ld-linux-riscv64-lp64d.so.1'`**：沒加 `-L sysroot`。Ubuntu 上是 `-L /usr/riscv64-linux-gnu/`。
4. **spike 跑 baremetal ELF 直接 segfault**：baremetal 程式沒 `_start` / 沒 linker script，spike 不知道從哪開始。這種情境要自己寫 startup 與 linker script（Ch 5 會教）或乖乖走 `spike pk` 路線。
5. **apt 的 spike 版本過舊，跑 V 擴充會錯**：2024 後社群在 V / B 擴充上變動很大，建議直接從 source build 最新版。

## 動手練習

1. 用方式 1 跑通 `hello` — 這是你整門課的 baseline 環境。
2. `riscv64-unknown-elf-objdump -d hello | grep -c '^   '` 看看 main 本身大概多少條指令。
3. 故意把 C code 改成 `int main(void) { return 42; }`，重編、用 `echo $?` 看 spike 回傳值是否為 42。
4. 改用 `-O0 -fno-inline`（不壓縮、不優化）重編，再看 objdump 的差別 — 觀察 compiler 預設做了多少事。
5. 加 `-march=rv64imac`（拿掉 F/D，也拿掉 C 之外的 FP）重編一次看差異；注意 `printf` 走哪條路。

## 自我檢核

- [ ] 我知道 spike、qemu-user、qemu-system 各自的定位
- [ ] 我能用 spike + pk 跑一個 hello world
- [ ] 我能用 qemu-user + glibc 跑一個 Linux binary
- [ ] 我能 objdump 出 RISC-V 機器碼並分辨 2-byte vs 4-byte 指令
- [ ] 我知道 `riscv64-unknown-elf-*` 與 `riscv64-linux-gnu-*` 差在哪

下一章我們正式進到 RISC-V 本體 — 從 RV32I 只有 47 條指令的核心設計哲學講起，解釋為什麼 load-store 架構、為什麼沒有 condition code、為什麼這種極簡主義最後反而贏了。

→ [Ch 1 RV32I 心法：為什麼只有 47 條指令](./01-rv32i-mindset.md)
