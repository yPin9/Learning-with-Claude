# Ch 34 — XFG (eXtended Flow Guard)

> **目標**：搞懂 XFG 為什麼出現、它的 type-based hash 驗證機制如何大幅縮小合法 indirect call target 集合、目前的部署現狀、以及仍然存在的繞過面——帶著「比 CFG 強、比 CET 弱」的精確定位進入下一章。

---

如果你對 CFG 的 bitmap 查表機制、`_guard_check_icall_nop` 與 `_guard_dispatch_icall` 的差異還不熟，先回看理論基礎——本章預設你已掌握「CFG 是什麼、它的 bitmap 長什麼樣、攻擊者為什麼還能找到合法 target 繞過它」。Ch 33 討論的繞過家族 3（**找合法函式指標目標**）是本章的直接動機：CFG 的 bitmap 只驗「是不是有效函式入口」，不管函式型別，於是只要 target 是任何被 CFG 記錄的函式，就通得過去。XFG 就是為了把這個口堵掉而設計的。

## 為什麼需要這個？——CFG 的根本弱點

CFG 做了一件事：**在每個 indirect call 之前，確認目標位址是「登記在 bitmap 裡的合法函式入口」**。聽起來不錯，但這個「合法」的定義太寬鬆了。

考慮這段虛構但典型的 C++ 場景：

```cpp
// 真實被呼叫的型別
typedef int (*ProcessCallback)(const char* data, size_t len, int flags);

// 程式裡也存在另一個完全不相關的函式
BOOL WINAPI SomeExport(HANDLE h, LPVOID p, DWORD d);
```

CFG 的立場：只要 `SomeExport` 是合法函式入口，bitmap 就會標記它為「可用 indirect call target」。即使攻擊者把 `ProcessCallback` 函式指標竄改為指向 `SomeExport`，CFG 也不會擋——因為 `SomeExport` 在 bitmap 裡是合格的。

這就是 **Ch 33 繞過家族 3** 的核心：攻擊者不需要跳到任意位址，只要在合法函式裡找到一個能推進攻擊的目標（gadget-like export、`WinExec`、`ntdll!TpAllocWork` 之類），CFG 就毫無作用。

研究者 Morten Schenk、Connor McGarr 等人在 2017–2019 年多次演示，Windows 的數萬個 exported 函式裡，光是 ntdll、kernel32、KernelBase 就有幾千個合法 target 可供選擇——CFG 的「白名單」根本就是白名單加了等於沒加。

Microsoft 的回應分兩層：短期是讓 CFG 的 bitmap granularity 更細（`_guard_dispatch_icall` 版本改成 16-byte 對齊而非 8-byte），長期是設計一個全新機制：**XFG（eXtended Flow Guard）**。

## 先建立直覺

XFG 的核心想法非常乾淨，一句話說完：

> **每個 indirect call site 只能跳到型別相符的函式。**

CFG 問的是：「target 是有效函式入口嗎？」  
XFG 問的是：「target 是**和這個 call site 型別相符**的有效函式入口嗎？」

把它想成門禁系統：

```
CFG：
   call site ──►  [門衛]  「你有員工證嗎？」
                          任何員工進得去
                          攻擊者偽裝成任何員工就通過

XFG：
   call site ──►  [門衛]  「你是 R&D 部門的員工嗎？」
                  ┌────────────────────────────────────┐
                  │  call site 的 hash = 0xABCD1234    │
                  │  target 函式前 8 bytes = 0xABCD1234│
                  │  → 型別相符，放行                  │
                  └────────────────────────────────────┘
                          只有同型別函式進得去
                          攻擊者需要找到「型別相符且能推進攻擊」的目標
```

XFG 的 hash 是依函式原型（回傳型別＋每個參數的型別）計算出來的，存在**被呼叫函式的入口位址前 8 bytes**，call site 在跳轉前比對這個值——型別不符就掛掉（`RaiseFailFastException`）。

## 機制深挖：XFG 是怎麼運作的

### 編譯期：計算並嵌入 hash

MSVC 用 `/guard:xfg` 旗標啟用 XFG。啟用後，編譯器對每一個 indirect call site 計算一個 64-bit hash，代表這個 call site 期望的函式原型。Hash 的計算輸入是函式型別資訊（型別的正規化字串），以某種散列演算法得出 64-bit 的值。

