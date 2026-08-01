# Ch 0 — 環境搭建：WinDbg / x64dbg / MSVC / symbols

> **目標**：把一台能「編出帶各種緩解的 PE、用除錯器看穿它的記憶體佈局、用 Python 寫 exploit」的 Windows pwn 工作站架起來。學完這章你能編一個 hello world、用 `dumpbin` 看它開了哪些緩解、掛上除錯器停在 `main`，並知道每個工具在整個攻擊流程裡負責哪一段。

> **環境**：Windows 11 Pro（build 26200，x64）。除錯器用 WinDbg（現代版 WinDbgX，2024+）與命令列 `cdb`；編譯器用 MSVC（VS 2022 v143 toolset）為主、mingw-w64 GCC 14 為輔；scripting 用 Python 3.12。標注「未實測」的段落請在你自己裝好 MSVC 後驗證。

## 為什麼需要這個？（不是「照抄安裝步驟」）

你已經有一套順手的 Linux pwn 工具鏈：`gcc` 編、`gdb`/`pwndbg` 調、`pwntools` 打、`checksec` 看防護、`ROPgadget` 找 gadget。到了 Windows，這五件事**每一件都換了工具，而且底層假設不一樣**：

| 你在 Linux 用的 | Windows 對應 | 關鍵差異（這章要你建立的直覺） |
|---|---|---|
| `gcc` / `clang` | **MSVC (`cl.exe`)** | 緩解（`/GS`、`/guard:cf`）是 **MSVC + `link.exe` 專屬**旗標，mingw 編不出來 |
| `gdb` / `pwndbg` | **WinDbg / `cdb`** | 指令語法完全不同，但 WinDbg 能直接讀 Microsoft 的 **public symbols**，看 `ntdll` 內部結構像開透視 |
| `checksec` | **`dumpbin /headers`、`/loadconfig`** | 看的是 PE 的 `DllCharacteristics` 與 Load Config，不是 ELF 的 `GNU_STACK`/`RELRO` |
| `ROPgadget` / `ropper` | **`rp++`、ropper（支援 PE）、mona.py** | gadget 來源常是 Windows 系統 DLL，且要對齊版本 |
| `pwntools` | **pwntools（有 Windows 支援）+ ctypes** | 很多驗證直接用 Python `ctypes` 呼叫 Win32 API 最快 |

如果不先搞懂「為什麼是這些工具」，你會一直用 Linux 的心智模型去套 Windows，然後在每個轉角撞牆。這章先把工具擺好、把它們各自的職責釐清。

## 先建立直覺：一條 Windows exploit 的工具流水線

想像你要打一個有漏洞的 Windows 程式，工具是這樣接力的：

```
   ┌─────────┐   編譯帶/不帶緩解    ┌──────────────┐
   │  MSVC   │ ──────────────────► │  target.exe  │  ← 你的靶
   │ cl+link │   /GS /guard:cf     │   (PE 檔)    │
   └─────────┘                     └──────┬───────┘
                                          │
        看它開了什麼防護                   │  掛上去看記憶體
   ┌──────────────────┐                   ▼
   │ dumpbin /headers │            ┌──────────────┐   讀 Microsoft symbols
   │ dumpbin /loadconfig├──────────│ WinDbg / cdb │ ─────────────────────►  ntdll!_PEB, _HEAP...
   └──────────────────┘            └──────┬───────┘
                                          │  找 gadget / 算 offset
        寫 exploit 送 payload              ▼
   ┌────────────────────┐          ┌──────────────┐
   │ Python (pwntools    │ ─payload►│ rp++ / ropper│
   │  / ctypes / socket) │          │  找 ROP gadget│
   └────────────────────┘          └──────────────┘
```

四個角色：**MSVC 造靶**、**dumpbin 驗防護**、**WinDbg 看記憶體**、**Python+gadget 工具打**。這章把四個都裝起來、各驗一次。

## Part 1：編譯器——MSVC 為主，mingw 為輔

### 為什麼一定要 MSVC，而不是繼續用 GCC？

這門課後半的重頭戲是**現代緩解對抗**（Part 5：CFG / XFG / CET）。這些緩解不是作業系統憑空加的，而是**編譯器在編譯時插入檢查、連結器在 PE 裡標記旗標**。而這套機制是 MSVC 生態的：

- `/GS`：stack cookie（對應 GCC 的 stack canary，但實作與繞法不同）
- `/guard:cf`：Control Flow Guard（Linux userland 沒有等價的預設緩解）
- `/guard:xfg`：eXtended Flow Guard
- `/CETCOMPAT`（link.exe）：標記支援 CET shadow stack
- `/SAFESEH`、`/NXCOMPAT`、`/DYNAMICBASE`、`/HIGHENTROPYVA`：SafeSEH、DEP、ASLR、高熵 ASLR

