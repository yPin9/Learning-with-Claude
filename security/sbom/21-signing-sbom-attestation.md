# Ch 21 — 簽 SBOM 與 attestation

> **目標**：用 cosign 對一份 SBOM 簽章，再進一步把 SBOM 包成 in-toto attestation 綁到 artifact 上。學完你能分清「簽一個檔案」和「把一份聲明（predicate）釘到特定 artifact（subject）」的差別，並知道為什麼後者是更強的信任保證。

## 為什麼需要這個？

Ch 20 講完了 sigstore 的原理：cosign 能簽 OCI image，keyless 靠 OIDC + Rekor 做無長期金鑰的簽章。但 SBOM 不一定跟 image 存在同一個 registry，它可能是一個獨立的 `.json` 檔案，附在 release asset 裡、或推進一個 SBOM 資料庫。

這裡有兩個層次的需求，值得分開想：

**第一層：完整性保護（簽 blob）**
你發布一份 SBOM，接收方怎麼知道它沒被篡改、是你親手產的？答案是對 SBOM 檔案簽章，接收方用你的公鑰驗。`cosign sign-blob` 做的就是這件事：對任意檔案內容的 hash 簽名，產出一個 `.sig` 簽章檔或 `.bundle` 束檔。

**第二層：綁定（attestation）**
更強的需求是：這份 SBOM 描述的是「那個特定 artifact（用 digest 指認）」，不是什麼 artifact 都適用。光簽一個 SBOM 檔案，無法防止有人把同一份 SBOM 貼到另一個 artifact 上。

Attestation 解決這個問題：它是一份「關於 artifact 的帶簽章聲明」，格式是 in-toto Statement，結構是：

```
artifact (subject, 用 sha256 digest 指認)
  ↑
  ‖ 綁定：這份 SBOM 描述的是這個 artifact
  ↓
SBOM (predicate)
  ↑
  ‖ 簽章：是我（特定 key 或 OIDC 身分）聲明的
  ↓
signature
```

任何人拿到這個 attestation，可以同時驗：(a) digest 對得上 artifact，(b) 簽章是合法的 key 發的。兩件事都驗到，才能信「這份 SBOM 描述的就是這個 artifact，且來自可信的人」。

## 先建立直覺

想像你是軟體供應商，每次 release 都產一份 SBOM 附在 GitHub release assets 裡。攻擊者可以做兩件事：

1. **竄改你的 SBOM**：把某個有漏洞的元件從清單裡刪掉，讓掃描器找不到。
2. **換包 SBOM**：把你描述 v1.0 的 SBOM 換成描述 v0.9（已知有漏洞版本）的 SBOM，讓使用者以為他裝的 v1.0 有某個漏洞，觸發不必要的警報或誤導修補。

- **簽 blob** 解決攻擊 1：竄改後驗章失敗。
- **Attestation** 解決攻擊 1 + 2：SBOM 被綁在特定 artifact digest 上，換包無效。

一份 attestation 在 in-toto 規範裡長這樣（這是真實的 in-toto Statement/v0.1 或 v1 格式）：

```json
{
  "_type": "https://in-toto.io/Statement/v0.1",
  "predicateType": "https://spdx.dev/Document",
  "subject": [
    {
      "name": "myapp-v1.0.0.tar.gz",
      "digest": {
        "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
      }
    }
  ],
  "predicate": {
    /* 這裡放 SBOM 的完整 JSON */
  }
}
```

- `subject`：說「我在聲明的是哪個 artifact」，用 sha256 digest 指認，名稱是輔助資訊。
- `predicateType`：說「我的 predicate 是什麼格式」，SBOM 是 `https://spdx.dev/Document` 或 `https://cyclonedx.org/bom`，SLSA provenance 是 `https://slsa.dev/provenance/v1`（Ch 22-23 的主題）。
- `predicate`：實際的聲明內容，這裡放整份 SBOM。

## 底層機制：DSSE 封裝

Attestation 的簽章格式是 **DSSE（Dead Simple Signing Envelope）**，in-toto v0.2+ 引入，cosign 預設用它。DSSE 的結構：

```
DSSE Envelope
┌─────────────────────────────────────────────┐
│  payloadType : "application/vnd.in-toto+json"│
│  payload     : base64(in-toto Statement JSON)│
│  signatures  : [                             │
│    { keyid: "...", sig: base64(sign(PAE))  } │
│  ]                                           │
└─────────────────────────────────────────────┘

PAE (Pre-Authentication Encoding) =
  "DSSEv1" + " " + len(payloadType) + " " + payloadType
  + " " + len(payload) + " " + payload
```

