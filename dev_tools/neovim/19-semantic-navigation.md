# Ch 19 — 語意導航：gd / gr / call hierarchy

> **目標**：這是 Part 4 的重頭戲，也是全課「懂語意」的操作核心。前兩章把 clangd 接上、餵飽 compile_commands.json，這章把它變成你的**逆向探針**：`gd`（跳真定義）、`gD`（跳宣告）、`gr`（找所有引用）、`gi`（跳實作）、`K`（hover 看型別/簽名）、type definition、以及讀碼追呼叫鏈的利器——**call hierarchy**（incoming/outgoing calls）。每個操作都連到「讀大型 C 專案時你什麼時候用它」。學完你在陌生 codebase 裡追 data flow、找影響面、溯源呼叫鏈，全程不碰滑鼠、不靠文字猜。

> **環境**：Neovim v0.12.4，WSL2 / Ubuntu，clangd 14。本章 `gd`/`gr`/hover/call-hierarchy 的輸出，都是 headless 對真 C 專案（含一個 Lua 5.4 的真實 call chain）跑 clangd-14 照抄的。

## 為什麼需要這個？導航即讀碼

讀碼是**導航密集**的活。你讀一個陌生函式，讀到第三行呼叫了 `foo()`，你想知道 `foo` 做什麼——跳過去看，看完跳回來。讀到一個變數 `ctx`，不知道它什麼型別——hover 一下。想知道這個函式是被誰呼叫的——找 reference 或開 call hierarchy。**這些操作一天做幾百次，它們的摩擦決定你讀碼的速度**，也決定你能不能維持思路（每次跳轉的認知成本越低，你越能專注在「這段 code 在幹嘛」而不是「我怎麼跳過去」）。

Part 2 的 ripgrep 和 Part 1 的 motion 也能移動，但它們是**文字級**的。你 grep `foo`，撈到所有叫 `foo` 的東西——別的檔的 `static foo`、註解裡的 `foo`、log 訊息裡的 `foo`。當符號名很獨特時還好，符號名一普通（`init`/`read`/`free`/`len`），文字搜尋就把你淹沒。語意導航不一樣：它問 clangd「游標下**這一個** `foo` 的定義在哪」，clangd 從 AST 知道它綁到哪個宣告，給你**唯一正確**的答案。

這章就是把 `reading_code` Ch 13 那套「把 clangd 當逆向探針」的方法，變成 Neovim 裡的按鍵肌肉。

## 先建立直覺：五個探針，各探一種問題

把 clangd 的語意能力想成五支探針，讀碼時各回答一個問題：

```
   讀到一個符號，你想問什麼？
        │
        ├─ 「它的定義（實作）在哪？」   → gd   （definition）
        ├─ 「它的宣告（原型）在哪？」   → gD   （declaration）
        ├─ 「哪些地方用到它？」         → gr   （references）
        ├─ 「它是什麼型別/簽名？」      → K    （hover）
        └─ 「誰呼叫它 / 它呼叫誰？」    → call hierarchy
```

跟你拿 IDA/Ghidra 逆 binary 時的 X-refs（cross-references）用法一模一樣——只是你在 source 上做，而且精準度來自真編譯而非啟發式。

## gd：跳到定義（真定義，非文字猜測）

`gd`（go-to-definition）是全課最常按的鍵。它問 clangd「游標下這符號的**定義本體**在哪」，跳過去。

「真定義」的重點在**精準**：clangd 從 AST 知道游標下的符號綁到哪個宣告，同名的區域變數、別檔的 static 函式、macro 裡的同名 token——它一個都不會混進來。

實測。一個小專案裡 `main.c` 呼叫了 `distance()`，定義在 `geometry.c`。在 `distance` 上跑 `gd`（headless 對 `textDocument/definition`，照抄）：

```
=== distance call at main.c line 8 ===
=== gd (definition) ===
  /tmp/demo/geometry.c : line 4 char 7      ← 精準跳到 double distance(...) { 的定義
```

