# Ch 1 — 讀碼即 pattern 辨識：chunking 的科學

> **目標**：講清楚一件對這門課生死攸關的事——讀碼變快的機制，不是手上多幾把工具，是腦中多幾個 pattern。用認知科學（chunking、working memory 的容量限制、beacon）把「為什麼專家一眼就懂、新手逐 token 啃」拆到骨頭。你會看清新手與專家的差距不在智力、不在努力，而在**長期記憶裡那個 pattern 庫的大小**——而這門課存在的唯一理由，就是系統化地把這個庫養大。

## 為什麼需要這個？

你可能會問：`reading_code` 已經給了一整套 SOP（偵察、data flow、假設驅動、收斂到 200 行），這門課還要幹嘛？多學十個工具嗎？

不是。工具會遇到報酬遞減。你已經有 `rg`、有 LSP、有 gdb——再學第十一個工具，讀碼速度不會再翻倍。真正還沒被系統開發的，是另一件事：**你一眼能認出多少東西**。

觀察一個資深工程師讀陌生 C 專案，你會發現他大部分 code 根本沒逐字讀。他掃過一段，說「這是個 event loop」，翻過；掃過另一段，說「這是 arena allocator」，翻過；停在某三行前面盯了十秒，說「這裡有個 off-by-one 的味道」。他不是讀得比你用力，是他**看形狀就認出 pattern**，把整段打包成一個他早就熟到爛的概念，工作記憶幾乎沒有負擔。

`reading_code` Ch 3（程式設計師怎麼理解程式）已經把這個機制的名字給你了：**chunking**。那一章講的是理論骨架；這一章要做的是把它變成這門課的訓練綱領——講清楚 pattern 辨識為什麼是讀碼速度的真正瓶頸，以及怎麼刻意去養那個庫。因為接下來六個 Part、二十幾章的每一次「攻堅一個 codebase → 萃取 pattern」，本質上都是在往你的 chunk 庫裡塞新 pattern。你得先信這件事有用，才會認真做。

## 先建立直覺：三個記憶體，讀 code 時同時在跑

先把 `reading_code` Ch 3 建的心智模型（mental model）搬過來壓縮成一張圖，這是後面所有討論的地基：

```
 ┌───────────────────────────────────────────────────────────┐
 │ 長期記憶 LTM (long-term memory)                             │
 │  容量近乎無限、持久。存你會的一切：語法、演算法、           │
 │  你讀過的每一個 pattern（← 這門課要灌爆的就是這一層）       │
 └───────────────────────────────────────────────────────────┘
              ▲ 提取(retrieval)        │ 儲存(把新 pattern 練成長期)
              │                        ▼
 ┌───────────────────────────────────────────────────────────┐
 │ 工作記憶 WM (working memory)  ← 讀碼的瓶頸就在這            │
 │  「處理器」。把眼前看到的字面 + 從 LTM 提取的 pattern       │
 │  拿來組合推理。容量約 4 個 chunk。爆了 = 「剛追到哪忘了」   │
 └───────────────────────────────────────────────────────────┘
```

一句話：**讀碼卡住，幾乎都是工作記憶過載，不是你不夠聰明。** 而工作記憶要不要過載，取決於你能不能把眼前一大段 code 從 LTM 提取現成的 pattern「打包」掉。打包得動，一段 code 只佔工作記憶 1 格；打包不動，同一段 code 佔掉你全部 4 格還爆出來。

這門課從頭到尾都在對抗這一件事：把「打包不動」的東西，透過反覆真讀，變成日後「打包得動」。

## chunk 是什麼：一個對你有意義的單位

先把 chunk 這個詞釘死，不然後面全是空話。一個 chunk **不是**一個字元、一個 token、一行——它是**一個對你有意義的單位**。

`reading_code` Ch 3 用過的例子值得再看一次。這串：

```
C H U N K I N G
```

對不識英文的人是 8 個 chunk（8 個要分別記的符號）。對你是 **1 個** chunk（"CHUNKING" 一個字）。同一份輸入，chunk 數差 8 倍——差別不在輸入，在**你 LTM 裡有沒有「CHUNKING 這個字」這個現成單位**。

搬到 code。看這一行（你在 Part 1 讀 Lua 時會真的遇到這種形狀）：

