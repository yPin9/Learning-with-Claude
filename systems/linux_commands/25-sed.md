# Ch 25 — sed

> **目標**：掌握 sed（串流編輯器，Stream EDitor）——它的核心 `s///` 替換、地址（行範圍）選擇、它的「pattern space / hold space」雙緩衝模型、為什麼 sed 是「編輯器」而非「搜尋工具」、以及 `-i` 原地編輯的危險。sed 是把「對每一行做同樣編輯」自動化的利器，理解它的執行模型才能用好它。

> **環境**：GNU sed 4.x（Linux）。BSD sed（macOS）的 `-i` 語法不同會標注。

## 為什麼需要 sed？

你常遇到「對一堆行做同樣的修改」：把每行的 `foo` 換成 `bar`、刪掉所有註解行、在每行前加縮排、提取某個範圍的行。手動編輯不可能（檔案太大、或要對很多檔案做），這就是 sed 的領域。

sed 是「串流編輯器」——它把編輯操作（取代、刪除、插入）自動套用到串流的每一行。和 grep（只能篩選、不能改）不同，sed 能**修改**內容。和文字編輯器（vim）不同，sed 不互動、能批次處理、能進管線。理解 sed 的關鍵是它的**執行模型**（逐行讀進 pattern space、套用命令、印出）——搞懂這個，sed 從「神秘咒語」變成「可預測的機器」。

## 先建立直覺：sed 是一條編輯流水線

```
sed：逐行讀入 → 套用編輯命令 → 印出 → 下一行

  每一行都走這條流水線：
  ┌─────────────────────────────────────┐
  │  讀一行進 "pattern space"（工作區）   │
  │         ↓                             │
  │  套用 sed 命令（s/// 替換、d 刪除...） │
  │         ↓                             │
  │  印出 pattern space（除非 -n）        │
  │         ↓                             │
  │  清空，讀下一行（重複）               │
  └─────────────────────────────────────┘
        │
  關鍵：sed 對「每一行」重複套用同一組命令
  輸入 ──▶ sed 'command' ──▶ 輸出（編輯過的串流）
        │
  → sed = 自動化的「對每行做同樣編輯」
```

關鍵心智：sed 逐行讀入到一個叫「pattern space」的工作區，對它套用命令（替換/刪除/插入），然後印出，再讀下一行。它對**每一行**重複同一組命令。預設印出每行（編輯過或沒編輯），`-n` 關掉自動印出（只印你明確要的）。

> sed 的 pattern 部分用 regex（預設 BRE，`-E` 用 ERE）。如果你對 regex 還不熟，先回看 [Ch 23 — 正規表示式](./23-regex.md)。sed 的威力 80% 在 `s///` 替換的 regex。

## 核心：s/// 替換

sed 90% 的用途是 `s///`（substitute，替換）：

```bash
# s/pattern/replacement/flags 的基本形式
echo "hello world" | sed 's/world/sed/'        # hello sed
echo "aaa" | sed 's/a/b/'                       # baa（預設只換「每行第一個」）
echo "aaa" | sed 's/a/b/g'                      # bbb（g flag：換全部，global）
echo "Hello" | sed 's/hello/hi/I'               # Hi（I flag：忽略大小寫）

# 用 regex
echo "phone: 0912345678" | sed 's/[0-9]/X/g'    # phone: XXXXXXXXXX（每個數字換 X）
echo "  trim me  " | sed 's/^ *//; s/ *$//'      # "trim me"（去頭尾空白，兩個命令用 ; 分隔）

# 反向引用（\1 \2）：捕獲分組
echo "John Smith" | sed -E 's/(\w+) (\w+)/\2 \1/'   # Smith John（交換，\1 \2 是分組）
echo "2024-01-15" | sed -E 's/([0-9]+)-([0-9]+)-([0-9]+)/\3\/\2\/\1/'  # 15/01/2024

# & 代表「整個匹配」
echo "important" | sed 's/important/[&]/'        # [important]（& = 匹配到的整個東西）
echo "5" | sed 's/[0-9]/<&>/'                    # <5>
```

