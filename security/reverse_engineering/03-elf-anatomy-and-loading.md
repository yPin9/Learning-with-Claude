# Ch 3 — ELF 解剖與載入

> **目標**：從逆向者的視角解剖一個 ELF binary——它的 header、段（segment）與節（section）、符號表、PLT/GOT 動態連結機制，以及 `_start → __libc_start_main → main` 的啟動鏈。學完你能看懂 `readelf`/`objdump` 的骨架輸出，知道逆向時該先看哪幾塊，並回答兩個關鍵問題：**為什麼 strip 也刪不掉 import？entry point 不是 main，那真正的 main 怎麼找？**

> **環境**：WSL2 / Linux x86-64，gcc 11.4.0 + readelf + objdump + nm。所有 `readelf`/`objdump` 輸出皆真跑貼出，目標是 Ch 1 那個 `check` 程式編譯後 strip 的版本（`ch1_strip`，PIE、動態連結、已 strip）。

## 為什麼需要這個？

你接過使用者的 [`elf_linking`](../../compilers/elf_linking/README.md) 課，那門從**連結器/載入器怎麼運作**的角度講 ELF。這章不重複那個視角，而是問一個逆向者才會問的問題：**手上這團 bytes，哪幾塊對我逆向有用？** 逆向者不需要能寫一個 linker，但必須能在三十秒內從 ELF 骨架讀出：這是什麼架構、strip 了沒、動態連結誰、entry 在哪、`.text` 有多大、它 import 了哪些危險 API。

不懂 ELF 骨架，你會犯兩個致命錯：一是**找不到 main**（傻傻從 entry point 讀起，讀了一堆 CRT 啟動樁還以為是主邏輯）；二是**看到 `call strcmp@plt` 一頭霧水**（不懂 PLT/GOT，你不知道那個 `@plt` 是在呼叫 libc）。這兩件事天天都會遇到，這章一次講清。

## 先建立直覺：ELF 有兩種「看法」

ELF（Executable and Linkable Format）最容易搞混的一點：**它有兩張互相重疊、但目的不同的地圖。**

```
        ELF 檔案 (在硬碟上)                    載入到記憶體後 (執行時)
   ┌──────────────────────┐              ┌──────────────────────┐ 高位址
   │ ELF header           │ 骨架/entry   │  stack (往下長)       │
   ├──────────────────────┤              ├──────────────────────┤
   │ program headers      │──┐           │       ...            │
   │ (segment 表)         │  │ 告訴載入器 │  libc.so 映射         │
   ├──────────────────────┤  │ 怎麼載入   ├──────────────────────┤
   │ .interp  .dynsym ... │  │           │  [heap] (往上長)      │
   │ .plt .plt.sec        │  │           ├──────────────────────┤
   │ .text  ← 你逆的指令  │◄─┘ LOAD R E  │  RW 段: .data .bss.got│
   │ .rodata .eh_frame    │◄─  LOAD R    │  R  段: .rodata       │
   │ .init_array .got     │◄─  LOAD RW   │  R E段: .text .plt    │ ← 指令映射在這
   │ .data .bss           │              │  R  段: header+.dynsym│
   ├──────────────────────┤              ├──────────────────────┤
   │ section headers      │  給連結器/    │  ld-linux.so (動態    │
   │ (.symtab strip 後沒) │  工具用       │   連結器本身)         │
   └──────────────────────┘              └──────────────────────┘ 低位址
```

兩張地圖的分工是這章的骨幹：

- **program headers（段 / segment）** 是**載入單位**——載入器（kernel + `ld-linux.so`）只看這個，它說「把檔案這段 bytes 映射到那塊記憶體、給這些權限（R/W/X）」。執行時真正存在的是段。
- **section headers（節 / section）** 是**連結/工具單位**——`.text`/`.rodata`/`.plt`/`.got` 這些細分，給連結器和逆向工具用。**strip 只動得了部分 section 資訊（尤其刪掉 `.symtab`），但段照樣能載入、程式照跑。**

一句話記住：**段是給機器載入的，節是給人（和工具）理解的。逆向者主要看節，但要知道節被歸進哪個段（決定了它執行時的權限）。**

## ELF header：三十秒體檢

拿到 binary 第一件事，`readelf -h`。這是 Ch 1「偵察」的第一手情報（真跑，`ch1_strip`）：

