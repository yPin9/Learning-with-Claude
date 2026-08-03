# Ch 9 — telescope 核心

> **目標**：把 telescope 從「一個會跳出來的模糊搜尋框」升級成你理解的一台機器。學完你知道它是 **picker + finder + sorter + previewer** 四件套組成的框架、掌握讀碼最常用的六個 picker（`find_files` / `live_grep` / `buffers` / `grep_string` / `oldfiles` / `resume`）、記熟 picker 內的鍵位（選、開、分割開、送 quickfix、多選）、並把 C 編譯的排序器 `telescope-fzf-native` 加進 config 讓大 repo 不再頓。

> **環境**：Neovim v0.12.4，WSL2 / Ubuntu，telescope `0.1.x` + plenary + telescope-fzf-native。本章 headless 驗證的輸出都是在此環境真跑出來的；picker 是互動 UI，截不了圖，以逐鍵操作描述，底層命令已 headless 驗證存在。

## 為什麼需要這個？

Ch 0 的 config 已經給了三個 telescope 鍵位（`<leader>ff` 找檔、`<leader>fg` 全文、`<leader>fs` 符號），你大概已經按過覺得「還不錯」。但那只是冰山一角，而且你不知道它底下在幹嘛。

讀大型 C 專案時你的第一個動作幾乎永遠是「找」：這個檔在哪、這個函式在哪定義、剛剛那個 struct 叫什麼、我上禮拜開過的那個檔叫什麼。傳統做法是 `:e src/very/long/path.c`（要記得完整路徑）或 `:grep`（結果一坨、要自己翻）。這些在 5 個檔的專案能忍，在 kernel 那種上萬檔的專案會把你的思路磨光。

telescope 的價值不在「有個搜尋框」——那誰都有。價值在**它把「找」這件事變成一個統一的、可組合的介面**：找檔、找字、找 buffer、找符號、找 git commit，全部同一套 UI、同一組鍵位、同一種模糊過濾邏輯。你學一次操作，套用到所有「找」的場景。這是 reading_code 講的「偵察」在 nvim 裡的主要載體。

但預設的 telescope 在超大結果集會頓——因為它的排序器是純 Lua 寫的。所以本章也把 C 編譯的 `fzf-native` 排序器裝上，這是讓 telescope 在真實大 repo 能用的關鍵一步。

## 先建立直覺：picker + finder + sorter + previewer 四件套

telescope 不是一個黑箱搜尋框，它是一個**框架**。每次你開一個 picker，四個零件協作：

```
   你打字 "newstate"
        │
        ▼
   ┌─────────────┐   finder 產生候選（跑 rg / fd / 讀 buffer 列表…）
   │   finder    │ ──────────────┐
   └─────────────┘               │  一長串候選項目
                                 ▼
   ┌─────────────┐   sorter 依你打的字算「多像」，重排 + 篩掉不像的
   │   sorter    │ ──────────────┐
   └─────────────┘               │  排好序的前幾名
                                 ▼
   ┌─────────────┐   picker 是整個視窗（結果列 + prompt 輸入列 + 選取狀態）
   │   picker    │ ← 你在這裡用 <C-n>/<C-p> 上下選
   └─────────────┘               │  你選中的那一項
                                 ▼
   ┌─────────────┐   previewer 即時預覽選中項（檔案內容 / grep 命中行的周圍）
   │  previewer  │
   └─────────────┘
```

- **finder**：候選來源。`find_files` 的 finder 跑 `fd`（或 `rg --files`）列出所有檔；`live_grep` 的 finder 每次你改字就重跑一次 `rg`；`buffers` 的 finder 讀 nvim 的 buffer 列表。**finder 決定「候選是什麼」**。
- **sorter**：你打字後，把候選依「有多符合」重新排序並篩掉太不像的。預設是 Lua 的 fuzzy sorter；換成 `fzf-native` 後由 C 算，快好幾個數量級。**sorter 決定「排序多快多準」**。
- **picker**：把上面兩者組成一個可互動的浮動視窗，處理你的按鍵。你按 `<CR>` 開檔、`<C-v>` 垂直分割開、`<Tab>` 多選——都是 picker 在管。
- **previewer**：右側那塊即時預覽。選到一個檔就顯示內容，選到一個 grep 命中就跳到那行並高亮。讀碼時 previewer 極重要——**你常常不用開檔，光看預覽就確認「是不是這個」**。

