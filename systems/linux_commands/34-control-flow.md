# Ch 34 — 控制流與函式

> **目標**：掌握 shell 的控制流——if/test（`[ ]` vs `[[ ]]` 的關鍵差別）、for/while/until 迴圈、case、函式定義與參數、以及「為什麼 `while read` 是讀檔案的正確方式而 `for f in $(ls)` 是錯的」。控制流讓腳本能「決策和重複」，是把命令序列變成真正程式的關鍵。

> **環境**：bash 5.x。`[[ ]]`、`(( ))` 是 bash 特性，POSIX sh 用 `[ ]` 的差異會標注。

## 為什麼控制流是腳本的骨架？

到目前你能設變數、展開參數，但腳本還只是「一條條命令依序跑」。真正的程式需要**決策**（如果檔案存在才備份）和**重複**（對每個檔案做處理）。這就是控制流——if 做決策、for/while 做重複、case 做多分支、函式做封裝。

shell 的控制流有它的怪異之處——它建立在「命令的退出碼」（Ch 33 的 `$?`）之上，而非傳統的布林值。`if command` 其實是「如果 command 成功（退出碼 0）」。理解這個「一切都是命令、用退出碼判斷」的模型，shell 的 if/while 才不會像黑魔法。這章也會解決一個經典問題：怎麼正確地逐行讀檔案（`while read`），以及為什麼別 `for f in $(ls)`。

## 先建立直覺:if 判斷的是「退出碼」

```
shell 的 if：判斷「命令成功與否」，不是「真假值」

  傳統語言：    if (x > 5) { ... }      ← 判斷布林值
  shell：       if command; then ...    ← 判斷 command 的「退出碼」
        │
  if command; then
      # command 退出碼 0（成功）→ 執行這裡
  else
      # command 退出碼非 0（失敗）→ 執行這裡
  fi
        │
  那 if [ "$x" -gt 5 ] 是什麼？
    [ 其實是一個「命令」（test 命令）！
    [ "$x" -gt 5 ] 執行 test，x>5 時退出碼 0，否則非 0
    if 判斷這個退出碼
        │
  → shell 沒有「布林型別」，一切靠退出碼
    0 = 成功 = 「真」，非 0 = 失敗 = 「假」
    （注意：和 C 相反！C 裡 0 是假）
```

關鍵心智：shell 的 `if` 判斷的是**命令的退出碼**（Ch 33 的 `$?`），不是布林值。`if command` = 「如果 command 成功（退出碼 0）」。`[ ]`（中括號）其實是一個叫 `test` 的**命令**——`[ "$x" -gt 5 ]` 執行 test 命令，條件成立時退出碼 0。所以 shell 裡 **0 = 成功 = 真**（和 C 的「0 為假」相反）。理解「一切是命令、用退出碼判斷」，控制流就不神秘了。

> if 的退出碼模型直接建立在 Ch 33 的 `$?` 上。如果對退出碼還不熟，回看 [Ch 33 — 變數/參數/展開](./33-variables-expansion.md) 的特殊變數那節。

## if 與 test:[ ] vs [[ ]]

```bash
# if 的基本結構
if command; then
    echo "command succeeded"
elif other_command; then
    echo "other succeeded"
else
    echo "all failed"
fi

# if 直接判斷命令（不用 [ ]）
if grep -q "error" log.txt; then     # grep -q：安靜，只回退出碼
    echo "found error"
fi
if ! ping -c1 example.com &>/dev/null; then   # ! 反轉退出碼
    echo "host unreachable"
fi

# [ ] 做測試（test 命令）
if [ "$x" -gt 5 ]; then echo "big"; fi      # 數值比較
if [ "$str" = "hello" ]; then echo "match"; fi   # 字串相等（= 不是 ==）
if [ -f "/etc/passwd" ]; then echo "exists"; fi  # 檔案存在

# 常用 test 條件
# 檔案：-f（普通檔）-d（目錄）-e（存在）-r/-w/-x（可讀寫執行）-s（非空）
# 字串：-z（空）-n（非空）= != 
# 數值：-eq -ne -gt -lt -ge -le
[ -f file ] && echo "file exists"
[ -z "$var" ] && echo "var is empty"
[ -d /tmp ] && echo "tmp is a dir"
```

