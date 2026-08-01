# Ch 17 — git 當考古工具

> **目標**：把版本歷史從「備份與協作機制」重新框架成「讀碼工具」。當你盯著一行 code 想「這到底為什麼長這樣」時，答案幾乎不在這行本身，也不在它旁邊的註解——它在**當初寫下它的那個 commit 的 message、以及那個 PR 的討論串**裡。這章教你用 `git log -S/-G/-L`、`git blame -C -M`、`git bisect` 把「一行 code 的來歷、演化、與設計意圖」逆向出來。

> **環境**：WSL2 Ubuntu 22.04，git 2.34+，沙包 `~/reading_code_lab/redis`（redis 7.4.0）。**關鍵前置**：Ch 0 的沙包是 `--depth 1` 淺 clone，只有一個 commit，本章所有指令都會失敗或退化。動筆前務必先補回完整歷史：
> ```bash
> cd ~/reading_code_lab/redis && git fetch --unshallow
> ```
> 本章所有輸出都是在 unshallow 後（12205 個 commit 的完整歷史）真跑照抄。

## 為什麼版本歷史是讀碼的一級武器

前面幾章的工具（rg、ctags、cscope、clangd）回答的都是**空間**問題：這個符號在哪、誰呼叫它、它的型別是什麼。它們把 codebase 當成一張攤平的靜態快照。

但 code 不是憑空長出來的。每一行都是某個人、在某個時間、為了某個理由寫下的。而讀碼真正卡人的問題，往往是**時間**問題：

- 這個看起來多餘的 `if` 為什麼在這？（八成是修某個 bug 補上的守衛）
- 這個 magic number `511` 從哪來、憑什麼是 511？
- 這段邏輯上禮拜還好好的，這禮拜壞了，是哪個改動害的？
- 這個函式現在這麼複雜，它一開始是什麼樣子、怎麼一步步長成這樣的？

**這些問題沒有一個能靠讀當前的 source 回答。** 註解會過期、會說謊、會描述早已被重構掉的舊邏輯；變數名會誤導；但 **commit 是不可變的歷史事實**——它精確記錄了「誰、何時、改了哪幾行、以及（如果作者有良心的話）為什麼」。

這就是為什麼資深工程師讀陌生 code 時，`git blame` 和 `git log` 的使用頻率跟 rg 不相上下。他們不是在讀 code，他們在**做考古**：從地層（commit 歷史）裡挖出每一塊石頭（每一行）的沉積年代與成因。

> 一句話心法：**當前的 code 告訴你「是什麼」（what），commit 歷史才告訴你「為什麼」（why）。** 而讀懂陌生 code 的瓶頸，九成在 why。

## 底層機制：git 怎麼讓「考古」變快

理解三件事，後面的指令就不是死記了。

**1. 每個 commit 是一個完整快照 + 差異可算。** git 物件模型裡，commit 指向一棵 tree（整個檔案系統的快照）。任意兩個 commit 之間的 diff 是**現算**出來的，不是儲存的。所以 `git log -p`、`-S`、`-G` 本質上都是「遍歷歷史、對相鄰快照算 diff、再對 diff 做過濾」。這也是為什麼在大 repo 上這些指令會慢——它在算成千上萬次 diff。

**2. blame 是「反向 diff 歸因」。** `git blame file` 對每一行問：「這一行最後一次被改動，是哪個 commit？」它從 HEAD 往回走，逐 commit 比對，把每一行歸因到最後動它的那個 commit。這是個 O(歷史深度) 的操作，所以 blame 一個改動頻繁的大檔案會有可感的延遲。

**3. bisect 是二分搜尋，把 O(n) 的「哪個 commit 引入了 X」降成 O(log n)。** 如果 X 是「某行為第一次出現/消失」，而你有一個能自動判定「這個 commit 有沒有 X」的測試，bisect 能在 log₂(n) 步內定位——12205 個 commit 只需約 13 次判定。

記住這三點：**diff 過濾（log -S/-G）、行歸因（blame）、二分定位（bisect）**——git 考古的三大原語。

## 武器一：`git log -S` / `-G` —— pickaxe，找「某段文字何時進出歷史」

這是被嚴重低估的功能。日常你用 `git log` 看某檔案的歷史，但那會列出「所有動過這檔的 commit」，雜訊巨大。你真正想問的通常是精準得多的問題：

