# Ch 0 — 環境與六個釘死的攻堅目標

> **目標**：把六個傳奇 codebase 用**釘死的版本**準備好，clone、build、跑起來，並理解「為什麼版本要釘死」對讀碼的可重現性至關重要。這章是全課的契約——你 clone 的每一行，都和後面章節引用的檔名、行號、function 對得上。

> **環境**：WSL2 / Linux x86-64，gcc + make + git。本課六個目標全部在此環境 clone、build、閱讀。

## 為什麼需要釘死版本？

`reading_code` 教你怎麼攻堅陌生 code。這門課換一種練法：拿六個**你 clone 得到、我也 clone 得到、一模一樣**的目標來練。

差別在哪？假設某章說「`lua_State` 定義在 `lstate.h` 第 320 行附近，`CallInfo` 陣列是 stack of frames」。如果你 clone 的是 Lua 的 master 分支，而我寫教材時讀的是另一個 commit，那第 320 行可能是別的東西——你會以為自己讀錯，其實是版本漂移。

**讀碼是精確的活。行號、function 名、struct 欄位，差一個版本就對不上。** 所以本課把每個目標釘死在一個**穩定 release tag**。你照 Ch 0 的指令 clone，看到的就和教材完全一致。這也是一個可遷移的紀律：日後你讀任何專案、跟同事討論任何一段 code，先對齊 commit hash，能省掉大量「我這邊不是這樣」的鬼打牆。

## 六個目標與它們要教的 pattern

我們刻意由小而硬排列，而且刻意**避開你在其他課已經深挖過的**（Linux kernel → `kernel_internals`、V8 → `browser_pwn`、LLVM → 你的 compiler 課群、你自己手刻的 DB → `database_internals`）。這六個都是你大概率沒逐行讀過的新鮮硬目標：

```
     小 ────────────────────── 讀碼難度 ──────────────────────► 硬
     │                                                          │
  ┌──────┐   ┌────────┐   ┌───────┐   ┌─────┐   ┌─────────┐  ┌───────────┐
  │ Lua  │ → │ SQLite │ → │ nginx │ → │ git │ → │ CPython │  │ PostgreSQL│
  │~2萬行│   │儲存+VM │   │reactor│   │資料 │   │大型     │  │ capstone  │
  │語言  │   │        │   │高並發 │   │模型 │   │runtime  │  │ 冷讀executor│
  │runtime│  │        │   │       │   │     │   │         │  │           │
  └──────┘   └────────┘   └───────┘   └─────┘   └─────────┘  └───────────┘
   register    VDBE        event        DAG /     refcount     火山模型
   VM / GC     bytecode     loop /       content-   + cyclic     executor /
              VM / btree    mem pool     addressed   GC / object  節點樹
                            / plugin     store       protocol
```

| 目標 | 釘死 tag | commit | 為什麼選它 |
|---|---|---|---|
| **Lua** | `v5.4.7` | `1ab3208` | 全世界最乾淨的語言 runtime，約 2 萬行讀得完；register VM + 增量 GC 是教科書級範本 |
| **SQLite** | `version-3.47.2` | `262de1b` | 地表被讀最多次的 C 專案之一；VDBE bytecode VM + B-tree + pager 分層清晰 |
| **nginx** | `release-1.26.2` | `37fe983` | event-driven 高並發架構的典範；memory pool、module pipeline 是 C 系統設計範本 |
| **git** | `v2.47.1` | `92999a4` | 「資料模型即一切」的最佳教材；content-addressed object store |
| **CPython** | `v3.13.1` | `0671451` | 你每天在用的大型 runtime；教你在大專案裡不迷路 + object model |
| **PostgreSQL** | `REL_17_2` | `6304632` | 畢業考：冷讀一個成熟資料庫的 executor，火山模型節點樹 |

## 第一步：把六個目標 clone 下來

每個都用 `--branch <tag>` 釘死到穩定版，`--depth 1` 只抓那個版本的樹（省時省空間；讀碼不需要完整歷史，需要歷史時 Ch 17「git 當考古工具」的技巧再補抓）：

```bash
mkdir -p ~/cbcs && cd ~/cbcs

git clone --depth 1 --branch v5.4.7          https://github.com/lua/lua           lua
git clone --depth 1 --branch version-3.47.2  https://github.com/sqlite/sqlite     sqlite
git clone --depth 1 --branch release-1.26.2  https://github.com/nginx/nginx       nginx
git clone --depth 1 --branch v2.47.1         https://github.com/git/git           git
git clone --depth 1 --branch v3.13.1         https://github.com/python/cpython    cpython
git clone --depth 1 --branch REL_17_2        https://github.com/postgres/postgres postgres
```

驗證你 clone 到的 commit 和教材一致（這一步別跳過——這就是「對齊版本」的紀律）：

