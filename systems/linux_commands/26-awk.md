# Ch 26 — awk

> **目標**：掌握 awk——它其實是一個**完整的程式語言**（不只是命令），專為「逐行處理欄位化文字」設計。理解它的 `pattern { action }` 模型、自動切欄位（$1 $2 $NF）、BEGIN/END 區塊、變數與算術、它在「sed 和寫程式之間」的甜蜜點。awk 是文字處理的最強工具，學會它你能一行命令做完別人寫 50 行 Python 的事。

> **環境**：GNU awk（gawk）4.x（Linux）。POSIX awk / mawk 部分功能差異會標注。

## 為什麼 awk 是文字處理的頂點？

grep 能篩選（不能改），sed 能替換（難做欄位和計算）。但很多任務是「**對每行的某些欄位做計算或重組**」——算第 3 欄的總和、印出 user 是 root 的行的第 1 和第 5 欄、統計每個狀態碼出現幾次。這些 grep/sed 都笨拙，awk 是為它們而生。

awk 是一個**完整的程式語言**（圖靈完備、有變數、陣列、函式、控制流），但它的設計讓「逐行處理欄位化文字」變得極簡——你不用寫讀檔迴圈、不用手動切欄位，awk 自動做。它的名字來自三位作者（Aho、Weinberger、Kernighan）。理解 awk，你的文字處理能力會質變——它是 sed 和「寫完整程式」之間的完美中間點。

## 先建立直覺：awk 是「對每行跑的小程式」

```
awk：對每一行，自動切成欄位，跑你的小程式

  輸入每一行：  "alice  25  engineer"
                  ↓ awk 自動切欄位（預設按空白）
                $1="alice"  $2="25"  $3="engineer"
                $0 = 整行    NF = 欄位數(3)
                  ↓ 跑你的程式
                { print $1, $3 }   →  "alice engineer"
        │
  awk 程式的結構：  pattern { action }
    pattern：哪些行要處理（像 grep 的條件）
    action：對這些行做什麼（像一段程式）
        │
  對每一行：若符合 pattern，執行 action
        │
  → awk = 自動切欄位 + 對每行跑 pattern{action}
    你只寫「做什麼」，讀檔/切欄位/迴圈 awk 全包了
```

關鍵心智：awk 對每一行自動切成欄位（`$1`、`$2`…，`$0` 是整行，`NF` 是欄位數），然後跑你的 `pattern { action }` 程式——符合 pattern 的行才執行 action。你不用寫讀檔迴圈、不用手動切欄位，這些 awk 自動做。這個「自動欄位化 + 隱含逐行迴圈」是 awk 簡潔的根源。

> awk 的 pattern 部分可以用 regex（Ch 23）。如果對 regex 不熟先回看 [Ch 23](./23-regex.md)。但 awk 比 grep/sed 強的地方在 action——它是完整的程式語言。

## 核心：欄位與 print

awk 最常見的用途是提取欄位：

```bash
# 自動切欄位（預設按空白/tab 分隔）
echo "alice 25 engineer" | awk '{print $1}'       # alice（第 1 欄）
echo "alice 25 engineer" | awk '{print $3}'       # engineer
echo "alice 25 engineer" | awk '{print $1, $3}'   # alice engineer（逗號 = 空白分隔）
echo "alice 25 engineer" | awk '{print $NF}'      # engineer（NF = 欄位數，$NF = 最後一欄）
echo "alice 25 engineer" | awk '{print NF}'       # 3（欄位數量）
echo "alice 25 engineer" | awk '{print $0}'       # alice 25 engineer（整行）

# 實戰：從 ps 提取 PID（第 2 欄）
ps aux | awk '{print $2}'                          # 所有 PID
ps aux | awk 'NR>1 {print $2}'                     # 跳過標題行（NR = 行號，NR>1 = 非第一行）

# 改分隔符（-F）
echo "alice:25:engineer" | awk -F: '{print $1}'    # alice（用 : 切欄位）
awk -F: '{print $1, $7}' /etc/passwd               # 印 username 和 shell（passwd 用 : 分隔）
awk -F',' '{print $2}' data.csv                    # CSV 第 2 欄
```

```
awk 的內建變數（自動維護）：
  $0      整行
  $1,$2   第 1、2 欄
  $NF     最後一欄（NF = Number of Fields）
  NF      當前行的欄位數
  NR      當前行號（Number of Records，跨檔案累計）
  FNR     當前檔案內的行號（多檔案時 reset）
  FS      欄位分隔符（Field Separator，預設空白；-F 設定）
  OFS     輸出欄位分隔符（print 用逗號時的分隔，預設空白）
  RS/ORS  記錄分隔符（預設換行）
```

