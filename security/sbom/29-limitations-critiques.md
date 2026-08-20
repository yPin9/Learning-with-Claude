# Ch 29 — 局限、批評與現實

> **目標**：誠實地把 SBOM 的邊界畫清楚。不是要你放棄 SBOM，是要你搞清楚它的工具屬性——它能告訴你什麼、不能告訴你什麼、在什麼情況下它會讓你有錯誤的安全感。讀完這章，你對 SBOM 的判斷應該比大部分在推銷它的人更清醒。

## 為什麼需要這個？

Ch 1 開頭說過：SBOM 不是萬靈丹。現在到了課程尾聲，我們要更系統地把這句話展開。

過去五年，SBOM 的聲量很大——EO 14028、EU CRA、FDA 規範，加上一波一波的供應鏈攻擊事件，讓它從小眾話題變成合規要求。結果是：大量的組織開始「做 SBOM」，但大量的組織做的方式讓 SBOM 變成合規儀式而不是安全工具。

另一邊，有一批工程師認為 SBOM 是過度炒作的文件負擔，懷疑整件事。這種懷疑有道理的地方，也有誤解的地方。

這章要做的是：逐條把 SBOM 的批評拿出來，誠實評估哪些成立、哪些是誤解、哪些是「成立但有解法」。這不是為了打擊士氣，是為了讓你把 SBOM 放在正確的位置上，搭配正確的工具組合使用。

## 先建立直覺

把 SBOM 想成醫院的病歷表。

病歷表告訴你這個病人吃過什麼藥、做過哪些手術、有哪些過敏記錄。這是極度有用的資訊——沒有病歷表的醫院每次問診都要從頭問、出問題了不知道用藥史、兩個科室開的藥可能衝突。

但是：病歷表不能診斷你現在有什麼病，不能判斷你有沒有感染，不能阻止你生病，也不能保證記錄在上面的所有東西都是對的。如果你進了手術室，外科醫生說「病歷表在，不用檢查了」，那是危險的。

SBOM 就是軟體的病歷表。有它比沒有好。但它的邊界在哪裡，你必須清楚。

## 批評一：SBOM 不保證元件沒漏洞

這是最常見的誤解。SBOM 是一份清單，告訴你「系統裡有什麼元件、哪個版本」。它不告訴你這些元件有沒有已知漏洞，更不告訴你那些漏洞對你的系統是不是可以被利用的。

你需要兩個額外的步驟：

**步驟一：漏洞掃描**

把 SBOM 丟進 grype 或 trivy，讓它去比對 NVD、OSV、GitHub Advisory Database，找出哪些元件有已知的 CVE。這才知道「有漏洞的元件」。

**步驟二：VEX 分析**

有漏洞的元件 ≠ 你的系統可以被利用。CVE 描述的是一個函式庫在特定使用方式下的弱點，但你可能：
- 根本沒有呼叫到那個有問題的程式碼路徑
- 你的系統在那個功能前面有其他防禦層
- 那個漏洞只影響特定作業系統或特定設定，跟你的部署環境不符

這就是 VEX（Vulnerability Exploitability eXchange，Ch 16）的用途——對每個 CVE 標記「對我的系統而言這個漏洞的可利用狀態是什麼」。沒有 VEX，光有漏洞掃描的結果就只是一個清單，你還是不知道該先修哪個、哪個可以暫時忽略。

**三層架構的完整意義**：

```
SBOM（我有什麼元件）
    │
    ▼
漏洞掃描（哪些元件有已知 CVE）
    │
    ▼
VEX（哪些 CVE 對我的系統實際可利用）
    │
    ▼
優先排序 + 修復
```

跳過任何一層，資訊都是不完整的。很多組織停在第一層就以為完成了。

## 批評二：生成有盲點，而且盲點比你想的多

這是比較少被正視的問題。工具的盲點不是邊緣案例——在特定技術棧裡，它們可以是主流案例。

### 靜態連結（Static Linking）

