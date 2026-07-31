# Ch 8 — lsof 與 fd 視角

> **目標**：掌握 lsof（list open files）——它列出 process 開啟的所有「檔案」（含 socket/pipe/裝置，因為一切皆檔案）。理解 fd 視角的觀察：誰開了某個檔案、某個 process 開了什麼、誰佔用了某個 port、誰開著被刪的大檔案。lsof 是 /proc/fd（Ch 7）的強大前端，是 debug「檔案被佔用」「port 被佔」「磁碟滿找不到檔案」的利器。

> **環境**：Linux，lsof（Ch 0 已裝）。看別人的 process 可能需要 sudo。

## 為什麼 fd 視角這麼有用？

Ch 7 你看到 /proc/<pid>/fd 顯示 process 開的 fd。lsof 是這個視角的強大前端——它不只看「一個 process 開了什麼」，還能反過來「誰開了這個檔案/port」。因為「一切皆檔案」（Ch 2），lsof 看的「檔案」包括一般檔案、socket（網路連線）、pipe、裝置——所以它能 debug 各種「資源被佔用」的問題。

fd 視角回答很多實際問題：「為什麼這個檔案刪不掉/卸載不了」（誰開著它）、「port 8080 被誰佔了」、「磁碟滿但找不到大檔案」（誰開著被刪的檔案，Ch 7 的延伸）、「這個 process 開了什麼網路連線」。這些「資源佔用」類的 debug，lsof 是首選。這章把 fd 視角的觀察講透。

## 先建立直覺:雙向查詢

```
lsof = fd 視角的「雙向查詢」

  /proc/<pid>/fd（Ch 7）：只能「一個 process → 它開的檔案」
        │
  lsof 能雙向：
    process → 開了什麼：lsof -p <PID>
    檔案 → 誰開著：lsof /path/to/file
    port → 誰佔著：lsof -i :8080
    使用者 → 開了什麼：lsof -u username
        │
  因為「一切皆檔案」，lsof 看的「檔案」包括：
    一般檔案、目錄
    socket（網路連線、unix socket）
    pipe（process 間通訊）
    裝置（/dev/...）
    被刪但 fd 還開著的檔案（deleted）
        │
  → lsof 是「資源佔用」的萬用查詢
    特別是「誰佔著這個資源」的反向查詢
```

關鍵心智：lsof 是 fd 視角的「雙向查詢」——不只「一個 process 開了什麼」（像 /proc/fd），還能反過來「誰開著這個檔案/port」。因為一切皆檔案，它看的包括檔案/socket/pipe/裝置。最有用的是**反向查詢**——「誰佔著這個資源」。

> lsof 是 Ch 7 的 /proc/<pid>/fd 的強大前端。如果對 fd、/proc/fd 不熟，回看 [Ch 7](./07-proc-filesystem-tour.md) 和 [Ch 2](./02-process-syscall-fd-model.md)。

## lsof 的核心用法

```bash
# === 一個 process 開了什麼 ===
sleep 300 &
lsof -p $!                        # 那個 process 開的所有檔案/fd
# COMMAND PID USER FD TYPE ... NAME
# sleep  ... 12345 cwd DIR ... /home/user      ← 當前目錄
# sleep  ... 12345 txt REG ... /usr/bin/sleep  ← 執行檔
# sleep  ... 12345 0u  CHR ... /dev/pts/0       ← stdin
# sleep  ... 12345 1u  CHR ... /dev/pts/0       ← stdout
kill %1

# === 誰開著某個檔案（反向查詢，超有用）===
lsof /var/log/syslog             # 哪些 process 開著這個檔案
lsof /usr/bin/bash               # 誰在執行 bash

# === 誰佔著某個 port（網路）===
lsof -i :8080                    # 誰用 port 8080
lsof -i :443                     # 誰用 443
lsof -i TCP                      # 所有 TCP 連線
lsof -i TCP:LISTEN               # 所有監聽中的 TCP（= ss -tlnp）

# === 某個使用者開的 ===
lsof -u username                 # 某使用者開的所有檔案

# === 某個目錄下被開的檔案 ===
lsof +D /var/log                 # /var/log 下被開的所有檔案

# === 被刪但還開著的檔案（磁碟滿的 debug！Ch 7）===
lsof | grep deleted              # 誰開著被刪的檔案
lsof -nP +L1                     # link count < 1（= 被刪但開著）
```

```
lsof 輸出的欄位：
  COMMAND  PID  USER  FD  TYPE  DEVICE  SIZE  NODE  NAME
        │
  FD 欄（重要）：
    cwd    當前目錄
    txt    程式碼（執行檔/library）
    0/1/2  stdin/stdout/stderr
    3u/4r  你開的 fd（數字+模式：r讀 w寫 u讀寫）
        │
  TYPE 欄：
    REG    一般檔案
    DIR    目錄
    CHR    字元裝置
    IPv4   網路 socket
    FIFO   pipe
    unix   unix socket
        │
  → FD + TYPE + NAME 告訴你「這個 fd 是什麼、指向哪」
```

