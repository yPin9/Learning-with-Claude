# Ch 2 — man 與自我求助

> **目標**：掌握 Linux 的自我求助系統——man page 的 section 結構（為什麼 `man 1 printf` 和 `man 3 printf` 不同）、man page 的標準格式、`apropos`/`man -k` 搜尋、`--help`/`info`/`type`，讓你不必每次都 Google，能從系統內建文件找到權威答案。

> **環境**：man-db，Ubuntu/Debian。section 結構在所有 Unix-like 系統通用。

## 為什麼自我求助這麼重要？

新手遇到不會的指令就 Google。但 Google 的答案良莠不齊（過時、錯誤、針對不同系統）。系統內建的 **man page** 是**權威來源**——它和你系統上的指令版本完全對應，是那個指令的官方文件。

更關鍵的是：本課大量需要查 **syscall 的 man page**（如 `man 2 open` 看 open syscall）。理解 man 的 section 結構，你才能查到對的東西。會用 man，你的自學能力會質變——遇到陌生指令/syscall，能自己找到精確答案，不靠運氣。

## 先建立直覺：man 是分章節的百科全書

```
man page 分成 9 個 section（章節），按主題分類：

  Section 1：使用者命令（ls, cp, grep...）← 你最常查的
  Section 2：syscall（open, read, fork...）← 本課常查！
  Section 3：library 函式（printf, malloc...）
  Section 4：特殊檔案 / device（/dev/null...）
  Section 5：檔案格式 / 設定檔（/etc/passwd, crontab...）
  Section 6：遊戲
  Section 7：雜項 / 概念（regex, signal, pipe...）← 概念說明
  Section 8：系統管理命令（mount, systemctl...）
  Section 9：kernel 常式
        │
  同一個名字可能在多個 section！
  printf：section 1（printf 命令）vs section 3（printf C 函式）
```

關鍵洞察：**同一個名字在不同 section 是不同東西**。`printf` 既是 shell 命令（section 1）也是 C 函式（section 3）。`man printf` 預設給 section 1（命令）；要看 C 函式要 `man 3 printf`。理解 section 是用好 man 的鑰匙。

## man 的基本用法

```bash
# 查一個命令（預設找最低 section，通常 1）
man ls

# 指定 section
man 1 printf       # printf 命令（shell）
man 3 printf       # printf C 函式
man 2 open         # open syscall ← 本課常用
man 7 signal       # signal 概念說明

# 看一個名字在哪些 section 有 man page
man -f printf      # = whatis printf
# printf (1)  - format and print data
# printf (3)  - formatted output conversion
#         ↑ section 號

# 同名多 section 時，man -a 依序顯示全部
man -a printf      # 先 section 1，按 q 後接 section 3
```

> 本課反覆叫你查 syscall man page（section 2）。例如 Ch 4 講 inode 會叫你 `man 2 stat`，Ch 11 講 rm 會叫你 `man 2 unlink`。記住：**syscall 在 section 2，library 函式在 section 3**。`man 2 <syscall>` 是本課的常用咒語。

## man page 的標準結構

每個 man page 有固定的章節結構，學會快速定位：

```
man page 的標準章節（以 man 2 open 為例）：

  NAME         一句話說明（open - open and possibly create a file）
  SYNOPSIS     函式簽名 / 命令語法（怎麼呼叫）
  DESCRIPTION  詳細說明（最長，但常常不用全讀）
  RETURN VALUE 回傳值（syscall/函式的成功/失敗回傳）
  ERRORS       錯誤碼（errno 的所有可能值和意義）← syscall man page 的精華
  EXAMPLES     範例（如果有）
  SEE ALSO     相關的 man page（延伸）
        │
  閱讀技巧：
  - 快速用：看 NAME + SYNOPSIS + EXAMPLES
  - debug 用：看 RETURN VALUE + ERRORS（為什麼失敗）
  - 不用每次從頭讀到尾！
```

```bash
# 在 man page 裡瀏覽（man 用 less 顯示）：
#   /pattern   搜尋
#   n / N      下/上一個搜尋結果
#   g / G      跳到開頭/結尾
#   q          離開
#   空白鍵     往下一頁

# 例：在 man 2 open 裡搜 O_CREAT
man 2 open      # 然後打 /O_CREAT 找那個 flag 的說明
```

