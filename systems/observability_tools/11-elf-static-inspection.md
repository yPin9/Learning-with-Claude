# Ch 11 — ELF 靜態檢視（nm/objdump/readelf）

> **目標**：掌握靜態分析——不執行程式，直接看 ELF 二進位的結構：readelf（ELF 結構/header/段）、nm（符號表/函式）、objdump（反組譯/段內容）、strings（字串）。理解 ELF 的組成（header/段/符號/動態連結資訊），你能在「不執行程式」的情況下看它的全貌（用哪些 library、有哪些函式、寫死的字串）。這補上動態觀察之外的視角——逆向工程、分析可疑程式、debug 連結問題都靠它。

> **環境**：Linux，binutils（nm/objdump/readelf/strings，Ch 0 已裝）。

## 為什麼需要靜態分析？

前面的工具都是動態的（執行程式看行為）。但有時你想「不執行就看程式」——分析可疑程式（不敢執行）、看一個 binary 用了哪些 library（debug 連結問題）、找寫死的字串/密碼、理解程式結構（逆向工程）。這是**靜態分析**——直接看 ELF 二進位本身。

ELF（Executable and Linkable Format）是 Linux 執行檔/library/object 檔的格式。理解它的結構（header/段/符號表/動態連結資訊），配上工具（readelf/nm/objdump/strings），你能在不執行的情況下看程式的全貌。這補上了動態觀察之外的視角（Ch 1 的 static vs dynamic）。它也連到 Ch 6 的動態連結（PLT/GOT 在 ELF 裡）、Ch 4 的 mini-strace（syscall 號）、和逆向工程/資安分析。

## 先建立直覺:ELF 是程式的「藍圖」

```
ELF 檔案 = 程式的「藍圖」（不執行也能看）

  動態觀察：看程式「執行時做什麼」（strace/ltrace）
  靜態觀察：看程式「藍圖長怎樣」（不執行）
        │
  ELF 的結構（藍圖的各部分）：
    ELF header：基本資訊（架構、類型、入口點）
    段（sections）：
      .text     程式碼（指令）
      .data     已初始化的全域變數
      .bss      未初始化的全域變數
      .rodata   唯讀資料（字串常數等）
      .symtab   符號表（函式/變數名）
      .dynamic  動態連結資訊（依賴的 library）
      .plt/.got 動態連結跳轉表（Ch 6）
        │
  工具對應：
    readelf：看 ELF 結構（header/段/動態資訊）
    nm：看符號表（有哪些函式/變數）
    objdump：反組譯（看組合語言）+ 段內容
    strings：找可讀字串
        │
  → 靜態看「程式可能做什麼」（全貌）
    不執行就能分析（安全、看結構）
```

關鍵心智：ELF 是程式的「藍圖」——靜態分析不執行程式，直接看這個藍圖的各部分（header/.text 程式碼/.data 資料/.symtab 符號/.dynamic 動態連結）。工具：readelf（ELF 結構）、nm（符號表）、objdump（反組譯）、strings（字串）。它看「程式可能做什麼」（全貌、不執行），補上動態觀察（執行時行為）。

> ELF 的動態連結部分（.plt/.got/.dynamic）連到 Ch 6 的動態連結。如果對 PLT/GOT 不熟，回看 [Ch 6](./06-ltrace-and-dynamic-linking.md)。靜態 vs 動態的互補見 [Ch 1](./01-observation-tools-overview.md)。

## readelf:看 ELF 結構

```bash
cd ~/obslab
cat > prog.c <<'EOF'
#include <stdio.h>
const char *secret = "password123";
int helper(int x) { return x * 2; }
int main() { printf("%d\n", helper(21)); return 0; }
EOF
gcc -g -o prog prog.c

# === ELF header（基本資訊）===
readelf -h prog
# Class: ELF64                  ← 64 位元
# Type: DYN (Position-Independent Executable)   ← PIE（現代預設）
# Machine: Advanced Micro Devices X86-64        ← 架構
# Entry point address: 0x1060                   ← 程式入口

# === 段（sections）===
readelf -S prog
# [Nr] Name      Type     Address  ...
# .text    PROGBITS ...    ← 程式碼
# .data    PROGBITS ...    ← 已初始化資料
# .rodata  PROGBITS ...    ← 唯讀（字串常數）
# .symtab  SYMTAB   ...    ← 符號表

# === 動態連結資訊（依賴哪些 library）===
readelf -d prog
# (NEEDED) Shared library: [libc.so.6]   ← 依賴 glibc（= ldd 的源頭）
readelf --dyn-syms prog | head            # 動態符號（外部函式如 printf）

# === 看程式依賴（ldd 的底層）===
ldd prog
# libc.so.6 => ...
```

