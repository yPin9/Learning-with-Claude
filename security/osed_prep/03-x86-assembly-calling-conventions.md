# Ch 3 — x86 組語與呼叫慣例（32-bit，vs Linux/x64）

> **目標**：把 OSED 全程要用的 x86 32-bit 地基釘牢——通用暫存器的用途、`EIP`/`ESP`/`EBP` 怎麼配合 stack 運作、函式呼叫（`call`/`ret`）對 stack 做了什麼、prologue/epilogue 的固定套路，以及四種呼叫慣例（cdecl/stdcall/fastcall/thiscall）的差異與它們為什麼直接影響你怎麼排 ROP chain。這是後面 stack overflow、SEH、ROP、shellcode 全部建立在上面的語言,值得啃到真的懂,不是掃過去。

> **環境**：本章的反組譯範例全部標注「未實測，理論預期」，概念以 Intel/AMD 架構手冊與一般編譯器（MSVC/GCC x86）行為為準;若要親手驗證，用 **WinDbg**（或任何 x86 偵錯器）載入一支簡單的 32-bit `.exe`，在 `call` 指令處下斷點，單步觀察 `ESP`/`EBP`/stack 內容的變化。

## 為什麼不能跳過這章

Linux pwn 背景的人常見的心態是「組語我會,這章跳過」。這是個危險的假設——OSED 要你**精算**的不是「組語讀得懂」這個層次,而是：

- ROP chain 裡每個 gadget 執行完,`ESP` 到底移動了幾個 byte,下一個要控制的值落在哪個位置。
- 呼叫一個 stdcall 函式時,它用 `ret N` 清棧,這個 `N` 不處理好,你 chain 後面接的東西全部錯位。
- 函式 prologue 建立的 stack frame 結構,決定了 stack overflow 覆寫到第幾個 byte 會先踩到 saved `EBP`、第幾個 byte 才踩到 return address。

這些都是「差一個 byte 整條 exploit 就不動」的精度要求。這章要把你的組語知識從「讀得懂」拉到「能逆向計算 stack 位移」。

## 建立直覺：把 CPU 想成一張有記憶的工作台

在深入暫存器清單之前,先建立一個類比。把 CPU 想成一位工匠在工作台上做事：

```
工作台（CPU）的常駐工具（暫存器）：
┌──────────────────────────────────────────┐
│  EAX — 「手上正在做的東西」（累加器/回傳值放這）│
│  EBX — 「旁邊備用的一塊材料」                  │
│  ECX — 「計數器」（loop 常用,也是 fastcall 第一引數）│
│  EDX — 「另一塊工作中材料」（常搭配 EAX 做除法/乘法）│
│  ESI — 「來源指標」（String/Source Index）     │
│  EDI — 「目的指標」（Destination Index）        │
│  EBP — 「這次工作的固定參考點」（stack frame 基準）│
│  ESP — 「堆料堆到哪裡了」（永遠指向 stack 最頂端）│
│  EIP — 「下一步要做什麼」（不能直接讀寫,只能跳轉改它）│
└──────────────────────────────────────────┘

工作台旁有一疊越堆越高、但編號越堆越小的箱子（stack）：
    地址高 ┌─────────┐ ← stack 的「底部」（先放的東西在這附近）
           │  ...    │
           │  舊資料  │
           ├─────────┤ ← ESP 指這裡（最新放上去的東西）
           │  新資料  │
    地址低 └─────────┘ ← stack 往這個方向長（push 讓 ESP 變小）
```

這個類比要記住兩件事,整門課都靠它：

1. **Stack 由高位址往低位址長**。`push` 讓 `ESP` 減少（往下、往低位址移動）,`pop` 讓 `ESP` 增加。這和「直覺上往上疊箱子」相反——方向搞反,後面算 offset 全部會錯。
2. **`EIP` 不是一個你能直接 `mov` 的暫存器**。你不能寫 `mov eip, 0x41414141`。能改變 `EIP` 的只有 `jmp`/`call`/`ret`/例外處理跳轉這類控制流指令——而 **exploit 要控制 `EIP`,本質上就是找一個方法讓 `ret`（或等效的控制流指令）從一個你能寫入的記憶體位置,讀出你塞進去的值,當作下一步要執行的位址**。這就是整個 stack overflow 攻擊的根。

