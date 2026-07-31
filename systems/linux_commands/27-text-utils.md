# Ch 27 — sort / uniq / cut / tr / join

> **目標**：掌握一組「小而專」的文字工具——sort（排序，含外部排序處理大檔案）、uniq（去重，為什麼必須先排序）、cut（切欄位，和 awk 的差別）、tr（字元轉換/刪除）、paste/join（合併）。這些是管線裡的零件，配合 grep/sed/awk 組成完整的文字處理流水線。這章收尾 Part 6，把所有文字工具串成一套。

> **環境**：GNU coreutils（Linux）。BSD 版部分選項不同會標注。

## 為什麼還需要這些「小工具」？

awk（Ch 26）幾乎無所不能，為什麼還要 sort、uniq、cut？因為**單一職責的小工具更簡單、更快、更好組合**（Ch 21 哲學）。要排序就 `sort`（不用在 awk 裡寫排序）、要去重就 `uniq`、要切欄位最快就 `cut`。它們是管線的標準零件。

更重要的是，它們各自有 awk 沒有的專長：sort 能處理**比記憶體大的檔案**（外部排序）、uniq 有 `-c`（計數）、tr 做**字元級**轉換（awk 是欄位級）。理解每個工具的專長和限制（特別是 uniq「為什麼要先 sort」），你才能組出正確高效的管線。

## sort：排序（不只是字母順序）

```bash
# 基本排序（預設：按整行、字典序、升序）
sort file                        # 字母順序排序
sort -r file                     # 反向（降序）
sort -u file                     # 排序 + 去重（unique）

# 數值排序（關鍵！字典序 vs 數值序）
echo -e "10\n2\n1\n20" | sort           # 1 10 2 20（字典序！"10" < "2"）
echo -e "10\n2\n1\n20" | sort -n        # 1 2 10 20（數值序，-n = numeric）
echo -e "10\n2\n1\n20" | sort -rn       # 20 10 2 1（數值 + 反向）

# 按欄位排序（-k）
sort -k2 file                    # 按第 2 欄排序
sort -k2 -n file                 # 按第 2 欄數值排序
sort -t: -k3 -n /etc/passwd      # 用 : 分隔，按第 3 欄（UID）數值排序
ps aux | sort -k3 -rn | head     # 按 CPU（第 3 欄）排序，找最吃 CPU 的

# 其他有用選項
sort -h file                     # human-readable 數字排序（1K 2M 3G，配 du -h）
du -h | sort -h                  # 按大小排序（懂 K/M/G）
sort -k2,2 -k3,3n file           # 多級排序（先第 2 欄字母，再第 3 欄數值）
sort -f file                     # 忽略大小寫
```

```
sort 的關鍵：字典序 vs 數值序

  字典序（預設）：一個字元一個字元比
    "10" vs "2"：先比 '1' vs '2' → '1' < '2' → "10" 排前面
    → "10" < "2" < "20"（違反數值直覺！）
        │
  數值序（-n）：當成數字比
    10 vs 2 → 2 < 10（符合直覺）
        │
  → 處理數字一定要 -n，否則 "10" 會排在 "2" 前面
    這是 sort 最常見的陷阱
```

> **`sort` 預設是字典序，數字一定要 `-n`**。這是 sort 最常見的坑：`sort` 對 `10 2 1 20` 給 `1 10 2 20`——因為字典序逐字元比，`"10"` 的第一個字元 `'1'` 小於 `"2"`，所以 `"10"` 排在 `"2"` 前面。要數值排序必須 `-n`。`-k` 指定按哪一欄排（`sort -k2 -n` 按第 2 欄數值），`-t` 設欄位分隔符（`sort -t: -k3 -n` 按 `:` 分隔的第 3 欄）。`-h`（human numeric）能排 `1K 2M 3G` 這種帶單位的（配 `du -h` 超實用）。記住這幾個就能應付 90% 的排序需求。

## sort 底層:外部排序如何處理大檔案

sort 能排序比記憶體還大的檔案——這是它的隱藏本領：

