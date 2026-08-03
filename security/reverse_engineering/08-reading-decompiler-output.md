# Ch 8 — 讀反編譯器輸出：它的謊言與怎麼騙你

> **目標**：把反編譯器（decompiler）從「按 F5 就有答案的魔法」重新定位成「一個會猜、而且經常猜錯的助手」。看懂它怎麼把 asm 還原成類 C pseudocode，更重要的是看穿它掰出來的東西：猜錯的型別、不存在的變數、被拆散的 struct、還原不了的 idiom、被攤平的 inline。核心紀律一句話——**反編譯器是輔助不是真相，卡住就回讀 asm**。

> **環境**：WSL2 / Linux x86-64，gcc + objdump + radare2（本章的反編譯輸出用 r2 的 `pdc`/`pdf` 真跑；Ghidra/IDA 的等價輸出會標「讀者自行重現」）。

## 為什麼需要這個？

前七章你都在讀 asm。現在你按下 F5（IDA）或打開 Ghidra 的 Decompile 視窗，看到類 C 的 pseudocode，第一反應是「終於有人話了」。這個反應很危險。

反編譯器把幾百行 asm 壓成十幾行像 C 的東西，閱讀效率是碾壓性的——沒有人會想純靠 objdump 逆一個一萬行的 binary。但這個效率是有代價的：**pseudocode 不是原始碼，是反編譯器對原始碼的一個猜測**。asm 是編譯器產出的、確定的事實；pseudocode 是逆向工具事後腦補回去的、可能錯的重建。兩者的可信度差了一整個等級。

Ch 0 的踩雷集錦第 2 條就埋了這個伏筆：反編譯器會「猜錯型別、掰出不存在的變數、把一個 struct 拆成散落的 offset」。這一章把那句話展開，帶你真跑一次同一段 code 的 asm vs pseudocode 對照，親眼看它在哪裡老實、在哪裡開始編故事。學會這件事，你才有資格用反編譯器——因為你會在它騙你的時候把它抓出來。

## 先建立直覺：反編譯器是「反向的編譯器」，而編譯是有損的

編譯是一條單行道，而且**丟資訊**：

```
   你的 source                     編譯器丟掉的東西
  ┌───────────────┐   ─編譯─►    ┌────────────────────┐
  │ 變數名 owner   │              │ 只剩 -0x10(%rbp)    │  名字沒了
  │ struct account│              │ 只剩 base+offset    │  結構沒了
  │ int / char*   │              │ 只剩「存取寬度」     │  型別沒了
  │ x / 2         │              │ shr;add;sar 三條    │  運算意圖沒了
  │ classify()    │              │ 可能被 inline 攤平   │  函式邊界沒了
  └───────────────┘              └────────────────────┘
       意圖                            機器碼（事實）
        │                                  │
        └────────── 反編譯器逆推 ◄──────────┘
              從機器碼「猜」回意圖 ← 猜錯就是 bug
```

反編譯器要做四件事，每一件都是**推斷**、都可能錯：

1. **控制流還原（control-flow recovery）**：把 `jmp`/`jcc`/label 的義大利麵，還原成 `if`/`while`/`for`/`switch`。這件它做得最好——控制流結構在 asm 裡有相對穩定的形狀（Ch 5 講過），還原率高。
2. **型別推斷（type inference）**：從「這個值被當指標解參考」「那個被當 4-byte 存取」反推 `int`/`char*`/`struct*`。這件**經常猜錯**——asm 沒有型別，只有寬度和用法。
3. **變數命名與合併（variable recovery）**：把散落的 stack slot 和暫存器，湊成「一個變數」。這件它**會多切或少切**——一個 source 變數可能被拆成兩個，兩個可能被併成一個。
4. **表達式重建（expression rebuilding）**：把多條 asm 併回一行 C。這件在 idiom 上**會露餡**——遇到 `shr;add;sar` 這種它認得的還原成 `x/2`，認不得的就留成一坨怪運算。

記住這張圖的方向：**asm 在事實那一側，pseudocode 在猜測那一側**。

## 真跑對照一：老實的部分（控制流還原得很好）

先看反編譯器表現好的地方，才知道它好在哪、又在哪開始失控。ground-truth source（`dec.c` 的 `classify`）：

