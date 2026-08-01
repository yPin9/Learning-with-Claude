# Ch 20 — ARM64 逆向必備

> **目標**：補齊「逆一個 Android `.so` 需要的 ARM64（AArch64）」——不是完整教 ISA，是教你**讀得懂反編譯出來的組語在幹嘛**。你要能：認得 x0–x30/sp/pc 各是什麼、知道 AAPCS64 呼叫慣例（哪個暫存器傳參、哪個放回傳值）、看懂 `mov`/`ldr`/`str`/`bl`/`ret`/`cbz`/`b.cond`/`adrp` 這些天天出現的指令、認出 stack frame 的長相、以及把常見的反編譯 pattern（存取全域變數、字串常數、迴圈、if）對應回 C。

> **環境提醒（本章最重要的一句）**：**你 Ch 0 建的 x86_64 AVD，裡面的 `.so` 是 x86_64 機器碼，不是 ARM64。** 要練 ARM64 逆向，你必須用 **arm64 的 AVD image**（`system-images;android-33;google_apis;arm64-v8a`）或一台**真機**。在 x86 host 上 arm64 AVD 走全 CPU 模擬（QEMU TCG），慢，但 `.so` 是真 ARM64。手機市場 99% 是 ARM64，所以逆真實 App 逆的一定是 ARM64——這章的一切都以 AArch64 為準。本章的**指令編碼**我用 Python 在本機**實際算出來驗證**（標「實際輸出」）；反編譯畫面因無 IDA/Ghidra，標「未實測，理論預期行為」。

## 為什麼需要這個？

你在 Ch 19 已經知道怎麼定位 native 入口函式。打開它，IDA/Ghidra 的 F5 反編譯給你近似 C——但反編譯不是萬能的：混淆過的碼 F5 出來一坨爛泥、關鍵的幾行常常反編譯錯、有時你得直接盯著 disassembly 才看得出真相。**會讀 ARM64 組語是 native 逆向的最低生存線**。而且看得懂組語，你才知道 F5 哪裡騙你、才能在 Frida 裡精準地 hook 某個 offset、才能手動 patch 一條指令繞過校驗。

好消息：ARM64 是 **RISC、定長 4-byte、指令集規整**，比 x86 那種變長、一個 `mov` 有幾十種形式的 CISC 好讀太多。你不需要背完 ARM 手冊，記住本章這幾十條指令與幾個 pattern，就能讀懂絕大多數 App 的 native 碼。

## 先建立直覺：一顆 ARM64 CPU 的心智模型

逆向時你腦中要有這張圖——暫存器是什麼、資料怎麼在暫存器與記憶體間流動：

```
   通用暫存器 (64-bit)                       特殊暫存器
 ┌─────────────────────────┐              ┌──────────────────────┐
 │ x0  x1  x2 ... x7        │ 傳參/回傳    │ sp  堆疊指標          │
 │ x8                       │ 間接結果/系呼│ pc  程式計數器(當前指令)│
 │ x9 ...x15                │ 臨時(caller存)│ lr = x30 返回位址     │
 │ x16 x17 (ip0/ip1)        │ PLT/veneer   │ nzcv 條件旗標          │
 │ x18                      │ 平台保留      └──────────────────────┘
 │ x19...x28                │ callee 保存
 │ x29 = fp 幀指標           │              w0..w30 = 各 x 暫存器的
 │ x30 = lr 返回位址         │                低 32-bit（同一顆暫存器）
 │ xzr/wzr = 恆為 0 的暫存器  │
 └─────────────────────────┘

   記憶體 ◀── ldr（load，記憶體→暫存器）──  暫存器
          ── str（store，暫存器→記憶體）──▶
```

三個要刻進腦子的事實：

