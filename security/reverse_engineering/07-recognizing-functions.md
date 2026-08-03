# Ch 7 — 認出函式：prologue / 參數 / inline 痕跡

> **目標**：把「函式」這個單位在 binary 裡認出來——用 prologue/epilogue 劃出函式邊界、從暫存器使用反推參數個數與型別、從 `rax` 認回傳值、從自我呼叫認遞迴、從 `jmp` 取代 `call` 認尾呼叫（tail call）、認出沒有 frame 的葉函式（leaf）。以及最難也最重要的：在 `-O2` 裡認出「**這裡本來是一個函式，但被 inline 蒸發了**」——呼應 [Ch 0](./00-environment-and-ground-truth-loop.md) 裡 `secret` 消失的那一幕。全程 `-O0`/`-O2` 真跑對照。

> **環境**：WSL2 / Linux x86-64，gcc 11.4 + objdump。本章所有 asm 都是真跑輸出。

## 為什麼需要這個？

函式是程式的**模組化單位**，也是逆向的**工作單位**——你不是一次逆完整個 binary，是一個函式一個函式攻破。所以第一件事永遠是：**這團 code 從哪裡到哪裡是一個函式？它收幾個參數、回傳什麼、呼叫了誰？** 有了這張「函式地圖」（call graph），你才能挑軟柿子先逆、順著呼叫關係推。

在 stripped binary 裡，函式名沒了，但函式的**結構**還在：prologue、epilogue、`call`/`ret` 的配對、呼叫慣例的暫存器使用。逆向認函式，就是認這些結構性指紋。而 `-O2` 的殺手是 **inline**——編譯器把小函式的 body 直接複製進呼叫者，函式邊界消失。認出「這段重複的 code 其實是被 inline 的同一個函式」，是分辨初學者和老手的關鍵。

> 這是 [`reading_code`](../../soft_skills/reading_code/README.md) 「先建 call graph、挑入口函式攻堅」在 binary 的鏡像。source 裡函式邊界白紙黑字，binary 裡要你從 prologue/`call`/`ret` 重建。

## 先建立直覺：函式在 asm 裡的解剖

一個 `-O0` 的函式有固定的三段式解剖，認得這個模板，函式邊界就一目了然：

```
   ┌─────────────────────────────────────────┐
   │ endbr64                    ← CET landing pad，函式入口路標
   │ push   %rbp          ┐
   │ mov    %rsp,%rbp      │ prologue：建立 stack frame
   │ sub    $0xNN,%rsp     ┘（要用 stack 才有這條）
   │ ...                                       │
   │   [ 收參數：mov %edi,-0xN(%rbp) ... ]     │ ← 呼叫慣例：參數從 rdi/rsi/...
   │   [ body：算術、控制流、call 別人 ]       │
   │   [ 設回傳值：mov ..., %eax ]             │ ← 回傳值放 rax/eax
   │ ...                                       │
   │ leave  (= mov %rbp,%rsp; pop %rbp) ┐      │
   │ pop    %rbp（或 leave）             │ epilogue：拆 frame
   │ ret                                 ┘ ← 回到呼叫者
   └─────────────────────────────────────────┘
```

**兩個邊界標記**：入口是 `endbr64` + `push %rbp`，出口是 `ret`。`call X` 把返回位址壓 stack 再跳去 X；`ret` 從 stack 彈返回位址跳回去。`call`/`ret` 成對，是函式呼叫的骨架。認函式邊界的最快法：**在 objdump 裡找 `endbr64`（每個函式入口一個）和 `ret`（出口）。**

## Prologue / Epilogue：劃出函式邊界

`-O0` 的 prologue 是鐵板一塊的 `push %rbp; mov %rsp,%rbp`——這叫「建立 frame pointer」。`rbp` 之後固定指向 frame 底，所有局部變數用 `rbp-relative` 定址（[Ch 6](./06-recognizing-data.md)）。epilogue 用 `leave`（等於 `mov %rbp,%rsp; pop %rbp`）或 `pop %rbp` 把 frame 拆掉，`ret` 返回。

