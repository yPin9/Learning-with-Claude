# Ch 40 — 加深一圈 I：x64 差異（為何考 x86）

> **目標**：講清楚 OSED 為什麼選 32-bit（x86）當考試範圍，以及這個選擇在多大程度上還能套用到真實世界的 64-bit（x64）目標。具體拆解 x64 對 exploit dev 造成衝擊的四個面向——呼叫慣例、位址寬度與壞字元、SEH 架構、暫存器與 gadget 生態——學完能一句話答出「我的 x86 SEH overwrite 技法，搬到 x64 目標為什麼直接失效」。

> **環境**：本章是概念與對照性質的章節，沒有獨立的動手實驗——所有 x64 ABI 的反組譯事實已在本 repo `windows_pwn` Ch 40（mingw-w64 GCC 14.2，本機實跑）驗證過，本章直接引用並轉譯到 OSED 的語境；x64 SEH 的 `.pdata`/`RUNTIME_FUNCTION` 事實引用自 `windows_pwn` Ch 12（同樣本機實跑）。涉及真實 x64 CVE 或 WinDbg 操作畫面的段落標「未實測，理論預期」。

---

## 為什麼需要這個？

你讀完本課 Part 0–10，手上有一套完整、能打穿 vulnserver 和類似靶的 x86 方法論：SEH overwrite、egghunter、手寫 shellcode、ROP 繞 DEP、ASLR bypass、format string。這套方法論在**考場**是完整且夠用的——OSED 的三個 assignment 全部是 32-bit 目標。

但走出考場，你會立刻撞到一個事實：**真實世界的 Windows 軟體，絕大多數是 64-bit（x64）進程**。現代瀏覽器、Office、大部分商用伺服器軟體、作業系統本體元件，早就全面 x64 化；32-bit 的進程在真實紅隊/漏洞研究場景裡反而是少數（舊版印表機驅動、某些工控軟體、WoW64 相容層跑的舊程式）。

這就產生一個合理的疑問：**OSED 考 x86，到底是在練什麼？** 答案不是「x86 過時了，白學」，而是三件事同時成立：

1. **x86 的呼叫慣例乾淨、技法可視化**——`cdecl`/`stdcall` 全部參數上 stack，buffer overflow 蓋 return address 的因果關係一步到位，SEH chain 也住在 stack 上，一個 overflow 就能碰到。這讓「控制流劫持」這個核心概念用最少的雜訊教給你。
2. **x86 的技法在 x64 上部分失效、部分要大改**——不是「學完 x86 自動會 x64」，而是「學完 x86 你才有底座去理解 x64 為什麼要那樣設計、哪裡被堵死、哪裡還能走」。
3. **OSED 的考試範圍本身就是 x86**——這不是教學選擇的全部理由，也是現實的證照範圍限制，你要先承認這一點再往下學。

本章的任務，就是把「x86 學完之後，x64 差在哪」講清楚，讓你在離開考場走進真實目標時，知道自己手上的工具哪些還能用、哪些要整套換。

## 先建立直覺：不是「同一套技法換個位元數」

初學者最容易有的錯覺是：「x64 不就是 x86 的暫存器變胖、位址變長嗎？把 `EAX` 換成 `RAX`、offset 數字乘以 2，技法照搬就行。」

這個直覺是錯的，而且錯得系統性——x64 不是 x86 的「放大版」，是**微軟重新設計了一套呼叫慣例與例外處理架構**，順便讓位址寬度變成 8 bytes。這三件事（呼叫慣例、例外處理、位址寬度）各自獨立地讓你的 x86 技法失靈，原因都不一樣：

```
  x86 技法                      x64 對應情況                     為什麼失效/要改
  ──────────────────────────────────────────────────────────────────────────
  stack 上排參數給 API           前 4 個參數要放進特定暫存器       呼叫慣例整套換了
  （壞字元 overflow 就能排）     （RCX/RDX/R8/R9），overflow
                                 寫不到暫存器

  jmp esp 類跳板（4 bytes 位址） 位址是 8 bytes，高位常含 0x00     壞字元規則變了，
                                                                  跳板 gadget 更稀有

  SEH overwrite（蓋 stack 上    SEH handler 資訊搬進 PE 唯讀      overflow 碰不到
  的 Next/Handler 指標）         section（.pdata/.xdata）          handler 資訊了

  mona 找 pop eax; ret 這種     gadget 要按暫存器名稱分類找        同一招「找 gadget」
  通用 gadget（32-bit 暫存器    （pop rcx/rdx/r8/r9 稀缺度不同）    但難度曲線完全不同
  都能用來傳參）
```

四行裡沒有一行是「位元數變大一點」，每一行都是「規則換了」。接下來逐一拆開講。

## 核心機制一：呼叫慣例大改——暫存器傳參 + shadow space + 對齊

### x86：一切上 stack

x86 的 `cdecl`/`stdcall` 慣例下，呼叫一個函式前，所有參數從右到左依序 `push` 到 stack：

```asm
; x86 cdecl：呼叫 func(a, b, c)
push c
push b
push a
call func
```

這對 exploit 的意義：你控制了一段連續的 stack 記憶體（overflow），要「呼叫」一個 API（例如 ROP 呼叫 `VirtualProtect`），只要把參數依序排在 stack 上，讓一串 `ret` 把它們一個個彈出，排法直覺、gadget 需求單一（`pop eax; ret` 這種通用 gadget 到處都是，因為任何 32-bit 通用暫存器都能充當中繼站）。Ch 29 的 VirtualProtect ROP chain 就是建立在這個前提上。

### x64：前四個參數走暫存器，其餘才上 stack

