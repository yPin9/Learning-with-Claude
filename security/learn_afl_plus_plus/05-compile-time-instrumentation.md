# Ch 5 — 編譯期 instrumentation 四種模式

> 目標：比較 `afl-gcc-fast` / `afl-clang-fast`（PCGUARD）/ `afl-clang-lto`（collision-free）/ 古早 `afl-as` 的差異；深入 LLVM pass 層級講 LTO mode 如何做到 collision-free；給出何時選哪個的判斷。

## 插樁要解決的問題

Ch 4 已經定義目標：每條 edge 執行時，做這兩行：

```c
__afl_area_ptr[prev_loc ^ cur_loc]++;
prev_loc = cur_loc >> 1;
```

**剩下的問題只剩「怎麼讓 compiler 把這兩行放進每個 basic block 的開頭」**。AFL 一路走過四種實作，對應不同時代的 trade-off：

| 方法 | 作用階段 | 生效 compiler | 現況 |
|---|---|---|---|
| `afl-as` | 組譯（.s 檔文字 rewrite） | GCC / Clang 皆可（via `as`） | Deprecated，相容 only |
| GCC plugin（`afl-gcc-fast`） | GCC middle-end | GCC | 主流 |
| LLVM pass（`afl-clang-fast`） | LLVM IR | Clang | 主流 |
| LTO pass（`afl-clang-lto`） | link 階段 | Clang + lld | **最推薦** |

## afl-as：古老的起點

最初的 AFL 用 `afl-as` 這個 fake assembler。原理可愛又粗暴：

- Compiler driver 呼叫 `/usr/bin/as` 時 AFL 把它換成 `afl-as`。
- `afl-as` 讀 `.s` 檔（組譯器的輸入），用文字 regex 找出每個函式入口與 branch target label，在那裡插入一段 hand-written assembly：

```asm
; 插在每個 basic block 開頭的段
push %rdx
push %rcx
push %rax
mov  __afl_area_ptr(%rip), %rdx
mov  __afl_prev_loc(%rip), %rcx
mov  $<random_u16>, %rax        ; 這個 block 的 cur_loc
xor  %rcx, %rax                 ; cur_loc ^ prev_loc
incb (%rdx, %rax)
shrl $1, %rax
mov  %rax, __afl_prev_loc(%rip)
pop  %rax
pop  %rcx
pop  %rdx
```

**問題一堆**：文字級 rewrite 不可靠（optimization 後 label 可能消失）、跨平台難（ARM、RISC-V 要另寫）、效能差（沒機會和 compiler 的 register allocation 合作）。2017 年後基本棄用，**如果你還在用就該升級**。

## afl-gcc-fast / afl-clang-fast：plugin 派

### GCC plugin

GCC 提供一個正式的 plugin API，可以在 pipeline 的某個階段看到中間表示（GIMPLE、RTL）。AFL++ 的 `gcc_plugin/` 目錄下的 pass 在 IPA / RTL 階段遍歷函式，給每個 basic block 插入前述那兩行 C（轉成 RTL）。

### LLVM pass

LLVM 也有 pass 系統，而且比 GCC plugin 更成熟。AFL++ 的 `instrumentation/afl-llvm-pass.so.cc` 是典型的 `ModulePass`（新版改 `PassPlugin`），簡化的骨架長這樣：

