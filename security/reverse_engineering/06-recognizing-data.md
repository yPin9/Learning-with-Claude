# Ch 6 — 認出資料：struct / array / 指標 / 全域

> **目標**：學會從定址模式（addressing mode）反推資料的形狀——`rbp-0x4` 是哪個局部變數、`0x8(%rax)` 在存取 struct 的哪個欄位、`(%rdi,%rsi,8)` 在索引什麼元素大小的陣列、`0x2e22(%rip)` 指向哪個全域。控制流（Ch 5）是骨架，資料是掛在上面的肉。逆向時把兩者對起來，程式的意圖就完整了。全程 `-O0`（可讀對照）與 `-O2`（真實樣貌）真跑對照。

> **環境**：WSL2 / Linux x86-64，gcc 11.4 + objdump + readelf。本章所有 asm/段表都是真跑輸出。

## 為什麼需要這個？

編譯器丟掉了型別和變數名，但它**丟不掉資料的物理佈局**——一個 struct 的欄位在記憶體裡的相對位置、一個陣列的元素大小、一個全域變數在哪個段。這些佈局資訊**全都藏在定址模式裡**。`0x8(%rax)` 這個「基址 + 8」，就是在對你說「這個物件的第 8 個 byte 有個欄位」。

逆向資料的本質，是**從一堆 offset 反推出型別佈局**：看到程式反覆用 `(%rax)`、`0x4(%rax)`、`0x8(%rax)` 存取同一個 `rax`，你就能重建出「這是一個 struct，欄位在 0/4/8」。這是 Ch 9「型別還原」的地基，也是逆向裡最像考古的部分——從碎片拼出原本的結構。

> 這是 [`codebase_case_studies`](../../soft_skills/codebase_case_studies/README.md) 的 pattern 辨識在 binary 的鏡像：那門教你在 source 裡認出「這是個 ring buffer struct」，這門教你從 `base+offset` 的定址 pattern 認出「這裡有個 struct，我來重建它的欄位」。

## 先建立直覺：資料住在哪，就用什麼方式定址

x86-64 的資料不外乎住在四個地方，**每個地方有一種招牌定址方式**。先把這張對應表刻進腦子，之後看到定址就能反推資料的「身分」：

```
   資料的家              招牌定址                       身分
   ─────────────────────────────────────────────────────────
   stack（局部變數/參數） -0xNN(%rbp) 或 0xNN(%rsp)    局部變數、spill 的參數
   全域/靜態（.data/.bss） 0xNN(%rip)                   全域變數、靜態變數
   唯讀（.rodata）        lea 0xNN(%rip), %reg         字串常數、jump table、唯讀陣列
   heap / 傳入的物件      (%rax) / 0xN(%rax)           指標解參考、struct 欄位
```

四個定址形狀，四種資料身分。這一章就是把每一種拆開講，每種都真跑一個範例逆給你看。核心動作永遠是同一個：**看基址是誰、位移是多少、有沒有索引×scale，反推出資料的形狀。**

## 局部變數與參數：rbp-relative（-O0）

`-O0` 下，每個局部變數和每個入參都有一個**固定的 stack slot**，用 `rbp` 加負位移定址。這是逆向最友善的情況——同一個 `-0x4(%rbp)` 從頭到尾就是同一個變數。

回顧 [Ch 4](./04-x86-64-for-reversers.md) 的 `add6`：函式一進來就把 6 個入參暫存器 spill 到 `-0x14(%rbp)` ~ `-0x28(%rbp)`。**這些 slot 就是參數的家**。之後 code 都讀寫這些 slot，你只要建一張「slot → 變數」對照表，就能把 asm 讀成有名字的 C。

`-O2` 下這個福利大幅消失：變數盡量留在暫存器、甚至被優化掉，stack slot 只在暫存器不夠用（spill）或需要取址（`&var`）時才出現。所以 **`rbp-relative` 的乾淨對應是 `-O0` 的特權**，真實 `-O2` binary 要靠暫存器追值（Ch 4 的資料流追蹤）。

