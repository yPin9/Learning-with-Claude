# 練習 A — 手工探索 inode/link/權限

> **目標**：整合 Ch 4–9 的檔案系統底層知識，寫一個 shell 腳本 `fsexplore.sh`，對給定的路徑「徹底解剖」——印出 inode、link count、權限（八進位+符號）、檔案類型、三個時間戳、以及（如果是 symlink）目標，並用 strace 驗證它底層做了哪些 syscall。完成後你能用底層視角看任何檔案，不再被表面迷惑。

## 背景與動機

你學了 inode、目錄、link、權限、特殊檔案、mount。這些知識散落在六章。這個練習把它們綁成一個工具——一個「檔案 X 光機」，輸入一個路徑，輸出它的所有底層屬性。

寫這個工具會逼你回憶每個概念：怎麼讀 inode（stat）、怎麼判斷類型、怎麼解析權限、怎麼處理 symlink。完成後你不只「知道」這些概念，還能「用程式提取」它們——這是真正掌握的標誌。這也是 Part 8（scripting）的暖身（你會用到基本的 shell 語法）。

## 任務規格

寫 `fsexplore.sh <path>`，對 `<path>` 輸出：

| 項目 | 來源 | 章節 |
|---|---|---|
| inode 號 | stat | Ch 4 |
| 檔案類型 | stat（regular/directory/symlink/device...）| Ch 4/8 |
| link count | stat | Ch 4/6 |
| 權限（八進位 + 符號）| stat | Ch 7 |
| 擁有者 / 群組 | stat | Ch 7 |
| 大小 | stat | Ch 4 |
| atime / mtime / ctime | stat | Ch 4 |
| symlink 目標（如果是 symlink）| readlink | Ch 6 |
| 所在的檔案系統（掛載點 + 類型）| findmnt / df | Ch 9 |
| 特殊位元（setuid/setgid/sticky）| stat | Ch 7 |

**驗收標準**：
- 對一般檔案、目錄、symlink、特殊檔案（/dev/null）都能正確輸出
- 處理錯誤（路徑不存在）給清楚的訊息
- symlink 要區分「symlink 本身」和「它指向的目標」的資訊
- 用 `strace` 確認你的腳本底層呼叫了 statx/readlink 等 syscall

## 期望輸出範例

```
$ ./fsexplore.sh ~/cmdlab/file.txt
=== File System Explorer ===
Path:        /home/you/cmdlab/file.txt
Type:        regular file
Inode:       1234567
Links:       1
Permissions: 644 (-rw-r--r--)
Owner:       you (1000)
Group:       you (1000)
Size:        6 bytes
Access:      2025-05-30 10:00:00  (atime)
Modify:      2025-05-30 10:00:00  (mtime)
Change:      2025-05-30 10:00:00  (ctime)
Filesystem:  /dev/sda2 on / (ext4)
```

```
$ ./fsexplore.sh /usr/bin/passwd
...
Type:        regular file
Permissions: 4755 (-rwsr-xr-x)
Special:     SETUID                       ← 認出 setuid（Ch 7）
...

$ ./fsexplore.sh ~/cmdlab/mylink
...
Type:        symbolic link
Symlink ->:  target.txt                    ← symlink 目標（Ch 6）
Target exists: NO (broken link)            ← 斷鏈偵測
```

## 如果你卡住了

1. `stat` 的 `-c` 選項能用格式字串輸出特定欄位：`stat -c "%i" file`（inode）、`%a`（八進位權限）、`%A`（符號）、`%h`（links）、`%U`（owner）、`%s`（size）。`man stat` 看所有格式
2. 判斷檔案類型：`stat -c %F`（給 "regular file"/"directory"/"symbolic link"...）或用 `[ -f ]`/`[ -d ]`/`[ -L ]` 測試
3. symlink 要用 `stat` 看 symlink 本身要加 `-c` 但注意 stat 預設跟隨 symlink——用 `stat -L`（跟隨）vs 不加（看 symlink 本身），或 `readlink` 看目標
4. 特殊位元：八進位權限的第一位（`stat -c %a` 如果是 4 位，第一位是特殊位元）
5. 檔案系統：`findmnt -n -o SOURCE,FSTYPE --target <path>` 給裝置和類型
6. 時間戳：`stat -c "%x"`（atime）、`%y`（mtime）、`%z`（ctime）

## 實作步驟建議

### Step 1：基本骨架 + 參數檢查（路徑存在嗎）
### Step 2：用 stat 提取 inode/links/權限/owner/size
### Step 3：判斷檔案類型，symlink 特別處理（目標 + 斷鏈偵測）
### Step 4：特殊位元偵測（setuid/setgid/sticky）
### Step 5：檔案系統資訊（findmnt）+ 整合輸出

## 完整參考解答

**寫完再看！**

<details>
<summary>fsexplore.sh</summary>

