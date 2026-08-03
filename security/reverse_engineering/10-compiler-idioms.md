# Ch 10 — 認出編譯器慣用語（compiler idioms）

> **目標**：建立這門課最核心的能力——一眼認出編譯器慣用語（compiler idiom）。這是 **binary 版的 pattern 辨識**，明確對應姊妹課 [`codebase_case_studies`](../../soft_skills/codebase_case_studies/README.md) 的「設計 pattern 字典」：那門教你在 source 裡認出「這是 reactor event loop」，這門教你在 asm 裡認出「這是 signed 除以 2」。每個 idiom 給你一個 beacon（一眼認出的形狀）+ 對應 source。全部 gcc 真跑。

> **環境**：WSL2 / Linux x86-64，gcc + objdump。本章所有 asm 真跑（`gcc -O1`/`-O2`）。

## 為什麼需要這個？

老手逆向為什麼快？不是讀得更用力，是**一眼把一段 asm chunk 成一個已知概念**。看到 `imul $0xcccccccd; shr $0x23` 不需要逐條推理——「除以 10」，下一個。看到 `shr $0x1f; add; sar`——「signed /2」，下一個。這種 chunking 就是姊妹課講的 pattern 辨識，認知科學的核心結論：**專家的速度來自 pattern 庫的大小**。

編譯器慣用語是 binary pattern 庫的骨幹。編譯器為了效率，會把高階運算換成看起來完全不同的低階序列——除法換成乘魔數、乘法換成 lea、取模換成 and。**不認得這些 idiom，你會逐條推理一段其實一眼可知的 code，還可能推錯**（把「除以 10」看成一堆神秘的乘法移位）。認得它們，逆向速度和正確率一起跳級。

這章要你為每個 idiom 建一張**卡片**（beacon → 語意 → source），Ch 30 會把全課的卡片收斂成完整字典。這正是 `codebase_case_studies` 每讀完一個 codebase 就萃取 pattern 卡的鏡像做法。

## 先建立直覺：編譯器把「慢操作」換成「快序列」

CPU 上有些操作很慢（除法幾十個 cycle）、有些很快（移位、lea 1 cycle）。編譯器的優化器有一本「等價變換」的帳：只要結果相同，就把慢的換成快的。你逆向時看到的是**變換後的快序列**，要在腦中做反變換還原意圖：

```
   你寫的（意圖）          編譯器產出的（你看到的）        反變換（你要認出的）
  ┌──────────────┐       ┌───────────────────────┐     ┌──────────────┐
  │  x / 10      │──────►│ imul $0xcccccccd; shr  │────►│ 「除以 10」   │
  │  x * 12      │──────►│ lea (x,x,2); shl $2    │────►│ 「乘以 12」   │
  │  x % 8       │──────►│ and $0x7               │────►│ 「取模 8」    │
  │  x == 0      │──────►│ test x,x; sete          │────►│ 「判零」      │
  │  memset(,0,) │──────►│ 一串 movq 0 / rep stos │────►│ 「清零」      │
  └──────────────┘       └───────────────────────┘     └──────────────┘
       source                    asm（事實）              idiom 卡片
```

每張 idiom 卡的格式：**beacon（形狀）→ 語意 → 對應 source → 怎麼還原參數（如除數）**。下面逐個真跑建卡。

## 卡片 1：除法變乘魔數（`x / 常數` → imul + shr）

**這是最重要、最容易被誤讀的 idiom。** source 是 `unsigned div10(unsigned x){ return x / 10; }`，`gcc -O1` 真跑：

```asm
0000000000001189 <div10>:
    1189:  endbr64
    118d:  mov    %edi,%eax
    118f:  mov    $0xcccccccd,%edx      ; ← 魔數 magic number
    1194:  imul   %rdx,%rax             ; ← 64-bit 乘法
    1198:  shr    $0x23,%rax            ; ← 右移 35（0x23）
    119c:  ret
```

