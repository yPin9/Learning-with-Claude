# Ch 2 — 從 Linux pwn 到 OSED：你會什麼、缺什麼

> **目標**：把你已有的 Linux userland pwn 能力做一次「能力遷移對照」——哪些概念直接搬得動、哪些名詞換了但骨架一樣、哪些是 Windows/OSED 世界全新要補的。讀完這章你該能看著任何一個 Windows exploit dev 主題，立刻在腦中接上對應的 Linux 概念，加速學習。

## 為什麼要先做這次對照

Linux pwn 和 Windows exploit dev 解決的是同一類問題——「程式沒檢查邊界，你能不能把它變成執行你的程式碼」——但兩邊的作業系統生態、工具鏈、文化，差異大到如果不先做一次系統性對照，你會不斷在「這個我明明會，為什麼卡住」和「這個全新概念到底是什麼」之間反覆橫跳，浪費大量時間在分不清這題到底是「換個名詞」還是「真的沒學過」。

先建立一個心智模型：把你的 Linux pwn 知識想成一套**已經組裝好的工具箱**。搬到 Windows/OSED 戰場，不是把工具箱整個丟掉重買，而是：

```
┌─────────────────────────────────────────────────────────┐
│                你的 Linux pwn 工具箱                      │
│                                                           │
│  [可直接用]          [要換握把才能用]      [箱子裡沒有，要新買]│
│  ROP 思維            stack 佈局細節         SEH 利用        │
│  cyclic pattern      壞字元概念             WinDbg 指令集    │
│  shellcode 概念      呼叫慣例(cdecl→stdcall) PEB/TEB 走訪    │
│  stack overflow 直覺  ELF→PE 的結構對應      egghunter 文化  │
└─────────────────────────────────────────────────────────┘
```

本章接下來逐條拆解這三類。

## 大對照表：能力遷移盤點

這是本章的核心，一張表走完 OSED 需要的全局能力盤點。「遷移度」分三級：**高**（概念幾乎一樣，換個名詞或工具就能用）、**中**（骨架相同但細節差異大，需要重新建立肌肉記憶）、**低**（Windows/OSED 特有，Linux 世界沒有對應物，要當全新主題學）。

