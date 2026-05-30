# 練習 A — 逆出一個無原始碼程式的控制流

> **目標**：把 Part 1（ptrace 心智模型、run/attach、breakpoint、執行控制）綜合起來。你會拿到一個**只有 binary、沒有原始碼**的 "crackme"，目標是用純 GDB 找出它接受的密碼，並畫出它的控制流。完成後你會驗證：就算沒有原始碼，GDB 依然是你最強的逆向工具。

## 背景與動機

真實世界裡，你要 debug 的東西常常**沒有原始碼**：第三方函式庫、被 strip 的線上 binary、惡意程式、CTF 題目。這時 `list`、`print x` 全部失效，你只剩執行控制與記憶體檢視。這個練習刻意把你丟進這個處境，逼你用 Part 1 的底層理解工作——這正是逆向工程與 pwn 的入門姿勢。

## 任務規格

### 你要做的事

1. 自己用下面的原始碼**產生 binary，然後當作沒看過原始碼**（編完就把 `.c` 收起來）。
2. 只用 GDB（不准用 `objdump -d` 全 dump、不准用反編譯器如 Ghidra/IDA），找出 `checkme` 接受什麼密碼。
3. 畫出這個程式的控制流：main 呼叫了哪些函式、判斷在哪、成功/失敗各走哪條路。
4. 用 GDB **不修改 binary、不知道密碼**的前提下，靠改暫存器或記憶體讓它印出 "Access granted"。

### 產生 binary（產生完請假裝沒看過）

```c
// crackme.c — 編譯：gcc -O0 crackme.c -o crackme
//           （故意不給 -g，模擬無 debug info 的逆向情境）
#include <stdio.h>
#include <string.h>

static int transform(const char *s) {
    int acc = 0;
    for (int i = 0; s[i]; i++)
        acc = acc * 31 + (unsigned char)s[i];
    return acc;
}

static int check(const char *input) {
    /* 正解的 transform 值；密碼本身不直接出現在 binary 字串裡 */
    return transform(input) == 0x7c8df2;
}

int main(int argc, char **argv) {
    if (argc != 2) {
        printf("usage: %s <password>\n", argv[0]);
        return 1;
    }
    if (check(argv[1])) {
        printf("Access granted.\n");
        return 0;
    }
    printf("Access denied.\n");
    return 1;
}
```

> 注意：故意用 `gcc -O0 crackme.c -o crackme`（**沒有 `-g`**）。這樣 `break main` 還能用（`main` 在 symtab），但 `print acc`、`list` 不能用——逼你在無 DWARF 的層級工作。`static` 函式 `transform`/`check` 的符號可能還在 `.symtab`（`-O0` 不會 strip），這沒關係，逆向時本來就是有什麼線索用什麼。

### 驗收標準

- [ ] 你能讓 `./crackme <你找到的密碼>` 印出 `Access granted.`
- [ ] 你能說出 `check` 是怎麼驗證的（不必逐位元組，但要講出「它把輸入做某種轉換再比一個常數」）
- [ ] 你能在**不給對的密碼**時，靠 GDB 改某個暫存器/記憶體，讓程式走到 "Access granted" 那條路
- [ ] 你能畫出 main → check → transform 的呼叫關係與分支

## 期望輸出範例

```
找到密碼後：
$ ./crackme PASSWORD
Access granted.

用 GDB 強闖（不知道密碼）：
(gdb) run wrongpass
... 停在比較處 ...
(gdb) set $eax = 1        # 或改 ZF，讓判斷反過來
(gdb) continue
Access granted.
```

## 如果你卡住了