跨檔、精準、唯一。這跟 ctags「丟五個同名候選讓你猜」是本質差別。

**讀碼情境：追 data flow**。你讀 `main()`，看到它呼叫 `process(data)`，想知道 `process` 對 `data` 做了什麼——`gd` 跳進 `process` 定義，讀完想回到 `main` 繼續，按 `<C-o>`（Ctrl-O）沿 jumplist 跳回原位（Part 1 Ch 5 教過 jumplist，這裡它跟 `gd` 是絕配）。`gd` + `<C-o>` 是「下鑽讀完再浮回」的基本節奏，追一條資料流就是這樣一層層鑽下去再退回來。

## gD：跳到宣告（原型）

C 把「宣告」（`.h` 裡的原型 `void foo(int);`）和「定義」（`.c` 裡的本體 `void foo(int x) { ... }`）分開。`gd` 帶你到**定義**（看實作），`gD`（go-to-declaration）帶你到**宣告**（看原型）。

為什麼分兩個鍵？**因為讀碼有兩種需求**：

- 想看「這函式**怎麼用**」（參數、回傳、header 上的註解）→ `gD` 跳宣告，看原型與 API 文件。
- 想看「這函式**怎麼實作**」→ `gd` 跳定義，讀本體。

clangd 精確分得清哪個是原型、哪個是本體（ctags 對這區分是模糊的，給你兩個候選讓你猜）。讀不熟的 C API 時 `gD` 特別有用：先看 `.h` 的原型和註解搞懂契約，再決定要不要 `gd` 進去看髒細節。

## K：hover 看型別/簽名/doc

`K`（hover）不跳轉，直接在游標處彈一個浮窗，告訴你游標下符號的**型別、簽名、文件**。這是**讀碼時最高頻的動作**——滑過任何變數就知道型別，滑過任何函式就知道簽名，**不用跳走就繼續讀**。

實測，對 `distance` hover（headless `textDocument/hover`，照抄）：

```
=== K (hover) ===
  ### function `distance`
  → `double`
  Parameters:
  - `Point a`
  - `Point b`

  double distance(Point a, Point b)
```

clangd 從 AST 直接吐出完整簽章：回傳 `double`、兩個 `Point` 參數。

**讀碼情境：精讀不被打斷**。你在讀一段密集的 code，卡在「`eventLoop->stop` 這個 `stop` 是什麼型別」。跳定義會把你的閱讀節奏打斷（跳走、看、跳回，思路斷了）。`K` 就地告訴你「`stop` 是 `int`」，你不離開這一行就繼續讀下去。這是「精讀模式」的核心工具——用 hover 補型別資訊，用 `gd` 只在真的需要看實作時才跳。**更狠的用法**：hover 一個被 macro 包住的 token，clangd 告訴你它展開後真正綁到什麼（Ch 21 的 macro 場景）。

## gr：找所有引用（送 quickfix）

`gr`（find-references）反過來：問 clangd「這符號**被哪些地方用到**」，把所有引用列出來，送進 **quickfix list**（Part 2 Ch 12 教過 quickfix，這裡它是 `gr` 結果的容器）。

實測，對 `distance` 找引用（headless `textDocument/references`，照抄）：

```
=== gr (references) count=6 ===
  main.c     : line 8    ← 呼叫
  geometry.c : line 4    ← 定義本身
  geometry.c : line 11   ← area_triangle 裡呼叫
  geometry.c : line 12   ← 呼叫
  geometry.c : line 13   ← 呼叫
  geometry.h : line 9    ← 宣告
```

六個引用，跨三個檔，含定義、宣告、四處呼叫，全部進 quickfix。你按 `:cnext`/`:cprev`（或 Part 2 教的 quickfix 導航鍵）一個個走訪。

