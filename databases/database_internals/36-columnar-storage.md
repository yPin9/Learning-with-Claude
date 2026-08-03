# Ch 36 — 欄式儲存與向量化執行

> **目標**：理解 row-store 和 column-store 在分析查詢上的根本效能差異、掌握 RLE/dictionary/bit-packing/delta 四種編碼、實作 column chunk 與 RLE 編碼，並理解向量化執行如何配合欄式資料實現 SIMD 加速。

## 為什麼分析查詢需要欄式儲存

### 問題的本質

考慮這個查詢：

```sql
SELECT region, SUM(amount)
FROM orders          -- 一億列，每列 200 bytes，20 個欄位
GROUP BY region;
```

這個查詢只碰 `region` 和 `amount` 兩個欄位。

**Row store（行式儲存，OLTP 設計）**：

```
┌──────────────────────────────────────────────────────┐
│ Row 1: [id=1][customer=42][date=...][amount=999][region=Asia][status=...][...18 more fields] │
│ Row 2: [id=2][customer=7 ][date=...][amount=150][region=EU  ][status=...][...18 more fields] │
│ ...                                                  │
└──────────────────────────────────────────────────────┘

讀取量：一億列 × 200 bytes = 20 GB
實際需要的資料：一億列 × (4+8) bytes = 1.2 GB（只有 region 和 amount）
浪費比例：(20-1.2)/20 = 94% 的 I/O 是無效的
```

**Column store（欄式儲存，OLAP 設計）**：

```
region column:  [Asia][EU][Asia][US][...] → 只有 4 bytes × 一億 = 400 MB（且可壓縮）
amount column:  [999][150][888][...     ] → 8 bytes × 一億 = 800 MB
其他 18 個欄位完全不讀
```

欄式儲存讓分析查詢只讀需要的欄位，I/O 減少 90% 以上，這是最根本的優勢。

### 三個優勢的機制

```
1. I/O 效率
   ─────────
   只讀 SELECT 用到的欄 → 磁碟 I/O 減少 80-95%
   欄式資料相似值相鄰 → 壓縮率高 → 有效 I/O 進一步縮小

2. Cache 效率
   ──────────
   Row store：讀一個欄位值，整個 200-byte row 進 cache，
              下一個 row 又要跨 200 bytes 跳
   Column store：欄位值緊密排列，一個 cache line (64 bytes)
                包含 8 個 f64 值，連續掃描無浪費

3. SIMD 向量化
   ────────────
   欄值連續排列 → CPU SIMD 可以一次操作 8-16 個值
   Row store 的值散在 row 中 → gather/scatter 開銷大
```

## Row vs Column 對比

```
Row Store 記憶體佈局（stride access）：
  Offset 0:   [id1|cust1|date1|amt1|rgn1|...]  ← 一整列
  Offset 200: [id2|cust2|date2|amt2|rgn2|...]
  Offset 400: [id3|cust3|date3|amt3|rgn3|...]

  讀 amount：跨步 200 bytes，stride access，prefetch 難
  CPU cache：每次取 cache line 只有 1/25 是有用的

Column Store 記憶體佈局（sequential access）：
  amounts:  [amt1|amt2|amt3|amt4|amt5|amt6|amt7|amt8|...]
  regions:  [rgn1|rgn2|rgn3|rgn4|rgn5|rgn6|rgn7|rgn8|...]

  讀 amount：連續，預取器有效，cache 全部有用
  SIMD：一次 _mm256_loadu_pd 讀 4 個 f64
```

### OLTP vs OLAP 的根本差異

| 維度          | OLTP（row store）          | OLAP（column store）        |
|--------------|----------------------------|------------------------------|
| 查詢模式      | 點查詢、小範圍更新           | 全表掃描、聚合                |
| 讀取欄數      | 通常讀整列（10-20 個欄）     | 通常讀 2-5 個欄              |
| 寫入模式      | 高頻單列 INSERT/UPDATE       | 批次載入（bulk load）         |
| 壓縮效益      | 低（row 內值多樣）           | 高（同欄值相似）              |
| 典型代表      | PostgreSQL、MySQL、InnoDB   | ClickHouse、DuckDB、Redshift |

混合方案：TiDB/CockroachDB 有 row + column 雙儲存引擎（HTAP），OLTP 用 row，分析查詢自動路由到 column 副本。

## 四種核心 Encoding

### 1. Run-Length Encoding（RLE）

連續相同值用「值 + 重複次數」表示：