1. **沒有 `-g` 怎麼定位？** 函式名還在（`info functions`）。先 `break check`、`break transform`，看程式怎麼在它們之間流動。
2. **看不到變數怎麼知道在比什麼？** 比較發生在組語層。`break check` 後用 `stepi` 一條條走，配合 `info registers` 看暫存器怎麼變。那個 `0x7c8df2` 常數會出現在某次比較裡。
3. **怎麼知道 transform 算出多少？** 在 `transform` 的 `ret` 前停下，看回傳值暫存器（x86-64 是 `$rax`/`$eax`）。
4. **怎麼強闖？** 找到那個 `if` 對應的條件跳躍指令（`je`/`jne`），在它執行前改 `$eax` 或直接改 `$pc` 跳過去。Ch 11 會深入暫存器，但這裡你已經夠用。

## 實作步驟建議

### Step 1：偵察——這程式有哪些函式？

```
$ gdb -q ./crackme
(gdb) info functions          # 看有哪些符號
(gdb) break main
(gdb) run wrongpass
```

子目標：確認 `main`、`check`、`transform` 存在，建立「下斷點鳥瞰」的起手式。

### Step 2：追控制流——main 怎麼決定 granted/denied？

```
(gdb) break check
(gdb) continue
(gdb) finish                  # 跑完 check，看它的回傳值
```

子目標：發現 `check` 回傳 0/1 決定成敗，`finish` 看到 `Value returned is $1 = 0`。理解 main 裡有個 `if (check(...))`。

### Step 3：鑽進 check / transform 看「比什麼」

```
(gdb) break transform
(gdb) run wrongpass
(gdb) finish                  # transform 算出的值在 $rax
```

子目標：拿到「wrongpass 的 transform 值」，並在 `check` 裡找到它跟 `0x7c8df2` 比較。用 `stepi` + `x/i $pc` 看那條 `cmp`。

### Step 4：強闖（不靠密碼）

子目標：在比較之後、條件跳躍之前，改暫存器讓判斷成立。例如把比較結果改掉，或 `set $eax = 1` 蓋掉 `check` 回傳值（在 `check` 的 `finish` 之後、main 用它之前）。`continue` 看到 granted。

### Step 5：真正解出密碼（加分核心）

子目標：`transform` 是 `acc = acc*31 + c` 的 hash，要反推出原文不容易（這是設計者的用意）。但你可以**爆破**：寫個小 script 或在 GDB 裡對候選字串呼叫 `transform`（inferior call，Ch 8 會教 `print transform("abc")`——但無 `-g` 可能要用位址呼叫）。或者直接接受「強闖」作為通關，把反推當延伸挑戰。

## 完整參考解答

**自己動手卡關後再看。**

<details>
<summary>點開逆向流程與強闖手法</summary>

### 偵察

```
(gdb) info functions
All defined functions:
0x...1149  main
0x...1135  check
0x...1112  transform
```

三個函式都在。控制流猜測：`main → check → transform`。

### 確認控制流

```
(gdb) break main
(gdb) run wrongpass
(gdb) break check
(gdb) break transform
(gdb) continue
Breakpoint 3, 0x...1112 in transform ()    # main 先呼叫了 check，check 再呼叫 transform
(gdb) finish
Run till exit from #0 ...transform...
0x...115a in check ()
Value returned is $1 = 8262542            # wrongpass 的 transform 值（十進位）
(gdb) finish
Run till exit from #0 ...check...
0x...1180 in main ()
Value returned is $2 = 0                   # check 回 0 → denied
```

所以結構是：

```
main(argc, argv)
 ├─ argc != 2 ? → usage, return 1
 └─ check(argv[1])
       └─ transform(input) == 0x7c8df2 ?
              ├─ true  → "Access granted",  return 0
              └─ false → "Access denied",   return 1
```

### 看那個關鍵比較

```
(gdb) break check
(gdb) run wrongpass
(gdb) disassemble                 # 看 check 的組語（disassemble 單一函式，不算「全 dump」）
   ... 
   0x...1156 <+33>: cmp  $0x7c8df2, %eax      ← 跟常數比！
   0x...115b <+38>: jne  0x...1167            ← 不等就跳去 return 0
   ...
```

`0x7c8df2` = 8162290（十進位）就是目標 transform 值。

### 強闖手法一：改回傳值

