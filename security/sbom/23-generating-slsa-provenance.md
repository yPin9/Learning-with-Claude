# Ch 23 — 生出 SLSA provenance

> **目標**：從三個角度實際產出 SLSA provenance：(a) 手工組 JSON + cosign 本地簽（真跑，理解結構），(b) GitHub Actions + slsa-github-generator reusable workflow（未實測，附完整 YAML），(c) 用 `slsa-verifier` 和 `cosign verify-blob-attestation` 驗 provenance。

## 為什麼需要這個？

Ch 22 說清楚了 SLSA provenance 的概念和 level 定義。這章回答：**「我現在要怎麼真的產一份出來？」**

有三種路線：

1. **手工組（L1）**：自己寫 JSON、用 cosign 簽。適合理解結構、本機練習、非 CI 環境。不會被 slsa-verifier 認為是 L2+，因為沒有受信 builder。
2. **GitHub Actions + slsa-github-generator（L3）**：官方推薦的生產路線，透過 reusable workflow 讓 GitHub 的基礎設施當 trusted builder，產出可被 slsa-verifier 驗的 L3 provenance。
3. **cosign attest（L1 的 OCI 版）**：對 OCI image 附 provenance，適合 container-based 供應鏈。

這三種路線的共同點是：**provenance 都是 in-toto Statement，predicateType 都是 `https://slsa.dev/provenance/v1`**，差別在 builder 是誰、能達到哪個 level。

## 先建立直覺

SLSA provenance 的 level 由誰說了算？是 `builder.id`。

```
builder.id URI
    ↓
slsa-verifier 查已知 trusted builder 清單
    ↓
  已知 → 回報對應 level（例如 GitHub Actions SLSA 3 generator → Build L3）
  未知 → Build L1（只能驗「有簽章的 provenance 存在」）
```

所以手工組的 provenance 就算格式完全正確，slsa-verifier 也只給 L1，因為沒有受信 build platform 在背後背書。L2+ 需要「trusted builder」——一個 slsa-verifier 認識的、有能力強制隔離 build 環境的 build platform。

## 方法 A：手工組 SLSA provenance + cosign 本地簽（真跑）

### Step 1：組 provenance predicate JSON

Provenance 的 `predicate` 欄位（不含 Statement 外層）長這樣（v1.0 格式，slsa.dev/spec/v1.0/provenance 的 schema）：

```bash
$ cat > slsa-predicate.json << 'EOF'
{
  "buildDefinition": {
    "buildType": "https://github.com/slsa-framework/slsa-github-generator/generic@v1",
    "externalParameters": {
      "workflow": {
        "ref": "refs/tags/v1.0.0",
        "repository": "https://github.com/example/myapp",
        "path": ".github/workflows/release.yml"
      }
    },
    "internalParameters": {
      "GITHUB_EVENT_NAME": "push"
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
      "id": "https://github.com/slsa-framework/slsa-github-generator/.github/workflows/generator_generic_slsa3.yml@v2.1.0"
    },
    "metadata": {
      "invocationId": "https://github.com/example/myapp/actions/runs/123456789",
      "startedOn": "2026-08-17T00:00:00Z",
      "finishedOn": "2026-08-17T00:05:00Z"
    }
  }
}
EOF
```

### Step 2：準備 artifact 並用 cosign attest-blob 附上 provenance

```bash
# 準備 artifact（真實場景是你的 build 產出物）
$ echo "myapp build artifact" > myapp-v1.0.0.tar.gz

# 用本地 key 附 provenance（L1：有簽章的 provenance，但 builder 是「手工」）
$ COSIGN_PASSWORD="" cosign attest-blob \
    --key cosign.key \
    --predicate slsa-predicate.json \
    --type slsaprovenance1 \
    --bundle myapp.provenance.bundle \
    --tlog-upload=false \
    myapp-v1.0.0.tar.gz
Using payload from: myapp-v1.0.0.tar.gz
Using payload from: slsa-predicate.json
Bundle wrote in the file  myapp.provenance.bundle
```

> **`--type slsaprovenance1`**：對應 SLSA v1.0 的 predicateType `https://slsa.dev/provenance/v1`。如果你用 `slsaprovenance`（無後綴 `1`），是舊版（`https://slsa.dev/provenance/v0.2`）。

### Step 3：驗 provenance 存在且簽章合法

```bash
$ COSIGN_PASSWORD="" cosign verify-blob-attestation \
    --key cosign.pub \
    --bundle myapp.provenance.bundle \
    --type slsaprovenance1 \
    --insecure-ignore-tlog \
    myapp-v1.0.0.tar.gz
WARNING: Skipping tlog verification is an insecure practice ...
Verified OK
```

