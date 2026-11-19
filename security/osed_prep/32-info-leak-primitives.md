# Ch 32 — info leak 製造：部分覆寫、可預測位址

> **目標**：當 Ch 31 的捷徑不存在——所有相關模組都乾乾淨淨開著 ASLR——你必須在執行期洩漏（leak）一個真實位址，再用它反推模組基址。這一章帶你過一遍常見的 leak 來源（format string、未初始化記憶體、把指標回顯給 client 的功能）、怎麼從一個 leak 到的位址算出整個模組的基址與 gadget 位址，以及部分覆寫（partial overwrite）這個「不需要完整 leak」的替代手段與它的限制。
>
> **環境**：Win10 x64 + WinDbg（2026）+ mona v3 + Python3

---

## 為什麼需要這個？

Ch 31 的前提是「運氣好，有一個模組忘了開 ASLR」。OSED 的題目不會每次都留這個洞——很多靶機把所有模組的 `/DYNAMICBASE` 都開好了，`!py mona modules` 跑出來一片 `ASLR: True`。這時候你寫死在 payload 裡的任何位址，下一次開機就全部失效。

ASLR 的根本假設是「攻擊者不知道位址」。**Info leak 直接打穿這個假設**：只要你能讓程式告訴你一個它自己才知道的執行期位址（不管是模組裡某個函式的位址、還是 stack 上某個殘留的指標），你就能反推出整個模組的基址，接下來所有 gadget 位址都變成「已知基址 + 已知 RVA」的加法題。這是繞過全開 ASLR 系統的通用路線，比暴力猜測可靠，也是 Ch 33 要用的最後一塊拼圖。

## 先建立直覺：一個 leak 怎麼變成整個模組的地圖

```
            靜態分析階段（在你自己機器上，用 IDA 開同版本的目標 DLL）
            ───────────────────────────────────────────────────────
            找到：某個會被 leak 出來的函式 F 的 RVA = 0x12AB0
            找到：你要用的 ROP gadget G 的 RVA     = 0x3F204

                                  │
                                  ▼
            執行期階段（對著真正的靶機）
            ───────────────────────────────────────────────────────
            觸發 leak 原語 → 收到回應 → 解析出 leaked_addr
                （leaked_addr 恰好是函式 F 在這次開機裡的實際位址）

                                  │
                                  ▼
            module_base = leaked_addr − 0x12AB0     ← 減去已知 RVA
                                  │
                                  ▼
            gadget_addr = module_base + 0x3F204     ← 加上目標 gadget 的 RVA
                                  │
                                  ▼
            把 gadget_addr 填進 ROP chain payload，送出
```

一句話總結這一整章：**leak 只需要洩漏一個位址，但你能用它算出的位址是整個模組的任何地方**——前提是你靜態分析時用的是跟靶機上完全相同版本的模組。這個「前提」正是 Ch 33 要特別強調的常見失敗點。

## Leak 來源一：format string（本課 Part 9 預告）

Format string 漏洞（`printf(user_input)` 這類）在 Windows 上比 Linux 少見（Windows 程式更常用 `StringCchPrintf` 等 SafeStr API），但一旦出現，是最直接的 leak 原語——`%x %x %x ...` 或 `%p %p %p ...` 直接把 stack 上的內容印出來，裡面常常藏著呼叫者的 return address（指向某個模組的 `.text` 段）。

```
送入: "%p.%p.%p.%p.%p.%p.%p.%p"
回應: "0012FF80.00401234.77C91234.0012FFA0.........."
                     ^^^^^^^^
                     這個值落在 target.exe 的 .text 範圍內，
                     是某次函式呼叫留在 stack 上的 return address
```

這條路線的完整機制留給 Part 9（Ch 34–36）細講，這裡先記住它在 leak 原語裡的位置：**它一次能印出一整排 stack 內容，是最省力的多重 leak 來源**，前提是漏洞本身存在。

## Leak 來源二：未初始化記憶體回傳

程式分配了一塊緩衝區（stack 上或 heap 上），沒有清零，就把它的內容回傳給 client。如果這塊記憶體之前存過某個指標（例如上一次呼叫留下的函式指標、物件指標），那個值還殘留在裡面。

