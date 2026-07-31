# 練習 D — Patch-diff 一個真實 V8 security commit，反推 root cause 與 PoC

> **目標**：把 [Ch 26](./26-reading-v8-source-commits.md)、[Ch 27](./27-patch-diffing.md)、[Ch 31](./31-oss-fuzz-regression.md) 串成一次完整的 1-day 前半段實戰——挑一個真實 V8 安全修補，讀 diff、定位 root cause、反推「壞的樣子」、寫出一個能觸發它的 PoC**思路**，並想清楚它怎麼往記憶體破壞升級（接 Part 3/4）。**這是防禦研究也需要的技能**：要防一類 bug，你得先能從修補反推出它怎麼被觸發。這個練習不要求你寫出完整 exploit（那是 Part 4 的活），要求的是**把「一個 fix」逆推成「一個攻擊劇本」的推理鏈**練到扎實。

> **環境**：V8 15.3.0，commit `ab2cad06`，`~/v8build/v8/`，d8 在 `out/x64.release/`。**限制（先講）**：本地 `--no-history` checkout 只有 1 個 commit，無法在本地 `git diff` 舊 fix。所以這個練習的**主素材是 regression test**（`test/mjsunit/regress/`，[Ch 31](./31-oss-fuzz-regression.md) 證實有 3109 個）——它把 root cause 與觸發常寫在註解裡，是「patch 已被翻譯好」的 diff。真正的線上 diff 用 gitiles 補。參考解答用**真實存在**的 `regress-491881374.js`（Turboshaft LoopUnrollingReducer miscompile）走一遍。

## 這個練習在幹嘛（先讀）

1-day 研究的前半段就是：**拿到「修好的樣子」，反推出「壞掉的樣子」+ 怎麼觸發 + 怎麼利用**。你在 [Ch 27](./27-patch-diffing.md) 學了流程，這裡自己走一遍完整的，並產出書面的分析。做完你會有一份「從 fix 到 PoC 思路」的分析報告——這正是賞金獵人 / 紅隊 / 防禦研究者的日常產出格式。

**為什麼這個練習值得認真做**：漏洞研究者的產能，很大一部分取決於「讀一個 fix 能榨出多少」。同一個 commit，新手只看到「喔它加了個 if」，老手看到「這個 if 在防 side-effect 縮短陣列造成的 OOB，而且它只補了 lastIndexOf、indexOf 的對稱路徑我得去查」。這個差距不是天分，是**練出來的逆推肌肉**。這個練習就是重量訓練——每逆推一個 fix，你腦中的「V8 會出什麼洞」模式庫就厚一分，那正是最終能找 0-day 的本錢。

而且這技能**防守方一樣要**：你要評估一個剛揭露的 V8 bug 對你的產品（用了 V8/Node/Electron）有多危險、要不要緊急升級，就得能從 fix 判斷「這洞好不好觸發、能不能到 RCE」。攻防在這一步用的是同一套推理。

## 規格：你要交付什麼

挑**一個** V8 安全相關的修補（下面「選題」給你三種難度的來源），產出一份**書面分析**，涵蓋六個部分：

### Part 1：選題與定位（必做）
- 你挑的是哪個 bug（bug id / commit / regression test 檔名）？從哪找到的（regression test grep？OSS-Fuzz tracker？gitiles）？
- 用 [Ch 26](./26-reading-v8-source-commits.md) 的目錄地圖：它屬於哪個子系統、root cause 在哪個檔哪個函式？

### Part 2：root cause（必做）
- 修之前，**哪個不變式（invariant）被打破**？（優化器押錯賭注？邊界檢查缺失？side-effect 沒被考慮？型別假設錯？公式算錯？）
- 用你自己的話寫出「錯誤的推理」：優化器/內建**以為**什麼，**實際**是什麼。

### Part 3：fix 的形狀（必做）
- fix 加了/改了什麼保護？（多一個 clamp？多一個 deopt/bailout？改對一個公式？補一個 side-effect 標記？）
- 對每個關鍵 `+`，回答「它在防什麼、拿掉會怎樣」。