```bash
$ for d in lua sqlite nginx git cpython postgres; do
    printf "%-10s %s\n" "$d" "$(cd ~/cbcs/$d && git rev-parse --short HEAD)"
  done
lua        1ab3208
sqlite     262de1b
nginx      37fe983
git        92999a4
cpython    0671451
postgres   6304632
```

六個 hash 對得上，你就和教材站在同一條 world line 上了。

## 第二步：至少 build 一個，確認工具鏈可用

讀碼不一定要能 build（很多時候你只讀不編），但**能 build 能跑的目標，你能用 debugger 動態驗證假設**（`reading_code` Ch 18 的 debugger-driven reading）。我們先 build 最小的 Lua 熱身，也順便踩第一個真實的 build 坑。

```bash
$ cd ~/cbcs/lua && make
```

第一次大概率不會過，你會看到：

```
lua.c:443:11: fatal error: readline/readline.h: No such file or directory
  443 | #include <readline/readline.h>
      |           ^~~~~~~~~~~~~~~~~~~~~
compilation terminated.
make: *** [<builtin>: lua.o] Error 1
```

**這是教材，不是意外。** Lua 的獨立直譯器（`lua.c`）用 GNU readline 做互動式命令列的行編輯，但 readline 是選配的系統套件。這是讀陌生 C 專案第一天最常見的卡點：**缺 build 依賴**。裝上它：

```bash
$ sudo apt-get install -y libreadline-dev
$ make clean && make
```

過了之後跑一下：

```bash
$ echo "print(1+2, _VERSION)" | ./lua
3	Lua 5.4
```

`3` 和 `Lua 5.4` 印出來，你的 Lua 就活了。（本課這段是在 WSL2 Ubuntu、gcc 11 真跑出來的輸出。）

> 為什麼從能 build 的 Lua 開始很重要：Part 1 讀 VM 的 dispatch loop 時，你可以在 `luaV_execute` 下中斷點，親眼看 opcode 一個一個被執行。**讀 + 跑 + 中斷** 三管齊下，比純讀快得多。SQLite、git、CPython、PostgreSQL 也都能 build（各有 configure/make 流程，對應 Part 開頭章會帶），nginx 需要幾個依賴（PCRE/zlib），Ch 13 會處理。

## 第三步：把 reading_code 的工具鏈架好

這門課不重教工具（`reading_code` Part 3 已經教過），但每個目標你都會用到這幾樣。快速自我檢查你手上有：

| 工具 | 幹嘛用 | 對應 reading_code |
|---|---|---|
| `ripgrep` (`rg`) | 全文極速搜尋，找 function 定義/呼叫點 | Ch 12 |
| **LSP**（clangd）| 跳定義、找 reference、型別提示 | Ch 13 |
| `ctags` / `cscope` | 無 build 時的符號索引（大專案救命） | Ch 14 |
| `tree-sitter` | 結構化查詢（找所有某形狀的 code） | Ch 15 |
| `gdb` | 動態驗證假設、下中斷點看真實執行 | Ch 18 |
| `git log -S` / `blame` | 考古：這行為什麼存在、何時改的 | Ch 17 |

