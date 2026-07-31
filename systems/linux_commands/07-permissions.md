# Ch 7 — 權限位元與 ownership

> **目標**：徹底理解 Unix 權限模型——rwx 三組九位、user/group/other、八進位表示、chmod/chown、特殊位元（setuid/setgid/sticky）、umask，以及 kernel 在每次存取時如何檢查權限。這是系統安全的基礎。

> **環境**：Linux，chmod/chown/umask。承接 Ch 4-5（inode 存權限、目錄權限的意義）。

## 為什麼權限模型這麼重要？

多使用者系統的核心問題：怎麼讓不同使用者共享一台機器，但各自的檔案互不干擾、系統檔案不被破壞？Unix 的答案是一套簡潔的權限模型——每個檔案有「擁有者」和「權限位元」，kernel 在每次存取時檢查。

這套模型（rwx × user/group/other）四十年來幾乎沒變，因為它足夠簡潔又足夠用。理解它，你能解釋無數「Permission denied」、能安全地設定檔案權限、能理解 sudo/setuid 這些提權機制。權限設錯是安全漏洞的常見根源，也是 SysOps 每天碰的東西。

## 先建立直覺：權限是 3×3 的矩陣

```
每個檔案的權限 = 3 組 × 3 位：

           讀(r)  寫(w)  執行(x)
  user      ●     ●      ●        ← 檔案擁有者
  group     ●     ○      ●        ← 擁有者所屬群組
  other     ●     ○      ○        ← 其他所有人
        │
  ls -l 顯示：-rwxr-xr--
              │└┬┘└┬┘└┬┘
              │ u  g  o
              └ 檔案類型（- 一般檔案，d 目錄，l symlink...）
        │
  kernel 檢查存取時：
  你是 owner？→ 看 user 那組
  你在 group？→ 看 group 那組
  都不是？    → 看 other 那組
```

權限是個 3×3 矩陣：三個身份（user/group/other）× 三種權限（read/write/execute）。kernel 根據「你是誰」決定看哪一組。理解這個矩陣是理解所有權限行為的基礎。

## 讀 ls -l 的權限欄位

```bash
cd ~/cmdlab
echo "data" > file.txt
ls -l file.txt
# -rw-r--r-- 1 you you 5 May 30 10:00 file.txt
# │└┬┘└┬┘└┬┘  └┬┘ └┬┘
# │ u  g  o    │   └ group（所屬群組）
# │            └ user（擁有者）
# └ 類型（- 一般檔案）
```

逐位解讀 `-rw-r--r--`：

```
- rw- r-- r--
│ │   │   └ other: r--（只能讀）
│ │   └ group: r--（只能讀）
│ └ user: rw-（能讀、能寫，不能執行）
└ 類型：- 一般檔案
        │
  類型符號：
  -  一般檔案
  d  目錄
  l  symlink（Ch 6）
  c  字元裝置（Ch 8）
  b  區塊裝置（Ch 8）
  p  named pipe（Ch 8）
  s  socket
```

## 八進位表示

權限常用八進位數字表示（chmod 用）：

```
rwx 對應二進位，再轉八進位：
  r = 4（讀）
  w = 2（寫）
  x = 1（執行）
        │
  每組相加：
  rwx = 4+2+1 = 7
  rw- = 4+2+0 = 6
  r-x = 4+0+1 = 5
  r-- = 4+0+0 = 4
  --- = 0
        │
  三組合起來：
  -rw-r--r-- = 644（user=6, group=4, other=4）
  -rwxr-xr-x = 755（user=7, group=5, other=5）
  -rwx------ = 700（只有 owner 能讀寫執行）
```

```bash
# chmod 用八進位設權限
chmod 644 file.txt       # rw-r--r--（一般檔案的標準）
chmod 755 script.sh      # rwxr-xr-x（可執行檔的標準）
chmod 600 secret.txt     # rw-------（只有 owner 能讀寫，私密）
chmod 700 ~/.ssh         # rwx------（私密目錄）

ls -l file.txt
# -rw-r--r-- ... file.txt
```

chmod 也支援符號表示：

```bash
chmod u+x script.sh      # 給 user 加執行權限
chmod g-w file.txt       # 拿掉 group 的寫權限
chmod o=r file.txt       # 設 other 為只讀
chmod a+r file.txt       # all（ugo）都加讀權限
chmod u+x,g+x script.sh  # 多個操作
```

