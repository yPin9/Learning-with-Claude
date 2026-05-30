# Ch 32 — 除錯最佳化過的 binary

> **目標**：硬啃 release binary——理解 `-O2` 為什麼讓 debug 變難（`<optimized out>`、inline、變數搬到暫存器、指令重排、tail call），以及對抗策略：`-Og`、組語級 debug、從暫存器找變數、看穿 inline。這是真實世界最有價值也最痛的 debug 技能，因為線上崩潰的就是 release 版。

> **環境**：GDB 13/14，Linux x86_64，gcc 12+，對比 `-O0` / `-Og` / `-O2`。

## 為什麼這是最重要也最痛的一章

殘酷現實：**你開發時 debug 的是 `-O0`，但線上崩潰的是 `-O2`。** 線上崩潰、core dump、效能問題——全發生在最佳化過的 binary 上。只會 debug `-O0` 等於只會 debug 玩具。

最佳化讓 debug 變難，是因為它打破了「原始碼 ↔ 機器碼」的一對一假設（Ch 0 的地圖失真）。這章教你在地圖殘缺的情況下還能 debug——這是把你和「只會 debug demo」的人區分開的能力。

## 最佳化做了什麼破壞 debug

```c
// opt_demo.c — gcc -g -O2 opt_demo.c -o opt2 / -O0 -o opt0
#include <stdio.h>
static int helper(int x) { return x * x; }     // 會被 inline
int compute(int a, int b) {
    int temp = a + b;          // 可能不存在記憶體
    int sq = helper(temp);     // helper 可能 inline 進來
    return sq + a;
}
int main(void) {
    int r = compute(3, 4);
    printf("%d\n", r);
    return 0;
}
```

最佳化的五種「破壞」：

1. **變數沒進記憶體**：`temp` 可能只活在暫存器，從沒寫進 stack → `print temp` 給 `<optimized out>`。
2. **變數被消除**：常數摺疊、死碼消除可能讓 `temp` 整個消失。
3. **函式被 inline**：`helper` 被展開進 `compute`，`break helper` 落空，backtrace 沒有 helper 這層。
4. **指令重排**：編譯器重排指令，`step` 一行可能跳來跳去（你 step 到下一行，又跳回上一行的某部分）。
5. **tail call 優化**：`return f(x)` 變成 `jmp f` 而非 `call f`，backtrace 少一層。

## 體驗破壞

```
$ gdb -q ./opt2
(gdb) break compute
(gdb) run
(gdb) print temp
$1 = <optimized out>              # temp 不在記憶體
(gdb) break helper
Function "helper" not defined.    # 被 inline 了，不存在獨立函式
(gdb) next
... 行號跳來跳去 ...               # 指令重排
(gdb) bt
#0 compute (a=3, b=4)            # 注意：可能連 a/b 都 <optimized out>
#1 main ()                        # helper 不在 backtrace（inline）
```

對比 `-O0`：一切正常。這個落差就是你要跨越的。

## 策略一：`-Og`（為 debug 而生的最佳化）

如果你**能控制編譯**（重現 bug 時），`-Og` 是甜蜜點：

```bash
gcc -g -Og opt_demo.c -o optg
```

`-Og` 啟用「不傷害 debug 的最佳化」——比 `-O0` 快、比 `-O2` 好 debug。變數大多還在、inline 較少、行號較準。**重現「只在最佳化下出現的 bug」首選 `-Og`**：它夠接近 `-O2` 的行為（能觸發某些最佳化相關 bug），又保留可 debug 性。

但注意：`-Og` ≠ `-O2`。有些 bug 只在 `-O2` 的激進最佳化下出現，`-Og` 重現不了——那就得硬啃 `-O2`（下面策略）。

## 策略二：從暫存器找 `<optimized out>` 的變數

`<optimized out>` 不代表值不存在——它常常**就在某個暫存器裡**，只是 DWARF 沒完整記錄。組語級找它：

```
(gdb) break compute
(gdb) run
(gdb) print a
$1 = <optimized out>
(gdb) info registers              # 看所有暫存器
(gdb) disassemble                 # 看組語，找 a 被放哪
   ... mov %edi, ... ...          # a（第一參數）原本在 edi
(gdb) print $edi                  # 直接看暫存器！可能就是 a 的值
(gdb) info args                   # 有時 GDB 知道參數在哪暫存器
```

技巧：函式入口時參數在 ABI 規定的暫存器（`$rdi`/`$rsi`…，Ch 11）。即使 DWARF 說 `<optimized out>`，在函式**入口處**參數通常還在那些暫存器。`break *函式位址`（prologue 前）+ 看 `$rdi` 等，常能撈到「消失」的參數。

