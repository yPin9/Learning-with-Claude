# Final Project — SysOps 腳本工具包

> **目標**：整合整門課（Ch 0–36，Part 1–8）的知識，打造一套**生產級的 SysOps 工具包**——一組互相配合的腳本，能巡檢系統健康、分析系統狀態、自動化日常運維。這個專案會用到檔案系統底層（Part 2）、process 觀測（Part 4）、管線與文字處理（Part 5-6）、系統管理（Part 7）、健壯 scripting（Part 8）。完成後你有一套能放進真實伺服器、敢讓它無人值守跑的工具——這是本課所有知識的終極驗收。

## 專案總覽

你要做一套叫 `sysops` 的工具包，包含多個子命令，每個對應一類運維任務：

```
sysops/
├── sysops              # 主入口（dispatch 到各子命令）
├── lib/
│   └── common.sh       # 共用函式庫（log/die/顏色/工具函式）
├── commands/
│   ├── health.sh       # 系統健康巡檢（CPU/記憶體/磁碟/負載）
│   ├── procwatch.sh    # process 監控（整合練習 B）
│   ├── diskhog.sh      # 找出佔空間的大檔案/目錄
│   ├── logwatch.sh     # log 分析與異常偵測（整合練習 C）
│   ├── netcheck.sh     # 連線與 port 檢查
│   └── cleanup.sh      # 安全清理（暫存檔/舊 log，整合練習 D 的健壯性）
└── README.md
```

```
使用方式：
  ./sysops health              # 系統健康總覽
  ./sysops procwatch nginx     # 監控 nginx
  ./sysops diskhog /var        # 找 /var 下的空間大戶
  ./sysops logwatch /var/log/nginx/access.log
  ./sysops netcheck            # 檢查網路連線和關鍵 port
  ./sysops cleanup --dry-run   # 模擬清理（不真的刪）
```

這個架構本身就是 Ch 21（Unix 哲學）和 Ch 34（函式/dispatch）的實踐——每個子命令「做一件事」，主入口組合它們，共用函式庫避免重複。

## 為什麼做這個專案？

這正是真實 SRE/SysOps 每天面對的：一台（或一群）伺服器，你要知道它健康嗎、誰在吃資源、log 有沒有異常、磁碟快滿了嗎、該清理什麼。商業工具（Datadog、Nagios、Prometheus）能做這些，但它們要架設、要錢、不總是手邊有。一個會寫腳本的工程師，能在任何一台裸 Linux 上，用這套自製工具立刻掌握系統狀態。

更重要的是，這個專案逼你把整門課**串起來**——不是孤立地用某個命令，而是把檔案系統知識、process 知識、文字處理、scripting 健壯性**組合**成解決真實問題的工具。完成它，你就從「會用 Linux 命令」進化到「能用 Linux 命令建構系統」。

## 整合的課程概念

| 子命令 / 元件 | 整合的章節 |
|---|---|
| 主 dispatch + 函式庫 | Ch 1（shell）、Ch 21（組合哲學）、Ch 34（函式）、Ch 32（quoting）|
| health（健康巡檢）| Ch 9（mount/df）、Ch 14-16（process/load）、Ch 16（/proc）|
| procwatch（process 監控）| Ch 14-18（process 全套）、練習 B |
| diskhog（空間分析）| Ch 4-6（inode/du）、Ch 12（find）、Ch 27（sort）|
| logwatch（log 分析）| Ch 23-27（regex/grep/awk/sort）、練習 C |
| netcheck（網路）| Ch 8（socket）、Ch 16（/proc/net）|
| cleanup（安全清理）| Ch 11（rm）、Ch 35（trap/錯誤處理）、練習 D |
| 全部 | Ch 19（重導向）、Ch 29（環境）、Ch 35（健壯）、Ch 36（shellcheck）|

整門課至少 70% 的核心概念都用上了——這是 Final Project 的標準。

## 任務規格

### 共用要求（所有腳本）

- **健壯**（Ch 35）：`set -euo pipefail`、錯誤到 stderr、有意義的退出碼
- **quoting 正確**（Ch 32）：所有變數加引號、通過 shellcheck（Ch 36）零警告
- **共用函式庫**（Ch 34）：log/die/顏色輸出等放 `lib/common.sh`，各腳本 `source` 它
- **cron 友善**（Ch 30）：不依賴互動環境、絕對路徑邏輯、`--quiet` 模式給自動化
- **help**：每個子命令支援 `-h`/`--help`（Ch 34 的 case 解析）

