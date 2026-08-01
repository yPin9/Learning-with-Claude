# Ch 1 — Windows pwn 為什麼和 Linux 不一樣：全景與天梯定位

> **目標**：在你開始鑽任何細節之前，先在腦中建一張完整的地圖——Windows 和 Linux 的設計哲學差在哪、這些差異在 pwn 層面造成什麼具體分歧、本課 7 個 Part 如何把這些分歧逐一攻克。讀完這章，你能說出每個 Windows 獨有機制的「為什麼」，而不只是它叫什麼名字。

## 為什麼需要這個？

你做完 `binary_exploitation`、`browser_pwn`、`kernel_pwn`，已經對作業系統和漏洞利用有了深刻的理解。但那套理解是以 Linux 為中心建立的：ELF、glibc、`ld.so`、syscall 編號固定、ASLR 依賴 `mmap` 隨機化、heap 是 ptmalloc2……這些假設深植在你的直覺裡，你幾乎不用想就能用。

到了 Windows，**每一個你認為理所當然的假設都需要重新確認**。不是 Windows 比 Linux 差，是它們在設計之初面對的問題不同、做出的取捨不同，於是在 pwn 的視角下，同一個「利用漏洞控制程式流程」的攻擊，在 Windows 上長得完全不一樣。

這章不教任何利用技巧。它做一件更重要的事：**讓你帶著正確的心智模型進入後面的章節**，而不是一直拿 Linux 的尺去量 Windows。

## 先建立直覺

想像你是個熟悉台北捷運的通勤者，第一次到東京搭地鐵。路線圖比台北密一倍、車站名字陌生、車票系統不同、轉乘邏輯也不一樣，但搭地鐵從 A 到 B 的本質需求沒有變。

Windows pwn 就是這種感覺。「找漏洞→建立原語→劫控制流→執行 shellcode」的攻擊骨架沒有變，但每一個環節的工具、機制、術語、限制都換了一套。這章就是給你看東京地鐵路線圖，讓你知道「哦，我要的那個站在這個線上」，之後才不會每次轉乘都靠猜。

## 設計哲學差異：Windows vs Linux

### 閉源但有 public symbols

Linux kernel 是開源的，看原始碼就能知道任何一個 struct 的欄位。Windows 不是——但 Microsoft 對所有系統 DLL 和 kernel 提供 **public symbols**，可以用 WinDbg + Microsoft Symbol Server 載入，然後 `dt ntdll!_PEB` 把結構的每個欄位、偏移、型別全印出來。

這個設定的實際效果比你想的好：public symbols 覆蓋 `ntdll.dll`、`kernel32.dll`、`ntoskrnl.exe` 等核心元件。研究者（j00ru、Alex Ionescu、Bruce Dang）靠著 symbols + 逆向工程，把 Windows internals 的文件程度逼近開源水準。本課後面所有「`dt ntdll!_HEAP`」這類結構觀察，都是靠 public symbols 做到的。

### Subsystem 模型與分層

Linux 的使用者態 API 層很薄：glibc 包了一層 syscall，大部分系統功能就是 syscall。Windows 是另一套哲學：

```
Win32 應用程式
      │
      ▼
 Win32 subsystem（kernel32.dll / kernelbase.dll）   ← 大多數開發者接觸的那層
      │
      ▼
 Native API（ntdll.dll）                            ← 真正的使用者態底層
      │
      ▼
 syscall → Windows Executive（ntoskrnl.exe）        ← 切進 ring 0
```

**Win32** 這層是給應用程式開發者的高階 API（`CreateFile`、`VirtualAlloc`、`CreateProcess`）。
**Native API** 這層（`Nt*` / `Zw*` 函數）是 Win32 下面的真實底層，直接對接 kernel。
**ntdll.dll** 是唯一一個 loader 在很早期就映射進去的 DLL，包含 Native API 的 stub。

