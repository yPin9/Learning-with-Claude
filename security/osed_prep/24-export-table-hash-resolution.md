# Ch 24 — export table 解析 + hash API resolution

> **目標**：接續 Ch 23 定位到的 kernel32 基址，搞懂如何解析它的匯出表（EAT）把 API 的實際位址挖出來，以及為什麼實務上用「函式名 hash」而非明文字串來比對。重點是看懂 EAT 三張平行陣列的關係與查找流程，並能用 WinDbg 驗證。逐行組語請對照延伸閱讀自行實作。

> **環境**：Win10 x64 22H2，偵錯 32-bit 行程，WinDbg（2026）。offset/結構為 x86（32-bit）。

> **本章定位**：EAT 解析與 API hashing 是 Windows 內部機制，攻擊面（shellcode 自力找 API）與防禦面（偵測不透過 GetProcAddress 的可疑解析）共用。本課在自有授權實驗室裡用 WinDbg 觀察 EAT 建立理解；可直接組譯的 shellcode 清單以權威公開教材為準（延伸閱讀）。

## 為什麼要自己解析匯出表？

Ch 23 讓你拿到 kernel32 的基址。但基址只是「這個 DLL 從哪開始」，你還不知道 `WinExec`、`LoadLibraryA`、`GetProcAddress` 各在它內部哪個位址。正常程式靠 loader 填 IAT，shellcode 沒這待遇——得自己翻 kernel32 的**匯出表（Export Address Table, EAT）**，那是 DLL 公開宣告「我提供哪些函式、各在我內部哪個 RVA」的地方（Ch 6 介紹過）。

拿到兩把萬能鑰匙就夠了：

```
解析 EAT 找到 GetProcAddress + LoadLibraryA
        │
        ├─ LoadLibraryA("ws2_32.dll")  → 載入任何其他 DLL
        └─ GetProcAddress(dll, "函式名") → 解析任何其他 API
```

有了這兩個，之後要什麼 API 都能拿——所以 shellcode 常只手動解析這兩個，其餘都透過它們。

## 先建立直覺：EAT 是三張要對齊讀的平行陣列

匯出表最容易讓人卡住的點：**函式位址、函式名、序號分成三張陣列，要交叉對照才能從「名字」查到「位址」。**

```
IMAGE_EXPORT_DIRECTORY（從 PE DataDirectory[0] 找到）
   │
   ├─ AddressOfNames        → 陣列 A：一堆「函式名字串」的 RVA（依字母排序）
   ├─ AddressOfNameOrdinals → 陣列 B：和 A 平行，存每個名字對應的「序號 index」
   └─ AddressOfFunctions    → 陣列 C：一堆「函式位址」的 RVA，用序號 index 來查
```

查找流程像查字典再翻頁：

```
想找 "WinExec"：
1. 在 陣列A（AddressOfNames）裡逐個比對，找到 "WinExec" → 它是第 i 個
2. 去 陣列B（AddressOfNameOrdinals）第 i 個，拿到序號 ordinal
3. 去 陣列C（AddressOfFunctions）第 ordinal 個，拿到 WinExec 的 RVA
4. kernel32 基址 + 該 RVA = WinExec 的實際位址 ★
```

> **為什麼要繞三張表這麼麻煩？** 因為 DLL 允許「用名字匯出」也允許「用序號匯出」，兩套索引並存。名字表按字母排序方便二分搜尋，但名字的順序不等於函式位址的順序，所以需要中間的 ordinals 表把「名字的第 i 個」翻譯成「位址表的第幾個」。看懂這層間接，EAT 就不神秘了。

## 核心：為什麼用 hash 而不是明文函式名

上面「在陣列 A 裡比對 "WinExec"」如果用明文字串，有兩個問題：

1. **壞字元**：函式名字串放進 shellcode，可能含被目標協定禁掉的位元組（Ch 8）。
2. **體積**：一堆函式名字串很佔空間，而 shellcode 要小。

解法：**對函式名做 hash**。走陣列 A 時，把每個函式名即時算成一個 32-bit hash，和你預先算好的目標 hash 比對。shellcode 裡只需放那個 4-byte hash，不放明文字串。

最經典的是 **ROR13 hash**：

```
hash = 0
對函式名的每個字元 c（含結尾）：
    hash = ROR(hash, 13)      ; 把 hash 向右旋轉 13 bits
    hash = hash + c           ; 加上這個字元
```

