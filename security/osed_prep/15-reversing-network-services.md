# Ch 15 — 逆向 Windows 網路服務：拆協定、找輸入點

> **目標**：學會在 IDA 裡從零定位一支 Windows TCP 服務的網路輸入點（socket API 呼叫鏈），順著資料流追到指令分派（command dispatch）邏輯，逆出它的「協定格式」——指令關鍵字、分隔符、參數結構——而不需要任何文件或原始碼。

> **環境**：Win10 x64 + IDA Free 9.x/IDA Pro。所有介面步驟與反組譯片段標**未實測，理論預期**；驗證法見文末。

## 為什麼需要這一章

OSED 的攻擊目標幾乎清一色是**自訂 TCP 網路服務**——不是本機程式,是一支你要連上去、送資料、等它處理的 daemon。vulnserver 是這類目標的教學代表,但考場給的是陌生、沒公開原始碼的 binary。你拿到的只有一支 `.exe`,不知道它聽哪個 port、收什麼格式的指令、每個指令對應什麼處理邏輯。

在你能"找漏洞"之前,必須先回答一個更基本的問題:**這支程式怎麼跟外界說話?** 這一章教的是從 binary 逆出協定的固定招式——幾乎每一支 Windows 網路服務的輸入路徑都遵循同一套 Winsock API 骨架,認出這套骨架,你就能在任何陌生目標裡快速定位「資料從外部進來、被解析、被分派給某段邏輯處理」的那條管線。

## 先建立直覺:socket → recv → parser → handler

不管協定多複雜,一支基於阻塞式（blocking）socket 的 TCP 服務,骨架幾乎都是這樣:

```
   網路 (攻擊機送 TCP 封包)
        │
        ▼
┌──────────────────┐
│ WSAStartup()       │  初始化 Winsock
│ socket()           │  建立 socket
│ bind()             │  綁定 port
│ listen()           │  開始聽
│ accept()           │  等客戶端連進來，回傳新的 connected socket
└─────────┬──────────┘
          │
          ▼
┌──────────────────┐
│  recv(sock, buf,    │  ← 資料真正從外部進來的那一行
│       len, 0)       │     buf 是個 stack 或 heap 上的緩衝區
└─────────┬──────────┘
          │  buf 內容現在是攻擊者完全控制的 bytes
          ▼
┌──────────────────┐
│  解析器 (parser)     │  通常是一連串 strcmp/strncmp/memcmp
│  比對開頭的指令關鍵字  │  "TRUN " / "GMON " / "HELP" ...
└─────────┬──────────┘
          │  比對到某個指令
          ▼
┌──────────────────┐
│  對應的 handler      │  真正處理這個指令的函式
│  可能把 buf 的某段     │  ← 漏洞常藏在這裡 (Ch 16)
│  複製到另一個緩衝區    │
└──────────────────┘
```

逆向的任務是把這張圖「反著畫出來」：從 binary 裡找到 `recv`,往下追資料流,找到 parser,找到每個 handler。這整條路徑只要你找到 `recv`,剩下大部分是**跟著資料走**,比從頭瞎逛函式列表快得多。

## 第一步：找 import,鎖定網路 API

Winsock（Windows Sockets）的函式都從 `ws2_32.dll` 匯入。在 IDA 的 **Imports window** 裡找這些名字（未實測，理論預期的典型清單）：

- `WSAStartup`：初始化,幾乎每支用網路的程式開頭都會呼叫一次,是個方便的「這支程式有網路功能」的訊號,但對找輸入點本身用處不大。
- `socket`：建立 socket 物件。
- `bind` / `listen`：伺服器端監聽用,`bind` 的參數裡常能找到 port 號（注意 port 用 `htons` 轉過網路位元序,16-bit 數字在反組譯裡可能不會直接以你熟悉的十進位出現）。
- `accept`：等待客戶端連線,回傳一個新的 socket handle 代表這個連線。
- **`recv`**：你最關心的函式。資料從外部真正進入程式的地方。

