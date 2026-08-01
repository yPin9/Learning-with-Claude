# Ch 28 — source ↔ disassembly 對照

> **目標**：讀碼的最後一道界線——當 source 不夠、甚至 source 在「說謊」時，往下沉到組合語言（assembly，本章以 x86-64 為主）看編譯器實際生成了什麼。你要學會判斷「什麼時候該下沉」（巨集/inline 展開後的真相、優化到底做了什麼、UB 被優化掉、`volatile`、只有 binary 沒 source），以及怎麼下沉（`objdump -d`、`objdump -S` 的 source-asm 交錯、gdb `disassemble`/`layout asm`）。全程真編譯、真 objdump，用 `-O0` vs `-O2` vs `-O3` 的對照，展示編譯器的 inline、向量化、死碼消除、UB 假設。讀完你不再把「讀 source」和「讀組語」當兩件事——它們是同一份程式的兩個視角，而真相在下面那一層。這章直通你的 gdb / perf_bench / binary_exploitation 課。

## 為什麼要下沉到組語？source 什麼時候「不夠」

前面 27 章都在讀 source，而且大多數時候 source 就是你要的答案。但有幾種情況，**source 給你的資訊是不完整的、甚至是誤導的**，這時候唯一的真相在編譯出來的機器碼裡：

1. **巨集/inline 展開後才有真相**：一個看起來簡單的呼叫，展開後可能是幾十行；一個 `container_of` 展開成一次減法。source 上的抽象在指令層是另一個樣子。
2. **編譯器優化做了什麼你看不到**：`-O2` 會 inline 函式、向量化迴圈、消除死碼、把變數放進暫存器不落記憶體。source 說「這裡呼叫 foo」，實際上 foo 被整個嵌進來、甚至被算成常數了。讀效能問題（perf_bench）非看指令不可。
3. **source 在「說謊」——UB 被優化**：C/C++ 有大量未定義行為（undefined behavior, UB）。source 寫的檢查，編譯器可能因為「這是 UB，我假設它不會發生」而**整段刪掉**。你讀 source 以為有防護，實際上沒有。
4. **`volatile` 的語義只在指令層看得清**：`volatile` 影響「每次讀寫是否真的落到記憶體」，這在 source 上是一個關鍵字，在指令層是「有沒有那條 load/store」。
5. **只有 binary 沒 source**：閉源程式、被 strip 的 library、malware、CTF 題目——根本沒有 source，組語就是唯一的 source。這是 binary reverse engineering 的本行，也是這整門「讀碼即逆向」課的原點。

工具三件套，後面逐一真跑：

| 工具 | 用途 | 需要 -g 嗎 |
|---|---|---|
| `objdump -d` | 純反組譯，看指令 | 否（但有符號更好讀） |
| `objdump -S` | source 與 asm 交錯對照 | **是**（要 debug info） |
| gdb `disassemble` / `layout asm` | 動態、可搭配斷點與暫存器 | 建議有 -g |

## 第一課：-O0 vs -O2，同一段 code 兩個世界

先建立最重要的直覺：**優化等級決定你看到的指令跟 source 差多遠。** 拿一個簡單的求和函式：

```c
int sum(const int *a, size_t n) {
    int s = 0;
    for (size_t i = 0; i < n; i++)
        s += a[i];
    return s;
}
```

**`-O0`（不優化）** 的反組譯（真跑輸出），幾乎是 source 的逐行直譯：

