# 練習 D — 對真實專案發出第一個真 PR

> **目標**：這是整門課的轉捩點——把 Part 1-4 學的全部，用在一個**真實的開源專案**上，發出你的第一個真 PR。不是模擬、不是自己的 repo——是對著真實維護者、真實流程的貢獻。完成後你就從「會協作」變成「貢獻過開源」。

> 前置：Part 4 全部（Ch 16-21），以及 Part 1-3 的所有技能。

## 背景與動機

前面所有東西都是為了這一刻。練習 C 你在自己的 repo 走通了流程，但那是安全的沙盒。真實貢獻不一樣：對象是陌生維護者、要遵守別人的規矩、要面對真實的 review、PR 會永久留在你的 GitHub 檔案上。

這個練習刻意「真實」——因為**開源是學不會的，只能做會**。讀一百遍 PR 流程，不如真的發一個。第一個真 PR 會逼出你所有沒注意到的細節，也會給你最有價值的東西：「我貢獻過開源」的真實經驗與信心。

**重要原則**：這個練習的目標是**走通一次真實貢獻**，不是做大事。從最小、最安全的貢獻開始——一個被合併的 typo 修正，就是 100% 成功。

## 任務規格

### 你要做的事

對一個**真實的開源專案**，完成一次完整的貢獻：找到一個你能做的小貢獻 → 做足功課 → 發出 PR → 在 review 中迭代 → （理想）被合併。

### 安全的起點（依風險由低到高，挑一個）

```
   1. 練習用 repo（零風險，先暖身）
      - firstcontributions/first-contributions（專為第一次設計，加你的名字到清單）
      - 各種 "first-timers-only" repo
      → 流程一模一樣，但維護者預期是新手，零壓力

   2. 文件 / typo 修正（低風險，推薦的「真」第一個）
      - 你在用的工具/套件，文件裡的 typo、壞連結、過時範例、跑不通的安裝步驟
      → 小、明確、易被接受

   3. good first issue（中等，進階目標）
      - 你能複現的小 bug，標 good first issue 且沒人在做
```

**建議路徑**：先用 firstcontributions 暖身（體驗真實 PR 介面但零壓力）→ 再對一個你真的在用的專案發一個 typo/文件 PR（真實貢獻）。行有餘力再挑 good first issue。

### 驗收標準

- [ ] **暖身**：對 firstcontributions（或類似）成功發一個 PR 並被合併（體驗真實流程）
- [ ] **真實貢獻**：對一個你在用的真實專案，發出一個有價值的 PR（typo/文件/小修）
- [ ] 全程遵守該專案的 CONTRIBUTING（commit 規範、測試、CLA/DCO）
- [ ] PR 有好的標題與描述（Ch 11）、關聯 issue（若有）
- [ ] CI 綠（若該專案有 CI，Ch 14）
- [ ] 得體地回應任何 review（Ch 13/19/20）
- [ ] 寫一份「貢獻心得」：做對什麼、踩了什麼雷、學到什麼

## 如果你卡住了

1. **找不到能做的貢獻？** 回 Ch 16：列你在用的工具，回想遇過的小問題（文件錯、範例跑不通、help 訊息怪）。或翻它的 good first issue。真的沒有就先做 firstcontributions 暖身。
2. **怕做不好、不敢發？** 先做 firstcontributions——它就是為了讓你「安全地體驗一次」設計的，搞砸了也沒關係。建立信心後再對真專案。
3. **不確定這個貢獻維護者要不要？** typo/文件修正通常直接做（不太需要問）。小功能/改行為先開 issue 問（Ch 12/18）。
4. **CONTRIBUTING 看不懂/要求很多？** 換一個流程更簡單的專案。不同專案門檻差很多，挑對新手友善的（Ch 16 健康度）。
5. **CLA bot 擋住我？** 看清楚再簽（Ch 21）。個人小貢獻通常 OK。不想簽就換不要 CLA 的專案。
6. **PR 發了沒人理？** 耐心（開源是業餘時間，Ch 20）。等幾週可禮貌 ping 一次。同時可以做別的貢獻，別卡在一個。

## 實作步驟建議

### Step 0：暖身——firstcontributions

