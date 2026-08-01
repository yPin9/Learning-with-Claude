# Ch 32 — CFG (Control Flow Guard) 原理

> **目標**：徹底理解 CFG 的存在動機、編譯器插樁機制、PE 裡的 Guard CF Function Table 結構與 bitmap 查找演算法、執行期 `ntdll!LdrpValidateUserCallTarget` 的驗證流程；能辨別 CFG 保護了什麼、不保護什麼；能用 `dumpbin /loadconfig` 判斷一個目標的 CFG 狀態（標未實測）。

## 為什麼需要 CFG？

Ch 30 打通了 vtable 劫持的完整路徑：UAF 蓋掉 vptr，讓 `call [rax]`（或 `call [rax + offset]`）跳到你控制的地址。在 Linux userland，你熟悉的是：

- GOT 覆寫（x64 `FULL_RELRO` 後基本死了）
- 偽造 `__malloc_hook`/`__free_hook`（glibc 2.34 後直接廢掉）
- 覆蓋 C++ vptr → 跳向 ROP gadget 或 shellcode（仍是主流）

Windows 對應的情況是什麼？`/GS` 擋了 stack cookie 直接改 return address，但**間接呼叫（indirect call/jmp）**完全是空窗——攻擊者把 vptr 或函式指標改成任意地址，CPU 就乖乖跳過去。

直到 Windows 8.1 Update 3（2014）引入 **控制流保護（Control Flow Guard, CFG）**，這個空窗才被正面封堵。

CFG 的基本主張只有一句話：

> **每次 indirect call/jmp 執行前，先確認目標地址是一個「合法的呼叫目標」（即程式裡確實存在的函式入口）；不是就終止行程。**

這在 Linux 對應什麼？最接近的是 **clang CFI（Control Flow Integrity）**，原理一樣（indirect call target validation），但實現路徑不同：
- clang CFI 用虛擬表型別（vtable type hash）或跳板函式（jump table）做檢查，是 compile-time type-based
- CFG 用 bitmap（位元圖）記錄所有合法目標，是 address-based、作業系統與編譯器共同執行

不同在哪？CFG 的 bitmap 是**整個行程位址空間共用**的，由 OS loader 管理；clang CFI 的檢查是 per-callsite 型別匹配，粒度更細但需要全 LTO。兩者各有優缺，Ch 33 講繞過時這個差異很關鍵。

## 先建立直覺：CFG 是一張白名單 bitmap

在任何程式碼之前，先把 CFG 的核心機制畫成一張圖：

```
  編譯期（cl + link）                          執行期（ntdll）
  ────────────────────────────────────────────────────────────
  1. 掃描所有函式入口地址
  2. 把合法 indirect call 目標記進 Guard CF
     Function Table（PE Load Config 裡）
  3. 在每個 indirect call 前插入：
       call __guard_check_icall_fptr
       ↓                                   5. 查 CFG bitmap（位於低位址空間）
  4. 執行期呼叫                             6. 若目標地址 bit = 1 → 繼續執行
     ntdll!LdrpValidateUserCallTarget       7. 若 bit = 0 → 觸發 STATUS_STACK_BUFFER_OVERRUN
                                               → 行程終止（!）
  ────────────────────────────────────────────────────────────

  bitmap 佈局（每 bit 對應 8 bytes 對齊的地址區間）：

  位址空間 0x0000_0000_0000 ～ 0x7FFF_FFFF_FFFF（使用者空間，x64）
  ↓
  bitmap：每 16 bytes = 1 byte（8 bits × 2 = 16 bytes），或說每 bit = 8 bytes
  ↓
  target addr A → byte_index = A >> 9, bit_index = (A >> 3) & 7
  bit = bitmap[byte_index] >> bit_index & 1
  → 1：合法目標；0：非法，擋下
```

「每 bit 對應 8 bytes」是因為函式入口通常對齊到 8 bytes（或至少 4 bytes），精度不需要到每個位元組。完整推導放後面「底層機制」一節。

## CFG 的三個組成部件

CFG 不是單一機制，是**編譯器、連結器、OS loader、執行期檢查函式**四方合力：

