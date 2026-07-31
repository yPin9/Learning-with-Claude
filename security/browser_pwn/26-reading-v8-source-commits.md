# Ch 26 — 讀 V8 原始碼與 commit：目錄地圖、gitiles、追一個修補

> **目標**：把「V8 原始碼」從一堆讓人卻步的檔案，變成一張你查得動、找得到路的地圖。學會三件事：(1) `src/` 底下每個大目錄住著哪一類程式碼、你要找的 bug 該去哪個目錄挖；(2) 怎麼用 gitiles / `git log` / `git show` / Chromium bug tracker 把「某個 commit → 它修了什麼 bug」串起來；(3) 怎麼讀 Torque（`.tq`）——V8 內建函式的實作語言，因為很多 Array/String 的洞就長在那裡。這章是 Part 5 的入口：**找洞的第一步是讀懂別人已經找到並修好的洞**。

> **環境**：V8 15.3.0（candidate），commit `ab2cad06`，原始碼在 `~/v8build/v8/`。本章所有 `ls` / `git show` / Torque 片段都是在這顆 checkout 上真跑出來的。**一個重要限制先講在前面**：我們在 [Ch 0](./00-environment-setup.md) 用 `fetch --no-history` 拉原始碼，所以這顆本地 checkout 只有 **1 個 commit**（`git log --oneline | wc -l` 回 `1`）。要看歷史 commit 的 diff，得靠線上的 gitiles，或針對特定 commit 另外 `git fetch`——這章會教你怎麼繞。

## 為什麼需要這個？

你在 `binary_exploitation` 找 glibc 的洞時，libc 原始碼幾萬行，還算讀得完。V8 是**另一個量級**：

```
$ cd ~/v8build/v8 && ls src/ | wc -l
```

`src/` 底下就有五十幾個子目錄，總行數以百萬計。你不可能「讀完」V8。找洞的人也不讀完——他們**針對性地讀**：知道自己要找哪一類 bug、那類 bug 住在哪個目錄、然後只深挖那塊。這章給你的就是這張「哪類 bug 住哪」的地圖，以及「怎麼從一個修補反推它修了什麼」的追查技術。

而且漏洞研究有個殘酷的現實：**絕大多數新洞，是從舊洞的變體、或從一個修補的不完整，長出來的**。Project Zero 的 1-day / variant analysis 方法論，核心就是「讀懂一個 fix，然後問：這個 fix 夠不夠？同一個模式在別處還在不在？」。所以你的第一項硬技能不是「憑空找洞」，而是**讀懂別人的修補**。這章教你怎麼把一個 commit 攤開來看。

## 先建立直覺：原始碼是一座分層的城市

回想 [Ch 2](./02-v8-architecture.md) 的管線圖（Parser → Ignition → Maglev/TurboFan → GC）。V8 原始碼的目錄結構，幾乎就是那張管線圖的實體投影。你只要記住「一段 JS 從進來到執行經過哪些站」，就能猜到對應的程式碼在哪個目錄：

```
   一段 JS 的旅程                     對應的 src/ 目錄
   ─────────────                     ────────────────
   解析原始碼          ──────────►    src/parsing/  src/ast/
   編成 bytecode      ──────────►    src/interpreter/
   直譯執行            ──────────►    src/interpreter/  src/runtime/
   內建函式(Array.push)──────────►    src/builtins/ (*.tq / *.cc)
   熱了 → 中階優化     ──────────►    src/maglev/
   熱了 → 頂階優化     ──────────►    src/compiler/ (TurboFan/Turboshaft)
   物件在記憶體怎麼長  ──────────►    src/objects/
   GC / 堆管理         ──────────►    src/heap/
   正規表達式          ──────────►    src/regexp/
   sandbox 邊界        ──────────►    src/sandbox/
```

這張對照表你會反覆用到。看到一篇 writeup 說「這是 TurboFan 的 typer bug」，你立刻知道去 `src/compiler/` 找 `typer.cc`；說「這是 Array.prototype.lastIndexOf 的 side-effect」，你去 `src/builtins/array-lastindexof.tq`。**能把「bug 的描述」翻譯成「該讀哪個檔」，你就上道了。**

