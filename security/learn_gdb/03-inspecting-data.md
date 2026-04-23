# Ch 3 — 看資料：print / display / ptype

> 目標：熟練 `print` 家族（包含 format、運算式、副作用）、`display` 自動重印、`ptype` 看型別、`set variable` 改值。

## `print` 基本用法

```
(gdb) print x
$1 = 3
```

縮寫 `p`。印完的結果會放進 `$N` 變數，之後可以引用：

```
(gdb) p $1 + 10
$2 = 13
```

`$`（不加數字）指**上一次**的結果。`$$` 指倒數第二次。

## Print 不只是印變數，它是個運算式求值器

這是初學者常錯過的關鍵 — `p` 吃的是**任意 C 運算式**，不是只能印一個變數名：

```
(gdb) p n * 2
$3 = 10

(gdb) p sizeof(int)
$4 = 4

(gdb) p total + square(99)
$5 = ...
```

**注意最後一個**：`square(99)` 會**真的在 inferior 裡呼叫 `square`**。GDB 會暫停原本的執行，把 RIP 移到 `square`，模擬一次 call 把它跑完，拿回 return value。這叫 **inferior function call**，Ch 21 會講原理。

### 副作用

既然 inferior call 是真的在跑，那副作用也是真的：

```c
int counter = 0;
int bump(void) { return ++counter; }
```

```
(gdb) p bump()
$1 = 1
(gdb) p bump()
$2 = 2
(gdb) p counter
$3 = 2      ← 真的被改了
```

這個行為有時候超方便（「幫我算這個」），有時候會害死你（對 production process 用時你會不小心改到狀態）。

## Print formats — 用不同進位看

```
(gdb) p/x n        # 十六進位
$4 = 0x5
(gdb) p/o n        # 八進位
$5 = 05
(gdb) p/t n        # 二進位
$6 = 101
(gdb) p/d n        # 十進位（預設）
$7 = 5
(gdb) p/u n        # 不帶正負號的十進位
$8 = 5
(gdb) p/c 65       # 字元
$9 = 65 'A'
(gdb) p/f 0x40490fdb    # 當 float 印
$10 = 3.14159274
(gdb) p/a &main    # 位址（解析到 symbol）
$11 = 0x11b8 <main>
```

format 的縮寫會一直沿用到下一個 format 出現，所以常看到人打 `p/x x` 再 `p/x y` 看 hex 連發。

## Print 一個陣列

```c
int arr[5] = {1, 4, 9, 16, 25};
```

```
(gdb) p arr
$1 = {1, 4, 9, 16, 25}
```

GDB 會依型別印。但如果 `arr` 是個 `int *`（heap 上的陣列），GDB 就不知道長度：

```c
int *arr = malloc(5 * sizeof(int));
```

```
(gdb) p arr
$1 = (int *) 0x5555555592a0
```

用 `@` 運算子手動指定長度：

```
(gdb) p *arr@5
$2 = {1, 4, 9, 16, 25}
```

讀法：「把 `arr` 指向的那塊記憶體，當成長度 5 的陣列印」。

## Print 結構

```c
struct Point { int x; int y; };
struct Point p = {3, 4};
```

```
(gdb) p p
$1 = {x = 3, y = 4}
```

巢狀 / 複雜結構時，預設會印到你看不完。三個好用的設定：

```
(gdb) set print pretty on
(gdb) set print array on
(gdb) set print array-indexes on
```

開啟後：

```
(gdb) p p
$2 = {
  x = 3,
  y = 4
}

(gdb) p arr
$3 = {[0] = 1, [1] = 4, [2] = 9, [3] = 16, [4] = 25}
```

建議寫進 `~/.gdbinit`（Ch 14 會教）。

## Print 字串

C 字串（`char *`）：

```
(gdb) p msg
$1 = 0x555555556004 "hello world"
```

自動印到 null terminator。如果你只想看前 N 個字：

```
(gdb) p msg[0]@3
$2 = "hel"
```

## Print 指標

指標本身是個位址：

```
(gdb) p ptr
$1 = (int *) 0x7fffffffe1ac
```

`*ptr` 解參照：

```
(gdb) p *ptr
$2 = 42
```

**印一個 linked list 的所有節點**：

```c
struct Node { int val; struct Node *next; };
```

```
(gdb) p head->val
(gdb) p head->next->val
(gdb) p head->next->next->val
```

打到你累。懶人寫法：Ch 15（Python API）教你寫個自訂 command 一次印完，或 Ch 16 寫 pretty printer。

## `display` — 自動重印

每次執行停下來時，自動重印這個運算式：

```
(gdb) display i
(gdb) display total
```

之後每次 `n`、`s`、斷點停，都會自動印：

```
(gdb) n
10              total += square(i);
2: total = 5
1: i = 2
```

管理：

