# Ch 22 — SEHOP：機制與繞過

> **目標**：搞清楚 SEHOP（Structured Exception Handler Overwrite Protection）如何在執行期驗整條 SEH chain 的完整性、為什麼它能封堵 Ch 21 的 SEH overwrite、以及繞過它的兩條主要路線——payload 裡重建合法鏈，或直接找沒開 SEHOP 的目標。同時把 SafeSEH（編譯期）和 SEHOP（執行期）的防禦邊界講清楚，讓你在 CTF 和真實滲透時能快速判斷「這個目標能不能打 SEH overwrite」。

## 為什麼需要 SEHOP？

Ch 21 結尾我們提過：SafeSEH 的死穴是「沒有 SafeSEH 的老 DLL 還在跑」。只要靶 process 裡有任何一個沒開 `/SAFESEH` 的模組，攻擊者就能在那裡找 pop-pop-ret gadget，繞過整個 `RtlIsValidHandler` 的白名單檢查。

SafeSEH 是**編譯期決策**——每個 DLL 各自選擇要不要用 `/SAFESEH` 編。對作業系統來說，它沒有辦法逼所有第三方 DLL 更新。

SEHOP（Vista SP1，2008 年）從另一個角度切入：**不管 handler 在哪個模組，先驗 SEH chain 本身的完整性**。一個正常的 SEH chain，在被 overflow 蓋之前是連續且以 OS 注入的 sentinel 結尾的；overflow 蓋完之後，這個性質被破壞。SEHOP 直接在 `RtlDispatchException` 呼叫任何 handler 之前做鏈完整性校驗，不通就終止。

這是「防禦策略從模組層移到 chain 層」的典型演進。SafeSEH 問「handler 在哪個模組裡、合不合法」；SEHOP 問「整條 chain 是不是一個合法的鏈結串列、結尾是不是 sentinel」。兩層疊加，才把 SEH overwrite 的空間大幅縮小。

> 如果你對 SEH chain 的結構（`EXCEPTION_REGISTRATION_RECORD`、nSEH/Handler、FS:[0]）還不熟，先回看 [Ch 11](./11-seh-x86.md)。SEHOP 的機制完全建立在你對那些結構的理解上。

---

## 先建立直覺：「給我看這條鏈的尾巴」

想像你是圖書館管理員，有人帶著一串借書單（SEH chain）過來。借書單是單向鏈結串列，每張單子上寫著「下一張單子是誰」，最後一張寫「收藏室」（sentinel）。

**SEHOP 的做法**：在你處理任何借書申請之前，先把整串單子走一遍，確認：
1. 每張單子的「下一張」指標指向比自己更靠後（更高位址）的位置（x86 stack 往低位址長，所以合法的 Next 必須是更高的 stack 位址）
2. 最後一張確實以「收藏室」結尾（OS 放的 sentinel）

如果攻擊者蓋了中間某張單子（nSEH），串列就斷掉了，或者尾巴不是「收藏室」——管理員發現了，直接拒絕整個申請。

對應到實際機制：

```
正常 SEH chain（stack 裡，低位址 → 高位址）：

  FS:[0] → RECORD_A → RECORD_B → RECORD_C → ntdll!FinalExceptionHandlerPad
                                               （sentinel，OS 在進程啟動時注入到每個 thread 的 stack 頂端）

overflow 蓋掉 RECORD_B 後：

  FS:[0] → RECORD_A → [OVERWRITTEN_RECORD_B]  ←  Next 或 Handler 指向了 shellcode/gadget
                            ↓
                      Next 可能指向一個非法位址
                      or 鏈根本到不了 sentinel

SEHOP 驗鏈：從 FS:[0] 開始走，走到頭，發現 sentinel 消失了——終止進程
```

---

## SEHOP 的完整機制

### Sentinel（FinalExceptionHandler）的注入

