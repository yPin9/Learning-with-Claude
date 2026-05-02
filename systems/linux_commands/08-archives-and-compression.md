# Ch 8 — 封存與壓縮

> 目標：理解封存（archive）和壓縮（compression）是兩件事，掌握 `tar` 的核心選項，知道各種壓縮格式的取捨。

## 封存 ≠ 壓縮

一個常見混淆：

- **封存**：把多個檔案合併成一個檔案（`.tar`）——不壓縮，只合併
- **壓縮**：讓單一檔案變小（`.gz`、`.bz2`、`.xz`）

`.tar.gz`（俗稱 tarball）= 先封存成 `.tar`，再壓縮成 `.gz`，兩步合一。

## tar：封存與解封

`tar` 的旗標設計有點奇特，但組合就那幾個：

```bash
# 建立封存（Create）
tar -cf archive.tar dir/        # -c = create，-f = 指定檔案名

# 解封（eXtract）
tar -xf archive.tar             # -x = extract

# 查看內容（List）
tar -tf archive.tar             # -t = list，不解封只列出

# 詳細輸出（Verbose）
tar -cvf archive.tar dir/       # 建立時顯示每個檔案
tar -xvf archive.tar            # 解封時顯示
```

### 帶壓縮的 tar

```bash
# gzip（最常用，速度快）
tar -czf archive.tar.gz  dir/   # -z = gzip
tar -xzf archive.tar.gz

# bzip2（壓縮率較好，較慢）
tar -cjf archive.tar.bz2 dir/  # -j = bzip2
tar -xjf archive.tar.bz2

# xz（最高壓縮率，最慢）
tar -cJf archive.tar.xz  dir/  # -J = xz
tar -xJf archive.tar.xz

# 自動偵測格式（GNU tar 支援）
tar -xf archive.tar.gz          # 不用加 -z/-j/-J，tar 自動偵測
```

### 常用組合

```bash
# 備份 /etc，排除特定目錄
tar -czf etc_backup.tar.gz /etc --exclude=/etc/shadow

# 解封到指定目錄
tar -xzf archive.tar.gz -C /tmp/restore/

# 只解封特定檔案
tar -xzf archive.tar.gz etc/nginx/nginx.conf

# 更新封存（只加入比封存裡更新的檔案）
tar -uf archive.tar newfile.txt
```

## 壓縮格式比較

| 格式 | 副檔名 | 壓縮率 | 速度 | 適用場景 |
|------|--------|--------|------|---------|
| gzip | `.gz` | 中 | 快 | 最通用，幾乎所有系統都有 |
| bzip2 | `.bz2` | 高 | 慢 | 需要比 gzip 更好的壓縮率 |
| xz | `.xz` | 最高 | 最慢 | 發布套件（Linux kernel tarball 用這個）|
| zstd | `.zst` | 高 | 很快 | 新一代，兼顧壓縮率和速度 |

實務上：備份日常用 gzip，發布/長期儲存用 xz，追求速度用 zstd。

## 單檔壓縮指令

```bash
# gzip（會替換原始檔案）
gzip file.txt           # → file.txt.gz（原始刪除）
gzip -k file.txt        # -k = keep，保留原始
gzip -d file.txt.gz     # 解壓縮（= gunzip）
gunzip file.txt.gz      # 等同
gzip -9 file.txt        # -9 = 最高壓縮（慢），-1 = 最快（低壓縮率）
zcat file.txt.gz        # 直接讀壓縮檔內容，不解壓到磁碟

# bzip2
bzip2 file.txt          # → file.txt.bz2
bunzip2 file.txt.bz2
bzcat file.txt.bz2      # 直接讀

# xz
xz file.txt             # → file.txt.xz
unxz file.txt.xz
xzcat file.txt.xz
```

## zip（跨平台）

```bash
zip archive.zip file1.txt file2.txt
zip -r archive.zip dir/             # -r = 遞迴
unzip archive.zip                   # 解壓縮
unzip archive.zip -d /tmp/          # 解壓到指定目錄
unzip -l archive.zip                # 列出內容
```

`zip` 格式主要用在跨平台（Windows 也能用），Linux-to-Linux 傳輸通常用 `.tar.gz`。

## 動手練習

```bash
# 1. 建立一個測試目錄，打包
mkdir -p /tmp/testpack/{src,docs}
echo "main code" > /tmp/testpack/src/app.py
echo "documentation" > /tmp/testpack/docs/README.md

# 打包成 tar.gz
tar -czf /tmp/testpack.tar.gz -C /tmp testpack/
ls -lh /tmp/testpack.tar.gz

# 2. 查看封存內容（不解封）
tar -tf /tmp/testpack.tar.gz

# 3. 解封到另一個位置
mkdir /tmp/restore
tar -xzf /tmp/testpack.tar.gz -C /tmp/restore/
ls -R /tmp/restore/

# 4. 比較不同壓縮格式的大小
dd if=/dev/urandom bs=1M count=10 | cat > /tmp/bigfile 2>/dev/null
gzip  -k /tmp/bigfile
bzip2 -k /tmp/bigfile
xz    -k /tmp/bigfile
ls -lh /tmp/bigfile*
```

## 自我檢核

- [ ] 理解封存（tar）和壓縮（gzip/bzip2/xz）是兩個獨立概念
- [ ] 記住 `tar -czf` 打包，`tar -xzf` 解包，`tar -tf` 查看
- [ ] 知道 `-C` 指定解封目標目錄
- [ ] 知道 gzip 快、xz 壓縮率高

→ [Ch 9 符號連結與掛載概念](./09-symlinks-and-mount.md)