在 Imports window 雙擊 `recv`,IDA 跳到它的 import stub（一個小小的跳轉,實際函式在 `ws2_32.dll` 裡,不需要深入）。接著對這個 import 項目按 **`X`**（xref），列出**誰呼叫了 `recv`**。這個清單通常不長——一支服務可能只有一兩個地方真的在收資料（例如主連線迴圈裡的一次 `recv`,或是多執行緒 architecture 下每個 worker thread 各有一次）。

## 第二步:從 recv 呼叫處往下追資料流

找到呼叫 `recv` 的位置,看它的三個關鍵參數(x86 cdecl/stdcall 下,`call` 前堆疊上由後往前推的順序,配合 Ch 3 的呼叫慣例):

```
; 未實測，理論預期示意
push 0              ; flags
push 400h           ; len  ← 緩衝區大小上限,這個數字很重要,記下來
push edi            ; buf  ← 指向接收緩衝區的指標,通常是 stack 上的區域變數
push esi            ; socket handle
call ds:recv
```

`buf` 參數指向的記憶體——通常是呼叫函式裡用 `sub esp, 0x400` 或類似指令配置出的一塊 stack 緩衝區，或是 `malloc`/`HeapAlloc` 來的 heap 緩衝區——就是你要盯著的東西。`recv` 執行完,這塊記憶體裡裝的就是攻擊者從網路送進來的原始 bytes。

接下來的邏輯幾乎必然是個**迴圈**：很多服務不是一次 `recv` 就處理完,而是用迴圈持續收資料直到看到某個結束符（例如 `\r\n`）或緩衝區滿。在 IDA 的 graph view 裡,留意 `recv` 所在的基本區塊是否被一個迴圈回頭的箭頭包住——如果有,說明這是個「持續收資料直到滿足條件」的模式,值得留意迴圈結束條件有沒有做好邊界檢查（這點 Ch 16 會深入）。

收完資料後,程式通常會把 `buf` 的指標傳給下一個函式——這是你順著資料流往下追的點。在反組譯裡看哪個函式把 `buf`（或它的副本）當參數傳出去,雙擊跳進那個函式,那裡大概就是 parser。

## 第三步:辨識指令分派(command dispatch)

vulnserver 風格的服務（以及很多考場目標模仿的設計）把協定設計成「一行文字指令 + 空白 + 參數」,像 `TRUN .AAAA...` 或 `GMON /AAAA...`。Parser 的任務就是看這一行開頭是哪個關鍵字,分派給對應的 handler。組語裡這段邏輯的典型樣貌（未實測,理論預期）：

```
; 示意:一連串字串比對做 command dispatch
mov  eax, [ebp+buf_ptr]
push offset aTrun          ; "TRUN "
push eax
call strncmp
test eax, eax
jnz  short check_next_cmd
call handle_TRUN            ; 比對成功，分派給 TRUN 的 handler
jmp  dispatch_done

check_next_cmd:
mov  eax, [ebp+buf_ptr]
push offset aGmon           ; "GMON "
push eax
call strncmp
test eax, eax
jnz  short check_next_cmd2
call handle_GMON
jmp  dispatch_done
...
```

認出這個模式的訣竅:**一長串結構相似的區塊,每個都是「字串比較 + 條件跳轉 + call 某個函式」**。這是 `strcmp`/`strncmp`/`memcmp` 做 dispatch 的指紋——即使沒有符號、沒有字串常數方便你直接搜（有些 binary 會把指令關鍵字加密或動態組出來,增加逆向難度,但 OSED 考場目標通常不會做到這麼狠）,看到這個結構就能推出「這是個指令分派表」。

最快找到這整段邏輯的路徑,往往是反過來:先去 **Strings window**（`Shift+F12`）掃一遍,找看起來像指令關鍵字的字串（全大寫、短、後面常帶空白,例如 `"TRUN "`），對它按 `X` 看誰引用了它,直接跳進 dispatch 函式,不需要從 `recv` 一路線性往下讀。兩個方向（從 `recv` 往下、從字串往上）互相印證,比單靠一條路徑快且可靠。

## 逆出協定格式

逆完 dispatch 段落,你應該能寫出一份**協定規格**,類似:

