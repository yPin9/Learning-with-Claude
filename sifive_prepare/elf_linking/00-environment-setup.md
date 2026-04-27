# Ch 0 — 環境搭建：readelf / objdump / nm / ld / lld

> 目標：把 ELF / linking 領域的工具鏈裝好，分清楚它們各自做什麼，並用一個簡單的 hello world 把整個「compile → assemble → link → run」鏈走一遍。

## 工具關係釐清

先把 ELF 生態的工具地圖畫清楚：

| 工具 | 角色 | 你會在哪用 |
|---|---|---|
| **gcc / clang** | 驅動程式，呼叫後面的一切 | day-to-day |
| **cpp** | 預處理（展開 `#include`、macro） | 少直接呼叫 |
| **cc1 / clang -cc1** | compiler proper，產生 `.s` | 幾乎不直接呼叫 |
| **as** | assembler，`.s` → `.o` | objdump 完看為什麼指令長這樣 |
| **ld** / **ld.lld** / **ld.gold** / **mold** | linker，`.o` 們 → 可執行檔或 `.so` | 研究 linker script 時 |
| **readelf** | ELF 結構解析器（**最重要的工具**） | 天天用 |
| **objdump** | 反組譯 + 部分 ELF 資訊 | 看 code + relocation |
| **nm** | 列出 symbol table | 找 undefined reference |
| **strip** | 砍掉 debug / symbol | 發 binary 前 |
| **strings** | 抓出所有 ASCII 字串 | 偵察、reverse engineering |
| **objcopy** | 把 ELF 轉成別的 format | 嵌入式常用 |
| **ldd** | 列出 dynamic binary 依賴的 `.so` | 動態連結 debug |
| **ld.so / ld-linux.so** | 動態連結器本身（kernel 的 interpreter） | 執行時 invisible 但關鍵 |

**心法**：90% 的 ELF 分析用 `readelf` + `objdump` + `nm` 這三把刀就夠。剩下 10% 才動到 linker 本身。

## 安裝

### Ubuntu 22.04 / 24.04

```bash
sudo apt update
sudo apt install -y \
    build-essential \
    gcc-riscv64-unknown-elf \
    gcc-riscv64-linux-gnu \
    binutils-riscv64-unknown-elf \
    binutils-riscv64-linux-gnu \
    lld \
    llvm \
    elfutils
```

**`binutils-riscv64-*`** 裡就有 RISC-V 版的 readelf / objdump / nm / ld。對應 binary 叫 `riscv64-unknown-elf-readelf` 等。

**一般 host 的 readelf / objdump**（不帶 prefix）也能分析 RISC-V ELF —— 只是反組譯時會打印「unknown instruction」。**要看 RISC-V 指令文字必須用帶 prefix 的版本**。

### macOS

macOS 的 `readelf` 比較弱（系統內建的是 Apple 客製）。推薦：

```bash
brew install binutils llvm
# 用 `greadelf` 代替 `readelf`（GNU 版本）
```

### Windows / WSL2

跟前課一樣走 WSL2 Ubuntu 最省心。

## 驗證

每個都要有輸出：

```bash
riscv64-unknown-elf-gcc --version
riscv64-unknown-elf-readelf --version    # binutils 2.38+ 較好
riscv64-unknown-elf-objdump --version
riscv64-unknown-elf-nm --version
riscv64-unknown-elf-ld --version
ld.lld --version                            # LLVM linker
```

## Hello world 的完整分解

這是本課的 baseline 範例。我們用四個步驟分開執行 compile / assemble / link，看每一步產生什麼：

### Step 1 — Source

```c
// hello.c
#include <stdio.h>
int main(void) {
    printf("hello\n");
    return 0;
}
```

### Step 2 — Preprocess

```bash
riscv64-linux-gnu-gcc -E hello.c -o hello.i
wc -l hello.i        # 大概 800 行，全是展開的 stdio.h
```

`.i` 是展開後的 C code。沒人直接看它，但知道它存在。

### Step 3 — Compile（C → assembly）

```bash
riscv64-linux-gnu-gcc -S hello.i -o hello.s
cat hello.s
```

你會看到 `.text` / `.data` / `.rodata` 之類的 `.section` 指令，以及 RISC-V 組語。這是本課 Ch 1 講的「section 概念」的起點。

### Step 4 — Assemble（`.s` → `.o`）

```bash
riscv64-linux-gnu-as hello.s -o hello.o
file hello.o
# ELF 64-bit LSB relocatable, UCB RISC-V, ...
```

**`.o` 是 ELF 的一種：relocatable**。它還沒有固定地址、只是一堆 section + 符號。Ch 1 會深入。

### Step 5 — Link

```bash
riscv64-linux-gnu-gcc hello.o -o hello
file hello
# ELF 64-bit LSB executable, UCB RISC-V, dynamically linked, ...
```