### 各子命令規格

**`health`** — 系統健康總覽：
- CPU 負載（`/proc/loadavg`，和核心數對比判斷高低，Ch 16）
- 記憶體使用（`free` 或 `/proc/meminfo`）
- 磁碟使用（`df -h`，標出 >80% 的，Ch 9）
- 最吃 CPU/記憶體的前 5 個 process（`ps` + `sort`，Ch 16/27）
- 系統運行時間、登入使用者數
- 用顏色標示警告（紅=危險、黃=注意、綠=正常）

**`procwatch <name|pid>`** — process 監控（整合練習 B）：
- 持續監控指定 process 的 state/CPU/記憶體/fd 數
- 偵測異常（zombie/D 狀態/高 fd）
- process 消失時通知

**`diskhog <dir>`** — 空間分析：
- 找出指定目錄下最大的前 N 個檔案和子目錄（`du` + `sort`，Ch 27）
- 找出大於某大小的檔案（`find -size`，Ch 12）
- 找出最近增長的檔案（按 mtime，Ch 10）

**`logwatch <logfile>`** — log 分析（整合練習 C）：
- 各狀態碼分布、Top IP/URL、錯誤率、可疑活動
- 支援 `--follow` 即時模式（`tail -f`，Ch 20）

**`netcheck`** — 網路檢查：
- 列出監聽的 port（`ss -tlnp` 或 `/proc/net/tcp`，Ch 8）
- 檢查關鍵主機連通性（ping/curl 一組目標）
- 偵測異常連線數

**`cleanup [--dry-run]`** — 安全清理（整合練習 D 的健壯性）：
- 清理 `/tmp` 下舊檔案、舊 log、套件快取
- `--dry-run` 模式：只顯示「會刪什麼」不真的刪（安全第一！）
- trap 保護、操作前確認、詳細日誌

### 驗收標準

- 所有腳本通過 `shellcheck` 零警告（Ch 36）
- `./sysops health` 在你的機器上跑出合理的健康報告
- 每個子命令的 `--help` 清楚說明用法
- cleanup 的 `--dry-run` 絕不刪任何東西（安全驗證）
- 錯誤情況（參數錯、檔案不存在、權限不足）優雅處理
- 能把 `health` 放進 cron 每小時跑、輸出進 log（Ch 30）

## 期望輸出範例

```
$ ./sysops health
╔════════════════════════════════════════╗
║        System Health Report            ║
╚════════════════════════════════════════╝
Hostname:   webserver-01
Uptime:     up 23 days, 4 hours
Load avg:   2.15 1.98 1.76  (4 cores)  [OK]

[Memory]
  Used:  6.2G / 7.7G  (80%)  [WARN]

[Disk]
  /        45% ████████░░░░░░░░  [OK]
  /var     87% █████████████░░░  [WARN]  ← 注意！
  /home    23% ████░░░░░░░░░░░░  [OK]

[Top CPU]
  PID    %CPU  COMMAND
  1234   45.2  /usr/bin/python3 train.py
  5678   12.1  nginx: worker

[Top Memory]
  PID    %MEM  COMMAND
  1234   34.5  /usr/bin/python3

Logged-in users: 2
```

```
$ ./sysops cleanup --dry-run
[DRY-RUN] Would remove the following (nothing actually deleted):
  /tmp/old-session-xyz (3 days old, 12M)
  /var/log/app.log.5.gz (45 days old, 2M)
  ...
[DRY-RUN] Total: 23 files, 145M would be freed
Run without --dry-run to actually delete.
```

## 如果你卡住了

