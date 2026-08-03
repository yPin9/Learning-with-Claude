# Ch 30 逆向者的 Pattern 字典

> **目標**：把全課學過的 binary pattern 收斂成可查閱的速查表。遇到不認識的 asm 片段時，先定類別，再翻對應節，一眼認出。

---

## 如何使用這張字典

1. **看到不認識的片段**——先問自己：這是單一運算技巧（→ 第 1 節 Compiler Idioms）、跳轉形狀（→ 第 2 節控制流）、記憶體佈局（→ 第 3 節資料結構）、神秘常數（→ 第 4 節演算法指紋）、library call（→ 第 5 節標準庫）、還是刻意干擾你視線的東西（→ 第 6 節混淆）？

2. **翻對應節**，比對 beacon 欄位。Beacon 是「最短的、足以唯一識別這個 pattern 的 asm 特徵」。

3. **看「對應 source 概念」**——確認你的猜測，再看「在哪章學過」補細節。

4. **這張字典不替代推理**。它幫你在腦中建立第一個假設，剩下靠你自己驗證。

---

## 1. Compiler Idioms（編譯器慣用語）

> 參見 Ch 10（Compiler Idioms 專章）、Ch 4（基礎 asm 讀法）。

---

### 1.1 整數除法魔數

**名稱**：乘法替代除法（Magic Number Division）

**beacon**：
```asm
; x / 7  (signed)
movslq  %edi, %rax
imulq   $0x24924925, %rax, %rax   ; 魔數
sarq    $0x23, %rax
movl    %edi, %ecx
sarl    $0x1f, %ecx
subl    %ecx, %eax
```

**對應 source 概念**：`return x / 7;`

**辨識重點**：看到 `imul` 緊跟 `sar`，且 `sar` 的位移 > 16，幾乎確定是除法魔數。魔數本身可丟進 `https://godbolt.org` 反推除數。

**在哪章學過**：Ch 10

---

### 1.2 Signed 除以 2

**名稱**：算術右移修正（Signed Divide by 2）

**beacon**：
```asm
movl    %edi, %eax
sarl    $0x1f, %eax      ; 取符號位到所有 bit（全 0 或全 1）
addl    %eax, %edi       ; 如果負數 +1，修正 truncation
sarl    %edi             ; >> 1
```

**對應 source 概念**：`return x / 2;`（signed）

**辨識重點**：`sar $0x1f` 後緊跟 `add`，再接 `sar $1`——這三步是 C 標準對 signed 除以 2 的正確實作。

**在哪章學過**：Ch 10

---

### 1.3 模 2 奇偶檢查

**名稱**：AND + TEST 奇偶（Mod 2 / Even-Odd Check）

**beacon**：
```asm
andl    $0x1, %eax
testl   %eax, %eax
jne     .odd            ; 或 je .even
```

**對應 source 概念**：`if (x % 2 != 0)`

**辨識重點**：`and $1` 後直接 `test self; jne/je`，不帶任何 `imul`。

**在哪章學過**：Ch 10

---

### 1.4 LEA 乘法

**名稱**：LEA 常數乘法（LEA Multiply）

**beacon**：
```asm
; x * 5
leaq    (%rax,%rax,4), %rax

; x * 3
leaq    (%rdi,%rdi,2), %rax

; x * 9
leaq    (%rdi,%rdi,8), %rax
```

**對應 source 概念**：`x * 5`、`x * 3`、`x * 9`

**辨識重點**：`lea (base, index, scale)` 中 scale 是 2/4/8，結果等於 `base + index*scale`。

**在哪章學過**：Ch 4、Ch 10

---

### 1.5 XOR 清零

**名稱**：XOR 自身清零（XOR Clear）

**beacon**：
```asm
xorl    %eax, %eax      ; int x = 0 / return 0 / 最省空間的清零
xorps   %xmm0, %xmm0   ; float/SSE 版
```

**對應 source 概念**：`int x = 0;` 或 `return 0;`

**辨識重點**：任何暫存器 `xor` 自身——這不是 bug，是最短的清零 encoding（`mov $0, %eax` 多 1-2 byte）。

**在哪章學過**：Ch 4

---

### 1.6 TEST 自身（NULL 檢查）