## 通用暫存器與旗標：各自的角色

| 暫存器 | 常見用途 | exploit dev 關注點 |
|---|---|---|
| `EAX` | 累加器,函式**回傳值**放這裡(cdecl/stdcall 慣例) | 看函式回傳什麼時第一個看它 |
| `EBX` | 通用,常被當「備用」暫存器,在 PIC/位置獨立碼裡有時存 base | ROP gadget 裡常見的 `pop ebx; ret` 之一 |
| `ECX` | 迴圈計數器、字串操作的計數;**fastcall 第一個整數引數**、**thiscall 的 `this` 指標** | 看到 `mov ecx, ...` 接著呼叫一個函式,很可能是 C++ 的 method call |
| `EDX` | 常搭配 `EAX` 做乘除法(64-bit 結果的高半部放這);**fastcall 第二引數** | — |
| `ESI` | **S**ource **I**ndex,字串/記憶體搬移指令(`movs`系列)的來源指標 | 常在 `rep movsb` 這類批量複製指令旁出現 |
| `EDI` | **D**estination **I**ndex,搬移指令的目的指標 | 同上,搭配 `ESI` 做 memcpy 風格操作 |
| `EBP` | **Base Pointer**,函式進入後固定指向這次呼叫的 stack frame 基準,用來存取區域變數與引數的相對位移 | stack overflow 分析時,`EBP` 常是第一個被你的溢位資料覆寫的東西（在 return address 之前） |
| `ESP` | **Stack Pointer**,永遠指向 stack 當前最頂端（最低位址那端） | ROP chain 的「游標」,每個 gadget/函式呼叫後都要精算它移動了多少 |
| `EIP` | **Instruction Pointer**,下一條要執行的指令位址 | exploit 的最終控制目標 |

另外有一個常被忽略但 OSED 偶爾用得到的暫存器組：**EFLAGS**。裡面的 Zero Flag（`ZF`）、Carry Flag（`CF`）、Sign Flag（`SF`）等單位元旗標控制條件跳轉（`jz`/`jnz`/`jc` 等）。逆向時看到一串 `cmp` 接 `jz`,就是在讀某個旗標位元做分支判斷——判斷「這裡是不是長度檢查」時,`EFLAGS` 的變化是你推斷邏輯的關鍵線索。

## Stack 機制：`push`/`pop`/`call`/`ret` 到底做了什麼

這是整章最關鍵的一節。先看最基本的兩條指令：

```
push eax
  等價於:
    esp = esp - 4        ; stack 向低位址長 4 bytes（32-bit = 4 bytes）
    [esp] = eax          ; 把 eax 的值寫到新的 esp 位置

pop eax
  等價於:
    eax = [esp]          ; 從目前 esp 位置讀值進 eax
    esp = esp + 4        ; stack 收回 4 bytes
```

`call` 和 `ret` 是 `push`/`pop` 的組合應用,專門處理函式呼叫：

```
call some_function
  等價於:
    push <call 指令後面那一條指令的位址>   ; 這就是 "return address"
    eip = some_function 的位址             ; 跳過去執行

ret
  等價於:
    eip = [esp]          ; 從 stack 頂端讀一個值,當成下一步要執行的位址
    esp = esp + 4         ; stack 收回這 4 bytes
```

把這兩段放在一起看,核心洞見就出來了：

> **`ret` 做的事情,本質上就是「相信 stack 頂端那 4 bytes 一定是合法的程式碼位址,然後跳過去執行」。如果你能透過某個漏洞,在 `ret` 執行之前把 stack 上那 4 bytes 改成你想要的值——你就控制了 `EIP`。** 這就是 stack-based buffer overflow 的全部原理,Ch 7 會用真實情境重現一次,但這個原理現在必須刻在腦子裡。

## 函式呼叫的完整 stack 變化：一個可讀範例

