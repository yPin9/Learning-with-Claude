# Ch 44 — OSS-Fuzz：把開源專案接上 Google 的持續 fuzzing 基礎設施

> **目標：** 理解 OSS-Fuzz 的整個流水線架構，能為一個開源專案撰寫 project.yaml、Dockerfile、build.sh、fuzz target，並在本機透過 helper.py 執行完整的 build/run/check 週期；進一步了解 ClusterFuzzLite 如何把同一套機制搬進自己的 CI。
>
> **環境：** Docker（OSS-Fuzz helper.py 用）、clang/libFuzzer（本地 fuzz target 驗證）。本機 Docker daemon 若未啟動，OSS-Fuzz helper.py 相關步驟標注「**本段未實測，為理論預期行為**」；libFuzzer fuzz target 的編譯與執行在有 clang 的 WSL2 環境均可真跑。

## 為什麼需要 OSS-Fuzz

afl++ 跑 24 小時之後你去睡覺，fuzzer 也停了。真實世界的 CVE hunting 需要的是**連續數個月、數千核心小時**的持續 fuzzing——每次有新 commit 就重跑、crash 自動 dedup、regression 自動 bisect、覆蓋率報表每天更新。這是個人工程師手動維護做不到的規模。

OSS-Fuzz 是 Google 2016 年開放的持續 fuzzing 服務，目前（2026）已涵蓋超過 1,000 個開源專案，累計發現超過 11,000 個漏洞（包含 OpenSSL、curl、FFmpeg、systemd 等）。它解決的不是「怎麼寫更好的 fuzzer」，而是「**誰來持續跑 fuzzer**」這個工程問題。

更重要的一點：OSS-Fuzz 上那些長期跑過幾億核心小時的專案，所有「容易找」的 bug 早就被找光了。如果你目標是找新 CVE，要不就去找**還沒接入 OSS-Fuzz** 的專案，要不就得比 OSS-Fuzz 用更聰明的策略（Part 8 的 hybrid/directed 就是為此）。

## 先建立直覺：整個流水線

```
開發者 push commit
        │
        ▼
  OSS-Fuzz GitHub Actions trigger
        │
        ▼
┌───────────────────────────────────┐
│  Docker 容器（per-project）        │
│  base-builder image               │
│    ├── build.sh   → 編 fuzz target │
│    └── Dockerfile → 安裝依賴       │
└───────────────────────────────────┘
        │ fuzz target binaries
        ▼
┌───────────────────────────────────┐
│  ClusterFuzz（後端）               │
│  ├── 分發到數千個 worker VM        │
│  ├── 跑 libFuzzer / AFL++ / honggfuzz
│  ├── crash dedup (stack hash)     │
│  ├── bisect regression            │
│  └── 通知開發者                   │
└───────────────────────────────────┘
        │
        ▼
  syzbot dashboard / bug tracker
  （crash report + reproducer）
```

三個你要寫的檔案住在 `oss-fuzz/projects/<project_name>/`：

| 檔案 | 用途 |
|------|------|
| `project.yaml` | 宣告專案 metadata（語言、主要聯絡人、fuzzing engine、sanitizer 清單） |
| `Dockerfile` | 從 base-builder 繼承，安裝 build 依賴，clone 原始碼 |
| `build.sh` | 在容器內把 target 編成 fuzz binary，必須用 `$OUT`、`$LIB_FUZZING_ENGINE` |

你自己的 fuzz target 通常住在**被 fuzz 的專案**的 source tree 裡（`fuzz/` 或 `test/fuzzing/`），在 `build.sh` 裡引用它。

## 核心概念：project.yaml

```yaml
homepage: "https://example.com/myproject"
language: c++
primary_contact: security@example.com
auto_ccs:
  - dev@example.com
fuzzing_engines:
  - libfuzzer
  - afl
  - honggfuzz
sanitizers:
  - address
  - memory
  - undefined
architectures:
  - x86_64
  - i386
```

- `language` 決定用哪個 base-builder image（`gcr.io/oss-fuzz-base/base-builder` 或 `base-builder-go`、`base-builder-rust`）
- `fuzzing_engines` 列出 OSS-Fuzz 要用哪些 fuzzer 跑你的 target——libfuzzer 是預設，AFL++ 和 honggfuzz 也由 OSS-Fuzz 維護的 wrapper 提供
- `sanitizers` 決定編譯 flag：ASan 對應 `-fsanitize=address`，MSan 對應 `-fsanitize=memory`，UBSan 對應 `-fsanitize=undefined`

