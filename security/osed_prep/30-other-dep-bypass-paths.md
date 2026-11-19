# Ch 30 — WriteProcessMemory 等其他路徑 + 除錯 ROP chain

> **目標**：了解 `VirtualProtect` 之外的 DEP bypass 替代路徑——`VirtualAlloc`（配一塊新的 RWX 記憶體）、`WriteProcessMemory`（把 shellcode 搬進既有可執行區)、`SetProcessDEPPolicy`（舊系統的 process 層關閉法）,各自的取捨;學會 `pushad` 一次擺多參數的技巧;並掌握一套系統化在 WinDbg 裡除錯 ROP chain 的方法——chain 歪掉時怎麼定位是哪個 gadget 出的問題。

> **環境**：Win10 x64 + WinDbg（2026）+ mona v3 + Python 3

## 為什麼 VirtualProtect 不是唯一答案

Ch 29 練的 `VirtualProtect` 路線有一個隱藏前提：**你要能精確算出或推出 shellcode 在 stack 上的絕對位址**。多數情況下這個前提成立,但不是永遠成立——如果 overflow 的空間配置特殊、或者你想要一個「不依賴算出精確位址」的備案,`VirtualAlloc` 和 `WriteProcessMemory` 就派上用場。這一章把它們和各自的取捨講清楚,再把本課 Part 7 一直在用的「未實測,理論預期」標注,換成一套真正能在 WinDbg 裡驗證 ROP chain 對不對的方法——這才是讓理論變成能打的 exploit 的最後一塊拼圖。

## 先建立直覺：三條路徑各解決什麼問題

```
問題：shellcode 的 bytes 已經在記憶體某處(通常是 stack),但那一頁不可執行。

路徑一（Ch 29，VirtualProtect）：
  shellcode 已經在的那一頁 ──VirtualProtect──→ 同一頁變成可執行 ──跳過去

路徑二（本章，VirtualAlloc + 複製）：
  (不管 shellcode 現在在哪) ──VirtualAlloc──→ 全新一塊 RWX 記憶體
                                    │
                                    ▼
                          把 shellcode 複製進這塊新記憶體（WriteProcessMemory 或等效)
                                    │
                                    ▼
                                跳到新記憶體執行

路徑三（本章，WriteProcessMemory 到既有可執行頁）：
  shellcode 原本在不可執行的頁 ──WriteProcessMemory──→ 複製到「已經可執行」的頁
                                                              │
                                                              ▼
                                                          跳到那裡執行
```

三條路徑的共同點：**最終目的都是讓 CPU 在某一頁「已標記可執行」的記憶體上 fetch 到 shellcode 指令**。差異只在於「這頁是原有的頁改保護」還是「全新分配的頁」還是「借用別人本來就可執行的頁」。

## 核心：VirtualAlloc 路徑

### 簽名與參數（MSDN 為準）

```c
LPVOID VirtualAlloc(
    LPVOID lpAddress,       // 期望的位址;NULL = 讓系統自己選
    SIZE_T dwSize,          // 要分配的大小
    DWORD  flAllocationType,// 分配類型
    DWORD  flProtect        // 保護旗標
);
// 回傳:成功則為分配到的起始位址;失敗回傳 NULL
```

| 參數 | 本章用值 | 備註 |
|---|---|---|
| `lpAddress` | `NULL` | 讓 OS 自己選位址——這正是這條路線的優勢:**不需要知道任何既有位址,連自己分配的位址也不用猜,回傳值會告訴你** |
| `dwSize` | 略大於 shellcode 長度,如 `0x1000` | 分配粒度通常是以頁（4KB）為單位,系統會自動向上取整 |
| `flAllocationType` | `MEM_COMMIT \| MEM_RESERVE = 0x3000` | 同時保留位址空間並實際分配實體記憶體支援 |
| `flProtect` | `PAGE_EXECUTE_READWRITE = 0x40` | 分配出來的頁**一開始就是**可執行,不需要再呼叫一次改保護 |

### 為什麼它的回傳值是個麻煩

