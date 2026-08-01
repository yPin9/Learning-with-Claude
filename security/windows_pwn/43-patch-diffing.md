# Ch 43 — 找洞：Patch Tuesday patch diffing

> **目標**：掌握 Windows 上從補丁還原漏洞（1-day research）的完整方法論——取得補丁前後的二進位、用 BinDiff / Diaphora / Ghidra 找出被修改的程式碼、從 diff 回推漏洞成因、定出 PoC 策略。理解這套技能組和 Linux 開源找洞的根本差異，以及 1-day 窗口期的現實。

## 為什麼需要這個？

你在 Linux 上找洞的主要工具是**原始碼**。你 `git log`，找到一個看起來像 security fix 的 commit，`git diff` 看改了什麼，然後直接讀 C code 理解漏洞。這套流程在 Windows 上完全失效——微軟不公開 Windows 的原始碼。

Patch Tuesday（每月第二個星期二，微軟發布安全更新的日期）會修掉一批洞，並在 MSRC（Microsoft Security Response Center）公告 CVE。公告通常只給你一行話：「Windows XYZ 元件存在記憶體損壞漏洞，CVSS 8.8，遠端可未授權利用。」沒有任何技術細節。

**但補丁本身就是線索**。補丁前和補丁後的 DLL 二進位不一樣——差在哪裡，漏洞就在哪裡。這個逆向推導過程叫 **patch diffing**，是 Windows 安全研究的核心技能之一。

### 為什麼有人要做 patch diffing？

動機有幾個層次：

1. **漏洞研究**：搞清楚 Microsoft 修了什麼洞，理解攻擊面。
2. **1-day exploit 開發**：補丁發布到用戶修補的時間窗口（通常數週到數月），在這個窗口裡用已知漏洞打未修補系統。
3. **防禦確認**：確認你的系統是否受這個漏洞影響，評估影響範圍。
4. **學習**：最好的漏洞學習材料之一——你知道「有洞，就在這個元件裡」，逆向壓力小很多。

> 本章內容是教育性研究方法論，目的是理解現代 Windows 漏洞研究的方式。本課程聚焦防禦理解與研究技能，不鼓勵在未授權系統上實施 exploit。

## 先建立直覺

把整個流程想成「找不同遊戲」，但在二進位層級：

```
  Patch Tuesday 流程全景
  ┌──────────────────────────────────────────────────────────────────┐
  │                                                                   │
  │  微軟修洞                                                         │
  │  漏洞在 ntdll.dll!RtlHeapFoo()                                   │
  │                                                                   │
  │   修補前 ntdll.dll             修補後 ntdll.dll                   │
  │  ┌──────────────────┐         ┌──────────────────┐               │
  │  │ RtlHeapFoo:      │         │ RtlHeapFoo:      │               │
  │  │   push rbp       │         │   push rbp       │               │
  │  │   mov ...        │  diff   │   mov ...        │               │
  │  │   lea rax, buf   │ ──────► │   lea rax, buf   │               │
  │  │   call memcpy    │ ◄ 不同 ►│   cmp len, 0x100 │  ← 修補點    │
  │  │   ret            │         │   jg  .too_big   │               │
  │  └──────────────────┘         │   call memcpy    │               │
  │                               │   ret            │               │
  │                               └──────────────────┘               │
  │                                                                   │
  │  diff 告訴你：「在 RtlHeapFoo 裡，memcpy 前面多了一個長度檢查」    │
  │  → 修補前沒有這個檢查 → 可以傳 len > 0x100 觸發 heap overflow     │
  └──────────────────────────────────────────────────────────────────┘
```

從 diff 到漏洞，要做三件事：找到修補點（差在哪）、理解為什麼改（缺了什麼檢查）、回推可觸發路徑（怎麼讓程式走到那裡）。

## 底層機制一：取得補丁前後的二進位

### Windows 更新的格式

微軟的更新以幾種格式分發：

- **MSU（Windows Update Standalone Installer）**：完整的更新包，含多個 .cab 和 .xml
- **MSP（Microsoft Patch/Installer Patch）**：MSI installer 的 delta patch
- **WIM / ESD**：映像更新，用在 Feature Update（版本升級）
- **Delta compression binary**：`msdelta` 格式，MSU 裡的 DLL 差異通常是以 delta 形式存放

最重要的是：**MSU 裡面的 DLL 不是完整 DLL，是 delta**。你要展開 delta 才能得到完整的修補後二進位。

