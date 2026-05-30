# Ch 4 — Breakpoint 的世界

> **目標**：把 breakpoint 的「種類、定位方式、生命週期、管理」一次建立完整地圖。學完你能精準地在任何想要的位置（函式、行號、位址、條件、未載入的 library）下斷點，並管理一堆斷點。底層 INT3 / 硬體斷點機制留到 Ch 39。

> **環境**：GDB 13/14，Linux x86_64。

## 為什麼 breakpoint 是 debugger 的靈魂

執行控制（Ch 1 的三件核心工作之一）的本質就是：**讓程式在你想要的精確時刻停下來。** breakpoint 就是「想要的時刻」的最主要表達方式。你下斷點的功力，直接決定你 debug 的效率——下對地方，一步到位；下錯地方，按 `continue` 按到天荒地老。

很多人只會 `break funcname`。這章要讓你會七八種定位方式、條件斷點、臨時斷點、pending 斷點，並且能管理一整批。

## 先建立直覺：在地圖上插一根旗子

回到 Ch 0 的「地圖」。下斷點就是在地圖的某個點插旗子，告訴 GDB「程式跑到這就凍住、叫我」。

```
     程式執行流（時間 →）
     ─────●─────────────●──────────X──────────►
          │             │          │
       break add     break:42   到這裡崩潰
       (函式入口)    (某行)
          ▲
       旗子：跑到這停下來
```

底層怎麼做到「跑到這停」？最常見是**軟體斷點**：GDB 把那個位址的第一個 byte 換成 `0xCC`（INT3 指令），CPU 執行到就觸發 SIGTRAP、被 OS 凍結、通知 GDB。這個 patch-and-restore 的細節 Ch 39 會放大；這章先當黑盒，專注「怎麼下、下在哪」。

## breakpoint 的定位方式（linespec）

GDB 用 **linespec**（location 規格）描述「斷在哪」。把這些練熟，你就能精準命中任何點。

```
(gdb) break add                 # 函式名（停在函式 body 第一行，跳過 prologue）
(gdb) break hello.c:42          # 檔案:行號
(gdb) break 42                  # 目前檔案的第 42 行
(gdb) break *0x555555555149     # 精確位址（前面加 * ）— 沒符號時的命脈
(gdb) break *main               # main 的第一條指令（含 prologue，跟 break main 不同！）
(gdb) break +3                  # 從目前停的行往後 3 行
(gdb) break file.c:func         # 指定檔案裡的某函式（同名函式消歧義）
```

C++ 還有更多：

```
(gdb) break Foo::bar            # 類別方法
(gdb) break 'Foo::bar(int)'     # overload 消歧義（用引號）
(gdb) rbreak ^test_            # 正規表示式：所有 test_ 開頭的函式都下斷！
```

`rbreak`（regex break）超實用：一次對所有符合 pattern 的函式下斷。例如 `rbreak malloc` 把所有 malloc 相關都攔下來。

> 踩雷：`break main` 和 `break *main` 不一樣。`break main` 停在函式 body 第一行（GDB 跳過 prologue——那段 setup stack frame 的指令），所以你看到的參數值已經就緒。`break *main` 停在最最開頭第一條指令，prologue 還沒跑，參數可能還沒到位。日常用前者，Ch 39 看 prologue 細節時用後者。

## breakpoint 的種類

| 種類 | 指令 | 特性 |
|---|---|---|
| 普通斷點 | `break` / `b` | 永久，命中後留著 |
| 臨時斷點 | `tbreak` | 命中一次後**自動刪除** |
| 條件斷點 | `break ... if cond` | 只在條件成立時停（Ch 12 深入） |
| 硬體斷點 | `hbreak` | 用 CPU debug register，可斷在唯讀記憶體 / ROM（Ch 39） |
| dprintf | `dprintf loc, fmt, args` | 命中時印東西但**不停**（Ch 12） |
| pending | （自動） | 目標還沒載入時的待定斷點，見下節 |

`tbreak` 的用途：「我只想第一次進這函式時停，之後別煩我」。`start` 就是內部用 `tbreak main` 實作的。

## pending breakpoint：對還沒載入的東西下斷

你想對一個 shared library 裡的函式下斷，但那個 `.so` 還沒被 `dlopen` / 載入——符號還不存在。GDB 怎麼辦？

