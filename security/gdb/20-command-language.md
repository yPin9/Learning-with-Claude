# Ch 20 — GDB 命令語言

> **目標**：掌握 GDB 內建的腳本語言——`define` 自訂指令、`if`/`while`/`loop_break`/`loop_continue`、參數 `$arg0`/`$argc`、`printf`/`echo`/`output`、`hook`/`hookpost`、`set logging`。學完你能在不寫 Python 的情況下把重複 debug 動作寫成指令。這也是 Python（Part 5）之前的「夠用就好」自動化層。

> **環境**：GDB 13/14，Linux x86_64。

## 為什麼先學命令語言，再學 Python

GDB 有兩套腳本能力：內建的**命令語言**（這章）和 **Python API**（Part 5）。命令語言較弱（沒有資料結構、字串處理笨拙），但有三個理由先學它：

1. **無所不在**：任何 GDB 都有，不需要 Python 支援，遠端/嵌入式/精簡環境也能用。
2. **夠用**：80% 的日常自動化（自訂走訪指令、批次操作、hook）命令語言就能做。
3. **是 `.gdbinit` 和 command file 的原生語言**：Ch 19 的 `define`、Ch 12 的 `commands` 都是它。

複雜邏輯（解析、資料結構、漂亮輸出）才上 Python。先把這層練熟。

## `define`：自訂指令

```gdb
define greet
  echo Hello from GDB!\n
end
```

```
(gdb) greet
Hello from GDB!
```

`define 名字 ... end` 定義一個新指令。配 `document` 加說明（`help 名字` 看得到）：

```gdb
define greet
  echo Hello!\n
end
document greet
  Print a greeting. Usage: greet
end
```

## 參數：`$arg0` ~ `$argN` 與 `$argc`

自訂指令可接參數，用 `$arg0`、`$arg1`… 引用，`$argc` 是參數個數：

```gdb
define pp
  # 印一個指標指向的內容
  print *$arg0
end
```

```
(gdb) pp head
$1 = {val = 11, next = 0x...}
```

可變參數 + `$argc` 判斷：

```gdb
define xdump
  if $argc == 2
    x/$arg1xw $arg0      # xdump addr count
  else
    x/8xw $arg0          # xdump addr  → 預設 8 個
  end
end
```

```
(gdb) xdump $sp 16
(gdb) xdump &mystruct
```

> 注意：參數是**文字替換**，不是型別化的值。`$arg0` 就是你打的那串字原樣貼進去。所以 `pp head->next` 也行（`*head->next`）。這既靈活又危險（沒有型別檢查）。

## 控制流：`if` / `while`

```gdb
define walklist
  set $node = $arg0
  while $node != 0
    printf "id=%d val=%d\n", $node->id, $node->val
    set $node = $node->next
  end
end
```

```
(gdb) walklist head
id=1 val=11
id=2 val=22
...
```

這就是 Ch 8 手打的 list 走訪，現在包成一個可重用指令。`if/else/end`、`while/end` 配 convenience variable（Ch 8）做游標，命令語言的核心模式就這些。

迴圈控制：

```gdb
define find_in_list
  set $node = $arg0
  set $target = $arg1
  while $node != 0
    if $node->val == $target
      printf "found at %p\n", $node
      loop_break              # 跳出迴圈
    end
    set $node = $node->next
  end
end
```

`loop_break`（break）和 `loop_continue`（continue）控制迴圈。

## 輸出：`printf` / `echo` / `output`

```gdb
printf "x = %d, ptr = %p, name = %s\n", x, ptr, name   # 像 C 的 printf
echo some literal text\n                                # 純文字（不求值）
output x                                                 # 印值但不換行、不存 history
```

`printf` 是命令語言裡格式化輸出的主力，支援 `%d %x %s %p %c %f` 等。比 `print` 適合做整齊的報告。

## `hook` / `hookpost`：在指令前後自動執行

`hook-XXX` 在每次執行指令 `XXX` **之前**自動跑；`hookpost-XXX` 在**之後**跑：

```gdb
define hook-run
  echo === starting program ===\n
end

define hookpost-step
  # 每次 step 後自動印當前行的某個變數
  # （示意；實務常用 display 達成）
end

define hook-stop
  # 每次 inferior 停下來時自動執行——做自訂 context 的關鍵！
  printf "stopped at: "
  output $pc
  echo \n
end
```

`hook-stop` 特別重要——它在**每次程式停下來時**自動執行。gef/pwndbg 的「停下來就自動印 context」就是掛在停止事件上（它們用 Python 的 `stop` event，Ch 25，但概念同源）。你可以用 `define hook-stop` 做一個簡易版的自動 context。

## `set logging`：把輸出存檔

蒐集 debug 記錄（配 Ch 12 的 logging 斷點）：

```gdb
(gdb) set logging file gdb-session.txt
(gdb) set logging on          # 之後所有輸出同時寫進檔案
(gdb) set logging redirect on # 只寫檔，不顯示在螢幕
(gdb) set logging overwrite on
... 操作 ...
(gdb) set logging off
```

配合「條件斷點 + commands + continue」蒐集大量呼叫記錄時，存檔事後分析。

## 一個完整的自訂工具：印整棵樹

```gdb
define walktree
  # walktree node depth
  set $n = $arg0
  if $n != 0
    set $d = $arg1
    # 縮排
    set $i = 0
    while $i < $d
      echo "  "
      set $i = $i + 1
    end
    printf "%d\n", $n->val
    walktree $n->left  $d+1     # 遞迴！自訂指令可呼叫自己
    walktree $n->right $d+1
  end
end
document walktree
  Recursively print a binary tree.  Usage: walktree <node> <depth>
end
```