### Step 4：解碼看 Statement 結構（確認格式正確）

```bash
$ python3 -c "
import json, base64
bundle = json.loads(open('myapp.provenance.bundle').read())
dsse = json.loads(base64.b64decode(bundle['base64Signature']))
stmt = json.loads(base64.b64decode(dsse['payload']))
print(json.dumps(stmt, indent=2))
"
```

真實輸出（本機實際跑出來）：

```json
{
  "_type": "https://in-toto.io/Statement/v0.1",
  "predicateType": "https://slsa.dev/provenance/v1",
  "subject": [
    {
      "name": "myapp-v1.0.0.tar.gz",
      "digest": {
        "sha256": "59834170646c0f8d36c66fe78ff0d2444572974405f27117b91cf02d33d8157e"
      }
    }
  ],
  "predicate": {
    "buildDefinition": {
      "buildType": "https://github.com/slsa-framework/slsa-github-generator/generic@v1",
      "externalParameters": {
        "workflow": {
          "path": ".github/workflows/release.yml",
          "ref": "refs/tags/v1.0.0",
          "repository": "https://github.com/example/myapp"
        }
      },
      "internalParameters": {
        "GITHUB_EVENT_NAME": "push"
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
        "id": "https://github.com/slsa-framework/slsa-github-generator/.github/workflows/generator_generic_slsa3.yml@v2.1.0"
      },
      "metadata": {
        "invocationId": "https://github.com/example/myapp/actions/runs/123456789",
        "startedOn": "2026-08-17T00:00:00Z",
        "finishedOn": "2026-08-17T00:05:00Z"
      }
    }
  }
}
```

注意：`subject[0].digest.sha256` 是 `myapp-v1.0.0.tar.gz` 的 sha256，`predicateType` 是 `https://slsa.dev/provenance/v1`。這份手工產的 provenance，格式完全符合 SLSA v1.0 schema。

## 底層機制：SLSA Provenance v1.0 各欄位的意義

```
in-toto Statement
┌──────────────────────────────────────────────────┐
│  predicateType: https://slsa.dev/provenance/v1   │
│  subject:                                        │
│    [ { name: "artifact.tar.gz",                  │
│        digest: { sha256: "..." } } ]             │
│  predicate:                                      │
│    buildDefinition:                              │
│      buildType  ──────→ 解讀 predicate 的 schema │
│      externalParameters ─→ 用戶可控的 build 輸入 │ ← 攻擊者能操控的點
│      internalParameters ─→ platform 控制的參數   │ ← 不可偽造
│      resolvedDependencies → 實際抓的依賴 + digest │ ← 追溯用
│    runDetails:                                   │
│      builder.id ──────→ 決定 SLSA level 的 URI   │ ← slsa-verifier 查的
│      metadata:                                   │
│        invocationId ──→ build run 的唯一 ID      │
│        startedOn/finishedOn → build 時間戳       │
└──────────────────────────────────────────────────┘
```

**為什麼 `builder.id` 決定 level**：slsa-verifier 有一個已知的 trusted builder 清單（embedded 在 binary 或從 GitHub 拉）。清單上的 builder 都有對應的公鑰和 SLSA level 評定。如果 `builder.id` 在清單上，驗通就回報對應 level；不在清單上，只能說「有簽章，L1」。

## 方法 B：GitHub Actions + slsa-github-generator（未實測，步驟如下）

這是官方推薦達到 **Build L3** 的路線。slsa-github-generator 是 OpenSSF 維護的一組 GitHub Actions reusable workflow，它透過 GitHub 的 ephemeral OIDC token 做 keyless 簽章，並把 provenance 附在 release assets 裡。

預期達到：**SLSA Build L3**（GitHub Actions 基礎設施當 trusted builder，build 環境隔離，signing key 對 user build steps 不可見）。

### 完整 Workflow YAML

