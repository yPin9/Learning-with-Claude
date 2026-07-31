# 練習 D — robust 備份腳本

> **目標**：整合 Ch 32–36 的 scripting 知識，寫一個**生產級**的備份腳本 `backup.sh`——它要 quoting 正確（Ch 32）、用參數展開（Ch 33）、有完整的控制流和函式（Ch 34）、用 set -euo pipefail + trap 做錯誤處理（Ch 35）、並通過 shellcheck（Ch 36）。完成後你能寫出「敢放進 cron 半夜自動跑」的腳本，這是 SysOps 的核心交付物。

## 背景與動機

你學完了 shell scripting（Part 8）。現在驗收：寫一個真實的備份腳本。備份是運維最基本也最重要的任務——但寫一個**健壯**的備份腳本出奇地難：要處理「來源不存在」「目標磁碟滿」「腳本跑到一半被中斷」「檔名有空白」「同時跑兩個實例」等等。

一個寫壞的備份腳本比沒有備份更糟（它讓你以為有備份，關鍵時刻發現備份是壞的或空的）。這個練習逼你把 Part 8 的每個技術用上——正確的 quoting、錯誤處理、trap 清理、shellcheck 驗證。完成後你寫的腳本能通過「半夜在 cron 無人值守自動跑」的考驗（呼應 Ch 30 的 cron 環境問題）。

## 任務規格

寫 `backup.sh`，把指定來源目錄備份成帶時間戳的壓縮檔，保留最近 N 份：

| 功能 | 要求 | 章節 |
|---|---|---|
| 參數 | `backup.sh <來源目錄> <目標目錄>`，驗證兩者 | Ch 33/34 |
| 備份 | tar + gzip 成 `backup-YYYYMMDD-HHMMSS.tar.gz` | Ch 33 |
| 輪替 | 只保留最近 N 份（預設 7），刪舊的 | Ch 33/34 |
| 健壯 | set -euo pipefail，所有變數加引號 | Ch 32/35 |
| 清理 | trap 清理暫存檔（中斷也清）| Ch 35 |
| 鎖 | 防止同時跑兩個實例 | Ch 35 |
| 日誌 | 記錄開始/結束/錯誤到 log，錯誤到 stderr | Ch 19/35 |
| 驗證 | 通過 shellcheck 無警告 | Ch 36 |

**驗收標準**：
- 來源不存在、目標不可寫 → 清楚報錯並以非 0 退出（Ch 35）
- 備份檔名有正確時間戳，內容正確（能解開還原）
- 超過 N 份時自動刪最舊的，保留正確數量
- 跑到一半 Ctrl-C → trap 清理暫存檔，不留半成品
- 第二個實例在第一個跑時啟動 → 偵測到鎖，拒絕執行
- `shellcheck backup.sh` 零警告
- 能直接放進 cron（不依賴互動環境，Ch 30）

## 期望輸出範例

```
$ ./backup.sh /home/alice/documents /backup
[2024-01-15 03:00:01] Starting backup of /home/alice/documents
[2024-01-15 03:00:01] Creating backup-20240115-030001.tar.gz
[2024-01-15 03:00:45] Backup complete: 234 MB
[2024-01-15 03:00:45] Rotating: keeping 7 most recent, removing 2 old backups
[2024-01-15 03:00:45]   Removed: backup-20240108-030001.tar.gz
[2024-01-15 03:00:45] Done.
```

```
錯誤情況：
$ ./backup.sh /nonexistent /backup
[2024-01-15 03:00:01] Error: source directory '/nonexistent' does not exist
$ echo $?
1

第二個實例（鎖）：
$ ./backup.sh /home/alice /backup
Error: another backup is already running (lock: /tmp/backup.lock)
$ echo $?
1
```

## 如果你卡住了

1. 先寫「快樂路徑」（能跑就好），再逐步加健壯性（錯誤處理、鎖、輪替）
2. 時間戳：`date +%Y%m%d-%H%M%S`（Ch 33 命令替換）
3. 壓縮：`tar -czf "$archive" -C "$source_parent" "$source_name"`（-C 換目錄避免絕對路徑進 tar）
4. 輪替：`ls -t backup-*.tar.gz | tail -n +$((N+1))`（按時間排序，跳過前 N 個，刪剩下的）
5. 鎖：`mkdir "$lockdir"` 是原子操作（成功=拿到鎖，失敗=別人在跑）；或用 `flock`
6. trap：`trap 'rm -rf "$tmp"; rmdir "$lockdir"' EXIT`（清理 + 釋放鎖，Ch 35）
7. 每寫一段就 `shellcheck` 一次（Ch 36），別等最後