### Part 4：PoC 思路（必做）
- 寫出一段 JS（或 pseudo-JS）**觸發器**：怎麼把 engine 帶進那個壞狀態。
- 標明觸發需要的「儀式」：要不要 `%PrepareFunctionForOptimization`/`%OptimizeFunctionOnNextCall` 逼優化？要不要熱身迴圈？要不要特定 flag（看 regression test 的 `// Flags:`）？
- 說明：在**修補前**的 d8 上，這段會發生什麼（crash？回錯值？OOB？）；在**修補後**（當前 d8）上會發生什麼（乖乖通過）。

### Part 5：可利用性升級方向（必做）
- 這個 bug 的原始 primitive 是什麼（miscompile 回錯值？OOB read？UAF？type confusion？）。
- 怎麼往記憶體破壞推一步？（把算錯的值餵給陣列存取讓 bounds check 被消 → OOB？UAF 重佔 → type confusion？）接到 Part 3/4 的哪個技法（addrof/fakeobj、OOB 滾任意讀寫）？
- **不用寫完整 exploit**，但要講清楚「下一步往哪走、為什麼那條路可行」。

### Part 6：variant 提問（必做，這是精華）
- 這個 fix **補乾淨了嗎**？同一個模式在別的 code path / 別的 elements kind / 別的 reducer 有沒有可能還在？
- 列 1-2 個你會去查的「可疑鄰居」（哪個檔、哪個函式），說明為什麼懷疑它。

## 背景：1-day 的時間軸與經濟學（做之前先懂賽局）

你在逆推的每個 fix，背後有一條真實的時間軸，決定「這個逆推有沒有實戰價值」：

```
   T0  bug 被找到（fuzzer/研究者）→ 私下報給 Google
   T1  V8 team 修補、commit 進 main（帶 regression test）  ← diff 從此半公開
   T2  修補隨 Chrome stable 推送給用戶（分批、數天到數週滾動）
   T3  T1 + 90 天：bug 細節在 tracker 揭露（reproducer 公開）
   ─────────────────────────────────────────────────────
   1-day 窗口 = T1/T2 到「所有用戶升級完」之間
```

**關鍵洞見**：
- **diff 在 T1 就半公開了**（進了 open-source repo，regression test 更是明碼），但**大量用戶要到 T2 之後好一陣子才升級**。這中間，一個從 T1 diff 逆推出的 exploit，打的是「還沒升級的那一大票」——這就是 1-day 的價值窗口。
- **T3 揭露不是起跑槍、T1 才是**。盯著 `test/mjsunit/regress/` 新增檔 + main 的 security commit 的人，在 T1 就開始逆推了，不等 T3。這也是為什麼「監控 V8 test commit」是某些團隊的日常。
- **升級速度 = 窗口大小**。Chrome 自動更新快，窗口相對短；但 Electron/Node/嵌入 V8 的產品升級慢，窗口可能長達數月——那是 1-day 最肥的目標。

理解這條時間軸，你就懂為什麼這個練習不是學術遊戲：**你逆推 diff 的速度，直接對應真實攻防裡「搶窗口」的能力**。

## 逆推常見陷阱（先知道才不會踩）

- **把 refactor 當成 bug**：一個 security commit 常夾帶無害的重構（改個變數名、搬個函式）。別把「純搬移」的 diff 當成「堵洞的關鍵」。專注在改變**控制流 / 邊界條件 / 型別假設 / 數值公式**的行。
- **root cause 抓在「症狀」而非「病因」**：fix 可能在 A 處加 clamp，但真正的病因是 B 處沒標 side-effect。問「為什麼需要這個 clamp」直到問不下去，才是 root cause。
- **忽略 `// Flags:` 的觸發前提**：有些 bug 只在特定 flag（`--turbo-xxx`、`--maglev`）或特定 tier 觸發。regression test 的 flag 行是「觸發需要什麼環境」的說明書，漏看就寫不出能觸發的 PoC。
- **停在「回錯值/crash」不推升級**：這是最常見的半途而廢。miscompile 回錯值、OOB 讀到 undefined，都只是**起點**。Part 5 要你推到「這錯值/越界怎麼變成可控記憶體破壞」。
- **variant 提問太空泛**：「別處可能也有」等於沒說。要具體到「`indexOf` 的 line X 沒有對應 clamp」這種可查證的指控。

## 選題：三種難度

**難度 1（推薦入門，本地可做）**：直接用本 checkout 的 regression test。
- `regress-491881374.js`（參考解答用的，Turboshaft miscompile）——或自己 grep 另一個：
  ```
  grep -rl 'turbofan\|turboshaft\|typer\|type confusion' ~/v8build/v8/test/mjsunit/regress/*.js
  ```
