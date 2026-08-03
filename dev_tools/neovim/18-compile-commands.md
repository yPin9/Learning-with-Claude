# Ch 18 — compile_commands.json：clangd 精準的前提

> **目標**：補上 Ch 17 留下的那塊拼圖。clangd attach 了、capabilities 都是 `true`，跳轉卻歪掉或跳不到——十之八九是缺 `compile_commands.json`。這章講清楚為什麼 clangd 非要它不可、三種主流生法（bear / CMake / compiledb）、clangd 怎麼找它、以及沒有它時 clangd 的降級行為。我們會用一個真專案，親眼看到「有 vs 沒有 compile_commands.json」時同一個 `gd` 跳到不同地方。這是讀碼者最常卡的一章。

> **環境**：Neovim v0.12.4，WSL2 / Ubuntu，clangd 14、bear 3.x。本章「有 vs 沒有 compile_commands.json」的跳轉差異，是用 headless 對同一份 C 專案跑 clangd-14 兩次照抄的。

## 為什麼需要這個？clangd 要「怎麼編這檔」的資訊

Ch 17 一直強調 clangd 的殺手鐧是「它是真正的編譯前端」。但這句話有個直接後果：**要編譯一個 C 檔，你得知道怎麼編它**。同一個 `foo.c`，用不同旗標編出來是不同的程式：

```c
#include "config.h"          // 這 header 在哪？要 -I 告訴它
#ifdef USE_EPOLL              // 這分支存不存在？要 -D 決定
    epoll_wait(...);
#endif
long x = 1L;                  // long 幾 bit？看 target
```

- `-I../include`：`config.h` 從哪個目錄找？沒有這個，clangd 連 include 都解不開，整檔紅一片。
- `-DUSE_EPOLL`：`#ifdef USE_EPOLL` 這段到底算不算存在？macro 定義直接決定 clangd 看到的是哪份 code。
- `-std=c11` / `-std=gnu11`：語言標準版本，影響哪些語法合法、哪些關鍵字存在。

**這些資訊 gcc 編譯時你在 Makefile / CMake 裡給了，但 clangd 不知道。** clangd 不會去讀你的 Makefile（Makefile 有幾百種寫法，它讀不了）。它需要一份標準化的清單，告訴它「每個檔是用哪條完整命令編的」。這份清單就是 **`compile_commands.json`**（業界叫它 compilation database，簡稱 CDB）。

**沒有它，clangd 瞎編**——它猜一組預設旗標（沒有你的 `-I`、沒有你的 `-D`），結果 include 解不開、conditional compilation 猜錯、跨檔跳轉失效。這是 clangd 第一大坑，也是「clangd 裝好了怎麼還是不會跳」的頭號原因。

## 先建立直覺：compile_commands.json 長什麼樣

它就是一個 JSON 陣列，每個元素是「一個編譯單元怎麼編」。拿一個小專案（`main.c` + `geometry.c`），用 bear 生出來的真實內容（照抄）：

```json
[
  {
    "arguments": [
      "/usr/bin/cc", "-Wall", "-std=c11", "-c", "main.c"
    ],
    "directory": "/tmp/demo",
    "file": "/tmp/demo/main.c"
  },
  {
    "arguments": [
      "/usr/bin/cc", "-Wall", "-std=c11", "-c", "geometry.c"
    ],
    "directory": "/tmp/demo",
    "file": "/tmp/demo/geometry.c"
  }
]
```

三個欄位就是全部：

- **`file`**：哪個檔。
- **`directory`**：在哪個目錄下編的（相對路徑的基準點）。
- **`arguments`**（或 `command`）：**完整的編譯命令**——編譯器、所有旗標、所有 `-I`/`-D`。這是精髓。

clangd 拿到某個檔的這條命令，就知道「照這條命令的旗標，把這檔編一遍」，於是它看到的世界跟你真正 build 出來的世界一致。真專案（如 Lua）這個檔案會有幾十筆，kernel 會有幾萬筆，格式都一樣。

