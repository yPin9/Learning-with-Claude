# Ch 12 — find：表達式引擎

> **目標**：精通 find——理解它不是「搜尋指令」而是「表達式引擎」（測試 + 動作 + 邏輯運算子）、各種測試條件（name/type/size/time/perm）、`-exec` 的兩種形式、以及為什麼 find 的語法這麼「奇怪」。find 是命令列最強大也最常被誤用的工具之一。

> **環境**：GNU findutils 4.x。BSD find 語法有差異（本章以 GNU find 為準）。

## 為什麼 find 的語法這麼奇怪？

新手用 find 常困惑：為什麼是 `find . -name "*.txt"` 而不是 `find "*.txt" .`？為什麼 `-exec` 後面要 `\;`？為什麼條件之間不用 `and`？

答案是：**find 不是「搜尋指令」，是「表達式引擎」**。它遍歷檔案樹，對每個檔案「評估一個表達式」（一串測試 + 動作 + 邏輯運算子）。理解這個模型，find 的「奇怪語法」就變成邏輯清晰的表達式。find 是命令列最強大的工具之一——學會它，你能做出 grep/ls 做不到的精確檔案篩選和批次操作。

## 先建立直覺：find 是「對每個檔案評估表達式」

```
find 的模型：

  find <起點> <表達式>
        │
  find 遍歷起點下的每個檔案/目錄
  對每個，從左到右「評估表達式」：
        │
  表達式 = 測試（test）+ 動作（action）+ 運算子（operator）
    測試：-name "*.txt"（檔名符合？）-type f（是檔案？）-size +1M（大於1M？）
    動作：-print（印出）-delete（刪除）-exec（執行命令）
    運算子：-a（and，預設）-o（or）! （not）( )（群組）
        │
  例：find . -name "*.txt" -type f -print
    對每個檔案：檔名符合 *.txt？ AND 是一般檔案？ → 都是，就 print
```

關鍵心智轉變：`find . -name "*.txt"` 不是「在 . 找 *.txt」，是「遍歷 . 下每個檔案，評估『檔名符合 *.txt 嗎』這個測試，符合的（預設）印出來」。find 是個小型的「對檔案樹求值的引擎」。理解這個，所有 find 語法都合理化。

## 測試條件（tests）

find 的測試是「對每個檔案問的問題」：

```bash
cd ~/cmdlab
mkdir -p proj/{src,docs}
touch proj/src/main.c proj/src/util.c proj/docs/readme.md proj/big.dat

# 按名字
find proj -name "*.c"           # 檔名符合 *.c（區分大小寫）
find proj -iname "*.MD"          # i = 不分大小寫
find proj -path "*/src/*"        # 完整路徑符合（含目錄）

# 按類型
find proj -type f                # 一般檔案
find proj -type d                # 目錄
find proj -type l                # symlink
#   f/d/l/c/b/p/s（對應 Ch 8 的檔案類型）

# 按大小
find proj -size +1M              # 大於 1MB（+ = 大於）
find proj -size -100k            # 小於 100KB（- = 小於）
find proj -size 50c              # 正好 50 bytes（c = bytes）

# 按時間（Ch 4 的三時間戳）
find proj -mtime -7              # mtime 在 7 天內（- = 之內）
find proj -mtime +30             # mtime 超過 30 天前
find proj -mmin -60              # mtime 在 60 分鐘內
find proj -newer reference.txt   # 比某檔案新

# 按權限/owner（Ch 7）
find proj -perm 644              # 權限正好 644
find proj -perm -u+w             # user 有寫權限（- = 至少有這些位）
find proj -user you              # 擁有者是 you
find proj -group developers      # 群組

# 按深度
find proj -maxdepth 1            # 只找第一層（不遞迴深入）
find proj -mindepth 2            # 至少第二層
```

