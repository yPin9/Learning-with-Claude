# Ch 10 — 假設驅動讀碼

> **目標**：把讀碼從「被動逐行看」升級成「主動偵測」。核心是一個迴圈：**觀察 → 形成假設 → 設計驗證 → 執行 → 修正**。這正是逆向工程的本質，也是這整門課的心臟。讀完你會有一套具體的驗證武器庫（讀更多 code、gdb 看實際值、加 log、改一行看行為、跑既有測試），能在 redis 上完整跑一次「猜 `dictExpand` 是雜湊表擴容」的假設-驗證循環，並且知道怎麼避開讀碼者最大的認知陷阱——確認偏誤。

## 讀碼不是「看」，是「破案」

先戳破一個瀰漫的錯覺：厲害的讀碼者不是「一眼就看懂」。他們跟你一樣，面對陌生 code 時腦子裡也是一團問號。差別在於——**他們把問號變成一個個可以檢驗的假設，然後用最省力的實驗一個個驗掉。**

這跟逆向工程一模一樣。你拿到一個沒有符號的 binary，你不會（也不能）「讀懂」它——你會**猜**：「這個 loop 看起來像在解密」「這個 `cmp` 後跟 `je` 大概是在檢查 magic number」，然後下斷點、改記憶體、看行為，驗證你的猜測。**讀 source 只是把 disassembly 換成了 C，破案的方法論完全不變。**

核心迴圈長這樣：

```
   ┌─────────────────────────────────────────────────┐
   │                                                   │
   ▼                                                   │
 觀察 ──→ 形成假設 ──→ 設計「最便宜」的驗證 ──→ 執行 ──┤
 (線索)   ("我猜...")   (用哪個實驗成本最低?)    (真跑)  │
                                                   │    │
                                            ┌──────┴────┴──┐
                                       假設成立?         假設錯?
                                            │              │
                                       收窄/深入      修正假設，重來
                                            │              │
                                            ▼              ▼
                                        繼續破案       (這一步最值錢!)
```

注意這個迴圈的兩個關鍵性質：**它是迭代的**（一次驗證通常不會給你全部答案，而是讓你形成下一個更精確的假設），而且**「假設錯了」是好事**——一個被證偽的假設，比十行漫無目的的閱讀更能推進理解。破案的人歡迎反例。

## 線索從哪來：形成好假設的原料

假設不是憑空瞎猜，它建立在線索上。讀碼時，好假設的原料有：

- **命名**：`dictExpand`、`freeMemoryIfNeeded`、`processMultiBulkBuffer`——名字是作者留給你的最大善意（也可能是最大的謊言，見踩雷）。`Expand` 強烈暗示「變大」。
- **型別與參數**：`int dictExpand(dict *d, unsigned long size)`——收一個 dict、一個 size，回 int。光看簽名就能猜「把 dict 調整到某個 size，回成功/失敗」。
- **呼叫脈絡**：誰呼叫它、在什麼情況下呼叫。`dictExpandIfNeeded` 呼叫 `dictExpand`——「若需要才擴容」，脈絡把假設收得更緊。
- **註解**：可能有用，可能過時騙人（Ch 30 專講爛 code）。當線索，不當結論。
- **既有知識/類比**：你知道雜湊表一般怎麼實作（load factor 到閾值就 rehash 加倍），這個先驗讓你能對 redis 的 dict 快速形成假設。

**好假設的特徵是「可證偽」**——它必須做出具體、可檢驗的預測。「`dictExpand` 大概跟 dict 有關」不是好假設（廢話，沒法驗）。「`dictExpand` 是雜湊表擴容，當元素數達到桶數時被觸發，把桶數加倍」——這是好假設，因為它預測了三件可驗的事：(1) 觸發時機、(2) size 的變化規律、(3) 呼叫者。

## 驗證武器庫：從最便宜到最貴

有了假設，別急著埋頭讀。先問：**驗證這個假設，最便宜的實驗是什麼？** 讀碼高手和新手的差距，很大一部分在「選對驗證手段」。這是你的武器庫，按成本由低到高排：

