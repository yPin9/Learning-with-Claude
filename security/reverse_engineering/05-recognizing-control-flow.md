# Ch 5 — 認出控制流：if / loop / switch

> **目標**：學會從一堆 `cmp` / `test` / 條件跳轉裡，反推出高階的控制結構——這個 `jne` 是 `if` 的哪一半、那個向後跳的 `jl` 是 `for` 迴圈的邊界、這串間接跳轉是 `switch` 的 jump table。逆向者不是逐條讀指令，是**把跳轉圖（CFG）還原成 C 的控制流骨架**。每種結構都用 `-O0`（可讀對照）與 `-O2`（真實樣貌）真跑對照。

> **環境**：WSL2 / Linux x86-64，gcc 11.4 + objdump。本章所有 asm 都是真跑 `objdump -d` 輸出。

## 為什麼需要這個？

[Ch 4](./04-x86-64-for-reversers.md) 給了你字母表（暫存器、指令、呼叫慣例）。但一段 asm 光認得每條指令，還是讀不懂——就像你認得每個英文單字卻讀不懂句子。**控制流是 asm 的語法**：它決定了指令的執行順序、哪些會跑、哪些是分支。

C 的控制流是結構化的：`if/else`、`for/while`、`switch` 都有清楚的巢狀邊界。但編譯器把它們**全部壓平成 `cmp` + 條件跳轉**——巢狀不見了，變成一堆往前往後跳的 `jXX`。逆向的第一個真本事，就是把這團 goto 湯**還原回結構化的控制流**。這件事你在讀 source 時免費得到（縮排就告訴你了），在 binary 裡要自己重建。

> 這正是 [`reading_code`](../../soft_skills/reading_code/README.md) 「假設驅動」在 binary 的鏡像：看到一個向後跳，先假設「這是迴圈」，再驗證跳轉條件和計數器對不對。反編譯器（Ghidra/IDA）做的核心工作之一，就是這個 CFG → 結構化控制流的還原——但它會猜錯，所以你得懂原理。

## 先建立直覺：控制流圖（CFG）

在認 pattern 前，先裝好一個心智工具：**控制流圖（Control Flow Graph, CFG）**。把 asm 切成一段段「基本區塊（basic block）」——每個區塊是一串**沒有分支、一路執行到底**的指令，直到遇到跳轉或被別人跳進來。區塊之間用箭頭（跳轉）連起來，就是 CFG。

```
   一段有 if 的 code 的 CFG：           一個 loop 的 CFG：

      ┌──────────┐                        ┌──────────┐
      │ cmp; jXX │                        │  init    │
      └──┬────┬──┘                        └────┬─────┘
   false │    │ true                           ▼
         ▼    ▼                          ┌──►┌──────────┐
    ┌─────┐ ┌─────┐                      │   │ 迴圈本體 │
    │else │ │ if  │                      │   └────┬─────┘
    └──┬──┘ └──┬──┘                      │        ▼
       └───┬───┘                         │   ┌──────────┐
           ▼                             └───┤cmp; j 向後│ ← 向後跳=迴圈的指紋
     ┌──────────┐                            └────┬─────┘
     │  merge   │                                 ▼ false
     └──────────┘                            ┌──────────┐
                                             │  迴圈後  │
```

兩個一眼可辨的形狀：**分支（一分為二再合流）= if/else**；**向後跳（箭頭指回前面）= 迴圈**。逆向時你先在腦中（或紙上、或反編譯器裡）畫出這張圖，結構就浮現了。跳轉的**方向**是最強的線索：**往後跳幾乎一定是迴圈，往前跳通常是 if 的 skip。**

## if / else：cmp + 條件跳轉

C 的 `if` 在 asm 裡永遠是「**比較 → 條件跳轉跳過某段**」。關鍵在讀懂「跳轉條件是原 C 條件的**反面**」——因為編譯器的邏輯是「條件**不成立**時**跳過** if 的 body」。

