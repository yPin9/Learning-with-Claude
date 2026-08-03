# Ch 2 — Neovim 架構：Lua / LSP client / treesitter

> **目標**：掀開 Neovim 的引擎蓋。搞懂五塊——Lua 作為一等設定語言、內建 LSP client、內建 treesitter、RPC/remote API、event loop（libuv/vim.uv）——你才知道後面每個讀碼外掛「掛在哪、怎麼運作」，也才精通得了這台機器。這章是全課的架構地基。

> **環境**：Neovim v0.12.4，WSL2 / Ubuntu。本章的 headless Lua 示範全在此環境真跑，輸出如實貼上。

## 為什麼需要這個？

你可以完全不懂架構就照抄 config 按鍵。但這門課的目標是**精通**，而精通操作型工具有個規律：**當它壞掉時，你能不能修，取決於你懂不懂它內部怎麼運作。**

具體場景：`gd` 跳定義突然沒反應。可能是——clangd（LSP server）沒起來？Neovim 的 LSP client 沒 attach 到這個 buffer？treesitter 的高亮蓋掉了什麼？某個外掛的 async 任務卡死了 event loop？你若不知道這幾塊是分開的、各自負責什麼，你只能瞎猜、重開、換發行版。你若知道，你會 `:LspInfo` 看 client 狀態、`:checkhealth` 看哪塊缺依賴、`:Inspect` 看 treesitter，三分鐘定位。

還有一個更根本的理由：**Neovim 整個 Lua 外掛生態的爆發，是這個架構直接催生的。** 你後面要裝的每個外掛——telescope、treesitter textobjects、harpoon、fzf-lua——之所以存在、之所以能非阻塞地跑，全靠這章講的這幾塊。理解架構，你看外掛不再是黑魔法，是「喔它是掛在這個 API 上」。

## 先建立直覺：Neovim 是一個可程式化的 server

老 Vim 的心智模型是「一個文字編輯器」。Neovim 的心智模型要換成：**一個可程式化的編輯器 server，核心是 C 寫的，外面包一層 Lua，開放一組 API 讓程式（不管是內建 Lua 還是外部程序）來操作它。**

```
        ┌─────────────────────────────────────────────┐
        │                Neovim core (C)                │
        │   buffer / window / 編輯引擎 / event loop     │
        │                  (libuv)                      │
        └───────────────────┬───────────────────────────┘
                            │  暴露一組 API（msgpack-RPC）
          ┌─────────────────┼──────────────────┐
          ▼                 ▼                  ▼
   ┌────────────┐   ┌──────────────┐   ┌───────────────┐
   │ 內建 Lua    │   │ 外部程序      │   │ 內建子系統     │
   │ (你的       │   │ (GUI / IDE   │   │ - LSP client  │
   │  init.lua、 │   │  外掛、遠端   │   │ - treesitter  │
   │  外掛)      │   │  控制)       │   │  (都用 Lua 寫) │
   └────────────┘   └──────────────┘   └───────────────┘
```

三個心智模型的轉換，一次記住：

1. **設定不是「一堆指令」，是一份會執行的 Lua 程式**（Ch 0 講過，這裡補為什麼）。
2. **外掛不是「補丁」，是掛在 API 上的 Lua 程式**——它們透過 `vim.api.*` 操作 Neovim，就像外部程序透過 RPC 操作它一樣。
3. **Neovim 不是一個單執行緒卡死的編輯器**，它有 event loop（libuv），LSP 查詢、外部搜尋、外掛任務都能非阻塞地在背景跑。

這三點對讀碼機器的意義：你的機器是**可程式化、可擴充、不會因為背景跑 clangd 就卡住**的。以下逐塊拆。

## 一、Lua 作為一等設定語言：從 Vim 分家的歷史

**這是理解 Neovim 為什麼存在的起點。**

2014 年，Neovim 從 Vim fork 出來。原因不是「想換個名字」，是 Vim 的架構到了瓶頸：

