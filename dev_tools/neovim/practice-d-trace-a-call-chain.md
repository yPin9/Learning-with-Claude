# 練習 D — clangd 追一條 call chain

> **目標**：把 Part 4 學到的語意導航（Ch 17–22）拼起來，在一個**真專案**裡用 clangd 追一條完整的 call chain——從對外 API 入口，一路追到底層核心函式。全程用 `gd`/`gr`/hover/call hierarchy，靠 jumplist（`<C-o>`/`<C-i>`）在檔案間來回，不碰滑鼠、不靠文字猜。這是「懂語意」從按鍵變成攻堅肌肉的一次實戰。

> **環境**：Neovim v0.12.4，WSL2 / Ubuntu，clangd 14、bear。靶：**Lua 5.4.6**（`git clone` + `make` + bear 生 compile_commands.json）。參考解答裡的定義/引用位置，都是 headless 對這份 Lua 跑 clangd-14 照抄的真實輸出——你在自己機器上跳到的位置應該一致（同一個 tag）。

## 為什麼是這個練習？

讀一個陌生大 C 專案，最常見的攻堅動線就是「call chain 追蹤」：你從一個**你懂的入口**（一個對外 API、一個你在文件看過的函式）出發，想搞懂「它底下到底做了什麼」——它呼叫誰、那個又呼叫誰，一路追到真正幹活的核心。或者反過來：你在底層看到一個關鍵函式，想知道「它最終是被哪個公開入口觸發的」，往上溯源。

這正是 `reading_code` 追 data flow / 溯源控制流的核心。文字工具（grep）做得到但很痛——每一跳都撈一堆同名雜訊要你自己過濾。clangd 讓每一跳精準：`gd` 給唯一定義、call hierarchy 給完整 caller 樹。這個練習讓你把這條動線走順。

## 靶：Lua 的 `lua_pcall` → 底層執行

Lua 是理想的靶——夠真實（一個完整的語言直譯器）、夠小（35 個 C 檔能秒 build）、架構清晰（對外 API 在 `lapi.c`、執行核心在 `ldo.c`/`lvm.c`）。

我們追的 call chain：**`lua_pcall`（宿主程式呼叫 Lua 函式的公開 API）一路到底層的 `luaD_call`（真正切換到被呼叫函式的核心）**。這條鏈跨好幾個檔，中間有 macro、有函式指標——正好考驗 clangd 的能與不能。

## 分段任務

### Step 0：把靶 build 起來、生 compile_commands.json

沒有 CDB，clangd 就是單檔模式，這個練習做不了（Ch 18）。先把地基打好：

```bash
cd /tmp
git clone --depth 1 --branch v5.4.6 https://github.com/lua/lua.git
cd lua
bear -- make            # 攔截 make，生 compile_commands.json
ls compile_commands.json   # 確認生出來了
```

> Lua 官方 git repo 的 makefile 直接 `make` 就能編（Linux 上會用 `gcc ... -DLUA_USE_LINUX`）。若你的機器缺 `readline`，`make` 可能抱怨——裝 `libreadline-dev` 或改用 release tarball（`make linux` / `make posix`）。只要 `bear -- make` 能跑完、生出 CDB 即可。

驗證 clangd 吃到 CDB：

```bash
clangd --check=lapi.c 2>&1 | grep -i "compilation database"
# 期望看到：Loaded compilation database from /tmp/lua/compile_commands.json
```

用你的隔離 config 開 nvim（別動主 config）：

```bash
export XDG_CONFIG_HOME=/tmp/nvim_part4/config XDG_DATA_HOME=/tmp/nvim_part4/data
/opt/nvim-linux-x86_64/bin/nvim lapi.c
```

打開 `lapi.c` 後，`:checkhealth vim.lsp`（或 `:lua =#vim.lsp.get_clients()`）確認 clangd attach 了。等狀態列的 indexing 跑完（背景索引，Ch 21）——不然跨檔 `gr` 會漏。

### Step 1：從入口 `lua_pcall` 出發