Windows x64 calling convention（Microsoft x64，不是 Linux 的 System V AMD64）規定：**前四個整數/指標參數依序放進 `RCX`、`RDX`、`R8`、`R9`**，第五個以後才輪到 stack。

```
Windows x64 參數傳遞（整數/指標）：
  第 1 個 → RCX
  第 2 個 → RDX
  第 3 個 → R8
  第 4 個 → R9
  第 5 個起 → stack（call 之後視角 [RSP+0x20]、[RSP+0x28]...）
  返回值   → RAX
```

這對 ROP chain 的意義是致命的：你不能再用「隨便一個 pop 通用暫存器」的 gadget 排參數，你要**明確找到** `pop rcx; ret`、`pop rdx; ret`、`pop r8; ret`、`pop r9; ret` 這四種特定 gadget，而且它們的稀缺度不同（`pop r8`/`pop r9` 因為帶 REX.B prefix，encoding 是 `41 58 c3`/`41 59 c3`，在小型模組裡比 `pop rax; ret`（`58 c3`）少見得多）。詳細的 encoding 對照表、gadget 稀缺度分析、跨 DLL 湊 gadget 的策略，`windows_pwn` Ch 40 用本機實跑的 mingw 反組譯完整示範過，本章不重複，直接引用其結論。

### shadow space：呼叫者要留 0x20 bytes

x64 Windows ABI 還有一個 x86 完全沒有的機制：**呼叫者**在 `call` 指令前，必須在 stack 上預留 **32 bytes（0x20）的 shadow space**（也叫 home space），緊接在 call 壓入的返回位址之上。這塊空間讓被呼叫者可以把 `RCX/RDX/R8/R9` spill（倒出）到 stack，方便除錯器還原參數——不管被呼叫者實際用不用，呼叫者都必須留。

```
x64 call 前後的 stack（簡化）：

   高位址
   ┌──────────────────────────────┐
   │  arg6（若有）  [RSP+0x28]    │  ← 第 5、6 個參數在這裡（a.c. = after call）
   │  arg5（若有）  [RSP+0x20]    │
   ├──────────────────────────────┤  ← shadow space 上緣
   │  R9  spill slot [RSP+0x18]   │
   │  R8  spill slot [RSP+0x10]   │  32 bytes，呼叫者必須預留
   │  RDX spill slot [RSP+0x08]   │
   │  RCX spill slot [RSP+0x00]   │  ← shadow space 下緣
   ├──────────────────────────────┤
   │  返回位址（call 壓入）       │ ← RSP 在此（進入被呼叫者的瞬間）
   └──────────────────────────────┘
   低位址
```

在 ROP chain 裡，你是「假扮呼叫者」的那個角色——你沒有真正執行 `call`，是讓 `ret` 跳進 API。這意味著 shadow space **沒有人幫你留**，如果 chain 裡 API 位址前面直接接著下一個 gadget 位址（你以為的「shadow space」），API 的 prologue 一旦 spill 參數，就會把你 chain 剩下的部分蓋掉。解法是在跳進 API 之前插一個 `sub rsp, 0x28; ret` 類的 gadget，手動模擬呼叫者該做的事。這在 x86 完全不存在——x86 的 API prologue 不會往你 chain 上面的「呼叫者預留區」寫東西。

### 16-byte 對齊：call 前 RSP 必須對齊

x64 ABI 規定 `call` 指令執行**之前**，`RSP` 必須是 16-byte 對齊的（`RSP % 16 == 0`）。這不是風格建議，是會讓程式**直接崩潰**的硬性要求——Windows API 內部大量使用 `MOVAPS`/`MOVAPD`/`MOVDQA` 這類 aligned SSE 指令，存取非對齊位址會觸發 `#GP`（General Protection Fault），Windows 把它轉成你熟悉的 `EXCEPTION_ACCESS_VIOLATION`（`0xC0000005`）。

關鍵的是：**就算你呼叫的 API 完全沒有浮點參數**（例如 `VirtualProtect`，純整數參數），一樣要求對齊——因為 API 內部的最佳化程式碼可能用 SSE 指令備份暫存器，不管你傳的參數型別是什麼。

x86 沒有這個要求（4-byte 對齊就夠，寬容得多）。這是三道新門檻裡最容易被忽略、也最容易讓 x64 ROP chain 莫名其妙 crash 在一個看起來毫不相關的 SSE 指令上的一個。

> **未實測，理論預期**：若你在自己的 x64 Win10 環境建一個有 overflow 的小程式並嘗試手搭 ROP chain 呼叫 `VirtualProtect`，刻意讓進入點 RSP mod 16 == 8（故意不對齊），預期在 `kernelbase!VirtualProtect` 或其內部呼叫的某個函式的 `movaps` 指令上觸發 access violation，而不是在你的 chain 本身的任何一步。用 WinDbg 的 `k`（call stack）配合 `u`（反組譯當前指令）可以定位到具體是哪一行 SSE 指令。

### 動手算一次：假設性的 x64 overflow 對齊推演

這段是純粹的紙上推演（未對應任何本機實跑的真實 binary），目的是讓你熟悉「追蹤 RSP 對齊」這件事該怎麼一步步算,假設一個 x64 版本的 vulnserver 風格目標，`TRUN` 指令的 overflow 蓋掉 saved RBP 和 return address 後，你的 ROP chain 從 return address 的位置開始：

