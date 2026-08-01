# Ch 35 — Intel CET / shadow stack on Windows

> **目標**：深挖 Intel CET（Control-flow Enforcement Technology）的兩大硬體機制——shadow stack 如何在硬體層殺死 ROP、IBT（Indirect Branch Tracking）如何用 `endbranch` 指令限縮間接跳轉目標——以及 Windows 的 Hardware-enforced Stack Protection 如何把這套機制接進 OS；理解 CET + CFG/XFG 組合後「傳統控制流劫持」的攻擊面幾乎被堵死，攻擊者被迫轉向 data-only；誠實面對 CET 的限制與殘存攻擊面。

> **環境（特別說明）**：本章多處標「未實測，需 CET 硬體」。Intel CET shadow stack 需要第 11 代 Intel Core（Tiger Lake，2020）或 AMD Zen 3（2020）以上的 CPU，以及 Windows 10 版本 2004（20H1）以上的 OS。CET IBT 則需要第 12 代 Intel Alder Lake 以上。本機硬體規格需自行確認（`wmic cpu get caption` 或 `cpuid`）。即使硬體具備，`/CETCOMPAT` 旗標也需要 MSVC linker（本機尚未安裝），無法實測。所有涉及 CET 執行期行為的描述均為**理論預期**，基於 Intel 架構手冊（SDM, Vol. 1 Ch 17）、Windows CET 官方部落格以及 Connor McGarr 的研究。

---

如果你從 Ch 33/34 一路讀來，你現在知道：CFG 和 XFG 保護的是 **indirect call/jump 的目標**，讓攻擊者無法隨意把函式指標指到任意位址。但傳統 ROP 攻擊靠的不是函式指標——它靠的是**竄改 stack 上的 return address**。CFG/XFG 對 `ret` 指令後面要去哪裡一點辦法都沒有。

ROP（Return-Oriented Programming）之所以能繞過 DEP/NX，就是因為攻擊者把 stack 上的一堆 return address 改掉，讓 `ret` 指令一個接一個地把執行流串起來。不管 XFG 的 hash 多精確，它保護的是 `call` 指令，不是 `ret` 指令。

Intel CET 的第一個武器，**shadow stack**，就是直接從硬體層攔截這個問題。

## 為什麼需要這個？——ROP 的根本前提

讓我們精確描述一下 ROP 的成立條件：

1. 攻擊者能寫入一段連續的 stack 記憶體（overflow、UAF、或任意寫原語）
2. 被寫入的區域包含函式 prologue 壓入的 return address（`call` 指令壓的值）
3. 當函式執行 `ret` 時，CPU 從 RSP 讀取 return address，跳過去
4. 攻擊者把這個 return address 改成一個 gadget 的位址，那個 gadget 最後也有 `ret`
5. gadget 的 `ret` 再讀下一個竄改的 return address……

整條 ROP chain 的成立，依賴一個假設：**CPU 只有一個 stack，return address 和攻擊者寫入的資料共用同一個 stack**。

如果有第二個 stack，專門只給 `ret` 指令查 return address，而且攻擊者**碰不到**這個第二 stack，ROP 就徹底失效。

這就是 shadow stack 的設計動機。

## 先建立直覺——兩個平行的 stack

Shadow stack 最容易用圖來理解：

```
正常執行（沒有 CET）：
─────────────────────────────────────────────────────

   CALL target：
   ─────────────────────────────────────────────────
   RSP 減 8，把 return address 壓入 stack

   ┌─────────────────────────────────────────────┐
   │  一般 Stack (RSP 管理)                       │
   │  ─────────────────────────────────────────  │
   │  [ return address = 0x7ff800001234 ]  ← RSP │   ← 攻擊者可以改這裡！
   │  [ 函式局部變數 a ]                          │
   │  [ 函式局部變數 b ]                          │
   │  [ caller 的 locals ... ]                   │
   └─────────────────────────────────────────────┘

   RET：
   從 RSP 讀 return address，跳過去 → 攻擊者竄改後跳到 gadget


CET shadow stack 開啟後：
─────────────────────────────────────────────────────

   CALL target：
   ─────────────────────────────────────────────────
   除了壓 RSP，CPU 同時把 return address 寫入 shadow stack

   ┌─────────────────────────────────────────────┐     ┌───────────────────────────────────────────┐
   │  一般 Stack (RSP 管理)                       │     │  Shadow Stack (SSP 管理，硬體維護)          │
   │  ─────────────────────────────────────────  │     │  ─────────────────────────────────────── │
   │  [ return address = 0x7ff800001234 ]  ← RSP │     │  [ 0x7ff800001234 ]                ← SSP │
   │  [ 函式局部變數 a ]                          │     │  (只讀；應用程式無法直接寫入)               │
   │  [ 函式局部變數 b ]                          │     │                                           │
   │  [ caller 的 locals ... ]                   │     │                                           │
   └─────────────────────────────────────────────┘     └───────────────────────────────────────────┘
            │                                                        │
            ▼  攻擊者改掉 RSP 上的 return address                    │
   [ return address = 0xdeadbeef ]  ← RSP（被竄改）           [ 0x7ff800001234 ] ← SSP（未被碰）

   RET 指令執行：
   ┌─────────────────────────────────────────────┐
   │ 1. 從 RSP 讀 return address = 0xdeadbeef    │
   │ 2. 從 SSP 讀 shadow return = 0x7ff800001234 │
   │ 3. 比對：不符！                             │
   │ 4. 觸發 #CP（Control Protection Exception） │
   │    → OS 例外處理 → 行程終止                  │
   └─────────────────────────────────────────────┘
   ROP chain 在第一個 ret 就被硬體斬斷
```