```bash
# 對 firstcontributions/first-contributions 做一次完整 PR
# 它的 README 會手把手帶你：fork → clone → 加你的名字 → PR
# 目的：在零壓力下體驗真實的 GitHub PR 介面與流程
```

子目標：成功發一個真 PR 並被合併（通常很快），破除「發 PR」的心理障礙。

### Step 1：找到真實貢獻目標（Ch 16）

```bash
# 列你在用的工具，找一個有小問題的
gh issue list --repo <你在用的專案> --label "good first issue"
# 或就找文件 typo / 跑不通的範例
```

子目標：鎖定一個小、明確、你能做的真實貢獻。

### Step 2：做功課（Ch 17）

```bash
# 讀 CONTRIBUTING、複現問題（若是 bug）、認領（若需要）、設環境、跑測試
gh repo fork <專案> --clone
# 讀 CONTRIBUTING.md，記下規矩
```

子目標：有備而來——知道規矩、確認問題、環境就緒。

### Step 3：做改動 + 自我檢查（Ch 18）

```bash
git switch -c fix/the-thing upstream/main
# ...做改動（只做這一件事，守住 scope）...
git diff                          # 確認只改了該改的（沒被格式化污染）
# 本地跑 CI 會跑的（Ch 14）
git commit -s -m "..."            # 好 message（Ch 2）+ DCO 若需要
git push -u origin fix/the-thing
```

子目標：一個乾淨、scope 受控、commit 清楚的改動。

### Step 4：發 PR（Ch 11）

```bash
gh pr create --repo <專案> --base main \
  --title "..." --body "..."      # 好標題/描述，關聯 issue
gh pr checks --watch              # 確認 CI 綠
```

子目標：一個包裝良好、CI 綠的 PR，送到真實維護者面前。

### Step 5：迭代 + 溝通（Ch 13/19/20）

子目標：得體回應任何 review，改 code + 回應每條 comment + re-request，保持禮貌耐心。

### Step 6：心得反思

子目標：寫下做對/踩雷/學到什麼，為 Final Project（更大的貢獻）準備。

## 完整參考流程

**這沒有「標準答案」（每個人的真實貢獻不同），但這是一個完整的範例流程。**

<details>
<summary>點開一個完整的真實貢獻範例</summary>

假設你發現你常用的某 CLI 工具，README 的安裝步驟少了一步（在 macOS 上要先裝某個相依），導致你照做時失敗。這是完美的第一個真實貢獻。

### 暖身（firstcontributions）

```bash
gh repo fork firstcontributions/first-contributions --clone
cd first-contributions
git switch -c add-my-name
# 照它 README 把你的名字加進 Contributors.md
git add Contributors.md
git commit -m "Add <你的名字> to contributors list"
git push -u origin add-my-name
gh pr create --fill
# 通常很快被合併。你剛發了第一個真 PR！
```

### 真實貢獻

```bash
# 1. 找到目標：你用的 CLI 工具 README 安裝步驟有問題
# 2. 讀 CONTRIBUTING——假設它說：用 DCO（git commit -s）、PR 描述要說明動機

# 3. fork + clone
gh repo fork owner/cli-tool --clone
cd cli-tool
git fetch upstream

# 4. 複現：照 README 在乾淨環境裝，確認真的失敗（缺那步）

# 5. 搜尋有沒有人回報過這個（Ch 12）
gh issue list --repo owner/cli-tool --search "macOS install"
#    沒有 → 可以順便開個 issue，或直接在 PR 說明

# 6. 開 branch、改 README
git switch -c docs/fix-macos-install upstream/main
# ...在 README 安裝段補上 macOS 的前置步驟...
git diff                          # 確認只改 README 該改的段落

# 7. commit（DCO + 好 message）
git commit -s -m "docs: add missing macOS prerequisite to install steps

On macOS, \`brew install libfoo\` is required before \`make install\`,
otherwise the build fails with 'libfoo.h not found'. Add this step
to the README installation section."

# 8. push + PR
git push -u origin docs/fix-macos-install
gh pr create --repo owner/cli-tool --base main \
  --title "docs: add missing macOS prerequisite to install steps" \
  --body "$(cat <<'EOF'
## What
Add the missing `brew install libfoo` step to the macOS install instructions.

## Why
Following the current README on a clean macOS fails at `make install`
with 'libfoo.h not found', because libfoo isn't mentioned as a
prerequisite. This tripped me up when setting up the project.

## Testing
Followed the updated steps on a clean macOS 14 — install now succeeds.
EOF
)"

# 9. 等 review，得體回應（Ch 13/19/20）
#    維護者可能說「謝謝！能不能也加 Linux 的對應說明？」
#    → 你改、push、回應："Good idea, added the Linux equivalent."
#    → 維護者 approve + merge

# 10. 合併後
git switch main && gh repo sync
```

