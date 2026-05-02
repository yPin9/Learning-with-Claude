# Ch 13 — sed：流式編輯器

> 目標：掌握 `sed` 的替換、刪除、插入、地址範圍語法，能用它做批次文字替換和行過濾。

## sed 的工作模型

```
輸入行 → 讀進 pattern space → 執行指令 → 輸出 → 下一行
```

`sed` 每次處理一行，把它放進「pattern space」，執行你給的指令，然後輸出。預設把每一行都輸出（除非用 `-n` 靜默）。

```bash
sed '指令' file.txt          # 輸出到 stdout
sed -i '指令' file.txt       # -i = in-place，直接修改檔案
sed -i.bak '指令' file.txt   # -i.bak = 修改前先備份成 .bak
```

## 替換：s 指令

這是 `sed` 最常用的功能：

```bash
sed 's/old/new/' file.txt         # 每行替換第一個 old
sed 's/old/new/g' file.txt        # g = global，替換所有 old
sed 's/old/new/2' file.txt        # 替換第 2 個
sed 's/old/new/gi' file.txt       # gi = 全局 + 不分大小寫
sed 's/old/new/p' file.txt        # p = 也把替換後的行多輸出一次
sed -n 's/old/new/p' file.txt     # -n + p = 只輸出有替換的行
```

分隔符不一定要 `/`，遇到路徑時換成 `|` 或 `#` 更清楚：

```bash
sed 's|/usr/local|/opt|g' config.txt    # 用 | 避免 / 衝突
sed 's#http://old#http://new#g' urls.txt
```

## 刪除：d 指令

```bash
sed '/pattern/d' file.txt         # 刪除含 pattern 的行
sed '/^$/d' file.txt              # 刪除空白行
sed '/^#/d' file.txt              # 刪除以 # 開頭的行（注解）
sed '5d' file.txt                 # 刪除第 5 行
sed '2,5d' file.txt               # 刪除第 2 到 5 行
sed '/start/,/end/d' file.txt     # 刪除從 start 到 end 的行
```

## 地址（Address）

地址指定指令套用在哪些行：

```bash
sed '3s/old/new/' file.txt         # 只改第 3 行
sed '2,5s/old/new/' file.txt       # 第 2 到 5 行
sed '/pattern/s/old/new/' file.txt # 包含 pattern 的行
sed '/start/,/end/s/old/new/' file # start 到 end 之間的行
sed '$s/old/new/' file.txt         # 最後一行
sed '1~2s/old/new/' file.txt       # 每隔 1 行（1, 3, 5...）
```

## 插入和附加：i 和 a

```bash
sed '3i\新行內容' file.txt          # 在第 3 行之前插入
sed '3a\新行內容' file.txt          # 在第 3 行之後附加
sed '/pattern/a\附加的行' file.txt  # 在 pattern 行後附加

# 插入多行（用 \n）
sed '/pattern/a\第一行\n第二行' file.txt
```

## 印出：p 指令

```bash
sed -n '5p' file.txt               # 只輸出第 5 行
sed -n '2,10p' file.txt            # 只輸出第 2 到 10 行
sed -n '/start/,/end/p' file.txt   # 輸出 start 到 end 之間
sed -n '/pattern/p' file.txt       # 只輸出含 pattern 的行（等同 grep）
```

`-n` 壓制預設輸出，配合 `p` 就能只印特定行。

## 多個指令

```bash
# -e 串接多個指令
sed -e 's/foo/bar/g' -e '/^$/d' file.txt

# 或用分號
sed 's/foo/bar/g; /^$/d' file.txt

# 複雜的用腳本檔
cat > fix.sed << 'EOF'
s/http:/https:/g
/^#/d
s/  */ /g
EOF
sed -f fix.sed file.txt
```

## 捕捉群組（back reference）

```bash
# 用 \( \) 捕捉，用 \1 \2 引用（BRE）
echo "2024-01-15" | sed 's/\([0-9]*\)-\([0-9]*\)-\([0-9]*\)/\3\/\2\/\1/'
# 15/01/2024

# ERE 模式（-E）不需要反斜線
echo "2024-01-15" | sed -E 's/([0-9]+)-([0-9]+)-([0-9]+)/\3\/\2\/\1/'
```

## 常用場景

```bash
# 刪除每行的前後空白
sed 's/^[[:space:]]*//' file.txt   # 刪前空白
sed 's/[[:space:]]*$//' file.txt   # 刪後空白
sed 's/^[[:space:]]*//; s/[[:space:]]*$//' file.txt  # 兩者

# 在每行加上行號前綴
sed = file.txt | sed 'N; s/\n/\t/'  # = 輸出行號，N 合併兩行

# 替換設定檔的某個值
sed -i 's/^PORT=.*/PORT=8080/' .env

# 批次重命名（結合 bash）
for f in *.txt; do
    mv "$f" "$(echo "$f" | sed 's/\.txt$/.md/')"
done

# 在每行開頭加 # 做注解
sed 's/^/#/' config.txt

# 刪除 HTML 標籤
sed 's/<[^>]*>//g' page.html
```

## 動手練習

```bash
cat > /tmp/config.txt << 'EOF'
# Database configuration
DB_HOST=old-server.local
DB_PORT=5432
DB_NAME=production
# Cache configuration
CACHE_HOST=redis-old.local
CACHE_PORT=6379

MAX_CONNECTIONS=100
EOF

# 1. 替換所有 "old" 為 "new"（不分大小寫）
sed 's/old/new/gi' /tmp/config.txt

# 2. 刪除注解行和空白行
sed '/^#/d; /^$/d' /tmp/config.txt

# 3. 只看 DB_ 開頭的設定（不修改，只輸出）
sed -n '/^DB_/p' /tmp/config.txt

# 4. 把 HOST 的值包在引號裡
sed -E 's/(.*HOST=)(.*)/\1"\2"/' /tmp/config.txt

# 5. 把設定檔的 DB_HOST 改成新值（-i 直接修改）
sed -i.bak 's/^DB_HOST=.*/DB_HOST=new-db.internal/' /tmp/config.txt
diff /tmp/config.txt /tmp/config.txt.bak
```

## 自我檢核

- [ ] 記住 `s/old/new/g` 和 `s/old/new/` 的差異（有無 `g`）
- [ ] 能用地址範圍（行號、pattern）限制指令套用範圍
- [ ] 知道 `-i` 直接修改檔案，`-i.bak` 先備份
- [ ] 能用捕捉群組 `\1`、`\2` 重排欄位

→ [Ch 14 awk：欄位處理引擎](./14-awk.md)
