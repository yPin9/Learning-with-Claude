# Ch 0 — 環境搭建：x86 靶 + WinDbg + IDA + mona

> **目標**：把一套能撐完整門課的 Windows x86 漏洞開發實驗室架好——偵錯器（WinDbg）、反組譯器（IDA）、exploit 開發外掛（mona v3）、練習靶（vulnserver），以及一台發 payload 的攻擊機。架完你能在 WinDbg 裡觸發一次 crash 並看到 `EIP` 被你的資料蓋掉。

> **環境**：本章以 **Windows 10 x64（22H2）** 當偵錯/靶機、**WinDbg（2026 winget 版）**、**mona v3**、**Python 3.9+**、**IDA Free 9.x 或 IDA Pro**、攻擊端用 **Kali / 任意 Linux + Python 3** 操作。版本差異處會特別標注。

## 為什麼先花一章搞環境？

Linux pwn 的實驗室幾乎是免費的：一台 WSL、`gcc`、`pwntools`、`gdb` 加 `pwndbg`，十分鐘搞定，而且大家的環境長得差不多。Windows exploit dev 不是這樣——**環境本身就是第一道關卡**，而且 OSED 考試現場用的工具鏈很固定：WinDbg 當偵錯器、mona 當自動化、IDA 當逆向。你在練習時用什麼，考試時最好就用什麼，否則考場上你會花寶貴時間跟工具打架而不是跟漏洞打架。

更重要的一個觀念要先講清楚，它會貫穿整門課：

> **OSED 的目標程式幾乎都是 32-bit（x86）。** 你的「偵錯機／靶機」作業系統可以是 x64 Windows 10，但你攻擊的**行程是 32 位的**。WinDbg 在 x64 系統上偵錯 32-bit 行程時，看到的是 **WoW64**（Windows-on-Windows 64）環境——這會帶來一些雷（例如兩套 `ntdll`、syscall 轉譯層），Ch 4、Ch 7 會遇到。先記住：**我們全程在 32-bit 的世界裡做 exploit**，暫存器是 `EAX/EBX/.../EIP/ESP`，指標 4 bytes。

這一章不講漏洞原理，純粹是把工具裝好、驗證好。但別跳過——後面每一章都預設這套環境就位。

## 實驗室拓撲：兩台機器

OSED 的工作模式是「攻擊機發 payload → 靶機上的服務崩潰 → 你在靶機的 WinDbg 裡看發生什麼」。所以最小配置是兩台：

```
┌─────────────────────────┐          TCP (exploit payload)         ┌──────────────────────────────┐
│   攻擊機 (Attacker)       │  ───────────────────────────────────► │   偵錯/靶機 (Debuggee)          │
│   Kali / Linux           │                                        │   Windows 10 x64 (22H2)        │
│   - Python 3 + socket     │                                        │   - 目標服務 (32-bit process)   │
│   - nc / msfvenom         │  ◄─────────────────────────────────── │   - WinDbg 附掛在服務上         │
│   - 寫 exploit.py 的地方   │          reverse shell 回連            │   - IDA 做靜態逆向             │
└─────────────────────────┘                                        └──────────────────────────────┘
```

- **攻擊機**：發 payload、收 reverse shell。Kali 最省事（`msfvenom`、`nc`、`python3` 都現成），但任何有 Python 3 的 Linux 甚至 Windows 都行。本課的 exploit 腳本一律用 **純 Python 3 + `socket`** 寫，不依賴 pwntools（OSED 考場習慣如此，且讓你看清每個 byte）。
- **偵錯/靶機**：一台 Windows 10 x64。服務跑在上面，WinDbg 也跑在上面附掛（attach）到服務。IDA 可以裝在這台，也可以把目標 binary 拷到別處逆向——逆向是靜態的，不需要跑起來。

> 用 VM 最好（VMware Workstation / VirtualBox / Hyper-V 皆可）：可以快照（snapshot），把裝好工具的乾淨狀態存起來，玩爛了一鍵還原。強烈建議裝完工具後立刻拍一個 snapshot。

為什麼不是一台機器全包？可以全包（攻擊腳本和靶跑在同一台 Windows 上，`127.0.0.1` 互打），初期這樣最快。但兩台的好處是**更貼近考試與真實**，而且 reverse shell 回連的網路行為更真實。初學先單機、熟了再拆兩台，都行。