```
原始資料（status 欄）：
  shipped, shipped, shipped, shipped, pending, pending, shipped

RLE 編碼：
  (shipped, 4), (pending, 2), (shipped, 1)

壓縮比：7 個值 → 3 對，節省 57%
最佳場景：排序後的低基數欄位
```

對已排序的欄位（如分組後的資料），RLE 效果驚人。Apache Parquet 對排好序的列式資料廣泛使用 RLE。

### 2. Dictionary Encoding

把每個 distinct 值替換成整數 id（dictionary code），實際存 id 而非字串：

```
原始（region 欄，字串）：
  "Asia", "EU", "Asia", "US", "Asia", "EU"

Dictionary：
  0 → "Asia"
  1 → "EU"
  2 → "US"

Encoded data（只存整數）：
  0, 1, 0, 2, 0, 1

儲存節省：字串 4-10 bytes → 整數 1-2 bytes
比較加速：字串比較 → 整數比較
SIMD 友善：整數欄可向量化計算
```

ClickHouse、Parquet、Arrow 對字串欄位幾乎都用 dictionary encoding。查詢時「predicate pushdown 到 dictionary」—— `WHERE region = 'Asia'` 先查 dictionary 得到 id=0，然後只比較整數 0，無需解壓字串。

### 3. Bit-Packing

如果欄位的最大值只需要 k bits（k < 8, 16, 32），就只用 k bits 存，省去高位的 0：

```
status_code 欄：值只有 0-7（3 bits 夠用）

原始（int32，4 bytes each）：3 1 4 1 5 9 2 6
Bit-packed（3 bits each，8 個值 = 24 bits = 3 bytes）：
  011 001 100 001 101 → ...（前 5 個值 = 15 bits）

壓縮比：4 bytes → 0.375 bytes（8.9x）
解壓：位元移位操作，SIMD 可批量解壓
```

PFOR（Patched Frame of Reference）是 bit-packing 的進階版：一個 block 內大多數值用少量 bits 編碼，少數 outlier 用 patch 補全，同時處理高壓縮比和 outlier。

### 4. Delta Encoding

存相鄰值的差值而非絕對值，適合單調遞增欄位（如 timestamp、primary key）：

```
timestamp 欄（unix ms）：
  1700000000000, 1700000001000, 1700000002000, 1700000003500

Delta（差值）：
  0, 1000, 1000, 1500

再配合 RLE：(0,1) (1000,2) (1500,1)
進一步配合 bit-packing：差值都在 16 bits 以內

現實中 Parquet 的 DELTA_BINARY_PACKED 就是 delta + bit-packing 的組合
```

## Rust 實作：Column Chunk + RLE 編碼