來看一段簡化但典型的 C 函式,用它走一次完整的 `call` → prologue → 函式體 → epilogue → `ret` 流程。

```c
void vulnerable_function(int a, int b) {
    int local_var = a + b;
    // ... 函式體
}

int caller() {
    vulnerable_function(1, 2);
    return 0;
}
```

典型 MSVC/GCC x86（cdecl,未開最佳化）反組譯大致長這樣（**未實測，理論預期**——實際輸出依編譯器/最佳化等級會有差異,請在你自己環境用相同原始碼編譯驗證）：

```asm
; caller() 呼叫 vulnerable_function(1, 2)
push    2                  ; 引數從右到左壓棧：先推 b=2
push    1                  ; 再推 a=1
call    vulnerable_function ; push 回傳位址,跳進函式

; ---- vulnerable_function 的 prologue ----
push    ebp                ; 保存呼叫者的 ebp
mov     ebp, esp           ; 建立新的 stack frame 基準
sub     esp, 0x10          ; 預留區域變數空間（含 local_var 與對齊）

; ---- 函式體 ----
mov     eax, [ebp+8]       ; 讀引數 a（相對 ebp 的正向位移）
add     eax, [ebp+0xc]     ; 加上引數 b
mov     [ebp-4], eax       ; 存進 local_var（相對 ebp 的負向位移）

; ---- epilogue ----
mov     esp, ebp           ; 收回區域變數空間（或用 leave 一次做完）
pop     ebp                ; 還原呼叫者的 ebp
ret                        ; 跳回 call 之後的位址

; ---- 回到 caller，cdecl 由呼叫者清理引數 ----
add     esp, 8             ; 把 push 1 / push 2 留下的 8 bytes 收回
```

對應的 stack 畫面,用 ASCII 圖拆成「剛進入函式」和「`sub esp, 0x10` 之後」兩個時間點：

```
剛執行完 `call vulnerable_function`,prologue 還沒跑：

  高位址
  ┌─────────────────────┐
  │   b = 2              │  ← caller 推進去的第二個引數（右到左,先推最右邊）
  ├─────────────────────┤
  │   a = 1              │  ← caller 推進去的第一個引數
  ├─────────────────────┤
  │  return address       │  ← call 自動推入,指回 caller 的下一條指令
  ├─────────────────────┤ ← ESP 指這裡（函式剛進入,EBP 還是 caller 的舊值）
  │   ...（caller 的 frame）│
  低位址

執行完 prologue（push ebp; mov ebp,esp; sub esp,0x10）之後：

  高位址
  ┌─────────────────────┐
  │   b = 2               │  ← EBP+0xc 能存取到
  ├─────────────────────┤
  │   a = 1               │  ← EBP+8  能存取到
  ├─────────────────────┤
  │  return address        │  ← EBP+4
  ├─────────────────────┤ ← EBP 指這裡（新 frame 的基準點）
  │  saved EBP（caller 的） │  ← EBP+0,也是 EBP-0 這一格
  ├─────────────────────┤
  │  local_var 等區域變數空間│  ← EBP-4 開始往下,sub esp,0x10 預留的空間
  │  （0x10 bytes）         │
  ├─────────────────────┤ ← ESP 指這裡（函式體執行期間的 stack 頂端）
  低位址
```

這張圖要記住的結構性事實（Ch 7 的 stack overflow 分析完全靠它）：

> **從函式體內某個 buffer 往高位址方向覆寫,依序會先踩到 saved EBP,再踩到 return address,再踩到引數。** 這就是為什麼經典 stack overflow 分析時,offset 定位常常會先看到 `EBP` 被某個已知 pattern 蓋掉,再往後幾個 byte 才是 `EIP`（return address）被蓋掉——兩者之間的距離,正是 saved `EBP` 這 4 bytes。

## 呼叫慣例對照表：cdecl / stdcall / fastcall / thiscall

呼叫慣例（calling convention）規定「引數怎麼傳、誰負責清理 stack、回傳值放哪」。32-bit Windows 世界同時存在好幾種,而且**混用在同一支程式裡是常態**——Win32 API 幾乎都是 stdcall,但程式自己寫的輔助函式常是 cdecl 或 C++ 的 thiscall。你必須在逆向時每個函式都重新判斷它用哪種,不能假設。

