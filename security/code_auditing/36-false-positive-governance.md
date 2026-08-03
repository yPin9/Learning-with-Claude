# Ch 36 — 誤報治理

> **目標**：把 triage 從「一個命中怎麼判」（Ch 12）升級成「幾百上千命中怎麼**規模化**治理」。你會學到 ranking、去重、分群、sink model 調校、baseline 增量、抑制標記管理這六個規模化武器，並用 SARIF + 一支 Python 腳本對真實命中做排序與去重。
> **環境**：WSL、semgrep 1.172.0、jq、python3。靶在 `~/audit-lab/ch36/`。

Ch 12 教你面對**一個**命中時怎麼走誤報三角、怎麼分級。那是「顯微鏡」。這一章是「望遠鏡」：當你對一個十萬行的專案跑完 CodeQL query suite 或 Semgrep registry，螢幕上吐出 1200 個命中，你**不可能**一個個走三角。逐個 triage 在這個量級是必敗——這不是態度問題，是算術問題。假設一個命中平均花你 2 分鐘，1200 個就是 40 小時，一整週。而且其中可能 90% 是同一批根因的翻版。

規模化治理的核心信念只有一句：**不要把命中當成一千個獨立問題，要把它們當成一個「命中集合」來做批次運算**。排序、去重、分群、批砍，都是在這個集合上做的操作。

---

## 為什麼「命中集合」要當資料來處理

先看清楚我們手上有什麼。一個現代 SAST 工具的輸出（不管是 CodeQL 的 SARIF、Semgrep 的 SARIF/JSON）本質上是一張表，每一列（一個命中）至少有這些欄位：

```
ruleId        觸發的規則      -> 決定「這是哪一類問題」
level/severity 工具給的嚴重度  -> 決定粗略優先序（但別全信，見踩雷）
location       檔案:行         -> 決定「在哪」「在不在攻擊面」
message        規則訊息        -> 人讀用
codeFlows     taint 路徑（若有）-> 決定 source→sink 可信度
fingerprint   穩定指紋（若有）  -> 決定「這個命中我之前判過沒」
```

一旦你把命中看成「一張有這些欄位的表」，規模化治理就變成對這張表做 **sort / group by / filter / dedup** 這幾個你在 SQL 或 pandas 裡再熟悉不過的操作。這就是本章的底層機制：**SARIF 是結構化資料，triage 決策是對它的查詢**。手工在 UI 裡一列列點，等於用手做本來該用腳本做的 group by。

---

## 底層機制：六個規模化武器

### 1. Ranking（排序）——決定你先看誰

你的時間是有限資源，命中順序決定你把時間投在哪。**不要按工具給的 severity 排**（下面踩雷會拆），要按一個綜合分數排。實務上這個分數是幾個因子的乘積或加權：

```
score ≈ exploitability × reachability × source 可信度 × sink 嚴重度 × 攻擊面權重
```

- **exploitability**：這類 bug 一般多好利用？（stack OOB write > info leak > 理論上的 DoS）。可拿 CWE 的 "Likelihood of Exploit" 當先驗。
- **reachability**：這條路徑真的從外部可達嗎？有 codeFlow 且 source 是真外部輸入的，排前面；source 是常數、測試 harness 的，往後丟。
- **source 可信度**：source 是 `recv()`/`argv`/HTTP body（高），還是某個內部 config 讀取（低）？
- **sink 嚴重度**：`memcpy`/`system`（高）vs `printf` 格式（中）。
- **攻擊面權重**：命中落在網路解析器（高）還是離線工具的 debug 路徑（低）？接 Ch 10 的攻擊面地圖，落在你標為 hot 的區塊就加權。

排序不是為了「不看低分的」，是為了**先把時間花在最可能是真 bug 的那 5%**。低分的留給批次處理。

### 2. 去重（dedup）——同根因多命中合併

同一個 bug 常會在工具輸出裡變成好幾個命中：

