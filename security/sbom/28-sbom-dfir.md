# Ch 28 — SBOM 與 DFIR / 藍隊

> **目標**：搞清楚 SBOM 在事件回應（IR）裡的角色——不是理論，是操作層面的差距。讀完這章，你能設計出一個「CVE 爆發 → 幾分鐘內找到所有受影響實例」的流程，以及說清楚這個流程的前提和限制。

## 為什麼需要這個？

2021 年 12 月 9 日，Log4Shell 在 Twitter 上洩出的那個下午，全世界的資安工程師同時面對一個問題：

**「我的系統裡到底有沒有 log4j 2.x？在哪裡？」**

有些人幾分鐘就有答案。大部分人花了三天，甚至幾週。還有一些組織在幾個月後仍然不確定。

Ch 1 用這個事件說明為什麼需要 SBOM。這章要反過來深挖：**如果你真的已經建好 SBOM 資產庫，那個週末的流程長什麼樣？沒有的話又是什麼地獄？** 差距不只是時間，而是整個 incident response 的品質——你能做 VEX 分流嗎？你能按暴露面排優先順序嗎？你的答案可信嗎？

SBOM 在藍隊的價值不是預防，是**在事件發生後加速一切判斷**。

## 先建立直覺

用兩條流程對照：

```
【沒有 SBOM 資產庫的 IR】

CVE-2021-44228 公開
    │
    ▼
「我們哪些系統有 log4j？」
    │
    ▼
    ├── Slack 問各個 team lead：「你們有用嗎？」
    │       ├── Team A：「我不確定，要問離職的 Bob」
    │       ├── Team B：「有，好像是 2.13？讓我查一下」
    │       └── Team C：「我們是 Spring Boot，有沒有不知道」
    │
    ├── 有人開始 grep pom.xml、build.gradle、package.json
    │       └── 找到直接依賴，但傳遞依賴呢？
    │
    ├── 有人開始 docker exec 進容器找 log4j*.jar
    │       └── fat jar / uber jar 裡的 log4j 根本找不到
    │
    └── 48 小時後：「大概摸清楚了，但不保證沒漏掉的」
            → 優先順序混亂，不知道從哪個服務開始修
            → 高管問「我們安全嗎？」無法給確定的答案


【有 SBOM 資產庫（Dependency-Track）的 IR】

CVE-2021-44228 公開
    │
    ▼
Dependency-Track 自動比對 NVD/OSV
    │
    ▼
    「受影響的 project 清單」（幾分鐘自動生成）
        ├── service-auth@2.3.1：log4j-core 2.14.1 → CRITICAL
        ├── backend-api@1.0.5：log4j-core 2.15.0 → CRITICAL
        ├── legacy-worker@0.8：log4j-core 1.2.17 → 不同 CVE，要另查
        └── frontend、service-user：無 log4j → 不受影響
    │
    ▼
VEX 分流（手動或自動）
    ├── backend-api 用了 log4j 但沒有 JNDI lookup code path
    │   → 標 not_affected，排除
    └── service-auth、legacy-worker 確認受影響 → 排 patch 優先順序
    │
    ▼
EPSS + CVSS + 暴露面 → 排序 → 通知 SRE → patch / deploy
```

整個流程從「48 小時混亂」壓到「幾分鐘有清單、幾小時有行動」。

這不是假設情境。美國 CISA 在 Log4Shell 事件後的指引明確提到，擁有維護良好 SBOM 的組織在清點暴露面的速度上，與沒有 SBOM 的組織之間存在巨大差距。

## Log4Shell 教給我們的事

三個具體教訓，不是泛論：

**第一：傳遞依賴是真正的問題所在**

很多被打到的組織聲稱「我們沒有用 log4j」。結果是 log4j 藏在他們用的 Elasticsearch、Apache Solr、VMware vCenter 等軟體的傳遞依賴裡。他們說「沒有用」是因為他們沒有在自己的 pom.xml 寫 log4j——但依賴的依賴帶進來了。

SBOM 包含完整的傳遞依賴圖（Ch 2 有詳細解釋），所以它能找到這些藏在第三、第四層的元件。人工 grep 只找得到直接依賴。

**第二：fat jar / uber jar 讓 grep 失效**