Go、Rust、某些 C/C++ 的部署策略會把函式庫直接編進 binary，沒有任何外部的套件 metadata。syft 對 Go binary 有一定的識別能力（能從 binary 內的 `go:buildinfo` section 讀出模組資訊），但對 stripped C binary 裡靜態連結的函式庫，它能做的主要是 binary signature matching，準確度不等。

實際狀況：你有一個 stripped C binary，裡面靜態連結了某個特定版本的 zlib，syft 可能認識它、也可能不認識——取決於 zlib 是不是 syft 的 signature database 裡有的版本。

### Vendored Code

很多 C/C++ 專案（以及部分 Go 舊專案）的做法是把第三方原始碼整個 copy 到自己的 repo 裡，通常放在 `vendor/`、`third_party/`、`extern/`、`deps/` 目錄下，甚至直接混進自己的 `src/`。

這些 vendored code 通常沒有套件 metadata（沒有 `package.json`、沒有 `go.mod`），只有原始碼。cataloger 看到的就是一堆 C 檔案，不知道那是哪個版本的 OpenSSL 或 SQLite。

chromium、ffmpeg、很多嵌入式系統的 BSP——這些專案大量使用 vendoring。你對它們生出來的 SBOM，遺漏的元件數量可能比列出的還多。

### 廠商不給

閉源元件、商業 SDK、OEM 的 binary blob，廠商有時候拒絕提供 SBOM，理由是「知識產權保護」。這在嵌入式、醫療設備、電信設備領域尤其常見。你收到的設備裡面有什麼，你可能永遠不會完整知道。

法規（FDA、CRA）正在逼廠商提供，但執行力還在建立中，實際情況是很多廠商仍然拒絕或提供不完整的資訊。

### 動態載入

Build-time SBOM 只能捕捉到 build 的時候能看到的東西。runtime 動態載入的 plugin、`dlopen()` 呼叫的 `.so`、通過配置指定的 backend、JVM 啟動時才確定的 classpath——這些 build-time SBOM 全部看不到。

你需要 runtime SBOM 或 deployed SBOM（Ch 3 的六型分類）才能捕捉這些。但 runtime SBOM 更難生成，生態工具也沒有 build-time 成熟。

### 盲點的量級

業界研究（包括 NTIA 的調查和若干 academic 研究）的量級共識是：對複雜的真實系統，一份 build-time SBOM 可能遺漏 10–30% 的實際元件。這不是精確數字——它依賴技術棧和工具選擇而有很大差異——但它的意思是：「我有 SBOM，所以我知道系統裡的全部元件」這個前提，在很多情況下是錯的。

## 批評三：Naming 對不齊是系統性問題

同一個元件在不同的系統裡有不同的名字，這不是小麻煩，這是整個漏洞比對鏈裡的一個深層問題。

```
同一個 lodash 函式庫：

npm 生態：      lodash
PyPI（有個同名 Python 套件）：  lodash（完全不同的東西）
NVD CPE：       cpe:2.3:a:lodash_project:lodash:4.17.21:*:*:*:*:node.js:*:*
GHSA：          GHSA-jf85-cpcp-j695（用 advisory ID，不用 CPE）
purl：          pkg:npm/lodash@4.17.21
SPDX SPDXID：   SPDXRef-Package-lodash-4.17.21
```

當掃描工具用你 SBOM 裡的元件去比對漏洞資料庫時，它做的是一個識別子映射：「SBOM 裡的 `pkg:npm/lodash@4.17.21` 對應到 NVD 裡的哪個 CPE？」

這個映射做的是近似，不是精確。結果是兩個方向的錯誤同時存在：

- **漏報**：元件有漏洞，但因為名字對不上，掃描工具沒有比對到——你以為安全，其實不是
- **誤報**：名字相似的不同元件被錯誤比對，或者版本範圍判斷錯誤，你拿到一個 CVE 但其實不影響你

purl（Package URL）的設計動機就是解這個問題（Ch 4），但 NVD 的 CPE 遷移到 purl 還是一個進行中的工程，而很多廠商生出的 SBOM 根本沒有 purl。

