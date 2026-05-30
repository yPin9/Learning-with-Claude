# Ch 23 — aptly：進階 repo 管理

> **目標**：理解 aptly 的三層架構（repo → snapshot → publish）如何解決 reprepro 做不到的「快照、多版本、回滾、漸進式發布」、mirror 功能、以及 aptly 的 API/CLI 工作流。

> **環境**：aptly 1.5.x（從 aptly.info 安裝）。本章對照 Ch 22 的 reprepro，理解兩者的設計差異。

## 為什麼需要 aptly？reprepro 不夠在哪？

reprepro（Ch 22）的單一版本模型很簡單，但對某些需求不夠：

- **回滾**：生產 repo 推了個壞版本，要立刻回到上週的狀態——reprepro 不保留歷史，做不到
- **漸進式發布**：先發到 staging repo 測試，確認沒問題再「升級」到 production——reprepro 沒有這個概念
- **凍結快照**：「2025-01-01 的 repo 狀態」永久保存，用於可重現的部署——reprepro 永遠是最新
- **同時維護多版本**：repo 裡同時有 greet 1.0 和 2.0 讓使用者選——reprepro 每套件只一個版本

aptly 用 **snapshot（快照）** 機制解決全部。它是「有版本控制概念」的 repo 管理工具。

## 先建立直覺：aptly 的三層架構

```
aptly 的核心三層（這是和 reprepro 最大的不同）：

  1. local repo（可變的套件集合）
       │  你往這裡加/刪套件，它一直變
       │  aptly repo add my-repo greet_1.0-1.deb
       ▼
  2. snapshot（不可變的時間點快照）
       │  把當前 repo 狀態「凍結」成一個快照
       │  aptly snapshot create snap-2025-01-01 from repo my-repo
       │  快照永遠不變——這是回滾和可重現的基礎
       ▼
  3. publish（把某個快照發布成 apt 可用的 repo）
       │  aptly publish snapshot snap-2025-01-01
       │  發布的是「快照」，所以發布的內容是凍結的
       ▼
  apt update 看到的是被發布的那個快照
```

關鍵洞察：**你發布的是 snapshot（不可變），不是 repo（可變）**。這個間接層帶來所有進階能力：

```
reprepro：  套件 → 直接發布（永遠最新）
aptly：     套件 → repo（可變）→ snapshot（凍結）→ publish（發布快照）
                                    ↑
                          這層讓你能：回滾、漸進發布、可重現
```

## 基本工作流

```bash
# 1. 建立 local repo
aptly repo create -distribution=bookworm -component=main my-repo

# 2. 加套件進 repo
aptly repo add my-repo greet_1.0-1_amd64.deb libgreet1_1.0-1_amd64.deb
# 或加整個目錄
aptly repo add my-repo ./incoming/

# 3. 看 repo 內容
aptly repo show -with-packages my-repo

# 4. 建立快照（凍結當前狀態）
aptly snapshot create greet-v1 from repo my-repo

# 5. 發布快照（簽署）
aptly publish snapshot -gpg-key=ABCD1234 greet-v1
#   發布到 aptly 的 public/ 目錄，含簽署的 Release
```

使用發布的 repo：
```bash
# aptly 預設發布在 ~/.aptly/public/
echo "deb [signed-by=/path/mykey.gpg] \
    http://localhost:8080 bookworm main" | \
    sudo tee /etc/apt/sources.list.d/aptly.list
# aptly serve（內建 http server）或用任何 web server 指向 public/
aptly serve
```

## 漸進式發布：staging → production

aptly 的 snapshot + publish 讓「測試後再上線」變得乾淨：

```bash
# 場景：先發到 staging 測試，OK 再上 production

# 1. 加新版本進 repo，建快照
aptly repo add my-repo greet_2.0-1_amd64.deb
aptly snapshot create greet-v2 from repo my-repo

# 2. 發布到 staging endpoint
aptly publish snapshot -gpg-key=ABCD greet-v2 staging
#   發布在 public/staging/，staging 環境測試

# 3. 測試通過後，把 production endpoint 切換到 v2 快照
aptly publish switch bookworm production greet-v2
#   production 環境的 apt 現在看到 v2
#   （switch 是原子操作，不會有中間狀態）
```

`aptly publish switch` 把某個發布點切換到不同快照——這是漸進發布和回滾的核心。

## 回滾

