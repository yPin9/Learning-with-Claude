# 練習 C — Corpus 最小化 + Crash Triage 完整流程

> **目標**：把 Ch 10-14 的 corpus management 和 crash 語意知識整合，在一個有大量 crashes 的 session 裡快速找出獨特 bugs。你會完整走過 corpus 最小化 → crash triage → uniqueness 確認 → bug report 的流程。
> **環境**：AFL++ 4.09c, Ubuntu 22.04 LTS, x86_64

---

## 背景與動機

你剛接手一個 security audit 任務。前任工程師跑了 24 小時的 AFL++ fuzzing session，產生了這個 session 狀態：

- Corpus（`out/queue/`）：約 800 個 testcase
- Crashes（`out/crashes/`）：**200 個 crash 檔案**
- 你的時間：**1 小時**

你的任務報告必須回答：「這 200 個 crash 裡面有幾個真正獨特的 bug？每個 bug 的類型是什麼？」

這個情境在真實的 fuzzing 工作裡極為常見。工具幫你找到了訊號，但**分類、縮減、確認這些工作仍然是人的責任**。有正確的流程，1 小時夠用；沒有流程，2 天也跑不完。

本練習模擬這個情境，提供一個預先設計的 target（包含 3 個獨立 bug），以及一組模擬的大量 crashes。你要：

1. 用 `afl-cmin` 把 corpus 縮到最小化有效集合
2. 用 `afl-tmin` 縮小每個 crash testcase
3. 按 signal 和 crash 位置分組
4. 用 GDB 確認真正獨特的 bug 數量
5. 寫出一份 bug report

---

## 任務規格

**Target 程式**：一個有故意植入 bug 的簡易 binary 格式 parser（`practice_target.c`，你需要自己建立）

**植入的 bug**：
- Bug 1：`parse_header()` 裡的 heap buffer overflow（`type = 'H'`）
- Bug 2：`parse_body()` 裡的 null pointer dereference（`type = 'B'`，特定 offset 條件）
- Bug 3：`parse_footer()` 裡的 integer overflow 導致 out-of-bounds read（`type = 'F'`）

**你要做到的事**：

1. 建立並編譯 target（AFL++ instrumentation）
2. 跑一個 30 分鐘的 fuzzing session（或使用預先提供的 session dump）
3. 對 `out/queue/` 執行 `afl-cmin`，corpus 縮減到 ≤50 個 testcase
4. 對所有 crash 執行 `afl-tmin`，縮到最小
5. 按 signal 分組並統計
6. 用 GDB/ASAN 確認每組 crash 的 root cause
7. 確定真正獨特的 bug 數量（目標：找出全部 3 個）
8. 輸出一份包含 PoC 重現指令的 bug report

---

## 期望輸出範例

**Step 2：afl-cmin 輸出**

```
[*] Testing the target binary...
[*] Obtaining traces for input corpus...
[*] Obtaining traces for minimal corpus...
[+] Narrowed down to 43 files, saved in '/tmp/cmin_out'.

原始 corpus: 847 個 testcase
最小化後:    43 個 testcase (節省 94.9%)
```

**Step 4：signal 分組統計**

```
=== Crash Signal Distribution ===
  163 sig:11   (SIGSEGV)
   35 sig:06   (SIGABRT)
    2 sig:11   (可能不同 bug)
Total: 200 crashes

=== By Source Seed ===
  143 src:000003
   41 src:000007
   16 src:000012
```

**Step 6：tmin 後的 crash 去重**

```
最小化前 crash 大小：平均 87 bytes
最小化後 crash 大小：平均 6 bytes

去重後 unique crashes (by SHA256 of tmin output)：
  3 unique testcases → 推測 3 個 independent bugs
```

**Step 7：GDB 確認輸出**

```
Bug 1 (crash/minimal_000000):
  Signal: SIGSEGV
  Faulting address: 0x5555557a1010 (heap region + 0x18)
  Instruction: movb %al, (%rdx)
  → heap-buffer-overflow (write) in parse_header()

Bug 2 (crash/minimal_000001):
  Signal: SIGSEGV
  Faulting address: 0x0 (NULL dereference)
  Instruction: mov (%rax), %edx
  → null pointer dereference in parse_body()

Bug 3 (crash/minimal_000002):
  Signal: SIGABRT (ASan)
  ASan report: heap-buffer-overflow (read) in parse_footer()
  → integer overflow caused oversized read
```

