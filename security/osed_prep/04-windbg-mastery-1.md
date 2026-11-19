# Ch 4 — WinDbg 精通 I：命令體系、斷點、記憶體與符號

> **目標**：把 WinDbg 從「開得起來」練到「能當手術刀用」——會讀暫存器與記憶體、下各種斷點、查符號與模組、走堆疊，並建立一套能貼在 gdb 經驗上的指令對照。這章結束你能把 WinDbg 當成和 `gdb+pwndbg` 同級的順手工具。

> **環境**：Win10 x64 22H2 + WinDbg（2026 winget 版）+ mona v3 + Python 3，偵錯對象為 32-bit 行程（WoW64）。

## 為什麼要先把偵錯器練熟？

OSED 的每一個動作最後都落在一句話：「我送了這個，程式在偵錯器裡發生了什麼？」你會花在 WinDbg 裡的時間，遠多於寫 exploit 腳本的時間。偵錯器不熟，你的瓶頸就不是漏洞難不難，而是「我剛剛那個 crash 到底 crash 在哪、EIP 被什麼蓋掉、我的 shellcode 落在哪個位址」這種本該兩秒看出來的事，卻要摸五分鐘。

從 Linux 過來的人常有個錯覺：「`gdb` 我很熟，WinDbg 不就是另一個 `gdb`？」對，也不對。概念一樣（斷點、單步、看記憶體、看暫存器），但**命令語法是另一套體系**，而且 WinDbg 的命令分三類前綴，不懂這個分類你會覺得指令很亂。先把分類搞懂，其餘都是查表。

## 先建立直覺：WinDbg 的三類命令

WinDbg 的命令照「問誰」分成三種，看前綴就知道：

```
┌─────────────────────────────────────────────────────────────┐
│  一般命令（無前綴）     問「被偵錯的程式」                      │
│    g, p, t, r, u, dd, bp, k ...   → 執行控制、看暫存器/記憶體  │
├─────────────────────────────────────────────────────────────┤
│  點命令（. 開頭）       問「WinDbg 自己」                       │
│    .sympath, .reload, .cls, .load, .formats   → 設定偵錯器     │
├─────────────────────────────────────────────────────────────┤
│  擴充命令（! 開頭）     問「擴充 DLL / 外掛」                   │
│    !address, !teb, !peb, !exchain, !py(mona)   → 進階分析      │
└─────────────────────────────────────────────────────────────┘
```

記住這條分類，看到 `.reload` 你就知道是「叫 WinDbg 重載符號」，看到 `!exchain` 你就知道是「某個擴充幫我分析 SEH 鏈」。三類混在一行 help 裡時，前綴就是你的地圖。

## 核心一：看暫存器與記憶體

**暫存器**用 `r`：

```
r                  ; 列出所有暫存器（32-bit 行程會看到 eax/ebx/.../eip/esp/ebp + efl）
r eax              ; 只看 eax
r eip              ; 看指令指標——crash 後第一個要看的
```

crash 當下，你最在意的就是 `r eip`。如果印出 `eip=41414141`，恭喜，你用 `A`（`0x41`）蓋到了返回位址。

**記憶體 dump** 是一組 `d*` 命令，差在「以什麼單位/格式顯示」：

```
dd <addr>          ; dword（4 bytes）為單位，16 進位。看 stack、看位址最常用
dd esp             ; dump 目前 stack 頂端往下一串 dword
db <addr>          ; byte 為單位，右邊附 ASCII。看 shellcode bytes、找壞字元用
dc <addr>          ; dword + ASCII 混合
da <addr>          ; 把該位址當 ASCII 字串印
du <addr>          ; 把該位址當 Unicode（寬字元）字串印
dps <addr>         ; dump 指標並嘗試把每個值解析成符號（看函式指標/回傳位址鏈很好用）
```

> **為什麼有這麼多 d 命令？** 因為同一段記憶體可以是「一串位址」「一段機器碼」「一個字串」，你得告訴偵錯器「我現在想把它當什麼看」。找壞字元時你要逐 byte 比對，用 `db`；追 stack 上的返回位址鏈時你要看 dword 和符號，用 `dd`/`dps`。