```c
int classify(const char *s){
    int n = strlen(s);
    if (n == 0) return -1;
    int sum = 0;
    for (int i = 0; i < n; i++) sum += (unsigned char)s[i];
    if (sum % 2 == 0) return sum / 2;
    return sum * 3 + 1;
}
```

`gcc -O0` 編、**不 strip**（先給反編譯器最好的條件），r2 的 `pdc`（pseudo-decompile）真跑輸出：

```c
$ r2 -e scr.color=0 -qc "aaa; pdc @ sym.classify" dec_O0
void sym.classify (char *arg1) {
        push (rbp)
        rbp = rsp
        rsp -= 0x20
        qword [s] = rdi // arg1
        rax = qword [s]
        rdi = rax     // const char *s
        sym.imp.strlen () // size_t strlen(0)
        dword [var_4h] = eax
        v = dword [var_4h] - 0
        if (v) goto loc_0x1195 // unlikely
        goto loc_0x0000118e;
    ...
    while (/* 0x000011bf */) {
        eax = dword [var_8h]
        rdx = eax
        rax = qword [s]
        rax += rdx
        eax = byte [rax]
        eax = al
        dword [var_ch] += eax
        dword [var_8h] += 1
    }
    ...
    loc_0x000011df:
        edx = dword [var_ch]
        eax = edx
        eax += eax
        eax += edx
        eax += 1
    ...
}
```

看它做對的地方：

- **`while (...) { ... }` 回來了**：那個 `for (i=0;i<n;i++)` 迴圈被還原成一個 `while` 區塊，`dword [var_8h] += 1` 就是 `i++`。控制流還原成功。
- **`if (v) goto ...` 反映了 `n == 0` 的判斷**：`v = var_4h - 0` 就是把 `n` 和 0 比。
- **`strlen()` 的呼叫認出來了**：因為 dynsym 還在（Ch 11 會細講為什麼 strip 也刪不掉它）。

但注意它**沒做到**的：`eax += eax; eax += edx; eax += 1` 這三行——這是 source 的 `sum * 3 + 1`，r2 的 `pdc` 沒有把它併回 `sum * 3 + 1`，留成三條加法。這是第一個「idiom 還原不了就攤在那」的例子。Ghidra/IDA 通常會把它併成 `sum * 3 + 1`（它們的表達式重建更強），但也不保證——這正是不同工具、不同版本輸出有差的地方。

> Ghidra 對同一個函式的 Hex-Rays/Ghidra 反編譯輸出會更像 C（有 `for` 迴圈、`s[i]`、`return sum * 3 + 1`），但變數名一樣是 `local_c`/`iVar1` 這種自動命名。**這段 Ghidra/IDA 輸出為反編譯器預期輸出，讀者自行以 Ghidra/IDA 重現**——版本不同、命名與型別推斷結果會有出入。

## 真跑對照二：反編譯器開始說謊（型別與變數）

反編譯器最愛騙人的地方是**型別**。看上面那段：第一行 r2 寫的是 `void sym.classify (char *arg1)`——但 source 的回傳型別是 `int`。r2 把回傳型別猜成 `void` 了，因為它從 asm 的呼叫慣例沒推出回傳值被用到。這是型別推斷的典型失手。

型別謊言有幾種常見形態，你要有免疫力：

| 反編譯器寫的 | 實際可能是 | 為什麼會猜錯 |
|---|---|---|
| `int` | `unsigned` / `enum` / `bool` | asm 沒有號誌以外的型別資訊；只能從 `sar` vs `shr`、`setcc` 猜 |
| `int` | 指標 | 4-byte 存取被當 int，但它其實是被截斷的指標低位（少見但致命） |
| `undefined8` / `long` | 指標 / `size_t` / `double` | 8-byte 的東西全長一個樣，靠後續用法才分得出 |
| `void` 回傳 | `int` 回傳 | 呼叫端沒用 `eax` 時，反編譯器推不出有回傳值 |
| `char*` | `struct foo*` | 只要沒解參考成 struct 欄位，指標就退化成 `char*`/`void*` |

**變數謊言**同樣要小心。反編譯器把 stack slot 湊成變數時：

