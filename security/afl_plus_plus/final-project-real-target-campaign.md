# Final Project — 實戰 Fuzzing Campaign

> **目標**：整合全課 80% 以上的核心概念，對一個真實的開源 library 執行完整的 fuzzing campaign，
> 從選 target 到 bug report 全流程。

> **環境**：AFL++ 4.09c, Ubuntu 22.04 LTS, x86_64, 建議 4 核以上

---

## 專案背景

這個 final project 選 **libpng 1.6.x** 作為 fuzzing target。

選 libpng 的原因：

1. **廣泛使用**：幾乎所有處理 PNG 的程式都依賴它（Firefox、Chrome、ImageMagick、Python Pillow 底層）。在 libpng 找到 bug 的影響範圍極廣。

2. **有真實 CVE 歷史**：CVE-2015-8472（heap overflow）、CVE-2016-3751（out-of-bounds write）、CVE-2019-7317（use-after-free）都是 libpng 的真實漏洞。舊版（1.6.20 以前）已知有可以找到的 bug。

3. **格式複雜但有 spec 可參考**：PNG 有嚴謹的 chunk-based 格式規範（RFC 2083），chunk type（IHDR、IDAT、PLTE...）可以做成 dictionary，展示 Ch 13 的技術。

4. **適合展示課程核心技術的組合**：
   - LTO 插樁（Ch 6）
   - Persistent mode（Ch 7）
   - CmpLog（Ch 15）——PNG 有大量的 4 bytes magic number 比較
   - Dictionary（Ch 13）——chunk type 是現成的 token
   - Parallel fuzzing（Ch 11）
   - Crash triage（Ch 22）
   - Coverage measurement（Ch 23）

---

## 目標說明

你要完成以下所有里程碑，按順序進行：

### 里程碑 1：環境與 Target 設置
- [ ] 下載並編譯 libpng 1.6.40（三種 build：PCGUARD、LTO、ASAN+LTO）
- [ ] 寫出 persistent mode harness，直接呼叫 `png_read_info`
- [ ] 準備初始 seed corpus（至少 5 個合法 PNG，大小 < 1KB）

### 里程碑 2：基線 Fuzzing
- [ ] 用 PCGUARD build 跑 1 小時，記錄 `execs_per_sec` 和 `total_edges`
- [ ] 啟用 CmpLog，對比有無 CmpLog 的前 30 分鐘 coverage 差異
- [ ] 啟動 4-core parallel fuzzing session

### 里程碑 3：Crash Triage
- [ ] 用 `afl-cmin` 對 crash corpus 做最小化
- [ ] 確認獨特 crash 數量（bitmap-based 和 stack hash 兩種方法）
- [ ] 用 `afl-tmin` 縮小最重要的 crash（最多保留前 3 個）
- [ ] 用 ASAN+LTO build 重現 crash，取得完整 stack trace

### 里程碑 4：Bug Report
- [ ] 撰寫標準格式的 bug report（影響版本、重現步驟、PoC、crash output）

---

## 環境要求

```bash
# 系統套件
sudo apt-get update
sudo apt-get install -y \
    build-essential git wget \
    clang llvm lld \
    libz-dev \
    python3 python3-pip \
    gdb \
    lcov

# 確認 AFL++ 已安裝
afl-fuzz --version  # 應該顯示 4.09c 或更新

# 確認 clang 版本（需要 12+）
clang --version

# 建立工作目錄
mkdir -p ~/fuzzing_libpng
cd ~/fuzzing_libpng
```

---

## 里程碑 1：環境與 Target 設置

### 步驟 1.1：下載 libpng

```bash
cd ~/fuzzing_libpng

wget https://download.sourceforge.net/libpng/libpng-1.6.40.tar.gz
# 如果 sourceforge 慢，也可以用：
# wget https://github.com/pnggroup/libpng/archive/refs/tags/v1.6.40.tar.gz

tar -xzf libpng-1.6.40.tar.gz
```

### 步驟 1.2：三種 Build

你需要三個獨立的 build，各自有不同用途：

**Build 1：PCGUARD（基線比較用）**

```bash
cd ~/fuzzing_libpng
cp -r libpng-1.6.40 libpng-pcguard
cd libpng-pcguard

CC=afl-clang-fast \
CXX=afl-clang-fast++ \
    ./configure --disable-shared --prefix=$(pwd)/install
make -j$(nproc)
make install

cd ~/fuzzing_libpng
```

**Build 2：LTO（主要 fuzzing 用）**

```bash
cp -r libpng-1.6.40 libpng-lto
cd libpng-lto

CC=afl-clang-lto \
CXX=afl-clang-lto++ \
AR=llvm-ar \
RANLIB=llvm-ranlib \
    ./configure --disable-shared --prefix=$(pwd)/install
make -j$(nproc)
make install

cd ~/fuzzing_libpng
```

