# Ch 16 — VEX：有漏洞不等於可被利用

> **目標**：理解 VEX（Vulnerability Exploitability eXchange）的設計動機、四種狀態與五種 justification 的精確語意；學會寫一份 OpenVEX 文件，讓 grype 吃進去後抑制誤報；理解三種 VEX 載體格式（OpenVEX/CSAF/CycloneDX）各自的定位。

## 為什麼需要這個？

你從 Ch 15 看到了掃出 110 個漏洞。問題是：這 110 個漏洞裡，有多少是「技術上存在這個元件、這個元件在那個版本確實有 CVE，但你的產品根本不可能觸發這個漏洞」的情況？

以 `flask 1.0.2` 的 CVE-2023-30861（Session Cookie 沒有設 `SameSite=None`）為例：這個漏洞要求你的應用使用了 Flask 的 session cookie 功能。如果你的服務是一個無狀態的 REST API（根本不用 session），這個 CVE 對你而言是完全不可利用的。

但你的掃描器不知道你怎麼用 Flask。它只知道「你有 flask 1.0.2、這個版本有 CVE-2023-30861」，就回報了一個 High 漏洞。

這就是 **false positive（誤報）的語意層**：不是工具算錯了版本，而是即使版本確實有漏洞，在你的具體使用情境裡也無法被利用。

VEX 就是讓你（廠商或使用者）對「這個 CVE 在我的產品裡到底可不可利用」做**機器可讀聲明**的機制。

## 先建立直覺：三張紙、三個問題

```
SBOM 回答：「我的軟體裡有哪些元件？」
掃描結果回答：「這些元件哪些版本有已知 CVE？」
VEX 回答：「這些 CVE 在我的具體產品裡，到底可不可以被利用？」

  SBOM                 Scan                   VEX
 ┌─────────┐          ┌──────────┐          ┌──────────────────────┐
 │ flask   │          │ flask    │  聲明     │ flask 1.0.2          │
 │ 1.0.2   │ ──查詢──▶│ 1.0.2    │ ────────▶│ CVE-2023-30861       │
 │         │          │ CVE-2023-│          │ status: not_affected │
 └─────────┘          │ 30861    │          │ justification:       │
                      │ High ← ─ │ ─ 降噪 ─ │   code_not_in_path   │
                      └──────────┘          └──────────────────────┘
                                                       ↑
                                              grype --vex my.vex.json
```

VEX 是廠商（或你自己的安全團隊）對消費者說：「我知道這個 CVE，我已經評估過了，以下是結論。」這個「結論」是機器可讀的，可以讓掃描工具自動抑制不相關的發現，也可以讓 CI 不因為已知無害的 CVE 而誤擋部署。

## 四種狀態（Status）

VEX 的核心是對每個 `(產品, CVE)` 組合的狀態聲明。OpenVEX 規範定義四種：

| Status | 意思 | 需要附加的行動/資訊 |
|---|---|---|
| `not_affected` | 這個 CVE 在這個產品裡不可利用，不需要採取行動 | 必須提供 `justification` |
| `affected` | 這個 CVE 確實影響這個產品，消費者應採取行動 | 建議提供 `action_statement` |
| `fixed` | 這個版本已包含修復，CVE 不再適用 | 通常附上修復說明 |
| `under_investigation` | 目前仍在評估，結論尚未確定 | 通常附上預計完成時間 |

`not_affected` 是最常用的——它讓你把誤報或不可利用的漏洞從掃描結果裡「消聲」。

## 五種 not_affected Justification

每個 `not_affected` 聲明**必須**附上 justification，說明為什麼這個 CVE 在你的產品裡不可利用。OpenVEX 規範定義五種（字串值直接用在 JSON 裡）：

