# Ch 35 — 錯誤處理與 trap

> **目標**：掌握讓 shell 腳本「健壯」（robust）的核心技術——`set -euo pipefail`（為什麼每個正經腳本都該有）、退出碼的設計、`trap` 捕捉信號做清理（呼應 Ch 17 signal）、以及錯誤處理的常見模式。預設的 shell 行為對錯誤極度寬容（出錯了還繼續跑），這章教你如何讓腳本在出錯時正確地停止和清理。

> **環境**：bash 5.x。`pipefail` 是 bash 特性，POSIX sh 差異會標注。

## 為什麼錯誤處理是 scripting 的分水嶺？

shell 預設對錯誤**極度寬容**——一個命令失敗了，腳本**繼續往下跑**，當作沒事。這是災難的根源：`cd /backup/$DIR`（目錄不存在，cd 失敗）後面接 `rm -rf *`（在錯誤的當前目錄刪光東西）。預設行為下，cd 失敗不會停腳本，rm 照跑——刪錯地方。

健壯的腳本必須**主動處理錯誤**：命令失敗就停（別繼續造成更大破壞）、用未定義變數就停（Ch 32 的 rm 空變數災難）、被中斷時清理暫存檔。這些靠 `set -euo pipefail` 和 `trap`。這章是「能跑的腳本」和「能在生產環境信任的腳本」之間的分水嶺——也是練習 D 和 Final Project 的關鍵。

## 先建立直覺:預設的 shell 太寬容

```
shell 預設行為：出錯了「假裝沒事」繼續跑

  腳本：
    cd /nonexistent      ← 失敗！（目錄不存在）
    rm -rf *             ← 但腳本繼續跑，在「原來的目錄」刪光！
        │
  預設下：
    - 命令失敗（非 0 退出碼）→ 腳本「不停」，繼續下一行
    - 用未定義變數 → 當成空字串，「不報錯」
    - 管線中間失敗 → 只看最後一個命令的退出碼
        │
  這對「互動式」合理（你打錯一個命令不該關掉 terminal）
  但對「腳本」是災難（自動跑時沒人盯著，錯誤累積成破壞）
        │
  set -euo pipefail 改變這些：
    -e  命令失敗就「立刻停止整個腳本」
    -u  用未定義變數就「報錯停止」
    -o pipefail  管線中「任一」失敗就算失敗
        │
  → 從「寬容地繼續」改成「出錯就停」（fail-fast）
```

關鍵心智：shell 預設對錯誤寬容——命令失敗了腳本繼續跑、用未定義變數當空字串、管線只看最後一個的退出碼。這對互動式合理（打錯不該關 terminal），但對腳本是災難（自動跑時錯誤累積成破壞）。`set -euo pipefail` 把它改成「fail-fast」——出錯就立刻停止，防止錯誤擴大。

> 錯誤處理建立在 Ch 33 的退出碼（`$?`）和 Ch 17 的 signal 上。trap 捕捉的就是 Ch 17 講的信號（SIGINT/SIGTERM）。如果對 signal 不熟，回看 [Ch 17 — signal](./17-signals.md)。

## set -euo pipefail:健壯腳本的開頭

這三個（其實四個）選項是幾乎每個正經腳本的標準開頭：

```bash
#!/bin/bash
set -euo pipefail            # 放在腳本開頭！
IFS=$'\n\t'                  # （進階）改 IFS 減少 word splitting 意外

# 逐一理解每個選項：

# -e (errexit)：任何命令失敗（非 0 退出碼）就立刻退出腳本
set -e
false                        # 這個命令失敗 → 腳本「立刻結束」（不會跑下一行）
echo "this never runs"

# -u (nounset)：用未定義變數就報錯退出
set -u
echo "$UNDEFINED_VAR"        # 報錯：UNDEFINED_VAR: unbound variable → 退出
# 防止 Ch 32 的「rm -rf $undefined/」災難！

# -o pipefail：管線中任一命令失敗，整個管線算失敗
set -o pipefail
false | true                 # 預設：退出碼 0（只看 true）；pipefail：退出碼 1（false 失敗）
grep pattern file | sort     # 若 grep 失敗（沒裝/檔案不存在），pipefail 才抓得到
```