> **未實測，理論預期**：具體的 hash 演算法 Microsoft 沒有完整公開文件，但 Connor McGarr、Javier Jimenez 等研究者透過逆向 MSVC 產生的物件檔與 ntdll 得出以下理解：hash 是對函式型別描述字串做一個確定性散列，相同型別永遠得到相同 hash，不同型別以很高機率得到不同 hash（非密碼學強度）。

同時，連結器在**每個被允許當作 indirect call target 的函式入口前 8 bytes** 嵌入這個 hash 值。這 8 bytes 不在函式的程式碼裡，而是在函式入口 `−8` 的位址，也就是說：

```
記憶體佈局（XFG 函式前）：

   ┌────────────────────────────────────────────────┐
   │  [func_addr - 8]  : XFG hash (8 bytes)         │
   │                     e.g. 0xA1B2C3D4E5F60718    │
   ├────────────────────────────────────────────────┤
   │  [func_addr]      : 函式真正的第一條指令        │
   │                     e.g. MOV RAX, RSP           │
   │  [func_addr + 1]  : ...                         │
   └────────────────────────────────────────────────┘
```

這個設計有點巧妙：函式入口本身不動，hash 藏在入口前面，既不影響正常 `call` 指令，又讓驗證邏輯知道去哪裡拿 hash。

### 執行期：call site 的驗證序列

當程式碼執行到一個受 XFG 保護的 indirect call，MSVC 插入的 prologue 大致如下（理論預期，基於逆向研究）：

```asm
; 假設 RAX = 函式指標（indirect call target）
; RCX 已放好 XFG call site hash（由編譯器在 call site 時靜態嵌入）

; XFG 驗證 stub（由編譯器插入，類似 CFG 的 _guard_dispatch_icall）
; 步驟 1：讀取目標函式入口前 8 bytes（也就是目標函式的 XFG hash）
MOV  R10, [RAX - 8]       ; 讀 target 的 type hash

; 步驟 2：比對 call site 期望的 hash 與 target 的 hash
CMP  R10, RCX             ; call site hash vs target hash

; 步驟 3：型別不符就進錯誤路徑（RaiseFailFastException）
JNZ  _xfg_check_fail

; 步驟 4：型別相符，繼續執行原來的 CFG 檢查（bitmap 查表）
; XFG 是疊加在 CFG 上的，不是取代
JMP  _guard_dispatch_icall_xfg_fptr  ; 接著呼叫 target
```

> **未實測，理論預期**：以上虛擬組語是基於多篇逆向研究的彙整（Connor McGarr 的「Exploit Development: Examining XFG」系列、Javier Jimenez 的 XFG 研究），實際 MSVC 編譯器生成的程式碼序列可能與此略有差異。裝好 MSVC + WinDbg 後，編一個帶 `/guard:xfg` 的程式，在 call site 前下中斷點，`u` 反組譯即可驗證。

關鍵點是：**XFG 不是取代 CFG，而是疊加在 CFG 上**。一個 indirect call 要通過 XFG，必須同時：
1. 目標在 CFG bitmap 裡（有效函式入口）
2. 目標函式入口前 8 bytes 的 hash 與 call site 的期望 hash 相符

### 整體資料流示意圖

```
編譯期（MSVC /guard:xfg）：
─────────────────────────────────────────────────────
   函式定義               Call site
   ┌──────────────┐       ┌──────────────────────────┐
   │ foo(int,int) │       │ void (*fp)(int,int) = ..;│
   │              │       │ fp(a, b);                │
   │ hash of      │       │                          │
   │ (int,int)->  │       │ 插入：                   │
   │ void         │       │   MOV RCX, <hash>        │
   │ = 0xDEAD... │       │   CALL _xfg_dispatch      │
   └──────────────┘       └──────────────────────────┘
          │                              │
          ▼                              ▼
   連結器在 foo 入口           編譯器嵌入 call site 期望 hash
   前 -8 bytes 寫入 0xDEAD...  （靜態常數，在 .text 裡）

執行期：
─────────────────────────────────────────────────────
   _xfg_dispatch:
      ┌─────────────────────────────────────────────┐
      │  1. R10 = *(target - 8)   // target 的 hash │
      │  2. if R10 != RCX         // call site hash  │
      │        → RaiseFailFastException              │
      │  3. CFG bitmap 查表（target 是合法入口？）   │
      │  4. 通過 → JMP target                       │
      └─────────────────────────────────────────────┘
```