```yaml
# .github/workflows/release.yml
name: Release with SLSA Provenance

on:
  push:
    tags:
      - 'v*'

permissions:
  # 最小化全域權限
  contents: read

jobs:
  # Step 1：Build artifact 並計算 SHA256
  build:
    runs-on: ubuntu-latest
    outputs:
      hashes: ${{ steps.hash.outputs.hashes }}
    steps:
      - uses: actions/checkout@v4

      - name: Build artifact
        run: |
          # 你的真實 build 指令，例如：
          make release
          # 或：
          go build -o myapp-${{ github.ref_name }}-linux-amd64 ./cmd/myapp

      - name: Compute SHA256 hashes
        id: hash
        run: |
          # 格式：sha256sum 的輸出（"HASH  FILENAME\n[...]"），base64 編碼
          HASHES=$(sha256sum myapp-* | base64 -w0)
          echo "hashes=$HASHES" >> "$GITHUB_OUTPUT"

      - name: Upload artifact
        uses: actions/upload-artifact@v4
        with:
          name: artifacts
          path: myapp-*

  # Step 2：呼叫 slsa-github-generator reusable workflow
  # 這個 job 由 slsa-github-generator 的 trusted build platform 執行
  # MUST 使用精確的 @vX.Y.Z tag（不能用 @main 或 commit hash）
  provenance:
    needs: [build]
    permissions:
      actions: read       # 讓 generator 讀取 workflow run 資訊
      id-token: write     # 讓 generator 取 OIDC token 做 keyless 簽章
      contents: write     # 讓 generator 上傳 provenance 到 release
    uses: slsa-framework/slsa-github-generator/.github/workflows/generator_generic_slsa3.yml@v2.1.0
    with:
      base64-subjects: "${{ needs.build.outputs.hashes }}"
      # 選填：自訂 provenance 檔案名稱（預設 artifact名.intoto.jsonl）
      # provenance-name: "myapp.intoto.jsonl"
      # 選填：同時上傳 artifact 到 release
      upload-assets: true

  # Step 3（選填）：把 artifact 發布到 GitHub Release
  release:
    needs: [build, provenance]
    permissions:
      contents: write
    runs-on: ubuntu-latest
    steps:
      - name: Download artifacts
        uses: actions/download-artifact@v4
        with:
          name: artifacts

      - name: Create GitHub Release
        uses: softprops/action-gh-release@v2
        with:
          files: |
            myapp-*
            # provenance 已由 slsa-github-generator 的 upload-assets 處理
```

**預期產出**（在 GitHub Release assets 裡）：

```
myapp-v1.0.0-linux-amd64          ← build 產物
myapp-v1.0.0-linux-amd64.intoto.jsonl  ← SLSA provenance（JSONL 格式的 bundle）
```

`.intoto.jsonl` 是 in-toto bundle 的一行 JSON，用 keyless 簽章（不需要管理私鑰）。

### 驗 GitHub Actions 產出的 L3 Provenance

用 `slsa-verifier`（未實測，需要真實 GitHub release）：

```bash
# 安裝 slsa-verifier
curl -sSL -o ~/bin/slsa-verifier \
  "https://github.com/slsa-framework/slsa-verifier/releases/download/v2.7.0/slsa-verifier-linux-amd64"
chmod +x ~/bin/slsa-verifier

# 驗 provenance（會連去 Rekor 查 keyless 簽章）
slsa-verifier verify-artifact \
  --provenance-path myapp-v1.0.0-linux-amd64.intoto.jsonl \
  --source-uri github.com/example/myapp \
  myapp-v1.0.0-linux-amd64

# 預期輸出：
# Verified signature against tlog entry index 12345678 at URL: https://rekor.sigstore.dev/api/v1/log/entries/...
# Verified build using builder https://github.com/slsa-framework/slsa-github-generator/.github/workflows/generator_generic_slsa3.yml@v2.1.0 at SLSA level 3
# PASSED: SLSA verification passed
```

`--source-uri` 告訴 verifier 「這個 artifact 宣稱從哪個 repo 來的」，如果 provenance 裡的 repository 和這個不符，驗章失敗。

## 方法 C：對 OCI Image 附 SLSA Provenance（未實測）

對 container image 的 CI 流程：

```bash
# 假設 image 已 build 並 push 到 registry
IMAGE=registry.example.com/myapp:v1.0.0@sha256:abc123...

# 用本地 key 附 provenance
cosign attest \
  --key cosign.key \
  --predicate slsa-predicate.json \
  --type slsaprovenance1 \
  ${IMAGE}

# 驗 provenance
cosign verify-attestation \
  --key cosign.pub \
  --type slsaprovenance1 \
  ${IMAGE} | jq '.[0].payload | @base64d | fromjson | .predicate.buildDefinition'
```

預期輸出（verify-attestation 成功時，最後的 jq 抽出 buildDefinition）：

```json
{
  "buildType": "https://...",
  "externalParameters": { ... },
  "resolvedDependencies": [ ... ]
}
```

## 底層機制：ASCII 流程圖

