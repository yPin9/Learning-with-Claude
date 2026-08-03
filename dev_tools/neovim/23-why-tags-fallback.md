# Ch 23 — 為什麼需要 tags 後備

> **目標**：搞清楚 clangd 這種語意引擎在什麼真實場景會跪掉，以及為什麼「純符號索引」（ctags / cscope / GNU Global）在那些場景反而是唯一能動的東西。讀完你會有一張**決策表**：什麼時候信 clangd、什麼時候果斷退回 tags，不再對著跳不動的 `gd` 乾瞪眼。

> **環境**：Neovim v0.12.4，WSL2 / Ubuntu。universal-ctags 5.9.0、GNU Global/gtags 6.6.7、cscope 15.9，全是純 CLI，本 Part 的輸出都是當場真跑照抄。

## 為什麼需要這個？

Part 4 我們把 clangd 調到能精準跳定義、反查引用、看 call hierarchy。那是讀碼的天花板——它**真的編譯**你的 code，`read` 是哪個 `read`、巨集展開後長什麼樣，它都知道。既然這麼準，為什麼還要學三個「粗糙」的老工具？

因為 clangd 的精準有**前提**，而真實世界裡這些前提經常不成立：

- 它要一份 `compile_commands.json`（Ch 18 專門在生它）。**生不出來**，clangd 就半殘。
- 它要**編得起來**。你接手的 legacy 樹可能缺 header、要一台退役的 cross toolchain、要某個早就下架的 SDK。
- 巨集與 `#ifdef` 一多，clangd 只看得到**它被餵的那一種 config**（Ch 21 的坑）。另一個 arch、另一個 `CONFIG_` 組合下的 code，它當作不存在。

當 `gd` 跳不動、`gr` 空空如也，你需要一個**不問語意、不需編譯、指到目錄就索引**的後備。那就是 ctags / cscope / GNU Global——這三個工具活了三十年不是因為懷舊，是因為它們踩在一個 clangd 永遠踩不到的位置：**粗糙但穩、覆蓋全**。

這一章不教指令，先把「什麼時候該退回來」講清楚。指令在 Ch 24（ctags）與 Ch 25（gtags/cscope）。

很多人裝好 clangd 就以為讀碼工具鏈完備了，然後在第一棵編不起來的樹上卡死——`gd` 不動，他們開始懷疑是 config 壞了、LSP 沒裝好，花半小時 debug clangd，最後放棄用滑鼠捲。真正的問題是**他們沒有第二層**。有後備索引的人遇到同一棵樹的反應是：「clangd 沒 compile db，退 gtags」，三十秒後照樣在跳。這一章要讓你成為後者——不是學新工具而已，是建立「主武器會卡、我永遠有備援」的心態。這也是 `reading_code` 反覆強調的：**讀碼是導航密集的活，導航一旦卡住、思路就斷了**，所以你的工具鏈必須有冗餘。

## 先建立直覺：兩種索引的世界觀

```
   語意引擎 (clangd)              符號索引 (ctags/cscope/gtags)
 ┌───────────────────────┐     ┌───────────────────────────┐
 │ 我「編譯」你的 code   │     │ 我「掃過」你的 code 文字   │
 │ → 我知道型別、作用域  │     │ → 我只認得符號的「形狀」   │
 │ → 同名我分得清        │     │ → 同名我全給你、不排序     │
 │ 但我要 compile db     │     │ 我什麼前提都不要           │
 │ 且要編得起來          │     │ 殘缺 / 編不起來也照掃      │
 │ 大樹啟動慢、吃幾 GB   │     │ 十萬行 0.03 秒、幾 MB      │
 └───────────────────────┘     └───────────────────────────┘
         精準                            覆蓋 + 速度 + 韌性
```

關鍵心智模型：**clangd 理解語意，tags 只索引符號**。理解語意要付出「必須編譯」的代價；只索引符號則放棄「同名分辨、巨集真相」換來「什麼樹都能掃」。這不是誰取代誰，是**分層**——clangd 是你的主武器，tags 是主武器卡彈時保命的後備。

