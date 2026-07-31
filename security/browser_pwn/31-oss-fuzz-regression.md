# Ch 31 — OSS-Fuzz / regression test 當漏洞線索：從測試反推曾經的 bug

> **目標**：把 V8 的**測試基礎設施本身**變成你的漏洞線索來源。學會：(1) V8 的 `test/` 目錄結構，特別是 `test/mjsunit/regress/`——每個 regression test 幾乎都對應「曾經的一個 bug」；(2) 怎麼從一個 `regress-<bugid>.js` **反推**它當初修的是什麼洞（root cause + 觸發條件常直接寫在檔名與註解裡）；(3) **OSS-Fuzz / ClusterFuzz** 是什麼、它怎麼持續產出 crash、你怎麼把它當「別人幫你 fuzz」的線索流。這章把 Part 5 收束：找洞不只靠自己 fuzz，V8 團隊為了「不讓 bug 回來」而留下的測試，是一座公開的、註解齊全的漏洞博物館。

> **環境**：V8 15.3.0，commit `ab2cad06`，`~/v8build/v8/`，d8 在 `out/x64.release/`。本章所有 `ls test/`、regression test 內容、d8 執行都是真跑。真實數據：`test/mjsunit/regress/` 下有 **3109 個** regression test（`ls | wc -l`）——這就是你可挖的線索量級。

## 為什麼需要這個？

前面幾章你學會了自己 fuzz（[Ch 28-29](./28-fuzzilli-internals.md)）、自己 patch-diff（[Ch 27](./27-patch-diffing.md)）。但漏洞研究有個省力的槓桿常被忽略：**V8 團隊每修一個 bug，幾乎都留下一個 regression test**——一個「這個 bug 以後不准回來」的守門員。這些 test 檔累積成一座**公開的漏洞資料庫**，而且：

- **檔名就是 bug 編號**（`regress-491881374.js` ↔ crbug 491881374）——直接連回 bug tracker。
- **註解常寫明 root cause、觸發、正解/錯解**（你在 [Ch 27](./27-patch-diffing.md) 已見識過那個 Turboshaft 例子）。
- **它是可執行的觸發器**——你能直接 `d8` 跑它，觀察行為。

對找 variant、學攻擊模式、理解「某類 bug 長怎樣」，這比自己從零 fuzz 高效太多。而 **OSS-Fuzz** 則是另一個槓桿：Google 用海量算力持續 fuzz V8，crash 進 tracker、修補進 regression test——**這條流水線的產出是公開的，等於全世界最強的 fuzzing 團隊在幫你找線索**，你只要學會怎麼讀它的輸出。

## 先建立直覺：regression test 是「bug 的墓碑」

每個 bug 被修掉時，V8 team 立一塊墓碑（regression test），上面刻著：這裡曾經有個洞、它長這樣、以後誰讓它復活這個 test 就會失敗。對防禦方，墓碑保證 bug 不回來；**對研究者，墓碑是一份帶座標的尋寶圖**：

```
   regress-<bugid>.js
   ├── 檔名 bugid      → 連回 issues.chromium.org/issues/<bugid>（原始報告 + 常附 PoC）
   ├── 版權年份        → 這 bug 大約哪年修的
   ├── // Flags: ...   → 觸發要哪些旗標（--allow-natives-syntax / 特定優化 flag）
   ├── 註解            → root cause、觸發條件、正解 vs 錯解（常常寫得很白）
   └── 程式碼本體      → 直接可跑的最小觸發器
```

**把 regression test 當「已破解的案例」讀**：別人已經找到、修好、還附上教學註解。你讀一百個，就內建了一百種「V8 會出什麼洞」的模式——這是找新洞的嗅覺來源。

## V8 的 `test/` 目錄地圖

先認結構（真跑）：

```
$ ls ~/v8build/v8/test/
benchmarks  bigint  cctest  common  debugger  debugging
filecheck   fuzzer  ...  mjsunit  ...
```

