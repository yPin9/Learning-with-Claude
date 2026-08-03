# Ch 7 — buffer / window / tab：多檔對照讀

> **目標**：搞清楚 Neovim 三個常被搞混的概念——**buffer（開啟的檔）、window（視口）、tab（window 佈局）**，並學會用它們**多檔對照讀**：左邊看 caller、右邊看 callee，或一個檔看 `.c`、旁邊看它的 `.h`。這是讀大專案的關鍵能力——讀碼很少只盯一個檔，你要在幾個相關檔之間對照。這一章也會說清楚為什麼**讀碼該是 buffer-centric（以檔為中心）而非 tab-centric**，那是很多從 VSCode 過來的人一開始搞反的地方。

> **環境**：Neovim v0.12.4，WSL2。範例用 Lua 源碼（`lvm.c` 配它的 header `lvm.h`、`lobject.h`）。

## 為什麼需要這個？

追一條 call chain（Ch 5）時，你常想**同時看見**呼叫端和被呼叫端。`luaV_tointeger` 呼叫 `luaV_flttointeger`，你想左邊擺 `luaV_tointeger`、右邊擺 `luaV_flttointeger`，對照它們怎麼銜接——參數怎麼傳、回傳怎麼用。單一視窗跳來跳去也行（`gd` 進、`Ctrl-O` 回），但**對照**時「兩個都看得到」比「跳來跳去」省腦力得多，因為你不用把另一邊記在腦子裡。

讀 C 尤其需要：`.c` 的實作旁邊擺 `.h` 的宣告，一邊看 struct 定義一邊看它怎麼被用。或讀一個 dispatch 表時，左邊擺 opcode 定義（`lopcodes.h`），右邊擺 handler（`lvm.c`）。

問題是，很多人（尤其 VSCode/分頁瀏覽器習慣的人）一來就狂開 tab，每個檔一個 tab，然後在幾十個 tab 間迷失。Neovim 的模型不是這樣——它的三層（buffer/window/tab）各司其職，**用對了多檔對照非常順，用錯了就是一團亂 tab**。先把三者理清楚。

## 先建立直覺：檔、視口、佈局是三層

```
    ┌─ TAB 1（一種 window 佈局）──────────────────┐
    │  ┌── window A ──┐  ┌── window B ──┐          │
    │  │ 顯示 buffer  │  │ 顯示 buffer  │          │
    │  │   lvm.c      │  │   lvm.h      │          │  ← 兩個 window
    │  └──────────────┘  └──────────────┘          │     並排（vsplit）
    └──────────────────────────────────────────────┘

    buffer 清單（在記憶體裡開著的檔，不一定顯示）：
      1: lvm.c    2: lvm.h    3: lobject.h    4: lstate.h
         ↑ 顯示中   ↑ 顯示中     ↑ 開著但沒顯示   ↑ 開著但沒顯示
```

三層定義：

- **buffer**：一個**被載入記憶體的檔**。你打開 20 個檔就有 20 個 buffer——**不管有沒有顯示出來**。buffer 是「檔的內容 + 你的游標位置 + undo 歷史」。
- **window（視口）**：一個**看 buffer 的視口**。一個 window 顯示一個 buffer。分割螢幕（split）= 開多個 window。
- **tab（分頁）**：一組 **window 的佈局**。注意——**tab 不是「一個檔」**，是「一種螢幕分割方式」。一個 tab 可以有好幾個 window 各看不同 buffer。

關鍵誤解拆解：**tab ≠ 檔案**。在 VSCode/瀏覽器，一個 tab 就是一個檔。在 Vim，**一個檔是一個 buffer**，tab 是「你怎麼排版視窗」。你可以開 50 個 buffer，卻只用 1 個 tab、1 個 window——因為 buffer 不需要各自的視口才能存在。這是關鍵心智翻轉。

## 核心一：buffer——切換你開著的檔

buffer 是「檔」這一層。常用操作：

- `:e <file>`：開一個檔成新 buffer（edit）
- `:ls` 或 `:buffers`：列出所有 buffer（看你開了哪些、編號多少）
- `:b <N>`：切到第 N 號 buffer
- `:b <部分檔名><Tab>`：按檔名（子字串）切 buffer，Tab 補全
- `:bn` / `:bp`：下一個 / 上一個 buffer（next/prev）
- `Ctrl-^`（或 `Ctrl-6`）：**切回上一個 buffer**（兩檔來回的神鍵）
- `:bd`：關掉一個 buffer（buffer delete）

**`Ctrl-^` 是這章最該練成反射的鍵**。它在「當前 buffer」和「上一個 buffer」之間切換——就是 buffer 版的「上一頁」。讀碼場景：你在 `lvm.c`，`gf` 跳進 `lvm.h` 看個宣告，`Ctrl-^` 一鍵跳回 `lvm.c`，再 `Ctrl-^` 又回 `lvm.h`。**兩個檔來回對照，不用記檔名、不用重開。** 這是 buffer-centric 工作流的核心動作。

