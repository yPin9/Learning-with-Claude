# 練習 A — 跑起第一個 AFL++ Session

> **目標**：把 Ch 0-4 學到的東西整合起來，能完整設置 target、啟動 fuzzing、解讀 status screen、找到 crash。

## 背景與動機

在真實的 fuzzing campaign 開始之前，你面對一個新 target，需要確認三件事：

1. **插樁成功了嗎**：target binary 裡有沒有 AFL++ 的 coverage instrumentation？
2. **Forkserver 啟動了嗎**：還是每次執行都在跑完整的 `execve()` 流程？
3. **Coverage 有在增長嗎**：status screen 上的 `map density` 是在慢慢長大，還是在 0.01% 卡住？

這三個問題，你只需要 15 分鐘就能確認。這個練習就是練那 15 分鐘。

選擇 `readelf`（來自 GNU binutils）作為 target，原因：
- 有原始碼，可以用 `afl-clang-fast` 插樁
- 輸入格式是 ELF binary，seed corpus 隨手可得（任何 .so 或 ELF 都可以）
- **歷史上真的找過 CVE**：CVE-2017-14333、CVE-2018-6323 等一批 binutils CVE 都是用 AFL 找到的，這不是 toy 練習

## 任務規格

**環境**：Ubuntu 22.04 LTS, x86_64, AFL++ 4.09c 已安裝（參考 Ch 0）

**Target**：`readelf` from GNU binutils 2.40

**驗收標準**：

| 檢查項目 | 成功條件 |
|---------|---------|
| 編譯 | `afl-clang-fast` 編譯 binutils 成功，無 warning about no instrumentation |
| 啟動 | `afl-fuzz` 成功啟動，status screen 出現，無 `fork server crash` 錯誤 |
| Forkserver | status screen 顯示 `forkserver` 而非 `exec calls` |
| Coverage | 跑 60 秒後 `map density` > 0.5%（readelf 的 coverage 通常很快就到 1%+） |
| Exec speed | > 200 exec/s（source instrumentation + 簡單 seed 下的合理期待） |
| 解讀 | 能說出 status screen 上每個主要欄位的含義 |

## 期望輸出範例

啟動後約 2 分鐘，status screen 應該類似這樣：