## 核心概念：Dockerfile

```dockerfile
FROM gcr.io/oss-fuzz-base/base-builder

# 安裝 build 依賴
RUN apt-get update && apt-get install -y \
    cmake \
    libssl-dev \
    zlib1g-dev

# Clone 目標專案
RUN git clone --depth 1 https://github.com/example/myproject.git

# 把 build script 和 fuzz target 複製進來
COPY build.sh $SRC/
COPY fuzz_*.cc $SRC/myproject/fuzz/
```

重點：base-builder image 已預裝 `$CC`（clang）、`$CXX`（clang++）、`$CFLAGS`（包含 coverage instrumentation flags）。你的 build.sh 必須用這些環境變數而不是硬寫 clang，否則 sanitizer 切換就會壞掉。

## 核心概念：build.sh

```bash
#!/bin/bash -eu

# 進入專案目錄
cd $SRC/myproject

# 用 OSS-Fuzz 提供的 $CC/$CXX/$CFLAGS/$LIB_FUZZING_ENGINE 編譯
cmake . \
  -DCMAKE_C_COMPILER=$CC \
  -DCMAKE_CXX_COMPILER=$CXX \
  -DCMAKE_C_FLAGS="$CFLAGS" \
  -DCMAKE_CXX_FLAGS="$CXXFLAGS" \
  -DBUILD_SHARED_LIBS=OFF

make -j$(nproc)

# 編譯 fuzz target，連結 libFuzzer
$CXX $CXXFLAGS -I. \
    fuzz/fuzz_parse.cc \
    libmyproject.a \
    $LIB_FUZZING_ENGINE \
    -o $OUT/fuzz_parse

# 打包 seed corpus
zip -j $OUT/fuzz_parse_seed_corpus.zip \
    fuzz/seeds/*.bin 2>/dev/null || true
```

`$LIB_FUZZING_ENGINE` 是關鍵：libfuzzer 模式下它是 `-fsanitize=fuzzer`，AFL++ 模式下它是 AFL++ 的 runtime lib，honggfuzz 模式下同理。**不要 hardcode `-fsanitize=fuzzer`**，否則換 fuzzer engine 就炸。

## 核心概念：fuzz target（LLVMFuzzerTestOneInput）

libFuzzer 的 fuzz target API 只有一個函式：

```c
// fuzz_parse.cc
#include <stdint.h>
#include <stddef.h>
#include "myproject/parser.h"

extern "C" int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    Parser p;
    p.parse(reinterpret_cast<const char*>(data), size);
    // 永遠 return 0（非零代表「丟棄這個 input，不加入語料」）
    return 0;
}
```

幾個設計要點：

1. **不要 call `exit()` 或 `abort()`**：讓 sanitizer 或 crash signal 負責終止，target 只管餵資料。
2. **不要有 global mutable state**：fuzz target 會被反覆呼叫數百萬次，初始化應該用 `LLVMFuzzerInitialize` 做一次。
3. **oracle 要清楚**：ASan/MSan/UBSan 是你的 oracle，不要加 assert 然後把 segfault 掩蓋掉。

`LLVMFuzzerInitialize` 用法（只在程式啟動時呼叫一次）：

```c
extern "C" int LLVMFuzzerInitialize(int *argc, char ***argv) {
    SSL_library_init();   // 例如初始化 OpenSSL
    return 0;
}
```

## 本地開發：helper.py

OSS-Fuzz 提供 `infra/helper.py` 讓你在本地模擬整個 CI 流程：

```bash
# clone OSS-Fuzz
git clone https://github.com/google/oss-fuzz.git
cd oss-fuzz

# 三個核心指令：
python3 infra/helper.py build_fuzzers myproject   # 建 Docker image + 編 target
python3 infra/helper.py run_fuzzer myproject fuzz_parse   # 本地跑 fuzzer
python3 infra/helper.py check_build myproject     # 驗證符合 OSS-Fuzz 要求
```