```
[ ] vs [[ ]]：關鍵差別

  [ ]（test，POSIX，所有 sh 都有）：
    是個「命令」，參數要嚴格 quoting
    [ $x = "a" ]    x 為空時變成 [ = "a" ] → 語法錯！
    必須 [ "$x" = "a" ]（加引號）
    不支援 && || < >（要用 -a -o -lt）
        │
  [[ ]]（bash/zsh 擴充，更安全）：
    是「語法」不是命令，不會因空變數出錯
    [[ $x = a ]]    x 為空也安全（不用引號也不崩，但仍建議加）
    支援 && || < >、regex 匹配 =~、glob 匹配
    [[ $file == *.txt ]]      glob 匹配
    [[ $str =~ ^[0-9]+$ ]]    regex 匹配（Ch 23）
        │
  → bash 腳本優先用 [[ ]]（更安全、更強）
    要 POSIX 可攜性才用 [ ]
```

```bash
# [[ ]] 的威力
if [[ "$file" == *.txt ]]; then echo "is txt"; fi    # glob 匹配
if [[ "$input" =~ ^[0-9]+$ ]]; then echo "is number"; fi  # regex（Ch 23）
if [[ "$x" -gt 5 && "$y" -lt 10 ]]; then echo "both"; fi  # && 直接用

# [ ] 要這樣（較笨拙）
if [ "$x" -gt 5 ] && [ "$y" -lt 10 ]; then echo "both"; fi
```

> **bash 腳本優先用 `[[ ]]`——它更安全、更強，避免 `[ ]` 的空變數陷阱**。`[ ]` 是 `test` 命令（POSIX，所有 sh 都有），但它有陷阱：空變數沒加引號會讓語法崩潰（`[ $x = a ]` 當 x 為空時變成 `[ = a ]`，test 報語法錯）。`[[ ]]` 是 bash 的**語法結構**（不是命令），空變數也安全，還支援 `&&`/`||`/`<`/`>` 直接用、glob 匹配（`[[ $f == *.txt ]]`）、regex 匹配（`[[ $s =~ ^[0-9]+$ ]]`，Ch 23）。**選擇**：寫 bash 腳本（`#!/bin/bash`）用 `[[ ]]`；需要 POSIX 可攜（`#!/bin/sh`，如 Alpine 容器的 dash）才用 `[ ]`。即使用 `[[ ]]`，變數仍建議加引號（好習慣）。`[ ]` 的數值比較用 `-eq/-gt`（不是 `==/>`），字串相等用 `=`（單等號，雖然 `[[ ]]` 也接受 `==`）——這些是常混淆的點。

## 迴圈:for / while / until

```bash
# for：遍歷一組值
for i in 1 2 3; do echo "$i"; done
for f in *.txt; do echo "$f"; done          # glob（安全處理含空白檔名！Ch 32）
for i in {1..10}; do echo "$i"; done        # 大括號範圍
for i in $(seq 1 10); do echo "$i"; done    # seq（範圍）
for ((i=0; i<10; i++)); do echo "$i"; done  # C 風格（bash）

# 遍歷陣列（Ch 33）
arr=("a b" "c d")
for x in "${arr[@]}"; do echo "[$x]"; done  # 引號保護元素完整

# while：條件為真時重複
i=0
while [ "$i" -lt 5 ]; do
    echo "$i"
    ((i++))
done

# while 讀命令輸出
while read -r line; do                       # 逐行讀（-r 防反斜線被吃，重要！）
    echo "Processing: $line"
done < file.txt                              # 重導向檔案進 while（Ch 19）

# until：條件為假時重複（while 的反面）
until ping -c1 host &>/dev/null; do
    echo "waiting for host..."
    sleep 1
done

# 迴圈控制
for i in {1..10}; do
    [ "$i" -eq 3 ] && continue    # 跳過這次
    [ "$i" -eq 7 ] && break       # 跳出迴圈
    echo "$i"
done
```

