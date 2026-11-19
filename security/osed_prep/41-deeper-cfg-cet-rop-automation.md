# Ch 41 — 加深一圈 II：CFG/CET 衝擊 + ROP 自動化

> **目標**：誠實回答「OSED 教的經典技法，碰到現代緩解會發生什麼事」。拆清楚 CFG（Control Flow Guard）管什麼、不管什麼；CET（Control-flow Enforcement Technology）shadow stack 為什麼能從硬體層直接打爆傳統 ROP；在這個局面下，data-only 攻擊為什麼從冷門研究變成主流出路；以及 mona 之外的 ROP 自動化工具（angrop、ropium、ropper）分別適合什麼場景、侷限在哪。

> **環境**：本章延續 Ch 40 的定位——概念與對照性質，核心機制的技術細節已在 `windows_pwn` Ch 32（CFG）、Ch 35（CET shadow stack）本機能驗證的範圍內逐步建立過（MSVC 編譯、`dumpbin /loadconfig`、WinDbg 觀察等步驟在那兩章標「未實測」的地方，本章同樣標注，不重複宣稱已驗證）。ROP 自動化工具（angrop/ropium/ropper）的安裝與輸出範例標「未實測，理論預期」——本機未安裝這些工具，描述依官方 repo 文件整理。

---

## 為什麼需要這個？

你在 Part 7（Ch 27–30）學會了手工打造 VirtualProtect ROP chain，繞過 DEP。這套技法在 OSED 考場上無往不利——練習靶和考試環境都刻意保持「乾淨」，沒有 CFG、沒有 CET，你只需要處理 bad char 和 gadget 排列。

但你應該已經隱約感覺到一個問題：ROP 這招的核心假設是「`ret` 指令會乖乖從 stack 上讀 return address，不管那個值是不是被你改過」。這個假設聽起來像是一個明顯的設計漏洞——微軟和 Intel 不可能不知道。事實上他們確實在過去十年裡針對這個假設建了兩道防線：**CFG** 管住「間接呼叫/跳轉要跳到哪裡」，**CET shadow stack** 管住「`ret` 要跳回哪裡」。

這一章要把這兩道防線講到讓你能精確回答：
- 我的 ROP chain 在只開 CFG、沒開 CET 的系統上，還能不能用？（能——因為 CFG 不管 `ret`）
- 在開了 CET shadow stack 的系統上呢？（不能——第一個 `ret` 就會被硬體攔下來）
- 如果傳統 ROP 被堵死，exploit dev 還剩下什麼路？（data-only 攻擊）
- 手搭 ROP chain 太累，有沒有工具能自動化？除了 mona，還有哪些選項、各自的侷限是什麼？

這些答案決定了你離開 OSED 考場之後，面對一個現代 Windows 10/11 目標時，該把力氣放在哪裡。

## 先建立直覺：CFG 管「跳去哪」，CET 管「跳回哪」

控制流劫持可以拆成兩種方向，用一個簡單的類比理解：

```
  「跳去哪」（forward-edge）：                    「跳回哪」（backward-edge）：
  程式正常執行中，透過函式指標/vtable           函式執行完要「回到」呼叫它的地方
  主動跳到某個目標（call/jmp 間接跳轉）           （ret 指令，依賴 stack 上的 return address）

  正常情況：目標是合法函式入口                   正常情況：目標是呼叫者 call 之後的那條指令
  攻擊情況：攻擊者竄改函式指標，                 攻擊情況：攻擊者竄改 stack 上的 return
           跳到任意位址（shellcode/gadget）                address，跳到 ROP gadget

  CFG 防的就是這個方向 ──────────────────►      CET shadow stack 防的就是這個方向 ──────►
```

ROP（Return-Oriented Programming）的本質是**backward-edge** 攻擊——它完全靠竄改 return address 串起一條條 `ret`。所以一個只部署了 CFG（管 forward-edge）而沒有 CET shadow stack（管 backward-edge）的系統，**ROP 完全不受影響**。這是本章第一個、也是最容易被誤解的事實。

## 核心機制一：CFG 管什麼，不管什麼

### CFG 的保護範圍：只有間接呼叫/跳轉的目標

CFG 是 Windows 8.1 Update 3（2014）引入的機制，核心主張只有一句話：**每次間接呼叫/跳轉（`call [reg]`、`jmp [reg]` 這類目標存在暫存器或記憶體裡、執行期才決定去哪的指令）執行前，先查一張 bitmap，確認目標位址是程式裡確實存在的合法函式入口；不是就終止行程**。

編譯器（MSVC `/guard:cf`）在每個間接呼叫前插入檢查樁：