對 pwn 的影響：你的 shellcode 或 ROP chain 最後常常需要呼叫 Win32 或 Native API，而不是直接 syscall（理由 Ch 7 講）。理解這個分層，你才能判斷「我的 payload 應該在哪一層落地」。

### Closed-source 但 patch 公開：Patch Tuesday 的重要性

Windows 漏洞的生命週期和 Linux CVE 有一個關鍵差異：Microsoft 在每個月第二個週二（Patch Tuesday）批量發布修補，同時，這些修補本身就是最好的「找洞指南」——你看到某個函數被修了，回去看舊版 binary 的差異，就知道哪裡有洞。Ch 43 的 patch diffing 正是這個邏輯。

## pwn 面向的六大差異：全景地圖

下面六點是本課後面所有 Part 的核心主題。這裡每條只給地圖，不給細節——細節在各自的章節裡。

### (1) 沒有「單一 libc」——ntdll / kernel32 / kernelbase 分層

Linux 世界裡，「libc」幾乎是個單一的概念：glibc（或 musl），一個 `.so`，包了 C 標準庫和 syscall wrapper。你的 ret2libc、one_gadget、`__malloc_hook` 覆寫，全靠「我知道 libc 在哪、它的 GOT 在哪、`system` 在哪」這個前提。

Windows 沒有這個單一性：

- **ntdll.dll**：最底層的使用者態 DLL，包含 Native API stub、heap 管理（`RtlAllocateHeap` 等）、exception dispatcher、各種運行時工具函數。每個 process 一定有它。
- **kernel32.dll**：包含大部分 Win32 API（`CreateProcess`、`VirtualAlloc`、`WinExec`……）。
- **kernelbase.dll**：Vista+ 之後 Windows 把 kernel32 的一部分下移到 kernelbase，讓 kernel32 變薄（API forwarding）。現在實際實作很多在 kernelbase。
- **msvcrt.dll / ucrtbase.dll / vcruntime140.dll**：C runtime，但 Windows 的 C runtime 不像 glibc 那樣和 syscall wrapper 綁在一起，它更純粹是 C 標準函數的實作。

對 exploit 的影響：「找到某個有用函數在哪個 DLL 的哪個位址」是一個需要**先做 DLL 解析**的問題（Ch 5 的 PEB-walk，Ch 6 的 API 分層）。沒有固定的「libc 基址 + offset」這種事。

### (2) 例外處理是一級公民——SEH / VEH

Linux 的例外處理在使用者態幾乎不存在（C++ exception 是 libstdc++ 在使用者態自己玩的，kernel 不特別關心）。Windows 不同：**結構化例外處理（Structured Exception Handling, SEH）是 Windows 作業系統設計的一部分**，kernel 和 OS 都積極參與例外的分發。

這衍生出了一類 Linux 完全沒有的攻擊技法：**SEH overwrite**。x86 下，每個 stack frame 都在 stack 上鏈結了一個 SEH record（指向例外處理函數的指標），如果你能做 stack buffer overflow 蓋過這條鏈，就能在觸發例外時劫控制流——而且這個攻擊的方向是**往 stack 高位寫**，和一般覆寫 ret addr 的路徑不完全一樣，保護機制也不一樣（SafeSEH、SEHOP）。

x64 的 SEH 架構不同（table-based，不在 stack 上），詳見 Ch 12。VEH（Vectored Exception Handler）是另一個使用者態可以掛的例外鉤子，在 exploit 和反調試兩邊都有應用。

### (3) Heap 是三代設計——NT Heap → LFH → Segment Heap

glibc 的 ptmalloc2 你應該很熟：chunks、bins（fastbin/tcache/unsorted/small/large）、metadata、House of X 技法族……這套設計是 Linux heap exploit 的基礎。

Windows 的 heap 是另一套完全不同的哲學，而且它**有三代在並行**：

