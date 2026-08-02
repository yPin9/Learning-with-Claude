# Ch 13 — Windows 記憶體鑑識入門

> 目標：理解為什麼 RAM 是入侵調查的第一現場；掌握記憶體擷取的方法與風險；能用 Volatility3 執行基礎進程分析，並說清楚 `windows.pslist.PsList` 和 `windows.psscan.PsScan` 的結構差異以及實戰意涵。
>
> 環境：Volatility3 2.x（`vol.py` 入口）；Windows 10/11 x64 memory image；symbol table 為 ISF JSON 格式（由 `symbols.zip` 解壓或 Volatility 自動下載）。

## 為什麼記憶體是寶庫

磁碟鑑識有個根本侷限：高明的攻擊者知道怎麼不落地。Fileless 攻擊把 shellcode 打進另一個進程的虛擬記憶體；mimikatz dump credential 之後立刻抹掉自身；Cobalt Strike beacon 從不寫磁碟，整個 C2 session 全在 heap 上。這些行為在磁碟上可能完全透明，但在記憶體裡，它們全都赤裸裸地活著——只要你夠快。

記憶體裡有什麼是磁碟沒有的：

- **明文憑證（plaintext credentials）**：Windows 的 LSASS（Local Security Authority Subsystem Service）進程的記憶體裡儲存 WDigest、Kerberos ticket、NTLM hash。mimikatz 就是讀這塊。你能讀，鑑識師也能讀。
- **注入的程式碼（injected code）**：Process injection 把攻擊者的 shellcode 打進合法進程（例如 `svchost.exe`）。這段程式碼不對應磁碟上任何 PE 檔，只在記憶體裡存在。
- **網路連線狀態（network state）**：開著的 socket、連線到 C2 的 IP/port，都掛在 OS 的 TCP 連線表上，只要進程活著就在記憶體裡。
- **隱藏進程（hidden processes）**：攻擊者可以把自己的進程從 OS 的進程鏈結串列（doubly linked list）上 unlink，讓 `tasklist` 和 Event Log 看不到。但進程的 EPROCESS 結構體仍然留在記憶體 pool 裡，pool scanning 抓得到。
- **加密金鑰（encryption keys）**：BitLocker、TLS、勒索軟體的 in-memory key 都只存在 RAM。

記憶體是「揮發性」的——機器關機一切就沒了。這也是為什麼 IR（Incident Response）的第一件事是「先 dump 記憶體再說」。

## 記憶體擷取

### 工具選擇

| 工具 | 特性 | 適用場合 |
|---|---|---|
| **WinPmem** | 開源，DumpIt 同作者後繼，支援 Win 10/11 | 首選，一般 IR |
| **DumpIt** | Magnet Forensics 出品，GUI + CLI，支援 live/hibernation | 客戶機器，非技術人員也能跑 |
| **Magnet RAM Capture** | 免費，Magnet 出品，輸出 raw/AFF4 | 法務現場 |
| **FTK Imager** | AccessData/Exterro，live RAM + disk image 一起做 | 綜合採證 |
| **VMware/Hyper-V snapshot** | Hypervisor 層暫停 VM 直接抓 .vmem / .bin | 雲端或 lab，最乾淨 |
| **AVML** | Microsoft 出品，Linux kernel 模組，非 Windows 用 | Linux 環境 |

WinPmem 的基本用法（在目標機器上以 Administrator 執行）：

```powershell
winpmem_mini_x64_rc2.exe memdump.raw
```

輸出格式預設為 raw（linear）；也可以輸出 AFF4（壓縮、帶 metadata）。

### 擷取風險

記憶體擷取**不是**完全無副作用的操作。你需要知道以下風險，否則會在法庭上或主管面前翻車：