- Vim 的設定與外掛語言是 **Vimscript**（`.vimrc` 裡那套 `set number` / `let g:foo = 1` / `function!`）。Vimscript 是為了「設定編輯器」長出來的語言，不是通用程式語言——它慢、語法怪、難組合、幾乎沒有生態系（沒有像樣的套件管理、測試、資料結構庫）。
- Vim 的核心是單一巨大的 C 程式碼庫，維護者 Bram Moolenaar 對貢獻把關極嚴，大改動（尤其是非同步、內嵌腳本語言）進度緩慢。

Neovim 的 fork 就是要打破這些：**重構核心、開放 API、把 Lua 扶正為一等設定語言。** Lua（更精確說是 LuaJIT）是個真正的通用程式語言——快、乾淨、可嵌入、有 closure 有 table 有 metatable。於是 Neovim 的設定變成 `init.lua`（Ch 0 的骨架），外掛可以用 Lua 寫，而 Lua 的生態能力讓外掛作者能寫出以前 Vimscript 寫不出的複雜工具。

> 這裡要精確：Vim 後來也追加了 Lua 綁定與非同步（Vim 8+），Neovim 不是唯一有 Lua 的。差別在**扶正的程度**——Neovim 把 Lua 當一等公民（`init.lua` 是官方推薦設定入口、核心子系統如 LSP client 和 treesitter 整合都用 Lua 寫、`vim.*` 這套 Lua API 是主要介面）。Vim 的 Lua 更像「附加綁定」。這個差別直接決定了兩邊生態的活力：Neovim 的 Lua 外掛生態這幾年爆發式成長，telescope、treesitter 整合這類東西幾乎都是 Neovim 專屬。

對你這個讀碼者的實際意義：**你的 config 是一份你能讀、能 debug、能組合的程式，不是一堆神秘咒語。** clangd 沒 attach？你可以在 config 裡加幾行 Lua 印出 `vim.lsp.get_clients()` 看狀態。這種「設定即程式」的可觀測性，是 Vimscript 時代做不到的。

## 二、內建 LSP client：Neovim 自己會說 LSP 協定

這塊是 Neovim 作為讀碼機器的**語意地基**，Part 4 會深挖，這裡先建立架構認知。

LSP（Language Server Protocol，語言伺服器協定）是微軟訂的一套協定：編輯器（client）和語言分析器（server，如 clangd）之間用 JSON-RPC 對話——client 問「游標下這個符號的定義在哪」，server 回一個檔名+行號。這套協定的意義是**解耦**：任何懂 LSP 的編輯器，可以接任何懂 LSP 的 server。

**關鍵事實：Neovim 內建一個 LSP client。** 也就是說，「說 LSP 協定」這件事是 Neovim 核心的一部分（`vim.lsp.*`），不是外掛。這跟很多人的印象相反——他們以為 `nvim-lspconfig` 這個外掛是 LSP 本體。不是。`nvim-lspconfig` 只是一包**設定範本**（告訴 Neovim 內建的 client「clangd 的執行檔叫什麼、要傳什麼參數、認哪些檔類型」），真正說協定、發請求、收回應、把定義位置塞進 jumplist 的，是 Neovim 內建的 client。

```
   你按 gd（跳定義）
        │
        ▼
   vim.lsp.buf.definition()   ← Neovim 內建 LSP client
        │  發 textDocument/definition 請求（JSON-RPC over stdio）
        ▼
   clangd（外部程序，語言 server）
        │  分析你的 C 專案，回傳定義的 檔案+行列
        ▼
   Neovim client 收到，跳過去（順便記進 jumplist，Ch 5）
```

這個架構對讀碼的意義：