- 同一個未建模的 sanitizer 讓一整批本來安全的 flow 都被報。
- 一個 macro 展開在 20 個呼叫點，每個點各報一次。
- source→sink 之間有多條路徑，工具把每條路徑各報一列（CodeQL 的 path query 尤其會這樣）。

**去重的 key 要選對**。最粗的是 `(ruleId, sink location)`——同一個 sink 被同一規則報多次，多半是同一問題。更細的是把 source 也納入。CodeQL 的 SARIF 有 `partialFingerprints`，Semgrep 也有指紋，那是工具幫你算好的「穩定身分」，跨掃描仍相同——拿它當去重與「這我判過沒」的 key 最穩。

去重的價值不只是少看幾列。**合併成一個「根因」後，你判一次就結掉一整簇**——這是規模化的乘數效應。

### 3. 分群（grouping）——按 rule / 檔案 / pattern 批次處理

把命中 `group by ruleId`，你會看到分布極度不均：往往前三名規則佔了七成命中。這立刻告訴你：

- **某規則命中特別多** → 很可能是這規則在你的 codebase 上系統性誤報（例如它不認得你們自訂的 `safe_copy()`）。與其一列列判，不如去看**那條規則本身**要不要調（見武器 4）。
- **某檔案命中特別多** → 可能是個高風險解析器（值得全量精審），也可能是自動生成的碼（可整檔忽略）。

分群把「一千個命中」壓縮成「十幾個決策」：對每一群做一個「這群整體怎麼辦」的判斷，比對每個命中做判斷快兩個數量級。

### 4. Sink model 調校——降**整類**誤報

當分群顯示某規則系統性誤報，正確反應不是「一個個標 FP」，是**改規則的 sink/sanitizer model**。典型情況：你的 codebase 有個自訂 `validate_len()` 會 clamp 長度，但工具不認得，於是所有經過它的 `memcpy` 都被報。解法是把 `validate_len()` 加進 sanitizer 模型（Semgrep 的 `pattern-not`/`focus`，CodeQL 的 `isBarrier`），一次讓那整簇消失。

這是規模化治理和逐個 triage 最根本的差異：**逐個 triage 是消費命中，調 model 是修正產生命中的函數**。前者 O(n)，後者 O(1) 解掉一整類。代價是你要有能力改規則（Part 4/5 的技能），且改完要重掃驗證沒把真 bug 一起 barrier 掉。

### 5. Baseline——只看新增

對一個已經跑過一次的專案，你**不該每次都重判全部命中**。Baseline 的概念是：把某個 commit 的命中集合存下來當基準，之後只呈現**相對於 baseline 的新增命中**。舊債（pre-existing）先擱著，PR 只為它**新引入**的問題負責。這是 diff 審計（Ch 38）和 CI gate（Ch 17）的地基。

Baseline 的實作靠指紋：新掃描的每個命中算指紋，比對 baseline 集合，不在裡面的才是「新的」。選對指紋演算法很關鍵——若指紋含絕對行號，上面多加一行空行就讓整檔命中「看起來全是新的」，baseline 就廢了。

### 6. 抑制標記管理——判過的別再判

triage 的結論必須**落地成資料**，否則下次掃描你又從頭判一遍。三種落地方式：

- **inline 抑制**：`// nosemgrep: rule-id` 或 CodeQL 的 `lgtm[...]` 註解，寫在碼旁邊。優點是跟碼一起版本控制、reviewer 看得到；缺點是污染原始碼，且大量 inline 抑制會變成沒人敢動的「例外墳場」。
- **集中式抑制/baseline 檔**：把 dismiss 的指紋清單放一個檔（GitHub code scanning 的 dismiss 狀態、DefectDojo 的 finding 狀態）。優點是不污染碼；缺點是要工具支援、指紋要穩。
- **triage 紀錄**：不只「抑制」，還記**為什麼**（誰、何時、判定理由、是 FP 還是 accepted risk）。這條是 Ch 12 結尾強調的——**記錄理由**讓你不重工、能催生批砍、能餵報告。

---

