# Ch 24 — 跨平台一瞥：Windows PE / ARM64

> **目標**：理解在 Linux x86-64 之外的兩個重要逆向目標——Windows PE binary 和 ARM64 binary——的結構差異，以及你在 ELF/x86-64 學到的方法論怎麼遷移。Windows PE 部分因跨平台工具限制，標「未實測，理論預期」。ARM64 部分用 `aarch64-linux-gnu-gcc` 交叉編譯並真跑 objdump 對照（已安裝）。

> **環境**：WSL2 / Linux x86-64。ARM64：`aarch64-linux-gnu-gcc` + `aarch64-linux-gnu-objdump`（真跑）。Windows PE：標「未實測」（需 Windows + MSVC/cdb/WinDbg 環境）。

## 為什麼需要這個？

逆向的對象不只是 Linux ELF。現實中：

- **Windows PE**：大量商業軟體、惡意程式、漏洞研究目標是 PE——結構和 ELF 相近，但有 IAT/PE header/SEH 等差異。
- **ARM64**：手機（Android/iOS）、Apple Silicon Mac、嵌入式裝置的 binary 都是 ARM64。你的 `android_reversing`、`ios_macos_exploitation`、`arm` 課都是 ARM64 的應用——本章在逆向視角補一張橋接地圖。

同一套心智模型（認 calling convention、追資料流、辨識 idiom）可以遷移，但有若干差異需要適應。

## 先建立直覺：ELF vs PE 的結構對比

```
ELF（Linux）                           PE（Windows）
────────────────────────────────────────────────────────────
ELF header                             DOS header（MZ magic）
Program headers（段表）                 → PE header（PE magic 0x4550）
Section headers                           → Optional header
  .text   → code                         → Section headers
  .data   → initialized data               .text → code
  .bss    → uninitialized data             .data → data
  .plt    → Procedure Linkage Table        .rdata → readonly data
  .got    → Global Offset Table            .idata → import table（IAT）
  符號表（strip 後消失）                   .edata → export table（EAT）
  .dynamic → 動態連結資訊               DebugDirectory（PDB path）

動態函式解析        PLT/GOT                     IAT（Import Address Table）
entry point         _start → main               WinMain / mainCRTStartup → main
calling conv        SysV ABI（rdi,rsi,rdx...）  Windows x64 ABI（rcx,rdx,r8,r9）
exception           DWARF unwind (.gcc_except)  SEH（Structured Exception Handling）
```

### PE 格式快速地圖（理論預期）

```
PE 檔案佈局（理論，未實測）

 offset 0x00: 4D 5A ("MZ") — DOS header magic
 offset 0x3c: PE header offset（e_lfanew）
 PE header: 50 45 00 00 ("PE\0\0") + Machine(0x8664=x64) + NumberOfSections
 Optional header: AddressOfEntryPoint, ImageBase, SizeOfCode
 Section table: N × 40-byte entries（name, VirtualAddress, SizeOfRawData, ...）
```

識別工具（需 Windows 或 Wine 環境）：`pefile`（Python library，可在 Linux 使用）、`readpe`、`dumpbin`（Windows SDK）。

## IAT vs PLT/GOT

動態函式解析是逆向的重要線索——知道 binary 呼叫了哪些 API，就知道它在做什麼。

### ELF 的 PLT/GOT（已熟悉）

動態 binary 呼叫 `printf` → 跳到 PLT stub `printf@plt` → 第一次呼叫時 resolver 填入 GOT entry → 之後直接跳 GOT 裡的地址。逆向時 `objdump -d` 可以看到 `call printf@plt`——函式名直接可讀。

### Windows PE 的 IAT（理論預期，未實測）

PE 的 Import Address Table（IAT）在載入時由 Windows loader 填入真實函式地址。逆向時（Ghidra/IDA）看到：

```asm
; IAT 的呼叫模式（理論預期）
call qword ptr [CreateFileA]    ; 透過 IAT 間接呼叫
call qword ptr [VirtualAlloc]
call qword ptr [WriteProcessMemory]
```

分析工具：`dumpbin /imports target.exe`（需 Windows + MSVC）輸出所有 import 函式名；在 Ghidra/IDA 的 .idata section 可以直接看 IAT 的 symbol table。

常見的重要 Windows API 分類：

| API 分類 | 逆向意義 |
|---|---|
| `CreateFile`/`ReadFile`/`WriteFile` | 檔案 IO |
| `VirtualAlloc`/`VirtualProtect` | 記憶體操作（可能是 shellcode 注入）|
| `CreateProcess`/`ShellExecute` | 程序啟動 |
| `RegOpenKey`/`RegSetValue` | 登錄檔操作（持久化）|
| `WSAStartup`/`connect`/`send`/`recv` | 網路通訊（C2 跡象）|
| `CryptAcquireContext`/`CryptEncrypt` | 加密操作（勒索病毒常見）|