```cpp
struct AFLCoverage : public PassInfoMixin<AFLCoverage> {
    PreservedAnalyses run(Module &M, ModuleAnalysisManager &MAM) {
        // 在 module 層級宣告 __afl_area_ptr、__afl_prev_loc
        GlobalVariable *AFLMapPtr = new GlobalVariable(
            M, PointerType::get(Int8Ty, 0), false,
            GlobalValue::ExternalLinkage, nullptr, "__afl_area_ptr");
        GlobalVariable *AFLPrevLoc = /* thread-local u32 */;

        for (Function &F : M) {
            for (BasicBlock &BB : F) {
                // 在 BB 開頭插入 coverage update
                IRBuilder<> B(&*BB.getFirstInsertionPt());

                Value *PrevLoc = B.CreateLoad(Int32Ty, AFLPrevLoc);
                Value *CurLoc = ConstantInt::get(Int32Ty, random_u16());
                Value *MapIdx = B.CreateXor(PrevLoc, CurLoc);

                Value *MapPtr = B.CreateLoad(/* ... */, AFLMapPtr);
                Value *Counter = B.CreateGEP(Int8Ty, MapPtr, MapIdx);
                Value *Old = B.CreateLoad(Int8Ty, Counter);
                Value *New = B.CreateAdd(Old, ConstantInt::get(Int8Ty, 1));
                B.CreateStore(New, Counter);   // ++ 動作

                Value *NewPrev = B.CreateLShr(CurLoc, 1);
                B.CreateStore(NewPrev, AFLPrevLoc);
            }
        }
        return PreservedAnalyses::none();
    }
};
```

這是「CLASSIC」風格的 instrumentation — 每個 block 用一個**編譯時隨機 ID**。會 collision，但實作最簡單。

### PCGUARD（預設）

AFL++ 現代版 `afl-clang-fast` 預設不再用 CLASSIC，改用 **PCGUARD**，走 LLVM 內建的 `-fsanitize-coverage=trace-pc-guard`：

1. LLVM 在每個 basic block 開頭插入 `__sanitizer_cov_trace_pc_guard(&guard)`。
2. `guard` 是 4-byte 全域，每個 block 一個。
3. AFL++ 提供自己的 `__sanitizer_cov_trace_pc_guard` 實作（在 `afl-compiler-rt.o.c`），用 `*guard` 當作 cur_loc。
4. 第一次執行時，runtime 幫每個 guard 分配一個值（通常遞增 ID），之後照著 XOR 模式寫 bitmap。

PCGUARD 的好處是**邊 SanitizerCoverage 基礎設施走**，能共用 LLVM 生態（同樣的 pass 也給 libFuzzer 用）。缺點是依然可能 collision（因為 random ID）。

## afl-clang-lto：collision-free

想徹底消 collision，唯一方法是：**編譯期知道全部 edge 的總數，給每條 edge 一個保證唯一的遞增 ID**。

問題：在單一 translation unit 編譯時，compiler 只看得到這個 .c 的函式。要全局遞增 ID，必須等到 **link 階段**所有 .o 合起來才行。這就是 LTO（Link-Time Optimization）的任務。

AFL++ 的 `afl-clang-lto` 流程：

```
.c ──clang -flto──▶ .o (含 LLVM bitcode, 尚未 emit machine code)
                        │
                        ▼
    所有 .o → lld --lto-*-pass-plugin=afl-lto-pass.so → 最終 binary
                        │
                        │ LTO pass 能看到所有 function、
                        │ 所有 basic block；分配遞增 ID
                        │ 保證 prev^cur 不會 collision
                        ▼
                  insert instrumentation
```

`instrumentation/SanitizerCoverageLTO.so.cc` 這個 pass 在 link 時跑，主要做兩件事：

1. **分配全局唯一 ID**：掃過所有 block，分 ID 1, 2, 3, ...。bitmap 大小自動設為 ID 上限的下一個 2 的冪。
2. **優化 `prev^cur`**：既然 ID 連續，可以證明某些 pair 之間不會碰撞，進一步把某些 block 的 instrumentation 省略（已知 single-predecessor 的 block 不需要追 prev）。

實務上 LTO mode 的 bitmap size 會自動調（能看到全部 block，精準算）。編譯命令長這樣：

```bash
CC=afl-clang-lto \
CXX=afl-clang-lto++ \
AR=llvm-ar-14 \
RANLIB=llvm-ranlib-14 \
make
```

為什麼要換 `AR` 和 `RANLIB`？因為 LTO 的 `.o` 裡放的是 LLVM bitcode，而不是 ELF — GNU `ar` 看不懂會壞，要用 llvm 版。

### Auto-dictionary：LTO 額外送的禮物

