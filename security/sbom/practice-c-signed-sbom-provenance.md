# 練習 C — signed SBOM + SLSA provenance

> **目標**：對一個你自己 build 的 artifact，跑完整條信任鏈：產 SBOM → 用 cosign 簽 SBOM → 把 SBOM 打成 attestation → 手工組 SLSA provenance → 驗整條（驗簽、驗 attestation、驗 provenance）。

## 任務規格

你要完成以下五件事，每件事都有對應的驗證指令確認「確實做到了」：

| # | 任務 | 驗證方式 |
|---|---|---|
| 1 | 準備 artifact + 產 SBOM | `jq '.name' myapp.spdx.json` 印出名稱 |
| 2 | 簽 SBOM（sign-blob） | `cosign verify-blob` 印出 `Verified OK` |
| 3 | 把 SBOM 打成 attestation | 解碼 bundle，確認 subject digest = artifact sha256 |
| 4 | 手工產 SLSA provenance | `cosign verify-blob-attestation --type slsaprovenance1` 印出 `Verified OK` |
| 5 | 整條驗證清單 | 填完下面的「驗證表」每一列 |

### 環境要求

- WSL2 Ubuntu，`cosign` 2.4.1，`jq`，Python 3
- cosign key pair（沒有就 `COSIGN_PASSWORD="" cosign generate-key-pair` 產一對）
- **不需要** Docker、OCI registry、GitHub Actions

### Artifact 規格

任意檔案都行。建議用一個 Go 程式：

```go
// main.go
package main

import "fmt"

func main() {
    fmt.Println("myapp v1.0.0")
}
```

```bash
# 編譯
go build -o myapp-v1.0.0-linux-amd64 main.go
# 如果沒有 Go，用 echo 產一個假的 binary 也行：
echo "fake binary v1.0.0" > myapp-v1.0.0-linux-amd64
```

## 期望輸出

### 任務 1：SBOM

`jq '.name' myapp.spdx.json` 應該印出你在 SBOM 裡設定的 artifact 名稱（如 `"myapp-1.0.0"`）。

`jq '.packages | length' myapp.spdx.json` 至少 1（有一個 package 描述）。

### 任務 2：簽 SBOM

```
WARNING: Skipping tlog verification is an insecure practice ...
Verified OK
```

故意改 SBOM 一個字元後再驗，應該看到：

```
Error: invalid signature when validating ASN.1 encoded signature
```

### 任務 3：SBOM Attestation

解碼後的 Statement（Python 解碼，見下方步驟建議），確認：

- `predicateType` = `"https://spdx.dev/Document"`
- `subject[0].name` = 你的 artifact 檔名
- `subject[0].digest.sha256` = `sha256sum myapp-v1.0.0-linux-amd64 | awk '{print $1}'` 的輸出

### 任務 4：SLSA Provenance Attestation

```
WARNING: Skipping tlog verification is an insecure practice ...
Verified OK
```

解碼後確認：

- `predicateType` = `"https://slsa.dev/provenance/v1"`
- `predicate.buildDefinition.buildType` = 你填的 buildType
- `predicate.runDetails.builder.id` = 你填的 builder URI

## 步驟建議

### Step 1：建目錄，準備金鑰和 artifact

```bash
mkdir -p ~/practice-c && cd ~/practice-c

# 產 key pair（若已有可跳過）
COSIGN_PASSWORD="" cosign generate-key-pair

# 準備 artifact
echo "myapp v1.0.0 fake binary" > myapp-v1.0.0-linux-amd64

# 確認 sha256（這個值後面要核對）
sha256sum myapp-v1.0.0-linux-amd64
```

### Step 2：產 SBOM

手工寫一份最小 SPDX JSON（或用 `syft` 掃真實的 binary；syft 需要 Docker 才能掃 image，掃本地 binary 用 `syft dir:.`）：

```bash
ARTIFACT_SHA=$(sha256sum myapp-v1.0.0-linux-amd64 | awk '{print $1}')
cat > myapp.spdx.json << EOF
{
  "SPDXID": "SPDXRef-DOCUMENT",
  "spdxVersion": "SPDX-2.3",
  "creationInfo": {
    "created": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
    "creators": ["Tool: manual-1.0.0"]
  },
  "name": "myapp-1.0.0",
  "dataLicense": "CC0-1.0",
  "documentNamespace": "https://example.com/myapp-1.0.0",
  "packages": [
    {
      "SPDXID": "SPDXRef-myapp",
      "name": "myapp",
      "version": "1.0.0",
      "downloadLocation": "NOASSERTION",
      "filesAnalyzed": false,
      "externalRefs": [
        {
          "referenceCategory": "PACKAGE-MANAGER",
          "referenceType": "purl",
          "referenceLocator": "pkg:generic/myapp@1.0.0?checksum=sha256:${ARTIFACT_SHA}"
        }
      ]
    }
  ]
}
EOF

jq '.name' myapp.spdx.json   # 驗 ✓
```

