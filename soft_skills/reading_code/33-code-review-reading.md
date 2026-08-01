# Ch 33 — code review 式讀碼

> **目標**：練一種你天天在做、卻幾乎沒被當成「技能」教過的讀碼模式——**讀 diff，不讀全檔**。學會怎麼把「別人的一個 PR」當成攻堅一個 codebase 的捷徑：從幾十行改動反推整個模組的意圖、副作用、與遺漏。讀完你有一套 review 心智清單，並知道 diff context 不夠時怎麼展開周圍看真相。真跑：拿 redis 一個真實 commit `git show`，示範怎麼「讀」它。

## 為什麼要單獨講這件事？

前面 32 章我們談的都是「拿到一個陌生 codebase，怎麼從零建地圖」。但工程師的日常裡，有一種讀碼場景頻率極高、卻被系統性低估：**你面前不是整個專案，是一坨 diff。**

- 你在 review 同事的 PR。
- 你在 debug，`git log -p` 想找「這行為是哪次改動引入的」。
- 你在追一個 CVE，官方只丟你一個 patch commit。
- 你在學一個新模組，發現最快的入口不是讀那 3000 行的 `networking.c`，而是讀最近 20 個碰過它的 PR。

這些場景的共同點：**閱讀單位是「改動」(change)，不是「檔案」(file)。** 而讀改動的技巧，跟讀全檔完全不同——甚至在某些維度上更難。

先建立一個反直覺的認識：**diff 是這個世界上 context 最貧乏的一種 code。** 它預設你已經懂周圍那幾百行，只給你「變了什麼」。這正是它高效的原因（訊號密度極高），也是它危險的原因（你很容易在不懂上下文的情況下對一個改動點頭）。這一章就是在對抗這個不對稱。

## 先給直覺：diff 是「導演的剪輯版」

把讀全檔想成看一整部電影的原始素材——每個鏡頭都在，但你得自己找重點。讀 diff 則像看導演的剪輯版：**有人已經替你決定了「哪些畫面重要」**，只留下變動的幀。

這個「有人」就是提交者。他用 `+`/`-` 告訴你：「注意這裡，其餘不變。」這是巨大的槓桿——你不必讀懂整個 `server.c`，只要讀懂這 9 行改動。但槓桿的另一端是風險：**剪輯是一種主張，而主張可能是錯的。** 提交者「認為」他只改了這 9 行的行為，但真相可能是這 9 行還悄悄影響了某個沒出現在 diff 裡的呼叫者。你的工作，就是驗證這個剪輯版有沒有騙你。

所以 code review 式讀碼的核心心法是：

> **讀 diff = 讀「作者聲稱的改動」+ 主動補齊「他沒給你看的上下文」，然後判斷兩者是否一致。**

## 底層機制：diff 到底是什麼

要讀得好，先知道你在讀什麼。unified diff 的一個 hunk 長這樣：

```
@@ -174,6 +174,7 @@ static void *createArrayObject(const redisReadTask *task, size_t elements) {
         return NULL;

     if (elements > 0) {
+        if (SIZE_MAX / sizeof(redisReply*) < elements) return NULL;  /* Don't overflow */
         r->element = hi_calloc(elements,sizeof(redisReply*));
         if (r->element == NULL) {
```

拆解每個部件：

- `@@ -174,6 +174,7 @@`：**hunk header**。`-174,6` = 舊檔從第 174 行起、涵蓋 6 行；`+174,7` = 新檔從第 174 行起、涵蓋 7 行。多一行，因為加了一行。
- `@@` 後面那段 `static void *createArrayObject(...)`：**hunk context / section heading**。git 用一個啟發式（往上找最近的「看起來像函式定義的那一行」）標出這個 hunk 在哪個函式裡。**這是免費情報**——它告訴你改動落在哪個符號範圍，很多人 review 時眼睛直接跳過它，虧大了。
- 沒有前綴的行：**context 行**（未改動，只是給你參照）。
- `-` 行：被刪掉的。
- `+` 行：新增的。