> **`$1 $2 $NF` 自動切欄位是 awk 最常用、最值得肌肉記憶的功能**。`ps aux | awk '{print $2}'` 提取 PID、`awk -F: '{print $1}' /etc/passwd` 提取 username——比 `cut`（Ch 27）靈活（awk 自動處理多個連續空白，cut 不會），比 sed 的反向引用清楚太多。`$NF`（最後一欄）和 `NF`（欄位數）特別有用——當你不知道有幾欄、只要最後一欄時。`NR`（行號）讓你能 `NR>1` 跳過標題、`NR==5` 只處理第 5 行。記住 `-F` 改分隔符（`-F:` `-F,` `-F'\t'`），這是處理 CSV、/etc/passwd、log 的關鍵。

## pattern { action }：選擇性處理

awk 的威力在 pattern——它決定「哪些行要處理」：

```bash
# pattern：只處理符合的行
awk '/error/' log                          # 只印含 error 的行（沒 action = 印整行，等於 grep）
awk '/error/ {print $1}' log               # 含 error 的行，印第 1 欄
awk '$3 > 100' data                        # 第 3 欄 > 100 的行（數值比較！）
awk '$1 == "alice"' data                   # 第 1 欄等於 alice 的行
awk 'NF > 5' data                          # 欄位數 > 5 的行
awk 'NR % 2 == 0' data                     # 偶數行（NR 行號取模）
awk 'length($0) > 80' file                 # 超過 80 字元的行

# pattern 可以是複雜條件
awk '$3 > 100 && $4 == "active"' data      # 多條件（&&、||、!）
awk -F: '$3 >= 1000 {print $1}' /etc/passwd   # UID >= 1000 的使用者名（一般使用者）

# pattern 是範圍（像 sed 的 /start/,/end/）
awk '/START/,/END/' file                   # START 到 END 之間的行

# 沒有 pattern = 對每行都執行 action
awk '{print NR, $0}' file                  # 每行加上行號（像 cat -n）
```

> **awk 的 `pattern` 能做 grep 做不到的「數值和欄位條件」**。grep 只能匹配文字模式，但 awk 能 `$3 > 100`（第 3 欄數值大於 100）、`NF > 5`（欄位數）、`$1 == "alice" && $3 > 50`（多欄位複合條件）。這是因為 awk **理解欄位和型別**——它知道 `$3` 是個可以比較大小的值，不只是字串。`awk -F: '$3 >= 1000 {print $1}' /etc/passwd`（印 UID ≥ 1000 的使用者）這種「根據某欄的數值篩選並提取另一欄」是 awk 的招牌動作，grep+cut+sed 組合起來都很笨拙。pattern 為空就對每行執行 action，action 為空就印整行（等於 grep）。

## BEGIN / END：前置與收尾

BEGIN 和 END 區塊讓 awk 能做「處理前」和「處理後」的工作——這是統計計算的關鍵：

```bash
# BEGIN：處理任何行之前執行一次（初始化）
# END：處理完所有行之後執行一次（收尾、印總結）

# 經典：求和
seq 1 100 | awk '{sum += $1} END {print sum}'      # 5050（累加每行，END 印總和）
#   每行：sum += $1（累加）
#   END：印最後的 sum

# 求平均
awk '{sum += $1; count++} END {print sum/count}' numbers

# BEGIN 設定 + END 總結
ps aux | awk 'BEGIN {print "PID\tMEM"} {print $2"\t"$4} END {print "Total:", NR-1}'

# 計數（統計每個值出現幾次）—— awk 的殺手鐧（用陣列）
awk '{count[$1]++} END {for (ip in count) print count[ip], ip}' access.log
#   count[$1]++：以第 1 欄為 key，計數（關聯陣列）
#   END：印出每個 key 和它的次數
#   這一行 = grep + sort + uniq -c 的功能，但更靈活

# 算每個使用者用哪個 shell（統計）
awk -F: '{shells[$7]++} END {for (s in shells) print shells[s], s}' /etc/passwd
```

