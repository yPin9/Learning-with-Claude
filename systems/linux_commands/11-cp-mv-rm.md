# Ch 11 — cp/mv/rm 與底層 syscall

> **目標**：理解三個最危險也最常用的檔案操作底層在做什麼——cp 的讀寫迴圈、mv 的 rename（同檔案系統超快、跨檔案系統其實是 copy+delete）、rm 的 unlink、以及為什麼這些操作有時快有時慢、刪了能不能救。用 strace 看穿它們。

> **環境**：GNU coreutils 9.x。承接 Ch 4-6（inode/目錄/link，rm 的 unlink 是 Ch 6 的延伸）。

## 為什麼這三個指令值得拆到 syscall？

cp、mv、rm 是你每天用的，但它們的底層行為有很多反直覺之處：為什麼 `mv` 同磁碟瞬間完成但跨磁碟很慢？為什麼 `rm` 大檔案瞬間完成（不像「刪除幾 GB」）？刪錯了能不能救？

這些問題的答案都在 syscall 層。`mv` 同磁碟是 `rename`（改目錄表，瞬間），跨磁碟是 copy+delete（真的搬資料，慢）。`rm` 是 `unlink`（移除檔名，Ch 6），不真的清資料。理解這些，你能預測操作的快慢、知道刪錯的補救可能性、避免災難性的誤操作。

## 先建立直覺：三個操作的本質

```
cp（複製）：
  讀來源 + 寫一份新的（新 inode，Ch 4）
  → 真的複製資料，大檔案慢

mv（移動/改名）：
  同檔案系統：rename（只改目錄表，Ch 5）→ 瞬間
  跨檔案系統：copy + delete（真的搬）→ 慢

rm（刪除）：
  unlink（移除檔名 + link count -1，Ch 6）→ 瞬間
  不真的清資料（資料還在磁碟，直到被覆蓋）
```

關鍵洞察：只有 cp（和跨檔案系統的 mv）真的搬資料；mv 同磁碟和 rm 只動「目錄表」（metadata），不碰資料本體。這解釋了它們的快慢差異，以及刪錯能不能救。

## cp：讀 + 寫

```bash
cd ~/cmdlab
echo "original data" > src.txt
cp src.txt dst.txt

# 看 cp 底層
strace -e openat,read,write cp src.txt dst.txt 2>&1 | grep -E "src|dst|read|write" | head
# openat(AT_FDCWD, "src.txt", O_RDONLY) = 3        ← 開來源讀
# openat(AT_FDCWD, "dst.txt", O_WRONLY|O_CREAT|O_TRUNC, 0644) = 4  ← 開目標寫
# read(3, "original data\n", ...) = 14             ← 從來源讀
# write(4, "original data\n", 14) = 14             ← 寫到目標
# ...（大檔案會有很多 read/write 迴圈）
```

```
cp 的本質：
  1. open 來源（讀）+ open 目標（寫，建立新檔案 = 新 inode）
  2. 迴圈：read 來源 → write 目標（一塊一塊搬）
  3. 複製 metadata（cp -p 保留權限/時間）
        │
  → cp 建立「新的 inode」（Ch 4）
    來源和目標是「兩份獨立的資料」（不像 hard link 共享 inode，Ch 6）
  → 大檔案慢（真的搬全部資料）
```

cp 的重要選項：

```bash
cp -p src dst        # preserve：保留權限、時間戳、owner
cp -r srcdir dstdir  # recursive：複製目錄（含子目錄）
cp -a srcdir dstdir  # archive：-r + 保留所有屬性 + 不跟隨 symlink（備份用）
cp -i src dst        # interactive：目標存在時詢問（防覆蓋）
cp -u src dst        # update：只在來源較新時複製
cp --reflink src dst # CoW 複製（btrfs/XFS，瞬間，共享資料直到修改）
```

> `cp -a`（archive）是備份的黃金選項——遞迴 + 保留所有屬性（權限/時間/owner）+ 正確處理 symlink（複製連結本身而非目標）。普通 `cp -r` 會跟隨 symlink、不保留某些屬性，做備份會出錯。`--reflink`（CoW，btrfs/XFS 支援）是現代亮點——它「複製」時不真的搬資料，而是共享（copy-on-write），直到其中一份被修改才真的分裂。這讓「複製大檔案」瞬間完成（在支援的檔案系統上）。理解 cp 建立新 inode，你會懂為什麼 cp 比 hard link（Ch 6，共享 inode）佔更多空間。

## mv：rename 或 copy+delete

mv 的行為取決於來源和目標是否在同一檔案系統：