## XFG vs CFG：target 集合縮小了多少？

這是 XFG 最核心的收益。以一個典型的 Windows 程式為例：

**CFG 下的合法 target 集合**：所有被 CFG 標記的函式入口（ntdll + kernel32 + KernelBase + 程式自身 export = 數千個函式）

**XFG 下的合法 target 集合**：所有 CFG 合法函式中，**且其 XFG hash 與 call site 的型別 hash 相符的**函式

假設一個 call site 期望 `void (*)(HANDLE, LPVOID, DWORD)` 這個型別，在整個 Windows 系統庫裡，hash 精確相符的函式可能只剩**個位數到數十個**，而不是原來的數千個。攻擊者的挑選空間從「任何 export」縮小到「型別匹配的那幾個 export」，這對 ROP-like 的 call-site gadget 利用衝擊巨大。

對照一下三個層次的控制流保護強度：

| 緩解 | 驗證內容 | 合法 target 集合大小 | 通過/擋住 |
|------|---------|---------------------|-----------|
| 無緩解 | 無 | 整個位址空間 | 任何位址 |
| CFG | 目標是已登記的函式入口 | 千 ~ 萬個 | 任何合法函式 |
| XFG | CFG ∩ 型別 hash 相符 | 個位數 ~ 數十個 | 只有同型別函式 |
| CET IBT | 目標有 `endbranch` 指令 | （見下章） | 帶標記的跳轉目標 |

XFG 明確定位在 CFG 之後、CET 之前的這個空間。它是純軟體、純編譯器機制，不需要硬體支援，而 CET 的 IBT（Indirect Branch Tracking）則是硬體輔助（見 Ch 35）。

## 部署現狀

### 支援的平台與工具鏈版本

XFG 從 Windows 10 版本 2004（Build 19041，May 2020 Update / 20H1）開始引入系統支援。但「系統支援」和「程式廣泛啟用」是兩回事。

需要的元件：

| 元件 | 需求 |
|------|------|
| OS | Windows 10 20H1（Build 19041）以上 |
| 編譯器 | MSVC（cl.exe），需要足夠新的版本（VS 2019 16.x 以上） |
| 連結器旗標 | `/guard:xfg`（需同時帶 `/guard:cf`） |
| 執行期函式庫 | ntdll 的 XFG dispatch stub（OS 提供） |

> **未實測，理論預期**：`/guard:xfg` 旗標在 VS 2019 引入，但具體可用的最小版本號請以 Microsoft 官方文件「/guard (Enable Control Flow Guard)」為準，我沒有在本機驗證過這個旗標的實際可用性。

### 目前的廣泛啟用程度——說實話

XFG 的問題不在技術設計，在**部署阻力**。兩個現實：

1. **需要重編**：現有二進位檔不會自動得到 XFG——每個 DLL 和 EXE 都要用 `/guard:xfg` 重新編譯。Windows 的系統元件有大量 DLL，把它們全部切換過去是長期工程。
2. **相容性問題**：XFG hash 儲存在函式入口前 8 bytes，這對某些需要把函式指標強轉（reinterpret cast）的程式碼有語義影響，容易造成型別不符的假陽性 crash。

截至 2026 年初，Microsoft 自家的一些系統元件（例如 Edge 的某些元件、部分 Windows Defender 元件）已啟用 XFG，但**大量的舊式 Win32 系統 DLL 仍然只有 CFG 而沒有 XFG**。在你打的目標上 XFG 到底開了沒，應該直接用 WinDbg `dumpbin /loadconfig` 或 `winchecksec` 確認。

這也意味著，即使你的目標程式啟用了 XFG，只要有任何一個它 `LoadLibrary` 的 DLL 沒有 XFG，攻擊者就可以找 non-XFG 模組裡的函式當 target——因為 XFG 的 hash 只在**啟用 XFG 的模組的函式入口前**存在，非 XFG 模組的函式入口前 8 bytes 是任意值或 0，hash 比對不會觸發。

