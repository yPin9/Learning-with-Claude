# Ch 0 — 環境搭建

> **目標**：裝好 uv + specify CLI、Claude Code，選配 Kiro，建立一個練習用 repo，並準備好整門課會用到的目錄結構。
>
> **環境**：以下所有工具版本、指令、定價資訊均標注「查證日期 2026-06-30」；AI 工具迭代速度快，實際操作前請以官方文件為準。

## 為什麼先裝環境，而不是先學概念？

大多數概念課把環境搭建藏在第一章末尾，或者乾脆說「自己去裝吧」。這門課反過來：先跑起來，再講原理。

原因有兩個。第一，規格驅動開發（Spec-Driven Development，SDD）的核心張力——在「AI 能不能理解我的意圖」這一點上——必須親眼見過工具拒絕你、誤解你、幫你寫出對的程式，才會有真實感受。光讀理論是沒有感覺的。第二，後面 Ch 27–32 會對工具做深入解剖，那時假設你的環境已經跑起來，可以邊讀邊驗。

這一章的目標是：30 分鐘之內，從零到可以對著一個真實 repo 跑出第一條 `/speckit.specify` 指令。

---

## 環境全貌

```
你的機器
│
├── uv  (Python 工具管理器)
│   └── specify  (Spec Kit CLI，由 uv 安裝)
│
├── Claude Code  (AI 代理，執行 /speckit.* 指令)
│       或
│   Kiro  (選配：AWS 出品的 SDD 整合 IDE)
│
└── sdd-practice/          ← 本課練習 repo
    ├── .specify/          ← Spec Kit scaffold
    ├── .claude/           ← Claude Code 整合 (若選 claude)
    ├── specs/             ← 每個 feature 的規格目錄
    └── src/               ← 課程範例原始碼
```

三個元素彼此分工：`specify` CLI 負責初始化 scaffold 和升級工具本身；AI 代理（Claude Code 或 Kiro）負責在 session 中執行 `/speckit.*` 系列指令；你的 repo 是「所有規格和程式碼共存的地方」，也是本課每一個 practice 的試驗場。

---

## 步驟一：安裝 uv

uv 是 Astral 出品的 Python 工具管理器，速度遠超 pip/pipx，而且不要求你先建立 virtualenv。Spec Kit 官方推薦用 uv 安裝。

**macOS / Linux**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Windows (PowerShell)**

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

安裝後確認：

```bash
uv --version
# 預期輸出類似：uv 0.x.x (...)
```

> 若你的機器已有 pipx 且不想安裝 uv，之後的 `uv tool install` 改用 `pipx install` 亦可。本課以 uv 為主線。

---

## 步驟二：安裝 specify CLI（Spec Kit）

GitHub Spec Kit（github/spec-kit）是 GitHub 於 2025-09-02 發布的開源 SDD 工具包。它的 Python CLI 叫做 `specify`，透過 uv 安裝（查證日期 2026-06-30）：

```bash
# 將 vX.Y.Z 換成最新 tag，截至 2026-06-29 為 v0.11.10
uv tool install specify-cli \
  --from git+https://github.com/github/spec-kit.git@v0.11.10
```

前置需求：Python 3.11 以上、Git。uv 會自動管理 Python 版本，通常不需要另行安裝。

確認安裝：

```bash
specify --version
# 預期：specify-cli 0.11.10 (或你裝的版本)
```

查看最新 tag 的方法：

```bash
# 直接看 GitHub releases 頁面
# https://github.com/github/spec-kit/releases
# 或用 gh CLI
gh release view --repo github/spec-kit --json tagName -q .tagName
```

> **版本依賴（version-dependent）**：Spec Kit 迭代速度非常快——截至 2026-06-29 已有 175 個以上的 release，且指令名稱在早期版本和現行版本之間有過重大更名（原本的裸指令 `/specify`、`/plan`、`/tasks` 在現行版本已全部改為 `/speckit.*` 命名空間）。本課所有指令以現行版（v0.11.x）為準，操作前請先用 `specify --version` 確認版本，並查閱對應的 README。

---

## 步驟三：安裝 Claude Code

Claude Code 是 Anthropic 提供的 AI 代理工具，可在終端機內直接與 Claude 互動並讀寫本地檔案。Spec Kit 把它列為一流（first-class）整合對象。

```bash
npm install -g @anthropic-ai/claude-code
```

