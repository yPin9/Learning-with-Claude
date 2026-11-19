# Ch 11 — SEH overwrite 原理

> **目標**：搞懂 Windows x86 的 Structured Exception Handling（SEH）為什麼存在、`_EXCEPTION_REGISTRATION_RECORD` 怎麼串成鏈掛在 stack 上、以及當 `/GS` cookie 擋住了「直接蓋 saved return address」這條路時，為什麼 SEH record 反而是一個現成的側門。讀完能自己畫出 SEH chain 的 stack 佈局，解釋 SEH overwrite 和「控 EIP」有什麼本質差異。

> **環境**：Win10 x64 22H2 + WinDbg（2026）+ mona v3 + Python3

---

## 為什麼需要這個？

Part 2（Ch 7–10）教的是最直覺的 stack overflow：覆寫到 saved return address，函式 `ret` 時 EIP 直接變成你寫的值。這條路好懂，但有個前提——程式真的能一路 overflow 到 saved return address，而且中間沒有東西擋。

擋路的東西之一是 `/GS` stack cookie：編譯器在 saved EBP / saved return address 前面插一個隨機值（cookie），函式返回前先檢查這個值有沒有被動過，動過就直接終止程式，不會走到那個被你蓋掉的 return address。

但這裡有個關鍵事實：**`/GS` cookie 只保護它自己後面（往高位址）的東西**，它不會回頭保護已經在它前面、更低位址的資料。如果這個函式裡還有一個 SEH record（`_EXCEPTION_REGISTRATION_RECORD`），而這個 record 的位置比 cookie 更靠近 buffer——overflow 會先蓋到它，根本還沒碰到 cookie。

SEH overwrite 要解決的問題正是這個：**當 overflow 長到會先觸發例外（access violation），或你想繞開 cookie 檢查，而程式裡又有一個比 cookie 更早被蓋到的 SEH record 時，怎麼利用這個 record 把執行流劫持過來。**

這一章只建立機制和「為什麼可行」的直覺；完整的利用步驟（nSEH 短跳、POP POP RET）留給 Ch 12。

> 本章只取 OSED 需要的切片，把 SEH overwrite 講到能自己動手。如果你想看 x86 SEH 分發流程的完整底層機制（`KiUserExceptionDispatcher`、`RtlDispatchException`、`__except_handler3/4`、CONTEXT 結構），去讀 [`windows_pwn` Ch 11](../windows_pwn/11-seh-x86.md)——那裡挖得更深，本課不重複。

## 先建立直覺：SEH 是什麼、為何存在

Windows 需要一套機制讓程式碼對「硬體例外」（除以零、存取違規、非法指令）和「程式自己丟出的例外」（`RaiseException`、C++ `throw`）做出反應——不是讓程式直接當掉，而是給它機會接手處理，或至少做清理再往上拋。

Linux 的對應機制是 `signal`（`SIGSEGV`、`SIGFPE`……）：kernel 發 signal，handler 是一個**全域的、per-thread 的函式指標**，存在 kernel 維護的 `sigaction` 表裡，和執行 stack 完全分離。

Windows x86 的設計決策不一樣：SEH 把每個函式的「例外處理框（frame）」直接**推到執行 stack 上**，用一條鏈串起來，鏈頭的位址存在 `fs:[0]`（x86 下 `FS` 段暫存器指向 TEB，`fs:[0]` 就是 `TEB.NtTib.ExceptionList`）。這個設計的好處是實作簡單、不需要額外的全域表、函式進出時 push/pop 一下就能動態建立/拆除處理框。代價是：**處理框（更精確地說是框裡的 handler 指標）和一般的 stack 資料住在同一塊記憶體裡**。

這個代價就是整章要講的攻擊面：只要有 stack buffer overflow，而且 overflow 的方向能摸到這個 record，就能蓋掉裡面的 handler 指標。

## `_EXCEPTION_REGISTRATION_RECORD`：只有兩個欄位

```c
/* winnt.h */
typedef struct _EXCEPTION_REGISTRATION_RECORD {
    struct _EXCEPTION_REGISTRATION_RECORD *Next;     /* nSEH：指向鏈裡下一個（更舊的）record */
    PEXCEPTION_ROUTINE                     Handler;  /* SEH：這個 frame 的例外處理函式指標 */
} EXCEPTION_REGISTRATION_RECORD, *PEXCEPTION_REGISTRATION_RECORD;
```

就 8 bytes（x86，兩個 4-byte 指標）：

