# Ch 3 — 檔案系統導覽與路徑

> **目標**：建立 Linux 檔案系統的全圖——單一樹狀結構（沒有 C:/D: 槽）、FHS（檔案系統階層標準）各目錄的用途、絕對路徑 vs 相對路徑、`.`/`..`/`~` 的意義、以及 current working directory 的本質。這是後面所有檔案操作的地圖。

> **環境**：Ubuntu/Debian，FHS 3.0。各目錄用途在所有遵守 FHS 的 Linux 通用。

## 為什麼要先看檔案系統全圖？

你之後會在檔案系統裡 cp、mv、find、grep。如果你不知道「`/etc` 放什麼、`/usr` 和 `/usr/local` 差在哪、`/proc` 為什麼是假的」，你會在系統裡迷路——不知道設定檔在哪、不知道裝的程式去哪、不敢碰某些目錄怕弄壞。

這章給你檔案系統的地圖：整體是一棵樹（不是 Windows 的 C:/D: 分槽），每個目錄有約定的用途（FHS），路徑有絕對和相對。有了這張地圖，後面的檔案操作才有方向感。

## 先建立直覺：Linux 是一棵樹，不是幾個槽

```
Windows 的檔案系統（多個獨立的槽）：
  C:\         D:\         E:\
  ├─Windows   ├─Data      ├─（USB）
  └─Users     └─...
  （每個磁碟是獨立的根）

Linux 的檔案系統（單一一棵樹）：
  /                          ← 唯一的根（root）
  ├── bin    usr    etc
  ├── home   var    tmp
  ├── proc   sys    dev
  └── mnt/usb ←─── USB 磁碟「掛」在樹的某個節點（Ch 9）
        │
  所有東西在「同一棵樹」上
  不同磁碟「掛載」到樹的不同位置（不是分成 C:/D:）
```

關鍵差異：**Linux 只有一個根 `/`**。Windows 每個磁碟是獨立的根（C:、D:）；Linux 把所有磁碟「掛載」（mount，Ch 9）到同一棵樹的不同位置。你的第二顆硬碟可能掛在 `/mnt/data`，USB 掛在 `/media/usb`——它們都在 `/` 這棵樹上，不是獨立的槽。這個「單一樹」設計讓路徑統一（永遠從 `/` 開始），是 Unix 的優雅之處。

## FHS：檔案系統階層標準

Linux 各目錄的用途由 **FHS**（Filesystem Hierarchy Standard）規範。重要的幾個：

```
/                根目錄（一切的起點）
├── bin          基本命令（ls, cp, cat...）← 現代多是 /usr/bin 的 symlink
├── sbin         系統管理命令（mount, fdisk...）
├── etc          系統設定檔（/etc/passwd, /etc/hostname...）★ 設定都在這
├── home         使用者的家目錄（/home/you）★ 你的檔案在這
│   └── you      你的家（~ 指向這）
├── root         root 使用者的家（不是 /home/root！）
├── usr          使用者程式（裝的軟體）
│   ├── bin      大部分命令在這（/usr/bin/python3...）
│   ├── lib      library
│   ├── local    你自己編譯安裝的（/usr/local/bin）★ 不被套件管理碰
│   └── share    架構無關的資料（文件、man page、icon）
├── var          可變資料（會變大的東西）
│   ├── log      日誌（/var/log/syslog...）★ debug 看這
│   ├── cache    快取
│   └── tmp      持久的暫存（重開機不一定清）
├── tmp          暫存檔（重開機通常清空）
├── opt          第三方大型軟體（自包含的）
├── dev          裝置檔案（/dev/sda, /dev/null...，Ch 8）★ 一切皆檔案
├── proc         kernel/process 資訊（假檔案，Ch 16）★ 即時生成
├── sys          kernel 參數（假檔案）
├── boot         開機檔案（kernel, initramfs, GRUB）
├── mnt /media   掛載點（外部磁碟、USB）
└── run          runtime 資料（PID 檔、socket）
```

最常打交道的：

| 目錄 | 放什麼 | 你會在這做什麼 |
|---|---|---|
| `/home/you`（`~`）| 你的檔案 | 日常工作 |
| `/etc` | 系統設定 | 改設定（要 sudo）|
| `/var/log` | 日誌 | debug 看 log |
| `/usr/bin` | 大部分命令 | 命令在哪（`which`）|
| `/usr/local` | 自編軟體 | 自己 make install 的 |
| `/tmp` | 暫存 | 臨時檔案 |
| `/proc` | process/系統狀態 | 觀測底層（本課常用）|
| `/dev` | 裝置 | /dev/null, /dev/sda |

> **`/usr/local` 為什麼重要**：套件管理（apt）裝的東西在 `/usr`，但**不會碰 `/usr/local`**。`/usr/local` 是保留給「你自己編譯安裝」的軟體（`make install` 預設裝這）。這個分離避免你手動裝的東西和套件管理的衝突。如果你修過 debian_packaging 課程，會記得 Policy 禁止套件碰 `/usr/local`。

## 路徑：絕對 vs 相對

路徑有兩種寫法：

