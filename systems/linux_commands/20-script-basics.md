# Ch 20 — Shell Script 基礎

> 目標：掌握 shebang、執行權限、變數、引號規則，能寫出第一個實用的 bash script。

## 第一個 Script

```bash
#!/bin/bash
# 第一個 script

echo "Hello, $(whoami)!"
echo "Today is $(date +%Y-%m-%d)"
echo "You are in: $PWD"
```

存成 `hello.sh`，然後：

```bash
chmod +x hello.sh    # 加執行權限
./hello.sh           # 執行
```

## Shebang：告訴系統用哪個直譯器

```bash
#!/bin/bash          # 用 bash（最常見）
#!/bin/sh            # 用 POSIX sh（更可攜，但功能少）
#!/usr/bin/env bash  # 透過 env 找 bash（適合虛擬環境）
#!/usr/bin/env python3  # Python 腳本
```

`/usr/bin/env bash` 比 `/bin/bash` 更好：它查找 PATH，適應不同系統的 bash 位置。

如果沒有 shebang，用哪個直譯器看你怎麼執行：

```bash
./script.sh   # 沒 shebang → 用當前 shell
bash script.sh  # 明確指定 bash
sh script.sh    # 明確指定 sh
```

## 執行方式的差異

```bash
./script.sh        # 建立子行程執行（變數不影響當前 shell）
bash script.sh     # 同上
source script.sh   # 在當前 shell 執行（會影響當前 shell 的變數）
. script.sh        # 同 source（點命令）
```

`source` 用來載入設定（`.bashrc`、venv 的 `activate`）；執行獨立任務用 `./script.sh`。

## 變數

```bash
name="Alice"           # 等號兩邊不能有空格
age=30
greeting="Hello $name" # 雙引號：展開變數

echo $name
echo ${name}           # 大括號，在需要消歧義時必要
echo ${name}s          # 輸出 "Alices"（不是 $names）
```

### 命令替換

```bash
today=$(date +%Y-%m-%d)   # 把命令輸出存進變數
files=$(ls *.txt)

# 舊語法：用反引號（不要用，難讀）
today=`date +%Y-%m-%d`    # 避免這樣寫
```

### 算術

```bash
a=5
b=3
result=$((a + b))      # 算術展開
echo $((10 / 3))       # 整數除法，輸出 3
echo $((10 % 3))       # 餘數，輸出 1

((count++))            # 遞增
((count += 5))
```

## 引號規則：最重要的一節

bash 的引號有三種，效果完全不同：

```bash
# 雙引號：展開 $ 變數和命令替換
name="Alice"
echo "Hello $name"          # Hello Alice
echo "Today: $(date)"       # Today: Fri May...

# 單引號：完全字面值，不展開任何東西
echo 'Hello $name'          # Hello $name（字面）
echo 'Today: $(date)'       # Today: $(date)（字面）

# 不加引號：展開 $ 和做 word splitting
files=*.txt
echo $files                 # 展開 glob，可能是 a.txt b.txt c.txt
echo "$files"               # 只輸出 *.txt 這個字串（不展開 glob）
```

**最重要的規則：含有空格或特殊字元的變數，一定用雙引號包起來：**

```bash
filename="my file.txt"

# 錯誤：空格導致 word splitting
cp $filename /tmp/           # 等同 cp my file.txt /tmp/（三個參數！）

# 正確
cp "$filename" /tmp/
```

## 特殊變數

```bash
$0      # script 本身的名字
$1      # 第 1 個參數
$2      # 第 2 個參數
$#      # 參數個數
$@      # 所有參數（各自獨立）
$*      # 所有參數（合成一個字串）
$?      # 上一個命令的 exit code
$$      # 當前行程的 PID
$!      # 上一個背景行程的 PID
```

用 `$@` 而不是 `$*`：

```bash
#!/bin/bash
# 正確傳遞所有參數
for arg in "$@"; do
    echo "arg: $arg"
done
```

## 實用模板：帶參數的 script

```bash
#!/usr/bin/env bash

# 使用方式說明
usage() {
    echo "Usage: $0 <source> <destination>"
    echo "  source:      要複製的目錄"
    echo "  destination: 目標目錄"
}

# 檢查參數數量
if [[ $# -ne 2 ]]; then
    usage
    exit 1
fi

src="$1"
dst="$2"

# 檢查 source 存在
if [[ ! -d "$src" ]]; then
    echo "Error: '$src' is not a directory" >&2
    exit 1
fi

echo "Copying $src → $dst"
cp -r "$src" "$dst"
echo "Done."
```

## 動手練習

```bash
# 1. 寫一個 script 顯示系統資訊
cat > /tmp/sysinfo.sh << 'EOF'
#!/usr/bin/env bash
echo "=== System Info ==="
echo "Hostname: $(hostname)"
echo "OS: $(uname -o)"
echo "Kernel: $(uname -r)"
echo "Uptime: $(uptime -p)"
echo "User: $(whoami)"
echo "Date: $(date)"
EOF
chmod +x /tmp/sysinfo.sh
/tmp/sysinfo.sh

# 2. 引號測試（觀察差異）
name="John Doe"
echo $name       # 當成兩個詞
echo "$name"     # 當成一個詞

# 3. 算術練習
for i in 1 2 3 4 5; do
    echo "$i squared = $((i * i))"
done

# 4. 命令替換
files_count=$(ls /etc | wc -l)
echo "There are $files_count files in /etc"
```

## 自我檢核

- [ ] 知道 shebang 的作用，偏好 `#!/usr/bin/env bash`
- [ ] 理解 `./script.sh` vs `source script.sh` 的差異
- [ ] 記住雙引號展開變數、單引號全字面值
- [ ] 知道含空格的變數一定要用 `"$var"` 保護

→ [Ch 21 條件判斷](./21-conditionals.md)
