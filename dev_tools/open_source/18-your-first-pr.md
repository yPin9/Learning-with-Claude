# Ch 18 — 你的第一個 PR

> **目標**：把功課（Ch 17）變成一個真正送出的 PR。掌握「從小做起」的貢獻階梯（typo → docs → bug → feature）、scope 控制（一個 PR 一件事）、第一個 PR 的心態，以及一個被低估的真相：第一個 PR 的目標不是「做大事」，是「走通流程、建立信任」。

> **環境**：GitHub、git、`gh` CLI。前置：Ch 10-17。

## 為什麼第一個 PR 特別重要（也特別該小）

第一個 PR 是個門檻——跨過去，你就從「想貢獻的人」變成「貢獻過的人」，後面越來越順。但很多人卡在這：要嘛遲遲不敢發（怕做不好），要嘛一上來想做大功能（難、慢、易被拒）。

關鍵心態調整：**第一個 PR 的目標不是「貢獻一個了不起的東西」，是「成功走通一次完整流程、和維護者建立第一次正面互動」。** 一個被合併的 typo 修正，價值遠超一個被無視的大功能 PR——前者給了你成功經驗、維護者的初步信任、和對該專案流程的熟悉。

所以：**第一個 PR，越小越好。**

## 先建立直覺：貢獻的階梯，從最低一階踏起

承 Ch 16 的難度階梯。第一個 PR 從最低、最安全的一階開始：

```
   風險/難度
   ▲
   │  加大功能 / 重構          ← 別當第一個 PR
   │  加小功能（先討論）
   │  修一般 bug
   │  修 good first issue（小 bug）
   │  改善錯誤訊息 / 小 UX       ← 不錯的第一個
   │  文件 / typo / 範例修正     ← 完美的第一個 PR
   ▼
```

**完美的第一個 PR 候選**：

- 文件裡的 typo、過時的範例、壞掉的連結
- README 安裝步驟在你的環境跑不通（補一步說明）
- 錯誤訊息不清楚（改清楚）
- 一個你能複現的小 bug（good first issue）

這些的共同點：**小、明確、低風險、容易驗證、不需要深入 codebase**。維護者看一眼就能合併，你快速得到「成功」的正回饋。

> 「typo 修正不算真貢獻」是錯的迷思（Ch 16 講過）。它完全算——文件正確性影響每個使用者，而且你藉此走通了流程、認識了維護者。Linus Torvalds 的第一個外部貢獻者很多也是從小修開始。別瞧不起小貢獻。

## scope 控制：一個 PR 一件事

承 Ch 11（PR 往小拆）、Ch 2（atomic）。第一個 PR 尤其要嚴守 **scope**——只做一件事，別「順手」多做：

```
   你本來要修一個 typo，但你發現...
   ❌ "順手把這個函式重構一下"
   ❌ "這裡縮排不一致，順便全改了"
   ❌ "啊那個 bug 也很煩，一起修了"
   
   ✅ 只修 typo。其他另開 PR / issue。
```

為什麼嚴守 scope：

- **好 review**：維護者一眼看懂「這 PR 就是修這個」，秒合併。
- **不引爭議**：你「順手」的重構/格式改動，可能不符專案偏好、引發爭論，拖累你本來簡單的 typo 修正。
- **CI 風險低**：改得少，CI 紅的機率低。

新手最常見的破壞 scope：**改格式/縮排**。你的編輯器可能自動重排了整個檔案的格式，diff 變成「改了 200 行」（其實只有 1 行是你要的）。**檢查你的 diff**（`git diff`），確認只有你要改的東西——別讓自動格式化污染你的 PR。

## 動手：把功課變成 PR

承 Ch 17 做完功課，現在動手（以修一個小 bug 為例）：