> 補充：`-O2` 常用 `rsp-relative`（`0xNN(%rsp)`）而非 `rbp-relative`，因為它省掉 frame pointer（`rbp` 拿去當一般暫存器用）。看到全用 `%rsp` 定址、沒有 `push %rbp; mov %rsp,%rbp`，那是「省了 frame pointer 的優化 code」，Ch 7 會細講。

## 全域與靜態：rip-relative（.data / .bss）

全域變數和 `static` 變數住在 `.data`（有初值）或 `.bss`（初值為 0）。它們用 **rip-relative（相對指令指標）** 定址：`0xNN(%rip)`。為什麼不用絕對位址？因為現代 binary 是 PIE（位置無關可執行檔），載入位址不固定，所以用「相對目前指令的位移」來指，不管載到哪都對。

**objdump 的貼心之處**：它會幫你算好 rip-relative 指向哪，寫在註解裡。

### 真跑：全域變數的讀寫

```c
int g_counter = 42;   // .data（有初值）
int g_zero;           // .bss（初值 0）

int use_global(void){
    g_counter++;
    return g_counter + g_zero;
}
```

`gcc -O0` 真跑：

```asm
00000000000011e0 <use_global>:
    11e0:	endbr64
    11e4:	push   %rbp
    11e5:	mov    %rsp,%rbp
    11e8:	mov    0x2e22(%rip),%eax        # 4010 <g_counter>  ← objdump 幫你解出是 g_counter！
    11ee:	add    $0x1,%eax                ; g_counter + 1
    11f1:	mov    %eax,0x2e19(%rip)        # 4010 <g_counter>  ← 寫回
    11f7:	mov    0x2e13(%rip),%edx        # 4010 <g_counter>
    11fd:	mov    0x2e21(%rip),%eax        # 4024 <g_zero>     ← 另一個全域
    1203:	add    %edx,%eax
    1205:	pop    %rbp
    1206:	ret
```

三件事對照原始 C：

1. **`0x2e22(%rip)` 後面 objdump 註解 `# 4010 <g_counter>`**：它幫你算出這個 rip-relative 定址的絕對目標是 `0x4010`，而且從符號表查到那是 `g_counter`。**真實 stripped binary 裡不會有 `<g_counter>` 這名字**，但位址 `0x4010` 還在——你得自己去查那是哪個全域（下面 readelf 示範）。
2. **`g_counter++` = 讀 rip 相對位址 → +1 → 寫回同一位址**（三條 mov）。這種「同一個 `0xNN(%rip)` 被讀又被寫」的 pattern，就是全域變數被修改的指紋。
3. **不同 offset 指不同全域**：`4010` 是 `g_counter`、`4024` 是 `g_zero`。位移差別對應不同變數。

真跑 `readelf -s` 確認這些位址的身分（stripped 後就沒這表了，這裡是 ground-truth 對照）：

```
 27: 0000000000004018     8 OBJECT  GLOBAL DEFAULT   25 g_msg
 33: 0000000000004010     4 OBJECT  GLOBAL DEFAULT   25 g_counter
 34: 0000000000004024     4 OBJECT  GLOBAL DEFAULT   26 g_zero
```

和 `readelf -S`（段表）交叉比對，看每個位址落在哪個段：

```
  [25] .data      PROGBITS   0000000000004000   ...
  [26] .bss       NOBITS     0000000000004020   ...
```

`g_counter`(0x4010)、`g_msg`(0x4018) 落在 `.data`（有初值）；`g_zero`(0x4024) 落在 `.bss`（`NOBITS`，載入時歸零，不佔檔案空間）。**逆向時判斷「這個 rip 目標是全域還是唯讀常數」，就看它落在 `.data`/`.bss`（可寫全域）還是 `.rodata`（唯讀）。**

## 字串與唯讀資料：lea + .rodata

字串常數、`const` 陣列、jump table（Ch 5）都住在 `.rodata`（唯讀資料）。載入它們的位址時，編譯器用 **`lea 0xNN(%rip), %reg`**——注意是 `lea`（算位址）不是 `mov`（讀值），因為你要的是「字串的位址」拿去傳給 `printf`，不是把字串內容搬進暫存器。

回顧 [Ch 4](./04-x86-64-for-reversers.md) 的 `main`：

