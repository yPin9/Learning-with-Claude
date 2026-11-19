# Ch 28 — ROP gadget 尋找（mona rop / rp++）與 chain 規劃

> **目標**：把 ROP（Return-Oriented Programming）的機制原理從「聽過」練到「能自己規劃」——知道去哪裡找 gadget（mona `rop`、rp++）、怎麼判斷一個 gadget 能不能用（短、無副作用、不含壞字元、來源模組無 ASLR）、常見 gadget 型別各自解決什麼問題,以及面對「我要呼叫一個 API」這個目標時,怎麼倒著規劃出整條 chain。

> **環境**：Win10 x64 + WinDbg（2026）+ mona v3 + Python 3

## 為什麼需要專門練「找 gadget」這件事？

Ch 27 講完了 DEP 為什麼逼你走 ROP,但「知道要走 ROP」和「真的能組出一條能跑的 ROP chain」之間,隔著一個很具體的工程問題：**你的 chain 需要的那些小片段（gadget）,實際上存在不存在、在哪裡、品質好不好？**

這不是理論問題,是搜尋問題。一個隨便抓的二進位檔案裡,`pop eax; ret` 可能有十幾個候選位址,但「我要的那個特定組合」（例如 `pop ebx; pop esi; ret` 且不含 `\x00`）可能只有一兩個、甚至需要跨模組找。這一章就是練這套「規劃需求 → 搜尋候選 → 篩選品質 → 排進 chain」的工作流,Ch 29 才會用這套工作流去真正組出呼叫 `VirtualProtect` 的完整 chain。

## 先建立直覺：ROP 怎麼靠 ret 串起片段

複習一下機制,這次聚焦在「為什麼 `ret` 是關鍵」：

```
正常的函式呼叫（CPU 眼中）：

  call some_func     ; push 返回位址,跳進去
  ...
some_func:
  ...
  ret                ; 彈出 stack 頂端的值,當成位址跳過去

一般情況下,ret 彈出的是「call 自己 push 的返回位址」。
ROP 的招數：讓 ret 彈出「攻擊者在 stack 上事先排好的任意位址」。
```

```
ROP chain 執行時的 stack 示意（x86,ESP 往下為高位址）：

  ESP → ┌──────────────────┐
        │ gadget1_addr     │ ← 第一次 ret 彈這個,EIP 跳到 gadget1
        ├──────────────────┤
        │ gadget2_addr     │ ← gadget1 自己結尾也有個 ret,彈這個
        ├──────────────────┤
        │ gadget3_addr     │
        ├──────────────────┤
        │        ...       │
        └──────────────────┘

每個 gadget 自己是一小段「做點事 + 以 ret 結尾」的既有程式碼：

  gadget1:  pop eax     被執行
            ret         彈 stack 頂端(此時已經是 gadget2_addr)→ 跳 gadget2

  gadget2:  pop ebx
            ret         彈下一個 → 跳 gadget3

  ...
```

關鍵洞察：**ESP 本身就是一個「指令指標」的替代品**。每次 `ret` 執行,CPU 做的事其實就是「用 ESP 當位址讀一個值,把它當成下一個要跳的位址,然後 ESP 往前推 4 bytes」。只要攻擊者控制了 stack 上一長串連續的位址,就等於控制了一串連續要執行的「跳轉」——每個跳轉落點就是一個 gadget。

## 核心：gadget 的找法

### 工具一：mona `rop`

mona（Ch 0 裝好的那支外掛）內建 ROP gadget 搜尋，`!mona rop` 是整個工作流的起點：

```
; 在 WinDbg（透過 pykd）裡,先確認要從哪些模組找 gadget
!py mona modules

; 對指定模組做完整 ROP 掃描,同時過濾壞字元
!py mona rop -m <modules> -cpb "\x00\x0a\x0d"
```

- `-m <modules>`：逗號分隔的模組清單（例如 `vulnserver.exe,essfunc.dll`）。限定範圍能大幅縮短掃描時間,也把 gadget 來源鎖定在你信任（ASLR 行為已知）的模組。
- `-cpb "\x00\x0a\x0d"`：壞字元過濾（bad char filter）——和 Ch 8 學的壞字元分析用同一套概念,但這次過濾的不是 shellcode,是**每一個 gadget 位址本身的 4 個 bytes**。如果某個 gadget 的位址裡含有壞字元,mona 直接把它從候選清單剔除。

