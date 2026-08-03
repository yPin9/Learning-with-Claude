# Ch 10 — 攻擊面建模與 target 選擇

> **目標**：你手上有一個幾十萬行的專案和有限的時間。動手審計前，先建一張**攻擊面地圖（attack surface map）**：哪些是 entry point、哪些 code 在處理不可信輸入、trust boundary 在哪、哪些區塊值得押注。這章把 `reading_code` 的「60 分鐘偵察 + 架構地圖」接過來，加上**安全視角的重加權**——同一個專案，工程師畫的地圖和攻擊者畫的地圖重點完全不同。

Ch 9 教你對「一條 flow」下判斷。但一個真實 codebase 有成千上萬條潛在 flow，你不可能全標。攻擊面建模是**在標 flow 之前的決策層**：它決定你把時間押在哪。押錯地方——一頭栽進最大最複雜但被審過一百遍的核心模組——你會花三天一無所獲；押對地方——某個沒人看的新 parser、某個特權邊界的角落——可能第一天就出 bug。這章講的是**選擇的藝術**，不是技術本身。

## 為什麼要先建地圖，而不是直接掃

你可能會想：「工具這麼強，直接 CodeQL 全掃不就好了，幹嘛還手動建地圖？」三個理由：

- **query 也要有的放矢**。全語言全 CWE 掃一個大 repo，你會得到幾千個命中，triage 到死。你得先知道「攻擊面在哪」才能把 query 對準、才能判斷哪些命中在攻擊面上因而值得先看。
- **工具看不到 threat model**。CodeQL 不知道哪個 entry point 是遠端未認證可達、哪個要 root 才能碰。exploitability 的排序需要人對系統的理解，地圖就是這個理解的載體。
- **地圖是複用資產**。你這次審的地圖，下次這個專案出新版、或你要寫針對性 query、或要教別人，都用得上。手讀一條路徑用完就忘，地圖是沉澱下來的。

## 攻擊面地圖有哪些欄位

一張夠用的攻擊面地圖，對每個 entry point 至少記這幾件事：

```
entry point │ 觸發條件         │ 認證前/後 │ 處理的輸入      │ trust boundary │ 值不值得
────────────┼─────────────────┼──────────┼────────────────┼───────────────┼─────────
RESP parser │ 任何 client 連上 │ 認證前    │ 網路 bytes      │ network        │ ★★★
AUTH 指令    │ client 送 AUTH   │ 認證前    │ 密碼字串        │ network        │ ★★★
Lua eval    │ EVAL 指令        │ 認證後    │ script 字串     │ 特權/腳本沙箱   │ ★★
RDB load    │ 載入 dump 檔     │ 本地/複製 │ 序列化資料檔    │ 檔案/複製鏈    │ ★★★
CONFIG SET  │ CONFIG 指令      │ 認證後    │ 設定值          │ network        │ ★★
```

（redis 示意，欄位以你 clone 的版本為準。）這張表的每一欄都是一個**排序維度**：

- **觸發條件**：多容易到達？任何連線就觸發 vs 要一長串前置指令。
- **認證前/後**：**認證前可達的攻擊面含金量最高**——不需要任何憑證就能打到的 code，是 pre-auth RCE 的溫床。
- **處理的輸入**：輸入越結構化、越需要 parse，bug 越多（見下節）。
- **trust boundary**：跨哪條邊界？跨特權邊界（本地提權）、跨網路（遠端）分量不同。
- **值不值得**：綜合上面 + 下一節的 heuristics 給的優先序。

## offensive 視角的 target 選擇 heuristics

哪些 target 值得投時間？以下是產出 CVE 機率高的區塊，按經驗排序。這不是理論，是「bug 真的常出在這裡」的統計直覺：

**1. parser / 解碼器 / 反序列化器。** 這是第一順位，沒有之一。任何把 bytes 變成結構的 code——協議 parser、檔案格式解析、圖片/字型/影音 codec、序列化框架——都在做「相信外部資料的結構」這件危險事，而且狀態多、邊界情況爆炸。歷史上絕大多數 memory-safety CVE 出在 parser。看到 `parse`、`decode`、`unmarshal`、`deserialize`、手寫的 length-prefixed 讀取迴圈，眼睛放亮。

**2. 特權邊界。** setuid 程式、kernel driver 的 ioctl handler、browser 的 IPC broker、沙箱的 syscall 過濾——任何「低權限方餵資料給高權限方」的介面。這裡一個 bug 就是提權。特權邊界通常有明確的 API 面（ioctl number、IPC message type），很好列舉。

**3. 新功能 / 最近改動。** 新加的 code 沒經過時間淬煉，審過的眼睛少。用 `git log`（`reading_code` Ch 17 的 git 考古）找最近幾個月的大改動、新增的檔案、新的 feature flag。**diff-based 審計**（Ch 38）整套方法就建在這個直覺上。