| 組件 | 負責什麼 | 工具/位置 |
|---|---|---|
| `cl.exe /guard:cf` | 在每個 indirect call 前插入 `call [__guard_check_icall_fptr]` | MSVC 編譯器 |
| `link.exe /guard:cf` | 蒐集函式入口 → 寫進 PE Load Config 的 GuardCFFunctionTable | MSVC 連結器 |
| OS loader（`ntdll!LdrpCfgInitialize`） | 行程啟動時依 GuardCFFunctionTable 建立 bitmap，掛到行程的 CFG 頁面 | ntdll.dll |
| `ntdll!LdrpValidateUserCallTarget` | 每次 indirect call 執行前查 bitmap、決定放行或終止 | ntdll.dll（執行期熱路徑） |

編譯器只插「呼叫檢查函式」的樁；真正的 bitmap 在 ntdll 的執行期函式裡查。這個分工讓 OS 可以在不重編應用程式的情況下更新 CFG 策略。

## 編譯器插樁：`/guard:cf` 做了什麼

### 插入的機器碼

在 MSVC 編譯的 x64 程式裡，每個 indirect call（例如 `p->method()`、`(*fnptr)()`）原本長這樣：

```asm
; 沒有 CFG 的 indirect call
mov  rax, [rbp-8]   ; 讀取函式指標或 vptr
call qword ptr [rax]  ; 直接跳，攻擊者控制 rax = 任意地址
```

開了 `/guard:cf` 後，MSVC 在 `call` 前插入檢查：

```asm
; 有 CFG 的 indirect call（x64，MSVC 生成）
mov  rcx, qword ptr [rbp-8]            ; 目標地址放進 rcx（呼叫慣例：第一參數）
call qword ptr [__guard_check_icall_fptr]  ; 呼叫 CFG 檢查函式指標
                                           ; （ntdll!LdrpValidateUserCallTarget）
call rcx                                   ; 通過後才真正跳
```

> **未實測，理論預期**：上面的 MSVC 生成碼是依 Microsoft 公開文件與逆向研究重建的標準模式。用 MSVC 裝好後編一個有虛擬函式的 C++ 類別，用 `dumpbin /disasm` 或 WinDbg `u` 指令，找到 `call [rax]` 前面應該有 `lea rcx, [rax]` + `call [__guard_check_icall_fptr]` 的序列。

有兩個版本的檢查函式：

- `__guard_check_icall_fptr`：傳統版，檢查後返回（target 合法才返回），再由呼叫端 `call rcx`
- `__guard_dispatch_icall_fptr`：合一版（Windows 10 1709+），檢查通過直接 jmp 到 target，省一次 call/ret

`__guard_dispatch_icall_fptr` 是個全域函式指標（放在 `.data` 段），執行期被 ntdll 初始化為 `LdrpValidateUserCallTarget` 的位址。這個「間接了一層」是刻意的設計：OS 可以在不重編 binary 的情況下替換實作。

### 對 64 位元 indirect jmp 的處理

除了 indirect call，CFG 也保護 `jmp [rax]` 型的間接跳轉，插入的是：

```asm
; 間接跳轉版本
mov  rcx, target
call qword ptr [__guard_check_icall_fptr]
jmp  rcx
```

x86（32 位元）使用不同的寄存器慣例（`ecx`），機制相同。

## PE Load Config：Guard CF Function Table

CFG 的 bitmap 種子在 PE 的 **Load Configuration Directory**（Load Config）裡。`dumpbin /loadconfig` 就是在讀這裡。

PE 的 Load Config 結構（`IMAGE_LOAD_CONFIG_DIRECTORY64`）的 CFG 相關欄位：