需求：Node.js 18 以上。

確認：

```bash
claude --version
```

Claude Code 需要 Anthropic API 金鑰。若還沒有，前往 console.anthropic.com 建立並設定環境變數：

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
# Windows PowerShell:
# $env:ANTHROPIC_API_KEY = "sk-ant-..."
```

> **定價提醒（查證日期 2026-06-30）**：Claude Code 依你選的 Claude 模型計費，具體費率以 Anthropic 官方定價頁為準。本課練習量不大，但建議先設定用量提醒（Console → Settings → Usage Limits），避免意外超支。

---

## 步驟四（選配）：安裝 Kiro

Kiro 是 AWS 出品的獨立 IDE，底層為 Code OSS（VS Code 的開源核心），它把三份規格文件（requirements.md、design.md、tasks.md）的生成流程直接整合進 UI。如果你習慣 GUI 工作流、或者想同時對比兩種工具，可以選裝。

到 [kiro.dev](https://kiro.dev) 下載對應平台的桌面安裝包，登入方式支援 GitHub、Google、AWS Builder ID、或 IAM Identity Center。

**定價（查證日期 2026-06-30，version-dependent，以官方最新為準）**：

| 方案 | 月費 | 每月額度 | 超額費率 |
|------|------|----------|----------|
| Free | $0 | 50 credits | — |
| Pro | $20 | 1,000 credits | $0.04/credit |
| Pro+ | $40 | 2,000 credits | $0.04/credit |
| Pro Max | $100 | 5,000 credits | $0.04/credit |
| Power | $200 | 10,000 credits | $0.04/credit |

Kiro 的預設模型是「Auto」混合模式，比直接選特定 Sonnet 版本便宜約 23%（等量任務在 Auto 消耗 X credits，直接用 Sonnet 則約 1.3X；查證日期 2026-06-30，Sonnet 版本 version-dependent，以官方最新為準）。

Kiro 對本課的用途主要在 Ch 30，現在裝起來備用即可；Ch 0–29 用 Spec Kit + Claude Code 就夠了。

---

## 步驟五：建立練習 repo

```bash
mkdir sdd-practice && cd sdd-practice
git init
```

用 `specify init` 初始化 Spec Kit scaffold，選擇 Claude Code 作為整合對象：

```bash
specify init . --integration claude
```

指令跑完後，目錄結構應該長這樣（版本不同細節可能略有出入）：

```
sdd-practice/
├── .specify/
│   ├── memory/
│   │   └── constitution.md        ← 專案守則（待你填寫）
│   ├── scripts/
│   │   ├── bash/                  ← sh 版本腳本
│   │   │   ├── check-prerequisites.sh
│   │   │   ├── common.sh
│   │   │   ├── create-new-feature.sh
│   │   │   ├── setup-plan.sh
│   │   │   └── setup-tasks.sh
│   │   └── ps/                    ← PowerShell 版本腳本
│   └── templates/
│       ├── spec-template.md
│       ├── plan-template.md
│       └── tasks-template.md
└── .claude/
    └── commands/                  ← /speckit.* 指令的 prompt 檔案
