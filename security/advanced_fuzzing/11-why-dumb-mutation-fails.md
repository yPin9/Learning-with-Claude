# Ch 11 — 為什麼 dumb mutation 打不進結構化格式

> **目標**: 理解為什麼 dumb byte mutation 在結構化格式（JSON/SQL/protobuf/PNG）面前幾乎無效，掌握三重障礙的底層機制，為後續結構感知 mutation 打好理論地基。

---

## 為什麼需要這章

你已經在 afl_plus_plus 課跑過 fuzzing，也知道 havoc mutation 會隨機翻轉、插入、刪除 bytes。那套在 string parsing、integer parsing、簡單 CLI 工具上效果不差。

但一旦目標是 JSON parser、SQL parser、protobuf decoder、或任何帶有 checksum 的二進位格式，coverage 曲線跑幾分鐘就趨於平坦。不是 fuzzer 壞了，是 dumb mutation 碰上了一道結構牆。

這章拆清楚這道牆長什麼樣、為什麼 `-x json.dict` 和「多給幾個 seed」都不夠，以及真正的解法在哪一章。

---

## 先建立直覺：JSON parser 的拒絕階層

大多數 parser 的結構決定了：越早被拒，浪費越多 exec。

```
輸入 bytes
    │
    ▼  check first byte
  [' ' '{' '[' '"' digit 't' 'f' 'n'] ?
    │
    ├─ NO  → immediate return          ← coverage wall #1
    │         (dumb seed 90% 死在這裡)
    │
    ▼ YES
  parse_object / parse_array / parse_string
    │
    ▼  check structure tokens
  ':' ',' '}' ']' 位置和順序對嗎？
    │
    ├─ NO  → return early              ← coverage wall #2
    │
    ▼ YES
  deep logic:
    - exponent path  (parse_number)
    - nested objects (遞迴深度)
    - unicode escape  (\uXXXX)
    - string interning / key dedup
    │
    ▼
  BUG SURFACE (integer overflow, OOB, UAF 都在這)
```

dumb mutation 的問題不只是「碰不到有效前綴」，而是就算某次 mutation 碰巧產生了 `{`，下一次 havoc 操作幾乎必然把它改掉或移位，讓整個 input 重新回到 wall #1 前面被拒。

---

## 真實數據：seed 差 5 倍覆蓋

下面是同一個小型 JSON parser target、同樣的 libFuzzer 版本，只換 seed 的初始化結果：

```
# Dumb seed: 只給 "hello world"
#2  INITED cov=8 ft=9 corp=1/11b

# Structured seeds: {}, [], "a", 0, true, null
#7  INITED cov=39 ft=66 corp=6/15b
```

初始覆蓋 8 vs 39 edges，差了將近 5 倍。這還只是 init 時刻，後續 dumb mutation 繼續跑，structured seed 組的曲線持續往上爬，dumb seed 組在幾分鐘內就飽和。

這組數字說明一件事：mutation 策略本身還沒進場，seed 的品質就已經決定了你能看到多少 code path。

---

## 三重障礙詳解

### 障礙 1：magic bytes 與結構前綴

JSON 第一個非空白 byte 必須是 `{`, `[`, `"`, digit, `t`, `f`, `n` 之一。256 個 byte 值裡面約 11 個合法，通過率 4.3%。

havoc 最終會撞進這 4.3%，所以 magic bytes 本身不是最硬的牆。真正的問題是：havoc 在找到有效前綴之後，下一輪 mutation 大概率又把它打壞。整體來看，大量 exec 反覆在 wall #1 前後跳，coverage 無法穩定累積。

afl++ 的 `cmplog` / redqueen 模式能自動推斷 magic bytes，這個問題可以部分緩解，但後面兩個障礙 cmplog 幫不上。

### 障礙 2：checksum（最硬的牆）

PNG、ZIP、PDF、gzip 這類格式在結構欄位裡嵌了 CRC32 或 Adler-32。

```
PNG IDAT chunk 結構:
┌────────────┬──────────┬──────────────┬─────────┐
│ Length (4B)│ Type (4B)│ Data (NB)    │ CRC (4B)│
└────────────┴──────────┴──────────────┴─────────┘
                                            ↑
                              改任何一個 data byte
                              → CRC 不符 → immediate return
```