`VirtualAlloc` 成功後,回傳值（新分配記憶體的起始位址）會放在 **EAX**——這是 ROP chain 裡少數「執行完才知道結果,而且是放在暫存器而不是你指定的某個記憶體位址」的情況。接下來要把 shellcode 複製進這塊新記憶體,下一個 gadget/函式呼叫就需要用到這個 EAX 值,但 ROP chain 的「下一步」通常是另一個函式呼叫（需要把參數排在 stack 上,而不是留在暫存器裡)。

```
VirtualAlloc 呼叫完的狀態：

  EAX = 新分配的 RWX 記憶體起始位址（執行時才知道,無法事先寫進 payload）
   │
   │ 需要一個 gadget 把 EAX「搬」到 WriteProcessMemory 呼叫需要的 stack 參數位置
   ▼
  WriteProcessMemory(hProcess, EAX, shellcode_src, len, &written)
                              ↑
                      這個參數位置需要動態填入 EAX 的值
```

常見解法：用 `pushad`（見下方)或 `mov [stack_slot], eax ; ret` 這類 gadget,把 EAX 的值寫進下一個函式呼叫的對應 stack slot。這也是為什麼 **VirtualAlloc 路徑永遠要搭配至少一次「把回傳值搬到下個呼叫參數位置」的 gadget**,比 `VirtualProtect` 單獨使用多一道工序。

## 核心：WriteProcessMemory 路徑

### 簽名與參數（MSDN 為準）

```c
BOOL WriteProcessMemory(
    HANDLE  hProcess,                  // 目標 process handle
    LPVOID  lpBaseAddress,             // 目標寫入位址(必須可寫)
    LPCVOID lpBuffer,                  // 來源資料位址(shellcode 現在在哪)
    SIZE_T  nSize,                     // 要寫的 bytes 數
    SIZE_T  *lpNumberOfBytesWritten    // [out] 實際寫入的 bytes 數,可寫位址
);
```

| 參數 | 本章用值 | 備註 |
|---|---|---|
| `hProcess` | `GetCurrentProcess()` 的回傳值,或常數 `-1`（`0xFFFFFFFF`,代表目前行程的 pseudo handle） | 攻擊目標和執行 shellcode 的目標是同一個行程,用 pseudo handle 省掉一次額外呼叫 |
| `lpBaseAddress` | VirtualAlloc 分配到的 RWX 位址,或既有可執行頁上的某個位置 | 這是 WriteProcessMemory 路徑的「目的地」 |
| `lpBuffer` | shellcode 目前所在的位址（通常也在 stack 上） | 這是「來源」,和 `lpBaseAddress` 搞反會變成往反方向複製,把目的地的垃圾值複製回 shellcode 原本的位置 |
| `nSize` | shellcode 長度 | |
| `lpNumberOfBytesWritten` | 任意可寫位址（可為 `NULL`，部分文件指出可傳 `NULL` 跳過回報，實務上仍建議給可寫位址更穩） | |

### 「把 shellcode 複製到 .text」在現代系統幾乎不可行

Ch 27 的對比表格提過這點,這裡展開：現代 Windows 的 `.text` 節（程式碼段）預設是 `PAGE_EXECUTE_READ`——**可執行但不可寫**。`WriteProcessMemory` 寫入一個不可寫的頁一樣會失敗（回傳 `FALSE`）。所以「把 shellcode 寫到既有可執行頁」這個原始構想,在現代系統裡幾乎找不到同時滿足「可寫且可執行」的頁——除非目標程式自己開了一個異常寬鬆的區段（很罕見,舊式程式偶爾有)。

**更實際的用法**：`WriteProcessMemory` 幾乎總是搭配 `VirtualAlloc`——先用 `VirtualAlloc` 要一塊全新的 RWX 記憶體（它一開始就同時可寫可執行),再用 `WriteProcessMemory` 把 shellcode 從 stack 複製進去。這也解釋了為什麼本章把 `VirtualAlloc` 和 `WriteProcessMemory` 放在一起講——它們通常是一組,不是互相替代的兩個選項。

## 核心：SetProcessDEPPolicy（舊系統路徑,背景知識）

```c
BOOL SetProcessDEPPolicy(
    DWORD dwFlags   // 0 = 關閉 DEP,其他值對應不同層級的開啟/permanent 設定
);
```