## 三種生法

你幾乎**不會手寫** compile_commands.json——它是從你的 build 系統自動生出來的。三種主流方法，看你的專案用什麼 build：

### 方法一：bear（攔截任何 build，最通用）

**bear**（Build EAR）的原理漂亮：它**攔截** build 過程中每一次編譯器呼叫，把命令記下來，生成 CDB。它不管你用 Makefile、shell script、還是什麼奇怪的 build 系統——只要最終有呼叫 `gcc`/`cc`/`clang`，bear 就攔得到。

用法就是在你原本的 build 命令前面加 `bear --`：

```bash
bear -- make              # 攔截 make 的所有編譯
bear -- ./build.sh        # 攔截任何腳本
bear -- make -j8          # 平行 build 也行
```

真跑一個小專案（照抄末幾行 + 生成結果）：

```
$ bear -- make
cc -Wall -std=c11 -c main.c
cc -Wall -std=c11 -c geometry.c
cc -Wall -std=c11 -o main main.o geometry.o -lm
$ ls compile_commands.json
compile_commands.json
```

bear 是讀陌生第三方專案的**首選**——因為你不知道它用什麼 build 系統，bear 一律通吃。它的原理是用 `LD_PRELOAD`（或 macOS 的等價機制）插進 exec 呼叫鏈，攔下每個 compiler 執行。**代價**：你得能真的 build 起來。build 缺依賴、缺 header、根本編不過，bear 就攔不到那些檔（它只記錄「真的被執行的」編譯）。這是 Part 5 tags 後備存在的理由——有些樹（如你沒有完整 toolchain 的 kernel 子系統）根本 build 不起來，clangd 就無從精準，只能退回 gtags。

### 方法二：CMake（一個旗標的事）

如果專案用 CMake，根本不用 bear——CMake 內建就能生 CDB，加一個旗標：

```bash
cmake -B build -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
```

它會在 `build/` 下生 `compile_commands.json`。很多用 CMake 的專案（LLVM、大量現代 C++ 專案）這是最乾淨的方式，因為 CMake 本來就知道每個檔怎麼編，不用攔截。通常你會把它 symlink 回專案根，clangd 才好找（見下節）：

```bash
ln -s build/compile_commands.json compile_commands.json
```

### 方法三：compiledb（給 make 專案的替代）

`compiledb`（pip 裝）是另一條路：它**不真的 build**，而是 dry-run 你的 make（`make --dry-run`），解析出編譯命令：

```bash
compiledb make          # 解析 make 會執行什麼，不真的編
```

優點：不用真跑編譯，快。缺點：靠解析 make 輸出，遇到複雜/非標準的 Makefile 可能解不對。**通用性 bear > compiledb**——bear 攔真實執行，compiledb 靠猜 make 意圖。我的預設：能 build 就用 bear（最準），CMake 專案用 CMake 旗標，只有「build 很慢又只想快速要個 CDB」時才用 compiledb。

| 方法 | 原理 | 適用 | 要真 build 嗎 |
|---|---|---|---|
| **bear** | 攔截真實編譯呼叫 | 任何 build 系統，第三方專案首選 | 是 |
| **CMake** | build 系統原生輸出 | CMake 專案 | 否（configure 即可） |
| **compiledb** | 解析 `make --dry-run` | make 專案、不想真 build | 否 |

## clangd 怎麼找它

生好 compile_commands.json 放哪？clangd 的搜尋規則：**從你打開的檔案所在目錄開始，往上一層層找**，直到找到一個 `compile_commands.json`（或 `build/compile_commands.json`）。

```
你打開 /proj/src/foo.c
   │  clangd 往上找 compile_commands.json：
   ├─ /proj/src/            有嗎？沒有 → 往上
   ├─ /proj/                有嗎？有！  ← 用這個
   └─ （找到就停）
```

所以慣例是**放在專案根**。CMake 生在 `build/` 的，要 symlink 回根（上面那條 `ln -s`），或用 `.clangd` 設定檔指定路徑（Ch 21）。