## 實作步驟建議

### Step 1：骨架（set -euo pipefail + 參數驗證 + log 函式）
### Step 2：快樂路徑（tar 壓縮成帶時間戳的檔案）
### Step 3：輪替（保留最近 N 份，刪舊的）
### Step 4：trap 清理 + 鎖（防並發）
### Step 5：shellcheck 修到乾淨 + 測試各種錯誤情況

## 完整參考解答

**寫完再看！** 自己踩過坑才學得到健壯性。

<details>
<summary>backup.sh</summary>

```bash
#!/bin/bash
#
# backup.sh — 健壯的目錄備份腳本
# 用法：backup.sh <來源目錄> <目標目錄> [保留份數]
#
set -euo pipefail              # Ch 35：fail-fast

# ---- 設定 ----
KEEP=7                         # 預設保留份數
LOCKDIR="/tmp/backup.lock"     # 鎖目錄（mkdir 是原子操作）
readonly LOCKDIR

# ---- 全域（暫存資源，trap 會清）----
TMPDIR=""

# ---- 函式 ----
log() {                        # Ch 34：log 函式（時間戳 + 訊息）
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

die() {                        # Ch 35：錯誤處理（stderr + 退出）
    log "Error: $*" >&2
    exit 1
}

cleanup() {                    # Ch 35：trap 的清理函式
    [[ -n "$TMPDIR" && -d "$TMPDIR" ]] && rm -rf "$TMPDIR"
    [[ -d "$LOCKDIR" ]] && rmdir "$LOCKDIR" 2>/dev/null || true
}

acquire_lock() {               # Ch 35：用 mkdir 的原子性做鎖
    if ! mkdir "$LOCKDIR" 2>/dev/null; then
        die "another backup is already running (lock: $LOCKDIR)"
    fi
}

rotate() {                     # Ch 33/34：輪替，保留最近 KEEP 份
    local dest="$1" keep="$2"
    # 按時間排序（最新在前），跳過前 keep 個，刪剩下的
    local old
    # 用 find + sort 而非 parse ls（Ch 32/34/36 的安全做法）
    mapfile -t old < <(find "$dest" -maxdepth 1 -name 'backup-*.tar.gz' -printf '%T@ %p\n' \
                       | sort -rn | tail -n +"$((keep + 1))" | cut -d' ' -f2-)
    if [[ ${#old[@]} -gt 0 ]]; then
        log "Rotating: keeping $keep most recent, removing ${#old[@]} old backups"
        local f
        for f in "${old[@]}"; do
            rm -f "$f"
            log "  Removed: $(basename "$f")"
        done
    fi
}

# ---- 主程式 ----
main() {
    # Ch 34：參數驗證
    if [[ $# -lt 2 ]]; then
        die "Usage: $0 <source-dir> <dest-dir> [keep-count]"
    fi
    local source="$1"
    local dest="$2"
    local keep="${3:-$KEEP}"   # Ch 33：第三參數可選，預設 KEEP

    # 驗證來源和目標
    [[ -d "$source" ]] || die "source directory '$source' does not exist"
    [[ -d "$dest" ]]   || die "destination directory '$dest' does not exist"
    [[ -w "$dest" ]]   || die "destination '$dest' is not writable"

    # Ch 35：拿鎖 + 設 trap（拿鎖後立刻設，保證釋放）
    acquire_lock
    trap cleanup EXIT          # 不管怎麼結束都清理 + 釋放鎖

    # 建暫存目錄
    TMPDIR="$(mktemp -d)"

    log "Starting backup of $source"

    # Ch 33：帶時間戳的檔名
    local timestamp archive
    timestamp="$(date '+%Y%m%d-%H%M%S')"
    archive="$dest/backup-${timestamp}.tar.gz"

    log "Creating $(basename "$archive")"

    # 先壓到暫存，成功後才移到目標（原子性：避免半成品被當成完整備份）
    local tmp_archive="$TMPDIR/backup.tar.gz"
    # -C 換到來源的上層目錄，避免絕對路徑和前導 / 進 tar
    local source_parent source_name
    source_parent="$(dirname "$source")"
    source_name="$(basename "$source")"
    if ! tar -czf "$tmp_archive" -C "$source_parent" "$source_name" 2>"$TMPDIR/tar.err"; then
        die "tar failed: $(cat "$TMPDIR/tar.err")"
    fi

    # 移到最終位置（mv 在同檔案系統是原子的）
    mv "$tmp_archive" "$archive"

    # 回報大小
    local size
    size="$(du -h "$archive" | cut -f1)"
    log "Backup complete: $size"

    # Ch 33/34：輪替
    rotate "$dest" "$keep"

    log "Done."
}

main "$@"                       # Ch 32：傳遞所有參數
```

