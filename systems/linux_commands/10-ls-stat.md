# Ch 10 — ls/stat 深入

> **目標**：把最常用的 `ls` 和 `stat` 用到精通——ls 的關鍵選項與排序、輸出格式、顏色機制、stat 的格式字串、以及「ls 的排序在哪裡做」這類底層細節。承接 Ch 5（ls 的 getdents64 底層），補上實用面。

> **環境**：GNU coreutils 9.x（ls/stat）。BusyBox 版選項較少，行為有差異。

## 為什麼 ls 值得一整章？

`ls` 是你打最多次的指令，但多數人只用 `ls -l` 和 `ls -a`。它其實有豐富的選項——排序、時間格式、遞迴、人類可讀的大小、各種篩選。把 ls 用熟，你查檔案的效率會大幅提升。

而且 Ch 5 講了 ls 的底層（getdents64 + statx）。這章補上「實用面」——怎麼用各種選項、ls 的排序和顏色怎麼來的、以及一些反直覺的行為（如 ls 的輸出在管線裡會變）。stat 則是「看單一檔案完整資訊」的利器（練習 A 用過）。

## 先建立直覺：ls 是「讀目錄 + 排序 + 排版」

```
ls 的三個階段（Ch 5 的底層 + 本章的實用面）：

  1. 讀目錄（getdents64，Ch 5）
     取得所有檔名 + inode 號
        │
  2. 取得每個檔案的 metadata（statx，Ch 5）
     -l 才需要（大小、權限、時間）；單純 ls 可能不用
        │
  3. 排序 + 排版（userspace，ls 自己做）
     預設按檔名字母排序
     -t 按時間、-S 按大小、-r 反序...
     排版成多欄（互動）或單欄（管線）
        │
  → ls 的「排序」「顏色」「欄位」都是 ls 自己在 userspace 做的
    kernel 只給「檔名 + inode」，其餘是 ls 的功勞
```

關鍵認知：kernel 給的目錄內容是**無序的**（getdents64 回傳的順序不保證）。ls 看到的整齊排序、漂亮的顏色、對齊的欄位，全是 **ls 自己在 userspace 做的後處理**。理解這個，你會懂為什麼 `ls | cat` 的輸出和 `ls` 不同（管線裡 ls 改變行為）。

## ls 的關鍵選項

```bash
# 基本
ls                  # 簡單列出（多欄，按檔名排序）
ls -l               # 長格式（權限/owner/大小/時間/名字）
ls -a               # 全部（含 . .. 和隱藏檔，Ch 5）
ls -A               # 全部但不含 . ..（almost all）

# 大小
ls -lh              # human-readable 大小（K/M/G 而非 bytes）
ls -ls              # 顯示佔用的 block 數

# 排序
ls -lt              # 按 mtime 排序（最新在前）
ls -ltr             # 按 mtime 反序（最舊在前，最新在最後）★ 常用
ls -lS              # 按大小排序（最大在前）
ls -lX              # 按副檔名排序
ls -lU              # 不排序（按目錄原始順序，最快）

# 時間
ls -l --time-style=full-iso    # 完整時間（含秒、時區）
ls -lu              # 顯示 atime 而非 mtime（Ch 4）
ls -lc              # 顯示 ctime 而非 mtime

# 遞迴與篩選
ls -R               # 遞迴列出子目錄
ls -d */            # 只列出目錄本身（不進去）
ls -l *.txt         # 用 shell 萬用字元篩選（其實是 shell 展開，Ch 33）

# 顯示 inode
ls -li              # 顯示 inode 號（Ch 4）
```

最實用的組合：

```bash
ls -ltrh            # 長格式 + 時間排序 + 反序 + human-readable
#   → 最新的檔案在最下面（剛操作的在眼前），大小好讀
#   這是查「最近改了什麼」的黃金組合
```

> `ls -ltr` 是我最常用的組合——按時間排序、反序（最新在最下面，正好在你的視線焦點）。配合 `-h`（人類可讀大小）。當你想知道「這個目錄最近改了什麼」「最新的 log 是哪個」，`ls -ltrh` 一眼看到。記住這個組合，比反覆 `ls` 找最新檔案有效率得多。

## ls 在管線裡會變

一個反直覺的行為：`ls` 直接執行和在管線裡輸出不同：

```bash
cd ~/cmdlab
touch "file with spaces.txt" file1 file2

# 直接 ls：多欄，可能有顏色
ls
# file1  file2  'file with spaces.txt'   ← 多欄排版，特殊字元加引號

# 管線裡的 ls：單欄，無顏色
ls | cat
# file1
# file2
# file with spaces.txt                    ← 單欄，無引號
```

