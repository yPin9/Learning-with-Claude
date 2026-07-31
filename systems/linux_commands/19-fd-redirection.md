# Ch 19 — file descriptor 與重導向

> **目標**：徹底理解 file descriptor（fd）——它是什麼（process 的「開啟檔案表」的索引）、stdin/stdout/stderr（fd 0/1/2）、重導向（>、<、2>、&>）的底層機制、以及這如何串起 Ch 6（inode 引用）、Ch 8（一切皆檔案）、Ch 15（fork 繼承 fd）。這是 shell 重導向和管線的核心。

> **環境**：bash 5.x，/proc/<pid>/fd。原理深挖章，多次引用前面的概念。

## 為什麼 fd 是命令列的核心概念？

前面章節反覆提到 fd——Ch 6（fd 引用讓被刪的 inode 不釋放）、Ch 8（裝置用 fd 操作）、Ch 15（fork 繼承 fd）、Ch 16（/proc/<pid>/fd）。現在正式深入它。

**file descriptor（fd）是 process 操作 I/O 的統一介面**——不管是檔案、裝置、pipe、socket，都透過 fd 讀寫。理解 fd，你就懂了重導向（`>`、`<`、`2>`）怎麼運作、管線（`|`）怎麼連接 process、為什麼 `2>&1` 是那樣寫。這是 Unix「一切皆檔案」（Ch 1）的具體機制——fd 是那個「統一的檔案介面」。

## 先建立直覺：fd 是「開啟檔案表」的索引

```
file descriptor（fd）：process 的「開啟檔案表」的索引

  每個 process 有一張「fd 表」（開啟的檔案列表）：
  ┌─────┬──────────────────────┐
  │ fd  │  指向什麼              │
  ├─────┼──────────────────────┤
  │  0  │  stdin（標準輸入）     │ ← 預設：鍵盤/終端機
  │  1  │  stdout（標準輸出）    │ ← 預設：螢幕/終端機
  │  2  │  stderr（標準錯誤）    │ ← 預設：螢幕/終端機
  │  3  │  你開的某個檔案        │
  │  4  │  另一個檔案/pipe/socket│
  └─────┴──────────────────────┘
        │
  fd 是個「小整數」（0, 1, 2, 3...）
  程式用 fd 讀寫（read(fd, ...)、write(fd, ...)）
  不管 fd 指向檔案、裝置、pipe——都用同樣的 read/write
        │
  → fd 是「一切皆檔案」（Ch 1）的統一介面
    程式只認 fd 這個整數，不管底層是什麼
```

關鍵心智：fd 是個小整數，是 process「開啟檔案表」的索引。程式用 fd 讀寫（不管底層是檔案/裝置/pipe）。fd 0/1/2 是約定的標準輸入/輸出/錯誤。重導向就是「改變某個 fd 指向哪裡」。

## fd 0/1/2：標準輸入/輸出/錯誤

```
三個標準 fd（每個 process 啟動時就有）：
  fd 0：stdin（標準輸入）  ← 程式從這讀輸入
  fd 1：stdout（標準輸出） ← 程式把正常輸出寫這
  fd 2：stderr（標準錯誤） ← 程式把錯誤訊息寫這
        │
  預設都連到「終端機」（你的螢幕和鍵盤）
        │
  為什麼分 stdout 和 stderr：
    讓「正常輸出」和「錯誤訊息」能分開處理
    command > file        只把正常輸出存檔（錯誤還顯示在螢幕）
    command 2> errors     只把錯誤存檔
        │
  → 這個分離讓你能獨立重導向輸出和錯誤
```

```bash
# 看當前 shell 的 fd（Ch 16）
ls -l /proc/self/fd
# lrwx------ ... 0 -> /dev/pts/0      ← stdin 連到終端機
# lrwx------ ... 1 -> /dev/pts/0      ← stdout 連到終端機
# lrwx------ ... 2 -> /dev/pts/0      ← stderr 連到終端機

# 程式為什麼分 stdout/stderr
ls /nonexistent /tmp
# /tmp:               ← 正常輸出（stdout）
# ls: cannot access '/nonexistent'   ← 錯誤訊息（stderr）
#   兩者都顯示在螢幕，但走不同 fd
```

