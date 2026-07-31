# Ch 1 — 命令列的本質

> **目標**：理解「shell 是什麼、一個指令怎麼從你按 Enter 到執行完成」——shell 作為 read-eval-print loop、命令的解析與執行、builtin vs 外部命令、以及「一切皆檔案」這個 Unix 核心哲學。建立整門課的心智骨架。

> **環境**：bash 5.x，Ubuntu/Debian。多數概念對 zsh/fish 通用，語法差異會標注。

## 為什麼要先搞懂「shell 是什麼」？

你每天在 shell 打指令，但 shell 本身是什麼？它是一個圖形介面嗎？是 kernel 的一部分嗎？都不是——**shell 是一個普通的程式**（和 `ls`、`cat` 一樣），它的工作是「讀你打的命令、解析它、請 kernel 執行它、把結果給你」。

搞懂 shell 的本質，很多「謎之行為」就清楚了：為什麼 `cd` 不能寫成腳本？為什麼 `*` 會展開成檔名？為什麼 `echo $X` 印出變數值？這些都是 shell 在「解析你的命令」時做的事，不是 kernel 做的。這章建立整門課的心智骨架。

## 先建立直覺：shell 是個 REPL

```
shell 的核心迴圈（read-eval-print loop, REPL）：

  ┌─────────────────────────────────────┐
  │  1. Read：印出 prompt（$ ），讀你打的一行 │
  │         你打：ls -l /tmp              │
  │                                      │
  │  2. Eval：解析這一行                  │
  │     - 展開（變數 $X、萬用字元 *、~）   │
  │     - 切成 command + arguments        │
  │     - 找到命令（builtin？外部程式？）   │
  │     - 請 kernel 執行（fork + exec）    │
  │                                      │
  │  3. Print：把命令的輸出顯示給你        │
  │                                      │
  │  回到 1，等下一個命令                  │
  └─────────────────────────────────────┘
```

shell 就是這個無限迴圈：讀一行、解析執行、顯示結果、再讀下一行。你和系統的對話，就是這個 REPL 一輪一輪跑。理解「shell 在 Eval 階段做了什麼」是本章重點——很多 shell 的「魔法」都在這裡。

## 一個指令的生命週期

打 `ls -l /tmp` 按 Enter，shell 做這些事：

```
你打：ls -l /tmp <Enter>
        │
  1. shell 讀入這一行字串 "ls -l /tmp"
        │
  2. 展開（expansion）：
     - 萬用字元（這裡沒有 *）
     - 變數（這裡沒有 $X）
     - ~ → home 目錄（這裡沒有）
     → 結果還是 "ls -l /tmp"
        │
  3. 分詞（word splitting）：
     切成 ["ls", "-l", "/tmp"]
     第一個是命令，其餘是參數
        │
  4. 找命令 "ls"：
     - 是 shell builtin 嗎？（cd, echo, export...）→ 不是
     - 在 PATH 裡找：/usr/bin/ls 找到了（Ch 29）
        │
  5. 執行：
     - fork：複製一個子 process（Ch 15）
     - exec：子 process 變成 /usr/bin/ls，帶參數 -l /tmp
     - wait：shell 等子 process 結束
        │
  6. ls 跑完，shell 顯示輸出，回到 prompt
```

用 strace 驗證（Ch 0 學的）：

```bash
# 看 shell 執行 ls 時的 fork + exec
strace -f -e fork,clone,execve bash -c 'ls /tmp' 2>&1 | grep -E "clone|execve"
# clone(...) = 12345              ← fork 出子 process
# execve("/usr/bin/ls", ["ls", "/tmp"], ...) = 0  ← 子 process 變成 ls
```

> 這個「fork + exec」是 Unix 執行程式的核心模式（Ch 15 深入）。shell 不是「直接變成 ls」——它**複製自己**（fork）成一個子 process，然後讓子 process **變身**（exec）成 ls。shell 自己還在，等 ls 結束。理解這個，你會懂為什麼 ls 跑完你還在原本的 shell（shell 沒被取代，是它的子 process 跑了 ls）。

## Builtin vs 外部命令

不是所有命令都是「外部程式」。有些是 shell **內建**的：

```
外部命令（external）：
  獨立的可執行檔，在 PATH 某處
  ls → /usr/bin/ls
  grep → /usr/bin/grep
  shell 用 fork + exec 執行它們

builtin（內建命令）：
  shell 程式本身的功能，不是獨立檔案
  cd, echo, export, alias, source, pwd...
  shell 直接執行（不 fork/exec）
```

為什麼有些命令必須是 builtin？經典例子是 `cd`：

```bash
# cd 為什麼必須是 builtin？
# 想像 cd 是外部程式：
#   shell fork 出子 process → 子 process 執行 cd（改變目錄）
#   → 子 process 結束 → shell（父）的目錄沒變！
#   因為改的是子 process 的目錄，父 process（shell）不受影響
#
# 所以 cd 必須是 builtin：shell「自己」改自己的目錄，不 fork
```

