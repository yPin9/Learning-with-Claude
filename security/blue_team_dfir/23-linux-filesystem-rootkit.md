# Ch 23 — Linux 檔案系統鑑識與 Rootkit 偵測

> **目標：** 從 ext4 的 timestamp 與 inode 機制理解 Linux 磁碟鑑識的底層，學會從已刪除檔案復原、利用 tmpfs 特性分析記憶體檔案，並系統化地偵測 user-mode（LD_PRELOAD）與 kernel-mode（LKM/syscall hook）rootkit。
> **環境：** Ubuntu 22.04，ext4 主分區；debugfs、sleuthkit（`apt install sleuthkit`）、rkhunter/chkrootkit（需要 root）；Volatility3 部分延續 Ch 22 的環境。

---

## 為什麼需要檔案系統鑑識？

記憶體是即時快照，磁碟是歷史紀錄。攻擊者就算清了歷史、刪了 binary，ext4 的 journal、inode bitmap、free block 裡可能還留著足以還原時間線的痕跡。

更重要的是：**rootkit 的核心就是讓你的工具看不到它**，而檔案系統鑑識的武器是在 rootkit 的 hook 之下作業——直接讀 raw device、解析 inode、比對 journal，不依賴任何會被 hook 的系統呼叫。

---

## 先建立直覺：ext4 的 timestamp 體系

ext4 的每個 inode 儲存四個時間戳，這是鑑識時間線的基礎：

| 時間戳 | 縮寫 | 意義 | 何時更新 |
|--------|------|------|----------|
| access time | atime | 最後一次讀取的時間 | `open()` / `read()`（除非 mount 有 `noatime`） |
| modification time | mtime | 最後一次資料內容更新 | `write()` |
| change time | ctime | 最後一次 inode 中繼資料更新 | `chmod`/`chown`/`rename`/`link`/`write()` |
| creation time | crtime | inode 建立時間 | `open(O_CREAT)`（ext4 特有，ext2/3 沒有） |

鑑識用法：
- **mtime 可以被 `touch -m -t` 竄改**，攻擊者常拿來讓惡意檔案看起來「很老」
- **ctime 比 mtime 難竄改**：`touch` 改不了 ctime（`touch -c` 是「不建立新檔」，不是設 ctime）；`debugfs` 可以改，但需要 root 且掛載後不能直接操作
- **crtime 是 ext4 的創建時間**，`stat` 有時顯示，`debugfs` 永遠能讀到，是最難偽造的時間戳

```bash
# 讀 inode 的完整時間戳（包含 crtime）
stat /etc/passwd
# 輸出：
# Access: 2026-07-30 01:23:45.123456789 +0000
# Modify: 2026-01-15 10:00:00.000000000 +0000
# Change: 2026-01-15 10:00:00.000000000 +0000
# Birth:  2024-04-25 12:34:56.789012345 +0000   ← crtime（Birth）

# 如果 stat 沒顯示 Birth，用 debugfs
debugfs -R 'stat /etc/passwd' /dev/sda1 2>/dev/null
# 找 crtime 那行
```

### inode 結構概念

ext4 的 inode 儲存檔案的所有 metadata（大小、時間戳、UID/GID、權限位元），還有指向資料 block 的指標。inode 本身在 inode table 裡，與檔案名稱（directory entry，dentry）分開儲存。

這個分離給了鑑識機會：**即使 dentry 被刪掉（檔案名稱消失），inode 和資料 block 可能還在**，直到被後續寫入覆蓋。

---

## 已刪除檔案復原

刪除檔案（`unlink`）在 ext4 做的事：
1. 把 dentry 從 directory 的 data block 中移除
2. 把 inode bitmap 中對應的 bit 標記為「空閒」
3. 把資料 block 在 block bitmap 中標記為「空閒」
4. **不清除 inode 本身的內容，不清除資料 block 的內容**

所以只要沒有新資料覆蓋，理論上可以復原。

### 用 debugfs 復原（ext4）