## 重導向：改變 fd 指向哪裡

重導向（`>`、`<`、`2>`）就是「讓某個 fd 指向別的地方」（檔案而非終端機）：

```bash
# stdout 重導向（> ）：fd 1 指向檔案
echo "hello" > out.txt          # 把 fd 1（stdout）導到 out.txt
#   echo 還是 write(1, ...)，但 fd 1 現在指向檔案不是終端機

# 追加（>> ）：不覆蓋，接在後面
echo "more" >> out.txt

# stdin 重導向（< ）：fd 0 從檔案讀
sort < unsorted.txt             # sort 從 unsorted.txt 讀（fd 0 指向它）

# stderr 重導向（2> ）：fd 2 指向檔案
ls /nonexistent 2> errors.txt   # 把錯誤導到 errors.txt

# stdout 和 stderr 都導到檔案
ls /tmp /nonexistent > all.txt 2>&1
#   > all.txt     fd 1 指向 all.txt
#   2>&1          fd 2 指向「fd 1 指向的地方」（也就是 all.txt）
#   順序重要！（後述）

# 簡寫（bash）：都導到同一個
ls /tmp /nonexistent &> all.txt    # &> 等於 > all.txt 2>&1

# 丟棄輸出（導到 /dev/null，Ch 8）
command > /dev/null 2>&1         # 丟棄所有輸出（黑洞）
command 2> /dev/null             # 只丟棄錯誤
```

> 重導向的本質是「改變 fd 指向」。`echo > file` 不改變 echo（它還是 `write(1, ...)`），而是改變 fd 1 指向哪裡——從終端機改成檔案。echo 不知道也不在乎（它只認 fd 1）。這呼應 Ch 15 的「fork 後 exec 前調整 child」——shell 在執行 echo 前，把 echo 的 fd 1 重導到檔案，echo 才開始跑。理解這個，所有重導向行為就清楚了：重導向操作的是 fd，不是命令。

## 2>&1 的玄機

`2>&1`（把 stderr 導到 stdout 的去處）是新手最困惑的語法，順序也很關鍵：

```
2>&1 的意義：「讓 fd 2 指向 fd 1 當前指向的地方」
  2>&1 不是「把 stderr 導到 stdout」
  而是「把 fd 2 指向『fd 1 此刻指向的東西』」
        │
  順序的陷阱：
  command > file 2>&1     ✓ 正確
    1. > file：fd 1 指向 file
    2. 2>&1：fd 2 指向「fd 1 指向的」= file
    → 兩者都到 file
        │
  command 2>&1 > file     ✗ 不如預期！
    1. 2>&1：fd 2 指向「fd 1 指向的」= 終端機（fd 1 還沒改）
    2. > file：fd 1 指向 file
    → fd 2 還指向終端機，fd 1 指向 file
    → 錯誤到螢幕，正常輸出到 file（不是你要的）
```

```bash
# 驗證順序的差異
ls /tmp /nonexistent > a.txt 2>&1   # 正常+錯誤都到 a.txt
ls /tmp /nonexistent 2>&1 > b.txt   # 只有正常到 b.txt，錯誤到螢幕
cat a.txt    # 有 /tmp 內容和錯誤訊息
cat b.txt    # 只有 /tmp 內容
```

> **`2>&1` 的順序是經典陷阱**。`2>&1` 是「讓 fd 2 指向 fd 1『此刻』指向的地方」——是複製當下的指向，不是建立永久連結。所以 `> file 2>&1`（先把 fd 1 導到 file，再讓 fd 2 跟著）才對；`2>&1 > file`（先讓 fd 2 跟著還在終端機的 fd 1，再把 fd 1 導到 file）會讓 stderr 留在終端機。記法：**重導向從左到右處理，`2>&1` 複製「當下」的 fd 1 指向。** 想兩者都到檔案，`> file 2>&1`（或 bash 的 `&> file`）。這個順序錯誤是 debug「為什麼錯誤訊息還在螢幕」的常見原因。

