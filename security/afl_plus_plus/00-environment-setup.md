# Ch 0 — 環境搭建
> **目標**：從原始碼 build AFL++、驗證四個主要工具鏈正確安裝、跑起第一個 fuzzing session 並讀懂 status screen 的核心欄位。
> **環境**：AFL++ 4.09c, Ubuntu 22.04 LTS, x86_64 Linux

## 為什麼需要這個？

2014 年之前，想用 AFL 的人直接下載 tarball 解壓縮，因為原版 AFL 的依賴很少。AFL++ 不一樣——它的核心功能有三套插樁系統（LLVM pass、LTO、GCC plugin），每一套都深度依賴特定版本的 LLVM/GCC 內部 API。LLVM 13 能編過的 pass，換 LLVM 16 的 API 就不一定能用，所以 AFL++ 在 build 時要偵測 LLVM 版本並決定要啟用哪些功能。

用 distro 的 package（`apt install afl++`）最省事，但那個版本通常落後好幾個小版本，而且你看不到 build 過程是怎麼決策的。從原始碼 build 才能理解工具鏈是怎麼構成的——這對後面讀插樁機制（Part 2）非常重要。

## 先建立直覺

把 AFL++ 的 build 過程想成三層蛋糕：

```
┌─────────────────────────────────────────────┐
│  Layer 3: afl-fuzz, afl-showmap, afl-tmin   │ ← 用 C 寫的獨立工具
├─────────────────────────────────────────────┤
│  Layer 2: afl-cc (compiler wrapper)         │ ← 包裝 clang/gcc，插入插樁
├─────────────────────────────────────────────┤
│  Layer 1: LLVM pass / LTO plugin / GCC      │ ← 真正做插樁的程式碼
│           plugin (shared libraries)         │    編譯成 .so，被 Layer 2 載入
└─────────────────────────────────────────────┘
```

`make distrib` 會嘗試 build 全部三層。如果 LLVM 太舊，Layer 1 的 LTO 插件會 build 失敗，但 Layer 2 和 Layer 3 仍然可以繼續，只是少了 LTO 功能。這就是為什麼 build log 有 warning 但整體不會 abort。

---

## 依賴與版本

### LLVM 版本為何敏感

AFL++ 的 `afl-clang-lto` 用到了 LLVM 的 `PassManagerBuilder` API——這個 API 在 LLVM 15 和 LLVM 16 之間做了不相容的修改（從 legacy PM 改為 new PM）。AFL++ 4.09c 要求 LLVM 11 以上才能用 `afl-clang-fast`，LLVM 15 以上才能用完整的 LTO 功能。

Ubuntu 22.04 的預設 LLVM 是 14，夠用但不是最新。安裝 LLVM 17 或 18 可以打開更多功能，但 API 破壞的風險也更高。這門課統一用 LLVM 15，夠穩且 LTO 全功能可用。

### 安裝依賴

```bash
# 基本工具
sudo apt update
sudo apt install -y build-essential python3-dev automake cmake git flex \
    bison libglib2.0-dev libpixman-1-dev python3-setuptools cargo libgtk-3-dev

# LLVM 15（使用 LLVM 官方 apt repo，Ubuntu 22.04 的預設 LLVM 14 也可以，但 LTO 功能受限）
wget https://apt.llvm.org/llvm.sh
chmod +x llvm.sh
sudo ./llvm.sh 15

# 確認版本
clang-15 --version
# 應該看到：clang version 15.x.x

# 如果你的系統預設 clang 不是 15，設定 alternatives
sudo update-alternatives --install /usr/bin/clang clang /usr/bin/clang-15 150
sudo update-alternatives --install /usr/bin/clang++ clang++ /usr/bin/clang++-15 150
sudo update-alternatives --install /usr/bin/llvm-config llvm-config /usr/bin/llvm-config-15 150
```

---

## 從原始碼 Build

### clone 與 build 選項

```bash
git clone https://github.com/AFLplusplus/AFLplusplus
cd AFLplusplus

# 三種主要 build target 的差異：
# make source-only  → 只 build afl-fuzz 和 afl-cc，不 build LLVM pass
#                     適合只想快速測試，不需要 instrumentation 的情境（很少見）
# make distrib      → build 所有東西：LLVM pass、LTO、QEMU mode、Frida mode
#                     這是你通常想要的
# make all          → 同 distrib，但不 build QEMU/Frida（省時，適合只用 compile-time 插樁）

make distrib
```

