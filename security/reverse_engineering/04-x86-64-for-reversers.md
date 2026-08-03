# Ch 4 — x86-64 asm 逆向者視角

> **目標**：把 x86-64 組合語言（assembly）建立到「**認得出**」的程度——不是要你會寫 asm，是要你看到一段反組譯（disassembly）能立刻分辨「這是在傳參數」「這是 stack frame 的 prologue」「這是回傳值」。逆向者的 asm 標準遠低於編譯器工程師：你只需要認識一小撮指令族、一套呼叫慣例（calling convention）、一個 stack frame 長相。這一章把那個「最小夠用集」釘死，後面三章（控制流、資料、函式）全部站在它上面。

> **環境**：WSL2 / Linux x86-64，gcc 11.4 + objdump。本章所有 asm 都是 `gcc -O0`（可讀對照）與 `gcc -O2`（真實樣貌）真跑 `objdump -d` 的輸出，未經修飾。

## 為什麼需要這個？

你在 [Ch 0](./00-environment-and-ground-truth-loop.md) 已經看過一次：一段你一眼看懂的 C，編譯後變成暫存器、位移、跳轉。那時你是「看熱鬧」；從這章開始你要「看門道」。

問題是 x86-64 的指令集手冊有上千頁、幾百條指令、無數定址模式。新手最常見的災難，是想「先把 asm 學會再逆向」——結果卡在 SSE 指令、AVX、對齊規則這些跟你的目標八竿子打不著的細節裡，永遠開不了工。

逆向者的 asm 觀是**倒過來的**：你不是要產生正確的機器碼，你是要**從機器碼反推意圖**。編譯器只會用一小撮指令來表達 C 的常見結構（傳參、算術、比較、跳轉、存取記憶體）。這一小撮，加上一套呼叫慣例，涵蓋你在 benign C binary 裡會遇到的九成。剩下的一成（SIMD、原子操作、特權指令）等你真的撞到再查。

> 這正是姊妹課 [`reading_code`](../../soft_skills/reading_code/README.md) 的「偵察」精神在 binary 世界的版本：先建立地圖與詞彙，別一頭栽進細節。逆向者的「詞彙」就是這一章的暫存器、指令族、呼叫慣例。

## 先建立直覺：一台只有暫存器和一塊記憶體的機器

在看指令之前，先在腦中裝好這台機器的樣子。x86-64 CPU 執行 code 時，眼裡只有兩種東西：

```
   ┌─────────────────────────────┐         ┌──────────────────┐
   │  暫存器 (registers)         │         │   記憶體 (RAM)    │ 高位址
   │  ─ CPU 內建、極快、數量有限 │         │  ┌────────────┐  │
   │                             │◄───────►│  │  stack     │  │ ← rsp 指這
   │  rax rbx rcx rdx  (通用)    │  mov     │  ├────────────┤  │
   │  rsi rdi rbp rsp            │  存取    │  │  heap      │  │
   │  r8  r9 ... r15            │          │  ├────────────┤  │
   │  rip (下一條指令位址)       │          │  │ .data/.bss │  │
   │  eflags (旗標)             │          │  │ .text 你逆 │  │ ← rip 指這附近
   └─────────────────────────────┘         │  └────────────┘  │ 低位址
                                            └──────────────────┘
```

一切運算都在暫存器裡做。記憶體慢，所以資料要「搬進」暫存器（`mov ... , %reg`）、算完再「搬回去」（`mov %reg, ...`）。逆向時你的工作，很大一部分就是**追蹤某個值從哪個記憶體位置搬進哪個暫存器、算了什麼、又搬回哪裡**——這就是重建資料流（data flow）。

## 暫存器：逆向者要認的 16+2 個

x86-64 有 16 個 64-bit 通用暫存器。逆向時你不需要背它們的歷史包袱，只需要知道**每個暫存器在呼叫慣例裡扮演什麼角色**（下一節細講），以及**它們的子暫存器名字**——因為同一個暫存器，用 64/32/16/8 bit 存取時名字不同，這是新手最容易看花眼的地方。