理解這個分工的實際好處：當 telescope 出問題，你能定位是哪個零件。「找不到檔」是 finder 問題（fd 沒裝？在錯的目錄？）；「打字沒反應/很慢」是 sorter 問題（沒裝 fzf-native？結果集太大？）；「預覽是空的」是 previewer 問題（二進位檔？檔太大被跳過？）。跟 Ch 0 講的「工具卡住時你要 debug 得了工具」同一個道理。

## 核心 picker：讀碼最常用的六個

### `find_files`——依檔名找檔（`<leader>ff`）

finder 列出專案裡所有檔，你打字模糊過濾。讀碼情境：你知道大概叫什麼（`t_string`、`server`、`main`）但懶得記完整路徑。

逐鍵操作（**互動 UI 無法貼截圖，以下為鍵位操作；底層 `:Telescope find_files` 命令已 headless 驗證註冊成功**）：

1. 按 `<leader>ff`，跳出 picker，游標在下方 prompt 列（insert 模式）。
2. 打 `tstr`——模糊過濾，`t_string.c` 浮到最上（fuzzy：字元順序對就算命中，不用連續）。
3. `<C-n>` / `<C-p>` 上下移動選取，右側 previewer 即時顯示該檔開頭。
4. `<CR>` 開在當前 window。

> **模糊過濾的心智模型**：打 `tstr` 能命中 `t_string.c`，是因為 fuzzy sorter 找 `t`、`s`、`t`、`r` 這幾個字元**按順序**出現即可。所以你不用打連續子字串，打「特徵字母」就好——這是 telescope 比 `:find` 快的核心。

### `live_grep`——全文即時搜（`<leader>fg`）

finder 每次你改字就重跑 `rg`，即時全文搜整個專案。讀碼情境：你不知道檔名，但知道會出現的字串——error message、函式名、某個 magic constant。**這是讀碼用得最兇的 picker**，Ch 10 整章講它底層的 rg。

逐鍵：按 `<leader>fg`，打 `luaD_throw`，結果列即時列出所有出現該字的行（檔:行:內容），`<C-n>` 選，previewer 顯示命中行周圍，`<CR>` 跳過去。

`live_grep` 與 `find_files` 的差別要記牢：**`find_files` 搜檔名，`live_grep` 搜檔案內容**。找「叫 parser 的檔」用前者，找「哪裡呼叫 parse()」用後者。

### `buffers`——切換已開的 buffer（`<leader>fb`）

finder 是你當前開著的 buffer 列表。讀碼情境：你追一條 call chain 開了七八個檔，要跳回三個檔前那個。比 `:bnext` 一個個翻快得多，直接打檔名片段跳。

逐鍵：`<leader>fb`，打你要回去那個檔的片段，`<CR>` 切過去。多檔對照讀（Ch 7 那套 window/buffer）時，這是你在 buffer 之間傳送的主要方式。

### `grep_string`——搜游標下的字（`<leader>fw`）

finder 跑 `rg`，但 pattern 是**游標當前所在的字**（或 visual 選取）。讀碼情境：游標停在一個函式名 `luaD_throw` 上，想立刻看「這東西還在哪出現」，不用手打。

逐鍵：游標移到某識別字上（`w`/`b` 移動或直接停在上面），按 `<leader>fw`，picker 立刻列出該字在全專案的所有出現。這是 `*`（Ch 6 搜當前字，但只在本檔）的跨檔全專案版。**當 clangd 的 `gr`（找 reference）因為缺 compile_commands 而不準時，`grep_string` 是純文字的後備**。

