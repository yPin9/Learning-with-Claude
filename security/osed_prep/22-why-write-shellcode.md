# Ch 22 — 為什麼手寫 shellcode（vs msfvenom）

> **目標**：搞懂為什麼 OSED 要你會手寫 shellcode，而不是一律 `msfvenom` 產生就好。你會看清 msfvenom 的侷限在哪、手寫的好處是什麼、position-independent code（PIC）為何是 shellcode 的硬需求，以及什麼時候該選哪一種。這章是整個 Part 6 的動機與地圖。

> **環境**：Win10 x64 22H2（32-bit 目標），後續章節用 nasm 組譯、WinDbg（2026）驗證。本章偏觀念，實作在 Ch 23–26。

## 為什麼不乾脆一律用 msfvenom？

從 Linux pwn 過來的人，習慣一行 `msfvenom -p ... -f python` 就拿到 shellcode。能動，為什麼 OSED 偏要你手寫？因為 OSED 官方有一整個模組叫「Creating Custom Shellcode」，而理由很實際：**在真實目標上，現成 shellcode 常常直接失效**，失效時你要有能力自己造一個，或至少看懂別人的在幹嘛。

更深一層：手寫 shellcode 會逼你理解「一段位元組如何在完全不知道自己被載到哪、不能依賴任何既有匯入表的情況下，自己找到 Windows API 並呼叫它」。這個理解不只是為了考試——它是你讀懂惡意軟體、分析 exploit payload、做偵測的共同底座。本 repo 的 `malware_analysis` 課從分析端看同一件事。

## 先建立直覺：shellcode 是「無家可歸」的程式

一般程式有作業系統載入器伺候：loader 幫它填好 IAT（Ch 6），所以它呼叫 `WinExec` 時，位址早就被填進匯入表了。shellcode 沒有這種待遇——它是被塞進某個緩衝區、靠 exploit 劫持控制流才開始跑的一段裸位元組：

```
   正常程式                           shellcode
┌──────────────────┐            ┌──────────────────────────┐
│ loader 幫你：     │            │ 沒人幫你：                │
│ - 載到固定位址    │            │ - 不知道自己在哪個位址    │ → 不能寫死位址（PIC）
│ - 填好 IAT        │            │ - 沒有 IAT 可用           │ → 自己找 API（Ch 23-24）
│ - 設好 stack      │            │ - 借用別人的 stack        │ → 小心不要踩壞
│ 你直接 call API   │            │ - 連 kernel32 在哪都要自己找 │
└──────────────────┘            └──────────────────────────┘
```

這個「無家可歸」的處境，決定了 shellcode 的兩條鐵律：

1. **position-independent（位置無關）**：不能寫死任何自身位址，因為每次被載到哪都不一定。要用相對跳轉、或執行期動態取得自身位址（Ch 26 的 GetPC）。
2. **自力更生找 API**：不能靠 IAT，得自己從 PEB 找到 kernel32、再從它的匯出表解析出需要的函式（Ch 23–24）。

> **為什麼 PIC 是硬需求？** 假設你的 shellcode 裡寫了「jump 到 0x00402000」。但 shellcode 這次被塞在 stack 的 `0x0012f1e0`、下次 exploit 可能落在 `0x0012f3a0`——寫死的位址立刻全錯。PIC 讓同一份位元組不管落在哪都能跑，這是它能當 payload 的前提。

## 核心：msfvenom 的四個侷限

msfvenom 很強，但在真實目標上常踩到這四件事：

**(1) 被偵測**。msfvenom 的輸出（包含它的 encoder，如經典的 `shikata_ga_nai`）位元組樣式是公開的，AV/EDR 的簽章庫對它瞭若指掌。你的 exploit 技術再漂亮，payload 一落地就被防毒吃掉，結果是「exploit 沒錯但沒反應」。

**(2) 壞字元（bad chars）**。目標協定可能禁掉一堆位元組（Ch 8）。msfvenom 的 `-b` 能排除壞字元，但它靠加 encoder 來迴避——encoder 本身又可能引入新問題（見下）、放大體積，有時候排不掉某些組合。

