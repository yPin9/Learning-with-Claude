# Ch21: bisect

二分搜尋歷史找 regression commit。**debug 神器**。

## 21.1 問題

「昨天還好好的，今天突然 crash」。歷史有 500 個 commit，哪個壞的？

手動一個個試：O(N)。
**Bisect**：O(log N)。500 個 commit 最多 9 次就找到。

## 21.2 基本流程

```bash
git bisect start
git bisect bad                    # 當前（或指定 commit）是壞的
git bisect good v1.0              # v1.0（或某 commit）是好的

# Git 自動 checkout 中間的 commit
# 你測試...
git bisect good      # 或 git bisect bad

# 繼續直到找出 first bad commit
# Git 顯示：abc1234 is the first bad commit

git bisect reset      # 結束，回到原 HEAD
```

## 21.3 實戰範例

假設：
- 當前 HEAD 會 crash
- 一週前的 `v1.2` tag 是好的

```bash
git bisect start
git bisect bad              # HEAD
git bisect good v1.2

# Bisecting: 125 revisions left to test after this (roughly 7 steps)
# [abc1234] feat: xxx

# 編譯、跑 test
make && ./a.out
# 正常！
git bisect good

# Bisecting: 62 revisions left to test after this (roughly 6 steps)
# [def5678] refactor: yyy

make && ./a.out
# Crash
git bisect bad

# ... 繼續 ...

# 最後
# 789abcd is the first bad commit
# commit 789abcd
# Author: ...
# Date: ...
#
#     fix: refactor data layer
git bisect reset
```

找到 commit，看 diff：
```bash
git show 789abcd
```

## 21.4 `git bisect run`：自動化

有測試腳本時，讓 bisect 自己跑：

```bash
git bisect start HEAD v1.2       # 一次指定 bad 和 good
git bisect run ./test.sh
```

`test.sh` 回傳：
- **0**：good（沒 bug）
- **1-124** 或 **126-127**：bad（有 bug）
- **125**：skip（無法測）
- **其他**：abort

範例：
```bash
#!/bin/bash
# test.sh
make || exit 125          # 編譯失敗，skip
./a.out --test || exit 1  # 測失敗（有 bug）
exit 0                     # pass
```

```bash
git bisect start HEAD v1.2
git bisect run ./test.sh
# Git 跑 log(N) 次、自動找
# Result: 789abcd is the first bad commit
```

## 21.5 `skip`：無法測的 commit

某個 commit 編不過（例如 WIP 中間狀態）：
```bash
git bisect skip
```

Git 會選另一個 commit 繼續。如果一連串都 skip 可能找不到確切那一個。

## 21.6 改 bisect 的「意義」

預設是 "bad" / "good"。改用更貼切的詞：
```bash
git bisect start --term-old=fast --term-new=slow
git bisect slow
git bisect fast v1.2
# 找「從哪個 commit 開始變慢」
```

## 21.7 視覺化進度

```bash
git bisect view              # 視覺化顯示剩下範圍
git bisect log               # 看已判定過的 commit
git bisect visualize         # = view
```

## 21.8 中途退出

```bash
git bisect reset             # 結束 bisect，回到原 HEAD
git bisect reset HEAD        # 明確指定
git bisect reset abc1234     # 結束後切到某 commit
```

## 21.9 常見 pitfalls

### 陷阱 1：測試不穩定
如果測試隨機 pass/fail，bisect 會帶你去錯的地方。確保 test **deterministic**。

### 陷阱 2：bisect 範圍不對
`good` 其實也 bad → bisect 找不到正確答案。確認 `good` 端真的沒 bug。

### 陷阱 3：編譯失敗的 commit 一堆
中間很多 commit 編不過，要一直手動 skip。自動化 script 用 exit 125 處理。

### 陷阱 4：Flaky test
測試偶爾 flake。建議跑**多次**再判定：
```bash
#!/bin/bash
# test.sh
for i in {1..5}; do
    ./a.out --test || exit 1
done
exit 0
```