```
ls 為什麼在管線裡變：
  ls 偵測「輸出是不是終端機」（isatty，Ch 19）
    輸出到終端機（互動）：多欄、顏色、特殊字元加引號（給人看）
    輸出到管線/檔案：單欄、無顏色（給程式處理）
        │
  → ls 的「智慧」：知道你是人還是程式
  → 但這也是陷阱：腳本裡 parse ls 的輸出不可靠
    （格式會變，且檔名可能有空格/換行）
```

> **不要 parse ls 的輸出**——這是 shell 的著名反模式。ls 的輸出格式會變（互動 vs 管線）、檔名可能有空格甚至換行（會破壞按行/按空格切割）。腳本裡要列檔案用 glob（`for f in *.txt`，Ch 33）或 `find`（Ch 12），不要 `for f in $(ls)`。ls 是給「人看」的工具，它的輸出針對人優化（顏色、對齊），不適合程式 parse。這個教訓在 Part 8（scripting）會反覆強調。

## ls 的顏色

ls 的顏色不是隨意的——由 `LS_COLORS` 環境變數控制：

```bash
# 看顏色設定
echo $LS_COLORS | tr ':' '\n' | head
# di=01;34       ← 目錄：藍色粗體
# ln=01;36       ← symlink：青色
# ex=01;32       ← 可執行：綠色
# ...

# ls 的顏色靠 --color
ls --color=auto     # 輸出到終端機才上色（多數系統的 alias）
ls --color=never    # 不上色
ls --color=always   # 永遠上色（即使管線，會插入控制碼）

# 通常 ls 是 alias
type ls
# ls is aliased to 'ls --color=auto'   ← 很多發行版預設這個 alias
```

> ls 的顏色由 `LS_COLORS` 控制（哪種檔案什麼顏色），`--color=auto` 讓它「只在輸出到終端機時上色」（管線裡不上色，避免控制碼污染）。多數發行版把 `ls` alias 成 `ls --color=auto`（`type ls` 確認）。顏色是「給人看」的——`--color=always` 在管線裡會插入 ANSI 控制碼（`\033[01;34m`），破壞程式 parse。這再次說明 ls 是人類工具。

## stat：看單一檔案的完整資訊

`stat`（練習 A 用過）是「看一個檔案所有 inode 屬性」的利器：

```bash
stat file.txt           # 完整資訊（人類可讀）

# -c 格式字串：只取你要的（適合腳本）
stat -c %i file.txt     # inode 號
stat -c %s file.txt     # 大小（bytes）
stat -c %a file.txt     # 八進位權限
stat -c %A file.txt     # 符號權限
stat -c %U file.txt     # 擁有者名
stat -c %F file.txt     # 檔案類型
stat -c %y file.txt     # mtime
stat -c %h file.txt     # link count

# 組合
stat -c "%n: %s bytes, perms %a, inode %i" file.txt
# file.txt: 6 bytes, perms 644, inode 1234567

# 看檔案系統的 stat（-f）
stat -f /              # 檔案系統資訊（總空間、可用 inode...）
```

```
stat vs ls：
  ls：列「多個」檔案，排版給人看（顏色、欄位）
  stat：看「單一」檔案的完整 metadata，可格式化（適合腳本）
        │
  腳本裡要取某個屬性用 stat -c（穩定、明確）
  不要從 ls -l 的輸出切欄位（格式會變，Ch 11/Part 8 的雷）
```

## 故意弄壞：從 ls -l 切欄位取大小

```bash
cd ~/cmdlab
echo "data" > "my file.txt"     # 檔名有空格！

# 錯誤：從 ls -l 切第 5 欄取大小
ls -l "my file.txt" | awk '{print $5}'
# 5                              ← 這次「剛好」對（空格沒影響到第5欄）

# 但檔名有空格時，欄位數可能亂：
ls -l | grep "my file" | awk '{print $5}'
# ... 取到的可能是錯的欄位（檔名的空格讓 awk 多切了欄）

# 正確：用 stat（不受檔名格式影響）
stat -c %s "my file.txt"
# 5                              ← 永遠正確
```

從 `ls -l` 切欄位取資訊是脆弱的——檔名有空格時欄位數變動，切錯欄位。`stat -c %s` 直接取大小，不受檔名格式影響。這是「用對的工具」——取單一屬性用 stat，不要 parse ls。

## 踩雷集錦

1. **parse ls 的輸出**：ls 格式會變（互動 vs 管線），檔名可能有空格/換行。腳本用 glob 或 find，不要 `for f in $(ls)` 或切 ls 欄位

2. **以為 ls 的排序是 kernel 給的**：kernel 給無序的目錄內容（getdents64），排序是 ls 在 userspace 做的。`ls -U`（不排序）最快（跳過排序）

3. **--color=always 污染管線**：always 在管線插入 ANSI 控制碼，破壞 parse。用 auto（只在終端機上色）

4. **混淆 ls -t 的時間**：`-t` 預設按 mtime（改內容時間）。要按 atime 用 `-tu`，按 ctime 用 `-tc`。對應 Ch 4 的三時間戳