### Windows x64 Calling Convention 差異（未實測）

Windows x64 ABI 和 SysV ABI 的差異：

```
SysV ABI（Linux x64）：    Windows x64 ABI：
  arg1 → rdi                 arg1 → rcx
  arg2 → rsi                 arg2 → rdx
  arg3 → rdx                 arg3 → r8
  arg4 → rcx                 arg4 → r9
  arg5→ r8                   arg5+ → stack（同 SysV）
  ...                        Shadow space：呼叫方保留 32 bytes 給被呼叫方
```

逆向時遇到 Windows binary，函式開頭若看到 `mov [rsp+0x08],%rcx` 這樣「把第一個 register 參數存到 shadow space」的模式，就是 Windows ABI 的典型序言。

### SEH（Structured Exception Handling）（未實測）

Windows 的例外處理機制和 Linux 的 DWARF unwind 機制不同：

- 32-bit：`FS:[0]` 鏈（exception handler chain 串在 TEB 裡）
- 64-bit：`__ImageBase` 相對的 unwind info，儲存在 `.pdata` section

逆向 SEH 的意義：惡意程式有時用 SEH 作為控制流混淆工具（用 exception 觸發 handler，跳到非常規位址）。識別：看 `.pdata` section 的存在，或看到大量 `__try`/`__except` 的 asm 痕跡（`cmp` + exception handler 地址）。

## ARM64（AArch64）逆向

ARM64 是你在 Android、iOS、Apple Silicon、嵌入式目標上會遇到的 ISA。本節用真實 cross-compiled binary 作對照。

### ARM64 的關鍵特性（和 x86-64 的差異）

```
x86-64                        ARM64（AArch64）
─────────────────────────     ──────────────────────────────────
變長指令（1-15 bytes）         固定 4 bytes 每條指令
CISC（複雜指令）               RISC（精簡指令，load/store 架構）
rax/rbx/rcx/rdx…暫存器       x0-x30（64-bit）/ w0-w30（32-bit low half）
call / ret                    bl（branch with link）/ ret（用 lr = x30）
許多記憶體直接操作             記憶體操作只有 ldr / str
stack frame: rbp as FP        x29 = frame pointer，x30 = link register
```

### ARM64 暫存器約定

| 暫存器 | 別名 | 用途 |
|---|---|---|
| x0-x7 | — | 函式參數（前 8 個）/ 返回值（x0）|
| x8 | xr | Indirect result（大型返回值的指標）|
| x9-x15 | — | Caller-saved 暫存器 |
| x16-x17 | ip0/ip1 | Intra-procedure call scratch |
| x18 | — | Platform register（有些 ABI 保留）|
| x19-x28 | — | Callee-saved 暫存器 |
| x29 | fp | Frame pointer |
| x30 | lr | Link register（`bl` 儲存返回地址）|
| sp | — | Stack pointer |

### 真跑：ARM64 交叉編譯並對照 x86-64

```bash
$ cat > /tmp/re_part3/arm_simple.c << 'EOF'
#include <stdio.h>

int add(int a, int b) { return a + b; }

int factorial(int n) {
    if (n <= 1) return 1;
    return n * factorial(n - 1);
}

int main(void) {
    printf("add(3,4) = %d\n", add(3, 4));
    printf("fact(5) = %d\n", factorial(5));
    return 0;
}
EOF

$ aarch64-linux-gnu-gcc -O0 -o /tmp/re_part3/arm_simple /tmp/re_part3/arm_simple.c
$ file /tmp/re_part3/arm_simple
arm_simple: ELF 64-bit LSB pie executable, ARM aarch64, ... dynamically linked
```

ARM64 的 `add` 函式（真跑 objdump 輸出）：

```bash
$ aarch64-linux-gnu-objdump -d /tmp/re_part3/arm_simple | grep -A 10 '<add>:'
```

```asm
0000000000000754 <add>:
 754:  d10043ff   sub   sp, sp, #0x10   ; 開 stack frame（沒有 rbp push）
 758:  b9000fe0   str   w0, [sp, #12]   ; 存參數 a（w0 = 32-bit 的 x0）
 75c:  b9000be1   str   w1, [sp, #8]    ; 存參數 b（w1 = 32-bit 的 x1）
 760:  b9400fe1   ldr   w1, [sp, #12]   ; 載入 a
 764:  b9400be0   ldr   w0, [sp, #8]    ; 載入 b
 768:  0b000020   add   w0, w1, w0      ; w0 = a + b（結果在 w0 = 返回值）
 76c:  910043ff   add   sp, sp, #0x10   ; 還原 sp
 770:  d65f03c0   ret                   ; 返回（lr = x30 的值）
```

ARM64 的 `main` 函式（函式呼叫和 calling convention）：