### 陷阱 5：bisect 中切 branch
不要。bisect 進行中不要用 `git switch`、`git checkout`。會破壞 bisect 狀態。

## 21.10 進階：跨 merge 的 bisect

有 merge commit 時 bisect 可能檢查 merge 的某邊。通常正確，但有時要用：

```bash
git bisect start --first-parent HEAD v1.2
```

只走 main 的線性歷史，不進 merged feature 的內部 commit。

## 21.11 bisect 的腳本化範例

### 找哪個 commit 讓 test X 開始失敗
```bash
git bisect start
git bisect bad HEAD
git bisect good v1.0

git bisect run bash -c 'cargo test test_feature_x --quiet'
```

### 找哪個 commit 讓 binary 變大
```bash
#!/bin/bash
# test.sh
make build || exit 125
size=$(stat -c %s ./a.out)
if [ "$size" -gt 1000000 ]; then
    exit 1    # 太大，bad
fi
exit 0
```

### 找哪個 commit 讓 benchmark 變慢
```bash
#!/bin/bash
# test.sh
make build || exit 125
time=$(./bench | awk '{print $1}')
if (( $(echo "$time > 100" | bc -l) )); then
    exit 1
fi
exit 0
```

## 21.12 其他 debug 時的 git 招

### `git log -S "bug string" --source`
找哪個 commit 引入某段 code（Ch3 講過）。

### `git log --all --follow -p -- path/to/file`
某檔的完整 diff 歷史。

### `git blame -L 10,20 file.c`
某段 code 的 blame（Ch3）。

### `git bisect` vs `git log -S`
- `-S`：我知道哪行壞、找哪個 commit 加/改了它
- `bisect`：我**不知道**哪行壞、只知道行為變了

互補。先 `-S` 看有沒有明顯的可疑 commit，不行再 bisect。

## 21.13 完整實戰 workflow

```bash
# 1. 確認 reproducer
./a.out --test        # 確認現在會 fail

# 2. 確認 good 端
git switch v1.0
make
./a.out --test        # 確認會 pass
git switch main

# 3. 自動化
cat > /tmp/test.sh << 'EOF'
#!/bin/bash
make clean
make || exit 125
./a.out --test || exit 1
exit 0
EOF
chmod +x /tmp/test.sh

# 4. Bisect
git bisect start HEAD v1.0
git bisect run /tmp/test.sh

# 5. 分析
git show <first-bad-commit>

# 6. 結束
git bisect reset
```

## 21.14 練習

Sandbox：
```bash
mkdir /tmp/bisect-test && cd /tmp/bisect-test
git init

# 建一堆 commit
for i in {1..30}; do
    echo "version $i" > v.txt
    git add v.txt
    git commit -m "v$i"
done

# 在某 commit 混一個「壞」
git switch --detach HEAD~15
echo "BROKEN" > v.txt
git add v.txt
git commit --amend --no-edit  # 改那 commit
```

這樣太人工了，真實練習：找一個中型 open source 專案，挑一個舊 bug PR，看 PR 關聯的 commit，自己 bisect 找它。

練習 `git bisect run` 寫自動化 script。

## 21.15 Debug workflow：bisect 找出元兇之後

```bash
# 找到壞 commit
git show abc1234          # 看 diff
git log --stat abc1234^..abc1234   # 看哪些檔

# Revert 或修
git revert abc1234        # 快速 revert
# 或
# 手動改回來
```

**bisect 本身不修 bug**，它只找到罪魁。找到後照正常 workflow 處理。

## 21.16 本章重點
- `git bisect start` / `bad` / `good` 二分搜尋歷史
- **`git bisect run <script>`** 自動化（殺手功能）
- Script exit code：0 good、非 0 bad、125 skip
- 確保 test deterministic（不要 flaky）
- Bisect 中不要切 branch
- `--first-parent` 跳過 merged feature 的內部
- 配合 `git show` + `git revert` 完成 debug 流程
