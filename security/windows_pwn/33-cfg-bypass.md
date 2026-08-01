# Ch 33 — CFG 繞過技法譜系

> **目標**：系統性掌握 CFG 的六個主要繞過家族（return/ROP、non-CFG 模組、合法但危險的 target、bitmap 竄改、歷史特殊繞過、函式中段），理解每個家族的前提、限制、後續緩解堵住了什麼；能在拿到任意寫原語後，走一遍「CFG 環境下的控制流劫持決策樹」；對照 browser_pwn 裡繞 clang CFI 的思路，看懂兩者的共通邏輯。

## 為什麼需要系統整理繞過？

Ch 32 講完 CFG 的機制，你可能有種感覺：bitmap 白名單 + 函式入口限制，攻擊者還能怎麼辦？

這個感覺在 2014 年 CFG 剛推出時，Microsoft 內部也有——他們相信 CFG 讓「vtable 劫持跳向 shellcode」成為歷史。但從 2015 到 2019 年，一系列研究接連拆穿了 CFG 的邊界：

- 2015：Morten Schenk 在 Black Hat USA 展示「非 CFG 模組」繞過
- 2016：j00ru 整理函式指標竄改 bitmap 本身的路線
- 2016–2017：多個研究者發現 `SetProcessValidCallTargets` 可被濫用
- 2018：XFG 開始開發，作為 CFG 的強化版（Ch 34）
- 2020+：CET shadow stack 部署，堵 return-side 的洞

每一個繞過家族出現，Microsoft 就推出新緩解；每個新緩解又帶來新繞過。理解這條演進線，不只是為了「打 CTF 能用」，更是為了讀懂現在的 Windows 10/11 攻防論文，知道「哪個技法在哪個 patch 之後就死了」。

## 先建立直覺：繞過 CFG 的核心策略

在學六個家族之前，先把所有繞過的**共同邏輯**提煉出來：

```
  CFG 的不變式：
    "每個 indirect call/jmp 的目標必須在 GuardCFFunctionTable 裡"

  繞過的本質，選其一：
  ┌─────────────────────────────────────────────────────────┐
  │ (A) 完全避開 CFG 保護的指令                               │
  │     → 用 ret 而不是 indirect call（ROP 家族）             │
  │     → 用沒有 CFG 插樁的程式碼（non-CFG 模組）             │
  │                                                         │
  │ (B) 在 CFG 允許的目標裡找到能被濫用的函式                   │
  │     → 合法 target 但能用來 pivot（合法危險 target 家族）    │
  │                                                         │
  │ (C) 竄改 CFG 的白名單本身                                  │
  │     → 改 bitmap，讓任意地址通過驗證（bitmap 竄改家族）       │
  │                                                         │
  │ (D) 利用 CFG 實作的邊界條件                                │
  │     → 函式中段、longjmp/setjmp、export suppression 的歷史  │
  │       漏洞（歷史繞過家族）                                  │
  └─────────────────────────────────────────────────────────┘
```

在 browser_pwn 的 clang CFI 繞過你學過：「跳到型別相容的 vtable」（因為型別 hash 碰撞）或「使用 JIT-generated code 的特殊路徑」——這是 (B) 的思路。CFG 的 (B) 更寬鬆，因為 CFG 的「合法目標」粒度只到函式入口，不做型別匹配。

## 家族一：Return 導向（ROP）——CFG 完全不管 ret

### 原理

CFG 插樁只覆蓋 `call [reg]` 和 `jmp [reg]`，**完全不插在 `ret` 前**。只要攻擊者能控制 stack（透過 stack overflow、UAF 覆蓋 stack 上的 saved RIP 等），ROP 鏈仍然可以自由執行。

```
  stack（攻擊者已控制）：
  ┌─────────────────────────────┐
  │  ...                        │
  │  [gadget 1 addr]  ← ret 跳這 │  ← CFG 看不到這個跳轉
  │  [gadget 2 addr]  ← pop rax  │
  │  [gadget 3 addr]  ← call rax │  ← 這個 indirect call 才被 CFG 管
  │  ...                        │
  └─────────────────────────────┘
```

ROP 鏈可以做任何事，包括呼叫 `VirtualProtect` 把某個頁面改成可執行，或直接呼叫系統 API。但要注意：ROP 鏈本身不受 CFG 限制，但 ROP 鏈最後如果需要做一個 indirect call（例如呼叫函式指標），那個 call 才進 CFG 的管轄範圍。

### 前提

- 能控制 stack 上的 return address（classic stack overflow、SEH overwrite 後 pivot 到 stack、或從 heap 上的 saved RBP/RIP 鏈）
- 知道 gadget 的地址（即繞了 ASLR，Ch 31 的 info leak 原語）

### 後續緩解（堵住的程度）