### 真跑：三分支的 if/else if/else

```c
int classify(int x){
    if (x < 0) return -1;
    else if (x == 0) return 0;
    else return 1;
}
```

`gcc -O0` 真跑（`objdump -d`）：

```asm
0000000000001149 <classify>:
    1149:	endbr64
    114d:	push   %rbp
    114e:	mov    %rsp,%rbp
    1151:	mov    %edi,-0x4(%rbp)       ; x 存 slot
    1154:	cmpl   $0x0,-0x4(%rbp)       ; ┐ 比較 x 和 0
    1158:	jns    1161 <classify+0x18> ; ┘ jns = jump if not sign = 若 x>=0 則跳過
    115a:	mov    $0xffffffff,%eax      ; ← x<0 這半：return -1
    115f:	jmp    1173 <classify+0x2a> ; 跳到結尾（別掉進下一分支）
    1161:	cmpl   $0x0,-0x4(%rbp)       ; ┐ 比較 x 和 0
    1165:	jne    116e <classify+0x25> ; ┘ 若 x != 0 則跳過
    1167:	mov    $0x0,%eax             ; ← x==0 這半：return 0
    116c:	jmp    1173 <classify+0x2a>
    116e:	mov    $0x1,%eax             ; ← else：return 1
    1173:	pop    %rbp
    1174:	ret
```

逐點對照原始 C：

1. `if (x < 0)` → `cmpl $0x0, -0x4(%rbp)` 後接 **`jns`**（jump if **not** sign）。C 寫的是「x<0 時做某事」，asm 反過來寫成「x**不小於**0 就跳過」。**條件跳轉的條件是 C 條件的反義**——這是讀 if 的第一心法。
2. 每個分支結尾都有一條 `jmp 1173`——把各分支「縫」到共同出口，避免執行掉進下一分支。這串 `jmp 到同一個位址` 是 if/else if 鏈的指紋。
3. 合流點 `1173`（epilogue）就是 CFG 裡那個 merge 區塊。

**條件碼速查（`cmp b, a` 之後，AT&T 順序是「a 對 b」）**：

| 跳轉 | 跳的條件 | signed? | 常對應的 C（跳過時）|
|---|---|---|---|
| `je` / `jz` | a == b | — | `if (a != b)` 的反面 |
| `jne`/`jnz` | a != b | — | `if (a == b)` 的反面 |
| `jl` / `jnge` | a < b | signed | `if (a >= b)` 反面 |
| `jle` | a <= b | signed | `if (a > b)` 反面 |
| `jg` / `jnle` | a > b | signed | `if (a <= b)` 反面 |
| `jge` | a >= b | signed | `if (a < b)` 反面 |
| `jb` / `ja` … | 無號版本 | **unsigned** | 型別是 unsigned 的線索 |
| `js` / `jns` | 符號位=1 / =0 | — | `x<0` / `x>=0` 的常見寫法 |

**認 signed vs unsigned 的免費線索**：`jl/jg/jle/jge` 是 signed 比較，`jb/ja/jbe/jae` 是 unsigned 比較。看到 `ja`（above）就知道那個變數是 unsigned——這是 Ch 9 還原型別 signedness 的直接證據。

### 對照 `-O2`：if 常變成無跳轉的 cmov

```c
int pick(int a, int b){ return a > b ? a : b; }
```

三元運算子（和簡單 if）在 `-O2` 常被編成**條件搬移 `cmov`**，完全沒有跳轉（真跑）：

```asm
0000000000001220 <pick>:
    1220:	endbr64
    1224:	cmp    %edi,%esi             ; 比較 a(edi) 和 b(esi)
    1226:	mov    %edi,%eax             ; 先假設結果 = a
    1228:	cmovge %esi,%eax             ; 若 esi>=edi（b>=a）則改成 b
    122b:	ret
```