```
絕對路徑（absolute）：從根 / 開始
  /home/you/cmdlab/file.txt
  /etc/passwd
  /usr/bin/ls
        │
  特點：從哪裡執行都指向同一個檔案（明確、不變）

相對路徑（relative）：從「當前目錄」開始
  file.txt              （當前目錄下的 file.txt）
  ./file.txt            （同上，. 是當前目錄）
  ../other/file.txt     （上一層的 other 目錄下）
  cmdlab/file.txt       （當前目錄下的 cmdlab 裡）
        │
  特點：意義取決於「你現在在哪」（current working directory）
```

關鍵的特殊符號：

```
.       當前目錄（current directory）
..      上一層目錄（parent directory）
~       你的家目錄（/home/you）
~user   某使用者的家目錄（~root → /root）
-       上一個目錄（cd - 回到剛才的目錄）
/       根目錄（路徑開頭的 / = 絕對路徑）
```

```bash
# 範例
cd ~/cmdlab           # 去家目錄下的 cmdlab（~ 展開成 /home/you）
pwd                   # /home/you/cmdlab（印當前絕對路徑）
cat ./file.txt        # 當前目錄的 file.txt（. = 這裡）
cat ../README.md      # 上一層的 README.md（.. = 上一層）
cd /etc               # 絕對路徑（從 / 開始）
cd -                  # 回到剛才的 ~/cmdlab
```

## Current Working Directory：你「在哪」

每個 process（包括你的 shell）有一個 **current working directory（CWD）**——相對路徑的基準點：

```
CWD（current working directory）：
  每個 process 記住「我現在在哪個目錄」
  相對路徑都從 CWD 算
        │
  cd 改變 shell 的 CWD（Ch 1：所以 cd 是 builtin）
  pwd 印出 CWD
```

```bash
# CWD 是 process 的屬性，存在哪？
pwd                          # 印當前 CWD
cat /proc/self/cwd 2>/dev/null || readlink /proc/self/cwd
# /home/you/cmdlab   ← kernel 記錄的 CWD（/proc 暴露出來，Ch 16）

# 每個 process 有自己的 CWD
ls -l /proc/$$/cwd           # $$ 是 shell 的 PID，看它的 CWD
```

> CWD 是 process 的屬性，kernel 記在 process 的資料結構裡（`/proc/<pid>/cwd` 暴露出來）。這解釋了 Ch 1 的「cd 必須是 builtin」——cd 改的是 shell process 自己的 CWD，子 process 有獨立的 CWD（改子的不影響父）。也解釋了相對路徑的本質：`cat file.txt` 是「CWD/file.txt」，CWD 變了，同樣的相對路徑指向不同檔案。

## 路徑解析的底層

當你 `cat /home/you/file.txt`，kernel 怎麼找到這個檔案？逐段解析路徑：

```
解析 /home/you/file.txt：
  從 / 開始（絕對路徑）
  / → 找 "home" 這個 entry → 進入 /home
  /home → 找 "you" → 進入 /home/you
  /home/you → 找 "file.txt" → 找到檔案
        │
  每一段是一次「在目錄裡查找名字」（Ch 5 的 dentry）
  相對路徑同理，但從 CWD 開始而非 /
```

用 strace 看路徑解析（Ch 0）：

```bash
strace -e openat cat /etc/hostname 2>&1 | grep hostname
# openat(AT_FDCWD, "/etc/hostname", O_RDONLY) = 3
#         ↑ AT_FDCWD 表示「相對於 CWD」（這裡是絕對路徑，所以 CWD 不影響）
```

## 故意弄壞：相對路徑在不同 CWD 的陷阱

```bash
cd ~/cmdlab
echo "in cmdlab" > note.txt

# 相對路徑 note.txt 在 cmdlab 找得到
cat note.txt          # in cmdlab

# 換個目錄
cd /tmp
cat note.txt          # cat: note.txt: No such file or directory
#   ↑ 因為現在 CWD 是 /tmp，note.txt 指 /tmp/note.txt（不存在）

# 絕對路徑不受 CWD 影響
cat ~/cmdlab/note.txt # in cmdlab（從哪都找得到）
```

這展示相對路徑的陷阱：同樣的 `note.txt`，在不同 CWD 指不同檔案。腳本裡用相對路徑特別危險（腳本的 CWD 可能不是你以為的）——這是 Part 8 的重要教訓（腳本用絕對路徑或明確設定 CWD）。

## 踩雷集錦

1. **以為 Linux 有 C:/D: 槽**：Linux 是單一樹，磁碟掛載到樹的節點（Ch 9）。沒有「磁碟槽」概念。`/mnt/data` 可能是另一顆硬碟，但路徑上看不出來

2. **混淆 /root 和 /home/root**：root 使用者的家是 `/root`（不是 `/home/root`）。一般使用者的家在 `/home/`，root 特殊

3. **相對路徑在腳本裡出錯**：相對路徑取決於 CWD。腳本被從不同目錄執行時，相對路徑指向不同地方。腳本用絕對路徑或 `cd` 到已知目錄（Part 8）