- **NT Heap（傳統堆）**：Windows NT 時代就有的設計。`HEAP_ENTRY` 帶 encoding（用 heap handle 做 XOR 混淆），FreeList、LookasideList。比 glibc 早了很多年有 metadata 保護。
- **LFH（Low Fragmentation Heap）**：Vista 時代引入，自動在 bucket size 的 heap 分配達到閾值後啟動。LFH 的 bucket 布局和 NT Heap 完全不同，做 heap feng shui 時要知道 LFH 有沒有啟動。
- **Segment Heap**：Win10+ 引入，在 UWP 和某些系統元件預設使用。後端分三種：VS Backend、Low Fragmentation Backend（類 LFH）、Large Block Backend。是目前最現代的設計，對應的研究論文是 Mark Vincent Yason 的 Black Hat 2016 Segment Heap Internals。

對 exploit 的影響：同一個「heap buffer overflow」在 NT Heap、LFH、Segment Heap 下需要不同的利用策略。先搞清楚目標走哪個 heap，才能選正確的技法。

### (4) 緩解演進分岔——Windows 有一整套 Linux userland 幾乎沒有的緩解

Linux userland 的主流緩解：ASLR、NX（`noexec`）、stack canary（`-fstack-protector`）、RELRO（Full RELRO 讓 GOT 不可寫）、PIE。這些 CTF 打多了，每個你都有對應的繞法。

Windows 從 Vista 時代起，在這些「Linux 也有的基本款」之上，疊了一整層 Linux userland 幾乎沒有對應物的緩解：

| 緩解 | 引入版本 | 核心概念 | Linux 有對應物嗎？ |
|---|---|---|---|
| **/GS** stack cookie | VS 2003 / WinXP SP2 | 和 GCC stack canary 相似，但 seed 不同、失敗行為不同 | 有（`-fstack-protector`），相似但不同 |
| **SafeSEH** | WinXP SP2 | 編譯時建立合法 SEH handler 表，非表中的 handler 拒執行 | 無（Linux 沒有 SEH） |
| **SEHOP** | Vista SP1 | 執行時驗 SEH chain 完整性（有 fake header 節點） | 無 |
| **CFG** | Win8.1 / Win10 | 編譯器插 indirect call 前的 check，非合法 target 就 crash | Clang 的 CFI 是類比，但 Linux 預設不開 |
| **XFG** | Win10 21H1 | CFG 強化版，加型別簽名 hash，縮小有效 target 集 | 無 |
| **Intel CET / shadow stack** | Win11（硬體+OS 雙重條件）| 硬體維護影子 stack，RET 必須和影子的 return addr 吻合 | 核心已有支援，但 userland 預設不廣泛 |
| **ACG** | Win10 | 進程禁止動態生成可執行程式碼（JIT 是例外路徑） | 無直接對應的 userland 機制 |

本課 Part 5 的重頭戲就是這張表——每個緩解的設計、它的弱點、現實中的繞法。

### (5) syscall 機制：Nt* stub + SSN，編號不固定

Linux x64：`syscall` 指令，`rax` 放系統呼叫號（System Call Number, SCN），號碼**跨核心版本穩定**（即使版本升級，`read` 永遠是 0，`write` 永遠是 1……）。

Windows：每個 `Nt*` 函數在 `ntdll.dll` 裡有一個 stub，大概長這樣：

```asm
; NtReadFile stub（示意形式，非精確 bytes）
mov r10, rcx
mov eax, <SSN>          ; System Service Number，每個 Windows 版本都不同
test byte ptr [SharedUserData+0x308], 1
jne  0x...              ; 某些情況走 int 2e 路徑
syscall
ret
```

關鍵差異：**SSN（System Service Number）在每個 Windows 版本（甚至 Service Pack）都可能不同**。`NtReadFile` 在 Win10 21H1 和 Win11 22H2 的 SSN 不一樣。這導致「直接 syscall（direct syscall）」——繞過 ntdll 的 hook，自己寫 stub 做 syscall——需要先知道當前 OS 版本的正確 SSN，否則號碼對不上 kernel 的 table，系統呼叫會失敗或呼叫到錯誤的函數。

