# 練習 B — Instrumentation 模式對比實驗

> **目標**：把 Ch 5–9 的 instrumentation 知識轉化為可量化的實驗，親眼看到不同模式的 throughput 和 coverage 差異。
> **環境**：AFL++ 4.09c, Ubuntu 22.04 LTS, x86_64

---

## 背景與動機

Ch 5–9 花了五個章節解釋 bitmap、compile-time instrumentation、LTO、QEMU mode、Frida mode——但一直到你實際量到數字之前，這些都只是抽象的說法。

工具選型不靠直覺，靠量測。「QEMU mode 比 compile-time 慢 2–5×」不是放諸四海皆準的定律，它依賴 target 的特性：

- 一個 parsing-heavy binary，compile-time 和 QEMU mode 的差距可能只有 2×。
- 一個有大量 syscall 的 binary，QEMU mode 的模擬 overhead 可能放大到 10×。
- 一個有複雜初始化但 parsing 很快的 binary，deferred forkserver 可以讓 throughput 跳一個數量級。

這個練習讓你用同一個 target（`readelf` from binutils-2.40）在四種模式下各跑 5 分鐘，記錄 **execs/sec** 和 **total_edges（unique coverage path 數）**，然後解釋你看到的差異。

**為什麼選 readelf？**

- 真實世界的工具，不是玩具
- 有充足的公開 corpus（ELF binary 到處都是）
- parsing 邏輯集中、沒有網路 I/O、沒有 GUI
- LTO 模式的效果在這類 C 程式上特別明顯（大量 inter-function inlining 後才看得到的 edge）
- stripped binary 版本和有插樁版本在行為上完全一致，QEMU mode 的測試條件乾淨

---

## 任務規格

| 項目 | 規格 |
|------|------|
| Target | `readelf`，從 binutils-2.40 原始碼編譯 |
| 輸入格式 | ELF binary（各種格式的 ELF 物件檔） |
| 每種模式的跑法 | `afl-fuzz` 跑 300 秒（5 分鐘），單一 instance，不開 parallel |
| 記錄的指標 | `execs_per_sec`、`total_edges`（從 `out/default/fuzzer_stats` 讀） |
| 四種模式 | PCGUARD、LTO、QEMU（對 stripped binary）、Frida（對 stripped binary） |
| Seed corpus | 從 `/usr/lib/` 和 `/usr/bin/` 蒐集 5–10 個不同格式的 ELF 檔 |

**不需要的事情**：
- 找 crash（找得到很好，但不是這個練習的目標）
- 跑超過 5 分鐘（數據已足夠比較）
- 開多個 fuzzer instance

---

## 期望輸出範例

跑完後填寫這張表（數字會因機器不同而有差異）：

| 模式 | execs/sec | total_edges | 相對 throughput | 備註 |
|------|-----------|-------------|-----------------|------|
| PCGUARD | ~2500 | ~8500 | 1.0× | baseline |
| LTO | ~2300 | ~9200 | 0.92× | edge 數略多（LTO 後 inline 展開） |
| QEMU | ~600 | ~7800 | 0.24× | 無原始碼版本 |
| Frida | ~800 | ~7600 | 0.32× | 無原始碼版本 |

上面只是參考範圍，你的數字可能不同。重點是**觀察相對關係**，解釋為什麼 LTO 的 edge 數比 PCGUARD 多，以及 QEMU 和 Frida 的 overhead 來自哪裡。

---

## 如果你卡住了

**LTO build 失敗：`could not find plugin afl-llvm-lto-instrumentlist.so`**

確認 AFL++ 的 `afl-clang-lto` binary 存在，且 LTO pass 已編譯：

```bash
ls $(afl-clang-lto --print-prog-name=afl-llvm-lto-instrumentlist.so 2>/dev/null || echo "/usr/local/lib/afl/")
# 如果沒有，重新 build AFL++ 時確保 LLVM 版本 >= 11
AFL_LLVM_LTO=1 make -C AFLplusplus
```

**QEMU mode 找不到 `afl-qemu-trace`**

QEMU mode 需要額外 build，不包含在 AFL++ 主程式裡：