```
ELF Header:
  Magic:   7f 45 4c 46 02 01 01 00 ...        # 7f 'E' 'L' 'F' → 這是 ELF
  Class:                             ELF64     # 64-bit
  Data:                              2's complement, little endian
  Type:                              DYN (Position-Independent Executable file)
  Machine:                           Advanced Micro Devices X86-64   # 架構
  Entry point address:               0x1080    # ← 執行從這裡開始（不是 main！）
  Start of program headers:          64 (bytes into file)
  Start of section headers:          12616 (bytes into file)
  Number of program headers:         13         # 13 個段
  Number of section headers:         29         # 29 個節
```

逆向者從這幾行讀出：

- **Magic `7f 45 4c 46`** = `\x7fELF`，確認是 ELF（`file` 也是靠這個判斷）。
- **Class ELF64 + Machine X86-64** = 這是 64 位 x86-64，你要用 x86-64 的 asm 知識逆。ARM binary 這裡會是 `AArch64`，逆法不同（[Ch 24](./24-cross-platform-pe-arm64.md)）。
- **Type `DYN` (PIE)** 這點很重要：現代 Linux 預設編出 **PIE（Position-Independent Executable）**，位址是相對的、載入時隨機（ASLR）。所以你看到的 `0x1080` 是**檔案內偏移**，執行時會加一個隨機基底。逆向靜態看偏移沒問題，但動態要記得加基底。老式 non-PIE 是 `EXEC` 型、固定位址。
- **Entry point `0x1080`**：程式第一條指令的位址。**這不是 `main`**——是 CRT 啟動樁 `_start`。這個誤區稍後專門拆。

## Program headers（段）：載入單位

`readelf -l` 看段。逆向者不用細背每個段，但要認得 **LOAD 段的權限**（真跑節選）：

```
Program Headers:
  Type           Offset   VirtAddr  ...  Flags  Align
  INTERP         0x318    0x318     ...  R      0x1
      [Requesting program interpreter: /lib64/ld-linux-x86-64.so.2]
  LOAD           0x0000   0x0000    ...  R      0x1000      ← header + 唯讀 metadata
  LOAD           0x1000   0x1000    ...  R E    0x1000      ← 可執行：.text .plt 在這
  LOAD           0x2000   0x2000    ...  R      0x1000      ← 唯讀：.rodata 在這
  LOAD           0x2db0   0x3db0    ...  RW     0x1000      ← 可寫：.data .bss .got 在這
```

逆向含義：

- **`R E` 段是你逆的指令所在**——`.text`。它 R+X 不可寫（除非程式自修改 code，那是混淆手法，[Ch 23](./23-obfuscation-anti-reversing.md)）。
- **`RW` 段是資料** ——`.data`/`.bss`/`.got`。逆向找「可被竄改的全域狀態」看這裡。注意 `.got` 在 RW 段，這是 GOT hijacking 攻擊的基礎（pwn 天梯用）。
- **`INTERP` 段指定 `/lib64/ld-linux-x86-64.so.2`** ——動態連結器。它的存在告訴你「這是動態連結的 binary，執行時會載入 libc」。靜態連結的 binary 沒有 INTERP 段（[Ch 22](./22-reversing-stripped-static-binaries.md) 講靜態連結為何更難逆）。

`readelf -l` 尾巴的 **Section to Segment mapping** 把兩張地圖接起來（真跑節選）：

```
  Segment Sections...
   03     .init .plt .plt.got .plt.sec .text .fini       ← R E 段裝這些
   04     .rodata .eh_frame_hdr .eh_frame                 ← R  段裝這些
   05     .init_array .fini_array .dynamic .got .data .bss ← RW 段裝這些
```

這就是「節被歸進哪個段」的具體答案：`.text` 在可執行段，`.got`/`.data` 在可寫段。

## Section headers（節）：逆向者關心哪幾個

段負責載入，但逆向者日常盯的是**節**。`readelf -S` 全列，逆向者只需認得這幾個（真跑節選）：

```
  [13] .plt      PROGBITS  0000000000001020    ← 呼叫外部函式的跳板
  [15] .plt.sec  PROGBITS  0000000000001060    ← （CET 版）真正的 PLT stub
  [16] .text     PROGBITS  0000000000001080    ← 你逆的指令主體
  [18] .rodata   PROGBITS  0000000000002000    ← 唯讀常數、字串字面量
  [24] .got      PROGBITS  0000000000003fb0    ← 動態符號的位址表
  [25] .data     PROGBITS  0000000000004000    ← 初始化過的可寫全域
  [26] .bss      NOBITS    0000000000004010    ← 未初始化全域（檔案裡不佔空間）
  [6]  .dynsym   DYNSYM    00000000000003d8    ← 動態符號表（import/export）
```