```
awk 程式的完整結構：

  BEGIN { ... }        ← 處理前執行一次（初始化變數、印標題）
  pattern1 { action1 } ← 對每行：符合 pattern1 就執行
  pattern2 { action2 } ← 可以有多組
  END { ... }          ← 處理後執行一次（印統計、總結）
        │
  執行流程：
    1. 跑 BEGIN
    2. 對輸入每一行：依序檢查每個 pattern，符合就執行對應 action
    3. 跑 END
```

> **BEGIN/END + 關聯陣列讓 awk 能做「統計」——這是它超越 sed/grep 的關鍵**。`{count[$1]++} END {for (k in count) print count[k], k}` 這一行就是「統計每個值出現幾次」——以 `$1` 為 key 累加，最後印出。這取代了 `sort | uniq -c`，而且**更靈活**（key 可以是任意欄位組合、可以邊算邊做其他事）。`{sum += $3} END {print sum}` 算總和、`{sum+=$1; n++} END {print sum/n}` 算平均——awk 的變數預設初始化為 0/空字串，所以直接 `+=` 就能累加。**關聯陣列**（`count["key"]`，key 是字串）是 awk 最強大的資料結構，能做分組統計、去重、查找。這些是練習 C（log 分析）的核心武器。

## awk 是完整語言：變數、控制流、函式

awk 不只 print——它有完整的程式語言特性：

```bash
# 變數與算術
echo "10 20 30" | awk '{print $1 + $2 + $3}'       # 60
echo "5" | awk '{print $1 * $1}'                    # 25

# 字串函式
echo "hello world" | awk '{print toupper($0)}'      # HELLO WORLD
echo "hello" | awk '{print length($0)}'             # 5
echo "a,b,c" | awk '{n = split($0, arr, ","); print arr[2]}'   # b（split 切字串到陣列）
echo "hello world" | awk '{print substr($0, 1, 5)}' # hello（子字串）
echo "2024-01-15" | awk '{gsub(/-/, "/"); print}'   # 2024/01/15（gsub = 全域替換）

# 控制流（if/else、for、while）
seq 1 5 | awk '{if ($1 % 2 == 0) print $1, "even"; else print $1, "odd"}'
awk 'BEGIN {for (i=1; i<=5; i++) print i, i*i}'     # 印 1-5 的平方

# printf（格式化輸出，像 C）
awk 'BEGIN {printf "%-10s %5.2f\n", "price", 3.14159}'   # price      3.14

# 自訂函式（gawk）
awk 'function square(x) {return x*x} {print square($1)}' numbers

# 多檔案 + FNR（每個檔案的行號）
awk 'FNR==1 {print "=== " FILENAME " ==="} {print}' file1 file2   # 印檔名標頭
```

> **awk 是圖靈完備的程式語言，不是「命令」**。它有變數、陣列、`if/else`、`for/while`、自訂函式、字串函式（`length`、`substr`、`split`、`gsub`、`toupper`）、`printf`（像 C 的格式化）。這意味著很多你以為要寫 Python 的任務，awk 一行搞定。`awk '{gsub(/-/, "/"); print}'` 做替換（像 sed）、`split($0, arr, ",")` 切字串、`printf "%-10s %5.2f"` 格式化對齊輸出。**awk 的甜蜜點**：當任務是「逐行處理欄位化資料 + 一些計算/統計」，awk 比 sed 清楚（有真正的變數和控制流）、比 Python 簡潔（不用寫讀檔迴圈和切欄位）。經驗法則：**sed 做不了（要計算/欄位/狀態）但又還不到要寫完整程式的程度——用 awk**。

## 對比：grep vs sed vs awk

| 任務 | grep | sed | awk |
|---|---|---|---|
| 篩選行 | ✓ 最佳 | ✓ | ✓ |
| 替換文字 | ✗ | ✓ 最佳 | ✓（gsub）|
| 提取欄位 | △（-o）| △（難）| ✓ 最佳 |
| 數值比較/計算 | ✗ | ✗ | ✓ 最佳 |
| 統計/分組 | ✗ | ✗（極難）| ✓ 最佳 |
| 多行/狀態邏輯 | ✗ | △（hold space）| ✓ |
| 速度（純篩選）| ✓ 最快 | 中 | 中 |

```bash
# 同一個「提取第 2 欄」，三種工具
grep -oE '^\S+ (\S+)' file       # grep：笨拙（要 regex 抓欄位）
sed -E 's/^\S+ (\S+).*/\1/' file # sed：可以但難讀
awk '{print $2}' file            # awk：天生為此（最清楚）

# 「統計每個狀態碼出現幾次」—— 只有 awk 優雅
awk '{count[$9]++} END {for (c in count) print count[c], c}' access.log | sort -rn
# grep/sed 做不到（沒有變數和陣列）
```