### 對照 Linux：Clang 的 type-based CFI

Linux 這邊的類比是 **Clang `-fsanitize=cfi-icall`**（Control Flow Integrity，indirect call 版）。它的機制和 XFG 在設計意圖上幾乎一樣：每個 indirect call 只能跳到型別相符的函式。差異在實作細節：

- Clang CFI 用 **type metadata（llvm.type.metadata）**，在連結期建立型別資訊表
- XFG 用編譯器算好的 hash 嵌在函式入口前
- Clang CFI 支援 whole-program 最佳化下的 virtual call 保護（`-fsanitize=cfi-vcall`）
- MSVC XFG 主要針對 indirect call；virtual call 保護有 VTGuard（另一個機制）

從攻擊角度，兩者都面對同樣的殘存問題：型別 hash 碰撞。

## XFG 的殘存繞過面

XFG 沒有把所有洞堵死。以下是目前知道的攻擊面：

### 1. 同 hash 碰撞的 target（型別相符的惡意目標）

XFG 的 hash 是對函式型別計算的，而 Windows 系統庫裡有**非常多 C 函式有相同型別**。例如 `BOOL (WINAPI *)(HANDLE, LPVOID, DWORD)` 這個型別在系統 DLL 裡可能有幾十個相符的函式，攻擊者只要找到一個型別相符但行為可被利用（接受外部輸入寫記憶體、呼叫 shellcode、或能鏈接到下一個 primitive）的函式，就繞過了 XFG 的 hash 驗證。

這就是為什麼 XFG 不是終點——它縮小了 target 集合，但沒有讓它小到「只有一個正確函式」。C/C++ 的型別系統天然就有很多共享型別的函式。

### 2. 非 XFG 模組

如前所述，只要攻擊者能把函式指標指向沒有啟用 XFG 的模組（老式 DLL、第三方 DLL），XFG 的驗證就不適用於那個目標——因為那個函式入口前 8 bytes 不是 XFG hash，驗證邏輯根本不知道。

> **未實測，理論預期**：理論上 XFG 驗證器在 call 到非 XFG 函式時的具體行為（pass through、false positive crash、還是有其他處理），需要在有 `/guard:xfg` 的環境下實測。Microsoft 的設計文件沒有清楚說明這個邊界條件的處理。

### 3. hash 演算法的弱點

由於 hash 不是密碼學強度，如果攻擊者能控制哪個函式被放在哪個位址（例如透過 heap spray + 利用 DLL 可預測載入位址的場景），理論上可以製造 hash 碰撞。這屬於高難度攻擊，但原則上存在。

### 4. Data-only 攻擊（XFG 完全擋不住）

這是最根本的限制：XFG 和 CFG 一樣，只保護**控制流**（indirect call/jump 的目標）。如果攻擊者完全不劫持控制流，而是直接竄改資料（例如把一個 struct 裡的 length 欄位改大、把 privilege bit 改掉、把密碼 hash 改成已知值），XFG 對此毫無防護。這個攻擊路線被稱為 **data-only attack**，Ch 37 整章討論。

## 對比與取捨

| 維度 | CFG | XFG | Clang CFI |
|------|-----|-----|-----------|
| 驗證內容 | 目標是已登記函式入口 | 目標是型別 hash 相符的已登記函式 | 目標是型別相符的函式 |
| 需要硬體 | 否 | 否 | 否 |
| 需要重編 | 是（MSVC） | 是（MSVC /guard:xfg） | 是（Clang -fsanitize=cfi） |
| Linux 對應 | 無直接對應（有 grsec RAP） | Clang `-fsanitize=cfi-icall` | 本身就是 |
| target 集合 | 千 ~ 萬 | 個位數 ~ 數十 | 類似 XFG |
| 殘存攻擊面 | 任何合法函式 | 型別相符函式 + 非 XFG 模組 | 型別相符函式 + 非 CFI 模組 |
| 效能開銷 | 低（bitmap lookup） | 稍高（多一次記憶體讀取比對） | 低 ~ 中（視實作） |
| 部署廣度（2026） | 廣泛（Windows 8+ 預設） | 局部（Edge 等新組件） | 廣泛（Android、Chrome） |

## 踩雷集錦

