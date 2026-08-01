# Ch 41 — WinDbg 進階：TTD time-travel debugging

> **目標**：理解 TTD（Time Travel Debugging）的原理與殺手級應用場景——UAF 誰先 free、記憶體被誰改、crash 根因往前追——掌握完整操作流程（錄製、重播、反向執行、LINQ 查詢），以及它和 Linux `rr` 的對照。本機目前未安裝 WinDbg TTD 環境，全章誠實標注「未實測，理論預期」，給讀者裝好後的完整操作步驟。

> **環境**：WinDbg Preview（WinDbgX）2024 版，需要 Windows 11 x64 管理員權限。**本機撰稿時 TTD 環境未安裝就緒，以下所有 WinDbg 輸出均標注「未實測，理論預期」，請在你裝好後對照驗證。**

---

## 為什麼需要 TTD？傳統除錯的根本缺陷

你在 Linux 遇到 UAF 崩潰，用 gdb 停在 crash 點，看到 `SIGSEGV`，然後發現問題是「某塊記憶體在五十個函式呼叫前被 free 掉了」。這時傳統除錯器給你的是一個**已經損壞的現場**——事發現場還在，但案發過程已經消失。

傳統除錯的局限是根本性的：**只能往前跑，不能倒帶**。你必須：

1. 重現問題（可能要幾十次才觸發）
2. 猜測問題可能發生在哪裡
3. 提前下 breakpoint 或 watchpoint
4. 重跑，看那個點有沒有異常
5. 重複以上，直到找到根因

對於時序敏感的 bug（race condition、UAF）或只在特定輸入下觸發的 crash，這個流程可能要幾小時甚至幾天。

**TTD 的核心主張**：**把整段程式執行錄下來**，之後可以無限次重播，可以向前也可以向後，可以從 crash 點**往回**走，直到找到真正的元兇。

---

## 先建立直覺：「執行歷史的資料庫」

把 TTD 想成一台錄影機，對著整個程式的執行過程錄影：每一個指令、每一次記憶體寫入、每一個函式呼叫，都錄成一個 **trace 檔**（副檔名 `.run`）。

重播時，WinDbg 不是真的重新執行程式——它讀取 trace 的記錄，在任意「時刻」還原當時的暫存器和記憶體狀態。

```
TTD 的兩個階段：

 ┌─────────────────────────────────────────────────────────────┐
 │  錄製階段                                                    │
 │                                                             │
 │  target.exe  ──► TTD 引擎（攔截每個指令）──► trace.run 檔  │
 │  （正常執行，但有 ~5x–50x 速度損耗）                        │
 └─────────────────────────────────────────────────────────────┘
                         ↓ 錄製完成（程式結束或 crash）

 ┌─────────────────────────────────────────────────────────────┐
 │  重播分析階段                                                │
 │                                                             │
 │  WinDbg  ──► 載入 trace.run ──► 在任意時刻查詢狀態         │
 │                                                             │
 │  可以：                                                     │
 │   • g（往前繼續）        • g-（往前反向繼續到上一個事件）   │
 │   • p（往前單步）        • p-（往回單步）                   │
 │   • t（往前步進 call）   • t-（往回步進）                   │
 │   • 反向執行到 watchpoint 被觸發的前一步                    │
 │   • LINQ 查詢整個執行歷史（哪些函式被呼叫、記憶體何時被改） │
 └─────────────────────────────────────────────────────────────┘
```

對照 Linux 的 `rr`：兩者的理念完全相同（record + replay），差別在 TTD 是 Windows 專屬、與 WinDbg 深度整合；`rr` 是 Mozilla 做的 Linux 工具、與 gdb 整合。

---

## TTD 是什麼等級的錄製？

TTD 是**使用者態指令級錄製**（user-mode instruction-level recording）：

