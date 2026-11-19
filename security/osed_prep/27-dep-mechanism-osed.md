# Ch 27 — DEP 機制復習 + OSED 情境

> **目標**：搞懂 DEP（Data Execution Prevention）在 Windows 上到底擋了什麼、硬體層的 NX bit 怎麼運作、四種 DEP 政策（OptIn/OptOut/AlwaysOn/AlwaysOff）的差異，以及這一切如何具體改變 OSED 考試現場的 exploit 寫法——從「蓋 EIP 直接跳 shellcode」被迫升級成「蓋 EIP 先跑 ROP」。

> **環境**：Win10 x64 + WinDbg（2026）+ mona v3 + Python 3

## 為什麼要重新講一次 DEP？

Part 2、3 練的 stack / SEH overflow，收尾動作都一樣：控制了 `EIP`（或 SEH handler），讓它跳到 shellcode 所在的 stack 位址，執行完收工。那個世界有一個隱藏的前提——**stack 上的資料可以被 CPU 當指令抓出來執行**。

DEP 就是專門打破這個前提的機制。一旦目標程序開了 DEP，Part 2、3 練的「蓋 EIP → jmp esp → shellcode」那條路直接斷掉：CPU 一讀到 stack 頁上的 bytes 要當指令執行，就跳例外，程式當掉，exploit 失敗在最後一步。

這章把 Ch 7–10 隱藏的那個前提挑出來講清楚，再告訴你 OSED 現場怎麼應對。Ch 28–30 才是真正動手兵器（找 gadget、組 chain、除錯），這章先把「為什麼非做不可」的機制講透，免得你把 ROP 練成背指令、不知道在解決什麼問題。

## 先建立直覺：一道牆，而不是一道鎖

DEP 不是「偵測 shellcode 然後擋掉」，它比那個笨得多、也因此強得多：它根本不管 stack 上那段 bytes 是不是 shellcode，**它只管那一頁記憶體有沒有被標成「可執行」**。

```
沒有 DEP 的世界（Part 2–3）：

   stack 頁（可讀可寫，預設也可執行）
   ┌─────────────────────────┐
   │ [A][A][A]...[shellcode] │ ← CPU 願意把這段 bytes 當指令 fetch
   └─────────────────────────┘
          ↑
   EIP 指過來 → 直接執行，沒人攔

有 DEP 的世界（這一章開始）：

   stack 頁（可讀可寫，標記「不可執行」= NX）
   ┌─────────────────────────┐
   │ [A][A][A]...[shellcode] │ ← bytes 還在，內容沒變
   └─────────────────────────┘
          ↑
   EIP 指過來 → CPU 去 fetch 指令 → 查頁表發現 NX=1
            → 直接觸發例外，shellcode 一行都沒跑
```

关键字是**頁（page）**，不是「這段資料」。DEP 是頁表層級的開關，CPU 每次要抓指令執行前都會查一次，和這段 bytes 的「內容」毫無關係——哪怕你把 shellcode 偽裝成合法指令也沒用，問題不在內容,在那一頁的屬性。

## 核心機制：NX bit 怎麼工作

### 硬體層：頁表裡的一個 bit

x86/x64 的分頁機制裡，每個 Page Table Entry（PTE）除了存實體頁框號，還有一堆旗標 bit，其中一個就是 **NX（No-eXecute）bit**：

```
PTE（簡化示意，PAE/x64 長模式下的格式）：

  bit 63       : NX   ← 1 = 這頁的內容不可被當指令 fetch
  bit 62-52    : 保留 / 其他現代旗標
  bit 51-12    : 實體頁框號（PFN）
  bit 11-0     : Present / RW / US / Accessed / Dirty 等標準旗標
```

這顆 bit 是 **CPU 硬體**負責檢查的，不是作業系統軟體模擬出來的行為。AMD 把它叫 **NX**，Intel 把對應功能叫 **XD（Execute Disable）**——名字不同，位置和語意一致。CPU 在 instruction fetch 階段查這個 bit，如果目標頁 NX=1，直接觸發 `#PF`（page fault），Windows kernel 把它轉成你在 WinDbg 裡熟悉的：

