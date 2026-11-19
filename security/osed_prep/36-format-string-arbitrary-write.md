# Ch 36 — 任意寫：%n 改寫導向 code exec

> **目標**：把 `%n` 從「原理」變成「武器」——理解 `%n` 寫入的值怎麼被寬度指定符控制、怎麼用 `%hn`/`%hhn` 分段寫出一個完整 4-byte 位址，以及在 Windows 上（沒有 Linux 的 GOT）該改寫什麼目標才能把控制流導向攻擊者的程式碼。**全程建立在 `%n` 已被目標啟用的前提上**——這個前提在 Windows 上不是預設成立,務必先確認。

> **環境**：Win10 x64 + WinDbg（2026）+ Python 3

## 為什麼需要這一章

Ch 35 的任意讀讓攻擊者能「看」記憶體,但看不等於控制。真正讓 format string 漏洞升級成完整 exploit 的是 `%n`——它把「印出什麼」變成「寫入什麼到哪裡」。這一章要把 Ch 34 提過的機制拆成可操作的流程：怎麼控制寫入的**數值**(靠撐大已輸出字元數)、怎麼分段寫出完整的 **4-byte 位址**(靠 `%hn`/`%hhn`)、以及改寫哪個**目標**才能真正劫持執行流。

在正式動手之前,再次強調本章建立在的前提,因為它決定整條路能不能走:

> **再次標注**：Windows 的 VC runtime 預設停用 `%n`。本章後面所有 payload 設計,都假設目標程式已經呼叫過 `_set_printf_count_output(1)`,或者目標使用的格式化函式庫本身不走標準 VC runtime 的 `%n` 停用邏輯(例如移植自 Linux 的程式碼、自行實作的 mini-libc)。**如果你還沒確認這個前提成立,先回到 Ch 34 的查證方法,把這件事確認清楚,否則本章的任意寫思路在實際目標上完全不會發生任何寫入動作。**

## 先建立直覺：%n 寫入的是「已輸出字元數」,不是你指定的數值

這是整章最容易誤解的地方。`%n` 不是「寫入某個你直接指定的數字」,而是寫入**從 format string 開始到目前這一刻,`printf` 總共已經輸出了多少個字元**。

```c
printf("AAAA%n");
// 輸出 "AAAA"(4 個字元)之後,%n 把整數 4 寫入
```

要讓 `%n` 寫入攻擊者想要的任意數值(例如一個函式指標的某個位元組),就必須想辦法讓「目前已輸出字元數」剛好等於那個數值。做法是用**寬度指定符(width specifier)**撐大某一輪輸出的字元數,而不印出真正有意義的內容:

```c
printf("%100x%n");
// %100x:把下一個 slot 的值印成 16 進位,但「至少」佔 100 個字元寬度
//       (不足的部分用空白補齊)
// 此時已輸出字元數 = 100,%n 就會寫入 100
```

```
┌─────────────────────────────────────────────────┐
│  %100x 的輸出(假設 slot 值是 0x41,印出來是 "41")  │
│  ┌──────────────────────────────────────────┐    │
│  │ (98 個空白填充)                      "41"  │    │
│  └──────────────────────────────────────────┘    │
│  總共 100 個字元 ──────────▶ %n 寫入的值 = 100     │
└─────────────────────────────────────────────────┘
```

這就是「控制寫入數值」的核心機制：**不是直接指定要寫什麼,而是透過累積輸出字元數,間接把這個數字「做出來」**。想寫入 `1000`,就想辦法讓前面累積輸出 1000 個字元;想寫入 `0x41414141`(十進位超過 10 億),理論上要撐出超過 10 億個字元的輸出——這顯然不現實,所以需要下一節的分段寫入技巧。

## 核心技巧：用 %hn / %hhn 分段寫出一個 4-byte 位址

直接撐出一個 4-byte 大數值(最大到 `0xFFFFFFFF`,約 43 億)需要 printf 輸出 43 億個字元,不可能在合理時間內完成,也會讓 payload 長度不可控。解法是把 4-byte 的寫入拆成多個**較小單位**的寫入,分別寫到目標位址的不同偏移上。

Windows CRT 和大多數現代 `printf` 實作支援長度修飾符(length modifier)：

- `%n`：寫入 4 bytes(`int`)
- `%hn`：寫入 2 bytes(`short`),只寫入目前累積字元數的低 16 bits 到指定位址
- `%hhn`：寫入 1 byte(`char`),只寫入低 8 bits