識別子問題是 SBOM 生態目前最需要持續工程投入的地方之一。

## 批評四：你收到的 SBOM 可能是敷衍的

接收供應商提供的 SBOM 時，你需要驗。因為「有 SBOM」和「有有用的 SBOM」是兩件事。

常見的敷衍型態：

**頂層 only（Top-level only）**

只列直接依賴，傳遞依賴全部略過。技術上合規了（NTIA 最小要素沒有要求必須到幾層），但實際上 Log4Shell 那種問題就是傳遞依賴帶進來的，這份 SBOM 在事件時幫不上忙。

**缺 purl**

只有元件名稱和版本字串，沒有 purl，沒有標準的識別子。掃描工具的比對成功率大幅降低，你基本上要靠模糊比對，誤報和漏報都上升。

**過期**

SBOM 的 timestamp 是半年前甚至一年前的，軟體卻有持續更新。你拿到的清單跟實際部署的版本可能已經完全對不上。

**格式錯誤**

JSON malformed、SPDX spec 的必要欄位缺失、CycloneDX schema 驗不過——你的工具直接吃不了，要先手動修才能用。

**防範方法**：不能靠人工，要定義驗收標準然後工具自動驗。

你可以用以下工具在 CI 里做自動驗證：

```bash
# SPDX 格式驗證
$ pyspdxtools --file received.spdx.json

# SBOM 品質評分（NTIA completeness check）
$ sbom-scorecard score received.spdx.json

# CycloneDX schema 驗證
$ cyclonedx validate --input-file received.cdx.json --input-version v1_6
```

在採購合約或開發合作協議裡把驗收標準寫進去——最少要有 purl、要有 hash、要有傳遞依賴、格式要能過 schema 驗證——比到時候收到不合格的 SBOM 再吵更有效。

## 批評五：合規劇場（Compliance Theater）

這是目前 SBOM 生態裡最嚴重的問題，也是最難解的。

法規說要提交 SBOM，廠商就生一份、提交一份、交差。這份 SBOM 有沒有接進持續監控？有沒有人在看？新的 CVE 出來有沒有告警？大概率：沒有。

從工程流程的角度看，一個「有用的 SBOM 計畫」和一個「合規 SBOM 計畫」的差距在哪：

```
【合規劇場版】

CI 跑 syft → SBOM 存到某個地方 → 提交給客戶 / 法規機構
                                        ↑
                          到此為止，後面沒有了

【有用的版本】

CI 跑 syft → SBOM 推進 Dependency-Track
                    │
                    ├── 新 CVE 入庫 → 自動比對 → 告警到 Slack/PagerDuty
                    ├── 每次 release → 新 SBOM → 差異比對（新增了什麼？）
                    ├── VEX 標記 → 告警去雜訊 → 真正重要的才上升
                    └── EPSS 優先排序 → 高可利用性漏洞先修
```

第二個流程需要投入：Dependency-Track 要設置、VEX 要有人維護、告警要有人處理。但如果沒有這個流程，SBOM 就只是一份「知道自己哪裡有問題但沒人管」的文件，比沒有更危險——因為你以為你有管，但其實沒有。

## 批評六：假陽性疲勞（Alert Fatigue）

這是很多 SBOM + 漏洞掃描計畫失敗的最常見原因，而且它是可預期的。

一個中型系統跑 grype 掃描，出現 200–400 個 CVE 是正常的。其中：
- 大部分是你的部署 context 裡不可利用的（你沒有暴露那個攻擊面）
- 一部分是沒有修好版本的（上游還沒修，你除了等沒什麼辦法）
- 一小部分是真正需要立即處理的

如果你的流程是「每次掃描把所有 CVE 丟給工程師看」，工程師在第一週可能認真看，第二週開始跳過，第三週變成自動歸檔。真正重要的 CVE 就這樣淹在雜訊裡。

解法不是停止掃描，而是三件事的組合：

**1. VEX 去雜訊**