1. **頁面置換（paging）**：OS 可能把某些頁面置換到 pagefile.sys，WinPmem 讀到的那頁可能已經不是最新狀態。Volatility 在找某些結構時會告訴你「page not present」。
2. **SMEP/VT-x 的影響**：現代 Hypervisor 可能攔截驅動存取某些記憶體範圍，導致擷取不完整。
3. **擷取期間狀態改變**：擷取需要數秒到數分鐘，這段時間記憶體內容持續變動。擷取的是一個「快照加近似值」，不是真正的原子快照（除非你是在 Hypervisor 層停機再讀）。
4. **Smear（塗抹效應）**：高速處理器的記憶體在你讀取 4GB 的同時，前幾 MB 的內容可能已被 OS 覆寫，導致結構跨頁不一致。
5. **防毒軟體干擾**：某些 EDR/AV 可能攔截 WinPmem 的驅動載入，或汙染記憶體內容作為欺騙機制。
6. **法律鏈保管（chain of custody）**：擷取前先記錄 hash（MD5 + SHA256），擷取後再記錄一次。如果要走法律程序，沒有 hash 就沒有鑑識效力。

WinPmem 完成後計算 hash：

```powershell
Get-FileHash memdump.raw -Algorithm SHA256
```

## Volatility3 架構

Volatility 2（Vol2）和 Volatility 3（Vol3）在架構上有根本差異，很多網路教程混著講，實際操作時會踩雷。

### Symbol Table / ISF

Vol2 靠「profile」——一個針對特定 Windows 版本編譯的 Python 物件，內含 kernel struct 的 offset。Vol3 改用 **Intermediate Symbol File（ISF）**：一個 JSON 格式的 symbol table，描述 kernel symbols 的型別、大小、field offset。

ISF 從哪來？

1. **自動下載**：Vol3 在分析時如果沒有對應 symbol，會嘗試從 `https://isf-server.techanarchy.net` 下載（需要網路）。
2. **手動生成**：用 `dwarf2json` 搭配目標系統的 kernel debug symbol（`ntoskrnl.pdb`）自己生。
3. **Volatility Foundation 的 symbols.zip**：離線使用時下載解壓放到 `volatility3/symbols/windows/` 目錄下。

Vol3 plugin 的命名規則是 `<platform>.<module>.<ClassName>`，例如 `windows.pslist.PsList`。這和 Vol2 的 `--plugin=pslist` 完全不同。

### 基本執行格式

```bash
python3 vol.py -f memdump.raw <plugin_name> [plugin_options]
```

Vol3 不需要指定 `--profile`；它從 image 裡自動偵測 OS 版本並選對應 symbol table。如果自動偵測失敗，用 `--isf` 手動指定。

### 基礎 Plugin：windows.info

```bash
python3 vol.py -f memdump.raw windows.info
```

這是第一步。輸出（示意輸出，實際依 image/版本而異）：

```
Variable          Value
----------------  ------------------------------------
Kernel Base       0xf8045e200000
DTB               0x1aa000
Symbols           file:///volatility3/symbols/windows/...
Is64Bit           True
IsPAE             False
layer_name        0 WindowsIntel32e
memory_layer      1 FileLayer
KdVersionBlock    0xf8045ea07398
Major/Minor       15.19041
MachineType       34404
SystemTime        2024-06-15 14:23:07
NtSystemRoot      C:\Windows
NtProductType     NtProductWinNt
NtMajorVersion    10
NtMinorVersion    0
...
```

`windows.info` 給你：OS 版本、kernel base address、DTB（Directory Table Base，即 CR3 的值，是 page table 的根）、系統時間。時間是 UTC，記得換算。

## EPROCESS 結構與進程鏈結串列

要理解後面的 plugin 為什麼抓到的進程不一樣，必須先懂 Windows kernel 的進程管理結構。

### EPROCESS 與 ActiveProcessLinks

Windows 用 `EPROCESS` 結構體描述每個進程。這個結構體很大（Windows 10 上超過 2KB），包含 PID、進程名稱、虛擬記憶體描述、security token 等所有進程相關資訊。