| 呼叫慣例 | 引數傳遞 | 清棧責任 | 回傳值 | 常見場景 |
|---|---|---|---|---|
| **cdecl** | 全部透過 stack,右到左壓棧 | **caller** 清棧（`add esp, N`） | `EAX` | C 標準函式庫、一般 C 程式自己寫的函式 |
| **stdcall** | 全部透過 stack,右到左壓棧 | **callee** 清棧（`ret N`,N = 引數總 bytes 數） | `EAX` | **幾乎所有 Win32 API**（`WinExec`、`VirtualProtect`、`CreateProcessA`…） |
| **fastcall** | 前兩個整數引數用 `ECX`、`EDX`,其餘才上 stack（右到左） | **callee** 清棧 | `EAX` | 效能敏感的內部函式,較少見於公開 API |
| **thiscall** | `this` 指標放 `ECX`,其餘引數上 stack | **callee** 清棧（MSVC 預設行為） | `EAX` | **C++ 的 non-static method call** |

### 為什麼這張表對 exploit dev 是生死攸關的細節

1. **stdcall 的 `ret N` 會「連本帶利」收棧**：假設你 ROP chain 裡呼叫了一個 stdcall 函式,它接收 2 個 32-bit 引數,函式執行完會用 `ret 8`——這代表它**不只跳走,還順手把 `ESP` 再往上移動 8 bytes**,相當於幫你多消耗了兩個 stack slot。如果你排 chain 時以為它只做普通的 `ret`（只移動 4 bytes）,後面接的 gadget 位置會整個偏移,chain 直接歪掉。Ch 29 的 `VirtualProtect` ROP chain 會具體示範這個計算。

2. **cdecl 的清棧責任在 caller**：如果你 ROP chain 裡要呼叫一個 cdecl 函式,呼叫完之後**你自己要負責把引數的 stack 空間收回**（或乾脆不管它,讓 chain 繼續往後跑,只要你知道下一個 gadget 的位置因此往後偏移了多少）。這和 stdcall 剛好相反責任方。

3. **fastcall/thiscall 的暫存器引數,意味著「引數不在 stack 上」**：如果你逆向時看到一個函式呼叫前有 `mov ecx, something` 卻沒對應的 `push`,先別急著以為少看到 push——很可能是 fastcall 或 thiscall,引數本來就該在暫存器裡,不在 stack 上。誤判會讓你以為函式少收一個引數,逆向邏輯整個推錯。

4. **ROP chain 裡「自己擺引數」的本質,就是在模擬 caller 原本會做的 `push`**：寫 ROP chain 呼叫 `VirtualProtect` 這類 stdcall API 時,你要用 `pop`/`ret` 系列 gadget 把引數依序擺到 stack 上對的位置,順序要符合該函式的引數定義——這就是為什麼 Ch 3 一定要先搞懂「引數怎麼從右到左壓棧」這個基本規則,才能正確手排 chain。

## 底層機制：把 stdcall 的清棧行為畫成流程圖

用 ASCII 圖對比 cdecl 和 stdcall 執行完之後 `ESP` 的差異,假設呼叫一個帶 2 個 32-bit 引數的函式：

```
[cdecl 版本]
                         push arg2
                         push arg1
                         call func      ← push return address, esp -= 4(再加上面兩個push共 -12)
                         (func 內部: 只 ret, 不多收 stack)
                         ret            ← esp += 4（只收回 return address）
caller 端接手:
                         add esp, 8     ← caller 自己把 arg1/arg2 的空間收回

[stdcall 版本]
                         push arg2
                         push arg1
                         call func      ← push return address
                         (func 內部: 執行完後用 ret 8，不是普通 ret)
                         ret 8          ← esp += 4（收回 return address）再 += 8（收回 2 個引數）
caller 端:                （什麼都不用做，callee 已經連引數空間一起收了）
```

