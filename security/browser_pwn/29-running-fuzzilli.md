# Ch 29 — 實跑 Fuzzilli：build Swift、patch V8、開 session、讀 statistics 與 corpus

> **目標**：把 [Ch 28](./28-fuzzilli-internals.md) 的原理落地成一條你能照著走的操作流程：(1) 裝 Swift toolchain 並 build 出 Fuzzilli 本體；(2) 用正確的 gn args build 一顆**帶 coverage 埋點的 fuzzing d8**；(3) 開一個 fuzzing session、認得指令列參數（`--profile`、`--jobs`、`--storagePath`）；(4) 讀 Fuzzilli 的即時 statistics 與 corpus 目錄結構，知道「跑得健不健康」；(5) 對「長時間 campaign」建立正確的期望值（要跑多久、產出什麼、crash 存哪）。

> **環境**：V8 15.3.0，commit `ab2cad06`，`~/v8build/v8/`。**真跑驗證政策（重要）**：本章分兩塊——**已驗證**的是「V8 端的 fuzzing build flag 確實存在且會拉進 coverage 程式碼」（`v8_fuzzilli=true` → `defines V8_FUZZILLI` + `fuzzilli_cov` source set，本章有真實 `BUILD.gn`/`gni` 佐證）。**未實測（理論預期）**的是：Fuzzilli 本體的 Swift build、完整 `--fuzzilli` d8 build、以及開一個 session 的即時輸出與長時間 campaign 產出——本 batch **未裝 Swift toolchain**，這些一律標「**未實測，理論預期（需裝 Swift + patch/build fuzzing V8 + 長跑）**」，步驟與預期產出以官方 repo 與 saelo 論文為準，**絕不捏造 statistics 或 crash 輸出**。

## 為什麼需要這個？

[Ch 28](./28-fuzzilli-internals.md) 講完原理，你知道 Fuzzilli「為什麼」有效。但漏洞研究是實作學科——**跑得起來、跑得健康、看得懂輸出**，才有機會真的挖到東西。這章是「把引擎發動」的一章：三個組件要對上（Fuzzilli 本體、fuzzing d8、兩者之間的 REPRL 握手），少一個都空轉。

而且這章要幫你建立一個**現實的期望值**。新手常見誤解是「跑 Fuzzilli 一晚上就有 0-day」。真相是：tip-of-tree 的 V8 被 Google 自己的 ClusterFuzz 用上千核心 24/7 轟著，你一台機器很難在 tip 上撿到新洞。Fuzzilli 對**個人**最務實的用途是：(a) 在**你自己 patch 出來的、故意有洞的 V8** 上練習整條 fuzzing → triage 流程；(b) 針對**特定舊 commit / 特定子系統**做 focused fuzzing；(c) 找 [Ch 27](./27-patch-diffing.md) 那種 variant。把期望值擺正，你才不會跑三天沒 crash 就以為自己做錯了。

## 先建立直覺：三個齒輪要咬合

```
   ┌─────────────┐   REPRL (fd + shared mem)   ┌──────────────────────┐
   │  Fuzzilli   │◄───────────────────────────►│  fuzzing d8          │
   │  (Swift)    │  1. 餵下一段 JS             │  (v8_fuzzilli=true,   │
   │             │  2. 讀回 exit status         │   coverage 埋點)      │
   │  - FuzzIL   │  3. 讀 coverage bitmap       │  - --fuzzilli 模式    │
   │  - mutator  │                              │  - __sanitizer_cov_.. │
   │  - corpus   │                              │  - REPRL loop         │
   └─────────────┘                              └──────────────────────┘
        │                                                  │
        │ 存 corpus / crashes / stats                      │ 執行、回報
        ▼                                                  ▼
   ~/fuzz-storage/{corpus,crashes,stats,...}          真的跑你的 JS
```

三個齒輪：**Fuzzilli（Swift 編出來的執行檔）**、**fuzzing d8（特殊 build 的 V8）**、**REPRL 通道（把兩者縫起來的 fd + 共享記憶體）**。這章就是把這三個齒輪各自準備好、讓它們咬合。

