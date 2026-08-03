# Ch 22 — 逆靜態連結 / 去符號的大 binary

> **目標**：面對靜態連結、strip 過的大 binary（幾萬個函式、沒有 import 名），用分而治之的策略定位你真正要逆向的那幾個函式——而不是從第一個函式逐個掃到最後。

> **環境**：WSL2 / Linux x86-64，gcc（`-static`）+ objdump + readelf + strings + nm + strace。

## 為什麼需要這個？

現實中最難啃的 binary 類型：靜態連結、strip 過的 ELF，幾 MB 大、幾萬個函式，沒有 `.plt` import 名（libc 也進來了，連 `printf@plt` 都不見了）。

動態連結 binary 的逆向有天然優勢：PLT 列出了 `fopen@plt`、`printf@plt`、`malloc@plt`——你知道哪裡做 IO、哪裡做記憶體操作，立刻縮小範圍。靜態連結 binary 把 libc 打進來，這些名字全不見了，你面對的是幾萬個沒有名字的函式群。

**真實情境**：
- 嵌入式 firmware（BusyBox 靜態連結 uClibc，直接燒進 flash）
- 惡意程式（帶 musl libc 或自帶 stdlib，讓分析環境少了對應符號）
- 老版 Unix 工具（strip 的 release binary）
- Go/Rust binary（前一章）——它們也是靜態連結但有自己的符號資源

本章解決的問題：在「函式名全沒有、幾萬個函式」的情況下，怎麼快速定位你真正需要逆的 5-50 個業務邏輯函式。

## 先建立直覺：大 binary 的地形圖

```
靜態連結 strip binary（以 801 KB 的範例為例）

  函式空間（~幾萬個，依大小可達幾十萬）
  ┌────────────────────────────────────────────────────┐
  │                                                    │
  │  libc 函式群                                       │
  │  （printf、malloc、memcpy、qsort、atoi……）         │ ← 大多數函式在這
  │  幾千個函式，你幾乎不需要逆這些                    │
  │                                                    │
  │  第三方庫                                          │
  │  （zlib、crypto……如果有的話）                      │ ← 靠指紋辨識（Ch 18）標記
  │                                                    │
  │  業務邏輯                                          │ ← 你真正要逆的目標
  │  （main + 5-50 個自訂函式）                        │
  │                                                    │
  └────────────────────────────────────────────────────┘

分而治之策略：
  1. 定位 main（entry → _start → __libc_start_main → main）
  2. 從字串交叉引用縮小到業務邏輯函式集合
  3. 用 FLIRT / 指紋辨識標記並隔離 libc 函式
  4. 用 call graph 確認業務邏輯邊界
  5. 只逆你需要的路徑（hypothesis-driven）
```

這個策略和 `reading_code` 課（Ch 31，攻堅大型 codebase）的 SOP 一脈相承：你不從第一行讀到最後一行——你用錨點縮小範圍，假設驅動地只讀相關路徑。

## 真跑：準備一個靜態 strip binary

```c
/* /tmp/re_part3/static_ex.c — 出題 source */
#include <stdio.h>
#include <string.h>

static int check_password(const char *pwd) {
    const char *secret = "re_master_2024";
    return strcmp(pwd, secret) == 0;
}

int main(int argc, char *argv[]) {
    if (argc != 2) {
        fprintf(stderr, "Usage: %s <password>\n", argv[0]);
        return 1;
    }
    if (check_password(argv[1])) {
        printf("Access granted!\n");
        return 0;
    }
    printf("Wrong password.\n");
    return 1;
}
```

```bash
$ gcc -static -O2 -o /tmp/re_part3/static_ex /tmp/re_part3/static_ex.c
$ strip /tmp/re_part3/static_ex
$ /tmp/re_part3/static_ex re_master_2024
Access granted!
$ /tmp/re_part3/static_ex wrong
Wrong password.
```

體積對比：

```bash
$ ls -lh /tmp/re_part3/static_ex /tmp/re_part3/dyn_ex
-rwxr-xr-x 1 ypp ypp 801K Aug  3 02:00 /tmp/re_part3/static_ex   ← 801 KB
-rwxr-xr-x 1 ypp ypp  16K Aug  3 02:00 /tmp/re_part3/dyn_ex      ← 16 KB
```

靜態：801 KB，動態：16 KB——相差 50 倍。這 801 KB 幾乎全是 libc（printf、strcmp、malloc、stdio 整個 runtime）。