關鍵的一個參數：**context 行數，預設 3。** 也就是 diff 只給你改動點上下各 3 行。3 行往往不夠——你看不到這個 `if` 屬於哪個迴圈、這個變數哪來的。這就引出第一個實用技巧。

### diff context 不夠時，怎麼展開看周圍

三種手段，由輕到重：

1. **加大 context**：`git show -U20 <commit>`（或 `git diff -U20`）把上下 context 從 3 行拉到 20 行。GitHub PR 頁面上點那個「展開」的箭頭是同一件事。這是最省事的第一招——很多「這改動看不懂」其實只是 context 太少。

2. **看完整函式**：`git show <commit>:path/to/file.c` 印出**那個 commit 當下**的完整檔案內容（注意是 `commit:path` 語法，冒號分隔）。你想看改動點所在的整個函式時用這招，把 diff 的那幾行放回它的完整脈絡。

3. **跳出 diff，回到 codebase**：checkout 出來，用前面學過的所有武器（`rg`、cscope 反查呼叫者、clangd 跳定義）去追「這個被改的函式，誰在呼叫它？改了它的行為，哪些呼叫點會受影響？」——這是 diff **永遠不會告訴你**的，也是最容易漏掉 bug 的地方。

第 3 點是 junior 和 senior reviewer 的分水嶺。junior 讀 diff 只讀 diff 框裡的東西；senior 讀到一個函式簽章變了，反射動作是去查「所有呼叫者」，因為簽章改動的爆炸半徑在 diff 外。

## 真跑：讀 redis 一個真實 commit

理論講夠了。我們拿 redis 7.4.0 附近一個**真實的 bugfix commit** 來走一遍 review-讀的流程。這個 commit 是 `88af96c7a`（PR #13407，"Trigger Lua GC after script loading"），修的是「大量 Lua script 載入後，垃圾一直不被回收」的問題。我選它是因為它同時包含**行為修正**和**重構**兩種成分，很適合練 review 眼力。

（環境：WSL Ubuntu 22.04，沙包 `~/reading_code_lab/redis`，已 `git fetch --unshallow` 補回完整歷史。）

### 第一步：先讀「作者聲稱做了什麼」

review 的第一動作**永遠是讀 commit message**，別急著看程式碼。作者的意圖是你後面判斷「改對了沒」的基準線。

```
$ git show 88af96c7a --stat | head -30
commit 88af96c7a2548013273d0be2bcb3690328e52a24
Author: debing.sun <debing.sun@redis.com>
Date:   Tue Jul 16 09:28:47 2024 +0800

    Trigger Lua GC after script loading (#13407)

    Nowdays we do not trigger LUA GC after loading lua script. This means
    that when a large number of scripts are loaded, ... the garbage might
    remain there indefinitely.

    Before this PR, we would share a gc_count between scripts and functions.
    ...
    In this PR, we assign a unique `gc_count` to each of them, so the GC
    triggers between them will no longer affect each other.
```

從這段 message，我們在讀 code **之前**就抽出了幾個「待驗證的主張」（把它們寫下來，這是 Ch 35 的外化）：

- **主張 A**：以前 script 載入後不會觸發 GC → 這次要加觸發點。
- **主張 B**：以前 script 跟 function **共用**一個 `gc_count`，不公平 → 這次要**各自獨立**一個。
- **隱含主張 C**：作者自己承認「這會給 `SCRIPT LOAD`/`FUNCTION LOAD` 帶來一點回歸（regression），但不是熱路徑，可接受」。這種作者自曝的取捨，是 review 時要盯的重點。

現在帶著這三個主張去讀 diff，而不是空腦子讀。

### 第二步：看 stat，先建立「改動地形」

```
$ git show 88af96c7a --stat | tail -8
 src/eval.c               |  9 +++++++++
 src/function_lua.c       |  4 ++++
 src/script_lua.c         | 35 ++++++++++++++++++-----------------
 src/script_lua.h         |  2 +-
 tests/unit/functions.tcl | 10 +++++-----
 tests/unit/scripting.tcl | 21 +++++++++++++++++++++
 6 files changed, 58 insertions(+), 23 deletions(-)
```

不看內容，光看 stat 就能建立地形圖：