**Build 3：ASAN+LTO（crash triage 用）**

```bash
cp -r libpng-1.6.40 libpng-asan
cd libpng-asan

CC=afl-clang-lto \
CXX=afl-clang-lto++ \
AR=llvm-ar \
RANLIB=llvm-ranlib \
CFLAGS="-fsanitize=address -g" \
CXXFLAGS="-fsanitize=address -g" \
LDFLAGS="-fsanitize=address" \
    ./configure --disable-shared --prefix=$(pwd)/install
make -j$(nproc)
make install

cd ~/fuzzing_libpng
```

### 步驟 1.3：寫 Persistent Mode Harness

這個 harness 是整個 fuzzing campaign 的核心，要用心設計。

```c
// fuzz_png.c
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

// AFL++ persistent mode 的 SHM 初始化
// 放在所有 include 之後，main() 之前
#ifdef __AFL_HAVE_MANUAL_CONTROL
    #include "alloc-inl.h"
#endif

// PNG library header
#include "png.h"

// 自定義的 error callback：把 libpng 的 longjmp 轉成正常 return
// 避免 longjmp 跳過 AFL++ 的 cleanup code
static void png_error_fn(png_structp png_ptr, png_const_charp error_msg) {
    // 觸發 longjmp，回到 setjmp 的位置
    png_longjmp(png_ptr, 1);
}

static void png_warning_fn(png_structp png_ptr, png_const_charp warning_msg) {
    // 忽略 warning
    (void)warning_msg;
}

// 實際的 fuzzing 函式：接受 raw bytes，呼叫 libpng API
static int fuzz_one(const uint8_t *data, size_t size) {
    if (size < 8) return 0;  // PNG header 至少需要 8 bytes

    png_structp png_ptr = NULL;
    png_infop info_ptr = NULL;

    // Step 1：建立 png_struct（每次 iteration 都要新建，確保 state 乾淨）
    png_ptr = png_create_read_struct(PNG_LIBPNG_VER_STRING,
                                     NULL,
                                     png_error_fn,   // 自定義 error handler
                                     png_warning_fn); // 自定義 warning handler
    if (!png_ptr) return 0;

    info_ptr = png_create_info_struct(png_ptr);
    if (!info_ptr) {
        png_destroy_read_struct(&png_ptr, NULL, NULL);
        return 0;
    }

    // Step 2：設定 error recovery（setjmp）
    // 如果 libpng 遇到格式錯誤，會 longjmp 到這裡
    if (setjmp(png_jmpbuf(png_ptr))) {
        // 格式錯誤，正常退出（不是 bug）
        png_destroy_read_struct(&png_ptr, &info_ptr, NULL);
        return 0;
    }

    // Step 3：設定 memory-based 讀取（不用 FILE*）
    // libpng 1.6.x 支援 png_set_read_fn 做客製化 I/O

    // 把 input data 包成一個 cursor struct
    struct {
        const uint8_t *data;
        size_t size;
        size_t pos;
    } reader = {data, size, 0};

    // 自定義 read callback
    void read_fn(png_structp p, png_bytep out, png_size_t len) {
        __typeof__(reader) *r = png_get_io_ptr(p);
        if (r->pos + len > r->size) {
            // 讀超出範圍：觸發 error
            png_error(p, "read past end");
        }
        memcpy(out, r->data + r->pos, len);
        r->pos += len;
    }

    png_set_read_fn(png_ptr, &reader, read_fn);

    // Step 4：讀取 PNG info（header + chunks）
    // 這裡會解析所有 chunk，是 bug 最多的地方
    png_read_info(png_ptr, info_ptr);

    // Step 5：嘗試讀取圖像資料（optional，但能覆蓋更多 code path）
    png_uint_32 width = png_get_image_width(png_ptr, info_ptr);
    png_uint_32 height = png_get_image_height(png_ptr, info_ptr);

    // 限制大小，避免 fuzzer 產生超大圖像讓 harness 跑得很慢
    if (width > 0 && width <= 1024 && height > 0 && height <= 1024) {
        size_t row_bytes = png_get_rowbytes(png_ptr, info_ptr);
        uint8_t *row_buf = malloc(row_bytes);
        if (row_buf) {
            for (png_uint_32 y = 0; y < height; y++) {
                png_read_row(png_ptr, row_buf, NULL);
            }
            free(row_buf);
        }
    }

    // Step 6：清理（每次 iteration 必做，否則 memory leak 累積）
    png_destroy_read_struct(&png_ptr, &info_ptr, NULL);
    return 0;
}

// AFL++ persistent mode 的 main
int main(int argc, char **argv) {
    // __AFL_FUZZ_INIT() 必須在任何 AFL++ SHM 存取之前呼叫
    __AFL_FUZZ_INIT();

    // persistent mode loop：AFL++ 會在這個 loop 裡反覆送 input
    // 1000 是每個 fork 最多跑幾次 iteration（之後重新 fork 避免 state 累積）
    while (__AFL_LOOP(1000)) {
        // __AFL_FUZZ_TESTCASE_BUF 和 __AFL_FUZZ_TESTCASE_LEN 是 SHM 裡的 input
        const uint8_t *buf = __AFL_FUZZ_TESTCASE_BUF;
        size_t len = __AFL_FUZZ_TESTCASE_LEN;

        fuzz_one(buf, len);
    }

    return 0;
}
```