**mingw-w64 GCC 編不出 CFG/XFG/SafeSEH**——它是另一套 runtime。所以 Part 5 你非 MSVC 不可。

那 mingw 還有用嗎？有。Part 0–4 講 PE 結構、PEB 走訪、基礎 stack/heap 溢位時，mingw 編得又快又不用開 VS，而且它預設就開了 ASLR+DEP，拿來當「基礎款靶」很方便。本課策略：**mingw 打前站，MSVC 打緩解硬仗**。

### 安裝 MSVC（VS 2022 C++ workload）

你若只裝了 Visual Studio 但沒勾 C++ 工作負載，是**沒有 `cl.exe` 的**（本機初始狀態就是這樣）。補裝：

**GUI 路徑**：開「Visual Studio Installer」→ 對 VS Community 2022 按「修改」→ 勾選 **「使用 C++ 的桌面開發」(Desktop development with C++)** → 安裝。

**命令列路徑**（系統管理員終端機）：

```powershell
& "C:\Program Files (x86)\Microsoft Visual Studio\Installer\vs_installer.exe" `
  modify --installPath "C:\Program Files\Microsoft Visual Studio\2022\Community" `
  --add Microsoft.VisualStudio.Workload.NativeDesktop --includeRecommended --passive
```

裝好後，`cl.exe` 不會進系統 PATH——你要透過 **Developer 環境**用它。兩種方式：

1. 開「x64 Native Tools Command Prompt for VS 2022」（開始選單搜得到），裡面 `cl`、`link`、`dumpbin` 都就位。
2. 在一般終端機裡呼叫 `vcvars64.bat` 載入環境：

```bat
call "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"
```

> **未實測**：本機撰稿時 C++ workload 尚未安裝，以下 `cl`/`dumpbin` 輸出為安裝後的預期結果。裝好後請自行跑一次對照。編一個最小 PE：
>
> ```bat
> cl /nologo hello.c            REM 預設就會帶 /GS、/guard:cf(視版本)、/DYNAMICBASE
> ```

### 安裝 mingw（已在本機驗證可用）

本機的 msys2 已經有 UCRT64 GCC 14.2。把 `C:\msys64\ucrt64\bin` 加進 PATH 即可用。**真實驗證**（本機實跑）：

```console
$ gcc --version
gcc.exe (Rev2, Built by MSYS2 project) 14.2.0

$ cat hello_pe.c
#include <stdio.h>
#include <windows.h>
int main(void) {
    printf("PE built by mingw runs. GetCurrentProcessId=%lu\n",
           (unsigned long)GetCurrentProcessId());
    return 0;
}

$ gcc -o hello_pe.exe hello_pe.c
$ file hello_pe.exe
hello_pe.exe: PE32+ executable (console) x86-64, for MS Windows, 20 sections

$ ./hello_pe.exe
PE built by mingw runs. GetCurrentProcessId=4912
```

`PE32+` 就是 64 位元 PE（`+` 代表 PE32+ 格式，Ch 3 細講）。這台已經能造 64 位元 Windows 執行檔了。

## Part 2：看穿防護——dumpbin 與 objdump

編出來的 PE 開了哪些緩解？在 Linux 你會 `checksec`；Windows 看的是 PE 標頭裡的 `DllCharacteristics` 位元遮罩，以及 Load Config 目錄（CFG 的資訊放這裡）。

### 用 mingw 的 objdump 先看（真實輸出）

```console
$ objdump -p hello_pe.exe | grep -iE "DllCharacteristics|DYNAMIC|NX|ENTROPY"
DllCharacteristics	00000160
					HIGH_ENTROPY_VA
					DYNAMIC_BASE
```

`0x0160` 這個遮罩拆開來看，是本課會反覆用到的緩解位元：

| bit | 值 | 意義 | 在 `0x0160` 裡？ |
|---|---|---|---|
| `HIGH_ENTROPY_VA` | `0x0020` | 64 位元高熵 ASLR | ✅ |
| `DYNAMIC_BASE` | `0x0040` | ASLR（映像可重定基址） | ✅ |
| `NX_COMPAT` | `0x0100` | DEP（資料頁不可執行） | ✅（`0x20+0x40+0x100=0x160`） |
| `GUARD_CF` | `0x4000` | Control Flow Guard | ❌（mingw 不支援） |
| `FORCE_INTEGRITY` | `0x0080` | 強制簽章 | ❌ |

