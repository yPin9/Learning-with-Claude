# Ch 2 — commit 是溝通

> **目標**：扭轉「commit 只是存檔」的單機思維。在協作中，commit 是寫給**別人**（含未來的你）讀的溝通。學會寫好的 commit message、做 atomic（單一目的）commit、理解為什麼乾淨的歷史在協作中價值連城。

> **環境**：git 2.40+。範例語言無關。

## 為什麼 commit 在協作中完全不同

單機時，commit message 寫 "fix"、"update"、"asdf" 都沒差——反正只有你看，而且你也不會回頭看。

協作時，commit 是**團隊的共享記錄**：
- review 你 PR 的人，靠 commit message 理解「你為什麼這樣改」。
- 三個月後有人用 `git blame` 查「這行為什麼這樣寫」，看到的是你的 commit message。
- 出 bug 用 `git bisect`（Ch 35）找出哪個 commit 引入問題，靠的是每個 commit 都是清楚、獨立的單位。
- release notes、changelog 常常直接從 commit message 生成。

一句話：**commit message 是你寫給未來的人的信，而那個人很可能就是六個月後忘記一切的你自己。**

## 先建立直覺：考古學家的視角

想像你是個程式碼考古學家，接手一個陌生專案。你看到某行可疑的 code，`git blame` 它，跳出引入它的 commit。你最想看到的是：

```
壞的：
  a3f2b1  fix              ← fix 什麼？為什麼？看不出來
  
好的：
  a3f2b1  Fix race condition in cache eviction
  
          The eviction thread could read `entry->next` after another
          thread freed it. Guard the traversal with the bucket lock.
          
          Fixes #1234
```

好的 commit 回答了考古學家的三個問題：**改了什麼（what）、為什麼改（why）、怎麼驗證/相關脈絡（context）**。其中 **why 最重要**——程式碼本身已經說明了 what（diff 看得到），但 diff **永遠看不出 why**。「為什麼這樣改」只能靠 commit message 留下來。

## 一個好的 commit message 的結構

業界有個廣泛接受的格式（源自 git 社群與 Tim Pope 的經典文章）：

```
<簡短摘要，一行，祈使句，≤50 字元>
<空一行>
<詳細說明：為什麼要這個改動、解決什麼問題、有什麼取捨。>
<每行 ≤72 字元。可以多段。>
<空一行>
<頁尾：關聯的 issue、Co-authored-by、Signed-off-by 等>
```

實例：

```
Add retry logic to the API client

The upstream service occasionally returns 503 during deploys,
causing our nightly job to fail spuriously. Retry up to 3 times
with exponential backoff (1s, 2s, 4s) on 5xx responses.

We deliberately don't retry on 4xx — those are our bugs, not
transient failures, and retrying would mask them.

Closes #482
```

逐項拆解：

1. **摘要行（subject）**：一行講清楚這個 commit 做什麼。≤50 字元（讓 `git log --oneline` 不被截斷）。
2. **祈使句**："Add retry logic" 不是 "Added" 或 "Adds"。為什麼？因為它補完「如果套用這個 commit，它將會 _____」——"Add retry logic"。git 自己的訊息（"Merge branch...", "Revert..."）也是祈使句，保持一致。
3. **空一行**：subject 和 body 之間必須空行，否則 git 工具會把整段當 subject。
4. **body 講 why**：不是重複 diff（what），而是說明動機、脈絡、取捨。上例的「為什麼不 retry 4xx」就是 diff 永遠看不出的關鍵決策。
5. **頁尾關聯 issue**：`Closes #482` / `Fixes #482` 會讓 GitHub 在 PR merge 時**自動關閉**該 issue（Ch 12）。

> 為什麼摘要要短：`git log --oneline`、GitHub 的 commit 列表、`git shortlog`、blame 摘要——這些地方都只顯示 subject。超過 50–72 字元會被截斷或難讀。短，是為了在這些場合可用。

## atomic commit：一個 commit 做一件事

協作中第二個關鍵：**每個 commit 應該是一個完整、獨立、單一目的的改動。**

壞的（一個 commit 塞了三件事）：

```
Update stuff

- fix login bug
- rename some variables
- add new export feature
- oh and fix a typo in README
```

好的（拆成四個 atomic commit）：

```
Fix null deref when login token is expired
Rename `usr` to `user` for clarity across auth module
Add CSV export to the reports page
Fix typo in README installation steps
```