```bash
# 假設 /dev/sda1 是要分析的分區，以唯讀模式掛載
debugfs /dev/sda1

# 在 debugfs prompt 裡：
debugfs: lsdel        # 列出所有刪除的 inode（可能很慢）
# 輸出（示意）：
# Inode  Owner   Mode    Size    Blocks   Time deleted
# 81923  0       100755  45678   12/12    Fri Aug  1 03:12:44 2026

debugfs: dump <81923> /tmp/recovered_file
# 把 inode 81923 的內容 dump 出來

# 確認復原的內容
file /tmp/recovered_file
```

注意：**lsdel 在 ext4 通常沒有辦法像 ext3 那樣完整恢復**，因為 ext4 在 unlink 時會把 inode 的 block pointer 清零（ext3 不清），這是 ext4 安全改進。大部分情況下需要用 Sleuth Kit 的 `icat`/`fls` 或 `extundelete`。

### 用 Sleuth Kit（TSK）

```bash
# 列出所有已刪除的檔案（-d 旗標）
fls -rd /dev/sda1 | head -50
# 輸出（示意）：
# r/r * 81923:   /tmp/.xsession-errors    ← * 表示已刪除

# 把已刪除 inode 的內容輸出
icat /dev/sda1 81923 > /evidence/recovered_file

# 也可以看特定目錄的 dentry 歷史
fls -rd /dev/sda1 | grep '/tmp/'
```

---

## tmpfs：記憶體檔案系統的特殊性

攻擊者愛用 `/dev/shm`、`/tmp`（有時是 tmpfs），因為：
- 資料存在記憶體，**重開機後自動消失**
- 預設對所有使用者可寫
- 一些 WAF/監控工具對這個路徑的監控比較寬鬆

鑑識要點：
- `/dev/shm` 掛的是 `tmpfs`，**關機後無法從磁碟復原**，只能在 live 狀態或從記憶體 dump 取得
- 如果你有 LiME dump，可以在記憶體裡找 tmpfs 的 page cache
- Volatility3 的 `linux.tmpfs` plugin（實驗性）可以嘗試從記憶體還原 tmpfs 檔案

```bash
# 確認哪些路徑是 tmpfs
mount | grep tmpfs
# 輸出（示意）：
# tmpfs on /dev/shm type tmpfs (rw,nosuid,nodev)
# tmpfs on /run type tmpfs (rw,nosuid,nodev,size=...)
# tmpfs on /tmp type tmpfs (rw,nosuid,nodev,size=...)   ← 如果 /tmp 是 tmpfs

# live 狀態：直接看
ls -la /dev/shm/
ls -la /tmp/ | grep -v drwxrwxrwt   # 排除 sticky bit 的正常目錄
```

---

## Rootkit 類型與鑑識策略

### User-mode Rootkit：LD_PRELOAD 機制

你在攻擊課學過 LD_PRELOAD 可以讓自訂的 `.so` 在每個 dynamically linked 進程啟動時被載入，覆蓋 libc 函數。用在 rootkit 上，典型做法是 hook `readdir`/`readdir64`/`getdents64` 讓 `ls` 看不到特定檔案，hook `fopen`/`fread` 讓 `cat` 看不到特定行。

```
攻擊者控制的 libhax.so
  ├── hook readdir() → 過濾掉 PID 3847 的目錄項
  ├── hook getdents64() → 讓 ls 看不到 /tmp/.x
  └── hook fopen("/proc/net/tcp") → 過濾掉 C2 連線的 socket 行
```

**偵測方法**：

```bash
# 1. 直接看 /etc/ld.so.preload
cat /etc/ld.so.preload
# 任何內容都可疑

# 2. 比較 LD_PRELOAD env var
cat /proc/*/environ 2>/dev/null | tr '\0' '\n' | grep LD_PRELOAD

# 3. 用 strace 看系統呼叫（不受 LD_PRELOAD hook 影響，因為 strace 用的是 ptrace，不過 hook 本身可以偵測到 ptrace 後反制）
strace -e trace=open,openat ls /tmp/ 2>&1 | grep -v '= -1'
# 如果 strace 看到 ls 打開了一個 /tmp/.libhax.so，但 ls 輸出沒有顯示它，就確認了 hook

# 4. 用 static binary（最可靠）
# busybox 靜態版本不受 LD_PRELOAD 影響
/usr/local/bin/busybox ls /tmp/    # 如果跟 /bin/ls 輸出不同，有問題
/usr/local/bin/busybox cat /proc/net/tcp    # 直接讀，不過任何使用者空間的 hook

# 5. 直接讀 /proc/net/tcp（hex 格式）
cat /proc/net/tcp | awk 'NR>1{print $2, $3, $4}'
# 跟 ss -tnap 的輸出比較，差異就是被隱藏的連線
```