> **`while read -r line; do ...; done < file` 是逐行讀檔案的正確方式——`for line in $(cat file)` 是錯的**。Ch 32 講過命令替換會切詞。`for line in $(cat file)`（無引號）按**空白**切，不是按行——含空白的行被切成多個、空行被吃掉、glob 字元被展開。正解是 `while read -r line; do ...; done < file`——`read` 一次讀**一行**（按換行），`-r` 防止反斜線被當跳脫吃掉（**永遠加 `-r`**），`< file`（Ch 19 重導向）把檔案餵給迴圈。處理 `IFS` 還能控制欄位切割（`while IFS=: read -r user pass uid rest; do` 讀 /etc/passwd 的欄位）。這是 shell 處理「逐行」的標準慣用法，記住它。陷阱：`while read` 在管線裡（`cat file | while read`）會在 subshell 跑，迴圈內設的變數迴圈外看不到（Ch 20 的 subshell 機制）——所以用 `< file` 重導向而非管線。

## case:多分支

```bash
# case：比 if-elif 鏈清楚的多分支（用 glob 匹配）
case "$1" in
    start)
        echo "Starting..."
        ;;                      # ;; 結束一個分支（必須！）
    stop)
        echo "Stopping..."
        ;;
    restart|reload)             # | = 或（多個模式）
        echo "Restarting..."
        ;;
    *.txt)                      # glob 模式！
        echo "A text file: $1"
        ;;
    *)                          # 預設（萬用）
        echo "Unknown: $1"
        ;;
esac

# 實戰：解析命令的子命令（像 git start/stop）
case "$action" in
    -h|--help)  show_help ;;
    -v|--verbose) verbose=1 ;;
    [0-9]*)     echo "starts with digit" ;;   # glob：數字開頭
    *)          echo "default" ;;
esac
```

> **case 用 glob 模式匹配，比一長串 if-elif 清楚，是解析參數/子命令的標準工具**。`case "$var" in 模式) 動作 ;; esac`——每個分支是一個 **glob 模式**（不是 regex），`|` 表示「或」（`start|begin`），`*` 是萬用預設。它特別適合「根據一個值分多種情況」——解析命令列子命令（`start`/`stop`/`restart`）、處理選項（`-h|--help`）、按副檔名分類（`*.txt`/`*.jpg`）。注意 `;;` 結束每個分支（漏了會語法錯或 fall-through）。case 用 glob（`*.txt`、`[0-9]*`）不是 regex——這點和 `[[ =~ ]]` 不同。比起 `if [ "$x" = "a" ]; elif [ "$x" = "b" ]...` 的長鏈，case 更易讀、更易加分支，是腳本處理多情況的首選。

## 函式

```bash
# 定義函式（兩種語法）
greet() {                     # POSIX 風格（推薦）
    echo "Hello, $1"          # $1 是函式的第一個參數（不是腳本的！）
}
function greet {              # bash 風格（function 關鍵字）
    echo "Hello, $1"
}

# 呼叫（像命令，參數空白分隔）
greet "alice"                 # Hello, alice
greet "$user"

# 函式參數和特殊變數（和腳本參數同名）
myfunc() {
    echo "function got $# args"     # $# 在函式裡是「函式的參數數」
    echo "first: $1, all: $@"       # $1 $@ 是函式的參數
    local result="computed"         # local！（Ch 33，別污染全域）
}

# 回傳值：兩種方式
# 1. return（只能回 0-255 的退出碼，表示成功/失敗）
is_valid() {
    [[ "$1" =~ ^[0-9]+$ ]]    # 函式的退出碼 = 最後命令的退出碼
    return $?                  # 顯式（其實可省略）
}
if is_valid "123"; then echo "valid"; fi   # 用退出碼判斷

# 2. echo 輸出（回傳「資料」）
get_timestamp() {
    echo "$(date +%s)"        # 用 echo「回傳」資料
}
ts=$(get_timestamp)           # 用命令替換接收（Ch 32）

# 函式 + local + 退出碼 的完整範例
file_size() {
    local file="$1"
    [[ -f "$file" ]] || return 1      # 檔案不存在 → 回傳失敗
    stat -c%s "$file"                  # echo 出大小
}
if size=$(file_size "/etc/passwd"); then
    echo "Size: $size bytes"
fi
```