```
(gdb) walktree root 0
5
  3
    1
    4
  8
```

自訂指令可以**遞迴呼叫自己**——走訪樹這種遞迴結構不到 15 行搞定。這對練習 B 的延伸挑戰（樹版偵探）直接可用。

## 命令語言的極限：何時該換 Python

命令語言會在這些地方撞牆：

- **字串處理**：沒有字串變數、不能 substr/split/比對（只能靠 `$_streq` 等 convenience function 勉強）。
- **資料結構**：沒有陣列、dict、list。只能用一堆 convenience variable 硬湊。
- **複雜邏輯**：巢狀邏輯一深就難讀難維護。
- **格式化/彩色輸出**：`printf` 夠基本，要彩色、對齊、表格就很痛。

撞到這些，換 Python（Ch 22 起）。判準：**走訪 + 簡單條件 + printf → 命令語言；解析 + 資料結構 + 漂亮輸出 → Python。** gef/pwndbg 全用 Python，就是因為它們要做的事遠超命令語言能力。

## 踩雷集錦

1. **`$arg0` 是文字替換不是值**：沒有型別檢查，打錯參數可能默默產生怪結果或晦澀錯誤。
2. **`while` 條件求值在 inferior 不存在時失敗**：自訂指令用到 inferior 變數，但程式還沒 run，會報錯。確認指令使用情境。
3. **遞迴自訂指令爆 stack**：GDB 對命令遞迴有深度限制，太深會中止。深樹用 Python。
4. **`define` 同名覆蓋內建指令**：`define print ...` 會蓋掉內建 `print`（GDB 會警告）。別亂蓋常用指令。
5. **`hook-stop` 寫壞讓 GDB 每次停都報錯**：hook 裡的錯誤會在每次停下時跳出來很煩。先在普通指令測好再做成 hook。
6. **命令語言的 `if` 沒有 `elif`**：要巢狀 `if/else`。多分支時很醜，這也是該換 Python 的訊號。

## 進階：再往深一層

- **`$argc` 與可變參數**：寫能接不同參數數量的彈性指令。
- **`eval`**：`eval "command %s", $arg0`——動態建構指令字串再執行，繞過文字替換的限制。
- **prefix command**：`define-prefix mytool` 後可定義 `mytool sub1`、`mytool sub2` 子命令（Python 版在 Ch 24 更完整）。
- **`with`（GDB 10+）**：`with print pretty on -- print x`——暫時改設定執行一條指令，完了還原。寫腳本時避免污染全域設定。
- **`commands` + define 組合**：斷點命中時呼叫自訂指令（Ch 12 + 本章），做出針對性的自動分析。
- **`python-interactive` / `python`**：在命令語言裡內嵌 Python 一行——兩套語言可混用，漸進遷移到 Python。

## 動手練習

1. 寫一個 `pp` 指令（`print *$arg0`），對一個指標用它。
2. 寫 `walklist`（走訪 linked list 印每個節點），對練習 B 的 detective.c 用它走訪整個 list。
3. 寫 `xdump addr [count]`，用 `$argc` 支援可選的 count 參數。
4. 寫 `walktree`（遞迴印二元樹），對一棵樹用它——直接服務練習 B 的樹版延伸挑戰。
5. 用 `define hook-stop` 做一個「每次停下來自動印 `$pc` 和 backtrace 前兩層」的簡易 context。
6. 用 `set logging` 把一段「條件斷點 + commands + continue」蒐集的記錄存檔。

## 本章重點整理

- `define ... end` 自訂指令，`document` 加說明；參數 `$arg0`…/`$argc`（文字替換，無型別）。
- 控制流：`if/else`、`while`、`loop_break`/`loop_continue`；配 convenience variable 當游標走訪結構。
- 輸出：`printf`（格式化主力）、`echo`（純文字）、`output`（不換行不存 history）。
- `hook-XXX`/`hookpost-XXX` 在指令前後自動執行；`hook-stop` 做自訂 context（gef 概念同源）。
- 命令語言適合「走訪 + 簡單條件 + printf」；字串/資料結構/漂亮輸出要換 Python。

## 自我檢核

- [ ] 怎麼把「手動走訪 linked list」包成一個可重用指令？
- [ ] `$arg0` 是「值」還是「文字」？這帶來什麼靈活與風險？
- [ ] `hook-stop` 能做什麼？跟 gef 的自動 context 有什麼關係？
- [ ] 自訂指令可以遞迴嗎？走訪樹時要注意什麼？
- [ ] 命令語言在哪些地方會撞牆、該換 Python？

## 延伸閱讀

### 官方文件

- **[GDB Manual: Canned Sequences of Commands](https://sourceware.org/gdb/current/onlinedocs/gdb/Sequences.html)**
  - **讀哪裡**：Define、Hooks、Command Files、`if`/`while`、`$argc`/`$arg`。
  - **和本章的關聯**：本章命令語言的完整權威。

- **[GDB Manual: Output / printf](https://sourceware.org/gdb/current/onlinedocs/gdb/Output.html)**
  - **讀哪裡**：`printf`、`echo`、`output`、`set logging`。
  - **和本章的關聯**：輸出與記錄的完整選項。

### 部落格 / 文章

- **[gdb-dashboard](https://github.com/cyrus-and/gdb-dashboard)** 的純命令語言/Python 混用範例
  - **為什麼值得讀**：看 hook-stop / 自動 context 怎麼做成完整工具；Final Project 的鋪墊。

下一章把命令語言的自訂指令收成「模式集」——常見的可重用 debug 指令範本，並總結何時用命令語言、何時升級 Python。

→ [Ch 21 自訂指令模式集](./21-custom-command-patterns.md)