跟漏洞研究最相關的幾個：

| 目錄 | 內容 | 對你的用途 |
|---|---|---|
| **`test/mjsunit/`** | JS 層的功能與回歸測試（`d8` 直接跑） | 主戰場。`regress/` 子目錄是漏洞金礦 |
| **`test/mjsunit/regress/`** | **回歸測試**，一個 bug 一個檔（3109 個） | 從檔名/註解反推曾經的 bug |
| `test/mjsunit/regress/wasm/` | WASM 相關回歸 | WASM 攻擊面（本課主線外） |
| `test/fuzzer/` | libFuzzer 風格的 fuzz target | 看 V8 怎麼被 fuzz（哪些入口被覆蓋） |
| `test/cctest/` | C++ 層單元測試 | 理解內部 API 行為 |

`test/mjsunit/regress/` 的規模（真跑）：

```
$ ls ~/v8build/v8/test/mjsunit/regress/ | wc -l
3109
$ ls ~/v8build/v8/test/mjsunit/regress/ | grep -E 'regress-[0-9]{9}' | head -5
regress-327247469.js
regress-328483357.js
regress-329153104.js
regress-491881374.js
...
```

**3109 個檔，其中很大一部分是安全相關的 bug**。九位數的檔名（`regress-491881374.js`）對應現代 crbug 編號；較短的（`regress-898785.js` 之類）是舊編號。這就是你的挖掘場。

## 從一個 regression test 反推 bug：實例走一遍

用 [Ch 27](./27-patch-diffing.md) 那個 Turboshaft 例子當範本，把「反推」的動作標準化。看它（真跑，檔案存在）：

```
$ sed -n '1,10p' ~/v8build/v8/test/mjsunit/regress/regress-491881374.js
// Copyright 2026 the V8 project authors. ...
// Flags: --allow-natives-syntax

// Turboshaft LoopUnrollingReducer miscompilation PoC
// Bug: GetIterCountIfStaticCanonicalForLoop in loop-unrolling-reducer.cc
// handles the right-phi case (phi on RIGHT side of Sub: `i = c - i`)
// incorrectly. ...
```

**反推的五個問句**（每個 regression test 都這樣拆）：

1. **檔名 → bug 編號 → tracker**：`491881374` → `issues.chromium.org/issues/491881374`。若已解鎖，原始報告 + reporter 分析 + 可能的 PoC 都在那。
2. **版權年份 → 時代座標**：`Copyright 2026` → 這 bug 是近期的，用的是現代 V8 架構（Turboshaft 已上線）。老 test（2016 年那種）打的是舊架構，offset/手法未必能搬。
3. **`// Flags:` → 觸發需求**：`--allow-natives-syntax` 表示它用 `%` 內部函式（多半 `%OptimizeFunctionOnNextCall` 逼優化）。有些 test 還帶特定優化 flag——那告訴你「這 bug 只在某條優化路徑觸發」。
4. **註解 → root cause**：這裡直接寫明「`GetIterCountIfStaticCanonicalForLoop` 對 right-phi 的 Sub 算錯次數」。用 [Ch 26](./26-reading-v8-source-commits.md) 的目錄地圖：`loop-unrolling-reducer.cc` 在 `src/compiler/turboshaft/`。**root cause 免費奉送**。
5. **程式碼 → 觸發器 + 攻擊模式**：`test_right_phi` 那段就是最小觸發。從它你學到一個**攻擊模式**：「讓優化器對迴圈變數的靜態推理出錯 → 次數/範圍算錯」。這個模式可以拿去問：**其他 reducer 有沒有同款左右不分的假設？**（variant hunting）

**在當前（已修）d8 上跑它，是「靜悄悄通過」**（[Ch 27](./27-patch-diffing.md) 驗過，記得載 `mjsunit.js`）：

```
$ cd ~/v8build/v8
$ ./out/x64.release/d8 --allow-natives-syntax \
    test/mjsunit/mjsunit.js test/mjsunit/regress/regress-491881374.js
$ echo "exit: $?"
exit: 0
```