```
set -euo pipefail 各選項的作用：

  -e (errexit)：
    命令失敗 → 立刻停止腳本
    防止「cd 失敗後繼續 rm」這類連鎖災難
        │
  -u (nounset)：
    用未定義變數 → 報錯停止
    防止「rm -rf $TYPO/」（拼錯變數名變成 rm -rf /）
        │
  -o pipefail：
    管線任一 stage 失敗 → 管線算失敗（配 -e 就會停）
    預設管線只看最後一個，會「漏掉」中間的失敗
        │
  → 三個一起 = 「出錯就停、不容忍未定義、管線失敗不放過」
    是生產級腳本的最低標準
```

> **`set -euo pipefail` 是健壯 shell 腳本的「安全帶」，幾乎每個正經腳本都該有**。**`-e`**（命令失敗就停）防止「cd 失敗後繼續執行危險命令」的連鎖災難。**`-u`**（未定義變數報錯）防止 Ch 32 的 `rm -rf $TYPO/`——拼錯變數名時，預設當空字串變成 `rm -rf /`，但 `-u` 會在用未定義變數時就停止。**`-o pipefail`**（管線任一失敗算失敗）——預設管線只看**最後一個**命令的退出碼，所以 `grep x file | sort` 中 grep 失敗（檔案不存在）會被 sort 的成功掩蓋，pipefail 讓你抓得到。三者合起來把腳本從「寬容地累積錯誤」變成「出錯立刻停」（fail-fast），這是生產環境信任一個腳本的最低標準。注意 `-e` 有些**例外和陷阱**（後述），不是萬靈丹，但作為預設防護網價值極高。

## -e 的陷阱:它不是萬靈丹

`set -e` 行為微妙，有些情況它**不會**觸發，知道這些才能正確用：

```bash
set -e

# 陷阱 1：在 if/while/&&/|| 條件裡的失敗「不算」（合理，但要知道）
if false; then echo "a"; fi   # false 失敗，但在 if 條件裡 → 不觸發 -e（正常）
false || true                 # false 失敗，但有 || → 不觸發（正常）
false && echo "x"             # 同理不觸發

# 陷阱 2：命令在管線「中間」（沒 pipefail 時）
false | true                  # 沒 pipefail：不觸發 -e（只看 true）

# 陷阱 3：函式裡的 -e 行為複雜
myfunc() {
    false                     # 在某些情境下不會讓函式立刻返回
    echo "still runs?"
}

# 陷阱 4：命令替換的失敗
output=$(false)               # 賦值的命令替換失敗 → 行為依版本/情境

# 陷阱 5：你「預期會失敗」的命令
grep "pattern" file           # 沒找到 grep 回非 0 → -e 會誤殺腳本！
# 解法：明確處理預期的「失敗」
grep "pattern" file || true   # 容許 grep 找不到（|| true 吸收失敗）
if grep -q "pattern" file; then ...; fi   # 或用 if 判斷

# 顯式錯誤處理（不全靠 -e）
command || { echo "command failed" >&2; exit 1; }
```

> **`set -e` 有微妙的例外——它不是「任何失敗都停」，過度依賴它會被反咬**。`-e` **不觸發**的情況：命令在 `if`/`while` 條件裡、在 `&&`/`||` 的左邊、在管線中間（沒 pipefail 時）、函式的某些情境。更危險的是**誤殺**：`grep "x" file`（沒找到時回非 0）會被 `-e` 當成「失敗」而停止腳本——但「沒找到」可能是正常的。解法：對「預期可能失敗」的命令用 `|| true`（吸收失敗）或 `if grep -q ...; then`（明確判斷）。所以最佳實踐是 **`set -e` 當預設防護網 + 對關鍵命令顯式處理錯誤**（`command || { echo "failed" >&2; exit 1; }`），不要以為加了 `-e` 就萬事大吉。理解 `-e` 的例外，你才不會寫出「明明 -e 了卻沒停」或「正常的 grep 把腳本搞死」的腳本。

## 退出碼設計

