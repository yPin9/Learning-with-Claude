# 練習 D — 備份腳本

> 目標：把 Part 5（Ch 20–26）的 shell scripting 技巧整合起來，寫一個有完整錯誤處理、參數解析、日誌記錄的備份腳本。

## 任務規格

寫一個名為 `backup.sh` 的腳本，符合以下規格：

### 功能需求

1. **接受命令列 flag**：
   - `-s <dir>` — 來源目錄（必填）
   - `-d <dir>` — 備份目的地目錄（必填）
   - `-r <days>` — 保留最近幾天的備份，刪除舊的（預設 7）
   - `-v` — verbose 模式，輸出更多資訊
   - `-h` — 顯示使用說明

2. **備份格式**：壓縮成 `backup_YYYY-MM-DD_HHMMSS.tar.gz`

3. **日誌記錄**：每次執行都寫入 `<dest>/backup.log`

4. **完整錯誤處理**：
   - 來源目錄不存在 → 印錯誤並退出（exit code 1）
   - 目的地目錄不存在 → 自動建立
   - tar 失敗 → 清理不完整的備份檔，退出
   - 保留 N 天後，刪除舊備份並記錄

5. **清理**：`trap` 確保中途失敗不留下不完整的備份

### 期望輸出

```
$ ./backup.sh -s /etc -d /tmp/backups -r 3 -v
[2024-01-15 09:00:01] Starting backup: /etc → /tmp/backups
[2024-01-15 09:00:01] Created backup directory: /tmp/backups
[2024-01-15 09:00:01] Creating backup...
[2024-01-15 09:00:02] Backup created: backup_2024-01-15_090001.tar.gz (234K)
[2024-01-15 09:00:02] Removing old backups (keeping last 3 days)...
[2024-01-15 09:00:02] Removed: backup_2024-01-10_120000.tar.gz
[2024-01-15 09:00:02] Done. Log: /tmp/backups/backup.log
```

## 實作步驟建議

### Step 1：骨架

```bash
#!/usr/bin/env bash
set -euo pipefail

# 常量和預設值
readonly SCRIPT_NAME="$(basename "$0")"
source_dir=""
dest_dir=""
keep_days=7
verbose=false

# 工具函式
log()  { ... }
die()  { ... }
usage() { ... }

# 解析 flag（getopts）
...

# 驗證參數
...

# 主邏輯
main() {
    # 1. 確保目的地目錄存在
    # 2. 建立備份
    # 3. 清理舊備份
    # 4. 記錄日誌
}

main "$@"
```

### Step 2：日誌函式

```bash
LOG_FILE=""   # 在 main 裡設定

log() {
    local ts="[$(date '+%Y-%m-%d %H:%M:%S')]"
    echo "$ts $*"
    [[ -n "$LOG_FILE" ]] && echo "$ts $*" >> "$LOG_FILE"
}
```

### Step 3：tar + trap

```bash
backup_file=""

cleanup() {
    local code=$?
    if [[ -n "$backup_file" && -f "$backup_file" && $code -ne 0 ]]; then
        log "Removing incomplete backup: $backup_file"
        rm -f "$backup_file"
    fi
}
trap cleanup EXIT
```

### Step 4：刪除舊備份

```bash
prune_old_backups() {
    local dest="$1"
    local keep="$2"
    # find 找 mtime > keep_days 的備份檔
    find "$dest" -name "backup_*.tar.gz" -mtime "+$keep" | while read -r old; do
        log "Removing old backup: $(basename "$old")"
        rm -f "$old"
    done
}
```

## 完整參考解答

**自己先寫，寫不出來再看！**

<details>
<summary>點開參考解答</summary>