---

## 如果你卡住了

**卡點 1：`afl-cmin` 說找不到 binary 或 timeout**

`afl-cmin` 需要用插了 instrumentation 的 binary，不能用原始的 `gcc` 編譯版本。確認你是用 `afl-clang-fast` 編譯的，而且 `afl-showmap` 能正常執行：

```bash
echo "test" | afl-showmap -o /dev/null -- ./practice_target /dev/stdin
# 應該顯示 coverage map，不應該 exit with error
```

**卡點 2：`afl-tmin` 跑很慢（超過 30 分鐘）**

每個 crash 的 `afl-tmin` 大概要 1-5 分鐘。200 個 crash 全部 tmin 要幾小時——先只處理每個 signal group 的代表性樣本（各取 5 個）。

**卡點 3：GDB 下 crash 不重現**

先確認 ASLR 設定：

```bash
cat /proc/sys/kernel/randomize_va_space
# 如果是 2，用 setarch 關掉
setarch $(uname -m) -R gdb ./practice_target
```

**卡點 4：所有 tmin 後的 crash 看起來一樣**

`afl-tmin` 的目標是最小化到仍然 crash，不保證產生人類可讀的差異。用 GDB 看 crash 的 `$rip` 和 `$rdi`——如果兩個 crash 的 faulting address 完全相同，大概率是同一個 bug。

**卡點 5：ASAN 版本 crash 但非 ASAN 版本不 crash**

部分 bug（特別是 heap-buffer-overflow read）在沒有 ASAN 時不一定 crash——讀取越界的數據不會馬上 SIGSEGV，只會讀到垃圾值。這正是 ASAN 的價值所在。

---

## 實作步驟建議

### Step 1：準備環境（建立 target）

```bash
mkdir -p ~/afl_practice_c && cd ~/afl_practice_c

cat > practice_target.c << 'CEOF'
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

/*
 * 模擬一個簡易 binary 格式：
 *   [0]     type byte: 'H' / 'B' / 'F'
 *   [1]     length byte (uint8)
 *   [2..]   payload
 */

/* Bug 1：heap buffer overflow (write) */
static void parse_header(const uint8_t *buf, size_t len) {
    if (len < 2) return;
    uint8_t payload_len = buf[1];
    /* BUG: 固定分配 8 bytes，但 payload_len 可以是 0-255 */
    char *heap = malloc(8);
    if (!heap) return;
    memcpy(heap, buf + 2, payload_len);   /* overflow if payload_len > 8 */
    /* 模擬使用 */
    heap[0] ^= 0x5A;
    free(heap);
}

/* Bug 2：null pointer dereference */
static void parse_body(const uint8_t *buf, size_t len) {
    if (len < 6) return;
    int *ptr = NULL;
    uint8_t flag = buf[3];
    /* BUG: 只在特定 magic 組合下才初始化 ptr，否則解引用 NULL */
    if (buf[2] == 0xDE && buf[3] == 0xAD) {
        int value = 42;
        ptr = &value;
    }
    /* 任何 buf[4] == 0xFF 的情況都會解引用 ptr */
    if (buf[4] == 0xFF) {
        *ptr = flag;   /* null deref if ptr == NULL */
    }
}

/* Bug 3：integer overflow → out-of-bounds read */
static void parse_footer(const uint8_t *buf, size_t len) {
    if (len < 4) return;
    uint8_t a = buf[1];
    uint8_t b = buf[2];
    /* BUG: a * b 可能溢出 uint8，但被用作 index */
    uint8_t computed_len = (uint8_t)(a * b);   /* 可能 overflow */
    if (computed_len == 0) return;
    char *result = malloc(16);
    if (!result) return;
    /* 若 computed_len > 16，越界讀取 buf */
    memcpy(result, buf + 3, computed_len);  /* OOB read if computed_len > len-3 */
    printf("footer checksum: %02x\n", result[0]);
    free(result);
}

int main(int argc, char *argv[]) {
    if (argc < 2) {
        fprintf(stderr, "Usage: %s <input_file>\n", argv[0]);
        return 1;
    }

    FILE *f = fopen(argv[1], "rb");
    if (!f) {
        perror("fopen");
        return 1;
    }

    uint8_t buf[256];
    size_t n = fread(buf, 1, sizeof(buf), f);
    fclose(f);

    if (n < 1) return 0;

    switch (buf[0]) {
        case 'H': parse_header(buf, n); break;
        case 'B': parse_body(buf, n);   break;
        case 'F': parse_footer(buf, n); break;
        default: break;
    }

    return 0;
}
CEOF

# 編譯三個版本
# 版本 1：AFL++ instrumentation（用於 fuzzing）
afl-clang-fast -o practice_target_afl practice_target.c
echo "[+] AFL version compiled: practice_target_afl"

# 版本 2：ASAN（用於 triage）
AFL_USE_ASAN=1 afl-clang-fast -o practice_target_asan practice_target.c
echo "[+] ASAN version compiled: practice_target_asan"

# 版本 3：帶 debug info（用於 GDB）
afl-clang-fast -g -O0 -o practice_target_debug practice_target.c
echo "[+] Debug version compiled: practice_target_debug"

# 建立初始 seed corpus
mkdir -p seeds/
printf 'H\x09AAAAAAA' > seeds/seed_h   # type H，payload_len=9，接近 overflow 邊界
printf 'B\x00\x00\x00\x00\x00' > seeds/seed_b  # type B
printf 'F\x02\x03XXX' > seeds/seed_f   # type F
echo "[+] Seeds created in seeds/"
```

