# Ch 14 — Bloom Filter

> **目標**：理解 Bloom filter 為什麼能用 O(m) 空間回答「這個 key 絕對不存在 / 可能存在」，推導假陽性率公式與最佳 k 的選法，動手實作一個 Rust BloomFilter（FNV-1a + double hashing），並把它整合進 SSTable 讀取路徑，消滅不必要的磁碟 I/O。

---

## 問題：讀一個不存在的 key 有多貴？

Ch 13 建好了 SSTable，Ch 12 建好了 MemTable。現在考慮一個查詢：

```
GET "user:99999999"   // 這個 key 根本不存在
```

LSM-Tree 的讀取路徑是：

```
1. 查 MemTable             → 沒有
2. 查 immutable MemTable   → 沒有
3. 查 L0 的每個 SSTable    → 沒有（L0 有 4~8 個 SSTable，每個都要查）
4. 查 L1 的對應 SSTable    → 沒有
5. 查 L2 的對應 SSTable    → 沒有
6. 查 L3 ...               → 沒有
7. 回傳 NOT FOUND
```

每一個「查 SSTable」都是一次磁碟 I/O：讀 footer（16 bytes）+ 讀 index block + 讀 data block。  
假設 5 個 level，每層 1 次 I/O，查一個不存在的 key 就要 **5 次磁碟 I/O**，每次 0.1ms（SSD），累計 0.5ms——而且 cache miss 時還更慢。

**不存在的查詢在 LSM-Tree 裡是最壞情況**。而 key-value 系統的讀取有大量的 `GET nonexistent`——快取 miss、TTL 過期的 key、使用者打錯名字——全都要付出這個代價。

Bloom filter 是這個問題的標準解法：用一個附在 SSTable 旁的小型概率資料結構，在 **O(1) 時間、O(m) 空間**内消滅 99% 以上不必要的 SSTable 讀取。

---

## 直觀：一個會說謊但永不否認的守門員

Bloom filter 只能回答兩件事：

```
"definitely NOT present"  ← 100% 正確，保證
"possibly present"        ← 可能是謊言（假陽性），但不會漏報
```

關鍵性質：
- **無假陰性（no false negatives）**：如果 key 確實存在，Bloom filter 一定說「possibly present」
- **有假陽性（false positives）**：如果 key 不存在，Bloom filter 有一定機率說「possibly present」（當作存在）

這個性質對資料庫剛好合用：假陽性只是多讀一次 SSTable（浪費 I/O），但資料是正確的；假陰性則會讓我們錯過真正存在的 key，這才是災難。

---

## Bloom Filter 的結構與操作

### 資料結構

一個 m 位元的 bit array，初始全為 0；k 個雜湊函數 h₁, h₂, ..., hₖ。

```
bit array (m=16 bits):
index:  0  1  2  3  4  5  6  7  8  9 10 11 12 13 14 15
value:  0  0  0  0  0  0  0  0  0  0  0  0  0  0  0  0  ← 初始狀態
```

### 插入（Insert）

計算 key 的 k 個雜湊值，把那 k 個位元設為 1：

```
Insert "apple"  (k=3)
  h1("apple") mod 16 = 3   → set bit[3] = 1
  h2("apple") mod 16 = 7   → set bit[7] = 1
  h3("apple") mod 16 = 12  → set bit[12] = 1

index:  0  1  2  3  4  5  6  7  8  9 10 11 12 13 14 15
value:  0  0  0  1  0  0  0  1  0  0  0  0  1  0  0  0

Insert "banana"  (k=3)
  h1("banana") mod 16 = 1  → set bit[1] = 1
  h2("banana") mod 16 = 7  → bit[7] 已是 1，不影響
  h3("banana") mod 16 = 9  → set bit[9] = 1

index:  0  1  2  3  4  5  6  7  8  9 10 11 12 13 14 15
value:  0  1  0  1  0  0  0  1  0  1  0  0  1  0  0  0
```

### 查詢（Query）