> 「`MAXMEMORY_FLAG_LFU` 這個旗標**是哪個 commit 第一次引入**的？」

`-S<string>`（pickaxe-S）回答的正是這個：**列出「讓這段字串的出現次數發生變化」的 commit**——也就是加入它或刪除它的那幾個 commit，而不是「碰巧改到同一檔」的那一堆。

在 redis 上真跑（找 LFU 這個 maxmemory 淘汰策略是何時進 codebase 的）：

```
$ git log --oneline -S 'MAXMEMORY_FLAG_LFU' -- src/server.h
5d07984c5 LFU: Redis object level implementation.
```

一條指令，從 12205 個 commit 裡精準撈出**唯一**引入這個旗標的 commit。接著看它的完整 message——這才是重點：

```
$ git show -s --format='commit %h%nAuthor: %an%nDate:   %ad%n%n%s%n%n%b' 5d07984c5
commit 5d07984c5
Author: antirez
Date:   Fri Jul 15 12:12:52 2016 +0200

LFU: Redis object level implementation.

Implementation of LFU maxmemory policy for anything related to Redis
objects. Still no actual eviction implemented.
```

看到了嗎？commit message 直接告訴你這個功能的**設計脈絡**：這是 LFU（Least Frequently Used）淘汰策略在物件層的實作，而且作者誠實標註「Still no actual eviction implemented」——當下只鋪了地基，真正的淘汰是後續 commit 補的。你要是只讀當前 code，永遠不會知道這個演化順序，而順序恰恰是理解一個功能怎麼拆解的鑰匙。

`-G<regex>` 是 `-S` 的正則版，語義略有不同：`-S` 只在乎「出現次數變了沒」，`-G` 則列出「diff 內容裡有匹配這個 regex 的**任何增刪行**」的 commit。想追一個**程式碼模式**（不只是固定字串）的演化，用 `-G`：

```
$ git log --oneline -G 'server\.lfu_' -- src/evict.c
06ca9d683 LFU: Fix LFUDecrAndReturn() to just decrement.
583c31472 LFU: do some changes about LFU to find hotkeys
53cea9720 LFU: change lfu* parameters to int
6854c7b9e LFU: make counter log factor and decay time configurable.
```

這四個 commit 勾勒出 `server.lfu_*` 這組欄位的**調參演化史**：先能設定（configurable）、再改型別（int）、再調演算法（find hotkeys）、再修 bug（just decrement）。你等於免費得到了一份「這個機制被反覆打磨」的地圖，而每個 commit message 都是一小段設計筆記。

> **何時用哪個**：找固定識別字/字串首次出現（新函式名、新旗標、新設定項）用 `-S`；追一個會變形的程式碼模式（某個運算式、某種呼叫慣例）用 `-G`。兩者都務必加 `-- <path>` 把範圍限縮到檔案，否則在大 repo 上會慢且雜。

## 武器二：`git log -L` —— 追一個函式（或一行）的完整生命史

`-S/-G` 找的是「某文字進出的那幾個 commit」。但有時你要的是更完整的東西：**這個函式從誕生到現在，每一次被改動的完整 diff 序列**。這是 `-L` 的主場。

語法 `-L :<funcname>:<file>`：git 用檔案型別對應的函式邊界規則，自動框出這個函式，然後給你它的完整演化史。在 redis 上追 `lookupKeyReadWithFlags`（redis 讀取一個 key 的核心函式）：

```
$ git log -L ':lookupKeyReadWithFlags:src/db.c' --format='%h %ad %s' --date=short \
    | grep -E '^[0-9a-f]{7,} 20'
acf3495eb 2021-11-28 Sort out the mess around writable replicas and lookupKeyRead/Write (#9572)
62a197516 2021-03-10 key miss stat increment was misplaced (#8630)
8f9958dc2 2021-02-09 Fix typo and some out of date comments (#8449)
f8ae99171 2020-11-18 EXISTS should not alter LRU, OBJECT should not reveal expired keys on replica (#8016)
1c7103854 2020-09-10 Squash merging 125 typo/grammar/comment/doc PRs (#7773)
040e52c77 2019-03-21 Renamed event name from "miss" to "keymiss"
99c2fe0bc 2019-03-21 added special flag for keyspace miss notifications
8620a434a 2019-03-19 Added keyspace miss notifications support
5ddd50762 2018-10-19 if we read a expired key, misses++
93238575f 2018-07-01 Fix typo
5877c02c5 2017-06-13 Fix PERSIST expired key resuscitation issue #4048.
41d804d9d 2016-06-14 TTL and TYPE LRU access fixed. TOUCH implemented.
32f80e2f1 2015-07-27 RDMF: More consistent define names.
06e76bc3e 2014-12-10 Better read-only behavior for expired keys in slaves.
b80b1c591 2012-02-01 Only incremnet stats for key miss/hit when the key is semantically accessed in read-only.
```