## fd 的底層：和 inode 的關係（串 Ch 6）

fd 串起了 Ch 6 的 inode 引用——這就是「rm 後檔案還能讀」的機制：

```
fd → 開啟檔案表 → inode（Ch 4-6）：

  open("file.txt") 做的事：
    1. 路徑解析找到 inode（Ch 4-5）
    2. 在 process 的 fd 表分配一個 fd（如 fd 3）
    3. fd 3 → 一個「開啟檔案描述」→ inode
        │
  這個「開啟檔案描述」對 inode 是一個引用（Ch 6）：
    inode 的釋放需要 link count = 0 「且」無 fd 開著
    → fd 開著 = inode 被引用 = 不釋放
        │
  → 這就是 Ch 6 的「rm 後 fd 還開著，inode 不釋放」
    /proc/<pid>/fd/3 能讀到被 rm 的檔案（fd 還引用著 inode）
```

```bash
# 驗證 fd 引用 inode（Ch 6 的延伸）
cd ~/cmdlab
echo "data" > f.txt
exec 3< f.txt                # 開 f.txt 到 fd 3（shell 自己的 fd）
ls -l /proc/$$/fd/3          # fd 3 -> .../f.txt
rm f.txt                     # 刪檔名（unlink，Ch 6）
ls -l /proc/$$/fd/3          # fd 3 -> .../f.txt (deleted)  ← 還指著（inode 沒釋放）
cat /proc/$$/fd/3            # data  ← 還能讀！（fd 引用著 inode）
exec 3<&-                    # 關閉 fd 3 → inode 釋放（Ch 6）
```

> fd 是 Ch 6「inode 引用計數」的另一半。inode 釋放需要「link count = 0 且無 fd 開著」。fd 開著就是「有 process 引用這個 inode」，所以 inode 不釋放——即使檔名被 rm（link count = 0）。`/proc/<pid>/fd/N` 能讀到被刪的檔案（fd 還引用著 inode 和它的資料）。這是「刪了檔案空間沒釋放」「從 /proc 救回被刪檔案」（Ch 4/6/11）的完整機制。fd 把 Ch 4（inode）、Ch 6（引用）、Ch 16（/proc/fd）串成一體。

## fork 繼承 fd（串 Ch 15）

fork 出的 child 繼承 parent 的 fd 表——這是重導向和管線能運作的關鍵：

```
fork 繼承 fd（Ch 15）：
  fork 時，child 複製 parent 的 fd 表
  → child 的 fd 0/1/2/3... 指向和 parent 一樣的地方
        │
  這就是重導向怎麼運作（Ch 15 的「中間窗口」）：
    shell 執行 command > file：
    1. fork 出 child
    2. child 在 exec 前，把 fd 1 重導到 file
    3. child exec 成 command
    4. command 繼承了「fd 1 指向 file」的狀態
    → command 的輸出（write fd 1）進 file
        │
  → 重導向是「fork 後、exec 前調整 child 的 fd」
    這就是 Ch 15 說的「中間窗口的彈性」
```

> fd 繼承（fork 時複製 fd 表）是 shell 重導向和管線的底層。Ch 15 講「fork 後 exec 前的窗口能調整 child」——調整什麼？主要就是 **fd**。`command > file`：shell fork child → child 把 fd 1 重導到 file → child exec 成 command → command 繼承「fd 1 指向 file」。command 自己只是 `write(1, ...)`，不知道輸出去了檔案。管線（Ch 20）同理——shell 用 pipe 連接兩個 child 的 fd。這把 Ch 15（fork/exec）和重導向完全串起來：重導向 = fork 後調整 child 的 fd。

## 自訂 fd 與 exec

你能開自訂 fd（fd 3 以上）做進階重導向：