對每個元件的 CVE 做可利用性分析，把「not_affected」標出來。下次掃描相同 CVE 就不再告警。這需要一次性的人工投入，但之後維護成本低。

**2. EPSS 優先排序**

EPSS（Exploit Prediction Scoring System）是 FIRST 組織建的模型，預測一個 CVE 在接下來 30 天內被主動利用的機率。一個 CVSS 9.8 的漏洞 EPSS 可能只有 0.3%（因為利用門檻高或攻擊工具不普遍），另一個 CVSS 7.5 的漏洞 EPSS 可能是 85%（因為已經有公開 exploit 工具）。用 EPSS 過濾，才能把有限的修復資源放到真正被打的地方。

**3. 告警閾值**

不是所有 CVE 都要人工看。設定規則：EPSS > 50% 且沒有 VEX 標記 → PagerDuty 告警；其他的 → 每週 digest 報告。這才是可持續的流程。

## 批評七：業界懷疑論的合理之處

對 SBOM 的懷疑不全是誤解，有幾條批評有它的道理：

**「只是多一層要維護的文件」**

如果你的 SBOM 流程是「每隔幾個月手動跑一次 syft，把結果存到某個共享磁碟」，那這個批評完全成立。手動維護的 SBOM 會過期、會跟實際部署脫鉤、會讓人誤以為有管理但其實沒有。

**「小公司沒有資源做」**

完整的 SBOM 生態（syft + Dependency-Track + VEX 工作流 + EPSS 整合）需要 DevSecOps 投入。1–5 人的新創在早期要做全套，成本相對收益確實不划算。

**「攻擊者可以反過來用你的 SBOM」**

公開 SBOM 讓攻擊者不需要逆向就知道你用什麼版本的什麼元件，可以針對已知漏洞制定攻擊計畫。這個擔憂是真實的。

## 批評七的反駁

**「只是多一層文件」的反駁**

如果你的 SBOM 生成是 CI 自動跑、自動推 Dependency-Track，維護成本接近零。每次 build 都自動更新，不存在過期問題。這個批評的前提是「手動維護」，但正確的做法是「自動化」。

**「小公司沒資源」的反駁**

基礎層的成本很低：在 CI 裡加一行 `syft . -o spdx-json=sbom.json`，把結果存到 artifact storage，對高嚴重性漏洞用 grype 設 CI 中斷。這個投入是幾小時的設置，不需要 Dependency-Track 和完整的 VEX 工作流。至少做基礎層，事故發生時能查表就已經值回票價。

**「攻擊者用 SBOM」的反駁**

攻擊者本來就能靠分析你的 `package.json`、`pom.xml`、`go.sum` 或者直接 string-dump 你的 binary 推斷你用什麼。SBOM 讓這件事更容易一點，但「保密依賴清單」從來不是真實的安全邊界。相比之下，SBOM 讓防禦方的事故回應速度提升是有實際量化的——攻擊者的信息增益遠小於防禦方的效益增益。

## 對比與取捨

| SBOM 能做 | SBOM 不能做 |
|-----------|-------------|
| 列出已知元件的清單 | 保證清單完整（靜態連結、vendored code、動態載入有盲點） |
| 提供事故回應時的查表能力 | 自動修復漏洞或做任何主動防禦 |
| 作為漏洞掃描的輸入 | 判斷漏洞對你的系統是否可利用（需要 VEX） |
| 支援 VEX 漏洞影響分類 | 保證 VEX 標記本身的準確性（標記需要人工審查） |
| 符合法規合規要求 | 保證系統安全（合規 ≠ 安全） |
| 加速漏洞影響範圍分析 | 取代滲透測試、程式碼審查或其他安全評估 |
| 讓下游客戶了解你的軟體構成 | 阻止攻擊發生 |
| 接進持續監控（Dependency-Track）做告警 | 在沒有人看告警的情況下自動產生價值 |
| 支援授權合規審查 | 解讀授權的法律含義（還是需要法務） |

## 踩雷集錦

**1. 把「有 SBOM」等同於「安全了」**

