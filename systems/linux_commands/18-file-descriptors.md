# Ch 18 — 檔案描述符深入

> 目標：理解 fd 的內部結構，能用 `/proc/<PID>/fd` 和 `lsof` 觀察行程的開啟資源，理解 pipe 的運作原理。

## fd 的三層抽象

你在 Ch 10 看過 fd 是數字，但它背後是三層結構：

```
行程 fd table          核心 open file table         inode table
┌──────────┐           ┌─────────────────┐          ┌──────────┐
│ fd 0     │ ────────→ │ offset: 0       │ ────────→│ inode 42 │
│ fd 1     │ ────────→ │ flags: O_WRONLY │          │ (file A) │
│ fd 2     │ ────┐     │ ref count: 1    │          └──────────┘
│ fd 3     │     └───→ │ offset: 1024    │
└──────────┘           │ flags: O_RDWR   │
                       │ ref count: 2    │
                       └─────────────────┘
```

- **fd**：行程私有的整數 index
- **open file description**（核心物件）：記錄偏移量和存取旗標，多個 fd 可以指向同一個 description
- **inode**：檔案本身

### 為什麼這三層很重要

`fork()` 之後，父子行程的 fd 指向**同一個** open file description：

```bash
# 這代表父子共享 offset
# 如果父行程寫了 10 bytes，子行程再寫，offset 會接著走
```

這就是為什麼 shell 的重新導向在 fork 之後仍然有效——fd 繼承了。

## 標準 fd 0/1/2

```
fd 0  stdin   ─── 鍵盤（或 pipe 來的輸入）
fd 1  stdout  ─── 終端（或 > 導到的檔案）
fd 2  stderr  ─── 終端（或 2> 導到的檔案）
```

這三個在行程啟動時就自動建立（繼承自父 shell）。

## /proc/PID/fd：觀察 fd

```bash
ls -la /proc/$$/fd         # 當前 shell 的 fd
ls -la /proc/1/fd          # PID 1 的 fd（systemd）

# 輸出範例：
# lrwxrwxrwx 1 root root 64 /proc/1234/fd/0 -> /dev/pts/0
# lrwxrwxrwx 1 root root 64 /proc/1234/fd/1 -> /dev/pts/0
# lrwxrwxrwx 1 root root 64 /proc/1234/fd/2 -> /dev/pts/0
# lrwxrwxrwx 1 root root 64 /proc/1234/fd/3 -> /tmp/logfile.txt

# 看 fd 對應的路徑
readlink /proc/1234/fd/3
```

開啟的 socket 會顯示成 `socket:[inode號]`，不是路徑。

## lsof：列出開啟的資源

`lsof`（list open files）更人性化，而且能查 socket：

```bash
lsof -p 1234           # 某個 PID 開啟的所有資源
lsof -u alice          # 某個使用者
lsof /tmp/logfile.txt  # 誰開啟了這個檔案
lsof +D /var/log       # 目錄下哪些檔案被開啟（遞迴）
lsof -i                # 所有網路連線
lsof -i :80            # 哪個行程在用 port 80
lsof -i TCP            # 只看 TCP
```

`lsof -i :80` 的輸出：

```
COMMAND  PID  USER  FD  TYPE  DEVICE SIZE/OFF NODE NAME
nginx   1234  root   6u  IPv4   12345  0t0  TCP *:80 (LISTEN)
```

## 重新導向的本質是 dup2

`> file.txt` 在 shell 裡做的事：

```
1. open("file.txt", O_WRONLY|O_CREAT|O_TRUNC)  → fd 3
2. dup2(fd 3, 1)   // 把 fd 3 複製到 fd 1（stdout）
3. close(fd 3)     // 關掉原來的 fd 3
```

所以重新導向後，程式對 fd 1 的寫入就流向了檔案，程式根本不知道 stdout 被換掉了。

`2>&1` 意思是「把 fd 2 指向 fd 1 目前指向的地方」：

```bash
./prog > output.txt 2>&1
# 順序重要：先讓 fd 1 指向 output.txt，再讓 fd 2 跟著 fd 1
# 反過來寫 2>&1 > output.txt 是錯的（fd 2 會指向原本的終端）
```

## pipe 的內部機制

```bash
ls | grep ".txt"
```

Shell 做的事：

```
1. pipe()  → 建立一個 kernel pipe buffer，取得 fd[0]（讀端）和 fd[1]（寫端）
2. fork()  → 建立子行程
3. 左邊行程（ls）：  close(fd[0]), dup2(fd[1], 1), exec("ls")
4. 右邊行程（grep）：close(fd[1]), dup2(fd[0], 0), exec("grep")
```

Pipe buffer 在核心，大小通常 64KB。如果 `ls` 產生的資料超過 64KB，`ls` 會被 block 住，等 `grep` 讀走一些後才繼續。

**`|` 兩邊是平行執行的行程，不是左邊跑完再跑右邊。**

## 特殊 fd 目的地

```bash
# /dev/null：丟棄所有輸出
command > /dev/null 2>&1

# /dev/stdin /dev/stdout /dev/stderr：符號連結
ls -la /dev/stdin     # -> /proc/self/fd/0

# /dev/fd/N：等同 /proc/self/fd/N
exec 3> /dev/fd/3     # 進階用法
```

## fd 的進階用法：exec 開啟 fd

```bash
# 在 shell 裡直接開啟 fd（不用外部命令）
exec 3> /tmp/log.txt         # fd 3 指向 /tmp/log.txt（寫）
echo "log message" >&3       # 寫到 fd 3
exec 3>&-                    # 關閉 fd 3

exec 4< /etc/passwd          # fd 4 指向 /etc/passwd（讀）
read line <&4                # 從 fd 4 讀一行
exec 4<&-                    # 關閉 fd 4
```

## 動手練習

```bash
# 1. 觀察當前 shell 的 fd
ls -la /proc/$$/fd

# 2. 開啟檔案後再觀察
exec 3> /tmp/test-fd.txt
ls -la /proc/$$/fd         # 多了 fd 3
echo "hello" >&3
exec 3>&-                  # 關掉
cat /tmp/test-fd.txt       # 確認有寫進去

# 3. 用 lsof 找誰在寫某個 log
# 先在背景開啟一個會寫 log 的行程
bash -c 'while true; do echo "$(date)" >> /tmp/demo.log; sleep 1; done' &
lsof /tmp/demo.log         # 誰在開啟這個檔案

# 4. 找佔用 port 的行程
lsof -i :22                # 誰在聽 SSH port

# 5. pipe buffer 體驗（概念用）
# 寫一個很大的輸出，觀察 ls 和 grep 同時在跑
ls /proc | wc -l           # 有多少項目

# 6. 重現 2>&1 順序問題
echo "test" > /tmp/a.txt 2>&1   # 正確
echo "test" 2>&1 > /tmp/b.txt   # 錯誤（stderr 仍到終端）
```

## 自我檢核

- [ ] 能解釋 fd → open file description → inode 三層結構
- [ ] 知道 `fork()` 後父子共享 open file description（共享 offset）
- [ ] 能用 `/proc/<PID>/fd` 和 `lsof` 觀察行程開啟的資源
- [ ] 能解釋 pipe 是 kernel buffer，兩端是平行行程

→ [Ch 19 環境變數](./19-environment-variables.md)