你的腳本也該回傳有意義的退出碼，讓呼叫它的人能判斷：

```bash
# 退出碼慣例
exit 0                        # 成功
exit 1                        # 一般錯誤
exit 2                        # 誤用（如參數錯誤，慣例）
# 64-78：BSD sysexits.h 定義的標準碼（EX_USAGE=64 等）
# 126：命令找到但不可執行
# 127：命令找不到（command not found）
# 128+N：被信號 N 殺死（如 130 = 128+2 = SIGINT/Ctrl-C，Ch 17）

# 在腳本裡設計退出碼
main() {
    if [[ $# -lt 1 ]]; then
        echo "Usage: $0 <file>" >&2
        exit 2                # 誤用
    fi
    if [[ ! -f "$1" ]]; then
        echo "Error: file not found: $1" >&2
        exit 1                # 一般錯誤
    fi
    # ... 正常處理 ...
    exit 0                    # 成功
}

# 錯誤訊息給 stderr（Ch 19）！
echo "Error: something failed" >&2    # 對：錯誤到 stderr
echo "Error: something failed"        # 錯：錯誤混進 stdout（污染輸出）

# 一個錯誤處理的 helper 函式（常見模式）
die() {
    echo "Error: $*" >&2      # 訊息到 stderr
    exit 1
}
[[ -f "$config" ]] || die "config not found: $config"
```

> **錯誤訊息要送 stderr（`>&2`），退出碼要有意義——這讓你的腳本能被其他程式正確使用**。錯誤訊息混進 stdout 是常見錯誤：如果你的腳本輸出資料（給管線下游用），錯誤訊息混進去會污染資料。**鐵律：錯誤、警告、診斷訊息一律 `>&2`**（送 stderr，Ch 19），stdout 只放「正常的、要給下游的輸出」。退出碼也要有意義——`exit 0`（成功）、`exit 1`（一般錯誤）、`exit 2`（參數誤用，慣例），讓 `if myscript.sh; then`（呼叫者用退出碼判斷）能正確運作。常見模式是定義 `die()` 函式（印錯誤到 stderr + exit 1），配合 `|| die "message"` 簡潔地處理錯誤。記住特殊退出碼：`127`（command not found）、`126`（找到但不可執行）、`128+N`（被信號 N 殺死，如 `130`=Ctrl-C）——debug 時這些碼告訴你失敗類型。

## trap:捕捉信號做清理

trap 讓腳本在收到信號（Ch 17）或退出時執行清理——這是「腳本被中斷也不留爛攤子」的關鍵：

```bash
# trap '動作' 信號：收到信號時執行動作
trap 'echo "interrupted!"; exit 130' INT    # Ctrl-C（SIGINT，Ch 17）

# 最有用：EXIT 偽信號（腳本「以任何方式」退出時都執行 → 清理！）
tmpfile=$(mktemp)             # 建暫存檔
trap 'rm -f "$tmpfile"' EXIT  # 不管腳本怎麼結束（正常/錯誤/被殺），都刪暫存檔
# ... 用 tmpfile 做事 ...
# 腳本結束時，trap 自動 rm 暫存檔（不用每個 exit 點都記得刪）

# 多個信號
cleanup() {
    echo "Cleaning up..." >&2
    rm -f "$tmpfile"
    rm -rf "$tmpdir"
    # kill 背景 job 等
}
trap cleanup EXIT INT TERM    # 退出、Ctrl-C、kill 時都清理

# 完整的健壯腳本骨架
#!/bin/bash
set -euo pipefail

tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT  # 保證清理（呼應練習 B/C 的 trap）

main() {
    # ... 用 tmpdir 工作 ...
    echo "working in $tmpdir"
}
main "$@"

# trap 移除 / 查看
trap -p                       # 列出當前所有 trap
trap - INT                    # 移除 INT 的 trap（恢復預設）
```