```asm
; 沒有 CFG 的間接呼叫
mov  rax, [rbp-8]      ; 讀取函式指標/vtable 指標
call qword ptr [rax]   ; 攻擊者控制 rax = 任意位址，直接跳

; 有 CFG 的間接呼叫
mov  rcx, qword ptr [rbp-8]
call qword ptr [__guard_check_icall_fptr]  ; 先查 bitmap
call rcx                                    ; 通過才真正跳
```

查的這張 bitmap，由連結器掃描所有編譯單元蒐集出的「合法間接呼叫目標」（`GuardCFFunctionTable`，放在 PE 的 Load Config 目錄）在行程啟動時建出來。

### CFG 完全不管 `ret`

CFG 的設計明確劃定了範圍：

```
  CFG 保護的：                           CFG 不保護的：
  ✅ indirect call（call [rax]）         ❌ ret 指令 → ROP 依然可行
  ✅ indirect jmp（jmp [rax]）           ❌ direct call/jmp（call 0x1234）
  ✅ vtable 呼叫                        ❌ 資料流（data-only attacks）
  ✅ 函式指標呼叫                        ❌ heap/stack 資料本身
```

這不是 CFG 的「漏洞」，是刻意的設計邊界——`ret` 指令讀的是 stack 上的值，保護它需要另一套完全不同的機制（第二個 stack），這正是 CET shadow stack 要填的洞。微軟自己的文件和多篇逆向研究都明確指出這個分工。

### 對 ROP 的實際結論

把這個結論套進你 Part 7 學的技法：**一個只開 CFG、沒開 CET 的系統上，你的 VirtualProtect ROP chain 完全照常運作**，因為整條 chain 的每一步跳轉都是 `ret`，CFG 連檢查都不會檢查。CFG 唯一會攔下來的，是如果你的 exploit 試圖透過竄改一個函式指標/vtable（走 `call [reg]` 這條路）來起始攻擊——那種情況下，CFG 的 bitmap 會擋下任意位址，逼你只能跳到「bitmap 裡存在的合法函式入口」（這仍然留了一條窄路，`windows_pwn` Ch 33 整章在拆這條路要怎麼走）。

> **未實測，理論預期**：若你有 MSVC 環境，用 `/guard:cf` 編一個有虛擬函式呼叫的程式，再搭配一條純 `ret` 構成的 ROP chain 去打它的 stack overflow，CFG 檢查樁完全不會觸發，`ret` 鏈正常執行到你排定的 gadget——這是本節結論的直接驗證方法，`windows_pwn` Ch 32 有同樣結論的本機可驗證步驟描述（該章同樣因無 MSVC 環境標注「未實測」）。

## 核心機制二：CET shadow stack——直接打爆傳統 ROP

### ROP 的成立前提

精確描述一次 ROP 能成立的條件：

1. 攻擊者能寫入一段連續的 stack 記憶體（overflow / 任意寫原語）
2. 寫入範圍蓋到了某個 `call` 指令壓入的 return address
3. `ret` 指令執行時，CPU 從 `RSP`（或 x86 的 `ESP`）讀 return address，跳過去——**不做任何驗證**
4. 攻擊者把這個值改成 gadget 位址，gadget 執行完再自己 `ret`，串起整條 chain

第 3 點是關鍵：**CPU 原生對 `ret` 的行為沒有任何完整性檢查**。CET shadow stack 就是直接在硬體層把這個檢查補上。

### shadow stack 怎麼運作

Intel CET（Control-flow Enforcement Technology）引入一個新的硬體暫存器 **SSP（Shadow Stack Pointer）**，維護一份**只有 CPU 特定指令能寫入**的第二份 return address 記錄：

```
正常 stack（RSP 管理）：                    shadow stack（SSP 管理，硬體維護）：
┌─────────────────────────────┐            ┌─────────────────────────────┐
│ return address = 0x7ff8...  │◄── RSP      │ 0x7ff8...（同一個值的副本） │◄── SSP
│ 函式局部變數                 │            │（一般 store 指令無法寫入；  │
│ ...                          │            │  只有 call/ret/WRSS 等      │
└─────────────────────────────┘            │  特定指令能操作）            │
                                            └─────────────────────────────┘

call 指令執行：                             ret 指令執行：
  RSP -= 8；寫入 return address             ret_addr_rsp = *(RSP)；RSP += 8
  SSP -= 8；同步寫入同一個值                ret_addr_ssp = *(SSP)；SSP += 8
                                             if (ret_addr_rsp != ret_addr_ssp):
                                                 #CP exception → 行程終止
                                             else:
                                                 跳到 ret_addr_rsp
```