這支 API 讓一個行程在**執行期間**修改自己的 DEP 政策,理論上攻擊者如果能先執行到一段可控的程式碼呼叫這支函式把 DEP 關掉,之後就能回到「直接跳 shellcode」的老路。但它有關鍵限制使它在現代系統上基本沒有實戰價值：

- 只在 **Windows XP SP3 / Windows Server 2008** 這類舊系統上有效;更新的系統對它的行為做了限制（例如一旦行程已經用某些旗標鎖定 DEP 設定為 permanent,呼叫這支 API 不會再改變行為）。
- 即使在支援的系統上,呼叫它本身也是一次函式呼叫,一樣需要先用 ROP 擺好呼叫——這代表你已經有能力做 ROP 了,不如直接呼叫 `VirtualProtect` 更直接,沒必要繞一圈先關 DEP 再跳 shellcode。

OSED 現場遇到的目標幾乎都是 Win10 x86 行程,`SetProcessDEPPolicy` 基本只是背景知識,用來理解「DEP 曾經可以這樣繞過」的歷史脈絡,不是本課的主力路線。

## 四條路徑完整取捨表

| 路徑 | 前提 | 需要的額外工序 | 難度 | 適用場景 |
|---|---|---|---|---|
| `VirtualProtect`（Ch 29） | 知道/能算出 shellcode 現在的精確位址 | 無（單次呼叫即完成） | 中 | shellcode 已在 stack/heap 上,位址可推算 |
| `VirtualAlloc` + `WriteProcessMemory` | 無（不需要事先知道任何位址） | 需要把 `VirtualAlloc` 回傳值（EAX）搬到下一個呼叫的參數位置 | 中高 | shellcode 位址難以推算,或想要更穩定、不依賴 stack 相對位置的做法 |
| `WriteProcessMemory` 單獨使用（寫進既有可執行頁） | 目標程式有異常寬鬆、可寫又可執行的頁(罕見) | 需要先找到這種頁 | 高（現代系統幾乎不存在此前提） | 幾乎不用,了解其存在即可 |
| `SetProcessDEPPolicy` | 舊系統（XP SP3/Server 2008 等) | 同樣需要 ROP 擺參數呼叫它,之後還要再跳回 shellcode | 低（舊系統)/不可行（新系統) | 歷史背景知識,OSED 現場基本不用 |

## pushad 一次擺多個參數的技巧

Ch 28 提過 `pushad` 的概念,這裡用 `VirtualAlloc` 的四參數具體示範它怎麼省 gadget：

```
目標：呼叫 VirtualAlloc(NULL, 0x1000, 0x3000, 0x40)

stdcall 由右至左 push,所以 stack 上從 ESP 往上讀的順序應該是：
  [NULL][0x1000][0x3000][0x40]

pushad 的固定推送順序（從 ESP 往上,也就是先 push 的在高位址)：
  EAX, ECX, EDX, EBX, (原始)ESP, EBP, ESI, EDI

如果事先把：
  EAX = 0x40      (flProtect,寫在最後一個 push 的格子,也就是最靠近 ESP 的位置—
                    不對,要核對清楚：pushad 先 push EAX,所以 EAX 最後落在
                    "最高位址"那端。要讓四個參數對應正確,
                    必須弄清楚 pushad 完整八個暫存器的推送順序和
                    stdcall 需要的四個格子怎麼對齊，通常只用得到其中四個，
                    中間夾雜的 ECX/EDX/EBX/ESP/EBP/ESI/EDI 要嘛恰好是
                    下一個呼叫不關心的 padding，要嘛要搭配額外 gadget 跳過)
  ...
```

> 上面特意把「對齊容易搞錯」的思考過程寫出來,是因為這是初學者用 `pushad` 時最常卡住的地方：`pushad` 一次推 8 個暫存器（32 bytes),但一個函式通常只要 4 個參數（16 bytes),多出來的 4 個暫存器值會變成**多餘的 stack 資料**,需要讓後續的「返回位址」「函式位址」剛好對齊到正確位置,中間用額外的 `ret` 或調整型 gadget把 ESP 推過這些多餘的格子。mona 自動生成的 chain 之所以常用 `pushad`,是因为它會先搜尋出「暫存器順序恰好對應目標函式參數順序」的理想情況(例如剛好要四個参数,而 EAX/ECX/EDX/EBX 這前四個被 pushad 推送的暫存器順序和函式期望的參數順序一致),這種情況下不需要額外補位 gadget，直接把函式位址接在 pushad 後面即可。如果順序對不上,就要退回逐一 `pop reg;ret` 的手排法（Ch 29 的做法)。