**這跟 `rg -w distance` 差在哪？** 這例子結果可能很像，因為 `distance` 名字夠獨特。但符號名一普通就見真章：對 `init` 這種名字 `rg -w init` 會撈到幾百個無關的同名東西（別的模組的 init、註解、字串），`gr` 只給**真正指向這同一個符號**的引用。**符號越普通，`gr` 相對 grep 的優勢越大。**

**讀碼情境：找影響面**。你想改（或想搞懂）某個函式，第一件事是「誰在用它」——`gr` 一鍵列出所有 caller 送 quickfix，你逐一走訪確認影響範圍。這是 `reading_code` 收斂到改動點的核心操作：從「這函式做什麼」擴展到「動它會影響誰」。

## gi：跳到實作（implementation）

`gi`（go-to-implementation）在 C 用得少，但概念要懂。它針對「宣告與實作分離、且可能有多個實作」的情況——最典型是 C++ 的虛擬函式（一個介面，多個 override），或 C 裡透過函式指標表實現的「介面」（如 redis 的事件後端 `aeApiPoll` 有 epoll/kqueue/select 多個實作）。`gd` 跳到「宣告」或「唯一定義」，`gi` 找「這個介面的所有具體實作」。純 C 專案多半 `gd` 就夠，讀 C++ 或抽象化很深的 C 才需要 `gi`。

## type definition：跳到「這變數的型別」的定義

還有一個常被忽略但讀碼很有用的：**go-to-type-definition**（`vim.lsp.buf.type_definition`）。差別在：

- `gd` 在一個**變數** `Point p` 上 → 跳到 `p` 這個變數宣告的地方（`Point p = {...};`）。
- type definition 在同一個 `p` 上 → 跳到 **`Point` 這個型別**的定義（`typedef struct { double x; double y; } Point;`）。

**讀碼情境**：你看到一個 `ctx->handler`，想知道 `handler` 是什麼——它是個 struct 欄位，你其實想看「它的**型別**長怎樣有哪些成員」。type definition 直接帶你到那個型別的 struct 定義，比「先 `gd` 到欄位、再手動找型別」快一步。我把它綁 `<leader>D`。

## call hierarchy：追「誰呼叫誰」的利器

這是 Part 4 讀碼最強的工具之一，也是 `gr` 的升級版。`gr` 給你「一層」的引用清單；**call hierarchy** 給你**可展開的呼叫樹**，一層層往上（誰呼叫我）或往下（我呼叫誰）追。

- **incoming calls**（`vim.lsp.buf.incoming_calls`）：**誰呼叫這個函式**。往上溯源。
- **outgoing calls**（`vim.lsp.buf.outgoing_calls`）：**這個函式呼叫誰**。往下展開。

實測一個**真的 Lua 5.4 call chain**。Lua 的核心呼叫函式 `luaD_call`，問「誰呼叫它」（headless `callHierarchy/incomingCalls`，照抄）：

```
=== incoming calls to luaD_call ===
  <- callclosemethod @ lfunc.c:107
  <- luaT_callTM      @ ltm.c:103
  <- luaT_callTMres   @ ltm.c:119
  <- luaV_execute     @ lvm.c:1146       ← 字節碼直譯器主迴圈
  <- lua_callk        @ lapi.c:1004      ← 對外 API lua_call
  <- lua_pcallk       @ lapi.c:1043      ← 對外 API lua_pcall
```

一條指令，clangd 告訴你 `luaD_call` 被六個地方呼叫，跨四個檔——包括字節碼直譯器 `luaV_execute` 和兩個對外 API `lua_callk`/`lua_pcallk`。**這就是「這個核心函式最終是被哪些入口觸發的」的完整地圖**，用 grep 你得手動追、還會撞到同名雜訊。

**讀碼情境：溯源與展開**。追「這個底層函式是被哪個公開 API 觸發的」用 incoming（往上溯源到入口）；搞懂「這個入口函式一路呼叫了哪些東西」用 outgoing（往下展開執行路徑）。這正是 Ch 19 練習 D 要做的事——用 call hierarchy 從一個 API 入口追到底層核心，或反過來。