| 節 | 裝什麼 | 逆向者為什麼在乎 |
|---|---|---|
| `.text` | 機器碼指令 | **主戰場**，你逆的邏輯都在這 |
| `.rodata` | 字串字面量、唯讀常數、jump table | 線索金礦——`strings` 抓的多半在這；jump table 也在（Ch 2） |
| `.data` | 初始化過的全域變數 | 找可變全域狀態、設定、預設值 |
| `.bss` | 未初始化全域（載入時清零） | `NOBITS`——檔案裡不佔空間，只記大小，載入才配置 |
| `.plt` / `.plt.sec` | 呼叫外部函式的跳板 | 看到 `call xxx@plt` 就是在這，代表呼叫 libc |
| `.got` | 外部符號的執行期位址表 | 動態連結填位址的地方；GOT hijack 目標 |
| `.dynsym` | 動態符號表 | **strip 刪不掉**——import 的 libc 函式名還在這（下一節） |
| `.symtab` | 完整符號表（你的函式名） | **strip 刪掉的就是它**——沒了你的 `check`/`main` 名字 |

## 符號表：為什麼 strip 刪掉了 `main`，卻刪不掉 `strcmp`

這是逆向者最該搞懂的 ELF 機制之一。ELF 有**兩張符號表**，命運不同：

- **`.symtab`**：完整符號表，含你寫的**所有**函式與全域變數名（`check`、`main`、靜態函式…）。它**純粹給連結和除錯用，執行時不需要**。所以 `strip` 一刀砍掉它。
- **`.dynsym`**：動態符號表，只含**動態連結相關**的符號——你 import 的 libc 函式、你 export 給別人的函式。它**執行時動態連結器必須用**（要靠符號名去 libc 裡找 `strcmp` 的真實位址）。所以 `strip` **不能**砍它，砍了程式就跑不起來。

真跑對照。strip 後 `nm`（讀 `.symtab`）：

```
$ nm ch1_strip
nm: ch1_strip: no symbols        # ← .symtab 沒了，你的 check/main 名字消失
```

但 `nm -D`（讀 `.dynsym`）照樣有東西：

```
$ nm -D ch1_strip
                 U __libc_start_main@GLIBC_2.34    # U = Undefined，要向 libc 借
                 U puts@GLIBC_2.2.5
                 U strcmp@GLIBC_2.2.5
```

`readelf --dyn-syms` 更清楚（真跑節選）：

```
Symbol table '.dynsym' contains 8 entries:
   Num:    Value    Type    Bind   Ndx Name
     3: ...         FUNC    GLOBAL UND puts@GLIBC_2.2.5
     4: ...         FUNC    GLOBAL UND strcmp@GLIBC_2.2.5     # UND = 未定義，import
```

**逆向含義極大**：strip 過的 binary 你雖然丟了自己的函式名，但**它 import 了哪些 libc 函式，一覽無遺**。看到 `strcmp` → 它在比字串；看到 `system`/`execve` → 它在執行命令（malware 分析的紅旗）；看到 `ptrace` → 它可能在反調試。**import 表是 stripped binary 最可靠的行為線索之一**，因為 strip 刪不掉它。這正是 Ch 1 資訊落差表裡「匯入的函式：strip 也刪不掉」那一列的機制解釋。

## PLT/GOT：`call strcmp@plt` 到底在幹嘛

你已經多次看到 `call strcmp@plt`。這個 `@plt` 是動態連結的核心機制，逆向天天遇到，必須懂。

問題：`ch1_strip` 是動態連結的，`strcmp` 的真正 code 在 `libc.so` 裡，執行時才載入、位址還隨機（ASLR）。編譯 `check` 時**根本不知道 strcmp 會在哪**。怎麼辦？

答案是 **PLT（Procedure Linkage Table）+ GOT（Global Offset Table）** 的間接跳轉：

- **GOT** 是一張「執行期位址表」，每個 import 的函式有一格。動態連結器在載入/首次呼叫時，把 `strcmp` 的真實位址填進它的 GOT 格。
- **PLT** 是一小段跳板 code，`call strcmp@plt` 實際跳到 PLT stub，stub 再 `jmp *（strcmp 的 GOT 格）`——間接跳到 GOT 裡填好的真實位址。

真跑看這條鏈。`check` 裡的呼叫點：

