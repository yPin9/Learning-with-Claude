# Ch 24 — grep

> **目標**：掌握 grep（全域正則列印，Global Regular Expression Print）——它的核心選項（-i/-v/-n/-r/-o/-c/-E/-P）、context 選項（-A/-B/-C）、它怎麼用 DFA 引擎做到處理 GB 級 log 還飛快、和 ripgrep/ag 等現代替代品的差異。grep 是你每天用最多次的工具，這章把它從「-i 忽略大小寫」挖到「它為什麼這麼快」。

> **環境**：GNU grep 3.x（Linux）。BSD grep（macOS）部分選項不同會標注。

## 為什麼 grep 值得專門一章？

grep 大概是你每天打最多次的命令。但多數人只會 `grep something file`。grep 其實有一整套選項能做精準搜尋、顯示上下文、遞迴整個專案、反向篩選——這些是日常效率的關鍵。

更深一層：grep **為什麼這麼快**？它能在幾百 MB 的 log 裡瞬間找到東西，靠的是 Ch 23 講的 DFA 引擎 + 一些精巧的優化（Boyer-Moore、避免逐字元比對）。理解它，你會知道什麼時候 grep 夠用、什麼時候該換 ripgrep。grep 的名字本身就是個歷史——它來自 ed 編輯器的 `g/re/p` 命令。

## 先建立直覺：grep 是一台「篩選機」

```
grep：逐行讀輸入，印出「匹配 pattern」的行

  輸入（stdin 或檔案）       grep 'error'         輸出
  ┌──────────────┐                              ┌──────────────┐
  │ info: started │ ─────▶ 每行問：             │              │
  │ error: oops   │        「含 error 嗎?」 ───▶│ error: oops  │
  │ info: running │                              │ error: bad   │
  │ error: bad    │                              └──────────────┘
  └──────────────┘         匹配的行才輸出
        │
  grep = 行的篩選器（filter）
  預設：印出「整行」（只要該行任一處匹配 pattern）
        │
  → 它是 Ch 21 管線哲學的典型「filter」：吃文字、吐文字
```

關鍵心智：grep 逐行讀輸入，對每行問「這行匹配 pattern 嗎？」，匹配的整行輸出。它是管線哲學的典型 filter。預設匹配「行內任一處」（不是整行），輸出「整行」（不是匹配的部分，除非 -o）。

> grep 完全建立在 Ch 23 的 regex 上。如果你對 BRE/ERE 方言、貪婪匹配還不熟，先回看 [Ch 23 — 正規表示式](./23-regex.md)。grep 預設用 BRE，`-E` 用 ERE，`-P` 用 PCRE。

## 核心選項：日常 80% 靠這些

```bash
# 基本
grep 'pattern' file              # 印含 pattern 的行
grep 'pattern' file1 file2       # 多檔案（會標檔名）
command | grep 'pattern'         # 從 stdin（管線）

# 最常用選項
grep -i 'error' log              # -i：忽略大小寫（Error/ERROR/error 都匹配）
grep -v 'debug' log              # -v：反向（印「不」含 debug 的行）
grep -n 'error' log              # -n：顯示行號
grep -c 'error' log              # -c：只印「匹配的行數」（計數）
grep -o 'error' log              # -o：只印匹配的「部分」（不是整行）
grep -w 'cat' file               # -w：詞匹配（cat 不匹配 category，等於 \bcat\b）
grep -l 'error' *.log            # -l：只印「有匹配的檔名」（不印內容）
grep -L 'error' *.log            # -L：只印「沒有匹配的檔名」

# 方言
grep -E 'a+|b+' file             # -E：ERE（+ | () 直接用）
grep -P '\d+' file               # -P：PCRE（\d \b lookahead）
grep -F 'a.b.c' file             # -F：固定字串（不當 regex，. 是字面點，也最快）
```

```bash
# 組合是 grep 的威力所在
grep -in 'error' log             # 忽略大小寫 + 顯示行號
grep -rn 'TODO' src/             # 遞迴搜尋 src/ 下所有檔案 + 行號（找專案裡的 TODO）
grep -v '^#' config | grep -v '^$'   # 去掉註解行和空行（看「有效」設定）
ps aux | grep -v grep | grep nginx   # 找 nginx process，排除 grep 自己（經典）
```

> **`-F`（固定字串）是被低估的選項**。當你要找的是「字面字串」（不是 regex），用 `grep -F` 或 `fgrep`——它不把輸入當 regex，所以 `.` `*` `[` 都是字面字元（不用跳脫），而且**最快**（不用編譯 regex 狀態機，直接字串比對）。找一個含很多特殊字元的字串（如 IP `192.168.1.1`、程式碼 `arr[i]`）時，`grep -F '192.168.1.1'` 比 `grep '192\.168\.1\.1'` 又簡單又快。判斷：你要找的是「模式」還是「確切字串」？確切字串就 `-F`。

