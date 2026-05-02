# Ch 5 — 目錄與檔案操作

> 目標：熟練 `mkdir`、`cp`、`mv`、`rm`、`ln`、`touch` 的常用選項，理解 `-i`/`-r`/`-f` 這幾個高風險旗標的意義。

## mkdir：建立目錄

```bash
mkdir docs                      # 建立單一目錄
mkdir -p projects/app/src       # -p = 一次建立整個路徑（parent）
mkdir -p {logs,tmp,config}      # 大括號展開：建立三個目錄
mkdir -m 700 private            # -m = 指定權限（等同 chmod 700）
```

`-p` 是最重要的選項：沒有它，父目錄不存在時 `mkdir` 會失敗。

## touch：建立空檔案 / 更新時間戳

```bash
touch newfile.txt               # 建立空檔案（若存在則更新 atime/mtime）
touch -t 202401150900 file.txt  # 設定指定時間戳
touch -r ref.txt target.txt     # 複製 ref.txt 的時間戳到 target.txt
```

`touch` 主要用在兩個場景：建立空的佔位檔案，以及腳本裡更新時間戳觸發監控。

## cp：複製

```bash
cp file.txt backup.txt          # 複製檔案
cp file.txt /tmp/               # 複製到目錄（保留原檔名）
cp -r src/ dest/                # -r = 遞迴複製目錄（必要）
cp -p file.txt backup.txt       # -p = 保留時間戳、權限、擁有者
cp -a src/ dest/                # -a = archive，等同 -r -p，最完整的複製
cp -i file.txt exist.txt        # -i = interactive，覆蓋前詢問
cp -n file.txt exist.txt        # -n = no-clobber，目標存在就跳過
cp -u file.txt exist.txt        # -u = update，只在來源較新時才覆蓋
```

複製目錄時一定要 `-r`，忘記加會報錯：

```bash
cp src/ dest/
# cp: -r not specified; omitting directory 'src/'
```

## mv：移動 / 重命名

```bash
mv old.txt new.txt              # 重命名
mv file.txt /tmp/               # 移動到目錄
mv *.txt /backup/               # 移動多個檔案
mv -i file.txt exist.txt        # 覆蓋前詢問
mv -n file.txt exist.txt        # 目標存在就不移動
mv -u file.txt exist.txt        # 只在來源較新時才移動
```

`mv` 在同一個 filesystem 裡是原子操作（只更新目錄條目），不需要複製資料，所以即使移動 10GB 的檔案也是瞬間完成。

## rm：刪除

```bash
rm file.txt                     # 刪除單一檔案
rm file1.txt file2.txt          # 刪除多個
rm -r directory/                # -r = 遞迴刪除目錄（必要）
rm -f file.txt                  # -f = force，忽略不存在的檔案，不詢問
rm -rf /tmp/cache/              # 組合：遞迴 + 強制（危險指令）
rm -i *.txt                     # -i = 每個檔案都詢問確認
```

**`rm -rf` 沒有垃圾桶，沒有復原**。養成習慣：

```bash
# 刪除前先用 ls 確認你要刪什麼
ls /tmp/cache/
rm -rf /tmp/cache/

# 或先用 -i
rm -ri /tmp/cache/
```

一個常見悲劇：`rm -rf /var/log/ myapp` — 如果 `/var/log/` 和 `myapp` 之間有個空格被當成兩個獨立參數，`/var/log/` 會被刪掉。

## ln：建立連結

```bash
# Hard link
ln original.txt hardlink.txt
# 兩者是同一個 inode，刪掉任一個另一個還在

# Symlink（-s = symbolic）
ln -s /etc/nginx/nginx.conf nginx.conf
ln -s /usr/bin/python3 python  # 建立捷徑
ln -sf new_target.txt link.txt # -f = 目標已存在時先刪掉再建
```

實際場景：部署系統常用 symlink 做版本切換：

```bash
ln -sfn /opt/app/v2.0 /opt/app/current
# 原來的 /opt/app/current → v1.0
# 改成 /opt/app/current → v2.0
# 應用程式只需要存取 /opt/app/current 就自動指向新版
```

## 大括號展開：批次操作

bash 的大括號展開讓批次操作更簡潔：

```bash
mkdir -p project/{src,tests,docs,config}
cp config.{yaml,yaml.bak}         # 複製並加 .bak 副檔名
mv file.{txt,md}                  # 重命名副檔名
echo {a,b,c}.txt                  # 展開：a.txt b.txt c.txt
echo file{1..5}.txt               # 展開：file1.txt ... file5.txt
```

## 動手練習

```bash
# 建立一個練習沙盒
mkdir -p ~/sandbox/{src,backup,tmp}
cd ~/sandbox

# 1. 建立幾個測試檔案
touch src/app.py src/utils.py src/config.yaml

# 2. 複製整個 src/ 到 backup/（保留時間戳）
cp -a src/ backup/src_backup

# 3. 移動 tmp 裡的內容（先建立一些）
touch tmp/log_{1..5}.txt
mv tmp/*.txt backup/

# 4. 建立 symlink 模擬版本切換
mkdir v1.0 v2.0
echo "version 1" > v1.0/app.py
echo "version 2" > v2.0/app.py
ln -sfn ~/sandbox/v1.0 ~/sandbox/current
cat current/app.py    # version 1
ln -sfn ~/sandbox/v2.0 ~/sandbox/current
cat current/app.py    # version 2

# 清理
cd ~
rm -rf ~/sandbox
```

## 自我檢核

- [ ] 知道 `cp -a` 是最完整的複製方式（保留時間戳、權限、遞迴）
- [ ] 理解 `mv` 在同一 FS 是瞬間完成的原因（只改目錄條目）
- [ ] 習慣在 `rm -rf` 前先 `ls` 確認
- [ ] 能用大括號展開做批次操作

→ [Ch 6 查看檔案內容](./06-viewing-file-content.md)
