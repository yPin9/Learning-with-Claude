# Ch 22 — Crash Triage：從 1000 個 Crash Files 找出 5 個真正的 Bug

> **目標**：從大量 AFL++ crashes 中高效找出獨特 bug；掌握 `afl-tmin`、`afl-cmin`、GDB 自動化三者的應用場景。

> **環境**：AFL++ 4.09c, Ubuntu 22.04 LTS, x86_64

---

## 為什麼需要這個？

AFL++ 在 24 小時的 campaign 結束後，`out/default/crashes/` 裡可能有 200 個、甚至 1000 個 crash files。
把這 1000 個檔案一個一個手動重現是不可能的，也沒意義——因為其中 95% 可能都是**同一個 bug 的不同觸發路徑**。

你真正需要的資訊是：

1. 有幾個**獨特的 bug**（distinct root cause）？
2. 每個 bug 的**最小重現案例**（minimal reproducer）是什麼？
3. 哪個 bug 最可能是**可利用的（exploitable）**？

沒有 triage，即使找到了一個真正嚴重的 heap overflow，也可能淹沒在一堆重複的 NULL dereference 裡被忽略。
AFL++ 提供的 `afl-tmin` 和 `afl-cmin` 就是自動化回答這三個問題的工具。

---

## 先建立直覺

想像你在一家餐廳，今天接到 1000 個客訴電話。

你的任務不是一一回電——而是先**分類**：
- 這 1000 個電話裡，關於「湯太鹹」的有 800 個（同一個根本問題）。
- 「食物裡有蟲」有 150 個（另一個根本問題）。
- 「帳單算錯」50 個（第三個問題）。

處理順序：先確認只有 3 個根本問題，然後對每個問題找一個最清楚的電話記錄作為代表，再去修問題。

**afl-cmin** 做的是「找出代表每個獨特 crash 的最小 crash corpus」（分類 + 去重）。
**afl-tmin** 做的是「把一個代表性的 crash input 縮到最小」（縮小單個電話記錄裡的雜訊）。

---

## 橫向連結

- **Ch 5（Edge Coverage Bitmap）**：crash uniqueness 判斷用的是 bitmap，理解 bitmap 才能理解 bucketing 的局限性。
- **Ch 10（Corpus Lifecycle）**：`afl-cmin` 的邏輯和 corpus minimization 相同，只是對象是 crash files。
- **Ch 21（Difficult Targets）**：ASAN build 的 crash 比 non-sanitized build 提供更多 triage 資訊。

---

## AFL++ 的 Crash Bucketing 機制複習

AFL++ 對每個 crash，用 **coverage bitmap** 判斷是否「獨特」。

```
bitmap[edge_id]++ 但 saturated at 255
crash A 觸發的 edges：{12, 47, 203, 891}
crash B 觸發的 edges：{12, 47, 203, 892}  ← edge 892 是新的
→ AFL++ 認為這是不同的 crash
```

這個機制的**問題**：同一個 bug（例如同一個 heap overflow），但攻擊路徑稍微不同（多走了一個 if branch），就會被算成兩個 crash。

反過來，兩個**完全不同的 bug**，如果碰巧觸發了相同的 edge set（雖然少見），會被 AFL++ 當成同一個 crash。

所以 AFL++ 的 crash bucketing 只是第一道篩選，不能直接等同於「unique bug count」。

---

## Crash File 命名格式解析

```
id:000001,sig:11,src:000003,time:12345,execs:678901,op:havoc,rep:4
```

每個欄位的含義：

```
┌─────────────────────────────────────────────────────────────────┐
│ crash file 命名格式                                               │
│                                                                 │
│ id:000001          ← crash 的序號（按發現順序遞增）               │
│ sig:11             ← kill signal（11 = SIGSEGV, 6 = SIGABRT）   │
│ src:000003         ← 從 queue 的哪個 input 變異來的              │
│ time:12345         ← 發現時距開始的秒數                           │
│ execs:678901       ← 發現時總執行次數                             │
│ op:havoc           ← 觸發此 crash 的變異操作                     │
│ rep:4              ← 確認 crash 可重現的執行次數                  │
└─────────────────────────────────────────────────────────────────┘
```

`sig:6`（SIGABRT）通常是 ASAN 觸發的，幾乎必定是真 bug。
`sig:11`（SIGSEGV）可能是真 bug，也可能是 target 本身在某些邊界條件下的預期行為（例如解析器遇到格式錯誤時 segfault）。
`sig:4`（SIGILL）罕見，通常是 `-fsanitize=undefined` 的 UBSan 觸發。

---

## afl-tmin：最小化單個 Crash