## 範例一：對真實 SARIF 做排序 + 去重（真跑）

我們在 `~/audit-lab/ch36/` 建了三個檔模擬一個多命中專案：`net.c`（網路模組，多個 `strcpy`/`memcpy`/`system`）、`util.c`（工具函式，一個 `strcpy` + 一個安全 const memcpy）、`test_net.c`（測試目錄的雜訊）。規則檔 `rules.yaml` 有三條規則（`dangerous-strcpy`=WARNING、`unbounded-memcpy`=ERROR、`command-exec`=ERROR）。先跑出 SARIF：

```bash
cd ~/audit-lab/ch36
semgrep --config rules.yaml . --sarif -o gov.sarif -q
jq '[.runs[].results[]] | length' gov.sarif
# 6
jq -r '[.runs[].results[].ruleId] | group_by(.) | map("\(length)\t\(.[0])") | .[]' gov.sarif
```

分群輸出：

```
4       dangerous-strcpy
1       command-exec
1       unbounded-memcpy
```

光這一步 `group by` 就告訴你：六個命中裡四個是同一條規則。接著跑排序 + 去重腳本 `triage.py`（完整碼見附錄，重點是它從 SARIF 讀命中、依 `severity × 攻擊面` 算分排序、把 `test/` 檔降級、再按 ruleId 做根因分桶）：

```bash
python3 triage.py gov.sarif
```

真實輸出：

```
== ranked (high score first); test/ demoted ==
 35 ERROR    PROD unbounded-memcpy   net.c:6
 35 ERROR    PROD command-exec       net.c:7
 25 WARNING  PROD dangerous-strcpy   net.c:4
 25 WARNING  PROD dangerous-strcpy   net.c:5
 25 WARNING  PROD dangerous-strcpy   util.c:2
 20 WARNING  TEST dangerous-strcpy   test_net.c:2

== dedup: root-cause buckets (triage once, close many) ==
 4x  dangerous-strcpy   -> net.c:4, net.c:5, test_net.c:2, util.c:2
 1x  unbounded-memcpy   -> net.c:6
 1x  command-exec       -> net.c:7
```

三個決策取代六次 triage：

1. 兩個 ERROR（`net.c:6` OOB memcpy、`net.c:7` command exec）排最前，逐個精審。
2. `dangerous-strcpy` 是一簇四個。判其中一個是真的（`strcpy` 進 16-byte buffer），就知道其餘三個是同類——但要注意 `test_net.c:2` 是測試碼（已被降級為 TEST/20 分，可整檔忽略），`util.c:2` 的 dest 是 64-byte、風險較低。**同簇不等於同結論**，但同簇讓你「用一個判斷框架掃過一簇」而非各自從零。
3. 排序讓你先碰 ERROR，最後才碰測試雜訊。

這就是規模化的縮影：**六個命中 → 一次 group by + 一次排序 → 三個決策**。放大到 1200 個命中、幾十條規則，同樣的腳本讓你在半小時內看清「哪三條規則佔了七成」「哪些落在攻擊面」「哪些整簇是同根因」，而不是在 UI 裡滑一整天。

### 邊界失敗一：去重把兩個不同 bug 併掉

如果去重 key 只用 `ruleId`（不含 location），而某規則在兩個真正不同的 sink 各報一次，你會把它們併成一桶，結果判其中一個是 FP 就順手把另一個真 bug 也 dismiss 了。**去重 key 一定要含 location（至少檔案+函式）**，同 rule 不同位置不能盲併。上面腳本的桶只是「同 rule 聚在一起看」的視覺分群，仍逐一列出 location 供你逐個確認——這跟「把它們當成一個命中直接關掉」是兩回事。

### 邊界失敗二：baseline 指紋不穩

若 baseline 指紋含絕對行號，你在檔頭加一行 include，下面所有命中行號 +1，指紋全變，baseline 判定「全部都是新的」，PR 被幾百個舊債擋下。這是 diff 審計最常見的翻車（Ch 38 再深挖）。解法是用工具提供的 `partialFingerprints`（基於程式碼上下文而非行號）。