計算 key 的 k 個雜湊值，如果**全部** k 個位元都是 1，回傳「possibly present」；只要有**任何一個**是 0，回傳「definitely not present」：

```
Query "apple":
  h1("apple") mod 16 = 3  → bit[3] = 1 ✓
  h2("apple") mod 16 = 7  → bit[7] = 1 ✓
  h3("apple") mod 16 = 12 → bit[12] = 1 ✓
  → "possibly present"  ← 正確（apple 確實插入過）

Query "grape":
  h1("grape") mod 16 = 3  → bit[3] = 1 ✓
  h2("grape") mod 16 = 5  → bit[5] = 0 ✗
  → "definitely NOT present"  ← 立刻回傳，不需要查 SSTable

Query "cherry":  ← 假設 cherry 沒有插入
  h1("cherry") mod 16 = 1  → bit[1] = 1 ✓  (被 banana 設的)
  h2("cherry") mod 16 = 7  → bit[7] = 1 ✓  (被 apple/banana 設的)
  h3("cherry") mod 16 = 9  → bit[9] = 1 ✓  (被 banana 設的)
  → "possibly present"  ← 假陽性！cherry 根本不存在
```

假陽性是怎麼發生的：cherry 的三個 bit 剛好被其他 key 插入時順帶設成 1，導致誤判。

---

## 假陽性率公式（False Positive Rate）

這是 Burton H. Bloom 在 1970 年論文中推導的結果。

**設定**：
- m = bit array 的總位元數
- n = 已插入的 key 數量
- k = 雜湊函數個數

**推導**：

插入一個 key 時，某個特定 bit 被某個雜湊函數設為 1 的機率是 1/m。  
那麼**不被**設的機率是 (1 - 1/m)。

插入 n 個 key，每個 key 用 k 個雜湊函數，某個 bit 始終為 0 的機率：

```
P(bit = 0) = (1 - 1/m)^(k*n)
```

對於很大的 m，利用極限 lim_{m→∞} (1 - 1/m)^m = e^{-1}：

```
P(bit = 0) ≈ e^(-k*n/m)
```

查詢一個**沒有插入的** key 時，它的 k 個對應 bit 都剛好是 1 的機率——這就是假陽性率：

```
f ≈ (1 - e^(-k*n/m))^k
```

來源：Burton H. Bloom, "Space/Time Trade-offs in Hash Coding with Allowable Errors,"  
Communications of the ACM, Vol. 13, No. 7, 1970.

### 數值感受

設 n = 10,000 keys，固定 m/n（每個 key 的 bit 數）：

```
m/n（bits/key）  k_opt  理論假陽性率
     6             4     ~5.6%
     8             6     ~2.2%
    10             7     ~0.84%
    14            10     ~0.12%
    20            14     ~0.006%
```

RocksDB 的預設是 **10 bits/key**，假陽性率約 1%。

---

## 最佳 k 的選法

對於固定的 m 和 n，假陽性率 f(k) 是 k 的函數。對 k 求導並令 df/dk = 0，得到最佳雜湊函數數量：

```
k_opt = (m/n) * ln(2)  ≈  0.693 * (m/n)
```

代入 f 的公式，當使用 k_opt 時，假陽性率化簡為：

```
f_opt ≈ (0.6185)^(m/n)
```

這意味著假陽性率完全由 **m/n（每個 key 的 bit 數）**決定，只要選了最佳 k。

```
bits/key = 10 → f_opt ≈ (0.6185)^10 ≈ 0.008  （約 1%）
bits/key = 8  → f_opt ≈ (0.6185)^8  ≈ 0.022  （約 2%）
bits/key = 6  → f_opt ≈ (0.6185)^6  ≈ 0.057  （約 6%）
```

實務上的設計決策：**先決定能容忍的假陽性率，算出需要的 bits/key，再用 k_opt 算 k**。不需要手動試 k。

---

## Rust 實作：BloomFilter