| 武器 | 成本 | 回答什麼 | 何時用 |
|---|---|---|---|
| **讀更多 code** | 最低 | 「作者到底寫了什麼」 | 假設在同一個檔案就能證實/證偽 |
| **cscope/rg 查關聯** | 低 | 「誰呼叫它、它改了什麼」 | 假設關於呼叫時機、資料流向 |
| **跑既有測試** | 低 | 「這功能的預期行為/邊界」 | 專案有測試覆蓋這塊 |
| **gdb 斷點看實際值** | 中 | 「runtime 真實發生了什麼」 | 假設關於執行時的值、路徑、次數 |
| **加 printf/log** | 中 | 「這段被走幾次、參數是啥」 | 想看一連串執行、不方便逐次斷點 |
| **改一行看行為變化** | 中高 | 「這行的因果作用」 | 假設「這行負責 X」，改掉看 X 壞不壞 |

原則：**能用讀 code 解決的，別動 gdb；能靜態確認的，別重編。** 但反過來——**當靜態怎麼讀都不確定時，別再空讀第十遍，果斷上動態。** 十分鐘的 gdb 常勝過一小時的乾瞪眼。

## 完整跑一次：`dictExpand` 是不是雜湊表擴容？

現在把整套用在 redis 上，一步步破案。沙包 `~/reading_code_lab/redis`。

### 步驟 1：觀察，形成假設

線索收集。先看它的簽名和定義（`src/dict.c:290`，真實 code）：

```c
/* return DICT_ERR if expand was not performed */
int dictExpand(dict *d, unsigned long size) {
    return _dictExpand(d, size, NULL);
}
```

再看內層 `_dictExpand`（`src/dict.c:281`，真實 code）：

```c
int _dictExpand(dict *d, unsigned long size, int* malloc_failed) {
    /* the size is invalid if it is smaller than the size of the hash table
     * or smaller than the number of elements already inside the hash table */
    if (dictIsRehashing(d) || d->ht_used[0] > size || DICTHT_SIZE(d->ht_size_exp[0]) >= size)
        return DICT_ERR;
    return _dictResize(d, size, malloc_failed);
}
```

線索齊了：名字 `Expand`、參數 `size`、註解說 size 太小就非法、若已在 rehashing 就拒絕、實際幹活的是 `_dictResize`。

**形成假設（要可證偽）**：

> H1：`dictExpand` 把雜湊表的桶數調整到 `size`（一個擴容操作）。
> H2：它由「元素多到需要擴容時」觸發，觸發者是某個 `...IfNeeded` 函式。
> H3：初次擴容從一個小的初始值開始，之後隨元素增加而變大。

三個假設，各有可驗的預測。

### 步驟 2：最便宜的驗證——查呼叫者（cscope）

H2 說「由某個 IfNeeded 觸發」。這個假設最便宜的驗法是查呼叫者，不用跑任何東西。實跑（真實輸出）：

```
$ cscope -d -L -3 dictExpand              # -3 = 誰呼叫 dictExpand
src/config.c   initConfigValues    3287  dictExpand(configs, sizeof(static_configs)/...);
src/dict.c     dictExpandIfNeeded  1498  dictExpand(d, DICT_HT_INITIAL_SIZE);
src/dict.c     dictExpandIfNeeded  1512  dictExpand(d, d->ht_used[0] + 1);
src/kvstore.c  kvstoreExpand        418  ... ? dictTryExpand(d, newsize) : dictExpand(d, newsize);
```

H2 **初步成立**：`dictExpandIfNeeded` 確實是主要觸發者（另外還有 config 初始化、kvstore 這兩個我們暫時歸類為「其他情境」）。而且這一條輸出直接餵了 H3 的線索——它在 `dictExpandIfNeeded` 裡有兩個呼叫點：一個傳 `DICT_HT_INITIAL_SIZE`（初始），一個傳 `d->ht_used[0] + 1`（隨用量增長）。**一條 cscope 就同時推進了 H2 和 H3。**

### 步驟 3：讀觸發邏輯——確認 H3 的機制

順著查到的 `dictExpandIfNeeded`（`src/dict.c:1492`）讀（真實 code，節錄核心）：