---

### Step 2：執行 Fuzzing Session（取得 corpus 和 crashes）

```bash
cd ~/afl_practice_c

# 關閉 ASLR（重現性更好）
echo 0 | sudo tee /proc/sys/kernel/randomize_va_space

# 跑 30 分鐘（足夠觸發三個 bug）
# -t 500：500ms timeout（target 很簡單，給短一點）
timeout 1800 afl-fuzz \
    -i seeds/ \
    -o out/ \
    -t 500 \
    -- ./practice_target_afl @@ 2>/dev/null || true

echo "[+] Fuzzing session completed"
echo "Corpus size: $(ls out/default/queue/ | grep 'id:' | wc -l) testcases"
echo "Crashes: $(ls out/default/crashes/ | grep 'id:' | wc -l) files"
echo "Hangs: $(ls out/default/hangs/ | grep 'id:' | wc -l) files"
```

**如果 30 分鐘後 crash 數量不夠**，可以用 dictionary 加速：

```bash
# 建立針對性 dictionary
cat > practice.dict << 'EOF'
type_h="H"
type_b="B"
type_f="F"
magic_dead="\xde\xad"
magic_ff="\xff"
EOF

# 重新跑（加 -x）
timeout 1800 afl-fuzz \
    -x practice.dict \
    -i seeds/ \
    -o out_dict/ \
    -t 500 \
    -- ./practice_target_afl @@ 2>/dev/null || true
```

---

### Step 3：Corpus 最小化（afl-cmin）

```bash
cd ~/afl_practice_c

# 確認 corpus 大小
echo "Before cmin: $(ls out/default/queue/ | grep 'id:' | wc -l) testcases"

# 執行 cmin
# -i：輸入 corpus 目錄
# -o：輸出最小化後的 corpus
# -t：timeout
mkdir -p cmin_out/

afl-cmin \
    -i out/default/queue/ \
    -o cmin_out/ \
    -t 500 \
    -- ./practice_target_afl @@

echo "After cmin: $(ls cmin_out/ | wc -l) testcases"
echo "Reduction: $(echo "scale=1; (1 - $(ls cmin_out/ | wc -l) / $(ls out/default/queue/ | grep 'id:' | wc -l)) * 100" | bc)%"
```

**驗證 cmin 結果**

