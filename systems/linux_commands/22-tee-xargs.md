# Ch 22 — tee 與 xargs

> **目標**：掌握兩個讓管線更強大的關鍵工具——`tee`（管線分岔：同時存檔和往下傳）和 `xargs`（把 stdin 變成命令的參數）。重點在 xargs：理解「stdin 是資料、參數是另一回事」這個常被混淆的區別，以及為什麼 `find | xargs` 要小心檔名特殊字元。這是 Part 5 的收尾，連接「資料流」和「命令參數」兩個世界。

> **環境**：bash 5.x，GNU coreutils（tee）、findutils（xargs）。

## 為什麼需要 tee 和 xargs？

管線把資料從一個命令流到下一個。但有兩個常見需求管線本身做不到：

1. **「我想存一份又繼續處理」**——管線是線性的，資料流過就沒了。`tee` 解決：在管線中間分岔，一份存檔、一份繼續流。

2. **「這個命令不從 stdin 讀，它要參數」**——很多命令（rm、cp、kill、mkdir）從**命令列參數**拿輸入，不從 stdin。`echo file.txt | rm` 不會刪 file.txt（rm 不讀 stdin）。`xargs` 解決：把 stdin 的內容轉成命令的參數。

這兩個工具填補了「資料流世界」和「命令參數世界」之間的鴻溝。特別是 xargs，理解它你才真正打通管線和命令的任督二脈。

## tee：管線的分岔口

```
tee：把 stdin 同時寫到「檔案」和「stdout」（往下游）

  上游 ──▶ tee ──┬──▶ 檔案（存一份）
                 └──▶ stdout（繼續往下游流）
        │
  名字來自水管的「T 字接頭」（tee fitting）
  一進兩出
        │
  用途：在管線中間「偷看 / 存檔」而不打斷流動
```

```bash
# tee：存一份又繼續處理
ps aux | tee processes.txt | grep nginx
#   ps 的完整輸出存到 processes.txt（一份）
#   同時往下游給 grep（篩 nginx）
#   → 你既有完整 log，又得到篩選結果

# tee -a：追加（不覆蓋）
echo "new entry" | tee -a log.txt

# tee 寫多個檔案
ls | tee a.txt b.txt c.txt    # 同時寫三個檔案 + stdout

# 經典用途：sudo 寫需要權限的檔案
# echo "text" > /etc/file 失敗（重導向由 shell 做，不是 sudo）
echo "127.0.0.1 myhost" | sudo tee -a /etc/hosts
#   sudo tee 有權限寫 /etc/hosts（tee 在 sudo 下跑）
#   > /dev/null 可丟棄 tee 的 stdout 輸出
echo "text" | sudo tee /etc/file > /dev/null
```

> **`sudo tee` 是 tee 最重要的慣用法之一**。`echo x | sudo > /etc/file` 不行——因為 `>` 重導向是 **shell** 做的（在 sudo 之前），shell 沒有寫 /etc/file 的權限。而 `echo x | sudo tee /etc/file` 行——因為寫檔案的是 **tee**，它在 sudo 下跑（有權限）。這解決了「怎麼用 sudo 寫一個檔案」的經典難題。記住：重導向（`>`）是 shell 的權限，管線到 `sudo tee` 才是 sudo 的權限。這是 Ch 19（重導向是 shell 做的）的實際後果。

## xargs：把 stdin 變成參數

xargs 是新手最常搞錯的工具。先搞懂核心區別：

```
stdin（資料）vs 命令列參數（argv）—— 兩個不同的東西：

  有些命令從 stdin 讀資料：
    grep pattern        ← 從 stdin 讀「要搜尋的文字」
    sort, wc, cat       ← 從 stdin 讀資料
        │
  有些命令從「參數」拿輸入（不讀 stdin）：
    rm file1 file2      ← 檔名是「參數」（argv），不是 stdin
    mkdir dir1 dir2     ← 同上
    kill 1234           ← PID 是參數
        │
  問題：echo "file.txt" | rm
    → rm 不讀 stdin！它等「參數」
    → 這個管線什麼都不刪（rm 沒收到參數）
        │
  xargs 的工作：把 stdin 轉成「參數」
    echo "file.txt" | xargs rm
    → xargs 讀 stdin（"file.txt"），把它當參數執行 rm file.txt
    → 等於 rm file.txt
```