```bash
# 1. 確認在最新 main 開的 branch（Ch 3/17）
git switch -c fix/typo-in-readme upstream/main   # 已 fork+clone+認領

# 2. 做改動——只做這一件事
# ...改 README 的 typo...

# 3. 檢查 diff——確認只改了該改的（沒被自動格式化污染）
git diff

# 4. commit（好 message，Ch 2；若 DCO 用 -s，Ch 17/21）
git commit -m "Fix typo in installation instructions

'depencency' -> 'dependency' in the setup section.

Closes #88"

# 5. 跑測試 / 本地檢查（Ch 14，即使是文件也跑一下 CI 會跑的）
# (文件改動可能有 lint/link-check)

# 6. push 到你的 fork
git push -u origin fix/typo-in-readme

# 7. 開 PR（好標題/描述，Ch 11）
gh pr create --repo owner/project --base main \
  --title "Fix typo in installation instructions" \
  --body "Corrects 'depencency' to 'dependency' in the README setup section. Closes #88"
```

然後等：CI 跑（確認綠，Ch 14）、維護者 review（你回應，Ch 13/19）。

## 第一個 PR 的心態

幾個讓你不卡關的心態：

**1. 不完美也沒關係。** 你的第一個 PR 可能會被 request changes——這正常，不是失敗。review 是過程的一部分，改就好（Ch 19）。沒有人第一次就完美。

**2. 維護者沒立刻回不是針對你。** 開源維護者多是無償、業餘時間做的。你的 PR 可能要等幾天甚至幾週才有人看。耐心（Ch 20）。等太久可以禮貌地 ping 一次。

**3. 被拒絕也是學習。** 有時 PR 不被接受（不符方向、維護者不想要、有更好的解法）。別玻璃心——理解原因、學到東西、換個專案/issue。被拒一個 PR 不代表你不行。

**4. 從「降低維護者負擔」想。** 你的 PR 越小、越清楚、CI 越綠、描述越完整、回應越得體，維護者越輕鬆、越願意合併。把自己放在「幫維護者省事」的位置。

## 如果是加功能（先別急著當第一個 PR）

如果你想做的是加功能（不是修 bug/文件），**強烈建議先開 issue 討論**（Ch 12/17），別直接寫 PR：

```
   ❌ 悶頭寫完 300 行功能 PR → 維護者：「我們不想要這個功能」→ 白做
   
   ✅ 先開 issue：「我想加 X 來解決 Y，你們有興趣嗎？方向對嗎？」
      → 維護者：「好啊，但建議用 Z 方式」→ 你照建議做 → 順利合併
```

加功能涉及「維護者想不想要」「該怎麼設計」——這些要**先對齊**再動手。直接發大功能 PR 是新手最浪費力氣的事之一。所以第一個 PR 別挑加功能，挑修 bug/文件（這些不太需要事先討論方向，做對就會被接受）。

## 一個完整的「第一個 PR」決策樹

```
   我想貢獻什麼？
   ├─ typo / 文件 / 壞連結 → 直接做（最佳第一個 PR），小 scope，發 PR
   ├─ 小 bug（good first issue）→ 複現（Ch 17）→ 修 → 加測試 → 發 PR
   ├─ 一般 bug → 複現 → 讀 codebase → 修 → 測試 → 發 PR（可能多輪 review）
   └─ 加功能 → 先開 issue 討論方向 → 有共識 → 才寫 PR（別當第一個）
```

第一次就走最左邊兩條（typo/文件、小 bug），走通流程、嚐到成功，再往難的爬。

## 踩雷集錦

1. **第一個 PR 就挑大功能/重構**：難、慢、易被拒、要先討論。第一次挑 typo/文件/小 bug。
2. **破壞 scope（順手多做）**：把重構、格式、別的 bug 塞進來。一個 PR 一件事，其他另開。
3. **自動格式化污染 diff**：編輯器重排整檔，diff 變幾百行。`git diff` 檢查，只留該改的。
4. **加功能不先討論就發 PR**：可能白做（維護者不想要/方向錯）。先開 issue 對齊。
5. **被 request changes 就放棄/玻璃心**：那是正常過程，改就好（Ch 19）。第一次不完美很正常。
6. **維護者沒回就焦慮/連環 ping**：開源是業餘時間，等幾天正常。耐心，真的久了禮貌 ping 一次（Ch 20）。
7. **PR 描述敷衍**：即使是 typo，也寫清楚改了什麼、關聯 issue（Ch 11）。

## 進階：再往深一層

