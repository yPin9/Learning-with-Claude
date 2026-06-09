# Final Project — 完整開源貢獻循環

> **目標**：整門課的畢業作——對一個**真實的開源專案**做出一個**有意義（非 typo 級）的貢獻**，全程記錄。這不是模擬：真實的專案、真實的維護者、真實的 review、真實的 merge（或被拒的學習）。完成後你能誠實地說：「我會跟全世界一起寫程式。」

> 前置：整門課（Part 1-7）。練習 D（第一個真 PR）是這個的暖身——那是 typo 級走通流程，這是更深、更完整的貢獻。

## 為什麼是這個 Final Project

練習 D 讓你發了第一個真 PR（typo/文件級，走通流程、破除心理障礙）。Final Project 更進一步——一個**有實質內容**的貢獻：修一個真正的 bug、加一個被需要的小功能、改善一個真實的問題。

為什麼這是畢業作：

- **它整合整門課**：找專案（Ch 16）、功課（Ch 17）、中階 git（Part 2）、好 PR（Ch 11）、CI（Ch 14）、review 迭代（Ch 13/19）、溝通（Ch 20）、法律（Ch 21）——全部用上。
- **它是真實的**：開源學不會、只能做會（Ch 20）。一個真實、有意義的貢獻，是你「會協作」的最終證明。
- **它有重量**：一個 merged 進真實專案的實質貢獻，是真實的作品、真實的履歷、真實的成就（Ch 37）。

**重要**：目標是「**完整走通一次有意義的貢獻**」，重點在過程的完整與品質，不在貢獻多大。一個被合併的小 bug 修復（含測試、好溝通），就是滿分。

## 任務規格

### 你要做的事

對一個真實開源專案，完成一次有實質內容的貢獻，全程運用整門課的技能，並記錄整個過程。

### 貢獻的「有意義」標準（比練習 D 深）

```
   練習 D（暖身）：typo / 文件修正 / 壞連結（走通流程）
   
   Final（畢業）：實質內容，至少其一：
   - 修一個真正的 bug（複現 → 修 → 加測試）
   - 加一個被需要的小功能（先討論方向 → 實作 → 測試）
   - 顯著改善（補一塊缺失的測試、改善錯誤處理、效能小優化）
   - 實質的文件貢獻（補一整塊缺失的指南、API 文件，不只 typo）
```

### 完整流程（驗收標準）

**找與評估（Ch 16）**
- [ ] 選一個你在用/在乎、健康（活躍、有回應）的真實專案
- [ ] 找到一個有意義的貢獻點（你能複現的 bug、被需要的功能、缺的測試/文件）

**功課（Ch 17）**
- [ ] 讀 CONTRIBUTING，遵守它的規矩
- [ ] （bug）複現問題；（功能）先開 issue 討論方向
- [ ] 認領 issue（若需要）、設好開發環境、跑通測試
- [ ] 處理 CLA/DCO（若需要，Ch 21）

**實作（Part 2 + Ch 18）**
- [ ] 從最新 main 開 branch（Ch 3/25）
- [ ] 做改動，scope 受控（Ch 18）
- [ ] **加/更新測試**（這是「有意義」的關鍵——test-first 尤佳，Ch 17）
- [ ] commit 乾淨、message 好（Ch 2）、整理歷史（Ch 7）

**PR 與 review（Ch 11/13/14/19/20）**
- [ ] 開一個好的 PR（標題/描述/關聯 issue，Ch 11）
- [ ] CI 綠（本地先跑，Ch 14）
- [ ] 得體地在 review 中迭代（Ch 13/19）、溝通禮儀（Ch 20）

**收束**
- [ ] PR 被合併（理想）/ 或從被拒中學到東西
- [ ] 寫一份完整的「貢獻報告」（見下）

### 貢獻報告（畢業作的一部分）

記錄整個過程，證明你走通了完整循環：

```markdown
# 我的開源貢獻報告

## 專案
- 哪個專案、為什麼選它（健康度評估、為什麼我在乎）

## 貢獻
- 解決什麼問題 / 加什麼 / 連結到 issue 和 PR

## 過程記錄
- 功課：怎麼複現/討論方向、讀了什麼、CLA/DCO
- 實作：怎麼找到改的地方、加了什麼測試、git 操作（rebase/衝突？）
- PR：怎麼包裝、CI、review 來回幾輪、怎麼回應
- 結果：合併了？多久？被拒？學到什麼

## 我用上了課程的哪些技能
（對照整門課，列出實際用到的）

## 我做對的 / 踩的雷 / 學到的
（誠實反思）
```