```
假設：overflow 覆寫後,return address 被你的第一個 gadget 位址取代,
      此時 RSP 指向 chain 的第一個 QWORD。假設此刻 RSP mod 16 == 8
      （典型情況：被覆寫的函式本身在 prologue 做過奇數次 push，
       讓偏移量停在 8 而非 0——這點必須在真實目標上用 WinDbg `r rsp` 配合
       `? rsp & 0xf` 實測確認，這裡只是示範算法）

  step 1：pop rcx; ret      → ret 讓 RSP += 8 → mod 16 變成 0
  step 2：pop rdx; ret      → RSP += 8        → mod 16 變成 8
  step 3：pop r8;  ret      → RSP += 8        → mod 16 變成 0
  step 4：pop r9;  ret      → RSP += 8        → mod 16 變成 8
  step 5：sub rsp, 0x28; ret → sub 後 RSP mod 16 仍是 8（0x28 不是 16 的倍數！）
                              → ret 再 += 8  → mod 16 變成 0
  step 6：跳進 API           → 進入時 RSP mod 16 == 0 ✔ 對齊正確
```

這裡故意選 `sub rsp, 0x28`（40 bytes，不是 16 的倍數）而非乾淨的 `0x20` 或 `0x30`，是因為真實環境裡你能找到的「湊巧留 shadow space」的 gadget，常常不會剛好是 16 的倍數——這正是 x64 ROP chain 讓人頭疼的地方：**每一條 chain 都要重新算一次，沒有放諸四海皆準的公式**，算錯的後果通常是在 API 內部某個 SSE 指令上無預警 crash，而不是在你自己的 gadget 上，debug 起來比 x86 的「EIP 變成某個奇怪值」直覺得多。

> **未實測，理論預期**：上面的 `RSP mod 16` 初始值（覆寫後是 8 還是 0）必須對著真實目標用 WinDbg 實測，不能憑空假設——不同的目標函式、不同的編譯器優化層級,prologue 的 push 數量都不一樣,進而影響覆寫發生那一刻的對齊狀態。

### 三道門檻疊加的總結

把這三件事放在一起看：x64 ROP 要同時滿足「湊到四個特定暫存器的 pop gadget（其中兩個稀有）」「手動模擬 shadow space」「維護 16-byte 對齊」，這是 x86 ROP 只需要考慮「找到夠多的通用 pop gadget、排對順序」單一維度的三倍工程量。這也是為什麼 OSED 選擇先在 x86 上把「ROP 為什麼能繞過 DEP」這個核心概念教清楚，再留給你在 `windows_pwn` 裡加深 x64 的工程細節。

## 核心機制二：位址 8 bytes、高位常含 0x00——壞字元與跳板更難用

x86 的指標是 4 bytes，x64 是 8 bytes。這件事聽起來只是「數字變大」，實際上在兩個地方直接影響你的 exploit：

### 壞字元規則變了

x86 時代你學的「壞字元分析」（Ch 8）是針對 4-byte 位址設計的直覺：一個位址裡哪個 byte 是 `0x00`、`0x0a`、`0x0d` 之類的壞字元，只要看 4 個 byte 就能判斷完。x64 的位址是 8 bytes，**壞字元檢查的範圍加倍**，而且 x64 的使用者空間位址（在 64-bit Windows 上，典型使用者模式位址是 `0x00007FF...` 這種格式）**高位三個 byte 幾乎永遠是 `0x00`**（因為 x64 的規範位址（canonical address）在 48-bit 定址空間下，高位是符號擴展出來的全 0 或全 1，使用者空間一律全 0）。

```
典型 x64 使用者空間位址範例：
  0x00007FF812345678
    └┬┘└──┬──┘
    全 0   有效位

  這個位址如果整段要寫進一個 null-terminated 字串型的 buffer，
  高位的 0x00 會提前截斷——這和 x86 位址裡偶爾出現 0x00 的情況，
  在「發生機率」上差了一個量級：x64 幾乎每個位址都躲不開高位 0x00。
```

這對哪種漏洞類型影響最大？**字串型溢位**（例如 `strcpy`/`sprintf` 這類遇 null 就停的函式造成的 overflow）在 x64 上幾乎不可能直接塞入一個完整的 8-byte 位址——高位的 `0x00` 會讓寫入提前結束。這類情境下，x64 漏洞研究往往要靠**部分覆寫**（partial overwrite，只改位址的低幾個 byte，利用目標模組基址已知的部分）或**非字串型的原語**（例如用長度已知的 `memcpy` 而非 `strcpy`）來繞過。

### jmp esp 類跳板更難用

x86 的經典跳板技法（Ch 9：找一個 `jmp esp` 指令的位址，蓋 return address 成這個位址，讓 EIP 跳到 `jmp esp`，再跳到 ESP 指向的你控制的 shellcode）之所以好用，建立在 ESP 當下就恰好指在你控制的 buffer 附近這個事實上，而 4-byte 位址本身好塞進 overflow payload。

搬到 x64：`jmp rsp` 的原理依然存在（找到這條指令的位址、蓋 return address），但兩個因素讓它變得更棘手：
1. **要塞進去的位址本身是 8 bytes**，前面提到的「高位 0x00 截斷」問題同樣適用；
2. **x64 程式的 stack 佈局和暫存器用量更複雜**（更多暫存器、更深的 prologue），`RSP` 在崩潰當下指向哪裡，比 x86 更難預判——因為 x64 函式的 prologue 常常先 `sub rsp, N` 配置一大塊 local stack，`RSP` 和「你覆寫到的位置」之間的距離不像 x86 那麼直觀。

> **未實測，理論預期**：實務上，x64 漏洞研究更常見的做法是用 ROP chain 整條接管控制流（反正 DEP 已經是預設開啟的常態），而不是依賴單一跳板跳回 shellcode——這部分策略轉移，不是本章能窮盡的，留給你在真實 CVE 分析時驗證。