## Step 1：build Fuzzilli 本體（Swift）——未實測，理論預期

Fuzzilli 用 **Swift** 寫（不是因為潮，而是 saelo 當初的選擇；Swift 的型別系統適合寫 FuzzIL 那套結構）。所以你要先有 Swift toolchain。

> **本 batch 未裝 Swift，以下步驟未實測，依官方 repo 的 README 整理。**

```bash
# 1. 裝 Swift（Linux）。官方發行版或用 swiftly。約需 Swift 5.x+
#    Ubuntu 上通常下載 swift.org 的 tarball，解壓後把 usr/bin 加進 PATH。
#    驗證：
swift --version        # 預期印出 Swift 版本

# 2. clone 並 build Fuzzilli
git clone https://github.com/googleprojectzero/fuzzilli.git
cd fuzzilli
swift build -c release           # release 模式編，快很多
#   產出：.build/release/FuzzilliCli
```

**預期產出**：一個 `FuzzilliCli` 執行檔。`swift build` 第一次會拉相依、編一陣子（幾分鐘量級）。

> **踩雷（依 repo 常見 issue）**：Swift 版本太舊 / 太新都可能編不過（Fuzzilli 對 Swift 版本有要求，看它 repo 當前 README 標的版本）。WSL 上裝 Swift 要注意相依的 libicu、libpython 等系統庫。這塊是本流程最容易卡的地方，**未實測**，請以你當下 repo README 標的 Swift 版本為準。

## Step 2：build 一顆帶 coverage 的 fuzzing d8

這是**部分已驗證**的一步。Part 1 那顆 `out/x64.release` 的 d8 **不能**拿來給 Fuzzilli——它沒有 coverage 埋點、沒編 `--fuzzilli` 模式。你要另外 build 一顆。

關鍵 gn arg 是 **`v8_fuzzilli=true`**。本 checkout 真實可驗證它的作用：

```
$ grep -n 'v8_fuzzilli' ~/v8build/v8/gni/v8.gni
194:  v8_fuzzilli = false          # 預設關

$ sed -n '1466,1468p' ~/v8build/v8/BUILD.gn
  if (v8_fuzzilli) {
    defines += [ "V8_FUZZILLI" ]
  }

$ sed -n '8985,8992p' ~/v8build/v8/BUILD.gn
if (v8_fuzzilli) {
  v8_source_set("fuzzilli_cov") {
    ...
      "src/fuzzilli/cov.cc",
      "src/fuzzilli/cov.h",
```

**已驗證的事實**：`v8_fuzzilli=true` 會 (a) 定義 `V8_FUZZILLI` 巨集（打開 `fuzzilli.cc` 裡的 `--fuzzilli`/REPRL/`Fuzzilli()` 內建），(b) 把 `fuzzilli_cov`（`cov.cc`/`cov.h`，[Ch 28](./28-fuzzilli-internals.md) 的共享記憶體 edge bitmap）編進來。這兩塊就是齒輪咬合的 V8 端。

Fuzzilli 官方對 V8 target 提供一份 build 腳本（repo 的 `Targets/V8/`）。它做的事，本質上是一組 gn args：

```bash
# 依 Fuzzilli 官方 Targets/V8/fuzzbuild.sh 的精神（未實測，數值以 repo 為準）：
cd ~/v8build/v8
gn gen out/fuzzbuild --args='
  is_debug=false
  target_cpu="x64"
  v8_fuzzilli=true
  sanitizer_coverage_flags="trace-pc-guard"
  v8_enable_object_print=true
  v8_enable_verify_heap=true
  ...
'
ninja -C out/fuzzbuild d8
```

逐點講這幾個 arg 為什麼在（呼應 [Ch 28](./28-fuzzilli-internals.md) 的機制）：

