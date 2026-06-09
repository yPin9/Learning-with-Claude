# Ch 34 — 安全與責任揭露

> **目標**：學會維護者的安全責任——SECURITY.md 與漏洞的私下回報、責任揭露（responsible disclosure）流程、CVE 與安全公告、Dependabot 與相依安全、供應鏈攻擊與防護。你的專案有漏洞 = 所有使用者的風險。安全是維護者最不能馬虎的責任。

> **環境**：GitHub。前置：Ch 28（維護者責任）、Ch 33（SECURITY.md 在基礎設施裡）。

## 為什麼安全是維護者最嚴肅的責任

承 Ch 28——「擁有專案」意味使用者依賴你。在安全上，這個責任最重：**你的專案有漏洞，不只你受害，而是所有使用你的人都暴露在風險中**。一個被廣泛使用的函式庫的漏洞（想想 Log4Shell、Heartbleed），影響的是全世界成千上萬的系統。

而且開源是**供應鏈**的一環——你的 code 被別人依賴、別人又被更多人依賴，漏洞會沿著依賴鏈擴散。維護者在安全上的疏忽，後果遠超專案本身。這章是維護者責任中最不能馬虎的一塊。

## 先建立直覺：漏洞要「先修好再公開」

安全漏洞和普通 bug 最大的不同：**公開的時機。**

```
   普通 bug：公開討論沒問題（在 issue 裡），越多人看越快修。

   安全漏洞：
   公開 = 告訴全世界的攻擊者「這裡有洞，快來打」
        而使用者還沒有修補可以裝 → 災難（zero-day）

   所以安全漏洞要：
   私下回報 → 私下修好 → 發布修補版 → 給使用者時間升級 → 才公開細節
                                                          (責任揭露)
```

核心原則：**安全漏洞先私下處理、修好並發布修補後才公開**——這叫 **responsible disclosure（責任揭露）**。在使用者有修補可裝之前公開漏洞細節，等於幫攻擊者。這個「時機」的紀律，是安全處理和普通 bug 處理的根本差別。

## SECURITY.md：提供私下回報管道

承 Ch 33——你的專案要有 `SECURITY.md`，告訴人「發現漏洞**別開公開 issue**，私下這樣回報」：

```markdown
# Security Policy

## Reporting a Vulnerability

**Please do NOT open a public issue for security vulnerabilities.**

Instead, report privately via:
- GitHub Security Advisories (preferred): [Report a vulnerability] button
  in the Security tab
- Or email: security@yourproject.org

We will acknowledge within 48 hours and work with you on a fix and
coordinated disclosure timeline.

## Supported Versions
| Version | Supported |
|---------|-----------|
| 2.x     | ✅        |
| 1.x     | ❌        |
```

為什麼必須有：

- **沒有私下管道 = 善意的回報者只能開公開 issue**——一開公開 issue，漏洞就曝光了（攻擊者也看得到）。
- SECURITY.md 給回報者一個「正確的、私下的」管道，避免無意中公開漏洞。

GitHub 會在 repo 的 Security 頁面顯示 SECURITY.md，且提供 **Private Vulnerability Reporting**（Security tab 的 "Report a vulnerability" 按鈕）——讓回報者私下提交、和維護者私密討論修復。建議在 repo 設定開啟這個功能。

## 責任揭露的流程

收到一個私下的漏洞回報後，維護者的標準流程：

```
   1. 確認收到（快速回應回報者，48h 內）
        │
   2. 評估與複現（這是真漏洞嗎？嚴重程度？）
        │
   3. 私下修復（在私密的 fork / draft security advisory 裡修，不公開）
        │
   4. 準備修補版本（patch release，可能要 backport 到舊版，Ch 9/32）
        │
   5. 協調揭露時間（和回報者議定何時公開——給使用者升級的時間）
        │
   6. 發布修補版 + 公開安全公告（CVE/advisory）
        │
   7. 致謝回報者（credit，除非他要求匿名）
```

關鍵：

