# Linux 指令與 Shell Scripting 學習筆記：從 inode 到自動化維運

> 給已經會幾個基本指令、想真正理解底層、並能寫出生產等級 shell 腳本的工程師。

這系列從 Linux 的底層結構（inode、file descriptor、process 狀態機）講起，貫穿所有常用指令、文字處理工具、shell scripting，最後整合成一套能真實部署的維運腳本。每章都有能跑的範例，概念用底層原理解釋——不只教「怎麼用」，更教「為什麼這樣設計」。

## 為什麼學這個？

- **底層思維讓指令不再是死記**：知道 inode 是什麼，`ln` 和 `cp` 的差異就再也不會忘
- **Shell Scripting 是維運的基礎語言**：自動備份、日誌輪轉、部署腳本，都少不了
- **文字處理工具是資料分析的快速通道**：grep + awk + sort 的組合，往往比寫 Python 更快

## 課程地圖

### Part 1 — 基礎架構：Linux 的世界觀
- [Ch 0 環境準備](./00-environment-setup.md)
- [Ch 1 一切皆檔案：VFS 與 inode](./01-everything-is-a-file.md)
- [Ch 2 目錄樹與路徑](./02-directory-tree-and-paths.md)
- [Ch 3 權限模型](./03-permissions.md)
- [Ch 4 使用者與群組](./04-users-and-groups.md)

### Part 2 — 檔案系統操作
- [Ch 5 目錄與檔案操作](./05-file-operations.md)
- [Ch 6 查看檔案內容](./06-viewing-file-content.md)
- [Ch 7 搜尋](./07-searching.md)
- [Ch 8 封存與壓縮](./08-archives-and-compression.md)
- [Ch 9 符號連結與掛載概念](./09-symlinks-and-mount.md)
- [練習 A：檔案系統偵探](./practice-a-filesystem-detective.md)

### Part 3 — 文字處理工具
- [Ch 10 Pipeline 與重導向](./10-pipeline-and-redirection.md)
- [Ch 11 grep 與正規表示式](./11-grep-and-regex.md)
- [Ch 12 cut / sort / uniq / wc / tr](./12-cut-sort-uniq-wc-tr.md)
- [Ch 13 sed：流式編輯器](./13-sed.md)
- [Ch 14 awk：欄位處理引擎](./14-awk.md)
- [練習 B：日誌分析 pipeline](./practice-b-log-analysis.md)

### Part 4 — 行程管理與 I/O
- [Ch 15 行程狀態機](./15-process-state-machine.md)
- [Ch 16 訊號（Signal）](./16-signals.md)
- [Ch 17 工作控制](./17-job-control.md)
- [Ch 18 File Descriptor 深入](./18-file-descriptors.md)
- [Ch 19 環境與 Shell 變數](./19-environment-variables.md)
- [練習 C：Process 偵探](./practice-c-process-detective.md)

### Part 5 — Shell Scripting
- [Ch 20 腳本基礎](./20-script-basics.md)
- [Ch 21 條件判斷](./21-conditionals.md)
- [Ch 22 迴圈](./22-loops.md)
- [Ch 23 函式與作用域](./23-functions-and-scope.md)
- [Ch 24 陣列與字串處理](./24-arrays-and-strings.md)
- [Ch 25 參數與特殊變數](./25-parameters-and-special-vars.md)
- [Ch 26 錯誤處理](./26-error-handling.md)
- [練習 D：備份腳本](./practice-d-backup-script.md)

### Part 6 — 系統管理工具
- [Ch 27 磁碟與儲存](./27-disk-and-storage.md)
- [Ch 28 網路指令](./28-network-commands.md)
- [Ch 29 系統監控](./29-system-monitoring.md)
- [Ch 30 套件管理與 systemd](./30-package-management-and-systemd.md)
- [Final Project：自動化維運腳本套件](./final-project-sysops-scripts.md)

## 學習方式建議

1. **每章的「動手練習」要真的做**：看懂和能打出來是兩回事，特別是 awk/sed
2. **故意把指令打錯或參數給錯**：Linux 的錯誤訊息比多數人以為的更有資訊量
3. **查文件用 `man`**：`man ls`、`man 2 open`，培養離線查的習慣

## 參考資料

- `man` pages — 所有指令的第一手文件
- 《The Linux Command Line》— William Shotts（最推薦的入門書）
- 《Advanced Bash-Scripting Guide》— Mendel Cooper（bash scripting 聖經）
- Linux man-pages：https://man7.org/linux/man-pages/