```c
// 示意漏洞模式
char buf[256];                  // 沒有 memset(buf, 0, sizeof(buf))
// buf 裡可能殘留著上一次處理請求時寫入的資料，
// 包括可能的函式指標、模組內某段程式碼的位址
sprintf(buf, "status: %d", some_code);   // 只蓋了前面幾個 bytes
send(client_sock, buf, sizeof(buf), 0);  // 把整個 256 bytes 都送出去，含殘留資料
```

**判斷方法**：如果某個回應欄位的長度固定、但你的輸入只填滿了一部分，多送幾次觀察尾端的殘留 bytes 是否看起來像一個合理的位址（例如落在 `0x00400000`～`0x7FFFFFFF` 之間，且低位元組符合某個已知模組的對齊特徵）。

## Leak 來源三：把 stack / 模組指標回顯給 client 的功能

有些服務會刻意把內部狀態「回報」出來做除錯或診斷用，這種功能常常是現成的 leak 原語，不需要觸發任何記憶體錯誤：

- **錯誤訊息裡夾帶位址**：某些服務在例外處理或錯誤回報路徑會把 `GetLastError()`、exception address、或內部 handle 值印進回應訊息。
- **診斷/狀態指令**：協定裡若有類似 `STATUS` / `DEBUG` / `INFO` 的指令，有時會印出內部指標或版本相關的位址資訊。
- **Banner / 版本回應裡的 build 資訊間接提示**：雖然不是直接的位址 leak，但能幫你確認目標模組版本，讓你知道該用哪一份靜態分析結果對應的 RVA。

這類「功能性」leak 的好處是：**不需要先找到一個記憶體安全漏洞**，只要協定本身就有這個出口。OSED 題目裡，逆向分析服務的每一個指令分支（Part 4 的工作）常常就是在找這種被忽略的回顯路徑。

## Leak 來源四：讀原語（arbitrary / bounded read）

如果你已經有一個獨立於主 overflow 漏洞之外的「讀」能力（例如某個指令可以指定 offset 讀回一段資料），直接用它去讀你猜測的、可能含有指標的記憶體區域（例如某個全域變數表、某個物件陣列）。這和 Ch 32 對應的 windows_pwn 教材（見延伸閱讀）裡講的 OOB read 本質相同，只是 OSED 的題目通常規模較小、協定較簡單，常常是「某個指令的參數沒做邊界檢查」這種直接形式。

## 核心：怎麼從 leak 到的位址算出模組基址

### 流程

```
Step 1：靜態分析，確定「被 leak 出來的位址」對應哪個 symbol
────────────────────────────────────────────────────────────
  用 IDA 開目標模組（和靶機上同版本！），找到會出現在
  leak 回應裡的那個位址對應哪個函式/資料，記下它的 RVA。

Step 2：執行期觸發 leak，拿到 leaked_addr
────────────────────────────────────────────────────────────
  送出會觸發 leak 的 payload，解析回應取出位址值
  （注意 little-endian 的 byte 順序，解析時用 struct.unpack("<I", ...)）

Step 3：算模組基址
────────────────────────────────────────────────────────────
  module_base = leaked_addr - known_rva

Step 4：算目標 gadget 位址
────────────────────────────────────────────────────────────
  gadget_addr = module_base + gadget_rva

Step 5：驗算（alignment sanity check）
────────────────────────────────────────────────────────────
  x86 模組的載入通常對齊到 0x10000（64KB），
  module_base % 0x10000 應該是 0——不是的話，RVA 很可能算錯了
```

### Python 示意：leak → 算基址 → 算 gadget