```
(未實測，理論預期 — WinDbg 斷下來的畫面)
(a).(b) ???: Access violation - code c0000005 (first/second chance not available)
First chance exceptions are reported before any exception handling.
This exception may be expected and handled.
eax=... ebx=... ecx=... edx=... esi=... edi=...
eip=41414141
...
```

`STATUS_ACCESS_VIOLATION`（`0xC0000005`）是個通用的存取違規碼，DEP 觸發的版本在例外參數裡會多一個「因為執行不可執行頁」的細節（WinDbg 的 `!analyze -v` 會指出 "The instruction at ... referenced memory at ..." 並標 "This memory could not be %s"，執行類違規會寫 "The data at memory location ... could not be executed"）。能不能直接看出是 DEP 觸發、還是普通野指標讀寫,靠的是這行字,而不是 code 本身——`0xC0000005` 讀、寫、執行三種違規共用同一個碼。

> **未實測，理論預期**：上述 WinDbg 輸出未在本機實際偵錯過。驗證法：關閉 DEP 跑一次相同 payload（跳過去正常執行 shellcode），再開啟 DEP 跑一次（crash），比較兩次 `!analyze -v` 的輸出差異，確認是不是「execute」類型的 access violation。

### 軟體層：Windows 怎麼用這顆 bit

NX bit 是硬體機制，但「要不要用它、對誰用」是 Windows 的政策決定，分好幾層：

| 層次 | 設定點 | 作用範圍 |
|---|---|---|
| **硬體/BIOS DEP** | CPU 是否支援 NX/XD + BIOS 是否開啟 | 整台機器的先決條件，現代 CPU 幾乎都支援且預設開 |
| **系統層 DEP 政策** | `bcdedit /set nx <policy>` | 全系統，決定「哪些 process 預設套用 DEP」 |
| **Process 層 DEP** | PE 的 `NX_COMPAT`（`DllCharacteristics` 旗標）、`SetProcessDEPPolicy` | 單一行程 |
| **頁面層保護** | `VirtualAlloc`/`VirtualProtect` 的 `PAGE_*` 參數 | 單一記憶體頁或頁範圍 |

這四層裡，OSED 考試現場你會天天打照面的是第二層（系統政策）和第四層（`VirtualProtect`，Ch 29 的主角）。

### 系統層的四種 DEP 政策

`bcdedit /set nx <value>` 的四個值，意義不是「開/關」兩檔那麼簡單：

| 政策 | 行為 | 考試現場意義 |
|---|---|---|
| **OptIn**（Windows 預設） | 預設**不**套用 DEP，只有明確 opt-in（帶 `NX_COMPAT` 旗標且是系統核心元件,或透過設定清單加入）的程式才受保護 | 很多舊式/第三方服務預設**沒有** DEP，直接可以 Part 2 那套打法 |
| **OptOut** | 預設**全部**套用 DEP，除非程式被明確排除在例外清單之外 | 比 OptIn 嚴格；企業環境常見 |
| **AlwaysOn** | 全系統**強制**套用 DEP，不給例外清單 | 最嚴格；本課 Ch 27–30 練的就是這個情境 |
| **AlwaysOff** | 全系統關閉 DEP | 練習時想先確認「關 DEP 真的能跑」用這個對照 |

```powershell
# 查目前政策
bcdedit /enum {current} | findstr nx

# 設成 AlwaysOn（本課 Part 7 的練習情境），需系統管理員，需重開機生效
bcdedit /set nx AlwaysOn

# 練習結束想對照「沒有 DEP」時的行為，改回或設 AlwaysOff
bcdedit /set nx AlwaysOff
```

> **未實測，理論預期**：以上 `bcdedit` 指令為 Microsoft 官方文件行為；本機未實際跑過 GUI 重開機驗證生效。驗證法：改完政策、重開機後,用 Task Manager 的「詳細資料」頁加上「資料執行防止」欄，或 `Get-Process | Select Name, @{n='DEP';e={...}}`（需配合 WMI），確認目標程序的 DEP 狀態符合預期。

### Process 層：PE 的 NX_COMPAT 旗標