**Intel CET shadow stack**（Ch 35）：每次 call 時把 return address 備份到 shadow stack（ring-3 可讀、ring-0 才能改），ret 時比對 shadow stack 上的值與 stack 上的值，不一致 → 終止。Shadow stack 讓 ROP 從「改 return address」出發的路線基本堵死。

**堵住程度**：CET + CFG 聯手後，forward-edge（CFG）和 backward-edge（CET）都被管住，經典 ROP 的前提幾乎不成立——除非能同時 bypass CET（例如 Spectre 類的 shadow stack 洩漏，目前公開案例極少）。

### 對照 browser_pwn

在 V8 的 CFI 環境，JIT code 不在 shadow stack 的保護範圍，所以攻擊者先用 JIT spray 讓 JIT page 成為可控的指令序列，再跳進去。同樣的思路：找 CFI/CET 管不到的執行路徑。

## 家族二：非 CFG 模組——載入沒有 CFG 的 DLL

### 原理

CFG 的 bitmap 保護依賴**每個模組都被 `/guard:cf` 編譯並加進 GuardCFFunctionTable**。如果攻擊者能讓行程載入一個**未被 CFG 保護的 DLL**（舊版、第三方、或刻意不開 CFG 的 binary），那個 DLL 裡的任何地址都不在 CFG bitmap 裡，跳進去的 indirect call 會失敗——

**等等，直接跳 non-CFG DLL 的函式也不行嗎？**

實際情況更微妙：

```
  場景 A：non-CFG DLL 的函式入口
  └→ 這個地址不在任何模組的 GuardCFFunctionTable
  └→ bitmap 對應 bit = 0
  └→ 直接用 indirect call 跳進去 → CFG 擋下 ✗

  場景 B：在 non-CFG DLL 裡的 gadget（不是函式入口）
  └→ 同上，bit = 0 → 擋下 ✗

  場景 C：攻擊者在 non-CFG DLL 的地址範圍裡找 indirect call
  └→ non-CFG DLL 本身沒有插 CFG 樁
  └→ 它裡面的 indirect call 不經過 LdrpValidateUserCallTarget
  └→ 只要能讓 RIP 跳進 non-CFG DLL（先用 ROP？），
     之後在這個 DLL 裡面的任何 indirect call 都不受 CFG 保護 ✓
```

所以正確的理解是：**non-CFG DLL 不受 CFG 保護的保護**，不是「跳進去容易」，而是「一旦執行流在 non-CFG DLL 裡面，攻擊者可以自由做 indirect call 不被擋」。

### 實際利用路線

```
  1. 找目標行程載入的 non-CFG DLL（工具：winchecksec / Process Hacker 看模組清單）
  2. 在 non-CFG DLL 裡找到能被繞進去的路徑（e.g. non-CFG DLL 的某函式被
     CFG 保護的模組 callback，但 callback 是在 non-CFG DLL 裡觸發的）
  3. 或者：先用 ROP（家族一）跳入 non-CFG DLL 的一個「正好是函式入口」的地址
     （這個地址在另一個 CFG 模組的 Function Table 裡，所以能被 indirect call）
     → 在這個入口函式裡繼續跳轉，因為這個 DLL 沒有 CFG 插樁，後續不受管
```

### 真實案例

Morten Schenk 在 Black Hat USA 2015（"Exploiting CVE-2015-0313"）使用這個思路：漏洞在 Adobe Flash（non-CFG 版），Flash 的 indirect call 不受 Windows CFG 保護，讓 CFG 的防禦在整個攻擊鏈中形同虛設。

瀏覽器是最典型的受害場景：瀏覽器本體開了 CFG，但若載入了一個舊版未更新的第三方外掛（PDF reader、media plugin），整個沙盒的 CFG 保護就破洞了。

### 前提

- 目標行程有載入 non-CFG DLL（現代 Windows + 強制 CFG 的系統模組：ntdll、kernel32、user32 都已啟用 CFG；漏洞通常來自第三方或老舊 binary）
- 能控制行程載入哪個 DLL（`LoadLibrary` 路徑注入、DLL hijacking）或找到已有的 non-CFG DLL

### 後續緩解

**`ProcessMitigationPolicy` = `CFGStrictMode` / `MITIGATION_POLICY_FLAGS_CF_GUARD_STRICT_MODE`**（Win10 1703+）：在 strict mode 下，若呼叫目標地址不在任何 CFG 模組的 Function Table 裡，即使目標模組本身沒有開 CFG，呼叫也會被拒絕。這讓「利用 non-CFG DLL 本身的 indirect call」更難，但不影響家族一（ROP）。

**堵住程度**：嚴格 CFG 模式顯著提高了這個繞過路線的難度，但不是 100% 封死——攻擊者仍然可以嘗試找「non-CFG DLL 恰好也被 CFG 模組的 Function Table 參照到的地址」。

