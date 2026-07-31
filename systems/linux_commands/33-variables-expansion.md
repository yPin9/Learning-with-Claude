# Ch 33 — 變數 / 參數 / 展開

> **目標**：掌握 shell 的變數和參數展開（parameter expansion）——變數的定義與作用域、特殊變數（$?、$$、$@、$#…）、參數展開的強大功能（`${var:-default}`、`${var%suffix}`、`${var//a/b}` 等）、陣列、算術展開。這些是讓腳本「能做事」的材料，特別是參數展開能取代很多外部命令（sed/cut/basename），又快又不依賴外部工具。

> **環境**：bash 5.x。陣列和部分展開是 bash 特性，POSIX sh 差異會標注。

## 為什麼參數展開值得專門學？

你已經會 `$var`（Ch 32）。但 shell 的參數展開遠不止取值——它能設預設值、去除前後綴、子字串、搜尋替換、改大小寫。這些 `${var...}` 語法看起來神秘，但它們能取代外部命令（`basename`、`dirname`、`sed`、`cut`），在腳本裡又快（不用 fork 外部程式，Ch 15）又可靠（不依賴系統有沒有裝某工具）。

理解變數和展開是 scripting 從「玩具」到「實用」的關鍵。一個成熟的 shell 腳本充滿 `${file%.txt}`（去副檔名）、`${path##*/}`（取檔名）、`${var:-$DEFAULT}`（預設值）這類展開——學會它們，你的腳本會更短、更快、更穩。

## 變數基礎與作用域

```bash
# 定義變數（注意：等號兩邊「不能」有空白！）
name="alice"                  # 對
name = "alice"                # 錯！shell 把 name 當命令，= 當參數
name="$first $last"           # 用其他變數組合（Ch 32 的雙引號）

# 讀取
echo "$name"                  # 永遠加雙引號（Ch 32）
echo "${name}"                # 等價，{} 在需要明確邊界時用

# 變數作用域：預設是「當前 shell」，函式裡用 local 限制
myfunc() {
    local temp="only in function"   # local：只在函式內可見
    global="visible outside"        # 沒 local：洩漏到外面（污染！）
}
myfunc
echo "$global"                # visible outside（洩漏了）
echo "$temp"                  # 空（local 的，函式外看不到）

# 唯讀變數
readonly PI=3.14159
PI=3                          # 錯誤：readonly variable（不能改）

# 刪除變數
unset name

# export（Ch 29）：讓子 process 繼承
export PATH="$HOME/bin:$PATH"
```

> **變數賦值的等號兩邊「絕對不能有空白」——這是新手第一個坑**。`name="alice"`（對）vs `name = "alice"`（錯）——後者 shell 會把 `name` 當**命令**、`=` 和 `"alice"` 當參數，報 "name: command not found"。這是因為 shell 用空白切詞（Ch 32），有空白就不是賦值語法了。另一個關鍵是**函式裡用 `local`**——shell 變數預設是「全域」（整個腳本可見），函式裡不加 `local` 的變數會**洩漏**到函式外，污染全域命名空間，造成難查的 bug（函式 A 的暫存變數意外改了函式 B 依賴的同名變數）。**鐵律：函式內的變數一律 `local`**。這是寫可維護腳本的基本紀律，Ch 34（函式）會再強調。

## 特殊變數:腳本的「狀態」

shell 有一組特殊變數，反映腳本和命令的狀態：

```bash
# 退出狀態（exit status）—— 最重要！
ls /tmp
echo $?                       # 0（上個命令的退出碼，0=成功，非0=失敗）
ls /nonexistent
echo $?                       # 2（失敗的退出碼）
# $? 是 Ch 35（錯誤處理）的核心

# Process 相關
echo $$                       # 當前 shell 的 PID（Ch 16）
echo $!                       # 最後一個背景命令的 PID（Ch 18）
sleep 100 &
echo $!                       # 那個 sleep 的 PID

# 腳本參數
# 假設 script.sh foo bar baz
echo $0                       # 腳本名（script.sh）
echo $1                       # 第一個參數（foo）
echo $2                       # 第二個（bar）
echo $#                       # 參數個數（3）
echo "$@"                     # 所有參數，分開（"foo" "bar" "baz"，Ch 32）
echo "$*"                     # 所有參數，併成一個字串（"foo bar baz"）

# 範例：處理所有參數
for arg in "$@"; do           # "$@" 正確遍歷每個參數（含空白的也對）
    echo "arg: $arg"
done
```

