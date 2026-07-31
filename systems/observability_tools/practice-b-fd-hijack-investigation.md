# 練習 B — fd 劫持調查

> **目標**：整合 Part 3 的系統狀態觀察工具（/proc、lsof、ss、sysstat），調查一系列「資源狀態」的謎題——磁碟滿但找不到檔案、port 被神祕佔用、fd 一直洩漏、process 卡在 D 狀態。每個謎題用狀態觀察工具偵探破案。完成後你具備「從系統狀態觀察 debug」的能力，這和 Part 2 的「從行為觀察（strace）」互補，是 debug 的兩大支柱。

## 背景與動機

Part 2 你學了「從行為觀察」（strace 看 syscall）。Part 3 你學了「從狀態觀察」（/proc/lsof/ss 看當前狀態）。這兩種是 debug 的兩大支柱——行為（程式做了什麼）和狀態（系統現在怎樣）。這個練習訓練「從狀態觀察破案」。

這正是運維/SRE 的日常：系統出現某個「狀態異常」（磁碟滿、port 佔用、process 卡住），你要用狀態觀察工具找出原因。這些問題往往不是「程式做錯什麼」（strace 看不太出來），而是「系統處於某個異常狀態」（要 /proc/lsof/ss 看）。完成這個練習，你建立了「狀態觀察破案」的能力，配合 Part 2 的「行為觀察」，你的 debug 武器庫就完整了。

## 任務規格

調查四個「狀態謎題」，用 Part 3 的工具找出真相：

| 謎題 | 症狀 | 用什麼工具 |
|---|---|---|
| 謎題 1 | 磁碟滿，但 du 找不到大檔案 | lsof + deleted（Ch 8）|
| 謎題 2 | port 8080 被佔，但 ps 看不到明顯的服務 | lsof -i / ss（Ch 8/9）|
| 謎題 3 | process 的 fd 一直漲（洩漏）| /proc/fd + lsof（Ch 7/8）|
| 謎題 4 | process 卡在 D 狀態（不可中斷睡眠）| /proc/wchan + iostat（Ch 7/10）|

**核心要求**：每個謎題用狀態觀察工具找出根因，說出「哪個工具的哪個輸出揭示了真相」。

## 如果你卡住了

1. 謎題 1（磁碟滿）：`df` 說滿但 `du` 找不到 → 想想 Ch 7/8 的「被刪但 fd 開著的檔案」
2. 謎題 2（port 佔用）：`lsof -i :8080` 或 `ss -tlnp | grep 8080` 直接找佔用者的 PID
3. 謎題 3（fd 洩漏）：`watch "ls /proc/<pid>/fd | wc -l"` 看 fd 數量隨時間漲，`lsof -p` 看開了什麼
4. 謎題 4（D 狀態）：`cat /proc/<pid>/wchan` 看卡在哪個 kernel 函式，iostat 看是不是 IO 問題
5. 狀態觀察的核心：看「當前的狀態快照」，不是「行為」

## 四個謎題

```bash
# === 謎題 1：磁碟滿找不到檔案 ===
cat > eat_disk.c <<'EOF'
#include <fcntl.h>
#include <unistd.h>
#include <string.h>
int main() {
    int fd = open("/tmp/hidden.dat", O_CREAT|O_WRONLY, 0644);
    char buf[1024]; memset(buf, 'X', sizeof(buf));
    for (int i = 0; i < 50000; i++) write(fd, buf, sizeof(buf));  // 寫 ~50MB
    sleep(600);   // 開著 fd 睡著
    return 0;
}
EOF
gcc -o eat_disk eat_disk.c
./eat_disk &
sleep 2
rm /tmp/hidden.dat    # 刪檔案（但 eat_disk 還開著 fd）→ 空間沒釋放！

# === 謎題 2：port 被神祕佔用 ===
# 用 bash 開一個監聽（不是明顯的服務程式）
(exec 3<>/dev/tcp/0.0.0.0/0 2>/dev/null; python3 -c "import socket; s=socket.socket(); s.bind(('0.0.0.0',8088)); s.listen(); import time; time.sleep(600)") &

# === 謎題 3：fd 洩漏 ===
cat > fd_leaker.c <<'EOF'
#include <fcntl.h>
#include <unistd.h>
int main() {
    while (1) {
        open("/etc/hostname", O_RDONLY);  // 一直開，不關 → fd 洩漏
        usleep(100000);   // 每 0.1 秒一個
    }
    return 0;
}
EOF
gcc -o fd_leaker fd_leaker.c
./fd_leaker &

# === 謎題 4：D 狀態（用 IO 製造）===
# dd 大量 IO 時，可能短暫 D 狀態
# dd if=/dev/zero of=/tmp/iotest bs=1M count=2000 oflag=direct &
```

## 完整參考解答

**自己調查再看！** 用狀態工具破案才學得到。

<details>
<summary>四個謎題的破案過程</summary>

