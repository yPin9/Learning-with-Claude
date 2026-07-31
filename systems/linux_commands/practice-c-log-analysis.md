# 練習 C — log 分析管線

> **目標**：整合 Ch 19–27 的 I/O 重導向、管線、regex、grep/sed/awk/sort/uniq——寫一個 `loganalyze.sh`，分析一個 web server access log，產出一份報表：總請求數、各狀態碼分布、前 10 熱門 IP、前 10 熱門 URL、流量總和、每小時請求趨勢、可疑活動偵測。完成後你能用純命令列做出真實的 log 分析工具，這是 SRE/DevOps 的日常硬功夫。

## 背景與動機

你學了管線（Part 5）和文字處理（Part 6）。現在把它們綁成一個真實工具——log 分析。每個運維工程師都做這件事：伺服器出問題，第一步就是分析 log，找出「誰在打我」「哪個頁面在報錯」「流量哪來的」。

商業工具（Splunk、ELK、Datadog）能做這些，但它們昂貴、要架設、不總是手邊有。一個會用 grep/awk/sort 的工程師，能在任何一台 Linux 上、對著原始 log 檔，幾秒鐘回答這些問題——不用任何額外工具。這正是 Ch 21 管線哲學的實證：小工具組合解決真實問題。這個練習會逼你把 regex、awk 欄位提取、sort|uniq 統計組合成完整的分析流水線。

## 任務規格

寫 `loganalyze.sh <access.log>`，分析標準的 Apache/Nginx combined log format：

```
標準 combined log 格式（每行）：
192.168.1.100 - - [10/Oct/2024:13:55:36 +0000] "GET /index.html HTTP/1.1" 200 2326 "-" "Mozilla/5.0..."
   $1=IP          $4=[time]              $6="method  $7=url  $9=status $10=bytes
```

| 報表項目 | 用到的工具 | 章節 |
|---|---|---|
| 總請求數 | wc -l | Ch 13 |
| 各狀態碼分布（200/404/500…）| awk + sort + uniq -c | Ch 26/27 |
| 前 10 熱門 IP | awk + sort + uniq -c + sort -rn | Ch 26/27 |
| 前 10 熱門 URL | awk + sort + uniq | Ch 26/27 |
| 總流量（bytes 加總）| awk sum | Ch 26 |
| 每小時請求趨勢 | awk 提取小時 + 統計 | Ch 26 |
| 4xx/5xx 錯誤率 | awk 條件計數 | Ch 26 |
| 可疑 IP（請求數異常高）| awk 閾值 | Ch 26 |

**驗收標準**：
- 接受 log 檔路徑當參數，檔案不存在要報錯
- 各區塊清楚分隔、有標題
- 數字正確（能手動驗證小樣本）
- 處理大檔案不爆記憶體（用 streaming，不要把整檔讀進變數）
- 前 N 排名正確（按次數降序）

## 期望輸出範例

```
$ ./loganalyze.sh access.log
========================================
  Log Analysis Report: access.log
========================================

[Overview]
Total requests:    15234
Unique IPs:        342
Total traffic:     1.2 GB
Time range:        10/Oct/2024:00:00 - 10/Oct/2024:23:59

[Status Code Distribution]
  10543  200
   2103  404
   1502  301
    876  500
    210  403

[Top 10 IPs]
   1832  203.0.113.45
    954  198.51.100.12
    ...

[Top 10 URLs]
   3201  /api/users
   2105  /index.html
    ...

[Requests per Hour]
  00:00  ███████ 421
  01:00  ████ 234
  ...

[Suspicious Activity]
  WARNING: 203.0.113.45 made 1832 requests (>1000 threshold)
  Error rate: 19.6% (2989 4xx/5xx out of 15234)
```

## 如果你卡住了