| gn arg | 作用 |
|---|---|
| `v8_fuzzilli=true` | **總開關**：`V8_FUZZILLI` + REPRL + `Fuzzilli()` + `fuzzilli_cov`（已驗證） |
| `sanitizer_coverage_flags="trace-pc-guard"` | 讓 compiler 在每個 edge 埋 `__sanitizer_cov_trace_pc_guard`，餵 [Ch 28](./28-fuzzilli-internals.md) 的 2MB bitmap |
| `v8_enable_verify_heap=true` | 開啟堆一致性檢查——**讓「輕微的堆損壞」更早 abort、更好被 Fuzzilli 當 crash 抓到**（提升訊號） |
| （常配）`dcheck_always_on=true` | 讓 release build 也跑 `DCHECK`——很多「不是立即 crash 但狀態已錯」的 bug 靠 DCHECK 現形（對應 `FUZZILLI_CRASH` case 2/8） |
| `is_debug=false` | release，貼近真實目標、又夠快 |

> **為什麼要開 verify-heap / dcheck**：純 release 的 d8 遇到「堆被搞亂但還沒立刻爆」的狀態會默默跑下去，fuzzer 看不到 crash 就當它沒事、丟掉這個珍貴 testcase。開了這些檢查，堆一不一致就 abort，fuzzer 才抓得到。這是「把弱訊號放大成 crash」的關鍵——同一個 tradeoff 你在 [Ch 0](./00-environment-setup.md) 的 dcheck 討論見過。

**驗證 build 成功（理論預期）**：`out/fuzzbuild/d8 --fuzzilli` 應該進入等待 REPRL 握手的狀態（不是一般 REPL）。**本 batch 未 build 這顆 d8，未實測此輸出。**

## Step 3：patch 一顆「故意有洞」的 V8 來練習

因為 tip-of-tree 很難撿到真洞，**學習階段最有價值的做法是自己種一個洞**，確認你的 fuzzer 抓得到它。兩條路：

1. **用內建的 `FUZZILLI_CRASH`（最省事）**：不用改任何 code。Fuzzilli 啟動時會自己跑 `FUZZILLI_CRASH` 做管線自檢（[Ch 28](./28-fuzzilli-internals.md)）；你也能把它當「已知會 crash 的 testcase」驗證 crash 存檔流程。它涵蓋 wild write（case 3）、UAF（case 4）、OOB（case 5/6）等（[Ch 30](./30-exploitability-triage.md) 用它練 triage）。
2. **手動植入一個真的 JS-triggerable 洞**：在某個 Torque 內建或 TurboFan reducer 裡故意拿掉一個 bounds check / clamp（例如把 [Ch 26](./26-reading-v8-source-commits.md) 那個 `array-lastindexof.tq` 的 `if (k >= elementsLen) k = elementsLen - 1;` clamp 刪掉），重編 fuzzing d8。然後看 Fuzzilli 能不能在合理時間內生出觸發它的 JS。這是驗證「我的整條 pipeline 真的會找洞」最扎實的方式。

> 練習 E（[practice-e](./practice-e-fuzzilli-crash-triage.md)）會帶你走「植入 → fuzz/觸發 → triage」完整一遍。

## Step 4：開一個 fuzzing session——未實測，理論預期

齒輪都在，發動：

```bash
# 依官方 CLI（未實測，參數以 repo 當前版本為準）
./fuzzilli/.build/release/FuzzilliCli \
    --profile=v8 \
    --jobs=8 \
    --storagePath=~/fuzz-storage \
    ~/v8build/v8/out/fuzzbuild/d8
```

關鍵參數逐個講：

| 參數 | 意義 |
|---|---|
| `--profile=v8` | 選 **V8 的 profile**：告訴 Fuzzilli 目標引擎特有的內建、觸發優化的方式（`%OptimizeFunctionOnNextCall` 等）、REPRL 細節。選錯 profile = 生成的 JS 對不上目標 |
| `--jobs=N` | 平行跑 N 個 fuzzing worker（各一顆 d8 process）。設成核心數量級 |
| `--storagePath=DIR` | corpus、crashes、statistics 存哪 |
| 最後的路徑 | 你的 fuzzing d8 執行檔 |
| （常用）`--timeout=ms` | 單一 testcase 的執行逾時，避免無窮迴圈卡死 worker |
| （進階）`--resume` | 從上次的 corpus 續跑，不從零開始 |