```python
#!/usr/bin/env python3
"""
未實測，理論預期——leak_payload 的格式、leaked_addr 在回應裡的解析方式，
以及下面的 RVA 數字，都因實際靶機而異，需要在你自己的環境用 WinDbg + IDA 實測取得
"""
import socket
import struct

target_ip, target_port = "192.168.x.x", 9999

# --- 這兩個 RVA 都來自靜態分析目標模組（IDA），不是猜的 ---
LEAKED_FUNC_RVA = 0x12AB0      # 會被 leak 出來的那個函式/符號在模組裡的 RVA
GADGET_RVA      = 0x3F204      # 目標 ROP gadget（例如 pop eax; ret）的 RVA

def p32(v: int) -> bytes:
    return struct.pack("<I", v)

def recv_leak(sock: socket.socket) -> int:
    """送出觸發 leak 的 payload，解析回應取出洩漏的位址"""
    sock.send(b"LEAKCMD trigger\r\n")      # 示意指令，依實際協定替換
    resp = sock.recv(4096)
    # 假設洩漏的位址以 little-endian 4 bytes 出現在回應固定 offset
    leaked_addr = struct.unpack("<I", resp[8:12])[0]
    return leaked_addr

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.connect((target_ip, target_port))

leaked_addr = recv_leak(s)
module_base = leaked_addr - LEAKED_FUNC_RVA

if module_base % 0x10000 != 0:
    print(f"[!] module_base 0x{module_base:08X} 沒有對齊到 0x10000，RVA 可能算錯")
else:
    gadget_addr = module_base + GADGET_RVA
    print(f"[+] leaked_addr  = 0x{leaked_addr:08X}")
    print(f"[+] module_base  = 0x{module_base:08X}")
    print(f"[+] gadget_addr  = 0x{gadget_addr:08X}")

s.close()
```

> **未實測，理論預期**：上面的協定格式、offset、RVA 都是示意值。**驗證法**：在 WinDbg 裡對目標模組跑 `lm`（list modules）看實際載入基址，用 `!py mona modules` 確認它的 `ASLR` 欄是 `True`（代表每次開機真的會變，需要走本章流程），然後觸發 leak 後用 `.printf` 或直接在 WinDbg 裡檢視收到的回應 bytes，核對你 Python 腳本解析出來的 `leaked_addr` 是否真的落在該模組的位址範圍內（`lm` 顯示的 base ~ base+size 之間）。

## Partial overwrite（部分覆寫）：不需要完整 leak 的替代手段

有時候你根本不需要 leak 出完整的位址——**只需要覆寫一個已存在、原本正確的指標的最低 1~2 bytes**，利用 ASLR 不會隨機化「頁內低位元組」的特性，讓它跳到附近的其他位址。

### 為什麼這招能繞過 ASLR

x86 Windows 的模組載入對齊單位是 **64KB（0x10000）**，也就是模組基址的低 16 bits（準確說是低於對齊粒度的部分）永遠是固定值（通常是 `0x0000`），**ASLR 隨機化的是基址的高位部分**。一個指向模組內某處的指標，它的低幾個 bytes（在對齊粒度範圍內的 offset）即使 ASLR 隨機化了高位，低位元組所表示的「模組內相對位置」概念上仍然有規律可循——但要小心：這裡的「低位不變」指的是**頁內 offset 的性質**，不是「指標的低 byte 數值一定不變」。嚴格地說，如果一個指向 `module_base + 0x234AB` 的指標因為 ASLR 讓 `module_base` 整個變了，低位 byte 的具體數值仍然會隨 `0x234AB` 在新 base 下的位置而定；部分覆寫能用的真正原因是——**你不需要知道完整位址，只需要讓一個已經正確、已經指向該模組某處的指標，透過覆寫它的最低 1~2 bytes，偏移到同一個模組內你想要的另一個位置**。

```
原本（正確、未被覆寫的）指標：  0xAA BB 34 AB     ← 指向 module_base + 0x234AB
                                 (這個指標的高 3 bytes 是當次開機的 ASLR 隨機值，
                                  但它已經「存在」在記憶體裡，你不需要知道它的值)

overflow 只蓋最低 1 byte：
  payload 結尾寫入：            0xC4
  結果變成：                    0xAA BB 34 C4     ← 高 3 bytes 保留（還是當次的隨機值）
                                                    低 1 byte 改成攻擊者選的值
  新指向：                      module_base + 0x234C4   ← 同一模組內，往後/往前偏移了 0x19 bytes
```

**關鍵點**：你完全不需要知道 `module_base` 的真實數值——overflow 覆寫發生在記憶體裡「原本已經有一個正確指標」的那個位置，高位 bytes 原封不動（因為你的漏洞只能蓋到最低幾個 bytes），所以新位址依然落在同一個模組範圍內，只是模組內的相對位置（RVA）變了。