4. **以為 /tmp 的東西永久存在**：`/tmp` 重開機通常被清空（有些系統定期清）。重要的東西不要放 /tmp。`/var/tmp` 較持久但也別當永久儲存

5. **在 /usr/local 找不到 apt 裝的東西**：apt 裝在 /usr，不是 /usr/local。/usr/local 是你自己編譯安裝的。`which <cmd>` 看命令實際在哪

## 進階：path 的長度限制與特殊情況

路徑有一些底層限制和特殊情況：

```
路徑的底層限制：
  PATH_MAX：單一路徑的最大長度（通常 4096 bytes）
    超過 → 某些 syscall 回傳 ENAMETOOLONG
  NAME_MAX：單一檔名的最大長度（通常 255 bytes）
        │
  特殊路徑：
  //etc      多個斜線等於一個（//etc = /etc），但開頭兩個斜線在 POSIX 有特殊保留
  /etc/      結尾斜線通常無影響（/etc 和 /etc/ 多數時候一樣）
  ./../.     . 和 .. 可以混用（./../. = 上一層）
```

```bash
# 看系統的限制
getconf PATH_MAX /     # 4096
getconf NAME_MAX /     # 255

# 多斜線等於一個
ls //usr///bin         # = /usr/bin（多餘斜線被忽略）
```

> 路徑長度限制（PATH_MAX 4096）偶爾會咬人——深層巢狀目錄或長檔名累積可能超過，導致 `ENAMETOOLONG`。檔名 255 bytes 限制（NAME_MAX）在處理某些資料（如用長字串當檔名）時要注意。這些是 Ch 4（VFS）會再碰到的底層約束。多斜線等於一個是個方便的容錯（路徑拼接時不用擔心多個斜線）。

## 動手練習

1. 探索 FHS：`ls /`，然後 `ls /etc | head`（設定）、`ls /var/log | head`（日誌）、`ls /usr/bin | wc -l`（多少命令）。建立各目錄用途的直覺

2. 玩路徑符號：`cd ~`（家）、`pwd`、`cd ..`（上一層）、`cd -`（回家）、`cd /etc`（絕對）、`cd ../var`（相對）。觀察 `pwd` 怎麼變

3. 看 CWD 的底層：`readlink /proc/self/cwd`（kernel 記錄的當前目錄）。`cd` 到別處再看，確認它跟著變

4. 跑「故意弄壞」：在 ~/cmdlab 建 note.txt，cd 到 /tmp 後 `cat note.txt` 看失敗，理解相對路徑取決於 CWD。用絕對路徑修復

## 本章重點整理

- Linux 是單一樹（唯一的根 `/`），不是 Windows 的 C:/D: 分槽；磁碟掛載到樹的節點（Ch 9）
- FHS 規範各目錄用途：/etc（設定）、/home（你的檔案）、/var/log（日誌）、/usr/bin（命令）、/usr/local（自編）、/proc（kernel 狀態）、/dev（裝置）
- 絕對路徑從 `/` 開始（明確不變）；相對路徑從 CWD 開始（取決於你在哪）
- 特殊符號：`.`（當前）、`..`（上層）、`~`（家）、`-`（上一個目錄）；CWD 是 process 屬性（/proc/<pid>/cwd）
- 路徑解析是逐段「在目錄裡查名字」；相對路徑在腳本裡危險（CWD 不確定）

## 自我檢核

- [ ] 能解釋 Linux 單一樹和 Windows 多槽的差異，以及磁碟怎麼進入這棵樹（掛載）
- [ ] 知道 /etc、/var/log、/usr/bin、/usr/local、/proc、/dev 各放什麼
- [ ] 能區分絕對和相對路徑，知道 `.`/`..`/`~` 的意義
- [ ] 能解釋 CWD 是什麼、它是 process 的屬性、和 cd 是 builtin 的關係
- [ ] 知道相對路徑在腳本裡的陷阱（取決於 CWD）

## 延伸閱讀

### 官方文件

- **[Filesystem Hierarchy Standard (FHS 3.0)](https://refspecs.linuxfoundation.org/FHS_3.0/fhs/index.html)**
  - **讀哪裡**：各目錄的章節（/etc, /usr, /var...）
  - **學什麼**：每個目錄用途的權威定義；本章是精選，這是完整規範
  - **前提**：本章

- **[hier(7) man page](https://man7.org/linux/man-pages/man7/hier.7.html)**
  - **讀哪裡**：整頁，檔案系統階層的描述
  - **學什麼**：FHS 的 man page 版，簡潔版的目錄用途
  - **前提**：無

### 部落格 / 文章

- **[Understanding the Linux filesystem hierarchy](https://opensource.com/article/linux-filesystem-explained)** 或類似系統性介紹
  - **這篇說什麼**：FHS 各目錄的實務說明和歷史由來
  - **讀哪裡**：各目錄的解釋
  - **為什麼值得讀**：補充本章沒展開的歷史（為什麼有 /bin 和 /usr/bin 兩個、usr-merge 的演進）

→ [Ch 4 VFS 與 inode](./04-vfs-inode.md)