1. **先搭骨架**：主 dispatch（`case "$1" in ...`）+ 一個最簡單的子命令（如 health 只印負載），跑通了再擴充
2. **函式庫先行**：先寫 `lib/common.sh`（log/die/顏色），所有腳本 `source "$(dirname "$0")/lib/common.sh"`
3. **一個子命令一個子命令做**：別想一次做完，health → cleanup → logwatch 逐個攻克
4. **重用練習**：procwatch 直接拿練習 B、logwatch 拿練習 C、cleanup 用練習 D 的健壯模式
5. **顏色**：用 ANSI 碼（`RED=$'\033[31m'`、`RESET=$'\033[0m'`），但偵測非終端機時關掉（`[[ -t 1 ]]`，cron 裡別輸出色碼）
6. **每個腳本都 shellcheck**：邊寫邊檢查，別累積問題
7. **--dry-run 先做**：cleanup 先把 dry-run 做對（只印不刪），確認邏輯對了再加真刪除

## 實作步驟建議

### Step 1：專案骨架 + 共用函式庫（lib/common.sh）+ 主 dispatch
### Step 2：health 子命令（最能展示系統觀測整合）
### Step 3：diskhog + logwatch（文字處理與分析整合，重用練習 C）
### Step 4：procwatch + netcheck（process 與網路觀測，重用練習 B）
### Step 5：cleanup（健壯性的極致，--dry-run + trap，重用練習 D）+ 全套 shellcheck + cron 整合

## 完整參考解答

**這是 Final Project，務必自己做！** 下面只給「函式庫 + 主 dispatch + health」當起步參考，其餘子命令你自己整合練習 B/C/D 完成。

<details>
<summary>lib/common.sh（共用函式庫）</summary>

```bash
#!/bin/bash
# lib/common.sh — sysops 工具包的共用函式庫
# 被各子命令 source，不單獨執行

# 顏色（偵測是否輸出到終端機，cron/管線時關閉色碼）
if [[ -t 1 ]]; then
    readonly C_RED=$'\033[31m'
    readonly C_GREEN=$'\033[32m'
    readonly C_YELLOW=$'\033[33m'
    readonly C_RESET=$'\033[0m'
else
    readonly C_RED='' C_GREEN='' C_YELLOW='' C_RESET=''
fi

# 日誌（時間戳 + 訊息）
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

# 錯誤（stderr + 退出，Ch 35）
die() {
    echo "${C_RED}Error:${C_RESET} $*" >&2
    exit 1
}

# 狀態標示（依閾值給顏色，Ch 34）
status_tag() {
    local value="$1" warn="$2" crit="$3"
    if (( value >= crit )); then
        echo "${C_RED}[CRIT]${C_RESET}"
    elif (( value >= warn )); then
        echo "${C_YELLOW}[WARN]${C_RESET}"
    else
        echo "${C_GREEN}[OK]${C_RESET}"
    fi
}

# 畫 ASCII 進度條（百分比）
bar() {
    local pct="$1" width=16 filled
    filled=$(( pct * width / 100 ))
    printf '%s%s' \
        "$(printf '█%.0s' $(seq 1 "$filled"))" \
        "$(printf '░%.0s' $(seq 1 $(( width - filled )) ))"
}
```

</details>

<details>
<summary>sysops（主入口 / dispatch）</summary>

```bash
#!/bin/bash
# sysops — SysOps 工具包主入口，dispatch 到各子命令
set -euo pipefail

# 找到自己所在目錄（讓腳本能從任何地方執行，cron 友善）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
COMMANDS_DIR="$SCRIPT_DIR/commands"

usage() {
    cat <<EOF
sysops — SysOps toolkit

Usage: sysops <command> [args]

Commands:
  health              System health overview
  procwatch <target>  Monitor a process
  diskhog <dir>       Find space hogs in a directory
  logwatch <logfile>  Analyze a log file
  netcheck            Check network connectivity and ports
  cleanup [--dry-run] Safely clean temp/old files

Run 'sysops <command> --help' for command-specific help.
EOF
}

main() {
    if [[ $# -lt 1 ]]; then
        usage
        exit 1
    fi

    local cmd="$1"
    shift                       # 移掉子命令名，剩下的是它的參數

    case "$cmd" in              # Ch 34：case dispatch
        -h|--help|help)
            usage
            ;;
        health|procwatch|diskhog|logwatch|netcheck|cleanup)
            local script="$COMMANDS_DIR/${cmd}.sh"
            [[ -x "$script" ]] || die "command script not found: $script"
            exec "$script" "$@"   # exec 換成子命令（Ch 15），傳所有剩餘參數
            ;;
        *)
            die "unknown command: $cmd (run 'sysops --help')"
            ;;
    esac
}

main "$@"
```