```
s/pattern/replacement/flags 解剖：

  s        substitute（替換命令）
  /.../    pattern（要找什麼，是 regex）
  /.../    replacement（換成什麼）
  flags：
    g      global，換該行所有匹配（預設只換第一個）
    I/i    忽略大小寫
    數字    換第 N 個匹配（s/a/b/2 換第 2 個 a）
    p      印出（配 -n 用）
        │
  replacement 裡的特殊字元：
    \1 \2  反向引用（pattern 裡 (...) 捕獲的）
    &      整個匹配的內容
    \n     換行
```

> **預設只換「每行第一個」匹配，要 `g` 才換全部**——這是 sed 最常見的初學者錯誤。`sed 's/a/b/'` 對 `aaa` 只給 `baa`（換了第一個 a），要 `s/a/b/g` 才給 `bbb`。記住：**sed 的 s 預設換一次，grep 的概念是「行」，sed 的 s 是「行內第一個匹配」**。另一個威力是**反向引用** `\1 \2`——`(...)` 在 pattern 捕獲、`\N` 在 replacement 引用，能做「交換」「重排」這種需要記住匹配內容的編輯。`&` 代表整個匹配（如給匹配加括號 `s/.../[&]/`）。這些讓 sed 不只是「換字串」而是「結構化重寫」。

## 分隔符不一定是 /

替換的分隔符可以換成任何字元，處理路徑時特別有用：

```bash
# 路徑替換的痛：/ 要一直跳脫
echo "/usr/local/bin" | sed 's/\/usr\/local/\/opt/'    # 醜！斜線到處跳脫
echo "/usr/local/bin" | sed 's#/usr/local#/opt#'        # /opt/bin（用 # 當分隔符，清爽）
echo "/usr/local/bin" | sed 's|/usr/local|/opt|'        # 同上，用 |

# 分隔符可以是任何字元（緊跟在 s 後面的那個字元）
echo "a/b/c" | sed 's,/,-,g'                            # a-b-c（用 , 當分隔符）
```

> **路徑替換時換掉 `/` 分隔符**。`s/.../.../ ` 用 `/` 當分隔符，所以替換路徑（含很多 `/`）時要把每個 `/` 跳脫成 `\/`，變成「傾斜牙籤症候群」（leaning toothpick syndrome）。解法：sed 允許用**任何字元**當分隔符——緊跟在 `s` 後面的字元就是分隔符。`s#/usr/local#/opt#` 用 `#`、`s|...|...|` 用 `|`，路徑裡的 `/` 就不用跳脫了，清爽得多。這是處理檔案路徑、URL 時的必備技巧。

## 地址：選擇要編輯哪些行

sed 命令前可以加「地址」指定作用於哪些行：

```bash
# 行號地址
sed '3s/foo/bar/' file           # 只在第 3 行替換
sed '3d' file                    # 刪除第 3 行（d = delete）
sed '2,5d' file                  # 刪除第 2-5 行（範圍）
sed '$d' file                    # 刪除最後一行（$ = 最後一行）

# regex 地址（匹配的行才作用）
sed '/error/d' file              # 刪除所有含 error 的行
sed '/^#/d' file                 # 刪除所有註解行（^# 開頭）
sed '/^$/d' file                 # 刪除空行
sed '/DEBUG/s/info/INFO/' file   # 只在含 DEBUG 的行做替換

# 範圍：從一個匹配到另一個匹配
sed '/START/,/END/d' file        # 刪除 START 到 END 之間的所有行（含兩端）
sed '/^BEGIN/,/^END/p' -n file   # 只印 BEGIN 到 END 之間（-n + p）

# 取反（地址後加 ! = 「不符合的行」）
sed '/keep/!d' file              # 刪除「不」含 keep 的行（等於只保留含 keep 的）

# 只印特定行（模擬 head/sed 當提取工具）
sed -n '10,20p' file             # 只印第 10-20 行（-n 關自動印，p 印出）
sed -n '/start/,/stop/p' file    # 印 start 到 stop 之間
```