攻擊者改了 RSP 上的 return address，但 shadow stack 裡的副本沒有被動到（shadow stack 受硬體保護，一般 `mov [mem], val` 或 stack overflow 無法寫入），兩邊對不起來，CPU 直接 #CP。

## Intel CET 的兩大機制

CET 是一個 Intel 的 ISA 擴充，引入了兩個完全獨立的控制流保護機制：

### 機制一：Shadow Stack（SS）

**功能**：維護一個硬體保護的第二個 stack，專門儲存 return address，讓 `ret` 指令在返回前做驗證。

**硬體元件**：
- 新增暫存器 **SSP（Shadow Stack Pointer）**：指向 shadow stack 目前頂端。類比 RSP，但 SSP 不能被一般指令直接修改。
- 新增 CPU 狀態位 **SHSTK enable**（在 `CR4.CET` 與 `IA32_U_CET` MSR 控制，OS 設定）
- Shadow stack 記憶體受到特殊頁面屬性（`WRUSS`/`SHSTK` 位）保護：一般的 store 指令無法寫入這個記憶體，只有 CPU 的 `ret`/`call`/`WRSS`/`RSTORSSP` 等特定指令才能操作

**運作流程**：

```
CALL 指令：
    RSP -= 8;  *(RSP) = return_address;   // 傳統 stack（完全不變）
    SSP -= 8;  *(SSP) = return_address;   // shadow stack（硬體同步寫）

RET 指令：
    ret_addr_rsp = *(RSP);  RSP += 8;     // 從傳統 stack 讀（不變）
    ret_addr_ssp = *(SSP);  SSP += 8;     // 從 shadow stack 讀（硬體比對）

    if (ret_addr_rsp != ret_addr_ssp):
        #CP exception (fault code = NEAR-RET)   // 不符：硬體例外
    else:
        JMP ret_addr_rsp                         // 相符：跳回
```

> **未實測，理論預期**：以上是 Intel SDM Vol. 1 Chapter 17 的規格摘要，已盡量忠實呈現。實際的微碼實作細節（pipeline stage、例外觸發時機）不在本文範圍。

**Shadow stack 的記憶體保護**：

Shadow stack 的記憶體頁有特殊的 page-table 設定（`Dirty` 位被重新定義為 `WRSS` 寫入權限），一般的 store 指令（`mov [addr], val`）無法寫入這些頁面。嘗試寫入會觸發 page fault，而不是靜默地成功。這意味著：

- stack overflow 就算覆蓋過了 RSP 上的 return address，shadow stack 的副本完整
- `memcpy`/`strcpy` 型的記憶體竄改影響不到 shadow stack
- `VirtualProtect` 嘗試改 shadow stack 頁面的屬性：OS 會拒絕（shadow stack 頁不允許 user-mode 改保護屬性）

### 機制二：IBT（Indirect Branch Tracking）

**功能**：強制所有間接跳轉（`jmp [reg]`、`call [reg]`、`jmp [mem]`、`call [mem]`）的目標必須是一條 `ENDBR64` 指令（64-bit mode 下）或 `ENDBR32` 指令（32-bit）。

**新增指令**：`ENDBR64` / `ENDBR32`

這兩條指令在沒有 CET IBT 的 CPU 上是 `NOP`（向後相容）。在 CET IBT 開啟的 CPU 上，它們會讓 CPU 的 TRACKER 狀態從「IDLE」切換到「WAIT_FOR_ENDBR」再切換回來：

```
CET IBT 開啟後，CPU 內部有一個 TRACKER 狀態位：

   一般執行（sequential）：TRACKER = IDLE
   執行 indirect branch（jmp reg / call reg）：TRACKER = WAIT_FOR_ENDBR

   ┌───────────────────────────────────────────────────┐
   │  下一條指令是 ENDBR64？                            │
   │  是 → TRACKER = IDLE，繼續正常執行                │
   │  否 → #CP exception (fault code = ENDBRANCH)     │
   │        行程被終止                                  │
   └───────────────────────────────────────────────────┘
```