```
$ gcc -O0 -g -c sum.c && objdump -d sum0.o
0000000000000000 <sum>:
   push   %rbp
   mov    %rsp,%rbp
   mov    %rdi,-0x18(%rbp)        ← 參數 a 存回堆疊
   mov    %rsi,-0x20(%rbp)        ← 參數 n 存回堆疊
   movl   $0x0,-0xc(%rbp)         ← s = 0（存在堆疊）
   movq   $0x0,-0x8(%rbp)         ← i = 0（存在堆疊）
   jmp    ...                      ← 跳到迴圈條件判斷
   mov    -0x8(%rbp),%rax          ← 每次迴圈都從堆疊重讀 i
   lea    0x0(,%rax,4),%rdx        ← i*4
   mov    -0x18(%rbp),%rax         ← 重讀 a
   add    %rdx,%rax                ← a + i*4
   mov    (%rax),%eax              ← 讀 a[i]
   add    %eax,-0xc(%rbp)          ← s += a[i]（存回堆疊）
   addq   $0x1,-0x8(%rbp)          ← i++
   ...cmp/jb 迴圈...
```

`-O0` 的特徵：**每個變數都在堆疊上、每次用都重新 load/store**（`s`、`i`、`a` 反覆進出 `-0x8(%rbp)` 這種堆疊位置）。這是為什麼 `-O0` 好 debug（每個變數隨時可看）但慢。它幾乎是 source 的一對一翻譯，讀起來最貼近你腦中的 C。

**`-O2`** 的反組譯（真跑輸出）——同一個函式，完全變了樣：

```
$ gcc -O2 -g -c sum.c && objdump -d sum2.o
0000000000000000 <sum>:
   test   %rsi,%rsi              ← n == 0 ?
   je     20 <sum+0x20>          ← 是就跳去 return 0
   lea    (%rdi,%rsi,4),%rdx     ← 算出陣列結尾指標 a+n
   xor    %eax,%eax              ← s = 0（用暫存器 eax，不碰記憶體）
   nop
   add    (%rdi),%eax            ← s += *a
   add    $0x4,%rdi              ← a++（指標遞增，不再算 i*4）
   cmp    %rdx,%rdi              ← 到結尾了嗎
   jne    10 <sum+0x10>
   ret
   ...
   20:  xor %eax,%eax            ← n==0 的 return 0
        ret
```

差別巨大：`-O2` 把 `s` 和 `i` **全放進暫存器**（`eax`、`rdi`），迴圈裡沒有一次多餘的記憶體存取；把「`a[i]` = `a + i*4` 再解引用」改寫成「指標一路 `+4` 往前走」（strength reduction，強度削減）；還多了「n==0 早退」的檢查。**source 沒變，指令從十幾條堆疊搬運變成五條暫存器運算。** 讀效能問題時，你看 source 看不出這些——只有 objdump 告訴你迴圈到底幾條指令、有沒有碰記憶體。

> 讀碼決策：**要理解邏輯，讀 `-O0`（貼近 source）；要理解效能/真相，讀 `-O2`/`-O3`（貼近實際執行）。** 兩個都是同一份 code 的真相，看你要問什麼。

## objdump -S：source 與 asm 交錯

純 `objdump -d` 給你指令，但要對回是哪一行 C 生的，得靠 `-S`（需要編譯時加 `-g`）。它把 source 行**穿插**在對應的指令之間（真跑輸出，`-O2 -g`）：

```
$ objdump -S sum2.o
0000000000000000 <sum>:
#include <stddef.h>
int sum(const int *a, size_t n) {
   endbr64
    for (size_t i = 0; i < n; i++)
   test   %rsi,%rsi
   je     20 <sum+0x20>
   lea    (%rdi,%rsi,4),%rdx
    int s = 0;
   xor    %eax,%eax
   nop
        s += a[i];
   add    (%rdi),%eax
    for (size_t i = 0; i < n; i++)
   add    $0x4,%rdi
   cmp    %rdx,%rdi
   jne    10 <sum+0x10>
    return s;
}
   ret
```

注意一件事：source 行的順序在 `-O2` 下**不是線性的**——`int s = 0;` 那行出現在 `for` 迴圈那行**之後**，因為優化把初始化重排了。`s += a[i]` 只對應一條 `add` 指令。這就是「優化後 source 行和指令不再一對一」的直接證據。**讀 `objdump -S` 的心態：source 行是「這條指令大約來自哪」的提示，不是嚴格對應。** 在 `-O0` 下對應很整齊，在 `-O2` 下要接受「指令跳來跳去、一行 source 散在好幾處」。