```
                         american fuzzy lop ++4.09c {default} (./readelf) [fast]
┌─ process timing ────────────────────────────────────┬─ overall results ────┐
│        run time : 0 days, 0 hrs, 2 min, 3 sec        │  cycles done : 1     │
│   last new find : 0 days, 0 hrs, 0 min, 42 sec       │ corpus count : 23    │
│ last uniq crash : none seen yet                      │  saved crashes : 0   │
│  last uniq hang : 0 days, 0 hrs, 1 min, 12 sec       │   saved hangs : 1    │
├─ cycle progress ──────────────────────────────────── ┼─ map coverage ───────┤
│  now processing : 8.23 (28.3%)                       │    map density : 1.82% / 3.44% │
│  runs timed out : 0 (0.00%)                          │ count coverage : 3.71 bits/tuple │
├─ stage progress ──────────────────────────────────── ┼─ findings in depth ──┤
│  now trying : havoc                                  │ favored items : 9 (39.13%) │
│ stage execs : 3.8k/8192 (46.09%)                     │  new edges on : 23 (100.0%) │
│ total execs : 85.9k                                  │ total crashes : 0 (0 unique) │
│  exec speed : 634.4/sec                              │  total tmouts : 65 (1 unique) │
├─ fuzzing strategy yields ─────────────────────────── ┴──────────────────────┤
│   bit flips : 37/1.45k, 4/1.44k, 0/1.44k                                   │
│  byte flips : 0/181, 0/180, 0/180                                           │
│ arithmetics : 0/10.1k, 0/1.01k, 0/124                                       │
│  known ints : 0/916, 0/225, 0/84                                            │
│  dictionary : 0/0, 0/0, 0/0, 0/0                                            │
│havoc/splice : 14/42.2k, 1/9.06k                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

**逐行解讀**：

| 欄位 | 含義 | 要注意的值 |
|------|-----|-----------|
| `run time` | Session 已執行時長 | - |
| `last new find` | 最後一次找到新 coverage 距現在多久 | 如果超過 30 分鐘沒有新發現，考慮重新評估策略 |
| `last uniq crash` | 最後一次找到新 crash | `none seen yet` 表示還沒有 crash |
| `cycles done` | 整個 queue 被 fuzz 完幾輪 | 第 1 輪完成後才算「完整跑過一次」 |
| `corpus count` | 當前 queue 裡的 seed 數量 | 應該會隨時間增加 |
| `map density` | 兩個數字：已發現的覆蓋率 / 最高峰的覆蓋率 | 太低（< 0.1%）說明插樁有問題或 seed 太差 |
| `exec speed` | 每秒執行次數 | source instrumentation 應該 > 200/sec |
| `now trying` | 當前的 mutation stage | `havoc` 表示在隨機 mutation 階段 |
| `total execs` | 累計執行次數 | - |
| `favored items` | favored minset 的 seed 數量 | 應該遠小於 corpus count |
| `saved crashes` | 已保存的 unique crash 數量 | 這是你最終要看的數字 |

## 如果你卡住了

**卡點 1：`afl-clang-fast: error: unable to infer...`**

binutils 的 `./configure` 要指定 CC 和 CXX：

```bash
CC=afl-clang-fast CXX=afl-clang-fast++ ./configure --disable-shared ...
```

不能只設 `CC`，因為部分 C++ 檔案需要 `CXX` 也被設成 AFL++ wrapper。

**卡點 2：`[!] PROGRAM ABORT: Fork server crashed with signal 11`**

最常見原因：target 在 forkserver 啟動前就 crash 了（通常是 constructor 裡出問題）。試試：

```bash
# 先直接執行看看有沒有錯誤
./readelf -a /usr/bin/ls
# 如果直接執行 OK，再用 afl-fuzz 跑，看 afl-fuzz 加了什麼環境變數
AFL_DEBUG=1 afl-fuzz -i in -o out ./readelf -a @@ 2>&1 | head -50
```

**卡點 3：`map density` 一直是 0.00%**

Binary 沒有被插樁。確認 `afl-clang-fast` 在編譯時有被實際呼叫：

```bash
# 重新編譯，加上 V=1 看完整編譯指令
make clean
AFL_QUIET=0 make -j4 V=1 2>&1 | grep "afl-clang-fast" | head -5
# 如果一行都沒有，代表 CC 設定沒有生效
```

**卡點 4：`exec speed` 只有 10-50/sec**

Forkserver 沒有啟動，每次執行都跑完整 `execve()`。

```bash
# 確認 status screen 上顯示的是 forkserver 還是 exec calls
# 或直接看 fuzzer_stats
grep "exec_timeout\|forkserver" out/default/fuzzer_stats
```

**卡點 5：seed corpus 太差，一直找不到新 coverage**

用系統上的 ELF binary 作為 seed，而不是隨機資料：

```bash
mkdir -p in
cp /bin/ls /bin/cat /usr/bin/python3 in/
afl-cmin -i in -o in_min -- ./readelf -a @@
# afl-cmin 會從這些 seed 中選出「覆蓋最多但數量最少」的子集
```

## 實作步驟建議

### Step 1：準備環境

確認 AFL++ 已安裝：

```bash
which afl-fuzz afl-clang-fast
afl-fuzz --version
# 應該顯示 AFL++ 4.09c 或更新版本
```

安裝 binutils 的 build dependency：

```bash
sudo apt-get install -y build-essential texinfo bison flex
```

### Step 2：取得並編譯 target

```bash
# 下載 binutils 2.40
wget https://ftp.gnu.org/gnu/binutils/binutils-2.40.tar.xz
tar xf binutils-2.40.tar.xz
cd binutils-2.40

# 用 afl-clang-fast 編譯
# --disable-shared：靜態連結，減少 forkserver 的 .so 載入 overhead
# --disable-werror：binutils 有些 warning 會被當成 error，繞過去
CC=afl-clang-fast \
CXX=afl-clang-fast++ \
./configure \
  --disable-shared \
  --disable-werror \
  --target=x86_64-linux-gnu \
  --prefix=/tmp/binutils-afl

make -j$(nproc) 2>&1 | tail -20

# 確認插樁成功：afl-clang-fast 編譯成功的話，binary 裡會有 __afl_ 符號
nm binutils/readelf | grep "__afl_" | head -5
# 應該看到 __afl_area_ptr、__afl_forkserver_start 等符號
```

如果 `nm` 輸出為空，代表插樁失敗——回頭確認 `CC` 是否設定正確。

### Step 3：準備 seed corpus

```bash
cd /tmp  # 或你選擇的工作目錄
mkdir -p fuzzing/in