```
格式: <COMMAND><space><參數>\r\n

已知指令:
  TRUN <任意長度字串>   → handle_TRUN
  GMON <任意長度字串>   → handle_GMON
  ... (繼續列出 dispatch 裡每個分支對應的關鍵字)

未知/待查:
  收到不認得的關鍵字時,程式似乎回覆 "不認識此指令" 然後繼續收下一行
  （這段行為需要動態驗證，Ch 17）
```

這份規格就是你後續 fuzz 或手工建構 payload 的依據——不再是盲目往服務塞 `b"A"*5000`,而是知道「我要送 `TRUN ` 加上一串垂圾字串才會進到 `handle_TRUN` 這個特定 handler」。協定逆得越準,Part 4 剩下的工作越有方向。

## 底層機制：為什麼這套骨架這麼穩定

這不是巧合——Winsock API 本身的設計就逼出這個形狀。阻塞式 socket 的 `recv` 呼叫**必須**把資料放進呼叫者事先配置好的緩衝區（`recv` 不像某些高階語言的 stream API 會幫你動態配置），所以緩衝區大小、位置、誰配置的,全部能從呼叫 `recv` 前的程式碼直接讀出來,不用猜。而「文字行指令協定」是 C 語言寫網路服務最省事的設計（比起自訂二進位協定,不需要額外寫 pack/unpack 邏輯）,所以教學用的靶程式和很多真實的輕量服務都長這樣。這也是為什麼 OSED 的目標程式即使换了名字、换了指令字串,骨架往往換湯不換藥。

## 對比取捨:兩種找輸入點的路徑

| 路徑 | 做法 | 優點 | 缺點 |
|---|---|---|---|
| 從 `recv` 的 import 往下追 | Imports → `recv` → `X` → 順著 buf 參數往下讀 | 保證找到真正的資料入口,不會漏 | 如果 dispatch 邏輯離 `recv` 很遠（中間套了幾層函式),路徑較長 |
| 從 Strings 的指令關鍵字往上追 | Strings window 掃可疑字串 → `X` → 跳進使用處 | 通常直接命中 dispatch 核心,速度快 | 依賴字串明文存在；如果協定不是文字指令（純二進位協定),這條路走不通 |

實務上兩條路都走一次,互相驗證——如果從 `recv` 往下追到的函式,恰好也是 Strings 的某個指令字串被引用的地方,你就有很高信心確認找對了 dispatch 位置。

## 踩雷集錦

1. **錯誤直覺：只要看到 `recv` 就代表找到漏洞點** → **正確**：`recv` 只是資料的入口,漏洞通常在資料被**複製**到別的（更小的）緩衝區時發生,那是下一章的範疇。找到 `recv` 只是起點。
2. **錯誤直覺：協定一定是文字行指令（像 vulnserver）** → **正確**：這是教學靶常見的設計,但真實或較硬的考場目標可能用二進位協定（固定長度 header + 長度欄位 + payload）,這時 Strings window 找不到明顯關鍵字,要改用「找 `recv` 之後第一個讀取固定 offset 的欄位」的方式逆,邏輯類似但沒有字串可以當捷徑。
3. **錯誤直覺：`strcmp`/`strncmp`/`memcmp` 在反組譯裡一定長得一樣** → **正確**：編譯器可能把短字串比較展開成直接的位元組比較（`cmp byte ptr [eax], 54h` 這種逐字元比,而不是呼叫 `strcmp`),尤其開了最佳化的 Release build。這種情況下 dispatch 邏輯不會呼叫任何「字串函式」,但本質還是同一種分派模式,看到一串逐字元 `cmp`+跳轉也要認得出來。
4. **錯誤直覺：一次 `recv` 呼叫就收完一個完整指令** → **正確**：TCP 是串流協定,一次 `recv` 可能只收到半個指令,也可能一次收到好幾個指令黏在一起。服務端常常要自己維護一個累積緩衝區,在裡面找 `\r\n` 當指令邊界。這個累積邏輯本身有時就是漏洞來源（邊界算錯）。
5. **錯誤直覺：找到 dispatch 就等於逆完協定** → **正確**：dispatch 只告訴你「關鍵字對應哪個 handler」,每個 handler 內部怎麼解析參數（固定分隔符?可變長度?有沒有子指令?）還要分別逆,這是下一章要做的事。