---

## 範例二：把高誤報規則降級而非砍掉（sink model 調校）

假設分群顯示 `dangerous-strcpy` 報了 40 個，你抽樣 5 個發現全部都先經過自訂的 `bounded_copy()`（會 clamp）——這是規則不認得你的 sanitizer。**錯誤反應**是把 `dangerous-strcpy` 整條關掉；**正確反應**是把 `bounded_copy` 加進規則的 barrier/`pattern-not`，讓經過它的命中消失、沒經過的仍報：

```yaml
rules:
  - id: dangerous-strcpy
    patterns:
      - pattern: strcpy($D, $S)
      - pattern-not-inside: |
          bounded_copy(...);
          ...
    message: unbounded strcpy not preceded by bounded_copy
    languages: [c]
    severity: WARNING
```

改完**必須重掃**：確認那 40 個掉到剩下真正沒經過 `bounded_copy` 的少數，而不是連真 bug 一起被 barrier 消音。這一步——「改 model → 重掃 → 驗證數字下降但沒歸零」——是規模化治理和「盲目關規則」的分水嶺。

---

## 團隊層面：誤報預算與規則品質度量

規模化治理到最後是**流程問題**，不只是腳本問題。幾個團隊層面的機制：

- **誤報預算（FP budget）**：規定一條規則若在你的 codebase 上 precision（真陽性 / 總命中）低於某門檻（例如 20%），就不准當 blocking gate，只能當 informational。這逼你用數據而非感覺決定哪些規則能擋 PR。
- **規則品質度量**：對每條規則統計 precision（要靠 triage 紀錄回填「這命中最後判真還假」）。長期低 precision 的規則要嘛修（調 model）、要嘛降級（改成不 block）、要嘛淘汰。
- **降級而非刪除**：一條規則就算目前誤報高，也可能偶爾抓到真 bug。與其刪掉，不如降到 informational tier——它還在跑、還在記錄，只是不擋 CI、不進主要 triage 佇列。這樣「藏在噪音裡的真 bug」不會被你連同噪音一起丟掉。

---

## 對比：Ch 12 逐個 triage vs 本章規模化治理

```
                    Ch 12 逐個 triage         Ch 36 規模化治理
處理單位            單一命中                   命中集合（一張表）
核心操作            走誤報三角/四問            sort / group by / dedup / filter
時間複雜度          O(n)                       批砍/調 model 讓一類 → O(1)
主要產物            這個命中真/假              哪些「類」要修/降級/忽略 + 紀錄
失敗模式            判得慢、判得累             併錯桶、baseline 漂移、盲關規則
接口                提供「判定理由」餵治理      靠指紋/紀錄避免重判
```

兩者不是取代關係。規模化治理**先**用排序/分群把命中壓成少數決策，**再**對排在前面、真正要人腦判的少數命中回頭用 Ch 12 的三角精審。望遠鏡先框範圍，顯微鏡再看細節。

---

## 踩雷集錦

**錯誤直覺一：某規則誤報高，直接整條關掉。**
→ 正確認識：高誤報規則裡可能藏著少數真 bug（它偶爾對）。盲關等於把真 bug 連噪音一起丟。正確做法是**降級**（改成不 block、進 informational tier）或**調 model**（加 barrier 讓誤報消失、真陽性保留），關掉是最後手段且要有數據支持。

**錯誤直覺二：命中太多，不去重逐個看就好，反正終究要看完。**
→ 正確認識：不去重你會把同一根因判 N 遍，且很可能第 3 遍就疲勞、後面全部草率 dismiss（triage 疲勞）。去重把 N 個同根因壓成一次判斷，是規模化的乘數效應，不是可有可無的優化。

**錯誤直覺三：triage 判完就好，不用記錄，反正我記得。**
→ 正確認識：下次掃描（或換人）會把你判過的全部重判。triage 紀錄（指紋 + 判定 + 理由）讓結論落地成資料，才有 baseline、才有規則 precision 度量、才有 diff 審計。不記錄的 triage 等於每次從零開始，永遠追不上命中增長。