SEHOP 需要一個鏈結串列的「合法結尾」來做驗證。這個結尾叫 **`ntdll!FinalExceptionHandlerPad`**（有時文件寫作 `ntdll!FinalExceptionHandler`，依版本而異；本課用 `FinalExceptionHandlerPad` 是文件較常見的稱法，以你環境 `dt` 驗證為準）。

進程啟動時，`ntdll` 在每個 thread 的初始 SEH chain 底部插入這個 sentinel：

```c
/* 概念示意，不是真實 ntdll 源碼 */
typedef struct _EXCEPTION_REGISTRATION_RECORD {
    struct _EXCEPTION_REGISTRATION_RECORD *Next;
    PEXCEPTION_ROUTINE                     Handler;
} EXCEPTION_REGISTRATION_RECORD;

/* 最底層的 record */
EXCEPTION_REGISTRATION_RECORD sentinel_record = {
    .Next    = (PVOID)0xFFFFFFFF,   /* 終止標誌：x86 下 -1，表示鏈末端 */
    .Handler = ntdll!FinalExceptionHandlerPad,
};
/* ntdll 把 sentinel_record 的位址寫到 TEB.NtTib.ExceptionList（FS:[0]）
   作為 thread 的 initial SEH chain bottom */
```

攻擊者要偽造的就是這個 sentinel：`Next = 0xFFFFFFFF`（鏈末端標誌），`Handler = ntdll!FinalExceptionHandlerPad` 的真實位址。

> 注意：`ntdll!FinalExceptionHandlerPad` 的位址依系統版本而異，且在啟用 ASLR 的系統上每次開機都會重定。

### 驗證邏輯（RtlDispatchException 裡）

> **未實測，理論預期**：以下邏輯來自公開的 SEHOP 機制分析（Symantec 的 SEHOP 白皮書、j00ru 的研究），不是真實 ntdll 反組譯輸出。

```c
/* 簡化的 SEHOP 驗證邏輯（RtlDispatchException 裡） */
BOOL SehopValidateChain(void) {
    EXCEPTION_REGISTRATION_RECORD *record;
    ULONG_PTR stackBase, stackLimit;

    /* 取得當前 thread 的 stack 邊界（TEB 裡） */
    stackBase  = NtCurrentTeb()->NtTib.StackBase;
    stackLimit = NtCurrentTeb()->NtTib.StackLimit;

    /* 從 FS:[0] 開始走 chain */
    record = (PVOID)__readfsdword(0);  /* TEB.NtTib.ExceptionList */

    while (record != (PVOID)0xFFFFFFFF) {
        /* 每個 Next 必須：1) 在 stack 範圍內  2) 比現在更高 */
        if ((ULONG_PTR)record->Next <= (ULONG_PTR)record)  return FALSE;
        if ((ULONG_PTR)record->Next  > stackBase)          return FALSE;
        if ((ULONG_PTR)record->Next  < stackLimit)         return FALSE;
        record = record->Next;
    }

    /* 鏈末尾必須有合法的 sentinel：Handler 是 FinalExceptionHandlerPad */
    if (record->Handler != ntdll_FinalExceptionHandlerPad)  return FALSE;

    return TRUE;
}
```

失敗就直接 `ZwTerminateProcess`，不給任何 handler 機會執行。

### 為什麼 Ch 21 的標準 SEH overwrite 被擋住

標準 SEH overwrite 的 payload：

```
[ 'A' * offset ] [ nSEH = EB 06 90 90 ] [ Handler = PP gadget 位址 ]
```

蓋完後的 SEH record（被蓋的那個）：
- `Next`（nSEH 欄位）= `0x909006EB`（short jmp 指令的 bytes 被解讀為一個位址）
- `Handler` = PP gadget 位址