</details>

<details>
<summary>commands/health.sh（系統健康巡檢）</summary>

```bash
#!/bin/bash
# commands/health.sh — 系統健康總覽
set -euo pipefail

# 載入共用函式庫
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/../lib/common.sh"

# --help
if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    echo "Usage: sysops health"
    echo "Shows CPU load, memory, disk usage, and top processes."
    exit 0
fi

echo "╔════════════════════════════════════════╗"
echo "║        System Health Report            ║"
echo "╚════════════════════════════════════════╝"

# 主機與運行時間
echo "Hostname:   $(hostname)"
echo "Uptime:     $(uptime -p)"

# CPU 負載（/proc/loadavg，Ch 16）+ 和核心數對比
read -r load1 load5 load15 _ < /proc/loadavg
cores="$(nproc)"
# 負載百分比（load1 / cores * 100，用 awk 因為要浮點，Ch 26/33）
load_pct="$(awk -v l="$load1" -v c="$cores" 'BEGIN {printf "%d", l/c*100}')"
echo "Load avg:   $load1 $load5 $load15  ($cores cores)  $(status_tag "$load_pct" 70 100)"

# 記憶體（free，Ch 16）
echo ""
echo "[Memory]"
mem_pct="$(free | awk '/^Mem:/ {printf "%d", $3/$2*100}')"
mem_h="$(free -h | awk '/^Mem:/ {print $3 " / " $2}')"
echo "  Used:  $mem_h  (${mem_pct}%)  $(status_tag "$mem_pct" 80 95)"

# 磁碟（df，Ch 9）—— 每個掛載點一行進度條
echo ""
echo "[Disk]"
df -hP -x tmpfs -x devtmpfs | awk 'NR>1 {print $6, $5}' | while read -r mount usage; do
    pct="${usage%\%}"          # 去掉 % 號（Ch 33 參數展開）
    printf "  %-8s %3s%% %s  %s\n" "$mount" "$pct" "$(bar "$pct")" "$(status_tag "$pct" 80 90)"
done

# Top CPU（ps + sort，Ch 16/27）
echo ""
echo "[Top CPU]"
printf "  %-6s %-5s %s\n" "PID" "%CPU" "COMMAND"
ps -eo pid,pcpu,comm --sort=-pcpu | awk 'NR>1 && NR<=6 {printf "  %-6s %-5s %s\n", $1, $2, $3}'

# Top Memory
echo ""
echo "[Top Memory]"
printf "  %-6s %-5s %s\n" "PID" "%MEM" "COMMAND"
ps -eo pid,pmem,comm --sort=-pmem | awk 'NR>1 && NR<=6 {printf "  %-6s %-5s %s\n", $1, $2, $3}'

# 登入使用者
echo ""
echo "Logged-in users: $(who | wc -l)"
```

</details>

**解答說明**：

- **三層架構**（Ch 21 哲學）：`sysops`（dispatch）→ `commands/*.sh`（各做一件事）→ `lib/common.sh`（共用）。每個子命令獨立、可單獨測試、組合成工具包
- **dispatch 用 exec**（Ch 15）：主入口 `exec "$script" "$@"` 換成子命令 process，乾淨地傳遞參數
- **SCRIPT_DIR 定位**（Ch 30 cron 友善）：`cd "$(dirname "${BASH_SOURCE[0]}")" && pwd` 讓腳本從任何地方執行都能找到自己的檔案（cron 的當前目錄不確定）
- **顏色偵測終端機**（Ch 19）：`[[ -t 1 ]]` 判斷 stdout 是否終端機——是才用色碼，cron/管線時關閉（否則 log 裡全是 `\033[31m` 亂碼）
- **/proc 和標準工具結合**（Part 4）：loadavg 從 /proc 讀、記憶體用 free、process 用 ps——展示「系統觀測」的多種來源
- **參數展開去 %**（Ch 33）：`${usage%\%}` 把 `87%` 變 `87`（去後綴），用於數值比較
- **awk 做浮點**（Ch 26/33）：負載百分比要浮點除法，shell 只有整數，借 awk
- **其餘子命令**：procwatch 整合練習 B、logwatch 整合練習 C、cleanup 整合練習 D——你自己完成，這是 Final Project 的核心工作