**沒有 `div` 指令**。除以 10 被換成「乘一個魔數再右移」。原理：`x / 10 ≈ x * (2^35 / 10) / 2^35`，魔數就是 `ceil(2^35 / 10) = 0xcccccccd`，右移 35 就是除以 `2^35`。驗證（真跑 Python）：

```
0xcccccccd = 3435973837
2^35       = 34359738368
0xcccccccd / 2^35 = 0.10000000000582...  ≈ 1/10  ✓
```

**還原除數的方法**：magic × 除數 ≈ 2^(移位量)。看到 `imul $M; shr $S`，除數 ≈ `2^S / M`。這裡 `2^35 / 0xcccccccd ≈ 10`。

**beacon**：看到一個**看起來很亂的 32-bit 常數**（`0xcccccccd`、`0xaaaaaaab`、`0x66666667`…）被 `imul`、後面接 `shr`——這是**無號除以常數**，不是什麼神秘乘法。`0xcccccccd`+`shr 35` 幾乎專屬「除以 10」，看多了直接反射。

> 踩雷預告：初學者看到 `imul $0xcccccccd` 會以為「這裡在乘一個大數」，然後整條邏輯全推錯。**看到亂魔數 imul 就想「這是除法」**。

## 卡片 2：signed 除以 2^n（`shr; add; sar` — 呼應 Ch 0）

source `int sdiv2(int x){ return x / 2; }`，`gcc -O1` 真跑：

```asm
000000000000119d <sdiv2>:
    119d:  endbr64
    11a1:  mov    %edi,%eax
    11a3:  shr    $0x1f,%eax            ; ┐ 取符號位（x >> 31，得 0 或 1）
    11a6:  lea    (%rax,%rdi,1),%eax    ; ┤ x + 符號位（負數時 +1 補償）
    11a9:  sar    %eax                  ; ┘ 算術右移 1 = /2
    11ab:  ret
```

Ch 0 就見過這個。**為什麼不是單一條 `sar`？** 因為 C 的整數除法**向零取整**（`-3/2 == -1`），而 `sar`（算術右移）**向負無窮取整**（`-3>>1 == -2`），對負數差 1。編譯器先 `shr $0x1f` 取符號位（負數得 1、非負得 0），加到 x 上補償，再 `sar`。

**beacon**：`shr $0x1f`（或 `$0x3f` for 64-bit）取符號位 → `add`/`lea` → `sar`。這三步連在一起 = **signed 除以 2^n**。除以 4 會是 `sar $0x2` 且補償用 `$0x1e`/加更多。看到 `shr 31; add; sar` 直接寫下「signed /2」。

**對照無號除以 2^n**（真跑 `gcc -O1`）：

```asm
<udiv2>:  mov %edi,%eax;  shr %eax;      ret   ; unsigned x/2 = 單一 shr $1
<udiv4>:  mov %edi,%eax;  shr $0x2,%eax; ret   ; unsigned x/4 = shr $2
```

無號除 2^n **只需一條 `shr $n`**——不用補償（無號沒有向下取整問題）。所以：`shr` 單獨 = 無號除 2^n；`shr 31; add; sar` = 有號除 2^n。這個 signed/unsigned 差異本身就洩露了變數的號誌（呼應 Ch 9：`shr` vs `sar` 是判 signed/unsigned 的線索）。

## 卡片 3：取模 2^n（`and mask`）

source `unsigned mod8(unsigned x){ return x % 8; }`，`gcc -O1` 真跑：

```asm
00000000000011ac <mod8>:
    11ac:  endbr64
    11b0:  mov    %edi,%eax
    11b2:  and    $0x7,%eax             ; ← x & 7 = x % 8
    11b5:  ret
```

`x % 8` = `x & 7`（因為 8 = 2^3，取模 = 保留低 3 位）。沒有 `div`、沒有 idiv。