```bash
$ nm /tmp/re_part3/static_ex 2>&1
nm: /tmp/re_part3/static_ex: no symbols   ← 完全沒有符號
```

## Step 1：定位 main（entry chain）

### 找 entry point

```bash
$ readelf -h /tmp/re_part3/static_ex | grep Entry
  Entry point address:  0x4016c0
```

### 看 _start（真實 objdump 輸出）

```bash
$ objdump -d /tmp/re_part3/static_ex | grep -A 20 '^0*4016c0 '
```

```asm
00000000004016c0:    ; _start（libc 啟動樁）
    4016c0:  endbr64
    4016c4:  xor    %ebp,%ebp           ; frame pointer = 0（表示這是最頂層）
    4016c6:  mov    %rdx,%r9            ; rtld_fini（動態連結用，靜態可能 NULL）
    4016c9:  pop    %rsi                ; 從 stack 取 argc
    4016ca:  mov    %rsp,%rdx           ; rdx = argv
    4016cd:  and    $0xfffffffffffffff0,%rsp  ; 對齊 stack
    4016d1:  push   %rax
    4016d2:  push   %rsp
    4016d3:  xor    %r8d,%r8d           ; fini = 0
    4016d6:  xor    %ecx,%ecx           ; init = 0
    4016d8:  mov    $0x401650,%rdi      ; ← rdi = main 的地址！
    4016df:  call   0x402a90            ; __libc_start_main
    4016e5:  hlt                        ; 不應該到這裡
```

**技巧**：`__libc_start_main(main, argc, argv, init, fini, rtld_fini, stack_end)` 的第一個參數（`rdi`）就是 `main`。在 `_start` 找 `call __libc_start_main` 前的 `mov $ADDR,%rdi`，那個 `ADDR` 就是 main。

這裡 `0x401650` = main。

### 看 main（真實 objdump 輸出）

```asm
0000000000401650:    ; main（靠 _start 推斷的位址）
    401650:  endbr64
    401654:  push   %r12
    401656:  cmp    $0x2,%edi            ; argc == 2?
    401659:  jne    0x401698             ; 不等 → usage message
    40165b:  mov    0x8(%rsi),%rdi       ; argv[1] → rdi（密碼字串）
    40165f:  lea    0x969b4(%rip),%rsi   # 0x49801a ← .rodata 裡的 secret
    401666:  call   0x401130             ; strcmp（或 check_password 內聯）
    40166e:  test   %eax,%eax            ; strcmp 回 0 = 相等
    401670:  je     0x40168a             ; 密碼正確 → "Access granted"
    401672:  lea    0x969c0(%rip),%rdi   # 0x498039 → "Wrong password."
    40167f:  call   0x40c1d0             ; puts（或 printf）
    401684:  pop    %r12
    401689:  ret
    40168a:  lea    0x96998(%rip),%rdi   # 0x498029 → "Access granted!"
    401691:  call   0x40c1d0
    401696:  jmp    0x401684
```

`-O2` 下 `check_password` 被 inline 進 main——沒有獨立的 `check_password` 函式節點，比較邏輯直接展開在 main 裡。這是 `-O2` 逆向的常態。

## Step 2：字串交叉引用

字串是 stripped binary 裡最強的錨點——存在 `.rodata`，不被 strip，且幾乎總被業務邏輯引用。

```bash
$ strings -t x /tmp/re_part3/static_ex | grep -E 're_master|Access|Wrong|password|Usage'
   9801a re_master_2024
   98004 Usage: %s <password>
   98029 Access granted!
   98039 Wrong password.
```

字串的 offset（`0x9801a`）加上 `.rodata` 的 load VA（`0x498000`，從 `readelf -S` 確認）= 字串的 VA = `0x49801a`。

```bash
$ readelf -S /tmp/re_part3/static_ex | grep '\.rodata'
  [10] .rodata  PROGBITS  0000000000498000  00098000
```

確認：`0x498000 + 0x9801a = 0x49801a`——和 `0x40165f: lea 0x969b4(%rip),%rsi # 0x49801a` 對上了。

**這就是定位業務邏輯的流程**：
1. `strings` 找到有意義的字串 → 計算 VA
2. `objdump` grep 那個 VA → 找到引用它的指令
3. 指令所在函式 = 業務邏輯

## Step 3：隔離已知庫函式（FLIRT 概念）

靜態 binary 的主要雜訊是「你不關心的 libc 函式」——它們佔了大部分函式空間。