```
shell 函式的「回傳」有兩種，別混淆：

  return N：回傳「退出碼」（0-255），表示成功/失敗
    用途：函式作為「條件」（if myfunc; then）
    不能回傳「資料」（return "hello" 是錯的）
        │
  echo 輸出：回傳「資料」（字串、數字）
    用途：函式產生一個值（result=$(myfunc)）
    透過 stdout + 命令替換接收
        │
  → 要「成功/失敗」用 return（配 if）
    要「一個值」用 echo（配 $()）
    常見錯誤：以為 return 5 能回傳數字 5 給變數（不行！）
```

> **shell 函式的「回傳」分兩種：`return`（退出碼，表示成敗）和 `echo`（stdout，回傳資料）——混淆它們是常見錯誤**。`return N` 只能回 0-255 的**退出碼**（成功/失敗），用於把函式當條件（`if validate_input; then`）。它**不能**回傳資料——`return "hello"` 或想用 `return 5` 把數字 5 存進變數都是錯的。要回傳**資料**（字串、計算結果）用 `echo` 輸出 + 命令替換接收（`result=$(myfunc)`）——函式的 stdout 被捕捉成值。所以一個函式常常 `echo` 出結果**並**用 `return` 表示成敗（如上面的 `file_size`：檔案不存在 `return 1`，存在則 `echo` 大小）。**函式內變數一律 `local`**（Ch 33）——否則污染全域。函式的 `$1 $@ $#` 是**函式的**參數（不是腳本的），這是另一個易混點。掌握「return 表成敗、echo 傳資料、local 防污染」三原則，你的函式就寫對了。

## 故意弄壞:控制流陷阱

```bash
cd ~/cmdlab
# 陷阱 1：for 讀檔案行（錯）vs while read（對）
printf "line one\nline two\nline*three\n" > lines.txt
for line in $(cat lines.txt); do echo "[$line]"; done
# [line][one][line][two][line*three→可能glob展開]  ← 切碎 + glob！
while read -r line; do echo "[$line]"; done < lines.txt
# [line one][line two][line*three]  ← 對（逐行）

# 陷阱 2：[ ] 空變數
empty=""
[ $empty = "x" ]              # 錯：[ = "x" ] 語法錯（空變數）
[ "$empty" = "x" ]            # 對（加引號）
[[ $empty == "x" ]]           # 對（[[ ]] 不會崩）

# 陷阱 3：= vs ==（test 字串相等）
[ "$a" == "$b" ]              # bash 接受但非 POSIX
[ "$a" = "$b" ]               # POSIX 正確（單 =）

# 陷阱 4：忘記 return 不能傳資料
get_five() { return 5; }
get_five
echo $?                       # 5（這是退出碼，不是「回傳值」）
val=$(get_five)               # val 是空！（函式沒 echo 任何東西）
get_five_correct() { echo 5; }
val=$(get_five_correct)       # val=5（用 echo + 命令替換）

# 陷阱 5：管線裡的 while（subshell 變數丟失，Ch 20）
count=0
cat lines.txt | while read -r line; do ((count++)); done
echo "$count"                 # 0！（while 在 subshell，count 改動丟失）
while read -r line; do ((count++)); done < lines.txt
echo "$count"                 # 3（重導向，不是管線，count 對）

rm lines.txt
```

> **管線裡的 `while read` 會在 subshell 跑，迴圈內的變數改動在迴圈外丟失——這個陷阱很隱蔽**。`cat file | while read line; do ((count++)); done` 之後 `count` 還是 0——因為管線的每個 stage 在**子 shell**（subshell，Ch 20）執行，`while` 在子 shell 裡，它改的 `count` 是子 shell 的副本，主 shell 的 count 沒變。這讓「用 while 計數/累加」的腳本神秘地失敗。**解法：用重導向 `< file` 而非管線 `cat file |`**——`while read -r line; do ...; done < file` 的 while 在主 shell 跑，變數改動保留。這是「為什麼我的計數器永遠是 0」的經典原因。配合前面的「`for in $(cat)` 切碎」「`[ ]` 空變數崩潰」「return 不傳資料」，這些是 shell 控制流的五大陷阱——shellcheck（Ch 36）能抓大部分，但理解原理你才能 debug。