這是最危險的錯誤。「我們有 SBOM 計畫」和「我們的供應鏈是安全的」是完全不同的命題。SBOM 是可見性工具——它讓你看得到；但看得到和有防禦是兩件事。如果你看到問題但沒有流程去處理，可見性反而讓你有一種虛假的安全感，因為你以為在管理風險，其實只是在記錄風險。

**2. 產一次放著不更新**

SBOM 的有效期和你的軟體一起移動。每次有新的依賴被加進來、每次升版、每次新的 release，SBOM 就過期了。一份六個月前的 SBOM 在事故時可能給你錯誤的信息：你以為某個元件是安全版本，但其實那個版本早就被升掉了、或者反過來新加了一個有漏洞的元件。過期的 SBOM 比沒有更糟——因為你信任一份錯的資訊。

正確做法：SBOM 生成必須是 CI 的一部分，每次 build 自動產、自動存，版本跟 artifact 綁定。

**3. 收到供應商 SBOM 就信以為真**

供應商提供的 SBOM 你必須驗。至少做三件事：
- 格式驗證（schema 通不通過）
- 簽章驗證（有沒有 cosign 簽章，Ch 21；有沒有可以追溯到供應商身份的 Rekor 記錄）
- 內容基本合理性檢查（sbom-scorecard 跑一遍，看 NTIA completeness 有沒有通過）

沒有驗就用，你不知道那份 SBOM 是準確的還是半年前隨便生的、格式是不是有問題、有沒有被竄改。

**4. 有掃描但沒人看告警**

Dependency-Track 收到新 CVE 發出告警，告警積了兩百封沒人處理。這比沒有告警更糟，因為「我們有監控」這個認知讓大家以為有人在管，但其實沒有。告警機制必須搭配處理流程：誰負責看、看完要做什麼決定、什麼等級的問題必須在幾天內回應。沒有這個流程，工具只是做了個樣子。

**5. 用 SBOM 覆蓋所有安全需求**

SBOM 是供應鏈透明度工具，不是全方位安全工具。它覆蓋的是「你在用什麼元件」這個問題。它覆蓋不了：你的應用程式邏輯有沒有漏洞（需要 SAST / code review / pen test）、你的基礎設施有沒有配置錯誤（需要 CSPM / infrastructure audit）、你的人員有沒有被釣魚（需要 security awareness）。把 SBOM 預算投進去然後認為其他安全工作可以縮減，是個很常見的誤判。

## 進階：再往深一層

### SBOM 的真正天花板：信任鏈的假設

整個 SBOM 架構有一個隱性假設：生成 SBOM 的工具本身是可信的，生成 SBOM 的環境是乾淨的，生成過程沒有被篡改。

SolarWinds 攻擊告訴我們這個假設可以被違反。如果 build 環境本身被污染，生出來的 SBOM 也可能是錯的——它正確地描述了被污染後的軟體構成，但無法告訴你「這個構成是被篡改的」。

這就是為什麼 SLSA（Ch 22–23）和 in-toto provenance 是 SBOM 的必要補充而不是可選項：SLSA 把 build 過程本身的完整性納入考量，讓你能問「這份 SBOM 是在什麼環境、什麼流程下生成的」，而不只是「這份 SBOM 說了什麼」。

在沒有 SLSA provenance 的情況下，一份 SBOM 的可信度取決於你對生成它的組織的信任——這是一個很脆弱的基礎。

### CPE → purl 遷移的進度問題

NVD 對 CVE 的 CPE 標記品質一直有問題——漏標、錯標、CPE dictionary 跟不上新套件。CISA 和 OpenSSF 有在推動把 NVD 的主要識別子從 CPE 遷移到 purl，但這是一個有大量歷史資料需要遷移的工程。

在遷移完成之前，掃描工具做的 SBOM-to-CVE 比對涉及從 purl 到 CPE 的映射，這個映射的品質決定了漏報率。Anchore 和 Chainguard 等公司在維護自己的補充映射表，但這不是一個完全解決的問題。