gdb 有等價的 `disassemble /s`（source 交錯）和 `disassemble /r`（顯示原始 byte）。`disassemble /r sum`（真跑輸出）：

```
(gdb) disassemble /r sum
   0x...<+0>:   f3 0f 1e fa    endbr64
   0x...<+4>:   48 85 f6       test   %rsi,%rsi
   0x...<+7>:   74 17          je     0x20 <sum+32>
   0x...<+9>:   48 8d 14 b7    lea    (%rdi,%rsi,4),%rdx
   0x...<+13>:  31 c0          xor    %eax,%eax
   ...
```

`/r` 左邊那欄（`f3 0f 1e fa` 等）是每條指令的實際機器碼 byte——你要做 patching、shellcode、對照 hex dump 時會用到（binary_exploitation 的日常）。gdb 的優勢是**動態**：可以在某行下斷點、`disassemble` 看當前函式、`x/i $pc` 看下一條要執行的指令、對照暫存器實際的值。`layout asm`（或 `layout split` source+asm 並排）開 TUI，適合一步步跟。

## source 在說謊：UB 被優化掉

這是「必須下沉到組語」最有說服力的場景。C 的有號整數溢位（signed overflow）是 UB——編譯器**被授權假設它永遠不發生**。看這段「檢查溢位」的 code：

```c
#include <limits.h>
int check(int x) {
    if (x + 1 < x) return 1;   // x 加 1 後會不會比 x 小？（想偵測溢位）
    return 0;
}
int main(void) { printf("%d\n", check(INT_MAX)); }
```

直覺上，`check(INT_MAX)` 應該回傳 1：`INT_MAX + 1` 溢位變成負數，比 `INT_MAX` 小，條件成立。**`-O0` 確實如此**（真跑）：

```
$ gcc -O0 ub.c -o ub0 && ./ub0
0    ← 咦？連 -O0 都是 0
```

（注：`INT_MAX+1` 的實際 wrap 結果依平台，這裡 `-O0` 算出的比較恰好不成立——但這不是重點，重點是下面。）**`-O2` 的行為**（真跑）：

```
$ gcc -O2 ub.c -o ub2 && ./ub2
0
```

看 `-O2` 把 `check` 編成什麼（真跑輸出）：

```
$ gcc -O2 -c ub.c && objdump -d ub.o
0000000000000000 <check>:
   endbr64
   xor    %eax,%eax        ← eax = 0
   ret                      ← 直接 return 0，永遠
```

**整個比較消失了。** 編譯器的推理是：「`x + 1 < x` 在無溢位的前提下永遠為假（因為 `x+1` 一定大於 `x`）；而有號溢位是 UB，我可以假設它不發生；所以這個 `if` 永遠不成立，整段是死碼，刪掉。」於是 `check` 被優化成無條件 `return 0`——**你在 source 裡寫的溢位檢查，被編譯器當成不可能發生而刪除了**。

這是讀 C/C++ 最危險的陷阱之一：**source 上明明有一段檢查/防護，編譯器因為 UB 假設把它刪了，而你讀 source 完全看不出來。** 只有 objdump 對照才會發現「這個檢查在 `-O2` 下根本不存在」。CVE 級的漏洞真的這樣產生過（例如某些 NULL 檢查因為前面已解引用該指標、編譯器認為「指標非 NULL 是既定事實」而刪掉後面的檢查）。

> 讀碼鐵律：**當 source 的行為和你的預期對不上、尤其牽涉 UB（溢位、越界、NULL 解引用、type punning、data race）時，別相信 source，去看 `-O2` 的 objdump。** 編譯器對 UB 的處置只在指令層看得見。這也是為什麼 UBSan（`-fsanitize=undefined`）這種工具存在——把「編譯器悄悄假設不發生的 UB」在執行期抓出來。

