# Ch 13 — Code Review（被審方）

> **目標**：學會當「被審的一方」——怎麼讀懂 review 意見、push 更新讓 PR 自動刷新、回應 comment、resolve conversation、處理「changes requested」、以及 force-push 在 PR 裡的時機。code review 是協作的核心儀式，被審得體是你 PR 被合併的關鍵。

> **環境**：GitHub、git。前置：[Ch 11 好的 PR](./11-good-pull-request.md)、[Ch 6 rebase / force-push](./06-rebase.md)。

## 為什麼「被 review」是一門技術

你開了 PR，reviewer 留下一堆意見。接下來怎麼處理，決定你的 PR 是順利合併、還是卡在無止境的來回、還是惹毛 reviewer。

新手常犯的錯：把 review 意見當人身攻擊（玻璃心）、改了東西卻不回應（reviewer 不知道你處理了沒）、為小事爭論不休、或默默 force-push 把 reviewer 看到的東西洗掉。這些都讓 review 變痛苦、PR 拖很久。

這章教你當一個「好被審」的作者——這跟寫 code 一樣是協作技能。Ch 29 會教反過來「怎麼審別人」。

## 先建立直覺：review 是協作不是審判

心態最重要：**code review 不是 reviewer 在挑你毛病、證明你不行——是兩個人一起把 code 變好。**

```
   錯的心態：              對的心態：
   "他在嫌我的 code"        "他幫我抓到我沒看到的問題"
   "為什麼這麼龜毛"         "這個 review 讓 code 更好"
   "我要證明我是對的"       "目標是合併好的 code，不是我贏"
```

reviewer 花時間看你的 code 是在**幫你**（尤其開源維護者，無償花時間）。即使意見你不同意，對方也是出於善意想讓專案更好。帶著這個心態，review 就從「對抗」變「合作」。這不是雞湯——心態直接影響你怎麼回應，而回應方式直接影響 PR 命運。

## review 的形式：comment / approve / request changes

reviewer 在 GitHub 上 review 時，會做三種事之一：

```
   ┌─────────────────────────────────────────────┐
   │ Comment       純留意見，不表態批准與否        │
   │ Approve       ✅ 我認可，可以合併             │
   │ Request changes  🔴 有問題，改了再合併        │
   └─────────────────────────────────────────────┘
```

外加**行內 comment（inline comment）**——reviewer 點在某一行 code 上留言，精準指出「這裡有問題」。一次 review 通常是「一堆 inline comment + 一個總結 + 一個表態（approve/request changes/comment）」。

你會看到的狀態：
- **Changes requested**：必須處理才能 merge（多數專案設了「request changes 會擋 merge」）。
- **Approved**：reviewer 認可了。達到專案要求的 approve 數（Ch 23）就能 merge。
- **Comment only**：給了意見但沒擋——你可斟酌處理。

## 回應 review 的標準流程

收到 review 後：

```
   1. 讀完所有 comment（先全看一遍，別逐條反射回應）
   2. 對每條 comment 決定：接受改 / 討論 / 解釋為什麼不改
   3. 改 code → commit → push（PR 自動更新）
   4. 回應每條 comment（"Done"、或解釋、或提問）
   5. 必要時 re-request review（請 reviewer 再看）
```

### 改 code：push 就自動更新 PR

承 Ch 10：PR 追蹤你的 branch。你改完 commit、push 到同一條 branch，**PR 自動更新**——不用重開：

```bash
# 在你的 PR branch 上
# ...改 reviewer 指出的問題...
git commit -m "Address review: handle empty input case"
git push                          # PR 自動刷新，reviewer 收到通知
```

reviewer 能看到「新的 commit」，甚至只看「上次 review 後的增量改動」（GitHub 的 "changes since last review"）。

### 回應每條 comment（關鍵！）

**改了東西一定要回應對應的 comment**——否則 reviewer 不知道你處理了沒，要自己一條條比對（很煩）。回應方式：

```
   reviewer 的 inline comment: "This could throw if list is empty"

   你的回應（在那條 comment 下回覆）：
   - "Done, added an empty check in a1b2c3"   ← 改了，指出 commit
   - "Good catch! Fixed."
   - "Actually this can't be empty here because <reason>. But I added
      an assert to make it explicit."          ← 不改但解釋
   - "Hmm, I'm not sure I follow — do you mean X or Y?"  ← 不懂就問
```

每條 comment 都該有個著落：改了（說 done）、不改（說為什麼）、或討論（提問）。**沉默 = reviewer 困惑**。

### resolve conversation

GitHub 的 inline comment 是一個個「conversation」。處理完一條，可以按 **"Resolve conversation"** 收合它——表示「這條處理完了」。