- `Next`（Corelan 教程慣稱 **nSEH**）：指向鏈裡前一個 record。鏈尾的 `Next` 是 sentinel `0xFFFFFFFF`。
- `Handler`（慣稱 **SEH** / SEH handler）：一個函式指標，型別是 `EXCEPTION_DISPOSITION (*)(EXCEPTION_RECORD*, void*, CONTEXT*, void*)`。當例外分發走到這個 record 時，OS 會呼叫它。

這兩個名詞（nSEH / SEH）之後整課都會用，記清楚：**nSEH 在前（低位址），SEH handler 在後（高位址，nSEH + 4）**。

## SEH chain 的 stack 佈局：ASCII 圖

一個函式如果用了 `__try`（MSVC）或者呼叫了會建立 SEH frame 的程式碼，它的 stack 上就會多出一個這樣的 record，鏈頭永遠是最新（最深）的那個：

```
高位址（stack bottom）
┌───────────────────────────────────────────────┐
│  fs:[0]  ──────────────────────────────────┐  │   ← TEB.NtTib.ExceptionList
└──────────────────────────────────────────────┼──┘     （SEH chain 鏈頭指標）
                                               │
┌───────────────────────────────────────────────▼─┐
│  caller 的 stack frame                          │
│  ...                                            │
│  ┌────────────────────────────────────────┐    │
│  │ EXCEPTION_REGISTRATION_RECORD [frame A]│    │
│  │  +0x00  Next (nSEH)  ──────────────┐   │    │
│  │  +0x04  Handler (SEH) → handler_A  │   │    │
│  └─────────────────────────────────────┼───┘    │
└────────────────────────────────────────┼────────┘
                                          │
┌─────────────────────────────────────────▼──────┐
│  目前函式的 stack frame                          │
│  ┌────────────────────────────────────────┐    │
│  │ EXCEPTION_REGISTRATION_RECORD [frame B]│    │
│  │  +0x00  Next (nSEH)  → 0xFFFFFFFF (尾) │    │
│  │  +0x04  Handler (SEH) → handler_B      │    │
│  └────────────────────────────────────────┘    │
│  ...local 變數...                               │
│  char vuln_buf[N]   ← overflow 從這裡開始       │
└──────────────────────────────────────────────────┘
低位址（stack top，ESP 附近）
```

鏈的方向是 `fs:[0]` → frame B（最新）→ frame A（較舊）→ `0xFFFFFFFF`（鏈尾 sentinel）。`vuln_buf` 在更低位址，往高位址 overflow 會先碰到 frame B 的 `Next`、再碰到 `Handler`——這正是攻擊的入口。

## 核心機制：為什麼 /GS 擋不住 SEH overwrite

Part 2 已經建立了「`/GS` cookie 擋在 saved EBP / saved return address 前面」這個事實。把 SEH record 放進同一張圖裡看：

```
記憶體方向（低位址 → 高位址，overflow 往這個方向寫）：

  [vuln_buf][...填充...][nSEH][SEH handler][...][cookie][saved EBP][saved ret addr]
     ↑                    ↑        ↑                        ↑
  overflow 起點      overflow 路徑上   覆蓋目標         /GS 門衛（在更高位址）
                      先碰到這裡
```

Overflow 是連續往高位址寫，寫到多長就蓋到多遠。如果寫到 `nSEH`/`SEH handler` 就停手，cookie 和 saved return address**完全沒被碰到**——`/GS` 的檢查邏輯只在函式 epilogue 比對 cookie 值，而這個函式可能根本沒機會走到那段 epilogue：因為例外（access violation）會先被觸發，執行流被 OS 的例外分發器接管，跳去呼叫被你蓋掉的 `Handler`。

換句話說：**SEH overwrite 不是繞過 `/GS` 的算法性弱點，而是繞過了「`/GS` 檢查發生的時間點」**——cookie 檢查要等到函式正常返回（epilogue）才做；SEH handler 的呼叫發生在那之前（例外分發的那一刻），cookie 根本還沒機會派上場。

這也點出 SEH overwrite 和「直接控 EIP」的本質差異：

- **直接控 EIP**（Part 2 教法）：overflow 寫到 saved return address，函式正常 `ret` 時 EIP 變成攻擊者的值。前提是沒有 cookie，或 cookie 被繞過。
- **SEH overwrite**（本章起）：不去碰 saved return address，而是蓋掉一個**例外處理器指標**。你不直接控制 EIP，你控制的是「當例外發生時，OS 會呼叫誰」。EIP 要等到例外真的被觸發、OS 呼叫到被蓋的 `Handler` 時才間接落到你手上。