| Justification | 意思 | 典型情境 |
|---|---|---|
| `component_not_present` | 漏洞元件根本沒有出現在最終產品裡 | build-time dependency 沒有被打包進去 |
| `vulnerable_code_not_present` | 元件在，但有漏洞的那段程式碼（函式/模組）不在這個版本裡 | 漏洞版本比你用的版本更新（靜態分析確認） |
| `vulnerable_code_not_in_execute_path` | 有漏洞的程式碼存在，但你的執行路徑絕對不會呼叫到它 | 你不使用某功能，攻擊者無法到達漏洞觸發點 |
| `vulnerable_code_cannot_be_controlled_by_adversary` | 攻擊者無法控制觸發漏洞的輸入或條件 | 有漏洞的函式只被內部系統呼叫，外部輸入無法到達 |
| `inline_mitigations_already_exist` | 存在其他內建緩解措施，使得利用不可能或實際無害 | WAF 阻擋了相關 payload；環境限制使攻擊無效 |

**重要**：選 justification 要誠實。`component_not_present` 是最強的聲明（可以靜態驗證），`inline_mitigations_already_exist` 是最難驗證的（依賴執行環境的假設）。濫用 VEX 把真實風險標成 `not_affected` 是一個嚴重的安全失誤。

## 實戰：寫一份 OpenVEX，讓 grype 閉嘴

### 情境設定

繼續用 `/tmp/vuln-demo/sbom.spdx.json`（Python 舊版套件 SBOM）。grype 掃出 flask 有兩個漏洞：

```bash
grype sbom:sbom.spdx.json --by-cve -o table 2>/dev/null | grep flask
```

輸出：
```
flask  1.0.2  2.2.5  python  CVE-2023-30861  High   1.3% (67th)  1.0
flask  1.0.2  3.1.3  python  CVE-2026-27205  Low    0.3% (27th)  < 0.1
```

評估後決定：
- **CVE-2023-30861**（Session cookie SameSite 問題）：我們的服務是無狀態 REST API，根本沒有用 Flask session。→ `not_affected`, `vulnerable_code_not_in_execute_path`
- **CVE-2026-27205**：還在評估中。→ `under_investigation`

### 手寫 OpenVEX

```bash
cat > /tmp/vuln-demo/my-app.vex.json << 'EOF'
{
  "@context": "https://openvex.dev/ns/v0.2.0",
  "@id": "https://example.com/vex/my-app/v1.0.0",
  "author": "Security Team <security@example.com>",
  "timestamp": "2026-08-17T00:00:00Z",
  "version": 1,
  "statements": [
    {
      "vulnerability": {
        "name": "CVE-2023-30861",
        "aliases": ["GHSA-m2qf-hxjv-5gpq"]
      },
      "products": [{"@id": "pkg:pypi/flask@1.0.2"}],
      "status": "not_affected",
      "justification": "vulnerable_code_not_in_execute_path",
      "impact_statement": "This service is a stateless REST API. Flask session cookies are not used in any code path.",
      "timestamp": "2026-08-17T00:00:00Z"
    },
    {
      "vulnerability": {
        "name": "CVE-2026-27205",
        "aliases": ["GHSA-68rp-wp8r-4726"]
      },
      "products": [{"@id": "pkg:pypi/flask@1.0.2"}],
      "status": "under_investigation",
      "timestamp": "2026-08-17T00:00:00Z"
    }
  ]
}
EOF
```

### 用 vexctl 生成（替代方案）

```bash
# vexctl 0.4.4 已裝在 ~/bin/vexctl
vexctl create \
  --author "Security Team" \
  --product "pkg:pypi/flask@1.0.2" \
  --vuln "CVE-2023-30861" \
  --status "not_affected" \
  --justification "vulnerable_code_not_in_execute_path"
```

真實輸出（vexctl 0.4.4 真跑）：

```json
{
  "@context": "https://openvex.dev/ns/v0.2.0",
  "@id": "https://openvex.dev/docs/public/vex-96f24de38cdc40447fdbb7554e166336fb63752c76f3b9893f270266519d3799",
  "author": "Security Team",
  "version": 1,
  "statements": [
    {
      "vulnerability": {"name": "CVE-2023-30861"},
      "products": [{"@id": "pkg:pypi/flask@1.0.2"}],
      "status": "not_affected",
      "justification": "vulnerable_code_not_in_execute_path",
      "timestamp": "2026-08-17T11:48:22.54675561Z"
    }
  ],
  "timestamp": "2026-08-17T11:48:22Z"
}
```