1. **`x0`–`x30` 是 64-bit；`w0`–`w30` 是同一顆暫存器的低 32-bit**。看到 `w0` 別以為是另一顆，它就是 `x0` 的下半截（寫 `w` 會把上半 32-bit 清零）。`int` 用 `w`、`long`/指標用 `x`——這幫你判斷變數型別。
2. **ARM64 是 load/store 架構**：算術指令只碰暫存器，**記憶體存取只能靠 `ldr`/`str`**。所以你會看到「`ldr` 把值搬進暫存器 → 算 → `str` 寫回」這個節奏反覆出現。
3. **沒有專門的 `push`/`pop`**（不像 x86）：進出函式靠 `stp`/`ldp`（一次存/載一對暫存器）配合調整 `sp` 來做——認出這個 pattern 就認出了函式的頭尾。

## 暫存器的角色分工（AAPCS64 呼叫慣例）

逆向最實用的知識不是每顆暫存器叫什麼，而是**呼叫函式時參數放哪、回傳值放哪、哪些暫存器函式會保存**。這套規則叫 **AAPCS64**（ARM 64-bit 呼叫慣例），是你把組語對回 C 函式的鑰匙。

| 暫存器 | 角色 | 逆向時的意義 |
|---|---|---|
| **x0–x7** | **傳參**（前 8 個整數/指標參數） | 進函式時 x0=第1參、x1=第2參…；超過 8 個走 stack |
| **x0**（也 x1） | **回傳值** | 函式 `ret` 後看 x0 就是回傳值；128-bit 才用到 x1 |
| **x8** | 間接結果位址 / syscall 號 | 回傳大結構時 x8 指向結果空間 |
| x9–x15 | 臨時（caller-saved） | 函式內隨便用，跨呼叫不保證留著 |
| x16/x17 | ip0/ip1，linker veneer/PLT | 跨模組跳轉的跳板，PLT 常見 |
| x19–x28 | callee-saved | 函式若用它們，開頭會先 `stp` 存、結尾 `ldp` 還 |
| **x29 = fp** | 幀指標 | 指向當前 stack frame 底 |
| **x30 = lr** | 返回位址 | `bl` 呼叫時自動填入，`ret` 就是跳回 x30 |
| **xzr/wzr** | 零暫存器 | 讀恆為 0；`cmp x0,#0` 其實是 `subs xzr,x0,#0` |

**這張表的實戰用法**：你在 Ch 19 知道 native 函式前兩個隱藏參數是 `JNIEnv*` 與 `jobject`。對回這張表就是：**x0 = `JNIEnv*`、x1 = `jobject`/`jclass`、x2 = 第一個真參數、x3 = 第二個…**。所以逆一個 `native String sign(String s)`，`s` 這個 `jstring` 就在 **x2**。這是把 Java 簽名對回 ARM64 暫存器的關鍵一步。

## 核心指令：讀 `.so` 會反覆遇到的那些

不求全，只講逆向高頻的。每條給格式與白話。

### 資料搬移

```asm
mov  x0, x1          ; x0 = x1（暫存器間複製）
mov  x0, #0x10       ; x0 = 立即數 0x10
movz x0, #0          ; x0 = 0（movz = move with zero，清上位）
movk x0, #0x1234, lsl #16  ; 只改某 16-bit 段，組大常數用（movz+movk 拼 64-bit）
```

> ARM64 一條指令只有 32-bit，塞不下 64-bit 立即數。所以載入大常數是 `movz`（設低 16-bit、清其餘）＋若干 `movk`（逐段填）拼出來。逆向看到連續的 `movz`/`movk` 就是在組一個常數，把各段拼起來就是那個值。

### 記憶體存取（load/store）

```asm
ldr  x0, [x1]        ; x0 = *(x1)           從 x1 指的位址讀 8 byte
ldr  w0, [x1, #4]    ; w0 = *(int*)(x1+4)   帶偏移，讀 4 byte
str  x0, [sp, #0x10] ; *(sp+0x10) = x0      寫回 stack
ldp  x29, x30, [sp]  ; 一次載一對：x29=*(sp), x30=*(sp+8)
stp  x29, x30, [sp, #-0x10]!  ; 一次存一對並更新 sp（! = pre-index）
```