- 每一條 CPU 指令都被捕捉
- 記憶體讀寫都有對應的 trace 記錄
- 系統呼叫的邊界（ntdll 的 syscall stub）有特殊處理（kernel 態不錄）
- 多執行緒 trace 也支援，每個 thread 有自己的 position

TTD **不錄製** kernel 態執行（Driver、Kernel 除錯需要另外的 kernel TTD，本課不涉及）。

**速度損耗**：程式在 TTD 下執行比正常慢 5x 到 50x，取決於程式的記憶體訪問模式。I/O 密集的程式損耗小，記憶體密集的程式損耗大。Trace 檔本身的大小：一個跑一秒的程式可能產出幾十 MB 到幾 GB 的 trace。

---

## 安裝前提

> **未實測，理論預期**

TTD 需要 **WinDbg Preview（WinDbgX）**，不是老版本的 WinDbg Classic（`windbg.exe`）。WinDbg Preview 是 Microsoft Store 應用程式，也可以透過 winget 安裝：

```powershell
# 未實測，理論預期
winget install --id Microsoft.WinDbg -e
```

安裝後，TTD 功能在 WinDbg Preview 的選單裡直接可用（`File → Launch executable under TTD` 或 `Attach to process under TTD`）。

**命令列工具 `ttd.exe`**：WinDbg Preview 安裝後附帶，通常在：

```
C:\Users\<user>\AppData\Local\DBG\UI\WinDbgX.exe
C:\Users\<user>\AppData\Local\DBG\TTD\ttd.exe    ← TTD 命令列工具
```

執行 TTD 錄製**需要管理員權限**（UAC 提升），因為 TTD 引擎使用 `PssCaptureSnapshot`-like 機制攔截 process 的執行。

---

## 錄製 Trace

### 方法 1：WinDbg Preview GUI

> **未實測，理論預期**

1. 開啟 WinDbg Preview（以管理員身份）
2. `File` → `Launch executable under TTD...`
3. 選擇目標 `.exe`，設定工作目錄和命令列參數
4. 點「Launch」——WinDbg 啟動目標程式，進入 TTD 錄製模式
5. 程式正常跑完或 crash 後，TTD 自動停下，在目標 exe 的目錄產出 `<exe名稱>01.run` 和 `.idx` 檔
6. WinDbg 自動開始重播，停在 session 開頭（`!positions` 可看當前位置）

如果要 attach 到**已在執行**的 process：

`File` → `Attach to process under TTD...` → 選 PID → 確認（管理員）→ 開始錄製，手動操作程式直到問題重現，然後 `Debug` → `Stop TTD Recording`。

### 方法 2：命令列 `ttd.exe`

> **未實測，理論預期**

```cmd
REM 未實測，理論預期（管理員命令提示字元）
ttd.exe -out C:\traces\ -launch "C:\target\vuln_app.exe" arg1 arg2
```

參數說明：

| 參數 | 說明 |
|---|---|
| `-out <dir>` | trace 檔輸出目錄（預設為目標 exe 的目錄） |
| `-launch <exe>` | 啟動並錄製該 exe |
| `-attach <pid>` | attach 到已執行的 process |
| `-ring <size>` | 使用 ring buffer 模式（只保留最後 N MB 的 trace，適合長時間跑） |
| `-stop` | 停止 attach 的 process 的 TTD 錄製 |

Ring buffer 模式（`-ring`）對 fuzzing 場景很有用：持續錄製，只保留 crash 前的最後幾秒的歷史。

### 產出的 trace 檔

```
C:\traces\
  vuln_app01.run     ← 主 trace 檔（二進位格式）
  vuln_app01.idx     ← 索引檔（加速時間位置查詢）
```

`.idx` 檔是 trace 的加速索引，第一次用 WinDbg 打開 `.run` 時如果沒有 `.idx` 會自動建立（需要時間）。

---

## 重播與基礎指令

### 開啟 Trace

> **未實測，理論預期**

