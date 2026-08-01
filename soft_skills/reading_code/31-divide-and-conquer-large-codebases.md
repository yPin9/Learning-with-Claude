# Ch 31 — 大型專案的分而治之

> **目標**：面對 Linux/Chromium/LLVM 這種百萬行等級的巨獸，「讀懂它」不是目標、也做不到，甚至有害。本章教你把尺度從十萬、百萬行收斂到「你要動的那幾百行」的分而治之策略：子系統隔離、主動無視、介面優先、按需深潛、用 build target 切範圍，而最鋒利的裁剪器是**你的具體任務**。沒有任務的漫遊，會在百萬行裡淹死。本章拿 redis（14 萬行）真跑一次收斂，示範任務如何把範圍砍到 59 行。

## 為什麼這章存在

Ch 11 講過「從 50 萬行收斂到你要改的 200 行」。這章是它的放大版：當專案不是 50 萬而是**500 萬到 2000 萬行**（Linux kernel 約 3000 萬、Chromium 約 4000 萬、LLVM 約數百萬），Ch 11 的技巧還在，但有幾件事**質變**了：

- **你連 grep 都會被淹死**：在 Chromium 裡 `rg "Init"` 回你幾萬筆，讀不完。
- **你不可能建立全局架構地圖**：沒有人腦裝得下 Chromium 的完整架構，包括寫它的人。
- **「讀懂整個專案」變成一個有害的目標**：它讓你漫無目的地讀，消耗掉本該用在任務上的注意力，最後什麼都沒讀進去。

大型專案讀碼的心態轉變是：**你的目標不是理解這個專案，而是完成一個具體任務。** 理解只是副產品，而且只需要理解到「夠完成任務」的程度。這個轉變聽起來消極，實際上是唯一能在百萬行裡生存的姿態。專業的大型 codebase 工作者跟新手最大的差別，不是他們讀得多，而是他們**故意讀得少、讀得準**。

## 先給直覺：巨大 codebase 是城市，不是房子

小專案像一棟房子，你可以每個房間都走一遍。百萬行專案是一座城市——你不會「逛完整座城市」，你是「為了辦一件事，導航到某個地址，辦完就走」。

```
   小專案（讀得完）              巨型專案（讀不完，也不該讀完）
 ┌──────────────┐            ┌────────────────────────────────┐
 │ 走遍每個房間  │            │ 城市：99% 的街區跟你今天的事無關 │
 │ 建全局地圖    │            │ 你只需要：從入口導航到目標地址   │
 │ 理解整體      │            │ 沿途「主動無視」無關街區         │
 └──────────────┘            │ 只在目標那棟樓「深潛」進去看細節  │
                             └────────────────────────────────┘
```

這個比喻帶出本章的核心動作：**導航**（用任務找路）與**主動無視**（辨識並跳過無關的 99%）。後者反直覺——讀碼新手總覺得「跳過沒讀完的部分」是偷懶、是心虛。在百萬行專案裡剛好相反：**主動無視是核心技能，不是偷懶**。你必須練到「一眼看出這 500 個檔案跟我的任務無關」然後心安理得地跳過。

## 底層機制：分而治之的六把刀

### 刀一：任務是最鋒利的裁剪器

一切從任務開始。**沒有具體任務就進大型 codebase = 在城市裡沒有目的地亂走**，保證迷路。任務把「無限的可能」壓成「有限的相關」。

- 壞問法：「我想理解 redis。」→ 範圍無限，讀一年也讀不完。
- 好問法：「`SETRANGE key offset value` 這個命令，當 offset 超大時 redis 怎麼防止爆記憶體？」→ 範圍瞬間收斂到一個命令、一個檢查函式。

任務越具體，裁剪越狠。把模糊的「理解 X」逼成「X 在情境 Y 下走哪條路、改了什麼」。

### 刀二：子系統隔離——一次只碰一塊

