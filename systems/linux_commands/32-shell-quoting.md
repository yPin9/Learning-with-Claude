# Ch 32 — shell 語法與 quoting

> **目標**：理解 shell 的「解析順序」——你打的一行命令，shell 怎麼一步步處理（quoting → 展開 → 切詞 → 執行）。重點是 quoting（單引號 vs 雙引號 vs 無引號）和它造成的無數 bug，特別是「為什麼變數一定要加雙引號」。這是 Part 8（scripting）的地基——quoting 錯誤是 shell 腳本 bug 的頭號來源。

> **環境**：bash 5.x。POSIX sh 差異會標注。

## 為什麼 quoting 是 shell 最重要的一課？

如果只能教 shell scripting 一件事，就是 quoting。shell 腳本的 bug，絕大多數來自引號用錯——檔名有空白導致命令把一個檔案當成兩個、變數沒加引號導致 glob 意外展開、`rm "$file"` 忘了引號刪錯東西。這些不是「進階陷阱」，是每天都會遇到的基本問題。

理解 quoting 要先理解 shell 怎麼**解析**一行命令。shell 不是直接把你打的字交給命令——它先做一連串處理（展開變數、切成詞、處理 glob…），quoting 控制這些處理。搞懂這個「解析管線」，你就不會再被「為什麼我的腳本碰到有空白的檔名就壞」困惑。這章是 Ch 1（shell 本質）的深化。

## 先建立直覺:shell 是個「預處理器」

```
shell 處理一行命令：不是直接執行，而是先「改寫」再執行

  你打：    cp $file /backup/
                ↓ shell 做一連串處理（展開、切詞...）
  shell 改寫成： cp myfile.txt /backup/
                ↓ 才執行
  實際執行： cp 帶著參數 ["myfile.txt", "/backup/"]
        │
  關鍵：shell 在「執行命令前」做了很多改寫：
    1. 展開變數（$file → myfile.txt）
    2. 展開 glob（*.txt → 所有 .txt 檔）
    3. 把結果「切成詞」（word splitting）—— 按空白切！
    4. 把切好的詞當參數傳給命令
        │
  → quoting 控制這些改寫：
    要不要展開？要不要切詞？要不要當 glob？
    引號就是告訴 shell「這部分別亂改」
```

關鍵心智：shell 是個「預處理器」——你打的命令在執行前，shell 會展開變數、展開 glob、然後**按空白切成詞**（word splitting），切好的詞才當參數傳給命令。quoting（引號）控制這些改寫：要不要展開、要不要切詞。多數 bug 來自「切詞」——shell 按空白切，所以含空白的值會被切成多個參數，除非你加引號。

> shell 的解析是 Ch 1（shell 怎麼把你的輸入變成命令執行）的細節版。如果對「shell 找命令、fork/exec」還不熟，回看 [Ch 1 — 命令列的本質](./01-shell-essence.md)。

## shell 的解析順序

shell 處理一行命令有固定的步驟順序，理解它解釋一切：

```
shell 解析一行命令的順序（簡化版，實際更多步）：

  1. 切分成 token（按語法：; | & 等）
  2. 各種展開（按順序）：
     a. 大括號展開    {a,b}  → a b
     b. 波浪號展開    ~      → /home/alice
     c. 變數/參數展開  $var   → 值
     d. 命令替換      $(cmd) → cmd 的輸出
     e. 算術展開      $((1+2)) → 3
  3. word splitting（切詞）：按 IFS（預設空白/tab/換行）切
     ★ 只切「展開的結果」，且只切「沒加引號的」
  4. glob 展開（pathname expansion）：*.txt → 實際檔名
     ★ 只對「沒加引號的」做
  5. quote removal：拿掉引號本身
  6. 執行（fork/exec，Ch 15）
        │
  → 關鍵：word splitting 和 glob 在「變數展開之後」
    所以 $var 的值如果含空白/星號，會被進一步切詞/glob！
    （除非加雙引號阻止）
```

```bash
# 驗證解析順序：變數展開後才切詞
file="my document.txt"        # 值含空白
touch "$file"                 # 建立 "my document.txt"（一個檔案）

ls $file                      # 錯！展開成 ls my document.txt → 找兩個檔案 "my" 和 "document.txt"
# ls: cannot access 'my': No such file or directory
# ls: cannot access 'document.txt': No such file or directory
ls "$file"                    # 對！"my document.txt" 當一個參數
# my document.txt

# 驗證 glob 在變數展開後
var="*.txt"
echo $var                     # 展開成所有 .txt 檔名！（var 的值 *.txt 被當 glob）
echo "$var"                   # *.txt（加引號阻止 glob，原樣輸出）
```