關鍵的部分是裡面的一個 field：`ActiveProcessLinks`，型別是 `LIST_ENTRY`（雙向鏈結串列節點）。OS 把所有活著的進程的 EPROCESS 用這個 field 串成一個環狀雙向鏈結串列（circular doubly linked list）：

```
System EPROCESS
  │ ActiveProcessLinks.Flink ──────────────────────────────────────────┐
  │ ActiveProcessLinks.Blink ◄────────────────────────────────────┐   │
  └────────────────────────────────────────────────────────────   │   │
                                                                  │   ▼
smss.exe EPROCESS                               csrss.exe EPROCESS
  │ ActiveProcessLinks.Flink ──────────────────► │ ActiveProcessLinks.Flink ──► ...
  │ ActiveProcessLinks.Blink ◄─────────────────── │ ActiveProcessLinks.Blink
```

`PsActiveProcessHead` 是這個鏈結串列的頭，Volatility 的 `windows.pslist.PsList` 就是從這個頭出發，沿著 Flink/Blink 走一圈把所有 EPROCESS 列出來。

### 攻擊者的 DKOM（Direct Kernel Object Manipulation）

攻擊者知道這個結構，所以他們做 **DKOM**：直接在 kernel 記憶體裡把自己的 EPROCESS 的 `ActiveProcessLinks` 從鏈結串列上摘除——把前一個節點的 Flink 指向後一個節點，把後一個節點的 Blink 指向前一個節點。

結果：這個進程從 `PsActiveProcessHead` 的遍歷路徑上消失了。`tasklist`、Task Manager、`NtQuerySystemInformation` 全都看不到它。但它的 EPROCESS 結構體本體仍然在記憶體裡，進程仍然在跑。

```
攻擊前：
EPROCESS_A ◄──► EPROCESS_malware ◄──► EPROCESS_B

DKOM 後：
EPROCESS_A ◄──────────────────────────► EPROCESS_B
                EPROCESS_malware（孤立，從鏈結串列消失，但還在記憶體裡）
```

## windows.pslist.PsList vs windows.psscan.PsScan

這是記憶體鑑識裡最重要的一對 plugin 對比。

### windows.pslist.PsList

走 `ActiveProcessLinks` 鏈結串列。

```bash
python3 vol.py -f memdump.raw windows.pslist.PsList
```

輸出（示意輸出，實際依 image/版本而異）：

```
PID    PPID   ImageFileName   Offset(V)          Threads  Handles  SessionId  Wow64  CreateTime                ExitTime
4      0      System          0xc5018f281040     163      -        -          False  2024-06-15 10:01:02.000000  -
88     4      Registry        0xc5018f40d080     4        -        -          False  2024-06-15 10:01:02.000000  -
344    4      smss.exe        0xc501901f7080     2        -        -          False  2024-06-15 10:01:03.000000  -
464    456    csrss.exe       0xc50190b3a300     11       -        0          False  2024-06-15 10:01:04.000000  -
...
4872   3456   svchost.exe     0xc5019f823080     8        -        0          False  2024-06-15 13:45:12.000000  -
```

優點：速度快，輸出乾淨。  
弱點：被 DKOM 的進程不會出現，也看不到已退出的進程（其 EPROCESS 可能還在 pool 裡但已從鏈結串列移除）。

### windows.psscan.PsScan

不走鏈結串列，改做 **pool scanning（記憶體池掃描）**。

Windows kernel 用 pool allocator 管理 kernel 物件的記憶體。每個 EPROCESS 在 `NonPagedPool`（或 `NonPagedPoolNx`）分配，分配時會在 pool header 標記一個 **pool tag**：EPROCESS 的 tag 是 `Proc`（`0x636f7250`）。

`windows.psscan.PsScan` 掃描整個 kernel 記憶體，找所有帶 `Proc` tag 的 pool chunk，然後把找到的結構體當 EPROCESS 解析。

```bash
python3 vol.py -f memdump.raw windows.psscan.PsScan
```