build 時注意 log 的開頭，AFL++ 會報告它偵測到什麼：

```
[*] Checking for working 'python3'... found, version 3.10.12
[*] Checking for llvm_config... llvm-config-15 (15.0.7)
[*] Checking for clang... clang-15 (15.0.7)
[*] LTO mode    - afl-clang-lto and afl-clang-lto++ [YES]
[*] LLVM mode   - afl-clang-fast and afl-clang-fast++ [YES]
[*] GCC_PLUGIN mode - afl-gcc-fast and afl-g++-fast [NO] (需要 gcc-plugin-dev)
[*] QEMU mode   - instrumenting binaries without source [YES]
```

如果 LTO mode 顯示 `[NO]`，原因幾乎都是 LLVM 版本問題，回頭確認 `llvm-config --version`。

### 安裝

```bash
sudo make install
# 預設安裝到 /usr/local/bin/
```

---

## 驗證安裝

```bash
# 確認四個主要工具都能找到
afl-fuzz --version
# AFL++ 4.09c by Michal Zalewski, Lszek Szymanski, van Hauser, ...

afl-cc --version
# afl-cc ++4.09c by ...

afl-clang-fast --version
# AFL clang-fast (15.0.7) ...

# 確認 LTO wrapper 存在
ls -la $(which afl-clang-lto)
# 應該是一個實際的 binary，不是 symlink 到 afl-cc（如果 LTO build 失敗就會是 symlink）

# 快速 sanity check：用 afl-clang-fast 編一個小程式並看看 coverage 有沒有注入
cat > /tmp/test_afl.c << 'EOF'
#include <stdio.h>
int main() { printf("hello afl\n"); return 0; }
EOF

afl-clang-fast /tmp/test_afl.c -o /tmp/test_afl
# 如果插樁成功，你會看到：
# [+] Instrumented 1 location (... collision free map size ..., roughly ...)
```

---

## 編譯第一個 Fuzzing Target

用 `readelf` 作為目標——它是 binutils 的一部分，用 C 寫，接受任意二進位檔作為輸入，是 fuzzing 教學的標準靶。

```bash
# 取得 binutils source
sudo apt install binutils-dev  # 只是為了確認依賴存在
wget https://ftp.gnu.org/gnu/binutils/binutils-2.40.tar.gz
tar xf binutils-2.40.tar.gz
cd binutils-2.40

# 用 afl-clang-fast 編譯（關鍵：CC 和 CXX 要指向 AFL++ 的 compiler wrapper）
CC=afl-clang-fast CXX=afl-clang-fast++ \
    ./configure --disable-shared --disable-plugins --disable-gdb \
                --prefix=/tmp/binutils-afl

make -j$(nproc) 2>&1 | tail -20
# 這個過程會看到大量 "Instrumented X locations" 的訊息

make install
ls /tmp/binutils-afl/bin/readelf
```

插樁產生的輸出說明：

```
afl-clang-fast: instrument at: /path/to/bfd/elf.c:1234
[+] Instrumented 4721 locations (LLVM-mode, ratio 100%).
#                ^^^^
#                這是 coverage bitmap 裡被追蹤的 edge 數量
```

---

## 第一次跑

### 準備 seed corpus

種子（seed）是 fuzzer 的起點，seed 的品質直接影響初期 coverage 的增長速度。

```bash
mkdir -p /tmp/fuzz_readelf/seeds /tmp/fuzz_readelf/out

# 給一個最小的 ELF 檔案作為 seed（readelf 的合法輸入）
cp /tmp/binutils-afl/bin/readelf /tmp/fuzz_readelf/seeds/sample.elf

# 也可以用 /bin/ls 這類小 ELF
cp /bin/ls /tmp/fuzz_readelf/seeds/ls.elf
```

### 啟動 afl-fuzz

```bash
# 先處理 system 設定（AFL++ 在啟動時會檢查這些）
echo core | sudo tee /proc/sys/kernel/core_pattern
echo performance | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor

# 執行
afl-fuzz -i /tmp/fuzz_readelf/seeds \
         -o /tmp/fuzz_readelf/out \
         -- /tmp/binutils-afl/bin/readelf -a @@
#                                                  ^^
#                                                  @@ 是佔位符，AFL++ 會替換成實際的測試檔案路徑
```

幾秒後你會看到 AFL++ 的 status screen。

---

## Status Screen 欄位解釋

AFL++ 的 TUI 第一眼很嚇人，但你只需要先理解 8 個欄位：

