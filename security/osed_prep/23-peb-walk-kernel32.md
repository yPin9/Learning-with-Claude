# Ch 23 — PEB 走訪找 kernel32 基址

> **目標**：搞懂 shellcode 的第一步——在不知道任何位址、沒有 IAT 的情況下，如何從 `fs:[0x30]` 的 PEB 出發，走過已載入模組的鏈，定位 kernel32.dll 的基址。重點是**看懂這條路每一跳的結構與 offset 來源**，並能在 WinDbg 用 `dt` 親手驗證。逐行組語實作請對照本章延伸閱讀的權威出處自行組譯。

> **環境**：Win10 x64 22H2，偵錯 32-bit 行程（WoW64），WinDbg（2026）用 `dt` 驗證結構。所有 offset 為 **x86（32-bit）** 版本，x64 的 offset 不同。

> **本章定位**：這是標準的 Windows 內部結構知識，攻擊面（手寫 shellcode）與防禦面（分析惡意程式怎麼找 API）共用。本課只在你自有授權的實驗室裡，用 WinDbg 觀察這些結構來建立理解；逐行可組譯的 shellcode 清單不在本章產出範圍，改以權威公開教材為準（見延伸閱讀）。

## 為什麼第一件事是找 kernel32？

Ch 22 說過 shellcode 無家可歸：沒有 IAT，不能直接 `call WinExec`。但你需要的 API（`WinExec`、`LoadLibraryA`、`GetProcAddress`、`CreateProcessA`…）全住在 **kernel32.dll**，或由它能帶你找到的 DLL 裡。這條鏈是：

```
定位 kernel32 基址  →  解析它的匯出表（Ch 24）  →  拿到任何 API 位址  →  呼叫
     (本章)
```

kernel32 是起點，而且有個方便事實：**它幾乎一定已被載入每個行程**（太基礎了），所以你不用自己 `LoadLibrary`——它就在記憶體裡，你只要「找到它在哪」。

> 為什麼是 kernel32 而不是 ntdll？兩者都一定在。但 kernel32 匯出了 `LoadLibraryA`/`GetProcAddress` 這兩把「萬能鑰匙」，有了它們就能載入並解析任何其他 DLL/API，所以傳統上從 kernel32 起手最省事。

## 先建立直覺：借用作業系統自己的「已載入模組清單」

作業系統本來就維護著「這個行程載入了哪些模組、各在哪」的清單——它得知道才能當 loader。shellcode 的手法就是**借用這份現成清單**。清單的入口藏在一個固定、位置無關的地方：`fs` 段指向的 TEB。

```
fs:[0]  ─────────────► TEB (Thread Environment Block)
                         │
fs:[0x30] ───────────►   └─ TEB + 0x30 = PEB (Process Environment Block)
                              │
         PEB + 0x0C ─────────►└─ PEB_LDR_DATA（模組清單的管理結構）
                                   │
         Ldr + 0x14 ─────────────►└─ InMemoryOrderModuleList（雙向鏈結串列）
                                        │
                                        ├─ 模組（程式自己）
                                        ├─ 模組（ntdll）
                                        ├─ 模組（kernel32）← 目標
                                        └─ ...
```

整個手法：`fs:[0x30]` 拿到 PEB → 進 Ldr → 走 InMemoryOrderModuleList → 逐一比對模組名找到 kernel32 → 讀出它的 DllBase。

> **為什麼用 `fs:[0x30]` 而非寫死 PEB 位址？** 在 32-bit Windows，`fs` 段永遠指向當前執行緒的 TEB，這是 CPU+OS 保證、位置無關的入口。shellcode 不管被載到哪，`fs:[0x30]` 永遠拿得到 PEB——正好滿足 PIC（Ch 22）。

## 核心：這條路的 offset 地址簿（x86）

這些 offset 是走訪的關鍵，每一個都能用 WinDbg `dt` 驗證：

```
fs:[0x30]                → PEB 基址
PEB + 0x0C               → PEB.Ldr（指向 PEB_LDR_DATA）
Ldr + 0x14               → InMemoryOrderModuleList 的第一個 LIST_ENTRY
```

InMemoryOrderModuleList 是一條 `LIST_ENTRY` 雙向鏈，每個節點指向某模組 `LDR_DATA_TABLE_ENTRY` 的 **InMemoryOrderLinks 欄位**（注意：指向的是結構中間的欄位，不是結構開頭）。從一個節點取資訊：

```
（offset 相對「InMemoryOrderLinks 節點指標」）
節點 + 0x00              → Flink（下一個模組節點）
節點 + 0x10              → DllBase（模組基址）★ 最終要的東西
節點 + 0x28              → BaseDllName.Buffer（模組名，UNICODE 寬字元）
```