exit 0 = 斷言全過 = 這顆 d8 已含修補。**這就是 regression test 的雙面性**：對 V8 team 它守著「bug 別回來」；對你它是「這裡曾經有洞、拿去學/找 variant」。

## 怎麼系統性地挖 regression test

3109 個檔不可能一個個看。幾個聚焦策略：

- **按子系統 grep 註解**：想找優化器 bug，`grep -rl 'turbofan\|turboshaft\|maglev\|typer\|type confusion' test/mjsunit/regress/*.js`。想找 Array/TypedArray，grep `elements\|OOB\|out.of.bounds\|length`。
- **按 flag 篩**：`grep -rl 'Flags:.*allow-natives' test/mjsunit/regress/` 找用 `%` 逼優化的（優化器類 bug 密集區）。
- **按年份/編號排**：新編號（大數字）= 近期 bug = 現代架構，手法較可搬到當前目標。
- **從一個已知 bug 找鄰居**：讀懂一個 test 後，用它的關鍵字（如 `LoopUnrollingReducer`）grep，找同一元件的其他 regression——同元件常有一串相關 bug（前修不乾淨的 variant）。
- **對照 tracker 的安全標籤**：把檔名 bugid 丟 tracker，篩出標 `Type=Vulnerability` 的——那些是確定的安全 bug，其他可能只是功能 bug。

## OSS-Fuzz / ClusterFuzz：別人持續幫你 fuzz

**ClusterFuzz** 是 Google 的大規模 fuzzing 基礎設施；**OSS-Fuzz** 是它對開源專案（含 V8）的公開版。它 24/7 用海量核心（[Ch 29](./29-running-fuzzilli.md) 提過，這是你個人難在 tip 撿洞的原因）跑各種 fuzzer（含 Fuzzilli 衍生的、libFuzzer target），流程是：

```
   OSS-Fuzz/ClusterFuzz（上千核心 24/7）
        │ 找到 crash
        ▼
   自動 minimize + dedup + 二分找 regression range
        │ 開 bug（附 reproducer）→ issues.chromium.org（安全 bug 上鎖）
        ▼
   V8 team 修補（commit 帶 Bug: <id>）
        │ 附一個 regress-<id>.js
        ▼
   90 天後揭露 → bug 細節 + reproducer 公開
```

**這條流水線對你的價值**：

- **它的產出是公開線索流**：已揭露的 OSS-Fuzz bug（bug tracker 上標 `oss-fuzz` / reporter 是 ClusterFuzz）帶著 reproducer，你能直接讀「一個真實 fuzzing crash 長怎樣、root cause 是什麼」。
- **regression range 是禮物**：ClusterFuzz 會自動二分出「哪個 commit 引入、哪個 commit 修掉」。這對 [Ch 27](./27-patch-diffing.md) 的 patch diffing 是現成的座標——你不用自己找 fix commit。
- **它幫你劃出「已被機器掃過的地面」**：知道哪些入口被 OSS-Fuzz 重度覆蓋，你就知道**個人 fuzzing 該避開那些（撿不到）、往它覆蓋不到的角落（新內建、複雜狀態組合、需要特定 harness 的路徑）鑽**——這是個人 fuzzing 選戰場的關鍵。

**怎麼用**：逛 `issues.chromium.org` 篩 reporter=ClusterFuzz + component=Blink>JavaScript + 已揭露；或看 OSS-Fuzz 的公開 issue tracker。挑帶 reproducer 的讀，把它當「免費的、已 minimize 的 PoC + root cause 練習題」。

## 實戰：一次 regression 挖掘 session

把「怎麼挖」演一遍（真跑，你可以照做）。假設你今天想研究「Array 內建的 side-effect bug」這一類：

