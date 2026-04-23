# Ch 4 — 檢視記憶體：x 指令全家

> 目標：熟練 `x`（examine memory）指令 — 任意位址、任意長度、任意格式的記憶體檢視器。這是從「用型別看資料」下降到「用 byte 看資料」的工具。

## 為什麼還要這個？

`print` 已經可以印一切，為什麼還要 `x`？

差別：

- `print` 需要**型別資訊**。「印變數 `x`」GDB 是靠 DWARF 知道 `x` 是 int，去讀 4 個 byte。
- `x` **完全不管型別**。給它一個位址，你要幾個 byte 就幾個 byte，你想當什麼格式看就什麼格式。

什麼時候需要沒型別的讀法？

- heap corruption — 看 `free` 後的記憶體到底長什麼樣
- reverse engineering — 沒 source、沒 debug info，只有位址
- 看 struct padding / alignment — 確認 compiler 插了多少 padding
- 看整塊 stack / heap 區 — 不管上面的「變數」是什麼

## `x` 的語法

```
x /NFU  ADDRESS
```

三個 modifier：

| 位置 | 意義 |
|---|---|
| `N` | 要看幾個「單位」 |
| `F` | 格式 format |
| `U` | 單位大小 unit |

看起來複雜，實務上你只會常用幾種組合。

### Format（F）

| 字母 | 意義 |
|---|---|
| `x` | hex |
| `d` | 十進位（signed） |
| `u` | 十進位（unsigned） |
| `o` | 八進位 |
| `t` | 二進位（**t**wo） |
| `f` | 浮點 |
| `c` | char |
| `s` | 字串 |
| `i` | 組合語言指令（**i**nstruction） |
| `a` | 位址（address）|

### Unit（U）

| 字母 | 大小 |
|---|---|
| `b` | byte（1） |
| `h` | halfword（2） |
| `w` | word（4） |
| `g` | giant word（8） |

**注意**：GDB 的 word = 4 byte（這是 i386 時代的遺物），不是你以為的 native word size（64-bit 機器上是 8）。64-bit 的常用值要用 `g`。

## 最常用的幾組

### 看一塊 byte

```
(gdb) x/32b arr
0x7fffffffe140: 0x01 0x00 0x00 0x00 0x04 0x00 0x00 0x00
0x7fffffffe148: 0x09 0x00 0x00 0x00 0x10 0x00 0x00 0x00
0x7fffffffe150: 0x19 0x00 0x00 0x00 0x00 0x00 0x00 0x00
0x7fffffffe158: 0x00 0x00 0x00 0x00 0x00 0x00 0x00 0x00
```

讀作「32 個 byte，hex 顯示」。注意 little-endian：`0x01 0x00 0x00 0x00` 實際是 `0x00000001`。

### 看一塊 32-bit word

```
(gdb) x/8wx arr
0x7fffffffe140: 0x00000001  0x00000004  0x00000009  0x00000010
0x7fffffffe150: 0x00000019  0x00000000  0x00000000  0x00000000
```

`8wx` = 8 個、word 大小、hex 格式。

### 看一塊 64-bit 值（常用於看指標）

```
(gdb) x/4gx &ptr
0x7fffffffe148: 0x00005555555592a0  0x0000000000000000
```

### 看字串

```
(gdb) x/s msg
0x555555556004: "hello world"
```

或給 char buffer 開 N 個字：

```
(gdb) x/20c buf
0x7fffffffe120: 72 'H'  101 'e' 108 'l' 108 'l' 111 'o' 0 '\000' 0 '\000' 0 '\000'
```

### 看組語（反組譯）

```
(gdb) x/10i $pc
=> 0x5555555551ab <main+10>: mov    $0x5,-0x10(%rbp)
   0x5555555551b2 <main+17>: mov    -0x10(%rbp),%eax
   0x5555555551b5 <main+20>: mov    %eax,%edi
   0x5555555551b7 <main+22>: call   0x555555555169 <sum_of_squares>
   ...
```

`=>` 標出 `$pc`（current instruction）。

Ch 8 會更深入 disassembly。

## 記憶體單位的「黏性」

`x` 的 `N`、`F`、`U` 都會**記住**，下次你打 `x/ ADDR`（不指定格式）會沿用。

```
(gdb) x/4wx &arr      ← 設定 4 個 word、hex
(gdb) x &arr2         ← 繼續用同格式，看 &arr2
(gdb) x               ← 沒給位址，接著上次的位置往下印
```

最後那個超好用 — 連續按 `x` Enter 就是「翻頁看記憶體」。

## 位址運算

`x` 吃的位址可以是任何運算式：

```
(gdb) x/4wx arr + 2       ← 從 arr 的第 2 個 element 開始印
(gdb) x/4wx $rsp          ← 看 stack top 附近
(gdb) x/4wx $rbp - 0x10   ← 看 frame 裡某個 offset
(gdb) x/s 0x555555556004  ← 絕對位址
```

## 看 stack：最常用的 debug 動作之一

當你懷疑 stack 被破壞，看一眼 `$rbp` 附近：

```
(gdb) x/16gx $rbp - 0x40
```

看：局部變數區、saved registers、canary（如果有 stack protector）。

### Stack canary

```c
gcc -g -fstack-protector-strong sample.c -o sample
```

編出來的函式開頭會放一個 canary，return 前會檢查。用 `x` 看得到：

```
(gdb) x/16gx $rbp - 0x40
0x7fffffffdfa0: 0x0000000000000005 0x00007ffff7dbc7a8
0x7fffffffdfb0: ...
0x7fffffffdfc0: ...
0x7fffffffdfd0: 0x36108b47f6e4f800 0x00007ffff7daa1ca  ← canary + saved RIP
```

