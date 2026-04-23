# Ch 1 — IDA 世界觀與資料庫

> 目標：搞懂 IDB 裡到底存什麼、主要 subview 各自扮演什麼角色，後面每一章講到「某個 view」你會秒懂。

## IDB 的心智模型

很多人以為 IDA 是「反組譯工具」，其實更準的描述是 **「binary 上面的筆記系統」**：

```
┌─────────────────────────────────────────────┐
│              你的原始 binary                │
│           （bytes，不會被改）               │
└─────────────────────────────────────────────┘
                     ▲
                     │ loader 解析
                     │
┌─────────────────────────────────────────────┐
│                  IDB                        │
│  ┌──────────┬──────────┬──────────┐         │
│  │ segments │ names    │ types    │         │
│  ├──────────┼──────────┼──────────┤         │
│  │ xrefs    │ comments │ funcs    │         │
│  ├──────────┼──────────┼──────────┤         │
│  │ structs  │ enums    │ stackframe│        │
│  └──────────┴──────────┴──────────┘         │
│           (所有分析結果都在這)              │
└─────────────────────────────────────────────┘
                     ▲
                     │ 讀寫
                     │
┌─────────────────────────────────────────────┐
│   IDA UI / IDAPython / plugin               │
└─────────────────────────────────────────────┘
```

記住這件事，因為後面寫 IDAPython 時你會發現：**幾乎每一個 API 都是在對 IDB 做 CRUD**，不是對 binary。改名字 `set_name(ea, "foo")` 是往 IDB 寫，`get_bytes(ea, 4)` 是往 IDB 讀 — 但 IDB 裡的 bytes 是從原 binary 複製過來的快照。

## 主要 subview 地圖

打開一個 binary 後，預設至少會開出四五個 window。一次看全貌：

| Subview | 開啟方式 | 幹嘛用的 |
|---|---|---|
| **IDA View-A** | 預設 | 反組譯主畫面。Text mode 看指令、Graph mode 看 CFG |
| **Pseudocode** | `F5` | Hex-Rays decompiler 產出的類 C 偽代碼 |
| **Hex View-1** | 預設 | 十六進位 + ASCII 對照，和 IDA View 連動 |
| **Strings** | `Shift+F12` | 所有偵測到的字串，逆向 CTF 的一號入口 |
| **Imports** | `View → Open subviews → Imports` | import 的 API 清單（`strcpy` / `CreateFileA`）|
| **Exports** | 同上 | export 的 symbol（DLL / `.so` 分析用）|
| **Functions** | 預設在左邊 | 所有 function 清單，可搜尋 |
| **Names** | `Shift+F4` | 所有 symbol（含 data label），是 Functions 的超集 |
| **Segments** | `Shift+F7` | section / segment 清單（`.text` / `.data` / `.bss`）|
| **Local Types** | `Shift+F1` | **9.x 的類型編輯器**，struct / enum / typedef 全在這 |
| **Type Libraries** | `Shift+F11` | 已載入的 til（標準 C、Win32 API 型別資料庫）|
| **Proximity Browser** | `View → Open subviews → Proximity browser` | function 呼叫圖（誰呼叫誰）|

**9.x 重點差異**：舊教材會告訴你 `Shift+F9` 打開 Structures、`Shift+F10` 打開 Enums — 9.x 沒了，全部進 Local Types (`Shift+F1`)。看到教材按那兩個鍵時，心裡要會換算成 `Shift+F1`。

## Navigator band：被忽略的寶藏

IDA 主畫面最上方那條彩色橫條叫 **navigator band**，不同顏色代表不同類型的資料：

```
深藍  = Library function（FLIRT 認出來的，例如 printf）
淺藍  = Regular function
黃色  = Data（大多是 string / 常數表）
紅色  = Unexplored bytes（IDA 還沒分析出是 code 還是 data）
灰色  = Instruction 但不在任何 function 內
粉紅  = Imports
```