```bash
cd AFLplusplus/qemu_mode/
sudo apt-get install -y python3-dev python3-setuptools ninja-build
./build_qemu_support.sh
# 完成後 afl-qemu-trace 會在 AFLplusplus/ 根目錄
```

**Frida mode `-O` 後 target 直接 segfault**

先確認 `afl-frida-trace.so` 存在：

```bash
ls AFLplusplus/afl-frida-trace.so
# 如果不存在：
cd AFLplusplus/frida_mode && make
```

如果 so 存在但仍然 segfault，加上 `AFL_DEBUG=1` 看 Frida agent 的初始化輸出，確認不是 glibc 版本問題。

**`fuzzer_stats` 裡的 `total_edges` 欄位在哪？**

```bash
cat out/default/fuzzer_stats | grep -E "execs_per_sec|edges_found"
# AFL++ 用 edges_found 這個欄位名，不是 total_edges
```

**seeds/ 目錄裡的 seed 太少（只有 1–2 個）**

太少 seed 會讓 fuzzer 卡在 deterministic mutation 很久，影響 coverage 數字的可比性。至少準備 5 個：

```bash
mkdir seeds
cp /bin/ls /bin/bash /bin/cat /usr/bin/file /usr/bin/objdump seeds/
# 確認它們是 ELF
file seeds/* | grep ELF
```

---

## 實作步驟建議

### Step 1：下載並解壓 binutils-2.40

```bash
wget https://ftp.gnu.org/gnu/binutils/binutils-2.40.tar.gz
tar xf binutils-2.40.tar.gz
cd binutils-2.40/
```

### Step 2：用 PCGUARD 模式編譯 readelf

```bash
cd /tmp/build_pcguard
mkdir -p /tmp/build_pcguard && cd /tmp/build_pcguard

# 用 afl-clang-fast（預設用 PCGUARD instrumentation）
CC=afl-clang-fast CXX=afl-clang-fast++ \
    /path/to/binutils-2.40/configure \
    --disable-shared --disable-nls --disable-werror \
    --prefix=/tmp/install_pcguard

make -j$(nproc) && make install

# 確認 readelf 在哪裡
ls /tmp/install_pcguard/bin/readelf
```

驗證插樁成功（應該看到 AFL++ 的 forkserver 相關字串）：

```bash
strings /tmp/install_pcguard/bin/readelf | grep -i afl
# 應該看到 __afl_area_ptr 或類似字串
```

### Step 3：用 LTO 模式編譯

```bash
mkdir -p /tmp/build_lto && cd /tmp/build_lto

# LTO 模式需要替換 ar 和 ranlib
CC=afl-clang-lto CXX=afl-clang-lto++ \
AR=llvm-ar RANLIB=llvm-ranlib \
    /path/to/binutils-2.40/configure \
    --disable-shared --disable-nls --disable-werror \
    --prefix=/tmp/install_lto

make -j$(nproc) && make install
```

如果 `llvm-ar` 不存在，用有版本號的版本：

```bash
which llvm-ar-14 || which llvm-ar-15 || which llvm-ar-16
# 找到之後 AR=llvm-ar-14（把版本號換成你的 LLVM 版本）
```

### Step 4：準備 stripped binary 供 QEMU 和 Frida

不要直接用 PCGUARD 的 binary 去跑 QEMU mode——那樣你量的是「有插樁的 binary 透過 QEMU 跑」，多了一層 overhead，也不是正確的使用場景。

正確的做法：用**沒有 AFL 插樁**的 binary，然後讓 QEMU/Frida 在 runtime 插樁：

```bash
# 用普通 gcc 編譯一個沒有 AFL instrumentation 的 readelf
mkdir -p /tmp/build_noinst && cd /tmp/build_noinst

CC=gcc CXX=g++ \
    /path/to/binutils-2.40/configure \
    --disable-shared --disable-nls --disable-werror \
    --prefix=/tmp/install_noinst

make -j$(nproc) && make install

# strip debug symbol（模擬真實的 closed-source 情境）
strip /tmp/install_noinst/bin/readelf
cp /tmp/install_noinst/bin/readelf /tmp/readelf_stripped
```

### Step 5：準備 seed corpus