### 具體案例：為什麼 strcpy 型 overflow 在 x64 上塞不進完整位址

用一個具體（但假設性）的情境把「高位 0x00」這件事講到骨子裡。假設你要把一個跳板位址 `0x00007FF712340000` 寫進一段用 `strcpy` 複製的 buffer：

```
目標位址：0x00007FF712340000
按小端序（little-endian）寫進記憶體的 byte 序列：
  00 00 34 12 F7 7F 00 00
  ^^ ^^                      ← 最低兩個 byte（位址低位）
           ^^ ^^ ^^ ^^        ← 中間四個 byte（有效位）
                       ^^ ^^  ← 最高兩個 byte（幾乎必為 0x00）

strcpy 複製到第一個 0x00 就停止：
  如果這個位址出現在 buffer 的「中間」（不是 buffer 最後 8 bytes），
  前面的 0x00（最低位元組，常見於 4K 對齊的位址）已經會提前截斷，
  後面你想寫的任何資料全部寫不進去。
```

這解釋了為什麼 x64 漏洞研究裡，字串型 copy 函式（`strcpy`/`sprintf`/`lstrcpyA` 等遇 `\0` 即停）造成的 overflow，往往要搭配**非字串型的後續操作**（例如漏洞本身先用一次字串複製觸發 overflow 到某個長度欄位，再用該欄位驅動一次已知長度的 `memcpy`），或者把要寫入的位址**刻意安排在 payload 的最後**（配合 0x00 自然收尾，不需要後面再寫東西），才能把一個完整的 x64 位址塞進去。這類「繞過 null 截斷」的構造技巧比 x86 時代常見的「避開壞字元」多了一層思考維度。

## 核心機制三：SEH 改為 table-based——x86 的 SEH overwrite 在 x64 失效

這是本章影響最大、也最該牢記的一條差異。

Ch 11–13 教你的 SEH overwrite 技法，前提是 x86 的 SEH 是 **stack-based**：每個函式進場時，把一個 `EXCEPTION_REGISTRATION_RECORD`（`{Next, Handler}` 兩個 4-byte 欄位）壓到 stack 上，串成一條鏈（`FS:[0]` 指向鏈頭）。一個 stack buffer overflow，只要寬度夠，自然就能蓋到這條鏈上某個 record 的 `Next`/`Handler`，讓例外發生時 CPU 跳去執行你控制的 `Handler`。

x64 完全拋棄了這個設計。Microsoft 在定義 x64 ABI 時，把例外處理資訊**整段搬出 stack，放進 PE image 的唯讀 section**：

```
x86（stack-based SEH）：                 x64（table-based SEH）：
─────────────────────────────            ─────────────────────────────
  stack（可寫，攻擊者碰得到）              PE image 的 .pdata / .xdata
  ┌─────────────────────┐                （唯讀，一般 overflow 碰不到）
  │ record N            │                ┌─────────────────────┐
  │  Next → record N-1   │◄── 可蓋         │ RUNTIME_FUNCTION[]  │
  │  Handler → func_ptr  │◄── 可蓋         │  BeginAddr/EndAddr  │
  ├─────────────────────┤                │  → UNWIND_INFO       │
  │ record N-1           │                │     → ExceptionHandler│
  │  ...                  │                └─────────────────────┘
  ├─────────────────────┤                例外發生時：依「例外發生
  │ ...（鏈到 FS:[0]）    │                的位址」查 RUNTIME_FUNCTION
  └─────────────────────┘                表，找到對應的 handler，
  overflow 直接寫壞 Next/Handler          完全不查 stack 上的任何東西
```

`RUNTIME_FUNCTION` 結構裡的欄位全部是**相對位址（RVA）**，指向 `.pdata`/`.xdata` 節——這兩個節在正常情況下是唯讀的（除非你本身就有任意寫原語能改 PE image，但那已經是另一個層級的攻擊面）。一個單純的 stack buffer overflow，不管寬度多大，都碰不到這些資訊。換句話說：

> **x86 的「蓋 SEH chain 上的 Next/Handler」這個攻擊面，在 x64 上結構性地不存在。**

這不是「x64 多一層防護要繞過」，是「x64 把整個攻擊面搬到你 overflow 碰不到的記憶體區域」——性質上和 DEP/ASLR 那種「設限制、可能繞過」的緩解不同，是**architecture-level 的設計變更**。想在 x64 上找類比的攻擊面，要看別的方向：Vectored Exception Handler（VEH，`RtlAddVectoredExceptionHandler` 註冊在行程層級的鏈，理論上如果你能控制到這條鏈的記憶體就有機會，但保護程度與觸發方式和 x86 SEH 完全不同）、或 `UnhandledExceptionFilter`（UEF）相關的邊界情況。這些主題、`.pdata`/`UNWIND_INFO` 的完整結構、以及 VEH/UEF 的攻防細節，`windows_pwn` Ch 12（例外處理架構 II：x64 table-based SEH / VEH / UEF）有本機實跑的反組譯示範，請對照閱讀。

## 核心機制四：暫存器更多、gadget 生態不同

x86 只有 8 個通用暫存器（`EAX/EBX/ECX/EDX/ESI/EDI/EBP/ESP`），x64 多了 `R8`–`R15` 八個，變成 16 個。表面上看「多了暫存器，gadget 選擇更多」，但實際的 gadget 生態變化更細緻：