```c
for (p = head; p; p = p->next) { ... }
```

一個沒讀過幾個 C 專案的新手，眼睛得分別追蹤：`p` 是什麼、`head` 是什麼、`p;` 這個條件為什麼能當布林、`p = p->next` 在幹嘛——四五個要同時掛在工作記憶裡的元素，四行就爆。

一個讀過幾百個 C 專案的人，這一行是 **1 個 chunk**：「linked-list 走訪」。他根本沒逐字讀 `p; ` 那個條件——他看到 `for (X = head; X; X = X->next)` 這個**形狀**，直接從 LTM 提取「單向鏈結串列走到底」這個概念，佔工作記憶 1 格，眼睛滑過去了。他甚至已經知道迴圈結束時 `p == NULL`，不用推。

再看一個。這門課 Part 1（Lua）、Part 2（SQLite）、Part 5（CPython）會反覆遇到的形狀：

```c
while (1) { ev = wait_for_event(); dispatch(ev); }
```

新手：一個無窮迴圈，裡面呼叫兩個函式，我得去看 `wait_for_event` 和 `dispatch` 各是什麼……
專家：**1 個 chunk**——「event loop」。他心裡立刻浮出一整套預期：這是程式的心臟、`wait_for_event` 大概會 block、`dispatch` 會依 event 型別分派到不同 handler、這附近應該有一張 event → handler 的表。這些預期他一個字都還沒讀就有了，因為「event loop」這個 chunk 在他 LTM 裡不是一行 code，是一整包**帶著預設結構與預期的知識**。

**這就是專家讀得快的全部秘密。** 不是眼睛快、不是記性好、不是 IQ 高——是同一段 code，他從 LTM 提取一個 chunk 就打包掉，你得逐 token 現場拼。

## 底層機制：working memory 的容量限制，逼出 chunking 的價值

為什麼 chunking 這麼關鍵？因為工作記憶小得可憐，而 chunking 是唯一能繞過這個限制的辦法。

工作記憶容量，最有名的數字是 Miller 1956 的〈The Magical Number Seven, Plus or Minus Two〉——約 7±2 個項目。但要誠實：Miller 那個「7」帶點修辭，他自己用了 "magical" 這個半開玩笑的詞。更近的 **Cowan 2001〈The Magical Number 4 in Short-Term Memory〉** 主張，在排除複誦（rehearsal）與 chunking 幫忙的純粹狀態下，容量其實只有**約 4 個 chunk**（也有研究給 3–5 的區間）。學界對確切數字仍有爭論，但對讀碼，採保守的「約 4」最有用——它逼你認清工作記憶有多小。Hermans 在《The Programmer's Brain》裡也採「同時處理 2–6 個元素就會吃力」這種保守估計。

關鍵在：**這個「4」數的是 chunk，不是 token。**

```
 工作記憶 = 4 個插槽。差別全在每個插槽裝多大一包。

 新手讀 for(p=head; p; p=p->next) { sum += p->val; }
 ┌────┬────┬────┬────┐
 │ p? │head│ p; │p=  │ ← 4 格塞滿還沒讀到迴圈體，爆
 └────┴────┴────┴────┘   sum+=p->val 進不來

 專家讀同一行
 ┌──────────────┬────┬────┬────┐
 │ 鏈結串列走訪  │    │    │    │ ← 整個迴圈打包成 1 格
 └──────────────┴────┴────┴────┘   還剩 3 格想別的
```

同樣 4 格工作記憶、同樣一段 code，新手爆掉、專家游刃有餘。**容量沒差，chunk 大小差 10 倍。** 這也解釋了 `reading_code` Ch 1 那個現象：你追一段 code 追到需要理解某個 flag，跳去看定義，看懂回來，「剛才追到哪」忘了——那不是你記性差，是工作記憶被塞爆的正常生理現象。專家不常遇到，因為他把沿路的東西都 chunk 掉了，工作記憶一直有空位裝「我追到哪」。

### 西洋棋大師的證據：差距在 LTM 不在 WM

這不是空口說。經典證據來自 De Groot 與 Chase & Simon 對西洋棋大師的研究（`reading_code` Ch 3 引過，這裡要它推出這門課的訓練哲學）：

