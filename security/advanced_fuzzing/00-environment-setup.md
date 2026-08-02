# Ch 0 — 環境搭建

> **目標**：把本課所有 Part 需要的工具裝進 WSL2 Ubuntu，跑一個能真正找到 crash 的 libFuzzer 目標確認環境健康，同時對「哪些東西在這台機器上跑不起來」有清醒認識。
>
> **環境**：WSL2 Ubuntu 22.04.3 LTS，kernel 6.18.33.2-microsoft-standard-WSL2，Intel i7-10700（vmx/VT-x 可見，`/dev/kvm` 存在）。版本數字以本章結尾的實測輸出為準，之後各章以此為基線。

---

## 為什麼要先把環境搞對

fuzzing 課的惡夢場景：花一週讀完原理，開始動手，發現 `clang -fsanitize=fuzzer` 版本不對、cargo 缺了某個 nightly feature、syzkaller 抱怨找不到 vmlinux——接著花三天救環境而不是學東西。

本章的任務是把這些問題提前解決：每個工具裝完都跑一個 sanity check，確認它能做到本課要用的那件最核心的事，而不是裝完看 `--version` 就算過。同時也要先講清楚哪些目標因為硬體或核心限制，在這台機器上只能讀理論、不能親手跑。

---

## 先建立直覺：本課的工具地圖

本課每個 Part 依賴不同的工具鏈，各自有不同的前置條件：

```
本課工具依賴圖

Part 1 (LibAFL)       ──► Rust stable + cargo   ──► 裝了就跑
Part 2 (grammar)      ──► LLVM/Clang + Python    ──► 裝了就跑
Part 3 (AFLNet)       ──► gcc + make + 網路庫    ──► 裝了就跑
Part 4 (syzkaller)    ──► Go + QEMU + KVM kernel ──► 部分實測（WSL KVM 受限）
Part 5 (Nyx/kAFL)    ──► VT-x + Intel PT         ──► 架構解析為主（WSL 不支援 PT）
Part 6 (firmware)     ──► unicorn + Python        ──► 裝了就跑
Part 7 (Fuzzilli)     ──► Rust + patched V8/JSC   ──► 流程可跑，build 耗時
Part 8 (hybrid)       ──► SymCC (clang plugin)    ──► 裝了就跑
```

工具分三類：

1. **今天裝、今天跑**：LLVM/Clang、Rust/cargo、unicorn-python
2. **今天裝、Part 到了再細調**：Go（syzkaller 用）、QEMU、libprotobuf-mutator
3. **受硬體/kernel 限制、本課用理論解析補**：Nyx/kAFL（需 bare-metal Intel PT）、全系統 syzkaller（需 KVM + 自 build kernel image）

---

## Step 1：基礎系統套件

```bash
sudo apt update && sudo apt upgrade -y

# LLVM 14（Ubuntu 22.04 官方倉庫最穩定版本）
sudo apt install -y \
    clang clang-14 clang-tools-14 \
    llvm-14 llvm-14-dev llvm-14-tools \
    libclang-14-dev \
    lld-14 \
    build-essential git cmake ninja-build \
    python3 python3-pip python3-dev \
    pkg-config curl wget unzip \
    libssl-dev zlib1g-dev \
    gdb valgrind

# 確認 clang
clang-14 --version
llvm-config-14 --version
```

實測輸出（本機）：

```
Ubuntu clang version 14.0.0-1ubuntu1.1
14.0.0
```

Ubuntu 22.04 官方倉庫是 clang-14，夠跑全課所有範例。如果你在其他 distro 或想要 clang-17+，裝法見本章延伸閱讀的 LLVM APT 倉庫。

---

## Step 2：Rust 與 cargo（Part 1 LibAFL 的地基）

LibAFL 需要 Rust stable（1.75+），部分功能用 nightly。用 rustup 管理最省心：

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
source $HOME/.cargo/env

# 裝 nightly（LibAFL 部分 feature 需要）
rustup toolchain install nightly
rustup default stable  # 日常用 stable

# 有用的附加工具
cargo install cargo-audit   # 安全稽核
rustup component add clippy rustfmt
```

sanity check：

```bash
rustc --version
cargo --version
```

實測輸出（本機）：

```
rustc 1.97.1 (8bab26f4f 2026-07-14)
cargo 1.97.1 (c980f4866 2026-06-30)
```

---

## Step 3：Go（Part 4 syzkaller 的地基）

Ubuntu 22.04 官方倉庫的 Go 版本夠用，syzkaller 要求 Go 1.18+：

```bash
sudo apt install -y golang-go