很多 Java 應用打包成 uber jar（把所有依賴打包進一個 jar 裡），或用 shaded jar 重命名 package。這時候你在 container 裡找 `log4j*.jar` 找不到——它被重命名成 `com.mycompany.shaded.log4j` 藏在 uber jar 裡。

syft 能辨識這種情況（透過分析 jar 的 MANIFEST.MF 和 class file），但 `find . -name "log4j*.jar"` 什麼也找不到。Log4Shell 爆發時，這讓很多人誤以為「我們沒有 log4j」。

**第三：「問同事」的資訊不可靠**

人記得自己寫的直接依賴，但記不住框架帶進來的傳遞依賴。三個月前的某個 PR 改了一個版本，傳遞依賴的圖就變了——沒有人記得這件事。人腦不是可靠的 SBOM。

## SBOM 在 incident response 的角色

把 SBOM 在 IR 裡的作用結構化：

```
CVE 爆發
  │
  ▼
Step 1: 影響面清點（SBOM 資產庫查詢）
  「哪些 project 的哪些版本含受影響元件？」
  → Dependency-Track API / grype 批次掃描
  → 幾分鐘，而不是幾天
  │
  ▼
Step 2: VEX 分流
  「哪些技術上含受影響元件，但實際上不在可被利用的 context？」
  → 標 not_affected，縮減真正需要 patch 的清單
  → Ch 16 有詳細說明 VEX 的作用
  │
  ▼
Step 3: 優先順序
  「剩下真正受影響的，哪個先修？」
  → CVSS（嚴重度）× EPSS（可被利用機率）× 暴露面（公網？內網？）
  → 暴露在公網的 CRITICAL 先；離線的 legacy-worker 後
  │
  ▼
Step 4: patch / mitigate / 追蹤
  「修了哪些？還剩哪些？進度是什麼？」
  → Dependency-Track 的 Finding 狀態追蹤
  → 不要靠 Slack thread 追
```

每一步都依賴前一步。Step 1 如果要花三天，整個後面的步驟就是在打地基的同時還要同時應急，混亂是必然結果。

## Dependency-Track 當 IR 引擎

Dependency-Track（D-T）在 Ch 17 講過它的基本操作。這裡聚焦它在 IR 時的具體作用。

**持續監控，不是點查**

D-T 的設計是讓你把所有產品的 SBOM 上傳進去，它持續在背景把每個元件比對 NVD、OSV、GitHub Advisory 等漏洞資料庫。一旦有新的 CVE 進資料庫，D-T 自動找出哪些 project 含受影響元件，不需要人去觸發。

這是 IR 能快的關鍵：漏洞資料庫更新的瞬間，你就有清單，而不是等工程師手動跑一遍。

**查詢 API：Log4Shell 那晚的實際操作**

D-T 提供 REST API，可以這樣查：

```bash
# 查詢哪些 project 含有某個元件（component name + version）
curl -H "X-Api-Key: ${DT_API_KEY}" \
  "https://dependencytrack.example.com/api/v1/component/search?query=log4j-core&pageNumber=1&pageSize=100"

# 取得某個 CVE 的所有 findings（哪些 project 被影響）
curl -H "X-Api-Key: ${DT_API_KEY}" \
  "https://dependencytrack.example.com/api/v1/finding?source=NVD&id=CVE-2021-44228"
```

Log4Shell 那個週末，有跑 D-T 的組織就是這樣操作：丟一個 CVE 編號，拿回一個清單。清單裡的每個 project 都有版本、上次更新時間、可以連到對應的 Finding 細節。

**Webhook 觸發自動工單**

D-T 支援 Webhook 通知。可以設定：新 CRITICAL 漏洞 → 自動開 Jira ticket → 指派給對應的 team。這樣就不需要有人盯著 D-T 的 dashboard，漏洞進資料庫的那一分鐘，team 就收到 ticket。

## SBOM + VEX 的 IR 分流實操

VEX 在 IR 裡的價值不是「宣稱我們沒問題」，而是讓真正需要修的清單縮小到可以行動的規模。

Log4Shell 的案例：CVSS 10.0，影響所有 log4j 2.x。技術上，所有含 log4j 2.0–2.14.1 的系統都「受影響」。但實際上：