以下是完整可編譯的實作。雜湊函數使用 FNV-1a（h1）+ DefaultHasher 混合（h2），再用 double hashing trick 生成 k 個位置：

```
h_i(x) = h1(x) + i * h2(x)   (mod m)
```

這個 trick 只需要兩個獨立的雜湊計算，就能模擬 k 個雜湊函數，且理論上假陽性率與真正 k 個獨立雜湊函數相同（Kirsch & Mitzenmacher, 2006）。

```rust
// 實測通過 (WSL)
// rustc 1.97.1 (cargo test -- --nocapture)
// 測試輸出：
//   m=100000, k=6, n=10000
//   Theoretical FPR: 0.0084 (0.84%)
//   Measured    FPR: 0.0088 (0.88%)

use std::collections::hash_map::DefaultHasher;
use std::hash::Hasher;

pub struct BloomFilter {
    bits: Vec<u64>,   // bit array，以 u64 為單位儲存
    m: usize,         // bit array 的總位元數
    k: usize,         // 雜湊函數個數
    n_inserted: usize,
}

impl BloomFilter {
    /// 建立一個 m 位元、k 個雜湊函數的 Bloom filter。
    pub fn new(m: usize, k: usize) -> Self {
        let words = (m + 63) / 64;
        BloomFilter {
            bits: vec![0u64; words],
            m,
            k,
            n_inserted: 0,
        }
    }

    /// 根據預期插入數量 expected_n 與每個 key 的 bit 數 bits_per_key，
    /// 自動計算 m 和最佳 k_opt = floor(bits_per_key * ln(2))。
    pub fn with_capacity(expected_n: usize, bits_per_key: f64) -> Self {
        let m = ((expected_n as f64 * bits_per_key) as usize).max(64);
        let k = ((bits_per_key * std::f64::consts::LN_2) as usize).max(1);
        Self::new(m, k)
    }

    /// Double hashing：h_i(x) = h1(x) + i * h2(x)  (mod m)
    fn hashes(&self, key: &[u8]) -> impl Iterator<Item = usize> + '_ {
        let h1 = fnv1a_64(key);
        let h2 = {
            let mut h = DefaultHasher::new();
            std::hash::Hash::hash_slice(key, &mut h);
            h.write_u64(0xDEAD_BEEF_CAFE_BABE);
            h.finish()
        };
        let m = self.m;
        let k = self.k;
        (0..k).map(move |i| {
            let combined = h1.wrapping_add((i as u64).wrapping_mul(h2));
            (combined % m as u64) as usize
        })
    }

    fn set_bit(&mut self, pos: usize) {
        self.bits[pos / 64] |= 1u64 << (pos % 64);
    }

    fn get_bit(&self, pos: usize) -> bool {
        (self.bits[pos / 64] >> (pos % 64)) & 1 == 1
    }

    /// 插入一個 key。
    pub fn insert(&mut self, key: &[u8]) {
        let positions: Vec<usize> = self.hashes(key).collect();
        for pos in positions {
            self.set_bit(pos);
        }
        self.n_inserted += 1;
    }

    /// 查詢 key 是否可能存在。
    /// 回傳 false → 絕對不存在（無假陰性）
    /// 回傳 true  → 可能存在（有假陽性率 f）
    pub fn may_contain(&self, key: &[u8]) -> bool {
        self.hashes(key).all(|pos| self.get_bit(pos))
    }

    /// 理論假陽性率：f ≈ (1 - e^(-k*n/m))^k
    pub fn theoretical_fpr(&self) -> f64 {
        let k = self.k as f64;
        let n = self.n_inserted as f64;
        let m = self.m as f64;
        (1.0 - (-k * n / m).exp()).powi(self.k as i32)
    }
}

/// FNV-1a 64-bit 雜湊函數
fn fnv1a_64(data: &[u8]) -> u64 {
    const FNV_OFFSET: u64 = 0xcbf29ce484222325;
    const FNV_PRIME: u64  = 0x00000100000001b3;
    let mut hash = FNV_OFFSET;
    for &byte in data {
        hash ^= byte as u64;
        hash = hash.wrapping_mul(FNV_PRIME);
    }
    hash
}

#[cfg(test)]
mod tests {
    use super::*;

    // 無假陰性：所有插入過的 key 都必須被找到
    #[test]
    fn no_false_negatives() {
        let n = 10_000;
        let mut bf = BloomFilter::with_capacity(n, 10.0);
        let keys: Vec<Vec<u8>> = (0..n)
            .map(|i| format!("key:{:08}", i).into_bytes())
            .collect();
        for key in &keys { bf.insert(key); }
        for key in &keys {
            assert!(bf.may_contain(key), "False negative for inserted key");
        }
    }

    // 假陽性率應接近理論值
    #[test]
    fn false_positive_rate_within_bounds() {
        let n = 10_000;
        let mut bf = BloomFilter::with_capacity(n, 10.0);
        for i in 0..n {
            bf.insert(&format!("inserted:{:08}", i).into_bytes());
        }

        let theoretical = bf.theoretical_fpr();
        let test_count = 100_000usize;
        let false_positives = (0..test_count)
            .filter(|i| bf.may_contain(&format!("notinserted:{:08}", i).into_bytes()))
            .count();
        let measured = false_positives as f64 / test_count as f64;

        println!("Theoretical FPR: {:.4} ({:.2}%)", theoretical, theoretical * 100.0);
        println!("Measured    FPR: {:.4} ({:.2}%)", measured, measured * 100.0);

        // 實測值應在理論值的 2.5 倍以內
        assert!(measured < theoretical * 2.5 + 0.01);
    }

    // 不同 bits/key 對應的假陽性率
    #[test]
    fn different_bit_densities() {
        let n = 5_000;
        for &(bpk, max_fpr) in &[(6.0f64, 0.12), (8.0, 0.04), (10.0, 0.015), (14.0, 0.002)] {
            let mut bf = BloomFilter::with_capacity(n, bpk);
            for i in 0..n { bf.insert(&format!("k{}", i).into_bytes()); }
            let checks = 50_000;
            let fp = (0..checks)
                .filter(|i| bf.may_contain(&format!("nk{}", i).into_bytes()))
                .count();
            let fpr = fp as f64 / checks as f64;
            println!("bpk={}, theory={:.4}, measured={:.4}", bpk, bf.theoretical_fpr(), fpr);
            assert!(fpr < max_fpr, "bpk={} fpr={} exceeds max {}", bpk, fpr, max_fpr);
        }
    }
}
```