go version
```

實測輸出（本機）：

```
go version go1.18.1 linux/amd64
```

syzkaller 的 build 指令在 Part 4 Ch 24 才跑，這裡只確認 Go 在位。

---

## Step 4：QEMU（Part 4/5/7 虛擬化基礎）

```bash
sudo apt install -y \
    qemu-system-x86 qemu-system-arm \
    qemu-utils qemu-kvm \
    ovmf  # UEFI firmware for QEMU

qemu-system-x86_64 --version
ls -la /dev/kvm  # 確認 KVM 可用
```

實測輸出（本機）：

```
QEMU emulator version 6.2.0 (Debian 1:6.2+dfsg-2ubuntu6.31)
crw-rw---- 1 root kvm 10, 232 Aug  1 10:34 /dev/kvm
```

本機的 WSL2 裡 `/dev/kvm` 存在（i7-10700 有 vmx，Hyper-V 允許巢狀虛擬化），syzkaller 的核心功能可以跑，但巢狀 VM 的效能比 bare-metal 差很多——Chapter 26 會說這個差距有多大。

---

## Step 5：libprotobuf-mutator（Part 2 結構感知 fuzzing）

Ubuntu 22.04 的 apt 沒有打包好的 `libprotobuf-mutator-dev`，需要從源碼 build：

```bash
sudo apt install -y libprotobuf-dev protobuf-compiler

cd /tmp
git clone https://github.com/google/libprotobuf-mutator.git
cd libprotobuf-mutator
cmake -B build -DLIB_PROTO_MUTATOR_DOWNLOAD_PROTOBUF=OFF \
      -DCMAKE_BUILD_TYPE=Release .
cmake --build build -- -j$(nproc)
sudo cmake --install build

# 確認 header 在位
ls /usr/local/include/libprotobuf-mutator/
# 應看到 mutator.h、libfuzzer_macro.h 等
```

完整使用範例在 Part 2 Ch 12，這裡只確認 build 環境。

**本段 build 指令為標準流程，如 upstream CMake API 有變動請以 GitHub README 為準。**

---

## Step 6：AFLNet（Part 3 stateful fuzzing）

```bash
sudo apt install -y libc6-dev-i386 libssl-dev libpcap-dev

cd ~/tools   # 或你偏好的工具目錄
git clone https://github.com/aflnet/aflnet.git
cd aflnet
make clean all 2>&1 | tail -5

./afl-fuzz --help 2>&1 | head -3
```

AFLNet 是 afl 的 fork，build 環境和 afl++ 基本一樣。如果你裝了 afl++，`afl-fuzz` 在 PATH 裡指向 afl++ 版本，AFLNet 要另外指路徑避免衝突。Part 3 Ch 17 會細說。

**本段為標準 build 流程，在本機未單獨執行——Step 1 的 apt 依賴都在位，若有 pcap 報錯加 `libpcap-dev`。**

---

## Step 7：unicorn（Part 6 韌體 rehosting）

```bash
pip3 install unicorn

python3 -c "import unicorn; print('unicorn', unicorn.__version__)"
```

實測輸出（本機）：

```
unicorn 2.1.2
```

unicorn 2.x Python binding 已穩定，Part 6 的 ARM Cortex-M rehosting 範例都用這版。

---

## Step 8：SymCC（Part 8 hybrid fuzzing）前置確認

SymCC 需要 clang-14 + cmake，Step 1 已裝。Part 8 Ch 41 才 build 完整 SymCC，這裡只確認 plugin build 環境：

```bash
clang-14 -v 2>&1 | grep "InstalledDir"
# 應看到 /usr/lib/llvm-14/bin