```
┌─ process timing ─────────────────────────────────────────────────┐
│        run time : 0 days, 0 hrs, 2 min, 34 sec                   │
│   last new find : 0 days, 0 hrs, 0 min, 12 sec                   │ ← (1)
│ last uniq crash : none seen yet                                   │ ← (2)
│  last uniq hang : none seen yet                                   │
├─ overall results ─────────────────────────────────────────────────┤
│  cycles done : 0                                                  │ ← (3)
│    total paths : 312                                              │ ← (4)
│  uniq crashes : 0                                                 │ ← (5)
│   uniq hangs : 0                                                  │
├─ cycle progress ──────────────────────────────────────────────────┤
│  now processing : 47 (15.06%)                                     │
│  paths timed out : 0 (0.00%)                                      │
├─ map coverage ────────────────────────────────────────────────────┤
│    map density : 1.32% / 4.71%                                    │ ← (6)
│ count coverage : 3.77 bits/tuple                                  │
├─ findings in depth ───────────────────────────────────────────────┤
│  favored paths : 89 (28.53%)                                      │
│  new edges on : 34                                                 │
├─ fuzzing strategy yields ─────────────────────────────────────────┤
│    bit flips : 47/1.41k, 0/1.4k, 0/1.40k                        │ ← (7)
│   byte flips : 0/177, 0/177, 0/177                               │
│  arithmetics : 89/8.12k, 0/1.33k, 0/42                          │
│   known ints : 0/1.15k, 0/381, 0/105                            │
│   dictionary : 0/0, 0/0, 0/0                                     │
│       havoc : 1.27k/45.5k, 0/0                                   │
├─ item geometry ───────────────────────────────────────────────────┤
│    levels : 5                                                      │
│   pending : 223                                                    │
│  pend fav : 79                                                     │
│ own finds : 312                                                    │
│  imported : 0                                                      │
├─ speed rating ────────────────────────────────────────────────────┤
│  exec speed : 2,341/sec                                           │ ← (8)
│  stability : 100.00%                                              │
└───────────────────────────────────────────────────────────────────┘
```

**(1) last new find**：上次發現新 coverage path 距今多久。如果超過 30 分鐘沒有新 find，代表 fuzzer 陷入停滯，需要考慮換策略（加 dictionary、換 mutator、或用 CmpLog mode）。

**(2) last uniq crash**：上次找到 unique crash 距今多久。`none seen yet` 在開始階段是正常的。

**(3) cycles done**：fuzzer 跑過整個 corpus queue 幾輪。一輪 = 對 queue 裡每個種子都做過一次完整的 deterministic mutation。

**(4) total paths**：目前 queue 裡有多少個 unique coverage path（不是種子數量，是執行軌跡數量）。增長越快越好。

**(5) uniq crashes**：去重後的 unique crash 數量。AFL++ 用 crash 發生時的 coverage trace 來去重，而不是用 signal 或 address。

**(6) map density**：bitmap 的使用率。`1.32% / 4.71%` 表示目前用了 4.71%（最高點）。如果超過 70%，bitmap collision 開始嚴重影響 coverage 準確度（見 Ch 5）。

**(7) fuzzing strategy yields**：各個 mutation 策略找到多少新 path（分子）/ 總共試了多少次（分母）。用來診斷哪種策略最有效。

**(8) exec speed**：每秒執行幾次目標程式。`readelf` 這類小程式應該在 1,000–5,000/sec，複雜目標可能只有幾十。低 exec speed 是瓶頸的直接指標。

---

## 底層機制：build system 怎麼選擇 LLVM pass

AFL++ 的 `GNUmakefile` 在 build 開始時執行一系列偵測腳本：

```
GNUmakefile build sequence:
  1. llvm-config --version
       → 如果 >= 11：啟用 afl-clang-fast (LLVM PASS mode)
       → 如果 >= 15：啟用 afl-clang-lto  (LTO mode)
       → 如果 >= 18：啟用 new pass manager only（停用 legacy PM）

  2. 偵測 lld (LLVM linker)
       → LTO mode 需要 lld，找不到 lld 就算 LLVM >= 15 也不啟用 LTO

  3. 偵測 python3 dev headers
       → 找不到 → custom mutator API 受限（只能 .so，不能 .py）

  4. 偵測 QEMU / libcapstone
       → 有才 build qemu_mode/
```