### grype --vex：讓它吃進 VEX

```bash
# 比較 before/after
echo "=== Without VEX ==="
grype sbom:sbom.spdx.json --by-cve -o table 2>/dev/null | grep flask

echo ""
echo "=== With VEX ==="
grype sbom:sbom.spdx.json --by-cve \
  --vex /tmp/vuln-demo/my-app.vex.json \
  -o table 2>/dev/null | grep flask
```

真實輸出（真跑驗證）：

```
=== Without VEX ===
flask  1.0.2  2.2.5  python  CVE-2023-30861  High  1.3% (67th)  1.0
flask  1.0.2  3.1.3  python  CVE-2026-27205  Low   0.3% (27th)  < 0.1

=== With VEX ===
flask  1.0.2  3.1.3  python  CVE-2026-27205  Low   0.3% (27th)  < 0.1
```

CVE-2023-30861 從結果裡消失了。`under_investigation` 的 CVE-2026-27205 仍然顯示（grype 的設計：`not_affected` 才抑制，`under_investigation` 繼續顯示讓你追蹤）。

## 底層機制：三種 VEX 載體格式

VEX 的「格式」有三種不同的載體，適用場景不同：

```
OpenVEX（獨立 JSON）         CSAF VEX（OASIS 標準）       CycloneDX 原生
─────────────────────────   ──────────────────────────   ────────────────────
  openvex.dev 社群標準         OASIS 工業標準               CycloneDX BOM 內嵌
  輕量、人類可讀                重量、複雜                    SBOM 和 VEX 合一
  適合開源/中小型廠商            適合大型企業/政府/產品廠商      適合 BOM 生產方
  grype --vex 支援              trivy --vex 支援部分           cyclonedx-java/
  vexctl 生成                   CSAF 工具鏈複雜               Python 生成
  無 PKI 要求                   通常需要 PKI 或數位簽章        SBOM 本身帶 VEX
```

### OpenVEX（openvex.dev）

OpenVEX 是 OpenSSF 旗下的規範，由 Chainguard 和 VMware（現為 Broadcom）主導開發。特色是輕量、以 JSON-LD 為基礎、用 PURL 識別產品，設計上就是讓工具鏈（grype、cosign）直接消費的。

grype 的 `--vex` 旗標支援 OpenVEX 格式，是目前工具整合最完整的 VEX 格式。

### CSAF VEX（OASIS Standard）

CSAF（Common Security Advisory Framework）是 OASIS 的標準，前身是 CVRF。CSAF VEX 是 CSAF 2.0 中的一個 profile，專門用於 VEX 聲明。

CSAF VEX 適合大型廠商（例如 Cisco、Microsoft）發布官方安全公告的場景，格式比 OpenVEX 複雜得多，但有更完整的元數據（供應商資訊、追蹤 ID、版本歷史）。

美國 CISA 的多份指引文件（包含 VEX Use Cases）對 CSAF VEX 有詳細說明。

### CycloneDX 原生 VEX

CycloneDX 格式支援在 BOM 本身內嵌 VEX 聲明（`vulnerabilities` 陣列），或作為獨立的 VEX BOM 文件。適合當你的 SBOM 生成工具本身就是 CycloneDX 系列工具（如 cdxgen）的情況，可以把 SBOM 和 VEX 一起打包傳遞。

```json
{
  "bomFormat": "CycloneDX",
  "specVersion": "1.6",
  "vulnerabilities": [
    {
      "id": "CVE-2023-30861",
      "affects": [{"ref": "pkg:pypi/flask@1.0.2"}],
      "analysis": {
        "state": "not_affected",
        "justification": "code_not_reachable",
        "detail": "Flask session not used in this service."
      }
    }
  ]
}
```

