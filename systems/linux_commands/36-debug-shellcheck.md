# Ch 36 — debug 與 shellcheck

> **目標**：掌握 debug shell 腳本的工具與方法——`set -x`（追蹤每個執行的命令）、`bash -n`（語法檢查）、`shellcheck`（靜態分析，會抓出本課教的所有 quoting/陷阱）、以及系統化的 debug 流程。這是 Part 8 的收尾，把前面學的「怎麼寫對」補上「怎麼找出寫錯的地方」，讓你的腳本真正可靠。

> **環境**：bash 5.x，shellcheck 0.8+。shellcheck 是獨立工具（`apt install shellcheck`）。

## 為什麼 debug 工具是必備技能？

你會寫腳本了，但腳本不會一次就對。變數展開不如預期、quoting 出錯、邏輯有 bug——這些都要 debug。問題是 shell 腳本的錯誤常常**沈默**（Ch 35：預設寬容，出錯繼續跑），不像編譯語言會在編譯時報錯。你需要工具把「腳本實際在做什麼」攤開來看。

更好的是**預防**——shellcheck 是個靜態分析工具，它讀你的腳本，把本課教的所有陷阱（沒加引號的變數、`for in $(ls)`、`[ ]` 的問題、未定義變數…）**自動標出來**。這是 shell scripting 的「編譯器警告」。學會 `set -x`（看執行）+ shellcheck（看問題），你的腳本品質會質變——從「希望它對」變成「驗證它對」。

## 先建立直覺:讓腳本「說出」它在做什麼

```
debug 的核心：讓「沈默執行」變成「看得見」

  問題：腳本跑出錯誤結果，但你不知道哪一步錯
    （shell 不像 IDE 能設斷點單步）
        │
  兩種「看得見」的方法：
        │
  1. set -x（執行追蹤）：印出「每個實際執行的命令」
     看到變數展開成什麼、哪個分支走了、迴圈跑幾次
     → 「動態」debug：看腳本「實際」做了什麼
        │
  2. shellcheck（靜態分析）：不執行，讀程式碼找問題
     沒加引號、未定義變數、邏輯錯誤...
     → 「靜態」debug：執行前就抓出潛在 bug
        │
  → 動態（set -x）看「實際行為」
    靜態（shellcheck）看「潛在問題」
    兩者互補，都要會
```

關鍵心智：debug 是「讓沈默的腳本說出它在做什麼」。兩種互補方法——`set -x`（動態：印出每個實際執行的命令，看變數展開、分支、迴圈的真實行為）和 shellcheck（靜態：不執行，讀程式碼找潛在問題）。前者看「實際做了什麼」，後者看「哪裡可能錯」。

## set -x:執行追蹤

`set -x` 是 shell debug 最強的工具——它印出每個實際執行的命令（含展開後的值）：

```bash
#!/bin/bash
set -x                        # 開啟追蹤：印出每個執行的命令

name="alice"
echo "Hello, $name"
# 輸出（每個命令前有 + 號）：
# + name=alice
# + echo 'Hello, alice'        ← 看到 $name 展開成 alice！
# Hello, alice

# 在腳本「部分」開關（只 debug 可疑段落）
set -x                        # 開始追蹤
problematic_command "$var"
set +x                        # 關閉追蹤（+ 是關，- 是開，反直覺！）

# 命令列開啟（不改腳本）
bash -x script.sh             # 跑 script.sh 並追蹤
bash -x script.sh arg1 arg2

# 只追蹤一段（用括號 subshell）
( set -x; suspicious_function )    # 只追蹤這個 function

# PS4：自訂追蹤前綴（加行號等資訊，超有用）
export PS4='+ ${BASH_SOURCE}:${LINENO}: '
bash -x script.sh
# + script.sh:5: name=alice    ← 帶檔名和行號！
```