| 64-bit | 32-bit | 16-bit | 8-bit | 逆向時的典型角色 |
|---|---|---|---|---|
| `rax` | `eax` | `ax` | `al` | **回傳值**；除法的被除數 |
| `rdi` | `edi` | `di` | `dil` | **第 1 個參數** |
| `rsi` | `esi` | `si` | `sil` | **第 2 個參數** |
| `rdx` | `edx` | `dx` | `dl` | **第 3 個參數** |
| `rcx` | `ecx` | `cx` | `cl` | **第 4 個參數** |
| `r8`  | `r8d`  | `r8w` | `r8b` | **第 5 個參數** |
| `r9`  | `r9d`  | `r9w` | `r9b` | **第 6 個參數** |
| `rbx` | `ebx` | `bx` | `bl` | callee-saved 暫存值 |
| `rbp` | `ebp` | `bp` | `bpl` | frame pointer（-O0 時）|
| `rsp` | `esp` | `sp` | `spl` | **stack pointer**（永遠指堆疊頂）|
| `r10`–`r15` | `r10d`… | … | … | 暫存 / callee-saved |
| `rip` | — | — | — | **指令指標**，指向下一條指令 |

**關鍵認知一**：`eax` 不是另一個暫存器，它是 `rax` 的低 32 bit。你會在同一段 code 裡看到 `mov %edi, -0x4(%rbp)`（存 32-bit 的參數）然後 `mov -0x4(%rbp), %rax`（讀回 64-bit）——這通常代表原始碼裡是個 `int`（32-bit）後來被提升成指標運算或 `long`。子暫存器名字本身就是型別線索。

**關鍵認知二**：寫 32-bit 子暫存器（如 `mov $0x5, %eax`）會**把上面 32 bit 清零**。所以你常看到編譯器用 `xor %eax, %eax` 來把整個 `rax` 歸零——比 `mov $0, %rax` 短。看到 `xor %reg, %reg` 別以為在做 XOR 運算，那是「把這個暫存器清成 0」的慣用語。

`rip`（instruction pointer）你不能直接讀寫，但它在逆向裡極重要：全域變數與字串是用「相對 `rip` 的位移」定址的（Ch 6 細講），你會一直看到 `lea 0x2e22(%rip), %rax` 這種東西。

## AT&T vs Intel：先分清楚你在讀哪一種

同一條指令，兩種語法寫法不同，這是逆向新手第一個大坑。`objdump` **預設 AT&T**，IDA/Ghidra/Windows 世界預設 Intel。

| 差異點 | AT&T（objdump 預設） | Intel（IDA/Ghidra）|
|---|---|---|
| 運算元順序 | `src, dst`（來源在前）| `dst, src`（目的在前）|
| 暫存器前綴 | `%rax` | `rax` |
| 立即數前綴 | `$0x5` | `0x5` |
| 記憶體定址 | `-0x4(%rbp)` | `[rbp-0x4]` |
| 指標大小 | 靠指令後綴 `movl`/`movq` | 明寫 `DWORD PTR` |

同一條「把 edi 存到 rbp-0x14」：

```
AT&T   (objdump 預設):  mov    %edi,-0x14(%rbp)
Intel  (objdump -M intel): mov    DWORD PTR [rbp-0x14],edi
```

注意方向整個顛倒了。**這是逆向者最容易讀反的地方**：在 AT&T 看到 `mov %eax, %ebx` 是「eax 搬進 ebx」，在 Intel 看到 `mov eax, ebx` 是「ebx 搬進 eax」。本課全程用 objdump 預設的 AT&T（因為它可重現、免額外參數），但你只要 `objdump -d -M intel` 就能切成 Intel。**看 asm 前先確認語法**，不然你會把每個資料流都追反。

## 逆向者只需認的指令族

你不用背指令表。把指令按「它想幹嘛」分成幾族，認族就夠：

| 族 | 代表指令 | 逆向時讀成 |
|---|---|---|
| **搬移** | `mov` / `movzx` / `movsx` / `lea` | 賦值、載入、存回；`lea` 常被拿來算地址或做乘加 |
| **算術** | `add` `sub` `imul` `neg` `inc` `dec` | 加減乘、取負 |
| **位元** | `and` `or` `xor` `shl` `shr` `sar` `not` | 位元運算；也常是「除以 2 的冪」「取餘」的偽裝 |
| **比較** | `cmp` `test` | 設 eflags 給後面的條件跳轉用（Ch 5 主角）|
| **跳轉** | `jmp` `je` `jne` `jl` `jg` `jle`… | 控制流（Ch 5 主角）|
| **函式** | `call` `ret` `push` `pop` `leave` | 呼叫、返回、進出 frame（Ch 7 主角）|

三個現在就要記住的細節：