大專案是由子系統組成的（kernel：排程/mm/vfs/net/…；redis：網路層/資料型別/持久化/複製/叢集）。**一次只把一個子系統放進工作記憶**，其餘當黑箱。你追 SETRANGE 時，複製（replication）、叢集（cluster）、AOF 持久化全都是黑箱——它們存在，但今天與你無關。

### 刀三：介面優先——讀 header/公開 API，不讀實作

想知道某個子系統「做什麼」，讀它的**介面**（header、公開函式簽章、文件）就夠了，不需要讀它的**實作**。`dbAdd(db, key, val)` 這個簽章告訴你「把 key-val 加進 db」，你不需要讀 `dbAdd` 內部的 hash table rehash 邏輯——除非你的任務就是改 rehash。介面是子系統之間的「合約」，讀合約比讀履約過程省十倍力氣。

### 刀四：按需深潛（need-to-know）

只在「任務逼你必須理解細節」的那個點，才深潛進實作。追 SETRANGE 時，你深潛進 `setrangeCommand` 和它的安全檢查 `checkStringLength`——因為那正是任務關心的。至於 `sdsnewlen` 內部怎麼配置記憶體，除非你懷疑 bug 在那，否則停在「它配一塊 N bytes」的抽象層，不深潛。**深潛是昂貴操作，只在必要處動用。**

### 刀五：用 build target 切範圍

大專案的 build 系統（Ch 21）已經幫你把 code 分成模組/target/library。「我的任務只碰 string 命令」→ 只有 `t_string.c` 相關的編譯單元進入視野，其他 100 多個 `.c` 可以主動無視。build 圖是天然的範圍切割器——它告訴你哪些檔案會被連進你關心的那個 binary/模組。

### 刀六：主動無視當成一等公民技能

前五把刀的共同產物是一大堆「與任務無關」的 code。**你要練的是心安理得地不讀它們**。判斷「這段跟我無關」並跳過，跟「讀懂這段」一樣是需要練的技能。在百萬行專案裡，你 95% 的動作是「跳過」，5% 是「細讀」——把跳過做好，細讀才有餘裕。

主動無視不是「亂猜然後跳過」，它有具體的判斷依據。給你的任務「SETRANGE 記憶體安全」，看 redis 的檔名清單（`ls src/*.c`），你能靠**檔名 + 領域知識**在幾秒內給每個檔標相關性：

```
cluster_legacy.c  replication.c  sentinel.c   → 叢集/複製/哨兵，跟單機字串命令無關 → 無視
rdb.c  aof.c                                   → 持久化，SETRANGE 不經過 → 無視
t_zset.c  t_hash.c  t_list.c  t_stream.c       → 其他資料型別 → 無視
module.c (1.4萬行)                             → 外掛 API，不是核心命令路徑 → 無視
t_string.c  sds.c  object.c                    → 字串型別/字串庫/物件層 → 相關，可能要讀
server.c  networking.c                         → 命令分派/網路，介面優先掃一眼 → 邊緣
```

這個標記動作靠的是「檔名慣例 + 你對這個領域的先驗知識」（redis 的 `t_*.c` 是各資料型別、`sds.c` 是字串庫）。你不需要打開任何一個檔就完成了 90% 的裁剪。**這才是主動無視的正確樣子**：有依據的快速分類，不是心虛的略過。分類錯了會怎樣？頂多之後追 data flow 時發現漏了一個檔，補讀就好——成本遠低於「每個檔都打開讀一遍」。

## 真跑範例：用任務把 redis 從 14 萬行砍到 59 行

redis 不是百萬行，但足以示範收斂機制（尺度更大時方法完全相同，只是每一刀砍掉更多）。先看戰場有多大（真跑輸出）：

```
$ cd ~/reading_code_lab/redis
$ cloc --quiet src/ | grep -E "^C |^SUM"
C          115  15755  32001  100023
SUM:       694  19189  35121  143472
$ ls src/*.c | wc -l
107
```

14 萬行、107 個 `.c`。最大的幾個檔案本身就勸退（真跑）：

