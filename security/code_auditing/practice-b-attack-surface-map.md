# 練習 B — 攻擊面地圖 + sink 清單

> **目標**：把 Part 2 的四樣工具（source/sink/sanitizer 思維、攻擊面建模、跨語言 sink 表、triage）落到一個真實開源專案上，產出一份**結構化的攻擊面地圖 + source/sink 清單**。這份清單不是交作業就丟——它是你 Part 3（Semgrep）、Part 4（CodeQL）真正拿去掃的 **target 規格**。攻擊面建模建不好，後面工具再強也是亂槍打鳥。

這是本課第一個「真的動手審一個專案」的練習。你不用找到 bug（那是後面工具的活），你要交出的是一張**地圖**：這個專案的攻擊面在哪、trust boundary 在哪、哪些 source 流向哪些 sink、你認為最值得深挖的三塊在哪且為什麼。這是所有後續掃描的起點。

## 選 target

三選一，難度遞增：

- **redis**（推薦，你在 `reading_code` 已熟）：C，網路服務，RESP protocol parser + 各種指令 handler + RDB/AOF 序列化 + Lua 腳本。攻擊面清晰、entry point 明確、有 CVE 歷史可對照。
- **curl**：C，client 端，協議極多，攻擊面重心在「解析遠端回應」，適合練「回應方向攻擊面」。
- **一個小型 C parser**（如某個 image/font/protocol 解析 library，幾千行）：最小、最聚焦，適合把「parser = 第一順位 target」的直覺跑到底。

建議第一次做選 redis 或一個小 parser。curl 協議多、面大，適合第二次做。

## 交付物規格

你要產出**一份 Markdown 文件**，含以下五個部分。格式精確定義如下（後面工具會吃這個結構）：

### 1. 專案基本資訊
- 專案名、版本/commit hash（**務必記 hash**，攻擊面隨版本變）、語言、程式碼行數（`cloc` 或 `tokei`）、build 方式。

### 2. 攻擊面地圖（表格）
對每個 entry point 一列，欄位固定：

| entry point | 檔案:函式 | 觸發條件 | 認證前/後 | 處理的輸入 | trust boundary | 優先序(★) |
|---|---|---|---|---|---|---|
| （範例）RESP 解析 | networking.c:processMultibulkBuffer | 任何 client 連上 | 認證前 | 網路 bytes | network | ★★★ |

至少列 **6 個 entry point**，涵蓋不同 trust boundary（別全是網路那條——找找檔案/序列化/腳本/config 那幾條）。

### 3. trust boundary 標註
用一段文字或 ASCII 圖說明這個專案有哪幾條 trust boundary（network、特權、序列化/複製鏈、腳本沙箱、config…），每條邊界外側是誰、內側從哪個函式開始追。

### 4. source/sink 清單（表格）
這是核心交付物。每列一條你識別出的 candidate flow 或 flow 起訖：

| # | source (檔案:函式/變數) | sink (檔案:函式) | 漏洞類別 (CWE) | 中間 sanitizer? | 可信度初判 |
|---|---|---|---|---|---|
| 1 | （範例）query buffer | 某 handler 的 memcpy | CWE-120 | 待查 | B |

至少 **10 條**。source 要列到「第一手輸入」（Ch 9），sink 用 Ch 11 的 CWE 分類，可信度用 Ch 12 的 A/B/C 初判。**允許很多是 B/C——這階段是列 candidate，不是確認 bug**。

### 5. 三個最值得深挖的區塊 + 理由
從上面的地圖選 **3 個區塊**，每個寫 2-3 句：為什麼值得（用 Ch 10 的 heuristics——parser？特權邊界？新功能？歷史 CVE？少人審的角落？），以及你打算之後用哪個工具掃它（Semgrep 快篩 / CodeQL global taint / weggli 找記憶體 pattern）。

## 驗收標準

你的交付物合格，當且僅當：