```asm
    118a:  call   1070 <strcmp@plt>      ; 呼叫 PLT stub，不是 libc 本體
```

跳到 `0x1070` 的 PLT stub（`objdump -d -j .plt.sec`，真跑）：

```asm
0000000000001070 <strcmp@plt>:
    1070:  endbr64
    1074:  bnd jmp *0x2f55(%rip)        # 3fd0 <strcmp@GLIBC_2.2.5>
```

`jmp *0x2f55(%rip)` 間接跳到 GOT 位址 `0x3fd0`。那格裝什麼？看重定位表（`readelf -r`，真跑）：

```
Relocation section '.rela.plt' contains 2 entries:
  Offset          Type              Sym. Name
  000000003fc8    R_X86_64_JUMP_SLO puts@GLIBC_2.2.5
  000000003fd0    R_X86_64_JUMP_SLO strcmp@GLIBC_2.2.5    # ← GOT[0x3fd0] = strcmp
```

整條鏈接起來：

```
   check 裡:  call 0x1070 (strcmp@plt)
                    │
                    ▼
   PLT stub:   jmp *GOT[0x3fd0]        ← 間接跳
                    │
                    ▼
   GOT[0x3fd0]: 動態連結器填入的 strcmp 真實位址（在 libc.so 內）
                    │
                    ▼
               libc 的 strcmp 本體
```

**逆向含義**：看到 `call xxx@plt` 你立刻知道「這在呼叫外部函式 xxx（多半是 libc）」，不用去追 GOT。反組譯器（objdump/Ghidra）會自動把 PLT stub 標成 `strcmp@plt` 幫你解讀，靠的就是把 PLT → GOT → 重定位表這條鏈接起來。這也是為什麼 stripped binary 你還看得到 `strcmp@plt`——那個名字來自 `.dynsym`，strip 刪不掉。

> 進階：首次呼叫時 GOT 還沒填真實位址，會觸發**延遲綁定（lazy binding）**——第一次跳去動態連結器解析、填好 GOT 再跳。`elf_linking` 課細講。逆向者知道「GOT 執行期才填、可被竄改」就夠了。

## 啟動鏈：entry 不是 main，怎麼找到真正的 main

最後解掉那個懸念：**entry point `0x1080` 不是 `main`。** 它是 CRT（C runtime）的 `_start`，負責在呼叫 `main` 前做初始化（設好 argc/argv、初始化 libc、跑 constructor）。逆向者若不知道，會從 `0x1080` 讀起，讀一堆與主邏輯無關的 CRT 樣板。

真跑看 `ch1_strip` 的 entry（`objdump -d`，這是 stripped 版，所以 objdump 只能用 dynsym 標籤，函式全顯示成 `strcmp@plt+offset`——這本身就是逆向 stripped binary 的真實體驗）：

```asm
1080:  endbr64
1084:  xor    %ebp,%ebp             ; ┐ _start 的標準開場：清 rbp
1086:  mov    %rdx,%r9              ; │ 準備 __libc_start_main 的參數
1089:  pop    %rsi                  ; │ rsi = argc（從 stack 拿）
108a:  mov    %rsp,%rdx             ; │ rdx = argv
...
1098:  lea    0xfa(%rip),%rdi       # 1199 ← ★ rdi = main 的位址！(0x1080+0x9f+0xfa=0x1199)
109f:  call   *0x2f33(%rip)         # 3fd8 <__libc_start_main> ← 呼叫它
10a5:  hlt
```

**找到 main 的關鍵在 `0x1098`**：`_start` 把 `main` 的位址 load 進 `%rdi`（`__libc_start_main` 的第一個參數就是 main 指標），然後 `call __libc_start_main`。`lea 0xfa(%rip),%rdi` 算出的目標是 `0x1199`——**那就是真正的 main。**

驗證（用沒 strip 的副本 `nm`，這是我們的 ground-truth）：

```
$ nm ch1_O0 | grep -E ' main$| _start$'
0000000000001080 T _start        ; entry，符合 header 的 0x1080
0000000000001199 T main          ; 果然是 0x1199，和 _start 裡 lea 出來的一致
```

完美吻合。所以**在 stripped PIE 裡找 main 的通用手法**：

1. `readelf -h` 拿 entry point（`_start`）。
2. 反組譯 `_start`，找 `call __libc_start_main`（`0x3fd8` 的 GOT 格靠 `readelf -r` 認出是 `__libc_start_main`，真跑：`3fd8 R_X86_64_GLOB_DAT __libc_start_main@GLIBC_2.34`）。
3. **緊鄰那個 call 前、load 進 `%rdi` 的位址就是 main。** 這裡是 `lea 0xfa(%rip),%rdi` → `0x1199`。

