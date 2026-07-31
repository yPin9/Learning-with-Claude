# Ch 27 — Patch diffing：從一個 security fix 反推 root cause 與 PoC

> **目標**：學會 1-day 漏洞研究的核心手藝——拿到一個**安全修補**（一段 diff），反推出「修之前哪裡錯了、怎麼觸發、能造成什麼記憶體破壞」，並把這條推理落地成一個能在舊版本上觸發的 PoC 思路。你會用一個**真實的 V8 Turboshaft 優化器 miscompilation**（本 checkout 附帶的 regression test）從頭走一遍：讀 diff → 定位 root cause → 造最小觸發 → 判斷可利用性方向。這章不是叫你寫完整 exploit（那是 Part 3/4 的活），而是把「一個 fix ↔ 一個洞」之間的推理鏈練熟。

> **環境**：V8 15.3.0，commit `ab2cad06`，`~/v8build/v8/`，d8 在 `out/x64.release/`。本章的 regression test（`test/mjsunit/regress/regress-491881374.js`）、d8 執行、Torque/`.tq` 觀察都是真跑。**限制**：本地 `--no-history` checkout 只有 1 個 commit，所以無法在本地對「修補前 vs 修補後」跑 `git diff`——這正是 patch diffing 的真實痛點，本章教你怎麼用 gitiles 的線上 diff 和「regression test 本身就藏著 root cause」兩條路繞過。

## 為什麼需要這個？

漏洞研究有兩種基本盤。0-day 是憑空找新洞，難、慢、玄。**1-day 是「洞已經被修了、細節被揭露了，你搶在所有人升級完之前，做出打舊版本的 exploit」**——這是真實世界（APT、紅隊、賞金獵人）的主力打法，因為：

- **投報率高**：root cause 別人幫你找到了（就在那個 fix 裡），你只要逆推、寫觸發、接利用。省掉最難的「發現」階段。
- **時間窗真實存在**：Chrome 修補上線到全球用戶升級完，中間有數天到數週。這段期間，一個 1-day exploit 打的是「還沒更新的那一大票人」。
- **它是學習 0-day 的最佳跳板**：你逆推過幾十個真實 fix，才會長出「什麼樣的程式碼模式會出洞」的嗅覺，那才是找 0-day 的本錢。

而 patch diffing 就是 1-day 的第一步、也是最吃基本功的一步：**把一段「修好的樣子」的 diff，讀成一個「壞掉的樣子」的攻擊劇本**。

## 先建立直覺：diff 是一張「兇案現場修復照」

想像你只看得到「牆補好之後」的照片，要反推「當初牆上的洞長怎樣、子彈從哪來」。patch diffing 就是這件事：

```
   你拿到的（fix 之後）              你要反推的（fix 之前）
   ────────────────                ────────────────────
   多了一個邊界檢查      ◄────────   之前沒檢查 → 越界
   多了一次 clamp        ◄────────   之前值可能超範圍 → OOB
   多了一個 deopt/bailout ◄────────   之前優化器押錯賭注 → type confusion
   改對了一個符號/公式    ◄────────   之前算錯 → 邏輯錯 → 記憶體錯
   加了 side-effect 標記  ◄────────   之前優化器以為沒 side-effect → 被 callback 偷改
```

**fix 加了什麼保護，反推就是「之前缺這個保護 → 攻擊者鑽這個縫」。** 讀 diff 的核心動作只有一個：對每一行 `+`（新增），問「這行在防什麼？如果沒有它，我怎麼讓壞事發生？」

## 一個真實案例：Turboshaft LoopUnrollingReducer 算錯迴圈次數

本 checkout 裡有一個 regression test，是**優化器 miscompilation** 的活教材。它有意思在：不是經典的 OOB 檢查缺失，而是**優化器把公式算錯**，導致優化後的機器碼跑出和直譯器不同的結果——這種「JIT 結果 ≠ 正解」正是 type confusion 的近親，很多能被推成記憶體破壞。

先看它（作者實跑，檔案真實存在）：