## volatile：告訴編譯器「這裡不准優化掉讀寫」

跟 UB 相關的另一面是 `volatile`。它的語義只有在指令層才看得清楚：**每次讀 `volatile` 變數，都必須真的產生一條 load 指令；每次寫，都必須真的產生一條 store**——不准快取進暫存器、不准合併、不准刪除。

沒有 `volatile` 時，`while (flag) {}`（`flag` 是普通變數）在 `-O2` 下，編譯器可能把 `flag` 讀進暫存器一次、之後不再重讀——因為「這個迴圈裡沒人改 flag」，於是變成 `while (reg) {}` 死迴圈（即使別的執行緒或中斷改了記憶體裡的 flag）。加了 `volatile`，每圈都重新 load 記憶體，就能看到外部的改動。

`volatile` 的正當用途是 **MMIO（記憶體映射 I/O，讀寫某位址其實是在跟硬體暫存器溝通，不能省）** 和 **signal handler 裡的旗標**。它**不是**執行緒同步原語（不保證原子、不保證 memory ordering——這點 Ch 25 已強調）。讀碼時看到 `volatile`：想「這個變數的每次讀寫都被強制落到記憶體，作者在防編譯器優化掉、通常因為背後有硬體或非同步的改動」。要驗證它真的生成了 load/store，`objdump -S` 對照——`volatile` 迴圈裡你會看到每圈一條記憶體 load，非 volatile 版本那條 load 會被提到迴圈外或消失。

## 只有 binary 沒 source

最後一種情況：連 source 都沒有。閉源二進位、strip 過的 library、CTF pwn/re 題、韌體 dump。這時 `objdump -d` 就是你的 source，而且是**沒有變數名、沒有型別、沒有註解**的 source。這正是 binary reverse engineering 的核心，也是這門課「讀碼即逆向」命名的來源——**前面所有讀 source 的方法（找 entry、追 data flow、猜 invariant、假設驅動），在只有組語時全部適用，只是抽象層更低。**

實務上這時你會升級工具：`objdump` 之外用 **Ghidra / IDA / Binary Ninja** 這類反編譯器（decompiler），它們把組語**反編譯回類 C 的偽代碼**，恢復控制流、猜型別、命名變數。反編譯出來的偽代碼不完美（型別常猜錯、變數名是 `local_28` 這種），但比讀純組語快十倍。讀 strip 過的 binary 的 SOP 跟讀 source 一樣：先找 entry（`_start` → `main`）、找字串（`strings` 常洩漏功能）、找關鍵 API 呼叫（`objdump -d | grep call`，看它呼叫了哪些 libc 函式反推它在幹嘛）、順 data flow。這一大塊是你的 binary_exploitation 和 gdb 課的主場，本章只點出「它跟讀 source 是同一套方法、不同抽象層」這個連結。

## 對比與取捨

什麼時候停在 source、什麼時候下沉，是個成本判斷：

| 場景 | 停在 source 夠嗎 | 該用什麼 |
|---|---|---|
| 理解程式邏輯、架構 | 夠，別下沉（組語看不出架構） | source + 前面 27 章的方法 |
| 巨集/inline 展開後到底是什麼 | 不夠 | `gcc -E`（看巨集展開）、`objdump -S` |
| 效能：這迴圈幾條指令、有沒有向量化 | 不夠 | `objdump -d` 看 `-O2`/`-O3`、perf |
| source 有檢查卻沒作用（疑 UB） | 危險，會被騙 | `objdump -d` 看 `-O2`、UBSan |
| `volatile`/MMIO 的讀寫真的發生了嗎 | 看不清 | `objdump -S` 對照 load/store |
| 沒有 source | 沒得選 | objdump + Ghidra/IDA + gdb |