- `[x1, #4]` 是**帶偏移定址**——存取結構體欄位、陣列元素全靠它。看到 `ldr w0, [x19, #8]` 多半是「讀 x19 指的結構第 8 byte 那個欄位」。
- `ldp`/`stp` 成對出現在函式頭尾，是 stack frame 的招牌。

### 分支與呼叫

```asm
b    label           ; 無條件跳（等同 goto）
bl   func            ; 呼叫函式：把返回位址存進 x30，跳過去
ret                  ; 返回：跳回 x30（lr）
br   x8              ; 跳到 x8 指的位址（間接跳，virtual call/跳表常見）
blr  x8              ; 間接呼叫 x8 指的函式
cbz  x0, label       ; x0 == 0 就跳（compare & branch if zero）
cbnz x0, label       ; x0 != 0 就跳
b.eq / b.ne / b.lt / b.gt / b.le / b.ge / b.hi / b.ls  ; 依旗標條件跳
```

- **`bl` 是 call、`ret` 是 return**——認出這兩個就切出了函式邊界。`bl` 之後看 x0 拿回傳值。
- **`cbz`/`cbnz`** 是逆向最愛看的：`cbz x0, fail` 常常就是「檢查某個東西是不是 0/null/失敗，是就跳去失敗處理」——校驗邏輯的骨架。

### 算術與比較

```asm
add  x0, x1, x2      ; x0 = x1 + x2
add  x0, x1, #0x10   ; x0 = x1 + 0x10
sub  x0, x1, x2
cmp  x0, #0          ; 比較（設定 nzcv 旗標，其實是 subs xzr,x0,#0）
subs x0, x1, x2      ; 減並設旗標
and/orr/eor x0,x1,x2 ; 位元 且/或/互斥或（eor 就是 XOR，加密演算法必看）
lsl/lsr/asr x0,x1,#3 ; 左移/邏輯右移/算術右移
```

> **`eor`（XOR）是逆加密的地標**：native 裡的簡單加密/混淆常是一串 `eor`。看到密集的 `eor`、`add`、位移，多半就是自製對稱加密或 hash 展開（Ch 23 專講怎麼從這些 pattern 認出 AES/MD5/XOR-cipher）。

### PC 相對定址：`adrp` + `add`（存取全域/字串的招牌）

這是 ARM64 **最該認得的 pattern**，因為存取全域變數、字串常數、GOT 都靠它：

```asm
adrp x0, #0x2000     ; x0 = (當前PC & ~0xFFF) + (0x2000 對齊到 4KB 頁)
add  x0, x0, #0x123  ; x0 += 頁內偏移 0x123  → 拼出目標位址
ldr  x1, [x0]        ; 讀那個位址的值
```

`adrp`（address of page）只能定位到 4KB 頁邊界（因為指令位元不夠放完整位址），所以**永遠配一個 `add`（或 `ldr` 的偏移）補上頁內偏移**。看到 `adrp x?, ...` 緊跟 `add x?, x?, #...`，就是「算出某個全域資料/字串的位址」——IDA/Ghidra 通常會自動把它註解成 `= "某字串"` 或 `= &global_var`。這是你在 native 裡追字串常數、找金鑰的主要線索。

## 底層機制：一次函式呼叫的完整 stack frame

把上面的指令組起來，看一個典型函式的頭尾長什麼樣——這是你在 IDA 裡每個函式都會看到的骨架：