```bash
# 開一個 fd 給 shell 自己（exec 不帶命令 = 操作 shell 的 fd，Ch 1）
exec 3> output.log           # 開 fd 3 指向 output.log
echo "log line 1" >&3        # 寫到 fd 3（= output.log）
echo "log line 2" >&3
exec 3>&-                    # 關閉 fd 3
cat output.log               # log line 1 / log line 2

# 同時讀寫不同檔案
exec 3< input.txt 4> output.txt
# 從 fd 3 讀、寫 fd 4...
exec 3<&- 4>&-               # 關閉

# 把 stdout 暫存再恢復（進階技巧）
exec 5>&1                    # fd 5 = 當前 stdout（備份）
exec 1> /tmp/redirected      # stdout 重導到檔案
echo "this goes to file"
exec 1>&5 5>&-              # 恢復 stdout，關閉 fd 5
echo "this goes to terminal"
```

> 自訂 fd（fd 3+）讓你做進階重導向——同時操作多個檔案、暫存/恢復 stdout、給特定輸出開專用 fd。`exec 3> file`（exec 不帶命令 = 操作 shell 自己的 fd，Ch 1）開 fd 3，`>&3` 寫到它。這在腳本裡很有用（如 fd 3 給 log、fd 4 給 debug 輸出，分開管理）。`exec 5>&1`（備份 stdout 到 fd 5）+ 之後 `exec 1>&5`（恢復）是「暫時重導 stdout 再還原」的技巧。這些是 Part 8（scripting）的進階工具。

## 故意弄壞：2>&1 順序錯誤

```bash
cd ~/cmdlab
# 想把正常輸出和錯誤都存到 log，但順序寫錯
ls /tmp /nonexistent 2>&1 > log.txt
# ls: cannot access '/nonexistent'   ← 錯誤還在螢幕！（順序錯）
cat log.txt
# /tmp 的內容                          ← 只有正常輸出進 log

# 正確順序
ls /tmp /nonexistent > log.txt 2>&1
# （螢幕沒輸出，都進 log）
cat log.txt
# /tmp 內容 + 錯誤訊息                  ← 兩者都進 log
```

這驗證了 `2>&1` 的順序——它複製「當下」的 fd 1 指向。先 `> log.txt`（fd 1 指向 log）再 `2>&1`（fd 2 跟著 = log）才對。順序錯誤是 debug「為什麼錯誤訊息沒進 log」的常見原因。

## 踩雷集錦

1. **2>&1 順序錯**：`2>&1` 複製當下的 fd 1 指向。`> file 2>&1`（對）vs `2>&1 > file`（錯，stderr 留終端機）。順序從左到右

2. **以為 > 會合併輸出**：`>` 覆蓋（截斷檔案）。要追加用 `>>`。`> file` 會清空 file 原內容（Ch 11 的覆蓋陷阱）

3. **忘記 stderr 需要單獨重導向**：`command > file` 只導 stdout，stderr 還在螢幕。要連錯誤用 `> file 2>&1` 或 `&> file`

4. **以為重導向改變命令**：重導向改變 fd 指向，不改變命令。命令還是 write(1)/read(0)，只是 fd 指向變了

5. **管線和重導向混用順序**：`cmd | tee` 和 `cmd > file` 的交互（Ch 20-22）。stdout 進管線時，重導向 stderr 要小心（`cmd 2>&1 | grep` 把錯誤也進管線）

## 進階：fd 的三層結構（open file description）

fd 背後其實有三層結構，理解它能解釋一些微妙行為：

```
fd 的三層結構（kernel 內）：
  1. fd（per-process 的整數）
        ↓ 指向
  2. open file description（開啟檔案描述，系統層）
       含：當前讀寫位置（offset）、開啟模式（O_RDONLY...）
        ↓ 指向
  3. inode（檔案本體，Ch 4）
        │
  關鍵：fork/dup 共享「open file description」（第 2 層）
    → 共享 offset！（一個改 offset，另一個看到）
    這是為什麼 fork 後 parent/child 寫同一個 fd 不會互相覆蓋
    （共享 offset，依序往後寫）
        │
  而兩次獨立 open 同一檔案 → 不同 open file description
    → 各自獨立的 offset
```