這份時間軸是一堂濃縮課。你看到這個「讀 key」的函式並不是一開始就長這麼複雜，它的複雜性是被**一連串真實需求逼出來的**：2012 年加了 read-only 情境下的統計、2014 年處理 slave 上過期 key 的讀取行為、2017 年修 PERSIST 復活過期 key 的 bug（#4048）、2018–2019 年圍繞 keyspace miss 通知反覆調整、2021 年為了 writable replica 做大重構（#9572）。

**這就是逆向一個「為什麼這麼複雜」的函式的正解**：不要試圖一次讀懂它現在的全部分支，而是沿著它的演化時間軸走一遍，每一段複雜性都對應一個當初的具體需求或 bug。理解「這行是為了修 #4048」比盯著它猜半天有效率一個數量級。

`-L` 也接受行號範圍 `-L <start>,<end>:<file>`，用來追不在函式內、或跨函式的特定幾行。

## 武器三：`git blame -C -M` —— 一行的責任歸屬，穿透搬移與重命名

`git blame` 你大概用過：對每一行標出「最後動它的 commit + 作者 + 日期」。但陌生 code 上直接用 blame 常常踩到一個坑：**你 blame 到的可能是一次「大搬家」，而不是「真正寫這行邏輯」的那次 commit。**

看 redis 的主迴圈那一行（Ch 0 我們用 cscope 定位到 `server.c:7251` 的 `aeMain(server.el)`）：

```
$ git blame -L 7251,7251 src/server.c
39ca1713d7 src/redis.c (antirez 2011-12-01 12:15:44 +0100 7251)     aeMain(server.el);
```

注意左邊那欄寫的是 **`src/redis.c`**，不是 `src/server.c`。這揭露了一段歷史：redis 的主檔案在早期叫 `redis.c`，後來才被拆分/更名為 `server.c`。git 的預設 blame 在這裡已經幫你穿透了檔案更名，把這行歸因到它 2011 年在 `redis.c` 裡真正被寫下的那一刻，而不是「搬進 server.c」的那次 commit。

`-M` 和 `-C` 讓這種穿透更強：

- **`-M`（detect Moves）**：偵測同一檔案內、或更名檔案間的**行搬移**。一行被從函式 A 剪下貼到函式 B，普通 blame 會歸因到「貼上」的那次 commit；`-M` 會追回它「原本被寫下」的 commit。
- **`-C`（detect Copies）**：更進一步，偵測從**其他檔案複製**過來的行。一段邏輯從 `foo.c` 複製到 `bar.c`，`-C` 能追回它在 `foo.c` 的原始出處。`-C -C`、`-C -C -C` 逐級加強（也逐級變慢）。

```
$ git blame -C -M -L 7251,7251 src/server.c
39ca1713d7 src/redis.c (antirez 2011-12-01 12:15:44 +0100 7251)     aeMain(server.el);
```

這行的結果沒變（因為預設已經追到更名前了），但在**大重構頻繁**的專案裡，`-C -M` 是把「一行的真正作者與意圖」從「搬運工 commit」底下挖出來的必備開關。

> **踩雷預告**：不加 `-C -M` 時，你很容易把一次無意義的 `clang-format` 全檔重排、或一次檔案搬家，誤認成「這行邏輯的來源」，然後跑去讀一個跟這行邏輯毫無關係的 commit message，白忙一場。陌生 code 上 blame，養成加 `-C -M` 的習慣。

搭配 `git log --follow` 可以在**檔案層級**做同樣的穿透——它讓 `git log <file>` 跨越更名邊界。看差別：

```
$ git log --oneline src/server.c            | wc -l
975
$ git log --oneline --follow src/server.c   | wc -l
1488
```

不加 `--follow`，你只看到 `server.c` 這個名字存在之後的 975 個 commit；加了 `--follow`，git 追過更名邊界，把它前身 `redis.c` 時代的歷史也接上，總共 1488 個。**少了那 513 個 commit，你就少了這個檔案一半的身世。**