### `oldfiles`——最近開過的檔（`<leader>fo`）

finder 是 nvim 記錄的「最近開過的檔」（跨 session，存在 shada 檔裡）。讀碼情境：昨天讀到一半，今天想接著讀那幾個檔——不用記路徑，`<leader>fo` 打個片段就回去。

### `resume`——回到上次的搜尋（`<leader>fr`）

**這個被嚴重低估**。`resume` 重開你上一次的 picker，連 prompt 打的字、選到第幾項都還原。讀碼情境：你 `live_grep` 找到十個命中，`<CR>` 開了第一個看，看完想回去看第二個——不用重打，`<leader>fr` 直接回到剛才那個結果列、停在你離開的位置。追一串命中時省下大量重複打字。

### `lsp_*`——符號導航（Part 4 深用）

`lsp_references`、`lsp_document_symbols`、`lsp_workspace_symbols` 這些的 finder 來自 LSP（clangd），是**語意**級的（懂型別、懂作用域，不會被同名字串騙）。Ch 0 已掛了 `<leader>fs`（workspace symbols）。這些要 clangd attach 才有料，留到 Part 4 講。本章先知道「telescope 也是 LSP 結果的檢視器」。

## picker 內鍵位：進到 picker 之後怎麼操作

picker 開著時你在一個特殊模式（預設 insert，打字即過濾），這組鍵位是操作核心，**每個都要按到變反射**：

- `<C-n>` / `<C-p>`：下一個 / 上一個候選（也可用方向鍵，但別）。
- `<CR>`：在當前 window 開選中項。
- `<C-x>`：**水平**分割開（split，上下）。讀碼情境：想邊看當前檔邊開命中，不覆蓋當前。
- `<C-v>`：**垂直**分割開（vsplit，左右）。對照兩個檔常用。
- `<C-t>`：在新 tab 開。
- `<Tab>`：**多選**（toggle 選取當前項並下移），選中的項打勾。搭配 `<C-q>` 一次把多個送進 quickfix。
- `<C-q>`：把**所有**結果送進 quickfix list 並開啟——這是 Part 2 收斂到 Ch 12 的關鍵動作，把一次搜尋變成一份可逐項導航的工作清單。
- `<C-u>` / `<C-d>`：previewer 內容上 / 下捲（命中在長檔中間時看更多上下文）。
- `<Esc>`（或 `<C-c>`）：關掉 picker。
- `<C-space>`：live_grep 專用——在當前結果上**再加一層 grep 過濾**（Ch 10 詳談）。

> **`<C-q>` 是 Part 2 的樞紐**：`live_grep` 找到「某函式的所有 caller」十個命中後，`<C-q>` 把這十個全塞進 quickfix，接著 `:cnext` 一個個看過去。搜尋（Ch 9–11）+ quickfix（Ch 12）合起來才是完整的「組織一次攻堅」。

## 把 fzf-native 加進 config：讓大 repo 不頓

telescope 預設的 sorter 是純 Lua。在小專案感覺不出來，但在 kernel（幾萬個檔、`find_files` 候選上萬）打字過濾會明顯卡頓——Lua 對每個候選算 fuzzy 分數，量一大就慢。

`telescope-fzf-native` 是把 fzf 的排序演算法用 **C** 重寫、編成 `.so` 給 telescope 呼叫的 sorter。同樣的過濾，C 版快到你感覺不到延遲。**這是讓 telescope 在真實大 repo 能用的必要外掛，不是可有可無的優化。**

往 Ch 0 的 config 加。原本 telescope 那塊的 `dependencies` 只有 plenary，我們補上 fzf-native（`build = "make"` 觸發編譯），並在 `config` 裡 `setup` telescope、load fzf extension、順手把更多 picker 鍵位與 `<C-q>` 送 quickfix 掛上：

