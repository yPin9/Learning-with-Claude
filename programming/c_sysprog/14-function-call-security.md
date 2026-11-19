# Ch 14 — 函式呼叫與資安：buffer overflow、canary 與 ROP

> **目標**：理解為什麼 stack 的結構讓「一次越界寫入」就能威脅控制流，並用工具親眼觀察每一層防禦（canary、FORTIFY_SOURCE、ASan、NX、ASLR/PIE、RELRO、CET）實際攔下了什麼、沒攔下什麼；同時掌握 heap 上 use-after-free 與 double free 的成因與偵測。這是一章**防禦教育章**：重點是看懂機制與如何偵測、預防，不是教你怎麼攻擊。

> **對應原文**：[你所不知道的 C 語言：函式呼叫篇](https://hackmd.io/@sysprog/c-function) 的「為何理解函式呼叫機制攸關資訊安全」「Why Segmentation fault?」「ROP 攻擊」「解決方案」「藏在 Heap 裡的細節」等節

> **環境**：gcc 11.4、gdb 12.1，glibc 2.35，WSL2 Ubuntu 22.04 x86-64（kernel 6.18）。Ubuntu 的 gcc 預設啟用 `-fstack-protector-strong`、PIE、`-fcf-protection`，以及（`-O1` 以上的）`-D_FORTIFY_SOURCE=2`，所以「預設旗標」本身就是一套防禦；下面會用旗標逐項關掉來觀察差異。

> **使用規範**：本章所有實驗程式都是**故意有漏洞的教學程式**，只在你自己擁有的環境（自己的 WSL／VM）執行。本章**不提供**可用的攻擊輸入（payload）、gadget 位址或串接步驟；ROP 只講原理與防禦對應。原文後半的 ROP 實作實驗（pwntools、ROPgadget、組裝 `execve` 的呼叫鏈）我們刻意不複製，想實作請在 CTF 靶場或自己架設的實驗環境，並搭配 ROP 論文閱讀。

## 為什麼需要這個？

1988 年的 Morris worm 利用 `fingerd` 呼叫 `gets()` 的 stack 緩衝區溢位散布；1996 年 Aleph One 在 *Phrack* 發表〈Smashing the Stack for Fun and Profit〉，把這種攻擊寫成了公開的教學文件。三十年過去，記憶體安全錯誤依然是 C/C++ 軟體漏洞的主要來源之一。防禦技術則是一路加層：

| 年代（約） | 攻擊 | 防禦 |
|---|---|---|
| 1988–1996 | 蓋掉返回位址、跳到自己放在 stack 上的機器碼 | （幾乎沒有） |
| 1998 起 | 同上 | stack canary（StackGuard）、不可執行 stack（NX／W^X，PaX 與 OpenBSD 等先行） |
| 2000 年代初 | 改為跳到 libc 裡既有的函式（return-to-libc） | ASLR（位址隨機化） |
| 2007 起 | 串接既有程式碼片段，不需要注入任何機器碼（ROP） | PIE、更完整的 ASLR、CFI、CET shadow stack |

這張表的重點是**節奏**：每一層防禦都是針對上一層攻擊的缺口而設計。理解函式呼叫機制（[Ch 11](./11-process-layout-and-abi.md)）是看懂這些防禦「為何有效、為何有限」的前提，這也是原文把這一章放在函式呼叫篇的理由。

## 先建立直覺

把 stack frame 想成一排**相鄰的格子**。陣列往高位址填，而返回位址就在更高處：

```
   高位址
   ┌──────────────────────┐
   │ 返回位址 (ret)       │ ← 決定 `ret` 指令跳去哪裡
   ├──────────────────────┤
   │ saved rbp            │
   ├──────────────────────┤
   │ canary（若有）        │ ← 「警報線」
   ├──────────────────────┤
   │ char buf[16]         │ ← strcpy 從這裡的低位址開始，一路往高位址寫
   └──────────────────────┘
   低位址
```

`strcpy`、`gets`、`memcpy` 這類函式**不知道** `buf` 有多大，只管一路寫到來源結束。輸入比 16 byte 長，多出來的就會依序蓋過警報線、saved rbp、返回位址。**函式返回時，CPU 無條件相信 stack 上那個值**，這就是整個問題的根源。

這個比喻有個限制：它畫的是 `-O0` 且沒有重排的情況。gcc 在開啟 stack protector 時會**重排區域變數**（把陣列放在靠近 canary 的位置、純量放在較低處）；而且不是所有越界寫入都會往返回位址的方向（見後面 intra-object 的例子）。

> 如果你對 prologue/epilogue、`rbp`、返回位址不熟，先回看 [Ch 11 — 用 gdb 逐步看一次呼叫](./11-process-layout-and-abi.md)。

## 成因：一個故意有漏洞的程式

`vuln.c`（只在自己的環境編譯與執行）：

```c
void greet(const char *name)
{
    char buf[16];
    strcpy(buf, name);                 /* name 超過 15 字元就越界寫入 */
    printf("Hello, %s\n", buf);
}
int main(int argc, char **argv) { /* ... */ greet(argv[1]); puts("greet() returned normally"); return 0; }
```

先用正常輸入，再用 40 個 `A`（遠超過 16 bytes），並逐項改變編譯旗標。以下是同一支程式的 6 種設定（`$LONG` 為 40 個 `A`；因為管道使 stdout 全緩衝，被中止的程式的 `Hello` 那行可能沒印出來，是正常現象）：

```
### 1. Ubuntu 預設旗標（-O0）
$ ./v_default hello
Hello, hello
greet() returned normally
$ ./v_default "$LONG"
*** stack smashing detected ***: terminated        ← exit=134（SIGABRT）

### 2. 關掉 canary / PIE / FORTIFY / CET（-fno-stack-protector -no-pie -fcf-protection=none -U_FORTIFY_SOURCE）
$ ./v_off "$LONG"
Segmentation fault (core dumped)                   ← exit=139

### 3. 只關 canary（其餘預設）
Segmentation fault (core dumped)

### 4. -O2（FORTIFY_SOURCE=2 生效）
*** buffer overflow detected ***: terminated       ← exit=134

### 5. -O2 -fno-stack-protector（只靠 FORTIFY）
*** buffer overflow detected ***: terminated

### 6. ASan（-fsanitize=address）
==28739==ERROR: AddressSanitizer: stack-buffer-overflow on address 0x7ffd90f85a10 ...
WRITE of size 41 at 0x7ffd90f85a10 thread T0
    #0 ... in __interceptor_strcpy
    #1 ... in greet /home/ypp/c_sysprog_lab/p2b/vuln.c:8
    ...
  This frame has 1 object(s):
    [32, 48) 'buf' (line 7) <== Memory access at offset 48 overflows this variable
```

同一個 bug，**四種不同的結局**：沒防護時 SIGSEGV（程式崩潰在遠處，你很難知道原因）；canary 在函式返回時攔下；FORTIFY 在 `strcpy` 呼叫當下攔下；ASan 給出最完整的診斷（哪一行、哪個變數、溢出了幾 byte）。

把無防護的版本放進 gdb，看看「崩潰在遠處」是什麼意思：

```
$ gdb -q -batch -x g7.gdb ./v_off      # run AAAA...（40 個 A）
Program received signal SIGSEGV, Segmentation fault.
0x0000000000401192 in greet (name=0x7fffffffe4bf 'A' <repeats 40 times>) at vuln.c:10
=> 0x401192 <greet+60>:	ret
$1 = (void *) 0x7fffffffe0e8
0x7fffffffe0e0:	0x4141414141414141	0x4141414141414141
#0  0x0000000000401192 in greet (...) at vuln.c:10
#1  0x4141414141414141 in ?? ()
```

停在 `ret` 指令上：這時 `rsp` 指向的返回位址已經被 `0x4141414141414141`（`AAAAAAAA`）覆蓋，CPU 要跳到一個非 canonical 的位址，於是 SIGSEGV。原文用 `gets()` 示範了同樣的現象（並接著示範導向 `evil()`）；我們到此為止：**確認「返回位址能被輸入覆蓋」就是 stack buffer overflow 的本質**，怎麼利用它不是本章的目的。

> 原文說 `gets()` 在 C99 標為 deprecated、C11 移除，GCC 14 對 implicit declaration 報錯。這點方向正確（C11 確實移除了 `gets`），但請記得 gcc 11.4 對隱含宣告只是警告（見 [Ch 11](./11-process-layout-and-abi.md)）；本章改用 `strcpy`，因為它是現實中更常見、也更不容易被工具一眼看出的越界來源。

## 防禦一：Stack Canary

**canary**（金絲雀，礦坑裡用來預警毒氣的鳥）是 StackGuard（Cowan 等人，USENIX Security 1998）提出的做法：函式進入時在返回位址之前放一個**隨機值**，返回前檢查它有沒有被改變。`greet` 在預設旗標的 `-O0` 組語（gdb Intel 語法）：

```asm
   <+4>:   push   rbp
   <+5>:   mov    rbp,rsp
   <+8>:   sub    rsp,0x30
   <+16>:  mov    rax,QWORD PTR fs:0x28          ; 從 TLS 讀出這個行程的 canary
   <+25>:  mov    QWORD PTR [rbp-0x8],rax        ; 放在 saved rbp 正下方
   <+29>:  xor    eax,eax                        ; 清掉暫存器中的 canary 副本
   ...                                           ; strcpy、printf
   <+78>:  mov    rax,QWORD PTR [rbp-0x8]        ; 返回前取回
   <+82>:  sub    rax,QWORD PTR fs:0x28          ; 和原值比對
   <+91>:  je     <greet+98>                     ; 一樣就正常返回
   <+93>:  call   __stack_chk_fail@plt           ; 不一樣：印訊息並 abort
   <+98>:  leave
   <+99>:  ret
```

在這個 frame 裡（`[rbp-0x20]` 是 `buf`）：`buf` → 8 bytes 填充 → canary（`rbp-8`）→ saved rbp（`rbp`）→ 返回位址（`rbp+8`）。連續的越界寫入想碰到返回位址，**必須先蓋過 canary**。

canary 本身用 gdb 看：

```
(gdb) x/gx $fs_base+0x28
0x7ffff7fad768:	0x066a18f78a00f100
```

它的幾個設計細節：

- **每個行程啟動時隨機產生**（核心經 auxiliary vector 的 `AT_RANDOM` 提供隨機位元組，C 啟動碼放進 `fs:0x28`），所以不同次執行值不同。
- **最低位元組是 `0x00`**（`...00f100` 的最後 `00`）。字串函式遇到 NUL 會停止，這讓「用字串函式連同 canary 一起覆寫成正確值」更困難：攻擊者必須在不能出現 NUL 的字串裡寫出含 NUL 的值。
- 存在 TLS（`fs:` 段）而非全域變數，是為了避免被任意位址寫入輕易覆寫，且各執行緒各有一份。

### 哪些函式會被保護？

gcc 有四種等級。我們用 `canary_which.c`（四個函式：純量、取了區域變數位址、32 byte 陣列、4 byte 陣列）在 `-O0` 數每個函式的 `__stack_chk_fail` 呼叫：

| 旗標 | `f_scalar` | `f_addr_taken` | `f_array`（32B） | `f_small_array`（4B） |
|---|---|---|---|---|
| `-fno-stack-protector` | 0 | 0 | 0 | 0 |
| `-fstack-protector` | 0 | 0 | 1 | 0 |
| `-fstack-protector-strong` | 0 | 1 | 1 | 1 |
| `-fstack-protector-all` | 1 | 1 | 1 | 1 |

（數字是該函式組語中 canary 檢查的個數，`1` 表示有保護。）`-fstack-protector`（不含 strong）只保護含「8 byte 以上字元陣列」的函式，所以 4 byte 陣列沒被保護；`strong` 再加上任何陣列、以及取了區域變數位址的函式（因為位址可能被傳去給別人寫）；`all` 連純量函式都保護。Ubuntu 預設 `strong`，是「覆蓋率」與「效能」的折衷：每次函式呼叫多兩次記憶體存取加一次比較。

### canary 不是萬能的

- **只在函式返回時檢查**：在 `greet` 返回之前，越界已經發生，若越界蓋到的是**別的區域變數或函式指標**，canary 看不到。
- **只擋連續寫入**：如果程式有「可以指定任意位址寫入」的漏洞（例如格式化字串漏洞的 `%n`），攻擊者可以跳過 canary 只寫返回位址。
- **資訊洩漏可以繞過**：如果程式能被誘導把 stack 內容讀出來（越界讀取、格式化字串），canary 值洩漏後就失去意義。
- **fork 型伺服器**：子行程繼承父行程相同的 canary，被反覆當作「猜測 oracle」逐 byte 暴力還原（理論上每個 byte 最多 256 次嘗試）。這是經典的設計議題；防禦做法是子行程重新 `exec`，或監控異常崩潰。

最後一點可以用一個不涉及返回位址的例子實測：**同一個 struct 內**的越界（`intra.c`）。

```c
struct login { char name[8]; int is_admin; };
/* ... memcpy(s.name, argv[1], strlen(argv[1]) + 1);  輸入 11 個 'B'  */
```

```
--- 預設旗標（有 canary）：      name=BBBBBBBB is_admin=4342338
--- ASan + UBSan：               name=BBBBBBBB is_admin=4342338     （沒有任何報錯）
--- -O2（FORTIFY_SOURCE=2）：    name=BBBBBBBB is_admin=4342338
```

`is_admin` 被改成了 `0x424242`（三個 `B` 加上結尾的 NUL），**三種防護都沒有攔下**。這個案例的教訓：返回位址只是控制流的其中一個目標，**資料本身（權限旗標、長度、指標）被越界寫入一樣危險**，而這是編譯器與工具最難偵測的一類（ASan 的 redzone 放在物件之間，不在 struct 內部）。FORTIFY 在這個案例為何沒觸發，我沒有深究內部原因，只記錄觀察結果。最可靠的防禦是**寫對邊界檢查**：用 `snprintf`、`memcpy` 前先比較長度、用帶長度的字串型別。

## 防禦二：FORTIFY_SOURCE 與 `*_chk`

`-D_FORTIFY_SOURCE=2`（Ubuntu 在 `-O1` 以上預設啟用）把 `strcpy`、`memcpy`、`sprintf` 等替換成 `__strcpy_chk` 等帶有「目標緩衝區大小」引數的版本，大小由 `__builtin_object_size()` 在編譯期推得。上面案例 4 與 5 的 `*** buffer overflow detected ***: terminated` 就是 `__strcpy_chk` 在 `strcpy` 之前發現 `strlen(src) >= 目的地大小` 而 abort，**比 canary 更早**（在寫入發生當下，而不是返回時）。

它的限制：只在編譯期**能知道目的地大小**時有效（區域陣列、已知大小的 `malloc`）；對來源不明大小的指標（如函式參數 `char *dst`）沒有幫助；`-O0` 不啟用（預設要 `-O1` 以上，所以案例 1 沒有這個訊息）。我們可以用 `chk.sh` 的「FORTIFY」一欄觀察一個執行檔有沒有引用 `*_chk` 符號。

## 防禦三：AddressSanitizer

ASan（Google，Serebryany 等，USENIX ATC 2012）在編譯期為每次記憶體存取插入檢查，並在執行期維護 **shadow memory**：每 8 bytes 的真實記憶體對應 1 byte 的 shadow，記錄這 8 bytes 哪些可以存取。物件之間放 **redzone**（毒化的間隔區）。上面的報告可以這樣讀：

```
Address 0x7ffd90f85a10 is located in stack of thread T0 at offset 48 in frame
  This frame has 1 object(s):
    [32, 48) 'buf' (line 7) <== Memory access at offset 48 overflows this variable
Shadow bytes around the buggy address:
=>0x1000321e8b40: 00 00[f3]f3 00 00 ...
```

`buf` 佔 frame 的 `[32, 48)`，第 48 byte 是 redzone，被 `strcpy` 寫到所以報錯；shadow 中 `f1` 是 stack 左側 redzone、`f3` 是右側 redzone、`00` 是完全可存取。ASan 的記憶體與執行時間都有數倍等級的額外成本（確切倍率隨程式而異，請看 ATC 論文的評估與你自己的實測），所以**用在開發與測試（CI、fuzzing），不是正式環境**。它是本課每章要求「用 `-fsanitize=address,undefined` 再跑一次」的原因：比 canary 更早、更精確。

## 防禦四：NX（不可執行的資料頁）

早期攻擊把機器碼放在輸入裡，蓋掉返回位址使其指向 stack 上的緩衝區。**NX**（No-eXecute，Windows 稱 DEP，通稱 W^X：一頁記憶體可寫或可執行，但不同時）讓 CPU 在頁表層級拒絕執行資料頁。我們用最無害的「機器碼」：一個 `0xC3` 位元組（x86 的 `ret`），放到不同位置，再呼叫它（`nx.c`）：

```
### 預設 (NX)：data（全域陣列，.data）
呼叫位於 .data 的 ret (位址 0x5e96fdc9c010)...      → SIGSEGV（exit=139）
### 預設 (NX)：stack（區域陣列）
呼叫位於 stack 的 ret (位址 0x7ffd10389257)...       → SIGSEGV
### 預設 (NX)：heap（mmap 的 RW 頁）
呼叫位於 mmap(RW) 的 ret (位址 0x74c853223000)...    → SIGSEGV
### 預設 (NX)：rx（mmap 為 RW，寫入後 mprotect 成 R+X）
呼叫位於 mmap->RX 的 ret (位址 0x7d9d8168b000)... 成功返回
### -z execstack：stack
呼叫位於 stack 的 ret (位址 0x7ffe7efe5867)... 成功返回
### -z execstack：data
→ SIGSEGV（只有 stack 被放寬）
```

重點：

1. **三個「可寫」的位置都不能執行**；先寫入、再用 `mprotect` 改成可執行才行，這正是 JIT 編譯器的標準做法（見 [Ch 38](./38-compiler-construction.md)、[練習 E](./practice-e-malloc-tracer-and-bf-jit.md) 的 Brainfuck JIT）。
2. `-z execstack` 在 ELF 的 `GNU_STACK` program header 上標 `RWE`，只放寬 stack（在本環境 `.data` 仍不可執行）。`chk.sh` 的 NX 欄就是讀這個標記。**除非有明確理由，永遠不要在發行版加 `-z execstack`**。

## 防禦五：ASLR、PIE 與 RELRO

**ASLR**（見 [Ch 11](./11-process-layout-and-abi.md)）隨機化 stack、heap、共享函式庫的基底；**PIE** 讓主程式本身也能被隨機載入（`readelf -h` 的 Type 為 `DYN` 而非 `EXEC`）。它們共同使「已知程式碼在記憶體的哪裡」變成攻擊者必須先猜的資訊。限制：

- 一個**位址洩漏**（例如印出指標、越界讀取）就能算出整個函式庫的基底，因為隨機化是以**整個區段**為單位平移，區段內相對位置固定。
- 隨機的位元數有限（取決於架構與核心設定），32 位元系統的熵遠小於 64 位元，就我所知可被暴力猜測；本課只討論 x86-64。
- 預設值由 `/proc/sys/kernel/randomize_va_space` 控制，本機為 `2`（含 heap 的完整隨機化）。

**RELRO**（RELocation Read-Only）處理另一個目標：動態連結時 GOT（Global Offset Table，一張**函式位址表**，見 [Ch 40](./40-dynamic-linking.md)）預設是可寫資料，覆寫其中一項就能讓下一次對該函式的呼叫跳到別處。`-Wl,-z,relro` 讓載入器在啟動後把 GOT 以外的重定位區段改成唯讀（Partial RELRO）；再加 `-z now`（Full RELRO）讓所有符號在啟動時解析完畢，整個 GOT 也能唯讀（代價是啟動稍慢）。

## 防禦六：CET——硬體層的控制流保護

Intel 的 **CET**（Control-flow Enforcement Technology）有兩部分，對應原文提到的內容：

- **IBT**（Indirect Branch Tracking）：間接 `call`/`jmp` 的目標必須是 `endbr64` 指令，否則 CPU 觸發例外。這就是 [Ch 11](./11-process-layout-and-abi.md) 看到的、每個函式開頭的 `endbr64`。
- **SHSTK**（Shadow Stack）：CPU 另外維護一條**只能被 `call`/`ret` 寫入**的 shadow stack，`call` 時把返回位址同時存兩份，`ret` 時比對，不一致就例外。一般的 `mov` 指令無法寫入它，所以覆寫一般 stack 上的返回位址沒有用。

`gcc -fcf-protection=full`（Ubuntu 預設）會讓產生的 ELF 帶上這兩項**支援標記**，我們可以讀它：

```
$ bash chk.sh c_default      # 預設旗標 -O2
  CET    : IBT SHSTK
$ bash chk.sh c_weak         # -fcf-protection=none
  CET    : 無 CET 標記
```

這只是「程式宣稱它相容」。要實際享受 shadow stack 保護，需要 **CPU、核心、C 函式庫三者都支援**：就我所知，Linux 的 x86 使用者態 shadow stack 支援是 kernel 6.6 起、glibc 2.39 起，所以**本機（glibc 2.35）的程式並未真正啟用 shadow stack，本段保護效果未實測**；你在 [Ch 12](./12-setjmp-and-coroutines.md) 看過的 `__longjmp` 裡的 `rdsspq`/`incsspq` 指令，就是 glibc 為了讓 `longjmp` 在 shadow stack 啟用時也能運作而寫的。

## ROP：為什麼擋住「執行資料」還不夠

**ROP**（Return-Oriented Programming，Shacham，CCS 2007）的原理是這樣一個觀察：

> NX 禁止「執行新放進去的碼」，但沒禁止「把既有的碼用新的順序執行」。

返回位址是**資料**（在可寫的 stack 上），而每次 `ret` 都會從資料取出下一個要執行的位置。如果程式（連同它載入的函式庫）裡已經有大量以 `ret` 結尾的**短指令片段**，攻擊者不必注入任何機器碼，只要在 stack 上安排一串位址，就能讓這些既有片段依序執行，而每個片段本身都在 `r-x` 頁、完全合法。Shacham 等人證明，在足夠大的程式碼庫（如 libc）裡，這樣的片段集合是圖靈完備的。

我們**不展開**怎麼找這些片段、怎麼排列。請記住攻擊成立的**前提**，因為每一層防禦都對準其中一個前提：

| ROP 要成立，需要…… | 針對它的防禦 | 為什麼有效 | 局限 |
|---|---|---|---|
| 能覆寫 stack 上的返回位址 | canary、`FORTIFY`、ASan、安全的程式寫法 | 在覆寫發生時或返回前攔截 | 不涵蓋非連續寫入、資訊洩漏 |
| 知道既有程式碼的位址 | ASLR + PIE | 位址每次執行不同 | 位址洩漏可算出基底 |
| `ret` 無條件相信 stack 上的值 | CET shadow stack | 返回位址在另一條只有 `call/ret` 能寫的 stack 上比對 | 需要 CPU、核心、libc 配合 |
| 能把控制流轉給任意位置（含間接 `call`/`jmp`） | CET IBT、編譯器 CFI（如 clang 的 `-fsanitize=cfi`） | 間接跳轉目標必須是合法的函式入口／型別相符 | 粒度有限，仍可能重用「合法入口」 |

這張表也解釋了為什麼**單一防禦不夠**：每一層都有已知的繞過方式，但需要同時繞過多層，攻擊成本顯著提高。這就是縱深防禦（defense in depth）。上一章你看到 `setjmp` 把 `rsp` 與返回位址做 pointer mangling，也是同一思路：即使 `jmp_buf` 被覆寫，攻擊者也無法直接寫入有意義的位址。

### 歷史案例：CVE-2015-7547

glibc 的 DNS 解析器 `getaddrinfo` 在處理特定大小的回應時，存在 stack 上緩衝區的越界寫入（CVE-2015-7547，2016 年公開並修補），影響幾乎所有使用 glibc 的 Linux 系統。它是個很好的警示：**漏洞存在於每個程式都會呼叫的基礎函式庫**，單靠應用程式層級的編碼謹慎無法避免，所以系統層級的防禦（ASLR、canary、NX）不可或缺。詳細時間線與影響範圍請參閱 Red Hat 與 Google 的公開通告。

### 觀察：一個迷你 checksec

沒有裝 `checksec` 也能用 `readelf` 讀出這些保護狀態。`chk.sh`（只讀 ELF，不執行目標程式）檢查：PIE（`Type: DYN`）、NX（`GNU_STACK` 的 flags）、RELRO（`GNU_RELRO` + `BIND_NOW`）、canary（有沒有引用 `__stack_chk_fail`）、FORTIFY（有沒有 `*_chk` 符號）、CET（`.note.gnu.property` 的 IBT/SHSTK）。對三種建置：

```
== c_default（gcc -O2，Ubuntu 預設）
  PIE    : PIE (ASLR 可涵蓋主程式)
  NX     : NX 開啟 (stack 不可執行, flags=RW)
  RELRO  : Full RELRO
  Canary : 有 canary (引用 __stack_chk_fail)
  FORTIFY: 有 FORTIFY (引用 *_chk)
  CET    : IBT SHSTK
== c_weak（-fno-stack-protector -no-pie -fcf-protection=none -D_FORTIFY_SOURCE=0 -z norelro -z execstack）
  PIE    : No PIE (固定載入位址)
  NX     : NX 關閉 (stack 可執行, flags=RWE)
  RELRO  : No RELRO
  Canary : 沒有 canary
  FORTIFY: 沒有 FORTIFY 呼叫
  CET    : 無 CET 標記
== c_hard（-fstack-protector-all -pie -z relro,-z now -fcf-protection=full）
  （與 c_default 的七項結果相同）
```

結論：在 Ubuntu 22.04 上，不加任何特殊旗標，你的程式就已經有 PIE、NX、Full RELRO、canary、FORTIFY、CET 標記。要觀察「沒有防護時的原始行為」，必須主動把它們關掉（如 `c_weak`）。這也是為什麼原文的實驗要加一長串 `-fno-stack-protector -fcf-protection=none -no-pie`。

## 藏在 Heap 裡的細節

stack 的漏洞改的是控制流；heap 的漏洞（use-after-free、double free）改的常是**資料與配置器的內部結構**。先看 glibc 的 chunk 長什麼樣（細節在 [Ch 17 — malloc 實作](./17-malloc-internals.md)，這裡只看觀察得到的部分）。`heap.c` 的 `chunk` 模式：

```
malloc(24) a   p=0x5ba1fe99d2a0  size 欄位 = 0x21  (p-8)
malloc(24) b   p=0x5ba1fe99d2c0  size 欄位 = 0x21  (p-8)
free(a) 之後 a[0] = 0x5ba1fe99d, a[1] = 0x4c5927693c5ed5d1
(uintptr_t)a >> 12 = 0x5ba1fe99d
free(b) 之後 b[0] = 0x5ba444863b3d, 預期 = (b>>12) ^ a = 0x5ba444863b3d
```

（位址每次執行不同。）逐項解讀：

- `p-8` 處的 **size 欄位 = `0x21`**：`malloc(24)` 實際配置 32 bytes（`0x20`）的 chunk（24 bytes 資料加 8 bytes 標頭，向上對齊到 16），最低位元的 `1` 是 `PREV_INUSE` 旗標。`free()` 不需要你告訴它大小，就是因為每塊記憶體前面都記錄著自己的大小。相鄰兩個 chunk 的位址差 `0x20`，與 size 一致。
- **`free` 之後，原本的使用者資料區被配置器拿去存管理資訊**：glibc 2.35 的 tcache（每執行緒的小型 free list）把 chunk 串成單向鏈結串列，前 8 bytes 存「下一個 free chunk 的位址」，但經過 **safe-linking**（glibc 2.32 起，就我所知）處理：存的是 `next XOR (p >> 12)`。第一個被 free 的 `a`，`next` 是 0，所以 `a[0] == a >> 12`，與印出的 `0x5ba1fe99d` 相符；第二個被 free 的 `b`，`next` 是 `a`，`b[0] == (b >> 12) ^ a`，實測吻合。
- 第二個 8 bytes（`a[1]`）是 tcache 的 **key**，用來偵測 double free（glibc 2.34 起為隨機值，就我所知）。

### double free：偵測

```c
char *p = malloc(24);
free(p);
free(p);      /* 第二次 free 同一個指標 */
```

```
$ ./heap double
第一次 free 完成，準備第二次 free(p)
free(): double free detected in tcache 2          ← exit=134（SIGABRT）
```

glibc 在 `free` 時檢查 chunk 的 key 欄位，發現這個 chunk 已經在 tcache 裡，就 abort。**這是「儘力而為」的偵測**：如果兩次 `free` 中間又有其他 `free` 把它擠到別的 bin、或 key 被覆蓋，檢查可能不觸發。原文說的「`free` 後把指標設為 NULL」仍然是最簡單的預防：

```
$ ./heap null
free(NULL) 沒事
```

`free(NULL)` 是合法的、什麼都不做（N1570 7.22.3.3p2）。ASan 對 double free 給出完整報告，包含「第一次 free 在哪一行、當初在哪裡配置」：

```
==29266==ERROR: AddressSanitizer: attempting double-free on 0x503000000040 in thread T0:
    #1 ... in main heap.c:52
0x503000000040 is located 0 bytes inside of 24-byte region [0x503000000040,0x503000000058)
freed by thread T0 here:
    #1 ... in main heap.c:49
previously allocated by thread T0 here:
    #1 ... in main heap.c:48
```

### use-after-free：舊指標看到新物件

`free(p)` 之後，`p` 這個變數**還保留著原來的位址**（變成 *dangling pointer*），而記憶體早已可以被分給別人。`heap.c` 的 `uaf` 模式模擬一個登入工作階段：

```c
struct session *s1 = malloc(sizeof *s1);  strcpy(s1->user, "alice");   s1->is_admin = 0;
free(s1);                                           /* s1 變成 dangling pointer */
struct session *s2 = malloc(sizeof *s2);            /* 同大小：tcache 立刻把剛 free 的 chunk 還給你 */
strcpy(s2->user, "mallory");  s2->is_admin = 1;
printf("透過 dangling s1 讀：user=%s is_admin=%d\n", s1->user, s1->is_admin);
```

```
s1 = 0x62aa193092a0  user=alice is_admin=0
s2 = 0x62aa193092a0  user=mallory is_admin=1
透過 dangling s1 讀：user=mallory is_admin=1   <- 讀到的是別人的資料
```

`s1` 與 `s2` 是**同一個位址**：tcache 是後進先出，剛 free 的 chunk 馬上被下一個同大小的 `malloc` 取走。舊指標與新指標指向同一個物件（*aliasing*），程式以為在操作「舊物件」，其實改的是新物件。**如果新物件裡存的是函式指標、權限旗標或長度，後果就是邏輯錯誤甚至控制流錯誤**。UAF 比 double free 更危險，因為它**不會立刻崩潰**，讀到的往往是看似合理的資料。ASan 的偵測方式是：`free` 之後把整塊記憶體標為毒化並放進**隔離區**（quarantine，暫時不重用），之後任何讀寫都被攔下：

```
==29264==ERROR: AddressSanitizer: heap-use-after-free on address 0x503000000050 at pc ...
READ of size 4 at 0x503000000050 thread T0
    #0 ... in main heap.c:44
0x503000000050 is located 16 bytes inside of 20-byte region [0x503000000040,0x503000000054)
freed by thread T0 here:   #1 ... in main heap.c:39
previously allocated by thread T0 here:   #1 ... in main heap.c:36
```

報告精確指出「在哪裡讀、這塊記憶體第幾個 byte、在哪裡 free、在哪裡配置」，這是沒有工具的情況下幾乎不可能手動推得的資訊。

### 大陣列：VLA、malloc 與 calloc

原文的謎題：`long arr[n + 1]`（n 約 10⁷）會 segfault，改 `malloc` 卻正常，為什麼？實測（`vla.c`，n = 10⁷，80,000,000 bytes ≈ 76 MiB）：

```
vla:    exit=139（SIGSEGV）
malloc: malloc ok
calloc: calloc ok
```

原文的解釋是「作業系統衡量要求的記憶體足不足夠」。更精確地說：VLA 配置在 **stack** 上，上限是 `RLIMIT_STACK`（本機 8 MiB），76 MiB 遠超過，所以程式一寫入超過 stack 區域就 SIGSEGV；`malloc` 這麼大的區塊，glibc 直接向核心 `mmap`（`strace` 看到 `mmap(NULL, 80003072, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANONYMOUS, ...)`），核心只配置虛擬位址空間，實際的實體頁面在第一次寫入時才由 page fault 配置（lazy allocation）。本機 `/proc/sys/vm/overcommit_memory` 為 `1`（總是允許 overcommit；WSL 的設定，標準 Linux 預設為 `0` 的啟發式），所以 `malloc` 不會因為「要太多」而失敗，真正的風險是之後實體記憶體不足時的 OOM killer。原文「stack segfault 是正確的錯誤提醒」這個結論正確：**VLA 的大小由外部輸入決定時，是一個安全問題**（可造成 stack overflow，甚至是跳過 guard 頁面的 *stack clash*），所以 C11 起把 VLA 物件列為可選特徵，不少專案（包括 Linux 核心）已全面禁用。

`calloc` 與 `malloc` + `memset` 的差別：新 `mmap` 來的頁面本來就是零，glibc 的 `calloc` 就我所知會對這種剛 `mmap` 的區塊略過 `memset`；而 `malloc` + `memset` 必然逐頁寫入，把整個區塊都觸碰到。`calloc(n, size)` 還會檢查 `n * size` 的**乘法溢位**（回傳 `NULL`），這是手寫 `malloc(n * size)` 常漏掉的安全檢查。

## 對比與取捨

| 防禦 | 保護對象 | 成本 | 主要局限 |
|---|---|---|---|
| canary | 返回位址（連續越界） | 每次呼叫幾個指令 | 不護資料、不擋任意位址寫入、可被洩漏繞過 |
| FORTIFY_SOURCE | `strcpy`/`memcpy` 等已知大小的越界 | 幾乎沒有 | 需 `-O1`+、需要編譯期可知的大小 |
| ASan | 越界、UAF、double free | 記憶體與時間都數倍 | 只用在開發與測試 |
| NX | 注入的機器碼 | 無 | 擋不住程式碼重用（ROP） |
| ASLR + PIE | 位址已知的假設 | 少量 | 位址洩漏、低熵平台 |
| Full RELRO | GOT 覆寫 | 啟動稍慢 | 只保護 GOT |
| CET | 返回位址竄改、間接跳轉 | 需要硬體 | 需 CPU/核心/libc 三者配合 |
| 寫對程式（邊界檢查、`snprintf`） | **全部** | 開發時間 | 需要紀律與審查 |

**為什麼不能單靠編譯器旗標？** 因為上表每一欄的「局限」都是真實存在的；旗標降低風險，但「intra-object 越界」這類 bug 三層防護全都漏掉（`intra.c`）。最有效的永遠是消除 bug 本身：語言層面的做法是記憶體安全語言（Rust 等），C 內部的做法是 bounds-checked 容器、靜態分析與 fuzzing。

## 踩雷集錦

1. **以為「開了 canary 就安全」**：canary 只擋連續覆寫返回位址的情況。同一個 struct 內蓋到相鄰欄位、非連續寫入、資訊洩漏之後，canary 都沒用（`intra.c`）。
2. **以為 `strncpy` 是安全版的 `strcpy`**：它在來源比 `n` 長時**不會補 NUL 結尾**，下一次 `printf("%s")` 就越界讀取；來源較短時又會補零到 `n` bytes，浪費效能。更好的選擇是 `snprintf(dst, sizeof dst, "%s", src)` 並檢查回傳值，或 `memcpy` 配上明確的長度檢查。
3. **`free` 完還在用，因為「還能跑」**：UAF 通常不會立刻崩潰（`uaf` 範例讀到別人的資料而且程式正常結束）。「測試通過」不代表沒有 UAF；用 ASan 跑測試。
4. **只防返回位址，忽略資料**：`is_admin`、長度欄位、函式指標被蓋，一樣能改變程式行為。設計資料結構時，把「危險欄位」（函式指標、權限）放在陣列**之前**（低位址側），陣列溢位時就不會蓋到它們。
5. **為了讓實驗跑起來而在正式建置留下 `-z execstack`、`-fno-stack-protector`**：本章為了觀察而關掉防護，這些旗標**只能用在實驗檔**。可以在 CI 加一步用 `chk.sh` 這類腳本檢查產出物的保護狀態。

## 進階：再往深一層

**安全的初始化與長度類型**：許多漏洞的根源不是 `strcpy` 本身，而是**長度計算**（整數溢位使 `malloc` 配置太小，之後的 `memcpy` 就越界）。這是 [Ch 22 — integer overflow](./22-integer-overflow.md) 的主題，與本章形成因果鏈。

**CFI（Control-Flow Integrity）**：Abadi 等人（CCS 2005）提出的概念：預先計算程式的合法控制流圖，執行期檢查每個間接呼叫的目標是否合法。clang 的 `-fsanitize=cfi`（需要 `-flto` 與 `-fvisibility=hidden`，就我所知）實作了「間接呼叫的目標函式型別必須與呼叫處的型別相符」；本機的 clang 14 未在本章實測。

**Stack clash 與 guard page**：stack 與 heap／其他映射之間的間隔如果小於單一函式的 frame 大小，一個大型 VLA 或 `alloca` 可以「跳過」guard 頁面直接寫入別人的記憶體。gcc 的 `-fstack-clash-protection`（Ubuntu 預設啟用，就我所知）會在配置大 frame 時逐頁探測（probe）以確保觸發 guard。

**Heap 的現代防禦**：glibc 2.32 起的 safe-linking（上面觀察到的 `XOR (p >> 12)`）、tcache key、以及 `MALLOC_CHECK_`／`MALLOC_PERTURB_` 環境變數，都在提高 heap 損毀被察覺的機率；系統層級有專門設計為抗利用的配置器（如 GrapheneOS 的 hardened_malloc），細節不在本課範圍。

## 動手練習

1. 用 `chk.sh` 檢查 `/bin/ls`、`/usr/bin/sudo`、你自己的專案執行檔，列出缺少哪些防護。
2. 把 `vuln.c` 的 `strcpy` 改成 `snprintf(buf, sizeof buf, "%s", name)`，確認長輸入被截斷而不崩潰；並在 `-fsanitize=address` 下驗證沒有報錯。
3. 寫一個 `intra.c` 的修正版：把 `is_admin` 放在 `name` 之前，並加上長度檢查；用 ASan 與一組邊界輸入（長度 7、8、9）驗證。
4. 在 `heap.c` 的 `uaf` 模式加上 `s1 = NULL;`（free 之後立刻清除），觀察編譯器或 ASan 在存取點的行為變化。這樣修能解決所有 UAF 嗎？（提示：有多個指標指向同一物件時呢？）
5. 查閱 `man 7 signal` 與 [N1570 7.22.3.3](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n1570.pdf)，說明為什麼在 `SIGSEGV` handler 裡呼叫 `free` 是危險的。

## 本章重點整理

- stack 把返回位址與區域變數放在相鄰位置，`ret` 無條件相信 stack 上的值，所以越界寫入可能改變控制流。
- 每層防禦對準一個攻擊前提：canary 護返回位址、FORTIFY 與 ASan 抓越界、NX 擋注入碼、ASLR/PIE 隱藏位址、RELRO 護 GOT、CET 對付返回位址竄改與間接跳轉。每層都有局限，需疊加使用。
- Ubuntu 22.04 預設已開 PIE、NX、Full RELRO、canary（strong）、FORTIFY（`-O1`+）、CET 標記；做實驗要主動關掉才看得到原始行為。
- heap 的 UAF 與 double free：free 後 chunk 立即被同大小的 `malloc` 重用，舊指標與新物件 alias；glibc 會偵測部分 double free，ASan 能完整偵測 UAF。

## 自我檢核

- [ ] 不看筆記，畫出一個含 canary 的 stack frame（`buf`、canary、saved rbp、返回位址的順序），並說出連續越界為什麼必須先碰到 canary？
- [ ] 能解釋為什麼 NX 擋不住 ROP，而 CET shadow stack 擋得住？
- [ ] 面試官問「stack canary 能防哪些、不能防哪些」，你能各舉兩個例子嗎？
- [ ] 能說出 `intra.c` 為什麼三種防護都沒攔下，以及怎麼寫才安全？
- [ ] 回想你寫過的 `free()`：有哪一處之後還有指標可能指向它？該怎麼確保它們不會被使用？

## 延伸閱讀

### 論文

- **[StackGuard: Automatic Adaptive Detection and Prevention of Buffer-Overflow Attacks](https://www.usenix.org/legacy/publications/library/proceedings/sec98/full_papers/cowan/cowan.pdf)** — Cowan 等，USENIX Security（1998）
  - **核心貢獻**：提出在返回位址前放 canary 並於返回前檢查；同時比較了「隨機 canary」與「terminator canary（含 NUL 等終止字元）」兩種設計。
  - **讀哪裡**：第 3 節的設計與第 5 節的效能數據；前提是讀懂本章的 frame 圖。
- **[Return-Oriented Programming: Systems, Languages, and Applications](https://dl.acm.org/doi/10.1145/2133375.2133377)** — Roemer、Buchanan、Shacham、Savage，ACM TISSEC（2012）
  - **核心貢獻**：系統性論述 ROP 為何圖靈完備、如何在不同架構實現，及針對它的防禦的檢討。
  - **讀哪裡**：前三節概念與討論；實作細節可略。這是理解「為什麼需要 CET」的最佳來源。
- **[AddressSanitizer: A Fast Address Sanity Checker](https://www.usenix.org/conference/atc12/technical-sessions/presentation/serebryany)** — Serebryany 等，USENIX ATC（2012）
  - **核心貢獻**：shadow memory 與 redzone 的設計，使常見記憶體錯誤能以相對低的成本偵測（數字見論文的評估）。
  - **讀哪裡**：第 3 節（演算法）；第 6 節的效能評估。
- **Control-Flow Integrity** — Abadi、Budiu、Erlingsson、Ligatti，ACM CCS（2005）
  - **核心貢獻**：CFI 的原始提出（本頁不附未經驗證的直接連結，請以標題在 ACM Digital Library 搜尋）。

### 部落格 / 技術文章

- **[Smashing the Stack for Fun and Profit](https://phrack.org/issues/49/14.html)** — Aleph One，*Phrack* 49（1996）
  - **這篇說什麼**：stack overflow 攻擊的經典入門文獻，閱讀時請對照本章的防禦：文中每個假設（stack 可執行、位址固定、沒有 canary）在今天都已被打破。
  - **為什麼值得讀**：是理解「為什麼每一層防禦存在」的歷史起點；僅作為歷史與機制學習，實驗請在自有環境。

### 官方文件 / Spec

- **[GCC 文件：Instrumentation Options](https://gcc.gnu.org/onlinedocs/gcc/Instrumentation-Options.html)**
  - **讀哪裡**：`-fstack-protector`、`-fstack-protector-strong`、`-fstack-clash-protection` 與 `-fsanitize=address` 條目；本章每個旗標的定義都在這裡。
- **[glibc Wiki：MallocInternals](https://sourceware.org/glibc/wiki/MallocInternals)**
  - **讀哪裡**：「Chunks」「Tcache」兩節；對照本章觀察到的 size 欄位與 tcache 結構。前提：讀過 [Ch 17](./17-malloc-internals.md) 會更順。

### 書籍

- **《Computer Systems: A Programmer's Perspective》（CSAPP，3rd ed.）** — Bryant & O'Hallaron（Pearson，2015）
  - **讀哪幾章**：第 3.10 節「Combining Control and Data in Machine-Level Programs」講 buffer overflow、canary、ASLR、NX 的概念，並附習題；配合本章實驗閱讀效果最佳。

stack 與 heap 的漏洞都建立在「呼叫者與被呼叫者互相信任」上。下一步，把這一部分學到的指標、callback 與 stack frame 全部用上，寫一個泛型的動態陣列與排序工具箱，並用 ASan 與 GDB 驗證它。

→ [練習 A 泛型容器與 callback 工具箱](./practice-a-generic-container.md)