核心取捨：**下沉到組語資訊量爆炸（一行 C 變幾十條指令），只在「source 這一層答不了的問題」才值得付這個成本。** 別為了炫技對整個程式反組譯——那是淹沒自己。反過來，遇到上面那幾種「source 不夠」的訊號，就要毫不猶豫地下沉，否則你會對著一段被優化刪掉的 code 苦思它為什麼「沒作用」。

## 踩雷集錦

1. **錯誤直覺：「source 這樣寫，編譯出來就這樣跑」→ 正確：`-O2` 會 inline、向量化、消除死碼、把變數藏進暫存器，指令跟 source 可以差很遠。** 尤其效能和 UB 相關的問題，只讀 source 會得到錯的結論。要真相就 objdump。

2. **錯誤直覺：「我在 source 寫了溢位/NULL 檢查，所以有防護」→ 正確：如果那檢查依賴 UB，編譯器可能假設 UB 不發生而把整段刪掉。** 本章 objdump 實測：`if (x+1 < x)` 在 `-O2` 被刪成無條件 `return 0`。防護有沒有真的存在，看 `-O2` 的指令。

3. **錯誤直覺：「`objdump -S` 的 source 行和下面的指令是精確對應的」→ 正確：只有 `-O0` 大致對應；`-O2` 下優化重排，一行 source 散在多處、多行 source 對一條指令，順序也亂。** 把它當「大約來自哪」的提示，不是嚴格映射。

4. **錯誤直覺：「`volatile` 能拿來做執行緒同步」→ 正確：`volatile` 只保證每次讀寫落到記憶體，不保證原子性、不保證 memory ordering。** 執行緒同步要用 atomic（Ch 25）。`volatile` 是給 MMIO 和 signal handler 的。

5. **錯誤直覺：「讀組語就是把每條指令翻成中文」→ 正確：讀組語跟讀 source 一樣要抓 pattern、追 data flow、猜意圖，不是逐指令直譯。** 認得函式序言/尾聲、呼叫慣例（哪個暫存器傳參）、迴圈骨架，比逐條讀快得多。

6. **錯誤直覺：「什麼問題都下沉到組語最徹底」→ 正確：組語資訊量爆炸，讀架構/邏輯下沉只會淹死你。** 只在 source 答不了的特定問題（優化、UB、無 source）才下沉。用對抽象層是效率關鍵。

## 進階：再往深一層

- **`gcc -E` 與 `-fdump-*`：下沉的中間站**：objdump 是最底層，但有時你只想看「巨集展開後」或「optimizer 做完某個 pass 後」。`gcc -E foo.c` 只跑預處理，看巨集/`#include` 展開的純 C（讀 Ch 22 那種巨集地獄時無敵）。`gcc -O2 -fdump-tree-optimized foo.c` 吐出優化後的 GIMPLE（gcc 的中介表示），比組語好讀、又已經反映了大部分優化。這幾個是「source 和組語之間」的觀察點，各自對應不同問題。

- **編譯器解釋器 Compiler Explorer（godbolt.org）**：把 source 貼進去，即時看任意 gcc/clang/版本/優化等級生成的組語，source 和 asm 用顏色對應高亮。這是本章所有對照實驗的線上版，而且能一鍵切 `-O0`/`-O2`/`-O3`/`-march`、切編譯器版本看差異。讀「這個寫法編出來的 code 好不好」時，godbolt 比本地 objdump 快得多。perf_bench 課會重度使用。

- **向量化（SIMD）的識別**：`-O3 -march=native` 對我們的 `sum` 迴圈真的做了 AVX2 向量化（真跑輸出，過濾）：`vpaddd (%rax),%ymm1,%ymm1` ——一條 `vpaddd` 同時加 8 個 int（`%ymm` 是 256-bit 暫存器），配 `vextracti128`/`vpsrldq` 做最後的水平相加（把向量裡 8 個部分和合成一個）。看到 `%xmm`/`%ymm`/`%zmm` 暫存器和 `vp*`/`v*ps` 指令，就是編譯器把純量迴圈自動向量化了。讀效能敏感 code 時，「這個熱迴圈有沒有被向量化」是關鍵問題，只有 objdump 答得出來。