```
sort 的外部排序（external sort）：

  問題：要排序 100 GB 檔案，但只有 8 GB 記憶體，怎麼辦？
        │
  外部排序（merge sort 的變體）：
    1. 讀一塊能放進記憶體的資料（如 1 GB）
    2. 在記憶體裡排序它，寫成暫存檔
    3. 重複，產生 N 個「已排序的暫存檔」
    4. 「多路合併」這 N 個暫存檔（每次取各檔最小的）
       → 合併出完整排序結果
        │
  → 所以 sort 能排任意大的檔案（受限於磁碟，不是記憶體）
    暫存檔放在 /tmp（或 -T 指定）
```

```bash
# sort 的效能選項（處理大檔案）
sort -S 2G bigfile               # -S：用 2GB 記憶體緩衝（減少暫存檔，更快）
sort --parallel=4 bigfile        # 多執行緒排序
sort -T /mnt/bigdisk bigfile     # -T：暫存檔放哪（/tmp 可能太小）

# 驗證 sort 用暫存檔（大檔案時）
yes "$(head -c 100 /dev/urandom | base64)" | head -10000000 > big.txt
strace -f -e trace=open,openat sort big.txt 2>&1 >/dev/null | grep -i tmp | head
# 看到 sort 在 /tmp 開暫存檔（外部排序的證據）
```

> **sort 用「外部排序」處理比記憶體大的檔案——這是它不可被 awk 取代的核心能力**。awk 排序要把資料全載進記憶體（陣列），100 GB 檔案會 OOM。sort 用 merge sort 的變體：把資料切成「能放進記憶體的塊」，各自排序寫成暫存檔（放 /tmp），最後多路合併。所以 sort 能排任意大的檔案（受限於磁碟空間，不是 RAM）。實務上處理大檔案要注意：`/tmp` 可能太小（用 `-T` 指定大磁碟）、`-S` 加大記憶體緩衝能減少暫存檔加速、`--parallel` 用多核。這是「為什麼 log 分析常以 sort 為中心」的原因——它能扛住生產環境的大資料。

## uniq：去重（但必須先排序！）

```bash
# uniq 去除「相鄰的」重複行 —— 注意「相鄰」！
echo -e "a\na\nb\na" | uniq            # a b a（只去掉相鄰的，最後的 a 沒被去！）
echo -e "a\na\nb\na" | sort | uniq     # a b（先 sort 讓相同的相鄰，才能全去重）

# 為什麼 uniq 要先 sort：uniq 只比較「相鄰行」
# 不相鄰的重複它看不到 → 必須先 sort 把相同的聚在一起

# uniq 的招牌：-c 計數
echo -e "a\nb\na\na\nc" | sort | uniq -c
#   3 a    ← 計數每個唯一值出現幾次
#   1 b
#   1 c

# 經典管線：找最頻繁的（Ch 21 的骨架）
awk '{print $1}' access.log | sort | uniq -c | sort -rn | head
#   提取 → 排序 → 計數 → 按次數排序 → 前 N

# 其他 uniq 選項
sort file | uniq -d              # -d：只印「有重複的」行
sort file | uniq -u              # -u：只印「沒重複的」行（唯一出現一次的）
sort file | uniq -i              # -i：忽略大小寫比較
```

```
為什麼 uniq 必須先 sort：

  uniq 只看「相鄰行」（為了 streaming，不用載入全部）：
    輸入 a a b a
    uniq：a(印) a(同前,跳) b(印) a(和前面 b 不同,印)
    → a b a（最後的 a 沒去掉，因為它和前一行 b 不同）
        │
  sort 先讓相同的聚在一起：
    sort → a a a b
    uniq → a b（現在所有 a 相鄰，正確去重）
        │
  → uniq 設計成只比相鄰行，是為了能 streaming（省記憶體）
    代價是必須先 sort。或用 awk '!seen[$0]++'（不用 sort，但要記憶體）
```