驗證 clangd 有沒有找到它，最快的是 `clangd --check`（不需編輯器，模擬打開一個檔）：

```
$ clangd --check=src/foo.c 2>&1 | grep -i "compilation database"
I[..] Loaded compilation database from /proj/compile_commands.json   ← 找到了
```

反過來，沒找到時：

```
$ clangd --check=main.c 2>&1 | grep -i database
I[..] Loading compilation database...
I[..] Failed to find compilation database for /tmp/nocc/main.c        ← 沒找到！
```

看到 `Failed to find compilation database` 就是問題根源。這是你 debug「clangd 為何不準」的第一個檢查點。

## 降級行為：沒有它，clangd 變單檔模式

沒有 compile_commands.json 時 clangd 不會直接死掉——它退化成**單檔模式**（single-file mode）：用一組猜的預設旗標盡力分析當前這一個檔。結果就是**時準時不準、跨檔跳轉不可靠**。

我們親眼看一次。同一個 `main.c`，裡面呼叫了 `distance()`，這個函式**宣告在 `geometry.h`、定義在 `geometry.c`**。在 `distance()` 上按 `gd`，正確答案應該是跳到定義（`geometry.c:4`）。

**有 compile_commands.json 時**（headless 對 `distance` 呼叫點跑 `textDocument/definition`，照抄）：

```
=== gd on distance WITH compile_commands.json ===
  -> /tmp/demo/geometry.c : line 4     ← 跳到「定義」，正確
```

**把 compile_commands.json 拿掉，同一個 `gd`**（照抄）：

```
=== gd on distance WITHOUT compile_commands.json ===
  -> /tmp/nocc/geometry.h : line 9     ← 只跳到「宣告」，沒跳到定義！
```

看到差別了嗎。**沒有 CDB，clangd 只能跳到同目錄 header 裡的宣告，跳不進 `geometry.c` 的真定義**——因為它不知道 `geometry.c` 也是這個專案的編譯單元（那份資訊在 compile_commands.json 裡），它的跨檔 index 建不起來。你以為「跳到定義」了，其實只跳到原型；你想看實作，它給你看宣告。這種**「看起來有反應、其實跳錯層」**最坑——比完全不動更難察覺，因為它沒報錯。

在真專案這個降級更嚴重：`#include` 解不開（缺 `-I`）→ 整檔紅底報錯 → 型別全部 unknown → hover 沒東西、find-references 漏一堆。單檔模式對「只有一個檔、沒 include 別人」的玩具尚可，對任何真實的多檔 C 專案基本廢掉。

> **這就是 Ch 17 那句「attach ≠ 精準跳轉」的實證。** clangd 好好地 attach 了、capabilities 全 `true`，但沒有 CDB，`gd` 就跳到宣告而非定義。讀碼者卡在這裡的比例極高，因為 attach 成功給人「應該會動了」的錯覺。

## headless 驗證：確認 clangd 載入了 CDB

除了 `clangd --check`，你也可以在 Neovim 裡驗證 clangd 真的吃到了 CDB——最直接的證據就是**跨檔跳轉會對**。用 Ch 17 的驗證框架，對有 CDB 的專案跑（照抄）：

```
=== gd on distance (WITH compile_commands.json) ===
  -> /tmp/demo/geometry.c : line 4 char 7
```

跳到 `geometry.c:4`（`double distance(Point a, Point b) {` 的定義），這就是 clangd 吃到 CDB、跨檔 index 建起來的鐵證。反之若你在真 Neovim 裡發現 `gd` 只跳到 `.h`，八成是 CDB 沒被找到——去 `clangd --check` 看那行 database 訊息。

## 鍵位表

這章沒有新鍵位——它是「讓 Ch 17 那些鍵真的準」的前提。但有幾個**命令列動作**是讀碼流程的固定步驟，值得記成肌肉：