> **為什麼是 ROR13？** 沒有數學上的必然，是 Metasploit 等工具沿用成事實標準的慣例：旋轉讓不同順序的字元產生不同 hash（避免 "AB" 和 "BA" 撞），13 這個值經驗上碰撞率低。你也可以自訂 hash 演算法——事實上換一套自訂 hash 正是改變 shellcode 位元組樣式、躲簽章的手段之一（Ch 26）。重點是**同一套演算法你要能離線先算好目標 API 的 hash**，填進 shellcode 當比對基準。

> **常數來源**：ROR13 的 13、32-bit 累加，來自 Metasploit 的 `ror13` hash 慣例（公開、廣泛沿用）。你用任何語言都能離線算出 `WinExec`、`LoadLibraryA` 等的 ROR13 值當作比對目標。

## 用 WinDbg 看真實的 EAT（本章重頭戲）

把抽象的三張表變成眼睛能看到的東西。附掛行程、確認 kernel32 基址後（示意，標未實測）：

```
0:000> lm m kernel32                 ; 取得 kernel32 基址
0:000> !dh kernel32 -f               ; dump PE header，看 Export Directory 的 RVA/大小
0:000> x kernel32!WinExec            ; 直接問符號：WinExec 的實際位址（驗證答案用）
0:000> u kernel32!WinExec            ; 確認那裡真的是 WinExec 的 code
```

> **未實測，理論預期**：`x kernel32!WinExec` 會給你 WinExec 的實際 VA——這是你手動走 EAT「應該」得到的答案。手動解析時，把你算出的 `基址 + RVA` 和 `x` 給的位址對照，一致就代表你的 EAT 走訪邏輯對了。這是驗證 shellcode API 解析最直接的方法：先用 `x` 知道正確答案，再回頭確認你的走訪。

> **為什麼用 `x` 當對照？** 因為手動走三張表很容易在 ordinals 那層算錯一個 index，結果拿到隔壁函式的位址。有了 `x kernel32!<api>` 的「標準答案」，你能立刻發現自己錯位，不必等 shellcode 跑崩了才回頭找。

## 範例：解析流程走一遍（高階）

```
目標：拿到 WinExec 位址（kernel32 基址已由 Ch 23 取得，設為 BASE）
1. 從 BASE 的 PE header → DataDirectory[0] → IMAGE_EXPORT_DIRECTORY 的 RVA
2. 讀出 AddressOfNames / AddressOfNameOrdinals / AddressOfFunctions 三個 RVA
3. 走 AddressOfNames：對每個名字算 ROR13 hash，比對預存的 WinExec hash
4. 命中第 i 個 → AddressOfNameOrdinals[i] = ordinal
5. AddressOfFunctions[ordinal] = WinExec 的 RVA
6. WinExec 實際位址 = BASE + 該 RVA
7. 用 WinDbg `x kernel32!WinExec` 對照確認一致
```

> 逐行組語（讀名字、算 hash、三表交叉查）見 Corelan part 9 與 skape 論文；本章聚焦「為什麼這樣查、每步在對哪張表」。

## 對比與取捨

| 比對方式 | 空間 | 壞字元風險 | 靜態分析可讀性 | 典型用途 |
|---|---|---|---|---|
| 明文函式名 | 大 | 高（字串含壞字元） | 高（一眼看出呼叫什麼） | 教學、空間充裕 |
| ROR13 hash | 小 | 低 | 低（只剩數字） | 標準 shellcode |
| 自訂 hash | 小 | 低 | 更低 | 躲已知簽章（Ch 26） |

## 踩雷集錦

1. **「名字表的第 i 個，位址表就取第 i 個」**：錯。要先經 `AddressOfNameOrdinals[i]` 拿到 ordinal，再用 ordinal 去 `AddressOfFunctions`。跳過 ordinals 這層就會拿到錯誤函式——這是 EAT 解析最常見的 bug。
2. **「hash 撞到別的函式」**：不同名字理論上可能算出同一 hash（碰撞）。kernel32 函式多，用太弱的 hash 會誤命中。ROR13 經驗上夠用，但自訂 hash 時要驗證沒撞到你要的 API 附近。
3. **「forwarded export 直接當位址用」**：有些匯出項是「轉送」到別的 DLL（AddressOfFunctions 的 RVA 落在 export directory 範圍內，值其實是個 "OTHERDLL.func" 字串）。直接跳過去會崩。OSED 層級通常避開這類函式即可，但要知道有這回事。
4. **「hash 在 shellcode 裡要放明文函式名來算」**：不用。你**離線**先算好目標 API 的 hash，shellcode 裡只放那個 4-byte 結果當比對基準，執行期對 kernel32 的名字即時算 hash 來比。明文字串根本不進 shellcode。
5. **「kernel32 的 EAT 一定有我要的 API」**：不一定。有些 API 在其他 DLL（如網路的 `ws2_32`）。先用 kernel32 的 `LoadLibraryA` 載入該 DLL，再 `GetProcAddress`/走它的 EAT——Ch 25 的 reverse shell 就要這樣拿 `ws2_32` 的函式。