`:b lvm<Tab>` 這種模糊切 buffer 也很順：你已經開了 `lvm.c`，打 `:b lvm.c<CR>`（或子字串 + Tab 補全）就切過去。但實務上大家更愛 telescope 的 buffer picker（Part 2 Ch 9，`<leader>fb` 模糊搜已開 buffer），比 `:b` 打字快。

## 核心二：window（split）——多檔對照的關鍵

window 是「視口」這一層。分割螢幕就是開多個 window：

- `:sp [file]` / `Ctrl-w s`：**水平**分割（split，上下）
- `:vsp [file]` / `Ctrl-w v`：**垂直**分割（vsplit，左右）——**讀碼對照最常用**
- `Ctrl-w h/j/k/l`：在 window 間移動（左/下/上/右，方向同 hjkl）
- `Ctrl-w w`：循環到下一個 window
- `Ctrl-w q` / `Ctrl-w c`：關掉當前 window（quit/close）
- `Ctrl-w o`：只留當前 window，關掉其他（only）

**讀碼對照的黃金組合是 vsplit（左右並排）**：

```
    :vsp lobject.h     ← 右邊開 lobject.h（看 struct 定義）
    Ctrl-w h           ← 游標回左邊的 lvm.c（看實作）
    ...左邊讀實作，一有不懂的 struct 欄位，看右邊的定義...
```

實戰：讀 `luaV_execute` 時，左邊 `lvm.c` 看迴圈，右邊 `:vsp lopcodes.h` 看 opcode 定義。左看「怎麼 dispatch OP_ADD」，右看「OP_ADD 是第幾號、格式是什麼」。兩邊都在眼前，不用把 opcode 定義背在腦子裡。`Ctrl-w h`/`Ctrl-w l` 在兩邊切游標。

**resize（調整視窗大小）**：

- `Ctrl-w =`：所有 window 等寬等高
- `Ctrl-w _` / `Ctrl-w |`：當前 window 最大化高 / 寬
- `Ctrl-w <` / `Ctrl-w >`：變窄 / 變寬（配 count，`10 Ctrl-w >` 加寬 10 欄）
- `Ctrl-w +` / `Ctrl-w -`：變高 / 變矮

讀碼常把「當前在讀的那個 window」加寬（`Ctrl-w |` 或 `Ctrl-w >`），參考的那個縮小——你主要盯一邊，另一邊瞄一眼。

**在 split 裡跳定義**：`Ctrl-w ]`（在新 split 開游標下符號的 tag 定義，Part 5）、`Ctrl-w d`（LSP：在 split 開定義，需設定）、`Ctrl-w f`（在 split 開游標下的檔案，`gf` 的分割版——讀 `#include` 時「右邊開這個 header」）。這些讓「跳定義」變成「在旁邊開一個新 window 對照」而非「跳走離開當前檔」。

## 核心三：tab——別拿它當檔案用

tab 是「佈局」這一層：

- `:tabnew [file]`：開新 tab
- `gt` / `gT`：下一個 / 上一個 tab
- `<N>gt`：跳到第 N 個 tab
- `:tabc`：關當前 tab

tab 的**正確**用途：**不同的工作情境用不同佈局**。例如 tab 1 是「讀 VM」的佈局（`lvm.c` + `lopcodes.h` 並排），tab 2 是「讀 GC」的佈局（`lgc.c` + `lstate.h` 並排）。每個 tab 是一組為某個子任務排好的 window——**tab = 工作區，不是檔案**。

**錯誤**用途（VSCode 習慣）：一個檔一個 tab，開幾十個。這在 Vim 很難用，因為 Vim 的 tab 沒有 VSCode 那種「檔名分頁條 + 點擊切換」的順手 UI，`gt`/`3gt` 在很多 tab 間巡很累。**檔案的切換交給 buffer（`Ctrl-^`、telescope），不是 tab。**

## 為什麼讀碼是 buffer-centric？

這是本章最重要的觀念。**buffer-centric = 所有開著的檔平等地躺在一個 buffer 清單裡，你用模糊搜尋/`Ctrl-^` 在它們間跳，不需要每個檔佔一個視覺分頁。**

為什麼適合讀碼：

