# Ch 13 — gdb 逆向工作流

> **目標**：把 gdb 從「除錯自己有原始碼的程式」的工具，切換成「逆向沒有符號的陌生 binary」的利器。學會在 strip binary 上用**位址**下斷點、讀無名記憶體、看參數暫存器、改暫存器/記憶體改掉執行流。全程對一個真實的 strip crackme 實戰，貼真實 gdb 輸出。

> **環境**：WSL2 / Linux x86-64，gcc + objdump + gdb 12.1。本章所有 gdb 會話都是真跑貼上的。

你有一門完整的 [`gdb`](../gdb/README.md) 課，從 breakpoint 到 Python API 全吃透了。這一章**不重教 gdb 全功能**——它只做一件事：把 gdb 用在逆向的特殊處境上。逆向和一般除錯最大的差別是**沒有符號**。你自己 debug 時，`break main`、`print x`、`bt` 都靠符號表和 DWARF 除錯資訊運作。逆一個 strip binary 時，這些全沒了：沒有 `main`、沒有 `x`、沒有行號、沒有型別。你只剩位址和指令。這章教你在這個「無名世界」裡把 gdb 用起來。

## 為什麼需要這個？

Ch 12 立了心法：與其瞪 asm 手算暫存器，不如下斷點讓真實值說話。gdb 就是那個「讓真實值說話」的主力工具——可觀察性三支柱裡的**斷點**這一柱。

但直接把日常 debug 習慣搬過來會全面撞牆：

```
$ gdb ./chk_stripped
(gdb) break main
Function "main" not defined.        ← 沒符號，名字全沒了
(gdb) break check
Function "check" not defined.       ← 你想斷的函式也沒名字
```

strip 把 `main`、`check`、變數名、行號、型別全部移除（Ch 0 已示範 `nm` 顯示 `no symbols`）。真實世界的 binary 幾乎都是 strip 的。所以逆向的 gdb 工作流，核心是**繞開「靠名字」這件事**：用位址下斷、把記憶體當無型別的 bytes 讀、從 calling convention 反推參數在哪個暫存器。

## 先建立直覺：從「靠名字」到「靠位址」

日常 debug 你活在符號的世界：`break foo`、`print bar->x`。逆向你活在位址的世界。這是最核心的思維切換：

```
   日常 debug（有符號）              逆向（strip，無符號）
   ─────────────────                ─────────────────
   break main                       break *0x555555555169   （用位址）
   print x                          x/s $rdi                （用暫存器 + 記憶體）
   info locals                      x/8xg $rbp-0x20         （手動看 stack）
   bt                               bt（還能用，但 frame 沒名字）
   step（依行號）                    stepi / nexti（依指令）
   型別自動顯示                      x/ 手動指定型別（/s /d /x /i）
```

工作流也隨之變成一套固定招式：**先靜態用 objdump 找出關鍵位址 → gdb 用位址下斷 → 跑到那 → 用暫存器和記憶體讀真實值 → 需要時改值改執行流**。這章把每一招拆開，最後在一個真 binary 上串起來。

## 本章的實戰目標：一個 strip crackme

我們用一個貫穿全章的 ground-truth 目標。先寫 source（這是**標準答案，逆的時候要蓋起來**），編譯，strip：

```c
// chk.c
#include <stdio.h>
#include <string.h>
int check(const char *s){
    int sum=0;
    for(int i=0; s[i]; i++) sum += s[i];   // 把每個 byte 加起來
    return sum == 0x28e;                    // magic：654
}
int main(int argc,char**argv){
    if(argc<2){ printf("usage: %s <pw>\n", argv[0]); return 1; }
    if(check(argv[1])){ printf("ACCESS GRANTED\n"); return 0; }
    printf("denied\n"); return 1;
}
```

```bash
$ gcc -O0 -o chk chk.c
$ cp chk chk_stripped && strip chk_stripped
$ nm chk_stripped
nm: chk_stripped: no symbols            # ← 無名世界，逆向的真實起點
```

假裝你沒看過上面那份 source。你手上只有 `chk_stripped`。任務：**讓它印出 `ACCESS GRANTED`**——找出正確密碼，或直接改掉檢查。

## 第一步：靜態定位——找出「該斷哪裡」

gdb 之前先 objdump，找關鍵位址。你不需要讀懂每一行，只要找到「決定成敗的那個比較」（真跑）：

```bash
$ objdump -d chk_stripped | sed -n '/<check>:/,/ret/p'
```