## context 選項：看匹配行的前後

debug log 時，光看匹配行不夠——你要看它前後發生什麼：

```bash
# context 選項（顯示匹配行的上下文）
grep -A 3 'error' log            # -A：匹配行 + 後面 3 行（After）
grep -B 3 'error' log            # -B：匹配行 + 前面 3 行（Before）
grep -C 3 'error' log            # -C：匹配行 + 前後各 3 行（Context）

# 實戰：看 error 發生時前後的脈絡
grep -B 2 -A 5 'Exception' app.log
#   Exception 前 2 行（什麼觸發的）+ 後 5 行（stack trace）

# 多個匹配之間用 -- 分隔
grep -A 2 'error' log
# error: first
# context1
# context2
# --                              ← 分隔不同的匹配區塊
# error: second
# ...
```

> **`-A`/`-B`/`-C`（context）是 debug log 的必備技能**。光看「error」那一行通常不夠——你需要它**前面**（什麼導致了 error）和**後面**（error 的後果、stack trace）。`grep -B 2 -A 5 'Exception' app.log` 給你 Exception 前 2 行和後 5 行，一眼看到完整脈絡。記法：**A**fter（後）、**B**efore（前）、**C**ontext（前後都要）。這是把 grep 從「找到那一行」升級到「理解那一行的處境」的關鍵，是實際排障時用最多的 grep 功能之一。

## 底層機制：grep 為什麼這麼快

grep 能在大檔案裡瞬間搜尋，背後有真功夫：

```
grep 的速度來源：

  1. DFA 引擎（Ch 23）：
     regex 編譯成確定狀態機，每個字元只看一次
     O(n) 線性，永不回溯 → 處理 GB 檔案也穩定快
        │
  2. Boyer-Moore 演算法（找固定字串時）：
     不逐字元比對，而是「跳躍」
     找 "hello" 時，先看第 5 個字元，不是 'o' 就跳 5 格
     → 平均只看 n/m 個字元（m = pattern 長度），次線性！
        │
  3. 避免不必要的工作：
     - 用 memchr（SIMD 加速）快速掃換行符切行
     - 沒有特殊字元時走 Boyer-Moore 快路徑
     - 用 mmap 或大塊讀取，減少 syscall
        │
  → GNU grep 作者 Mike Haertel 的名言：
    「grep 快是因為它『盡量不看』每個位元組」
```

```bash
# 驗證 grep 的速度（在大檔案上）
# 生成一個大檔案
yes "the quick brown fox jumps over the lazy dog" | head -5000000 > big.txt
ls -lh big.txt                   # ~200 MB

time grep -c 'fox' big.txt       # 數百毫秒搜完 200 MB（DFA + Boyer-Moore）
time grep -F 'fox' big.txt > /dev/null   # -F 更快（純字串，Boyer-Moore 快路徑）

# 對比：用 grep -P（PCRE/NFA）通常較慢
time grep -P 'fox' big.txt > /dev/null
```

> **grep 的速度哲學是「盡量不看每個位元組」**。GNU grep 作者 Mike Haertel 在一封著名郵件裡解釋：grep 快，不是因為它看得快，而是因為它**跳過**大量資料。找固定字串時用 **Boyer-Moore** 演算法——從 pattern 尾端比對，不匹配就「跳躍」整個 pattern 長度，平均只看 n/m 個字元（次線性！）。配合 DFA（Ch 23，線性、不回溯）和 SIMD 加速的換行掃描，grep 處理 GB 級 log 仍然飛快。這解釋了為什麼 `grep -F`（固定字串）最快——它走純 Boyer-Moore 快路徑。理解這個，你會知道：grep 的瓶頸通常是磁碟 I/O 不是 CPU，而 `grep -P`（NFA）會放棄這些優化。

## 對比：grep vs ripgrep vs ag

現代有更快的替代品，知道何時換工具：

| 工具 | 特點 | 適用 |
|---|---|---|
| `grep` | 無所不在、穩定、DFA | 任何 Unix、管線、腳本 |
| `grep -r` | 遞迴，但會搜所有檔案 | 小專案遞迴搜尋 |
| `rg`（ripgrep）| 極快、自動跳過 .gitignore/二進位、預設遞迴 | 大型程式碼庫搜尋 |
| `ag`（the silver searcher）| 類似 rg，較早出現 | 同 rg（rg 通常更快）|
| `ack` | Perl 寫的，程式碼搜尋導向 | 較舊，現多被 rg 取代 |