```
# 1. 按關鍵字撈：找註解/程式碼提到 side-effect + array 的 regression
$ grep -rln 'side.effect\|valueOf\|toPrimitive' ~/v8build/v8/test/mjsunit/regress/*.js | head

# 2. 撈到後，挑一個讀它的「五問句」線索：檔名、年份、Flags、註解
$ head -20 ~/v8build/v8/test/mjsunit/regress/<挑的檔>.js

# 3. 從註解/檔名抽出元件名，反查同元件的其他 regression（找 variant 群）
$ grep -rln '<元件名，如某 builtin>' ~/v8build/v8/test/mjsunit/regress/*.js
```

**這個 session 的產出不是一個 bug，是一個「模式 + 一串同類 bug」**：你會發現「side-effect 改陣列長度」這個模式在 `regress/` 裡出現過**很多次**（不同內建、不同年份）——這證明它是個**反覆出洞的洞型**，而「反覆出洞的地方」正是 variant hunting 最肥的礦。你下一步就去當前原始碼，看這個模式在**還沒被 regression test 覆蓋的內建**裡補乾淨了沒。

**進階挖法：按「元件」而非「關鍵字」聚焦**。讀懂一個 bug 後，用它的**函式名/reducer 名**（如 [Ch 27](./27-patch-diffing.md) 的 `LoopUnrollingReducer`）grep，你會撈出「同一個元件被修過的所有 bug」。一個元件被修 5 次，意味它的邏輯很微妙、開發者反覆犯錯——第 6 個洞很可能還在那附近。這比按通用關鍵字（`OOB`）聚焦精準得多。

## 對比：三種漏洞線索來源

| 來源 | 你做的事 | 成本 | 產出 |
|---|---|---|---|
| **自己 fuzz（Fuzzilli）** | build + 跑 + triage | 高（算力、時間、triage） | 可能全新，但 tip 上難撿 |
| **regression test 挖掘** | 讀 + 反推 + 找 variant | 低（讀碼） | 攻擊模式、variant 線索、root cause 教材 |
| **OSS-Fuzz 已揭露 bug** | 讀 tracker + reproducer | 低（讀） | 現成 PoC + regression range + root cause |

**最聰明的組合**：先用 regression test / OSS-Fuzz **學模式、找 variant**（低成本、高命中），把「這類 bug 長怎樣」內化；再用**針對性 fuzzing** 往「機器沒重度覆蓋的角落」鑽（避開和 ClusterFuzz 硬碰）。純靠個人 fuzz tip-of-tree 是最貴、命中最低的路。

## 踩雷集錦

1. **在當前 d8 上想用 regression test「重現漏洞」**：當前是已修版本，test 跑起來是靜悄悄通過。要看 bug 得回到引入~修補之間的 commit（regression range 幫你標好了）。混淆「驗證已修」和「重現漏洞」會鬼打牆（同 [Ch 27](./27-patch-diffing.md) 的雷）。
2. **跑 mjsunit test 忘了載 `mjsunit.js`**：`assertEquals is not defined`。要 `d8 ... test/mjsunit/mjsunit.js test/mjsunit/regress/xxx.js`。
3. **只看程式碼不看註解/檔名**：regression test 的**檔名（bugid）和註解**才是線索精華——root cause、觸發、正解常寫在那。埋頭讀 code body 反而慢。
4. **以為老 regression test 手法能直接搬**：2016 年的 `regress-*.js` 打的是舊架構（無 Turboshaft、無 sandbox、offset 全不同）。看版權年份判斷時代，別把古董當現貨（呼應 [Ch 0](./00-environment-setup.md) 的環境漂移紀律）。
5. **想在 OSS-Fuzz 重度覆蓋的入口硬 fuzz**：那些地面被上千核心掃過了，個人撿不到。個人 fuzzing 要挑機器覆蓋不到的角落。
6. **把所有 regression test 都當安全 bug**：3109 個裡很多是功能/正確性 bug、非安全。要靠 tracker 的 `Type=Vulnerability` 標籤或註解裡的 OOB/UAF/type confusion 字樣篩出真正安全相關的。