1. **讀大專案你會碰幾十上百個檔**。若每檔一個 tab，分頁條爆炸、`gt` 巡到崩潰。buffer 清單無所謂多少個——反正你靠模糊搜尋（telescope）按名字瞬間跳到任一個，不靠「肉眼掃分頁條」。
2. **讀碼的檔是「用完就走」的**：你跳進 `lmem.c` 看一個函式，看完就不再需要它顯示著。buffer 讓它留在清單裡（要回頭再切回去），但不佔螢幕。tab 模型會逼你「關掉它」或「留一個沒用的分頁」。
3. **window 負責「對照」，buffer 負責「切換」，tab 負責「不同任務的佈局」**——三者分工。多數讀碼時間你用 1 個 tab、1–2 個 window（對照時 vsplit），加一個很長的 buffer 清單就夠。tab 只在你要平行推進兩個不相干的子任務時才開。

VSCode 把三層壓成「一排分頁」，簡單但不 scale 到大專案讀碼。Vim 的三層分開，學習成本高一點，但**幾十個檔的對照讀**它才撐得住。Part 6（Ch 27）會講怎麼用 session 把一整套佈局存下來，Part 2 的 telescope buffer picker（`<leader>fb`）是 buffer-centric 的日常入口。

## 鍵位表

| 模式 | 按鍵 | 作用 |
|---|---|---|
| n | `Ctrl-^` / `Ctrl-6` | **切回上一個 buffer（兩檔來回，最常用）** |
| c(ex) | `:ls` / `:buffers` | 列出所有 buffer + 編號 |
| c(ex) | `:b <N>` / `:b <名><Tab>` | 切到第 N / 按檔名切 buffer |
| c(ex) | `:bn` / `:bp` | 下一個 / 上一個 buffer |
| c(ex) | `:bd` | 關掉 buffer |
| c(ex) | `:vsp [file]` | **垂直分割（左右對照，讀碼最常用）** |
| c(ex) | `:sp [file]` | 水平分割（上下） |
| n | `Ctrl-w h/j/k/l` | 在 window 間移動（左/下/上/右） |
| n | `Ctrl-w w` | 循環到下一個 window |
| n | `Ctrl-w c` / `Ctrl-w o` | 關當前 window / 只留當前 |
| n | `Ctrl-w =` | 所有 window 等大 |
| n | `Ctrl-w \|` / `Ctrl-w _` | 當前 window 最大化寬 / 高 |
| n | `Ctrl-w >` / `Ctrl-w <` | 加寬 / 變窄（配 count） |
| n | `Ctrl-w f` | 在新 split 開游標下的檔案（`gf` 的對照版） |
| n | `:tabnew` / `gt` / `gT` | 開新 tab / 下 / 上一個 tab |

## 對比與取捨

| 你想做的事 | 用哪一層 | 動作 |
|---|---|---|
| 兩個檔來回對照切換 | buffer | `Ctrl-^` |
| caller / callee 並排同時看 | window | `:vsp` + `Ctrl-w h/l` |
| `.c` 旁邊看 `.h` | window | `:vsp lvm.h` |
| 按名字跳到某個開著的檔 | buffer | telescope `<leader>fb`（Part 2）或 `:b 名<Tab>` |
| 兩個不相干子任務各自的佈局 | tab | `:tabnew` 排一組新 window |
| 「跳定義但不離開當前檔」 | window | `Ctrl-w ]` / `Ctrl-w f`（在 split 開） |

| 模型 | buffer-centric（本課建議） | tab-centric（VSCode 習慣） |
|---|---|---|
| 一個檔對應 | 一個 buffer（不佔螢幕） | 一個分頁（佔分頁條） |
| 切換靠 | 模糊搜尋 / `Ctrl-^` | 肉眼點分頁 |
| 幾十個檔時 | 清單無壓力 | 分頁條爆炸 |
| 適合 | 讀大專案 | 少量檔 |

## 踩雷集錦

1. **把 tab 當檔案用，開一堆 tab**。這是 VSCode 習慣的最大坑。Vim 的 tab 是「佈局」不是「檔」。檔的切換用 buffer（`Ctrl-^`、telescope），tab 只在不同子任務時開。
2. **`:q` 想關檔卻關掉整個 window / 退出 nvim**。`:q` 關的是 **window** 不是 buffer。只有一個 window 時 `:q` 就退出 nvim。想關檔（buffer）留著 window 用 `:bd`；想只關這個 split 用 `Ctrl-w c`。
3. **`:bd` 後才發現 undo 歷史沒了**。`:bd` 把 buffer 從記憶體卸載，游標位置、undo 都清掉。讀碼時通常不必 `:bd`——讓 buffer 留著（反正靠模糊搜尋切換，不佔螢幕），要回去隨時切回，位置還在。
4. **找不到「上一個 buffer」是哪個**。`Ctrl-^` 只在最近兩個 buffer 間切。想看全部開著的、按名字跳，用 `:ls` 看清單或 telescope buffer picker。別靠記憶。
5. **vsplit 開錯邊 / 游標沒過去**。`:vsp file` 預設新 window 開在**左邊**且游標**在新 window**。想游標回原來那邊 `Ctrl-w l`（或 `h`，看方向）。搞不清就 `Ctrl-w w` 循環找回你要的 window。
6. **split 太多變迷宮**。讀碼對照通常 2 個 window 夠了（左實作右宣告）。開到 4、5 個 split 反而每個都太窄。要更多「檔」用 buffer 切換，不是無限 split。`Ctrl-w o` 一鍵收回單 window 重來。