**啟動時 Fuzzilli 會先做自檢**（跑 `FUZZILLI_CRASH` 各 case，確認 crash 偵測管線通、REPRL 握手成功、coverage 讀得到）。**這步失敗**（最常見：`--profile` / build config 不對、REPRL 握手不上）就會直接報錯退出——這是驗證「三齒輪咬合」的第一道關卡。

## Step 5：讀 statistics——跑得健不健康

Fuzzilli 執行中會定期印統計。**以下欄位是 Fuzzilli 會輸出的類型（依官方/論文），具體數值本 batch 未實測、不捏造**。你要學會「看這些數字判斷跑得健不健康」：

| statistics 欄位（類型） | 它告訴你什麼 | 健康 vs 不健康 |
|---|---|---|
| **Coverage（%）** | 累積點亮了多少 edge（相對已知可達） | 開頭快速爬升 → 逐漸趨緩是正常的；**完全不動**代表變異沒進展（profile 錯？corpus 死了？） |
| **Exec/s（吞吐）** | 每秒跑幾個 testcase | REPRL 正常時應該是**數百到數千**量級；掉到個位數代表 REPRL 沒生效（退回 fork-per-exec）或 timeout 太多 |
| **Valid samples（合法率）** | 生成的程式有多少跑得完不 early-abort | [Ch 28](./28-fuzzilli-internals.md) 說的**八九成**量級；異常低代表生成/lifting 有問題 |
| **Corpus size** | 目前保留了幾個「有趣」程式 | 持續成長 → 健康；停滯不長 → 探索卡住 |
| **Crashes / Timeouts** | 找到幾個 crash、幾個逾時 | crash 是你要的訊號；timeout 多是雜訊，太多要調 `--timeout` |

**判斷健康的心法**：開頭 coverage 猛爬（因為到處都是沒走過的 edge）、然後爬升變慢（好摘的果子摘完了）是**正常曲線**。你要警惕的是：`Exec/s` 掉到很低（REPRL 壞了）、`Valid samples` 很低（生成壞了）、`Coverage` 從一開始就不動（profile/build 對不上）。這三個是「跑錯了」的訊號，不是「還沒找到洞」。

## Step 6：corpus 與 crashes 目錄

`--storagePath` 底下（結構依官方，未實測具體檔名）大致：

```
~/fuzz-storage/
├── corpus/         # 保留的「有趣」程式（觸發過新覆蓋），FuzzIL/JS 形式
├── crashes/        # 找到的 crash：觸發的程式 + crash 資訊
├── stats/          # 統計快照（可畫覆蓋率曲線）
└── settings.json   # 這次 session 的設定（profile、seed…）
```