- `eval.c`（純加 9 行）+ `function_lua.c`（純加 4 行）→ 這兩個是**呼叫端**，加了「觸發 GC」的呼叫點。對應主張 A。
- `script_lua.c`（+/- 交錯，35 行變動）→ 這是**改動核心**，既刪又加，八成是把邏輯抽成一個共用 helper。
- `script_lua.h`（改 2 行）→ header 動了，代表**有函式簽章對外變了**。反射動作亮起：header 一改，就要問「誰 include 它、誰受影響」。
- 兩個 `.tcl` 是測試。**有測試** → 加分。review 一個沒帶測試的行為修正，要先皺眉。

光讀 stat，我們已經猜出改動的骨架：「把散在各處的 GC 邏輯抽成 `script_lua.c` 裡一個 helper（簽章進 header），然後在 eval / function 兩個引擎各自呼叫它、各自帶自己的 counter」。現在去 diff 驗證這個猜測。

### 第三步：讀核心 hunk，驗證每個主張

先看核心檔 `script_lua.c` 的重構：

```
$ git show 88af96c7a -- src/script_lua.c
...
+/* Call the Lua garbage collector from time to time ...
+ * Each script VM / State (Eval and Functions) maintains its own unique `gc_count`
+ * to control GC independently. */
+#define LUA_GC_CYCLE_PERIOD 50
+void luaGC(lua_State *lua, int *gc_count) {
+    (*gc_count)++;
+    if (*gc_count >= LUA_GC_CYCLE_PERIOD) {
+        lua_gc(lua, LUA_GCSTEP, LUA_GC_CYCLE_PERIOD);
+        *gc_count = 0;
+    }
+}
```

同一個 diff 裡，被刪掉的舊碼是：

```
-    #define LUA_GC_CYCLE_PERIOD 50
-    {
-        static long gc_count = 0;
-
-        gc_count++;
-        if (gc_count == LUA_GC_CYCLE_PERIOD) {
-            lua_gc(lua,LUA_GCSTEP,LUA_GC_CYCLE_PERIOD);
```

**這一組 `+`/`-` 就是主張 B 的全部真相，值得逐字讀：**

- 舊碼：`static long gc_count`——`static` 意味著**全域唯一、所有呼叫共享**。這正是「script 跟 function 共用、不公平」的根源。
- 新碼：`void luaGC(lua_State *lua, int *gc_count)`——counter 變成**參數傳入**，由呼叫端各自持有。獨立性透過「把狀態外移給呼叫端」達成。這是很乾淨的重構手法：**消除共享全域狀態的標準做法就是把它變成參數。**
- 順手抓到一個微妙變動：舊碼 `gc_count == LUA_GC_CYCLE_PERIOD`（**恰好等於**才觸發），新碼 `*gc_count >= LUA_GC_CYCLE_PERIOD`（**大於等於**）。`==` 改 `>=` 看似無關痛癢，其實是**防禦性**修正：一旦有路徑讓 counter 一次加超過 1、或某次錯過了那個精確值，`==` 會永遠不觸發、垃圾永遠不回收。改成 `>=` 消除這個潛在死角。**這種「順手把脆弱的相等判斷改成範圍判斷」的小改動，review 時要能看出它的意圖，並在心裡給作者加分。**

再看呼叫端 `eval.c`，驗證主張 A（新增觸發點）與 C（load 路徑也加了）：

```
$ git show 88af96c7a -- src/eval.c
...
+static int gc_count = 0; /* Counter ... reset after each GC execution */
...
 sds luaCreateFunction(client *c, robj *body, int evalsha) {
     ...
         lua_pop(lctx.lua,1);
+        luaGC(lctx.lua, &gc_count);      /* <- 錯誤路徑也回收 */
         return NULL;
     ...
     incrRefCount(body);
+
+    /* Perform GC after creating the script and adding it to the LRU list,
+     * as script may be evicted during addition. */
+    luaGC(lctx.lua, &gc_count);          /* <- load 成功路徑，就是主張 A/C */
+
     return sha;
 }
...
 void evalGenericCommand(client *c, int evalsha) {
     ...
     scriptResetRun(&rctx);
+    luaGC(lua, &gc_count);               /* <- 執行路徑，取代被刪掉的舊 inline 版 */
```

