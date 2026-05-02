# Ch 7 — 搜尋

> 目標：掌握 `find` 的條件組合和 `-exec`，理解 `which`/`type`/`whereis` 的差異，能快速定位系統上的任意檔案或指令。

## find：最強大的搜尋工具

`find` 遞迴搜尋目錄，支援各種條件組合：

```bash
find /path -條件 -動作
```

### 按名稱搜尋

```bash
find /etc -name "*.conf"          # 副檔名 .conf
find /home -name "*.log"          # 家目錄下所有 .log
find / -name "passwd" 2>/dev/null # 從根目錄找，忽略沒有權限的錯誤
find . -iname "readme*"           # -iname = 不分大小寫
find . -name "*.py" -not -path "*/venv/*"  # 排除 venv 目錄
```

### 按類型搜尋

```bash
find /tmp -type f         # 只找普通檔案（f = file）
find /etc -type d         # 只找目錄（d = directory）
find /dev -type b         # 只找 block device
find /dev -type c         # 只找 character device
find . -type l            # 只找 symlink
```

### 按大小搜尋

```bash
find /var -size +100M     # 大於 100MB（+ = 大於，- = 小於）
find /home -size +10M -size -100M   # 10MB 到 100MB 之間
find /tmp -size 0         # 空檔案（大小為 0）
find . -size +1k          # 大於 1KB（k=KB, M=MB, G=GB）
```

### 按時間搜尋

```bash
find /var/log -mtime -1   # 過去 1 天內修改過的（- = 之內）
find /tmp -atime +7       # 超過 7 天沒有被存取的
find . -newer ref.txt     # 比 ref.txt 還新的
find /home -mmin -60      # 過去 60 分鐘內修改（mmin = 分鐘）
```

### 按權限 / 擁有者搜尋

```bash
find /usr/bin -perm -4000  # 有 SUID 的程式（-4000 = 4xxx）
find /home -perm 777       # 精確是 777 的
find /home -user alice     # alice 擁有的
find /var -group www-data  # www-data 群組的
find / -nouser 2>/dev/null # 沒有對應 UID 的檔案（被刪帳號留下的）
```

### 條件組合

```bash
find . -name "*.log" -size +1M             # AND（預設）
find . -name "*.log" -o -name "*.txt"      # OR（-o）
find . -not -name "*.py"                   # NOT（-not）
find . \( -name "*.log" -o -name "*.txt" \) -size +100k  # 括號分組
```

### -exec：對每個結果執行動作

```bash
# 對每個找到的檔案執行 rm
find /tmp -name "*.tmp" -exec rm {} \;
# {} 是 find 找到的路徑，\; 結束 -exec

# 更高效：用 + 把多個結果合成一次呼叫（等同 xargs）
find /tmp -name "*.tmp" -exec rm {} +

# 列出詳細資訊
find . -name "*.py" -exec ls -lh {} \;

# 複製到另一個目錄
find . -name "*.conf" -exec cp {} /backup/ \;

# 修改權限
find /var/www -type f -exec chmod 644 {} +
find /var/www -type d -exec chmod 755 {} +
```

`-exec ... \;` 對每個檔案分別執行一次程式；`-exec ... +` 把所有結果一起傳給程式，效率更好。

## xargs：從標準輸入構建參數

```bash
find . -name "*.log" | xargs rm
find . -name "*.py" | xargs grep "import os"
find . -name "*.txt" | xargs -I {} cp {} /backup/{}.bak
# -I {} = 替換符號，每個輸入替換 {}

# 處理檔名有空格的情況
find . -name "*.txt" -print0 | xargs -0 rm
# -print0 用 null 分隔，-0 以 null 解析
```

## which / type / whereis

```bash
which python3         # 在 PATH 裡找第一個匹配的執行檔
# /usr/bin/python3

type python3          # bash 的版本，更詳細（會顯示是 alias / builtin / file）
# python3 is /usr/bin/python3

type ll               # 如果 ll 是 alias
# ll is aliased to `ls -alF'

type cd               # cd 是 bash builtin
# cd is a shell builtin

whereis python3       # 找 binary、source、man page
# python3: /usr/bin/python3 /usr/lib/python3 /usr/share/man/man1/python3.1.gz
```

**差異整理**：
- `which` — 找 PATH 裡的執行檔
- `type` — bash 內部解析：alias? builtin? 外部程式？（最全面）
- `whereis` — 找 binary + man page + source，不限 PATH

## locate：快速搜尋（靠資料庫）

```bash
locate passwd          # 超快，但靠預先建好的資料庫
sudo updatedb          # 更新資料庫（通常每天自動跑）
locate -i readme       # 不分大小寫
locate -c "*.conf"     # 只顯示數量
```

`locate` 比 `find` 快很多，但資料庫不是即時的——剛建立的檔案可能找不到。找「系統上有沒有這個程式/檔案」用 `locate`，找「這個目錄下符合條件的檔案」用 `find`。

## 動手練習

```bash
# 1. 找系統上所有 > 100MB 的檔案
find / -size +100M -type f 2>/dev/null | head -10

# 2. 找過去 24 小時內被修改的設定檔
find /etc -mtime -1 -type f 2>/dev/null

# 3. 找有 SUID 的執行檔（潛在的安全關注點）
find /usr/bin /usr/sbin -perm -4000 -type f 2>/dev/null

# 4. 用 find + xargs 找所有 .py 檔裡包含 "password" 的
find . -name "*.py" | xargs grep -l "password" 2>/dev/null

# 5. type 的探索
type ls     # 可能是 alias
type echo   # builtin
type find   # 外部程式
```

## 自我檢核

- [ ] 能組合 `find` 的 `-name`、`-size`、`-mtime`、`-type` 條件
- [ ] 理解 `-exec {} \;` 和 `-exec {} +` 的效能差異
- [ ] 知道 `type` 比 `which` 更全面（能偵測 alias 和 builtin）
- [ ] 理解 `locate` 快但不即時，`find` 慢但即時

→ [Ch 8 封存與壓縮](./08-archives-and-compression.md)