- **用了 log4j 但完全沒有 JNDI lookup code path 的應用**：攻擊面不存在，理論上是 `not_affected`
- **用了 log4j 但沒有暴露任何會把 user input 記 log 的 endpoint**：攻擊面極小
- **用了 log4j 2.15.0 至 2.16.0**：Log4Shell 的初版 patch，但後來又發現後續 CVE（CVE-2021-45046），需要繼續追

VEX 的分流流程：

```bash
# 假設你有所有受影響 project 的清單
# Step 1：確認哪些確定 not_affected
#   → 靠人工 code review / 靠 SAST 找 JNDI lookup 呼叫鏈
#   → 在 D-T 裡把這些 Finding 標 NOT_AFFECTED，附理由

# Step 2：剩下 affected 的，按暴露面排序
#   Public API (暴露 0.0.0.0)    → 最優先
#   Internal API (內網 only)     → 第二優先
#   Batch job (沒有 HTTP 入口)   → 第三優先
#   Offline 系統                 → 最後
```

沒有 VEX 的話，你的 patch 清單可能是 200 個 project，工程師會被淹沒。用 VEX 分流後，真正需要緊急處理的可能縮減到 30 個，剩下的排計劃性升版。

## 示範：幾秒鐘查出 log4j

這個示範重現「有 SBOM 的情況下，查一個元件是幾秒的事」。

**建一個測試 SBOM（含 log4j-core 2.14.1）**

```bash
wsl.exe -d Ubuntu -e bash -lc '
cat > /tmp/app.cdx.json << '"'"'EOF'"'"'
{
  "bomFormat": "CycloneDX",
  "specVersion": "1.4",
  "version": 1,
  "metadata": {
    "timestamp": "2021-12-09T00:00:00Z",
    "component": {
      "type": "application",
      "name": "my-java-app",
      "version": "2.3.1"
    }
  },
  "components": [
    {
      "type": "library",
      "name": "log4j-core",
      "version": "2.14.1",
      "group": "org.apache.logging.log4j",
      "purl": "pkg:maven/org.apache.logging.log4j/log4j-core@2.14.1",
      "licenses": [{"license": {"id": "Apache-2.0"}}]
    },
    {
      "type": "library",
      "name": "log4j-api",
      "version": "2.14.1",
      "group": "org.apache.logging.log4j",
      "purl": "pkg:maven/org.apache.logging.log4j/log4j-api@2.14.1",
      "licenses": [{"license": {"id": "Apache-2.0"}}]
    },
    {
      "type": "library",
      "name": "spring-boot",
      "version": "2.6.1",
      "group": "org.springframework.boot",
      "purl": "pkg:maven/org.springframework.boot/spring-boot@2.6.1",
      "licenses": [{"license": {"id": "Apache-2.0"}}]
    },
    {
      "type": "library",
      "name": "jackson-databind",
      "version": "2.13.0",
      "group": "com.fasterxml.jackson.core",
      "purl": "pkg:maven/com.fasterxml.jackson.core/jackson-databind@2.13.0",
      "licenses": [{"license": {"id": "Apache-2.0"}}]
    }
  ]
}
EOF
echo "SBOM 建好了"
'
```

**用 jq 查詢：Log4Shell 那晚的操作**

```bash
wsl.exe -d Ubuntu -e bash -lc '
# 查 log4j 相關的所有元件
jq '"'"'.components[] | select(.name | test("log4j"; "i")) | {name, version, purl}'"'"' /tmp/app.cdx.json
'
```

預期輸出：

```json
{
  "name": "log4j-core",
  "version": "2.14.1",
  "purl": "pkg:maven/org.apache.logging.log4j/log4j-core@2.14.1"
}
{
  "name": "log4j-api",
  "version": "2.14.1",
  "purl": "pkg:maven/org.apache.logging.log4j/log4j-api@2.14.1"
}
```

確認：log4j-core 2.14.1 在這個應用裡，這是 CVE-2021-44228 的受影響版本（2.0 到 2.14.1）。整個查詢花了不到一秒。

**更快的版本：直接 grep**

```bash
wsl.exe -d Ubuntu -e bash -lc 'grep -i "log4j" /tmp/app.cdx.json'
```

這個在你不需要結構化輸出的時候夠用，幾秒出答案。