```asm
sign:                              ; 函式進入
    stp  x29, x30, [sp, #-0x20]!   ; ① 存 fp+lr，sp 下移 0x20（開 frame）
    mov  x29, sp                   ; ② 設新 fp = 當前 sp
    str  x19, [sp, #0x10]          ; ③ 存要用到的 callee-saved 暫存器
    ...
    mov  x19, x2                   ; ④ 把第一個真參數(jstring)搬到 x19 保存
    bl   GetStringUTFChars         ; ⑤ 呼叫 JNI 函式，回傳在 x0
    ...
    ldr  x19, [sp, #0x10]          ; ⑥ 還原 callee-saved
    ldp  x29, x30, [sp], #0x20     ; ⑦ 還原 fp+lr，sp 上移 0x20（收 frame）
    ret                            ; ⑧ 跳回 x30
```

```
   進函式前 sp ─────────────────────────┐
                                        │
   stp x29,x30,[sp,#-0x20]! 之後：      ▼
   ┌──────────────┬──────────┬────────────────┐
   │ x29(舊fp)    │ x30(lr)  │  區域變數/保存區 │
   └──────────────┴──────────┴────────────────┘
    ▲sp(=x29)                          高位址 ▲
```

認出這個模式的價值：

- **`stp x29,x30,[sp,#-N]!` = 函式開頭**、**`ldp x29,x30,[sp],#N` + `ret` = 函式結尾**。切函式邊界靠它。
- **參數在函式一開始常被搬到 x19–x28**（callee-saved），因為 x0–x7 隨時會被下一個 `bl` 覆蓋。所以「x2 是 jstring 參數，接著 `mov x19, x2`」——後面要追這個參數就跟著 x19，不是 x2。這是讀 native 碼追資料流的關鍵直覺。

## 範例一：手算指令編碼，證明它們是真的

逆向偶爾要手動 patch 一條指令（例如把一個檢查改成永遠通過），這時你得會算指令的 4-byte 編碼。我用 Python 依 ARM 手冊的位元佈局算幾條，對照已知正確值驗證（**實際輸出**）：

```python
# ret x30 : 定長 4-byte，位元佈局 1101011 0 0 10 11111 0000 0 0 | Rn(5) | 00000(5)
ret = ((0b1101011_0_0_10_11111_0000_0_0 << 5) | 30) << 5 | 0
print("ret x30      :", hex(ret))

# movz x0,#0 : sf=1 opc=10 100101 hw imm16 Rd
def movz(rd, imm16, hw=0):
    return (1<<31)|(0b10<<29)|(0b100101<<23)|(hw<<21)|(imm16<<5)|rd
print("movz x0,#0   :", hex(movz(0,0)))

# mov x0,x1 實為 ORR x0,xzr,x1 : sf=1 opc=01 01010 shift=00 N=0 Rm imm6=0 Rn=31 Rd
def orr_reg(rd, rn, rm):
    return (1<<31)|(0b01<<29)|(0b01010<<24)|(rm<<16)|(rn<<5)|rd
print("mov x0,x1    :", hex(orr_reg(0,31,1)))

# cbz x0,+8 : sf=1 011010 0 imm19 Rt ; imm19 = offset>>2
def cbz(rt, off):
    return (1<<31)|(0b0110100<<24)|(((off>>2)&((1<<19)-1))<<5)|rt
print("cbz x0,+8    :", hex(cbz(0,8)))

# ldr x0,[x1] : size=11 111 0 01 01 imm12 Rn Rt
def ldr_imm(rt, rn, imm12):
    return (0b11<<30)|(0b111001<<24)|(0b01<<22)|(imm12<<10)|(rn<<5)|rt
print("ldr x0,[x1]  :", hex(ldr_imm(0,1,0)))
```

**實際輸出**（Python 3.12 在本機跑）：

```
ret x30      : 0xd65f03c0
movz x0,#0   : 0xd2800000
mov x0,x1    : 0xaa0103e0
cbz x0,+8    : 0xb4000040
ldr x0,[x1]  : 0xf9400020
```