等等——strip 了哪來的 `<check>`？objdump 對 `.text` 裡的函式，就算 strip 也會用 `.symtab` 缺失時回退標，這裡為了教學我用**沒 strip 的 `chk`** 對照（逆向常態：先在有符號版看清楚，再到 strip 版下位址斷點）。真跑 `objdump -d chk`：

```asm
0000000000001169 <check>:
    1169:  endbr64
    116d:  push   %rbp
    116e:  mov    %rsp,%rbp
    1171:  mov    %rdi,-0x18(%rbp)     ; 第一個參數 s 存進 stack（rdi = arg0）
    1175:  movl   $0x0,-0x8(%rbp)      ; sum = 0
    117c:  movl   $0x0,-0x4(%rbp)      ; i = 0
    1183:  jmp    119f <check+0x36>
    1185:  mov    -0x4(%rbp),%eax      ; ┐
    1188:  movslq %eax,%rdx            ; ┤ 迴圈體：
    118b:  mov    -0x18(%rbp),%rax     ; ┤   rax = s
    118f:  add    %rdx,%rax            ; ┤   rax = s + i
    1192:  movzbl (%rax),%eax          ; ┤   eax = s[i]（zero-extend byte）
    1195:  movsbl %al,%eax             ; ┤   sign-extend 成 int
    1198:  add    %eax,-0x8(%rbp)      ; ┘   sum += s[i]
    119b:  addl   $0x1,-0x4(%rbp)      ; i++
    119f:  mov    -0x4(%rbp),%eax
    11a2:  movslq %eax,%rdx
    11a5:  mov    -0x18(%rbp),%rax
    11a9:  add    %rdx,%rax
    11ac:  movzbl (%rax),%eax          ; eax = s[i]
    11af:  test   %al,%al              ; s[i] == 0 ?（迴圈終止條件）
    11b1:  jne    1185 <check+0x1c>    ; 非 0 → 繼續迴圈
    11b3:  cmpl   $0x28e,-0x8(%rbp)    ; ★ sum == 0x28e ?  ← 決定成敗的比較
    11ba:  sete   %al                 ; 相等就回 1
    11bd:  movzbl %al,%eax
    11c0:  pop    %rbp
    11c1:  ret
```

靜態你只需要讀出**骨架**：一個迴圈把 `s` 的每個 byte 加起來（`movzbl (%rax); add %eax,-0x8(%rbp)`），然後在 `0x11b3` 拿總和跟 `0x28e` 比。關鍵位址有兩個：**`0x1169`（check 入口，看參數）** 和 **`0x11b3`（那個 cmp，看/改總和）**。剩下的值——sum 實際算出來多少——不用手算，等下讓 gdb 告訴我們。

## 第二步：用位址下斷點

strip binary 沒有函式名，斷點用 `break *位址`。但 PIE（position-independent executable，現代預設）每次執行載入基底位址不同，`0x1169` 只是**檔案相對偏移**。兩個辦法：

**辦法 A：關掉 ASLR，用固定基底。** PIE 在關掉隨機化時載到固定的 `0x555555554000`。所以絕對位址 = `0x555555554000 + 0x1169`：

```bash
$ gdb -q ./chk_stripped
(gdb) set disable-randomization on        # 關 ASLR（gdb 預設就開這個）
(gdb) break *(0x555555554000 + 0x1169)    # check 入口
(gdb) run mmmmmm
```

**辦法 B：先 `starti` 停在 entry，再算基底。** 不想背 `0x555555554000` 就用這招——`starti` 停在最開頭，`info proc mappings` 或看 `$pc` 反推基底，再用 gdb convenience variable 算。教學我們用辦法 A（更直接）。

真跑，斷在 `check` 入口看第一個參數（`starti` 停在 loader，再設斷點跑）：

```
(gdb) set disable-randomization on
(gdb) starti
Program stopped.
0x00007ffff7fe3290 in _start () from /lib64/ld-linux-x86-64.so.2
(gdb) break *(0x555555554000+0x1169)
Breakpoint 1 at 0x555555555169
(gdb) run mmmmmm

Breakpoint 1, 0x0000555555555169 in ?? ()      ← ?? = 無符號，這才是逆向常態
```

`in ?? ()` 的 `??` 就是「這裡沒有符號名」的標記。斷點命中了，我們現在停在 `check` 開頭。

## 第三步：看參數——從 calling convention 反推