> **lsof 的反向查詢（誰開著這個檔案/port）是它最強的用途——`lsof /path`、`lsof -i :port` 是 debug「資源被佔用」的首選**。lsof 的正向查詢（`lsof -p <PID>` 看一個 process 開了什麼）和 /proc/fd 類似。但它最強的是**反向查詢**：**`lsof /path/to/file`**（誰開著這個檔案——debug「為什麼這個檔案刪不掉/這個 USB 卸載不了，因為有 process 開著它」）；**`lsof -i :8080`**（誰佔著 port 8080——debug「port already in use，是哪個 process 佔的」，這比 `ss -tlnp | grep 8080` 更直接，還顯示 PID）；**`lsof -u user`**（某使用者開的所有檔案）；**`lsof +D /dir`**（某目錄下被開的檔案）。讀 lsof 輸出看 **FD 欄**（cwd=當前目錄、txt=執行檔/library、0/1/2=標準流、3u=你開的 fd 加模式）和 **TYPE 欄**（REG=檔案、IPv4=網路 socket、FIFO=pipe、unix=unix socket——一切皆檔案的體現）和 **NAME**（指向哪）。lsof 因為「一切皆檔案」能統一查詢檔案、網路、pipe——這讓它成為「資源佔用」類問題的萬用工具。記住兩個最常用的反向查詢：`lsof /path`（誰開著檔案）、`lsof -i :port`（誰佔著 port）。

## debug「資源被佔用」

```bash
# === 場景 1：檔案/裝置卸載不了（"device is busy"）===
# umount /mnt 失敗，說 busy → 誰開著 /mnt 下的東西？
lsof +D /mnt                     # /mnt 下被開的檔案
lsof /mnt                        # 直接開 /mnt 的
# → 找到佔用的 process，處理它（kill 或讓它關閉）後才能 umount

# === 場景 2：port 被佔（"address already in use"）===
# 啟動服務說 port 8080 被佔
lsof -i :8080
# COMMAND PID ... NAME
# python  9999 ... *:8080 (LISTEN)   ← 是 PID 9999 的 python 佔的
# → 找到佔 port 的 process（kill 它或換 port）

# === 場景 3：磁碟滿但找不到大檔案（Ch 7 的經典 debug）===
# df 顯示磁碟 100% 滿，但 du 找不到大檔案
lsof -nP +L1 | grep deleted      # 被刪但 fd 還開著的檔案
# COMMAND PID ... SIZE ... NAME
# myapp   1234 ... 5000000000 ... /var/log/huge.log (deleted)
# → 某 process 開著一個被刪的 5GB 檔案！（空間沒釋放）
#   解法：重啟那個 process（釋放 fd → 空間釋放），或讓它關閉/輪替 log

# === 場景 4：某 process 開了哪些網路連線 ===
lsof -i -a -p <PID>              # 那個 process 的網路連線（-a = AND）
```

> **lsof 是 debug「device busy」「port in use」「磁碟滿找不到檔案」三大資源佔用問題的首選**。這三個是運維最常遇到的「資源被佔用」問題，lsof 都能秒解：(1) **「device is busy」（卸載不了）** → `lsof +D /mnt` 或 `lsof /mnt` 找出「誰開著 /mnt 下的東西」，處理那個 process 才能 umount（常見於 USB 拔不掉、卸載不了的掛載點）；(2) **「address already in use」（port 被佔）** → `lsof -i :8080` 直接顯示「哪個 PID 的什麼程式佔著 8080」（比 ss 更直接，顯示完整的 process 資訊，是 debug「服務啟動失敗說 port 被佔」的首選）；(3) **「磁碟滿但 du 找不到大檔案」**（Ch 7 的經典問題）→ `lsof | grep deleted` 或 `lsof +L1` 找出「誰開著被刪的大檔案」——某 process 開著一個被 rm 但 fd 還開著的大檔案（如沒輪替的 log），空間不釋放（df 滿但 du 找不到），解法是重啟那個 process 釋放 fd。這三個場景是 lsof 的招牌用途——它的「誰佔著這個資源」反向查詢正是這類問題需要的。記住：**遇到「資源被佔用/busy/in use」就想到 lsof**。這是 lsof 在 debug 武器庫的定位——資源佔用問題的萬用查詢。

## 故意弄壞:製造並偵測資源佔用