| 主題 | Linux 世界 | Windows / OSED 世界 | 遷移度 | 說明 |
|---|---|---|---|---|
| Stack overflow 直覺 | 覆寫 return address 控 `RIP`/`EIP` | 覆寫 return address 控 `EIP`（OSED 全程 32-bit） | **高** | 機制完全一樣：函式 epilogue 的 `ret` 從 stack 彈回位址當目的地。本課 Ch 7。 |
| Cyclic pattern 找 offset | `pwntools cyclic` / `pattern_create.rb` | mona 的 `!mona pc <len>` / `!mona findmsp` | **高** | 同一套手法換工具產出，mona 甚至是同一系譜（Metasploit pattern 概念延伸）。Ch 7–8。 |
| 壞字元（bad characters） | 協定/解析器對特定 byte 敏感（如 `\x00` 截斷字串） | 概念完全相同，Windows 服務一樣要排除會被協定吃掉的 byte | **高** | 分析方法一樣：全 256 byte 送進去，WinDbg dump 記憶體找哪些 byte 被改掉或造成截斷。Ch 8。 |
| Shellcode 概念 | 一段自給自足的 machine code，呼叫 syscall 拿 shell | 概念相同，但**沒有 syscall 可以直接裸用**——要呼叫 Win32 API（`WinExec`/`CreateProcess`） | **中** | 思路一樣（寫 raw bytes 執行任意操作），**取得 API 位址的方式整個不同**（Linux 直接 syscall number；Windows 要動態解析 `kernel32` 匯出函式）。Ch 22–25。 |
| ROP（Return-Oriented Programming） | 串 libc/binary 裡的 gadget 繞過 NX | 串 `kernel32`/`ntdll` 等模組裡的 gadget 繞過 DEP | **高（思維）/中（細節）** | 核心思維完全遷移：找 `pop; ret` 設暫存器、串 gadget 達成任意呼叫。差異在**呼叫慣例**（stdcall 的 `ret N` 會多清棧，Ch 3 會細講）和目標函式（`VirtualProtect`/`VirtualAlloc` 取代 `mprotect`/`execve`）。Ch 27–30。 |
| 例外處理利用面 | 沒有直接對應物（Linux 的 signal/exception handling 不是常見攻擊面） | **SEH（Structured Exception Handling）overwrite** 是獨立一大類漏洞，`fs:[0]` 指向 SEH chain | **低** | 這是 Linux pwn 背景完全沒碰過的攻擊面：覆寫 exception handler 指標，讓例外觸發時執行你控制的位址。整個 Part 3（Ch 11–13）專講。 |
| 動態分析/偵錯器 | `gdb` + `pwndbg`/`GEF`，`/proc/self/maps` 查記憶體佈局 | `WinDbg`，查記憶體佈局要用 `!address`、`lm`，沒有 `/proc` 這種檔案系統便利介面 | **中** | 指令集完全不同（下方有專門對照表），但「設斷點、看暫存器、dump 記憶體、看 call stack」這些**操作目的**一樣。Ch 4–5。 |
| 行程記憶體結構 | ELF + `/proc/pid/maps`，没有對應 PEB 的集中結構 | PE 格式 + **PEB（Process Environment Block）/ TEB（Thread Environment Block）** | **低（結構）/中（目的）** | Windows 把「這個行程的環境資訊、模組列表」集中在 PEB 這個使用者模式可讀的結構，shellcode 常靠走訪 PEB 找 `kernel32` 基址——Linux 沒有這種單一集中結構可以這樣用。Ch 6、Ch 23。 |
| 呼叫慣例 | 主要是 System V AMD64 ABI（x64）；32-bit 時代也有 cdecl | **cdecl / stdcall / fastcall / thiscall 並存**，Win32 API 多為 stdcall | **中** | x64 Linux 背景的人對「callee 負責清棧」這件事通常沒概念——stdcall 的 `ret N` 會直接影響你 ROP chain 怎麼排。Ch 3 整章講。 |
| Format string 漏洞 | `printf` 系列 `%n` 寫記憶體，glibc 下有成熟利用鏈 | 概念相同（`%n` 寫記憶體），但 Windows CRT 的實作細節、常見觸發場景、保護機制不同 | **中** | 攻擊原語一樣（可控格式字串 + `%n`），**可利用性與常見場景**在兩邊不同，OSED 的 format string 題型有自己的套路。Ch 34–36。 |
| ASLR/DEP 繞過心態 | 找 non-PIE 二進位、ret2libc、leak 後算位址 | 找「未啟用 ASLR 的模組」直接當 ROP gadget 來源、info leak 搭配部分覆寫 | **高** | 思路幾乎一致：先找「不隨機化的東西」當立足點，再用它跳板到隨機化區域。Linux 的 `checksec` 習慣直接遷移成 Windows 的「查每個載入模組的 ASLR 旗標」。Ch 31–33。 |
| Egghunter | 偶有對應需求（shellcode 空間極小時），但 Linux pwn 教學很少系統性教 | OSED 文化裡的**標準技法**，當可用 payload 空間太小時放一段極小 stub 去記憶體裡找真正的 shellcode | **低** | 技法本身不難，但這套「兩階段：先丟小 stub 找 egg，再執行大 shellcode」的文化在 Linux CTF pwn 很少被系統性強調。Part 5。 |
| Fuzz → crash → triage 工作流 | 寫腳本打 service，`gdb` attach 看 crash | 一樣的工作流，工具換成 Python `socket` + WinDbg attach | **高** | 整體節奏一致：送資料、觀察崩潰、縮小範圍。Ch 7。 |
| 逆向找漏洞（無原始碼） | 有一定基礎（CTF reverse 題），但 Linux CTF pwn 常附原始碼或有明確 vuln 提示 | **OSED 核心哲學：給你的是黑箱二進位，逆向找洞是主要工作**，沒有原始碼、沒有提示 | **低（強度）** | 不是全新概念（你應該會讀 disassembly），但 OSED 把「獨立逆向找出漏洞」這件事的**權重**拉到遠高於一般 CTF pwn 題。這是整個 Part 4 的重點，也是心態轉變最大的地方（下一節細講）。 |

