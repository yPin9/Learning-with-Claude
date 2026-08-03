# Final Project — 用 Neovim 冷啟動攻堅一個大 C 專案

> **目標**：這是全課的畢業考。前面每個 Part 教一件工具，Ch 28 示範了它們怎麼協同，Ch 30 給了鍵位手冊。現在**真的拿這台機器去攻一個你沒讀過的大 C 專案**——全程只用 nvim + 你搭的這台機器，限時完成：偵察建地圖、追一條核心 call chain、外化（harpoon 標熱點 + notes.md 筆記），最後產出「攻堅報告 + 我用了哪些鍵位/工具」。這個 Final 接 `reading_code` 的 final（冷啟動攻堅一個真實 codebase），但這裡聚焦「**用這台機器**」——不是再學攻堅方法，是證明你能用 Neovim 把方法跑出來。

> **環境**：Neovim v0.12.4，WSL2 / Ubuntu，你 Ch 0-27 搭好的 config（treesitter/telescope/clangd/harpoon/persistence）+ gtags/rg/ctags。下面 `<details>` 的 redis 示範是真 clone、真 gtags 查詢，輸出都是真跑的。

## 為什麼是「冷啟動」+「只用這台機器」

前面練習都在熟悉的檔上按鍵。真正的考驗是兩件事疊加：

1. **冷啟動**——一個你零背景的大專案。這是你職涯反覆遇到的：onboarding legacy 系統、審一個陌生依賴、貢獻沒看過的開源。
2. **只用這台機器**——不准開 VSCode、不准 GitHub 網頁瀏覽、不准 grep 完用滑鼠捲。全程 nvim + 終端 CLI 工具。逼你把這台機器的每個工具用出來，暴露你哪個 Part 還不熟。

`reading_code` 的 final 考的是「方法內化了沒」，這個 final 考的是「**方法能不能用這台機器跑出來**」——手和方法的合一。

## 選一個目標

**選擇標準**（盡量都滿足）：

1. **中型**：兩萬到十五萬行 C。太小沒攻堅張力，太大一天摸不到邊。
2. **能編出來**：最好能 `bear -- make` 生 `compile_commands.json`，讓 clangd 精準（Ch 18）。編不起來也行——那正好練 clangd 跪→退 gtags（Ch 23-25），但別卡在 build 超過一小時。
3. **陌生但用過/聽過**：沒讀過原始碼、但知道它幹嘛。
4. **接 `codebase_case_studies` 或本課用過的靶**：那批專案（redis/lua/nginx/memcached…）都符合標準。

**候選清單**（難度遞增）：

| 專案 | 語言 | 規模 | 為什麼適合 |
|---|---|---|---|
| **lua** | C | ~2 萬行 | 教科書級乾淨，clangd 好用，一天摸到核心 |
| **memcached** | C | ~2 萬行 | 網路 + 事件驅動 + slab，接你的 networking |
| **redis 的某個沒碰過的子系統** | C | 局部 | 熟環境換戰場（cluster / AOF / scripting） |
| **nginx** | C | ~15 萬行 | 事件驅動 + 模組化，考驗收斂能力 |
| **sqlite（讀 src/ 非 amalgamation）** | C | ~15 萬行 | VM + B-tree + parser，硬但極有料 |

進階（逼出技巧）：挑一個編**不起來**的（如 Linux kernel 某個 driver 子目錄），強制練整條 gtags 後備流程——這正是本課 Part 5 的用武之地。

下面 `<details>` 用 **redis** 做精簡示範（真 gtags 查詢）。**你該挑一個不同的專案**——照抄示範等於沒考。

## 任務規格：三個里程碑 + 一份報告

限時建議一天（真正動手約 4-6 小時）。分三個里程碑，每個有明確驗收。全程用這台機器。

### M1 — 偵察建地圖（約 1-1.5 小時）

用 nvim 建出這專案的骨架印象。**必須用到的工具**：

