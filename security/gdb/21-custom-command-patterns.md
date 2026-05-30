# Ch 21 — 自訂指令模式集

> **目標**：把 Ch 20 的命令語言收斂成一套**可直接抄用的 debug 指令範本**——資料結構走訪、批次檢視、條件監控、自動報告、context 顯示。同時建立「何時用命令語言、何時升級 Python」的判斷力，作為 Part 5 的橋樑。

> **環境**：GDB 13/14，Linux x86_64。本章是 recipe 集，每個範本都可貼進 `.gdbinit` 直接用。

## 為什麼需要「模式集」

學會 `define`、`while`、`if` 不等於會寫好用的 debug 指令。真正的價值在於**累積一套你自己的工具**：每次 debug 都重複的動作，寫成指令，下次一個字搞定。這章給你十來個經過驗證的範本，你可以直接抄、改、組合成自己的工具箱——這個工具箱最終會在 Part 5 用 Python 重寫成一個完整插件（Final Project）。

## 模式一：資料結構走訪

最常見的需求。linked list、tree、hash table 都是這個模式的變體。

```gdb
# 走訪 singly linked list
define list
  set $p = $arg0
  set $i = 0
  while $p != 0
    printf "[%d] %p: ", $i, $p
    print *$p
    set $p = $p->next
    set $i = $i + 1
  end
  printf "total: %d nodes\n", $i
end
document list
  Walk a singly-linked list. Usage: list <head_pointer>
end
```

```gdb
# 走訪陣列（含越界保護）
define arr
  set $a = $arg0
  set $n = $arg1
  set $i = 0
  while $i < $n
    printf "[%d] = ", $i
    print $a[$i]
    set $i = $i + 1
  end
end
```

```gdb
# 遞迴走訪二元樹（中序）
define tree
  if $arg0 != 0
    tree $arg0->left
    printf "%d ", $arg0->val
    tree $arg0->right
  end
end
```

## 模式二：記憶體與暫存器快照

逆向/pwn 常要「看一眼當前狀態全貌」。

```gdb
# 把常用暫存器一次印出（hex）
define regs
  printf "rax=%#lx rbx=%#lx rcx=%#lx rdx=%#lx\n", $rax, $rbx, $rcx, $rdx
  printf "rsi=%#lx rdi=%#lx rbp=%#lx rsp=%#lx\n", $rsi, $rdi, $rbp, $rsp
  printf "rip=%#lx ", $rip
  output/a $rip
  echo \n
end
```

```gdb
# telescope：把 stack 上每個 slot 印出來，並嘗試解讀成符號/位址
define stack
  set $i = 0
  set $count = 8
  if $argc == 1
    set $count = $arg0
  end
  while $i < $count
    printf "%#lx|+%04x: ", $sp + $i*8, $i*8
    x/gx $sp + $i*8
    set $i = $i + 1
  end
end
```

`stack`（telescope）是 gef/pwndbg context 的招牌功能——把 stack 一格格印出來看內容。這裡是命令語言的簡版；Final Project 你會用 Python 做出會「自動跟隨指標、上色」的完整版。

## 模式三：條件監控與自動報告

配合斷點/hook，做「出事才報告」。

```gdb
# 掛在斷點上：記錄每次呼叫的參數
define log_call
  printf "call: arg0=%#lx arg1=%#lx caller=", $rdi, $rsi
  output/a *(void**)$rsp
  echo \n
end
# 用法：
#   break target_func
#   commands
#     silent
#     log_call
#     continue
#   end
```

```gdb
# hook-stop 版簡易 context（每次停自動印）
define hook-stop
  printf "─── context ───\n"
  regs
  printf "─── code ───\n"
  x/3i $pc
  printf "─── stack ───\n"
  stack 4
end
```

把上面三個範本（`regs`、`stack`、`hook-stop`）一起放進 `.gdbinit`，你就有了一個迷你版的「停下來自動顯示 registers + code + stack」的 context——這是 gef 的核心體驗，用純命令語言就能做出雛形。

## 模式四：批次操作

```gdb
# 對所有 thread 印某個 TLS 變數
define allthreads
  thread apply all printf "thread: errno=%d\n", errno
end
```

```gdb
# 連續 N 步並每步印一個變數（手動 trace）
define trace_var
  set $n = $arg1
  set $i = 0
  while $i < $n
    next
    printf "%d: ", $i
    print $arg0
    set $i = $i + 1
  end
end
```

## 模式五：搜尋與驗證

```gdb
# 在 list 裡找特定值
define find
  set $p = $arg0
  set $target = $arg1
  set $found = 0
  while $p != 0 && $found == 0
    if $p->val == $target
      printf "found %d at %p\n", $target, $p
      set $found = 1
    end
    set $p = $p->next
  end
  if $found == 0
    printf "not found\n"
  end
end
```

```gdb
# 驗證 list 沒有環（Floyd 龜兔）
define check_cycle
  set $slow = $arg0
  set $fast = $arg0
  set $cyclic = 0
  while $fast != 0 && $fast->next != 0
    set $slow = $slow->next
    set $fast = $fast->next->next
    if $slow == $fast
      set $cyclic = 1
      loop_break
    end
  end
  printf "cycle: %s\n", $cyclic ? "YES" : "NO"
end
```

`check_cycle` 把一個演算法（Floyd 找環）寫進 GDB——debug 「list 是不是被改成環導致無窮迴圈」（呼應練習 B/C）時直接驗證。

## 命令語言 vs Python：決策表

到這裡你已經把命令語言推到實用極限。什麼時候該升級 Python（Part 5）？