## 目錄地圖：逐個攤開

先看真實的 top-level：

```
$ ls ~/v8build/v8/src/
api          ast          baseline     bigint       builtins
codegen      common       compiler     compiler-dispatcher
d8           debug        deoptimizer  diagnostics  execution
extensions   flags        fuzzilli     handles      heap
ic           init         inspector    interpreter  json
libplatform  maglev       numbers      objects      parsing
profiler     regexp       roots        runtime      sandbox
snapshot     strings      torque       trap-handler utils
wasm         zone         ...
```

從「攻擊面密度」的角度，把重要的幾個標出來（密度沿用 [Ch 2](./02-v8-architecture.md) 的礦脈圖）：

| 目錄 | 住著什麼 | 攻擊面 | 你什麼時候來這 |
|---|---|---|---|
| `src/compiler/` | TurboFan + Turboshaft（sea-of-nodes / block-based IR、typer、各種 reducer） | ██████ 最高 | 找 type confusion、優化器誤推理（Part 4 主場） |
| `src/maglev/` | Maglev 中階 JIT | ████ 高 | Maglev 專屬的型別 bug |
| `src/builtins/` | 內建函式（`.tq` Torque + `.cc` CSA） | ▓▓ 中 | Array/String/RegExp/JSON 的實作 bug（Ch 21/25） |
| `src/objects/` | 物件模型（Map、JSArray、FixedArray、elements kind） | ▓▓ 中 | 讀懂記憶體佈局、找 UAF-ish |
| `src/runtime/` | runtime 函式（`%Foo` 背後、慢路徑） | ▓ 低中 | 慢路徑的邊界 bug |
| `src/heap/` | GC（Orinoco、Scavenger、mark-compact） | ▓▓ 中 | GC 時序、佈局、UAF |
| `src/regexp/` | 正規表達式引擎（Irregexp） | ▓ 低中 | regexp 的洞（Ch 25） |
| `src/sandbox/` | V8 Sandbox（ubercage、trusted space） | — | 理解護欄、找 sandbox 逃逸（Ch 34） |
| `src/parsing/` `src/ast/` | Parser / AST | ░ 稀少 | 罕見的語法邊界 bug |

`src/compiler/` 自己就是一整個世界（幾百個檔）。先認幾個你在 writeup 裡最常聽到的名字：

```
$ ls ~/v8build/v8/src/compiler/ | head -40
access-builder.cc      access-info.cc
branch-elimination.cc  bytecode-graph-builder.cc
common-operator.cc     compilation-dependencies.cc
...
```

- **`typer.cc` / `types.cc`**：TurboFan 的型別系統核心。它推斷「這個節點的值是什麼型別範圍」。**typer 推錯 = type confusion 的源頭**（Part 4 的 [Ch 19](./19-turbofan-type-confusion.md)）。
- **`simplified-lowering.cc`**：把高階操作降到低階、決定 representation（tagged / float64 / word32）。歷史上一堆 bug 出在這（representation 選錯 → OOB）。
- **`*-reducer.cc`**（如 `typed-optimization.cc`、`load-elimination.cc`、`branch-elimination.cc`）：各種優化 pass。每個 reducer 都在「假設某些不變式」，假設錯就是洞。
- **`compilation-dependencies.cc`**：優化器押的賭注（「這個 Map 不變」）登記在這，賭注被打破時觸發 deopt。**登記不完整 = 該 deopt 卻沒 deopt**。
- **`backend/`**：把 IR 降到機器碼的後端（instruction selection、register allocation）。

`compiler/` 旁邊還有 **Turboshaft**（`src/compiler/turboshaft/`）——V8 較新的、block-based（非 sea-of-nodes）的 IR，正逐步取代 TurboFan 的部分 pass。你在近期 commit / regression test 會看到 `turboshaft` 字樣（本章後面那個真實例子就是 Turboshaft 的 bug），別當成打錯字。

## 底層機制：一個 commit 長什麼樣、怎麼追

先看我們本地這顆 checkout 的 tip commit——它本身就是一個活生生的教材：

