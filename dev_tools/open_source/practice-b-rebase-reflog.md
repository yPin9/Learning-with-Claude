# 練習 B — 複雜 rebase 衝突 + reflog 救援

> **目標**：把 Part 2（remote、merge、rebase、衝突解決、reflog）綜合起來。你會 rebase 一條和 main 嚴重分岔的 branch，逐個 commit 解衝突；中途故意「搞砸」一次，再用 reflog 從災難中救回來。完成後你會具備協作中最讓人緊張的兩件事——複雜衝突與災難復原——的實戰能力與信心。

> 前置：[Ch 6](./06-rebase.md)、[Ch 8](./08-conflict-resolution.md)、[Ch 9](./09-cherrypick-stash-reflog.md)。

## 背景與動機

協作久了一定會遇到：你的 feature branch 開了一週，main 同時被改了一堆，你要 rebase 跟上——結果每個 commit 都衝突，解到一半手滑，branch 看起來毀了。這個練習刻意製造這個場景，讓你在安全環境裡練「逐 commit 解衝突」和「reflog 救援」。練過一次，真實遇到時你會冷靜得多——因為你知道**幾乎沒有救不回來的**。

## 任務規格

### 製造一個「嚴重分岔」的場景

照著跑這個腳本，產生 feature 和 main 各自改同一個檔案、注定衝突的歷史：

```bash
mkdir rebase-practice && cd rebase-practice
git init && git switch -c main
git config rerere.enabled true        # 開 rerere（Ch 8），體驗它的威力

# 初始檔案
cat > config.py <<'EOF'
TIMEOUT = 30
RETRIES = 3
DEBUG = False
LOG_LEVEL = "INFO"
EOF
git add . && git commit -m "Initial config"

# 開 feature branch，做三個改動（每個都改 config.py）
git switch -c feature/tune-config
sed -i 's/TIMEOUT = 30/TIMEOUT = 60/' config.py
git commit -am "Increase timeout to 60"
sed -i 's/RETRIES = 3/RETRIES = 5/' config.py
git commit -am "Bump retries to 5"
sed -i 's/LOG_LEVEL = "INFO"/LOG_LEVEL = "DEBUG"/' config.py
git commit -am "Set log level to DEBUG for testing"

# 回到 main，做衝突的改動（也改同幾行！）
git switch main
sed -i 's/TIMEOUT = 30/TIMEOUT = 45/' config.py
git commit -am "Set timeout to 45 (ops recommendation)"
sed -i 's/LOG_LEVEL = "INFO"/LOG_LEVEL = "WARNING"/' config.py
git commit -am "Default log level to WARNING in production"
```

現在的局面：

```bash
git switch feature/tune-config
git log --oneline --all --graph
```

```
   * (main)    Default log level to WARNING in production
   * (main)    Set timeout to 45 (ops recommendation)
   | * (feature) Set log level to DEBUG for testing
   | * (feature) Bump retries to 5
   | * (feature) Increase timeout to 60
   |/
   * Initial config
```

feature 改了 TIMEOUT(60)、RETRIES(5)、LOG_LEVEL(DEBUG)；main 改了 TIMEOUT(45)、LOG_LEVEL(WARNING)。TIMEOUT 和 LOG_LEVEL 兩邊都改 = 注定衝突。

### 你要做的事

1. **rebase feature 到 main**，逐個 commit 解衝突。決策：每個衝突你要保留誰的值？（這需要你判斷，不是機械式選邊——這就是真實協作的樣子。）
2. **中途故意搞砸**：在解某個衝突時亂解（留錯值或留 marker），continue 下去，發現結果不對。
3. **用 reflog 救援**：把整個 rebase 當作沒發生過，回到 rebase 前的乾淨 feature branch。
4. **重新正確 rebase**：這次注意 rerere 是否幫你自動套用之前解過的衝突。
5. 最終得到一個乾淨、基於最新 main、衝突正確解決的 feature branch。

### 決策準則（這題沒有單一正確答案，但要合理）

