# Ch 29 — 環境變數與 PATH

> **目標**：理解環境變數（environment variable）的底層——它是每個 process 都帶著的一張「字串對照表」、怎麼透過 fork/exec 繼承（為什麼 export 才會傳給子 process）、PATH 怎麼決定命令去哪找、shell 變數和環境變數的區別。這把 Ch 15（fork/exec 繼承）和 Ch 1（shell 找命令）的機制補完整。

> **環境**：bash 5.x，Linux。其他 shell（zsh/fish）概念相同語法略異。

## 為什麼環境變數無所不在？

你設過 `export PATH=...`、`echo $HOME`、被告知「把這個加到環境變數」。環境變數是 process 之間傳遞設定的標準機制——它決定命令去哪找（PATH）、家目錄在哪（HOME）、用什麼語言（LANG）、程式的各種行為（很多軟體讀環境變數設定）。

理解它的底層回答了關鍵問題：為什麼 `VAR=x` 設的變數子 process 看不到，要 `export`？為什麼在一個 terminal `export` 的東西，另一個 terminal 沒有？為什麼改了 `~/.bashrc` 要重開 terminal 或 `source`？這些都是「環境變數透過 fork/exec 繼承」的直接後果——而這正是 Ch 15 講的 process 繼承機制。

## 先建立直覺：每個 process 都背著一張表

```
環境變數：每個 process 都帶著的一張「字串對照表」

  process（如你的 bash）背著一張環境表：
  ┌──────────────────────────────┐
  │ HOME=/home/alice              │
  │ PATH=/usr/bin:/bin:...        │
  │ LANG=en_US.UTF-8              │
  │ USER=alice                    │
  │ ... （一堆 KEY=VALUE 字串）   │
  └──────────────────────────────┘
        │
  這張表是 process 的一部分（在它的記憶體裡）
  程式能讀這張表（getenv("HOME")）來決定行為
        │
  fork/exec 時，child 「複製」一份這張表（繼承！Ch 15）
    → child 看得到 parent 的環境變數
    → 但 child 改自己的表，不影響 parent（各有一份）
        │
  → 環境變數是「單向、向下」傳遞的（parent → child）
    這解釋了所有環境變數的「怪」行為
```

關鍵心智：環境變數是每個 process 記憶體裡的一張「KEY=VALUE 字串表」。fork/exec 時 child **複製**一份（繼承，Ch 15）。所以環境變數是**單向向下**傳遞——parent 傳給 child，child 改動不影響 parent，sibling process 之間互不可見。理解這個「複製繼承」模型，所有環境變數行為就清楚了。

> 環境變數的繼承完全是 Ch 15（fork/exec）的機制。如果你對 fork 怎麼複製 parent 狀態還不熟，先回看 [Ch 15 — fork/exec/wait](./15-fork-exec-wait.md)。child 繼承的不只 fd（Ch 19），還有環境變數。

## shell 變數 vs 環境變數:export 的意義

這是新手最大的困惑點——為什麼有些變數子 process 看得到，有些看不到：

```bash
# shell 變數：只存在當前 shell（不傳給 child）
MYVAR="hello"
echo $MYVAR                  # hello（當前 shell 看得到）
bash -c 'echo $MYVAR'        # （空！子 shell 看不到，因為沒 export）

# 環境變數：export 後，傳給所有 child
export MYVAR="hello"
echo $MYVAR                  # hello
bash -c 'echo $MYVAR'        # hello（子 shell 繼承了！）

# 一行設定 + export
export MYVAR="hello"
# 或
MYVAR="hello"
export MYVAR                 # 把已存在的 shell 變數「升級」成環境變數

# 看差別
VAR1="shell-only"            # shell 變數
export VAR2="exported"       # 環境變數
env | grep VAR               # VAR2=exported（只有 export 的在環境裡）
set | grep VAR               # 兩個都在（set 列出所有 shell 變數）
```

```
shell 變數 vs 環境變數：

  shell 變數（VAR=x）：
    只在當前 shell process 的「shell 變數空間」
    fork/exec 時「不」複製給 child
    → 子 process 看不到
        │
  環境變數（export VAR=x）：
    放進 process 的「環境表」（環境變數空間）
    fork/exec 時「會」複製給 child（繼承）
    → 子 process 看得到
        │
  export 做的事：把 shell 變數「標記」為要放進環境表
    → 之後 fork 的 child 才會繼承
        │
  → 這就是為什麼 PATH 要 export（讓你執行的命令繼承它）
    而純粹的腳本內部變數不用 export（不需要傳給 child）
```