攻擊者竄改了 `RSP` 上的 return address（你的 overflow 本來就是這麼做的），但 shadow stack 裡的副本完全沒被動到——一般的 `mov [addr], val`、`memcpy`、stack overflow 都碰不到它，因為 shadow stack 所在的記憶體頁在 page table 層被標記為「一般 store 不可寫」。兩邊一比對不一致，CPU 立刻丟出 `#CP`（Control Protection Exception），行程終止。

**你的 ROP chain 在第一個 `ret` 就會被攔下來，連第一個 gadget 都執行不完。**

> **未實測，理論預期**：CET shadow stack 需要第 11 代 Intel（Tiger Lake, 2020）以上或 AMD Zen 3 以上的 CPU，加上 Windows 10 2004（20H1）以上、程式用 `/CETCOMPAT` 連結或系統政策強制開啟。本機硬體/軟體環境是否滿足需自行確認（`wmic cpu get caption` 或 `!cpuid`），`windows_pwn` Ch 35 有完整的硬體需求表與驗證方法，均標注「未實測」等待讀者在對應硬體上驗證。

### CFG + CET 疊加之後，傳統技法的處境

把 Ch 40 的 x64 ABI 差異、本章的 CFG/CET 放在同一條時間線上看：

```
Part 7–8 教的路線（DEP + ASLR，OSED 考試環境）：
  overflow → info leak（如果需要） → ROP chain（VirtualProtect） → shellcode
  ────────────────────────────────────────────────────────────────
  完全可行，因為考試環境沒有 CFG/CET


加上 CFG（但沒有 CET）：
  overflow → ROP chain（CFG 不查 ret） → VirtualProtect → shellcode
  ────────────────────────────────────────────────────────────────
  依然完全可行——這是本章最重要的單一結論


加上 CFG + CET shadow stack：
  overflow → 第一個 ret 執行 → shadow stack 比對失敗 → #CP → 行程終止
  ────────────────────────────────────────────────────────────────
  ROP 整條死透，連第一步都走不到
```

這條時間線直接說明了：**CFG 不是 OSED 技法的威脅，CET shadow stack 才是**。如果你在規劃「學完 OSED 之後要優先補什麼」，CET 對經典 ROP 技法的衝擊，重要性遠高於 CFG。

## 核心機制三：IBT 與傳統跳板技法

CET 的第二個機制 **IBT（Indirect Branch Tracking）** 和 CFG 的保護對象重疊（都是 forward-edge），但實現方式不同：IBT 要求所有間接跳轉的目標必須是一條 `ENDBR64`/`ENDBR32` 指令，CPU 內部用一個 TRACKER 狀態機強制檢查，不是軟體插樁比對 bitmap，是硬體直接檢查下一條指令的 opcode。

這對 Ch 9 教的 `jmp esp` 跳板技法有什麼影響？如果你蓋 return address 讓它先跳到一個 `jmp esp` 指令（這本身是透過 `ret` 跳過去，不是 indirect call/jmp，IBT 不檢查這一步），IBT 本身不會攔住這個跳板；但如果你的 exploit 路線裡有任何一步是真正的**間接呼叫/跳轉**（例如你的 shellcode 裡有 `call [rax]`），那一步就要求目標是 `ENDBR64`，否則同樣觸發 `#CP`。IBT 和 shadow stack 是兩個獨立的子系統，分別鎖住 forward-edge 和 backward-edge，可以各自開關。

## 繞過概念：CFG/CET 之下還能走哪條路

### CFG 繞過的概念性方向（細節見 `windows_pwn` Ch 33）

CFG 的 bitmap 允許跳到「任何合法函式入口」，這留下三個思路：

1. **跳到一個合法但危險的函式**（例如某個內部會執行任意位址的 helper 函式），讓它幫你完成本來做不到的事；
2. **利用 CFG 的粒度**（bitmap 以 8 bytes 為單位，只檢查「是不是函式入口」，不檢查「呼叫方是不是真的該呼叫這個函式」）——合法集合數量龐大，只要找到一個「被允許、剛好有用」的目標，CFG 的驗證就形同虛設；
3. **export suppression 之類的補強機制出現前的歷史漏洞**——微軟持續在堵這類路線，新版 Windows 的 suppressed export 機制就是對應的補丁。

這些都是**軟體層**的繞過，建立在「CFG 只驗證位址合法性，不驗證語義合理性」這個根本侷限上。

### CET 之下真正剩下的大路：data-only 攻擊

CFG 的繞過路線再怎麼巧妙，最終都還是在「竄改控制流」這個框架裡打。CET shadow stack 把 ROP 這條路堵死之後，再加上 CFG/XFG 限縮了函式指標竄改的空間，**攻擊者如果堅持要竄改控制流，選項已經少到極限**。