```
(gdb) info display
(gdb) delete display 1
(gdb) disable display 2
(gdb) enable display 2
```

迴圈內觀察多個變數很方便。

## `ptype` — 看型別

```
(gdb) ptype n
type = int

(gdb) ptype arr
type = int [5]

(gdb) ptype p
type = struct Point {
    int x;
    int y;
}

(gdb) ptype bump
type = int (void)
```

`whatis` 是 `ptype` 的簡化版，只印頂層型別、不展開 struct。

看 typedef 是不是你以為的東西：

```c
typedef struct { uint64_t hi; uint64_t lo; } u128;
```

```
(gdb) ptype u128
type = struct {
    uint64_t hi;
    uint64_t lo;
}
```

## `set variable` — 改值

```
(gdb) set variable n = 100
(gdb) set var n = 100        # 縮寫
(gdb) set n = 100            # 更短，但有坑，見下
```

**注意**：直接 `set x = ...` 可能跟 GDB 內建設定撞名。`set history filename` 就是個 GDB 設定，不是「設變數 history.filename」。保險寫法是 `set variable x = ...` 或 `set var x = ...`。

這招用途：

- 假設某個條件分支平常進不去，手動設變數讓它進去
- 測試 bug 修復：把引起 crash 的變數設成安全值
- 繞過某個 if 檢查

## GDB convenience variables

不要跟 `$1`、`$2` 這種「上次 print 結果」搞混，convenience variable 是你自己取名的：

```
(gdb) set $ptr_backup = ptr
(gdb) ... (繼續做事) ...
(gdb) p $ptr_backup
$5 = (int *) 0x7fffffffe1ac
```

命名空間跟 inferior 變數分開，不會打架。常用來暫存某個值。

## 幾個好用的內建 convenience

| 變數 | 意義 |
|---|---|
| `$pc` | program counter（= RIP on x86_64） |
| `$sp` | stack pointer |
| `$fp` | frame pointer |
| `$_exitcode` | 最近一次 inferior 的 exit code |
| `$_siginfo` | 最近一個 signal 的資訊 |
| `$_thread` | 當前 thread id |

```
(gdb) p $pc
$1 = (void (*)()) 0x555555555160 <square>
```

## 一個常見誤解

「`p &x` 印出的位址，下次再跑還會一樣嗎？」

**不會**。現代 Linux 有 ASLR（Address Space Layout Randomization），每次 `run` 位址都會變。但 GDB 在 debug session 中預設**關掉 ASLR**，所以**同一個 gdb session 裡連續幾次 `run` 位址會一樣**。退出 gdb 再進，又不一樣了。

想保留 ASLR（例如你要 debug 的 bug 只有 ASLR 打開才會發生）：

```
(gdb) set disable-randomization off
```

Ch 20 會詳談。

## 常見坑

1. **印 C++ 物件看不懂**：Ch 16 會教 pretty printer，libstdc++ 官方就有 `std::vector` / `std::map` 的 printer。
2. **印浮點看到很長的小數**：`set print frame-arguments all` 跟 `set print finish on` 有時候有幫助，或用 `p/f` 明確指定。
3. **`p` 當機**：你可能 inferior call 到一個會 crash 的函式（例如踩到野指標的 getter）。GDB 預設會把 inferior call 的 signal 吃掉，但極端情況會 stuck — 打 Ctrl-C 通常可以中斷。
4. **修改常數**：`set var PI = 4` 在某些 const 變數上會失敗，因為它在 `.rodata` 段（唯讀）。

## 動手練習

沿用 Ch 2 的 `sample.c`：

1. 在 `sum_of_squares` 裡停下來，`display i` 和 `display total`，然後 `n` 幾次，觀察自動重印。
2. `p square(10)` — 直接在 GDB 裡算平方。
3. `p/x n`、`p/t n`、`p/o n` — 同一個值三種進位。
4. `set var n = 100` — 在 `main` 裡把 `n` 改成 100，繼續跑，看 `sum_of_squares` 結果。
5. `ptype sum_of_squares` — 看函式簽章。
6. 改個會 crash 的：在 `square` 加一條 `int *ptr = NULL; *ptr = 5;`，重編，進 gdb。先跑一次 crash，然後重跑、在 crash 之前 `set var ptr = &n`，讓它不 crash。

## 自我檢核

- [ ] 我能用 `p` 印任意 C 運算式（包含函式呼叫）
- [ ] 我知道 `$1`、`$` 跟 convenience variable `$myvar` 的差別
- [ ] 我能用 `/x /t /o /f /c` 切換 print format
- [ ] 我知道 heap 陣列要用 `p *ptr@N`
- [ ] 我能用 `set var` 改 inferior 裡的值

下一章進入記憶體層次 — `x` 指令讓你直接看任意位址的 raw bytes。

→ [Ch 4 檢視記憶體：x 指令全家](./04-examining-memory.md)