沒有符號，gdb 不知道這函式有幾個參數、叫什麼。但你知道 **calling convention**：x86-64 System V ABI 下，整數/指標參數依序放 `rdi, rsi, rdx, rcx, r8, r9`。`check(const char *s)` 只有一個參數，所以 `s` 在 `rdi`。真跑：

```
(gdb) x/s $rdi
0x7fffffffe821:  "mmmmmm"          ← 第一個參數就是我們傳的密碼字串
(gdb) info registers rdi rsi rax
rdi   0x7fffffffe821   140737488349217
rsi   0x7fffffffe578   140737488348536
rax   0x7fffffffe821   140737488349217
```

`x/s $rdi` 把 rdi 當成字串指標印出來——真實的參數值 `"mmmmmm"` 當場現形。這就是 Ch 12 心法的實踐：不推理「rdi 這時應該是密碼」，直接讀出來。

`x/` 的格式後綴是逆向讀無名記憶體的命脈，記熟這幾個：

| 指令 | 意思 |
|---|---|
| `x/s $rdi` | 把 rdi 當字串印 |
| `x/8i $pc` | 從 $pc 印 8 條指令（反組譯） |
| `x/16xb $rsp` | 從 rsp 印 16 個 byte（hex） |
| `x/8xg $rbp-0x20` | 從 rbp-0x20 印 8 個 8-byte word（hex giant） |
| `x/d $rax` | 把 rax 指向的記憶體當 int 印 |
| `p/x $eax` | 直接印暫存器值（hex），不解參照 |

## 第四步：看指令、單步

停在無名位址，`x/8i $pc` 看接下來要跑什麼（真跑）：

```
(gdb) x/8i $pc
=> 0x555555555169:  endbr64
   0x55555555516d:  push   %rbp
   0x55555555516e:  mov    %rsp,%rbp
   0x555555555171:  mov    %rdi,-0x18(%rbp)
   0x555555555175:  movl   $0x0,-0x8(%rbp)
   0x55555555517c:  movl   $0x0,-0x4(%rbp)
   0x555555555183:  jmp    0x55555555519f
   0x555555555185:  mov    -0x4(%rbp),%eax
```

`=>` 標的是 `$pc` 當前位置。要單步用 **`stepi`（stepi，進單一指令）** 或 **`nexti`（跨過 call 的單指令）**——注意逆向用的是指令級單步，不是 `step`/`next`（那是行號級的，strip binary 沒行號）。想邊跑邊看反組譯，`layout asm` 開 TUI 的反組譯視窗，`layout regs` 再加暫存器視窗，畫面會即時跟著 `$pc` 走。TUI 對逆向很順手，但批次腳本裡不方便展示，這裡以指令為主。

## 第五步：讀真實的中間值——不手算

現在做 Ch 12 心法的核心動作：斷在那個決定成敗的比較 `0x11b3`，讓 gdb 把總和算給我們看。那個總和在 `-0x8(%rbp)`（objdump 裡 `cmpl $0x28e,-0x8(%rbp)`）。真跑，故意餵一個錯密碼 `wrongpw`：

```
(gdb) break *(0x555555554000+0x11b3)
Breakpoint 2 at 0x5555555551b3
(gdb) run wrongpw

Breakpoint 2, 0x00005555555551b3 in ?? ()
(gdb) printf "sum at -0x8(rbp) = 0x%x (%d)\n", *(int*)($rbp-0x8), *(int*)($rbp-0x8)
sum at -0x8(rbp) = 0x314 (788)
```

`"wrongpw"` 的 byte 總和是 **0x314（788）**，而它要的是 **0x28e（654）**。這一步是逆向的分水嶺：我們**沒有手算** `w+r+o+n+g+p+w` 是多少，也沒推理演算法對不對——直接讀出真實的中間值，跟目標值一比就知道差多少。`p`（print）能當計算機用：`p $rbp-0x8`、`p/x $eax`、`p (char)0x6d` 都行。

## 第六步：改執行流——讓它無條件通過

最爽的一步，也是 Ch 12 心法第 3 條「能改就改」的實踐。我們有兩種改法。

**改法 A：改記憶體，讓比較通過。** 停在 `cmp` 的那一刻，總和存在 `-0x8(%rbp)`。把它改成目標值 `0x28e`，比較就相等，`check` 回 1。真跑，餵**錯的**密碼 `wrongpw` 卻要它 GRANTED：