其中最該先架好的是 **clangd**（LSP）。對 C 專案，clangd 需要 `compile_commands.json` 才能精準跳轉。多數目標可以用 [bear](https://github.com/rizsotto/Bear) 攔截 build 產生它：

```bash
$ sudo apt-get install -y bear clangd
$ cd ~/cbcs/lua && make clean && bear -- make   # 產生 compile_commands.json
```

之後在編輯器開 `~/cbcs/lua`，clangd 就能對 `luaV_execute` 這種 function 做精準 go-to-definition。沒有 LSP 也能讀（退回 `rg` + `ctags`），但有 LSP 讀大專案快一個檔次。

## 底層機制：為什麼「釘死版本 + 真 clone」改變讀碼品質

畫一下這門課和「隔空講解」的差別：

```
   隔空講解（教材憑記憶寫）              本課（釘死版本真讀）
   ┌────────────────────────┐        ┌──────────────────────────────┐
   │ 「lua_State 大概長這樣」│        │ 你 clone v5.4.7               │
   │  → 可能記錯欄位         │        │ 教材引用 lstate.h:<真實行號>  │
   │  → 版本漂移對不上       │        │ 你 rg 一下，親眼看到同一段    │
   │  → 讀者無法驗證         │        │ 你能下中斷點驗證它真的這樣跑  │
   └────────────────────────┘        └──────────────────────────────┘
        讀者被動相信                       讀者能親手核對每一句
```

這不只是教材品質問題，是**讀碼方法**問題。讀碼的鐵律是「不要相信任何二手描述，去看 code 本身」——包括不要無條件相信這份教材。每一章引用某個 function 時，你的動作應該是 `rg` 它、跳過去、親眼確認。教材的角色是**帶路**（告訴你該看哪裡、那段在幹嘛、屬於哪個 pattern），不是替你讀。

## 踩雷集錦

1. **不 clone、光看教材**：這是最大的雷。這門課的技巧是「讀」，不 clone 真 source 來讀，等於看健身教學影片不進健身房。每個 Part 一定先 clone 對應 repo。
2. **clone master 而不是釘死的 tag**：master 一直在變，你會發現行號、function 都和教材對不上，然後懷疑人生。永遠 `--branch <tag>`，並 `git rev-parse HEAD` 核對 hash。
3. **以為 build 失敗是自己的錯**：缺依賴（readline、PCRE、zlib、openssl）造成的 build 失敗是常態，不是你環境壞了。讀錯誤訊息最後一行的 `No such file`，裝對應 `-dev` 套件即可。
4. **一開始就想讀懂全部**：CPython 有近 50 萬行、PostgreSQL 更大。想從第一行讀到最後一行必定崩潰。本課每個 Part 只攻**幾條關鍵路徑**，其餘刻意不讀——這正是 `reading_code` Ch 11「收斂到你要的 200 行」的實戰。
5. **沒架 LSP 硬用純文字讀大專案**：在 CPython 這種規模裡，沒有 go-to-definition，你會在 `#define` 和函式指標間迷路。花 10 分鐘架好 clangd，省後面幾小時。

## 進階：再往深一層

- **用 `git worktree` 同時開多個版本**：想比較 Lua 5.3 和 5.4 的 GC 差異？clone 一次、`git worktree add` 另一個 tag，兩個版本並排讀。這是 patch-diff 式讀碼（找漏洞常用，接 `reading_code` Ch 32）的基礎。
- **Docker 化每個目標的 build 環境**：nginx/PostgreSQL 的依賴較多，把每個目標的 build 環境寫成一個 Dockerfile，日後重現零摩擦。你的 `docker` 課的技巧正好用上。
- **建一個 `compile_commands.json` 的快取**：對每個目標跑一次 `bear -- make` 存起來，之後開編輯器直接吃，LSP 秒回。

## 本章重點整理

- 這門課用**六個釘死版本的傳奇 codebase**當練習器材：Lua / SQLite / nginx / git / CPython / PostgreSQL。
- **釘死版本（穩定 tag + 核對 commit hash）** 是可重現讀碼的前提——教材引用的行號/function 和你 clone 的完全一致。
- 讀碼鐵律：不信二手描述，`rg` 過去親眼看。教材是帶路的，不是替你讀的。
- 每個目標只攻幾條關鍵路徑，其餘刻意不讀——這是 `reading_code` 收斂技巧的實戰。

## 自我檢核

- [ ] 我六個 repo 都 clone 了，而且 `git rev-parse HEAD` 六個 hash 都和上表對得上
- [ ] 我至少把 Lua build 起來、`./lua` 跑出了 `3	Lua 5.4`
- [ ] 我知道為什麼「釘死版本」對讀碼的可重現性是必要的，而不只是龜毛
- [ ] 我的 LSP（clangd）或至少 `rg` + `ctags` 準備好了
- [ ] 我理解教材的角色是帶路，我讀每一段都會自己 `rg` 過去核對

## 延伸閱讀

### 官方架構文件（讀 code 前先讀它們的自述）

- **[SQLite Architecture](https://www.sqlite.org/arch.html)**
  - **讀哪裡**：整頁 + 那張分層圖；Part 2 開始前先看，你會知道 tokenizer→parser→codegen→VDBE→B-tree→pager 的分層
  - **前提**：無，這是給讀者的高層導覽
- **[Git Internals — Git Objects](https://git-scm.com/book/en/v2/Git-Internals-Git-Objects)**（Pro Git 第 10 章）
  - **讀哪裡**：10.2 Git Objects、10.4 Packfiles；Part 4 讀 git 前先看，object model 的心智模型先建好
  - **前提**：會用 git 基本命令
- **[CPython Internals（devguide）](https://devguide.python.org/internals/)**
  - **讀哪裡**：「CPython source code layout」與「Compiler / interpreter」兩節；Part 5 的地圖
  - **前提**：讀得懂 C

### 書籍

- **《The Programmer's Brain》** — Felienne Hermans（Manning, 2021）
  - **這本書的定位**：解釋「為什麼 pattern 庫讓你讀得快」的認知科學；下一章 Ch 1 會用到它的 chunking / beacon 概念
  - **讀哪幾章**：第 1–3 章（chunking、working memory、beacon）與本課 Part 0 最相關

六個目標就位、版本對齊。下一章我們先講清楚一件事：讀碼變快的機制到底是什麼？為什麼「pattern 辨識」是這門課的核心，而不是再學十個工具？

→ [Ch 1 讀碼即 pattern 辨識：chunking 的科學](./01-reading-is-pattern-recognition.md)