```
trap 最重要的用途：EXIT 偽信號 + 清理

  問題：腳本建了暫存檔/暫存目錄，怎麼保證「一定」清理？
    在每個 exit 點都 rm？ → 容易漏（尤其錯誤退出、被 Ctrl-C）
        │
  解法：trap 'cleanup' EXIT
    EXIT 是「偽信號」—— 腳本「以任何方式結束」都觸發：
      正常結束、exit、set -e 觸發的退出、被 SIGTERM 殺...
    → cleanup 保證執行一次（清理暫存檔）
        │
  → 「建立資源後立刻設 trap 清理」是健壯腳本的標準模式
    tmpfile=$(mktemp); trap 'rm -f "$tmpfile"' EXIT
```

> **`trap 'cleanup' EXIT` 是「保證清理」的關鍵模式——建立暫存資源後立刻設它**。腳本常建暫存檔/目錄（`mktemp`），問題是怎麼保證**一定**清理掉？在每個 `exit` 點手動 `rm` 容易漏——尤其錯誤退出（`set -e` 觸發）、被 Ctrl-C（SIGINT）、被 kill（SIGTERM）時。解法是 **`EXIT` 偽信號**——它在腳本「以任何方式結束」時都觸發（正常結束、exit、-e 退出、被信號殺），所以 `trap 'rm -rf "$tmpdir"' EXIT` 保證清理執行一次。標準模式：**建立資源後「立刻」設 trap**（`tmpdir=$(mktemp -d); trap 'rm -rf "$tmpdir"' EXIT`），這樣後面不管發生什麼，暫存目錄都會被清掉。這呼應練習 B/C 用 trap 處理 Ctrl-C。trap 捕捉的信號就是 Ch 17 的 SIGINT/SIGTERM——shell 層的 signal handler。配合 `set -euo pipefail`，trap 是健壯腳本的另一根支柱。

## 故意弄壞:沒有錯誤處理的災難

```bash
cd ~/cmdlab
# 演示為什麼需要 set -e（在安全的測試目錄）
mkdir error-test && cd error-test
mkdir realdir

# 沒有 set -e：cd 失敗後繼續，在錯的地方操作
cat > bad.sh <<'EOF'
#!/bin/bash
# 沒有 set -e！
cd /nonexistent-dir          # 失敗，但腳本繼續！
echo "current dir: $(pwd)"   # 還在原來的目錄（不是預期的）
touch DANGER.txt             # 在「錯誤的」目錄建檔（可能覆蓋重要東西）
EOF
chmod +x bad.sh
./bad.sh                     # cd 失敗訊息，但 touch 還是執行了
ls                           # DANGER.txt 出現在錯的地方！

# 有 set -e：cd 失敗就停
cat > good.sh <<'EOF'
#!/bin/bash
set -euo pipefail            # 防護網
cd /nonexistent-dir          # 失敗 → 立刻停止
echo "this never runs"       # 不會執行
touch SAFE.txt               # 不會執行
EOF
chmod +x good.sh
./good.sh                    # cd 失敗 → 腳本立刻停（沒建任何檔案）
ls                           # 沒有 SAFE.txt（腳本在 cd 失敗就停了）

# 演示 trap 清理
cat > cleanup.sh <<'EOF'
#!/bin/bash
set -euo pipefail
tmp=$(mktemp)
trap 'echo "cleaning $tmp"; rm -f "$tmp"' EXIT
echo "using temp file $tmp"
# 即使這裡 Ctrl-C 或出錯，trap 都會清理 tmp
sleep 2
EOF
chmod +x cleanup.sh
./cleanup.sh                 # 正常結束 → 看到 cleaning（暫存檔被清）
# 再跑一次，中途 Ctrl-C → 還是看到 cleaning（trap EXIT 保證清理）

cd ~/cmdlab && rm -rf error-test
```

> 這個對比親眼展示 `set -e` 的價值：`bad.sh`（沒 set -e）的 `cd` 失敗後，`touch` 還是在**錯誤的目錄**執行——這正是「cd /backup/$DIR 失敗後 rm -rf * 刪錯地方」災難的縮影。`good.sh`（有 set -euo pipefail）在 cd 失敗就**立刻停止**，沒造成任何破壞。`cleanup.sh` 展示 `trap ... EXIT` 保證暫存檔被清理（正常結束或 Ctrl-C 都清）。這些不是學術練習——是真實腳本在生產環境出錯時，「優雅停止 + 不留爛攤子」和「悄悄造成破壞」的差別。