### 演算法：逐步二分刪減

`afl-tmin` 對一個 crash input 做最小化，目標是找到**能觸發相同 crash 的最小 input**。

演算法：

```
初始 input：[A B C D E F G H I J]（10 bytes）

Round 1：嘗試刪掉後半
  試驗：[A B C D E]
  還 crash？是 → 保留縮小版本
  現在：[A B C D E]

Round 2：嘗試刪掉後半
  試驗：[A B C]
  還 crash？否 → 恢復
  現在：[A B C D E]

Round 3：嘗試刪掉前半
  試驗：[D E]
  還 crash？否 → 恢復
  現在：[A B C D E]

Round 4：嘗試逐 byte 刪除
  試驗：[B C D E] → crash？否 → 恢復
  試驗：[A C D E] → crash？否 → 恢復
  試驗：[A B D E] → crash？是 → 保留
  ...
```

最終：找到讓 crash 發生的最小充分條件。

### 實際用法

```bash
# 最小化單個 crash
afl-tmin -i out/default/crashes/id:000001,sig:11,src:000003,... \
         -o minimized_crash.bin \
         -- ./target @@

# 如果 target 從 stdin 讀
afl-tmin -i crash_input.bin -o minimized.bin -- ./target

# 重要：-e flag（保留 exit code）
# 預設行為：afl-tmin 只要 target crash（任何 signal）就認為成功
# 如果你只想保留「sig:11」的 crash，不想讓它退化成 sig:6，用 -e
afl-tmin -e -i crash.bin -o minimized.bin -- ./target @@
```

`-e` flag 讓 `afl-tmin` 要求 exit code 和原始 crash 一致，防止「trimming 過頭讓 crash 消失」或「變成不同的 crash」。

### 預期輸出

```
afl-tmin 的輸出：
[*] Stage #0: One-time block normalization...
[*] Stage #1: Block removal (coarse)...
[*] Stage #2: Block removal (fine)...
[*] Stage #3: Byte-level trimming...
[+] Final cleanup step...
[+] Writing output to 'minimized_crash.bin'...
[+] Done! Input minimized from 4096 to 127 bytes (96.9% reduction).
```

96.9% 的 reduction 很常見——大多數 crash 只需要很少的 bytes 觸發，周圍都是無關的填充。

---

## afl-cmin：最小化 Crash Corpus（集合最小化）

`afl-cmin` 解決的問題比 `afl-tmin` 更上一層：面對整個 `crashes/` 目錄，找出**覆蓋所有獨特 crash coverage 的最小子集**。

```
輸入：crashes/ 目錄（1000 個 crash files）
輸出：unique_crashes/ 目錄（可能只有 30 個 crash files）
保證：這 30 個 crash files 覆蓋 1000 個 crash files 的所有獨特 edge
```

```bash
# crash corpus 最小化
afl-cmin -i out/default/crashes/ \
         -o unique_crashes/ \
         -- ./target @@

# 如果 target 從 stdin 讀
afl-cmin -i crashes/ -o unique_crashes/ -- ./target

# 注意：afl-cmin 要求 target 在 crash 時回傳非零 exit code
# 用 ASAN build 時通常沒問題（ASAN abort 會有 exit code 1）
```

`afl-cmin` 執行時間和 crash 數量成正比——1000 個 crash 每個都要執行一次 target，可能要幾十分鐘。在送 bug report 前、或是要手動分析前，先跑 `afl-cmin` 是值得的。

---

## Stack Hash-Based Dedup：更精準的去重

bitmap-based dedup（AFL++ 內建）和 stack hash-based dedup 用不同標準判斷「獨特」：

- **Bitmap-based**：用觸發的 edge set 判斷 → 同一個 bug 但不同路徑 = 兩個 crash
- **Stack hash-based**：用 crash 時的 call stack 判斷 → 同一個 bug = 一個 crash（無論路徑）

Stack hash 通常更接近「真正獨特的 bug」數量。

### 批次獲取 Stack Trace 並 Hash

```bash
#!/bin/bash
# collect_stacks.sh

CRASH_DIR="out/default/crashes"
TARGET="./target"
OUTPUT="stacks.txt"

for crash in "$CRASH_DIR"/id:*; do
    echo "=== $(basename $crash) ===" >> "$OUTPUT"
    # 用 GDB 跑 crash，取 backtrace
    gdb -q -batch \
        -ex "set pagination off" \
        -ex "run $crash" \
        -ex "bt 20" \
        -ex "quit" \
        "$TARGET" 2>&1 | grep -A 20 "Program received signal\|ASAN" >> "$OUTPUT"
    echo "" >> "$OUTPUT"
done
```