```bash
# 生產推了壞的 v2，要回滾到 v1
aptly publish switch bookworm production greet-v1
#   production 立刻回到 v1 快照
#   因為 snapshot 不可變，v1 永遠在那，回滾是即時的

# 對比 reprepro：舊版本早就被取代刪除，根本無從回滾
```

這是 aptly 最有價值的能力之一：**因為快照不可變且保留，回滾只是「切換發布點到舊快照」**，即時且可靠。

## mirror：鏡像上游 repo

aptly 能鏡像整個上游 repo（比 reprepro 的 update 更完整）：

```bash
# 1. 建立 mirror（指向上游）
aptly mirror create -filter="greet | libgreet1" \
    debian-mirror http://deb.debian.org/debian bookworm main
#   -filter 可選，只鏡像符合條件的套件

# 2. 同步（下載套件）
aptly mirror update debian-mirror

# 3. 從 mirror 建快照（凍結上游某時刻的狀態）
aptly snapshot create debian-2025-01 from mirror debian-mirror

# 4. 發布這個快照（你的本地鏡像）
aptly publish snapshot debian-2025-01
```

這對「凍結上游某個時間點」很有用——例如 CI 要可重現，就鏡像並快照 Debian 的某個時刻，永遠用那個快照 build，不受上游更新影響。

## merge：合併多個快照

```bash
# 把多個來源的快照合併成一個（如自家套件 + 上游精選）
aptly snapshot merge combined my-snapshot debian-2025-01
aptly publish snapshot combined
```

這讓你能組合「自己的套件 + 上游的依賴」成一個 repo，使用者一個 source 就能裝齊。

## aptly vs reprepro：何時用哪個

| 面向 | reprepro | aptly |
|---|---|---|
| 架構 | 套件 → 直接發布 | repo → snapshot → publish（多一層）|
| 版本 | 每套件單一版本 | 快照保留多個時間點 |
| 回滾 | 不支援 | 切換到舊快照，即時 |
| 漸進發布 | 不支援 | staging → production switch |
| 可重現 | 弱（永遠最新）| 強（快照凍結）|
| mirror | update（基本）| mirror（完整）+ 快照 |
| 複雜度 | 低 | 中 |
| API | 無 | 有 REST API |
| 適合 | 簡單「永遠最新」私有 repo | 需要版本控制/回滾/CI 的生產 repo |

> 選擇原則：**簡單的「內部最新套件」repo 用 reprepro**（少設定、純檔案）；**需要回滾、漸進發布、可重現快照、或要 API 整合的生產環境用 aptly**。aptly 功能多但設定也複雜，按需選擇。

## aptly 的 REST API

aptly 有 HTTP API，適合 CI/自動化整合：

```bash
# 啟動 API server
aptly api serve -listen=:8080 &

# 透過 API 操作（CI 可以用）
curl -X POST http://localhost:8080/api/repos/my-repo/file/incoming
curl -X POST http://localhost:8080/api/publish/bookworm/production
```

CI（Ch 32）可以透過 API 自動化整個發布流程，不用 SSH 進 server 跑 CLI。這是 aptly 相比 reprepro 更適合大型自動化的一個原因。

## 故意弄壞：直接發布 repo 而非 snapshot

```bash
# aptly 也允許直接發布 repo（不經 snapshot），但這放棄了所有快照優勢
aptly publish repo my-repo
#   能用，但：
#   - 沒有快照，無法回滾
#   - repo 一變，發布的內容就跟著變（失去凍結特性）
#   - 退化成類似 reprepro 的行為，卻沒有 reprepro 的簡單

# 之後想回滾？
# 沒有快照可切換，回不去
```

教訓：用 aptly 卻直接 `publish repo` 是浪費它的核心價值。aptly 的威力在 snapshot 這層——永遠 `publish snapshot`，享受回滾和可重現。直接發布 repo 只在「我就是要 reprepro 行為但已經裝了 aptly」的妥協場景。

## 踩雷集錦

1. **直接 publish repo 而非 snapshot**：放棄回滾和可重現的核心優勢。永遠經過 snapshot 層

2. **以為 snapshot 會自動更新**：snapshot 是**不可變**的凍結。repo 加了新套件，舊 snapshot 不變——要建**新** snapshot 才包含新套件。這正是它可重現的原因，但新手會困惑「為什麼發布的 repo 沒有我剛加的套件」（因為發布的是舊 snapshot）