```
  IMAGE_LOAD_CONFIG_DIRECTORY64（節選）
  ─────────────────────────────────────────────────────────────
  偏移  大小  欄位名
  0x58  8     GuardCFCheckFunctionPointer     ← __guard_check_icall_fptr 的 VA
  0x60  8     GuardCFDispatchFunctionPointer  ← __guard_dispatch_icall_fptr 的 VA
  0x68  8     GuardCFFunctionTable            ← 合法 indirect call 目標表 VA
  0x70  8     GuardCFFunctionCount            ← 表內有幾筆
  0x74  4     GuardFlags                      ← CFG 旗標遮罩（見下）
  ─────────────────────────────────────────────────────────────
  （偏移是相對 Load Config 結構起始，確切值視 Windows 版本微調，
   以你環境的 dt 輸出為準）
```

`GuardCFFunctionTable` 是一個 RVA 陣列，每筆 4 bytes（RVA），指向模組內的每個合法 indirect call 目標（函式入口）。連結器掃描所有目標檔（`.obj`），蒐集所有「可能被 indirect call/jmp 到的函式」，排序後塞進這個表。

### GuardFlags 位元定義

```
  GuardFlags 常見位元（來源：Microsoft SDK winnt.h）
  ─────────────────────────────────────────────────────────────
  0x00000100  IMAGE_GUARD_CF_INSTRUMENTED
              模組已插入 CFG 樁（/guard:cf 開了）

  0x00000200  IMAGE_GUARD_CFW_INSTRUMENTED
              包含 write barrier（XFG/RFG 用，Ch 34）

  0x00000400  IMAGE_GUARD_CF_FUNCTION_TABLE_PRESENT
              GuardCFFunctionTable 欄位有效

  0x00000800  IMAGE_GUARD_SECURITY_COOKIE_UNUSED
              /GS cookie 有初始化保護

  0x00001000  IMAGE_GUARD_PROTECT_DELAYLOAD_IAT
              延遲載入 IAT 保護（CFG 控制寫入）

  0x00002000  IMAGE_GUARD_DELAYLOAD_IAT_IN_ITS_OWN_SECTION
              延遲 IAT 獨立 section，允許最小保護

  0x00004000  IMAGE_GUARD_CF_EXPORT_SUPPRESSION_INFO_PRESENT
              有 suppressed export（被標記排除在 CFG 外的 export）

  0x00008000  IMAGE_GUARD_CF_ENABLE_EXPORT_SUPPRESSION
              真正啟用 suppressed export 機制

  0x00010000  IMAGE_GUARD_CF_LONGJUMP_TABLE_PRESENT
              包含 longjmp 目標表

  0x00020000  IMAGE_GUARD_RF_INSTRUMENTED
              Return Flow Guard（RFG）已插樁（實驗性，見 Ch 33）

  0x00F00000  IMAGE_GUARD_CF_FUNCTION_TABLE_SIZE_MASK
              每筆附加資料的大小（單位：4 bytes），預設 0（無附加）

  ─────────────────────────────────────────────────────────────
  一個「正常 CFG 模組」的 GuardFlags 通常是 0x00004500：
    0x0100（CF_INSTRUMENTED）| 0x0400（FUNCTION_TABLE_PRESENT）| 0x4000（EXPORT_SUPPRESSION_INFO）
```

> **未實測，理論預期**：用 MSVC 裝好後：
> ```bat
> dumpbin /loadconfig your_cfg_binary.exe
> ```
> 輸出應包含：
> ```
> Guard Flags              00004500
>     CF Instrumented
>     FID table present
>     Export Suppression Info Present
> Guard CF Function Table          0000000140003000  (30 functions)
> Guard CF Dispatch Function Pointer Address  0000000140001234
> Guard CF Check Function Pointer Address     0000000140001238
> ```
> `(30 functions)` 是連結器掃到的合法 indirect call 目標數量。

### `DllCharacteristics` 欄位的另一條線

除了 Load Config，PE Optional Header 的 `DllCharacteristics` 也有一個 CFG 旗標：

```
  DllCharacteristics 位元（節選）
  0x4000  IMAGE_DLLCHARACTERISTICS_GUARD_CF  ← 這個模組要求 CFG 保護
```

Ch 0 的 `objdump -p` 輸出裡，mingw 編的 binary 沒有這個位元（`0x0160`）；MSVC 加了 `/guard:cf` 後會設成 `0x4160`（或你的實際值）。

## 底層機制：bitmap 查找演算法