- 優點：root cause 常寫在註解裡，本地可跑驗證（記得載 `mjsunit.js`）。

**難度 2（要上網）**：從 OSS-Fuzz / bug tracker 挑一個**已揭露**、帶 reproducer 的 V8 JS 安全 bug（[Ch 31](./31-oss-fuzz-regression.md)）。
- 用 gitiles 看它的 fix diff，對照 reproducer。
- 優點：更接近真實 1-day；你能看到真正的 C++ diff 而不只是 regression test。

**難度 3（進階）**：挑一個有公開 writeup 的歷史 V8 CVE（Part 4 提過的類型：Array.prototype 的 side-effect、TurboFan typer OOB、Maglev 型別混淆），**先不看 writeup**，自己從 fix + regression test 反推，最後拿 writeup 對答案。
- 優點：最真實地檢驗你的推理力。

## 交付形式

一份 markdown 分析（六個 Part 都寫到），加上你實際跑過的指令記錄（哪些 test 在當前 d8 上通過、你 grep 到什麼）。**推理鏈完整 > 篇幅長**。

## 評分自檢（做完對照）

給自己打分，每項 0/1/2（0=沒做到、1=部分、2=到位）：

| 項目 | 到位長什麼樣 |
|---|---|
| root cause 精準 | 說得出**具體哪個不變式被打破**，不是含糊的「有個 bug」 |
| 「以為 vs 實際」對比 | 能一句話寫出優化器/內建的錯誤推理與真相的落差 |
| fix 形狀反推 | 對關鍵 `+` 都答得出「防什麼、拿掉會怎樣」 |
| PoC 觸發儀式完整 | flag、`%Prepare`/`%Optimize`、熱身迴圈都標到，別人照著能重現 |
| 升級路徑具體 | 不是「可以做任意讀寫」而是「這個錯值餵給 X → 消掉 Y 檢查 → OOB 到 Z」 |
| variant 提問有料 | 至少一個**具體**的可疑鄰居（檔+函式），不是泛泛「別處可能也有」 |

10 分以上算過關。低於 6 分通常是卡在 root cause 沒抓準——回去重讀註解 + 對照原始碼那個函式。

---

## 讀一段真 diff：line-by-line 的動作示範

難度 2/3 會遇到真正的 C++ diff（不像 regression test 有註解餵你）。這裡示範「一行一行問問題」的動作。假設你在 gitiles 看到某個 Array 內建 fix 的 diff 長這樣（合成示意，練動作用）：

```diff
   BUILTIN(ArraySomething) {
     Handle<JSArray> array = ...;
     Handle<Object> from_index = args.at(1);
-    intptr_t k = Object::ToIntptr(from_index);
+    intptr_t len_before = array->elements()->length();
+    intptr_t k = Object::ToIntptr(from_index);   // 可能觸發 user getter
+    if (k >= array->elements()->length()) {
+      k = array->elements()->length() - 1;
+    }
     for (; k >= 0; k--) {
       Object elem = array->elements()->get(k);   // ← 用 k 當索引讀
       ...
```

逐行審問（這是你每次讀 diff 該做的內心獨白）：

- `+ intptr_t len_before = ...`：**為什麼要在求值 `from_index` 前先存長度？** 嗅到「長度會變」。誰能改長度？→ `ToIntptr(from_index)` 若 `from_index` 是個物件，會呼叫它的 `valueOf`/`Symbol.toPrimitive`——**user callback！** 這就是 side-effect 入口（[Ch 21](./21-array-prototype-side-effect.md)）。
- `+ if (k >= ...length()) k = ...length()-1;`：**這個 clamp 在防什麼？** 防 `k` 超過「求值後」的實際長度。反推：**修補前沒這個 clamp，若 user getter 在求值 `from_index` 時把陣列縮短，`k` 還是舊的大值 → `array->elements()->get(k)` OOB read。**
- `for (; k >= 0; k--) { ...get(k) }`：確認 `k` 真的被拿去當索引存取。→ OOB 的落點確定在這個 `get(k)`。