cmake --version
# 需要 3.15+；Ubuntu 22.04 apt 的 cmake 是 3.22，符合
```

---

## 誠實界線：哪些東西在這台機器跑不了

在動手之前把這個說清楚，比讀完 Part 5 才發現跑不起來更誠實。

### Nyx / kAFL（Part 5）

這兩個 snapshot fuzzer 需要**原生 Intel PT（Processor Trace）**。Intel PT 是 Intel 4th gen Core 之後的硬體追蹤功能，但在虛擬化環境（包括 WSL2 和大多數雲 VM）裡，PT 不會透傳到 Guest——它需要 Ring 0 的 PMU 存取，而 Hypervisor 不允許 Guest 操控 PMU。

本機 i7-10700 的 WSL2 kernel 沒有開啟 PT 透傳，`/sys/bus/platform/devices/intel_pt` 不存在。Part 5 以架構解析為主，給出「如果在 bare-metal 上怎麼驗證」的具體步驟作為補充。

### syzkaller 全端（Part 4）

syzkaller 需要一個**自 build 的 Linux kernel image**（帶 `CONFIG_KCOV=y CONFIG_KASAN=y CONFIG_DEBUG_INFO=y`），用 QEMU 跑這個 image，syzkaller 主機透過 SSH 控制 QEMU guest 執行 syscall 序列。

WSL2 裡 QEMU 可跑（有 `/dev/kvm`），但 syzkaller 要求的 kernel image 需要自己 `make`，這個步驟跑了大約 1–2 小時。Part 4 Ch 26 給完整 build 步驟；核心概念章節（Ch 22–25）完全可以在 WSL2 上跑，不需要 kernel image。

### Fuzzilli（Part 7）

Fuzzilli 需要一個打了 coverage instrumentation patch 的 JavaScriptCore 或 V8。Build 流程可在 WSL2 上完成，但 JSC 需要 50+ GB 磁碟、3+ 小時 build 時間。Part 7 會標出哪些步驟是本機實測的。

---

## Sanity Check：用 libFuzzer 找到第一個 crash

環境裝完，跑一個端到端驗證——從寫 harness 到真正找到 crash：

```c
/* sanity_fuzz.c */
#include <stdint.h>
#include <stddef.h>

/* 觸發條件：輸入前 4 字元為 FUZZ */
int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    if (size >= 4 &&
        data[0] == 'F' && data[1] == 'U' &&
        data[2] == 'Z' && data[3] == 'Z') {
        __builtin_trap();
    }
    return 0;
}
```

```bash
clang-14 -fsanitize=fuzzer,address -o sanity_fuzz sanity_fuzz.c
mkdir corpus
./sanity_fuzz -max_total_time=30 corpus/
```

實測輸出（本機，約 10 秒內找到 crash）：

```
INFO: Running with entropic power schedule (0xFF, 100).
INFO: Seed: 3816210608
INFO: Loaded 1 modules   (7 inline 8-bit counters): 7 [0x64a3...fef0, 0x64a3...fef7),
INFO: Loaded 1 PC tables (7 PCs): 7 [0x64a3...fef8, 0x64a3...ff68),
INFO:        0 files found in corpus/
INFO: A corpus is not provided, starting from an empty corpus
#2     INITED cov: 2 ft: 2 corp: 1/1b exec/s: 0 rss: 30Mb
#11    NEW    cov: 3 ft: 3 corp: 2/5b  lim: 4   exec/s: 0 rss: 30Mb L: 4/4 MS: 4 ...
#9858  NEW    cov: 4 ft: 4 corp: 3/10b lim: 98  exec/s: 0 rss: 31Mb L: 5/5
       MS: 2 CMP-CopyPart- DE: "F\000"-
#15193 NEW    cov: 5 ft: 5 corp: 4/13b lim: 149 exec/s: 0 rss: 32Mb L: 4/4
       MS: 3 PersAutoDict-PersAutoDict-CMP- DE: "F\000"-"U\000"-
#177052 NEW   cov: 6 ft: 6 corp: 5/108b lim: 1750 exec/s: 0 rss: 43Mb L: 95/95
        MS: 4 ... CMP- DE: "Z\000"-
==303087== ERROR: libFuzzer: deadly signal
    #0  __sanitizer_print_stack_trace
    #4  LLVMFuzzerTestOneInput (/tmp/sanity_fuzz+0x11593f)
SUMMARY: libFuzzer: deadly signal
MS: 2 CrossOver-CopyPart-
0x46,0x55,0x5a,0x5a,0xa,
FUZZ\012
artifact_prefix='./'; Test unit written to ./crash-c8ec8f720f...
```

關鍵欄位解讀：

- `cov: 2 → 6`：函式共 7 個 edge，coverage 從起點的 2 爬到 6，最後一個 edge（trap 後）才在 crash 時觸發
- `CMP- DE: "F\000"-`：libFuzzer CMP feedback 把 `'F'` 加入 persistent dictionary，之後 mutation 直接套用
- `corp: 5/108b`：5 個 corpus 種子，共 108 bytes
- crash 輸入是 `FUZZ\x0a`（`\x0a` 是 mutation 殘留，無關緊要）

環境健康確認完成。

---

## 底層機制：libFuzzer 的 instrumentation 流程

sanity check 跑起來了，它在做什麼？Ch 3 會深挖，這裡先給一個感性認識：

```
clang-14 -fsanitize=fuzzer,address 在編譯時做的事

源碼 .c
  │
  ▼  instrumentation pass