> 慣例：**通常由 reviewer（或你，看專案文化）resolve**。有些專案希望作者改完按 resolve（表示「我處理了」），有些希望 reviewer 確認後才 resolve（表示「我同意你的處理」）。看專案習慣。安全做法：你改完回覆 "Done"，讓 reviewer 決定 resolve——避免「你自己 resolve 但 reviewer 還不滿意」的尷尬。

## changes requested 怎麼處理

被 request changes 不是壞事——它是正常的 review 結果。流程：

```bash
# 1. 逐條處理 comment（改的改、解釋的解釋）
# 2. push 更新
# 3. 回覆每條 comment
# 4. 請 reviewer 重審：
gh pr review --comment      # 或在 GitHub 按 "Re-request review"
```

re-request review 很重要——reviewer 不會一直盯著你的 PR，你改完要主動請他再看（GitHub 上 reviewer 名字旁有個重審按鈕）。

## force-push 在 PR 裡的時機（小心！）

承 Ch 6/7：整理 PR 歷史（squash、rebase 跟上 main）需要 force-push。但在**review 進行中** force-push 有個問題：

```
   reviewer 審了你的 commit A, B, C，留了 inline comment
        │ 你 rebase + force-push，A B C 變成 A' B' C'（新 hash）
        ▼
   reviewer 的 inline comment 可能「outdated」（指向的舊 commit 不見了）
   "changes since last review" 也亂了（git 不認得新舊對應）
```

所以原則：

- **review 進行中**：盡量用**新 commit 疊上去**（"Address review: ..."），別 rebase/force-push——讓 reviewer 能看清楚「你針對 review 改了什麼」。
- **review 結束、要 merge 前**：這時可以 rebase/squash 整理歷史（如果專案要求乾淨歷史）——reviewer 已經審完了。
- **rebase 跟上 main**：必要時做（解衝突），但盡量集中、且告知 reviewer。

```bash
# review 中：加 commit（推薦）
git commit -m "Address review feedback"
git push

# review 完、merge 前：整理（若需要）
git rebase -i main
git push --force-with-lease       # 永遠用 --force-with-lease（Ch 6）
```

> 認識論誠實：這個「review 中別 force-push」是常見建議，但**依專案文化而異**。有些專案（尤其用 squash merge 的，Ch 10）不在乎你 PR 的中間歷史（反正會壓成一個），force-push 隨意；有些重視乾淨歷史的專案則要求你每次都 rebase。讀 CONTRIBUTING 或問維護者。安全預設：review 中加 commit、merge 前才整理。

## 處理分歧：不是每條都要照做

reviewer 不是永遠對的。你可以**有禮貌地不同意**——但要有理有據：

```
   reviewer: "Use a for loop instead of map here"
   
   差的回應："不要，我喜歡 map"  ← 沒理由的拒絕
   
   好的回應："I'd prefer to keep map here because it's a pure
            transformation and reads more declaratively. But if
            the team's convention is loops, I'll switch — let me
            know."  ← 有理由 + 尊重對方 + 願意妥協
```

原則：
- **技術分歧用技術理由討論**，不是「我喜歡/我習慣」。
- **小事讓步**（風格偏好），把精力留給真正重要的（正確性、設計）。
- **僵住時尊重維護者**——這是他的專案，他有最終決定權（Ch 20）。為一個小風格爭到撕破臉不值得。
- **不確定就問**，別假設 reviewer 的意思。

## 一個完整的 review 回應實戰

```
1. reviewer request changes，留了 5 條 inline comment
2. 你全部讀完，分類：
   - 3 條同意 → 改
   - 1 條是誤會 → 解釋（附 code 說明為什麼現在這樣是對的）
   - 1 條不確定 → 提問澄清
3. git commit -m "Address review: validate input, fix off-by-one"
   git push                     # PR 自動更新
4. 逐條回覆：
   - 3 條改的："Done in <commit>"
   - 1 條誤會："This is actually safe because... (see line X)"
   - 1 條提問："Did you mean A or B? Happy to do either."
5. Re-request review
6. reviewer 回來，approve（或再一輪）
7. 達到 approve 要求 → merge
```

整個過程禮貌、清楚、每條有著落——reviewer 樂意繼續、PR 順利推進。

## 踩雷集錦

1. **把 review 當人身攻擊（玻璃心）**：reviewer 在幫你。心態錯了回應就會帶刺，PR 變對抗。
2. **改了 code 不回應 comment**：reviewer 不知道你處理了沒，要自己比對。每條都回（done/解釋/提問）。
3. **review 中 rebase/force-push 把意見洗掉**：reviewer 的 inline comment 變 outdated、增量 diff 亂掉。review 中加 commit，merge 前才整理（看專案文化）。
4. **為小風格爭到底**：把精力浪費在無關緊要的偏好上。小事讓步，留力氣給重要的。
5. **無理由拒絕意見**："我不要" 沒用。不同意要給技術理由、保持尊重。
6. **改完不 re-request review**：reviewer 不會自己回來。主動請他重審。
7. **一次只改一條就 push 一次**：改完一批再 push（別把 reviewer 的通知洗版）。但也別累積太多才一次回——節奏拿捏。