用 `%hn` 把一個 4-byte 位址拆成**高 2 bytes** 和**低 2 bytes** 兩次寫入,分別導向位址的 offset 0 和 offset 2:

```
目標要寫入的值：0x6f4a1234(假設是一個函式指標要改寫成的新值)

拆成兩段：
  低 16 bits:0x1234 → 寫到 目標位址 + 0(offset 0)
  高 16 bits:0x6f4a → 寫到 目標位址 + 2(offset 2)

ASCII 圖(little-endian 記憶體佈局)：

目標位址 (低位址)                         目標位址+3 (高位址)
┌────────┬────────┬────────┬────────┐
│  0x34  │  0x12  │  0x4a  │  0x6f  │
└────────┴────────┴────────┴────────┘
    ↑ 第一次 %hn 寫這 2 bytes      ↑ 第二次 %hn 寫這 2 bytes
    (寫入數值 0x1234)              (寫入數值 0x6f4a,
                                    但要先讓累積字元數從之前的狀態
                                    再「增加」到對應這個數值)
```

**關鍵細節**:兩次 `%hn` 寫入時,`printf` 的「已輸出字元數」是**持續累加**的,不會在兩次寫入之間重置。所以寫第二段時,必須計算「要讓累積字元數從第一次寫入後的狀態,再增加多少,才能讓第二次 `%hn` 寫入時的總字元數等於第二段要的值(取 16 bits)」。如果直接算出來的目標值比目前已累積的字元數還小(例如低位是大數、高位是小數),就需要利用 16-bit 截斷的特性:寫入時只取累積字元數的低 16 bits,所以可以透過「累積字元數 mod 65536」等於目標值這個關係來調整,通常會把**較小的值放前面寫、較大的值放後面寫**,減少需要撐出的 padding 數量。

## 完整思路：從定位到任意寫的 payload 結構

結合 Ch 35 的 offset 定位技巧,一個典型的任意寫 payload 結構如下：

```
┌──────────────────┬──────────────────┬───────────────────────────┐
│  目標位址低 2 bytes │  目標位址高 2 bytes │  format string 部分         │
│  的寫入目的位址     │  的寫入目的位址     │  (撐 padding + %hn x2,     │
│  (4 bytes,         │  (4 bytes,         │   搭配位置參數或逐輪 %x 佔位 │
│   = target_addr)   │   = target_addr+2) │   跳到這兩個位址對應的 slot) │
└──────────────────┴──────────────────┴───────────────────────────┘
```

```python
#!/usr/bin/env python3
import socket, struct

target_ip, target_port = "192.168.x.x", 9999

offset = 7                                  # Ch 35 定位出的 offset
target_addr = 0x6f4a3000                    # 想改寫的位址(例如某函式指標儲存位置)
new_value   = 0x6f4a1234                    # 想寫入的新值(例如 shellcode 位址,僅示意)

low_word  = new_value & 0xFFFF              # 0x1234
high_word = (new_value >> 16) & 0xFFFF      # 0x6f4a

addr_low  = struct.pack("<I", target_addr)       # 低 2 bytes 寫入目的位址
addr_high = struct.pack("<I", target_addr + 2)   # 高 2 bytes 寫入目的位址

# 計算 padding,讓第一次 %hn 時累積字元數 = low_word
# (假設兩個位址各佔 4 bytes,已經輸出了 8 個字元;%x 佔位輪數依 offset 調整)
pad1 = low_word - 8
if pad1 < 0:
    pad1 += 0x10000     # 16-bit 截斷特性:不足就加一圈

# 計算第二次 %hn 的 padding,讓累積字元數(從 low_word 繼續累加)增加到 high_word
pad2 = (high_word - low_word) & 0xFFFF

fmt = f"%{pad1}x%{offset}$hn" .encode() if False else None
# 註:%N$ 位置參數語法在 Windows CRT 上支援與否「以目標實測為準」(Ch 34/35 已標注)。
# 若目標不支援位置參數,改用逐輪 %x 佔位到對應 slot,寫法如下(較保守,相容性更高):

fmt = (
    b"%" + str(pad1).encode() + b"x" +      # 撐出 pad1 個字元
    b"%hn" +                                 # 寫低 2 bytes 到 addr_low 指向位址
    b"%" + str(pad2).encode() + b"x" +      # 再撐出 pad2 個字元
    b"%hn"                                   # 寫高 2 bytes 到 addr_high 指向位址
)

payload = addr_low + addr_high + fmt

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.connect((target_ip, target_port))
s.send(payload + b"\r\n")
s.close()
print("[+] arbitrary write payload sent")
```