優點：
- 抓到被 DKOM unlink 的隱藏進程
- 抓到已退出但 EPROCESS 尚未被 free 的進程（有 ExitTime 的那些）

弱點：
- 速度慢（要掃整個記憶體）
- False positive：記憶體裡可能有舊的 pool chunk 碎片，被誤認為 EPROCESS
- 某些欄位（例如 handle table）可能已被部分覆寫，導致解析出雜訊

### 差異對照

| 面向 | windows.pslist.PsList | windows.psscan.PsScan |
|---|---|---|
| 遍歷方式 | `ActiveProcessLinks` 鏈結串列 | kernel pool tag `Proc` 掃描 |
| 速度 | 快 | 慢 |
| 能抓 DKOM 隱藏進程 | 否 | 是 |
| 能抓已退出進程 | 否 | 是（ExitTime 非空） |
| False positive 風險 | 低 | 有（碎片誤判） |
| 實戰策略 | 先跑，看整體 | 比對 PsList，找差異 |

**實戰操作**：把兩個 plugin 的輸出都存下來，用 PID 做 diff。出現在 `PsScan` 但不在 `PsList` 的進程，就是你需要盯的目標。

```bash
python3 vol.py -f memdump.raw windows.pslist.PsList > pslist.txt
python3 vol.py -f memdump.raw windows.psscan.PsScan > psscan.txt
# 提取 PID 欄位做比對
grep -oP '^\d+' pslist.txt | sort > pids_list.txt
grep -oP '^\d+' psscan.txt | sort > pids_scan.txt
comm -13 pids_list.txt pids_scan.txt   # 在 scan 有但 list 沒有的
```

## windows.pstree.PsTree

`windows.pstree.PsTree` 用縮排樹狀格式展示進程的親子關係（parent-child relationship）：

```bash
python3 vol.py -f memdump.raw windows.pstree.PsTree
```

輸出（示意輸出，實際依 image/版本而異）：

```
PID    PPID   ImageFileName       Offset(V)          Threads  Handles  SessionId  Wow64  CreateTime
4      0      System              0xc5018f281040     163      -        -          False  2024-06-15 10:01:02
* 88   4      Registry            0xc5018f40d080     4        -        -          False  2024-06-15 10:01:02
* 344  4      smss.exe            0xc501901f7080     2        -        -          False  2024-06-15 10:01:03
** 464 344    csrss.exe           0xc50190b3a300     11       -        0          False  2024-06-15 10:01:04
** 472 344    wininit.exe         0xc50190b5c080     1        -        0          False  2024-06-15 10:01:04
*** 568 472   services.exe        0xc50190c2a080     5        -        0          False  2024-06-15 10:01:04
**** 692 568  svchost.exe         0xc50190d8b080     14       -        0          False  2024-06-15 10:01:05
**** 768 568  svchost.exe         0xc50190e9a080     10       -        0          False  2024-06-15 10:01:05
...
```

為什麼樹狀圖比列表更有用：PPID spoofing 攻擊會把惡意進程的 PPID 設定成 `explorer.exe` 或 `svchost.exe` 等合法父進程，讓它在列表裡看起來無害。但在樹狀圖裡，你一眼就能看到：

- `svchost.exe` 的父進程不是 `services.exe`？異常。
- `powershell.exe` 的父進程是 `winword.exe`（Word）？高度可疑，可能是 macro 執行的結果。
- `cmd.exe` 或 `wscript.exe` 的父進程是奇怪的 PID？追下去。

## 具體範例與邊界情況

### 範例 1：正常進程樹 vs 異常 PPID

正常的 Windows 進程樹（父子關係應該要符合這些）：

```
System (PID 4)
  └─ smss.exe
       ├─ csrss.exe（每個 session 一個）
       └─ wininit.exe（Session 0）
            └─ services.exe
                 └─ svchost.exe（很多個）
explorer.exe（使用者登入後由 userinit.exe 啟動）
  └─ 使用者應用程式（chrome.exe, notepad.exe ...）
```