```python
#!/usr/bin/env python3
# dedup_by_stack_hash.py

import sys
import hashlib
import re
from collections import defaultdict

stacks = defaultdict(list)
current_crash = None
current_frames = []

with open("stacks.txt") as f:
    for line in f:
        if line.startswith("=== "):
            if current_crash and current_frames:
                # hash 前 5 個 frame（更深的通常是 libc 的 abort 路徑）
                frame_key = "\n".join(current_frames[:5])
                h = hashlib.md5(frame_key.encode()).hexdigest()[:8]
                stacks[h].append(current_crash)
            current_crash = line.strip()[4:-4]  # 去掉 "=== " 和 " ==="
            current_frames = []
        elif re.match(r"\s*#\d+", line):
            # 只保留 frame 的函式名和位址，忽略行號（行號可能因編譯而異）
            frame = re.sub(r" at .*:\d+", "", line.strip())
            current_frames.append(frame)

# 輸出統計
print(f"獨特 crash（by stack hash）：{len(stacks)}")
for h, crashes in sorted(stacks.items()):
    print(f"\n[{h}] {len(crashes)} 個 crash files")
    print(f"  代表：{crashes[0]}")
    # 輸出 stack trace 的前 3 frame
```

---

## 確認 Crash 是真 Bug 還是 False Positive

並非所有 crash 都是值得回報的 bug。

### ASAN Crash：幾乎都是真 bug

ASAN 的 crash 訊息非常明確：

```
==12345==ERROR: AddressSanitizer: heap-buffer-overflow on address 0x602000000050
READ of size 4 at 0x602000000050 thread T0
    #0 0x401234 in parse_chunk target.c:89
    #1 0x401567 in process_input target.c:145
    #2 0x401890 in main target.c:200

0x602000000050 is located 0 bytes to the right of 16-byte region
```

`heap-buffer-overflow`、`stack-buffer-overflow`、`use-after-free`、`double-free` 都是嚴重 bug。
`null-dereference`、`wild-pointer` 要個案判斷（有可能是 error handling path 的預期行為）。

### Non-Sanitized Crash：需要手動確認

非 ASAN 的 crash（`sig:11` SIGSEGV），用 GDB 重現：

```bash
# 用 GDB 手動確認 crash
gdb -q ./target
(gdb) run ./crashes/id:000001,...
Program received signal SIGSEGV, Segmentation fault.
0x00007ffff7a34201 in png_read_row (...)
(gdb) bt
#0  0x00007ffff7a34201 in png_read_row (...)
#1  0x000000000040123f in main (...)
(gdb) info registers
(gdb) x/20xb $rsp  # 看 stack 附近的記憶體
```

常見的 false positive 案例：
- Parser 遇到格式錯誤，走到 `abort()` 或 `exit(1)` —— 這是預期行為，不是 bug。
- Divide-by-zero：`if (size == 0) crash;` —— 要看是否在正常輸入下也會觸發。
- Stack overflow：遞迴太深 —— 可能是 fuzzer 構造了異常深的巢狀結構，在現實輸入中不會發生。

---

## 底層機制：Crash Triage 的完整流程

```
out/default/crashes/（1000 個 crash files）
         │
         ▼
   afl-cmin 去重
         │
         ▼
unique_crashes/（例如 30 個 crash files）
    由 bitmap coverage 判斷唯一性
         │
         ├──→ 每個 crash 跑 GDB / ASAN，收集 stack trace
         │
         ▼
   stack hash dedup
         │
         ▼
   獨特 bug（例如 5 個，各有代表 crash file）
         │
         ├──→ afl-tmin 縮小每個代表 crash
         │
         ▼
   minimized PoC（幾十到幾百 bytes）
         │
         ├──→ ASAN 重現，取完整 sanitizer report
         │
         ▼
   bug report（影響版本、重現步驟、PoC、crash output）
```

---

## 進一步用法：利用 exploitable 判斷嚴重度

Mozilla 的 `exploitable` GDB plugin 可以自動評估 crash 的 exploitability：

```bash
# 安裝
git clone https://github.com/jfoote/exploitable
# 在 GDB 裡載入
source /path/to/exploitable/exploitable/exploitable.py

# 在 crash 後執行
gdb ./target
(gdb) run ./minimized_crash.bin
Program received signal SIGSEGV.
(gdb) exploitable
Description: Segmentation fault on unknown address
Short description: SegFaultOnPc
Hash: 1234567890abcdef.fedcba0987654321
Exploitability Classification: PROBABLY_EXPLOITABLE
Explanation: ...
```