再細一層：「tags 系」內部也有語意光譜。ctags 是**純語法**——它只認得「`int foo(...) {` 這個形狀是一個定義」，不知道呼叫、不知道型別。cscope 和 gtags 是**半語意**——它們認得 C 的呼叫語法 `foo(...)`，所以能區分「這裡是呼叫 `foo`」和「這裡是定義 `foo`」，才做得到反查 caller。但它們的「半語意」到此為止：認得呼叫語法，不代表懂作用域或型別，所以**三者都分不清同名符號**（兩個 `main` 哪個是真入口，它們一律全給你）。這條「半語意的天花板」是後備工具的共同界線，也是你需要 clangd 的唯一理由——當問題是「這個 `read` 到底是哪個 `read`」時。

## clangd 會在哪裡跪：四個真實場景

### 場景一：Linux kernel——生 compile_commands 麻煩，且只涵蓋一種 config

kernel 是 clangd 的頭號難題，原因疊了好幾層：

- **Kbuild 不是普通的 build system**。它用遞迴 Makefile + 一堆自製規則，不是 `cmake`、不是單純 `make`，`bear` 未必包得乾淨。kernel 有內建的 `scripts/clang-tools/gen_compile_commands.py`，但你得先**成功編一次** kernel 才生得出來。
- **它只涵蓋你編的那一種 config**。kernel 有上萬個 `CONFIG_` 選項、多個 arch（x86 / arm64 / riscv…）。你用 `x86_64_defconfig` 編出來的 `compile_commands.json`，對 `drivers/net/wireless/` 裡某個只在 `CONFIG_ARM` 才編的檔完全沒有記錄——clangd 對那個檔跳不動。
- **`#ifdef` 地獄**。同一個函式在不同 config 下有不同定義，clangd 只認得被啟用的那份。

結論：想在**沒編過、或編的是別的 config** 的 kernel 子系統裡跳來跳去，clangd 幫不上。你需要一個「不管 config、把整棵樹的符號都掃進去」的索引——這正是 Practice E 要做的事。

再補一個 kernel 特有的坑，順便解釋為什麼連「反查 caller」都需要後備工具：kernel 大量用**函式指標分派**（一個 struct 裡塞一堆 `.handler = foo`，之後透過 `ptr->handler()` 呼叫）。這種 indirect call 讓「誰呼叫 `foo`」變得棘手——呼叫點寫的是 `ptr->handler(...)` 不是 `foo(...)`，clangd 和 cscope 的直接 caller 查詢都追不到。gtags 的 `-rx`（引用）能撈到 `.handler = foo` 這個**註冊點**，讓你手動接上那一段。這是讀 kernel 一定會撞到、也一定要會繞的，Ch 25 與 Practice E 專門處理。

### 場景二：超大 monorepo——啟動慢、吃記憶體

Chromium、Android、大公司的單體 repo 動輒上千萬行。clangd 在這種規模上開一個檔要背景 index 好幾分鐘、吃掉幾 GB RAM，還常常 index 一半就 OOM 或卡住；就算撐過去，它的 index 存在磁碟上動輒好幾 GB。相比之下，gtags 索引整個 kernel `net/ipv4` 子系統（100 個 C 檔）只要 **1.77 秒**（上面現場真跑），GTAGS 檔十幾 MB。

差異的本質是**它們在做的事根本不同層級**：clangd 對每個檔跑完整的 C++ 前端（preprocess、parse、建 AST、做語意分析），才能回答「這個 `read` 是哪個 `read`」；gtags 只掃文字建符號表，不碰語意。前者的成本隨語言複雜度與樹規模爆炸，後者幾乎線性且極小。所以當你只想快速回答「這個函式誰呼叫」「這個符號定義在哪」——不需要語意精準的那種問題——為它等 clangd 暖機五分鐘是荒謬的成本。**在超大 repo，gtags 常常是唯一「開了就能用」的選擇。**

### 場景三：編不起來的樹

這是最常見、也最痛的一種。你 clone 了一個十年沒維護的專案、或是只拿到某個子目錄的 source dump、或是要 cross-compile 但手邊沒有那顆晶片的 toolchain。**它就是編不起來**，於是 `bear -- make` 生不出 `compile_commands.json`，clangd 沒有 compile db，`gd` 直接不動。

ctags/gtags 不在乎。它們不編譯，指到目錄 `gtags` 一下，整棵樹的符號就進索引了——**能不能編譯跟能不能索引是兩件完全獨立的事**，這是後備工具最大的價值。