> find 的測試對應前面學的所有 inode 屬性（Ch 4-7）——name、type、size、time、perm、owner。`-size +1M`（大於 1MB）、`-mtime -7`（7 天內改過）、`-type f`（一般檔案）是最常用的。注意時間的 `-`/`+`：`-mtime -7` 是「7 天**內**」，`-mtime +7` 是「7 天**前**」。`-maxdepth` 限制遞迴深度（不深入子目錄）——這很重要，避免 find 遍歷整個巨大的樹。

## 邏輯運算子

測試之間用邏輯運算子組合：

```bash
# AND（-a，預設，可省略）
find proj -type f -name "*.c"          # 是檔案 AND 名字 *.c（-a 省略）
find proj -type f -a -name "*.c"       # 同上（明確 -a）

# OR（-o）
find proj -name "*.c" -o -name "*.h"   # 名字 *.c OR *.h

# NOT（! 或 -not）
find proj -type f ! -name "*.c"        # 是檔案 AND 不是 *.c

# 群組（用 \( \)，括號要跳脫）
find proj \( -name "*.c" -o -name "*.h" \) -a -type f
#   ↑ (是 *.c 或 *.h) 而且 是檔案
#     括號要跳脫（\( \)），否則 shell 會解讀括號
```

```
運算子的優先序（像數學）：
  ! （not）最高
  -a （and）次之（且是預設）
  -o （or）最低
        │
  find -name a -o -name b -a -type f
  = -name a -o ( -name b -a -type f )   ← and 先綁
  要改變優先序用括號 \( \)
        │
  → find 是真正的「布林表達式引擎」
    有運算子、優先序、群組——像寫程式的條件
```

## 動作：-print、-delete、-exec

find 找到符合的檔案後，做什麼？預設 `-print`（印出），但能做更多：

```bash
# -print（預設）
find proj -name "*.c"            # 等於 find proj -name "*.c" -print

# -delete（刪除！危險）
find proj -name "*.tmp" -delete  # 刪除所有 .tmp（小心！）

# -exec：對每個檔案執行命令
find proj -name "*.c" -exec wc -l {} \;
#   {} 代表「當前檔案」，\; 結束 -exec
#   → 對每個 .c 檔案執行 wc -l（一個一個執行）

# -exec 的 + 形式（批次，更有效率）
find proj -name "*.c" -exec wc -l {} +
#   {} + 把所有檔案一次傳給 wc（一次執行，不是每個一次）
#   → 像 xargs（Ch 22），少 fork 很多次

# -ok：像 -exec 但每個操作前詢問
find proj -name "*.tmp" -ok rm {} \;
#   ok: rm proj/x.tmp? （刪每個前問你）
```

```
-exec 的兩種形式：
  -exec cmd {} \;
    對每個檔案執行一次 cmd（檔案多 = fork 多次，慢）
    find ... -exec wc -l {} \;
    → wc -l file1; wc -l file2; ...（N 次 wc）

  -exec cmd {} +
    把多個檔案一次傳給 cmd（少 fork，快）
    find ... -exec wc -l {} +
    → wc -l file1 file2 file3 ...（1 次 wc）
        │
  → 能用 + 就用 +（效率高很多，尤其檔案多時）
    但有些命令需要 \;（一次處理一個的語意）
```

> `-exec ... \;` vs `-exec ... +` 的差別是效能關鍵。`\;` 對每個檔案 fork 一次命令（1000 個檔案 = 1000 次 fork，慢，Ch 15 的 fork 成本）。`+` 把多個檔案一次傳給命令（1000 個檔案可能 1-2 次 fork，快）。能用 `+` 就用 `+`。這和 `xargs`（Ch 22）解決同樣的問題（避免海量 fork）。`{}` 是「當前檔案」的佔位符，`\;` 或 `+` 結束 -exec（`;` 要跳脫成 `\;`，否則 shell 解讀分號）。

## find 底層：遍歷 + 評估

```bash
# find 底層是遍歷目錄樹（getdents64）+ stat 每個檔案
strace -e openat,getdents64,newfstatat find ~/cmdlab/proj -name "*.c" 2>&1 | head
# openat(... "proj" O_DIRECTORY ...) = 3
# getdents64(3, ...)                          ← 讀目錄（Ch 5）
# openat(... "proj/src" O_DIRECTORY ...) = 4  ← 進子目錄
# getdents64(4, ...)                          ← 讀子目錄
# newfstatat(...)                             ← stat 每個檔案（檢查條件）
# ...
```