```bash
# cmin 後的 corpus 應該有相同或更好的 coverage
# 用 afl-showmap 比對
afl-showmap -C -i out/default/queue/ -o /tmp/map_orig -- ./practice_target_afl @@
afl-showmap -C -i cmin_out/ -o /tmp/map_cmin -- ./practice_target_afl @@

# 比對 unique tuples 數量（應該相同）
echo "Original corpus unique tuples: $(wc -l < /tmp/map_orig)"
echo "cmin corpus unique tuples: $(wc -l < /tmp/map_cmin)"
```

---

### Step 4：Crash Testcase 最小化（afl-tmin）

```bash
cd ~/afl_practice_c

# 建立目錄存放最小化後的 crash
mkdir -p tmin_crashes/

# 統計 crash 數量和 signal 分布
echo "=== Total crashes ==="
ls out/default/crashes/ | grep 'id:' | wc -l

echo "=== Signal distribution ==="
ls out/default/crashes/ | grep 'id:' | grep -oP 'sig:\K[0-9]+' | sort | uniq -c | sort -rn

# 策略：先對每個 signal group 取 5 個代表，全部 tmin
# 處理 SIGSEGV crashes（sig:11）
echo "--- Minimizing SIGSEGV crashes ---"
SIGSEGV_CRASHES=$(ls out/default/crashes/ | grep 'id:' | grep 'sig:11' | head -5)
for crash in $SIGSEGV_CRASHES; do
    output="tmin_crashes/tmin_${crash}"
    echo "Minimizing: $crash"
    afl-tmin \
        -i "out/default/crashes/$crash" \
        -o "$output" \
        -t 500 \
        -- ./practice_target_afl @@ 2>/dev/null
    echo "  $(wc -c < "out/default/crashes/$crash") → $(wc -c < "$output") bytes"
done

# 處理 SIGABRT crashes（sig:06）
echo "--- Minimizing SIGABRT crashes ---"
SIGABRT_CRASHES=$(ls out/default/crashes/ | grep 'id:' | grep 'sig:06' | head -5)
for crash in $SIGABRT_CRASHES; do
    output="tmin_crashes/tmin_${crash}"
    echo "Minimizing: $crash"
    afl-tmin \
        -i "out/default/crashes/$crash" \
        -o "$output" \
        -t 500 \
        -- ./practice_target_afl @@ 2>/dev/null
    echo "  $(wc -c < "out/default/crashes/$crash") → $(wc -c < "$output") bytes"
done

echo "[+] Minimized crashes saved to tmin_crashes/"
```

---

### Step 5：按 Signal 分組與去重

```bash
cd ~/afl_practice_c

echo "=== Step 5: Grouping and deduplication ==="

# 方法 1：按 signal 分組
echo "--- By Signal ---"
ls out/default/crashes/ | grep 'id:' | grep -oP 'sig:\K[0-9]+' | \
    sort | uniq -c | sort -rn | while read count sig; do
    case $sig in
        11) echo "  $count × SIGSEGV (sig:11) — illegal memory access" ;;
        06) echo "  $count × SIGABRT (sig:06) — abort/ASAN/double-free" ;;
        07) echo "  $count × SIGBUS  (sig:07) — bus error/alignment" ;;
        08) echo "  $count × SIGFPE  (sig:08) — divide by zero" ;;
        04) echo "  $count × SIGILL  (sig:04) — illegal instruction/UBSan" ;;
        *)  echo "  $count × SIG:$sig" ;;
    esac
done

# 方法 2：按 tmin 後的 SHA256 去重（找真正 unique 的 testcase）
echo ""
echo "--- Deduplication by SHA256 (tmin outputs) ---"
for f in tmin_crashes/*; do
    sha256sum "$f"
done | sort -k1 | awk '
    {
        if ($1 != prev_hash) {
            print "UNIQUE: " $0
            unique++
        } else {
            print "  DUP: " $0
        }
        prev_hash = $1
    }
    END { print "\nUnique crash testcases: " unique }
'

# 方法 3：按 source seed 分組
echo ""
echo "--- By Source Seed (top 10) ---"
ls out/default/crashes/ | grep 'id:' | grep -oP 'src:\K[0-9]+' | \
    sort | uniq -c | sort -rn | head -10
```

---