## 多範例：什麼情況下 overflow 會先碰到 SEH record

| 情境 | overflow 行為 | 結果 |
|---|---|---|
| Buffer 很短，距離 saved EIP 不遠，中間沒有 SEH record | 一路蓋到 saved EIP | 走 Part 2 的技法，不需要 SEH |
| Buffer 很長，overflow 必然先經過函式內的 SEH record | 寫到 nSEH/Handler 時，繼續寫會**先觸發例外**（例如寫出 committed stack 範圍、guard page） | 走不到 saved EIP；要靠 SEH overwrite |
| 開了 `/GS`，但 SEH record 在 cookie 之前 | overflow 寫到 SEH record 時 cookie 還沒被碰 | SEH overwrite 可行，`/GS` 對它無效 |
| 程式本身邏輯會在 overflow 後解引用一個被蓋壞的指標 | overflow 蓋壞局部指標 + SEH record，程式繼續跑到解引用那行 | 觸發 access violation → SEH 分發開始 |

一個常見的邊界案例：如果 overflow **根本沒有觸發任何例外**（例如只是蓋壞了一些不會被用到的局部變數，函式照樣正常返回），SEH overwrite 完全沒有機會發動——因為例外分發器只在例外真的發生時才會去走這條鏈。**SEH overwrite 永遠需要一個「觸發點」**：要讓程式在 overflow 之後真的撞到一個會產生例外的狀況。Ch 12 會列出幾種常見觸發方式（vulnserver 的 `GMON` 指令就是一個現成例子）。

## 多範例：同一支程式，兩種 overflow 長度的不同命運

拿一個假想的、和 vulnserver 架構類似的函式來對照（未實測，理論預期，純粹用來建立直覺）：

```c
void handle_gmon(char *client_data) {
    char buf[2000];
    strcpy(buf, client_data);   /* 沒有長度檢查 */
    /* ... 後續處理 ... */
}
```

假設這個函式的 stack 上，從 `buf` 往高位址數，`buf` 之後 2600 bytes 是一個 SEH record，再往後 400 bytes 是 saved EBP / saved return address：

| `client_data` 長度 | 蓋到哪裡 | 會發生什麼 |
|---|---|---|
| 500 bytes | 只蓋到 `buf` 裡面，碰不到任何 record | 函式正常返回，什麼也沒發生 |
| 2700 bytes | 蓋過 SEH record 的 `Next`/`Handler`，但沒蓋到 saved return address | 如果函式接下來的程式碼觸發了例外（例如存取已被破壞的某個指標、或 overflow 本身撞出 committed stack 範圍），SEH 分發開始，走 Ch 12 的路線；如果沒觸發例外，函式試圖用被蓋壞的 SEH record 做正常的 SEH 清理（函式返回時的 epilogue 會 pop 掉這個 record、恢復 `fs:[0]`），用一個已經被蓋爛的 `Next` 去恢復 `fs:[0]`，這本身也很可能直接讓程式崩潰，但走的是另一條例外路徑，不一定是你能預期控制的 |
| 3200 bytes | 蓋過 SEH record，也蓋到了 saved return address | 如果函式真的正常走到 `ret`（沒有任何中途例外），直接控 EIP 就夠用，不需要 SEH overwrite；但如果中途已經因為 SEH record 被蓋壞而先出事，可能根本走不到 `ret` 那一行 |

這張表要說的是：**overflow 長度和「會不會先觸發例外」不是你能完全預先用紙筆算出來的**，很大程度取決於程式邏輯什麼時候會去動用那些被蓋壞的資料。這也是為什麼 Ch 11–13 和 Ch 7–8 一樣強調「先送 crash、用偵錯器看清楚現場，再反推佈局」，而不是憑程式碼推理就動手寫完整 exploit。

## 底層機制：例外分發到 handler 的簡化流程

完整流程（`KiTrap0E` → `KiUserExceptionDispatcher` → `RtlDispatchException`）在 [`windows_pwn` Ch 11](../windows_pwn/11-seh-x86.md) 有逐步拆解，這裡只取和利用直接相關的骨架：

```
1. CPU 觸發例外（例如存取違規）
2. kernel 建立 EXCEPTION_RECORD + CONTEXT，轉回 user-mode
3. ntdll!KiUserExceptionDispatcher 呼叫 RtlDispatchException
4. RtlDispatchException 從 fs:[0] 開始走鏈：
     對每個 record，呼叫 record->Handler(ExceptionRecord, EstablisherFrame, Context, ...)
     若 Handler 被攻擊者蓋成任意位址 → OS 直接把 EIP 轉移到那裡執行
5. 走到鏈尾（0xFFFFFFFF）還沒人處理 → 未處理例外 → 程式崩潰
```