> **grep/sed/awk 是文字處理的三層階梯，各有甜蜜點**。**grep**：純篩選（找含某模式的行），最快、最簡單。**sed**：逐行替換和刪除，簡單的 `s///` 編輯。**awk**：欄位提取、數值計算、統計分組——任何「理解欄位」或「需要計算/狀態」的任務。經驗：能用 grep 就 grep（最快），需要改內容用 sed，需要欄位/計算/統計用 awk。它們也常組合（`grep | awk`、`awk | sort`）。不要用 awk 做純篩選（殺雞用牛刀，grep 更快），也不要硬用 sed 做統計（幾乎不可能）。認得「這個任務是哪一層」是命令列功力的體現。

## 故意弄壞：常見 awk 陷阱

```bash
# 陷阱 1：欄位編號 vs 字串比較
echo "100" | awk '{if ($1 == 100) print "num"; else print "str"}'   # num（awk 自動判斷型別）
echo "100" | awk '{if ($1 == "100") print "eq"}'                     # eq（也對，字串比較）
echo "0100" | awk '{print $1 + 0}'                                   # 100（強制當數字 → 去掉前導 0）

# 陷阱 2：忘記 $ → 變數 vs 欄位
echo "5 10" | awk '{print $1}'   # 5（$1 = 第 1 欄）
echo "5 10" | awk '{print 1}'    # 1（沒有 $ = 字面數字 1，每行都印 1！）

# 陷阱 3：FS 改了但分隔的是多字元
echo "a::b" | awk -F: '{print $2}'   # 空（兩個 : 之間是空欄位）
echo "a  b" | awk '{print $2}'       # b（預設 FS：多個空白算一個分隔！）
echo "a  b" | awk -F' ' '{print $2}' # b（-F' ' 仍是「多空白算一個」的特殊行為）
echo "a::b" | awk -F'::' '{print $2}' # b（-F 可以是多字元/regex）

# 陷阱 4：print 的逗號 vs 無逗號
echo "a b" | awk '{print $1 $2}'     # ab（無逗號 = 連接，沒有分隔）
echo "a b" | awk '{print $1, $2}'    # a b（逗號 = OFS 分隔，預設空白）
```

> **「預設 FS 把多個空白當一個分隔」是 awk 的重要特殊行為**。`echo "a    b" | awk '{print $2}'` 給 `b`——awk 預設把連續空白/tab 當**單一**分隔符，並忽略行首尾空白。這通常是你要的（處理對齊的表格輸出如 `ps`、`ls -l` 很方便），但和 `cut`（Ch 27，嚴格按單一字元切）不同。要注意：用 `-F:`（明確分隔符）時，**連續分隔符會產生空欄位**（`a::b` 的 `$2` 是空）——這個和預設空白行為不一致，是 CSV 處理常見的坑。另一個經典錯誤：`print 1`（印字面數字 1）vs `print $1`（印第 1 欄）——忘記 `$` 是新手最常見的 bug。`print a, b`（逗號用 OFS 分隔）vs `print a b`（無逗號直接連接）也要分清。

## 進階：awk 的真實威力範例

```bash
# 1. 加總某欄（如算 du 的總和）
du -s * | awk '{sum += $1} END {print sum/1024 " MB"}'

# 2. 找出第 3 欄最大的行
awk '$3 > max {max = $3; line = $0} END {print line}' data

# 3. 兩檔案 join（用陣列記住第一個檔案）
awk 'NR==FNR {names[$1]=$2; next} {print $0, names[$1]}' names.txt data.txt
#   NR==FNR：還在第一個檔案時（建查找表）；next 跳過後續
#   第二個檔案：用第一檔的查找表補資料

# 4. 去重但保持順序（uniq 要先排序，awk 不用）
awk '!seen[$0]++' file           # 經典！第一次見的行印出，重複的不印
#   seen[$0]++：第一次是 0（!0 = true 印出），之後遞增（!非0 = false 不印）

# 5. 印出特定欄位範圍
awk '{for(i=3; i<=NF; i++) printf "%s ", $i; print ""}' file   # 印第 3 欄到最後

# 6. CSV 轉換 / 重組
awk -F',' 'BEGIN{OFS="\t"} {print $2, $1}' data.csv    # 交換前兩欄，輸出用 tab
```