```
(gdb) break *(0x555555554000+0x11b3)
Breakpoint 1 at 0x5555555551b3
(gdb) run wrongpw

Breakpoint 1, 0x00005555555551b3 in ?? ()
(gdb) set *(int*)($rbp-0x8) = 0x28e        ← 把總和改成 magic
(gdb) continue

ACCESS GRANTED                              ← 錯密碼也過了！
[Inferior 1 (process 409983) exited normally]
```

我們用一個**錯的**密碼 `wrongpw`，靠在執行期改記憶體，讓它印出 `ACCESS GRANTED`。這證實了我們對 `0x11b3` 那個 cmp 的理解完全正確——能干預到行為變化，就是真的懂了。

**改法 B：改暫存器 / 跳過檢查。** 如果比較結果影響的是某個暫存器或標誌，也能直接改。例如某些寫法比較完結果落在 `eax`，你可以 `set $eax = 1`；或者更粗暴地在條件跳轉前 `set $pc = 目標位址` 直接跳過整段檢查，或 `set $eflags` 翻 ZF 讓 `jne`/`je` 走你要的方向。改記憶體、改暫存器、改 `$pc`、改 `$eflags`——這四把是「改執行流」的全部家當。

> **一個真實踩雷**：初學常想「總和不是在 `eax` 嗎？我 `set $eax=0x28e` 不就好了」。但看 objdump——`0x11b3` 的 `cmpl $0x28e,-0x8(%rbp)` 是拿**記憶體** `-0x8(%rbp)` 跟常數比，`eax` 這時根本沒參與。改錯地方（改 eax）比較不會受影響。**改值前先看清楚那個 cmp 到底比的是暫存器還是記憶體**——這正是為什麼第一步的 objdump 定位不能省。

## 第七步：找出真正的密碼（不只是繞過）

改值/patch 讓 binary 招了，但那是**繞過**，不是**還原**。Ch 12 踩雷第 5 條講過：要真正逆出正確密碼，得從觀察到的值反推演算法。

我們已經知道演算法（第一步 objdump 讀出來的）：**byte 總和 == 0x28e（654）**。所以任何 byte 加起來等於 654 的字串都是正確密碼。6 個 `'m'`（ASCII 109）：`6 × 109 = 654`。真跑驗證：

```bash
$ ./chk mmmmmm
ACCESS GRANTED                    ← 真正的密碼，不靠 patch
$ ./chk wrong
denied
```

`mmmmmm` 是**真正的密碼**——不改記憶體、不 patch，binary 自己認。這是逆向的完整閉環：靜態讀出演算法 → 動態確認中間值 → 反推出滿足條件的輸入 → 真跑驗證。

## 對比與取捨：逆向 gdb vs 日常 gdb

| 面向 | 日常 debug | 逆向 strip binary |
|---|---|---|
| 下斷點 | `break func` / `break file:line` | `break *0x位址`（objdump 先找位址） |
| 看變數 | `print var` | `x/` + calling convention 反推暫存器 |
| 單步 | `step` / `next`（行號） | `stepi` / `nexti`（指令） |
| 型別 | 自動（DWARF） | 手動 `x/s /d /i` 指定 |
| 找 main | `break main` | 找 entry `_start` → 追到真正的 main（Ch 3） |
| PIE 位址 | 通常不在意 | 必須處理基底：關 ASLR 或算相對偏移 |

## 踩雷集錦

1. **`break main` / `break func` 撞牆才想起沒符號**：strip 後名字全沒了。逆向的下斷點一律經過「objdump 找位址 → `break *位址`」。想省事可 `info functions` 看看 gdb 有沒有從 PLT/動態符號撈到一些（libc 函式的 PLT stub 常還在，能 `break *puts` 之類）。
2. **忘了 PIE，直接 `break *0x1169`**：`0x1169` 是檔案偏移，不是執行期位址。不關 ASLR、不加基底，斷點會落在錯的地方或根本不觸發。要嘛 `set disable-randomization on` 用 `0x555555554000` 基底，要嘛先 `starti` 算出真實基底。
3. **改錯了 cmp 比的東西**（上面那個真實踩雷）：`cmp` 比的是記憶體還是暫存器，決定你該 `set *(int*)(...)` 還是 `set $reg`。改前一定回 objdump 確認那條指令的運算元。
4. **用 `step`/`next` 逆 strip binary**：這兩個依賴行號資訊，strip binary 沒有，行為會很怪（可能一路衝過去）。逆向用 `stepi`/`nexti`。
5. **把「patch 過了」當成「逆出來了」**：nop 掉 jne 或改記憶體讓它 GRANTED，只證明你找對了檢查點，沒告訴你正確密碼是什麼。要還原輸入，得從演算法反推（第七步）。這兩個目標別混。