### 條件與限制

1. **必須有一個「只影響低幾位 bytes」的覆寫原語**：典型場景是 off-by-one（只蓋了 1 byte）、或者字串處理遇到 null byte 提前截斷覆寫範圍。這不是你能自由選擇的，取決於原始漏洞的形狀。
2. **目標必須有「附近正好有用的 gadget」這個運氣成分**：1 byte 覆寫只給你 256 個可能的新偏移（`0x00`~`0xFF`），2 bytes 給你 65536 個。能不能在這個範圍內找到一個可用的 gadget 或 `jmp`/`ret` sled，取決於該模組程式碼密度與你要的 gadget 種類。
3. **對 x86 比 x64 更實用**：x86 整體位址空間小、ROP gadget 密度相對高，256 個可能偏移裡找到有用 gadget的機率比 x64 高。
4. **它繞過的是「隨機化」，不是「不存在原本的指標」**：部分覆寫的前提是記憶體裡**本來就有一個指向目標模組的合法指標**（例如 saved return address、一個函式指標欄位），你只是調整它指向同模組的別處。如果這個位置原本是垃圾值或指向別的模組，部分覆寫沒有意義。

### 風險

部分覆寫本質是「有限範圍的瞎猜」，不保證一次成功：如果新偏移落在一段無效指令或會造成程式進一步崩潰的位址，你只是把一次可控的 crash 換成一次不可控的 crash。穩健的做法是先在自己環境窮舉 256（或 65536）個可能偏移，用 WinDbg 腳本自動化逐一嘗試，記錄哪些偏移能成功落地，而不是憑感覺挑一個數字就上靶機。

## 底層機制：leak → 算基址 → 定位 gadget 的完整資料流

```
┌────────────────────────────────────────────────────────────────┐
│  階段 1：漏洞 / 功能觸發                                         │
│  - format string ×N / 未初始化記憶體回傳 / 診斷功能回顯          │
│  - 讀原語（OOB read / 指定 offset 讀取）                         │
└───────────────────────────┬──────────────────────────────────────┘
                            ↓ 收到一段 bytes，裡面藏著一個位址
┌────────────────────────────────────────────────────────────────┐
│  階段 2：解析與識別                                              │
│  - 從回應裡用正確的 offset / little-endian 解析出 leaked_addr   │
│  - 判斷這個位址落在哪個模組的範圍（靠事先的靜態分析 + 粗略範圍猜測）│
└───────────────────────────┬──────────────────────────────────────┘
                            ↓ 已知 leaked_addr 對應的 symbol RVA
┌────────────────────────────────────────────────────────────────┐
│  階段 3：位址計算                                                │
│  module_base = leaked_addr - known_rva                          │
│  gadget_addr  = module_base + gadget_rva                         │
│  （alignment check：module_base % 0x10000 == 0）                  │
└───────────────────────────┬──────────────────────────────────────┘
                            ↓ 位址已確定
┌────────────────────────────────────────────────────────────────┐
│  階段 4：組裝第二階 payload                                      │
│  - 把算好的 gadget_addr 填進 ROP chain（Ch 33 接手）              │
│  - 觸發真正的 overflow，送出完整 exploit                         │
└────────────────────────────────────────────────────────────────┘
```

## 對比取捨：三種 leak 原語的使用場景

| 原語 | 能洩漏什麼 | 可靠性 | 需要的前提 |
|---|---|---|---|
| **format string** | stack 上一整排值（可能含多個模組的 return address） | 高（一次性洩漏多個候選值） | 存在格式字串漏洞（Part 9） |
| **未初始化記憶體回傳** | 殘留在緩衝區裡的舊指標/函式位址 | 中（取決於程式是否真的不清零、殘留值是否剛好是有用指標） | 緩衝區分配邏輯沒清零、且有管道把它整塊回傳 |
| **功能性回顯（診斷/錯誤訊息）** | 視功能設計而定，常是單一位址 | 高（不依賴記憶體錯誤，是設計出來的管道） | 協定本身存在這類指令/路徑 |
| **部分覆寫** | 不算「洩漏」，是重定向既有指標 | 低～中（有限範圍內嘗試，不保證一次成功） | 原始漏洞只能覆寫低位 bytes |