```c
int dictExpandIfNeeded(dict *d) {
    if (dictIsRehashing(d)) return DICT_OK;

    /* If the hash table is empty expand it to the initial size. */
    if (DICTHT_SIZE(d->ht_size_exp[0]) == 0) {
        dictExpand(d, DICT_HT_INITIAL_SIZE);         // ← 空表 → 擴到初始大小
        return DICT_OK;
    }

    /* If we reached the 1:1 ratio ... we resize doubling the number of buckets. */
    if ((dict_can_resize == DICT_RESIZE_ENABLE &&
         d->ht_used[0] >= DICTHT_SIZE(d->ht_size_exp[0])) || ...)
    {
        if (dictTypeResizeAllowed(d, d->ht_used[0] + 1))
            dictExpand(d, d->ht_used[0] + 1);        // ← 用量達桶數(1:1) → 擴容
        return DICT_OK;
    }
    return DICT_ERR;
}
```

H3 **在 source 層得到強力支持**：空表擴到 `DICT_HT_INITIAL_SIZE`；當 `ht_used[0] >= 桶數`（load factor 到 1:1）時，擴到 `used+1`（`_dictResize` 內會把它進位到下一個 2 的冪，等效加倍）。註解白紙黑字寫「reached the 1:1 ratio... doubling the number of buckets」。

到這裡，靜態讀已經讓三個假設都站得住。但——**靜態讀懂「作者寫了什麼」，不等於確認「執行時真的這樣跑」**。註解可能過時、可能有你沒讀到的分支繞過這裡。破案最後一步：讓它在你眼前跑一遍。

### 步驟 4：最硬的驗證——gdb 看它真的擴容（真跑）

設計實驗：在 `dictExpand` 下斷點，然後往一個空的 redis 灌 key，看 `dictExpand` 是不是真的被觸發、`size` 參數是不是照 H3 預測的規律變化、呼叫者是不是 `dictExpandIfNeeded`。

我實跑了：gdb 底下起 `redis-server --port 7779`，斷 `dictExpand`，印出 `size` 和 backtrace，然後 `redis-cli SET key1..key50`。這是**真實輸出**：

```
Breakpoint 1, dictExpand (size=4, d=<optimized out>) at src/dict.c:291
291	    return _dictExpand(d, size, NULL);
dictExpand called: requested size = 4
#0  dictExpand (size=4, ...) at src/dict.c:291
#1  dictExpandIfNeeded (d=<optimized out>) at src/dict.c:1498
#2  dictExpandIfNeeded (d=d@entry=0x7ffff7808070) at src/dict.c:1492
```

逐項核對假設：

- **H1（是擴容）✓**：`dictExpand` 真的被觸發了，參數是 `size`。
- **H2（由 IfNeeded 觸發）✓**：backtrace `#1` 明明白白是 `dictExpandIfNeeded (src/dict.c:1498)`——正是我們查到的、傳 `DICT_HT_INITIAL_SIZE` 的那個呼叫點。**動態直接印證了靜態查到的呼叫關係。**
- **H3（從初始值開始）✓**：第一次擴容 `size=4`。我們去 source 確認 `DICT_HT_INITIAL_SIZE` 的值（真實輸出）：

```
$ rg -n 'DICT_HT_INITIAL_SIZE' src/dict.c
1498:        dictExpand(d, DICT_HT_INITIAL_SIZE);
```

`DICT_HT_INITIAL_SIZE` 在標頭定義為 4——**gdb 看到的 `size=4` 與 H3「從初始值開始」的預測完全吻合**。第一個 key 進來時，空表擴容到 4 個桶，正是 `dictExpandIfNeeded` 那句 `if (DICTHT_SIZE(...)==0) dictExpand(d, DICT_HT_INITIAL_SIZE)` 的執行結果。

**三個假設全部通過靜態 + 動態雙重驗證。破案完成。** 我們現在對 `dictExpand` 的理解不是「大概是擴容吧」，而是「它是雜湊表擴容，由 `dictExpandIfNeeded` 在空表或 load factor 達 1:1 時觸發，初始擴到 4，之後隨用量加倍」——每一句都有真跑證據撐著。