具體想想你會遇到的「編不起來」有多少種：只 clone 了 monorepo 的一個子目錄（缺其他模組）、廠商給你一包 source dump 但沒給 build system、專案要某個已停產晶片的私有 toolchain、十年前的 code 用的 autoconf 版本現在跑不動、CI-only 的 build 你本機重現不了……這些情況下 clangd 的 `gd` 一律不動，而 `gtags && global -x foo` 一律有答案。你不需要「讓它能編」才能開始讀——這對接手 legacy 或做逆向分析的人是決定性的差別。

### 場景四：混語言樹

一個專案裡 C + Go + Python + shell 混在一起（很多基礎設施專案長這樣）。clangd 只管 C/C++，其他語言它一概不理——你得為每種語言各裝一個 language server，而它們**彼此不通**（Python 的 pyright 不知道你的 C 擴充叫什麼）。GNU Global 透過 pygments/ctags plugin 能一次把幾十種語言都索引進**同一個** GTAGS，讓你在 C 和 Python 之間用同一套查詢跳。跨語言追一條「Python 腳本 → 呼叫 C 擴充 → 進到某個 C 函式」的路徑時，這是任何單一 language server 給不了的——一個索引吃全部語言，查詢不換工具。

## tags 系的優勢：不用 build、不用理解語意

把上面四個場景的共同點抽出來，就是 tags 系工具的三大優勢：

1. **不用 build**。索引 = 掃文字，跟能否編譯無關。編不起來的樹照樣索引。
2. **不用理解語意**。它不試圖搞懂型別與作用域，純用「符號長什麼形狀」建表。代價是同名分不清，好處是**快、穩、殘缺 code 也能掃**。
3. **覆蓋全**。clangd 只看得到「被它那份 config 編到的」code；tags 把整棵樹（所有 arch、所有 `#ifdef` 分支的定義）都掃進去。你要找的符號在哪個角落，它都索引得到。

一句話：**clangd 精準但挑食，tags 粗糙但什麼都吃**。讀大型、老舊、混雜的 C 樹，「什麼都吃」常常比「精準」更值錢——因為在那些樹上，精準的工具根本啟動不了。

把三個優勢連到 `reading_code` 的方法論：偵察一棵陌生大樹的第一步是**先建地圖**（哪些檔、哪些符號、大概怎麼呼叫），這一步不需要語意精準，需要的是**快、覆蓋全、什麼都能掃**——正是 tags 系的強項。等你偵察完、鎖定要細讀的那幾個函式，才可能（在能編的前提下）叫 clangd 上場確認語意。所以 tags 不是「clangd 的次級替代品」，它在讀碼流程裡有**自己的位置**：粗地圖與批次查詢引擎。

## 現場：clangd 瞎掉的地方，gtags 照樣答

抽象講「clangd 會跪」不夠有感，看一個真實對照。我們拿一棵**編不起來的 kernel 子系統**（Linux `net/ipv4`，用 sparse checkout 抓下來，沒有完整 kernel、沒有 build config，`bear -- make` 生不出 `compile_commands.json`）。這棵樹上：

- **clangd**：對 `net/ipv4/tcp_ipv4.c` 開檔，它 attach 得了、hover 得了單一 token，但沒有 compile db，`gd` 跳 `tcp_v4_do_rcv` 要嘛跳不動、要嘛跳到錯的同名符號——它不知道這個檔該用哪些 `-I`、哪些 `-D CONFIG_*` 編。
- **gtags**：`gtags` 一下（1.77 秒，整棵 `net/ipv4` 100 個 C 檔），`global -x` 立刻給定義：

```
$ cd /tmp/klab/linux        # 編不起來的 kernel 子系統
$ gtags                      # 不編譯，1.77s 建完整棵索引
$ global -x tcp_v4_rcv
tcp_v4_rcv       2070 net/ipv4/tcp_ipv4.c int tcp_v4_rcv(struct sk_buff *skb)
$ global -rx tcp_v4_rcv      # 誰引用它（clangd 在這棵樹上答不出來）
tcp_v4_rcv        364 include/net/tcp.h    int tcp_v4_rcv(struct sk_buff *skb);
tcp_v4_rcv       1934 net/ipv4/af_inet.c   	.handler	=	tcp_v4_rcv,
tcp_v4_rcv        207 net/ipv4/ip_input.c  	ret = INDIRECT_CALL_2(ipprot->handler, tcp_v4_rcv, udp_rcv,
```