這意味著：攻擊者的 ROP gadget 如果不是以 `ENDBR64` 開頭，就無法作為 indirect call/jump 的目標。編譯器在每個合法間接跳轉目標的開頭插入 `ENDBR64`，沒有插的地方就被 CPU 當成非法目標。

**IBT vs XFG 的定位差異**：

| 機制 | 方式 | 粒度 | 需要硬體 |
|------|------|------|---------|
| XFG | 型別 hash 軟體比對 | 型別相符的數十個函式 | 否 |
| CET IBT | 硬體追蹤 ENDBR64 | 所有有 ENDBR64 的目標（粒度較粗） | 是（第 12 代 Intel+） |

XFG 比 IBT 更精確（型別限縮），但 IBT 是硬體強制、假陽性更少。兩者在設計上可以同時啟用（疊加保護），也可以各自獨立。

## Windows 的 Hardware-enforced Stack Protection

Microsoft 把 CET shadow stack 整合進 Windows，對外的名稱是 **Hardware-enforced Stack Protection（硬體強制堆疊保護）**，有時也叫 **CET shadow stack**（文件中混用）。

### 啟用方式

#### 程式層級：`/CETCOMPAT` 連結器旗標

```bat
REM 未實測，需 MSVC linker + CET 支援的硬體
link /CETCOMPAT program.obj
```

`/CETCOMPAT` 在 PE 的 Load Config 目錄裡設置一個旗標，告訴 OS「這個執行檔希望開 shadow stack」。OS 在載入時看到這個旗標，如果硬體支援 CET，就為這個行程啟用 shadow stack。

> **未實測，理論預期**：`/CETCOMPAT` 需要 MSVC link.exe（本機尚未安裝），以及 `dumpbin /loadconfig` 驗證旗標設置。

#### OS / 系統政策層級

Windows 也可以透過 Windows Defender Exploit Guard（WDEG）或系統登錄強制對特定行程或全系統啟用 CET：

```powershell
# 未實測；理論上可用 WDEG 或 Set-ProcessMitigation 啟用 CET
Set-ProcessMitigation -Name target.exe -Enable UserShadowStack
```

這讓 OS 可以為**沒有重新編譯**的舊程式啟用 shadow stack（相容模式），代價是可能有相容性問題（見後面的限制節）。

#### 確認 CET 狀態（系統層）

> **未實測，理論預期**：在支援 CET 硬體上，可以用 WinDbg 的 `!cpuid` 或直接查 `CPUID[7,0].ECX[7]`（CET shadow stack）確認硬體支援。

```bat
REM 未實測；在 WinDbg 或 cdb 裡確認 CET 狀態
!cpuid
REM 確認 CPUID leaf 7, subleaf 0, bit ECX[7] = 1 (CET SS support)
REM 確認 CPUID leaf 7, subleaf 0, bit EDX[20] = 1 (CET IBT support)
```

### Shadow stack 的記憶體佈局

每個執行緒（thread）有自己的 shadow stack，OS 在執行緒建立時分配。

```
一般 stack（RSP 管理）：
─────────────────────────────────────────────────────
高位址 ┌──────────────────────────────┐
        │  thread stack 頂端（初始 RSP）│
        ├──────────────────────────────┤
        │  CALL fn1 壓入：ret_addr_1   │
        ├──────────────────────────────┤
        │  fn1 的 locals              │
        ├──────────────────────────────┤
        │  CALL fn2 壓入：ret_addr_2   │
        ├──────────────────────────────┤
        │  fn2 的 locals              │  ← 溢位可以淹到 ret_addr_2
        ├──────────────────────────────┤
        │  ……                          │
低位址  └──────────────────────────────┘ ← 目前 RSP

shadow stack（SSP 管理，獨立記憶體區域）：
─────────────────────────────────────────────────────
高位址 ┌──────────────────────────────┐
        │  (shadow stack 頂端)         │
        ├──────────────────────────────┤
        │  ret_addr_1 的副本           │
        ├──────────────────────────────┤
        │  ret_addr_2 的副本           │  ← 硬體保護，攻擊者碰不到
        ├──────────────────────────────┤
        │  ……                          │
低位址  └──────────────────────────────┘ ← 目前 SSP

  ↑
  shadow stack 和一般 stack 是分開的獨立記憶體，
  映射在不同的虛擬位址範圍，
  有不同的頁面屬性（SHSTK bit），
  一般 store 指令無法寫入。
```

SSP 的值在執行緒運行時由 CPU 自動更新，OS 核心在執行緒切換（context switch）時儲存/還原 SSP（存在 KTHREAD 結構裡），就像儲存/還原 RSP 一樣。

### SSP 暫存器與特殊指令

> **未實測，理論預期**：以下暫存器與指令的語意來自 Intel SDM Vol. 1 Ch 17，未在本機硬體驗證。

