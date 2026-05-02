# Ch 12 — cut / sort / uniq / wc / tr

> 目標：掌握這五個文字處理工具，能組合成 pipeline 做欄位提取、排序去重、計數、字元轉換。

## cut：切割欄位

`cut` 從每行取出特定欄位或字元範圍：

```bash
# -d = delimiter（分隔符），-f = field（欄位）
cut -d: -f1 /etc/passwd          # 取第 1 欄（使用者名稱）
cut -d: -f1,3 /etc/passwd        # 取第 1 和 3 欄
cut -d: -f1-3 /etc/passwd        # 取第 1 到 3 欄
cut -d, -f2 data.csv             # CSV 的第 2 欄

# -c = characters（字元位置）
cut -c1-10 file.txt              # 取每行前 10 個字元
cut -c5- file.txt                # 從第 5 個字元到行尾

# 處理 Tab 分隔
cut -f2 tsv_file.txt             # -f2 預設 delimiter 是 Tab
```

`cut` 只能做簡單的固定分隔符切割，欄位寬度不均勻或有引號的 CSV 就要用 `awk`。

## sort：排序

```bash
sort file.txt                    # 按字母升序
sort -r file.txt                 # -r = reverse 降序
sort -n numbers.txt              # -n = numeric，按數值排序（不是字串）
sort -rn numbers.txt             # 數值降序
sort -k2 file.txt                # -k = key，按第 2 欄排序
sort -k2 -t: /etc/passwd         # -t = 指定分隔符，按 UID 欄排序
sort -k2 -t: -n /etc/passwd      # 數值排序 UID
sort -u file.txt                 # -u = unique，排序後去重
sort -h sizes.txt                # -h = human-numeric，理解 1K 2M 等
```

多欄位排序：

```bash
sort -k1,1 -k2,2n file.txt       # 先按第 1 欄字母，再按第 2 欄數字
# -k1,1 代表「第 1 欄開始，第 1 欄結束」（只用第 1 欄）
# 如果只寫 -k1 代表「第 1 欄到行尾」
```

## uniq：去除重複行

**注意：`uniq` 只去除相鄰的重複行，用之前通常要先 `sort`。**

```bash
sort file.txt | uniq             # 排序再去重（標準用法）
sort file.txt | uniq -c          # -c = count，每個值出現幾次（頻率統計）
sort file.txt | uniq -d          # -d = duplicate，只顯示重複的行
sort file.txt | uniq -u          # -u = unique，只顯示沒有重複的行
```

頻率統計是最常用的 pattern：

```bash
# 統計哪個 IP 出現最多次
grep -oE "([0-9]{1,3}\.){3}[0-9]{1,3}" access.log | sort | uniq -c | sort -rn | head -10
```

## wc：計算

```bash
wc file.txt                      # 行數 字數 bytes（三個一起）
wc -l file.txt                   # 只顯示行數
wc -w file.txt                   # 只顯示字數（空白分隔）
wc -c file.txt                   # 只顯示 bytes
wc -m file.txt                   # -m = characters（處理多 byte UTF-8）
wc -l *.log                      # 多個檔案，最後一行是總計

# 常見用法：統計行數
ls /etc | wc -l                  # /etc 有幾個項目
grep -c "error" app.log          # 等同 grep "error" | wc -l
```

## tr：字元轉換

`tr` 從 stdin 讀，對字元做轉換，輸出到 stdout（不支援直接處理檔案）：

```bash
echo "hello" | tr 'a-z' 'A-Z'   # 小寫轉大寫
echo "HELLO" | tr 'A-Z' 'a-z'   # 大寫轉小寫

tr -d '\n' < file.txt            # -d = delete，刪除換行，把多行合成一行
tr -d '[:space:]' < file.txt     # 刪除所有空白字元
tr -s ' ' < file.txt             # -s = squeeze，多個連續空格壓縮成一個
tr ':' '\n' <<< "$PATH"          # 把 PATH 的冒號換成換行，一行一個路徑
tr -cd '[:digit:]' < file.txt    # -c = complement（取補集），只保留數字

echo "Hello World" | tr -d 'aeiou'  # 刪除母音
# Hll Wrld
```

## 實用組合

```bash
# 統計 /etc/passwd 裡有幾個不同的 shell
cut -d: -f7 /etc/passwd | sort | uniq -c | sort -rn

# 找 access.log 裡回應最慢的 10 個請求（假設最後一欄是回應時間）
awk '{print $NF}' access.log | sort -n | tail -10

# 把 CSV 的第一欄提取出來，轉大寫，去重排序
cut -d, -f1 data.csv | tr 'a-z' 'A-Z' | sort | uniq

# 統計每個小時的請求數（access log 格式：[day/Mon/year:HH:MM:SS]）
grep -oE "\d{2}:\d{2}:\d{2}" access.log | cut -d: -f1 | sort | uniq -c

# 找 /etc/passwd 裡 UID > 1000 的使用者
cut -d: -f1,3 /etc/passwd | awk -F: '$2 > 1000 {print $1}'
```

## 動手練習

```bash
# 建立測試資料
cat > /tmp/scores.txt << 'EOF'
Alice 85 IT
Bob 72 HR
Carol 91 IT
Dave 88 IT
Eve 65 HR
Frank 79 IT
Grace 93 HR
EOF

# 1. 只取名字欄（第 1 欄）
cut -d' ' -f1 /tmp/scores.txt

# 2. 找 IT 部門的成員，按分數降序
grep "IT" /tmp/scores.txt | sort -k2 -n -r

# 3. 統計每個部門有幾人
cut -d' ' -f3 /tmp/scores.txt | sort | uniq -c

# 4. 計算 IT 部門的平均分數（用 awk 輔助）
grep "IT" /tmp/scores.txt | cut -d' ' -f2 | \
    awk '{sum+=$1; count++} END {print "平均:", sum/count}'

# 5. 把所有名字轉大寫
cut -d' ' -f1 /tmp/scores.txt | tr 'a-z' 'A-Z'
```

## 自我檢核

- [ ] 知道 `cut -d: -f1` 的 `-d` 指定分隔符、`-f` 指定欄位
- [ ] 記住 `uniq` 只去相鄰重複，要先 `sort`
- [ ] 能用 `sort | uniq -c | sort -rn` 做頻率統計
- [ ] 知道 `tr` 只能從 stdin 讀（不能直接給檔名）

→ [Ch 13 sed：流式編輯器](./13-sed.md)