> fd 的三層結構（fd → open file description → inode）解釋了一些微妙行為。`fork`/`dup` 複製 fd 時，兩個 fd 共享同一個「open file description」（含 offset）——所以 parent 和 child 寫同一個重導向的檔案不會互相覆蓋（共享 offset，依序往後寫，這是 `(command1; command2) > file` 為什麼能正確接續的原因）。而兩次獨立 `open` 同一檔案是不同的 open file description（各自的 offset）。這個三層結構是 Ch 6 的 inode、本章的 fd 之間的橋樑。理解它，你會懂為什麼 `(echo a; echo b) > file` 的兩個 echo 不會互相覆蓋（共享 offset）。這是 TLPI Ch 5 的核心概念。

## 動手練習

1. 看 fd：`ls -l /proc/self/fd`（你 shell 的 0/1/2）。重導向後再看（`exec 3> /tmp/x; ls -l /proc/$$/fd`）

2. 練重導向：`> file`（stdout）、`2> file`（stderr）、`> file 2>&1`（都）、`&> file`（簡寫）、`> /dev/null 2>&1`（丟棄）。理解每個導向哪個 fd

3. 跑「故意弄壞」：`2>&1 > file` vs `> file 2>&1`，看錯誤訊息去哪不同。理解 2>&1 的順序

4. 串 Ch 6：`exec 3< file; rm file; cat /proc/$$/fd/3`（fd 引用著被刪的 inode）。理解 fd 和 inode 釋放的關係

## 本章重點整理

- fd 是 process「開啟檔案表」的索引（小整數）；程式用 fd 讀寫，不管底層是檔案/裝置/pipe（一切皆檔案的統一介面）
- fd 0/1/2 = stdin/stdout/stderr；stdout/stderr 分離讓你能獨立重導向正常輸出和錯誤
- 重導向（>/</2>/&>）改變 fd 指向（不改變命令）；`2>&1` 複製「當下」的 fd 1 指向（順序關鍵）
- fd 串起 Ch 6（fd 引用 inode → rm 後不釋放）和 Ch 15（fork 繼承 fd → 重導向是 fork 後調整 child 的 fd）
- fd 三層結構（fd → open file description → inode）；fork/dup 共享 open file description（含 offset）

## 自我檢核

- [ ] 能解釋 fd 是什麼，以及它如何體現「一切皆檔案」
- [ ] 知道 fd 0/1/2 是什麼，為什麼分 stdout 和 stderr
- [ ] 能正確使用重導向，特別是 `2>&1` 的順序
- [ ] 能解釋 fd 和 inode 釋放的關係（串 Ch 6）
- [ ] 能解釋重導向怎麼透過 fork 繼承 fd 運作（串 Ch 15）

## 延伸閱讀

### 書籍

- **《The Linux Programming Interface》— Ch 5 (File I/O: Further Details)** — Michael Kerrisk
  - **讀哪幾章**：Ch 5（fd 三層結構、dup、共享 offset）；Ch 4（open/read/write 基礎）
  - **這本書的定位**：fd 機制的權威來源
  - **前提**：本章 + Ch 4-6

### 部落格 / 文章

- **[Bash redirection explained](https://catonmat.net/bash-one-liners-explained-part-three)** — Peteris Krumins
  - **這篇說什麼**：bash 重導向的完整解釋，含 2>&1 的順序、自訂 fd
  - **讀哪裡**：redirection 那部分
  - **為什麼值得讀**：把重導向的各種語法和順序講透

- **[File descriptors explained](https://jvns.ca/blog/2016/08/13/file-descriptors/)** — Julia Evans
  - **這篇說什麼**：fd 是什麼、怎麼運作
  - **讀哪裡**：整篇
  - **為什麼值得讀**：把 fd 講得最易懂

→ [Ch 20 pipe 底層](./20-pipe-internals.md)