`lua_pcall` 是宿主程式（如 `lua` 命令列直譯器本身）呼叫一個 Lua 函式、並捕捉錯誤的公開 API。任務：**找到它的真正實作**。

- 用 `<leader>ws`（workspace symbol）搜 `lua_pcall`，或在任何用到它的地方 `gd`。
- 你會發現 `lua_pcall` 其實是個 **macro**（定義在 `lua.h`），展開成 `lua_pcallk`。這是第一個考點——追鏈遇到 macro 別慌。

### Step 2：追進 `lua_pcallk`，找它呼叫的核心

跳到 `lua_pcallk` 的定義（在 `lapi.c`）。讀它的 body，找到它把「真正執行」委派給哪個底層函式。

- 在 body 裡那個底層呼叫上按 `gd`，跳到它的定義。
- 提示：那個函式在 `ldo.c`（Lua 的 "do" ——執行/呼叫/錯誤處理核心）。

### Step 3：往下追到 `luaD_call`

從 Step 2 落地的函式，繼續往真正「切換到被呼叫函式並執行」的核心追。目標是 `luaD_call`（或 `luaD_callnoyield`）。

- 用 `gd` 一層層往下，或用 `<leader>co`（outgoing calls）展開「這個函式呼叫誰」。
- **這裡有個坑**：某些委派是透過**函式指標**（`Pfunc func`）做的，call hierarchy 對它追不到（Ch 19/21 講的靜態天花板）。遇到 outgoing calls 回空，別以為壞了——那是函式指標，得靠讀 body 手動找。

### Step 4：反向驗證——`luaD_call` 是被誰呼叫的

追到 `luaD_call` 後，反過來確認你的鏈追對了：**用 call hierarchy 的 incoming calls（`<leader>ci`）看 `luaD_call` 被誰呼叫**。

- 你應該在 caller 清單裡看到你一路追下來的那些函式（`lua_pcallk`/`lua_callk`），以及字節碼直譯器 `luaV_execute`。
- 這一步是「閉環驗證」：往下追的路徑，能在往上溯源的清單裡對上，才確定沒追錯。

### Step 5：全程用 jumplist 回到起點

追了四五跳，現在一路 `<C-o>` 退回最初的 `lua_pcall`。體會 jumplist 怎麼記住你每一跳、讓你原路返回。追鏈的完整節奏就是「`gd` 下鑽 → 讀 → `<C-o>` 浮回 → 換條路再鑽」。

## 如果你卡住了（鍵位方向提示）

1. **找不到 `lua_pcall` 從哪開始**：`<leader>ws` 打 `lua_pcall`，clangd 列出它的定義（會發現是 macro）。或直接開 `lua.h`，`/lua_pcall` 搜到那行 `#define`。

2. **`gd` 跳到 macro 的 `#define` 就卡住**：macro 展開後的目標要在**展開後的 token** 上跳。`lua_pcall` → `lua_pcallk`，你在 `lua_pcallk` 這個名字上再 `gd` 才進實作。

3. **outgoing calls（`<leader>co`）回空的**：那多半是函式指標分派（`Pfunc`）。改用「讀 body + 對 body 裡的呼叫名 `gd`」手動追。這是 clangd 的天花板，不是你操作錯。

4. **`gr`/call hierarchy 漏東西**：背景 index 還沒建完（Ch 21）。看狀態列 indexing 進度，等它跑完再查。大專案剛開的前一分鐘跨檔功能不完整。

5. **跳到別的檔迷路了、想回起點**：狂按 `<C-o>` 沿 jumplist 一站站退。或在起點先按 `ma` 標一個 mark（Part 1 Ch 8），之後 `` `a `` 一鍵跳回。

## 驗證：你追對了嗎

追完後，你的鏈應該長這樣（每個環節都能用 clangd 驗證）：

```
lua_pcall (lua.h 的 macro)
   │ 展開
   ▼
lua_pcallk (lapi.c)
   │ 呼叫
   ▼
luaD_pcall (ldo.c)
   │ 透過函式指標 Pfunc 執行 → 最終走到
   ▼