- **接任何 language server**：clangd（C/C++）、pyright（Python）、rust-analyzer（Rust）、gopls（Go）……內建 client 是通用的，換語言只是換 server。你這台機器不綁死 C。
- **語意能力來自 server 不是 Neovim**：跳定義準不準是 clangd 的事（呼應 Ch 1 結尾）。Neovim 負責「說協定、把結果呈現到你的導航流」，clangd 負責「懂 C 的語意」。責任分清楚，才 debug 得對。
- **Neovim 0.11+ 給了 `vim.lsp.enable("clangd")` 這種一行啟用 API**（Ch 0 骨架用的就是它），比舊的手動 `setup()` 乾淨。這也是為什麼這門課要求新版 nvim——API 在 0.10→0.11→0.12 一直在演進。

## 三、內建 treesitter：語法感知的解析器

第二塊內建的重武器，Part 3 深挖，這裡建立架構認知。

treesitter 是一個**增量解析器函式庫**：它把你的原始碼解析成一棵**具體語法樹**（concrete syntax tree），而且是「增量」的——你改一行，它只重解析受影響的部分，快到可以在每次按鍵後即時更新。

Neovim 內建 treesitter 整合（`vim.treesitter.*`）。這跟老式的高亮不同：

- **老式高亮**：用正則表達式猜「這個字看起來像關鍵字、那個像字串」。正則不懂結構，所以在巢狀、巨集、跨行的場景常常猜錯。
- **treesitter 高亮**：真的把 code 解析成語法樹，知道「這個 `foo` 是**函式名**、那個 `foo` 是**變數**、這一段是 `if` 的**條件**」。它懂結構。

對讀碼的意義極大，因為讀碼很多動作是**結構性**的：

- 「選中整個函式」——treesitter 知道函式從哪到哪（Ch 14 textobjects）
- 「跳到下一個函式定義」——沿語法樹走，不是靠猜空行
- 「這個游標在哪個結構裡（哪個 if 的哪個 branch）」——查語法樹就知道（Ch 15 sticky context）
- 「找出所有 `switch` 語句」——結構化查詢（Ch 16 InspectTree）

```
   C 原始碼字串
        │
        ▼
   treesitter C parser（一個編譯好的 C parser，Ch 0 build 時裝的）
        │  解析成語法樹
        ▼
   translation_unit
     └─ function_definition
          ├─ primitive_type "int"
          ├─ function_declarator "add(int a, int b)"
          └─ compound_statement { ... }
```

有了這棵樹，Neovim 才能做「依結構」的高亮與導航——這是正則高亮永遠做不到的。

## 四、RPC / remote API：為什麼 Lua 生態會爆發

這塊最少人講，但它是**整個 Neovim 外掛生態爆發的底層原因**，值得單獨拆。

Neovim 核心暴露一組完整的 API（`vim.api.nvim_*`），透過 **msgpack-RPC** 協定開放。「RPC」的意思是：**任何程序都能透過這個協定遠端操作 Neovim**——建 buffer、塞內容、移游標、開視窗、讀任何狀態。Neovim 本質上是個 **server**，它把「操作編輯器」這件事變成一組可被程式呼叫的 API。

這件事的威力，分兩個層面：

**層面一：外掛用同一組 API 操作編輯器。** 你 config 裡的 `vim.api.nvim_buf_set_lines(...)`、外掛裡的 `vim.api.nvim_create_autocmd(...)`，用的是同一組核心 API。這表示外掛能做的事跟核心能做的事一樣多——沒有「這是內部功能外掛碰不到」的高牆。telescope 能開一個浮動視窗、即時過濾、預覽檔案，就是因為它能透過 API 完整操作 Neovim 的 buffer/window。**API 完整且開放 → 外掛能力上限高 → 生態能長出複雜工具。** 這是 Neovim 外掛比 Vim 強大的結構性原因。

**層面二：外部程序也能操作 Neovim。** GUI 前端（Neovide 等）、IDE 整合（VS Code 的 Neovim 外掛）、遠端腳本，都是透過同一組 RPC API 把 Neovim 當引擎用。**編輯核心與 UI 前端解耦**——這也是 fork 出來時的架構目標之一。