1. **`lea` 不存取記憶體**。`lea (%rdi,%rdi,4), %eax` 算的是「rdi + rdi*4 = rdi*5」這個**數值**，存進 eax——編譯器超愛用它做乘法和加常數，因為它一條抵好幾條、又不動 eflags。看到 `lea` 別急著當「載入」，先看它算的是不是一個算術式。（真跑對照見下節。）
2. **`test %eax, %eax`** 是「檢查 eax 是不是 0」的慣用語（它做 `eax & eax` 只為了設旗標）。`test %al, %al` 同理檢查一個 bool/char。
3. **`xor %reg, %reg`** = 把 reg 清零（前面說過）。

### 真跑：`lea` 當算術用

寫一段 C，讓它做「乘以 5」和「陣列索引」，看編譯器怎麼用 `lea`（`gcc -O2 -c`）：

```c
int  f(int x){ return x * 5; }
long g(long *p, long i){ return p[i]; }
```

真跑 `objdump -d`：

```asm
0000000000000000 <f>:
   0:	endbr64
   4:	lea    (%rdi,%rdi,4),%eax    ; eax = rdi + rdi*4 = x*5，一條搞定
   7:	ret

0000000000000010 <g>:
  10:	endbr64
  14:	mov    (%rdi,%rsi,8),%rax    ; rax = *(rdi + rsi*8) = p[i]（long 是 8 bytes）
  18:	ret
```

`f` 裡沒有 `imul`——編譯器把 `x*5` 變成 `lea (%rdi,%rdi,4)`。這是你在 [Ch 0](./00-environment-and-ground-truth-loop.md) 看過的 strength reduction 的一種：認得出 `lea (%r,%r,4)` = `×5`、`lea (%r,%r,2)` = `×3`，逆向就快。`g` 的 `(%rdi,%rsi,8)` 是**base + index×scale** 定址，`scale=8` 直接告訴你元素是 8 bytes——這是 Ch 6 認陣列的核心。

## 底層機制：System V AMD64 呼叫慣例

這是本章最重要的一節。**呼叫慣例（calling convention）決定了「參數怎麼傳、回傳值放哪、誰負責保存暫存器」**。Linux/x86-64 用的是 **System V AMD64 ABI**。認得它，你就能在完全沒有符號的 binary 裡，看一個函式**用了哪些暫存器**就反推出它**收幾個參數、回傳什麼**——這是逆向函式簽章（signature）的地基（Ch 7 深入）。

規則：

```
   整數/指標參數，依序放進：
   ┌─────┬─────┬─────┬─────┬─────┬─────┐
   │ rdi │ rsi │ rdx │ rcx │ r8  │ r9  │   第 1~6 個參數
   └─────┴─────┴─────┴─────┴─────┴─────┘
   第 7 個以後 → 壓 stack（由右往左 push）

   回傳值        → rax（64-bit）/ eax（32-bit）
   浮點參數/回傳  → xmm0~xmm7 / xmm0（本章不深入）
```

**caller-saved vs callee-saved**（誰負責在呼叫前後保住暫存器的值）：

- **caller-saved（volatile）**：`rax, rcx, rdx, rsi, rdi, r8-r11`。呼叫別人前，如果 caller 還要用這些值，得自己先存起來——因為被呼叫者可以隨便改。
- **callee-saved（non-volatile）**：`rbx, rbp, r12-r15`。被呼叫者若要用，得先 `push` 存、返回前 `pop` 還。**所以你在函式開頭看到 `push %rbx` / `push %r12`，那是「這個函式打算用這些 callee-saved 暫存器，先幫 caller 保管好」的訊號**——順便告訴你這函式內部有跨呼叫存活的變數。
- **紅區（red zone）**：`rsp` 以下 128 bytes 是「保證不被中斷/信號踩」的暫存區。葉函式（leaf function，自己不再呼叫別人的函式）可以直接用這塊而**不必調整 rsp**——這就是為什麼你會看到有些小函式沒有 `sub $N, %rsp` 卻還在用 stack。看到「用了 `-0x8(%rsp)` 卻沒動 rsp」別困惑，那是紅區。

### 真跑：一個帶 6 個參數的函式，看參數怎麼進暫存器

這是本章的 ground-truth 核心。寫一個吃滿 6 個整數參數的函式：

```c
int add6(int a, int b, int c, int d, int e, int f){
    int s = a + b + c;
    int t = d + e + f;
    return s + t;
}
int main(void){
    int r = add6(1, 2, 3, 4, 5, 6);
    printf("%d\n", r);
    return 0;
}
```