前三個是可查證的規範值：`ret` 永遠是 `0xd65f03c0`、`movz x0,#0` 是 `0xd2800000`、`mov x0,x1`（ORR）是 `0xaa0103e0`——算對了。這證明 ARM64 指令是純粹的位元打包，不是黑魔法。**實戰上你不會手算**（用 keystone/`llvm-mc` 組），但算過一次你就懂「patch 一條指令」到底在改什麼 4 個 byte。

> **patch 的實用形式**：想讓 `cbz x0, fail`（x0 為 0 才跳）永遠不跳，最省事是把它換成 `nop`（`0xd503201f`）讓它直接往下走；想讓一個函式直接回傳成功，把開頭換成 `movz x0,#1` + `ret`（`0xd2800020 0xd65f03c0`）。這是 Ch 25/32 繞校驗的基本功。

## 範例二：把一段反編譯 pattern 對回 C

給你一段典型的 native 反組譯（校驗某輸入是否等於預期），逐行對回 C。**未實測，理論預期行為**（無 IDA，這段是依 AAPCS64 與指令語意寫的代表性反組譯）：

```asm
check:
    stp  x29, x30, [sp, #-0x10]!
    mov  x29, sp
    ldr  w8, [x0]          ; w8 = *(int*)x0     讀第1參指的 int
    cmp  w8, #0x2A          ; 跟 42 比
    b.ne fail               ; 不等就跳 fail
    mov  w0, #1             ; 相等 → 回傳 1
    b    done
fail:
    mov  w0, #0             ; 回傳 0
done:
    ldp  x29, x30, [sp], #0x10
    ret
```

對回 C：

```c
int check(int* p) {         // x0 = p
    if (*p == 42) return 1; // ldr + cmp #0x2A + b.ne
    return 0;
}
```

對照法則：**x0 是第一參 → `ldr w8,[x0]` 是解參考讀 int → `cmp #0x2A` 是跟 42 比 → `b.ne fail` 是不等就走失敗分支 → `mov w0,#1`/`#0` 是設回傳值（回傳值在 w0/x0）→ `ret`**。把「哪個暫存器傳參、回傳值在 x0、cbz/b.cond 是條件分支」這幾條套上去，一段組語就還原成 C 邏輯了。

**邊界/失敗情況**：若把 `check` 的參數當成 `x0=某值本身`（而非指標）去讀，就會誤判——這裡 `ldr w8,[x0]` 明確做了一次解參考，說明 x0 是**指標**參數。分不清「x0 是值還是指標」是新手最常犯的錯：看有沒有 `ldr [x0]` 就知道它被當指標用了。

## 對比與取捨：F5 反編譯 vs 直接讀組語

| 情境 | 用 F5 反編譯 | 直接讀 disassembly |
|---|---|---|
| 一般未混淆函式 | ✅ 最快，讀近似 C | 太慢，不需要 |
| F5 出來一坨爛泥 / 明顯錯 | 別信 | ✅ 回去盯組語才看得出真相 |
| 要精準 hook 某個點的 offset | 看不出精確位址 | ✅ 組語才有每條指令的位址 |
| OLLVM 控制流平坦化 | F5 常投降 | ✅ 手動追（Ch 27） |
| 要 patch 指令 | 不行 | ✅ 對著組語改 byte |

實務結論：**F5 當第一遍快讀，但關鍵幾行一定回去對組語**。反編譯器是啟發式的、會猜錯，尤其在混淆碼、手寫組語、非標準呼叫慣例的地方。會讀組語，你才有「F5 騙我」時的第二意見。

## 踩雷集錦