```
  MSU 展開流程
  ┌─────────────────────────────────────────────────────────────┐
  │                                                              │
  │  Windows10.0-KB5XXXXXX-x64.msu                               │
  │       │                                                      │
  │       │ expand 或 wusa /extract                              │
  │       ▼                                                      │
  │  Windows10.0-KB5XXXXXX-x64.cab                               │
  │       │                                                      │
  │       │ expand 展開 .cab                                     │
  │       ▼                                                      │
  │  amd64_microsoft-windows-ntdll_...   (目錄名含版本號)         │
  │       ├── ntdll.dll.delta            ← delta 格式，不是完整 DLL  │
  │       └── ...                                                │
  │                                                              │
  │  delta + 原始 DLL → ApplyDelta() → 完整修補後 DLL            │
  └─────────────────────────────────────────────────────────────┘
```

### 展開 MSU 的具體步驟

```bat
REM 步驟 1：展開 MSU → CAB
expand -F:* Windows10.0-KB5XXXXXX-x64.msu C:\extracted\

REM 步驟 2：展開 CAB → 各元件目錄
expand -F:* Windows10.0-KB5XXXXXX-x64.cab C:\extracted\

REM 步驟 3：找到目標元件的 .delta 檔
dir /s /b C:\extracted\*.delta | findstr ntdll
REM 輸出類似：
REM C:\extracted\amd64_microsoft-windows-ntdll_...\ntdll.dll.delta

REM 步驟 4：套用 delta（需要原始 DLL + delta → 修補後 DLL）
REM 原始 DLL 從未修補系統的 C:\Windows\System32\ntdll.dll 取得
```

> **未實測，理論預期**：以上 `expand` 指令語法在 Windows 11 Pro 上應可用（`expand.exe` 內建在 System32）；`msdelta` 套用步驟需要 `delta_patch.exe`（Win SDK）或 msdelta-python 等工具，視 delta 格式版本而定。

### 使用 msdelta 套用 delta

微軟的 `msdelta` 是用於套用 delta 壓縮的工具：

```python
# 用 msdelta Python binding 套用 delta（未實測，理論預期）
# pip install msdelta 或使用 cabarchive + ApplyDeltaB API
import ctypes

MSDelta = ctypes.windll.msdelta
# ApplyDeltaB(lpdwFlags, Source, Delta, lpTarget)
# 詳細參數見 Windows SDK 的 msdelta.h
```

實務上更常用的方法是：**直接用 WimLib 或 7-Zip 展開 WIM 映像取得完整 DLL**，或使用 **winbindex** 取得已展開的版本。

### winbindex：直接按版本下載 DLL