回顧 [Ch 4](./04-x86-64-for-reversers.md) 的 `add6`（`-O0`）：`114d: push %rbp; 114e: mov %rsp,%rbp` 是 prologue，`118d: pop %rbp; 118e: ret` 是 epilogue。這對「push rbp 開頭 / pop rbp+ret 結尾」把函式邊界框得清清楚楚。

**但 `-O2` 會打破這個模板**，這正是難點的開始：

## 葉函式：沒有 frame 的函式

**葉函式（leaf function）** = 自己不再呼叫任何別的函式。它有個特權：**不需要建 frame**——因為沒人會覆蓋它的暫存器（它不 call 別人），參數和暫存值都能待在暫存器裡，還能用紅區（red zone，[Ch 4](./04-x86-64-for-reversers.md)）而不必動 `rsp`。

### 真跑：葉函式在 -O2 沒有 prologue

```c
int square(int x){ return x * x; }
```

`gcc -O2` 真跑：

```asm
0000000000001190 <square>:
    1190:	endbr64
    1194:	mov    %edi,%eax          ; eax = x（第一參數）
    1196:	imul   %edi,%eax          ; eax = x * x
    1199:	ret                       ; 回傳（值已在 eax）
```

**沒有 `push %rbp`、沒有 `sub $N,%rsp`、沒有 stack slot**。整個函式三條指令：讀參數、算、回傳。這就是葉函式在 `-O2` 的樣貌——邊界只剩 `endbr64`（入口）和 `ret`（出口）。**逆向時看到「`endbr64` 開頭、沒 prologue、幾條算完就 `ret`」，那是個葉函式**，通常是小工具函式（getter、簡單計算）。

## 從暫存器使用反推參數與回傳值

stripped binary 沒有函式簽章，但**呼叫慣例讓你把簽章考古出來**（[Ch 4](./04-x86-64-for-reversers.md) 的呼叫慣例是這裡的地基）：

- **參數個數**：看函式**在賦值前就讀取**了哪些參數暫存器。用了 `rdi` = 至少 1 個參數；用到 `rdi,rsi,rdx` = 至少 3 個。`square` 只讀 `edi` → 1 個參數。`add6` 讀到 `r9d` → 6 個參數。
- **參數型別（寬度）**：用 `edi`（32-bit）→ `int`；用 `rdi`（64-bit）→ 指標或 `long`；用 `dil`（8-bit）→ `char`/`bool`。子暫存器寬度給你參數大小（[Ch 4](./04-x86-64-for-reversers.md)）。
- **回傳值**：函式結尾前**最後寫進 `rax`/`eax` 的值**就是回傳值。`square` 結尾 `eax` 是 `x*x`，回傳 `int`。若函式從不碰 `rax`，可能是 `void`。
- **回傳型別（寬度）**：結尾用 `eax`（32-bit）→ 回傳 `int`；用 `rax`（64-bit）→ 回傳指標/`long`；`al` → `char`/`bool`。

**這套推理是逆向函式簽章的 SOP**：數用了幾個參數暫存器、看它們的寬度、看回傳走 rax 的寬度，你就能寫出 `int square(int)` 這樣的簽章，完全不需要符號表。

> 注意陷阱：這是**下界**推理。函式可能宣告 3 個參數但只用了 2 個（第 3 個沒被讀），你會少數一個。反過來，若函式內部呼叫別人前設了 `rdx`，那是「它給別人傳參」不是「它自己收了 3 個參數」——別把它 call 別人時設的參數暫存器誤當成自己的入參。要看的是「**在第一次寫入前就被讀取**」的暫存器。

## 遞迴：函式呼叫自己

遞迴在 asm 裡的指紋直白到不行：**函式裡有一條 `call` 指向自己的位址**。

### 真跑：階乘

```c
int fact(int n){
    if (n <= 1) return 1;
    return n * fact(n - 1);
}
```

`gcc -O0` 真跑：