- **快速回應**：回報者花心力負責任地私下回報，至少快速確認收到（別讓善意的人覺得被無視，否則他可能改成公開揭露施壓）。
- **私下修**：用 GitHub Security Advisories 的私密 fork 修（不在公開 PR/commit 留痕跡——公開的 commit 會洩漏漏洞）。
- **協調時間**：和回報者議定揭露日（通常給 90 天或議定的期限），讓你有時間修、使用者有時間升。
- **致謝**：credit 回報者是責任揭露文化的核心（回報者的回報是為了讓專案更安全，致謝是基本尊重，也鼓勵未來的回報）。

> 認識論誠實：揭露時間有不同流派——有些主張固定 90 天（如 Google Project Zero），有些協調議定。但核心共識是「給使用者升級的時間」。如果維護者拖延不修，回報者可能在期限後公開（施壓）——這是正當的（無限期保密等於讓使用者一直暴露）。維護者的責任是及時修，不是無限期壓著。

## CVE 與安全公告

修好後，正式的安全漏洞會發布**公告**讓使用者知道：

- **CVE（Common Vulnerabilities and Exposures）**：漏洞的全球唯一編號（如 `CVE-2024-12345`）。讓全世界能一致地指涉這個漏洞。
- **GitHub Security Advisory（GHSA）**：GitHub 上的安全公告。維護者能透過 GitHub 申請 CVE、發布 advisory——它會通知所有依賴你專案的 repo（透過 Dependabot），讓他們知道「你用的版本有漏洞，快升級」。

```
   你發布 GHSA →
   ├─ 取得 CVE 編號
   ├─ 公告漏洞細節 + 影響版本 + 修補版本
   └─ GitHub 自動通知所有依賴你的專案（Dependabot alert）
        → 使用者收到「你依賴的 X 有 CVE-2024-xxx，升到 vY」
```

這個「公告 → 自動通知下游」的機制，是供應鏈安全的關鍵——讓漏洞資訊沿依賴鏈傳遞，使用者能及時升級。

## Dependabot：相依的安全

維護者不只要管自己 code 的安全，還要管**你依賴的東西**的安全。你的專案依賴一堆套件，那些套件有漏洞 = 你的專案有漏洞。

**Dependabot**（GitHub 內建）自動：

```
   ├─ Dependabot alerts：你的相依有已知漏洞（CVE）時警告你
   ├─ Dependabot security updates：自動開 PR 把有漏洞的相依升到安全版
   └─ Dependabot version updates：定期開 PR 更新相依（保持最新）
```

```yaml
# .github/dependabot.yml
version: 2
updates:
  - package-ecosystem: "pip"        # 或 npm/cargo/...
    directory: "/"
    schedule:
      interval: "weekly"
```

開啟 Dependabot（Settings → Security）後，你的相依有漏洞會自動收到警告 + 修補 PR——維護者不用手動盯每個相依的安全公告。這是「管理供應鏈安全」的基本配備。

## 供應鏈攻擊：新的威脅

近年最嚴重的開源安全威脅：**供應鏈攻擊**——攻擊者不直接打目標，而是打目標依賴的開源套件，藉此滲透下游所有使用者。

```
   攻擊手法（維護者要警覺）：
   ├─ 惡意 PR：貢獻者偷偷在 PR 裡藏後門（Ch 29 審外部 PR 的安全考量）
   ├─ 帳號劫持：維護者帳號被盜，發布惡意版本
   ├─ typosquatting：發布名字很像熱門套件的惡意套件（誘人裝錯）
   ├─ 依賴混淆：利用 public/private registry 的命名漏洞
   └─ 接管廢棄套件：認領沒人維護的套件再下毒
```

著名案例：xz utils 後門（2024，一個潛伏多年的「貢獻者」逐步取得維護權再植入後門，差點滲透全球 Linux）——這個案例震撼了開源界，凸顯供應鏈與「信任」的脆弱。

維護者的防護：

- **審 PR 警覺**（Ch 29）：尤其外部 PR、動到 build/CI/依賴的可疑改動、obfuscated code。
- **保護帳號**：2FA（Ch 0）、強認證——帳號被盜=災難。
- **保護發布流程**：簽署 release（Ch 32）、限制誰能發布、CI 的 secret 管理。
- **謹慎給權限**（Ch 31）：co-maintainer 權限要給信任的人（xz 案就是信任被濫用）。
- **CI secret 安全**（Ch 14）：fork PR 拿不到 secret、限制 workflow 權限——防惡意 PR 偷 secret。