mona 執行完會輸出兩個東西：
1. 一份完整 gadget 清單（存成文字檔,通常叫 `rop.txt`）,按指令型態分類。
2. 一份自動產生的 `rop_chains.txt`，裡面是**針對 VirtualProtect/VirtualAlloc 等常見目標自動拼好的 Python chain 骨架**——Ch 29 的重點就是讀懂、修補這份骨架。

> **未實測，理論預期**：`!py mona rop` 的實際輸出格式與掃描耗時依目標模組大小而定,本機未實際跑過。驗證法：先對一個小模組（例如靶程式自己的 exe,通常幾百 KB）跑,確認指令語法通順、能跑出 `rop.txt`,再逐步加大範圍到系統 DLL。

### 工具二：rp++

`rp++`（rp-win.exe / rp-lin 等平台版本）是另一套獨立的 ROP gadget 搜尋器,常在 mona 掃描結果不夠用、或需要離線分析（不想掛 WinDbg）時補位：

```
# 未實測,理論預期 — rp++ 命令列語法
rp-win-x86.exe --file target.dll --rop=3 --unique --bad-bytes="00"
```

- `--rop=3`：限制 gadget 最長 3 條指令（越短越可靠,見下方品質判斷）。
- `--unique`：去重複。
- `--bad-bytes`：和 mona 的 `-cpb` 同概念。

mona 和 rp++ 的差異不在「找不找得到」，而是 mona 整合在 WinDbg/Immunity 流程裡,找完能直接接 chain 自動生成；rp++ 是純離線分析工具,輸出需要自己整理,但掃描速度和規則細節上有時比 mona 更靈活（例如可以精確指定最大 gadget 長度）。OSED 考場兩個都可能用到,mona 是主力、rp++ 是補位。

## gadget 品質：什麼叫「好用」

找到一堆候選位址之後,不是隨便挑。判斷標準按重要性排：

1. **短**：理想是 1–2 條指令 + `ret`。越長,副作用越多、越難推理執行完後各暫存器的狀態。
2. **無副作用（或副作用可預期、可接受）**：例如 `pop eax; pop ebx; ret` 看起來是「設兩個暫存器」,但如果中間藏了一個 `inc ecx`,你的 chain 裡 `ecx` 會被動到——如果後面剛好要用 `ecx` 存別的東西,就會被污染。每個 gadget 都要看完整反組譯,不能只看「關鍵字」。
3. **不含壞字元**：gadget 的**位址**（4 bytes,小端序）本身要過壞字元篩,這點最容易漏——位址裡出現 `0x00` 是最常見的雷,因為很多模組的 base 落在低位址範圍。
4. **來源模組 ASLR 行為已知/穩定**：優先用沒開 `/DYNAMICBASE` 的模組（Ch 31 會細講怎麼判斷）,如果只能用系統 DLL（開了 ASLR）,代表你的 exploit 需要先做 info leak 才能用——這通常超出單純 DEP bypass 的範圍,先知道這個依賴存在。

```
好 gadget vs 壞 gadget 示意：

  好：0x10015042  pop ebx ; ret                  ← 2 instr,無副作用,乾淨
  好：0x10016a01  pop eax ; pop ebx ; ret         ← 3 instr,兩個都是 pop,可預期

  差：0x10017eb3  pop ebx ; add eax,4 ; ret       ← 動到了你可能還在用的 eax
  差：0x7c001000  pop edx ; ret                   ← 位址含 0x00（最高位元組),壞字元問題
  差：0x100183af  mov [ebx+4], eax ; xor ecx,ecx ; pop ebp ; ret
               ↑ 太長,副作用太多（寫記憶體 + 清 ecx + pop）,除非恰好需要全部效果,否則別用
```

## 常見 gadget 型別與各自解決的問題