## 動手練習

1. set -e 的價值：跑「故意弄壞」的 bad.sh vs good.sh，親眼看 cd 失敗後有無 set -e 的差別

2. set -u 防災：寫個用 `$undefined` 變數的腳本，加 set -u 前後對比（無 -u 當空字串，有 -u 報錯停止）

3. pipefail：`false | true; echo $?`（0）vs `set -o pipefail; false | true; echo $?`（1），理解管線失敗偵測

4. trap 清理：寫個建 mktemp 暫存檔 + `trap 'rm -f "$tmp"' EXIT` 的腳本，正常結束和 Ctrl-C 都驗證暫存檔被清

5. die 模式：寫個 `die()` helper（訊息到 stderr + exit 1），用 `[[ -f "$f" ]] || die "..."` 做檢查

## 本章重點整理

- shell 預設對錯誤寬容（失敗繼續跑、未定義變數當空、管線只看最後一個）——對腳本是災難
- `set -euo pipefail`：-e（失敗就停）、-u（未定義變數報錯，防 rm 空變數災難）、-o pipefail（管線任一失敗算失敗）
- -e 不是萬靈丹：if/&&/管線中間不觸發、會誤殺預期失敗的命令（grep 沒找到）；對關鍵命令仍要顯式處理
- 退出碼要有意義（0/1/2）、錯誤訊息送 stderr（>&2）；die() 是常見錯誤處理模式
- `trap 'cleanup' EXIT` 保證清理：EXIT 偽信號在腳本任何方式結束時都觸發；建資源後立刻設 trap

## 自我檢核

- [ ] 能解釋為什麼 shell 預設對錯誤寬容，以及這為什麼對腳本危險
- [ ] 知道 set -euo pipefail 每個選項做什麼，能說出各自防範的災難
- [ ] 知道 set -e 的陷阱（不觸發的情況、誤殺 grep），不會盲目依賴它
- [ ] 知道錯誤訊息要送 stderr，退出碼要有意義
- [ ] 會用 `trap 'cleanup' EXIT` 保證暫存資源被清理

## 延伸閱讀

### 必讀資源

- **[Use the Unofficial Bash Strict Mode](http://redsymbol.net/articles/unofficial-bash-strict-mode/)** — Aaron Maxwell
  - **這篇說什麼**：詳解 `set -euo pipefail` + `IFS=$'\n\t'`（所謂 bash 嚴格模式），每個選項的作用和陷阱
  - **讀哪裡**：整篇
  - **為什麼值得讀**：本章 set -euo pipefail 那節的權威來源，連 -e 的陷阱和 IFS 技巧都講透

- **[BashFAQ/105 — Why doesn't set -e do what I expected](https://mywiki.wooledge.org/BashFAQ/105)** — Greg's Wiki
  - **這篇說什麼**：set -e 的所有微妙例外和陷阱，為什麼有人說「別用 set -e」
  - **為什麼值得讀**：本章「-e 不是萬靈丹」的完整版，理解 -e 的爭議和正確用法

### 官方文件

- **[Bash manual — The Set Builtin](https://www.gnu.org/software/bash/manual/bash.html#The-Set-Builtin)** + **[Signals/trap](https://www.gnu.org/software/bash/manual/bash.html#Bourne-Shell-Builtins)** — GNU
  - **讀哪裡**：set 的 -e/-u/-o pipefail 定義、trap builtin
  - **為什麼值得讀**：這些選項和 trap 的權威定義；EXIT 偽信號的官方說明

### 文章

- **[Writing Robust Bash Shell Scripts](https://www.davidpashley.com/articles/writing-robust-shell-scripts/)** — David Pashley
  - **這篇說什麼**：寫健壯 shell 腳本的完整實踐（錯誤處理、trap、原子操作、鎖）
  - **為什麼值得讀**：把錯誤處理放進「整個健壯腳本」的脈絡，是練習 D 和 Final Project 的好參考

→ [Ch 36 debug 與 shellcheck](./36-debug-shellcheck.md)