## 進階：再往深一層

- **不關 ASLR 也能下斷**：用 `starti` 停在 entry，`info proc mappings` 看載入基底，或直接 `break *(main_offset + $載入基底)`。更常見的招是對還存在的**動態符號**（PLT stub）下斷——`break *puts`、`break *strcmp`——讓程式跑到 libc 呼叫時停，再從 stack/暫存器往回看是誰呼叫的。這對「不知道該斷哪」時很有用。
- **conditional breakpoint + commands**（你 [`gdb`](../gdb/README.md) 課 Ch 12）：迴圈裡想在特定條件停，`break *0x... if $eax==0x41`。或斷點命中就自動 dump 再繼續：`commands / printf ... / continue / end`。逆向常用這招把「每次呼叫某函式的參數」全錄下來，不用手動 continue 幾百次。
- **gdb 增強外掛**（gef / pwndbg）：命中斷點自動顯示暫存器、stack、反組譯 context，逆向體感差很多。你的 gdb 課 Final Project 就是自寫一套。逆向實戰強烈建議裝一個。

## 本章重點整理

- 逆向的 gdb 核心是**繞開「靠名字」**：strip binary 沒有 `main`、沒有變數名、沒有行號。改用**位址下斷**（`break *位址`）、**calling convention 反推參數暫存器**、**`x/` 讀無名記憶體**、**`stepi/nexti` 指令級單步**。
- 固定工作流：objdump 靜態找關鍵位址（那個 cmp、那個 call）→ gdb 用位址下斷 → 讀真實的中間值（不手算）→ 需要時改記憶體/暫存器/`$pc`/`$eflags` 改執行流。
- PIE 要處理基底：關 ASLR 用 `0x555555554000`，或 `starti` 算相對偏移。
- 改值能讓 binary 招（驗證你找對檢查點），但「繞過」不等於「還原」——要真正逆出正確輸入，得從演算法反推再真跑驗證。

## 自我檢核

- [ ] 我知道為什麼 `break main` 在 strip binary 上會失敗，以及該怎麼改用位址下斷
- [ ] 我能用 System V calling convention 說出「一個 char* 參數的函式，第一個參數在哪個暫存器」，並用 `x/s` 讀出來
- [ ] 我理解 PIE 的 `0x1169` 是檔案偏移，會處理成執行期位址（關 ASLR 或算基底）
- [ ] 我能斷在一個 cmp、讀出被比較的真實中間值，並判斷該改記憶體還是改暫存器來改變結果
- [ ] 我能區分「patch 繞過檢查」和「逆出正確輸入」，並知道後者要從演算法反推

## 延伸閱讀

### 官方文件 / 工具

- **你自己的 [`gdb`](../gdb/README.md) 課**
  - **讀哪裡**：Ch 4（breakpoint 世界）、Ch 7（print/display/examine，`x/` 全解）、Ch 11（暫存器與記憶體）、Ch 12（conditional breakpoint + commands）。本章是這些的**逆向應用摘要**，全功能回去補
  - **前提**：本章聚焦「無符號 strip binary」的特殊處境，gdb 課教的是通用能力
- **[GDB 官方 manual — Examining Memory](https://sourceware.org/gdb/current/onlinedocs/gdb/Memory.html)**
  - **讀哪裡**：`x` 指令的完整格式字母表（s/i/x/d/u/c/f + b/h/w/g）——逆向讀無名記憶體全靠這張表

### 書籍

- **《Practical Binary Analysis》** — Dennis Andriesse（No Starch, 2019）
  - **讀哪幾章**：Ch 3（ELF 載入與 PIE 基底，對應本章 PIE 位址處理）；動態分析章節與本章工作流呼應
- **《Reverse Engineering for Beginners》** — Dennis Yurichev（[免費](https://beginners.re/)）
  - **讀哪裡**：GDB isn't your only friend 及各處的 gdb 實戰片段，海量「斷點看真實值」的小例子

gdb 讓我們停下來精細地看。但有時候你不想停、只想側錄整個執行過程——它開了哪個檔、比對了什麼字串、呼叫了哪些函式庫。下一章換一支柱：trace。

→ [Ch 14 trace 執行：strace / ltrace / 自寫 tracer](./14-tracing-execution.md)