## 武器四：`git bisect` —— 二分定位「行為是哪個 commit 變的」

前面三招都在讀「某段文字/某個函式」的歷史。`bisect` 解的是另一類問題，而且是最戲劇性的一類：

> 「這個行為（一個 bug、一個功能、一個效能退化）**是在哪個 commit 引入的**？我只知道舊版沒有、新版有。」

手動找是災難：幾千個 commit，一個個 checkout + 編譯 + 測試，天荒地老。bisect 用二分把它變成 log₂(n) 次。核心是你要提供三樣東西：

1. 一個 **bad** 端點（有這個行為的 commit，通常 HEAD）
2. 一個 **good** 端點（沒有這個行為的較舊 commit）
3. 一個**判定腳本**：對任意 commit，自動回答「這裡有沒有這個行為」（exit 0 = good，exit 非 0 = bad）

我們拿一個可完全自動化、可重現的例子：**`allkeys-lfu` 這個 maxmemory 設定值是哪個 commit 加進 `src/config.c` 的？** 判定方式很單純——`git grep` 這個檔裡有沒有這個字串。先找一個夠舊的 good 端點（2015 年底、LFU 出現之前）：

```
$ git log --format='%h %ad' --date=short -1 --before=2016-01-01
b1f84d41f 2015-12-29
```

然後跑全自動 bisect（`git bisect run` 讓 git 自己驅動整個二分過程）：

```
$ git bisect start HEAD b1f84d41f
Bisecting: 3359 revisions left to test after this (roughly 12 steps)

$ git bisect run bash -c 'git grep -q "allkeys-lfu" -- src/config.c && exit 1 || exit 0'
Bisecting: 1677 revisions left to test after this (roughly 11 steps)
Bisecting: 839 revisions left to test after this (roughly 10 steps)
Bisecting: 418 revisions left to test after this (roughly 9 steps)
Bisecting: 209 revisions left to test after this (roughly 8 steps)
Bisecting: 104 revisions left to test after this (roughly 7 steps)
Bisecting: 52 revisions left to test after this (roughly 6 steps)
Bisecting: 26 revisions left to test after this (roughly 5 steps)
Bisecting: 12 revisions left to test after this (roughly 4 steps)
Bisecting: 6 revisions left to test after this (roughly 3 steps)
Bisecting: 2 revisions left to test after this (roughly 2 steps)
Bisecting: 0 revisions left to test after this (roughly 1 step)
Bisecting: 0 revisions left to test after this (roughly 0 steps)
5d07984c5d48d6253ea5884d69da3f06cdc90f1b is the first bad commit
```

**約 3300 個候選 commit，13 步就收斂。** 判定腳本裡 `git grep -q "allkeys-lfu"` 命中就 `exit 1`（在 bisect 語義裡「bad」=「已經有這行為」），否則 `exit 0`（good）。git 自動 checkout 每個中點、跑腳本、根據結果縮半，最後吐出那個「第一個引入此行為的 commit」——正是我們前面 pickaxe 找到的 `5d07984c5`（LFU 實作），互相印證。

跑完務必還原，否則你的工作區還停在某個 bisect 中點：

```
$ git bisect reset
```

真實世界的 bisect 判定腳本通常是「編譯 + 跑一個測試」：

```bash
git bisect run bash -c 'make -j$(nproc) >/dev/null 2>&1 && ./run_repro_test.sh'
```

如果某個 commit 根本編不過（無法判定 good/bad），腳本可以 `exit 125`，bisect 會跳過它。這是追迴歸 bug 最快的工業級手法——**Linux kernel 社群報 bug 幾乎都要求先 bisect 出 offending commit**，就是這個原因。

## 心法：commit message 與 PR 才是「設計意圖」的所在

前面的指令都是為了同一個終極目的：**從一行 code 導航到「解釋這行為什麼存在」的那段文字**。而那段文字，永遠不在 code 裡，在 commit message 和 PR 討論裡。

看一個完美示範。redis 的監聽 socket 有一行 `listen(fd, 511)`——這個 `511` 是哪來的、為什麼是這個數？pickaxe 追它的來歷：

```
$ git log --oneline -S '511' -- src/anet.c
d76aa96d1 Add support for listen(2) backlog definition
...
```

打開這個 commit 的 message：