**名稱**：TEST self NULL 檢查

**beacon**：
```asm
testq   %rax, %rax
je      .null_branch     ; if (ptr == NULL)
; 或
testl   %eax, %eax
js      .negative        ; if (x < 0)
```

**對應 source 概念**：`if (ptr == NULL)` / `if (x == 0)` / `if (x < 0)`

**辨識重點**：`test reg, reg`（等同 `cmp $0, reg`）後接條件跳轉。`je`→NULL/零，`js`→負數。

**在哪章學過**：Ch 5、Ch 4

---

### 1.7 條件選擇（CMOV）

**名稱**：無分支條件賦值（CMOV Select）

**beacon**：
```asm
cmpl    %esi, %edi
cmovge  %esi, %eax      ; eax = (edi >= esi) ? edi : esi
```

**對應 source 概念**：`return (a >= b) ? a : b;`（編譯器把三元運算子最佳化成 CMOV）

**辨識重點**：看到 `cmov*` 系列指令——這是 branchless 選擇，Decompiler 常誤讀成 if/else。

**在哪章學過**：Ch 5、Ch 10

---

### 1.8 位移替代乘以 2 的冪

**名稱**：SHL 乘法（Shift-Left Multiply）

**beacon**：
```asm
shll    $3, %eax        ; x * 8
shlq    $2, %rdi        ; x * 4（常見於 int 陣列索引）
```

**對應 source 概念**：`x * 8`、`arr[i]` 中的 `i * sizeof(int)`

**辨識重點**：`shl $n` 等於乘以 2^n。陣列索引算 offset 時很常見。

**在哪章學過**：Ch 4

---

## 2. 控制流形狀

> 參見 Ch 5（控制流分析）、Ch 6（迴圈識別）。

---

### 2.1 尾遞迴化迴圈

**名稱**：尾遞迴消除（Tail-Call Elimination）

**beacon**：
```asm
fact:
    testl   %edi, %edi
    je      .base
    ; ... 計算 ...
    jmp     fact        ; 不是 call fact，是 jmp 回開頭
.base:
    ret
```

**對應 source 概念**：`return n * fact(n-1);`（尾遞迴版）被編譯器攤平成迴圈

**辨識重點**：函式內部有 `jmp` 跳回函式開頭，且沒有對應的 `call self`。

**在哪章學過**：Ch 6

---

### 2.2 Switch 跳轉表

**名稱**：Switch Jump Table

**beacon**：
```asm
; 邊界檢查
cmpl    $5, %eax
ja      .default
; 查表跳轉
movslq  %eax, %rax
leaq    .Ltable(%rip), %rcx
movq    (%rcx,%rax,8), %rax
jmpq    *%rax

.Ltable:
    .quad   .case0
    .quad   .case1
    ...
```

**對應 source 概念**：`switch (x) { case 0: ... case 1: ... }`

**辨識重點**：`cmp $N; ja .default` 後接 `jmp [base + idx*8]`。表格本身在 `.rodata`。

**在哪章學過**：Ch 5

---

### 2.3 Short-Circuit 求值

**名稱**：短路求值（Short-Circuit &&/||）

**beacon**：
```asm
; (a != 0 && b > 0)
testl   %edi, %edi
je      .false          ; a == 0 直接跳，不評估 b
testl   %esi, %esi
jle     .false
; 都通過才到這
```

**對應 source 概念**：`if (a != 0 && b > 0)`

**辨識重點**：連續多個條件跳轉跳向同一個失敗 label——這是 `&&`。跳向 true label 是 `||`。

**在哪章學過**：Ch 5

---

### 2.4 迴圈 Canonical Form

**名稱**：計數迴圈 / 指標遞增迴圈 / 哨兵迴圈

**beacon**：
```asm
; for (int i = 0; i < n; i++)
xorl    %ecx, %ecx          ; i = 0
.loop:
    cmpl    %edi, %ecx       ; i < n
    jge     .exit
    ; body
    incl    %ecx             ; i++
    jmp     .loop

; while (*ptr)   哨兵版
.loop:
    movzbl  (%rsi), %eax
    testb   %al, %al
    je      .exit
    ; body
    incq    %rsi
    jmp     .loop
```