`PAE` 是真正被簽的內容，包含了 payloadType——這確保簽章不能被跨型別重放（你不能把一份「簽過 SBOM」的 DSSE 換成「簽過 provenance」來欺騙驗證方）。

cosign 會把整個 DSSE 再 base64 一次，存進 bundle 的 `base64Signature` 欄位：

```
cosign .bundle 檔
{
  "base64Signature": base64(DSSE Envelope JSON),
  "cert": base64(公鑰或 Fulcio 憑證)
}
```

## 動手：簽 SBOM blob（真跑）

先準備環境。如果沒有 cosign key pair，產一對：

```bash
$ mkdir -p ~/sbom-demo && cd ~/sbom-demo
$ COSIGN_PASSWORD="" cosign generate-key-pair
Private key written to cosign.key
Public key written to cosign.pub
```

產一份 SBOM（這裡用手寫的最小 SPDX JSON 做示範；真實環境用 `syft` 產）：

```bash
$ cat > myapp.spdx.json << 'EOF'
{
  "SPDXID": "SPDXRef-DOCUMENT",
  "spdxVersion": "SPDX-2.3",
  "creationInfo": {
    "created": "2026-08-17T00:00:00Z",
    "creators": ["Tool: syft-1.51.0"]
  },
  "name": "myapp-1.0",
  "dataLicense": "CC0-1.0",
  "documentNamespace": "https://example.com/myapp-1.0",
  "packages": [
    {
      "SPDXID": "SPDXRef-libssl",
      "name": "libssl",
      "version": "3.0.2",
      "downloadLocation": "NOASSERTION",
      "filesAnalyzed": false
    }
  ]
}
EOF
```

**簽章**：`--tlog-upload=false` 跳過上傳 Rekor（本地 key 離線用，不需要透明日誌；生產環境建議不加這個旗標，讓 Rekor 留下記錄）：

```bash
$ COSIGN_PASSWORD="" cosign sign-blob \
    --key cosign.key \
    --bundle myapp.spdx.json.bundle \
    --tlog-upload=false \
    myapp.spdx.json
Using payload from: myapp.spdx.json
Wrote bundle to file myapp.spdx.json.bundle
MEQCIHqSx2WpzO/wi/SWqCTG807H8H0ENwdvEhE1pA4o4WJOAiBpchCdDzXRQfXFI+F9jAsGnE2iPFHZsrT0SzHEuyW/SQ==
```

最後印的那串 base64 是簽章本身；bundle 檔把簽章和公鑰資訊打包在一起，方便一起分發。

**驗章**：

```bash
$ COSIGN_PASSWORD="" cosign verify-blob \
    --key cosign.pub \
    --bundle myapp.spdx.json.bundle \
    --insecure-ignore-tlog \
    myapp.spdx.json
WARNING: Skipping tlog verification is an insecure practice ...
Verified OK
```

**刻意篡改後驗章**（驗它確實在保護完整性）：

```bash
$ echo "tamper" >> myapp.spdx.json
$ COSIGN_PASSWORD="" cosign verify-blob \
    --key cosign.pub \
    --bundle myapp.spdx.json.bundle \
    --insecure-ignore-tlog \
    myapp.spdx.json
Error: invalid signature when validating ASN.1 encoded signature
```

篡改後立刻驗失敗，符合預期。

如果你想要 `.sig` 和 `.sbom` 分開分發（常見做法）：

```bash
$ COSIGN_PASSWORD="" cosign sign-blob \
    --key cosign.key \
    --output-signature myapp.spdx.json.sig \
    --tlog-upload=false \
    myapp.spdx.json

$ COSIGN_PASSWORD="" cosign verify-blob \
    --key cosign.pub \
    --signature myapp.spdx.json.sig \
    --insecure-ignore-tlog \
    myapp.spdx.json
Verified OK
```

## 動手：把 SBOM 打成 attestation（真跑）

`cosign attest-blob` 把 predicate（你的 SBOM）和 subject（你要保護的 artifact）打包成 in-toto Statement，再用 DSSE 簽起來：