第 4 步是整個 SEH overwrite 的靶心：`RtlDispatchException` **不會檢查這個 Handler 指標是不是合法函式**（在 SafeSEH/SEHOP 出現之前完全不檢查）——它直接呼叫。這是「設計上信任 stack 上的資料」留下的後門。

## 對比取捨：直接控 EIP vs SEH overwrite

| 面向 | 直接控 EIP（Part 2） | SEH overwrite（本章起） |
|---|---|---|
| 覆寫目標 | saved return address | `_EXCEPTION_REGISTRATION_RECORD.Handler` |
| 需要觸發例外嗎 | 不需要，函式正常 `ret` 即可 | **需要**，必須讓程式撞到一個例外 |
| 繞 `/GS` 嗎 | 不行，cookie 會先擋 | 可以，因為時間點比 cookie 檢查早 |
| EIP 何時落入控制 | 函式 `ret` 那一刻 | 例外分發呼叫 `Handler` 那一刻 |
| 需要額外 gadget 嗎 | 不一定（可直接填 jmp esp 等位址） | 需要（Ch 12 的 POP POP RET） |
| 受何種緩解限制 | `/GS`、ASLR | SafeSEH（Ch 13）、SEHOP |
| 典型適用場景 | 短 overflow，沒有 SEH record 擋在中間 | 長 overflow、有 `/GS`、程式裡有 SEH frame |

## 多範例：巢狀 `__try` 讓 chain 變長，但攻擊入口不變

一個函式可能有多層巢狀的 `__try`，這會讓 stack 上同時掛著多個 SEH record：

```c
void handle_request(char *data) {
    __try {
        parse_header(data);
        __try {
            char buf[256];
            strcpy(buf, data);     /* overflow 從這裡發生 */
        } __except (EXCEPTION_EXECUTE_HANDLER) {
            /* 內層 handler */
        }
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        /* 外層 handler */
    }
}
```

對應的 chain（示意，未實測）：

```
fs:[0] → [內層 record]（最新，離 buf 最近） → [外層 record] → 0xFFFFFFFF
```

重點是：**不管鏈有幾層，`buf` overflow 永遠先碰到離它最近（最新）的那個 record**——也就是內層。多一層 `__try` 不會讓攻擊變得更複雜，你永遠只需要關心「離 overflow 起點最近的那個 record」，因為例外分發也是從鏈頭（最新的那個）開始呼叫，不管外層還有幾個 record 排在後面。這和 Ch 11 ASCII 圖裡「frame B 比 frame A 更靠近 buf、也更靠近鏈頭」是同一件事的延伸。

## 核心機制補充：CONTEXT 結構裡的一個進階應用

Ch 11 的 `Handler` 簽名裡，第三個參數 `ContextRecord`（型別 `CONTEXT*`）是例外發生瞬間完整的暫存器快照（`Eip`、`Esp`、`Eax`……）。在標準流程裡你不需要碰它——POP POP RET 的操作完全在 stack 層面完成，不需要讀寫 `CONTEXT`。但知道它存在，能幫你理解一個進階變化：如果某個 handler 回傳 `ExceptionContinueExecution`，系統會呼叫 `NtContinue(ContextRecord, FALSE)`，用 `ContextRecord->Eip` 的值繼續執行——這代表**修改 CONTEXT 裡的 Eip 欄位，是另一條（比 POP POP RET 更進階、也更少用到）轉移控制流的路徑**。本課不展開（會牽涉到更複雜的 handler 寫法），但如果你之後讀進階資料看到「透過 CONTEXT.Eip 控制流程」的說法，知道它的位置在哪就不會一頭霧水。

## 對比：Windows SEH vs Linux（無此機制）

Linux 沒有「例外處理器指標住在 stack 上」這種設計。`SIGSEGV` 的 handler 是透過 `sigaction()` 設定、存在 kernel 維護的 per-process 表裡；C++ exception 走 `.eh_frame`（DWARF unwind 資訊），放在唯讀的 ELF section 裡。兩者都**不在可寫的執行 stack 上**，所以單純的 stack buffer overflow 碰不到它們——Linux pwn 裡沒有「SEH overwrite」這個攻擊類別的對應物。