- 給大師看**真實對局**的棋盤幾秒，他能幾乎完整重建。
- 給大師看**隨機亂擺**的棋盤幾秒，他重建能力**跟新手一樣爛**。

結論是致命的：**大師的記憶力（工作記憶）沒有比較好。** 他快，是因為真實棋局是熟悉 pattern（開局陣型、常見結構）的組合，他把一整片棋子打包成少數幾個 chunk 存進 LTM，用少數幾格工作記憶就記住。亂擺的棋盤沒有 pattern、無從打包，他只能一顆一顆記，於是被工作記憶那 4 格限制打回原形，跟新手平手。

**讀 code 一模一樣。** 給資深工程師看設計良好的 Lua source，他打包得飛快；給他看一坨義大利麵爛 code（`reading_code` Ch 30），沒有可辨識的 pattern，他也得退回逐行啃，慢得跟新手差不多。這反過來印證：**讀碼的速度差距，來自 LTM 的 chunk 庫大小，不是天賦。** 而 chunk 庫是**可以練的**——這是後天技能，不是命。

這一句就是這門課的地基。如果差距是天賦，這門課沒意義；因為差距是 chunk 庫、而 chunk 庫可練，這門課才成立。

## beacon：code 裡觸發你提取正確 chunk 的信號

chunk 是你腦中打包好的知識。**beacon（信標）** 是 code 裡那個「一眼觸發你提取正確 chunk」的線索。Brooks（1983）提出這個概念：beacon 是一眼就暗示某段 code 在幹嘛的刻板標記，讓你快速形成或驗證假設。

beacon 分三類，讀陌生 code 時你要學會主動掃它們：

**1. 命名 beacon（語意）。** 好命名直接把語意洩漏給你。你在 Part 4 讀 git 會遇到 `lookup_object`、`parse_object`、`write_object`——這三個名字是強 beacon：`lookup` 說是查詢、`parse` 說是解析、`write` 說是寫入，`object` 說操作對象是 git object。你根本沒讀函式 body，就已經對它們的職責形成準確假設。這正是 `reading_code` Ch 1「命名恢復語意」的認知學解釋——**命名之所以是線索，因為它是 beacon，觸發你 LTM 裡的 chunk。**

**2. 結構/慣用 idiom beacon（語法形狀）。** 前面那兩個例子——`for (p = head; p; p = p->next)` 的形狀、`while(1){ev=wait();dispatch(ev);}` 的形狀——本身就是 beacon。你看到形狀就認出 pattern，不需要命名幫忙。C 裡一堆這種：`if (!ptr) return NULL;`（早退 guard）、`while ((n = read(fd, buf, len)) > 0)`（讀到 EOF 的迴圈）、`obj->refcount++`（引用計數，你在 Part 5 CPython 會讀爛）。這些刻板形狀是純結構 beacon。

**3. 慣例 beacon（專案/領域約定）。** 每個成熟 codebase 有自己的命名與結構約定，熟了就變 beacon。nginx（Part 3）裡看到 `ngx_` 前綴 + 回傳 `ngx_int_t` 的函式，你會預期它遵循 nginx 的錯誤碼約定（`NGX_OK`/`NGX_ERROR`/`NGX_AGAIN`）；SQLite（Part 2）裡看到 `sqlite3` 前綴 + 回傳 `int`，你會預期它回傳 `SQLITE_OK` 之類的結果碼。這類 beacon 是你讀熟一個 codebase 才長出來的，也是為什麼「讀過的 codebase 隔月再讀收穫巨大」。

beacon 的實務意義：**讀陌生 code 先掃 beacon**——先掃函式名、關鍵字串、明顯的結構形狀，用它們快速搭出「這裡大概在幹嘛」的假設骨架，再挑要緊的地方驗證。這是 `reading_code` Ch 4（掃讀）與 Ch 10（假設驅動）的認知基礎。反過來也成立：**當 beacon 壞掉（爛命名、非慣例結構），你被迫退回逐 token 的新手模式**，這是爛 code 讀起來慢十倍的真正機制，不是「風格問題」。

### 一個 beacon 觸發 chunk 的實況演練

抽象講完，走一遍真實過程。假設你在 Part 3 打開 nginx 一個沒看過的檔案，掃到這段形狀（示意，非逐字節錄）：