```
$ git show -s --format='commit %h%nAuthor: %an%nDate:   %ad%n%n%s%n%n%b' d76aa96d1
commit d76aa96d1
Author: Nenad Merdanovic
Date:   2013-11-08

Add support for listen(2) backlog definition

In high RPS environments, the default listen backlog is not sufficient, so
giving users the power to configure it is the right approach, especially
since it requires only minor modifications to the code.
```

**這就是答案，而且只有這裡有。** 這個 backlog 常數是為了「高 RPS（每秒請求數）環境下預設 backlog 不夠用」而做成可設定的。source 裡的那行 `listen(fd, backlog)` 只告訴你「它呼叫了 listen」；commit message 告訴你「為什麼需要能調這個值、在什麼場景會踩到預設值的天花板」。這種**設計權衡的知識**，是你讀一萬遍當前 code 都得不到的。

推而廣之，讀陌生 code 時的黃金反射動作：**遇到任何看不懂「為什麼要這樣」的一行，第一件事不是硬想，是 `git log -S`/`blame` 找到它的 commit，讀 message，若 message 裡有 PR 編號（如 `#9572`）就上 GitHub 讀那個 PR 的討論串。** 好的專案（redis、Linux、CPython）的 PR 討論裡，有大量「我們考慮過 A 方案但因為 X 放棄」「這個 edge case 是 reviewer 指出的」——這些是設計決策的**完整推理過程**，比任何文件都珍貴。

> commit message 的品質決定了一個 codebase 的「可考古性」。這也反過來提醒你自己寫 commit 時：未來某個讀你 code 的人（很可能是你自己）會靠這段 message 活命。寫「why」，不要只寫「what」——diff 已經說了 what。

## 對比與取捨

| 工具 | 回答的問題 | 輸入 | 何時用 | 弱點 |
|---|---|---|---|---|
| `git blame [-C -M]` | 這一行/這幾行最後是誰、何時、為何改的 | 檔案 + 行範圍 | 盯著某幾行問「來歷」 | 只給「最後一次」改動；要看完整演化得再 `log -L` |
| `git log -S<str>` | 某段固定文字何時被加入/刪除 | 字串 + 路徑 | 追新符號/旗標/設定項的首次出現 | 只認字面；重排/重命名會干擾計數 |
| `git log -G<re>` | 某程式碼模式的增刪散落在哪些 commit | 正則 + 路徑 | 追會變形的模式的演化 | 比 -S 雜訊多，需慎選 regex |
| `git log -L :fn:file` | 一個函式從生到今的完整 diff 序列 | 函式名 + 檔 | 逆向「為什麼這函式這麼複雜」 | 函式邊界偵測偶爾失準；輸出量大 |
| `git bisect run` | 某行為是哪個 commit 引入的 | good/bad 端點 + 判定腳本 | 追迴歸 bug、找功能引入點 | 需可自動判定；歷史中有編不過的 commit 會拖慢 |
| `git log --follow file` | 一個檔案跨更名的完整歷史 | 檔案 | 檔案被更名/搬移過 | 只在檔層級追，不追函式搬移 |

**組合拳（實戰最常見的一套）**：`blame -C -M` 定位到 commit → `git show` 讀那個 commit 的 message 與 diff → 若不夠，`log -L` 看整個函式的演化 → 若是追某行為的引入，`bisect` 二分定位 → 全程把 PR 編號記下來上 GitHub 讀討論。

## 踩雷集錦

1. **淺 clone 上做考古，得到「只有一個 commit」的荒謬結果**。
   - 錯誤直覺：「這個 repo 怎麼只有一個 commit，git 壞了？」
   - 正確認識：`git clone --depth 1` 只抓一個 commit，`log`/`blame`/`bisect` 全部退化。先 `git fetch --unshallow` 補回完整歷史（或一開始就完整 clone）。這是本章第一坑，Ch 0 已預告。

2. **blame 到一次「大搬家/格式化」，跑去讀無關的 commit**。
   - 錯誤直覺：「這行是這個 commit 寫的，來讀它 message。」結果那個 commit 是 `clang-format` 全檔重排或檔案更名，message 跟這行邏輯毫無關係。
   - 正確認識：陌生 code 上 blame **一律加 `-C -M`** 穿透搬移；若懷疑碰到格式化 commit，用 `git blame <格式化commit>^ -- file`（該 commit 的 parent）跳過它再 blame 一次，或用 `.git-blame-ignore-revs` 忽略清單。