**用 grype 確認漏洞**

```bash
wsl.exe -d Ubuntu -e bash -lc '
export PATH="$HOME/bin:$PATH"
grype sbom:/tmp/app.cdx.json --only-fixed 2>/dev/null | head -20
'
```

grype 會比對這個 SBOM 的所有元件對漏洞資料庫，CVE-2021-44228 會出現在結果裡，對應到 log4j-core 2.14.1，CVSS 10.0 CRITICAL。

這就是那個週末「有 SBOM 的人」的操作：一個命令，幾秒，確認中了，開始行動。「沒有 SBOM 的人」在同一個時間翻 git history。

**對多個 SBOM 批次查**

如果你有 100 個服務各自有一份 SBOM，批次掃也很簡單：

```bash
wsl.exe -d Ubuntu -e bash -lc '
for f in /tmp/sboms/*.cdx.json; do
  result=$(jq -r ".components[] | select(.name | test(\"log4j\"; \"i\")) | \"\(.name)@\(.version)\"" "$f" 2>/dev/null)
  if [ -n "$result" ]; then
    echo "=== $f ==="
    echo "$result"
  fi
done
'
```

這個腳本把「哪些服務含 log4j」的問題掃完 100 個 SBOM 也不過幾秒。

## 連結到更完整的 DFIR 流程

這章聚焦 SBOM 在 IR 裡的角色。但 DFIR 是一個更大的主題：記憶體取證、EDR 偵測、log 分析、事件時間軸重建——這些都超出 SBOM 的範疇。

本 repo 的 `../blue_team_dfir/` 課（`../blue_team_dfir/README.md`）完整涵蓋藍隊 DFIR 全流程，包括：

- Volatility3 記憶體分析（找 in-memory payload）
- Sysmon / ETW / Sigma 規則的 Detection Engineering
- Windows 事件日誌分析（持久化機制偵測）
- 網路流量分析（Zeek、Suricata、beacon 偵測）
- CloudTrail / GuardDuty 雲端 IR

SBOM 在那整個流程裡扮演「影響面清點」的角色，是 IR 的第一步，後面的調查需要 DFIR 課的工具和方法。

## 對比與取捨

| 維度 | 有 SBOM 資產庫 | 沒有 SBOM 資產庫 |
|------|---------------|----------------|
| 發現受影響實例的時間 | 幾分鐘（自動比對） | 幾天（人工清點） |
| 人力需求 | 低（主要靠工具） | 高（需要問遍所有 team） |
| 資訊準確度 | 高（機器掃的，含傳遞依賴） | 低（人腦記憶，漏傳遞依賴） |
| 能否支援 VEX 分流 | 能（D-T 原生支援，把 not_affected 標出） | 不能（連清單都不完整，遑論分流） |
| 高管報告的可信度 | 可提供完整數字和進度 | 只能說「我們還在清點」 |
| fat jar 裡的隱藏依賴 | 能找到（syft 在生成時就解開） | 找不到（grep jar 名稱失效） |

這個表格說明一件事：沒有 SBOM 資產庫，IR 的品質從根本上就被限制了——不只是慢，而是不可靠。

## SBOM 的 DFIR 限制（誠實面）

上面說的好處有一個前提：**SBOM 必須事前建好、持續維護、且夠準確**。這個前提在實際場景裡沒那麼容易達到。

**問題一：事後補做的 SBOM 沒有意義**

最常見的錯誤：事件發生了，才臨時跑 syft 對現有 image 掃一份 SBOM。

這個 SBOM 告訴你的是「現在這個 image 有什麼元件」，不是「事件發生時的 image 有什麼元件」。如果攻擊者已經進來，他可能已經修改了 image、刪了痕跡、升了某些元件版本。你掃出來的 SBOM 不能代表事件發生時的狀態。

更根本的問題：如果系統沒有和每次 release 綁定生成 SBOM，你根本不知道事件發生時部署的是哪個版本的哪些元件。事後跑 syft 補的 SBOM，是對現在的快照，不是對事件時間點的快照。

SBOM 的力量在**預先建立資產庫**，每次 release 生一份、簽章（Ch 21）、上傳 D-T，不是事後補做。

**問題二：SBOM 沒接 D-T，等於白存**

