# Ch 13 — 檔案內容工具

> **目標**：掌握查看檔案內容的工具——cat（與它的誤用）、head/tail（與 tail -f 的底層）、less（pager 的設計）、od/xxd（看二進位）、以及 wc。理解這些工具的底層（怎麼讀檔、tail -f 怎麼追蹤）和適用場景。

> **環境**：GNU coreutils 9.x，less。承接 Ch 4（檔案讀取）、Ch 8（特殊檔案）。

## 為什麼看檔案內容也要懂底層？

「看檔案內容不就 `cat`？」但 cat 一個 10GB 的 log 會洗版（甚至卡住終端機）、cat 一個二進位檔會噴亂碼搞壞終端機、`tail -f` 怎麼能「即時」看 log 增長？這些都需要選對工具、懂底層。

這章把「看檔案」的工具講清楚：cat 適合什麼（小檔案、管線）、head/tail 怎麼只看一部分、less 為什麼能看大檔案不卡、tail -f 怎麼追蹤增長、od/xxd 怎麼看二進位。選對工具讓你查檔案高效又安全。

## 先建立直覺：不同工具對應不同需求

```
看檔案內容的工具地圖：

  小檔案、全部、要進管線     → cat
  只看開頭 N 行             → head
  只看結尾 N 行             → tail
  即時追蹤增長（log）       → tail -f
  大檔案、互動瀏覽          → less（pager）
  二進位檔                 → od / xxd / hexdump
  只要統計（行數/字數）     → wc
        │
  → 用錯工具的後果：
    cat 大檔案 → 洗版/卡終端機
    cat 二進位 → 亂碼搞壞終端機
    用 cat 看大 log 找東西 → 應該用 less + 搜尋
```

關鍵：cat 不是「看檔案的萬用工具」。它適合小檔案和管線。大檔案用 less（能翻頁、搜尋、不卡），追蹤用 tail -f，二進位用 xxd。選對工具是這章的核心。

## cat：concatenate，不是「看檔案」

cat 的本名是 **concatenate**（串接）——它的設計目的是「串接多個檔案輸出」，不是「看檔案」：

```bash
cd ~/cmdlab
echo "line 1" > a.txt
echo "line 2" > b.txt

# cat 的本意：串接多個檔案
cat a.txt b.txt          # 兩個檔案的內容接在一起輸出

# 看單一檔案（cat 的常見用法，但不是它的本意）
cat a.txt                # 輸出 a.txt（小檔案 OK）

# cat 底層：open + read + write
strace -e openat,read,write cat a.txt 2>&1 | grep -E "a.txt|read|write"
# openat(... "a.txt" O_RDONLY) = 3
# read(3, "line 1\n", ...) = 7      ← 讀
# write(1, "line 1\n", 7) = 7       ← 寫到 stdout（fd 1）
```

```
cat 的問題（為什麼不該用 cat 看大檔案）：
  cat 把整個檔案讀出來「全部」寫到 stdout
        │
  大檔案（10GB log）：
    → 洗版（10GB 的文字捲過終端機）
    → 可能卡住（終端機處理不了這麼多輸出）
        │
  二進位檔：
    → 噴出控制字元，可能搞壞終端機顯示
    → （如果搞壞了，打 reset 恢復）
        │
  → cat 適合：小檔案、管線（cat file | grep）
    不適合：大檔案、二進位（用 less / xxd）
```

> **「Useless Use of Cat」（UUOC）** 是 shell 的著名反模式——`cat file | grep pattern` 其實不用 cat，`grep pattern file` 就好（grep 能直接讀檔）。cat 在這裡是多餘的（多一個 process、多一層管線）。cat 的正當用途是「串接多個檔案」（`cat *.txt`）或「stdin 來源」（`cat | program`）。看單一檔案找東西，直接用 grep/less/head。記住：cat 是串接工具，不是萬用的「看檔案」工具。

## head / tail：只看一部分

```bash
# head：看開頭
head file.txt            # 前 10 行（預設）
head -n 20 file.txt      # 前 20 行
head -c 100 file.txt     # 前 100 bytes

# tail：看結尾
tail file.txt            # 後 10 行
tail -n 20 file.txt      # 後 20 行
tail -n +5 file.txt      # 從第 5 行到結尾（+ 表示「從第 N 行開始」）

# 組合：看中間某幾行
head -n 20 file.txt | tail -n 5   # 第 16-20 行（前 20 的後 5）
sed -n '16,20p' file.txt          # 同上（sed 更直接，Ch 25）
```

