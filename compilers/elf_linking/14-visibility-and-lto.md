# Ch 14 — Visibility、LTO 與符號

> 目標：理解 symbol visibility（default / hidden / protected / internal）的實際效果、`-fvisibility=hidden` 是怎麼改善大 shared library 的、以及 LTO（Link-Time Optimization）如何跟 linker / visibility 互動。

## 為什麼 visibility 重要

一個 shared library 輸出的 symbol 越多：

- `.dynsym` 越大、檔案越大
- Symbol hash 表越大、lookup 越慢
- 每次 relocate 時要處理的符號越多 → startup 更慢
- 內部實作細節洩漏，ABI 兼容性難管
- Indirection via PLT 增加，runtime cost 高

**一個典型 library 的實測數字**：

- 未指定 visibility 的 libfoo.so：500 KB、匯出 1500 symbol
- 加 `-fvisibility=hidden` 只明確 export API：300 KB、匯出 50 symbol
- startup 時間 -40%、runtime cost -15%

這是巨大優化。現代 library（Qt、libc++、LLVM 本體）都嚴控 visibility。

## Visibility 四種

ELF spec 定義的 visibility（`st_other` 欄位）：

```
STV_DEFAULT     0    完全公開，可以被任何 library 引用
STV_PROTECTED   1    公開但不能被別人 override (不走 PLT)
STV_HIDDEN      2    只在本 module 內可見，不出現在 .dynsym
STV_INTERNAL    3    比 hidden 更嚴（很少用）
```

### DEFAULT

預設。symbol 出現在 `.dynsym`，可被其他 `.so` / executable 引用。跨 `.so` 呼叫走 PLT（允許 `LD_PRELOAD` 覆蓋）。

### HIDDEN

symbol 不出現在 `.dynsym`，只在本 `.so` 內用。跨 `.so` 看不到。

效果：**跨 function call 不走 PLT**（直接 PC-relative）、優化空間大。

C 裡用：

```c
__attribute__((visibility("hidden"))) void helper(void) { ... }
```

或全域用 `-fvisibility=hidden`、在 header 對 API 明確 `__attribute__((visibility("default")))`。

### PROTECTED

symbol **公開**但**不能被 override**。跟 default 差別：

- default：`LD_PRELOAD` 可以 hook
- protected：不能 hook，本 library 直接 bind

Protected 用於「我要 export，但不希望被劫持」。但 glibc 有歷史 bug，protected 早期不穩、現在穩定但用的人少。

### INTERNAL

比 hidden 更嚴格（某些平台語意上 = 本 function 不被 process-wide 任何 code 呼叫）。實務上 GCC 處理跟 hidden 差不多。

## 實戰 use case：一個 Qt 風格的 library

Header：

```c
// foo.h
#ifdef BUILDING_FOO
    #define FOO_API __attribute__((visibility("default")))
#else
    #define FOO_API
#endif

FOO_API int foo_public_function(int);
// 其他 function 沒標記 → 走 hidden (因為 -fvisibility=hidden)
```

編譯：

```bash
gcc -shared -fPIC -fvisibility=hidden -DBUILDING_FOO \
    -o libfoo.so foo.c bar.c baz.c
```

效果：

- `foo_public_function` visible
- 其他所有 internal helper hidden
- binary size / symbol table 大幅縮減

這是 production library 的標準做法。

## 跟 GCC / LLVM version script 搭配

更細的控制可以用 **version script**：

```
FOO_1.0 {
    global:
        foo_public_function;
        foo_other_api;
    local:
        *;                     # 其他全部 hidden
};
```

連結時：

```bash
gcc -shared -fPIC -Wl,--version-script=foo.ver ...
```

這可以 per-symbol 精細控制、還能 version 化（同 library 內新舊 API 共存）。GCC / glibc 都用這個。

### Version script 的實戰威力

Glibc 用 version script 讓一個 `libc.so.6` 同時有：

- `memcpy@GLIBC_2.2.5`
- `memcpy@GLIBC_2.14`

不同 client 綁不同版本。向下相容神器。看：

```bash
readelf --dyn-syms /lib/x86_64-linux-gnu/libc.so.6 | grep memcpy
```

一堆 `memcpy@` 不同版本。

## Hidden 的 performance 影響

`helper()` 被 `foo_public_function` 呼叫，兩個在同一 `.so`：

**default visibility**：

```asm
# 走 PLT，可能被 override
call  helper@plt
```

多一次 indirect jump via GOT。

**hidden visibility**：

```asm
# 直接 PC-relative call
call  helper         # auipc + jalr (or jal if relaxed)
```

快 1–2 cycle，還可以被 linker relax 成單條 `jal`。

對 hot path function，差距累積可觀。

## LTO (Link-Time Optimization)

傳統 compile 流程：

```
a.c → a.o (optimized per-file)
b.c → b.o
a.o + b.o → linker → binary
```

每個 `.c` 獨立優化。**跨 file 的優化機會（如 inline 不同 file 的 function）無法做**。

LTO 改變：**compile 時只產中間表示（GIMPLE / LLVM IR），link 時再做整合優化**。

```
a.c → a.o (contains IR, NOT machine code)
b.c → b.o (contains IR)
a.o + b.o → LTO: 合併 IR → 全域優化 → 產 machine code → linker
```

### 開啟 LTO