```asm
    11c8:	lea    0xe35(%rip),%rax        # 2004 <_IO_stdin_used+0x4>
    11cf:	mov    %rax,%rdi               ; 字串位址 → rdi（printf 第 1 參數）
```

`lea 0xe35(%rip), %rax` 把格式字串 `"%d\n"` 的位址算進 rax，再放進 rdi 當 printf 的第一個參數。**看到 `lea 0xNN(%rip), %reg` 後面接著把 `%reg` 當參數傳給某函式，那 `%reg` 幾乎一定是個字串或唯讀資料的位址。** 真跑 `objdump -s -j .rodata` 就能看到那塊記憶體的內容：

```
Contents of section .rodata:
 2000 01000200 68656c6c 6f00256c 64202564   ....hello.%ld %d
 2010 20256420 25730a00                      %d %s..
```

`68656c6c 6f00` 是 `"hello\0"`，`256c6420...` 是 `"%ld %d %d %s\n\0"`。**這就是 `strings` 指令為什麼是逆向的第一線索**——可讀字串直接洩漏程式在幹嘛（錯誤訊息、格式、路徑、密碼提示）。逆向時你會反覆做「找到一個字串 → 在 `objdump -d` 裡搜哪條 `lea` 載入它 → 那裡就是用到這字串的邏輯」。

## 指標與解參考：(%reg)

指標在 asm 裡就是「一個存著位址的暫存器」。**解參考（dereference）= 用括號定址 `(%reg)`**：

- `mov (%rax), %rbx` = `rbx = *rax`（讀 rax 指向的記憶體）
- `mov %rbx, (%rax)` = `*rax = rbx`（寫進 rax 指向的記憶體）
- `mov 0x8(%rax), %rbx` = `rbx = *(rax + 8)`（存取 rax 指向物件的第 8 byte）

**沒括號 = 用暫存器的值本身（指標）；有括號 = 用它指向的內容（解參考）。** 這個區別是讀指標邏輯的命門。一層 `(%rax)` 是一次解參考；`mov (%rax), %rax; mov (%rax), %rbx` 兩次解參考 = 追一條指標鏈（如 linked list 的 `p->next->data`）。

## Struct：base + 固定 offset

struct 在記憶體裡是**一塊連續的 bytes**，欄位按宣告順序排（中間可能有對齊 padding）。存取欄位 = **基址 + 該欄位的固定 offset**。這是逆向重建 struct 佈局的核心：**收集所有對同一個基址的固定 offset，就重建出欄位表。**

### 真跑：從 offset 反推 struct 佈局

```c
struct Point { int x; int y; long tag; };

long point_sum(struct Point *p){
    return (long)p->x + p->y + p->tag;
}
```

`gcc -O0` 真跑：

```asm
0000000000001169 <point_sum>:
    1169:	endbr64
    116d:	push   %rbp
    116e:	mov    %rsp,%rbp
    1171:	mov    %rdi,-0x8(%rbp)          ; p（指標）存 slot
    1175:	mov    -0x8(%rbp),%rax          ; rax = p
    1179:	mov    (%rax),%eax              ; ★ p->x  = *(p+0)，讀 4 bytes（eax）→ offset 0
    117b:	movslq %eax,%rdx                ; int 符號延伸成 64-bit（x 是 signed int）
    117e:	mov    -0x8(%rbp),%rax
    1182:	mov    0x4(%rax),%eax           ; ★ p->y  = *(p+4)，讀 4 bytes → offset 4
    1185:	cltq                            ; 符號延伸
    1187:	add    %rax,%rdx
    118a:	mov    -0x8(%rbp),%rax
    118e:	mov    0x8(%rax),%rax           ; ★ p->tag = *(p+8)，讀 8 bytes（rax）→ offset 8
    1192:	add    %rdx,%rax
    1195:	pop    %rbp
    1196:	ret
```

逆向重建 struct 的推理，逐條對照：

| asm | offset | 讀取寬度 | 推論 |
|---|---|---|---|
| `mov (%rax),%eax` | 0 | 4 bytes（`eax`）| 一個 `int` 欄位 @ 0 |
| `mov 0x4(%rax),%eax` | 4 | 4 bytes | 一個 `int` 欄位 @ 4 |
| `mov 0x8(%rax),%rax` | 8 | 8 bytes（`rax`）| 一個 8-byte 欄位（`long`/指標）@ 8 |

