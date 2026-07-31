# 練習 E — 設定 Fuzzilli 跑一個 session，對一個 crash 做 triage

> **目標**：把 [Ch 28](./28-fuzzilli-internals.md)、[Ch 29](./29-running-fuzzilli.md)、[Ch 30](./30-exploitability-triage.md) 串成一次完整的「主動找洞」實戰——build 起 Fuzzilli + fuzzing d8、開一個 session、拿一個**可人工植入的 crash** 做完整 triage（分類 → ASan 讀報告 → 最小化 → 判可利用性方向）。這個練習故意用「可控的、已知型別的 crash」（`FUZZILLI_CRASH` 內建，或自己 patch 掉一個 check），讓你在**知道正確答案**的情況下練整條 triage pipeline，把「讀 ASan 報告 → 分類 → 判價值」練到可靠。**防禦研究同樣需要**：要評估一個 crash 是不是安全問題、多嚴重，就是這套流程。

> **環境**：V8 15.3.0，commit `ab2cad06`，`~/v8build/v8/`。**真跑驗證政策（重要）**：**已驗證**的是 V8 端的 fuzzing 支援真實存在（`v8_fuzzilli=true` → `V8_FUZZILLI` + `fuzzilli_cov`，[Ch 29](./29-running-fuzzilli.md) 有 `BUILD.gn` 佐證）、以及 `FUZZILLI_CRASH` 的 crash 清單是官方原始碼（`src/fuzzilli/fuzzilli.cc`，[Ch 28/30](./28-fuzzilli-internals.md) 引過）。**未實測（理論預期）**：Swift build Fuzzilli、build 完整 fuzzing d8、跑一個真 session、觸發 ASan 報告——本 batch 未裝 Swift、未 build ASan/fuzzing d8。所有涉及即時 statistics / ASan 具體輸出的部分，本練習標「**未實測，理論預期**」，格式以官方 repo / ASan 文件為準，**不捏造數字或位址**。

## 這個練習在幹嘛（先讀）

fuzzing 的真正難點不在「跑起來」，而在「跑起來之後怎麼處理一堆 crash」。這個練習讓你在**受控條件**下走完整條路：因為你用的是**已知型別**的 crash（`FUZZILLI_CRASH` 或你自己植入的洞），你能拿「正確答案」校準自己的判斷——「我讀 ASan 報告判成 UAF，對不對？」「我最小化到 10 行，root cause 抓對了嗎？」。等你在受控條件下練熟，面對真實 fuzzing 的髒 crash 才有底。

**為什麼用受控 crash 而不是直接等真的 fuzzing crash**：真實 fuzzing 在 tip-of-tree 上可能跑好幾天才給你一個 crash（[Ch 29](./29-running-fuzzilli.md) 講過 ClusterFuzz 已經把地面掃過）。用受控 crash，你**十分鐘就能練一輪完整 triage**，而且知道正確答案能立刻校準——這是把 triage 肌肉練起來最有效率的方式。等肌肉練好，真 crash 來的時候你才不會手忙腳亂。這跟練拳先打沙包、不是一上場就實戰同理。

**triage 的產能意義**：一場 fuzzing 給你 30 個 crash，會 triage 的人一小時篩出值錢的 1-2 個、丟掉 28 個垃圾；不會的人可能花一整天在一個良性 `CHECK` 上鑽牛角尖，或錯過一個其實很肥的 UAF。這個練習練的就是那個「一小時篩對」的能力。

## 規格：你要交付什麼

分成 **A（環境）** 和 **B（triage）** 兩大塊。B 是重點。

### Part A：把 fuzzing 環境架起來（理論預期為主）

1. **build Fuzzilli 本體**（Swift）：`swift build -c release`，產出 `FuzzilliCli`。記錄 Swift 版本與你撞到的相依問題（[Ch 29](./29-running-fuzzilli.md) 說這是最容易卡的地方）。
2. **build 一顆 fuzzing d8**：`v8_fuzzilli=true` + `sanitizer_coverage_flags="trace-pc-guard"` + `v8_enable_verify_heap=true`（放大弱訊號）。**另外** build 一顆 **ASan d8**（`is_asan=true`）給 triage 用。
3. **驗證三齒輪咬合**：`out/fuzzbuild/d8 --fuzzilli` 進入等待 REPRL 的狀態（非一般 REPL）；`FuzzilliCli --profile=v8 ... out/fuzzbuild/d8` 啟動時的**自檢**（跑 `FUZZILLI_CRASH` 各 case）要通過——這證明 REPRL + coverage + crash 偵測都通。

