# Ch 9 — 型別與結構還原

> **目標**：把上一章「反編譯器最愛在型別和 struct 上騙人」的問題，變成一套你自己動手的還原流程。學會從 `base + offset` 存取逆出 struct 佈局、從存取寬度判欄位型別、分清陣列 vs struct、認出 union / 巢狀 struct / 指標鏈、從比較的常數值域還原 enum。全程 ground-truth：寫一個佈局明確的 struct、編、strip、從 asm 逆出定義，再打開 source 對答案。

> **環境**：WSL2 / Linux x86-64，gcc + objdump。本章的 asm 全部真跑。

## 為什麼需要這個？

Ch 6 你已經會認「這裡有一個 struct」。但認出「有結構」離「知道結構長什麼樣」還差一大截——而後者才是真正有用的：你要能寫出

```c
struct account { unsigned int id; char flags; long balance; char *owner; };
```

這樣一份定義，才能在反編譯器裡把它套上去、讓 `*(long*)(param+8)` 變回 `a->balance`，才能寫 parser 解析這個 binary 產生的資料，才能理解它的演算法在動哪些欄位。

struct 還原是逆向從「看得懂控制流」升級到「看得懂資料模型」的關鍵一步。而且它是**反編譯器最不可靠的地方**（Ch 8 講過，它常把 struct 拆成散 offset），所以這是你必須親手做的技能，不能外包給 F5。

## 先建立直覺：struct 在 asm 裡就是「一個 base + 一組固定 offset」

C 的 struct 在記憶體裡是**連續的一塊**，欄位按宣告順序排、中間可能有對齊填充（padding）。編譯器存取欄位時，永遠是「拿到 struct 起始位址（base），加上該欄位的固定 offset」：

```
   struct account a;   &a = base（某個暫存器，如 %rax）
                            │
   a.id       ← base + 0x00   （unsigned int, 4 bytes）
   （padding）  base + 0x04   （3 bytes 填充，湊 8 對齊）
   a.flags    ← base + 0x04   （char, 1 byte）  ← 注意 flags 在 0x04
   a.balance  ← base + 0x08   （long, 8 bytes）
   a.owner    ← base + 0x10   （char *, 8 bytes 指標）

   記憶體佈局（bytes）：
   0x00 0x01 0x02 0x03 | 0x04 | 0x05 0x06 0x07 | 0x08 ...... 0x0f | 0x10 ...... 0x17
   └──── id (4) ─────┘  flags   └─ padding ─┘   └─── balance ───┘  └──── owner ────┘
```

所以還原 struct 的核心動作只有一個：**收集所有 `base + 常數` 的存取，把 base 相同的那組 offset 列出來，就是這個 struct 的欄位表。** 每個 offset 配上它的**存取寬度**，就得到欄位型別。這一章就是把這個動作做熟。

## 真跑：從 asm 逆出 struct

ground-truth source（`st.c`）——先蓋起來，等下對答案：

```c
struct account {
    unsigned int  id;        // +0x00  4 bytes
    char          flags;     // +0x04  1 byte（後接 3 bytes padding）
    long          balance;   // +0x08  8 bytes
    char         *owner;     // +0x10  8 bytes
};

long process(struct account *a){
    a->balance += 100;
    if (a->flags & 1) a->balance *= 2;
    return a->balance + a->id;
}
```

`gcc -O0` 編，`objdump` 逆 `process`（真跑）：