```
$ wc -l src/*.c | sort -rn | head -5
 14002 src/module.c
 10815 src/redis-cli.c
  7256 src/server.c
  6498 src/cluster_legacy.c
  5463 src/sentinel.c
```

光 `module.c` 一個檔就 1.4 萬行。**如果你的目標是「理解 redis」，你已經輸了**——你會從 `server.c` 開始一路讀到天荒地老。改成有任務。

### 任務：「SETRANGE 在 offset 超大時怎麼防爆記憶體？」

**刀一（任務裁剪）** 已經生效：這個問題只關心一個命令 + 它的記憶體安全檢查。**刀二（子系統隔離）**：這是「字串資料型別」子系統，複製/叢集/持久化/module 全部黑箱。107 個檔立刻只剩「string 相關」的少數幾個。

### 收斂步驟（真跑）

**Step 1 — 一個 grep 定位命令**（不是漫遊，是導航）：

```
$ rg -n "\"setrange\"|setrangeCommand" src/commands.def | head -1
11225:{MAKE_CMD("setrange","Overwrites a part of a string value ...",
       ...,setrangeCommand,4,CMD_WRITE|CMD_DENYOOM,...)
```

命令表告訴你：`SETRANGE` 的 handler 是 `setrangeCommand`，且帶 `CMD_DENYOOM` flag（記憶體不足時拒絕——已經跟你的「防爆記憶體」任務相關了）。

**Step 2 — 跳到 handler，量它多大**：

```
$ rg -n "setrangeCommand" src/t_string.c
421:void setrangeCommand(client *c) {
$ awk '/^void setrangeCommand/,/^}/' src/t_string.c | wc -l
59
```

從 143,472 行，一個命令表查詢 + 一次定位，收斂到 **59 行**的實作。這 59 行就是你今天要細讀的全部。**刀五（build target）** 在這裡的體現是：你根本不需要碰其他 106 個 `.c`——它們雖然會被連進 `redis-server`，但都在你任務的視野外。

**Step 3 — 用 cscope 拿到「只需要看的下一層」**（刀四，按需深潛的清單）：

```
$ cscope -b -q -R -s src
$ cscope -d -L -2 setrangeCommand    # -2 = setrangeCommand 呼叫了誰
src/t_string.c getLongFromObjectOrReply 426  if (getLongFromObjectOrReply(c,c->argv[2],&offset,NULL) ...
src/t_string.c lookupKeyWrite         434  o = lookupKeyWrite(c->db,c->argv[1]);
src/t_string.c checkStringLength      443  if (checkStringLength(c,offset,sdslen(value)) != C_OK)
src/t_string.c createObject           446  o = createObject(OBJ_STRING,sdsnewlen(NULL, offset+sdslen(value)));
src/t_string.c sdsnewlen              446  ...
```

cscope 把「這 59 行呼叫了哪些函式」列成一張清單。這是你的**深潛候選菜單**。你的任務是「防爆記憶體」，所以菜單裡 `checkStringLength` 這個名字直接命中——那就是你要深潛的**唯一**一個。`lookupKeyWrite`（查 key）、`createObject`（建物件）跟「防爆記憶體」關係遠，**主動無視**（刀六）：你不深潛它們，停在「查 key」「建物件」的抽象層。

**Step 4 — 只深潛命中的那一個**：

```
$ awk '/static int checkStringLength/,/^}/' src/t_string.c
static int checkStringLength(client *c, long long size, long long append) {
    if (mustObeyClient(c))
        return C_OK;
    long long total = (uint64_t)size + append;
    if (total > server.proto_max_bulk_len || total < size || total < append) {
        addReplyError(c,"string exceeds maximum allowed size (proto-max-bulk-len)");
        return C_ERR;
    }
    return C_OK;
}
```

任務回答了：redis 靠 `checkStringLength` 把 `offset + value長度` 跟 `proto_max_bulk_len` 比，超過就拒絕；`total < size || total < append` 是溢位保護。**整個任務從 14 萬行收斂到「59 行 handler + 12 行檢查函式」**，其餘 143,401 行你一行都沒讀，也不需要讀。