問題出在 `Next = 0x909006EB`：
1. 這個位址大概率不在 stack 範圍內（stack 位址通常在 `0x00xxxxxx` 或 `0x00Fxxxxx`）
2. 即使 `0x909006EB` 湊巧在 stack 範圍，它也指向不含合法 `FinalExceptionHandlerPad` 的地方
3. 鏈從這個斷掉的 record 走不到 sentinel

SEHOP 在例外發生、準備走 chain 之前，先驗這個鏈，發現 `Next` 不合法，直接 terminate。Handler 永遠沒有機會被呼叫。

---

## SEHOP 的預設狀態：版本與部署現實

這是選目標時最重要的情報，必須講清楚：

| 版本 | 預設狀態 | 備註 |
|---|---|---|
| Windows Vista RTM | 沒有 SEHOP | 尚未引入 |
| Windows Vista SP1 | **Server 版本預設開啟**；Client（Home/Business）**預設關閉** | 第一次部署 |
| Windows Server 2008 | **預設開啟** | Server 版本從這裡開始全開 |
| Windows 7 | Client 預設**關閉**；可手動開 | 微軟認為相容性風險 > 防禦收益（對桌面） |
| Windows Server 2008 R2 | **預設開啟** | |
| Windows 8 / 8.1 | Client 版本開始啟用（依 App 設定）；Server 版本全開 | 開始對部分 App 強制 |
| Windows 10 | 預設**開啟**（全版本）；可透過 WDEG/EMET 覆寫 per-process | 標誌性轉折點 |
| Windows 11 | 預設開啟；SEHOP 列在 WDEG（Windows Defender Exploit Guard）的 System 設定裡 | |

**實際含義**：

- 打 **2008 年以前的 Windows**（XP、Vista RTM）：SEHOP 根本不存在，純 SEH overwrite 可行。
- 打 **Windows 7 客戶端**（常見的老 CTF 設定）：SEHOP 預設關閉，SEH overwrite 只需過 SafeSEH。
- 打 **Windows Server 2008+**：SEHOP 預設開，標準 SEH overwrite 會被擋。
- 打 **Windows 10/11**：SEHOP 全開，但 x64 binary 根本沒有 x86 SEH chain（table-based SEH），SEHOP 的討論在 x64 本來就不適用。

> **CTF 現實**：大多數現代 Windows pwn CTF 題（x64、Win10/11）不考 SEH overwrite；SEH overwrite 題通常是 x86 binary、關掉部分緩解的設定（舊版 OS 或明確關閉 SEHOP）。如果看到 x86 binary + 沒有 SEHOP 的說明，直接套 Ch 21 的技法。

### 怎麼檢查目標是否開啟 SEHOP

**方法 1：WDEG（Windows Defender Exploit Guard）**

Windows 10/11 裡，SEHOP 的 per-process 設定在：

```
Windows Security → App & browser control → Exploit protection settings
→ System settings → SEHOP（可設 On / Off / Use default(On)）
```

或用 PowerShell 查（需要 WDEG）：

```powershell
Get-ProcessMitigation -System
# 找 SEHOP 那行：若 Enable: True 就是開啟
```

**方法 2：登錄機碼（舊方法，Vista/7 時代）**

```
HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\kernel
值：DisableExceptionChainValidation
0 = SEHOP 開啟（預設）
1 = 關閉
```

---

## SafeSEH vs SEHOP 差異：編譯期 vs 執行期

這是本章最重要的概念對比：

