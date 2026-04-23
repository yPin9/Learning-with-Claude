# Ch 12 — LTO 效果量測

> 目標：LTO 是現代 compile 的 standard tool。但它實際效果如何？trade-off 怎麼量？這章專注量測 + 實用建議。

## LTO 快速複習

Link-Time Optimization：把每個 `.o` 內容（IR 形式）一起 optimize、而非 per-file。

好處：

- Cross-TU inlining
- Cross-TU dead code elimination
- Cross-TU constant propagation
- Whole-program register allocation

`learn_elf_linking` Ch 14 有詳細 overview。這章 focus **量測**。

## Full LTO vs ThinLTO

### Full LTO

所有 bitcode 合成一個大 module、一起 optimize。

- 最強優化
- Memory peak 很高（GB 級）
- Serial（慢）
- Link time 很長

### ThinLTO

每個 module 仍獨立 optimize、但 expose cross-module summary 讓 linker 做 priority inlining。

- 弱一點優化
- Parallel（快）
- Memory 分散
- 比 non-LTO 強、比 Full LTO 弱

**2026 實務**：多數大 project 用 ThinLTO。Full LTO 只小 project 或極端 performance target。

## 量 binary size 影響

典型 measurement：

```bash
# Baseline
gcc -O2 foo.c bar.c -o app
size app

# with LTO
gcc -O2 -flto foo.c bar.c -o app_lto
size app_lto
```

典型結果：

```
App                  .text    .data    .bss     Total
no LTO               523KB    48KB    120KB    691KB
ThinLTO              472KB    45KB    118KB    635KB (-8%)
Full LTO             448KB    45KB    118KB    611KB (-12%)
```

**LTO 通常減 binary size 5-15%**。Cross-TU dead code elimination 是主力。

## 量 runtime speed

```bash
hyperfine --warmup 3 --runs 10 \
    './app' \
    './app_thin_lto' \
    './app_full_lto'
```

範例結果（SPEC CPU 個別 benchmark）：

```
Benchmark: perlbench_s
no LTO:        103.2 sec  baseline
ThinLTO:        97.1 sec  -6%
Full LTO:       95.3 sec  -8%
```

**LTO 對不同 workload 影響差異大**。某些 benchmark 受益 15%、某些 0%。

## Link time 的 penalty

```bash
time gcc -O2 *.o -o app
time gcc -O2 -flto *.o -o app_lto
```

典型：

```
no LTO: link time 0.3 sec
Thin LTO: link time 8 sec
Full LTO: link time 60 sec (or more for big projects)
```

Full LTO 對 Chrome-level codebase 可能 10 分鐘。所以 Chrome / Firefox 都走 ThinLTO。

## Memory peak

監測 linker 吃多少 memory：

```bash
/usr/bin/time -v gcc -O2 -flto *.o -o app 2>&1 | grep "Maximum resident"
```

典型：

- no LTO: 500 MB
- ThinLTO: 1.5 GB
- Full LTO: 6 GB

**16 GB RAM 的機器跑 Full LTO chromium 會 OOM**。ThinLTO 比較 tractable。

## 跟 PGO 的 combine

LTO + PGO 疊加：

```
Baseline (-O2):          100
LTO only:                105
PGO only:                108
LTO + PGO:               115
```

兩者 benefit 大致 additive，但不完全。最強 combo。

## -fwhole-program vs -flto

GCC 有另一個 flag `-fwhole-program`：declare 你的 code **不被 link 到其他 module**。opt 可以假設 `extern` function 不存在、更激進 DCE。

```bash
gcc -O2 -fwhole-program foo.c -o app
```

效果類似 LTO 但僅 single-TU。**小 project 用 -fwhole-program + -O2**、大 project 用 LTO。

Clang 沒 `-fwhole-program`，要用 LTO 等效。

## LTO 的 debuggability

LTO 會激進 inline → stack trace 不準。但 `-g -flto` 仍產 debug info、只是 symbol 可能混合。

實務建議：

- Release build：`-O2 -flto -g -gsplit-dwarf`
- Debug build：`-O0 -g`
- Performance debug：`-O2 -g`（no LTO，保留 symbol boundary）

## LTO vs visibility

Ch 14 of `learn_elf_linking` 講過。LTO + `-fvisibility=hidden` 是黃金組合：