**beacon**：`and $0x7`（=%8）、`and $0xf`（=%16）、`and $0x1f`（=%32）——**and 一個「全 1 的低位遮罩」（2^n - 1）= 取模 2^n**。mask+1 就是模數。這也是 Ch 0 那句「除以 2 的餘數 = 最低位」（`and $0x1` = %2）的一般化。

**signed 取模沒這麼乾淨**。source `int smod4(int x){ return x % 4; }`，`gcc -O1` 真跑：

```asm
0000000000000013 <smod4>:
    13:  mov    %edi,%edx
    19:  sar    $0x1f,%edx            ; ┐ 取符號位（全 0 或全 1）
    1c:  shr    $0x1e,%edx            ; ┤ 補償量（負數時 = 3）
    1f:  lea    (%rdi,%rdx,1),%eax    ; ┤ x + 補償
    22:  and    $0x3,%eax             ; ┤ & 3
    25:  sub    %edx,%eax             ; ┘ 減回補償
    27:  ret
```

`x % 4`（帶號）為了讓負數結果號誌正確，前後夾了 `sar`/`shr` 補償和 `sub` 還原，中間才是 `and $0x3`。**beacon**：`and 遮罩` 被 `sar;shr` 補償和 `sub` 包起來 = **signed 取模 2^n**（對照無號的裸 `and`）。這和卡片 2 的 signed 除法補償是同一套「處理負數向零取整」的把戲。

## 卡片 4：乘法變 lea / shift（`x * 常數`）

source `unsigned mul12(unsigned x){ return x * 12; }`，`gcc -O1` 真跑：

```asm
00000000000011b6 <mul12>:
    11b6:  endbr64
    11ba:  lea    (%rdi,%rdi,2),%eax    ; ← x + x*2 = x*3
    11bd:  shl    $0x2,%eax             ; ← *4
    11c0:  ret                          ; → x*3*4 = x*12
```

`x * 12` = `(x * 3) * 4` = `lea (x,x,2)` 得 3x，再 `shl $2` 乘 4。**沒有 `imul`**——編譯器把常數乘法拆成 lea（可做 ×2/×3/×4/×5/×8/×9）和移位的組合。

**beacon**：`lea (%r,%r,N)` 是 `x*(N+1)`（N∈{1,2,4,8}，得 ×2/×3/×5/×9）；`shl $k` 是 ×2^k；`sub` 補差（如 ×7 = ×8 − ×1，會是 `lea (,%r,8); sub`）。看到 lea/shl/sub 組合算一個值，**在腦中乘回去**得到乘數。`lea (%rdi,%rdi,2)` 一眼就是 ×3。

**對照**：`x * 2` = `add %eax,%eax` 或 `shl $1` 或 `lea (%r,%r)`；`x * 8` = `shl $3`。乘 2 的冪永遠是純移位。

非 lea 能一步搞定的乘數，編譯器用 **加減補差**。source `unsigned mul7(unsigned x){ return x * 7; }`，`gcc -O1` 真跑：

```asm
0000000000000028 <mul7>:
    2c:  lea    0x0(,%rdi,8),%eax     ; ← x * 8（lea 的 scale=8 形式）
    33:  sub    %edi,%eax             ; ← − x  → x*8 − x = x*7
    35:  ret
```

`x * 7` = `x * 8 - x`——`lea (,%rdi,8)` 得 8x，`sub %edi` 減回 1x。**beacon**：`shl`/`lea` 算出一個接近的 2 的冪倍數，再 `sub`/`add` 補差 = **乘一個非 2 冪常數**。看到 `×8 然後 −x`，腦中算 `8−1=7`。乘 15 會是 `×16 − x`、乘 9 會是 `lea (%r,%r,8)`（×9 一步）。**這也是強度削減（strength reduction）的核心**：把 `imul` 換成更快的 lea/shift/add——`-O0` 你會看到老實的 `imul $7`，`-O1` 以上才變這副樣子。

## 卡片 5：xor 清零 與 test 判零

兩個超高頻的小 idiom，真跑（`iszero` 來自 idiom_O1）：