## 家族三：合法但危險的 Target——pivot 到 bitmap 裡的函式

### 核心思路

這是 CFG 繞過研究中最優雅也最有深度的家族。CFG bitmap 包含所有 CFG 模組的函式入口——在一個典型的 Windows 行程裡，光 ntdll、kernel32、user32、ole32 等系統 DLL 就有**數萬個函式入口**在 bitmap 裡。攻擊者的問題從「能跳到任意地址」變成「從數萬個合法函式裡找一個能被濫用的」。

```
  攻擊者目標：
    找一個函式 F，滿足：
    1. F 在 CFG bitmap 裡（合法 target）
    2. F 的行為可以被攻擊者的引數或 object 控制，達到任意行為
    3. F 可以作為跳板（pivot），讓執行流到達攻擊者想要的位置
```

### `LdrpDispatchUserCallTarget`（Windows 10 早期）

這個函式本身是 CFG 的執行器之一，但在某些版本的 Windows 10 早期，它的行為允許被用來 pivot：

```
  LdrpDispatchUserCallTarget（概念性）：
    輸入 rcx = 目標地址
    → 查 bitmap
    → 若合法：jmp rcx

  問題：這個函式**本身**在 bitmap 裡，
        攻擊者可以 indirect call LdrpDispatchUserCallTarget，
        傳入自訂的 rcx（任意地址），讓它再跳一次。

  但：LdrpDispatchUserCallTarget 查完 bitmap 後若目標非法仍然會終止
  → 這個繞過本身有限制，目標地址仍然要在 bitmap 裡
  → 但若攻擊者能同時做到「把新地址加進 bitmap」（家族四），就打通了
```

這個路線主要在研究早期 Windows 10 版本，現代版本的 `LdrpDispatchUserCallTarget` 已有額外保護。

### `WinExec`、`system`、`ShellExecuteEx` 等危險 API

更直接的思路：bitmap 裡有 `WinExec`（在 `kernel32.dll`），有 `system`（在 MSVCRT），有 `ShellExecuteA`。如果攻擊者能把間接呼叫的目標改成 `WinExec`，並且控制第一個引數（`rcx` = 指向 `"cmd.exe"` 的字串），就能直接 RCE 而不需要任何 ROP chain。

```
  傳統 vtable 劫持：
    vptr → fake vtable → gadget 或 shellcode   (CFG 擋下)

  CFG 下的 vtable 劫持：
    vptr → fake vtable → WinExec 地址   (WinExec 在 bitmap 裡 ✓)
    引數設計讓 rcx 指向 command string
    → WinExec 執行，不需 bypass NX/ASLR，只需 info leak 拿到 kernel32 base
```

這正是現代 Windows heap exploit 的主流模式：info leak（Ch 31）+ CFG-compatible target（`WinExec`/`VirtualAlloc`/`VirtualProtect`）+ 精確的 heap grooming（Ch 28）。

### COOP（Counterfeit Object-Oriented Programming）

更系統性的版本。COOP（由 Schuster et al. 在 IEEE S&P 2015 提出）的思路是：

```
  C++ 的虛擬呼叫是 CFG 的 forward-edge 保護範圍，
  但虛擬呼叫的目標只能是「類別的某個 virtual method」，
  而這些 method 全部都在 CFG bitmap 裡。

  攻擊者偽造一系列 C++ 物件（counterfeit objects），
  每個物件的 vptr 指向「真實但被濫用的」vtable，
  透過虛擬呼叫串起一系列操作（類似 ROP 但每一步都是一個「合法的 virtual call」）。
```

COOP 的要點：
1. 每一步都是 CFG 合法的 virtual call（方法在 bitmap 裡）
2. 「vfgadget」：精心挑選的虛擬方法，每個讀/寫特定記憶體或做特定算術
3. 串起來達到任意讀寫，再用任意寫呼叫 `WinExec` 等 API

COOP 在 browser_pwn 的 V8 物件濫用章節有類比（用 JS 物件組成的「假 WASM module」鏈），原理相同。現代緩解（XFG 的型別哈希，Ch 34）對 COOP 有更好的阻擋效果，因為 XFG 要求呼叫目標的函式簽名必須匹配。

### 前提與限制

- 需要知道目標函式的精確地址（要有 info leak，至少洩漏某個 DLL base）
- 需要控制呼叫時的引數寄存器（`rcx`/`rdx`/`r8`/`r9`），通常透過 heap grooming 讓物件的引數欄位指向可控記憶體
- COOP 的難度顯著高於傳統 ROP，需要在目標二進位裡找到合適的 vfgadget

### 後續緩解