- **crashes/**：這是你 fuzzing 的**產出**。每個 crash 存下「能觸發它的程式」。但**注意**：一個原始 crash 檔常常又長又髒（Fuzzilli 為了觸發，會堆一堆無關的操作）。**你不能直接把它當 PoC**——要先**最小化（minimize）**成「只保留觸發所需的最小程式」，再判斷可利用性。這正是 [Ch 30](./30-exploitability-triage.md) 的主題。
- **corpus/**：跑久了會膨脹，可用 Fuzzilli 的功能做 corpus minimization（蒸餾成維持相同覆蓋的最小集），加速後續。

## 常見故障排除：三齒輪咬不上時

fuzzing 環境最折磨人的是「跑不起來但不知道哪個齒輪壞」。按這個順序排查（從最底層往上）：

| 症狀 | 最可能原因 | 怎麼確認/修 |
|---|---|---|
| `FuzzilliCli` 編不出來 | Swift 版本不符 | 對 repo 當前 README 標的 Swift 版本；檢查 libicu/libpython 相依 |
| 啟動就報「REPRL handshake failed」 | d8 build config 錯 | 確認 `v8_fuzzilli=true`（親驗 `defines V8_FUZZILLI`）；`out/fuzzbuild/d8 --fuzzilli` 該進等待態而非 REPL |
| 啟動自檢（`FUZZILLI_CRASH`）不過 | crash 偵測管線斷 | 通常也是 build/profile 問題；確認 `--profile=v8` |
| 跑起來但 Coverage 永遠 0 | 漏了 coverage flag | build 沒加 `sanitizer_coverage_flags="trace-pc-guard"`，沒 edge 資料可讀 |
| Exec/s 個位數 | REPRL 沒生效 / timeout 淹沒 | 檢查 REPRL；調 `--timeout`；看是不是每個 testcase 都逾時 |
| Valid samples 很低 | profile 對不上目標 | `--profile` 選錯，或 d8 版本和 profile 假設的內建對不上 |

**排查心法**：從「最底層的齒輪」往上。Swift build 不過 → 連 Fuzzilli 都沒有。REPRL 握手失敗 → 通道沒通，coverage/session 都免談。Coverage 為 0 → 通道通了但 instrumentation 沒埋。一層一層確認，別跳。

## 一份針對 TurboFan 的 focused 設定範例

通用 fuzzing 撒網，但你若想**專打 TurboFan type confusion**（[Ch 2](./02-v8-architecture.md) 的主礦），要把火力集中。概念上（實際參數依 repo，未實測）：

- **profile 層面**：調高「生成觸發優化的程式」的權重——多產熱身迴圈、多用會逼 JIT 的模式，讓生成的 JS 更容易升到 TurboFan/Maglev。
- **build 層面**：可加 `--trace-opt`/`--trace-deopt` 類的觀測（debug 用），或開更多 DCHECK 讓「優化器內部不一致」提早現形。
- **種子層面**：把已知的 TurboFan bug regression test（[Ch 31](./31-oss-fuzz-regression.md)）compile 成 FuzzIL 當種子，讓 fuzzer 從「已知有趣的優化器路徑」附近開始變異。

**通用 vs focused 的取捨**：通用覆蓋廣但淺，focused 深但可能錯過別處。個人算力有限時，focused（挑 ClusterFuzz 沒重度覆蓋的子系統深鑽）通常比和 ClusterFuzz 硬碰通用 fuzzing 划算——這是 [Ch 31](./31-oss-fuzz-regression.md) 「選戰場」的實作面。

## 對比：Part 1 的 d8 vs fuzzing d8

| 面向 | Part 1 `out/x64.release` d8 | fuzzing `out/fuzzbuild` d8 |
|---|---|---|
| 用途 | 手動打 exploit、`%DebugPrint`、gdb | 給 Fuzzilli 自動化跑 |
| coverage 埋點 | 無 | **有**（`sanitizer-coverage=trace-pc-guard`） |
| `--fuzzilli` / REPRL | 無 | **有**（`v8_fuzzilli=true`，已驗證會定義 `V8_FUZZILLI`） |
| 堆檢查 | 一般 release | 常開 `verify_heap` / `dcheck_always_on`（放大弱訊號） |
| 跑一段 JS | 一般 REPL / `-e` | 等 REPRL 握手，逐個吃 testcase |

**兩顆 d8 不能混用**。拿 Part 1 的 d8 餵 Fuzzilli，會在自檢/REPRL 握手就失敗。

## 踩雷集錦

1. **拿 Part 1 的 d8 給 Fuzzilli**：沒 coverage、沒 `--fuzzilli`，REPRL 握手直接失敗。一定要另 build `v8_fuzzilli=true` 那顆。
2. **忘了開 coverage flag**：只設 `v8_fuzzilli=true`、漏了 `sanitizer_coverage_flags`，Fuzzilli 跑起來 coverage 永遠不動（沒 edge 資料可讀），退化成盲 fuzzing。
3. **`--profile` 選錯或漏了**：Fuzzilli 支援多引擎，profile 決定它認得哪些內建、怎麼觸發優化。V8 就要 `--profile=v8`。錯的 profile → 合法率暴跌、覆蓋停滯。
4. **在 tip-of-tree 上期待撿到 0-day**：ClusterFuzz 上千核心早轟過了。個人 fuzzing 對「自種的洞 / 舊 commit / 特定子系統 / variant」才務實。跑三天沒 crash 不代表你做錯。
5. **把原始 crash 檔當 PoC**：它又長又髒，得先 minimize 再 triage。直接拿去打會被雜訊淹沒。
6. **不開 verify-heap/dcheck 導致漏抓**：純 release 對「堆已壞但沒立刻爆」的狀態默默跑過，fuzzer 看不到 crash 就丟掉珍貴 testcase。fuzzing build 要放大弱訊號。
7. **Exec/s 個位數卻以為正常**：那代表 REPRL 沒生效或 timeout 淹沒。REPRL 對的話應該數百到數千 exec/s。

## 進階：再往深一層

- **針對性 fuzzing（focused）**：改 Fuzzilli 的 CodeGen 權重/profile，讓它多生「陣列操作 + 觸發優化」的程式，把火力集中在 TurboFan/Maglev（[Ch 28](./28-fuzzilli-internals.md) 的攻擊面主礦）。通用 fuzzing 撒網，focused fuzzing 鑽井。
- **differential / miscompilation 模式**：不只找 crash，還比對「開優化 vs 關優化」對同一段 JS 的輸出，不一致 = 潛在 miscompile（[Ch 27](./27-patch-diffing.md) 那類靜默 bug）。這能挖到「不 crash 但算錯」的深洞。
- **分散式 fuzzing**：Fuzzilli 支援多機協作、共享 corpus。個人做不到 ClusterFuzz 規模，但幾台機器共享 corpus 仍有意義。
- **餵種子（corpus seeding）**：把已知的 PoC / regression test compile 成 FuzzIL 丟進 corpus 當種子，引導 fuzzer 從「已知有趣的地方」附近開始變異——找 variant 的利器。
- **覆蓋率曲線分析**：把 `stats/` 的覆蓋率隨時間畫出來。曲線「平掉」代表現有策略探索到頂，該換 profile / 加種子 / 做 minimization 重啟。這是判斷「該不該繼續跑」的量化依據。

## 動手練習

> 涉及真的 build Swift / 跑 session 的部分，本 batch 未實測；以下標「(需 Swift)」的請在你裝好環境後做，其餘可在本 checkout 直接驗證。

1. **(可直接驗證)** 在本 checkout 跑 `grep -n 'v8_fuzzilli' ~/v8build/v8/gni/v8.gni` 和 `sed -n '1466,1468p' ~/v8build/v8/BUILD.gn`，親眼確認 `v8_fuzzilli=true` 會 `defines V8_FUZZILLI`。再 `sed -n '8985,8992p' BUILD.gn` 確認它拉進 `cov.cc`/`cov.h`。寫下：這個 flag 到底打開了哪兩塊東西？
2. **(需 Swift)** 裝 Swift、`swift build -c release` 出 `FuzzilliCli`。記錄你撞到的相依問題與 Swift 版本——這塊是本流程最容易卡的地方。
3. **(需 Swift + fuzzing build)** 用官方 `Targets/V8/` 腳本 build 一顆 fuzzing d8，`out/fuzzbuild/d8 --fuzzilli` 確認它進入等待 REPRL 的狀態（而非一般 REPL）。
4. **(需完整環境)** 開一個 `--jobs=4` 的 session 跑 10 分鐘。記錄 Exec/s、Coverage、Valid samples 的**趨勢**（不是絕對值），對照本章的「健康 vs 不健康」判斷你的跑得對不對。
5. **(進階)** 手動把 [Ch 26](./26-reading-v8-source-commits.md) `array-lastindexof.tq` 的 clamp 刪掉、重編 fuzzing d8，看 Fuzzilli 能否生出觸發它的 JS（這驗證你的整條 pipeline 真會找洞）——接 [練習 E](./practice-e-fuzzilli-crash-triage.md)。

## 本章重點整理

- 三個齒輪要咬合：**Fuzzilli 本體（Swift）**、**fuzzing d8（`v8_fuzzilli=true` + coverage 埋點）**、**REPRL 通道**。少一個都空轉。
- **已驗證**：`v8_fuzzilli=true` 會 `defines V8_FUZZILLI` 並編進 `fuzzilli_cov`（`cov.cc/.h`）——這是 V8 端的 coverage/REPRL 來源。
- fuzzing build 要配 `sanitizer_coverage_flags="trace-pc-guard"`（餵 bitmap）與 `verify_heap`/`dcheck_always_on`（**放大弱訊號**，讓堆一壞就 abort）。
- session 參數：`--profile=v8`（認引擎特性，選錯就廢）、`--jobs`、`--storagePath`；啟動先跑 `FUZZILLI_CRASH` 自檢。
- 讀 statistics 看**健康**：Coverage 開頭爬後趨緩(正常)、Exec/s 數百到數千(REPRL 對)、Valid 八九成、Corpus 成長。Exec/s 個位數 or Coverage 不動 = 跑錯了。
- crashes/ 是產出，但**原始 crash 又長又髒，得先 minimize 再 triage**（[Ch 30](./30-exploitability-triage.md)）。tip-of-tree 難撿新洞——個人 fuzzing 對自種洞/舊 commit/variant 才務實。

## 自我檢核

- [ ] 能解釋為什麼 Part 1 的 d8 不能給 Fuzzilli、fuzzing d8 多了哪些東西（coverage 埋點、`V8_FUZZILLI`、堆檢查）
- [ ] 知道 `v8_fuzzilli=true` 具體打開了什麼（親眼在 BUILD.gn/gni 驗證過）
- [ ] 說得出 `--profile=v8` 的作用，以及選錯會怎樣
- [ ] 看到一份 statistics，能判斷「跑得健康 / REPRL 壞了 / 生成壞了 / profile 錯了」
- [ ] 知道為什麼要開 verify-heap/dcheck（放大弱訊號），以及原始 crash 為何不能直接當 PoC
- [ ] 對「tip-of-tree 個人 fuzzing 的現實產出」有正確期待，知道個人該把火力放哪
- [ ] （面試題）「你會怎麼設置一場針對 V8 TurboFan 的 fuzzing？從 build config 到 profile 到怎麼判斷跑得健康。」

## 延伸閱讀

- **[Fuzzilli 官方 repo 的 `Targets/V8/` 與 README — github.com/googleprojectzero/fuzzilli](https://github.com/googleprojectzero/fuzzilli)**
  - **讀哪裡**：`Targets/V8/fuzzbuild.sh`（V8 的實際 gn args）、README 的 “Usage” 與 CLI 參數。本章的 build/session 步驟就照它，**跑之前務必對照 repo 當前版本**（flag 會變）。
  - **和本章的關聯**：本章標「未實測」的每一步，這裡是你真跑時的第一手依據。

- **[saelo 碩論《FuzzIL: Coverage Guided Fuzzing for JavaScript Engines》](https://saelo.github.io/papers/thesis.pdf)**
  - **讀哪裡**：實驗章節——覆蓋率曲線、合法率、吞吐的實測數字。本章「健康 vs 不健康」的判準源自這裡。
  - **和本章的關聯**：讓你對 statistics 的「正常長相」有量化的錨。

- **[V8 `src/fuzzilli/`（BUILD.gn 相關 target、cov.cc、fuzzilli.cc）](https://source.chromium.org/chromium/chromium/src/+/main:v8/src/fuzzilli/)**
  - **讀哪裡**：對照本章的 `v8_fuzzilli` build 邏輯與 `--fuzzilli` REPRL 實作。
  - **和本章的關聯**：Step 2 的「已驗證」佐證就在這；想深入 REPRL 握手細節看 `fuzzilli.cc`。

- **[Chrome Security / ClusterFuzz 文件 — google.github.io/clusterfuzz](https://google.github.io/clusterfuzz/)**
  - **這篇說什麼**：Google 大規模自動化 fuzzing 的基礎設施。
  - **和本章的關聯**：理解「為什麼 tip-of-tree 個人難撿洞」——你在和這台機器競爭。也是 [Ch 31](./31-oss-fuzz-regression.md) OSS-Fuzz 的背景。

跑得起來、看得懂 stats，你會開始撿到 crash。但一個 crash 不等於一個漏洞、更不等於一個可利用的漏洞。下一章教你面對一個 crash 怎麼分類、怎麼最小化、怎麼判斷它到底是不是安全問題、朝哪個 primitive 走。

→ [Ch 30 — 從 crash 判斷可利用性：ASan、crash 分類、testcase 最小化](./30-exploitability-triage.md)