> **readelf 看 ELF 的「結構藍圖」——header（架構/類型）、段（.text/.data）、動態連結（依賴哪些 library）**。`readelf -h`（ELF header）看基本資訊：Class（32/64 位元）、Type（**現代是 DYN/PIE**——位置無關執行檔，安全機制 ASLR 需要的，不是傳統的固定位址 EXEC）、Machine（架構 x86-64/ARM）、Entry point（程式入口位址）。`readelf -S`（段）看 ELF 的各部分：`.text`（程式碼）、`.data`（已初始化全域變數）、`.bss`（未初始化全域變數——不佔檔案空間，只記大小）、`.rodata`（唯讀資料，字串常數在這）、`.symtab`（符號表）。`readelf -d`（動態連結資訊）看「依賴哪些 library」——`(NEEDED) libc.so.6` 就是 `ldd` 顯示的依賴的源頭（ldd 讀這個）。理解 ELF 結構讓你能 debug 連結問題（「為什麼說找不到某個 library」→ readelf -d 看它依賴什麼）、看程式的架構（32 vs 64 位元、x86 vs ARM——跨平台時重要）、理解現代安全機制（PIE/ASLR）。readelf 是 ELF 結構的權威檢視工具——當你想知道「這個 binary 是什麼、依賴什麼、結構怎樣」，用它。這也是逆向工程和資安分析的第一步——先看 ELF 結構，再深入符號和反組譯。

## nm / strings:符號與字串

```bash
# === nm：看符號表（有哪些函式/變數）===
nm prog
# 0000000000001149 T main        ← T = 程式碼段的全域符號（函式）
# 0000000000001139 T helper      ← 你定義的 helper 函式
# ...                 U printf    ← U = undefined（外部，要動態連結）
#                     D secret    ← D = data 段（全域變數）
# 符號類型：T/t=text(函式), D/d=data, B/b=bss, U=undefined(外部)
nm -D prog                       # 只看動態符號（外部函式）
nm --defined-only prog           # 只看這個程式定義的

# 找特定函式
nm prog | grep helper

# === strings：找可讀字串 ===
strings prog | head
# password123                    ← 寫死的字串！（資安分析常找這個）
# /lib64/ld-linux-x86-64.so.2
# libc.so.6
# %d
# → strings 找出 binary 裡的可讀字串
#   用途：找寫死的密碼/URL/錯誤訊息、判斷程式功能、惡意軟體分析

# 找特定字串（如可疑的 URL/密碼）
strings prog | grep -iE 'password|http|key'
# password123    ← 找到寫死的密碼（資安問題！）
```

> **nm 看符號（程式有哪些函式）、strings 找字串（寫死的密碼/URL）——資安分析和逆向工程的入門工具**。**nm** 看符號表——程式有哪些函式和變數：`T`/`t`（text 段，函式——大寫全域、小寫 local）、`D`/`d`（data，已初始化變數）、`B`/`b`（bss，未初始化）、`U`（undefined，外部符號如 printf，要動態連結）。這讓你看「程式定義了哪些函式」（理解結構）、「依賴哪些外部函式」（U 的）。**注意**：`gcc -s`（strip）或 release 編譯會移除符號表，nm 就看不到函式名（逆向工程時的障礙——strip 過的 binary 只有位址沒有名字）。**strings** 找 binary 裡的可讀字串——這是**資安分析的招牌**：找寫死的密碼/API key（`strings prog | grep -i password` 找到 "password123" = 安全問題！密碼不該寫死在程式裡）、找 URL/IP（惡意軟體連的 C2 伺服器）、看錯誤訊息和功能線索（判斷程式做什麼）。strings 對分析「不知道是什麼的 binary」（可疑程式、忘了原始碼的舊程式）很有用——不執行就能看它「寫死了什麼」。這些是逆向工程和惡意軟體分析的入門——靜態看符號（功能結構）和字串（寫死的資料），不執行就能初步理解一個 binary。當然完整逆向要 objdump 反組譯（下節）和更專業的工具（Ghidra/IDA），但 nm/strings 是快速的第一步。