```bash
# === 謎題 1：磁碟滿找不到檔案 ===
# 症狀
df -h /tmp                       # 顯示用量增加
du -sh /tmp/* 2>/dev/null | sort -rh | head   # 但找不到大檔案（hidden.dat 被刪了）
# 破案：lsof 找被刪但開著的檔案（Ch 7/8）
lsof -nP +L1 2>/dev/null | grep deleted
# eat_disk <PID> ... 51200000 ... /tmp/hidden.dat (deleted)
# → 真相：eat_disk 開著一個被刪的 50MB 檔案，空間沒釋放！
# 解法：kill 那個 process（釋放 fd → 空間釋放）
pkill eat_disk

# === 謎題 2：port 被佔 ===
# 症狀：想用 8088 但被佔
lsof -i :8088
# python3 <PID> ... *:8088 (LISTEN)
# 或
ss -tlnp | grep 8088
# users:(("python3",pid=<PID>,...))
# → 真相：PID 的 python3 佔著 8088
# 解法：kill 它或換 port
ss -tlnp | grep 8088    # 確認

# === 謎題 3：fd 洩漏 ===
# 找到 fd_leaker 的 PID
LEAKER=$(pgrep fd_leaker)
# 觀察 fd 數量隨時間漲（洩漏的證據）
for i in 1 2 3; do
    echo "fd count: $(ls /proc/$LEAKER/fd | wc -l)"
    sleep 2
done
# fd count: 25 → 45 → 65   ← 一直漲！（洩漏）
# 看開了什麼（一堆 /etc/hostname）
lsof -p $LEAKER | grep hostname | head
# → 真相：fd_leaker 一直 open /etc/hostname 不關 → fd 洩漏
#   最終會 "too many open files"
# 解法：修程式（open 後要 close）
pkill fd_leaker

# === 謎題 4：D 狀態 ===
# 找 D 狀態的 process
ps aux | awk '$8 ~ /D/ {print $2, $11}'    # STAT 含 D 的
# 對 D 狀態的 process 看它卡在哪
cat /proc/<DPID>/wchan; echo              # 卡在哪個 kernel 函式
# 如果是 IO 相關（如 wait_on_page_bit）→ 在等磁碟 IO
# 配合 iostat 確認磁碟是否飽和
iostat -x 1 2                              # %util 高？await 高？
# → 真相：process 在等磁碟 IO（D 狀態 = 不可中斷睡眠，通常是 IO）
#   D 狀態的 process kill -9 都殺不掉（要等 IO 完成）
```

**解答說明**：

- **謎題 1（磁碟滿）**：經典的「被刪但 fd 開著」（Ch 7/8）。`df` 看到空間用掉（kernel 知道 fd 還開著，inode 沒釋放）但 `du` 找不到（沒有檔名）。`lsof +L1 | grep deleted` 是唯一能找到的工具
- **謎題 2（port 佔用）**：`lsof -i` 或 `ss -tlnp` 直接顯示佔用 port 的 PID 和程式。注意佔用者可能不是「明顯的服務」（這裡是 python 一行程式）
- **謎題 3（fd 洩漏）**：用 `watch ls /proc/<pid>/fd | wc -l` 看 fd 數量**隨時間漲**（洩漏的特徵——狀態觀察的「變化」維度）。`lsof -p` 看開了什麼（一堆同樣的檔案 = 反覆 open 不關）
- **謎題 4（D 狀態）**：D 狀態（不可中斷睡眠，Ch 2）通常是等 IO。`/proc/<pid>/wchan` 看卡在哪個 kernel 函式（IO 相關 = 等磁碟），iostat 確認磁碟是否飽和。D 狀態的 process **kill -9 都殺不掉**（要等 IO 完成）——這是 D 狀態的特徵
- **核心**：這些都是「狀態」問題（系統處於異常狀態），用狀態觀察工具（lsof/proc/ss）破案，而非行為工具（strace）

</details>

## 測試用案例

| 謎題 | 關鍵工具 | 揭示真相的輸出 |
|---|---|---|
| 磁碟滿 | lsof +L1 | (deleted) 的大檔案 |
| port 佔用 | lsof -i / ss | 佔用者的 PID |
| fd 洩漏 | /proc/fd + watch | fd 數量隨時間漲 |
| D 狀態 | /proc/wchan + iostat | 卡在 IO 相關 kernel 函式 |

## 延伸挑戰（加分）

- **挑戰一**：寫一個「fd 洩漏監控腳本」——給一個 PID，每秒記錄它的 fd 數量，如果持續增長就警告（自動偵測洩漏）

- **挑戰二**：寫一個「找出磁碟空間元兇」腳本——綜合 du（找大檔案）和 lsof（找被刪但開著的），完整定位「磁碟滿」的原因

- **挑戰三**：用 strace（Part 2）+ lsof（Part 3）結合調查 fd 洩漏——strace 看「在哪 open 不 close」（行為），lsof 看「累積的 fd」（狀態），兩個視角

- **挑戰四**：調查一個「殭屍 process（Z 狀態）」——製造一個 zombie（parent 不 wait），用 ps/proc 觀察，理解 zombie 和 D 狀態的差別

- **挑戰五**：調查「load average 高但 CPU 不忙」——這通常是 D 狀態 process（等 IO）拉高 load，用 vmstat（b 欄）+ ps（D 狀態）+ iostat 定位

## 自我檢核

- [ ] 能用 lsof 找「磁碟滿但找不到的檔案」（被刪但 fd 開著）
- [ ] 能用 lsof -i / ss 找 port 佔用者
- [ ] 能用 /proc/fd + watch 偵測 fd 洩漏（看數量隨時間漲）
- [ ] 能用 /proc/wchan + iostat 分析 D 狀態 process（等 IO）
- [ ] 理解「狀態觀察」和「行為觀察」（strace）的互補

這個練習訓練了「從系統狀態觀察 debug」的能力。接下來 Part 4 補上靜態分析——不執行程式，直接看 ELF 二進位的結構（nm/objdump/readelf），這是動態觀察之外的另一個視角。

→ [Ch 11 ELF 靜態檢視（nm/objdump/readelf）](./11-elf-static-inspection.md)