| 型別 | 範例 | 解決什麼 |
|---|---|---|
| **`pop reg; ret`** | `pop eax; ret` | 把 stack 上下一個值載入某個暫存器——組參數最基本的積木 |
| **`mov [reg], reg; ret`** | `mov [ebx], eax; ret` | 把一個值寫到某個記憶體位址,例如填 `lpflOldProtect` 需要的可寫位址,或動態修補 chain 本身 |
| **`xchg reg, reg; ret`** | `xchg eax, esp; ret` | 交換兩個暫存器,常用在 stack pivot 或繞開「某個值只能先出現在固定暫存器」的限制 |
| **`add`/`sub`/`neg; ret`** | `neg eax; ret` | 用算術**動態產生**一個數值,迴避「這個常數的 bytes 裡有壞字元」的問題（例如要 `0x40` 但該位置不能出現該值的某種編碼,改成先湊出 `0xFFFFFFC0` 再 `neg`） |
| **`pushad; ret`** | `pushad; ret` | 一次把 EAX/ECX/EDX/EBX/ESP(呼叫前)/EBP/ESI/EDI 八個暫存器全部 push 到 stack——如果事先把這八個暫存器設好要的參數值,一個 `pushad` 就等於同時把好幾個 stdcall 參數排上 stack,比一個個 `push` 省很多 gadget |
| **stack pivot** | `xchg eax, esp; ret` / `add esp, 0x100; ret` | 把 `ESP` 換到攻擊者完全可控的另一塊記憶體（通常是 heap 或更大的 buffer),用在 overflow 能寫的空間太小、放不下完整 chain 的情況 |

### 壞字元迴避的算術技巧

這個技巧值得單獨展開,因為 OSED 考場很常遇到：你需要的常數（例如 `PAGE_EXECUTE_READWRITE = 0x40`）如果直接當 4-byte little-endian 寫進 payload 沒問題（`0x40` 不是常見壞字元）,但如果目標值恰好含壞字元（例如某些位址或某些 size 值裡有 `0x00`）,可以改用「先湊一個不含壞字元的值,再用算術 gadget 轉成目標值」：

```
目標：讓某個暫存器最終變成 0x00000040（假設這個值本身在某種編碼環境下有問題，示意用）

做法一：neg
  pop eax; ret         → eax = 0xFFFFFFC0  （這個值本身不含 0x00）
  neg eax; ret          → eax = 0x00000040  ✓

做法二：add / sub 湊兩個不含壞字元的數相加
  pop eax; ret         → eax = 0x10000040
  sub eax, 0x10000000; ret → eax = 0x00000040  ✓（前提：0x10000000 和 0x10000040 都不含壞字元,各自都要查）
```

這套技巧在 Ch 29 組 `VirtualProtect` 的 `flNewProtect` 參數時會直接用到。

## chain 規劃思路：倒著想「我要呼叫一個 API」

規劃一條 ROP chain,建議用這個順序思考,而不是從第一個 gadget開始硬湊：

```
Step 1：先定最終目標
        「我要讓 CPU 執行到 VirtualProtect(lpAddress, dwSize, 0x40, &old)
          然後返回到 shellcode_addr」

Step 2：倒推 stdcall 的 stack 佈局（Ch 29 會細講,這裡先抓骨架）
        [VirtualProtect 位址]
        [返回位址 = shellcode_addr]
        [arg1][arg2][arg3][arg4]
        → 這一段的位置和順序是「死的」,stdcall calling convention 決定,不能亂排

Step 3：往前補「怎麼把這段佈局正確地放到 stack 上」
        如果 overflow 本來就能連續寫一大塊 stack,也許可以直接把
        [VP addr][ret addr][arg1..4] 當成普通資料寫進去,完全不需要 gadget！
        —— 這是最理想情況,很多簡單場景就是這樣。

Step 4：只有當某個參數「值無法直接寫進 payload」時才需要 gadget
        常見原因：
          a) 參數值本身含壞字元 → 用算術 gadget 動態產生（如上）
          b) 參數值要「等到執行時才知道」（例如 shellcode 在 stack 上的絕對位址,
             因為 ASLR 或 offset 計算複雜，難以在寫 exploit 時就算出常數）
             → 用 pop/mov 類 gadget,在執行時用 ESP 相對位置算出來
          c) overflow 空間不夠放完整 chain → stack pivot 先跳到更大空間

Step 5：把 Step 4 需要的 gadget 一個個排在 Step 2 的佈局前面,
        确保每個 gadget 執行完的狀態剛好是下一個 gadget期望的狀態
```