### 誰該擁有 SBOM 的技術決策

在大型組織裡，SBOM 計畫常常找不到明確的 owner：安全團隊認為這是開發的工具問題，開發認為這是安全要求，法務認為這是合規的事，DevOps 認為這是 CI 的配置問題。

結果是：有人建了個工具，沒有人負責流程，沒有人對「告警後真的有人處理」負責。技術工具到位，但組織流程沒有到位。

這個問題的解法是：在啟動 SBOM 計畫的時候，就明確定義 RACI——誰負責工具維護（Responsible），誰是決策者（Accountable），誰要被諮詢（Consulted），誰需要知道（Informed）。Ch 25（企業 SBOM 計畫）展開了這個面向。

## 動手練習

這是一個批判性分析練習，目的是讓你親眼看到 SBOM 的盲點，而不是只是聽我描述它。

**工具準備**：syft、grype，以及一個能跑的 Linux / WSL 環境。

**步驟一：生成一份真實的 SBOM**

用 curl 作為練習對象（一個你可能認識的、有複雜依賴的 C 專案）：

```bash
# clone 一個釘死的版本，確保結果可重現
git clone --depth=1 --branch curl-8_9_1 https://github.com/curl/curl.git /tmp/curl-sbom

# 用 syft 對原始碼目錄生成 SBOM
cd /tmp/curl-sbom
syft dir:. -o spdx-json=curl.spdx.json
```

**步驟二：評估 SBOM 品質**

對這份 SBOM 做以下檢查，每個問題寫下你的答案：

```bash
# 1. 有幾個 package 被列出來？
cat curl.spdx.json | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d.get('packages', [])))"

# 2. 有幾個 package 有 purl？
cat curl.spdx.json | python3 -c "
import json, sys
d = json.load(sys.stdin)
pkgs_with_purl = [p for p in d.get('packages', []) if any(ref.get('referenceType') == 'purl' for ref in p.get('externalRefs', []))]
print(f'{len(pkgs_with_purl)} / {len(d[\"packages\"])} 有 purl')
"

# 3. 有沒有 sha256 hash？
python3 -c "
import json
d = json.load(open('curl.spdx.json'))
with_hash = [p for p in d.get('packages', []) if p.get('checksums')]
print(f'{len(with_hash)} / {len(d[\"packages\"])} 有 checksum')
"

# 4. 有沒有 dependency relationship？
python3 -c "
import json
d = json.load(open('curl.spdx.json'))
rels = d.get('relationships', [])
print(f'共 {len(rels)} 條 relationship')
"
```

**步驟三：找出盲點**

打開 curl 的 `configure.ac` 或 `CMakeLists.txt`，找出它依賴了哪些外部函式庫（OpenSSL、zlib、libssh2、c-ares 等）：

```bash
grep -E "(CURL_WITH|pkg_check_modules|find_package)" /tmp/curl-sbom/CMakeLists.txt | head -30
```

對照你的 SBOM，找出：哪些依賴出現在 SBOM 裡？哪些沒有？為什麼沒有（它們是 system package、build-time 決定的、還是根本被 syft 的 C cataloger 忽略了）？

**步驟四：用 2–3 句話回答**

如果 libcurl 8.9.1 有一個影響了 OpenSSL 連線處理的 CVE 被公開，這份你生出來的 SBOM 能給你什麼資訊、不能給你什麼？你需要額外做什麼才能知道你的系統是否受影響？

---

這個練習的重點不是答案，是親眼看到差距。你會發現 syft 對 C 專案的識別比對 npm 或 Go 弱，而那個弱點在真正需要查表的時候是有代價的。

## 本章重點整理

