# Ch 0 — 環境搭建

> **目標**：把整門課會用到的工具鏈一次裝好、驗證能跑，並跑通第一個「生成 SBOM → 掃漏洞」的最小迴圈。這章之後，你每讀一章都能立刻動手，而不是卡在裝環境。

> **環境**：Windows 11 + WSL2 Ubuntu 22.04.3 LTS，工具版本 syft 1.51.0 / grype 0.117.0 / trivy 0.74.0 / cosign 2.4.1 / Docker 27.3.1。本章所有輸出都是在這個環境真跑出來的；你的版本號會比較新，行為大致相同，有差異的地方後面章節會標。

## 為什麼需要這個？

SBOM 這個領域有個很惱人的特性：**它幾乎全部是工具驅動的**。你可以把 SPDX 規範背得滾瓜爛熟，但沒有 syft 幫你生一份、沒有 grype 幫你掃一遍、沒有 Dependency-Track 幫你長期盯著，你對「一份 SBOM 到底長怎樣、能做什麼、哪裡會出錯」不會有任何真實直覺。這門課的信條是**每章都動手**，所以第一步就是把手弄髒。

我們選 **WSL2 Ubuntu** 當主環境，理由很直接：

- 這整個生態（syft/grype/trivy/cosign/in-toto/Dependency-Track）是 Linux 原生的。macOS 大多也能跑，Windows 原生則到處是坑（路徑、權限、container 支援）。
- container image 是 SBOM 最重要的目標之一，而 container 世界就是 Linux 世界。
- 在 WSL 裡跑，你的指令跟教材、跟真實 CI 環境（GitHub Actions runner 也是 Linux）幾乎一模一樣，學到的東西可以直接搬。

如果你在 Linux 或 macOS 上，跳過 WSL 那節，其餘照做。

## 先建立直覺：這門課的工具地圖

在裝任何東西之前，先在腦中畫出這些工具各站在哪一格。SBOM 的生命週期有三個大動作——**生成、消費、信任**——每個動作有對應的工具：

```
        生成                    消費                      信任
   （產出清單）           （拿清單做事）            （證明清單可信）
  ┌───────────┐        ┌──────────────┐        ┌──────────────┐
  │   syft    │───SBOM─▶│    grype     │        │   cosign     │
  │  trivy    │  .json  │    trivy     │        │ (sigstore)   │
  │ (build    │        │ osv-scanner  │        │  in-toto     │
  │  plugins) │        │ Dependency-  │        │  slsa        │
  └───────────┘        │   Track      │        └──────────────┘
                       └──────────────┘
       ▲                      ▲                       ▲
    Ch 9-12               Ch 13-17                Ch 18-23
```

- **syft**（Anchore）：這門課的生成主力，把 container / 檔案系統 / 專案目錄掃成一份 SBOM。
- **grype**（Anchore）：吃 SBOM 或 image，比對漏洞資料庫吐出 CVE 清單。
- **trivy**（Aqua Security）：瑞士刀，能生 SBOM 也能掃，還能掃 misconfig / secret。我們拿它跟 syft/grype 對照，看不同工具看到的東西不一樣。
- **cosign**（sigstore）：簽章與驗章，把 SBOM 綁到 artifact 上、證明它沒被動過。
- **Dependency-Track**（OWASP）：一個長跑的 server，把 SBOM 當資產庫持續監控（Ch 17 才會用 docker compose 起它，這章先不裝）。

> 別急著記每個工具的旗標。這章只要讓它們全部「能叫得出來、版本印得出來、跑一次不報錯」。

## Step 1 — 確認 WSL2 與 Ubuntu

在 Windows 的 PowerShell（一般權限即可）確認你有 WSL2 與一個 Ubuntu 發行版：

```powershell
wsl -l -v
```

我的機器上輸出（節錄）：

```
  NAME              STATE           VERSION
* Ubuntu            Stopped         2
  docker-desktop    Stopped         2
```

重點看兩件事：**VERSION 是 2**（WSL2，不是 1），以及你有一個 Ubuntu。沒有的話：

```powershell
wsl --install -d Ubuntu
```

裝完重開機、設好 Linux 使用者名稱密碼。之後所有指令都在 **Ubuntu 這個 WSL 裡**跑，本章接下來的 `$` 提示符都代表「在 WSL Ubuntu 的 shell 裡」。

## Step 2 — Docker：讓 WSL 能操作容器

SBOM 最常見的目標是 container image，所以 Docker 是必要的。最省事的做法是裝 **Docker Desktop for Windows**，然後在它的設定裡開 **WSL integration**（Settings → Resources → WSL Integration → 打開你的 Ubuntu）。開完之後在 WSL 裡就能直接 `docker`：

```
$ docker --version
Docker version 27.3.1, build ce12230
```

驗證能真的拉 image、跑 container：