- **telescope `<leader>ff`** 看整體結構（檔名分層）。
- **rg / `<leader>fg`** 找所有 `int main`（別假設只有一個）。
- **document symbol `<leader>ds`** 對核心檔建單檔地圖。
- **treesitter**（`:InspectTree` / sticky context）確認結構。
- 若能編：`bear -- make` 生 compile db，確認 clangd attach（`:LspInfo`）。

**產出**：notes.md 裡的偵察節 + 一張主流程圖（ASCII/mermaid）：規模、目錄分層、entry point(s)、build 方式、核心資料結構、主迴圈在哪。

### M2 — 追一條核心 call chain（約 1.5-2 小時）

挑一個**使用者可觀察的行為**（一條命令、一次請求），從觸發點追到底。**必須用到的工具**：

- **`gd` / `gr`**（clangd）追語意、反查 caller；配 **`Ctrl-o`/`Ctrl-i`** jumplist 來回。
- **clangd 跪就退 gtags**：`global -x foo`（定義）、`global -rx foo`（caller）。至少展示一次「判斷 clangd 半殘 → 退 gtags」的決策（就算 clangd 沒跪，也跑一次 gtags 對照，證明你會後備）。
- **quickfix `:copen`/`:cnext`** 把一組 caller 組成待看清單逐一走。
- 左右分割對照 caller/callee（Ch 27 佈局）。

**產出**：notes.md 裡的路徑節——`funcA → funcB → funcC`，每一跳標「資料變成什麼、為什麼跳這條」，附行號（gd/gtags 查到的）。

### M3 — 外化 + 報告（約 1-1.5 小時）

把攻堅成果固化，產出報告。**必須用到的工具**：

- **harpoon `<leader>a`** 把這條路徑的 4-5 個熱點釘進清單，示範 `<leader>1-4` 瞬移。
- **notes.md 三欄**（假設/問題/發現）——至少各 3 條，且有幾條「流動」（問題→發現、假設→驗證）。
- **persistence**：關 nvim 前確認 session 自動存，下次 `<leader>qs` 能還原（證明你會凍結現場）。
- （可選，接 Ch 29）動態驗證一個假設：跑起來看行為，或 gdb 下斷點。

**產出**：一份**攻堅報告**（見下方模板）+ 一張**「我用了哪些鍵位/工具」對照表**。

## 攻堅報告模板

```markdown
# 攻堅報告：<專案> @ <commit>   <日期>

## 任務界定
- 本次任務：
- 成功標準：
- 不需要懂：

## M1 偵察與地圖
- 規模：__ 行 / 語言 __ / entry point：<檔:行>（共 __ 個，真主程式是哪個）
- build：__（成功 / 卡在哪 / clangd attach 了嗎）
- 核心資料結構：<struct> —— 一句話
- 主流程圖：
  （ASCII/mermaid）

## M2 call chain
- 觸發：<使用者動作>
- 路徑：funcA(檔:行) → funcB(檔:行) → funcC(檔:行)
  - 每一跳：資料變成什麼 / 為什麼跳這條
- clangd vs gtags：哪裡用 clangd、哪裡退 gtags、為什麼

## M3 外化
- harpoon 熱點（4-5 個）：
- 三欄筆記摘要（假設/問題/發現各幾條，哪些流動了）：
- 動態驗證（如有）：

## 我用了哪些鍵位/工具（對照表）
| 階段 | 用的鍵/工具 | 幹嘛 |
|---|---|---|
| 偵察 | <leader>ff, rg | ... |
| ... | ... | ... |
```

## 驗收標準

**M1（偵察建地圖）**

- [ ] 正確辨識所有 entry point，說明哪個是真主程式
- [ ] 主流程圖對應到真實的 entry→主迴圈路徑（不是憑空想）
- [ ] 核心資料結構抓得準（真的核心，不是隨便挑的 struct）
- [ ] 至少用到 telescope + rg + document symbol 三個工具

**M2（追 call chain）**

