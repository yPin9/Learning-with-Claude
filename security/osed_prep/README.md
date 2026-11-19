# OSED 備考全攻略：逆向 → 手寫 shellcode → 三大題型打穿（EXP-301）

> 給會 Linux pwn、Windows 生疏、要拿 OffSec Exploit Developer（OSED / EXP-301）證照的人。

這門課把你從「Linux ROP 很熟、一碰 Windows exploit dev 就卡」帶到「能獨立逆向一支陌生的 Windows x86 網路服務、找出 bug、寫出繞 DEP/ASLR 的穩定 exploit 並產出 OffSec 風格報告」。**全程 x86 32-bit**（考試就是 32 位）、**WinDbg + IDA 真操作**、以 **OffSec 考試方法論**為主軸：不是堆技巧，是把「fuzz → 逆向 → 控流 → 繞緩解 → shellcode → 報告」這條生產線練到閉著眼睛也能跑。

和本 repo 既有的 `windows_pwn`（x64、CTF、現代緩解對抗）是**互補的兩個世界**：那門課挖 internals 與 Win11 緩解；這門課打 OSED 考試現場。深度重疊的 internals（PE/SEH/ROP 原理）本課用橫向連結帶過，篇幅全花在 OSED 專屬的逆向找洞、手寫 shellcode、三大題型打磨上。

## 為什麼學這個？

- **實用角度**：OSED 是少數真正逼你「從零手寫 shellcode、手搓 ROP chain、逆向陌生二進位找洞」的證照。考完你不只有一張證，而是真的具備 Windows 漏洞研究的入門生產力——這是紅隊、漏洞研究、exploit dev 職缺的硬通貨。
- **底層理解**：OSED 不讓你用自動化工具蒙混。你會被逼著搞懂 PEB 怎麼走、SEH chain 長什麼樣、ROP 為什麼要那樣排、format string 的 `%n` 到底寫了什麼到哪。這種「每個 byte 都知道為什麼」的理解，是 CTF 和真實研究共通的底座。
- **職涯/考試角度**：OSED 是 OffSec「300 系列」的核心，和 OSEP（EXP-312 之外的 PEN-300）、OSEE（EXP-401）構成進階線。它的含金量在於**動手門檻真實**——會的人都是真的會。

## 先修知識

- **Linux userland pwn**（程度：做完本 repo `binary_exploitation` 或等價）：ROP、stack overflow、shellcode、壞字元的直覺。本課大量用「vs Linux / vs glibc」對照加速。
- **x86 / x86-64 組語**（程度：能讀 disassembly、懂 stack frame）。32-bit 的 cdecl/stdcall 差異 Ch 3 會補。
- **C**（程度：能讀，懂指標與字串函式的危險）。
- **基本網路程式概念**（socket、TCP recv/send）——OSED 的目標幾乎都是網路服務。
- 沒有也沒關係的：WinDbg（Ch 4–5 從零教）、IDA（Ch 14 從零教）、Windows API 開發經驗（用到才補）。

## 課程地圖

### Part 0 — OSED 定位與環境（Ch 0–2）
- [Ch 0 環境搭建：x86 靶 + WinDbg + IDA + mona](./00-environment-setup.md)
- [Ch 1 OSED 是什麼：EXP-301 考綱與考試格式拆解](./01-osed-exam-overview.md)
- [Ch 2 從 Linux pwn 到 OSED：你會什麼、缺什麼](./02-linux-to-osed-mindset.md)

### Part 1 — 基礎補強：x86 / WinDbg / PE（Ch 3–6）
- [Ch 3 x86 組語與呼叫慣例（32-bit，vs Linux/x64）](./03-x86-assembly-calling-conventions.md)
- [Ch 4 WinDbg 精通 I：命令體系、斷點、記憶體與符號](./04-windbg-mastery-1.md)
- [Ch 5 WinDbg 精通 II：例外觀測、搜尋、crash triage 工作流](./05-windbg-mastery-2.md)
- [Ch 6 PE 格式與記憶體佈局速成](./06-pe-format-crash-course.md)

### Part 2 — Stack Overflow 完整方法論（Ch 7–10）
- [Ch 7 vanilla stack overflow：fuzz → crash → 控 EIP](./07-stack-overflow-methodology.md)
- [Ch 8 壞字元分析與 offset 定位](./08-badchars-and-offset.md)
- [Ch 9 找 jump point（jmp esp）與模組選擇](./09-finding-jump-point.md)
- [Ch 10 第一個完整 exploit：壞字元安全 shellcode + 反彈 shell](./10-first-complete-exploit.md)
- [練習 A：vulnserver 完整 stack overflow exploit](./practice-a-vulnserver-stack-overflow.md)