```bash
# 體會差別
echo "/tmp/test.txt" | rm          # 沒用！rm 不讀 stdin（什麼都沒刪）
echo "/tmp/test.txt" | xargs rm    # 有用！xargs rm /tmp/test.txt

# xargs 的常見用途
find . -name "*.tmp" | xargs rm           # 刪所有 .tmp（find 給檔名，xargs 轉參數給 rm）
find . -name "*.log" | xargs wc -l        # 算每個 log 的行數
cat servers.txt | xargs -n1 ping -c1      # 對每個 server ping（-n1：一次一個參數）
ls *.txt | xargs -I{} cp {} backup/       # 把每個 .txt 複製到 backup（-I{} 佔位符）
```

> **xargs 的核心是「stdin 是資料、argv 是參數，兩者不同」**。`grep`/`sort`/`wc` 從 stdin 讀；`rm`/`mkdir`/`kill`/`cp` 從**參數**（argv）拿輸入。管線送的是 stdin——所以 `echo file | rm` 沒用（rm 不看 stdin）。xargs 是橋樑：它讀 stdin，把內容當**參數**去執行命令。這個 stdin/argv 的區別是命令列最重要的概念之一，搞懂它，你就不會再寫出 `echo x | rm` 這種無效管線。判斷一個命令該不該用 xargs：問「它的輸入是資料（stdin）還是要操作的對象（參數）？」——對象就用 xargs。

## xargs 的重要選項

```bash
# -n N：每次傳 N 個參數（分批執行）
echo "1 2 3 4 5" | xargs -n2 echo
#   echo 1 2
#   echo 3 4
#   echo 5            ← 每次 2 個，分批

# -I {}：用佔位符指定參數位置（不是接在最後）
ls *.jpg | xargs -I{} mv {} images/{}    # {} 代表每個輸入
echo "a b c" | xargs -I{} echo "item: {}"

# -P N：並行執行 N 個（加速！）
find . -name "*.png" | xargs -P4 -I{} optimize {}    # 4 個並行處理
cat urls.txt | xargs -P8 -n1 curl -O                 # 8 個並行下載

# -d / -0：改變分隔符（預設是空白和換行）
echo "a:b:c" | xargs -d: echo    # 用 : 分隔 → echo a b c

# -t：執行前印出命令（debug 用）
ls | xargs -t rm
#   rm file1 file2...   ← 先印命令再執行

# -r（--no-run-if-empty）：stdin 空時不執行（避免無參數誤執行）
echo -n "" | xargs -r rm    # stdin 空，不執行 rm（沒有 -r 會跑 rm 無參數）
```

> **`-P`（並行）是 xargs 的隱藏超能力**。`find . -name '*.png' | xargs -P4 -I{} optimize {}` 同時跑 4 個 optimize——這是把單核任務變多核的最簡單方法，不用寫任何並行程式碼。處理大量檔案（壓縮圖片、轉碼、批次下載）時，`-P$(nproc)` 能讓速度乘上 CPU 核心數。這是 xargs 從「轉參數的工具」升級成「簡易並行框架」的關鍵。配合 `-n1`（一次一個參數）讓每個任務獨立並行。

## 故意弄壞：檔名有空白的災難

xargs 最危險的陷阱是檔名含特殊字元（空白、換行）：

```bash
cd ~/cmdlab
mkdir xargs-test && cd xargs-test
touch "my file.txt"          # 檔名有空白！
touch normal.txt

# 危險：預設 xargs 用空白分隔 → "my file.txt" 被當成兩個！
ls | xargs rm
# rm: cannot remove 'my': No such file or directory      ← 把 "my file.txt" 拆成 "my" 和 "file.txt"
# rm: cannot remove 'file.txt': No such file or directory
#   normal.txt 被刪了，但 "my file.txt" 沒被刪（名字被拆壞）

# 更糟的情境：檔名是 "important; rm -rf ~" 之類 → 可能災難

# 正確做法：用 NUL 分隔（find -print0 + xargs -0）
touch "another file.txt"
find . -type f -print0 | xargs -0 rm
#   -print0：find 用 NUL（\0）分隔檔名（NUL 不可能出現在檔名裡）
#   -0：xargs 用 NUL 分隔
#   → 含空白的檔名也安全處理
```