很多組織已經在 CI 裡生 SBOM，但只是把 JSON 丟到 artifact 倉庫就結束了。沒有接 Dependency-Track，沒有持續比對漏洞資料庫。

結果是：SBOM 存在，但在 CVE 爆發時還是要人工把 SBOM 撈出來、手動跑 grype。比沒有強一點，但失去了「自動通知」的核心價值——你不會在漏洞進 DB 的那一分鐘收到警報，還是要等有人想到「去查一下」。

**問題三：SBOM 的準確度依賴工具的能力和 build 時機**

syft 的掃描不是完美的。Ch 12（SBOM 品質）和 Ch 10（syft internals）有詳細說明盲點。幾個常見的問題：

- 動態載入的元件（Java 用 `Class.forName()` 在 runtime 載入的 jar）不會出現在靜態掃的 SBOM
- 某些 fat jar 的元件識別不完整
- 非標準的依賴管理方式（自己 vendor 的 C 函式庫、手動複製進去的 .js 檔）可能被漏掉

這代表「SBOM 裡沒有」不等於「系統裡確實沒有」。SBOM 的準確度是有上限的，在 IR 時要保持這個認知。

**問題四：如果 SBOM 沒有簽章，可信度打折**

如果你的 SBOM 沒有和 release artifact 綁定並簽章（Ch 21），在 IR 時有一個問題：這份 SBOM 是不是真的對應到那個版本的 image？有沒有被竄改過？

在事件調查裡，可信度很重要。你需要能向審查者證明「這份 SBOM 確實是在 2021-12-09 14:30 的那個 build 生出來的，沒有被事後修改」。沒有簽章的 SBOM 無法提供這個保證。

## 踩雷集錦

**1. 事後跑 syft 補 SBOM，以為這樣就能回答「我們有沒有受影響」**

已經說了，但值得再強調：你掃出來的是現在的狀態，不是事件時間點的狀態。如果 IR 已經開始，現在 image 的狀態可能已經被改過（修復了、被攻擊者改了、或只是日常部署更新了版本）。事後補做的 SBOM 對 IR 幾乎沒有幫助，對這次事件的影響面分析是不可信的。

**2. 有 D-T 但沒有設 Webhook 通知，結果 CVE 進 DB 沒人知道**

D-T 的核心價值是「持續監控 + 自動通知」。如果你只是把 SBOM 上傳到 D-T，但沒有設定 Webhook 觸發 Jira / Slack 通知，那麼當新 CVE 進 DB 的時候，沒有人會知道。你的「幾分鐘發現」能力依賴有人主動去看 D-T 的 dashboard，而不是被推送通知。這等於把主動監控退化成被動點查。

**3. Log4Shell 時用 `find . -name "log4j*.jar"` 找——很多系統什麼也找不到，誤以為沒問題**

fat jar / uber jar 把所有依賴打包進一個 jar，裡面的 log4j 不是一個獨立的 `log4j-core-2.14.1.jar`，而是被 explode 進去的 class file。你的 `find` 命令找不到 log4j 開頭的 jar，但系統裡確實有 log4j 的 code 在跑。

此外，有些框架（如 Spring Boot 的 executable jar）用嵌套 jar 格式，log4j 的 jar 在另一個 jar 裡面。標準的 `find` 根本進不去。用 syft 生 SBOM 才能正確識別這種情況，因為 syft 會遞迴解析 jar-in-jar。

**4. VEX 標錯 not_affected，然後那個系統真的被打**

VEX 的 `not_affected` 判斷需要實際的 code review 或 SAST 分析，不是靠直覺。Log4Shell 的案例裡，有些工程師判斷「我們的 log 不會包含 user input」，標成 not_affected，但後來發現有某個 middleware 在 request 進來時會 log User-Agent——這就是攻擊面。VEX 判斷如果粗糙，會讓你把真正受影響的系統從清單上移除。不確定時寧可 patch，不要輕易標 not_affected。

## 進階：再往深一層

**EPSS：比 CVSS 更好的優先順序工具**

CVE-2021-44228 是 CVSS 10.0，但不是所有 CVSS 10.0 的 CVE 都值得當天晚上出動。EPSS（Exploit Prediction Scoring System）估算漏洞在未來 30 天內被實際利用的機率，考慮了攻擊工具的可用性、漏洞的技術特性、歷史 exploit 活動等因素。