- **TIMEOUT**：feature 要 60、main（ops 建議）要 45——假設你決定尊重 ops，留 45（但這是你的判斷，要能說出理由）。
- **LOG_LEVEL**：feature 要 DEBUG（測試用）、main 要 WARNING（生產）——假設留 WARNING（生產設定優先），但你的 "Set log level to DEBUG" 這個 commit 就變得沒意義了，該思考是否 drop 它。
- **RETRIES**：只有 feature 改（5），main 沒碰——不衝突，自動保留 5。

### 驗收標準

- [ ] 完成 rebase，feature 基於最新 main（`git log --graph` 是直線）
- [ ] 至少正確解決 TIMEOUT 和 LOG_LEVEL 兩處衝突，且你能說出為什麼留那個值
- [ ] 成功用 reflog 從「搞砸的 rebase」救回 rebase 前的狀態
- [ ] 最終 config.py 沒有殘留 conflict marker，值符合你的決策
- [ ] （加分）觀察到 rerere 在第二次 rebase 自動套用你的解法

## 期望輸出範例

```
$ git rebase main
Auto-merging config.py
CONFLICT (content): Merge conflict in config.py
error: could not apply a1b2c3... Increase timeout to 60
# 解 TIMEOUT 衝突...
$ git add config.py && git rebase --continue
# 下一個 commit 可能又衝突（LOG_LEVEL）...

# 最終：
$ git log --oneline --graph
* xxxxxxx Bump retries to 5
* xxxxxxx Increase timeout to 60 (resolved: kept 45)
* xxxxxxx Default log level to WARNING in production
* xxxxxxx Set timeout to 45 (ops recommendation)
* xxxxxxx Initial config

$ cat config.py
TIMEOUT = 45
RETRIES = 5
DEBUG = False
LOG_LEVEL = "WARNING"
```

## 如果你卡住了

1. **rebase 衝突方向（ours/theirs）暈了？** 回 Ch 8：rebase 時 `<<<<<<< HEAD` 下面是「你 rebase 到的 main」的版本，`>>>>>>>` 上面是「你正在重放的 feature commit」。方向和 merge 相反。開 `git config merge.conflictStyle zdiff3` 看 base 會清楚很多。
2. **解完一個又衝突？** 正常——rebase 逐 commit 重放，每個改了衝突區的 commit 都會各自衝突（Ch 8）。`git add` + `git rebase --continue` 處理下一個。
3. **怎麼知道 rebase 前的狀態在 reflog 哪裡？** `git reflog`，找 rebase 開始前那筆（通常標 "rebase (start)" 之前的 commit，或最後一個 "commit:" 條目）。
4. **搞砸後完全亂了？** 別慌，這正是練習目的。`git rebase --abort` 是第一招（rebase 進行中時）；如果 rebase 已「完成」但結果錯了，用 `git reflog` + `git reset --hard HEAD@{n}`。
5. **LOG_LEVEL commit 變得沒意義？** 如果你決定留 WARNING，那「Set log level to DEBUG」這個 commit 重放後等於沒做（或衝突）。可以在 rebase 時 drop 它（`rebase -i`，Ch 7），或解衝突時保留 WARNING。

## 實作步驟建議

### Step 1：先記下「逃生點」

```bash
git switch feature/tune-config
git log --oneline                # 記下 feature 頂端的 hash（萬一要手動救）
git branch backup-feature        # 額外保險：開一個備份 branch（也是一種救援）
```

子目標：知道 rebase 前的狀態，建立安全感。

### Step 2：第一次 rebase，逐個解衝突

```bash
git config merge.conflictStyle zdiff3   # 開 zdiff3 好解衝突
git rebase main
# 解 TIMEOUT 衝突（決定留 45）→ git add → git rebase --continue
# 解 LOG_LEVEL 衝突（決定留 WARNING）→ git add → git rebase --continue
```

子目標：完成一次正確的 rebase，理解逐 commit 解衝突的節奏。