### Step 6：用 GDB 確認 Crash 唯一性

```bash
cd ~/afl_practice_c

# 確保 debug 版本已編譯
ls practice_target_debug || afl-clang-fast -g -O0 -o practice_target_debug practice_target.c

echo "=== Step 6: GDB Analysis ==="

# 函式：用 GDB 分析一個 crash，輸出關鍵資訊
analyze_crash_gdb() {
    local crash_file="$1"
    echo "--- Analyzing: $crash_file ---"

    # 設定 ASLR 關閉並執行
    setarch $(uname -m) -R gdb -q -batch \
        -ex "set pagination off" \
        -ex "run $crash_file" \
        -ex "info signal" \
        -ex "bt 5" \
        -ex "info registers rip rdi rsi rdx rax" \
        -ex "quit" \
        ./practice_target_debug 2>&1 | \
        grep -E "signal|Program received|#[0-9]|0x[0-9a-f]+ in |rip|rdi"
    echo ""
}

# 分析每個 unique tmin crash
for f in tmin_crashes/*; do
    analyze_crash_gdb "$f"
done
```

**用 ASAN 取得更詳細的錯誤報告**

```bash
echo "=== ASAN Analysis ==="

analyze_crash_asan() {
    local crash_file="$1"
    echo "--- ASAN report for: $crash_file ---"
    setarch $(uname -m) -R \
        ./practice_target_asan "$crash_file" 2>&1 | \
        grep -A 10 "ERROR: AddressSanitizer" | head -15
    echo ""
}

for f in tmin_crashes/*; do
    analyze_crash_asan "$f"
done
```

---

### Step 7：確認 Bug 覆蓋完整性

目標：確認 3 個植入的 bug 全部被找到。

```bash
cd ~/afl_practice_c

echo "=== Step 7: Coverage Check ==="

# 直接手動觸發三個 bug，確認 target 確實有這三個 crash
echo "--- Manual trigger: Bug 1 (heap buffer overflow) ---"
python3 -c "import sys; sys.stdout.buffer.write(b'H\x10' + b'A'*20)" > /tmp/bug1.bin
./practice_target_asan /tmp/bug1.bin 2>&1 | grep -E "ERROR:|#0|heap-buffer"

echo ""
echo "--- Manual trigger: Bug 2 (null dereference) ---"
python3 -c "import sys; sys.stdout.buffer.write(b'B\x00\x00\x00\xff\x00')" > /tmp/bug2.bin
./practice_target_asan /tmp/bug2.bin 2>&1 | grep -E "ERROR:|#0|SIGSEGV|null"

echo ""
echo "--- Manual trigger: Bug 3 (integer overflow → OOB read) ---"
python3 -c "import sys; sys.stdout.buffer.write(b'F\x10\x10' + b'A'*5)" > /tmp/bug3.bin
./practice_target_asan /tmp/bug3.bin 2>&1 | grep -E "ERROR:|#0|heap-buffer|READ"

echo ""
echo "--- Compare with fuzzer-found crashes ---"
echo "Check if each bug's signature appears in tmin_crashes/"

# 用 xxd 檢查 tmin 後的 crash 第一個 byte（type dispatcher）
for f in tmin_crashes/*; do
    type_byte=$(xxd "$f" | head -1 | awk '{print $2}' | cut -c1-2)
    echo "  $f → first byte: 0x$type_byte"
done | sort -k4
```

---

### Step 8：撰寫 Bug Report