```
$ sed -n '1,24p' ~/v8build/v8/test/mjsunit/regress/regress-491881374.js
// Flags: --allow-natives-syntax

// Turboshaft LoopUnrollingReducer miscompilation PoC
// Bug: GetIterCountIfStaticCanonicalForLoop in loop-unrolling-reducer.cc
// handles the right-phi case (phi on RIGHT side of Sub: `i = c - i`)
// incorrectly. CountIterations simulates `i - c` instead of `c - i`,
// causing wrong loop iteration count and incorrect full unrolling.

function test_right_phi() {
  let count = 0;
  for (let i = 2; i >= 0; i = (3 - i) | 0) {
    // Real: i oscillates 2 -> 1 -> 2 -> 1 -> ... (always >= 0, runs 5 times)
    // Bug: analysis computes i: 2 -> 2-3=-1 (exits after 1 iter, unrolls wrong)
    count = (count + 1) | 0;
    if (count >= 5) break;
  }
  return count;  // Should be 5, JIT returns 2
}
```

這段 regression test **把 root cause 直接寫在註解裡**——這是 V8 regression test 的常態，也是 patch differ 的福利。逐點拆解，這就是完整的「兇案重建」：

### 步驟一：root cause 在哪個檔、哪個函式

註解點名了：`GetIterCountIfStaticCanonicalForLoop` in `loop-unrolling-reducer.cc`（在 `src/compiler/turboshaft/`）。這是 **Turboshaft 的迴圈展開 reducer**——它會分析一個「規範計數迴圈」，靜態算出它會跑幾次（iteration count），然後決定要不要**完全展開**（full unrolling：把迴圈攤成沒有迴圈的直線碼，快，但前提是次數算對）。

用本章 [Ch 26](./26-reading-v8-source-commits.md) 的目錄地圖：`turboshaft` + `loop-unrolling` → 這是**優化器對迴圈的靜態推理**。優化器的靜態推理算錯，就是我們要的那種洞。

### 步驟二：錯在哪（root cause 的本體）

註解講得很清楚：這個迴圈的更新式是 `i = (3 - i)`——**phi（迴圈變數）在減法的右邊**（`常數 - i`）。而 `CountIterations`（算次數的函式）**誤把它當成 `i - 常數`（phi 在左邊）來模擬**。

- **真實語意**：`i` 從 2 出發，每輪 `i = 3 - i`，所以 `i` 在 `2 → 1 → 2 → 1 → …` 之間**震盪**，永遠 `>= 0`，迴圈條件 `i >= 0` 永遠成立 → 靠 `count >= 5` 的 break 跳出 → 實際跑 **5 次**。
- **優化器算的**：它以為更新是 `i - 3`，於是模擬 `i: 2 → 2-3 = -1`，`-1 >= 0` 為假 → 判定「這迴圈只跑 1 次就結束」→ 依這個錯誤次數做 full unrolling，生成的直線碼只跑 1（實測回 2）次。

**結論：優化後的機器碼回傳 2，直譯器回傳 5。同一段 JS，兩種執行方式給出不同答案。** 這就是 miscompilation。

### 步驟三：fix 長什麼樣（反推）

我們雖然不能在本地 `git diff`（no-history），但從 root cause 能**反推 fix 必然是什麼形狀**：`CountIterations` / `GetIterCountIfStaticCanonicalForLoop` 裡，一定有一段判斷「phi 在減法哪一邊」的邏輯。修補就是**把 right-phi 的情形（`c - i`）正確地模擬成「每輪 `i ← c - i`」，而不是套用 left-phi（`i - c`）的公式**。換句話說，diff 大概是：

```
   在算下一輪 i 的地方：
-    next_i = phi_input - constant;        // 錯：假設 phi 永遠在左邊
+    if (phi_on_right) next_i = constant - phi_input;   // 對：分左右邊
+    else              next_i = phi_input - constant;
   // 或：對 right-phi 直接放棄靜態展開（bail out）
```

**這就是 patch diffing 的反推**：你沒看到真正的 diff，但從「壞在哪」推出「fix 必然在防哪個 case」。真的拿到 diff 時，你是去**驗證**這個推理，不是從零讀起。

### 步驟四：從 miscompilation 到記憶體破壞（可利用性方向）

「JIT 算出的迴圈次數 ≠ 正解」本身還不是記憶體破壞，但它是**通往記憶體破壞的門票**。經典升級路徑（Part 4 的技法）：