- **會多切**：source 裡的一個 `int x`，如果生命週期被切成兩段（前半用完、後半重用同一個 slot 存別的東西），反編譯器可能給你 `iVar1` 和 `iVar2` 兩個變數。
- **會少切 / 掰出中間變數**：為了讓表達式好看，它會塞出 source 裡根本不存在的臨時變數 `uVar3 = ...; uVar4 = uVar3 + ...`——這些是反編譯器的產物，不是原作者寫的。

**struct 被拆成散 offset** 是同一類謊言裡最常見的。拿一個明確有 struct 的 ground-truth（`st.c` 的 `process`，操作 `struct account`），strip 後 r2 `pdc` 真跑：

```c
$ r2 -e scr.color=0 -qc "aaa; pdc @ fcn.00001189" st_strip
void fcn.00001189 (int64_t arg1) {        // ← 回傳其實是 long，被猜成 void；arg1 其實是 struct account*
        qword [var_8h] = rdi
        rax = qword [var_8h]
        rax = qword [rax + 8]              // ← a->balance，但反編譯器只給你「rax+8 的 8-byte」
        rdx = rax + 0x64                   // + 100
        rax = qword [var_8h]
        qword [rax + 8] = rdx              // 寫回 rax+8
        rax = qword [var_8h]
        eax = byte [rax + 4]               // ← a->flags，被印成「rax+4 的 1-byte」
        eax &= 1
        ...
        eax = dword [rax]                  // ← a->id，「rax+0 的 4-byte」
        rax += rdx                         // balance + id
        return
}
```

反編譯器把一個 `struct account*` 拆成 `[rax+8]`（8-byte）、`[rax+4]`（1-byte）、`[rax]`（4-byte）三個**散落的 offset 存取**——它完全沒認出這是同一個 struct 的三個欄位。這不是 bug，是反編譯器的預設行為：**沒有型別資訊時，它只能把記憶體當成「某個位址加偏移」**。這正是 Ch 9 要教你自己動手還原 struct 的原因——這些散 offset 的規律就是佈局線索，你把 struct 定義餵回去，反編譯器才會把 `[rax+8]` 變回 `a->balance`。

**紀律**：看到 pseudocode 的型別，永遠問一句「這是反編譯器推的，還是我驗證過的？」。要驗證，回去看 asm 的**存取寬度**（`movzbl` = 1 byte、`mov ...,%eax` = 4 byte、`mov ...,%rax` = 8 byte，Ch 6/Ch 9 的手法）和**用法**（有沒有被解參考當指標）。asm 是事實，pseudocode 是意見。

## 真跑對照三：strip 之後，連名字都是假的

上面用的是**沒 strip** 的 binary，所以還有 `sym.classify`、`strlen` 這些名字。真實世界你拿到的是 stripped binary。同一份 code strip 掉，r2 分析後函式就變成 `fcn.000011e9` 這種位址名——**反編譯器連函式名都是它自己編的**（見本課 Ch 11 與練習 A）。

更要命的是 `-O2` 之後的**inline 攤平**。看 `dec_O2` strip 版的 `main`，r2 真跑 `pdc`：

```c
$ r2 -e scr.color=0 -qc "aaa; pdc @ main" dec_O2_strip
int main (signed int argc, char **argv) {
        v = edi - 1   // argc
        if (v <= 0) goto loc_0x10b2 // likely
        return rax;
    loc_0x000010b2:
        eax = 2
        return 2
}
```

`classify` 去哪了？在 `-O2`，編譯器把它**inline 進 `main`、又發現 `main` 根本沒用到結果**（原始 `main` 只在 `argc>=2` 時 `printf` 它，但這個精簡版把整條算完的邏輯優化掉了），結果 `main` 縮成「檢查 argc、回傳」。原本清楚的 `classify` 函式邊界在 pseudocode 裡**完全消失**——這不是反編譯器的錯，是編譯器 inline 的結果，但後果一樣：你按 F5 想看 `classify`，發現它不存在，它的邏輯被攤平、揉進、甚至優化掉了。

這裡有個關鍵教訓：**pseudocode 越乾淨，不代表你越了解程式——可能只是編譯器把邏輯優化沒了。** 上面那個 O2 `main` 讀起來清爽（就四行），但它清爽是因為 `classify` 的計算被判定為「結果沒被用到」而整段刪除。一個逆向新手可能看著這段 pseudocode 下結論「這程式什麼都沒做」，完全錯過原本的 `classify` 邏輯——它還在 binary 別處（獨立函式版），只是不在 `main` 的 pseudocode 裡。**pseudocode 的乾淨是編譯器的視角，不是程式的全貌。**