```
最常用的特殊變數：
  $?    上個命令的退出碼（0=成功）—— 錯誤處理的核心
  $$    當前 shell 的 PID（常用於暫存檔名 /tmp/foo.$$）
  $!    最後背景命令的 PID
  $0    腳本/命令名
  $1-$9 位置參數（第 N 個）
  ${10} 第 10 個以上要 {}（$10 會被當 $1 加 0）
  $#    參數個數
  "$@"  所有參數（分開，每個保完整）← 遍歷/轉發用這個
  "$*"  所有參數（合成一個字串）
```

> **`$?`（退出碼）是 shell 腳本的「成敗訊號」，是錯誤處理的基石**。每個命令執行完都設定 `$?`——`0` 是成功，非零是失敗（不同的非零值代表不同錯誤類型）。`if command; then`（Ch 34）背後就是檢查 `$?`。`$$`（當前 PID）常用於產生唯一的暫存檔名（`tmpfile=/tmp/myscript.$$`，避免多個腳本實例衝突）。`$#`（參數個數）用於驗證「使用者給對參數了嗎」（`if [ $# -lt 2 ]; then 報錯`）。`"$@"`（Ch 32）遍歷所有參數。這些特殊變數讓腳本能「感知狀態」並據此反應——沒有它們，腳本只能盲目地一條條跑，無法判斷成敗、無法處理參數。記住 `$?`、`$#`、`"$@"` 這三個，覆蓋大半的腳本控制需求。

## 參數展開:取代外部命令的利器

這是本章的精華——`${var...}` 的各種變形，能做字串處理而不 fork 外部命令：

```bash
file="/home/alice/document.txt"

# 預設值（變數沒設或為空時用替代值）
echo "${name:-anonymous}"     # name 沒設 → 用 "anonymous"（不改 name）
echo "${name:=anonymous}"     # 同上，但「同時設定」name = anonymous
echo "${name:?must be set}"   # name 沒設 → 報錯並退出（防呆，Ch 32 的災難防範！）
echo "${name:+yes}"           # name「有設」才輸出 "yes"（反向）

# 字串長度
echo "${#file}"               # 24（字串長度）

# 去除前綴/後綴（取代 basename/dirname/sed）
echo "${file%.txt}"           # /home/alice/document（去掉最短的 .txt 後綴，% = 從尾）
echo "${file%%.*}"            # /home/alice/document（去掉最長的 .* 後綴，%% = 貪婪）
echo "${file#*/}"             # home/alice/document.txt（去掉最短的 */ 前綴，# = 從頭）
echo "${file##*/}"            # document.txt（去掉最長的 */ → 等於 basename！）
echo "${file%/*}"             # /home/alice（去掉 /檔名 → 等於 dirname！）

# 子字串
echo "${file:0:5}"            # /home（從位置 0 取 5 個字元）
echo "${file:6}"             # alice/document.txt（從位置 6 到結尾）
echo "${file: -3}"            # txt（負數從尾算，注意空白！）

# 搜尋替換（取代 sed）
echo "${file/alice/bob}"      # /home/bob/document.txt（替換第一個）
echo "${file//o/0}"           # /h0me/alice/d0cument.txt（// 替換全部）
echo "${file/.txt/.bak}"      # 改副檔名

# 大小寫（bash 4+）
str="Hello World"
echo "${str^^}"               # HELLO WORLD（全大寫）
echo "${str,,}"               # hello world（全小寫）
echo "${str^}"                # Hello World（首字母大寫）
```