1. **低位 8 個暫存器（RAX–RDI 對應的擴充）的 gadget 常見度沿用 x86 的直覺**——`pop rax; ret`（`58 c3`）、`pop rbx; ret`（`5b c3`）這類單 byte opcode 的 gadget，在 x64 大型模組裡依然遍地都是。
2. **`R8`–`R15` 的 gadget 需要 REX prefix**（例如 `pop r8` 是 `41 58`，比 `pop rax` 的 `58` 多一個 byte），**稀缺度明顯更高**，尤其在小型 EXE 或 strip 過符號的模組裡。這直接影響前面講的「x64 傳參要湊 `pop r8`/`pop r9`」這件事的難度。
3. **caller-saved / callee-saved 的分工也換了一套**——x86 沒有嚴格對應的概念（cdecl 底下大部分暫存器呼叫後都可能被改），x64 的 Windows ABI 明確規定 `RBX/RBP/RSI/RDI/R12–R15` 和 `XMM6–XMM15` 是 callee-saved（函式用了要存回去）。這會讓函式的 prologue 多出一串 `push rbx`/`push rsi`/`push rdi` 之類的指令，改變了 stack 在函式內的偏移關係——你在 x86 習慣的「offset 計算」直覺要重新建立。

> 完整的暫存器用量對照表、caller/callee-saved 分工表、R8/R9 gadget 稀缺度的替代策略（`xchg r8, rax`、`mov r8, rcx` 等 move gadget），已在 `windows_pwn` Ch 40 用本機實跑的 mingw 反組譯逐一驗證，本章不重複表格，請直接對照閱讀。

## 核心機制四之後：caller/callee-saved 速查與 gadget 搜尋的實務差異

把機制四的結論落成一張可以直接查的表，方便你在考場外第一次面對 x64 目標時不用重新推導：

| 類別 | x86（cdecl 慣例下的實務） | x64 Windows ABI |
|---|---|---|
| **一定是 volatile（可自由覆寫）** | 幾乎所有通用暫存器（無嚴格規定） | `RAX, RCX, RDX, R8–R11`, `XMM0–XMM5` |
| **callee 必須保存/還原** | `EBX, EBP, ESI, EDI`（約定俗成，非強制） | `RBX, RBP, RSI, RDI, R12–R15`, `XMM6–XMM15`（ABI 明文規定） |
| **對 gadget 搜尋的意義** | 找到的 `pop`/`mov` gadget幾乎都能安全當中繼站 | 如果 gadget 落在一段「有 `push rbx`/`push rsi` 的函式 prologue 裡面」，你跳進去的那個偏移點，RSP 相對函式入口的距離會因為這些 push 而位移——算 offset 要連 prologue 的 push 數量一起算 |

**實務後果**：在 x86，mona 幫你找到一個 `pop eax; ret` gadget，你幾乎不需要關心這個 gadget「活在哪個函式的哪個位置」，因為通用暫存器沒有強制的保存義務。在 x64，你若想重用一個「函式中段」的 gadget（而非整段獨立的 `pop reg; ret` 兩字節小 gadget），就必須先確認這段函式前面有沒有 `push rbx`/`push r12` 之類的 callee-saved 保存指令，否則你算的 stack offset 會全部錯位。這是 x64 gadget 搜尋比 x86 多一層「要看上下文」的具體原因。

## 驗證法：拿到一個新目標，怎麼快速確認你面對的是哪一套規則

這一節是方法論，不是理論——拿到一個陌生的 Windows binary，怎麼在幾分鐘內確認它是 x86 還是 x64、呼叫慣例用哪一套，才知道接下來該套用本章哪一半的知識。

1. **先看 PE Machine 欄位**：
   ```
   dumpbin /headers target.exe | findstr machine
   ```
   `IMAGE_FILE_MACHINE_I386`（`0x14c`）是 x86，`IMAGE_FILE_MACHINE_AMD64`（`0x8664`）是 x64。IDA/Ghidra 開檔時也會在狀態列直接顯示。這是最快、最不會錯判的一步，優先做。

2. **確認是不是 WoW64 底下的 x86 進程**（在 x64 Windows 上常見）：在 WinDbg 附掛後，若命令列提示字元是 `0:000:x86>` 這類帶 `x86` 標記，代表你正在用 WinDbg 的 WoW64 相容模式偵錯一個 32-bit 進程；若無此標記且暫存器視圖顯示 `RAX/RBX/...`（64-bit 名稱），就是原生 x64 進程。

3. **用 `.pdata` 節是否存在反推例外處理架構**：
   ```
   objdump -h target.exe | grep -E "pdata|xdata"
   ```
   x64 PE 一定有 `.pdata`（table-based unwind 的必要節），x86 PE 沒有這個節（x86 沒有這套機制，例外處理全靠 stack 上的 `FS:[0]` 鏈）。看到 `.pdata` 存在，就能直接判定「這個目標的 SEH overwrite 技法不適用，要看 VEH/UEF 這條路」。

4. **反組譯裡找呼叫慣例的指紋**：x86 的函式呼叫前會看到一串 `push`（cdecl/stdcall），x64 的函式呼叫前會看到 `mov rcx, ...` / `mov rdx, ...` 這類把參數塞進暫存器的指令，加上 `sub rsp, 0x20`（或更大）這種明顯是 shadow space 分配的痕跡。看到後者，直接確認是 x64 Windows ABI，前面講的三道門檻全部適用。

> **未實測，理論預期**：以上四步是方法論整理，沒有在單一真實 CVE binary 上逐步跑過作為本章示範；`dumpbin`/`objdump` 指令本身在本課 Ch 6（PE 格式）、`windows_pwn` Ch 40 已有本機實跑驗證,此處只是把它們重新組成一套「新目標快速定位」的檢查清單。

## 底層機制：x86 vs x64 的 ROP chain 排法對照

把前面三個機制合在一張圖裡看，直接對比「同樣要呼叫一個四參數 API」在兩個世界裡 ROP chain 長什麼樣：

