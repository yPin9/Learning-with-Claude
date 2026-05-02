# Ch 14 — awk：欄位處理引擎

> 目標：掌握 awk 的欄位變數、內建變數、BEGIN/END、條件與迴圈，能用它做欄位計算、統計、格式化輸出。

## awk 的工作模型

```
對每一行：
  把行拆成欄位（$1 $2 $3 ... $NF）
  執行符合條件的指令
```

```bash
awk '程式' file.txt
awk -F: '程式' file.txt    # -F = 指定 field separator（預設空白）
awk -f script.awk file.txt # 從檔案讀程式
```

awk 程式的結構：

```awk
BEGIN { 初始化 }      # 讀任何行之前執行一次
/pattern/ { 指令 }    # 每行如果符合 pattern 就執行
condition { 指令 }    # 每行如果 condition 為真就執行
END { 收尾 }          # 所有行讀完後執行一次
```

## 欄位變數

```bash
echo "Alice 30 IT" | awk '{print $1}'       # Alice
echo "Alice 30 IT" | awk '{print $2, $3}'   # 30 IT
echo "Alice 30 IT" | awk '{print $NF}'      # IT（最後一欄）
echo "Alice 30 IT" | awk '{print $(NF-1)}'  # 30（倒數第 2 欄）
echo "Alice 30 IT" | awk '{print $0}'       # 整行
```

## 內建變數

| 變數 | 說明 |
|------|------|
| `$0` | 整行 |
| `$1`–`$NF` | 各欄位 |
| `NF` | 當前行的欄位數 |
| `NR` | 當前行號（所有檔案累計）|
| `FNR` | 當前行號（單檔案內）|
| `FS` | 輸入分隔符（預設空白）|
| `OFS` | 輸出分隔符（預設空白）|
| `RS` | 輸入記錄分隔符（預設換行）|
| `ORS` | 輸出記錄分隔符（預設換行）|
| `FILENAME` | 目前處理的檔案名 |

## 常用 pattern

```bash
# 不給 condition：對每行都執行
awk '{print $1}' file

# regex pattern
awk '/error/{print}' file            # 包含 error 的行
awk '!/^#/{print}' file              # 不是 # 開頭的行

# 比較
awk '$3 > 80 {print $1, $3}' scores.txt    # 第 3 欄 > 80
awk 'NR > 1 {print}' file                  # 跳過第 1 行（表頭）
awk 'NR >= 5 && NR <= 10 {print}' file    # 第 5 到 10 行

# 字串比較
awk '$3 == "IT" {print $1}' file           # 第 3 欄是 "IT"
awk '$1 ~ /^A/ {print}' file               # 第 1 欄以 A 開頭（~ = regex 匹配）
awk '$1 !~ /^A/ {print}' file              # 第 1 欄不以 A 開頭
```

## BEGIN 和 END

```bash
# 加表頭和統計
awk 'BEGIN {print "Name Score"} {print $1, $2} END {print "Done"}' scores.txt

# 計算總和和平均
awk '{sum += $2; count++} END {printf "Total: %d, Average: %.1f\n", sum, sum/count}' scores.txt

# 指定 OFS（輸出分隔符）
awk 'BEGIN {OFS=","} {print $1, $2, $3}' file    # 輸出 CSV
```

## 計算和累加

```bash
# 統計每個部門的人數
awk '{dept[$3]++} END {for (d in dept) print d, dept[d]}' employees.txt

# 計算每個部門的平均分數
awk '{
    dept[$3] += $2
    count[$3]++
} END {
    for (d in dept)
        printf "%s: %.1f\n", d, dept[d]/count[d]
}' scores.txt

# 求最大值和最小值
awk 'NR==1 || $2 > max {max=$2; name=$1} END {print "最高分:", name, max}' scores.txt
```

## printf 格式化輸出

```bash
awk '{printf "%-15s %5d %s\n", $1, $2, $3}' file
# %-15s = 左對齊 15 字元的字串
# %5d = 右對齊 5 字元的整數
# %.2f = 2 位小數的浮點數
```

## 常用場景

```bash
# /etc/passwd：列出 UID > 1000 的使用者和家目錄
awk -F: '$3 > 1000 {print $1, $6}' /etc/passwd

# access.log：計算每個 IP 的請求數
awk '{count[$1]++} END {for (ip in count) print count[ip], ip}' access.log | sort -rn | head

# 計算 CSV 某欄的總和
awk -F, '{sum += $3} END {print "Total:", sum}' data.csv

# 轉換欄位順序（把 col2 col1 col3 改成 col1 col3 col2）
awk '{print $2, $1, $3}' file

# 過濾並重新格式化（把 "name age dept" 轉成 "dept: name (age)")
awk '{printf "%s: %s (%s)\n", $3, $1, $2}' employees.txt

# 找出 log 裡回應時間 > 1000ms 的請求
awk '$NF > 1000 {print $0}' access.log

# 條件性替換（第 2 欄 < 60 改成 "FAIL"）
awk '{if ($2 < 60) $2 = "FAIL"; print}' scores.txt
```

## 動手練習

```bash
cat > /tmp/employees.txt << 'EOF'
Alice 85 IT 65000
Bob 72 HR 55000
Carol 91 IT 75000
Dave 88 IT 70000
Eve 65 HR 52000
Frank 79 IT 68000
Grace 93 HR 58000
EOF

# 1. 列出 IT 部門，按薪資降序
awk '$3 == "IT" {print $1, $4}' /tmp/employees.txt | sort -k2 -n -r

# 2. 計算每個部門的平均薪資
awk '{
    salary[$3] += $4
    count[$3]++
} END {
    for (dept in salary)
        printf "%s 平均薪資: %d\n", dept, salary[dept]/count[dept]
}' /tmp/employees.txt

# 3. 找出評分最高的員工
awk 'NR==1 || $2 > max {max=$2; name=$1} END {print "最高分:", name, max}' /tmp/employees.txt

# 4. 生成 CSV 格式報告（加表頭）
awk 'BEGIN {
    OFS=","
    print "Name,Score,Department,Salary"
}
{print $1,$2,$3,$4}' /tmp/employees.txt
```

## 自我檢核

- [ ] 記住 `$1`、`$NF`、`NR`、`FS`、`OFS` 這幾個內建變數
- [ ] 能用關聯陣列（`arr[$key]++`）做分組統計
- [ ] 知道 BEGIN 在讀任何行之前執行，END 在所有行讀完後執行
- [ ] 能用 `printf` 做格式化輸出

→ [練習 B：日誌分析 pipeline](./practice-b-log-analysis.md)