## 如果你卡住了

1. **找不到「有意義」的貢獻點？** 從你在用的工具的 good first issue / help wanted 找（Ch 16）；或你工作/side project 中用某套件遇到的真實 bug（最有動機、最容易成功）。
2. **怕做不到「有意義」級？** 補一塊缺失的測試是完美的中間難度——不用懂太多 codebase、價值明確、維護者歡迎。
3. **是功能不確定要不要做？** 先開 issue 討論（Ch 17/18），別悶頭寫。維護者說要再做。
4. **複現不出 bug / codebase 太難？** 換一個。專案/issue 難度差很多，挑你能掌握的（Ch 16 健康度 + 難度）。
5. **CLA 擋住 / 不想簽？** 看清楚（Ch 21），或換不要 CLA 的專案。
6. **PR 發了很久沒回？** 耐心（Ch 20），禮貌 ping，同時這個 Final 的「過程」已經完成了——合併與否不是唯一的成功標準（被拒/沒回也能寫報告、也學到東西）。

## 實作步驟建議

### Step 1：選專案 + 找貢獻點（Ch 16）

選一個你在用、健康的專案，找一個有意義、你能掌握的貢獻點（bug/測試/小功能/實質文件）。

### Step 2：功課（Ch 17/21）

讀 CONTRIBUTING、複現（bug）/討論（功能）、認領、設環境、跑測試、CLA/DCO。

### Step 3：實作 + 測試（Part 2 + Ch 18）

從最新 main 開 branch、做改動（scope 控制）、**加測試**、commit 乾淨、整理歷史。

### Step 4：PR + CI（Ch 11/14）

開好的 PR、本地先跑 CI、確保綠、好的描述關聯 issue。

### Step 5：review 迭代（Ch 13/19/20）

得體回應 review、改+回應+re-request、保持禮貌耐心。

### Step 6：收束 + 報告

合併/學習、寫完整貢獻報告。

## 完整參考流程

**這沒有單一答案（每個人的貢獻不同），這是一個完整的範例。**

<details>
<summary>點開一個完整的「有意義貢獻」範例</summary>

假設你常用某個開源 CLI 工具，發現它在處理「空輸入檔」時崩潰（而非給友善的錯誤訊息）。這是完美的 Final 貢獻——真實 bug、能複現、修起來範圍可控、價值明確。

### Step 1-2：選定 + 功課

```bash
# 選定：你在用的 mytool，健康（最近有 commit、PR 有在合）
# 貢獻點：mytool 處理空檔案時 crash（你真的遇到過）

# 搜尋有沒有人回報過（Ch 12）
gh issue list --repo owner/mytool --search "empty file crash"
#   沒有 → 先開一個 issue 回報（Ch 12）
gh issue create --repo owner/mytool --title "Crash on empty input file" \
  --body "### Steps to reproduce\n1. echo -n > empty.txt\n2. mytool process empty.txt\n### Expected\nFriendly error\n### Actual\nIndexError: list index out of range\n..."
#   假設成為 issue #501

# 讀 CONTRIBUTING（Ch 16）：假設要求 DCO + 加測試 + Conventional Commits
# fork + clone + 環境（Ch 10/17）
gh repo fork owner/mytool --clone && cd mytool
git fetch upstream

# 複現（Ch 17）
echo -n > /tmp/empty.txt
python -m mytool process /tmp/empty.txt    # 確認 crash，看到 IndexError

# 認領
gh issue comment 501 --repo owner/mytool --body "I'd like to fix this. Could you assign it to me?"
```

### Step 3：實作 + 測試（test-first，Ch 17）