> **未實測,理論預期**：這份腳本把 Ch 34–35 的機制串成一條完整流程,但 padding 數字的精確計算(尤其是「前面已經有 offset-2 輪 `%x` 佔位,各佔幾個字元」這個變數)**必然因目標程式的實際 stack 佈局而異**,此處的 `pad1 = low_word - 8` 只是示意公式,8 這個數字代表「兩個 4-byte 位址被某輪 `%x` 讀出印成 16 進位字串後的字元數」,實際字元數取決於位址值本身印出來有幾位數——這正是 format string exploit 公認最繁瑣的部分,必須在 WinDbg 裡逐步核對每一輪輸出的實際字元數,反覆調整 padding 直到兩次 `%hn` 寫入的值精確吻合。延伸閱讀的 Corelan 系列有更完整、更嚴謹的公式推導,建議對照練習。

## 底層機制：控字元數 → %n 寫位址 → 劫持指標

```
┌───────────────────────────────────────────────────────────┐
│ ① 攻擊者透過 width specifier(%N x)精確控制 printf          │
│    到某一刻為止「已輸出的字元數」                             │
│         │                                                   │
│         ▼                                                   │
│ ② %hn / %hhn 把這個字元數的低 16 / 8 bits,                  │
│    寫入到「下一個 slot 的值」所指向的記憶體位址               │
│         │                                                   │
│         ▼                                                   │
│ ③ 分兩次(或四次,用 %hhn)寫入,拼出一個完整 4-byte 的新值      │
│    覆寫到目標位址(例如某個函式指標、return address)           │
│         │                                                   │
│         ▼                                                   │
│ ④ 程式稍後讀取並呼叫這個被改寫的指標                         │
│    → 執行流跳到攻擊者指定的位址(shellcode / ROP gadget)      │
└───────────────────────────────────────────────────────────┘
```

這條鏈和 Ch 7–13 的 stack/SEH overflow 本質上在做同一件事——「找到一個會被程式信任並執行的指標,把它改成攻擊者控制的值」。差別只在**怎麼寫**:overflow 是靠溢出直接覆蓋相鄰記憶體,format string 任意寫是靠 `%n` 間接寫到**任意**(不需要相鄰)位址。這正是為什麼任意寫比單純的 stack overflow 更有彈性——不受「溢出方向、相鄰記憶體佈局」限制,理論上能寫到程式有讀寫權限的任何位址。

## 要寫去哪:Windows 沒有 Linux 的 GOT,改寫目標怎麼選

Linux 下的經典 format string 任意寫教材常拿 **GOT(Global Offset Table)** 當改寫目標——GOT 存著動態連結函式的實際位址,改寫它等同於劫持「下次呼叫這個函式時跳去哪裡」,效果立竿見影。Windows PE 格式**沒有 GOT 這個結構**(動態連結機制不同,Ch 6 已講過 PE 佈局),所以必須換一批目標:

| 目標 | 說明 | 可行性 |
|---|---|---|
| **Return address** | 和 overflow 一樣的核心目標——改寫 stack 上某層呼叫的 return address,函式返回時直接跳到攻擊者指定位址 | 高,前提是能算出 return address 在 stack 上的確切位址(需要已知的 stack leak,接 Ch 35) |
| **SEH handler** | 改寫 exception handler 的指標(Ch 11–13 的 SEH overwrite 技巧),觸發例外時執行流跳過去 | 中高,需配合 Ch 13 的 SafeSEH 繞過考量 |
| **函式指標(function pointer)** | 程式自己在 heap/全域變數裡存的 callback 指標、vtable 指標等,改寫後等程式呼叫它 | 視目標程式架構而定,需逆向(Part 4 技巧)找到這類指標的位置 |
| **IAT(Import Address Table)條目** | PE 格式裡確實存在 IAT,功能類似 GOT——改寫某個 import 函式在 IAT 裡記錄的實際位址,後續對該函式的呼叫都會跳到攻擊者指定位址 | 高,是 Windows 上最接近「GOT 攻擊」思路的對應目標,但需要先定位 IAT 條目的位址(IDA 靜態分析可找到) |

> **選擇哪個目標取決於能不能先用 Ch 35 的任意讀,定位出目標位址的確切記憶體位置**。IAT 條目的位址在模組載入後是固定偏移(相對模組基址),只要先 leak 出模組基址(Ch 35),加上靜態分析(IDA)算出的 IAT 偏移,就能得到確切的絕對位址,是四個選項裡最穩定、最常用於 OSED 情境的目標。Return address 的優勢是概念上和 overflow 一致、容易理解,但缺點是它在 stack 上的位置隨每次呼叫深度變化,需要更精確的 stack leak 才能定位。