```bash
#!/usr/bin/env bash
set -euo pipefail

readonly SCRIPT_NAME="$(basename "$0")"

# 預設值
source_dir=""
dest_dir=""
keep_days=7
verbose=false
LOG_FILE=""
backup_file=""

# 工具函式
log() {
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $*"
    echo "$msg"
    [[ -n "$LOG_FILE" ]] && echo "$msg" >> "$LOG_FILE"
}

vlog() {
    $verbose && log "$@" || true
}

die() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $*" >&2
    exit 1
}

usage() {
    cat <<EOF
Usage: $SCRIPT_NAME -s <source> -d <dest> [OPTIONS]

OPTIONS:
  -s DIR    Source directory to backup (required)
  -d DIR    Destination directory (required)
  -r DAYS   Keep backups for DAYS days (default: 7)
  -v        Verbose output
  -h        Show this help

EXAMPLE:
  $SCRIPT_NAME -s /etc -d /backups -r 14 -v
EOF
}

cleanup() {
    local code=$?
    if [[ -n "$backup_file" && -f "$backup_file" && $code -ne 0 ]]; then
        echo "Removing incomplete backup: $backup_file" >&2
        rm -f "$backup_file"
    fi
}
trap cleanup EXIT

# 解析參數
while getopts ":s:d:r:vh" opt; do
    case "$opt" in
        s) source_dir="$OPTARG" ;;
        d) dest_dir="$OPTARG" ;;
        r) keep_days="$OPTARG" ;;
        v) verbose=true ;;
        h) usage; exit 0 ;;
        :) die "-$OPTARG requires an argument" ;;
        \?) die "Unknown option: -$OPTARG" ;;
    esac
done
shift $((OPTIND - 1))

# 驗證
[[ -z "$source_dir" ]] && die "-s <source> is required"
[[ -z "$dest_dir" ]]   && die "-d <dest> is required"
[[ -d "$source_dir" ]] || die "Source '$source_dir' is not a directory"
[[ "$keep_days" =~ ^[0-9]+$ ]] || die "-r must be a positive integer"

main() {
    # 建立目的地目錄
    if [[ ! -d "$dest_dir" ]]; then
        mkdir -p "$dest_dir"
        log "Created backup directory: $dest_dir"
    fi

    LOG_FILE="$dest_dir/backup.log"

    log "Starting backup: $source_dir → $dest_dir"

    # 建立備份檔名
    local ts; ts=$(date '+%Y-%m-%d_%H%M%S')
    backup_file="$dest_dir/backup_${ts}.tar.gz"

    vlog "Creating backup..."
    tar -czf "$backup_file" -C "$(dirname "$source_dir")" "$(basename "$source_dir")"

    local size; size=$(du -sh "$backup_file" | cut -f1)
    log "Backup created: $(basename "$backup_file") ($size)"

    # 清理舊備份
    vlog "Removing old backups (keeping last $keep_days days)..."
    local removed=0
    while IFS= read -r old; do
        [[ -n "$old" ]] || continue
        log "Removed: $(basename "$old")"
        rm -f "$old"
        ((removed++))
    done < <(find "$dest_dir" -name "backup_*.tar.gz" -mtime "+${keep_days}" 2>/dev/null)

    (( removed > 0 )) && vlog "Removed $removed old backup(s)"

    log "Done. Log: $LOG_FILE"
}

main
```

</details>

## 測試用例

```bash
# 1. 正常備份
./backup.sh -s /etc -d /tmp/my-backups -v

# 2. 來源不存在應該報錯
./backup.sh -s /nonexistent -d /tmp/my-backups
echo "Exit code: $?"   # 應該是 1

# 3. 目的地自動建立
rm -rf /tmp/new-dest
./backup.sh -s /tmp -d /tmp/new-dest -v
ls /tmp/new-dest

# 4. 保留天數測試（建立舊檔案模擬）
mkdir -p /tmp/test-backups
touch -d "10 days ago" /tmp/test-backups/backup_2024-01-01_120000.tar.gz
touch -d "1 day ago"   /tmp/test-backups/backup_2024-01-14_120000.tar.gz
./backup.sh -s /tmp -d /tmp/test-backups -r 5 -v
ls /tmp/test-backups/   # 舊的應該被刪了
```

## 自我檢核

- [ ] 能用 `getopts` 解析多個 flag，包含有參數和無參數的
- [ ] 能用 `trap cleanup EXIT` 確保失敗時清理不完整的產出
- [ ] 能同時輸出到 stdout 和 log 檔案
- [ ] 能用 `find -mtime +N` 找出超過 N 天的舊檔案

→ [Ch 27 磁碟與儲存](./27-disk-and-storage.md)