> **`export` 的本質是「把這個變數放進會被繼承的環境表」**。`VAR=x`（shell 變數）只存在當前 shell，fork 出的子 process **看不到**——因為它在 shell 的私有變數空間，不在會被複製的「環境表」裡。`export VAR=x` 把它放進環境表，之後 fork 的 child 就繼承得到。這解釋了一切：為什麼 PATH 要 export（你執行的每個命令都是 child，要繼承 PATH 才知道去哪找其他命令）、為什麼腳本內的暫存變數不用 export（不需要傳給子 process）。`env` 列出環境變數（export 的），`set` 列出所有 shell 變數（含沒 export 的）。判斷要不要 export：**這個變數需要被你啟動的程式看到嗎？** 需要就 export。

## PATH:命令去哪找

PATH 是最重要的環境變數，它決定你打的命令從哪執行：

```bash
# PATH 是「: 分隔的目錄列表」，shell 依序在這些目錄找命令
echo $PATH
# /usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin
echo $PATH | tr ':' '\n'     # 每個路徑一行（Ch 27 的 tr 技巧）

# 你打 "ls" 時，shell 依序在 PATH 的目錄找名叫 ls 的可執行檔
which ls                     # /usr/bin/ls（找到的第一個）
type ls                      # ls is /usr/bin/ls（更完整，也認 builtin/alias）
command -v ls                # /usr/bin/ls（腳本裡用這個檢查命令存在）

# 找命令的順序（Ch 1 的補完）
type cd                      # cd is a shell builtin（builtin 優先，不查 PATH）
type -a ls                   # 列出所有匹配（PATH 裡多個同名時）

# 加入 PATH（常見需求：裝了工具但找不到）
export PATH="$HOME/bin:$PATH"        # 把 ~/bin 加到「前面」（優先找）
export PATH="$PATH:/opt/tool/bin"    # 加到「後面」（最後才找）
#   注意：$PATH 要包含原本的，否則覆蓋掉系統路徑 → 所有命令找不到！
```

```
shell 找命令的完整順序（打 "foo" 時）：

  1. 是 alias 嗎？        → 用 alias（Ch 1）
  2. 是 function 嗎？     → 執行 function
  3. 是 builtin 嗎？      → 執行 builtin（cd, echo, export...）
  4. 在 PATH 裡找：
       依序檢查 PATH 的每個目錄，找名叫 foo 的可執行檔
       找到第一個 → 執行它（fork/exec，Ch 15）
       全找不到 → "command not found"
        │
  → PATH 的「順序」很重要：前面的目錄優先
    這也是安全問題：如果 . （當前目錄）在 PATH 前面，
    惡意的 ./ls 會被優先執行（所以別把 . 放 PATH！）
```

> **PATH 的順序決定命令優先級，把 `.`（當前目錄）放進 PATH 是安全漏洞**。shell 依序在 PATH 的目錄找命令，用**第一個**找到的。所以 `export PATH="$HOME/bin:$PATH"`（自己的 bin 放前面）能覆蓋系統命令。危險：如果 `.`（當前目錄）在 PATH 裡，攻擊者在某目錄放一個惡意的 `ls`，你 `cd` 進去打 `ls` 就執行了惡意版本——這是經典的提權手法，所以**永遠不要把 `.` 放進 PATH**。另一個常見災難：`export PATH="/my/path"`（忘了 `:$PATH`）會**覆蓋**整個 PATH，導致 ls/cat 等系統命令全部 "command not found"（因為 /usr/bin 不在 PATH 了）——修法是 `export PATH="/usr/bin:/bin:$PATH"` 救回來。`type -a` 看一個命令的所有匹配（debug「我跑的是哪個版本」）。

## 環境變數從哪來:登入流程與設定檔

「為什麼改了 .bashrc 要重開 terminal」的答案在這裡：

```bash
# 常見環境變數的來源檔案（bash）
# 登入 shell（login shell）讀：
#   /etc/profile → /etc/profile.d/* → ~/.bash_profile (或 ~/.profile)
# 互動非登入 shell（如開新 terminal 分頁）讀：
#   /etc/bash.bashrc → ~/.bashrc

# 看一個變數是哪裡設的（搜尋設定檔）
grep -r "PATH" ~/.bashrc ~/.profile /etc/profile 2>/dev/null

# source（. ）：在「當前 shell」執行設定檔（不 fork！）
source ~/.bashrc             # 重新讀 .bashrc，套用到當前 shell
. ~/.bashrc                  # 同上（. 是 source 的簡寫）
#   為什麼要 source 而非 bash ~/.bashrc：
#   bash ~/.bashrc 開「子 shell」執行 → 設定在子 shell，退出就沒了
#   source 在「當前 shell」執行 → 設定留在當前 shell（這才是你要的）

# 設定 vs 立即生效
# 改了 ~/.bashrc 後，當前 shell 不會自動重讀 → 要 source 或重開 terminal
```