```bash
cd ~/cmdlab
echo "data" > file.txt

# 同檔案系統的 mv：rename（瞬間）
strace -e rename,renameat,renameat2 mv file.txt renamed.txt 2>&1 | grep rename
# renameat2(AT_FDCWD, "file.txt", AT_FDCWD, "renamed.txt", 0) = 0
#   ↑ 只呼叫 rename！沒有 read/write
#     mv 同磁碟 = 改目錄表（檔名 file.txt → renamed.txt），瞬間
```

```
mv 的兩種行為：

  同檔案系統（rename）：
    只改目錄表的 entry（Ch 5）：
      "file.txt" → inode 123  變成  "renamed.txt" → inode 123
    inode 不變、資料不動！
    → 瞬間完成（即使是 100GB 的檔案，mv 同磁碟也是瞬間）

  跨檔案系統（copy + delete）：
    rename 失敗（EXDEV：cross-device link）
    mv 退而求其次：cp 到目標 + rm 來源
    → 真的搬資料，大檔案慢
        │
  → mv 同磁碟快得驚人（只改 metadata）
    mv 跨磁碟慢（真的複製）
```

```bash
# 跨檔案系統的 mv（如果 /tmp 是不同檔案系統，如 tmpfs）
echo "data" > ~/cmdlab/big.txt
strace -e rename,openat,read,write,unlink mv ~/cmdlab/big.txt /tmp/big.txt 2>&1 | head
# renameat2(...) = -1 EXDEV (Invalid cross-device link)  ← rename 失敗（跨檔案系統）
# openat(... "/tmp/big.txt" ...) = 4                       ← 退而 copy
# read(...) write(...)                                     ← 真的搬資料
# unlink("...big.txt")                                     ← 刪來源
```

> **mv 同磁碟為什麼瞬間**：因為它只是 `rename`——改目錄表的一個 entry（檔名變了，inode 號不變，資料一動不動）。即使是 100GB 的檔案，同磁碟 mv 也是瞬間（沒搬資料）。**mv 跨磁碟為什麼慢**：rename 不能跨檔案系統（EXDEV，呼應 Ch 6 的 hard link 限制），mv 只好 copy+delete（真搬）。這解釋了一個常見觀察：把檔案在同一磁碟移動瞬間完成，移到 USB/另一顆磁碟就要等。理解這個，你能預測 mv 的快慢，並知道「跨磁碟移動大檔案」需要時間（且中途中斷可能留下半個檔案）。

## rm：unlink

rm 是 Ch 6 的 unlink 的直接應用：

```bash
cd ~/cmdlab
echo "data" > victim.txt

strace -e unlink,unlinkat rm victim.txt 2>&1 | grep unlink
# unlinkat(AT_FDCWD, "victim.txt", 0) = 0
#   ↑ rm 就是 unlink（移除檔名 + link count -1，Ch 6）
```

```
rm 的本質（Ch 6）：
  rm = unlink = 移除目錄表的 entry + inode link count -1
  資料本體不被清除！（只是「失去了名字」）
        │
  inode 釋放條件（Ch 6）：link count = 0 且無 process 開著
    → 條件滿足，inode 和資料 block 被標記為「可重用」
    → 但資料還在磁碟上，直到被新資料覆蓋
        │
  → rm 瞬間完成（即使大檔案，只改 metadata）
  → 刪錯了「可能」能救（資料還在，直到被覆蓋）
```

rm 的危險選項：

```bash
rm file.txt          # 刪一個檔案
rm -r dir            # 遞迴刪目錄（含內容）
rm -f file           # force（不問、忽略不存在的）
rm -i file           # interactive（刪前詢問）
rm -rf dir           # 遞迴 + 強制（最危險的組合）

# 危險示範（永遠別這樣）：
# rm -rf /            # 刪整個系統（現代 rm 有 --preserve-root 保護根）
# rm -rf $UNDEFINED/  # 變數沒定義 → rm -rf / （腳本災難！Part 8）
```

> **rm -rf 的災難**：`rm -rf $DIR/` 如果 `$DIR` 沒定義（空字串），變成 `rm -rf /`——刪整個系統。這是 shell 腳本最著名的災難（Part 8 會強調）。現代 rm 有 `--preserve-root`（預設保護 `/`），但 `rm -rf /home/$USER/$SUBDIR` 如果 `$SUBDIR` 空，變成 `rm -rf /home/$USER/`（刪整個家目錄，沒有保護）。**rm 沒有資源回收桶、沒有 undo**。養成習慣：rm 前確認路徑、危險操作用 `-i`、腳本裡的 rm 路徑用引號且檢查變數非空。