1. **「XFG 取代了 CFG」**：錯。XFG 是**疊加在 CFG 上**的，兩個檢查都會做。`/guard:xfg` 要和 `/guard:cf` 一起用，不是替代關係。連結器產生的 Guard Flags 會同時標記兩者。

2. **「XFG hash 存在被保護函式的入口裡」**：錯。Hash 存在**入口位址前 8 bytes**（`func_addr - 8`），不是函式內部的第一條指令。函式本身的 prologue 不變，hash 夾在前一個資料（或 padding）和函式入口之間。這個位置選擇是設計上的，讓正常的 `call func_addr` 完全不受影響。

3. **「只要程式開了 XFG 就沒有 CFG 繞過問題」**：錯。非 XFG 模組的函式仍然是合法的 CFG target，不受 XFG hash 保護。攻擊者可以直接瞄準未啟用 XFG 的模組的函式。大量的老式 Win32 DLL 目前沒有 XFG。

4. **「XFG 的 hash 是 SHA 之類的密碼學 hash」**：錯。它是一個**確定性非密碼學散列**，相同型別永遠同 hash，但 hash 空間不大（64-bit），型別碰撞（不同型別卻算出相同 hash）理論上存在，只是機率很低。這不是設計缺陷，而是效能取捨：密碼學 hash 太慢，放在每個 indirect call 前不現實。

5. **「XFG 能防 data-only 攻擊」**：完全不能。XFG 和所有 CFI 一樣只管控制流。攻擊者如果選擇不碰函式指標、只竄改資料，CFG 和 XFG 都對此視而不見。

## 進階：再往深一層

### VTGuard（C++ 虛函式呼叫的 XFG 對應）

XFG 主要針對間接呼叫（C 函式指標、C++ 非虛函式指標）。對於 C++ 的 **virtual dispatch**（vtable 查表呼叫），MSVC 有另一個機制 **VTGuard**：在 vtable 裡嵌入型別資訊，呼叫前驗證物件的 vtable 指標是合法的同型別 vtable。VTGuard 和 XFG 在設計精神上一致，但針對的 call pattern 不同。攻擊 C++ 物件的 vtable（Ch 30）在 VTGuard + XFG 都啟用時難度大幅上升。

### 實測 XFG hash 的方法（需 MSVC 環境）

> **未實測，理論預期**：以下是裝好 MSVC 後可以做的驗證實驗。

```bat
REM 編一個帶 XFG 的 DLL
cl /c /guard:cf /guard:xfg /Zi target.c
link /DLL /guard:cf /guard:xfg /DEBUG target.obj

REM 用 dumpbin 看 Guard Flags
dumpbin /loadconfig target.dll

REM 預期看到 Guard Flags 裡有 XFG 相關旗標
REM 在 WinDbg 裡，反組譯 indirect call 前面幾條指令
REM 應該能看到讀 [RAX-8] 並比對的邏輯
```

### 面試被問到 XFG 怎麼答

被問「XFG 比 CFG 多了什麼」時，標準答案結構：

1. **CFG 的問題**：bitmap 只驗「是不是函式入口」，target 集合是所有已登記函式（千 ~ 萬個）
2. **XFG 的做法**：每個 call site 依函式原型算 64-bit hash，target 函式入口前 -8 bytes 存該 hash，call 前比對
3. **效果**：target 集合從數千縮小到型別相符的數十個
4. **限制**：非 XFG 模組不受保護、型別相符的惡意 target 仍存在、data-only 完全不擋

## 動手練習

> **環境需求**：需要 MSVC + WinDbg（本機目前未裝）。以下是準備好後的任務描述。

**任務**：觀察 XFG 的 hash 嵌入與 call site 驗證

1. 寫一個最小 C 程式，定義兩個型別**不同**的函式和兩個型別**相同**的函式，用一個函式指標呼叫其中一個
2. 用 MSVC 編譯（`/guard:cf /guard:xfg /Zi /O0`）
3. 用 WinDbg 或 dumpbin 確認：  
   a. `dumpbin /loadconfig` 看 Guard Flags 有 XFG 旗標  
   b. 在 WinDbg 裡，`u` 反組譯 indirect call 前後，找到讀 `[rax-8]` 的指令  
   c. 在函式入口前 `-8` 的位址設讀取中斷點（`ba r8 <addr>`），確認 hash 被讀到  