```asm
000000000000115c <fact>:
    115c:	endbr64
    1160:	push   %rbp
    1161:	mov    %rsp,%rbp
    1164:	sub    $0x10,%rsp
    1168:	mov    %edi,-0x4(%rbp)      ; n
    116b:	cmpl   $0x1,-0x4(%rbp)      ; ┐ n <= 1 ?
    116f:	jg     1178 <fact+0x1c>     ; ┘ n>1 → 遞迴那半
    1171:	mov    $0x1,%eax            ; base case: return 1
    1176:	jmp    1189 <fact+0x2d>
    1178:	mov    -0x4(%rbp),%eax
    117b:	sub    $0x1,%eax            ; n - 1
    117e:	mov    %eax,%edi            ; 當參數
    1180:	call   115c <fact>          ; ★ call 自己（115c = fact 入口）→ 遞迴！
    1185:	imul   -0x4(%rbp),%eax       ; n * fact(n-1)
    1189:	leave
    118a:	ret
```

`1180: call 115c`——`115c` 就是 `fact` 自己的入口位址。**函式裡 `call` 到自身入口 = 遞迴**，一眼可辨。旁邊的 `imul -0x4(%rbp), %eax`（`n * 回傳值`）告訴你這是「先遞迴再乘」的結構。

**但 `-O2` 可能把遞迴優化掉**。同一份 `fact` 在 `gcc -O2`（真跑）：

```asm
00000000000011a0 <fact>:
    11a0:	endbr64
    11a4:	mov    $0x1,%eax
    11a9:	cmp    $0x1,%edi
    11ac:	jle    11bd <fact+0x1d>
    11ae:	xchg   %ax,%ax              ; (對齊填充)
    11b0:	mov    %edi,%edx            ; ┐
    11b2:	sub    $0x1,%edi            ; │ 迴圈！eax *= edi; edi--
    11b5:	imul   %edx,%eax            ; │ 向後跳到 11b0
    11b8:	cmp    $0x1,%edi            ; │
    11bb:	jne    11b0 <fact+0x10>     ; ┘ ★ 沒有 call 了！遞迴變成迴圈
    11bd:	ret
```

**`call` 消失了**——編譯器把這個尾遞迴/可迭代的遞迴轉成了迴圈（向後跳 `11bb: jne 11b0`，[Ch 5](./05-recognizing-control-flow.md) 的迴圈 pattern）。逆向時看到「一個把值累乘、計數器遞減的迴圈」，要想到「原始碼可能是遞迴，被編譯器攤平了」。**優化會抹掉遞迴的結構痕跡**——這是「binary 不忠實反映 source 結構」的又一例。

## 尾呼叫：jmp 取代 call

**尾呼叫（tail call）**：函式的最後一個動作是「呼叫另一個函式並直接回傳它的結果」（`return foo(x);`）。編譯器優化成 **`jmp` 而非 `call`**——因為不需要「call 進去、回來、再 ret」兩次跳，直接跳過去讓對方的 `ret` 替你返回，省一層 frame。

### 真跑：尾呼叫變 jmp

```c
size_t mylen(const char *s){ return strlen(s); }   // 尾呼叫 strlen
```

`gcc -O2` 真跑：

```asm
0000000000001150 <mylen>:
    1150:	endbr64
    1154:	jmp    1060 <strlen@plt>    ; ★ jmp 不是 call！尾呼叫優化
```

`mylen` 沒有 `call strlen; ret`，而是直接 `jmp strlen@plt`——把參數（`s` 已在 `rdi`）原封不動交給 `strlen`，讓 `strlen` 的 `ret` 直接返回到 `mylen` 的呼叫者。**看到函式結尾是 `jmp 別的函式`（而非 `call` 後 `ret`），那是尾呼叫**。逆向時別把這個 `jmp` 誤讀成「跳到另一段 code 繼續執行」——它語意上是「呼叫並返回」。

另一個外部函式的尾呼叫例（`extern int compute(int); int wrapper(int x){ return compute(x+1); }`，`-O2` 真跑）：

