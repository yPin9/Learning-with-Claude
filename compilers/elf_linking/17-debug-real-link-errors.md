# Ch 17 — 實戰 debug：讀懂真實 link error

> 目標：把前 16 章知識整合成「看到 link error 就能立刻定位」的能力。本章蒐集 15+ 種常見錯誤訊息、症狀、診斷步驟、修法。

## debug 心法

link error 的 debug 流程：

```
1. 完整讀錯誤訊息（不要只看第一行）
2. 分辨階段：Resolution / Relocation / Relaxation
3. 對應到本書的哪章
4. 對症下藥
```

別走的路：

- Google 第一篇文章貼 answer
- 亂加 `-fPIC` / `-fno-lto` 試
- 重裝 toolchain

## Error 1：`undefined reference to 'X'`

```
/usr/bin/ld: hello.o: in function `main':
hello.c:(.text+0x20): undefined reference to `my_function'
collect2: error: ld returned 1 exit status
```

**分類**：Symbol Resolution

**原因**：

- 有人引用 `my_function`，linker 找不到定義
- 可能：忘了 `.o`、忘了 `-l`、library 搜尋路徑不對

**診斷**：

```bash
# 誰引用？
nm *.o | grep "U my_function"

# 誰定義？
nm *.o *.a | grep "T my_function"      # T = defined in .text

# 如果在 .so：
nm -D /path/libfoo.so | grep my_function
```

**修法**：

- 補 `.o`：`gcc a.o b.o my_function.o`
- 補 lib：`gcc main.c -lfoo`
- 順序：**lib 在後**：`gcc main.c -lfoo`（不是 `-lfoo main.c`）
- 加搜尋路徑：`-L/path/to/lib`

## Error 2：`multiple definition of 'X'`

```
/usr/bin/ld: b.o:(.data+0x0): multiple definition of `global_var'; a.o:(.data+0x0): first defined here
```

**分類**：Symbol Resolution

**原因**：

- 兩個 `.c` 都 define 同一個 global variable
- 現代 GCC `-fno-common` 預設，重複 declaration 直接錯

**典型錯誤**：header 裡寫 `int x = 0;` 而不是 `extern int x;`。每個 `.c` include header 就各自 define 一個。

**修法**：

```c
// common.h
extern int x;      // declaration

// common.c
int x = 0;         // definition (只這裡)

// 其他 .c
#include "common.h"
```

## Error 3：`relocation truncated to fit`

```
/usr/bin/ld: hello.o: in function `main':
hello.c:(.text+0x20): relocation truncated to fit: R_RISCV_JAL against `my_function'
```

**分類**：Relocation

**原因**：`jal` 最多跳 ±1 MiB，target 超過這個範圍。

**診斷**：

```bash
# 看 relocation
objdump -r hello.o | grep R_RISCV_JAL

# 看 my_function 在哪
nm hello | grep my_function
```

**修法**：

- 用 `call` 取代 `jal`（若手寫 asm）
- 換 `-mcmodel=medany`
- 調 linker script 讓 target 更近
- 檢查是不是意外把兩個 section 排太遠

## Error 4：`recompile with -fPIC`

```
/usr/bin/ld: a.o: relocation R_RISCV_HI20 against symbol `g' can not be used when making a shared object; recompile with -fPIC
```

**分類**：Relocation + PIC

**原因**：

- 你在用 `non-PIC .o` 做 `-shared`
- non-PIC 用絕對地址，shared library 不允許

**修法**：

```bash
gcc -fPIC -c a.c -o a.o
gcc -shared a.o -o libfoo.so
```

全部 `.o` 都要 `-fPIC`。

## Error 5：`relocation R_X86_64_PC32 against symbol cannot be used when making a PIE object; recompile with -fPIE`

類似 Error 4，但 target 是 PIE executable 而非 shared library。

**修法**：加 `-fPIE` 編 `.o`，link 加 `-pie`。

## Error 6：`cannot find -lfoo`

```
/usr/bin/ld: cannot find -lfoo: No such file or directory
```

**分類**：Input scanning

**原因**：linker 找不到 `libfoo.so` 或 `libfoo.a`。

**診斷**：

```bash
# linker 搜哪些路徑
ld --verbose | grep SEARCH_DIR

# 加 -L
gcc main.c -L/custom/path -lfoo
```

**修法**：