## 第一步：偵錯/靶機的 Windows 設定

裝 Windows 10 x64（22H2 這一版本課全程使用；Win11 也可，但 OSED 歷史題材以 Win10 為主，行為更穩定可預期）。裝好後做幾件事：

1. **關掉 Windows Defender 的即時保護**。你會一直產生、傳輸 shellcode，Defender 會把它們當病毒刪掉，讓你以為是 exploit 寫錯。
   - 設定 → 隱私權與安全性 → Windows 安全性 → 病毒與威脅防護 → 管理設定 → 關閉「即時保護」。
   - 更徹底的做法（VM 內才建議）：用群組原則或 `Set-MpPreference -DisableRealtimeMonitoring $true`（需系統管理員 PowerShell）。

   > **這是刻意削弱安全設定，只在你自己的隔離 VM 裡做。** 絕對不要在日常使用的主機上關 Defender。這也呼應整個 repo 的定位：受控、授權、純學習環境。

2. **記下這台的 IP**：PowerShell 跑 `ipconfig`，記住 IPv4 位址（例如 `192.168.x.x`），攻擊腳本要連它。

3. **裝 Python 3（64-bit 與 32-bit 都要）**。這點容易踩雷，先說清楚原因：mona 需要 **pykd**，而 pykd 的位元數必須和你要偵錯的目標位元數一致——偵錯 32-bit 目標要用 **32-bit Python + 32-bit pykd**。Corelan 的安裝腳本會幫你把兩個版本都裝好（見下方 mona 段落），所以這步通常交給它。

## 第二步：裝 WinDbg（2026 版，不是 Store 那個）

這裡有個 2026 年的重要變化，務必用對：

> **WinDbg Preview 已經正式改名為「WinDbg」**，而且 **Microsoft Store 上那個版本不再更新了**。官方現在建議用 `winget` 或直接下載安裝器裝最新版。（來源見延伸閱讀）原本 Debugging Tools for Windows 附的舊版現在被稱為 **WinDbg (classic)**，本課不用它——它的 UI 停留在上個世代。

在偵錯/靶機的 PowerShell（系統管理員）跑：

```powershell
winget install Microsoft.WinDbg
```

> **未實測**：我無法在本機實際跑 winget 安裝 GUI 程式。這條指令來自 Microsoft 官方文件（2026 現行）。若 `winget` 找不到來源，先跑 `winget source update`；或到 Microsoft Learn 的「Install the Windows debugger」頁抓直接安裝器。

裝好後，從開始功能表開 **WinDbg**（新版圖示是深色的、UI 是 ribbon 風格）。第一次開先設兩樣東西：

**(a) 符號路徑（symbol path）**——讓 WinDbg 能從 Microsoft 符號伺服器下載 `ntdll`、`kernel32` 等系統模組的符號，你才看得到函式名而不是一堆位址。在 WinDbg 的命令列（下方那條輸入框）打：

```
.sympath srv*C:\symbols*https://msdl.microsoft.com/download/symbols
.reload /f
```

`.sympath` 設定路徑（`srv*本地快取目錄*伺服器 URL`），`.reload /f` 強制重新載入所有模組符號。第一次會下載一陣子。

**(b) 預設的反組譯語法與一些習慣**，後面章節會慢慢加。現在先會開、能設符號就夠。

### WinDbg 怎麼「附掛」到一個服務

OSED 的工作流是：服務先跑起來 → WinDbg 附掛上去 → 你發 payload → 服務在 WinDbg 裡崩潰。附掛的方法：

- **File → Attach to Process**（或命令列 `File → Start debugging → Attach to process`），選到目標行程。
- 附掛後，WinDbg 會讓行程「停住」（break-in）。按 `g`（go）或 F5 讓它繼續跑，它才會正常接收你的 payload。

這整套操作 Ch 4 會從零詳細帶，現在只要知道「附掛 → `g` 繼續 → 等 crash」這個節奏。

## 第三步：裝 mona v3（Corelan 的 exploit 開發神器）