## 進階：再往深一層

- **hidden 選項**：`vim.opt.hidden` 預設在 Neovim 是開的，意思是「buffer 有未存的改動也能切走（藏起來）不強迫你存」。讀碼幾乎不改檔，這無感；但知道它存在，才懂為什麼你能自由 `Ctrl-^` 亂切不被擋。
- **buffer 的四種狀態**（`:ls` 的標記）：`%`（當前）、`#`（上一個，`Ctrl-^` 的目標）、`a`（active，顯示中）、`h`（hidden，開著沒顯示）、`+`（有改動）。讀 `:ls` 輸出時這些標記告訴你每個 buffer 現在什麼狀態——`#` 就是按 `Ctrl-^` 會去的那個。
- **`:vert sb <名>`**：在垂直 split 裡開一個**已存在的** buffer（split buffer），不是重新讀檔。對照讀時「把已開的 `lvm.h` 拉到右邊 split」就用它，不用 `:vsp lvm.h` 重讀。
- **window-local vs buffer-local**：有些設定（如折疊 `foldmethod`）是 window-local，同一個 buffer 在兩個 window 可以有不同折疊狀態——這在對照讀「一邊折疊看大綱、一邊展開看細節」時有用（Ch 8 折疊詳談）。
- **Part 6 的 session（Ch 27）**：把「buffer 清單 + window 佈局 + tab」整組存進一個 session 檔，下次 `nvim -S` 回到一模一樣的讀碼現場。攻堅一個大專案跨好幾天時，這是不用每天重排佈局的關鍵。

## 本章重點整理

- 三層分工：**buffer=開著的檔（不一定顯示）、window=看 buffer 的視口、tab=window 的佈局**。**tab ≠ 檔案**——這是最關鍵的心智翻轉。
- 多檔對照靠 **window（vsplit 左右並排）**：`:vsp` + `Ctrl-w h/l`，左看 caller 右看 callee、左 `.c` 右 `.h`。
- 檔的切換靠 **buffer**：`Ctrl-^`（兩檔來回神鍵）、telescope 模糊切（Part 2）。**別拿 tab 當檔案分頁用**。
- 讀碼是 **buffer-centric**：幾十個檔平躺在 buffer 清單，靠模糊搜尋跳，不靠肉眼掃分頁——這才 scale 到大專案。
- tab 留給「不同子任務的不同佈局」，不是「一檔一頁」。

## 自我檢核

- [ ] 我能用自己的話講清楚 buffer / window / tab 的差別，並解釋為什麼 tab 不等於檔案
- [ ] 我知道要「兩個檔來回對照」按哪個鍵（`Ctrl-^`），要「兩個檔並排同看」用什麼（`:vsp`）
- [ ] 我知道 `:q`、`:bd`、`Ctrl-w c` 分別關掉的是什麼，不會誤退出 nvim
- [ ] 我能說出為什麼讀大專案該 buffer-centric 而非一檔一 tab
- [ ] 我能在 `Ctrl-w h/j/k/l` 之間移動游標並用 `Ctrl-w |` 加寬當前 window

## 延伸閱讀

- **Neovim `:help windows.txt`**
  - **讀哪裡**：開頭的 buffer/window/tab 三者定義（`:help window`、`:help buffer`）、`CTRL-W` 指令表、`:help buffer-hidden`；這是三層概念的權威來源
  - **重點**：`:help CTRL-W_f`、`CTRL-W_]` 這些「在 split 裡跳」的變體，對照讀很實用
- **Neovim `:help :buffers` / `:help CTRL-^`**
  - **讀哪裡**：`:ls` 輸出的狀態標記（`% # a h +`）意義、`Ctrl-^` 的定義；讀懂 `:ls` 才能掌握你開了哪些檔
- **`soft_skills/reading_code` Ch 8「data flow 追蹤」+ Ch 9「call graph」**
  - **讀哪裡**：追 caller/callee 的方法；本章的 vsplit 對照就是那套追蹤在「同時看兩端」時的視窗安排——方法在 reading_code，視窗擺法在這裡

多檔對照解決「同時看幾個檔」。但在**單一大檔**內，你還需要：標記幾個攻堅點反覆回來、把長檔折疊起來看大綱、把當前行對齊到螢幕中央看上下文。這些「大檔定位」技巧是 Part 1 的最後一塊，也是下一章。

→ [Ch 8 marks / folds / 捲動：大檔定位](./08-marks-folds-scrolling.md)