這條東西比它看起來重要得多。看到一大片紅色：表示還有未分析區域，可能藏壓縮 payload 或 overlay；大片深藍：FLIRT 命中率高，你可以省下去細看 libc 的力氣。

**點擊 navigator band 上的任何一點就會跳到那個位址** — 比在 Function window 滑到底快。

## 四個主要 window 怎麼連動

IDA View、Pseudocode、Hex View、Output 同步移動，但行為稍微不同：

```
      IDA View (disasm)          Pseudocode (F5)
      ┌──────────────┐           ┌──────────────┐
      │ mov rax, rdi │           │ v1 = a1;     │ ← 同一個位址
      │ mov rcx, rsi │  ←同步→   │ v2 = a2;     │
      │ call foo     │           │ foo(v1, v2); │
      └──────────────┘           └──────────────┘
             │                          │
             ▼                          ▼
      ┌──────────────┐           ┌──────────────┐
      │ Hex View     │           │ Output       │
      │ 48 8B C7     │           │ (log / print)│
      └──────────────┘           └──────────────┘
```

- **IDA View ↔ Hex View**：雙向同步，點 IDA View 的指令，Hex View 會跳到對應 bytes。
- **IDA View ↔ Pseudocode**：**單向同步** — 在 disasm 移動會讓 Pseudocode 跳到對應行，但在 Pseudocode 上下移動不會連動 disasm。想反向跳要按 `Tab`（下一章細講）。

## `.i64` vs `.i64.bak`：誤觸救命機制

IDA 每次存檔會自動留一份 `xxx.i64.bak`（前一次儲存的版本）。不小心存壞了（例如跑了錯的 IDAPython 把所有 function 都改名亂七八糟）— 關閉 IDA，把 `.i64.bak` 改回 `.i64`，剛存的災難就沒了。

**養成跑 script 前存檔的習慣**，後面 Part 2 我們會寫不少會動 IDB 的 script。

## 常見誤解

- **「把 `.i64` 傳給隊友就能分析」**：錯，`.i64` 不含 binary 本身的完整 image（某些 section 只存元資料）。debug、patch、重新 load 都需要原 binary。
- **「IDA 自動分析等於 decompiler」**：錯。auto analysis 產生的是 disasm + function boundary + type guess；decompiler 是 Hex-Rays 另外一層，要另外授權，**沒授權時 F5 不會有東西**。
- **「函式顯示 sub_XXXXXX 就是沒命中任何 FLIRT 簽名」**：不完全對。FLIRT 只對 library code 有用（libc、MSVCRT、Boost 等）；使用者自己寫的 function 本來就會是 `sub_`。

## 動手練習

1. 打開一個你熟悉的 binary，把 Strings、Imports、Functions、Local Types 這四個 window 都叫出來，排好版面。
2. 按 `Ctrl+1`（或 `View → Recent scripts`）沒內容沒關係 — 記住這個快捷鍵，Part 2 會常用。
3. 觀察 navigator band 的顏色分布，估計這個 binary 有多少 library function 比例。
4. 在 Local Types（`Shift+F1`）看一下 — IDA 自動認出來的 standard C type 應該一大堆。

## 自我檢核

- [ ] 知道 IDB 是 binary 上的筆記層，所有 API 本質在讀寫 IDB
- [ ] 認識 Strings / Imports / Functions / Local Types 四個主要 subview
- [ ] 知道 9.x 的 Local Types 取代了舊版 Structures / Enums window
- [ ] 能看懂 navigator band 的顏色
- [ ] 知道 Pseudocode 和 IDA View 是單向同步（disasm 跳會帶偽代碼）

下一章開始真正的快捷鍵戰鬥 — 靜態分析情境，改名、改型別、交叉引用、data/code 切換。

→ [Ch 2 靜態分析情境快捷鍵](./02-static-analysis-hotkeys.md)