**注意**：上面的 nested function（`read_fn` 定義在 `fuzz_one` 裡）是 GCC extension，標準 C99 不支援。如果遇到編譯錯誤，把 `read_fn` 移到檔案的頂層，用 global struct 傳遞 reader state：

```c
// 替代方案：頂層 read callback
static struct {
    const uint8_t *data;
    size_t size;
    size_t pos;
} g_reader;

static void read_fn(png_structp p, png_bytep out, png_size_t len) {
    if (g_reader.pos + len > g_reader.size) {
        png_error(p, "read past end");
    }
    memcpy(out, g_reader.data + g_reader.pos, len);
    g_reader.pos += len;
}
```

### 步驟 1.4：編譯 Harness

```bash
cd ~/fuzzing_libpng

# PCGUARD build 的 harness
afl-clang-fast -o fuzz_png_pcguard fuzz_png.c \
    -I libpng-pcguard/install/include \
    -L libpng-pcguard/install/lib \
    -Wl,-rpath,$(pwd)/libpng-pcguard/install/lib \
    -lpng16 -lz -lm

# LTO build 的 harness（主力）
afl-clang-lto -o fuzz_png_lto fuzz_png.c \
    -I libpng-lto/install/include \
    -L libpng-lto/install/lib \
    -Wl,-rpath,$(pwd)/libpng-lto/install/lib \
    -lpng16 -lz -lm

# ASAN+LTO build 的 harness（triage 用）
afl-clang-lto -fsanitize=address -g \
    -o fuzz_png_asan fuzz_png.c \
    -I libpng-asan/install/include \
    -L libpng-asan/install/lib \
    -Wl,-rpath,$(pwd)/libpng-asan/install/lib \
    -lpng16 -lz -lm

# 確認全部編譯成功
ls -la fuzz_png_*
```

### 步驟 1.5：準備 Seed Corpus

```bash
mkdir -p seeds/

# 方法 1：用系統裡已有的小 PNG
find /usr/share -name "*.png" -size -1k 2>/dev/null | head -10 | \
    xargs -I{} cp {} seeds/

# 方法 2：用 Python 生成最小合法 PNG（純色 1x1 pixel）
python3 << 'EOF'
import struct, zlib

def make_png(r=0, g=0, b=0):
    def chunk(name, data):
        c = name + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    header = b'\x89PNG\r\n\x1a\n'
    ihdr = chunk(b'IHDR', struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    idat_raw = b'\x00' + bytes([r, g, b])  # filter byte + RGB
    idat = chunk(b'IDAT', zlib.compress(idat_raw))
    iend = chunk(b'IEND', b'')
    return header + ihdr + idat + iend

# 生成幾個不同顏色的 1x1 PNG
colors = [(0,0,0), (255,0,0), (0,255,0), (0,0,255), (128,128,128)]
for i, (r,g,b) in enumerate(colors):
    with open(f'seeds/seed_{i:02d}.png', 'wb') as f:
        f.write(make_png(r, g, b))
    print(f"seeds/seed_{i:02d}.png: {len(make_png(r,g,b))} bytes")
EOF

ls -la seeds/
echo "Seed count: $(ls seeds/ | wc -l)"
```

### 步驟 1.6：準備 PNG Dictionary

```bash
# PNG chunk types（4 bytes 每個）
cat > png.dict << 'EOF'
# PNG signature
header="\x89PNG\r\n\x1a\n"
# Critical chunks
ihdr="IHDR"
idat="IDAT"
iend="IEND"
plte="PLTE"
# Ancillary chunks
bkgd="bKGD"
chrm="cHRM"
gama="gAMA"
hist="hIST"
iccp="iCCP"
itxt="iTXt"
phys="pHYs"
sbit="sBIT"
splt="sPLT"
srgb="sRGB"
text="tEXt"
time="tIME"
trns="tRNS"
ztxt="zTXt"
# Common color types
ct_rgb="\x02"
ct_rgba="\x06"
ct_palette="\x03"
ct_gray="\x00"
EOF
```

---

## 里程碑 2：基線 Fuzzing

