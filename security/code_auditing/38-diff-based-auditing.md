# Ch 38 — diff-based 審計

> **目標**：學會只審**改動**而非整個 codebase：PR gate、增量審計、以及從 security patch 反推漏洞的 variant hunting。你會用 Semgrep 的 `--baseline-commit` 在真 git repo 上做「只報新增問題」的增量掃描，並理解 silent fix 反推的方法論。
> **環境**：WSL、git、semgrep 1.172.0、python3。靶在 `~/audit-lab/ch38/`（本章現建的 git repo）。

到目前為止我們都在審**整個** codebase：對全專案跑 query suite，然後用 Ch 36 的治理把幾千個命中壓成決策。但真實工程裡，最高頻的審計場景其實是另一種：「這個 PR 改了 30 行，幫我看有沒有引入漏洞。」你**不該**每次都重掃十萬行——那太慢、太吵、而且會拿全部舊債來擋一個只改了 30 行的 PR。

diff-based 審計的核心信念：**多數時候，你只需要關心改動，以及改動能觸及的地方**。它有三個典型形態，本章各拆一個：

```
形態              審什麼                       典型場景
PR gate           這次 PR 新引入的問題         CI 卡 PR，只擋新增不擋舊債
增量審計          自上次審計以來的改動          週期性 review，不重掃全部
patch variant hunt 一個 fix 反推的同類漏洞      拿到 security patch 找它漏修的變體
```

---

## 為什麼「只審改動」是對的（也知道它何時錯）

先講對的地方。假設一個成熟專案有 500 個 pre-existing 命中（舊債），你新開一個 PR 改了三個檔。若每次 CI 都全量掃：

- **它會報 500+ 個命中**，其中 500 個是舊債、跟你這次改動無關。開發者看到一片紅，直接無視——**警報疲勞讓 gate 形同虛設**。
- **它無法回答你真正的問題**：「我這次 PR 有沒有**新引入**問題？」這個問題淹沒在舊債裡。

diff 審計把問題縮小到「**這次改動引入的**」，讓 gate 精確、可執行、不吵。舊債另外排期處理（或用 baseline 凍結），不混進 PR gate。

但「只審改動」有個致命前提要記住：**改動的影響不限於改動的那幾行**。一個看似無害的改動（例如把某個函式的回傳型別從 `size_t` 改成 `int`）可能讓**遠處**一個原本安全的 `memcpy` 變成 OOB。只看 diff 的那幾行會漏掉這種「改動觸發的遠處 bug」。所以 diff 審計不是「只掃 diff 的行」，而是「**以 diff 為起點，追它的資料流與型別影響能到多遠**」——這是它和「只看 patch 行」的分水嶺（見踩雷）。

---

## 形態一：PR gate（Semgrep baseline，真跑）

Semgrep 的 `--baseline-commit` 是做 PR gate 最直接的工具。它的機制是：**掃兩次**（當前版本 + baseline commit 版本），只報「當前有、baseline 沒有」的命中——也就是這段 diff **新引入**的。我們在 `~/audit-lab/ch38/` 建一個真 git repo 示範。

**commit 1（baseline）**：一個 pre-existing 的 `strcpy`（舊債）。
**commit 2（PR）**：新增一個 `strcpy`（這是我們要 gate 的東西）。

```bash
cd ~/audit-lab/ch38
git log --oneline
# 19bd2ce add feature   <- PR
# 358bd1c baseline      <- 舊債在這
```

**先看全量掃描——它報兩個（舊+新都報）**：

```bash
semgrep --config rules.yaml . --json -q | \
  python3 -c 'import json,sys;print(len(json.load(sys.stdin)["results"]))'
# 2
```

全量掃看到兩個命中：`app.c:2`（舊債）+ `app.c:3`（新引入）。若拿這個當 PR gate，舊債 `app.c:2` 會無理由擋下這個 PR。

**再用 baseline——只報新增**：

```bash
BASE=$(git rev-parse HEAD~1)          # baseline commit
semgrep --config rules.yaml --baseline-commit "$BASE" . 2>&1 | \
  grep -E "findings|Blocking|line|:[0-9]|┆"
```

真實輸出（節錄）：

```
  Current version has 2 findings.
Creating git worktree from '358bd1cc...' to scan baseline.
  Will report findings introduced by these commits ...
Ran 1 rule on 1 file: 1 finding.
    app.c
          ❰❰ Blocking ❱❱
            3┆ void feature(char *s){ char b[8]; strcpy(b, s); }  // NEW in this PR
```