那個 `0x36...` 看起來像隨機數的就是 canary。Ch 19 會細講。

## 看 heap

用 malloc 拿到的位址：

```c
int *arr = malloc(5 * sizeof(int));
arr[0] = 1; arr[1] = 4; ...
```

```
(gdb) p arr
$1 = (int *) 0x5555555592a0

(gdb) x/5wx arr
0x5555555592a0: 0x00000001  0x00000004  ...
```

想看 heap chunk 的 **metadata**（glibc 在你 malloc 給的位址前放 size 等資訊）：

```
(gdb) x/4gx arr - 16
0x555555559290: 0x0000000000000000  0x0000000000000021
0x5555555592a0: 0x0000000400000001  ...
```

那個 `0x21` 就是 chunk size（包含 metadata）+ flags。之後 heap corruption 的練習 B 會用到。

## 實例：找出 struct 的實際 layout

```c
struct Foo {
    char c;
    int i;
    char c2;
    double d;
};
struct Foo f = {'A', 42, 'B', 3.14};
```

你猜它多大？1 + 4 + 1 + 8 = 14？錯：

```
(gdb) p sizeof(f)
$1 = 24
(gdb) x/24b &f
0x7fffffffe140: 0x41 0x00 0x00 0x00 0x2a 0x00 0x00 0x00
0x7fffffffe148: 0x42 0x00 0x00 0x00 0x00 0x00 0x00 0x00
0x7fffffffe150: 0x1f 0x85 0xeb 0x51 0xb8 0x1e 0x09 0x40
```

看到了嗎？

- `0x41` = 'A'（c）
- 然後 3 個 `0x00` — **padding**（讓 int 對齊到 4 byte）
- `0x2a` = 42（i）
- `0x42` = 'B'（c2）
- 然後 7 個 `0x00` — **padding**（讓 double 對齊到 8 byte）
- 最後 8 byte = 3.14 的 IEEE 754

`x` 讓你**眼見為憑**地理解 struct padding。

## `find` — 搜尋記憶體

GDB 可以在 inferior 記憶體裡搜 pattern：

```
(gdb) find &low, &high, 0xdeadbeef
(gdb) find /w 0x7fffffffe000, +0x1000, 42       # 在 stack 的這段裡找 int 值 42
(gdb) find /s 0x555555554000, +0x10000, "hello" # 找字串
```

分別用 `/b /h /w /g /s` 指定單位。

## 一個容易混的觀念：虛擬位址 vs 實體位址

你在 gdb 看到的位址全都是**虛擬位址**（也就是 inferior 自己的 page table 映射的位址）。這代表：

- 同一個位址在 GDB process 跟 inferior process 意義不同 — GDB 不能直接在自己 process 裡 `memcpy`，必須走 ptrace。
- 兩個不同的 process 的 `0x5555555592a0` 是兩個不同的實體記憶體。
- 如果你看到位址大於 `0x0000800000000000`，那可能是 kernel 或 vDSO 映射，不是普通 heap/stack。

用 Linux 的 `/proc/PID/maps` 可以看 inferior 的完整記憶體地圖：

```
(gdb) info proc mappings
```

或：

```bash
cat /proc/PID/maps
```

會看到類似：

```
555555554000-555555555000 r--p 00000000 fd:00 ...  /tmp/sample
555555555000-555555556000 r-xp 00001000 fd:00 ...  /tmp/sample
555555556000-555555557000 r--p 00002000 fd:00 ...  /tmp/sample
7ffff7c00000-7ffff7c28000 r--p 00000000 fd:00 ...  /usr/lib/x86_64-linux-gnu/libc.so.6
7ffff7fb7000-7ffff7fbb000 r--p                      [vvar]
7ffffffdd000-7ffffffff000 rw-p 00000000 00 0       [stack]
```

## 常見坑

1. **`x/s ptr` 印出一堆亂碼**：`ptr` 沒指向 null-terminated 字串，印到下一個 `\0` 為止才停 — 可能吃到幾 KB。
2. **`Cannot access memory at address 0xXXX`**：位址不在任何 mapping 裡，或者無讀取權限。用 `info proc mappings` 查。
3. **位址看起來很大（`0x7f...`）**：這是 Linux 的典型使用者空間高位址，stack、shared libs、mmap 都在這。不是壞掉。
4. **`x/4wx arr` 印出來的值跟 `p arr` 不同**：檢查 endianness、檢查你以為的型別大小。

## 動手練習

沿用 `sample.c`，再加兩個全域變數：

```c
int g_arr[4] = {0xdeadbeef, 0xcafebabe, 0x12345678, 0x87654321};
struct { char c; int i; } g_foo = {'X', 0x41424344};
```

1. 停在 main 後，`x/4wx g_arr`，驗證 endianness。
2. `x/16b g_arr`，看每個 byte。
3. `x/6b &g_foo` — 看 padding 在哪裡。
4. `x/4gx $rbp - 0x40` — 看看 stack frame。
5. `info proc mappings`，找出 `g_arr` 在哪個 segment（應該是 `.data`）。
6. 用 `find` 在 stack 裡搜 `0xdeadbeef`（不會找到，因為它在 data segment；然後在 data 範圍搜，應該找到）。

## 自我檢核

- [ ] 我能說出 `x` 跟 `p` 的差別
- [ ] 我知道 `/NFU` 三個位置的意義
- [ ] 我能用 `x/s`、`x/i`、`x/16gx` 看不同性質的記憶體
- [ ] 我能用 `x` 觀察 struct padding
- [ ] 我知道 `info proc mappings` 在幹什麼

下一章看 stack 的**結構化**視角 — `backtrace`、frame 切換、每一層 frame 的 local variable。

→ [Ch 5 Stack 與 frame](./05-stack-and-frames.md)