### 步驟 2.1：基線測試（PCGUARD，1 小時）

先用 PCGUARD build 跑 1 小時，取得基線數字：

```bash
# 確認 CPU frequency scaling 設定（AFL++ 的建議）
echo performance | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor

# 基線跑 1 小時（background，讓它跑）
afl-fuzz \
    -i seeds/ \
    -o out_pcguard/ \
    -x png.dict \
    -t 1000 \
    -- ./fuzz_png_pcguard &

AFL_PCGUARD_PID=$!

# 等 5 分鐘後看初始狀態
sleep 300
echo "=== 5 minutes in ==="
cat out_pcguard/default/fuzzer_stats | grep -E "execs_per_sec|total_edges|unique_crashes"

# 讓它跑滿 1 小時
sleep 3300  # 3300 + 300 = 3600 秒 = 1 小時
kill $AFL_PCGUARD_PID 2>/dev/null

echo "=== 1 hour PCGUARD results ==="
cat out_pcguard/default/fuzzer_stats | \
    grep -E "execs_per_sec|total_edges|unique_crashes|execs_done"
```

預期結果（1 核 PCGUARD，libpng persistent mode）：
- `execs_per_sec`：10000–30000
- `total_edges`：1500–3000

### 步驟 2.2：啟用 CmpLog，比較差異

```bash
# CmpLog 需要兩個 build：一個正常 LTO，一個 CmpLog instrumented
# CmpLog build：AFL++ 自動處理，只需要加 -c 參數

# 沒有 CmpLog 的 LTO
afl-fuzz \
    -i seeds/ \
    -o out_lto_nocmplog/ \
    -x png.dict \
    -t 1000 \
    -- ./fuzz_png_lto &

LTO_PID=$!

# 有 CmpLog 的 LTO
# 需要先編譯 CmpLog 版本的 binary
AFL_LLVM_CMPLOG=1 afl-clang-lto \
    -o fuzz_png_cmplog fuzz_png.c \
    -I libpng-lto/install/include \
    -L libpng-lto/install/lib \
    -Wl,-rpath,$(pwd)/libpng-lto/install/lib \
    -lpng16 -lz -lm

afl-fuzz \
    -i seeds/ \
    -o out_lto_cmplog/ \
    -x png.dict \
    -c ./fuzz_png_cmplog \   # -c 指定 CmpLog binary
    -t 1000 \
    -- ./fuzz_png_lto &

CMPLOG_PID=$!

# 等 30 分鐘
sleep 1800
echo "=== 30 min: Without CmpLog ==="
cat out_lto_nocmplog/default/fuzzer_stats | grep "total_edges"
echo "=== 30 min: With CmpLog ==="
cat out_lto_cmplog/default/fuzzer_stats | grep "total_edges"

kill $LTO_PID $CMPLOG_PID 2>/dev/null
```

### 步驟 2.3：4-Core Parallel Fuzzing

啟動完整的 parallel session（這個跑 24 小時）：

```bash
# 建立 corpus 目錄
mkdir -p out_parallel/

# 主節點（-M）：負責 bitmap 同步和 queue 管理
afl-fuzz \
    -M main \
    -i seeds/ \
    -o out_parallel/ \
    -x png.dict \
    -c ./fuzz_png_cmplog \
    -t 1000 \
    -- ./fuzz_png_lto &

# 次節點 1：用不同的 power schedule（這裡用 fast）
AFL_CYCLE_STATES=1 \
afl-fuzz \
    -S worker01 \
    -i seeds/ \
    -o out_parallel/ \
    -x png.dict \
    -p fast \
    -t 1000 \
    -- ./fuzz_png_lto &

# 次節點 2：加入更激進的 mutation（enable_rpc 讓 havoc 更久）
afl-fuzz \
    -S worker02 \
    -i seeds/ \
    -o out_parallel/ \
    -x png.dict \
    -p explore \
    -t 1000 \
    -- ./fuzz_png_lto &

# 次節點 3：用 PCGUARD build（不同 instrumentation，補充 coverage）
afl-fuzz \
    -S worker03 \
    -i seeds/ \
    -o out_parallel/ \
    -x png.dict \
    -p exploit \
    -t 1000 \
    -- ./fuzz_png_pcguard &

echo "All 4 fuzzers started. PIDs: $(jobs -p)"
echo "Monitor with: afl-whatsup out_parallel/"
```

```bash
# 監控整體狀態
watch -n 30 'afl-whatsup out_parallel/'

# 或者看 main 節點的即時 TUI
# （在另一個終端）：afl-fuzz 的 TUI 會自動顯示，不需要額外指令
```

---

## 里程碑 3：Crash Triage

（在 parallel session 跑了至少幾小時後進行）

### 步驟 3.1：收集所有 Crash