**XFG（eXtended Flow Guard）**（Ch 34）：在 CFG 的函式入口驗證基礎上，加入函式型別哈希（prototype hash）。跳進 `WinExec` 的 indirect call，若 callsite 期望的型別是 `void (*)(struct Animal*)` 但 `WinExec` 的型別是 `UINT (LPCSTR, INT)`，哈希不匹配 → 擋下。XFG 讓「合法但型別不符」的 target 失效，是對家族三最直接的防禦。

## 家族四：竄改 Bitmap——任意寫 → 修改 CFG 白名單

### 原理

如果攻擊者已經有了**任意寫原語**（arbitrary write primitive），CFG bitmap 本身就成了攻擊面。bitmap 是行程虛擬位址空間裡一塊普通的記憶體頁面；如果攻擊者知道 bitmap base 位址，並且能寫入，就能把任意地址對應的 bit 設成 1，讓 CFG 把那個地址視為合法目標。

```
  目標：把地址 X（例如 shellcode 位址）加進 CFG 白名單

  步驟：
  1. 洩漏 CFG bitmap base 位址
     → 在 ntdll 裡有全域指標指向 bitmap（各版本偏移不同，需逆向或找公開研究）
  2. 計算 X 在 bitmap 裡的位元位置
     byte_offset = X >> 9
     bit_index   = (X >> 3) & 7
  3. 把 bitmap[byte_offset] |= (1 << bit_index)
     → 1 次 8-byte 對齊的寫入（或 bit-OR）
  4. 現在 indirect call 到 X 不再被 CFG 擋下 ✓

  攻擊者需要：
  ① 任意讀（找 bitmap base） + ② 任意寫（改 bitmap bit）
```

### 為什麼這是真正的威脅

這個路線的強大之處在於：它**不依賴**合法 target 的語義，完全繞過了家族三的限制（不需要找「剛好能當 pivot 的合法函式」）。你有任意讀寫，你就能把任何地址變成合法 CFG target。

### `SetProcessValidCallTargets` 的雙面刃

正規用途（JIT 編譯器）：把即時生成的函式入口加進 bitmap。

濫用角度：如果攻擊者能控制 `SetProcessValidCallTargets` 的引數（或有任意寫能直接呼叫 Windows Native API），就能把任意地址加進 bitmap 而不需要知道 bitmap 的實體位址。

```c
// SetProcessValidCallTargets 語法（來源：Microsoft SDK）
BOOL SetProcessValidCallTargets(
  HANDLE                hProcess,
  PVOID                 VirtualAddress,   // 要設定的記憶體區域
  SIZE_T                RegionSize,
  ULONG                 NumberOfOffsets,
  PCFG_CALL_TARGET_INFO OffsetInformation // 每筆含 offset + Flags（加/移除）
);

// 攻擊者的呼叫（概念，非真實 exploit）：
CFG_CALL_TARGET_INFO target = {
    .Offset = shellcode_offset,         // shellcode 相對區域起始的偏移
    .Flags  = CFG_CALL_TARGET_VALID     // 設成「合法」
};
SetProcessValidCallTargets(
    GetCurrentProcess(),
    shellcode_page_base,
    page_size,
    1,
    &target
);
// → shellcode 地址現在在 CFG bitmap 裡，indirect call 不再被擋
```

### 前提與限制

- **bitmap 竄改**：需要任意讀（洩漏 bitmap base）+ 任意寫（改對應 bit）。洩漏 bitmap base 在不同 Windows 版本有不同難度，因為 ntdll 裡儲存 bitmap base 的全域指標位置會隨版本和 ASLR 移動。
- **`SetProcessValidCallTargets` 濫用**：需要能呼叫這個 API，或能控制呼叫它的代碼路徑。在沙盒（如瀏覽器 render 行程）裡，這個 API 通常被封鎖（ACG，Ch 36）。

### 後續緩解

**ACG（Arbitrary Code Guard）**（Ch 36）：在啟用 ACG 的行程（如 Microsoft Edge 的 render 行程），`SetProcessValidCallTargets` 的 `CFG_CALL_TARGET_VALID` 旗標被限制只能在已有 CFG 保護的頁面使用，且 JIT code 必須在獨立的 JIT 行程（`MicrosoftEdgeCP.exe`）裡生成後用 IPC 傳過來，render 行程本身不能直接建立可執行記憶體。這讓「bitmap 竄改＋shellcode 執行」路線在 Edge render 行程裡不可行。

**bitmap 頁面的記憶體保護**：Windows 10 1903+ 在某些組態下把 CFG bitmap 頁面標成 `PAGE_NOACCESS`（除了 ntdll 的 CFG 驗證函式外不可讀寫），讓任意寫到 bitmap 更難操作。確切的保護強度視版本與行程設定而定。