```
x86 ROP chain（呼叫 func(a, b, c, d)）：
──────────────────────────────────────────────────────
  [esp+0x00] → addr of: pop eax; ret      （墊位，把 func 位址排到位）
  [esp+0x04] → addr of func
  [esp+0x08] → addr of: ret               （讓 func 的 return addr 可控，可省略視情況）
  [esp+0x0C] → d
  [esp+0x10] → c
  [esp+0x14] → b
  [esp+0x18] → a
  ────────────────────────────────────────
  （概念示意：核心是「全部參數依序排在 stack 上，func 從 stack 讀」）


x64 ROP chain（呼叫 func(a, b, c, d)，Windows x64 ABI）：
──────────────────────────────────────────────────────
  [rsp+0x00] → addr of: pop rcx; ret
  [rsp+0x08] → a                           ← 第 1 個參數進暫存器，不是 stack
  [rsp+0x10] → addr of: pop rdx; ret
  [rsp+0x18] → b
  [rsp+0x20] → addr of: pop r8; ret        ← 稀缺 gadget，可能要跨 DLL 找
  [rsp+0x28] → c
  [rsp+0x30] → addr of: pop r9; ret        ← 同樣稀缺
  [rsp+0x38] → d
  [rsp+0x40] → addr of: sub rsp, 0x28; ret ← 手動模擬 shadow space + 對齊
  [rsp+0x48] → addr of func                ← 進入 func 時 RSP 必須 16-byte 對齊
  ────────────────────────────────────────
  （核心差異：前 4 個參數要「湊特定暫存器」而非單純排 stack，
   且多一道 shadow space + 對齊的手動模擬步驟）
```

同一個目標（呼叫一個四參數函式），x86 用 7 個 slot，x64 用 9 個 slot 外加兩種稀缺 gadget 和一次對齊計算——gadget 搜尋的複雜度不是線性增加，是結構性變難。

## 對比取捨：x86 vs x64 對 exploit dev 的全景對照

| 面向 | x86（OSED 考試範圍） | x64（真實世界主流） |
|---|---|---|
| **呼叫慣例** | 全部參數上 stack（cdecl/stdcall），gadget 通用 | 前 4 個走 RCX/RDX/R8/R9，其餘上 stack，gadget 要指名 |
| **shadow space** | 無 | 32 bytes，呼叫者（即你的 ROP chain）要手動留 |
| **call 前對齊要求** | 4-byte，寬容 | 16-byte，SSE 指令誤用直接 crash |
| **位址寬度** | 4 bytes | 8 bytes，高位常含 0x00 |
| **壞字元影響** | 單一位址 4 bytes 範圍內檢查 | 檢查範圍加倍，且高位 0x00 幾乎必然出現 |
| **跳板技法（jmp esp 類）** | 直覺好用，4-byte 位址好塞 | 可用但更難預判 RSP 位置，位址本身難塞 |
| **SEH 架構** | stack-based（`FS:[0]` 鏈），overwrite 可行 | table-based（`.pdata`/`.xdata`），overwrite 結構性失效 |
| **通用暫存器數量** | 8 個 | 16 個，但 R8–R15 的 gadget 因 REX prefix 更稀缺 |
| **caller/callee-saved** | 無嚴格對應概念 | 明確規定，影響函式 prologue 與 offset 計算 |
| **OSED 是否涵蓋** | 是（考試範圍） | 否（需 `windows_pwn` 等課程另補） |

## 對比取捨：預設緩解狀態的差異

x86/x64 不只在 ABI 層不同，連「出廠預設開了哪些緩解」也有系統性差異，這點直接影響你評估一個真實目標的難度：

| 緩解機制 | 32-bit 進程（典型狀況） | 64-bit 進程（典型狀況） |
|---|---|---|
| **DEP** | 自 Windows Vista/Server 2008 起，許多系統元件預設開啟；部分舊第三方 32-bit 程式仍可能未啟用（相容性考量） | 幾乎強制開啟（x64 原生支援 NX bit，關閉的代價與相容性顧慮比 32-bit 小得多） |
| **ASLR** | 可用，但 32-bit 位址空間小（理論上限 4GB，usermode 實際約 2GB），entropy 有限，部分舊模組可能選擇不隨機化 | 64-bit 位址空間遠大，entropy 高出多個量級，info leak 的價值相對更高（猜測成本更高） |
| **SafeSEH** | 編譯期選項，舊模組常未啟用（Ch 13 的繞過前提） | 不適用——x64 的 table-based SEH 本身就不是 SafeSEH 要解決的那種問題 |
| **CFG/CET** | 可以開（MSVC `/guard:cf`、`/CETCOMPAT`），但 32-bit 目標上實務部署率明顯低於 64-bit 系統元件 | Windows 系統元件與新版商用軟體的預設部署率持續提升，是 Ch 41 要談的主角 |

> **未實測，理論預期**：以上是基於 Microsoft 官方緩解機制文件與業界對 32-bit/64-bit 部署現狀的一般性認知整理的相對比較,不是某個特定統計數字的引用；具體到某一台機器的某個進程，仍然要用 Process Hacker / `Get-ProcessMitigation`（Ch 39 會提到的檢查手法）實測確認，不能憑這張表直接下結論。

這張表的現實意義是：OSED 考試環境刻意把這些「現代緩解的部署差異」清空（練習靶多半關掉 CFG/CET，專注在考核心技法），但**走出考場遇到的 x64 目標，大概率同時疊著本章的 ABI 差異和 Ch 41 要講的現代緩解**——兩層難度加在一起，才是真實世界漏洞研究的起點。

## 踩雷集錦