```bash
# 判斷一個命令是 builtin 還是外部
type cd          # cd is a shell builtin
type ls          # ls is /usr/bin/ls（或 aliased）
type echo        # echo is a shell builtin（雖然也有 /usr/bin/echo）

# command -v 也能查
command -v cd    # 印出它是什麼
```

> `cd` 必須是 builtin 是個經典的「為什麼這樣設計」案例。子 process 改目錄不影響父 process（每個 process 有自己的 current working directory）。要改 shell 自己的目錄，shell 必須「親自」改（builtin），不能委派給子 process。同理 `export`（改 shell 的環境變數）、`alias`（改 shell 的別名表）也必須是 builtin。理解這個區分，能解釋很多「為什麼這個不能寫成腳本」的問題。

## 「一切皆檔案」：Unix 核心哲學

Unix/Linux 最深刻的設計哲學是 **「一切皆檔案」**（everything is a file）：

```
「一切皆檔案」的含義：
  不只磁碟上的檔案是檔案，還有：
  - 目錄（是一種特殊檔案，Ch 5）
  - 裝置（/dev/sda 磁碟、/dev/null、/dev/random，Ch 8）
  - 管線（pipe，Ch 20）
  - socket（網路連線）
  - process 資訊（/proc，Ch 16）
  - kernel 參數（/sys）
        │
  全部都用「檔案」的介面操作：open、read、write、close
        │
  好處：一套 API（檔案操作）操作所有東西
       一套工具（cat、grep、重導向）處理所有東西
```

這個哲學的威力：

```bash
# 因為「一切皆檔案」，這些都能用同樣的方式操作：

cat /etc/hostname          # 讀一般檔案
cat /proc/cpuinfo          # 讀 kernel 暴露的 CPU 資訊（不是真檔案！）
cat /dev/urandom | head -c 16 | xxd  # 讀「隨機數裝置」
echo "test" > /dev/null    # 寫到「黑洞裝置」（丟棄）
echo 1 > /proc/sys/net/ipv4/ip_forward  # 改 kernel 參數（用寫檔案的方式！）
```

> 「一切皆檔案」是 Unix 最優雅的設計之一。它讓 `cat`、`grep`、重導向（`>`）這些工具能操作**任何東西**——檔案、裝置、kernel 狀態、網路。你不需要學一百種 API，只要學「檔案操作」這一套，就能操作整個系統。本課後面講的 fd（Ch 19）、pipe（Ch 20）、device（Ch 8）、/proc（Ch 16）都是這個哲學的具體展現。記住這個哲學，整門課的概念會串成一體。

## 互動式 shell vs 腳本

shell 有兩種運作模式：

```
互動式（interactive）：
  你打一行，它執行一行（REPL）
  讀 ~/.bashrc（設定別名、prompt、變數）
  有 prompt、history、tab 補全

腳本（script）：
  一個檔案裡寫很多命令，shell 一次跑完
  #!/bin/bash 開頭（shebang，告訴 kernel 用哪個 shell 跑）
  不讀 ~/.bashrc（通常）
  沒有互動功能
```

```bash
# 互動式：你現在打的
$ echo hello
hello

# 腳本：寫進檔案
cat > script.sh <<'EOF'
#!/bin/bash
echo "I am a script"
echo "PID: $$"
EOF
chmod +x script.sh
./script.sh
```

Part 8 會深入腳本。這裡的重點是：**互動式和腳本是同一個 shell 的兩種模式**，語法一樣（你互動打的命令，寫進腳本也能跑）。

## 故意弄壞：以為 cd 能寫成獨立程式

```bash
# 寫一個「cd 程式」（外部，不是 builtin）
cat > mycd.sh <<'EOF'
#!/bin/bash
cd "$1"        # 改目錄
pwd            # 印當前目錄（在子 process 裡）
EOF
chmod +x mycd.sh

cd ~/cmdlab
./mycd.sh /tmp     # 子 process 跑這個腳本
# /tmp           ← 腳本裡 pwd 印 /tmp（子 process 的目錄）
pwd                # 但你的 shell：
# /home/you/cmdlab  ← 父 shell 的目錄沒變！
```

這驗證了 cd 為什麼必須是 builtin：腳本（子 process）改的是它自己的目錄，父 shell 不受影響。要改父 shell 的目錄，只能用 builtin `cd`（shell 親自改）。

## 踩雷集錦

1. **以為 shell 是 OS / kernel 的一部分**：shell 是普通程式（和 ls 一樣）。你能換 shell（bash→zsh→fish），kernel 不變。shell 只是「和你對話、請 kernel 做事」的中介

2. **不知道 builtin 和外部命令的差別**：`cd`、`export` 是 builtin（必須是，改 shell 自己的狀態）；`ls`、`grep` 是外部程式。`type <cmd>` 查。搞不清會困惑「為什麼 cd 不能 strace 到 execve」

3. **以為命令直接執行，沒有「展開」**：shell 在執行前會展開（`*`、`$X`、`~`）。`rm *` 的 `*` 是 shell 展開成檔名列表後才傳給 rm——rm 根本沒看到 `*`（Ch 33）