`cmovge %esi, %eax` = 「如果 b>=a 就把 eax 改成 b」。編譯器用它取代分支，因為**沒有跳轉就沒有分支預測失敗**，對現代 CPU 更快。逆向時看到 `cmovXX` 要讀成「一個 `? :` 或簡單 if 被消成無分支」——它是控制流，只是沒有跳。認不出來的話你會以為那條 `mov` 一定會執行（其實它有條件）。

## loop：向後跳就是迴圈

迴圈的指紋是 **一個往回指的條件跳轉**（`jl`/`jne` 跳到比自己位址小的地方）。`for`/`while`/`do-while` 的差別在於「條件檢查放在迴圈的頭還是尾」。

### 真跑：for 迴圈

```c
int sum_to(int n){
    int s = 0;
    for (int i = 0; i < n; i++) s += i;
    return s;
}
```

`gcc -O0` 真跑：

```asm
0000000000001175 <sum_to>:
    1175:	endbr64
    1179:	push   %rbp
    117a:	mov    %rsp,%rbp
    117d:	mov    %edi,-0x14(%rbp)      ; n
    1180:	movl   $0x0,-0x8(%rbp)       ; s = 0
    1187:	movl   $0x0,-0x4(%rbp)       ; i = 0   ← 迴圈初始化
    118e:	jmp    119a <sum_to+0x25>    ; ★ 先跳去做條件檢查（for 的特徵）
    1190:	mov    -0x4(%rbp),%eax       ; ┐ 迴圈本體：
    1193:	add    %eax,-0x8(%rbp)       ; ┘ s += i
    1196:	addl   $0x1,-0x4(%rbp)       ; i++     ← 計數器遞增
    119a:	mov    -0x4(%rbp),%eax       ; ┐ 條件檢查：
    119d:	cmp    -0x14(%rbp),%eax      ; │ i 和 n 比
    11a0:	jl     1190 <sum_to+0x1b>    ; ┘ ★ i<n 就往回跳（向後跳=迴圈）
    11a2:	mov    -0x8(%rbp),%eax       ; 迴圈結束，return s
    11a5:	pop    %rbp
    11a6:	ret
```

還原步驟（這是逆迴圈的 SOP）：

1. **找向後跳**：`11a0: jl 1190`，跳到 `1190` < `11a0`，往回跳——這是迴圈的鐵證。迴圈本體 = `1190`~`11a0` 這段。
2. **找計數器**：本體裡 `addl $0x1, -0x4(%rbp)` 每輪 +1 的那個 slot（`-0x4(%rbp)`）就是 `i`。
3. **找邊界**：`cmp -0x14(%rbp), %eax; jl` 告訴你迴圈條件是 `i < n`（n 在 `-0x14(%rbp)`）。
4. **認 for 的指紋**：進入迴圈前那條 `118e: jmp 119a`（先跳到條件檢查再進本體）是 gcc 編 `for`/`while` 的典型——**「條件在底、先跳去檢查」的結構代表這是頭部檢查迴圈（for/while）**。若是 `do-while`（尾部檢查），就不會有這條前置 `jmp`，直接落進本體。

### 三種迴圈的 asm 差異

| C | 結構特徵 | 指紋 |
|---|---|---|
| `for(init;cond;inc)` | init → **jmp 到 cond** → body → inc → cond → 向後跳 | 進迴圈前有一條 `jmp` 到底部條件 |
| `while(cond)` | 同 for，只是沒有 inc（或 inc 在 body 裡）| 同上，前置 `jmp` |
| `do{}while(cond)` | body → cond → 向後跳（**沒有**前置 jmp）| 一進來直接是 body，條件只在尾巴 |