1. **「我把 x86 的 SEH overwrite exploit 改一下暫存器名稱，套到 x64 目標就行」**：不行。這不是改名字的問題，是 x64 的 SEH 資訊根本不在 stack 上，你 overflow 再寬都碰不到。要攻擊 x64 目標的例外處理面，得研究 VEH/UEF 這條完全不同的路，前提條件和觸發方式都不一樣。

2. **「x64 位址比 x86 長一倍，所以 x64 比 x86 更安全」**：位址長度本身不是防禦機制（它只是讓**暴力猜測**位址更難，但 exploit dev 本來就不靠猜），真正讓 x64 更難打的是 ABI 本身的設計（暫存器傳參、shadow space、對齊）和配套的現代緩解（ASLR 在 64-bit 位址空間下的 entropy 遠高於 32-bit，見 Ch 31 的 x86 討論再對比思考）。把「位址變長」和「防護變強」混為一談會讓你誤判攻擊難度的真正來源。

3. **「x64 的 jmp esp 等價技法完全不能用」**：過度悲觀。`jmp rsp`/類似跳板依然存在且有人用，只是前提條件（RSP 在崩潰當下的可預測性、位址塞入方式）比 x86 更挑，不是「完全死絕」。真實研究裡，x64 更常見的是整條 ROP chain 接管控制流而不依賴單一跳板，但這是策略選擇，不是跳板技法被物理消滅。

4. **「mona 在 x64 目標上用法完全一樣」**：mona 本身支援 x64 目標（`!mona rop` 可以指定模組找 x64 gadget），但它找到的 gadget 的「好用程度」要你自己判斷——`pop r8; ret` 這類稀缺 gadget 如果 mona 選的落在某個會被 ASLR 重新定位、或帶壞字元的模組裡，你還是要手動換模組或手動湊 move gadget，不能完全信任自動化草稿。

5. **「OSED 只考 x86，所以 x64 知識對考試沒用，可以跳過」**：對考試分數而言，x64 細節確實不直接考（考試目標是 32-bit）。但如果你的目標是這張證照背後代表的能力（不是只拿分），跳過這章等於把自己限制在「只會打 32-bit 練習靶」的水準——而真實世界的漏洞研究職缺，x64 是常態不是例外。

6. **「Egghunter（Ch 20）在 x64 上原理完全一樣，直接搬就行」**：原理（用兩次合法 syscall 掃記憶體找 egg tag）相通，但 x86 egghunter 常用的 `int 0x2e`/`SetHandlerAddress` 搭配的 32-bit syscall 編號、以及掃描時用的暫存器（`EDX` 存 page 對齊遮罩等）全部要換成 64-bit 版本的暫存器與呼叫慣例,egghunter 本身也是一段要手寫的 shellcode,會被本章機制一（暫存器傳參）和機制二（位址/對齊）同時影響。不要假設「概念一樣=直接換行得通」。

## 進階：再往深一層

- **WoW64 環境裡的 x86 進程**：Ch 0 提過，x64 Windows 上跑的 32-bit 進程活在 WoW64（Windows-on-Windows 64）相容層裡，有一套「兩個 ntdll」的特殊佈局（32-bit 的 `ntdll.dll` 和 64-bit 的 `ntdll64.dll` 都映射進同一個進程）、以及 syscall 轉譯（32-bit syscall 經由 `wow64cpu.dll` 轉成 64-bit syscall 再進 kernel）。這意味著你在 OSED 考場攻擊的「x86 目標」，其底層系統呼叫路徑其實已經是 x64 kernel 在接手——這是 OSED 課程不深究、但你了解了會對「為什麼某些 WinDbg 行為怪怪的」豁然開朗的一塊知識。
- **x64 的 ASLR entropy 與 info leak 策略差異**：Ch 31–33 教的 x86 ASLR bypass（非隨機化模組、部分覆寫）在 x64 上部分邏輯依然適用（例如仍然可能找到未隨機化的舊模組），但 x64 位址空間的 entropy 遠高於 x86，單純的「brute force 猜位址」在 x64 上更不現實，info leak 原語（找到真正能洩漏位址的 bug，而非猜測）變得更不可或缺。
- **手寫 shellcode 在 x64 的對應差異**：Part 6 教的 PEB walk（Ch 23：從 `fs:[0x30]` 走到 `PEB → Ldr → InMemoryOrderModuleList` 找 `kernel32` 基址）在 x64 上概念完全相通，但兩個細節要換：(1) PEB 指標的來源暫存器不同——x86 用 `fs:[0x30]`，x64 用 `gs:[0x60]`（段暫存器從 `FS` 換成 `GS`，這是 x64 ABI 的規定，與呼叫慣例無關但容易搞混）；(2) 走訪的結構體欄位 offset 全部要用 64-bit 指標寬度重新算（原本 4-byte 的指標欄位變成 8-byte，連鎖讓後面每個欄位的 offset 往後位移）。手寫 x64 shellcode 時這兩點是最容易讓 Ch 23 的技法直接複製貼上卻跑不動的原因。
- **資料導向思維提前預習**：x64 目標如果開了現代緩解（尤其 Ch 41 要講的 CET shadow stack），傳統 ROP 的立足點會被連根拔起，逼著你提前習慣「不竄改控制流、只竄改資料」的思路——這和 OSED 考試裡「找到 bug 就能一路打到 shellcode」的單純世界觀已經不是同一個難度等級，是你離開考場後必須補的心態轉換。
- **這些加深內容的下一步**：本 repo 的 `windows_pwn` 課完整覆蓋 x64 + Win11 現代緩解（CFG/XFG/CET/ACG 等），是本課讀完後最自然的銜接。下一章（Ch 41）先處理一個更緊迫的問題——即使只談 x86/x64 的「經典」ROP/SEH 技法，現代緩解（CFG、CET）對它們的衝擊有多大、還剩下什麼路可走。