**錯誤直覺四：按工具給的 severity 排序就對了。**
→ 正確認識：工具的 severity 是**規則作者對這類問題的先驗嚴重度**，不含你的 codebase 上下文——它不知道這個命中在不在攻擊面、source 可不可控、path 可不可達。一個 ERROR 級規則命中在離線 debug 工具的死路徑上，遠不如一個 WARNING 級命中落在網路解析器上值得先看。排序要用綜合分數（含 reachability、攻擊面權重），severity 只是其中一個因子。

**錯誤直覺五：去重就是把同規則的命中全部併成一個關掉。**
→ 正確認識：同規則不同 location 可能是不同 bug，甚至一個真一個假。去重的 key 必須含 location；「分桶」是為了**用同一框架逐個快速掃過一簇**，不是把整簇當一個命中一鍵處理。盲併會漏掉真 bug（見範例一邊界失敗一）。

---

## 進階延伸

- **precision 隨時間漂移**：codebase 演化會讓規則 precision 變動——新加的 framework 引入工具不認得的 pattern，本來乾淨的規則開始噴誤報。成熟團隊會**定期重算規則 precision**（靠累積的 triage 紀錄），把它當規則的健康指標監控，而非設定一次就不管。
- **抽樣估計整類**：命中上萬時連分級都做不完。統計做法是每一類**隨機抽 k 個**判，用抽樣的真陽性比例估計整類的 precision，再決定「這類整體怎麼辦」。這把「全量 triage」的成本從 O(n) 降到 O(類數 × k)，代價是接受抽樣誤差——對低攻擊面區塊完全可接受，對 hot 區塊仍要全量。
- **triage 資料 → 訓練排序模型**：累積夠多「命中 + 最終判定」的紀錄後，可以拿它當標註資料訓練一個排序器（哪些特徵預測真陽性），讓 ranking 從手調權重進化成學出來的權重。這是工業界 SAST 平台在做的事，但**慎防過擬合到歷史**——它會學會你過去的盲點。

---

## 附錄：triage.py（真跑用的排序 + 去重腳本）

```python
#!/usr/bin/env python3
import json, sys, re

sarif = json.load(open(sys.argv[1]))
SEV = {"ERROR": 3, "WARNING": 2, "INFO": 1}
rows = []
for run in sarif["runs"]:
    # semgrep 把嚴重度放在 rule 的 defaultConfiguration，不是每個 result
    rule_sev = {}
    for rule in run["tool"]["driver"].get("rules", []):
        rule_sev[rule["id"]] = rule.get(
            "defaultConfiguration", {}).get("level", "warning")
    for r in run["results"]:
        loc = r["locations"][0]["physicalLocation"]
        uri = loc["artifactLocation"]["uri"]
        line = loc["region"]["startLine"]
        rid = r["ruleId"]
        sev = (r.get("level") or rule_sev.get(rid, "warning")).upper()
        surface = 0 if re.search(r"(^|/)test", uri) else 1  # test/ 降級
        score = SEV.get(sev, 1) * 10 + surface * 5
        rows.append({"rule": rid, "uri": uri, "line": line,
                     "sev": sev, "surface": surface, "score": score})

buckets = {}
for row in rows:
    buckets.setdefault(row["rule"], []).append(row)

print("== ranked (high score first); test/ demoted ==")
for row in sorted(rows, key=lambda x: -x["score"]):
    tag = "TEST" if row["surface"] == 0 else "PROD"
    print("{:>3} {:<8} {} {:<18} {}:{}".format(
        row["score"], row["sev"], tag, row["rule"], row["uri"], row["line"]))

print("\n== dedup: root-cause buckets (triage once, close many) ==")
for rule, v in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
    locs = ", ".join("{}:{}".format(x["uri"], x["line"]) for x in v)
    print("{:>2}x  {:<18} -> {}".format(len(v), rule, locs))
```

---