- [ ] 呼叫鏈完整、每一跳有「資料變成什麼」標註、有行號
- [ ] 路徑是「使用者可觀察行為」，不是內部工具函式
- [ ] **展示了 clangd → gtags 的後備決策**（至少 gtags 對照一次）
- [ ] 用 quickfix 或 jumplist 組織過導航（不是靠搜尋跳回去）

**M3（外化 + 報告）**

- [ ] harpoon 釘了 4-5 個熱點，示範過 `<leader>1-4` 瞬移
- [ ] notes.md 三欄各 ≥3 條，且有 ≥1 條「流動」（問題→發現 或 假設→驗證）
- [ ] session 能還原（persistence 存了、`<leader>qs` 回得來）
- [ ] 報告完整，含「我用了哪些鍵位/工具」對照表

**整合度**：全程只用這台機器（不開別的編輯器/GUI），且用到本課 70%+ 的 Part——移動、搜尋、結構、語意、後備、外化、佈局都碰到。

## 「用到本課哪些章/工具」對照表

攻堅完，用這張表自查你把這台機器用了多滿。理想是每一列都打勾：

| 本課 Part / 章 | 工具 | 這次攻堅用在哪 | 用了嗎 |
|---|---|---|---|
| Part 1（Ch 3-8） | motion / jumplist / marks | 檔內快速到位、追進去倒退回來 | [ ] |
| Part 2（Ch 9-12） | telescope / rg / quickfix | 偵察看結構、找 main、組 caller 清單 | [ ] |
| Part 3（Ch 13-16） | treesitter | 看單檔結構、sticky context | [ ] |
| Part 4（Ch 17-22） | clangd gd/gr/symbol | 追語意、反查 caller、建地圖 | [ ] |
| Part 5（Ch 23-25） | gtags / ctags | clangd 跪時後備、對照 | [ ] |
| Ch 26 | harpoon / notes.md | 釘熱點、外化假設發現 | [ ] |
| Ch 27 | session / 佈局 | 三區佈局、凍結現場 | [ ] |
| Ch 29 | :LspInfo / :checkhealth | 判斷 clangd 半殘、debug | [ ] |

## 分階段時程建議（一天）

```
 時段          里程碑                    產出           防坑
 ────────────────────────────────────────────────────────
 00:00-00:15   選目標 + 界定任務(notes)  三欄頭         「不需要懂」欄一定填
 00:15-01:30   M1 偵察建地圖(含build)    偵察節+主流程圖  build 卡 1hr 就換專案
 01:30-03:00   M2 追 call chain          路徑節         至少對照一次 gtags
 03:00-03:30   午休/沉澱
 03:30-04:30   M3 外化(harpoon+notes)    熱點+三欄       流動至少一條
 04:30-05:30   寫報告 + 對照表 + 驗證     完整報告       補「用了哪些鍵」表
```

## 常見卡點與提示

**卡點 1：build 編不起來，卡了兩小時。**
→ 選目標時就該確認能編。真卡住：試 README 步驟、缺依賴就裝、給 build 硬上限 1 小時，到點沒編出來**果斷換專案**或**直接進 gtags 流程**（gtags 不需要編譯，反而是練 Part 5 的好機會）。攻堅能力不是跟 build 系統搏鬥證明的。

**卡點 2：偵察完不知從哪下手，地圖畫不出來。**
→ 別想「一次理解全部」。收窄：先畫「從 main 到主迴圈這一條線」，有一條線就有骨架。找主迴圈技巧：`global -rx` 反查 `*Main`/`*loop`/事件分派函式的呼叫者（Ch 25/28），或看檔名找 `*vm*`/`*event*`/`*loop*`。

**卡點 3：`gd` 沒反應，卡住。**
→ 這正是本課教你處理的（Ch 29 反模式 3）。`:LspInfo` 判斷——沒 attach？缺 compile db（root_markers 沒命中）？還在索引？確認 clangd 半殘就**立刻退 gtags**，別在 clangd 上死磕。這個決策本身就是驗收項。