```cmd
REM 未實測，理論預期
WinDbgX.exe -z C:\traces\vuln_app01.run
REM 或在 WinDbg Preview 裡：File → Open trace file
```

WinDbg 進入 TTD 重播模式，停在 trace 的**起始點**（`!positions` 顯示 `0:0` 或 `1:0`）。

### 時間位置表示法

TTD 用「`Major:Minor`」格式表示時間位置（position）：

```
Position: 1F3:2A
          ^^^  ^^
          │    └─ Minor（同一個 instruction 內的 sub-step，通常是 0）
          └─ Major（指令計數，十六進位）
```

用 `!positions` 看所有執行緒目前的 position：

```
0:000> !positions
Thread ID=0x1234 - Position: 1F3:2A
Thread ID=0x5678 - Position: 1F3:2A
```

### 反向執行指令

| 指令 | 等效的前向指令 | 說明 |
|---|---|---|
| `g-` | `g` | 反向繼續執行，直到上一個 breakpoint 或 trace 起點 |
| `p-` | `p` | 往回一個 step（不進 call） |
| `t-` | `t` | 往回一個 step（進 call） |
| `g- <addr>` | `g <addr>` | 反向執行到某個位址 |

反向執行的語意：**暫存器和記憶體狀態會還原到那個「時刻之前」**，讓你看到上一個事件發生時的狀態。

### Breakpoint 與 Watchpoint 在 TTD 裡的行為

> **未實測，理論預期**

普通 breakpoint（`bp`）在重播模式下也有效——`g` 前進到下一次命中，`g-` 反向到上一次命中。

**Data breakpoint（watchpoint）在 TTD 裡是殺手級功能**：

```
0:000> ba w 8 <target_address>
```

然後 `g-`（反向繼續），WinDbg 會往回走，找到**最後一次**把那個位址改掉的指令，在那裡停下。這讓你直接回答「這塊記憶體是在哪裡、被誰改掉的」，不用任何猜測。

---

## 核心使用場景一：UAF——誰先 free 的

這是 TTD 最強的應用。典型情境：

```
程式 crash 了，crash 點是 mov rax, [rbx+0x10]
rbx 指向一塊已經被 free 的 heap chunk（HeapFree 或 operator delete 呼叫過了）
現在要找：這塊記憶體是在哪裡、哪個 call stack 被 free 的？
```

**傳統除錯的做法**：在 `HeapFree`/`operator delete` 下斷點，重新執行，等到它 free 這塊位址——但問題是很多 free 呼叫，你不知道哪個是目標的那個。

**TTD 的做法**（未實測，理論預期）：

```
步驟 1：錄製到 crash 為止
          ttd.exe -launch vuln_app.exe <args>
          （讓它 crash 或手動觸發問題後停止錄製）

步驟 2：在 crash 點（WinDbg 停在 access violation）記錄 rbx 的值
          0:000> r rbx
          rbx=000001a2b3c4d5e0   ← 這是那塊已 free 的 chunk 位址

步驟 3：在 free 函式上設 breakpoint，往回找
          0:000> bp ntdll!RtlFreeHeap
          0:000> g-    ← 反向執行，找上一次命中 RtlFreeHeap 的時刻
          （若要確認是 free 了 rbx 那塊，加條件：bp ... ".if (@rcx == 0x<heap>) {} .else {gc}"）
          更精確：用 LINQ 查詢（見下節）

步驟 4：找到 free 的 call stack
          0:000> k       ← 看是誰呼叫了 free
          ... 這就是 UAF 的 free 路徑
```

> **未實測，理論預期**：以上流程描述的是 TTD 的標準 UAF 分析方法，實際指令語法在 WinDbg TTD 環境中應如此運作，但偵測特定 chunk 位址的條件 breakpoint 語法可能需要微調（RtlFreeHeap 的 chunk 參數是第二個參數 RDX，不是 RCX）。

---

## 核心使用場景二：記憶體被誰改的（反向 watchpoint）