具體想像一下這個解耦：Neovim 核心不負責「怎麼把字畫到螢幕上」，它只負責「編輯邏輯 + 暴露狀態與 API」。畫面怎麼呈現，交給前端——可能是內建的終端 UI（你平常看到的那個），可能是 Neovide 這種帶動畫的 GPU GUI，也可能是 VS Code 把 Neovim 當一個嵌在裡面的編輯引擎。它們全部連到同一個核心、用同一組 RPC API 操作它。這就是為什麼「同一台 Neovim 可以有很多種長相」——長相是前端的事，編輯是核心的事。對讀碼者的實際意義：你的編輯邏輯與 config 不綁死在某個 UI 上，換前端（終端 → Neovide）你的操作肌肉、你的 config 全部照舊。

對你的實際意義：**「Neovim 是可程式化的」不是比喻，是字面事實。** 你可以寫一段 Lua 腳本，讓 headless（無 UI）的 nvim 開檔、解析、印出資訊——這正是這門課驗證 config 的方式，也是下一節要真跑給你看的東西。

## 五、event loop / async：為什麼外掛能非阻塞跑

最後一塊。Neovim 核心的 event loop 建在 **libuv**（Node.js 也用的那個跨平台非同步 I/O 函式庫）上，在 Lua 裡透過 `vim.uv`（舊名 `vim.loop`）暴露。

為什麼讀碼者要在乎？想像沒有它會怎樣：你 `gd` 跳定義，clangd 要分析半秒——如果是同步的，這半秒你整個編輯器**凍住**，不能捲動、不能打字、不能取消。大專案（kernel）clangd 索引要幾十秒，同步的話你就對著凍住的畫面乾等。

有了 event loop，這些**慢操作都在背景非阻塞跑**：

- clangd 在背景索引，你照樣讀別的檔，索引好了結果自己浮上來
- telescope 的 `live_grep` 背景跑 ripgrep，你打字它即時更新結果，UI 不卡
- 外掛的網路請求、檔案掃描、大量計算，都能丟給 event loop，不擋住你

```
   主執行緒（你在操作）           event loop（背景）
        │                            │
   按 gd ─────────────► 發 LSP 請求 ─┤
        │  UI 不凍，你繼續讀          │ clangd 在算…
        │                            │
        │  ◄──────── 結果回來，跳過去 ┘
```

這就是為什麼一台配好的 Neovim，跑著 clangd + treesitter + 一堆外掛，操作起來還是即時不卡。**event loop 是「重武器不拖累手感」的底層保證。** 你在 Ch 0 骨架裡看到的 `vim.uv.fs_stat(lazypath)`——那個 `vim.uv` 就是這個 libuv 介面。

## 五塊怎麼串成一次讀碼動作

分開講完五塊，把它們合起來看一次真實動作，你就懂「後面外掛掛在哪」了。情境：你在讀 kernel，游標停在 `kmalloc(size, GFP_KERNEL)` 上，按 `gd` 想跳進 `kmalloc` 定義。這一按，五塊全部參與：

```
   你按 gd
     │
   ①Lua：init.lua 裡 LspAttach 時綁的 keymap 觸發，呼叫一段 Lua
     │   (vim.lsp.buf.definition)
     ▼
   ②LSP client：Neovim 內建 client 組一個 textDocument/definition 請求
     │
   ③event loop：請求透過 libuv 非阻塞送給 clangd，你的 UI 不凍
     │   clangd 在背景查它的索引…
     ▼
   ④LSP client：收到回應（kmalloc 定義在 mm/slab_common.c 第幾行）
     │   跳過去，位置記進 jumplist
     ▼
   ⑤treesitter：新檔一載入，treesitter 立刻解析成語法樹
         → 語法感知高亮，你一眼看出哪是型別、哪是函式名、哪是巨集
```