```bash
cd ~/afl_practice_c

cat > bug_report.txt << 'REPORT_EOF'
=============================================================
  Fuzzing Session Bug Report
  Target: practice_target
  Session: 30-minute AFL++ 4.09c run
  Date: $(date +%Y-%m-%d)
=============================================================

SUMMARY
-------
Total crashes found: [填入數字]
After triage (unique bugs): 3

BUG LIST
--------

Bug #1: Heap Buffer Overflow (Write) in parse_header()
  Severity: HIGH (potentially exploitable write primitive)
  Signal: SIGSEGV (sig:11)
  Function: parse_header() @ practice_target.c:22
  Root Cause:
    malloc(8) allocates fixed 8-byte buffer, but memcpy uses
    payload_len (uint8, 0-255) from user input. Input with
    payload_len > 8 causes heap overflow.
  PoC:
    python3 -c "import sys; sys.stdout.buffer.write(b'H\x10' + b'A'*20)" > poc1.bin
    ./practice_target_debug poc1.bin
  Fix: Change malloc(8) to malloc(payload_len + 1) or validate payload_len <= 8.

Bug #2: Null Pointer Dereference in parse_body()
  Severity: MEDIUM (crash, likely not exploitable without further work)
  Signal: SIGSEGV (sig:11)
  Function: parse_body() @ practice_target.c:35
  Root Cause:
    ptr is initialized to NULL. Only set to a valid pointer when
    buf[2]==0xDE && buf[3]==0xAD. Any input with buf[4]==0xFF but
    without the magic bytes causes null dereference.
  PoC:
    python3 -c "import sys; sys.stdout.buffer.write(b'B\x00\x00\x00\xff')" > poc2.bin
    ./practice_target_debug poc2.bin
  Fix: Add null check before `*ptr = flag`, or initialize ptr to a valid target.

Bug #3: Integer Overflow → Out-of-Bounds Read in parse_footer()
  Severity: MEDIUM (info leak potential)
  Signal: SIGABRT (via ASAN) or silent without ASAN
  Function: parse_footer() @ practice_target.c:47
  Root Cause:
    computed_len = (uint8_t)(a * b). Values like a=16, b=16 give
    computed_len=0 (overflow), but values like a=10, b=20 give
    computed_len=200 (wraps to 200 as uint8), causing memcpy to
    read 200 bytes from a buffer that may only have a few valid bytes.
  Note: This bug is SILENT without ASAN (no crash, just memory disclosure).
  PoC:
    python3 -c "import sys; sys.stdout.buffer.write(b'F\x0a\x14' + b'X'*5)" > poc3.bin
    AFL_USE_ASAN=1 ./practice_target_asan poc3.bin   # crashes with ASAN
    ./practice_target_debug poc3.bin                 # may NOT crash
  Fix: Check that (size_t)a * b <= len - 3 before memcpy.

METHODOLOGY
-----------
1. afl-clang-fast instrumentation, 30-min fuzzing session
2. afl-cmin: reduced corpus from [N] to [M] testcases
3. afl-tmin: minimized each crash representative
4. Signal-based grouping → 2 signal types (sig:11, sig:06)
5. SHA256 dedup of tmin output → [K] unique testcases
6. GDB backtrace + ASAN report → confirmed 3 root causes
7. Manual PoC construction and reproduction

REPRODUCTION
------------
All bugs reproduced on:
  Ubuntu 22.04 LTS x86_64
  AFL++ 4.09c / afl-clang-fast
  gcc 11.4.0 (via clang-14)
REPORT_EOF

# 填入實際數字
TOTAL_CRASHES=$(ls out/default/crashes/ | grep 'id:' | wc -l)
sed -i "s/\[填入數字\]/$TOTAL_CRASHES/" bug_report.txt

echo "[+] Bug report written to bug_report.txt"
cat bug_report.txt
```

---

## 完整參考解答

<details>
<summary>展開參考解答（先自己做完再看）</summary>

### 核心觀念確認

**為什麼 afl-cmin 能大幅縮減 corpus？**

`afl-cmin` 使用集合覆蓋演算法（set cover）：對每個 edge，選擇能覆蓋這個 edge 的最小 testcase 集合。理論下界是 NP-hard，AFL++ 用 greedy 近似：先選覆蓋最多 new edge 的 testcase，重複直到所有 edge 都被覆蓋。

通常 80-95% 的 testcase 是「冗余的」——它們走的 edge 都已經被其他更小的 testcase 覆蓋了。

**為什麼 200 個 crash 可能只有 3 個 bug？**

AFL++ 的 bitmap-based uniqueness 會把同一個 bug 的不同觸發路徑記為不同 crash。Bug 1（heap overflow）可能有 100 個觸發路徑：