Ghidra/IDA 會自動幫你認出 main 並標好，但你要懂**它怎麼認的**——就是這條鏈。手法失效時（自製 CRT、混淆），你得手動走一遍。

整條啟動鏈：

```
  entry 0x1080 <_start>
        │  設好 argc/argv，把 main 位址放 rdi
        ▼
  __libc_start_main   (libc 內，透過 GOT[0x3fd8] 呼叫)
        │  初始化 libc、跑 .init_array constructor
        ▼
  main  0x1199        ← 你真正想逆的地方從這開始
```

## 對比與取捨

| 概念 | 是什麼 | 逆向者怎麼用 |
|---|---|---|
| 段（program header） | 載入單位，決定記憶體 R/W/X | 看 `.text` 在哪個可執行段、`.got` 在可寫段 |
| 節（section header） | 連結/工具單位，細分內容 | 日常主要盯 `.text`/`.rodata`/`.plt`/`.got` |
| `.symtab` | 你的函式名 | strip 就沒了，別指望它 |
| `.dynsym` | import/export 符號 | **strip 刪不掉**，是行為線索金礦 |
| PLT/GOT | 動態呼叫 libc 的間接跳板 | `call xxx@plt` = 在呼叫外部函式 xxx |
| entry vs main | entry=`_start`（CRT），main 要找 | 從 `_start` 的 `call __libc_start_main` 前一個 `lea ...,%rdi` 找 main |

## 踩雷集錦

1. **從 entry point 開始逆，以為那是主邏輯**。錯誤直覺：entry 是程式起點，逆向從那讀。正確做法：entry 是 `_start`（CRT 樣板），真正的 main 要靠 `__libc_start_main` 前的 `lea ...,%rdi` 找。從 entry 硬讀等於在讀啟動樣板。
2. **以為 strip 把所有符號都刪光了**。錯誤直覺：stripped binary 什麼名字都沒有。正確認知：strip 只刪 `.symtab`（你的函式名），`.dynsym`（import 的 libc 函式）照樣在——`nm -D` / `readelf --dyn-syms` 就看得到，是重要線索。
3. **搞混段和節、以為它們是同一層東西**。錯誤直覺：`.text` 是「段」。正確認知：`.text` 是**節**，它被歸進一個可執行的 **LOAD 段**。段是載入單位（機器用），節是內容細分（人和工具用）。strip 可以搞掉部分節資訊，段照載。
4. **看到 `call xxx@plt` 不知道那是 libc**。錯誤直覺：`@plt` 是某個本地函式。正確認知：`@plt` = 透過 PLT/GOT 呼叫**外部**函式（多半 libc）。那個名字來自 `.dynsym`，是可靠的行為線索。
5. **PIE binary 拿檔案偏移當執行期絕對位址**。錯誤直覺：`0x1199` 就是 main 執行時的位址。正確認知：PIE 執行時位址 = 隨機基底 + 偏移（ASLR）。靜態逆看偏移沒問題，但 gdb 動態下斷點要記得程式已加了基底（`0x555555554000` 之類）。
6. **`.bss` 大小當成檔案大小**。錯誤直覺：`.bss` 很大檔案就大。正確認知：`.bss` 是 `NOBITS`——檔案裡不佔空間，只記「載入時配這麼多清零記憶體」。逆向找未初始化全域看它。

## 進階：再往深一層

- **`readelf -d` 看動態區段（.dynamic）**：列出 `NEEDED`（依賴哪些 .so，如 `libc.so.6`）、`RUNPATH`、`SONAME`。逆向判斷「這 binary 依賴什麼、可能載入哪些庫」看這裡。
- **RELRO 與 GOT 保護**：`readelf -l` 裡的 `GNU_RELRO` 段代表 Full/Partial RELRO——決定 GOT 執行期還能不能被寫。這對 pwn（GOT hijack）與逆向判斷利用面都相關，接你的 [`binary_exploitation`](../binary_exploitation/README.md) 課。
- **`.init_array` / constructor**：`__libc_start_main` 在呼叫 main 前會跑 `.init_array` 裡的函式（`__attribute__((constructor))`）。**malware 常把惡意 code 藏在 constructor**，在 main 之前就跑了——只盯 main 會漏掉。逆 malware 要順手看 `.init_array`。
- **靜態連結的 binary 沒有 PLT/GOT 那套**：libc 直接編進去，沒有 INTERP、`call` 直接到本地位址。這讓它**大得多、也難逆得多**（分不清你的 code 和 libc 的 code），[Ch 22](./22-reversing-stripped-static-binaries.md) 專講。