Exploitability 分類：
- `EXPLOITABLE`：幾乎確定可以控制 PC（control flow hijacking）
- `PROBABLY_EXPLOITABLE`：write primitive 或 controlled read，很可能可利用
- `PROBABLY_NOT_EXPLOITABLE`：null dereference 或 assert，難以利用
- `NOT_EXPLOITABLE`：預期的程式終止

這個分類不是 100% 準確，但可以幫助快速優先排序。

---

## 對比與取捨

| 工具 | 輸入 | 輸出 | 速度 | 唯一性標準 |
|------|------|------|------|-----------|
| `afl-cmin` | crash corpus | 最小子集（仍是檔案集合） | 慢（每個 crash 執行一次） | Bitmap edge coverage |
| `afl-tmin` | 單個 crash file | 最小化的單個 crash file | 中（二分法迭代） | 相同的 crash（signal/coverage） |
| Stack hash script | crash corpus | 按 call stack 分組的清單 | 很慢（GDB 每個 crash 一次） | Call stack hash |
| `exploitable` | 單個 crash（GDB 裡） | Exploitability 評估 | 快（一次執行） | 控制流分析 |

---

## 踩雷集錦

1. **`afl-tmin` 讓 crash 消失**：二分刪減有時候會刪掉讓 crash 發生的關鍵 bytes，產生一個「不 crash 的最小 input」。用 `-e` flag 強制保留和原始 crash 相同的 exit code；如果 crash 還是消失，試試加 `-k`（keep going even if trimming fails）。

2. **Bitmap-based uniqueness 和 stack hash 的差異被嚴重低估**：AFL++ 的 crash 去重後可能有 50 個 "unique" crashes，但 stack hash 去重後只有 3 個根本不同的 bug。如果你直接對 50 個 crash 個別做 triage，浪費的時間是 stack hash 方案的 15 倍。

3. **`afl-cmin` 在 crash 很多時跑到天荒地老**：1000 個 crash 每個執行一次，target 如果初始化要 0.5 秒，總時間就是 500 秒（8 分鐘）。加上 `afl-cmin -T` flag 設定逾時，避免個別 hang crash 讓整個 cmin 卡住。

4. **用 non-ASAN build 做 triage，誤判 false positive**：non-sanitized build 的 crash 有時候看起來像「正常錯誤處理」。一定要用 ASAN build 重現——ASAN 會給出精確的 bug 位置，讓你判斷是否是真 bug。

5. **`afl-tmin` 的 crash path 和原始 crash 不同**：tmin 後的 minimized input 觸發相同的 crash（signal + coverage bitmap 一致），但 call stack 可能稍有不同。送 bug report 前，用 ASAN 重現 minimized input，確認 stack trace 和預期的 bug location 吻合。

---

## 進階：再往深一層

### Casr：自動化 crash 分析報告

CASR（Crash Analysis and Severity Reporting）是 ISP RAS 開發的工具，整合了多種 sanitizer 的 crash 分析，自動產生結構化的 crash report：

```bash
pip3 install casr
casr-san -o ./report.casrep -- ./target_asan ./minimized_crash.bin
casr-cli ./report.casrep  # 查看 report
```

CASR 能自動識別 crash 的嚴重度，比 `exploitable` 支援更多 sanitizer（ASAN、UBSAN、MSAN、LSAN）。

### Differential Triage：比較修復前後

當你發現一個 crash 並且開發者提交了修復，要確認修復有效：

```bash
# 在修復前的版本上跑
git checkout before-fix
make && afl-tmin -i crash.bin -o crash_min.bin -- ./target @@
./target_asan crash_min.bin 2>&1  # 應該 crash

# 在修復後的版本上跑
git checkout after-fix
make && ./target_asan crash_min.bin 2>&1  # 應該不 crash，或出現不同的錯誤
```

---

## 動手練習

### 練習 1：用 afl-tmin 最小化 crash

```c
// triage_target.c
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

void parse(unsigned char *buf, size_t size) {
    if (size < 4) return;
    if (buf[0] != 0xDE) return;
    if (buf[1] != 0xAD) return;
    if (buf[2] != 0xBE) return;
    if (buf[3] != 0xEF) return;
    // 讀 header 指定的長度，沒有邊界檢查
    uint16_t len = (buf[4] << 8) | buf[5];
    char *heap = malloc(16);
    memcpy(heap, buf + 6, len);  // heap-buffer-overflow
    free(heap);
}

int main(int argc, char **argv) {
    if (argc < 2) return 1;
    FILE *f = fopen(argv[1], "rb");
    if (!f) return 1;
    unsigned char buf[4096];
    size_t n = fread(buf, 1, sizeof(buf), f);
    fclose(f);
    parse(buf, n);
    return 0;
}
```