`do-while` 因為「保證至少跑一次」，不需要進迴圈前先檢查條件，所以少那條前置 `jmp`——這是區分它和 `for`/`while` 的關鍵。**`-O2` 常把 `for`/`while` 也轉成 `do-while` 形狀**（如果編譯器能證明迴圈至少跑一次），並把條件檢查搬到底部、頂上只留一次進入檢查——所以真實 binary 裡你看到的多半是「頂部一個進入守衛 + 底部向後跳」的形狀。

## switch：小 case 變 if 鏈，密集 case 變 jump table

`switch` 有兩種完全不同的 asm 化身，取決於 case 值的分布：

- **case 少或稀疏** → 編譯器編成一串 `cmp; je`（等價於 if/else if 鏈）。
- **case 多且密集**（如 0,1,2,3,4）→ 編成 **jump table（跳轉表）**：用 case 值當索引，查一張位址表，直接**間接跳轉**過去。這是 switch 最有辨識度的 pattern。

### 真跑：密集 case → jump table

```c
int dispatch(int cmd){
    switch (cmd){
        case 0: return 100;   case 1: return 200;
        case 2: return 300;   case 3: return 400;
        case 4: return 500;   default: return -1;
    }
}
```

`gcc -O0` 真跑：

```asm
00000000000011a7 <dispatch>:
    11a7:	endbr64
    11ab:	push   %rbp
    11ac:	mov    %rsp,%rbp
    11af:	mov    %edi,-0x4(%rbp)       ; cmd
    11b2:	cmpl   $0x4,-0x4(%rbp)       ; ┐ 邊界檢查：cmd 和最大 case(4) 比
    11b6:	ja     11ff <dispatch+0x58> ; ┘ ★ unsigned 比！cmd>4（含負數）→ default
    11b8:	mov    -0x4(%rbp),%eax       ; ┐
    11bb:	lea    0x0(,%rax,4),%rdx     ; │ 用 cmd 當索引：offset = cmd*4
    11c3:	lea    0xe3a(%rip),%rax      ; │ rax = jump table 基址（.rodata）
    11ca:	mov    (%rdx,%rax,1),%eax    ; │ 讀出表中第 cmd 項（一個 32-bit offset）
    11cd:	cltq                         ; │ 符號延伸成 64-bit
    11cf:	lea    0xe2e(%rip),%rdx      ; │ rdx = 基址（同一張表）
    11d6:	add    %rdx,%rax             ; │ 目標 = 基址 + offset
    11d9:	notrack jmp *%rax            ; ┘ ★ 間接跳轉！跳到算出來的位址
    11dc:	mov    $0x64,%eax            ; case 0: 100 (0x64)
    11e1:	jmp    1204 <dispatch+0x5d>
    11e3:	mov    $0xc8,%eax            ; case 1: 200 (0xc8)
    11e8:	jmp    1204 <dispatch+0x5d>
    11ea:	mov    $0x12c,%eax           ; case 2: 300 (0x12c)
    11ef:	jmp    1204 <dispatch+0x5d>
    11f1:	mov    $0x190,%eax           ; case 3: 400 (0x190)
    11f6:	jmp    1204 <dispatch+0x5d>
    11f8:	mov    $0x1f4,%eax           ; case 4: 500 (0x1f4)
    11fd:	jmp    1204 <dispatch+0x5d>
    11ff:	mov    $0xffffffff,%eax      ; default: -1
    1204:	pop    %rbp
    1205:	ret
```

jump table 的三段式指紋，逐一對照：

1. **邊界檢查用 `ja`（unsigned above）**：`cmpl $0x4; ja default`。為什麼是 unsigned？因為若 `cmd` 是負數，當成 unsigned 會變成超大值 > 4，一次就把「負數」和「>4」兩種越界都擋掉、跳去 default。看到 `switch` 前的 `ja` 別困惑，那是 range check 的慣用語。
2. **`lea (,%rax,4)` 算表索引**：`cmd * 4`（每項 4 bytes），加上表基址，讀出一個 offset。
3. **`jmp *%rax`（間接跳轉）**：`*` 代表「跳到 rax 裡**存的位址**」，不是跳到 rax。**這條 `jmp *(...)` 就是 jump table 的招牌**——看到間接跳轉，八成是 switch（或函式指標、vtable，Ch 6/Ch 20 會分辨）。

