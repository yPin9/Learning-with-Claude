# 練習 D — 手寫 bad-char-free reverse shell shellcode

> **目標**：把 Ch 23–26 串起來——在你自有授權的隔離實驗室裡，從零組出一個不含 `\x00\x0a\x0d` 的 shellcode（先 WinExec，再進階到 reverse shell），用 nasm 組譯、WinDbg 分段驗證。這個練習驗證你是否真的理解「找 API + 呼叫慣例 + 消壞字元」整條生產線。

> **環境**：Win10 x64 22H2（32-bit shellcode），nasm、WinDbg（2026）、攻擊機（Kali/Linux 收連）。全程在你自有或明確授權的隔離環境中進行。

> **本練習定位**：手寫 shellcode 是 OSED 官方模組。本頁提供**任務規格、驗證方法、卡關提示與解題思路**；逐行可組譯的完整 payload 清單不在本頁產出，請依 Ch 23–26 的機制與延伸閱讀的 Corelan part 9 / skape 自行組出並在隔離環境驗證——這正是練習的意義：你自己寫出來，才算學會。

## 背景與動機

OSED 考試會遇到「msfvenom payload 被壞字元卡死、或空間/偵測不允許」的情境，這時你得自己造 shellcode。這個練習模擬那個處境：給你壞字元限制，要你端出一個能跑的 payload。能獨立完成它，代表你對 Part 6 的機制不是「讀過」而是「做得出來」。

## 任務規格

| 項目 | 要求 |
|---|---|
| 階段 1（必做） | 手寫 WinExec shellcode，彈出 `calc.exe` |
| 階段 2（進階） | 手寫 reverse shell，連回攻擊機 IP:port 拿互動 cmd |
| 壞字元 | 產出的 shellcode **不得含** `\x00`、`\x0a`、`\x0d` |
| position-independent | 不得寫死任何自身位址；字串/參數執行期在 stack 上組 |
| 找 API | 自走 PEB→kernel32→EAT（Ch 23–24），不得依賴目標 IAT |
| 驗證 | 用 nasm 組譯、WinDbg 分段單步確認每階段正確 |
| 工具限制 | 階段 1、2 的核心 shellcode 必須**手寫**（可用 python 做離線編碼/抽 bytes） |

## 期望輸出範例

```
階段 1：
  在隔離 VM 裡把 shellcode 載入 RWX 記憶體、EIP 指過去執行
  → calc.exe 視窗彈出
  → 檢查 shellcode bytes：grep 不到 00 / 0a / 0d

階段 2：
  攻擊機：nc -lvnp 443
  目標 VM：執行 shellcode
  → 攻擊機收到連線，出現目標的 cmd 提示字元
  → 在攻擊機打 whoami / ipconfig，看到目標主機的回應
```

## 如果你卡住了

1. **calc 彈不出來，先別懷疑呼叫慣例**——多半是前面 kernel32 基址或 WinExec 位址解析錯了。先在 WinDbg 用 `lm m kernel32` 和 `x kernel32!WinExec` 拿「正確答案」，對照你算出的值。錯在哪一跳，分段驗證（Ch 25）會告訴你。
2. **解析出的 API 位址對、但 call 後就崩**——檢查 stdcall 的參數壓棧順序（右到左）與「誰清棧」（stdcall 是被呼叫者清，你別再動 ESP）。字串參數的指標是不是真的指向你 push 上 stack 的那幾個 byte？
3. **shellcode 裡還是有 `\x00`**——通常來自把小的立即值用 4-byte mov 載入（高位補零）。想想怎麼用更短的指令、或 XOR 歸零、或編碼（Ch 26）避開。
4. **reverse shell 連不回來**——先確認 `ws2_32.dll` 真的被 `LoadLibraryA` 載入了（WinDbg `lm` 看有沒有）。再確認 `sockaddr_in` 的 port 是 network byte order（大端）、IP 沒被壞字元截斷。
5. **編碼後 decoder 不動**——decoder stub 自己是不是含壞字元？GetPC 有沒有正確拿到 payload 位址（在 WinDbg 看 pop 後那個暫存器的值對不對）？

## 實作步驟建議

### Step 1：WinExec 鏈打通（階段 1 核心）
目標：calc 彈出。先讓「PEB 走訪→解析 WinExec→呼叫」這條最短鏈 work，不管壞字元。參照 Ch 23、24、25。每一跳都在 WinDbg 對照驗證。

### Step 2：量 WinExec shellcode 的壞字元
把階段 1 的 bytes 抽出來，python 掃有沒有 `00/0a/0d`。有的話記下在哪、來自哪條指令。

### Step 3：消壞字元
兩條路（Ch 25 / Ch 26）：改寫產生壞字元的指令（選等價但乾淨的 opcode），或整段 XOR 編碼 + 加無壞字元的 decoder stub。小段優先改指令，複雜就編碼。

### Step 4：升級 reverse shell（階段 2）
在 WinExec 鏈之上，換成 Ch 25 的 reverse shell API 序列：LoadLibrary ws2_32 → WSAStartup → WSASocketA → connect → CreateProcessA（socket 當三個 std handle）。逐個 API 解析、逐步在 WinDbg 驗證。