```asm
; 清零：xor reg,reg （不是 mov $0）
    xor    %eax,%eax          ; eax = 0（比 mov $0,%eax 短、且斷相依）

; 判零：test reg,reg + setcc
00000000000011c1 <iszero>:    ; int iszero(int x){ return x == 0; }
    11c1:  endbr64
    11c5:  test   %edi,%edi           ; ← test x,x（x AND x，設 ZF）
    11c7:  sete   %al                 ; ← ZF=1（x==0）→ al=1
    11ca:  movzbl %al,%eax            ; 零延伸成 int
    11cd:  ret
```

- **`xor reg,reg` = 把 reg 設 0**。編譯器幾乎不用 `mov $0,%reg`，一律 `xor`（機器碼更短、CPU 認得這是「清零」不建立假相依）。**beacon**：`xor %eax,%eax` 不是「x XOR x 的運算」，是「eax = 0」。
- **`test reg,reg` = 判斷 reg 是否為 0**（`test x,x` 做 `x & x`，只設旗標）。後面接 `je`/`jne`（判零跳轉）或 `sete`/`setne`（判零取布林）。**beacon**：`test %eax,%eax; je` = `if (x == 0)`；`test; jne` = `if (x != 0)`。

這兩個是 asm 的「標點符號」，認不出會讀得很卡。

## 卡片 6：三元 / 條件選擇變 cmov（無分支）

source `int clampsel(int x,int a,int b){ return x > 0 ? a : b; }`，`gcc -O1` 真跑：

```asm
00000000000011ce <clampsel>:
    11ce:  endbr64
    11d2:  test   %edi,%edi            ; 比較 x 和 0
    11d4:  mov    %edx,%eax            ; eax = b（先放 false 值）
    11d6:  cmovg  %esi,%eax            ; ← x>0（greater）時 eax = a
    11d9:  ret
```

`x > 0 ? a : b` 沒有跳轉——用 `cmovg`（conditional move if greater）：先把 `b` 放進 eax，若條件成立就用 `a` 覆蓋。**無分支（branchless）** 避免分支預測失敗的成本。

**beacon**：`mov`（放預設值）+ `cmovcc`（條件覆蓋）= **三元運算子 / `if(cond) x=a; else x=b;`**。`cmovg`/`cmovle`/`cmovs`… 的後綴就是條件。看到 cmov，想「這是個沒被編成跳轉的 if」。反編譯器通常能還原成 `? :`，但混淆過的 code 靠 cmov 藏控制流時，你得自己認。

## 卡片 7：迴圈累加 與 向量化（SSE/AVX 的 memset/memcpy/sum）

**純量迴圈**：source `sumarr` 累加陣列，`gcc -O2` 真跑：

```asm
0000000000001270 <sumarr>:
    1270:  endbr64
    1274:  test   %esi,%esi            ; n <= 0 ?
    1276:  jle    1298
    1278:  lea    -0x1(%rsi),%eax      ; ┐ 算迴圈上界（&p[n]）
    127b:  lea    0x4(%rdi,%rax,4),%rdx; ┘
    1280:  xor    %eax,%eax            ; sum = 0（xor 清零，卡片 5）
    1288:  add    (%rdi),%eax          ; ┐ sum += *p
    128a:  add    $0x4,%rdi            ; ┤ p++（+4 = sizeof int）
    128e:  cmp    %rdx,%rdi            ; ┤ p != end ?
    1291:  jne    1288                 ; ┘ 迴圈
    1293:  ret
```

**beacon**：`add (%reg),%acc; add $元素大小,%reg; cmp end; jne` = **陣列遍歷累加**。指標用「加元素大小」前進、和一個預先算好的 end 比——這是 pointer-based 迴圈的標準形（比 index 版少一個乘法）。

**向量化（vectorized）memset**：`memset(buf, 0, 256)` 在 `-O2` 真跑（`bigzero`）：