```asm
$ objdump -d -M att --no-show-raw-insn st_O0
0000000000001189 <process>:
    1189:  endbr64
    118d:  push   %rbp
    118e:  mov    %rsp,%rbp
    1191:  mov    %rdi,-0x8(%rbp)      ; 參數 a（struct account*）存進 stack
    1195:  mov    -0x8(%rbp),%rax      ; rax = a（base）
    1199:  mov    0x8(%rax),%rax       ; ← 讀 base+0x8，8-byte（%rax）→ 欄位@0x8 是 8-byte
    119d:  lea    0x64(%rax),%rdx      ; rdx = *(base+0x8) + 100
    11a1:  mov    -0x8(%rbp),%rax      ; rax = a
    11a5:  mov    %rdx,0x8(%rax)       ; ← 寫回 base+0x8，8-byte → a->balance += 100
    11a9:  mov    -0x8(%rbp),%rax
    11ad:  movzbl 0x4(%rax),%eax       ; ← 讀 base+0x4，movzbl=1-byte！→ 欄位@0x4 是 1-byte
    11b1:  movsbl %al,%eax             ; 符號延伸（char，帶號）
    11b4:  and    $0x1,%eax            ; & 1
    11b7:  test   %eax,%eax
    11b9:  je     11cf <process+0x46>  ; if (flags & 1) == 0 跳過
    11bb:  mov    -0x8(%rbp),%rax
    11bf:  mov    0x8(%rax),%rax       ; 讀 base+0x8（balance）
    11c3:  lea    (%rax,%rax,1),%rdx   ; rdx = balance * 2
    11c7:  mov    -0x8(%rbp),%rax
    11cb:  mov    %rdx,0x8(%rax)       ; 寫回 base+0x8 → a->balance *= 2
    11cf:  mov    -0x8(%rbp),%rax
    11d3:  mov    0x8(%rax),%rdx       ; 讀 base+0x8（balance, 8-byte）
    11d7:  mov    -0x8(%rbp),%rax
    11db:  mov    (%rax),%eax          ; ← 讀 base+0x0，4-byte（%eax）→ 欄位@0x0 是 4-byte
    11dd:  mov    %eax,%eax            ; 零延伸（unsigned int → 8-byte 湊加法）
    11df:  add    %rdx,%rax            ; balance + id
    11e2:  pop    %rbp
    11e3:  ret
```

現在**不看 source**，純從 asm 收集欄位：

| 存取指令 | offset | 寬度 | 推斷型別 | 線索 |
|---|---|---|---|---|
| `mov 0x8(%rax),%rax` | +0x08 | 8 byte（`%rax`） | `long` / `int64` | 讀寫都 8-byte |
| `movzbl 0x4(%rax),%eax` | +0x04 | 1 byte（`movzbl`） | `char` | zbl = 讀 1 byte；後面 `movsbl` 表示帶號 |
| `mov (%rax),%eax` | +0x00 | 4 byte（`%eax`） | `int` / `unsigned` | 4-byte 讀；後面 `mov %eax,%eax` 零延伸 → **unsigned** |

逆出的 struct：

```c
struct recovered {
    /* +0x00 */ unsigned int field_0;   // 4-byte，零延伸 → unsigned
    /* +0x04 */ char         field_4;   // 1-byte movzbl
    /* +0x05 */ // 3 bytes padding（0x05~0x07 沒被存取，且 0x08 是 8-byte 欄位需 8 對齊）
    /* +0x08 */ long         field_8;   // 8-byte
    /* +0x10 */ // 沒在 process 裡出現 → 這函式沒碰 owner，但不代表欄位不存在
};
```

打開 source 對答案：`id`（unsigned int @0x0）、`flags`（char @0x4）、`balance`（long @0x8）——**全中**。你逆對了。`owner`（@0x10）在 `process` 裡沒被碰，所以逆不出來——這是重要教訓：**單一函式只揭露它碰到的欄位，完整佈局要看所有碰這個 struct 的函式（Ch 25 講交叉引用彙整）**。

跑一次驗證行為（id=5, flags=1, balance=50 → +100=150 → *2=300 → +5 = 305）：

```
$ ./st_O0 5
305 ./st_O0
```

305 = balance（(50+100)*2=300）+ id（5）。邏輯逆對了。

## 判欄位型別的規則表

從 asm 判型別，靠**存取寬度**（主）+ **號誌/用法**（輔）：

| asm 線索 | 寬度 | 型別提示 |
|---|---|---|
| `movzbl`/`movsbl`、`(%r..)`寫入用 `%al`/`byte` | 1 byte | `char`/`unsigned char`/`bool`/`int8` |
| `movzwl`/`movswl`、`%ax`/`word` | 2 byte | `short`/`unsigned short`/`int16` |
| `mov ...,%eax`（32-bit reg） | 4 byte | `int`/`unsigned`/`float`/`enum` |
| `mov ...,%rax`（64-bit reg） | 8 byte | `long`/指標/`double`/`size_t`/`int64` |
| `movz*`（zero-extend） | — | **unsigned**（無號延伸） |
| `movs*`/`sar`/`cdqe` | — | **signed**（帶號延伸） |
| 值被 `mov (%reg),...` 解參考 | 8 byte | **指標**（不是普通 long！） |
| `movss`/`movsd`/xmm 暫存器 | 4/8 byte | `float`/`double` |

兩個最重要的判別：