真跑看那張表本身（`objdump -s -j .rodata`）：

```
Contents of section .rodata:
 2000 01000200 d8f1ffff dff1ffff e6f1ffff  ................
 2010 edf1ffff f4f1ffff 25642025 64202564  ........%d %d %d
```

從 `2004` 開始那 5 個 4-byte 小端值：`d8f1ffff` `dff1ffff` `e6f1ffff` `edf1ffff` `f4f1ffff`——就是 5 個 case 的跳轉 offset（相對表基址的有號位移，都是負的因為 code 在表前面）。**逆向時看到 `.rodata` 裡一排等距、值相近的 4-byte 數，那就是一張 jump table**，數一數幾項就知道 switch 有幾個 case。

> 若 case 稀疏（如 `case 1, 100, 9999`），編譯器不會做這麼大的表，改成一串 `cmp $1; je ...; cmp $100; je ...`——退化成 if 鏈。所以「switch」和「if/else if 鏈」在 asm 裡沒有本質分別，是同一種 pattern 的兩端；反編譯器要靠啟發式猜你原本寫的是哪個。

## 短路求值：&& 和 || 是「藏起來的分支」

`a && b`：C 保證 a 為假就不算 b。這個「短路」在 asm 裡就是**多一個提早跳出的條件跳轉**。

### 真跑：&&

```c
int both(int a, int b){
    if (a > 0 && b > 0) return 1;
    return 0;
}
```

`gcc -O0` 真跑：

```asm
0000000000001206 <both>:
    1206:	endbr64
    120a:	push   %rbp
    120b:	mov    %rsp,%rbp
    120e:	mov    %edi,-0x4(%rbp)       ; a
    1211:	mov    %esi,-0x8(%rbp)       ; b
    1214:	cmpl   $0x0,-0x4(%rbp)       ; ┐ a > 0 ?
    1218:	jle    1227 <both+0x21>      ; ┘ a<=0 → 直接跳去 return 0（短路！不看 b）
    121a:	cmpl   $0x0,-0x8(%rbp)       ; ┐ b > 0 ?（只有 a>0 才走到這）
    121e:	jle    1227 <both+0x21>      ; ┘ b<=0 → return 0
    1220:	mov    $0x1,%eax             ; 兩個都 >0 → return 1
    1225:	jmp    122c <both+0x26>
    1227:	mov    $0x0,%eax             ; return 0
    122c:	pop    %rbp
    122d:	ret
```

還原：兩個 `cmp; jle` **各跳到同一個 false 出口（`1227`）**，而且**第一個檢查失敗就直接跳走、不做第二個檢查**——這正是 `&&` 短路的 asm 形狀。「**兩個條件、各自跳去同一個 fail 標籤、串在一起**」= `&&`。

對稱地，`||` 是「任一條件成立就跳去 **success**」：兩個 `cmp` 各跳到同一個 true 出口。**認法：跳去 fail = `&&`，跳去 success = `||`。** 巢狀的 `&&`/`||` 就是這兩種 pattern 疊起來，逆向時一層層拆。

## 對比與取捨

| C 結構 | `-O0` 樣貌 | `-O2` 常見變化 | 招牌指紋 |
|---|---|---|---|
| `if/else` | `cmp` + 條件跳 + `jmp` 縫合 | 可能變 `cmov` 無分支 | 一分為二、跳去 merge |
| `?:` 三元 | 同 if | 幾乎一定 `cmov` | `cmovXX` |
| `for/while` | init→jmp 條件→body→向後跳 | 常轉成 do-while 形狀、可能展開/向量化 | **向後跳** + 計數器 +1 |
| `do-while` | body→尾部條件→向後跳 | 同上 | 向後跳、**無**前置 jmp |
| `switch`(密集) | range check + jump table + `jmp *` | 同左，或部分退化 | `ja` + `jmp *(...,%r,N)` + .rodata 位址表 |
| `switch`(稀疏) | 一串 `cmp; je` | 同左 | 等同 if/else if 鏈 |
| `&&` | 兩 cmp 各跳同一 fail | 同左 | 跳去 fail 標籤 |
| `\|\|` | 兩 cmp 各跳同一 success | 同左 | 跳去 success 標籤 |