```c
for (cl = in; cl; cl = cl->next) {
    ...
    b = cl->buf;
    ...
}
```

一個沒讀過 nginx 的人：`cl` 是什麼、`in` 是什麼、`cl->buf` 又是什麼——三個生詞掛在工作記憶，得跳去查型別定義。

一個讀熟過的人，這裡有**兩層 beacon 疊在一起**觸發**兩個 chunk**：

1. **結構 beacon**：`for (X = in; X; X = X->next)` 這個形狀 → chunk「走訪一條 linked list」（跟開頭那個 `p = head` 是同一個 chunk 的變體）。
2. **命名 beacon**：`cl`（chain link）+ `cl->buf` + 迴圈輸入叫 `in` → chunk「這是 nginx 的 buffer chain（`ngx_chain_t`），在依序處理一串緩衝區」。

兩個 chunk 一提取，他立刻知道：這是在對一條輸出緩衝鏈做逐塊處理，`cl->next` 走到 `NULL` 結束，每塊的實際資料在 `cl->buf`。他**一行 body 都還沒細讀**就有了這整套預期，工作記憶只佔 2 格。這就是 beacon → chunk → 預期的完整反射，也是你在 Part 3 讀 nginx 之後會長出來的 chunk（buffer chain 是 nginx 最核心的 idiom 之一，Ch 15 專門讀它）。

看清楚這個例子的意義：**同樣兩層 beacon，對有 chunk 的人是「秒懂 + 一整套預期」，對沒 chunk 的人是「三個生詞要查」。** 這門課 Part 3 做的事，就是讓「nginx buffer chain」這個 chunk 進你的 LTM，讓你下次看到這個形狀也能秒觸發。

## 這門課到底在系統化地做什麼

把上面串起來，這門課的設計就清楚了。既然：

- 讀碼速度 = 你能把眼前 code 打包成幾個 chunk；
- 打包能力 = LTM 裡 chunk 庫的大小；
- chunk 庫可以靠**反覆真讀真經典的 code** 養大；

那麼要變快，唯一的長期途徑就是**刻意擴充 chunk 庫**，而擴充的最佳材料是**設計良好、被無數人打磨過的傳奇 codebase**——因為它們的 pattern 是「乾淨的樣本」，你在這裡認出的「register-based VM 的 dispatch loop」「arena/pool allocator」「reactor event loop」「content-addressed store」，是可遷移的 chunk：日後你在任何專案遇到同類東西，都能一眼打包。

```
 這門課六個 Part 的訓練循環（每個都在往 LTM 塞 chunk）

   攻堅一個傳奇 codebase          萃取可遷移 pattern → 進 LTM
   ┌──────────────────┐          ┌────────────────────────┐
   │ Lua VM dispatch   │  ───────▶│ "register VM 的 dispatch"│
   │ SQLite pager      │  ───────▶│ "分頁快取 + WAL"         │
   │ nginx event loop  │  ───────▶│ "reactor + object pool"  │
   │ git object store  │  ───────▶│ "content-addressed DAG"  │
   │ CPython refcount  │  ───────▶│ "引用計數 + cyclic GC"   │
   └──────────────────┘          └────────────────────────┘
                                          │
                                          ▼
                        第七個陌生 codebase 你一眼認出這些形狀
                        （Ch 27 三個 VM 橫向對照就是 pattern 遷移的高光）
```

Ch 27（三個 VM 橫向對照）之所以是全課高光，正是因為到那時你已經讀過 Lua、SQLite、CPython 三個 bytecode dispatch loop——三個 chunk 在腦中疊起來，你會**一眼認出**這是同一個 pattern 的三種變體，這就是 pattern 遷移發生的瞬間。Ch 30（你的 pattern 字典）則是把六個 codebase 萃取的 idiom 收斂成一張表，那張表就是你這門課練出來的 chunk 庫的外化清單。

## 怎麼刻意累積 pattern 庫（可操作）

理論不是拿來背的。上面每個機制都推出具體動作，這門課後面每一章都會用到：