3. **快照累積佔空間**：每個快照引用 pool 的套件。久了快照很多，要 `aptly db cleanup` 清理沒被任何快照引用的套件

4. **publish switch 的 distribution 要對應**：`publish switch <distribution> <prefix> <snapshot>`，distribution 和 prefix 要和原本 publish 的一致，否則找不到要切換的發布點

5. **mirror 的 GPG 驗證**：鏡像上游時要驗證上游的 Release 簽署（`-keyring` 指定上游 key），否則鏡像了被竄改的內容

## 進階：aptly 在 GitOps / CI 的角色

aptly 的 snapshot 模型和現代 GitOps/CI 哲學契合：

```
CI pipeline（Ch 32）的理想 repo 流程：

  git push → CI build → .deb
        │
  aptly repo add（加進 local repo）
        │
  aptly snapshot create snap-${CI_COMMIT}  ← 每次 build 一個快照
        │                                     （對應 git commit，可追溯）
  aptly publish switch staging snap-${CI_COMMIT}
        │
  自動測試 staging
        │
  通過 → aptly publish switch production snap-${CI_COMMIT}
  失敗 → production 維持舊快照（自動「回滾」= 沒切換）
```

每個 git commit 對應一個 aptly snapshot——repo 狀態變得**可追溯、可回滾、可重現**，就像 git 對程式碼做的。這是 aptly 相比 reprepro 在現代 CI 環境的決定性優勢。Final Project 會用到這個模式。

## 動手練習

1. 用 aptly 走完整三層：create repo → add 練習 B 的套件 → snapshot create → publish snapshot → apt install。體會「發布的是 snapshot」

2. 測試回滾：建 v1 快照發布，加新版本建 v2 快照，`publish switch` 到 v2，再 `switch` 回 v1。確認 apt 看到的版本跟著切換

3. 測試「snapshot 不可變」：發布一個 snapshot 後，往 repo 加新套件，`apt update` 確認看不到新套件（因為發布的是舊 snapshot），然後建新 snapshot 並 switch，確認看得到了

4. 試 mirror：鏡像 Debian 的一兩個小套件，建快照，發布，從你的鏡像安裝

## 本章重點整理

- aptly 三層：repo（可變）→ snapshot（不可變凍結）→ publish（發布快照）；發布的是 snapshot
- snapshot 不可變是核心：帶來回滾（切換到舊快照）、漸進發布（staging→production switch）、可重現
- mirror 完整鏡像上游 + 快照，能凍結上游某時刻用於可重現 build
- vs reprepro：reprepro 簡單「永遠最新」；aptly 複雜但有版本控制/回滾/API，適合生產 CI
- 反模式：直接 publish repo（放棄快照優勢）；正確永遠 publish snapshot

## 自我檢核

- [ ] 能解釋 aptly 三層架構，以及「發布 snapshot 而非 repo」帶來什麼能力
- [ ] 知道為什麼 snapshot 不可變是回滾和可重現的基礎
- [ ] 能說出 aptly 和 reprepro 的關鍵差異，以及各自適合的場景
- [ ] 知道為什麼「往 repo 加套件後發布的內容沒變」（發布的是舊 snapshot）
- [ ] 能描述 aptly snapshot 如何對應 git commit 實現可追溯的 repo 狀態

## 延伸閱讀

### 官方文件

- **[aptly documentation](https://www.aptly.info/doc/overview/)**
  - **讀哪裡**：concept overview（repo/snapshot/publish/mirror）和 tutorial
  - **學什麼**：aptly 的完整概念模型和所有指令；本章是教學版
  - **前提**：讀完本章

- **[aptly REST API](https://www.aptly.info/doc/api/)**
  - **讀哪裡**：repos、snapshots、publish 的 API endpoint
  - **學什麼**：CI 整合用的 API；Final Project 會用到
  - **前提**：本章的 API 部分

### 部落格 / 文章

- **[Managing APT repositories with aptly](https://www.aptly.info/tutorial/)** — aptly 官方 tutorial
  - **這篇說什麼**：從零到漸進發布的完整實戰，含 snapshot 工作流
  - **讀哪裡**：整個 tutorial，跟著做一遍
  - **為什麼值得讀**：官方教學把三層架構講得很清楚，是本章的最佳補充

→ [Ch 24 Ubuntu PPA 與 Launchpad](./24-ppa-launchpad.md)