注意：CycloneDX 的 justification 字串值（這裡是 `code_not_reachable`）和 OpenVEX（`vulnerable_code_not_in_execute_path`）不同，語意相近但**不是一一對應**——CycloneDX 有自己一套較大的 justification 列舉（`code_not_present`、`code_not_reachable`、`requires_configuration`、`requires_dependency`、`requires_environment`、`protected_by_compiler`、`protected_at_runtime`、`protected_at_perimeter`、`protected_by_mitigating_control`），比 OpenVEX 的五種細。跨格式轉 VEX 時要自己做語意對映，不能直接照抄字串。

## 對比與取捨：三種載體選哪個？

| 場景 | 推薦載體 |
|---|---|
| 你是開源專案或中小型組織，工具主要是 grype | OpenVEX + vexctl |
| 你是大型商業軟體廠商，需要發布官方安全公告 | CSAF VEX |
| 你的 SBOM pipeline 全程用 CycloneDX | CycloneDX 原生 VEX |
| 你要交給政府或大型企業（合規要求） | CSAF VEX（CISA 推薦） |
| 你想讓 CI 自動消噪、grype 直接整合 | OpenVEX |

沒有最好的格式，只有最適合你場景的格式。OpenVEX 是目前工具整合最簡單的起點；CSAF 是政府/大型廠商的最終目的地。

## 踩雷集錦

1. **`not_affected` 不是「我不想處理這個漏洞」**：VEX 是技術上的聲明（這個程式碼路徑不可達、這個元件不存在於執行路徑），不是「我知道有漏洞但懶得修」。把可利用的漏洞標成 `not_affected` 是謊言，在合規場景裡是嚴重違規。評估不確定就用 `under_investigation`，不要亂用 `not_affected`。
2. **`@id` 的產品 PURL 要完全匹配**：VEX 裡的 `"@id": "pkg:pypi/flask@1.0.2"` 必須和 SBOM 裡的 PURL 完全一致（包含大小寫、版本格式）。`pkg:pypi/Flask@1.0.2`（大寫 F）不會比中 `pkg:pypi/flask@1.0.2`。
3. **VEX 文件本身要有版本和時間戳**：`version` 欄位是整數，每次更新聲明要遞增；`timestamp` 是 ISO8601 格式。grype 會用這些欄位決定哪個 VEX 更新，如果時間戳一樣且版本一樣，重複聲明的行為未定義。
4. **grype `--vex` 只支援 OpenVEX**：不支援 CSAF VEX 或 CycloneDX VEX。trivy 支援部分 VEX 格式但整合方式不同。不要假設「VEX」是通用旗標。
5. **`under_investigation` 不能無限期掛著**：它代表「我正在評估」，不是「我永遠不想管」。理論上應該設一個 deadline（`action_statement` 或組織流程保證），最終要轉成 `not_affected`、`affected`、或 `fixed` 其中一個結論。

## 進階：再往深一層

### VEX 的合規價值

美國 CISA 的 VEX Use Cases 文件（2023）明確說明：對於醫療器材、工業控制系統等受監管的軟體產品，廠商提供 SBOM + VEX 的組合，讓使用者能自動化評估「我用的這個產品受不受影響」，是比光有 SBOM 更完整的安全透明度。

FDA 的醫材 SBOM 指引和 EU CRA 的討論中，VEX 都被提及為 SBOM 的重要補充——你說你有 log4j，但你有義務告訴客戶「log4j 在我的設備裡是不是可利用的」。

### vexctl merge：多份 VEX 合併

當你有多個來源的 VEX 文件（廠商提供的 + 你自己評估的），可以用 vexctl 合併：

```bash
vexctl merge vendor.vex.json my-assessment.vex.json > merged.vex.json
grype sbom:sbom.spdx.json --vex merged.vex.json
```

合併規則：同一個 `(product, vuln)` 組合有多個聲明時，以最新時間戳的為準。

### 把 VEX 接進 CI pipeline

