# 練習 A — 檔案系統偵探

> 目標：把 Part 1–2（Ch 0–9）學到的工具拼起來，用一系列指令分析一個你不熟悉的目錄，回答關於 inode、權限、大小、連結的問題。

## 任務規格

用 Linux 的指令回答下列所有問題，**每個答案都要附上你用的指令**。

### 謎題一：/etc 大調查

| 問題 | 指令 | 答案 |
|------|------|------|
| /etc 目錄下有幾個項目（含隱藏）？ | `ls -la /etc \| wc -l` | ? |
| /etc 下有幾個 symlink？ | ? | ? |
| /etc/hostname 的 inode 號碼是？ | ? | ? |
| /etc/hostname 的 mtime（最後修改時間）是？ | ? | ? |
| /etc 下最大的檔案（不遞迴）是什麼？ | ? | ? |

### 謎題二：Hard Link 調查

```bash
# 先建立測試環境
mkdir /tmp/linklab
echo "original content" > /tmp/linklab/original.txt
ln /tmp/linklab/original.txt /tmp/linklab/hardlink.txt
ln -s /tmp/linklab/original.txt /tmp/linklab/softlink.txt
```

| 問題 | 答案 |
|------|------|
| `original.txt` 和 `hardlink.txt` 的 inode 號碼是否相同？ | ? |
| `softlink.txt` 的 inode 號碼是否和 `original.txt` 相同？ | ? |
| `original.txt` 的 hard link 計數（Links）是幾？ | ? |
| 刪掉 `original.txt` 後，`hardlink.txt` 的內容還能讀嗎？ | ? |
| 刪掉 `original.txt` 後，`softlink.txt` 呢？ | ? |

### 謎題三：磁碟偵探

| 問題 | 答案 |
|------|------|
| 根 filesystem（`/`）目前用了多少空間（%）？ | ? |
| `/var/log` 目錄總大小是多少？ | ? |
| 系統上最大的 3 個目錄（在 /var 下，depth=1）？ | ? |
| `/usr/bin` 下有幾個有 SUID 的程式？ | ? |

### 謎題四：Magic Bytes

```bash
# 先準備這些「假裝」有特定副檔名的檔案
cp /bin/ls /tmp/detective/fake_image.jpg
cp /etc/passwd /tmp/detective/fake_binary.bin
gzip -k /etc/hosts && cp /etc/hosts.gz /tmp/detective/mystery_file
mkdir /tmp/detective
cp /bin/ls /tmp/detective/fake_image.jpg
cp /etc/passwd /tmp/detective/fake_binary.bin
gzip -c /etc/hosts > /tmp/detective/mystery_file
```

| 問題 | 答案 |
|------|------|
| `fake_image.jpg` 的真實類型是？ | ? |
| `fake_binary.bin` 的真實類型是？ | ? |
| `mystery_file` 的真實類型是？ | ? |
| `fake_image.jpg` 開頭的前 4 bytes（hex）是什麼？ | ? |

## 期望輸出範例

謎題一：

```bash
ls -la /etc | wc -l
# 208   ← 你的輸出

ls -la /etc | grep "^l" | wc -l
# 32

stat /etc/hostname | grep Inode
# Device: 8,1    Inode: 262145    Links: 1
```

## 實作步驟建議

### Step 1：先把謎題環境建立好

```bash
mkdir -p /tmp/detective
# 建立謎題四的測試檔案
cp /bin/ls /tmp/detective/fake_image.jpg
cp /etc/passwd /tmp/detective/fake_binary.bin
gzip -c /etc/hosts > /tmp/detective/mystery_file
```

### Step 2：逐一回答謎題

謎題一的提示：
- 幾個 symlink → `ls -la /etc | grep "^l"` 裡的 `^l` 是什麼意思？
- 最大的檔案 → `ls` 的哪個選項可以按大小排序？

謎題二的提示：
- 比 inode 用 `stat` 的哪個欄位？
- link 計數叫什麼？

謎題三的提示：
- 磁碟用量 → `df` 還是 `du`？
- 最大目錄 → `du + sort` 的組合

謎題四的提示：
- 真實類型 → `file` 指令
- hex dump → `xxd -l 4` 只看前 4 bytes

### Step 3：整理成一份報告

把你的答案和指令整理成以下格式：

```bash
# 謎題一：/etc 大調查
echo "=== 謎題一 ==="
echo "項目數量："
ls -la /etc | wc -l

echo "Symlink 數量："
ls -la /etc | grep "^l" | wc -l

# ... 以此類推
```

## 完整參考解答

**全部做完再看！**

<details>
<summary>點開參考解答</summary>

```bash
# 謎題一
ls -la /etc | wc -l                        # 項目數
ls -la /etc | grep "^l" | wc -l            # symlink 數
stat /etc/hostname | grep Inode            # inode 號碼
stat /etc/hostname | grep Modify           # mtime
ls -lS /etc | grep "^-" | head -1         # 最大檔案

# 謎題二
mkdir /tmp/linklab
echo "original content" > /tmp/linklab/original.txt
ln /tmp/linklab/original.txt /tmp/linklab/hardlink.txt
ln -s /tmp/linklab/original.txt /tmp/linklab/softlink.txt

stat /tmp/linklab/original.txt  | grep Inode    # inode
stat /tmp/linklab/hardlink.txt  | grep Inode    # 應該一樣
stat /tmp/linklab/softlink.txt  | grep Inode    # 不一樣
stat /tmp/linklab/original.txt  | grep Links    # link count = 2

rm /tmp/linklab/original.txt
cat /tmp/linklab/hardlink.txt   # 還能讀
cat /tmp/linklab/softlink.txt   # Permission denied 或 No such file

# 謎題三
df -h /                                        # 根 FS 用量
du -sh /var/log                               # /var/log 大小
du -h --max-depth=1 /var 2>/dev/null | sort -rh | head -4   # 前 3 大
find /usr/bin -perm -4000 -type f | wc -l    # SUID 數量

# 謎題四
file /tmp/detective/fake_image.jpg   # ELF executable
file /tmp/detective/fake_binary.bin  # ASCII text
file /tmp/detective/mystery_file     # gzip compressed

xxd -l 4 /tmp/detective/fake_image.jpg
# 00000000: 7f45 4c46  .ELF

xxd -l 4 /tmp/detective/mystery_file
# 00000000: 1f8b 0800  ....  ← gzip magic: 1f 8b
```

</details>

## 清理

```bash
rm -rf /tmp/linklab /tmp/detective
```

## 自我檢核

- [ ] 能用 `ls -la | grep "^l"` 過濾 symlink
- [ ] 理解 hard link 刪掉原始後資料還在，symlink 則懸空
- [ ] 能用 `du + sort` 找磁碟殺手
- [ ] 知道 ELF 的 magic bytes 是 `7f 45 4c 46`，gzip 是 `1f 8b`

→ [Ch 10 Pipeline 與重導向](./10-pipeline-and-redirection.md)