**對應 source 概念**：`for`、`while`、`do-while`

**辨識重點**：形狀固定——初始化在迴圈外，條件在頂端或底端，遞增在 body 末尾。

**在哪章學過**：Ch 6

---

## 3. 資料結構指紋

> 參見 Ch 9（struct 與資料佈局）、Ch 8（heap）。

---

### 3.1 Vtable（C++ 虛擬函式表）

**名稱**：Vtable Pointer Pattern

**beacon**：
```asm
; 建構子：設 vtable 指標
leaq    _ZTV5Shape+16(%rip), %rax
movq    %rax, (%rdi)          ; obj->vptr = &vtable[0]

; 虛擬呼叫：
movq    (%rdi), %rax          ; rax = obj->vptr
movq    8(%rax), %rax         ; rax = vtable[1]（第二個虛擬函式）
callq   *%rax
```

**對應 source 概念**：`shape->draw();`（虛擬呼叫）

**辨識重點**：`[obj]` 取出指標，再 `[ptr + offset]` 取函式指標，再 `call *reg`——雙重解引用 + 間接呼叫。

**在哪章學過**：Ch 9

---

### 3.2 std::string（三指標佈局 / SSO）

**名稱**：std::string Layout

**beacon**：
```asm
; 三個欄位：ptr(+0), size(+8), capacity(+16)
movq    (%rdi), %rsi          ; data pointer
movq    8(%rdi), %rdx         ; size
movq    16(%rdi), %rcx        ; capacity

; SSO（字串 <= 15 bytes 時內聯在 struct 裡）
; capacity 欄位存 15，data pointer 欄位就是 buffer 本身
```

**對應 source 概念**：`std::string s;`

**辨識重點**：物件 +0 是 data pointer、+8 是 size、+16 是 capacity。若 capacity == 15 且 pointer 指向物件本身，就是 SSO。

**在哪章學過**：Ch 9、Ch 11

---

### 3.3 malloc chunk header

**名稱**：glibc Heap Chunk Header

**beacon**：
```asm
; 拿到 user ptr 後往前 8 byte 是 chunk header
; prev_size(+0) 或 fd/bk（若 free）
; size field(+8)，最低 3 bit 是 flag：P（prev_in_use）、M、A
movq    -8(%rdi), %rax        ; rax = chunk->size field
andq    $-8, %rax             ; mask off flags，取實際大小
```

**對應 source 概念**：`malloc()` 分配的 chunk 元資料

**辨識重點**：user pointer 減 8 或 16 取 size，`and $~7` 去除 flag bits。

**在哪章學過**：Ch 8

---

### 3.4 Linked List 遍歷

**名稱**：Linked List Next Pointer Loop

**beacon**：
```asm
.loop:
    movq    (%rax), %rax      ; rax = node->next
    testq   %rax, %rax
    jne     .loop             ; while (node != NULL)
```

**對應 source 概念**：`while (node->next) node = node->next;`

**辨識重點**：`[rax]` → `rax` 的反覆自我解引用，加上 NULL 終止條件。

**在哪章學過**：Ch 9

---

### 3.5 Arena / Bump Allocator

**名稱**：Bump Pointer Allocator

**beacon**：
```asm
; arena->ptr += size; if (arena->ptr > arena->end) fail;
movq    arena_ptr(%rip), %rax
addq    %rdi, %rax            ; bump
movq    arena_end(%rip), %rcx
cmpq    %rcx, %rax
ja      .oom
movq    %rax, arena_ptr(%rip)
```

**對應 source 概念**：`void* arena_alloc(Arena* a, size_t sz)`

**辨識重點**：只有 add + cmp + store，沒有 free list 操作——這不是 malloc，是 arena。

**在哪章學過**：Ch 8、Ch 9

---

## 4. 演算法指紋（Crypto 常數）

> 參見 Ch 12（密碼學常數識別）、Ch 13（演算法識別）。

---

### 4.1 FNV-1a Hash

**名稱**：FNV-1a 雜湊

**beacon**：
```asm
; 32-bit FNV-1a
; hash ^= byte; hash *= 0x01000193
xorl    %eax, %edi
imull   $0x01000193, %edi, %edi

; offset basis = 0x811c9dc5
movl    $0x811c9dc5, %eax
```