到這裡三個主張全部對上了。注意 `eval.c` 頂端那個 `static int gc_count = 0`——它是 eval 引擎**專屬**的 counter；`function_lua.c` 裡另有一個同名 `static` 變數，是 function 引擎專屬的。兩個檔各一個 `static`，正是「不再共享」的具體實現。**這是讀 diff 時容易漏的關聯：兩個不同檔案裡的兩個同名 `static` 變數，合起來才講完「獨立性」這個故事。** 只讀單一 hunk 你看不出這層，得把整個 commit 的 hunk 拼起來讀。

### 第四步：跑一遍 review 心智清單

改動看懂了，但「看懂」不等於「review 完」。真正的 review 是拿改動去撞一張清單。下面這張清單我建議背起來，逐項對 `88af96c7a` 過一遍：

| 維度 | 問題 | 對本 commit 的判斷 |
|---|---|---|
| **正確性** | 改動真的達成 commit message 的目標嗎？ | 是，三主張皆對上 |
| **邊界** | 有沒有邊界會出錯？counter 溢位？ | `int gc_count` 每到 50 歸零，永遠不會逼近 `INT_MAX`，安全 |
| **錯誤路徑** | 失敗/提早 return 的路徑也照顧到了嗎？ | 有——`luaCreateFunction` 的錯誤 `return NULL` 前也加了 `luaGC`，作者沒漏 |
| **副作用/爆炸半徑** | header 簽章變了，diff 外有誰受影響？ | 需離開 diff 用 cscope 反查（下一步做） |
| **命名** | 新符號名稱表意清楚？ | `luaGC` / `gc_count` 清楚；但 helper 從 `static` 提升成 export 符號，命名夠不夠獨特避免衝突？（可挑剔點） |
| **測試** | 有沒有測到新行為？ | 有兩個 `.tcl` 改動，但要看是否真的斷言「GC 有觸發」還是只是既有測試順帶 |
| **取捨/回歸** | 作者自曝的 regression 可接受嗎？ | load 非熱路徑，可接受；作者已誠實標註，加分 |

這張清單的價值不在於「每一項都要挑出毛病」，而在於**強迫你系統性地掃過每個維度，不讓任何一類問題從指縫漏掉**。人腦 review 最大的失敗模式是「只盯著看得懂的地方猛看，看不懂的地方假裝沒看到」。清單把這個弱點補起來。

### 第五步：離開 diff，追爆炸半徑

清單裡「副作用/爆炸半徑」那格，diff 自己答不了。`script_lua.h` 多 export 了一個 `luaGC` 符號——我們得跳出 diff，回到 codebase 問「誰會用到它、有沒有命名衝突」：

```
$ git checkout 88af96c7a
$ cscope -b -q -R -s src
$ cscope -d -L -3 luaGC          # 誰呼叫 luaGC
src/eval.c          luaCreateFunction     ...  luaGC(lctx.lua, &gc_count);
src/eval.c          evalGenericCommand    ...  luaGC(lua, &gc_count);
src/function_lua.c  ...                    ...  luaGC(lua, &gc_count);
```

呼叫者就是 diff 裡動過的那幾處，沒有「diff 外還有人偷偷依賴舊行為」的驚喜。**這一步做完，你才真的能對「副作用」那格打勾**——而不是憑感覺假設「應該沒別人用」。這正是 diff 讀碼與全檔讀碼必須交替使用的原因：diff 給你高密度的訊號，codebase 給你 diff 缺的上下文。

## 進階工具：讓 diff 更好讀

