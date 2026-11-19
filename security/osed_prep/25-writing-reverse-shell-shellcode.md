# Ch 25 — 寫 WinExec / reverse shell shellcode（組語 → bytes）

> **目標**：把 Ch 23–24 拿到的 API 位址用起來——理解如何依 stdcall 呼叫慣例擺好參數、呼叫 API，先組一個最小的 WinExec（彈 calc）確認流程，再看懂 reverse shell 需要哪一串 API 呼叫、各自要準備什麼。也會走一遍「nasm 組譯 → 抽出 bytes → WinDbg 載入驗證」的工具流。逐行可組譯的完整 payload 以權威公開教材為準（延伸閱讀），本章聚焦呼叫序列與機制。

> **環境**：Win10 x64 22H2（32-bit shellcode），nasm 組譯，WinDbg（2026）驗證。所有操作在你自有授權的隔離實驗室中進行。

> **本章定位**：手寫 shellcode 是 OSED 官方模組「Creating Custom Shellcode」。本課教的是**呼叫慣例、API 序列、驗證工具流**這些機制，讓你讀得懂、也能照權威教材自行組出 payload；一份可直接開火的完整 reverse shell byte blob 不在本章產出範圍——那無助於理解，且逐行組語在 Corelan / skape 等公開出處俱全。

## 為什麼先做 WinExec 再做 reverse shell？

因為兩者的「找 API + 呼叫慣例」機制一樣，但 WinExec 只要呼叫**一個** API、參數最少，是驗證你整條鏈（PEB 走訪→EAT 解析→呼叫）通不通的最小測試。確認 WinExec 能彈出 calc，代表前面 Ch 23–24 全對了，再去堆疊 reverse shell 那串較長的 API 呼叫才有把握。

```
WinExec("calc", SW_SHOW)        ← 單一 API，最小驗證：鏈通了嗎？
        │ 通過後
        ▼
reverse shell                   ← 多個 API 串起來：WSAStartup → WSASocket
                                   → connect → CreateProcess（把 socket 接上 cmd）
```

## 先建立直覺：呼叫一個 Windows API 要擺好什麼

Windows API 絕大多數是 **stdcall**（Ch 3）：參數**由右到左**壓進 stack，呼叫後由被呼叫函式自己清棧（`ret N`）。所以手動呼叫一個 API 的動作是：

```
1. 把參數「由右到左」依序 push 到 stack
2. 把 API 位址放進某暫存器（或直接 call）
3. call 它
→ 回傳值在 EAX；stack 由 API 自己清（stdcall），你不用清
```

以 `WinExec(lpCmdLine, uCmdShow)` 為例（兩個參數）：

```
stack（push 後，由右到左壓，所以 uCmdShow 先壓、lpCmdLine 後壓）：
┌────────────────────┐ ← ESP（呼叫時）
│ lpCmdLine → "calc" 的位址 │
├────────────────────┤
│ uCmdShow = 1 (SW_SHOWNORMAL) │
└────────────────────┘
call WinExec
```

> **關鍵難點：字串參數怎麼來？** `lpCmdLine` 要的是一個「指向字串 "calc" 的指標」。shellcode 裡不能依賴字串被放在某個已知位址——position-independent（Ch 22）。所以慣用手法是**把字串當立即值 push 到 stack 上**（例如把 "calc" 的 4 個 bytes 當一個 dword push），再把當下的 ESP 當作「指向這字串的指標」。字串是執行期現場堆到 stack 上的，不寫死位址。

> **SW_SHOW 這類常數**：`uCmdShow` 的 `SW_SHOWNORMAL = 1`、`SW_HIDE = 0`，來自 Win32 `ShowWindow` 常數（MSDN）。彈 calc 用 1 即可。

## 核心：reverse shell 需要哪一串 API

reverse shell 的本質：**開一個 socket 連回攻擊機，把這個 socket 當成 cmd.exe 的標準輸入/輸出/錯誤，於是攻擊機打字就等於在目標上對 cmd 打字。** 達成它的 API 序列（走 Winsock）：

```
1. LoadLibraryA("ws2_32.dll")      載入 Winsock（kernel32 的 EAT 沒有網路 API）
2. WSAStartup(...)                 初始化 Winsock
3. WSASocketA(...)                 建立一個 TCP socket
4. connect(sock, sockaddr_in, ...) 連回攻擊機（IP+port 編進 sockaddr_in）
5. CreateProcessA("cmd", ...)      啟動 cmd，STARTUPINFO 把三個 std handle 設成 sock
```

每一步的重點與坑：