```bash
#!/bin/bash
# fsexplore.sh — 檔案系統 X 光機（解剖一個路徑的所有底層屬性）

# Step 1: 參數檢查
if [ $# -ne 1 ]; then
    echo "Usage: $0 <path>" >&2
    exit 1
fi
path="$1"

# 用 -e 測試存在（但 symlink 斷鏈時 -e 為 false，所以也測 -L）
if [ ! -e "$path" ] && [ ! -L "$path" ]; then
    echo "Error: '$path' does not exist" >&2
    exit 1
fi

echo "=== File System Explorer ==="
echo "Path:        $(readlink -f "$path" 2>/dev/null || echo "$path")"

# Step 3: 檔案類型（注意 symlink 要看本身，不跟隨）
# 用 stat（不加 -L，看 symlink 本身）
ftype=$(stat -c %F "$path" 2>/dev/null)
echo "Type:        $ftype"

# Step 2: stat 提取核心屬性（symlink 看本身用 stat，不加 -L）
echo "Inode:       $(stat -c %i "$path")"
echo "Links:       $(stat -c %h "$path")"

# 權限：八進位 + 符號
perm_oct=$(stat -c %a "$path")
perm_sym=$(stat -c %A "$path")
echo "Permissions: $perm_oct ($perm_sym)"

# Step 4: 特殊位元偵測（八進位若 4 位，第一位是特殊位元）
if [ ${#perm_oct} -eq 4 ]; then
    special_digit=${perm_oct:0:1}
    specials=""
    [ $((special_digit & 4)) -ne 0 ] && specials="$specials SETUID"
    [ $((special_digit & 2)) -ne 0 ] && specials="$specials SETGID"
    [ $((special_digit & 1)) -ne 0 ] && specials="$specials STICKY"
    [ -n "$specials" ] && echo "Special:    $specials"
fi

echo "Owner:       $(stat -c %U "$path") ($(stat -c %u "$path"))"
echo "Group:       $(stat -c %G "$path") ($(stat -c %g "$path"))"
echo "Size:        $(stat -c %s "$path") bytes"

# 時間戳
echo "Access:      $(stat -c %x "$path")  (atime)"
echo "Modify:      $(stat -c %y "$path")  (mtime)"
echo "Change:      $(stat -c %z "$path")  (ctime)"

# Step 3 (續): symlink 特別處理
if [ -L "$path" ]; then
    target=$(readlink "$path")
    echo "Symlink ->:  $target"
    if [ -e "$path" ]; then
        echo "Target exists: YES"
    else
        echo "Target exists: NO (broken link)"
    fi
fi

# Step 5: 檔案系統資訊
fsinfo=$(findmnt -n -o SOURCE,TARGET,FSTYPE --target "$path" 2>/dev/null | head -1)
if [ -n "$fsinfo" ]; then
    src=$(echo "$fsinfo" | awk '{print $1}')
    tgt=$(echo "$fsinfo" | awk '{print $2}')
    fst=$(echo "$fsinfo" | awk '{print $3}')
    echo "Filesystem:  $src on $tgt ($fst)"
fi
```

```bash
chmod +x fsexplore.sh
./fsexplore.sh ~/cmdlab/file.txt
./fsexplore.sh /dev/null
./fsexplore.sh /usr/bin/passwd
```

**解答說明**：

- **symlink 不跟隨**：`stat`（不加 `-L`）看 symlink 本身的資訊（它自己的 inode、是 symbolic link 類型）。`readlink` 取目標路徑。`[ -e "$path" ]` 對斷鏈 symlink 為 false（目標不存在）——用這個偵測斷鏈
- **特殊位元用位元運算**：八進位權限若 4 位，第一位是特殊位元（4=setuid, 2=setgid, 1=sticky）。`$((digit & 4))` 用位元 AND 偵測（Ch 7）
- **stat 格式字串**：`%i`inode `%h`links `%a`八進位權限 `%A`符號 `%U`/%u owner名/號 `%s`size `%x`/%y/%z 三時間戳 `%F`類型。一個工具提取所有 inode 資訊（Ch 4）
- **findmnt --target**：找出路徑所在的檔案系統（Ch 9）——印出它掛在哪個裝置、什麼類型
- **錯誤處理**：路徑不存在給清楚訊息並 exit 1（Part 8 的錯誤處理預習）

</details>

## 測試用案例

| 輸入 | 預期 | 驗證 |
|---|---|---|
| 一般檔案 | type=regular file，正確 inode/權限 | Ch 4/7 |
| 目錄 | type=directory，link count ≥ 2 | Ch 5 |
| symlink | type=symbolic link，顯示目標 | Ch 6 |
| 斷鏈 symlink | Target exists: NO | Ch 6 |
| /dev/null | type=character special file | Ch 8 |
| /usr/bin/passwd | Special: SETUID | Ch 7 |
| 不存在的路徑 | Error 訊息，exit 1 | 錯誤處理 |

## 延伸挑戰（加分）

- **挑戰一**：加 hard link 偵測——如果 link count > 1，用 `find <mountpoint> -inum <inode>` 找出所有指向同一 inode 的檔名（顯示這個檔案的所有 hard link，Ch 6）

- **挑戰二**：對目錄，計算它有幾個子目錄（用 link count - 2，Ch 5 的公式），並驗證和實際數的一致

- **挑戰三**：加 strace 自我驗證模式——`./fsexplore.sh --trace <path>` 用 strace 跑自己，過濾出 statx/readlink syscall，展示這個工具底層做了什麼（呼應全課的 strace 手法）

- **挑戰四**：對裝置檔案（/dev/sda），額外印出 major/minor number（`stat -c "%t %T"`，Ch 8）

## 自我檢核

- [ ] 能用 stat 提取一個檔案的所有 inode 屬性（不靠 ls -l 的排版）
- [ ] 知道怎麼正確處理 symlink（看本身 vs 看目標，斷鏈偵測）
- [ ] 能從八進位權限解析出特殊位元（setuid/setgid/sticky）
- [ ] 能找出一個路徑所在的檔案系統（findmnt）
- [ ] 能解釋這個工具底層做了哪些 syscall（statx/readlink）

→ [Ch 10 ls/stat 深入](./10-ls-stat.md)