**本段未實測，為理論預期行為**（本機 Docker daemon 未啟動）。驗證步驟：

```bash
# 1. 確認 Docker daemon 正在執行
docker info

# 2. 第一次 build 會 pull base-builder image（約 2GB），之後 cached
python3 infra/helper.py build_fuzzers --sanitizer address myproject

# 預期輸出（概要）：
# INFO:root:Running: docker build ... -t gcr.io/oss-fuzz/myproject ...
# INFO:root:Running: docker run ... compile
# INFO:root:Fuzz targets built successfully.

# 3. 跑 fuzzer，輸出會有 libFuzzer 的 stat line
python3 infra/helper.py run_fuzzer myproject fuzz_parse \
    -- -max_total_time=60 -print_final_stats=1

# 預期輸出（概要）：
# #0      READ units: 1
# #1      INITED cov: 43 ft: 44 corp: 1/1b exec/s: 0 rss: 27Mb
# #256    NEW    cov: 51 ft: 55 corp: 2/4b exec/s: 0 rss: 27Mb
# ...
# Done 50000 runs in 1 second(s)
```

## 本地直接跑 libFuzzer（繞過 Docker）

如果只是想驗證 fuzz target 本身，可以跳過 Docker，用本地 clang 直接編：

```bash
# 編譯 fuzz target（含 ASan + libFuzzer）
clang++ -g -O1 \
    -fsanitize=address,fuzzer \
    fuzz_parse.cc \
    -L. -lmyproject \
    -o fuzz_parse

# 建立 seed corpus 目錄
mkdir -p corpus seeds
echo 'hello' > seeds/seed0.txt

# 跑（Ctrl-C 停止）
./fuzz_parse corpus/ seeds/ \
    -max_total_time=60 \
    -print_final_stats=1

# 發現 crash 時輸出形如：
# ==12345==ERROR: AddressSanitizer: heap-buffer-overflow on address 0x...
# ...
# artifact_prefix='./'; Test unit written to ./crash-abc123
```

## 底層機制：OSS-Fuzz 的 build 環境

```
helper.py build_fuzzers
        │
        ▼
docker build -t gcr.io/oss-fuzz/<project> .
  （執行 Dockerfile）
        │
        ▼
docker run ... compile
  （執行 build.sh，注入環境變數）
  ┌────────────────────────────────────────────┐
  │ CC=clang                                   │
  │ CXX=clang++                                │
  │ CFLAGS=-O1 -fno-omit-frame-pointer         │
  │        -fsanitize=address                  │
  │        -fsanitize-coverage=trace-pc-guard, │
  │          indirect-calls,trace-cmp          │
  │ LIB_FUZZING_ENGINE=-fsanitize=fuzzer       │
  │ OUT=/out                                   │
  │ SRC=/src                                   │
  │ WORK=/work                                 │
  └────────────────────────────────────────────┘
        │
        ▼
/out/ 裡有：
  fuzz_parse          ← fuzz target binary
  fuzz_parse.options  ← libFuzzer flags（可選）
  fuzz_parse_seed_corpus.zip ← 初始 seed corpus
```

`-fsanitize-coverage=trace-pc-guard,indirect-calls,trace-cmp` 是 libFuzzer 的 coverage instrumentation：
- `trace-pc-guard`：每個 edge 一個 guard，是主要 coverage feedback
- `indirect-calls`：indirect call target 也算 coverage
- `trace-cmp`：比較指令的兩個運算元值記下來，用於 magic byte 推斷，讓 fuzzer 能「猜」到 checksum

## ClusterFuzzLite：把 OSS-Fuzz 搬進自己的 CI

不是所有專案都符合 OSS-Fuzz 的接入條件（需要 Google 審查、開源、有足夠影響力）。ClusterFuzzLite 是 2021 年發布的精簡版，讓你在自己的 GitHub Actions / GitLab CI 裡跑同一套機制：