| 面向 | SafeSEH（`/SAFESEH`） | SEHOP |
|---|---|---|
| **在哪裡做防護** | 編譯/連結期把合法 handler 放白名單進 PE | 執行期在例外分發前驗 chain 完整性 |
| **驗的是什麼** | Handler 位址是否在當前模組的白名單裡 | 整條 SEH chain 的拓撲結構是否合法 |
| **失效條件** | 進程裡有任何一個無 SafeSEH 的模組 | chain 的 Next 指標被竄改，或 sentinel 被覆蓋 |
| **部署方式** | 每個 PE 各自決定，per-binary | 作業系統層級，per-process（可透過 WDEG 覆寫） |
| **對 gadget 位置的要求** | gadget 必須在 SafeSEH=False 的模組 | 即使 gadget 在合法位置，如果 chain 被破壞也無效 |
| **攻擊者的因應** | 找 SafeSEH=False 的老 DLL | 重建合法的 chain 結構（見下一節） |
| **對 x64 的意義** | x64 沒有 stack-based SEH，SafeSEH 無意義 | 同左 |
| **引入版本** | Windows XP SP2 (2004) + MSVC 2003 | Vista SP1 / Server 2008 (2008) |

兩者不是替代關係，而是**互補的雙層防線**：SafeSEH 管 handler 來源合法性，SEHOP 管 chain 拓撲完整性。同時開時，攻擊者需要同時滿足兩個條件才能把 handler 送到呼叫點。

---

## 繞過 SEHOP

### 前提確認

要討論繞過，先確認目標條件：

1. **目標是 x86 binary**（x64 根本沒有 x86 SEH chain，SEHOP 在 x64 pwn 是無關的話題）
2. **目標系統 SEHOP 是開啟的**（否則直接用 Ch 21 技法）
3. **有 stack overflow 的原語**（能控制足夠多的 bytes）

### 繞過路線 1：重建合法 SEH chain（Fake Chain）

這是繞過 SEHOP 的標準技法。核心思路：**如果我的 payload 能放一條看起來合法的 chain，讓 SEHOP 驗完通過，再跳到我的控制位址**。

#### 需要什麼資訊

要偽造一條合法 chain，攻擊者需要知道：
1. **`ntdll!FinalExceptionHandlerPad` 的位址**——用於偽造 sentinel 的 `Handler` 欄位
2. **stack 上 sentinel 的位址**——用於偽造最後一個 record 的 `Next` 欄位

問題：在 ASLR 開啟的系統上，這兩個值每次開機都不一樣。

所以：**繞過 SEHOP 的前提幾乎一定需要 info leak**（拿到 ntdll 的 base address 或 stack 上的指標）。

#### 偽造 chain 的結構

假設攻擊者已透過 info leak 拿到：
- `ntdll_base`：ntdll 的載入基址
- `final_handler_offset`：`FinalExceptionHandlerPad` 在 ntdll 裡的 RVA（可以靜態分析 ntdll 取得，但每版 ntdll 不同）
- `stack_sentinel_addr`：stack 上真實 sentinel record 的位址（可從 TEB 或 stack 掃描取得）

payload 裡放的偽 chain：

```
[ 'A' * offset_to_fake_nSEH ]
[ fake_record.Next   = stack_sentinel_addr ]   ← 指向 stack 上真實 sentinel 的位址
[ fake_record.Handler = PP_gadget_or_shellcode ]  ← 這才是我們想執行的東西
[ real_sentinel.Next = 0xFFFFFFFF ]           ← sentinel 鏈末端
[ real_sentinel.Handler = ntdll_FinalExceptionHandlerPad ]
```

SEHOP 驗鏈：
- 從 `FS:[0]` 走到 fake_record
- fake_record.Next → stack_sentinel_addr → Next=0xFFFFFFFF，Handler=合法 FinalExceptionHandlerPad
- 驗通！
- 然後呼叫 fake_record.Handler——攻擊者的 PP gadget 或 shellcode

ASCII 圖：

```
偽造的合法 chain（SEHOP 視角）：

  FS:[0] → ... → [FAKE_RECORD]  →  [REAL_SENTINEL]  → (鏈末尾)
                  Next = real_sentinel_addr
                  Handler = PP gadget ← 攻擊者控制的執行流
                                         ↑
                              SEHOP 以為這是合法 handler，放行

  SEHOP 走完整條鏈，sentinel 是合法的 → 驗證通過
  → RtlDispatchException 開始依序呼叫 Handler
  → 第一個呼叫的就是 FAKE_RECORD.Handler（PP gadget）
  → 攻擊者得到執行流
```

