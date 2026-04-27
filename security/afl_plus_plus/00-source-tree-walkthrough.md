# Ch 0 — 從原始碼 build AFL++：先看它有哪些元件

> 目標：掃過一次 AFL++ 的 source tree，知道 `src/`、`instrumentation/`、`qemu_mode/`、`frida_mode/`、`custom_mutators/` 各自負責什麼，讓後面章節提到檔案路徑時能對得上。

## 為什麼要從 source 看起

AFL++ 表面上是一個 binary（`afl-fuzz`）加一個 compiler wrapper（`afl-cc`），但打開原始碼會發現，它是個合併了十幾篇論文 idea 的瑞士刀。後面章節一直會說「CMPLOG 在這裡做事」「LTO pass 在這個檔案裡賦予 edge ID」，你需要先知道檔案大致長在哪。

順便一個警告：**發行版打包的 AFL++ 通常落後主線半年以上**，而這個領域半年足以換代。本教材以主線 `stable` 分支為準，請自己 build：

```bash
git clone https://github.com/AFLplusplus/AFLplusplus
cd AFLplusplus
make distrib      # 連 QEMU mode、Frida mode、custom mutators 一起 build
sudo make install
```

`make distrib` 大約 10–20 分鐘。如果只要核心 fuzzer，用 `make all` 快很多。

## Source tree 一張圖

```
AFLplusplus/
├── src/                         # afl-fuzz 主程式、afl-cc、afl-showmap...
│   ├── afl-fuzz.c               # 進入點與主 event loop
│   ├── afl-fuzz-state.c         # 全局狀態結構 afl_state_t 初始化
│   ├── afl-fuzz-queue.c         # queue entry 管理、cull_queue()
│   ├── afl-fuzz-one.c           # 一次 fuzzing iteration 的大 pipeline
│   ├── afl-fuzz-bitmap.c        # has_new_bits()、simplify_trace()
│   ├── afl-fuzz-mutators.c      # mutator 介面、custom mutator loader
│   ├── afl-fuzz-extras.c        # dictionary 載入
│   ├── afl-fuzz-redqueen.c      # CmpLog / input-to-state 替換
│   ├── afl-fuzz-run.c           # common_fuzz_stuff()：執行一次 target
│   ├── afl-forkserver.c         # 和 target 中的 forkserver 溝通
│   ├── afl-cc.c                 # 編譯器 wrapper 主體
│   └── afl-showmap.c / afl-tmin.c / afl-cmin.c ...
├── include/                     # 共享 header
│   ├── config.h                 # MAP_SIZE_POW2、FORKSRV_FD、各種魔數
│   ├── types.h
│   └── forkserver.h
├── instrumentation/             # compiler 側：LLVM pass + runtime
│   ├── afl-compiler-rt.o.c      # runtime：forkserver、__AFL_INIT、bitmap 更新
│   ├── afl-llvm-pass.so.cc      # 傳統 inline instrumentation pass
│   ├── SanitizerCoverageLTO.so.cc    # LTO 模式的 collision-free pass
│   ├── SanitizerCoveragePCGUARD.so.cc
│   ├── cmplog-instructions-pass.cc   # CmpLog instruction-level hook
│   ├── cmplog-routines-pass.cc       # CmpLog function hook (strcmp 等)
│   ├── compare-transform-pass.so.cc  # 拆 `x == 0xDEADBEEF` 成 byte-wise (laf-intel)
│   └── split-switches-pass.so.cc
├── qemu_mode/                   # 改過的 QEMU，做動態插樁
├── frida_mode/                  # Frida Stalker-based 動態插樁
├── unicorn_mode/                # Unicorn engine 嵌入
├── nyx_mode/                    # Snapshot fuzzing (hypervisor-based)
├── custom_mutators/             # 內建 grammar mutators、honggfuzz 風格 mutator
│   ├── gramatron/
│   ├── grammar_mutator/
│   └── honggfuzz/
├── dictionaries/                # 常用格式 dict：png.dict、xml.dict ...
├── test-instr.c                 # 最基本的 smoke test target
└── docs/
```

後面每一章都會從這張圖裡挑一個資料夾或一個檔案深入。

## Build 完會有哪些 binary

Install 後 `/usr/local/bin/` 會長出一堆東西。分三組看比較清楚：

### Fuzzer 本體