```yaml
# .github/workflows/cfl-run.yml
name: ClusterFuzzLite
on:
  push:
    branches: [main]
  schedule:
    - cron: '0 3 * * *'   # 每天凌晨 3 點跑批次 fuzzing

jobs:
  Build-Fuzzers:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Build Fuzzers
        uses: google/clusterfuzzlite/actions/build_fuzzers@v1
        with:
          language: c++
          sanitizer: address

  Run-Fuzzers:
    needs: Build-Fuzzers
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run Fuzzers
        uses: google/clusterfuzzlite/actions/run_fuzzers@v1
        with:
          github-token: ${{ secrets.GITHUB_TOKEN }}
          fuzz-seconds: 600        # 每次 CI 跑 10 分鐘
          mode: 'code-change'      # 只跑與本次 commit 相關的 target
          sanitizer: address
```

ClusterFuzzLite 的 corpus 存在 GitHub artifact 或 GCS bucket，每次 CI 跑完會把新發現的語料上傳，下次接著用。這是把 fuzzing 變成 CI 常規一環的最低成本方式。

## 進階用法：FuzzedDataProvider

raw bytes 往往不夠用，FuzzedDataProvider 讓你從 fuzzer 的輸入切出有型別的資料：

```c
#include <fuzzer/FuzzedDataProvider.h>

extern "C" int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    FuzzedDataProvider fdp(data, size);

    int version   = fdp.ConsumeIntegralInRange<int>(1, 10);
    bool compress = fdp.ConsumeBool();
    std::string payload = fdp.ConsumeRemainingBytesAsString();

    process(version, compress, payload.data(), payload.size());
    return 0;
}
```

這比手動解析 `data[0]`、`data[1]` 乾淨，而且 libFuzzer 看到的還是連續 bytes，mutation 效果不受影響。

## 對比取捨

| 方案 | 規模 | 成本 | 接入門檻 | 適用場景 |
|------|------|------|----------|---------|
| OSS-Fuzz | 數千核心 × 7×24 | 免費（Google 出） | 需 Google 審查，須為知名開源 | 已建立的開源 library |
| ClusterFuzzLite | CI runner（幾核） | GitHub Actions 免費額度 | 任何 GitHub 專案 | 自己的開源或早期閉源 |
| 自建 fuzzing infra | 自行控制 | 需自費機器 | 低 | 企業內部、閉源 |
| 本地 libFuzzer | 1 機器 | 0 | 最低 | 開發驗證、新 target 原型 |

## 踩雷

**踩雷 1：build.sh 用 hardcode clang 路徑**
錯誤直覺：「反正 OSS-Fuzz 用的就是 clang，hardcode 沒差。」
正確：`$CC`/`$CXX` 在不同模式下指向不同 wrapper。AFL++ 模式下 `$CC` 是 `afl-clang-fast`，若你 hardcode `clang` 就沒有 AFL++ 的 edge map instrumentation，等於用了 dumb AFL——fuzzer 跑起來看起來正常，但 coverage-guided 的效果完全沒有。

**踩雷 2：fuzz target 裡過度 filter 輸入**
錯誤直覺：「遇到格式不符的輸入，return -1 告訴 fuzzer 丟掉，省得浪費時間。」
正確：你 return -1 的那些輸入裡可能藏著 crash 的路徑——就是那些「格式稍微不對」的邊界情況最容易觸發 parser 的 out-of-bounds。只有在 harness 本身有昂貴副作用（例如建立真實網路連線）時才考慮 filter。

**踩雷 3：忘記打包 seed corpus**
錯誤直覺：「libFuzzer 會從空開始跑，seed 不重要。」
正確：對有複雜格式要求的目標（protobuf、PDF、PNG），沒有有效 seed，libFuzzer 可能要花幾小時才能湊出第一個通過格式驗證的輸入，這段時間 coverage 幾乎不增長。用 `zip -j $OUT/${fuzzer_name}_seed_corpus.zip seeds/*.bin` 打包幾個真實格式樣本，效果立竿見影。

**踩雷 4：fuzz target 每次都重新初始化**
錯誤直覺：「每次呼叫 LLVMFuzzerTestOneInput 都初始化一遍，反正都是 in-process 很快。」
正確：libFuzzer 每秒可能呼叫你的 target 數萬次。若初始化（例如解密 key、載入 schema 檔案、建 regex engine）每次都做，吞吐量會掉一到兩個數量級。用 static local 或 `LLVMFuzzerInitialize` 做一次性初始化。

## 進階延伸