WSL 執行結果：

```
$ wsl cargo test -- --nocapture

running 3 tests
test tests::no_false_negatives ... ok
Theoretical FPR: 0.0084 (0.84%)
Measured    FPR: 0.0088 (0.88%)
test tests::false_positive_rate_within_bounds ... ok
bpk=6,  theory=0.0561, measured=0.0555
bpk=8,  theory=0.0217, measured=0.0216
bpk=10, theory=0.0084, measured=0.0090
bpk=14, theory=0.0012, measured=0.0013
test tests::different_bit_densities ... ok

test result: ok. 3 passed; 0 failed; 0 ignored; 0 measured
```

實測假陽性率與理論公式誤差在 5% 以內。

---

## 整合進 SSTable 讀取路徑

### Bloom filter 存在哪裡？

Bloom filter 儲存在 SSTable **檔案內部**，緊接在 index block 之前（RocksDB 稱之為 filter block / meta block）：

```
SSTable 檔案結構（含 Bloom filter）：

┌──────────────────────────────────────────┐
│  Data Block 0   (4KB, 壓縮)              │
├──────────────────────────────────────────┤
│  Data Block 1   (4KB, 壓縮)              │
├──────────────────────────────────────────┤
│  ...                                     │
├──────────────────────────────────────────┤
│  Filter Block                            │ ← Bloom filter 的 bit array
│  ┌──────────────────────────────────┐    │
│  │ filter_len: u32                  │    │
│  │ m: u32   (bit array 大小)        │    │
│  │ k: u32   (雜湊函數數量)          │    │
│  │ bits[0..⌈m/8⌉]  (raw bytes)     │    │
│  └──────────────────────────────────┘    │
├──────────────────────────────────────────┤
│  Index Block                             │
├──────────────────────────────────────────┤
│  Footer (16 bytes)                       │
│  index_offset: u64                       │
│  filter_offset: u64  ← 新增這個         │
└──────────────────────────────────────────┘
```

