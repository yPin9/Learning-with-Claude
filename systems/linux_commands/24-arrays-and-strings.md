# Ch 24 — 陣列與字串處理

> 目標：掌握 bash 的 indexed array 和 associative array，以及 `${var#...}` 系列的字串操作語法。

## Indexed Array

```bash
# 建立
fruits=("apple" "banana" "cherry")

# 存取
echo "${fruits[0]}"     # apple（index 從 0 開始）
echo "${fruits[1]}"     # banana
echo "${fruits[-1]}"    # cherry（-1 = 最後一個）

# 所有元素
echo "${fruits[@]}"     # apple banana cherry
echo "${fruits[*]}"     # 同上，但在雙引號內行為不同

# 元素個數
echo "${#fruits[@]}"    # 3

# 所有 index
echo "${!fruits[@]}"    # 0 1 2

# 修改和新增
fruits[1]="blueberry"
fruits+=("date")         # 新增到結尾

# 刪除元素
unset fruits[2]          # 刪除 index 2，但不壓縮（留空洞）
```

### `"${arr[@]}"` vs `"${arr[*]}"`

```bash
arr=("hello world" "foo" "bar")

# 用 "@" 在雙引號裡：每個元素獨立
for item in "${arr[@]}"; do
    echo "item: $item"
done
# item: hello world
# item: foo
# item: bar

# 用 "*" 在雙引號裡：所有元素合成一個字串
for item in "${arr[*]}"; do
    echo "item: $item"
done
# item: hello world foo bar（一整個）
```

**永遠用 `"${arr[@]}"`，不要用 `"${arr[*]}"`。**

## Associative Array（bash 4+）

```bash
# 必須先宣告
declare -A ages

ages["alice"]=30
ages["bob"]=25
ages["carol"]=35

echo "${ages[alice]}"    # 30
echo "${ages[@]}"        # 所有值
echo "${!ages[@]}"       # 所有 key
echo "${#ages[@]}"       # key 的個數

# 一次建立
declare -A config=(
    [host]="db.example.com"
    [port]="5432"
    [db]="production"
)

echo "${config[host]}"   # db.example.com

# 迭代（key 的順序不保證）
for key in "${!config[@]}"; do
    echo "$key = ${config[$key]}"
done
```

## 字串操作：`${var...}` 語法

這是 bash 最強大也最常被忽略的功能：

### 長度

```bash
str="hello world"
echo "${#str}"           # 11
```

### 子字串擷取

```bash
str="hello world"
echo "${str:6}"          # world（從 index 6 到結尾）
echo "${str:6:3}"        # wor（從 index 6 取 3 個字元）
echo "${str:0:5}"        # hello（前 5 個）
echo "${str: -5}"        # world（最後 5 個，注意空格）
```

### 前綴/後綴刪除

```bash
path="/usr/local/bin/bash"

# # = 刪最短前綴，## = 刪最長前綴
echo "${path#*/}"        # usr/local/bin/bash（刪掉第一個 /）
echo "${path##*/}"       # bash（刪掉最長匹配，= dirname 效果）

# % = 刪最短後綴，%% = 刪最長後綴
echo "${path%/*}"        # /usr/local/bin（= dirname 效果）
echo "${path%%/*}"       # （空，/ 是最長後綴）

# 常見用途
filename="report.tar.gz"
echo "${filename%%.*}"   # report（去掉所有副檔名）
echo "${filename%.*}"    # report.tar（只去最後一個）
echo "${filename##*.}"   # gz（只取最後一個副檔名）
```

### 替換

```bash
str="hello world world"
echo "${str/world/earth}"    # hello earth world（替換第一個）
echo "${str//world/earth}"   # hello earth earth（替換所有）
echo "${str/#hello/Hi}"      # Hi world world（替換前綴）
echo "${str/%world/globe}"   # hello world globe（替換後綴）
```

### 大小寫轉換（bash 4+）

```bash
str="Hello World"
echo "${str,,}"    # hello world（全小寫）
echo "${str^^}"    # HELLO WORLD（全大寫）
echo "${str,}"     # hello World（第一個字小寫）
echo "${str^}"     # Hello World（第一個字大寫）
```

### 預設值

```bash
# ${var:-default}：var 未設定或空時用 default
echo "${name:-anonymous}"       # 如果 name 空，用 anonymous
echo "${PORT:-8080}"            # 常見於設定預設 port

# ${var:=default}：同上，但也把 default 賦值給 var
echo "${TMPDIR:=/tmp}"          # 設定 TMPDIR 的同時提供預設

# ${var:?message}：未設定時印 message 並 exit
echo "${REQUIRED_VAR:?must be set}"  # 強制要求環境變數
```

## 實用 Pattern

```bash
# 批次處理檔案名
for f in /var/log/*.log; do
    base="${f##*/}"          # 取得檔名（去路徑）
    name="${base%.log}"      # 去掉 .log
    echo "Processing: $name"
done

# 解析 URL
url="https://api.example.com/v1/users?page=2"
protocol="${url%%://*}"      # https
host="${url#*://}"
host="${host%%/*}"           # api.example.com
path="${url#*://*/}"         # v1/users?page=2
```

## 動手練習

```bash
# 1. 建立一個 associative array 存設定，並迭代輸出
declare -A db_config=(
    [host]="localhost"
    [port]="5432"
    [user]="admin"
    [name]="myapp"
)

for key in "${!db_config[@]}"; do
    printf "%-10s = %s\n" "$key" "${db_config[$key]}"
done

# 2. 字串操作
path="/home/alice/documents/report.pdf"
echo "Base name: ${path##*/}"      # report.pdf
echo "Dir name:  ${path%/*}"       # /home/alice/documents
echo "No ext:    ${path%.*}"       # /home/alice/documents/report
echo "Ext only:  ${path##*.}"      # pdf

# 3. 大寫轉換日誌等級
for level in info warn error debug; do
    echo "[${level^^}] message"
done

# 4. 從 indexed array 過濾元素
servers=("web-01" "db-01" "web-02" "cache-01" "web-03")
web_servers=()
for s in "${servers[@]}"; do
    [[ "$s" == web-* ]] && web_servers+=("$s")
done
echo "Web servers: ${web_servers[*]}"
```

## 自我檢核

- [ ] 能用 `"${arr[@]}"` 安全地遍歷陣列（和 `"${arr[*]}"` 的差異）
- [ ] 記住 `##*/` 取檔名、`%/*` 取目錄的慣用法
- [ ] 知道 `${var:-default}` 用來提供預設值
- [ ] 能用 `declare -A` 建立 associative array

→ [Ch 25 參數與特殊變數](./25-parameters-and-special-vars.md)