RPC/remote API 沒在這條線上直接出現，因為它是**底層基礎**——上面每一步（Lua 呼叫、client 操作 buffer、跳轉、treesitter 更新 buffer 的高亮）用的都是那組核心 API。外掛做的事就是把這條線的某幾環包裝、強化：telescope 把「找檔/找符號」那環做成模糊 picker，treesitter textobjects 把「選中一個函式」那環做成一個按鍵，harpoon 把「標攻堅點跳回」那環做成快取清單。

**理解了這條線，你看任何讀碼外掛都知道它掛在五塊的哪一塊、強化哪一環。** 這就是為什麼要先讀這章再學外掛——不然外掛全是黑魔法。

## 底層機制：headless 真跑，證明「nvim 是可程式化的」

前面講「Neovim 是可程式化的 server」是字面事實。我們真跑一段給你看。

把下面存成 `arch_demo.lua`，用 headless 模式（`--headless -l`，不開 UI、純跑腳本）執行。這段腳本開一個 buffer、塞幾行 C code 進去、讀回來、並確認 libuv（`vim.uv`）在：

```lua
-- arch_demo.lua：示範 nvim 是可程式化的 server
print("nvim version: " .. vim.inspect(vim.version()))
vim.cmd("enew")
vim.api.nvim_buf_set_lines(0, 0, -1, false, {
  "int add(int a, int b) {",
  "    return a + b;",
  "}",
})
local n = vim.api.nvim_buf_line_count(0)
print("buffer line count: " .. n)
print("line 1: " .. vim.api.nvim_buf_get_lines(0, 0, 1, false)[1])
print("has vim.uv (libuv): " .. tostring(vim.uv ~= nil))
print("uv hrtime ns: " .. tostring(vim.uv.hrtime()))
vim.cmd("qa!")
```

真跑（`nvim --headless -l arch_demo.lua`）輸出：

```
nvim version: {
  api_compatible = 0,
  api_level = 14,
  api_prerelease = false,
  build = "v0.12.4",
  major = 0,
  minor = 12,
  patch = 4,
  ...
}
buffer line count: 3
line 1: int add(int a, int b) {
has vim.uv (libuv): true
uv hrtime ns: 2.2039480985638e+14
```

這證明了幾件事，全是本章的架構論點落地：

- **Lua 是一等設定語言**：整段是純 Lua，`vim.api.*` / `vim.cmd` / `vim.inspect` 直接可用。
- **Neovim 是可程式化的**：沒有 UI、沒有人按鍵，一段程式就開了 buffer、塞了 C code、讀了回來。這就是「編輯器是個可被程式操作的 server」的字面證據——外掛做的事，本質上就是這個，只是複雜得多。
- **event loop 在底層**：`vim.uv` 存在且能拿到 libuv 的高解析度時間（`hrtime`）。這個介面就是所有非阻塞外掛的地基。

再驗一塊：**內建 treesitter 真的會解析 C**。把下面存成 `ts_demo.lua`：

```lua
-- ts_demo.lua：nvim 內建 treesitter，語法感知
vim.cmd("enew")
vim.bo.filetype = "c"
vim.api.nvim_buf_set_lines(0, 0, -1, false, {
  "int add(int a, int b) {",
  "    return a + b;",
  "}",
})
local ok, parser = pcall(vim.treesitter.get_parser, 0, "c")
print("has builtin treesitter: " .. tostring(vim.treesitter ~= nil))
print("got c parser (ok): " .. tostring(ok))
if ok and parser then
  local tree = parser:parse()[1]
  print("root node type: " .. tree:root():type())
  print("root child count: " .. tree:root():child_count())
end
vim.cmd("qa!")
```

真跑輸出：

```
has builtin treesitter: true
got c parser (ok): true
root node type: translation_unit
root child count: 1
```