#### 偽造 chain 的限制

這條路線的實際難度很高：

1. **需要 info leak**：ntdll 基址和 stack 位址都要洩漏。在現代 Win10/11 + ASLR 高熵的環境下，這本身就是一個獨立的挑戰（Ch 24/Ch 31 的主題）。
2. **ntdll 版本依賴**：`FinalExceptionHandlerPad` 的 RVA 不是標準公開文件，不同補丁版本可能不同。需要靜態分析目標系統的 ntdll。
3. **x86 alone**：x64 進程根本沒這個路線。

> **未實測，理論預期**：上述偽造 chain 的技法需要在完整靶環境（MSVC x86 binary + Windows 7 x86 + SEHOP 開啟 + 已知 ntdll 版本）下實際驗證。在現代環境實測前，以下骨架僅作理論說明。

#### 理論 payload 骨架（教育性）

```python
# 教育性骨架——SEHOP bypass via fake chain
# 需要：ntdll_base (from leak)，stack_sentinel_addr (from leak)
# 未實測，理論預期

from pwn import *

# 假設已 leak 的值（實際需要 info leak 原語）
ntdll_base         = 0x7C800000      # 假設值，實際每次開機不同
final_handler_rva  = 0x0001A1D0      # FinalExceptionHandlerPad 的 RVA（依 ntdll 版本靜態分析）
final_handler_addr = ntdll_base + final_handler_rva

stack_sentinel_addr = 0x0012FFF8     # 假設值，stack 上真實 sentinel 的位址

pp_gadget = 0x100432AB  # 仍需在 SafeSEH=False 的模組裡（如果 SafeSEH 也開）

# payload 結構
offset_to_fake_record = 100  # cyclic_find 找到的 offset

# 偽造的 record：
fake_nSEH    = p32(stack_sentinel_addr)   # Next 指向真實 sentinel
fake_handler = p32(pp_gadget)             # Handler = 我們的 PP gadget

# 偽造的 sentinel（放在 payload 後面，stack 上我們控制的位置）
real_next    = p32(0xFFFFFFFF)            # 鏈末端
real_handler = p32(final_handler_addr)   # FinalExceptionHandlerPad

payload  = b"A" * offset_to_fake_record
payload += fake_nSEH
payload += fake_handler
# 填充讓 stack_sentinel_addr 指向的位置落在 payload 裡的 sentinel
payload += b"A" * (stack_sentinel_addr - current_esp - len(payload))
payload += real_next
payload += real_handler

print(f"[*] len={len(payload)}")
print(f"[*] fake_handler: {hex(pp_gadget)}")
print(f"[*] FinalExceptionHandlerPad: {hex(final_handler_addr)}")
```

### 繞過路線 2：找沒有 SEHOP 的目標

這條路線更務實，也是 CTF 環境的常見設計：

- **目標是 x86 binary 但 SEHOP 被關閉**：登錄機碼 `DisableExceptionChainValidation=1`，或 WDEG 裡明確關掉，或舊系統（Windows 7 client 預設）。
- **目標本來就不是 x86**：打 x64 binary 時 SEHOP 根本不適用，直接做 DEP+ROP（Ch 23 主題）。
- **目標在 Wine/ReactOS 等相容層**：可能沒有 SEHOP。

判斷流程：

```
          目標是 x86 binary？
                ↓
               Yes
                ↓
         SEHOP 是否開啟？
         ↙              ↘
       No               Yes
       ↓                 ↓
   直接用 Ch 21        需要 info leak
   SEH overwrite      + 偽造 chain
   （過 SafeSEH）      或找別的攻擊面
```

---

## 底層機制：RtlDispatchException 的 SEHOP 校驗位置