```asm
0000000000000000 <bigzero>:
    0:   endbr64
    4:   movq   $0x0,(%rdi)           ; 頭部對齊處理
    e:   lea    0x8(%rdi),%rdi
   1f:   and    $0xfffffffffffffff8,%rdi ; 對齊到 8
   26:   add    $0x100,%ecx           ; 剩餘 byte 數 = 256
   2c:   shr    $0x3,%ecx             ; /8 = qword 個數
   2f:   rep stos %rax,%es:(%rdi)     ; ← rep stosq：批次寫 0
   32:   ret
```

**beacon**：`rep stos`（批次填充）、或一串 `movdqa/movaps %xmm0`（16-byte SSE）、`vmovdqu %ymm0`（32-byte AVX）**寫連續記憶體** = **memset / 陣列初始化**。編譯器把 `memset(,0,N)` inline 成向量化或 `rep stos`，你看到一坨 xmm/rep 別怕，那是「填一塊記憶體」。同理連續的 xmm **load+store** = memcpy。

（較小的 memset 如 Ch 前面的 64-byte，`-O1` 會展開成 8 條 `movq $0x0` 直寫——也是同一個「清零一塊」語意，只是沒到動用 rep 的門檻。）

## 卡片 8：switch jump table

source 的 `classify` 是 0..4 的 switch，`gcc -O1` 真跑：

```asm
0000000000001207 <classify>:
    1207:  endbr64
    120b:  cmp    $0x4,%edi            ; ← 邊界檢查：x > 4 ?
    120e:  ja     123b                 ; ← 超界跳 default（ja=無號大於）
    1210:  mov    %edi,%edi            ; 零延伸 index
    1212:  lea    0xdeb(%rip),%rdx     ; ← rdx = jump table 基址（.rodata@0x2004）
    1219:  movslq (%rdx,%rdi,4),%rax   ; ← 讀 table[x]（4-byte 相對 offset）
    121d:  add    %rdx,%rax            ; ← 基址 + offset = 目標位址
    1220:  notrack jmp *%rax           ; ← 間接跳轉到 case
    1223:  mov    $0xc8,%eax           ; case 0: return 200(0xc8)... 各 case body
    ...
```

jump table 內容（真跑 `objdump -s -j .rodata`，位址 0x2004 起）：

```
 2004 3df2ffff 1ff2ffff 25f2ffff 2bf2ffff 31f2ffff
      └case0─┘ └case1─┘ └case2─┘ └case3─┘ └case4─┘   （4-byte 相對 offset）
```

**beacon**：`cmp $N; ja default`（邊界檢查）→ `lea table(%rip)` → `mov (%table,%index,4)` → `jmp *%reg`（間接跳）= **switch 的 jump table 派發**。這是 `switch` 在 case 密集連續時的實作。看到「邊界檢查 + 用 index 查表 + 間接跳」立刻認出 switch，然後去 .rodata 讀表找各 case 位址。

（case 稀疏時編譯器改用一串 `cmp; je`（if-else 鏈）或二分——那時 beacon 是「同一變數連續和不同常數比」，呼應 Ch 9 的 enum 判別。）

## 對比與取捨：idiom 一覽卡

| idiom | beacon（一眼形狀） | 語意 | 還原參數 |
|---|---|---|---|
| 除以常數 | 亂魔數 `imul` + `shr` | `x / C`（無號） | C ≈ 2^移位 / 魔數 |
| signed /2^n | `shr 31; add; sar` | `x / 2^n`（有號） | 移位量 = n |
| 無號 /2^n | 單 `shr $n` | `x / 2^n`（無號） | n |
| 取模 2^n | `and $(2^n−1)` | `x % 2^n` | mask+1 = 模數 |
| 乘常數 | `lea (r,r,N)` / `shl` / `sub` 組合 | `x * C` | 在腦中乘回去 |
| 清零 | `xor r,r` | `r = 0` | — |
| 判零 | `test r,r` + `je`/`sete` | `x == 0` | — |
| 三元 | `mov` + `cmovcc` | `cond ? a : b` | cc = 條件 |
| 陣列累加 | `add (p),acc; add $sz,p; cmp end` | 遍歷加總 | sz = 元素大小 |
| memset/cpy | `rep stos` / 連續 xmm/ymm | 填/複製記憶體 | — |
| switch | `cmp N; ja` + 查表 + `jmp *` | jump table 派發 | 表在 .rodata |