對 ROP chain 設計者來說,這代表：**呼叫一個 stdcall 函式之後,你排在 chain 裡「下一個該執行的 gadget 位址」,必須擺在「引數們之後」那個位置,而不是緊接在 return address 後面**——因為 `ret N` 執行時會把引數一起吃掉,`ESP` 落地的位置已經跳過了引數區。這也是為什麼手排 ROP chain 時,画一張和上面類似的 stack 圖永遠是第一步,不要跳過這步直接憑感覺排 gadget。

## 簡短對照 x64：留一個鉤子給 Ch 40

OSED 全程 32-bit,但知道 x64 差多少有助於你理解「為什麼選 32-bit 教」。這裡只給最關鍵的三點差異,完整對照留給 Ch 40（加深一圈 I）：

| 項目 | x86（本課全程） | x64（Ch 40 細講） |
|---|---|---|
| 整數引數傳遞 | 全部走 stack（cdecl/stdcall 都一樣） | 前 4 個整數引數走 `RCX`/`RDX`/`R8`/`R9`,其餘才上 stack |
| 「shadow space」 | 不存在這個概念 | 呼叫 Windows x64 API 前,caller 必須在 stack 上預留 32 bytes 的 shadow space,即使引數都在暫存器裡 |
| 呼叫慣例數量 | 四種並存（cdecl/stdcall/fastcall/thiscall） | 統一成單一的 Microsoft x64 calling convention,不再需要分辨多種慣例 |

這代表 x64 的 ROP chain 設計思路和 x86 不同——引數大多要塞進暫存器而不是排在 stack 上,「找 `pop rcx; ret` 之類的 gadget 設暫存器」取代了 x86 時代「把引數依序壓在 stack 上」的直覺。這部分在你學完本課全程 x86 之後,是最自然的下一個深化方向,`windows_pwn` 系列和本課 Ch 40 都會接手。

## 對比與取捨：四種呼叫慣例一次看懂「誰負責什麼」

| 維度 | cdecl | stdcall | fastcall | thiscall |
|---|---|---|---|---|
| 清棧責任 | Caller | Callee | Callee | Callee |
| 引數位置 | 全在 stack | 全在 stack | 前兩個整數在 `ECX`/`EDX` | `this` 在 `ECX`,其餘在 stack |
| 函式簽名可變引數（如 `printf`） | **可以**（caller 清棧,callee 不需知道引數數量） | **不行**（callee 要用固定的 `ret N`） | 不行 | 不行 |
| 典型來源 | C 標準函式庫、一般使用者程式 | Win32 API | 少數效能關鍵內部函式 | C++ 編譯器產生的 method call |
| 逆向辨識線索 | `call` 之後接 `add esp, N` | `call` 之後**沒有**清棧指令（函式內部自己 `ret N`） | `call` 前有 `mov ecx,.../mov edx,...` 卻引數對不上 stack push 數量 | `call` 前有 `mov ecx, <物件指標>`,通常是 virtual call 或直接 method call |

> **重要觀察**：cdecl 可以支援可變引數（像 `printf`）正是因為清棧責任在 caller——caller 知道自己傳了幾個引數,清多少 stack 它自己算得出來;stdcall 要求 callee 用固定的 `ret N`,所以 callee 必須在編譯時就知道確切的引數 byte 數,**天生不支援可變引數**。這也解釋了為什麎 Windows 的 `wsprintfA` 這類可變引數函式是 cdecl,而大多數定引數的 API 是 stdcall。

## 踩雷集錦

1. **錯誤直覺：「`call` 之後 `ESP` 一定只變化 4 bytes（push 的 return address）」**
   正確：要看呼叫慣例。stdcall/fastcall/thiscall 的函式**內部**會用 `ret N` 多清棧,從 caller 的視角看,`call` 回來之後 `ESP` 比呼叫前少掉的量,是「push 的引數 + `ret N` 清掉的量」互相抵銷後的結果——如果 callee 用 `ret N` 自己清了引數,caller 就不需要再 `add esp` 一次。永遠先確認這個函式是哪種慣例,再推算 `ESP` 的淨變化。