為什麼 atomic 在協作中重要：

- **review 容易**：審查者能一個 commit 一個 commit 看，每個都是聚焦的小改動。塞滿東西的 commit 沒人審得動。
- **revert 精準**：export feature 出問題？單獨 revert 那個 commit，不會連帶撤銷 login 修復。混在一起就只能全砍。
- **bisect 有意義**：`git bisect`（Ch 35）二分搜尋找出引入 bug 的 commit——前提是每個 commit 都能獨立 build/通過測試。混雜的 commit 讓 bisect 指向一坨東西，沒幫助。
- **cherry-pick 可行**：想把「export feature」單獨挑到另一個分支（Ch 9）？atomic 才挑得乾淨。

判準：**一個 commit 能不能用一句不含「and」的話描述？** 需要「and」就該拆。

## 草稿可以亂，最終要乾淨

新手常誤會：「那我開發時每一步都要想清楚、寫完美 commit？」不是。

開發過程本來就是混亂的——試錯、改了又改、commit 一堆 "wip"、"oops"、"fix typo in previous"。**這完全沒問題**，因為協作有個關鍵工具：**在 push / 發 PR 前，把混亂的歷史整理乾淨。**

```
   開發中（本機，隨便亂 commit）        整理後（要給人看的）
   wip                                  Add user authentication
   fix                          ─────►  Add password reset flow
   actually fix the test                Update auth docs
   typo
   wip add reset
   forgot import
```

整理的工具是 **interactive rebase**（Ch 7）——squash 合併、reword 改訊息、reorder 重排、drop 刪除。練習 A 就是練這個。

所以心法是：**開發時別被「完美 commit」綁手綁腳，盡情亂 commit;但在送出去給別人看之前，花五分鐘把歷史 rebase 成乾淨、atomic、訊息清楚的樣子。** 這是專業協作者和新手最明顯的差別之一。

> 認識論誠實：有些社群/團隊**不**要求整理歷史（主張「真實的開發過程也有價值」「squash merge 時反正會壓成一個」）。trunk-based 的團隊甚至偏好小而頻繁的 commit 直接進 main。所以「要不要整理」依專案文化而定——但「能寫清楚的 commit message」是普世技能。讀專案的 CONTRIBUTING（Ch 16）看它的偏好。

## commit message 的協作慣例

除了基本結構，協作中常見的附加慣例：

```
# 關聯 issue（GitHub 會自動連結，merge 時自動關閉）
Closes #123
Fixes #456
Refs #789          # 只關聯不關閉

# 共同作者（pair programming / 採納別人建議）
Co-authored-by: Name <email@example.com>

# DCO 簽署（某些專案要求，Ch 21）
Signed-off-by: Your Name <you@example.com>     # git commit -s 自動加
```

有些專案用 **Conventional Commits** 格式（`feat:`, `fix:`, `docs:` 前綴），能自動生成 changelog——Ch 27 專講，這裡先知道有這回事。

## 踩雷集錦

1. **commit message 寫 "fix"/"update"/"wip" 就 push**：協作中這是失職。別人看不懂、blame 沒用、review 困難。整理後再送（Ch 7）。
2. **body 重複 diff（講 what 不講 why）**："Change x to y" 沒意義——diff 看得到。要寫「為什麼從 x 改成 y」。
3. **一個 commit 塞十件事**：review 災難、revert 連坐、bisect 無用。atomic 拆開。
4. **subject 用過去式 / 太長**："Added a really long description of what this commit does and..." → 改祈使句、≤50 字元。
5. **subject 和 body 沒空行**：git 會把它們黏成一段，工具顯示錯亂。中間必須空一行。
6. **以為「整理歷史」是作弊**：不是。草稿亂、成品乾淨，是專業流程。但別 rebase 已公開的歷史（Ch 6 的 golden rule）。
7. **誤把 `Co-authored-by` 拼錯**：GitHub 對格式很挑（`Co-authored-by: Name <email>`），拼錯就不會顯示共同作者。

## 進階：再往深一層