## 進階：再往深一層

- **GitHub 的 "Add suggestion"**：reviewer 能直接在 comment 裡寫好建議的 code，你按一個按鈕就接受（變成一個 commit）——小修改超快。
- **batch 回應**：GitHub 的 review 可以「start a review」批次留言、一次送出，而非一條條即時送（避免通知轟炸）。你回應時也可考慮節奏。
- **"changes since last review"**：reviewer 能只看「上次 review 後的增量」。為了讓這個有效，review 中用新 commit（別 force-push）很重要。
- **allow edits by maintainers**（Ch 10）：開 PR 時勾選，讓維護者能直接 push 小修到你的 branch——有時維護者懶得來回，直接幫你改完合併。
- **CODEOWNERS 自動指派 reviewer**（Ch 24）：你 PR 碰到的檔案會自動找對應的 reviewer。
- **draft → ready 的 review 時機**：draft PR 通常不觸發正式 review；轉 ready 才請人審（Ch 11）。

## 動手練習

1. 在你自己的兩個帳號（或找朋友/用測試 repo）上，開一個 PR、用另一個身分 review（留 inline comment + request changes），體驗「被審」。
2. 針對 review，改 code、push、看 PR 自動更新、逐條回覆 comment、re-request review——走完整流程。
3. 練習寫三種回應：「Done in <commit>」、有禮貌的「不改 + 理由」、「提問澄清」。
4. 故意在 review 中 force-push 一次，觀察 reviewer 的 inline comment 變 outdated / 增量 diff 的變化——體會「review 中別 force-push」的理由。
5. 試 GitHub 的 "Add suggestion"（reviewer 端）+ 一鍵接受（作者端）——體驗快速修改。
6. 找一個真實開源專案「來回很多輪才合併」的 PR，讀作者怎麼回應 review——學好的（和不好的）回應方式。

## 本章重點整理

- 心態決定一切：review 是協作不是審判，reviewer 在幫你（尤其無償的開源維護者）。
- review 形式：inline comment + comment / approve / request changes；changes requested 是正常結果，處理就好。
- 流程：全讀 → 分類處理 → 改+push（PR 自動更新）→ **逐條回應**（done/解釋/提問）→ re-request review。
- **改了一定要回應 comment**，沉默讓 reviewer 困惑；conversation 通常讓 reviewer resolve。
- force-push 時機：review 中加 commit（別洗掉意見）、merge 前才整理（看專案文化）；永遠 `--force-with-lease`。
- 分歧用技術理由禮貌討論，小事讓步，僵住時尊重維護者的最終決定權。

## 自我檢核

- [ ] 為什麼說「心態」是被 review 最重要的事？錯的心態會怎樣？
- [ ] 改了 reviewer 指出的問題後，除了 push，還必須做什麼？
- [ ] 為什麼 review 進行中盡量別 rebase/force-push？什麼時候整理歷史比較安全？
- [ ] 不同意 reviewer 的意見時，好的回應和差的回應差在哪？
- [ ] 改完 changes requested 後，怎麼讓 reviewer 回來再看？

## 延伸閱讀

### 部落格 / 文章

- **[How to Make Your Code Reviewer Fall in Love with You](https://mtlynch.io/code-review-love/)** — Michael Lynch
  - **這篇說什麼**：從作者角度，怎麼讓 review 順利、討 reviewer 喜歡。
  - **讀哪裡**：整篇；本章「被審方」的最佳延伸。
  - **為什麼值得讀**：實務、具體、換位思考，這個主題的經典。

- **[Code Review Guidelines (the contributor side)](https://google.github.io/eng-practices/review/developer/)** — Google Engineering Practices
  - **這篇說什麼**：Google 內部的「PR 作者該怎麼做」指南——怎麼回應、處理分歧、何時 escalate。
  - **讀哪裡**：The CL author's guide 整章。
  - **為什麼值得讀**：大廠的系統性做法，含「分歧怎麼解」的成熟流程。

### 官方文件

- **[GitHub Docs: Reviewing changes in pull requests](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/reviewing-changes-in-pull-requests)**
  - **讀哪裡**：comment/approve/request changes、suggestions、resolve conversation 的操作。
  - **和本章的關聯**：操作面的權威。

被審懂了，下一章談 PR 的另一個守門員——CI：為什麼你的 PR 變紅、status check 是什麼、怎麼本地先跑避免丟臉。

→ [Ch 14 GitHub Actions / CI](./14-github-actions-ci.md)