mona 是一支 Python 外掛，把 exploit 開發裡一堆重複勞動自動化：產生 cyclic pattern 找 offset、掃壞字元、找 `jmp esp`、找 ROP gadget、產 ROP chain、產 egghunter……幾乎每一章都會用到它。

> **2026 重要更新**：**mona v3 於 2026 年 5 月發布**，同時支援 Python 2 和 3（官方建議用 Python 3），可在 **WinDbg、WinDbgX、Immunity Debugger** 上跑。它需要 **pykd**（一個讓 Python 能驅動 WinDbg 的橋）。舊版基於 PyKD 0.2.0.x + Python 2 的 mona 會被新版安裝腳本覆蓋，別混用。

最省事的裝法是用 Corelan 官方的安裝腳本，它會一次把 Python 3.9.13（32 + 64 位）和 pykd 都裝好：

```powershell
# 在偵錯/靶機，系統管理員 PowerShell
# 1) 取得 Corelan 的安裝腳本（CorelanPyKDInstall.ps1），依官方 repo 指示執行
#    它會裝 Python 3.9.13 (x86 + x64) 與 pykd
# 2) 取得 mona v3 本體，放到固定路徑，例如 C:\Tools\mona3\mona.py
```

> **未實測**：安裝腳本與路徑以 Corelan 官方 `mona` / `windbglib` repo 的現行 README 為準（連結見延伸閱讀）——版本細節（Python 3.9 vs 更新版、pykd-ext 版本）各頁面不一致，**裝之前務必看一次當下的官方 README**，不要照抄某篇舊教學。

在 WinDbg 裡載入與執行 mona：

```
.load pykd
!py -3.9 C:\Tools\mona3\mona.py help
```

- `.load pykd` 載入 pykd 橋（新版可能是 `!load pykd` 走 pykd-ext，依官方說明）。
- `!py -3.9 <path>\mona.py <command>` 用指定的 Python 3.9 跑 mona。之後你會常打 `!py mona ...` 這類簡寫（可設定別名，Ch 4 教）。

看到 mona 印出一長串 help 指令清單，就代表這條鏈（WinDbg ↔ pykd ↔ Python ↔ mona）通了。這是整個環境裡最容易卡的一環，通了就成功一大半。

## 第四步：裝 IDA（逆向用）

OSED 的靈魂是「逆向陌生 binary 找洞」（Part 4），工具用 IDA。

- **IDA Free 9.x**：免費，有反組譯（disassembler）與圖形檢視（graph view），足夠本課大部分逆向。新版 IDA Free 甚至附了雲端反編譯器的有限功能。
- **IDA Pro**：付費，有本地 Hex-Rays 反編譯器（F5 把組語變成類 C 虛擬碼），大幅加速逆向。OSED 官方教材用 IDA，有條件用 Pro 最舒服；沒有的話 Free + 手讀組語也能過——本課逆向章會同時示範「有反編譯器」和「只有反組譯」兩種讀法，不綁死你要付費。

> **其他替代**：Ghidra（免費、有反編譯器）也完全可用，本課的逆向觀念不綁工具。但 OSED 官方以 IDA 為準，練習時建議至少熟悉 IDA 的操作與快捷鍵，考場才不卡手。

裝好後開 IDA、隨便丟一個 `.exe` 進去，能看到 graph view 的函式方塊圖，就算就位。Ch 14 會從介面開始帶。

## 第五步：攻擊機 + 練習靶

**攻擊機**：Kali 或任意 Linux，確認有 `python3`、`nc`（netcat）。本課 exploit 腳本長這樣的骨架（Ch 7 會真正用到）：

```python
#!/usr/bin/env python3
import socket, sys

target_ip   = "192.168.x.x"   # 偵錯/靶機 IP
target_port = 9999

buf  = b"A" * 2000            # 之後換成真正的 payload

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.connect((target_ip, target_port))
s.send(buf + b"\r\n")
s.recv(1024)
s.close()
print("[+] payload sent")
```

**練習靶 vulnserver**：這是本課 Part 2–3 的主力靶，一支故意寫得漏洞百出的 TCP 服務，專門用來練 stack / SEH overflow。