```bash
mkdir -p /tmp/afl_seeds_readelf

# 蒐集不同類型的 ELF：executable、shared library、relocatable object
cp /bin/ls /tmp/afl_seeds_readelf/ls.elf
cp /bin/cat /tmp/afl_seeds_readelf/cat.elf
cp /lib/x86_64-linux-gnu/libc.so.6 /tmp/afl_seeds_readelf/libc.elf
objcopy --only-section=.text /bin/ls /tmp/afl_seeds_readelf/ls_text.elf 2>/dev/null || true
cp /usr/lib/debug/lib/x86_64-linux-gnu/libc.so.6 /tmp/afl_seeds_readelf/libc_debug.elf 2>/dev/null || true

# 至少確保有 4–5 個有效 ELF
file /tmp/afl_seeds_readelf/* | grep ELF
```

### Step 6：各跑 5 分鐘，記錄數據

建立一個統一的 wrapper script，方便重複執行：

```bash
#!/bin/bash
# run_mode.sh <mode_flag> <target_binary> <output_dir>
# mode_flag: "" (default) / "-Q" / "-O"

MODE=$1
TARGET=$2
OUTDIR=$3

mkdir -p "$OUTDIR"

# 跑 300 秒後自動停止
timeout 300 \
    afl-fuzz $MODE \
    -i /tmp/afl_seeds_readelf \
    -o "$OUTDIR" \
    -- "$TARGET" -a @@

echo "=== $OUTDIR/default/fuzzer_stats ==="
grep -E "execs_per_sec|edges_found|exec_timeout" "$OUTDIR/default/fuzzer_stats"
```

四次執行：

```bash
# Mode 1: PCGUARD
bash run_mode.sh "" /tmp/install_pcguard/bin/readelf /tmp/out_pcguard

# Mode 2: LTO
bash run_mode.sh "" /tmp/install_lto/bin/readelf /tmp/out_lto

# Mode 3: QEMU（-Q）
bash run_mode.sh "-Q" /tmp/readelf_stripped /tmp/out_qemu

# Mode 4: Frida（-O）
bash run_mode.sh "-O" /tmp/readelf_stripped /tmp/out_frida
```

每次執行後，從 `fuzzer_stats` 讀取數據：

```bash
for dir in /tmp/out_pcguard /tmp/out_lto /tmp/out_qemu /tmp/out_frida; do
    echo "--- $(basename $dir) ---"
    grep -E "execs_per_sec|edges_found|start_time|last_update" "$dir/default/fuzzer_stats"
done
```

### Step 7：分析差異

填寫你的觀察表，然後回答以下問題：

1. LTO 的 `edges_found` 比 PCGUARD 多還是少？為什麼？（提示：Ch 7 的 link-time inlining 展開了哪些 edge？）

2. QEMU mode 的 `execs_per_sec` 降低了多少比例？這個比例符合 Ch 8 的 0.2–0.5× 估算嗎？如果不符合，可能的原因是什麼？

3. Frida mode 比 QEMU mode 快還是慢？為什麼 `readelf` 這個 target 上的結果是這樣？（提示：`readelf` 的 syscall 密度）

4. 五分鐘後，compile-time 版本和 QEMU 版本的 `edges_found` 差距多大？差距主要來自 throughput 差異還是 instrumentation 品質差異？

---

## 完整參考解答

<details>
<summary>展開參考解答（先自己做完再看）</summary>

### 預期數字範圍（i7-10700, 8 cores, Ubuntu 22.04）

| 模式 | execs/sec 範圍 | edges_found 範圍 | 說明 |
|------|---------------|-----------------|------|
| PCGUARD | 2000–3500 | 7000–10000 | baseline，最快 |
| LTO | 1800–3200 | 7500–11000 | throughput 略低（build time link cost），但 edge 數略高 |
| QEMU | 400–900 | 5000–9000 | 約 PCGUARD 的 0.2–0.4× |
| Frida | 600–1200 | 4800–8500 | 和 QEMU 接近，`readelf` 沒有反除錯，差距不明顯 |

**注意**：這些數字在不同硬體、不同 AFL++ build 下會有 2× 以內的波動，是正常的。

### 關鍵觀察解釋

**為什麼 LTO 的 edge 數通常比 PCGUARD 多？**