```rust
// src/columnar/column_chunk.rs

/// 欄式資料的基本單位：一個 column chunk
/// 儲存一個欄位的 N 個值，支援 RLE 壓縮
#[derive(Debug, Clone)]
pub struct ColumnChunk<T: Clone + PartialEq> {
    /// RLE 壓縮格式：(值, 重複次數)
    runs: Vec<(T, u32)>,
    /// 原始列數（解壓後的長度）
    n_rows: usize,
}

impl<T: Clone + PartialEq + std::fmt::Debug> ColumnChunk<T> {
    /// 從未壓縮的 slice 建立 RLE column chunk
    pub fn from_slice(data: &[T]) -> Self {
        let mut runs = Vec::new();
        let mut iter = data.iter();

        if let Some(first) = iter.next() {
            let mut current = first.clone();
            let mut count = 1u32;

            for val in iter {
                if val == &current {
                    count += 1;
                } else {
                    runs.push((current.clone(), count));
                    current = val.clone();
                    count = 1;
                }
            }
            runs.push((current, count));
        }

        Self {
            runs,
            n_rows: data.len(),
        }
    }

    pub fn n_rows(&self) -> usize {
        self.n_rows
    }

    /// 解壓成 Vec<T>
    pub fn decompress(&self) -> Vec<T> {
        let mut result = Vec::with_capacity(self.n_rows);
        for (val, count) in &self.runs {
            for _ in 0..*count {
                result.push(val.clone());
            }
        }
        result
    }

    /// 讀取第 i 列的值（O(runs) 時間，隨機存取代價高）
    pub fn get(&self, idx: usize) -> Option<&T> {
        if idx >= self.n_rows {
            return None;
        }
        let mut pos = 0usize;
        for (val, count) in &self.runs {
            pos += *count as usize;
            if pos > idx {
                return Some(val);
            }
        }
        None
    }

    /// 壓縮比：run 數 / 原始列數
    pub fn compression_ratio(&self) -> f64 {
        self.runs.len() as f64 / self.n_rows as f64
    }

    /// 迭代所有 (值, 重複次數) 對（不解壓，直接操作 RLE 格式）
    pub fn iter_runs(&self) -> impl Iterator<Item = (&T, u32)> {
        self.runs.iter().map(|(v, c)| (v, *c))
    }
}

/// 對 RLE column chunk 做等值過濾，回傳 row indices（不解壓）
/// 這是欄式執行引擎的核心優勢：直接在壓縮資料上過濾
pub fn filter_eq<T: Clone + PartialEq + std::fmt::Debug>(
    chunk: &ColumnChunk<T>,
    target: &T,
) -> Vec<usize> {
    let mut result = Vec::new();
    let mut row_start = 0usize;

    for (val, count) in chunk.iter_runs() {
        let row_end = row_start + count as usize;
        if val == target {
            // 整個 run 都匹配，批量加入
            result.extend(row_start..row_end);
        }
        row_start = row_end;
    }

    result
}

/// Dictionary encoding：字串 → 整數 id
#[derive(Debug, Default)]
pub struct DictionaryEncoder {
    dict: std::collections::HashMap<String, u32>,
    reverse: Vec<String>,
}

impl DictionaryEncoder {
    pub fn encode(&mut self, s: &str) -> u32 {
        if let Some(&id) = self.dict.get(s) {
            return id;
        }
        let id = self.reverse.len() as u32;
        self.dict.insert(s.to_string(), id);
        self.reverse.push(s.to_string());
        id
    }

    pub fn decode(&self, id: u32) -> Option<&str> {
        self.reverse.get(id as usize).map(|s| s.as_str())
    }

    pub fn dict_size(&self) -> usize {
        self.reverse.len()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_rle_encoding() {
        let data = vec!["shipped", "shipped", "shipped", "pending", "pending", "shipped"];
        let chunk = ColumnChunk::from_slice(&data);

        assert_eq!(chunk.n_rows(), 6);
        assert_eq!(chunk.compression_ratio(), 3.0 / 6.0); // 3 runs / 6 rows = 50%

        let decompressed = chunk.decompress();
        assert_eq!(decompressed, data);
    }

    #[test]
    fn test_rle_get() {
        let data = vec![10u32, 10, 10, 20, 20, 30];
        let chunk = ColumnChunk::from_slice(&data);

        assert_eq!(chunk.get(0), Some(&10u32));
        assert_eq!(chunk.get(3), Some(&20u32));
        assert_eq!(chunk.get(5), Some(&30u32));
        assert_eq!(chunk.get(6), None);
    }

    #[test]
    fn test_filter_eq_no_decompress() {
        let data = vec!["Asia", "EU", "Asia", "Asia", "US", "EU"];
        let chunk = ColumnChunk::from_slice(&data);

        // 注意：filter_eq 直接操作 RLE，不解壓
        // 但這裡 runs 不連續（Asia, EU, Asia Asia, US, EU）
        // 實際 runs：(Asia,1),(EU,1),(Asia,2),(US,1),(EU,1)
        let matched = filter_eq(&chunk, &"Asia");
        assert_eq!(matched, vec![0, 2, 3]);
    }

    #[test]
    fn test_filter_eq_with_long_run() {
        // 對已排序資料，RLE 效果最好，過濾也最快
        let mut data = vec!["Asia"; 10000];
        data.extend(vec!["EU"; 5000]);
        data.extend(vec!["US"; 2000]);

        let chunk = ColumnChunk::from_slice(&data);
        assert_eq!(chunk.compression_ratio(), 3.0 / 17000.0); // 3 runs

        let matched = filter_eq(&chunk, &"EU");
        assert_eq!(matched.len(), 5000);
        assert_eq!(matched[0], 10000);
        assert_eq!(matched[4999], 14999);
    }

    #[test]
    fn test_dictionary_encoder() {
        let mut enc = DictionaryEncoder::default();
        assert_eq!(enc.encode("Asia"), 0);
        assert_eq!(enc.encode("EU"), 1);
        assert_eq!(enc.encode("Asia"), 0); // 重複值同一 id
        assert_eq!(enc.encode("US"), 2);

        assert_eq!(enc.decode(1), Some("EU"));
        assert_eq!(enc.dict_size(), 3);
    }
}
```

## 向量化執行：配合欄式資料的 SIMD

Ch 29 介紹了向量化執行模型（vectorized execution）。欄式儲存和向量化是天作之合：

```
Volcano 模型（row-at-a-time）：
  next() → 取一列 → decode 一個值 → 做一次比較
  overhead：每列一次函數呼叫，分支預測難，SIMD 無從下手

Vectorized 模型（batch-at-a-time，配合 column store）：
  next_batch(1024) → 取 1024 個欄值（已連續排列）
                   → SIMD 一次比較 4/8 個 f64
                   → 輸出 1024 個 bool（or 位元 bitmap）
```

