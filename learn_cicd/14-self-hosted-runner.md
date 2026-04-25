# Ch 14 — Self-hosted runner 與部署

> 目標：知道什麼時候真的需要 self-hosted runner、為什麼 public repo 絕對不能開、以及一條從 GHCR image 到真實部署的最簡路徑。

## Self-hosted runner 是什麼

預設 Actions 的 runner 是 GitHub 提供的 Azure VM（`ubuntu-latest` 這些）。Self-hosted 就是你自己準備一台機器、裝 runner agent、註冊到 repo / org：

```
你的機器 ──連線→ GitHub
            ←job 推給你
你的機器跑 job
            →結果回報 GitHub
```

GitHub 把 job 下發給你的機器跑。

## 什麼時候需要 self-hosted

合理場景：

- **要 GPU**：GitHub 免費 runner 沒 GPU（付費的有但貴）
- **要存取 private 網路**：部署目標在 VPC 裡、runner 要能 SSH 進去
- **合規**：code 或資料不能離開你的網路
- **特殊 OS / 硬體**：ARM bare-metal、Windows Server 特定版本
- **用量大到免費額度不夠 + 自己機器便宜**

**不該 self-hosted 的場景**：

- 「我覺得比較快」— 設定 + 維護成本遠高於省下來的時間
- 「我要完全掌控環境」— 這是錯的價值主張。CI 的價值就是 reproducible，self-hosted 很容易不乾淨

## 為什麼 public repo 絕對不能開

這是硬規則。**任何人開 PR，都能在你的 self-hosted runner 上跑 arbitrary code**。後果：

1. **偷 SSH key、cloud credential**：runner 機器的 `.ssh/`、`~/.aws/` 全沒了
2. **用你機器挖礦、當 proxy**：很多這種攻擊
3. **污染下次 run 的環境**：前一個 PR 留下 backdoor，下個 PR 跑到
4. **橫移你的網路**：從 runner 跳到你的 VPC 其他資源

GitHub 有 warning，但不會阻止你。**public repo + self-hosted runner = 放火**。

安全版做法：

- 只在 private repo 用 self-hosted runner
- 或用 `Actions Runner Controller`（ARC）在 K8s 每次跑起 ephemeral runner，跑完銷毀
- 或限制「only trusted contributors 的 PR 可跑」（有 setting）

## 部署那一哩路

image 在 GHCR 了（Ch 12、13），還差一步：**讓它實際跑在某個地方**。這步跟你基礎架構綁深，沒有通用做法，幾個常見模式：

### 模式 1：K8s（`kubectl set image`）

```yaml
deploy:
  needs: build
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
    - uses: azure/setup-kubectl@v4
    - uses: aws-actions/configure-aws-credentials@v4
      with:
        role-to-assume: arn:aws:iam::...
        aws-region: ...
    - run: aws eks update-kubeconfig --name my-cluster
    - run: kubectl set image deployment/tasktrack app=ghcr.io/${{ github.repository }}:${{ github.sha }}
```

這是雲原生圈的主流。

### 模式 2：VPS / bare-metal 上跑 compose

```yaml
deploy:
  needs: build
  runs-on: ubuntu-latest
  steps:
    - uses: appleboy/ssh-action@v1
      with:
        host: ${{ secrets.PROD_HOST }}
        username: deploy
        key: ${{ secrets.SSH_KEY }}
        script: |
          cd /srv/tasktrack
          docker pull ghcr.io/${{ github.repository }}:${{ github.sha }}
          export IMAGE_TAG=${{ github.sha }}
          docker compose up -d
```

`appleboy/ssh-action` 把 shell script 透過 SSH 送到 remote 跑。簡單、沒包袱。缺點：如果 SSH key leak 就完蛋。

### 模式 3：Serverless / managed container

- **AWS App Runner / Fargate**：`aws apprunner update-service`
- **Google Cloud Run**：`gcloud run deploy`
- **Fly.io**：`flyctl deploy`
- **Railway**：git push 到 Railway 自動 deploy

這些都有對應 action 或 CLI。**小專案最低摩擦的選項**。

### 模式 4：GitOps（ArgoCD、Flux）

你 workflow **不直接 deploy**。你 push image + 更新一個 manifest repo 裡的 image tag，ArgoCD 在 K8s 那端看到 manifest 變了就自動 sync。