## chmod 底層：chmod syscall

```bash
strace -e chmod,fchmodat chmod 644 file.txt 2>&1 | grep file
# fchmodat(AT_FDCWD, "file.txt", 0644) = 0
#   ↑ chmod 命令底層呼叫 chmod/fchmodat syscall
#     0644 就是八進位的權限

# 權限存在 inode 裡（Ch 4），chmod 改的是 inode 的權限欄位
stat -c "%a %A" file.txt   # %a 八進位權限，%A 符號權限
# 644 -rw-r--r--
```

## r/w/x 對檔案 vs 目錄的不同意義

關鍵陷阱：rwx 對「檔案」和「目錄」意義不同：

```
對「檔案」：
  r：能讀內容（cat, less）
  w：能改內容（echo >>, vim）
  x：能執行（如果是程式/腳本）

對「目錄」：
  r：能「列出」目錄內容（ls 看得到檔名）
  w：能「修改」目錄（建檔/刪檔/改名，Ch 5！）
  x：能「進入」目錄（cd 進去、存取裡面的檔案）
        │
  → 目錄的 x 是「通行權」：
    沒有 x，連 cd 進去都不行，更別說存取裡面的檔案
    有 x 沒 r：能進去存取已知檔名的檔案，但不能 ls 列出
```

```bash
# 目錄權限的微妙差異
mkdir testdir
echo "secret" > testdir/file.txt

# 拿掉目錄的 r（不能 ls），但保留 x（能進入）
chmod 100 testdir          # --x------（只有 x）
ls testdir                 # Permission denied（不能列出，沒 r）
cat testdir/file.txt       # secret（能讀！因為有 x 能進入，且知道檔名）
#   ↑ 有 x 沒 r：能存取已知檔名，但不能列出

# 拿掉 x（不能進入）
chmod 600 testdir          # rw-------（沒 x）
cat testdir/file.txt       # Permission denied（沒 x，連進去都不行）
chmod 755 testdir          # 恢復
```

> 「目錄的 x 是通行權」是權限最常踩的雷。對檔案，x 是「能執行」；對目錄，x 是「能進入/穿過」。沒有目錄的 x，你連 `cd` 進去、存取裡面的檔案都不行（即使你知道完整路徑）。這解釋了為什麼有時「檔案明明可讀卻 Permission denied」——是路徑上某個目錄沒有 x。也呼應 Ch 5：目錄的 w 是「改目錄表」（建/刪/改名），不是改檔案內容。

## 特殊位元：setuid / setgid / sticky

除了 rwx，還有三個特殊位元：

```
setuid（4000）：
  執行檔有此位元 → 執行時用「檔案擁有者」的身份跑（不是執行者）
  例：/usr/bin/passwd 是 setuid root
    一般使用者執行 passwd → 它用 root 身份跑（才能改 /etc/shadow）
  ls -l 顯示：-rwsr-xr-x（user 的 x 變 s）

setgid（2000）：
  執行檔：用「群組」身份跑
  目錄：在裡面建的檔案自動繼承目錄的群組（協作目錄好用）
  ls -l 顯示：-rwxr-sr-x（group 的 x 變 s）

sticky bit（1000）：
  目錄：只有檔案擁有者能刪自己的檔案（即使目錄可寫）
  例：/tmp 有 sticky bit → 大家都能建檔，但只能刪自己的
  ls -l 顯示：drwxrwxrwt（other 的 x 變 t）
```

```bash
# 看 /tmp 和 passwd 的特殊位元
ls -ld /tmp
# drwxrwxrwt ... /tmp        ← t = sticky bit（防互刪）
ls -l /usr/bin/passwd
# -rwsr-xr-x ... passwd      ← s = setuid（用 root 跑）

# 設特殊位元（八進位前加一位）
chmod 4755 myprogram         # setuid + 755
chmod 2755 shared_dir        # setgid 目錄
chmod 1777 shared_tmp        # sticky + 777（像 /tmp）
```

> **setuid 是提權的關鍵也是安全風險**。`passwd` 需要改 `/etc/shadow`（只有 root 能改），但一般使用者要能改自己的密碼。setuid 解決這個：`passwd` 是 setuid root，執行時用 root 身份跑。但 setuid 程式如果有漏洞，就是提權漏洞（攻擊者利用它用 root 身份做壞事）。所以 setuid 程式要極度小心。**sticky bit 在 /tmp 防互刪**——/tmp 大家都能寫（建檔），sticky bit 確保只能刪自己的（不然你能刪別人的暫存檔）。這呼應 Ch 5：刪檔需要目錄寫權限，sticky bit 是對這個的限制。