```bash
# 把所有節點的 crash 集中到一個目錄
mkdir -p all_crashes/
for worker in out_parallel/*/crashes/id:*; do
    cp "$worker" "all_crashes/$(basename $(dirname $(dirname $worker)))_$(basename $worker)" 2>/dev/null
done

echo "Total crash files: $(ls all_crashes/ | wc -l)"
```

### 步驟 3.2：afl-cmin 去重

```bash
mkdir -p unique_crashes/

afl-cmin \
    -i all_crashes/ \
    -o unique_crashes/ \
    -T 5000 \           # 每個 crash 的 timeout（ms）
    -- ./fuzz_png_lto

echo "After afl-cmin: $(ls unique_crashes/ | wc -l) unique crashes"
```

### 步驟 3.3：Stack Hash Dedup

```bash
# 對每個 unique crash 取 ASAN stack trace
mkdir -p asan_reports/

for crash in unique_crashes/*; do
    base=$(basename "$crash")
    # 用 ASAN build 重現，取 stderr（ASAN report 在 stderr）
    ./fuzz_png_asan "$crash" 2> "asan_reports/${base}.txt" || true
done

# dedup_by_stack.py
python3 << 'EOF'
import os
import re
import hashlib
from collections import defaultdict

reports_dir = "asan_reports"
stacks = defaultdict(list)

for fname in os.listdir(reports_dir):
    filepath = os.path.join(reports_dir, fname)
    with open(filepath) as f:
        content = f.read()

    # 找 ASAN 的 stack trace（#0, #1, #2...）
    frames = re.findall(r'#\d+ 0x[0-9a-f]+ in (\S+)', content)
    if not frames:
        # 找 GDB-style stack trace
        frames = re.findall(r'#\d+\s+\S+ in (\S+)', content)

    if frames:
        # 用前 5 個 frame 的函式名 hash
        key = "|".join(frames[:5])
        h = hashlib.md5(key.encode()).hexdigest()[:8]
        stacks[h].append(fname)
    else:
        stacks["no_trace"].append(fname)

print(f"Total unique crashes (bitmap): {len(os.listdir(reports_dir))}")
print(f"Unique by stack hash: {len(stacks)}")
print()
for h, files in sorted(stacks.items(), key=lambda x: -len(x[1])):
    print(f"[{h}] {len(files)} files")
    print(f"  Representative: {files[0]}")
    # 印出 stack trace 的前幾行
    with open(os.path.join(reports_dir, files[0])) as f:
        for line in f:
            if re.match(r'\s*#[0-3] ', line):
                print(f"  {line.rstrip()}")
EOF
```

### 步驟 3.4：afl-tmin 縮小代表性 Crash

```bash
mkdir -p minimized_crashes/

# 對每個獨特 bug（按 stack hash 選一個代表）做 tmin
# 假設你已從上一步找出代表 crash files

for crash in unique_crashes/id:000001,* unique_crashes/id:000005,* unique_crashes/id:000012,*; do
    [ -f "$crash" ] || continue
    base=$(basename "$crash")
    echo "Minimizing $base..."
    afl-tmin \
        -e \             # 保留相同 exit code
        -i "$crash" \
        -o "minimized_crashes/${base}_min" \
        -t 5000 \
        -- ./fuzz_png_lto

    orig_size=$(stat -c%s "$crash")
    min_size=$(stat -c%s "minimized_crashes/${base}_min")
    reduction=$(( (orig_size - min_size) * 100 / orig_size ))
    echo "  $orig_size → $min_size bytes ($reduction% reduction)"
done
```

### 步驟 3.5：ASAN 重現確認

```bash
# 用 ASAN build 重現每個 minimized crash，取完整 report
for crash in minimized_crashes/*_min; do
    echo "=== $(basename $crash) ==="
    ASAN_OPTIONS="halt_on_error=1:print_stats=1" \
        ./fuzz_png_asan "$crash" 2>&1 | head -40
    echo ""
done
```

---

## 里程碑 4：Bug Report

以下是標準格式的 bug report 範本：