### Part 3 — SEH Overflow（Ch 11–13）
- [Ch 11 SEH overwrite 原理](./11-seh-overwrite-principle.md)
- [Ch 12 SEH exploit 流程：nSEH/SEH、POP POP RET、短跳+長跳](./12-seh-exploit-flow.md)
- [Ch 13 SafeSEH 與模組選擇繞過](./13-safeseh-bypass.md)
- [練習 B：SEH-based exploit](./practice-b-seh-exploit.md)

### Part 4 — IDA Pro 逆向找洞（OSED 靈魂，Ch 14–18）
- [Ch 14 IDA Pro 入門：graph view、xref、工作流](./14-ida-pro-intro.md)
- [Ch 15 逆向 Windows 網路服務：拆協定、找輸入點](./15-reversing-network-services.md)
- [Ch 16 從反組譯辨識 bug：unsafe copy、長度檢查缺陷](./16-identifying-bugs-in-disasm.md)
- [Ch 17 靜態逆向 + 動態驗證雙軌（IDA ↔ WinDbg）](./17-static-dynamic-dual-track.md)
- [Ch 18 逆向實戰：從未知二進位找出可利用 overflow](./18-reversing-case-study.md)
- [練習 C：逆向自訂網路服務找出並觸發漏洞](./practice-c-reverse-and-trigger.md)

### Part 5 — 空間限制與 Egghunter（Ch 19–21）
- [Ch 19 空間限制：shellcode 放不下怎麼辦](./19-space-restrictions.md)
- [Ch 20 Egghunter 原理與實作](./20-egghunter-principles.md)
- [Ch 21 客製 egghunter 與受限字元調校](./21-custom-egghunter.md)

### Part 6 — 自寫 x86 Shellcode（Ch 22–26）
- [Ch 22 為什麼手寫 shellcode（vs msfvenom）](./22-why-write-shellcode.md)
- [Ch 23 PEB 走訪找 kernel32 基址](./23-peb-walk-kernel32.md)
- [Ch 24 export table 解析 + hash API resolution](./24-export-table-hash-resolution.md)
- [Ch 25 寫 WinExec / reverse shell shellcode（組語 → bytes）](./25-writing-reverse-shell-shellcode.md)
- [Ch 26 自寫 encoder/decoder stub + 消壞字元](./26-custom-encoder-decoder.md)
- [練習 D：手寫 bad-char-free reverse shell shellcode](./practice-d-custom-reverse-shell.md)

### Part 7 — DEP Bypass（Ch 27–30）
- [Ch 27 DEP 機制復習 + OSED 情境](./27-dep-mechanism-osed.md)
- [Ch 28 ROP gadget 尋找（mona rop / rp++）與 chain 規劃](./28-rop-gadget-finding.md)
- [Ch 29 手工打造 VirtualProtect / VirtualAlloc ROP chain](./29-virtualprotect-rop-chain.md)
- [Ch 30 WriteProcessMemory 等其他路徑 + 除錯 ROP chain](./30-other-dep-bypass-paths.md)
- [練習 E：stack overflow + 手工 ROP DEP bypass](./practice-e-stack-overflow-dep-bypass.md)

### Part 8 — ASLR Bypass（Ch 31–33）
- [Ch 31 ASLR 復習 + 非隨機化模組利用](./31-aslr-nonrandomized-modules.md)
- [Ch 32 info leak 製造：部分覆寫、可預測位址](./32-info-leak-primitives.md)
- [Ch 33 ASLR + DEP 合體完整鏈](./33-aslr-dep-full-chain.md)

### Part 9 — Format String（Ch 34–36）
- [Ch 34 format string 原理（%x/%n/%s，Windows vs Linux）](./34-format-string-principle.md)
- [Ch 35 任意讀：leak 記憶體 / 繞 ASLR](./35-format-string-arbitrary-read.md)
- [Ch 36 任意寫：%n 改寫導向 code exec](./36-format-string-arbitrary-write.md)
- [練習 F：format string → leak → 控制流](./practice-f-format-string-chain.md)