```lua
{ "nvim-telescope/telescope.nvim", branch = "0.1.x",
  dependencies = {
    "nvim-lua/plenary.nvim",
    -- fzf-native：C 編譯的排序器，大 repo 過濾不頓（build 觸發 make）
    { "nvim-telescope/telescope-fzf-native.nvim", build = "make" },
  },
  config = function()
    local telescope = require("telescope")
    local actions = require("telescope.actions")
    telescope.setup({
      defaults = {
        mappings = {
          i = {
            -- 把整份結果送進 quickfix 並開啟（Ch 12 的樞紐動作）
            ["<C-q>"] = actions.send_to_qflist + actions.open_qflist,
          },
        },
      },
      extensions = {
        fzf = { fuzzy = true, override_generic_sorter = true, override_file_sorter = true },
      },
    })
    pcall(telescope.load_extension, "fzf")  -- 沒編成功也不讓 config 掛掉
  end,
  keys = {
    { "<leader>ff", "<cmd>Telescope find_files<cr>" },  -- 找檔名
    { "<leader>fg", "<cmd>Telescope live_grep<cr>" },   -- 全文即時搜
    { "<leader>fb", "<cmd>Telescope buffers<cr>" },     -- 切 buffer
    { "<leader>fw", "<cmd>Telescope grep_string<cr>" }, -- 搜游標下的字
    { "<leader>fo", "<cmd>Telescope oldfiles<cr>" },    -- 最近開過的檔
    { "<leader>fr", "<cmd>Telescope resume<cr>" },      -- 回上次搜尋
    { "<leader>fs", "<cmd>Telescope lsp_workspace_symbols<cr>" }, -- 符號（Part 4）
  } },
```

> `build = "make"` 需要系統有 C 編譯器（`gcc`/`cc` + `make`）。第一次 `:Lazy sync` 時 lazy 會跑 `make` 編出 `libfzf.so`。編不出來（缺 compiler）時 `pcall(load_extension)` 讓 config 照樣載入，只是退回 Lua sorter——`pcall` 這層防護是刻意的，別省。

### headless 驗證：外掛裝好 + fzf 編譯 + 命令與 picker 註冊

用隔離 XDG 目錄跑 `:Lazy! sync`，確認 fzf-native 裝進來且 `.so` 編出來：

```
$ ls $XDG_DATA_HOME/nvim/lazy/
lazy.nvim  nvim-lspconfig  nvim-treesitter  plenary.nvim
telescope-fzf-native.nvim  telescope.nvim

$ find .../telescope-fzf-native.nvim/build -name '*.so'
.../telescope-fzf-native.nvim/build/libfzf.so
```

`libfzf.so` 存在 = C 排序器編譯成功。再用 headless 腳本確認 Telescope 命令註冊、六個 picker 都在、fzf extension 載入（真跑輸出）：

```
Telescope command registered (2=yes): 2
telescope.builtin loaded: true
  picker find_files: function
  picker live_grep: function
  picker buffers: function
  picker grep_string: function
  picker oldfiles: function
  picker resume: function
  picker lsp_references: function
fzf extension loaded: true
```

`Telescope` ex-command 註冊成功（`exists(':Telescope')` 回 2）、六個核心 picker 都是可呼叫的 function、`fzf` extension 載入成功（C sorter 生效）。**picker 本身是互動浮動視窗、headless 開不出來截不了圖，但「命令與 picker 存在、fzf 排序器已載入」這些底層事實已驗證。**

## 鍵位表