情境：某個全域變數 / struct 欄位被不應該的人改掉了，程式在後來 crash。

**TTD + 反向 watchpoint 的做法**（未實測，理論預期）：

```
步驟 1：先正向執行到 crash，確認損壞的記憶體位址
          0:000> g   ← 執行到 crash（access violation 或 assert）
          0:000> ? <變數位址>   ← 確認位址，比如 0x1a2b3c40

步驟 2：設 data breakpoint（write watchpoint）
          0:000> ba w 8 0x1a2b3c40   ← 當這個位址被寫入時中斷

步驟 3：反向執行到最後一次寫入
          0:000> g-   ← 往回走，停在最後一次寫入 0x1a2b3c40 的指令

步驟 4：看當時的 call stack 與暫存器
          0:000> k    ← 這就是「壞資料寫入者」的路徑
          0:000> r    ← 看暫存器，確認寫入的值
```

這個流程把「找記憶體破壞根因」從幾小時的假設-驗證循環，壓縮成幾分鐘的直線操作。

---

## 核心使用場景三：從 crash 往前追根因

情境：程式在某個 null dereference crash，但 null 是很久之前就寫進去的。

**TTD 的做法**（未實測，理論預期）：

```
步驟 1：執行到 crash（access violation，null dereference）
          WinDbg 自動停在 crash 點

步驟 2：找被存取的位址
          0:000> .exr -1          ← 看最後一個 exception record
          ExceptionAddress: 00007ff8...
          ExceptionCode: c0000005 (Access violation)
          ExceptionFlags: 00000000
          NumberParameters: 2
          Parameter[0]: 0000000000000000   ← 讀或寫
          Parameter[1]: 0000000000000010   ← 試圖存取的位址（就是 null + 0x10）

步驟 3：確認哪個暫存器持有 null pointer
          0:000> r   ← 找哪個 reg 是 0

步驟 4：在那個暫存器被設成 0 的地方設 watchpoint（往回找賦值）
          0:000> ba w 8 <address-of-variable-that-became-null>
          0:000> g-

步驟 5：找到賦值點，往上看 call stack，確認根因
```

---

## LINQ 查詢執行歷史

TTD 最獨特的功能：整個執行歷史被建模成一個**可查詢的資料集**，用 `dx` 指令搭配 LINQ 語法查詢。

> **未實測，理論預期**

### 查詢所有函式呼叫

```
0:000> dx @$cursession.TTD.Calls("ntdll!RtlAllocateHeap")
```

這會列出整個 trace 裡所有呼叫 `RtlAllocateHeap` 的時刻，包含：

```
( 未實測，理論預期輸出 )
[0x0]
  EventType   : 0x0
  ThreadId    : 0x1234
  UniqueThreadId : 0x2
  TimeStart   : 1A3:0          ← 呼叫開始的 position
  TimeEnd     : 1A9:0          ← 呼叫結束的 position
  Function    : ntdll!RtlAllocateHeap
  FunctionAddress : 0x7ff8...
  ReturnAddress   : ...
  Parameters  : {...}          ← 呼叫時的參數
```

### 用 LINQ 篩選

```
dx @$cursession.TTD.Calls("ntdll!RtlFreeHeap")
    .Where(c => c.Parameters[1] == 0x1a2b3c4d5e0)
```

這行 LINQ 找出所有 `RtlFreeHeap` 的呼叫裡，第二個參數（chunk 位址）等於目標位址的那次。找到後可以 `.Select(c => c.TimeStart)` 拿到 position，再跳到那個時間點：

```
0:000> dx @$cursession.TTD.Memory[0x1a2b3c4d5e0, 0x1a2b3c4d5e8, "w"]
```

查詢某段記憶體範圍**所有的寫入歷史**。

### 跳到特定時間點

```
0:000> !tt 1A3:0          ← 跳到 position 1A3:0
0:000> !tt 100%           ← 跳到 trace 末尾
0:000> !tt 0%             ← 跳到 trace 起點
0:000> !tt 50%            ← 跳到中間點（二分查找法用）
```

