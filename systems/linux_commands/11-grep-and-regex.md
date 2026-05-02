# Ch 11 — grep 與正規表示式

> 目標：掌握 `grep` 的核心選項，理解 BRE/ERE 的差異，能寫出實用的 regex pattern 從日誌裡提取資訊。

## grep 基本用法

```bash
grep "pattern" file.txt         # 找包含 pattern 的行
grep "error" /var/log/syslog    # 在日誌裡找 error
grep -r "TODO" ./src/           # -r = 遞迴搜尋目錄
grep -ri "error" /var/log/      # -i = 不分大小寫
```

## 最常用的選項

```bash
grep -v "pattern" file          # -v = invert，顯示不包含的行
grep -c "error" file            # -c = count，只顯示行數
grep -n "error" file            # -n = 顯示行號
grep -l "error" *.log           # -l = 只顯示符合的檔名
grep -L "error" *.log           # -L = 只顯示不符合的檔名
grep -o "pattern" file          # -o = only，只輸出匹配的部分
grep -w "the" file              # -w = word，只匹配完整單字
grep -x "exact line" file       # -x = 整行完全匹配
grep -m 5 "error" file          # -m = max，找到 5 個就停止
grep -A 3 "error" file          # -A = after，顯示匹配行後 3 行
grep -B 2 "error" file          # -B = before，顯示匹配行前 2 行
grep -C 2 "error" file          # -C = context，前後各 2 行
```

## BRE vs ERE vs PCRE

grep 支援三種 regex 模式：

| 模式 | 旗標 | 差異 |
|------|------|------|
| BRE（Basic RE）| 預設 | `+`, `?`, `\|` 需要加反斜線 |
| ERE（Extended RE）| `-E` 或 `egrep` | `+`, `?`, `\|` 直接用，更接近一般認知 |
| PCRE（Perl 相容）| `-P` | 支援 lookahead、named capture 等進階語法 |

**一般用 `-E`（ERE）就夠，想用 lookahead 才用 `-P`。**

```bash
# BRE（預設）
grep "colou\?r" file   # \? = 0 或 1 個 u（需要反斜線）

# ERE
grep -E "colou?r" file  # ? 直接用
grep -E "cat|dog" file  # 或
grep -E "^[A-Z]" file   # 行首大寫字母
```

## 正規表示式核心語法

```
.       任意單一字元（除了 \n）
*       前面的元素出現 0 次或以上
+       前面的元素出現 1 次或以上（ERE）
?       前面的元素出現 0 或 1 次（ERE）
^       行首
$       行尾
[abc]   字元集合（a 或 b 或 c）
[a-z]   字元範圍
[^abc]  排除集合（不是 a b c）
\w      字母數字底線（= [a-zA-Z0-9_]）
\d      數字（= [0-9]）
\s      空白字元（空格、Tab、換行）
{n}     恰好 n 次（ERE）
{n,m}   n 到 m 次（ERE）
(abc)   群組（ERE）
a|b     a 或 b（ERE）
\b      單字邊界
```

## 常用 pattern 範例

```bash
# 找 IP 地址
grep -E "([0-9]{1,3}\.){3}[0-9]{1,3}" access.log

# 找 email
grep -E "[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}" file

# 找空白行
grep "^$" file
grep -v "^$" file    # 移除空白行

# 找以數字開頭的行
grep "^[0-9]" file

# 找包含 URL 的行
grep -E "https?://[^ ]+" file

# 找 ERROR 或 WARN（不分大小寫）
grep -iE "error|warn" /var/log/syslog

# 找某個 IP 的存取記錄
grep "^192\.168\.1\." access.log

# 在 .py 檔案裡找 import 語句
grep -rn "^import\|^from.*import" ./src/
```

## grep 效能技巧

```bash
# fgrep（= grep -F）：固定字串，不是 regex，快很多
grep -F "exact.string" file     # 不當 regex 解析，速度更快
fgrep "exact.string" file       # 等同

# 限制搜尋範圍
grep -m 1 "error" file          # 找到第一個就停
grep -l "error" *.log | head    # 先找有的檔案，再進一步分析

# --include / --exclude：只搜尋特定副檔名
grep -r "TODO" . --include="*.py"
grep -r "TODO" . --exclude="*.pyc" --exclude-dir="__pycache__"
```

## 動手練習

```bash
# 建立測試資料
cat > /tmp/test.log << 'EOF'
2024-01-15 09:00:01 INFO  Service started
2024-01-15 09:00:15 INFO  Database connected from 192.168.1.42
2024-01-15 09:05:32 WARN  Memory usage above 80%
2024-01-15 09:10:44 ERROR Connection timeout: db-server-01
2024-01-15 09:11:02 INFO  Retry connection
2024-01-15 09:11:15 ERROR Authentication failed: user-api
2024-01-15 09:12:00 INFO  Health check passed
EOF

# 1. 找所有 ERROR 行（顯示行號）
grep -n "ERROR" /tmp/test.log

# 2. 找所有 WARN 或 ERROR
grep -E "WARN|ERROR" /tmp/test.log

# 3. 找包含 IP 地址的行
grep -E "([0-9]{1,3}\.){3}[0-9]{1,3}" /tmp/test.log

# 4. 找 ERROR 行並顯示前後 1 行（context）
grep -C 1 "ERROR" /tmp/test.log

# 5. 統計各 log level 的數量
grep -oE "INFO|WARN|ERROR" /tmp/test.log | sort | uniq -c
```

## 自我檢核

- [ ] 知道 `-v`（反選）、`-c`（計數）、`-n`（行號）、`-r`（遞迴）是最常用的選項
- [ ] 理解 BRE 和 ERE 的差異：ERE 不需要反斜線就能用 `+`、`?`、`|`
- [ ] 能用 `-A/-B/-C` 顯示上下文
- [ ] 能用 `-o` 只輸出匹配的部分（配合其他工具做進一步處理）

→ [Ch 12 cut / sort / uniq / wc / tr](./12-cut-sort-uniq-wc-tr.md)