```
# 在偵錯/靶機下載 vulnserver（stephenbradshaw/vulnserver，見延伸閱讀），
# 它是 32-bit，直接雙擊 vulnserver.exe 或命令列跑，預設聽 TCP 9999。
# 用攻擊機 nc 連過去試水溫：
nc 192.168.x.x 9999
# 連上後打 HELP，會列出一堆指令 (TRUN, GMON, GTER, ...)，各自藏著不同漏洞。
```

> **下載來路安全提醒**：vulnserver、mona 這類工具請從官方 repo（延伸閱讀的連結）取得，放進 VM 裡用。它們是**故意有漏洞**的教學程式，只在隔離環境跑，別放到會接觸真實網路/資料的機器上。

## 收尾驗證：架好了沒？

跑完下面這個「冒煙測試」，全過就代表環境就位，可以進 Ch 1：

1. 偵錯/靶機上 `vulnserver.exe` 跑起來，聽 9999。
2. WinDbg **Attach to Process** 掛上 `vulnserver.exe`，按 `g` 讓它繼續跑。
3. 攻擊機用上面的 Python 骨架，把 `buf` 設成 `b"TRUN ." + b"A" * 5000`，發出去。
4. WinDbg 應該**停下來**（break），顯示 access violation，而且暫存器區的 **`EIP` 變成 `41414141`**（`A` 的 ASCII 是 `0x41`）。

看到 `EIP=41414141` 那一刻——你已經控制了指令指標。這就是整門課要反覆放大的那個瞬間。

> **未實測**：上述流程我無法在本機實跑（需 GUI WinDbg + 靶機）。這是 vulnserver `TRUN` 指令廣為人知的 stack overflow 行為，Ch 7 會完整拆解並附真實 WinDbg 輸出的逐步說明。若你跑出來 `EIP` 不是 `41414141`，先別急，Ch 7–8 正是教你怎麼定位。

## 踩雷集錦

1. **裝成 x64 的 Python/pykd 去偵錯 32-bit 目標**：pykd 位元數要和目標行程一致。偵錯 32-bit 的 vulnserver 就要用 **32-bit** Python + pykd。Corelan 腳本兩種都裝就是為了這個——用錯位元 mona 會載入失敗或行為詭異。
2. **用了 Microsoft Store 的舊 WinDbg**：Store 版已不更新（2026）。用 `winget install Microsoft.WinDbg` 或官方安裝器，否則你會缺新功能、對不上現行教學。
3. **沒設符號路徑就開始偵錯**：沒有符號，WinDbg 裡全是裸位址，`kernel32!...` 這種可讀名稱都看不到，排錯難上加難。第一件事就是 `.sympath` + `.reload /f`。
4. **Defender 把你的 shellcode 吃掉**：exploit 寫對了卻「沒反應」，很多時候是 Defender 在 VM 裡悄悄刪了你的 payload/產物。隔離 VM 內關掉即時保護（別在主機關）。
5. **照抄過時教學的 mona/pykd 版本**：這塊版本變動大（Python 2→3、pykd→pykd-ext、mona v2→v3）。一定看**當下**的官方 README，不要照 2018 年的部落格一字一句敲。

## 進階：再往深一層

- **WinDbgX 的 TTD（Time Travel Debugging）**：新版 WinDbg 能錄下整段執行再前後回放，對分析「crash 之前發生了什麼」是神器。Ch 41（加深一圈）與 `windows_pwn` Ch 41 有深入；現在先知道有這東西。
- **自動化掛載**：每次手動 Attach 很煩。可以用 WinDbg 的命令列參數 `windbg.exe -o -g -G vulnserver.exe` 直接啟動並偵錯，或寫 `.cmdtree` 把常用 mona 指令做成按鈕。Ch 4 會教怎麼把重複操作變快。
- **pwntools 能不能用？** 能，但本課刻意用裸 `socket`。原因：OSED 考場重視你對每個 byte 的掌控，而且有時目標協定怪到 pwntools 的便利封裝反而礙事。練基本功時手寫，熟了再自己決定要不要上 pwntools。

## 本章重點整理

- OSED 全程打 **32-bit（x86）目標**，即使 OS 是 x64（WoW64 環境）。暫存器 `EIP/ESP/EAX...`，指標 4 bytes。
- 工具鏈四件套：**WinDbg**（偵錯，winget 裝 2026 新版）、**mona v3**（自動化，需 pykd，位元數對齊目標）、**IDA**（逆向）、**vulnserver + 攻擊機 Python**（練習靶）。
- 環境本身是 OSED 的第一道關卡；裝完立刻拍 VM snapshot。
- 冒煙測試看到 `EIP=41414141` 就算就位。