**Part A 的三顆 d8（別搞混）**：這個練習你手上會有三顆不同 build 的 d8，各司其職——

| d8 | gn args 關鍵 | 幹嘛用 |
|---|---|---|
| Part 1 那顆 | `v8_enable_object_print=true`（[Ch 0](./00-environment-setup.md)） | 手動看 `%DebugPrint`、gdb。**不能**給 Fuzzilli |
| fuzzing d8 | `v8_fuzzilli=true` + `sanitizer_coverage_flags="trace-pc-guard"` + `v8_enable_verify_heap=true` | 給 Fuzzilli 跑（REPRL + coverage） |
| ASan d8 | `is_asan=true` + `v8_enable_object_print=true` | **triage 專用**：把安靜的 OOB/UAF 變成大聲的報告 |

三顆並存（不同 `out/` 目錄），別拿錯。最常見錯誤是拿 Part 1 的 d8 餵 Fuzzilli（REPRL 握手直接失敗），或在非 ASan d8 上 triage UAF（看不到 `heap-use-after-free`）。

### Part B：對一個 crash 做完整 triage（核心）

拿到一個 crash（來源見「選題」），產出一份 **triage 報告**，涵蓋：

1. **crash 來源與初步觀察**：哪來的？在哪顆 d8（fuzzing / ASan / release）上、怎麼觸發的？第一眼看到什麼（SIGSEGV？ASan 報告？乾淨 abort？）。
2. **是不是安全問題**：用 [Ch 30](./30-exploitability-triage.md) 的框架判斷——這是「V8 主動、乾淨 abort（`CHECK`/OOM/stack overflow，多半良性）」還是「碰到不該碰的記憶體（OOB/UAF/type confusion，是漏洞）」？依據是什麼？
3. **分類**：若是記憶體破壞，是哪一類（OOB read / OOB write / UAF / type confusion）？從 ASan 報告的哪些欄位判定（錯誤類型、read/write、越界方向距離、alloc/free stack）？
4. **最小化**：把觸發程式削成最小 PoC，記錄「刪到剩幾行還 crash 在同一個地方」。附最小 PoC。
5. **可利用性方向**：這個 crash 的價值（可控性）如何？越界方向/距離/被打物件可控嗎？往哪個 primitive 走（接 Part 3/4）？還是判定「良性、不值得」？
6. **校準**：因為你用的是**已知型別**的 crash，對答案——你的分類判對了嗎？哪裡差點判錯、為什麼？

## 選題：三種 crash 來源

**來源 1（最省事，推薦）**：`FUZZILLI_CRASH` 內建（要 fuzzing build 才有 `Fuzzilli` 函式）。
- 挑幾個不同 case 練分類：
  - `Fuzzilli("FUZZILLI_CRASH", 4)` → **UAF**（要 ASan 才明確報 `heap-use-after-free`）
  - `("FUZZILLI_CRASH", 5)` → **OOB read**
  - `("FUZZILLI_CRASH", 6)` → **OOB write**
  - `("FUZZILLI_CRASH", 3)` → **wild write**（對 `0x414141414141`）
  - `("FUZZILLI_CRASH", 1)` → `CHECK(false)` → **良性 abort**（練「判成不可利用」）
- 優點：**每個 case 你事先知道正確答案**（[Ch 30](./30-exploitability-triage.md) 那張表），完美校準。case 4 特別重要——它原始碼裡 `#ifndef V8_USE_ADDRESS_SANITIZER` 那段證明「UAF 非 ASan build 可能不現形」，親自體會為什麼 triage 要 ASan。

**來源 2（更真實）**：自己 patch 掉一個真的 JS-triggerable 洞。
- 例如把 [Ch 26](./26-reading-v8-source-commits.md) 的 `src/builtins/array-lastindexof.tq` 那個 `if (k >= elementsLen) k = elementsLen - 1;` clamp 刪掉，重編 fuzzing/ASan d8。
- 然後寫一段 JS（`fromIndex` 用會縮短陣列的 getter），或讓 Fuzzilli 去生，觸發 OOB read。用 ASan 觀察報告。
- 優點：這是**真的 JS 觸發的記憶體破壞**，最接近實戰 triage。