| 需求 | 命令語言 | Python |
|---|---|---|
| 走訪結構 + printf | ✅ 夠用 | 也行 |
| 簡單條件/迴圈 | ✅ 夠用 | 也行 |
| 字串解析/比對 | ❌ 很痛 | ✅ |
| 陣列/dict/集合 | ❌ 沒有 | ✅ |
| 彩色/對齊/表格輸出 | ❌ 很醜 | ✅ |
| 讀寫檔案、呼叫外部工具 | ❌ 只能 shell | ✅ |
| 自訂 pretty-printer | ❌ 不行 | ✅ |
| 自訂 TUI 視窗 | ❌ 不行 | ✅ |
| 處理大量資料 | ❌ 慢且笨 | ✅ |
| 可維護的複雜邏輯 | ❌ 難讀 | ✅ |

**判準**：你的指令開始需要「字串處理、資料結構、漂亮輸出、或超過 30 行邏輯」，就是 Python 的訊號。本章的 `stack` telescope、`hook-stop` context，到 Final Project 都會用 Python 重寫成「會自動跟隨指標、上色、解讀型別」的完整版——那是命令語言做不到的。

## 踩雷集錦

1. **指令名衝突**：`define list`、`define find` 可能蓋到內建或其他指令。用前綴（`mylist`、`myfind`）或檢查 `help`。
2. **沒有型別的痛**：`$arg0->val` 假設了 `$arg0` 是某結構指標，換個型別就壞。命令語言指令很難寫成通用的。
3. **遞迴深度限制**：`tree` 對深樹會爆。深結構直接上 Python。
4. **三元運算子 `?:` 支援有限**：`$cyclic ? "YES" : "NO"` 在 printf 的 `%s` 可行（求值表示式），但別期待命令語言有完整表示式能力。
5. **錯誤處理幾乎沒有**：指令中途出錯就中斷，沒有 try/catch。Python 才有像樣的錯誤處理。
6. **共用與發布難**：一堆 `define` 散在 `.gdbinit` 難管理、難分享。Python 模組化好太多——這也是 gef 用 Python 的原因。

## 進階：再往深一層

- **把工具箱模組化**：把這些 `define` 放進 `~/scripts/gdb/toolbox.gdb`，`.gdbinit` 裡 `source` 它（Ch 19）。
- **prefix command 組織**：`define-prefix dbg` 後做 `dbg list`、`dbg tree`、`dbg regs`，把工具歸到一個命名空間（Python 版 Ch 24 更乾淨）。
- **與 Python 漸進混用**：單一指令裡 `python ...` 內嵌一段 Python 處理命令語言搞不定的部分，漸進遷移。
- **參考 gef/pwndbg 的指令組織**：它們有幾百個指令，全用 Python class 組織。讀它們的 source，你會看到本章每個模式的「成熟 Python 版」。
- **`hook-stop` 的進階**：條件式 context（只在特定情況印）、依當前是組語還是 source 模式切換顯示。

## 動手練習

1. 把 `list`、`tree`、`find`、`check_cycle` 四個範本放進 `~/scripts/gdb/toolbox.gdb`，`.gdbinit` source 它。
2. 對練習 B 的 detective.c 用 `list` 走訪、用 `check_cycle` 驗證有沒有被改成環。
3. 組合 `regs` + `stack` + `hook-stop`，做出「每次停自動顯示 context」的迷你 gef，跑一個程式體驗。
4. 寫一個你自己 debug 工作中常重複的動作的指令（例如印某個專案特定結構）。
5. 對照決策表，找出你寫的指令裡哪個「其實該用 Python」——那就是你 Part 5 的第一個改寫目標。

## 本章重點整理

- 累積一套可重用 debug 指令（走訪、快照、監控、搜尋、驗證）是熟手的標誌。
- 關鍵範本：`list`/`tree`（走訪）、`regs`/`stack` telescope（快照）、`hook-stop`（自動 context）、`find`/`check_cycle`（搜尋驗證）。
- `regs` + `stack` + `hook-stop` 組成迷你版 gef context——命令語言能做出雛形。
- 決策：走訪 + 簡單條件 + printf → 命令語言；字串/資料結構/漂亮輸出/複雜邏輯 → Python。
- 本章的工具，Final Project 會用 Python 重寫成「自動跟隨指標、上色、解讀型別」的完整版。

## 自我檢核

- [ ] 你能默寫一個走訪 linked list 的自訂指令嗎？
- [ ] `regs` + `stack` + `hook-stop` 怎麼組成簡易 context？跟 gef 的關係？
- [ ] 對照決策表，哪些需求是命令語言的死穴？
- [ ] 你日常 debug 有哪個重複動作值得寫成指令？
- [ ] 為什麼 gef/pwndbg 全用 Python 而非命令語言？

## 延伸閱讀

### 官方文件

- **[GDB Manual: Sequences](https://sourceware.org/gdb/current/onlinedocs/gdb/Sequences.html)**
  - **讀哪裡**：複習 define/hook/if/while；本章是它的實戰應用。

### 部落格 / 原始碼

- **[gef 原始碼](https://github.com/hugsy/gef)** 與 **[pwndbg 原始碼](https://github.com/pwndbg/pwndbg)**
  - **讀哪裡**：挑一個簡單指令（如 telescope / context_stack）看它的 Python 實作。
  - **和本章的關聯**：本章每個命令語言範本，這裡有「工業級 Python 版」對照；Final Project 的標竿。

- **[gdb-dashboard](https://github.com/cyrus-and/gdb-dashboard)**
  - **為什麼值得讀**：介於命令語言與 gef 之間，展示 hook-stop context 的優雅做法。

Part 4 完成。用練習 D 把命令語言的自動化能力綜合驗證，然後 Part 5 正式進入 Python API——把這些工具升級成真正的插件。

→ [練習 D：用純命令語言寫自動化指令](./practice-d-command-language-automation.md)