- SBOM 是必要條件，不是充分條件。它解決可見性問題，但不解決安全問題。
- 生成工具有系統性盲點：靜態連結、vendored code、動態載入、閉源元件——一份「完整」的 SBOM 可能遺漏 10–30% 的實際元件。
- Naming 對不齊（purl vs CPE vs 元件名稱）是整個漏洞比對鏈的系統性問題，造成漏報和誤報同時存在。
- 你收到的 SBOM 可能是敷衍的——要定義驗收標準並工具自動驗，不能靠信任。
- 合規劇場是目前最嚴重的問題：SBOM 被當一次性任務交差，沒有接進持續監控和告警處理流程。
- 假陽性疲勞是 SBOM 計畫最常見的失敗原因，解法是 VEX + EPSS 優先排序 + 合理的告警閾值。
- 業界懷疑論有部分成立，但批評的前提通常是「沒有自動化」和「沒有流程」——正確的做法可以解決這些問題。
- SBOM 要和漏洞掃描、VEX、SLSA provenance、持續監控、有人處理的告警流程一起，才能從「清單」變成「安全基礎設施」。

## 自我檢核

- [ ] 我能解釋為什麼「SBOM 說有這個元件」不等於「這個元件的漏洞對我的系統可利用」
- [ ] 我能說出 syft 在 C/C++ 靜態連結、vendored code、動態載入這三種場景下的盲點
- [ ] 我知道 Naming 對不齊如何同時造成漏報和誤報，以及 purl 的作用
- [ ] 我能描述一份「敷衍型 SBOM」的常見特徵，以及如何用工具自動驗收
- [ ] 我理解「合規劇場」和「有用的 SBOM 計畫」在流程上的具體差別
- [ ] 我能解釋假陽性疲勞為什麼是 SBOM 計畫最常見的失敗原因，以及三種緩解手段
- [ ] 我能公平地評估業界懷疑論的成立之處和反駁，而不是把 SBOM 當宗教來辯護
- [ ] 我知道 SBOM 需要搭配哪四到五種東西才能從清單變成有用的安全基礎設施

## 延伸閱讀

- **[CISA「Known Limitations of SBOM」工作組文件](https://www.cisa.gov/sbom)**（CISA SBOM 資源頁）
  - **讀哪裡**：「Sharing & Considerations」和相關的 white paper——官方對 SBOM 已知局限的誠實描述
  - **和本章的關聯**：本章批評的官方版本，適合拿來對照

- **[Anchore「The State of Software Supply Chain Security」年度報告](https://anchore.com/software-supply-chain-security-report/)**（Anchore）
  - **讀哪裡**：「SBOM adoption」和「quality gaps」章節——真實數據看業界的 SBOM 品質現況
  - **和本章的關聯**：「收到的 SBOM 可能是敷衍的」這個批評的資料支撐

- **[EPSS 模型說明](https://www.first.org/epss/model)**（FIRST.org）
  - **讀哪裡**：首頁的模型概述和 FAQ——理解 EPSS 如何預測利用機率，和 CVSS 的比較
  - **和本章的關聯**：假陽性疲勞的解法之一，EPSS 優先排序的理論基礎

- **[「Is SBOM Dead?」— Dan Lorenc（Chainguard，2024）](https://dlorenc.medium.com/)**（Medium / Blog）
  - **讀哪裡**：整篇，它是一個業界懷疑論的有力版本，以及作者的反駁
  - **和本章的關聯**：對本章「業界懷疑論的合理之處」那一節的良好補充，業界實踐者視角

- **[NTIA「Framing Software Component Transparency」最終報告（2021）](https://www.ntia.gov/report/2021/framing-software-component-transparency-establishing-common-software-bill-materials)**（NTIA）
  - **讀哪裡**：「Challenges and Limitations」章節——官方承認的挑戰，沒有過度樂觀
  - **和本章的關聯**：SBOM 基礎文件對其自身局限的誠實表述

---

這是課程的最後一個內容章。我們從 Ch 1 的「SBOM 不是萬靈丹」開始，到這裡把那句話系統地展開——它的邊界在哪裡、它的盲點是什麼、什麼情況下它會給你假的安全感、以及它正確的使用姿勢是什麼。

清醒地知道一個工具的局限，是真正掌握它的前提。

→ [Final Project：端到端供應鏈安全 pipeline](./final-project-supply-chain-pipeline.md)