### 完整 UAF 分析的 LINQ 流程

> **未實測，理論預期**

```
REM 步驟 1：找到所有 free 呼叫，看哪個 free 了目標 chunk
dx @$cursession.TTD.Calls("ntdll!RtlFreeHeap")
    .Where(c => c.Parameters[1] == <目標chunk位址>)

REM 步驟 2：取得 free 的 time position
dx @$cursession.TTD.Calls("ntdll!RtlFreeHeap")
    .Where(c => c.Parameters[1] == <目標chunk位址>)
    .Select(c => c.TimeStart)

REM 步驟 3：跳到 free 的那個時刻
!tt <TimeStart 值>

REM 步驟 4：看 call stack 確認是誰 free 的
k
```

---

## `!positions`、`dx @$curprocess`、`dx @$cursession` 的 TTD 物件模型

> **未實測，理論預期**

TTD 在 WinDbg 裡暴露一個 NatVis / LINQ-based 的物件模型：

| 物件 | 說明 |
|---|---|
| `@$cursession` | 目前 TTD session |
| `@$cursession.TTD` | TTD 根命名空間 |
| `@$cursession.TTD.Calls(...)` | 函式呼叫歷史 |
| `@$cursession.TTD.Memory[start, end, type]` | 記憶體存取歷史 |
| `@$cursession.TTD.Threads` | 執行緒列表 |
| `@$cursession.TTD.Events` | 所有事件（exception、thread create/exit、module load） |
| `@$curprocess.TTD.Lifetime` | 整個 process 的 time range |

```
REM 未實測，理論預期
dx @$cursession.TTD.Events
    .Where(e => e.Type == "Exception")
    .Select(e => new { e.Position, e.Exception.Code })
```

列出所有 exception 事件及其 position——對複雜 bug 做初步地圖很有用。

---

## 和一般除錯的差異對照

| 面向 | 傳統除錯（WinDbg/gdb） | TTD（WinDbg TTD） |
|---|---|---|
| **執行方向** | 只能往前 | 可以往前 + 往後 |
| **Breakpoint** | 需要提前設，需要重跑 | 可以在分析時任意設，往回走找 |
| **Watchpoint** | 命中要提前知道會發生 | 反向 watchpoint 找「誰改了這裡」 |
| **Reproducibility** | 每次 crash 都要重現 | 錄一次，分析無限次 |
| **速度損耗** | 無（正常速度執行） | 5x–50x（錄製期間） |
| **Trace 大小** | 無 trace | 幾十 MB 到幾 GB |
| **Race condition** | 很難抓（每次重現可能行為不同） | 錄製一次包含所有 thread 順序 |
| **歷史查詢** | 無法查詢過去 | LINQ over 整個執行歷史 |
| **環境需求** | WinDbg 或 gdb | WinDbg Preview + 管理員 + 足夠磁碟 |

---

## 對照 Linux rr（record & replay）

如果你以前用過 `rr`（Mozilla 的 Linux record & replay 工具），理念完全相同：

| 面向 | Linux `rr` | Windows TTD |
|---|---|---|
| **錄製指令** | `rr record ./target` | `ttd.exe -launch target.exe` |
| **重播指令** | `rr replay` → 進入 gdb | WinDbg Preview 開 `.run` 檔 |
| **反向執行** | `reverse-continue`（gdb 指令） | `g-`（WinDbg 指令） |
| **反向單步** | `reverse-step`、`reverse-next` | `t-`、`p-` |
| **歷史查詢** | 無內建 LINQ，靠 gdb Python scripting | LINQ over `@$cursession.TTD` |
| **除錯器整合** | gdb（熟悉的老朋友） | WinDbg Preview（陌生但有 symbols） |
| **多執行緒** | 支援（確定性重播） | 支援 |
| **Trace 機制** | 用 `ptrace` 記錄 syscall + signal | 用 Windows debug API + binary instrumentation |
| **Kernel 態** | 不錄（userland only） | 不錄（userland only，kernel TTD 是另一回事） |
| **開源** | 是（Mozilla 授權） | 否（Microsoft 閉源）|