4. 嘗試（在除錯器裡手動）把函式指標改成指向型別**不同**的函式，確認 XFG 檢查失敗並觸發 `RaiseFailFastException`

這個實驗讓你親眼看到 hash 比對的時機與值，比看文件扎實得多。

## 本章重點整理

- CFG 只驗「是不是有效函式入口」，target 集合太大（數千個），攻擊者輕易找到合法但可利用的 target 繞過——這是 Ch 33 繞過家族 3 的根本原因
- XFG 在此基礎上加入 **type-based hash 驗證**：call site 期望 hash（依函式原型計算）必須與 target 函式入口前 `-8` bytes 的嵌入 hash 相符
- 效果是把合法 target 集合從數千縮小到**型別相符的數十個**，大幅提高攻擊者找可用 target 的難度
- 殘存弱點：**非 XFG 模組**（舊 DLL）仍是安全死角；型別相符的惡意 target 仍存在；**data-only 攻擊完全不受影響**（Ch 37 主題）

## 自我檢核

- [ ] 不看筆記能解釋：CFG bitmap 為什麼允許攻擊者找到繞過路徑？XFG 用什麼機制堵住這個洞？
- [ ] 能畫出 XFG 的記憶體佈局：hash 存在哪裡、call site 的驗證序列是什麼順序
- [ ] 被問「XFG 開了，程式是不是就安全了？」能說出至少兩個殘存攻擊面
- [ ] 能解釋 XFG 和 CFG 是疊加關係而不是替代關係
- [ ] 知道 XFG 和 Clang `-fsanitize=cfi-icall` 在設計意圖上的相似點與實作差異

## 延伸閱讀

### 研究部落格（首選）

- **Connor McGarr — 「Exploit Development: Examining XFG」系列**（Connor McGarr 個人部落格）
  - **讀哪裡**：這個系列是目前公開資料中對 XFG 機制逆向最深的；分多篇，從 MSVC 編譯器輸出到執行期驗證 stub 逐一拆解
  - **和本章的關聯**：本章理論預期的驗證序列就是以這個研究為基礎；有 MSVC 環境時拿這篇對著做一次
  - **前提知識**：CFG 機制（Ch 32/33）、x86-64 組語、WinDbg 基本操作

- **Javier Jimenez — 「Windows Exploitation Tricks: XFG」**（Exodus Intelligence）
  - **讀哪裡**：獨立分析 XFG hash 計算方式與繞過考量，和 McGarr 的角度互補
  - **和本章的關聯**：hash 計算細節那節的補充材料

### 官方文件

- **Microsoft Learn — `/guard (Enable Control Flow Guard)`**
  - **讀哪裡**：`/guard:xfg` 的 flag 說明、和 `/guard:cf` 的關係、Guard Flags 的值定義
  - **和本章的關聯**：確認 XFG 的旗標組合、系統版本需求
  - **URL 提示**：在 docs.microsoft.com 搜 "guard Enable Control Flow Guard compiler option"

### 學術/研究報告

- **「Control Flow Guard for Visual C++」— Microsoft Security Blog**（MSRC, 2014）
  - **讀哪裡**：雖然是 CFG 的原始設計文，但 XFG 的設計動機直接建立在這篇的缺陷上；對照著讀更能理解 XFG 為什麼這樣設計
  - **和本章的關聯**：「為什麼需要這個？」那節的 CFG 弱點分析直接對應這篇的設計選擇

### Linux 對應

- **Clang CFI 文件 — `https://clang.llvm.org/docs/ControlFlowIntegrity.html`**
  - **讀哪裡**：`-fsanitize=cfi-icall` 那節；Clang CFI 的設計思路和 XFG 高度類似，互相印證理解
  - **和本章的關聯**：本章「對照 clang」段落的延伸材料

---

XFG 是目前純軟體 CFI 方案裡 Windows 最強的一層，但它的部署程度有限，殘存的攻擊面（非 XFG 模組、型別碰撞）讓它不能單獨當成最後防線。下一章的 Intel CET 從**硬體**層面解決 CFI 做不到的那個問題：return address 竄改（ROP）。

→ [Ch 35 — Intel CET / shadow stack on Windows](./35-cet-shadow-stack.md)