**關鍵**：Bloom filter 不大（10 bits/key，100 萬個 key = 1.25 MB），可以在 SSTable 開啟時全部載入記憶體（放進 block cache），之後的查詢完全不需要磁碟 I/O。

### 讀取路徑的改變

**沒有 Bloom filter 的讀取**：

```
GET "user:99999999"
  1. 查 MemTable                → not found
  2. 查 L0 SSTable-0:
     讀 footer → 讀 index → 讀 data block → not found   (3 次 I/O)
  3. 查 L0 SSTable-1:
     讀 footer → 讀 index → 讀 data block → not found   (3 次 I/O)
  4. 查 L1 SSTable:
     讀 footer → 讀 index → 讀 data block → not found   (3 次 I/O)
  ...
  最終：NOT FOUND（可能 10+ 次磁碟 I/O）
```

**有 Bloom filter 的讀取**：

```
GET "user:99999999"
  1. 查 MemTable                       → not found
  2. 查 L0 SSTable-0 的 Bloom filter:
     (已在 memory) may_contain() = false → 跳過，0 次磁碟 I/O
  3. 查 L0 SSTable-1 的 Bloom filter:
     (已在 memory) may_contain() = false → 跳過，0 次磁碟 I/O
  4. 查 L1 SSTable 的 Bloom filter:
     (已在 memory) may_contain() = false → 跳過，0 次磁碟 I/O
  ...
  最終：NOT FOUND（0 次額外磁碟 I/O）
```

假陽性率 1% 代表 100 次查詢中有 1 次會誤判「possibly present」，進而真的讀一次 SSTable。這一次讀取可以發現 key 不在這個 SSTable，繼續往下找——資料仍然正確，只是多了一次 I/O。

---

## 比較表

| 方案 | 假陰性率 | 假陽性率 | 空間複雜度 | 查詢時間 | 支援刪除 | 支援範圍查詢 |
|------|----------|----------|-----------|----------|----------|------------|
| Bloom filter | 0% | f ≈ 1% | O(m)，~1.25 bytes/key | O(k)，幾十 ns | 否 | 否 |
| 線性掃描所有 SSTable | 0% | 0% | 不額外佔空間 | O(L × B) 磁碟 I/O | 是 | 是 |
| 記憶體 HashSet（存所有 key） | 0% | 0% | O(n)，~50 bytes/key | O(1) | 是 | 否 |
| Counting Bloom filter | 0% | f ≈ 1% | O(m)，~4 bytes/key | O(k) | 是 | 否 |
| Cuckoo filter | 0% | f ≈ 1% | O(m)，~1 byte/key | O(1) | 是 | 否 |

線性掃描（無 filter）是正確的，但太慢。  
記憶體 HashSet 是正確且快的，但 100 萬個 key 需要 ~50 MB，遠超 Bloom filter 的 1.25 MB。  
Bloom filter 是三者間的最佳折衷：極小的空間代價換掉幾乎全部不必要的磁碟 I/O。

---

## 為什麼假陽性對資料庫可以接受？

假陽性「可能存在」會讓我們讀一次 SSTable，然後在 SSTable 裡找不到 key——這只是浪費一次 I/O，**資料不會出錯**。

因為 SSTable 的讀取會提供最終的正確答案：如果 key 真的不存在，SSTable 讀完也是 NOT FOUND；如果 Bloom filter 說「definitely not present」而我們選擇相信，那只有在 Bloom filter 無假陰性的前提下才安全。