> **注意 call hierarchy 的一個真限制**：它對**函式指標**呼叫追不到。Lua 的 `luaD_pcall` 透過 `Pfunc func`（函式指標）呼叫真正的工作函式，clangd 的 outgoing calls 對它回傳空——因為靜態分析看不出函式指標在執行期指向誰。這是所有靜態工具的天花板，不是 clangd 壞了。遇到函式指標分派（callback、vtable、dispatch table），得靠讀 code 或動態工具（gdb 下斷點看實際跳到哪）補上。

## `<C-t>` / tagstack：跳轉的返回鍵

跳來跳去，怎麼回去？兩套返回機制，別搞混：

- **jumplist**（`<C-o>` / `<C-i>`）：Neovim 全域的「游標去過哪」歷史，`gd`/搜尋/`G` 這類大跳躍都會記進去。`<C-o>` 往回、`<C-i>` 往前。這是 Part 1 Ch 5 教的，日常主力。
- **tagstack**（`<C-t>`）：源自 ctags 時代的「跳定義專用返回棧」。傳統上 `Ctrl-]` 跳定義、`<C-t>` 彈回。LSP 的 `gd` 現在也可以配成推 tagstack。

實務上 **jumplist（`<C-o>`）是你的主力返回鍵**——它記所有跳躍，最直覺。`<C-t>` 是給偏好「定義跳轉獨立一個返回棧、不跟搜尋跳躍混在一起」的人。這門課的 config 用 `gd` + `<C-o>` 這一套，夠用且一致。理解 tagstack 的存在即可（Part 5 ctags 那章會再用到它）。

## config：Part 4 完整 LSP 鍵位

Ch 0 骨架只掛了 `gd`/`gr`/`K`/`<leader>ds`。這章把 `LspAttach` 那塊補成完整的語意導航鍵位。往 `init.lua` 的 `LspAttach` callback 加：

```lua
vim.api.nvim_create_autocmd("LspAttach", {
  callback = function(ev)
    local o = function(desc) return { buffer = ev.buf, desc = desc } end

    -- 五支探針
    vim.keymap.set("n", "gd", vim.lsp.buf.definition,      o("跳定義"))
    vim.keymap.set("n", "gD", vim.lsp.buf.declaration,     o("跳宣告"))
    vim.keymap.set("n", "gr", vim.lsp.buf.references,      o("找所有引用"))
    vim.keymap.set("n", "gi", vim.lsp.buf.implementation,  o("跳實作"))
    vim.keymap.set("n", "K",  vim.lsp.buf.hover,           o("hover 型別/簽名"))
    vim.keymap.set("n", "<leader>D", vim.lsp.buf.type_definition, o("跳型別定義"))

    -- call hierarchy：追呼叫鏈
    vim.keymap.set("n", "<leader>ci", vim.lsp.buf.incoming_calls, o("誰呼叫我"))
    vim.keymap.set("n", "<leader>co", vim.lsp.buf.outgoing_calls, o("我呼叫誰"))

    -- symbol 大綱（Ch 20 深化）
    vim.keymap.set("n", "<leader>ds", vim.lsp.buf.document_symbol, o("當前檔符號"))
  end,
})
```

`desc` 欄位讓 `:map` 和 which-key 這類外掛顯示人話說明，不是必要但對「自己懂的 config」有幫助。call hierarchy 的結果會進 quickfix，你用 quickfix 導航鍵走訪（Part 2 Ch 12）。

## 鍵位表