## umask：新檔案的預設權限

新建檔案的權限由 **umask** 決定（從最大權限「遮掉」某些位）：

```
umask 的運作：
  新檔案的「理論最大」權限：
    一般檔案：666（rw-rw-rw-，檔案預設不給 x）
    目錄：    777（rwxrwxrwx）
        │
  umask「遮掉」某些位（umask 是「要拿掉的權限」）：
    umask 022 → 拿掉 group 和 other 的 w
    檔案：666 - 022 = 644（rw-r--r--）
    目錄：777 - 022 = 755（rwxr-xr-x）
        │
  → umask 022 是常見預設（owner 完整，others 只讀）
```

```bash
umask                # 看當前 umask
# 0022

# 建檔看預設權限
touch newfile        # 644（666 遮掉 022）
mkdir newdir         # 755（777 遮掉 022）
ls -l newfile
ls -ld newdir

# 改 umask（更嚴格）
umask 077            # 拿掉 group/other 所有權限
touch private        # 600（666 遮掉 077，只有 owner）
ls -l private
umask 022            # 恢復
```

> umask 是「預設權限的遮罩」。它定義「新檔案**不要**給哪些權限」。umask 022（常見）= 拿掉 group/other 的寫權限 → 檔案 644、目錄 755。umask 077（嚴格）= 只給 owner → 檔案 600。理解 umask 能解釋「為什麼我新建的檔案是 644 而不是 666」。設定檔（~/.bashrc）改 umask 能控制你所有新檔案的預設權限——077 適合處理敏感資料的環境。

## 故意弄壞：權限設錯的後果

```bash
cd ~/cmdlab

# 場景一：把腳本權限拿掉 x，不能執行
echo '#!/bin/bash
echo hi' > script.sh
chmod 644 script.sh          # 沒有 x
./script.sh                  # Permission denied（沒有執行權限）
chmod +x script.sh           # 加 x
./script.sh                  # hi（現在能跑）

# 場景二：把 ~/.ssh 權限設太寬，ssh 拒絕用
chmod 777 ~/.ssh 2>/dev/null
# ssh 會抱怨 "Permissions are too open" 並拒絕用 key
#   ↑ ssh 要求 ~/.ssh 和私鑰權限嚴格（700/600），太寬就拒絕
chmod 700 ~/.ssh 2>/dev/null

# 場景三：誤把 / 的權限改掉（千萬別在真機做！）
# chmod -R 777 / 會破壞整個系統的權限模型，很多服務拒絕啟動
```

權限設錯的後果各異：腳本沒 x 不能執行、SSH key 權限太寬被拒絕、系統檔案權限亂改導致服務失效。權限是安全和功能的基礎，設錯會以各種方式咬你。

## 踩雷集錦

1. **目錄的 x 是通行權，不是執行**：對目錄，x 是「能進入/穿過」。路徑上某個目錄沒 x，即使檔案可讀也存取不了。「檔案可讀卻 Permission denied」常是路徑上缺 x

2. **chmod 777 不是「修好權限」**：777 是「所有人能讀寫執行」，是安全災難（任何人能改）。別用 777 解決權限問題，要設對應的權限（檔案 644、可執行 755、私密 600）

3. **混淆 setuid 和執行權限**：setuid 是「用擁有者身份跑」，不是「能執行」。setuid 是提權機制（有安全風險），不是一般權限

4. **umask 是「拿掉」不是「設定」**：umask 022 是「拿掉 group/other 的 w」，不是「設權限為 022」。它是遮罩（要移除的位）

5. **以為 root 不受權限限制就無敵**：root 確實繞過大部分權限檢查，但仍受某些限制（如 immutable 屬性 chattr +i、SELinux/AppArmor MAC）。root != 絕對無限制

## 進階：權限之外——ACL、capabilities、MAC

傳統 rwx 模型簡潔但有限。現代 Linux 有更細緻的機制：