**來源 3（最真實，看運氣）**：在來源 2 的植洞 d8 上，真的開一個 Fuzzilli session，看它能否**自己生出**觸發那個植入洞的 JS。
- 優點：驗證你的整條 fuzzing pipeline 真的會「發現」洞、而不只是你手餵。
- 缺點：要 Swift 環境 + 較長時間，且不保證短時間內命中。

## 交付形式

一份 markdown：Part A 的環境記錄（含你實際跑過/驗證過的、和標「未實測」的）+ Part B 的 triage 報告（六點都寫）+ 最小 PoC。**triage 推理完整 > 篇幅**。

## triage checklist（拿到任何 crash 先跑這張）

把這張表當你面對 crash 的固定動作，一格一格填，逼自己不跳步：

```
[ ] 1. 死法是什麼？   → 乾淨 abort(CHECK/FATAL/RangeError/OOM)？還是碰記憶體(SIGSEGV/ASan)？
[ ] 2. 在哪顆 d8？    → release 不 crash ≠ 沒事；換 ASan build 再看一次
[ ] 3. ASan 首行類型？ → heap-buffer-overflow / heap-use-after-free / stack-overflow / 無
[ ] 4. read or write？ → READ→先想 leak；WRITE→通常更嚴重
[ ] 5. 方向與距離？    → "N bytes to the right/left of M-byte region"，可控嗎？
[ ] 6. 打到什麼？      → alloc stack 指出是哪個 V8 物件；後面剛好是 length/map/指標=黃金
[ ] 7. sandbox？      → 有 sandbox violation = 越界被框住、還沒打穿(Ch34)
[ ] 8. 最小化         → 削到最小、確認 crash 在【同一個地方】
[ ] 9. 可控性評分     → 固定越界無關欄位(低) ↔ 可控越界到 length(高)
[ ] 10. 去重          → 和已有 crash 是不是同一個 bug 的不同觸發？
```

第 1、2 步就能篩掉大半垃圾（乾淨 abort 的良性 crash）。第 5、6、9 步決定它值不值得寫 exploit。

---

## ASan 報告解剖：一份 UAF 報告該怎麼讀（理論預期格式）

triage 的核心讀圖能力。以 UAF 為例，ASan 報告有**三個 stack trace**，缺一不可：

```
==PID==ERROR: AddressSanitizer: heap-use-after-free on address 0xADDR ...
READ of size 8 at 0xADDR thread T0          ← (1) crash 現場：現在誰在碰
    #0 ... 這裡是「用已釋放記憶體」的那行
0xADDR is located 0 bytes inside of 8-byte region [...,...)
freed by thread T0 here:                    ← (2) 誰把它放了（free）
    #0 operator delete / Free
    #1 ... V8 哪個函式釋放的
previously allocated by thread T0 here:     ← (3) 誰配的（alloc）
    #0 operator new / Allocate
    #1 ... V8 哪個函式配的
```

- **(1) crash stack**：漏洞被觸發的那一刻、程式在哪。這是「怎麼觸發」的線索。
- **(2) free stack**：那塊記憶體**何時、被誰釋放**。UAF 的核心矛盾：這裡放了、但 (1) 又用了。
- **(3) alloc stack**：那塊記憶體**原本是什麼物件**。常直接點出「喔是個 JSArrayBuffer 的 backing store」——root cause 地圖。

**把三個 stack 連起來讀**：「(3) 配了一個 X 物件 → (2) 某操作把 X 釋放了 → (1) 但某個舊指標還指著 X 並去用它」。這三步就是 UAF 的完整故事，也是你判斷「free 和 use 之間我能不能塞進一個重佔」的依據——那決定可利用性。

OOB 報告則沒有 free stack，換成 **`located N bytes to the right of M-byte region allocated by ...`**——直接給你越界方向、距離、和被越界物件的 alloc stack。

---

## 卡點與提示