> xz 案的教訓：開源的信任模型有根本脆弱性——「任何人可貢獻」也意味「惡意者可長期潛伏取得信任再下手」。這沒有完美解，但維護者的警覺（審查、權限、簽署）是第一道防線。這也是為什麼維護者審外部 PR、給權限要格外謹慎（Ch 29/31）。

## 別把 secret 提交進 repo

承 Ch 0/27/36——一個維護者和貢獻者都要避免的安全雷：**別把密鑰/token/密碼提交進 git**：

```
   誤提交 API key / token / 密碼 / 私鑰 進 repo：
   - 公開 repo = 全世界都看得到（爬蟲幾秒內就掃到並濫用）
   - 即使刪掉 commit，git 歷史還在（要徹底清除很麻煩，Ch 36）

   防護：
   ├─ .gitignore 排除 secret 檔案（.env 等）
   ├─ pre-commit 的 detect-private-key / gitleaks（Ch 27）擋在 commit 前
   ├─ GitHub secret scanning（自動掃描已知格式的 secret 並警告/撤銷）
   └─ 真的誤提交了：立刻撤銷該 secret（不是只刪 commit！歷史還在，Ch 36）
```

關鍵：**誤提交 secret，第一件事是「撤銷/輪換那個 secret」**（假設它已洩漏），不是只刪 commit——刪 commit 救不了已經洩漏的 key（爬蟲可能已掃走、git 歷史還在）。Ch 36 講怎麼從歷史清除（但前提是 secret 已先撤銷）。

## 一個完整的安全基礎設施

維護者該為專案建立的安全配備（綜合本章 + 前面）：

```
   ☑ SECURITY.md（私下回報管道，Ch 33）
   ☑ GitHub Private Vulnerability Reporting（開啟）
   ☑ Dependabot alerts + security updates（相依安全）
   ☑ secret scanning（防誤提交 secret）
   ☑ pre-commit 擋 secret（Ch 27）
   ☑ 帳號 2FA、簽署 release（Ch 0/32）
   ☑ 審外部 PR 的安全警覺（Ch 29）
   ☑ 謹慎的 co-maintainer 權限（Ch 31）
```

## 踩雷集錦

1. **沒有 SECURITY.md / 私下回報管道**：善意回報者只能開公開 issue = 漏洞曝光。提供私下管道。
2. **在公開 issue 討論安全漏洞**：等於通知攻擊者。漏洞一律私下處理（責任揭露）。
3. **公開修復 commit 洩漏漏洞**：在公開 PR/commit 修安全 bug，commit message/diff 洩漏漏洞細節。用 GitHub Security Advisory 的私密 fork 修。
4. **拖延不修安全回報**：回報者可能在期限後公開施壓（正當）。及時修是責任。
5. **不管相依的安全**：你依賴的套件有漏洞 = 你有漏洞。開 Dependabot。
6. **誤提交 secret 只刪 commit**：歷史還在、可能已洩漏。第一件事是**撤銷該 secret**，再清歷史（Ch 36）。
7. **隨意給 co-maintainer 權限**：xz 案的教訓——信任被濫用。權限給信任的人、謹慎。

## 進階：再往深一層

- **GitHub Security Advisories 的私密協作**：建一個 draft advisory，它給你一個私密 fork 修漏洞、私密討論、議定揭露——修好後一鍵發布 + 申請 CVE。
- **CVSS 評分**：用 CVSS 標準化評估漏洞嚴重程度（分數 + 向量），讓使用者判斷急迫性。
- **backport security fix**（Ch 9/32）：還在支援的舊版本也要修（cherry-pick 修復到 release branch），不是只修最新版。
- **SBOM（軟體物料清單）**：列出專案所有相依的清單，供應鏈透明化（法規漸要求）。
- **簽署與來源證明**：sigstore、SLSA 等讓使用者驗證「這個 release 真的來自你、沒被竄改」——對抗供應鏈攻擊。
- **fuzzing / 安全測試**：OSS-Fuzz（Google 免費為開源做 fuzzing）等主動找漏洞——呼應 security/afl_plus_plus 課程。
- **bug bounty**：大專案設賞金鼓勵安全研究者回報（而非賣給黑市）。