## objdump:反組譯

```bash
# === objdump：反組譯（看組合語言）===
objdump -d prog | grep -A10 '<main>:'
# 0000000000001149 <main>:
#   1149: push %rbp
#   114a: mov %rsp,%rbp
#   114d: mov $0x15,%edi        ← 0x15 = 21（傳給 helper 的參數）
#   1152: call 1139 <helper>    ← 呼叫 helper
#   ...
# → 看到 main 的組合語言（不執行就知道它做什麼）

# 反組譯特定函式
objdump -d prog | awk '/<helper>:/,/^$/'
# helper 的組合語言：x*2 → 可能是 shl（左移）或 add

# === 看段的內容 ===
objdump -s -j .rodata prog       # .rodata 段的內容（字串常數）
objdump -s -j .data prog         # .data 段（已初始化資料）

# === 看 PLT（動態連結，Ch 6）===
objdump -d prog | grep -A3 'printf@plt'
# printf@plt 的 PLT 條目（跳轉到 GOT）

# === 含原始碼對照（要 -g 編譯）===
objdump -S prog                  # 反組譯 + 對照原始碼行（debug 用）
```

> **objdump 反組譯看「程式的組合語言」——不執行就知道它做什麼，是逆向工程和理解編譯結果的核心**。`objdump -d`（反組譯）把機器碼翻成組合語言——你能看到每個函式的實際指令。這有幾個用途：(1) **逆向工程**——沒有原始碼時，反組譯看程式邏輯（`call <helper>` 看呼叫關係、看參數、看控制流）；(2) **理解編譯結果**——看你的 C 程式編譯成什麼（如 `x*2` 編譯成左移 `shl` 還是乘法、優化做了什麼）；(3) **debug 優化問題**——`-O2` 優化過的程式行為怪時，反組譯看實際的指令（優化可能重排、消除、inline）；(4) **看 PLT/GOT**（Ch 6 的動態連結在組合語言層）。`objdump -S`（要 `-g` 編譯）反組譯**並對照原始碼行**——這是「看每行 C 編譯成什麼指令」的利器（debug、學編譯器、優化分析）。`objdump -s -j .rodata`（看某段的內容）配合 strings 看資料。反組譯需要一點組合語言基礎（x86-64 的暫存器和指令），但即使不深入，看「呼叫了哪些函式、大致的控制流」也有價值。objdump 是靜態分析最深入的工具——從結構（readelf）、符號（nm）、字串（strings）到實際指令（objdump），組成完整的靜態分析。完整的逆向工程會用更專業的工具（Ghidra/IDA，有反編譯成 C 的能力），但 objdump 是基礎，理解它你就懂了反組譯的核心。

## 靜態 vs 動態:互補的分析

```bash
# 靜態和動態結合分析一個程式（Ch 1 的互補）
cd ~/obslab

# 靜態：不執行，看全貌
file prog                        # 這是什麼檔案（ELF 64-bit PIE...）
readelf -d prog | grep NEEDED    # 依賴哪些 library
nm -D prog                       # 用哪些外部函式
strings prog | grep -iE 'http|password|key'   # 寫死的敏感資料

# 動態：執行，看實際行為
strace -e trace=network ./prog   # 實際連哪些網路（靜態看不到「連哪」，只看到「會連」）
ltrace ./prog                    # 實際呼叫哪些 library 函式

# → 互補：
#   靜態：「程式『可能』做什麼」（依賴 libcurl → 可能連網）
#   動態：「程式『實際』做什麼」（connect 到 1.2.3.4）
#   分析可疑程式：先靜態（安全，不執行）→ 隔離環境動態（看實際）

# 分析可疑程式的流程（資安）：
# 1. file/readelf：是什麼、架構、依賴
# 2. strings：寫死的 URL/IP/命令（線索）
# 3. nm/objdump：函式、邏輯（它能做什麼）
# 4.（隔離環境）strace/ltrace：實際行為（連哪、做什麼）
```