如果你看到 `svchost.exe` 的父進程是 `notepad.exe`（PID 9999），而 `notepad.exe` 是個你不認識的進程——就是問題所在。

### 範例 2：已退出進程

```bash
python3 vol.py -f memdump.raw windows.psscan.PsScan | grep -v "^PID" | awk '$8 != "N/A" {print $0}'
```

（示意輸出，實際依 image/版本而異）：ExitTime 欄位非空的，是已退出的進程。如果你看到一個 PID 在 PsList 裡沒有，但在 PsScan 裡有且 ExitTime 是在事件時間點前後，值得深挖：它可能是攻擊者的 stager，用完就退出了。

### 範例 3：PsList 和 PsScan 都找不到

如果攻擊者用的是 **kernel-level rootkit**，不只 unlink EPROCESS，還把 pool tag 改掉（或用 physically backed 的 stealthed allocation），那兩個 plugin 都找不到。這時候要靠其他 artifact（網路連線指向陌生 IP 但找不到對應 PID、VAD 發現 RWX 記憶體但沒有 module 對應）來發現異常。這是 Ch 14 的內容。

### 邊界：Wow64 進程

`Wow64`（Windows 32-bit on Windows 64-bit）進程在 64 位元系統上跑 32 位元程式。PsList 的 Wow64 欄位為 `True` 代表這個進程是 32 位元的。這會影響 DLL 路徑（`SysWOW64` 而非 `System32`）和記憶體佈局，做 DLL 分析時要注意。

## 踩雷清單

1. **Vol2 plugin 名稱搬到 Vol3 會失敗**：在網路上看到 `volatility --profile=Win10x64 --plugin=pslist` 是 Vol2 語法。Vol3 用 `python3 vol.py -f ... windows.pslist.PsList`。profile 名稱那套完全作廢。

2. **PsScan 輸出有垃圾列**：pool scanning 的 false positive 很常見。PsScan 輸出裡，如果 PPID 是 0（不是 `System`）、CreateTime 在 1970 年代、ImageFileName 是亂碼——這些都是記憶體碎片誤判，直接忽略。

3. **時間是 UTC 不是本機時間**：windows.info 的 `SystemTime` 和 PsList 裡的 `CreateTime` 都是 UTC。受害機器如果在 UTC+8 台灣，所有時間要加 8 小時才對應當地事件時間。timeline 分析時統一用 UTC，不要混著換算。

4. **Symbol table 下載失敗就不能分析**：如果你的分析機器沒有網路（air-gap），必須先下載 symbols.zip 或用 `dwarf2json` 生成 ISF，否則 Vol3 跑什麼都報 `Unsatisfied requirement`。備好離線包。

5. **記憶體 image 要對應 OS 版本**：Windows 10 20H2 和 Windows 11 22H2 的 EPROCESS 結構 field offset 可能不同。如果 symbol table 不符合 image 的 OS build，plugin 輸出會是錯的或直接 crash。`windows.info` 先跑，確認 build number 再決定要用哪個 symbol。

## 進階延伸

- **VAD（Virtual Address Descriptor）**：每個進程的虛擬記憶體被 OS 用 VAD tree（平衡樹）管理。`windows.vadinfo.VadInfo` 列出每個進程所有的 VAD 節點，包括映射的檔案、保護屬性（RWX）、類型（private/mapped/image）。找注入程式碼的關鍵工具，Ch 14 深挖。

- **KASLR（Kernel Address Space Layout Randomization）**：現代 Windows 的 kernel 基底位址每次開機都不一樣。Vol3 透過 ISF 和幾個 kernel 固定 signature 定位 kernel base，不需要你手動算。但如果 image 有擷取問題（開頭幾 MB 損壞），偵測會失敗。

