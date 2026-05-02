# Ch 2 — 目錄樹與路徑

> 目標：掌握 Linux FHS 目錄結構的設計邏輯，熟練使用絕對路徑/相對路徑，以及 `ls` 的各種實用選項。

## FHS：目錄不是亂放的

Linux 遵循 **FHS**（Filesystem Hierarchy Standard），每個目錄都有明確用途：

```
/
├── bin/     → 所有使用者可用的基本指令（ls, cat, grep...）
├── sbin/    → 系統管理指令（需要 root）
├── etc/     → 設定檔（/etc/passwd, /etc/nginx/nginx.conf...）
├── home/    → 使用者家目錄（/home/alice, /home/bob）
├── root/    → root 使用者的家目錄
├── tmp/     → 暫存檔（重開機後清空）
├── var/     → 會動態增長的資料
│   ├── log/   → 系統日誌
│   ├── spool/ → 郵件、列印佇列
│   └── run/   → 執行中程式的 PID 檔
├── usr/     → 使用者安裝的軟體
│   ├── bin/   → 一般程式（python3, gcc...）
│   ├── lib/   → 函式庫
│   └── share/ → 文件、圖示
├── lib/     → 開機必要的共用函式庫
├── proc/    → 虛擬 FS，核心資料（不佔磁碟）
├── sys/     → 虛擬 FS，裝置樹（不佔磁碟）
├── dev/     → 裝置檔（/dev/sda, /dev/null...）
├── mnt/     → 臨時掛載點
└── opt/     → 第三方大型軟體（/opt/google/chrome...）
```

記幾個關鍵：設定找 `/etc`，日誌找 `/var/log`，程式找 `/usr/bin`。

## 絕對路徑與相對路徑

**絕對路徑**：從根目錄 `/` 開始，不隨當前位置改變：

```bash
/home/alice/documents/report.txt
/etc/nginx/nginx.conf
```

**相對路徑**：從當前目錄開始，`.` 代表當前目錄，`..` 代表上一層：

```bash
./report.txt          # 當前目錄下的 report.txt
../config/app.conf    # 上一層的 config/ 目錄下
../../etc             # 上兩層再進 etc
~/documents           # ~ 是家目錄的縮寫，展開成 /home/alice
```

## cd：移動位置

```bash
cd /var/log           # 移到絕對路徑
cd documents          # 移到相對路徑（當前目錄下的 documents/）
cd ..                 # 上一層
cd ~                  # 家目錄
cd -                  # 回到上一個所在目錄（很實用）
cd                    # 不給參數，等同 cd ~
```

`cd -` 是「上一個目錄」，在兩個遠端目錄之間切換時省很多打字。

## pwd：確認位置

```bash
pwd
# /home/alice/documents

pwd -P    # 顯示實際路徑（解析 symlink）
```

## ls：列目錄

最常用的指令之一，選項很多但常用的就幾個：

```bash
ls                    # 基本列出（隱藏 . 開頭的檔案）
ls -l                 # 長格式（權限、大小、時間）
ls -a                 # 包含隱藏檔（. 開頭）
ls -la                # 兩者組合（最常用）
ls -lh                # -h = human readable，大小顯示成 K/M/G
ls -lt                # 按修改時間排序（最新在上面）
ls -ltr               # -r = 反序，最舊在上面（看日誌常用）
ls -R                 # 遞迴列出子目錄
ls -d */              # 只列目錄
ls --color=auto       # 依檔案類型上色（通常預設開）
```

長格式的每欄意義：

```
-rw-r--r--  1  alice  alice  1234  Jan 15 09:00  file.txt
│           │  │      │      │     │              └─ 檔名
│           │  │      │      │     └─ 最後修改時間
│           │  │      │      └─ 大小（bytes）
│           │  │      └─ 群組
│           │  └─ 擁有者
│           └─ hard link 計數
└─ 類型+權限（- = 普通檔，d = 目錄，l = symlink）
```

## 特殊目錄與路徑

```bash
# 家目錄
echo $HOME     # /home/alice
ls ~           # 等同 ls /home/alice
ls ~/.bashrc   # 家目錄下的設定檔

# 隱藏檔（以 . 開頭）
ls -a ~
# 會看到 .bashrc .bash_history .ssh/ 等

# 當前目錄和上一層
ls .           # 等同 ls（當前目錄）
ls ..          # 上一層目錄的內容
```

## tree：一次看整個結構

`tree` 指令把目錄樹視覺化（可能需要安裝）：

```bash
sudo apt install tree

tree /etc/nginx           # 顯示 nginx 設定目錄
tree -L 2 /usr            # 只看 2 層深
tree -a ~/                # 包含隱藏檔
tree -d /var              # 只顯示目錄
```

## 動手練習

```bash
# 1. 探索 FHS 目錄
ls /etc | head -20        # 設定檔
ls /var/log               # 日誌
ls /proc | head -20       # 行程 PID 和其他虛擬檔

# 2. 練習路徑轉換
cd /var/log
pwd                       # /var/log
ls ../../etc/hosts        # 用相對路徑找到 /etc/hosts

# 3. cd - 的用途
cd /etc
cd /var/log
cd -                      # 回到 /etc
pwd                       # 應該是 /etc

# 4. ls 長格式練習
ls -lah /etc | head -10   # 看 /etc 的第一個幾項
# 找到一個 symlink（l 開頭的行）
ls -lah /etc | grep "^l"  # 過濾出 symlink
```

## 自我檢核

- [ ] 知道 `/etc`、`/var/log`、`/usr/bin`、`/tmp` 各放什麼
- [ ] 理解 `..`、`~`、`-` 在路徑裡的意義
- [ ] 能用 `ls -lah` 讀出長格式每欄的意義
- [ ] 知道 `cd -` 的用途

→ [Ch 3 權限模型](./03-permissions.md)