```asm
00000000000007b4 <main>:
 7b4:  a9bf7bfd   stp   x29, x30, [sp, #-16]!  ; 存 fp 和 lr 到 stack（prologue）
 7b8:  910003fd   mov   x29, sp                 ; fp = sp
 7bc:  52800081   mov   w1, #0x4                ; 參數 b = 4（w1）
 7c0:  52800060   mov   w0, #0x3                ; 參數 a = 3（w0）
 7c4:  97ffffe4   bl    754 <add>               ; 呼叫 add（bl = branch + link）
 7c8:  2a0003e1   mov   w1, w0                  ; 返回值 w0 移到 w1（printf 的第二個 arg）
 7cc:  90000000   adrp  x0, 0                   ; ┐ 載入 format string 到 x0
 7d0:  91206000   add   x0, x0, #0x818          ; ┘ "add(3,4) = %d\n"
 7d4:  97ffff97   bl    630 <printf@plt>         ; 呼叫 printf
 7d8:  528000a0   mov   w0, #0x5                ; 參數 n = 5（w0）
 7dc:  97ffffe6   bl    774 <factorial>          ; 呼叫 factorial
 7e0:  2a0003e1   mov   w1, w0
 7e4:  90000000   adrp  x0, 0
 7e8:  9120a000   add   x0, x0, #0x828          ; "fact(5) = %d\n"
 7ec:  97ffff91   bl    630 <printf@plt>
 7f0:  52800000   mov   w0, #0x0                ; 返回 0
 7f4:  a8c17bfd   ldp   x29, x30, [sp], #16    ; 還原 fp 和 lr（epilogue）
 7f8:  d65f03c0   ret
```

### ARM64 vs x86-64 逆向對照表

| 概念 | x86-64 | ARM64 |
|---|---|---|
| 函式呼叫 | `call addr`（push rip; jmp）| `bl addr`（lr=PC+4; 跳去）|
| 返回 | `ret`（pop rip）| `ret`（跳到 lr）|
| Stack prologue | `push rbp; mov rbp,rsp` | `stp x29,x30,[sp,#-N]!; mov x29,sp`|
| 第一個參數 | `rdi` | `x0`（64-bit）/ `w0`（32-bit）|
| 記憶體讀 | `mov eax,[rbx+8]` | `ldr w0, [x1, #8]`（只有 ldr/str）|
| 比較 + 分支 | `cmp eax,1; jle target` | `cmp w0, #1; b.le target`|
| 位址計算 | `lea rax,[rip+offset]` | `adrp x0,page; add x0,x0,#off`|
| 指令大小 | 1-15 bytes | 固定 4 bytes |

### ARM64 的 idiom：`adrp` + `add`

ARM64 沒有 x86-64 的 `lea rip+offset` 那樣的單指令 PC-relative 定址，因為指令固定 4 bytes，可編碼的 offset 有限。取而代之用兩條：

```asm
adrp x0, 0         ; 取當前 PC 的頁基址（4KB 頁對齊）
add  x0, x0, #0x818 ; 加頁內 offset
; 合起來 = &"add(3,4) = %d\n"
```

逆向時看到 `adrp; add` 兩條連續，就是「取某個靜態資料的地址」——和 x86-64 的 `lea rip+N,%reg` 等價。

### `stp`/`ldp`：Store/Load Pair

ARM64 的 `stp x29, x30, [sp, #-16]!` = 原子地把兩個 64-bit 暫存器存到 sp-16（pre-decrement）。這是最常見的 prologue 形式，對應 x86-64 的 `push rbp`。

`ldp x29, x30, [sp], #16` = 從 sp 載入並 sp+=16（post-increment），對應 x86-64 的 `pop rbp`。

## 同一套方法論，不同平台的適應

逆向時 ELF/PE、x86-64/ARM64 的切換，核心工具箱不變：

1. **找 main**：ELF = entry → `_start`；PE = entry → `WinMain`/`main` 通過 CRT 啟動函式。ARM64 = 同 ELF，但 calling convention 不同。
2. **字串 xref**：`strings -t x`，跨平台通用。
3. **追資料流**：追蹤函式參數的 register（ELF = rdi; PE = rcx; ARM64 = x0）。
4. **認出 idiom**：ARM64 的 `adrp;add` = x86-64 的 `lea rip+N`；`bl` = `call`；`ret` = `ret`。
5. **動態驗假設**：gdb for ELF/ARM64（qemu-user 或 native）；WinDbg/x64dbg for PE。

## 踩雷集錦

1. **ARM64 的 `w0` 和 `x0` 搞混**：`w0` 是 `x0` 的低 32 bits——`int` 型別用 `w`，`pointer` / `long` 用 `x`。看到 `str w0` 後面又 `ldr x0` 是正常的。