> man page 的 **ERRORS** 章節對 debug 特別有用。當一個 syscall 失敗（如 `openat` 回傳 -1），它設定 `errno`。man page 的 ERRORS 列出每個 errno 值的意義（`EACCES` 權限不足、`ENOENT` 檔案不存在...）。strace 會印出 errno（如 `openat(...) = -1 ENOENT`），你查 man page 的 ERRORS 就知道為什麼失敗。這是「strace + man」的 debug 組合拳。

## 搜尋：不知道指令名怎麼辦

知道想做什麼，但不知道指令叫什麼？用 `apropos`（= `man -k`）搜尋 man page 的描述：

```bash
# apropos：搜尋 man page 的「描述」（NAME 那行）
apropos "copy files"
# cp (1)       - copy files and directories
# install (1)  - copy files and set attributes
# ...

man -k "list directory"   # 同 apropos
# ls (1)  - list directory contents

# 搜尋更精確
apropos -s 2 "open"       # 只在 section 2（syscall）搜「open」
```

```bash
# whatis：看一個命令的一句話說明（不開整個 man page）
whatis ls
# ls (1) - list directory contents
```

> `apropos`/`man -k` 解決「我知道要做什麼，但不知道用哪個指令」的問題。它搜尋所有 man page 的描述行。`apropos "compress"` 找出所有和壓縮相關的指令。這比 Google「linux 壓縮指令」更可靠——它列出**你系統實際有的**指令。

## --help、info、type：其他求助管道

man 不是唯一的求助來源：

```bash
# --help：多數命令支援，快速看選項（比 man 簡短）
ls --help | head
grep --help

# info：GNU 的文件系統（比 man 更詳細，有超連結，但較少用）
info coreutils 'ls invocation'   # GNU coreutils 的 ls 完整文件

# type：查一個命令是什麼（Ch 1，builtin/外部/alias）
type cd          # cd is a shell builtin
type ls          # ls is /usr/bin/ls

# help：查 shell builtin 的說明（man 查不到 builtin！）
help cd          # bash builtin cd 的說明
help type
```

> **builtin 的文件不在 man，在 `help`**。`man cd` 會給你一個「BASH_BUILTINS」的大雜燴頁面（所有 builtin 擠一起）。要看單一 builtin 的說明，用 `help cd`（bash 的 builtin 求助）。這是常見的卡點——查 builtin 用 `help`，查外部命令用 `man`。

## 各求助工具的選擇

| 想做的事 | 用哪個 |
|---|---|
| 查外部命令的完整文件 | `man <cmd>` |
| 查 syscall | `man 2 <syscall>` |
| 查 C library 函式 | `man 3 <func>` |
| 查概念（regex/signal/pipe）| `man 7 <topic>` |
| 查設定檔格式 | `man 5 <file>` |
| 快速看選項 | `<cmd> --help` |
| 查 builtin | `help <builtin>` |
| 不知道指令名，搜功能 | `apropos "..."` / `man -k` |
| 查命令是什麼類型 | `type <cmd>` |

## 故意弄壞：查錯 section

```bash
# 你想看 printf 的 C 函式（格式化字串），但打：
man printf
# 顯示的是 printf 命令（section 1，shell 的 printf）
# 裡面沒有 %d、%s 在 C 裡的完整說明！

# 正確：明確指定 section 3（C 函式）
man 3 printf
# 這才是 C 的 printf，有完整的格式化說明

# 不確定哪個 section？先查：
man -f printf      # 看 printf 在哪些 section
```

查錯 section 是常見困惑——你以為 man page「不完整」或「沒講你要的」，其實是查到了不同 section 的同名項目。`man -f <name>` 先看有哪些 section，再查對的那個。

## 踩雷集錦

1. **不知道 section，查到不同東西**：`printf`、`open`、`time` 等名字在多 section 都有。`man -f <name>` 先看，再 `man <section> <name>` 查對的

2. **查 builtin 用 man**：`man cd` 給一坨 builtin 大雜燴。查單一 builtin 用 `help cd`（bash）。記住 builtin → help，外部 → man

3. **以為 man page 要從頭讀到尾**：man page 很長但有結構。快速用看 NAME+SYNOPSIS+EXAMPLES，debug 看 RETURN VALUE+ERRORS。用 `/` 搜尋跳到你要的部分

4. **忽略 SEE ALSO**：man page 結尾的 SEE ALSO 列出相關的 man page。順著它能找到更多相關工具/概念。這是探索的好起點

5. **man page 和你的版本不符（很少）**：man page 對應你系統安裝的版本。如果你看網路上的 man page，可能版本不同（選項有差）。以本機 `man` 為準

## 進階：man page 從哪來、怎麼寫的