Shadow stack 引入了幾個新的操作元件：

| 元件 | 說明 |
|------|------|
| **SSP** | Shadow Stack Pointer，類比 RSP；user-mode 可以讀（`RDSSP`），但一般 store 不能寫 |
| `RDSSP reg` | 讀 SSP 的當前值到暫存器（user-mode 允許，但意義有限） |
| `INCSSP reg` | 以特定步驟增加 SSP（類比 `add rsp, N`，用於函式的 shadow stack 調整） |
| `RSTORSSP [mem]` | 從記憶體恢復 SSP（OS 的 context switch 使用） |
| `SAVEPREVSSP` | 儲存前一個 shadow stack 的 SSP（longjmp / setjmp 支援） |
| `WRSS [mem], reg` | 向 shadow stack 寫入（只允許在 kernel mode 或特定授權路徑） |

對攻擊者來說，最關鍵的是：user-mode 的一般 `mov [addr], val` 無法寫入 shadow stack 記憶體，`WRSS` 的使用有額外限制，而 `INCSSP` 只能以有限的步長增加 SSP（不能任意跳）。

## CET + CFG/XFG 組合：「傳統控制流劫持」幾乎被堵死

讓我們把整個防禦層疊起來看：

```
攻擊者的傳統路線（2015 年之前）：
─────────────────────────────────────────────────────
  [漏洞原語] → 竄改 return address 或函式指標
                         ↓
              [任意程式碼執行]

攻擊者的路線（DEP + ASLR 時代）：
─────────────────────────────────────────────────────
  [漏洞原語] → info leak 拿到 gadget 位址
                         ↓
              竄改 return address → ROP chain
                         ↓
              [call VirtualProtect / mprotect → shellcode]

攻擊者的路線（CFG 時代，2015–2018）：
─────────────────────────────────────────────────────
  [漏洞原語] → info leak
                         ↓
              ROP chain（CFG 不管 ret）
                         ↓
              合法函式 target 作為 pivot（繞過 CFG bitmap 驗證）
                         ↓
              [程式碼執行]

攻擊者的路線（XFG + CET shadow stack + CET IBT 時代）：
─────────────────────────────────────────────────────
  竄改 return address？ → CET shadow stack 在第一個 ret 就 #CP  ← 殺死 ROP
  竄改函式指標？       → XFG hash 不符 → RaiseFailFastException  ← 殺死 CFI 繞過
  indirect jump gadget？→ CET IBT 要求 ENDBR64 → #CP            ← 限縮 gadget 集合
  合法型別相符函式？   → XFG 縮到數十個，攻擊者空間極小
```

三層聯合之後，傳統意義上「竄改控制流」的攻擊路線幾乎被封死。攻擊者面對的局面是：

1. 不能 ROP（shadow stack）
2. 不能隨意設函式指標（XFG）
3. 不能用非 ENDBR64 的 gadget（IBT）
4. 就算找到型別相符的合法函式，選項也極少（XFG）

這就是為什麼 **data-only attacks（資料導向攻擊）** 在現代緩解體系下從冷門研究課題變成了主流攻擊路線——那是唯一剩下的大路。Ch 37 整章會系統性地討論。

## CET 的限制與繞過考量

誠實地說，CET 沒有把所有問題都解決。以下是已知的限制與可能的攻擊面：

### 1. 相容性問題導致部分部署

CET shadow stack 的最大敵人是**相容性**。以下情況都可能要求暫時禁用或降級 shadow stack：

- **JIT 編譯器（JIT 引擎）**：JIT 生成的程式碼在執行時才知道 return address，傳統 JIT 的呼叫約定與 shadow stack 不相容。瀏覽器（Edge、Chrome）都需要特別的 JIT 相容模式（`VirtualProtect` + shadow stack 特殊切換）才能運作。
- **第三方攔截（Hook）**：許多防毒軟體、除錯器、及合法的程式（如輸入法框架）會在 `ntdll` 函式入口掛 hook（竄改函式開頭幾個 bytes），呼叫約定改變，shadow stack 可能在 hook 跳回時 mismatch 觸發 #CP。
- **C `setjmp`/`longjmp`**：`longjmp` 跳過多個 stack frame，RSP 回到 `setjmp` 時的位址，但 shadow stack 裡仍有那些被跳過的 frame 的 return address 副本，需要特殊的 `SAVEPREVSSP`/`RSTORSSP` 機制才能正確調整 SSP。不是所有程式都正確處理這個。
- **手寫組語函式**：沒有正確 prologue/epilogue（`push rbp; ... pop rbp; ret`）的組語函式，shadow stack 的深度和 RSP 的深度可能不同步。

> **未實測，理論預期**：微軟在 Windows 11 22H2 之後對一些系統元件強制啟用了 shadow stack，但仍有相容性 opt-out 機制（`SetProcessMitigationPolicy` 可以動態禁用 shadow stack per-process）。