## 策略三：看穿 inline

inline 函式在 DWARF 5 裡其實**有記錄**（`DW_TAG_inlined_subroutine`），GDB 13+ 能顯示 inline frame：

```
(gdb) bt
#0  helper (x=7) at opt_demo.c:3       # GDB 標示這是 inlined
#1  compute (a=3, b=4) at opt_demo.c:6
(gdb) info frame                        # 會標 (inlined into ...)
```

GDB 把 inline 的函式顯示成「邏輯上的」frame（即使機器碼層沒有真的 call）。所以即使 `break helper` 失敗，backtrace 仍可能顯示 helper——因為 DWARF 記錄了「這段碼邏輯上屬於 helper」。

對 inline 函式下斷：用行號（`break opt_demo.c:3`）而非函式名，GDB 會在所有 inline 展開的位置下斷（multiple locations，Ch 4）。

## 策略四：組語級才是真相

當原始碼層全是 `<optimized out>` 和跳動的行號，**下到組語層**：原始碼會騙你（最佳化後不對應），但組語不會——CPU 執行的就是組語。

```
(gdb) layout asm                  # 或 layout split（Ch 18）
(gdb) set disassembly-flavor intel
(gdb) disassemble /s compute      # 組語 + 原始碼交錯，看最佳化怎麼重組
(gdb) stepi                       # 一條條走，看暫存器變化（不信行號）
(gdb) display/i $pc
```

`disassemble /s` 顯示組語與原始碼交錯——你能看到「這幾條指令對應原始碼哪幾行」「helper 怎麼被 inline 進來」「temp 怎麼只活在暫存器」。最佳化 binary 的 debug，最終都會下到這一層。Part 1 練習 A 的組語逆向技能在這裡全用上。

## 一個完整的 release crash 分析

線上 `-O2` binary 崩潰，core dump 在手（Ch 33）：

```
(gdb) bt
#0  process (item=<optimized out>) at proc.c:88     # 參數看不到
#1  ... (inlined) ...
(gdb) frame 0
(gdb) info registers
(gdb) disassemble /s             # 看崩在哪條指令
=> 0x... mov (%rax), %edx        # 崩在解 rax → rax 是壞指標
(gdb) print/x $rax
$1 = 0x0                          # NULL！
(gdb) # 往回找 rax 哪來：看前幾條指令、看哪個暫存器/記憶體餵給它
(gdb) print $rdi                 # 入口參數可能還在，撈出 item 的線索
```

策略：原始碼層不夠就下組語層，從崩潰指令往回追暫存器來源。這需要組語功底（Ch 11、39），但這是 release crash 唯一的路。

## 踩雷集錦

1. **看到 `<optimized out>` 就放棄**：值常在暫存器。`info registers` + `disassemble` 找它，別投降。
2. **相信最佳化 binary 的行號**：指令重排讓行號跳動、甚至誤導。組語才是真相。
3. **`break helper` 落空就以為沒執行**：被 inline 了。用行號下斷，或在呼叫端看。
4. **backtrace 少一層**：tail call 優化（`jmp` 代替 `call`）讓那層消失。`bt` 看到的呼叫鏈可能不完整。
5. **以為 `-O0` 能重現所有 bug**：有些 bug（race、UB、aliasing）只在最佳化下出現。`-O0` 反而藏住它們。要用 `-Og`/`-O2` 重現。
6. **`-O2 -g` 以為沒用**：`-g` 對 `-O2` 仍有價值——它給你殘缺但有用的 DWARF（inline 記錄、部分變數位置）。release 一定要帶 `-g`（再 strip 出來，Ch 0）。
7. **LTO 讓事情更糟**：`-flto`（link-time optimization）跨檔案最佳化，debug 更難，符號更亂。

## 進階：再往深一層