**實務建議**：`pushad` 是「賭一把,賭暫存器順序剛好對齊」的技巧,能省很多 gadget,但前提脆弱。初學階段,逐一 `pop reg; ret`（Ch 29 示範的手排法）雖然多打幾個 gadget,但每一步你都清楚知道發生什麼,除錯起來容易得多。等熟練到能一眼判斷「這次 pushad 的順序剛好對齊」,再用它省時間。

## 核心：怎麼在 WinDbg 裡除錯一條 ROP chain

這是本章真正的重點——前面三條路徑都講完了,真正決定你能不能把理論變成能跑的 exploit,是這套除錯方法。

### 第一步：在第一個 gadget 下斷點

```
bp <第一個 gadget 的位址>
g
```

發送 payload 後,WinDbg 應該在這裡停下。如果**沒停下**,代表控制流根本沒有到達 ROP chain 起點——問題出在更早的階段（EIP 蓋得不對、offset 算錯、或 overflow 本身就沒觸發),回去檢查 Ch 7-8 的 offset/EIP 控制,不用往下看 ROP 的問題。

### 第二步：單步,每個 ret 都核對

```
p         ; 單步(step over,call 不進去)
r         ; 看目前所有暫存器
dd esp L8 ; 看 ESP 開始的 8 個 dword(通常夠看到接下來兩三個 gadget 位址)
```

每走完一個 gadget（通常是 2-3 次 `p`,視 gadget 指令數而定),問自己三個問題：

1. **EIP 現在落在哪？** 是不是你規劃裡的下一個 gadget位址？如果不是,問題就出在剛剛那個 `ret` 彈出的值不對——往回看上一個 gadget 到底動了什麼。
2. **關鍵暫存器的值對不對？** 例如規劃裡這一步應該讓 `eax` 變成 shellcode 位址,實際 `r eax` 看到的是不是合理的 stack 位址範圍（如果看到的是明顯不合理的小數字或 `0x00000000`,代表前面某步算錯)。
3. **ESP 現在指向哪裡？** 這決定了下一個 `ret` 會彈出什麼。如果 ESP 的位置和你規劃時以為的不一樣（常見原因：中間某個 gadget 比你以為的多吃了一個 stack dword,或少吃了一個),之後所有 gadget 都會系統性地錯位。

### 第三步：在 VirtualProtect/VirtualAlloc 入口核對完整參數

```
bp <VirtualProtect_addr>
g
dd esp L5   ; ESP, 以及正上方四個 dword(返回位址 + 三個參數,因為VirtualProtect
            ; 有四個參數,這裡示意用 L5 看返回位址加四個參數共五格)
```

確認這五格依序是：返回位址、`lpAddress`、`dwSize`、`flNewProtect`、`lpflOldProtect`,數值都合理（尤其 `lpAddress` 應該落在你知道是 stack 的位址範圍)。

### 第四步：函式呼叫完,核對效果

```
p        ; 走完整個 VirtualProtect/VirtualAlloc(視函式大小,可能要多次p或用 g 搭配
          ; 在函式 ret 處下臨時斷點)
r eax     ; VirtualAlloc 看這裡拿新位址;VirtualProtect 看這裡的 1/0(成功/失敗)
!vprot <lpAddress>   ; 確認目標頁的保護旗標是否真的變成 PAGE_EXECUTE_READWRITE
```

如果 `!vprot` 顯示的保護旗標沒有變,代表函式呼叫失敗（`eax=0`),回去檢查參數——最常見原因是 `lpflOldProtect` 指向了不可寫位址,或 `lpAddress` 根本不是一個已分配（committed）的頁。

### 第五步：確認最終跳轉落點

```
p    ; 走完函式的 ret,EIP 應該落在你規劃的返回位址(shellcode 起點)
u eip L5   ; unassemble 看接下來幾條指令,確認是你放的 shellcode(或 \xcc 佔位)
```