```
Title: libpng 1.6.40 — [bug type] in [function name]

Affected versions: libpng 1.6.x (tested on 1.6.40)

Severity: [根據 ASAN report 判斷：heap-buffer-overflow = High，
            null-deref = Medium，stack-overflow = High]

Description:
A [heap-buffer-overflow / use-after-free / ...] was found in libpng's
[function_name()] when processing a specially crafted PNG file.
The vulnerability can be triggered by passing a malformed PNG to any
application that uses libpng for image processing.

Steps to Reproduce:
1. Build libpng 1.6.40 with AddressSanitizer:
   CC=clang CFLAGS="-fsanitize=address -g" ./configure
   make

2. Compile the following minimal harness:
   [harness code]

3. Run with the attached PoC file:
   ./harness poc.png

Expected behavior:
libpng should return an error code and not crash.

Actual behavior:
Process crashes with SIGSEGV / SIGABRT.

ASAN Report:
==12345==ERROR: AddressSanitizer: heap-buffer-overflow on address 0x...
READ of size 4 at 0x... thread T0
    #0 0x... in png_read_row libpng-1.6.40/pngrutil.c:2845
    #1 0x... in fuzz_one fuzz_png.c:89
    ...

PoC: [附上 minimized crash file]

Additional notes:
- The issue was found using AFL++ 4.09c with LTO instrumentation
- Fuzzing time: [X hours] on a 4-core machine
- ASAN build confirmed the issue is a genuine memory safety bug
```

---

## 底層機制：整個 Campaign 的 Coverage 資料流

```
seeds/（5 個合法 PNG）
      │
      ▼
afl-fuzz（LTO + CmpLog + Dictionary）
      │
      ├── SHM bitmap：edge coverage 追蹤
      │   └── total_edges 增長曲線（plot_data）
      │
      ├── queue/：有趣的 input（per worker）
      │   └── 每個 input：coverage 有新 edge
      │
      └── crashes/：觸發 crash 的 input
            │
            ▼
      afl-cmin（bitmap 去重）
            │
            ▼
      unique_crashes/（代表性 crash）
            │
            ├── stack hash dedup → 獨特 bug 數量
            │
            ├── afl-tmin → minimized PoC
            │
            └── ASAN 重現 → bug report
```

---

## 常見卡點與解法

1. **`png_read_info` 在所有 input 上都回傳錯誤，coverage 不增長**
   原因：seed corpus 裡沒有合法的 PNG，fuzzer 從垃圾 bytes 開始，PNG header 驗證永遠失敗。
   解法：確認 `seeds/` 裡至少有一個合法的 PNG；用 `file seeds/*.png` 確認是真正的 PNG 而不是截斷的檔案。

2. **harness 編譯失敗：`nested function`**
   原因：`read_fn` 定義在 `fuzz_one` 裡，Clang 不支援 GCC nested function extension。
   解法：把 `read_fn` 移到頂層，改用 global struct 傳遞 reader state（見步驟 1.3 的替代方案）。

3. **`execs_per_sec` 只有 100-200，比預期低 100x**
   原因：persistent mode 沒有正確啟用，每次 iteration 都在 fork + exec。
   診斷：檢查 harness 是否有 `__AFL_FUZZ_INIT()` 和 `__AFL_LOOP(1000)`；用 `AFL_DEBUG=1 afl-fuzz ...` 確認 persistent mode 啟動訊息。

4. **`afl-cmin` 對 crash corpus 跑了很久（> 30 分鐘）**
   原因：crash 數量很多，每個都要執行一次 target。
   解法：先用 `ls out_parallel/*/crashes/ | wc -l` 確認總數；加 `-T 2000`（2 秒 timeout）防止 hang 的 crash 拖慢整個 cmin。

5. **ASAN 重現時沒有 crash（false positive）**
   原因：crash 是 non-ASAN build 裡的記憶體越界，但 ASAN 的 shadow memory 改變了記憶體 layout，越界恰好打到合法記憶體。
   解法：這是真 bug，只是 ASAN 的記憶體佈局讓它無法重現。用 GDB + `valgrind --tool=memcheck` 重現：`valgrind ./fuzz_png_lto ./minimized_crash.bin`。

6. **`afl-tmin` 說 input minimized 到 0 bytes**
   原因：target 在任何 input（包括空 input）上都 crash（可能是 target 本身的 null pointer dereference）。
   解法：確認這是真正的「空 input 也 crash」bug，而不是 harness 沒有正確處理 `size=0` 的情況。

7. **`map_density > 70%`，coverage 看起來不準**
   原因：libpng 的 edge 數量超過預設 bitmap size 的容量。
   解法：用 `AFL_MAP_SIZE=262144 afl-fuzz ...`，並重新用 `AFL_LLVM_MAP_SIZE=262144` 編譯 harness。

---

## 參考解答

<details>
<summary>展開：完整的 fuzz_png.c（不使用 nested function 版本）</summary>