## 家族五：歷史性邊界繞過

### `longjmp` / `setjmp` 的 target table 問題

在 Windows 10 早期（1511 之前），`setjmp` 的跳轉目標不在 CFG bitmap 的主流程驗證範圍內，或者 `GuardLongJumpTargetTable` 的處理有缺陷，導致攻擊者能用 `longjmp` 跳到任意地址而不被 CFG 攔截。

Microsoft 在 KB3093513 修補了這個問題，在 Image Load Config 加入了 `GuardLongJumpTargetTable` 欄位，並在 `ntdll!RtlGuardCheckLongJumpTarget` 加入對應的驗證。

### `RtlCaptureContext` / 結構化例外的利用

x64 的 table-based SEH（Ch 12）透過 `RUNTIME_FUNCTION` 表驗證 unwind 路徑——這是獨立於 CFG 的機制。攻擊者若能在 SEH 展開路徑上做手腳（例如偽造 `UNWIND_INFO` 指向可控地址），可能繞過 CFG（因為例外 unwind 不走 CFG 的 indirect call 路徑）。這類技術比 CFG 本身的繞過更依賴目標的例外處理結構，細節在 Ch 22（SEHOP）有部分覆蓋。

### Export Suppression 的早期實作缺陷

`IMAGE_GUARD_CF_ENABLE_EXPORT_SUPPRESSION`（0x8000）被引入前，某些「危險的但正常 export 的」ntdll 函式可以被直接當成合法 CFG target。Microsoft 在 Windows 10 1703 引入 export suppression 後，把一部分函式（例如某些 ntdll 內部 dispatch 函式）標成 suppressed，讓它們雖然在 bitmap 裡但不能被外部 indirect call 到。

### 跳到函式中段（mid-function entry）

CFG bitmap 的 bit 粒度是 8 bytes。若一個函式裡有「剛好 8-byte 對齊」的中段地址，在 bitmap 裡那個 bit 可能為 0（因為連結器沒把它當函式入口加進 Function Table）。一般情況下，跳函式中段會失敗。

但有些 edge case：

```
  1. 某些 thunk 函式本身就是一個 jmp [rel]，沒有 prologue，
     「入口」和「中段」在 bitmap 裡是同一個 bit → 合法

  2. 編譯器 ICF（Identical COMDAT Folding）把兩個相同函式合一，
     可能讓同一個地址有多個「函式入口」，bitmap 裡 bit = 1 → 合法

  3. 對齊 padding（int3 或 nop 填充）的某些位置恰好 8-byte 對齊，
     若 bit 被其他原因設成 1 則可能成為合法跳板（極罕見）
```

這個家族的可靠性最低，依賴目標 binary 的編譯輸出細節，通常用作其他繞過技法的補充，不作主要路線。

### `VirtualProtect` 歷史爭議

早期有研究提出：既然 `VirtualProtect` 在 kernel32 的 Function Table 裡（合法 target），攻擊者可以 indirect call 到它，把 shellcode 頁面改成 RWX，再跳進去。這是家族三的一個具體實例，在 ACG 引入之前是有效的。ACG 封鎖了「在 process 內部把 non-exec 頁面改成 exec」的能力，讓這個路線在 ACG 保護下失效。

## 繞過家族對照表

| 家族 | 前提 | 核心技法 | 後續緩解 | 現代可行性（Win11 最新 patch） |
|---|---|---|---|---|
| (1) Return/ROP | stack 可控 + gadget leak | 直接用 ret bypass CFG | CET shadow stack（Ch 35） | 低（需同時繞 CET） |
| (2) Non-CFG 模組 | 行程有未保護 DLL | 在 non-CFG 環境執行 indirect call | CFG strict mode | 中（仍需找 non-CFG DLL） |
| (3) 合法危險 target | info leak + 引數控制 | 呼叫 WinExec/COOP vfgadget | XFG 型別哈希（Ch 34） | 中（XFG 未全面部署） |
| (4) Bitmap 竄改 | 任意讀寫 | 直接設 bitmap bit | ACG + bitmap 頁面保護 | 低（ACG sandbox 下不可行） |
| (5a) longjmp | 老版 Windows | longjmp 跳任意地址 | GuardLongJumpTargetTable | 無（已修補） |
| (5b) mid-function | 特殊編譯輸出 | 跳 8-byte 對齊中段 | 粒度不變（難以系統利用） | 極低（不可靠） |
| (5c) export suppression | Win10 1703 前 | 呼叫 suppressed-but-in-bitmap 函式 | Export suppression 機制 | 無（已修補） |

## CFG 環境下的控制流劫持決策樹