```
$ cd ~/v8build/v8 && git show --stat ab2cad06 | head -16
commit ab2cad06c8e209248ce0d2cf76e1f0d16aa51533
Author: Victor Gomes <victorgomes@chromium.org>
Date:   Fri Jul 31 16:05:08 2026 +0200

    [turbofan] Disable additive safe integer feedback

    Bug: 539350801

    Change-Id: I679b5120df2d736da823f7420b2432df31302d6c
    Reviewed-on: https://chromium-review.googlesource.com/c/v8/v8/+/8174345
    ...
    Cr-Commit-Position: refs/heads/main@{#108981}
```

一個 V8 commit message 有幾個**對追查漏洞至關重要**的欄位，逐個講：

- **標題的 `[turbofan]` 標籤**：告訴你這改動屬於哪個子系統。看到 `[turbofan]` / `[maglev]` / `[compiler]` / `[runtime]` 就知道去哪個目錄，也大致知道 bug 的性質。
- **`Bug: 539350801`**：對應 **Chromium issue tracker**（`issues.chromium.org/issues/539350801`）的編號。安全 bug 一開始通常**上鎖**（restricted view），修補上線且過了 90 天揭露期後才公開。這個編號是你「commit → 原始 bug 報告 → 常常附帶 PoC」的鑰匙。
- **`Reviewed-on: .../c/v8/v8/+/8174345`**：**Gerrit（chromium-review）的 code review 連結**。點進去能看到完整的 review 討論、每一版 patch、reviewer 的意見——常常比 commit message 本身資訊多十倍。安全修補的 review 討論若沒鎖，能看到「為什麼這樣改」的第一手推理。
- **`Cr-Commit-Position: refs/heads/main@{#108981}`**：這個 commit 在 main 分支的**序號**。用來判斷「某個 Chrome 版本包不包含這個修補」——這是 1-day 開發判斷「這洞在目標版本補了沒」的關鍵。

看這顆 commit 改了什麼（因為它就是我們的 tip，能直接 `git show`）：

```
$ git show ab2cad06 -- src/compiler/ src/flags/ | head -40
```

它做的事，光看標題「Disable additive safe integer feedback」就有味道：把某個「加法的 safe-integer feedback」優化**關掉**。優化器類的 commit 標題出現 `Disable` / `Fix` / `Correctly handle` / `Bail out`，八成是在收某個推理錯誤——這正是你找洞時最想讀的那種 commit。

### gitiles：線上讀歷史（繞過 no-history）

我們本地 checkout 沒有歷史，所以 `git log ab2cad06~5` 會失敗。線上有兩條路：

1. **gitiles**（Google 的 git web 介面）：`https://chromium.googlesource.com/v8/v8/`
   - 看某個檔的歷史：`.../+log/refs/heads/main/src/compiler/typer.cc`
   - 看某個 commit 的 diff：`.../+/<commit-hash>`
   - gitiles 網址可以直接加 `?format=TEXT` 拿純文字 diff，方便 script 化。
2. **針對性 `git fetch`**：如果你只需要某幾個 commit 的完整 diff 在本地分析，可以：
   ```
   $ git fetch --unshallow          # 拉完整歷史（大，慎用）
   # 或只加深一點：
   $ git fetch --depth=200
   ```
   `--unshallow` 會把 `--no-history` 省下的東西補回來——磁碟和時間都要付出代價，只在真的要做大量本地 patch-diff 時才做。

### Chromium bug tracker：從 commit 追到 PoC

流程是雙向的，你兩個方向都會走：

- **正向（有 commit，想知道它修什麼）**：commit message 的 `Bug:` 編號 → 開 `issues.chromium.org/issues/<id>` → 若已解鎖，裡面常有原始 crash、reproducer、reporter 的分析。
- **逆向（想找近期修的安全洞）**：逛 `issues.chromium.org` 篩 `component:Blink>JavaScript` + `Type=Vulnerability` + 已揭露；或看 Chrome Release blog 的 security fix 清單（每個 stable 更新都列 CVE + 對應 bug 編號 + 賞金）。