- **OSS-Fuzz Introspector**：分析 fuzz target 的靜態 call graph，找出「哪些函式從未被 fuzzer 呼叫到」，協助改進 harness 覆蓋範圍。`helper.py introspector myproject` 可本地跑（需 Docker）。
- **持久模式 vs fork server**：libFuzzer 本身是 in-process 持久模式（沒有 fork）。如果你的 target 有不可恢復的 global state，可用 LibAFL 的 in-process-fork executor，每 N 次重 fork 一次清除 state。
- **coverage-only build**：用 `-fsanitize=fuzzer-no-link` 建一個只有 coverage 不跑 fuzzing 的 binary，搭配 `llvm-cov` 做報表，確認 target 覆蓋到哪些函式。

## 動手練習

1. 在有 clang 的 WSL2 上，用 `libpng` 或 `zlib` 寫一個最小 `LLVMFuzzerTestOneInput`，用 `clang++ -fsanitize=address,fuzzer` 編譯後跑 5 分鐘，觀察 `cov:` 和 `ft:` 兩個數字的增長趨勢。
2. 瀏覽 `https://github.com/google/oss-fuzz/tree/master/projects/` 找一個你熟悉語言的現有 project，讀它的 Dockerfile 和 build.sh，列出三個「如果是我來寫會漏掉的細節」。
3. 在 GitHub 上建一個 repo，加入 ClusterFuzzLite workflow，讓它在 push 時自動跑 60 秒 fuzzing，確認 Actions 有執行並輸出 libFuzzer 的 stat 行。

## 本章重點

- OSS-Fuzz 的核心貢獻是「持續 fuzzing 的工程基礎設施」，不是新演算法
- 三個檔案（project.yaml / Dockerfile / build.sh）加上 fuzz target，構成整個接入點
- `$CC`、`$CXX`、`$CFLAGS`、`$LIB_FUZZING_ENGINE` 是 build.sh 的絕對 API，不能 hardcode
- helper.py 是本地測試的正確工具，不要等 PR merge 才發現 build 壞掉
- ClusterFuzzLite 把同一套機制帶進任何 GitHub Actions CI，接入門檻極低

## 自我檢核

- [ ] 我能說出 project.yaml 的三個必要欄位及其作用
- [ ] 我能解釋 `$LIB_FUZZING_ENGINE` 為什麼不能寫死 `-fsanitize=fuzzer`
- [ ] 我能寫一個最小可用的 `LLVMFuzzerTestOneInput`，並說明 return 0 vs return -1 的語義
- [ ] 我知道 `helper.py build_fuzzers` / `run_fuzzer` / `check_build` 各做什麼
- [ ] 我能說出 OSS-Fuzz 和 ClusterFuzzLite 的適用場景差異
- [ ] 我能說明 seed corpus 的命名慣例（`${fuzzer_name}_seed_corpus.zip`）

## 延伸閱讀

1. **[OSS-Fuzz: Five months later, and rewarding fuzzing bugs](https://security.googleblog.com/2017/05/oss-fuzz-five-months-later-and.html)** — Google Security Blog 2017
   讀哪段：整篇（短文）；學什麼：Google 最初推出 OSS-Fuzz 的動機與早期成果，是理解「為什麼規模化持續 fuzzing 比偶發 fuzzing 重要」的一手素材。關聯：本章動機節的背景數據來源。

2. **[ClusterFuzzLite documentation](https://google.github.io/clusterfuzzlite/)** — Google 官方文件
   讀哪段：「Integrating into GitHub Actions」一節；學什麼：YAML workflow 的正確寫法，特別是 `mode: 'code-change'` vs `mode: 'batch'` 的差異，以及 corpus 儲存到 GitHub artifact 的具體機制。關聯：ClusterFuzzLite 小節的實作細節。

3. **[FuzzGen: Automatic Fuzzer Generation](https://www.usenix.org/conference/usenixsecurity20/presentation/ispoglou)** — USENIX Security 2020
   讀哪段：Section 3（consumer analysis 如何自動推斷 API usage sequence）；學什麼：「如何自動化撰寫 fuzz harness」的學術方向，與本章手寫 harness 形成對比——碰到有幾百個 API 的 library，手寫 harness 的局限性在哪。關聯：fuzz target 設計的延伸思考。

→ [下一章：ClusterFuzz 與 corpus 管理](./45-clusterfuzz-corpus.md)