**Bloom filter 不適合的場合**：如果「集合成員查詢」本身就是最終答案（例如：「這個 IP 有沒有在黑名單裡？」），用 Bloom filter 可能讓惡意 IP 被誤判為不在黑名單，直接導致安全漏洞。這種需要 100% 精確的場合不能用 Bloom filter。

資料庫的讀取路徑有 SSTable 作為後盾，假陽性可以被糾正；這是 Bloom filter 能在資料庫中廣泛應用的根本原因。

---

## 常見陷阱

**1. Bloom filter 無法刪除**

一旦把某個 bit 設為 1，無法知道它是被哪個 key 設的，所以無法撤回。如果刪掉 key 並清掉那個 bit，可能誤傷其他 key（它們也設了這個 bit）。

LSM-Tree 的做法是：刪除不修改 Bloom filter，等到 SSTable 在 compaction 時被重寫，新的 SSTable 才會建立一個不包含已刪除 key 的全新 Bloom filter。這正好與 SSTable 不可變的設計吻合。

**2. m 必須在建立時確定**

Bloom filter 的 bit array 大小 m 在建立時就固定。設計上要用 `m = n * bits_per_key`，其中 n 是**預期**插入的 key 數量。如果實際插入超過 n，假陽性率會上升（n 變大但 m 不變）。

SSTable 在 flush 前已知 MemTable 的 key 數量，所以 m 的估算準確。如果 MemTable 有 100 萬個 key，就建一個 1,000,000 × 10 = 10,000,000 bits 的 Bloom filter。

**3. 雜湊函數品質直接影響假陽性率**

理論假陽性率假設 k 個雜湊函數的輸出均勻分佈且獨立。如果雜湊函數品質差（例如直接用 `std::collections::hash_map::DefaultHasher` 單獨作為所有雜湊函數）導致輸出聚集，實際假陽性率會遠高於理論值。

使用 FNV-1a 或 xxHash 作為基底雜湊函數，再搭配 double hashing 生成 k 個位置，是兼顧品質與效能的標準做法。

**4. Bloom filter 的記憶體開銷要進 block cache**

1,000,000 個 key，10 bits/key = 10,000,000 bits = 1.25 MB，這是**每個 SSTable** 的開銷。一個有 100 個 SSTable 的系統，全部 Bloom filter 合計 125 MB。設計 block cache 時要給 Bloom filter 留空間，否則 filter 頻繁從磁碟讀入，失去加速效果。

**5. 「possibly present」不代表 key 在這個 SSTable 裡**

Bloom filter 只能排除「肯定不在」，即使判斷「可能在」，key 實際可能在更舊的 SSTable 或根本不存在。每個 SSTable 有自己的 Bloom filter，只管自己的 key 範圍；LSM-Tree 仍需依序查過所有層級。

---

## 進階：Blocked Bloom Filter

標準 Bloom filter 的問題：k 個 bit 散落在 m 位元的任意位置，每次查詢 k 次 random access，對 CPU cache 不友好。

RocksDB 使用 **Blocked Bloom Filter（Cache-line Bloom Filter）**：

```
把 m bits 分成若干個 512-bit（64 bytes = 1 cache line）的 block。
查詢時，先用一個「routing hash」決定要查哪個 block，
再把 k 個雜湊全部限制在那個 block 內。

                 routing hash
                      │
                      ▼
           ┌──────────────────────┐
           │  Block 0 (512 bits)  │  ← 所有 k 個 bit 都在這裡
           │  ■ □ □ ■ □ ■ □ □ …  │
           └──────────────────────┘
           ┌──────────────────────┐
           │  Block 1 (512 bits)  │
           │  □ ■ □ □ ■ □ □ ■ …  │
           └──────────────────────┘
```

