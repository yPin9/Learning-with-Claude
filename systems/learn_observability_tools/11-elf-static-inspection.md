# Ch 11 — ELF 靜態檢視

> 目標：學會 nm / readelf / objdump / addr2line / ldd / strings —— 不執行 binary 就能看出它要什麼、有什麼 symbol、stack trace 怎麼翻成 source 行。

## 為什麼要靜態檢視

dynamic 工具看「程式跑時做什麼」，static 工具看「程式裡面有什麼」。需求場景：

- strace -k 印出 stack 是 `+0x1234` offset，要翻成 source 行
- core dump 裡的 frame 要對應到 function name
- 第三方 binary 要不要 link 某 lib？
- 為什麼程式 size 從 1MB 變 5MB？哪些 symbol 加進去了
- 想知道 binary 用什麼 compiler / build 設定

## ELF 是什麼（30 秒版）

Linux executable / .so / .o 都是 ELF (Executable and Linkable Format)。結構：

```
 ┌────────────────────────┐
 │ ELF Header             │ magic + arch + entry point
 ├────────────────────────┤
 │ Program Headers        │ runtime 用：哪段 mmap 進記憶體
 ├────────────────────────┤
 │ Section .text          │ code
 │ Section .data          │ initialized data
 │ Section .bss           │ uninitialized
 │ Section .rodata        │ read-only data (字串等)
 │ Section .symtab        │ symbol table
 │ Section .strtab        │ string table
 │ Section .debug_info    │ DWARF debug
 │ Section .got / .plt    │ 動態連結用
 │ ...                    │
 ├────────────────────────┤
 │ Section Headers        │ link / debug 用：每個 section 元資料
 └────────────────────────┘
```

兩種 view：

- **Program Headers / Segments** — runtime view（loader 怎麼 mmap）
- **Section Headers** — link/debug view（編譯器產生的內容單位）

`readelf` 能看兩者。

## readelf — ELF 全景

```bash
readelf -h /bin/ls          # 看 ELF header
readelf -l /bin/ls          # 看 program headers
readelf -S /bin/ls          # 看 sections
readelf -s /bin/ls          # 看 symbol table
readelf -d /bin/ls          # dynamic section（依賴的 .so）
readelf -n /bin/ls          # notes section（build-id, ABI）
readelf -a /bin/ls          # 全部
```

ELF header：

```bash
readelf -h /bin/ls
# Magic:   7f 45 4c 46 02 01 01 00 00 00 00 00 00 00 00 00
# Class:                             ELF64
# Data:                              2's complement, little endian
# Type:                              DYN (Position-Independent Executable file)
# Machine:                           Advanced Micro Devices X86-64
# Entry point address:               0x6ab0
# ...
```

`Type: DYN` 表示 PIE (Position Independent Executable)，現代預設。`EXEC` 是非 PIE。

dynamic section（最常用）：

```bash
readelf -d /bin/ls | head
# Tag         Type           Name/Value
# 0x00000001 (NEEDED)        Shared library: [libselinux.so.1]
# 0x00000001 (NEEDED)        Shared library: [libc.so.6]
# 0x0000000c (INIT)          0x4000
# 0x0000000d (FINI)          0x18a08
# ...
```

`NEEDED` 列出依賴的 .so —— 跟 `ldd` 看到的一樣，但不執行 binary（safer，不會誤跑）。

## ldd — 動態依賴 list

```bash
ldd /bin/ls
# linux-vdso.so.1 (0x00007ffd...)
# libselinux.so.1 => /lib/x86_64-linux-gnu/libselinux.so.1 (0x00007f...)
# libc.so.6 => /lib/x86_64-linux-gnu/libc.so.6 (0x00007f...)
# /lib64/ld-linux-x86-64.so.2 (0x00007f...)
```

每行：「需要的 lib」 → 「實際在哪」。

**安全警告**：`ldd` 內部會**部分執行 binary**（透過 LD_TRACE_LOADED_OBJECTS）。對不信任 binary 用：

```bash
readelf -d binary | grep NEEDED
```

更安全。

## nm — symbol list

```bash
nm /usr/lib/libc.so.6 | head
# 0000000000022bd0 T __libc_start_main
# 0000000000094b80 T malloc
# 0000000000094a40 T free
# ...
```