| 執行檔 | 用途 |
|---|---|
| `afl-fuzz` | 主 fuzzer |
| `afl-showmap` | 給一個 input，跑一次，把 bitmap dump 出來 |
| `afl-tmin` | 把 crash input 縮到最小（delta debugging） |
| `afl-cmin` | 把 corpus 縮到最小（set cover on coverage） |
| `afl-analyze` | 分析 input 的哪些 byte 對 coverage 有影響 |

### 編譯器 wrapper

| 執行檔 | 底層 |
|---|---|
| `afl-cc` / `afl-c++` | 統一入口，依環境變數分流 |
| `afl-gcc-fast` / `afl-g++-fast` | GCC + AFL 的 GCC plugin |
| `afl-clang-fast` / `afl-clang-fast++` | Clang + AFL 的 LLVM pass（PCGUARD） |
| `afl-clang-lto` / `afl-clang-lto++` | Clang + LTO + collision-free instrumentation |
| `afl-as` (deprecated) | 組譯階段 wrapper，相容性 only |

`afl-cc` 是總入口，讀 `AFL_CC_COMPILER` / `AFL_LLVM_INSTRUMENT` 等環境變數決定走哪條：

```bash
export AFL_CC_COMPILER=LTO           # 或 LLVM、GCC、GCC_PLUGIN
export AFL_LLVM_INSTRUMENT=PCGUARD   # 或 CLASSIC、NGRAM-8、CTX
afl-cc -o target target.c
```

### Mode 輔助執行檔

| 執行檔 | 用途 |
|---|---|
| `afl-qemu-trace` | QEMU mode 的 target launcher |
| `afl-frida-trace.so` | Frida mode 注入的 .so |
| `afl-network-client` / `afl-network-server` | 無法直接 fuzz 的 target 做 I/O proxy |

## 一個最小例子的流程

為了讓這張圖有血肉，走一次 `test-instr.c` 的命運：

1. **編譯**：`afl-clang-fast -o test test-instr.c`
   - `afl-cc` 讀 `AFL_CC_COMPILER`，沒設就走 LLVM
   - 呼叫 clang，載入 `SanitizerCoveragePCGUARD.so`
   - Link `afl-compiler-rt.o`（forkserver + bitmap runtime）
2. **執行**：`afl-fuzz -i seeds/ -o out/ -- ./test`
   - `afl-fuzz` 開 shared memory 要一塊 64KB
   - Fork `./test` 子程序，把 SHM id 透過 `__AFL_SHM_ID` env 傳過去
   - `afl-compiler-rt` 的 constructor 接管，在 `main()` 前起 forkserver
   - 之後每個 iteration，forkserver fork 乾淨 child，child 跑 target，instrumentation 寫 bitmap
3. **觀察**：TUI 上每個數字都對應 `src/afl-fuzz-stats.c` 的某個欄位

這條鏈後面每一章會拆一塊。

## 常見誤解

- **「AFL++ 就是 AFL 加幾個 feature」**：不是。AFL++ 的 scheduling、bitmap 處理、mutator 抽象、LLVM pass 幾乎都重寫過，只是對外 CLI 保留相容。
- **「`afl-gcc` 和 `afl-gcc-fast` 差不多」**：差很多。`afl-gcc` 走 `afl-as` 做組譯級文字 rewrite，慢、相容性差，已 deprecated。`afl-gcc-fast` 是 GCC plugin，效能對齊 `afl-clang-fast`。
- **「QEMU mode 和編譯時插樁效果一樣」**：不一樣。QEMU mode overhead 2–5x，bitmap 準確度受 basic block 粒度影響。有 source 永遠先選編譯時。

## 動手之前

這一章沒有作業。但建議你：

```bash
cd AFLplusplus
ls src/ | head -20
wc -l src/afl-fuzz-queue.c
wc -l instrumentation/SanitizerCoverageLTO.so.cc
```

感受規模。`afl-fuzz-queue.c` 約 1500 行，`SanitizerCoverageLTO.so.cc` 約 2000 行 — 這是後面章節的主戰場。

## 自我檢核

- [ ] 知道 `src/` 放 fuzzer 本體、`instrumentation/` 放 compiler 側 pass 與 runtime
- [ ] 能說出 `afl-cc` 和 `afl-clang-fast` / `afl-clang-lto` 的關係
- [ ] 記得 `AFL_CC_COMPILER` 是切換 backend 的總開關
- [ ] 知道要用 git 主線版，不要用 distro 打包版

下一章開始進真正的主題 — 為什麼會有 coverage-guided fuzzing 這東西。

→ [Ch 1 Fuzzing 三種流派](./01-fuzzing-landscape.md)