**關鍵觀察**：mingw 預設就給你 ASLR + DEP + 高熵，但 **`GUARD_CF` 是 0**——這就是為什麼 mingw 靶適合練 Part 3/4，但 Part 5 要 MSVC 才有 CFG 可打。

> 為什麼是 `0x160` 而不是別的值？因為這三個位元 `0x20|0x40|0x100` 剛好相加。之後你會常在 exploit 裡手動改這個欄位（例如 patch 掉靶的 ASLR 來簡化練習），所以把它當成一個可讀可改的旗標集合，不是黑盒。

### 用 dumpbin 看（MSVC 路徑，未實測）

MSVC 裝好後，`dumpbin` 給的資訊更完整，尤其是 CFG：

```bat
REM 未實測，MSVC 安裝後預期輸出
dumpbin /headers target.exe        REM 看 OPTIONAL HEADER 的 "DLL characteristics"
dumpbin /loadconfig target.exe     REM 看 "Guard Flags"、CFG function table
```

`/loadconfig` 會列出 `Guard Flags: ... CF Instrumented`、`Guard CF Function Table` 的位址與筆數——這是判斷一個目標「CFG 到底開了沒、保護了哪些 indirect call target」的權威來源，Ch 32 會逐欄位拆。

### 一個更好用的替代：winchecksec