| 場景 | 命令 | 作用 |
|---|---|---|
| 進一個 make 專案 | `bear -- make` | 生 compile_commands.json |
| 進一個 CMake 專案 | `cmake -B build -DCMAKE_EXPORT_COMPILE_COMMANDS=ON` | 生 CDB 到 build/ |
| CMake 的 CDB 接回根 | `ln -s build/compile_commands.json .` | 讓 clangd 找得到 |
| 驗證 clangd 有吃到 | `clangd --check=<file>` | 看 "Loaded compilation database" |
| 在 Neovim 內確認 | 對跨檔符號按 `gd`，看跳定義還是只跳宣告 | 判斷 CDB 是否生效 |

## 對比與取捨

| | bear | CMake 旗標 | compiledb | 手寫 |
|---|---|---|---|---|
| 通用性 | 最高（攔任何 build） | 只限 CMake | make 專案 | 任何但地獄 |
| 準確度 | 最高（真實命令） | 最高 | 中（靠解析） | 看你 |
| 要能 build | 是 | 否（configure 即可） | 否 | 否 |
| 讀第三方專案首選 | ✓ | （若它用 CMake） | | |

## 踩雷集錦

1. **clangd 裝好、attach 成功，跳轉卻歪**：頭號原因是缺 compile_commands.json。先 `clangd --check=<file>` 看有沒有 `Failed to find compilation database`。這比查 Neovim 設定快十倍。

2. **compile_commands.json 建一次就永遠有效**：錯。它是**快照**。你 `git pull` 進來新檔、改了 build 旗標、加了新的 `-D`，舊的 CDB 對不上新 code，clangd 對新檔一無所知。改動 build 後要**重建**（`bear -- make` 再跑一次）。

3. **CMake 生在 build/ 但 clangd 找不到**：clangd 從檔案往上找根目錄的 `compile_commands.json`，不會自動鑽進 `build/`（它會找 `<root>/compile_commands.json` 和 `<root>/build/compile_commands.json` 但路徑慣例各異）。最保險是 symlink 回專案根，或 `.clangd` 明寫 `CompilationDatabase: build`。

4. **bear 攔不到「沒被編譯的檔」**：bear 只記錄 build 過程**真的執行了**的編譯命令。如果某個檔因為條件而沒被編（另一個平台的實作、被 `#if 0` 掉的模組），它不在 CDB 裡，clangd 對它退回單檔模式。這是為什麼有些檔就是不準——它根本沒進 CDB。

5. **build 編不過就沒有 CDB**：bear/CMake 都要你的 build 能跑（至少跑到編譯階段）。缺 toolchain、缺依賴、cross-compile 環境沒配好——build 失敗，CDB 空或殘缺。這種「編不起來的樹」正是 Part 5 gtags 後備的用武之地：純語法索引不需要能編譯。

6. **絕對路徑 vs 相對路徑**：CDB 裡 `file` 若是相對路徑，`directory` 是它的基準。搬動專案目錄後路徑對不上，clangd 找不到檔。跨機器/搬目錄後最好重建 CDB。

## 進階：再往深一層

- **`.clangd` 覆寫旗標**：專案根放一個 `.clangd` YAML（Ch 21 詳講），可以在 CDB 之外**追加/移除**旗標——例如專案用了 clang 不認得的 gcc 特有旗標，可以 `CompileFlags: { Remove: [-mno-...] }` 把它拿掉，止住假紅線。這是讀「clangd 水土不服」的第三方專案的救命工具。

- **`clangd --check` 當 CI 健檢**：對關鍵檔批次跑 `--check`，能自動找出「哪些檔 clangd 分析不了」（通常是 CDB 缺項）。想確認整個專案 clangd 都能吃，這是最快的體檢。

- **kernel 的 CDB**：Linux kernel 有自己的腳本 `scripts/clang-tools/gen_compile_commands.py`，從 kernel build 的 `.cmd` 檔生 CDB，不用 bear。但 kernel 的 `#ifdef` 迷宮讓 clangd 只看到「你這份 `.config` 的世界」（Ch 21 深講），這是 clangd 讀 kernel 的根本局限，也是 gtags 在 kernel 場景不可取代的原因。