**4. 歷史 CVE 多的模組。** 出過一個 bug 的地方通常有兄弟 bug——同一個作者、同一種模式、同一個沒學到的教訓。查這個專案的 CVE 歷史、security advisory，哪個檔案反覆出現，那裡就是富礦。這也是 **variant analysis** 的起點：拿一個已修 CVE，找它的所有變體。

**5. 少人審的角落。** 冷門的 code path——很少啟用的功能、可選的 protocol 分支、被 `#ifdef` 包住的實驗功能、legacy 相容層。熱門路徑被無數雙眼睛看過，角落沒有。attack surface 大但關注度低 = 高 CVE 密度。

**6. 信任了不該信任的東西。** 找「這裡假設輸入是良性的」的地方——把 config 檔當可信、把 localhost 連線當可信、把「內部服務」的 RPC 當可信。這些信任假設在真實部署中常被打破。

## 用攻擊面大小排優先序

有了地圖和 heuristics，怎麼排？一個實用的心智公式：

> **優先序 ≈ (可達性 × 輸入複雜度 × 特權增益) / 已被審的程度**

- **可達性**高（pre-auth、遠端、預設開啟）→ 加分。
- **輸入複雜度**高（要 parse 的結構化資料）→ 加分。
- **特權增益**大（跨特權邊界，bug = 提權/RCE）→ 加分。
- **已被審的程度**高（核心熱門路徑、被 fuzz 過、CVE 已清乾淨）→ 減分。

把地圖每一列套這個公式，你就有了投時間的順序。注意分母：**別跟一群人擠在同一塊被審爛的 code 上**。你的優勢常在「大家都跳過的地方」。

## 具體：對 curl 做一次攻擊面走查

拿 curl 當例子走一遍（curl 是理想 target：協議極多、輸入全來自網路、歷史 CVE 豐富、可讀）：

1. **entry point 是什麼？** curl 的核心是「給一個 URL，發請求收回應」。所以攻擊面兩端：一端是**使用者/腳本給的 URL 與選項**（本地 source），另一端更關鍵——**遠端伺服器回傳的資料**（遠端 source，攻擊者若控制 server 或能中間人就控制它）。

2. **哪些 code 處理不可信輸入？** 每一個協議 handler：HTTP header 解析、chunked encoding 解碼、cookie 解析、URL parser、TELNET/FTP/IMAP 等各協議的回應解析。**這些 parser 全是第一順位 target**（heuristic 1）。curl 歷史上大量 CVE 正是出在協議 parser 與 URL 解析（例如各種 host/credential 解析的邊界問題）。

3. **trust boundary 在哪？** 最重要的一條是「curl 進程 ↔ 遠端伺服器」。很多人直覺以為「我發請求、server 回資料」是可信的，但**回應完全由對端控制**——redirect 到哪、header 多長、chunk size 多大，都是 attacker-controlled。把「回應解析」當可信是 curl 類 client 的經典盲區。

4. **重加權。** 工程師看 curl 會先看「怎麼發請求」；攻擊者看 curl 先看「收回應時哪個 parser 最脆」。同一份 code，安全視角把權重全押在**回應解析與各協議狀態機**上，而非請求構造。

（以上是方法示範；具體檔名/CVE 以你 clone 的 curl 版本與其 security advisory 頁面為準。）

## 別忘了「非程式碼」攻擊面

最容易被整批漏掉的一類：**build/測試/工具鏈**。

- **build 系統**：`configure` 腳本、`Makefile`、CMake、下載依賴的步驟。供應鏈攻擊（xz-utils 後門就藏在 build 階段的 `m4` 與測試檔裡）證明了 build path 是真實攻擊面。
- **測試與 fixtures**：CI 會執行的東西、測試會解析的樣本檔。
- **依賴**：專案 vendored 進來的第三方 code、動態載入的 plugin。
- **文件生成、腳本、CI workflow**：`.github/workflows` 裡的 `pull_request_target`、注入點。

這些不在「主程式邏輯」裡，卻常有最軟的攻擊面，因為沒人用安全眼光看它們。建地圖時留一格給它們。

## 踩雷集錦

**錯誤直覺：「先攻最大最核心的模組，那裡最重要。」**
正確認識：最核心 = 最多人審 = bug 密度最低。你會跟全世界的研究者擠在同一塊被 fuzz 到爛的 code 上。真正的機會在**大家跳過的角落**：冷門協議分支、新功能、legacy 層。優先序公式的分母（已被審程度）就是提醒你避開紅海。

**錯誤直覺：「只看程式碼就能建攻擊面地圖。」**
正確認識：地圖需要 **threat model**——這個 entry point 是遠端還本地？pre-auth 還 post-auth？攻擊者控制得了輸入的哪部分？這些資訊不在 code 裡，在部署方式、認證流程、信任假設裡。只讀 code 不讀 threat model，你會把一個「要 root 才能碰」的介面跟「任何人遠端可達」的介面排一樣的優先序。