- **函式序言/尾聲與 ABI**：讀組語要快，得認得樣板。x86-64 System V ABI：整數參數依序放 `rdi, rsi, rdx, rcx, r8, r9`、回傳值在 `rax`（本章 `sum` 的 `a` 在 `rdi`、`n` 在 `rsi`、回傳在 `eax`，正是這個）。`push %rbp; mov %rsp,%rbp` 是傳統序言（`-O0` 常見，`-O2` 常省略 frame pointer）。`endbr64` 是 CET（控制流完整性）的落地指令。認得這些，你就能略過樣板、專注在真正做事的指令——這是讀組語從「逐條啃」進化到「掃 pattern」的關鍵，你的 binary_exploitation 課會把這套練到反射。

## 動手練習

1. **-O0 vs -O2 對照**：拿本章的 `sum.c`，分別 `gcc -O0 -g -c` 和 `gcc -O2 -g -c`，`objdump -d` 兩個 `.o`。數迴圈本體各幾條指令、哪個碰記憶體、哪個用暫存器。用一句話說出優化做了什麼。

2. **objdump -S 交錯**：對 `-O2 -g` 的 `sum` 跑 `objdump -S`，觀察 source 行順序在優化下怎麼亂掉（`int s=0;` 跑到 `for` 後面）。體會「行對應是提示不是嚴格映射」。

3. **重現 UB 被優化**：編譯本章的 `ub.c`，`-O0` 和 `-O2` 各跑一次、各 objdump `check`。確認 `-O2` 把 `check` 編成無條件 `return 0`、比較消失。然後加 `-fsanitize=undefined` 重編跑跑看，UBSan 會不會抓到。

4. **看向量化**：`gcc -O3 -march=native -c sum.c && objdump -d`，找 `%ymm`/`vpaddd` 指令。跟 `-O2`（本章顯示沒向量化，是純量指標迴圈）對照。體會 `-O3 -march=native` 才敢積極向量化。

5. **volatile 對照**：寫 `int f(volatile int *p){int s=0; for(int i=0;i<4;i++) s+=*p; return s;}`，volatile 版和拿掉 volatile 版各 `-O2` 編譯 objdump。看 volatile 版每圈一條 load、非 volatile 版把 load 提到迴圈外（只讀一次）。

6. **gdb 動態下沉**：把 `sum` 編成可執行檔（含 main），gdb 進去 `break sum`、`run`、`disassemble`、`layout asm`、`info registers rdi rsi`（看參數怎麼傳）、`x/4xw $rdi`（看陣列內容）。體會靜態 objdump 給不了的「當下暫存器與記憶體實際值」。

## 本章重點整理

- source 大多數時候夠用，但五種情況必須下沉到組語：巨集/inline 展開後真相、優化做了什麼、UB 被優化刪掉、volatile 語義、只有 binary 沒 source。
- `-O0` 幾乎是 source 逐行直譯（變數在堆疊、好 debug、慢）；`-O2`/`-O3` 把變數放暫存器、消死碼、強度削減、向量化——同一 source 兩個世界。要邏輯讀 -O0，要真相/效能讀 -O2。
- 工具：`objdump -d`（純指令）、`objdump -S`（source-asm 交錯，需 -g，但 -O2 下對應是提示非嚴格）、gdb `disassemble /s`(交錯)/`/r`(顯 byte)/`layout asm`（動態）。
- UB 陷阱（最危險）：source 寫的檢查若依賴 UB（有號溢位、NULL 解引用…），`-O2` 可能整段刪掉。本章實測 `if(x+1<x)` 被編成無條件 `return 0`。行為對不上又涉 UB 就看 -O2 objdump。
- `volatile` = 每次讀寫強制落記憶體、不准優化掉；給 MMIO/signal 用，不是執行緒同步。
- 沒 source 時，objdump/Ghidra/IDA 就是 source，前 27 章的讀碼方法全部適用，只是抽象層更低——這是「讀碼即逆向」的原點。

