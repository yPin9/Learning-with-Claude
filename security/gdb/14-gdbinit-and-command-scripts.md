# Ch 14 — .gdbinit 與 command script

> 目標：熟練 `~/.gdbinit`、`.gdbinit` per-project、`define` 自訂命令、hooks、command files，讓 GDB 記住你的偏好、把常用動作自動化。

## `~/.gdbinit` — 全域設定

GDB 啟動時會讀 `~/.gdbinit`（每個使用者 home 目錄一個）。你在 interactive session 裡常打的設定，寫進這裡就永久有效。

**我建議的最小 .gdbinit：**

```gdb
# ===== Display =====
set print pretty on
set print array on
set print array-indexes on
set print null-stop on
set pagination off
set disassembly-flavor intel
set confirm off

# ===== Thread handling =====
set scheduler-locking step
set print thread-events on

# ===== History =====
set history save on
set history size 10000
set history filename ~/.gdb_history

# ===== Python =====
set python print-stack full

# ===== Startup =====
# start 時直接進 TUI
# tui enable
```

逐行解釋：

- `print pretty` + `array-indexes`：讓 struct / array 的 print 可讀性大增
- `pagination off`：不要每 24 行停下來問「--More--」，會中斷自動化
- `disassembly-flavor intel`：個人偏好，想用 at&t 就改 `att`
- `scheduler-locking step`：Ch 11 提過，step 時凍結其他 thread
- `confirm off`：不要問「Are you sure?」，老手一律 off
- `history save on`：session 結束後記住指令歷史，下次能用 ↑ 找

## 專案的 `.gdbinit`

除了 `~/.gdbinit`，gdb 還會嘗試讀**當前目錄**的 `.gdbinit`。這非常好用：每個專案可以有自己的 debug 設定、pretty printer、自訂指令。

**但有安全機制**：預設下 gdb 會拒絕讀「非 root 擁有的、非 home 目錄的」`.gdbinit`，因為你 `cd` 進一個不明目錄跑 gdb，可能被別人的惡意 .gdbinit 打。

要啟用 per-project：

```
# ~/.gdbinit 裡加
set auto-load local-gdbinit on
add-auto-load-safe-path /path/to/project
```

或更放鬆（**慎用**）：

```
add-auto-load-safe-path /
```

等於信任所有路徑。開發自己機器上 OK，不建議共享帳號用。

### 專案 .gdbinit 範例

假設你的專案叫 `my-server`，在 `/home/me/my-server/.gdbinit`：

```gdb
# 對 my-server 特化的設定
handle SIGPIPE nostop noprint pass
handle SIGUSR1 nostop noprint pass

# 預先下幾個常用斷點
break handle_request
break log_error

# 載入專案自己的 pretty printers（Ch 16）
source /home/me/my-server/scripts/gdb_printers.py
```

每次在專案目錄跑 `gdb` 就自動套用。

## `define` — 自訂命令

`define` 讓你建 macro：

```gdb
define psize
    printf "sizeof(%s) = %d bytes\n", "$arg0", sizeof($arg0)
end

document psize
    Print the size of a type or variable.
    Usage: psize <type_or_variable>
end
```

`$arg0`、`$arg1`... 是參數，`$argc` 是參數數量。

用：

```
(gdb) psize int
sizeof(int) = 4 bytes

(gdb) psize struct User
sizeof(struct User) = 48 bytes
```

`document` 是給 `help` 命令用的：

```
(gdb) help psize
Print the size of a type or variable.
Usage: psize <type_or_variable>
```

### 一個實用的自訂 command：dump-heap-list

遍歷 linked list 印每個節點：

```gdb
define plist
    set $node = $arg0
    set $i = 0
    while $node != 0
        printf "[%d] id=%d\n", $i, $node->id
        set $node = $node->next
        set $i = $i + 1
    end
end
```

```
(gdb) plist user_db
[0] id=1042
[1] id=2
[2] id=1
```

### `if` / `while` / `else`

```gdb
define is_null_or_deep
    if $arg0 == 0
        printf "NULL\n"
    else
        set $depth = 0
        set $n = $arg0
        while $n != 0
            set $depth = $depth + 1
            set $n = $n->next
        end
        printf "depth = %d\n", $depth
    end
end
```

## hooks — 在命令前後自動執行

`hook-NAME` 定義「執行 NAME 命令前」跑的東西；`hookpost-NAME` 定義「之後」跑。

實例：每次 `stop`（斷點 / signal / step 停下）都自動印當前 bt：

```gdb
define hook-stop
    bt 3                  ; 印最內 3 層
    x/3i $pc              ; 印接下來 3 條指令
end
```

或：每次 `run` 前自動設一些東西：

```gdb
define hook-run
    set environment SECRET_KEY="debug_key"
end
```

常見的 hook：

