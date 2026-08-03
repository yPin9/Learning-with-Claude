# 練習 B — 大 repo 快速定位一個功能

> 目標：把 Part 2（Ch 9–12）整套串起來跑一遍。在一個**你沒讀過**的真專案上，限時定位一個功能的**入口**與**所有相關函式**，把結果組進 quickfix 逐一檢視。這是 reading_code 的「偵察 + 假設驅動 + 追 call chain」在 Neovim 裡的完整落地。

> **環境**：Neovim v0.12.4，WSL2 / Ubuntu，telescope + fzf-native + rg（Part 2 的 config）。靶場是 `github.com/lua/lua`（Lua 直譯器本體，純 C，35 個 `.c`）。**互動 UI（telescope picker、quickfix 視窗）無法貼截圖，參考解答以逐鍵描述**；但每一步的 rg 命中都是在 `/tmp/lua` 真跑照抄的，你能對照「你搜到的和參考解答一不一樣」。

## 為什麼是這個練習？

讀碼最常見的真實任務不是「讀完整個專案」，是「**我要改/理解某個功能，它在哪？相關的函式有哪些？**」。你被丟進一個 50 萬行的 repo，任務是「搞懂互動模式（REPL）怎麼運作」——你不可能從第一行讀到最後一行。你要用搜尋快速定位入口，順著 call chain 找出相關的一小撮函式，把它們組成一份清單逐一看懂。

這正是 Part 2 給你的能力：telescope 找、rg 搜、quickfix 組織。這個練習逼你在**限時**內把它跑順，逼出策略——因為讀碼是速度技能，慢慢摸不算會。

## 準備靶場

```bash
cd /tmp
git clone --depth 1 https://github.com/lua/lua.git
cd /tmp/lua
ls *.c | wc -l    # 35 個 .c 檔
```

Lua 直譯器：你在終端打 `lua` 進到那個能一行行輸入的互動介面（REPL, Read-Eval-Print Loop）——這個功能就是這次的目標。你**不用懂 Lua 內部**，就當它是個陌生 C 專案。用 Part 2 的 config 開它：

```bash
cd /tmp/lua && nvim lua.c    # 用你 Part 2 的隔離 config 開
```

## 任務

**限時 20 分鐘**。完成三件事：

1. **定位入口**：找到程式的 `main`，並找到「進入互動模式（REPL）」的那個函式（我們叫它「REPL 迴圈函式」）。回答：它叫什麼、在哪個檔哪一行、`main` 怎麼一路呼叫到它。
2. **找出所有相關函式**：REPL 迴圈函式**直接呼叫**哪些函式？把這一小撮「REPL 相關函式」全找出來（讀一行 code 使用者輸入、判斷輸入完不完整、執行、印結果——這幾件事各由哪個函式做）。
3. **組進 quickfix 逐一檢視**：把這些相關函式的**定義位置**組成一份 quickfix 清單，`]q` 逐項跳過去，每個看它的簽章與前幾行，確認「它負責 REPL 的哪一步」。

**交付**：一張手畫的 call chain（`main → ... → REPL 迴圈 → {它呼叫的那幾個函式}`），每個函式標「檔:行」與「一句話它做什麼」。

## 如果你卡住了（鍵位方向提示，5 條）

先自己試滿 20 分鐘再看。卡住時：

1. **找 `main` 別用 telescope find_files**——你要找的是**內容**（`int main`）不是檔名。用 `<leader>fg`（live_grep）打 `int main`，或 shell `rg -n "^int main" .`。C 專案的 `main` 常在跟專案同名的檔（這裡 `lua.c`）。
2. **從 `main` 往下追別硬讀**——`main` 通常很短，把重活丟給一個 `pmain` / `run` 之類的函式。游標停在那個被呼叫的函式名上，按 `gd`（跳定義，Ch 0 綁的）或 `<leader>fw`（grep_string 搜這個字）。
3. **「進入互動模式」有特徵字**——REPL 相關的函式名常含 `REPL`、`repl`、`interactive`、`prompt`、`readline`、`loop`。用 live_grep 打這些關鍵字試，`<C-space>` 二次過濾收窄。
4. **找「一個函式呼叫了誰」**——進到 REPL 迴圈函式的定義後，讀它的 body，把它呼叫的每個函式名記下來；或游標停在函式名上 `gd` 進去、`<C-o>` 跳回來（jumplist，Ch 5），逐個掃。
5. **組 quickfix**：找到那批函式後，`<leader>fg` 搜它們共同的命名前綴（Lua 的內部函式常有共同前綴），`<Tab>` 多選定義那幾行，`<C-q>` 送 quickfix，`]q` 逐項看。或直接 shell `rg -n "^static .*loadline|^static .*pushline" lua.c` 確認定義行。

## 分段步驟

### 第 1 段：定位入口（目標 ~7 分鐘）

- 用 live_grep 或 `rg` 找 `main`。確認在哪個檔。
- 讀 `main` 的 body（很短），找出它把控制權交給哪個函式。
- 追進那個函式，找出「不是跑腳本、而是進互動模式」的那條分支呼叫了哪個函式。**這個就是 REPL 迴圈函式。**

### 第 2 段：找相關函式（目標 ~8 分鐘）

- 進到 REPL 迴圈函式的定義，讀它的 while 迴圈。
- 它每一輪做四件事：讀一行輸入、（若輸入沒完）續讀、執行、印結果。找出各由哪個函式負責。
- 把這些函式名記下來。用 `gd` 跳進去確認每個是幹嘛的，`<C-o>` 跳回。

### 第 3 段：組 quickfix 逐一檢視（目標 ~5 分鐘）