```c
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include "png.h"

// Reader state（全域，每次 fuzz_one 呼叫前重置）
static struct {
    const uint8_t *data;
    size_t size;
    size_t pos;
} g_reader;

static void read_fn(png_structp p, png_bytep out, png_size_t len) {
    if (g_reader.pos + len > g_reader.size) {
        png_error(p, "EOF");
    }
    memcpy(out, g_reader.data + g_reader.pos, len);
    g_reader.pos += len;
}

static void err_fn(png_structp p, png_const_charp msg) {
    png_longjmp(p, 1);
}

static void warn_fn(png_structp p, png_const_charp msg) {
    (void)msg;
}

static int fuzz_one(const uint8_t *data, size_t size) {
    if (size < 8) return 0;

    g_reader.data = data;
    g_reader.size = size;
    g_reader.pos  = 0;

    png_structp png = png_create_read_struct(
        PNG_LIBPNG_VER_STRING, NULL, err_fn, warn_fn);
    if (!png) return 0;

    png_infop info = png_create_info_struct(png);
    if (!info) {
        png_destroy_read_struct(&png, NULL, NULL);
        return 0;
    }

    if (setjmp(png_jmpbuf(png))) {
        png_destroy_read_struct(&png, &info, NULL);
        return 0;
    }

    png_set_read_fn(png, NULL, read_fn);
    png_read_info(png, info);

    png_uint_32 w = png_get_image_width(png, info);
    png_uint_32 h = png_get_image_height(png, info);
    if (w > 0 && w <= 512 && h > 0 && h <= 512) {
        size_t rb = png_get_rowbytes(png, info);
        uint8_t *row = malloc(rb);
        if (row) {
            for (png_uint_32 y = 0; y < h; y++) {
                png_read_row(png, row, NULL);
            }
            free(row);
        }
    }

    png_destroy_read_struct(&png, &info, NULL);
    return 0;
}

__AFL_FUZZ_INIT();

int main(void) {
    while (__AFL_LOOP(1000)) {
        fuzz_one(__AFL_FUZZ_TESTCASE_BUF, __AFL_FUZZ_TESTCASE_LEN);
    }
    return 0;
}
```

</details>

<details>
<summary>展開：完整的 afl-fuzz 命令（4-core，帶所有選項的說明）</summary>

```bash
# 主節點（main）：
# -M main：主節點，負責 bitmap 同步
# -c：CmpLog binary
# -x：dictionary
# -t 1000：1 秒 timeout（libpng 解析應該很快）
# -m none：不限制 memory（ASAN 需要更多 memory）
afl-fuzz -M main \
    -i seeds/ -o out_parallel/ \
    -x png.dict \
    -c ./fuzz_png_cmplog \
    -t 1000 \
    -- ./fuzz_png_lto

# 次節點 1（explore power schedule）：
afl-fuzz -S worker01 \
    -i seeds/ -o out_parallel/ \
    -x png.dict \
    -p explore \
    -t 1000 \
    -- ./fuzz_png_lto

# 次節點 2（fast power schedule）：
afl-fuzz -S worker02 \
    -i seeds/ -o out_parallel/ \
    -x png.dict \
    -p fast \
    -t 1000 \
    -- ./fuzz_png_lto

# 次節點 3（exploit：focus 在已知 crash 附近）：
afl-fuzz -S worker03 \
    -i seeds/ -o out_parallel/ \
    -x png.dict \
    -p exploit \
    -t 1000 \
    -- ./fuzz_png_lto
```

</details>

<details>
<summary>展開：如果一個 crash 都沒有，診斷清單</summary>

```
1. 確認 ASAN build 有正確 link
   ./fuzz_png_asan 2>&1 | head  # 應該有 ASAN 的 startup message

2. 確認 crash 不是被 harness 吞掉
   # 暫時把 setjmp error handling 移除，看是否 crash
   # 如果移除後 crash，說明 error handling 把 bug 遮蔽了

3. 試試舊版 libpng（有已知 CVE 的版本）
   wget https://download.sourceforge.net/libpng/libpng-1.6.20.tar.gz
   # 重複上述 build 步驟

4. 確認 dictionary 正確被載入
   AFL_DEBUG=1 afl-fuzz ... 2>&1 | grep -i dict

5. 跑夠長時間
   libpng 1.6.40 已修復大多數已知 bug，24 小時可能只有 0-2 個 crash
   這是正常的——說明 harness 設計正確，target 比較穩健
```

</details>

---

## 預期成果

在 4 核機器上跑 24 小時，libpng 1.6.40 的預期結果：

- **Edge coverage**：約 3000–5000 total edges（persistent mode + CmpLog + dictionary）
- **execs_per_sec**：主節點 20000–50000（4 core 合計 80000–200000）
- **Crash 數量**：0–5 個（1.6.40 是相對穩定的版本）
  - 0 個 crash = harness 設計正確，target 穩健，這是好結果
  - 5+ 個 crash = 可能有真實 bug，進行 triage

如果想要更高的 crash 機率，改用 **libpng 1.6.20**（CVE-2015-8472 影響的版本範圍）。

---

## 延伸挑戰

### 挑戰 1：舊版 libpng，狩獵已知 CVE