| 模式 | 按鍵 | 作用 | 讀碼情境 |
|---|---|---|---|
| n | `gd` | 跳定義（實作本體） | 下鑽讀實作，配 `<C-o>` 浮回 |
| n | `gD` | 跳宣告（原型） | 看 API 契約、header 註解 |
| n | `gr` | 找所有引用（→ quickfix） | 找影響面、誰在用它 |
| n | `gi` | 跳實作 | C++ 虛擬函式、C 介面多實作 |
| n | `K` | hover 型別/簽名 | 精讀不打斷，補型別 |
| n | `<leader>D` | 跳型別定義 | 看變數的型別長怎樣 |
| n | `<leader>ci` | incoming calls（誰呼叫我） | 溯源到入口 |
| n | `<leader>co` | outgoing calls（我呼叫誰） | 展開執行路徑 |
| n | `<C-o>` / `<C-i>` | jumplist 返回/前進 | 跳完回來的主力鍵 |
| n | `<C-t>` | tagstack 彈回 | 定義跳轉的專用返回棧 |

## 對比與取捨

| 操作 | 文字工具（rg/grep） | 語意工具（clangd） |
|---|---|---|
| 跳定義 | `rg` 撈同名一堆，自己挑 | `gd` 唯一正確 |
| 找引用 | `rg -w` 含同名雜訊 | `gr` 只給真引用 |
| 追呼叫鏈 | 手動一層層 grep + 過濾 | call hierarchy 可展開樹 |
| 函式指標分派 | 看不出 | **也看不出**（靜態天花板） |
| 成本 | 秒級、通用 | 要 CDB、要 index |
| 何時勝出 | 掃讀、找字串、樹編不起來 | 符號普通、要精準、追控制流 |

**原則**：掃讀、找字串、找 log 訊息 → ripgrep（快、通用）；「這個符號到底是哪個 + 追控制流」→ clangd。符號越普通、專案越大，clangd 越不可取代。

## 踩雷集錦

1. **`gd` 跳到宣告而非定義**：多半不是 `gd` 的錯，是缺 compile_commands.json（Ch 18 那個實證）。clangd 單檔模式下跨檔 index 建不起來，只能跳到同目錄 header 的宣告。先確認 CDB 有被載入。

2. **`gr` 漏引用 / call hierarchy 不完整**：剛開大專案時**背景 index 還沒建完**，跨檔查詢會漏。等 index 建好（clangd 通常有狀態提示）再用。跟工具品質無關，是資料還沒就緒。

3. **在 macro 名或 `#define` 上按 `gd` 行為怪**：clangd 對 macro 的跳轉是「跳到 `#define` 那行」，但 macro **展開後**的符號跳轉要在展開後的 token 上做。macro 重度的 code（kernel）這裡很多坑，Ch 21 專講。

4. **call hierarchy 對函式指標回傳空**：不是壞了。靜態分析看不出函式指標執行期指向誰（上面 Lua `luaD_pcall` 的例子）。callback/vtable/dispatch table 的分派得靠讀 code 或 gdb 動態追。

5. **跳太深回不去**：`gd` 連跳五層後想回原點，一直按 `<C-o>` 會沿路一站站退。想直接標記攻堅起點、之後一鍵跳回，用 mark（Part 1 Ch 8）或 harpoon（Part 6 Ch 26）——那是「外化攻堅點」該做的事，別全靠 jumplist 記。

## 進階：再往深一層

- **`vim.lsp.buf.definition` 的參數**：可以傳 `{ reuse_win = true }` 讓它在已開的 window 跳而非開新的，或配 handler 讓「只有一個結果時直接跳、多個時開 telescope 選」。Ch 20 會把 `gd` 接到 telescope 讓多結果時有模糊選單。

- **hover 的 markdown 渲染**：clangd 回的 hover 是 markdown。Neovim 內建會渲染，但想要更漂亮的邊框/語法高亮，可裝 `lspsaga` 或設 `vim.lsp.util.open_floating_preview` 的 `border`。這是選配美化，不影響功能。