- **DWARF location list**：`<optimized out>` 其實是 DWARF 的 location 表達式說「這個值在這段 PC 範圍在暫存器 X、那段範圍在 stack、其他範圍沒了」。GDB 在當前 PC 查不到就說 optimized out。`readelf --debug-dump=loc`（Ch 38）看這些 location list。
- **`info address var`**：對最佳化變數，告訴你 GDB 認為它在哪（可能是「a complex DWARF expression」）。
- **entry value**：DWARF 5 的 `DW_OP_entry_value` 能記錄「參數進入函式時的值」，即使後來被覆蓋。GDB 用它顯示 `a=3` 即使 `a` 的暫存器已被重用。`set print entry-values`。
- **`-fno-omit-frame-pointer`**：保留 frame pointer 讓 backtrace 較穩（犧牲一個暫存器）。release 想要好 backtrace 可加。
- **`-fno-inline` 局部關 inline**：debug 特定問題時，對某檔案關 inline。
- **debug + 最佳化的本質矛盾**：這是編譯器領域的經典難題（呼應 ssa_optimizations / compiler_backend 課程）。最佳化越激進，debug info 越難精確——沒有完美解，只有取捨。
- **`-O2` 的 UB 放大**：最佳化器假設沒有 UB，會基於此做激進變換。所以 UB bug 常常「只在 `-O2` 出現」——debug 這類要配 `-fsanitize=undefined`（UBSan）。

## 動手練習

1. 把 `opt_demo.c` 編成 `-O0`/`-Og`/`-O2` 三版，對每個 `break compute` + `print temp`/`print a`，記錄哪些 `<optimized out>`。
2. 在 `-O2` 版，`print a` 是 optimized out，但 `info registers` + `disassemble` 找出 a 在哪個暫存器、`print $那個暫存器` 撈出值。
3. `-O2` 版 `break helper` 落空，改用 `break opt_demo.c:3`（helper 的行）下斷，看它在 inline 位置停。
4. `-O2` 版 `bt`，觀察 helper 是否以 inline frame 出現、a/b 是否 optimized out。
5. `disassemble /s compute` 對比 `-O0` 和 `-O2`，看最佳化怎麼重組指令、inline helper。
6. `set print entry-values both`，看 GDB 能否用 entry value 顯示被覆蓋的參數。

## 本章重點整理

- 線上崩潰的是 `-O2`，只會 debug `-O0` 等於只會 debug 玩具。
- 最佳化的破壞：變數進暫存器/被消除（`<optimized out>`）、函式 inline、指令重排、tail call 少一層。
- 策略：能控制編譯用 `-Og`（甜蜜點）；`<optimized out>` 從暫存器/`disassemble` 找；inline 用行號下斷、看 inline frame；最終下組語層（`disassemble /s`）信指令不信行號。
- release 一定帶 `-g`（DWARF 殘缺但有用，inline 記錄/entry value/location list）。
- 有些 bug 只在最佳化下出現（race/UB/aliasing），`-O0` 反而藏住。

## 自我檢核

- [ ] 為什麼「只會 debug `-O0`」是不夠的？
- [ ] `<optimized out>` 代表值真的不存在嗎？怎麼把它找回來？
- [ ] 函式被 inline 後，怎麼對它下斷、怎麼在 backtrace 看到它？
- [ ] 最佳化 binary 的原始碼行號可信嗎？真相在哪一層？
- [ ] `-Og` 和 `-O2` 各適合什麼情境？為什麼有些 bug 要 `-O2` 才重現？

## 延伸閱讀

### 官方文件

- **[GDB Manual: Optimized Code](https://sourceware.org/gdb/current/onlinedocs/gdb/Optimized-Code.html)**
  - **讀哪裡**：整節——inline frame、`<optimized out>`、entry values、`set print entry-values`。
  - **和本章的關聯**：本章核心的權威；GDB 對最佳化 code 的所有支援。

### 部落格 / 文章

- **[Debugging optimized code](https://developers.redhat.com/blog/2018/03/21/debugging-optimized-code-elfutils)** — Red Hat（DWARF location/entry value 系列）
  - **這篇說什麼**：DWARF 怎麼用 location list 描述「值在哪」，entry value 怎麼救回被覆蓋的參數。
  - **為什麼值得讀**：理解 `<optimized out>` 背後的 DWARF 機制，Ch 38 的預習。

- **[What every C programmer should know about UB](https://blog.llvm.org/2011/05/what-every-c-programmer-should-know.html)** — Chris Lattner
  - **和本章的關聯**：為什麼某些 bug 只在最佳化下爆——UB 與最佳化的互動。

### 規格

- **[DWARF5 §2.6 Location Descriptions](https://dwarfstd.org/doc/DWARF5.pdf)**
  - **讀哪裡**：location expression / location list。
  - **和本章的關聯**：`<optimized out>` 與「變數在哪」的底層；Ch 38 細講。

Part 6 收尾用練習 F：為一個自訂 C++ 容器寫 pretty-printer + xmethod，把 Ch 26/28/30 整合，讓它 debug 體驗等同 STL。

→ [練習 F：替自訂 C++ 容器寫 printer + xmethod](./practice-f-cpp-container-printer.md)
