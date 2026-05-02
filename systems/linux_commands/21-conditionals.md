# Ch 21 — 條件判斷

> 目標：掌握 `if`/`elif`/`else` 語法，理解 `[[ ]]` 與 `[ ]` 的差異，熟悉常用的測試運算子。

## Exit Code：一切的基礎

Bash 的條件判斷建立在 **exit code** 上：

```
0   = 成功（true）
非 0 = 失敗（false）
```

`if` 其實是判斷命令的 exit code：

```bash
if ls /tmp; then
    echo "ls succeeded"
fi

if grep -q "error" /var/log/syslog; then
    echo "errors found"
fi
```

## if / elif / else

```bash
if [[ condition ]]; then
    ...
elif [[ other_condition ]]; then
    ...
else
    ...
fi
```

注意：`then` 跟 `if` 之間要有分號或換行。這是 bash 語法規定，不是習慣問題。

```bash
# 都正確
if [[ -f file ]]; then echo "exists"; fi   # 一行寫
if [[ -f file ]]
then
    echo "exists"
fi
```

## [[ ]] vs [ ]

**用 `[[ ]]`，不要用 `[ ]`。** 除非你要寫 POSIX sh。

| 比較 | `[ ]`（POSIX sh）| `[[ ]]`（bash 內建）|
|------|----------------|-------------------|
| 含空格的變數 | 必須加引號 | 不加引號也安全 |
| `&&` / `\|\|` | 不支援，用 `-a` / `-o` | 直接用 `&&` / `\|\|` |
| regex 比對 | 不支援 | `=~` 運算子 |
| glob 比對 | 不支援 | `==` 支援 glob |
| 效能 | 外部命令 | bash 內建，更快 |

```bash
# [ ] 的陷阱
file="my file.txt"
[ -f $file ]      # 錯：等同 [ -f my file.txt ]（多一個詞）
[ -f "$file" ]    # 對

# [[ ]] 沒這個問題
[[ -f $file ]]    # 對，自動處理空格
```

## 測試運算子

### 檔案測試

```bash
[[ -e file ]]    # exists（存在，不管是什麼類型）
[[ -f file ]]    # is a regular file（普通檔案）
[[ -d file ]]    # is a directory
[[ -l file ]]    # is a symbolic link
[[ -r file ]]    # readable
[[ -w file ]]    # writable
[[ -x file ]]    # executable
[[ -s file ]]    # size > 0（非空）
[[ -z file ]]    # 注意：-z 是測試字串長度，不是檔案
```

### 字串比較

```bash
[[ "$a" == "$b" ]]   # 相等
[[ "$a" != "$b" ]]   # 不相等
[[ -z "$a" ]]        # 空字串（zero length）
[[ -n "$a" ]]        # 非空字串（non-zero length）
[[ "$a" < "$b" ]]    # 字典序比較（小於）
[[ "$a" > "$b" ]]    # 字典序比較（大於）

# glob 比對
[[ "$file" == *.txt ]]     # 是 .txt 結尾

# regex 比對（=~ 是 bash 內建的 ERE）
[[ "$email" =~ ^[a-z]+@[a-z]+\.[a-z]+$ ]]
[[ "$version" =~ ^[0-9]+\.[0-9]+$ ]]
```

### 數值比較

```bash
[[ $a -eq $b ]]    # equal
[[ $a -ne $b ]]    # not equal
[[ $a -lt $b ]]    # less than
[[ $a -le $b ]]    # less than or equal
[[ $a -gt $b ]]    # greater than
[[ $a -ge $b ]]    # greater than or equal

# 也可以用算術展開
(( a > b ))        # 更直觀
(( a == 0 ))
```

### 邏輯組合

```bash
[[ -f file && -r file ]]     # AND
[[ -f file || -d file ]]     # OR
[[ ! -f file ]]              # NOT
```

## case 語句

```bash
case "$extension" in
    *.txt|*.md)
        echo "text file"
        ;;
    *.png|*.jpg|*.gif)
        echo "image file"
        ;;
    *.tar.gz|*.tgz)
        echo "archive"
        ;;
    *)
        echo "unknown: $extension"
        ;;
esac
```

`case` 用 glob 比對，比一堆 `elif [[ == ]]` 更清楚。

## 常見 Script 模式

```bash
#!/usr/bin/env bash

# 檢查 root 權限
if [[ $EUID -ne 0 ]]; then
    echo "This script must be run as root" >&2
    exit 1
fi

# 檢查命令是否存在
if ! command -v git &>/dev/null; then
    echo "git is not installed" >&2
    exit 1
fi

# 檔案是否存在且非空
if [[ ! -s /tmp/data.txt ]]; then
    echo "data file is missing or empty" >&2
    exit 1
fi

# 字串非空
if [[ -z "$API_KEY" ]]; then
    echo "API_KEY is not set" >&2
    exit 1
fi
```

## 動手練習

```bash
# 1. 寫一個 script 檢查某個 port 是否在監聽
cat > /tmp/check-port.sh << 'EOF'
#!/usr/bin/env bash
port="${1:-80}"
if ss -tlnp | grep -q ":${port} "; then
    echo "Port $port is OPEN"
else
    echo "Port $port is CLOSED"
fi
EOF
chmod +x /tmp/check-port.sh
/tmp/check-port.sh 22
/tmp/check-port.sh 99999

# 2. 測試 =~ regex
for email in "alice@example.com" "not-an-email" "bob@test.org"; do
    if [[ "$email" =~ ^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$ ]]; then
        echo "$email: valid"
    else
        echo "$email: INVALID"
    fi
done

# 3. case 分類檔案
for f in /etc/passwd /bin/ls /var/log /usr/lib/libc.so; do
    case "$f" in
        *.so|*.so.*)  echo "$f: shared library" ;;
        /etc/*)       echo "$f: config file" ;;
        /bin/*|/usr/bin/*) echo "$f: executable" ;;
        *)            echo "$f: other" ;;
    esac
done
```

## 自我檢核

- [ ] 記住 bash 的 true/false 是 exit code 0/非 0
- [ ] 偏好 `[[ ]]` 而不是 `[ ]`，知道原因
- [ ] 能區分 `-eq`（數值）和 `==`（字串）
- [ ] 能用 `=~` 做 regex 比對

→ [Ch 22 迴圈](./22-loops.md)