> **未實測，理論預期**：以下反組譯片段為概念示意，不是從真實 ntdll 取出的輸出。

在 ntdll 的 `RtlDispatchException` 裡，SEHOP 驗鏈發生在走 chain 之前。大致結構：

```asm
; RtlDispatchException（x86 概念片段，未實測）
RtlDispatchException:
    ; ... 建立 frame ...
    call    RtlpValidateExceptionChain    ; ← SEHOP 在這裡
    test    eax, eax
    jz      TerminateProcess_path         ; 驗失敗：直接 ZwTerminateProcess
    ; ... 正常 chain 走訪 ...
```

`RtlpValidateExceptionChain` 才是真正做上面那段「走鏈、驗 Next 位址範圍、驗 sentinel」邏輯的函式。函式名依 Windows 版本可能略有不同（公開符號只有部分版本有）。

---

## 踩雷集錦

1. **「SEHOP 在 Windows 7 預設開啟」**：不對。Windows 7 的**客戶端版本**（Home、Professional、Ultimate）SEHOP **預設關閉**；Server 2008 R2（同期的 Server 版）預設開啟。這是 Microsoft 當時對相容性的讓步。如果你的 CTF 靶是 Windows 7 client x86，SEHOP 很可能是關的，直接用 Ch 21。

2. **「SEHOP 和 SafeSEH 是同一個機制的不同叫法」**：完全不同。SafeSEH 是編譯時的白名單機制（per-binary，驗 handler 合法性）；SEHOP 是執行時的 chain 完整性驗證（per-process，驗鏈結構）。兩個可以同時開，也可以只開一個。

3. **「在 x64 靶上繞過 SEHOP 才能打 SEH overwrite」**：x64 進程根本沒有 x86 SEH chain（table-based SEH，handler 在 `.xdata`），SEHOP 對 x64 是無意義的概念。x64 pwn 直接跳 DEP+ROP（Ch 23）。

4. **「偽造 chain 不需要 info leak，可以硬編 ntdll 位址」**：Vista SP1+ 的 ntdll 被 ASLR 保護，位址每次開機隨機化。在 Windows 7+ 上，如果 ASLR 開啟，`ntdll!FinalExceptionHandlerPad` 的位址每次都不同，偽造 chain 必須先拿到 leak。沒有 leak 就沒有可靠的繞過。

5. **「找到一個沒有 SafeSEH 的 DLL 就能繞 SEHOP」**：SafeSEH bypass 和 SEHOP bypass 是兩個獨立問題。SafeSEH=False 的 DLL 讓你的 PP gadget 通過 `RtlIsValidHandler` 的 handler 合法性檢查；但 SEHOP 在那之前先驗 chain 結構。兩道都要過。

---

## 進階：再往深一層

### 為什麼 SEHOP 對 heap-based SEH 攻擊沒有完整防護

SEHOP 只驗 stack 上的 SEH chain（透過 TEB 的 `ExceptionList` 走訪）。VEH（Vectored Exception Handler）的鏈結串列是在 **heap 上**（ntdll 維護的雙向鏈結串列），不在 stack 的 SEH chain 裡。如果攻擊者有 heap 寫入原語，能攻擊 VEH 鏈結串列，SEHOP 無法偵測。VEH 攻擊是更高難度的路線，在 Ch 12 有基礎介紹。

### SEHOP + ASLR 的組合強度

SEHOP 的 fake chain 繞過**完全依賴 info leak**。ASLR（Ch 24）讓 ntdll 的 base 和 stack 位址在每次開機都變化。在 SEHOP + ASLR 都開啟的環境（Windows 10/11），繞過 SEH overwrite 的攻擊鏈長度大幅增加：

```
step 1: 找 info leak → 拿 ntdll base
step 2: 找 info leak → 拿 stack 位址
step 3: 靜態分析目標系統 ntdll → 拿 FinalExceptionHandlerPad RVA
step 4: 計算偽 chain
step 5: 確保同時滿足 SafeSEH + SEHOP
```

