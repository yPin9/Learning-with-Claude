# Ch 42 — fuzzing on Windows：WinAFL / TTD-based

> **目標**：學會在 Windows 上設計與執行覆蓋率導向的 fuzzing 工作流——包括 WinAFL 的三種插樁模式取捨、persistent mode harness 撰寫、語料管理與 crash triage；理解 TTD-based fuzzing 的概念；並能把你熟悉的 AFL++ 知識直接對照過來，不從零開始。

> **環境**：Windows 11 Pro x64。WinAFL 需搭配 DynamoRIO 或 Intel PT 支援。工具鏈安裝步驟標注「未實測，理論預期」。純概念與架構分析段可直接閱讀。

## 為什麼需要這個？

你用 AFL++ 在 Linux 上 fuzz 過。那套邏輯——插樁二進位、啟動 fork server、feed 輸入、觀察覆蓋率、保留觸發新 path 的樣本——在 Linux 效果極好，原因之一是 Linux 有 `fork()`：

```
AFL++ on Linux
   fuzzer process
       │
       ├── fork() ← 超快，copy-on-write，複製整個行程狀態
       │       │
       │       └── child: feed input → execute → measure coverage → die
       │
       └── 下一輪：再 fork()，行程從 "post-init" 狀態重生
```

**Windows 沒有 `fork()`**。`CreateProcess()` 的代價比 `fork()` 高出一到兩個數量級——要走完整個 loader 流程（PE 載入、DLL 初始化、TLS 回呼），一輪測試動輒多幾百毫秒。對 fuzzer 而言這是災難性的，因為效率的瓶頸直接從「產生輸入」變成「啟動行程」。

第二個問題：**閉源目標多**。Linux 上你能拿到 nginx、sqlite、libpng 的原始碼，直接用 `afl-gcc`/`afl-clang` 插樁編譯。Windows 上你想 fuzz 的目標常是 `windows.storage.dll`、`msxml6.dll`、Office 解析器、PDF 閱讀器——沒有原始碼，只能在二進位層級插樁。

這兩個問題催生了 WinAFL：把 AFL 的覆蓋率導向 fuzzing 搬到 Windows，用動態二進位插樁解決閉源問題，用 **persistent mode** 解決 `CreateProcess()` 代價問題。

## 先建立直覺

WinAFL 整體流程和 AFL 是同一件事，差在「覆蓋率怎麼收集」和「行程怎麼重用」：

```
                      WinAFL 大流程
  ┌─────────────────────────────────────────────────────────┐
  │  fuzzer (afl-fuzz.exe)                                  │
  │                                                         │
  │  ┌─────────────┐    shared memory (bitmap)              │
  │  │  input gen  │ ──────────────────────────────────┐   │
  │  │  (mutate)   │                                   │   │
  │  └──────┬──────┘                                   │   │
  │         │ test case file                           │   │
  │         ▼                                          ▼   │
  │  ┌──────────────────────────────────────────────────┐  │
  │  │  target process (persistent mode)                │  │
  │  │                                                  │  │
  │  │  [DynamoRIO / IntelPT / syzygy 在這裡收覆蓋率]   │  │
  │  │                                                  │  │
  │  │  ┌──────────────────────────────────────────┐   │  │
  │  │  │  for 每一輪：                             │   │  │
  │  │  │    read_input()  ← 從共享記憶體或檔案讀  │   │  │
  │  │  │    target_func() ← 你要 fuzz 的那個函式  │   │  │
  │  │  │    reset_state() ← 清理狀態               │   │  │
  │  │  └──────────────────────────────────────────┘   │  │
  │  │                                                  │  │
  │  │  崩潰 → 通知 fuzzer / 存 crash                   │  │
  │  └──────────────────────────────────────────────────┘  │
  └─────────────────────────────────────────────────────────┘
```

「persistent mode」就是讓 target process **活著、反覆跑**，不用每次 `CreateProcess()`。這是 WinAFL 效能的命脈。

## 底層機制：三種插樁模式

WinAFL 支援三種截然不同的方式收集覆蓋率：