```
參數展開的「去前後綴」記憶法：

  # 和 ## ：從「開頭」去（# 在鍵盤上 $ 左邊 = 前面）
    ${var#pattern}   去掉「最短」匹配的前綴
    ${var##pattern}  去掉「最長」匹配的前綴
  % 和 %% ：從「結尾」去（% 在 $ 右邊 = 後面）
    ${var%pattern}   去掉「最短」匹配的後綴
    ${var%%pattern}  去掉「最長」匹配的後綴
        │
  常用組合：
    ${path##*/}   取檔名（去最長的「*/」前綴）= basename
    ${path%/*}    取目錄（去最短的「/*」後綴）= dirname
    ${file%.*}    去副檔名
    ${file##*.}   取副檔名（去最長的「*.」前綴）
```

> **參數展開能取代 basename/dirname/sed/cut，在腳本裡又快又可靠**。`${file##*/}`（取檔名）等於 `basename "$file"`、`${file%/*}`（取目錄）等於 `dirname "$file"`、`${file%.txt}`（去副檔名）取代 `sed 's/.txt$//'`、`${var//a/b}`（全域替換）取代 `sed 's/a/b/g'`——但**不用 fork 外部程式**（Ch 15，每次 fork 都有成本），在迴圈裡跑幾千次差別很明顯。記憶法：`#` 從**前**去（`#` 在 `$` 左邊）、`%` 從**後**去（`%` 在右邊）；單個是**最短**匹配、雙個是**最長**匹配。`${var:-default}`（預設值）和 `${var:?error}`（未設就報錯，Ch 32 的災難防範）是防呆的關鍵。這些 `${...}` 語法是成熟 shell 腳本的標誌——學會它們，你的腳本更短、更快、不依賴外部工具是否存在。

## 陣列

bash 有陣列（POSIX sh 沒有），處理「一組值」時必要：

```bash
# 定義陣列
fruits=("apple" "banana" "cherry")
fruits[3]="date"              # 加元素

# 存取
echo "${fruits[0]}"           # apple（索引從 0）
echo "${fruits[@]}"           # apple banana cherry date（所有元素）
echo "${#fruits[@]}"          # 4（元素個數）
echo "${!fruits[@]}"          # 0 1 2 3（所有索引）

# 遍歷（注意引號，和 "$@" 同理）
for fruit in "${fruits[@]}"; do    # "${arr[@]}" 正確遍歷（含空白元素也對）
    echo "$fruit"
done

# 切片
echo "${fruits[@]:1:2}"       # banana cherry（從索引 1 取 2 個）

# 從命令輸出建陣列
mapfile -t lines < file.txt   # 把檔案每行讀進陣列（bash 4+，安全！）
readarray -t lines < file.txt # 同 mapfile
files=(*.txt)                 # glob 結果存陣列（安全處理含空白檔名！）
echo "${#files[@]}"           # .txt 檔的數量

# 關聯陣列（bash 4+，像 dict/map）
declare -A ages               # 宣告關聯陣列
ages["alice"]=30
ages["bob"]=25
echo "${ages[alice]}"         # 30
for name in "${!ages[@]}"; do      # 遍歷 key
    echo "$name is ${ages[$name]}"
done
```

> **`files=(*.txt)`（glob 存進陣列）是安全處理「一組檔案」的正確方式，勝過 `for f in $(ls)`**。Ch 32 講過 `for f in $(ls)` 會切碎含空白的檔名。正解之一是陣列：`files=(*.txt)` 把所有 .txt 檔安全存進陣列（每個檔名是一個元素，含空白也完整），再 `for f in "${files[@]}"` 遍歷。`mapfile -t arr < file`（把檔案每行讀進陣列）是讀檔案行的安全方式（取代有陷阱的 `while read`）。**遍歷陣列永遠用 `"${arr[@]}"`（含引號）**——和 `"$@"` 同理，保護每個元素的完整性。bash 還有**關聯陣列**（`declare -A`，像字典）——做計數、查找表很方便（雖然複雜邏輯時 awk 的關聯陣列 Ch 26 更適合）。陣列是 bash 相對 POSIX sh 的重要優勢，處理「多個值」時別用空白分隔的字串硬湊，用陣列。

