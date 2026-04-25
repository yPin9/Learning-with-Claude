# CI/CD 與容器化學習筆記：用 Docker + GitHub Actions 把 tasktrack 送上線

> 給會寫 code、Docker 與 GitHub Actions 都碰過但不熟、想把專案自動化交付的工程師。

這是一系列循序漸進的教學文章。以一個小型的 FastAPI + PostgreSQL 服務 **`tasktrack`** 當範例，從 Dockerfile 的 layer 原理寫到 multi-stage build，從 workflow 語法寫到 reusable workflow，最後把整套 PR → test → build → push → release 串成一條可交付的 pipeline。

每一章都有「**驗收標準**」清單，達成即可繼續下一章。**不追求完美，追求 done**。這是這門課的 meta 設計。

## 為什麼學這個？

- **容器化是現代交付的基本盤**：沒有它，你的「在我機器上可以跑」只會變成罵戰。
- **CI/CD 是職業加速器**：會一個人 push 一個 branch 就觸發全套測試 + 建 image + 發 release 的人，比只會本地 `pytest` 的人值錢。
- **強制切 milestone 的外部工具**：pipeline 紅了就是紅了、image build 不出來就是不出來。這套東西會誠實告訴你哪裡沒做完，不讓你在細節裡打轉。

## 課程地圖

### Part 1 — Docker 進階（把容器搞懂）
- [Ch 0 環境搭建](./00-environment-setup.md)
- [Ch 1 容器到底在解什麼問題？CI/CD 全貌](./01-why-containerize.md)
- [Ch 2 Dockerfile 與 layer 原理](./02-dockerfile-layers.md)
- [Ch 3 Multi-stage build：把 image 砍小](./03-multi-stage-build.md)
- [Ch 4 docker-compose — 服務從來不是單機](./04-docker-compose.md)
- [Ch 5 容器安全與多平台建構](./05-container-security-multiarch.md)
- [練習 A：把爛 Dockerfile 優化到生產等級](./practice-a-dockerfile-rescue.md)

### Part 2 — GitHub Actions 核心（把 CI 跑起來）
- [Ch 6 workflow 檔案結構](./06-workflow-structure.md)
- [Ch 7 job 機制、matrix、runner 生命週期](./07-jobs-matrix-runner.md)
- [Ch 8 cache 是一切](./08-cache-is-everything.md)
- [Ch 9 secrets、環境變數、OIDC](./09-secrets-oidc.md)
- [Ch 10 reusable workflow 與 composite action](./10-reusable-workflow.md)
- [Ch 11 進階：service container、artifact、concurrency](./11-services-artifacts-concurrency.md)
- [練習 B：為 tasktrack 設計完整 PR CI](./practice-b-tasktrack-pr-ci.md)

### Part 3 — 整合與自動化（把產品交付出去）
- [Ch 12 Container registry 與 tag 策略](./12-container-registry.md)
- [Ch 13 Release automation](./13-release-automation.md)
- [Ch 14 Self-hosted runner 與部署](./14-self-hosted-runner.md)
- [Ch 15 完整 pipeline 審視](./15-pipeline-full-review.md)
- [練習 C：給 pipeline 加安全檢查](./practice-c-security-checks.md)

### Part 4 — 整合專案
- [Final Project：把 tasktrack 完整生產化](./final-project-tasktrack-production.md)

## 學習方式建議

1. **盯著「驗收標準」走，不要偏離**：每章結尾有一張 checklist。勾完就走，該回頭補的之後再說。這門課本身就是在練習「先求有、再求好」。
2. **故意弄壞**：把 Dockerfile 的 COPY 順序調反、把 workflow 的 cache key 寫死、把 secret 印出來。工具會告訴你為什麼不行。
3. **看真實輸出**：`docker history`、`docker build --progress=plain`、GitHub Actions 的 job 詳細 log — 都是最誠實的老師。

## 參考資料

- Docker 官方文件：<https://docs.docker.com/>（特別是 Dockerfile reference 與 Buildx）
- GitHub Actions 官方文件：<https://docs.github.com/actions>
- 《Docker Deep Dive》— Nigel Poulton（容器原理補充）
- awesome-actions：<https://github.com/sdras/awesome-actions>（找現成 action 的起點）