## 踩雷集錦

1. **以為 leak 到的位址就是模組基址本身**：幾乎不會這麼巧。你 leak 到的通常是模組裡**某個函式或資料的實際位址**，要減去它的 RVA 才拿到基址。直接把 leak 到的值當基址去加別的 RVA，算出來的 gadget 位址會整體偏移一個固定量，100% 錯。

2. **靜態分析用錯版本的模組**：你在自己機器上用 IDA 分析的 DLL，必須和靶機上實際跑的版本**完全一致**（同一個 build）。不同版本（甚至同版本不同 patch）的函式位置可能不同，RVA 對不上，算出來的「gadget 位址」會指向別的地方，造成不可預期的 crash。對齊檔案大小、版本資訊字串、或 hash 是最基本的核對步驟。

3. **忽略 little-endian 解析順序**：從回應 bytes 裡取出位址時，如果用 `struct.unpack(">I", ...)`（big-endian）而不是 `"<I"`，算出來的值會完全是另一個數字，表面上「看起來像一個位址」但其實是 byte 順序顛倒後的垃圾。

4. **部分覆寫時忘了 alignment/粒度檢查**：覆寫 1 byte 得到的新位址如果落在一個指令的中間（不是一個合法的指令起始位置），CPU 執行時會解出完全不同、不可預期的指令序列。找到的「可用偏移」必須是確認過的、對齊到有效指令起始點的位置,不能只看數值合理就假設可用。

5. **以為一次 leak 永久有效**：Windows image ASLR 是 per-boot 固定，不是每次連線都重新隨機——理論上同一次開機內 leak 一次就能重複使用。但如果目標服務崩潰後整台機器被自動重啟（不只是服務重啟，而是整機 reboot），ASLR 會重新洗牌，你的 exploit 腳本必須每次執行都重新走一次 leak 流程，不能把上次 leak 到的位址硬編到 payload 裡當作永久常數。

## 進階：再往深一層

- **連鎖 leak（leak 鏈）**：有時候一次 leak 只能拿到一個不夠直接有用的位址（例如 heap 上某物件的位址），但透過這個位址可以再讀一次（如果有讀原語）拿到第二個、更有用的位址（例如該物件的函式指標欄位，指向某個模組）。這種「用一個 leak 解鎖下一個 leak」的鏈式思考，在複雜的 OSED 題目或更進階的瀏覽器/應用程式 exploit 裡很常見，windows_pwn Ch 31 有更完整的連鎖案例。
- **IAT（Import Address Table）作為間接 leak 來源**：如果你能讀到目標模組的 IAT（在 `.rdata` 段，裡面存著被 import 的函式在它們各自模組裡的實際位址），讀到任何一筆 IAT 條目就等於 leak 出了**另一個模組**（通常是 `kernel32.dll`）的基址——因為 `module_base_kernel32 = iat_entry_value - known_function_rva_in_kernel32`。這是「用一個模組的洩漏，順帶算出另一個模組的基址」的經典技巧。
- **為什麼找 leak 比找 overflow 更考驗逆向功夫**：overflow 通常在崩潰時就自報身分（EIP 變成 `41414141`）；leak 原語往往藏在正常路徑裡，不會讓程式崩潰,必須透過 Part 4 的逆向方法論（拆協定、追蹤每個欄位怎麼被處理）主動找出來。這也是為什麼 OSED 把逆向列為核心能力而不是加分項。

## 本章重點整理

- 當所有相關模組都開 ASLR，必須先 leak 一個執行期位址，再用 `module_base = leaked_addr - known_rva` 算出基址，之後 `gadget_addr = module_base + gadget_rva`。
- 常見 leak 來源：format string（Part 9）、未初始化記憶體回傳、把內部指標/位址回顯給 client 的功能性路徑、讀原語（OOB/bounded read）。
- Partial overwrite 是另一條不需要完整 leak 的路線：利用 ASLR 不隨機化對齊粒度內低位 bytes 的特性，覆寫既有指標的最低 1~2 bytes，讓它偏移到同模組內的別處；代價是只能在有限範圍（256 或 65536 個可能值）裡嘗試，不保證命中。
- 整條資料流：觸發 leak → 解析回應取出位址 → 靜態分析對應 RVA → 算基址 → 算 gadget 位址 → 餵進 Ch 33 的 ROP chain。
- 最常見失敗點是版本不符（RVA 算錯）、byte 順序解析錯，以及混淆「leak 到的值」和「模組基址」兩個不同的東西。