## 刪錯能不能救

```
rm 後資料還在嗎？
  rm = unlink，資料本體沒被清（只失去名字）
  inode 和資料 block 標記為「可重用」，但內容還在
        │
  救援的可能性：
  1. 如果有 process 還開著它（Ch 6）：
     /proc/<pid>/fd/<n> 還能讀到 → 立刻 cp 出來救
  2. 如果沒 process 開著但還沒被覆蓋：
     用 extundelete、photorec 等工具「可能」救回
     （但任何寫入都可能覆蓋它，要立刻停止使用該檔案系統）
  3. 如果已被覆蓋：
     救不回（資料真的沒了）
        │
  → 刪錯立刻停止寫入該檔案系統，增加救援機會
  → 但最可靠的是：備份。rm 沒有後悔藥
```

```bash
# 救援場景：檔案被 rm 但 process 還開著（Ch 6 的延伸）
echo "important" > important.txt
tail -f important.txt &
TAILPID=$!
rm important.txt              # 刪了名字，但 tail 開著
# 從 /proc 救回！
cp /proc/$TAILPID/fd/3 recovered.txt 2>/dev/null || \
  cat /proc/$TAILPID/fd/*    # 找到 tail 開的那個 fd
kill $TAILPID
```

> 「rm 後能不能救」取決於資料有沒有被覆蓋（rm 只移除名字，資料還在直到被新資料蓋掉）。最好救的情況：有 process 還開著它（`/proc/<pid>/fd/` 還能讀，Ch 6）——立刻 cp 出來。其次：沒人開著但沒被覆蓋——用 extundelete 等工具「可能」救（但要立刻停止寫入，否則新資料覆蓋它）。最糟：已被覆蓋——救不回。**結論：rm 沒有後悔藥，備份才是王道。** 這也是為什麼很多人 alias `rm` 成移到回收桶的工具（如 trash-cli），給自己一層保護。

## 故意弄壞：cp 覆蓋 + mv 覆蓋（無聲災難）

```bash
cd ~/cmdlab
echo "important content" > important.txt
echo "junk" > junk.txt

# cp 無聲覆蓋（important.txt 的內容沒了！）
cp junk.txt important.txt     # 沒有任何警告
cat important.txt             # junk（important 的內容被覆蓋，無法復原）

# 防護：cp -i（覆蓋前詢問）
echo "data" > a.txt; echo "other" > b.txt
cp -i a.txt b.txt            # cp: overwrite 'b.txt'? （問你）

# mv 同樣會無聲覆蓋
mv junk.txt important.txt    # 也是無聲覆蓋目標
```

cp 和 mv **無聲覆蓋**目標檔案——沒有警告，目標原本的內容直接沒了（且不像 rm 還可能救，覆蓋是真的寫入新資料）。這是常見的資料丟失原因。防護：`-i`（覆蓋前詢問）、`-n`（不覆蓋存在的）、或養成 `cp -i`/`mv -i` 的習慣（甚至 alias）。

## 踩雷集錦

1. **以為 mv 一定瞬間**：mv 同檔案系統是 rename（瞬間），跨檔案系統是 copy+delete（慢）。移動大檔案到 USB/別的磁碟要等

2. **以為 rm 清除資料**：rm 是 unlink（移除名字），資料還在直到被覆蓋。「刪了還能救」和「rm 不安全清除敏感資料」都源於此（要安全清除用 shred）

3. **cp/mv 無聲覆蓋**：目標存在時直接覆蓋，沒警告。重要操作用 `-i`（詢問）或 `-n`（不覆蓋）

4. **rm -rf 配未定義變數**：`rm -rf $DIR/` 若 `$DIR` 空 = `rm -rf /...`。腳本裡 rm 路徑要引號 + 檢查變數非空（Part 8）

5. **cp -r 跟隨 symlink 造成意外**：`cp -r` 會跟隨 symlink（複製目標內容）。備份用 `cp -a`（不跟隨，複製連結本身）

## 進階：原子性與 rename 的妙用

`rename` 有個重要特性——**原子性**，這讓它成為「安全更新檔案」的關鍵技巧：