這點是你從 Linux pwn 轉來 OSED 最值得記住的一個「Windows 專屬利用面」：Windows 把一部分控制流資料故意放進了攻擊者可寫的區域，這是歷史設計決策（NT 3.1 時代，簡單換來了安全代價），不是 bug。

## 踩雷集錦

1. **錯誤直覺：「SEH overwrite 就是另一種控 EIP 的方法，兩者沒差」。** 正確：直接控 EIP 覆寫 saved return address，函式 `ret` 時發生；SEH overwrite 覆寫的是例外處理器指標，**必須有例外被觸發**才會被呼叫。沒有觸發點，蓋了也沒用。

2. **錯誤直覺：「`/GS` 開了就代表 SEH overwrite 也會被擋」。** 正確：`/GS` cookie 只保護它後面（更高位址）的 saved EBP/saved return address，不保護更低位址的 SEH record。SEH overwrite 正是利用這個位置關係繞過 `/GS`。

3. **錯誤直覺：「只要 overflow 夠長，蓋到 Handler 就直接拿到 shellcode 執行」。** 正確：蓋到 `Handler` 只是把「例外發生時 OS 會呼叫誰」換成了你的值；中間還要經過 POP POP RET 轉一手才能真正落到你控制的 shellcode（Ch 12）。直接把 shellcode 位址填進 `Handler` 不會按預期工作，因為 handler 被呼叫時的呼叫慣例和 stack 狀態不是你能直接利用的執行環境。

4. **錯誤直覺：「nSEH 和 SEH handler 是同一個東西，隨便填哪個都一樣」。** 正確：`nSEH`（`Next`，offset +0）和 `SEH handler`（`Handler`，offset +4）是兩個分開的 4-byte 欄位，各自有不同的角色——nSEH 之後會放短跳指令，SEH handler 放 gadget 位址。混著填會讓整條利用鏈斷掉。

5. **錯誤直覺：「SEH chain 鏈頭是最舊的 frame」。** 正確：`fs:[0]` 指向**最新（最深）**的 frame，鏈尾（`0xFFFFFFFF`）才是最舊的。函式呼叫時新 record 被 push 成鏈頭，返回時再 pop 掉——這和直覺可能預期的順序相反。

## 進階：再往深一層

- SEH overwrite 利用的 record 到底在哪個 stack offset，取決於編譯器版本、優化層級、是否有巢狀 `__try`，不能假設固定在某個位置——這也是為什麼 Ch 12 一定要靠 crash/pattern 實測找 offset，而不是靠理論推算。
- vulnserver（以及許多老舊網路服務）不需要程式碼裡真的寫 `__try`——只要程式呼叫鏈上有任何函式建立了 SEH frame（例如 CRT 的某些函式、`SetUnhandledExceptionFilter` 之外的預設 frame），你的 overflow 就有機會碰到可用的 record。實務上這是用 mona/`!exchain` 觀察到的，不是靠讀原始碼推出來的。
- `EXCEPTION_INFORMATION` 裡的存取類型（讀/寫/執行）和故障位址，在判斷「我蓋到的 SEH record 是不是真的被拿來分發」時很有用：如果崩潰時的 fault address 剛好等於你填的某個值（例如 `0x41414141`），通常代表程式正試圖把那個值當位址解引用——這可能是 SEH 分發正在呼叫你的 `Handler`，也可能是別的解引用路徑，兩者在 WinDbg 裡的畫面不完全一樣，要搭配 `!exchain` 一起看才能確認是哪一種。
- 同一個漏洞函式有時候會有多條觸發路徑：一條走到 SEH record 就先炸、另一條（例如縮短輸入或換一個相似指令）可能根本碰不到 SEH record，直接走 Part 2 的直接控 EIP。OSED 考場遇到不確定走哪條路時，兩種假設都值得各送一次測試資料驗證，不要只認定一種可能。

## 本章重點整理

- SEH 把例外處理框（`_EXCEPTION_REGISTRATION_RECORD {Next, Handler}`）直接放在執行 stack 上，鏈頭位址存在 `fs:[0]`——這是 Windows 一級公民的機制，不是語言層功能。
- `Next`（nSEH）和 `Handler`（SEH）各 4 bytes，nSEH 在前；鏈尾 sentinel 是 `0xFFFFFFFF`。
- `/GS` cookie 只保護它後面（更高位址）的資料；SEH record 若在更低位址，overflow 蓋到它時 cookie 根本還沒被碰——這是 SEH overwrite 繞 `/GS` 的根本原因。
- SEH overwrite 必須有一個「觸發點」讓例外真的發生，和直接控 EIP（`ret` 即觸發）不同。
- Linux 的例外處理資訊不在可寫 stack 上，這個攻擊類別是 Windows x86 獨有的利用面。

