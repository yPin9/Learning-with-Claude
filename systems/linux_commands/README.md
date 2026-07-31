# Linux 命令列學習筆記：從 `ls` 到讀懂 VFS

> 給懂一點 C、想把 Linux 命令列從「背指令」變成「理解底層」的工程師。

這系列不是指令速查表。它從「每個命令底層在對 kernel 做什麼 syscall」的角度重新教 Linux 命令列：VFS 與 inode、process 狀態機、file descriptor、pipe 的底層、正則引擎、shell 的展開規則。每個指令都拆到 syscall 層，讓你不只會用，還懂為什麼。最後整合成一套生產級的 SysOps 腳本工具包。

## 為什麼學這個？

- **命令列是系統工程的母語**：SRE、DevOps、後端、資安——每天都在 shell 裡。背指令只能應付熟悉的場景；懂底層才能在陌生問題前推理出解法
- **理解底層 = debug 能力**：「為什麼 rm 刪了還佔空間」「為什麼 kill 殺不掉」「為什麼管線卡住」——這些只有懂 fd/inode/signal/pipe 底層的人能秒解
- **職涯角度**：命令列底層知識是系統工程面試的硬通貨，也是把你和「只會複製貼上 stackoverflow」的人區分開的東西

## 先修知識

- **C 語言**（程度：會指標、struct、知道 syscall 是什麼；不需要寫過系統程式）
- **Linux 基礎**（程度：知道怎麼開終端機、跑過幾個指令）
- 不需要：系統程式設計經驗、shell scripting 經驗（課程從零補）

## 課程地圖

### Part 1 — 心智模型與環境（Ch 0–3）
- [Ch 0 環境搭建](./00-environment-setup.md)
- [Ch 1 命令列的本質](./01-shell-essence.md)
- [Ch 2 man 與自我求助](./02-man-and-help.md)
- [Ch 3 檔案系統導覽與路徑](./03-filesystem-tour.md)

### Part 2 — 檔案系統底層（Ch 4–9）
- [Ch 4 VFS 與 inode](./04-vfs-inode.md)
- [Ch 5 目錄與 dentry](./05-directory-dentry.md)
- [Ch 6 hard link vs symlink](./06-links.md)
- [Ch 7 權限位元與 ownership](./07-permissions.md)
- [Ch 8 特殊檔案：device/pipe/socket](./08-special-files.md)
- [Ch 9 mount 與檔案系統階層](./09-mount-fhs.md)
- [練習 A：手工探索 inode/link/權限](./practice-a-inode-explore.md)

### Part 3 — 檔案操作命令（Ch 10–13）
- [Ch 10 ls/stat 深入](./10-ls-stat.md)
- [Ch 11 cp/mv/rm 與底層 syscall](./11-cp-mv-rm.md)
- [Ch 12 find：表達式引擎](./12-find.md)
- [Ch 13 檔案內容工具](./13-file-content-tools.md)

### Part 4 — Process（Ch 14–18）
- [Ch 14 process 狀態機](./14-process-states.md)
- [Ch 15 fork/exec/wait](./15-fork-exec-wait.md)
- [Ch 16 ps/top/proc filesystem](./16-ps-proc.md)
- [Ch 17 signal](./17-signals.md)
- [Ch 18 job control 與 nohup/disown](./18-job-control.md)
- [練習 B：mini job monitor](./practice-b-job-monitor.md)

### Part 5 — I/O 重導向與管線（Ch 19–22）
- [Ch 19 file descriptor 與重導向](./19-fd-redirection.md)
- [Ch 20 pipe 底層](./20-pipe-internals.md)
- [Ch 21 管線哲學與組合](./21-pipeline-philosophy.md)
- [Ch 22 tee/xargs/process substitution](./22-tee-xargs.md)

### Part 6 — 文字處理（Ch 23–27）
- [Ch 23 正規表示式](./23-regex.md)
- [Ch 24 grep](./24-grep.md)
- [Ch 25 sed](./25-sed.md)
- [Ch 26 awk](./26-awk.md)
- [Ch 27 sort/uniq/cut/tr/join](./27-text-utils.md)
- [練習 C：log 分析管線](./practice-c-log-analysis.md)

### Part 7 — 使用者、權限、系統（Ch 28–31）
- [Ch 28 user/group/sudo](./28-users-groups.md)
- [Ch 29 環境變數與 PATH](./29-env-path.md)
- [Ch 30 cron 與 systemd timer](./30-cron-timer.md)
- [Ch 31 systemctl/journalctl 基礎](./31-systemd-basics.md)

### Part 8 — Shell Scripting（Ch 32–36）
- [Ch 32 shell 語法與 quoting](./32-shell-quoting.md)
- [Ch 33 變數/參數/展開](./33-variables-expansion.md)
- [Ch 34 控制流與函式](./34-control-flow.md)
- [Ch 35 錯誤處理與 trap](./35-error-handling.md)
- [Ch 36 debug 與 shellcheck](./36-debug-shellcheck.md)
- [練習 D：robust 備份腳本](./practice-d-backup-script.md)

### Final Project
- [Final Project：SysOps 腳本工具包](./final-project-sysops-toolkit.md)

## 學習方式建議

1. **每個指令都 strace 一次**：`strace -f ls` 看 ls 對 kernel 做了什麼 syscall。這把「指令」和「底層」連起來，是本課的核心手法
2. **故意把它弄壞**：權限設錯、把 fd 重導到奇怪地方、製造 zombie process——看現象，比讀說明書有效
3. **讀 /proc**：`/proc` 是 kernel 把 process/系統狀態暴露成檔案。每章都會用它驗證底層

## 精選資料庫

### 必讀基礎

- **《The Linux Programming Interface》** — Michael Kerrisk（No Starch Press, 2010）
  - 本課的底層聖經；檔案 I/O、process、signal、pipe 的 syscall 層權威。雖 2010 年，核心 API 不過時
- **[man7.org](https://man7.org/linux/man-pages/)** — Michael Kerrisk 維護的 man pages
  - 每個 syscall/命令的權威文件；本課反覆指向特定 man page 的特定小節

### 推薦部落格 / 文章

- **[Julia Evans (jvns.ca)](https://jvns.ca/)** — Julia Evans
  - 把 Linux 底層（strace、signal、fd、網路）講得最清楚易懂的作者；她的 zine 是本課很多概念的最佳補充
- **[Brendan Gregg's blog](https://www.brendangregg.com/)** — Brendan Gregg
  - 系統觀測和效能的權威；Part 4（process）和系統觀測章節的延伸

### 書籍

- **《The Art of Command Line》** — [GitHub: jlevy/the-art-of-command-line](https://github.com/jlevy/the-art-of-command-line)
  - 命令列實務技巧的精煉清單；和本課的「底層理解」互補
- **《Classic Shell Scripting》** — Robbins & Beebe（O'Reilly）
  - Part 8（scripting）的延伸，POSIX shell 的經典

### 讀完本課之後

- **《Advanced Programming in the UNIX Environment (APUE)》** — Stevens & Rago（把 syscall 層推到極致）
- **[OSTEP](https://pages.cs.wisc.edu/~remzi/OSTEP/)**（作業系統三易，理解 process/檔案系統背後的 OS 原理）