這裡是 CFG 最有趣的工程細節，也是後面分析繞過時必須搞懂的基礎。

### bitmap 放在哪裡？

OS 在每個行程的位址空間低端預留一塊特殊頁面，稱為 **CFG Bitmap**：
- x64：bitmap 從 `0x0`（或很接近 0 的位址）開始，覆蓋整個使用者空間（`0x000_00000000` ～ `0x7FF_FFFFFFFF`，約 128TB）
- 每個 bit 對應 8 bytes 的位址空間
- 整個 128TB 使用者空間需要 `128TB / 8 / 8 = 2TB` 的 bitmap，但用 sparse mapping（即 Windows AWE 機制），只有實際有映射的頁面才佔實際實體記憶體，沒用到的頁面為 0（all-zero 意味全部是非法目標）

bitmap 的核心映射（大部分研究者叫做「CFG bitflip table」）：

```
  合法 indirect call target 地址 → bitmap 中的 bit

  設 target = 0x140001000

  step 1：右移 3 bits（位址對齊到 8 bytes 粒度）
          index = target >> 3 = 0x28000200

  step 2：bitmap 的 byte offset = index >> 3 = 0x5000040
          bit   within byte    = index &  7  = 0

  查詢：
    bit_set = (bitmap[0x5000040] >> 0) & 1
    → 1：合法；0：非法

  簡化公式：
    byte_offset = target >> 9       (= target / 512)
    bit_index   = (target >> 3) & 7 (= (target / 8) mod 8)
```

為什麼是「右移 9 = 右移 3 + 右移 6」？因為一個 byte 代表 8 個 bit，每個 bit 對應 8 bytes，所以一個 byte 代表 64 bytes 的位址空間。`target >> 9` = 把 target 除以 512，得到對應的 bitmap byte。

ASCII 圖：

```
  位址空間（每格 8 bytes）
  ┌──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┐
  │  │  │  │  │  │  │  │  │  │  │  │  │  │  │  │  │  ...
  └──┴──┴──┴──┴──┴──┴──┴──┴──┴──┴──┴──┴──┴──┴──┴──┘
   0  8  10 18 20 28 30 38 40 48 50 58 60 68 70 78 ...  (hex)
   ↑                    ↑
   addr 0               addr 0x28（= 40d，第 5 個 8-byte 槽）
   bit 0 of byte 0      bit 5 of byte 0

  bitmap（每格 1 byte，代表 64 bytes 的位址空間）
  ┌────────────────────────────────────────────────┐
  │ byte[0] │ byte[1] │ byte[2] │ ... byte[N]      │
  │ bits 0-7│ bits 0-7│         │                  │
  └────────────────────────────────────────────────┘
    對應       對應       對應
    addr       addr       addr
    0x00-0x38  0x40-0x78  0x80-0xB8
```

### `LdrpValidateUserCallTarget`：執行期驗證

`ntdll!LdrpValidateUserCallTarget`（有時也叫 `LdrpValidateUserCallTargetBitMapCheck`，視 Windows 版本）的虛擬碼邏輯：

```c
// 概念性虛擬碼；真實實作是 inline asm 或編譯器高度最佳化的熱路徑
// 來源：逆向研究（Morten Schenk, j00ru, Windows Internals 研究社群）
void LdrpValidateUserCallTarget(void *target) {
    // CFG bitmap 的 base 位址，存在行程私有頁面（透過 ntdll 全域指標）
    ULONG_PTR bitmap_base = GetCFGBitmapBase();

    // 取 bitmap 中的位元
    ULONG_PTR index     = (ULONG_PTR)target >> 3;
    ULONG_PTR byte_off  = index >> 3;           // = target >> 6? 不，是 index/8
    ULONG     bit_index = index & 7;

    BYTE byte_val = ((BYTE*)bitmap_base)[byte_off];

    if (!((byte_val >> bit_index) & 1)) {
        // 目標位址不在合法集合中 → 直接引發例外
        RaiseFailFastException(STATUS_STACK_BUFFER_OVERRUN, ...);
    }
    // 合法：返回（呼叫端再 call target）
}
```