### Step 5：整合、消壞字元、端到端測
整段再掃一次壞字元、必要時重編碼，然後端到端：攻擊機 `nc -lvnp 443`，目標跑 shellcode，確認拿到 shell 並能執行命令。多跑幾次確認穩定。

## 完整參考解答

**先自己做到卡住再看——這個練習的價值在你親手組出來。**

<details>
<summary>點開解題思路（方法與驗證，非逐行 payload）</summary>

本練習不貼可直接開火的完整 shellcode byte blob（那無助於你學會，且逐行組語在 Corelan part 9 / skape 論文俱全）。以下是能帶你走到終點的解題骨架：

**階段 1（WinExec）思路**
1. 依 Ch 23：`fs:[0x30]`→PEB→`+0x0C` Ldr→`+0x14` InMemoryOrderModuleList，走鏈比對模組名 hash 找 kernel32，讀 `+0x10` DllBase。用 `dt _PEB`/`dt _PEB_LDR_DATA`/`dt _LDR_DATA_TABLE_ENTRY` 確認你環境的 offset。
2. 依 Ch 24：從 kernel32 的 Export Directory 走 AddressOfNames（算 ROR13 hash 比對）→ AddressOfNameOrdinals → AddressOfFunctions，拿 WinExec 的 RVA，加 DllBase 得位址。用 `x kernel32!WinExec` 對照。
3. 依 Ch 25：把 `"calc"`（4 bytes）push 上 stack 當字串，ESP 當指標；push `uCmdShow=1`；push 字串指標；`call WinExec`（stdcall，不用自己清棧）。
4. 驗證：nasm `-f bin` 組譯 → python 抽 bytes → WinDbg 在 RWX 記憶體單步 → calc 彈出。

**消壞字元思路**
- 掃 bytes 找 `00/0a/0d`。`00` 最常來自 `mov eax, 1`（高位補零）或 push 小立即值——改用 `xor eax,eax; inc eax` 之類等價乾淨指令，或整段 XOR 編碼。
- 若編碼：離線 python 試 XOR key 直到「編碼輸出 + key 皆無壞字元」，加 Ch 26 的 GetPC+XOR decoder（stub 本身也要乾淨）。

**階段 2（reverse shell）思路**
1. 先 `LoadLibraryA("ws2_32.dll")`（字串 push 上 stack），再解析 `WSAStartup`/`WSASocketA`/`connect`。
2. stack 上現場組 `sockaddr_in`：`AF_INET=2`、port（大端、避壞字元）、IP（4 bytes、避壞字元）。
3. `WSASocketA` 建 socket → `connect` 連回。
4. `CreateProcessA("cmd", ...)`，`STARTUPINFO.dwFlags = STARTF_USESTDHANDLES`，三個 std handle 設為 socket。
5. 逐 API 解析、逐步 WinDbg 驗證；端到端用 `nc -lvnp <port>` 收。

**逐行可組譯的 asm**：見 Corelan「Exploit writing tutorial part 9」的 WinExec 與 reverse shell 範例、skape「Understanding Windows Shellcode」。照著組、用本練習的驗證法確認在你環境 work。

</details>

## 測試用例

| 測試 | 預期結果 | 說明 |
|---|---|---|
| shellcode bytes 掃壞字元 | 不含 00/0a/0d | 階段 1、2 都要過 |
| 階段 1 執行 | calc.exe 彈出 | 鏈正確 |
| kernel32 基址 vs `lm m kernel32` | 一致 | PEB 走訪對 |
| WinExec 位址 vs `x kernel32!WinExec` | 一致 | EAT 解析對 |
| 階段 2 執行 + 攻擊機監聽 | 收到 cmd、能執行命令 | reverse shell 成功 |
| 重跑數次 | 每次都成功 | 穩定性（Ch 39 考試重點） |

## 延伸挑戰（加分）

1. **加 ROR13 hash 讓 shellcode 更小**：若階段 1 用明文比對 "WinExec"，改成 hash 比對（Ch 24），比較前後 byte 數。
2. **自訂 hash / 自訂 key encoder**：換掉標準 ROR13 與常見 XOR key，產生「AV 簽章庫沒有的樣式」（Ch 26 進階），並思考這對靜態偵測的影響。
3. **壓到最小**：把階段 1 的 WinExec shellcode 壓到最少 bytes（複用解析出的位址、用最短等價指令），記錄你砍掉了哪些、為什麼還能 work。
4. **staged 版**：改寫成極小的第一階（連回 + recv 一塊 RWX + 跳過去），大 payload 由攻擊機送——比較對「可控空間」需求的差異（Ch 22）。

## 自我檢核

- [ ] 我的 shellcode 真的 position-independent（換個載入位址仍 work）
- [ ] 我能解釋每一個壞字元原本來自哪條指令、我怎麼消掉的
- [ ] 我能在 WinDbg 分段指出「這步拿到 kernel32、這步拿到 API、這步 call」
- [ ] 階段 2 的 reverse shell 我能說出每個 API 的角色與參數來源
- [ ] 我能說出自己的實作和 Corelan 範例的差異，以及各自取捨

Part 6 完成——你已經能自力造 payload。接下來進 Part 7，處理現代目標的第一道硬牆：DEP 讓你的 shellcode 不能直接執行，得先用 ROP 開路。

→ [Ch 27 DEP 機制復習 + OSED 情境](./27-dep-mechanism-osed.md)