**卡點 4：追路徑追到一半迷失。**
→ rabbit-hole。回 notes.md 看任務三欄，問「這條岔路跟成功標準有關嗎」。無關記 TODO、`Ctrl-o` 退回主線。harpoon 的熱點是你的錨——迷失了 `<leader>1` 回到入口重新定向。

**卡點 5：報告寫出來自己都覺得心虛。**
→ 心虛誠實告訴你「這裡還沒懂」（費曼測試的價值，`reading_code` Ch 36）。別粉飾，回去把心虛那句對應的 code 用 gd/gtags 再追一次。真懂了心虛自然消失。

## 示範：redis 精簡攻堅（真跑）

下面是用這台機器對 redis 做的**精簡示範**（不是完整一天，是給你看「長什麼樣」）。**你的 Final 該用不同專案。**

<details>
<summary>點開：redis 精簡攻堅示範（真實 gtags 輸出）</summary>

### 選目標 + 界定任務

從 `github.com/redis/redis` clone。選它因為符合標準：C、十幾萬行、事件驅動、活躍。

```
本次任務：搞懂一條 GET 命令從進來到執行的路徑
成功標準：(1) 畫出 主迴圈→讀輸入→分派→執行 的流程
          (2) 追出 GET 落地在哪個 handler
不需要懂：RESP3 編碼細節、cluster、AOF 持久化
```

### M1 偵察建地圖

用 `<leader>ff`（telescope）看結構——`src/` 下按功能分：`server.c`（主）、`networking.c`（IO）、`t_string.c`/`t_list.c`（各型別命令）、`dict.c`（hash table）、`ae.c`（事件迴圈）。

找 entry（rg / `<leader>fg`）——真跑 gtags：

```
$ global -x main
main             6917 server.c    int main(int argc, char **argv) {
```

主迴圈（事件迴圈的呼叫點 = 心臟）：

```
$ global -rx aeMain
aeMain           7251 server.c        aeMain(server.el);
```

主流程圖（推導）：

```
 main(server.c:6917)
   │  initServer → 註冊 read handler
   ▼
 aeMain(server.c:7251)          事件迴圈（心臟）
   │  socket 可讀 → 觸發 handler
   ▼
 readQueryFromClient            讀進 c->querybuf
   │  parse 成 c->argv[]
   ▼
 processCommand(server.c:3884)  分派中心
   │  lookupCommand 查表 → call()
   ▼
 具體 handler（如 getCommand）
```

核心資料結構：`client *c`（每個連線一個，欄位 querybuf/argv/buf 是 in/parse/out）、`redisCommand`（命令表項，含 handler 函式指標）、`redisObject`（一個值）。

### M2 追 call chain — GET 怎麼被執行

觸發：client 送 `GET foo`。追分派中心 `processCommand`，先用 clangd `gd`/`gr`，這裡用等價 gtags 真跑對照：

```
$ global -x processCommand
processCommand   3884 server.c    int processCommand(client *c) {

$ global -rx processCommand           # 誰呼叫分派中心
processCommand   2505 networking.c        if (processCommand(c) == C_OK) {
```

`processCommand` 被 `networking.c:2505` 呼叫（parse 完整後）。分派後執行走 `call()`：

```
$ global -x call
call             3524 server.c    void call(client *c, int flags) {
```

`call` 執行 `c->cmd->proc(c)`（函式指標分派到具體 handler）。GET 的 handler：

```
$ global -x getCommand
getCommand        316 t_string.c    void getCommand(client *c) {
```

完整鏈（費曼式）：`aeMain`(7251) 事件迴圈偵測 socket 可讀 → `readQueryFromClient` 讀進 `c->querybuf`、parse 成 `c->argv[]` → `processCommand`(3884) 查表分派 → `call`(3524) 執行 `c->cmd->proc(c)` → `getCommand`(t_string.c:316) 從 db 取 key 寫回 client buf。

**clangd vs gtags 決策**：redis `bear -- make` 能生 compile db，clangd `gd` 精準（能辨析 `call` 這種常見名）。但上面全用 gtags 對照示範——若讀的是 kernel 那種編不起來的，clangd `gd` 對 `call` 會失效或給錯的同名符號，退 gtags 的 `global -x call` 照樣秒回。這就是 Part 4→5 的後備價值。