> head/tail 對「大檔案只看一部分」很有用——不像 cat 讀全部。`head -n 100 huge.log` 只讀前 100 行（不會洗版）。`tail` 看結尾（log 最新的在結尾）。`tail -n +5`（從第 5 行開始）和 `tail -n 5`（最後 5 行）差一個 `+`，意義不同。head/tail 底層也是 read，但 head 讀夠就停（不讀完整個檔案），tail 則要找到結尾（大檔案 tail 可能要 seek）。

## tail -f：即時追蹤增長

`tail -f`（follow）是看 log 的神器——它「即時」顯示檔案新增的內容：

```bash
# 監看 log 即時增長
tail -f /var/log/syslog          # 持續顯示新增的行（按 Ctrl-C 停）

# 多檔案
tail -f /var/log/*.log           # 同時追蹤多個 log

# -F：即使檔案被 rotate（重建）也繼續追蹤
tail -F /var/log/app.log         # F 處理 log rotation（檔案被換掉）
```

```
tail -f 怎麼做到「即時」：
  傳統方式：定期 stat 檔案看大小變了沒，變了就讀新增的部分
    （poll：每隔一段時間檢查）
        │
  現代 tail -f 用 inotify（kernel 的檔案變動通知）：
    向 kernel 註冊「監看這個檔案」
    檔案被寫入 → kernel 通知 tail → tail 讀新增的部分
    → 不用一直 poll，省 CPU，更即時
        │
  -f vs -F：
    -f：追蹤這個「inode」（檔案被 rotate 換成新 inode 後就追蹤不到舊的）
    -F：追蹤這個「檔名」（rotate 後重新開啟新檔案，繼續追蹤）
        │
  → log rotation 環境用 -F（檔案會被換掉）
```

```bash
# 看 tail -f 用 inotify
strace -e inotify_add_watch tail -f a.txt 2>&1 &
sleep 1; echo "new line" >> a.txt; sleep 1; kill %1 2>/dev/null
# inotify_add_watch(...) ← 註冊監看
```

> `tail -f` 是 SysOps 看 log 的日常工具。現代它用 **inotify**（kernel 的檔案變動通知機制）——不用一直 poll 檔案，而是 kernel 在檔案變動時主動通知 tail。`-f` vs `-F` 的差別在 log rotation：log 工具常把 `app.log` rotate 成 `app.log.1` 並建新的 `app.log`。`-f` 追蹤舊 inode（rotate 後看不到新 log），`-F` 追蹤檔名（rotate 後重開新檔案繼續看）。**監看會被 rotate 的 log 用 `-F`**。inotify 也是 IDE/檔案同步工具偵測檔案變動的機制。

## less：大檔案的互動瀏覽

`less` 是 pager（分頁器）——互動地瀏覽大檔案，不會洗版：

```bash
less /var/log/syslog     # 互動瀏覽（大檔案也不卡）

# less 裡的操作：
#   空白 / f    下一頁
#   b           上一頁
#   /pattern    向下搜尋
#   ?pattern    向上搜尋
#   n / N       下/上一個搜尋結果
#   g / G       到開頭/結尾
#   q           離開
#   F           像 tail -f（即時追蹤，按 Ctrl-C 回到瀏覽）
```

```
less 為什麼能看大檔案不卡（cat 卻會）：
  cat：讀「全部」寫到 stdout（10GB 全噴出來）
        │
  less：只讀「你正在看的那一頁」
    用 seek 跳到檔案的某個位置，只讀那部分
    你往下翻 → 才讀下一部分
    → 不管檔案多大，只佔一頁的記憶體
        │
  → less is more（比 more 強的 pager）
    能向上翻、搜尋、即時追蹤（F）
    看大 log、找東西用 less，不要 cat
```

> `less` 的名字是「less is more」的雙關（它是 `more` 這個舊 pager 的增強版，但功能更多）。它能看任意大的檔案不卡——因為它只讀「你正在看的部分」（用 seek 跳到檔案位置），不像 cat 讀全部。`less` 的搜尋（`/pattern`）讓你在大 log 裡快速找東西。`F`（大寫）讓 less 進入「tail -f 模式」（即時追蹤），按 Ctrl-C 回到瀏覽——這比 tail -f 更靈活（能即時看又能往回翻）。**看大檔案、在 log 裡找東西，用 less，不要 cat。**

## od / xxd：看二進位

文字工具（cat/less）看二進位會噴亂碼。看二進位用 **xxd**/od（hex dump）：