- 裝 package：`apt install libfoo-dev`
- 加 `-L/path/to/lib`
- 設 `LIBRARY_PATH`

注意 `LIBRARY_PATH` 影響 link-time，`LD_LIBRARY_PATH` 影響 runtime。

## Error 7：Runtime `error while loading shared libraries: libfoo.so.1: cannot open shared object file`

**分類**：Runtime（不是 link-time）

**原因**：binary link 好了，但 runtime 找不到 `.so`。

**診斷**：

```bash
ldd hello       # 看哪些 .so，find not found 的
```

**修法**：

- 裝 package：`apt install libfoo1`
- `LD_LIBRARY_PATH=/path/to/lib ./hello`（臨時）
- `ldconfig` + 放 `.so` 到 `/etc/ld.so.conf.d/*.conf` 列的路徑
- `patchelf --set-rpath /path ./hello`（永久綁 RPATH）

## Error 8：`version node not found for symbol`

```
/usr/bin/ld: foo.o: version node not found for symbol X@FOO_1.0
```

**分類**：Version script

**原因**：version script 裡提到了某個不存在的 symbol。

**診斷**：

```bash
grep '^FOO_1.0' foo.ver    # 看你的 version script
nm -D libfoo.so | grep X    # 看 library 有沒有這個 symbol
```

**修法**：

- 確保 symbol 存在（有沒有拼錯、有沒有 visibility("default")）
- 修改 version script 的 symbol list

## Error 9：`symbol 'X' already defined`

```
/usr/bin/ld: warning: a.o: symbol X already defined; redefinition ignored
```

**分類**：Resolution（但只 warning）

**原因**：linker script 或另一個 `.o` 重複定義。

**修法**：

- 用 `PROVIDE` 取代直接 assign（linker script）
- 讓一邊改 static

## Error 10：`relocation against local symbol in readonly segment`

```
/usr/bin/ld: relocation R_RISCV_32 against `.rodata' in readonly segment; recompile with -fPIC
```

**分類**：Relocation + PIE

**原因**：非 PIC / 非 PIE 的 code 試圖放 relocation 在 RO segment。

**修法**：加 `-fPIC`（for .so）或 `-fPIE -pie`（for PIE）。

## Error 11：Section overflow

```
/usr/bin/ld: region `FLASH' overflowed by 123 bytes
```

**分類**：Linker script

**原因**：binary 超過 MEMORY region 的 LENGTH。

**診斷**：

```bash
ld -T my.lds -Map=map.txt ...
size hello         # 看 .text/.data/.bss 大小
```

**修法**：

- 砍功能 / 砍 library
- 開 `-Os` / `-flto` 優化 size
- 加大 MEMORY 的 LENGTH（如果硬體允許）
- 用 `--gc-sections` 砍無用

## Error 12：`relocation is not in range against ...`（`.eh_frame`）

**分類**：DWARF 相關

**原因**：`.eh_frame` 的 relocation 範圍太遠。多半 linker script 亂動造成。

**修法**：確保 `.eh_frame_hdr` 跟 `.eh_frame` 位置合理（通常在 `.text` 附近）。

## Error 13：`segfault in ld.so at load time`

不算 link error，但看起來像。

**原因**：binary 的 dynamic section 有問題（可能 `DT_NEEDED` 指向不存在的 `.so`、或 PT_INTERP 設錯）。

**診斷**：

```bash
readelf -d hello | head -20   # 看 NEEDED 清單
readelf -x .interp hello      # 看 interpreter path
file /lib/.../ld-linux-*.so.* # 確認 interpreter 存在
```

## Error 14：RISC-V 專屬：`gp cannot be used for this relocation`

**分類**：Relaxation

**原因**：某個 relocation 試圖用 gp-relative 但 `gp` 沒設好。

**修法**：

- startup 裡設 `gp`（`la gp, __global_pointer$`）
- 關 relaxation（`--no-relax`）
- 檢查 linker script 有沒有 `PROVIDE(__global_pointer$ = ...)`

## Error 15：C++：`undefined reference to vtable for X`