# 用系統的 ELF binary 作為 seed（它們是合法的 ELF，readelf 能完整解析）
cp /bin/ls /bin/cat /bin/echo fuzzing/in/
# 再加幾個 .so
cp /lib/x86_64-linux-gnu/libc.so.6 fuzzing/in/libc.so
cp /usr/lib/x86_64-linux-gnu/libssl.so.3 fuzzing/in/libssl.so 2>/dev/null || true

ls -la fuzzing/in/
# 應該有 3-5 個 seed 檔案
```

可選：用 `afl-cmin` 最小化 seed corpus（需要 readelf binary 路徑正確）：

```bash
afl-cmin -i fuzzing/in -o fuzzing/in_min \
  -- /tmp/binutils-afl/bin/readelf -a @@
# 如果沒有 prefix 安裝，用 binutils-2.40/binutils/readelf
```

### Step 4：啟動 AFL++

```bash
cd /tmp/fuzzing

# 設定 core dump pattern（AFL++ 需要這個才能偵測 crash）
echo core | sudo tee /proc/sys/kernel/core_pattern
# 如果沒有 sudo，試試：
sudo sysctl -w kernel.core_pattern=core

# 啟動！
afl-fuzz \
  -i in \
  -o out \
  -p fast \
  -- /path/to/binutils-2.40/binutils/readelf -a @@

# 解釋各個 flag：
# -i in        : seed corpus 目錄
# -o out       : 輸出目錄（queue/crashes/hangs 都在這裡）
# -p fast      : 使用 AFLFast 的 power schedule
# -- readelf -a @@  : target 指令，@@ 是 AFL++ 替換測試用例的位置
```

如果看到：
```
[+] All right - let's fuzz!
```

你已經成功啟動了。

### Step 5：解讀 status screen

啟動後，對照「期望輸出範例」一節，確認每個欄位你都能說出含義。

重點確認：

```
map density : 1.xx% / x.xx%
```

如果這個數字在增長（每隔幾分鐘觀察），代表 AFL++ 在持續發現新的 code path。如果在 0.01% 卡住不動，回到 Step 2 確認插樁。

### Step 6：等待並找 crash

讓 AFL++ 跑 15-30 分鐘。

同時在另一個終端觀察輸出目錄的結構：

```bash
# 即時觀察 queue 增長
watch -n 5 'ls out/default/queue/ | wc -l'

# 查看 fuzzer 統計
cat out/default/fuzzer_stats

# 如果有 crash（saved crashes > 0）
ls out/default/crashes/
# crash 的檔案名稱格式：id:XXXXXX,sig:YY,src:ZZZZ,...

# 驗證 crash 可重現
./binutils-2.40/binutils/readelf -a out/default/crashes/id:000000,*
# 應該會看到 segfault 或類似錯誤
```

如果 15 分鐘內沒有 crash，也沒關係——readelf 2.40 的已知漏洞大部分已被 patch，需要更長時間。繼續看「延伸挑戰」。

## 完整參考解答

<details>
<summary>點開前先自己試試——最少跑完 Step 4 再看</summary>

### 完整的從零到啟動指令序列

```bash
# ── 0. 確認環境 ──────────────────────────────────────────────
afl-fuzz --version
# AFL++ 4.09c (dev) by Michal Zalewski and lcamtuf

# ── 1. 設定 core dump（每次重開機後需要重設）─────────────────
echo core | sudo tee /proc/sys/kernel/core_pattern

# ── 2. 下載並編譯 binutils ───────────────────────────────────
cd /tmp
wget -q https://ftp.gnu.org/gnu/binutils/binutils-2.40.tar.xz
tar xf binutils-2.40.tar.xz
cd binutils-2.40

CC=afl-clang-fast \
CXX=afl-clang-fast++ \
CFLAGS="-O2 -g" \
./configure \
  --disable-shared \
  --disable-werror \
  --prefix=/tmp/binutils-afl-out

make -j$(nproc) 2>&1 | grep -E "error:|warning:|afl-clang" | tail -20

# 確認插樁
READELF=/tmp/binutils-2.40/binutils/readelf
nm $READELF | grep "__afl_forkserver" | head -3
# 輸出範例：
# 0000000000416ae0 T __afl_forkserver_start

# 驗證可正常執行
$READELF -a /bin/ls | head -5