### 尺度放大時會怎樣

同樣這套刀，搬到 Chromium/LLVM：每一刀砍掉的比例更大（14 萬 → 71 行是砍掉 99.95%；4000 萬行專案裡砍掉的絕對量更驚人），而且你會更依賴 **build target**（GN/Bazel 的 target 圖直接告訴你哪些 `.cc` 進了你關心的那個 component）和 **code search 基礎設施**（Chromium 有 Code Search、內部有 Kythe 這種跨 repo 索引，因為 grep 真的會淹死）。方法不變，只是工具要更重。

### 換一個專案驗證同一套刀：curl（14 萬行）

方法不能只在一個 codebase 上有效。換 curl（`~/reading_code_lab/curl`，lib+src 約 14.5 萬行 C，137 個 `lib/*.c`）試同一套刀。任務：「curl 的 `-L`（跟隨 HTTP 重導向）邏輯在哪、上限怎麼防無限迴圈？」

**刀一（任務裁剪）+ 導航（真跑輸出）**：

```
$ cd ~/reading_code_lab/curl
$ ls lib/*.c | wc -l
137
$ rg -n "followlocation" lib/*.c | rg "maxredirs|>=" | head
lib/http.c:1184:       (data->state.followlocation >= data->set.maxredirs)) {
lib/http.c:1190:      data->state.followlocation++; /* count redirect-followings ... */
```

一個 grep 命中 `lib/http.c:1184`——`followlocation >= maxredirs` 正是「防無限重導向」的上限檢查，任務直接回答了：curl 用一個 `followlocation` 計數器對 `maxredirs` 比，達到就停。137 個檔你碰了 1 個，`lib/http.c` 以外全部**主動無視**（刀六）。

curl 這個例子還帶出一個真實世界的細節：`rg "followlocation" lib/*.c` 其實會回你一堆散在 `url.c`/`transfer.c`/`multi.c`/`getinfo.c` 的命中——**光 grep 一個名字在大專案就會給你雜訊**（踩雷 5）。是「防無限迴圈」這個**任務**幫你在那堆命中裡一眼挑出 `>= maxredirs` 那筆。沒有任務，你會把那 10 幾筆命中全讀一遍浪費半小時。任務是裁剪器，這個對照最能體現。

## 對比與取捨

| 策略 | 適用尺度 | 好處 | 風險 |
|---|---|---|---|
| **讀懂整個專案** | 小專案（幾萬行內） | 全局理解、能重構 | 百萬行專案上是災難，保證淹死 |
| **任務驅動收斂**（本章） | 任何尺度，越大越必要 | 有限時間內出結果 | 局部理解，可能漏掉全局後果（要靠 review 補） |
| **介面優先、不讀實作** | 想知道「做什麼」 | 省十倍力氣 | 若 bug 在實作細節就會漏 |
| **build target 切範圍** | 有清楚模組化的專案 | 天然精準的範圍 | build 圖複雜的專案（generated code、動態載入）會失準 |
| **全文 code search 基礎設施** | 巨型 monorepo | grep 淹死時的救命稻草 | 要有基礎設施（Kythe/Code Search），一般 repo 沒有 |

**實戰選擇**：專案越大，越要早、越要狠地用任務裁剪。介面優先是預設姿態，只在任務逼你時才按需深潛。**唯一要警惕的**是「局部理解漏掉全局後果」——你改的那 59 行可能有你沒讀到的呼叫者依賴它的舊行為（回到 Ch 8 data flow、Ch 9 call graph 反查呼叫者），這是任務驅動法要靠 code review（Ch 33）補的盲點。

## 踩雷集錦

