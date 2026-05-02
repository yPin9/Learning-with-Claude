# Ch 26 — 錯誤處理

> 目標：理解 `set -euo pipefail` 的作用，掌握 `trap` 的清理模式，建立有一致 exit code 慣例的 script。

## 預設行為的問題

不加任何錯誤處理，bash script 會在出錯後**繼續跑**：

```bash
#!/bin/bash
cp /nonexistent /tmp/   # 失敗了
echo "still running!"   # 還是輸出這行
rm -rf "$dest_dir"      # dest_dir 是空的，等同 rm -rf /（！）
```

這不是假設，是真實事故的來源。

## set -e：出錯立刻退出

```bash
set -e    # 任何命令失敗就立刻退出 script
```

但 `set -e` 有幾個反直覺的規則：

```bash
set -e

false           # 這個會讓 script 退出
false || true   # 這個不會（| 後面成功了）
if false; then  # 這個也不會（if 語境裡的失敗不觸發 -e）
    echo "in if"
fi
```

## set -u：未定義變數視為錯誤

```bash
set -u    # 引用未設定的變數 → 立刻退出

echo $undefined_var   # 觸發退出，而不是印出空字串

# 但有例外：這個不觸發 -u
echo "${undefined_var:-default}"   # 使用預設值語法可以繞過
```

## set -o pipefail：Pipeline 失敗

```bash
set -o pipefail    # pipeline 裡任何一個命令失敗，整個 pipeline 的 exit code 就失敗

cat nonexistent.txt | grep "pattern" | wc -l
# 不加 pipefail：exit code = 0（wc 成功）
# 加了 pipefail：exit code = 1（cat 失敗）
```

## 組合使用

幾乎所有嚴肅的 script 都應該從這三行開始：

```bash
#!/usr/bin/env bash
set -euo pipefail
```

或更詳細的說明方式：

```bash
set -e   # -e: exit on error
set -u   # -u: treat unset variables as error
set -o pipefail  # propagate pipe failures
```

## trap：確保清理工作

```bash
cleanup() {
    local exit_code=$?
    echo "Cleaning up..." >&2
    rm -f "$tmpfile"
    exit "$exit_code"    # 保留原來的 exit code
}

trap cleanup EXIT        # 不管怎麼退出都執行 cleanup
trap cleanup INT TERM    # 也在收到訊號時執行

tmpfile=$(mktemp)
echo "Working with $tmpfile"
# ... 做事情 ...
# 無論如何，cleanup 會在結束時跑
```

`trap` 的常見訊號：

| 訊號 | 意義 |
|------|------|
| `EXIT` | Script 結束（任何方式）|
| `INT` | Ctrl+C |
| `TERM` | kill 送的 SIGTERM |
| `ERR` | 任何命令失敗（和 -e 搭配）|

## Exit Code 慣例

```
0     成功
1     一般錯誤
2     錯誤的使用方式（參數錯）
126   命令找到了但無法執行（權限不足）
127   命令找不到
128+N 被訊號 N 終止（128+9=137 = SIGKILL）
```

自訂 exit code（建議在 script 頂部定義）：

```bash
readonly EXIT_OK=0
readonly EXIT_ERR=1
readonly EXIT_USAGE=2
readonly EXIT_NOT_FOUND=3
readonly EXIT_PERMISSION=4

die() {
    echo "Error: $*" >&2
    exit "${EXIT_ERR}"
}
```

## 完整錯誤處理模板

```bash
#!/usr/bin/env bash
set -euo pipefail

# 常量
readonly SCRIPT_NAME="$(basename "$0")"
readonly TMPDIR_PREFIX="/tmp/${SCRIPT_NAME%.*}"

# 變數
tmpdir=""

# 工具函式
log()  { echo "[$(date '+%H:%M:%S')] $*"; }
warn() { echo "[$(date '+%H:%M:%S')] WARN: $*" >&2; }
die()  { echo "[$(date '+%H:%M:%S')] ERROR: $*" >&2; exit 1; }

cleanup() {
    local code=$?
    [[ -n "$tmpdir" ]] && rm -rf "$tmpdir"
    (( code != 0 )) && warn "Exited with code $code"
}
trap cleanup EXIT

usage() {
    cat <<EOF
Usage: $SCRIPT_NAME [OPTIONS] <input>
Options:
  -h  Show help
EOF
}

main() {
    # 建立工作目錄
    tmpdir=$(mktemp -d "${TMPDIR_PREFIX}-XXXXXX")
    log "Working in $tmpdir"

    # 參數檢查
    [[ $# -lt 1 ]] && die "missing input argument"
    [[ -f "$1" ]] || die "input file '$1' not found"

    local input="$1"
    log "Processing $input"
    # ... 實際邏輯 ...
}

main "$@"
```

## 動手練習

```bash
# 1. 觀察 set -e 的行為
bash << 'EOF'
set -e
echo "before"
false
echo "after"   # 這行不會執行
EOF
echo "Script exited with: $?"

# 2. pipefail 的差異
# 不加 pipefail
bash -c 'cat /nonexistent 2>/dev/null | wc -l; echo "exit: $?"'
# 加了 pipefail
bash -c 'set -o pipefail; cat /nonexistent 2>/dev/null | wc -l; echo "exit: $?"'

# 3. trap 清理練習
bash << 'EOF'
set -euo pipefail

tmpfile=$(mktemp)
trap "echo 'Cleaning $tmpfile'; rm -f $tmpfile" EXIT

echo "Working..."
ls "$tmpfile"
echo "Created temp file: $tmpfile"
# 不管有沒有 error，cleanup 都跑
EOF

# 4. 寫一個帶完整錯誤處理的 backup script（簡化版）
cat > /tmp/safe-copy.sh << 'EOF'
#!/usr/bin/env bash
set -euo pipefail

die() { echo "ERROR: $*" >&2; exit 1; }

[[ $# -ne 2 ]] && die "Usage: $0 <source> <dest>"

src="$1"
dst="$2"

[[ -e "$src" ]] || die "'$src' does not exist"
[[ -d "$(dirname "$dst")" ]] || die "Parent dir of '$dst' does not exist"

cp -v "$src" "$dst"
echo "Done."
EOF
chmod +x /tmp/safe-copy.sh
/tmp/safe-copy.sh /etc/hostname /tmp/hostname.bak
```

## 自我檢核

- [ ] 知道 `set -euo pipefail` 三個的作用，以及為什麼都需要
- [ ] 能用 `trap cleanup EXIT` 確保暫時檔案被清理
- [ ] 知道 exit code 0 = 成功，非 0 = 各種失敗
- [ ] 能寫 `die()` 函式統一錯誤輸出格式

→ [練習 D：備份腳本](./practice-d-backup-script.md)