luaD_call (ldo.c) ← 真正切換到被呼叫函式
```

用 `<leader>ci` 對 `luaD_call` 看 incoming calls，確認 `lua_pcallk`/`lua_callk`/`luaV_execute` 都在清單裡——閉環對上就是追對了。

## 參考解答（先自己追完再看）

<details>
<summary>點開逐鍵示範 + headless 驗證的真實位置</summary>

以下位置是 headless 對 Lua 5.4.6 跑 clangd-14 照抄的真實輸出。你的行號應該一致（同一個 tag v5.4.6）。

**Step 1：`lua_pcall` 是 macro**

在 `lua.h` 搜 `lua_pcall`（`/lua_pcall<CR>`），看到：

```
lua.h:298:  #define lua_pcall(L,n,r,f)  lua_pcallk(L, (n), (r), (f), 0, NULL)
```

`lua_pcall` 是 macro，展開成 `lua_pcallk`。在 `lua_pcallk` 這個 token 上按 `gd`。

**Step 2：`lua_pcallk` 的定義在 `lapi.c`**

`gd` 跳到（真實驗證）：

```
lua_pcallk 定義 @ lapi.c:1043
    LUA_API int lua_pcallk (lua_State *L, int nargs, int nresults, int errfunc, ...)
```

順帶用 `gr` 看 `lua_pcallk` 被誰引用（真實 headless 輸出，count=8）：

```
=== gr lua_pcallk count=8 ===
  lapi.c:1043      ← 定義
  lbaselib.c:477   ← Lua 標準庫 pcall
  lbaselib.c:494
  ldblib.c:428     ← debug 庫
  lua.c:160        ← 命令列直譯器（REPL）
  lua.c:583
  lua.c:673
  lua.h:296        ← 宣告
```

一眼看出：命令列 `lua` 程式（`lua.c`）和標準庫 `pcall`（`lbaselib.c`）都走這個 API。這就是 `gr` 找影響面的價值。

讀 `lua_pcallk` 的 body，往下捲到它呼叫 `luaD_pcall` 那行：

```
lapi.c:1064:    status = luaD_pcall(L, f_call, &c, savestack(L, c.func), func);
```

游標移到 `luaD_pcall`，按 `gd`。

**Step 3：`gd luaD_pcall` → `ldo.c`**

真實驗證：

```
=== gd luaD_pcall from lapi.c ===
  -> ldo.c:946
    int luaD_pcall (lua_State *L, Pfunc func, void *u, ...)
```

跳到 `ldo.c:946`。注意它第二個參數是 `Pfunc func`——**函式指標**。這就是 Step 3 的坑：`luaD_pcall` 真正執行的工作是透過 `func` 這個指標呼叫的。

在 `luaD_pcall` 上按 `<leader>co`（outgoing calls）——真實輸出是**空的**：

```
=== outgoing calls from luaD_pcall ===
（空）
```

不是壞了。`luaD_pcall` 透過函式指標分派，clangd 靜態看不出它指向誰。要往下追，得改讀 body 或看誰把 `func` 傳進來（那些 caller 傳的是 `f_call` 之類的具體函式，`f_call` 裡才呼叫 `luaD_call`）。這是 Ch 19/21 講的「call hierarchy 對函式指標追不到」的親身現場。

**Step 4：反向 incoming calls 驗證 `luaD_call`**

`luaD_call` 定義在 `ldo.c:646`。游標放上去，按 `<leader>ci`（incoming calls）——真實 headless 輸出：

```
=== incoming calls to luaD_call ===
  <- callclosemethod @ lfunc.c:107
  <- luaT_callTM      @ ltm.c:103
  <- luaT_callTMres   @ ltm.c:119
  <- luaV_execute     @ lvm.c:1146     ← 字節碼直譯器主迴圈
  <- lua_callk        @ lapi.c:1004    ← 對外 API lua_call
  <- lua_pcallk       @ lapi.c:1043    ← 我們的起點！