treesitter 把那三行 C 解析成一棵語法樹，root 是 `translation_unit`（C 的編譯單元），底下一個 child（那個 `add` 函式定義）。**這就是「內建 treesitter、語法感知」的字面證據**——不是正則猜色，是真的解析出結構。Part 3 會用這棵樹做結構化導航。

## Neovim vs Vim vs IDE：架構對照表

把五塊放進一張表，跟 Vim、傳統 IDE（VS Code / CLion）對比，你就看清 Neovim 的架構定位。

| 面向 | 傳統 Vim | **Neovim** | IDE（VS Code / CLion） |
|---|---|---|---|
| **設定語言** | Vimscript（附加 Lua 綁定） | **Lua 一等公民**（`init.lua`、核心用 Lua 寫） | JSON 設定 + JS/內部框架 |
| **LSP client** | 靠外掛（如 coc.nvim，跑在 node 上） | **內建**（`vim.lsp.*`，核心的一部分） | 內建 |
| **treesitter** | 無（靠正則高亮） | **內建**（`vim.treesitter.*`） | VS Code 內建；CLion 用自家引擎 |
| **remote API** | 有限（Vim 8 job/channel） | **完整 msgpack-RPC**，editor-as-server | 有各自的 extension host |
| **async/event loop** | Vim 8+ job control（後加、較弱） | **libuv 原生**（`vim.uv`），核心即非同步 | Node event loop（VS Code） |
| **擴充模型** | Vimscript/外部程序外掛 | **Lua 外掛掛在完整 API 上**，能力上限高 | 沙箱化 extension API |
| **家在哪** | 終端 | **終端**（+ RPC 給 GUI 前端） | GUI（VS Code 有 Remote，CLion 偏本機） |

讀這張表的重點：**Neovim 把 IDE 的核心能力（LSP、treesitter、async）內建進一個終端原生、Lua 可完全程式化的編輯器核心。** 它不是「IDE 的窮人版」，是另一種架構取捨——用可程式化與終端原生，換 IDE 那種開箱即用的打磨。對一個想**精通**讀碼操作、又活在終端/WSL/SSH 的人，這個取捨划算。而 Vim 在這張表上每一格都比 Neovim 弱一階或靠外部拼湊——這就是 2014 年那個 fork 的意義。

## 踩雷集錦

1. **以為 `nvim-lspconfig` 外掛就是 LSP 本體**：不是。LSP client 是 Neovim **內建**的（`vim.lsp.*`），`nvim-lspconfig` 只是一包告訴內建 client「clangd 怎麼啟動」的設定範本。搞錯這點，你 debug LSP 時會找錯地方——`gd` 沒反應該先 `:LspInfo` 看內建 client 的 attach 狀態，不是先怪那個外掛。
2. **以為 treesitter 是拿來寫程式的工具**：對讀者而言 treesitter 主要是**理解結構**的引擎（高亮、選函式、依結構導航），不是叫你去寫 parser。你享受它的成果，不用懂它的內部演算法（Part 3 只教怎麼用它導航）。
3. **同步思維**：以為 clangd 在索引時 nvim 一定卡住。不會——event loop 讓它背景跑。若你的 nvim 真的卡住不動，多半是某個外掛寫了同步的重操作擋住主執行緒，或 clangd 崩了在重試，不是「Neovim 就是會卡」。知道它「應該」非阻塞，你才會去查「為什麼這次卡了」。
4. **抄舊教材的 LSP 設定**：`vim.lsp` API 在 0.10→0.11→0.12 演進很快（`vim.lsp.enable` 是較新的）。網路上很多 LSP 設定教材是舊 API，抄了在新版可能報錯或行為不同。以你裝的版本 `:help lsp` 為準（Ch 0 的骨架用的是 0.11+ 的 `vim.lsp.enable`）。
5. **用 apt 的舊 Neovim 期待這些內建能力**：內建 LSP client、新版 treesitter 整合、`vim.uv`、`vim.lsp.enable` 都需要新版（0.10+/0.11+）。apt 常給 0.6/0.7，這些通通沒有或行為不同。用官方 tarball（Ch 0 講過）。
6. **把 treesitter 高亮出錯當成 Neovim 的錯**：某個檔高亮亂掉、顏色怪，多半是那個語言的 treesitter parser 版本跟你的 nvim 對不上，或該檔用了 parser 沒涵蓋的語法（很新的 C++ 特性、詭異巨集）。這是 parser 的問題不是核心的問題，`:checkhealth nvim-treesitter` 會告訴你 parser 狀態。搞清楚是哪一塊出錯，你才不會誤判成「Neovim 壞了」（Part 3 詳談）。
7. **以為 Vimscript 完全不能用了**：不是。Neovim 完全相容 Vimscript，你的舊 `.vim` 設定、用 Vimscript 寫的老外掛照樣跑（`:help vimscript`）。「扶正 Lua」是把 Lua 變成推薦與核心語言，不是廢掉 Vimscript。這對「想漸進遷移舊 Vim 設定」的人是好消息——可以 Lua、Vimscript 混用，慢慢搬。