```
(gdb) break some_plugin_func
Function "some_plugin_func" not defined.
Make breakpoint pending on future shared library load? (y or [n]) y
Breakpoint 1 (some_plugin_func) pending.
```

選 `y`，GDB 記住這個斷點，等對應的 library 一載入就自動「實體化」它。要預設不問直接 pending：

```
(gdb) set breakpoint pending on
```

這對 debug 動態載入的 plugin、`dlopen` 的模組、或還沒進入的程式階段非常關鍵。

## 管理一堆斷點

下了十幾個斷點後，你需要管理：

```
(gdb) info breakpoints          # 列出全部（簡寫 i b）
Num     Type           Disp Enb Address            What
1       breakpoint     keep y   0x...1149 in main at hello.c:9
2       breakpoint     keep y   0x...1131 in add  at hello.c:4
        breakpoint already hit 3 times

(gdb) disable 2                 # 暫時停用 2（留著但不觸發）
(gdb) enable 2                  # 重新啟用
(gdb) delete 2                  # 刪除 2
(gdb) delete                    # 刪除全部（會問）
(gdb) ignore 2 5                # 忽略斷點 2 接下來 5 次命中
(gdb) clear add                 # 刪掉位在 add 的斷點（用 location 而非編號）
```

幾個欄位讀法：

- **Disp**：`keep`（普通）或 `del`（臨時，命中即刪）
- **Enb**：`y` / `n`，是否啟用
- **already hit N times**：命中過幾次——debug 迴圈時很有用

`disable` vs `delete` 的智慧：當你在縮小問題範圍、想暫時關掉某些斷點但等下可能還要用，`disable` 比 `delete` 好——保留編號和條件。

## breakpoint 與 location 的一對多

一個 linespec 可能對應**多個實際位址**（multiple locations）。最常見：

- inline 函式被展開到很多地方
- C++ template 實例化成多個版本
- 同名 static 函式存在於多個檔案

```
(gdb) break process
Breakpoint 1 at 0x1149: process. (3 locations)
(gdb) info breakpoints
Num     Type           Disp Enb Address    What
1       breakpoint     keep y   <MULTIPLE>
1.1                         y   0x...1149 in process at a.c:10
1.2                         y   0x...2271 in process at b.c:22
1.3                         y   0x...3390 in process at c.c:5
```

你可以單獨 enable/disable 某個 location：`disable 1.2`。理解這個一對多，debug template-heavy 的 C++（Ch 29）才不會困惑。

## 一個實戰流程

```
$ gdb -q --args ./parser config.txt
(gdb) start                          # 停在 main
(gdb) rbreak ^parse_                 # 對所有 parse_ 函式下斷，鳥瞰流程
(gdb) break parser.c:120 if depth > 3   # 只在遞迴深度 >3 時停（Ch 12）
(gdb) tbreak cleanup                 # cleanup 只想看一次
(gdb) info breakpoints               # 檢視全貌
(gdb) continue
...
(gdb) ignore 3 100                   # 這個太吵，先忽略 100 次
(gdb) continue
```

下斷點是一種「策略」：先用 `rbreak` 鳥瞰、再用條件斷點聚焦、用 `ignore` / `disable` 過濾雜訊。

## 踩雷集錦

1. **`break main` 落空**：可能是 strip 過（無符號，改用 `break *0x位址`）、或 binary 沒 `main`（如 PIE 的某些情況、或入口是 `_start`）。`info functions main` 查。
2. **斷點「沒作用」**：常見是斷在**不會執行到**的程式碼路徑，或那段被最佳化掉了（Ch 32）。確認 `info breakpoints` 的 hit count——0 次就是根本沒到。
3. **斷在迴圈裡按 continue 按到死**：用條件斷點（`if i == 500`，Ch 12）或 `ignore N 次`，別硬按。
4. **以為 `disable` 會刪掉斷點**：`disable` 只是停用，斷點還在、條件還在。要清掉用 `delete`。
5. **PIE/ASLR 下位址斷點失效**：`break *0x...` 用的是執行期位址，但 PIE 每次載入位址不同（Ch 40）。要嘛 `set disable-randomization on`（預設就是），要嘛用符號 / 相對偏移。
6. **多 location 只想斷其中一個**：別 `delete` 整個斷點，用 `disable 1.2` 關掉特定 location。