> **`awk '!seen[$0]++'`（去重保序）是 awk 最精煉的慣用法之一**。它去除重複行但**保持原順序**——這是 `sort -u` 或 `uniq` 做不到的（它們需要先排序，會打亂順序）。原理：`seen[$0]` 第一次是 0（未定義變數預設 0），`!0` 為真所以印出，`++` 讓它變 1；之後再遇到同一行，`seen[$0]` 是 1（或更多），`!1` 為假所以不印。一個 9 字元的程式做了「保序去重」。同樣精妙的是 **awk 做兩檔案 join**（`NR==FNR` 判斷在第一個還是第二個檔案，用陣列當查找表）——這展示了 awk 能做關聯式的資料處理。這些慣用法是 awk 老手和新手的分水嶺，值得記住並理解原理。

## 動手練習

1. 欄位提取：`ps aux | awk '{print $2, $11}'`（PID + 命令），`awk -F: '{print $1}' /etc/passwd`（使用者），熟悉 $N 和 -F

2. 統計：用 `awk '{count[$1]++} END {for(k in count) print count[k], k}'` 統計任何 log 的某欄頻率，對照 sort|uniq -c

3. 計算：用 `seq 1 100 | awk '{s+=$1} END {print s}'` 求和，改成求平均、最大值，練 BEGIN/END

4. 去重保序：建一個有重複行的檔案，比較 `awk '!seen[$0]++'`（保序）vs `sort -u`（會排序）的差別

5. 跑「故意弄壞」：試 `print 1` vs `print $1`、多空白的欄位切割，理解 awk 的型別和 FS 行為

## 本章重點整理

- awk 是完整程式語言，專為「逐行處理欄位化文字」設計：自動切欄位（$1 $NF）、隱含逐行迴圈
- `pattern { action }`：符合 pattern 的行執行 action；pattern 能做數值/欄位條件（$3>100），grep 做不到
- 內建變數：$0（整行）、$1..$NF（欄位）、NF（欄位數）、NR（行號）、FS/OFS（分隔符）；-F 改輸入分隔符
- BEGIN/END + 關聯陣列是統計的關鍵：`{count[$1]++} END {...}` 做分組計數，取代並超越 sort|uniq -c
- 三層階梯：grep（篩選）< sed（替換）< awk（欄位/計算/統計）；認得任務屬於哪層

## 自我檢核

- [ ] 能用 awk 提取任意欄位（含 -F 改分隔符、$NF 最後一欄）
- [ ] 能寫 pattern 做數值和欄位條件篩選（$3 > 100 && ...）
- [ ] 會用 BEGIN/END 和關聯陣列做統計（分組計數、求和、平均）
- [ ] 知道 awk 是完整語言（變數、控制流、函式），能說出它相對 sed 的優勢
- [ ] 能在 grep/sed/awk 之間正確選擇

## 延伸閱讀

### 必讀書籍

- **《The AWK Programming Language》— 全書** — Aho, Kernighan, Weinberger（awk 作者親著，2nd ed 2023）
  - **讀哪幾章**：Ch 1-2（基礎和欄位）、Ch 3（統計和報表）、Ch 7（小語言）
  - **這本書的定位**：awk 的原典，三位作者（就是 a-w-k）親自寫。2023 第二版加了現代範例。薄但每頁都是精華
  - **前提**：本章 + 一點程式基礎

- **《sed & awk》— Part II (awk)** — Dougherty & Robbins（O'Reilly）
  - **讀哪幾章**：Ch 7-11（awk 的完整教學，從欄位到陣列到函式）
  - **這本書的定位**：比原典更循序漸進，適合當教材

### 官方文件

- **[GNU awk (gawk) manual](https://www.gnu.org/software/gawk/manual/gawk.html)** — GNU
  - **讀哪裡**：「Getting Started」+「Patterns and Actions」+「Arrays in awk」
  - **為什麼值得讀**：gawk 的權威文件，含 GNU 擴充（如 gensub、真正的多維陣列）；Arrays 那章對關聯陣列講得最清楚

### 文章

- **[awk one-liners explained](https://catonmat.net/awk-one-liners-explained-part-one)** — Peteris Krumins
  - **這篇說什麼**：逐一拆解著名的「awk one-liners」合集
  - **讀哪裡**：Part 1-3
  - **為什麼值得讀**：把 `!seen[$0]++` 這類精煉慣用法一句句講透，是從「會用」到「精通」的橋樑

→ [Ch 27 sort/uniq/cut/tr/join](./27-text-utils.md)