### Step 3：簽 SBOM

```bash
# 簽（不上傳 Rekor，本地練習用）
COSIGN_PASSWORD="" cosign sign-blob \
  --key cosign.key \
  --bundle myapp.spdx.json.bundle \
  --tlog-upload=false \
  myapp.spdx.json

# 驗
COSIGN_PASSWORD="" cosign verify-blob \
  --key cosign.pub \
  --bundle myapp.spdx.json.bundle \
  --insecure-ignore-tlog \
  myapp.spdx.json
# 期望：Verified OK

# 故意篡改後驗（確認保護有效）
echo "tamper" >> myapp.spdx.json
COSIGN_PASSWORD="" cosign verify-blob \
  --key cosign.pub \
  --bundle myapp.spdx.json.bundle \
  --insecure-ignore-tlog \
  myapp.spdx.json
# 期望：Error: invalid signature ...

# 還原
sed -i '/tamper/d' myapp.spdx.json
```

### Step 4：把 SBOM 打成 Attestation

```bash
# artifact 是 myapp-v1.0.0-linux-amd64，SBOM 是 predicate
COSIGN_PASSWORD="" cosign attest-blob \
  --key cosign.key \
  --predicate myapp.spdx.json \
  --type spdxjson \
  --bundle myapp.sbom-attest.bundle \
  --tlog-upload=false \
  myapp-v1.0.0-linux-amd64

# 驗
COSIGN_PASSWORD="" cosign verify-blob-attestation \
  --key cosign.pub \
  --bundle myapp.sbom-attest.bundle \
  --type spdxjson \
  --insecure-ignore-tlog \
  myapp-v1.0.0-linux-amd64
# 期望：Verified OK

# 解碼看結構（確認 subject digest 正確）
python3 << 'PYEOF'
import json, base64
bundle = json.loads(open('myapp.sbom-attest.bundle').read())
dsse = json.loads(base64.b64decode(bundle['base64Signature']))
stmt = json.loads(base64.b64decode(dsse['payload']))
print("predicateType:", stmt['predicateType'])
print("subject name:", stmt['subject'][0]['name'])
print("subject sha256:", stmt['subject'][0]['digest']['sha256'])
PYEOF

# 用 sha256sum 核對
sha256sum myapp-v1.0.0-linux-amd64
```

`subject sha256` 和 `sha256sum` 的輸出要一致。

### Step 5：手工組 SLSA Provenance

```bash
# 把你的 artifact sha256 填進去（用變數帶入）
ARTIFACT_SHA=$(sha256sum myapp-v1.0.0-linux-amd64 | awk '{print $1}')

cat > slsa-predicate.json << EOF
{
  "buildDefinition": {
    "buildType": "https://example.com/manual-build/v1",
    "externalParameters": {
      "source": "https://github.com/example/myapp",
      "ref": "refs/tags/v1.0.0"
    },
    "internalParameters": {
      "builder": "manual",
      "os": "linux"
    },
    "resolvedDependencies": [
      {
        "uri": "git+https://github.com/example/myapp@refs/tags/v1.0.0",
        "digest": {
          "gitCommit": "abc123def456789abcdef0123456789abcdef01"
        }
      }
    ]
  },
  "runDetails": {
    "builder": {
      "id": "https://example.com/manual-builder/v1"
    },
    "metadata": {
      "invocationId": "local-build-$(date +%s)",
      "startedOn": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
      "finishedOn": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    }
  }
}
EOF

# 附 provenance
COSIGN_PASSWORD="" cosign attest-blob \
  --key cosign.key \
  --predicate slsa-predicate.json \
  --type slsaprovenance1 \
  --bundle myapp.provenance.bundle \
  --tlog-upload=false \
  myapp-v1.0.0-linux-amd64

# 驗
COSIGN_PASSWORD="" cosign verify-blob-attestation \
  --key cosign.pub \
  --bundle myapp.provenance.bundle \
  --type slsaprovenance1 \
  --insecure-ignore-tlog \
  myapp-v1.0.0-linux-amd64
# 期望：Verified OK

# 解碼確認格式
python3 << 'PYEOF'
import json, base64
bundle = json.loads(open('myapp.provenance.bundle').read())
dsse = json.loads(base64.b64decode(bundle['base64Signature']))
stmt = json.loads(base64.b64decode(dsse['payload']))
print("predicateType:", stmt['predicateType'])
print("subject sha256:", stmt['subject'][0]['digest']['sha256'])
print("builder.id:", stmt['predicate']['runDetails']['builder']['id'])
print("buildType:", stmt['predicate']['buildDefinition']['buildType'])
PYEOF
```