> 上述是高度簡化的示意。真實的 `LdrpValidateUserCallTarget` 有額外處理：
> 1. **Export-suppressed 函式**：被 `__declspec(guard(suppress))` 標記的函式即使在 bitmap 裡也受到額外限制（Ch 33 的繞過路線之一）
> 2. **延遲 bitmap 初始化**：模組延遲載入時，其合法 target 要等 loader 把它的 GuardCFFunctionTable 解析完才加進 bitmap
> 3. **target 的「中段對齊」特殊值**：bitmap 的 byte 不只有 0/1，有些 bit pattern 代表「此地址是函式的 prologue 部分，是特殊 entry」——這是 Windows 10 1703+ 加的細粒度資訊

### bitmap 如何建立？

行程啟動時，`ntdll!LdrpCfgInitialize` 讀每個已映射模組的 `GuardCFFunctionTable`，把所有合法 target 地址的 bit 設成 1。隨後每次 `LoadLibrary`（`LdrLoadDll`）把新模組的合法 target 加進去。

這意味著：**bitmap 是動態增長的**，但只能加（加新模組），不能刪（除非 `FreeLibrary` 後對應模組的 target 被清除）。

## CFG 的範圍限制：只保護 forward-edge

CFG 的設計明確是 **forward-edge CFI**（間接呼叫/跳轉的目標合法性），對以下東西完全不管：

```
  CFG 保護的：
  ✅ indirect call（call [rax]、call [rax+offset]）
  ✅ indirect jmp（jmp [rax]）
  ✅ vtable 呼叫（virtual function call）
  ✅ 透過函式指標的呼叫

  CFG 不保護的：
  ❌ return 指令（ret）→ ROP 仍然可行
  ❌ direct call/jmp（call 0x1234、jmp 0x5678）
  ❌ 資料流（data-only attacks）
  ❌ heap/stack 資料本身（只管控制流跳轉目標）
```

**return 不保護**是 CFG 設計的刻意選擇，因為：
1. return address 在 stack 上，保護它需要另一套機制（shadow stack）
2. CFG 的 bitmap 檢查對 return 沒意義——return 的目標不是「函式入口」，而是 call 之後的那條指令

這正是 **Intel CET (Control-flow Enforcement Technology)** 的 shadow stack 填補的空缺——shadow stack 專門保護 return address，CFG 保護 indirect call/jmp。在 Windows 11 上，CFG + CET shadow stack 聯手才能同時堵住 forward-edge 和 backward-edge。Ch 35 專章講 CET。

### 對照 Linux CFI

```
  Linux clang CFI（-fsanitize=cfi-icall）vs Windows CFG
  ─────────────────────────────────────────────────────────
  clang CFI：
  - type-based：每個 callsite 只允許「型別相容的函式」
  - 粒度細：不同型別簽名 → 不同合法集合
  - 需要 LTO（全程式型別資訊）
  - 純 userspace，不需 OS 支援
  - 不保護 kernel（kCFI 是另一套，Linux 5.19+ 主線）

  Windows CFG：
  - address-based：合法集合是「所有函式入口地址」的 bitmap
  - 粒度粗：任何 CFG 保護模組的函式入口都是合法 target
    （意味著可以把控制流劫持到「合法但危險」的函式，Ch 33 的核心繞過）
  - 不需 LTO
  - OS 與編譯器共同執行，bitmap 由 loader 管理
  - 同樣不保護 return
  ─────────────────────────────────────────────────────────
  結論：clang CFI 比 CFG 的類型安全保護更強（型別匹配），
       CFG 的 bitmap 方法更容易部署（不需 LTO，相容性更好）。
```

## 對比與取捨