> **`find -print0 | xargs -0` 是處理檔名的安全慣用法**。預設 xargs 用空白和換行分隔——但檔名**可以**含空白、換行（Linux 檔名幾乎什麼字元都能有，除了 `/` 和 NUL）。所以 `ls | xargs rm` 碰到 `my file.txt` 會把它拆成 `my` 和 `file.txt` 兩個參數，刪錯東西。唯一安全的分隔符是 **NUL（`\0`）**——因為它是檔名唯一不能含的字元（除了 `/`）。`find -print0`（用 NUL 分隔輸出）配 `xargs -0`（用 NUL 分隔輸入）才能安全處理任意檔名。這是 SysOps 必須形成的肌肉記憶：**處理 find 的結果，永遠 `-print0 | xargs -0`**（或用 `find -exec`，後述）。`ls | xargs` 在腳本裡是 bug。

## tee 和 xargs 的對比與選擇

| 場景 | 工具 | 為什麼 |
|---|---|---|
| 管線中間存一份 | `tee file` | 分岔：存檔+繼續流 |
| sudo 寫檔案 | `sudo tee` | 重導向是 shell 權限，tee 才在 sudo 下 |
| 把 stdin 變成命令參數 | `xargs cmd` | 橋接 stdin 和 argv |
| 批次處理檔案 | `xargs -I{} cmd {}` | 佔位符指定位置 |
| 並行加速 | `xargs -P N` | 簡易並行 |
| 處理檔名（安全）| `find -print0 \| xargs -0` | NUL 分隔避免空白問題 |
| 簡單的對每個檔案執行 | `find -exec cmd {} \;` | find 內建，不用 xargs |

```bash
# find -exec vs find | xargs（兩種做法）
find . -name "*.tmp" -exec rm {} \;       # find 內建 exec（每個檔案跑一次 rm）
find . -name "*.tmp" -exec rm {} +        # + 結尾：批次（像 xargs，少執行次數）
find . -name "*.tmp" -print0 | xargs -0 rm    # xargs 版（更靈活，能並行 -P）
#   -exec ... \;  ：每個檔案一次 rm（慢但簡單，自動處理特殊字元）
#   -exec ... +   ：批次傳給一次 rm（快，自動處理特殊字元）
#   | xargs       ：最靈活（-P 並行、-I 佔位）但要 -print0/-0 防特殊字元
```

> **`find -exec` 和 `find | xargs` 是兩個競爭方案**。`-exec cmd {} \;` 對每個檔案執行一次命令（簡單、自動安全處理特殊字元，但慢——N 個檔案 N 次 fork）。`-exec cmd {} +` 把多個檔案批次傳給一次命令（快，像 xargs，也自動安全）。`| xargs -0` 最靈活（能 `-P` 並行、`-I` 佔位符）但需要 `-print0/-0` 防特殊字元。經驗：簡單的「對每個檔案做一件事」用 `-exec ... +`；需要並行或複雜參數位置用 `xargs`。兩者都比 `ls | xargs`（不安全）好。

## 進階：xargs 與命令列長度限制

xargs 還解決一個你可能沒意識到的問題——命令列長度上限：

```bash
# 問題：參數太多會超過 ARG_MAX（命令列最大長度）
rm *.txt          # 如果有 100 萬個 .txt → "Argument list too long" 錯誤
#   shell 展開 *.txt 成 100 萬個參數，超過 kernel 的 ARG_MAX 限制

getconf ARG_MAX   # 看你系統的上限（通常 ~2 MB）

# xargs 自動分批，繞過這個限制
find . -name "*.txt" -print0 | xargs -0 rm
#   xargs 自動把參數切成多批（每批不超過 ARG_MAX），分次執行 rm
#   → 即使 100 萬個檔案也能處理

# 對比：echo * 也可能爆（同樣的 ARG_MAX 問題）
# 解法都是 find | xargs（分批）或 find -exec ... +
```