2. **錯誤直覺：「`EBP` 永遠等於目前函式的 stack frame 基準,逆向時可以無條件信任它」**
   正確：這假設只在**沒有特別最佳化、且函式確實做了標準 `push ebp; mov ebp,esp` prologue** 時成立。有些最佳化等級（尤其 Release build 常見的 "omit frame pointer" 最佳化）會讓編譯器直接用 `ESP` 相對位移存取區域變數,完全不建立 `EBP` frame。逆向陌生 binary 時先確認 prologue 長什麼樣子,別預設一定有 `EBP` frame。

3. **錯誤直覺：「ROP chain 裡接了一個函式呼叫,下一個 gadget 位址就緊接著填在它後面」**
   正確：要先判斷這個函式的呼叫慣例與引數數量,算出 `ret N` 會把 `ESP` 往前推多少,下一個 gadget 位址**要擺在 `ret N` 執行完之後 `ESP` 真正落地的位置**,不是你直覺以為的「下一個 stack slot」。這是 Ch 29 手排 `VirtualProtect` chain 時最容易出錯的地方。

4. **錯誤直覺：「看到 `mov ecx, xxx` 後面接 `call`,一定是 fastcall」**
   正確：thiscall 也會在呼叫前把值放進 `ECX`（放的是 `this` 指標,不是第一個整數引數）。區分方法要看**上下文**：如果這個函式明顯是某個 class 的 method（例如前面有 vtable 的呼叫模式,或 `ECX` 的值來自一個明顯是物件指標的來源）,更可能是 thiscall;如果 `ECX`/`EDX` 放的是看起來像一般整數引數的值,才考慮 fastcall。兩者語法上長得像,要靠語義判斷。

5. **錯誤直覺：「stack 往高位址長,push 應該讓 ESP 變大」**
   正確（反過來）：x86/x64 的 stack **由高位址往低位址長**,`push` 讓 `ESP` **減少**。這個方向記反,後面算 offset、畫 stack 圖全部會反過來——建議一開始就把本章「工作台類比」的圖刻進腦子裡,每次畫圖都從「高位址在上、低位址在下、箱子往下堆」這個固定方向畫起。

## 進階：再往深一層

- **`leave` 指令**：很多 epilogue 不是寫 `mov esp,ebp` + `pop ebp` 兩條,而是用單一條 `leave` 指令做完全一樣的事（`mov esp,ebp; pop ebp` 的等價縮寫）。逆向時看到 `leave; ret` 這個固定組合,直接認出它是標準 epilogue 收尾,不用逐條推敲。
- **Frame Pointer Omission（FPO）與逆向的摩擦**：Release build 常省略 `EBP` frame 改用純 `ESP` 相對定址,這讓逆向定位區域變數變困難（`ESP` 在函式體內會隨著 `push`/`pop` 不斷浮動,不像 `EBP` 固定不變）。IDA 通常能自動分析出正確的堆疊版面(stack frame),但遇到混淆過的或 FPO 的程式碼,手動核對 `ESP` 在每個指令點的實際值會變成必要功夫——Part 4 逆向章會碰到這類情境。
- **呼叫慣例混用與 name mangling**：C++ 編譯器會把呼叫慣例資訊編進 mangled 符號名（MSVC 的 decoration,例如 `__stdcall` 相關符號常帶 `_` 前綴、`@N` 後綴標示引數 byte 數）。如果你的目標程式有符號資訊(symbol),IDA 顯示的 mangled name 本身就會告訴你呼叫慣例與引數大小——這是比純讀反組譯更快的判斷捷徑,Ch 14 會示範怎麼讀。

## 本章重點整理