```yaml
# GitHub Actions 範例（概念性，需依環境調整）
- name: Scan SBOM with VEX
  run: |
    grype sbom:sbom.spdx.json \
      --vex vex/my-app.vex.json \
      --fail-on critical \
      -o sarif > results.sarif

- name: Upload to Code Scanning
  uses: github/codeql-action/upload-sarif@v3
  with:
    sarif_file: results.sarif
```

這樣的 pipeline 讓 VEX 文件的維護變成程式碼庫的一部分（version control），讓「這個 CVE 我們評估過了、不可利用、原因是 XXX」這件事有歷史紀錄、可稽核。

## 動手練習

1. 對 `/tmp/vuln-demo/sbom.spdx.json` 找一個你認為「在一個無狀態 API 服務裡不可利用」的 High 漏洞（參考 grype 輸出，讀漏洞描述），寫一份 OpenVEX 聲明（選一個合理的 justification），然後用 `grype sbom:sbom.spdx.json --vex your.vex.json` 驗證它消失了。
2. 試試錯誤的 PURL（例如用 `pkg:pypi/Flask@1.0.2`，大寫 F），確認 VEX 不生效，理解大小寫敏感性的重要。
3. 對同一個 CVE，先後寫兩份 VEX（同一個 `@id`，但 `version` 分別為 1 和 2，時間戳不同），看 grype 是否以最新的為準。

## 本章重點整理

- VEX 是對「這個 CVE 在這個產品裡可不可利用」的機器可讀聲明，補 SBOM 和掃描結果之間「找到漏洞元件」和「漏洞真的可被利用」的語意差距。
- 四種 status：`not_affected`（不需行動）、`affected`（需行動）、`fixed`（已修復）、`under_investigation`（評估中）。
- `not_affected` 必須附 justification，五種選項：`component_not_present`、`vulnerable_code_not_present`、`vulnerable_code_not_in_execute_path`、`vulnerable_code_cannot_be_controlled_by_adversary`、`inline_mitigations_already_exist`。
- OpenVEX + grype `--vex` 是目前工具整合最完整的路徑。`not_affected` 會讓 grype 抑制該漏洞，`under_investigation` 仍然顯示。
- VEX 是技術聲明，不是「決定不修」的藉口。

## 自我檢核

- [ ] 我能說出四種 VEX status 的語意差別，以及為什麼 `under_investigation` 不等於 `not_affected`
- [ ] 我能寫一份格式正確的 OpenVEX JSON，並用 grype `--vex` 驗證它抑制了正確的 CVE
- [ ] 我知道 `@id` 的 PURL 必須精確匹配 SBOM 裡的 PURL（大小寫敏感）
- [ ] 我能解釋 OpenVEX、CSAF VEX、CycloneDX VEX 的適用場景差異

## 延伸閱讀

- **[OpenVEX Spec](https://github.com/openvex/spec/blob/main/OPENVEX-SPEC.md)** — 規範本文，status/justification 的完整字串值定義在這裡；寫 VEX 前必讀，別靠記憶背 justification 值
- **[CISA VEX Use Cases](https://www.cisa.gov/resources-tools/resources/vex-use-cases-and-sharing-guidance)** — CISA 官方的 VEX 使用場景文件，解釋各種廠商/消費者的 VEX 交換情境，以及 VEX 和 SBOM 如何配合
- **[vexctl](https://github.com/openvex/vexctl)** — OpenVEX 的官方 CLI（今天用的版本 v0.4.4）；支援 create/merge/filter，也有 attest（把 VEX 簽進 cosign attestation）的子命令
- **[Chainguard: Getting Started with OpenVEX](https://edu.chainguard.dev/open-source/sbom/getting-started-openvex-vexctl/)** — Chainguard Academy 的 OpenVEX 入門，有很多具體的 vexctl 使用範例

---

VEX 解決了「此刻的掃描有雜訊」的問題，但今天沒有漏洞不代表明天沒有。下一章進入持續監控的世界：Dependency-Track 讓你把 SBOM 當資產庫，有新的 CVE 爆發就自動告警。

→ [Ch 17 Dependency-Track 營運](./17-dependency-track.md)