```
set -x 的輸出怎麼讀：

  每個「實際執行」的命令前面有 + 號（PS4 設定的前綴）
  顯示的是「展開後」的命令（變數已經變成值）
        │
  for f in *.txt; do echo "$f"; done
  追蹤輸出：
    + echo file1.txt       ← 第一圈，$f = file1.txt
    + echo file2.txt       ← 第二圈
    （看到 glob 展開成什麼、迴圈跑幾次）
        │
  → set -x 讓你看到「變數實際的值」「走了哪個分支」
    「迴圈實際跑幾次」—— 最常解開「為什麼結果不對」的謎
```

> **`set -x`（執行追蹤）是 shell debug 的瑞士刀——它印出每個命令展開後的真實樣子**。腳本結果不對時，最常見的原因是「變數的值不是你以為的」或「走了錯誤的分支」。`set -x` 把這些攤開——每個實際執行的命令前面加 `+`，顯示**展開後**的命令（`$name` 已經變成 `alice`、glob 已經變成實際檔名）。你能看到變數的真實值、哪個 if 分支走了、迴圈實際跑幾圈。用法靈活：腳本裡 `set -x`/`set +x` 包住可疑段落（`+x` 是**關閉**，`-x` 是開啟，這個正負號很反直覺）、或命令列 `bash -x script.sh`（不改腳本）。進階技巧：設 `PS4='+ ${BASH_SOURCE}:${LINENO}: '` 讓追蹤輸出**帶檔名和行號**，大腳本 debug 時知道是哪一行。`set -x` 解開絕大多數「為什麼結果不對」的謎。

## 其他 debug 選項

```bash
# bash -n：語法檢查（不執行，只檢查語法錯誤）
bash -n script.sh             # 檢查語法（漏 fi、漏 done、引號不配對等）
# 沒輸出 = 語法 OK；有輸出 = 指出語法錯在哪行

# set -v：印出「讀到的」原始行（未展開，和 -x 互補）
set -v                        # 印原始程式碼行（展開前）
# vs set -x 印展開後 → 兩者一起 set -xv 看「原始 → 展開」

# set -e -u（Ch 35）也是 debug 利器
set -u                        # 用未定義變數立刻報錯（抓出 typo 的變數名）

# 手動 debug：到處 echo（土法但有效）
echo "DEBUG: var=$var, count=$count" >&2    # 印中間狀態到 stderr

# 用 trap DEBUG（進階：每個命令前執行）
trap 'echo "About to run: $BASH_COMMAND" >&2' DEBUG

# 檢查腳本「實際」收到的參數
echo "DEBUG: got $# args: $*" >&2

# 互動式 debug：bashdb（bash debugger，像 gdb）
# bashdb script.sh   （能設斷點、單步，較少用但存在）
```

> **`bash -n`（語法檢查）和 `set -u`（未定義變數）是兩個快速抓 bug 的工具**。`bash -n script.sh` **不執行**腳本，只檢查語法——抓出漏掉的 `fi`/`done`、不配對的引號、case 漏 `;;` 等結構錯誤。這是修改大腳本後的快速檢查（確認沒打破語法）。`set -u`（Ch 35）抓**拼錯的變數名**——你把 `$filename` 打成 `$filenmae`，預設當空字串（沈默），`-u` 會報 "unbound variable" 指出來。傳統的 `echo "DEBUG: ..." >&2`（印中間狀態到 stderr）雖然土法，但對「這個變數此刻是什麼值」這類問題又快又直接（記得送 stderr，Ch 19/35，別污染輸出）。`set -v`（印原始行，展開前）和 `set -x`（印展開後）互補——`set -xv` 一起看「原始程式碼 → 展開結果」的對照。這些工具配合 shellcheck（下節），覆蓋 debug 的各個面向。

## shellcheck:shell 的「編譯器」

shellcheck 是改變遊戲規則的工具——它靜態分析你的腳本，抓出本課教的**所有**陷阱：