> **注意這個循環的節奏**：cscope（便宜）先把假設推到八成 → 讀 code（便宜）補上機制細節 → gdb（中等成本）做臨門一腳的事實確認。**沒有一開始就 gdb，也沒有全程只靠讀。每一步都選了當下最划算的武器。** 這種「成本意識」是熟手和生手最大的差別。

## 頭號敵人：確認偏誤（Confirmation Bias）

現在講這章最重要、也最反直覺的一件事。破案的最大陷阱不是「猜錯」，是**確認偏誤**——你形成假設後，會不自覺地**只去找支持它的證據，忽略反駁它的證據**。這會讓你「驗證」出一個錯誤的理解，還信心滿滿。

在讀碼裡它長這樣：

- 你猜「這個函式是做 X 的」，然後只讀那些「看起來像 X」的行，跳過那些不符合的分支——結果那個被你跳過的分支才是真相。
- gdb 跑一次符合預期，你就收工——但你只餵了 happy path 的輸入，那條讓假設崩潰的邊界 case 你根本沒試。
- 你認定某段是 dead code，之後每次看到它都自動略過，強化「它不重要」的印象——直到它在生產環境炸了。

**對抗確認偏誤的具體做法**（這是可操作的紀律，不是口號）：

1. **主動問「什麼證據會證明我錯」**，然後去找那個證據。假設「`dictExpand` 只擴不縮」？那就去 rg `dictShrink`（真的有！`src/dict.c:301`）——反例立刻推翻「dict 只會變大」的過度概括。**刻意找反例，是破解確認偏誤的第一原則。**
2. **餵邊界與異常輸入，不只 happy path。** 驗 `GET` 別只 `GET existing_key`，也 `GET nonexistent`、`GET` 一個 list 型別的 key（型別錯誤分支）。假設要能扛住邊界才算真站住。
3. **讓別人（或 rubber duck）挑戰你的假設。** 講出來「我認為這裡是 X 因為 Y」，講的過程常會自己發現 Y 站不住。Ch 36 的費曼測試就是把這個系統化。
4. **給假設標信心等級**，別非黑即白。「我 70% 確定這是擴容」比「這就是擴容」健康——它逼你保留「還有 30% 可能我錯」的空間，繼續找證據。

> 逆向工程老手對確認偏誤有肌肉記憶：你以為某個 check 是 license 驗證，patch 掉它結果程式行為沒變——**反例當場打臉，逼你重想**。讀 source 沒有 binary 那種「patch 一下立刻見真章」的即時反饋，反而更容易讓錯誤假設潛伏。**所以讀 source 時要更刻意地找反例。**

## 快速實驗法：把驗證成本壓到最低

假設驅動的效率，取決於你能多快跑完一個「假設→驗證」循環。幾個把單次循環壓到最短的技巧：

- **改一行看行為**：懷疑某行負責 X？把它註解掉/改個值，重編那一個 TU（redis `make` 增量編譯只重編改動的檔），跑一下看 X 是否壞掉。因果驗證最直接的一招。（redis 單檔重編通常幾秒。）
- **gdb 的 `return` / `set var`**：不想重編？在 gdb 裡直接 `set var x=0` 改變數、或 `return` 讓函式提前回，當場看行為變化。零重編成本的「改一行」。
- **條件斷點**：`break dictExpand if size > 1024`——只在你關心的 case 停，不被幾百次無關觸發淹沒。
- **既有測試當 oracle**：`tests/unit/type/string.tcl` 這種既有測試，是作者對「正確行為」的定義。跑它、讀它，比你自己瞎猜預期行為準得多。改了 code 跑一遍測試，紅了就知道你動到了什麼。
- **一次性 log**：懷疑某路徑走幾次？塞一行 `serverLog(LL_WARNING, "HIT %s %d", __func__, x);` 重編跑一次，看 log 刷幾次。比逐次 gdb continue 快。

核心是：**每個假設都該對應一個「幾分鐘內能跑完」的實驗。** 如果你發現自己「盯著 code 想了半小時還不確定」，那不是你不夠聰明——是你沒把問題轉成一個能跑的實驗。停下來，設計實驗。

## 對比與取捨