## 卡住提示

**「cosign: command not found」**
`export PATH="$HOME/bin:$PATH"` 確認 cosign 在 `~/bin`。

**「Error: signing ... upload to tlog: user declined the prompt」**
加上 `--tlog-upload=false`。

**「invalid signature when validating ASN.1 encoded signature」**
驗章失敗，通常是 artifact 被改過，或你傳給 `verify-blob` 的檔案不是當初簽的那個。確認檔案 sha256 沒有改變。

**「subject digest mismatch」**
`verify-blob-attestation` 傳的最後一個參數要是 **artifact**（`myapp-v1.0.0-linux-amd64`），不是 SBOM（`myapp.spdx.json`）。

**「Error: provenance predicate: required field buildDefinition missing」**
`--type slsaprovenance1` 模式下，cosign 對 `--predicate` 的 JSON 做結構驗證，確認有 `buildDefinition` 和 `runDetails` 欄位。predicate JSON 不能是整個 in-toto Statement，只能是 predicate 部分。

**Python 解碼出現 KeyError: 'base64Signature'**
不同版本的 cosign 可能用不同的 bundle 格式。先印 `json.loads(open('myapp.sbom-attest.bundle').read()).keys()` 看有哪些 key，如果有 `dsseEnvelope` 而不是 `base64Signature`，把程式碼改成 `dsse = bundle['dsseEnvelope']`。

## 驗證表

完成後填這張表，確認每一列都打勾：

| 步驟 | 指令 | 期望輸出 | 通過？ |
|---|---|---|---|
| SBOM 名稱 | `jq '.name' myapp.spdx.json` | `"myapp-1.0.0"` | [ ] |
| SBOM 有 package | `jq '.packages \| length' myapp.spdx.json` | `>= 1` | [ ] |
| sign-blob 驗通 | `cosign verify-blob ... myapp.spdx.json` | `Verified OK` | [ ] |
| 篡改後驗失敗 | 改 SBOM 後驗 | `Error: invalid signature` | [ ] |
| SBOM attestation 驗通 | `cosign verify-blob-attestation --type spdxjson ...` | `Verified OK` | [ ] |
| subject digest 吻合 | Python 解碼 vs `sha256sum` | 兩個 sha256 一致 | [ ] |
| Provenance 驗通 | `cosign verify-blob-attestation --type slsaprovenance1 ...` | `Verified OK` | [ ] |
| predicateType 正確 | Python 解碼 | `https://slsa.dev/provenance/v1` | [ ] |

## 參考解答

**寫完再看！**

<details>
<summary>點開完整參考解答</summary>

### 完整可跑的腳本（一鍵跑完）