### 向量化 SUM 的概念

```rust
// 未編譯驗證（需 std::simd 或 packed_simd crate，nightly Rust）
// 概念示意：對 f64 slice 做向量化 SUM

#[cfg(target_arch = "x86_64")]
fn sum_f64_simd(data: &[f64]) -> f64 {
    use std::arch::x86_64::*;
    
    // 概念：用 AVX2 一次處理 4 個 f64
    // _mm256_loadu_pd：從記憶體連續讀 4 個 f64 進 256-bit 暫存器
    // _mm256_add_pd：4 路並行加法
    // 欄式儲存保證 data 是連續的 f64 slice，正好適合這個模式
    
    let mut sum = 0.0f64;
    for &v in data {
        sum += v;
    }
    sum // 這裡是純量版本；真實 SIMD 版本見 perf_bench 課程
}
```

我們在 perf_bench 課程中有完整的 SIMD 向量化實作。欄式儲存和向量化的協同效果：從磁碟讀進來的 column chunk 直接就是可以 SIMD 的記憶體佈局，不需要任何 gather 操作。

## Parquet 與 Arrow 概觀

### Apache Parquet

欄式儲存格式，廣泛用於 Hadoop/Spark 生態系：

```
Parquet 檔案結構：

┌─────────────────────────────────────┐
│ Magic (4 bytes: "PAR1")             │
├─────────────────────────────────────┤
│ Row Group 0（預設 128 MB）           │
│   Column Chunk: col_a              │
│     Page 0: [data pages, RLE/dict] │
│     Page 1: ...                    │
│   Column Chunk: col_b              │
│     ...                            │
├─────────────────────────────────────┤
│ Row Group 1                        │
│   ...                              │
├─────────────────────────────────────┤
│ Footer（column statistics, schema）  │
│   min/max per column per row group │
│   → predicate pushdown 到 row group│
└─────────────────────────────────────┘
```

**Predicate Pushdown 到 Row Group**：Parquet footer 記錄每個 row group 每欄的 min/max 統計。讀之前先比對 predicate，整個 row group 都不在範圍內就跳過，完全不讀。

### Apache Arrow

**記憶體中的欄式格式**（不是檔案格式，是 IPC 格式）：

```
Arrow RecordBatch（記憶體中的欄式表格）：

Buffer 0 (validity bitmap): [1111 1110]  ← null 標記
Buffer 1 (offsets, 字串用): [0, 4, 6, 10, ...]
Buffer 2 (data):            [A s i a E U A s i a ...]
                              ^ 連續記憶體，可直接傳給 SIMD

優勢：
  - 零拷貝（zero-copy）傳遞：Rust 的 ArrowArray 可直接傳給 C/Python
  - 統一格式：DuckDB、Polars、Pandas 2.0 都能接 Arrow 記憶體，不需 serialize
```

DuckDB 的向量化引擎內部就是 Arrow 格式，Polars 的底層是 Arrow2（Rust 實作的 Arrow）。

## 欄式儲存的代價

欄式不是銀彈，OLTP 場景下欄式儲存有明顯劣勢：

```
單列插入（OLTP 典型操作）：

Row store：一次 I/O，整列寫入一個 page
Column store：N 個欄位 → N 個 column chunk 都要更新
              如果 N=20，寫入放大 20x

點查詢（lookup by pk）：
Row store：index → 直接讀整列
Column store：需要對所有要求的欄位各讀一次（row reconstruction）
              如果查詢 SELECT *，幾乎沒有欄式優勢
```

這是 HTAP（Hybrid Transactional/Analytical Processing）系統的核心設計問題：同一份資料維護 row + column 兩份副本，寫入到 row store，分析查詢路由到 column store，背景持續同步。

## 對比表格

| 維度             | Row Store            | Column Store           |
|-----------------|----------------------|------------------------|
| 讀取欄數少的查詢 | 差（讀整列）          | 優（只讀需要的欄）       |
| 點查詢           | 優                   | 差（row reconstruction）|
| 壓縮率           | 差（值多樣）          | 優（同欄值相似）         |
| SIMD 向量化      | 難（stride access）   | 易（連續 buffer）        |
| 單列插入代價     | 低                    | 高（N 欄各更新）         |
| 批量載入         | 可                    | 優（columnar bulk load）|
| 適合場景         | OLTP                  | OLAP                   |

## 踩雷