- **commit message 模板**：`git config commit.template ~/.gitmessage` 設一個模板，每次 commit 自動帶出提示（提醒你寫 why、關聯 issue）。
- **`git commit --fixup` / `--squash`**：開發中發現某個舊 commit 要補東西，`git commit --fixup=<sha>` 做一個標記 commit，之後 `git rebase -i --autosquash` 自動把它 squash 回去（Ch 7）。
- **commit 的兩個身分**：每個 commit 有 author（原作者）和 committer（套用者）——rebase/cherry-pick 後 committer 會變但 author 不變。`git log --format=...` 看得到。理解這個，你才懂為什麼 rebase 過的 commit 時間/committer 變了。
- **trailer 解析**：`git interpret-trailers` 能程式化讀寫頁尾的 `Closes:`/`Signed-off-by:` 等，CI/自動化常用。
- **commit message 的 i18n**：開源專案的 commit 幾乎一律用英文（國際協作）。即使專案是中文社群，commit 多半英文——降低跨語言貢獻門檻。

## 動手練習

1. 找你自己一個舊 repo，`git log --oneline` 看你的 commit message——有多少是 "fix"/"update"？感受未來的你看到會多痛苦。
2. 寫一個含 subject + body 的完整 commit message，body 必須回答「為什麼」而非「改了什麼」。
3. 拿一個你最近「塞了好幾件事」的改動，想想該怎麼拆成 atomic commit（每個能用無 "and" 的一句話描述）。
4. 設一個 `~/.gitmessage` 模板（含 "Why:" 提示），`git config commit.template` 套用，做一次 commit 體驗。
5. 做一個 commit 用 `Closes #1`（在一個有 issue 的測試 repo），merge 後看 issue 是否自動關閉。

## 本章重點整理

- 協作中 commit 是溝通：寫給 reviewer、未來的 blame 考古者、bisect、changelog——以及六個月後的你。
- 好的 message：祈使句摘要（≤50 字元）+ 空行 + body 講 **why**（diff 看不出的動機/取捨）+ 頁尾關聯 issue。
- atomic commit：一個 commit 做一件事（能用無 "and" 的一句話描述）——好 review、好 revert、好 bisect、好 cherry-pick。
- 開發時可以亂 commit，push/PR 前用 interactive rebase（Ch 7）整理乾淨——但別 rebase 已公開歷史。
- 慣例：`Closes #N`（自動關 issue）、`Co-authored-by`、`Signed-off-by`（DCO）。

## 自我檢核

- [ ] 為什麼說「diff 看得出 what，但只有 commit message 能說明 why」？
- [ ] 好的 commit subject 有哪些特徵（時態、長度、內容）？
- [ ] atomic commit 的判準是什麼？它讓 review/revert/bisect 各受什麼益？
- [ ] 「開發時亂 commit、送出前整理」這個流程合理嗎？什麼情況不適用？
- [ ] `Closes #123` 在 commit/PR 裡會觸發什麼自動行為？

## 延伸閱讀

### 部落格 / 文章

- **[How to Write a Git Commit Message](https://cbea.ms/git-commit/)** — Chris Beams
  - **這篇說什麼**：commit message 的七條黃金規則（祈使句、50/72、講 why…）。
  - **讀哪裡**：整篇，是這個主題最被引用的文章。
  - **為什麼值得讀**：本章 message 結構的原始出處，寫得簡潔有力。

- **[A Note About Git Commit Messages](https://tbaggery.com/2008/04/19/a-note-about-git-commit-messages.html)** — Tim Pope
  - **這篇說什麼**：50/72 規則與祈使句慣例的經典提案。
  - **為什麼值得讀**：這些慣例的歷史源頭，理解「為什麼是這些數字」。

- **[Write Better Commits, Build Better Projects](https://github.blog/2022-06-30-write-better-commits-build-better-projects/)** — GitHub Blog
  - **這篇說什麼**：atomic commit 與好歷史對專案的長期價值。
  - **讀哪裡**：atomic commit 與 reviewable history 段落。

### 書籍

- **[Pro Git, Ch 5.2 — Contributing to a Project (Commit Guidelines)](https://git-scm.com/book/en/v2/Distributed-Git-Contributing-to-a-Project)**
  - **讀哪幾章**：5.2 的 "Commit Guidelines" 小節。
  - **和本章的關聯**：官方的 commit 與貢獻準則，含 atomic 與 message 規範。

commit 寫好了，下一章談協作的下一個基本單位：branch——為什麼要開、本地和遠端的關係。

→ [Ch 3 branch 是協作的單位](./03-branches-as-units.md)