對照 `dec_O2`（不 strip）的 `classify` objdump，看它在 O2 下本體長什麼樣（這是真的 asm、事實）：

```asm
$ objdump -d -M att --no-show-raw-insn dec_O2
00000000000011b0 <classify>:
    11b0:  endbr64
    11b5:  mov    %rdi,%rbx
    11b8:  call   1060 <strlen@plt>
    11bd:  test   %eax,%eax
    11bf:  je     1208 <classify+0x58>   ; n==0 → return -1
    11c1:  jle    1200 <classify+0x50>
    ...
    11d0:  movzbl (%rdi),%edx            ; ┐ sum += (unsigned char)s[i]
    11d3:  add    $0x1,%rdi              ; ┤ 迴圈：逐 byte 加
    11d7:  add    %edx,%eax              ; ┤
    11d9:  cmp    %rdi,%rcx              ; ┤
    11dc:  jne    11d0 <classify+0x20>   ; ┘
    11de:  lea    0x1(%rax,%rax,2),%r8d  ; sum*3+1（lea 一條搞定，Ch 10 講）
    11e3:  test   $0x1,%al               ; sum & 1（判奇偶）
    11e5:  je     11f0 <classify+0x40>
    11e7:  mov    %r8d,%eax              ; 奇：回 sum*3+1
    ...
    11f0:  sar    %eax                   ; 偶：回 sum/2（sar，Ch 0 的 idiom）
```

在 O2 這個獨立的 `classify` 還在（因為它同時被非 inline 路徑呼叫），但 `main` 裡那份被 inline 掉了。**同一個函式在同一個 binary 裡可以同時「以獨立函式存在」和「被 inline 進呼叫者」——反編譯器看到的是後者的攤平版。** 這是逆 release binary 最常見的困惑來源。

## 底層機制：反編譯器怎麼從 asm 蓋回 pseudocode

知道它怎麼運作，才知道它會在哪裡失手。反編譯器內部大致是一條和編譯器對稱的 pipeline：

```
  機器碼 bytes
     │ 反組譯（disassembly）── objdump 也做到這
     ▼
  asm 指令串
     │ lifting：把 asm 抬升成中間表示（IR / p-code / microcode）
     ▼
  低階 IR（每條指令 → 幾個 IR 微操作）
     │ 資料流分析：定義-使用鏈、暫存器/stack slot → 變數
     │ 控制流分析：支配樹、迴圈偵測 → if/while/for（結構化）
     │ 型別推斷：從用法約束推 int/ptr/struct
     │ 表達式傳播、死碼消除、idiom 匹配
     ▼
  高階 IR
     │ 輸出成類 C
     ▼
  pseudocode（← 你按 F5 看到的）
```

- **控制流結構化**靠支配樹（dominator tree）和迴圈偵測——這套理論成熟，所以 `if`/`while` 還原可靠。遇到編譯器產生的 `goto` 義大利麵（如 `-O2` 的多出口、共用 epilogue），它可能還原不出乾淨結構，留一堆 `goto label`（你在上面 O2 的 pdc 裡看到的 `goto loc_...` 就是）。
- **型別推斷**是一組約束求解：「x 被 `movzbl` 讀 → x 至少 1 byte」「x 被 `call ...; ` 當 `rdi` → x 可能是指標」。約束不夠時就退化成保守猜測（`int`/`undefined8`）。**約束越少，猜得越爛**——這就是為什麼 strip + 高優化的 binary 反編譯品質差。
- **idiom 匹配**是一組硬編碼的 pattern：認得 `shr 31;add;sar` → 印 `x/2`；認得魔數乘法 → 印除法（Ch 10 全講）。認不得的 pattern 就原封不動抬上來，變成 pseudocode 裡那坨怪運算。**反編譯器的 idiom 庫有多大，決定它能還原多漂亮**——但它的庫永遠有邊界，你的 idiom 庫（本課要你建的卡片）要能補它的洞。