### 常見失敗徵兆對照表

| 徵兆 | 最可能的原因 |
|---|---|
| 完全沒停在第一個 gadget | EIP 控制階段出錯（offset/壞字元),不是 ROP 的問題 |
| 停在第一個 gadget,但走第二步就跳到完全無關的位址 | 第一個 gadget 其實比預期多吃了一個 stack dword(例如你以為是 `pop eax;ret`,實際是 `pop eax;pop ebx;ret`) |
| 走到 VirtualProtect 入口,但 `dd esp` 看到的參數是垃圾值 | 前面某個該動態填值的 slot（如 Ch 29 的 ⑧⑨格）忘了補,還留著佔位常數 |
| VirtualProtect 呼叫後 `eax=0`，`!vprot` 保護沒變 | 檢查 `lpflOldProtect` 是否可寫、`lpAddress` 是否是已分配頁 |
| VirtualProtect 成功（`eax=1`),但跳過去之後 crash | 返回位址（shellcode 起點)算錯,實際落在 shellcode 前後幾個 bytes,不是真正的起點 |
| 一切都對,但 shellcode 本身執行到一半 crash | 這已經不是 ROP 的問題,回去檢查 shellcode 本身（壞字元、PEB walk 邏輯,Ch 23-26） |

> **未實測,理論預期**：本節所有 WinDbg 指令序列和輸出格式,依官方文件與 mona 擴充指令行為推論,本機未對一個真實崩潰的 x86 process 實際跑過整套流程驗證。但這套「下斷點 → 單步 → 核對暫存器/ESP → 核對函式參數 → 核對效果 → 核對最終跳轉」的方法論本身,是通用的 ROP 除錯邏輯,不依賴特定目標,可以直接套用到你自己的練習靶上驗證。

## 踩雷集錦

1. **以為 `VirtualAlloc` 的回傳位址可以「猜」是固定的**：`VirtualAlloc(NULL, ...)` 讓系統自己選位址,這個位址**執行時才確定**,沒有辦法在寫 exploit 時寫死。一定要用 gadget 動態把 `EAX`（回傳值)搬到下一步需要的地方。

2. **`WriteProcessMemory` 的 `lpBaseAddress`/`lpBuffer` 寫反**：複製方向搞反,結果是把目的地（通常是剛分配的空記憶體）的垃圾值複製回 shellcode 原本的位置,shellcode 區塊反而被污染。每次用這支函式前默念一次「目的地在前,來源在後」。

3. **在現代系統上還想著「把 shellcode 寫進 .text」**：`.text` 預設不可寫,這條路徑在 Win10/11 上幾乎必然失敗。除非明確確認過目標段有 `PAGE_EXECUTE_READWRITE`（罕見),不要浪費時間規劃這條路。

4. **`pushad` 用錯順序,沒有驗證對齊是否真的成立**：賭 `pushad` 省 gadget,卻沒有先確認八個暫存器的推送順序和目標函式參數順序是否真的對齊,結果呼叫時某個暫存器的值落在錯誤的參數位置,函式用了垃圾值當參數。

5. **除錯時一次改好幾個地方再跑,搞不清楚哪個修正真正有效**：chain 歪掉時,建議一次只改一個 gadget/參數,重新跑一次單步驗證再改下一個。一次改多處,等到結果對了也不知道是哪個改動起作用,日後遇到類似問題又要重新摸索。

## 進階：再往深一層

- **`NtAllocateVirtualMemory` / `NtProtectVirtualMemory`：繞過 Win32 層直接呼叫 native API**：`VirtualAlloc`/`VirtualProtect` 在 `kernel32.dll`/`kernelbase.dll` 內部最終都是包著 `ntdll.dll` 的 native 版本（`Nt*`/`Zw*` 開頭)。某些情況下（例如 `kernel32.dll` 的某個版本掛了額外的參數檢查、或想要更穩定跨版本的 gadget 來源),ROP chain 可以直接呼叫 `ntdll.dll` 裡的 native 版本,跳過 Win32 層的額外邏輯。這套路徑的參數語意和 Win32 版本略有差異,屬於進階技巧,先知道它存在即可,`windows_pwn` Ch 7（syscall 機制）有更完整的背景。
- **ROP chain 失敗時用 Time Travel Debugging（TTD）回放**：Ch 0 提過 WinDbgX 的 TTD 功能,能把整段執行錄下來事後回放——對一條複雜 ROP chain 來說,比起每次重新觸發 crash 再單步,錄一次後可以**反覆回放、往前往後跳**檢查任何一步的狀態,除錯效率高很多。Ch 41（加深一圈）會回來講 TTD 在 ROP 除錯上的完整用法。