```

**閉環對上了**：清單裡有 `lua_pcallk`（我們的起點）——證明 `lua_pcall → lua_pcallk → ...(f_call/luaD_pcall)... → luaD_call` 這條鏈追對了。同時你看到 `luaV_execute`（字節碼直譯器）也直接呼叫 `luaD_call`——Lua 執行 bytecode 時遇到 `CALL` 指令就走這條。這是往上溯源給你的額外情報：`luaD_call` 是「所有函式呼叫的匯流點」。

**Step 5：`<C-o>` 回起點**

從 `ldo.c` 的 `luaD_call` 開始狂按 `<C-o>`：退回 `lapi.c:1064`（luaD_pcall 呼叫點）→ 退回 `lapi.c:1043`（lua_pcallk 定義）→ 退回 `lua.h:298`（lua_pcall macro）。每一跳 jumplist 都記著，原路返回。

</details>

## 驗證清單

- [ ] `bear -- make` 生出了 `compile_commands.json`，`clangd --check=lapi.c` 印 "Loaded compilation database"
- [ ] 我發現 `lua_pcall` 是 macro，展開成 `lua_pcallk`，並在展開後的 token 上跳進實作
- [ ] `gd` 從 `lapi.c` 的 `luaD_pcall` 呼叫跳到 `ldo.c:946`
- [ ] 我遇到 `luaD_pcall` 的函式指標坑（outgoing calls 空），知道那是靜態天花板不是操作錯
- [ ] `<leader>ci` 對 `luaD_call` 的 incoming 清單裡有 `lua_pcallk`，閉環驗證追對
- [ ] 全程用 `<C-o>`/`<C-i>` 在檔案間來回，最後退回起點

## 延伸挑戰

1. **另一條鏈**：追 `lua_call`（非 protected 版，`lua_callk`）從 `lapi.c:1004` 到底層，跟 `lua_pcall` 的鏈對比——它們最後都匯到 `luaD_call`，但 `lua_pcall` 多了一層錯誤保護（`luaD_pcall` 的 setjmp/longjmp）。用 hover 和 `gr` 找出「保護」是怎麼加上去的。

2. **函式指標的另一端**：Step 3 卡在 `Pfunc func`。手動追：`gr` 找 `luaD_pcall` 的所有 caller，看它們傳什麼函式進 `func`（如 `lua_pcallk` 傳的是 `f_call`）。跳進 `f_call`，看它才是呼叫 `luaD_call` 的地方。這是「函式指標分派」的手動穿透，clangd 給不了、只能讀 code。

3. **gtags 對照（Part 5 預習）**：對同一條鏈，用 `global -x luaD_call`（GNU global，純語法）找 `luaD_call` 的定義與引用，跟 clangd 的 incoming calls 比。gtags 會不會多撈到雜訊？函式指標那段 gtags 反而追得到嗎（因為它不管語意、全文字索引）？體會「精準層 vs 廣度層」的差別。

4. **診斷掃一遍**：對 `ldo.c` 開 `<leader>q`（診斷送 quickfix），看 clangd 對這個核心檔報了什麼。有沒有它自己 clang 版本造成的假紅線（Ch 22 踩雷）？

## 自我檢核

- [ ] 我能獨立在一個真專案裡從公開 API 追一條 call chain 到底層核心
- [ ] 我知道追鏈遇到 macro（`lua_pcall`→`lua_pcallk`）該怎麼穿透
- [ ] 我理解 call hierarchy 對函式指標追不到，並會手動用 `gr` 穿透
- [ ] 我會用 incoming calls 做「閉環驗證」確認鏈追對了
- [ ] 我全程用 jumplist（`<C-o>`/`<C-i>`）在多檔間導航、原路返回
- [ ] 我體會到「往下追（outgoing/gd）」與「往上溯源（incoming）」是同一條鏈的兩個方向

做完這個練習，Part 4「懂語意」就從按鍵變成攻堅肌肉了。你現在能在能 build 的專案裡用 clangd 精準追鏈。但很多真實場景——編不起來的 kernel 子系統、多 config 樹、超大到 clangd index 跑不動——clangd 會失效。Part 5 補上那塊：沒有 compile_commands 時的 tags 後備（ctags/gtags/cscope），純語法但無所不在。

→ [Ch 23 為什麼需要 tags 後備](./23-why-tags-fallback.md)