┌──────────────────────────────────────────┐
│  每個 basic block 邊界：                  │
│    call __sanitizer_cov_trace_pc_guard() │  ← edge 覆蓋計數
│                                          │
│  每個整數常量比較：                        │
│    call __sanitizer_cov_trace_const_cmp4()│ ← 比較值暴露給 mutator
└──────────────────────────────────────────┘
  │
  ▼  連結時加入
┌───────────────────┐    ┌──────────────────┐
│  libFuzzer runtime │    │  ASan runtime    │
│  ─ corpus 管理     │    │  ─ heap 監控     │
│  ─ mutator 引擎    │    │  ─ stack 監控    │
│  ─ scheduler       │    │  ─ crash 攔截    │
└───────────────────┘    └──────────────────┘
  │
  ▼  每次執行 LLVMFuzzerTestOneInput() 的循環
  1. 記錄本次跑到的 edge 集合（inline 8-bit counter bitmap）
  2. 若有新 edge → 把這個輸入加進 corpus
  3. 若 ASan 偵測到記憶體錯誤或 SIGSEGV → crash，儲存觸發輸入
  4. 根據 corpus + dictionary 產生下一個 mutation
  5. 重複
```

---

## 各 Part 快速環境對照表

| Part | 主要工具 | 安裝方式 | 本機 WSL2 可跑？ |
|------|---------|---------|----------------|
| 0 起點 | clang-14, python3 | apt | 是 |
| 1 LibAFL | Rust stable 1.97 | rustup | 是 |
| 2 Grammar | clang + libprotobuf-mutator | apt + cmake build | 是 |
| 3 AFLNet | gcc + AFLNet source | make | 是（需 source build）|
| 4 syzkaller | Go 1.18 + QEMU + kernel | apt + kernel build | 部分（無 kernel image）|
| 5 Nyx/kAFL | Intel PT + KVM module | bare-metal 才完整 | 否（PT 不透傳）|
| 6 unicorn | python3-unicorn | pip3 | 是 |
| 7 Fuzzilli | Rust + patched JSC/V8 | 數小時 build | 是，但耗時 |
| 8 SymCC | clang-14 plugin | cmake build | 是 |

---

## 踩雷集錦

**踩雷 1：`-fsanitize=fuzzer` 和 `-fsanitize-coverage=trace-pc-guard` 不能同時加**

libFuzzer 14 開始，`-fsanitize=fuzzer` 已經內含了它自己的 coverage instrumentation（inline 8-bit counters）。若再加 `-fsanitize-coverage=trace-pc-guard`，執行時報：

```
-fsanitize-coverage=trace-pc-guard is no longer supported by libFuzzer.
```

正確認識：要用自訂 coverage callback（Ch 3 那種），要改用 `-fsanitize=fuzzer-no-link` 或完全不用 libFuzzer runtime 的獨立 build。

**踩雷 2：看到 `/dev/kvm` 不代表 Intel PT 能用**

KVM 是 hardware-accelerated VM（靠 vmx 指令集），Intel PT 是獨立的硬體追蹤功能（靠 PMU）。WSL2 裡 `/dev/kvm` 存在，但 Intel PT 寄存器沒有透傳到 WSL Guest。`perf record -e intel_pt//` 在 WSL2 會失敗。kAFL/Nyx 需要的是 Intel PT，而不只是 KVM。

**踩雷 3：AFLNet 和 afl++ 的 `afl-fuzz` binary 衝突**

如果你裝了 afl++，`afl-fuzz` 在 PATH 裡指向 afl++ 版本，不認得 AFLNet 的 `-P protocol` 旗標。做法：把 AFLNet build 到獨立目錄（如 `~/tools/aflnet/`），執行時用絕對路徑 `~/tools/aflnet/afl-fuzz`，不要改 PATH。

**踩雷 4：libprotobuf-mutator cmake 選項踩坑**

`-DLIB_PROTO_MUTATOR_DOWNLOAD_PROTOBUF=ON` 會在 build 時自動下載特定版本的 protobuf，容易和系統的 `libprotobuf23` 版本衝突，連結時出現 symbol version mismatch。若系統已裝 `libprotobuf-dev`，用 `OFF` 讓 cmake 找系統版本更穩。

**踩雷 5：Rust PATH 在 WSL 單次命令不生效**

`curl | sh` 安裝 rustup 後，`~/.cargo/bin` 加到 `~/.bashrc`，但若你用 `wsl -e bash -c "..."` 單次呼叫，`~/.bashrc` 不被 source，找不到 `rustc`。驗證：`~/.cargo/bin/rustc --version`。解法：在 `~/.profile` 也加一行 `export PATH="$HOME/.cargo/bin:$PATH"`，或每次 source。

