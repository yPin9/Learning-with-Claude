# Ch 19 — .gdbinit、auto-load 與安全模型

> **目標**：把你在 session 調好的設定持久化，並理解 GDB 的啟動檔機制與 auto-load 安全模型。掌握 `~/.gdbinit`、per-project `.gdbinit`、command files、`source`、auto-load 的安全路徑（safe-path），以及為什麼 GDB 預設會「拒絕」載入某些 `.gdbinit`。

> **環境**：GDB 13/14，Linux x86_64。

## 為什麼設定持久化是分水嶺

每次開 GDB 都重打一遍 `set print pretty on`、`set disassembly-flavor intel`、`set scheduler-locking step`？這是業餘。把它們寫進 `.gdbinit`，GDB 一啟動就到位——這是熟手與新手的分水嶺之一。

更進一步，`.gdbinit` 能放自訂指令（Ch 20、Ch 21）、載入 Python 腳本（Part 5）、設定 per-project 的 pretty-printer。gef、pwndbg 本質就是一個塞進 `.gdbinit` 的大型腳本。理解這套機制，你才能組織自己的 debug 環境、最終發布自己的插件（Final Project）。

## GDB 啟動時讀哪些檔

GDB 啟動的載入順序（簡化）：

```
   1. 系統級：/etc/gdb/gdbinit（distro 提供，通常設 auto-load safe-path）
   2. 使用者級：~/.gdbinit 或 ~/.config/gdb/gdbinit（你的全域設定）
   3. 命令列 -x / -ix 指定的檔
   4. 載入要 debug 的 binary（file）
   5. auto-load：當前目錄的 .gdbinit、binary 相關的 -gdb.py 腳本（受 safe-path 約束）
```

`gdb --help` 與 `show data-directory` 可看細節。重點是有**三層** gdbinit：系統、使用者、專案。

## `~/.gdbinit`：你的全域設定

放你「每次都想要」的設定。一份務實的起手 `.gdbinit`：

```gdb
# ~/.gdbinit  ── 全域偏好

# === 顯示 ===
set print pretty on
set print array on
set print array-indexes on
set print elements 0          # 不截斷陣列（小心超大陣列）
set print null-stop on
set pagination off            # 不要每頁停下問 --More--（自動化必備）
set confirm off               # 不要每次都問「Are you sure?」（老手一律 off）
set disassembly-flavor intel  # Intel 語法（個人偏好；要 AT&T 就刪）

# === 多執行緒 ===
set scheduler-locking step    # 單步只動當前 thread（Ch 16 推薦）
set print thread-events on

# === 歷史 ===
set history save on
set history size 100000
set history filename ~/.gdb_history
set history remove-duplicates 1

# === Python / debuginfod ===
set python print-stack full
set debuginfod enabled on     # 自動下載 debug info（Ch 0）

# === 自訂 prompt（選配）===
# set prompt (gdb) 
```

逐項的理由前面章節都講過：`pagination off` + `confirm off` 是自動化與老手必備；`scheduler-locking step` 是多執行緒救命；`print elements 0` 解除陣列截斷（但超大陣列要小心）。

> GDB 13+ 也支援 XDG 路徑 `~/.config/gdb/gdbinit`，比家目錄塞一堆 dotfile 乾淨。兩者擇一。

## per-project `.gdbinit`：每個專案的設定

不同專案需要不同設定（特定 pretty-printer、自訂走訪指令、特定斷點）。GDB 支援讀**當前目錄**的 `.gdbinit`：

```gdb
# /path/to/myproject/.gdbinit
source ./scripts/my_printers.py    # 載入專案的 pretty-printer
break myproject_panic              # 專案常用斷點
define dumpstate
  ...
end
```

但這有安全問題（下節），預設**不會**自動載入，需要設定 safe-path。

## auto-load 安全模型：為什麼 GDB 拒絕你的 .gdbinit

想像這個攻擊：有人在某目錄放一個惡意 `.gdbinit`（裡面有 `shell rm -rf ~` 之類）。你 `cd` 進去、跑 `gdb ./something`，如果 GDB 自動執行那個 `.gdbinit`——你就被打了。

所以 GDB 預設**只信任特定路徑**的 auto-load 檔案。當你在一個未信任的目錄跑 GDB，會看到：

```
warning: File "/path/to/project/.gdbinit" auto-loading has been declined by your
`auto-load safe-path' set to "$debugdir:$datadir/auto-load".
To enable execution of this file add
        add-auto-load-safe-path /path/to/project/.gdbinit