- **第 1 步**：網路 API 在 `ws2_32.dll`，不在 kernel32。所以要先用 Ch 24 拿到的 `LoadLibraryA` 把它載入，再解析它的匯出（或用 `GetProcAddress`）拿 `WSAStartup`/`WSASocketA`/`connect`。
- **第 3 步**：用 `WSASocketA` 而非 `socket`，因為前者建立的 handle 預設可直接當 std handle 繼承給子行程（經典 shellcode 慣例，省事）。
- **第 4 步**：`sockaddr_in` 結構要在 stack 上現場組：family（`AF_INET=2`）、port（**network byte order，大端**）、IP（4 bytes）。**IP 和 port 都要注意壞字元**——例如 port 4444 = `0x115C`，若含被禁位元組要換 port 或編碼。
- **第 5 步**：`CreateProcessA` 的 `STARTUPINFO` 裡，`dwFlags` 設 `STARTF_USESTDHANDLES`，把 `hStdInput`/`hStdOutput`/`hStdError` 三個都指向第 3 步的 socket。這一步是「把網路接上 cmd」的關鍵。

> **這些結構/常數的來源**：`sockaddr_in`（family/port/addr 欄位佈局）、`AF_INET=2`、`STARTF_USESTDHANDLES`、`STARTUPINFO`/`PROCESS_INFORMATION` 佈局，全來自 MSDN（見延伸閱讀）。手動組 shellcode 時，你是在 stack 上「照這些結構的欄位順序」把值堆出來。

> **為什麼不用 WinExec 做 reverse shell？** WinExec 只能「執行一個命令」，無法把 socket 接到它的 std handle。reverse shell 需要 socket + 行程 + handle 重導三者綁定，所以非走 WSASocket + CreateProcess 不可。WinExec 適合「執行本機命令」（彈 calc、加使用者）這類不需回連的 payload。

## 工具流：從組語到可測試的 bytes

手寫 shellcode 的開發循環（概念步驟，逐行 asm 見延伸閱讀）：

```
1. 用 nasm 把你的 .asm 組譯成扁平 binary：
   nasm -f bin shellcode.asm -o shellcode.bin
2. 把 .bin 轉成可貼進 exploit 的位元組（python 讀檔轉 \xNN，或 objdump 看）
3. 在 WinDbg 裡測試：
   - 用一個「shellcode runner」小程式，或在偵錯中把 bytes 寫進一塊
     VirtualAlloc 出來的 RWX 記憶體，EIP 指過去單步跑
4. 單步觀察：PEB 走訪對不對？API 位址解析對不對（對照 x kernel32!WinExec）？
   參數擺對了嗎？call 之後 EAX 回傳值合理嗎？
```

> **未實測，理論預期**：上述流程我無法在本機實跑（需 Windows + nasm + WinDbg）。`nasm -f bin` 產生扁平 binary、WinDbg 在 RWX 記憶體單步 shellcode，都是標準做法。驗證的核心紀律：**不要一次寫完整段才測**，而是「找 kernel32 → 測一次、解析一個 API → 測一次、呼叫 → 測一次」，分段驗證，否則錯了很難定位。

## 範例：最小 WinExec 的驗證思路

```
目標：讓目標彈 calc，驗證 Ch23→24→25 整條鏈
1. PEB 走訪拿 kernel32 基址（Ch 23）→ WinDbg 比對 lm m kernel32
2. EAT+hash 解析 WinExec 位址（Ch 24）→ 對照 x kernel32!WinExec 一致
3. 把 "calc" push 上 stack，ESP 當字串指標；push SW_SHOWNORMAL(1)
4. call WinExec → calc 彈出 = 成功
```

> calc 彈出的那一刻，代表你手寫鏈的「找 API + 呼叫慣例」完全正確。這是進 reverse shell 前的必過關卡。

## 對比與取捨

| payload | 需要的 API | 複雜度 | 用途 |
|---|---|---|---|
| WinExec("calc") | 1 個 | 最低 | 驗證鏈通了沒 |
| WinExec("cmd /c ...") | 1 個 | 低 | 執行本機命令（加使用者等） |
| reverse shell | 5+ 個（含 ws2_32） | 高 | 回連拿互動 shell |
| bind shell | 類似 + listen/accept | 高 | 目標開 port 等連入（易被防火牆擋） |

> OSED 考試多半要 reverse shell（回連比 bind 不易被目標防火牆擋）。但開發時先用 WinExec/calc 驗證鏈，是省時間的紀律。

## 踩雷集錦

1. **「stdcall 呼叫後還自己清棧」**：stdcall 是**被呼叫函式**清棧（`ret N`）。你若呼叫後又去調 ESP，會把 stack 弄歪，下一個 call 就崩。搞清楚 cdecl（你清）vs stdcall（它清）的差別（Ch 3）。
2. **「字串放在固定位址」**：違反 PIC。字串要執行期 push 到 stack、用 ESP 當指標。寫死字串位址的 shellcode 換環境即錯。
3. **「忘了 IP/port 的壞字元」**：port 和 IP 編進 sockaddr_in 時，某些值含 `\x00` 等壞字元會讓 payload 在送達前被截斷。選 port/IP 時就要避，或用 Ch 26 的編碼。port 還要記得是 network byte order（大端）。
4. **「網路 API 去 kernel32 的 EAT 找」**：找不到。`WSAStartup`/`connect` 在 `ws2_32.dll`，要先 `LoadLibraryA("ws2_32.dll")` 再解析。只有最基礎的 API 在 kernel32。
5. **「一次寫完整段 reverse shell 才測」**：錯了極難定位（是 PEB 走訪錯？API 解析錯？參數錯？）。分段測：每拿到一個 API、每完成一次 call 都在 WinDbg 確認一次。