IR 時，把 CVSS 和 EPSS 搭配使用：

- CVSS 高 + EPSS 高 → 確定優先，這週內要修好
- CVSS 高 + EPSS 低 → 計劃性升版，不需要緊急出動
- CVSS 中 + EPSS 高 → 注意，可能比看起來危險

FIRST.org 提供 EPSS 的 API 查詢，可以在 IR 的清單裡加上 EPSS 分數輔助排序。

**部署 SBOM 的 IR 應用**

Ch 3 介紹過幾種 SBOM 類型，其中「Deployed SBOM」（描述特定機器或環境上實際部署了什麼版本）在 IR 時特別有價值。生成時間的 SBOM 告訴你「build 時有什麼元件」，Deployed SBOM 告訴你「現在線上跑的是什麼版本」。

在有自動部署的環境裡，這兩者理應一致，但不一定。有些組織在 deploy 時也觸發 syft 掃 running container 生一份 Deployed SBOM，這樣 IR 時可以確認「線上跑的和 build 出來的一致嗎」，作為完整性驗證的一環。

**SBOM 作為數位鑑識證據**

在嚴重事件的調查裡，SBOM 可以成為時間軸重建的輔助工具。例如：你有一份在事件發生前三天生成並簽章的 SBOM，你現在掃 container 發現多了一個元件。這個差異可能意味著：

1. 在事件期間有人改了 image（可疑）
2. 在那三天有一次正常部署（需要看 deploy log 確認）

SBOM 不能單獨做鑑識，但結合 release 記錄、deploy log、簽章時間戳，可以幫助重建「什麼時候、什麼東西、被誰部署」的時間軸。這是為什麼 Ch 21 強調簽章和 attestation 在嚴肅的安全環境裡不是可選的。

## 動手練習

這兩個練習模擬 Log4Shell 那個週末的操作，真實跑過一遍比看十遍說明更有感。

**練習 1：建 SBOM 並用 jq 查詢**

```bash
# Step 1：建測試 SBOM（含 log4j-core 2.14.1）
wsl.exe -d Ubuntu -e bash -lc '
cat > /tmp/ir-demo.cdx.json << '"'"'EOF'"'"'
{
  "bomFormat": "CycloneDX",
  "specVersion": "1.4",
  "version": 1,
  "metadata": {
    "timestamp": "2021-12-09T00:00:00Z",
    "component": {"type": "application", "name": "my-java-app", "version": "1.0.0"}
  },
  "components": [
    {
      "type": "library",
      "name": "log4j-core",
      "version": "2.14.1",
      "group": "org.apache.logging.log4j",
      "purl": "pkg:maven/org.apache.logging.log4j/log4j-core@2.14.1"
    },
    {
      "type": "library",
      "name": "spring-boot",
      "version": "2.6.0",
      "group": "org.springframework.boot",
      "purl": "pkg:maven/org.springframework.boot/spring-boot@2.6.0"
    }
  ]
}
EOF

# Step 2：查詢 log4j
echo "=== jq 查詢 ==="
jq '"'"'.components[] | select(.name | test("log4j"; "i")) | {name, version, purl}'"'"' /tmp/ir-demo.cdx.json

# Step 3：快速 grep 版本
echo "=== grep 快查 ==="
grep -i "log4j" /tmp/ir-demo.cdx.json
'
```

**練習 2：用 grype 掃 SBOM，確認 CVE-2021-44228**

```bash
# grype 需要先安裝（Ch 0 / Ch 15 有說明）
wsl.exe -d Ubuntu -e bash -lc '
export PATH="$HOME/bin:$PATH"
echo "=== grype 掃描 log4j CVE ==="
grype sbom:/tmp/ir-demo.cdx.json 2>/dev/null | grep -i "log4j\|CVE-2021-44228\|CRITICAL" | head -20
echo ""
echo "如果看到 CVE-2021-44228 對應 log4j-core 2.14.1，代表流程正確。"
'
```

預期在 grype 輸出裡看到 `CVE-2021-44228` 對應 `log4j-core 2.14.1`，SEVERITY 是 `Critical`。