先看 **`-O0` 的 `main`**，也就是**呼叫端**怎麼擺參數（真跑）：

```asm
000000000000118f <main>:
    118f:	endbr64
    1193:	push   %rbp
    1194:	mov    %rsp,%rbp
    1197:	sub    $0x10,%rsp
    119b:	mov    $0x6,%r9d          ; 第 6 個參數 f=6 → r9d
    11a1:	mov    $0x5,%r8d          ; 第 5 個參數 e=5 → r8d
    11a7:	mov    $0x4,%ecx          ; 第 4 個參數 d=4 → ecx
    11ac:	mov    $0x3,%edx          ; 第 3 個參數 c=3 → edx
    11b1:	mov    $0x2,%esi          ; 第 2 個參數 b=2 → esi
    11b6:	mov    $0x1,%edi          ; 第 1 個參數 a=1 → edi
    11bb:	call   1149 <add6>        ; 呼叫
    11c0:	mov    %eax,-0x4(%rbp)    ; 回傳值在 eax，存回 r
```

一行一行對得上：`rdi=1, rsi=2, rdx=3, rcx=4, r8=5, r9=6`。這就是呼叫慣例的活教材——**參數的順序在 asm 裡是靠暫存器編號決定的，不是靠位置**。（順帶一提：編譯器用 32-bit 的 `edi/esi/...` 因為參數是 `int`；若是指標會用 64-bit 的 `rdi/rsi/...`——又一個型別線索。）

再看**被呼叫端 `add6`**（`-O0`）怎麼收參數（真跑）：

```asm
0000000000001149 <add6>:
    1149:	endbr64
    114d:	push   %rbp              ; ┐ prologue
    114e:	mov    %rsp,%rbp         ; ┘ 建 frame
    1151:	mov    %edi,-0x14(%rbp)  ; a 存進 stack slot
    1154:	mov    %esi,-0x18(%rbp)  ; b
    1157:	mov    %edx,-0x1c(%rbp)  ; c
    115a:	mov    %ecx,-0x20(%rbp)  ; d
    115d:	mov    %r8d,-0x24(%rbp)  ; e
    1161:	mov    %r9d,-0x28(%rbp)  ; f
    1165:	mov    -0x14(%rbp),%edx  ; ┐
    1168:	mov    -0x18(%rbp),%eax  ; │ s = a + b + c
    116b:	add    %eax,%edx         ; │
    ...（略）
    118d:	pop    %rbp
    118e:	ret                      ; 回傳值已在 eax
```

`-O0` 有個一致的模式：**函式一進來就把 6 個入參暫存器全 spill（溢出）到 stack slot**，之後只用 stack 上的副本運算。這讓 `-O0` 的 code 又臭又長但**極好讀**——每個變數都有固定的 `rbp-0xNN` 位址（Ch 6 認資料靠這個）。

### 對照 `-O2`：呼叫慣例不變，但一切都被壓縮

同一份 source，`gcc -O2` 的 `add6`（真跑）：

```asm
0000000000001180 <add6>:
    1180:	endbr64
    1184:	add    %esi,%edi         ; edi += esi   (a+b)
    1186:	add    %r8d,%ecx         ; ecx += r8d   (d+e)
    1189:	add    %edx,%edi         ; edi += edx   (a+b+c)
    118b:	add    %r9d,%ecx         ; ecx += r9d   (d+e+f)
    118e:	lea    (%rdi,%rcx,1),%eax ; eax = edi + ecx = 全部加總
    1191:	ret
```

同一個函式從 25 條變成 6 條。注意：

- **沒有 prologue**（`push %rbp`）、**沒有 stack slot**：`add6` 是葉函式，參數直接在暫存器裡算完，用不到 stack。這就是紅區精神的極致——連紅區都不用。
- **參數暫存器不變**：一樣是 `rdi/rsi/rdx/rcx/r8/r9`。**呼叫慣例是 ABI，不受優化等級影響**——這是它作為逆向錨點如此可靠的原因。
- 最後那條 `lea (%rdi,%rcx,1)` 又是 `lea` 當「純加法」用（scale=1），把兩個部分和相加放進 eax（回傳值）。