> **為什麼 offset 看起來「錯位」（0x10、0x28）？** 因為鏈節點指向的是結構中間的 `InMemoryOrderLinks`，所以從節點取 DllBase/名稱的 offset 是相對「該欄位的位置」算的，不是相對結構開頭。這是初學者最常算錯的點——務必用 `dt` 對一次。

## 走訪邏輯（高階描述，非組語清單）

用文字描述演算法，逐行組語請見延伸閱讀的 Corelan part 9 / skape：

```
1. EAX ← fs:[0x30]                 取 PEB
2. EAX ← [EAX + 0x0C]              取 Ldr
3. ESI ← [EAX + 0x14]             取 InMemoryOrderModuleList 第一個節點
4. 迴圈：
     讀目前節點 + 0x28 的模組名（寬字元）
     和 "kernel32.dll"（通常比對大小寫不敏感、或只比前幾個字）比較
     命中 → 讀 節點 + 0x10 = DllBase，結束
     沒命中 → 節點 ← [節點 + 0x00]（Flink，下一個），繼續
```

常見的省位元組技巧：不比對完整字串，而是對模組名做一個雜湊（hash），和預先算好的 kernel32 雜湊比對——這同時解決了「字串含壞字元」和「省空間」，Ch 24 的 API 解析會用同一套 hashing 思路。

## 在 WinDbg 親手驗證每一跳（這是本章的重頭戲）

與其背 offset，不如在 WinDbg 裡把它們走一遍，看到真實結構。附掛任一 32-bit 行程後（示意，標未實測）：

```
0:000> r $teb                      ; 或 !teb，拿到 TEB 位址
0:000> dt _TEB @$teb ProcessEnvironmentBlock   ; TEB 裡的 PEB 指標
0:000> dt _PEB <peb位址> Ldr                    ; PEB.Ldr
0:000> dt _PEB_LDR_DATA <ldr位址> InMemoryOrderModuleList
0:000> dt _LDR_DATA_TABLE_ENTRY <節點> DllBase BaseDllName
0:000> !peb                        ; 一次把 PEB 全貌（含模組清單）印出來對照
```

> **未實測，理論預期**：以上命令與結構名來自 WinDbg 的符號化型別（`dt`）。實際位址依環境而定。`dt` 會直接告訴你每個欄位的 offset——把 `dt _PEB`、`dt _PEB_LDR_DATA`、`dt _LDR_DATA_TABLE_ENTRY` 各印一次，你會親眼看到 0x0C、0x14、0x10、0x28 這些數字從何而來，比死背可靠得多。

> **為什麼要自己用 `dt` 對？** 因為這些 offset **會隨 Windows 版本漂移**。網路上 2010 年教學的 offset 不保證在你的 Win10 上一樣。`dt` 是你環境的最終仲裁——OSED 考場也是在特定版本上，養成「用 dt 確認」的習慣能避免照抄舊數字翻車。

## 對比與取捨

| 找 kernel32 的方式 | 優點 | 缺點 |
|---|---|---|
| 走 InMemoryOrderModuleList（本章） | 經典、穩定、資料多 | offset 隨版本漂移，要用 dt 確認 |
| 走 InLoadOrderModuleList | 載入順序直覺 | 第一個常是程式自己，仍要過濾 |
| 比對完整模組名字串 | 直覺 | 字串佔空間、易含壞字元 |
| 比對模組名 hash | 省空間、避壞字元 | 要先算 hash、可讀性差（Ch 24 詳述） |

## 踩雷集錦

1. **「照抄舊教學的 offset」**：錯誤直覺是「offset 是固定的」。正確：它隨 Windows 版本/位元數會變，x64 更是整組不同。永遠用你目標環境的 WinDbg `dt` 確認一次。
2. **「節點指標當成結構開頭」**：鏈節點指向的是 `InMemoryOrderLinks` 欄位，不是 `LDR_DATA_TABLE_ENTRY` 開頭。取 DllBase 用 0x10、取名稱用 0x28，都是相對那個欄位。搞錯會讀到垃圾。
3. **「模組名是 ASCII」**：`BaseDllName` 是 `UNICODE_STRING`，Buffer 是寬字元（每字元 2 bytes）。比對時要按寬字元處理，否則比不中。
4. **「第一個模組就是 kernel32」**：不是。InMemoryOrderModuleList 第一個通常是程式自己，kernel32 在後面。一定要走鏈比對名稱/hash，不能取第一個。
5. **「x64 可以沿用這些 offset」**：不行。x64 用 `gs` 段、PEB/Ldr 的 offset 全不同（Ch 40 會談 x64 差異）。本章全是 x86。