> **word splitting 和 glob 發生在「變數展開之後」——這是所有 quoting bug 的根源**。順序是：先把 `$file` 展開成它的值，**然後**才切詞和 glob。所以如果 `$file` 的值含空白（`my document.txt`），展開後會被切成兩個詞（`my` 和 `document.txt`）；如果值含 `*`，會被當 glob 展開。`ls $file`（無引號）因此壞掉。雙引號 `"$file"` 阻止這個——它讓變數展開後**不**切詞、**不** glob，整個值當一個參數。理解這個順序（展開 → 切詞/glob），你就懂為什麼「變數永遠加雙引號」是鐵律。這不是迷信，是 shell 解析機制的直接後果。

## 三種 quoting:無、單、雙引號

```bash
# 無引號：全部處理（展開 + 切詞 + glob）
echo $var                     # 展開變數、切詞、glob 都做

# 單引號 '...'：完全不處理（字面字串，連 $ 都不展開）
echo '$var and *.txt'         # $var and *.txt（原樣，什麼都不展開）
echo 'can'\''t'               # can't（單引號裡不能有單引號，要這樣拼）

# 雙引號 "...":展開變數和命令替換，但「不」切詞、「不」glob
echo "$var"                   # 展開 $var，但不切詞不 glob
echo "today is $(date)"       # 展開 $(date)（命令替換）
echo "path: $HOME/*.txt"      # 展開 $HOME，但 *.txt 不 glob（字面）
```

```
三種 quoting 對照：

  處理項目          無引號   單引號 '   雙引號 "
  變數展開 $var       ✓        ✗          ✓
  命令替換 $(cmd)     ✓        ✗          ✓
  glob *.txt          ✓        ✗          ✗
  word splitting      ✓        ✗          ✗
  字面 $ * 等         展開     原樣        $展開,*原樣
        │
  口訣：
    單引號 = 「完全字面」（所見即所得，連 $ 都不展開）
    雙引號 = 「展開變數，但保護空白和 glob」← 最常用！
    無引號 = 「全展開」（危險，除非你明確要切詞/glob）
        │
  → 規則：變數引用幾乎永遠用雙引號 "$var"
    要完全字面（如含 $ 的密碼、正則）用單引號
```

> **記住兩條規則就能避免 90% 的 quoting bug：變數引用用雙引號 `"$var"`，要完全字面用單引號 `'...'`**。**雙引號**展開變數和命令替換（`$var`、`$(cmd)` 會被處理），但**保護**空白和 glob（不切詞、不展開 `*`）——這是你引用變數時 99% 想要的：「給我變數的值，但別把它切碎或當萬用字元」。**單引號**完全字面——連 `$` 都不展開，適合含特殊字元的字串（正則、密碼、AWK 程式、含 `$` 的字面文字）。**無引號**全展開——只在你**明確**要切詞或 glob 時用（如故意讓 `$@` 展開成多個參數，或 `for f in *.txt`）。預設用雙引號，這是 shellcheck（Ch 36）會不斷提醒你的事。

## 命令替換:$() 與反引號

把命令的輸出嵌入命令列，是 scripting 的核心技巧：

```bash
# $(...)：執行命令，用它的輸出取代（命令替換）
today=$(date +%Y-%m-%d)       # today = "2024-01-15"
echo "Backup for $today"
files=$(ls *.txt)             # files = 所有 .txt 檔名

# 巢狀（$() 可以巢狀，反引號不行 → 用 $()）
echo "$(dirname "$(which bash)")"   # bash 所在的目錄

# 舊語法：反引號 `...`（避免使用！）
today=`date`                  # 等於 $(date)，但有缺點
# 為什麼用 $() 不用反引號：
#   1. $() 能巢狀，反引號要醜陋的跳脫
#   2. $() 可讀性好（反引號容易和單引號看錯）
#   3. 反引號裡的跳脫規則很怪

# 命令替換的 quoting 陷阱
count=$(ls | wc -l)
echo "Found $count files"     # 對
# 但命令替換的「結果」也會切詞！
for f in $(ls)                # 危險！檔名有空白會被切碎
do echo "$f"; done
# 正確做法見 Ch 34（用 glob 或 find -print0）
```