**取捨**：這些 idiom 隨**優化等級**和**編譯器**變。`-O0` 常保留原始 `div`/`idiv`/`imul`（不做 strength reduction），`-O2`/`-O3` 才大量出現。gcc 和 clang 的魔數、lea 拆法可能不同——所以老手能從 idiom 風格反推編譯器（Ch 0 進階提過）。**你的字典要標明「在哪個等級/編譯器看到的」**。

**gcc vs clang 的除法魔數**（真跑對照 `div10`）：

```asm
; gcc -O1                          ; clang -O1
mov $0xcccccccd,%edx              mov $0xcccccccd,%eax
imul %rdx,%rax                    imul %rcx,%rax
shr  $0x23,%rax                   shr  $0x23,%rax
```

除法這種有唯一數學解的 idiom，gcc 和 clang **魔數與移位量一致**（都是 `0xcccccccd` + `shr 35`），只有暫存器配置微差——這類 idiom 認一次跨編譯器通用。但 lea 拆法、迴圈展開、向量化門檻各家不同，那些才是能反推編譯器的指紋。認 idiom 時把「數學等價的核心」和「編譯器風格的外殼」分開看。

## 踩雷集錦

1. **把除法魔數當成乘法**：看到 `imul $0xcccccccd` 就寫「乘以 34 億」，整條算術全錯。錯誤直覺：「imul 就是乘法」。正確：**跟著一個亂常數又接 shr 的 imul，是除法**。認魔數，別認字面。

2. **把 `xor %eax,%eax` 當成 XOR 運算**：以為這裡在對某個值做互斥或。錯誤直覺：「xor 就是位元運算」。正確：`xor r,r`（**同一個暫存器**）是慣用的「設 0」，不是運算。

3. **看到 `sar` 就當成無條件 /2，漏掉補償**：把 `shr 31; add; sar` 只看最後的 `sar`，以為是單純右移。錯誤直覺：「sar 就是 /2」。正確：前面的 `shr 31; add` 是**負數補償**，整組才是「signed /2」；少了補償是無號。

4. **看到 cmov 找不到分支就以為沒有 if**：`cmovg` 沒有 `jmp`，你以為這裡是直線 code。錯誤直覺：「沒跳轉就沒條件」。正確：`cmovcc` 是**無分支的 if**，控制流藏在條件搬移裡。

5. **被向量化的 memset/memcpy 嚇到**：看到一坨 `movdqa %xmm0` / `rep stos` 以為遇到什麼高深 SIMD 演算法。錯誤直覺：「xmm = 複雜數值計算」。正確：連續寫/複製記憶體的 xmm/rep，九成是**編譯器 inline 的 memset/memcpy**，語意平凡。

## 進階：再往深一層