```bash
# 安裝與使用
sudo apt install shellcheck    # Debian/Ubuntu
shellcheck script.sh           # 分析腳本，列出所有問題

# shellcheck 抓出本課教的陷阱範例
cat > buggy.sh <<'EOF'
#!/bin/bash
file=$1
rm $file                       # 沒加引號（Ch 32）
for f in $(ls *.txt); do       # parse ls + 命令替換切詞（Ch 32/34）
    echo $f                     # 沒加引號
done
if [ $count = 5 ]; then         # 空變數可能崩 + 數值用 = （Ch 34）
    echo "five"
fi
EOF

shellcheck buggy.sh
# In buggy.sh line 3:
# rm $file
#    ^-- SC2086: Double quote to prevent globbing and word splitting.
#
# In buggy.sh line 4:
# for f in $(ls *.txt); do
#          ^-- SC2045: Iterating over ls output is fragile. Use globs.
# ...
# 每個問題有「SC編號」，能查詳細解釋
```

```
shellcheck 抓的常見問題（都是本課教過的！）：

  SC2086：變數沒加引號（Ch 32 的頭號 bug）
  SC2045：parse ls 輸出（Ch 34，用 glob 代替）
  SC2046：命令替換沒加引號會切詞
  SC2164：cd 沒檢查失敗（Ch 35，cd || exit）
  SC2154：變數沒定義就使用（可能 typo）
  SC2155：local x=$(cmd) 掩蓋了退出碼
  SC2006：用了反引號（Ch 32，改 $()）
        │
  每個 SCxxxx 在 shellcheck wiki 有詳細解釋：
  https://www.shellcheck.net/wiki/SC2086
        │
  → shellcheck 把本課的「踩雷集錦」自動化
    它讀過無數 bug，知道所有常見陷阱
```

```bash
# 整合進工作流
shellcheck *.sh                # 檢查所有腳本
shellcheck -S error script.sh  # 只看 error 級（忽略 style 建議）

# 在編輯器整合（VS Code、vim 等有 shellcheck 外掛 → 即時標出問題）

# 在 CI 整合（每次 commit 自動檢查）
# .github/workflows: shellcheck **/*.sh

# 抑制特定警告（確定那是 false positive 時）
# shellcheck disable=SC2086
echo $intentionally_unquoted   # 上一行的註解讓 shellcheck 跳過這行

# 線上版（不用安裝）：https://www.shellcheck.net
```