## WinDbg vs gdb+pwndbg：常用指令速查對照

這張小表不求完整（Ch 4–5 會整章教 WinDbg），目的是讓你第一次開 WinDbg 時不會完全沒有方向感。

| 操作目的 | gdb / pwndbg | WinDbg |
|---|---|---|
| 設斷點 | `break *0x401234` | `bp 0x401234` |
| 繼續執行 | `continue` / `c` | `g` |
| 單步（進入 call） | `step` / `si` | `t` |
| 單步（跨過 call） | `next` / `ni` | `p` |
| 看暫存器 | `info registers` / pwndbg 自動顯示 | `r` |
| dump 記憶體（hex） | `x/20xw $esp` | `dd esp` |
| dump 記憶體（字串） | `x/s $eax` | `da eax` |
| 看 call stack | `bt` | `k` |
| 看已載入模組 | `info sharedlibrary` / `vmmap` | `lm` |
| 看記憶體區域屬性 | `vmmap`（pwndbg） | `!address` |
| 看目前反組譯 | `x/10i $pc` / pwndbg 自動顯示 | `u eip` |
| 設條件式監看 | watchpoint `watch` | `ba`（break on access）/`bp /t` |
| 列出例外/訊號資訊 | `info signal` | `.exr -1` / `!analyze -v` |

> **沒有 `/proc/self/maps` 這種便利介面**：Linux 你可以直接 `cat /proc/pid/maps` 看整個記憶體佈局，一行指令搞定。Windows 沒有對應的檔案系統介面，要在 WinDbg 裡用 `!address` 或 `lm` 現場查，或用 Process Hacker/Process Explorer 這類 GUI 工具輔助——這個「查詢成本變高」的現實，會讓你更依賴偵錯器內的工作流而不是外部快速指令，Ch 4 會建立這套習慣。

## 心態差異：OSED 重「逆向找洞」，不是「給你 PoC 改一改」

這是整門課、也是這一章最重要的一句話，值得單獨拉出來講：

> **多數 Linux pwn CTF 題會給你原始碼，或至少強烈暗示漏洞型態（題名叫 `format_string_1`，你就知道要找 `%n`）。OSED 的核心哲學完全相反：給你一支陌生的、沒有原始碼的 Windows 服務，告訴你裡面有漏洞，剩下全部靠你自己逆向找出來。**

這個差異會具體影響你該怎麼練：

- **別只練「已知漏洞型態的利用」**。知道怎麼寫 SEH exploit 沒用，如果你不知道怎麼從一支陌生 binary 判斷「這裡用了 SEH、而且這裡有 overflow」。Part 4（IDA 逆向找洞）是整門課份量最重的 Part 之一，原因就在這裡。
- **逆向的切入點要系統化**：打開 IDA 不是亂逛，是有方法地找「這個程式接收外部輸入的地方在哪」「輸入怎麼被複製/解析」「有沒有長度檢查」。Ch 15–17 會建立這套方法論。
- **动態驗證永遠是逆向判斷的最後裁判**：靜態讀 disassembly 猜「這裡應該會 overflow」之後，一定要上 WinDbg 餵資料驗證——這點和 Linux pwn 的習慣是一致的（遷移度高），只是 Windows 下驗證的摩擦成本更高（沒有方便的 `/proc`、沒有 `pwndbg` 的自動化視覺化），所以更需要紀律。

換句話說：**OSED 考的是「把你變成一個小型漏洞研究員」，不是「把你變成一個會套公式的 exploit 寫手」**。你的 Linux pwn 底子給了你公式的直覺（ROP 怎麼排、shellcode 概念、stack 怎麎長），但「公式之前的那一步——怎麼找到問題」，是這門課要新建的核心能力。