```yaml
deploy-manifest:
  steps:
    - uses: actions/checkout@v4
      with:
        repository: org/tasktrack-manifests
        token: ${{ secrets.MANIFESTS_TOKEN }}
    - run: |
        sed -i "s|image: ghcr.io/.*/tasktrack:.*|image: ghcr.io/${{ github.repository }}:${{ github.sha }}|" app.yaml
        git add . && git commit -m "bump tasktrack to ${{ github.sha }}"
        git push
```

這是進階做法，適合多 service 的團隊，超出這課範圍。知道有這做法就好。

## 示範：SSH deploy 到一台 VPS

這課不要求你真的有 VPS，但示範最低成本的自動 deploy 是長怎樣：

**前置**：

1. 你有一台 Linux VPS，裝了 Docker 和 docker-compose
2. VPS 上 `/srv/tasktrack/` 有 `docker-compose.yml`（跟 Ch 4 類似但 `image:` 指定到 GHCR）
3. 你有一對 SSH key，public 在 VPS 的 `authorized_keys`，private 存 GitHub secret `SSH_KEY`
4. `PROD_HOST` secret 存 VPS 位址

```yaml
# .github/workflows/deploy.yml
name: Deploy

on:
  workflow_run:              # ← 在 Release workflow 成功後觸發
    workflows: ["Release"]
    types: [completed]

permissions:
  contents: read

jobs:
  deploy:
    if: ${{ github.event.workflow_run.conclusion == 'success' }}
    runs-on: ubuntu-latest
    environment: production    # ← 指定 environment，可加 required reviewer
    steps:
      - uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.PROD_HOST }}
          username: deploy
          key: ${{ secrets.SSH_KEY }}
          script: |
            cd /srv/tasktrack
            docker compose pull
            docker compose up -d
            docker image prune -f
```

幾個注意：

- `environment: production` 觸發 Ch 9 提過的 environment secret 與 approval gate
- `workflow_run` 是個 event，在另個 workflow 結束時觸發
- `docker compose pull` 拉最新 image（compose.yml 裡可能用 `:latest`，或每次 deploy 前動態修）
- `docker image prune -f` 清舊 image，避免 disk 漲爆

## rollback 策略

什麼時候需要 rollback：deploy 後發現 bug。三種做法：

### 手動 rollback（最快）

```bash
ssh vps
cd /srv/tasktrack
docker compose down
# 改 compose.yml 的 image tag 回上一版 sha
docker compose up -d
```

### workflow_dispatch 觸發 rollback

```yaml
on:
  workflow_dispatch:
    inputs:
      sha:
        description: 'Git SHA to rollback to'
        required: true
```

觸發：UI 按鈕 → 填 sha → 跑 deploy，換 image 到舊 sha。

### 自動 rollback（配 healthcheck）

進階：deploy 後跑一連串 smoke test，失敗自動 `docker compose down && 回上版`。實務上不容易做穩。

## 動手練習

這章大部分概念性，沒辦法每個都實作。能做的：

1. 讀一遍 workflow、了解 `workflow_run` event
2. 在你 repo 建個 `production` environment，配 required reviewer
3. （如果有 VPS）實際跑一次 SSH deploy
4. （替代）用 Fly.io 或 Railway 的免費方案部署一次 `tasktrack`

## 常見誤解

- 「**Self-hosted runner 比 GitHub 快**」 — 網速、CPU、IO 看你機器，經常沒比較快
- 「**Self-hosted runner 免費**」 — GitHub 端免費，但機器、電、維護不免
- 「**Public repo + self-hosted 有警告就安全**」 — 警告就是警告，沒阻止。**不要用**
- 「**SSH deploy 是老派**」 — 小專案 SSH + compose 超夠用，不是每個地方都需要 K8s
- 「**環境 environment 是部署環境的名字**」 — 是。但也是 GitHub Actions 的 resource（gate、secret 層）

## 驗收標準

- [ ] 你能說出 4 種部署模式（K8s set image / SSH compose / serverless / GitOps）
- [ ] 你知道 public repo + self-hosted runner 為什麼是 nuke
- [ ] 你會用 `environment:` + required reviewer 加部署 gate
- [ ] 你有概念地 rollback：準備好換 image tag 回上一版的能力

## 自我檢核

- [ ] 我能判斷自己需不需要 self-hosted runner（多半答案是不需要）
- [ ] 我懂 SSH deploy 的簡單寫法
- [ ] 我知道 rollback 最直接是換 image tag，不是回 git revert
- [ ] 我認同「小專案用 SSH + compose 就夠」的立場

Part 3 進最後一章：整個 pipeline 從上到下審視一次，討論診斷、成本、觀測性。

→ [Ch 15 完整 pipeline 審視](./15-pipeline-full-review.md)