### 2. Shadow stack 本身的攻擊面

雖然 shadow stack 頁受硬體保護，但攻擊者並不是完全碰不到它：

- **`INCSSP` 指令的有限操縱**：`INCSSP` 可以增加 SSP（跳過部分 shadow stack 的 entry），但步長有限（最大 256 * 8 bytes per 指令）。如果攻擊者能控制程式執行路徑，重複呼叫某個用了 `INCSSP` 的函式，理論上可以讓 SSP 和 RSP 失去同步，製造出 shadow stack underflow 的狀況。這屬於高難度、高前提的攻擊面。

- **Shadow stack 頁的 mmap 位址預測**：OS 為 shadow stack 分配的虛擬位址如果可被攻擊者預測（弱 ASLR 場景），攻擊者可以嘗試在 shadow stack 頁周邊做某些操作。但實際上 shadow stack 的頁面保護在 page table 層就攔截了一般 store，這個路線非常困難。

- **核心層的 context save/restore**：OS 核心在 context switch 時要儲存/還原 SSP。如果核心本身有漏洞（kernel 利用場景），攻擊者可以竄改 KTHREAD 裡儲存的 SSP 值，讓下次 context switch 後 shadow stack 指向攻擊者控制的記憶體。這已經是核心層攻擊，不在 userland CET 的保護範圍內。

> **未實測，理論預期**：Connor McGarr 在其研究中分析了 shadow stack 頁面的頁表屬性（`SHSTK` bit），以及 `INCSSP` 的限制如何限縮但不消滅攻擊面。

### 3. 未支援 CET 的舊硬體與行程

現實世界裡：

- 第 10 代 Intel Core 或更舊、AMD Zen 2 或更舊的 CPU **不支援 CET shadow stack**（硬體不存在 SSP 暫存器）
- 即使硬體支援，如果行程沒有設 `/CETCOMPAT` 旗標（且沒有系統政策強制），shadow stack 不會被啟用
- 行程裡只要有一個載入的 DLL 有相容性問題，OS 可能為整個行程禁用 shadow stack

這意味著在真實的攻擊場景中，攻擊者會先確認目標的硬體和 OS 版本、行程的緩解設定，找到 CET 沒有覆蓋到的入口。

### 4. Data-only 攻擊（CET 完全擋不住）

這是最根本的限制，也是我們在整個 Part 5 一再強調的：

CET shadow stack 保護 `ret` 的返回位址。CET IBT 限制 indirect jump 目標。XFG 限制 indirect call 型別。**但如果攻擊者根本不碰控制流，只竄改資料——**：

- 把一個 struct 的 `is_admin` 欄位從 0 改成 1 → 繞過身分驗證
- 把 `credential` 結構裡的帳號替換 → 橫向移動
- 把 length 欄位改大讓程式的下一個 memcpy 複製超出範圍 → 利用程式的正常控制流完成越界寫

這種攻擊完全不觸碰函式指標、不 ROP、不跳到任何非預期的程式碼位址。所有 CFI 機制對此視而不見。Ch 37 系統性討論。

## CET 的硬體與 OS 需求

> **未實測，理論預期**：以下需求資訊基於 Intel 白皮書與 Microsoft 官方文件。

```
CET Shadow Stack 需求：
────────────────────────────────────────────────
  CPU  │ Intel 第 11 代（Tiger Lake, 2020）以上
       │ AMD Zen 3（Ryzen 5000, 2020）以上
  OS   │ Windows 10 版本 2004（20H1, Build 19041）以上
       │ Linux 5.18+（arch/x86/kernel/shstk.c 引入）
  編譯器│ MSVC：link.exe /CETCOMPAT
       │ GCC/Clang：-fcf-protection=return（Linux 路線）

CET IBT (Indirect Branch Tracking) 需求：
────────────────────────────────────────────────
  CPU  │ Intel 第 12 代（Alder Lake, 2021）以上
       │ AMD Zen 4（Ryzen 7000, 2022）以上
  OS   │ Windows 11 22H2 以上（user-mode IBT 支援）
       │ Linux 5.18+（-fcf-protection=branch）
  編譯器│ GCC：-fcf-protection=full 插入 ENDBR64
       │ Clang：-fcf-protection=full
       │ MSVC：理論上支援，旗標與 GCC 不同
```

## 對照 Linux 的 CET 支援

Linux 社群從 kernel 5.18 開始引入 CET shadow stack 支援，glibc 2.35 起支援 shadow stack 相容的 setjmp/longjmp。