> **地址讓 sed 從「全套用」變成「選擇性編輯」**。`sed 's/x/y/'` 對每行都套用，但 `sed '/error/s/x/y/'` 只對含 error 的行套用。地址可以是行號（`3`）、範圍（`2,5`）、regex（`/pattern/`）、最後一行（`$`）、或範圍 regex（`/START/,/END/`）。地址後加 `!` 取反（`/keep/!d` 刪掉不含 keep 的）。配合 `-n`（關自動印）+ `p`（印出），sed 還能當「提取工具」：`sed -n '10,20p'` 印第 10-20 行，`sed -n '/start/,/stop/p'` 印兩個標記之間的區塊——這是 grep 做不到的（grep 不懂「範圍」）。

## 底層機制：pattern space 與 hold space

sed 其實有兩個緩衝區，這是它能做複雜處理的根源：

```
sed 的雙緩衝模型：

  pattern space（模式空間）：當前處理的行（工作區）
  hold space（保持空間）：一個「暫存區」（預設空）
        │
  每行的處理週期：
    1. 讀一行進 pattern space
    2. 套用命令（s/d/p... 都作用於 pattern space）
    3. 印出 pattern space（除非 -n）
    4. 清空 pattern space，讀下一行
        │
  hold space 讓 sed 能「跨行記憶」：
    h  把 pattern space 複製到 hold space
    H  附加 pattern space 到 hold space
    g  把 hold space 複製到 pattern space
    G  附加 hold space 到 pattern space
    x  交換 pattern 和 hold space
        │
  → 大部分 sed 用途只用 pattern space
    hold space 用於「需要記住前面行」的進階處理（如 tac、去重）
```

```bash
# hold space 的經典用途：反轉檔案行序（模擬 tac）
printf "1\n2\n3\n" | sed -n '1!G;h;$p'
# 3 / 2 / 1
#   1!G：非第一行時，把 hold（之前累積的）附加到 pattern 後
#   h：  把當前 pattern 存進 hold
#   $p： 最後一行時印出（此時 pattern 累積了反序的全部）

# 大部分時候你不需要 hold space，但知道它存在解釋了 sed 的「完整性」
# sed 是圖靈完備的（理論上能做任何計算），靠的就是這雙緩衝 + 分支命令
```

> **pattern space / hold space 的雙緩衝是 sed 的完整心智模型**。99% 的 sed 用途（s/d/p）只碰 **pattern space**（當前行的工作區）。但 sed 還有一個 **hold space**（暫存區），讓它能「跨行記憶」——把這行存起來、處理下一行時再取出。`h`（存）、`g`（取）、`x`（交換）、`G`（附加）操作這兩個空間。這就是為什麼 sed 能做「反轉行序」「跨行去重」這種需要記憶的操作（甚至圖靈完備）。**實務上你很少需要 hold space**——需要跨行邏輯時 awk（Ch 26）更好用。但知道它存在，你才理解 sed 那些「魔法 one-liner」（如 `sed -n '1!G;h;$p'` 反轉檔案）的原理，不會覺得 sed 是黑魔法。

## 故意弄壞：-i 原地編輯的危險

`-i`（原地編輯）會直接改檔案，是 sed 最危險的功能：