```bash
gcc -flto -O2 a.c b.c -o hello
```

GCC 跟 LLVM 都支援。LLD 跟 GCC 的 `ld` 對 LTO 整合很好。

### LTO 的實際收益

常見收益：

- **Cross-file inlining**：小 function 可以 inline 過 file boundary
- **Dead code elimination**：沒被任何人 call 的 function 砍掉
- **Constant propagation**：跨 file 的常數 propagation
- **Indirect call promotion**：某些 vtable call 變直接 call

實測：

- 典型 C 程式 size -5~15%、速度 +2~10%
- C++ 程式（很多 small function）收益更大

### LTO 的代價

- **Link 時間變長**：整合 IR + 優化 = 慢幾倍
- **Memory 消耗大**：link 時吃大量 memory
- **Debug 變難**：stack trace 可能含 inlined function（需要 `-g -flto`）

## LTO 跟 visibility 的互動

LTO 能做 inline / constant propagation 的前提：**知道 function 只在本 module 用**。

如果 symbol 是 default visibility → LTO 要保守（外人可能 override / hook）。

所以 **LTO + `-fvisibility=hidden` 是黃金組合**：

- 多數 function hidden → LTO 可以積極優化
- 只有 API 是 default → LTO 保留它們為 hooking 留空間

現代 library 標準 build：

```bash
gcc -shared -fPIC -fvisibility=hidden -flto -O3 \
    -Wl,--version-script=api.ver \
    -o libfoo.so *.c
```

## `__attribute__((always_inline))` vs visibility

常見疑問：既然 LTO 能跨 file inline，還需要 `always_inline` 嗎？

**還需要**：`always_inline` 強制 inline，`-flto` 只是 enable。LTO 仍然根據 heuristic 決定是否 inline。某些一定要 inline 的（e.g., 避免 function call overhead 的 hot primitive）仍需 attr。

## Whole-archive linking 跟 LTO

```bash
gcc -flto main.c -Wl,--whole-archive -lfoo -Wl,--no-whole-archive
```

`--whole-archive` 強制 linker 把 `.a` 全部 `.o` 拉進來（不 lazy）。LTO 時這常常需要 —— LTO 的 `.o` 拉進來才能 IR 整合優化。

## Visibility 影響的最典型 debug scenario

**症狀**：「我在 library 裡加了 function，但 main program 找不到 symbol (undefined reference)」

**原因**：你用 `-fvisibility=hidden` 但忘了 mark 新 function 為 `visibility("default")`。

**修法**：

```c
// foo.h
FOO_API void new_function(void);    // 加 FOO_API
```

FOO_API macro 展開為 `visibility("default")`。

## PLT 的 "binding" 與 visibility

前面說 hidden 的 symbol 不走 PLT。其實更精確：

- default + lazy：走 PLT + GOT（可 override）
- default + eager (RELRO full)：走 PLT + GOT（但 GOT RO，不可 hot-hook）
- protected：不走 PLT，直接 bind（本 lib 內）
- hidden：不走 PLT，不 export，跟 static-in-same-file 差不多

所以 hidden function 的 call overhead 跟 `static inline function` 接近，跟跨 `.so` 差很多。

## 動手練習

1. 寫 5 個 function 的 `.c`，分別用 default / protected / hidden visibility。用 `readelf --dyn-syms` 看哪些 export。
2. 編同一個 library 有 / 沒有 `-fvisibility=hidden`，比 size 差異、比 `.dynsym` 條數。
3. 用 `-flto` 重編自己某個 project，量 binary size + runtime 差異。
4. 寫 version script 給你 library 三個 function 指定 FOO_1.0 namespace，用 `readelf --dyn-syms` 看版本標示。
5. 故意在 default visible function 上用 `LD_PRELOAD` 測試 hook；換 protected 重試看能不能被 hook（應不能）。

## 常見誤會

1. **「hidden 會讓 C++ 模板問題變多」**：反過來，hidden 能避免模板 symbol 的 runtime 重複 lookup。但 RTTI 跟 exception 需要 default visible（Ch 14 不深入，跟 Itanium ABI 有關）。
2. **「LTO 跟 `-O3` 等價」**：不。`-O3` 是 per-file 優化、LTO 是 cross-file。可以同時用。
3. **「protected 被廢棄」**：glibc 早期有 bug，但現在穩定。只是很少人用。
4. **「static function 就是 hidden」**：在 ELF 層面兩者相似（都 LOCAL），但 C 層 `static` 是 file-scope，hidden 是 module-scope（跨 `.c` 可見）。
5. **「LTO 總是讓 binary 變小」**：通常是。但極端 case（大量 template inline）可能更大。測，不要 assume。

## 自我檢核

- [ ] 我能列出四種 visibility 的語意差異
- [ ] 我能解釋為什麼大 library 要用 `-fvisibility=hidden`
- [ ] 我能寫 version script 控制 symbol 匯出
- [ ] 我知道 LTO 的運作機制與開啟方法
- [ ] 我能解釋 hidden + LTO 協同的優化效果

下一章進一個新議題 — DWARF debug info 跟 ELF section 如何互動。對寫 debugger / profiler 的人必讀，對寫 compiler 的人也有幫助。

→ [Ch 15 DWARF debug info 與 section 佈局](./15-dwarf-and-debug.md)