```bash
chmod +x backup.sh
shellcheck backup.sh            # Ch 36：應該零警告
./backup.sh /home/alice/documents /backup 7
```

**解答說明**：

- **set -euo pipefail**（Ch 35）：開頭就設，fail-fast。任何命令失敗、未定義變數、管線失敗都停
- **die / log 函式**（Ch 34）：die 印錯誤到 stderr（Ch 19）並 exit 1；log 加時間戳。函式封裝重複邏輯
- **參數展開**（Ch 33）：`${3:-$KEEP}`（第三參數可選，預設值）；`$(basename ...)` 也可用 `${archive##*/}`
- **mkdir 鎖**（Ch 35）：`mkdir` 是**原子操作**——同時只有一個能成功建立目錄，所以拿來當鎖（失敗=別人在跑）。比用檔案存在判斷可靠（沒有 race condition）
- **trap cleanup EXIT**（Ch 35）：拿鎖後**立刻**設 trap，保證不管腳本怎麼結束（正常/錯誤/Ctrl-C）都清理暫存目錄 + 釋放鎖。這是「不留爛攤子」的關鍵
- **原子性備份**：先壓到暫存（TMPDIR）成功後才 `mv` 到目標——避免「壓到一半被中斷，半成品被當成完整備份」。mv 在同檔案系統是原子的
- **find -printf 而非 parse ls**（Ch 32/34/36）：輪替用 `find -printf '%T@ %p'`（修改時間 + 路徑）+ sort，安全處理含空白檔名，不踩 parse ls 的坑
- **mapfile 讀進陣列**（Ch 33）：`mapfile -t old < <(...)` 安全地把要刪的檔案讀進陣列
- **所有變數加引號**（Ch 32）：每個 `"$var"`、`"${arr[@]}"` 都加引號，shellcheck 驗證
- **cron 友善**（Ch 30）：用絕對路徑邏輯、不依賴互動環境、錯誤碼明確——能直接放 cron

</details>

## 測試用案例

| 操作 | 預期 | 驗證 |
|---|---|---|
| 正常備份 | 建立 backup-時間戳.tar.gz | 快樂路徑 |
| 來源不存在 | "source does not exist"，exit 1 | 參數驗證 |
| 目標不可寫 | "not writable"，exit 1 | 權限檢查 |
| 超過 N 份 | 刪最舊的，保留 N 份 | 輪替 |
| 跑到一半 Ctrl-C | trap 清理暫存，無半成品 | trap |
| 第二實例（鎖）| "already running"，exit 1 | 並發鎖 |
| `shellcheck backup.sh` | 零警告 | Ch 36 驗證 |
| 來源含空白檔名 | 正確備份（不切碎）| quoting |
| 解開備份還原 | 內容和來源一致 | 正確性 |

## 延伸挑戰（加分）

- **挑戰一**：增量備份——用 `tar --listed-incremental` 或 rsync 只備份「變動的檔案」，大幅節省時間和空間

- **挑戰二**：遠端備份——備份後用 `scp`/`rsync` 傳到遠端伺服器，處理網路失敗的重試（呼應 networking 課）

- **挑戰三**：加密備份——用 `gpg` 加密 tar.gz（`tar ... | gpg -c`），保護備份內容。處理密碼/金鑰管理

- **挑戰四**：備份驗證——備份後自動 `tar -tzf` 驗證壓縮檔完整、或算 checksum 存起來，下次能驗證備份沒損壞

- **挑戰五**：設定檔 + 通知——從 config 檔讀多個備份來源，全部備份後用 email/webhook 通知成敗（呼應 Ch 30 cron 的 MAILTO）

## 自我檢核

- [ ] 能寫一個 quoting 全對、通過 shellcheck 的腳本
- [ ] 用 set -euo pipefail + trap 讓腳本健壯（出錯停止、中斷清理）
- [ ] 知道為什麼用 mkdir/flock 做鎖，原子性為什麼重要
- [ ] 理解「先暫存後 mv」的原子性備份為什麼避免半成品
- [ ] 寫的腳本能直接放進 cron 無人值守跑（不依賴互動環境）

→ [Final Project：SysOps 腳本工具包](./final-project-sysops-toolkit.md)