畫成 stack 執行圖：

```
ESP 起點（ROP chain 開始）
  │
  ▼
┌──────────────────────────────┐
│ [gadget: 取得 shellcode_addr] │  ← Step 4b：動態算位址(如果需要)
│ [gadget: 存到某個暫存器/位址]  │
├──────────────────────────────┤
│ [gadget: 把 0x40 湊出來]      │  ← Step 4a：迴避壞字元(如果需要)
├──────────────────────────────┤
│ [VirtualProtect 位址]         │  ← Step 2：死的佈局開始
│ [shellcode_addr]              │  ← 返回位址(=shellcode)
│ [lpAddress]                   │
│ [dwSize]                      │
│ [0x40]                        │
│ [&lpflOldProtect]             │
├──────────────────────────────┤
│ [shellcode bytes...]          │  ← VP 執行完後跳過來這裡
└──────────────────────────────┘
```

Ch 29 會把這張圖的每一格填上真正的 gadget 候選和理由。

## 對比取捨：mona rop vs rp++ vs 手動排

| 方式 | 優點 | 缺點 |
|---|---|---|
| **mona `rop -m ...`** | 一條指令掃完+自動產生針對 VirtualProtect 的 chain 骨架,整合進 WinDbg 工作流 | 骨架有時用了你不熟悉的 pattern（例如大量 `pushad`),要花時間讀懂才能改 |
| **rp++** | 掃描規則更可控（最大長度、去重複)，離線用、速度快 | 沒有自動產生 chain,找完要自己手排 |
| **純手動排（Ch 29 的手工版）** | 完全理解每個 gadget 在做什麼,最適合學習 | 耗時,靠肉眼在一堆候選裡挑,容易漏掉壞字元/副作用 |

OSED 考試現場的實務做法通常是：**mona 先掃出候選、生出骨架 → 手動核對每個 gadget（讀反組譯確認無副作用、無壞字元）→ 必要時換掉 mona 選錯的那個**。純手動排從頭做一遍,是本課用來訓練「理解」的方式,不是考場唯一節流做法。

## 踩雷集錦

1. **只看 gadget 的「關鍵字」,沒讀完整反組譯**：`pop eax; ret` 搜尋結果裡混進 `pop eax; inc ecx; ret` 這種,如果沒注意到多了一條指令,chain 排到一半才發現某個暫存器被污染,debug 半天。

2. **忘記篩 gadget 位址本身的壞字元**：只篩了 shellcode 和參數常數的壞字元,忘了每個 gadget 位址（4 bytes）也要過一遍 `-cpb`。沒過濾的話,payload 傳輸過程中可能被目標協定當成終止字元截斷。

3. **挑了來自會被 ASLR 重新隨機化的模組**：練習時在自己機器上測成功,換一台機器或重開機就全壞——因為 gadget 來源模組其實有 `/DYNAMICBASE`,之前只是「這次開機恰好沒變」而已。先用 mona 確認 ASLR 狀態（Ch 31 會細講）。

4. **`pushad` 用錯順序假設**：`pushad` 把八個暫存器依固定順序 push（EAX,ECX,EDX,EBX,原始ESP,EBP,ESI,EDI),如果事先沒把每個暫存器設成你要的值,直接套用 mona 生成的 `pushad` chain 會把垃圾值排進 stdcall 的參數位置。

5. **用太長的 gadget 貪圖「一次做多件事」**：長 gadget 看起來省事,但只要中間有一條指令不是你預期的（例如誤判了運算元順序），整條 chain 從那一步之後全部錯位,而且錯位通常不會馬上 crash,會在幾個 gadget 之後才爛掉,排查起來比短 gadget 麻煩得多。

## 進階：再往深一層

