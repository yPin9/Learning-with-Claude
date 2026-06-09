# Ch 39 — hash table

> **目標**：搞懂 hash table 怎麼做到平均 O(1) 查找、hash function 的角色、碰撞（collision）兩大解法（chaining vs open addressing）、load factor 與 rehash、以及最壞情況退化。hash table 是面試高頻概念題。

> **環境**：C，`gcc -Wall`。前置：Ch 36（array/linked list）。

## 為什麼考這個

hash table 是「用空間換時間」做到平均 O(1) 查找的核心結構——字典、快取、symbol table、資料庫索引都用它。面試愛問「O(1) 怎麼來的」「碰撞怎麼處理」「最壞情況呢」。答得清楚代表你懂原理而非死背「hash table 很快」。

## 先建立直覺

```
   問題：給一個 key（如字串 "apple"），怎麼 O(1) 找到它的值？

   array 用整數索引 O(1)：arr[3] 直接算位址。
   但 key 是字串怎麼辦？→ 把 key「轉成」一個陣列索引！

   "apple" --hash function--> 7  →  table[7]
   "banana" --hash function--> 2  →  table[2]
```

核心：**hash function 把任意 key 映射成陣列索引**，於是查找變成陣列存取 O(1)。代價是要一個夠大的陣列（空間換時間）。

## hash function（雜湊函數）

把 key 轉成 `[0, table_size)` 的索引。好的 hash function 要：
- **均勻分布**（avalanche）：不同 key 盡量散到不同 index，減少碰撞。
- **快**：計算要快（不然 O(1) 沒意義）。
- **確定性**：同 key 永遠同 index。

字串 hash 範例（簡單版）：

```c
unsigned int hash(const char *key, int table_size) {
    unsigned int h = 5381;          // djb2 起始值
    int c;
    while ((c = *key++))
        h = ((h << 5) + h) + c;     // h * 33 + c
    return h % table_size;          // 壓到 table 範圍
}
```

`% table_size` 把大 hash 值壓進陣列範圍。table_size 常取**質數**（減少規律性碰撞）。

## 碰撞（collision）

不同 key 算出同一個 index = 碰撞。一定會發生（鴿籠原理：key 空間 > 陣列大小）。兩大解法：

### 1. chaining（鏈結法，最常用）

每個 slot 放一個 linked list，碰撞的 key 都掛在同一條鏈上：

```
   table[2] → ["banana"|val] → ["grape"|val] → NULL   ← 碰撞的掛成鏈
   table[7] → ["apple"|val] → NULL
```

```c
typedef struct Entry {
    char *key; int val;
    struct Entry *next;     // 鏈
} Entry;
Entry *table[SIZE];

// 查找：算 index → 走那條鏈找 key
int get(const char *key) {
    Entry *e = table[hash(key, SIZE)];
    while (e) { if (strcmp(e->key, key)==0) return e->val; e = e->next; }
    return -1;  // not found
}
```

查找：算 index O(1) + 走那條鏈。鏈短時平均 O(1)；鏈長時退化。

### 2. open addressing（開放定址法）

碰撞時找「下一個空 slot」放（資料都存在 array 本身，沒有鏈）：
- **linear probing（線性探測）**：碰撞就試 index+1、+2...（會聚集 clustering）。
- **quadratic probing**：試 index+1²、+2²...（減少聚集）。
- **double hashing**：用第二個 hash function 決定步長。

```
   put "grape" → hash=2，但 table[2] 已被 "banana" 佔 → 試 table[3]（空）→ 放這
```

open addressing 的刪除麻煩（要用 tombstone 標記，不能直接清空，否則探測鏈斷掉）。

| | chaining | open addressing |
|---|---|---|
| 碰撞處理 | 掛 linked list | 找下一個空 slot |
| 空間 | 額外指標 | 無額外指標、但要 array 夠大 |
| load factor | 可 > 1 | 必須 < 1（array 會滿）|
| cache | 差（鏈分散）| 好（都在 array，連續，Ch 30）|
| 刪除 | 直接刪節點 | 要 tombstone |

## load factor 與 rehash

**load factor（負載因子）α = 元素數 / 表大小**。α 越大碰撞越多、效能越差。

- chaining：α 可超過 1（鏈變長），但通常控制在 0.75 左右。
- open addressing：α 必須 < 1，通常 > 0.7 就效能驟降。

當 α 超過閾值，**rehash（重新雜湊）**：開一個更大的表（通常 2 倍），把所有元素重新 hash 進去。rehash 是 O(n)，但攤平（amortized）後每次操作仍平均 O(1)。

## 時間複雜度（必考的「最壞情況」）

| 操作 | 平均 | 最壞 |
|---|---|---|
| 查找/插入/刪除 | **O(1)** | **O(n)** |

**最壞 O(n)**：所有 key 都碰撞到同一個 slot（爛 hash function，或被惡意攻擊 hash flooding）→ 退化成一條 linked list（chaining）或全表探測（open addressing）→ O(n)。

這就是為什麼面試要強調「**平均** O(1)」——前提是 hash function 好、load factor 控制得當。

## 考古題詳解

### Q1：hash table 為什麼能 O(1) 查找？