```bash
# subject：你要保護的 artifact（不是 SBOM 本身，是 SBOM 描述的那個東西）
$ echo "myapp build output" > myapp-v1.0.0.tar.gz

# predicate：你的 SBOM（--type spdxjson 告訴 cosign predicateType 是什麼）
$ COSIGN_PASSWORD="" cosign attest-blob \
    --key cosign.key \
    --predicate myapp.spdx.json \
    --type spdxjson \
    --bundle myapp.attestation.bundle \
    --tlog-upload=false \
    myapp-v1.0.0.tar.gz
Using payload from: myapp-v1.0.0.tar.gz
Using payload from: myapp.spdx.json
Bundle wrote in the file  myapp.attestation.bundle
```

解碼 bundle 看 in-toto Statement 結構（確認 subject 是 artifact，predicate 是 SBOM）：

```bash
$ python3 -c "
import json, base64
bundle = json.loads(open('myapp.attestation.bundle').read())
dsse = json.loads(base64.b64decode(bundle['base64Signature']))
stmt = json.loads(base64.b64decode(dsse['payload']))
print(json.dumps(stmt, indent=2))
"
{
  "_type": "https://in-toto.io/Statement/v0.1",
  "predicateType": "https://spdx.dev/Document",
  "subject": [
    {
      "name": "myapp-v1.0.0.tar.gz",
      "digest": {
        "sha256": "59834170646c0f8d36c66fe78ff0d2444572974405f27117b91cf02d33d8157e"
      }
    }
  ],
  "predicate": {
    "SPDXID": "SPDXRef-DOCUMENT",
    "creationInfo": { ... },
    "name": "myapp-1.0",
    ...
  }
}
```

`subject[0].digest.sha256` 是 `myapp-v1.0.0.tar.gz` 的 hash，不是 SBOM 的 hash——這就是「綁定」的意思。

**驗 attestation**：

```bash
$ COSIGN_PASSWORD="" cosign verify-blob-attestation \
    --key cosign.pub \
    --bundle myapp.attestation.bundle \
    --type spdxjson \
    --insecure-ignore-tlog \
    myapp-v1.0.0.tar.gz
WARNING: Skipping tlog verification is an insecure practice ...
Verified OK
```

cosign 內部做了三件事：(1) 驗 DSSE 簽章，(2) 算 `myapp-v1.0.0.tar.gz` 的 sha256 確認對得上 subject，(3) 確認 predicateType 是 `spdxjson`。三件事全過才印 `Verified OK`。

## OCI Image 的 attestation：cosign attest（未實測，步驟如下）

如果 artifact 是推進 registry 的 container image（最常見的情境），cosign 支援直接把 attestation 存進 OCI registry，用 OCI referrers API 掛在 image 旁邊：

```bash
# 假設 image 已 push 到 registry
IMAGE=registry.example.com/myapp:v1.0.0

# 生 SBOM（用 syft）
syft ${IMAGE} -o spdx-json > myapp.spdx.json

# 把 SBOM 當 predicate 附到 image（keyless，需 OIDC）
# 未實測——需要 GitHub Actions / Workload Identity 等 OIDC 環境
cosign attest \
  --predicate myapp.spdx.json \
  --type spdxjson \
  ${IMAGE}

# 用本地 key 也行
cosign attest \
  --key cosign.key \
  --predicate myapp.spdx.json \
  --type spdxjson \
  ${IMAGE}

# 驗 attestation
cosign verify-attestation \
  --key cosign.pub \
  --type spdxjson \
  ${IMAGE}
```

預期輸出（`verify-attestation` 成功時）：

```
Verification for registry.example.com/myapp:v1.0.0 --
The following checks were performed on each of these signatures:
  - The cosign claims were validated
  - The signatures were verified against the specified public key

[{"payloadType":"application/vnd.in-toto+json","payload":"...","signatures":[...]}]
```

`verify-attestation` 最後會把 attestation 的 JSON 印出來，你可以 `| jq '.[0].payload | @base64d | fromjson'` 取出 Statement 看 predicate 內容。

> **OCI referrers 是什麼**：attestation 不是塞進 image layer，而是用 OCI image-spec 的 referrers API（`/v2/<name>/referrers/<digest>`）存成一個「指向原 image 的附屬物件」。任何人 pull 原 image 後，可以用 `cosign verify-attestation` 或 `oras discover` 找到與它關聯的 attestation，不需要額外的 side channel。

## 底層機制：attestation 與簽 blob 的技術差異

```
sign-blob（檔案簽章）              attest-blob（attestation）
─────────────────────              ──────────────────────────
輸入：任意檔案                      輸入：subject artifact + predicate 檔案
簽的是：hash(檔案內容)              簽的是：DSSE(in-toto Statement)
輸出：.sig 或 .bundle               輸出：.bundle（含完整 Statement）
驗章問題：「這個檔沒被改」            驗章問題：「這份 SBOM 描述的是這個
                                             artifact，且由特定 key 聲明」
綁定性：無（同一簽章可附到任何地方）   綁定性：有（subject digest 固定）
格式標準：無（cosign 自訂）           格式標準：in-toto Statement + DSSE
```