```
  插樁模式比較
  ┌──────────────┬──────────────────────────────────┬─────────────────────────────┐
  │              │  DynamoRIO                        │  Intel PT                   │
  │              │  (動態二進位插樁)                  │  (硬體追蹤)                  │
  ├──────────────┼──────────────────────────────────┼─────────────────────────────┤
  │  syzygy      │                                  │                             │
  │  (靜態插樁)   │  執行時即時 JIT 插入探針           │  CPU 硬體收 branch 追蹤       │
  └──────────────┴──────────────────────────────────┴─────────────────────────────┘
```

### 模式一：DynamoRIO 動態插樁模式

DynamoRIO 是 MIT 出品的動態二進位插樁框架（類似 Valgrind，但效能更好）。WinAFL 附帶一個 DynamoRIO client（`winafl.dll`），在執行時為每個 basic block 的開頭插入一條「更新 bitmap」的指令。

```
    原始 basic block
    ┌─────────────────┐
    │  push rbp        │
    │  mov rbp, rsp    │
    │  ...             │
    └─────────────────┘

    DynamoRIO 插樁後（JIT cache 裡）
    ┌─────────────────────────────────┐
    │  mov rax, [bitmap_ptr]           │  ← 插入：
    │  xor rax, [prev_loc]            │      bitmap[(cur_block ^ prev_block) % SIZE]++
    │  inc byte [rax]                 │
    │  mov [prev_loc], cur_block_id   │
    │  push rbp                       │  ← 原始指令
    │  mov rbp, rsp                   │
    │  ...                            │
    └─────────────────────────────────┘
```

這和 AFL++ 的 LLVM instrumentation 邏輯完全一樣，只是 AFL++ 在編譯期做，DynamoRIO 在執行期做。

**優點**：不需要原始碼，任何 PE 都能插樁；覆蓋率品質高（basic block 精度）。

**缺點**：JIT 有額外開銷，比 compile-time 插樁慢；對自修改程式碼（SMC）有限制；有時觸發 DynamoRIO 和目標 DLL 的相容性問題（尤其是有 anti-debug 或特殊 loader trick 的目標）。

### 模式二：Intel PT 硬體追蹤模式

Intel Processor Trace（Intel PT）是 Broadwell 之後的 Intel CPU 都有的功能：CPU 在硬體層級把所有 **taken branches** 的記錄寫入一個 ring buffer，完全不需要軟體插樁。WinAFL 的 Intel PT 模式讀取這個 buffer、把 branch 資訊轉成 AFL-style bitmap。

```
    Intel PT 資料流
    ┌──────────────────┐
    │  CPU 執行指令     │
    │    jne target    │ ──►  PT packet: TNT(taken)
    │    call func     │ ──►  PT packet: TIP(target)
    │    ret           │ ──►  PT packet: TIP(return)
    └──────────────────┘
           │
           ▼ (硬體 ring buffer)
    ┌──────────────────────────────────────────┐
    │  WinAFL PT decoder                        │
    │  packets → basic block sequence          │
    │  sequence → AFL-style (src^dst) bitmap   │
    └──────────────────────────────────────────┘
```

**優點**：零插樁開銷、對目標完全透明（目標看不到自己被追蹤）、能追蹤有 anti-debug 的目標。

**缺點**：需要特定 CPU 代次（Broadwell+）；解碼 PT packets 本身有 overhead；需要有二進位的 address range（要告訴 WinAFL 追哪個模組）；PT packet decode 有時有精度問題（特別是 indirect call target）。

### 模式三：syzygy 靜態插樁

syzygy 是 Google 發布的 Windows PE 靜態二進位重寫工具。它在 fuzzing 之前「一次性」改寫目標 PE，把插樁程式碼直接插進 binary，之後執行就像 compile-time 插樁一樣快。

**優點**：執行時最快（沒有 JIT 或 PT decode 開銷）；覆蓋率精度高。

**缺點**：只支援 32 位元 PE（這是硬傷，目前 64 位元支援不完整）；PE 要符合特定格式假設（加殼目標不行）；需要重寫步驟、workflow 複雜一些。

### 三種模式取捨總表

