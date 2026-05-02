# Ch 6 — 查看檔案內容

> 目標：根據場景選對工具：`cat` 小檔案、`less` 大檔案、`tail -f` 追蹤日誌、`file`/`stat`/`xxd` 分析檔案底層。

## cat：輸出整個檔案

```bash
cat file.txt                    # 輸出到終端
cat -n file.txt                 # -n = 顯示行號
cat -A file.txt                 # -A = 顯示不可見字元（$ = 行尾，^I = Tab）
cat file1.txt file2.txt         # 串接多個檔案輸出
cat file1.txt file2.txt > merged.txt  # 合併到新檔案
```

`cat` 適合小檔案（< 幾百行）。大檔案用 `cat` 只是把整個內容噴到螢幕，沒有意義——用 `less`。

## less：翻頁閱讀大檔案

```bash
less /var/log/syslog
less +F /var/log/syslog   # +F = 追蹤新增內容（等同 tail -f）
```

`less` 裡的操作：

| 按鍵 | 動作 |
|------|------|
| `Space` / `f` | 下一頁 |
| `b` | 上一頁 |
| `g` / `1G` | 跳到開頭 |
| `G` | 跳到結尾 |
| `/pattern` | 向下搜尋 |
| `?pattern` | 向上搜尋 |
| `n` / `N` | 下/上一個搜尋結果 |
| `q` | 退出 |
| `F` | 進入追蹤模式（Ctrl+C 退出）|

## head / tail：看開頭和結尾

```bash
head file.txt              # 預設顯示前 10 行
head -n 20 file.txt        # 前 20 行
head -c 100 file.txt       # 前 100 個 bytes

tail file.txt              # 後 10 行
tail -n 30 file.txt        # 後 30 行
tail -f /var/log/syslog    # -f = follow，持續追蹤新增內容
tail -F /var/log/syslog    # -F = 如果檔案被輪替（rotate）也繼續追蹤
```

`tail -f` 是看日誌最常用的指令，按 `Ctrl+C` 停止。

組合用法：

```bash
# 取第 10 到第 20 行
head -n 20 file.txt | tail -n 11

# 最新的 100 行日誌裡有沒有 ERROR
tail -n 100 /var/log/app.log | grep ERROR
```

## file：偵測檔案類型

`file` 根據**內容**（magic bytes）判斷類型，不靠副檔名：

```bash
file /bin/ls
# /bin/ls: ELF 64-bit LSB pie executable, x86-64...

file /etc/passwd
# /etc/passwd: ASCII text

file /usr/share/doc/manpage.gz
# .gz: gzip compressed data

file image.png
# image.png: PNG image data, 800 x 600...
```

把 `.txt` 改成 `.jpg` 騙不了 `file`，它看的是檔案最前面的幾個 byte（magic number）。

## stat：完整 inode 資訊

```bash
stat /etc/passwd
# 輸出 inode 號碼、權限、UID/GID、大小、三個時間戳

stat -c "%n %s %Y" *.txt   # -c 自訂格式：檔名 大小 mtime（Unix timestamp）
```

## xxd：十六進位檢視

```bash
xxd file.txt | head          # 十六進位 + ASCII 對照
xxd -l 32 file.txt           # -l = 只看前 32 bytes
xxd -b file.txt | head       # -b = 二進位模式
xxd /bin/ls | head           # 看執行檔的開頭（ELF magic: 7f 45 4c 46）
```

ELF 執行檔的開頭 4 bytes 是 `7f 45 4c 46`（`\x7fELF`）——這就是 `file` 辨識它的依據。

```bash
xxd /bin/ls | head -1
# 00000000: 7f45 4c46 0201 0100 0000 0000 0000 0000  .ELF............
```

## wc：計算行數 / 字數 / bytes

```bash
wc file.txt               # 輸出：行數 字數 bytes 檔名
wc -l file.txt            # 只顯示行數
wc -c file.txt            # 只顯示 bytes
wc -w file.txt            # 只顯示字數
wc -l *.log               # 多個檔案，最後一行是總計
ls /etc | wc -l           # 計算 /etc 下有幾個項目
```

## od：更底層的二進位檢視

```bash
od -c file.txt            # 顯示字元（包含 \n \t 等跳脫符號）
od -x file.txt            # 十六進位
od -An -tx1 file.txt      # -An 不顯示位址，-tx1 每 byte 一格
```

## 動手練習

```bash
# 1. 確認一個「假圖片」的真實類型
echo "This is not a picture" > fake.jpg
file fake.jpg    # 應該說是 ASCII text，不是 JPEG

# 2. 看 /bin/ls 的 magic bytes
xxd /bin/ls | head -2
# 第一個 4 bytes 應該是 7f 45 4c 46 (ELF)

# 3. tail -f 追蹤即時日誌
sudo tail -f /var/log/syslog &   # 背景執行
sleep 5
kill %1                          # 停止背景工作

# 4. 計算 /etc 下有幾個檔案和目錄
ls /etc | wc -l

# 5. 找最後 5 行包含 "error" 的日誌（不分大小寫）
tail -n 100 /var/log/syslog | grep -i error | tail -5
```

## 自我檢核

- [ ] 知道 `cat` 只適合小檔案，大檔案用 `less`
- [ ] 能在 `less` 裡搜尋、翻頁、跳到末尾
- [ ] 知道 `tail -f` 和 `tail -F` 的差異（後者能跨 log rotate）
- [ ] 理解 `file` 靠 magic bytes 判斷類型，不靠副檔名

→ [Ch 7 搜尋](./07-searching.md)