```
find 的底層流程（Ch 5 的 getdents64 + Ch 4 的 stat）：
  1. 從起點開始，getdents64 讀目錄（Ch 5）
  2. 對每個 entry：
     - stat 它（取得 type/size/time，Ch 4）
     - 評估表達式（符合測試嗎？）
     - 符合 → 執行動作（print/exec/delete）
  3. 遇到子目錄 → 遞迴進去（除非 -maxdepth 限制）
        │
  → find = 遞迴遍歷 + 對每個檔案 stat + 評估表達式 + 動作
    這就是為什麼 find 大目錄樹慢（海量 getdents + stat）
```

## 故意弄壞：find 的常見錯誤

```bash
cd ~/cmdlab
# 錯誤一：忘記引號，shell 先展開萬用字元
find . -name *.txt           # 如果當前目錄有 .txt 檔，shell 展開 *.txt！
#   → find . -name a.txt b.txt（語法錯，find 困惑）
find . -name "*.txt"         # 正確：引號讓 find 自己處理萬用字元

# 錯誤二：-delete 放錯位置（先刪了才篩選）
find . -delete -name "*.tmp" # 危險！-delete 在前，會刪所有東西！
#   表達式從左到右評估，-delete 先執行（刪掉），-name 才測試（太遲）
find . -name "*.tmp" -delete # 正確：先篩選，再刪

# 錯誤三：-exec 的 {} 沒跳脫分號
find . -name "*.c" -exec wc -l {} ;   # shell 把 ; 當命令分隔！
find . -name "*.c" -exec wc -l {} \;  # 正確：跳脫 \;
```

這三個是 find 最常見的災難：(1) 萬用字元沒引號 → shell 先展開（Ch 33）；(2) `-delete` 放錯位置 → 先刪後篩（刪光！）；(3) `;` 沒跳脫 → shell 解讀。記住：find 的萬用字元要引號、動作放表達式最後、`\;` 要跳脫。

## 踩雷集錦

1. **萬用字元沒引號**：`find . -name *.txt` 的 `*.txt` 會被 shell 先展開（Ch 33）。一定加引號 `"*.txt"` 讓 find 自己處理

2. **-delete 放錯位置**：find 從左到右評估。`-delete` 放前面會先刪。永遠把動作（-delete/-exec）放表達式最後（篩選之後）

3. **`;` 沒跳脫**：`-exec cmd {} ;` 的 `;` 被 shell 解讀。用 `\;`（跳脫）或 `';'`

4. **-mtime 的方向搞反**：`-mtime -7` 是「7 天內」，`+7` 是「7 天前」。搞反會找到相反的檔案

5. **沒用 -maxdepth 遍歷整個樹**：find 預設遞迴到最深。在大樹（如 `find /`）會很慢。用 `-maxdepth` 限制，或從更精確的起點開始

## 進階：find 的效能與替代工具

find 遍歷大樹慢（海量 getdents + stat）。有更快的替代和優化：

```
find 的優化與替代：
  優化 find：
    -maxdepth N      限制深度（不深入）
    把最便宜的測試放前面（-name 比 -size 便宜，先 -name 篩掉大部分）
    -exec ... +      批次（少 fork）
        │
  替代工具：
    locate / mlocate：查預建的索引資料庫（瞬間，但資料可能過時）
      locate "*.conf"   → 從 updatedb 建的索引查（不即時遍歷）
    fd（現代工具）：更快、語法更友善的 find
      fd "\.txt$"       → 比 find 快（平行、智慧忽略 .git 等）
        │
  → 即時精確搜尋用 find；快速查已知檔名用 locate；
    日常用 fd（如果裝了）
```