- **Hibernation file（hiberfil.sys）分析**：Win 10 的休眠會把記憶體壓縮寫到 hiberfil.sys。Vol3 可以直接分析 hiberfil.sys（指定 `-f hiberfil.sys` 即可），裡面的資料跟 live dump 質量差不多，適合無法做 live capture 的離線分析。

- **Timeline Fusion**：記憶體裡的 CreateTime/ExitTime，搭配 `$MFT` 的 $STANDARD_INFORMATION 時間、Event Log 的 Process Create（Event ID 4688），三個來源交叉比對。任何一個的時間異常都是破口，Ch 15-16 繼續補。

## 本章重點整理

- RAM 是 fileless 攻擊、credential dump、網路連線、隱藏進程唯一留痕的地方
- 記憶體擷取的工具（WinPmem/DumpIt）、格式（raw/AFF4）、風險（smear/paging/AV干擾/chain of custody）
- Vol3 用 ISF symbol table 取代 Vol2 的 profile，plugin 命名格式完全不同
- EPROCESS.ActiveProcessLinks 是進程鏈結串列的節點；DKOM 攻擊把自己從這個串列上摘掉
- `windows.pslist.PsList` 走鏈結串列，快但漏掉 DKOM 進程
- `windows.psscan.PsScan` 掃 pool tag `Proc`，慢但能抓隱藏/已退出進程
- `windows.pstree.PsTree` 看親子關係，識別 PPID spoofing
- 實戰策略：PsList 和 PsScan 取差集，不在 PsList 裡的就要深挖

## 自我檢核

- [ ] 我能說出為什麼 fileless 攻擊在磁碟鑑識上是盲區，但記憶體鑑識看得到
- [ ] 我能解釋 EPROCESS.ActiveProcessLinks 的結構，以及 DKOM 怎麼讓進程從系統視圖消失
- [ ] 我能說出 PsList 和 PsScan 各自的遍歷方式、優缺點，以及怎麼互補使用
- [ ] 我能說出 pool tag `Proc` 是什麼，以及 PsScan 為什麼有 false positive
- [ ] 我能解釋 Vol3 的 ISF symbol table 取代 Vol2 profile 的意義
- [ ] 我知道記憶體擷取的主要風險，以及 chain of custody 需要做什麼

## 延伸閱讀

1. **《The Art of Memory Forensics》** Ch 6–7（Windows Processes and Threads）：EPROCESS 的完整結構逐欄解析，ETHREAD、PEB、VAD 的關係圖。記憶體鑑識的必讀一手資料，比這章深一個量級。

2. **Volatility3 官方文件** — [https://volatility3.readthedocs.io](https://volatility3.readthedocs.io)：Plugin 列表、ISF 產生流程、`dwarf2json` 使用方式。跑不動 plugin 先查這裡，不要憑記憶猜 flag 名稱。

3. **SANS FOR508 課程材料**（特別是 Memory Forensics Cheat Sheet）：SANS 的速查表把常用 Vol3 plugin 和它們的目的整理成一頁，貼在桌面旁邊用。FOR508 是業界標準的 DFIR 認證課程，這門課的架構很大程度參考它。

4. **「Windows Internals, Part 1」** — Russinovich et al.，Ch 3（Processes and Jobs）：EPROCESS 欄位的官方定義，Windows 記憶體管理的 canonical reference。搭配《Art of Memory Forensics》一起讀，兩個視角互補。

5. **"DKOM Rootkits" — Greg Hoglund & Jamie Butler（2005, Black Hat）**：雖然古老，但 DKOM 的原理沒變。看完你會理解為什麼 PsScan 要掃 pool tag 而不是信任鏈結串列。

---

Ch 14 把記憶體鑑識推進一層：當你找到可疑進程，怎麼確認它是注入了 shellcode、做了 process hollowing、還是 reflective DLL loading？malfind、ldrmodules、VAD 不一致怎麼解讀？

→ [Ch 14 記憶體鑑識進階：注入與 hollowing 偵測](./14-windows-memory-forensics-advanced.md)