### Kernel-mode Rootkit：LKM 與 Syscall Table Hook

LKM rootkit 在 kernel 空間運行，可以修改：
- **syscall table**（sys_call_table）：把 `sys_getdents64` 指標換成自己的函數
- **fops（file operations）**：修改 `/proc` 的 iterate_shared/read 函數指標，讓 `/proc/PID` 目錄在 kernel 層就被過濾掉
- **netfilter hooks**：在 TCP 層隱藏特定連接埠的封包
- **IDT（interrupt descriptor table）**：更罕見，修改中斷處理程序

**Syscall table hook 的工作原理**：

```
正常流程：
syscall 指令 → kernel 的 syscall handler → sys_call_table[__NR_getdents64]
                                                    ↓
                                             kernel_getdents64()

Hook 後：
syscall 指令 → kernel 的 syscall handler → sys_call_table[__NR_getdents64]
                                                    ↓
                                             rootkit_getdents64()  ← 過濾惡意項目後
                                                    ↓             再呼叫原本的
                                             kernel_getdents64()   kernel 函數
```

**偵測方法**：

```bash
# 1. 比對 /proc/kallsyms 和 sys_call_table 的實際內容（需要 root）
# sys_call_table 的位址在 /proc/kallsyms
grep 'sys_call_table' /proc/kallsyms
# 輸出：ffffffff82e001a0 R sys_call_table

# 但讀記憶體需要 kernel 模組或特殊工具，所以用 Volatility：
python3 vol.py -f memory.lime linux.check_syscall 2>/dev/null
# 比對所有 syscall 指標，回報不在正常 kernel 範圍內的

# 2. 比對 lsmod 和 Volatility 的 linux.lsmod
lsmod > /tmp/live_lsmod.txt
python3 vol.py -f memory.lime linux.lsmod > /tmp/vol_lsmod.txt
diff /tmp/live_lsmod.txt /tmp/vol_lsmod.txt
# 差異就是被隱藏的 kernel module

# 3. 用 rkhunter（有侷限，見踩雷）
rkhunter --check --skip-keypress
# 重點看 Checking for rootkits、Checking system commands 部分

# 4. 用 chkrootkit
chkrootkit
# 主要用途：快速掃描已知 rootkit 的 signature 和行為特徵
```

---

## 具體範例：發現 /proc 不一致

這是最直接的 LKM rootkit 偵測手法：比較「kernel 認知的 PID」和「/proc 暴露的 PID」。

```bash
# 方法一：比對 /proc 目錄和 ps 輸出（都可能被 hook）
# 不可靠，如果兩者都被 hook 了就沒差異

# 方法二：對特定 PID 範圍暴力測試（繞過目錄 listing hook）
for pid in $(seq 1 65535); do
  if [ -d /proc/$pid ]; then
    name=$(cat /proc/$pid/comm 2>/dev/null)
    if ! ps -p $pid >/dev/null 2>&1; then
      echo "HIDDEN PID: $pid ($name)"
    fi
  fi
done
# 注意：fops hook 也可以讓 /proc/$pid 的存取失敗，這時連這個方法也失效
# 最終要靠 Volatility 繞過所有 kernel hook

# 方法三：kill -0（信號測試）
# kill -0 PID 如果進程存在會回傳 0，不用任何 getdents
for pid in $(seq 1 65535); do
  if kill -0 $pid 2>/dev/null && ! ps -p $pid >/dev/null 2>&1; then
    echo "HIDDEN PID: $pid"
  fi
done
```

---

## rkhunter 與 chkrootkit 的侷限

這兩個工具是鑑識初學者的入門，但要清楚它們的實際能力邊界：