- **`git range-diff A B`**：**diff 的 diff**。當一個 PR 被 reviewer 打回、作者 force-push 了新版本，你想知道「第二版相對第一版又改了哪些」，`git range-diff old-base..old-head new-base..new-head` 會逐 commit 對照兩個版本序列。review 迭代中的 PR 時這是神器——你不用重看整個 PR，只看「這輪又動了什麼」。
- **`gh pr diff <n>`**（GitHub CLI）：不開瀏覽器，直接在終端機把某個 PR 的完整 diff 拉下來，配合 `| less` 或 `| delta` 閱讀。可惜本課 WSL 環境沒裝 `gh`，這裡只給用法不貼假輸出——你自己環境有的話，`gh pr diff 13407 --repo redis/redis` 就能重現上面那個 commit 的 PR 視角。
- **`delta`**：一個 diff 的語法高亮 pager，把 `git show` 的輸出變成有色、對齊、行內高亮（word-level diff）的樣子，長 diff 讀起來省眼力一個檔次。本課環境未安裝，故不貼輸出；裝法 `cargo install git-delta` 或發行版套件，然後 `git config --global core.pager delta`。
- **`git show --word-diff`**：不裝任何東西，就能把「一行裡只改了幾個字」的改動用行內標記高亮出來，避免你把整行當成全變了。改一個字面常數、改一個函式名這種微改動特別有用。
- **`git log -p -S'某字串'`** / **`-G'regex'`**：**pickaxe**。「這個字串/這段邏輯是哪個 commit 引入或刪除的」——`-S` 找「出現次數改變」的 commit，`-G` 找「diff 內容 match regex」的 commit。追一個行為的來歷時，這是把「讀 diff」和「git 考古」（Ch 17）接起來的橋。

## 對比與取捨

| 讀法 | 訊號密度 | 上下文完整度 | 最適合 | 主要風險 |
|---|---|---|---|---|
| 讀全檔 | 低（大海撈針） | 高（全都在） | 第一次理解一個模組的全貌 | 慢、抓不到重點 |
| 讀 diff（預設 -U3） | 極高 | 極低 | review、追改動、學「最近在動什麼」 | 在無上下文下對改動點頭 |
| 讀 diff + 展開 context（-U20） | 高 | 中 | 改動看不懂時的第一救援 | 仍看不到跨檔關聯 |
| 讀 diff + 回 codebase 追呼叫者 | 高 | 高 | 判斷副作用、爆炸半徑 | 最花時間，但最不會漏 bug |

**實戰策略**：review 一個 PR 時，順序永遠是「commit message → stat 建地形 → 核心 hunk 讀懂改動 → 過 review 清單 → 針對『副作用』那格離開 diff 追呼叫者」。不要一上來就一行一行讀 diff，那是最沒效率的讀法。

另一個取捨層面：**用讀 PR 當學 codebase 的捷徑**。想快速上手一個陌生模組，`git log --oneline -20 -- src/networking.c` 列出最近碰它的 20 個 commit，挑幾個 `git show` 來讀——你會在一小時內學到「這個模組最近在解什麼問題、活躍的貢獻者是誰、哪些地方脆弱到需要反覆修」。這比硬讀 3000 行原始碼高效得多，因為 **diff 天然聚焦在「有人覺得值得改的地方」**，而那通常就是模組裡最有意思、最容易出錯的部分。

## 踩雷集錦

1. **錯誤直覺：「diff 看得懂 = 改動沒問題」。** 正確認識：你看懂的是「作者給你看的那幾行」，真正的 bug 常藏在**沒出現在 diff 裡的呼叫者**。一個函式改了回傳語意，diff 裡它自己看起來人畜無害，災難發生在三個檔案外某個沒更新的呼叫點。看到函式簽章或回傳語意變動，反射去 cscope 反查呼叫者。

2. **錯誤直覺：「hunk header 那行是雜訊，跳過。」** 正確認識：`@@ ... @@` 後面那段函式簽章是 git 免費送你的定位情報，告訴你這個 hunk 落在哪個符號。忽略它，你會在不知道「這改動在哪個函式裡」的情況下亂讀。（注意它是啟發式，偶爾會標錯到上一個函式，但九成準。）

3. **錯誤直覺：「只讀新增的 `+` 行就好，`-` 行是要丟的、不重要。」** 正確認識：**被刪的行往往比新增的行資訊量更大**。上面 redis 例子裡，是那行被刪的 `static long gc_count`（共享全域）才讓你懂了整個「不公平」問題的根因。不讀 `-` 行，你只知道「加了什麼」，不知道「為什麼要改」。