man page 不是魔法生成的——它們是用 **troff/groff** 標記語言寫的文件，隨軟體一起安裝：

```bash
# man page 檔案在哪
man -w ls            # 印出 ls 的 man page 檔案路徑
# /usr/share/man/man1/ls.1.gz   ← 壓縮的 groff 原始檔

# 看原始 groff 標記（壓縮的）
zcat /usr/share/man/man1/ls.1.gz | head
# .TH LS "1" ...        ← troff/groff 巨集
# .SH NAME
# ls \- list directory contents

# MANPATH：man 去哪找 man page（類似 PATH，Ch 29）
echo $MANPATH
manpath              # 印出實際搜尋路徑
```

> man page 是 groff 格式的文件，隨軟體套件安裝（如 coreutils 套件帶 ls/cp/... 的 man page）。`man -w` 找原始檔。理解這個，你會懂：(1) 為什麼裝了軟體就有 man page（套件一起裝）；(2) 為什麼某些精簡系統缺 man page（如 Docker 的 minimal image 常移除 man page 省空間，要 `unminimize` 或裝 `man-db`）。如果你打包過軟體（debian_packaging 課程），會記得 man page 是套件的一部分。

## 動手練習

1. 探索 section：`man -f printf`、`man -f open`、`man -f time`，看這些名字在哪些 section。然後 `man 2 open` vs `man 3 fopen`，對比 syscall 和 library 函式的文件

2. 查 syscall 的 ERRORS：`man 2 open`，搜尋（`/`）到 ERRORS 章節，看 `EACCES`、`ENOENT`、`EISDIR` 各是什麼。這些 errno 你 strace 時會看到

3. 用 apropos 找指令：你想「比較兩個檔案」，但不知道指令名。`apropos "compare files"` 找出 `diff`、`cmp` 等。`man diff` 學它

4. 區分求助管道：對 `cd` 用 `man cd`（看到 builtin 大雜燴）和 `help cd`（看到 cd 的精確說明），理解 builtin 要用 help

## 本章重點整理

- man page 分 9 個 section：1=命令、2=syscall、3=library 函式、5=設定檔、7=概念、8=系統管理
- 同名在不同 section 是不同東西（printf 命令 vs C 函式）；`man -f <name>` 看有哪些 section，`man <N> <name>` 查指定的
- man page 標準結構：NAME/SYNOPSIS/DESCRIPTION/RETURN VALUE/ERRORS/SEE ALSO；debug 看 ERRORS（errno 意義）
- 求助管道：man（外部命令/syscall）、help（builtin）、--help（快速選項）、apropos/man -k（搜功能）、type（命令類型）
- 本課常用 `man 2 <syscall>` 查 syscall；strace 的 errno 配 man page 的 ERRORS 是 debug 組合拳

## 自我檢核

- [ ] 知道 man section 1/2/3/5/7 各放什麼，能說出 `man 2 open` 和 `man 3 fopen` 的差別
- [ ] 能用 `man -f` 看一個名字在哪些 section，並查到對的那個
- [ ] 知道查 builtin 用 `help` 不是 `man`
- [ ] 不知道指令名時，能用 `apropos`/`man -k` 搜尋
- [ ] 知道 man page 的 ERRORS 章節怎麼配合 strace 做 debug

## 延伸閱讀

### 官方文件

- **[man(1) man page](https://man7.org/linux/man-pages/man1/man.1.html)** 和 **[man-pages(7)](https://man7.org/linux/man-pages/man7/man-pages.7.html)**
  - **讀哪裡**：man-pages(7) 解釋 section 結構和 man page 的標準格式
  - **學什麼**：man 系統的完整設計，section 的權威定義
  - **前提**：本章

### 部落格 / 文章

- **[How to read a man page](https://jvns.ca/blog/2017/06/15/how-do-you-read-a-manpage/)** — Julia Evans
  - **這篇說什麼**：怎麼有效率地讀 man page（不用從頭讀）
  - **讀哪裡**：整篇
  - **為什麼值得讀**：把「man page 看起來很嚇人」變成「知道去哪找答案」

### 工具

- **[tldr pages](https://tldr.sh/)**
  - **這篇說什麼**：man page 的「常見用法範例」精簡版（社群維護）
  - **讀哪裡**：安裝 `tldr`，試 `tldr tar`、`tldr find`
  - **為什麼值得讀**：man page 太詳細時，tldr 給你「最常用的 5 個用法」。和 man 互補（tldr 看用法，man 看完整文件）

→ [Ch 3 檔案系統導覽與路徑](./03-filesystem-tour.md)