```
傳統 rwx 的限制與補充：
  限制：只能設 user/group/other 三組
    「給特定使用者 A 讀，但不給 B」做不到（A、B 都是 other）
        │
  ACL（Access Control List，Ch 進階）：
    setfacl/getfacl 給「特定使用者/群組」設權限
    setfacl -m u:alice:r file   → 單獨給 alice 讀權限
        │
  capabilities：
    把 root 的「全能」拆成細粒度能力
    一個程式能有 CAP_NET_BIND_SERVICE（綁低 port）
    而不需要完整 root（最小權限原則）
        │
  MAC（SELinux/AppArmor）：
    強制存取控制，凌駕於 rwx 之上
    即使 rwx 允許，MAC policy 不允許就不行
```

> 傳統 rwx 模型的限制是「只有三組身份」。要「給 alice 讀但不給 bob」，rwx 做不到（兩人都是 other）——這時用 **ACL**（`setfacl`）給特定使用者設權限。**capabilities** 把 root 的全能拆細（程式只拿需要的能力，不用完整 root，更安全）。**MAC**（SELinux/AppArmor）是凌駕 rwx 的強制控制。這些是傳統權限的補充，在 high-security 環境重要。本課聚焦傳統 rwx（最常用），但知道這些補充機制存在，能在 rwx 不夠時知道往哪找。

## 動手練習

1. 練 chmod：對一個檔案設 644、755、600，用 `ls -l` 和 `stat -c %a` 確認。練符號表示（`u+x`、`g-w`、`o=r`）。用 `strace -e fchmodat chmod 644 file` 看底層 syscall

2. 試目錄權限的微妙：建目錄放檔案，`chmod 100`（只有 x）看能存取已知檔名但不能 ls，`chmod 600`（沒 x）看連進去都不行。理解目錄 x 是通行權

3. 看特殊位元：`ls -ld /tmp`（sticky t）、`ls -l /usr/bin/passwd`（setuid s）。理解它們的作用

4. 玩 umask：`umask` 看當前值，`touch` 建檔看權限。`umask 077` 後建檔看變 600。理解 umask 是遮罩

## 本章重點整理

- 權限是 3×3 矩陣：user/group/other × read/write/execute；ls -l 顯示 `-rwxr-xr--`
- 八進位：r=4 w=2 x=1，每組相加（644 = rw-r--r--，755 = rwxr-xr-x）；chmod 改 inode 的權限
- 對目錄：r=列出、w=改目錄表（建/刪/改名）、x=進入/通行（沒 x 連 cd 都不行）
- 特殊位元：setuid（用擁有者身份跑，如 passwd）、setgid、sticky（/tmp 防互刪）
- umask 是新檔案的權限遮罩（022 → 檔案 644 目錄 755）；root 繞過多數檢查但非絕對無限制

## 自我檢核

- [ ] 能讀懂 `ls -l` 的權限欄位，並轉成八進位（rw-r--r-- = 644）
- [ ] 能解釋 rwx 對檔案和目錄的不同意義（尤其目錄的 x 是通行權）
- [ ] 知道 setuid 是什麼、為什麼 passwd 需要它、它的安全風險
- [ ] 知道 sticky bit 在 /tmp 的作用，以及它和 Ch 5「刪檔需要目錄寫權限」的關係
- [ ] 能解釋 umask 怎麼決定新檔案的權限

## 延伸閱讀

### 書籍

- **《The Linux Programming Interface》— Ch 15 (File Attributes), Ch 9 (Process Credentials)** — Michael Kerrisk
  - **讀哪幾章**：Ch 15（權限位元、setuid/setgid/sticky）、Ch 9（UID/GID、setuid 程式的安全）
  - **這本書的定位**：權限和身份機制的權威來源
  - **前提**：本章

### 官方文件

- **[chmod(2)](https://man7.org/linux/man-pages/man2/chmod.2.html)** 和 **[credentials(7)](https://man7.org/linux/man-pages/man7/credentials.7.html)** man pages
  - **讀哪裡**：chmod 的權限位元定義、credentials 的 UID/GID 模型
  - **學什麼**：權限和身份的權威定義
  - **前提**：本章

### 部落格 / 文章

- **[Setuid demystified](https://www.cs.berkeley.edu/~daw/papers/setuid-usenix02.pdf)** — Chen, Wagner, Dean (USENIX 2002)
  - **這篇說什麼**：setuid 的語意和安全陷阱的學術分析
  - **讀哪裡**：前半（setuid 模型）
  - **為什麼值得讀**：setuid 比看起來複雜（real/effective/saved UID），這篇講透為什麼 setuid 程式難寫對

→ [Ch 8 特殊檔案：device/pipe/socket](./08-special-files.md)