line to your configuration file "/home/you/.gdbinit".
```

這不是 bug，是保護。解法（在 `~/.gdbinit` 加）：

```gdb
# 信任特定路徑
add-auto-load-safe-path /home/you/projects/

# 或啟用 local gdbinit 載入
set auto-load local-gdbinit on

# 看目前設定
# show auto-load safe-path
```

> 認識論誠實 + 安全警告：有人會直接 `set auto-load safe-path /`（信任所有路徑）圖方便。**這等於關掉保護**——任何你 cd 進去 debug 的目錄（下載的別人專案、CTF 題目附帶的檔案）都能對你執行任意指令。只 `add-auto-load-safe-path` 你信任的具體目錄（自己的專案根目錄），不要信任 `/`。

## auto-load 的另一面：`-gdb.py` 腳本

GDB 還會自動載入和 binary 關聯的 Python 腳本。當你 debug `/usr/lib/libfoo.so`，GDB 會找 `libfoo.so-gdb.py`（或 ELF 裡 `.debug_gdb_scripts` section 指定的）——這就是 libstdc++ 的 STL pretty-printer（Ch 30）怎麼「自動」出現的：libstdc++ 附帶 `libstdc++.so.6.0.xx-gdb.py`。

```
(gdb) info auto-load              # 看哪些 auto-load 腳本被載入/拒絕
(gdb) info auto-load python-scripts
```

理解這個，你發布自己的函式庫時，可以附帶 pretty-printer 讓使用者自動獲得漂亮的 `print`（Final Project 進階）。

## `source`：手動載入腳本

不想靠 auto-load，隨時手動載入命令檔或 Python：

```
(gdb) source ~/scripts/debug_helpers.gdb     # 載入 GDB 命令腳本
(gdb) source ~/scripts/my_plugin.py          # 載入 Python 腳本
```

命令列啟動時：

```bash
gdb -x script.gdb ./prog          # 啟動後執行 script.gdb
gdb -ix early.gdb ./prog          # 在載入 inferior「之前」執行
gdb -batch -x script.gdb ./prog   # 批次模式：跑完 script 就退出（CI/自動化）
gdb --command=script.gdb ...      # 同 -x
```

`gdb -batch -x` 是自動化的核心——寫一個腳本（下斷、run、印狀態、退出），一行跑完無互動。CI 的崩潰分析、批次測試都靠它。

## 一個完整的環境組織

```
~/.gdbinit                       # 全域偏好 + add-auto-load-safe-path ~/projects/
~/.config/gdb/                   # （選）放共用腳本
~/scripts/gdb/
  ├── pwn.py                     # 你的 pwn 輔助（context 視窗等）
  └── helpers.gdb                # 常用 define 指令