> **永遠用 `$(...)` 不用反引號 `` `...` ``——這是現代 shell 的共識**。兩者都是命令替換（執行命令、用輸出取代），但 `$(...)` 更好：能**巢狀**（`$(a $(b))`，反引號要醜陋的跳脫 `` `a \`b\`` ``）、可讀性好（反引號容易和單引號 `'` 混淆，尤其在小字體下）、跳脫規則正常。反引號是過時語法，只在極老的 sh 才需要。**陷阱**：命令替換的**結果**也會被切詞——`for f in $(ls)`（無引號）碰到含空白的檔名會切碎（這是 Ch 34 會講的「為什麼別 parse ls」）。命令替換放在雙引號裡（`"$(cmd)"`）保留輸出的完整性（含換行），不放引號則切詞——看你要哪個。

## 故意弄壞:quoting bug 大全

```bash
cd ~/cmdlab
mkdir quote-test && cd quote-test

# bug 1：檔名有空白
touch "important file.txt"
file="important file.txt"
rm $file                      # 嘗試刪，但 rm important file.txt → 刪不掉（找兩個檔案）
# rm: cannot remove 'important': No such file or directory
rm "$file"                    # 對：rm "important file.txt"

# bug 2：空變數導致命令缺參數
empty=""
ls $empty                     # ls （沒參數，列出當前目錄——可能不是你要的）
# 更糟：rm -rf $undefined/    → 如果 undefined 沒設，變成 rm -rf / ！！！
# 這就是著名的「Steam 刪光家目錄」bug 類型

# bug 3：glob 意外展開
pattern="*"
echo $pattern                 # 列出當前所有檔案！（* 被當 glob）
echo "$pattern"               # *（加引號，字面）

# bug 4：命令替換結果切詞
touch "a b.txt"
for f in $(ls); do echo "[$f]"; done   # [a] [b.txt]（切碎了！）
for f in *; do echo "[$f]"; done       # [a b.txt]（用 glob，對）

# bug 5：算術/比較沒引號
n=""
[ $n -eq 0 ]                  # 錯誤：[ -eq 0 ] 語法錯（n 是空的）
[ "$n" -eq 0 ]                # 還是錯（空字串不是數字），但至少不是語法崩潰
[ "${n:-0}" -eq 0 ]           # 對（預設值，Ch 33）

# 清理
cd ~/cmdlab && rm -rf quote-test
```

> **`rm -rf $undefined/` 是真實毀滅性 bug 的範本——空變數 + 無引號 = 災難**。如果 `$undefined` 沒設定（或為空），`rm -rf $undefined/` 展開成 `rm -rf /`——刪光整個系統。這不是假設：2015 年 Steam 的 Linux 客戶端就因類似 bug（`rm -rf "$STEAMROOT/"*`，當 STEAMROOT 為空時）刪光使用者家目錄。防範：(1) **變數永遠加雙引號** `"$var"`；(2) **設定預設值** `"${var:?error}"`（var 未設就報錯停止，Ch 33）；(3) 腳本開頭 `set -u`（用未定義變數就報錯，Ch 35）。`for f in $(ls)` 切碎含空白檔名是另一個經典——**永遠用 `for f in *`（glob）或 `find -print0`，不要 parse ls**（Ch 34）。這些不是吹毛求疵，是會真的刪掉資料、毀掉系統的 bug。quoting 是 shell scripting 的安全帶。

## 進階:特殊變數與跳脫

```bash
# 跳脫單一字元（反斜線）
echo \$HOME                   # $HOME（跳脫 $，字面）
echo "a\"b"                   # a"b（雙引號裡跳脫 "）
echo "path\\to"               # path\to（跳脫反斜線本身）

# $* vs $@（腳本參數，Ch 33 詳述，但 quoting 在這很關鍵）
# "$@" 是唯一正確傳遞「所有參數」的方式（保留每個參數的完整性）
# "$*" 把所有參數併成一個字串
# $@ / $* （無引號）都會切詞（壞）

# heredoc（多行字串）
cat <<EOF                     # 展開變數
Home is $HOME
EOF
cat <<'EOF'                   # 'EOF' 單引號 = 不展開（字面）
Literal $HOME (not expanded)
EOF

# $'...'（ANSI-C quoting，能用跳脫序列）
echo $'line1\nline2'          # 真的換行（\n 被解釋）
echo 'line1\nline2'           # line1\nline2（單引號不解釋 \n）

# 變數加引號的邊界情況
echo "${var}_suffix"          # 用 {} 明確變數邊界（不然 $var_suffix 找不到）
```