**1. 讀完一段就問「這是什麼 pattern」，替它命名。** 認知科學上，替一段 code 取一個 pattern 名字，就是在 LTM 裡建一個新 chunk 的索引。讀完 Lua 的 dispatch loop，別只想「我看懂了」，要逼自己講出「這是 computed-goto 加速的 opcode dispatch」。講不出名字 = chunk 沒成形，下次還是打包不動。這正是每個 Part 的「萃取章」在幹的事，也是為什麼 README 要你**先自己寫**再看萃取章。

**2. 掃 beacon 先於逐行讀。** 拿到陌生檔案，先只掃函式名、關鍵字串、結構形狀，用它們搭假設骨架，別一開始就逐行。這訓練你「用 beacon 觸發 chunk」的反射，而不是退回新手的逐 token 模式。

**3. 刻意在關鍵處退出自動 chunking。** chunk 是雙刃劍：太熟的 pattern 你會自動打包而不細看，於是漏掉「這次 pattern 裡藏了個 bug 或變異」。找漏洞、code review、讀防禦式 C（Part 2 的 SQLite）時，要在關鍵處**刻意切回慢速逐行**，別被自己的 chunk 騙過去（`reading_code` Ch 33 講這個陷阱）。

**4. 間隔重複，讓 chunk 固化。** 今天讀懂一個 pattern 不代表它進了 LTM——沒有間隔複習會流失（spacing effect）。這門課用「pattern 卡片」（下一章詳談）當外化材料，讓你日後能重看、再遇到同類 pattern 時複習，把它從「這次懂了」變成「永久認得」。

**5. 誠實評估先備知識，選對策略。** 有這領域的 chunk 就 top-down 猛跳、假設驅動；沒有就 bottom-up 老實逐行建 chunk，建完再跳回 top-down（`reading_code` Ch 3 的 Letovsky 整合模型）。最常見的錯是沒背景硬 top-down（假設全錯讀歪），或有背景卻苦哈哈 bottom-up（逐行讀你其實一眼能打包的東西）。

### chunk 庫的成長不是線性，是複利

再強調一次為什麼「多讀」值得投資：chunk 庫的回報是**複利的**，不是線性的。原因是 pattern 會互相組合。你 LTM 裡有了「linked list 走訪」「early-return guard」「函式指標 hook」三個基礎 chunk 之後，讀 Lua 的 `dictAddRaw` 那種「找位置→找不到早退→呼叫可選 hook→插入」的函式，你不是分別打包三個 chunk，而是把整個函式打包成**一個更大的 chunk**——「帶 hook 的插入操作」。基礎 chunk 越多，你能組出的高階 chunk 越多，能打包的單位越大，讀得越快，累積新 chunk 也越快。

這解釋了一個殘酷但公平的現象：**讀碼能力是強者越強**。已經讀過五十個 C 專案的人，讀第五十一個時，大半形狀他都能打包，省下的工作記憶全拿去攻真正新的那一小塊——於是他從第五十一個專案裡萃取新 chunk 的效率也最高。而卡在起步的人，每個形狀都是生的，工作記憶天天爆，連萃取新 chunk 的餘力都沒有。這門課存在的意義，就是給你一個**加速起跑**的密集訓練場：六個經典 codebase 濃縮了大量高品質、可遷移的 pattern，讓你在最短時間衝過那個「chunk 庫小到讀什麼都累」的起步門檻。

## 對比與取捨

| 維度 | 新手讀碼 | 專家讀碼 |
|---|---|---|
| 打包單位 | token / 行（現場拼） | pattern（從 LTM 提取現成 chunk） |
| 同一段 code 佔工作記憶 | 4 格塞滿還爆 | 1–2 格，剩下想別的 |
| 遇到熟悉形狀 | 逐字讀 `for (p=head; p; ...)` | 一眼「鏈結串列走訪」，滑過 |
| 對 beacon 的利用 | 常忽略，直接啃 body | 先掃 beacon 搭假設，再挑要緊處驗證 |
| 差距根源 | —— | **LTM 的 chunk 庫大小**（可練，非天賦） |
| 讀爛 code（無 pattern）時 | 慢 | 也慢（退回逐行，證明差距在 chunk 庫） |
| 變快的途徑 | 不會，只是更用力 | 反覆真讀經典 code，累積 chunk（複利） |