> **uniq 只去除「相鄰」重複，所以幾乎總是要先 sort**。`uniq` 為了能串流處理（不載入整個檔案）只比較**相鄰行**——`a a b a` 給 `a b a`（最後的 a 和前面的 b 不同，沒被去掉）。要正確去重必須先 `sort` 讓相同的行聚在一起：`sort | uniq`。`uniq -c`（計數）是它的招牌——配合 `sort | uniq -c | sort -rn` 是 log 分析的萬用骨架（統計頻率排序）。替代方案：`sort -u`（排序+去重一步到位）或 `awk '!seen[$0]++'`（Ch 26，不用排序、保持原順序，但要記憶體存所有見過的行）。選擇：要計數用 `uniq -c`，要保序用 awk，純去重 `sort -u` 最簡。

## cut：切欄位（和 awk 的取捨）

```bash
# cut 按「欄位」或「字元位置」切
cut -d: -f1 /etc/passwd          # -d 分隔符，-f 欄位號：第 1 欄（username）
cut -d: -f1,7 /etc/passwd        # 第 1 和第 7 欄
cut -d: -f1-3 /etc/passwd        # 第 1 到 3 欄（範圍）
cut -d, -f2 data.csv             # CSV 第 2 欄

# 按字元位置切（-c）
echo "2024-01-15" | cut -c1-4    # 2024（第 1-4 個字元）
echo "abcdefgh" | cut -c2,4,6    # bdf（第 2,4,6 個字元）

# 按 byte（-b）—— 注意多位元組字元
echo "héllo" | cut -c1-3         # hél（字元）
echo "héllo" | cut -b1-3         # 可能切壞 UTF-8（byte 不是字元）
```

```
cut vs awk 切欄位的關鍵差別：

  cut -d' ' -f2  對 "a    b"（多個空白）：
    → cut 把「每一個空白」當分隔 → 第 2 欄是「空的」！
    （a, '', '', '', b → 第 2 欄是第一個空白後的空字串）
        │
  awk '{print $2}' 對 "a    b"：
    → awk 把「連續空白」當「一個」分隔 → 第 2 欄是 "b"
        │
  → 處理「對齊的、多空白分隔」的資料（ps、ls -l）用 awk
    處理「嚴格單字元分隔」的資料（CSV、/etc/passwd）用 cut
```

> **cut 和 awk 切欄位的關鍵差別：cut 把「每個」分隔符當一次切割，awk 把「連續」分隔符當一個**。對 `a    b`（多個空白），`cut -d' ' -f2` 給**空字串**（第一個空白後就是第 2 欄，是空的），而 `awk '{print $2}'` 給 `b`（連續空白算一個分隔）。所以：**嚴格單字元分隔的資料**（CSV、/etc/passwd 的 `:`）用 cut（簡單快速）；**對齊的、多空白分隔的資料**（`ps aux`、`ls -l` 的輸出）用 awk（自動處理連續空白）。用錯會得到空欄位或錯位。cut 還有 `-c`（按字元位置切，固定寬度資料用）。cut 比 awk 簡單快速，但只能做「切欄位」這一件事——需要計算、條件、重組就用 awk。

## tr：字元級轉換與刪除

```bash
# tr 做「字元對字元」的轉換（不是字串！）
echo "hello" | tr 'a-z' 'A-Z'    # HELLO（小寫轉大寫，逐字元映射）
echo "hello" | tr 'el' 'ip'      # hippo→ "hippo"（e→i, l→p）
echo "hello world" | tr ' ' '_'  # hello_world（空白換底線）

# tr -d：刪除字元
echo "hello123" | tr -d '0-9'    # hello（刪所有數字）
echo "a-b-c" | tr -d '-'         # abc（刪所有 -）
cat file | tr -d '\r'            # 刪除 \r（DOS → Unix 換行，去掉 CR）

# tr -s：壓縮連續重複（squeeze）
echo "a    b    c" | tr -s ' '   # a b c（多空白壓成一個）
echo "aaabbbccc" | tr -s 'abc'   # abc（連續重複壓成一個）

# tr -c：補集（complement）
echo "hello123world" | tr -cd 'a-z'   # helloworld（-c：刪除「非」小寫字母）
echo "hello123" | tr -cd '0-9'        # 123（只留數字）

# 經典用途
echo "Hello World" | tr 'A-Z' 'a-z'   # 轉小寫（normalize）
cat file | tr -s '\n'                 # 壓縮連續空行
echo $PATH | tr ':' '\n'              # 把 PATH 的 : 換成換行（每個路徑一行，方便看）
```