| 面向 | CFG（Windows） | clang CFI（Linux） | 無 CFI |
|---|---|---|---|
| 保護範圍 | forward-edge indirect call/jmp | forward-edge indirect call（type-based） | 無 |
| 保護 return | ❌（要靠 CET） | ❌（要靠 ShadowCallStack） | ❌ |
| 精確度 | address-level（函式入口） | type-level（型別相容的函式） | — |
| 部署成本 | 需 MSVC /guard:cf 重編 + OS 支援 | 需 clang + LTO（全程式） | 無 |
| 繞過難度 | 中（bitmap 粒度粗，合法 target 多） | 高（型別匹配更嚴） | 易 |
| OS 強制 | ✅（loader 管理 bitmap，可保護未開 CFG 的模組呼叫） | 僅限有開 CFI 的模組 | — |
| 效能開銷 | 低（bitmap 查找約 5-10 ns） | 低～中（視 type-hash 複雜度） | 0 |

## 底層機制：行程啟動的 CFG 初始化流程

```
  CreateProcess → kernel 建立行程，映射 ntdll
       ↓
  ntdll!LdrpInitializeProcess
       ↓
  ntdll!LdrpCfgInitialize（或 LdrpValidateCFGNodes，版本依賴）
       ↓
  對每個已映射的 PE 模組（exe + ntdll + kernel32 + ...）：
    讀 Load Config → GuardCFFunctionTable + GuardCFFunctionCount
       ↓
  計算每個 RVA 的絕對 VA = module_base + RVA
       ↓
  呼叫 NtSetSystemInformation(SystemCFGBitmapInformation, ...) 或
  直接寫入行程的 CFG bitmap 頁面
       ↓
  bitmap 中對應 bit 設為 1
       ↓
  後續 LoadLibrary → 同樣流程加新模組的合法 target
       ↓
  行程執行中：每個 indirect call 前的 call [__guard_dispatch_icall_fptr]
  ↓ → LdrpValidateUserCallTarget → 查 bitmap → 放行或終止
```

> **未實測，理論預期**：在 WinDbg 裡可以用以下指令觀察 CFG 初始化：
> ```
> bp ntdll!LdrpCfgInitialize
> g
> k   ← 看 call stack，確認是從 LdrpInitializeProcess 呼叫來的
> ```
> 以及觀察 CFG bitmap 頁面（需要找到 ntdll 裡存 bitmap base 的全域變數，
> 各版本偏移不同，以 `dt ntdll!_CFG_CALL_TARGET_INFO` 或符號搜索為準）。

## 踩雷集錦

1. **「mingw 編的程式也有 CFG 吧，它有 `/GUARD:CF` 旗標」**：錯。mingw 編的 binary 即使手動加了旗標宣告，也**沒有插入 `__guard_check_icall_fptr` 樁**，因為 mingw 的編譯器前端不支援這個插樁。`DllCharacteristics` 的 `GUARD_CF` 位元是連結器設的，但沒有對應的插樁 → 等於沒有 CFG 保護。永遠用 MSVC 才能正確測試 CFG。

2. **「CFG 讓 vtable 劫持完全不可能了」**：不對。CFG 只讓「跳到任意位址」不可行，但跳到「bitmap 裡存在的合法目標」仍然允許。如果攻擊者能把目標地址設成一個在 bitmap 裡的函式，仍然能劫持控制流（只是只能跳到函式入口，而且那個函式必須在某個 CFG 模組的 GuardCFFunctionTable 裡）。Ch 33 專門拆這件事。

3. **「bitmap 的 bit 粒度是每個位元組」**：錯。粒度是每 8 bytes。地址只要 8-byte 對齊，且是函式入口，就能設 bit。未對齊到 8 bytes 的地址（如函式中段）正常情況下 bit = 0——這就是為什麼直接跳函式中段通常過不了 CFG。但有些邊界情況（Ch 33 講）。

4. **「CFG 也保護 ret，所以 ROP 不行了」**：錯。CFG 完全不管 return 指令。ret 從 stack 拿 return address 並跳轉，CFG 不插樁在 ret 前，你的 ROP 鏈在有 CFG 但無 CET 的系統上仍然有效。

5. **「關掉 CFG 只要 patch 掉 DllCharacteristics 的 GUARD_CF 位元」**：patch 了之後，loader 知道這個模組不要求 CFG 保護，但系統自身的 ntdll/kernel32 仍然開著 CFG——這只是讓你自己的 binary 不受 CFG 保護，攻擊 Windows 系統 DLL 的 vtable 仍然要過它們自己的 CFG。