## 自我檢核

- [ ] 能說出為什麼偵錯 32-bit 目標要用 32-bit 的 Python/pykd
- [ ] 能在 WinDbg 裡設好符號路徑並 `.reload /f`
- [ ] 能把 WinDbg 附掛到一個跑著的服務、按 `g` 讓它繼續
- [ ] 能解釋為什麼要在隔離 VM 裡關 Defender、而不是在主機關
- [ ] 知道 mona 載入失敗時第一個要懷疑的是什麼（位元數 / pykd）

## 延伸閱讀

每條都說清楚讀哪裡、學什麼、前提。

### 官方文件

- **[Install the Windows debugger — Microsoft Learn](https://learn.microsoft.com/en-us/windows-hardware/drivers/debugger/)**
  - **讀哪裡**：安裝頁的 winget / 直接安裝器段落，以及符號設定（symbol path）說明。
  - **學什麼**：本章 WinDbg 安裝與 `.sympath` 的權威來源；版本/支援 OS 以這裡為準。
  - **注意**：同一份文件還列了 Store 安裝選項，但 Store 版已停更——以 winget/安裝器為準。

- **[the return of windbg — poppastring](https://www.poppastring.com/blog/the-return-of-windbg)**
  - **這篇說什麼**：說明 WinDbg Preview 改名為 WinDbg、Store 版不再更新的來龍去脈。
  - **為什麼值得讀**：確認本章「別用 Store 舊版」這個判斷的出處。

### mona / pykd（務必看現行版）

- **[Mona v3 Released — Corelan（2026-05）](https://www.corelan.be/index.php/2026/05/01/mona-v3-released/)**
  - **讀哪裡**：整篇，特別是支援的偵錯器、Python 版本、`!load pykd` 與 `!py -3.9 mona.py` 的用法。
  - **學什麼**：2026 現行 mona v3 的安裝與執行方式；本章 mona 段落的主要依據。
  - **前提**：先大致知道「外掛／直譯器橋接」的概念即可。

- **[corelan/mona（GitHub）](https://github.com/corelan/mona)** 與 **[corelan/windbglib（GitHub）](https://github.com/corelan/windbglib)**
  - **讀哪裡**：兩個 repo 的 README——`windbglib` 是 mona 在 WinDbg 上運作的 pykd 包裝層。
  - **學什麼**：最準確的安裝步驟、pykd 版本需求、CorelanPyKDInstall 腳本。
  - **為什麼值得讀**：版本細節在部落格各頁不一致，**repo README 是最終仲裁**。

### 練習靶

- **[vulnserver（GitHub）— Stephen Bradshaw](https://github.com/stephenbradshaw/vulnserver)**
  - **讀哪裡**：README 的指令列表（`TRUN`/`GMON`/`GTER`/`KSTET`...），每個指令對應不同漏洞類型。
  - **學什麼**：本課 Part 2–3 的主力靶；先知道哪個指令練哪種 overflow。
  - **注意**：故意有漏洞的程式，只在隔離 VM 跑。

### 方法論總覽（選讀，建立全局感）

- **[Corelan — Exploit Writing Tutorial part 1](https://www.corelan.be/index.php/2009/07/19/exploit-writing-tutorial-part-1-stack-based-overflows/)** — Peter Van Eeckhoutte
  - **這篇說什麼**：Windows stack overflow 從環境到第一個 exploit 的經典開篇。
  - **讀哪裡**：環境與工具段落先讀，漏洞細節留到 Ch 7 再回來對照。
  - **為什麼值得讀**：OSED 技法的源頭文獻，和本課 Part 2 直接對應。

環境就位後，下一章把「OSED 到底考什麼、怎麼評分、和你熟悉的 OSCP 差在哪」講清楚——先看懂考試的形狀，後面每一章你才知道自己在為什麼而練。

→ [Ch 1 OSED 是什麼：EXP-301 考綱與考試格式拆解](./01-osed-exam-overview.md)