2. **ARM64 的 `bl` 和 x86 的 `call` 差異**：`bl` 把返回地址存到 `lr`（x30），不 push 到 stack——所以如果函式再呼叫另一個函式（non-leaf），它要先 `stp ..., x30, [sp,#-N]!` 保存 lr，否則 lr 就被覆寫了。逆向時看到沒有 save lr 的 `bl`，這個函式是 leaf function（不再呼叫其他函式）。

3. **PE 的 ImageBase 讓 VA 對不上**：PE 的 VA 是以 `ImageBase` 為基底（預設 `0x140000000`），分析時看到的地址和 section offset 要加上 ImageBase 才對得上——反編譯器通常自動處理，但手動算時要注意。

4. **Windows shadow space 看起來像「多餘的 push」**：Windows x64 ABI 要求 caller 在 call 前保留 32 bytes 的 shadow space（給 callee 用），所以 main 開頭常見 `sub rsp,0x28`（32 bytes + 8 bytes 對齊）。這不是在 push 任何東西——只是 ABI 要求的預留空間。

5. **ARM64 的固定指令長度讓 junk byte 技巧無效**：x86 的反反組譯靠插入一個 byte 讓 disassembler 錯位，ARM64 固定 4 bytes 對齊，插 junk byte 不可能讓指令錯位——ARM64 binary 的控制流混淆通常用其他手法（間接跳轉表、opaque predicate）。

## 進階：再往深一層

- **PE 格式深入**：`pefile` Python library 可在 Linux 解析 PE 格式，讀 IAT、EAT、Resources、Overlay——接 `malware_analysis` 課的 Windows 惡意程式章節。
- **ARM64 PAC（Pointer Authentication Codes）**：Apple Silicon 和部分 ARM64 實作啟用 PAC，在指標高位元儲存 authentication code，讓 ROP 更難——`ios_macos_exploitation` 課的 PAC bypass 章節。
- **Thumb-2 / AArch32**：舊 ARM 裝置用 32-bit Thumb-2 ISA，和 AArch64 不同（但邏輯相似）；`arm` 課的 Cortex-M 章節涵蓋 AArch32。

## 本章重點整理

- **PE vs ELF**：結構相近，差異在 IAT（PE）vs PLT/GOT（ELF）、Windows x64 ABI（rcx/rdx/r8/r9）vs SysV ABI（rdi/rsi/rdx/rcx）、SEH vs DWARF unwind。（Windows PE 理論，未實測）
- **ARM64 calling convention**：參數 x0-x7（w0-w7），返回值 x0；`bl` = 呼叫（lr←PC+4）；`stp x29,x30` = 保存 fp 和 lr。
- **ARM64 的 `adrp;add`** = x86-64 的 `lea rip+N,%reg`——PC-relative 靜態地址計算。
- **固定 4 bytes 指令**：ARM64 反反組譯的 junk byte 技巧無效；load/store 是唯一記憶體存取指令。
- 同一套方法論（字串 xref、入口追 main、追資料流、認 idiom）跨平台可遷移，只需調整 register 名和 calling convention。

## 自我檢核

- [ ] 我能解釋 IAT（PE）和 PLT/GOT（ELF）的共同點和差異
- [ ] 我知道 Windows x64 ABI 的前四個參數是 rcx/rdx/r8/r9
- [ ] 我能從 ARM64 asm 認出 `stp x29,x30` 是函式 prologue
- [ ] 我能解釋 ARM64 的 `adrp;add` 兩條指令合起來做什麼（對應 x86-64 哪條指令）
- [ ] 我知道 ARM64 `bl` 把返回地址存到 lr（x30）而不是 stack，並能說明這對 leaf function 的意義

## 延伸閱讀

1. **ARM Architecture Reference Manual（AArch64 for Armv8-A）**（[https://developer.arm.com/documentation/ddi0487](https://developer.arm.com/documentation/ddi0487)）
   - 學什麼：AArch64 指令集的完整參考，calling convention 的官方定義（AAPCS64）
   - 前提：能讀英文規格

2. **《Reverse Engineering for Beginners》Part II（ARM）** — Dennis Yurichev（[免費](https://beginners.re/)）
   - 學什麼：大量「C → ARM asm」對照，涵蓋 AArch32 和 AArch64，教材風格和本課相同
   - 前提：本課 Part 1

3. **pefile Python library**（[https://github.com/erocarrera/pefile](https://github.com/erocarrera/pefile)）
   - 學什麼：在 Linux 解析 PE 格式（IAT、EAT、sections、resources），可用於惡意程式初步分析
   - 前提：Python 基礎

Part 3「目標識別：逆出結構」到此完成。練習 C 把本 Part 的格式逆向技術做成 ground-truth 任務。

→ [練習 C：逆一個檔案格式並寫出 parser](./practice-c-reverse-a-format-write-parser.md)