1. 先用小樣本：手動造 5-10 行 log，確認你的 awk 欄位編號對（`awk '{print $1, $9}'` 看 IP 和狀態碼對不對）
2. log 格式的欄位：IP 是 `$1`，狀態碼是 `$9`，bytes 是 `$10`，URL 在 `$7`（在 `"GET /url HTTP/1.1"` 裡，整個 request 是 `$6 $7 $8`）
3. 提取小時：時間在 `$4` 是 `[10/Oct/2024:13:55:36`，用 `substr` 或 `split` 取 `:13`（小時）
4. 統計骨架：`awk '{print $欄}' | sort | uniq -c | sort -rn | head`（Ch 27 萬用骨架）
5. 加總流量：`awk '{sum += $10} END {print sum}'`，注意 bytes 可能是 `-`（要過濾）
6. 可疑偵測：先統計每個 IP 的次數，再 awk 篩出超過閾值的
7. 用函式組織：每個報表區塊一個函式，主程式呼叫它們

## 實作步驟建議

### Step 1：參數驗證 + 檔案存在檢查
### Step 2：Overview（總數、unique IP、總流量）
### Step 3：狀態碼分布 + 各種 Top N 排名
### Step 4：每小時趨勢（提取小時 + 統計 + ASCII bar）
### Step 5：可疑活動偵測 + 整合成報表

## 完整參考解答

**寫完再看！** 自己組管線才學得到東西。

<details>
<summary>loganalyze.sh</summary>

```bash
#!/bin/bash
# loganalyze.sh — 分析 web server access log（combined format）

set -euo pipefail

# Step 1: 參數驗證
if [ $# -lt 1 ]; then
    echo "Usage: $0 <access.log>" >&2
    exit 1
fi
LOG="$1"
if [ ! -f "$LOG" ]; then
    echo "Error: file '$LOG' not found" >&2
    exit 1
fi

TOP_N=10
SUSPICIOUS_THRESHOLD=1000

# 標題
echo "========================================"
echo "  Log Analysis Report: $LOG"
echo "========================================"

# Step 2: Overview
echo ""
echo "[Overview]"
total=$(wc -l < "$LOG")
printf "Total requests:    %s\n" "$total"

# unique IP（第 1 欄）
unique_ips=$(awk '{print $1}' "$LOG" | sort -u | wc -l)
printf "Unique IPs:        %s\n" "$unique_ips"

# 總流量（第 10 欄 bytes，過濾非數字的 "-"）
total_bytes=$(awk '$10 ~ /^[0-9]+$/ {sum += $10} END {print sum+0}' "$LOG")
# 轉成 human-readable
human=$(awk -v b="$total_bytes" 'BEGIN {
    split("B KB MB GB TB", u, " ");
    i=1; while (b >= 1024 && i < 5) {b /= 1024; i++}
    printf "%.1f %s", b, u[i]
}')
printf "Total traffic:     %s\n" "$human"

# Step 3: 狀態碼分布（第 9 欄）
echo ""
echo "[Status Code Distribution]"
awk '{print $9}' "$LOG" | sort | uniq -c | sort -rn

# Top N IPs（第 1 欄）
echo ""
echo "[Top $TOP_N IPs]"
awk '{print $1}' "$LOG" | sort | uniq -c | sort -rn | head -"$TOP_N"

# Top N URLs（第 7 欄，request 的 URL 部分）
echo ""
echo "[Top $TOP_N URLs]"
awk '{print $7}' "$LOG" | sort | uniq -c | sort -rn | head -"$TOP_N"

# Step 4: 每小時請求趨勢
echo ""
echo "[Requests per Hour]"
# 時間在 $4 = [10/Oct/2024:13:55:36，提取 :HH（小時）
awk -F'[:[]' '{print $3}' "$LOG" | sort | uniq -c | sort -k2 -n | \
awk '{
    count = $1; hour = $2;
    # 畫 ASCII bar（每 50 個請求一個 █，上限 40 格）
    bars = int(count / 50); if (bars > 40) bars = 40;
    bar = "";
    for (i = 0; i < bars; i++) bar = bar "█";
    printf "  %s:00  %s %d\n", hour, bar, count;
}'

# Step 5: 可疑活動
echo ""
echo "[Suspicious Activity]"
# 請求數超過閾值的 IP
awk '{print $1}' "$LOG" | sort | uniq -c | sort -rn | \
awk -v t="$SUSPICIOUS_THRESHOLD" '$1 > t {
    printf "  WARNING: %s made %d requests (>%d threshold)\n", $2, $1, t
}'

# 錯誤率（4xx/5xx）
awk -v total="$total" '
    $9 ~ /^[45]/ {errors++}
    END {
        rate = (errors / total) * 100;
        printf "  Error rate: %.1f%% (%d 4xx/5xx out of %d)\n", rate, errors, total
    }
' "$LOG"
```