**對應 source 概念**：`hash ^= *p++; hash *= FNV_PRIME;`

**辨識重點**：看到 `0x01000193` 或 `0x811c9dc5` 任何一個即確認。常見於 hash map 實作。

**在哪章學過**：Ch 12

---

### 4.2 CRC32

**名稱**：CRC32 查表

**beacon**：
```asm
; 軟體查表版
movzbl  (%rsi), %eax
xorl    %edi, %eax
andl    $0xff, %eax
movl    crc_table(,%rax,4), %eax
; 表第一個 entry：0xEDB88320（reflected polynomial）
```

**對應 source 概念**：`crc = crc_table[(crc ^ *buf) & 0xff] ^ (crc >> 8);`

**辨識重點**：`0xEDB88320` 在 .rodata 表格頭，或使用 `crc32` 硬體指令。

**在哪章學過**：Ch 12

---

### 4.3 MD5 初始向量

**名稱**：MD5 Init Constants

**beacon**：
```asm
; 四個初始 state word
movl    $0x67452301, state+0(%rip)
movl    $0xefcdab89, state+4(%rip)
movl    $0x98badcfe, state+8(%rip)
movl    $0x10325476, state+12(%rip)
```

**對應 source 概念**：`MD5_Init()` 中的狀態初始化

**辨識重點**：這四個常數同時出現即確認 MD5。

**在哪章學過**：Ch 12

---

### 4.4 SHA-256 輪常數

**名稱**：SHA-256 K Constants

**beacon**：
```asm
; K[0] = 0x428a2f98, K[1] = 0x71374491 ...
; 通常以 .rodata 表格形式出現，256 bytes
leaq    sha256_K(%rip), %rax
movl    (%rax,%rcx,4), %edx   ; K[i]
```

**對應 source 概念**：SHA-256 compression function 的 K 表

**辨識重點**：.rodata 裡 64 個 dword，開頭 `0x428a2f98`——確認 SHA-256。

**在哪章學過**：Ch 12

---

### 4.5 AES S-Box

**名稱**：AES S-Box（軟體版）

**beacon**：
```asm
; SubBytes 查表
movzbl  (%rdi), %eax
movzbl  sbox(%rax), %eax      ; sbox[0] = 0x63, sbox[1] = 0x7c ...

; 或硬體 AES-NI
aesenc  %xmm1, %xmm0
aesenclast %xmm2, %xmm0
```

**對應 source 概念**：`AES_encrypt()` SubBytes 步驟

**辨識重點**：.rodata 表格開頭 `63 7c 77 7b f2 6b 6f c5`，或出現 `aesenc/aesdec` 指令。

**在哪章學過**：Ch 12

---

### 4.6 TEA/XTEA delta

**名稱**：TEA Delta 常數

**beacon**：
```asm
; sum += 0x9e3779b9
addl    $0x9e3779b9, %eax

; XTEA 32 輪後 sum = delta * 32 = 0xC6EF3720
```

**對應 source 概念**：TEA/XTEA 加密輪函式

**辨識重點**：`0x9e3779b9` 是 golden ratio 的 32-bit 近似值，TEA 獨有。

**在哪章學過**：Ch 12

---

### 4.7 RC4 KSA

**名稱**：RC4 Key Scheduling

**beacon**：
```asm
; 初始化 S[256]
xorl    %eax, %eax
.init:
    movb    %al, S(%rax)        ; S[i] = i
    incb    %al
    jnz     .init               ; 256 次後溢位到 0
```

**對應 source 概念**：`for (i=0;i<256;i++) S[i]=i;`

**辨識重點**：256 byte 初始化迴圈 + 後續的 swap 迴圈 = RC4 KSA。

**在哪章學過**：Ch 12

---

## 5. 標準庫指紋

> 參見 Ch 11（標準庫識別）、Ch 7（PLT/GOT）。

---

### 5.1 strlen SIMD 版

**名稱**：SSE2 strlen