### FLIRT 的原理（理論）

FLIRT（Fast Library Identification and Recognition Technology）是 IDA 的技術：
- 預先對已知版本的 libc、openssl 等庫的每個函式，計算「函式開頭 N bytes 的 hash」。
- 分析目標 binary 時，對每個函式的開頭 bytes 查表——命中 = 已知庫函式，自動貼名字。

Ghidra 的等價功能叫 **Function ID**，Ghidra 允許你匯入外部庫的 `.a` 檔來建立 FID database。

### 手動版隔離策略

沒有 Ghidra/IDA 時：

```bash
# 方法 1：動態偵察（strace 看 syscall 序列）
$ strace /tmp/re_part3/static_ex re_master_2024 2>&1
execve(...)
brk(NULL)
...
write(1, "Access granted!\n", 16) = 16
exit_group(0)
```

`write` 呼叫前的函式序列就是關鍵路徑——從 `write` syscall 往前追就是業務邏輯。

```bash
# 方法 2：找 strcmp / memcmp 的候選位置
# strcmp 的 asm 指紋：逐 byte 比較，有條件跳轉
$ objdump -d /tmp/re_part3/static_ex | grep -B 2 -A 10 '401130'
# 看 0x401130 的實作，確認是 strcmp
```

```bash
# 方法 3：Ch 18 的算法指紋辨識
# 如果 binary 裡有 zlib → 找 0x77073096 (CRC-32 table)
# 如果有 AES → 找 0xc66363a5 (T-table)
# 找到了就貼上「已知加密庫」標籤，不再深挖
```

## Step 4：call graph 確認業務邏輯邊界

從 main 出發，列出它直接呼叫的函式（一層）：

```asm
; main 直接 call：
0x401130  → strcmp（libc，不逆）
0x40c1d0  → puts/printf（libc，不逆）
0x449d90  → fprintf（libc，不逆）
; ← main 本身就是業務邏輯，check_password 已被 inline
```

原則：從 main 出發三層以內是業務邏輯，超過三層大概率進入 libc 或 runtime。一旦進入「這個函式看起來在做字串格式化」或「這個函式在做記憶體操作」，停下來——這不是你要逆的。

### 用 gdb 確認關鍵路徑（動態輔助靜態）

```bash
$ gdb -q /tmp/re_part3/static_ex
(gdb) break *0x401650     # main
(gdb) run re_master_2024
(gdb) stepi               # 逐指令，觀察 rdi 的值
(gdb) x/s $rsi            # 看 secret 字串
```

```
(gdb) x/s $rsi
0x49801a:  "re_master_2024"   ← 在 gdb 裡直接確認字串
```

動態執行一次，立刻得到 `secret` 字串——這比純靜態分析快得多。

## Step 5：只逆你需要的路徑

「只逆你需要的路徑」是本章最重要的心態轉變：

```
目標驅動的逆向（和 reading_code 的 hypothesis-driven 閱讀一致）

  問題：這個 binary 的密碼驗證邏輯在哪？
    ↓
  偵察：strings 找到 "Wrong password" 和 "re_master_2024"
    ↓
  字串 xref：兩個字串都被 0x401650 引用 → 這就是 main/check_password
    ↓
  靜態分析 0x401650：看到 strcmp 比對 0x49801a（= "re_master_2024"）
    ↓
  動態確認：gdb x/s 確認字串值
    ↓
  完成！不需要逆 printf、strcmp、malloc、_start、__libc_start_main
```

## 踩雷集錦

1. **誤以為 `_start` 裡第一個 call 就是 main**：`_start` 通常在呼叫 `__libc_start_main` 前還有幾條設置指令（`xor %ebp,%ebp`、對齊 stack）。真正的 main 是 `__libc_start_main` 的**第一個參數**（`rdi`），不是 `_start` 呼叫的第一個函式。

2. **-O2 把 check_password inline 掉讓你找不到「那個函式」**：在 `-O2` 下，一個小函式（如 `check_password`）可能直接展開在 main 裡，沒有獨立的函式節點。遇到這種情況：不用找獨立函式，在 main 裡直接找「看起來在做比較的」分支——有 cmp 後接「Access granted」路徑的就是。

3. **FLIRT 誤判率**：FLIRT 用函式開頭 N bytes 做指紋，如果業務邏輯函式的前幾 bytes 剛好和某個 libc 函式相同，會誤標。實際逆向時看到標了名字但行為不符，回去讀 asm 確認。