# ── 3. 準備 seed corpus ───────────────────────────────────────
mkdir -p /tmp/fuzzing-readelf/in
cp /bin/ls /bin/cat /bin/sh /tmp/fuzzing-readelf/in/

# 用 afl-showmap 確認 seed 的 coverage（可選但有教育意義）
afl-showmap -o /dev/null -q -- $READELF -a /tmp/fuzzing-readelf/in/ls
# 如果有 coverage，會看到 "Traced XXXX tuples" 之類的輸出

# ── 4. 啟動 AFL++ ─────────────────────────────────────────────
cd /tmp/fuzzing-readelf

afl-fuzz \
  -i in \
  -o out \
  -p fast \
  -t 1000 \
  -- $READELF -a @@

# -t 1000 : timeout 設 1000ms，比預設略長，避免 readelf 解析大檔案超時

# ── 5. 如果要在背景跑並監控 ──────────────────────────────────
# 另開一個終端：
watch -n 10 'cat /tmp/fuzzing-readelf/out/default/fuzzer_stats | grep -E "exec_speed|paths_found|unique_crashes|map_size"'
```

### 預期的 fuzzer_stats 內容（約 5 分鐘後）

```
start_time        : 1716000000
last_update       : 1716000300
run_time          : 300
fuzzer_pid        : 12345
cycles_done       : 2
cycles_wo_finds   : 0
time_wo_finds     : 0
execs_done        : 185000
execs_per_sec     : 617.45
corpus_count      : 34
corpus_favored    : 11
corpus_found      : 31
corpus_imported   : 0
max_depth         : 4
cur_item          : 22
pending_favs      : 3
pending_total     : 12
stability         : 98.12%
bitmap_cvg        : 1.92%
unique_crashes    : 0
unique_hangs      : 2
last_path         : 1716000198
last_crash        : 0
last_hang         : 1716000089
execs_since_crash : 185000
exec_timeout      : 1000
slowest_exec_ms   : 143
peak_rss_mb       : 24
cpu_affinity      : -1
edges_found       : 1258
var_byte_count    : 11
havoc_expansion   : 0
afl_banner        : readelf
afl_version       : ++4.09c
target_mode       : default
command_line      : afl-fuzz -i in -o out -p fast -t 1000 -- readelf -a @@
```

### 如果找到 crash 的後續步驟

```bash
# 列出所有 crash
ls -la out/default/crashes/

# 用 GDB 分析第一個 crash
crash_file=$(ls out/default/crashes/ | head -1)
gdb -ex "run -a out/default/crashes/$crash_file" \
    -ex "bt" \
    -ex "quit" \
    --args $READELF

# 用 afl-tmin 最小化 crash input（讓 PoC 更小更乾淨）
afl-tmin \
  -i "out/default/crashes/$crash_file" \
  -o crash_minimized.elf \
  -- $READELF -a @@
```

</details>

## 測試用例（驗收表格）

逐一確認以下項目，全部打勾才算完成本練習：

| # | 驗收項目 | 如何確認 | 預期結果 |
|---|---------|---------|---------|
| 1 | afl-clang-fast 編譯成功 | `nm readelf \| grep __afl_` | 至少有 3 個 `__afl_` 符號 |
| 2 | Readelf 直接執行正常 | `./readelf -a /bin/ls \| head` | 輸出 ELF header 資訊，無 crash |
| 3 | AFL++ 啟動無錯誤 | Status screen 正常出現 | 無 `PROGRAM ABORT` 訊息 |
| 4 | Exec speed 合理 | Status screen `exec speed` | > 200/sec |
| 5 | Coverage 在增長 | 觀察 `map density` 2 分鐘 | 數字有在上升 |
| 6 | 能解釋 `corpus count` | 問自己：為什麼這個數字在增加？ | 每次找到新 coverage 就 +1 |
| 7 | 能解釋 `favored items` | 問自己：favored 和 corpus 的差別？ | favored 是能代表所有 coverage 的最小子集 |
| 8 | 能解釋 `cycles done` | 問自己：cycle 是什麼意思？ | 整個 queue 被輪完一次算一個 cycle |
| 9 | 輸出目錄結構正確 | `ls out/default/` | 有 queue/、crashes/、hangs/、fuzzer_stats |
| 10 | 能找到 crash 或解釋為何沒有 | 看 `saved crashes` 或能解釋 | 有 crash 則驗證可重現；沒有則能說出可能原因 |

## 延伸挑戰

### 挑戰一：deferred forkserver

預設情況下，forkserver 在 `main()` 之前就啟動了。但如果 `main()` 之前有很多初始化工作（載入設定檔、建立大型資料結構），每次 fork 都要拷貝這些記憶體很浪費。

**Deferred forkserver** 讓你手動指定 forkserver 在哪個點啟動，讓昂貴的初始化只跑一次。

```c
// 在你想讓 forkserver 啟動的地方，加上這個 macro
// 把初始化放在這個 macro 之前，parsing logic 放在之後
__AFL_INIT();
```

對 readelf 而言，ELF parsing 邏輯在 `main()` 裡面。試試找到 `readelf.c` 裡開始解析 argv[1]（input 檔案）的地方，在那之前加上 `__AFL_INIT()`，重新編譯，比較 exec/s 的變化。

```bash
# 比較速度差異
# 版本 A：預設 forkserver（fork 在 main() 之前）
grep "exec_speed" out_default/default/fuzzer_stats