| 維度 | DynamoRIO | Intel PT | syzygy |
|------|-----------|----------|--------|
| 需要原始碼 | 否 | 否 | 否 |
| 執行開銷 | 中（JIT） | 低（硬體） | 低（靜態） |
| 覆蓋率精度 | 高（basic block） | 中（branch decode 有時不準） | 高（basic block） |
| 64 位元支援 | ✅ | ✅ | ⚠️（受限） |
| 對 anti-debug 的透明度 | 低（DBI 常被偵測） | 高（硬體層） | 高（靜態重寫，不動態 hook） |
| 適合的目標 | 一般閉源 DLL | 有保護邏輯的目標 | 可靜態重寫的 32 位元 PE |
| 相容性風險 | 中（DRI/目標衝突） | 低 | 高（PE 格式要求嚴） |
| 主流使用率 | **最高** | 中 | 低 |

實務上，**DynamoRIO 模式是預設首選**，大多數 Windows fuzzing 工作從這裡開始。Intel PT 模式留給「DynamoRIO 被偵測/相容性炸掉」的場景。syzygy 除非你確定目標是 32 位元且格式乾淨，否則不必碰。

## Persistent Mode：WinAFL 的核心效能技巧

這是 WinAFL 最重要的設計，也是和 AFL++ persistent mode 直接對照的地方。

### AFL++ persistent mode 回顧

你在 AFL++ 做過的：

```c
// AFL++ persistent mode harness
#include "afl-fuzz.h"

int main(int argc, char *argv[]) {
    while (__AFL_LOOP(1000)) {
        // 每輪：從 stdin 或共享記憶體讀輸入
        // 呼叫目標解析函式
        // 不做任何 cleanup（或只做最小 cleanup）
    }
    return 0;
}
```

`__AFL_LOOP(N)` 讓行程跑 N 輪後自動退出重啟，避免狀態累積爆炸。重點是「**不重啟行程，只重置解析器狀態**」。

### WinAFL persistent mode

WinAFL 的做法完全相同，但因為目標是閉源 binary，你不能修改目標原始碼。解法：**harness 是一個外部包裝程式**，它載入目標 DLL、找到目標函式的位址、然後反覆呼叫它。WinAFL 透過 `afl_persistent_loop()` 或命令列 `-target_offset` 機制控制迴圈。

```
persistent mode 執行流程
  ┌─────────────────────────────────────────────────────────────────┐
  │  harness.exe（你寫的 wrapper）                                    │
  │                                                                  │
  │  1. LoadLibrary("target.dll")   ← 只做一次                       │
  │  2. GetProcAddress("ParseFile") ← 找到目標函式                   │
  │  3. 做任何需要一次性的初始化（開 COM、建 parser context...）        │
  │                                                                  │
  │  ┌──────────────────────────────────────────────────────────┐   │
  │  │  while (afl_persistent_loop(iterations)):                 │   │
  │  │                                                          │   │
  │  │    // WinAFL 在這裡更新 fuzzer 的 bitmap 並讀新輸入        │   │
  │  │                                                          │   │
  │  │    data = read_from_file(argv[1]);  ← 讀輸入檔            │   │
  │  │    ParseFile(data, len);            ← 呼叫目標函式        │   │
  │  │    free(data);                      ← 最小 cleanup        │   │
  │  │                                                          │   │
  │  └──────────────────────────────────────────────────────────┘   │
  │                                                                  │
  │  // 崩潰在 ParseFile() 裡 → WinAFL 攔截、記錄、繼續               │
  └─────────────────────────────────────────────────────────────────┘
```

關鍵：`afl_persistent_loop()` 由 WinAFL 在 DynamoRIO 層攔截並控制迴圈次數，你的 harness 源碼裡呼叫的那個函式只是一個 stub。

**效能量級**：有 persistent mode 的 WinAFL 在 DynamoRIO 模式下大約能達到 **1000–5000 exec/sec**（視目標複雜度）。沒有 persistent mode 每次 CreateProcess，實際量測常低於 100 exec/sec。這個差距讓 persistent mode 從「可選功能」變成「必要條件」。

> **未實測，理論預期**：上述效能數字來自 WinAFL GitHub 文件與 Ivan Fratric 原始發表；實際值依目標、CPU、DynamoRIO 版本而異。建議在你的環境用 `afl-whatsup` 觀察實際 exec/sec。

## 撰寫 Harness：把一個 File Parser API 包起來

### 目標情境

假設你要 fuzz `libfoo.dll`，它匯出一個函式：