> **xargs 自動繞過 `ARG_MAX`（命令列長度上限）**。`rm *.txt` 碰到太多檔案會報 "Argument list too long"——因為 shell 把 `*.txt` 展開成全部檔名當參數，超過 kernel 的 `ARG_MAX`（約 2 MB，`getconf ARG_MAX` 查）。xargs **自動分批**：它把輸入切成多批，每批參數不超過上限，分次執行命令。所以處理「海量檔案」時 `find | xargs` 不只是好習慣，是**必須**——它是唯一不會爆 ARG_MAX 的方法（`find -exec ... +` 同理會分批）。這是「為什麼大量檔案不能直接 `rm *`」的根本原因，也是 xargs 存在的原始動機之一。

## 動手練習

1. tee 分岔：`ls -la | tee full.txt | grep "^d"`，確認 full.txt 有完整輸出、終端機只看到目錄

2. sudo tee：試 `echo "test" > /etc/test`（失敗，permission denied）vs `echo "test" | sudo tee /etc/test`（成功），理解差別

3. stdin vs argv：`echo /tmp/x | rm`（無效）vs `echo /tmp/x | xargs rm`（有效），體會 xargs 的橋接

4. 跑「故意弄壞」：建含空白的檔名，`ls | xargs rm`（壞）vs `find -print0 | xargs -0 rm`（對），看差別

5. xargs 並行：`seq 1 10 | xargs -P4 -I{} sh -c 'sleep 1; echo {}'`，感受 -P 的並行加速

## 本章重點整理

- tee 是管線分岔口（一進兩出）：同時存檔和往下游流；`sudo tee` 解決「sudo 寫檔案」（重導向是 shell 權限，tee 才在 sudo 下）
- xargs 橋接 stdin（資料）和 argv（參數）：把 stdin 內容轉成命令參數，解決「rm/cp/kill 不讀 stdin」
- xargs 選項：`-I{}`（佔位符）、`-P N`（並行）、`-n N`（分批）、`-0`（NUL 分隔）
- 安全鐵律：處理檔名永遠 `find -print0 | xargs -0`（檔名可含空白，唯有 NUL 安全）
- xargs 自動分批繞過 ARG_MAX——海量檔案唯一不會爆的方法

## 自我檢核

- [ ] 能解釋 `sudo tee` 為什麼能寫 /etc 而 `sudo >` 不行
- [ ] 能說清楚 stdin 和命令列參數的區別，以及 xargs 橋接它們
- [ ] 知道為什麼 `ls | xargs` 不安全，正確做法是什麼
- [ ] 知道 xargs `-P` 能並行、能繞過 ARG_MAX
- [ ] 能在 `find -exec` 和 `find | xargs` 之間選擇

## 延伸閱讀

### 官方文件 / man page

- **[xargs(1) man page](https://man7.org/linux/man-pages/man1/xargs.1.html)** — Linux man-pages
  - **讀哪裡**：OPTIONS（特別是 -0、-I、-P、-n、-r）和 EXAMPLES
  - **為什麼值得讀**：權威定義所有選項；EXAMPLES 段示範 find -print0 | xargs -0 的安全用法

- **[tee(1) man page](https://man7.org/linux/man-pages/man1/tee.1.html)** — GNU coreutils
  - **讀哪裡**：整篇（短），注意 -a（追加）選項
  - **為什麼值得讀**：理解 tee 的完整行為

### 文章

- **[Using xargs safely](https://www.gnu.org/software/findutils/manual/html_node/find_html/Safe-File-Name-Handling.html)** — GNU findutils manual
  - **這篇說什麼**：詳述檔名特殊字元的危險和 -print0/-0 的解法
  - **讀哪裡**：Safe File Name Handling 整節
  - **為什麼值得讀**：官方對「為什麼 ls | xargs 危險」的權威說明，本章踩雷的完整版

- **[BashFAQ/020 — find files with special characters](https://mywiki.wooledge.org/BashFAQ/020)** — Greg's Wiki
  - **這篇說什麼**：處理含特殊字元檔名的各種正確/錯誤做法
  - **為什麼值得讀**：Greg's Wiki 是 bash 最可靠的社群資源，這篇把檔名陷阱講透

### 書籍

- **《Classic Shell Scripting》— Ch 5 (Pipelines Can Do Amazing Things)** — Robbins & Beebe（O'Reilly）
  - **讀哪幾章**：Ch 5（管線+xargs 的實戰組合）
  - **這本書的定位**：把管線、tee、xargs 組合成真實工具的範例集

→ [Ch 23 正規表示式](./23-regex.md)