## 測試用案例

| 操作 | 預期 | 驗證的整合能力 |
|---|---|---|
| `./sysops health` | 完整健康報告，顏色標示 | Part 4 + Part 7 觀測 |
| `./sysops --help` | 列出所有子命令 | Ch 34 dispatch |
| `./sysops badcommand` | "unknown command"，exit 非 0 | Ch 35 錯誤處理 |
| `./sysops cleanup --dry-run` | 只顯示不刪除 | Ch 35 安全 + 練習 D |
| `./sysops logwatch access.log` | log 分析報告 | Part 6 + 練習 C |
| `shellcheck **/*.sh` | 全部零警告 | Ch 36 |
| cron 跑 health 進 log | 無色碼、正確記錄 | Ch 30 + Ch 19 |
| 各子命令 `--help` | 清楚說明 | Ch 34 |

## 延伸挑戰（加分）

- **挑戰一**：加 `--json` 輸出——所有子命令支援 JSON 格式輸出（用 awk 產出），讓工具能被其他程式/監控系統消費（呼應 Ch 21 結構化 vs 文字的討論）

- **挑戰二**：閾值設定檔——從 `~/.sysops.conf` 讀各種閾值（磁碟警告線、CPU 閾值），讓工具可配置而非寫死

- **挑戰三**：歷史與趨勢——health 每次跑記錄到時序檔案，加 `sysops trend` 顯示「過去 24 小時的負載/記憶體趨勢」（用 awk 處理時序資料）

- **挑戰四**：告警整合——health/procwatch 偵測到危險時，發送通知（email/Slack webhook/桌面通知），可放 cron 做主動監控

- **挑戰五**：多機版本——讓 health 能 SSH 到一組伺服器收集狀態，彙總成一個總覽（呼應 networking 課的遠端管理）

- **挑戰六**：打包發布——寫一個 `install.sh` 把 sysops 安裝到 `/usr/local/bin`，加 man page，做成能 `apt`/`make install` 的形式（呼應 debian_packaging 課）

## 自我檢核

完成這個專案後，你應該能回答：

- [ ] 我能把整門課的知識（檔案系統/process/文字處理/scripting）**組合**成解決真實問題的工具，而非孤立地用單一命令
- [ ] 我寫的腳本通過 shellcheck、有完整錯誤處理、敢放進 cron 無人值守跑
- [ ] 我理解「組合小工具」（Unix 哲學）不只是命令列哲學，也是腳本架構的設計原則
- [ ] 面試被問「你怎麼用命令列排查系統問題」，我能展示這套工具背後的思路
- [ ] 我能向別人解釋每個子命令底層在對 kernel 做什麼（strace 的視角貫穿全課）

## 結語：你現在站在哪裡

完成這門課和這個專案，你已經從「背 Linux 指令」進化到「理解命令列底層 + 能用它建構系統」。你知道：

- 每個命令底層在對 kernel 做什麼 syscall（strace 的視角，Ch 0 貫穿全課）
- 檔案系統的真相（inode/VFS/fd，Part 2），能 debug「rm 了還佔空間」這類問題
- process 的生命週期（fork/exec/signal，Part 4），能處理 zombie、卡死、訊號
- 管線和文字處理的威力（Part 5-6），能用 grep/awk/sed 組合做資料分析
- 系統管理的核心（Part 7），能管服務、排程、查日誌
- 寫健壯腳本的紀律（Part 8），能交付生產級的自動化工具

這些不是「會用工具」，是**理解系統**。這個基礎能讓你在任何陌生的 Linux 問題前推理出解法——這正是資深系統工程師和「只會複製貼上」的人的根本差異。

接下來往哪去？這門課的「精選資料庫」（見 [README](./README.md)）列了進階方向：《APUE》把 syscall 推到極致、OSTEP 講 process/檔案系統背後的 OS 原理、Brendan Gregg 的效能工程。但更重要的是——**去用它**。在你的伺服器上跑這套工具、遇到問題時用 strace 挖底層、把日常重複的事寫成腳本。命令列的功力是用出來的，不是讀出來的。

恭喜你走到這裡。