## 動手練習

1. 看三個大型開源專案的 SECURITY.md，對照本章——它們怎麼說「私下回報」、有沒有 supported versions、揭露流程。
2. 為一個（你的或假想的）專案寫一個 SECURITY.md（私下回報管道 + supported versions）。
3. 在你的 repo 開啟 Dependabot alerts（Settings → Security），看它有沒有抓到相依的已知漏洞。
4. 看 GitHub Security Advisories 的流程（Security tab → Advisories → New draft）——理解私密修復+揭露怎麼運作（不用真的發）。
5. 研究 xz utils 後門案（2024）的來龍去脈——理解供應鏈攻擊與「信任」的脆弱，以及維護者該警覺什麼。
6. 設一個 pre-commit 的 detect-private-key / gitleaks（Ch 27），故意試 commit 一個假的 key，看它被擋——體驗「擋 secret 在 commit 前」。

## 本章重點整理

- 安全是維護者最嚴肅的責任——你的漏洞是所有使用者的風險，且沿供應鏈擴散。
- 安全漏洞和普通 bug 的根本差別是**公開時機**：責任揭露 = 私下回報 → 私下修 → 發布修補 → 給使用者升級時間 → 才公開。公開未修的漏洞 = 幫攻擊者。
- SECURITY.md 提供私下回報管道（別讓人開公開 issue 曝光漏洞）；GitHub Private Vulnerability Reporting + Security Advisories 支援私密修復與協調揭露。
- 流程：快速確認 → 評估 → 私下修（不公開 commit）→ 修補版（含 backport）→ 協調揭露 → 公告（CVE/GHSA，自動通知下游）→ 致謝。
- Dependabot 管相依安全（alert + 自動修補 PR）；供應鏈攻擊（惡意 PR、帳號劫持、xz 案）要維護者警覺（審查、權限、簽署、2FA）。
- 別提交 secret 進 git；誤提交第一件事是**撤銷該 secret**（不是只刪 commit）。

## 自我檢核

- [ ] 安全漏洞和普通 bug 在「怎麼處理」上最大的差別是什麼？為什麼？
- [ ] 責任揭露的流程是什麼？為什麼要私下修、協調揭露時間？
- [ ] SECURITY.md 的作用是什麼？沒有它會怎樣？
- [ ] Dependabot 解決什麼問題？供應鏈攻擊有哪些手法、維護者怎麼防？
- [ ] 誤提交 secret 進 repo，第一件該做的事是什麼？為什麼不是只刪 commit？

## 延伸閱讀

### 官方文件 / 指南

- **[GitHub Docs: Security advisories & Private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories)** 與 **[Dependabot](https://docs.github.com/en/code-security/dependabot)**
  - **讀哪裡**:Security Advisories 的流程、Private Vulnerability Reporting、Dependabot 設定。
  - **和本章的關聯**:本章機制的操作權威。

- **[Open Source Guides: Security best practices for maintainers](https://opensource.guide/)** / **[OpenSSF Best Practices](https://www.bestpractices.dev/)**
  - **讀哪裡**:維護者安全實踐、責任揭露。
  - **和本章的關聯**:安全責任的綜合指南。

### 案例 / 文章

- **[The xz utils backdoor (2024) 分析](https://research.swtch.com/xz-timeline)** — Russ Cox（時間線整理）
  - **這篇說什麼**:xz 後門案的完整時間線——攻擊者怎麼長期潛伏取得信任再下毒。
  - **為什麼值得讀**:理解供應鏈攻擊與開源信任脆弱性的最重要案例；維護者必讀的警世故事。

- **[Coordinated disclosure / responsible disclosure](https://en.wikipedia.org/wiki/Coordinated_vulnerability_disclosure)** 與 **[CVE 制度](https://www.cve.org/)**
  - **讀哪裡**:責任揭露的概念、CVE 怎麼運作。
  - **和本章的關聯**:揭露流程與 CVE 的背景。

Part 6 的維護者技能都齊了。用練習 F 把它們綜合起來：把一個你的小專案，打造成一個「準備好接受貢獻、安全、可持續」的開源專案。

→ [練習 F：打造一個準備好接受貢獻的專案](./practice-f-contribution-ready.md)