~/projects/myapp/.gdbinit        # 專案專屬：source 專案 printer、常用斷點
```

`~/.gdbinit` 裡 `source ~/scripts/gdb/helpers.gdb` 載入共用工具，`add-auto-load-safe-path ~/projects/` 信任你的專案。這套組織方式，最終會長成你自己的「gef」（Final Project）。

## 踩雷集錦

1. **per-project `.gdbinit` 沒被載入**：safe-path 沒包含該目錄。`add-auto-load-safe-path` 它，別用 `set auto-load safe-path /`（不安全）。
2. **`set auto-load safe-path /` 圖方便埋雷**：等於關掉保護，debug 任何不明專案都可能被打。只信任具體目錄。
3. **`.gdbinit` 裡的設定順序問題**：某些設定有依賴（例如 source 一個 Python 檔前要確認 Python 可用）。出錯時 `set python print-stack full` 看完整錯誤。
4. **CI 裡 GDB 卡住等輸入**：忘了 `set confirm off` + `set pagination off`，或沒用 `-batch`。自動化一定要這三件。
5. **`~/.gdbinit` 太肥拖慢啟動**：載入一堆 Python 插件會讓每次啟動變慢。考慮按需 `source` 而非全塞 `.gdbinit`。
6. **改了 `.gdbinit` 沒生效**：它只在「啟動時」讀一次。改完要重開 GDB，或手動 `source ~/.gdbinit`。

## 進階：再往深一層

- **`-nx` / `-nh`**：`gdb -nx` 不讀任何 gdbinit（debug 「是不是我的 .gdbinit 害的」時用）；`-nh` 只跳過家目錄的。
- **`set startup-quietly on`** / `-q`：抑制啟動的版權訊息。
- **command file 裡的控制流**：`.gdbinit` 與 command file 可用 `if/while/define`（Ch 20）寫複雜邏輯。
- **`gdb.events` 與啟動**：Python 腳本可掛 `gdb.events.new_objfile` 等，在特定時機自動執行——gef 的自動 context 就靠事件（Ch 25）。
- **環境變數**：`GDBHISTFILE`、`DEBUGINFOD_URLS` 等影響 GDB 行為，可在 shell rc 設。
- **發布插件的慣例**：一個成熟插件（如 gef）的安裝就是「下載 .py + 在 ~/.gdbinit 加一行 source」。理解這個，你的 Final Project 才知道怎麼讓別人安裝。

## 動手練習

1. 寫一份 `~/.gdbinit`，至少含本章範例的顯示、多執行緒、歷史三組設定。重開 GDB 確認生效。
2. 在某專案目錄放一個 `.gdbinit`（含一個 `define` 指令），不設 safe-path 時觀察 GDB 拒絕的警告；`add-auto-load-safe-path` 後確認載入。
3. 故意 `set auto-load safe-path /`，理解它的危險（不要留著）。
4. 寫一個 `crash.gdb`（`break main`、`run`、`bt`、`quit`），用 `gdb -batch -x crash.gdb ./prog` 跑，體會無互動自動化。
5. `info auto-load` 看你 debug 一個用到 libstdc++ 的 C++ 程式時，哪些 `-gdb.py` 被自動載入（STL printer）。
6. 用 `gdb -nx` 啟動，對比有無 `.gdbinit` 的差別。

## 本章重點整理

- 三層 gdbinit：系統 `/etc/gdb/gdbinit`、使用者 `~/.gdbinit`（或 `~/.config/gdb/gdbinit`）、專案當前目錄 `.gdbinit`。
- 全域 `.gdbinit` 放每次都要的偏好；自動化必備 `set confirm off` + `set pagination off`。
- auto-load 安全模型：GDB 預設只信任 safe-path 的 `.gdbinit` / `-gdb.py`，防惡意目錄；用 `add-auto-load-safe-path 具體目錄`，**別**信任 `/`。
- `source` 手動載入；`gdb -batch -x script.gdb` 無互動自動化（CI 崩潰分析）。
- 函式庫的 `libfoo.so-gdb.py` 是 STL pretty-printer 自動出現的機制；發布插件就靠這套。

## 自我檢核

- [ ] 三層 gdbinit 各放什麼？自動化（CI）一定要設哪幾個選項？
- [ ] GDB 為什麼會「拒絕」載入某目錄的 `.gdbinit`？正確的解法是什麼？錯誤的圖方便做法有什麼風險？
- [ ] libstdc++ 的 STL pretty-printer 怎麼「自動」出現的？
- [ ] 怎麼寫一個無互動、CI 可用的崩潰分析腳本？
- [ ] 懷疑 `.gdbinit` 害 GDB 出問題，怎麼啟動一個乾淨的 GDB？

## 延伸閱讀

### 官方文件

- **[GDB Manual: Initialization Files](https://sourceware.org/gdb/current/onlinedocs/gdb/Initialization-Files.html)** 與 **[Auto-loading](https://sourceware.org/gdb/current/onlinedocs/gdb/Auto_002dloading.html)**
  - **讀哪裡**：啟動載入順序、auto-load safe-path、local gdbinit。
  - **和本章的關聯**：本章安全模型的權威；safe-path 的精確語意。

- **[GDB Manual: Command Files](https://sourceware.org/gdb/current/onlinedocs/gdb/Command-Files.html)**
  - **讀哪裡**：source、`-x`/`-ix`/`-batch` 的差別。
  - **和本章的關聯**：自動化腳本的完整選項。

### 部落格 / 文章

- **[A useful .gdbinit](https://github.com/cyrus-and/gdb-dashboard)** — gdb-dashboard 專案
  - **這篇說什麼**：一個純 Python 的 `.gdbinit` dashboard，展示 `.gdbinit` 能做到什麼程度。
  - **為什麼值得讀**：介於原生 TUI 與 gef 之間的優雅範例；Final Project 的靈感來源。

下一章把 `.gdbinit` 裡的零散設定升級成「程式」：GDB 的命令語言——define、迴圈、條件、hooks，讓你寫出真正的自動化指令。

→ [Ch 20 GDB 命令語言](./20-command-language.md)