## 算術展開

```bash
# $(( ))：算術運算
echo $((2 + 3))               # 5
echo $((10 / 3))              # 3（整數除法，shell 沒有浮點！）
echo $((10 % 3))              # 1（餘數）
echo $((2 ** 10))             # 1024（次方）

# 變數在算術裡不用 $
count=5
echo $((count + 1))           # 6（算術裡 count 不用 $，但加了也行）
result=$((count * 2))

# 遞增/遞減
i=0
((i++))                       # i = 1（C 風格）
((i += 5))                    # i = 6
echo $i

# 比較（回傳 0/1，配 if 用）
((count > 3)) && echo "big"   # count > 3 為真 → echo

# 浮點要用 bc 或 awk（shell 只有整數！）
echo "scale=2; 10/3" | bc     # 3.33（bc 任意精度計算器）
awk 'BEGIN {print 10/3}'      # 3.33333（awk 有浮點，Ch 26）
```

> **shell 的算術 `$(())` 只有整數——要浮點得用 bc 或 awk**。`$((10/3))` 給 `3`（整數除法，小數截掉），不是 3.33。這是 shell 的根本限制：它只做整數運算。需要浮點（金額、百分比、科學計算）時用 `bc`（`echo "scale=2; 10/3" | bc`）或 awk（`awk 'BEGIN{print 10/3}'`，Ch 26）。算術展開裡變數**不用加 `$`**（`$((count+1))`，count 直接寫）。`(( ))`（不帶 $）做算術判斷和遞增——`((i++))`、`((count > 3)) && ...`。整數算術夠用於計數、索引、迴圈控制，但碰到小數一定要外借 bc/awk。記住這個限制，免得納悶「為什麼我的除法結果不對」。

## 故意弄壞:展開的陷阱

```bash
# 陷阱 1：賦值有空白
x = 5                         # 錯：x: command not found
x=5                           # 對

# 陷阱 2：算術是整數
echo $((5/2))                 # 2（不是 2.5！整數除法）

# 陷阱 3：${var} 邊界
prefix="log"
echo "$prefix_2024.txt"       # .txt（找變數 prefix_2024，不存在 → 空）
echo "${prefix}_2024.txt"     # log_2024.txt（{} 明確邊界）

# 陷阱 4：未設變數 vs 空變數
unset a
b=""
echo "${a:-default}"          # default（a 未設）
echo "${a-default}"           # default（: 表示「未設或空」，無 : 只看「未設」）
echo "${b:-default}"          # default（b 為空，: 讓空也用預設）
echo "${b-default}"           # （空！b 有設只是空值，無 : 不觸發預設）

# 陷阱 5：陣列不加引號遍歷
arr=("a b" "c d")
for x in ${arr[@]}; do echo "[$x]"; done    # [a][b][c][d]（切碎！）
for x in "${arr[@]}"; do echo "[$x]"; done  # [a b][c d]（對）
```

> **`${var:-x}` 和 `${var-x}` 的差別（冒號）是微妙但重要的展開陷阱**。有冒號 `:` 的版本（`${var:-default}`）在「var 未設定**或**為空字串」時用預設值；無冒號（`${var-default}`）只在「var 完全**未設定**」時用預設（var 設成空字串時不觸發）。多數情況你要的是有冒號的版本（「沒有有用的值就用預設」）。這個區別在 `${var:=}`、`${var:?}`、`${var:+}` 都適用。另一個常踩的是 `${prefix}_suffix` 的大括號——`$prefix_suffix` 會找名叫 `prefix_suffix` 的變數（底線是合法變數字元），要 `${prefix}_suffix` 才是「prefix 的值接 _suffix」。還有陣列遍歷一定要 `"${arr[@]}"`（含引號），否則切碎。這些展開細節是 shellcheck（Ch 36）會幫你抓的，但理解它們你才能 debug 那些「變數明明設了卻是空的」謎題。