就算系統政策是 OptIn，個別 PE 檔案也能自己宣告「我支援 DEP，請對我套用」——這個宣告寫在 PE Optional Header 的 `DllCharacteristics` 裡,`0x0100` 這個 bit 就是 **IMAGE_DLLCHARACTERISTICS_NX_COMPAT**。MSVC、mingw 現代版本預設都會打開它。這代表：**OSED 考場遇到的目標,只要是近期編譯的,幾乎都帶 DEP**,不要假設「它看起來是舊程式就沒有 DEP」,先用 `!mona modules` 或 `dumpbin /headers` 確認。

## OSED 情境：你的 exploit 被迫升級

把前面的機制接回 exploit 開發現場，流程是這樣變化的：

```
Part 2–3（無 DEP）的 exploit 流程：

  overflow → 蓋 EIP/SEH handler → 跳到 shellcode（在 stack 上）→ 執行 → 彈 shell
                                        ↑
                                 這一步的前提：stack 頁可執行


Part 7（有 DEP）的 exploit 流程：

  overflow → 蓋 EIP/SEH handler → 跳到 ROP chain（也在 stack 上,但只是「資料」）
                                        │
                                        ▼
                      ROP chain 呼叫 VirtualProtect，把 shellcode 所在頁
                      改成 PAGE_EXECUTE_READWRITE（可執行）
                                        │
                                        ▼
                      VirtualProtect 返回 → 跳到 shellcode → 執行 → 彈 shell
```

注意最關鍵的一點：**ROP chain 本身也是放在 stack 上的資料，不是指令**。CPU 從頭到尾沒有把 ROP chain 的 bytes 當指令抓出來執行過——每個 gadget 位址都指向**既有的、合法的、已經標記可執行的程式碼頁**（系統 DLL 或靶 binary 自己的 `.text`）。ROP 不是繞過 DEP 的檢查，而是**完全不去踩那條檢查**：攻擊者只是重複利用已經合法存在的可執行程式碼片段，用 `ret` 把它們串起來。

```
為什麼 ROP chain 本身不會被 DEP 擋下來：

  stack 頁（NX=1，不可執行）
  ┌────────────────────────────┐
  │ [gadget1_addr]             │ ← 這是「資料」，CPU 讀它當位址用，不是當指令執行
  │ [gadget2_addr]             │
  │ [VirtualProtect_addr]      │
  │ ...                        │
  └────────────────────────────┘
          │ ret 彈出位址，EIP 跳過去
          ▼
  kernel32.dll 的 .text 頁（NX=0，可執行）
  ┌────────────────────────────┐
  │ pop eax                    │ ← 這才是真正被 CPU fetch 並執行的指令
  │ ret                        │
  └────────────────────────────┘
```

Ch 28 會把「怎麼找這些 gadget」講清楚，Ch 29 把「怎麼拼出呼叫 VirtualProtect 的 chain」講完。

## ret2libc 到 ROP 的演進

你在 Linux pwn 裡大概先學的是 **ret2libc**：overflow 蓋 saved return address,直接跳到 libc 裡某個函式（例如 `system`），再在它後面接偽造的參數,讓函式以為自己是被正常呼叫的。ROP 是 ret2libc 的推廣：

| 面向 | ret2libc | ROP |
|---|---|---|
| **跳的目標** | 函式的**入口**（例如 `system+0`） | 任意**片段**（gadget），不必是函式入口 |
| **能做的事** | 呼叫一個現成函式，參數照它的 calling convention 擺好即可 | 可以組合多個片段做任意運算（設暫存器、算術、記憶體寫入），再呼叫函式 |
| **對 calling convention 的依賴** | 必須完全照目標函式期望的方式擺參數 | 一樣要照，但中間可以先用 gadget「湊出」正確的參數值（例如壞字元迴避、動態算位址） |
| **典型 Windows 用途** | 單純呼叫 `WinExec`（如果參數本來就在正確位置） | 呼叫 `VirtualProtect`（四個參數、還要動態算 shellcode 位址）幾乎不可能不用 ROP |

Windows exploit dev 幾乎不單獨玩「純 ret2libc」，因為 Windows 的目標函式（`VirtualProtect`、`VirtualAlloc`）參數比 `system(cmd)` 複雜得多（stdcall、四個參數、其中一個還要是可寫位址），單靠「跳函式入口」湊不出完整呼叫，一定要上 ROP gadget 動態組參數。這也是為什麼本課從 Ch 28 開始直接進 ROP，不特別分一章講 ret2libc。