## 踩雷集錦

1. **錯誤直覺：「WinDbg 應該和 gdb 差不多，邊用邊猜就會了」**
   正確：WinDbg 的指令體系（`k`、`dd`、`!address`、`.exr`）和 gdb 風格差異很大，而且錯誤訊息的可讀性常常不如 pwndbg 友善。花 Ch 4–5 整整兩章紮實學完指令體系，比邊用邊猜省時間十倍。

2. **錯誤直覺：「shellcode 就是調個 syscall，Windows 應該也有類似的東西」**
   正確：Windows user-mode shellcode **不直接裸呼叫 syscall**（真要做也行，但極其脆弱、依賴版本），標準做法是動態解析並呼叫 `kernel32`/`ntdll` 的匯出函式。這是 Ch 22–25 要建立的全新肌肉記憶，不要用 Linux 的 syscall 直覺硬套。

3. **錯誤直覺：「SEH 就是 Windows 版的 signal handler，應該很好懂，跳過細節」**
   正確：SEH 不只是「例外發生時跑的函式」，它是一條**存在 stack 上、可被你的 overflow 直接覆寫的連結串列**（`fs:[0]` 指向鏈首）。這個「handler 指標本身就在你能寫到的記憶體裡」的性質，是 Linux signal handling 完全沒有對應物的攻擊面，Part 3 整章專講。

4. **錯誤直覺：「我 Linux pwn 很熟，ROP chain 應該直接照搬排法」**
   正確：Windows 的 Win32 API 多數是 **stdcall**（callee 用 `ret N` 清棧），這會讓你的 ROP chain 排列方式和單純的 cdecl/x64 System V 思維不一樣——每個 gadget/函式呼叫後 stack 指標的位移量要精算，算錯 chain 會直接歪掉。Ch 3 會用完整的 ASCII stack 圖拆解這件事。

5. **錯誤直覺：「考試應該跟 vulnserver 差不多，練熟 vulnserver 就夠」**
   正確：vulnserver 是**練方法論**的靶，不是**考試題目的預覽**。考試用的是客製化、你沒見過的程式。練 vulnserver 時要刻意練「如果我完全不知道這支程式有什麼指令，我會怎麼開始」，而不是背答案。

## 進階：再往深一層

- **WoW64 這個「隱形的第三層」**：OSED 的目標是 32-bit 程式，但你的偵錯/靶機作業系統通常是 x64 Windows。這代表你其實是在**WoW64（Windows-on-Windows 64）**層裡偵錯——行程裡同時存在 32-bit 與 64-bit 的 `ntdll`，syscall 要經過一層轉譯。多數時候這層對你透明，但偶爾會在偵錯體驗上冒出奇怪現象（例如看到兩份模組列表）。Ch 4、Ch 7 會遇到時再細講，現在只要知道這東西存在、別被嚇到。
- **為什麼 OSED 選 32-bit 而不是 64-bit**：不只是教學簡化——32-bit 的呼叫慣例多樣性（cdecl/stdcall/fastcall/thiscall 並存）、更小的位址空間、更直觀的 stack 佈局，剛好適合把「exploit dev 第一原理」學得紮實。等你讀到 Ch 40（x64 差異）會看到，x64 的 fastcall-only 呼叫慣例、shadow space、更大的隨機化空間，其實是把這裡學的原理往上疊,而不是推翻重來。
- **這套遷移思維本身可以重複用**：以後碰到任何「新平台的漏洞利用」（ARM、嵌入式、macOS），都可以套用這一章的框架——先列一張「哪些直接遷移、哪些換握把、哪些全新」的表，比盲目從零學快得多。

## 本章重點整理