- **把 clangd 當逆向探針的完整動線**：`reading_code` Ch 13 有個經典範例——搞懂 redis 事件迴圈：hover `aeMain` 看簽名 → `gd` 進去看 `while(!stop)` 主迴圈 → hover `stop` 確認是旗標 → `gr` 找誰設 `stop`（誰喊停）→ call hierarchy 看誰呼叫迴圈核心。整個過程零文字搜尋，每步都要精準。那是這章所有鍵的實戰串接，值得照著在 redis 上跑一遍。

- **LSP `references` 的 `includeDeclaration`**：`gr` 底層可控制要不要把「定義/宣告本身」也算進引用。預設含（上面 count=6 就含定義和宣告）。想只看「呼叫點」不看定義，可以在 config 裡設 `context.includeDeclaration = false`。

## 本章重點整理

- 五支語意探針：`gd`（定義）、`gD`（宣告）、`gr`（引用）、`K`（型別/簽名）、call hierarchy（呼叫關係）——像逆 binary 時的 X-refs，但精準來自真編譯。
- `gd` 跳**真定義**（唯一正確，非同名候選），配 `<C-o>` 浮回是追 data flow 的基本節奏。
- `gD` vs `gd`：看 API 契約跳宣告、看實作跳定義，clangd 分得清。
- `K`（hover）不跳轉就給型別/簽名，是「精讀不被打斷」的核心。
- `gr` 找所有引用送 quickfix，**符號越普通相對 grep 優勢越大**；用於找影響面。
- **call hierarchy** 給可展開的呼叫樹，incoming 溯源到入口、outgoing 展開執行路徑——但對**函式指標追不到**（靜態天花板）。
- jumplist（`<C-o>`）是返回主力；tagstack（`<C-t>`）是定義跳轉的獨立返回棧。

## 自我檢核

- [ ] 我能說出 `gd` 相對 `rg` 的精準差別，以及「符號越普通差距越大」的道理
- [ ] 我知道 `gd`（定義）與 `gD`（宣告）分別對應「看實作」與「看 API 契約」
- [ ] 我能用 `K` 在不跳走的情況下讀懂一段卡住的 code（補型別）
- [ ] 我知道 `gr` 送 quickfix、用於找影響面，且結果比 `rg -w` 準
- [ ] 我能用 call hierarchy 的 incoming/outgoing 分別做溯源與展開
- [ ] 我知道 call hierarchy 對函式指標追不到，以及這是靜態工具的天花板
- [ ] 我能分清 jumplist（`<C-o>`）與 tagstack（`<C-t>`）兩套返回機制

## 延伸閱讀

### 官方文件（優先）

- **Neovim `:help vim.lsp.buf`**
  - **讀哪裡**：`definition`、`declaration`、`references`、`hover`、`implementation`、`type_definition`、`incoming_calls`、`outgoing_calls`。這章每個鍵底下呼叫的就是它們。
- **Neovim `:help lsp-defaults`**
  - **讀哪裡**：0.11+ 開始 Neovim 內建了一些預設 LSP 鍵位（如 `grr`/`gri`/`grn`）。看它預設給了什麼，決定你要不要覆寫成本章的 `gd`/`gr` 風格。

### 規格

- **[LSP Spec — Language Features](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/#languageFeatures)**
  - **讀哪裡**：`textDocument/definition`、`references`、`hover`、`callHierarchy/*`。想懂每個鍵送出的 request 結構就讀這。

### 橫向連結

- **`soft_skills/reading_code` Ch 13**「LSP 與語意導航」
  - 本章是它的操作鏡像。那章的「把語意工具當逆向探針」實戰動線（redis 事件迴圈）與「何時語意工具救你的命」（同名/macro/多載）是這章鍵位的方法論支撐，務必對照。

五支探針到手，追單一符號很順了。但進一個**陌生大檔**時，你需要先鳥瞰它的結構——有哪些函式、哪些型別。下一章講 symbol 搜尋與 outline：document symbols 建當前檔地圖、workspace symbols 全專案搜符號名直接跳。

→ [Ch 20 symbol 搜尋與 outline](./20-symbol-search-outline.md)