從三個 offset + 讀取寬度，你**重建出**：`struct { int @0; int @4; long @8; }`——和原始 `struct Point` 完全吻合。**這就是逆向 struct 的全部祕密：offset 給你欄位位置，讀寫寬度（`eax` 4-byte vs `rax` 8-byte、`movzbl` 1-byte…）給你欄位大小，`movslq`/`movzx` 給你 signed/unsigned。** 你不需要原始 struct 宣告——你從使用方式把它考古出來。

注意 `movslq %eax, %rdx`（符號延伸）：因為 `x` 是 `signed int`，加到 `long` 前要補號。若原始是 `unsigned`，這裡會是 `mov %eax, ...`（zero-extend）。**這是 signedness 的直接證據。**

### 對照 `-O2`：offset 不變，但更緊

```asm
00000000000011a0 <point_sum>:
    11a0:	endbr64
    11a4:	movslq (%rdi),%rax          ; p->x（offset 0），一條就 load+符號延伸
    11a7:	movslq 0x4(%rdi),%rdx        ; p->y（offset 4）
    11ab:	add    %rdx,%rax
    11ae:	add    0x8(%rdi),%rax        ; p->tag（offset 8），直接加不用先 load
    11b2:	ret
```

從 12 條變 5 條，但**三個 offset（0, 4, 8）一模一樣**——因為 struct 佈局是 ABI 決定的，不受優化影響。`-O2` 也不再把 `p` spill 到 stack，直接用 `rdi`（第一參數）當基址。**struct 的 offset 是逆向裡最穩定的錨點之一**，不管優化等級都認得出。

## Array：base + index × scale

陣列存取 = **基址 + 索引 × 元素大小**，用 x86-64 的 SIB 定址 `(%base, %index, scale)`。**scale（1/2/4/8）直接洩漏元素大小**：scale=4 → `int`/`float` 陣列，scale=8 → `long`/指標陣列，scale=1 → `char` 陣列。

### 真跑：陣列走訪

```c
int array_sum(int *a, int n){
    int s = 0;
    for (int i = 0; i < n; i++) s += a[i];
    return s;
}
```

`gcc -O0` 真跑（迴圈本體節錄）：

```asm
    11b6:	mov    -0x4(%rbp),%eax          ; i
    11b9:	cltq                            ; i 延伸成 64-bit
    11bb:	lea    0x0(,%rax,4),%rdx        ; ★ rdx = i * 4（scale=4 → int 陣列！）
    11c3:	mov    -0x18(%rbp),%rax          ; rax = a（基址）
    11c7:	add    %rdx,%rax                 ; rax = a + i*4
    11ca:	mov    (%rax),%eax              ; ★ eax = *(a + i*4) = a[i]
    11cc:	add    %eax,-0x8(%rbp)          ; s += a[i]
```

`lea 0x0(,%rax,4)` 算 `i*4`——**那個 `4` 就是元素大小，告訴你這是 `int` 陣列**。加上基址 `a`、解參考，就是 `a[i]`。`-O0` 把 base+index 拆成好幾步（先算 `i*4`、再加基址、再讀），但邏輯清楚。

`-O2` 常把它合成一條漂亮的 SIB。回顧 [Ch 4](./04-x86-64-for-reversers.md) 的 `g`：

```asm
  14:	mov    (%rdi,%rsi,8),%rax          ; rax = *(rdi + rsi*8) = p[i]（scale=8 → long/指標）
```

`(%rdi,%rsi,8)` 一條抵好幾條：`rdi`=基址、`rsi`=索引、`8`=元素大小。**看到 `(%base,%index,N)` 立刻讀成 `base[index]`，N 告訴你元素幾 bytes。** 這是最高頻的陣列指紋。

**struct 陣列**：若元素是 struct（如 16-byte），你會看到 `index*16`（或 scale 湊不出時用 `imul index, $16`）算出元素起點，再在其上用固定 offset 取欄位——`base + i*16 + 4` 就是「第 i 個元素的 @4 欄位」。struct 陣列 = array 定址（外層）套 struct 定址（內層），Ch 9 深入。