任何 dumb mutation 改了 data bytes 但沒有同步更新 CRC，parser 在讀 chunk 的第一步就拒絕，永遠進不到 data 解碼邏輯。這不是「概率問題」，是確定性的屏蔽。

cmplog 看得到 CRC 比較，但它是 single-byte 或 short string 的推斷工具，對 CRC32 這種輸入相關的 4-byte 計算結果無能為力。

### 障礙 3：length-prefix 欄位

protobuf、TLV 協定、MQTT、各種 binary frame 都有 length-prefix：

```
TLV frame:
┌────────┬──────────┬─────────────────────────┐
│ Tag(1B)│Length(2B)│ Value (Length bytes)     │
└────────┴──────────┴─────────────────────────┘
             ↑
  dumb mutation 改了 Value 但沒改 Length
  → parser 按舊 Length 讀，切到錯誤位置
  → 後續欄位解析全部錯位
  → 永遠進不去 Value 的實際邏輯
```

這個障礙比 checksum 更隱蔽，因為 parser 不會立即 return，而是繼續跑但走在錯誤的 offset 上，最後在無關的地方死掉，留下一個難以復現的 crash。

### 障礙 4：狀態依存結構

SQL 這類語言有跨欄位的一致性約束：

```sql
SELECT <column_list> FROM <table> WHERE <condition>
   ↑           ↑          ↑
   改這裡       改這裡      不同步
   → column count 和 FROM clause 不一致
   → planner 在 bind 階段崩潰，跟你想找的 parser bug 無關
```

dumb mutation 改了 `SELECT` 後面的 token 但沒有同步調整 `FROM` 之後的 table reference，整個 statement 的語意一致性被打破。parser 的早期拒絕在這裡換成了 planner 的錯誤路徑，你在 coverage 裡看到的是雜訊，不是真正的 bug surface。

---

## 底層機制：edges 分佈

```
我們的小型 JSON parser，total edges: 138

早期拒絕 path (coverage wall #1, #2)
  edge  1-8:   check first byte, dispatch
  edge  9-15:  skip_whitespace

淺層結構 path (coverage wall #2 之後)
  edge 16-25:  parse_object key 讀取
  edge 26-35:  expect ':' separator
  edge 36-40:  parse_object value dispatch

深層邏輯 path (bugs live here)
  edge 41-80:  parse_object / parse_array 遞迴嵌套
  edge 81-110: parse_number 指數/小數 path
  edge 111-138: parse_string unicode escape (\uXXXX)

─────────────────────────────────────────────────
Dumb seed ("hello world") init:      edge 1-8    (8 edges)
Structured seeds init:               edge 1-40   (39 edges)
Structured + structure-aware mut:    edge 1-100+ (進 bug surface)
```

這張圖解釋了「coverage 飽和」的實質：dumb mutation 能讓 edge 1-15 的計數器飆高，但 edge 16 之後幾乎沒有新的覆蓋。fuzzer 的 power schedule 根據 coverage 增長分配 mutation 時間，覆蓋不增長就分配不到時間，形成死鎖。

---

## 進階用法：能緩解但不能根治的招

**afl++ `-P explore` + cmplog**

```bash
AFL_USE_CMPLOG=1 afl-fuzz -P explore -i seeds/ -o out/ -- ./target @@
```

cmplog 插樁記錄所有比較指令的兩側值，redqueen 嘗試把 input 裡的某段 bytes 替換成比較的另一側。這對 magic bytes 有用，對 checksum 無用（因為 CRC 值本身依賴 input 內容）。

**字典 `-x`**

```bash
afl-fuzz -x json.dict -i seeds/ -o out/ -- ./target @@
```

字典補充的是 token（`"null"`, `"true"`, `"false"`, `":"`, `"}"`），不理解 token 之間的結構關係。`{"key"}` 這種 dict injection 能越過 wall #1，但打不進 value 的處理路徑，因為缺少 `:` + value 的組合。

**structured seed corpus**

給 `{}`, `[]`, `"a"`, `0`, `true`, `null`, `{"k":1}`, `[1,2,3]`。這是成本最低的改善方式，能讓初始覆蓋從 8 edges 跳到 39 edges。但 mutation 之後很快就破壞合法性，後期收益遞減。

