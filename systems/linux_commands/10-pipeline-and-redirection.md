# Ch 10 — Pipeline 與重導向

> 目標：從 file descriptor 的底層理解 `|`、`>`、`>>`、`<`、`2>`、`&>` 的工作原理，不只是記語法。

## File Descriptor：I/O 的底層

每個行程有一張 **file descriptor table**，預設有三個 fd：

```
fd 0 → stdin  (標準輸入，鍵盤)
fd 1 → stdout (標準輸出，終端機)
fd 2 → stderr (標準錯誤，終端機)
```

`echo "hello"` 把 "hello\n" 寫入 fd 1（stdout）。`cat file.txt` 從 fd 0（stdin）讀，但命令列給了路徑，所以它 `open()` 那個檔案讀。

所有的重導向和 pipeline，本質上都是**替換 fd 指向的目標**。

## 輸出重導向

```bash
echo "hello" > file.txt        # fd 1 指向 file.txt（覆蓋）
echo "world" >> file.txt       # fd 1 指向 file.txt（附加）
ls /nonexist 2> error.log      # fd 2 指向 error.log
ls /nonexist 2>> error.log     # fd 2 附加到 error.log
ls /nonexist 2>/dev/null       # 丟棄 stderr
ls /nonexist > out.txt 2>&1    # stdout 和 stderr 都進 out.txt
ls /nonexist &> all.txt        # 等同上面（bash 簡寫）
ls /nonexist 2>&1 1>/dev/null  # 只保留 stderr（注意順序！）
```

`2>&1` 的意思：「讓 fd 2 指向 fd 1 目前指向的地方」。順序很重要：

```bash
# 錯誤的順序（常見陷阱）
ls /nonexist 2>&1 > out.txt
# 執行時序：
# 1. 2>&1：fd 2 = fd 1 當前指向（還是終端機）
# 2. > out.txt：fd 1 改指 out.txt
# 結果：stdout 進 out.txt，stderr 還是印到終端機

# 正確的順序
ls /nonexist > out.txt 2>&1
# 1. > out.txt：fd 1 指向 out.txt
# 2. 2>&1：fd 2 = fd 1 當前指向（out.txt）
# 結果：stdout 和 stderr 都進 out.txt
```

## 輸入重導向

```bash
sort < names.txt               # sort 從 names.txt 讀（而不是鍵盤）
mysql < schema.sql             # 把 SQL 檔餵給 mysql
wc -l < file.txt               # 注意：不顯示檔名，只顯示數字
cat < file.txt                 # 和 cat file.txt 結果一樣，但機制不同
```

## Here Document（heredoc）

多行字串輸入，不需要暫存檔：

```bash
cat << EOF
第一行
第二行
第三行
EOF

# 常見用途：餵給 ssh 執行多個指令
ssh server01 << EOF
echo "hello from server01"
df -h
uptime
EOF

# 帶縮排（- 前綴，忽略 Tab 縮排）
cat <<- EOF
    這行的 Tab 縮排會被去掉
    這行也是
EOF
```

## Here String

單行版本的 heredoc：

```bash
grep "error" <<< "this is an error message"
# 等同：echo "this is an error message" | grep "error"

base64 <<< "hello"
# aGVsbG8K
```

## Pipeline：行程間通信

`|` 把左邊的 stdout 連到右邊的 stdin，透過 kernel 的 **pipe buffer** 傳遞：

```bash
cat file.txt | grep "error" | sort | uniq -c | sort -rn
```

底層發生的事：

```
kernel 為每個 | 建立一個 pipe（fd pair）
cat      → [pipe1] → grep   → [pipe2] → sort → [pipe3] → uniq → stdout
```

每個指令在**獨立行程**裡執行，pipeline 是並行的。

**管線的退出碼**：預設是最後一個指令的退出碼。用 `set -o pipefail` 讓整個 pipeline 在任一指令失敗時就視為失敗（腳本裡很重要）。

```bash
set -o pipefail
cat nonexist.txt | grep "ok"   # cat 失敗，整個 pipeline 失敗
echo $?   # 非 0
```

## tee：同時輸出到螢幕和檔案

```bash
./build.sh | tee build.log           # stdout 同時送到終端機和 build.log
./build.sh | tee -a build.log        # -a = append
make 2>&1 | tee make.log             # 含 stderr 一起捕捉
```

`tee` 的名字來自 T 形管，一個輸入，兩個輸出。

## /dev/null 和 /dev/zero

```bash
/dev/null    # 黑洞：寫入的資料消失，讀取得到 EOF
/dev/zero    # 無限 0 byte 的來源
/dev/random  # 亂數
/dev/urandom # 非阻塞的亂數（通常用這個）

# 常見用途
command 2>/dev/null              # 靜默錯誤
dd if=/dev/zero bs=1M count=10 of=testfile  # 建立 10MB 測試檔案
cat /dev/urandom | head -c 16 | xxd   # 產生 16 bytes 亂數
```

## 動手練習

```bash
# 1. 把 ls /etc 的 stdout 和 stderr 分別存到不同檔案
ls /etc /nonexist > /tmp/stdout.txt 2> /tmp/stderr.txt
cat /tmp/stdout.txt | wc -l
cat /tmp/stderr.txt

# 2. 找到所有 /proc 目錄下包含數字的子目錄（PID 目錄）
ls /proc | grep "^[0-9]" | wc -l   # 行程數量

# 3. 故意製造 pipefail 場景
set -o pipefail
cat /nonexist | wc -l     # cat 失敗
echo "結束碼：$?"
set +o pipefail   # 關掉

# 4. tee 同時輸出
ls /etc | tee /tmp/etc_list.txt | wc -l
cat /tmp/etc_list.txt | head -5
```

## 自我檢核

- [ ] 理解 `2>&1` 的意義是「讓 fd 2 複製 fd 1 的當前目標」
- [ ] 知道 `> out.txt 2>&1` 和 `2>&1 > out.txt` 效果不同（順序很重要）
- [ ] 理解 pipeline 的每個指令是獨立行程，並行執行
- [ ] 知道 `set -o pipefail` 讓 pipeline 中間失敗不被忽略

→ [Ch 11 grep 與正規表示式](./11-grep-and-regex.md)