> **關於 90 天揭露**：Google 的政策是安全 bug 修補後預設 90 天才公開細節。所以你「現在」能讀到的公開 bug，是「約 3 個月前修的」。這個時間差是 1-day 研究的天然節奏——揭露當下就是一批人開始搶做 exploit 的起跑槍（[Ch 27](./27-patch-diffing.md) 講這個流程）。

## 讀 Torque（`.tq`）：內建函式的洞長這裡

`src/builtins/` 底下大量 `.tq` 檔，用 V8 自己的 DSL **Torque** 寫成。為什麼要另外一種語言？因為內建函式（`Array.prototype.push`、`String.prototype.slice`…）既要**快**（接近手寫組語）、又要**型別安全到一定程度**（純 CSA 手寫太容易出錯）。Torque 是兩者的折衷：它會被編譯成 CSA、再變成機器碼，同時 Torque 的型別系統能在編譯期擋掉一部分錯誤。

**為什麼漏洞研究要讀 Torque**：Array/TypedArray/String 這類「操作 elements 的內建」是經典攻擊面（Ch 25）。它們的邏輯就寫在 `.tq` 裡，而 bug 常常是「某個邊界檢查在特定 elements kind / 特定 side-effect 下被繞過」。你不讀 `.tq` 就看不到那個檢查在哪、為什麼漏。

看一個真實、而且**註解直接標了 bug 編號**的例子——`Array.prototype.lastIndexOf` 的 Torque 實作：

```
$ sed -n '32,52p' ~/v8build/v8/src/builtins/array-lastindexof.tq
macro FastArrayLastIndexOf<Elements : type extends FixedArrayBase>(
    context: Context, array: JSArray, from: Smi, searchElement: JSAny): Smi {
  const elements: FixedArrayBase = array.elements;
  const elementsLen: Smi = Convert<Smi>(elements.length_intptr);
  let k: Smi = from;

  // Bug(898785): Due to side-effects in the evaluation of `fromIndex`
  // the {from} can be out-of-bounds here, so we need to clamp {k} to
  // the {elements} length. We might be reading holes / hole NaNs still
  // due to that, but those will be ignored below.
  if (k >= elementsLen) {
    k = elementsLen - 1;
  }
```

這段是**讀 Torque 的完美教材**，逐點拆：

- **`macro FastArrayLastIndexOf<Elements : ...>`**：Torque 的 `macro` 會 inline 到呼叫點；尖括號是**泛型參數**（這裡對不同 `Elements` 型別各生一份），呼應 [Ch 7](./07-jsarray-elements-kind.md) 的 elements kind。
- **`array.elements` / `elements.length_intptr`**：Torque 能直接存取物件的內部欄位（`elements`、`length`），型別是 `FixedArrayBase`、`Smi`、`intptr` 這些 V8 內部型別——**這就是你在 `%DebugPrint` 看到的那些欄位，在程式碼裡的樣子**。
- **那段 `// Bug(898785)` 註解**：這是重點。它明說「因為 `fromIndex` 求值時的 **side-effect**，`from` 可能已經**越界**了，所以要 clamp」。翻譯成攻擊者語言：**歷史上有人讓 `fromIndex` 的 getter 在求值時偷偷縮短陣列，導致後面用舊的 `from` 去讀，OOB read**。這正是 [Ch 21](./21-array-prototype-side-effect.md) 那類 side-effect callback 攻擊的活化石。修補就是這個 `if (k >= elementsLen) k = elementsLen - 1;`。

**看到 `.tq` 裡的 `// Bug(...)` 或 `// Note:` 註解就慢下來讀**——那往往是「這裡曾經有洞、這是補丁、這是為什麼」的三合一線索。找 variant 的人專門盯這種註解：問「這個 clamp 夠不夠？別的 elements kind 有沒有漏掉類似的 clamp？」

Torque 的 `otherwise`/`labels`（例外/跳轉標籤）、`UnsafeCast`（跳過型別檢查的轉型——**名字裡的 Unsafe 就是紅旗**）也值得留意。`UnsafeCast` 是「程式設計師向 Torque 保證這裡型別一定對」的地方，保證錯了就是 type confusion 的溫床。找洞時 `grep -rn UnsafeCast src/builtins/` 是個起手式。