- **first-timers-only 友善專案**：有些專案專門為新手準備「手把手」的 first issue（含詳細步驟），是極佳的第一次體驗（firsttimersonly.com）。
- **連續貢獻建立節奏**：第一個 PR 合併後，趁熱在同專案找第二個——你已熟悉流程和 codebase，第二個更快，也開始累積在該專案的信任（Ch 37）。
- **被拒的 PR 怎麼處理**：禮貌詢問原因、感謝 review、若是方向問題就接受、把學到的用在下一個。被拒不刪 fork（可能還會貢獻）。
- **PR 模板強制的 checklist**：認真勾（測試加了、文件更新了、CI 綠了）——這是維護者的信任基礎。
- **「stale PR」自救**：PR 久沒人理，可以 rebase 跟上 main（保持可合併）、禮貌 ping、或在相關 issue/discussion 提一下。但別 spam。
- **降低維護者負擔的細節**：小 PR、綠 CI、完整描述、得體回應、自己先 review 過 diff——這些加起來決定維護者對你的印象。

## 動手練習

1. 對你 Ch 16/17 選定的目標，確認它是「適合第一個 PR」的（typo/文件/小 bug，不是大功能）。是大功能就先開 issue 討論。
2. 做改動後，`git diff` 仔細檢查——確認只有你要改的，沒被自動格式化污染。
3. 寫一個好的 commit message（Ch 2）+ 好的 PR 描述（Ch 11），即使改動很小。
4. 本地跑 CI 會跑的檢查（Ch 14），確認綠了才 push。
5. **真的發出這個 PR**（這也是練習 D 的核心）——對一個真實的新手友善專案。
6. 發完後，列出你做對/可改進的地方（scope 守住了嗎？描述夠清楚嗎？CI 綠嗎？），為下一個 PR 改進。

## 本章重點整理

- 第一個 PR 的目標是「走通流程 + 建立信任」，不是「做大事」——越小越好。
- 從貢獻階梯最低踏起：typo/文件/壞連結 是完美的第一個 PR（小、明確、低風險、易驗證）。
- 嚴守 scope：一個 PR 一件事，別順手重構/改格式/修別的；`git diff` 檢查避免自動格式化污染。
- 加功能**先開 issue 討論方向**，別直接發大 PR（可能白做）；第一個 PR 別挑加功能。
- 心態：不完美沒關係（review 會幫你）、維護者沒立刻回不是針對你（業餘時間）、被拒也是學習、從「降低維護者負擔」想。

## 自我檢核

- [ ] 為什麼第一個 PR 該小、甚至從 typo 開始？目標是什麼？
- [ ] 「破壞 scope」最常見的形式是什麼（提示：格式）？怎麼避免？
- [ ] 為什麼加功能不該當第一個 PR、且該先開 issue？
- [ ] 第一個 PR 被 request changes，代表你失敗了嗎？該怎麼想？
- [ ] 「降低維護者負擔」具體包含哪些做法？

## 延伸閱讀

### 官方指南

- **[GitHub Open Source Guides: Submitting a contribution](https://opensource.guide/how-to-contribute/#how-to-submit-a-contribution)** — GitHub
  - **讀哪裡**："Opening a pull request" 與 "What happens after you submit a contribution"。
  - **和本章的關聯**：第一個 PR 全流程與心態的官方版。

### 站點 / 工具

- **[First Timers Only](https://www.firsttimersonly.com/)** 與 **[firstcontributions/first-contributions](https://github.com/firstcontributions/first-contributions)**
  - **這些是什麼**：專為「第一次貢獻」設計的手把手練習 repo / 指南。
  - **和本章的關聯**：練習 D 的安全起點——對它們發第一個 PR，流程一模一樣但零壓力。

### 部落格 / 文章

- **[How to make your first open source contribution](https://www.freecodecamp.org/news/a-beginners-guide-to-open-source/)** 類 freeCodeCamp 指南
  - **這篇說什麼**：第一次貢獻的完整心態與步驟。
  - **為什麼值得讀**：補充本章的鼓勵與實例（挑近年、有作者的版本）。

PR 發出去了，下一章是接下來最關鍵的階段——在 review 中迭代：面對 changes requested、處理分歧、知道什麼時候該堅持、什麼時候該放手。

→ [Ch 19 在 review 中迭代](./19-iterating-in-review.md)