## 動手練習

1. if 退出碼：`if grep -q pattern file; then`、`if [ -f file ]; then`、`if ! command; then`，理解 if 判斷退出碼

2. [ ] vs [[ ]]：對空變數試 `[ $x = a ]`（崩）vs `[[ $x == a ]]`（安全），用 `[[ =~ ]]` 做 regex 匹配

3. while read：用 `while read -r line; do ...; done < file` 逐行讀檔，對照 `for line in $(cat file)` 的切碎

4. 函式回傳：寫一個函式用 return 表成敗（配 if）、另一個用 echo 傳資料（配 $()），理解兩種回傳

5. 跑「故意弄壞」：特別跑管線 while 的 subshell 陷阱（count=0），理解為什麼用重導向不用管線

## 本章重點整理

- shell 的 if 判斷「命令退出碼」（0=成功=真，和 C 相反）；`[ ]` 是 test 命令
- `[[ ]]`（bash）比 `[ ]`（POSIX test）安全強大：空變數不崩、支援 &&/regex/glob——bash 腳本優先用
- for 用 glob（`for f in *`）安全；逐行讀檔用 `while read -r line; done < file`（別 `for in $(cat)`）
- case 用 glob 模式多分支，比 if-elif 鏈清楚，適合解析子命令/選項
- 函式：return 傳退出碼（成敗，配 if）、echo 傳資料（配 $()）、變數用 local；管線裡的 while 是 subshell（變數丟失，用重導向）

## 自我檢核

- [ ] 能解釋 shell 的 if 判斷的是退出碼，以及 0=真的反直覺
- [ ] 知道 `[ ]` 和 `[[ ]]` 的差別，為什麼 bash 優先用 `[[ ]]`
- [ ] 會用 `while read -r` 正確逐行讀檔，知道為什麼不用 `for in $(cat)`
- [ ] 知道函式的兩種回傳（return 成敗 vs echo 資料），何時用哪個
- [ ] 知道管線裡的 while 為什麼變數會丟失（subshell）

## 延伸閱讀

### 必讀資源

- **[BashGuide/TestsAndConditionals](https://mywiki.wooledge.org/BashGuide/TestsAndConditionals)** — Greg's Wiki
  - **這篇說什麼**：if/test/[[ ]]/case 的完整教學，含退出碼模型
  - **讀哪裡**：整篇
  - **為什麼值得讀**：把「if 判斷退出碼」「[ ] vs [[ ]]」講得最清楚

- **[BashFAQ/001 — How can I read a file line by line](https://mywiki.wooledge.org/BashFAQ/001)** — Greg's Wiki
  - **這篇說什麼**：逐行讀檔的正確方式（while read -r）和各種陷阱
  - **為什麼值得讀**：本章「while read」那節的權威完整版，解釋每個細節（-r、IFS、subshell）

### 官方文件

- **[Bash manual — Conditional Constructs](https://www.gnu.org/software/bash/manual/bash.html#Conditional-Constructs)** + **[Looping Constructs](https://www.gnu.org/software/bash/manual/bash.html#Looping-Constructs)** — GNU
  - **讀哪裡**：if/case/[[ ]]、for/while/until 的語法定義
  - **為什麼值得讀**：控制流語法的權威；`[[ ]]` 的 =~ regex 和 == glob 行為的官方說明

### 書籍

- **《The Linux Command Line》— Part 4 (Writing Shell Scripts)** — William Shotts（No Starch, 免費線上）
  - **讀哪幾章**：Ch 27-36（從 if 到函式到 case 的完整 scripting 教學）
  - **這本書的定位**：最受推薦的 shell 入門書，控制流部分循序漸進、例子實用
  - **線上版**：[linuxcommand.org/tlcl.php](http://linuxcommand.org/tlcl.php)

→ [Ch 35 錯誤處理與 trap](./35-error-handling.md)