- **8-byte 到底是 long 還是指標？** 看它有沒有被**再解參考**。`mov 0x10(%rax),%rax; mov (%rax),...`——`0x10` 那個欄位被拿去當位址讀，它就是指標。純算術用的 8-byte 才是 `long`。
- **4-byte 到底 signed 還是 unsigned？** 看延伸指令：`movzX`/`mov %eax,%eax`（零延伸）=unsigned；`movsX`/`cdqe`/`sar` =signed。上面 `id` 用 `mov %eax,%eax` 零延伸，所以是 `unsigned int`——逆對了。

## 陣列 vs struct：看 offset 是「變數 × 元素大小」還是「固定常數」

這是最容易搞混的區分。兩者都是 base + offset，差別在 offset 怎麼算：

```
   struct：offset 是編譯期固定的「不同常數」
       mov 0x0(%rax), ...     ┐ 不同欄位、不同 offset、
       mov 0x4(%rax), ...     ┤ 固定常數、寬度可以不同
       mov 0x8(%rax), ...     ┘

   陣列：offset 是「index 變數 × 元素大小」，同一種寬度
       mov (%rax,%rcx,4), ... ← base + index*4，同寬度 4
       （%rcx 是跑動的 i，×4 是 sizeof(int)）
```

**beacon**：看到 `(%base,%index,scale)` 這種**帶 index 暫存器和 scale（1/2/4/8）**的定址，就是**陣列**存取，scale 就是元素大小（`,4` = int 陣列，`,8` = 指標或 long 陣列）。看到一堆**不同固定常數 offset、寬度可能不一**的存取，就是 **struct**。

**struct 陣列**（`struct foo arr[]`）兩者疊加：`base + i*sizeof(struct) + 欄位offset`，你會看到 `lea (%base,%index,大常數)` 先算到第 i 個 struct，再 `mov 小常數(%那個),...` 取欄位。大 scale（如 `,%rcx,24` 這種不是 1/2/4/8 的）或先乘一個 struct 大小再加，是 struct 陣列的指紋。

真跑一個 struct 陣列：`struct point{int x,y;}`（8 bytes），`sumx` 遍歷 `arr[i].x`，`gcc -O1`：

```asm
0000000000000000 <sumx>:
    8:   mov    %rdi,%rax             ; p = arr
    e:   lea    0x8(%rdi,%rdx,8),%rcx ; ← end = arr + n*8（元素大小 8 = sizeof(struct point)）
   18:   add    (%rax),%edx           ; s += *(p+0) = p->x（只讀 x@0，沒讀 y@4）
   1a:   add    $0x8,%rax             ; ← p += 8（跳一整個 struct，不是 +4）
   1e:   cmp    %rcx,%rax             ; p != end ?
   21:   jne    18
```

**指紋在步長**：迴圈每輪 `add $0x8,%rax`——步長 8 = **元素是 8-byte 的 struct**（不是 int 陣列，那會 +4）。而且只 `add (%rax)`（讀 offset 0 = `x`），沒碰 offset 4（`y`）——揭露「元素是至少 8 bytes 的 struct，只用了它的第一個欄位」。步長就是 `sizeof(元素)`，是還原陣列元素大小的關鍵。

**巢狀 struct** 真跑：`struct rec{ int id; struct point p; long tag; }`，`tagof` 讀 `r->tag + r->p.y`，`gcc -O1`：

```asm
000000000000002d <tagof>:
   31:  movslq 0x8(%rdi),%rax         ; r->p.y @ +0x8（4-byte，movslq 帶號延伸）
   35:  add    0x10(%rdi),%rax         ; r->tag @ +0x10（8-byte）
   39:  ret
```

佈局：`id`@0（int）、`p.x`@0x4、`p.y`@0x8、`tag`@0x10（8-byte 需 8 對齊，所以 p 後面 tag 落在 0x10）。**內層 struct `p` 的欄位就是外層 base 加更大的固定 offset**（`p.y` = base+0x8）——asm 看不出「這裡有個內層 struct」，你看到的只是一組連續 offset。除非 `lea 0x4(%rdi),%rsi; call ...`（把內層 `p` 的位址單獨傳出去），否則巢狀和攤平等價，靠語意判層次。

## union、巢狀 struct、指標鏈、enum

**union**：同一個 offset 被用**不同寬度/型別**存取，而且看起來互斥（不同程式路徑）。`mov (%rax),%eax`（當 int）在一條路徑、`mov (%rax),%rdx`（當 8-byte 指標）在另一條路徑、同一個 offset——這是 union 的指紋。union 難逆，因為 asm 只反映「這條路徑當它是什麼」，你要靠上下文判斷。