- 如果這個「被優化器誤判的迴圈變數」被拿去當**陣列索引**或**長度計算**，優化器以為它的範圍是 `[0, 1)`（只跑 1 次那套推理），於是**省掉了邊界檢查**（bounds check elimination，因為它「證明」了索引不會超）。但實際迴圈跑更多次、索引更大 → **OOB read/write**。
- 更廣義：優化器對某個值的「範圍/型別推斷」錯了，它據此消除的檢查就成了缺口。這正是 [Ch 19](./19-turbofan-type-confusion.md)（type confusion）、[Ch 22](./22-typer-range-analysis-bug.md)（bounds check 消除）的核心模式。**miscompilation → 錯誤的範圍推斷 → 檢查被消 → OOB**。

所以你的 PoC 思路是：**不要只證明「回傳值錯了」，要把那個被算錯的值餵給一個陣列存取**，讓優化器基於錯誤範圍消掉 bounds check，然後用實際更大的索引打出界。regression test 只驗到「值錯」那一步（因為 V8 團隊的目的是防迴歸，不是寫 exploit），**你要接著往「值錯 → 記憶體錯」推一步**——這一步就是 1-day exploit 開發的實質。

## 底層機制：patch diffing 的標準流程

把上面的個案抽象成一套你每次都能套的流程：

```
   1. 拿到 fix        ── commit / Gerrit / gitiles diff / regression test
        │
   2. 定位 root cause ── 哪個檔、哪個函式、多了/改了什麼保護
        │              （讀 commit message、`Bug:`、regression test 註解）
        ▼
   3. 反推「壞的樣子」── 沒有這個保護時，什麼輸入會踩到？
        │              （對每個 `+` 問「這在防什麼」）
        ▼
   4. 造最小觸發       ── 寫一段 JS，在【舊版本】上觸發那個壞狀態
        │              （優化器 bug 要 %PrepareForOptimization + %OptimizeOnNextCall）
        ▼
   5. 判斷可利用性     ── OOB read? write? type confusion? 有多可控？
        │              （把算錯的值餵給陣列存取 → Part 4 技法）
        ▼
   6. 接 Part 3/4      ── OOB → addrof/fakeobj → 任意讀寫 → RCE
```

### 實務工具：怎麼真的看 diff

因為本地無歷史，這幾條路你會輪流用：

- **gitiles 線上 diff**：`chromium.googlesource.com/v8/v8/+/<fix-commit>` 直接看該 commit 的完整 diff。加 `%5E!`（即 `^!`，URL-encode）或用 `.../+/<hash>^!/` 只看那一顆的改動。
- **regression test 反查**：fix commit 幾乎都**同時附一個 `test/mjsunit/regress/regress-<bugid>.js`**。這個 test 常常（像本章的例子）把 root cause、觸發條件、正解/錯解都寫在註解裡——**有時候讀 test 比讀 diff 還快**。[Ch 31](./31-oss-fuzz-regression.md) 專門講這個金礦。
- **本地深挖特定 commit**：真要在本地對兩個版本 build 出兩顆 d8 做「行為 diff」，`git fetch --depth=N` 把那段歷史拉下來，checkout fix 前一顆 build 一次、fix 那顆 build 一次，同一段 PoC 跑兩顆看結果差異。貴，但對複雜 bug 最可靠。

### 驗證我們的個案：兩種執行路徑給不同答案

這個 regression test 在**當前（已修）**的 d8 上是「不觸發」的——因為 bug 修好了，直譯和優化結果一致。跑它需要 mjsunit 的斷言框架：

```
$ cd ~/v8build/v8
$ ./out/x64.release/d8 --allow-natives-syntax \
    test/mjsunit/mjsunit.js test/mjsunit/regress/regress-491881374.js
$ echo "exit: $?"
exit: 0
```

**exit 0、無輸出 = 所有 `assertEquals` 都過 = 這顆 d8 已含修補。** （這也印證 [Ch 31](./31-oss-fuzz-regression.md) 的觀點：regression test 進了 repo，就變成「這 bug 以後不准回來」的守門員。）

> **踩雷提前講**：如果你直接 `d8 regress-xxx.js` 不帶 `mjsunit.js`，會噴 `ReferenceError: assertEquals is not defined`——因為 `assertEquals`/`assertOptimized` 這些斷言定義在 `test/mjsunit/mjsunit.js`，得**先載它**。這是新手跑 mjsunit test 最常撞的牆。

要「看到 bug」，你得回到 **fix 前**的 commit 建一顆 d8。本 batch 未做（無歷史），但流程就是上面第 6 點的「兩顆 d8 行為 diff」。這裡我們用 regression test 的註解已經完整重建了因果，不需要真的觸發也講得清楚 root cause——**這正是 patch diffing 的價值：不必先有 crash，光讀就能重建攻擊劇本**。