[winbindex](https://winbindex.m417z.com/) 是 m417z 維護的 Windows binary index，按 KB 編號、版本號索引了大量 Windows 系統 DLL 的完整二進位。

```
  winbindex 使用方式
  ┌─────────────────────────────────────────────────────────────┐
  │  1. 搜尋 DLL 名稱（e.g., "ntdll.dll"）                      │
  │  2. 選擇版本號（build number）：                             │
  │       10.0.19041.XXX  ← 補丁前                             │
  │       10.0.19041.YYY  ← 補丁後                             │
  │  3. 點「Download」直接取得完整 DLL                            │
  │                                                              │
  │  重要：winbindex 收錄的 DLL 是從 Microsoft symbol server     │
  │  下載的完整 DLL（不是 delta）。合法的 Microsoft 資源。         │
  └─────────────────────────────────────────────────────────────┘
```

**這是 patch diffing 的最省力路徑**：不用自己展開 MSU，直接在 winbindex 按版本號下載修補前後兩個版本的 DLL，然後進 BinDiff。

### 確認版本號的方法

```powershell
# 看目前系統的 ntdll.dll 版本（True output 直接用）
(Get-Item C:\Windows\System32\ntdll.dll).VersionInfo.FileVersion
# 輸出類似：10.0.19041.4355

# 找 KB 對應的版本號：
# MSRC 公告 → Microsoft Update Catalog → 搜 KB 號 → 看 "Version" 欄位
```

## 底層機制二：binary diffing 工具

### BinDiff

BinDiff 是 Zynamics（現 Google）開發的商業級二進位比對工具，現在免費提供。它的比對演算法核心是**函式匹配（function matching）**。

```
  BinDiff 比對流程
  ┌─────────────────────────────────────────────────────────────┐
  │                                                              │
  │  ntdll_old.dll → IDA/Ghidra 分析 → ntdll_old.idb           │
  │  ntdll_new.dll → IDA/Ghidra 分析 → ntdll_new.idb           │
  │                                                              │
  │         BinDiff 接這兩個 .idb                                 │
  │                     │                                        │
  │                     ▼                                        │
  │  ┌──────────────────────────────────────────────────────┐   │
  │  │  函式匹配演算法：                                       │   │
  │  │  1. 名稱匹配（有 symbols 時最準）                       │   │
  │  │  2. 函式 hash：MD index（指令序列 hash）                │   │
  │  │  3. call graph 拓撲：相同的 caller/callee 結構          │   │
  │  │  4. basic block 計數、邊數                              │   │
  │  └──────────────────────────────────────────────────────┘   │
  │                     │                                        │
  │                     ▼                                        │
  │  輸出：                                                       │
  │  ● Similarity 100% 的函式：未修改                             │
  │  ● Similarity 95% 的函式：修改過（patch 點在這裡）            │
  │  ● 僅在新版存在的函式：新增（有時是把邏輯拆出來加驗證）        │
  └─────────────────────────────────────────────────────────────┘
```

**BinDiff 的核心數字：Similarity（相似度）**

Similarity 0.0–1.0，1.0 代表 basic block 內容和控制流完全相同。在 patch diffing 場景，你要找的是：

- **0.8–0.99**：修改過的函式，很可能是 patch 點
- **0.5–0.8**：大幅重寫，可能是重要的邏輯改動
- **0.0（僅在一版存在）**：新增或移除的函式

BinDiff 的 UI 在 IDA 外掛或獨立 GUI 裡操作，核心動作：

```
  1. File → Diff Binaries → 選兩個 .idb
  2. Functions 視窗：按 Similarity 排序，找低於 1.0 的函式
  3. 雙擊低相似度函式 → 打開 flow graph diff 視窗
     左邊：舊版；右邊：新版；差異的 basic block 用顏色標出
  4. 找到差異 basic block → 看新版多了什麼指令
```

> **未實測，理論預期**：BinDiff GUI 和 IDA 外掛的操作流程基於 BinDiff 7.0 官方文件；Ghidra BinDiff 整合需 bindiff-ghidra 外掛。

### Diaphora

Diaphora 是 Joxean Koret 開發的開源 IDA 外掛（Python），功能和 BinDiff 類似但免費，對有 symbols 的 Windows 二進位效果很好。

```python
# Diaphora 啟動方式（在 IDA 的 Script → Run Script...）
# 載入 diaphora.py → 設定輸出 .sqlite 檔 → 對兩個 .idb 各跑一次 → 比對兩個 .sqlite
```

**Diaphora 對比 BinDiff 的差異**：

| 維度 | BinDiff | Diaphora |
|------|---------|----------|
| 授權 | 免費（Google） | 開源（MIT） |
| 平台 | IDA + Ghidra 外掛 | IDA（主），有 Ghidra port |
| 精準度 | 業界公認最高 | 接近，對有 debug info 的目標有時更好 |
| 速度 | 快 | 較慢（Python） |
| 比對粒度 | 函式、basic block | 函式、basic block，額外有 pseudocode diff |
| 主動維護 | Google 維護 | Joxean 個人維護（更新不規律） |

對大多數 patch diffing 工作，BinDiff 是首選，Diaphora 作為備選或交叉驗證。

### Ghidra 的 Version Tracking

Ghidra 內建的 Version Tracking 功能是免費版的 patch diff 工具。不需要 IDA 授權。

```
  Ghidra Version Tracking 流程
  ┌─────────────────────────────────────────────────────────────┐
  │  1. 把兩個 DLL 都匯入同一個 Ghidra project                   │
  │  2. Tools → Version Tracking                                 │
  │  3. 選「Source Program」（舊版）與「Destination Program」（新版） │
  │  4. Run Correlators（相關性演算法）：                         │
  │       - Exact Symbol Name Match → 有 symbols 時先跑         │
  │       - Exact Function Instructions → 完全相同的函式         │
  │       - Bulk Instructions Correlator → 找相似函式            │
  │  5. 未匹配的函式 → 手動審查                                   │
  │  6. 找到差異函式 → 對比兩側 disassembly / decompile           │
  └─────────────────────────────────────────────────────────────┘
```

**Ghidra Version Tracking 的硬傷**：比 BinDiff 的比對演算法弱，對大型二進位（ntdll.dll 這種）需要很長時間，且 "unmatched" 函式清單容易有雜訊。但對預算有限或沒有 IDA 授權的研究者，它是可用的起點。

### 三種工具的比對粒度圖示

```
  比對粒度層次
  ┌───────────────────────────────────────────────────────┐
  │                                                        │
  │  Level 1：函式層級（所有三種工具都做）                   │
  │  ────────────────────────────────────────────────────  │
  │  RtlAllocateHeap:  similarity 0.97 ← 有修改            │
  │  RtlFreeHeap:      similarity 1.00 ← 未修改            │
  │  RtlQueryHeap:     similarity 0.50 ← 大幅改動          │
  │                                                        │
  │  Level 2：Basic Block 層級（BinDiff / Diaphora 做）     │
  │  ────────────────────────────────────────────────────  │
  │  RtlAllocateHeap 的 flow graph：                       │
  │  ╔══════════════╗  vs  ╔══════════════════════╗        │
  │  ║ BB_01 (相同) ║      ║ BB_01 (相同)          ║        │
  │  ║ BB_02 (相同) ║      ║ BB_02 (相同)          ║        │
  │  ║ BB_03 (相同) ║      ║ BB_03 (不同) ←patch   ║        │
  │  ║ BB_04 (相同) ║      ║ BB_03b (新增) ←patch  ║        │
  │  ╚══════════════╝      ║ BB_04 (相同)          ║        │
  │                        ╚══════════════════════╝        │
  │                                                        │
  │  Level 3：指令層級（BinDiff flow graph diff 最細）       │
  │  ────────────────────────────────────────────────────  │
  │  BB_03 差異：                                           │
  │  舊：call memcpy                                        │
  │  新：cmp rdx, 0FFFFh                                   │
  │      ja  error_too_large                               │
  │      call memcpy                                       │
  └───────────────────────────────────────────────────────┘
```

## 方法論：從 diff 到漏洞成因

找到修改的 basic block 之後，要做的不是「看到 diff 就知道漏洞」，而是系統地逆向推導。

### 步驟一：定性 diff 的類型

修補通常屬於幾種固定模式：

```
  Patch 類型模式庫

  模式 A：加入邊界檢查
  ────────────────────
  舊版：
    imul rax, rcx, 8   ; size * 8，沒有溢位檢查
    call malloc
  新版：
    imul rax, rcx, 8
    jo   overflow_handler   ; ← 新加：Jump if Overflow
    call malloc
  → 整數溢位，乘法結果溢位導致 malloc 申請的 size 過小

  模式 B：加入長度限制
  ────────────────────
  舊版：
    call memcpy          ; 長度從呼叫者傳入，無驗證
  新版：
    cmp rdx, MAX_LEN
    ja  .error
    call memcpy          ; ← 長度現在有上限
  → 傳統 buffer overflow，長度未驗證

  模式 C：加入 NULL / 指標驗證
  ────────────────────────────
  舊版：
    mov rax, [rcx]       ; 直接解引用 rcx
  新版：
    test rcx, rcx
    jz   .null_path      ; ← NULL check
    mov rax, [rcx]
  → NULL deref（通常 severity 較低）

  模式 D：Use-after-free 修補
  ─────────────────────────
  舊版：
    call Free(ptr)
    ; ... 後面還 access ptr
  新版：
    call Free(ptr)
    mov ptr, 0           ; ← 清空指標，防止再次存取
  → UAF，free 後沒清指標

  模式 E：型別混淆修補
  ────────────────────
  舊版：
    ; 物件 type check 過於寬鬆
    call [vtable + offset]   ; 任何 type 都能到這裡
  新版：
    cmp [obj+type_offset], EXPECTED_TYPE
    jne .type_error
    call [vtable + offset]
  → type confusion
```

### 步驟二：找出可觸發路徑

知道「缺了什麼檢查」之後，要問的是：**呼叫者能控制那個缺少驗證的參數嗎？**

這需要逆向追蹤 call graph——從修補的函式往上走，找到「這個函式接受什麼輸入、輸入從哪來」。

```
  Call graph 往上追蹤
  ┌─────────────────────────────────────────────────────────┐
  │                                                          │
  │  RtlHeapFoo(buf, len)   ← 修補點：len 沒驗證            │
  │       ↑                                                  │
  │       呼叫者 A: RtlProcessHeapEntry(...)                 │
  │             ↑                                            │
  │             呼叫者 B: HeapWalk(hHeap, lpEntry)           │
  │                   ↑                                      │
  │                   呼叫者 C: 程式呼叫 HeapWalk            │
  │                                                          │
  │  HeapWalk 是 Win32 public API → 任何行程都能呼叫         │
  │  → 攻擊者能從 user code 控制 HeapWalk 的 heap 內容       │
  │  → heap 內容的某個欄位最終成為 len → 可控 → overflow     │
  └─────────────────────────────────────────────────────────┘
```

**關鍵問題**：

- 修補的函式接受哪些參數？這些參數從哪裡來？
- 攻擊者能控制的輸入（網路封包、檔案內容、API 呼叫）能走到這裡嗎？
- 有多少層函式隔開攻擊者輸入和漏洞點？每層有沒有額外的過濾？

### 步驟三：評估可利用性

並非所有 patch diff 找到的漏洞都值得追。快速評估：

```
  可利用性評估決策樹
  ┌─────────────────────────────────────────────────────────┐
  │                                                          │
  │  漏洞型別是什麼？                                         │
  │  ├── stack/heap overflow with write → 繼續               │
  │  ├── integer overflow → 看它導致的結果                   │
  │  │      └── 結果是 malloc 申請過小 → overflow → 繼續     │
  │  ├── NULL deref → 通常 DoS，不繼續（除非核心態）          │
  │  └── UAF → 繼續                                         │
  │                                                          │
  │  攻擊者能控制的資料量多少？                                │
  │  ├── 完全可控（例如檔案內容） → 繼續                       │
  │  ├── 部分可控（限制 size/type） → 看限制有多嚴             │
  │  └── 不可控 → 放棄                                       │
  │                                                          │
  │  緩解機制狀態（需搭配 Ch 32–39）？                         │
  │  ├── CFG 開著：indirect call 類漏洞難搞                   │
  │  ├── heap metadata encoding（Ch 17）：heap overflow 利用難  │
  │  └── ASLR + DEP → 需要 info leak                        │
  └─────────────────────────────────────────────────────────┘
```

## 具體流程示範：CVE-2021-31166 方法論

選一個概念上清楚的公開案例作方法論示範——**CVE-2021-31166（HTTP Protocol Stack Remote Code Execution，http.sys）**。這個洞在 2021 年 5 月 Patch Tuesday 公開，很快就有研究者從 patch 逆出根因。

> 本節只講方法論，不提供可用的 exploit payload。漏洞已在 2021 年 5 月修補超過三年，此為歷史教育案例。

### 背景

MSRC 公告：「HTTP Protocol Stack Remote Code Execution Vulnerability，CVSS 9.8，無需認證，unauthenticated attacker 透過惡意 HTTP request 觸發。」元件：`http.sys`（Windows HTTP 核心驅動，不是 IIS）。

### Patch diffing 步驟

**步驟 1：取得補丁前後的 `http.sys`**

```
  CVE-2021-31166 版本資訊（公開）
  修補前：KB5003171 未安裝前的 http.sys（build 19041.985 之前）
  修補後：安裝 KB5003171 後的 http.sys

  winbindex 搜 "http.sys" → 找 10.0.19041.98X 和 10.0.19041.98Y 兩版
```

**步驟 2：IDA 各自分析、BinDiff 比對**

BinDiff 跑完，函式清單裡大部分 Similarity 1.0，少數低於 1.0 的函式就是候選。這個 CVE 的 diff 集中在 HTTP/2 的 header parsing 相關函式——研究者報告說修改集中在一個處理 chunked encoding 或 header trailer 的函式（確切函式名在有 symbols 的版本裡可見）。

**步驟 3：分析 diff 的語意**

研究者（如 Axel Souchet 的公開分析）觀察到修補加入了對某個計數值的邊界檢查，缺少這個檢查時可以觸發整數 wraparound，導致後續記憶體操作時使用了不正確的 size。

**步驟 4：追蹤觸發路徑**

`http.sys` 是核心態驅動，直接從網路 recv 資料，攻擊者送一個特製的 HTTP 請求就能到達漏洞點。這使得漏洞的觸發路徑非常短：

```
  攻擊者 HTTP 請求
       │
       ▼ (TCP/IP 堆疊)
  http.sys 核心態驅動
       │
       ▼ HTTP/2 header 解析
  漏洞函式：整數計數 wraparound
       │
       ▼ 記憶體損壞（核心池）
  Kernel Pool Overflow
```

因為是 kernel mode，沒有 user space 的 CFG/ASLR 等緩解問題；但 Windows 10 kernel 有自己的緩解（kernel ASLR、kASLR、Safe Unlinking 等）。

**研究者最終輸出**：公開 PoC（觸發 BSOD 的 PoC，不是完整 RCE exploit），提交到 GitHub 並附方法論文章。

> 這個案例的重要教學點不是漏洞本身，而是**「Patch Tuesday → BinDiff → 24 小時內有公開 PoC」**這個速度——說明 patch diffing 不是高深技術，是有系統地做是能快速執行的。

### 更適合入門的 userland 案例

`http.sys` 是核心態的（kernel pool corruption），對本課讀者來說略超前（Windows kernel 是另一門課的主題）。Userland 場景下的 patch diffing 方法論完全相同，只是目標換成 `ntdll.dll`、`comctl32.dll`、`mshtml.dll`（老 IE 洞）、Office 元件等。

Cisco Talos、Project Zero 發布的 blog post 裡有大量 userland patch diffing 範例（延伸閱讀給了連結），可以拿公開案例練習「從 patch 分析到明白漏洞成因」這條路。

## 1-day 的價值與時間窗

### 時間窗的現實

```
  1-day 時間窗口分析
  ──────────────────────────────────────────────────────────
  Day 0：Patch Tuesday 發布（每月第二個星期二）
       │
       │ 研究者開始 diff 分析
       │ 最快的：2–48 小時內出現概念 PoC
       │
  Day 2–7：第一批 PoC 公開（技術社群）
       │
       │ 企業 patch cycle：
       │   快的：2–4 週完成部署
       │   一般企業：4–8 週（測試、部署）
       │   政府/工控：幾個月到一年
       │
  Day 30+：大量企業仍未修補
       │
       │ 時間窗口越寬，「1-day」越接近「0-day」的實際危害
       │
  Day 90+：大多數企業的補丁覆蓋率才達到高比例
```

**CVSS ≥ 9.0 的高危漏洞**：這類洞研究者會在幾天內 diff 完，PoC 快速公開，威脅情報廠商追蹤，但實際修補進度仍然緩慢。這就是為什麼「1-day exploit 開發」在漏洞研究和紅隊界有實際市場。

### 1-day vs 0-day 的技術差異

| 維度 | 0-day（未公開漏洞） | 1-day（已修補但 diff 可逆） |
|------|------------------|--------------------------|
| 資訊來源 | 自己找漏洞 | Patch Tuesday diff |
| 投入時間 | 數週到數月 | 數小時到數天 |
| 技術難度 | 極高（漏洞發現） | 中（二進位分析 + 逆向） |
| 目標環境 | 任何版本 | 特定舊版（未修補） |
| 倫理/法律風險 | 更高（合法披露或出售） | 視使用場景 |

Patch diffing 能做到的是「**從補丁逆推漏洞成因**」——這個技能同時服務於漏洞研究（學習怎麼寫更好的 security bug）和防禦（評估自己暴露面）。

## 對比 Linux 開源找洞

| 維度 | Linux（開源） | Windows（閉源） |
|------|-------------|----------------|
| 資訊來源 | git log / git diff / kernel commit | Patch Tuesday MSU + binary diff |
| 找洞工具 | grep / CodeQL / Semgrep（原始碼層級） | BinDiff / Diaphora / Ghidra（二進位層級） |
| 漏洞成因精度 | 原始碼直接讀 | 逆向推斷，有時不確定 |
| 觸發路徑追蹤 | 原始碼 call graph | 逆向 call graph（IDA / Ghidra） |
| 緩解機制資訊 | 原始碼裡的 `__attribute__` / compile flag | `dumpbin /loadconfig`、PE header |
| 找洞速度 | 快（直接讀 C） | 慢（要逆向每個函式） |
| 洞的價值 | 競爭激烈（很多人在看同一份源碼） | 競爭較低（逆向有門檻） |

**關鍵差異**：在 Linux 上，你可以直接看到「刪掉了哪一行、加了哪一行 C 程式碼」；在 Windows 上，你看到的是「assembly 改了什麼」——這需要更強的逆向能力，也是 Windows 安全研究的門檻比 Linux 高的原因之一。

## 踩雷集錦

1. **「BinDiff similarity 1.0 的函式一定沒被修改」**：不完全對。如果微軟在修補時把漏洞邏輯移到一個**全新的輔助函式**裡，然後從舊函式呼叫這個新函式，舊函式只加了一個 `call new_function`，BinDiff 可能把舊函式標為高相似度但新函式是「新增」。**一定要同時看「僅存在於新版的函式」**。

2. **「用 delta 展開出來的 DLL 比直接從 winbindex 下載的小」**：delta 是差異壓縮，展開後應該等同完整 DLL。如果大小不對，通常是 delta 的 base 版本不符——`msdelta` 的 delta 對 base 版本有綁定，用錯 base 會展開失敗或得到錯誤結果。改用 winbindex 避免這個問題。

3. **「IDA 分析兩個大型 DLL（如 ntdll.dll）跑 BinDiff 要等很久」**：正常現象。ntdll.dll 有數千個函式，BinDiff 的 call graph 匹配是 O(n²) 量級。實務技巧：先用 diff 的 quick mode（只比 MD index），得到低相似度函式清單，再只對這些函式做深度分析。或先用 winbindex 確認改了哪個版本的 `FileVersion`，用 strings 或 import 表縮小目標 DLL 範圍。

4. **「找到了 diff 但不確定怎麼觸發」**：這是 patch diffing 最難的一步，二進位分析只告訴你「改了什麼」，沒有告訴你「怎麼到達那裡」。解法：找到修補函式的 exported name（有 symbols 最好），用 IDA 的 cross-reference（`xref to`）往上追 caller chain，配合 MSRC 公告的「affected feature/component」縮小範圍。有時候公告裡的技術分類（如「Heap Buffer Overflow in Windows Kerberos」）已經夠指向性了。

5. **「Ghidra Version Tracking 結果雜訊太多，找不到真正的 diff」**：Ghidra 的相關性演算法比 BinDiff 弱。實用策略：先用 Ghidra 做粗篩（找 unmatched 函式），再把候選函式放到 BinDiff 或 Diaphora 做細比對。或直接用 `bindiff` CLI 工具（BinDiff 7+ 提供命令列版）批次處理，不依賴 GUI。

## 進階：再往深一層

### 半自動化 patch diffing pipeline

當你要追蹤每個月的 Patch Tuesday（幾十個 CVE），手動一個個 diff 不現實。自動化思路：

```python
# 半自動化流程骨架（理論）
# 1. 從 MSRC API 取得本月 CVE 清單
# 2. 用 winbindex API 批次下載修補前後 DLL
# 3. 用 BinDiff CLI 批次比對，輸出 CSV 結果
# 4. 按 similarity score 排序，把 < 0.95 的函式清單輸出
# 5. 人工審查最低相似度的前 N 個函式

import requests

def get_monthly_cves(year, month):
    # MSRC security updates API
    url = f"https://api.msrc.microsoft.com/cvrf/v2.0/updates/{year}-{month:02d}"
    # 返回當月 CVE 清單與受影響元件
    ...
```

這個方向被 pwn2own 參賽者和頂尖 Windows 研究者（如 Maddie Stone, Tavis Ormandy）在內部工具化，公開工具有 `winbindex-dl` 等腳本。

### 使用 bindiff CLI 做批次比對

```bat
REM BinDiff 7+ 的 CLI（未實測，理論預期）
bindiff --primary ntdll_old.BinExport --secondary ntdll_new.BinExport
REM 輸出 .BinDiff 結果檔案
REM 先要用 IDA + BinDiff plugin 匯出 .BinExport（Binexport format）
```

### 面試題準備

- **「patch diffing 和直接讀 Linux commit 有什麼本質差異？」**：精度和效率。Linux commit 直接給原始碼 diff，Windows 要從彙編推回語意——訓練的是更底層的逆向能力，但速度慢一個數量級。

- **「如何快速確定一個 Patch Tuesday CVE 影響哪個 DLL？」**：MSRC 公告的 "Affected Products" 有時只給「Windows 10」，不給元件。方法：用 `winbindex` 對照補丁前後的版本號；用微軟的 MSRC Security Update Guide API 查 KB 號；對於 CVSS ≥ 9 的 RCE 洞，威脅情報廠商（Qualys、Rapid7）通常會在當天發技術 blog 指出元件。

- **「BinDiff 的 similarity 0.97 和 0.50 的函式，哪個更可能是主要 patch 點？」**：不一定。0.97 代表只有細微改動（加了一個 check），0.50 代表大幅重寫。前者更可能是「精準修補一個 check」，後者可能是整個邏輯重構或把一個函式拆成多個。實際上，最精準的 security fix 通常是加幾行 check → similarity 很高（0.9+），比大幅重寫更值得注意。

## 動手練習

選擇一個**已公開超過 2 年的 Windows CVE**（建議從 MSRC 2021–2022 的中等 CVSS 洞選），進行方法論演練：

1. **找 DLL 版本**：用 MSRC Security Update Guide 找到 CVE 對應的 KB 號，在 winbindex 搜尋受影響的 DLL，下載修補前後兩個版本。
2. **IDA / Ghidra 分析**：分別分析兩個 DLL，有 symbols 就設好 `_NT_SYMBOL_PATH` 讓工具自動載入。
3. **BinDiff 或 Diaphora 比對**：找到 Similarity < 0.99 的函式清單，按相似度排序。
4. **分析最低相似度的函式**：打開 flow graph diff，找到具體改變的 basic block，嘗試說出「改動的語意是什麼」（加了什麼驗證？）。
5. **對照公開分析**：搜這個 CVE 的公開 blog post（Talos、Project Zero、Qualys 等），驗證你的分析是否和公開資訊一致。

這個練習不需要寫任何 exploit，目標是走完一遍「補丁→diff→成因理解」的流程。

## 本章重點整理

- **Patch diffing 的基礎邏輯**：微軟修的是二進位，diff 出修改點，修改點就是漏洞的修補地點，逆推就能還原漏洞成因。
- **取得二進位的路徑**：MSU/expand/msdelta 展開 delta patch，或直接用 winbindex 按版本號下載完整 DLL。
- **工具選擇**：BinDiff 精度最高（首選）；Diaphora 是開源替代；Ghidra Version Tracking 是無 IDA 授權時的備選。
- **Diff 分析的核心動作**：找低 similarity 函式 → flow graph diff 定位修改 basic block → 分析修改語意（加了什麼驗證）→ call graph 往上追觸發路徑。
- **與 Linux 的對比**：Linux 直讀原始碼 commit，Windows 靠彙編逆向推斷，技術門檻高、競爭相對少。

## 自我檢核

- [ ] 不看筆記，能說出從一個 Patch Tuesday MSU 到拿到兩個可 diff 的 DLL，需要哪些步驟
- [ ] 能解釋 BinDiff 的 similarity 數字是什麼意思，0.97 和 0.50 各代表什麼量級的修改
- [ ] 能說出 patch diffing 的五種典型修補模式（加邊界檢查、加長度限制、加 NULL check、清指標防 UAF、加型別驗證），以及各自對應什麼漏洞型別
- [ ] 面試被問「Windows 上怎麼找 1-day？」能說出完整流程（MSRC → 版本號 → winbindex → BinDiff → 分析 → 觸發路徑）
- [ ] 能說出 patch diffing 和 Linux git diff 在精度與效率上的根本差異

## 延伸閱讀

### 工具 / 專案

- **[BinDiff 官方文件 — Google](https://www.zynamics.com/bindiff/manual/)**
  - **讀哪裡**：「Comparing Binaries」章節（整個 diff workflow）；「Matched Functions View」（如何解讀 similarity 數字）；「Flow Graph Diff」（basic block 層級對比）
  - **學什麼**：BinDiff 每個視窗的語意、如何從 IDA 匯出 .BinExport、如何解讀比對結果
  - **前提知識**：本章內容；基本 IDA Pro 操作（能分析 DLL）

- **[winbindex — m417z](https://winbindex.m417z.com/)**
  - **讀哪裡**：首頁的搜尋功能直接用；"About" 頁說明資料來源與版本索引機制
  - **學什麼**：如何按 build number / KB 號取得特定版本的 Windows DLL，不用自己展開 MSU
  - **和本章的關聯**：patch diffing 取得「修補前後 DLL」的最省力路徑

### 部落格 / 研究文章

- **zeifan（Zhiniang Peng）的 patch diffing 文章系列**
  - **讀哪裡**：zeifan.me 或 NCC Group 發表的 "Exploit Writeup" 類文章；特別找含「patch analysis」段落的
  - **學什麼**：真實案例的 patch diffing 流程、如何從 diff 推到 PoC 策略、call graph 追蹤技巧
  - **和本章的關聯**：本章方法論的實戰示範；讀一篇公開案例比讀十頁教程更有效

- **Cisco Talos 的 Patch Tuesday 分析文章 — [blog.talosintelligence.com](https://blog.talosintelligence.com/)**
  - **讀哪裡**：每個月 Patch Tuesday 後發布的 "Microsoft Patch Tuesday — Month YYYY" 系列文；找 CVSS ≥ 8.0 的條目看技術細節
  - **學什麼**：工業規模的 patch diffing 產出樣貌；如何快速分類「值得深追」vs「DoS only」的洞；漏洞成因的技術描述格式
  - **前提知識**：本章 patch 分析方法；基本 Windows 記憶體管理概念（Part 2）

- **"1day to 0day" — Project Zero（various authors）**
  - **讀哪裡**：Google Project Zero 部落格（googleprojectzero.blogspot.com）搜 "patch diffing" 或 "variant analysis"；Tavis Ormandy 的 Windows 漏洞分析文章
  - **學什麼**：頂級研究者的 patch diffing 方法論、variant analysis（從一個 patch 找相關函式有沒有類似問題）、如何評估 patch 的品質
  - **和本章的關聯**：「patch diffing 進階」——不只找被修的洞，還找修補不完整的地方

---

本課的「找洞」工具技能至此完整。接下來最後一個 Part，我們換換口味——把視角從「打進去」移到「打進去之後能做什麼」，碰一點 Windows 提權的地基：token、SID、integrity level。

→ [Ch 44 — access token 模型：SID / privileges / integrity level](./44-access-token-model.md)