**巢狀 struct**：`struct outer { int a; struct inner b; }`。內層 struct 的欄位表現為「outer base + inner 起始 offset + 內層欄位 offset」，看起來就是一組更大的固定 offset。除非內層 struct 的位址被**單獨取出來傳給別的函式**（`lea 0x10(%rax),%rdi; call ...`），否則你分不出「一個大 struct」和「巢狀 struct」——功能上等價，要靠函式邊界判斷結構層次。

**指標鏈**：`a->b->c` 表現為**連續解參考**：`mov 0x8(%rax),%rax; mov 0x10(%rax),%rax; mov (%rax),...`。每個 `mov off(%rax),%rax` 是「跟著一個指標欄位跳到下一個 struct」。linked list 遍歷（Ch 11）就是這個 pattern 的迴圈版：`mov 0x8(%rax),%rax`（`p = p->next`）在迴圈裡。

**enum**：C 的 enum 底層就是 int，asm 看不到「enum」這個型別。你認出它靠**比較的常數值域**：一個 4-byte 變數被拿去和一組**小而連續的常數**比（`cmp $0x0`、`cmp $0x1`、`cmp $0x2`…或一個 `switch` jump table 覆蓋 0..N），而且這些常數看起來是「狀態/種類」而非數值運算——那它很可能是 enum。Ch 10 的 switch jump table 就常是 enum dispatch。

## 對比與取捨

| 你想還原的 | 主要線索 | 陷阱 |
|---|---|---|
| 欄位 offset | 所有 `base+常數` 存取 | 單一函式只揭露部分欄位，要彙整多函式 |
| 欄位寬度/型別 | 存取寬度 + 延伸號誌 | 8-byte 別預設 long，先查是否被解參考（指標） |
| 陣列 vs struct | 有無 `index*scale` 定址 | struct 陣列是兩者疊加，別當單一 struct |
| signed vs unsigned | `movz`(無號) vs `movs`/`sar`(帶號) | 只讀一次沒延伸時可能判不出，看寫入/運算 |
| struct 總大小 | 最大 offset + 該欄位寬度，向對齊進位 | 尾端 padding 看不到，用 `sizeof` 被編譯期常數化的地方反推（如 malloc 的引數） |

一個實用捷徑：如果程式有 `malloc(sizeof(struct foo))`，那個 `mov $0x18,%edi; call malloc` 的 `0x18`（24）**直接告訴你 struct 大小**——這是 Ch 11 會用的「malloc 引數即 struct 大小」指紋。

## 踩雷集錦

1. **把 8-byte 欄位一律當 `long`**：最常見的型別誤判。base+0x10 是 8-byte，你寫成 `long`，其實它被 `mov 0x10(%rax),%rax; mov (%rax),...` 解參考——是**指標**。錯誤直覺：「8 byte 就是 long」。正確：8-byte 先問「有沒有被當位址用」，有就是指標。

2. **忽略 padding，把 offset 當連續**：看到 `id`@0x0（4-byte）就以為下一個欄位在 0x4——對，`flags` 確實在 0x4，但 `balance` 在 0x8 不是 0x5，因為 8-byte 欄位要 8 對齊，0x5~0x7 是 padding。錯誤直覺：「欄位緊挨著排」。正確：欄位按**對齊**排，中間有洞是正常的，洞的存在本身也是型別線索（後面是需要對齊的大欄位）。

3. **用單一函式的視野下 struct 完整佈局的結論**：`process` 只碰了 id/flags/balance，你就宣稱 struct 只有三個欄位——漏了 `owner`@0x10。錯誤直覺：「這函式沒碰到就是沒有」。正確：完整佈局要**彙整所有碰這個 struct 的函式**（交叉引用）。

4. **把 struct 陣列當成一個巨大 struct**：看到 `base+0`, `base+24`, `base+48` 全是同一種欄位存取——這不是一個有很多欄位的 struct，是 `struct[3]` 每個 24 bytes。錯誤直覺：「不同大 offset = 不同欄位」。正確：offset 呈**等差（元素大小的倍數）**且欄位 pattern 重複 = 陣列。

5. **信反編譯器把 struct 拆成的散 offset**：Ghidra 印 `*(int*)(param+8)`、`*(char*)(param+4)`——它沒認出 struct。你若照抄，永遠看不到資料模型。錯誤直覺：「反編譯器沒給 struct 就是沒 struct」。正確：這些散 offset 正是叫你自己建 struct、套回去讓反編譯器刷新（Ch 8 進階）。