rr 的優點是開源、輕量、和 gdb 無縫整合；TTD 的優點是 LINQ 查詢語法強大、和 WinDbg symbols（dt、!heap 等）整合好。如果你已熟悉 rr，上手 TTD 大約一天——核心概念完全一致，只是換了指令語法和查詢語言。

---

## 底層機制：TTD 怎麼錄製的

> **以下為理論描述，基於 Microsoft 公開的文件與研究論文（見延伸閱讀），未實測細節可能與實作有出入**

TTD 使用 **binary instrumentation** 方式錄製——它不是 emulation，而是在目標程式的每個 basic block 入口插入一段「記錄指令」的 probe。

```
TTD 錄製原理（簡化）：

  target 的 .text：              TTD 改寫後（in-memory）：
  ┌──────────────┐              ┌──────────────────────────────┐
  │ 原始指令 1   │              │ probe: 記錄 position, 記憶體  │
  │ 原始指令 2   │  ──────►     │ 原始指令 1                   │
  │ 原始指令 3   │              │ probe: 記錄 position          │
  │ ...          │              │ 原始指令 2                   │
  └──────────────┘              │ ...                          │
                                └──────────────────────────────┘
```

Trace 格式是 Microsoft 閉源的二進位格式（`.run`），由 `ttd.exe` 和 WinDbg 的 `TTDReplay.dll` 讀取。

---

## 踩雷集錦

1. **「TTD 不需要管理員」**：需要。TTD 的錄製機制需要 SeDebugPrivilege 或更高的權限，以管理員身份啟動 WinDbg 是必要條件。不然 `ttd.exe -launch` 會報 access denied。

2. **「`g-` 就是直接倒帶到起點」**：錯。`g-` 是「反向繼續，直到上一個事件（breakpoint / watchpoint / exception）」，不是一路倒回去。如果沒有設任何 breakpoint，`g-` 反向到 trace 開頭。要跳到特定位置用 `!tt <position>`。

3. **「TTD 影響不到多執行緒 race」**：反過來，TTD 的錄製**確定性地**捕捉了多執行緒的指令交錯順序。重播時每次都是完全相同的順序，不會因為重播時的排程不同而看到不同行為。這是 rr/TTD 對 race condition 的核心優勢。

4. **「Trace 磁碟空間不用擔心」**：長時間跑的程式（幾分鐘以上）可以產出幾 GB 的 trace。用 `-ring <MB>` 限制 ring buffer 大小，只保留最後 N MB 的歷史——對 fuzzing 找 crash 的場景很有用，代價是 crash 前的很遠歷史會被丟棄。

5. **「反向 watchpoint 一定能找到寫入者」**：如果你的 watchpoint 是 `ba w 8 <addr>`，但那塊記憶體是被 `memset`/`RtlZeroMemory` 之類的 SSE 大量寫入（一次寫 16 或 32 bytes），watchpoint 命中的是那個 `MOVAPS [mem], xmm0` 指令，往上看 call stack 才能確認是哪個 caller 觸發的。

---

## 進階：再往深一層

### TTD 搭配 JavaScript/TypeScript Debugger Extension

WinDbg Preview 支援 JavaScript 擴充，可以寫自定義分析腳本：

```javascript
// 未實測，理論預期（WinDbg JS extension）
"use strict";
function findFrees(chunkAddr) {
    let calls = host.currentSession.TTD.Calls("ntdll!RtlFreeHeap");
    for (let c of calls) {
        if (c.Parameters[1] == chunkAddr) {
            host.diagnostics.debugLog("Free at position: " + c.TimeStart + "\n");
        }
    }
}
```