### M3 外化

harpoon 釘四個熱點：#1 `aeMain`(server.c:7251)、#2 `processCommand`(server.c:3884)、#3 `call`(server.c:3524)、#4 `getCommand`(t_string.c:316)。`<leader>1-4` 在這條鏈的四個關鍵點瞬移。

notes.md 三欄（摘要）：

```markdown
## 假設
- [x] H1: aeMain 是唯一主迴圈 —— 已驗證，global -rx 只有 server.c:7251 這個實際呼叫
## 問題
- [x] Q1: 命令怎麼從 argv[0] 查到 handler？—— 見 F2
## 發現
- F1: 主迴圈 aeMain(server.c:7251)，事件驅動
- F2: processCommand 查表分派，call() 執行 c->cmd->proc(c)（函式指標）
- F3: getCommand(t_string.c:316) 是 GET 的落地
```

（H1 從假設流動到已驗證，Q1 從問題流動到發現 F2——三欄在流動。）

session：關 nvim 前 persistence 自動存 `%home%...%redis.vim`，下次 `<leader>qs` 還原這四檔的佈局。

### 我用了哪些鍵位/工具

| 階段 | 用的 | 幹嘛 |
|---|---|---|
| 偵察 | `<leader>ff`, rg | 看 src/ 分層、找 main |
| 建圖 | `global -x/-rx`, `<leader>ds` | 定位主迴圈、單檔地圖 |
| 追鏈 | `gd`/`gr`, `Ctrl-o`, gtags | 追分派、反查 caller、後備對照 |
| 外化 | `<leader>a`, `<leader>1-4`, notes.md | 釘熱點、瞬移、三欄 |
| 現場 | persistence `<leader>qs` | 凍結/還原 |

一個小時的精簡版，就把移動/搜尋/結構/語意/後備/外化/佈局全串了一遍。

</details>

## 做完你站在哪

跑完這個 Final，你**不只有一台讀碼機器，你證明了自己能用它冷啟動攻堅**。這是 `reading_code` 的方法 + 這門課的手，合一了：

- 你能拿一個沒讀過的大 C 專案，**只用鍵盤 + 這台機器**，在一天內從「完全看不懂」推進到「畫出主流程、追通一條 call chain、外化成報告」。
- clangd 跪了你不慌——`:LspInfo` 判斷、退 gtags，繼續跑。
- 你的攻堅現場（螢幕/熱點/思緒）能凍結、能還原，讀好幾天不用每天從零重建。
- 這套導航肌肉**兩邊通用**：接回 `codebase_case_studies` 的六個 codebase 讀 source，接回 `reverse_engineering` 讀反組譯輸出——同一套 `gd`/harpoon/quickfix。

## 自我檢核

- [ ] 我全程只用了 nvim + CLI 工具，沒開別的編輯器/GUI 瀏覽器嗎？
- [ ] 我的 call chain 每一跳都有行號和「資料變成什麼」的標註，不是憑印象嗎？
- [ ] 我**真的展示了 clangd → gtags 的後備決策**，而不是只用 clangd 或只用 gtags 嗎？
- [ ] 我的 harpoon 熱點是這條路徑的核心 4-5 個，`<leader>1-4` 瞬移過嗎？
- [ ] notes.md 三欄有流動（問題→發現 / 假設→驗證）嗎？session 能還原嗎？
- [ ] 對照「用到本課哪些章」那張表，我打了幾個勾？沒打的是哪個 Part、代表我哪裡還不熟？
- [ ] **最終問題**：再丟一個沒讀過的中型 C 專案給我，我有沒有信心只用這台機器一天攻下來？

如果最後一題答案是「有」——恭喜，你出師了。這門課的全部價值，就濃縮在「打開一個陌生 repo，你的手知道下一步按什麼」這句話裡。

← [回到總目錄](./README.md)