注意 `gcc` 其實呼叫 `ld`（加上 crt 起始檔、libc 等）。想看真實過程加 `-v`：

```bash
riscv64-linux-gnu-gcc -v hello.o -o hello 2>&1 | tail -20
```

會看到 `collect2`（ld 的 driver）被呼叫、一堆 `-l` 跟 `crt*.o`。

## 第一個 readelf：看 ELF 的骨架

```bash
riscv64-linux-gnu-readelf -h hello
```

輸出：

```
ELF Header:
  Magic:   7f 45 4c 46 02 01 01 00 00 00 00 00 00 00 00 00
  Class:                             ELF64
  Data:                              2's complement, little endian
  Version:                           1 (current)
  OS/ABI:                            UNIX - System V
  ABI Version:                       0
  Type:                              DYN (Shared object file)
  Machine:                           RISC-V
  Version:                           0x1
  Entry point address:               0x61c
  Start of program headers:          64 (bytes into file)
  Start of section headers:          14432 (bytes into file)
  Flags:                             0x5, RVC, double-float ABI
  Size of this header:               64 (bytes)
  Size of program headers:           56 (bytes)
  Number of program headers:         9
  Size of section headers:           64 (bytes)
  Number of section headers:         29
  String table index of section headers: 28
```

**每一個欄位都有意義**。Ch 1 會拆。現在注意三件事：

1. **Magic `7f 45 4c 46`** = "\x7fELF"。這是 ELF 的 magic number。
2. **Type: DYN** —— 現代 distro 的 executable 其實是 shared object（PIE），不是 `EXEC`。Ch 11 講。
3. **Entry point: 0x61c** —— 程式從這個地址開始跑（但因為是 PIE，runtime 會 relocate）。

## 第二個 readelf：看所有 section

```bash
riscv64-linux-gnu-readelf -S hello | head -40
```

```
There are 29 section headers, starting at offset 0x3860:

Section Headers:
  [Nr] Name              Type             Address           Offset
       Size              EntSize          Flags  Link  Info  Align
  [ 0]                   NULL             0000000000000000  00000000
       0000000000000000  0000000000000000           0     0     0
  [ 1] .interp           PROGBITS         0000000000000238  00000238
       000000000000001a  0000000000000000   A       0     0     1
  [ 2] .note.gnu.build-id NOTE           0000000000000254  00000254
       0000000000000024  0000000000000000   A       0     0     4
  [ 3] .hash             HASH             0000000000000278  00000278
       000000000000003c  0000000000000004   A       5     0     8
  ...
  [14] .text             PROGBITS         00000000000005c0  000005c0
       0000000000000218  0000000000000000  AX       0     0     4
  [15] .rodata           PROGBITS         00000000000007d8  000007d8
       0000000000000016  0000000000000000   A       0     0     8
  ...
```

**二三十個 section 是正常的**。一個真實的 executable 不只 `.text` + `.data`，還有 dynamic linker、hash、unwind info 等十幾個 section。Ch 1 / Ch 2 會分門別類。

## 第三個 readelf：看 program headers（segment）

```bash
riscv64-linux-gnu-readelf -l hello
```

```
Program Headers:
  Type           Offset    VirtAddr    PhysAddr    FileSiz  MemSiz   Flg Align
  PHDR           0x40      0x40        0x40        0x1f8    0x1f8    R   0x8
  INTERP         0x238     0x238       0x238       0x1a     0x1a     R   0x1
  LOAD           0x0       0x0         0x0         0x7ee    0x7ee    R E 0x1000
  LOAD           0x1000    0x1000      0x1000      0x168    0x260    RW  0x1000
  DYNAMIC        ...
  ...

 Section to Segment mapping:
  Segment Sections...
   00     
   01     .interp
   02     .interp .note.gnu.build-id .hash .gnu.hash .dynsym .dynstr .text .rodata .eh_frame_hdr .eh_frame
   03     .init_array .fini_array .dynamic .got .data .bss
```

**Section 跟 Segment 是兩個 view 的同一份資料**：

- Section：linker 看的，細顆粒度
- Segment：loader / kernel 看的，粗顆粒度（只關心「這塊 load 到 memory 哪裡、RWX 權限」）

Ch 2 深入。

## 第四個：objdump 看實際指令

```bash
riscv64-linux-gnu-objdump -d hello | less
```

搜尋 `<main>`：

```
00000000000006e8 <main>:
 6e8:   1141                    addi    sp,sp,-16
 6ea:   e406                    sd      ra,8(sp)
 6ec:   e022                    sd      s0,0(sp)
 6ee:   0800                    addi    s0,sp,16
 6f0:   00001517                auipc   a0,0x1
 6f4:   0e850513                addi    a0,a0,232 # 17d8
 6f8:   00000097                auipc   ra,0x0
 6fc:   f18080e7                jalr    -232(ra) # 610 <puts@plt>
 ...
```

注意：