實際偵測位置在 `GNUmakefile` 的 `check_llvm` target 和 `llvm_mode/GNUmakefile`。Build 後在 `llvm_mode/` 目錄可以看到生成的 `afl-llvm-pass.so`——這個 `.so` 是 LLVM pass 的實體，在 `afl-clang-fast` 執行時被 `clang -Xclang -load -Xclang afl-llvm-pass.so` 載入。

```bash
# 確認 LLVM pass 確實被 build 出來
ls -la $(dirname $(which afl-fuzz))/../lib/afl/
# 應該看到 afl-llvm-pass.so, afl-llvm-lto-instrumentation.so 等
```

---

## 常用環境變數

AFL++ 透過環境變數控制大量行為，比 flag 更靈活。以下是最常用的：

| 環境變數 | 用途 | 典型值 |
|----------|------|--------|
| `AFL_NO_FORKSRV=1` | 停用 forkserver，每次 exec 都是全新 process。除錯用，速度極慢 | `1` |
| `AFL_DEBUG=1` | 讓 afl-fuzz 輸出詳細內部 log，包括 forkserver 溝通協議 | `1` |
| `AFL_SKIP_CRASHES=1` | 啟動時跳過會 crash 的種子（不停止）。種子品質不確定時用 | `1` |
| `AFL_FAST_CAL=1` | 加快 calibration 階段，犧牲一點準確度。大 corpus 時省時間 | `1` |
| `AFL_TMPDIR` | 指定 afl-fuzz 存放 .cur_input 的目錄。指向 tmpfs 提速 | `/tmp/ramdisk` |
| `AFL_MAP_SIZE` | 手動指定 bitmap size（預設 65536）。超大 target 可以加大 | `262144` |
| `AFL_PRELOAD` | 類似 LD_PRELOAD，在 target 啟動前載入自訂 .so | `./hook.so` |
| `AFL_AUTORESUME=1` | 如果 out/ 目錄已有資料，自動 resume 而非報錯 | `1` |

---

## 版本差異：4.x vs 3.x

| 特性 | 3.x | 4.x |
|------|-----|-----|
| 預設 instrumentation | LLVM 或 GCC | LLVM（LLVM-mode 是預設） |
| CmpLog / RedQueen | 需要手動 -c | 更容易啟用，部分整合進主流程 |
| Persistent mode 自動偵測 | 否 | 是（target 有 `__AFL_LOOP` 就自動啟用） |
| custom mutator API | v1 | v2（新增更多 hook 點） |
| Frida mode | 不完整 | 完整，支援 aarch64/x86_64 |
| `afl-cc` 統一入口 | 否（各模式分開） | 是（`afl-cc` 根據 invocation name 決定行為） |

如果你在看比較舊的教學（2021 年之前），很多 flag 名稱已經改變。碰到不認識的 flag，先查 `afl-fuzz --help` 或 `docs/env_variables.md`。

---

## 踩雷集錦

1. **很多人以為 LLVM 版本不影響功能，但實際上** LLVM 14 和 LLVM 15 在 LTO pass 的 API 上有 breaking change。如果你的 build log 顯示 `[LTO mode] [NO]`，幾乎都是因為 `llvm-config` 指到的版本不對，或者 `lld` 沒安裝。解法：`sudo apt install lld-15` 並確認 `which lld` 指到 15 版。

2. **很多人以為 seeds 目錄給一個全零的檔案沒差，但實際上** 全零輸入只能觸發 target 處理「壞輸入」的路徑，幾乎不會觸及格式解析的深層邏輯。給一個格式正確的 minimal 範例（對 readelf 就是一個最小 ELF）比給空白或全零快很多。

3. **很多人以為沒有用 afl-clang-fast 編譯 target 跑起來沒關係（只是少點功能），但實際上** 沒有插樁的 target 在 AFL++ 的 coverage-guided 模式下完全沒有 feedback，fuzzer 只是在盲目亂跑——等同 dumb fuzzer。這種情況 AFL++ 會警告 `no instrumentation detected`，但不會 abort。正確做法是用 QEMU mode（`-Q`）或重新用 AFL++ compiler 編譯。

4. **很多人以為 `ulimit -c unlimited` 是可選的，但實際上** 如果 core dump 被 OS 的 crash reporter（例如 Ubuntu 的 `apport`）截走，AFL++ 就看不到 crash，或者 crash 分析變得很慢。`echo core | sudo tee /proc/sys/kernel/core_pattern` 把 core dump 還原成直接寫檔，AFL++ 才能正確偵測。