```
undefined reference to `vtable for MyClass'
```

**分類**：Resolution + C++

**原因**：C++ class 有 virtual method 但沒 define。第一個 non-inline non-pure virtual method 的 translation unit 負責 vtable。

**典型問題**：

```cpp
// foo.h
class Foo {
public:
    virtual void method();  // 聲明但沒定義
};
```

**修法**：在 .cpp 定義某個 virtual method。

## Error 16：`cannot represent X relocation`

**分類**：Relocation type 不支援

**原因**：compiler 產生了 linker 不認的 relocation type。可能：

- 用 experimental extension，linker 沒跟上
- compiler / linker 版本不匹配

**修法**：

- 升級 binutils / LLD
- 關 extension

## Error 17：`wrong architecture`

```
/usr/bin/ld: hello.o: Relocations in generic ELF (EM: 243)
```

**分類**：ABI mismatch

**原因**：用錯 linker。host linker 處理不了 RISC-V ELF。

**修法**：用 `riscv64-*-ld` 而不是 system `ld`。

## Error 18：LTO 相關的 `cannot find section .gnu.lto_*`

**分類**：LTO

**原因**：`.o` 含 LTO 中間表示，但 linker 不支援 LTO 或用錯 plugin。

**修法**：

- 確保 GCC / LLVM 版本一致
- 不混用 gcc-10 的 `.o` 跟 gcc-13 的 linker
- 加 `-fuse-linker-plugin`

## 一個完整 debug flow 示範

假設遇到：

```
./hello: error while loading shared libraries: libfoo.so: cannot open shared object file: No such file or directory
```

流程：

```bash
# Step 1: 確認 runtime 問題
ldd hello
# libfoo.so => not found

# Step 2: 找 libfoo.so 在不在系統
find / -name "libfoo.so*" 2>/dev/null

# Step 3a: 如果存在但路徑不對
echo $LD_LIBRARY_PATH
# 加到 ld.so.conf 或 LD_LIBRARY_PATH

# Step 3b: 如果不存在
apt search libfoo
apt install libfoo-dev

# Step 4: 用 patchelf 硬綁（臨時）
patchelf --set-rpath /custom/path ./hello
```

## Debug 工具速查

| 情境 | 工具 |
|------|------|
| 看 link 過程 | `gcc -v`, `ld -v` |
| 看 symbol 位置 | `nm`, `readelf -s` |
| 看 relocation | `objdump -r` |
| 看 dynamic deps | `ldd`（runtime），`readelf -d`（靜態）|
| 看 linker 路徑 | `ld --verbose` |
| 看 runtime linking | `LD_DEBUG=all ./binary` |
| 看 linker decision | `ld -M` / `-Wl,-Map=...` |
| 看 section layout | `readelf -S`, `readelf -l` |
| 看 Version | `readelf --dyn-syms | grep '@@'` |

## 常見誤會

1. **「link error 一定是 linker bug」**：99% 是你的 flag / code / script。先排除人為。
2. **「錯誤訊息看第一行就夠」**：至少看完整個 stack、所有 warning 通常有線索。
3. **「加 `-fPIC` 什麼都能修」**：不，有時反而掩蓋真正問題。
4. **「重裝 toolchain 能修 90% 問題」**：幾乎 0%。別浪費時間。
5. **「Linker error 不會影響 runtime」**：link-time 警告有時預示 runtime bug（如 GOT 用錯 entry）。

## 動手練習

1. 故意造 10 種錯誤：漏 `.o`、漏 `-l`、順序錯、version script 空、section overflow ...。每一個錯誤記下診斷與修法。
2. 找一個 open source project（e.g., redis），故意改 Makefile 讓它 link 錯、練習 debug。
3. 寫一份「linker error cheatsheet」，把本章每個錯誤的 error message + 修法壓成一張 A4。
4. 用 `LD_DEBUG=all` 跑一個複雜 program，讀 output 辨認出：load library / symbol resolve / relocation 各階段。
5. 面對一個陌生 binary（e.g., 從 CI 拿到），用 readelf / objdump 用 30 分鐘寫一份 "diagnosis report"：架構、linker、feature、可能問題。

## 自我檢核

- [ ] 我能分辨 resolution / relocation / relaxation 階段的錯
- [ ] 我能在 1 分鐘內定位常見 10 種 link error 的根源
- [ ] 我能用 readelf / objdump / nm / ldd 組合 debug 任何問題
- [ ] 我能讀 `LD_DEBUG=all` 的 runtime output
- [ ] 我知道什麼時候該升級 toolchain、什麼時候改 source

Part 5 結束。下一章進入 Part 6 最後一章 — mini linker 的設計 concepts，為 final project 鋪路。

→ [Ch 18 Mini linker 的結構設計](./18-mini-linker-design.md)