## 五種「fix 形狀 → bug 型」的對應（讀 diff 的速查表）

讀多了 fix，你會發現堵洞的 `+` 就那幾種形狀，每種對應一類 bug。背下這張表，讀 diff 時一眼認出：

| fix 加了什麼（`+`） | 反推出的 bug 型 | 典型 root cause |
|---|---|---|
| 多一個 **bounds/範圍檢查**（`if (i >= len) ...`） | **OOB** | 某路徑漏檢查、或優化器消掉了檢查 |
| 多一個 **clamp**（`if (k >= len) k = len-1`） | side-effect OOB | user callback 在求值中改了長度（[Ch 21](./21-array-prototype-side-effect.md)） |
| 多一個 **deopt / bailout**（`Bailout(...)` / `Deoptimize`） | type confusion | 優化器押了不成立的賭注、該退卻沒退 |
| 多一個 **`compilation-dependency` 登記** | type confusion | 優化器忘了把某賭注登記進 deopt 依賴 |
| 改對一個 **數值公式 / 型別 representation** | miscompile | 算式/representation 選錯（本章的個案） |
| 多一個 **side-effect 標記**（`kEliminatable`→有副作用） | side-effect | 優化器以為某操作無副作用、被 callback 偷改狀態 |
| 多一個 **型別/map 檢查**（`CheckMap` / `CheckInstanceType`） | type confusion | 把 A 物件當 B 用之前沒驗型別 |

**用法**：拿到 diff，先分類「這是上面哪一種 `+`」，就大致知道 bug 型與觸發方向，再細讀。這比每次從零讀快十倍。本章的個案屬於「改對數值公式 → miscompile」；[Ch 26](./26-reading-v8-source-commits.md) 的 `array-lastindexof.tq` 屬於「多一個 clamp → side-effect OOB」。

## 進階：真的比兩顆 build 的行為（differential triage）

有時候光讀 diff 猜不出「修之前到底錯多少」，最可靠的是**建兩顆 d8、跑同一段 PoC 看差異**。流程（需要歷史，`git fetch --depth=N` 拉出 fix 前後兩顆 commit）：

```
   1. checkout fix 的【前一顆】commit → build 出 d8_before
   2. checkout fix 那顆 commit         → build 出 d8_after
   3. 同一段 candidate PoC 分別跑兩顆：
        d8_before：miscompile / crash / OOB   ← bug 現形
        d8_after ：正常 / 通過                ← 已修
   4. 差異 = bug 的可觀測效果
```

對本章的個案，`d8_before` 上 `test_right_phi()` 回 **2**（優化器算錯），`d8_after` 回 **5**（修對）。**這個「同輸入、兩答案」的差異，就是 miscompile 的鐵證**，也是你確認「我真的觸發到那個 bug 了」的方法。

**本 batch 因為 `--no-history` 沒做這個雙 build**（省下的歷史正是代價），但流程你要會——真做 1-day 時，differential triage 是把「猜測的 root cause」變成「確認的 root cause」的關鍵一步。它也是 differential fuzzing（[Ch 28](./28-fuzzilli-internals.md) 進階）的手動版：比對兩個執行環境對同一輸入的輸出，不一致就是 bug。

## 對比：patch diffing V8 vs patch diffing 傳統二進位

| 面向 | 傳統閉源軟體（如 Windows patch） | V8（開源） |
|---|---|---|
| 你拿到的 | 兩個 binary，得用 BinDiff/Diaphora 比組語 | **原始碼 diff**（gitiles/commit） |
| root cause 線索 | 幾乎沒有，全靠逆向猜 | commit message + `Bug:` + **regression test 註解**常直接寫明 |
| 觸發最小化 | 難，要黑箱試 | 有原始碼 + 可自編帶 `%DebugPrint` 的 d8，白箱 |
| 主要難點 | 「diff 在哪、意義是什麼」 | 「root cause → 記憶體破壞」的**升級**那步 |

V8 是**開源**的，patch diffing 的「找 diff、懂 diff」這半段被大幅簡化——難點整個往後移到「怎麼把一個邏輯/型別錯誤，推成可控的記憶體破壞」（Part 3/4）。這對學習是好事：你能把精力放在最有價值的「升級」技巧上。

## 踩雷集錦