**beacon**：
```asm
; glibc strlen 快速路徑
pxor    %xmm0, %xmm0           ; xmm0 = 0
movdqu  (%rdi), %xmm1
pcmpeqb %xmm0, %xmm1           ; 比 16 bytes 有沒有 \0
pmovmskb %xmm1, %eax
bsfl    %eax, %eax             ; 找第一個 \0 的位元
```

**對應 source 概念**：`strlen(s)`

**辨識重點**：`pcmpeqb` + `pmovmskb` 組合幾乎只出現在 strlen/strchr 這類字串掃描。

**在哪章學過**：Ch 11

---

### 5.2 memcpy REP MOVSQ

**名稱**：REP MOVSQ（大塊 memcpy）

**beacon**：
```asm
movq    %rdx, %rcx             ; count in bytes
shrq    $3, %rcx               ; / 8 = qword count
rep movsq                      ; 每次複製 8 bytes
; 處理尾端 < 8 bytes：
andl    $7, %edx
; ...
```

**對應 source 概念**：`memcpy(dst, src, n)`

**辨識重點**：`rep movsb/movsq` 前的 `mov rcx` 設定 count。大 n 用 SIMD，小 n 展開。

**在哪章學過**：Ch 11

---

### 5.3 PLT Call Pattern

**名稱**：PLT 間接呼叫（動態連結函式）

**beacon**：
```asm
callq   printf@plt
; 展開後 PLT stub：
; jmpq   *GOT+N(%rip)           ; 第一次跳 resolver，之後直接跳函式
```

**對應 source 概念**：`printf("...");`

**辨識重點**：call target 名稱帶 `@plt`，或 call 到 `.plt` 段的 stub（Ghidra/IDA 通常已標注）。

**在哪章學過**：Ch 7

---

### 5.4 malloc/free Pattern

**名稱**：malloc + free Call Pattern

**beacon**：
```asm
; malloc
movl    $0x40, %edi            ; size = 64
callq   malloc@plt
testq   %rax, %rax
je      .alloc_failed           ; NULL check 是慣例

; free
movq    %rbx, %rdi
callq   free@plt
```

**對應 source 概念**：`ptr = malloc(64); if (!ptr) ...`

**辨識重點**：`call malloc` 後緊跟 NULL 檢查是 hardened 程式碼；沒有 NULL 檢查則要注意可能是 bug。

**在哪章學過**：Ch 8、Ch 11

---

## 6. 混淆指紋

> 參見 Ch 20（去混淆）、Ch 21（反調試與保護）。

---

### 6.1 不透明謂詞

**名稱**：Opaque Predicate（永遠 true/false 的條件）

**beacon**：
```asm
; 永遠 true：x*x >= 0（signed overflow 未定義但實際永遠成立）
imull   %eax, %eax
testl   %eax, %eax
jns     .real_code             ; always taken
; dead code（junk）
movl    $0xdeadbeef, %eax
callq   *%rax

; 數論版：n*(n+1) 永遠是偶數
; ...
andl    $1, %eax
testl   %eax, %eax
jne     .never_taken           ; always NOT taken
```

**對應 source 概念**：無意義的 if (不影響語義)，純粹干擾 CFG

**辨識重點**：條件跳轉的 target 永遠可達或永遠不可達，symbolic execution / MAAT / angr 可自動識別。

**在哪章學過**：Ch 20

---

### 6.2 Dead Branch 的 Junk Bytes

**名稱**：Ret 後垃圾 Bytes

**beacon**：
```asm
callq   some_func
ret                            ; <- 真正的 return
.byte   0xeb, 0x05             ; JMP +5（若被誤當指令執行，跳過後面）
.byte   0xde, 0xad, 0xbe, 0xef ; junk
; 真正下一個函式從這裡才開始
```

**對應 source 概念**：無（純混淆）

**辨識重點**：`ret` 後還有 bytes 但不是有效指令——disassembler 可能誤對齊，Ghidra 按 D 強制 define data，再看跳轉目標。

**在哪章學過**：Ch 20、Ch 21

---

### 6.3 間接跳轉表（計算版）

**名稱**：Computed Indirect Jump（混淆版 switch）

**beacon**：
```asm
; 不是查表，是計算
movl    %eax, %ecx
xorl    $0x13, %ecx            ; 混淆索引
imull   $0x49, %ecx, %ecx
addl    $offset, %ecx
jmpq    *%rcx                  ; 跳到計算結果
```