## 進階：再往深一層

- **variant hunting 的閉環**：讀懂一個 regression test → 抽出攻擊模式 → grep 找同元件的其他 test（看這類 bug 修過幾次）→ 讀當前原始碼問「這個模式在別的 code path 補乾淨了嗎」→ 沒補的就是你的 0-day 候選。這是 regression test 通往新洞的正道，直接呼應 [Ch 26](./26-reading-v8-source-commits.md) 的 P0 variant analysis。
- **regression range → patch diff**：ClusterFuzz 給的「引入 commit / 修補 commit」是 [Ch 27](./27-patch-diffing.md) 的完美輸入。有了範圍，patch diffing 從「大海撈 fix」變成「看這兩顆 commit 的 diff」。
- **N-day 的溫床**：安全 bug 修補後 90 天才揭露，但 **regression test 常在修補當下就進了公開 repo**（早於揭露）。有心人盯著 `test/mjsunit/regress/` 的新增檔，能在 bug 還鎖著時就從 test 反推出 root cause——這是 1-day/N-day 研究者的實戰技巧（也是為什麼有些團隊監控 V8 的 test commit）。
- **fuzzer harness 本身是攻擊面地圖**：讀 `test/fuzzer/` 看 V8 開放了哪些 fuzz 入口——反過來，**沒被 harness 覆蓋的入口**就是 OSS-Fuzz 掃不到、值得個人針對的角落。
- **跨引擎對照**：同一類 bug（如某個 Array 內建的 side-effect）常在 V8/JSC/SpiderMonkey 都出現過。讀一個引擎的 regression test，去別的引擎找對應的沒補乾淨處——這是跨引擎 variant hunting。

## 動手練習

1. **(真跑)** `ls ~/v8build/v8/test/mjsunit/regress/ | wc -l` 確認數量。再 `grep -rl 'turbofan\|turboshaft\|typer' ~/v8build/v8/test/mjsunit/regress/*.js | head` 找優化器相關的 regression test，挑一個讀。用本章的「五個問句」反推它當初的 bug。
2. **(真跑)** 用 `mjsunit.js` 框架跑 `regress-491881374.js`，確認 exit 0（已修）。再讀它後半的 `test_left_phi` 對照組——為什麼作者要證明 left-phi 沒被弄壞？這對「fix 的範圍」透露什麼？
3. **(反推)** 挑一個檔名是九位數的 regression test，把 bugid 丟 `issues.chromium.org/issues/<id>`。看它是否已解鎖、是不是 ClusterFuzz 報的、有沒有 reproducer。記錄：從檔名到 tracker，你多快能拿到 root cause？
4. **(variant)** 讀懂一個 regression test 後，用它元件的關鍵字（如某個 reducer 名）grep 整個 `regress/` 目錄，看同元件被修過幾次。列出這串 bug——它們是「同一個地方反覆出洞」的證據，也是 variant hunting 的起點。
5. **(選戰場)** 逛 OSS-Fuzz/ClusterFuzz 已揭露的 V8 JS bug 三五個，歸納它們集中在哪些入口。反過來想：哪些角落它似乎沒重度覆蓋？（新內建？需要特定 flag 組合的路徑？）——這是你個人 fuzzing 該挑的地方。

## 本章重點整理

- V8 每修一個 bug 幾乎留一個 **regression test**（`test/mjsunit/regress/`，本 checkout 3109 個），是一座**公開、註解齊全的漏洞博物館**。
- 反推一個 `regress-<bugid>.js` 的五問句：**檔名(bugid)→tracker、版權年份→時代、`Flags:`→觸發需求、註解→root cause、程式碼→觸發器+攻擊模式**。
- 當前 d8 跑 regression test 是「靜悄悄通過（已修）」；要看 bug 得回到 regression range 之間的 commit。記得載 `mjsunit.js`。
- **OSS-Fuzz/ClusterFuzz** 用海量算力持續 fuzz，產出公開線索流：已揭露 bug 帶 reproducer + **regression range**（現成的 patch-diff 座標），還幫你標出「哪些地面已被機器掃過」。
- 最聰明的策略：用 regression test / OSS-Fuzz **低成本學模式、找 variant**，再用**針對性 fuzzing 往機器沒覆蓋的角落**鑽，別硬碰 ClusterFuzz。
- variant hunting 閉環：讀懂一個 test → 抽模式 → 找同元件其他 test → 問「當前原始碼補乾淨了嗎」→ 沒補的是 0-day 候選。