## 對比與取捨

| 方法 | 優點 | 缺點 | 適用情境 |
|---|---|---|---|
| 只發 SBOM，不簽 | 零成本 | 無法驗完整性，無法驗來源 | 內部工具，信任閉環 |
| `sign-blob`（`.sig`） | 簡單，任何人能驗 | 無 subject 綁定 | SBOM 獨立分發，補簽章即可 |
| `sign-blob`（`.bundle`） | `.sig` + 公鑰一包打 | 同上，無綁定 | 方便接收方，不需要額外查公鑰 |
| `attest-blob`（DSSE） | 強綁定，in-toto 標準格式 | 驗方需要 cosign 或懂 DSSE | 高可信度分發，接 slsa-verifier |
| `cosign attest`（OCI） | 跟 image 存一起，自動發現 | 需要 OCI registry 支援 referrers | container-based 供應鏈 |
| keyless（OIDC）attest | 不需要管理長期金鑰 | 需要 OIDC provider（GitHub Actions 等）| CI/CD 生產環境 |

## 踩雷集錦

1. **`cosign attest` 和 `cosign attest-blob` 是不同指令**：`attest` 對 OCI image（存進 registry）；`attest-blob` 對任意本地檔案（存進 bundle）。混用會得到 `failed to get manifest` 之類的 registry 連線錯誤，跟 SBOM 本身無關。

2. **`--predicate` 只接受 predicate 的內容，不是整個 in-toto Statement**：cosign 會自己把 predicate 包進 Statement（加 subject、predicateType 等外層）。如果你把整個 Statement JSON 當 `--predicate` 丟進去，它會被塞成 `predicate` 欄位裡面的內容，結構變成兩層巢狀 Statement，驗章時通常還是過，但解析出來的結構不對。

3. **`--type spdxjson` 的字串要精確**：cosign 支援的型別名有 `spdxjson`、`cyclonedx`、`vuln`、`slsaprovenance`、`slsaprovenance1` 等。大小寫和有無後綴都有差，打錯會得到 `unsupported predicate type` 錯誤。

4. **驗 blob attestation 時 subject 要傳對**：`cosign verify-blob-attestation` 的最後一個參數是 **subject artifact**（`myapp-v1.0.0.tar.gz`），不是 SBOM 檔案。cosign 會自己算它的 sha256 去比對 Statement 裡的 subject digest。如果傳錯檔案，sha256 對不上，驗章失敗，報 `subject digest mismatch` 類的錯誤，跟簽章本身無關。

5. **keyless 需要 OIDC 環境**：`cosign attest --key` 是用本地 key，能本機跑；不加 `--key` 預設走 keyless OIDC，需要在 GitHub Actions / Cloud Run / 其他有 OIDC token 的環境裡才能執行。本機跑 keyless 會卡在要你打開瀏覽器登入。

## 進階：再往深一層

**Policy 驗證（CUE / Rego）**

cosign 驗 attestation 時可以加 `--policy` 指定一個 CUE 或 Rego 規則，讓驗章同時做內容稽核：

```bash
# 用 CUE 規則確認 SBOM 的 predicate 包含特定欄位
cosign verify-attestation \
  --key cosign.pub \
  --type spdxjson \
  --policy sbom-policy.cue \
  ${IMAGE}
```

```cue
// sbom-policy.cue：要求 SBOM 至少有 packages 欄位
predicate: {
  packages: [...] & len(packages) > 0
}
```

這讓「驗 attestation」從「有沒有」變成「有沒有、且符合規格」。

**多 subject**

一個 attestation 可以宣告多個 subject（例如同一次 build 的 tar.gz + checksums 文件）：

```bash
# 計算兩個 artifact 的 hash 並傳給 cosign（需用 OCI image 模式或手動建 Statement）
# attest-blob 目前每次只能一個 subject，多 subject 需手動組 Statement 再簽
```

**Rekor 透明日誌的作用**

如果不加 `--tlog-upload=false`，cosign 會把簽章記錄上傳到 Rekor（公開 append-only 日誌）。好處是接收方可以查 Rekor 確認「這份簽章確實在某時間點存在」，防止 key 洩露後被用來回填假簽章。生產環境不要跳過 Rekor——`--insecure-ignore-tlog` 是本地開發偷懶用的旗標，不是建議做法。