## 本章重點整理

- OSED 考 x86 是刻意選擇：x86 的呼叫慣例與 SEH 架構乾淨、直覺，適合教「控制流劫持」的核心概念；但真實世界主流是 x64，學完 x86 是地基，不是終點。
- **呼叫慣例**：x64 前四個整數參數走 `RCX/RDX/R8/R9`（不是 stack），要求呼叫者預留 **32 bytes shadow space**，且 `call` 前 `RSP` 必須 **16-byte 對齊**——三者疊加讓 x64 ROP chain 的工程複雜度遠超 x86。
- **位址與壞字元**：x64 位址 8 bytes，使用者空間位址高位幾乎必含 `0x00`，讓字串型 overflow 塞位址更困難，`jmp esp` 類跳板技法更挑前提條件。
- **SEH 架構**：x64 把例外處理資訊整段搬進 PE image 的 `.pdata`/`.xdata`（唯讀），x86 的 SEH overwrite（蓋 stack 上的 `Next`/`Handler`）在 x64 上結構性失效，要改攻 VEH/UEF 這條不同的路（詳見 `windows_pwn` Ch 12）。
- **暫存器與 gadget 生態**：x64 多了 `R8`–`R15`，但這些暫存器的 gadget 因 REX prefix 更稀缺；callee-saved 規則（`RBX/RBP/RSI/RDI/R12–R15`、`XMM6–XMM15`）改變了函式 prologue 的結構，連帶改變 offset 計算習慣。

## 自我檢核

- [ ] 能解釋 OSED 選 x86 當考試範圍的教學理由，同時不誤認為 x64 知識不重要
- [ ] 不看筆記，能列出 Windows x64 前四個整數參數的暫存器順序，並說出 shadow space 的大小與誰該負責留它
- [ ] 能解釋為什麼就算呼叫純整數參數的 API，x64 ROP chain 一樣要求 16-byte 對齊
- [ ] 能說出為什麼 x86 的 SEH overwrite 技法在 x64 上結構性失效（不是「被加固」，是「資訊搬家」）
- [ ] 能解釋為什麼 `pop r8; ret` 這類 gadget 比 `pop eax; ret` 更稀缺（從 encoding 角度回答）
- [ ] 拿到一個陌生 binary，能在不開 WinDbg 的情況下，只用 `dumpbin`/`objdump` 判斷它是 x86 還是 x64、以及是否有 table-based 例外處理
- [ ] 能說出 x86 PEB walk 用的段暫存器和 x64 用的段暫存器分別是什麼，以及為什麼不是同一個

## 延伸閱讀

- **本 repo [`windows_pwn` Ch 40 — x64 ABI / calling convention 對 exploit 的影響](../windows_pwn/40-x64-abi.md)**
  - **讀哪裡**：全章，特別是「參數傳遞：暫存器對照」「Shadow Space」「Stack 對齊」三節，附有本機實跑的 mingw 反組譯驗證
  - **學什麼**：本章呼叫慣例差異的完整技術細節與 encoding 對照表；本章只取結論，這裡有逐步推導
  - **前提**：本章讀完即可，不需額外背景

- **本 repo [`windows_pwn` Ch 12 — 例外處理架構 II：x64 table-based SEH / VEH / UEF](../windows_pwn/12-seh-x64-veh.md)**
  - **讀哪裡**：全章，特別是 `.pdata`/`RUNTIME_FUNCTION`/`UNWIND_INFO` 結構與本機實跑的 `objdump` 驗證段落
  - **學什麼**：x64 SEH 為什麼結構性地堵死 stack-based overwrite，以及 VEH/UEF 這條替代攻擊面的細節
  - **前提**：完成本課 Ch 11–13（x86 SEH）以建立對照基礎

- **[x64 calling convention — Microsoft Learn](https://learn.microsoft.com/en-us/cpp/build/x64-calling-convention)**
  - **讀哪裡**：「Parameter Passing」「Stack Allocation」兩節，shadow space 的正式定義在這裡
  - **學什麼**：本章所有呼叫慣例規則的第一手官方來源
  - **前提**：基本 x86-64 暫存器概念

- **[`/guard` 與 x64 Exception Handling — Microsoft Learn「x64 exception handling」](https://learn.microsoft.com/en-us/cpp/build/exception-handling-x64)**
  - **讀哪裡**：table-based unwind 的概觀段落
  - **學什麼**：x64 例外處理為什麼要設計成 table-based 的官方敘述，補足本章 SEH 段落的設計動機
  - **前提**：本章 SEH 段落讀完即可

- **[Agner Fog — Calling Conventions for Different C++ Compilers and Operating Systems（PDF）](https://www.agner.org/optimize/calling_conventions.pdf)**
  - **讀哪裡**：第 6 節「Calling conventions for 64-bit Windows」
  - **學什麼**：比 MSDN 更密集的邊角案例（HFA、variadic、naked function），`windows_pwn` Ch 40 的對照表來源之一
  - **前提**：熟悉 C/C++ 函式宣告語法

x86 的技法學完之後，下一個該誠實面對的問題不是「x64 差在哪」，而是「就算待在 x86/x64 的經典技法範圍裡，CFG 和 CET 這類現代緩解已經讓多少招失效」——下一章直接拆這個問題。

→ [Ch 41 — 加深一圈 II：CFG/CET 衝擊 + ROP 自動化](./41-deeper-cfg-cet-rop-automation.md)