```
$ docker run --rm alpine:3.19 echo "docker works"
docker works
```

> **踩雷預告**：如果 `docker` 在 WSL 裡 command not found，八成是 WSL integration 沒開，或開了但沒重啟 WSL。回 Docker Desktop 設定確認，然後 `wsl --shutdown`（在 PowerShell）再重進。

## Step 3 — 裝 syft 與 grype

Anchore 官方 installer 會抓對應平台的 binary 丟到你指定的目錄。我們裝到 `~/bin`（家目錄下，不需要 root）：

```bash
mkdir -p ~/bin
curl -sSfL https://raw.githubusercontent.com/anchore/syft/main/install.sh  | sh -s -- -b ~/bin
curl -sSfL https://raw.githubusercontent.com/anchore/grype/main/install.sh | sh -s -- -b ~/bin
```

真實安裝輸出（節錄）：

```
[info] using release tag='v1.51.0' version='1.51.0' os='linux' arch='amd64'
[info] installed /home/ypp/bin/syft
[info] using release tag='v0.117.0' version='0.117.0' os='linux' arch='amd64'
[info] installed /home/ypp/bin/grype
```

把 `~/bin` 加進 `PATH`（加到 `~/.bashrc` 讓它永久生效）：

```bash
echo 'export PATH="$HOME/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

驗證：

```
$ syft version
Application:   syft
Version:       1.51.0
BuildDate:     2026-08-10T14:48:50Z

$ grype version
Application:   grype
Version:       0.117.0
```

> **為什麼用 installer 而不是 `apt install`？** Ubuntu 22.04 的官方 apt 源沒有 syft/grype，而且這類工具迭代很快（漏洞比對邏輯常改），你會想要相對新的版本。installer 抓的是 GitHub release 的官方 binary。生產環境還可以進一步驗證它的 checksum / 簽章——這正是這門課後半在講的東西（Ch 20-21），到時你會回頭用學到的技術驗證自己的工具鏈。

## Step 4 — 裝 trivy 與 cosign

trivy 抓 release tarball；cosign 直接抓 release binary：

```bash
# trivy
TRIVY_VER=0.74.0
curl -sSL "https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VER}/trivy_${TRIVY_VER}_Linux-64bit.tar.gz" \
  | tar xz -C ~/bin trivy

# cosign
curl -sSL -o ~/bin/cosign \
  "https://github.com/sigstore/cosign/releases/download/v2.4.1/cosign-linux-amd64"
chmod +x ~/bin/cosign
```

驗證：

```
$ trivy --version
Version: 0.74.0

$ cosign version
GitVersion:    v2.4.1
```

> **踩雷（親身踩到）**：trivy 的 release 資產檔名裡的版本號要跟 tag 對得上（`trivy_0.74.0_Linux-64bit.tar.gz`）。我第一次寫錯版本號抓到 404，下載下來是個 9 bytes 的「Not Found」文字檔，`tar` 就報 `gzip: stdin: not in gzip format`。看到這個錯誤先用 `curl -w '%{http_code}'` 確認 HTTP 狀態，別急著懷疑 tar。這是「失敗長什麼樣」的第一課——工具不會告訴你「其實是 404」，它只會抱怨解壓縮失敗。

## Step 5 — 補齊輔助工具

後面章節會大量用到 `jq`（切 JSON）、`go`（有些 build-time 生成範例、slsa 工具是 Go 寫的）、`git`。Ubuntu 上：

```bash
sudo apt update && sudo apt install -y jq git
```

驗證：

```
$ jq --version
jq-1.6
$ go version
go version go1.18.1 linux/amd64
```

`go` 如果 apt 版本太舊（22.04 預設 go 1.18），部分工具會要 1.21+，需要時再從 [go.dev](https://go.dev/dl/) 裝新版；這章先不強求。

## 底層機制：第一次生成，syft 到底做了什麼？

現在跑通第一個真實迴圈。生成 alpine image 的 SBOM：

```
$ syft alpine:3.19
NAME                    VERSION               TYPE
alpine-baselayout       3.4.3-r2              apk
alpine-baselayout-data  3.4.3-r2              apk
alpine-keys             2.4-r1               apk
apk-tools               2.14.4-r0            apk
busybox                 1.36.1-r20           apk
busybox-binsh           1.36.1-r20           apk
ca-certificates-bundle  20250911-r0          apk
libc-utils              0.7.2-r5             apk
libcrypto3              3.1.8-r1             apk
libssl3                 3.1.8-r1             apk
musl                    1.2.4_git20230717-r5 apk
musl-utils              1.2.4_git20230717-r5 apk
scanelf                 1.3.7-r2             apk
ssl_client              1.36.1-r20           apk
zlib                    1.3.1-r0             apk
```

15 個 package，type 全是 `apk`（Alpine 的套件格式）。這裡發生的事，遠比「跑個指令」深：

```
  syft alpine:3.19
        │
        │ 1. 透過 docker 把 image 的每一層 (layer) 拉下來
        ▼
  ┌─────────────────────────────┐
  │  把所有 layer 疊成一個檔案系統快照  │   ← 不是啟動容器，是解開 tar
  └─────────────────────────────┘
        │
        │ 2. 一堆「cataloger」各自在檔案系統裡找自己認得的證據
        ▼
  ┌───────────────────────────────────────┐
  │ apk cataloger  → 讀 /lib/apk/db/installed │
  │ dpkg cataloger → 讀 /var/lib/dpkg/status  │  (alpine 沒有 → 0 筆)
  │ npm cataloger  → 找 package-lock.json     │  (沒有 → 0 筆)
  │ ... 幾十個 cataloger                       │
  └───────────────────────────────────────┘
        │
        │ 3. 把找到的 package 正規化成統一模型 (名稱/版本/PURL/檔案清單)
        ▼
     一份 SBOM（記憶體裡，預設印成表格）