```
為什麼改 .bashrc 要 source 或重開 terminal：

  環境變數在 process 啟動時（讀設定檔）載入到記憶體
  之後改設定檔，「已經在跑的 shell」不會自動重讀
        │
  你的 terminal 是一個已經在跑的 bash process
    它的環境表是「開啟時」載入的
    改 .bashrc 不影響「已經載入的」環境表
        │
  解法：
    source ~/.bashrc  → 在當前 shell 重新讀設定檔（更新環境表）
    或重開 terminal   → 新 bash process 重新讀設定檔
        │
  → 這又是「環境變數是 process 記憶體的一部分」的後果
    改檔案 ≠ 改正在跑的 process 的記憶體
```

> **`source`（`.`）和直接執行腳本的差別是「在哪個 shell 跑」——這是設定檔機制的核心**。`bash ~/.bashrc` 開一個**子 shell** 執行 .bashrc，設定的變數在子 shell 裡，子 shell 一退出就沒了（你的 terminal 看不到）。`source ~/.bashrc`（或 `. ~/.bashrc`）在**當前 shell** 執行，設定留在當前 shell——這才是你要的。原理：環境變數是 process 記憶體的一部分，改設定**檔案**不會改「已經在跑的 shell」的記憶體。所以改完 .bashrc 要 `source` 它（讓當前 shell 重讀）或重開 terminal（新 process 重新讀）。延伸：login shell（SSH 登入）和互動 shell（開新分頁）讀**不同的**設定檔（`.bash_profile` vs `.bashrc`）——這是「為什麼我的 PATH 在 SSH 裡有、在 terminal 分頁裡沒有」這類困惑的根源。

## 常見的重要環境變數

```bash
# 看所有環境變數
env                          # 或 printenv

# 重要的環境變數及其作用
echo $HOME                   # 家目錄（~  展開成這個）
echo $USER                   # 當前使用者名
echo $SHELL                  # 預設 shell（注意：不是「當前」shell！是登入 shell 設定）
echo $PWD                    # 當前目錄（cd 會更新它）
echo $PATH                   # 命令搜尋路徑
echo $LANG                   # 語言/locale（影響排序、日期格式、訊息語言）
echo $TERM                   # 終端機類型（影響顏色、游標控制）
echo $EDITOR                 # 預設編輯器（git、crontab 等會用）
echo $TMPDIR                 # 暫存目錄（不設預設 /tmp）

# 程式用環境變數設定（無數例子）
LANG=C sort file             # 臨時用 C locale 排序（ASCII 順序，更快更可預測）
TZ=UTC date                  # 臨時用 UTC 時區
DEBUG=1 ./myapp              # 很多程式讀 DEBUG 環境變數開除錯模式

# 臨時設定環境變數給「單一命令」（不影響當前 shell）
VAR=value command            # VAR 只對這個 command 有效
LANG=C LC_ALL=C grep pattern file    # 強制 C locale（避免 UTF-8 慢、確保 ASCII 行為）
```

> **`VAR=value command`（命令前綴設定）只對那一個命令有效，是極實用的慣用法**。`LANG=C sort file` 臨時用 C locale 跑 sort（不改你 shell 的 LANG），`TZ=UTC date` 臨時看 UTC 時間，`DEBUG=1 ./app` 開某程式的除錯模式。這些設定**只對那個命令**有效，命令結束就沒了（不污染你的 shell 環境）。原理：shell 在 fork/exec 那個命令時，把這個 VAR 加進**那個 child** 的環境表（Ch 15 的「中間窗口調整 child」）。特別有用的是 `LC_ALL=C`——強制用 C/POSIX locale，讓 sort/grep 按 ASCII 處理（比 UTF-8 locale 快很多，且行為可預測，腳本裡常用以避免 locale 造成的排序差異）。這比 `export VAR; command; unset VAR` 簡潔太多。

## 故意弄壞:破壞 PATH 看後果

```bash
# 在「子 shell」裡實驗（不影響你的主 shell！用 bash -c 或開新 terminal）
bash    # 開一個子 shell 來實驗（搞砸了 exit 就好）

# 災難 1：覆蓋 PATH（忘了 :$PATH）
export PATH="/nonexistent"
ls                           # bash: ls: command not found（系統命令全沒了！）
/bin/ls                      # 用絕對路徑還能跑（不依賴 PATH）
export PATH="/usr/bin:/bin"  # 救回來（用絕對路徑的 export... 但 export 是 builtin 不需 PATH）
ls                           # 恢復

exit    # 退出實驗子 shell，回到正常的主 shell

# 災難 2：export 一個程式會用的變數，改變它行為
export LANG=C
date                         # 英文日期（C locale）
ls /                         # 排序變 ASCII 順序
unset LANG                   # 取消（或重開 terminal）

# 驗證繼承的單向性
export PARENT_VAR="from parent"
bash -c 'export CHILD_VAR="from child"; echo "child sees: $PARENT_VAR"'
# child sees: from parent     ← child 看得到 parent 的（繼承）
echo "parent sees child: $CHILD_VAR"
# parent sees child:          ← 空！parent 看不到 child 設的（單向向下）
```