```
(gdb) break check
(gdb) run wrongpass
(gdb) finish                      # 跑完 check 回到 main
(gdb) set $eax = 1                # 把 check 的回傳值 (在 eax) 改成 1
(gdb) continue
Access granted.
```

### 強闖手法二：改條件跳躍

在 `check` 裡停在 `cmp` 之後、`jne` 之前：

```
(gdb) break *(check+33)           # cmp 那條（位址依你的 binary）
(gdb) run wrongpass
(gdb) set $eax = 0x7c8df2         # 讓 cmp 的兩邊相等 → ZF=1 → jne 不跳
(gdb) continue
Access granted.
```

或更暴力，直接改 PC 跳過 `jne`：`set $pc = <jne 之後的位址>`。

### 真正解密碼（延伸）

`transform` 是 `acc*31+c` 的多項式 hash，不可逆。用爆破：對所有短字串算 transform 比對 `0x7c8df2`。一個 4 字元 ASCII 的搜尋空間很小。寫個 C 或 Python 跑：

```python
target = 0x7c8df2
import itertools, string
cs = string.printable
for n in range(1,5):
    for t in itertools.product(cs, repeat=n):
        s = ''.join(t)
        acc = 0
        for ch in s: acc = (acc*31 + ord(ch))   # 注意 C 的 int overflow，需 & 0xffffffff 並處理 signed
        if (acc & 0xffffffff) == target:
            print(repr(s)); 
```

（精確爆破要模擬 C `int` 的 32-bit 溢位與 signed 行為——這正是「逆向要懂底層型別」的一課，留給你調。）

**解答說明**：這題的教學點是——沒有 `-g`、沒有反編譯器，光靠 `break` / `finish` / `disassemble` 單函式 / `stepi` / 改暫存器，你就能完全掌控一個未知程式。`finish` 看回傳值、`set $reg` 改狀態，是逆向與 pwn 的麵包奶油。

</details>

## 測試用例

| 操作 | 預期結果 | 說明 |
|---|---|---|
| `run wrongpass` | Access denied. | 正常失敗路徑 |
| `break check` + `finish` + `set $eax=1` + `continue` | Access granted. | 改回傳值強闖 |
| `info functions` | 列出 main/check/transform | 無 `-g` 仍有 symtab |
| `break transform` + `finish` | `Value returned = <某數>` | 看回傳值暫存器 |
| 對的密碼（爆破出） | Access granted. | 真正解出 |

## 延伸挑戰（加分）

1. **完整爆破**：精確模擬 C `int` 的 32-bit signed 溢位，真的算出一個可用密碼，不靠強闖。
2. **不用 disassemble**：只靠 `stepi` + `x/i $pc` 一條條走，重建 check 的邏輯——體會最純粹的動態逆向。
3. **anti-debug 版**：在 `crackme.c` 的 `main` 開頭加 `if (ptrace(PTRACE_TRACEME,0,0,0) < 0) { puts("debugger!"); return 1; }`，重編。現在它偵測到被 debug 就拒絕。想辦法用 GDB 繞過（提示：在 ptrace 呼叫後改 `$rax`，或 `set $pc` 跳過檢查）。這串起 Ch 2 的「一個 tracee 只能一個 tracer」。
4. **attach 版**：把程式改成讀 stdin 的無窮迴圈，跑起來後 `gdb -p` attach，在它讀下一行前下斷，動態改它的判斷。

## 自我檢核

- [ ] 沒有原始碼時，我還能用 GDB 做哪些事、不能做哪些事？（對照 Ch 1）
- [ ] 我能用 `finish` 看任意函式的回傳值，並解釋回傳值在哪個暫存器
- [ ] 我能在條件跳躍前改暫存器，扭轉程式的控制流
- [ ] 我能說出自己的逆向流程和參考解答的差異，並解釋各自取捨

把 Part 1 的地基踩穩了，接下來 Part 2 進入 debug 的另一半核心：在程式停下來後，怎麼看穿它的內部狀態。

→ [Ch 5 Stepping 全家](./05-stepping.md)