## 找洞的 grep 起手式：從紅旗字串下手

讀 V8 找洞不是從頭讀，是**針對「危險模式」grep**，把幾百萬行縮成幾十個可疑點。這裡是一組實戰起手式（縮範圍後才 grep，別盲掃）：

| grep 什麼 | 為什麼是紅旗 |
|---|---|
| `UnsafeCast` in `src/builtins/` | 程式設計師手動保證型別、跳過檢查——保證錯 = type confusion |
| `// Bug(` / `// TODO` / `// FIXME` in `.tq` | 開發者自己標的「這裡曾/可能有問題」 |
| `DisallowGarbageCollection` / `DisallowHeapAllocation` | 這段假設 GC 不會發生——若假設被打破可能 UAF |
| `raw_` / `RawField` / `unchecked` | 繞過安全存取的原始操作 |
| side-effect / `kNoSideEffects` in `src/compiler/` | 優化器對「有無副作用」的判斷——判錯 = side-effect 漏洞 |

示範（真跑，數量會因版本略異）：

```
$ grep -rln UnsafeCast ~/v8build/v8/src/builtins/ | head
$ grep -rn 'Bug(' ~/v8build/v8/src/builtins/*.tq | head
```

`grep Bug(` 會撈出一堆像 [Ch 26 前面那個 `Bug(898785)`] 的「歷史傷疤」——每個都是「這裡曾經有洞、這是補丁」，也是找 variant 的起點（那個補丁補乾淨了嗎？）。

## 讀 objects/：一個物件在記憶體怎麼長

找 type confusion / OOB 常要對照「這個物件在記憶體的佈局」。`src/objects/` 是佈局的定義處，且**很多也是 Torque（`.tq`）**——`.tq` 裡的欄位宣告順序，就是記憶體裡的欄位順序，也就是你 `%DebugPrint` 看到的順序（[Ch 5](./05-map-hidden-class.md)）。

```
$ ls ~/v8build/v8/src/objects/ | grep -E 'js-array|fixed-array|map' | head
```

例如 `js-array.tq` 定義 `JSArray` 的欄位（`length`、`elements`、`properties`…）。**讀 `.tq` 的欄位宣告 = 讀記憶體佈局**。當你在 exploit 裡要「越界打到相鄰物件的 length 欄位」，你得先從這裡確認 length 在物件的哪個 offset。這把「讀原始碼」和「Part 3 的記憶體操作」直接接起來：原始碼的欄位順序 → gef 裡 `x/8gx` 看到的那幾格。

## 對比：讀 V8 原始碼 vs 讀 glibc/kernel 原始碼

| 面向 | glibc / Linux kernel | V8 |
|---|---|---|
| 主要語言 | C（+少量 asm） | C++ **+ Torque(.tq) + CSA**，還有一點 Rust |
| 找 bug 的入口 | CVE + patch | Chromium bug tracker + Gerrit review + `[標籤]` commit |
| 版本定位 | glibc 版本號 / kernel 版本 | **git commit hash + Cr-Commit-Position 序號** |
| 揭露節奏 | 各異 | Google 90 天政策，有可預期的公開節奏 |
| 「哪類 bug 住哪」 | 子系統目錄 | 管線階段目錄（本章的對照表） |
| 內建函式實作 | 直接 C | 得先讀懂 Torque 這層 DSL |

最大的差異：V8 的 commit 生態**極度結構化**（每個 commit 有 Bug/Gerrit/Cr-Position），這對漏洞研究是**福利**——你有非常清楚的線索鏈可追。代價是你得多學 Torque 這層抽象。

## 踩雷集錦