- LTO 知道哪些 symbol 要 export（default visibility）
- 其他 function 保證 internal
- 對 internal function 激進優化（inline、specialize）

沒 `-fvisibility=hidden` → LTO 對 extern function 保守。效果打折。

## 實測：一個 mini project

```c
// main.c
#include <stdio.h>
extern int add(int a, int b);
extern int multiply(int a, int b);
int main() {
    int r = add(1, multiply(2, 3));
    printf("%d\n", r);
    return 0;
}

// math.c
int add(int a, int b) { return a + b; }
int multiply(int a, int b) { return a * b; }
```

```bash
# no LTO
gcc -O2 main.c math.c -o app_no_lto

# ThinLTO
gcc -O2 -flto=thin main.c math.c -o app_thin

# Full LTO
gcc -O2 -flto main.c math.c -o app_full

objdump -d app_no_lto | grep -A5 '<main>:'
objdump -d app_full | grep -A5 '<main>:'
```

在 Full LTO 下 `add` / `multiply` 被 inline 甚至 constant fold 掉：`main` 可能只 `printf("7")`。

non-LTO 下 `main` 仍 call 這兩個 function。

## RISC-V 上的 LTO 考量

- **GCC / LLVM 都支援**
- **LLD 的 ThinLTO 比 GNU ld 快**（parallelism 好）
- **新 extension** 可能 LTO 階段處理不一致（compiler vs linker 看到的 attribute）
- **`.bc` format 版本**：不同 LLVM 版產的 bitcode 可能不兼容

## LTO + LLD 實戰 command

```bash
clang -O2 -flto=thin -c foo.c -o foo.o
clang -O2 -flto=thin -c bar.c -o bar.o
clang -O2 -flto=thin -fuse-ld=lld foo.o bar.o -o app
```

或 one-shot：

```bash
clang -O2 -flto=thin -fuse-ld=lld foo.c bar.c -o app
```

## ThinLTO 的 threading control

```
-Wl,--thinlto-jobs=4
```

限制 LTO 用幾 thread。OOM 時降 thread 數。

## Distributed LTO

**distributed ThinLTO**：把 optimization 任務分給多台機器 build server。Google / Meta 內部用。

公開工具：`distcc` + ThinLTO integration。大型 project 才值得 setup。

## 量 LTO 的 side effect

可能的 regression：

- **Binary layout 改變** → cache behavior 不同
- **Symbol 消失** → dynamic load 壞
- **過度 inline** → I-cache miss 增

**永遠 benchmark、永遠看 profile**。不要假設。

## 實務 decision flow

```
Small project (< 50 KLoC)?
  Yes → -O2 -flto (full LTO OK)
  No → 繼續

Team 工作流允許 slow link?
  Yes → -O2 -flto=thin (release)
  No → -O2 (no LTO)

Performance critical?
  Yes → PGO + LTO combo
  No → 看 team 偏好
```

## 動手練習

1. 對同一 multi-file C project build 3 版（no LTO, ThinLTO, Full LTO），測 size + speed + build time。
2. 用 `/usr/bin/time -v` 量 LTO 的 memory peak。
3. 同 project 加 `-fvisibility=hidden`，對比 LTO 效果（應該更好）。
4. 跑 Coremark 三種 LTO 版本，看數字差異。
5. 試 `-fuse-ld=lld` vs default linker，對比 LTO link time。

## 常見誤會

1. **「LTO 永遠更快」**：通常 yes, 但某些情況 regression。驗證。
2. **「LTO = 一次性開」**：production pipeline 要考慮 build time。ThinLTO 折衷。
3. **「-fwhole-program 過時」**：small project 仍 valid。
4. **「LLD LTO 最快」**：相對 GNU ld 確實。mold 在某些 case 更快。
5. **「LTO 影響 binary format」**：不。output 仍 ELF、仍 standard。只 optimize 不同。

## 自我檢核

- [ ] 我能量 LTO 對 binary size / speed / build time 的影響
- [ ] 我知道 Full LTO vs ThinLTO 的 trade-off
- [ ] 我能 combine LTO + PGO
- [ ] 我知道 LTO 跟 visibility 的互動
- [ ] 我能做一個 informed decision 要不要開 LTO

下一章看 vectorization report — compiler 有沒有 vectorize 是現代 perf 的關鍵決策。

→ [Ch 13 Vectorization report 閱讀](./13-vectorization-reports.md)