1. **錯誤直覺：「我得先讀懂整個專案才能動手」。** → 正確認識：百萬行專案沒人讀得完，包括作者。目標是完成任務，理解是副產品，只需理解到「夠完成任務」。抱著「讀懂整個」進 Chromium，你會在第一週淹死且一事無成。
2. **錯誤直覺：跳過沒讀的 code 是偷懶、是心虛。** → 正確認識：主動無視是核心技能。在百萬行專案裡 95% 的動作就該是「判斷無關並跳過」。練到心安理得地跳過，你才有注意力細讀那關鍵 5%。
3. **錯誤直覺：沒有任務也能「先熟悉一下 codebase」。** → 正確認識：沒有具體任務的漫遊 = 城市裡沒目的地亂走，保證迷路且低效。先逼出一個具體任務（哪怕是自己編的「X 在情境 Y 下走哪」），再進場。
4. **錯誤直覺：要理解一個子系統就得讀它的實作。** → 正確認識：讀介面（header/公開 API/簽章）就知道它「做什麼」，讀實作才知道「怎麼做」。多數任務只需要前者。把「讀實作」當昂貴操作，按需深潛。
5. **錯誤直覺：grep 一下就能定位。** → 正確認識：在巨型專案 `rg` 常回你幾萬筆淹死你。要先用任務+子系統把範圍縮小（限定目錄、限定 build target），再 grep；或改用專門的 code search 基礎設施。無範圍的 grep 在大 repo 是雜訊產生器。

## 進階：再往深一層

- **code search 基礎設施 vs grep**：Google 的 Kythe、Chromium 的 Code Search、Sourcegraph、LLVM 的 clangd index——這些對「跨百萬行找定義/引用」做了 grep 做不到的事：語意級、跨 repo、秒級。當你的專案大到 grep 淹死，投資學會團隊的 code search 工具，比硬 grep 值十倍。呼應 Ch 12–14 但尺度質變。
- **「相關性梯度」而非二元**：實務上 code 不是「相關/無關」二分，而是一條梯度：核心命中（必讀）→ 直接呼叫（按需）→ 間接相依（掃一眼）→ 完全無關（無視）。訓練自己快速給每個檔案標梯度，是主動無視的精細版。
- **反查呼叫者是任務驅動法的安全帶**：任務驅動法讓你只讀一小塊，但你改它會影響所有呼叫者。動手前務必 `cscope -L -3`（誰呼叫我）把呼叫者清單拉出來（Ch 9、14），評估局部改動的全局漣漪。這是「局部理解」與「全局責任」之間的橋。
- **build target 當文件讀**：`BUILD.gn`/`BUILD.bazel`/`CMakeLists.txt` 的 target 定義本身就是架構文件——它宣告了「這個 component 由哪些檔組成、依賴哪些其他 component」。在超大 monorepo 裡，讀 build 檔比讀 source 更快得到「模組邊界在哪」，是子系統隔離的地圖來源（Ch 21）。

## 動手練習

1. **重現收斂**：在 redis 上，從 `cloc` 的 14 萬行開始，用一個 grep + `awk '/^void setrangeCommand/,/^}/'` 收斂到 59 行。記錄你總共讀了幾行、跳過了幾行，算出「主動無視率」。
2. **換一個任務、換一個子系統**：任務改成「redis 收到一個 GET 命令，從網路 buffer 到回覆，data flow 走哪」。用命令表定位 `getCommand`，只讀它 + 它直接呼叫的函式，全程主動無視叢集/複製。體會同一套刀換子系統。
3. **反查安全帶**：對 `checkStringLength` 跑 `cscope -d -L -3 checkStringLength`（誰呼叫它）。你會發現 SETRANGE 不是唯一呼叫者（還有 APPEND 等）。想像你要改 `checkStringLength`，列出所有會被你影響的命令——這就是局部改動的全局漣漪。
4. **克隆更大的來練**（選）：`git clone --depth 1 https://github.com/postgres/postgres` 或 LLVM 的一部分，`cloc` 看規模，然後給自己一個具體任務（如「PostgreSQL 怎麼 parse 一個 SELECT」），用本章六把刀收斂。感受尺度放大時每一刀砍得更狠。
5. **練主動無視**：打開 redis 的 `src/module.c`（1.4 萬行）。給自己 30 秒，只憑函式名/註解判斷「它跟『字串命令的記憶體安全』有沒有關」，答案是「幾乎無關」。體會「一眼判無關並闔上」的心安理得。