**反組譯**用 `u`：

```
u eip              ; 從 eip 往下反組譯幾條指令
u <addr> L20       ; 從 addr 反組譯 0x20 條
uf <addr>          ; 反組譯「整個函式」（unassemble function）
```

**運算式求值**用 `?`（一般）和 `??`（C++ 語法）：

```
? esp+0x10         ; 算出 esp 加 0x10 的位址（16 進位）
? 41414141         ; 進位轉換：印出它的 10 進位、字元等
.formats 0x41414141 ; 把一個值用所有格式（hex/dec/char/binary/時間…）攤開——找壞字元/算值超好用
```

> **未實測**：本章所有 WinDbg 輸出都無法在本機實跑（需 GUI WinDbg + 靶行程）。上述命令語法來自 WinDbg 官方命令參考（見延伸閱讀）。請在你自己的環境附掛 vulnserver 後，逐條敲一次確認輸出形狀——這是建立肌肉記憶最快的方法。

## 核心二：下斷點

斷點是偵錯的骨架。WinDbg 有兩種本質不同的斷點：

**軟體斷點 `bp`**——在某位址寫入 `0xCC`（int3）攔截執行：

```
bp 0x401000              ; 在絕對位址下斷
bp kernel32!WinExec      ; 在某個 API 下斷（符號解析）
bp vulnserver+0x1740     ; 在「模組基址 + 偏移」下斷——ASLR 下最常用這種
bl                       ; 列出所有斷點（list）
bc 0                     ; 清除 0 號斷點（clear）；bc * 清全部
bd 0 / be 0              ; 停用 / 啟用（disable/enable）
```

> **為什麼常用「模組+偏移」而不是絕對位址？** 因為 ASLR 下模組每次載入的基址會變，但「函式在模組內的偏移」是固定的。你在 IDA 看到的位址是以預設 ImageBase 算的 RVA，下斷時用 `模組名+偏移`，WinDbg 會自動加上這次的實際基址——Ch 17 會把這個換算講透。

**硬體斷點 `ba`**——用 CPU 的 debug register，可攔「存取某記憶體」而不只是「執行某位址」：

```
ba r4 <addr>       ; 當有人「讀」這 4 bytes 時斷（read）
ba w4 <addr>       ; 當有人「寫」這 4 bytes 時斷（write）—— 追「誰改了我的資料」神器
ba e1 <addr>       ; 當執行此位址時斷（execute，1 byte）
```

> 硬體斷點數量有限（通常 4 個），但 `ba w4` 在「找出是哪段 code 覆寫了我的 SEH / 返回位址」時無可取代——這在 Ch 11–13 的 SEH 分析會用到。

## 核心三：執行控制

```
g                  ; go，繼續執行（crash 或命中斷點才停）
p                  ; step over，單步但跨過 call（執行完整個函式）
t                  ; trace / step into，單步進入 call
pa <addr>          ; 執行到某位址（step to address）
gu                 ; go up，執行到目前函式返回（相當於 gdb 的 finish）
```

對照 gdb：`g`≈`continue`、`p`≈`next`(ni)、`t`≈`step`(si)、`gu`≈`finish`。

## 核心四：符號、模組、堆疊

**模組列表 `lm`**——看載入了哪些 DLL、各自基址：

```
lm                 ; 列出所有模組（基址、結束、名稱）
lm m vuln*         ; 只看名稱符合的
lmv m kernel32     ; verbose，看版本、路徑、符號狀態
```

**符號查詢 `x`** 與 **位址轉符號 `ln`**：

```
x kernel32!*Virtual*   ; 列出 kernel32 裡名稱含 Virtual 的符號（找 VirtualProtect 用）
ln <addr>              ; 這個位址最接近哪個符號？（crash 在哪個函式附近）
```

**堆疊回溯 `k`**：

```
k                  ; call stack（誰呼叫誰）
kv                 ; 加上 frame 細節
kb                 ; 加上前三個參數
```