```bash
git switch -c fix/empty-input-crash upstream/main

# test-first：先寫一個會抓到 bug 的測試（現在會失敗）
cat >> tests/test_process.py <<'EOF'

def test_process_empty_file(tmp_path):
    empty = tmp_path / "empty.txt"
    empty.write_text("")
    # 應該給友善錯誤，而非 crash
    result = process(str(empty))
    assert result.error == "input file is empty"
EOF
python -m pytest tests/test_process.py::test_process_empty_file   # 失敗（bug 還在）

# 讀 codebase 找到該改的地方（Ch 17）
grep -rn "list index" src/    # 或 git blame 相關處
# 找到 src/process.py 的 process()，沒檢查空輸入

# 修
# （在 process() 開頭加空檔案檢查，回傳友善錯誤）

python -m pytest tests/    # 全綠（修好 + 測試過）

# commit（DCO + Conventional Commits + 好 message，Ch 2/21/27）
git commit -s -m "fix: handle empty input file gracefully

process() raised IndexError on empty files instead of returning a
clear error. Add an empty-input check that returns a friendly
'input file is empty' error.

Closes #501"

# 整理歷史（若有多個 wip commit，Ch 7）
git rebase -i upstream/main    # 整理成乾淨的 commit

git push -u origin fix/empty-input-crash
```

### Step 4：PR + CI（Ch 11/14）

```bash
# 本地先跑 CI 會跑的（Ch 14）
python -m pytest && flake8 src tests    # 綠

gh pr create --repo owner/mytool --base main \
  --title "fix: handle empty input file gracefully" \
  --body "$(cat <<'EOF'
## What
Make `process()` return a clear error for empty input files instead
of crashing.

## Why
Passing an empty file currently crashes with `IndexError: list index
out of range` (see #501). Users may legitimately pass empty files;
they should get a clear error, not a stack trace.

## How
Added an empty-input check at the start of `process()`. Returns a
`Result` with `error="input file is empty"`.

## Testing
Added `test_process_empty_file`. All existing tests pass; CI green.

Closes #501
EOF
)"

gh pr checks --watch    # CI 綠
```

### Step 5：review 迭代（Ch 13/19/20）

```
維護者 review（request changes）：
  "Thanks! Two things:
   1. Should this also handle whitespace-only files?
   2. Please use the existing `EmptyInputError` instead of a string error."

你的回應（Ch 13/19）：
  - 改 code（用 EmptyInputError、加 whitespace-only 處理 + 測試）
  - push（PR 自動更新）
  - 回應每條：
    "Good points! 
     1. Done — now also handles whitespace-only files (added a test).
     2. Switched to EmptyInputError. Thanks for pointing to it."
  - re-request review

維護者 approve + squash merge。issue #501 自動關閉。
```

### Step 6：收束 + 報告

```bash
git switch main && gh repo sync    # 同步 fork（Ch 25）
git branch -d fix/empty-input-crash
```

```markdown
# 我的開源貢獻報告

## 專案
mytool（我每天用的 CLI）。健康度：每週有 commit、PR 平均 3 天內 review、
維護者語氣友善、有 good first issue——值得投入。

## 貢獻
修了「空輸入檔導致 crash」的 bug（issue #501 / PR #xxx）。讓它回傳
友善錯誤而非 IndexError stack trace。

## 過程記錄
- 功課：自己遇到這 bug、能複現、搜尋確認沒人回報過、先開 issue、認領、
  讀 CONTRIBUTING（DCO + 測試 + Conventional Commits）
- 實作：test-first（先寫失敗的測試）、grep + 讀 codebase 找到 process()、
  加空輸入檢查、git commit -s（DCO）、rebase -i 整理成一個乾淨 commit
- PR：好描述（What/Why/How/Testing）、本地先跑 CI、一次綠
- review：2 輪（維護者要求用既有的 EmptyInputError + 處理 whitespace-only），
  我改+回應每條+re-request，得體溝通
- 結果：4 天後 squash merged，issue 自動關閉

## 用上的課程技能
找專案/健康度(Ch16)、複現/功課/認領(Ch17)、DCO(Ch21)、branch/從最新main開(Ch3/25)、
test-first(Ch17)、好commit(Ch2)、rebase -i整理(Ch7)、好PR(Ch11)、本地跑CI(Ch14)、
review迭代(Ch13/19)、溝通禮儀(Ch20)、squash merge(Ch10)、同步fork(Ch25)

## 做對的 / 踩雷 / 學到的
做對：從自己遇到的真實 bug 出發（有動機、能複現、描述有真實場景）；
     test-first 讓維護者一眼信服我真的修好了
踩雷：第一次忘了 git commit -s（DCO），bot 擋住，重新 commit 加 sign-off
學到：維護者的 review 讓我發現專案已有 EmptyInputError（我自創了字串錯誤）——
     讀周邊 code 不夠仔細；以及真實 review 的來回比想像的友善
```