**這就是逆向的核心對照**：`-O0` 給你一個囉嗦但和 source 幾乎一一對應的版本（教學、對答案用）；`-O2` 給你真實 release binary 的樣貌——參數還在老位置，但變數、frame、指令全被壓成最精簡的形式。認得出「這 6 條在算 `add6` 的 body」，靠的就是「參數暫存器 + `lea` 加法」這些 pattern。

## 對比與取捨

| 面向 | `-O0`（可讀對照） | `-O2`（真實樣貌） |
|---|---|---|
| 參數 | spill 到 `rbp-0xNN` stack slot | 盡量留在暫存器裡直接算 |
| stack frame | 必有 `push %rbp; mov %rsp,%rbp` | 葉函式常完全省略 |
| 變數 | 每個都有固定 stack 位址 | 可能只活在暫存器、甚至被消掉 |
| 指令數 | 多、囉嗦、好追 | 少、緊、需認 pattern |
| 逆向難度 | 低，適合對答案 | 高，是真實目標 |
| 呼叫慣例 | 一致（rdi/rsi/.../rax） | 一致（不受優化影響）|

用法：練認 pattern 時先看 `-O0` 建立對應，再看 `-O2` 學它被壓成什麼樣。真實目標永遠假設是 `-O2` 起跳。

## 踩雷集錦

1. **把 AT&T 讀成 Intel（方向反了）**：`mov %eax, %ebx` 在 AT&T 是「eax→ebx」。若你習慣 IDA 的 Intel，會在腦中讀成「ebx→eax」，於是每個資料流都追反、整個邏輯還原錯。**看 asm 前先確認語法**，objdump 沒加 `-M intel` 就是 AT&T。
2. **以為 `eax` 和 `rax` 是兩個暫存器**：它們是同一個的不同寬度視圖。寫 `eax` 會清掉 `rax` 高 32 bit。追值時要把 `al/ax/eax/rax` 當同一條線追。
3. **把 `lea` 當成記憶體載入**：`lea (%rdi,%rdi,4), %eax` 完全不碰記憶體，它算的是 `rdi*5` 這個數。看到 `lea` 先問「它在算地址還是在偽裝算術」。
4. **看到 `xor %eax,%eax` 以為在做加密/XOR**：那是「eax = 0」的慣用語，編譯器用它清零因為比 `mov $0,%eax` 短。同理 `test %eax,%eax` 是「檢查是否為 0」不是普通位元運算。
5. **假設參數一定 spill 到 stack**：那是 `-O0` 的習慣。`-O2` 下參數常一輩子待在暫存器裡，你在 stack 上找不到它別以為函式沒收參數——去暫存器裡找。
6. **忽略子暫存器寬度的型別線索**：用 `edi`（32-bit）通常代表 `int`，用 `rdi`（64-bit）通常代表指標或 `long`。把這個當免費的型別提示，Ch 9 還原型別會用到。

## 進階：再往深一層