## 進階：再往深一層

- **`save breakpoints file.txt`**：把目前所有斷點存成可重載的腳本，下次 `source file.txt` 一次重建。長期 debug 同一個 bug 必備。
- **`break ... thread N`**：只對特定 thread 生效的斷點（Ch 16）。
- **`break ... if cond` + `commands`**：條件斷點配上命中時自動執行的指令串，可做到「命中就印某些值再自動繼續」——半自動化 debug（Ch 12）。
- **硬體斷點的數量限制**：x86 只有 4 個 debug register（DR0–DR3），所以 `hbreak` 和硬體 watchpoint 加起來最多 4 個。超過 GDB 會報錯。Ch 13、Ch 39。
- **`set breakpoint auto-hw`**：GDB 自動決定軟/硬體斷點。對唯讀記憶體（無法 patch INT3）會自動改用硬體斷點。

## 動手練習

1. 對 `hello.c` 用六種 linespec 各下一個斷點（函式、行號、`檔案:行`、`*位址`、`+n`、regex），`info breakpoints` 看它們。
2. 寫一個有遞迴的函式，用 `tbreak` 只停第一次進入，對比 `break` 每次都停。
3. 寫一個 `dlopen` 載入 plugin 的程式，對 plugin 內函式下 pending 斷點，觀察載入瞬間它如何實體化。
4. 對一個有 inline 函式或 C++ template 的程式 `break`，觀察 `<MULTIPLE>` 與 `1.1 / 1.2` location，練習 `disable 1.2`。
5. `save breakpoints bp.txt`，`delete` 全部，再 `source bp.txt` 還原。

## 本章重點整理

- breakpoint 是執行控制的主要表達；底層多半是 patch INT3（Ch 39），這章專注怎麼下、下在哪。
- linespec 有多種：函式、行號、`檔案:行`、`*位址`、regex（`rbreak`）、`+n`。`break f` 跳過 prologue，`break *f` 不跳。
- 種類：普通 / 臨時（tbreak）/ 條件 / 硬體（hbreak）/ dprintf / pending。
- 一個 linespec 可能對應多個 location（inline、template、同名）；可單獨 enable/disable。
- 管理靠 `info breakpoints` + enable/disable/delete/ignore；`disable` 保留、`delete` 清掉。

## 自我檢核

- [ ] 不查表，講得出至少五種 linespec 寫法嗎？
- [ ] `break main` 與 `break *main` 差在哪？為什麼？
- [ ] 對一個還沒 `dlopen` 的 library 函式下斷，要怎麼做？
- [ ] 斷點 hit count 是 0 代表什麼？你會怎麼排查？
- [ ] 為什麼硬體斷點數量有限？大概限制在多少？

## 延伸閱讀

### 官方文件

- **[GDB Manual: Breakpoints, Watchpoints, and Catchpoints](https://sourceware.org/gdb/current/onlinedocs/gdb/Breakpoints.html)**
  - **讀哪裡**：Setting Breakpoints、Specifying Locations（linespec 的完整文法）、Deleting/Disabling Breakpoints。
  - **和本章的關聯**：linespec 文法的權威定義；condition 與 commands 留到 Ch 12 再讀。

- **[GDB Manual: Specify Location](https://sourceware.org/gdb/current/onlinedocs/gdb/Specify-Location.html)**
  - **讀哪裡**：linespec、explicit location、address location 三種寫法。
  - **和本章的關聯**：把本章的定位方式講到最完整，含 `-function`/`-line` 的 explicit 寫法。

### 部落格 / 文章

- **[How debuggers work: Part 2 (Breakpoints)](https://eli.thegreenplace.net/2011/01/27/how-debuggers-work-part-2-breakpoints)** — Eli Bendersky
  - **這篇說什麼**：軟體斷點的 INT3 patch 怎麼用 ptrace 做出來。
  - **和本章的關聯**：本章把 INT3 當黑盒，這篇先讓你偷看；Ch 39 會完整實作。

完成 Part 1 的觀念地基後，用練習 A 把它們綜合起來：對一個沒有原始碼的程式，光靠執行控制與斷點逆出它的邏輯。

→ [練習 A：逆出一個無原始碼程式的控制流](./practice-a-controlflow-reversing.md)