5. **很多人以為 fuzzing 在一般磁碟上跑就好，但實際上** afl-fuzz 每次執行 target 都要寫 `.cur_input` 到磁碟，在高 exec/sec 的情境下（> 10,000/sec）這會成為瓶頸。把 `AFL_TMPDIR` 指向 `/dev/shm` 或一個 `tmpfs` 掛載點，speed 提升通常在 20–50%。

---

## 進階：再往深一層

AFL++ 4.09 有一個新功能 `AFL_LLVM_LAF_ALL`，它讓 LLVM pass 把多位元組比較（`strcmp`、`memcmp`、多 byte integer compare）拆解成一連串單 byte 比較：

```bash
# 編譯時啟用 LAF-Intel（把比較拆細，讓 fuzzer 更容易靠近 magic bytes）
AFL_LLVM_LAF_ALL=1 afl-clang-fast target.c -o target_laf

# 跑時加上 CmpLog（與 LAF 相輔相成，見 Ch 15）
afl-fuzz -c ./target_cmplog_binary -i seeds/ -o out/ -- ./target_laf @@
```

`AFL_LLVM_LAF_ALL=1` 的代價是 binary 變大、每個比較點都被拆成多個 edge（bitmap 密度上升）。只有在明確知道 target 有複雜的 magic bytes 條件時才開。

---

## 動手練習

1. 從原始碼 build AFL++ 4.09c，讓 `make distrib` 的 log 顯示 LTO mode `[YES]`。
2. 用 `afl-clang-fast` 編譯 `readelf`，確認 instrumented locations 數量 > 1000。
3. 跑 fuzzer 5 分鐘，截圖 status screen，找到 `exec speed` 和 `total paths` 的數字。
4. 把 `AFL_TMPDIR=/dev/shm` 加上之後重跑，比較 exec speed 的差異。
5. 故意用 `gcc`（沒有 AFL++ 插樁）編譯 readelf，跑 afl-fuzz，確認你看到 instrumentation 警告。

---

## 本章重點整理

- AFL++ 的 build system 在 compile time 偵測 LLVM 版本，決定啟用哪些插樁後端（LLVM-mode / LTO / GCC plugin），版本不對就靜默降級而非報錯終止。
- Status screen 最關鍵的三個數字：`last new find`（還在學新東西嗎）、`total paths`（已知多少條執行路徑）、`exec speed`（每秒能試多少次）。
- Seed 品質 > seed 數量：一個格式合法的最小範例比一千個全零檔案更有效。

---

## 自我檢核

- 不看文件，說出 `make distrib` 和 `make all` 的差異是什麼，各自適合什麼情境。
- status screen 的 `map density` 達到什麼數字時要開始擔心 collision 問題，為什麼？
- 如果 `afl-fuzz` 啟動後幾分鐘 `total paths` 不再增加，你的下一步排查步驟是什麼？
- `AFL_TMPDIR` 指向 `/dev/shm` 能加速的根本原因是什麼（不是「因為快」，是具體機制）？

---

## 延伸閱讀

### 官方文件

- **[AFL++ GitHub README — Building section](https://github.com/AFLplusplus/AFLplusplus#building-and-installing-afl)**
  - **讀哪裡**：`Building and installing AFL++` 和 `Choosing a target` 兩節

- **[AFL++ docs/env_variables.md](https://github.com/AFLplusplus/AFLplusplus/blob/stable/docs/env_variables.md)**
  - **讀哪裡**：`Settings for afl-fuzz` 節，先把每個 AFL_* 變數掃一遍，不用全記，建立印象

- **[LLVM Clang Plugin documentation](https://clang.llvm.org/docs/ClangPlugins.html)**
  - **讀哪裡**：`Loading a Plugin` 節，理解 `-Xclang -load` 的機制——這正是 afl-cc 載入 pass 的方式

### 部落格 / 技術文章

- **[lcamtuf — AFL status screen explained](https://lcamtuf.blogspot.com/2014/11/afl-fuzz-nobody-expects-cdata.html)** — lcamtuf (lcamtuf.blogspot.com, 2014)
  - **這篇說什麼**：status screen 每個欄位的設計意圖，原作者親自解釋
  - **讀哪裡**：整篇，很短
  - **為什麼值得讀**：很多 GUI 工具顯示的數字，不讀這篇就容易誤解含義

→ [Ch 1 — Fuzzing 流派](./01-fuzzing-landscape.md)