真正的解法在 Ch 12（libprotobuf-mutator / FuzzTest 的結構感知 mutator）和 Ch 13（grammar fuzzing）。

---

## 對比取捨表

| 方法 | 適用目標 | 穿透結構 | 速度 | 實作難度 |
|------|---------|---------|------|---------|
| Dumb byte mutation | CLI 工具、簡單 parser | 差（被早期 return 擋） | 快 | 低 |
| 結構化 seed corpus | 任何有明確 input 的目標 | 中（進得去但 mutation 不保結構） | 快 | 低 |
| 字典 + cmplog | magic bytes 問題 | 中（解決前綴，不解決 checksum） | 快 | 低 |
| Structure-aware mutation (Ch 12) | 協定/格式化輸入 | 好（保持結構合法性） | 中 | 中 |
| Grammar fuzzing (Ch 13) | 語言/DSL/SQL | 極好（100% 語法合法） | 慢 | 高 |

---

## 踩雷

**踩雷 1：「havoc 最終還是會找到有效前綴，所以 dumb mutation 夠用」**

錯誤直覺：havoc 遲早會隨機產生 `{` 開頭的 bytes，這樣不就進去了？

真相：能找到有效前綴，但接下來每一輪 havoc 操作（bit flip、byte insertion、splice）以極高概率再次打壞這個剛建立好的合法結構。最終效果是大量 exec 在 wall #1/wall #2 前後反覆橫跳，coverage 計數器在早期 edges 上累積，scheduler 的 power 分配根本不會讓這條路徑繼續被探索。你在 afl++ UI 看到的「execs/s 很高」其實是在做無效的重複覆蓋。

**踩雷 2：「我加了 -x json.dict 字典就解決了結構問題」**

錯誤直覺：字典裡有 `{`, `}`, `"`, `:`, `null`, `true`, `false`，mutation 把這些塞進去就能產生合法 JSON。

真相：字典 injection 是把 token 隨機插入 input 的任意位置，不保證 token 的相對順序和巢狀關係正確。`{"key"}` 能越過 wall #1，但因為缺少 `: value`，還是在 wall #2 被拒。字典 + cmplog 的組合能解決 magic bytes 問題，但對結構依存的 token 排列（object 必須是 `"key": value` 對、array 內 value 必須用 `,` 分隔）完全沒有感知。

**踩雷 3：「seed corpus 夠豐富就夠了，給 1000 個真實 JSON 檔案」**

錯誤直覺：seed 多 = 覆蓋廣，覆蓋廣 = mutation 有更多好的起點。

真相：seed 的初始覆蓋確實會提高，但 mutation 本身不保結構。1000 個合法 JSON seed，每一次 havoc mutation 後幾乎必然產生不合法的 JSON，fallback 回早期拒絕。更嚴重的問題：大型 corpus 讓 fuzzer 分散 power 在太多 seed 上，每個 seed 分到的 mutation 次數減少，整體探索效率反而下降。seed 多 ≠ 結構感知 mutation，這兩件事根本不是同一個維度。

---

## 進階延伸

**afl++ custom mutator API**

afl++ 提供了 custom mutator hook，讓你在 havoc 之外插入自己的結構感知 mutation 函數：

```c
// custom_mutator.c
size_t afl_custom_fuzz(void *data, uint8_t *buf, size_t buf_size,
                       uint8_t **out_buf, uint8_t *add_buf,
                       size_t add_buf_size, size_t max_size) {
    // 在這裡實作保結構的 mutation
    // Ch 12 會用 libprotobuf-mutator 填充這個函數
    return mutated_size;
}
```

這個 API 的存在本身就是承認 dumb mutation 對結構化格式不夠用。Ch 12 會用 libprotobuf-mutator 和 FuzzTest 填充這個 hook，讓 mutation 在合法的 protobuf/JSON AST 上操作，而不是在 raw bytes 上亂翻。

**cmplog 的能力邊界**

cmplog 插樁記錄 `cmp`、`strcmp`、`memcmp` 兩側的值，redqueen 把 input 裡的對應 bytes 替換成比較目標。這能解決：magic bytes、版本號比較、簡單的 enum 值。無法解決：CRC32（值依賴整個 input，不能單獨推斷）、length-prefix（需要同時更新多個欄位）、語意約束（SQL 的 column/table 一致性）。

