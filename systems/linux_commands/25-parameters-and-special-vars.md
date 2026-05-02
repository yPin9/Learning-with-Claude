# Ch 25 — 參數與特殊變數

> 目標：掌握位置參數 `$0`–`$9`、`$@`/`$*`/`$#`，理解 `$?`/`$$`/`$!`，能用 `getopts` 解析 flag。

## 位置參數回顧

```bash
#!/usr/bin/env bash
# script.sh arg1 arg2 arg3

echo "$0"    # ./script.sh（script 名字）
echo "$1"    # arg1
echo "$2"    # arg2
echo "$3"    # arg3
echo "$#"    # 3（參數個數）
echo "$@"    # arg1 arg2 arg3（各自獨立）
echo "$*"    # arg1 arg2 arg3（合成一個字串）
```

## `$@` vs `$*`

在雙引號裡行為不同（重要）：

```bash
args=("hello world" "foo" "bar")

# "$@"：每個元素是獨立的詞
set -- "hello world" "foo" "bar"
for a in "$@"; do echo "[$a]"; done
# [hello world]
# [foo]
# [bar]

# "$*"：所有元素合成一個字串（用 IFS 分隔）
for a in "$*"; do echo "[$a]"; done
# [hello world foo bar]（一整個）
```

傳遞參數給函式時，用 `"$@"` 保留每個參數的完整性：

```bash
process_files() {
    for f in "$@"; do
        echo "Processing: $f"
    done
}

process_files "file with spaces.txt" "normal.txt"
# 正確：兩個分開的參數
```

## 特殊狀態變數

```bash
$?    # 上一個命令的 exit code
$$    # 當前 shell/script 的 PID
$!    # 上一個背景行程的 PID
$_    # 上一個命令的最後一個參數
$-    # 當前 shell 的 option flags
PPID  # 父行程 PID（不是 $PPID，是環境變數）
```

### `$?` 的常見用法

```bash
make build
if [[ $? -ne 0 ]]; then
    echo "Build failed"
    exit 1
fi

# 更簡潔：直接用 if
if ! make build; then
    echo "Build failed"
    exit 1
fi
```

### `$$` 建立唯一暫時檔名

```bash
tmpfile="/tmp/myapp-$$.tmp"    # PID 保證唯一
trap "rm -f $tmpfile" EXIT
echo "data" > "$tmpfile"
```

`mktemp` 更安全，但 `$$` 在需要固定前綴時有用。

### `$!` 管理背景行程

```bash
./task1.sh &
PID1=$!
./task2.sh &
PID2=$!

wait $PID1; echo "task1 done with code $?"
wait $PID2; echo "task2 done with code $?"
```

## shift：消耗位置參數

```bash
#!/usr/bin/env bash
while [[ $# -gt 0 ]]; do
    echo "Argument: $1"
    shift          # 把 $2 變成 $1，$3 變成 $2，以此類推
done
```

`shift N` 一次消耗 N 個。手動解析 option 時很有用。

## getopts：解析 flag

```bash
#!/usr/bin/env bash
# 用法：./script.sh -v -o output.txt input.txt

verbose=false
output=""

while getopts "vo:" opt; do
    case "$opt" in
        v) verbose=true ;;
        o) output="$OPTARG" ;;    # OPTARG 是 flag 後面的值
        ?) echo "Usage: $0 [-v] [-o output] input" >&2; exit 1 ;;
    esac
done

shift $((OPTIND - 1))    # 消耗掉所有 option，剩下的是位置參數
input_files=("$@")

echo "verbose: $verbose"
echo "output: $output"
echo "input files: ${input_files[@]}"
```

`getopts` 語法說明：
- `"vo:"` 裡的 `v` 是沒有參數的 flag，`o:` 的冒號代表後面要有參數
- 開頭加 `:` 變成 silent mode：`":vo:"` 讓你自己處理錯誤

```bash
# silent mode：更細緻的錯誤處理
while getopts ":vo:h" opt; do
    case "$opt" in
        v) verbose=true ;;
        o) output="$OPTARG" ;;
        h) usage; exit 0 ;;
        :) echo "Error: -$OPTARG requires an argument" >&2; exit 1 ;;
        \?) echo "Error: unknown flag -$OPTARG" >&2; exit 1 ;;
    esac
done
```

## 完整 Script 模板

```bash
#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS] <input>

OPTIONS:
  -v          Verbose output
  -o FILE     Output file (default: stdout)
  -n COUNT    Max lines (default: all)
  -h          Show this help

EXAMPLES:
  $(basename "$0") -v -o out.txt input.txt
  $(basename "$0") -n 100 big.log
EOF
}

# defaults
verbose=false
output="-"     # - 代表 stdout
max_lines=0    # 0 = 不限制

while getopts ":vo:n:h" opt; do
    case "$opt" in
        v) verbose=true ;;
        o) output="$OPTARG" ;;
        n) max_lines="$OPTARG" ;;
        h) usage; exit 0 ;;
        :) echo "Error: -$OPTARG requires an argument" >&2; exit 1 ;;
        \?) echo "Error: unknown option -$OPTARG" >&2; exit 1 ;;
    esac
done
shift $((OPTIND - 1))

if [[ $# -lt 1 ]]; then
    echo "Error: missing input file" >&2
    usage
    exit 1
fi

input="$1"
$verbose && echo "Processing: $input" >&2
```

## 動手練習

```bash
# 1. 寫一個接受 -d（directory）和 -e（extension）的 script
cat > /tmp/find-ext.sh << 'EOF'
#!/usr/bin/env bash
dir="."
ext="txt"

while getopts "d:e:" opt; do
    case "$opt" in
        d) dir="$OPTARG" ;;
        e) ext="$OPTARG" ;;
        ?) echo "Usage: $0 [-d dir] [-e ext]" >&2; exit 1 ;;
    esac
done

echo "Searching in $dir for *.$ext:"
find "$dir" -name "*.$ext" -type f 2>/dev/null
EOF
chmod +x /tmp/find-ext.sh
/tmp/find-ext.sh -d /etc -e conf
/tmp/find-ext.sh -d /var/log -e log

# 2. 測試 $@ vs $*
test_args() {
    echo "=== \$@ ==="
    for a in "$@"; do echo "  [$a]"; done
    echo "=== \$* ==="
    for a in "$*"; do echo "  [$a]"; done
}
test_args "hello world" "foo" "bar"
```

## 自我檢核

- [ ] 能解釋 `"$@"` 和 `"$*"` 的差異（雙引號裡的行為）
- [ ] 知道 `$$` 用來建立唯一暫時檔名
- [ ] 能用 `getopts` 解析 `-v`、`-o file` 這類 flag
- [ ] 記得 `shift $((OPTIND - 1))` 消耗 option 後，`$@` 才是剩下的位置參數

→ [Ch 26 錯誤處理](./26-error-handling.md)