## 對比與取捨

| 資料種類 | 招牌定址 | 洩漏的資訊 | -O2 變化 |
|---|---|---|---|
| 局部變數/參數 | `-0xNN(%rbp)`（-O0）| 每 slot = 一個變數 | 多留暫存器、改用 `%rsp` |
| 全域/靜態 | `0xNN(%rip)` → `.data`/`.bss` | 讀+寫同址=被改的全域 | offset 不變（PIE 定址）|
| 字串/唯讀 | `lea 0xNN(%rip)` → `.rodata` | 位址被當參數傳=字串 | 不變 |
| 指標解參考 | `(%reg)` / `0xN(%reg)` | 括號=解參考 | 不變 |
| struct 欄位 | `base + 固定 offset` | offset=欄位位置、寬度=欄位大小 | **offset 不變**（ABI）|
| array 元素 | `(%base,%index,scale)` | **scale=元素大小** | 常合成一條 SIB |

## 踩雷集錦

1. **把 `lea` 當 `mov`（把位址當內容）**：`lea 0xNN(%rip), %rax` 是「把字串的**位址**放進 rax」，`mov 0xNN(%rip), %rax` 才是「把那位址的**內容**讀進 rax」。看到 `lea` 載字串位址別以為它讀了字串內容。
2. **忽略讀寫寬度而誤判欄位大小**：`mov 0x8(%rax), %eax`（讀 eax，4 bytes）和 `mov 0x8(%rax), %rax`（讀 rax，8 bytes）在還原 struct 時是「4-byte 欄位」vs「8-byte 欄位」的差別。只看 offset 不看寬度會把佈局還原錯。
3. **把 padding 當成有欄位**：struct 為對齊會插 padding。你看到欄位在 offset 0（int）、下一個在 offset 8（long），中間 4~7 是 padding 不是欄位——別硬塞一個不存在的欄位進去。對齊規則見 Ch 9。
4. **分不清「全域」和「唯讀常數」**：兩者都用 `0xNN(%rip)`。差別在目標落在哪個段：`.data`/`.bss` = 可寫全域，`.rodata` = 唯讀常數。用 `readelf -S` 對位址落點判斷，別一律當全域變數。
5. **把 scale 當成無關的立即數**：`(%rdi,%rsi,8)` 裡的 `8` 不是隨便的數，它**是元素大小**，直接告訴你陣列元素幾 bytes。忽略它就丟掉了型別線索。
6. **`-O0` 的 rbp-slot 直覺套到 `-O2`**：`-O2` 變數多在暫存器、slot 少且可能被 SSA 重排，同一個變數可能在不同時刻活在不同暫存器。別假設「一個 slot = 一個變數」在 `-O2` 還成立——那是 `-O0` 特權。

## 進階：再往深一層