| 項目 | Linux | Windows |
|------|-------|---------|
| 硬體 shadow stack | kernel 5.18+，XSAVE CET_U 狀態 | Win10 20H1+，/CETCOMPAT |
| 使用者態啟用方式 | `prctl(PR_SET_SHADOW_STACK_STATUS, ...)` | `/CETCOMPAT` PE 旗標 or WDEG 政策 |
| IBT 支援 | `-fcf-protection=branch`（GCC/Clang） | Windows 11 22H2+ |
| setjmp/longjmp 相容 | glibc 2.35+ 正確處理 | MSVC CRT 有對應處理 |
| 主要阻力 | 第三方 hook、JIT | 相同 |
| 部署廣度（2026） | Android + 部分伺服器 Linux | Windows 11 新硬體 |

值得注意的是，Linux 走的是 `prctl` + gcc 旗標的路線，不需要修改 PE/ELF 格式。效果相同，只是 OS/ABI 整合方式不同。

## 底層機制：shadow stack 頁面如何受保護

> **未實測，理論預期**：以下 page table 分析基於 Intel SDM Vol. 3 和 Connor McGarr 的研究。

Shadow stack 的記憶體保護不是一般的 `PAGE_READONLY`——它靠的是 page table 裡的一個特殊位元組合：

```
一般記憶體頁（page table entry, PTE）：
  Bit 0  (P)   = 1  (present)
  Bit 1  (W)   = 1  (writable)
  Bit 63 (XD)  = 0  (executable)

Shadow stack 頁面（PTE）：
  Bit 0  (P)   = 1  (present)
  Bit 1  (W)   = 1  (「dirty」位被重新定義為 WRSS 寫入許可)
  Bit 10 (D)   = 1  (「dirty」設定，表示這是 shadow stack 頁)
  Bit 63 (XD)  = 1  (不可執行)

  當 CET 開啟時，CPU 檢查：
  「W=1 但 D=1」 的頁面 = shadow stack 頁面 → 禁止一般 store 指令寫入
```

這個設計讓 shadow stack 頁面在 page table 層面對一般 load 透明（可以讀），但 store 被硬體攔截。攻擊者就算知道 shadow stack 的位址（info leak），也無法用一般的 `mov [addr], val` 寫入。

## 對比與取捨

| 維度 | CFG | XFG | CET Shadow Stack | CET IBT |
|------|-----|-----|------------------|---------|
| 保護對象 | indirect call/jump | indirect call（型別限縮） | `ret` 的 return address | indirect jump 目標 |
| 機制層 | 軟體（OS+compiler） | 軟體（compiler） | 硬體（CPU ISA） | 硬體（CPU ISA） |
| 殺死 ROP | 否 | 否 | **是** | 部分（限縮 gadget 集） |
| 需要硬體 | 否 | 否 | 是（11代 Intel+） | 是（12代 Intel+） |
| 需要重編 | 是（MSVC） | 是（MSVC /guard:xfg） | 是（MSVC /CETCOMPAT） | 是（ENDBR64 插入） |
| 相容性風險 | 低 | 中（型別 mismatch） | 中（JIT/hook） | 中（老組語） |
| 主要殘存弱點 | 任何合法 target | 同型別 target | data-only | 有 ENDBR64 的 gadget |
| Linux 對應 | grsec RAP | Clang CFI | kernel 5.18+ CET SS | `-fcf-protection=branch` |

## 踩雷集錦

1. **「CET shadow stack 保護 RBP、局部變數，也保護 return address 以外的東西」**：不，shadow stack 只存 **return address**（`call` 指令壓的那個值）。RBP、局部變數、函式參數全都在一般 stack，攻擊者仍然可以覆蓋它們。攻擊者的機會是：用竄改局部變數來影響函式的**行為**，而不是控制流目標。這就是 data-only 攻擊的切入點之一。

2. **「CET IBT 和 XFG 做的是同一件事」**：不一樣。XFG 是型別層級的限縮（同型別函式才通過），IBT 是指令層級的限縮（有 `ENDBR64` 才通過）。XFG 更精確，IBT 更硬體強制。兩者可以同時開，但關注的不是同一個維度。

3. **「shadow stack 開了，`longjmp` 就不能用了」**：不對，但需要正確支援。MSVC CRT 和 glibc 都針對 CET shadow stack 修改了 `setjmp`/`longjmp` 的實作，用 `RSTORSSP` 正確調整 SSP。用的是庫的 setjmp，不是手寫的，就沒有問題。

4. **「`ENDBR64` 是很重的指令，會拖慢程式」**：`ENDBR64` 在沒有 CET 的 CPU 上是 `NOP`（4 bytes），開銷完全是零。在有 CET 的 CPU 上，它的開銷也極小（pipeline state 轉換，無記憶體操作）。Intel 量測的效能開銷在 2–3% 以內，對多數程式來說可忽略。