- payload_len = 9（overflow 1 byte）
- payload_len = 16（overflow 8 bytes）
- payload_len = 255（overflow 247 bytes）
- payload_len = 9，payload 前 5 bytes 是 "AAABB"
- payload_len = 9，payload 前 5 bytes 是 "BBBBB"
- ...

每條路徑都有略微不同的 bitmap（因為前置路徑的 conditional branch 可能有差異），所以每條都被記錄。

**tmin 後 SHA256 去重為什麼有效？**

`afl-tmin` 把 testcase 縮到能 crash 的最小形式。對同一個 bug，多個不同的觸發 testcase，tmin 後往往收斂到相同的最小 testcase（因為它們共用相同的 root cause，最小表示只保留觸發 root cause 的必要 bytes）。

不過這不是 100% 可靠——有時候兩個 path 到同一個 bug，tmin 後仍然是不同的 testcase（因為最小化路徑不同）。SHA256 去重是**輔助指標**，最後還是要看 GDB backtrace。

### Bug 3 的特殊性

Bug 3（integer overflow → OOB read）在沒有 ASAN 時通常**不會 crash**：

```
computed_len = (uint8_t)(10 * 20) = 200
memcpy(result, buf + 3, 200)
```

`buf` 只有 256 bytes 的 stack buffer，`buf + 3` 往後 200 bytes 可能還在 stack frame 裡，或者進入了其他合法 mmap 的記憶體（stack page 通常很大）。這種情況下程式讀到垃圾值但不 crash。

這就是為什麼啟用 ASAN 對 fuzzing 很重要：ASAN 能捕捉到 AFL++ 無法用 crash 信號偵測的「靜默的」記憶體錯誤。

### GDB 分析備忘

```bash
# 快速提取 crash 地址
gdb -batch -ex "run $crash" -ex "p/x \$rip" -ex "p/x \$rdi" ./practice_target_debug 2>&1

# 分辨 heap vs stack 的 crash 地址
# heap address:  0x5555...（典型範圍，但取決於 ASLR）
# stack address: 0x7fff...（典型範圍）
# NULL deref:    0x0000000000000000 或極小地址

# 看 crash 在哪個函式
gdb -batch -ex "run $crash" -ex "bt 3" ./practice_target_debug 2>&1 | grep "in parse_"
```

### 效率技巧

對 200 個 crashes 做 triage 的實際工作流程（不是每個都 tmin，那太慢）：

```bash
# Phase 1：5 分鐘快速分組
ls out/default/crashes/ | grep 'id:' | grep -oP 'sig:\K[0-9]+' | sort | uniq -c
# → 知道有幾種 signal 類型

# Phase 2：每個 signal group 取 10 個代表，做 tmin
# → 通常 30-60 分鐘

# Phase 3：tmin 後 SHA256 去重
# → 找出 unique 的最小數量

# Phase 4：對每個 unique，跑 GDB + ASAN 確認 root cause
# → 這是最重要的一步，但只需要做 unique 的數量次（通常 3-10 次）

# Phase 5：對所有剩餘 crash 快速驗證
# → 看是否有不屬於已知 bug 的 crash（回到 Phase 4）
```

</details>

---

## 測試用例：各步驟的預期輸出

### 測試 1：afl-cmin 的 corpus 縮減率

```bash
# 預期：縮減 80% 以上
before=$(ls out/default/queue/ | grep 'id:' | wc -l)
after=$(ls cmin_out/ | wc -l)
reduction=$(echo "scale=1; ($before - $after) * 100 / $before" | bc)
echo "Reduction: ${reduction}%"

# 驗收標準
if [ "$(echo "$reduction > 80" | bc)" = "1" ]; then
    echo "PASS: corpus reduced by more than 80%"
else
    echo "FAIL: expected >80% reduction, got ${reduction}%"
fi
```

### 測試 2：三個 bug 都被找到

```bash
# 手動驗證三個 PoC 都能重現
test_bug() {
    local bug_num="$1"
    local poc_file="$2"
    ./practice_target_asan "$poc_file" 2>&1 | grep -q "AddressSanitizer" && \
        echo "Bug $bug_num: FOUND" || \
        echo "Bug $bug_num: NOT reproduced (check poc)"
}

test_bug 1 /tmp/bug1.bin
test_bug 2 /tmp/bug2.bin
test_bug 3 /tmp/bug3.bin
```