一句話：**反編譯器是把「事實（asm）」跑一遍推斷 pipeline 得到「意見（pseudocode）」。推斷用的資訊在 strip/優化後越來越少，意見就越來越不可信。**

## 對比與取捨：什麼時候信 pseudocode，什麼時候回讀 asm

| 情境 | 信 pseudocode 的程度 | 該做什麼 |
|---|---|---|
| 掌握整體控制流、函式在幹嘛 | 高 | 直接讀 pseudocode，效率取向 |
| 判斷一個變數的**精確型別/寬度** | 低 | 回 asm 看存取寬度與解參考 |
| 逆一個**演算法/檢查邏輯**的精確語意 | 中 | pseudocode 起手，關鍵運算回 asm 驗證 |
| 逆 `-O2`/`-O3` 且被 **inline/向量化** | 低 | asm 為主，pseudocode 當地圖 |
| struct 佈局還原 | 低 | 反編譯器常把 struct 拆成散 offset，回 asm 自己重建（Ch 9） |
| 快速掃一個 1 萬行 binary 找目標函式 | 高（作為導航） | pseudocode 導航，鎖定目標再深入 asm |

原則：**pseudocode 用來「導航」和「建立假設」，asm 用來「確認事實」。** 這正是姊妹課 [`reading_code`](../../soft_skills/reading_code/README.md) 的假設驅動 SOP 在 binary 世界的版本——pseudocode 給你假設，asm 是你驗證假設的 ground truth。

## 踩雷集錦

1. **把 pseudocode 當原始碼讀**：最致命。你以為 `iVar1 = uVar2 * 3 + 1;` 是作者寫的，其實 `iVar1`/`uVar2` 是反編譯器編的名、型別也是它猜的。錯誤直覺：「反編譯出來就是原本的 code」。正確：pseudocode 是反編譯器對原 code 的一份**有損、會錯的重建**。

2. **迷信型別，尤其 `int` 和指標**：反編譯器把一個其實是指標的東西標成 `int`，你順著它推理，整條資料流全錯。看到關鍵型別，回 asm 看它有沒有被 `mov (%rax),...` 解參考——會解參考的就是指標，不管反編譯器標什麼。

3. **信任它切出來的變數**：反編譯器把一個 source 變數拆成 `iVar1`/`iVar2`（因為 slot 被重用），你以為是兩個東西；或掰出 `uVar3` 這種不存在的中間變數。錯誤直覺：「pseudocode 有幾個變數，原碼就有幾個」。正確：變數邊界是反編譯器**湊**的，不可盡信。

4. **看到 struct 被拆成散 offset 就放棄**：反編譯器經常把 `a->balance` 印成 `*(long *)(param_1 + 8)`——它沒認出這是 struct 欄位。這不是死路，是叫你自己動手還原 struct（Ch 9 專講）。錯誤直覺：「它拆成 offset 代表這裡沒結構」。正確：offset 存取的規律就是 struct 佈局的線索。

5. **`-O2` 找不到函式就以為看錯了**：你想看 `classify`，pseudocode 裡沒有——不是你眼花，是它被 inline 攤平進呼叫者了（上面真跑過）。錯誤直覺：「函式一定以函式形式存在」。正確：inline 會讓 source 的函式在 binary 裡消失或散進多處。

## 進階：再往深一層

- **調反編譯器的型別，讓它重算**：Ghidra/IDA 允許你手動把一個變數的型別改成 `struct account *`、把回傳型別從 `void` 改成 `int`，反編譯器會**重新推斷並刷新** pseudocode，把 `*(long*)(p+8)` 變回 `p->balance`。這是反編譯器最強的互動用法——你餵它正確約束，它回報更好的重建。把它想成「你和反編譯器協作求解」：它先給一版猜測，你用 asm 驗證出的事實（這是指標、那是 struct）回饋，它重算出更接近原意的版本。反覆幾輪，pseudocode 從「一坨散 offset」變成「乾淨的 struct 存取」。Ch 9 的 struct 還原、Ch 26 的腳本化會用到。
- **對照 `pdc` vs `pdg`**：r2 的 `pdc` 是輕量 pseudo-disassembly（貼近 asm），`pdg`（需 r2ghidra 外掛）是接 Ghidra 反編譯引擎、輸出更像 C。裝了 r2ghidra 可以 `pdg @ fcn` 得到更高階的輸出——但更高階 = 更多推斷 = 更多可能的謊言，取捨依舊。
- **反編譯器彼此打架**：同一個函式，Ghidra、Hex-Rays、r2 的輸出可以差很多（變數名、型別、迴圈形態）。老手會**交叉比對**：三個工具都同意的部分較可信，只有一個工具這麼寫的部分要回 asm 查。Ch 28（二進位相似度）會延伸這個「多來源交叉驗證」的思路。