## 自我檢核

- [ ] 能用「五問句」從一個 regression test 反推它當初修的 bug（root cause + 觸發 + 攻擊模式）
- [ ] 知道 `test/mjsunit/regress/` 是什麼、規模量級、怎麼按子系統/年份/flag 聚焦挖掘
- [ ] 解釋 regression test 對防禦方（守門員）與研究者（尋寶圖）的雙面性
- [ ] 說得出 OSS-Fuzz/ClusterFuzz 的流水線，以及它的哪些產出（reproducer、regression range）對你最有用
- [ ] 知道為什麼「用 regression test/OSS-Fuzz 找 variant」比「個人硬 fuzz tip」高效，以及個人 fuzzing 該挑什麼戰場
- [ ] 理解「regression test 常早於 bug 揭露就進 repo」對 N-day 研究的意義
- [ ] （面試題）「除了自己跑 fuzzer，你還有哪些低成本的 V8 漏洞線索來源？怎麼用它們找 variant？」

## 延伸閱讀

- **[V8 `test/mjsunit/regress/`（原始碼）— source.chromium.org](https://source.chromium.org/chromium/chromium/src/+/main:v8/test/mjsunit/regress/)**
  - **讀哪裡**：隨機翻十個 `regress-*.js`，感受「檔名=bugid、註解=root cause」的常態，練「五問句」反推。
  - **和本章的關聯**：本章的核心素材庫；[Ch 27](./27-patch-diffing.md) 的個案就出自這。

- **[OSS-Fuzz 文件 — google.github.io/oss-fuzz](https://google.github.io/oss-fuzz/)** 與 **[ClusterFuzz — google.github.io/clusterfuzz](https://google.github.io/clusterfuzz/)**
  - **讀哪裡**：它怎麼 minimize/dedup/找 regression range、bug 怎麼揭露。
  - **和本章的關聯**：理解那條「crash → bug → 修補 → regression test → 揭露」的公開流水線，以及每個環節你能撿什麼線索。

- **[Chromium issue tracker — issues.chromium.org（篩 reporter=ClusterFuzz, component=Blink>JavaScript）](https://issues.chromium.org/)**
  - **讀哪裡**：已揭露、帶 reproducer 的 V8 JS 安全 bug。
  - **和本章的關聯**：把「regression test 反推」和「OSS-Fuzz 產出」在真實 bug 上對起來——檔名 bugid 直接連到這裡的報告。

- **[Project Zero — variant analysis 相關文章](https://googleprojectzero.blogspot.com/)**
  - **這篇說什麼**：從已知 bug 系統性找變體的方法論。
  - **和本章的關聯**：本章「variant hunting 閉環」的理論靠山，串起 [Ch 26](./26-reading-v8-source-commits.md)、[Ch 27](./27-patch-diffing.md)、本章成一套「找洞不靠運氣」的方法。

Part 5 到此收束：你會讀 V8 原始碼與 commit、會 patch-diff、懂 Fuzzilli 原理與實跑、會 triage crash、會把 regression test/OSS-Fuzz 當線索流。接下來兩個練習把這些串成完整流程——練習 D 走「真實 commit → patch-diff → PoC 思路」，練習 E 走「Fuzzilli session → 植入 crash → triage」。

→ [練習 D — patch-diff 一個真實 V8 security commit，反推 root cause 與 PoC](./practice-d-patch-diff-poc.md)