```

關鍵洞察：**syft 不執行這個 image，它把 image 的層解開成一個靜態檔案系統，然後「讀證據」**。它認得 apk，是因為它去讀了 `/lib/apk/db/installed` 這個 Alpine 的套件資料庫檔案。這個「靠證據推斷有哪些元件」的機制，就是 SBOM 生成的核心，也是它一切盲點的來源——**沒留下證據的東西，syft 看不到**（你手動 `curl` 下來塞進 image 的 binary、靜態編進去的函式庫，都可能是隱形的）。Ch 10 會把 cataloger 拆開來看。

現在把它輸出成真正的 SBOM 檔案（機器可讀的 JSON），而不是給人看的表格：

```bash
syft alpine:3.19 -o spdx-json=alpine.spdx.json
syft alpine:3.19 -o cyclonedx-json=alpine.cdx.json
```

`-o` 指定輸出格式。SPDX 與 CycloneDX 是兩大標準格式（Part 2 整整四章在講），這裡先感受一下：同一個 image，兩份格式不同但描述同一件事的清單。

## 消費：把 SBOM 餵給 grype

生成只是一半，SBOM 的價值在被消費。grype 吃 image（或剛剛那份 SBOM），比對漏洞資料庫：

```
$ grype alpine:3.19
NAME           INSTALLED             FIXED IN              TYPE  VULNERABILITY   SEVERITY
busybox        1.36.1-r20                                  apk   CVE-2025-60876  Medium
musl           1.2.4_git20230717-r5  1.2.4_git20230717-r6  apk   CVE-2026-40200  High
musl-utils     1.2.4_git20230717-r5  1.2.4_git20230717-r6  apk   CVE-2026-40200  High
zlib           1.3.1-r0                                    apk   CVE-2026-27171  Medium
busybox        1.36.1-r20            1.36.1-r21            apk   CVE-2025-46394  Low
...
```

`FIXED IN` 空白的（如 CVE-2025-60876）代表**上游還沒出修好的版本**——你現在無處可升，只能靠其他手段緩解。這一欄後面會反覆出現，它決定了「這個漏洞你現在能不能處理」。

> **第一次跑 grype 會慢**：它要先下載漏洞資料庫（幾百 MB），之後會快取在 `~/.cache/grype/`。這也帶出一個生產議題：離線 / air-gapped 環境要怎麼餵 DB？Ch 15 會談。

你剛剛完成了 SBOM 的完整最小迴圈：**生成（syft）→ 消費（grype）**。整門課就是把這條線的每一環挖深、加上信任（簽章）、接上流程（CI / 監控 / 治理）。

## 踩雷集錦

1. **「我在 WSL 裡 `docker` command not found」**：不是 Docker 沒裝，是 Docker Desktop 的 WSL integration 沒對這個發行版打開。開了之後要 `wsl --shutdown`（PowerShell）重啟 WSL 才生效。
2. **「syft 裝好了但 `syft: command not found`」**：`~/bin` 沒在 `PATH` 裡。`echo $PATH` 確認，沒有就把 `export PATH="$HOME/bin:$PATH"` 加進 `~/.bashrc` 並 `source` 它。
3. **把工具裝在 Windows 端而不是 WSL 端**：syft 有 Windows 版，但你會在 container / 路徑 / 權限上到處撞牆。這門課一律在 WSL 裡裝、在 WSL 裡跑。別混用。
4. **以為 `syft <image>` 的表格輸出就是 SBOM**：那只是給人看的摘要。真正的 SBOM 是 `-o spdx-json` / `-o cyclonedx-json` 產出的結構化檔案。表格丟了一大堆欄位（PURL、hash、檔案清單、關係），拿表格去做自動化會少一半資訊。
5. **第一次掃描很慢就以為卡住了**：grype / trivy 首次執行在下載漏洞 DB，是正常的。別 Ctrl-C，讓它下載完，之後就快了。

## 進階：再往深一層

- **版本釘定**：生產環境不要用 installer 抓「latest」，要釘特定版本並記錄。原因是漏洞掃描工具的比對邏輯會隨版本改變，同一份 SBOM 用不同版 grype 掃可能出不同結果——這對「可重現的安全報告」是硬需求。Ch 15 會展示同一 SBOM 跨版本結果漂移。
- **工具自身的供應鏈**：你剛剛 `curl | sh` 裝了一堆 binary——這本身就是一個供應鏈信任問題（你怎麼知道抓到的 syft 沒被掉包？）。這不是反諷，是這門課的核心議題。Ch 20-21 學完 cosign 後，你可以回來驗證這些工具 release 的 sigstore 簽章。
- **一次裝好的腳本**：把上面 Step 3-5 寫成一個 `setup.sh`，之後換機器一鍵重建。final project 的 CI pipeline 會需要在乾淨 runner 上重裝這套。

## 動手練習

1. 跑 `syft alpine:3.19 -o spdx-json=alpine.spdx.json`，然後用 `jq '.packages | length' alpine.spdx.json` 數數看有幾個 package，跟表格輸出的 15 個對不對得上（提示：SPDX 會多一個描述 image 本身的 root package，數字可能是 16，想想為什麼）。
2. 換一個更肥的 image 試試：`syft python:3.12-slim`，比較 package 數量。感受一下「基底 image 選得肥，你的攻擊面就大」這件事有多具體。
3. 故意製造失敗：`syft this-image-does-not-exist:latest`，看它怎麼報錯。記住這個錯誤長相，之後在 CI 裡 image tag 打錯時你會認得。

## 本章重點整理

- 這門課全程在 **WSL2 Ubuntu** 動手，工具鏈：syft/trivy 生成、grype/trivy 消費、cosign 信任、Dependency-Track 監控（Ch 17 才起）。
- SBOM 的最小迴圈是**生成 → 消費**：`syft` 產清單、`grype` 比對漏洞。
- syft **不執行** image，它把層解開成靜態檔案系統、靠 cataloger 讀證據推斷元件——這是它的能力，也是它一切盲點的根源。
- 給人看的表格 ≠ SBOM 檔案；真正的 SBOM 是 `-o spdx-json` / `-o cyclonedx-json` 的結構化輸出。

## 自我檢核

- [ ] 我能在 WSL 裡跑出 `syft version`、`grype version`、`trivy --version`、`cosign version`，四個都印得出版本號
- [ ] 我能用自己的話解釋為什麼 syft「不啟動容器也能列出裡面的套件」
- [ ] 我知道 `syft alpine:3.19` 的表格輸出跟 `-o spdx-json` 的差別在哪
- [ ] 我跑通了 syft → grype 的最小迴圈，並看懂 `FIXED IN` 空白代表什麼

## 延伸閱讀

### 官方文件 / 工具

- **[syft README](https://github.com/anchore/syft)**（Anchore）
  - **讀哪裡**：README 的「Supported Ecosystems」表——列出 syft 認得哪些套件格式，直接對應它有哪些 cataloger
  - **和本章的關聯**：這章你只跑了 apk，那張表告訴你它還能認 npm/pip/go/maven/… 幾十種，Ch 10 會深入
- **[grype README](https://github.com/anchore/grype)**（Anchore）
  - **讀哪裡**：「What's shown in the results」與漏洞 DB 來源那節
  - **為什麼值得讀**：解釋 `FIXED IN` / severity 這些欄位怎麼來，以及它比對的資料庫是哪些——這是 Ch 14 的前導

### 部落格 / 背景

- **[Anchore: What is an SBOM?](https://anchore.com/sbom/)** — Anchore（syft/grype 的作者公司）
  - **這篇說什麼**：從工具作者視角講 SBOM 的定位，偏實務、不學術
  - **讀哪裡**：整篇不長，當作 Ch 1 之前的暖身；但注意它有行銷成分，對「SBOM 能解決一切」的樂觀說法保留態度（Ch 29 會潑冷水）

### 官方入口

- **[CISA SBOM](https://www.cisa.gov/sbom)**（美國 CISA）
  - **讀哪裡**：先看首頁的定義段落即可，深入的 minimum elements 等 Ch 3 再回來
  - **為什麼值得讀**：這是後面法規、標準討論的權威源頭，先知道它在哪

環境好了，工具能叫得動了。下一章我們退一步問一個更根本的問題：**這一切到底是為了解決什麼？** 為什麼 2020 年之後全世界突然都在講 SBOM？

→ [Ch 1 為什麼需要 SBOM](./01-why-sbom.md)