```bash
#!/usr/bin/env bash
# practice-c.sh — 需要 cosign 在 PATH，在 ~/practice-c/ 目錄下執行

set -euo pipefail
export PATH="$HOME/bin:$PATH"
cd ~/practice-c

echo "=== Step 0: 準備金鑰 ==="
if [ ! -f cosign.key ]; then
  COSIGN_PASSWORD="" cosign generate-key-pair
fi

echo "=== Step 1: 準備 artifact ==="
echo "myapp v1.0.0 fake binary" > myapp-v1.0.0-linux-amd64
ARTIFACT_SHA=$(sha256sum myapp-v1.0.0-linux-amd64 | awk '{print $1}')
echo "artifact sha256: $ARTIFACT_SHA"

echo "=== Step 2: 產 SBOM ==="
cat > myapp.spdx.json << EOF
{
  "SPDXID": "SPDXRef-DOCUMENT",
  "spdxVersion": "SPDX-2.3",
  "creationInfo": {
    "created": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
    "creators": ["Tool: manual-1.0.0"]
  },
  "name": "myapp-1.0.0",
  "dataLicense": "CC0-1.0",
  "documentNamespace": "https://example.com/myapp-1.0.0",
  "packages": [
    {
      "SPDXID": "SPDXRef-myapp",
      "name": "myapp",
      "version": "1.0.0",
      "downloadLocation": "NOASSERTION",
      "filesAnalyzed": false
    }
  ]
}
EOF
echo "SBOM name: $(jq -r '.name' myapp.spdx.json)"

echo "=== Step 3: 簽 SBOM ==="
COSIGN_PASSWORD="" cosign sign-blob \
  --key cosign.key \
  --bundle myapp.spdx.json.bundle \
  --tlog-upload=false \
  myapp.spdx.json

echo "-- 驗章 (應 Verified OK) --"
COSIGN_PASSWORD="" cosign verify-blob \
  --key cosign.pub \
  --bundle myapp.spdx.json.bundle \
  --insecure-ignore-tlog \
  myapp.spdx.json

echo "-- 篡改後驗章 (應失敗) --"
echo "tamper" >> myapp.spdx.json
COSIGN_PASSWORD="" cosign verify-blob \
  --key cosign.pub \
  --bundle myapp.spdx.json.bundle \
  --insecure-ignore-tlog \
  myapp.spdx.json 2>&1 || echo "篡改後驗章失敗，符合預期 ✓"
sed -i '/tamper/d' myapp.spdx.json

echo "=== Step 4: SBOM Attestation ==="
COSIGN_PASSWORD="" cosign attest-blob \
  --key cosign.key \
  --predicate myapp.spdx.json \
  --type spdxjson \
  --bundle myapp.sbom-attest.bundle \
  --tlog-upload=false \
  myapp-v1.0.0-linux-amd64

echo "-- 驗 attestation (應 Verified OK) --"
COSIGN_PASSWORD="" cosign verify-blob-attestation \
  --key cosign.pub \
  --bundle myapp.sbom-attest.bundle \
  --type spdxjson \
  --insecure-ignore-tlog \
  myapp-v1.0.0-linux-amd64

echo "-- 解碼 Statement --"
python3 << 'PYEOF'
import json, base64
bundle = json.loads(open('myapp.sbom-attest.bundle').read())
dsse = json.loads(base64.b64decode(bundle['base64Signature']))
stmt = json.loads(base64.b64decode(dsse['payload']))
subj_sha = stmt['subject'][0]['digest']['sha256']
print(f"  predicateType: {stmt['predicateType']}")
print(f"  subject.sha256: {subj_sha}")
PYEOF
echo "  sha256sum: $(sha256sum myapp-v1.0.0-linux-amd64 | awk '{print $1}')"

echo "=== Step 5: SLSA Provenance ==="
cat > slsa-predicate.json << EOF
{
  "buildDefinition": {
    "buildType": "https://example.com/manual-build/v1",
    "externalParameters": {
      "source": "https://github.com/example/myapp",
      "ref": "refs/tags/v1.0.0"
    },
    "internalParameters": { "builder": "manual" },
    "resolvedDependencies": [
      {
        "uri": "git+https://github.com/example/myapp@refs/tags/v1.0.0",
        "digest": { "gitCommit": "abc123def456" }
      }
    ]
  },
  "runDetails": {
    "builder": {
      "id": "https://example.com/manual-builder/v1"
    },
    "metadata": {
      "invocationId": "local-build-$$",
      "startedOn": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
      "finishedOn": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    }
  }
}
EOF

COSIGN_PASSWORD="" cosign attest-blob \
  --key cosign.key \
  --predicate slsa-predicate.json \
  --type slsaprovenance1 \
  --bundle myapp.provenance.bundle \
  --tlog-upload=false \
  myapp-v1.0.0-linux-amd64

echo "-- 驗 provenance (應 Verified OK) --"
COSIGN_PASSWORD="" cosign verify-blob-attestation \
  --key cosign.pub \
  --bundle myapp.provenance.bundle \
  --type slsaprovenance1 \
  --insecure-ignore-tlog \
  myapp-v1.0.0-linux-amd64

echo "-- 解碼 Provenance Statement --"
python3 << 'PYEOF'
import json, base64
bundle = json.loads(open('myapp.provenance.bundle').read())
dsse = json.loads(base64.b64decode(bundle['base64Signature']))
stmt = json.loads(base64.b64decode(dsse['payload']))
print(f"  predicateType: {stmt['predicateType']}")
print(f"  builder.id: {stmt['predicate']['runDetails']['builder']['id']}")
print(f"  buildType: {stmt['predicate']['buildDefinition']['buildType']}")
PYEOF

echo ""
echo "=== 全部完成 ==="
ls -lh myapp-v1.0.0-linux-amd64 myapp.spdx.json \
       myapp.spdx.json.bundle myapp.sbom-attest.bundle \
       myapp.provenance.bundle
```