**錯誤直覺：「build/CI/測試不是攻擊面，跳過。」**
正確認識：供應鏈攻擊時代，build path 是一等公民。xz-utils 後門、各種 CI 注入、`pull_request_target` 濫用都證明了這點。這些地方軟，正因為沒人用安全眼光看。地圖務必保留「非程式碼攻擊面」一欄。

**錯誤直覺：「攻擊面 = 所有對外的 API，列完就完了。」**
正確認識：攻擊面是**分層**的。列完網路 entry point 只是第一層；每跨一條 trust boundary（進到特權區、進到 parser 內部狀態機）都有新的一層。而且「回應/回讀」方向的攻擊面（client 收 server 資料、程式讀回 DB 資料）最常被漏——大家只列「輸入」不列「回流」。

**錯誤直覺：「地圖建一次就固定了。」**
正確認識：攻擊面隨版本演進。新版加了協議、改了認證、開了預設功能，地圖就得更新。而「兩個版本之間新增的攻擊面」本身就是最值得審的 target（delta = 未被審的新肉）。把地圖當活文件，diff 它（延伸挑戰與 Ch 38）。

## 進階延伸

- **attack surface 量化**：學界有把攻擊面形式化成「entry/exit points × channel × 資料項」的度量（Manadhata & Wing 的 attack surface metric）。實務不必嚴格套，但「越多入口 × 越多管道 × 越多資料項 = 面越大」的直覺很有用。
- **reachability 與 pre-auth 判定**：判斷一個 sink 是否 pre-auth 可達，需要 call graph 從未認證入口能不能走到它。這正是 Ch 22 CodeQL global taint 能自動幫你算的——用工具把「可達性」這一欄機器化。
- **DA（Dominator/支配）視角看必經點**：如果所有 request 都必經某個 dispatch 函式，那就是攻擊面的咽喉，優先建模它。這跟 `ssa_optimizations` 的 dominator 概念同源。

## 本章重點整理

- 攻擊面建模是**標 flow 之前的決策層**：決定把有限時間押在哪，避免栽進被審爛的核心模組。
- 攻擊面地圖對每個 entry point 記：觸發條件、認證前/後、輸入、trust boundary、優先序——每一欄都是排序維度。
- target 選擇 heuristics（高 CVE 機率）：**parser/反序列化 > 特權邊界 > 新功能/改動 > 歷史 CVE 多的模組 > 少人審的角落 > 錯誤的信任假設**。
- 優先序 ≈ (可達性 × 輸入複雜度 × 特權增益) / 已被審程度。**別擠紅海，往冷門角落押注**。
- 別漏 **build/CI/測試/依賴**這類非程式碼攻擊面，也別只列「輸入」而漏「回應/回讀」方向。
- 地圖是活文件，會隨版本演進；版本間的攻擊面 delta 本身就是好 target。

## 自我檢核

- 為什麼「工具能全掃」不代表可以跳過手動建攻擊面地圖？給出至少兩個理由。
- 列出攻擊面地圖該有的欄位，說明每一欄是哪個排序維度。
- 背出 target 選擇的 heuristic 排序，並解釋為什麼 parser 排第一、為什麼「最核心模組」反而不是首選。
- 對一個你熟的專案（redis/curl/任一 CLI），說出它「回應/回讀」方向的攻擊面在哪，以及為什麼容易被漏。
- 舉一個「非程式碼攻擊面」導致真實事件的例子，說明它為什麼軟。
- 「pre-auth 可達」為什麼是最重的加權？你會怎麼判斷一個 sink 是否 pre-auth 可達？

## 延伸閱讀

- **`reading_code` Ch 5–7（偵察、找 entry point、建架構地圖）**——本章的直接前身。回頭讀它的 60 分鐘偵察流程，本章做的是「在那張架構地圖上塗安全權重」。前提：無。兩者合起來就是完整的「進場流程」。
- **OWASP, *Attack Surface Analysis Cheat Sheet***——業界對攻擊面盤點的 checklist。讀它列的「哪些算攻擊面」與「怎麼分層」，跟本章的地圖欄位對照。前提：本章。偏 web 但方法通用。
- **curl 的 *security problems* / CVE 歷史頁面**（curl.se/docs/security.html）——真實 target 的 CVE 富礦。挑幾個協議 parser 的 CVE 看它們出在哪個 entry point，驗證「parser 排第一」的直覺。前提：本章。也是練習 B 的好素材。
- **Manadhata & Wing, *An Attack Surface Metric*, IEEE TSE 2011**——把攻擊面量化的學術嘗試。讀 intro 與 metric 定義建立「面的大小可度量」的直覺，不必啃完形式化。前提：無。想把直覺變成可比較的數字時讀。

你現在會挑 target、會建地圖了。動手時你會需要一份「危險操作速查」——各語言的 sink 到底長什麼樣、對到哪個 CWE、sanitizer 該是什麼。下一章給你一份跨語言 sink 大表，之後寫 query 直接查。

→ [Ch 11 跨語言 sink 目錄](./11-cross-language-sink-catalog.md)