```asm
0000000000000000 <wrapper>:
   0:	endbr64
   4:	add    $0x1,%edi           ; x + 1
   7:	jmp    c <wrapper+0xc>     ; jmp（重定位後指向 compute）→ 尾呼叫
```

同樣是 `jmp` 收尾，不是 `call`。**尾呼叫是 call graph 分析的陷阱**：如果你只靠「找 `call` 指令」建呼叫關係，會漏掉所有尾呼叫的邊。要把「結尾的 `jmp 到另一函式」也算成一條呼叫邊。

## inline 的痕跡：函式蒸發後怎麼認回來

`-O2` 最狠的變換是 **inline**：把小函式的 body 直接複製進呼叫者，函式本身**從 binary 消失**（objdump 裡沒有它的 label）。這是 [Ch 0](./00-environment-and-ground-truth-loop.md) 裡 `secret` 消失的機制。

### 真跑：secret 被 inline 進 use_secret

```c
static int secret(int x){ return x * 3 + 7; }
int use_secret(int x){ return secret(x) + secret(x + 1); }
```

`gcc -O2` 真跑，先確認 `secret` 這個 label 存不存在：

```
$ objdump -d ch7_O2 | grep -c "<secret>"
0                                  ← secret 這個函式在 binary 裡不存在了
```

`secret` 蒸發了。再看 `use_secret`（真跑）：

```asm
00000000000011c0 <use_secret>:
    11c0:	endbr64
    11c4:	lea    (%rdi,%rdi,2),%eax     ; ┐ eax = x + x*2 = 3x（一個 secret 的 *3）
    11c7:	lea    0x11(%rax,%rax,1),%eax  ; ┘ eax = 3x + 3x + 0x11 = 6x + 17
    11cb:	ret