```
方法 A（本機 / L1）
─────────────────────────────────────────────────────
artifact  ──sha256──▶  subject digest
                              ↓
slsa-predicate.json  ──▶  cosign attest-blob ──▶  .bundle
                              ↑
                          cosign.key（本地）
                              ↓
slsa-verifier 驗：builder.id 不在 trusted list → L1

方法 B（GitHub Actions / L3）
─────────────────────────────────────────────────────
                      GitHub Actions
  build job       slsa-github-generator        Fulcio / Rekor
 ──────────        ───────────────────         ──────────────
  build artifact  │  生 provenance           │  OIDC token
  輸出 hash ───▶  │  builder.id = generator  │  ← 換成短期憑證
                  │  subject = artifact hash  │  → 簽 provenance
                  │  keyless 簽章 ──────────▶  │
                  │  .intoto.jsonl ◀──────────  Rekor 留記錄
                              ↓
  slsa-verifier 驗：builder.id 在 trusted list → L3
```

## 對比與取捨

| 方法 | SLSA Level | 需要 | 簽章方式 | 驗方工具 |
|---|---|---|---|---|
| 手工 + cosign 本地 key | L1 | cosign key pair | 本地 key | cosign verify-blob-attestation |
| slsa-github-generator | L3 | GitHub repo + Actions | keyless（OIDC） | slsa-verifier v2.x |
| cosign attest（OCI） | 視 builder 而定 | OCI registry | 本地 key 或 keyless | cosign verify-attestation |
| 其他 CI（Tekton / Google Cloud Build） | L2/L3 視 builder | 對應 CI 平台 | 平台管理 | slsa-verifier（需支援該 builder） |

## 踩雷集錦

1. **`slsa-github-generator` 的 workflow ref 必須是精確 tag（`@v2.1.0`），不能是 `@main`**：這是設計上的強制要求。用 `@main` 會讓 workflow 無法被 slsa-verifier 驗證（verifier 比對 ref 是否是 immutable tag）。

2. **`--type slsaprovenance1` 不等於 `--type slsaprovenance`**：cosign 對應的 predicateType 分別是 `https://slsa.dev/provenance/v1` 和 `https://slsa.dev/provenance/v0.2`。SLSA v1.0 要用 `slsaprovenance1`；如果驗方期望 `slsaprovenance` 你卻用 `slsaprovenance1`，type 不符合驗章失敗。

3. **手工組的 provenance 中 `subject.digest` 必須是 artifact 的真實 sha256**：cosign attest-blob 會自己算 subject artifact 的 sha256 填進 Statement，不是你在 predicate JSON 裡填的——predicate JSON 只有 buildDefinition 和 runDetails，subject 是 cosign 從 artifact 算出來的。如果你嘗試自己包整個 Statement 當 predicate 傳進去，會出現巢狀 Statement 的問題（參見 Ch 21 踩雷 2）。

4. **slsa-verifier v1.x 不能驗 v1.0 格式**：slsa-verifier 2.x 才支援 SLSA v1.0 的 provenance 格式。如果裝了舊版本的 slsa-verifier，驗 slsa-github-generator 產的 `.intoto.jsonl` 會失敗（`unknown predicate type`）。裝之前確認版本。

5. **`base64-subjects` 的格式要精確**：slsa-github-generator 的 `base64-subjects` 輸入是 `sha256sum` 輸出格式（`HASH  FILENAME\n`）的 base64，不是只有 hash。打錯格式 generator 會靜靜失敗產出空的 provenance，不會報清楚的錯。用 `echo "$HASHES" | base64 -d | head` 驗一下格式對不對。

## 進階：再往深一層

**slsa-verifier 的 `--source-tag` 和 `--source-branch` 驗證**

除了 `--source-uri`，slsa-verifier 還支援進一步限縮：

```bash
slsa-verifier verify-artifact \
  --provenance-path myapp.intoto.jsonl \
  --source-uri github.com/example/myapp \
  --source-tag v1.0.0 \       # 確認是從這個 tag build 的
  myapp-v1.0.0-linux-amd64
```

加了 `--source-tag` 之後，就算有人用同一個 repo 的另一個 tag build 出一個 artifact 附上合法 provenance，用這份 provenance 驗它也會失敗，因為 tag 不符。

**Provenance 的消費：Dependency-Track 整合**

Dependency-Track（Ch 17）有 Beta 支援匯入 SLSA provenance，把 build 來源資訊附在 component 旁邊。目前整合還不成熟，但方向是「SBOM 告訴你有什麼，provenance 告訴你每個元件怎麼來的，放在同一個工具裡讓安全團隊一次看到全貌」。

**多平台 provenance**