## 進階：再往深一層

### Function Table Entry 的附加資料（metadata stride）

在 Windows 10 1703+，`GuardCFFunctionTable` 的每筆 entry 可以攜帶附加的 metadata（稱為「extra data stride」），由 `GuardFlags` 的 `IMAGE_GUARD_CF_FUNCTION_TABLE_SIZE_MASK` 位元組（0x00F00000）控制，每個單位是 4 bytes。附加資料可以存「這個函式是否是 export-suppressed」等資訊，讓 `LdrpValidateUserCallTarget` 做更細的判斷。

### Export Suppression

有些 Windows 系統函式雖然在 bitmap 裡（因為它們是函式入口），但被標記為 **suppressed export**，意思是它們**不允許被 CFG 驗證後的 indirect call 目標**。例如某些以前可以用來 pivot 的 ntdll 函式，在較新版本被加上這個標記。`GuardFlags` 的 `IMAGE_GUARD_CF_ENABLE_EXPORT_SUPPRESSION`（0x8000）控制是否啟用這個機制。

> 這是 Microsoft 對「合法但危險的 target」的防禦措施，直接針對 Ch 33 講的繞過路線（3）。

### 動態新增合法 target：`SetProcessValidCallTargets`

`kernelbase!SetProcessValidCallTargets`（Win10+）允許應用程式動態新增合法 indirect call target。JIT 編譯器用這個 API 把即時生成的機器碼頁面中的函式入口加進 CFG bitmap，讓 JIT code 也能被 CFG 保護。這是 Chrome/Edge/Firefox 在 Windows 上讓 JIT 和 CFG 共存的標準方法，在 browser_pwn 課的 JIT spray 對抗章節有提到。

### `RtlGuardRestoreContext` / `RtlGuardCheckLongJump`

`longjmp` 跳轉不是正常的 indirect call，CFG 對它有特殊處理：`GuardFlags` 的 `IMAGE_GUARD_CF_LONGJUMP_TABLE_PRESENT`（0x10000）搭配 `GuardLongJumpTargetTable`，讓 MSVC 把 `setjmp` 的目標也列成合法 longjmp target，驗證由 `ntdll!RtlGuardCheckLongJumpTarget` 做。Ch 33 的繞過路線（5）涉及這裡的歷史漏洞。

## 動手練習

（需要 MSVC 安裝完畢）

1. 建一個最小 C++ 程式，含一個虛擬函式（Animal/Dog 風格，抄 Ch 30 的範例即可）
2. 分別用 `/guard:cf`（開 CFG）和不帶任何 guard flag 編兩個版本
3. 用 `dumpbin /loadconfig` 比較兩個版本的 `GuardFlags` 與 `Guard CF Function Table` 有無出現
4. 用 `objdump -d`（或 WinDbg `u`）找到有 CFG 版本在虛擬呼叫前的 `call [__guard_check_icall_fptr]` 或 `call [__guard_dispatch_icall_fptr]` 插樁指令，截圖記錄

> **未實測**：步驟 3/4 需要 MSVC，以上是預期看到的。可以先用 winchecksec 快速確認 `CFG: true` 的欄位。

## 本章重點整理

- CFG 是 Windows 8.1 Update 3 引入的 **forward-edge CFI**：在每個 indirect call/jmp 前插入 bitmap 查找，確認目標是合法函式入口（在 GuardCFFunctionTable 裡的），不是就終止行程。
- 三個核心組件：**MSVC 插樁**（`/guard:cf`）、**PE Load Config 的 GuardCFFunctionTable**（合法目標白名單）、**ntdll 執行期驗證**（`LdrpValidateUserCallTarget`）。
- bitmap 公式：`byte_offset = target >> 9`，`bit_index = (target >> 3) & 7`；每個 bit 對應 8 bytes 位址空間。
- CFG **只保護 forward-edge（indirect call/jmp），不保護 return**（ret + ROP 不受 CFG 限制）——這是 CET shadow stack 要填的洞。

## 自我檢核