1. **以為本地 `git log` 看得到歷史**：`fetch --no-history` 的 checkout 只有 1 個 commit。想追歷史 commit，用 gitiles 線上看，或 `git fetch --unshallow`（貴）。在本地對舊 commit 跑 `git show` 會直接說 unknown revision。
2. **把 Torque 當成普通 C++ 讀**：`.tq` 有自己的型別系統、`macro`/`builtin`/`labels`/`otherwise` 語法、`UnsafeCast` 語意。用讀 C++ 的直覺硬看會誤判邊界檢查在哪。先花半小時看官方 Torque 文件的語法表。
3. **只看 commit message 不看 Gerrit**：`Reviewed-on:` 的 Gerrit 連結常有比 commit message 詳細得多的討論、多版 patch 的演進、reviewer 對「這個 fix 完不完整」的質疑——後者正是 variant 分析的金礦。
4. **忽略 `Cr-Commit-Position` 序號**：判斷「目標 Chrome 版本有沒有這個修補」要靠序號 / 分支，不是 commit hash 的先後（hash 無序）。1-day 時搞錯這個 = 對著已修補的目標白打。
5. **看到安全 bug tracker 是 403 就以為沒救**：安全 bug 在揭露期內上鎖是常態。等 90 天、或從 Chrome Release blog 的已公開 CVE 清單反查，或看有沒有對應的 mjsunit regression test（[Ch 31](./31-oss-fuzz-regression.md)）——regression test 常在 bug 還鎖著時就進了公開 repo。
6. **`grep` 整個 `src/` 沒有先縮範圍**：V8 太大，盲 grep 一個常見字（如 `length`）會淹死你。先用本章的目錄地圖鎖定 1-2 個目錄再 grep。

## 進階：再往深一層

- **`git blame` 追一行的身世**：對一個可疑的檢查，`git blame -L <line>,+1 <file>` 能查到「這行是哪個 commit 加的、為什麼」。在有歷史的 checkout（或 gitiles 的 blame 視圖）上，這是把「這個 clamp」追回「當初補哪個 bug」的最快路。
- **`OWNERS` / `DIR_METADATA`**：每個目錄有 `OWNERS`（誰能 review 這塊）和 `DIR_METADATA`（bug component 對應）。`DIR_METADATA` 告訴你「這個目錄的 bug 該歸到 tracker 的哪個 component」——反過來幫你在 tracker 上篩對地方。
- **Turboshaft 遷移**：V8 正把 TurboFan 的 pass 逐步搬到 Turboshaft（`src/compiler/turboshaft/`）。遷移期「新舊兩套邏輯並存」本身是 bug 溫床（一邊修了、另一邊忘了）。近期的優化器 bug 很多帶 `turboshaft` 標籤（本課 [Ch 27](./27-patch-diffing.md)、[Ch 31](./31-oss-fuzz-regression.md) 都用到一個真實的 Turboshaft miscompile 例子）。
- **snapshot / mksnapshot**：`src/snapshot/` 是把「內建 + 初始堆」序列化成啟動快照的機制。理解它有助於看懂為什麼某些物件位址在每次啟動都相似（快照決定初始佈局），對 spray 策略有幫助。

## 動手練習

1. `ls ~/v8build/v8/src/compiler/ | wc -l` 數數看 `src/compiler/` 有幾個檔。再 `grep -rln UnsafeCast ~/v8build/v8/src/builtins/ | head` 列出哪些 Torque 內建用了 `UnsafeCast`——這些是「程式設計師手動保證型別」的地方，天然的可疑清單。挑一個檔打開，看那個 `UnsafeCast` 前面憑什麼保證型別對。
2. `git show ab2cad06` 完整讀我們 tip 這顆 commit。回答：它改了哪些檔？標題說「Disable additive safe integer feedback」，對照 diff，它是怎麼「disable」的（加 flag？改預設？刪 code path？）？`Bug: 539350801` 在 tracker 上（現在多半還鎖著）——記下這個編號，等它解鎖回來看。
3. 開 gitiles（`chromium.googlesource.com/v8/v8/+log/refs/heads/main/src/compiler/typer.cc`），瀏覽 `typer.cc` 最近 20 個 commit 的標題。挑一個標題含 `Fix` / `Correctly` / `Bail out` 的，點進去看 diff。試著只從 diff 猜「修之前會發生什麼壞事」——這就是 [Ch 27](./27-patch-diffing.md) 的暖身。

## 本章重點整理