## 進階：再往深一層

- **為什麼不直接 syscall？** 有些 shellcode 走更底層的 syscall（直接 `int 2e`/`sysenter` 進 kernel），繞過 kernel32 層。但 syscall 號隨版本漂移更嚴重（`windows_pwn` 有談），kernel32 路線相對穩定，OSED 層級夠用。
- **hash 比對的細節**：把模組名/函式名逐字元做 ROR13（向右旋轉 13 bits）累加成一個 32-bit 值，和預存 hash 比。這招讓 shellcode 裡不用放明文字串（避壞字元、省空間、也讓靜態分析難讀），Ch 24 解析 API 時完整展開。
- **防禦視角**：EDR 會監看「有程式在走 PEB 模組鏈 + 對匯出表做 hash 比對」這種不透過正常 `GetProcAddress` 找 API 的行為——這正是 shellcode 的指紋之一。理解走訪，就理解了偵測點在哪（見 `blue_team_dfir`、`malware_analysis`）。

## 本章重點整理

- shellcode 第一步是定位 kernel32，因為所有要用的 API 從它（或它帶路的 DLL）而來，且它一定已載入。
- 入口是 `fs:[0x30]`（位置無關）→ PEB → Ldr（+0x0C）→ InMemoryOrderModuleList（+0x14）。
- 走鏈：節點 +0x00=下一個、+0x10=DllBase、+0x28=模組名（寬字元）。節點指向結構中間欄位，offset 相對它算。
- offset 隨版本漂移，用 WinDbg `dt _PEB`/`dt _PEB_LDR_DATA`/`dt _LDR_DATA_TABLE_ENTRY` 親手確認。
- 比對模組名常用 hash（ROR13）取代明文字串，省空間、避壞字元。

## 自我檢核

- [ ] 不看筆記，能畫出 fs:[0x30] → PEB → Ldr → 模組鏈 → DllBase 這條路
- [ ] 能解釋為什麼用 `fs:[0x30]` 而不是寫死 PEB 位址（和 PIC 的關係）
- [ ] 能說出為什麼取 DllBase 的 offset 是 0x10 而不是結構開頭
- [ ] 知道該用哪三個 `dt` 命令驗證本章的 offset，為什麼不能照抄舊數字
- [ ] 能說出用 hash 比對模組名/函式名的兩個好處

## 延伸閱讀

- **[Corelan — Exploit writing tutorial part 9: Win32 shellcoding](https://www.corelan.be/index.php/2010/02/25/exploit-writing-tutorial-part-9-introduction-to-win32-shellcoding/)** — Peter Van Eeckhoutte
  - **讀哪裡**：「Finding kernel32 base address」整段，含逐行組語與多種走訪法。
  - **學什麼**：本章機制的逐行實作出處；想要可組譯的 asm 看這裡。
  - **前提**：Ch 3 組語、本章的結構圖。

- **[skape — Understanding Windows Shellcode](https://www.hick.org/code/skape/papers/win32-shellcode.pdf)**
  - **讀哪裡**：找 kernel32 與解析匯出的章節。
  - **學什麼**：最早系統化整理 Win32 shellcode 技術的論文，offset 與 hashing 的源頭。
  - **注意**：年代久，offset 以你環境 `dt` 為準；觀念與方法仍適用。

- **[WinDbg `dt` 與 PEB/TEB 結構 — Microsoft Learn](https://learn.microsoft.com/en-us/windows-hardware/drivers/debuggercmds/dt--display-type-)**
  - **讀哪裡**：`dt` 命令用法；搭配搜尋 `_PEB`/`_PEB_LDR_DATA`/`_LDR_DATA_TABLE_ENTRY` 結構定義。
  - **學什麼**：如何在你自己環境印出這些結構與真實 offset——本章「親手驗證」段落的依據。

- **本 repo [`windows_pwn` Ch 25](../windows_pwn/25-windows-shellcode.md) 與 [`malware_analysis`](../malware_analysis/README.md)**
  - **和本章的關聯**：前者是同主題的另一份教材；後者從分析/偵測端看「惡意碼如何找 API」——攻防同一套知識。

定位到 kernel32 之後，下一章解析它的匯出表，把 `GetProcAddress`、`LoadLibraryA` 這些 API 的實際位址挖出來——並用 hash 比對避開明文字串。

→ [Ch 24 export table 解析 + hash API resolution](./24-export-table-hash-resolution.md)