- **用 godbolt 反查驗證**：認出一個 idiom 後，把你猜的 source（如 `x/10`）貼進 [Compiler Explorer](https://godbolt.org/)、選同編譯器同等級，看它產出的 asm 是否吻合你手上的——這是 idiom 卡的 ground-truth 驗證，正向編譯確認你的反向認讀。
- **反編譯器的 idiom 庫有邊界**：Ghidra/Hex-Rays 內建認得除法魔數、`x/2` 等常見 idiom（會直接還原成 `x/10`），但它的庫有限——遇到它不認得的 strength reduction 就留成怪運算（Ch 8 見過）。**你的 idiom 字典要能補反編譯器認不出的洞**，這是你勝過 F5 的地方。
- **idiom 是混淆與反混淆的戰場**：混淆器（Ch 23）反過來把簡單運算「複雜化」成怪 idiom 藏意圖；而你認 idiom 的能力就是拆它。認得越多正常 idiom，越容易分辨「這是正常編譯器優化」vs「這是刻意混淆」。

## 本章重點整理

- 編譯器慣用語 = binary 版的 pattern（`codebase_case_studies` 設計 pattern 的鏡像）；逆向速度來自**一眼 chunk 成已知 idiom**，不逐條推理。
- 每張卡：**beacon → 語意 → source → 還原參數**。核心 idiom：除法魔數、signed/unsigned 除 2^n、取模 2^n（and mask）、乘常數（lea/shl/sub）、xor 清零、test 判零、cmov 三元、陣列累加、向量化 memset/memcpy、switch jump table。
- 最易誤讀的：**亂魔數 imul = 除法**（不是乘）、**`shr 31;add;sar` = signed /2**（補償別漏）、**xor r,r = 清零**（不是運算）、**cmov = 無分支 if**。
- idiom 隨優化等級/編譯器變——`-O0` 少、`-O2`/`-O3` 多；字典要標來源。用 godbolt 正向反查驗證你的認讀。
- 把每個認出的 idiom 記成卡片，Ch 30 收斂成完整字典——這是你逆向的 pattern 庫。

## 自我檢核

- [ ] 看到 `imul $0xcccccccd; shr $0x23` 我立刻說「除以 10」，並能還原除數
- [ ] 我能分辨 `shr $1`（無號 /2）和 `shr 31;add;sar`（有號 /2），並解釋補償
- [ ] 看到 `and $0x7` 我說「取模 8」；看到 `lea (%rdi,%rdi,2)` 我說「×3」
- [ ] `xor %eax,%eax` 和 `test %eax,%eax` 我一眼認出是「清零」和「判零」，不當運算
- [ ] 看到 `cmp N; ja; lea table; jmp *` 我認出 switch jump table，知道去 .rodata 讀表
- [ ] 我為本章每個 idiom 建了卡片（beacon/語意/source/參數），準備進 Ch 30 字典

## 延伸閱讀

### 書籍

- **《Reverse Engineering for Beginners》** — Dennis Yurichev（[免費](https://beginners.re/)）
  - **定位**：compiler idiom 的聖經，海量「一行 C ↔ asm」對照，本章每張卡都能在裡面找到擴充。
  - **讀哪裡**：算術/除法優化、strength reduction、switch、SIMD 相關章節——當字典逐條擴充你的卡片庫。
- **《Hacker's Delight》** — Henry S. Warren Jr.
  - **定位**：除法變乘魔數、位元技巧的數學原理來源；想搞懂「為什麼是 0xcccccccd」讀這本。
  - **讀哪裡**：整數除法（division by constants）章——魔數怎麼算出來的完整推導。

### 工具與文件

- **[Compiler Explorer (godbolt.org)](https://godbolt.org/)**
  - **這是什麼**：idiom 反查與驗證神器。認出一個 idiom 後，貼你猜的 source、選同編譯器等級，看 asm 是否吻合。
  - **怎麼用**：本章所有卡片都可以在 godbolt 上換 `-O0`/`-O2`/`-O3`、換 gcc/clang，觀察 idiom 怎麼變——建字典的最佳沙盒。
- **[Agner Fog's optimization manuals](https://www.agner.org/optimize/)**
  - **讀哪裡**：instruction tables 與 optimizing assembly——理解編譯器為什麼偏好 lea/shift/cmov（成本模型），從「為什麼優化成這樣」反向鞏固 idiom 認讀。

認得 idiom 是逆向的 pattern 辨識骨幹。下一章我們認另一種 pattern——標準庫呼叫與資料結構的指紋，讓你在 binary 裡一眼認出「這裡在用 malloc」「這是 linked list 遍歷」「這是 std::string」。

→ [Ch 11 認出標準庫與資料結構指紋](./11-recognizing-stdlib-fingerprints.md)