> **分析可疑程式的標準流程是「先靜態（安全，不執行）→ 再隔離環境動態」——靜態看可能做什麼，動態看實際做什麼**。靜態和動態分析互補（Ch 1）：**靜態**看「程式**可能**做什麼」（不執行，安全）——`file`/`readelf`（是什麼、架構、依賴）、`strings`（寫死的 URL/IP/密碼/命令，這是線索的金礦）、`nm`/`objdump`（有哪些函式、邏輯）。**動態**看「程式**實際**做什麼」（執行）——strace（實際連哪個 IP、開哪些檔案）、ltrace（實際呼叫哪些函式）。**分析可疑程式（惡意軟體/不明 binary）的流程**：(1) **先靜態**（不執行——因為執行可疑程式有風險）：file/readelf 看是什麼、strings 找寫死的線索（C2 伺服器的 URL/IP、執行的命令）、objdump 看邏輯；(2) **再在隔離環境動態**（沙箱/VM，避免感染）：strace/ltrace 看實際行為。這是資安分析的標準方法——靜態先建立「它可能做什麼」的假設（安全），動態驗證「它實際做什麼」（在隔離環境）。對一般 debug，靜態也有用——「為什麼這個 binary 依賴某個 library」（readelf -d）、「它有沒有某個函式」（nm）、「它寫死了什麼設定」（strings）。理解靜態 vs 動態的互補，你的分析視角就完整了——不只能看執行時行為（前面的章節），也能看不執行時的結構。這是本課從「動態觀察」擴展到「完整分析」的一塊。

## 動手練習

1. readelf 結構：對一個程式用 `readelf -h/-S/-d`，看 header、段、依賴的 library

2. nm 符號：用 nm 看程式的函式（T）和外部依賴（U），對比 strip 過的（看不到名字）

3. strings 找線索：用 `strings | grep` 找程式寫死的字串（試找密碼/URL），理解資安用途

4. objdump 反組譯：反組譯一個簡單函式，看它的組合語言，用 `-S` 對照原始碼

5. 靜態+動態：對一個程式先靜態分析（看可能做什麼）再 strace（看實際做什麼），理解互補

## 本章重點整理

- 靜態分析不執行程式，看 ELF 二進位的結構（藍圖）；補上動態觀察（執行行為）的另一視角
- readelf 看 ELF 結構：header（架構/PIE）、段（.text/.data/.rodata）、動態連結（依賴哪些 library，ldd 源頭）
- nm 看符號（函式 T、外部 U）；strings 找可讀字串（寫死的密碼/URL，資安分析招牌）；strip 移除符號
- objdump 反組譯看組合語言（逆向、理解編譯、debug 優化）；-S 對照原始碼
- 靜態（可能做什麼，不執行安全）vs 動態（實際做什麼）互補；分析可疑程式：先靜態再隔離環境動態

## 自我檢核

- [ ] 知道 ELF 的結構（header/段/符號/動態連結），會用 readelf 看
- [ ] 會用 nm 看符號、strings 找字串，理解資安分析用途
- [ ] 會用 objdump 反組譯，知道它的用途（逆向/理解編譯/debug 優化）
- [ ] 理解靜態 vs 動態分析的互補
- [ ] 知道分析可疑程式的流程（先靜態後動態）

## 延伸閱讀

### 文章

- **[ELF 格式詳解](https://www.intezer.com/blog/research/executable-and-linkable-format-101-part1-sections-and-segments/)** — Intezer
  - **這篇說什麼**：ELF 的段/符號/動態連結的詳解（資安角度）
  - **讀哪裡**：Part 1-2
  - **為什麼值得讀**：本章 ELF 結構的權威深入版

- **[Linux binary analysis](https://blog.k3170makan.com/2018/09/introduction-to-elf-format-elf-header.html)** — k3170makan
  - **這篇說什麼**：用 readelf/objdump 分析 ELF 的系列
  - **為什麼值得讀**：本章工具的實戰擴充

### 書籍

- **《Learning Linux Binary Analysis》— Ryan O'Neill**
  - **讀哪幾章**：ELF 格式、靜態分析那幾章
  - **這本書的定位**：Linux 二進位分析的權威（逆向工程/資安）
  - **前提**：本章 + 一點組合語言

### 官方文件

- **[ELF 規格](https://refspecs.linuxfoundation.org/elf/elf.pdf)** — Linux Foundation
  - **讀哪裡**：ELF header、sections 那幾節
  - **為什麼值得讀**：ELF 格式的權威標準

Part 4（ELF 靜態分析）到此完成。接下來 Part 5 進入現代 tracing——perf（效能 profiling）、ftrace（kernel 函式 trace）、bpftrace（可程式化 trace）。從「看行為/狀態」進到「看效能和 kernel 內部」。

→ [Ch 12 perf 基礎](./12-perf-fundamentals.md)