4. **錯誤直覺：「大 PR 就一行一行從頭讀到尾。」** 正確認識：3000 行的 diff 逐行讀必定失焦。先用 `--stat` 分出「核心改動檔 vs 機械式改動檔（rename、格式化、大量測試資料）」，把 review 精力集中在核心那 1-2 個檔。機械式改動用 `--word-diff` 或直接抽樣掃過即可。

5. **錯誤直覺：「這 PR 有加測試，代表行為被驗證了。」** 正確認識：有測試檔改動 ≠ 測試真的斷言了新行為。要打開測試看它到底 assert 什麼——很多「測試改動」只是既有測試因為 API 微調而順手改，根本沒覆蓋這次修的那個 bug。review 測試跟 review 產品碼一樣要較真。

6. **錯誤直覺：「context 只有 3 行看不懂，那就是我 C 不好。」** 正確認識：多半不是你的問題，是 context 太少。先 `git show -U30` 把周圍拉出來，或 `git show commit:file` 看完整函式。把「看不懂」先歸因於「訊息不足」，再歸因於「自己不懂」，能省你很多自我懷疑。

## 進階：再往深一層

- **review 順序影響你抓到什麼 bug**：認知研究與 reviewer 經驗都指出，人對 diff 前段的注意力遠高於後段（疲勞遞減）。刻意反過來：長 PR 從最後一個檔往前 review 一遍，你會抓到平常漏掉的東西。或者乾脆**分兩次不同時間**review，每次換個切入維度（一次只看正確性、一次只看命名與測試）。
- **語意 review vs 語法 review**：diff 是純文字的，它不知道「你把這個變數從 `int` 改成 `size_t`」在型別上意味著什麼。真要深追副作用，把兩個版本各自餵給 clangd/編譯器，看**型別層面**變了什麼——例如一個隱式轉換是否從安全變成有號溢位。這把「讀 diff」升級成「讀語意 diff」，是找 Ch 32 那種漏洞式改動的關鍵。
- **從 review 一個 commit 到 review 一串 commit**：一個功能常是 5-10 個 commit 疊起來的。逐 commit review（`git log -p base..head`）比看合併後的一坨大 diff 好，因為每個 commit 是作者刻意切出的一個「思考單元」。若作者 commit 切得爛（一個 commit 混了重構+修 bug+格式化），那本身就是 review 該提的意見——**難 review 的 diff 往往反映的是難維護的變更結構**。
- **把 review 清單變成 CI/lint**：你 review 時反覆手抓的東西（未檢查回傳值、`malloc` 後沒判 NULL、簽章改了呼叫者沒跟上），很多能被靜態分析工具（Ch 16）或 CI 規則自動抓。senior 的做法是把「這類 bug 我第三次在 review 抓到」變成一條自動規則，讓機器擋掉，人腦省下來看機器看不懂的語意問題。

## 動手練習

1. **完整走一遍 review 流程**：在你的 redis 沙包（記得先 `git fetch --unshallow`）對 `git show 7d3545cb1`（PR #13412，"Reduce redundant call of prepareClientToWrite"）跑一次本章五步流程：讀 message 抽主張 → `--stat` 建地形 → 讀核心 hunk → 過 review 清單 → cscope 反查 `prepareClientToWrite` 的呼叫者評估爆炸半徑。寫下你抓到的每一個「待驗證主張」。
2. **練展開 context**：對同一個 commit，先 `git show 7d3545cb1`（預設 -U3），把你「看不懂為什麼這樣改」的 hunk 記下來；再 `git show -U25 7d3545cb1` 展開，看有多少「看不懂」其實只是 context 不夠。
3. **練讀 `-` 行**：找一個含重構的 commit（`88af96c7a` 就是），只讀被刪的 `-` 行，試著在不看 `+` 行的情況下，說出「這次改動想消除的舊設計缺陷是什麼」。
4. **用 PR 學模組**：`git log --oneline -15 -- src/networking.c`，挑 3 個 commit `git show`，一小時內寫出「networking 模組最近在解什麼問題、哪裡最脆弱」的一段話。體會「讀 diff 當 codebase 導覽」。
5. **（選）pickaxe 考古**：用 `git log -p -S'LUA_GC_CYCLE_PERIOD' -- src/` 找出這個常數的**引入與這次搬移**兩個時間點，把「一段邏輯的生命史」串起來。