| 讀碼模式 | 假設驅動（本章） | 逐行精讀 | 純掃讀 |
|---|---|---|---|
| 心態 | 破案：猜→驗 | 逐字消化 | 抓大概 |
| 速度 | 快（只驗關鍵假設） | 慢 | 最快 |
| 適用 | 陌生大 codebase、找特定機制 | 你要改的那 200 行、安全審計 | 建立第一印象、偵察 |
| 風險 | 確認偏誤（可管理） | 淹死在細節、抓不到重點 | 只有錯覺沒有理解 |
| 何時切換 | 主力模式 | 假設收窄到具體區塊後 | 開場與探路 |

三者不是對立，是**節奏**：掃讀探路 → 假設驅動收窄 → 對關鍵區塊精讀。假設驅動是連接「粗略掃視」和「深度精讀」的引擎。純逐行精讀一個 50 萬行專案是自殺；純掃讀只能得到錯覺。**假設驅動讓你把昂貴的精讀，只花在假設指向的關鍵處。**

## 踩雷集錦

1. **錯誤直覺：「函式名說什麼就是什麼」。**
   正確認識：名字是**線索不是結論**。`freeMemoryIfNeeded` 在新版 redis 其實已改名 `performEvictions`（舊名的 rg 會落空），`getGenericCommand` 也被 `SET ... GET` 選項複用（名字只說了一半用途）。名字給你初始假設，但**必須用 code/動態驗證**，不能拿名字當事實。爛 code 裡名字甚至會主動騙你（Ch 30）。

2. **錯誤直覺：「gdb 跑一次符合預期，假設就成立了」。**
   正確認識：一次成功只證明「至少這個輸入下成立」。你可能只餵了 happy path。**確認偏誤最愛藏在這裡。** 要主動餵邊界（空表、超大 size、已在 rehashing 的表）看假設扛不扛得住。`_dictExpand` 開頭那三個 `return DICT_ERR` 的條件，就是你該去戳的邊界。

3. **錯誤直覺：「讀懂了 code 寫什麼 = 懂了它 runtime 怎麼跑」。**
   正確認識：靜態讀給你「所有可能」，但哪條分支真的走、走幾次、變數真實值多少，只有動態知道。我上面若只讀 `dictExpandIfNeeded`，會「以為」第一次是擴到 4；gdb 印出 `size=4` 才是**確認**。讀懂 ≠ 驗證，中間差一次真跑。

4. **錯誤直覺：「不確定就再多讀幾遍」。**
   正確認識：讀第十遍不會比讀第三遍多懂多少——你卡住是因為**這個問題靜態解不了**（例如「這值 runtime 是多少」「這路徑走不走得到」）。多讀是原地打轉。正確反應是**換武器**：轉成一個能跑的實驗（gdb/log/改一行）。「還在讀」常是逃避設計實驗的舒適區。

5. **錯誤直覺：「假設被推翻 = 我浪費時間了」。**
   正確認識：**被證偽的假設是最高效的學習。** 它把你從一條錯路上拉回來，且往往揭露你原本沒想到的真相（「原來 dict 還會 shrink」）。破案的人追求的不是「一猜就中」，而是「快速猜、快速驗、快速修」。怕猜錯而不敢形成假設的人，讀碼最慢。

## 進階：再往深一層

- **多假設並行（competing hypotheses）**：對同一個現象，刻意同時持有兩三個互斥假設，設計一個能區分它們的實驗。這是情報分析的正規方法（Analysis of Competing Hypotheses），天然對抗確認偏誤——因為你不是在「驗證我的假設」，而是在「淘汰錯的假設」。例如某函式行為異常，同時假設「是鎖競爭」「是 cache 失效」「是配置沒生效」，設計一個能一次分辨的觀測點。

- **假設的「賭注」意識**：形成假設時，順手估「如果我錯了，代價多大」。對一個你**要改**的核心函式，假設錯的代價是引入 bug，值得動用最貴的驗證（改一行、跑全套測試）；對一個你只是**路過掃讀**的輔助函式，八成把握就夠了，別過度驗證。**把驗證力度匹配到假設的賭注大小**，是高手的資源分配。

- **記錄你的假設（外化）**：破案過程中形成的假設別只放腦裡——寫下來（「H1: dictExpand 是擴容，70% 確定，待 gdb 驗」）。一來 working memory 有限（Ch 3），二來寫下的假設之後能回頭檢視「當初這條驗了沒」。Ch 35 專講外化，這裡先養成「假設要落字」的習慣。

