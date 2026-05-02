# Ch 23 — 函式與作用域

> 目標：掌握 bash 函式的定義、`local` 作用域、回傳值的慣例，能寫出模組化的 script。

## 定義函式

```bash
# 寫法一（bash 偏好）
greet() {
    echo "Hello, $1!"
}

# 寫法二（`function` 關鍵字）
function greet {
    echo "Hello, $1!"
}
```

兩種都對。偏好第一種（更簡短、更 POSIX 相容）。

呼叫就像呼叫命令：

```bash
greet "Alice"      # Hello, Alice!
greet "World"      # Hello, World!
```

## 函式參數

函式內部用 `$1`、`$2`... 存取參數，和 script 參數一樣：

```bash
add() {
    echo $(( $1 + $2 ))
}

add 3 5    # 輸出 8
```

`$@` 是所有參數，`$#` 是參數個數：

```bash
print_all() {
    echo "Got $# args:"
    for arg in "$@"; do
        echo "  - $arg"
    done
}

print_all a b c    # Got 3 args: a, b, c
```

## 回傳值：bash 的陷阱

Bash 函式的 `return` 只能回傳 0-255 的整數（exit code），不能回傳字串。

```bash
# 錯誤：return 只能回傳 0-255
get_name() {
    return "Alice"    # 語法錯誤！
}

# 正確方式 1：echo 輸出，呼叫端用 $() 捕捉
get_name() {
    echo "Alice"
}
name=$(get_name)     # name="Alice"

# 正確方式 2：用全域變數（少用，容易搞混）
RESULT=""
get_name() {
    RESULT="Alice"
}
get_name
echo "$RESULT"

# return 用於 exit code（成功/失敗）
is_even() {
    (( $1 % 2 == 0 ))    # 偶數時 exit code = 0（true）
}

if is_even 4; then echo "even"; fi   # 輸出 even
if is_even 3; then echo "even"; fi   # 不輸出
```

## local：作用域

Bash 預設所有變數是全域的——函式內設定的變數會污染外面：

```bash
counter=0

increment() {
    counter=1    # 改了外面的 counter！
    temp=999     # 這個 temp 在函式外也看得到
}

increment
echo $counter    # 1（被函式改了）
echo $temp       # 999（跑到外面了）
```

用 `local` 限制作用域：

```bash
process() {
    local temp="$1"    # temp 只在函式內有效
    local result
    result=$(echo "$temp" | tr '[:lower:]' '[:upper:]')
    echo "$result"
}

process "hello"    # HELLO
echo "${temp:-empty}"  # 輸出 empty（temp 在外面不存在）
```

**規則：函式裡的所有變數都用 `local`，除非你明確想讓它影響外面。**

## $? 和 exit code

```bash
check_file() {
    local file="$1"
    if [[ ! -f "$file" ]]; then
        echo "Error: $file not found" >&2
        return 1    # 失敗
    fi
    echo "$file exists"
    return 0    # 成功（可省略，函式最後一個命令的 exit code 就是回傳值）
}

if check_file /etc/passwd; then
    echo "check passed"
else
    echo "check failed"
fi
```

## 實用 Pattern

### 函式庫：把函式放進 lib.sh

```bash
# lib.sh
log_info()  { echo "[INFO]  $(date '+%H:%M:%S') $*"; }
log_warn()  { echo "[WARN]  $(date '+%H:%M:%S') $*" >&2; }
log_error() { echo "[ERROR] $(date '+%H:%M:%S') $*" >&2; }

require_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "This script must be run as root"
        exit 1
    fi
}

require_command() {
    local cmd="$1"
    if ! command -v "$cmd" &>/dev/null; then
        log_error "Required command '$cmd' not found"
        exit 1
    fi
}
```

```bash
# main.sh
source ./lib.sh

require_root
require_command rsync
log_info "Starting backup..."
```

### 函式當 main

```bash
#!/usr/bin/env bash

setup() {
    local dir="$1"
    mkdir -p "$dir"
    chmod 700 "$dir"
    log_info "Created $dir"
}

cleanup() {
    log_info "Cleaning up..."
    rm -rf "$TMPDIR"
}

main() {
    TMPDIR=$(mktemp -d)
    trap cleanup EXIT    # 結束時自動清理

    setup "$TMPDIR/data"
    setup "$TMPDIR/logs"

    echo "Working in $TMPDIR"
}

main "$@"
```

## 動手練習

```bash
# 1. 寫一個 parse_date 函式
parse_date() {
    local date_str="$1"
    local year month day
    IFS=- read -r year month day <<< "$date_str"
    echo "Year: $year, Month: $month, Day: $day"
}
parse_date "2024-01-15"

# 2. 用函式封裝重複邏輯
ensure_dir() {
    local dir="$1"
    if [[ ! -d "$dir" ]]; then
        mkdir -p "$dir"
        echo "Created: $dir"
    else
        echo "Already exists: $dir"
    fi
}

for d in /tmp/test/a /tmp/test/b /tmp/test/c; do
    ensure_dir "$d"
done

# 3. local 作用域測試
outer_var="outer"
test_scope() {
    local outer_var="inner"
    echo "Inside: $outer_var"
}
test_scope
echo "Outside: $outer_var"   # 應該是 outer

# 4. 函式回傳狀態碼
is_port_valid() {
    local port="$1"
    (( port >= 1 && port <= 65535 ))
}

for p in 22 80 0 70000 443; do
    if is_port_valid "$p"; then
        echo "Port $p: valid"
    else
        echo "Port $p: INVALID"
    fi
done
```

## 自我檢核

- [ ] 知道 bash 函式的 `return` 只能回傳整數（exit code）
- [ ] 知道要回傳字串，要用 `echo` + `$()` 捕捉的模式
- [ ] 記住函式內所有臨時變數都加 `local`
- [ ] 能用 `source` 載入函式庫

→ [Ch 24 陣列與字串處理](./24-arrays-and-strings.md)