4. **以為 echo $X 是 echo 在讀變數**：是 shell 先把 `$X` 展開成值，再把值傳給 echo。echo 收到的是展開後的字串，不知道有變數這回事

5. **混淆「shell 被取代」和「子 process 跑命令」**：執行 ls 不會取代你的 shell。shell fork 子 process，子 process exec 成 ls。shell 還在（除非你用 `exec ls`，那才取代 shell）

## 進階：exec 取代 shell 自己

正常執行命令是 fork + exec（子 process 跑命令）。但 `exec` builtin 能讓 shell **不 fork，直接 exec**——用命令取代 shell 自己：

```bash
# 正常：fork + exec（shell 還在）
bash -c 'ls; echo "shell still here"'
# 列出檔案，然後印 "shell still here"（shell 跑完 ls 繼續）

# exec：取代 shell（shell 沒了）
bash -c 'exec ls; echo "never printed"'
# 列出檔案，然後... "never printed" 不會印！
# 因為 exec ls 讓這個 bash 進程「變成」ls，ls 結束就沒了
```

```
exec 的用途：
  - 包裝腳本：腳本最後 exec 真正的程式
    （不留一個多餘的 shell process）
  - 重導向 shell 自己的 fd（Ch 19）
    exec 3< file  → 給 shell 自己開一個 fd
        │
  exec 體現了「fork 和 exec 是分開的兩件事」（Ch 15）
  正常命令 = fork（複製）+ exec（變身）
  exec 命令 = 只 exec（不複製，直接變身，取代自己）
```

理解 `exec` 能加深「fork + exec 模型」的理解——它們是兩個獨立的動作，正常命令兩個都做，`exec` builtin 只做後者。這是 Ch 15 的伏筆。

## 動手練習

1. 判斷命令類型：對 `cd`、`ls`、`echo`、`pwd`、`grep`、`export`、`alias` 跑 `type <cmd>`，分類哪些是 builtin、哪些是外部程式。思考為什麼 builtin 的那些必須是 builtin

2. 用 strace 看 fork + exec：`strace -f -e clone,execve bash -c 'ls /tmp'`，找出 clone（fork）和 execve（變身成 ls）

3. 體驗「一切皆檔案」：用同一個 `cat` 讀 `/etc/hostname`（真檔案）、`/proc/uptime`（kernel 資訊）、`/dev/null`（空）。用 `>` 寫到 `/dev/null`（黑洞）

4. 跑「故意弄壞」的 mycd.sh，確認子 process 改目錄不影響父 shell。理解 cd 為什麼是 builtin

## 本章重點整理

- shell 是普通程式（不是 OS/kernel），核心是 REPL：讀命令 → 展開解析 → fork+exec 執行 → 顯示結果
- 一個命令的生命週期：讀入 → 展開（*/$X/~）→ 分詞 → 找命令（builtin？PATH？）→ fork+exec → wait
- builtin（cd/export/alias）必須是 builtin——它們改 shell 自己的狀態，子 process 改不了；外部命令（ls/grep）用 fork+exec
- 「一切皆檔案」是 Unix 核心哲學：檔案/目錄/裝置/pipe/process 都用檔案介面操作，讓一套工具處理所有東西
- `exec` builtin 揭示 fork 和 exec 是分開的：正常命令 fork+exec，exec 只 exec（取代 shell 自己）

## 自我檢核

- [ ] 能用自己的話解釋 shell 是什麼（普通程式、REPL、和 kernel 的中介）
- [ ] 能描述一個命令從按 Enter 到執行完成的生命週期
- [ ] 能解釋為什麼 cd 必須是 builtin（子 process 改目錄不影響父 shell）
- [ ] 能舉例說明「一切皆檔案」哲學的威力（cat 讀 /proc、寫 /dev/null）
- [ ] 知道正常命令是 fork+exec，理解 exec builtin 為什麼會取代 shell

## 延伸閱讀

### 書籍

- **《The Linux Programming Interface》— Ch 27 (Program Execution)** — Michael Kerrisk
  - **這本書的定位**：本課的底層聖經
  - **讀哪幾章**：Ch 27（exec、fork）解釋命令執行的 syscall 層；Ch 24-26（process creation）是 Ch 15 的延伸
  - **前提**：本章建立的概念

### 部落格 / 文章

- **[How does the shell work?](https://jvns.ca/blog/2021/01/27/day-26--using-strace-to-understand-how-a-shell-works/)** — Julia Evans
  - **這篇說什麼**：用 strace 看 shell 怎麼執行命令（fork+exec）
  - **讀哪裡**：整篇
  - **為什麼值得讀**：把本章的「命令生命週期」用 strace 具體展示

- **[The TTY demystified](https://www.linusakesson.net/programming/tty/)** — Linus Åkesson
  - **這篇說什麼**：終端機（TTY）的底層——你打字到 shell 收到之間發生什麼
  - **讀哪裡**：前半（TTY 和 process 的關係）
  - **為什麼值得讀**：補充本章沒展開的「終端機」這一層，理解 shell 之下還有什麼

→ [Ch 2 man 與自我求助](./02-man-and-help.md)