同一棵樹，clangd 因為編不起來而半殘，gtags 秒答定義與引用——連 `tcp_v4_rcv` 是**透過函式指標 `.handler` 註冊、間接被呼叫**這種 kernel 特有的接線方式都撈得出來。這就是「編不起來 ≠ 不能索引」的具體樣子。Practice E 會把這條 call chain 完整追一遍；這裡先讓你看到「後備工具在 clangd 的死角照樣有答案」。

## 三個工具的定位對照

「tags 系」不是一個工具，是三個分工不同的工具。搞混它們的能力邊界，你會浪費時間在 ctags 上找它根本沒有的功能（這是最常見的坑）。

```
 ctags   =  符號「定義」索引     → 這符號定義在哪？（跳定義）
 cscope  =  C 交叉引用關聯查詢   → 誰呼叫它？它呼叫誰？哪裡用到這字串？
 gtags   =  兩者兼具 + 好整合     → 定義 + 所有引用，輸出格式乾淨、可腳本化
```

| 面向 | ctags | cscope | GNU Global (gtags) |
|---|---|---|---|
| 核心能力 | **定義**索引 | C **交叉引用**（含 caller/callee/字串） | 定義 **+** 引用 |
| 反查呼叫者（caller） | **不能** | ✓ 最精準（`-3`） | ✓（references，`-rx`） |
| 反查被呼叫（callee） | 不能 | ✓（`-2`） | 間接 |
| 找字串 / grep pattern | 不能 | ✓（`-4` / `-6`） | ✓（`-g`） |
| 建索引速度（10 萬行 C） | ~0.03s | ~0.04s | ~0.05s（本章 Lua 真跑） |
| 輸出腳本友善度 | 中（readtags） | 中（`-L`） | **高**（`--result=grep`） |
| 多語言 | 廣 | C/C++ 為主 | 廣（plugin） |
| Neovim 整合現況 | **內建原生支援**（`Ctrl-]`、tagstack） | **內建已移除**（Ch 25 詳談） | 外掛 / grepprg |
| 同名分辨 | 否 | 否 | 否 |

三個重點先記住，後兩章展開：

- **ctags 根本沒有「反查呼叫者」這功能**。它是定義索引，就這樣。想知道「誰呼叫 `foo`」，得用 cscope 或 gtags。這是 Ch 24 的邊界。
- **cscope 的 caller 查詢（`-3`）最精準**——它區分「呼叫 / 宣告 / 定義」，只回真正的呼叫點，還告訴你呼叫發生在哪個函式裡。但它的 Neovim 內建整合**在新版被移除了**，得換 gtags 當後端或用 CLI 灌 quickfix（Ch 25 的重頭戲）。
- **gtags 兩者兼具、輸出最乾淨**（`global --result=grep` 直接吐 `file:line:text`），最好接 Neovim 的 quickfix、fzf、CI。多語言也是它強。這是本 Part config 的主力。

## 先嘗一口：同一個問題，三工具怎麼答

抽象的分工表看完就忘，看一個具體的。同樣問「`luaV_execute` 這個函式（Lua 的位元組碼執行迴圈核心）**誰呼叫它、它定義在哪**」，三個工具給的東西差很多（都在編不起來的樹上、都不用 clangd）：

**ctags——只能答「定義在哪」**：

```
$ readtags -t tags luaV_execute
luaV_execute	lvm.c	/^void luaV_execute (lua_State *L, CallInfo *ci) {$/
```

給了定義（`lvm.c`）。問「誰呼叫」？ctags **沒有這個功能**，它是定義索引，就這樣。

**cscope——能答「誰呼叫」，還帶所在函式**：

```
$ cscope -d -L -3 luaV_execute        # -3 = 誰呼叫（caller）
ldo.c ccall 768 luaV_execute(L, ci);
ldo.c unroll 876 luaV_execute(L, ci);
ldo.c resume 933 luaV_execute(L, ci);
```

三個真呼叫點，還告訴你發生在 `ccall`/`unroll`/`resume` **哪個函式裡**——這一欄是追 call chain 的金礦。

**gtags——能答「所有引用」，格式最乾淨好整合**：

```
$ global -rx luaV_execute
luaV_execute      768 ldo.c    luaV_execute(L, ci);  /* call it */
luaV_execute      876 ldo.c    luaV_execute(L, ci);
luaV_execute      933 ldo.c    luaV_execute(L, ci);
luaV_execute      128 lvm.h    LUAI_FUNC void luaV_execute (lua_State *L, CallInfo *ci);  ← 宣告，混進來了
```