---

## 進階延伸：裝 LLVM 更新版本

Ubuntu 22.04 官方倉庫只到 clang-14。若需要 clang-17 或 18（某些 LibAFL sanitizer feature）：

```bash
wget https://apt.llvm.org/llvm.sh
chmod +x llvm.sh
sudo ./llvm.sh 17   # 或 18

# 更新 alternatives
sudo update-alternatives --install /usr/bin/clang clang /usr/bin/clang-17 100
sudo update-alternatives --install /usr/bin/clang++ clang++ /usr/bin/clang++-17 100
```

本課範例全部用 clang-14 測試，升級前確認行為一致再動。

---

## 動手練習

1. 跑 sanity check，確認在你的機器上也能看到 `SUMMARY: libFuzzer: deadly signal`。如果沒有，記錄卡在哪一步。
2. 用 `nm sanity_fuzz | grep __sanitizer_cov` 看 libFuzzer 插了哪些 coverage hook；對照上面的底層機制圖，說出每個 symbol 的作用。
3. 把 sanity check 的觸發條件改成需要 8 個字元的 magic（`FUZZING!`），觀察 `cov:` 的增長速度和找到 crash 所需的時間有何差異。記下觀察。
4. 執行 `python3 -c "import unicorn; e = unicorn.Uc(unicorn.UC_ARCH_ARM, unicorn.UC_MODE_THUMB); print('unicorn ARM ok')"` 確認 unicorn binding 正常。
5. 執行 `go env GOPATH` 和 `go version`，確認 Go 工具鏈在 PATH 裡。

---

## 本章重點

- 本課工具鏈分三層：今天裝就能跑（LLVM/Rust/unicorn）、需要 source build（libprotobuf-mutator/AFLNet/SymCC）、受硬體限制（Nyx/kAFL）。
- libFuzzer sanity check 跑通代表 clang instrumentation + runtime 都健康。
- `/dev/kvm` 可用不代表 Intel PT 可用，這兩個是獨立的硬體功能。
- 版本基線：clang-14.0、rustc 1.97.1、Go 1.18.1、QEMU 6.2.0、unicorn 2.1.2。

---

## 自我檢核

不翻書，在腦子裡回答：

- [ ] `clang -fsanitize=fuzzer` 和 `-fsanitize=fuzzer-no-link` 差在哪？後者什麼時候需要？
- [ ] libFuzzer 的 corpus 目錄和 crash artifact 各存什麼格式的東西？
- [ ] 本課哪個 Part 最需要 bare-metal 機器而不能用 WSL？缺的硬體功能叫什麼？
- [ ] AFLNet 和 afl++ 的 `afl-fuzz` 若 PATH 衝突，最安全的解法是什麼？
- [ ] `CMP- DE: "F\000"-` 這行 log 是什麼意思？libFuzzer 接下來會怎麼用這個資訊？

---

## 延伸閱讀

1. **[LLVM libFuzzer 官方文件](https://llvm.org/docs/LibFuzzer.html)**（`fuzzer` vs `fuzzer-no-link` 章節、`-fsanitize-coverage` 的可選旗標列表）——搞清楚每個 flag 的語意，特別是 inline 8-bit counters vs PC-table 的差別；和本章 sanity check 直接相關，Ch 3 覆蓋率章會再引用。

2. **[LibAFL 官方 book](https://aflplus.plus/libafl-book/)** 的「Getting Started」和「Quickstart」章節——確認你的 Rust toolchain 版本和 LibAFL 最新 release 需求吻合；Part 1 開始前的必讀，特別是 nightly feature gate 那段。

3. **[Nyx 論文 §2 Background](https://www.usenix.org/conference/usenixsecurity21/presentation/schumilo)**（USENIX Security 2021）——解釋 Intel PT 是什麼、為什麼在 VM 裡無法使用（Ring 0 PMU 存取限制）、snapshot fuzzing 的動機；對照本章「哪些跑不動」段落，理解硬體限制的根本來源。

4. **[syzkaller docs: Setting up Linux kernel fuzzing](https://github.com/google/syzkaller/blob/master/docs/linux/setup.md)**——Part 4 的預讀材料；「Prerequisites」章節直接對應本章的 Go 安裝步驟，「Building Linux kernel」告訴你 `CONFIG_KCOV` 這些選項為什麼必要。

---

環境建好之後，下一個問題是：**為什麼 afl++ 打不進我真正感興趣的目標**？

→ [下一章：Ch 1 afl++ 的四道牆](./01-afl-plus-plus-walls.md)