- [ ] 記了確切 commit hash 與行數。
- [ ] 攻擊面地圖 ≥ 6 個 entry point，且**涵蓋至少 3 種不同 trust boundary**（不能全是網路）。
- [ ] trust boundary 段落明確指出每條邊界的「內側從哪個函式開始追」。
- [ ] source/sink 清單 ≥ 10 條，每條有 CWE 分類與可信度初判，source 列到第一手。
- [ ] 三個深挖區塊每個都用 Ch 10 的具名 heuristic 說明理由（不能只寫「感覺重要」）。
- [ ] 清單裡至少有 **2 條是「隱性/間接 sink」或「回應/回讀方向 source」**（證明你沒只抓顯性面）。

## 分段建議（5 步）

1. **偵察（30–60 分鐘）**：clone、記 hash、跑 `tokei`、讀 README/架構、用 `reading_code` Ch 5–7 的手法找 `main`/event loop/dispatcher。目標是畫出「資料從哪進來」的大圖。
2. **列 entry point + 畫 boundary**：從入口往內，把每個「值來自外部」的地方標成 entry。刻意找非網路的邊界（讀檔、序列化、腳本、config）。填第 2、3 部分。
3. **列 source（第一手）**：對每個 entry point，追到「值進來的第一手函式」。別停在你順眼的變數。
4. **列 sink + 連 flow**：grep Ch 11 各類 sink 函式，對照 source，連出 candidate flow，標 CWE 與可信度。刻意找 2 條隱性/間接的。
5. **排優先序 + 選三塊**：套 Ch 10 的優先序公式，選三個深挖區塊，寫理由與打算用的工具。

## 如果你卡住了

- **entry point 找不到**：找 `main`、`accept`/`recv`、`register_command`/dispatch table、HTTP route 註冊。event-driven 程式的入口是 event loop 的 callback 註冊處。
- **source 列不到第一手**：從一個你確定是外部輸入的變數，用 LSP/`grep` 反向追它的賦值來源，追到某個 `recv`/`read`/`parse` 為止。
- **只找得到網路那條 boundary**：問「這程式還從哪讀資料？」——設定檔、載入的 dump/state 檔、外掛、環境變數、它信任的『內部』服務。這些都是別條 boundary。
- **不知道哪塊值得深挖**：預設押 parser（任何 `parse_`/`decode_`/手寫的 length-prefixed 讀取迴圈）——歷史統計上它 CVE 密度最高。
- **可信度全不會判**：初判階段允許大量標 B/C。A 只給「四問一眼全過」的；其餘 B（有一問不確定）、C（明顯某問不成立但沒確認）。

## 參考解答（redis 示意）

<details>
<summary>展開：redis 攻擊面地圖 + sink 清單示範（函式/檔名為示意，以你 clone 的版本為準）</summary>

以下是一份**縮減版**示範，展示格式與思路，不是完整答案。redis 各大版本有大量重構，**所有檔案/函式名以你 clone 的 commit 為準**。

### 專案基本資訊
- redis，commit `<你的 hash>`，C，約數十萬行（含 deps），`make` build。

### 攻擊面地圖

| entry point | 檔案:函式 | 觸發條件 | 認證前/後 | 輸入 | trust boundary | ★ |
|---|---|---|---|---|---|---|
| RESP 解析 | networking.c:processMultibulkBuffer / processInlineBuffer | 任何連線 | 認證前 | 網路 bytes | network | ★★★ |
| AUTH 指令 | acl.c / server.c 的 auth handler | 送 AUTH | 認證前 | 密碼字串 | network | ★★★ |
| 各指令 handler | t_string.c / t_hash.c 等 `*Command` | 認證後送指令 | 認證後 | argv[]（已 parse） | network | ★★ |
| RDB 載入 | rdb.c:rdbLoad* | 啟動載入/複製 | 本地/複製鏈 | 序列化 dump | 檔案/複製 | ★★★ |
| Lua/腳本 | script/eval 相關 | EVAL 指令 | 認證後 | script 字串 | 腳本沙箱 | ★★ |
| CONFIG SET | config.c:configSetCommand | CONFIG 指令 | 認證後 | 設定值 | network→本地設定 | ★★ |