每行：「位址 + type + 名字」。type 字母重要：

| 字母 | 意義 |
|---|---|
| `T` / `t` | text (code), `T` global / `t` local |
| `D` / `d` | data, init |
| `B` / `b` | bss, uninit |
| `R` / `r` | read-only data |
| `U` | undefined（要從別處連進來） |
| `W` | weak（可被 override） |
| `V` | weak data |

```bash
nm -D /usr/lib/libc.so.6     # dynamic symbol（runtime export 的）
nm -u myprog                  # 列 undefined（缺什麼 lib）
nm --defined-only myprog      # 只列定義的
nm -S myprog                  # 顯示 size
nm -C ...                     # demangle C++
```

debug「missing symbol」標準動作：

```bash
nm -u myprog | head
# U malloc
# U free
# U some_external_func
```

`U some_external_func` 是「我用了但沒人提供」 → 缺 lib。

## objdump — 反組譯 + 詳細

```bash
objdump -d /bin/ls | less     # disassemble
objdump -d /bin/ls --section=.plt
objdump -t /bin/ls            # symbol table
objdump -T /bin/ls            # dynamic symbol
objdump -p /bin/ls            # program headers
objdump -h /bin/ls            # section headers
objdump -s /bin/ls            # 全部 section 內容
objdump -x /bin/ls            # 全部
objdump -r ...                # relocations
objdump -C ...                # demangle
```

最常用：disassemble 一個 function：

```bash
objdump -d ./myprog | grep -A 20 '<main>:'
# 0000000000401130 <main>:
#   401130: f3 0f 1e fa          endbr64 
#   401134: 55                   push   %rbp
#   401135: 48 89 e5             mov    %rsp,%rbp
#   ...
```

或拿 raw binary 反組譯：

```bash
objdump -D -b binary -m i386:x86-64 raw.bin
```

shellcode 分析常用。

## addr2line — 地址翻 source

`strace -k`、core dump、gdb backtrace 顯示的常常是 `+0x1234` offset。要翻 source 行：

```bash
addr2line -e ./myprog 0x401234
# /home/me/src/myprog.c:42

addr2line -e ./myprog -f 0x401234        # 加 function name
# main
# /home/me/src/myprog.c:42

addr2line -e ./myprog -f -C -i 0x401234  # demangle + inline 鏈
```

binary 必須有 debug info（`-g` build），不然只顯示 `??`。

stripped binary 沒 debug info：

```bash
file myprog
# ELF ... not stripped
strip myprog
addr2line -e myprog -f 0x401234
# ?? at ??:0
```

production 常 strip 來縮小 size，但要保留 separate debug file（`objcopy --only-keep-debug`）。

## strings — 找字串

```bash
strings /bin/ls | head
# /lib64/ld-linux-x86-64.so.2
# libselinux.so.1
# _ITM_deregisterTMCloneTable
# ...
```

抽 binary 裡所有可印字元 sequence ≥ 4 個。debug 用法：

- 找版本號：`strings /usr/bin/git | grep '^[0-9]'`
- 找 hardcoded path：`strings myprog | grep '^/'`
- 找 secret 不小心 baked in：`strings myprog | grep -i 'key\|token'`

老掉牙的「不知道這 binary 是什麼」第一招：`strings binary | head`。

## file — 一行識別

```bash
file /bin/ls
# /bin/ls: ELF 64-bit LSB pie executable, x86-64, version 1 (SYSV), 
# dynamically linked, interpreter /lib64/ld-linux-x86-64.so.2, 
# BuildID[sha1]=..., for GNU/Linux 3.2.0, not stripped
```

一行解讀很多事：64-bit / pie / dynamically linked / debug 沒 strip / build ID。

## 場景：strace -k 翻 source

```bash
strace -k -e openat ./myprog
# openat(AT_FDCWD, "/etc/passwd", O_RDONLY) = 3
#  > /lib/x86_64-linux-gnu/libc.so.6(open64+0x4d)[0x123abc]
#  > ./myprog(read_file+0x12)[0x40123a]
#  > ./myprog(main+0x45)[0x4012a8]
```

```bash
addr2line -e ./myprog -f 0x40123a
# read_file
# /home/me/src/myprog.c:25
```

知道 syscall 從 source 哪行發起。

## 場景：core dump 翻 frame