呼叫點都在，但**多混進一個 header 宣告**（`lvm.h:128`）。gtags 的 `-r` 給「引用」，比 cscope `-3` 粗一點，但輸出格式（`--result=grep`）最好接 Neovim 的 quickfix、fzf、CI。

一眼看清分工：**ctags 給定義（最輕）、cscope 給最精準的 caller（帶上下文）、gtags 給引用且最好整合**。Ch 24 展開 ctags、Ch 25 展開 gtags/cscope，Practice E 把三者用在編不起來的 kernel 上。

## 底層機制：後備索引怎麼跟 Neovim 接上

這門課的 config 從 Ch 0 骨架長起來。到這個 Part，我們往上加「tags 後備」這塊。三種工具接進 Neovim 的方式不同，先建立全貌（細節在 Ch 24/25）：

```
 ctags  ── tags 檔 ──▶ Neovim 「內建原生」讀
                        Ctrl-]（跳定義）、Ctrl-t（跳回）、:tag/:tselect
                        gutentags 外掛自動重建 tags（存檔後增量更新）

 gtags  ── GTAGS ──▶  兩條路：
                       (a) global --result=grep 當 grepprg → :grep 灌 quickfix
                       (b) gtags.vim / cscope_maps.nvim 外掛

 cscope ── cscope.out ─▶ 舊 Neovim 有內建 :cscope，新版「已移除」
                          → 改用 gtags-cscope 後端，或直接跑 CLI
```

`ctags` 這條是 Neovim **原生**的（`Ctrl-]` 三十年沒變），最無痛，Ch 24 講。`gtags` 這條要嘛靠 `grepprg` 把 `global` 的輸出灌進 quickfix、要嘛靠外掛，Ch 25 講——而且會踩到一個關鍵事實：**Neovim 把 Vim 的 `:cscope` 內建命令拿掉了**（Ch 25 headless 驗證給你看），所以「照抄 Vim 教學設 `cscopeprg`」在 Neovim 會直接報 `E518: Unknown option`。

到 Part 5 結束，Ch 0 的 config 骨架會多出這三塊（每塊在對應章節逐行解釋）：

```
Ch 0 骨架                    Part 5 加的（Ch 24 / 25）
┌─────────────────┐         ┌──────────────────────────────┐
│ treesitter      │         │ vim.opt.tags = "./tags;,tags" │ ← Ch 24：讓 Ctrl-] 找到 tags
│ telescope       │    +    │ gutentags（自動重建 ctags）    │ ← Ch 24：存檔增量更新
│ lspconfig/clangd│         │ <leader>g* → global + quickfix │ ← Ch 25：gtags 反查 caller
└─────────────────┘         └──────────────────────────────┘
       主武器                        clangd 跪掉時的後備
```

兩層是**共存**的，不是二選一：clangd 掛在 `gd`/`gr`（Part 4），tags 掛在 `Ctrl-]` 和 `<leader>g*`（Part 5）。同一個 buffer，能編就用 clangd，clangd 跳空了手一滑按後備。

## 怎麼判斷 clangd 是不是跪了（該退 tags 了）

決策的前提是**認得出 clangd 現在有沒有在正常工作**。`reading_code` 的信條是「工具卡住時先 debug 工具」——這裡給你幾個秒判斷的信號：

| 症狀 | 多半是 | 該怎麼辦 |
|---|---|---|
| `gd` 完全沒反應、游標不動 | clangd 沒 attach，或沒 compile db | `:LspInfo` / `:checkhealth lsp` 看 attach 沒；沒 compile db 就退 tags |
| `gd` 跳到**錯的同名**符號 | 缺 compile db，clangd 用啟發式猜 | Ch 18 生 compile db，或退 tags 用 `g Ctrl-]` 自己選 |
| `gr`（references）**空的**，但你知道有人用 | clangd 只 index 了 compile db 涵蓋的檔 | 退 gtags `-rx`，它掃全樹 |
| 開檔後轉圈幾分鐘、editor 卡 | clangd 在超大樹上背景 index | 別等，退 gtags（秒級） |
| 某個 `#ifdef` 分支裡的 code 像不存在 | clangd 只看被啟用的 config | 退 gtags，它把所有分支的定義都掃了 |

一句話 SOP：**`gd` 一有異狀，先 `:LspInfo` 確認 clangd 狀態，再決定是修 compile db（Ch 18）還是直接退後備**。別卡在「它為什麼不動」——分辨清楚是哪一層的問題，是這門課教你的核心技能。