## 進階：再往深一層

- **多執行緒服務**：有些服務對每個連線開一條 thread（`CreateThread` 包住 `accept` 後的處理邏輯）。這種情況下在 Imports 找 `CreateThread`,它的參數（thread 函式指標）往往直接指向真正處理連線的主邏輯,比自己從 `accept` 往下追更快。
- **非阻塞/IOCP 架構**：效能導向的服務可能用 overlapped I/O 或 IOCP（I/O Completion Port），資料流不會長得像本章這麼直線,`recv` 呼叫和實際處理邏輯可能分散在不同 callback 裡。OSED 考場目標通常不會搞這麼複雜,但真實世界常見,遇到時心裡要有這個可能性。

## 本章重點整理

- 逆向網路服務輸入點的固定骨架：`socket`/`bind`/`listen`/`accept` → **`recv`**（真正的資料入口） → parser → handler。
- 兩條定位路徑：從 Imports 的 `recv` 往下追資料流;從 Strings 的指令關鍵字往上追引用處。兩條互相驗證。
- Command dispatch 的指紋：一連串結構相似的「字串比較 + 條件跳轉 + call handler」區塊,即使最佳化後變成逐位元組 `cmp`,邏輯本質不變。
- 逆完這一章,產出物應該是一份**協定規格**：指令關鍵字、分隔符、參數格式——這是後續找漏洞與建構 payload 的地圖。

## 自我檢核

- [ ] 能說出一支典型 Winsock TCP 服務從啟動到收資料的 API 呼叫順序
- [ ] 能解釋為什麼找到呼叫 `recv` 的位置,比在函式列表裡亂逛更快定位輸入點
- [ ] 能描述 command dispatch 在反組譯裡的典型模式,包括最佳化後可能的變形
- [ ] 能說出「從 recv 往下追」和「從字串往上追」兩條路徑各自的優缺點
- [ ] 能解釋為什麼一次 `recv` 不保證收完一個完整指令,這對逆向累積緩衝區邏輯有什麼影響

## 延伸閱讀

- **[Microsoft Learn — Winsock 函式參考（recv/accept/listen）](https://learn.microsoft.com/en-us/windows/win32/api/winsock2/)**
  - **讀哪裡**：`recv`、`accept`、`WSAStartup` 各自的參數說明。
  - **學什麼**：每個 API 的參數語意是逆向時讀懂呼叫處堆疊佈局的權威依據。
  - **前提**：無。

- **[vulnserver 原始碼（GitHub）— Stephen Bradshaw](https://github.com/stephenbradshaw/vulnserver)**
  - **讀哪裡**：`vulnserver.c` 的主迴圈與指令比對段落。
  - **學什麼**：拿到原始碼後,對照你自己在 IDA 裡逆出的結果,驗證本章教的方法是否真的能逆出同樣的邏輯——這是最好的自我驗證素材,因為答案已知。
  - **前提**：已完成 Ch 14 的 IDA 基本操作。

- **[Corelan — Exploit Writing Tutorial part 1](https://www.corelan.be/index.php/2009/07/19/exploit-writing-tutorial-part-1-stack-based-overflows/)**
  - **讀哪裡**：vulnserver 介紹與指令列表段落。
  - **學什麼**：對照已知的 vulnserver 指令清單,驗證你逆出的 dispatch 是否完整。
  - **前提**：無。

- **[OffSec EXP-301 課程教材 — 逆向網路服務章節]**
  - **讀哪裡**：官方教材處理陌生二進位目標的模組。
  - **學什麼**：考場貼近的逆向流程範例,和本章對齊但用的是官方自製靶。
  - **前提**：已有 OffSec 帳號存取課程教材。

協定逆出來後,下一步是在每個 handler 裡找出真正的漏洞樣式——哪個 unsafe 複製、哪個長度檢查缺陷,讓你能從「看懂邏輯」進到「找到洞」。

→ [Ch 16 從反組譯辨識 bug：unsafe copy、長度檢查缺陷](./16-identifying-bugs-in-disasm.md)