| 模式 | 按鍵 | 作用 |
|---|---|---|
| Normal | `<leader>ff` | `find_files`：依檔名模糊找檔 |
| Normal | `<leader>fg` | `live_grep`：全文即時搜（底層 rg） |
| Normal | `<leader>fb` | `buffers`：切換已開 buffer |
| Normal | `<leader>fw` | `grep_string`：搜游標下的字 / visual 選取 |
| Normal | `<leader>fo` | `oldfiles`：最近開過的檔（跨 session） |
| Normal | `<leader>fr` | `resume`：回到上一次的 picker（含輸入與位置） |
| Normal | `<leader>fs` | `lsp_workspace_symbols`：全專案符號（需 clangd） |
| picker (insert) | `<C-n>` / `<C-p>` | 下一個 / 上一個候選 |
| picker | `<CR>` | 在當前 window 開選中項 |
| picker | `<C-x>` | 水平分割（split）開 |
| picker | `<C-v>` | 垂直分割（vsplit）開 |
| picker | `<C-t>` | 新 tab 開 |
| picker | `<Tab>` | 多選（toggle 當前項並下移） |
| picker | `<C-q>` | 全部結果送 quickfix 並開啟 |
| picker | `<C-u>` / `<C-d>` | previewer 內容上 / 下捲 |
| picker | `<C-space>` | live_grep：在結果上再加一層過濾 |
| picker | `<Esc>` / `<C-c>` | 關閉 picker |

## 對比與取捨

| 場景 | 用哪個 picker | 別用 |
|---|---|---|
| 知道檔名片段 | `find_files` | `live_grep`（搜內容不是搜名） |
| 知道會出現的字串 | `live_grep` | `find_files`（搜不到內容） |
| 游標已停在目標字上 | `grep_string`（免打字） | `live_grep`（還要手打） |
| 回到剛才追的檔 | `buffers` | `find_files`（buffer 已開，直接切更快） |
| 昨天讀的檔 | `oldfiles` | 靠記憶硬打路徑 |
| 語意精準找 reference | `lsp_references`（Part 4） | `grep_string`（會被同名字串騙） |

| sorter | 語言 | 大 repo 表現 | 何時用 |
|---|---|---|---|
| **telescope 內建 fuzzy** | Lua | 上萬候選會頓 | 沒 compiler 時的退路 |
| **fzf-native** | C | 快到無感 | 預設就該裝，本章加它 |

## 踩雷集錦

1. **`find_files` 找不到某些檔**：telescope 的 `find_files` 預設**尊重 `.gitignore`**（底層 fd/rg 的行為）。build 產物、`.git/`、被 gitignore 的檔預設不列。這通常是好事（不被雜訊淹），但你要找一個被 ignore 的產生檔（如 `compile_commands.json` 有時被 ignore）就會「找不到」——需要時開 `hidden = true` 或用 `find_files` 的 `no_ignore`。
2. **打字很頓 = 沒裝 fzf-native**：如果在大 repo 打字明顯延遲，先確認 `libfzf.so` 有編出來、`load_extension("fzf")` 有成功。`build = "make"` 需要 compiler，容器裡常缺，`:Lazy build telescope-fzf-native.nvim` 重編一次看報什麼錯。
3. **`live_grep` 完全沒結果**：telescope 的 `live_grep` 底層要 `rg`（ripgrep）。沒裝 rg 就整個 picker 空的。`:checkhealth telescope` 會告訴你缺哪個外部工具（Ch 10 詳談 rg）。
4. **`<CR>` 開錯 window / 蓋掉重要 buffer**：`<CR>` 在當前 window 開。如果你想保留當前檔，該用 `<C-v>`（垂直分割）或 `<C-x>`（水平分割）開，而不是 `<CR>` 覆蓋。這個習慣讀碼對照時很重要。
5. **`grep_string` 搜到一坨無關的**：它搜的是游標下**整個字**當純文字，`i`（單字母變數）這種會炸出上千結果。短名、常見字用 `grep_string` 前先想想，或改用語意的 `lsp_references`。

## 進階：再往深一層