## 動手練習

1. 參數展開取代命令：對一個路徑用 `${path##*/}`（basename）、`${path%/*}`（dirname）、`${file%.*}`（去副檔名），對照真的 basename/dirname

2. 預設值防呆：寫 `${1:?Usage: need an argument}`，不給參數跑腳本看它報錯退出（Ch 32 的災難防範）

3. 搜尋替換：用 `${var//old/new}` 做全域替換，對照 `sed 's/old/new/g'`，理解不用 fork 的好處

4. 陣列：用 `files=(*.txt)` 安全收集檔案，`"${files[@]}"` 遍歷，對照 `for f in $(ls)` 的切碎問題

5. 跑「故意弄壞」：每個展開陷阱實際跑（整數除法、${var} 邊界、:- vs - 、陣列引號），建立肌肉記憶

## 本章重點整理

- 變數賦值等號兩邊不能有空白；函式內變數用 local（否則洩漏污染全域）
- 特殊變數：$?（退出碼，錯誤處理核心）、$$（PID）、$#（參數數）、"$@"（所有參數）
- 參數展開 `${var...}` 取代外部命令：`##*/`（basename）、`%/*`（dirname）、`%.*`（去副檔名）、`//a/b`（全域替換）、`:-default`（預設值）
- 陣列：`files=(*.txt)` 安全收集、`"${arr[@]}"` 遍歷、關聯陣列 `declare -A`；勝過空白分隔字串
- 算術 `$(())` 只有整數，浮點要 bc/awk；`${var:-x}`（含冒號，未設或空）vs `${var-x}`（只未設）

## 自我檢核

- [ ] 知道變數賦值的語法規則（無空白）和 local 的重要性
- [ ] 熟悉 $?、$#、"$@" 等特殊變數的用途
- [ ] 能用參數展開取代 basename/dirname/sed（知道 #/##/%/%% 的差別）
- [ ] 會用陣列安全處理一組檔案，知道為什麼勝過 `for f in $(ls)`
- [ ] 知道 shell 算術只有整數，以及 `${var:-x}` 的冒號差別

## 延伸閱讀

### 必讀資源

- **[Bash manual — Shell Parameter Expansion](https://www.gnu.org/software/bash/manual/bash.html#Shell-Parameter-Expansion)** — GNU
  - **讀哪裡**：Shell Parameter Expansion 整節（所有 ${var...} 變形）
  - **為什麼值得讀**：參數展開所有語法的權威列表；本章「取代外部命令」那些展開的完整清單

- **[BashGuide/Parameters](https://mywiki.wooledge.org/BashGuide/Parameters)** + **[/Arrays](https://mywiki.wooledge.org/BashGuide/Arrays)** — Greg's Wiki
  - **這篇說什麼**：參數、特殊變數、陣列的完整教學
  - **讀哪裡**：Parameters 和 Arrays 兩篇
  - **為什麼值得讀**：把參數展開和陣列講得最清楚，含大量「為什麼這樣寫」的解釋

### 文章

- **[Parameter expansion cheat sheet](https://wiki.bash-hackers.org/syntax/pe)** — Bash Hackers Wiki
  - **這篇說什麼**：所有參數展開的速查表 + 範例
  - **為什麼值得讀**：放手邊查 `${var...}` 各種變形的最快參考

### 書籍

- **《Pro Bash Programming》— Ch 3-5** — Chris Johnson & Jayant Varma（Apress）
  - **讀哪幾章**：Ch 3（參數和變數）、Ch 4（展開）、Ch 5（陣列）
  - **這本書的定位**：把 bash 當程式語言深入教，參數展開和陣列的進階用法

→ [Ch 34 控制流與函式](./34-control-flow.md)
