# Final Project — SysOps 腳本工具包

> 目標：把整門課的內容整合成一套可用的維運腳本，包含健康監控、日誌清理、備份、和警報通知。

## 專案規格

你要建立一個 `sysops-toolkit/` 資料夾，裡面有四個腳本：

```
sysops-toolkit/
├── lib.sh          # 共用函式庫
├── health.sh       # 系統健康報告
├── logclean.sh     # 日誌清理
├── backup.sh       # 目錄備份（從練習 D 演化）
└── README          # 使用說明
```

## 每個腳本的規格

### lib.sh — 共用函式庫

所有腳本都 `source` 這個檔案：

- `log INFO|WARN|ERROR <message>` — 有時間戳的帶等級日誌，寫到 stdout 和可選的 log 檔
- `die <message>` — 輸出錯誤到 stderr，exit 1
- `require_command <cmd>` — 確認命令存在，否則 die
- `require_root` — 確認 root 權限
- `human_size <bytes>` — 把 bytes 轉成 1.2K / 3.4M / 1.2G

### health.sh — 健康報告

用法：`./health.sh [-o <output.html>] [-t <threshold_percent>]`

輸出一份系統健康報告，包含：

1. **CPU**：load average，和 CPU 核數比較
2. **記憶體**：available 百分比，低於 threshold 標記警告
3. **磁碟**：所有分區使用率，高於 threshold 標記警告
4. **服務**：列出所有 failed 的 systemd 服務
5. **整體狀態**：OK / WARNING / CRITICAL

加 `-o` 輸出 HTML 格式（危險項目紅色）。

### logclean.sh — 日誌清理

用法：`./logclean.sh [-d <dir>] [-a <days>] [-s <max_size>] [-n]`

- `-d` — 要清理的目錄（預設 `/var/log`）
- `-a` — 刪除超過 N 天的 `.log` 和 `.log.gz`（預設 30）
- `-s` — 如果某個 log 超過 N MB 就截斷它（用 `> file` 清空），預設 500
- `-n` — dry-run，只列出會做什麼，不真的做

輸出每個操作：`Removed xxx.log (saved 23M)` 或 `Truncated xxx.log (was 1.2G)`

### backup.sh — 備份

這是練習 D 的進化版，新增：

- 支援多個來源目錄（`-s` 可以指定多次）
- 備份後驗證壓縮檔（`tar -tzf` 列一遍確認能讀）
- 支援 `-x <pattern>` 排除特定路徑
- 把每次備份的統計寫入 `<dest>/backup-history.csv`（日期,大小,耗時,狀態）

## 實作建議

### 從 lib.sh 開始

```bash
#!/usr/bin/env bash
# lib.sh — 共用函式庫
# source 這個檔案，不要直接執行

# 防止直接執行
[[ "${BASH_SOURCE[0]}" != "${0}" ]] || {
    echo "This file should be sourced, not executed." >&2
    exit 1
}

# log 等級
LOG_FILE=""    # 設定這個變數可以同時寫檔案

log() {
    local level="$1"; shift
    local ts; ts="$(date '+%Y-%m-%d %H:%M:%S')"
    local msg="[$ts] [$level] $*"
    case "$level" in
        ERROR) echo "$msg" >&2 ;;
        *)     echo "$msg" ;;
    esac
    [[ -n "$LOG_FILE" ]] && echo "$msg" >> "$LOG_FILE"
}

die() { log "ERROR" "$@"; exit 1; }

require_command() {
    command -v "$1" &>/dev/null || die "Required command not found: $1"
}

require_root() {
    [[ $EUID -eq 0 ]] || die "This script must be run as root"
}

human_size() {
    local bytes="$1"
    if   (( bytes >= 1073741824 )); then printf "%.1fG" "$(echo "scale=1; $bytes/1073741824" | bc)"
    elif (( bytes >= 1048576 ));    then printf "%.1fM" "$(echo "scale=1; $bytes/1048576" | bc)"
    elif (( bytes >= 1024 ));       then printf "%.1fK" "$(echo "scale=1; $bytes/1024" | bc)"
    else echo "${bytes}B"
    fi
}
```