一句話取捨：**讀碼速度不是努力或智力的函數，是你 LTM 裡 pattern 庫大小的函數；而 pattern 庫只能靠反覆真讀經典 code 養大——這門課就是那座健身房。**

## 踩雷集錦

1. **錯誤直覺**：「我讀得慢是因為工具不夠好，再學幾個工具就會快。」
   **正確認識**：工具（`rg`、LSP、gdb）解決的是「怎麼找到 code、怎麼跳轉、怎麼動態驗證」，`reading_code` Part 3 已經教滿。工具幫你把 code 送到眼前，但**打包它靠的是 chunk 庫，不是工具**。同一段 code，工具再好，你 LTM 沒有對應 pattern 就是得逐行啃。速度的下一個台階在 chunk 庫，不在第十一把工具。

2. **錯誤直覺**：「專家讀得快是因為他們聰明/記性好，我天生就慢。」
   **正確認識**：Chase & Simon 的西洋棋研究直接打臉——大師記亂擺棋盤跟新手一樣爛，工作記憶容量沒有比較大。他快是因為 LTM 的 chunk 庫大，而 chunk 庫是後天累積的。你慢不是天生，是 pattern 庫還小，而這**可以練**。

3. **錯誤直覺**：「我逐行從頭讀到尾最踏實，總能讀懂。」
   **正確認識**：逐行被動掃描是新手模式——你在做大量現場 chunking，工作記憶反覆爆掉，讀到後面忘了前面。有背景時該 top-down 用 beacon 觸發 chunk、大膽跳過；只有真的沒背景才 bottom-up 逐行建。硬逐行讀你其實一眼能打包的東西，是把自己降級成新手。

4. **錯誤直覺**：「這段 code 我一眼就懂了，chunk 打包得很順，一定沒問題。」
   **正確認識**：chunk 是雙刃劍。太熟的 pattern 會讓你**自動打包而不細看**，眼睛滑過去，漏掉「這次 pattern 裡藏了變異或 bug」。這正是找漏洞（作者也有這個盲點）與 code review 的機會與陷阱。關鍵處要刻意退出自動 chunking，切回慢速逐行。

5. **錯誤直覺**：「讀懂了就是讀懂了，不用特別做什麼它自然會留在腦裡。」
   **正確認識**：沒有間隔複習，今天懂的 chunk 會流失（spacing effect）。「這次看懂」和「永久認得」是兩回事。要靠外化（pattern 卡片、筆記、圖）留下材料，日後重看、再遇同類時複習，才把 chunk 固化進 LTM。

## 進階：再往深一層

- **plan knowledge（計畫知識）是 chunk 的一種特化。** Soloway & Ehrlich 的研究指出，程式設計有一堆刻板的「解法模板」——用一個 flag 追蹤是否找到、用哨兵值標記結尾、先蒐集再排序。這些 plan 存在 LTM，讀 code 時你不斷拿眼前 code 去比對已知 plan：「喔這是 accumulator plan」。認出 plan = 一次高效 chunking。這解釋了「讀熟悉領域的 code 快得多」——不是語法簡單，是你有那個領域的 plan 庫。這門課每個 Part 就是在替你補一個領域的 plan 庫（VM / 儲存引擎 / event-driven server / 資料模型 / runtime）。

- **認知負荷的三型，指導你怎麼降負荷。** Sweller 的 cognitive load theory 把負荷分成 intrinsic（問題本身固有難度）、extraneous（呈現方式造成的多餘負荷，如爛命名、糟排版）、germane（用於建立 schema 的有效負荷）。讀碼策略的一大部分是**砍掉 extraneous**（外化、重命名、畫圖）好把工作記憶留給 intrinsic 與 germane。這是下一章訓練協定裡「外化」步驟的理論依據。

- **chunk 的固化需要 desirable difficulty。** 認知科學有個反直覺的發現：學習時適度的困難（自己回想而非被動看答案）反而讓記憶更牢。這直接推出下一章訓練協定的核心設計——**先自己限時攻堅撞牆，再看教材**。被動讀教材的解答，chunk 進不了 LTM；自己撞牆撞出來的，才會留下。下一章把這個原理落地成具體 SOP。

## 本章重點整理