**對應 source 概念**：switch 或分派邏輯，但被混淆成難以靜態分析的計算

**辨識重點**：`jmp *reg` 但 reg 的值來自一連串算術（不是乾淨的 `jmp [base + idx*8]`）——動態執行或 taint analysis 才能還原。

**在哪章學過**：Ch 20

---

### 6.4 CALL/POP 取 RIP（位置無關 Shellcode 老技法）

**名稱**：CALL-POP Get-PC Trick

**beacon**：
```asm
callq   .next
.next:
    popq    %rax               ; rax = 當前 RIP（.next 的地址）
    leaq    data - .next(%rax), %rbx   ; 計算 data 相對於 PC 的位置
```

**對應 source 概念**：x86-32 時代取 PC 的方法（x86-64 有 RIP-relative 定址，但老 shellcode / 某些保護機制仍用此法）

**辨識重點**：`callq` 緊跟 `popq`，而 call target 就是下一條指令——這不是正常函式呼叫。

**在哪章學過**：Ch 21

---

### 6.5 Anti-Debug TLS Callback

**名稱**：TLS Callback 隱藏入口（PE 格式）

**beacon**：
```asm
; PE .tls 節，callback array 指向執行比 main 更早的函式
; 在 IDA: LOAD segment → TLS directory → AddressOfCallBacks
```

**對應 source 概念**：`__declspec(thread)` 相關初始化 / 刻意隱藏的早期執行點

**辨識重點**：IDA/Ghidra 在 TLS callback 不一定自動建立函式，需手動查 TLS directory。

**在哪章學過**：Ch 21

---

## 自我檢核

以下問題不需全部秒答，但要能在 30 秒內定位到本字典的正確節：

1. 你看到 `imulq $0x2AAAAAAB, %rax` 然後 `sarq $32, %rax`，這在幹什麼？
2. 一個函式最後有 `jmp func_start`（跳回自己開頭）而不是 `call func; ret`，這是什麼 pattern？
3. 物件的第一個 `movq (%rdi), %rax` 取出的值，再對它 `movq 16(%rax), %rax` 然後 `callq *%rax`——發生了什麼？
4. .rodata 有一個 256 byte 的表，第一個 byte 是 `0x63`——這最可能是什麼？
5. `pcmpeqb %xmm0, %xmm1` + `pmovmskb %xmm1, %eax` 這組合對應哪個 libc 函式？
6. `callq .+5` 緊跟 `popq %rbx`——這在做什麼？哪種情況下你會看到它？
7. 一個條件跳轉，經過 symbolic 分析後發現分支永遠只走一側——你在第幾節找這個 pattern？

**參考答案方向**：1→Compiler Idioms 除法魔數；2→尾遞迴；3→Vtable 虛擬呼叫；4→AES S-Box；5→strlen SIMD；6→CALL-POP Get-PC，位置無關 shellcode 或老 x86-32 PIC；7→第 6 節混淆，不透明謂詞。

---

## 本章重點整理

- **分類先於識別**：看到不認識的片段，先問「這是什麼類型的 pattern」，再查對應節——這比逐行猜測省十倍時間。
- **Beacon 是最短充分特徵**：不需整段 asm 都符合，一個 magic number 或一個指令組合就夠觸發假設。
- **這張字典是起點，不是終點**：認出 pattern 後，仍需在 Ghidra/GDB 中驗證——decompiler 會騙你，真跑不會。
- **混淆只是雜訊**：第 6 節的 pattern 目的是讓你花時間在 dead code 和 CFG 噪訊上；認出後直接標注跳過，不要試圖理解 junk bytes 的「邏輯」。
- **字典需要你自己增補**：每次逆向遇到新 pattern，寫下 beacon + source concept——六個月後你會有比這張更完整的個人字典。

---

本章是整個課程的靜態知識收斂點。下一章把這張字典拿來實戰——在沒有 debug info 的 strip binary 上走完完整逆向流程，看看哪些 pattern 你能一眼認出，哪些還需要動態驗證。

→ [Ch 31 完整攻堅實況：冷逆一個 strip binary](./31-full-attack-live.md)