**三行 diff，逆推出完整攻擊劇本**：「傳一個帶惡意 `valueOf` 的 `from_index`，getter 裡把陣列 `length` 改小、shrink backing store，回傳一個大數當 index → 舊 `k` 越界 → `get(k)` 讀到 backing store 之後的記憶體 → OOB read → leak」。這就是你在 Part 4 的 PoC 要做的事。**注意這和 [Ch 26](./26-reading-v8-source-commits.md) 那個真實 `array-lastindexof.tq` 的 `Bug(898785)` clamp 是同一個模式**——那不是巧合，side-effect-during-index-evaluation 是 Array 內建的經典洞型，補了一個還有對稱的其他內建。

---

## 卡點與提示

- **卡在「找不到 root cause」**：先讀**檔名 bugid + 註解 + `// Flags:`**（[Ch 31](./31-oss-fuzz-regression.md) 的五問句），別一頭栽進 code body。V8 regression test 的註解常常直接告訴你答案。
- **卡在「跑 regression test 報 assertEquals is not defined」**：要載 mjsunit 框架：`d8 --allow-natives-syntax test/mjsunit/mjsunit.js test/mjsunit/regress/xxx.js`。
- **卡在「當前 d8 跑起來沒 crash」**：對，因為當前是**已修**版本。regression test 在已修 d8 上是「靜悄悄通過（exit 0）」。要看 bug 得回到修補前的 commit（本 batch 無歷史，所以這裡用「讀 + 反推」重建因果，不強求真觸發）。
- **卡在「Part 5 升級不知道怎麼接」**：核心模板是「**錯誤的範圍/型別推斷 → 優化器消掉的檢查 → 用範圍外的值打 OOB**」。把 bug 算錯的那個值，想像餵給一個陣列索引，優化器基於錯誤範圍消掉 bounds check，實際值超界 → OOB。這是 [Ch 22](./22-typer-range-analysis-bug.md)、Part 3 的套路。
- **卡在「不會看線上 diff」**：gitiles `chromium.googlesource.com/v8/v8/+/<commit>`；只看那顆用 `.../+/<hash>^!/`。加 `?format=TEXT` 拿純文字。

---

## 參考分析（做完再看）

<details>
<summary>展開：以 <code>regress-491881374.js</code>（Turboshaft LoopUnrollingReducer miscompile）走完整六個 Part</summary>

這是 [Ch 27](./27-patch-diffing.md) 的個案，這裡把它填進六個 Part 的交付格式，當你的範本。

### Part 1：選題與定位
- **bug**：`regress-491881374.js`，crbug 491881374。從本 checkout `test/mjsunit/regress/` grep `turboshaft` 找到。
- **子系統**：Turboshaft 優化器的迴圈展開。root cause 在 `src/compiler/turboshaft/loop-unrolling-reducer.cc` 的 `GetIterCountIfStaticCanonicalForLoop` / `CountIterations`（用 [Ch 26](./26-reading-v8-source-commits.md) 目錄地圖：`turboshaft` + `loop-unrolling` → 優化器對迴圈的靜態推理）。

### Part 2：root cause
- **被打破的不變式**：`CountIterations` 假設迴圈更新式的 phi（迴圈變數）永遠在減法**左邊**（`i = i - c`）。但這個迴圈是 `i = (3 - i)`——phi 在**右邊**（`c - i`）。
- **錯誤推理**：優化器**以為**每輪 `i ← i - 3`，於是模擬 `i: 2 → 2-3 = -1`，`-1 >= 0` 為假 → 判定「迴圈只跑 1 次」。**實際**每輪 `i ← 3 - i`，`i` 在 `2 ↔ 1` 震盪、永遠 `>= 0`，靠 `count >= 5` break，實跑 5 次。
- **後果**：優化器基於「只跑 1 次」的錯誤次數做 **full unrolling**（攤成直線碼），生成的碼回傳 2；直譯器回傳 5。**同一段 JS，兩種執行給不同答案 = miscompilation**。

### Part 3：fix 的形狀（反推）
- 沒有本地 diff，但反推 fix 必然在 `CountIterations`/`GetIterCountIfStaticCanonicalForLoop` 裡**區分 phi 在減法的左邊還右邊**：
  ```
  -  next_i = phi_input - constant;                 // 錯：假設 phi 在左
  +  if (phi_on_right) next_i = constant - phi_input; // 對：右邊 = c - i
  +  else              next_i = phi_input - constant;
  //  或：對 right-phi 直接 bail out，不做靜態展開
  ```