這也是為什麼現代 x86 CTF 題如果考 SEH overwrite，通常會關掉 ASLR 或 SEHOP 其中之一讓題目聚焦在一個機制上。

### SEHOP 的歷史上下文

SEHOP 的設計最初由 Microsoft Research 提出（Sotirov & Dowd 的 Black Hat 2008 "Bypassing Browser Memory Protections" 間接推動了它的部署）。技術細節可參閱 Symantec 的研究報告 "Mitigations and Exploits" 以及 Thierry Zoller 的 SEHOP 白皮書（2009 年）。

在那個年代，stack cookie（/GS）、SafeSEH、DEP、ASLR、SEHOP 逐步推出，每個機制都是對前一個機制被繞過後的應對——Ch 38 的 EMET 演進史會把這條防禦時間線完整講一遍。

### 面試題

**問**：一個 Windows 7 x86 程式，開了 `/GS` 和 SafeSEH，但系統 SEHOP 預設關閉，問攻擊面。

**答**：
- `/GS` 保護 saved EIP（cookie 擋在前面），但 SEH record 在 cookie 前面 → SEH overwrite 繞 `/GS`
- SafeSEH 開啟，handler 必須通過 `RtlIsValidHandler` → 需要找 SafeSEH=False 的模組裡的 PP gadget
- SEHOP 關閉 → chain 完整性不被驗，不需要偽造 chain
- 攻擊路線：Ch 21 的標準 SEH overwrite，但 PP gadget 要找 SafeSEH=False 的 DLL（`!mona seh` 過濾）

---

## 動手練習

> **環境**：需要 Immunity Debugger + mona.py，靶機 Windows 7 x86（SEHOP 預設關閉，或在登錄機碼手動關閉 `DisableExceptionChainValidation=1`）。

1. 在 Windows 10 的登錄機碼裡找到 `DisableExceptionChainValidation`，把值從 0 改成 1，驗證 SEHOP 是否關閉（用 PowerShell `Get-ProcessMitigation -System` 確認）。
2. 反向操作：把值改回 0，用 Ch 21 的 SEH overwrite exploit（或骨架）送 payload，觀察程式是否直接被 SEHOP 終止（預期：不觸發 PP gadget，程式崩潰或直接退出）。
3. 研究目標系統的 ntdll.dll：用 `rp++` 或 IDA/Ghidra 找 `FinalExceptionHandlerPad` 函式的 RVA（方法：搜尋函式名匯出，或 `dumpbin /exports ntdll.dll`）。記錄 RVA，思考「如果我有 ntdll base leak，偽 chain 怎麼構造」。

---

## 本章重點整理

- SEHOP 在例外分發前走完整條 SEH chain，驗每個 `Next` 在 stack 範圍內且方向合法，鏈必須以 `ntdll!FinalExceptionHandlerPad` 結尾。驗失敗直接 `ZwTerminateProcess`，handler 永遠不被呼叫。
- SEHOP 封堵 Ch 21 的標準 SEH overwrite：payload 蓋掉 nSEH 之後，`Next` 欄位指向非法位址，鏈完整性被破壞，SEHOP 偵測並終止。
- 繞過路線：一是用 info leak 重建合法的偽 chain（讓 SEHOP 驗過），二是直接找沒開 SEHOP 的目標（Windows 7 client 預設關、CTF 環境明確關）。
- SafeSEH 是編譯期的 handler 白名單（per-binary），SEHOP 是執行期的 chain 完整性驗證（per-process）；兩者是互補的雙層防線，不是同一個東西的別名。

---

## 自我檢核