- **compile_flags.txt（窮人版）**：對於簡單專案，你甚至可以不生完整 CDB，只放一個 `compile_flags.txt`（一行一個旗標，如 `-I./include`、`-std=c11`），clangd 會對**所有檔**套用這組旗標。適合「所有檔編法都一樣」的小專案，省掉 build 一次的成本。

## 本章重點整理

- clangd 是真編譯前端，所以它需要知道**每個檔怎麼編**（`-I`/`-D`/`-std`）——這份資訊就是 `compile_commands.json`（CDB）。
- CDB 是 JSON 陣列，每筆記 `file` / `directory` / `arguments`（完整編譯命令）。你不手寫，從 build 系統生。
- 三種生法：**bear**（攔任何 build，第三方首選）、**CMake**（`-DCMAKE_EXPORT_COMPILE_COMMANDS=ON`）、**compiledb**（解析 make dry-run）。
- clangd 從檔案往上找根目錄的 CDB；CMake 生在 build/ 的要 symlink 回根。
- **沒有 CDB，clangd 退成單檔模式**：跨檔跳轉失效、`gd` 只跳到宣告而非定義、include 解不開。實證：同一個 `gd`，有 CDB 跳 `geometry.c:4`（定義），沒 CDB 只跳 `geometry.h:9`（宣告）。
- CDB 是**快照**，改 build 後要重建。

## 自我檢核

- [ ] 我能說出 clangd 為什麼非要 compile_commands.json（要知道 `-I`/`-D`/`-std`）
- [ ] 我能講出 bear / CMake / compiledb 三種生法各自的原理與適用場景
- [ ] 我知道 clangd 怎麼找 CDB（從檔案往上找根目錄），CMake 的要怎麼接回去
- [ ] 我能描述「沒有 CDB 時」clangd 的降級行為，並知道「gd 只跳到宣告」是徵兆
- [ ] 我會用 `clangd --check=<file>` 確認 CDB 有沒有被載入
- [ ] 我知道 CDB 是快照，改 build 後要重建；也知道 build 編不過就沒有 CDB

## 延伸閱讀

### 官方文件（優先）

- **[clangd — Compile Commands](https://clangd.llvm.org/design/compile-commands)**
  - **讀哪裡**：clangd 怎麼找 CDB、搜尋順序、`compile_flags.txt` 的替代方案。本章「clangd 怎麼找它」的權威依據。
- **Neovim `:help lsp-root_dir`**（或 `:help vim.lsp.config`）
  - **讀哪裡**：Neovim 怎麼判定專案 root——這跟 clangd 找 CDB 的目錄息息相關。root 認錯，clangd 就從錯的地方找 CDB。

### 工具

- **[bear 官方 README](https://github.com/rizsotto/Bear)**
  - **讀哪裡**：用法與原理（LD_PRELOAD 攔截）、遇到 wrapper 編譯器時的坑。理解它「攔真實執行」的本質。
- **[CMake `CMAKE_EXPORT_COMPILE_COMMANDS`](https://cmake.org/cmake/help/latest/variable/CMAKE_EXPORT_COMPILE_COMMANDS.html)**
  - **讀哪裡**：這個旗標的行為與限制（只支援 Makefile/Ninja generator）。

### 橫向連結

- **`soft_skills/reading_code` Ch 13**「LSP 與語意導航」的 `--check` 段落
  - 那章示範 `clangd --check` 對 redis 的完整輸出（找到 CDB → 建 preamble → 建 AST），跟本章的降級對照著看，能完整理解 clangd 的工作流程。

CDB 就位、clangd 精準了。下一章是 Part 4 的重頭戲：語意導航——`gd`/`gr`/`gi`/`K`/call hierarchy，把 clangd 當逆向探針，追 data flow、找影響面、溯源呼叫鏈。

→ [Ch 19 語意導航：gd / gr / call hierarchy](./19-semantic-navigation.md)