## 自我檢核

- [ ] 我能不能說出五種「該從 source 下沉到組語」的訊號，並各舉一例？
- [ ] 給我同一函式的 `-O0` 和 `-O2` objdump，我能不能指出優化做了哪些事（暫存器化、強度削減、死碼消除、早退）？
- [ ] 我能不能解釋為什麼「source 裡的溢位檢查」會在 `-O2` 被刪掉，並知道怎麼用 objdump 驗證？
- [ ] 我知道 `objdump -S` 的 source 行對應在 `-O2` 下不是嚴格的，該怎麼正確解讀嗎？
- [ ] 我能不能說清楚 `volatile` 保證什麼、不保證什麼，以及它跟執行緒同步的關係？
- [ ] 面對一個沒有 source 的 binary，我知道前面所有讀 source 的方法都適用、只是換更低的抽象層嗎？

## 延伸閱讀

每條都說清楚讀哪裡、學什麼、前提。

- **[Compiler Explorer（godbolt.org）](https://godbolt.org/)**
  - **讀哪裡**：直接貼 code，切 gcc/clang、切 `-O0`/`-O2`/`-O3`/`-march=native`，看 source-asm 的顏色對應。先重現本章的 sum 向量化和 ub 死碼消除。
  - **學到什麼**：本章所有對照實驗的線上即時版，切版本/等級/編譯器一鍵完成。讀「這寫法編出來如何」最快的工具，perf_bench 課會重度使用。
  - **前提**：會讀基本 x86-64 組語（本章打底）。

- **[Matt Godbolt, "What Has My Compiler Done for Me Lately?"（CppCon 演講）](https://www.youtube.com/watch?v=bSkpMdDe4g4)**
  - **讀哪裡**：整場，Compiler Explorer 作者親自示範編譯器怎麼 inline、向量化、消除、把除法變乘法。
  - **學到什麼**：把「編譯器優化做了什麼」從抽象概念變成一個個看得見的組語對照，建立「不要低估也不要迷信編譯器」的判斷。
  - **前提**：本章的 -O0/-O2 對照；基本組語。

- **[LLVM Blog / John Regehr, "A Guide to Undefined Behavior in C and C++"](https://blog.regehr.org/archives/213)**
  - **讀哪裡**：三篇系列全讀，特別是講「編譯器怎麼利用 UB 做優化」與「哪些常見寫法其實是 UB」的部分。
  - **學到什麼**：本章「source 說謊」那節的深度與廣度版——完整理解為什麼你寫的檢查會被刪、哪些日常寫法暗藏 UB。讀 C/C++ 安全問題必修。
  - **前提**：C/C++ 中階；本章的 UB objdump 範例當引子。

- **[x86-64 System V ABI 文件 / Agner Fog 的優化手冊](https://www.agner.org/optimize/)**
  - **讀哪裡**：ABI 查「參數傳遞暫存器、回傳值、caller/callee-saved」那張表；Agner Fog 的 "Optimizing subroutines in assembly language" 當進階字典。
  - **學到什麼**：讀組語從「逐條啃」進化到「認 pattern」所需的呼叫慣例與指令知識，直通 binary_exploitation / gdb 課。
  - **前提**：本章的組語打底；有反組譯實作經驗更佳。

到這裡，Part 4「讀懂特定結構」的五種硬核場景（build system、巨集、indirection、狀態機、並發、C++、kernel 慣例、source↔asm）你都攻過一遍了。接下來的練習 C，會把這整個 Part 綜合起來——給你一段真實的硬核 code（redis `dict.c` 的漸進式 rehash），要你完整解釋它的機制、畫出資料結構圖、說清楚它維持的不變式、並找出一個 edge case。這是驗收「能不能真的讀懂一段難 code」的實戰。

→ [練習 C：讀懂一段硬核 code](./practice-c-read-hardcore-code.md)