```c
// 目標 DLL 的 API（從文件或逆向得到的簽章）
BOOL FooParseBuffer(const BYTE *data, SIZE_T length, FOO_OPTIONS *opts);
```

你沒有 `libfoo.dll` 的原始碼，但你知道函式簽章（透過逆向或公開文件）。

### Harness 骨架

```c
// harness.c — 為 WinAFL 包 libfoo.dll 的 FooParseBuffer
#include <windows.h>
#include <stdio.h>

// WinAFL persistent loop stub（鏈入 WinAFL 提供的 lib 時才有實體）
// 在 DynamoRIO 模式下，這個函式被 DRI client hook，不需實際定義
int __declspec(dllexport) afl_persistent_loop(unsigned int cnt);

typedef BOOL (*FooParseBuffer_t)(const BYTE *, SIZE_T, void *);

static FooParseBuffer_t g_parse = NULL;
static BYTE g_buf[1 * 1024 * 1024];  // 1 MB 輸入緩衝

void load_target(void) {
    HMODULE lib = LoadLibraryA("libfoo.dll");
    if (!lib) {
        fprintf(stderr, "LoadLibrary failed: %lu\n", GetLastError());
        exit(1);
    }
    g_parse = (FooParseBuffer_t)GetProcAddress(lib, "FooParseBuffer");
    if (!g_parse) {
        fprintf(stderr, "GetProcAddress failed\n");
        exit(1);
    }
}

int main(int argc, char *argv[]) {
    if (argc < 2) {
        fprintf(stderr, "Usage: %s <input_file>\n", argv[0]);
        return 1;
    }

    load_target();  // 只初始化一次

    // persistent loop：WinAFL 在 DynamoRIO 層控制迴圈次數
    while (afl_persistent_loop(2000)) {
        FILE *f = fopen(argv[1], "rb");
        if (!f) continue;

        SIZE_T len = fread(g_buf, 1, sizeof(g_buf), f);
        fclose(f);

        // 呼叫目標函式：WinAFL 在這個呼叫內部收集覆蓋率
        g_parse(g_buf, len, NULL);

        // 最小 cleanup：不需要完整清理——只要確保
        // 下一輪 g_parse 不會因舊狀態導致假崩潰即可
    }

    return 0;
}
```

> **未實測，理論預期**：以上 harness 骨架基於 WinAFL 官方範例與 Ivan Fratric 的文章；`afl_persistent_loop` 的 stub 鏈入方式依 WinAFL 版本略有差異，請參照 `winafl/afl-staticinstr.h` 的說明。

### `-target_module` / `-target_offset` / `-coverage_module`

WinAFL 的命令列旗標控制「在哪個函式進入 persistent loop」和「收哪個模組的覆蓋率」：

```bat
REM 未實測，理論預期
afl-fuzz.exe -i corpus\ -o findings\ -t 5000 --
    %DYNAMORIO_HOME%\bin64\drrun.exe
    -c winafl.dll
    -target_module harness.exe       <- persistent loop 的 module
    -target_offset 0x1234            <- main() 後面 afl_persistent_loop 呼叫的 RVA
    -coverage_module libfoo.dll      <- 只收這個 DLL 的覆蓋率（避免 ntdll noise）
    -fuzz_iterations 2000            <- 每輪 persistent 跑幾次
    -- harness.exe @@                <- @@ 被 WinAFL 替換成輸入檔路徑
```

**三個旗標的語意**：

| 旗標 | 意義 | 類比 AFL++ |
|------|------|-----------|
| `-target_module` | persistent loop 所在的 DLL/EXE | 自動偵測（AFL++ 自己知道） |
| `-target_offset` | loop 起點的 RVA（十六進位） | `__AFL_LOOP()` 插入點 |
| `-coverage_module` | 只收這個模組的 edge bitmap | AFL++ 可指定 `AFL_INST_LIBS=1` 等 |

`-target_offset` 是讓初學者最頭痛的地方。你需要用 IDA/Ghidra 找到 `afl_persistent_loop()` 呼叫在 `harness.exe` 裡的 RVA，或者用 WinDbg 找：