任務：
```bash
# 編譯（帶 ASAN）
afl-clang-fast -fsanitize=address -o triage_target triage_target.c

# 建立一個觸發 crash 的 input（100 bytes 的「雜訊」）
python3 -c "import os; data = b'\xde\xad\xbe\xef\x00\x40' + b'A'*64 + os.urandom(30); open('crash_input.bin','wb').write(data)"

# 確認 crash
./triage_target crash_input.bin

# 最小化
afl-tmin -i crash_input.bin -o minimized.bin -- ./triage_target @@

# 確認最小化後還是 crash，並查看大小差異
ls -la crash_input.bin minimized.bin
```

### 練習 2：批次 stack hash 去重

```bash
# 假設你有多個 crash files（模擬：建立幾個變體）
mkdir -p fake_crashes
python3 -c "
import struct
# 同一個 bug（不同觸發路徑）
for i, extra in enumerate([b'', b'X', b'XX', b'XXX']):
    data = b'\xde\xad\xbe\xef\x00\x40' + b'A'*64 + extra
    open(f'fake_crashes/crash_{i:03d}.bin', 'wb').write(data)
# 不同的 bug（不觸發 parse，直接 null deref）
open('fake_crashes/crash_004.bin', 'wb').write(b'\x00' * 4)
"

# 建立 stack hash script 並執行
# 觀察：前 4 個 crash 應該有相同的 stack hash
#        最後 1 個 crash（不 crash 因為 buf[0] != 0xDE）根本不 crash
```

---

## 本章重點整理

- **Crash triage 三步驟**：`afl-cmin` 去重 crash corpus → stack hash 找真正獨特的 bug → `afl-tmin` 縮小每個代表 crash；不做 triage 直接面對 1000 個 crash files 等於自我毀滅。
- **`afl-tmin` 的 `-e` flag 是關鍵**：沒有 `-e`，tmin 可能讓 crash 退化成不同的 crash 或完全消失；`-e` 強制保留相同的 exit code。
- **ASAN build 是 triage 的前提**：non-sanitized crash 難以判斷真偽；ASAN 的 heap-buffer-overflow / use-after-free 幾乎都是真 bug，不需要猜。

---

## 自我檢核

1. `afl-cmin` 輸出了 30 個 crash files，stack hash script 只找到 3 個獨特 stack。哪個數字更接近「真正的獨特 bug 數量」？為什麼？
2. `afl-tmin` 跑完後，minimized input 不再 crash。最可能的原因是什麼？怎麼修？
3. 一個 crash file 的名稱是 `id:000023,sig:6,...`。這個 crash 最可能是什麼原因？需要用 GDB 確認嗎？
4. 你想對 2000 個 crash files 做 stack hash dedup，但 target 啟動需要 2 秒。估算總時間，並說明如何加速。
5. `exploitable` 回報 `PROBABLY_NOT_EXPLOITABLE`（null dereference），但 crash 發生在 `parse_header()` 裡。你還需要繼續調查嗎？說明理由。

---

## 延伸閱讀

- **AFL++ `docs/triaging_crashes.md`**
  核心貢獻：AFL++ 官方的 crash triage 指引，說明 `afl-tmin`/`afl-cmin` 的設計意圖和具體用法。
  讀哪裡：整份文件，約 2 頁。
  和本章關聯：本章內容的官方來源；有些 flag 的行為文件比 `--help` 詳細。

- **"CollAFL: Path Sensitive Fuzzing"（Chen et al., S&P 2018）**
  核心貢獻：指出 AFL 的 hash collision 讓 bitmap-based uniqueness 低估了 path 數量；提出 path-sensitive coverage 修正。Section 5 有對 crash 去重方法的量化分析。
  讀哪裡：Section 2（hash collision 問題的說明）和 Section 5（實驗）。
  和本章關聯：為什麼 bitmap-based bucketing 不等於 unique bug count；stack hash 是補充而不是替代。

- **Exploitable GDB plugin（https://github.com/jfoote/exploitable）**
  核心貢獻：用控制流分析自動評估 crash 的 exploitability，把 SIGABRT/SIGSEGV 分類成 EXPLOITABLE/PROBABLY_EXPLOITABLE/NOT_EXPLOITABLE。
  讀哪裡：README 裡的 classification logic；源碼 `exploitable.py` 的 `classify_signal()` 函式。
  和本章關聯：triage 的最後一步——判斷哪個 crash 優先處理。

---

→ 下一章：[Ch 23 — Measuring Effectiveness](23-measuring-effectiveness.md)