3. **`git log <file>` 少了一半歷史，還以為自己看全了**。
   - 錯誤直覺：「這檔就這些 commit。」
   - 正確認識：檔案若被更名過，不加 `--follow` 會在更名邊界斷掉。redis 的 `server.c` 少 `--follow` 就少了 513 個（前身 `redis.c` 時代的）commit。任何「歷史看起來異常短」的檔案，先懷疑更名，加 `--follow` 驗證。

4. **pickaxe `-S` 不加 `-- <path>`，在大 repo 上慢到懷疑人生且結果雜亂**。
   - 錯誤直覺：「`-S` 好慢，這功能沒用。」
   - 正確認識：不限路徑等於對每個 commit 的**全 repo diff** 做過濾。永遠加 `-- <file>` 或 `-- <dir>` 縮範圍。真的要全域搜，接受它慢，並考慮先用 rg 定位到檔再 pickaxe。

5. **bisect 跑完忘了 `git bisect reset`，之後所有操作都在一個 detached 的中點 commit 上**。
   - 錯誤直覺：「我怎麼在一個奇怪的 commit 上、branch 不見了？」
   - 正確認識：bisect 過程中你的工作區停在某個歷史中點，**跑完/中斷都要 `git bisect reset`** 回到原本的 HEAD。養成 reset 收尾的肌肉記憶。

## 進階：再往深一層

- **`.git-blame-ignore-revs`**：專案可以列一份「blame 時要無視的 commit 清單」（通常是大型格式化/重排 commit），blame 自動穿透它們歸因到真正的邏輯作者。Chromium、React 等大專案都有這檔。你自己的專案做一次大 reformat 後，把那個 commit 加進去，全隊的 blame 從此乾淨。設定 `git config blame.ignoreRevsFile .git-blame-ignore-revs` 讓它預設生效。

- **`git log --merges` 與讀 merge commit**：GitHub PR 合併通常產生 merge commit，其 message 常含 PR 標題與編號。`git log --merges --oneline -- <file>` 專門撈這些「一個功能整包進來」的節點，比逐個 squash commit 更能看清「一次做了什麼大事」。

- **`git bisect skip` 與 `exit 125`**：歷史中總有編不過、或無法判定的 commit。互動式 bisect 用 `git bisect skip`；自動 `bisect run` 腳本 `exit 125` 告訴 git「這個跳過」。善用它，bisect 才能穿過一整段「當時就是壞的」的歷史區間。

- **`git log -L` 追跨越檔案搬移的函式**：函式被整個搬到另一個檔案時，單純 `-L :fn:oldfile` 會在搬移點斷。這時退回用 `-S 函式名` 找到搬移 commit，再從新檔繼續 `-L`。git 對「函式跨檔搬移」的自動追蹤仍不完美，這是手動接力的地方。

- **把考古自動化進 review 流程**：`git log -p --since='2 weeks' -- <你負責的模組>` 是很好的「我離開兩週，這模組發生了什麼」掃描；`git shortlog -sn -- <file>` 一秒看出「這檔的主要維護者是誰」——onboarding 找人問問題時的神器。

## 動手練習

在 unshallow 後的 redis 沙包上做（都能真跑出結果）：

1. **pickaxe 找首次出現**：用 `git log --oneline -S 'lazyfree-lazy-eviction' -- src/config.c` 找出 lazyfree eviction 這個設定項是哪個 commit 引入的，讀它的 commit message，寫一句話說明它為什麼被加。

2. **函式演化史**：對 `src/db.c` 裡另一個函式（如 `dbAdd` 或 `setKey`）跑 `git log -L ':函式名:src/db.c'`，數出它被改過幾次，挑其中一個「Fix」開頭的 commit 讀懂它修了什麼 bug。

3. **穿透搬移的 blame**：找 `src/server.c` 裡任一行你好奇的邏輯，先用普通 `git blame -L n,n`，再用 `git blame -C -M -L n,n`，比較兩者歸因的 commit 是否不同；若不同，讀懂為什麼（多半碰到了搬移/更名）。

4. **自動 bisect**：挑一個「新版有、舊版沒有」的字串（例如某個較新的命令名或設定項），自己設好 good/bad 端點，寫一行 `git bisect run` 判定腳本，把它引入的 commit 二分出來，並用 `-S` pickaxe 驗證兩者一致。做完記得 `git bisect reset`。