- **`movzx` vs `movsx`**：`movzx`（zero-extend）把窄值補零拉寬，對應 `unsigned` 提升；`movsx`（AT&T 寫 `movslq` 等）補符號位，對應 `signed` 提升。你在 Ch 6 的 struct 例子裡會看到 `movslq (%rdi), %rax`——那是把一個 `int` 欄位補號拉成 64-bit。這個帶不帶符號的區別，是還原型別 signedness 的直接證據。
- **eflags 與條件碼**：`cmp`/`test` 設定 ZF/SF/OF/CF 等旗標，`je/jl/jg...` 根據旗標組合跳。你不必背旗標怎麼算，但要知道「`cmp a,b` 後接 `jl` = 若 b<a 則跳」——Ch 5 會把每個條件跳轉對回它的 C 條件。
- **`endbr64` 是什麼**：每個函式開頭那條 `endbr64` 是 Intel CET（控制流強制技術）的 landing pad，防 ROP。逆向時它就是**函式入口的可靠標記**，Ch 7 認函式邊界會用到。它不影響邏輯，當作「函式從這裡開始」的路標。
- **想反查任意 pattern**：把一小段 C 貼上 [Compiler Explorer](https://godbolt.org/)，選 gcc/clang + 優化等級，右邊即時出 asm。想確認「這條怪 asm 對應什麼 C」，反著試。本課全程可拿它當 ground-truth 的快速版。

## 本章重點整理

- 逆向者的 asm 標準是「認得出」不是「寫得出」：認一小撮指令族 + 一套呼叫慣例 + 一個 stack frame 長相就能開工。
- 暫存器有 64/32/16/8-bit 的子視圖（rax/eax/ax/al 是同一個）；寫 32-bit 子暫存器會清高 32 bit；子暫存器寬度是型別線索。
- **objdump 預設 AT&T（src,dst）**，IDA/Ghidra 預設 Intel（dst,src）——看反了整個資料流就追錯，先確認語法。
- **System V AMD64 呼叫慣例**是逆向最可靠的錨點：參數 `rdi/rsi/rdx/rcx/r8/r9`、回傳 `rax`、`rbx/rbp/r12-r15` callee-saved、葉函式可用紅區省 frame。**它不受優化等級影響。**
- `lea` 常被拿來當乘法/加常數（`lea (%r,%r,4)`=×5）與地址計算；`xor %r,%r`=清零；`test %r,%r`=檢查是否為 0——這些慣用語要一眼認出。
- `-O0` 把參數 spill 到固定 stack slot、好對答案；`-O2` 把一切壓進暫存器、是真實目標。認 pattern 先看 `-O0`，驗功力看 `-O2`。

## 自我檢核

- [ ] 我看到 `mov %edi, -0x14(%rbp)` 能說出這是「AT&T 語法，把第 1 個參數（int）存進 stack slot」
- [ ] 我能默寫 System V 前 6 個整數參數的暫存器順序，並說出回傳值放哪
- [ ] 我看到 `lea (%rdi,%rdi,4), %eax` 知道它算的是 `x*5`，不是在載入記憶體
- [ ] 我能解釋為什麼 `-O2` 的葉函式沒有 `push %rbp`（紅區 / 不需 frame）
- [ ] 我看到函式開頭 `push %rbx` 能推論「這函式要用 callee-saved 暫存器，內部有跨呼叫存活的值」
- [ ] 我知道 `xor %eax,%eax` 和 `test %eax,%eax` 各是什麼慣用語，不會誤讀成一般位元運算

## 延伸閱讀

### 書籍

- **《Reverse Engineering for Beginners》(RE101)** — Dennis Yurichev（[免費下載](https://beginners.re/)）
  - **定位**：本章的最佳題庫。它從「一行 C ↔ 一段 asm」教起，海量對照。
  - **讀哪裡**：Part I 的前幾節（函式 prologue/epilogue、參數傳遞、`lea` 的用法），跟本章一一呼應，拿來當練習題。
- **《Practical Binary Analysis》** — Dennis Andriesse（No Starch, 2019）
  - **定位**：Linux/ELF/x86-64 分析的系統教材。
  - **讀哪裡**：附錄 A（x86-64 assembly 速成）與 Ch 5–6（disassembly 原理），補足本章沒展開的指令細節。

### 官方文件 / 工具

- **[System V AMD64 ABI 規格](https://gitlab.com/x86-psABIs/x86-64-ABI)**
  - **這是什麼**：呼叫慣例的權威來源。參數暫存器順序、caller/callee-saved、紅區、struct 傳遞規則的原始定義。
  - **讀哪裡**：§3.2「Function Calling Sequence」——本章呼叫慣例那節的出處，想追細節（如 struct 怎麼拆進暫存器）看這裡。
- **[Compiler Explorer (godbolt.org)](https://godbolt.org/)**
  - **這是什麼**：即時 source↔asm 對照。想確認某條 asm 對應什麼 C，或想看某段 C 在不同編譯器/優化下變成什麼，反查它。
  - **怎麼用**：貼 C、選 gcc + `-O0`/`-O2`、右邊即時出 asm，本章每個範例都能在上面重現。
- **[Intel 64 and IA-32 Architectures Software Developer's Manual, Vol. 2（指令參考）](https://www.intel.com/content/www/us/en/developer/articles/technical/intel-sdm.html)**
  - **這是什麼**：每條指令語意的最終答案。逆向撞到不認識的指令時查這裡（別想通讀）。
  - **怎麼用**：當字典查——「這條 `sar` 到底做什麼」直接搜指令名看它的 Operation 段。

暫存器、指令族、呼叫慣例、stack frame——這是你認 binary 的字母表。下一章我們用它拼出第一個「詞」：控制流。你會看到 C 的 `if`/`for`/`switch` 在 asm 裡各自是什麼跳轉 pattern，以及怎麼從一堆 `cmp`/`jne` 反推出高階的控制結構。

→ [Ch 5 認出控制流：if / loop / switch](./05-recognizing-control-flow.md)