## 本章重點整理

- ELF 有兩張地圖：**段（program header）是載入單位**（機器用，決定 R/W/X）、**節（section header）是內容細分**（人和工具用，逆向主要盯這個）。
- **三十秒體檢**：`readelf -h` 讀出架構、PIE 與否、entry point、strip 與否。
- **兩張符號表命運不同**：`.symtab`（你的函式名）strip 就沒；`.dynsym`（import 的 libc）strip 刪不掉，是 stripped binary 的行為線索金礦。
- **PLT/GOT**：`call xxx@plt` = 透過間接跳板呼叫外部函式（libc）。PLT 跳 GOT，GOT 執行期由動態連結器填真實位址。
- **entry ≠ main**：entry 是 CRT 的 `_start`。找 main 的手法：反組譯 `_start`，找 `call __libc_start_main` 前那個 `lea ...,%rdi`，它 load 的就是 main 位址（本例 `0x1199`，用 `nm` 驗證吻合）。

## 自我檢核

- [ ] 我能說出段（segment）和節（section）的差別，以及各給誰用
- [ ] 我能用 `readelf -h` 判斷一個 binary 的架構、是否 PIE、是否 stripped、entry 在哪
- [ ] 我能解釋為什麼 strip 刪掉了 `main` 卻刪不掉 `strcmp`（`.symtab` vs `.dynsym`）
- [ ] 我看到 `call strcmp@plt` 知道那是在呼叫 libc，並能大致說出 PLT→GOT 的間接跳轉
- [ ] 我能在一個 stripped PIE 裡，從 `_start` 找到真正的 main
- [ ] 我知道 `.rodata` 藏字串/jump table、`.got` 在可寫段、`.init_array` 可能藏 constructor 惡意碼

## 延伸閱讀

- **[`compilers/elf_linking`](../../compilers/elf_linking/README.md)**
  - **定位**：從連結器/載入器怎麼運作的角度講 ELF，本章的深度補充。想真懂 lazy binding、重定位、RELRO 的機制看這門。
  - **讀哪裡**：[Ch 2 section vs segment](../../compilers/elf_linking/02-section-vs-segment.md)、[Ch 3 符號與字串表](../../compilers/elf_linking/03-symbol-and-string-table.md)；再往後找 PLT/GOT、動態連結相關章，帶著「這在逆向留下什麼線索」的問題讀。
- **《Practical Binary Analysis》** — Dennis Andriesse（No Starch, 2019）
  - **定位**：Linux/ELF 逆向最佳入門，本章的主要對照書。
  - **讀哪裡**：Ch 2「The ELF Format」逐節對應本章（header/section/segment/符號/PLT-GOT）；Ch 1「Anatomy of a Binary」。
  - **前提**：會用 `readelf`/`objdump`，本章已帶你入門。
- **[ELF 規格 (System V ABI, x86-64 supplement)](https://gitlab.com/x86-psABIs/x86-64-ABI)**
  - **這是什麼**：ELF 與 x86-64 ABI 的權威原始文件。查某個欄位/重定位型別的精確語意時的最終依據。
  - **怎麼用**：當字典查——想確認 `R_X86_64_JUMP_SLO` 或某 program header 型別的定義時翻它，別整本讀。
- **`man readelf` / `man elf`**
  - **這是什麼**：`readelf` 各旗標與 ELF 結構的手冊。
  - **怎麼用**：`readelf -h/-l/-S/-r/-d/--dyn-syms` 這幾個本章用到的旗標，`man` 裡都有精確說明；`man 5 elf` 有 C struct 定義。

你現在能解剖一個 ELF、找到 main、看懂 import 與 PLT/GOT 呼叫。Part 0 的地基到此打完——你有了心智模型（Ch 1）、知道編譯器動了什麼手腳（Ch 2）、看得懂 binary 的骨架（Ch 3）。下一個 Part 進入靜態逆向的核心：從 `.text` 那堆 x86-64 指令裡，認出控制流、資料、函式——真正開始讀 asm。

→ [Ch 4 x86-64 asm：逆向者視角](./04-x86-64-for-reversers.md)