| 能力 | rkhunter | chkrootkit | 備註 |
|------|---------|------------|------|
| 已知 rootkit signature 掃描 | 是（資料庫需更新） | 是（內建） | 只能抓已知的 |
| SUID 檔案比對 | 是（需要基準） | 部分 | 需要乾淨基準才有意義 |
| Syscall table hook 偵測 | 有限 | 部分 | 不如 Volatility |
| LKM 隱藏偵測 | 部分 | 部分 | 依賴 /proc，有盲點 |
| LD_PRELOAD 偵測 | 是（檢查 /etc/ld.so.preload） | 是 | 容易過 |
| 自身被 hook 的情況 | 不能自我偵測 | 不能自我偵測 | 如果 rootkit 已 hook libc |
| False positive 率 | 中—高 | 中 | 需要人工判讀 |

**核心問題**：rkhunter 和 chkrootkit 都是 dynamically linked 的程式，如果 libc 已經被 hook，它們的掃描結果就不可信。這就是為什麼「懷疑有 rootkit 時要用 static binary」是鐵律。

---

## 對比表：User-mode vs Kernel-mode Rootkit

| 面向 | LD_PRELOAD（user-mode） | LKM syscall hook（kernel-mode） |
|------|------------------------|--------------------------------|
| 需要的權限 | root 或 LD_PRELOAD 環境變數 | root + kernel.modules_disabled=0 |
| 影響範圍 | 只影響 dynamically linked 進程 | 影響所有進程（kernel 層） |
| Static binary 可以繞過 | 是 | 否（syscall 直接進 hook） |
| 偵測難度 | 低（strace、/etc/ld.so.preload） | 高（需要記憶體分析） |
| Secure Boot 會阻擋 | 否 | 是（如果 kernel module signing 生效） |
| 移除方式 | 清除 /etc/ld.so.preload | 移除 LKM 或重開機（但可能有 boot persistence） |
| Volatility 可見性 | 在 maps 可以看到 | linux.check_syscall 可以找到 hook |
| rkhunter/chkrootkit 有效 | 部分 | 部分（資料庫依賴） |

---

## 踩雷

1. **ext4 的 inode 清零問題**：ext4 刪除檔案時會把 inode 的 block pointer 歸零（`EXT4_FEATURE_INCOMPAT_EXTENTS`），導致 `debugfs lsdel` 無法恢復資料，只能知道 inode 曾存在。如果要復原 ext4 的已刪除檔案，要用 `extundelete` 或商業工具（Autopsy 的 Sleuth Kit backend）掃描 block group 的 journal。

2. **atime 信心問題**：現代 Linux 通常用 `relatime` 而非 `atime` 或 `noatime`（`relatime` 只在 mtime 比 atime 新時才更新 atime）。這讓 atime 的精確度降低——你看到的 atime 可能是「上次 mtime 改變前的最後一次存取」，不是最近一次讀取。不要把 atime 當成精確的時間線。

3. **debugfs 必須在唯讀模式下使用**：否則 debugfs 的操作本身會更新 atime/ctime，污染你的證據。掛載時加 `mount -o ro,noatime`，debugfs 用 `-R` 選項非互動式執行。

4. **rkhunter --propupd 要在乾淨狀態下跑**：`rkhunter --propupd` 建立的是「現在這台機器的基準」，如果你在入侵後才建基準，rootkit 的特徵也被包進去了，之後掃描就不會報警。基準要在部署初期、上 production 前建立，定期更新，並存到外部位置。

5. **LD_PRELOAD 不影響 setuid binary 的某些情況**：glibc 的動態連結器在 setuid/setgid 執行時會忽略 `LD_PRELOAD` 環境變數（安全機制），但 `/etc/ld.so.preload` 仍然有效。這是 `/etc/ld.so.preload` 比環境變數更危險的原因——它連 sudo 後的 shell 也吃得到。

---

## 進階延伸