- [ ] 不看筆記，能畫出 SEHOP 的驗鏈流程 ASCII 圖：從 `FS:[0]` 開始、驗 `Next` 的條件、走到 sentinel、sentinel 的結構
- [ ] 能說出 `ntdll!FinalExceptionHandlerPad` 在整個 SEHOP 機制裡的角色：誰注入它、放在哪、驗的是什麼
- [ ] 面試被問「Windows 7 x86 靶、SafeSEH 開、SEHOP 關，攻擊路線是什麼」：能說出正確的前提確認方法和利用步驟
- [ ] 能說出偽造 chain 繞過 SEHOP 的三個前提（info leak × 2、ntdll 靜態分析），以及為什麼它在現代環境難度極高
- [ ] 能正確說明 SafeSEH vs SEHOP 的差異（各自驗什麼、在哪層做、失效條件）

---

## 延伸閱讀

### 論文 / 白皮書

- **"Bypassing Browser Memory Protections"** — Alexander Sotirov & Mark Dowd（Black Hat US 2008）
  - **讀哪裡**：第 6 節「SEHOP」
  - **學什麼**：SEHOP 設計的動機與初始分析；偽 chain 繞過的最早公開討論之一
  - **和本章關聯**：本章的偽 chain 繞過技法框架直接來自這篇的分析
  - **前提**：Ch 11（SEH 機制）+ Ch 21（SEH overwrite）+ 本章

- **"SEHOP — Structured Exception Handler Overwrite Protection"** — Thierry Zoller（2009）
  - **讀哪裡**：全文（公開 PDF，搜尋 "Zoller SEHOP 2009"）
  - **學什麼**：SEHOP 機制的完整技術描述（比本章更詳細的 `RtlpValidateExceptionChain` 邏輯），以及當年的繞過 PoC
  - **和本章關聯**：本章的驗鏈邏輯偽碼和機制描述以這篇為主要依據；讀這篇能驗證本章對機制的理解
  - **前提**：本章讀完

### 官方文件

- **[Process Mitigations — Windows Dev Docs](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-setprocessmitigationpolicy)**
  - **讀哪裡**：`ProcessEnableReadWriteVmLogging` 旁邊的 SEHOP 相關 flag 說明（`PROCESS_MITIGATION_BINARY_SIGNATURE_POLICY` 下游）
  - **學什麼**：怎麼用 API 查詢或設定 SEHOP 的 per-process 狀態（`ProcessSEHOPEnabled`）
  - **和本章關聯**：本章說的「WDEG 裡設定」底層就是這個 API

- **[Exploit Protection — WDEG（Windows Defender Exploit Guard）— Microsoft Learn](https://learn.microsoft.com/en-us/microsoft-365/security/defender-endpoint/exploit-protection-reference)**
  - **讀哪裡**：「Structured Exception Handler Overwrite Protection (SEHOP)」條目
  - **學什麼**：SEHOP 在 Windows 10/11 的部署方式、per-app 覆寫方法
  - **和本章關聯**：本章的「SEHOP 預設狀態」表格資訊的官方來源

### 部落格

- **Corelan Team — "Exploit writing tutorial part 3b: SEH — the sequel"**（[corelan.be](https://www.corelan.be/index.php/2009/07/28/seh-based-exploit-writing-tutorial-continued-just-another-example/)）
  - **讀哪裡**：文末的「SEHOP」一節
  - **學什麼**：SEH overwrite 在有 SEHOP 的環境下「剩下什麼攻擊面」的實務分析
  - **和本章關聯**：本章偽 chain 繞過的實踐面向；Corelan 的 mona workflow 是本章動手練習的工具基礎
  - **前提**：Ch 21（SEH overwrite）+ 本章

SEH overwrite 在現代 Win10/11 x64 環境幾乎已是博物館技法，但它留下的防禦演進思路（SafeSEH → SEHOP → table-based SEH）是理解後面所有機制的必要底子。下一章進 DEP 和 ROP——那才是 x64 userland pwn 的主戰場。

→ [Ch 23 — DEP + ROP on Windows](./23-dep-rop.md)