5. **「開了 CET shadow stack 就不用 ASLR 了」**：完全不對。Shadow stack 防的是「攻擊者已知道 gadget 位址後用 ROP 跳過去」這件事。但攻擊者如果不用 ROP（data-only），或者如果能找到一個不觸發 shadow stack 驗證的控制流路徑（例如 vtable hijack，如果同時沒有 VTGuard），ASLR 的 info leak 防護仍然重要。各層緩解不是替代關係，是縱深防禦。

## 進階：再往深一層

### CET Shadow Stack 的 Windows 核心整合

> **未實測，理論預期**：以下是基於公開 Windows 核心研究的理論描述。

Windows 核心在 context switch 時，把 SSP 存在 `KTHREAD.CetUserSsp` 欄位（具體結構欄位名稱以你環境的 `dt nt!_KTHREAD` 輸出為準）。每次 NtCreateThread / NtCreateThreadEx 時，OS 分配 shadow stack 記憶體（用 `NtAllocateVirtualMemory` 加上特殊屬性），並在 KTHREAD 裡記錄初始 SSP。

User-mode shadow stack 的範圍可以用 `NtQueryVirtualMemory` 查，shadow stack 頁面的 `State` 和 `Type` 會顯示特殊屬性（不同於一般的 `MEM_PRIVATE`）。

### 如何判斷你的目標啟用了 CET

> **未實測，理論預期**：

```bat
REM 用 dumpbin 看 Load Config 的 GuardFlags
dumpbin /loadconfig target.exe
REM 找 GuardFlags 裡是否有 CET 相關旗標

REM 用 Process Hacker / Sysinternals Process Explorer
REM 看行程的「Mitigation Policies」欄位，確認 CET Shadow Stack 狀態

REM 用 PowerShell
Get-ProcessMitigation -Name target.exe
REM CETDynamicApisOutOfProcOnly, UserShadowStack, UserShadowStackStrictMode 的值
```

### Connor McGarr 的 CET bypass 研究

Connor McGarr 在 2021 年發表了一系列關於 Windows CET 繞過的研究，探討了幾個可能的方向：

1. **`INCSSP` 濫用**：若攻擊者能夠控制多次呼叫某個使用 `INCSSP` 的函式，理論上可以讓 SSP 和 RSP 失步，但實際利用難度極高（需要精確對齊）。

2. **Exception handler 路徑**：某些 OS 的例外分發路徑在 shadow stack 相容性上有複雜的邊界情況，例如 VEH 的 `RtlRestoreContext` 呼叫需要特殊的 shadow stack token（`SAVEPREVSSP` 機制），如果未正確實作就可能有空隙。

3. **非 CET 行程的 shadow stack 停用**：攻擊者如果能以某種方式讓 OS 認為行程有相容性問題，可能誘導 OS 自動禁用 shadow stack（opt-out 機制）。

> **前提**：以上研究在 McGarr 發表時（2021）是前沿研究，部分可能在後續 OS 更新中被修補。「CET bypass」的威脅模型不是「shadow stack 有設計缺陷」，而是「在 OS 與應用的整合層有邊界條件」。

## 動手練習

> **環境需求**：需要支援 CET 的 CPU（Intel 第 11 代以上或 AMD Zen 3 以上）、Windows 10 20H1+、MSVC linker（本機尚未安裝）。以下描述你裝好後可以做的實驗。

**任務一：確認硬體支援 CET**

```python
# 用 Python 的 ctypes 讀 CPUID，確認 CET 位元
# 未實測，需在有 CPUID 支援的平台驗證
import ctypes

# CPUID leaf 7, subleaf 0：
# ECX[7] = 1 → CET Shadow Stack 支援
# EDX[20] = 1 → CET IBT 支援
# 在 Python 裡無法直接呼叫 CPUID，可以用 ctypes 呼叫 GetSystemInfo
# 或用 WinDbg !cpuid 指令
```

**任務二：編 `/CETCOMPAT` 程式，觀察 ROP 被擋**

```bat
REM 未實測，需 MSVC + CET 硬體
REM 1. 寫一個有 stack buffer overflow 的 C 程式
REM 2. 編譯：cl /GS- /O2 target.c
REM 3. 連結：link /CETCOMPAT /NXCOMPAT target.obj
REM 4. 不開 CET 的版本：用一個小的 ROP chain（兩個 gadget）讓 RIP 跳到預期位址
REM 5. 開 CET 的版本：用同樣的 payload，觀察 #CP exception 在哪裡觸發
REM    （WinDbg 裡 sxe cp 可以在 #CP 時停下來）
```

**任務三：觀察 shadow stack 記憶體**

```bat
REM 未實測，需 WinDbg + CET 硬體
REM 啟動一個 /CETCOMPAT 行程，掛上 WinDbg
REM !teb → 找 shadow stack 的位址範圍
REM dq <shadow_stack_addr> → 看 shadow stack 的內容（應該是 return address 序列）
REM 和 k （call stack）對照，確認兩者的 return address 一致
```

## 本章重點整理