因為 LTO 看得到完整 compare 指令，它還能**靜態抽出**所有字串常數與大數字常數，自動寫進 dictionary 給 fuzzer 用。比起手動 `-x dict.txt` 強太多。Ch 11 詳述。

## 效能與 collision 率

AFL++ 官方和多篇論文（CollAFL、AFL++ WOOT 2020）實測對比，大致：

| Mode | 相對速度 | Collision 率 | Build 複雜度 |
|---|---|---|---|
| `afl-as` | 0.5–0.7x | 高（隨機 ID） | 低 |
| `afl-gcc-fast` (GCC plugin) | 1.0x | ~3–10% | 中 |
| `afl-clang-fast` (PCGUARD) | 1.0–1.1x | ~3–10% | 中 |
| `afl-clang-lto` | 1.0–1.2x | **0%** | 高（要 llvm-ar、完整 source） |

差距不大的地方是 runtime 速度（兩行 C 都很便宜），主要差別在**能不能精準覆蓋**。能用 LTO 就用 LTO，除非遇到 build system 不配合。

## 選擇指南

| 情境 | 建議 |
|---|---|
| 小型 CLI tool、新 project | `afl-clang-lto` |
| 大型 C project（e.g. ffmpeg、openssl） | `afl-clang-lto`（多下點工夫 patch build system） |
| 純 C++（template 爆炸、link 時間難忍） | `afl-clang-fast`（PCGUARD） |
| 只有 GCC（e.g. Linux kernel） | `afl-gcc-fast` |
| 二進位 only（沒 source） | Ch 6 的 runtime mode |
| 古董系統、compiler 過舊 | `afl-as`（最後手段） |

## 幾個重要環境變數

不記全，但常用的幾個：

| Env | 作用 |
|---|---|
| `AFL_CC_COMPILER=LTO` / `LLVM` / `GCC` / `GCC_PLUGIN` | 切 backend |
| `AFL_LLVM_INSTRUMENT=PCGUARD` / `CLASSIC` / `NGRAM-8` / `CTX` | instrumentation 風格 |
| `AFL_LLVM_LAF_ALL=1` | 啟用 laf-intel 的 compare 拆分 |
| `AFL_LLVM_DICT2FILE=/tmp/auto.dict` | LTO mode 自動 dict 寫到檔 |
| `AFL_LLVM_CMPLOG=1` | 編第二份 CMPLOG binary |
| `AFL_HARDEN=1` | 加 `-fstack-protector-all` 等強化 flag |
| `AFL_USE_ASAN=1` | 一併開 ASan |

Build 失敗時第一時間看這些 env 有沒有衝突，比去翻 afl-cc.c source code 快。

## 常見誤解

- **「`afl-clang-fast` 預設就是 LTO」**：不是。`afl-clang-fast` 用 PCGUARD，`afl-clang-lto` 才是 LTO。兩個是不同 binary。
- **「LTO build 失敗只能放棄」**：先試 `AFL_LLVM_ALLOWLIST`/`DENYLIST` 排除有問題的檔案；實在不行退 PCGUARD，差幾個百分點而已。
- **「GCC 什麼都插不了」**：GCC plugin 版（`afl-gcc-fast`）功能幾乎對齊 `afl-clang-fast`，只差沒有 LTO。Linux kernel fuzzing 就靠它。

## 自我檢核

- [ ] 能區分 `afl-as` / GCC plugin / LLVM pass / LTO pass 四種作用階段
- [ ] 能寫出 AFL instrumentation 的核心三行 C（`++`、`prev_loc = cur_loc >> 1`）
- [ ] 能解釋為什麼要 LTO 才能做 collision-free
- [ ] 知道 LTO mode 會自動產 dictionary、為什麼要換 `AR`
- [ ] 看得懂 `AFL_CC_COMPILER`、`AFL_LLVM_INSTRUMENT` 是切什麼

下一章看沒 source 怎麼辦 — QEMU mode、Frida mode、Unicorn mode 三種執行期 instrumentation。

→ [Ch 6 執行期 instrumentation：沒原始碼怎麼辦](./06-runtime-instrumentation.md)