```bat
REM 找 afl_persistent_loop 呼叫的 RVA（未實測，理論預期）
cdb -c "x harness!afl_persistent_loop; q" harness.exe
REM 輸出類似：00007ff6`12341234 harness!afl_persistent_loop
REM RVA = VA - ImageBase（從 PE header 讀 ImageBase）
```

### 如果目標沒有匯出函式

很多閉源程式的解析邏輯藏在 `.text` 裡沒有匯出。方法：

1. **逆向找 RVA**：在 IDA/Ghidra 找到解析函式的位址，算出 RVA，直接用 `-target_offset` 指向那個函式。
2. **用 DLL injection**：把 harness 邏輯寫成 DLL，注射進目標行程，再用 WinAFL 的 in-process fuzzing 模式。
3. **harness 呼叫公開 API**：很多「閉源目標」其實透過有文件的 COM/WinRT/Shell API 公開了接口，harness 呼叫這些 API 而不是內部函式。

## 語料（Corpus）與 Crash Triage

### 語料策略

和 AFL++ 完全一樣的邏輯：

- **小樣本**：初始語料越小越好（1–10 KB 的合法檔案），讓 fuzzer 自己演化變異。
- **多樣性**：不同格式特性的樣本（壓縮/未壓縮、有/沒有特殊 chunk、各種版本）。
- **去重**：用 `afl-cmin` 剪掉觸發相同 path 的重複樣本。在 Windows 上 WinAFL 附帶 `winafl-cmin.py`。

```
corpus\
  seed_01.pdf   ← 最小合法 PDF
  seed_02.pdf   ← 有 embedded font 的版本
  seed_03.pdf   ← 有 JavaScript action 的版本
```

### Crash Triage 流程

WinAFL 把 crash 存進 `findings\crashes\`，格式和 AFL++ 一樣（`id:000000,sig:11,...`）。Triage 流程：

```
  findings\crashes\
      │
      ├── 1. 去重：用 !exploitable 或 BugId 把相同根因的 crash 聚合
      │
      ├── 2. 分類：
      │       EXPLOITABLE   ← 程式控制流被影響（write-what-where / PC control）
      │       PROBABLY_EXPLOITABLE ← 重要記憶體被寫
      │       PROBABLY_NOT_EXPLOITABLE ← null deref / read AV
      │       UNKNOWN       ← 還不確定
      │
      └── 3. 根因分析：
              用 WinDbg 或 TTD 重放 crash input，找漏洞點
              (TTD 在這裡特別好用，下節說明)
```

**`!exploitable`（Microsoft 開發的 WinDbg 擴充）**：自動分析 crash 時的 register state 和 exception type，輸出可利用性評分。安裝：從 [AppVerif SDK](https://learn.microsoft.com/en-us/windows-hardware/drivers/devtest/application-verifier) 取得。

```bat
REM 未實測，理論預期
cdb -c "!load exploitable; g; !exploitable; q" -g harness.exe crash_input.pdf
```

**BugId**：Skylined 開發的 crash 分析工具，用 hash 識別唯一根因，適合批次 triage 大量 crash。

### 使用 AddressSanitizer / App Verifier 提升 Crash 品質

很多記憶體錯誤不會直接崩潰（先寫 bad 記憶體，幾個呼叫後才 crash，報告點離根因很遠）。解法：

- **Application Verifier**：Windows 內建，啟用後對 heap 操作做完整性檢查，讓「silent corruption」變成立即崩潰。
- **ASan for MSVC**：MSVC 2019 16.4+ 支援 `/fsanitize=address`，在閉源 harness 上能提早發現越界。

```bat
REM 用 Application Verifier 監控 harness（未實測，理論預期）
appverif /enable Heaps /for harness.exe
REM 之後跑 harness，任何 heap corruption 立即 AV
```

## TTD-based Fuzzing 概念

Time Travel Debugging（TTD，Ch 41）讓你把一段程式執行**錄下來**，之後可以任意倒帶。這個能力和 fuzzing 的交叉點有兩個：

### 用途一：Crash 根因分析（最常見）

Fuzzer 找到 crash input 後，要搞清楚「哪一行程式碼發生了什麼」。直接在 WinDbg 重放，crash 點清楚，但控制流往回看要靠直覺猜。TTD 的做法：

```
  TTD-based crash 分析
  ┌─────────────────────────────────────────────────────────┐
  │  1. 用 WinDbg TTD 錄製 crash input 的執行               │
  │     ttd.exe -out crash.run harness.exe crash_input.pdf  │
  │                                                         │
  │  2. 在 WinDbg 開 .run 檔，倒帶到 crash 前              │
  │     0:000> !tt 0%                  ← 去到錄製開頭       │
  │     0:000> g crash_address         ← 快進到崩潰點       │
  │     0:000> !tt -10ms               ← 倒退 10 毫秒       │
  │                                                         │
  │  3. 在倒帶後的時間點看「誰寫了那個 buffer」             │
  │     能精確找到 root cause，不靠猜                       │
  └─────────────────────────────────────────────────────────┘