```
  你拿到了任意寫原語。下一步？
  ──────────────────────────────────────────────────────────
  問題 1：目標行程有 CET shadow stack 嗎？
    → 是（Win11 + CET 硬體）：
        → ROP 路線基本阻斷，考慮其他家族
    → 否（或 CET 未啟用）：
        → 考慮 ROP（家族一）：stack 可控嗎？
           → 是：優先 ROP（最穩定）
           → 否：繼續往下

  問題 2：行程有 non-CFG DLL 嗎？
    → 是（winchecksec 驗過）：
        → 找機會讓執行流進入 non-CFG DLL 的 indirect call 路徑（家族二）
    → 否：繼續

  問題 3：你有任意讀（info leak）嗎？
    → 是：
        → 問題 3a：行程有 ACG 嗎？
             → 是（Edge / 受保護行程）：bitmap 竄改 + shellcode 不可行
             → 否：考慮 bitmap 竄改（家族四）
        → 也問：能找到 WinExec / 危險 API 的地址並控制引數嗎？
             → 是：家族三（合法危險 target）
    → 否：困難，需先找 info leak（Ch 31）

  問題 4：XFG 啟用了嗎？（目前非全面部署）
    → 是：家族三的型別不符 target 被擋，需找型別相符的危險 target（COOP）
    → 否：家族三自由度較高

  輸出：優先 ROP（若 CET 未啟）> 合法危險 target（若有 info leak）>
        bitmap 竄改（若無 ACG）> non-CFG DLL（若有）
```

## 對照 browser_pwn 的 CFI 繞過思路

你在 browser_pwn 裡繞 V8 / Chromium 的 CFI 時，用過的核心思路：

```
  browser_pwn → windows_pwn 對照
  ──────────────────────────────────────────────────────────
  1. "找型別混淆，讓 JS 引擎誤認物件型別，
     呼叫到型別相符但語義不符的 virtual method"
     → 對應 CFG 的 COOP（家族三）：
       找 vtable 裡型別對（xfg hash 相符）但語義可濫用的 virtual method

  2. "JIT spray 讓 JIT page 成為合法程式碼"
     → 對應 CFG 的 SetProcessValidCallTargets 濫用（家族四）：
       讓 bitmap 認為你的 payload 是合法 target

  3. "用 SharedArrayBuffer + SpiderMonkey/V8 bug 洩漏物件地址"
     → 對應 CFG 的 info leak（Ch 31）：
       洩漏 kernel32 base 才能找 WinExec 位址（家族三）

  4. "找沙盒邊界：renderer 呼叫 browser process 的 IPC 介面"
     → 對應 CFG + ACG 下的跨行程路線（不在 windows_pwn 範圍，
       往 windows_kernel_driver 走）
  ──────────────────────────────────────────────────────────
  共通邏輯：任何 CFI 的繞過，不是找「CFI 管不到的執行路徑」
  就是找「CFI 裡合法但語義可濫用的目標」。CFG 的 bitmap 比
  clang CFI 的型別哈希粒度粗，所以家族三在 CFG 下比在 clang CFI
  下容易——XFG（Ch 34）嘗試縮小這個差距。
```

## 踩雷集錦

1. **「non-CFG DLL 的函式我可以直接 indirect call 跳進去」**：錯。跳進 non-CFG DLL 的函式本身也要過 CFG 驗證，而那個地址的 bit = 0（不在任何模組的 Function Table 裡）→ 被擋。non-CFG 的優勢是「在 non-CFG DLL 內部的 indirect call 不受保護」，不是「外部跳進去不受保護」。

2. **「ROP 在開了 CFG 的 Win11 上完全無效」**：不對，要分兩個問題：CFG 管 indirect call，ROP 管 ret，兩者是獨立的。沒有 CET 的系統（Win10 老機器、或 CET 未在 UEFI 啟用），ROP 照跑。Win11 + CET 硬體才是真正的雙重保護。

3. **「bitmap 是 read-only，任意寫攻不了」**：在沒有 ACG 的行程裡，bitmap 頁面的保護在多數版本並非嚴格 read-only（`PAGE_READWRITE` 是常見設定）。ACG 才是真正的防線；不要把 bitmap 保護與 CFG 本身的插樁混為一談。

4. **「COOP 只是理論，沒有實際 exploit 用過」**：COOP 2015 年就已有完整 PoC（Schuster 等人在 IEEE S&P 2015 發表時附有演示）；後來多個實際 CVE 的 exploit 也用了類似思路（特別是針對 IE 的 exploit，因為 IE 的 C++ 物件模型有豐富的 vfgadget 資源）。

5. **「家族三的目標是 `WinExec`，所以只要擋住 WinExec 就沒事了」**：擋 WinExec 沒用。bitmap 裡的危險目標不只有 WinExec，`CreateProcess`、`ShellExecuteEx`、`VirtualAlloc`（+ 寫 shellcode 到新頁面 + `CreateThread` 執行）都可以被串起來。真正的防禦是型別匹配（XFG）而不是 blocklist。