# 版本 B：deferred forkserver（fork 在 readelf 開始解析 ELF 之前）
grep "exec_speed" out_deferred/default/fuzzer_stats
```

### 挑戰二：persistent mode

Persistent mode 比 deferred forkserver 更進一步：不用每次都 fork，而是在同一個 process 裡反覆執行 parsing loop，類似 libFuzzer 的 in-process fuzzing。

```c
// 在 readelf.c 的核心解析 loop 加上：
while (__AFL_LOOP(1000)) {
    // 這裡的 code 會在同一個 process 中執行 1000 次
    // 之後才真的 fork 出新 child
    do_something_with_input(input_file);
}
```

Persistent mode 的限制：target code 必須是「無狀態的」——每次迴圈開始時，內部狀態要和上一次結束時一樣乾淨。對於有全域狀態的 readelf，這需要手動 reset。

練習：比較三種模式的 exec/s：

```
預設 forkserver → deferred forkserver → persistent mode
```

期望看到的速度提升：2x → 5x（視 target 的初始化成本而定）。

### 挑戰三：AFL_USE_ASAN

AddressSanitizer（ASan）能偵測原本不會 crash 的記憶體錯誤（off-by-one write、use-after-free、heap buffer overflow 等）。沒有 ASan，AFL++ 只能找到「有明顯 crash signal 的 bug」；加上 ASan，才能找到「會被攻擊者利用但 readelf 平時不會 crash 的 bug」。

```bash
# 用 ASan 重新編譯（注意：ASan 會讓速度下降 2-3x）
CC=afl-clang-fast \
CXX=afl-clang-fast++ \
CFLAGS="-O1 -g -fsanitize=address -fno-omit-frame-pointer" \
LDFLAGS="-fsanitize=address" \
./configure --disable-shared --disable-werror
make -j$(nproc)

# 用 AFL_USE_ASAN 啟動（AFL++ 會自動調整 memory limit）
AFL_USE_ASAN=1 afl-fuzz \
  -i in \
  -o out_asan \
  -p fast \
  -m none \
  -- ./binutils/readelf -a @@
# -m none : 關掉 AFL++ 預設的 memory limit（ASan 需要大量虛擬記憶體）
```

觀察：
- ASan 版本的 exec/s 比純 instrumentation 版低多少？
- 在相同的 corpus 下，ASan 版本找到哪些純 instrumentation 版沒發現的 crash？

## 自我檢核

1. 你的 `afl-clang-fast` 編譯出來的 readelf，和直接用 `gcc` 編譯的 readelf，在執行行為上有什麼差異？在二進位層面有什麼差異（用 `nm` 看符號）？

2. 如果 `exec speed` 顯示 15/sec，但你知道 readelf 很快，你會懷疑哪三個最可能的原因？每個原因怎麼診斷？

3. `map density` 顯示 `1.82% / 3.44%`，兩個數字各代表什麼？哪個數字更能代表「AFL++ 探索了多少 readelf 的 code」？

4. 當 `cycles done` 從 0 變成 1，代表什麼事情發生了？這個時間點通常代表「你的 seed corpus 品質還不錯」還是「AFL++ 在浪費時間」？

5. 你找到一個 crash，其 filename 是 `id:000000,sig:11,src:000002,time:45321,execs:12345,op:havoc,rep:4`。從這個 filename 你能讀出哪些資訊？

---

→ [Ch 5 — Edge Coverage Bitmap](./05-edge-coverage-bitmap.md)