## 本章重點整理

- 規模化治理的核心信念：**不要把命中當一千個獨立問題，要當一個「命中集合」做批次運算**。逐個 triage 在千級命中是算術上的必敗。
- SARIF 是結構化資料，triage 決策等於對它做 **sort / group by / dedup / filter**。手工在 UI 一列列點，是用手做該用腳本做的 group by。
- 六個武器：**ranking**（綜合分數而非工具 severity）、**去重**（key 含 location，同根因合併）、**分群**（group by rule/檔案，壓成少數決策）、**sink model 調校**（改產生命中的函數，O(1) 解一類）、**baseline**（只看新增，靠穩定指紋）、**抑制紀錄**（結論落地成資料）。
- 逐個 triage（Ch 12）與規模化治理是望遠鏡+顯微鏡：先分群排序框範圍，再對前排少數用三角精審。
- 團隊層面靠**誤報預算**（precision 低就不准 block）和**規則 precision 度量**（靠 triage 紀錄回填）把治理從個人技藝變成流程。

## 自我檢核

- 為什麼「按工具 severity 排序」在規模化 triage 裡是次優的？綜合排序分數還要納入哪些因子，各解決什麼盲點？
- 去重的 key 若只用 `ruleId`（不含 location）會出什麼事？舉一個「盲併漏掉真 bug」的具體情境。
- 分群（group by rule）顯示某規則佔了七成命中，你的下一步不該是逐個判，而該做什麼？列出兩種可能的整群處置及各自的驗證動作。
- 「調 sink model」和「逐個標 FP」在時間複雜度上差在哪？調完 model 為什麼**必須**重掃？不重掃可能出什麼錯？
- baseline 為什麼一定要用穩定指紋？若指紋含絕對行號，加一行空行會發生什麼？這跟 Ch 38 的 diff 審計怎麼接？
- 你的團隊決定「precision < 20% 的規則不准當 blocking gate」。要落實這條，你得先有什麼資料？這條資料從哪來（回想 Ch 12 結尾）？

## 延伸閱讀

- **SARIF 2.1.0 規格中 `result.partialFingerprints` 與 `automationDetails` 章節**——去重與 baseline 的官方指紋機制。用法：想搞懂「跨掃描怎麼認出是同一個命中」時讀這兩節，它定義了穩定身分的計算方式。前提：Ch 39 會先帶你看 SARIF 全貌，讀完再回來看指紋更有感。
- **GitHub code scanning 的 "About code scanning alerts" / dismiss 與 baseline 文件**——工業級平台怎麼呈現、dismiss、baseline 命中。用法：對照本章六武器，看真實平台把哪些做成 UI 功能（尤其 dismiss 狀態如何持久化）。前提：本章；GitHub 上傳部分接 Ch 39。偏產品操作。
- **Semgrep 官方 "Managing findings" / `--baseline-commit` 文件**——baseline 增量掃描的實作細節與陷阱。用法：Ch 38 diff 審計會直接用到，先讀它了解 baseline 怎麼算「新增」。前提：Ch 17 semgrep CI。接 Ch 38。
- **《Building Secure and Reliable Systems》（Google SRE 系列）中談 SAST 規模化與誤報預算的章節**——大型組織怎麼把 SAST 治理做成流程與度量。用法：想把本章從「個人腳本」提升到「團隊機制」（誰負責調 model、precision 門檻怎麼定）時讀。前提：本章；偏工程管理視角。

你現在能把幾千個命中壓成幾十個決策了——但這些決策裡排最前的「高信命中」，還只是**靜態工具說它可疑**。要從「可疑」變「確認是真 bug」，光靠讀碼判斷不夠，得動態驗證：把它接到 fuzzer 或符號執行，讓它真的觸發一次。下一章我們對 `~/audit-lab` 的 memcpy OOB 命中做這件事，跑出真正的 ASan crash，閉合「靜態懷疑 → 動態確認」的環。

→ [Ch 37 靜態 + 動態驗證](./37-static-plus-dynamic.md)