## 進階：再往深一層

### Return Flow Guard（RFG）的短暫生涯

2016 年，Microsoft 在 Windows 10 insider build 引入了 **Return Flow Guard（RFG）**的原型，試圖用軟體方式保護 return address（類似 shadow stack 的前身）。RFG 在每個函式入口把 return address 備份到 `gs:[rsp]`（Thread Control Stack），return 時比對。

但 RFG 從未正式 release（一直是 insider/experimental 狀態），最終被 CET hardware shadow stack 取代。`GuardFlags` 裡的 `IMAGE_GUARD_RF_INSTRUMENTED`（0x20000）是 RFG 的旗標，在現代系統上通常不啟用。

研究 RFG 的價值在於理解「為什麼 software shadow stack 難以做對（效能、相容性問題）」，以及為什麼 Intel 最終在硬體層解決這個問題（CET）。

### Process Mitigation Policy 的 CFG 相關選項

`SetProcessMitigationPolicy(ProcessControlFlowGuardPolicy, ...)` 有三個選項：

```
  PROCESS_MITIGATION_CONTROL_FLOW_GUARD_POLICY：
  ┌────────────────────────────────────────────────────────┐
  │ EnableControlFlowGuard : 1    → 啟用 CFG               │
  │ EnableExportSuppression : 1   → 啟用 export suppression │
  │ StrictMode : 1                → strict mode             │
  │                               （non-CFG 模組也受保護）   │
  └────────────────────────────────────────────────────────┘
```

`StrictMode` 是對家族二最直接的防禦。但它的代價是相容性問題：有些老 DLL 沒有 CFG 支援，strict mode 下載入它們的行程會在第一個 indirect call 時被殺死。這就是為什麼 strict mode 不是預設值。

### 針對 XFG（Ch 34）的預告

XFG 在 CFG 的 bitmap 之上加入了每個 callsite 的「函式原型哈希（prototype hash）」驗證。攻擊者不只要找「地址在 bitmap 裡的函式」，還要找「型別哈希匹配 callsite 期望的函式」。這讓家族三的成本大幅提高——不再能隨便找一個 `WinExec` 就好，要找「哈希碰撞」或「型別相符的危險函式」。

XFG 的哈希計算方式、碰撞攻擊思路、以及 XFG 目前的部署狀況留到 Ch 34 完整展開。

### 研究者索引