- **自訂 picker 的 finder**：telescope 的 `pickers` 可以傳自訂命令。例如做一個「只搜 header 檔的 live_grep」：把 `additional_args` 設成 `{'--glob', '*.h'}`（Ch 10 詳談 additional_args）。這是把「圈範圍」焊進一個專用 picker。
- **`:Telescope` 直接帶參數**：picker 可從命令列帶初始查詢，如 `:Telescope live_grep default_text=luaD_throw`。寫進更複雜的 keymap 或指令時有用。
- **normal 模式操作 picker**：在 picker 裡按 `<Esc>` 進 normal 模式（不是關閉，要看 config），此時可以用 vim motion 在**結果列**上移動、`gg`/`G` 跳頭尾。結果很多時比一直 `<C-n>` 快。
- **previewer 的效能**：超大檔（幾 MB 的產生檔）previewer 會慢或跳過。telescope 有 `preview.filesize_limit` 之類設定，讀碼很少碰但知道有這回事。

## 本章重點整理

- telescope 是 **finder + sorter + picker + previewer** 四件套的框架，不是單一搜尋框；出問題時能定位是哪個零件。
- 讀碼核心六 picker：`find_files`（找檔名）、`live_grep`（找內容，最常用）、`buffers`（切已開）、`grep_string`（搜游標下的字）、`oldfiles`（最近檔）、`resume`（回上次搜尋，被低估）。
- picker 內鍵位是操作核心：`<C-n>/<C-p>` 選、`<CR>`/`<C-x>`/`<C-v>` 開、`<Tab>` 多選、`<C-q>` 送 quickfix。
- **`fzf-native` 是必裝**——C 排序器讓大 repo 過濾不頓；`build = "make"` 編出 `libfzf.so`，headless 已驗證載入。
- `find_files` 搜檔名、`live_grep` 搜內容，別搞混。

## 自我檢核

- [ ] 我能說出 finder / sorter / picker / previewer 各管什麼，並用它們定位「telescope 卡住」是哪個零件的問題
- [ ] 我知道 `find_files` 和 `live_grep` 的根本差別，能對「找叫 X 的檔」和「哪裡用了 X」分別選對
- [ ] 我不用查就能按出：picker 內垂直分割開、多選、送 quickfix、回上次搜尋
- [ ] 我知道 fzf-native 是什麼、為什麼大 repo 必裝、怎麼確認它編出來了
- [ ] 我知道 `grep_string` 是純文字、會被同名騙，語意精準要退到 `lsp_references`

## 延伸閱讀

- **Neovim `:help telescope`（外掛內建 doc，`:h telescope.nvim`）**
  - **讀哪裡**：`telescope.builtin` 那節列出**所有** picker；先掃過知道有哪些「找」的場景，讀碼會用到的遠不只本章六個
  - **注意**：這是外掛裝好後才有的 help，`:Telescope` 能用就查得到
- **[telescope.nvim README + wiki](https://github.com/nvim-telescope/telescope.nvim)**
  - **讀哪裡**：`Default Mappings` 表（picker 內全部鍵位，本章挑了讀碼常用的）、`Customization` 那段（怎麼改 mappings 與 defaults）
- **[telescope-fzf-native.nvim README](https://github.com/nvim-telescope/telescope-fzf-native.nvim)**
  - **讀哪裡**：`Installation` 的 `build` 那段（`make` vs `cmake`）與 `fuzzy` 語法（`'` 前綴精確、`!` 排除等 fzf 過濾語法，picker 裡也能用）
- **對照本課**：Ch 0 config 骨架（telescope 已在裡面，本章往它加 fzf-native 與更多 picker）；`reading_code` Ch 12「grep/ripgrep 的藝術」是 `live_grep` 底層那把 rg 的完整版

telescope 讓你「找得到」，但它的 `live_grep` 底層那台引擎——ripgrep——本身就是一門學問。下一章我們鑽進去：rg 為什麼快、大 repo 的搜尋策略怎麼定、怎麼把 rg 旗標傳給 telescope、以及什麼時候該跳出 nvim 直接在 shell 用 rg。

→ [Ch 10 ripgrep 深度整合](./10-ripgrep-integration.md)