```bash
# grep 遞迴 vs ripgrep
grep -rn 'TODO' .                # grep：搜所有檔案（含 .git、node_modules、二進位）
rg 'TODO'                        # ripgrep：自動跳過 .gitignore 內容和二進位，預設遞迴，更快

# 為什麼 rg 在程式碼庫快很多：
#   1. 自動讀 .gitignore，跳過 node_modules/.git/build 等
#   2. 平行搜尋多個檔案（多執行緒）
#   3. 自動偵測並跳過二進位檔
#   4. 用 Rust 的 regex crate（DFA，類似 grep 的線性保證）

# 但 grep 仍不可取代：
#   - 一定存在（rg 要另外裝）
#   - 管線中處理 stdin（rg 也行但 grep 更通用）
#   - 腳本可移植性（grep 是 POSIX 標準）
```

> **ripgrep（`rg`）在程式碼庫搜尋上完勝 grep，但 grep 不可取代**。`rg` 快的原因不只是 Rust——它**自動跳過** `.gitignore` 裡的東西（node_modules、build、.git）、跳過二進位檔、多執行緒平行搜尋。在大型專案裡 `rg 'foo'` 可能比 `grep -rn 'foo' .` 快 10 倍以上（因為 grep 傻傻地搜了 node_modules 和 .git）。但 grep 仍是基礎：它**無所不在**（POSIX 標準，任何 Unix 都有，rg 要另裝）、腳本可移植、管線通用。經驗：**互動式搜程式碼用 `rg`，腳本和管線用 `grep`**。兩者的 regex 引擎都是 DFA（線性保證），所以都不會被 catastrophic backtracking 拖垮。

## 故意弄壞：常見 grep 陷阱

```bash
cd ~/cmdlab
# 陷阱 1：忘記跳脫特殊字元
echo "price: \$5.00" > prices.txt
grep '$5.00' prices.txt          # 可能不匹配！$ 是「行尾」錨點，. 是任意字元
grep -F '$5.00' prices.txt       # 對：-F 把它當字面字串
grep '\$5\.00' prices.txt        # 對：跳脫 $ 和 .

# 陷阱 2：grep 自己出現在結果裡
ps aux | grep nginx              # 結果包含「grep nginx」這個 process 自己！
ps aux | grep nginx | grep -v grep   # 排除 grep 自己（傳統做法）
ps aux | grep '[n]ginx'          # 更巧妙：[n]ginx 匹配 nginx 但 pattern 本身是 "[n]ginx" 不匹配自己
pgrep nginx                      # 最佳：pgrep 不會有這問題（Ch 16）

# 陷阱 3：-o 配多匹配
echo "a1b2c3" | grep -o '[0-9]'  # 1\n2\n3（每個匹配一行，不是整行）

# 陷阱 4：二進位檔案
grep 'text' /bin/ls              # "Binary file /bin/ls matches"（不印內容）
grep -a 'text' /bin/ls           # -a：當文字處理（強制印出）
```

> **`grep '[n]ginx'` 是排除 grep 自己的優雅技巧**。`ps aux | grep nginx` 的結果總是包含 `grep nginx` 這個 process 本身（因為它的命令列含 "nginx"）。傳統解法 `| grep -v grep` 多一個 stage。優雅解法 `grep '[n]ginx'`——這個 regex 匹配字串 `nginx`（`[n]` 就是 `n`），但 grep 自己的命令列是 `grep [n]ginx`（含中括號），**不**匹配 pattern `[n]ginx`（因為實際命令列裡的 "nginx" 前面沒有中括號字面字元……更精確說：pattern `[n]ginx` 不會匹配字串 `[n]ginx`）。最乾淨的方法還是 `pgrep nginx`（Ch 16）——它根本不掃自己。但 `[n]ginx` 技巧在沒有 pgrep 時很有用，也是面試常考的小聰明。

## 進階：grep 的實用組合

```bash
# 只印匹配部分 + 統計（提取 + 計數）
grep -oE '[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' access.log | sort | uniq -c | sort -rn
#   提取所有 IP → 計數 → 排序（Ch 21 的管線思維）

# 多 pattern（-e 或 -E 的 |）
grep -e 'error' -e 'warning' log         # 含 error 或 warning
grep -E 'error|warning|critical' log     # 同上，用 ERE

# 從檔案讀 pattern（-f）
grep -f patterns.txt log                 # patterns.txt 每行一個 pattern

# 反向 + context（看 error 但排除已知的）
grep 'error' log | grep -v 'known_harmless_error'

# 計算多檔案各自的匹配數
grep -c 'error' *.log                    # 每個檔案印 "檔名:數量"

# 只要第一個匹配就停（大檔案加速）
grep -m1 'pattern' huge.log              # -m1：找到 1 個就停（不掃完整個檔案）

# 顯示匹配在第幾個 byte（罕見但有用）
grep -b 'pattern' file                   # -b：byte offset
```