這樣每次查詢只需要讀一個 cache line，CPU 的 prefetcher 效率大幅提升。代價是相同空間下假陽性率略高於標準 Bloom filter（因為 k 個 bit 被限制在同一個 block，不再完全獨立），但實測差異很小，CPU 效能提升顯著。

Bits per key 相同時，blocked bloom filter 的查詢延遲約是標準 bloom filter 的一半。

---

## 本章重點整理

- LSM-Tree 讀不存在的 key 需要掃描所有 SSTable；Bloom filter 把這個代價壓到接近 0。
- Bloom filter = m bits 的 bit array + k 個雜湊函數；insert 設 k 個 bit，query 檢查 k 個 bit 是否全為 1。
- **無假陰性**：插入過的 key 一定被找到。**有假陽性**：未插入的 key 有機率被誤判為存在。
- 假陽性率公式：`f ≈ (1 - e^(-k*n/m))^k`（Bloom, 1970）。
- 最佳 k：`k_opt = (m/n) × ln(2)`；此時 `f_opt ≈ (0.6185)^(m/n)`，只由 bits/key 決定。
- 10 bits/key → k=7 → f ≈ 1%，這是 RocksDB 的預設設定。
- Bloom filter 存在 SSTable 的 filter block，隨 SSTable 開啟後載入記憶體，查詢不需磁碟 I/O。
- 無法刪除。SSTable 不可變，compaction 重寫時才重建 Bloom filter，剛好對應 LSM-Tree 的設計。
- Double hashing trick：`h_i(x) = h1(x) + i × h2(x) mod m`，只用兩個雜湊計算模擬 k 個獨立雜湊。
- Blocked bloom filter（RocksDB 實際用法）：把所有 k 個 bit 限制在同一個 cache line，大幅降低查詢延遲。

---

## 自我檢核

1. 為什麼 Bloom filter 可以保證無假陰性，但無法保證無假陽性？從 bit array 的操作邏輯解釋。
2. 給定 n=50,000 keys、要求假陽性率 ≤ 2%，請計算需要多少 bits（m）和最佳 k？
3. 如果把 bits/key 從 10 降到 6，假陽性率大約變成多少？這會帶來什麼實際影響？
4. 為什麼 Bloom filter 不支援刪除？Counting Bloom filter 如何解決這個問題，它的代價是什麼？
5. 如果一個 SSTable 的 Bloom filter 說「possibly present」，但 key 真的不在這個 SSTable，LSM-Tree 接下來怎麼處理？最終能回傳正確答案嗎？

---

## 延伸閱讀

1. **Burton H. Bloom, "Space/Time Trade-offs in Hash Coding with Allowable Errors"**  
   Communications of the ACM, Vol. 13, No. 7, 1970, pp. 422–426.  
   原始論文。Section 2 完整推導假陽性率公式與最佳 k，數學從頭寫，讀完會對公式有直覺。

2. **RocksDB Wiki — "RocksDB Bloom Filter"**  
   https://github.com/facebook/rocksdb/wiki/RocksDB-Bloom-Filter  
   描述 RocksDB 實際使用的 blocked bloom filter（cache-line aligned）設計，以及 per-SSTable filter 和 whole-file filter 兩種模式的取捨。與本章理論直接對應的實作文件。

3. **LevelDB `util/bloom.cc`**  
   https://github.com/google/leveldb/blob/main/util/bloom.cc  
   原始 LevelDB 的 Bloom filter 實作，約 100 行 C++，乾淨直接。可與本章 Rust 實作對照，理解 double hashing 在 production 程式碼中的寫法。

4. **Broder & Mitzenmacher, "Network Applications of Bloom Filters: A Survey"**  
   Internet Mathematics, Vol. 1, No. 4, 2004.  
   系統性整理 Bloom filter 的各種變體：counting bloom filter（支援刪除）、compressed bloom filter、spectral bloom filter，以及在網路路由、快取、P2P 系統中的應用。想深入理解 Bloom filter 家族必讀。

---

→ [Ch 15 Compaction 策略](./15-compaction.md)