```bash
gdb -c core ./myprog
(gdb) bt
# #0 0x00007f... in ??()
# #1 0x00000000004012a8 in main () at myprog.c:42
```

gdb 直接翻好。如果 stripped：

```bash
addr2line -e ./myprog 0x4012a8
# myprog.c:42
```

## 場景：binary size 變大

```bash
size myprog
#    text    data     bss     dec     hex filename
#  123456   12345    1234  137035   2178b myprog
```

`text` 是 code、`data` 是有初值的 data、`bss` 是無初值 data。

```bash
nm -S --size-sort myprog | tail -20
```

按 symbol size 排序，看誰最肥。

## 場景：找 missing symbol

```c
// uses_md5.c
#include <openssl/md5.h>
int main() {
    unsigned char d[16];
    MD5_Init(NULL);
    return 0;
}
```

```bash
gcc uses_md5.c -o uses_md5
# /usr/bin/ld: undefined reference to `MD5_Init'
```

```bash
nm -u myprog 2>&1 | head
# U MD5_Init
```

需要 -lcrypto。

## 一個常見踩雷：strip 之後 backtrace 沒名字

production 為了 size 通常 strip。但 strace -k / gdb bt 看到一片 `??`。

正確做法：

```bash
gcc -g myprog.c -o myprog
objcopy --only-keep-debug myprog myprog.debug
strip --strip-debug myprog
objcopy --add-gnu-debuglink=myprog.debug myprog
```

`myprog` strip 但記住 debug file 的路徑。gdb 會自動找 `myprog.debug` 的 symbol。Linux distro 套件的 `-dbg` package 就是這樣做。

## 一個常見踩雷：addr2line 有時翻錯

PIE binary（現代預設）的 address 是 runtime relocation 後的。strace 印的位址是 runtime，addr2line 要的是 file-relative：

```bash
# runtime address
0x55b1234561234

# 找 binary base from /proc/PID/maps
55b123450000-55b1234abcde r-xp ... ./myprog

# file address = runtime - base
0x55b1234561234 - 0x55b123450000 = 0x6234

addr2line -e ./myprog 0x6234
```

或乾脆 build non-PIE：`gcc -no-pie ...`，address 就是 file address。

## 一個常見踩雷：`readelf` vs `objdump`

兩個工具大量 overlap。差別：

- `readelf` 是 binutils 的「按 ELF spec 印」工具，輸出格式穩定 — script 解析友善
- `objdump` 是 binutils 的「綜合 binary 處理」，含反組譯，輸出格式比較人類友善

**讀資訊** readelf，**反組譯** objdump。

## 動手練習

**1. 看你寫過程式的 ELF**

```bash
gcc -g hello.c -o hello
readelf -h hello
readelf -l hello | head -20
readelf -d hello
nm hello | head
file hello
```

**2. 翻 strace stack**

```bash
gcc -g -O0 hello.c -o hello
strace -k ./hello 2>&1 | grep -A 5 openat | head
addr2line -e hello -f $(那個 offset)
```

**3. strip 前後**

```bash
gcc -g hello.c -o hello
ls -l hello
nm hello | wc -l
strip hello
ls -l hello       # 變小
nm hello          # nm: hello: no symbols
```

**4. 找誰 link 某 lib**

```bash
for f in /usr/bin/*; do
    if ldd "$f" 2>/dev/null | grep -q libselinux; then
        echo "$f"
    fi
done | head
```

**5. 反組譯一個 function**

```bash
objdump -d hello | sed -n '/<main>:/,/^$/p'
```

對著 source 看每條 asm 指令對應到 C 哪一行（`-S` flag 加上 source 混排）：

```bash
objdump -dS hello | sed -n '/<main>:/,/^$/p'
```

需要 `-g` build。

## 自我檢核

- [ ] 知道 ELF 的 4 大區（header / program headers / sections / section headers）
- [ ] readelf -d / -h / -l / -s 各看什麼
- [ ] nm 的 T / U / W 字母意義
- [ ] addr2line 用 file-relative address，不是 runtime
- [ ] 知道為什麼 PIE binary 翻地址要先扣 base
- [ ] strip 跟 separate debug file 怎麼配合

Part 4 結束。下一章進 perf — performance 分析的瑞士刀。

→ [Ch 12 perf 基礎](./12-perf-fundamentals.md)