剩下的大路是完全不碰控制流的 **data-only 攻擊**：

```
  data-only 攻擊的本質：
  ──────────────────────────────────────────────────────
  不改任何函式指標、不 ROP、不跳到任何非預期的程式碼位址，
  只竄改程式「正常執行路徑」會讀到的資料：

  - 把一個 struct 裡的 is_admin 欄位從 0 改成 1 → 繞過權限檢查
  - 把一個 length 欄位改大，讓程式自己的 memcpy 複製超出範圍
  - 把一個函式指標旁邊、邏輯上會影響分支判斷的旗標位元改掉
  - 篡改堆上一個物件的某個資料欄位，讓後續的合法呼叫做出不同決策
```

因為整個攻擊過程中控制流完全走在程式設計者預期的路徑上（沒有任何 `call`/`jmp`/`ret` 跳到非預期位址），CFG、XFG、CET shadow stack、CET IBT 這整套緩解體系**對 data-only 攻擊完全視而不見**——它們全部是控制流完整性（CFI）機制，而 data-only 攻擊根本不碰控制流。

這不是一個繞過技巧，是一個**攻擊思維的轉移**：從「我要讓 CPU 執行我的程式碼」變成「我要讓程式自己的程式碼，用錯誤的資料做出對我有利的決策」。`windows_pwn` Ch 35 結尾、Ch 37 整章系統性處理這個轉移，是 CET 時代漏洞研究的主戰場。

## ROP 自動化工具：mona 之外的選擇

OSED 考試教你用 mona（`!mona rop`）產生 ROP chain 草稿。這在考試的乾淨 x86 環境夠用，但走出考場，面對更複雜的目標（x64、gadget 稀缺、需要繞過額外檢查），其他工具各有定位：

### angrop（基於 angr 的符號執行）