## 動手練習

1. 建一個最小的 CycloneDX JSON SBOM（可以直接 `echo '{...}'` 寫一個假的），用 `cosign sign-blob` 簽，驗通，然後篡改一個字元，確認驗失敗。
2. 用 `cosign attest-blob` 把那份 CycloneDX SBOM 當 predicate 綁到一個 artifact，用 Python 解碼 bundle 確認 subject digest 對得上 artifact 的 sha256（`sha256sum <artifact>`）。
3. `--type` 換成 `cyclonedx`，重做一遍，比較 `predicateType` 欄位的值有什麼不同。

## 本章重點整理

- `cosign sign-blob` 對任意檔案簽章，驗完整性；`cosign attest-blob` 把 SBOM 打成 in-toto attestation，同時驗完整性 + subject 綁定。
- Attestation 的格式是 **in-toto Statement**，封裝格式是 **DSSE（Dead Simple Signing Envelope）**。
- `subject` 用 sha256 digest 指認 artifact，`predicate` 是 SBOM 內容，`predicateType` 標明 SBOM 格式。
- `cosign attest`（無 blob 後綴）把 attestation 存進 OCI registry，讓任何人 pull image 時都能找到附屬的 SBOM attestation。
- 生產環境讓 Rekor 留記錄；本地開發才用 `--tlog-upload=false` / `--insecure-ignore-tlog`。

## 自我檢核

- [ ] 我能解釋「簽 SBOM blob」跟「把 SBOM 打成 attestation」的差別，以及各自防止什麼攻擊
- [ ] 我能說出 in-toto Statement 的三個主要欄位（`subject`、`predicateType`、`predicate`）各是什麼
- [ ] 我知道 DSSE 裡真正被簽的是什麼（PAE，包含 payloadType）
- [ ] 我在本機成功執行 `cosign sign-blob` → `cosign verify-blob` → 篡改 → 驗失敗這個迴圈
- [ ] 我能在本機執行 `cosign attest-blob` 並解碼 bundle 確認 subject digest

## 延伸閱讀

- **[cosign attest 文件](https://docs.sigstore.dev/cosign/verifying/attestation/)**（sigstore 官方）
  - **讀哪裡**：`cosign attest` 與 `cosign verify-attestation` 的完整旗標說明，尤其是 `--policy` 那節
  - **和本章的關聯**：本章的 OCI attestation 指令就從這裡來

- **[in-toto 規範：Statement 格式](https://github.com/in-toto/attestation/blob/main/spec/v1/statement.md)**（in-toto 官方）
  - **讀哪裡**：Statement v1 的 `subject`、`predicateType`、`predicate` 欄位定義；DSSE 那節
  - **為什麼值得讀**：本章用的 `v0.1` 是舊版，v1 把 `_type` 改成 `type`；了解兩者差異，讀 bundle 時才不會困惑

- **[DSSE 規範](https://github.com/secure-systems-lab/dsse)**（Secure Systems Lab）
  - **讀哪裡**：一頁的 spec，重點是 PAE 的定義和為什麼需要 payloadType 防重放
  - **為什麼值得讀**：任何你解碼 bundle 時看到的 `payloadType` / `payload` / `signatures` 結構，都是這份 spec 規定的

- **[OCI Referrers API](https://github.com/opencontainers/distribution-spec/blob/main/spec.md#listing-referrers)**（OCI 官方）
  - **讀哪裡**：`/v2/<name>/referrers/<digest>` 這個 endpoint 的行為定義
  - **和本章的關聯**：`cosign attest` 對 image 時，attestation 就是靠這個 API 附到 image 旁邊的

- **[cosign sign-blob / verify-blob 完整旗標](https://github.com/sigstore/cosign/blob/main/doc/cosign_sign-blob.md)**（sigstore GitHub）
  - **讀哪裡**：`--bundle`、`--output-signature`、`--output-certificate` 這幾個輸出格式旗標的說明，以及 `--tlog-upload` 的生產 vs. 開發建議

下一章聚焦「build 過程本身的可信度」：SBOM 說「你的軟體裡有什麼」，但攻擊者也可以在 build 過程中植入後門，讓 SBOM 誠實地列出了一個有毒的元件。SLSA 的核心問題是：怎麼證明「artifact 確實是從這個 source、用這個方式 build 出來的，沒有被動過手腳」？

→ [Ch 22 SLSA framework](./22-slsa-framework.md)