> **shellcheck 是 shell scripting 最重要的工具，沒有之一——它把本課的「踩雷集錦」全部自動化**。shell 沒有編譯器幫你抓錯（Ch 35：預設寬容、沈默失敗），shellcheck 填補了這個空缺——它靜態分析你的腳本，標出本課反覆強調的每個陷阱：沒加引號的變數（SC2086，Ch 32）、parse ls（SC2045，Ch 34）、cd 沒檢查（SC2164，Ch 35）、反引號（SC2006，Ch 32）、可能未定義的變數（SC2154）……每個問題有 `SCxxxx` 編號，連到 [shellcheck.net/wiki](https://www.shellcheck.net/wiki) 的詳細解釋（為什麼是問題、怎麼修）。**最佳實踐：每個腳本都過 shellcheck，整合進編輯器（即時標示）和 CI（commit 時自動檢查）**。它讀過無數真實 bug，知道的陷阱比任何人記得的都多。把 shellcheck 當「shell 的編譯器警告」——它的每個提示都值得認真對待（極少 false positive，真的是 false positive 時用 `# shellcheck disable=SCxxxx` 註解抑制）。學會它，你的腳本品質立刻上一個台階。

## 系統化 debug 流程

遇到腳本 bug 時的系統化流程：

```
腳本出錯時的 debug 流程：

  1. shellcheck 先過一遍
     → 抓出 quoting、未定義變數等靜態問題（最快）
        │
  2. bash -n 檢查語法
     → 確認沒有結構性語法錯誤
        │
  3. set -euo pipefail（如果還沒加）
     → 讓沈默的錯誤浮現（Ch 35）
        │
  4. bash -x 追蹤執行
     → 看實際的變數值、走的分支、迴圈次數
     → 對照「預期 vs 實際」，找出分歧點
        │
  5. 縮小範圍
     → 在可疑段落前後加 echo "DEBUG: ..." >&2
     → 或 set -x / set +x 只追蹤那一段
        │
  6. 最小重現
     → 把 bug 抽成最小的測試腳本，獨立重現
```

```bash
# 一個完整的 debug 範例
# 症狀：腳本應該處理所有 .txt 但漏了某些

# Step 1: shellcheck
shellcheck process.sh         # 發現 SC2045（parse ls）→ 可能就是原因

# Step 2-3: 加 strict mode
# 在腳本開頭加 set -euo pipefail

# Step 4: 追蹤
bash -x process.sh 2>&1 | head -50
# + for f in 'file' 'one.txt' 'file' 'two.txt'   ← 看到！檔名 "file one.txt" 被切碎了
# → 確認是 quoting/parse ls 問題（Ch 32/34）

# Step 5: 修正
# for f in $(ls *.txt)  →  for f in *.txt
# echo $f  →  echo "$f"

# Step 6: 驗證
shellcheck process.sh         # 沒有警告了
bash -x process.sh            # 追蹤確認檔名完整了
```

> **系統化 debug 流程：shellcheck → bash -n → set -euo pipefail → bash -x → 縮小範圍 → 最小重現**。不要亂槍打鳥地改腳本——按順序來。先 **shellcheck**（最快，抓靜態問題，很多 bug 這裡就現形）；**bash -n** 確認語法；加 **set -euo pipefail**（Ch 35）讓沈默錯誤浮現；**bash -x** 追蹤看「實際 vs 預期」的分歧點；在可疑段落加 `echo DEBUG >&2` 或局部 `set -x` **縮小範圍**；最後把 bug 抽成**最小重現**腳本（獨立、最簡，往往抽的過程就找到原因了）。這個流程的威力在於它從「最快、最廣」（shellcheck）逐步深入到「最精確」（最小重現），不浪費時間。多數 shell bug 在 shellcheck + bash -x 兩步就解決——它們攤開了「程式碼的潛在問題」和「執行的實際行為」這兩個 debug 最需要的資訊。

## 故意弄壞:用工具抓出 bug

```bash
cd ~/cmdlab
# 寫一個有多個本課陷阱的腳本，用工具一一抓出
cat > buggy.sh <<'EOF'
#!/bin/bash
# 故意有多個 bug

dir=$1
cd $dir                        # bug: 沒引號 + cd 沒檢查
files=`ls`                     # bug: 反引號 + parse ls
for f in $files; do            # bug: 切詞
    if [ $f = "important.txt" ]; then    # bug: 沒引號 + 空變數
        cp $f /backup           # bug: 沒引號
    fi
done
EOF

# 用 shellcheck 抓
shellcheck buggy.sh
# SC2086 (×4): 沒加引號
# SC2006: 反引號改 $()
# SC2045: parse ls
# SC2164: cd 沒檢查失敗
# → 一次列出所有問題！

# 修正後的版本
cat > fixed.sh <<'EOF'
#!/bin/bash
set -euo pipefail

dir="$1"
cd "$dir" || exit 1            # 引號 + 檢查 cd
for f in *; do                 # glob 代替 parse ls
    if [[ "$f" == "important.txt" ]]; then    # [[ ]] + 引號
        cp "$f" /backup/        # 引號
    fi
done
EOF
shellcheck fixed.sh            # 沒有警告了（乾淨）

rm -f buggy.sh fixed.sh
```

> 這個練習展示 shellcheck 的威力——一個塞滿本課陷阱的腳本（沒引號 ×4、反引號、parse ls、cd 沒檢查），shellcheck **一次全部列出**，每個都連到詳細解釋。對照修正版（`set -euo pipefail` + 引號 + `[[ ]]` + glob + cd 檢查），shellcheck 變乾淨。這就是為什麼 shellcheck 是必備工具——它把你寫腳本時可能犯的所有錯誤，在執行前就攤在你面前。把每個腳本都跑過 shellcheck，是 Part 8 所有知識的「自動驗收」。

## 動手練習

1. set -x 追蹤:對一個有迴圈和變數的腳本跑 `bash -x`，看變數展開的真實值、迴圈實際跑幾次

2. PS4 加行號:設 `export PS4='+ ${LINENO}: '` 再 `bash -x`，看追蹤輸出帶行號

3. shellcheck 全套:寫一個塞滿陷阱的腳本（或用「故意弄壞」的 buggy.sh），跑 shellcheck，把每個 SCxxxx 查 wiki 理解

4. 修到乾淨:把有問題的腳本一個個按 shellcheck 提示修正，直到 shellcheck 無警告

5. 系統化流程:拿一個你寫過/找到的腳本，走完整流程（shellcheck → bash -n → set -euo → bash -x），體會每步抓到什麼

## 本章重點整理

- debug = 讓沈默的腳本「說出」它在做什麼；動態（set -x 看實際行為）+ 靜態（shellcheck 看潛在問題）互補
- `set -x`（執行追蹤）印每個展開後的命令——看變數真實值、走的分支、迴圈次數；`set +x` 關閉；`bash -x` 不改腳本追蹤；PS4 加行號
- `bash -n`（語法檢查不執行）、`set -u`（抓 typo 變數名）、`echo DEBUG >&2`（看中間狀態）
- shellcheck 是 shell 的「編譯器」——自動抓本課所有陷阱（SC2086 沒引號、SC2045 parse ls、SC2164 cd 沒檢查…），每個腳本都該過
- 系統化流程：shellcheck → bash -n → set -euo pipefail → bash -x → 縮小範圍 → 最小重現

## 自我檢核

- [ ] 會用 `set -x` / `bash -x` 追蹤腳本執行，讀懂追蹤輸出
- [ ] 知道 `bash -n`（語法）和 `set -u`（未定義變數）各抓什麼
- [ ] 會用 shellcheck，知道它能抓本課教的哪些陷阱
- [ ] 養成「每個腳本過 shellcheck」的習慣
- [ ] 遇到 bug 能走系統化 debug 流程，不亂槍打鳥

## 延伸閱讀

### 必備工具

- **[ShellCheck](https://www.shellcheck.net/)** — Vidar Holen（開源）
  - **讀哪裡**：線上版貼腳本即時檢查；[wiki](https://www.shellcheck.net/wiki/) 查每個 SCxxxx 的詳細解釋
  - **為什麼值得讀**：本章的主角；每個 SC 警告的 wiki 頁面是學 shell 陷阱的最佳教材（解釋為什麼是問題 + 怎麼修）
  - **整合**：裝編輯器外掛（VS Code/vim）即時檢查，整合 CI 自動把關

### 官方文件

- **[Bash manual — The Set Builtin (-x, -v, -n)](https://www.gnu.org/software/bash/manual/bash.html#The-Set-Builtin)** — GNU
  - **讀哪裡**：set 的 -x/-v/-n 選項、PS4 變數
  - **為什麼值得讀**：執行追蹤和語法檢查選項的權威定義

### 文章

- **[Debugging Bash scripts](https://www.shell-tips.com/bash/debug-script/)** — shell-tips
  - **這篇說什麼**：系統整理 bash debug 的所有技巧（set -x、PS4、trap DEBUG、bashdb）
  - **讀哪裡**：set -x 和 PS4 那幾節
  - **為什麼值得讀**：本章 debug 技巧的擴充，含 trap DEBUG 等進階手法

- **[How "Exit Traps" Can Make Your Bash Scripts Way More Robust](https://redsymbol.net/articles/bash-exit-traps/)** — Aaron Maxwell
  - **這篇說什麼**：trap EXIT 做清理的深入應用（呼應 Ch 35）
  - **為什麼值得讀**：把 debug 和健壯性連起來，為練習 D 鋪路

→ [練習 D：robust 備份腳本](./practice-d-backup-script.md)