**是什麼**：`angrop` 是建立在 [angr](https://angr.io/)（符號執行框架）之上的 ROP chain 自動生成工具。它不只是「掃描 gadget 清單」，而是用符號執行理解每個 gadget 真正對暫存器/記憶體做了什麼效果，再用約束求解拼出滿足你指定目標（例如「RDI=X 且 RSI=Y 且呼叫某個位址」）的 chain。

**何時用**：目標的 gadget 生態複雜、單純字串比對找不到乾淨的 `pop reg; ret`，但有很多「副作用複雜」的 gadget（例如一條指令同時動了三個暫存器）時，angrop 能利用符號執行找出 mona 式的樸素掃描找不到的組合。

**侷限**：符號執行開銷大，對大型二進位（像完整的 `ntdll.dll`）分析時間可能明顯變長；angrop 設計初期以 Linux ELF 為主要場景，對 PE 格式與 Windows calling convention 的支援程度要實測確認，不能假設和 Linux 下的體驗一致。

> **未實測，理論預期**：angrop 在 Windows PE 目標上的實際可用性（gadget 提取、chain 生成品質）本機未安裝驗證，以上描述依官方 GitHub repo（`angr/angrop`）文件整理。

### ropium

**是什麼**：以暫存器/記憶體狀態變化為目標的 ROP chain 生成工具，核心訴求類似「我要讓 RAX 變成某個值，順便讓 [某個記憶體位址] 被寫入另一個值」這類多重約束，用更輕量（非完整符號執行）的方式求解。

**何時用**：介於「mona 的純粹字串掃描」和「angrop 的完整符號執行」之間——需要處理多個同時成立的暫存器約束，但又不想付出完整符號執行的效能代價。

**侷限**：對目標二進位的格式與架構支援範圍要查當下文件確認；和 angrop 一樣，不是 OSED/mona 生態圈原生的工具，拿到 Windows PE 目標上用之前，先用小範例驗證它確實能正確解析你的目標格式。

> **未實測，理論預期**：以上描述依 ropium 官方 GitHub repo 文件整理，本機未安裝實測。

### ropper

**是什麼**：一個通用的 gadget 搜尋與 ROP chain 輔助工具，支援 ELF/PE/Mach-O 等多種格式，功能定位接近「跨平台版的 mona rop 功能」——快速列出符合條件的 gadget（依暫存器、指令數、是否含壞字元過濾），但不像 angrop/ropium 做完整的符號執行約束求解。

**何時用**：你已經大致知道要湊哪些 gadget（例如延續 Ch 40 的「要找 `pop rcx`/`pop rdx`/`pop r8`/`pop r9`」），只需要快速掃出候選清單並篩選壞字元/位址範圍，ropper 的查詢語法比在 WinDbg 裡手動 `s` 搜尋更有效率。

**侷限**：和 mona 一樣是「搜尋 + 列舉」工具，不做語義層面的約束求解，複雜的多暫存器依賴仍然要你自己手動拼。

### 三者與 mona 的定位對照

| 工具 | 核心能力 | 適合場景 | 相對 mona 的差異 |
|---|---|---|---|
| **mona（`!mona rop`）** | 字串/模式比對掃 gadget，簡單規則拼 chain | OSED 考試環境、乾淨 x86 目標、標準 API（VirtualProtect 等）| 基準線，OSED 官方工具鏈一部分 |
| **ropper** | 跨格式（PE/ELF/Mach-O）gadget 搜尋與篩選 | 需要快速掃描、跨平台、篩選條件多（壞字元/暫存器/長度） | 搜尋能力更通用，但不做約束求解 |
| **ropium** | 多重暫存器/記憶體狀態約束求解（非完整符號執行） | gadget 生態複雜但不到需要完整符號執行的程度 | 多了約束求解，換取額外計算成本 |
| **angrop** | 完整符號執行 + 約束求解 | gadget 副作用複雜、mona/ropper 的樸素掃描找不到解 | 最強的求解能力，but 開銷最大、對 PE 支援度需自行驗證 |

### 指令風格對照：從 mona 到 ropper

為了讓「換工具」這件事不只是概念，對照一下同樣「找 `pop rcx; ret` 型 gadget」這個任務在不同工具的指令風格差異：

```
mona（WinDbg 內，考場標準工具）：
  !mona rop -m "ntdll,kernelbase" -cp nonull -n
  （-cp nonull 篩掉含 null byte 的 gadget，-n 不要求無其他壞字元）

ropper（獨立 CLI 工具，未實測，依官方文件語法）：
  ropper --file ntdll.dll --search "pop rcx# ret"
  （用 `#` 分隔指令序列做模式搜尋，輸出候選位址清單）

angrop（Python API，未實測，依官方文件語法）：
  rop = angrop.Analysis(proj)
  chain = rop.rop.set_regs({"rcx": 0x1000, "rdx": 0x2000})
  （不是下指令找 gadget，而是描述「我要的最終暫存器狀態」，
   由求解器自己決定湊哪些 gadget）
```

三者的本質差異在這裡看得最清楚：mona/ropper 是「你描述 pattern，工具列出符合的位址」，角色接近進階版的 `grep`；angrop 是「你描述目標狀態，工具自己求解路徑」，角色更接近約束求解器。兩種思路沒有絕對優劣，取決於你對目標的 gadget 生態了解到什麼程度——如果你已經知道要哪些 gadget、只是嫌手動搜尋麻煩，用前者；如果你連「湊不湊得出這個狀態」都不確定，用後者讓工具幫你探索。

> **未實測，理論預期**：以上 ropper/angrop 指令語法依官方 GitHub repo README 整理，具體參數名稱和輸出格式可能隨版本變動，使用前請對照當下版本的官方文件。

### 何時該用自動化，何時該手搭

OSED 的訓練哲學（Part 4 反覆強調「逆向優先於 PoC」）同樣適用在 ROP 自動化工具上：**自動化工具產的 chain 草稿必須手動驗證**，無論是 mona 還是 angrop。原因不是工具不可靠，是這些工具對「對齊」「shadow space」「API 內部副作用」的建模都有簡化，複雜目標上常常需要你自己在 WinDbg 裡單步走一次確認。工具的價值是**節省搜尋 gadget 清單的體力勞動**，不是取代你對 chain 正確性的判斷責任。

## 驗證法：拿到一個真實目標，怎麼確認它開了哪些緩解

延續 Ch 40 的方法論精神——理論講完要落地成「拿到陌生目標的第一步檢查清單」。以下是確認一個目標的 CFG/CET 部署狀態的具體步驟：

1. **先看 PE Load Config 的 GuardFlags**：
   ```
   dumpbin /loadconfig target.exe
   ```
   找 `Guard Flags` 欄位，`CF Instrumented` 出現代表編譯期插了 CFG 樁；`Guard CF Function Table` 的筆數非零代表有合法目標表。看到這個欄位全空，大致可以判斷這個 binary 沒有開 CFG（但系統 DLL 如 `ntdll`/`kernelbase` 通常是開的，要分開確認目標 EXE 本身和它載入的系統模組）。

2. **用 Process Hacker 或 PowerShell 看執行期的緩解政策**：
   ```powershell
   Get-ProcessMitigation -Name target.exe
   ```
   關注 `CFG`、`UserShadowStack`、`UserShadowStackStrictMode` 這幾個欄位的布林值。這一步比單看 PE 檔案的 Load Config 更準確,因為系統政策（WDEG）可能在**沒有重編**的情況下對一個行程強制啟用（或停用）這些保護。

3. **確認硬體是否支援 CET**：
   ```
   wmic cpu get caption
   ```
   對照 Intel 第 11 代（Tiger Lake）/ AMD Zen 3 以上才有 shadow stack 硬體；第 12 代（Alder Lake）/ Zen 4 以上才有 IBT。硬體不支援，軟體政策再怎麼設都是空談。

4. **用 WinDbg 在 ROP chain 執行前主動確認會不會撞牆**：
   ```
   sxe cp          ; 讓 WinDbg 在 #CP exception 時主動中斷（需 CET 硬體與驅動支援）
   ```
   如果你的 ROP chain 一執行就在第一個 `ret` 附近觸發中斷，而不是正常跑到你排定的 gadget，這就是 shadow stack 在起作用的直接訊號——比盯著暫存器值猜測更快確認問題根源。

> **未實測，理論預期**：上述四步驟中，`dumpbin`/`Get-ProcessMitigation` 的語法來自官方文件，`sxe cp` 的具體行為需要 CET 硬體與對應驅動支援才能驗證，本機環境未確認是否齊備，使用前請先完成步驟 3 的硬體確認。

## 底層機制：三道防線各自攔截的時間點

把 CFG、CET IBT、CET shadow stack 三道機制放進同一條攻擊時間軸：

```
攻擊者的完整路線（假設有一個函式指標竄改 + 後續 ROP 的複合攻擊）：

  [漏洞原語觸發]
       │
       ▼
  竄改函式指標，準備間接呼叫 ──────► CFG 查 bitmap
       │                              │
       │                              ├─ 目標不在 bitmap → #CP（RaiseFailFastException）→ 行程終止
       │                              └─ 目標在 bitmap（合法但可能危險）→ 繼續
       ▼
  間接跳轉執行 ──────────────────────► CET IBT 查 TRACKER
       │                              │
       │                              ├─ 目標不是 ENDBR64 → #CP → 行程終止
       │                              └─ 目標是 ENDBR64 → 繼續
       ▼
  跳到的函式本身觸發一串 ROP（ret 鏈）
       │
       ▼
  第一個 ret 執行 ────────────────────► CET shadow stack 比對 SSP vs RSP
       │                              │
       │                              ├─ 不一致 → #CP → 行程終止   ← ROP 死在這裡
       │                              └─ 一致（沒被竄改）→ 正常返回
```

三道防線各自守在攻擊鏈的不同環節，**任何一道擋下來，整條攻擊鏈就終止**。這也是為什麼「CFG + CET 全開」被認為幾乎封死傳統控制流劫持——不是單一機制多強，是三道防線疊加後，攻擊者要同時繞過全部才能走到程式碼執行。

## 對比取捨：OSED 乾淨環境 vs 現代緩解全開

| 面向 | OSED 考試環境（Part 2–9 的練習靶） | 現代緩解全開（CFG + XFG + CET SS + CET IBT） |
|---|---|---|
| **DEP** | 開（Part 7 教你繞） | 開（視為常態） |
| **ASLR** | 開（Part 8 教你繞） | 開，entropy 更高（尤其 x64） |
| **SafeSEH** | 部分關（Part 3 教你繞）| 不適用於 x64（SEH 本身是 table-based）|
| **CFG** | 關 | 開——限縮間接呼叫/跳轉目標為合法函式入口 |
| **CET shadow stack** | 關 | 開——**直接打爆 ROP**，backward-edge 完全封鎖 |
| **CET IBT** | 關 | 開——限縮間接跳轉目標為 ENDBR64 |
| **傳統 ROP 可行性** | 完全可行（核心技法） | 不可行（第一個 ret 即終止）|
| **函式指標竄改可行性** | 完全可行 | 受限（CFG bitmap，進一步受 XFG 型別限縮）|
| **主要可行攻擊路線** | 控制流劫持（ROP/SEH/shellcode）| data-only 攻擊 |
| **ROP 自動化工具夠不夠用** | mona 足夠 | 需要更強的 gadget 語義分析（angrop 等），但即使找到 gadget，shadow stack 開著也用不上 |

這張表的重點不是「現代緩解讓考試變沒用」——OSED 教的技法是地基，沒有它你連「為什麼 CET 要防 ROP」都聽不懂。重點是校正期望：**拿到 OSED 證書後，碰到一個現代 Windows 11 目標，你需要的不是「更強的 ROP 技巧」，而是「换一套攻擊思維」**。

## 踩雷集錦

1. **「CFG 開了，ROP 就不能用了」**：錯，這是整章最該糾正的誤解。CFG 完全不檢查 `ret`，ROP 在只有 CFG、沒有 CET 的系統上照常可行。真正打爆 ROP 的是 CET shadow stack。

2. **「CET 和 CFG 是同一個東西的兩個名字」**：不是。CFG 是微軟主導的軟體機制（編譯器插樁 + OS loader 管理 bitmap），CET 是 Intel 主導的硬體 ISA 擴充（shadow stack + IBT），Windows 透過 `/CETCOMPAT` 把 CET 整合進系統，兩者是**設計者不同、機制層不同、互補而非重疊**的兩套系統。

3. **「只要硬體支援 CET，所有程式都自動受保護」**：不對。CET shadow stack 需要程式用 `/CETCOMPAT` 連結，或 OS 透過系統政策（`Set-ProcessMitigation`）強制開啟。沒有重新編譯、也沒有政策強制的舊程式，即使跑在支援 CET 的硬體上，照樣沒有 shadow stack 保護——這也是為什麼真實紅隊評估裡，「這台機器的 CPU 支援 CET」和「這個目標進程真的開了 shadow stack」是兩件要分開確認的事。

4. **「用 angrop/ropium 一定比 mona 找到更好的 chain」**：不一定。這些工具的求解能力更強,但代價是計算開銷更大、對 Windows PE 這類非主場格式的支援程度需要實測確認,簡單目標上 mona 的樸素掃描反而更快出結果。先判斷目標的 gadget 生態複雜度,再決定要不要上重型工具。

5. **「CFG 繞過了，CET 開著也沒差，反正都繞過了」**：錯誤的疊加邏輯。CFG 和 CET IBT 管的是 forward-edge（間接呼叫/跳轉的目標），CET shadow stack 管的是 backward-edge（`ret` 的目標），是**三個獨立的檢查點**。繞過其中一個不會讓另外兩個失效——真實目標若三者全開，你要分別應付每一個，不能假設「繞過一個等於全部繞過」。

6. **「PE 的 Load Config 沒有 CFG 旗標，這個目標就一定沒有 CFG 保護」**：不完全對。EXE 本身可能沒開 `/guard:cf`，但它載入的系統 DLL（`ntdll.dll`、`kernelbase.dll`）在現代 Windows 上幾乎都是開的。CFG 的保護是**per-module**的，一個進程裡可能同時存在「有保護的系統模組」和「沒保護的應用程式自己的程式碼」。你要跳去哪個模組的哪個函式，決定了那一步會不會撞上 CFG 檢查——不能用「這個 EXE 沒開」一概而論整個進程的狀態。

## 進階：再往深一層

- **XFG（型別限縮版 CFG）對 data-only 的間接影響**：XFG 用型別 hash 進一步限縮合法間接呼叫目標的集合，讓「跳到合法但危險的函式」這條 CFG 繞過路線更難走——這進一步把攻擊者推向 data-only，`windows_pwn` Ch 34 整章討論 XFG 的 hash 機制與粒度。
- **Return Flow Guard（RFG）的歷史**：微軟在 CET 硬體普及前，曾實驗性地用純軟體方式做過 return address 保護（RFG，Return Flow Guard），後來被 CET 硬體機制取代/棄用。了解這段歷史有助於理解「軟體模擬 shadow stack」為什麼效果有限（純軟體方案本質上還是能被某些記憶體寫入原語碰到，不像硬體頁面保護那麼乾淨）。
- **JIT 引擎與 CET 的相容性問題**：瀏覽器的 JIT 編譯器動態生成程式碼，天然和 CET shadow stack 的呼叫慣例假設有摩擦（JIT code 的 `call`/`ret` 配對關係不像傳統編譯產物那麼規律），這是 `browser_pwn` 方向會深入的議題，本課先知道有這個摩擦點即可。
- **data-only 攻擊的下一步學習路徑**：本章只建立了「為什麼 data-only 是現代緩解下的主戰場」這個結論，真正系統性的 data-only 攻擊方法論（控制流劫持以外如何靠純資料竄改達成任意程式碼執行或權限提升）在 `windows_pwn` Ch 37 完整展開，是你讀完本課、`windows_pwn` 之後最該優先補的一塊。

## 本章重點整理

- **CFG 只管間接呼叫/跳轉的目標合法性（forward-edge），完全不檢查 `ret`**——你的傳統 ROP chain 在只開 CFG、沒開 CET 的系統上完全照常運作，這是本章最重要、最常被誤解的結論。
- **CET shadow stack 從硬體層維護一份攻擊者碰不到的 return address 副本**，`ret` 執行時比對不符就 `#CP` 終止行程——這才是真正打爆傳統 ROP 的機制，ROP chain 在第一個 `ret` 就會死。
- **CET IBT 要求間接跳轉目標必須是 `ENDBR64`/`ENDBR32`**，用硬體 TRACKER 狀態機限縮 gadget 集合，和 CFG 同屬 forward-edge 但實現方式不同（硬體 vs 軟體 bitmap）。
- **CFG + XFG + CET 全開之後，傳統控制流劫持幾乎被封死，data-only 攻擊（竄改資料而非控制流）變成主流出路**——這是攻擊思維的轉移，不是一個繞過技巧。
- **ROP 自動化工具**：mona 適合 OSED 考試環境的樸素掃描；ropper 提供跨格式的通用 gadget 搜尋；ropium/angrop 用（輕量或完整）約束求解處理複雜的多暫存器依賴，代價是計算開銷更大、對 PE 格式的支援度需自行驗證。

## 自我檢核

- [ ] 能準確回答「CFG 開了，我的 ROP chain 還能不能用」，並說明理由（CFG 不檢查 ret）
- [ ] 能畫出 shadow stack 如何在硬體層攔截被竄改的 return address（一般 stack 被改、shadow stack 完整、ret 時 #CP）
- [ ] 能說出 CFG、CET IBT、CET shadow stack 分別守住攻擊鏈的哪一個環節，以及為什麼繞過一個不代表其他兩個也失效
- [ ] 能解釋為什麼 CFG/CET 這整套機制對 data-only 攻擊完全無效（從「它們檢查什麼」的定義回答）
- [ ] 能說出 mona、ropper、ropium、angrop 四者在求解能力與適用場景上的排序與取捨
- [ ] 面試被問「現代緩解全開之後,OSED 教的技法還有用嗎」，能給出不是簡單「有用」或「沒用」的、有層次的回答

## 延伸閱讀

- **本 repo [`windows_pwn` Ch 32 — CFG (Control Flow Guard) 原理](../windows_pwn/32-cfg.md)**
  - **讀哪裡**：全章，特別是「CFG 的範圍限制：只保護 forward-edge」一節
  - **學什麼**：CFG bitmap 查找演算法、PE Load Config 結構、`LdrpValidateUserCallTarget` 的完整技術細節；本章只取結論，這裡有逐步推導
  - **前提**：本章 CFG 段落讀完即可

- **本 repo [`windows_pwn` Ch 35 — Intel CET / shadow stack on Windows](../windows_pwn/35-cet-shadow-stack.md)**
  - **讀哪裡**：全章，特別是「CET + CFG/XFG 組合：『傳統控制流劫持』幾乎被堵死」與「CET 的限制與繞過考量」兩節
  - **學什麼**：shadow stack 的硬體實作細節（page table 保護位、SSP 暫存器、`INCSSP`/`RSTORSSP` 等指令）、CET 的殘存攻擊面；本章的 CET 段落是這一章的濃縮
  - **前提**：本章讀完即可，有 x64 ABI 基礎（Ch 40）更佳

- **[Control Flow Guard — Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/secbp/control-flow-guard)**
  - **讀哪裡**：「How does CFG work」一節
  - **學什麼**：CFG 設計動機與系統整合方式的官方一手說明
  - **前提**：本章讀完即可

- **Intel® 64 and IA-32 Architectures Software Developer's Manual, Volume 1, Chapter 17：「Control Flow Enforcement Technology (CET)」**
  - **讀哪裡**：Section 17.2（Shadow Stack）、17.3（IBT）
  - **學什麼**：CET 的硬體規格一次文件，本章所有 shadow stack 機制描述的最終權威來源
  - **前提**：x86-64 暫存器模型基礎

- **[angr/angrop — GitHub](https://github.com/angr/angrop)** 與 **[ropper — GitHub](https://github.com/sashs/Ropper)**
  - **讀哪裡**：兩個 repo 的 README 與使用範例段落
  - **學什麼**：ROP 自動化工具除了 mona 之外的具體操作方式與輸出格式；裝好後可以在自己的練習靶上和 mona 的 chain 草稿做對照
  - **前提**：本課 Part 7（Ch 27–30）完成，熟悉手搭 ROP chain 的流程再用工具會更清楚它省了你哪些步驟

CFG 管得住間接呼叫的目標、CET shadow stack 管得住 `ret` 的返回位址——這兩道防線疊起來，幾乎把本課 Part 2–9 教的「控制流劫持」核心打法封死在現代 Windows 上。但這不代表漏洞研究走到了終點：OSED 給你的是看懂、也能自己構造這整套攻擊鏈的能力，這個能力本身會遷移到 data-only 攻擊、也會遷移到下一步要補的 x64 現代緩解對抗。現在，把所有章節學到的東西放進一場完整的模擬考裡驗收。

→ [Final Project](./final-project-osed-mock-exam.md)