- Intel CET 的兩大機制：**shadow stack**（讓 `ret` 指令比對硬體維護的第二份 return address，直接殺死 ROP）與 **IBT**（要求 indirect 跳轉目標有 `ENDBR64`，限縮 gadget 集合）
- Shadow stack 的核心原理：`call` 時 CPU 同時寫一般 stack 和 shadow stack；`ret` 時兩者比對，不符則 #CP 例外——攻擊者即使竄改 RSP 上的 return address，SSP 的副本完整，在第一個 `ret` 就被硬體斬斷
- Windows 的 Hardware-enforced Stack Protection 用 `/CETCOMPAT` 旗標整合 CET；每個行程、每個執行緒有獨立的 shadow stack，OS 負責分配與 context switch 時的儲存/還原
- **CET + CFG/XFG 組合**把「傳統控制流劫持」（ROP + CFG 繞過 + 任意函式指標竄改）的主要路線幾乎封死，**逼向 data-only 攻擊**（Ch 37）
- CET 的限制：相容性（JIT/hook）、需要新硬體、data-only 攻擊完全不擋、部分部署（非所有行程都有 `/CETCOMPAT`）

## 自我檢核

- [ ] 不看筆記，能在紙上畫出 ROP 攻擊如何被 shadow stack 擋住的圖：一般 stack 被改、shadow stack 完整、`ret` 時 #CP
- [ ] 能解釋 shadow stack 記憶體為什麼一般 `mov` 指令寫不進去（從 page table 屬性角度回答）
- [ ] 知道 SSP 是什麼、誰管理它、context switch 時 OS 怎麼保存/還原它
- [ ] 能說出 CET shadow stack 和 CET IBT 分別防的是哪種攻擊模式、需要哪一代硬體
- [ ] 被問「CET 開了，還能怎麼打？」，能說出至少三個殘存攻擊面（包含 data-only）
- [ ] 能解釋為什麼 `longjmp` 在 CET shadow stack 下需要特殊處理，以及 OS/libc 是怎麼解決的

## 延伸閱讀

### 硬體規格（最高優先）

- **Intel® 64 and IA-32 Architectures Software Developer's Manual, Volume 1, Chapter 17：「Control Flow Enforcement Technology (CET)」**
  - **讀哪裡**：Section 17.1（CET 概觀）、17.2（Shadow Stack）、17.3（IBT）；這是機制的一次文件
  - **和本章的關聯**：本章所有「理論預期」的來源；有硬體後應對著 SDM 逐一驗證
  - **前提知識**：x86-64 暫存器模型、XSAVE 狀態機制

### Microsoft 官方部落格

- **「Understanding Hardware-enforced Stack Protection」— Microsoft Security Blog（MSRC）**
  - **讀哪裡**：全文；Microsoft 官方說明 Windows 如何整合 CET，包含 `/CETCOMPAT` 的使用、行程政策設定、與 JIT 的相容性處理
  - **和本章的關聯**：Windows 整合層的官方說明；把本章「理論預期」的 Windows 側細節轉成有 MSRC 背書的結論
  - **URL 提示**：在 MSRC Blog 搜 "Hardware-enforced Stack Protection"

### 研究部落格（重點）

- **Connor McGarr — CET bypass 研究系列**（個人部落格，2021）
  - **讀哪裡**：「Exploit Development: Browser Exploitation - CET Bypass (Part 2)」及相關文章；這篇是 CET user-mode 繞過目前最深的公開研究
  - **和本章的關聯**：本章「殘存攻擊面」與「shadow stack 的攻擊面」節的主要依據；本章認為 CET 沒有「設計缺陷」而只是「整合邊界條件」的定性判斷就是以這篇研究的結論為基礎
  - **前提知識**：shadow stack 原理（本章）、Windows exploit 開發基礎（Part 3-4）

### Linux 路線的對比

- **「x86 Shadow Stack support — Linux kernel documentation」**（linux/Documentation/arch/x86/shstk.rst）
  - **讀哪裡**：全文不長；說明 Linux 如何用 `prctl` 和 `arch_prctl` 讓 user-space 啟用 shadow stack、glibc 的 setjmp 修改、以及 signal frame 的 shadow stack 處理
  - **和本章的關聯**：Linux vs Windows 整合方式差異的一次對比資料；本章 Linux 對照表的具體來源

---

CET shadow stack 在硬體層把 ROP 的根基（`ret` 指令可以被竄改的假設）撤掉了。加上 XFG 對 indirect call 的型別限縮，「傳統控制流劫持」這條路幾乎走到了終點。下一章的 ACG（Arbitrary Code Guard）和 CIG（Code Integrity Guard）把問題往前推一步：即使攻擊者有辦法執行程式碼，也要阻止它在執行期把**新的程式碼**注入記憶體。

→ [Ch 36 — ACG / CIG / code integrity](./36-acg-cig-code-integrity.md)