```

> **Windows 使用者**：若你的環境主要是 PowerShell，可以加 `--script ps` 讓 scaffold 改用 PowerShell 腳本：`specify init . --integration claude --script ps`

驗證 scaffold 是否正確：

```bash
specify check
# 預期：列出 prerequisites 狀態，全部 OK
```

接著建立課程會用到的目錄：

```bash
mkdir -p src specs
```

初始 commit：

```bash
git add -A
git commit -m "chore: init sdd-practice with spec-kit scaffold"
```

---

## 步驟六：冒煙測試（smoke test）

在 `sdd-practice` 目錄下啟動 Claude Code：

```bash
claude
```

進入互動模式後，試跑第一條 Spec Kit 指令：

```
/speckit.constitution
```

Claude Code 應該會提示你描述專案的核心原則（用途、技術限制、程式碼品質要求等）。輸入一個簡單描述，例如：

```
這是一個用來學習 SDD 的練習 repo。
語言：TypeScript 或 Python，視章節而定。
品質要求：所有公開介面必須有型別標注；每個功能必須有至少一個失敗測試案例。
```

確認 `.specify/memory/constitution.md` 被寫入後，冒煙測試通過。

如果你看到「Unknown slash command」或類似錯誤，最常見原因是 `.claude/commands/` 目錄沒有正確產生——執行 `specify check` 診斷，或重新跑 `specify init . --integration claude --force` 覆蓋。

---

## 踩雷集錦

**錯誤直覺一：「舊文章用 `/specify`，我照著打就好」**

正確認識：`/specify`、`/plan`、`/tasks` 是 Spec Kit 2025 年 9 月最初發布時的裸指令名稱。現行版本（v0.11.x）已全部改為 `/speckit.specify`、`/speckit.plan`、`/speckit.tasks` 等帶命名空間的形式，舊指令會報錯或什麼都不做。如果你在 Google 搜到的教學步驟對不起來，先確認 Spec Kit 版本。

**錯誤直覺二：「pipx 和 uv 都行，差不多」**

正確認識：功能上兩者都能安裝 CLI 工具，但 Spec Kit 的自動升級邏輯（`specify self upgrade`）能偵測到 uv 和 uvx 環境，並利用 uv 的快取機制做更順滑的升級。用 pipx 也能跑，但在升級路徑上可能遇到邊角 bug。本課統一用 uv，避免在工具問題上浪費時間。

**錯誤直覺三：「Kiro 和 Spec Kit 選一個就好，功能一樣」**

正確認識：兩者概念相近但實作截然不同。Spec Kit 是 CLI + prompt-file 架構，可以跨任何支援 slash command 的 AI 代理（30+ 個整合）；Kiro 是整合好的獨立 IDE，三份規格文件的生成嵌入 UI 流程中，學習曲線較低但靈活性較低。Ch 32 會做詳細橫向對比，現在先用 Spec Kit 建立心智模型，Kiro 當作 Ch 30 的實驗對象。

**錯誤直覺四：「constitution 以後可以改，現在隨便填」**

正確認識：constitution.md 是整門課程的 LLM 行為錨點——它告訴 Claude 這個 repo 的優先級和限制。如果填得太空泛（例如「這是一個學習 repo，沒什麼特別要求」），後面所有 `/speckit.plan` 和 `/speckit.tasks` 的輸出品質都會受影響。冒煙測試時花 5 分鐘把真實約束寫清楚，後面省的時間遠多於此。

**錯誤直覺五：「Windows 上只能用 WSL」**

正確認識：Spec Kit 的 `specify init` 明確支援 `--script ps` 旗標，會產生 PowerShell 版本的腳本取代 bash 腳本。Claude Code 本身也支援 Windows PowerShell。WSL 不是必需品——雖然 WSL 能給你更接近 Linux 的環境，但本課的所有練習在原生 Windows 上都能跑。

---

## 進階設定（可選）

### 多代理並排測試

Spec Kit 允許同一個 repo 同時安裝多個代理整合，方便比較不同 AI 代理對同一份 spec 的解讀差異：

```bash
# 在已初始化的 repo 中再加 Cursor 整合
specify init . --integration cursor-agent --ignore-agent-tools
```

`--ignore-agent-tools` 避免覆蓋已有的 agent 設定。注意：不同代理的 slash command 檔案裝在各自的目錄下（`.claude/`、`.cursor/` 等），不會互相衝突。

### 自動升級

```bash
specify self check    # 檢查是否有新版本
specify self upgrade  # 升級到最新 release
specify self upgrade --tag v0.11.10  # 或升級到特定版本
```

Spec Kit 版本更新很快，建議每隔幾週確認一次。

---

## 目錄結構備忘

整門課的練習都放在同一個 repo 下，按課程章節組織：

```
sdd-practice/
├── .specify/              ← Spec Kit 內部（不要手動改）
├── .claude/               ← Claude Code 整合
├── specs/                 ← 每個 feature spec 目錄（Spec Kit 自動管理）
│   └── 001-first-feature/ ← Ch 27 練習後會出現
│       ├── spec.md
│       ├── plan.md
│       └── tasks.md
├── src/                   ← 課程範例程式碼
│   ├── practice-a/        ← 練習 A 用
│   ├── practice-b/        ← 練習 B 用
│   └── ...
└── docs/                  ← 自己的筆記、決策紀錄（optional）
```

`specs/` 下的目錄由 Spec Kit 的 `create-new-feature.sh` 腳本自動建立並編號（001、002…）。不要手動建，讓工具管理，否則編號會衝突。

---

## 動手練習

1. 完成上面六個步驟，確認 `specify check` 全部通過。
2. 在 constitution.md 補上你自己的程式風格偏好（語言、測試框架、命名慣例），讓後面的練習有個具體的約束背景。
3. 查看 `.specify/templates/spec-template.md`，讀懂它的結構——特別注意 `[NEEDS CLARIFICATION: ...]` 標記是什麼意思（Ch 27 會深入講，這裡先有印象）。
4. （選配）如果裝了 Kiro：開一個新的 Spec session，試著輸入一個小功能需求，看它自動生成的 requirements.md 結構與 Spec Kit 的 spec-template.md 有什麼異同。記錄下來，Ch 30 和 Ch 32 會用到這個對比。

---

## 本章重點整理

- uv 是 Spec Kit CLI 的推薦安裝方式；Python 3.11+ 和 Git 是前置需求。
- `specify init . --integration claude` 會 scaffold `.specify/` 目錄和 `.claude/commands/` 的 prompt 檔案。
- 現行 Spec Kit 指令全部帶 `/speckit.*` 命名空間；舊版裸指令已棄用。
- constitution.md 是整個工具鏈的行為錨點；它越具體，後面 AI 輸出品質越高。
- Kiro 是選配的 GUI 替代方案；Ch 27–29 深入 Spec Kit，Ch 30 再補 Kiro。
- 所有工具的版本、定價、指令都是 version-dependent，永遠先查官方文件。

---

## 自我檢核

- [ ] 用自己的話解釋：為什麼 Spec Kit 要用 `/speckit.*` 命名空間，而不是裸的 `/specify`？（提示：如果你只記得「改過名了」，那還不夠——為什麼改？有什麼好處？）
- [ ] `specify init` 的 `--integration` 旗標控制什麼？它對你選的 AI 代理有什麼影響？
- [ ] constitution.md 放在哪裡？它在 Spec Kit 工作流裡扮演什麼角色？如果不寫，會怎樣？
- [ ] 如果一個同事說「我在 2025 年 9 月的教學文章裡看到用 `/plan` 指令」，你會怎麼回答他？
- [ ] Kiro 的 Free 方案有幾個 credits？這個數字本身不重要——面試被問時，你會怎麼解釋 Kiro 的計費模型設計動機？

---

## 延伸閱讀

- **github/spec-kit README**（https://github.com/github/spec-kit）：Spec Kit 的一手文件，「Get Started」和「Available Slash Commands」兩節是讀懂本課後續章節的基礎。特別看 acknowledgement 段落了解 John Lam 的研究背景。
- **Den Delimarsky，"Spec-driven development with AI"，GitHub Blog，2025-09-02**（https://github.blog/ai-and-ml/generative-ai/spec-driven-development-with-ai-get-started-with-a-new-open-source-toolkit/）：Spec Kit 的原始發布文章，解釋「把 coding agent 當字面意思的 pair programmer」這個核心動機，也是了解早期指令集（未帶命名空間）與現行版差異的好對照。
- **Spec Kit Releases**（https://github.com/github/spec-kit/releases）：快速確認最新版本 tag 和 changelog，特別是「BREAKING」類更新。本課建議每次開始新的長期練習前先檢查一次。
- **uv 官方文件**（https://docs.astral.sh/uv/）：深入了解 `uv tool install` 的隔離機制和自動管理 Python 版本的原理。如果你之後要把 Spec Kit 整合進 CI/CD，這份文件的「uv in CI」段落很有用。
- **Kiro Docs — Specs**（https://kiro.dev/docs/specs/）：Kiro 三份規格文件的官方定義，查證日期 2026-06-30。Ch 30 的核心閱讀材料；現在可以先掃一眼，了解 requirements.md / design.md / tasks.md 的結構，對比 Spec Kit 的 spec.md / plan.md / tasks.md。

---

環境搭好了，工具也跑起來了。下一章我們退後一步，問一個更根本的問題：「規格」這件事為什麼在 AI 時代突然變重要了？在 GitHub Copilot 出現之前，需求和規格其實一直是軟體工程最難解決的問題之一——但那個難題長期被「讓工程師直接改 code」這個應急方案蓋住了。AI coding agent 把這塊遮羞布掀開了。

→ [Ch 1 為什麼「規格」突然重要了：AI 把瓶頸推到意圖上](./01-why-specs-matter-now.md)