## 本章重點整理

- 反編譯器把 asm 跑一條「反向編譯」pipeline（lifting → 資料流/控制流分析 → 型別推斷 → 表達式重建）得到 pseudocode。**pseudocode 是推斷出來的意見，asm 是編譯器產出的事實。**
- 它做得好的：**控制流還原**（if/while/for 可靠）、認得的 **idiom** 還原、有 dynsym 的 **libc 呼叫**。
- 它會騙你的：**型別**（int/指標/enum/void 回傳常猜錯）、**變數邊界**（多切、少切、掰中間變數）、**struct**（拆成散 offset）、**認不得的 idiom**（留成怪運算）、**inline**（把函式攤平甚至消失）。
- 核心紀律：**pseudocode 用來導航與建假設，asm 用來確認事實。卡住、存疑、關鍵語意——一律回讀 asm。**
- 越 strip、越高優化，反編譯器可用的推斷資訊越少，輸出越不可信——真實 release binary 正是這種最難的情況。

## 自我檢核

- [ ] 我能說出反編譯器 pipeline 的四個推斷步驟，並指出哪一步最容易出錯（型別推斷）
- [ ] 我知道為什麼「控制流還原」比「型別推斷」可靠
- [ ] 給一個標成 `int` 的變數，我知道怎麼回 asm 判斷它其實是不是指標（看有沒有被解參考、存取寬度）
- [ ] 我理解為什麼 `-O2` 下想看的函式可能在 pseudocode 裡消失（inline 攤平）
- [ ] 我能複述紀律：pseudocode 導航、asm 確認，卡住回讀 asm
- [ ] 我知道同一函式在 Ghidra/IDA/r2 的輸出會不同，且該交叉比對

## 延伸閱讀

### 書籍

- **《The Ghidra Book》** — Chris Eagle & Kara Nance（No Starch, 2020）
  - **定位**：Ghidra 反編譯器的權威書；本章「反編譯器怎麼運作」的深化。
  - **讀哪幾章**：講 decompiler 與 p-code 的章節（反編譯 pipeline 的內部）、以及手動修型別/struct 讓反編譯刷新的操作章。
- **《Practical Binary Analysis》** — Dennis Andriesse（No Starch, 2019）
  - **定位**：disassembly 與 lifting 的原理，理解反編譯器上游做了什麼。
  - **讀哪裡**：反組譯原理與 IR/lifting 相關章節（對應本章的 pipeline 前半）。

### 工具與文件

- **[Hex-Rays Decompiler 官方說明](https://hex-rays.com/decompiler/)**
  - **這是什麼**：業界標準反編譯器的官方文件；理解 F5 背後的推斷，以及怎麼手動修正型別讓它重算。
  - **前提**：接你的 [`ida_pro`](../ida_pro/README.md) 課。
- **[radare2 / r2ghidra](https://github.com/radareorg/radare2) + `pdc`/`pdg`**
  - **怎麼用**：`r2 -qc "aaa; pdc @ fcn" 檔案` 看輕量反編譯；裝 r2ghidra 後 `pdg` 接 Ghidra 引擎。本章所有 pseudocode 對照即用 r2 真跑。
- **[Compiler Explorer (godbolt.org)](https://godbolt.org/)**
  - **這是什麼**：反查神器——pseudocode 看不懂時，猜一個 source 貼進去，看它編出的 asm 是否吻合你手上的 asm，用「正向編譯」驗證你的「反向重建」。

反編譯器的謊言裡，型別和 struct 是最需要你親手還原的。下一章我們把「從 offset 存取逆出 struct 佈局」這件事做成一套可操作的方法。

→ [Ch 9 型別與結構還原](./09-type-and-struct-recovery.md)