1. **只證明「回傳值錯了」就以為完成**：miscompilation 的回傳值錯只是起點。exploit 要的是「把算錯的值餵給陣列存取，讓優化器消掉的 bounds check 變成 OOB」。停在「值錯」= 只做了 patch diffing 的一半。
2. **在已修補的 d8 上想重現 bug**：本 checkout 是 fix 後版本，regression test 跑起來是「靜悄悄地通過」。要看 bug 得回到 fix 前的 commit 建 d8。搞混「驗證已修」和「重現漏洞」會鬼打牆。
3. **跑 mjsunit test 忘了載 `mjsunit.js`**：`assertEquals is not defined`。要 `d8 ... test/mjsunit/mjsunit.js test/mjsunit/regress/xxx.js`（斷言框架在前）。
4. **忽略 regression test 這條捷徑**：很多人埋頭讀 C++ diff，卻沒發現同一顆 commit 附的 `regress-<id>.js` 已經把 root cause、觸發、正解全寫在註解裡。**先讀 test**。
5. **忘了優化器 bug 要「儀式」才觸發**：光呼叫一次函式不會被優化。要 `%PrepareFunctionForOptimization(f); f(...); %OptimizeFunctionOnNextCall(f); f(...);` 逼它進優化路徑（[Ch 0](./00-environment-setup.md)/[Ch 12](./12-speculation-deopt.md)）。少了 `Prepare` 那步在新版 V8 會失效。
6. **把「fix 改了 N 行」當成「bug 有 N 個」**：一個 fix 常同時改多處（主修 + 加防禦 + 加 test + 順手重構）。要分辨哪幾行是**堵洞的關鍵**、哪些只是周邊。看 `+` 裡真正新增「檢查/clamp/bailout/分支修正」的那幾行。

## 進階：再往深一層

- **incomplete fix / variant hunting**：讀完一個 fix，別停。問「這個修補只補了 right-phi 的 Sub，那 right-phi 的**加法**呢？其他 reducer 有沒有同款的 left/right 假設？」——V8 歷史上大量「N-day」是前一個 fix 沒補乾淨的變體。這是從 1-day 走向 0-day 的橋（呼應 [Ch 26](./26-reading-v8-source-commits.md) 的 P0 variant analysis）。
- **bindiff 心法用在原始碼**：即使是開源，對「大 refactor 混著安全修補」的 commit，你也要有「哪些改動是語意性的、哪些是無害搬移」的判斷力。專注在改變**控制流 / 邊界條件 / 型別假設**的行。
- **從 Gerrit 撈更多**：一個 fix 的 Gerrit（`Reviewed-on:`）常有多版 patch。看**第一版 vs 最終版**的演進，能看到 reviewer 逼作者「這個 case 也要處理」——那些被補上的 case 就是額外的觸發點。
- **範圍推斷（typer/range）是升級的核心**：本例的迴圈次數算錯，本質是「值的靜態範圍推斷錯」。V8 pwn 的一大類 primitive 就是「騙優化器把某值的範圍推小 → 它消掉檢查 → 你用範圍外的值打 OOB」。把這個個案的思路直接帶去 [Ch 22](./22-typer-range-analysis-bug.md)。

## 動手練習

1. 完整讀 `test/mjsunit/regress/regress-491881374.js`（含它後面的 `test_left_phi` 對照組與斷言）。回答：作者為什麼要放一個「left-phi 正確運作」的對照 test？（提示：證明 fix 只動 right-phi、沒把本來對的 left-phi 弄壞。）這對你反推 fix 的形狀有什麼幫助？
2. 用 mjsunit 框架在**當前**（已修）d8 上跑這個 test，確認 exit 0（已修）。再想：如果我手上是 fix 前的 d8，這個 test 會怎麼失敗？它會印出什麼樣的 `assertEquals` 落差訊息？
3. **紙上 PoC 升級**：基於「優化器誤判 `i` 只跑 1 次」，設計（寫在紙上/註解，不需真觸發）一段把 `i` 或 `count` 當陣列索引的程式，說明優化器會因為錯誤的範圍推斷消掉哪個 bounds check、實際迴圈多跑幾次會讓索引超界多少。這就是把 miscompilation 接到 OOB 的那一步。
4. 去 gitiles 找一個近期標題含 `[turboshaft]` 或 `[turbofan]` + `Fix`/`Correctly` 的 commit，只看它附的 `regress-*.js`（不看 C++ diff），試著只從 test 註解重建 root cause。再打開 C++ diff 驗證你猜對沒。