- 讀碼變快的機制是 **pattern 辨識（chunking）**，不是多幾把工具。工具把 code 送到眼前，打包它靠 chunk 庫。
- **chunk = 對你有意義的單位**。專家把 `for(p=head;p;p=p->next)` 一眼打包成「鏈結串列走訪」、把 `while(1){ev=wait();dispatch(ev);}` 打包成「event loop」，各佔工作記憶 1 格；新手逐 token 拼，4 格塞爆。
- **working memory 只有約 4 個 chunk**（Cowan 2001，非 Miller 的 7）。這個「4」數的是 chunk 不是 token，所以 chunking 是繞過容量限制的唯一辦法。
- **beacon** 是 code 裡觸發你提取正確 chunk 的信號：命名、結構/idiom 形狀、專案慣例。讀陌生 code 先掃 beacon 搭假設，再挑要緊處驗證。
- 新手 vs 專家的差距**不在智力或努力，在 LTM 裡 pattern 庫的大小**（西洋棋研究：大師記亂擺棋盤跟新手一樣爛）。chunk 庫可練、複利、後天。
- 這門課就是系統化擴充 chunk 庫：攻堅六個傳奇 codebase → 萃取可遷移 pattern → 進 LTM。Ch 27 三個 VM 對照是 pattern 遷移的高光，Ch 30 是 chunk 庫的外化清單。

## 自我檢核

- [ ] 不看筆記，能不能用「chunk 大小」解釋為什麼同一段 code、同樣 4 格工作記憶，新手爆掉而專家游刃有餘？
- [ ] 西洋棋大師記「亂擺棋盤」跟新手一樣爛——這個實驗證明了專家的優勢**不在**哪裡、**在**哪裡？對「你想變快該做什麼」有什麼結論？
- [ ] 舉三個你會一眼打包的 C idiom 形狀（beacon），各說出你從它提取的 chunk 是什麼、附帶哪些預期。
- [ ] 為什麼「再學一個工具」不會讓你讀碼更快，但「多讀十個經典 codebase」會？兩者分別作用在哪一層？
- [ ] chunk 是雙刃劍——「自動打包而不細看」在什麼場景（找漏洞 / review）會害你？該怎麼刻意對抗？

## 延伸閱讀

- **Felienne Hermans,《The Programmer's Brain》(Manning, 2021), Part 1（Ch 1–4）**
  - **讀哪裡**：Ch 2 chunking、Ch 3 STM/LTM 與 working memory、Ch 4 beacon 與 confusion 的四型。本章的骨架就來自這裡。
  - **學到什麼**：用大量 code 例子把 chunk / working memory / beacon 講透，還給「如何刻意練 chunk」的具體方法。
  - **和本章關聯**：本課 Part 0 的理論支柱，也是全課「為什麼萃取 pattern 有用」的依據。讀完本章直接接它。

- **Nelson Cowan,〈The Magical Number 4 in Short-Term Memory: A Reconsideration of Mental Storage Capacity〉(Behavioral and Brain Sciences, 2001)**
  - **讀哪裡**：不必啃全文，看 Abstract 與結論，理解「為什麼是 4 不是 7」以及「chunk 才是計數單位」。
  - **學到什麼**：工作記憶容量的當代修正，讓你有底氣不盲信流傳的「7±2」。
  - **和本章關聯**：本章「約 4 個 chunk」數字的出處。

- **Chase & Simon,〈Perception in Chess〉(Cognitive Psychology, 1973)**
  - **讀哪裡**：看它怎麼設計「真實對局 vs 隨機亂擺」的重建實驗，以及結論。
  - **學到什麼**：專家優勢來自 LTM 的 chunk 庫而非工作記憶容量——這門課「差距可練」哲學的第一手實證。
  - **和本章關聯**：本章推出「chunk 庫可練、非天賦」這個全課地基的證據來源。

pattern 辨識為什麼是讀碼速度的真正瓶頸，講清楚了。下一章把「怎麼練這個庫」落地成一套每個 codebase 都跑一遍的訓練協定——限時攻堅、對照深挖、萃取複述，並解釋為什麼「先自己撞牆再看解答」比直接讀答案有效。

→ [Ch 2 訓練協定：限時攻堅 → 萃取 pattern → 費曼複述](./02-the-training-protocol.md)
