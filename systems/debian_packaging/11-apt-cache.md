# Ch 11 — APT 快取與本地儲存

> 目標：理解 APT 把東西存在哪裡、為什麼存、以及在離線或受限環境下如何管理本地套件快取。

## APT 的兩個主要儲存區

```
/var/cache/apt/          ← 下載快取（.deb 檔案）
/var/lib/apt/            ← 套件 metadata（列表、狀態）
/var/lib/dpkg/           ← dpkg 的資料庫（已安裝套件資訊）
```

## /var/cache/apt/

```bash
ls /var/cache/apt/
# archives/           ← 已下載的 .deb 檔
# archives/partial/   ← 下載中的 .deb（斷點續傳）
# pkgcache.bin        ← 已解析的套件快取（二進位格式，加速 apt 啟動）
# srcpkgcache.bin     ← 源碼套件快取
```

`archives/` 目錄裡的 .deb 是 apt 下載但尚未清除的套件。安裝完後這些 .deb 還留著，直到你執行 `apt clean`。

```bash
# 看快取用了多少空間
du -sh /var/cache/apt/archives/

# 清除所有下載的 .deb（釋放空間，下次安裝要重新下載）
sudo apt clean

# 只清除舊版本（保留最新版本的 .deb）
sudo apt autoclean
```

**為什麼 apt clean 後重新安裝要重新下載？**

`apt clean` 刪除了 `archives/` 裡的 .deb，下次 `apt install` 時沒有快取，只能重新從 repo 下載。在頻寬受限的環境（嵌入式系統、CI）要注意。

## /var/lib/apt/

```bash
ls /var/lib/apt/lists/
# tw.archive.ubuntu.com_ubuntu_dists_jammy_main_binary-amd64_Packages
# tw.archive.ubuntu.com_ubuntu_dists_jammy_universe_binary-amd64_Packages
# ...
```

這是 `apt update` 下載的套件 metadata。每個 repo 的每個 component 對應一個壓縮的 Packages 檔（或 InRelease 簽章檔）。

```bash
# 看 metadata 用了多少空間
du -sh /var/lib/apt/lists/

# 清除（下次 apt update 重新下載）
sudo rm -rf /var/lib/apt/lists/*
sudo apt update   # 重建

# 列出所有 metadata 檔案（按時間排序）
ls -lt /var/lib/apt/lists/ | head -20
```

一個 Packages 檔的結構（人類可讀格式）：

```bash
# 查看 main 的套件列表（解壓後）
zcat /var/lib/apt/lists/tw.archive.ubuntu.com_ubuntu_dists_jammy_main_binary-amd64_Packages | head -50
```

```
Package: 0ad
Version: 0.0.26-1
Architecture: amd64
Maintainer: Debian Games Team <pkg-games-devel@lists.alioth.debian.org>
Installed-Size: 28808
Depends: 0ad-data (>= 0.0.26), 0ad-data (<= 0.0.26-1), ...
Filename: pool/main/0/0ad/0ad_0.0.26-1_amd64.deb
Size: 6256704
MD5sum: ...
SHA256: ...
Description: Real-time strategy game of ancient warfare
...
```

## /var/lib/dpkg/

```bash
ls /var/lib/dpkg/
# info/        ← 每個已裝套件的詳細資訊
# status       ← 所有套件的狀態資料庫
# available    ← 可用套件列表（dpkg 的版本）
# lock         ← 鎖定檔（防止多個 dpkg 同時執行）
```

```bash
# status 是純文字，可以直接讀
grep -A5 "^Package: curl" /var/lib/dpkg/status
```

```
Package: curl
Status: install ok installed
Priority: optional
Section: web
Installed-Size: 448
Maintainer: Ubuntu Developers...
Architecture: amd64
Version: 7.81.0-1ubuntu1.15
Depends: libc6 (>= 2.17), libcurl4 (= 7.81.0-1ubuntu1.15), zlib1g (>= 1:1.1.4)
```

```bash
ls /var/lib/dpkg/info/ | grep "^curl"
# curl.conffiles
# curl.list        ← 裝了哪些檔案
# curl.md5sums
```

## 離線安裝：打包 .deb 快取

在沒有網路的伺服器上安裝套件：

```bash
# 在有網路的機器上，下載所有需要的 .deb（包含依賴）
sudo apt install --download-only nginx
# 或下載到指定目錄
apt download nginx
apt download $(apt-cache depends --recurse --no-recommends nginx | grep "^\w" | sort -u)

# 複製 /var/cache/apt/archives/*.deb 到目標機器

# 在目標機器上安裝（用 dpkg）
sudo dpkg -i /path/to/debs/*.deb

# 如果有依賴順序問題
sudo apt install -f
```

更完整的方案：`apt-offline`（專門為離線場景設計的工具）。

## 建立本地 .deb 快取 repo

```bash
# 安裝 dpkg-dev（提供 dpkg-scanpackages）
sudo apt install dpkg-dev

# 把 .deb 放到一個目錄
mkdir ~/local-repo
cp /var/cache/apt/archives/*.deb ~/local-repo/

# 生成 Packages 索引
cd ~/local-repo
dpkg-scanpackages . /dev/null | gzip -9c > Packages.gz

# 加入 sources.list
echo "deb [trusted=yes] file:///home/$USER/local-repo ./" \
    | sudo tee /etc/apt/sources.list.d/local.list

# 更新並使用
sudo apt update
apt install curl   # 優先從本地 repo 拿
```

這是最簡單的本地 repo（沒有 GPG 簽章），`trusted=yes` 跳過簽章驗證。Ch 23 會用 reprepro 架更完整的私有 repo。

## 自我檢核

- [ ] `/var/cache/apt/archives/` = 下載的 .deb 快取；`apt clean` 清除
- [ ] `/var/lib/apt/lists/` = `apt update` 下載的 metadata（Packages 列表）
- [ ] `/var/lib/dpkg/status` = 所有套件狀態的純文字資料庫
- [ ] `/var/lib/dpkg/info/<pkg>.list` = 該套件裝了哪些檔案
- [ ] `dpkg-scanpackages . | gzip > Packages.gz` 生成本地 repo 索引

→ [Ch 12 APT 依賴解決演算法](./12-dependency-solver.md)