## 踩雷集錦

1. **把條件跳轉的條件當成 C 的條件**：看到 `jns`（x>=0 跳）以為原 C 是 `if(x>=0)`，其實編譯器寫的是 `if(x<0)` 然後「x>=0 就跳過 body」。**條件跳轉的條件通常是 C 條件的反面**。要看它「跳過了什麼」才知道 C 原意。
2. **沒注意 signed/unsigned 跳轉**：`jl`（signed）和 `jb`（unsigned）在 CFG 上長一樣，但意義不同。看到 `ja`/`jb` 就該想「這個值是 unsigned」——這是還原型別的免費線索，讀反了會誤判邊界邏輯。
3. **看到 `cmov` 以為那條 mov 一定執行**：`cmovge` 是有條件的搬移，它是被消掉分支的 if。漏掉它的條件性，會把一個 `? :` 讀成無條件賦值。
4. **把 jump table 的間接跳轉當成亂跳/混淆**：`jmp *%rax` 是正常的 switch 實作，不是反調試花招。先去 `.rodata` 找那張位址表，數 case 數，別當成 obfuscation。
5. **分不清 for 和 do-while**：關鍵在「進迴圈前有沒有前置 `jmp` 到底部條件」。有 = 頭部檢查（for/while）；沒有、一進來就是 body = 尾部檢查（do-while）。`-O2` 會把 for 也轉成 do-while 形狀，別因此誤判原始碼寫的是 do-while。
6. **`-O2` 迴圈被展開/向量化後認不出**：編譯器可能把迴圈展開（一輪做 4 次）或用 SSE 一次處理多個元素，向後跳的次數變少、body 變大且出現 `xmm` 暫存器。這時「一個乾淨計數器 +1」的形狀被打散——認不出來是正常的，退回動態逆向（Part 2）看它實際跑幾輪。

## 進階：再往深一層