**(3) 體積**。msfvenom 的 reverse shell 動輒 350+ bytes，加上 encoder 的 decoder stub 更大。當你可控空間只有一百多 bytes（SEH 之後、短緩衝），msfvenom 的標準 payload 根本塞不下——這也是 egghunter（Ch 20）存在的理由之一。

**(4) 對環境的隱藏假設**。msfvenom 的 Windows payload 預設走某些 API、某些找 kernel32 的方式，在特定 Windows 版本或受限情境下不一定成立。你不懂它內部怎麼運作，它出問題時你無從 debug。

## 手寫的好處

對照上面四點：

| 面向 | msfvenom | 手寫 |
|---|---|---|
| 偵測 | 位元組樣式已知、易被簽章命中 | 樣式獨特，可針對性避開特徵 |
| 壞字元 | 靠 encoder 迴避，可能引入新問題 | 選指令時就避開，精準可控 |
| 體積 | 偏大（含 encoder stub） | 可壓到極小（只做必要的事） |
| 可理解/可除錯 | 黑箱，壞了難查 | 每個 byte 你都知道在幹嘛 |
| 開發速度 | 一行就有 | 慢，要組語+測試 |

> 最後一欄點出代價：**手寫慢**。所以現實是混用——能用 msfvenom 就用（開發初期快速驗證 exploit 鏈），碰到偵測/壞字元/空間問題再手寫關鍵部分。OSED 要你會手寫，不是要你永遠不用 msfvenom，是要你在 msfvenom 失效時不會卡死。

## 範例：同一個目標，兩種 shellcode 的差別

假設你要讓目標彈一個 `calc.exe`（最小驗證用）：

**msfvenom 版**（示意）：
```bash
msfvenom -p windows/exec CMD=calc.exe -b "\x00\x0a\x0d" -f python -v sc
# 產出：一段含 encoder decoder stub 的位元組，約 200+ bytes
```

**手寫版的思路**（Ch 23–25 會真的做）：
```
1. 從 fs:[0x30] 找到 PEB，走模組鏈找到 kernel32 基址   （Ch 23）
2. 解析 kernel32 匯出表，找到 WinExec 的位址            （Ch 24）
3. 把字串 "calc" 推上 stack，依 stdcall 擺好參數
4. call WinExec
→ 可壓到數十 bytes，無 encoder，位元組樣式自己掌控
```

> **未實測**：兩版的實際位元組與行為需在你自己環境組譯、WinDbg 載入驗證（Ch 25 給完整步驟）。這裡的重點是對比「黑箱一行」vs「每步你都懂」——後者在 payload 出問題時，你能單步進去看卡在哪。

## 對比與取捨：什麼時候選哪個

| 情境 | 建議 |
|---|---|
| 開發初期，先驗證 exploit 鏈通不通 | msfvenom（快） |
| payload 落地被 AV 吃掉 | 手寫 / 自訂編碼（Ch 26） |
| 可控空間極小 | 手寫精簡版 或 egghunter + 他處大 shellcode |
| 壞字元多且 msfvenom 排不乾淨 | 手寫，選指令時避開 |
| 要深入理解 / 考 OSED | 手寫（這就是訓練目的） |
| 時間緊、環境單純、無防毒 | msfvenom 夠用 |

## 踩雷集錦

1. **「shellcode 可以寫死位址，反正我知道它會載到哪」**：錯誤直覺。exploit 每次的記憶體佈局可能不同，寫死自身位址的 shellcode 換個環境/再跑一次就錯。shellcode 必須 position-independent。
2. **「msfvenom `-b` 排了壞字元就一定乾淨」**：不一定。它靠 encoder 迴避，而 encoder 的 decoder stub 自己也得無壞字元，某些壞字元組合下會失敗。手寫時你是在選指令階段就避開，更可靠。
3. **「手寫 shellcode 就一定繞得過 AV」**：不保證。AV 還有啟發式與行為偵測（連外、可疑 API 序列）。手寫只是改變位元組樣式躲簽章，不是萬靈丹——這點 Ch 26、Ch 37 會談。
4. **「shellcode 能直接用目標程式 import 的 API」**：不能假設。你不知道目標 import 了什麼、IAT 在哪。shellcode 要自力從 kernel32 找 API（Ch 23–24），這是它和普通程式最大的不同。
5. **「體積不重要，塞得下就好」**：在 SEH 之後或短緩衝，空間常只有百來 bytes。msfvenom 標準 payload 塞不下是真實常見的卡點，不是理論問題。