- **`printf` 被 compile 成 `puts`**（gcc 優化）
- **`puts@plt`** 表示「呼叫動態連結的 puts」—— Ch 10 講 PLT / GOT
- **`auipc + addi`** 的 PC-relative addressing 模式（`riscv` Ch 3 的 foreshadow 在此）

## 第五個：nm 看 symbol

```bash
riscv64-linux-gnu-nm hello | head -20
```

```
0000000000001fa8 d _DYNAMIC
0000000000002058 d _GLOBAL_OFFSET_TABLE_
                 w _ITM_deregisterTMCloneTable
                 w _ITM_registerTMCloneTable
                 U __libc_start_main@GLIBC_2.27
0000000000000728 T __libc_csu_fini
00000000000006e0 T __libc_csu_init
                 U printf@GLIBC_2.27
0000000000000620 T _start
00000000000006e8 T main
```

第一欄地址、第二欄類型（**T** = text、**D** = data、**U** = undefined、**W** = weak）、第三欄 symbol 名。Ch 3 深入。

## 補充工具：看 dynamic 依賴

```bash
riscv64-linux-gnu-readelf -d hello
# 或（在 target 機器上，這裡用 x86 host 看不到 RISC-V 的動態）
# ldd hello   (只在 host architecture 相同時才能跑)
```

`-d` 印 `.dynamic` section 內容，列出 `NEEDED libc.so.6` 之類的依賴。

## 一個快速備忘錄（Cheat sheet）

複製下面貼在手邊：

```
readelf -h file         # ELF header
readelf -S file         # section headers
readelf -l file         # program headers (segments)
readelf -s file         # .symtab
readelf -d file         # .dynamic
readelf -r file         # relocations
readelf --hex-dump=.rodata file   # dump section 內容
readelf -n file         # notes

objdump -d file         # disassemble
objdump -D file         # disassemble all sections (不只 .text)
objdump -r file         # relocation entries
objdump -t file         # symbol table
objdump -s file         # 全部 section hex dump
objdump -M no-aliases -d file  # 不展 pseudo（看真實指令）

nm file                 # symbol list
nm -C file              # C++ demangle
nm -D file              # dynamic symbols only
nm --defined-only file

file hello              # 認 binary 類型
strings hello           # 所有可讀字串

# RISC-V 版的工具有前綴：
riscv64-linux-gnu-readelf -h hello
riscv64-unknown-elf-readelf -h baremetal.elf
```

**把這張表列印出來**。每章會反覆用。

## 常見坑

1. **用錯 prefix 的 objdump**：host `objdump` 會說「unknown instruction」；要用 `riscv64-*-objdump`。
2. **`file` 顯示 "not stripped"**：代表還有 debug symbol。`strip hello` 後再看一次。
3. **`readelf` vs `objdump` 兩邊顯示 section address 不同**：前者印 virtual address、後者有些模式印 file offset。注意分辨。
4. **混用 `riscv64-unknown-elf-*`（baremetal）跟 `riscv64-linux-gnu-*`（Linux）的產物**：兩者的 ABI / libc 不同，`.o` 互連會錯。教材每章清楚標示用哪個。
5. **ldd 對 cross-compiled binary 無效**：ldd 只能分析 host-native binary。要看 cross-compiled ELF 的依賴用 `readelf -d`。

## 動手練習

1. 用 `riscv64-linux-gnu-gcc` 編 `hello.c`、跑一次 `-E -S -c -o` 分別產物，用 `file` 確認每個產物類型。
2. 用 `readelf -h` 查你的 hello，找出 entry point 位址，再用 `objdump -d` 搜尋這個地址看是哪個 symbol。答案是 `_start` 不是 `main`。
3. `readelf -S` 印出所有 section，從 [0] 數到最後一個。認出 `.text` / `.data` / `.rodata` / `.bss` 的號碼。
4. 用 `objdump -r hello.o`（對 `.o` 不是 linked binary）看 relocation entries。這是 Ch 5 的預習。
5. 寫兩個 `.c`（`a.c` 定義 `int x = 42;`、`b.c` 宣告 `extern int x;` 並 `printf`），分別 `-c` 產 `.o`，再 link。用 `nm a.o b.o` 對比 `x` 的符號狀態。

## 自我檢核

- [ ] 我能列出 ELF 分析的三把刀（readelf / objdump / nm）各自適合的場景
- [ ] 我能解釋 `.o` 跟 executable ELF 的 Type 差異
- [ ] 我能用 `readelf -h` 認出 ELF magic、machine、entry point
- [ ] 我能用 `objdump -d` 反組譯 RISC-V ELF
- [ ] 我知道 `ldd` 跟 `readelf -d` 的適用差異

下一章進入 ELF 的核心 —— header / section / segment 三層結構。這章打穿了，後面 15 章都是補細節。

→ [Ch 1 ELF 三層結構：header / section / segment](./01-elf-three-layers.md)