- 把 Linux pwn 能力分成三類看待 OSED：**高遷移**（ROP 思維、cyclic pattern、badchar 概念、shellcode 概念、fuzz→crash 工作流）、**中遷移**（呼叫慣例細節、WinDbg 操作、format string 的場景差異）、**低遷移/全新**（SEH 利用面、PEB/TEB、egghunter 文化、逆向找洞的強度要求）。
- WinDbg 和 gdb+pwndbg 的指令體系完全不同，但操作**目的**一致——照上面的對照表建立初步映射，細節留給 Ch 4–5。
- Windows user-mode shellcode 不直接裸呼叫 syscall，要動態解析 Win32 API——這是要新建的核心肌肉記憶。
- **最重要的心態轉變**：OSED 考的是「獨立逆向找出陌生程式裡的漏洞」，不是「套用已知漏洞型態的公式」。Part 4 的逆向方法論是整門課重心所在。

## 自我檢核

- [ ] 能說出至少 3 個「直接遷移」、3 個「要換握把」、3 個「全新」的 OSED 能力項目
- [ ] 能解釋為什麼 Windows shellcode 不能直接裸用 syscall number
- [ ] 能說出 SEH 和 Linux signal handler 在「攻擊面」上的關鍵差異
- [ ] 能把至少 5 個 gdb/pwndbg 指令對應到 WinDbg 指令
- [ ] 能解釋為什麼「練熟 vulnserver」不等於「準備好考試」

## 延伸閱讀

- **[Corelan — Exploit Writing Tutorial part 1: Stack Based Overflows](https://www.corelan.be/index.php/2009/07/19/exploit-writing-tutorial-part-1-stack-based-overflows/)** — Peter Van Eeckhoutte
  - **讀哪裡**：全篇，特別是和 Linux stack overflow 對照的隱含假設（文章本身預設讀者熟悉一般 overflow 概念）。
  - **學什麼**：驗證「stack overflow 控 EIP」這個核心概念在兩個平台上是同一件事,只是工具鏈不同。
  - **前提**：已有 Linux stack overflow 基礎（你應該有）。

- **[Microsoft Learn — About Processes and Threads](https://learn.microsoft.com/en-us/windows/win32/procthread/about-processes-and-threads)**
  - **讀哪裡**：行程記憶體佈局與 PEB 的簡介段落。
  - **學什麼**：建立「Windows 行程環境資訊集中在 PEB」這個和 Linux `/proc` 心智模型不同的概念,Ch 6/23 會深入。
  - **前提**：無。

- **[FuzzySecurity — Windows Exploit Development Tutorial Series](https://www.fuzzysecurity.com/tutorials.html)** — b33f (Ruben Boonen)
  - **讀哪裡**：系列目錄,挑幾篇 SEH 與 egghunter 主題的文章瀏覽。
  - **學什麼**：感受 Windows exploit dev 文化裡 SEH、egghunter 這些「Linux pwn 沒有對應物」主題的實際寫法與行話。
  - **前提**：無,當作文化導覽讀。

- **[Connor McGarr — Exploit Development blog](https://connormcgarr.github.io/)** — Connor McGarr
  - **讀哪裡**：ROP 與 Windows 呼叫慣例相關的文章。
  - **學什麼**：現代、清楚的視角看 Windows ROP chain 怎麼和 stdcall 的 `ret N` 互動——這是本章表格裡「中遷移」項目的延伸閱讀。
  - **前提**：先讀完 Ch 3 的呼叫慣例章再回來看會更有感。

- **[WinDbg vs GDB cheat sheet 類文章（搜尋關鍵字自行查找最新版）]**
  - **讀哪裡**：任何 WinDbg/gdb 指令對照表，交叉比對本章表格有無遗漏你常用的 gdb 指令。
  - **學什麼**：補完本章對照表,建立你個人的指令速查卡。
  - **注意**：這類對照表版本品質不一,以 Microsoft Learn 的 WinDbg 官方指令文件做最終仲裁。

能力盤點做完了,接下來要補上最基礎、但後面每一章都會用到的地基:x86 的暫存器、stack 機制、呼叫慣例——這是 ROP chain、SEH overwrite、shellcode 全部建立在上面的底層語言。

→ [Ch 3 x86 組語與呼叫慣例（32-bit，vs Linux/x64）](./03-x86-assembly-calling-conventions.md)