載入後在 WinDbg 裡 `dx Debugger.State.Scripts.<script>.Contents.findFrees(0x1a2b3c40)` 執行。

### TTD + WinAFL（Ch 42 預告）

TTD ring buffer 模式配合 WinAFL 的 `-postfix` 模式：fuzzer 偵測到 crash 後自動用 TTD 錄製 crash 的重現。這讓 crash triage（分類哪些 crash 是新的 bug、哪些是重複的）可以在 TTD trace 上做，省去每次 crash 都要手動分析的成本。

### 生產環境的 TTD（Azure/MSRC 用途）

Microsoft 內部和 MSRC 會對難以重現的 production crash 使用 TTD，在客戶端錄製 crash trace，把 `.run` 檔傳回 Microsoft 分析。這是 TTD 設計的最初動機之一——讓 crash 調查不再受限於能否在本地重現問題。

---

## 動手練習

（裝好 WinDbg Preview 後執行）

**目標**：用 TTD 錄製一個已知有 use-after-free 的小程式，並用反向執行找到 free 點。

**步驟 1**：寫一個明確的 UAF 程式（或使用 Ch 27 的練習靶）：

```c
/* uaf_demo.c */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct { int id; char name[16]; } Object;

int main(void) {
    Object *obj = malloc(sizeof(Object));
    obj->id = 42;
    strncpy(obj->name, "test", 16);

    printf("before free: id=%d\n", obj->id);
    free(obj);          /* free 發生在這裡 */
    /* UAF: obj 被 free 之後繼續使用 */
    printf("after free: id=%d\n", obj->id);   /* 這裡讀 freed memory */
    return 0;
}
```

```bash
gcc -O0 -o uaf_demo.exe uaf_demo.c
```

**步驟 2**：以管理員身份開 WinDbg Preview，`File → Launch executable under TTD...`，選 `uaf_demo.exe`，啟動，讓它跑完（不一定 crash，但 trace 有 free 的記錄）。

**步驟 3**：重播，用 LINQ 找 `HeapFree` 呼叫：

```
0:000> dx @$cursession.TTD.Calls("kernelbase!HeapFree")
```

找到 free 那個 chunk 的呼叫，記下 TimeStart position。

**步驟 4**：`!tt <TimeStart>` 跳到 free 的那刻，`k` 看 call stack 確認是 `main` 呼叫的 `free`。

**步驟 5**：在 `obj` 的位址設 data watchpoint，用 `g-` 反向找「誰寫了 obj 的欄位 id」（在 free 之後）。

目標：**親手走一遍「從 crash 點反向找事件發生位置」的完整流程**，建立 TTD 分析的肌肉記憶。

---

## 本章重點整理

- TTD（Time Travel Debugging）把整段程式執行錄成 trace，允許**反向執行**（`g-`、`p-`、`t-`）和**跳到任意時刻**（`!tt <position>`），根本解決「只能往前執行」的傳統除錯局限。
- **三大殺手場景**：UAF 找誰先 free（反向 + `RtlFreeHeap` LINQ）、記憶體被誰改（反向 watchpoint `ba w 8 <addr>` + `g-`）、crash 根因往前追（從 exception 起點反向）。
- **LINQ 查詢**（`dx @$cursession.TTD.Calls(...)`）讓整個執行歷史成為可篩選的資料集，`Where`/`Select` 直接找出特定條件的呼叫，不需要重跑程式、不需要猜測 breakpoint 位置。
- 對照 Linux `rr`：理念完全相同，差別在工具鏈整合——`rr` 搭 gdb，TTD 搭 WinDbg；TTD 多了 LINQ 物件模型，`rr` 多了開源的透明度。

---

## 自我檢核