```

發生了什麼？`use_secret(x)` = `secret(x) + secret(x+1)` = `(3x+7) + (3(x+1)+7)` = `6x + 17`（`0x11`）。編譯器**把兩次 `secret` 呼叫全部 inline、代數化簡成一個運算式**，`secret` 這個函式完全不存在了。兩條 `lea` 就把整件事算完：`lea (%rdi,%rdi,2)` = `3x`，`lea 0x11(%rax,%rax,1)` = `2*(3x) + 17` = `6x+17`。

**逆向 inline 的心法**：

1. **沒有 `call` 卻做了「本該是別的函式」的工作**：`use_secret` 裡沒有任何 `call`，但它算的東西明顯是「某個 `3x+7` 邏輯用了兩次」。這種「該有呼叫卻沒有」是 inline 的第一個嗅覺。
2. **認出重複的 code pattern**：當同一個函式被 inline 進多個呼叫者，你會在不同地方看到**同一段指令序列重複出現**。看到「這五個地方都有一模一樣的 `lea ...; and ...; cmp ...`」，那八成是同一個 inline 函式的多份複本——把它認出來、命名、當成一個邏輯單位，你的分析就從「一堆散指令」升級成「這裡呼叫了 helper X」。
3. **代數被摺疊**：像這裡 `(3x+7)+(3(x+1)+7)` 被算成 `6x+17`，原始的兩次呼叫、加法結構全沒了。看到一個「不太自然的常數」（`0x11`=17）要想「這可能是幾個運算摺疊後的結果」，反推它怎麼來的。

這正是 [Ch 0](./00-environment-and-ground-truth-loop.md) 講的：**你逆的是編譯器的產物，不是原稿。** inline 讓「函式」這個你以為最穩固的邊界都會消失。認回它靠的是「重複 pattern + 該有呼叫卻沒有 + 常數反推」。

## 對比與取捨

| 特徵 | `-O0`（可讀對照） | `-O2`（真實樣貌） | 認法 |
|---|---|---|---|
| 函式邊界 | `push %rbp` … `pop %rbp; ret` | 葉函式無 prologue，只剩 `endbr64`…`ret` | 找 `endbr64`（入口）+ `ret`（出口）|
| frame pointer | 一定有 `mov %rsp,%rbp` | 常省略、`rbp` 拿去當一般暫存器 | 有無 `mov %rsp,%rbp` |
| 參數個數 | spill 到 stack 好數 | 看賦值前讀了哪些參數暫存器 | 用到 rdi..r9 的最遠一個 |
| 回傳值 | 結尾 `mov ...,%eax` | 同 | 結尾寫進 rax/eax 的值 |
| 遞迴 | `call` 自身入口 | 可能被轉成迴圈（無 call）| 找 `call 自己` / 認累乘迴圈 |
| 尾呼叫 | `call` 後 `ret` | `jmp` 取代 `call` | 結尾 `jmp 別的函式` |
| inline | 保留獨立函式 | 函式蒸發、body 複製進呼叫者 | 重複 pattern + 該有 call 卻沒有 |

## 踩雷集錦

1. **以為每個函式都有 `push %rbp` prologue**：葉函式和 `-O2` 省 frame pointer 的函式沒有。用「`endbr64` + `ret`」找邊界比找 prologue 可靠。
2. **把 call 別人時設的參數暫存器，誤當成自己的入參**：函式 A 內部 `call B` 前會設 `rdi/rsi`——那是給 B 的參數，不是 A 收了幾個參數。認 A 的參數，只看「A 開頭、在第一次寫入前就被讀取」的暫存器。
3. **把尾呼叫的 `jmp` 讀成普通跳轉**：`jmp strlen@plt` 語意上是「呼叫並返回」，不是「跳過去繼續同一個函式」。漏掉它 call graph 會缺邊。
4. **看到遞迴被優化成迴圈就否定它是遞迴**：`-O2` 把尾遞迴/可迭代遞迴轉迴圈是常態。「累乘/累加 + 計數器遞減的迴圈」原始碼很可能是遞迴——別因為沒 `call` 就排除。
5. **沒認出 inline，把重複 pattern 當成不同邏輯**：同一個 inline 函式散在五處，看起來像五段無關 code。認出它們是同一段複本，把它抽象成「一次 helper 呼叫」，分析量立刻減少。反過來，把兩段**碰巧相似**的 code 硬當成同一函式也會錯——要看指令序列是否真的一致。
6. **把常數摺疊的結果當成原始常數**：`use_secret` 裡的 `0x11`(17) 不是原始碼寫的 17，是 `7+3+7` 摺疊出來的。看到「怪常數」先懷疑它是幾個運算摺疊的產物，反推來源。

## 進階：再往深一層

- **PLT 與外部函式**：`call strlen@plt` 裡的 `@plt` 是「程序連結表（Procedure Linkage Table）」的樁，動態連結的函式（libc 的 `printf`/`strlen`…）都透過它呼叫。認出 `@plt` 呼叫就等於認出「這裡呼叫了某個外部庫函式」——是識別標準庫使用的關鍵，Ch 11 認 stdlib 指紋會深入，ELF 動態連結機制見 [Ch 3](./03-elf-anatomy-and-loading.md)。
- **函式指標與間接呼叫**：`call *%rax` / `call *0x8(%rbx)` 是間接呼叫——目標在執行期才定（函式指標、C++ 虛擬函式 vtable、callback）。靜態逆向看不出它呼叫誰，要動態逆（Part 2）或型別分析（Ch 20 的 vtable）。它和 [Ch 5](./05-recognizing-control-flow.md) 的 jump table 間接跳轉是親戚。
- **反編譯器怎麼標函式**：Ghidra/IDA 靠掃描 prologue pattern、`call` 目標、CET landing pad 來自動辨識函式邊界，但對「省 frame 的葉函式」「被 inline 的函式」「間接呼叫的目標」常標錯或漏標。Ch 8 專講反編譯器在函式識別上怎麼騙你。
- **控制 inline 觀察**：把本章的 `use_secret` 貼上 [Compiler Explorer](https://godbolt.org/)，加 `__attribute__((noinline))` 到 `secret` 上，看它從「蒸發」變回「獨立函式 + call」。這是理解 inline 邊界最直接的實驗。

## 本章重點整理

- 函式是逆向的工作單位。**用 `endbr64`（入口）+ `ret`（出口）劃邊界比找 prologue 可靠**——葉函式和 `-O2` code 常沒有 `push %rbp`。
- **函式簽章可考古**：數「賦值前讀了哪些參數暫存器」得參數個數、看寬度得型別、看結尾寫進 rax 的值得回傳值——全靠呼叫慣例，不需符號表。
- **遞迴** = `call` 自身入口；但 `-O2` 可能把它轉成迴圈（無 call 的累乘/累加）。
- **尾呼叫** = 結尾用 `jmp 別的函式` 取代 `call`＋`ret`；建 call graph 時別漏掉這條邊。
- **inline** 讓函式在 `-O2` 蒸發：認回它靠「重複的 code pattern + 該有 call 卻沒有 + 怪常數是摺疊產物」——呼應 Ch 0 的 secret 消失。
- **你逆的是編譯器的產物**：函式邊界、遞迴結構、呼叫關係都可能被優化抹掉或改寫。

## 自我檢核

- [ ] 我能用 `endbr64` 和 `ret` 在一段 objdump 裡框出每個函式的邊界，即使沒有 `push %rbp`
- [ ] 我看到一個函式讀了 `rdi,rsi,rdx`、結尾寫 `eax`，能推出它大概是「3 個參數、回傳 int」的簽章
- [ ] 我看到函式裡 `call` 指向自己的入口位址，知道那是遞迴
- [ ] 我看到函式結尾是 `jmp strlen@plt` 而非 `call`，知道那是尾呼叫（呼叫並返回）
- [ ] 我看到一段沒有任何 `call`、卻算出「像是某邏輯用了兩次」的 code，會懷疑有函式被 inline
- [ ] 我知道 `-O2` 可能把遞迴轉成迴圈、把 `x*3+7` 這種 helper 摺疊成怪常數，不會因結構消失就否定原始邏輯

## 延伸閱讀

### 書籍

- **《Reverse Engineering for Beginners》(RE101)** — Dennis Yurichev（[免費](https://beginners.re/)）
  - **定位**：函式 ↔ asm 的最佳題庫。
  - **讀哪裡**：`Function prologue/epilogue`、`Recursion`、`Tail call`、`Inline functions` 各節——每個都有 asm 對照，把本章的認法練成反射。
- **《Practical Binary Analysis》** — Dennis Andriesse（No Starch, 2019）
  - **定位**：把函式識別與 call graph 建構系統化。
  - **讀哪裡**：Ch 6（disassembly）與 Ch 8（自訂分析），講工具怎麼自動辨識函式邊界、建 call graph——理解它的啟發式才知道它何時出錯。

### 官方文件 / 工具

- **[Compiler Explorer (godbolt.org)](https://godbolt.org/)**
  - **這是什麼**：即時觀察 inline/尾呼叫/遞迴優化。加 `noinline`/`always_inline` 屬性看函式邊界的出現與消失。
  - **怎麼用**：貼本章 `use_secret`，切 `-O0`/`-O2`，在 `secret` 上加減 `__attribute__((noinline))`，親眼看 inline 邊界。
- **[System V AMD64 ABI 規格](https://gitlab.com/x86-psABIs/x86-64-ABI)**
  - **這是什麼**：參數暫存器、回傳值、frame 佈局的權威定義——本章「從暫存器反推簽章」的規則出處。
  - **讀哪裡**：§3.2「Function Calling Sequence」，尤其 struct/大物件怎麼傳（by pointer / 拆進多暫存器）——複雜簽章還原時查它。

Part 1 到這裡，你已經能徒手從 objdump 認出控制流、資料、函式——binary 的三大骨架。但徒手讀 asm 很慢，現代逆向的主力是**反編譯器**：它把 asm 還原成類 C 的 pseudocode。下一章我們就來讀反編譯器的輸出——以及它會怎麼騙你：掰出不存在的變數、猜錯型別、把一個 struct 拆成散落的 offset。認得出它的謊言，你才駕馭得了它。

→ [Ch 8 讀反編譯器輸出：它的謊言與怎麼騙你](./08-reading-decompiler-output.md)