## 面試題：為什麼「蓋掉 handler 指標」在 Windows 上行得通，但聽起來像是很基本的設計失誤？

這其實是 1990 年代初的設計決策在幾十年後被重新審視的典型案例。NT 3.1（1993 年）設計 SEH 時，攻擊者主動利用記憶體損壞去劫持控制流還不是主流的威脅模型——當時的優先順序是「讓例外處理機制簡單、高效、不需要額外的全域表」。把處理框放在 stack 上完全符合這個優先順序：函式呼叫自然地建立 frame，函式返回自然地拆除 frame，不需要像 x64 後來的 table-based 方案一樣，在編譯期就把所有 unwind 資訊算好、塞進 PE 的唯讀 section。

這個設計在威脅模型改變之後（stack buffer overflow 被大量武器化的 2000 年代初）才顯出代價，而 Windows 的回應路徑——`/GS` → SafeSEH → SEHOP → x64 table-based SEH——正好是一條逐步修補、而不是從頭重新設計的歷史軌跡。理解這條軌跡，比死記每個緩解機制的細節更重要：它解釋了為什麼這些機制彼此之間常常有「補洞沒補全」的縫隙。

## 自我檢核

- [ ] 能不看筆記畫出一個函式裡 SEH record 和 `/GS` cookie 的相對位置圖，並說明 overflow 寫到哪裡時會先碰到誰
- [ ] 能解釋為什麼 SEH overwrite 繞過的不是 `/GS` 的演算法，而是它檢查發生的「時間點」
- [ ] 能說出 nSEH 和 SEH handler 分別是哪個欄位、各佔幾個 bytes、哪個在前
- [ ] 能說明為什麼「蓋到 Handler」不等於「馬上執行 shellcode」，中間還缺什麼
- [ ] 能解釋為什麼 Linux pwn 沒有對應的 SEH overwrite 攻擊面

## 延伸閱讀

- **Corelan — "Exploit writing tutorial part 3: SEH Based Exploits"**（[corelan.be](https://www.corelan.be/index.php/2009/07/25/writing-buffer-overflow-exploits-a-quick-and-basic-tutorial-part-3-seh/)）
  - 讀哪裡：前半「Basics about SEH」到「where's our overflow」。
  - 學什麼：SEH 機制和攻擊入口的原始經典講法，本章結構和術語直接對應這篇。
  - 前提：讀過本章的 record 結構和 stack 圖即可對照。
- **Connor McGarr — exploit development 系列文章**（[connormcgarr.github.io](https://connormcgarr.github.io/)）
  - 讀哪裡：站內搜尋 SEH 相關文章。
  - 學什麼：現代視角重講 SEH overwrite，和 OSED 教材的用語高度對齊。
  - 前提：先讀完本章建立基本詞彙（nSEH/SEH/record）。
- **Microsoft Learn — `EXCEPTION_REGISTRATION_RECORD` / Structured Exception Handling**（[learn.microsoft.com](https://learn.microsoft.com/en-us/windows/win32/debug/structured-exception-handling)）
  - 讀哪裡：SEH 概念頁 + `__try`/`__except` 語法參考。
  - 學什麼：官方對 SEH 語意（`EXCEPTION_EXECUTE_HANDLER` 等 filter 值）的定義，查語意時的仲裁來源。
  - 前提：無，隨時可查。
- **[`windows_pwn` Ch 11 — 例外處理架構 I：x86 SEH chain](../windows_pwn/11-seh-x86.md)**
  - 讀哪裡：全文，尤其是「例外分發流程：從硬體到 handler」一節。
  - 學什麼：完整的 `KiTrap0E` → `KiUserExceptionDispatcher` → `RtlDispatchException` 流程、CONTEXT 結構、EH3/EH4 細節；本章只取切片，internals 更深看那裡。
  - 前提：本章讀完後再看，會更清楚哪些是本章省略的細節。

下一章把「蓋到 Handler 之後要做什麼」補完：POP POP RET 這個核心 gadget 為什麼存在、nSEH 短跳怎麼算、以及一條完整的 vulnserver `GMON` exploit 骨架。

→ [Ch 12 — SEH exploit 流程：nSEH/SEH、POP POP RET、短跳+長跳](./12-seh-exploit-flow.md)