- **卡在「Swift build 不過」**：Fuzzilli 對 Swift 版本敏感，照它 repo **當前** README 標的版本裝。WSL 上注意 libicu/libpython 相依。這塊本 batch 未實測，是已知痛點。
- **卡在「REPRL 握手失敗 / 自檢不過」**：99% 是 d8 build 不對——漏了 `v8_fuzzilli=true` 或 `sanitizer_coverage_flags`，或 `--profile` 不是 `v8`。回 [Ch 29](./29-running-fuzzilli.md) 對 build config。
- **卡在「跑 case 4 沒看到 UAF 報告」**：你用的是**非 ASan** d8。case 4 的原始碼寫明非 ASan 時 UAF 不明顯（要額外 wild write 才 crash）。換 ASan d8 跑才會報 `heap-use-after-free`——這正是本練習要你體會的重點。
- **卡在「分不清良性 vs 漏洞」**：問一句——「是 V8 **主動、乾淨地** abort，還是**碰到了不該碰的記憶體**？」前者（case 1 的 `CHECK`）多半良性；後者（case 4/5/6）是漏洞。[Ch 30](./30-exploitability-triage.md) 的四大類表。
- **卡在「最小化削出不同的 crash」**：鐵律——刪東西後必須確認「還 crash **且在同一個地方**（同 ASan 類型、同出錯點）」。削出無關的新 crash 要還原。
- **卡在「不會估可利用性」**：價值 = **可控性**。問：越界方向/距離可控嗎？打到的是相鄰物件的 length/map/指標（黃金）還是無關欄位？UAF 能穩定重佔嗎？（[Ch 30](./30-exploitability-triage.md) 進階）

---

## 參考流程（做完再看）

<details>
<summary>展開：用 <code>FUZZILLI_CRASH</code> case 4（UAF）走完整 triage，並校準</summary>

以 case 4 為例，填進 Part B 的六點格式，當範本。**ASan 具體輸出為理論預期格式，未實測、不填真實位址。**

### 1. crash 來源與初步觀察
- 來源：`Fuzzilli("FUZZILLI_CRASH", 4)`（fuzzing build 內建函式）。
- 先在**非 ASan** fuzzing d8 上跑：可能**不明顯 crash**（原始碼 `#ifndef V8_USE_ADDRESS_SANITIZER` 那段會補一個 wild write 才讓它在非 ASan 也 crash）。→ 這本身就是第一個教訓：UAF 在沒 sanitizer 時可能安靜。
- 換 **ASan d8** 跑：ASan 立刻 abort 並報告。

### 2. 是不是安全問題
- ASan 報 `heap-use-after-free` → **是記憶體破壞、是安全問題**（不是 V8 主動 abort，是真的用了已釋放記憶體）。對照：case 1 的 `CHECK(false)` 是 V8 主動乾淨 abort → 判**良性**。

### 3. 分類
- ASan 報告首行 `heap-use-after-free` → **UAF**。
- 讀關鍵欄位：`READ/WRITE of size N`（case 4 是讀已釋放的 `vec->at(0)`）、`freed by thread ... here`（free 的 stack）、`previously allocated by ... here`（alloc 的 stack）。這**三個 stack**（crash/free/alloc）是 UAF 的地圖——哪裡配、哪裡放、哪裡又碰。

### 4. 最小化
- `FUZZILLI_CRASH` 的觸發已經是最小（就一行 `Fuzzilli("FUZZILLI_CRASH", 4)`）。若是真實 fuzzing crash，這裡要 delta-debug 削到最小、確認 crash 點不變。
- 記錄：最小 PoC = `Fuzzilli("FUZZILLI_CRASH", 4);`（受控範例本就最小）。

### 5. 可利用性方向
- 這是 Fuzzilli **自檢用**的合成 UAF（一個 `std::vector` delete 後 at(0)），**不是** V8 引擎的真洞——**可控性極低**（它就是要驗證「ASan 抓不抓得到 UAF」）。所以判定：**作為漏洞不值得**（它是測試工具，非引擎缺陷）。
- 但**當作 triage 演練**：如果這是**真的** JS-triggerable UAF（如來源 2 植入的洞），升級路徑是——free 後**重佔**：spray 你控制內容的物件填回那塊記憶體 → 舊指標現在指向你控制的物件 → **type confusion** → addrof/fakeobj → 任意讀寫（Part 3/4）。UAF 是 JS engine 最好用的一類 primitive 之一。