### health.sh 的核心邏輯

```bash
check_disk() {
    local threshold="$1"
    local status="OK"
    while IFS= read -r line; do
        local use pct mount
        pct=$(echo "$line" | awk '{print $5}' | tr -d '%')
        mount=$(echo "$line" | awk '{print $6}')
        if (( pct >= threshold )); then
            log WARN "Disk $mount at ${pct}%"
            status="WARNING"
        fi
    done < <(df -h | grep '^/' | grep -v tmpfs)
    echo "$status"
}
```

## 完整參考實作

**先自己寫！** 這個 final project 沒有「對答案」，而是看你的實作是否符合規格且能跑。

<details>
<summary>lib.sh 完整實作</summary>

```bash
#!/usr/bin/env bash
# lib.sh

[[ "${BASH_SOURCE[0]}" != "${0}" ]] || {
    echo "Source this file, don't execute it." >&2
    exit 1
}

LOG_FILE=""

log() {
    local level="$1"; shift
    local ts; ts="$(date '+%Y-%m-%d %H:%M:%S')"
    local line="[$ts] [${level}] $*"
    case "$level" in
        ERROR|WARN) echo "$line" >&2 ;;
        *)          echo "$line" ;;
    esac
    [[ -n "$LOG_FILE" ]] && echo "$line" >> "$LOG_FILE"
}

die() { log "ERROR" "$@"; exit 1; }

require_command() {
    command -v "$1" &>/dev/null || die "Command not found: $1"
}

require_root() {
    [[ $EUID -eq 0 ]] || die "Must run as root"
}

human_size() {
    local b="$1"
    if   (( b >= 1073741824 )); then awk "BEGIN{printf \"%.1fG\", $b/1073741824}"
    elif (( b >= 1048576 ));    then awk "BEGIN{printf \"%.1fM\", $b/1048576}"
    elif (( b >= 1024 ));       then awk "BEGIN{printf \"%.1fK\", $b/1024}"
    else echo "${b}B"
    fi
}
```

</details>

<details>
<summary>health.sh 完整實作</summary>

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"

output_file=""
threshold=80
overall="OK"

mark_warn() { [[ "$overall" != "CRITICAL" ]] && overall="WARNING"; }
mark_crit() { overall="CRITICAL"; }

usage() {
    echo "Usage: $(basename "$0") [-o output.html] [-t threshold%]"
}

while getopts ":o:t:h" opt; do
    case "$opt" in
        o) output_file="$OPTARG" ;;
        t) threshold="$OPTARG" ;;
        h) usage; exit 0 ;;
        :) die "-$OPTARG requires an argument" ;;
        \?) die "Unknown: -$OPTARG" ;;
    esac
done

check_cpu() {
    local cores; cores=$(nproc)
    local load1; load1=$(uptime | awk -F'load average:' '{print $2}' | cut -d, -f1 | tr -d ' ')
    local load_int; load_int=${load1%.*}
    log "INFO" "CPU cores: $cores, Load(1m): $load1"
    (( load_int > cores )) && { log "WARN" "High load!"; mark_warn; }
}

check_memory() {
    local avail total pct
    avail=$(free -b | awk '/Mem:/{print $7}')
    total=$(free -b | awk '/Mem:/{print $2}')
    pct=$(awk "BEGIN{printf \"%d\", $avail*100/$total}")
    log "INFO" "Memory available: $(human_size $avail) ($pct%)"
    (( pct < 20 )) && { log "WARN" "Low memory!"; mark_warn; }
}

check_disk() {
    while IFS= read -r line; do
        local pct mp
        pct=$(echo "$line" | awk '{print $5}' | tr -d '%')
        mp=$(echo "$line" | awk '{print $6}')
        log "INFO" "Disk $mp: ${pct}% used"
        (( pct >= threshold )) && { log "WARN" "Disk $mp at ${pct}%"; mark_warn; }
        (( pct >= 95 ))        && mark_crit
    done < <(df -h | awk '/^\// && !/tmpfs/' )
}