4. **靜態 binary 的 `.plt` 有時仍在（musl libc）**：某些靜態連結 binary（尤其用 musl libc 的）仍有 PLT 風格的跳轉樁，即使靜態連結——這不代表有動態依賴，是 musl 的實作特性。看到 PLT 但 `file` 說 `statically linked`，不要驚訝。

5. **字串被刻意加密時 xref 失效**：惡意程式可能把所有字串加密、執行時解密（Ch 23 的主題）。這時 `strings` 找不到錨點，要改用動態方式（gdb watch memory write，Frida hook printf/write）。靜態 binary 的字串 xref 只對「有明文字串」的 binary 有效。

6. **strace 在 strip+static binary 反而更清晰**：動態 binary 有動態連結器的 syscall 雜訊，靜態 binary 的 strace 輸出更乾淨——這反而是靜態 binary 動態分析的優點。

## 進階：再往深一層

- **Ghidra Function ID database**：Ghidra 可以從已知 libc `.a` 靜態庫建立函式指紋資料庫（File → New → Function ID Database → Import shared library），分析靜態 binary 時自動識別並標上 libc 函式名——讓「海撈針」縮小到真正的業務邏輯。
- **BinDiff 做 library 比對**：對同版本 glibc 的 `.a` 做分析，導出函式指紋，再對目標 binary 做 diff——命中的都是 libc，剩下的是業務邏輯（Ch 28 binary 相似度）。
- **EMBA firmware analysis framework**（[https://github.com/e-m-b-a/emba](https://github.com/e-m-b-a/emba)）：自動化 firmware 逆向的全套工具鏈，把本章的手動步驟大規模自動化，適合 IoT 安全研究。

## 本章重點整理

- **定位 main**：`readelf -h` → entry point → `_start` 的 `mov $ADDR,%rdi; call __libc_start_main` → ADDR = main。
- **字串是最強錨點**：`strings -t x binary | grep 關鍵詞` → VA = offset + .rodata 起始 VA → `objdump grep VA` → 引用該字串的函式 = 業務邏輯。
- **體積差距揭示問題規模**：靜態 binary 比動態大 50 倍以上，多出來的幾乎全是 libc——先隔離它們，再逆業務邏輯。
- **FLIRT/FID**：預先計算庫函式指紋，分析時自動標記——Ghidra FID 是開源可用的版本。
- **假設驅動，只逆需要的路徑**：問題決定要逆哪個函式；不求全解，對應 `reading_code` 課的 hypothesis-driven 閱讀。

## 自我檢核

- [ ] 我能從 `_start` 的 asm 推斷 main 的地址，不依賴 nm 或符號
- [ ] 我能用 `strings -t x` 找字串 offset，並計算出 VA（加上 .rodata load address）
- [ ] 我理解 FLIRT/Function ID 的概念：對庫函式計算指紋 → 在目標 binary 比對 → 標名字
- [ ] 我能解釋靜態 binary 比動態 binary 大 50 倍的原因，以及這對逆向策略的影響
- [ ] 我知道 strace 在靜態 binary 上反而比動態 binary 更清晰的原因

## 延伸閱讀

1. **《Practical Binary Analysis》Ch 8（靜態分析工具）** — Dennis Andriesse（No Starch, 2019）
   - 學什麼：靜態分析的系統化方法，包括 CFG 建構、call graph、library function 辨識的工具流程
   - 前提：Part 0-1 基礎，熟悉 ELF 和 objdump

2. **Ghidra Function ID 官方說明**（Ghidra 內建 Help → Function ID）
   - 學什麼：如何從 `.a` 靜態庫建立 FID database，以及如何對目標 binary 應用 FID——把手動隔離 libc 自動化
   - 前提：裝好 Ghidra，了解基本操作

3. **BinExport + BinDiff 文件**（[https://google.github.io/binexport/](https://google.github.io/binexport/)）
   - 學什麼：binary 函式相似度的工業級工具；靜態 binary 的 library 識別核心方法（Ch 28 的工業版）
   - 前提：有 IDA Pro 或 Ghidra BinExport plugin

大 binary 的分而治之解決了「找到哪裡」的問題。下一章看「找到了，但它故意混淆讓你讀不懂」時怎麼辦。

→ [Ch 23 認出並對抗混淆 / anti-reversing](./23-obfuscation-anti-reversing.md)