j00ru 維護了一個完整的 [Windows X86 System Call Table](https://j00ru.vexillium.org/syscalls/nt/64/)，逐版本列出每個 `Nt*` 的 SSN，是本課 Ch 7 和 Ch 37（direct syscall 技法）的主要參照。

### (6) ASLR 由 loader 對整個 image 做 relocation

Linux 的 ASLR（`mmap` 隨機化 + PIE 的 load address 隨機化）：每個 `.so` 有自己的隨機 base，RELRO 把 GOT 設成 read-only，讓 GOT 覆寫失效。

Windows 的 ASLR 機制在幾個方面不同：

- **relocation 由 loader 在 load time 做**：PE 有一張 `.reloc` 表，loader 選定新的 image base 之後，把所有需要修正的絕對位址都 patch 過。ELF 靠 PLT/GOT 做 lazy binding，Windows 靠靜態 relocation 配 IAT（Import Address Table）。
- **熵的分布**：Windows x64 的 ASLR 對 image 有 17 位熵（`HIGHENTROPYVA` 開啟時），對 heap 有 8 位熵。
- **「部分覆寫（partial overwrite）」**：Windows 一樣有這個技法——如果你能洩漏某個模組的部分位址，或者利用不完整的覆寫只改低位元組，可以在不知道完整基址的情況下做有限的控制。Ch 24 細講。
- **IAT vs GOT**：Windows 的 IAT 是每個 PE 自己的 import 解析表，不是全局的。IAT 是否可寫取決於記憶體保護設定，而不像 RELRO 那樣有一個編譯器選項決定全局。這個差異在「覆寫哪個指標來劫控制流」的決策裡很重要。

## 天梯定位：你在哪、這門課補什麼

你的現有天梯（本 repo 的課程）：

```
binary_exploitation ── Linux userland pwn 基礎（ROP / heap / format string / FSOP）
        │
        ▼
browser_pwn ─────── V8 JIT 漏洞、type confusion、V8 Sandbox 突破
        │
        ▼
vm_escape ───────── QEMU/KVM 設備模擬、MMIO/DMA 原語、虛擬機逃逸
        │
        ▼
kernel_pwn ─────── Linux kernel exploit（heap cross-cache / dirty pagetable / LPE）
        │
        ▼
windows_kernel_driver ── Windows ring-0（驅動開發 / token 竊取 / BYOVD / Anti-EDR）
        ▲
        │
        │  本課補這塊
        │
windows_pwn ────── Windows userland exploitation（本課，Ch 1–46）
```

**本課（`windows_pwn`）接在 `binary_exploitation` 之後，補 Windows userland 這塊大陸**。你已經會「Linux 使用者態 pwn」的完整思路，現在要把這個思路遷移到 Windows，並且學會 Windows 獨有的緩解（CFG/XFG/CET/SEH）如何工作、如何被研究者突破。

**本課邊界**：
- **主線**：Windows 使用者態漏洞利用（Ch 1–43）。
- **碰一點 ring-0 銜接**：Ch 44–46 講 access token、UAC、token stealing 的概念，讓你知道 userland exploit 如何轉換成提權的第一步，但深度刻意淺——完整的 kernel 路徑在 `windows_kernel_driver`。
- **不包含**：kernel exploit（ring-0）、驅動漏洞、BYOVD、HVCI 對抗——這些都在 `windows_kernel_driver`。

## 本課 7 個 Part 如何堆疊成能力

```
┌──────────────────────────────────────────────────────────────────────┐
│ Part 0  定位與環境（Ch 0–2）                                          │
│  建立正確心智模型、工具鏈（WinDbg/x64dbg/MSVC/symbols）               │
│  Linux↔Windows 對照字典（你現在在這）                                  │
└────────────────────────────┬─────────────────────────────────────────┘
                             │ 地基
                             ▼
┌──────────────────────────────────────────────────────────────────────┐
│ Part 1  Windows 執行環境 internals（Ch 3–13）                         │
│  PE格式 / Loader & ASLR / PEB & TEB / Win32 vs Native API /          │
│  syscall機制 / Handle & Object / 虛擬記憶體 / Process建立 /            │
│  SEH（x86 chain + x64 table-based + VEH）/ 逆向工具鏈                 │
│  ─ 你能在腦中畫出一個 process 從 CreateProcess 到 main() 的建立過程    │
└────────────────────────────┬─────────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────────┐
│ Part 2  Windows heap internals（Ch 14–18）                            │
│  NT Heap / LFH / Segment Heap / metadata encoding /                  │
│  WinDbg !heap 觀測 / heap grooming 基礎                              │
│  ─ 你能用 WinDbg 追一次 heap 分配，知道目標走哪個 heap backend          │
└────────────────────────────┬─────────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────────┐
│ Part 3  基礎 userland exploitation（Ch 19–25）                        │
│  Stack overflow / /GS 繞過 / SEH overwrite（Windows 經典）/            │
│  SEHOP 繞過 / DEP + ROP on Windows / ASLR leak & partial overwrite / │
│  Windows shellcode（PEB-walk 找 kernel32 / resolve API / PIC）        │
│  ─ 你能做完整的 x86 SEH overwrite → VirtualProtect ROP → shellcode    │
└────────────────────────────┬─────────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────────┐
│ Part 4  heap exploitation（Ch 26–31）                                 │
│  heap overflow 原語 / UAF / LFH feng shui / Segment Heap 技法 /       │
│  C++ vtable 劫持 / info leak 原語大全                                  │
│  ─ 你能做 UAF → vtable 控制 → ROP 的完整 heap exploit chain           │
└────────────────────────────┬─────────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────────┐
│ Part 5  現代緩解與對抗（Ch 32–39）── 重頭戲                             │
│  CFG 原理 / CFG 繞過譜系 / XFG / Intel CET & shadow stack /           │
│  ACG / CIG / data-only attacks / EMET→WDEG 演進史 / 緩解決策樹        │
│  ─ 你能判斷「這個 target 開了什麼緩解」並選出對應繞過路徑               │
└────────────────────────────┬─────────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────────┐
│ Part 6  真實環境與找洞（Ch 40–43）                                     │
│  x64 ABI & calling convention / WinDbg TTD time-travel /              │
│  WinAFL fuzzing / Patch Tuesday patch diffing                         │
│  ─ 你能對真實 Windows 程式做 patch diff 並找到潛在漏洞                 │
└────────────────────────────┬─────────────────────────────────────────┘
                             │ 天梯銜接
                             ▼
┌──────────────────────────────────────────────────────────────────────┐
│ Part 7  天梯銜接：碰一點提權（Ch 44–46）                               │
│  access token 模型 / UAC & integrity level / token stealing 概觀      │
│  ─ 你知道 userland exploit 的下一步怎麼走向提權                        │
│                       │ 交棒給                                        │
│                       ▼                                               │
│              [windows_kernel_driver]                                  │
│              ring-0：BYOVD / Anti-EDR / VBS / HVCI                   │
└──────────────────────────────────────────────────────────────────────┘
```

## 對比與取捨

| 維度 | Linux userland pwn | Windows userland pwn |
|---|---|---|
| 主要 C runtime | glibc（單一、版本可識別） | ntdll + kernel32 + VC runtime（分層） |
| syscall 編號 | 固定，跨版本穩定 | 每版本可能不同（j00ru syscall table） |
| heap 實作 | ptmalloc2（tcache/bins/chunks） | NT Heap / LFH / Segment Heap（三代） |
| 例外處理 | 使用者態幾乎透明 | SEH/VEH 是系統設計一部分，可利用 |
| 控制流保護 | 預設少（RELRO 為主） | CFG / XFG / CET（編譯器+OS 協作） |
| Symbols | 需要 debug build 或 DWARF | Microsoft public symbols（系統 DLL 免費） |
| 動態連結 | PLT/GOT + lazy binding | IAT + loader relocation（load time） |
| 利用目標 | `system`、one_gadget、`__malloc_hook` | `WinExec` / `CreateProcess` / shellcode-in-RWX |
| 學習資料 | 大量開源教材、glibc 原始碼 | BH/USENIX 論文、Corelan、j00ru、Windows Internals 書 |

## 踩雷集錦

1. **「Windows 也應該有 one_gadget」**：glibc 的 one_gadget 是 glibc 特定的工件——它存在是因為 glibc 的某些函數在特定條件下會執行 `execve("/bin/sh")` 這條路徑。Windows 的 ntdll/kernel32 沒有這種設計，也沒有任何工具能幫你「找 one_gadget」。你需要的是建一個完整的 ROP chain 呼叫 `WinExec` 或 `CreateProcess`。

2. **「ASLR 在 Windows 比較弱，暴力猜就好」**：x64 Windows 的 image ASLR 有 17 位熵（`HIGHENTROPYVA` 開啟），heap 有 8 位。暴力猜不現實——你還是需要 info leak 建立 leak-then-exploit 的完整 chain。x86 Windows 的 ASLR 熵確實較低，這種情況在 CTF 偶爾有題，但不是現代主流。

3. **「Full RELRO 等於 Windows 的 IAT 不可寫」**：這個對比不成立。Linux 的 Full RELRO 是個編譯選項，會讓整個 GOT 在 load time resolve 完後設成 read-only。Windows 的 IAT 可寫性取決於記憶體保護設定，沒有一個對應的「Full RELRO 旗標」——你要用 `VirtualQuery` 查 IAT 所在頁面的保護屬性，而不是假設它一定可寫或不可寫。

4. **「Windows 的 syscall 比 Linux 難打，繞掉 ntdll 就好」**：direct syscall（自己寫 `mov eax, SSN; syscall`）在 anti-EDR 的場景很常見，但它的代價是你必須**硬編 SSN**，或者在 runtime 動態解析 SSN。SSN 會隨 Windows 版本變動。寫 exploit 時硬編 SSN 等於綁定到特定版本——比 Linux 麻煩得多。

5. **「看了 Linux heap exploit 的 House of X 技法，Windows heap 應該類似」**：架構完全不同。glibc 的 metadata（大小、prev_size、fd、bk 指標）和 NT Heap 的 `HEAP_ENTRY`（帶 XOR encoding）設計思路不同；LFH 的 bucket 布局和 tcache 不同；Segment Heap 更是另一套。技法的名字不能通用，概念（「控制 allocator 的 free list 讓你分配到任意位址」）才是可遷移的。

## 進階：再往深一層

### 「為什麼 Windows 設計成這樣」的歷史脈絡

Windows 的這些設計不是憑空出現的。幾個關鍵轉折點：

- **MS-DOS 相容性的包袱**：Windows 早期要兼容 16 位元 DOS 程式，這塑造了 Win32 subsystem 的分層設計，讓 kernel 可以獨立演化而不破壞應用程式相容性。
- **Code Red / Blaster / Slammer（2001–2003）**：這一波蠕蟲攻擊讓 Microsoft 意識到安全的重要性，催生了 2002 年的「Trustworthy Computing」備忘錄和 SDL（Security Development Lifecycle）。/GS、SafeSEH、DEP、ASLR 都是這波之後的產物。
- **CVE-2014-1776（IE use-after-free）等影響大的 CVE**：幾乎每個重大的 CVE 都帶動一個新緩解的誕生。CFG（2014 年 Win8.1 引入）是對抗 IE UAF 利用的直接回應。

Alex Ionescu 的演講和《Windows Internals》書是了解這段歷史最好的資料。

### 研究者視角：「不能跑 source，但有 symbols + IDA/Ghidra」

做 Linux kernel exploit，你可以 `git clone` 原始碼、`grep` 任何你想找的函數、在本地 build 一個帶 KASAN 的 kernel 來測試。Windows 做不到這個，但有兩個替代方案：

1. **public symbols + WinDbg**：`dt`、`dps`、`ln` 這些指令讓你在 debug 時看穿任何結構和函數名稱。本課 Part 1 大量使用。
2. **IDA Pro / Ghidra + BinDiff**：逆向系統 DLL 和 ntoskrnl，配合 BinDiff 做 patch-before-after 比較。這是 patch diffing 的核心工作流，Ch 43 細講。

### 面試被問「Windows 和 Linux pwn 最大的差異」怎麼答

不要說「Windows 是閉源的所以難」，這個答案太淺。好的答案聚焦在**機制差異的具體影響**：

- SEH 是一級公民，衍生了 SEH overwrite 這類 Linux 沒有的技法，以及 SafeSEH/SEHOP 這類 Linux 沒有對應物的緩解。
- CFG/XFG 把間接呼叫保護推到編譯器+OS 協作的程度，Linux userland 的 default 配置沒有對應物。
- syscall 號碼不固定，直接 syscall 需要版本感知的 SSN 解析。
- heap 有三代設計，不同 backend 需要不同的利用策略。

## 動手練習

**任務**：用 WinDbg（或 cdb）對任意一個 Windows 程式（例如 Ch 0 編出來的 `hello_pe.exe`）掛上除錯器，用 `lm` 列出載入的模組，找到 `ntdll.dll` 的 base address，然後用 `dt ntdll!_PEB` 看 PEB 結構的欄位列表。

這個練習不需要真的理解 PEB 的每個欄位（Ch 5 才講），但要讓你**親眼看到 public symbols 的威力**：一個系統 DLL 的完整結構——欄位名稱、型別、偏移——被印在你面前，這就是 Windows pwn 研究的基礎工具。

> **未實測，理論預期**（需要 cdb 安裝 + `_NT_SYMBOL_PATH` 設好）：
> ```bat
> cdb -c "lm m ntdll; dt ntdll!_PEB; q" hello_pe.exe
> ```
> 預期輸出：`_PEB` 的所有欄位（`InheritedAddressSpace`、`Ldr`、`ProcessParameters` 等），帶型別和偏移。裝好後請對照真實輸出。

## 本章重點整理

- Windows 和 Linux 的核心設計哲學差異（subsystem 分層、closed-source 但有 public symbols、SEH 是一級公民）不是細節問題，而是決定了 pwn 技法和緩解的整個生態。
- pwn 面向的六大差異：(1) 沒有單一 libc、(2) SEH 衍生利用技法和獨有緩解、(3) heap 三代不同利用路徑、(4) 緩解演進大幅超前（CFG/XFG/CET/ACG）、(5) syscall 號碼不穩定、(6) ASLR 配 PE relocation + IAT 的機制。
- 本課補 `binary_exploitation` 之後的 Windows userland 缺口；kernel 和驅動路徑由 `windows_kernel_driver` 接棒。
- 7 個 Part 從 internals（PE/loader/PEB/heap）到基礎利用（SEH overwrite/ROP）到現代緩解對抗（CFG/XFG/CET），是一條完整的技能堆疊。

## 自我檢核

- [ ] 不看筆記，能說出 Windows 的 ntdll / kernel32 / kernelbase 三層各自負責什麼，以及和 glibc 最關鍵的一個差異
- [ ] 能解釋為什麼「Windows 沒有 one_gadget」——不是「找不到」，而是「為什麼不存在」
- [ ] 能說出 SEH overwrite 為什麼在 Linux 不存在、在 x86 Windows 為什麼成立（不需要知道細節，能說出原因）
- [ ] 能說出 CFG 和 Linux userland 的哪個緩解最接近，以及最關鍵的差異
- [ ] 能說出 Windows syscall 號碼不固定這件事對 exploit 的具體影響（用一句話）
- [ ] 不看課程地圖，能說出 Part 5 的主題是什麼、為什麼它是「重頭戲」

## 延伸閱讀

### 書籍

- **《Windows Internals, 7th Edition》（Part 1）** — Yosifovich, Ionescu, Russinovich, Solomon（Microsoft Press）
  - **讀哪裡**：Ch 1（概念與工具）、Ch 3（Process & Job）、Ch 5（Memory Management）——三章涵蓋本課 Part 0–1 的理論底層
  - **學什麼**：Windows 系統的第一人稱視角；你在 WinDbg 看到的任何結構，在這本書裡都有對應的解釋
  - **和本章的關聯**：本章所有「為什麼 Windows 這樣設計」的論述，來源都是這本書的架構
  - **前提知識**：作業系統概念（process / virtual memory / DLL）；不需要 Windows 開發經驗

### 論文 / 白皮書

- **[Windows 10 Segment Heap Internals](https://www.blackhat.com/docs/us-16/materials/us-16-Yason-Windows-10-Segment-Heap-Internals.pdf)** — Mark Vincent Yason，Black Hat US 2016
  - **讀哪裡**：Introduction 和 Architecture Overview 兩節，先建全貌
  - **學什麼**：Segment Heap 三種 backend 的設計邏輯，為什麼 NT Heap 不夠用
  - **和本章的關聯**：本章 (3) 的 heap 三代差異的詳細來源；Ch 16/29 的核心參照
  - **前提知識**：heap allocator 基本概念（chunk、freelist）

- **[Bypass Control Flow Guard Comprehensively](https://www.blackhat.com/docs/us-15/materials/us-15-Zhang-Bypass-Control-Flow-Guard-Comprehensively-wp.pdf)** — Yunhai Zhang，Black Hat US 2015
  - **讀哪裡**：Section 1–3（CFG 設計原理），看懂它在解決什麼問題
  - **學什麼**：CFG 第一版的設計，以及它的侷限——之後 XFG 的誕生是這些侷限的直接回應
  - **和本章的關聯**：本章 (4) CFG 的背景；Ch 32/33 的入場準備
  - **前提知識**：間接呼叫（`call [rax]`）的概念；ROP 是什麼

### 部落格

- **[j00ru // Windows X86/X64 System Call Tables](https://j00ru.vexillium.org/syscalls/nt/64/)** — Mateusz Jurczyk
  - **讀哪裡**：直接查表——找任意 `Nt*` 函數在 Windows 各版本的 SSN
  - **學什麼**：syscall 號碼跨版本漂移的直觀感受；Win7 到 Win11 跨了多少號
  - **和本章的關聯**：本章 (5) syscall 號碼不固定的具體依據；Ch 7 的主要參照
  - **前提知識**：知道什麼是 syscall 即可

- **[Alex Ionescu — Sheep Year Kernel Heap Fengshui（REcon 2016 slides）](http://www.alex-ionescu.com/?p=255)**
  - **讀哪裡**：前半段關於 Windows heap 架構演進的介紹
  - **學什麼**：從 NT Heap 到 LFH 的設計演進，以及從 allocator 設計角度看利用技法的興衰
  - **和本章的關聯**：本章 (3) heap 三代的設計邏輯；Ch 14–16 的背景脈絡
  - **前提知識**：glibc heap 的基本概念（chunk、bin）

讀完本章，你有了完整的地圖。下一章我們進入第一個具體工作：把你每一個 Linux pwn 直覺，逐條對應到 Windows 的等價物，建立你在本課全程都會用到的「翻譯字典」。

→ [Ch 2 — Linux→Windows 攻堅直覺遷移對照表](./02-linux-to-windows-mindset.md)