- V8 原始碼的目錄結構是 [Ch 2](./02-v8-architecture.md) 管線圖的實體投影：**把 bug 的描述翻譯成該讀哪個目錄**是核心技能（compiler/maglev = 優化器 type confusion；builtins = Array/String 實作 bug；objects = 記憶體佈局）。
- 一個 V8 commit 的 `[標籤]`、`Bug:`、`Reviewed-on:`(Gerrit)、`Cr-Commit-Position:` 四個欄位，是你「commit ↔ 原始 bug 報告 ↔ PoC ↔ 目標版本有沒有補」的完整線索鏈。
- 本地 `--no-history` checkout 只有 1 個 commit；追歷史靠 **gitiles** 或針對性 `git fetch`。
- **Torque(`.tq`)** 是內建函式的實作語言，Array/String/RegExp 的洞常長在這；`// Bug(...)` 註解與 `UnsafeCast` 是找 variant 的紅旗。
- Google 的 90 天揭露政策，讓公開 bug 有可預期的節奏——這是 1-day 研究的天然時鐘。

## 自我檢核

- [ ] 給你一句「這是 TurboFan typer 的越界推斷」，你能立刻說出去 `src/compiler/` 找哪類檔（typer.cc / simplified-lowering.cc / *-reducer.cc）
- [ ] 能解釋 commit message 裡 `Bug:`、`Reviewed-on:`、`Cr-Commit-Position:` 各自在漏洞追查中的用途
- [ ] 知道為什麼本地 `git log` 看不到歷史，以及兩種繞法
- [ ] 讀得懂一段 Torque `macro`：認得 `array.elements`、`Smi`、`UnsafeCast`、`labels/otherwise`，並知道 `UnsafeCast` 為什麼危險
- [ ] 能說出「90 天揭露政策」對 1-day 研究節奏的意義
- [ ] （面試題）「你會怎麼從一個 Chrome stable 更新的 CVE 編號，一路追到能在本地重現的 PoC？」——講得出 CVE → Chrome release note → bug tracker id → Gerrit/commit → regression test / PoC 這條鏈

## 延伸閱讀

- **[V8 官方 Torque 文件 — v8.dev/docs/torque](https://v8.dev/docs/torque)**
  - **讀哪裡**：語法總覽（`macro` vs `builtin`、型別、`labels`/`otherwise`、`UnsafeCast`）。本章那段 `array-lastindexof.tq` 讀完文件再回看會通透很多。
  - **和本章的關聯**：把「讀 Torque」從猜變成懂，是進 Ch 25 的前提。

- **[V8 原始碼線上瀏覽 — source.chromium.org / chromium.googlesource.com/v8/v8](https://source.chromium.org/chromium/chromium/src/+/main:v8/)**
  - **讀哪裡**：用它的 cross-reference（點一個符號跳到定義/所有引用）讀 `src/compiler/typer.cc`。比本地 grep 好用太多。
  - **和本章的關聯**：本章的目錄地圖 + 這個工具 = 你的日常讀碼工作台。

- **[Project Zero — “The More You Know, The More You Know You Don’t Know”（variant analysis 方法論）](https://googleprojectzero.blogspot.com/)**
  - **這篇說什麼**：P0 團隊怎麼從「一個已修的 bug」系統性地找出同模式的其他 bug。
  - **和本章的關聯**：本章教你讀懂一個修補；這篇教你讀懂之後該問什麼問題（「這 fix 夠嗎？」）——直接通往 [Ch 27](./27-patch-diffing.md)。

- **[Chromium issue tracker — issues.chromium.org（component: Blink>JavaScript）](https://issues.chromium.org/)**
  - **讀哪裡**：篩選已揭露的 JS engine 安全 bug，讀幾個含 reproducer 的。感受真實 bug 報告長什麼樣。

追一個修補的技術有了。下一章把它推到極致：拿到一個**安全修補的 diff**，反推出 root cause，並想清楚怎麼寫出觸發它的 PoC——這就是 1-day exploit 開發的第一步。

→ [Ch 27 — Patch diffing：從一個 security fix 反推 root cause 與 PoC](./27-patch-diffing.md)
