# Ch 16 — 手工建立第一個 deb

> 目標：不用任何打包工具，從零手工組出一個可以 `dpkg -i` 安裝的 .deb 檔案，徹底理解 deb 結構。

## 我們要打包什麼

一個簡單的 shell 腳本 `greet`：

```bash
#!/bin/bash
echo "Hello, $1!"
```

打包後，使用者可以：
```bash
sudo dpkg -i greet_1.0-1_all.deb
greet World
# Hello, World!
```

## 目錄結構

手工打包的目錄結構：

```
greet-1.0/
├── DEBIAN/
│   ├── control          ← 必填：套件 metadata
│   ├── postinst         ← 選填：安裝後腳本
│   └── md5sums          ← 選填（dpkg-deb 會自動生成）
└── usr/
    ├── bin/
    │   └── greet        ← 實際安裝的程式
    └── share/
        └── doc/
            └── greet/
                └── changelog.Debian.gz  ← 選填但 lintian 喜歡
```

規則：`DEBIAN/` 目錄外的所有東西都會被安裝到系統根目錄。`usr/bin/greet` 裝到 `/usr/bin/greet`。

## Step 1：建立目錄結構

```bash
mkdir -p greet-1.0/DEBIAN
mkdir -p greet-1.0/usr/bin
mkdir -p greet-1.0/usr/share/doc/greet
```

## Step 2：寫程式本體

```bash
cat > greet-1.0/usr/bin/greet << 'EOF'
#!/bin/bash
if [ -z "$1" ]; then
    echo "Usage: greet <name>"
    exit 1
fi
echo "Hello, $1!"
EOF

# 設定執行權限（重要！沒有 +x 裝進去也跑不了）
chmod 755 greet-1.0/usr/bin/greet
```

## Step 3：寫 control 檔

```bash
cat > greet-1.0/DEBIAN/control << 'EOF'
Package: greet
Version: 1.0-1
Architecture: all
Maintainer: Your Name <you@example.com>
Installed-Size: 4
Depends: bash (>= 4.0)
Section: utils
Priority: optional
Description: A simple greeting tool
 Greet a person by name from the command line.
 .
 This is a demonstration package for learning Debian packaging.
EOF
```

注意：
- `Description` 的第二行起要有一個空格縮排
- 空行用 ` .`（一個空格加一個點）
- `Installed-Size` 用 KB 為單位（這裡是 4KB，實際更小但要填個值）

## Step 4：產生 md5sums

```bash
# 在 greet-1.0/ 目錄執行（路徑要相對於套件根目錄）
cd greet-1.0
find . -type f ! -path './DEBIAN/*' \
    | sort \
    | xargs md5sum \
    | sed 's|^\.\./||; s| \./| |' \
    > DEBIAN/md5sums
cat DEBIAN/md5sums
cd ..
```

## Step 5：打包成 .deb

```bash
# 方法 1：用 dpkg-deb（推薦）
dpkg-deb --build --root-owner-group greet-1.0/

# 這會產生 greet-1.0.deb（dpkg-deb 自動命名）
# 或指定輸出檔名
dpkg-deb --build --root-owner-group greet-1.0/ greet_1.0-1_all.deb

# 方法 2：用 dpkg --build（等價）
dpkg --build greet-1.0/ greet_1.0-1_all.deb
```

`--root-owner-group` 讓 dpkg-deb 把所有檔案的 owner 設為 root:root（不然會繼承當前用戶的 uid/gid）。

## Step 6：驗證 .deb

```bash
# 查看結構
dpkg-deb -c greet_1.0-1_all.deb   # data 部分
dpkg-deb -f greet_1.0-1_all.deb   # control 資訊

# 用 ar 查看三個成員
ar -t greet_1.0-1_all.deb
```

```
debian-binary
control.tar.xz
data.tar.xz
```

## Step 7：安裝和測試

```bash
# 安裝
sudo dpkg -i greet_1.0-1_all.deb

# 測試
greet World
# Hello, World!

greet
# Usage: greet <name>

# 確認安裝了什麼
dpkg -L greet

# 確認套件資訊
dpkg -s greet

# 移除
sudo apt remove greet
# 或
sudo dpkg -r greet
```

## 常見錯誤

**錯誤 1：`dpkg-deb: error: control directory has bad permissions`**

```bash
# DEBIAN/ 目錄必須是 755
chmod 755 greet-1.0/DEBIAN

# control 檔必須是 644
chmod 644 greet-1.0/DEBIAN/control
```

**錯誤 2：`dpkg-deb: error: control file has field 'Description' which is not terminated by a newline`**

control 檔最後必須有一個空行：

```bash
# 確認 control 最後有換行
tail -c1 greet-1.0/DEBIAN/control | xxd   # 應看到 0a（\n）
```

**錯誤 3：安裝後 greet 跑不了（Permission denied）**

```bash
# 檢查執行權限
ls -l greet-1.0/usr/bin/greet   # 應是 -rwxr-xr-x
# 修復後重新打包
chmod 755 greet-1.0/usr/bin/greet
dpkg-deb --build --root-owner-group greet-1.0/ greet_1.0-1_all.deb
```

## 完整腳本（一次跑完）

```bash
#!/bin/bash
set -e

PKG="greet"
VER="1.0-1"
ARCH="all"

# 建立結構
rm -rf ${PKG}-build
mkdir -p ${PKG}-build/DEBIAN
mkdir -p ${PKG}-build/usr/bin

# 程式
cat > ${PKG}-build/usr/bin/${PKG} << 'SCRIPT'
#!/bin/bash
[ -z "$1" ] && { echo "Usage: greet <name>"; exit 1; }
echo "Hello, $1!"
SCRIPT
chmod 755 ${PKG}-build/usr/bin/${PKG}

# control
cat > ${PKG}-build/DEBIAN/control << CTRL
Package: ${PKG}
Version: ${VER}
Architecture: ${ARCH}
Maintainer: Build Script <build@example.com>
Installed-Size: 4
Depends: bash (>= 4.0)
Section: utils
Priority: optional
Description: A simple greeting tool
 Example package built by hand.
CTRL

# md5sums
cd ${PKG}-build
find . -type f ! -path './DEBIAN/*' | sort | xargs md5sum \
    | sed 's| \./| |' > DEBIAN/md5sums
cd ..

# 打包
dpkg-deb --build --root-owner-group ${PKG}-build/ ${PKG}_${VER}_${ARCH}.deb
echo "Built: ${PKG}_${VER}_${ARCH}.deb"
```

## 自我檢核

- [ ] 手工 deb 結構：`DEBIAN/`（metadata）+ 其他目錄（安裝到系統的檔案）
- [ ] `DEBIAN/control` 必填欄位：Package、Version、Architecture、Maintainer、Description
- [ ] `chmod 755` 給程式本體和 `DEBIAN/` 目錄；`644` 給 `DEBIAN/control`
- [ ] `dpkg-deb --build --root-owner-group <dir> <output.deb>` 打包
- [ ] `dpkg-deb -c` 看 data；`dpkg-deb -f` 看 control

→ [Ch 17 debian/ 目錄結構全覽](./17-debian-directory.md)