**關鍵對比**：全量掃 = 2 findings（舊+新），baseline 掃 = **1 finding，只有新引入的 `app.c:3`**。舊債 `app.c:2` 被 baseline 正確過濾掉了。這正是 PR gate 要的：只為你這次改動負責，不拿歷史包袱擋你。

拿 JSON 輸出可以直接餵 CI 做 gate 判斷：

```bash
semgrep --config rules.yaml --baseline-commit "$BASE" . --json -o base.json -q
python3 -c 'import json;print("new findings:", len(json.load(open("base.json"))["results"]))'
# new findings: 1
```

CI 邏輯就是：**new findings > 0 → gate fail（擋 PR）**。這就是練習 F 要組的 pipeline 的核心零件。

### 底層機制：baseline 怎麼認出「新的」

Semgrep 的 baseline 不是靠行號 diff，是**掃兩個版本各得一組命中，用指紋比對**——當前版本命中的指紋不在 baseline 命中集合裡，才算新的。這一點承接 Ch 36 講的指紋：指紋若含絕對行號就不穩，你在檔頭加一行 import 把下面全部行號 +1，baseline 會把整檔都當「新的」，gate 就爆掉一堆假新增。Semgrep 用的是基於程式碼上下文的指紋來抵抗這種漂移，但仍非完美——大規模重排（搬移整個函式）可能讓它誤判。

### CodeQL 的 diff 分析

CodeQL 沒有 semgrep 這麼一鍵的 `--baseline-commit`，它的增量做法有兩條：GitHub code scanning 在 PR 上會**自動 diff**（比對 base branch 與 PR 的 alert 集合，只在 PR 標新增的），這是平台層做的；本地則靠對 base 與 head 各建一次 database、各跑一次 query、用 SARIF 指紋 diff 兩份結果（Ch 39 的 SARIF 合併/去重技能直接可用）。概念相同：兩組命中相減。

---

## 形態二：git diff 範圍限定掃描

比 baseline 更粗但更省的做法：**根本不掃沒動的檔**。先問 git 這次改了哪些檔，只把那些檔丟給掃描器：

```bash
cd ~/audit-lab/ch38
BASE=$(git rev-parse HEAD~1)
git diff --name-only "$BASE" HEAD
# app.c
```

只有 `app.c` 改了，所以只掃它：

```bash
git diff --name-only "$BASE" HEAD | grep '\.c$' | xargs -r semgrep --config rules.yaml
```

這比 baseline 快（省掉建 baseline worktree、少掃檔），但**更粗**：它不做「新舊相減」，所以會把改動檔裡的**舊債也一起報**（`app.c:2` 的舊 strcpy 也會出現）。適合「快速看一眼改了的檔有沒有明顯問題」，不適合當精確 gate。要精確 gate 用 baseline；要快速掃改動檔用 diff 範圍限定——兩者常搭配（先 diff 縮檔範圍，再 baseline 縮命中範圍）。

**注意這裡的取捨與前面「改動影響遠處」的張力**：只掃改動檔會漏掉「改動觸及但檔沒改」的地方（例如你改了 header 的 struct，用它的 `.c` 沒改但受影響）。所以 diff 範圍限定是**效能捷徑**，不是完整性保證——對安全關鍵改動仍要考慮全域資料流（用 CodeQL 全域 taint 追改動點的下游）。

---

## 形態三：patch variant hunting（對回 Ch 26）

這是 diff 審計最進攻性的形態，也是找 0-day 的主力方法之一。核心觀察：**一個安全 patch 洩漏了漏洞的資訊**。維護者修一個 bug 時，patch 本身就是一張藏寶圖——它精確指出「哪裡曾經是錯的、正確該長怎樣」。你可以：

1. **讀 fix 反推漏洞**：patch 加了一個 bound check，說明加之前那裡 OOB。你不用等公告，讀 diff 就知道 pre-fix 版本怎麼觸發。
2. **找 silent fix**：不是每個安全修補都有 CVE 或公告。維護者常在一個「重構」「改善健壯性」的 commit 裡**默默**修掉一個安全 bug（silent fix）。掃 commit 訊息看不出來，要**看 diff 的形狀**——「加了 length 檢查」「把 `strcpy` 換 `strncpy`」「多了 NULL 檢查」都是 silent security fix 的指紋。
3. **找漏修的變體（variant）**：這是重點。維護者修了 `parse_a()` 的 OOB，但**同樣的錯誤 pattern**可能還存在於 `parse_b()`、`parse_c()`——他只修了觸發到的那個。你把 fix 的 pattern 抽象成一條 query（Ch 26 教的「從 CVE/patch 到 query」），對整個 codebase 掃，找出所有**沒被這次 patch 覆蓋的同類**。

### diff-two-versions 的 query 對比法