5. **`--follow` 驗證更名**：對 `src/server.c` 跑 `git log --oneline` 與 `git log --oneline --follow`，算出兩者 commit 數差多少，找出「更名邊界」是哪個 commit（提示：`e2641e09c redis.c split into many different C files`）。

## 本章重點整理

- 當前 code 說「是什麼」，**commit 歷史說「為什麼」**——讀碼的 why 瓶頸靠考古解。
- 三大原語：**diff 過濾**（`log -S/-G`）、**行歸因**（`blame`）、**二分定位**（`bisect`）。
- `-S` 找固定文字進出、`-G` 找程式碼模式演化、`-L :fn:file` 看函式完整生命史。
- `blame` 陌生 code 上**一律加 `-C -M`** 穿透搬移/複製，否則會歸因到搬運工 commit；檔案層級用 `git log --follow` 穿透更名。
- `git bisect run` + 自動判定腳本，把「哪個 commit 引入此行為」從 O(n) 降到 O(log n)；跑完務必 `git bisect reset`。
- 終極目的是導航到 **commit message / PR 討論**——設計意圖與權衡只活在那裡。好的 message 寫 why，diff 已經說了 what。

## 自我檢核

- [ ] 面對一行「看不懂為什麼要這樣」的 code，你能不能立刻說出三個把它的來歷挖出來的指令？
- [ ] `-S` 和 `-G` 的差別是什麼？各自何時用？
- [ ] 為什麼陌生 code 上 blame 要加 `-C -M`？不加會踩什麼坑？
- [ ] 給你「舊版沒 bug、新版有 bug」和一個可重現腳本，你能寫出一條 `git bisect run` 定位 offending commit 嗎？跑完要做什麼收尾？
- [ ] 為什麼 `git log <file>` 有時只顯示一半的歷史？怎麼補齊？
- [ ] 一句話：為什麼「設計意圖」不在 code 裡而在 commit message／PR 裡？

## 延伸閱讀

- **[Pro Git — "Git Tools: Debugging with Git"](https://git-scm.com/book/en/v2/Git-Tools-Debugging-with-Git)**
  - **讀哪裡**：整節，重點看 `git blame` 的 `-C`/`-M`/`-L` 與 `git bisect`（含 `bisect run`）兩小節。
  - **學到什麼**：官方對 blame 移動偵測與 bisect 自動化的權威說明，把本章的直覺補上精確語義。
  - **關聯**：本章武器三、四的地基文件。

- **[git-log 官方 man page — pickaxe（`-S`/`-G`）與 `-L` 一節](https://git-scm.com/docs/git-log)**
  - **讀哪裡**：搜 "pickaxe" 與 "-L" 兩段；`--pickaxe-regex`、`-G` 與 `-S` 的差異定義寫得很精準。
  - **學到什麼**：`-S`（出現次數變化）與 `-G`（diff 含匹配行）語義差別的官方定義——這是很多人用錯的地方。
  - **前提**：知道 diff 的基本概念即可。

- **[Linux Kernel — "Bisecting a bug"（`Documentation/admin-guide/bug-bisect.rst`）](https://www.kernel.org/doc/html/latest/admin-guide/bug-bisect.html)**
  - **讀哪裡**：整份很短，看它如何要求 reporter 用 bisect 縮到單一 commit、以及 `bisect skip` 的實務用法。
  - **學到什麼**：工業級專案怎麼把 bisect 當成 bug 報告的**標準流程**，而非偶爾的技巧。讀完你會理解為什麼「能 bisect」是資深工程師的基本功。
  - **關聯**：本章武器四在真實大專案的落地形態。

- **[How to Write a Git Commit Message（cbeams）](https://cbea.ms/git-commit/)**
  - **讀哪裡**：七條規則裡最該內化的是「用 body 解釋 why 而非 how」。
  - **學到什麼**：從「未來考古者」的視角反推「現在該怎麼寫 commit」——這章教你挖別人的 message，這篇教你別讓後人挖你的時候一無所獲。
  - **關聯**：本章「心法」一節的另一面。

考古挖出了「一行為什麼存在」與「它怎麼演化」。但有些理解問題連歷史都答不了——「這條 code 路徑**跑起來到底有沒有走到**、走的時候變數是什麼值」。靜態讀與歷史考古都到頂了，該讓程式**動起來**。下一章我們把 gdb 從除錯工具重新框架成**讀碼工具**。

→ [Ch 18 debugger-driven reading](./18-debugger-driven-reading.md)