### Step 3：故意搞砸（學救援用）

reset 回去重來，這次亂解：

```bash
git reset --hard backup-feature   # 回到 rebase 前
git rebase main
# 這次故意：留錯值、或留一個 ======= marker 沒刪，git add，continue
# 觀察最終 config.py 是壞的
```

子目標：製造一個「搞砸的結果」。

### Step 4：reflog 救援

```bash
git reflog                        # 找 rebase 開始前的 HEAD@{n}
git reset --hard HEAD@{n}         # 救回 rebase 前的乾淨 feature
git log --oneline                 # 確認回到三個原始 commit
```

子目標：不靠 backup branch，純用 reflog 救回——這是真實情境（你常常忘了開 backup）。

### Step 5：正確重做 + 觀察 rerere

```bash
git rebase main
# 因為 rerere 開著（Step 0），且你 Step 2 解過同樣的衝突，
# git 可能自動套用："Resolved 'config.py' using previous resolution."
# 檢查它套對了再 git add + continue
```

子目標：完成正確 rebase，見識 rerere 的自動套用。

## 完整參考解答

**自己卡到 Step 4 再看。**

<details>
<summary>點開完整流程</summary>

### Step 2：第一次 rebase 解衝突

```bash
git config merge.conflictStyle zdiff3
git switch feature/tune-config
git branch backup-feature         # 保險
git rebase main
```

第一個衝突（重放 "Increase timeout to 60"）：

```python
TIMEOUT = <<<<<<< HEAD
45                    # main 的版本（ops 建議）
||||||| parent of a1b2c3 (Increase timeout to 60)
30                    # base（原始值，zdiff3 顯示）
=======
60                    # feature 的版本（你正在重放的 commit）
>>>>>>> a1b2c3 (Increase timeout to 60)
```

決策：尊重 ops，留 45。編輯成：

```python
TIMEOUT = 45
```

```bash
git add config.py
git rebase --continue
```

> 注意：這個 commit 原本要「改成 60」，但你解衝突留了 45——所以這個 commit 重放後實際上「沒改變 TIMEOUT」。這是合理的（你判斷 ops 的值優先）。commit message 可能該 reword 反映這點（用 `git rebase -i` 或 `--continue` 時的編輯機會）。

下一個衝突（重放 "Set log level to DEBUG"）：

```python
LOG_LEVEL = <<<<<<< HEAD
"WARNING"             # main（生產設定）
||||||| ...
"INFO"               # base
=======
"DEBUG"              # feature
>>>>>>> ... (Set log level to DEBUG for testing)
```

決策：生產設定優先，留 WARNING：

```python
LOG_LEVEL = "WARNING"
```

```bash
git add config.py
git rebase --continue
```

"Bump retries to 5" 不衝突（main 沒碰 RETRIES），自動套用。rebase 完成。

```bash
$ cat config.py
TIMEOUT = 45
RETRIES = 5
DEBUG = False
LOG_LEVEL = "WARNING"
```

### Step 3-4：搞砸 + reflog 救援

```bash
git reset --hard backup-feature   # 回 rebase 前
git rebase main
# 第一個衝突，故意留錯：
#   TIMEOUT = 999    （亂填）
git add config.py
git rebase --continue
# ...繼續，得到一個錯的結果...
$ cat config.py
TIMEOUT = 999        # 壞了

# 救援：
$ git reflog
a1b2c3 HEAD@{0}: rebase finished: returning to refs/heads/feature/tune-config
d4e5f6 HEAD@{1}: rebase: Bump retries to 5
...
g7h8i9 HEAD@{5}: checkout: moving from ... 
j0k1l2 HEAD@{6}: commit: Set log level to DEBUG for testing   ← rebase 前的 feature 頂端！

$ git reset --hard HEAD@{6}        # 或直接 reset --hard backup-feature
$ git log --oneline                # 回到三個原始 commit，乾淨
$ cat config.py
TIMEOUT = 60                       # 回到 feature 原始狀態
```