- Stack **由高位址往低位址長**,`push`/`call` 讓 `ESP` 減少,`pop`/`ret` 讓 `ESP` 增加。
- `ret` 的本質是「相信 stack 頂端是合法位址,跳過去」——這就是 stack overflow 控制 `EIP` 的根本原理。
- 標準函式 prologue（`push ebp; mov ebp,esp; sub esp,N`）建立的 frame 裡,從低位址往高位址依序是：區域變數 → saved `EBP` → return address → 引數。Overflow 分析時這個順序決定你會先踩到什麼。
- 四種呼叫慣例的核心差異是**清棧責任**（caller vs callee）與**引數位置**（stack vs 暫存器）：cdecl（caller 清棧）、stdcall（callee 用 `ret N` 清棧,Win32 API 多用這個）、fastcall（`ECX`/`EDX` 傳前兩引數）、thiscall（`ECX` 放 `this`）。
- stdcall 的 `ret N` 會在 ROP chain 裡多清掉 `N` bytes 的 stack,排 chain 時必須精算這個位移,否則後續 gadget 全部錯位。
- x64 把這一切簡化成單一慣例、引數優先走暫存器、外加 shadow space 的概念,Ch 40 再深入。

## 自我檢核

- [ ] 能不看圖,自己畫出 `push ebp; mov ebp,esp; sub esp,N` 執行完之後的 stack 佈局
- [ ] 能解釋為什麼 `ret` 是 stack overflow 控制 `EIP` 的根本機制
- [ ] 能說出 cdecl 和 stdcall 誰負責清棧、`ret N` 的 N 代表什麼
- [ ] 給一段呼叫 stdcall 函式的反組譯,能算出函式返回後 `ESP` 相對呼叫前的淨位移
- [ ] 能說出至少兩個逆向時判斷「這是不是 thiscall」的線索

## 延伸閱讀

- **[Microsoft Learn — x86 calling conventions overview](https://learn.microsoft.com/en-us/cpp/cpp/calling-conventions)**
  - **讀哪裡**：`__cdecl`、`__stdcall`、`__fastcall`、`__thiscall` 各節。
  - **學什麼**：每種呼叫慣例的官方定義與 MSVC 實作細節,本章表格的權威出處。
  - **前提**：無。

- **[Intel 64 and IA-32 Architectures Software Developer's Manual, Volume 1: Basic Architecture](https://www.intel.com/content/www/us/en/developer/articles/technical/intel-sdm.html)**
  - **讀哪裡**：通用暫存器（第 3 章）與 stack 操作（`CALL`/`RET`/`PUSH`/`POP` 指令參考,Volume 2）。
  - **學什麼**：`call`/`ret` 指令的官方語意定義,本章「等價於」的那些展開式都源自這裡。
  - **前提**：無,工具書式查閱。

- **[Corelan — Exploit Writing Tutorial part 1: Stack Based Overflows](https://www.corelan.be/index.php/2009/07/19/exploit-writing-tutorial-part-1-stack-based-overflows/)** — Peter Van Eeckhoutte
  - **讀哪裡**：講解 stack frame 與 return address 關係的段落。
  - **學什麼**：把本章的理論 stack 圖和真實 exploit 情境對照一次,為 Ch 7 鋪路。
  - **前提**：讀完本章再看,會更容易對上每個術語。

- **[Connor McGarr — Return Oriented Programming (ROP) 系列文章](https://connormcgarr.github.io/)** — Connor McGarr
  - **讀哪裡**：涉及 Windows API ROP chain 排列的文章。
  - **學什麼**：stdcall 的 `ret N` 在真實 ROP chain 裡怎麼被考慮進去,把本章的理論對照到 Ch 29 要做的事。
  - **前提**：先理解本章的 stdcall 清棧機制。

- **[FuzzySecurity — Windows Exploit Development Tutorial Series](https://www.fuzzysecurity.com/tutorials.html)** — b33f (Ruben Boonen)
  - **讀哪裡**：涉及組語基礎與 WinDbg 初探的早期篇章。
  - **學什麼**：另一個角度的 x86/stack 基礎講解,互相印證加深理解。
  - **前提**：無。

地基釘好了,下一章把你帶進 WinDbg 本體——怎麼下斷點、怎麼讀暫存器、怎麼查記憶體,把這章學的理論在真正的偵錯器裡親眼驗證一次。

→ [Ch 4 WinDbg 精通 I：命令體系、斷點、記憶體與符號](./04-windbg-mastery-1.md)