check_services() {
    local failed
    failed=$(systemctl list-units --type=service --state=failed --no-legend 2>/dev/null | wc -l)
    if (( failed > 0 )); then
        log "WARN" "$failed failed service(s)"
        systemctl list-units --type=service --state=failed --no-legend 2>/dev/null | \
            awk '{print $1}' | while read -r svc; do log "WARN" "  FAILED: $svc"; done
        mark_warn
    else
        log "INFO" "All services OK"
    fi
}

main() {
    log "INFO" "=== Health Check: $(hostname) ==="
    check_cpu
    check_memory
    check_disk
    check_services
    log "INFO" "=== Overall: $overall ==="
    [[ "$overall" == "OK" ]] || exit 1
}

main
```

</details>

<details>
<summary>logclean.sh 完整實作</summary>

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"

log_dir="/var/log"
max_age=30
max_size_mb=500
dry_run=false
total_saved=0

while getopts ":d:a:s:nh" opt; do
    case "$opt" in
        d) log_dir="$OPTARG" ;;
        a) max_age="$OPTARG" ;;
        s) max_size_mb="$OPTARG" ;;
        n) dry_run=true ;;
        h) echo "Usage: $(basename "$0") [-d dir] [-a days] [-s maxMB] [-n]"; exit 0 ;;
        :) die "-$OPTARG requires an argument" ;;
        \?) die "Unknown: -$OPTARG" ;;
    esac
done

$dry_run && log "INFO" "DRY RUN mode — no changes will be made"

# 刪除舊檔案
while IFS= read -r f; do
    [[ -f "$f" ]] || continue
    local_size=$(stat -c%s "$f" 2>/dev/null || echo 0)
    if $dry_run; then
        log "INFO" "[DRY] Would remove: $f ($(human_size $local_size))"
    else
        rm -f "$f"
        log "INFO" "Removed: $f ($(human_size $local_size))"
        (( total_saved += local_size ))
    fi
done < <(find "$log_dir" \( -name "*.log" -o -name "*.log.gz" \) -mtime "+${max_age}" 2>/dev/null)

# 截斷大檔案
max_bytes=$(( max_size_mb * 1048576 ))
while IFS= read -r f; do
    [[ -f "$f" ]] || continue
    size=$(stat -c%s "$f" 2>/dev/null || echo 0)
    (( size > max_bytes )) || continue
    if $dry_run; then
        log "INFO" "[DRY] Would truncate: $f ($(human_size $size))"
    else
        > "$f"
        log "INFO" "Truncated: $f (was $(human_size $size))"
        (( total_saved += size ))
    fi
done < <(find "$log_dir" -name "*.log" -type f 2>/dev/null)

$dry_run || log "INFO" "Total space freed: $(human_size $total_saved)"
```

</details>

## 測試腳本

```bash
# 建立工具包目錄
mkdir -p ~/sysops-toolkit
cd ~/sysops-toolkit

# 測試 health.sh（先在沒有 root 的情況下測試）
./health.sh
echo "Exit code: $?"

# 測試 logclean.sh（dry-run 模式）
./logclean.sh -d /var/log -a 90 -n

# 測試 backup.sh
./backup.sh -s /etc -d /tmp/sysops-backups -v

# 整合測試：跑全套
echo "=== Health ===" && ./health.sh
echo "=== Logclean (dry) ===" && ./logclean.sh -d /var/log -n
echo "=== Backup ===" && ./backup.sh -s /etc -d /tmp/backups
```

## 驗收清單

完成後確認：

- [ ] 所有腳本都有 `set -euo pipefail`
- [ ] 所有腳本都 source `lib.sh` 並使用其中的函式
- [ ] 每個腳本的 `-h` 輸出清楚的使用說明
- [ ] `logclean.sh -n` 的 dry-run 不真的刪任何東西
- [ ] `backup.sh` 在 source 目錄不存在時正確退出（exit code 1）
- [ ] `health.sh` 在有問題時 exit code 非 0，一切正常時 exit code 0
- [ ] `trap cleanup EXIT` 確保 backup.sh 失敗時不留下不完整的 tar.gz