LTO（Link-Time Optimization）在連結時把多個翻譯單元合並，讓 compiler 能做 cross-function inlining。`readelf` 有很多小的 helper 函式（`is_elf_file()`、`get_section_name()` 等），在 PCGUARD 模式下它們是獨立的函式，有 call/return edge；在 LTO 模式下它們被 inline 展開，call site 和函式體的 edge 都出現在 caller 的 basic block 圖裡，edge 數增加。

LTO 模式還使用 `AFL_LLVM_LTO_STARTID` 機制確保 edge ID 全局唯一，減少 bitmap collision，進一步提升 edge 數量的準確性。

**為什麼 Frida 在 readelf 上不比 QEMU 快？**

`readelf` 是個 parsing-intensive 程式，幾乎沒有 syscall（只有最開始的 `open()`/`read()` 和最後的 `close()`）。Frida 相對於 QEMU 的優勢在「不需要模擬 syscall」，但 `readelf` 的 syscall 密度太低，這個優勢體現不出來。Frida 的 Stalker 在這個 target 上的 recompile overhead 和 QEMU 的 TCG overhead 差不多。

換一個有大量 `getpid()`/`clock_gettime()` 呼叫的 target，Frida 的優勢才會明顯。

**Makefile（一次性建置四個版本）**

```makefile
# Makefile for practice-b
BINUTILS_SRC  := /path/to/binutils-2.40
SEEDS_DIR     := /tmp/afl_seeds_readelf
COMMON_FLAGS  := --disable-shared --disable-nls --disable-werror

.PHONY: all pcguard lto noinst seeds

all: pcguard lto noinst seeds

pcguard:
	mkdir -p /tmp/build_pcguard && cd /tmp/build_pcguard && \
	CC=afl-clang-fast CXX=afl-clang-fast++ \
	$(BINUTILS_SRC)/configure $(COMMON_FLAGS) --prefix=/tmp/install_pcguard && \
	make -j$(shell nproc) && make install

lto:
	mkdir -p /tmp/build_lto && cd /tmp/build_lto && \
	CC=afl-clang-lto CXX=afl-clang-lto++ \
	AR=llvm-ar RANLIB=llvm-ranlib \
	$(BINUTILS_SRC)/configure $(COMMON_FLAGS) --prefix=/tmp/install_lto && \
	make -j$(shell nproc) && make install

noinst:
	mkdir -p /tmp/build_noinst && cd /tmp/build_noinst && \
	CC=gcc CXX=g++ \
	$(BINUTILS_SRC)/configure $(COMMON_FLAGS) --prefix=/tmp/install_noinst && \
	make -j$(shell nproc) && make install && \
	strip /tmp/install_noinst/bin/readelf && \
	cp /tmp/install_noinst/bin/readelf /tmp/readelf_stripped

seeds:
	mkdir -p $(SEEDS_DIR)
	cp /bin/ls /bin/cat /bin/grep $(SEEDS_DIR)/
	cp /lib/x86_64-linux-gnu/libc.so.6 $(SEEDS_DIR)/libc.elf 2>/dev/null || true
	cp /usr/bin/file $(SEEDS_DIR)/file.elf 2>/dev/null || true
	@echo "Seeds ready: $$(ls $(SEEDS_DIR) | wc -l) files"
```

### 完整 afl-fuzz 命令

```bash
# 在跑之前，確認 CPU scaling governor（如果機器支援）
echo performance | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor 2>/dev/null || true

# PCGUARD
AFL_SKIP_CPUFREQ=1 timeout 300 \
    afl-fuzz -i /tmp/afl_seeds_readelf -o /tmp/out_pcguard \
    -- /tmp/install_pcguard/bin/readelf -a @@

# LTO
AFL_SKIP_CPUFREQ=1 timeout 300 \
    afl-fuzz -i /tmp/afl_seeds_readelf -o /tmp/out_lto \
    -- /tmp/install_lto/bin/readelf -a @@

# QEMU（stripped binary）
AFL_SKIP_CPUFREQ=1 timeout 300 \
    afl-fuzz -Q -i /tmp/afl_seeds_readelf -o /tmp/out_qemu \
    -- /tmp/readelf_stripped -a @@

# Frida
AFL_SKIP_CPUFREQ=1 timeout 300 \
    afl-fuzz -O -i /tmp/afl_seeds_readelf -o /tmp/out_frida \
    -- /tmp/readelf_stripped -a @@
```