```bash
# xxd：hex + ASCII 對照（最常用）
echo "Hello" | xxd
# 00000000: 4865 6c6c 6f0a            Hello.
# │位移      │ hex（每個 byte）      │ ASCII（可印字元，其餘是 .）

# 看一個檔案的前幾 bytes（如判斷檔案類型的 magic number）
xxd /bin/ls | head -1
# 00000000: 7f45 4c46 0201 0100 ...   .ELF....
#           ↑ 7f 45 4c 46 = ELF magic（執行檔的標誌）

# od：octal dump（傳統，更多格式選項）
od -A x -t x1z file.bin   # -A x 位移用 hex，-t x1z hex+ASCII
```

```
什麼時候用 xxd/od：
  - 看二進位檔（執行檔、圖片、壓縮檔）的內容
  - 判斷檔案類型（看 magic number，如 ELF/PNG/PDF 的開頭）
  - debug 看不可見字元（換行 \n、tab \t、null \0、CRLF 問題）
  - 看編碼問題（UTF-8 的多 byte 字元）
        │
  例：debug「為什麼這個檔案 grep 不到」
    可能是 CRLF（\r\n，Windows 換行）或隱藏字元
    xxd file | head 看到 0d 0a（CRLF）就知道了
```

> `xxd` 是 debug 隱藏字元的利器。「為什麼這個檔案看起來一樣但 grep 不到」「為什麼這行有奇怪的行為」——常是不可見字元（CRLF `\r\n`、tab、null、BOM）。`xxd file | head` 看 hex，`0d 0a` 是 CRLF（Windows 換行，Unix 是 `0a`）、`ef bb bf` 是 UTF-8 BOM。文字工具看不出這些（它們不顯示），xxd 一目了然。判斷檔案類型也用它（看 magic number：`7f 45 4c 46` = ELF、`89 50 4e 47` = PNG）。`file` 命令背後就是看 magic number。

## wc：統計

```bash
wc file.txt              # 行數 字數 byte數 檔名
# 10  50  300 file.txt

wc -l file.txt           # 只要行數
wc -w file.txt           # 只要字數
wc -c file.txt           # 只要 byte 數
wc -m file.txt           # 字元數（多 byte 字元和 -c 不同）

# 常用：算管線結果的數量
ls | wc -l               # 有幾個檔案（但別 parse ls，Ch 10；用 find）
find . -name "*.c" | wc -l   # 有幾個 .c 檔
```

## 故意弄壞：cat 二進位搞壞終端機

```bash
cd ~/cmdlab
# cat 一個二進位檔（噴亂碼）
cat /bin/ls              # 噴出一堆亂碼和控制字元
#   → 終端機可能顯示錯亂（控制字元改變終端機狀態）
#   如果終端機壞了（顯示亂碼、打字看不到）：
reset                    # 重設終端機（恢復正常）
#   或 stty sane

# 正確：看二進位用 xxd
xxd /bin/ls | head       # 安全地看 hex
```

cat 二進位檔會噴出控制字元，可能搞壞終端機顯示（某些 byte 是終端機控制碼，改變顏色/游標/模式）。如果終端機壞了（亂碼、打字看不到），打 `reset` 或 `stty sane` 恢復。永遠用 xxd/od 看二進位，不要 cat/less（less 較安全，會跳脫控制字元，但 cat 危險）。

## 踩雷集錦

1. **cat 大檔案/二進位**：cat 讀全部寫出來，大檔案洗版/卡、二進位搞壞終端機。大檔案用 less，二進位用 xxd

2. **Useless Use of Cat**：`cat file | grep x` 多餘，用 `grep x file`。cat 是串接工具，不是「看檔案」萬用工具

3. **tail -f 在 log rotation 後失效**：`-f` 追蹤 inode，log 被 rotate 換掉就看不到新的。用 `-F`（追蹤檔名）

4. **head -n +N vs tail -n +N**：tail -n +5 是「從第 5 行到結尾」，head 沒有 + 語意。注意方向

5. **看不出隱藏字元**：文字工具不顯示 CRLF/null/BOM。grep 不到/行為怪時，用 `xxd | head` 看 hex 找隱藏字元

## 進階：管線中的緩衝問題

當工具在管線中時，輸出緩衝行為會變，造成「tail -f | grep 看不到即時輸出」的困惑：