- **畫 CFG 是逆向的核心動作**：反編譯器和 IDA/Ghidra 內部都先建 CFG 再還原結構。你手動逆時，把 objdump 輸出裡每個跳轉目標標出來、切基本區塊、連箭頭，結構會自己浮現。Ghidra 的 Function Graph、IDA 的 graph view 就是幫你自動畫這個——但你要懂原理才知道它畫錯時怎麼修。
- **jump table 的變體**：除了「位址表」，還有「跳到 `base + case*固定步長`」的算術式跳轉（case 之間 code 等長時），以及巢狀/多層 table（大 switch 分段）。Ch 22 逆大 binary 會遇到。
- **不可歸約控制流（irreducible CFG）**：`goto`、某些優化、混淆器會製造「無法還原成乾淨 if/loop」的 CFG。反編譯器遇到這種只能吐一堆 `goto label`。Ch 23 對抗混淆會碰到刻意製造的 irreducible flow。
- **反查練習**：把本章每段 C 貼上 [Compiler Explorer](https://godbolt.org/)，切 `-O0`/`-O2`/`-O3`，看 if 何時變 cmov、迴圈何時被展開、switch 何時退化成 if 鏈。這是建立「控制流 pattern 字典」最快的方法。

## 本章重點整理

- 控制流是 asm 的語法：把指令切成基本區塊、連成 **CFG**，if/loop/switch 的形狀就浮現。**跳轉方向是最強線索：往後跳=迴圈，往前跳=if 的 skip。**
- **if**：`cmp/test` + 條件跳轉，且**跳轉條件是 C 條件的反面**（條件不成立時跳過 body）；各分支用 `jmp` 縫到共同 merge 點。
- **signed/unsigned 跳轉**（`jl/jg` vs `jb/ja`）是免費的型別線索。
- **`cmov`** 是被消掉分支的 if / 三元運算子——它是控制流，只是沒有跳。
- **loop**：向後跳 + 計數器 +1；`for/while`（頭部檢查、有前置 jmp）vs `do-while`（尾部檢查、無前置 jmp），`-O2` 常把前者轉成後者形狀。
- **switch**：密集 case → range check(`ja`) + jump table(`jmp *(...)`) + `.rodata` 位址表；稀疏 case → 退化成 if/else if 鏈。
- **`&&`/`||`**：多一個提早跳出的條件跳轉；跳去 fail=`&&`，跳去 success=`||`。

## 自我檢核

- [ ] 我看到 `cmp $0x0; jns` 能還原出原 C 大概是 `if (x < 0) {...}`（跳轉是反義）
- [ ] 我能從一個向後跳的 `jl` 定位出迴圈本體、計數器、邊界條件
- [ ] 我能靠「有沒有前置 jmp 到底部條件」區分 for/while 和 do-while
- [ ] 我看到 `ja` + `jmp *(%rdx,%rax,...)` + 一排 .rodata 位址，知道這是 switch 的 jump table，並能數出 case 數
- [ ] 我看到 `cmovge` 知道它是被消掉分支的 `? :` 或簡單 if，那條 mov 是有條件的
- [ ] 我能靠「兩個 cmp 跳去 fail 還是跳去 success」分辨 `&&` 和 `||`

## 延伸閱讀

### 書籍

- **《Reverse Engineering for Beginners》(RE101)** — Dennis Yurichev（[免費](https://beginners.re/)）
  - **定位**：控制流 pattern 的最佳題庫。
  - **讀哪裡**：`if`、`switch`、`loops` 各章——每種都有多編譯器、多優化等級的 asm 對照，把本章的 pattern 練成反射。特別看它的 switch/jump table 章。
- **《The Art of Assembly Language》** — Randall Hyde（免費線上版）
  - **定位**：從「高階結構如何編成 asm」正著教，逆向時反著讀。
  - **讀哪裡**：控制結構那幾章（if/loop/switch 的 asm 生成），補足「編譯器為什麼這樣編」的直覺。

### 官方文件 / 工具

- **[Compiler Explorer (godbolt.org)](https://godbolt.org/)**
  - **這是什麼**：即時看控制流結構 ↔ asm。想知道某個 if 何時變 cmov、迴圈何時被展開，切優化等級即時對照。
  - **怎麼用**：貼本章的 C、切 `-O0`/`-O2`/`-O3`，觀察同一段 switch 在不同等級下 jump table 是否退化。
- **[Ghidra](https://ghidra-sre.org/) 的 Function Graph / [IDA](https://hex-rays.com/) 的 Graph View**
  - **這是什麼**：自動畫 CFG 的工具，把 asm 變成視覺化的區塊圖。
  - **怎麼用**：對本章的 `dispatch`/`sum_to` 按圖形視圖，親眼看基本區塊怎麼連——但記得它會猜錯，卡住時回去手動讀跳轉。交叉引用你的 [`ida_pro`](../ida_pro/README.md) 課。

控制流是骨架，但骨架上要掛肉——資料。下一章我們認出 asm 裡的變數、陣列、struct、全域、指標：`rbp-0x4` 是哪個局部變數、`0x8(%rax)` 在存取 struct 的哪個欄位、`(%rdi,%rsi,8)` 在索引什麼陣列。

→ [Ch 6 認出資料：struct / array / 指標 / 全域](./06-recognizing-data.md)