- **反編譯器怎麼還原 struct**：Ghidra/IDA 會自動把「同一基址的多個 offset」聚合成一個 struct 型別（IDA 的「Create struct from offsets」、Ghidra 的 Auto Create Structure）。但它常猜錯欄位邊界、把 padding 當欄位、把 union 拆錯。Ch 8/Ch 9 專講反編譯器在型別上怎麼騙你，以及怎麼手動修正它的 struct。
- **`.bss` 為什麼 `NOBITS`**：`.bss` 存零初值全域，不佔檔案空間（載入時由 loader 清零），所以段表標 `NOBITS`。逆向時 `.bss` 裡的東西在靜態檔案裡「不存在內容」，你只能看到它的位址和大小——要看實際值得動態逆（Part 2）。
- **PIE 與絕對定址**：非 PIE 的老 binary（或 `-no-pie` 編的）會用絕對位址 `mov 0x404010, %eax` 而非 rip-relative。看到絕對位址就知道不是 PIE。ELF 載入細節見 [Ch 3](./03-elf-anatomy-and-loading.md)。
- **反查練習**：把本章的 struct/array 貼上 [Compiler Explorer](https://godbolt.org/)，改欄位順序、加 padding、換元素型別，看 offset 和 scale 怎麼變。這是把「offset↔欄位、scale↔元素大小」練成反射的最快路。

## 本章重點整理

- 四種資料家、四種招牌定址：局部/參數=`rbp/rsp-relative`、全域=`rip-relative`(.data/.bss)、唯讀/字串=`lea rip`(.rodata)、指標/heap 物件=`(%reg)`。
- **struct 逆向**：收集同一基址的所有固定 offset → 重建欄位表；讀寫寬度給欄位大小、`movslq`/`movzx` 給 signedness。**offset 不受優化影響，是最穩的錨點。**
- **array 逆向**：`(%base,%index,scale)`，**scale 直接洩漏元素大小**（4=int、8=long/指標、1=char）。
- **全域 vs 唯讀常數**都用 `0xNN(%rip)`，靠目標落在 `.data`/`.bss`（可寫）還是 `.rodata`（唯讀）區分；`readelf -S/-s` 對位址查身分。
- **字串是逆向第一線索**：`lea rip → 當參數傳` = 字串位址；`strings` + `objdump -s -j .rodata` 直接洩漏程式意圖。
- **括號 = 解參考**：`(%reg)` 用內容、無括號用指標本身；多層括號 = 追指標鏈。

## 自我檢核

- [ ] 我看到程式反覆用 `(%rax)`、`0x4(%rax)`、`0x8(%rax)` 能重建出「struct 有欄位在 offset 0/4/8」並從讀寫寬度推欄位大小
- [ ] 我看到 `(%rdi,%rsi,8)` 知道這是 `array[index]` 且元素是 8 bytes（long/指標）
- [ ] 我看到 `0x2e22(%rip)` 配 objdump 註解，能判斷它是全域變數，並知道 stripped 後要靠 readelf 查身分
- [ ] 我能靠目標落在 `.data`/`.bss` vs `.rodata` 區分「可寫全域」和「唯讀常數」
- [ ] 我看到 `lea 0xNN(%rip), %rax` 後接傳參，知道那是在載入字串位址而非讀內容
- [ ] 我看到 `movslq` 知道那個欄位是 signed（若是 `movzx` 則 unsigned）

## 延伸閱讀

### 書籍

- **《Reverse Engineering for Beginners》(RE101)** — Dennis Yurichev（[免費](https://beginners.re/)）
  - **定位**：資料佈局 ↔ asm 的最佳題庫。
  - **讀哪裡**：`Arrays`、`Structures`、`Pointers to functions`、`Strings` 各章——每種資料結構都有 asm 對照，把本章的 offset/scale 推理練成反射。
- **《Practical Binary Analysis》** — Dennis Andriesse（No Starch, 2019）
  - **定位**：把資料流分析系統化。
  - **讀哪裡**：Ch 6（disassembly 與資料）與後面 data-flow 分析章，補足「怎麼自動追蹤一個值/指標流向」的方法。

### 官方文件 / 工具

- **[Compiler Explorer (godbolt.org)](https://godbolt.org/)**
  - **這是什麼**：即時看 struct/array 佈局 ↔ 定址。改欄位順序、換型別，看 offset 和 scale 怎麼變。
  - **怎麼用**：貼本章 `struct Point`，加一個 `char` 欄位或調換順序，觀察 padding 和 offset 的變化。
- **[readelf / objdump（binutils）手冊](https://sourceware.org/binutils/docs/)**
  - **這是什麼**：本章用到的段表（`readelf -S`）、符號表（`readelf -s`）、段內容（`objdump -s -j`）的完整選項。
  - **怎麼用**：查「怎麼 dump 某個段的原始 bytes」「怎麼列 .data 的內容」——逆向資料時反覆會用。段與載入的完整原理見本課 [Ch 3](./03-elf-anatomy-and-loading.md)。

資料和控制流都認得了，下一章我們回到「函式」這個單位：怎麼從 prologue/epilogue 認出函式邊界、從暫存器使用反推參數個數與型別、從自我呼叫認出遞迴——以及最難的，怎麼在 `-O2` 裡認出「這裡本來是一個函式，但被 inline 蒸發了」。

→ [Ch 7 認出函式：prologue / 參數 / inline 痕跡](./07-recognizing-functions.md)
