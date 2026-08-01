# Windows 使用者態漏洞利用學習筆記：從 internals 到打穿 Win11 現代緩解

> 給已經會 Linux userland pwn、想把攻堅直覺搬進 Windows 的人。

這門課把你整條 Linux pwn 天梯（`binary_exploitation` → `browser_pwn` → `vm_escape` → `kernel_pwn` → `android_exploitation`）缺的那塊大陸補起來：**Windows userland exploitation**。前半把 Windows internals 挖到能當參考書（對照你 Linux 的 `kernel_internals`），後半從經典 SEH/ROP 一路打到 Win11 現代緩解（CFG / XFG / CET / ACG）對抗。CTF 導向、全程 Win11 x64 實測，最後碰一點 token/提權當作銜接 `windows_kernel_driver` 的天梯扶手。

## 為什麼學這個？

- **實用角度**：桌面/企業攻擊面九成是 Windows。你會 glibc heap、會 ROP、會繞 ASLR/NX，但一到 Windows 就卡在 SEH、Segment Heap、CFG——這些沒有 Linux 對應物，非補不可。
- **底層理解**：Windows 的 loader、例外處理、堆管理和 Linux 是**兩套完全不同的設計哲學**。看懂它們的取捨，你對「作業系統怎麼撐起一個 process」的理解會立體一倍。
- **職涯/CTF 角度**：Windows pwn 題在 CTF 相對稀有、分數高、會的人少；紅隊與漏洞研究職缺幾乎都要 Windows 底子。

## 先修知識

- **Linux userland pwn**（程度：做完 `binary_exploitation` 或等價）：ROP、heap 利用、ASLR/NX 繞過的直覺，這門課大量用「vs glibc / vs Linux」對照
- **C / C++**（程度：能讀能寫，懂 vtable、calling convention）
- **x86 / x86-64 組語**（程度：能讀 disassembly、懂 stack frame）
- 沒有也沒關係的：Windows API 開發經驗（Ch 6 會補）、WinDbg（Ch 0 從零教）

## 課程地圖

### Part 0 — 定位與環境（Ch 0–2）
- [Ch 0 環境搭建：WinDbg / x64dbg / MSVC / symbols](./00-environment-setup.md)
- [Ch 1 Windows pwn 為什麼和 Linux 不一樣](./01-why-windows-pwn.md)
- [Ch 2 Linux→Windows 攻堅直覺遷移對照表](./02-linux-to-windows-mindset.md)

### Part 1 — Windows 執行環境 internals（Ch 3–13）
- [Ch 3 PE 格式深挖（vs ELF）](./03-pe-format.md)
- [Ch 4 載入器與模組：image base / ASLR relocation / LDR](./04-loader-and-modules.md)
- [Ch 5 PEB / TEB：結構、走訪與在 exploit 裡的用途](./05-peb-teb.md)
- [Ch 6 Win32 API vs Native API (ntdll)](./06-win32-vs-native-api.md)
- [Ch 7 syscall 機制與版本漂移](./07-syscall-mechanism.md)
- [Ch 8 Handle 與 Object Manager](./08-handle-object-manager.md)
- [Ch 9 虛擬記憶體與保護：VirtualAlloc/Protect / section / W^X](./09-virtual-memory.md)
- [Ch 10 行程與執行緒建立：CreateProcess 內部](./10-process-thread-creation.md)
- [Ch 11 例外處理架構 I：x86 SEH chain](./11-seh-x86.md)
- [Ch 12 例外處理架構 II：x64 table-based SEH / VEH / UEF](./12-seh-x64-veh.md)
- [Ch 13 符號與逆向工具鏈：public symbols / IDA / Ghidra](./13-symbols-and-re-tooling.md)
- [練習 A：手寫 PE parser + 從 PEB 走 LDR 找 API](./practice-a-pe-parser-peb-walk.md)

### Part 2 — Windows heap internals（Ch 14–18）
- [Ch 14 NT Heap 傳統架構（對照 glibc）](./14-nt-heap.md)
- [Ch 15 LFH (Low Fragmentation Heap)](./15-lfh.md)
- [Ch 16 Segment Heap（Win10+ 現代預設堆）](./16-segment-heap.md)
- [Ch 17 heap metadata encoding 與完整性檢查](./17-heap-metadata-encoding.md)
- [Ch 18 用 WinDbg !heap 觀測與 heap grooming 基礎](./18-windbg-heap-grooming.md)
- [練習 B：用 WinDbg 追一次 LFH 分配，畫出 bucket 布局](./practice-b-lfh-tracing.md)

### Part 3 — 基礎 userland exploitation（Ch 19–25）
- [Ch 19 stack buffer overflow（x86，無防護的世界）](./19-stack-overflow.md)
- [Ch 20 /GS stack cookie：機制與繞過思路](./20-gs-stack-cookie.md)
- [Ch 21 SEH overwrite：Windows 經典技法](./21-seh-overwrite.md)
- [Ch 22 SEHOP：機制與繞過](./22-sehop.md)
- [Ch 23 DEP + ROP on Windows](./23-dep-rop.md)
- [Ch 24 ASLR：Windows 特性 / leak / 部分覆寫](./24-aslr.md)
- [Ch 25 Windows shellcode：PEB 找 kernel32 / resolve API / PIC](./25-windows-shellcode.md)
- [練習 C：x86 SEH overwrite → ROP-to-VirtualProtect exploit](./practice-c-seh-rop-exploit.md)