如果 grype 還沒裝，可以用 Ch 0 的安裝步驟，或直接看練習 1 的 jq 查詢——那個已經示範了「有 SBOM 就能秒查」的核心概念。

## 本章重點整理

- SBOM 在 IR 裡的核心價值是**把「找出受影響系統」從幾天壓到幾分鐘**，不是預防漏洞，而是加速回應。
- Log4Shell 案例的三個教訓：傳遞依賴是真正問題、fat jar 讓 grep 失效、人腦記不住依賴圖。
- IR 的四個步驟：影響面清點（SBOM 資產庫）→ VEX 分流（排除 not_affected）→ 優先順序（CVSS × EPSS × 暴露面）→ patch 追蹤。
- Dependency-Track 提供持續監控 + 新 CVE 自動通知，是 SBOM 資產庫的核心工具。
- SBOM 有四個真實限制：事後補做無意義、沒接 D-T 等於白存、掃描工具有盲點、沒有簽章可信度打折。
- 這章的 DFIR 聚焦影響面清點；記憶體分析、EDR、log 分析等完整藍隊流程見本 repo `../blue_team_dfir/` 課。

## 自我檢核

- [ ] 我能解釋 Log4Shell 案例裡「有 SBOM 資產庫」和「沒有 SBOM 資產庫」的流程差距
- [ ] 我知道 fat jar / uber jar 為什麼讓 `find . -name "log4j*.jar"` 失效
- [ ] 我能說出 IR 的四個步驟，以及每個步驟 SBOM/VEX 在哪裡起作用
- [ ] 我理解「事後補做的 SBOM 在 IR 裡幾乎無用」的原因
- [ ] 我能用 jq 對一個 CycloneDX JSON 查詢特定元件
- [ ] 我知道為什麼 SBOM 資產庫沒接 D-T Webhook 會讓主動監控退化成被動點查
- [ ] 我理解 EPSS 和 CVSS 搭配使用在 IR 優先排序上的優勢
- [ ] 我知道 VEX 標 not_affected 的風險，以及為什麼不確定時寧可 patch

## 延伸閱讀

- **[CISA Log4j CVE-2021-44228 漏洞指南](https://www.cisa.gov/news-events/news/apache-log4j-vulnerability-guidance)**（CISA）
  - **讀哪裡**：事後分析段落，特別是關於「清點暴露面」的困難
  - **和本章的關聯**：Log4Shell 是這章的核心案例，第一手文件比二手分析更值得讀

- **[NIST 「Software Bill of Materials (SBOM) 在 Vulnerability Management 的應用」](https://nvlpubs.nist.gov/nistpubs/ir/2023/NIST.IR.8441.pdf)**（NIST IR 8441，2023）
  - **讀哪裡**：Section 4（Vulnerability Management Workflows）——這是官方對 SBOM + IR 流程的標準描述
  - **和本章的關聯**：本章實操流程的理論基礎

- **[EPSS 官方資源（FIRST.org）](https://www.first.org/epss/)**
  - **讀哪裡**：Model Overview，理解 EPSS 是如何估算利用機率的
  - **和本章的關聯**：IR 優先順序排序的重要補充工具，和 CVSS 搭配使用

- **[Dependency-Track 文件：Findings & Analysis](https://docs.dependencytrack.org/usage/analysis-states/)**
  - **讀哪裡**：Analysis States 頁面——理解 D-T 裡 Finding 的各個狀態（NOT_AFFECTED、FALSE_POSITIVE 等）如何對應 VEX 的概念
  - **和本章的關聯**：D-T 的 IR 操作細節

- **[CISA「Improving Security of Open Source Software in Operational Technology and Industrial Control Systems」（2023）](https://www.cisa.gov/sites/default/files/2023-10/CISA-Fact-Sheet-Improving-Security-of-OSSin-OT-ICS-508c.pdf)**
  - **讀哪裡**：SBOM 在工控/OT 環境的 IR 應用段落——OT 系統的修補比 IT 慢得多，SBOM 的影響面清點尤其重要
  - **和本章的關聯**：把這章的概念延伸到 patch 困難的 OT/ICS 場景

---

下一章誠實潑冷水：SBOM 的局限、整個領域的過度樂觀聲音、工具盲點和流程現實。

→ [Ch 29 局限、批評與現實](./29-limitations-critiques.md)