## 對比：%n 任意寫 vs Linux GOT overwrite vs 直接 overflow 劫持 EIP

| 手法 | 改寫目標 | 前提 | OSED 情境可行性 |
|---|---|---|---|
| Linux glibc `%n` → GOT overwrite | GOT 條目 | glibc 預設啟用 `%n` | 不適用(Windows 無 GOT) |
| Windows `%n` → IAT overwrite | IAT 條目 | **目標已啟用 `%n`**(非預設)+ 已知模組基址(leak) | 中——OSED 若出此題型通常會明確鋪陳啟用 `%n` 的情境 |
| Windows `%n` → return address overwrite | stack 上的 return address | **目標已啟用 `%n`** + 已知該 return address 的 stack 位址(leak) | 中——需要精確 stack leak,比 overflow 直接覆寫更繁瑣 |
| 直接 stack overflow 劫持 EIP(Ch 7） | return address(透過溢出,不需要知道確切位址,靠填滿覆蓋) | 存在長度檢查缺陷的 unsafe copy | 高——OSED 最常見、最穩定的題型,不需要 `%n` 這個額外前提 |

這個對比表格說明了一件事：**如果目標同時存在 overflow 漏洞和 format string 漏洞,優先選 overflow**,因為它不依賴 `%n` 這個在 Windows 上預設關閉的脆弱前提。Format string 任意寫是在「目標沒有更直接的 overflow 可用、但確實存在已啟用 `%n` 的 format string 漏洞」這種特定情境下才派上場的技巧。

## 踩雷集錦

1. **沒確認 `%n` 是否啟用就開始設計 payload**：本章開頭已強調,整章內容建立在這個前提上。送出 payload 後毫無動靜或直接 access violation,第一個該懷疑的永遠是這個前提是否成立。
2. **把 width specifier 的數字算錯方向**：`%Nx` 的 N 是「至少輸出 N 個字元(用空白補齊)」,不是「輸出 N 次」或其他語意。如果 slot 值本身印出來已經超過 N 個字元,width specifier 不會截斷,反而會讓實際輸出字元數比預期多,打亂後續的字元數計算。
3. **忘記字元數是累加的,每次 %hn 都要算「增量」而不是「總量」**：第二次 `%hn` 要寫的是高 16 bits 的值,但實際輸出字元數早已包含第一次 `%hn` 之前累積的量,padding 公式必須算「還差多少才到目標值」,不能直接套用目標值本身。
4. **寬度指定符撐出的 payload 過長被截斷**:如果目標位址的值很大(例如高位元組接近 `0xFFFF`),可能需要撐出數萬字元的 padding,如果目標程式的接收 buffer 或 `printf` 本身對輸出長度有限制,整個 payload 會失敗。前面提到的「把較小值放前面寫」策略能緩解,但無法完全消除這個限制。
5. **改寫目標選錯導致程式永遠不會「使用」被改寫的值**:例如改寫了一個從來不會再被讀取執行的函式指標,或改寫的 return address 所在的那層函式呼叫根本不會真的返回(例如中途被其他邏輯劫持)。改寫前先用逆向(Part 4)確認這個位址之後確實會被讀取並用於控制流。

## 進階：再往深一層

- **一次 payload 同時完成 leak + write**：進階技巧是設計一個 format string,前段用 `%s` leak 出需要的位址,後段立刻用 leak 到的資訊算出的位址做 `%n` 寫入——這需要 `printf` 支援在同一次呼叫裡混用不同指定符對不同 slot 操作,理論上可行,但 payload 設計複雜度顯著提高。Ch 練習 F 的延伸挑戰會實際練這個。
- **CFG(Control Flow Guard)對函式指標改寫的衝擊**：現代 Windows 的 CFG 緩解機制會在間接呼叫(indirect call)前檢查目標位址是否為合法的函式入口,如果目標程式啟用了 CFG,改寫一個任意函式指標指向 shellcode 可能會被 CFG 擋下而不是成功跳轉。OSED 範圍內的題目通常不特別鋪陳 CFG,但在 Part 11(Ch 41)會補這塊現代緩解的衝擊。
- **用 `%hhn` 做更細粒度的單 byte 寫入**：如果某些情境下 2-byte 對齊的寫入會波及不該動的相鄰記憶體(例如目標位址旁邊緊接著另一個重要結構),改用 `%hhn` 以 1 byte 為單位寫,能更精確地只動你想動的那一塊,代價是需要寫 4 次而不是 2 次,payload 更長、更繁瑣。

## 本章重點整理

- `%n` 寫入的值是「目前累積已輸出字元數」,不是直接指定的數值——要控制寫入值,靠 width specifier(`%Nx`)撐大累積字元數。
- 直接撐出一個完整 4-byte 大數值不現實,實務上用 `%hn`(2 bytes)或 `%hhn`(1 byte)分段寫,每段寫入前重新計算「還差多少累積字元數才到目標值」。
- **全章建立在「目標已啟用 `%n`」的前提上**——Windows VC runtime 預設停用,需要目標呼叫 `_set_printf_count_output(1)`。沒有這個前提,整條任意寫思路無法執行。
- Windows PE 沒有 Linux 的 GOT,常見改寫目標改為:return address、SEH handler、函式指標、IAT 條目——IAT 條目在思路上最接近傳統 GOT 攻擊。
- 若目標同時有 overflow 漏洞可用,優先選 overflow——format string 任意寫依賴的前提(`%n` 啟用)在 Windows 上不是常態。
- payload 設計的核心難點是精確計算每一段的 padding 數量,務必在 WinDbg 裡逐步核對實際累積字元數。

## 自我檢核

- [ ] 能解釋為什麼 `%n` 寫入的是「字元數」而不是你指定的任意數值,以及 width specifier 怎麼間接控制這個數字
- [ ] 能說出 `%hn` 和 `%hhn` 分別寫入幾個 bytes,以及為什麼要分段寫而不是一次寫滿 4 bytes
- [ ] 能列出 Windows 上至少 3 個可作為 `%n` 改寫目標的選項,並說出各自的前提條件
- [ ] 能講出「如果目標同時有 overflow 和 format string 漏洞,該優先選哪個」以及原因
- [ ] 能重述本章反覆強調的前提——`%n` 在 Windows 上預設是關閉的

## 延伸閱讀

- **"Exploiting Format String Vulnerabilities"** — scut / team teso
  - **讀哪裡**：第 5–6 節,`%n` 任意寫與分段寫入(short write)技巧的原始推導。
  - **學什麼**：本章 `%hn`/`%hhn` 分段寫入公式的理論源頭,原文以 GOT 為改寫目標示範,對照本章 Windows 的 IAT 替代方案理解差異。
  - **前提**：Linux/glibc 背景,`%n` 預設啟用,讀時留意 Windows 需要額外前提。

- **[`_set_printf_count_output` — Microsoft Learn](https://learn.microsoft.com/en-us/cpp/c-runtime-library/reference/set-printf-count-output)**
  - **讀哪裡**：整頁,再次核對。
  - **學什麼**：本章反覆強調的前提的官方出處,務必在動筆設計任何任意寫 payload 之前再確認一次。
  - **前提**：無。

- **[Corelan — Exploit Writing Tutorial 系列](https://www.corelan.be/index.php/articles/)** — Peter Van Eeckhoutte
  - **讀哪裡**：系列目錄,搜尋 format string / `%n` 相關篇章的分段寫入公式推導部分。
  - **學什麼**：更嚴謹、步驟化的 padding 計算方法,補足本章示意腳本未覆蓋的邊界情況。
  - **前提**：先讀完本章與 Ch 35 的 offset 定位。

- **[PE Format — Microsoft Learn（Import Address Table 段落）](https://learn.microsoft.com/en-us/windows/win32/debug/pe-format)**
  - **讀哪裡**：Import Address Table(IAT)相關段落。
  - **學什麼**：IAT 條目在 PE 檔案裡的佈局與定位方式,支撐本章「改寫 IAT 條目」這個目標選項的技術細節。
  - **前提**：先讀過 Ch 6 PE 格式速成。

- **[Connor McGarr — Exploit Development blog](https://connormcgarr.github.io/)**
  - **讀哪裡**：站內搜尋 format string / arbitrary write 相關文章。
  - **學什麼**：現代 Windows exploit dev 視角對任意寫技巧的實戰示範與除錯過程記錄。
  - **前提**：先完成本章與 Ch 35。

下一章把這條完整鏈(定位 → leak → 任意寫)串成一個端到端的練習,從啟用 `%n` 的假設目標打出完整的控制流劫持。

→ [練習 F：format string → leak → 控制流](./practice-f-format-string-chain.md)