```bash
# 下載有已知 CVE 的版本
wget https://download.sourceforge.net/libpng/libpng-1.6.20.tar.gz
# 重複 Build 步驟，但用 1.6.20 的 source
# 已知 CVE：CVE-2015-8472（out-of-bounds write in png_set_PLTE）
# 目標：確認 fuzzer 能復現這個 CVE
```

### 挑戰 2：加入更完整的 dictionary

```bash
# 除了 chunk type，加入常見的 PNG field 值
cat >> png.dict << 'EOF'
# Color type values
color_gray="\x00"
color_rgb="\x02"
color_indexed="\x03"
color_gray_alpha="\x04"
color_rgba="\x06"
# Compression method
compress_deflate="\x00"
# Filter method
filter_adaptive="\x00"
# Interlace method
interlace_none="\x00"
interlace_adam7="\x01"
# Common bit depths
bit_depth_1="\x01"
bit_depth_2="\x02"
bit_depth_4="\x04"
bit_depth_8="\x08"
bit_depth_16="\x10"
EOF
```

### 挑戰 3：對比 AFL++ 和 libFuzzer 的 coverage 曲線

```bash
# libFuzzer 版本（直接用 LLVMFuzzerTestOneInput）
cat > fuzz_png_libfuzzer.c << 'EOF'
#include <stdint.h>
#include <stddef.h>
#include "png.h"

// ... 和 fuzz_one() 相同的邏輯

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    return fuzz_one(data, size);
}
EOF

clang -fsanitize=fuzzer,address -g \
    fuzz_png_libfuzzer.c \
    -I libpng-lto/install/include \
    -L libpng-lto/install/lib \
    -lpng16 -lz -lm \
    -o fuzz_png_libfuzzer_bin

# 跑 1 小時（注意：libFuzzer 不是用 AFL++ 的 corpus 格式，但 raw bytes 相容）
timeout 3600 ./fuzz_png_libfuzzer_bin seeds/ \
    -max_total_time=3600 \
    -print_final_stats=1 \
    2>&1 | tee libfuzzer_run.log

# 從 log 裡提取 coverage 數字
grep "cov:" libfuzzer_run.log | tail -5
```

---

## 自我檢核（對全課的回顧）

- [ ] 能解釋為什麼選 LTO 而非 PCGUARD 作為主要 build：LTO 的插樁精度（Ch 6）、collision 率、和 persistent mode 的搭配效果
- [ ] 能說明 CmpLog 對 PNG chunk 比較有沒有幫助，以及為什麼：PNG 的 chunk type 是 4 bytes magic（IHDR、IDAT），CmpLog 的 input-to-state correspondence（Ch 15）能直接定位這些比較並注入正確值
- [ ] 能解釋 crash file 名稱裡 `sig:6` 和 `sig:11` 的差別：SIGABRT（通常是 ASAN）vs SIGSEGV（記憶體存取違規），以及各自對 exploitability 的影響（Ch 22）
- [ ] 能根據 `execs_per_sec` 評估 target 的 fuzzing 友好程度：persistent mode 應該有 10000+；如果只有 1000，說明 persistent mode 沒生效；如果只有 100，可能是 QEMU mode 或有 heavy initialization
- [ ] 如果 coverage 在 2 小時後趨平，能說出三個可能的原因：（a）hard-to-reach path 需要特定 magic bytes 組合，要 CmpLog + dictionary；（b）seed corpus 太差，起點限制了探索方向；（c）target 有 initialization barrier（比如 TLS negotiation），需要 desocket 或 grammar mutator（Ch 21, 23）

---

## 延伸閱讀

- **libpng 官方安全公告（http://www.libpng.org/pub/png/libpng.html）**
  核心貢獻：所有已知 CVE 的說明，包含影響版本和 patch。
  和本章關聯：了解歷史 CVE 的觸發條件，可以設計更針對性的 seed 和 dictionary。

- **"AFL++: Combining Incremental Steps of Fuzzing Research"（Fioraldi et al., WOOT 2020）**
  核心貢獻：AFL++ 的設計論文，整合了 CmpLog、LTO instrumentation、power schedule 等所有改進的設計理由。
  讀哪裡：Section 3（各元件的設計）；Table 2（各改進的效果量化）。
  和本章關聯：final project 用到的技術（LTO、CmpLog、power schedule）都在這篇有設計說明。

- **OSS-Fuzz libpng harness（https://github.com/google/oss-fuzz/tree/master/projects/libpng）**
  核心貢獻：Google 生產環境使用的 libpng fuzz harness，包含多個 target（reader、writer、simplified API）。
  讀哪裡：`libpng_read_fuzzer.cc`（最主要的 harness，和本章的 harness 可以直接比較）。
  和本章關聯：和你自己寫的 harness 對比，看有什麼差異；OSS-Fuzz 的版本涵蓋了更多 libpng API，作為延伸的參考。