執行（本機 WSL 真跑，輸出節錄）：

```
=== Step 0: 準備金鑰 ===
=== Step 1: 準備 artifact ===
artifact sha256: 598341706...
=== Step 2: 產 SBOM ===
SBOM name: myapp-1.0.0
=== Step 3: 簽 SBOM ===
Wrote bundle to file myapp.spdx.json.bundle
-- 驗章 (應 Verified OK) --
WARNING: Skipping tlog verification is an insecure practice ...
Verified OK
-- 篡改後驗章 (應失敗) --
WARNING: Skipping tlog verification ...
Error: invalid signature when validating ASN.1 encoded signature
篡改後驗章失敗，符合預期 ✓
=== Step 4: SBOM Attestation ===
Bundle wrote in the file  myapp.sbom-attest.bundle
-- 驗 attestation (應 Verified OK) --
WARNING: Skipping tlog verification ...
Verified OK
-- 解碼 Statement --
  predicateType: https://spdx.dev/Document
  subject.sha256: 59834170646c0f8d36c66fe78ff0d2444572974405f27117b91cf02d33d8157e
  sha256sum:      59834170646c0f8d36c66fe78ff0d2444572974405f27117b91cf02d33d8157e
=== Step 5: SLSA Provenance ===
Bundle wrote in the file  myapp.provenance.bundle
-- 驗 provenance (應 Verified OK) --
WARNING: Skipping tlog verification ...
Verified OK
-- 解碼 Provenance Statement --
  predicateType: https://slsa.dev/provenance/v1
  builder.id: https://example.com/manual-builder/v1
  buildType: https://example.com/manual-build/v1
=== 全部完成 ===
```

> **未實測部分（需要 GitHub Actions）**：`slsa-github-generator` reusable workflow 產出的 `.intoto.jsonl` 驗證，以及 `slsa-verifier verify-artifact` 的執行——這兩個需要 GitHub repo 和 push tag 觸發 workflow。完整指令見 Ch 23 方法 B。

</details>

## 延伸挑戰

1. **CycloneDX 版本**：把 Step 2 的 SBOM 換成 CycloneDX JSON 格式（注意 `--type cyclonedx`），重跑 Step 4，確認 `predicateType` 從 `https://spdx.dev/Document` 變成 `https://cyclonedx.org/bom`。

2. **自動化腳本**：把以上五個步驟包成一個 `practice-c.sh`，讓它能在乾淨環境一鍵執行並印出每步的 pass/fail 結論。

3. **CI 描述（不需要真跑）**：寫一份描述「如果你有 GitHub repo，要怎麼修改 Ch 23 的 workflow YAML 讓它同時產 SBOM 和 SLSA provenance 並上傳到 release」的設計文件（300 字以內）。思考兩個 job 之間的依賴關係和 `permissions` 要怎麼設定。

4. **Policy 驗證**：研究 `cosign verify-blob-attestation --policy` 的旗標，試著寫一個 CUE 或 Rego policy，要求 SBOM attestation 的 predicate 必須包含 `packages` 欄位且長度大於零。

## 自我檢核

做完你應該能回答：

- [ ] `cosign sign-blob` 的 `--bundle` 和 `--output-signature` 的差別是什麼？實際用途有什麼不同？
- [ ] 為什麼 `verify-blob-attestation` 的最後一個參數是 artifact 而不是 attestation bundle？cosign 在驗什麼？
- [ ] 這個練習產的 provenance 是 SLSA 幾 level？為什麼不是 L3？
- [ ] 如果攻擊者有你的 public key，他能偽造一份通過驗章的 SBOM attestation 嗎？（提示：他需要什麼？）
- [ ] `--tlog-upload=false` 加了以後，少了什麼保護？在什麼情境下應該去掉這個旗標？

接下來 Part 6 轉向更大的視角：SBOM 在法規、治理、企業導入層面的位置。

→ [Ch 24 法規版圖：EO 14028 / EU CRA / FDA](./24-regulations.md)