- [ ] 不看筆記，能解釋 TTD 和傳統除錯器的根本差異（一句話）
- [ ] 能說出「反向 watchpoint 找記憶體破壞根因」的具體步驟（至少三步）
- [ ] 知道 `g-`、`p-`、`t-`、`!tt` 各自做什麼
- [ ] 能解釋 LINQ 查詢 `@$cursession.TTD.Calls("ntdll!RtlFreeHeap").Where(...)` 的語意
- [ ] 面試被問「UAF 分析中 TTD 比傳統除錯強在哪裡」，能給出兩個具體例子

---

## 延伸閱讀

### 官方文件

- **[Time Travel Debugging — Microsoft Learn](https://learn.microsoft.com/en-us/windows-hardware/drivers/debugger/time-travel-debugging-overview)**
  - **讀哪裡**：「Record a trace」、「Replay a trace」、「TTD.Calls」、「TTD.Memory」四節；把所有 LINQ 範例抄下來在自己環境跑一遍
  - **和本章的關聯**：本章所有 TTD 指令的一手來源；本機環境裝好後以此為查表依據
  - **前提知識**：WinDbg 基礎（Ch 0 環境設定做完）

- **[Debugger Objects in WinDbg — Microsoft Learn（dx 與 LINQ）](https://learn.microsoft.com/en-us/windows-hardware/drivers/debugger/using-linq-with-the-debugger-objects)**
  - **讀哪裡**：「TTD Objects」小節；列出所有 `@$cursession.TTD.*` 的屬性和方法
  - **和本章的關聯**：LINQ 查詢章節的完整 API 參照；本章的 LINQ 範例都從這裡延伸

### 研究論文 / 白皮書

- **"Reverse Debugging of Operating Systems" — Barkalov et al.（rr 原始論文背景）**
  - 雖然 TTD 本身沒有公開學術論文，但 rr 的論文（Mozilla，2015）「Engineering Record And Replay For Deployability」奠定了 record & replay 的理論框架；TTD 和 rr 的設計目標高度一致
  - **讀哪裡**：Introduction 和 Section 3（Recording）；理解「確定性重播」的挑戰（non-determinism 來源：timing、signal、mmap ordering）
  - **和本章的關聯**：本章的「TTD vs rr」對比章節的理論基礎
  - **搜尋**：`rr: Lightweight Recording and Deterministic Debugging with Mozilla` 在 Google Scholar

- **[WinDbg TTD — Trace Indexing and LINQ Deep Dive（Microsoft 內部 PDC 2019 slides）](https://learn.microsoft.com/en-us/shows/)**
  - **讀哪裡**：Microsoft 在 PDC 2019 的 TTD 演講（YouTube 搜「WinDbg time travel debugging channel9」）；LINQ 物件模型的設計動機
  - **和本章的關聯**：LINQ 查詢的進階用法，本章的 LINQ 範例是其簡化版

### 部落格 / 教學

- **[j00ru — on using TTD for vulnerability research（j00ru.vexillium.org）](https://j00ru.vexillium.org/)**
  - **讀哪裡**：搜 "time travel debugging" 或 "TTD" 的文章；j00ru 有在 Chrome / Windows kernel 漏洞分析裡使用 TTD 的筆記
  - **和本章的關聯**：本章的三大使用場景（UAF、記憶體破壞、crash 根因）在他的文章裡有真實 CVE 的案例
  - **前提知識**：本章做完，有 WinDbg TTD 基礎環境

- **[Connor McGarr — Windows Kernel Exploitation with TTD](https://connormcgarr.github.io/)**
  - **讀哪裡**：搜 "TTD" 相關文章；McGarr 把 TTD 用在 kernel exploit 開發的 debugging 上
  - **和本章的關聯**：本章聚焦 userland，他的文章展示 TTD 在更複雜場景的用法，是本課天梯繼續往上的參照
  - **前提知識**：Ch 0–41 做完，有 kernel pwn 底子更佳

→ [Ch 42 — fuzzing on Windows：WinAFL / TTD-based](./42-fuzzing-winafl.md)