具體操作：對 **fix 前**和 **fix 後**兩個版本各跑同一條 query，比對結果：

```
pre-fix 版本 query 命中：  { A, B, C }   <- A 是這次被修的
post-fix 版本 query 命中： { B, C }      <- A 消失了（被 fix 了）
                                          <- B, C 還在！= 漏修的變體
```

`B` 和 `C` 是黃金——它們是**同一種漏洞、被同一個 fix 漏掉的實例**，往往就是下一個 CVE。CodeQL 對這種「兩版本 db 各跑 query 再 diff」特別擅長（各建一個 database、各跑、SARIF 相減）。這就是為什麼 patch-diff 是 variant hunting 的引擎——公開的 fix 免費給了你一條「已驗證會抓到真 bug」的 query 模板。

---

## 對比：三種 diff 審計形態

```
                 精確度        成本          漏什麼                典型用途
git diff 範圍    低（含舊債）   最低          改動影響的未改檔      快速掃改動檔
baseline 掃描    高（只新增）   中（掃兩次）  大規模重排的指紋漂移  PR gate
patch variant    —（進攻性）    高（建兩 db） 依賴 query 抽象品質   找 0-day/漏修變體
```

三者對映不同意圖：範圍限定是**省時間**，baseline 是**精確 gate**，variant hunting 是**主動找洞**。它們共享同一個底層動作——「拿兩個狀態相減，只看差集」——差別在減什麼、為了找什麼。

---

## 踩雷集錦

**錯誤直覺一：只看 diff 那幾行有沒有問題就好。**
→ 正確認識：改動的影響不限於改動行。改一個型別、一個回傳值、一個 struct 欄位，可能讓**遠處**一個沒改的 `memcpy`/迴圈邊界變 OOB。diff 審計的正確姿勢是「以 diff 為起點追它的資料流/型別影響能到多遠」，不是「只讀 patch 的紅綠行」。對安全關鍵改動，要用全域 taint 追改動點的下游（接 Ch 22）。

**錯誤直覺二：baseline commit 隨便設哪個都行。**
→ 正確認識：baseline 設錯會讓 gate 失效或誤報。設太新（設成 PR 自己的 HEAD）→ diff 為空，什麼都不報，gate 形同關閉。設太舊或設到不相關的分支 → 把一堆本不屬於這次改動的東西都當「新增」報出來。baseline 應該是**這次改動的共同祖先**（PR 的 base branch merge-base），不是隨手一個 commit。

**錯誤直覺三：silent fix 都會有 CVE，掃 CVE 清單就抓得到。**
→ 正確認識：大量安全 bug 是在「重構」「健壯性改善」的 commit 裡**默默**修掉的，從來沒有 CVE、沒有公告、commit 訊息完全看不出安全含義。要抓 silent fix 得**看 diff 的形狀**（新增 bound check、換安全 API、加 NULL 檢查），不是等 CVE。這也是為什麼盯著大專案的 commit 流做 patch-diff 能撿到別人還不知道的洞。

**錯誤直覺四：patch 修好了，同類問題就都沒了。**
→ 正確認識：維護者通常只修**觸發到/被回報的那一個**實例，同樣的錯誤 pattern 常散落在別的函式沒被修（漏修變體）。這正是 variant hunting 的機會：把 fix 抽象成 query，掃出所有同類，其中沒被這次 patch 覆蓋的往往就是下一個 CVE。「修了一個」和「修完一類」是兩回事。

**錯誤直覺五：diff 很大也照 diff 審計就好。**
→ 正確認識：diff 大到某個程度（例如一次 merge 進來上萬行、或大重構），diff 審計就失去意義——「只審改動」的前提是改動小到可以聚焦。面對巨型 diff，要嘛拆成邏輯單元分批審，要嘛退回全量掃 + Ch 36 治理。硬把巨型 diff 當 diff 審計，你只是把全量掃的工作量偽裝成增量，還少了治理工具。

---

## 進階延伸

- **merge-base 的正確計算**：PR gate 的 baseline 該是 `git merge-base origin/main HEAD`（PR 分支與目標分支的共同祖先），而非 `HEAD~1`。用 `HEAD~1` 只在「PR 剛好一個 commit」時對；多 commit PR 或 rebase 過的分支，`HEAD~1` 會漏掉同 PR 前面幾個 commit 引入的問題。CI 腳本要顯式算 merge-base，這是 baseline gate 最常見的隱性 bug。
- **反向 diff 找 regression**：diff 審計通常找「新引入的 bug」，但反過來也有用——對比新舊版本的 query 命中，若某個**本來被修好的**命中在新版本又出現了（fix 被 revert 或被重構意外破壞），那是 security regression。把「舊版有、被 fix、新版又冒出來」當一個獨立訊號監控，能抓到「修好又壞掉」這類特別尷尬的洞。
- **N-day 到 0-day 的 variant 管線**：把 patch variant hunting 自動化——訂閱目標專案的 commit 流，每個看起來像 security fix 的 commit（diff 形狀啟發式）自動抽成 query、對最新版本掃變體。這是安全研究團隊撿洞的產線化形態，接 Ch 27 的 MRVA（一次對上千個 repo 跑同一 query）就能規模化到「一個 fix pattern 掃全 GitHub 的同類洞」。