- **ext4 的 journal 分析**：`/dev/sda1` 裡的 journal（journal inode #8）紀錄了最近幾千個 transaction，可以從 journal 看到刪除操作的詳細 inode 變化。`jcat` 和 TSK 的 `jls` 可以用來分析 journal。
- **DKMS 生成的 rootkit**：如果攻擊者安裝了一個偽裝成合法 driver 的 DKMS package，它在 kernel 更新時會自動重新編譯和載入。偵測要查 `/var/lib/dkms/` 的目錄。
- **eBPF rootkit**（新興威脅）：攻擊者也可以用 eBPF 寫 rootkit（已有 PoC：`bad-bpf`），可以在 `tc`/`xdp` hook 隱藏網路封包、在 `kprobe` 修改 return value。這類 rootkit 不觸碰 syscall table，`linux.check_syscall` 抓不到，需要 `linux.check_idt` 或 Tetragon 的 BPF map inspection。
- **Integrity Measurement Architecture（IMA）**：Linux kernel 內建的檔案完整性驗證，可以在 boot 時和執行時驗證文件雜湊，是最根本的 kernel-level 防禦。

---

## 本章重點整理

- **ext4 四個時間戳**：atime/mtime/ctime/crtime，ctime 最難竄改，crtime（Birth）是 ext4 特有；mtime 可以被 touch 偽造，鑑識時要交叉比對。
- **已刪除檔案復原**：ext4 的 block pointer 清零讓 lsdel 大多無效；用 Sleuth Kit 的 fls/icat 或 extundelete 掃 journal 和 block group。
- **tmpfs 資料只在記憶體**：關機消失，live triage 或 LiME dump 是唯一機會。
- **LD_PRELOAD rootkit**：只影響 dynamic binary，static busybox 或 strace 可以繞過偵測；/etc/ld.so.preload 的影響範圍最廣。
- **LKM rootkit**：修改 syscall table 或 fops，所有 user-space 工具都可能被欺騙；Volatility linux.check_syscall + linux.lsmod 是最可靠的偵測方式。
- **rkhunter/chkrootkit 有盲點**：資料庫依賴、被 hook 後自身不可信、false positive 多；當輔助工具，不當結論。

## 自我檢核

1. 你用 `stat` 看一個可疑檔案，發現 mtime 是三個月前但 ctime 是今天，這代表什麼？
2. 在 ext4 上，`rm` 之後為什麼 `debugfs lsdel` 通常找不到資料（不像 ext3）？
3. 攻擊者的 LD_PRELOAD hook 讓 `ls` 看不到 `/tmp/.mal`，但你怎麼用 static busybox 確認它存在？
4. `linux.check_syscall` 發現 syscall 78（getdents64）的指標指向 `0xffffffffc0a01000`，你怎麼判斷這是合法的 kernel module 還是 rootkit？
5. 為什麼 `/etc/ld.so.preload` 比 `LD_PRELOAD` 環境變數更適合用來做持久性 rootkit？

## 延伸閱讀

1. **SANS FOR508 — Linux Filesystem Forensics lab**：完整的 ext4 timeline 建立實驗，從 disk image 用 Sleuth Kit 重建入侵時間線；學時間戳分析最直接的材料。
2. **[Sleuth Kit Wiki — Analysing ext Filesystems](https://wiki.sleuthkit.org/index.php?title=Ext)**：fls/icat/ils 的官方說明，ext2/3/4 的 inode 結構和刪除復原機制的正確描述，比任何二手教程可靠。
3. **[The DFIR Report — Linux Rootkit Analysis](https://thedfirreport.com/)**：找含 "rootkit" tag 的案例；看真實事件中 LKM rootkit 怎麼被發現（通常是某個工具的輸出不一致，而非掃描器告警）。
4. **bad-bpf（GitHub）**：eBPF rootkit PoC 專案，看攻擊者未來可能使用的隱藏手法；接你的 bpf 課，理解後才能設計對應的偵測規則。
5. **《The Art of Memory Forensics》Ch 15（Linux Rootkits）**：系統性地分類 kernel rootkit 手法（syscall hook/VFS hook/netfilter hook/IDT hook）並說明 Volatility plugin 怎麼偵測各種，是本章 kernel-mode rootkit 部分最好的延伸閱讀。

---

→ [Ch 24 網路鑑識：Zeek/Suricata/PCAP/C2](./24-network-forensics.md)