1. **RLE 對未排序資料幾乎無效**：`region` 欄如果沒有按 region 排序，每個值都是 1 個 run，壓縮比 = 1，浪費了 RLE 的意義。欄式 DB 通常在 bulk load 時對某些欄位排序（ClickHouse 的 ORDER BY 子句就是這個用途）。

2. **Dictionary encoding 在高基數欄位反而更大**：UUID primary key 這種幾乎不重複的欄位建 dictionary，dictionary 大小等於資料大小，額外的 id 欄還要多一份，得不償失。

3. **Parquet predicate pushdown 要對 column statistics 有感**：`WHERE ts > '2024-06-01'` 能跳過大量 row group；`WHERE lower(name) = 'alice'` 跳不了，因為 Parquet footer 存的是原始值的 min/max，不是轉換後的。

4. **Row Reconstruction 代價**：`SELECT *` 配合欄式儲存會比 row store 慢，因為要從 N 個 column buffer 拼回一列。分析查詢應該精確指定欄名，不要用 `SELECT *`。

5. **向量化 batch size 的選擇**：太小（<128）SIMD overhead 大；太大（>4096）可能超出 L1 cache，每批都要從 L2/L3 拉。DuckDB 用 2048，ClickHouse 用 65536——視 cache 大小而定，需要 benchmark。

## 進階延伸

**Late Materialization**：謂詞過濾後再拼 row，而非先拼再過濾。只對最終輸出的少量列做 row reconstruction，對欄式儲存的查詢效能有顯著幫助。

**Vectorized Hashing for Group-By**：ClickHouse 的 hash aggregation 也是向量化的——一個 batch 的 group key 批量雜湊，同時更新 aggregate 狀態，配合欄式資料 cache 效率極高。

## 本章重點整理

- Column store 只讀需要的欄，I/O 節省 80-95%，同欄值相似壓縮率高，SIMD 友善。
- RLE 對排序後的低基數欄位效果最佳；dictionary encoding 對字串欄位幾乎必用；bit-packing 對小範圍整數；delta encoding 對單調遞增序列。
- 向量化執行和欄式儲存是天作之合：連續 column buffer 直接餵給 SIMD，不需 gather。
- Parquet 是持久化欄式格式（帶 row group statistics），Arrow 是記憶體欄式格式（帶零拷貝 IPC）。
- 欄式儲存在 OLTP 場景（單列插入、點查詢）表現差，HTAP 系統用雙副本解決。

## 自我檢核

- [ ] 能解釋為什麼 `SELECT SUM(amount) FROM orders WHERE region = 'Asia'` 在 column store 上比 row store 快 10 倍以上？
- [ ] RLE、dictionary、bit-packing、delta 各自最適合哪種資料特性？
- [ ] 為什麼 Parquet 的 footer statistics 能跳過整個 row group？
- [ ] Arrow 和 Parquet 的區別是什麼？什麼時候用哪個？
- [ ] 為什麼 `SELECT *` 在欄式 DB 上可能比 row store 慢？

## 延伸閱讀

1. **CMU 15-721 Advanced Database Systems, Lecture "Column Stores" (2023)**  
   Andy Pavlo 的進階課，深入到 late materialization、compression-aware query processing、vectorized hash table，是本章的深度延伸。

2. **《Database Internals》第 14–15 章（Distributed Systems, Analytics）**  
   Alex Petrov 對欄式儲存引擎設計與壓縮機制的討論，補充了 Parquet/ORC 格式細節。

3. **MonetDB/X100: Hyper-Pipelining Query Execution** — Boncz, Zukowski, Nes (CIDR 2005)  
   向量化執行引擎的開山論文，X100 模型是 DuckDB/Vectorwise 的前身，說清楚了為什麼 Volcano 模型對現代 CPU 是災難。

4. **Apache Parquet 官方格式規格**  
   <https://parquet.apache.org/docs/file-format/>  
   直接讀 byte-level 格式規格，理解 row group / column chunk / page 的三層結構，以及各種 encoding 的具體 binary 格式。

5. **Dremel: Interactive Analysis of Web-Scale Datasets** — Melnik et al. (VLDB 2010)  
   Google 欄式儲存論文，Parquet 的設計靈感來源，Definition Level 和 Repetition Level 處理 nested 資料的方法在這裡。

---

銜接：欄式儲存讓我們只讀必要的欄，但資料最終要過記憶體。Buffer pool 的設計決策——特別是要不要用 mmap——在效能上有深遠的影響，而這個選擇涉及一場圈內有名的論戰。

→ [下一章：Ch 37 記憶體與效能：mmap 的爭議](./37-memory-performance.md)
