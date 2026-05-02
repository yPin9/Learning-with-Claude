# Ch 22 — 迴圈

> 目標：掌握 for/while/until 三種迴圈，理解各自的適用場景，能寫出正確的 break/continue。

## for 迴圈

### 遍歷列表

```bash
for item in a b c d; do
    echo "$item"
done

# 遍歷檔案
for f in /etc/*.conf; do
    echo "config: $f"
done

# 遍歷陣列
fruits=("apple" "banana" "cherry")
for fruit in "${fruits[@]}"; do
    echo "$fruit"
done
```

### 遍歷命令輸出

```bash
for user in $(cut -d: -f1 /etc/passwd); do
    echo "user: $user"
done

# 更安全的寫法（處理含空格的行）
while IFS= read -r user; do
    echo "user: $user"
done < <(cut -d: -f1 /etc/passwd)
```

`$(cut ...)` 有 word splitting 問題，如果輸出含空格就出錯。`while read` 更穩。

### C-style for（數字範圍）

```bash
for ((i=0; i<10; i++)); do
    echo $i
done

for ((i=10; i>=0; i--)); do
    echo $i
done

# 或用 seq
for i in $(seq 1 10); do
    echo $i
done

# 或用 brace expansion
for i in {1..10}; do
    echo $i
done

for i in {0..100..10}; do    # 步進 10
    echo $i
done
```

## while 迴圈

條件為 true（exit code 0）就繼續：

```bash
count=0
while [[ $count -lt 5 ]]; do
    echo "count: $count"
    ((count++))
done

# 無窮迴圈（配合 break）
while true; do
    read -p "Input (q to quit): " line
    [[ "$line" == "q" ]] && break
    echo "You said: $line"
done
```

### while read：逐行讀檔案

這是 bash 最重要的 pattern 之一：

```bash
while IFS= read -r line; do
    echo "line: $line"
done < /etc/passwd

# 從命令輸出讀
while IFS= read -r line; do
    echo ">> $line"
done < <(grep "error" /var/log/syslog)

# 讀 CSV 的各欄
while IFS=, read -r name age dept; do
    echo "Name=$name Age=$age Dept=$dept"
done < employees.csv
```

`IFS=` 防止前後空白被 strip；`-r` 防止反斜線被吞掉。不寫這兩個你會後悔。

## until 迴圈

條件為 false（exit code 非 0）就繼續——和 while 相反：

```bash
# 等服務啟動
until systemctl is-active nginx &>/dev/null; do
    echo "waiting for nginx..."
    sleep 2
done
echo "nginx is up!"

# 等檔案出現
until [[ -f /tmp/done.flag ]]; do
    sleep 1
done
echo "flag appeared!"
```

`until` 其實就是 `while !`，純粹語意上更清楚「等到某個條件成立」。

## break / continue

```bash
# break：跳出迴圈
for i in {1..10}; do
    if [[ $i -eq 5 ]]; then
        break
    fi
    echo $i
done
# 輸出 1 2 3 4

# continue：跳過這次，繼續下一次
for i in {1..10}; do
    if (( i % 2 == 0 )); then
        continue
    fi
    echo $i
done
# 輸出 1 3 5 7 9

# break N：跳出 N 層迴圈
for i in 1 2 3; do
    for j in a b c; do
        if [[ "$i$j" == "2b" ]]; then
            break 2    # 跳出兩層
        fi
        echo "$i$j"
    done
done
```

## 實用 Pattern

```bash
# 批次處理檔案
for f in /var/log/*.log; do
    [[ -f "$f" ]] || continue    # 跳過不是普通檔案的
    echo "Processing $f ($(wc -l < "$f") lines)"
done

# 重試機制
max_retries=3
attempt=0
while (( attempt < max_retries )); do
    if ./deploy.sh; then
        echo "Deploy succeeded"
        break
    fi
    ((attempt++))
    echo "Attempt $attempt failed, retrying..."
    sleep 5
done

(( attempt >= max_retries )) && echo "All retries failed" && exit 1

# 等待並輸出進度
for i in $(seq 1 100); do
    printf "\rProgress: %3d%%" $i
    sleep 0.1
done
echo    # 最後補換行
```

## 動手練習

```bash
# 1. 計算 1 到 100 的總和
sum=0
for i in {1..100}; do
    ((sum += i))
done
echo "Sum: $sum"   # 5050

# 2. 找 /etc 下最大的 5 個檔案
for f in /etc/*; do
    [[ -f "$f" ]] && du -sh "$f" 2>/dev/null
done | sort -rh | head -5

# 3. 逐行讀 /etc/passwd，找 UID > 1000 的使用者
while IFS=: read -r user _ uid _ _ home shell; do
    if (( uid > 1000 )) && [[ "$shell" != "/usr/sbin/nologin" ]]; then
        echo "Regular user: $user (uid=$uid, home=$home)"
    fi
done < /etc/passwd

# 4. 批次建立目錄和檔案
for dept in hr it finance; do
    mkdir -p "/tmp/company/$dept"
    for person in alice bob carol; do
        touch "/tmp/company/$dept/${person}.txt"
    done
done
find /tmp/company -type f | sort

# 清理
rm -rf /tmp/company
```

## 自我檢核

- [ ] 偏好 `while IFS= read -r line` 逐行讀檔，而不是 `for line in $(cat)`
- [ ] 知道 `{1..10}` 和 `$(seq 1 10)` 的差異（前者是 brace expansion，前者更快）
- [ ] 能用 `until` 寫「等到某事發生」的 polling loop
- [ ] 知道 `break 2` 可以跳出多層迴圈

→ [Ch 23 函式與作用域](./23-functions-and-scope.md)