## 進階：再往深一層

- **`:checkhealth` 是架構的體檢報告**：它一塊塊檢查——LSP 有沒有找到 server、treesitter parser 裝了沒、外掛缺不缺依賴、`vim.uv` provider 正不正常。理解本章五塊之後再看 `:checkhealth`，每一行你都知道它在檢查架構的哪一塊。config 出問題第一個跑它（Ch 29 詳談）。
- **`nvim --headless` 是這門課的驗證引擎**：本章兩段 demo 用的 `--headless -l script.lua` 不只是 demo——它是「editor-as-server」最純粹的用法（把 nvim 當一個能跑 Lua、能操作 buffer 的無頭引擎）。這門課每次驗證 config（外掛裝好沒、LSP attach 沒、treesitter 解析對不對）都靠它。你甚至能用它寫 CI 檢查你的 config 沒壞。
- **msgpack-RPC 可以自己戳**：進階玩法是 `nvim --listen /tmp/nvim.sock` 開一個 RPC socket，另一個程序（甚至另一個 nvim `--remote`）連上去操作它。你平常不會這樣用，但知道「Neovim 是個能被外部連上操作的 server」，你就理解了 GUI 前端、遠端控制、和很多外掛底層在做什麼。
- **Lua vs LuaJIT**：Neovim 內嵌的是 **LuaJIT**（`nvim --version` 那行 `LuaJIT 2.1.x`，本課環境是 `LuaJIT 2.1.1774638290`），不是標準 Lua。LuaJIT 快很多，但 API 對應 Lua 5.1。寫進階 config 時偶爾會碰到「這是 Lua 5.3 的語法，LuaJIT 沒有」的坑，記住它是 5.1 基底。
- **`api_level` 與版本相容**：本章 demo 印出的 `vim.version()` 裡有個 `api_level = 14`——這是 Neovim API 的版本號。外掛可以檢查它來決定「這個 nvim 夠不夠新、能不能用某個 API」。這也是為什麼舊教材的設定會在新版報錯或行為不同：API 一路演進，`api_level` 就是那把量尺。你 debug 外掛相容性問題時，這個數字是線索。

## 本章重點整理

- **Neovim 是一個可程式化的 server**：C 核心 + Lua 層 + 開放 API（msgpack-RPC），不是「一個文字編輯器」。
- **Lua 是一等設定語言**：2014 從 Vim fork 出來的核心動機之一，就是擺脫 Vimscript、扶正 Lua——這直接催生了 Lua 外掛生態的爆發。
- **內建 LSP client**（`vim.lsp.*`）：Neovim 自己會說 LSP 協定，所以能接 clangd/任何 language server；`nvim-lspconfig` 只是設定範本，不是本體（Part 4）。
- **內建 treesitter**（`vim.treesitter.*`）：把 code 解析成語法樹、語法感知，讀碼的結構性導航靠它（Part 3）。
- **RPC / remote API**：完整開放的 API 是 Lua 生態爆發的底層原因——外掛與外部程序用同一組 API 操作編輯器，能力上限高。
- **event loop / async**（libuv / `vim.uv`）：慢操作（clangd 索引、telescope 搜尋）背景非阻塞跑，重武器不拖累手感。
- 五塊合起來的定位：**把 IDE 的核心能力內建進一個終端原生、可完全程式化的核心**——這是它相對 Vim 全面領先、相對 IDE 另闢蹊徑的架構取捨。