社群工具 [`winchecksec`](https://github.com/trailofbits/winchecksec)（Trail of Bits）是 Windows 版的 `checksec`，一行看全部緩解：

```console
> winchecksec target.exe
# 未實測；輸出含 Dynamic Base / NX / GS / SEH / CFG / RFG / High Entropy VA 等欄位
```

裝一個備用，比記 `dumpbin` 欄位快。

## Part 3：除錯器——WinDbg 與 cdb

這是整套環境裡最重要、也最值得你花時間的工具。理由：**WinDbg 能直接讀 Microsoft public symbols**，於是你可以 `dt ntdll!_PEB` 把 PEB 結構的每個欄位、偏移、型別印出來——在 Linux 你要有 debug info 才辦得到，Windows 對系統 DLL 是官方免費提供的。這對「摸清 internals」是決定性的。

### 兩個形態：WinDbgX（GUI）與 cdb（命令列）

- **WinDbgX**（現代版 WinDbg，本機已有，位於 `WindowsApps\WinDbgX.exe`）：GUI，適合互動探索、時光旅行除錯（TTD，Ch 41）。
- **cdb.exe**：命令列版，**同一個除錯引擎（dbgeng）**，適合腳本化、自動化驗證。本課很多結構驗證會用 cdb 跑 `-c "指令;q"` 一次噴出結果。

cdb 來自 **Windows SDK 的「Debugging Tools for Windows」元件**（本機初始沒裝命令列版）。安裝：裝 Windows SDK 時勾「Debugging Tools for Windows」，或：

```powershell
winget install --id Microsoft.WinDbg -e   # 現代 WinDbgX；classic cdb 在 SDK 的 Debugging Tools 元件
```

裝好後 `cdb.exe` 通常在 `C:\Program Files (x86)\Windows Kits\10\Debuggers\x64\cdb.exe`。

### 設定 symbol path（一定要做，否則除錯器是瞎的）

沒有 symbols，你看到的 `ntdll` 全是位址；有了 symbols，你看到的是 `ntdll!RtlAllocateHeap`。設一次環境變數永久生效：

```powershell
# 讓除錯器自動從 Microsoft symbol server 下載並快取到 C:\symbols
[Environment]::SetEnvironmentVariable("_NT_SYMBOL_PATH",
  "srv*C:\symbols*https://msdl.microsoft.com/download/symbols", "User")
```

> **踩雷預告**：`_NT_SYMBOL_PATH` 沒設好是新手第一個坑。症狀是 `dt _PEB` 報 `Symbol not found`。設好後第一次載入會從網路下載（要通外網），之後走 `C:\symbols` 快取。

### 第一次掛除錯器（cdb 命令列示範，未實測）

```bat
REM 未實測（需 cdb 安裝）；本課後續結構驗證的標準用法
cdb -c "bp target!main; g; k; q" target.exe
```

拆解這串（對照你熟的 gdb）：

| cdb | gdb 對應 | 作用 |
|---|---|---|
| `bp target!main` | `break main` | 在 `main` 下中斷點 |
| `g` | `continue` | 執行 |
| `k` | `bt` | 印 call stack |
| `q` | `quit` | 離開 |

WinDbg 指令表 Ch 0 只給這幾個入門，完整的（`dt`、`!heap`、`!teb`、`dps`、`u`）散在後面各章第一次用到時教。

### x64dbg（互動打洞的手感之選）

[`x64dbg`](https://x64dbg.com/) 是開源的 Windows 使用者態除錯器，介面像 OllyDbg/x32dbg，**互動打 exploit 時手感比 WinDbg GUI 好**（記憶體視窗、下 breakpoint、看 stack 很直覺），還有 `ERC`/`xAnalyzer` 等 exploit 外掛。本課定位：**WinDbg 挖 internals（symbols 強），x64dbg 打洞當手感輔助**。本機尚未安裝，`winget install x64dbg.x64dbg` 或官網下載可攜版。

## Part 4：scripting 與 gadget 工具

### Python（本機已驗證）

```console
$ python --version
Python 3.12.7
```

三個常用套件：

```powershell
pip install pwntools     # 有 Windows 支援，process/remote/ELF-PE/shellcraft
pip install capstone keystone-engine   # 反組譯 / 組譯，寫 exploit 常用
```

很多結構驗證其實不必動除錯器——直接用 Python `ctypes` 呼叫 Win32 API 最快。先確認 ctypes 能打到 ntdll（Ch 5 會用它從 `GS:[0x60]` 拿 PEB）：

```python
# 真實可跑（純 Python + ctypes，不需編譯器）
import ctypes
ntdll = ctypes.WinDLL("ntdll")
print("ntdll loaded:", ntdll)   # 能載入就代表 ctypes 打 Win32 的路通了
```

> 上面這段只驗「ctypes 能載 ntdll」；真正從 `GS:[0x60]` 拿 PEB 的完整寫法留到 Ch 5，因為要先講 TEB/PEB 結構才不會是 magic number。

### gadget 搜尋

- **`rp++`**：跨平台 ROP gadget 搜尋器，吃 PE，速度快。
- **`ropper`**：Python 寫的，支援 PE/ELF/Mach-O，可做 gadget 語意搜尋。
- **`mona.py`**：Corelan 出的 WinDbg/Immunity 外掛，Windows exploit 開發的瑞士刀（算 offset、找 gadget、生 ROP chain、檢查模組防護）。Part 3 打 SEH/ROP 時是主力，Ch 21 正式介紹。

## 驗收：你的環境到位了嗎？

跑完這章，逐項確認（打勾表示本機已驗或你裝完能驗）：

```
[✅] mingw gcc 能編並執行 PE32+ x64（本機已驗）
[✅] objdump 能看 DllCharacteristics 緩解旗標（本機已驗）
[✅] Python 3.12 + ctypes 能載 ntdll（本機已驗）
[ ] cl.exe 能編（裝完 C++ workload 後：`cl /nologo hello.c`）
[ ] dumpbin /loadconfig 能看 Guard Flags（同上）
[ ] cdb 能掛上 target 並印 call stack（裝完 Debugging Tools 後）
[ ] _NT_SYMBOL_PATH 設好，`dt ntdll!_PEB` 能印出結構
```

## 踩雷集錦

1. **「我裝了 Visual Studio 就有 cl 了吧」**：錯。VS IDE 和 C++ 編譯器工作負載是分開的。沒勾「使用 C++ 的桌面開發」就沒有 `cl.exe`/`link.exe`/`dumpbin`。本機初始狀態正是如此。
2. **在一般 PowerShell 直接打 `cl` 說找不到**：`cl` 從不進系統 PATH，一定要透過 `vcvars64.bat` 或「x64 Native Tools Command Prompt」。這不是壞掉，是設計。
3. **`_NT_SYMBOL_PATH` 沒設，`dt _PEB` 報 symbol not found**：除錯器不會自己猜。設環境變數指向 Microsoft symbol server，第一次要能連外網下載。
4. **拿 mingw 練 CFG**：白費工。mingw 的 `DllCharacteristics` 永遠沒有 `GUARD_CF`(0x4000)，你怎麼編都不會有 CFG 可打。Part 5 一律換 MSVC。
5. **32 位元 vs 64 位元混淆**：Windows 上 x86(32) 與 x64 的 SEH、calling convention、ASLR 熵都不同。本課主線 x64，但 SEH overwrite 經典技法（Ch 21）在 x86 才成立——每章開頭會釘位元數，別搞混用錯除錯器架構（`cdb` vs `cdb -x86`）。

## 進階：再往深一層

- **對稱地建「乾淨靶環境」**：exploit 開發最怕 ASLR 讓位址每次都變。開發階段可用除錯器固定基址，或編譯時關 `/DYNAMICBASE`（`link /DYNAMICBASE:NO`）先做出「無 ASLR 版」把邏輯打通，再逐一把緩解加回來。這是本課反覆用的「逐層加防護」教學法。
- **TTD（Time Travel Debugging）**：WinDbg 的殺手級功能，把整段執行錄下來可倒帶。找 UAF「誰先 free 的」這種問題神快，Ch 41 專章。先知道它存在。
- **VM 快照**：打系統元件或做危險測試時，用 Hyper-V/VMware 開一台 Win11 VM 並存快照，打壞了還原即可。本課多數練習在本機跑無妨，但 Part 6/7 碰系統面時建議 VM。

## 本章重點整理

- Windows pwn 工具鏈四角色：**MSVC 造靶、dumpbin/winchecksec 驗防護、WinDbg/cdb 看記憶體、Python+gadget 工具打**。
- **MSVC 不可替代**：CFG/XFG/SafeSEH/GS 是 MSVC+link 專屬；mingw 只能打前站（Part 0–4）。
- **WinDbg 的核心價值是 public symbols**：`dt ntdll!_PEB` 直接透視系統結構，這是摸清 internals 的關鍵武器。
- 緩解狀態寫在 PE 的 `DllCharacteristics`（ASLR/DEP/高熵）與 Load Config（CFG），不是憑感覺。

## 自我檢核

- [ ] 不看表，能說出 Linux 的 `gcc`/`gdb`/`checksec`/`pwntools` 各自對應 Windows 的什麼工具，以及最關鍵的一個差異
- [ ] 能解釋為什麼 Part 5 一定要 MSVC 而不能用 mingw
- [ ] 知道 `DllCharacteristics = 0x160` 代表開了哪三個緩解、少了哪個
- [ ] 知道 `_NT_SYMBOL_PATH` 是幹嘛的、沒設會發生什麼
- [ ] 能說出 WinDbg 相對 x64dbg 的一個獨特優勢

## 延伸閱讀

### 官方文件

- **[Debugging Tools for Windows（WinDbg）— Microsoft Learn](https://learn.microsoft.com/en-us/windows-hardware/drivers/debugger/)**
  - **讀哪裡**：「Getting Started with WinDbg (User-Mode)」與「Common WinDbg Commands (Reference)」兩節；把 `bp`/`g`/`k`/`dt`/`dps` 這幾個先記熟
  - **和本章的關聯**：本章只給了入門幾個指令，這份是後續每章除錯操作的權威查表處

- **[Linker options（/GUARD、/DYNAMICBASE、/NXCOMPAT…）— MSVC 文件](https://learn.microsoft.com/en-us/cpp/build/reference/linker-options)**
  - **讀哪裡**：`/GUARD:CF`、`/DYNAMICBASE`、`/NXCOMPAT`、`/HIGHENTROPYVA`、`/SAFESEH` 各條
  - **和本章的關聯**：這些就是本章「造帶緩解的靶」用到的旗標；Part 5 打緩解前會再回來精讀

### 工具 / 專案

- **[winchecksec — Trail of Bits](https://github.com/trailofbits/winchecksec)**
  - **這是什麼**：Windows 版 `checksec`，一行列出 PE 的所有緩解狀態
  - **為什麼值得裝**：比背 `dumpbin` 欄位快，判斷靶開了什麼防護的第一手工具

- **[x64dbg 官方文件](https://help.x64dbg.com/en/latest/)**
  - **讀哪裡**：「GUI」與「Commands」概覽，先熟記憶體視窗與 breakpoint 操作
  - **為什麼值得讀**：互動打 exploit 時手感優於 WinDbg GUI，本課的手感輔助工具

### 部落格 / 教學

- **[Corelan — Exploit writing tutorial part 1（Stack Based Overflows）](https://www.corelan.be/index.php/2009/07/19/exploit-writing-tutorial-part-1-stack-based-overflows/)** — Peter Van Eeckhoutte
  - **這篇說什麼**：Windows exploit 開發的經典入門，同時帶你把 Immunity/WinDbg + mona 的環境架起來
  - **讀哪裡**：前半的環境與工具設定段落，正好對照本章；後半的實作留到 Part 3 再回來
  - **前提知識**：基本 x86 stack frame 概念（你已具備）

裝好 MSVC + cdb 後，下一章我們拉高視角：把「Windows pwn 到底和 Linux 差在哪」講成一張完整的地圖，讓你帶著正確的心智模型進 internals。

→ [Ch 1 — Windows pwn 為什麼和 Linux 不一樣：全景與天梯定位](./01-why-windows-pwn.md)