```

### 用途二：TTD 快照作為 Fuzzing 起點（研究性質）

一個更進階的概念：把 target process 初始化完成後（DLL 載入、COM 初始化都做好）的狀態錄成 TTD trace，然後從這個 trace 快照出發「重播並修改輸入」。這類似 LibAFL 的「snapshot fuzzer」概念，但用 TTD 做快照而不是 OS-level fork。

這條路由 Microsoft Research 的 Onefuzz 和一些學術 fuzzing 工具在探索；目前成熟度不如 WinAFL 的 persistent mode，但對「init 成本極高」的目標（如 JavaScript 引擎）有潛力。

> 目前最實用的 TTD 用途是 crash triage（用途一），TTD-fuzzing 作為 fuzzing 引擎本體仍是研究方向，不是本章的實作重點。

## fuzz 什麼：Windows 上的好目標

### 檔案解析器（最常見）

| 目標類型 | 舉例 | 理由 |
|----------|------|------|
| 圖片解析 | `windowscodecs.dll` (WIC)、GDI+ | 攻擊面大，歷史洞多 |
| 文件解析 | Office OOXML parser、PDF | 用戶觸及率高，解析邏輯複雜 |
| 字型解析 | `atmfd.dll`（已移除）、`fontsub.dll` | 核心態/用戶態都有，字型洞傳統肥沃 |
| 媒體解析 | `mfreadwrite.dll`（Media Foundation） | 壓縮格式解析邏輯多 |
| 壓縮格式 | `cabinet.dll`、`msdelta` | 複雜的狀態機，歷史有多個 CVE |

### 格式 Library（比較小、容易撰寫 harness）

`expat`（XML）、`libwebp`（WebP）、`zlib`/`libpng` 在 Windows 上的版本——這些雖然有原始碼，但在 Windows 上的行為和 Linux build 可能不同。

### 協定解析器

針對 SMB 客戶端（`mrxsmb.dll`）、RDP 協定處理（`mstscax.dll`）、DNS（`dnsapi.dll`）——這些通常需要 in-process fuzzing 或 network fuzzing，WinAFL 可以搭配 network harness 做。

## 對比 AFL++：WinAFL 對應關係

| AFL++ 概念 | WinAFL 對應 | 關鍵差異 |
|-----------|------------|---------|
| `afl-fuzz` | `afl-fuzz.exe` | 命令列介面基本相同 |
| `afl-clang-fast` 插樁 | DynamoRIO client | 動態而非靜態；有額外開銷 |
| QEMU mode（閉源目標） | DynamoRIO 模式 | WinAFL 的 QEMU 等價 |
| Fork server | Persistent mode | 解法相同，實作不同（無 fork()） |
| `__AFL_LOOP(N)` | `afl_persistent_loop(N)` | 語意相同 |
| `AFL_MAP_SIZE` | `-coverage_module` 控制範圍 | bitmap 大小預設 64KB |
| CmpLog / REDQUEEN | 無直接等價 | WinAFL 沒有原生 CmpLog |
| `-t timeout` | `-t timeout` | 相同 |
| `afl-cmin` | `winafl-cmin.py` | Python 版的語料去重 |
| `afl-showmap` | 無直接等價（可用 DRI 的 coverage tool） | 需要繞過 |
| AddressSanitizer | App Verifier / MSVC ASan | App Verifier 是 Windows 版替代 |

**WinAFL 目前沒有的**：
- CmpLog / REDQUEEN 類型的 feedback（這讓 WinAFL 對有 magic byte check 的目標較弱）
- 原生 Python mutation 外掛（AFL++ 有 `afl-python-module`）
- 多目標同時 fuzz 的平行化管理（AFL++ 有 `-M`/`-S` 主次節點）

要彌補 CmpLog 的缺失，常見策略是先在 Linux 用 AFL++ 找一個「合法但多樣」的語料，再把語料搬到 WinAFL。

## 踩雷集錦

1. **「DynamoRIO 跑起來了但 exec/sec 接近 0」**：最常見原因是忘記設 `-coverage_module`，WinAFL 在插樁整個行程（包括 ntdll、kernel32 等）的 bitmap，光是更新 bitmap 就把速度榨乾。**一定要把 coverage 限縮到目標模組**。

2. **「harness 在我手動跑時正常，WinAFL 跑就 hang」**：通常是 harness 在 fuzz 輸入路徑下觸發了一個 blocking 的 Win32 API 呼叫（例如 `MessageBox`、`CreateFileDialog`），要在 harness 裡把這些 UI 呼叫 mock 掉或讓 target 以「無 UI 模式」執行。

3. **「`-target_offset` 怎麼算都不對」**：RVA 要從 PE 的 ImageBase 算。如果你是從 WinDbg 拿到 VA（虛擬位址），需要減掉 ImageBase（在 PE 的 Optional Header 裡）。重要：ASLR 開著時每次 ImageBase 不同——**用 WinDbg 的 `!lm m harness` 看載入基址，或直接從 PE header 讀 ImageBase，不是從記憶體觀測**。

4. **「DynamoRIO 和目標 DLL 衝突跑不起來」**：特別是有 hook 機制的 DLL（安全軟體、DRM）和 DynamoRIO 的 JIT 會互搶記憶體或 hook 同一個函式。方向：換 Intel PT 模式；或在沒有安全軟體的 VM 裡 fuzz。

5. **「找到 crash 但無法重現」**：Windows 的 heap 有隨機化（LFH 分配器的 bitmap 是隨機初始化的，Ch 15），相同輸入不一定觸發相同記憶體布局。這不代表 crash 是假的——建議開 App Verifier 重現（它移除部分隨機性），或用 TTD 錄下來研究。

## 進階：再往深一層

### WinAFL 的 network fuzzing 擴充

要 fuzz 協定解析器時，目標往往是從 socket 讀輸入而非從檔案。WinAFL 社群有 `afl-net` 類型的 patch，或者你可以在 harness 裡把 `recv()` hook 成從檔案讀——讓 fuzzer 仍然透過檔案介面 feed 輸入，但目標看到的是 socket 語意。

### 覆蓋率品質的進一步提升

如果你想知道「fuzzer 到底覆蓋了哪些函式」，可以在 DynamoRIO 的 `-code_api` 模式下讓它輸出 basic block 日誌，再用 IDA 的 lighthouse 外掛視覺化覆蓋率熱圖。這對確認 fuzzer 有沒有真的走進解析邏輯的深處很有用。

### 面試題準備

- **「WinAFL 的 persistent mode 和 AFL++ 的 fork server 解決同一個問題嗎？」**：是，都是避免行程重啟的代價，但機制不同——AFL++ 用 fork() COW 複製行程狀態，WinAFL persistent mode 讓行程自己迴圈。

- **「為什麼 WinAFL 的 DynamoRIO 模式比 AFL++ 編譯時插樁慢？」**：因為 DynamoRIO 要在執行期 JIT 重寫每個 basic block，這個 JIT 本身有 overhead；AFL++ 的插樁在編譯期完成，執行時只是跑幾條額外的 store 指令，快一個量級以上。

## 動手練習

找一個 Windows 上的開源 XML/PDF library 的 DLL 版本（例如 `expat.dll`），撰寫一個最小 WinAFL harness：

1. `LoadLibrary` 載入目標 DLL，`GetProcAddress` 取得解析函式
2. 實作 `afl_persistent_loop()` 迴圈，每輪從輸入檔讀 buffer 後呼叫解析函式
3. 用 WinAFL 的 `-coverage_module` 限縮到目標 DLL
4. 觀察初始語料（3–5 個小檔）下的 exec/sec，確認 persistent mode 啟動

> 如果你的環境尚未安裝 DynamoRIO + WinAFL，先完成 harness.c 的設計（不跑），用 WinDbg 確認能手動載入目標 DLL 並成功呼叫解析函式，這樣到時候接上 WinAFL 的步驟就只剩命令列旗標設定。

## 本章重點整理

- Windows fuzzing 的核心困難：無 `fork()`（行程重啟代價高）+ 閉源目標多（需動態插樁）。
- WinAFL 三種插樁模式：**DynamoRIO**（主流，動態 JIT）/ **Intel PT**（硬體，對抗 anti-debug）/ **syzygy**（靜態，32 位元限制）；首選 DynamoRIO。
- **Persistent mode 是效能命脈**：不重啟行程、只重置解析器狀態，exec/sec 從 <100 提升到 1000+。
- **`-coverage_module` 必設**：不設就插樁整個行程、速度歸零。
- WinAFL 對比 AFL++：邏輯完全一致，差在插樁時機（動態 vs 編譯期）與 fork/persistent 的實現機制；CmpLog 是 WinAFL 目前的明顯缺口。

## 自我檢核

- [ ] 不看表，能解釋 WinAFL 為什麼需要 persistent mode，以及 Windows 上「沒有 fork()」如何影響 fuzzer 設計
- [ ] 能說出 DynamoRIO 模式 vs Intel PT 模式各自適合什麼場景
- [ ] 能從零寫出一個 WinAFL harness 的骨架（`LoadLibrary`→`GetProcAddress`→persistent loop→呼叫目標）
- [ ] 知道 `-target_module`、`-target_offset`、`-coverage_module` 三個旗標各自控制什麼
- [ ] 面試被問「WinAFL 的 persistent mode 和 AFL++ 的 fork server 有什麼差別」，能給出清楚答案

## 延伸閱讀

### 工具 / 專案

- **[WinAFL GitHub — googleprojectzero/winafl](https://github.com/googleprojectzero/winafl)**
  - **讀哪裡**：README 的 "How does it work" 與 "Usage" 兩節；`harness/` 目錄下的範例 harness；`CHANGELOG` 追 coverage backend 演進
  - **學什麼**：persistent mode 的命令列設定、DynamoRIO / Intel PT 兩種模式的切換旗標、已知的相容性問題清單
  - **前提知識**：本章內容；基本 Windows PE/DLL 概念（Ch 3–4）

### 論文 / 研究文章

- **Ivan Fratric — "WinAFL: A fuzzer for Windows applications"（2016, Google Project Zero）**
  - **讀哪裡**：Project Zero 部落格的原始發表文；特別是 "Persistent mode" 與 "DynamoRIO instrumentation" 兩段
  - **學什麼**：為什麼 Windows fuzzing 困難、persistent mode 設計的動機、效能實測數字
  - **和本章的關聯**：本章所有核心設計概念的第一手來源

- **"Fuzzing Windows Stuff" — Nicolas Joly（MSRC, various Microsoft Security talks）**
  - **讀哪裡**：Microsoft Security Response Center 公開的 fuzzing 方法論文章與 BlueHat 演講
  - **學什麼**：Microsoft 內部對 Windows 元件做大規模 fuzzing 的方法論，包括語料建立、triage 自動化
  - **和本章的關聯**：工業規模的 Windows fuzzing 實踐，是本章方法論的進階延伸

### 工具文件

- **[DynamoRIO 官方文件](https://dynamorio.org/docs/)**
  - **讀哪裡**："DynamoRIO Extensions" → "drcov"（coverage tool）；"Usage: Running"（基本使用）
  - **學什麼**：WinAFL 底層的插樁框架；`drcov` 能獨立使用做 coverage 分析（和 lighthouse 搭配）
  - **前提知識**：本章 DynamoRIO 模式的說明即可

- **[BugId — Skylined](https://github.com/SkyLined/BugId)**
  - **讀哪裡**：README 的 "How does it work" 段落；`./BugId.py --help`
  - **學什麼**：大量 crash 的自動 triage 與去重，用 crash hash 識別唯一根因
  - **和本章的關聯**：crash triage 流程的重要輔助工具

找到 crash 只是第一步；下一章我們換個方向——在沒有 fuzzer 的情況下，把微軟已經「偷偷修了的洞」從補丁裡逆回來。

→ [Ch 43 — 找洞：Patch Tuesday patch diffing](./43-patch-diffing.md)