## 對比與取捨：何時信 clangd、何時退 tags

這是本章最該帶走的東西——一張決策表：

| 你的處境 | 首選 | 為什麼 |
|---|---|---|
| 樹**編得起來**、能生 `compile_commands.json` | **clangd** | 語意精準，同名/巨集都對，call hierarchy 準 |
| 需要**同名分辨 / 巨集展開後的真相** | **clangd**（無可取代） | 這是 tags 的天花板，只有真編譯能答 |
| 樹**編不起來** / 缺 toolchain / 只拿到子目錄 | **gtags / ctags** | 不編譯照樣索引，唯一能動的 |
| **kernel / 多 config / 多 arch** 樹裡跳 | **gtags** | clangd 只看一種 config，gtags 掃全樹 |
| **超大 monorepo**、只想快查一個符號 | **gtags** | 秒級索引，不用等 clangd 暖機幾分鐘 |
| 只要**跳定義**、最輕量 | **ctags** | Neovim 原生 `Ctrl-]`，零外掛 |
| 要**反查 caller** 且是純 C | **cscope**（或 gtags `-rx`） | cscope `-3` 最精準；gtags 好整合 |
| **混語言**樹跨語言追 | **gtags** | 一個索引吃多語言 |
| 要**批次腳本查**（查 500 個函式各被誰呼叫） | **gtags**（`--result=grep`） | 格式最乾淨，接 awk/CI |

實務工作流是**兩層並存**：平常用 clangd（精準），它一卡（跳不動、references 空、開檔轉圈）就立刻退到 gtags——`gtags` 建個索引、`global -rx foo` 秒回所有引用。這對應 `reading_code` 的信條：**工具會卡，你要能分辨是哪一層卡了、並有備援**，而不是卡在「它為什麼不動」。

## 踩雷集錦

1. **以為 clangd attach 成功就萬事俱備**。attach ≠ 能精準跳。缺 `compile_commands.json` 時 clangd 照樣 attach、照樣給你 hover，但 `gd` 跳到錯的地方或跳不動。看到 `gd` 行為詭異，先確認 compile db 在不在（Ch 18/21），不在就別跟 clangd 死磕，退 gtags。

2. **在 kernel 上硬等 clangd**。kernel 沒先編過就沒有 `compile_commands.json`，clangd 對大半檔案是瞎的；就算生了也只涵蓋一種 config。在 kernel 子系統讀碼，**預設就用 gtags**，別浪費生命調 clangd。

3. **把 ctags 當萬能索引**。「我建了 tags，應該什麼都能查」——錯。ctags **只有定義**，沒有「誰呼叫」。想反查 caller 卻對著 ctags 找，是找一個它從來沒有的功能。反查是 cscope / gtags 的事。

4. **照抄 Vim 的 cscope 教學**。網路上大量「Neovim 設 `set cscopeprg=...` + `Ctrl-\ c` 查 caller」的教學是**過時的**——Neovim 已移除內建 `:cscope`。照抄會報 `E518: Unknown option: cscopeprg`。正確做法在 Ch 25。

5. **索引忘了更新**。tags/GTAGS 是快照。你 `git pull`、切 branch 後 code 變了，索引還是舊的，跳轉跳到錯行。因為重建只要毫秒級，裝 gutentags（Ch 24）或 git hook 自動重建，別手動記。

## 進階：再往深一層

- **兩層可以真的並存**：clangd 掛在 `gd`/`gr`（Part 4），gtags 掛在另一組鍵（`<leader>g*`，Ch 25）。同一個 buffer，clangd 能答就用它，答不出（跳空）就手一滑按 gtags 那組。Ch 28 會把這套「主武器 + 後備」的完整工作流串起來。

- **為什麼 tags 快到誇張**：ctags 的 `tags` 檔是**已排序**的純文字表，查詢用二分搜尋，所以秒回；gtags 用 Berkeley DB 存索引。它們不做語意分析（不建 AST、不解型別），省下的全是時間。這是「放棄精準換速度」的具體體現。

- **後備索引 + ripgrep 的分工**（回扣 Ch 10）：rg 找「任意文字 / log 訊息 / 跨語言字串」，tags 找「符號的定義與呼叫關係」。三層：rg 先粗定位 → gtags 反查呼叫鏈 → clangd（能編時）確認語意。讀陌生大樹就是這三層來回切。三者的界線很清楚：rg 不理解「符號」（`foo` 在註解裡、在字串裡、在呼叫裡它一視同仁），gtags 理解符號但不理解型別，clangd 全都理解但要能編。問題越「語意」，越往右邊的工具走；越「純文字 / 越大 / 越編不動」，越往左邊走。