### Part 10 — PE 後門 / Trojanizing（Ch 37–38）
- [Ch 37 PE 後門原理：code cave、hijack entry point](./37-pe-backdoor-principles.md)
- [Ch 38 手工 trojanize 合法 PE：插入 shellcode + 還原執行流](./38-trojanizing-pe.md)

### Part 11 — 考試方法論 + 加深一圈（Ch 39–41）
- [Ch 39 OSED 考試作戰：時間分配、報告、常見失分](./39-osed-exam-strategy.md)
- [Ch 40 加深一圈 I：x64 差異（為何考 x86）](./40-deeper-x64-differences.md)
- [Ch 41 加深一圈 II：CFG/CET 衝擊 + ROP 自動化](./41-deeper-cfg-cet-rop-automation.md)

### Final Project
- [Final Project：48 小時 OSED 模擬考](./final-project-osed-mock-exam.md)

## 學習方式建議

1. **每章都在 WinDbg 裡驗證**：OSED 沒有 WinDbg 就是紙上談兵。每個結構、每次 crash、每條 ROP chain 都在偵錯器裡親眼看一次。
2. **故意弄壞**：關掉 DEP 看 shellcode 直接跑、開 DEP 看它怎麼當、改一個 ROP gadget 位址看 chain 怎麼歪——對照 before/after 才學得到緩解的意義。
3. **全程對照 Linux**：每學一個 Windows 技法，先問「Linux 對應的是什麼？差在哪？」你的 Linux pwn 底子是最大加速器。
4. **逆向優先於 PoC**：OSED 的分數在「你能不能自己找到洞」，不是「給你 PoC 能不能改」。Part 4 的逆向工作流請反覆練到變成肌肉記憶。
5. **正確性優先**：本課範例盡量在 Win10 x86 真編真跑。標注「未實測，理論預期」的段落，請在你自己環境驗證。

## 精選資料庫

整門課最值得反覆參照的資源；每章的「延伸閱讀」會指向更具體的小節。

### 必讀基礎

- **OffSec EXP-301 課程教材（WEB-301 / PDF + 影片）** — Offensive Security
  - 考試的唯一權威範圍界定。本課對齊它的模組順序，但把每個主題挖得更深、補更多對照與踩雷。
- **《Windows Internals, 7th Edition》（Part 1）** — Yosifovich, Ionescu, Russinovich, Solomon（Microsoft Press）
  - 需要查 process/memory/SEH/PEB 的權威定義時來這裡。本課前置章只取 OSED 需要的切片。
- **[Microsoft Learn — Win32 API docs](https://learn.microsoft.com/en-us/windows/win32/)**
  - `VirtualProtect` / `VirtualAlloc` / `WinExec` 等 API 的參數語意最終仲裁；寫 ROP chain 和 shellcode 時必查。

### 推薦部落格 / 教學系列

- **[Corelan Team — Exploit Writing Tutorials](https://www.corelan.be/index.php/articles/)** — Peter Van Eeckhoutte
  - OSED 技法的祖師爺級教材；stack overflow / SEH / egghunter / mona 的原始出處，整個系列都值得精讀。
- **[Connor McGarr — Exploit Development blog](https://connormcgarr.github.io/)** — Connor McGarr
  - 現代、清楚、Windows exploit dev 導向；ROP、egghunter、format string 的講解和 OSED 高度對齊。
- **[FuzzySecurity — Windows Exploit Development Tutorial Series](https://www.fuzzysecurity.com/tutorials.html)** — b33f (Ruben Boonen)
  - 老但扎實的 Windows exploit 系列，和 OSED 題型直接對應。

### 工具與靶

- **[vulnserver](https://github.com/stephenbradshaw/vulnserver)** — Stephen Bradshaw
  - 練 stack/SEH overflow 的經典靶，本課 Part 2–3 的主力靶程式。
- **[mona.py](https://github.com/corelan/mona)** — Corelan
  - WinDbg/Immunity 的 exploit 開發神器；pattern、bad char、rop、egghunter 一條龍。

### 讀完本課之後

- **本 repo [`windows_pwn`](../windows_pwn/README.md)**：把 x86 經典技法推進到 x64 + Win11 現代緩解（CFG/XFG/CET/Segment Heap），是 OSED 之後最自然的下一步。
- **OSEE（EXP-401）方向**：瀏覽器與核心的進階 exploit dev；本 repo 的 [`browser_pwn`](../browser_pwn/README.md) 與 [`kernel_pwn`](../kernel_pwn/README.md) 可當前菜。