## 對照 Linux NX（給背景）

你已經在 Linux pwn 碰過的 NX/DEP 概念，拿來對照能少走很多彎路：

| 面向 | Linux | Windows |
|---|---|---|
| **硬體機制** | 同一顆 NX bit（CPU 層完全相同） | 同一顆 NX bit |
| **標記方式** | ELF program header 的 `GNU_STACK` segment（有無 `E` flag） | PE `DllCharacteristics` 的 `NX_COMPAT` bit |
| **per-page 動態改保護的 API** | `mprotect(addr, len, PROT_EXEC)` | `VirtualProtect(addr, size, PAGE_EXECUTE_READWRITE, &old)` |
| **系統層開關granularity** | 通常整個系統一致（較少見「per-process 政策」這層） | 四級政策（OptIn/OptOut/AlwaysOn/AlwaysOff），per-process 還能再 opt-in/opt-out |
| **最常見 bypass 終點** | `ret2libc`／`mprotect` ROP | `VirtualProtect` ROP（Ch 29） |

最大的差異不在機制本身,而在 **Windows 的政策分層比 Linux 細得多**。這也是為什麼 OSED 考場你第一件事是用 mona 確認「這個目標到底有沒有開 DEP、開到哪一層」,而不是看到 Windows 就假設一定有、或假設一定沒有。

## 對比取捨：三種 DEP bypass 總覽（本章先列,Ch 29–30 展開）

| 繞過路徑 | 核心動作 | 前提 | 細節在 |
|---|---|---|---|
| **VirtualProtect ROP** | 把 shellcode 已在的頁改成可執行 | 知道 shellcode 的精確位址 | Ch 29 |
| **VirtualAlloc + 複製 shellcode** | 分配新的 RWX 頁，再把 shellcode 搬進去 | 不需要事先知道精確位址，但多一步複製 | Ch 30 |
| **WriteProcessMemory 到既有可執行頁** | 把 shellcode 寫進已經可執行的記憶體 | 需要找到可寫又可執行的頁（少見） | Ch 30 |

## 踩雷集錦

1. **以為 DEP 是「掃描內容抓 shellcode」**：完全不是。DEP 不檢查內容,只檢查頁的 NX bit。就算你把 shellcode 包成一百層加密、看起來完全不像 shellcode,只要它被放在 NX 頁上試圖執行,一樣被擋。別把 DEP 和防毒軟體的行為偵測搞混。

2. **假設 Windows 10 預設政策是 AlwaysOn**：Windows 預設是 **OptIn**,很多舊服務/第三方程式沒 `NX_COMPAT` 就直接沒 DEP 保護。OSED 考場遇到看起來很舊、沒重新編譯過的目標,先用 `!mona modules` 查一下再決定要不要上 ROP——如果根本沒開 DEP,硬寫 ROP chain 是白做工。

3. **忘記「ROP chain 本身也在 NX 頁上」這個事實,誤以為要先把 stack 改成可執行才能放 chain**：不需要。ROP chain 從頭到尾不是被「執行」的,是被 `ret` 一個個「讀出位址再跳過去」。stack 保持不可執行完全沒問題,chain 照樣能跑完。真正需要改成可執行的,只有最終的 shellcode 那一頁。

4. **把 Process DEP 和系統 DEP 政策混為一談**：改了 `bcdedit /set nx AlwaysOff` 不代表所有程式都失去 DEP 的「宣告」——PE 自己的 `NX_COMPAT` 旗標還在,只是系統層不強制套用而已。驗證目標狀態時兩層都要查。

5. **在練習時忘了重開機讓 bcdedit 生效**：`bcdedit /set nx` 改的是開機設定,不重開機不會套用到新啟動的程序,你會以為自己設錯了。

## 進階：再往深一層