```bash
cd ~/obslab

# 製造「port 被佔」並用 lsof 找出
python3 -m http.server 8888 &
SERVER_PID=$!
sleep 1
# 假裝你不知道誰佔了 8888，用 lsof 找
lsof -i :8888
# python3 <PID> ... *:8888 (LISTEN)   ← 找到了！
kill $SERVER_PID

# 製造「磁碟滿找不到檔案」並用 lsof 找
cat > holder.c <<'EOF'
#include <fcntl.h>
#include <unistd.h>
int main() {
    int fd = open("/tmp/secret_big.log", O_CREAT|O_WRONLY, 0644);
    // 寫一些資料
    for (int i = 0; i < 1000; i++) write(fd, "xxxxxxxxxx\n", 11);
    sleep(300);    // 開著 fd 睡著
    return 0;
}
EOF
gcc -o holder holder.c
./holder &
HOLDER_PID=$!
sleep 1
rm /tmp/secret_big.log            # 刪檔案（但 holder 還開著 fd）

# 現在：檔案「看起來」不見了，但空間沒釋放
ls /tmp/secret_big.log 2>&1       # No such file（檔案沒了）
# 用 lsof 找出「誰開著這個被刪的檔案」
lsof -nP +L1 | grep secret_big
# holder <PID> ... /tmp/secret_big.log (deleted)   ← 找到元兇！
# → 解法：kill holder（釋放 fd → 空間釋放）
kill $HOLDER_PID

# 製造「檔案被開著不能卸載」（概念）
# 一個 process 開著某掛載點下的檔案 → 那個掛載點 umount 會 busy
# lsof <掛載點> 找出佔用者
```

> 這些實驗讓你親手製造並用 lsof 偵測「資源佔用」——**製造 port 佔用**（跑一個 server，用 `lsof -i :port` 找出是誰）、**製造「磁碟滿找不到檔案」**（開檔案後刪掉但 fd 還開著，用 `lsof +L1 | grep deleted` 找出元兇）。第二個特別重要——它重現了運維最頭痛的問題之一：「`df` 說磁碟滿了，但 `du` 怎麼算都找不到那麼多檔案」。真相是「某 process 開著被刪的大檔案，空間沒釋放」，只有 lsof 能找到（因為那個檔案沒有檔名了，檔案系統工具找不到，但 lsof 從 process 的 fd 角度看得到）。解法是找出那個 process（lsof）並重啟它（釋放 fd）。親手製造一遍，你以後遇到「磁碟莫名滿」就知道 `lsof | grep deleted`。這呼應 Ch 7 的 /proc/fd（deleted）——lsof 是它的強大前端（能反向查詢、跨所有 process 搜尋）。掌握 lsof，你 debug「資源被佔用」類問題就有了利器。

## 動手練習

1. 正向查詢：`lsof -p <某PID>` 看一個 process 開了什麼，對照 /proc/<pid>/fd（Ch 7）

2. 反向查詢：`lsof /usr/bin/bash`（誰在跑 bash）、`lsof -i :22`（誰用 SSH port）

3. 找 port 佔用：跑一個 server，用 `lsof -i :<port>` 找出佔用者

4. 讀輸出：理解 lsof 的 FD 欄（cwd/txt/數字）和 TYPE 欄（REG/IPv4/FIFO）

5. 跑「故意弄壞」：製造「磁碟滿找不到檔案」（開檔案+刪），用 `lsof +L1 | grep deleted` 找元兇

## 本章重點整理

- lsof 是 fd 視角的雙向查詢：process→開了什麼、檔案/port→誰開著（反向查詢最強）
- 一切皆檔案：lsof 看的包括一般檔案/socket/pipe/裝置/被刪的檔案——統一查詢各種資源
- 三大用途：device busy（誰開著掛載點）、port in use（誰佔 port）、磁碟滿找不到檔案（誰開著被刪的）
- 讀輸出看 FD 欄（cwd/txt/數字u）+ TYPE 欄（REG/IPv4/FIFO）+ NAME（指向哪）
- 「磁碟滿但 du 找不到」= 某 process 開著被刪的大檔案，`lsof +L1 | grep deleted` 找元兇，重啟釋放

## 自我檢核

- [ ] 能用 lsof 雙向查詢（process 開了什麼、誰開著某資源）
- [ ] 知道 lsof 因為「一切皆檔案」能查檔案/網路/pipe
- [ ] 會用 lsof debug「port 被佔」「device busy」
- [ ] 會用 lsof 找「磁碟滿但找不到的大檔案」（deleted）
- [ ] 能讀懂 lsof 輸出的 FD/TYPE/NAME 欄

## 延伸閱讀

### 官方文件

- **[lsof(8) man page](https://man7.org/linux/man-pages/man8/lsof.8.html)** — lsof
  - **讀哪裡**：選項（-p/-i/-u/+D/+L）和 OUTPUT 那節
  - **為什麼值得讀**：lsof 所有選項的權威

### 文章

- **[lsof 實用範例](https://www.thegeekstuff.com/2012/08/lsof-command-examples/)** — The Geek Stuff
  - **這篇說什麼**：lsof 的常用範例
  - **讀哪裡**：整篇
  - **為什麼值得讀**：本章用法的擴充，更多實戰範例

- **[Debugging disk space with lsof](https://www.cyberciti.biz/tips/linux-unix-deleted-open-files-recover.html)** — nixCraft
  - **這篇說什麼**：用 lsof 找被刪但開著的檔案（磁碟滿 debug）
  - **為什麼值得讀**：本章「磁碟滿找不到檔案」的權威版

下一章看網路觀察——ss（連線狀態）和 tcpdump（抓封包）。從 fd 視角的網路（lsof -i）擴展到專門的網路觀察工具，debug 連線和封包問題。

→ [Ch 9 ss 與 tcpdump](./09-ss-and-tcpdump.md)