> **`"$@"`（含引號）是傳遞「所有腳本參數」的唯一正確方式**。當你的腳本要把收到的所有參數原封不動傳給另一個命令時，`"$@"` 是唯一保留「每個參數完整性」的寫法——它展開成 `"$1" "$2" "$3"...`（每個參數獨立加引號，含空白的參數不被切碎）。`$@`（無引號）會切詞（含空白的參數壞掉）、`"$*"` 把全部併成一個字串（`"$1 $2 $3"`）、`$*` 也切詞。記住：**轉發參數永遠用 `"$@"`**。另外 `${var}`（大括號）明確變數邊界——`"$var_suffix"` 會找名叫 `var_suffix` 的變數（找不到），`"${var}_suffix"` 才是「var 的值接上 _suffix」。heredoc 的 `<<EOF`（展開變數）vs `<<'EOF'`（字面，引號 EOF）也是常用的 quoting 控制。這些是 Ch 33（變數展開）的前置。

## 動手練習

1. 解析順序：建一個含空白的檔名，`ls $file`（壞）vs `ls "$file"`（對），理解 word splitting 在變數展開後

2. 三種引號：`echo $var`、`echo '$var'`、`echo "$var"` 對照，理解單/雙/無引號的展開差異

3. 跑「故意弄壞」全套：每個 quoting bug 都實際跑一遍，親眼看檔名切碎、glob 意外展開、空變數的後果

4. 命令替換：`for f in $(ls)`（切碎）vs `for f in *`（對）對含空白檔名的差異

5. 危險演練（安全版）：`echo rm -rf $undefined/`（先加 echo 看會展開成什麼）vs `echo rm -rf "${undefined:?}"/`，理解空變數的災難和防範

## 本章重點整理

- shell 是預處理器：執行前先展開變數/glob、按空白切詞（word splitting），切好的詞才當參數
- 關鍵順序：word splitting 和 glob 在「變數展開之後」——所以含空白/星號的變數值會被進一步切碎/展開（除非加引號）
- 三種引號：單引號（完全字面，連 $ 不展開）、雙引號（展開變數但保護空白/glob，最常用）、無引號（全展開，危險）
- 鐵律：變數引用永遠 `"$var"`；轉發所有參數用 `"$@"`；命令替換用 `$()` 不用反引號
- quoting bug 是真實災難（rm -rf 空變數刪光系統、檔名切碎）——quoting 是 shell 的安全帶

## 自我檢核

- [ ] 能解釋 shell 的解析順序，特別是 word splitting 為什麼在變數展開之後
- [ ] 清楚單引號、雙引號、無引號各展開什麼、保護什麼
- [ ] 知道為什麼變數要加雙引號，能舉出不加引號的災難案例
- [ ] 知道 `"$@"` 是轉發參數的正確方式，為什麼
- [ ] 看到一段 shell 腳本能指出 quoting 問題（為 Ch 36 shellcheck 鋪路）

## 延伸閱讀

### 必讀資源

- **[BashGuide/Quotes](https://mywiki.wooledge.org/Quotes)** — Greg's Wiki
  - **這篇說什麼**：quoting 的完整解釋，從 word splitting 到各種引號的精確行為
  - **讀哪裡**：整篇（不長）
  - **為什麼值得讀**：Greg's Wiki 是 bash 最可靠的社群權威，quoting 這篇是必讀；本章規則的完整版

- **[BashPitfalls](https://mywiki.wooledge.org/BashPitfalls)** — Greg's Wiki
  - **這篇說什麼**：50+ 個常見 bash 陷阱，大半和 quoting 有關（`for f in $(ls)`、`rm $var` 等）
  - **讀哪裡**：前 20 條，幾乎都是 quoting/word splitting 問題
  - **為什麼值得讀**：本章「故意弄壞」的完整擴充，每個陷阱都有解釋和正解

### 官方文件

- **[Bash manual — Shell Expansions](https://www.gnu.org/software/bash/manual/bash.html#Shell-Expansions)** — GNU
  - **讀哪裡**：Shell Expansions 整章（展開的精確順序）、Word Splitting、Quoting
  - **為什麼值得讀**：shell 解析順序的權威定義；本章「解析順序」那節的官方來源

### 書籍

- **《Learning the bash Shell》— Ch 1, 4, 7** — Cameron Newham（O'Reilly, 3rd ed）
  - **讀哪幾章**：Ch 1（quoting 基礎）、Ch 4（展開）、Ch 7（進階 I/O）
  - **這本書的定位**：bash 的系統教材，把 quoting 和展開講得循序漸進

→ [Ch 33 變數/參數/展開](./33-variables-expansion.md)