```bash
# locate：查索引（快，但要 updatedb 更新過）
sudo updatedb              # 更新索引（通常 cron 自動跑）
locate passwd              # 瞬間查到（從索引，不遍歷）

# fd（如果裝了，現代 find 替代）
fd "\.c$" ~/cmdlab         # 比 find 快、語法簡單、自動忽略 .gitignore
```

> find 的效能瓶頸是「遍歷 + stat 每個檔案」。優化：`-maxdepth` 限深度、把便宜的測試（`-name`）放前面（先篩掉大部分，後面的昂貴測試做得少）、`-exec +` 批次。替代工具：`locate`（查預建索引，瞬間但可能過時——適合「我知道大概檔名」）、`fd`（現代 find，平行 + 智慧忽略，日常更好用）。但 find 的精確表達式能力（複雜的 -perm/-time/-exec 組合）仍不可替代——複雜批次操作還是 find。理解這個分工：日常快查用 fd/locate，精確批次操作用 find。

## 動手練習

1. 練表達式：在 ~/cmdlab/proj 找 `*.c`、找一般檔案、找大於某大小、找 7 天內改過的。組合 `-type f -name "*.c"`、`\( -name "*.c" -o -name "*.h" \)`

2. 練 -exec：`find . -name "*.c" -exec wc -l {} \;`（每個一次）vs `{} +`（批次）。`time` 比較兩者（檔案多時 + 快很多）

3. 看 find 底層：`strace -e getdents64,newfstatat find ~/cmdlab -name "*.c"`，看它遞迴讀目錄 + stat 每個。理解為什麼大樹慢

4. 跑「故意弄壞」：萬用字元沒引號（看 shell 展開搞亂）、`-delete` 放錯位置（在 sandbox 測，先建假檔案）、`;` 沒跳脫。理解三個經典錯誤

## 本章重點整理

- find 是「表達式引擎」不是搜尋指令：遍歷檔案樹，對每個檔案評估「測試 + 動作 + 運算子」的表達式
- 測試對應 inode 屬性（Ch 4-7）：-name/-type/-size/-mtime/-perm/-user；運算子 -a（預設）/-o/!/\( \)
- 動作：-print（預設）/-delete/-exec；`-exec {} \;`（每個一次，慢）vs `-exec {} +`（批次，快）
- find 底層 = 遞迴 getdents64 + stat 每個 + 評估表達式 + 動作；大樹慢（海量遍歷+stat）
- 常見錯誤：萬用字元沒引號、-delete 放錯位置、`;` 沒跳脫；替代：locate（索引）、fd（現代）

## 自我檢核

- [ ] 能解釋 find 是「表達式引擎」，以及為什麼語法「奇怪」（測試+動作+運算子）
- [ ] 能組合複雜的 find 表達式（多條件 AND/OR、括號群組）
- [ ] 知道 `-exec {} \;` 和 `-exec {} +` 的差別（效能）
- [ ] 知道 find 的三個經典錯誤（萬用字元引號、-delete 位置、; 跳脫）
- [ ] 知道 find、locate、fd 各自適合什麼場景

## 延伸閱讀

### 官方文件

- **[GNU findutils manual](https://www.gnu.org/software/findutils/manual/html_mono/find.html)**
  - **讀哪裡**：Finding Files（測試和動作）、Combining Primaries（運算子）
  - **學什麼**：find 所有測試/動作/運算子的完整說明
  - **前提**：本章

- **[find(1) man page](https://man7.org/linux/man-pages/man1/find.1.html)**
  - **讀哪裡**：EXPRESSION 那節（tests/actions/operators）
  - **學什麼**：find 表達式的完整參考
  - **前提**：本章

### 部落格 / 文章

- **[The find command is your friend](https://www.redhat.com/sysadmin/find-command-linux)** 或 find 實戰文
  - **這篇說什麼**：find 的實用場景和組合技巧
  - **讀哪裡**：-exec 和複雜表達式那部分
  - **為什麼值得讀**：把 find 的表達式能力用實際場景展示

→ [Ch 13 檔案內容工具](./13-file-content-tools.md)