## 自我檢核

- [ ] 能完整說出「leak 到一個值」到「算出某個 gadget 位址」中間的每一步，以及每一步各需要什麼前提知識
- [ ] 能解釋為什麼部分覆寫不需要知道模組基址的真實數值,而且能說出它成立的前提（原本就有一個正確指標存在）
- [ ] 能列舉至少三種 Windows 服務上可能出現的 leak 原語，並說出各自的可靠性差異
- [ ] 能解釋為什麼「靜態分析用錯版本模組」是本章最容易踩的雷，以及怎麼避免
- [ ] 能說出部分覆寫在 x86 vs x64 上可行性的差異原因

## 延伸閱讀

- **[windows_pwn Ch 31 — info leak 原語大全](../windows_pwn/31-info-leak-primitives.md)**
  - 讀哪裡：「資料流圖：info leak 如何餵給後續步驟」與「位址計算：從洩漏到有用位址」兩節。
  - 學什麼：同一套「leak → 算基址 → 算 gadget」資料流在更複雜場景（C++ vtable、heap grooming 配合 OOB read）下的延伸版本,以及連鎖洩漏的完整案例。
  - 前提：本章讀完後對照,會發現核心公式（`base = leaked - rva`）完全相同，差異只在 leak 原語的複雜度。
- **[windows_pwn Ch 24 — ASLR：Windows 特性 / leak / 部分覆寫](../windows_pwn/24-aslr.md)**
  - 讀哪裡：「路線 1：Info Leak」與「路線 2：部分覆寫」兩節。
  - 學什麼：部分覆寫在 x64 下成功率更低的量化說明，以及 info leak 與 x86 暴力路線的對照取捨表。
  - 前提：Ch 31（本課）讀完後對照。
- **Connor McGarr — "ASLR Bypass with Information Leaks"**（[connormcgarr.github.io](https://connormcgarr.github.io/)）
  - 讀哪裡：站內搜尋 ASLR bypass 系列，特別是從漏洞觸發到模組基址計算的完整程式碼示範。
  - 學什麼：現代工具鏈下實際的 leak → 計算 → ROP 整合流程，是本章示意程式碼的更完整真實版本。
  - 前提：本章全部讀完。
- **[Microsoft Learn — Win32 API: `GetModuleHandle` / IMAGE_OPTIONAL_HEADER](https://learn.microsoft.com/en-us/windows/win32/)**
  - 讀哪裡：`IMAGE_OPTIONAL_HEADER` 結構裡 `ImageBase` 欄位的說明，以及模組對齊（`SectionAlignment`/`FileAlignment`）的定義。
  - 學什麼：本章「module_base 應該對齊 0x10000」檢查背後的 PE 格式依據。
  - 前提：Ch 6（PE 格式速成）讀過更順。
- **Corelan — "Exploit writing tutorial part 6: Bypassing Stack Cookies, SafeSEH, SEHOP, HW DEP and ASLR"**（[corelan.be](https://www.corelan.be/index.php/2009/09/21/exploit-writing-tutorial-part-6-bypassing-stack-cookies-safeseh-hw-dep-and-aslr/)）
  - 讀哪裡：「Bypassing ASLR」一節裡討論 partial overwrite 的部分。
  - 學什麼：partial overwrite 技法的原始出處與早期實作範例。
  - 前提：本章「Partial overwrite」一節讀完後對照。

有了算出來的 gadget 位址，接下來就是把它們餵進 ROP chain——這也是整門課最終要收束的那條鏈：Part 7 的 DEP bypass 加上本章的 ASLR bypass，合起來才是一個現代、完整的 Windows exploit。下一章把兩邊接起來，走一遍兩階段互動的完整骨架。

→ [Ch 33 — ASLR + DEP 合體完整鏈](./33-aslr-dep-full-chain.md)