- 把這批函式的定義位置送進 quickfix（telescope `<C-q>` 或 `:grep`）。
- `<leader>qo` 開 quickfix 看全貌——你的「REPL 攻堅地圖」。
- `]q` 逐項跳，每個看簽章 + 前 3 行，在你的 call chain 圖上補「一句話它做什麼」。

## 驗證方式

你的 call chain 圖應該長這樣（函式名、行號要對得上）。**互動操作截不了圖，但下面每個位置都是 `rg` 在 `/tmp/lua` 真跑出來的，拿來對答案**：

- `main` 存在、`pmain` 是它交棒的對象：

```
$ rg -n "^int main|^static int pmain" lua.c
731:static int pmain (lua_State *L) {
777:int main (int argc, char **argv) {
```

- `pmain` 在互動分支呼叫 `doREPL`（就是 REPL 迴圈函式）：

```
$ rg -n "doREPL" lua.c
698:static void doREPL (lua_State *L) {
764:    doREPL(L);  /* do read-eval-print loop */
768:      doREPL(L);  /* do read-eval-print loop */
```

- `doREPL` 的迴圈呼叫 `loadline`（讀一行）、`docall`（執行）、`l_print`（印結果）：

```
$ rg -n "loadline|docall|l_print" lua.c | rg "^70[0-9]:"
703:  while ((status = loadline(L)) != -1) {
705:      status = docall(L, 0, LUA_MULTRET);
706:      if (status == LUA_OK) l_print(L);
```

- `loadline` 再往下呼叫 `pushline`（讀一行）、`multiline`（續讀多行）、`incomplete`（判斷輸入完不完整）：

```
$ rg -n "^static int loadline|^static int pushline|^static int multiline|^static int incomplete" lua.c
574:static int incomplete (lua_State *L, int status) {
588:static int pushline (lua_State *L, int firstline) {
638:static int multiline (lua_State *L) {
661:static int loadline (lua_State *L) {
```

你完成時的 call chain：

```
main (lua.c:777)
  └─ pmain (lua.c:731)          做初始化、決定跑腳本還是互動
       └─ doREPL (lua.c:698)    REPL 迴圈：讀-執行-印，一輪一圈
            ├─ loadline (661)   讀一整條輸入（含多行續行）
            │    ├─ pushline (588)     讀一行原始輸入
            │    ├─ multiline (638)    輸入沒完時續讀下一行
            │    └─ incomplete (574)   判斷這行是不是「還沒打完」
            ├─ docall (執行)    把讀到的 chunk 跑起來
            └─ l_print (681)    印執行結果
```

**驗證通過的標準**：你的圖裡 `main → pmain → doREPL` 這條主幹對、`doREPL` 底下抓到 `loadline`/`docall`/`l_print` 至少兩個、`loadline` 底下抓到 `pushline`/`multiline`/`incomplete` 至少一個，且行號對得上。全部靠 Part 2 的搜尋在 20 分鐘內定位到——不用讀完 `lua.c` 的 800 行。

## 延伸挑戰

做完基本任務，挑一個往深挖：

1. **用 quickfix `:cdo` 批次標記**：把上面 7 個 REPL 函式的定義送 quickfix，`:cdo normal! O// [REPL]` 在每個定義上方插一行標記（**別存檔**，`u` 復原就好），體會「一次操作一批位置」。
2. **換用 grep_string 追反向**：游標停在 `incomplete` 上按 `<leader>fw`，看它除了被 `loadline` 呼叫，還有誰用它。這是從「一個函式往上找 caller」的方向。
3. **對照 fzf-lua**：如果裝了 fzf-lua（Ch 11），用 `<leader>zg`（fzf-lua live_grep）重跑第 1 段的搜尋，感受介面差異（`lua.c` 太小感覺不出速度差，但操作流程對照一遍）。
4. **換靶場：nginx**：`git clone https://github.com/nginx/nginx`，任務改成「定位 HTTP 請求處理的入口與相關函式」（提示：搜 `ngx_http_process_request`）。nginx 大得多，這時 Part 2 的圈範圍策略（`-t c`、指子目錄 `src/http`）才真正發揮。
5. **`resume` 追命中**：live_grep 搜 `doREPL` 的兩個呼叫點，`<CR>` 開第一個看完，`<leader>fr`（resume）回到結果列接著看第二個，體會 `resume` 省下的重打字。

## 自我檢核

完成後你應該能回答：

- [ ] 面對陌生 C 專案要找 `main`，我第一個動作是 live_grep 搜 `int main`（內容）而不是 find_files 搜檔名——為什麼？
- [ ] 我能從 `main` 用 `gd` + `<C-o>` 一路追到 REPL 迴圈函式，不用逐行讀完中間的 code
- [ ] 我知道怎麼從「一個函式的 body」抓出它呼叫的所有函式，並用 `gd` 逐個確認它們做什麼
- [ ] 我能把一批函式定義送進 quickfix，用 `]q` 逐項看完、一個不漏，而不是重開 picker 記進度
- [ ] 這整套（找入口 → 追 call chain → 組 quickfix → 逐項看）我能在 20 分鐘內對一個沒讀過的專案跑完

做完這個練習，你的 Part 2 出師了：面對任何陌生大 repo，你能用搜尋快速定位一個功能的入口與相關函式、組成攻堅清單系統化看完——這是 reading_code「偵察 + 追 call chain」的手。但到目前為止你都在跟**文字**打交道（rg 只認字串，會被同名騙）。下一 Part 進入 treesitter，讓你看得懂 code 的**結構**——依語法樹移動、選中一整個函式、看清巢狀，而不只是搜字串。

→ [Ch 13 treesitter 基礎與 master/main 分裂](./13-treesitter-basics.md)