- 這個 `+` 在防的是「right-phi 被套用 left-phi 的公式」。拿掉它 → 次數算錯 → 錯誤展開。

### Part 4：PoC 思路
- 觸發器就是 `test_right_phi`（更新式 phi 在右的遞減迴圈）。
- **儀式**：`// Flags: --allow-natives-syntax`；要 `%PrepareFunctionForOptimization` + 熱身 + `%OptimizeFunctionOnNextCall` 逼它進 Turboshaft 的迴圈展開路徑（regression test 靠斷言比對 `interp` vs `opt` 兩種執行）。
- **修補前 d8**：優化版回傳 2、直譯版回傳 5，`assertEquals(interp, opt)` 失敗（miscompile 現形）。
- **修補後（當前 d8）**：兩者都回 5，斷言通過，exit 0（[Ch 27](./27-patch-diffing.md) 真跑驗過）。

### Part 5：可利用性升級方向
- 原始 primitive：**miscompile**（優化器對迴圈變數的**範圍推斷錯**）。單看回錯值不是記憶體破壞。
- **升級**：把那個被誤判範圍的迴圈變數（優化器以為 `∈[某小範圍]`）拿去當**陣列索引**。優化器基於錯誤範圍「證明」索引不會超界 → **消掉 bounds check**（[Ch 22](./22-typer-range-analysis-bug.md)）。但實際迴圈跑更多次、索引更大 → **OOB read/write**。
- **接 Part 3/4**：OOB → 打到相鄰物件的 length/elements → 造更大 OOB → addrof/fakeobj → 任意讀寫 → RCE。這條「錯誤範圍推斷 → bounds check 被消 → OOB → 任意讀寫」是 V8 pwn 最經典的骨架。

### Part 6：variant 提問（精華）
- **fix 補乾淨了嗎**：它（推測）只處理了 Sub 的 right-phi。那——
  - right-phi 的**加法**（`i = c + i` 形式的變體）呢？`CountIterations` 對加法有沒有同款左右假設？
  - **其他 reducer**（不只 loop-unrolling）有沒有「假設 phi 在運算子固定一邊」的靜態分析？例如 induction variable analysis、range analysis。
  - **可疑鄰居**：grep `src/compiler/turboshaft/` 裡處理 phi + 算術的靜態分析函式，看有沒有沒分左右邊的。這串就是 variant hunting 的起點（呼應 [Ch 31](./31-oss-fuzz-regression.md) 的閉環）。

**這份分析的價值不在「重現了 bug」，而在整條推理鏈**：從一個 regression test，反推出不變式、fix 形狀、觸發儀式、升級路徑、以及「這 fix 夠不夠」的 variant 提問。這就是 1-day 前半段的完整產出。

</details>

<details>
<summary>展開：第二個範本 — Array 內建的 side-effect OOB（對照「optimizer bug」的另一種型）</summary>

第一個範本是**優化器 miscompile**（範圍算錯）。這第二個是**內建函式的 side-effect OOB**（[Ch 21](./21-array-prototype-side-effect.md) 那類），讓你看到不同 bug 型的六 Part 長什麼樣。用 [Ch 26](./26-reading-v8-source-commits.md) 真實的 `array-lastindexof.tq` `Bug(898785)` clamp 當素材。

### Part 1：選題與定位
- **bug**：`Bug(898785)`，`Array.prototype.lastIndexOf` 的 side-effect OOB。從 `grep -rn 'Bug(' ~/v8build/v8/src/builtins/*.tq` 找到（[Ch 26](./26-reading-v8-source-commits.md) 引過）。
- **子系統**：內建函式（`src/builtins/array-lastindexof.tq`，Torque）。不是優化器——是 builtin 本身的邏輯。

### Part 2：root cause
- **被打破的不變式**：「求值 `fromIndex` **不會改變陣列長度**」。但 `fromIndex` 若是物件，`ToInteger` 會呼叫它的 `valueOf`——**user callback 能在裡面 shrink 陣列**。
- **錯誤推理**：builtin **以為** `from`（來自 `fromIndex`）落在 elements 範圍內；**實際**上 getter 已把 elements 縮短，`from` 現在越界了。

### Part 3：fix 的形狀
- 就是那個真實的 clamp：
  ```
  + if (k >= elementsLen) {
  +   k = elementsLen - 1;
  + }
  ```