### 結果讀取腳本

```bash
#!/bin/bash
# print_results.sh

echo "| 模式 | execs/sec | edges_found |"
echo "|------|-----------|-------------|"

for mode in pcguard lto qemu frida; do
    stats="/tmp/out_${mode}/default/fuzzer_stats"
    if [ -f "$stats" ]; then
        eps=$(grep "execs_per_sec" "$stats" | awk -F': ' '{printf "%.0f", $2}')
        edges=$(grep "edges_found" "$stats" | awk -F': ' '{printf "%.0f", $2}')
        echo "| ${mode} | ${eps} | ${edges} |"
    else
        echo "| ${mode} | N/A | N/A |"
    fi
done
```

</details>

---

## 測試用例：四種模式的合理性檢查

跑完後用以下標準確認結果是否合理（如果明顯偏差，先排查環境問題再解讀數字）：

| 模式 | execs/sec 下限 | execs/sec 上限 | 異常原因可能 |
|------|---------------|---------------|------------|
| PCGUARD | 500 | 10000 | 低於 500：forkserver 沒啟動或 seed 無效；高於 10000：target 太簡單 |
| LTO | 400 | 10000 | 和 PCGUARD 差距應 < 20%，差距過大代表 LTO build 有問題 |
| QEMU | 100 | 3000 | 低於 100：QEMU mode build 有問題；高於 PCGUARD：不可能，重查 |
| Frida | 100 | 3000 | 同 QEMU；若 Frida 比 QEMU 快 5× 以上，確認用的是同一個 stripped binary |

---

## 延伸挑戰

**挑戰 1：Context Sensitivity（上下文敏感度）**

用 `AFL_LLVM_CTX=1` 重新編譯 PCGUARD 版本，這會啟用 caller context 感知的 edge ID（詳見 Ch 6）：

```bash
AFL_LLVM_CTX=1 CC=afl-clang-fast ... configure ...
```

比較啟用 `AFL_LLVM_CTX=1` 前後的 `edges_found` 和 bitmap collision rate（從 `fuzzer_stats` 的 `stability` 欄位間接觀察）。思考：context sensitivity 為什麼有時會減少而不是增加有效 edge 數？

**挑戰 2：MAP_SIZE 對 collision 的影響**

AFL++ 預設 bitmap 是 65536 bytes（64KB，`MAP_SIZE` = 2^16）。重新編譯時改變 MAP_SIZE：

```bash
# 縮小 MAP_SIZE，提高 collision
AFL_MAP_SIZE=16384 CC=afl-clang-fast ... configure ...

# 擴大 MAP_SIZE，降低 collision（需要更多記憶體）
AFL_MAP_SIZE=262144 CC=afl-clang-fast ... configure ...
```

對 `readelf` 這樣有 ~9000 個 edge 的程式，64KB bitmap（65536 個 slot）的 collision 率理論上很低。用縮小版的 map 跑 5 分鐘，觀察 `stability` 是否下降（bitmap collision 增加會讓 stability 降低）。

**挑戰 3：Deferred Forkserver 的效果**

找一個有昂貴初始化（例如讀取大設定檔）的真實程式，自己加上 `__AFL_INIT()` macro，比較加前加後的 execs/sec 差異，驗證 Ch 9 的效能階梯說法。

---

## 自我檢核

1. 你量到的 QEMU mode execs/sec 約是 PCGUARD 的幾倍？和 Ch 8 的估算（0.2–0.5×）比較，符合嗎？如果不符合，你認為原因是什麼？

2. LTO 版本的 `edges_found` 比 PCGUARD 高還是低？解釋這個差異背後的原理。

3. 如果你需要對一個有原始碼的 target 選擇 instrumentation 模式，什麼情況下你會選 LTO 而不是 PCGUARD？

4. Frida mode 和 QEMU mode 在 `readelf` 這個 target 上的 throughput 差距小——如果換成一個大量呼叫 `gettimeofday()` 的 target，你預期差距會如何改變？

5. 如果你在工作中收到一個沒有原始碼的 closed-source binary，你會優先選 QEMU 還是 Frida mode？判斷依據是什麼？