---

## 本章重點整理

- diff 審計的核心信念：**多數時候只需關心改動及其影響能觸及的地方**，不必每次重掃全 codebase。三形態：PR gate、增量審計、patch variant hunting。
- 全量掃 vs baseline 掃的真跑對比：對 `~/audit-lab/ch38` 的 repo，全量掃報 2 個（舊+新），`--baseline-commit` 只報 1 個（新引入的 `app.c:3`），舊債被正確過濾——這就是 PR gate 要的精確性。
- baseline 靠**指紋相減**認出新增，不是行號 diff；指紋不穩（含絕對行號）會讓 baseline 漂移、把整檔當新增。
- 三形態是同一動作「兩狀態相減看差集」的不同用法：git diff 範圍限定（省時間）、baseline（精確 gate）、variant hunting（用 fix 前後兩版 query 相減找漏修變體）。
- patch variant hunting 是找 0-day 的主力：fix 洩漏漏洞資訊，同類 pattern 常有漏修的變體（`B`、`C`），把 fix 抽成 query 掃出來就是下一個 CVE。

## 自我檢核

- 為什麼「只審 diff 那幾行」是危險的？舉一個「改一行、遠處炸掉」的具體情境，你會怎麼追它的影響範圍？
- 全量掃和 baseline 掃對範例 repo 各報幾個命中？差在哪個命中、為什麼？這對 PR gate 有什麼實務意義？
- baseline commit 設成 PR 的 HEAD（而非 merge-base）會怎樣？設成不相關的舊 commit 又會怎樣？正確該設哪個？
- silent fix 為什麼掃 CVE 清單抓不到？你要看 diff 的什麼「形狀」才認得出它是安全修補？
- 解釋 variant hunting 的「兩版 query 相減」：pre-fix 命中 {A,B,C}、post-fix {B,C}，A/B/C 各代表什麼？哪個是你要找的？
- 什麼情況下 diff 審計「失去意義」、該退回全量掃 + 治理？為什麼硬做只是自欺？

## 延伸閱讀

- **Semgrep 官方 `--baseline-commit` / diff-aware scanning 文件**——本章 PR gate 真跑用的功能，它怎麼算 baseline、指紋怎麼比、CI 整合怎麼設。用法：照文件把本章的 baseline 掃描接進一個真的 GitHub Actions（練習 F 會做），注意 merge-base 的設定。前提：本章 + Ch 17 semgrep CI。
- **Ch 26《從 CVE/patch 到 query》**——variant hunting 的引擎：怎麼把一個 fix 的 pattern 抽象成一條會抓到同類的 query。用法：本章形態三只講方法論，要真的做 variant hunt 得先有那章的「patch → query」技能。前提：CodeQL 基礎。回頭接本章形態三。
- **GitHub Security Lab 的 variant analysis / patch-diff 部落格文章**——真實案例：研究者拿一個公開 fix 找出同專案（或跨專案）的漏修變體、拿到新 CVE 的完整過程。用法：看職業選手怎麼從一個 patch 推出一串洞，對照本章的「兩版相減」。前提：本章 + Ch 26。接 Ch 27 MRVA / Ch 43 案例。
- **`git merge-base` 與 `git diff` 的官方文件**——diff 審計的地基指令，尤其 merge-base 為什麼是 PR gate 的正確 baseline。用法：搞懂進階延伸講的「merge-base vs HEAD~1」陷阱，寫 CI 腳本前務必弄清。前提：git 基礎。

你已經能只審改動、能從一個 fix 找出一串漏修變體了。但這一路下來，我們用了 Semgrep 的命中、CodeQL 的 SARIF、動態驗證的結果——它們**格式各不相同**，散在各處。要把多工具、多階段的結果匯流成一張總圖、上傳到 GitHub、生成報告，需要一種通用格式。下一章我們深入 SARIF：它的結構、四工具怎麼輸出它、怎麼用 jq 合併去重、以及它接進的整個生態。

→ [Ch 39 SARIF 與生態整合](./39-sarif-ecosystem.md)