### 6. 校準
- 已知答案（[Ch 30](./30-exploitability-triage.md) 表）：case 4 = UAF。
- 你若在非 ASan d8 上判成「良性/wild write」→ **差點判錯**，原因是沒用 ASan build，UAF 被那段 `#ifndef` 的 wild write 蓋掉了、看起來像單純 SIGSEGV。**教訓固化：triage UAF/OOB 一定要 ASan build。**

**再對 case 5（OOB read）走一遍**：ASan 應報 `heap-buffer-overflow READ`，方向「to the right of ... region」。對照答案（OOB read）校準。再對 case 1（`CHECK`）走一遍：乾淨 abort、明確錯誤訊息、無 ASan 記憶體報告 → 判**良性、不可利用**。三個 case 練完，你就有了「良性 abort / OOB / UAF」三種 ASan 長相的手感。

</details>

<details>
<summary>展開：對照範本 — 一個「真的 JS 觸發」的 OOB（來源 2 植入洞）</summary>

`FUZZILLI_CRASH` 是合成 crash（C++ 層），練讀報告很好，但不是真的 JS 觸發的引擎洞。這個範本用**來源 2**（植入洞）走一遍，貼近實戰。

### 設置
- 把 [Ch 26](./26-reading-v8-source-commits.md) 的 `src/builtins/array-lastindexof.tq` 裡 `if (k >= elementsLen) { k = elementsLen - 1; }` 這個 clamp **刪掉**，重編一顆 ASan d8（`is_asan=true`）。這就人工復活了 `Bug(898785)`（side-effect OOB read）。
- 觸發 JS：
  ```js
  let a = [1.1, 2.2, 3.3, 4.4, 5.5];
  let evil = { valueOf() { a.length = 1; return 4; } };
  a.lastIndexOf(9.9, evil);   // 求值 evil 時 a 被砍到 length 1，from 仍=4
  ```

### triage 六點（ASan 輸出為理論預期格式）
1. **來源觀察**：手寫 JS，在植洞 ASan d8 上跑。ASan 立刻 abort。
2. **是否安全問題**：ASan 報 `heap-buffer-overflow READ` → **是**（不是 V8 主動 abort，是真的讀了越界的 double backing store）。
3. **分類**：**OOB read**。報告的 `located 24 bytes to the right of a 8-byte region allocated by ...FixedDoubleArray...` 告訴你：越界方向=右、距離、被越界的是縮短後的 `FixedDoubleArray`。
4. **最小化**：這段已很小；確認刪掉 `evil` 的 `a.length=1` 就不 crash（證明 side-effect 是必要條件）、刪掉多餘陣列元素仍 crash（無關）。
5. **可利用性**：OOB read 且**距離部分可控**（`from` 由 getter 回傳值決定，`a.length` 由 getter 設定）→ 可讀縮短後 backing store 之後的記憶體 → **leak 相鄰物件的 map/指標**。價值中上（可控的 OOB read = leak primitive）。接 Part 3 的 leak → addrof。
6. **校準**：你事先知道這是「side-effect OOB read」（因為是你植入的）。判成 OOB read 且找到「side-effect 是必要條件」= 對。若你漏了 `valueOf` 是觸發點、只盯 `lastIndexOf` 的參數 → 沒抓到 root cause，回去看「哪個參數求值會呼叫 user code」。

**這個範本 vs `FUZZILLI_CRASH` 範本的差別**：這裡的 root cause 是**真的 V8 邏輯缺陷**（side-effect 沒被 clamp），最小化要證明「哪個操作是必要觸發條件」，可利用性是真的能往 leak 走。這才是真實 triage 的樣子；`FUZZILLI_CRASH` 只是讓你先練會讀 ASan 報告。

</details>

---

## 健康 session 長什麼樣（理論預期，做 Part A 時對照）

跑起來後，別只盯著「有沒有 crash」——先確認**跑得健康**（[Ch 29](./29-running-fuzzilli.md)）。一個健康的 V8 session（未實測，趨勢以官方/論文為準）大致：