## 進階：再往深一層

- **stageless vs staged（Ch 22 回顧）**：手寫完整 reverse shell 較大。空間緊時改手寫一個極小的「第一階」：只做 socket 連回 + recv 一塊 RWX + 跳過去，真正的大 payload 由攻擊機送第二階。這把手寫的負擔降到最小。
- **CreateProcess 的替代**：除了把 socket 當 std handle 給 cmd，也有用 `cmd.exe` 搭配管線、或用其他行程建立 API 的變體。OSED 層級掌握經典 WSASocket+CreateProcess 版即可。
- **穩定性**：reverse shell 跑完，原本被你劫持的目標行程往往已經殘破。考慮讓 shellcode 最後乾淨結束執行緒（`ExitThread`）或嘗試讓目標服務續命，避免 shell 一拿到目標就崩——這在 Ch 39 考試穩定性會再提。
- **防禦視角**：這串 API 序列（WSASocket→connect→CreateProcess with 重導 handle）是 reverse shell 的強行為指紋，EDR 可據此偵測。理解序列就理解偵測點（見 `blue_team_dfir`）。

## 本章重點整理

- Windows API 多為 stdcall：參數右到左 push，被呼叫函式清棧，回傳在 EAX。
- 字串參數要執行期 push 到 stack、用 ESP 當指標（PIC），不寫死位址。
- 先做 WinExec("calc") 驗證「找 API + 呼叫慣例」整條鏈，再堆 reverse shell。
- reverse shell 序列：LoadLibrary ws2_32 → WSAStartup → WSASocketA → connect → CreateProcessA（socket 當三個 std handle）。網路 API 不在 kernel32。
- 工具流：nasm -f bin → 抽 bytes → WinDbg 在 RWX 記憶體分段單步驗證。IP/port 注意壞字元與 byte order。

## 自我檢核

- [ ] 不看筆記，能說出 stdcall 下呼叫 API 後「誰清棧」，以及搞錯會怎樣
- [ ] 能解釋字串參數為什麼要 push 到 stack 而非寫死位址
- [ ] 能背出 reverse shell 的 API 呼叫序列，並說出哪些在 ws2_32 而非 kernel32
- [ ] 知道為什麼先做 WinExec/calc，再做 reverse shell
- [ ] 能說出「分段驗證」具體是分哪幾段、各在 WinDbg 看什麼

## 延伸閱讀

- **[Corelan — Exploit writing tutorial part 9: Win32 shellcoding](https://www.corelan.be/index.php/2010/02/25/exploit-writing-tutorial-part-9-introduction-to-win32-shellcoding/)** — Peter Van Eeckhoutte
  - **讀哪裡**：WinExec 與 reverse shell 的逐行組語、呼叫慣例段落。
  - **學什麼**：本章 API 序列的逐行實作出處；想要可組譯的 asm 看這裡。
  - **前提**：Ch 23–24、本章的呼叫慣例圖。

- **[MSDN — WinExec / WSASocketA / connect / CreateProcessA / STARTUPINFO](https://learn.microsoft.com/en-us/windows/win32/api/)**
  - **讀哪裡**：各 API 的參數表與相關結構（`sockaddr_in`、`STARTUPINFO`、`PROCESS_INFORMATION`）。
  - **學什麼**：每個參數的語意與結構欄位佈局——手動在 stack 組參數的依據。
  - **注意**：這是本章所有常數（`AF_INET`、`STARTF_USESTDHANDLES` 等）的權威來源。

- **[skape — Understanding Windows Shellcode](https://www.hick.org/code/skape/papers/win32-shellcode.pdf)**
  - **讀哪裡**：payload 建構（WinExec、connect-back）章節。
  - **學什麼**：系統化的 shellcode 設計思路。

- **本 repo [`windows_pwn` Ch 25](../windows_pwn/25-windows-shellcode.md) 與 [`blue_team_dfir`](../blue_team_dfir/README.md)**
  - **和本章的關聯**：前者同主題另一份教材；後者從偵測端看 reverse shell 的行為指紋。

shellcode 組出來了，但它可能含壞字元。下一章處理這個：用 encoder 把壞字元編掉、執行期再用 decoder stub 還原，並看 GetPC 這個「取得自身位址」的關鍵技巧。

→ [Ch 26 自寫 encoder/decoder stub + 消壞字元](./26-custom-encoder-decoder.md)