如果你 build 了 linux/amd64 和 linux/arm64 兩個 binary，可以一個 slsa-github-generator 呼叫同時涵蓋兩個（把兩個 sha256 都放進 `base64-subjects`）。產出的 provenance 的 `subject` 陣列會有兩個元素，slsa-verifier 驗時要指定對的那個 artifact。

## 動手練習

1. 跑方法 A 的完整流程（Step 1–4），用 `jq` 從解碼後的 Statement 中抽出 `predicate.runDetails.builder.id`，確認是你填的那個值。
2. 修改 `slsa-predicate.json`，把 `buildType` 改成一個虛構的 URI（`https://my-fake-builder.example.com/v1`），重跑 attest-blob + verify-blob-attestation，確認 cosign 本地驗章仍然通過（說明：cosign 只驗簽章，不驗 buildType 的內容）。
3. 如果你有 GitHub repo，按方法 B 的 workflow YAML 建 `.github/workflows/release.yml`，推一個 tag，確認 release assets 裡出現 `.intoto.jsonl` 檔（你不需要真的跑 slsa-verifier 驗，先看 provenance 檔案有沒有產出來）。

## 本章重點整理

- SLSA provenance 有三種產法：手工（L1）、slsa-github-generator reusable workflow（L3）、OCI image attest。
- 手工版用 `cosign attest-blob --type slsaprovenance1`，能驗簽章，但 `builder.id` 不在 trusted list，slsa-verifier 只認 L1。
- slsa-github-generator 的 workflow ref **必須** 是精確 tag（`@v2.1.0`），用 keyless OIDC 簽章，不需要管理私鑰，達到 Build L3。
- `slsa-verifier verify-artifact` 是驗 L2/L3 provenance 的標準工具，`cosign verify-blob-attestation` 適合驗本地 key 簽的。
- Predicate JSON 只包含 `buildDefinition` + `runDetails`；`subject` 是 cosign 從 artifact 算出來自動填入的，不要在 predicate 裡重複寫。

## 自我檢核

- [ ] 我能在本機手工產一份 SLSA v1.0 provenance 並用 cosign 驗通
- [ ] 我能解碼 `.bundle` 並確認 `predicateType` 是 `https://slsa.dev/provenance/v1`
- [ ] 我能說出為什麼手工產的 provenance 只有 L1，而 slsa-github-generator 能達到 L3
- [ ] 我知道 `--type slsaprovenance1` 對應哪個 predicateType URI
- [ ] 我知道 slsa-github-generator workflow 的 `base64-subjects` 應該填什麼格式

## 延伸閱讀

- **[slsa-github-generator Generic Generator README](https://github.com/slsa-framework/slsa-github-generator/blob/main/internal/builders/generic/README.md)**（OpenSSF 官方）
  - **讀哪裡**：「Getting started」的 workflow YAML，以及 `base64-subjects` 格式說明
  - **和本章的關聯**：本章的 workflow YAML 從這裡簡化而來，原版有更多選項說明

- **[slsa-verifier README](https://github.com/slsa-framework/slsa-verifier)**（OpenSSF 官方）
  - **讀哪裡**：`verify-artifact` 的 `--source-uri`、`--source-tag`、`--source-branch` 旗標說明
  - **為什麼值得讀**：了解 verifier 用哪些條件決定要不要信 provenance，比「驗通就好」多一層理解

- **[SLSA v1.0 Provenance schema](https://slsa.dev/spec/v1.0/provenance)**（slsa.dev 官方）
  - **讀哪裡**：`buildDefinition` 各子欄位的「Required for Build Lx」標注，以及 `resolvedDependencies` 的用途說明
  - **和本章的關聯**：本章的 predicate JSON 格式直接按這份 schema 寫

- **[cosign attest-blob 文件](https://github.com/sigstore/cosign/blob/main/doc/cosign_attest-blob.md)**（sigstore GitHub）
  - **讀哪裡**：`--type` 支援的值列表（所有 predicate type alias），以及 `--predicate` 格式說明

- **[in-toto attestation spec v1](https://github.com/in-toto/attestation/blob/main/spec/v1/statement.md)**（in-toto 官方）
  - **讀哪裡**：Statement v1 vs v0.1 的差異（`type` vs `_type`），讓你讀 bundle 時不被格式差異搞混

下一步：把本章的技術整合起來，對你自己 build 的 image 做「產 SBOM → 簽 SBOM → 附 provenance → 驗整條信任鏈」的完整練習。

→ [練習 C signed SBOM + SLSA provenance](./practice-c-signed-sbom-provenance.md)