### 測試 3：tmin 確實縮小了 testcase

```bash
# 驗證 tmin 輸出比原始 crash 小
all_smaller=true
for f in tmin_crashes/*; do
    orig_name=$(basename "$f" | sed 's/^tmin_//')
    orig_size=$(wc -c < "out/default/crashes/$orig_name" 2>/dev/null || echo 999)
    tmin_size=$(wc -c < "$f")
    if [ "$tmin_size" -gt "$orig_size" ]; then
        echo "FAIL: $f is larger than original ($tmin_size > $orig_size)"
        all_smaller=false
    fi
done
$all_smaller && echo "PASS: all tmin outputs are smaller or equal to originals"
```

---

## 延伸挑戰

### 挑戰 1：啟用 AFL_DEBUG=1 觀察 crash 偵測機制

```bash
# 用 AFL_DEBUG 看 AFL++ 如何記錄 crash
AFL_DEBUG=1 timeout 10 afl-fuzz \
    -i seeds/ \
    -o out_debug/ \
    -- ./practice_target_afl @@ 2>&1 | \
    grep -E "crash|CRASH|sig:|bitmap" | head -20

# 特別注意：
# "Saving crash..." 出現時的 bitmap 狀態
# "Crash in the seed corpus!" 如果 seed 本身就 crash
```

### 挑戰 2：ASAN session 的重新 Triage

```bash
# 用 ASAN 版本重新跑一個短 session
AFL_USE_ASAN=1 timeout 600 afl-fuzz \
    -i cmin_out/ \
    -o out_asan/ \
    -t 2000 \
    -- ./practice_target_asan @@ 2>/dev/null || true

# 問題：
# 1. ASAN session 的 crash signal 分布和原始 session 有什麼不同？
# 2. ASAN 有沒有找到原始 session 沒有找到的 crash？
# 3. Bug 3（OOB read）在 ASAN session 裡的 signal 是什麼？

echo "=== ASAN session signal distribution ==="
ls out_asan/default/crashes/ | grep 'id:' | grep -oP 'sig:\K[0-9]+' | \
    sort | uniq -c | sort -rn
```

### 挑戰 3：自動化 Triage 腳本

撰寫一個腳本 `auto_triage.sh`，接受 `out/` 目錄路徑，自動完成：

1. Signal 統計
2. 每個 signal group 取前 10 個做 tmin
3. SHA256 去重
4. ASAN 報告提取
5. 輸出 Markdown 格式的 triage report

預期呼叫方式：

```bash
./auto_triage.sh out/ ./practice_target_asan ./practice_target_afl > triage_report.md
```

---

## 自我檢核

1. `afl-cmin` 和 `afl-tmin` 的目的不同：一個縮減 corpus，一個縮小個別 testcase。用你自己的話解釋這個差異，以及各自在 triage 流程中扮演的角色。

2. 你有 200 個 crash，其中 180 個是 `sig:11`（SIGSEGV），20 個是 `sig:06`（SIGABRT）。這兩組各自最可能對應什麼類型的 bug？你會先分析哪一組？為什麼？

3. `afl-tmin` 對一個 crash 跑了 5 分鐘，把 87 bytes 縮到 6 bytes。你現在有 5 個 tmin 後的 crash，SHA256 顯示只有 2 個是 unique 的。但 GDB 分析顯示這 2 個其實是同一個 bug（相同的 faulting address，相同的 backtrace）。為什麼 tmin 後 SHA256 不同，但 GDB 顯示是同一個 bug？

4. Bug 3（integer overflow → OOB read）在沒有 ASAN 的 session 裡可能完全沒有 crash。這對 fuzzing 工作流程有什麼啟示？你應該如何設定 fuzzing 環境來避免漏掉這類 bug？

5. 在 Step 6 的 GDB 分析中，你發現一個 crash 的 faulting address 是 `0x4141414141414141`（'AAAA...AAAA' 的 ASCII）。這意味著什麼？這個 crash 的 exploitability 評估會是什麼？