<details>
<summary>詳解</summary>

hash function 把 key 映射成陣列索引，於是查找變成陣列存取（O(1)）。前提：hash function 均勻分布、load factor 不太高（碰撞少）。是空間換時間。

**考點**：O(1) 的原理，必考。
</details>

### Q2：碰撞怎麼處理？兩種方法的差異？

<details>
<summary>詳解</summary>

- **chaining**：每 slot 掛 linked list，碰撞的掛同一條鏈。簡單、α 可 > 1、刪除容易；但鏈分散 cache 差、有指標開銷。
- **open addressing**：碰撞找下一個空 slot（linear/quadratic/double hashing）。cache 好（連續）、無指標；但 α 必須 < 1、刪除要 tombstone、有 clustering。

**考點**：兩種碰撞解法，必考。
</details>

### Q3：hash table 的最壞時間複雜度？什麼時候發生？

<details>
<summary>詳解</summary>

最壞 **O(n)**：所有 key 碰撞到同一 slot（hash function 爛或被惡意構造 hash flooding 攻擊）→ 退化成 linked list（chaining）或全表探測。所以強調「**平均** O(1)」。

防禦：好的 hash function、隨機化 seed（防 hash flooding）、控制 load factor + rehash。

**考點**：最壞情況，分辨真懂假懂的題。
</details>

### Q4：load factor 是什麼？為什麼要 rehash？

<details>
<summary>詳解</summary>

load factor α = 元素數 / 表大小，衡量「多滿」。α 越大碰撞越多、效能越差。超過閾值（chaining ~0.75、open addressing ~0.7）就 **rehash**：開更大的表（通常 2 倍）重新 hash 所有元素。rehash 單次 O(n)，但攤平後維持平均 O(1)。

**考點**：load factor + rehash，進階考點。
</details>

### Q5：什麼時候不該用 hash table？

<details>
<summary>詳解</summary>

- **要排序 / 範圍查詢**：hash table 無序，要「找 > 50 的所有 key」「最小值」做不到——用 BST（Ch 38，有序）。
- **記憶體吃緊**（嵌入式）：hash table 要預留大陣列（空間換時間），韌體 RAM 少時不划算。
- **key 數量很少**：直接 array 或線性搜尋更簡單，hash 的常數開銷不值得。
- **要最壞情況保證**：hash 最壞 O(n)，real-time 系統（Ch 18）要 O(log n) 保證時用平衡 BST。

**考點**：何時不用，展現判斷力。
</details>

## 踩雷集錦

1. **以為 hash table 永遠 O(1)**：最壞 O(n)（全碰撞）。是「平均」O(1）。
2. **碰撞以為很少見**：鴿籠原理保證會碰撞（key 空間 > 陣列）。生日悖論下碰撞比直覺快很多。
3. **open addressing 直接清空刪除**：會斷掉探測鏈，後面的 key 找不到。要用 tombstone 標記。
4. **table_size 用 2 的次方又用爛 hash**：某些 hash 配 2^n 大小會讓低位元主導，碰撞變多。常用質數大小。
5. **load factor 太高不 rehash**：碰撞暴增，O(1) 名存實亡。
6. **要排序還用 hash table**：hash 無序。要排序/範圍查詢用 BST。
7. **嵌入式無腦用 hash table**：吃 RAM。韌體常用更省的結構。

## 速記

- **原理**：hash function 把 key → 陣列索引 → 查找變陣列存取 **平均 O(1)**（空間換時間）。
- **hash function**：均勻、快、確定性；`% table_size`（常質數）壓範圍。
- **碰撞**（必發生）：**chaining**（掛 linked list，α可>1，cache差，刪除易）vs **open addressing**（找空 slot，cache好，α<1，刪除要 tombstone）。
- **load factor** α = 元素/表大小；超閾值（~0.75）**rehash**（開 2 倍表重 hash，攤平 O(1)）。
- **最壞 O(n)**：全碰撞（爛 hash / hash flooding）→ 退化。
- 不該用：要排序/範圍查詢（用 BST）、嵌入式 RAM 少、要最壞保證。

## 自我檢核

- [ ] hash table 怎麼做到平均 O(1)？前提是什麼？
- [ ] chaining 和 open addressing 怎麼處理碰撞？各的優缺點？
- [ ] hash table 最壞是什麼複雜度？什麼情況發生？
- [ ] load factor 是什麼？為什麼要 rehash？
- [ ] 什麼時候該用 BST 而非 hash table？

## 延伸閱讀

### 書籍

- **《Introduction to Algorithms (CLRS)》** — Ch 11 Hash Tables
  - **讀哪幾章**：11.1–11.4（直接定址、hash function、open addressing）。
  - **和本章的關聯**：hash table 的標準理論，含 universal hashing。

### 文章

- **[GeeksforGeeks — Hashing Data Structure](https://www.geeksforgeeks.org/hashing-data-structure/)**
  - **讀哪裡**：collision handling、load factor 篇。
  - **和本章的關聯**：補強兩種碰撞解法的實作細節。

hash table 是查找結構的高峰，下一章回到演算法本身——排序，面試必考的複雜度與穩定性。

→ [Ch 40 sorting](./40-sorting.md)