## 自我檢核

- [ ] 我能畫出「Neovim 是可程式化 server」的架構圖：C 核心 + Lua 層 + 開放 API，三種東西（內建 Lua / 外部程序 / 內建子系統）都透過 API 操作它
- [ ] 我能說出 2014 年從 Vim fork 出來的核心動機，以及「扶正 Lua」為什麼重要
- [ ] 我能解釋「LSP client 是 Neovim 內建的，`nvim-lspconfig` 只是設定範本」，並知道 `gd` 壞掉先查哪裡
- [ ] 我能說出內建 treesitter 對讀碼的意義（語法感知、結構性導航），以及它跟正則高亮的差別
- [ ] 我能解釋 RPC/remote API 為什麼是 Lua 外掛生態爆發的底層原因
- [ ] 我能解釋 event loop / `vim.uv` 為什麼讓「clangd 背景索引時 nvim 不卡」
- [ ] 我能講出 Neovim vs Vim vs IDE 在「設定語言 / LSP / treesitter / async / 擴充模型」上的差異
- [ ] 我能描述一次 `gd` 跳定義用到五塊中的哪幾塊、順序是什麼
- [ ] 我知道 `nvim --headless -l script.lua` 是「editor-as-server」的用法，也是這門課驗證 config 的引擎

## 延伸閱讀

### 官方文件（優先）

- **[Neovim `:help api`](https://neovim.io/doc/user/api.html)**
  - **讀哪裡**：開頭的 API overview 與 `nvim_buf_*` / `nvim_win_*` 那幾組；本章 demo 用的 `nvim_buf_set_lines` / `nvim_buf_get_lines` 都在這
  - **學什麼**：「editor-as-server」的 API 長什麼樣——理解外掛是掛在這組 API 上的
- **[Neovim `:help vim.uv`](https://neovim.io/doc/user/luvref.html)**（或 `:help luv`）
  - **讀哪裡**：overview 段落，感受它是 libuv 的 Lua 綁定
  - **學什麼**：event loop / 非同步的入口，為什麼外掛能非阻塞跑
- **[Neovim `:help treesitter`](https://neovim.io/doc/user/treesitter.html)**
  - **讀哪裡**：開頭的 overview（Part 3 會回來深挖）
  - **學什麼**：內建 treesitter 的定位——語法樹、增量解析、與高亮/導航的關係

### 背景 / 歷史

- **[Neovim `:help vim-differences`](https://neovim.io/doc/user/vim_diff.html)**
  - **讀哪裡**：Lua、LSP、treesitter、job/async 相關的差異段落
  - **學什麼**：Neovim 相對 Vim 在架構上多了什麼——本章對照表的官方版
- **[Neovim charter / 專案首頁](https://neovim.io/)**
  - **讀什麼**：專案的目標宣言（可程式化、解耦 UI、擁抱現代擴充）——理解 2014 fork 的動機與方向

理解了架構，你知道後面每個外掛掛在哪、怎麼運作了。接下來進 Part 1，回到最基礎也最核心的一塊——**移動**。Ch 1 說讀碼是導航密集的活、Neovim 的殺傷力在導航；Part 1 就把那套 motion 肌肉一塊塊長出來，從模式與基本移動開始。

→ [Ch 3 模式與基本移動](./03-modes-and-basic-motion.md)