## 進階：再往深一層

- **直接存 API 位址 vs 每次解析**：解析一次後把位址存在暫存器/stack 重複用，比每次都走 EAT 省時省空間。大 shellcode 常先建一個「API 位址表」再呼叫。
- **GetProcAddress 之後就輕鬆了**：手動只需解析 `GetProcAddress`+`LoadLibraryA`，之後所有 API 都透過 `GetProcAddress(LoadLibraryA("dll"), "func")` 拿——但注意 `GetProcAddress` 的第二參數是明文字串，又把壞字元/可讀性問題帶回來，所以極簡 shellcode 常全程自走 EAT+hash。取捨看情境。
- **防禦視角**：手動走 EAT + hash 比對是 shellcode 的強指紋——正常程式用 IAT/`GetProcAddress`，不會自己翻匯出表算 hash。EDR 可監看這種行為；逆向分析師看到一段 code 在做 ROR13 迴圈，幾乎可斷定是 shellcode 在解析 API（見 `malware_analysis`）。

## 本章重點整理

- shellcode 自走 kernel32 的 EAT 來拿 API 位址，取代沒有的 IAT。
- EAT 是三張平行陣列：AddressOfNames（名字）、AddressOfNameOrdinals（名字→序號）、AddressOfFunctions（序號→位址 RVA）。查找要三表交叉，不能跳過 ordinals。
- 用 hash（經典 ROR13）比對函式名，省空間、避壞字元、降低可讀性；hash 離線先算好放進 shellcode。
- 只需手動解析 `GetProcAddress`+`LoadLibraryA` 即可帶出其餘所有 API。
- 用 WinDbg `x kernel32!<api>` 當標準答案驗證你的解析。

## 自我檢核

- [ ] 不看筆記，能說出從函式名查到函式位址要經過哪三張表、順序為何
- [ ] 能解釋為什麼不能「名字第 i 個就取位址第 i 個」
- [ ] 能說出用 hash 取代明文字串的三個理由
- [ ] 知道 ROR13 的 hash 怎麼算（旋轉再累加），以及為什麼要離線先算
- [ ] 能說出用哪個 WinDbg 命令取得「正確答案」來驗證自己的 EAT 走訪

## 延伸閱讀

- **[Corelan — Exploit writing tutorial part 9: Win32 shellcoding](https://www.corelan.be/index.php/2010/02/25/exploit-writing-tutorial-part-9-introduction-to-win32-shellcoding/)** — Peter Van Eeckhoutte
  - **讀哪裡**：「resolving symbols / export table」與 hashing 段落，含逐行組語。
  - **學什麼**：本章 EAT 解析與 ROR13 的逐行實作出處。
  - **前提**：Ch 23 的 kernel32 定位、本章的三表結構圖。

- **[skape — Understanding Windows Shellcode](https://www.hick.org/code/skape/papers/win32-shellcode.pdf)**
  - **讀哪裡**：symbol resolution 與 hashing 章節。
  - **學什麼**：API hashing 技術的系統化源頭。

- **[PE Format — Export Directory — Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/debug/pe-format#the-edata-section-image-only)**
  - **讀哪裡**：`.edata` / Export Directory Table、三個 Address-of 陣列的定義。
  - **學什麼**：EAT 三張表的權威欄位定義；本章結構的出處。

- **本 repo [`malware_analysis`](../malware_analysis/README.md)**
  - **讀哪裡**：API hashing 偵測、shellcode 分析相關章節。
  - **和本章的關聯**：從逆向/偵測端辨認「這段 code 在 hash 解析 API」——攻防同一套知識。

拿得到任意 API 位址後，下一章把它用起來：先組一個最小的 WinExec（彈 calc）確認呼叫慣例，再組 reverse shell 的 API 呼叫序列。

→ [Ch 25 寫 WinExec / reverse shell shellcode（組語 → bytes）](./25-writing-reverse-shell-shellcode.md)