1. **在 x86_64 AVD 上逆 `.so` 卻以為在讀 ARM64**：本課最常見的環境錯誤。x86_64 AVD 的 `.so` 是 x86 組語（`rax`/`push`/變長指令），不是 ARM64。要練 ARM64 用 arm64 image 或真機。`readelf -h libfoo.so` 看 `Machine:` 是 `AArch64` 還是 `X86-64` 先確認再逆。
2. **忘了 native 函式 x0/x1 是 JNIEnv/jobject**：把 x0 當成第一個真參數，整個參數對位錯一格。native JNI 函式真參數從 **x2** 起。
3. **把 w 暫存器當成獨立暫存器**：`w0` 就是 `x0` 的低 32-bit，不是另一顆。看到 `mov w0,...` 後 `ret`，回傳值就是 x0 的低 32 位（一個 `int`）。
4. **`adrp` 的位址算錯**：`adrp` 定位到 4KB 頁**邊界**，真正位址要加後面 `add`/`ldr` 的頁內偏移。只看 `adrp` 的立即數會得到錯的位址——一定要連著後面那條一起看。工具通常已幫你算好並註解，別自己拆一半。
5. **把 `cmp`/`cbz` 的條件方向看反**：`cbz x0, L` 是「x0 **等於** 0 才跳」；`b.ne` 是「**不**相等才跳」。條件跳的方向看反，整個 if/else 邏輯就顛倒，繞校驗會繞錯邊。逐條把條件唸成白話（「零就跳」「不等就跳」）再對邏輯。

## 進階：再往深一層

- **條件旗標 nzcv 與 `b.cond` 家族**：`cmp`/`subs`/`adds` 設定 N（負）Z（零）C（進位）V（溢位）四個旗標，`b.eq`（Z=1）`b.hi`（無號大於）`b.lt`（有號小於）等依它們跳。搞混「無號 `b.hs/b.lo`」與「有號 `b.ge/b.lt`」會在比較大小的邏輯上判斷錯——涉及範圍檢查時要分清有號無號。
- **PLT/GOT 與跨 `.so` 呼叫**：呼叫別的 `.so` 的函式（如 `libc` 的 `strcmp`）走 PLT 跳板，你會看到 `bl` 到一段 `adrp x16,...; ldr x17,[x16,...]; br x17`——這是 PLT stub，x16/x17 是它的專用暫存器。Ch 21（ELF）與 Ch 25（PLT hook）會展開。
- **NEON/SIMD 暫存器 v0–v31**：AES 硬體加速、大量資料處理會用 128-bit 的 `v`/`q` 暫存器與 `aese`/`aesmc` 等指令。看到 `aese v0,v1` 這種，恭喜，你找到硬體 AES 了（Ch 23 認演算法會用到這個地標）。
- **BTI / PAC（指標驗證）**：較新 ARMv8.3+ 的 `.so` 開頭可能有 `bti c`（分支目標識別）、返回前有 `paciasp`/`autiasp`（對返回位址簽名/驗證，防 ROP）。逆向時它們是「裝飾指令」，不影響邏輯，但 patch 時若破壞了 PAC 的配對會 crash——認得它們免得被 `paciasp` 這種沒見過的助憶符嚇到。

## 動手練習

1. 用 arm64 AVD（或真機）撈一個真實 App 的 `libxxx.so`，`readelf -h` 確認 `Machine: AArch64`。丟進 Ghidra（免費），找 Ch 19 定位的入口函式，先只看 disassembly：數出函式開頭的 `stp x29,x30` 與結尾的 `ldp ... ret`，標出函式邊界。
2. 在那個函式裡找一條 `adrp` + `add`（或 `adrp` + `ldr`），看 Ghidra 把它註解成什麼字串/全域變數——親眼確認這個 pattern 就是「取全域/字串位址」。
3. 把本章範例二的組語自己對回 C，然後改題：若 `cmp w8,#0x2A` 改成 `cmp w8,#0x100`、`b.ne` 改成 `b.eq`，還原成的 C 邏輯會變成什麼？（練「條件方向」不看反）
4. 用 Python 依本章的位元佈局，算出 `movz x0,#1` 與 `nop`（提示：`nop` = `0xd503201f`）的編碼，理解「patch 一條指令」實際改的是哪 4 個 byte。

## 本章重點整理