---

## 動手練習

1. 在 libFuzzer 下為一個你自己寫的 JSON parser 跑兩組實驗：只給 `"hello"` 作 seed vs 給 `{}`, `[]`, `"a"`, `0`, `true`, `null`。記錄 INITED 時的 `cov=` 和 `ft=` 數字，確認覆蓋差距。

2. 找一個開源的 PNG decoder（例如 libpng 的 contrib/pngminus/），用 dumb seed 跑 30 秒，記錄達到的 edge count。然後改用合法的小型 PNG 檔案當 seed，再跑 30 秒，對比結果。

3. 閱讀 afl++ 的 `docs/custom_mutators.md`，找到 `fuzz_count` callback 的說明，理解 fuzzer 在什麼時機呼叫 custom mutator vs 內建 havoc。寫一段 200 字的說明，解釋為什麼 `post_process` hook 比 `fuzz` hook 更適合處理 checksum 更新。

---

## 本章重點

- dumb byte mutation 在結構化格式面前主要卡在三道牆：magic bytes / 結構前綴、checksum 欄位、length-prefix 欄位
- 真實測量：同目標只換 seed，初始覆蓋可以差 5 倍（`cov=8` vs `cov=39`）
- checksum 是最硬的障礙，cmplog 無法解決，必須在 mutation 層或 post-process 層同步更新
- `-x dict` 補的是 token，不補 token 之間的結構關係；seed corpus 豐富 ≠ 結構感知 mutation
- afl++ custom mutator API 是正確的切入點；Ch 12 用 libprotobuf-mutator 填充它，Ch 13 用 grammar fuzzing 解決語言類目標

---

## 自我檢核

- [ ] 能說出 JSON parser 的兩道 coverage wall 各攔截在哪一步
- [ ] 能解釋為什麼 checksum 障礙比 magic bytes 障礙更難被 cmplog 突破
- [ ] 能從記憶中說出 dumb seed vs structured seeds 的初始覆蓋數字（`cov=8` vs `cov=39`）
- [ ] 能解釋「seed corpus 多 ≠ 結構感知 mutation」這個命題
- [ ] 能說出 `-x json.dict` 能解決什麼、不能解決什麼

---

## 延伸閱讀

1. **Böhme et al., "Coverage-Based Greybox Fuzzing as Markov Chain" (CCS 2016)**
   讀 §2 "Power Schedules" 和 §3 "Seed Selection" 這兩節。文章用 Markov chain 建模 fuzzer 探索過程，power schedule 決定每個 seed 分到多少 mutation 預算。理解「飽和 seed 的 energy 趨近於零」這個推論，就能理解為何 dumb seed 在 wall #1 飽和後停止貢獻覆蓋。這是理解後續 structure-aware fuzzing 必要性的數學基礎。

2. **The Fuzzing Book, Ch 5 "Grammar-Based Fuzzing"** (https://www.fuzzingbook.org/html/GrammarFuzzer.html)
   讀開頭的 "Motivation" section 和 "The Limits of Mutation-Based Fuzzing" 小節。作者用實測數字展示 dumb mutation 在 URL parser 上的覆蓋效率，並引出 grammar 的必要性。這一節不長，是這章論點最好的英文對照閱讀材料。

3. **AFL++ 官方文件：Custom Mutators** (https://github.com/AFLplusplus/AFLplusplus/blob/stable/docs/custom_mutators.md)
   從 `fuzz_count` 讀到 `afl_custom_queue_new_entry`。這段說明了 fuzzer 呼叫 custom mutator 的時機、`post_process` 用來做 checksum fixup 的慣用模式、以及 `afl_custom_queue_new_entry` 讓你在新 seed 進 queue 時做結構驗證。Ch 12 的實作直接依賴這幾個 callback，先讀過這段才不會在 hook 點上迷路。

---

dumb mutation 的問題現在清楚了：它不是慢，是在結構化格式面前根本打不進有意義的 code region。下一章進入解法的核心。

→ [下一章](./12-libprotobuf-mutator-fuzztest.md)