```
rename 的原子性：
  rename 是原子操作（要嘛成功、要嘛沒發生，沒有中間狀態）
        │
  安全更新檔案的模式（atomic write）：
    1. 寫新內容到「臨時檔案」（同一目錄）
    2. rename 臨時檔案 → 目標檔案
        │
  為什麼這樣安全：
    - rename 原子：目標檔案要嘛是舊的、要嘛是新的完整版
    - 沒有「寫到一半」的中間狀態（不會看到半個檔案）
    - 如果中途崩潰，目標檔案還是舊的完整版（沒被破壞）
        │
  → 很多程式這樣更新設定檔/資料（先寫 tmp 再 rename）
    對比「直接覆蓋」：覆蓋到一半崩潰 = 半個檔案（損壞）
```

```bash
# atomic write 模式（很多工具/程式用）
echo "new config" > config.txt.tmp     # 寫臨時檔
mv config.txt.tmp config.txt           # 原子 rename（瞬間替換）
#   ↑ config.txt 要嘛是舊的、要嘛是新的，沒有半個的中間狀態
```

> rename 的原子性是「安全更新檔案」的核心技巧。直接覆蓋（`echo new > file`）如果寫到一半崩潰，會留下半個檔案（損壞）。**先寫臨時檔再 rename** 則安全——rename 原子，目標檔案要嘛是舊的完整版、要嘛是新的完整版，沒有中間狀態。資料庫、設定管理工具、很多程式都用這個模式（你 strace 一個編輯器存檔，常看到 `write tmp + rename`）。這呼應 debian_packaging 課程的 conffile 處理、和很多「為什麼存檔不會壞」的設計。理解 rename 原子性，你能寫出更 robust 的腳本（Part 8）。

## 動手練習

1. 看 cp 底層：`strace -e openat,read,write cp src dst`，看開來源讀、開目標寫、read/write 迴圈。建一個大檔案（dd）看 cp 的 read/write 次數

2. 看 mv 的兩種行為：同磁碟 `mv`（strace 看只有 rename，瞬間）；跨磁碟（mv 到 /tmp 如果是 tmpfs，看 EXDEV + copy+delete）

3. 看 rm = unlink：`strace -e unlinkat rm file`。理解 rm 不清資料。試 Ch 6 的「rm 後從 /proc/fd 救回」

4. 跑「故意弄壞」：cp/mv 無聲覆蓋（重要檔案內容被蓋）。用 `-i` 防護。練 atomic write（寫 tmp + rename）

## 本章重點整理

- cp = 讀 + 寫（建新 inode，真搬資料，大檔案慢）；`cp -a` 備份黃金選項，`--reflink` CoW 瞬間複製
- mv 同檔案系統 = rename（只改目錄表，瞬間）；跨檔案系統 = copy+delete（EXDEV，真搬，慢）
- rm = unlink（移除名字 + link count -1，Ch 6），不清資料；rm 瞬間、無 undo、刪錯可能能救（資料未覆蓋前）
- cp/mv 無聲覆蓋目標；用 -i（詢問）/-n（不覆蓋）防護；rm -rf 配未定義變數是腳本災難
- rename 原子性 → atomic write 模式（寫 tmp + rename）安全更新檔案，避免半個檔案的損壞

## 自我檢核

- [ ] 能解釋 mv 同磁碟瞬間、跨磁碟慢的原因（rename vs copy+delete）
- [ ] 知道 rm 是 unlink，不清資料，以及「刪錯能不能救」取決於什麼
- [ ] 知道 cp/mv 無聲覆蓋，以及怎麼防護
- [ ] 能解釋 rm -rf 配未定義變數的災難
- [ ] 能解釋 rename 原子性，以及 atomic write 模式為什麼安全

## 延伸閱讀

### 書籍

- **《The Linux Programming Interface》— Ch 18 (rename, link, unlink)** — Michael Kerrisk
  - **讀哪幾章**：Ch 18 的 rename、unlink（含原子性）
  - **這本書的定位**：這些 syscall 的權威來源
  - **前提**：本章 + Ch 6

### 官方文件

- **[rename(2)](https://man7.org/linux/man-pages/man2/rename.2.html)** man page
  - **讀哪裡**：DESCRIPTION（原子性）、ERRORS（EXDEV）
  - **學什麼**：rename 的原子性保證、跨檔案系統的限制
  - **前提**：本章

### 部落格 / 文章

- **[Files are hard](https://danluu.com/file-consistency/)** — Dan Luu
  - **這篇說什麼**：檔案操作的一致性陷阱（atomic write、fsync、崩潰一致性）
  - **讀哪裡**：rename 和 atomic write 那部分
  - **為什麼值得讀**：把本章的 rename 原子性放進「崩潰一致性」的大圖，理解為什麼可靠地寫檔案這麼難

→ [Ch 12 find：表達式引擎](./12-find.md)