```bash
cd ~/cmdlab
echo -e "line1\nline2\nline3" > test.txt

# -i 直接修改原檔案（不輸出到 stdout）
sed -i 's/line/LINE/g' test.txt
cat test.txt                     # LINE1 / LINE2 / LINE3（原檔被改了）

# 危險 1：regex 寫錯 → 檔案被毀，沒有 undo
echo "important data" > critical.txt
sed -i 's/.*//' critical.txt     # 災難！把所有內容清空（.* 匹配整行換成空）
cat critical.txt                 # （空的，資料沒了，無法復原）

# 安全做法 1：先不加 -i，看輸出對不對，再加
sed 's/old/new/g' file           # 先看 stdout（不改檔案）
sed -i 's/old/new/g' file        # 確認對了再 -i

# 安全做法 2：-i 加備份後綴
sed -i.bak 's/old/new/g' file    # 改檔案，但先存 file.bak（GNU 和 BSD 都支援）
ls                               # file file.bak（原檔備份）

# BSD（macOS）的 -i 語法不同！
# GNU:  sed -i 's/x/y/' file       （-i 後直接接命令）
# BSD:  sed -i '' 's/x/y/' file    （-i 要接一個參數，空字串 = 不備份）
# 跨平台腳本要小心這個差異
```

> **`sed -i`（原地編輯）會直接改原檔案、沒有 undo——這是 sed 最危險的功能**。一個寫錯的 regex（如 `s/.*//` 清空所有行）配 `-i` 能瞬間毀掉檔案，無法復原。安全鐵律：**先不加 `-i` 跑一次看 stdout**，確認結果對了再加 `-i`。或用 `-i.bak`（GNU/BSD 都支援）自動備份原檔成 `.bak`。另一個跨平台地雷：**GNU 和 BSD 的 `-i` 語法不同**——GNU `sed -i 's/x/y/' file`，BSD（macOS）要 `sed -i '' 's/x/y/' file`（`-i` 後要跟備份後綴參數，空字串表示不備份）。寫跨平台腳本時這個差異會讓你的腳本在 macOS 上壞掉。處理重要檔案前，永遠先在副本上測試。

## 進階：其他常用 sed 命令

```bash
# d：刪除行
sed '/^$/d' file                 # 刪空行
sed '1d' file                    # 刪第一行（如去掉 CSV 標題）

# p：印出（配 -n）
sed -n '/error/p' file           # 等於 grep error（印匹配行）
sed -n '5p' file                 # 印第 5 行

# i/a/c：插入/附加/取代整行
sed '2i\inserted line' file      # 在第 2 行「前」插入（insert）
sed '2a\appended line' file      # 在第 2 行「後」附加（append）
sed '/error/c\REDACTED' file     # 把含 error 的行整行換成 REDACTED（change）

# q：處理到某行就退出（加速大檔案）
sed '100q' file                  # 印前 100 行就退出（像 head -100，但更早停）
sed '/STOP/q' file               # 印到遇見 STOP 就停

# y：字元轉換（像 tr，一對一）
echo "hello" | sed 'y/el/ip/'    # hippo（e→i, l→p, l→p；h/o 不變）
echo "abc" | sed 'y/abc/xyz/'    # xyz（a→x, b→y, c→z）

# 多命令（-e 或 ;）
sed -e 's/foo/bar/' -e 's/baz/qux/' file    # 多個替換
sed 's/foo/bar/; s/baz/qux/' file            # 同上，用 ; 分隔

# 從腳本檔讀命令（-f）
sed -f commands.sed file
```

> **sed 不只 `s///`——`d`/`p`/`i`/`a`/`c`/`q`/`y` 各有用途**。`d`（刪行）+ regex 地址是「過濾」的利器（`sed '/^#/d; /^$/d'` 去註解和空行）。`q`（退出）能加速大檔案（`sed '1000q'` 印 1000 行就停，不掃完整個檔案）。`i`/`a`/`c`（插入/附加/取代整行）能做「在某行前後加內容」。`y`（字元轉換）像簡化版 tr（Ch 27）。但記住一條經驗：**當 sed 命令開始變複雜（多個 hold space 操作、巢狀邏輯），就該換 awk（Ch 26）**——awk 有變數、條件、迴圈，做複雜文字處理比 sed 清楚太多。sed 的甜蜜點是「簡單的逐行替換和刪除」，超過這個範圍 awk 更合適。