5. **ls -a 包含 . ..，ls -A 不包含**：腳本遍歷時 `-a` 會包含 `.` 和 `..`（容易造成遞迴問題）。用 `-A`（almost all，不含 . ..）

## 進階：ls 的效能與大目錄

ls 一個有海量檔案的目錄會慢，原因和 Ch 5 的底層有關：

```
ls 大目錄慢的原因：
  1. getdents64 讀全部 entry（檔案多就慢）
  2. ls -l 對每個檔案 statx 一次（海量 stat）
  3. ls 排序要把全部讀進記憶體再排（記憶體 + 排序成本）
        │
  優化：
  ls -U        不排序（跳過排序成本）
  ls -1        單欄（跳過多欄排版計算）
  ls -f        = -aU（不排序 + 含隱藏，最快）
  find . -maxdepth 1   有時比 ls 快（不排序、不 stat）
        │
  → 「ls 卡住」常是大目錄 + ls -l 的海量 stat
    用 ls -U 或 find 避開
```

```bash
# 在大目錄，這些差很多：
time ls /usr/bin > /dev/null         # 排序
time ls -U /usr/bin > /dev/null      # 不排序（較快）
time ls -lU /usr/bin > /dev/null     # -l 要 stat 每個（最慢）
```

> 「ls 一個目錄卡住」通常是「海量檔案 + ls -l」——ls -l 對每個檔案 stat 一次，幾十萬檔案就是幾十萬次 stat。如果你只要檔名（不要大小/權限），用 `ls -U`（不排序，最快）或 `ls -1`（單欄）。`ls -f`（= -aU）是最快的列檔名方式。這呼應 Ch 5：ls -l 慢是因為 statx 每個檔案。當你 ssh 到一個塞滿檔案的目錄，`ls` 半天不回，先試 `ls -U` 或 `ls | head`（雖然 head 不會讓 ls 提早停，但至少不排版全部）。

## 動手練習

1. 練 ls 組合：`ls -ltrh`（時間反序 + human-readable）看 ~/cmdlab 最近改的檔案。`ls -li` 看 inode（對照 Ch 4）。`ls -lu` vs `ls -lc` 看不同時間戳

2. 看 ls 在管線裡變：`ls` vs `ls | cat`，觀察多欄變單欄、顏色消失。理解 ls 偵測 isatty

3. 用 stat 取屬性：`stat -c "%n %s %a"` 對幾個檔案，對比從 ls -l 切欄位。建一個有空格的檔名，看 ls -l 切欄位出錯而 stat 正確

4. 看效能：`time ls -lU /usr/bin` vs `time ls -U /usr/bin`，理解 -l 的 stat 成本。在大目錄試 `ls -f`（最快）

## 本章重點整理

- ls 三階段：getdents64 讀目錄（Ch 5）+ statx 取 metadata（-l 才需要）+ userspace 排序排版
- 黃金組合 `ls -ltrh`（時間反序 + human-readable）看最近改了什麼；排序/顏色/欄位都是 ls 自己做的
- ls 偵測 isatty：終端機（多欄+顏色+引號，給人）vs 管線（單欄+無色，給程式）——不要 parse ls 輸出
- stat -c 格式字串取單一屬性（穩定、適合腳本），優於從 ls -l 切欄位（檔名有空格會錯）
- ls 大目錄慢是 -l 的海量 stat + 排序；ls -U（不排序）/-f（最快）避開

## 自我檢核

- [ ] 知道 `ls -ltrh` 各選項的意義，能說出它解決什麼需求
- [ ] 能解釋為什麼 ls 在管線裡輸出不同（isatty 偵測）
- [ ] 知道為什麼不該 parse ls 的輸出，腳本該用什麼（glob/find/stat）
- [ ] 能用 stat -c 取一個檔案的特定屬性
- [ ] 知道 ls 大目錄慢的原因（statx + 排序），以及怎麼優化（-U/-f）

## 延伸閱讀

### 官方文件

- **[GNU coreutils: ls invocation](https://www.gnu.org/software/coreutils/manual/html_node/ls-invocation.html)**
  - **讀哪裡**：sorting 和 general output formatting 那幾節
  - **學什麼**：ls 所有選項的完整說明；本章是精選
  - **前提**：本章

### 部落格 / 文章

- **[Don't parse ls!](https://mywiki.wooledge.org/ParsingLs)** — Greg's Wiki
  - **這篇說什麼**：為什麼不該 parse ls 的輸出，以及正確做法
  - **讀哪裡**：整頁
  - **為什麼值得讀**：把「不要 parse ls」這個重要原則講透，列出所有陷阱和替代方案

→ [Ch 11 cp/mv/rm 與底層 syscall](./11-cp-mv-rm.md)