**流程說明**：

這個範例展現了 Final Project 的精髓——**一個有意義的貢獻，完整運用整門課**：

- **從真實需求出發**（Ch 16）：自己遇到的 bug，有動機、能複現、描述有說服力。
- **test-first**（Ch 17）：先寫失敗的測試，這是「有意義貢獻」的關鍵——證明你真的修好了、且不會回歸。
- **完整的功課**（Ch 17/21）：複現、搜尋、開 issue、認領、讀 CONTRIBUTING、DCO。
- **乾淨的 git**（Part 2）：從最新 main 開、test-first、好 commit、rebase 整理。
- **好包裝 + 得體迭代**（Ch 11/13/19/20）：好 PR、本地先跑 CI、2 輪 review 得體回應。
- **誠實反思**：記錄做對的、踩的雷、學到的——這是學習的鞏固。

你的貢獻會不一樣，但這個「完整、有品質、有意義」的標準相同。

</details>

## 評估你的成果

這是畢業作，用這些標準自評：

1. **它有意義嗎？** 不只 typo——修了真 bug / 加了被需要的東西 / 補了缺的測試，且**有測試**佐證。
2. **流程完整嗎？** 從找專案到收束，整門課的環節都走過（對照貢獻報告的「用上的技能」）。
3. **品質夠嗎？** 好的 commit/PR、CI 綠、得體的 review 回應——讓維護者願意合。
4. **溝通得體嗎？** 整個過程禮貌、清楚、尊重維護者（Ch 20）。
5. **你誠實反思了嗎？** 寫下做對的、踩的雷、學到的——成長來自反思。

合併與否不是唯一標準——**完整走通一次有品質、有意義的貢獻**才是。被拒/沒回也能是滿分的 Final（如果過程完整、學到東西、反思誠實）。

## 延伸挑戰（畢業之後）

1. **持續貢獻**（Ch 37）：在同一專案做第二、第三個貢獻——體驗複利，往常駐貢獻者爬。
2. **承擔維護者的工作**（Ch 37）：在你貢獻的專案幫忙 review 一個別人的 PR、triage 一個 issue。
3. **貢獻你工作依賴的開源**：把你工作/side project 中遇到並 workaround 的 bug，真正修復貢獻回上游（最有價值）。
4. **經營自己的專案**（練習 F / Part 6）：把你的 side project 武裝成健康開源專案，招募第一個貢獻者。
5. **更難的貢獻**：挑戰一個需要多輪 review、較深 codebase 的貢獻——練 Ch 19 的迭代耐力。
6. **寫下來分享**（Ch 37）：把你的第一次有意義貢獻寫成 blog——鞏固學習 + 建立可見度。

## 自我檢核（畢業檢核）

- [ ] 我對一個真實專案做出了一個**有意義**（非 typo）的貢獻，且有測試佐證
- [ ] 我完整走過了整門課的環節（找專案 → 功課 → 中階 git → 好 PR → CI → review 迭代 → 收束）
- [ ] 我的貢獻品質夠（好 commit/PR、CI 綠、得體溝通），讓維護者願意認真對待
- [ ] 我得體地與真實維護者協作（禮貌、清楚、尊重、耐心）
- [ ] 我誠實反思了做對的、踩的雷、學到的
- [ ] 我能誠實地說：**「我會跟全世界一起寫程式。」**

---

## 結語：你走過的路

從第一句「我會 commit/push/pull，但對協作沒頭緒」，到這裡完成一個真實、有意義的開源貢獻——你走過了：

- **會用 git** → **會用 git 跟人協作**（中階 git、衝突、reflog）
- **一個人寫** → **在團隊裡協作**（branch 策略、保護、CODEOWNERS、規範）
- **使用開源** → **貢獻開源**（找專案、發 PR、review 迭代、溝通禮儀、法律）
- **貢獻者** → **也懂維護者**（審 PR、triage、社群、release、安全）
- **單次貢獻** → **長期參與**（聲譽、生涯、複利）

協作的本質，一半是技術（git/GitHub），一半是人（溝通、信任、社群）。你都學了。

但記住整門課最重要的一課：**開源是做會的，不是讀會的。** 這門課是地圖，真正的能力來自你去發每一個 PR、解每一個衝突、跟每一個維護者協作。去貢獻吧——跟全世界一起寫程式。

← 回到 [課程首頁](./README.md)