- 它在防「`from` 越過**求值後**的實際長度」。拿掉 → 用舊的大 `from` 去 `elements[from]` → OOB read（讀 holes / 越界記憶體）。

### Part 4：PoC 思路
- 觸發器（pseudo-JS）：
  ```js
  let a = [1.1, 2.2, 3.3, 4.4, 5.5];
  let evil = { valueOf() { a.length = 1; return 4; } };  // getter 縮短陣列、回大 index
  a.lastIndexOf(9.9, evil);   // fromIndex = evil，求值時 a 被砍短，from 仍=4 → 越界讀
  ```
- **儀式**：不需要逼優化（這是 builtin 慢路徑的 bug，不靠 JIT）。**不用** `%Optimize`——這點和第一個範本（優化器 bug 要逼 JIT）正好相反，是重要對比。
- **修補前**：`elements[4]` 讀到縮短後 backing store 之外 → OOB read。**修補後**：clamp 把 `k` 拉回 `elementsLen-1`，讀到的是 hole、被忽略，安全。

### Part 5：可利用性升級方向
- 原始 primitive：**OOB read**（讀越界的 double/指標）。
- 升級：把讀到的越界值 leak 出來 → 洩漏相鄰物件的 map 指標 / backing store 位址 → 配合其他 primitive 造 addrof。若能把 side-effect 玩成 OOB **write**（別的內建可能可以），更直接。接 Part 3 的 leak → addrof。

### Part 6：variant 提問
- fix 只補了 `lastIndexOf`。**對稱的 `indexOf`、`includes`、`fill`、`copyWithin`、`lastIndexOf` 的 TypedArray 版**呢？它們也求值 `fromIndex`/其他參數，也可能有 user getter side-effect。
- **可疑鄰居**：`grep -rn 'fromIndex\|ToInteger\|valueOf' ~/v8build/v8/src/builtins/array-*.tq`，看哪個內建在求值參數後、用該值當索引前，**沒有**類似 clamp。這一串就是 variant 候選——side-effect OOB 是 Array 內建反覆出現的洞型，補一個常漏對稱的。

**兩個範本對照的重點**：優化器 bug（範本一）靠 JIT 觸發、root cause 在 `src/compiler/`、升級靠「範圍推斷錯 → bounds check 消除」；內建 side-effect bug（範本二）不靠 JIT、root cause 在 `src/builtins/*.tq`、升級靠「callback 改狀態 → 舊值越界」。**同樣是六 Part 格式，不同 bug 型的每一格填法不同**——這正是你要練的分辨力。

</details>

---

## 延伸

- **做兩個對照**：一個難度 1（本地 regression test）、一個難度 2（OSS-Fuzz 真 diff）。對比「註解餵給你的 root cause」和「你自己從 C++ diff 挖出的 root cause」，感受兩種難度差別。
- **把 Part 6 認真做**：挑你 variant 提問裡最可疑的一個鄰居，真的去讀當前原始碼確認「補了沒」。就算沒找到新洞，這個動作本身是 1-day → 0-day 的核心訓練。
- **接練習 E**：練習 D 是「別人找到、你逆推」；[練習 E](./practice-e-fuzzilli-crash-triage.md) 是「你自己 fuzz、自己 triage」。兩個練習合起來，就是漏洞研究「發現」的兩條主路——被動（patch-diff）與主動（fuzz）。
- **寫成 writeup**：把你的六 Part 分析整理成一篇對外的 writeup 格式（假設讀者沒看過這個 bug）。能把逆推講清楚給別人聽，代表你真的懂了。

## 本練習重點回顧

- 1-day 前半段 = 從 fix 反推「壞樣子 + 觸發 + 升級 + variant」，防禦研究同樣需要這技能。
- 主素材是 **regression test**（root cause 常寫在註解，本地可跑驗證「已修」），線上 diff 用 gitiles 補。
- 交付六 Part：定位 → root cause → fix 形狀 → PoC 思路（含觸發儀式）→ 升級方向 → **variant 提問**。
- 升級模板：**錯誤範圍/型別推斷 → 優化器消掉的檢查 → 範圍外的值打 OOB → 任意讀寫**。
- 精華是 Part 6：問「這 fix 補乾淨了嗎」，把 1-day 逆推變成 variant hunting 的起點。

→ [練習 E — 設定 Fuzzilli 跑一個 session，對一個 crash 做 triage](./practice-e-fuzzilli-crash-triage.md)