## 本章重點整理

- 百萬行專案上，「讀懂整個專案」是做不到且有害的目標。目標是**完成具體任務**，理解是副產品，只需到「夠完成任務」。
- 分而治之六把刀：任務裁剪（最鋒利）、子系統隔離、介面優先、按需深潛、build target 切範圍、主動無視。
- 沒有具體任務就進大型 codebase = 城市裡沒目的地亂走，保證迷路。先逼出任務再進場。
- 主動無視是一等公民技能，不是偷懶。百萬行專案裡 95% 動作是「判斷無關並跳過」。
- 讀介面（做什麼）比讀實作（怎麼做）省十倍力；深潛是昂貴操作，只在任務逼你的那個點動用。
- 任務驅動法的盲點是「局部理解漏掉全局後果」——動手前用 cscope 反查呼叫者評估漣漪（Ch 9、14），並靠 code review 補（Ch 33）。

## 自我檢核

- [ ] 有人叫你「熟悉一下這個百萬行專案」，你能不能講出為什麼要先問「熟悉來做什麼具體任務」？
- [ ] 你能說出「介面優先」和「按需深潛」怎麼配合，以及為什麼深潛要當昂貴操作看待嗎？
- [ ] 面試官問「Chromium 四千萬行你怎麼下手」，你能不能講出六把刀而不是「我從 main 開始讀」？
- [ ] 你認同「主動無視是技能不是偷懶」嗎？能舉出你在 redis 上主動無視了多少行的具體數字嗎？
- [ ] 任務驅動法讓你只讀一小塊，它的盲點是什麼？你用什麼補（提示：反查呼叫者）？

## 延伸閱讀

- **[Chromium: "Getting Around the Chromium Source Code" + Code Search](https://chromium.googlesource.com/chromium/src/+/main/docs/README.md)（官方 docs 目錄）。**
  - **讀哪裡**：docs 目錄的入門導覽，以及試用 [source.chromium.org](https://source.chromium.org) 的 Code Search。
  - **學到什麼**：一個真正四千萬行等級專案，官方**怎麼教新人下手**——你會發現他們也不叫你讀懂全部，而是給你導航工具和「按 component 進入」的指引。本章主張的真實世界背書。
  - **關聯**：印證「巨型專案靠 code search 基礎設施而非 grep」。

- **[Kythe — 跨 repo 語意 code 索引](https://kythe.io/)（官方文件）。**
  - **讀哪裡**：概觀 "What is Kythe" 與它解決的問題。
  - **學到什麼**：當專案大到 grep/ctags 淹死時，工業界用什麼——語意級、跨語言、跨 repo 的索引。理解它為什麼存在，你就懂本章「尺度質變」的具體含義。
  - **關聯**：進階「code search 基礎設施 vs grep」的技術底。

- **《The Programmer's Brain》— Felienne Hermans（Manning, 2021），working memory 與 cognitive load 章節。**
  - **讀哪裡**：講 working memory 容量（4–7 chunk）與認知負荷的那幾節。
  - **學到什麼**：本章「一次只碰一個子系統」「主動無視」的認知科學根據——你的工作記憶裝不下百萬行，硬塞只會 overload，所以分而治之不是偷懶而是生理必需。
  - **關聯**：支撐子系統隔離與主動無視的底層原理。

收斂到關鍵幾百行之後，讀法會依「你的目的」分岔。下一章換上攻擊者的眼睛：不是要理解 code，而是要**找它的洞**——source→sink、trust boundary、危險函式。我們把剛剛收斂到的 `checkStringLength` 當起點，看它守的是什麼洞。

→ [Ch 32 找漏洞式讀碼](./32-vulnerability-hunting-reading.md)