> **tr 是「字元級」工具，和 sed/awk 的「字串/欄位級」不同**。`tr 'a-z' 'A-Z'` 是逐**字元**映射（a→A, b→B…），不是字串替換——它沒有 regex，只做字元集合的對應、刪除、壓縮。專長：大小寫轉換（`tr 'A-Z' 'a-z'`）、刪特定字元（`tr -d '\r'` 去 Windows 換行）、壓縮重複（`tr -s ' '` 多空白變一個）、補集刪除（`tr -cd '0-9'` 只留數字）。`echo $PATH | tr ':' '\n'`（把 PATH 拆成每行一個路徑）是好用的小技巧。tr 不能做的：多字元字串替換（用 sed）、欄位操作（用 awk/cut）。它快又簡單，是管線裡做「字元清理/正規化」的零件。

## paste / join：合併（管線的反操作）

```bash
# paste：把多個檔案「並排」合併（按行）
paste file1 file2                # file1 和 file2 同行並排（tab 分隔）
paste -d, file1 file2            # 用逗號分隔
paste -s file                    # -s：把多行併成一行（serial）
seq 1 6 | paste - - -            # 把 stdin 每 3 個併一行（- 重複讀 stdin）

# join：按「共同欄位」關聯兩個檔案（像 SQL join）
# 前提：兩檔案都要按 join 欄位「已排序」
join file1 file2                 # 按第 1 欄關聯（兩檔都要先 sort）
join -t: -1 1 -2 1 a.txt b.txt   # -t 分隔符，-1/-2 指定各檔的 join 欄位

# 範例：join 兩個檔案
# users.txt:  1 alice / 2 bob
# scores.txt: 1 90 / 2 85
join users.txt scores.txt        # 1 alice 90 / 2 bob 85（按第 1 欄關聯）
```

> **paste（並排）和 join（關聯）是「合併」工具，和管線的「逐行流」不同**。paste 把多個檔案**同行並排**（`paste a b` = a 的第 1 行 + b 的第 1 行並排），或用 `-s` 把多行併成一行。`seq 1 6 | paste - - -`（每 3 個併一行）是把長串資料重排的技巧。join 像 SQL 的 join——按**共同欄位**關聯兩個檔案（`join users.txt scores.txt` 按第 1 欄把 user 和 score 配對），但**前提是兩檔案都要先按 join 欄位排序**（join 也用相鄰比較，像 uniq）。實務上 awk 的關聯陣列 join（Ch 26 的 `NR==FNR`）更靈活（不用先排序），但 join 對「已排序的大檔案」更省記憶體。這些工具補全了文字處理：grep/sed/awk 處理「流」，paste/join 處理「合併多個來源」。

## 故意弄壞：字典序排序的陷阱

```bash
cd ~/cmdlab
# 經典陷阱：忘記 -n，數字按字典序亂掉
echo -e "file10\nfile2\nfile1\nfile20" | sort
# file1 / file10 / file2 / file20    ← file10 排在 file2 前面（字典序）
echo -e "file10\nfile2\nfile1\nfile20" | sort -V
# file1 / file2 / file10 / file20    ← -V（版本排序）懂「檔名裡的數字」

# uniq 沒先 sort 的陷阱
echo -e "apple\nbanana\napple\ncherry\napple" | uniq -c
#   1 apple / 1 banana / 1 apple / 1 cherry / 1 apple   ← 沒去重！（不相鄰）
echo -e "apple\nbanana\napple\ncherry\napple" | sort | uniq -c
#   3 apple / 1 banana / 1 cherry   ← 對了（先 sort）

# cut 多空白的陷阱
echo "alice    engineer" | cut -d' ' -f2     # 空字串！（多空白）
echo "alice    engineer" | awk '{print $2}'  # engineer（awk 對）
```

> **`sort -V`（版本排序）是處理「檔名含數字」的神器**。`file2 file10 file1` 用 `sort` 給 `file1 file10 file2`（字典序，file10 在 file2 前），但 `sort -V` 給 `file1 file2 file10`——它理解「檔名裡嵌的數字」，按版本/自然順序排。這對排序版本號（`v1.9` < `v1.10`）、帶編號的檔名（`log1 log2 ... log10`）超有用。配合本章開頭的「數字要 -n」，記住三種排序：純數字用 `-n`、帶單位（K/M/G）用 `-h`、檔名含數字用 `-V`。選錯會得到反直覺的順序，是 SysOps 常見的小坑。