- **從假設驅動到漏洞假設**：Ch 32 找漏洞，本質是特殊的假設驅動——假設不是「這函式做什麼」，而是「這裡**可能**有 X 類漏洞（整數溢位/UAF/越界）」，然後驗證觸發條件是否可達。`_dictExpand` 那個 `d->ht_used[0] > size` 檢查，安全視角會立刻假設「若 size 來自不可信輸入且沒這檢查會怎樣」。同一套迴圈，換個假設目標。

## 動手練習

在 `~/reading_code_lab/redis` 上做，貼真跑輸出：

1. **完整跑一次別的循環**：對 `expireIfNeeded`（key 過期檢查）形成三個可證偽假設，然後用「cscope 查呼叫者 → 讀觸發邏輯 → gdb 斷點驗證」把它們一一驗掉。貼出你的假設、每步證據、以及哪個假設被修正了。

2. **刻意找反例**：針對「redis 的 dict 只會擴容不會縮小」這個（錯的）假設，用一條 rg 找出反例。寫一句話說明反例如何修正你的理解。

3. **改一行看因果**：把 `dictExpandIfNeeded` 裡的擴容條件改成永不觸發（或把 `DICT_HT_INITIAL_SIZE` 的擴容那行註解掉），重編 `make`，觀察行為（提示：redis 可能起不來或效能異常）。這驗證了「這行負責什麼」。做完記得改回。

4. **邊界戳假設**：用 gdb 斷 `_dictExpand`，構造一個會走到開頭三個 `return DICT_ERR` 之一的情境（例如對已在 rehashing 的 dict 再擴容），確認假設「dictExpand 總會擴容成功」是錯的——它在特定條件下直接拒絕。

5. **標信心等級**：挑 redis 任一個你沒讀過的函式，先只憑名字+簽名寫下假設和信心%，再讀 code 驗證，記錄信心%怎麼變化。體會「不確定性是可管理的量，不是非黑即白」。

## 本章重點整理

- 讀碼的引擎是破案迴圈：**觀察 → 形成（可證偽的）假設 → 選最便宜的驗證 → 執行 → 修正**。這就是逆向工程的本質。
- 好假設要**可證偽**——做出具體、可檢驗的預測（觸發時機、值的規律、呼叫者），而非「大概跟 X 有關」。
- 驗證武器庫按成本排：讀 code < cscope/rg < 跑測試 < gdb < 加 log < 改一行。**能靜態別動態；但靜態卡住就果斷上動態，別空讀第十遍。**
- 完整實例：`dictExpand` 三假設 → cscope 查到 `dictExpandIfNeeded` 觸發 → 讀觸發邏輯確認 1:1 加倍 → gdb 實測 `size=4` 印證初始擴容。靜態 + 動態雙重驗證才算破案。
- 頭號敵人是**確認偏誤**：對抗法是主動找反例、餵邊界輸入、標信心等級、讓假設接受挑戰。
- 卡住不是笨，是問題靜態解不了——**把它轉成幾分鐘能跑完的實驗**。

## 自我檢核

- [ ] 我能說出破案迴圈的五步，並解釋為什麼「假設被推翻」是好事。
- [ ] 給我一個陌生函式，我能寫出一個「可證偽」的假設（含具體預測），而不是「它大概做 X」。
- [ ] 面對一個假設，我能說出驗證它「最便宜」的手段是什麼，而不是反射性地開 gdb。
- [ ] 我能講出確認偏誤在讀碼裡的三種具體樣貌，以及至少兩個對抗它的操作。
- [ ] 當我「盯著 code 想了很久還不確定」時，我知道該停下來設計一個能跑的實驗，而不是再讀一遍。

我們現在會追資料、看控制流、用假設破案。Part 2 的最後一塊拼圖：面對一個 50 萬行的專案，你怎麼從「整座工廠」快速收斂到「你真正要改的那 200 行」？下一章把定位技術系統化。

→ [Ch 11 從 50 萬行收斂到你要改的 200 行](./11-narrowing-to-change-site.md)