## 本章重點整理

- **1-day** 是真實世界主力打法：root cause 已被 fix 揭露，你搶時間窗打舊版本；patch diffing 是它的第一步。
- 讀 diff 的核心動作：對每個 `+`（新增的檢查/clamp/bailout/分支修正）問「**它在防什麼？沒它我怎麼踩？**」——fix 的形狀反推出 bug 的形狀。
- **V8 regression test 常把 root cause、觸發、正解直接寫在註解裡**（本章的 `regress-491881374.js` 就是），有時比讀 C++ diff 還快。
- miscompilation（JIT 結果 ≠ 直譯結果，如迴圈次數算錯）本身不是記憶體破壞，但**錯誤的範圍推斷 → 被消掉的 bounds check → OOB**，這步升級才是 exploit 的實質（接 Part 4）。
- 開源讓「找 diff、懂 diff」變簡單；難點整個移到「邏輯/型別錯 → 可控記憶體破壞」的升級。

## 自我檢核

- [ ] 能用自己的話解釋 1-day 和 0-day 的差別，以及 patch diffing 在 1-day 流程的位置
- [ ] 拿到一段 diff，能對每個 `+` 說出「它在防什麼、拿掉會怎樣」
- [ ] 讀得懂 `regress-491881374.js` 的 root cause：為什麼 `i = 3 - i` 的 right-phi 被誤當 `i - 3`，導致次數算成 1 而非 5
- [ ] 知道跑 mjsunit regression test 要先載 `mjsunit.js`，且當前 d8 跑它是「靜悄悄通過（已修）」
- [ ] 能說出「miscompilation → 記憶體破壞」的升級路徑（錯誤範圍推斷 → bounds check 被消 → OOB）
- [ ] （面試題）「給你一個 V8 security fix 的 commit，你從哪裡開始、怎麼一步步做到能觸發舊版本的 PoC？」——講得出定位 root cause → 反推壞樣子 → 造最小觸發 → 判可利用性 → 接利用

## 延伸閱讀

- **[V8 CTF / 1-day writeup（如 numen、star labs、DDV team 的 V8 1-day 分析）](https://www.numencyber.com/)**
  - **這篇說什麼**：真實把一個 V8 fix commit 逆推成 exploit 的全流程。
  - **和本章的關聯**：本章教流程，這些文章給你「別人怎麼實際走完」的樣板。讀的時候刻意找「他從哪個 commit / regression test 起步」。

- **[Project Zero — 各篇 V8 root cause / variant 分析（googleprojectzero.blogspot.com）](https://googleprojectzero.blogspot.com/)**
  - **讀哪裡**：任一篇標題含 “V8” / “JIT” 的深入分析。看他們怎麼從 fix 反推、又怎麼問「這 fix 夠不夠」。
  - **和本章的關聯**：把「讀懂 fix」升級成「找 incomplete fix / variant」，是 1-day → 0-day 的橋。

- **[V8 `test/mjsunit/regress/` 目錄（原始碼）](https://source.chromium.org/chromium/chromium/src/+/main:v8/test/mjsunit/regress/)**
  - **讀哪裡**：隨機翻幾個 `regress-<bigid>.js`，感受「註解直接寫 root cause」的常態。
  - **和本章的關聯**：本章的個案就出自這裡；[Ch 31](./31-oss-fuzz-regression.md) 會把「從 regression test 反查 bug」講成一套方法。

- **[“Attacking JavaScript Engines” — Phrack 0x45（saelo）](http://www.phrack.org/papers/attacking_javascript_engines.html)**
  - **這篇說什麼**：JS engine 漏洞從 root cause 到 exploit 的經典奠基文（雖以 JSC 為例，模式相通）。
  - **和本章的關聯**：本章「miscompilation → OOB → addrof/fakeobj」的升級路徑，這篇是它的祖師爺版本。

patch diffing 是「別人已經找到、你逆推」。但漏洞研究的另一半是**主動去挖新的**——這需要 fuzzing。下一章進入 Fuzzilli：專為 JS engine 設計的 coverage-guided fuzzer，它怎麼用 IL 生成語法/語意都合法、又能鑽進優化器深處的 JS。

→ [Ch 28 — Fuzzilli 原理：IL、mutator、coverage feedback](./28-fuzzilli-internals.md)