```bash
chmod +x loganalyze.sh
# 造測試 log（或用真實的 /var/log/nginx/access.log）
./loganalyze.sh access.log
```

**解答說明**：

- **欄位編號**：combined log 用空白分隔，IP=$1、狀態碼=$9、bytes=$10、URL=$7。awk 自動切欄位（Ch 26）
- **過濾非數字 bytes**：`$10 ~ /^[0-9]+$/` 確保只加總數字（有些行 bytes 是 `-`），避免 awk 把 `-` 當 0 之外的怪值
- **human-readable 流量**：在 awk BEGIN 裡做除法迴圈（1024 進位），`split("B KB MB GB TB", u)` 建單位陣列（Ch 26）
- **提取小時**：`-F'[:[]'`（用 `:` 或 `[` 當分隔符，多字元 FS 是 regex）把 `[10/Oct/2024:13:...` 切開，第 3 欄是小時。也可用 `substr`/`split`
- **統計骨架**：到處用 `awk '{print $欄}' | sort | uniq -c | sort -rn`（Ch 27 萬用骨架），這是整個工具的核心模式
- **ASCII bar**：用 awk 迴圈印 `█` 字元視覺化（每 50 請求一格），讓趨勢一眼可見
- **可疑偵測**：兩段 awk——先 `uniq -c` 統計每 IP 次數，再 awk 篩超過閾值的。錯誤率用 `$9 ~ /^[45]/`（4xx/5xx 開頭）計數
- **streaming 不爆記憶體**：全程用管線和 awk 逐行處理，沒有把整個 log 讀進 shell 變數（大檔案安全，Ch 20）

</details>

## 測試用案例

| 操作 | 預期 | 驗證 |
|---|---|---|
| 正常 access.log | 完整報表 | 整合功能 |
| 不存在的檔案 | "Error: file not found" | 錯誤處理 |
| 手動造 10 行 log | 數字能手動核對 | 正確性 |
| bytes 欄含 `-` | 不影響流量加總 | 邊界處理 |
| 某 IP 請求 > 1000 | WARNING 出現 | 可疑偵測 |
| 空 log 檔 | 不崩潰（0 請求）| 邊界處理 |
| 超大 log（GB 級）| 不爆記憶體、能完成 | streaming |

## 延伸挑戰（加分）

- **挑戰一**：加「時間範圍篩選」——只分析某個時段（如 `--since 13:00 --until 14:00`）的請求。用 awk 比較 `$4` 的時間

- **挑戰二**：偵測「掃描行為」——一個 IP 在短時間內請求大量不同的 404 URL（可能在掃漏洞）。需要按 IP 分組 + 統計它的 404 URL 多樣性

- **挑戰三**：輸出多種格式——加 `--format json` 用 awk 產出 JSON（給其他工具吃）、`--format csv` 產 CSV

- **挑戰四**：即時監控模式——`--follow` 用 `tail -f access.log | ...` 即時分析新進的 log（呼應 Ch 20 的串流），每 N 秒更新統計

- **挑戰五**：比較兩個時段——分析昨天和今天的 log，報告「哪些 URL 流量暴增」「新出現的錯誤」（用 join 或 awk 兩檔案比較，Ch 26/27）

## 自我檢核

- [ ] 能用 awk 從結構化 log 提取任意欄位（IP/狀態碼/URL/bytes/時間）
- [ ] 熟練 `提取 | sort | uniq -c | sort -rn` 的頻率統計骨架
- [ ] 能用 awk 做條件計數（錯誤率）和閾值篩選（可疑 IP）
- [ ] 知道怎麼用 streaming 處理大檔案而不爆記憶體
- [ ] 能說出這個工具和商業 log 分析（Splunk/ELK）解決的是同一類問題

→ [Ch 28 user/group/sudo](./28-users-groups.md)