### Part 4 — heap exploitation（Ch 26–31）
- [Ch 26 heap overflow 原語與布局控制](./26-heap-overflow.md)
- [Ch 27 UAF on Windows](./27-uaf.md)
- [Ch 28 LFH 精確 grooming (feng shui)](./28-lfh-grooming.md)
- [Ch 29 Segment Heap 利用技法](./29-segment-heap-exploitation.md)
- [Ch 30 C++ 物件導向利用：vtable 劫持 / 物件再用](./30-cpp-vtable-hijack.md)
- [Ch 31 info leak 原語大全](./31-info-leak-primitives.md)
- [練習 D：heap UAF → 控 vtable → 轉 ROP](./practice-d-uaf-vtable.md)

### Part 5 — 現代緩解與對抗（重頭戲，Ch 32–39）
- [Ch 32 CFG (Control Flow Guard) 原理](./32-cfg.md)
- [Ch 33 CFG 繞過技法譜系](./33-cfg-bypass.md)
- [Ch 34 XFG (eXtended Flow Guard)](./34-xfg.md)
- [Ch 35 Intel CET / shadow stack on Windows](./35-cet-shadow-stack.md)
- [Ch 36 ACG / CIG / code integrity](./36-acg-cig-code-integrity.md)
- [Ch 37 data-only attacks：繞過所有 CFI](./37-data-only-attacks.md)
- [Ch 38 EMET→WDEG 緩解演進史](./38-emet-wdeg-history.md)
- [Ch 39 緩解總表 + 繞過決策樹](./39-mitigation-decision-tree.md)
- [練習 E：打穿 CFG — 從被擋到繞過](./practice-e-cfg-bypass.md)

### Part 6 — 真實環境與找洞（Ch 40–43）
- [Ch 40 x64 ABI / calling convention 對 exploit 的影響](./40-x64-abi.md)
- [Ch 41 WinDbg 進階：TTD time-travel debugging](./41-windbg-ttd.md)
- [Ch 42 fuzzing on Windows：WinAFL / TTD-based](./42-fuzzing-winafl.md)
- [Ch 43 找洞：Patch Tuesday patch diffing](./43-patch-diffing.md)

### Part 7 — 天梯銜接：碰一點提權（Ch 44–46）
- [Ch 44 access token 模型：SID / privileges / integrity level](./44-access-token-model.md)
- [Ch 45 UAC 與 integrity level 繞過概觀](./45-uac-integrity-level.md)
- [Ch 46 token stealing / EoP 原語概觀](./46-token-stealing-eop.md)

### Final Project
- [Final Project：Win11 x64 現代緩解全開下的 userland exploit chain](./final-project-windows-exploit-chain.md)

## 學習方式建議

1. **讀完一章就動手**：Windows pwn 沒有 WinDbg 就是紙上談兵。每章的結構觀察都在 WinDbg 裡驗證一次。
2. **故意把它弄壞**：關掉/GS 看 stack cookie 怎麼變、開 CFG 看 indirect call 被擋——對照 before/after 才學得到緩解的意義。
3. **全程對照 Linux**：每學一個 Windows 機制，先問「Linux 對應的是什麼？差在哪？」——你的 Linux pwn 底子是最大的加速器。
4. **正確性優先**：本課範例盡量在 Win11 x64 真編真跑。標注「未實測，理論預期」的段落，請在你自己環境驗證。

## 精選資料庫

整門課最值得反覆參照的資源；每章的「延伸閱讀」會指向更具體的小節。

### 必讀基礎

- **《Windows Internals, 7th Edition》（Part 1 & 2）** — Yosifovich, Ionescu, Russinovich, Solomon（Microsoft Press）
  - Windows internals 的權威聖經；Part 1 的 process/memory/object、Part 2 的 I/O 是本課前半的主要靠山
- **[Microsoft Learn — Win32 / Windows Driver docs](https://learn.microsoft.com/en-us/windows/win32/)**
  - API 語意與結構定義的最終仲裁；遇到行為不符預期時來這裡查

### 推薦論文 / 白皮書

- **[Windows 10 Segment Heap Internals](https://www.blackhat.com/docs/us-16/materials/us-16-Yason-Windows-10-Segment-Heap-Internals.pdf)** — Mark Vincent Yason，Black Hat US 2016
  - Segment Heap 目前最完整的公開剖析；Ch 16 的骨架來源
- **[Bypassing Control Flow Guard in Windows 10](https://improsec.com/tech-blog/bypassing-control-flow-guard-in-windows-10)** — Morten Schenk
  - CFG 繞過的經典整理，Ch 33 的參照

### 推薦部落格 / 文章

- **[Corelan Team — Exploit Writing Tutorials](https://www.corelan.be/index.php/articles/)** — Peter Van Eeckhoutte
  - Windows 漏洞利用教學的黃金標準（SEH/mona/ROP 都出自這裡），Part 3 的實作藍本
- **[j00ru // windows kernel logs](https://j00ru.vexillium.org/)** — Mateusz Jurczyk
  - Windows 安全研究最硬的個人部落格之一，syscall table / 漏洞研究方法論

### 讀完本課之後

- **《windows_kernel_driver》（本 repo）** — 把提權從 userland 帶進 kernel，token 竊取/BYOVD/Anti-EDR
- **[MSRC Blog](https://msrc.microsoft.com/blog/)** — 追 Microsoft 官方的緩解演進與漏洞緩解設計思路