> crash 後如果 `k` 印出一堆 `????`，通常代表堆疊已被你的 overflow 砸爛到連回溯都做不出來——這本身就是「我蓋到返回位址了」的強訊號。

## 範例：一次典型的 crash 檢視流程

假設你附掛 vulnserver、送了過長的 `TRUN` payload，WinDbg 停下來報 access violation。你會這樣一路看下去（示意流程，標未實測）：

```
0:004> r eip
eip=41414141                         ; 控到返回位址了

0:004> dd esp                        ; 看 stack 頂，你的資料在不在這附近
0012f1f0  41414141 41414141 41414141 ...

0:004> db esp L40                    ; 逐 byte 看，確認 pattern / 有無壞字元截斷

0:004> ln eip                        ; eip=41414141 當然沒有符號，但真實 gadget 位址時這很有用

0:004> k                             ; 堆疊大概是一片 ???? —— 砸爛的正常現象
```

> **未實測，理論預期**：以上是 vulnserver `TRUN` 溢位的典型表現（Ch 7–8 完整拆解）。你的 `esp` 位址、pattern 落點會因環境而異——重點是這套「看 eip → 看 esp 內容 → 逐 byte 確認」的動作順序，而非具體數字。

## 進階：把重複操作變快

每次手動附掛、設符號、下同一組斷點很煩。幾個加速手段：

1. **命令列直接啟動偵錯**：
   ```
   windbgx.exe -o -g -G -c ".sympath srv*C:\symbols*https://msdl.microsoft.com/download/symbols; .reload" vulnserver.exe
   ```
   `-o` 跟進子行程、`-g` 忽略初始斷點、`-G` 結束時不斷。（參數以官方文件為準，標未實測。）

2. **別名（alias）**：把常打的 mona 指令設成短別名：
   ```
   as !mona !py -3.9 C:\Tools\mona3\mona.py
   ```
   之後 `!mona rop` 就等於那串長命令（實際 mona 載入語法以其現行 README 為準）。

3. **`.cmdtree`**：載入一個把常用命令做成按鈕的樹狀面板，考試時點一點就下好一組斷點。

## 對比與取捨：WinDbg ↔ gdb/pwndbg 速查

| 動作 | gdb / pwndbg | WinDbg |
|---|---|---|
| 看所有暫存器 | `info registers` | `r` |
| 看 EIP/RIP | `p $eip` | `r eip` |
| dump dword | `x/8wx $sp` | `dd esp` |
| dump byte+ASCII | `x/64bx $sp` | `db esp L40` |
| 反組譯 | `disassemble` / `x/10i` | `u eip` / `uf` |
| 位址下斷 | `break *0x401000` | `bp 0x401000` |
| 函式下斷 | `break WinExec` | `bp kernel32!WinExec` |
| 硬體寫斷點 | `watch *addr` | `ba w4 addr` |
| 繼續 | `continue` | `g` |
| step over / into | `ni` / `si` | `p` / `t` |
| 執行到返回 | `finish` | `gu` |
| call stack | `bt` | `k` |
| 看模組 | `info sharedlibrary` | `lm` |

> 最大的心態調整：gdb 的語法圍繞 `x/FMT`，WinDbg 把「格式」拆成不同命令字母（`dd`/`db`/`da`/`du`）。一旦接受「命令字母就是格式」，你查表幾次就順了。

## 踩雷集錦