```
管線中的緩衝問題：
  程式輸出到「終端機」：通常 line-buffered（每行就 flush）
  程式輸出到「管線/檔案」：通常 fully-buffered（湊滿一個 buffer 才 flush）
        │
  問題場景：
    tail -f log | grep ERROR
    tail 的輸出進管線 → grep 的輸入
    但 grep 輸出到終端機... 應該 line-buffered？
    實際上某些工具在管線中變 fully-buffered → 看不到即時輸出
    （要湊滿 buffer，可能要等很久）
        │
  解法：強制 line-buffered
    grep --line-buffered ERROR
    stdbuf -oL command       （強制 line buffer）
    unbuffer command         （expect 套件，假裝是終端機）
```

```bash
# 即時管線：強制 line-buffered
tail -f /var/log/syslog | grep --line-buffered ERROR
#   ↑ --line-buffered 讓 grep 每行就輸出（即時看到 ERROR）
#   沒有它，grep 可能 buffer 一大堆才輸出（延遲）
```

> 管線中的緩衝是個隱蔽的坑。程式偵測「輸出到終端機」就 line-buffered（每行 flush，即時），「輸出到管線」就 fully-buffered（湊滿才 flush，延遲）——這是 libc 的預設行為（為效能）。所以 `tail -f log | grep ERROR | other` 中間的 grep 可能 buffer 很久才輸出，你看不到即時的 ERROR。解法：`grep --line-buffered`、`stdbuf -oL cmd`（強制 line buffer）、或 `unbuffer`（假裝是終端機）。這個緩衝問題在 Ch 19-21（fd/pipe/管線）會再碰到，是「為什麼我的管線沒即時輸出」的根本原因。

## 動手練習

1. 看工具底層：`strace -e openat,read,write cat a.txt`（cat 的 read+write）。`head -n 5` vs `cat`（head 讀夠就停）。`tail -f` 用 strace 看 inotify

2. 練 less：`less /var/log/syslog`（或任何大檔案），練 `/pattern` 搜尋、`g`/`G` 跳開頭結尾、`F` 即時追蹤。對比 cat 同檔案（洗版）

3. 看二進位：`xxd /bin/ls | head`（看 ELF magic 7f 45 4c 46）。建一個有 CRLF 的檔案（`printf "a\r\nb\r\n" > crlf.txt`），`xxd` 看到 `0d 0a`

4. 跑「故意弄壞」：cat 二進位搞壞終端機，用 `reset` 恢復。試管線緩衝（`tail -f` | grep 沒 `--line-buffered` vs 有，看即時性差異）

## 本章重點整理

- 選對工具：小檔案/管線用 cat、開頭 head、結尾 tail、即時追蹤 tail -f、大檔案 less、二進位 xxd、統計 wc
- cat 是串接工具（concatenate）不是「看檔案」萬用工具；`cat file | grep` 是 UUOC（直接 grep file）
- tail -f 用 inotify 即時追蹤；log rotation 環境用 -F（追蹤檔名而非 inode）
- less 只讀「正在看的部分」（seek），大檔案不卡；能搜尋、往回翻、F 即時追蹤
- xxd/od 看二進位和隱藏字元（CRLF/BOM/null）；管線緩衝問題用 --line-buffered/stdbuf 解

## 自我檢核

- [ ] 知道各工具的適用場景（cat/head/tail/less/xxd/wc 各用於什麼）
- [ ] 知道為什麼不該 cat 大檔案/二進位，以及 UUOC 是什麼
- [ ] 能解釋 tail -f 怎麼即時追蹤（inotify），以及 -f vs -F 的差別
- [ ] 能用 xxd 看隱藏字元（CRLF/BOM）debug「看起來一樣但行為不同」的檔案
- [ ] 知道管線緩衝問題，以及怎麼強制 line-buffered

## 延伸閱讀

### 官方文件

- **[less(1) man page](https://man7.org/linux/man-pages/man1/less.1.html)**
  - **讀哪裡**：COMMANDS（互動操作）、搜尋那部分
  - **學什麼**：less 的完整操作；本章是精選
  - **前提**：本章

### 部落格 / 文章

- **[Useless Use of Cat Award](https://porkmail.org/era/unix/award.html)**
  - **這篇說什麼**：UUOC 的經典討論，為什麼 `cat file | cmd` 多餘
  - **讀哪裡**：整頁（很短）
  - **為什麼值得讀**：把 cat 的正確用法和誤用講清楚

- **[Buffering in standard streams](https://www.pixelbeat.org/programming/stdio_buffering/)** — Pádraig Brady（coreutils 維護者）
  - **這篇說什麼**：stdio 緩衝行為（line/full buffered）和管線的關係
  - **讀哪裡**：buffering modes 那部分
  - **為什麼值得讀**：coreutils 維護者寫的，把本章的「管線緩衝問題」講透

→ [Ch 14 process 狀態機](./14-process-states.md)