> **覆蓋 PATH 後用絕對路徑還能執行命令——這驗證了 PATH 只影響「找命令」不影響「執行」**。如果你不小心 `export PATH="/wrong"`，`ls` 會 "command not found"，但 `/bin/ls`（絕對路徑）還能跑——因為 PATH 只在「你給的是命令名、需要去搜尋」時用，給絕對路徑時 shell 直接執行那個檔案（不查 PATH）。這是搞砸 PATH 後的救命知識：用絕對路徑 `/usr/bin/...` 還能操作，再 `export PATH="/usr/bin:/bin:$PATH"` 救回。另外這節驗證了**繼承的單向性**——child 看得到 parent 的環境變數（繼承），但 parent 看不到 child 設的（各有一份表，child 的改動不回流）。這就是為什麼「在腳本裡 export 的東西，腳本結束後在你的 shell 看不到」（腳本是 child）。

## 動手練習

1. export 的意義：設 `VAR=x`（不 export），`bash -c 'echo $VAR'`（看不到）；再 `export VAR`，重試（看得到），理解 export

2. 玩 PATH：`echo $PATH | tr ':' '\n'` 看搜尋路徑，`which`/`type -a` 一個命令看它從哪來

3. source vs 執行：寫個設 `export FOO=bar` 的小腳本，`bash script.sh`（FOO 不在你的 shell）vs `source script.sh`（FOO 在了），理解差別

4. 跑「故意弄壞」：在**子 shell**（`bash`）裡覆蓋 PATH 看命令消失，用絕對路徑救援，exit 回到正常 shell

5. 命令前綴：`LANG=C date` vs `date`、`LC_ALL=C sort` vs `sort`（含非 ASCII 內容），體會臨時環境變數

## 本章重點整理

- 環境變數是每個 process 記憶體裡的 KEY=VALUE 表；fork/exec 時 child 複製繼承（Ch 15）——單向向下傳遞
- shell 變數（VAR=x）不傳給 child；export 把它放進「會被繼承的環境表」，child 才看得到
- PATH 是 : 分隔的目錄列表，shell 依序找命令用第一個；順序決定優先級；別放 `.`（安全）；別覆蓋（會丟系統命令）
- 改設定檔不影響已在跑的 shell；source（在當前 shell 執行）vs 直接執行（子 shell，設定會丟失）
- `VAR=value command`（前綴）只對單一命令設環境變數；`LC_ALL=C` 強制 ASCII locale 是腳本常用技巧

## 自我檢核

- [ ] 能解釋 export 做什麼，為什麼沒 export 的變數子 process 看不到
- [ ] 知道 PATH 怎麼決定命令去哪找，以及順序的安全意義
- [ ] 能解釋為什麼改 .bashrc 要 source 或重開 terminal
- [ ] 知道 source 和直接執行腳本的差別
- [ ] 理解環境變數繼承的單向性（parent→child，不回流）

## 延伸閱讀

### 書籍

- **《The Linux Programming Interface》— Ch 6 (Process Environment), Ch 27 (Program Execution)** — Kerrisk
  - **讀哪幾章**：Ch 6.4（environ、getenv/setenv）、Ch 27（exec 怎麼傳遞環境給新程式）
  - **這本書的定位**：環境變數在 C 層的真相——environ 陣列、exec 怎麼帶環境。本章「繼承」機制的底層
  - **前提**：本章 + Ch 15

### 官方文件

- **[environ(7)](https://man7.org/linux/man-pages/man7/environ.7.html)** — Linux man-pages
  - **讀哪裡**：整篇，列出常見環境變數和它們的標準意義
  - **為什麼值得讀**：權威定義 PATH、HOME、LANG 等變數的用途

- **[Bash manual — Bash Startup Files](https://www.gnu.org/software/bash/manual/bash.html#Bash-Startup-Files)** — GNU
  - **讀哪裡**：Bash Startup Files 整節
  - **為什麼值得讀**：權威解釋 login/non-login/interactive shell 各讀哪些設定檔——「為什麼我的設定有時生效有時不」的答案

### 文章

- **[Bash startup files explained](https://blog.flowblok.id.au/2013-02/shell-startup-scripts.html)** — flowblok
  - **這篇說什麼**：詳細圖解 bash/zsh 在各種啟動情境（login/interactive/script）讀哪些檔案
  - **為什麼值得讀**：把「.bashrc vs .bash_profile vs .profile」的混亂講清楚，附流程圖

→ [Ch 30 cron 與 systemd timer](./30-cron-timer.md)