- `hook-stop`：停下來時自動印 context
- `hook-run` / `hook-start`：啟動前準備
- `hook-continue`：continue 前做事
- `hook-quit`：gdb 結束前

## `commands N` — 斷點自動執行

前面 Ch 6 講過，這裡補充它的 scripting 能力：

```gdb
b process_request
commands
    silent
    printf "request %d from %s\n", req->id, req->source
    if req->id == 42
        bt
    end
    continue
end
```

`silent` 讓它不印「Breakpoint 1, ...」、`continue` 讓斷點變成 tracing。

## command files：從外部載入

寫一個 `debug_session.gdb`：

```gdb
file ./myprog
set args --verbose --input test.json
break main
break bug_prone_function
run
```

然後：

```bash
gdb -q -x debug_session.gdb
```

或在 interactive session：

```
(gdb) source debug_session.gdb
```

`source` 可以載入任何 gdb script。同樣可以 `source foo.py` 載入 Python 腳本（Ch 15 會深入）。

## `-ex` / `-iex`：命令行一次性指令

```bash
gdb -q ./prog \
    -ex "set print pretty on" \
    -ex "break main" \
    -ex "run"
```

- `-ex`：在載入 .gdbinit 之後執行
- `-iex`：在 .gdbinit 之前執行

適合 CI / 一次性分析。

實例：「給我 bt，完就 quit」：

```bash
gdb -q -batch ./prog core.* -ex bt -ex quit
```

`-batch` 就跑 script 不 interactive。寫在 CI 裡做 crash 自動分析：

```bash
#!/bin/bash
for core in core.*; do
    echo "=== $core ==="
    gdb -q -batch ./prog "$core" -ex "thread apply all bt" -ex quit
done
```

## 實戰：一個好用的 `.gdbinit` 增強

```gdb
# 每次 stop 時自動印 context：bt 3 層 + disas 當前 + 局部
define hook-stop
    printf "\n"
    printf "─── Backtrace ───\n"
    bt 3
    printf "\n─── Next instructions ───\n"
    x/5i $pc
    printf "\n─── Locals ───\n"
    info locals
end

# 簡寫：pbt N = 印 N 層 bt
define pbt
    bt $arg0
end

# dump heap chunk 的 malloc header（glibc）
define mchunk
    printf "prev_size = %ld\n", ((long *)$arg0)[-2]
    printf "size      = 0x%lx\n", ((long *)$arg0)[-1]
end

# assert-style breakpoint：在特定條件下 bt + continue
define trace_bt
    b $arg0
    commands
        silent
        bt
        continue
    end
end
```

## 常見坑

1. **.gdbinit 的錯誤訊息被淹沒**：`gdb -iex "set debug gdbinit on"` 可以看載入過程。
2. **專案 .gdbinit 沒生效**：大概率是 auto-load safe-path 沒設。用 `info auto-load local-gdbinit` 檢查。
3. **`define` 裡 `$arg0` 是 string 還是 value？** — 它就是原始字元，gdb 會把命令當 expression 替換。`printf "%s", "$arg0"` 會印字面字串「$arg0」，要印參數名要寫 `printf "%s", "$arg0"`（引號外）或用 `eval` 技巧。小心引號行為。
4. **hook-stop 印太多把主要輸出淹掉**：保持短，只印 1–3 項關鍵資訊。
5. **`commands` 忘記 `end`**：gdb 會進持續等待狀態，Ctrl-C 中斷後重打。
6. **history 檔過大**：`set history size 10000` 避免失控。

## 動手練習

1. 寫你自己的 `~/.gdbinit`，至少包含 pretty print、history、disassembly flavor 設定。
2. 在某個專案目錄寫 `.gdbinit`，自動載入專案相關的斷點。啟用 auto-load。
3. 用 `define` 寫一個 `plist` 命令，印 linked list（沿用 Ch 6 / 練習 B 的結構）。
4. 加 `hook-stop` 每次停下時印 bt 3 層 + `x/3i $pc`。
5. 寫 `debug.gdb`：載入 core、印 bt、印 `info threads`、quit。用 `gdb -batch -x debug.gdb prog core.*` 跑看看。
6. `trace_bt` 命令 — 對某個函式下 silent 斷點，每次自動印 bt 然後 continue。

## 自我檢核

- [ ] 我有自己的 `~/.gdbinit` 設定
- [ ] 我會寫 `define` 自訂命令，含 `if` / `while`
- [ ] 我會用 `hook-stop` 自動印 context
- [ ] 我會用 `commands` 讓斷點自動執行並 continue
- [ ] 我會用 `-ex` / `-batch` 做命令列一次性分析
- [ ] 我知道專案 `.gdbinit` 要設 auto-load safe-path

下一章進入 Python — gdb 內建 Python 3 解釋器，能存取 inferior 的所有內部 API，把 script 能力提升一個數量級。

→ [Ch 15 Python API（一）：commands 與 breakpoints](./15-python-api-basics.md)