- 逆真實 App 逆的是 **ARM64（AArch64）**；x86_64 AVD 的 `.so` 是 x86，要 arm64 image 或真機。
- **AAPCS64**：x0–x7 傳參、x0 放回傳值、x29=fp/x30=lr、x19–x28 是 callee-saved。native JNI 函式 **x0=JNIEnv*、x1=jobject、真參數從 x2 起**。
- 高頻指令：`mov`/`ldr`/`str`（搬移與 load/store）、`bl`/`ret`（call/return）、`cbz`/`b.cond`（條件分支）、`adrp+add`（取全域/字串位址）、`eor`（XOR，加密地標）。
- **stack frame 招牌**：`stp x29,x30,[sp,#-N]!` 開頭、`ldp x29,x30,[sp],#N`+`ret` 結尾；參數常被搬到 x19–x28 保存。
- **F5 反編譯當快讀，關鍵行回去對組語**——反編譯是啟發式的、會騙你，會讀組語是 native 逆向的生存線。

## 自我檢核

- [ ] 不看筆記，能說出 x0–x7、x0、x29、x30 各自的角色，以及 native JNI 函式的第一個真參數在哪個暫存器
- [ ] 看到 `stp x29,x30,[sp,#-0x20]!` 與 `ldp x29,x30,[sp],#0x20; ret`，能指出它們是函式的頭和尾
- [ ] 能解釋 `adrp x0,...` 後面為什麼一定跟一個 `add`/`ldr`，以及這個 pattern 在做什麼
- [ ] 能把一段含 `ldr`/`cmp`/`b.ne`/`mov w0,#1`/`ret` 的組語對回一個 C 的 if 判斷
- [ ] 知道要確認一個 `.so` 是不是 ARM64，該看 `readelf -h` 的哪個欄位

## 延伸閱讀

- **[Arm Architecture Reference Manual for A-profile（官方）](https://developer.arm.com/documentation/ddi0487/latest)**
  - **讀哪裡**：C3「A64 instruction set overview」與各指令的編碼圖；`adrp`/`ldr`/`bl`/`b.cond` 的位元佈局
  - **和本章的關聯**：本章手算的指令編碼、每條指令的精確語意，最終仲裁都在這；逆到沒見過的指令來這查
- **[Procedure Call Standard for the Arm 64-bit Architecture（AAPCS64）](https://github.com/ARM-software/abi-aa/blob/main/aapcs64/aapcs64.rst)**
  - **讀哪裡**：「Parameter passing」與「General-purpose registers」那節（x0–x7 傳參、callee-saved 清單）
  - **為什麼值得讀**：本章把組語對回 C 靠的就是這套規則，這是它的權威原文
- **[Azeria Labs — ARM Assembly Basics](https://azeria-labs.com/writing-arm-assembly-part-1/)**
  - **這篇說什麼**：從零、對逆向者友善的 ARM 組語教學（含 AArch64）
  - **讀哪裡**：暫存器、load/store、分支那幾篇；比官方手冊好入門
  - **前提知識**：讀過本章即可，它補更多小範例讓你練手感
- **[Ghidra 官方文件與 A64 反組譯](https://ghidra-sre.org/)**
  - **讀哪裡**：載入 `.so`、看 Listing（disassembly）與 Decompiler 視窗對照
  - **和本章的關聯**：本章教你讀組語，Ghidra 是免費且能同時看組語與 F5 的工具，Ch 22 深入

下一章我們拆 `.so` 這個檔案本身的結構——它是 **ELF** 格式。你已經會讀裡面的 ARM64 指令，但那些指令住在 ELF 的哪個 section？符號表、`.init_array`（Ch 19 說的反調試藏身處）、GOT/PLT（本章提的跨模組跳板）在檔案裡怎麼佈局？Ch 21 用 Python 實跑解析一個 ELF header，把 `.so` 的骨架攤開給你看。

→ [Ch 21 ELF / .so 結構](./21-elf-so-structure.md)