（涵蓋 network / 檔案-複製鏈 / 腳本沙箱三種以上 boundary。）

### trust boundary
```
攻擊者(遠端) ──► [socket] readQueryFromClient ──► query buffer   ← network 邊界，追蹤起點
複製主節點/dump 檔 ──► rdbLoad*                                  ← 序列化/複製鏈邊界
EVAL 送入的 script ──► Lua VM                                    ← 腳本沙箱邊界（sandbox escape 面）
CONFIG SET ──► 改 server 設定 ──► 間接影響檔案路徑/行為          ← 設定即間接 source
```
- network 內側從 `readQueryFromClient` → `processMultibulkBuffer` 把 bytes 變 `argv[]` 開始追。
- RDB 內側從 `rdbLoad*` 系列的反序列化讀取迴圈開始追（length-prefixed 讀取是重點）。

### source/sink 清單（節選）

| # | source | sink | 類別(CWE) | sanitizer? | 可信度 |
|---|---|---|---|---|---|
| 1 | query buffer(網路) | processMultibulkBuffer 裡的長度/配置運算 | CWE-190/120 | 有長度上限檢查（待驗） | B |
| 2 | RDB 中的 length 欄位 | rdbLoad* 的 alloc/memcpy | CWE-120/190 | 待查是否驗長度 | B |
| 3 | argv 字串(網路) | 某指令對 buffer 的索引存取 | CWE-125/787 | 指令參數個數檢查 | C |
| 4 | CONFIG SET 的路徑值 | 之後 open/寫檔的路徑 | CWE-22 | 待查是否驗路徑 | B（間接 sink）|
| 5 | EVAL script(網路) | Lua VM 執行 | CWE-94/沙箱逃逸 | 沙箱限制 | B |
| 6 | 複製鏈傳來的資料 | 主從同步的解析 | CWE-120 | 待查 | B（回讀方向）|
| … | … | … | … | … | … |

（其中 #4 是間接 sink、#6 是回讀/複製方向 source，滿足「≥2 條隱性/回流」驗收項。）

### 三個深挖區塊 + 理由
1. **RDB/序列化載入（rdb.c）** — heuristic：**parser/反序列化排第一**。length-prefixed 反序列化歷史上是 memory-safety CVE 富礦；且複製鏈讓它在某些部署下遠端可影響。打算用 **weggli/CodeQL** 找「讀 length 後未驗就 alloc/memcpy」的 pattern。
2. **RESP protocol 解析（networking.c）** — heuristic：**pre-auth 可達 + parser**。任何連線就觸發、認證前，攻擊面權重最高。打算用 **CodeQL global taint** 從 query buffer 追到各 handler 的記憶體操作。
3. **Lua 腳本邊界** — heuristic：**特權/沙箱邊界**。sandbox escape = 從腳本跳到主機執行，影響大且是少人深審的角落。打算用 **Semgrep/手讀** 盤點暴露給 script 的危險 API。

</details>

## 延伸挑戰

- **版本 diff 找新增攻擊面**：clone 兩個相鄰大版本，`git diff --stat` 找改動最大的檔案，比對你的地圖——**新增/大改的 entry point 就是最值得審的 delta**（沒被時間淬煉、審過的眼睛少）。這是 Ch 38 diff-based 審計的預演。
- **交叉比對已知 CVE**：查你 target 的 CVE 歷史（redis/curl 都有 security 頁面），把每個歷史 CVE 標回你的攻擊面地圖——它落在哪個 entry point？你的地圖有沒有涵蓋到那塊？沒涵蓋到的，說明你的攻擊面盤點有盲區，補上。這同時驗證「歷史 CVE 多的模組」heuristic，也是 Ch 26「從 CVE 到 query」的起手式。

你現在有了一份真實專案的 target 規格。下一個 Part 開始，我們拿起第一把刀——Semgrep：一個能在幾秒內掃完整個 repo 的輕量 pattern engine。先從最直接的語法模式開始，把你清單上的 sink 變成能跑的 query。

→ [Ch 13 Semgrep 語法模式](./13-semgrep-syntactic-patterns.md)