## 本章重點整理

- `VirtualAlloc` 路徑**不需要事先知道任何位址**（系統自己選),但多一道「把回傳值(EAX)搬到下一個呼叫的參數位置」的工序,通常搭配 `WriteProcessMemory` 把 shellcode 複製進新配置的 RWX 記憶體。
- `WriteProcessMemory` 單獨寫進既有可執行頁,在現代系統上因為 `.text` 預設不可寫而幾乎不可行,它的實際用途是配合 `VirtualAlloc` 複製 shellcode。
- `SetProcessDEPPolicy` 只在舊系統有效,是歷史背景知識,OSED 現場不實用。
- `pushad` 能省很多 gadget,但前提是暫存器推送順序恰好對齊目標函式的參數順序,對不上就要退回逐一 `pop reg;ret` 的手排法。
- 除錯 ROP chain 的系統化方法：**第一個 gadget 下斷 → 逐 ret 核對 EIP/暫存器/ESP → 函式入口核對完整參數 → 函式返回後核對效果(`!vprot`) → 核對最終跳轉落點**，每一步都有對應的常見失敗徵兆可以對照排查。

## 自我檢核

- [ ] 能說出 `VirtualAlloc` 路徑比 `VirtualProtect` 路徑多出來的那道工序是什麼、為什麼需要它
- [ ] 能解釋為什麼「把 shellcode 寫進 .text」在現代 Windows 上幾乎不可行
- [ ] 能說出 `pushad` 省 gadget 的原理,以及它什麼時候會失效
- [ ] 給一條歪掉的 ROP chain,能按順序說出該檢查哪五個步驟來定位問題
- [ ] 看到 `VirtualProtect` 呼叫後 `eax=0`,能列出至少兩個最可能的原因並說出怎麼驗證

## 延伸閱讀

- **[VirtualAlloc function — Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/nf-memoryapi-virtualalloc)**
  - 讀哪裡：參數表,特別是 `flAllocationType`（`MEM_COMMIT`/`MEM_RESERVE`)和 `flProtect` 的常數定義。
  - 學什麼：本章 `VirtualAlloc` 段落的參數語意權威出處。
  - 前提：無。

- **[WriteProcessMemory function — Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/nf-memoryapi-writeprocessmemory)**
  - 讀哪裡：參數表,特別是 `lpBaseAddress` 與 `lpBuffer` 的方向說明。
  - 學什麼：確認複製方向,避免踩「寫反」的雷。
  - 前提：無。

- **[corelan/mona（GitHub）](https://github.com/corelan/mona)**
  - 讀哪裡：README 裡關於 `!mona rop` 針對 `VirtualAlloc` 目標生成 chain 的範例段落（若有列出）。
  - 學什麼：mona 處理「回傳值需要搬到下一步」這類情境的實際做法。
  - 前提：Ch 29。

- **[Install the Windows debugger / WinDbg command reference — Microsoft Learn](https://learn.microsoft.com/en-us/windows-hardware/drivers/debugger/)**
  - 讀哪裡：`bp`、`g`、`p`、`t`、`dd`、`u` 等指令的精確語法與差異（特別是 `p` vs `t` 在 `call` 上的行為差異)。
  - 學什麼：本章除錯流程用到的每個指令的細節,确保單步時不會不小心走進系統函式內部出不來。
  - 前提：Ch 4–5。

四條 DEP bypass 路徑和除錯方法都備齊了,下一個練習把 Part 2（stack overflow）的控 EIP 功夫和 Part 7（DEP bypass）的手工 ROP 接起來,做一次完整端到端的 exploit。

→ [練習 E：stack overflow + 手工 ROP DEP bypass](./practice-e-stack-overflow-dep-bypass.md)