## 動手練習

1. 排序陷阱：對 `10 2 1 20` 跑 `sort` vs `sort -n`，對檔名 `file1 file10 file2` 跑 `sort` vs `sort -V`，理解三種順序

2. uniq 必須先 sort：對有不相鄰重複的資料跑 `uniq -c`（錯）vs `sort | uniq -c`（對）

3. cut vs awk：對多空白分隔的資料（如 `ps aux` 輸出）用 cut（會錯位）vs awk（對），理解差別

4. tr 清理：用 `tr -d '\r'` 去 Windows 換行、`tr 'A-Z' 'a-z'` 轉小寫、`echo $PATH | tr ':' '\n'` 拆路徑

5. 組合管線：用 `提取 | sort | uniq -c | sort -rn | head` 對任何 log 做頻率分析（為練習 C 暖身）

## 本章重點整理

- sort：預設字典序，數字用 `-n`、帶單位 `-h`、檔名數字 `-V`；`-k` 按欄、`-t` 分隔符；外部排序能處理比記憶體大的檔案
- uniq：只去「相鄰」重複，幾乎總要先 sort；`-c` 計數是 log 分析骨架的核心
- cut：嚴格按單一字元分隔（CSV、passwd 用）；awk 把連續空白當一個（對齊資料用）
- tr：字元級轉換/刪除/壓縮（大小寫、去 \r、壓空白），沒有 regex
- paste（並排合併）、join（按共同欄位關聯，需先排序）；萬用骨架 `提取 | sort | uniq -c | sort -rn`

## 自我檢核

- [ ] 知道 sort 預設是字典序，何時用 -n/-h/-V
- [ ] 能解釋 uniq 為什麼必須先 sort（只比相鄰行）
- [ ] 知道 cut 和 awk 切欄位的差別（單字元 vs 連續空白），何時用哪個
- [ ] 會用 tr 做字元級的轉換、刪除、壓縮
- [ ] 能組出 `提取 | sort | uniq -c | sort -rn` 的頻率分析管線

## 延伸閱讀

### 官方文件

- **[GNU coreutils manual](https://www.gnu.org/software/coreutils/manual/coreutils.html)** — GNU
  - **讀哪裡**：sort、uniq、cut、tr、join、paste 各自的章節（在「Operating on sorted files」和「Operating on fields」分類下）
  - **為什麼值得讀**：這些工具所有選項的權威來源；sort 的 -k 語法（複雜的多級排序）這裡講得最清楚

### 書籍

- **《Classic Shell Scripting》— Ch 4-5** — Robbins & Beebe（O'Reilly）
  - **讀哪幾章**：Ch 4（文字處理工具）、Ch 5（管線組合）
  - **這本書的定位**：把這些小工具組合成真實任務的範例集，承接 Ch 21 的管線哲學
  - **前提**：Ch 21-27

- **《Data Science at the Command Line》— Ch 5, 7** — Jeroen Janssens（O'Reilly, 免費線上）
  - **讀哪幾章**：Ch 5（清理資料：tr/sort/uniq/cut）、Ch 7（探索資料）
  - **這本書的定位**：把這些工具用在資料分析，展示命令列做資料科學的威力
  - **線上版**：[datascienceatthecommandline.com](https://www.datascienceatthecommandline.com/)

### 文章

- **[Command-line tools can be 235x faster than your Hadoop cluster](https://adamdrake.com/command-line-tools-can-be-235x-faster-than-your-hadoop-cluster.html)** — Adam Drake
  - **這篇說什麼**：用 grep/awk/sort 處理資料，比 Hadoop 叢集快 235 倍的真實案例
  - **為什麼值得讀**：震撼地展示這些「小工具」配合管線的真實威力，是 Ch 21 哲學的最佳實證

→ [練習 C：log 分析管線](./practice-c-log-analysis.md)