## 進階：再往深一層

- **把還原的 struct 餵回反編譯器**：在 Ghidra/IDA 定義 `struct account`，把參數型別設成 `struct account *`，反編譯器會把所有 `*(long*)(p+8)` 自動變回 `p->balance`——pseudocode 瞬間可讀。這是 struct 還原最有價值的產出，Ch 26 腳本化可批次做。
- **DWARF 對照驗證**：你自己的 ground-truth binary 若帶 `-g`，`objdump --dwarf=info` 或 `pahole`（`apt install dwarves`）能直接印出編譯器記錄的**真實 struct 佈局**。逆完 struct 用它對答案，是 struct 還原的完美 ground-truth 迴圈。真實 stripped binary 沒有 DWARF，但練習時用它驗證你逆對了。
- **C++ 物件的 struct 化**：C++ 的物件在 binary 裡就是帶 vtable 指標的 struct——`+0x0` 通常是 vtable 指標（指向一堆函式指標）。Ch 20 逆 C++ 會把「物件即 struct + vtable@0x0」這個觀念展開。

## 本章重點整理

- struct 還原的核心動作：**收集所有 `base + 固定常數` 存取，同 base 的 offset 集合就是欄位表**；每個 offset 配存取寬度得型別。
- 判型別靠**寬度**（movzbl=1、%eax=4、%rax=8）+ **號誌**（movz=unsigned、movs/sar=signed）+ **用法**（被解參考=指標）。
- **陣列 vs struct**：有 `(%base,%index,scale)` 動態定址=陣列（scale=元素大小）；一組不同固定常數 offset=struct。
- union=同 offset 不同型別存取；巢狀 struct=更大的固定 offset 組；指標鏈=連續解參考；enum=和一組小連續常數比較。
- 完整佈局要**彙整所有碰該 struct 的函式**——單一函式只揭露它用到的欄位。反編譯器把 struct 拆成散 offset，是叫你自己還原，不是沒有結構。

## 自我檢核

- [ ] 給一段有 `mov 0x8(%rax),%rax` / `movzbl 0x4(%rax),%eax` / `mov (%rax),%eax` 的 asm，我能列出欄位表（offset + 寬度 + 型別）
- [ ] 我知道怎麼分辨一個 8-byte 欄位是 `long` 還是指標（看有無被解參考）
- [ ] 我能從 `(%rax,%rcx,4)` 一眼認出這是 int 陣列存取，且元素大小 4
- [ ] 我理解為什麼 `balance` 在 +0x8 而不是 +0x5（對齊 padding）
- [ ] 我知道單一函式逆不出完整 struct，要彙整所有引用
- [ ] 我能用 `pahole` 或 DWARF 對自己的 ground-truth binary 驗證逆出的佈局

## 延伸閱讀

### 書籍

- **《Reverse Engineering for Beginners》** — Dennis Yurichev（[免費](https://beginners.re/)）
  - **定位**：海量「C struct/array ↔ asm」對照，本章的最佳題庫。
  - **讀哪裡**：struct、array、指標相關章節——逐個看它把不同佈局編成什麼定址模式。
- **《The Art of Assembly Language》** — Randall Hyde
  - **定位**：從 asm 角度理解資料結構的記憶體佈局與對齊。
  - **讀哪裡**：composite data types / alignment 章節，補齊「編譯器為什麼這樣排欄位」。

### 工具與文件

- **`pahole`（dwarves 套件）**
  - **這是什麼**：印出 struct 的完整佈局（含 padding/hole），`apt install dwarves` 後對帶 `-g` 的 binary 用。
  - **怎麼用**：`pahole -C account st_O0`——逆完 struct 拿它對答案，完美的 ground-truth 驗證。
- **[System V AMD64 ABI](https://gitlab.com/x86-psABIs/x86-64-ABI)**
  - **讀哪裡**：aggregate types 的對齊與傳遞規則——理解編譯器為什麼把欄位排在那些 offset。
  - **前提**：接你的 [`elf_linking`](../elf_linking/README.md) 與 Ch 4 ABI 基礎。

還原了型別和 struct，你補上了反編譯器最弱的一環。下一章我們攻反編譯器**另一個**弱點、也是逆向者最核心的技能——認出編譯器慣用語（compiler idiom），這是 binary 版的 pattern 辨識。

→ [Ch 10 認出編譯器慣用語（compiler idioms）](./10-compiler-idioms.md)