- **前幾分鐘 Coverage 快速爬升**（到處都是沒走過的 edge），然後爬升變慢趨於平緩——這是**正常曲線**，不是卡住。
- **Exec/s 穩定在數百到數千**——代表 REPRL 生效（省掉了 process 反覆啟動）。若掉到個位數，REPRL 壞了或 timeout 淹沒。
- **Valid samples 八九成**——生成/lifting 正常。異常低代表 profile 或 build 對不上。
- **Corpus 持續成長**——探索在進展。停滯不長代表變異卡住。

**判斷「跑得對不對」的順序**：先看 Exec/s（REPRL）→ 再看 Valid（生成）→ 再看 Coverage 有沒有在動（探索）。這三個綠燈了，才是「健康地在找洞、只是還沒撞到」。三個裡有紅燈，是「跑錯了」，別傻等。啟動時的 `FUZZILLI_CRASH` 自檢通過，是這一切的前提——它證明 crash 偵測管線本身是通的。

## 延伸

- **把三個 case（1 良性 / 5 OOB / 4 UAF）都做一遍**，並排它們的 ASan 報告（或缺 ASan 報告）差異。你會建立起「一眼分辨三大類」的手感——這是真實 triage 最值錢的直覺。
- **玩 case 9（sandbox violation）**：在 sandbox build 上跑 `Fuzzilli("FUZZILLI_CRASH", 9)`，看 V8 sandbox 怎麼主動偵測並 abort。理解「越界被 sandbox 框住 = 還沒真的打穿」對判斷可利用性至關重要（[Ch 34](./34-v8-sandbox.md)）——很多現代 V8 crash 是 sandbox violation，意味你需要的是 sandbox 逃逸而非普通 OOB。
- **對比非 ASan vs ASan 對同一個 UAF（case 4）**：先在非 ASan fuzzing d8 跑（可能靠那段 `#ifndef` 的 wild write 才 crash、看起來像單純 SIGSEGV），再在 ASan d8 跑（明確 `heap-use-after-free`）。把兩份輸出並排——這是「為什麼 triage 一定要 ASan」最有說服力的親身證據。
- **做來源 2（植入真洞）**：把 `array-lastindexof.tq` 的 clamp 刪掉、重編、手寫觸發 JS，在 ASan d8 上看真正的 `heap-buffer-overflow`。這比合成 crash 真實一個檔次，且把 [Ch 26](./26-reading-v8-source-commits.md) 的 Torque 讀碼、[Ch 30](./30-exploitability-triage.md) 的 triage、[Ch 21](./21-array-prototype-side-effect.md) 的 side-effect 觸發全串起來。
- **接 Part 3/4**：挑你 triage 出的（或植入的）一個 OOB/UAF，真的往「造 addrof/fakeobj → 任意讀寫」推一步。這是從「找到洞」到「打穿」的跨越。
- **建一份 triage checklist**：把你這次的判斷步驟固化成一張「拿到 crash 先問什麼」的清單（乾淨 abort? → ASan 類型? → read/write? → 方向距離? → 打到什麼? → 可控嗎?），下次面對真 fuzzing 一堆 crash 時照跑，一小時篩掉垃圾。

## 本練習重點回顧

- 用**已知型別**的 crash（`FUZZILLI_CRASH` / 自植洞）在受控條件練整條 triage，能拿正確答案校準判斷。
- Part A 三齒輪：Fuzzilli(Swift) + fuzzing d8(`v8_fuzzilli`+coverage) + REPRL；另備 ASan d8 給 triage。啟動自檢通過 = 咬合成功。
- Part B triage 六點：來源觀察 → 是否安全問題 → 分類 → 最小化 → 可利用性 → 校準。
- 核心教訓：**UAF/OOB 非 ASan 可能不現形**（case 4 原始碼實證），triage 必用 ASan build；**價值 = 可控性**；最小化鐵律是「同一個 crash 點」。
- 良性（`CHECK`/OOM/stack overflow，V8 主動乾淨 abort）vs 漏洞（碰到不該碰的記憶體）——第一刀就分這個。

Part 5 的兩個練習到此完成：練習 D 走被動路徑（patch-diff 逆推），練習 E 走主動路徑（fuzz + triage）。你現在有了「發現漏洞」的完整方法論——接下來把找到的洞，用 Part 3/4 的技法打穿。