- **DEP 與 Windows 更新的互動**：早期 Windows XP SP2 引入 DEP 時,很多舊軟體因為合法用到可執行 stack（例如某些 JIT、某些老式 trampoline）而相容性崩潰,這段歷史解釋了為什麼 Windows 選擇 **OptIn** 當預設政策,而不是像現代 Linux distro 那樣幾乎全面強制。了解這段背景,有助於理解「為什麼 Windows 的 DEP 政策這麼細」。
- **ACG（Arbitrary Code Guard）的進一步收緊**：Ch 29 寫出的 `VirtualProtect` ROP chain,在開了 ACG 的現代瀏覽器行程裡會直接失效——ACG 禁止把任何頁的保護動態改成可執行。OSED 的考試目標通常不會開 ACG（那是瀏覽器/OSEE 範疇）,但知道「VirtualProtect 不是萬能鑰匙」能幫你理解為什麼業界持續加碼緩解。`windows_pwn` Ch 36 有完整討論。

## 本章重點整理

- DEP 的硬體基礎是頁表 PTE 裡的 **NX bit**,CPU 在 instruction fetch 時檢查,不檢查內容——這是它比「掃描偵測」強的根本原因。
- Windows 的 DEP 分四層：硬體/BIOS → 系統政策（**OptIn/OptOut/AlwaysOn/AlwaysOff**，`bcdedit /set nx`）→ Process 層（PE 的 `NX_COMPAT`）→ 頁面層（`VirtualAlloc`/`VirtualProtect` 的 `PAGE_*`）。
- OSED 情境下,DEP 把「蓋 EIP 直接跳 shellcode」這條路堵死,逼 exploit 升級成「蓋 EIP 先跑 ROP → 呼叫 VirtualProtect 把 shellcode 頁改成可執行 → 再跳過去」。
- ROP chain 本身放在不可執行的頁上完全沒問題,因為它從未被當成指令執行——每個 gadget 位址指向的是既有的、合法標記為可執行的程式碼。
- Linux 的 NX/`mprotect` 和 Windows 的 NX/`VirtualProtect` 機制上是同一回事,Windows 多出來的複雜度主要在**政策分層**。

## 自我檢核

- [ ] 能說出 NX bit 在 PTE 裡大致的位置,以及 CPU 在什麼時機檢查它
- [ ] 能背出 Windows 系統層 DEP 的四種政策與各自行為差異
- [ ] 能解釋為什麼「ROP chain 放在 stack 上」不會被 DEP 擋下來
- [ ] 能講出 ret2libc 和 ROP 的差異,以及為什麼 Windows exploit dev 幾乎都要上 ROP 而不是純 ret2libc
- [ ] 遇到一個陌生目標,能說出第一步要查什麼才知道「要不要做 ROP」

## 延伸閱讀

- **[Data Execution Prevention — Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/memory/data-execution-prevention)**
  - 讀哪裡：DEP 概觀與 `bcdedit /set nx` 四種政策的官方定義段落。
  - 學什麼：本章「系統層四政策」表格的權威出處,政策名稱/行為以這裡為準。
  - 前提：無。

- **[VirtualProtect function — Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/nf-memoryapi-virtualprotect)**
  - 讀哪裡：參數表與 `PAGE_*` 常數定義（特別是 `PAGE_EXECUTE_READWRITE = 0x40`）。
  - 學什麼：Ch 29 動手組 ROP chain 前必查的參數語意,本章先建立印象。
  - 前提：無。

- **Corelan Team — "Exploit writing tutorial part 10: Chaining DEP with ROP"**（corelan.be）
  - 讀哪裡：開頭的 DEP 原理複習段落。
  - 學什麼：DEP 如何逼出 ROP 的經典敘事,和本章觀念一致、案例更生動。
  - 前提：Ch 7–10（stack overflow）。

- **本 repo `windows_pwn` [Ch 23 — DEP + ROP on Windows](../windows_pwn/23-dep-rop.md)**
  - 讀哪裡：全文,特別是「DEP 的 Windows 實作」與「x64 calling convention」段落。
  - 學什麼：同一套 DEP 機制的更深入版本（含 x64 ABI 的 shadow space/對齊細節）,補本章刻意省略的 x64 面向。
  - 前提：本章。

DEP 機制搞懂了,下一章進入真正動手的部分——去哪裡找 gadget、怎麼判斷一個 gadget 好不好用,以及怎麼規劃一條完整的 ROP chain。

→ [Ch 28 ROP gadget 尋找（mona rop / rp++）與 chain 規劃](./28-rop-gadget-finding.md)