> **`grep -oE '...' | sort | uniq -c | sort -rn` 是 log 分析的萬用骨架**。`-o`（只印匹配部分）把 grep 從「篩選行」變成「提取資料」——配合 regex 提取 IP、URL、錯誤碼等，再用 `sort | uniq -c | sort -rn`（Ch 27）統計頻率。這個組合能回答無數問題：「最常見的錯誤是什麼」「哪個 IP 訪問最多」「哪個 URL 最熱門」。`-m1`（找到一個就停）在超大檔案裡確認「有沒有」時能大幅加速（不用掃完）。這些組合是練習 C（log 分析）的核心，也是 SysOps 日常。

## 動手練習

1. context 練習：找一個有錯誤的 log，用 `grep -B 2 -A 5 'error'` 看完整脈絡，體會比單純 `grep error` 多了什麼

2. 速度感受：生成大檔案（`yes ... | head -5000000 > big.txt`），`time grep` vs `time grep -F` vs `time grep -P`，比較三種引擎

3. 跑「故意弄壞」：`ps aux | grep ssh`（看到 grep 自己）vs `ps aux | grep '[s]sh'`（沒有 grep 自己），理解中括號技巧

4. 提取 + 統計：用 `grep -oE` 從任何 log 提取一種資料（IP/數字/單詞），接 `sort | uniq -c | sort -rn` 統計

5. 比較 rg：如果裝了 ripgrep，在一個 git 專案裡 `time grep -rn foo .` vs `time rg foo`，看速度差

## 本章重點整理

- grep 逐行篩選：對每行問「匹配嗎」，匹配的整行輸出；是管線哲學的典型 filter
- 核心選項：-i（忽略大小寫）、-v（反向）、-n（行號）、-o（只印匹配部分）、-c（計數）、-w（詞）、-r（遞迴）、-F（固定字串最快）、-E/-P（方言）
- context 選項 -A/-B/-C 是 debug log 的必備（看匹配行前後脈絡）
- grep 快的原因：DFA 引擎（線性）+ Boyer-Moore（跳躍式比對）+「盡量不看每個位元組」
- 現代替代 ripgrep（rg）在程式碼庫更快（跳過 .gitignore、多執行緒），但 grep 無所不在、腳本通用

## 自我檢核

- [ ] 能熟練組合 grep 選項（如 -in、-rn、-v + 管線）解決實際搜尋
- [ ] 知道什麼時候用 -F（固定字串）、-E（ERE）、-P（PCRE）
- [ ] 會用 -A/-B/-C 看 log 的上下文
- [ ] 能解釋 grep 為什麼快（DFA + Boyer-Moore）
- [ ] 知道何時該換 ripgrep，以及 grep 為什麼仍不可取代

## 延伸閱讀

### 必讀文章

- **[why GNU grep is fast](https://lists.freebsd.org/pipermail/freebsd-current/2010-August/019310.html)** — Mike Haertel（GNU grep 作者，2010）
  - **核心貢獻**：grep 作者親自解釋 grep 為什麼快——Boyer-Moore、避免讀每個 byte、用 mmap。短短一封郵件，是理解 grep 效能的第一手資料
  - **讀哪裡**：整封（很短）
  - **和本章的關聯**：本章「grep 為什麼快」那節的原始來源

- **[ripgrep is faster than grep, ag, git grep, ...](https://blog.burntsushi.net/ripgrep/)** — Andrew Gallant（ripgrep 作者，2016）
  - **這篇說什麼**：ripgrep 作者詳述各種搜尋工具的效能比較和 rg 的設計
  - **讀哪裡**：開頭的 benchmark 表 + "How does ripgrep work?" 段
  - **為什麼值得讀**：理解現代搜尋工具的工程細節，以及 grep 的優化空間在哪

### 官方文件

- **[GNU grep manual](https://www.gnu.org/software/grep/manual/grep.html)** — GNU
  - **讀哪裡**：Invoking grep（所有選項）+ Regular Expressions（BRE/ERE）
  - **為什麼值得讀**：所有選項的權威說明；遇到行為不符預期時的仲裁

### 書籍

- **《grep Pocket Reference》** — John Bambenek & Agnieszka Klus（O'Reilly）
  - **這本書的定位**：grep 的口袋速查，把選項和 regex 組合整理成可快速查閱的格式
  - **讀哪幾章**：適合放手邊查，不用從頭讀

→ [Ch 25 sed](./25-sed.md)