- **Morten Schenk（Improsec）**：CFG 繞過的系統整理，Black Hat USA 2015/2016 演講；家族一、二、三的主要公開整理者
- **j00ru（Mateusz Jurczyk）**：[Vexillium Blog](https://j00ru.vexillium.org/)；CFG bitmap internals 逆向、export suppression 機制分析
- **Alex Ionescu**：CFG 初始化流程（`LdrpCfgInitialize`）的公開逆向；REcon 2015 的 bitmap 建立機制說明
- **Schuster, Tendyck, Liebchen, Davi, Sadeghi, Holz**：COOP 論文作者（IEEE S&P 2015）；家族三的系統化學術版本
- **Connor McGarr**：[connormcgarr.github.io](https://connormcgarr.github.io/)；現代 Windows 緩解分析，CFG bitmap 與 ACG 互動的清晰說明

## 動手練習

（部分步驟需要 MSVC）

1. **找你環境裡的 non-CFG 模組**：用 `winchecksec`（`winchecksec notepad.exe` 或你有的任意 Windows 程式），列出 CFG 狀態。找三個「沒開 CFG」的 DLL（第三方或老版本的最多）。記錄它們的 Path 和 CFG 狀態。

2. **（理論推算）bitmap 位元計算**：給定一個地址（例如 `notepad.exe` 在你系統的 base + 0x1000），手動計算它在 CFG bitmap 中的 byte_offset 和 bit_index。不需要實際讀 bitmap，只是熟悉公式。

   參考：`byte_offset = addr >> 9`，`bit_index = (addr >> 3) & 7`

3. **（MSVC 裝好後）GuardCFFunctionTable 分析**：

   > **未實測，理論預期**：以下步驟需要 MSVC 安裝完畢並有 dumpbin。
   - 用 MSVC 編一個含多個函式的 C++ 程式（開 `/guard:cf`）
   - `dumpbin /loadconfig` 看 Guard CF Function Table 的 RVA 列表
   - 手動選兩個 RVA，加上 module base，確認它們在 bitmap 裡（理論上 bit = 1）
   - 手動選一個「函式中段地址」（入口 + 1），確認這個地址在 bitmap 裡應該 bit = 0

## 本章重點整理

- CFG 的六個繞過家族：**ROP（ret 不受 CFG 管）、non-CFG 模組（未插樁環境裡的 indirect call 自由）、合法危險 target（bitmap 裡的危險 API/COOP）、bitmap 竄改（任意寫改白名單）、歷史邊界繞過（longjmp/mid-function）**。
- 現代 Windows 11 最有效的繞過路線：**合法危險 target + info leak**（XFG 未全面部署前）；ROP 路線需同時繞 CET。
- 後續緩解的對應關係：CET 堵家族一（ROP）、XFG 堵家族三（合法危險 target）、ACG 堵家族四（bitmap 竄改 + shellcode）——沒有一個緩解能全部堵死，需要疊加。
- 和 browser_pwn 的共通邏輯：任何 CFI 繞過的本質都是「找管不到的路徑」或「找語義可濫用的合法目標」，CFG vs clang CFI 的差異只在粒度（address vs type）。

## 自我檢核

- [ ] 不看筆記，能列出 CFG 繞過的六個家族，以及每個家族的**最關鍵前提**（不是全部細節，只是「要能這麼做，首先需要什麼」）
- [ ] 面試被問「你有任意讀寫但行程開了 CFG，你怎麼打」，能走一遍決策樹，說出優先考慮哪個家族、為什麼
- [ ] 能解釋 COOP 和普通 vtable 劫持的差異，以及 XFG 如何讓 COOP 變難
- [ ] 能說出「non-CFG DLL 可以 indirect call 到它的函式入口嗎」並解釋為什麼不行（家族二的常見誤解）
- [ ] 能解釋 `SetProcessValidCallTargets` 的正常用途，以及在沒有 ACG 的行程裡如何被濫用
- [ ] 知道 RFG 是什麼、為什麼沒有 release、最終被什麼取代

## 延伸閱讀

### 研究報告 / 演講

- **[Morten Schenk — "Bypassing Control Flow Guard in Windows 10"（Improsec Blog）](https://improsec.com/tech-blog/bypassing-control-flow-guard-in-windows-10)**
  - **讀哪裡**：家族一、二、三的具體 PoC 說明；「non-CFG DLL」段落和「merging valid and invalid call targets」段落
  - **和本章的關聯**：本章家族一～三的主要一次文獻；先讀本章再讀這篇能驗證理解
  - **前提知識**：本章 + Ch 32 + 基本 PE 結構（Ch 3）

- **[Schuster et al. — "Counterfeit Object-oriented Programming: On the Difficulty of Preventing Code Reuse Attacks in C++ Applications"（IEEE S&P 2015）](https://www.ieee-security.org/TC/SP2015/papers-archived/6949a745.pdf)**
  - **讀哪裡**：Section 3（COOP 的形式化定義）和 Section 4（vfgadget 分類）；附錄的 PoC 說明
  - **和本章的關聯**：家族三（COOP 部分）的學術根據；理解 COOP 的系統性而不只是直覺
  - **前提知識**：C++ vtable 機制（Ch 30）+ 本章家族三的說明

### 部落格

- **[j00ru — Windows CFG 逆向分析（Vexillium Blog）](https://j00ru.vexillium.org/)**
  - **讀哪裡**：搜尋 j00ru 的 CFG 相關文章；重點是 export suppression 的逆向分析和 bitmap base 洩漏的觀察
  - **和本章的關聯**：家族五（export suppression）和家族四（bitmap 定位）的逆向細節
  - **前提知識**：WinDbg 基本操作 + ntdll 逆向的心理準備

- **[Connor McGarr — Windows Process Mitigation Policy and CFG Interaction](https://connormcgarr.github.io/)**
  - **讀哪裡**：CFG + ACG 互動、`StrictMode` 的行為、bitmap 頁面保護的組合說明
  - **和本章的關聯**：「後續緩解」段落的補充，特別是 ACG 如何讓家族四的 bitmap 竄改在 Edge render 行程失效
  - **前提知識**：本章 + Ch 36（ACG，可先讀概念部分）

### 官方文件

- **[`SetProcessMitigationPolicy` — Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-setprocessmitigationpolicy)**
  - **讀哪裡**：`ProcessControlFlowGuardPolicy` 結構的三個欄位定義（`EnableControlFlowGuard`、`EnableExportSuppression`、`StrictMode`）
  - **和本章的關聯**：「進階」一節的 Process Mitigation Policy 說明的官方來源；理解每個 flag 對應擋哪個繞過家族
  - **前提知識**：本章讀完即可

CFG 的 bitmap 粒度（函式入口 address-level）是它最大的弱點——XFG 試圖用函式型別哈希把這個粒度縮小到 type-level，下一章完整拆解 XFG 的機制與它自身的限制。

→ [Ch 34 — XFG (eXtended Flow Guard)](./34-xfg.md)