## 本章重點整理

- code review 式讀碼的單位是「改動」不是「檔案」；diff 是訊號密度極高、但 context 極貧乏的一種 code。
- 核心心法：讀 diff = 讀作者聲稱的改動 + 主動補齊他沒給你看的上下文 + 判斷兩者是否一致。
- 讀 diff 五步：commit message 抽主張 → `--stat` 建地形 → 核心 hunk 讀懂 → 過 review 清單 → 離開 diff 追呼叫者評估爆炸半徑。
- context 不夠三招：`-U20` 加 context、`git show commit:file` 看完整函式、checkout 回 codebase 用全套武器。
- `-` 行常比 `+` 行資訊量大；hunk header 是免費定位情報；有測試檔改動不等於行為被驗證。
- 工具：`git range-diff`（diff 的 diff）、`gh pr diff`、`delta`、`--word-diff`、pickaxe（`-S`/`-G`）。
- 用「讀最近的 PR」當學一個陌生模組的捷徑——diff 天然聚焦在「有人覺得值得改的地方」。

## 自我檢核

- [ ] 不看筆記，你能說出讀 diff 相對讀全檔的那個「不對稱」（高訊號密度 vs 貧乏 context）以及它帶來的具體風險嗎？
- [ ] 一個 hunk 的 `@@ -174,6 +174,7 @@` 每個數字什麼意思？後面那段函式名有什麼用？
- [ ] 一個函式在 diff 裡看起來人畜無害，你怎麼判斷它會不會在 diff 外炸掉別人？用哪個工具？
- [ ] 為什麼「只讀 `+` 行」是危險的？舉本章那個 redis 例子說明 `-` 行帶了什麼關鍵資訊。
- [ ] 你的 review 心智清單有哪幾個維度？面試官問「你 review PR 都看什麼」，你能一口氣講出正確性/邊界/錯誤路徑/副作用/命名/測試/取捨嗎？

## 延伸閱讀

- **[Google Engineering Practices — Code Review Developer Guide](https://google.github.io/eng-practices/review/)**
  - **讀哪裡**：`reviewer/` 底下的 "What to look for in a code review" 與 "Navigating a CL in review"。
  - **學到什麼**：Google 內部 reviewer 的標準清單（設計、功能、複雜度、測試、命名、註解），跟本章的 review 心智清單高度重疊，但更完整，還教你「一個大 CL 該按什麼順序讀」。
  - **前提**：無，讀過本章直接看更有共鳴。

- **[Git 官方文件 — `git-range-diff`](https://git-scm.com/docs/git-range-diff)**
  - **讀哪裡**：開頭的 "DESCRIPTION" 與 "EXAMPLES"，看它怎麼把兩個 commit 序列對照成「diff 的 diff」。
  - **學到什麼**：review 一個反覆 force-push 的 PR 時，怎麼只看「這輪又改了什麼」而不是重讀整個 PR。這是 Linux kernel / git 郵件列表 patch series review 的日常工具。
  - **前提**：懂 `git rebase` 造成 commit hash 變動的概念。

- **[delta — a syntax-highlighting pager for git](https://github.com/dandavison/delta)**
  - **讀哪裡**：README 的動圖與 "Features" 一節，特別是 side-by-side 與 word-level diff。
  - **學到什麼**：一個好的 diff pager 能把讀長 diff 的體感提升一個檔次；看它支援的功能，反過來會讓你意識到「原來讀 diff 有這麼多可以被工具化的痛點」。
  - **前提**：無。裝了就能立刻感受差別。

讀 diff 是「別人已經改好、你來驗證」的場景。下一章翻到硬幣的另一面：**當你才是那個要改的人——為了把一段 code 移植到別的平台、或整個重寫成另一種語言，你得從實作反推出它到底承諾了什麼。** 這是讀碼裡最需要「讀出言外之意」的一種。

→ [Ch 34 為了移植/重寫而讀](./34-reading-to-port-rewrite.md)