- **Gadget 的「意外」來源**：x86 指令是變長編碼,一段合法指令的中間某個 byte,接著解讀下去可能恰好湊出完全不同、但同樣合法的一串指令——這叫 **unintended gadget**。mona/rp++ 掃描時不只看函式邊界,是逐 byte 位移去找所有可能的 `ret` 結尾序列,這也是為什麼看起來「正常」的程式碼裡能挖出意外豐富的 gadget 庫。
- **ROP 的圖靈完備性**：Shacham 2007 年的論文證明只要 gadget 集合夠豐富,ROP 理論上可以做任意圖靈完備計算（條件分支、迴圈等),不只是「呼叫一個 API」這麼簡單。考試現場不需要走到這麼複雜,但知道這個理論上限有助於理解為什麼進階 CTF 題能用 ROP 做出完整邏輯。

## 本章重點整理

- ROP 靠 **`ret` 反覆把 ESP 當成「指令指標」使用**：每次 `ret` 彈出 stack 頂端當位址跳過去,串起一系列既有的程式碼片段（gadget）。
- 找 gadget 的主力工具是 **mona `!py mona rop -m <modules> -cpb <badchars>`**，補位用 **rp++**；兩者都要篩壞字元、限定可信模組範圍。
- 判斷 gadget 好不好用,依序看：**短、無副作用、位址不含壞字元、來源模組 ASLR 行為已知**。
- 常見 gadget 型別各司其職：`pop;ret` 設值、`mov [reg],reg;ret` 寫記憶體、`xchg`/算術繞壞字元或動態產生值、`pushad` 一次擺多參數、stack pivot 解決空間不足。
- chain 規劃建議**倒著想**：先定函式呼叫的死佈局,再往前補「哪些參數值無法直接寫進payload,需要 gadget 動態產生」。

## 自我檢核

- [ ] 能解釋為什麼 ROP 靠的是 `ret`,而不是隨便哪種跳轉指令
- [ ] 能背出判斷 gadget 品質的四個標準,並說出每一個為什麼重要
- [ ] 能說出 `pushad` gadget 的用途和使用前提（八個暫存器要先設好）
- [ ] 能舉例說明什麼情況需要用算術 gadget 繞開壞字元
- [ ] 給一個目標函式呼叫,能用「倒著想」的順序規劃出需要哪些 gadget

## 延伸閱讀

- **[corelan/mona（GitHub）](https://github.com/corelan/mona)**
  - 讀哪裡：README 裡 `rop` 指令的旗標說明（`-m`、`-cpb`、`-t`）。
  - 學什麼：`!mona rop` 完整參數語意,本章指令範例的權威出處。
  - 前提：Ch 0 環境就位。

- **Corelan — "Exploit writing tutorial part 10: Chaining DEP with ROP"**（corelan.be）
  - 讀哪裡：mona ROP 掃描與 `pushad` chain pattern 的實際示範段落。
  - 學什麼：mona 自動生成 chain 的實務細節,和本章規劃思路互補。
  - 前提：本章。

- **[rp++（GitHub, 0vercl0k/rp）](https://github.com/0vercl0k/rp)**
  - 讀哪裡：README 的命令列參數（`--rop`、`--bad-bytes`、`--unique`）。
  - 學什麼：離線 gadget 搜尋的替代工具,掌握和 mona 不同的篩選維度。
  - 前提：無,但先懂本章的 gadget 品質判斷標準再看會更有感。

- **"The Geometry of Innocent Flesh on the Bone"** — Hovav Shacham（ACM CCS 2007）
  - 讀哪裡：Section 3–4（ROP 的形式化定義與 gadget 完備性論證）。
  - 學什麼：ROP 的學術根源,理解「為什麼湊夠 gadget 能做任意計算」。
  - 前提：本章 + 基本計算理論背景（選讀,不影響考試準備）。

找到 gadget、懂了規劃思路,下一章把這套工作流用在 OSED 最核心的那條鏈上——手工打造一條完整呼叫 `VirtualProtect` 的 ROP chain。

→ [Ch 29 手工打造 VirtualProtect / VirtualAlloc ROP chain](./29-virtualprotect-rop-chain.md)