reflog 把「搞砸的 rebase」完全抹掉，回到 rebase 從沒發生的狀態。

### Step 5：正確重做 + rerere

```bash
git rebase main
# 因為 rerere 開著，且 Step 2 你解過同樣的 TIMEOUT/LOG_LEVEL 衝突：
#   Resolved 'config.py' using previous resolution.
# git 自動套用你 Step 2 的解法（留 45、留 WARNING）！
# 你只需檢查、git add、continue
```

rerere 記住了你的解法，第二次同樣的衝突自動處理——這在反覆 rebase 長命 branch 時省下大量重複勞動。

**解答說明**：

- **逐 commit 解衝突是 rebase 的常態**：feature 有兩個 commit 改了衝突區，所以解兩次。理解這個節奏（status → 編輯 → add → continue）就不會慌。
- **解衝突是判斷，不是機械選邊**：TIMEOUT 留 45、LOG_LEVEL 留 WARNING 是「決策」——你要能說出理由（ops/生產優先）。真實協作就是這樣權衡。
- **reflog 是終極安全網**：就算沒開 backup branch，reflog 也能救回。記住它，你就敢大膽 rebase。
- **rerere 的價值**：第二次免解同樣衝突。對「rebase 一條長命 branch、反覆撞同樣衝突」的真實場景，這是救命功能。
- **backup branch vs reflog**：開 backup branch（Step 1）是「主動保險」，reflog 是「事後救援」。兩者都該會——但真實情境你常忘了開 backup，所以 reflog 更重要。

</details>

## 測試用例

| 情境 | 預期 |
|---|---|
| 第一次 rebase | 兩次衝突（TIMEOUT、LOG_LEVEL），RETRIES 自動合併 |
| 解衝突後 `cat config.py` | TIMEOUT=45, RETRIES=5, LOG_LEVEL=WARNING（依你決策）|
| 故意亂解後 | config.py 有錯值/marker |
| reflog + reset --hard | 回到 rebase 前（TIMEOUT=60 的原始 feature）|
| 第二次 rebase（rerere 開）| 自動套用前次解法 |
| `git log --graph` 最終 | 直線（feature 在 main 之上）|

## 延伸挑戰（加分）

1. **改用 merge 而非 rebase**：把 main merge 進 feature（`git merge main`），比較——衝突一次解決（不是逐 commit），但歷史留下 merge commit。體會 Ch 6 的 merge vs rebase 差異。
2. **drop 沒意義的 commit**：既然 LOG_LEVEL 最終留 WARNING，「Set log level to DEBUG」這個 commit 變得多餘。用 `git rebase -i` drop 它，讓歷史更乾淨（Ch 7）。
3. **`git rerere forget`**：解錯後想讓 rerere 忘記某個解法重新解，用 `git rerere forget <file>`。
4. **模擬「別人也基於 feature 工作」**：clone 兩份，一份 rebase + force-push，另一份 pull，重現 Ch 6 的黃金法則災難——理解為什麼這條 feature 能 rebase（沒別人用）、共享 branch 不能。
5. **三方衝突**：再開一條 branch 也改 config.py，製造更複雜的多 branch 衝突場景。

## 自我檢核

- [ ] 我能逐 commit 解 rebase 衝突，不被 ours/theirs 方向搞混（會用 zdiff3 看 base）
- [ ] 我理解解衝突是「判斷該留什麼」，不是機械選邊
- [ ] 我能用 reflog 把一個搞砸的 rebase 完全救回，即使沒開 backup branch
- [ ] 我見識過 rerere 自動套用先前的解法
- [ ] 我知道為什麼這條 feature 能安全 rebase（沒別人基於它），以及什麼 branch 絕不能

Part 2 完成——你補齊了協作必備的中階 git，且不再怕弄壞（reflog 兜底）。Part 3 進入 GitHub 平台：fork、Pull Request、issue、CI、code review——把 git 操作接上真實的協作平台。

→ [Ch 10 Fork 與 PR 的本質](./10-fork-and-pr.md)