## 進階：再往深一層

- **分階（staging）**：與其塞一個大 payload，不如塞一個極小的「第一階」shellcode，它的工作只是「連回攻擊機、把真正的大 payload 下載進可執行記憶體再跳過去」。這大幅降低對可控空間的需求。msfvenom 的 `windows/shell/reverse_tcp`（有 `/`）就是 staged，`windows/shell_reverse_tcp`（底線）是 stageless。
- **API 呼叫慣例的坑**：Windows API 多為 stdcall（callee 清棧，Ch 3），手寫時參數壓棧順序、呼叫後 stack 狀態都要算準，否則 call 完 stack 歪掉。Ch 25 會逐一處理。
- **偵測對抗是貓抓老鼠**：手寫改樣式只贏一時，EDR 會追行為。真正的對抗邏輯（以及對應的偵測）是另一個大主題——攻擊面見本課 Ch 26，偵測面見 `blue_team_dfir`。

## 本章重點整理

- shellcode 是「無家可歸」的裸位元組：沒有 loader、沒有 IAT、不知自身位址。
- 兩條鐵律：position-independent（不寫死自身位址）、自力找 API（從 PEB→kernel32→EAT）。
- msfvenom 四侷限：易被偵測、壞字元靠 encoder 迴避、體積大、黑箱難 debug。
- 手寫好處：樣式獨特、壞字元精準可控、可壓極小、每 byte 可理解。代價是慢。
- 現實是混用：能用 msfvenom 就用，碰偵測/壞字元/空間問題再手寫。

## 自我檢核

- [ ] 不看筆記，能說出 shellcode 為什麼必須 position-independent
- [ ] 能解釋 shellcode 為什麼不能用目標程式的 IAT，得自己找 API
- [ ] 能講出 msfvenom 的至少三個侷限，各對應什麼真實卡點
- [ ] 知道 staged 和 stageless payload 的差別，以及為什麼 staged 省空間
- [ ] 能判斷一個情境該用 msfvenom 還是手寫

## 延伸閱讀

- **[Corelan — Exploit writing tutorial part 9: Introduction to Win32 shellcoding](https://www.corelan.be/index.php/2010/02/25/exploit-writing-tutorial-part-9-introduction-to-win32-shellcoding/)** — Peter Van Eeckhoutte
  - **讀哪裡**：開頭「為什麼要懂 shellcode 內部」與找 kernel32 的動機段落。
  - **學什麼**：本章觀念的經典出處，並直接銜接 Ch 23–25 的實作。
  - **前提**：會基本 x86 組語（Ch 3）。

- **[msfvenom 官方文件 / Offensive Security docs](https://docs.metasploit.com/docs/using-metasploit/basics/how-to-use-msfvenom.html)**
  - **讀哪裡**：payload 命名（staged vs stageless）、`-b`、`-f`、encoder 段落。
  - **學什麼**：看懂 msfvenom 在做什麼，才知道它的侷限從哪來。

- **[Nightbird / skape — 各家 Win32 shellcode 入門]（搜 "win32 shellcode tutorial"）**
  - **讀哪裡**：任一講「PEB 找 kernel32 + EAT 解析」的入門文，對照本課 Ch 23–24。
  - **為什麼值得讀**：多看一個作者的講法，能補足單一來源的盲點。
  - **注意**：留意文章年代，找 kernel32 的 offset 隨 Windows 版本有差異，以你環境實測為準。

- **本 repo [`malware_analysis`](../malware_analysis/README.md) 與 [`windows_pwn` Ch 25](../windows_pwn/25-windows-shellcode.md)**
  - **和本章的關聯**：前者從分析/偵測端看 shellcode，後者是本主題的另一份教材；三者對照能把「攻與防是同一套知識」看清楚。

動機與地圖建立了。下一章進入第一個實作：shellcode 的第一步永遠是「找到 kernel32 在哪」——我們從 `fs:[0x30]` 的 PEB 走起。

→ [Ch 23 PEB 走訪找 kernel32 基址](./23-peb-walk-kernel32.md)