1. **「`r eip` 看到的位址以為是絕對、拿去 IDA 對不上」**：錯誤直覺是「偵錯器的位址=IDA 的位址」。正確認識：IDA 預設顯示 ImageBase 相對的 RVA，WinDbg 顯示的是這次載入的實際 VA（含 ASLR 偏移）。對照前要先 `lm` 查這次的模組基址做換算（Ch 17 詳解）。
2. **「下了 `bp` 卻從不命中」**：常見原因是斷點位址其實在一個還沒載入的模組裡，或你把 RVA 當絕對位址下。用 `bp 模組名+偏移` 讓 WinDbg 幫你加基址；用 `bl` 確認斷點狀態是否為 `(enabled)` 還是未解析。
3. **「`db` 看 shellcode 少了幾個 byte 或變形」**：別急著以為 dump 壞了——那常常正是壞字元把你的資料在送達前就截斷/改寫了。這不是偵錯器的錯，是 Ch 8 要處理的壞字元現象。
4. **「沒 `.reload /f` 就抱怨看不到 `kernel32!VirtualProtect`」**：符號沒載入，`x`/`bp` 用符號名當然失敗。設好 `.sympath` 後務必 `.reload /f`，`lmv` 確認該模組符號狀態是 `pdb symbols`。
5. **「用 64-bit 的視角看 32-bit 行程」**：WoW64 下你偵錯的是 32-bit 行程，暫存器是 `eip/esp`。若你看到的是 `rip/rsp`，表示你停在 64-bit 的外層——確認你是以 32-bit 情境在看（WinDbg 通常會自動切，但 `.effmach` 可檢視/切換）。

## 本章重點整理

- WinDbg 命令分三類：無前綴（問程式）、`.`（問偵錯器）、`!`（問擴充）。看前綴定位命令。
- 看記憶體用 `d*` 家族，格式即命令字母：`dd`（dword）、`db`（byte+ASCII）、`da`/`du`（字串）、`dps`（指標+符號）。
- 斷點兩種：`bp`（軟體，攔執行）、`ba`（硬體，可攔讀寫）。ASLR 下用 `bp 模組+偏移`。
- 符號/模組/堆疊：`lm`、`x`、`ln`、`k`。對不上 IDA 時先想基址換算。
- crash SOP：`r eip` → `dd/db esp` → `ln`/`k`。

## 自我檢核

- [ ] 不看筆記，能說出 `dd` 和 `db` 分別適合看什麼、為什麼
- [ ] 能解釋為什麼 ASLR 下要用 `bp 模組+偏移` 而不是絕對位址
- [ ] 知道 `bp` 和 `ba w4` 的本質差別，以及後者在 SEH 分析的用途
- [ ] 能把「crash 後想確認有沒有控到返回位址」翻譯成具體幾條 WinDbg 命令
- [ ] 能說出至少三組 gdb → WinDbg 的對應命令

## 延伸閱讀

- **[WinDbg 命令參考 — Microsoft Learn](https://learn.microsoft.com/en-us/windows-hardware/drivers/debuggercmds/)**
  - **讀哪裡**：先讀「Reading and Writing Memory」「Controlling Execution」「Using Breakpoints」三組命令頁。
  - **學什麼**：本章所有命令的權威語法與完整選項；`d*`/`b*`/執行控制的參數細節。
  - **前提**：先有本章的分類框架，再查細節不會迷路。

- **[Getting Started with WinDbg (User-Mode) — Microsoft Learn](https://learn.microsoft.com/en-us/windows-hardware/drivers/debugger/getting-started-with-windbg)**
  - **讀哪裡**：整篇，特別是附掛行程與符號設定段落。
  - **學什麼**：官方的 user-mode 偵錯起步流程，補足本章沒展開的 UI 操作。

- **[Corelan — Exploit Writing Tutorial part 1（工具段落）](https://www.corelan.be/index.php/2009/07/19/exploit-writing-tutorial-part-1-stack-based-overflows/)** — Peter Van Eeckhoutte
  - **讀哪裡**：偵錯器操作與 crash 分析段落。
  - **為什麼值得讀**：把偵錯器命令放進真實 exploit 流程看，和本課 Part 2 直接對應。

- **[WinDbg 深入淺出系列 / WinDbg cheat sheet（多家整理）]**
  - **讀哪裡**：任一份社群維護的 WinDbg command cheat sheet，印出來貼螢幕邊。
  - **注意**：cheat sheet 只當速查，命令語意仍以官方參考為準；本章的對照表可先當你的隨身版。

下一章把這套命令用到「例外處理與 crash triage」——OSED 的日常不是看正常流程，是看程式怎麼掛、掛在哪、你能不能接管。

→ [Ch 5 WinDbg 精通 II：例外觀測、搜尋、crash triage 工作流](./05-windbg-mastery-2.md)