- **索引可以只建你關心的子樹**：不必對整棵 monorepo 建索引。`find drivers/net -name '*.c' > gtags.files`（或 cscope 的 `cscope.files`）只索引你這次要讀的子系統，建得更快、查詢更乾淨、不被無關符號干擾。Practice E 就是只 sparse checkout + 索引 kernel 的 `net/ipv4`，不碰整棵 kernel。

- **`reading_code` 的橫向連結**：這一整個 Part 是 `reading_code` 第 14 章「離線索引三巨頭」在 Neovim 裡的**落地**。那章講原理與 CLI 對照（`readtags` / `cscope -L -N` / `global -x`），這個 Part 講怎麼把它們變成 Neovim 裡的按鍵與工作流。兩章對讀最有收穫。

## 本章重點整理

- clangd 精準但有前提：**要 compile_commands.json、要編得起來、只看一種 config**。這三個前提在 kernel / 大 monorepo / 編不起來的樹 / 多 config 樹上經常不成立。
- tags 系的優勢：**不用 build、不用理解語意、覆蓋全**——粗糙但穩，什麼樹都吃。
- 三工具定位：**ctags = 定義索引**（Neovim 原生跳定義）、**cscope = C 交叉引用**（caller/callee/字串，但 Neovim 內建已移除）、**gtags = 兩者兼具且好整合**（本 Part config 主力）。
- 決策原則：**編得起來 / 要同名分辨 → clangd；編不起來 / kernel 多 config / 超大 / 要腳本化 → gtags**。ctags 最輕、cscope caller 最精準。
- 實務是**兩層並存**，clangd 一卡就退 gtags，別跟卡住的工具死磕。

## 自我檢核

- [ ] 我能說出 clangd 精準的三個前提，以及各自在什麼真實場景會破功
- [ ] 我知道為什麼在「沒編過的 kernel 子系統」裡 clangd 幫不上，該用什麼
- [ ] 我能一句話講清 ctags / cscope / gtags 三者的核心分工，不會拿 ctags 找 caller
- [ ] 我知道「編不起來」跟「不能索引」是兩件獨立的事——tags 為什麼不在乎能否編譯
- [ ] 我知道照抄 Vim 的 `cscopeprg` 教學在 Neovim 會出什麼事（Ch 25 會驗）
- [ ] 給我一個處境（如「超大 monorepo 快查一個符號」），我能立刻說出該用哪個工具

## 延伸閱讀

### Neovim 內建 `:help`（優先）

- **`:help tags`**
  - **讀哪裡**：`tags-and-searches` 開頭那段，看 Neovim「原生」怎麼看待 tag 檔——這就是 Ch 24 `Ctrl-]` 背後的機制
  - **學什麼**：為什麼 tag 支援是編輯器核心功能、不需外掛；tagstack 的概念從哪來
- **`:help compile-commands`（對照 Part 4 的 `:help lsp`）**
  - **讀哪裡**：回頭看 Ch 17/18 談的 clangd 前提，跟本章「clangd 會跪」對讀
  - **學什麼**：確認「attach 成功 ≠ 能精準跳」這條界線畫在哪

### 本課與姊妹課

- **`soft_skills/reading_code` Ch 14「ctags / cscope / GNU global：離線索引三巨頭」**
  - **這是什麼**：本 Part 的原理與 CLI 對照母章，`readtags` / `cscope -L -N` / `global -x` 逐一真跑
  - **讀哪裡**：整章；特別是「同一問題三工具對照：誰呼叫 aeMain」那張表
- **本課 Ch 21「clangd 進階與 macro/ifdef 的坑」**
  - **這是什麼**：本章「clangd 會在哪跪」的技術細節版，看 `#ifdef` 具體怎麼讓 clangd 瞎掉
  - **讀哪裡**：macro/ifdef 那幾節

搞清楚「什麼時候退回 tags」之後，下一章我們動手：universal-ctags 建索引、Neovim 原生的 `Ctrl-]` / `Ctrl-t` tagstack 導航，以及用 gutentags 讓 tags 自動跟著 code 更新。

→ [Ch 24 ctags 與 tagstack](./24-ctags-tagstack.md)
