# 練習 B — 日誌分析 Pipeline

> 目標：整合 Part 3（Ch 10–14）的工具，用一系列 pipeline 分析 Nginx access log，回答幾個真實維運問題。

## 任務規格

分析一份 Nginx access log，回答以下問題，每個答案要附上使用的指令。

Nginx access log 的標準格式（combined）：

```
IP - - [day/Mon/year:HH:MM:SS +TZ] "METHOD /path HTTP/x.x" STATUS bytes "referer" "user-agent"
```

範例：

```
192.168.1.42 - - [15/Jan/2024:09:00:01 +0800] "GET /index.html HTTP/1.1" 200 1234 "-" "Mozilla/5.0"
```

## 建立測試資料

```bash
cat > /tmp/access.log << 'EOF'
192.168.1.42 - - [15/Jan/2024:09:00:01 +0800] "GET /index.html HTTP/1.1" 200 1234 "-" "Mozilla/5.0"
192.168.1.10 - - [15/Jan/2024:09:00:05 +0800] "POST /api/login HTTP/1.1" 200 89 "-" "curl/7.68"
192.168.1.42 - - [15/Jan/2024:09:00:10 +0800] "GET /style.css HTTP/1.1" 200 456 "-" "Mozilla/5.0"
10.0.0.5 - - [15/Jan/2024:09:01:00 +0800] "GET /admin HTTP/1.1" 403 234 "-" "python-requests/2.25"
192.168.1.99 - - [15/Jan/2024:09:01:30 +0800] "GET /notfound HTTP/1.1" 404 123 "-" "Mozilla/5.0"
192.168.1.42 - - [15/Jan/2024:09:02:00 +0800] "GET /api/data HTTP/1.1" 200 5678 "-" "Mozilla/5.0"
10.0.0.5 - - [15/Jan/2024:09:02:10 +0800] "GET /admin HTTP/1.1" 403 234 "-" "python-requests/2.25"
192.168.1.10 - - [15/Jan/2024:09:02:30 +0800] "DELETE /api/user/3 HTTP/1.1" 204 0 "-" "curl/7.68"
192.168.1.42 - - [15/Jan/2024:09:03:00 +0800] "GET /large-file HTTP/1.1" 200 98765 "-" "wget/1.20"
10.0.0.5 - - [15/Jan/2024:09:03:10 +0800] "POST /api/login HTTP/1.1" 401 56 "-" "python-requests/2.25"
192.168.1.77 - - [15/Jan/2024:09:03:30 +0800] "GET /index.html HTTP/1.1" 200 1234 "-" "Mozilla/5.0"
192.168.1.10 - - [15/Jan/2024:09:04:00 +0800] "GET /api/data HTTP/1.1" 500 345 "-" "curl/7.68"
EOF
```

## 問題列表

### 問題一：流量分析

| 問題 | 你的指令 | 答案 |
|------|---------|------|
| 總共有幾個請求？ | | |
| 每個 IP 各發了幾個請求？（按請求數降序）| | |
| 請求最多的 IP 是哪個？ | | |

### 問題二：狀態碼分析

| 問題 | 你的指令 | 答案 |
|------|---------|------|
| 各狀態碼的數量分布？ | | |
| 有幾個 4xx 和 5xx 的錯誤？ | | |
| 哪些路徑出現了 404？ | | |

### 問題三：可疑行為

| 問題 | 你的指令 | 答案 |
|------|---------|------|
| 哪個 IP 持續收到 403（拒絕存取）？ | | |
| 有沒有 IP 收到多次登入失敗（401）？ | | |
| 哪個 User-Agent 看起來像爬蟲/腳本？ | | |

### 問題四：流量大小

| 問題 | 你的指令 | 答案 |
|------|---------|------|
| 回應最大的那個請求（bytes）是哪個路徑？ | | |
| 所有請求的總傳輸量（bytes）是多少？ | | |

## 實作步驟建議

### Step 1：理解欄位位置

先用 `awk` 印出各欄位，確認位置：

```bash
head -1 /tmp/access.log | awk '{
    for (i=1; i<=NF; i++) print i": "$i
}'
```

Nginx combined log 的關鍵欄位：
- `$1` = IP
- `$7` = 路徑
- `$9` = 狀態碼
- `$10` = 回應大小（bytes）
- `$12`（含引號）= User-Agent 開頭

### Step 2：逐一解答

每個問題的提示方向：

**問題一**：`awk '{count[$1]++} END {...}' | sort -rn`

**問題二**：狀態碼在 `$9`，用 `awk` 抓 `$9` 再 `sort | uniq -c`

**問題三**：`grep` 篩 403 行，再看 IP 欄

**問題四**：bytes 在 `$10`，用 `awk` 找最大值或求總和

## 完整參考解答

**全部做完再看！**

<details>
<summary>點開參考解答</summary>

```bash
# 問題一
# 總請求數
wc -l < /tmp/access.log

# 每個 IP 的請求數
awk '{count[$1]++} END {for (ip in count) print count[ip], ip}' /tmp/access.log | sort -rn

# 請求最多的 IP
awk '{count[$1]++} END {for (ip in count) print count[ip], ip}' /tmp/access.log | sort -rn | head -1

# 問題二
# 狀態碼分布
awk '{print $9}' /tmp/access.log | sort | uniq -c | sort -rn

# 4xx 和 5xx 錯誤數
awk '$9 ~ /^[45]/ {count++} END {print count}' /tmp/access.log

# 404 的路徑
awk '$9 == 404 {print $7}' /tmp/access.log

# 問題三
# 持續 403 的 IP
awk '$9 == 403 {count[$1]++} END {for (ip in count) if (count[ip]>1) print count[ip], ip}' /tmp/access.log

# 多次 401 的 IP
awk '$9 == 401 {count[$1]++} END {for (ip in count) print count[ip], ip}' /tmp/access.log

# User-Agent 分布（移除引號）
awk -F'"' '{print $6}' /tmp/access.log | sort | uniq -c | sort -rn

# 問題四
# 回應最大的路徑
awk '{if ($10 > max) {max=$10; path=$7}} END {print path, max}' /tmp/access.log

# 總傳輸量
awk '{sum += $10} END {print sum, "bytes"}' /tmp/access.log
```

</details>

## 進階挑戰

```bash
# 產生一份完整的統計報告（輸出到檔案）
{
echo "=== Access Log 分析報告 ==="
echo "總請求數: $(wc -l < /tmp/access.log)"
echo ""
echo "--- 狀態碼分布 ---"
awk '{print $9}' /tmp/access.log | sort | uniq -c | sort -rn
echo ""
echo "--- Top 5 IP ---"
awk '{count[$1]++} END {for (ip in count) print count[ip], ip}' /tmp/access.log | sort -rn | head -5
echo ""
echo "--- 錯誤請求 ---"
awk '$9 >= 400 {print $1, $9, $7}' /tmp/access.log
} > /tmp/report.txt

cat /tmp/report.txt
```

## 自我檢核

- [ ] 能用 `awk '{count[$1]++} END {...}'` 做分組計數
- [ ] 能組合 `awk + sort + uniq` 做頻率分析
- [ ] 能用 `awk '{sum += $10}'` 做欄位累加
- [ ] 能用 `awk -F'"'` 處理含引號的欄位

→ [Ch 15 行程狀態機](./15-process-state-machine.md)