### 心得反思範例

```
做對的：
- 從真實遇到的問題出發（複現過、有動機、描述有真實場景）
- 守住 scope（只改安裝步驟，沒順手改別的）
- commit/PR 都講 why（為什麼缺這步會失敗）
- 遵守 CONTRIBUTING（DCO sign-off）

踩的雷：
- 一開始 git diff 發現編輯器把整個 README 重排了（trailing whitespace）
  → 還原，只留真正的改動
- 忘了 git commit -s，CLA/DCO bot 擋住 → 重新 commit 加 sign-off

學到的：
- 真實 PR 比練習緊張，但流程一模一樣
- 維護者很友善（小文件修正通常很歡迎）
- 「從自己遇到的問題貢獻」真的最容易成功
```

**流程說明**：

這個範例體現了 Part 4 的精髓：
- **從自己在用的工具、自己遇到的問題出發**（Ch 16）——最容易成功的貢獻類型。
- **先暖身再實戰**（firstcontributions → 真專案）——降低心理門檻。
- **小 scope、文件級**（Ch 18）——完美的第一個真實貢獻，低風險高成功率。
- **做足功課**（複現、讀 CONTRIBUTING、DCO，Ch 17/21）。
- **好包裝 + 得體溝通**（Ch 11/13/19/20）。

你的真實貢獻會不一樣（不同專案、不同問題），但骨架相同。重點不是「做了多大的事」，是「完整走通一次真實貢獻」。

</details>

## 檢查點

| 階段 | 檢查 |
|---|---|
| 暖身 | firstcontributions PR 合併（體驗真實流程）|
| 找目標 | 鎖定一個小、明確、你能做的真實貢獻 |
| 功課 | 讀了 CONTRIBUTING、複現了問題（若 bug）、環境就緒 |
| 改動 | scope 受控、`git diff` 乾淨、commit message 好 |
| PR | 好標題/描述、CI 綠、符合專案規範 |
| 迭代 | 得體回應 review（若有）|
| 結果 | （理想）被合併；或學到為什麼沒合併 |

## 延伸挑戰（加分）

1. **修一個真正的 bug**：不只文件，挑一個 good first issue 的小 bug，複現 → 修 → 加測試 → PR（Ch 17 的 test-first）。
2. **連續貢獻**：第一個合併後，趁熱在同專案找第二個——你已熟悉它的 codebase 和流程，第二個更快（Ch 37 建立聲譽）。
3. **貢獻你修過的 bug**：你工作/side project 中用某開源套件遇到 bug 並 workaround 過——把真正的修復貢獻回上游（最有價值的貢獻類型）。
4. **non-code 貢獻**：翻譯文件、改善 error message、補測試——體驗「貢獻不只是寫功能」。
5. **多輪 review 的耐力**：挑一個會經歷幾輪 review 的稍大貢獻，練 Ch 19 的迭代耐力。

## 自我檢核

- [ ] 我真的對一個真實專案發了 PR（不是模擬）
- [ ] 我從「自己在用、自己遇過問題」的角度找貢獻，而非硬挑陌生大專案
- [ ] 我遵守了該專案的 CONTRIBUTING、做足了功課（複現/CLA/測試）
- [ ] 我的 PR 包裝良好（標題/描述/CI 綠），且我得體地回應了 review
- [ ] 我體會到「開源是做會的，不是讀會的」——並準備好做更大的貢獻（Final）

無論你的第一個 PR 被合併、還在 review、還是被拒——你都跨過了最難的一步：從旁觀者變成參與者。Part 5 轉向**團隊協作**：當你不只是外部貢獻者，而是團隊的一員，要會的分支策略、保護規則、CODEOWNERS、規範自動化。

→ [Ch 22 branching model](./22-branching-models.md)