- [ ] 不看筆記，能解釋 CFG bitmap 查找的計算公式（target 地址 → byte_offset 和 bit_index）
- [ ] 能說出 `dumpbin /loadconfig` 輸出裡，判斷「CFG 是否真正開啟」要看哪兩個欄位（GuardFlags 的哪個位元 + Guard CF Function Table 的筆數）
- [ ] 面試被問「CFG 和 clang CFI 有什麼差」，能從 address-based vs type-based、粒度、部署成本三個角度回答
- [ ] 能解釋為什麼 ROP 在只有 CFG 但沒有 CET 的系統上仍然有效
- [ ] 能說出 `__guard_check_icall_fptr` 和 `__guard_dispatch_icall_fptr` 的差異
- [ ] 知道 `SetProcessValidCallTargets` 是做什麼的，為什麼 JIT 編譯器需要它

## 延伸閱讀

### 官方文件

- **[Control Flow Guard — Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/secbp/control-flow-guard)**
  - **讀哪裡**：全文不長，重點是「How does CFG work」與「Working with Components that Don't Have CFG」兩節，釐清 CFG 的系統整合方式
  - **和本章的關聯**：這是 Microsoft 官方對 CFG 設計的一手說明；本章的機制描述以此為基礎
  - **前提知識**：本章讀完即可

- **[`/guard` (Enable Control Flow Guard) — MSVC 編譯器選項](https://learn.microsoft.com/en-us/cpp/build/reference/guard-enable-control-flow-guard)**
  - **讀哪裡**：`/guard:cf`、`/guard:ehcont`（例外處理 continuation targets）的語法與行為
  - **和本章的關聯**：本章講的編譯器插樁旗標的權威說明；裝好 MSVC 後對照動手練習

### 研究報告

- **[Morten Schenk — "Taking Windows 10 Kernel Exploitation to the Next Level"（Improsec Blog）](https://improsec.com/tech-blog/bypassing-control-flow-guard-in-windows-10)**
  - **讀哪裡**：前半的 CFG 原理說明（bitmap 計算、GuardCFFunctionTable 結構）；後半是繞過，留到 Ch 33 再讀
  - **和本章的關聯**：最清楚的公開 CFG 原理整理之一，bitmap 公式驗證的最佳參照
  - **前提知識**：本章 + PE 結構基礎（Ch 3）

- **[Alex Ionescu — "Sheep Year Kernel Heap Fengshui"（REcon 2015）](https://github.com/ionescu007/Sheep-Year-Kernel-Heap-Fengshui)**
  - **讀哪裡**：CFG bitmap 建立與 NtSetSystemInformation 的使用說明段落
  - **和本章的關聯**：CFG bitmap 初始化流程的底層細節，補充本章「行程啟動 CFG 初始化」一節
  - **前提知識**：基本 Windows 核心機制（Ch 7 syscall 後讀效果最好）

### 部落格

- **[j00ru — "Windows 10 CFG Internals"（Vexillium Blog）](https://j00ru.vexillium.org/)**
  - **讀哪裡**：搜尋 j00ru 部落格的 CFG 相關文章；他的逆向分析比 Microsoft 官方文件更接近真實實作
  - **和本章的關聯**：`LdrpValidateUserCallTarget` 虛擬碼的主要逆向來源
  - **前提知識**：本章 + 基本 WinDbg 操作（能看反組譯輸出）

- **[Connor McGarr — "An Analysis of Exploit Mitigations on Windows 10"（2020）](https://connormcgarr.github.io/)**
  - **讀哪裡**：CFG 段落（約 1/3 篇幅），著重講 bitmap 結構與行程位址空間佈局
  - **和本章的關聯**：bitmap sparse mapping 說明的補充參照，視覺化的圖表幫助理解
  - **前提知識**：本章 + 虛擬記憶體基礎（Ch 9）

CFG 堵住了 vtable 劫持跳向任意地址的路，但「bitmap 裡的合法 target」仍然有數千個可用的函式入口——下一章就來系統整理攻擊者如何在這些限制下找出繞過路線。

→ [Ch 33 — CFG 繞過技法譜系](./33-cfg-bypass.md)