## 動手練習

1. s/// 基礎：把一個檔案裡所有 `foo` 換 `bar`，先不加 g（看只換第一個）再加 g（換全部），理解差別

2. 反向引用：用 `sed -E 's/(\w+) (\w+)/\2 \1/'` 交換兩個詞，理解 \1 \2

3. 地址：用 `sed -n '10,20p'` 印第 10-20 行，`sed '/^#/d'` 刪註解行，體會地址選擇

4. 跑「故意弄壞」：故意用錯的 sed -i 改一個**副本**檔案，看它如何不可逆，理解為什麼要先看 stdout

5. hold space 魔法：跑 `printf "1\n2\n3\n" | sed -n '1!G;h;$p'`（反轉），對照 pattern/hold space 那節

## 本章重點整理

- sed 是串流編輯器：逐行讀進 pattern space → 套用命令 → 印出 → 下一行；對每行重複同組命令
- `s/pattern/replacement/flags` 是核心：g（全部）、I（忽略大小寫）、\1\2（反向引用）、&（整個匹配）；預設只換每行第一個
- 分隔符可換（`s#...#...#`）避免路徑的斜線跳脫；地址（行號/regex/範圍）選擇作用的行
- 雙緩衝模型：pattern space（工作區）+ hold space（暫存區，跨行記憶）——99% 用途只碰 pattern space
- `sed -i`（原地編輯）危險：無 undo、GNU/BSD 語法不同——先看 stdout 再 -i，或用 -i.bak 備份

## 自我檢核

- [ ] 能解釋 sed 的執行模型（逐行進 pattern space、套用、印出）
- [ ] 熟練 `s///` 含 g flag、反向引用 \1\2、& 整個匹配
- [ ] 會用地址（行號/regex/範圍）選擇要編輯的行
- [ ] 知道 pattern space 和 hold space 的差別，理解 sed 為什麼能做跨行處理
- [ ] 知道 `sed -i` 的危險和安全做法，以及 GNU/BSD 的差異

## 延伸閱讀

### 官方文件

- **[GNU sed manual](https://www.gnu.org/software/sed/manual/sed.html)** — GNU
  - **讀哪裡**：「sed scripts」（命令語法）+「Examples」+「Some Sample Programs」（hold space 的進階範例）
  - **為什麼值得讀**：sed 的權威來源；Some Sample Programs 那章解釋了那些「魔法 one-liner」怎麼運作

### 書籍

- **《sed & awk》— Part I (sed)** — Dale Dougherty & Arnold Robbins（O'Reilly, 2nd ed）
  - **讀哪幾章**：Ch 4-6（sed 的命令、地址、pattern/hold space）
  - **這本書的定位**：sed 和 awk 的經典權威，把 sed 的執行模型和 hold space 講到透徹
  - **前提**：本章 + Ch 23（regex）

### 文章 / 速查

- **[sed one-liners explained](https://catonmat.net/sed-one-liners-explained-part-one)** — Peteris Krumins
  - **這篇說什麼**：逐一拆解著名的「sed one-liners」合集（含那些 hold space 魔法）
  - **讀哪裡**：Part 1-3，從簡單到複雜
  - **為什麼值得讀**：把網路上流傳的 sed 咒語一句句講清楚，是理解 hold space 進階用法的最佳教材

- **[The sed FAQ / grymoire sed tutorial](https://www.grymoire.com/Unix/Sed.html)** — Bruce Barnett
  - **這篇說什麼**：sed 的完整教學，從基礎到 hold space 和分支
  - **為什麼值得讀**：把 sed 的每個命令和執行模型講得很細，適合當參考手冊

→ [Ch 26 awk](./26-awk.md)
