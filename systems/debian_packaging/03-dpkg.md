# Ch 3 — dpkg：apt 的底層工具

> 目標：理解 dpkg 的功能和限制，掌握它在 apt 無法使用時的直接操作方式，以及診斷套件問題時的常用技巧。

## dpkg 做什麼（和不做什麼）

`dpkg`（Debian Package）是最底層的套件安裝工具：

**做：**
- 把 `.deb` 解包安裝到系統
- 記錄哪些檔案屬於哪個套件（資料庫在 `/var/lib/dpkg/`）
- 卸載套件、查詢套件狀態

**不做：**
- 解算依賴（這是 apt 的工作）
- 下載套件（只處理本地 .deb 檔）
- 連接到 repo

所以直接用 `dpkg -i` 裝套件，如果有缺依賴，dpkg 會失敗或警告，但不會自動去抓。

## 安裝本地 .deb 檔

```bash
# 下載一個 .deb（這裡用 curl 舉例）
apt download curl   # 下載到當前目錄

# 直接用 dpkg 安裝
sudo dpkg -i curl_7.81.0-1ubuntu1.15_amd64.deb

# 如果缺依賴，dpkg 會列出：
# dpkg: error processing package curl (--install):
#  dependency problems - leaving unconfigured
# Errors were encountered while processing: curl

# 用 apt 補齊依賴
sudo apt install -f   # -f = --fix-broken
```

`apt install -f` 是修復半損套件環境的標準方式——它讓 apt 去找缺少的依賴並安裝。

## 常用 dpkg 查詢命令

```bash
# 查詢套件狀態（格式：ii = 已安裝，rc = 已移除但有設定，un = 完全移除）
dpkg -l curl
dpkg -l "lib*"   # 支援萬用字元

# 列出套件安裝的所有檔案
dpkg -L curl

# 反查：某個檔案屬於哪個套件
dpkg -S /usr/bin/curl
dpkg -S /lib/x86_64-linux-gnu/libcurl.so.4

# 顯示套件詳細資訊
dpkg -s curl
dpkg --status curl   # 同義

# 列出 .deb 檔內容（不安裝，只看）
dpkg -c some-package.deb

# 解包 .deb 的 metadata（DEBIAN/ 目錄內容）
dpkg -e some-package.deb ./extracted-control/

# 解包 .deb 的安裝檔案（data.tar 內容）
dpkg -x some-package.deb ./extracted-data/
```

## dpkg -l 的狀態欄位

```bash
$ dpkg -l curl
Desired=Unknown/Install/Remove/Purge/Hold
| Status=Not/Inst/Conf-files/Unpacked/halF-conf/Half-inst/trig-aWait/Trig-pend
|/ Err?=(none)/Reinst-required (Status,Err: uppercase=bad)
||/ Name    Version                    Architecture Description
+++-=======-==========================-============-============================
ii  curl    7.81.0-1ubuntu1.15         amd64        command line tool for...
```

前兩個字母的意義：

| 第一個字母（Desired） | 意義 |
|---------------------|-----|
| `i` | 想要安裝（install） |
| `r` | 想要移除（remove） |
| `p` | 想要 purge |
| `h` | hold（鎖定） |

| 第二個字母（Status） | 意義 |
|--------------------|-----|
| `i` | 已安裝（installed） |
| `c` | 只剩設定檔（config-files） |
| `n` | 未安裝（not installed） |
| `u` | 已解包但未設定（unpacked） |

常見組合：`ii` = 正常安裝；`rc` = 被 remove 但有設定檔殘留；`un` = 完全不存在。

## 強制操作（小心使用）

```bash
# 強制安裝，忽略依賴問題
sudo dpkg --force-depends -i some-package.deb

# 強制移除，即使其他套件依賴它
sudo dpkg --force-remove-reinstreq -r some-package

# 重新設定已解包但未完成設定的套件
sudo dpkg --configure -a
```

`--configure -a` 是修復 dpkg 中斷後的標準命令（例如安裝過程中斷電）。

## dpkg-deb：操作 .deb 格式

`dpkg-deb` 是 dpkg 的子工具，專門處理 .deb 檔案格式：

```bash
# 查看 .deb 的 control 資訊（不解包）
dpkg-deb -f some-package.deb

# 查看 .deb 的內容列表
dpkg-deb -c some-package.deb

# 解包整個 .deb（到指定目錄）
dpkg-deb -x some-package.deb ./output/

# 只解包 DEBIAN/ 控制目錄
dpkg-deb -e some-package.deb ./control-output/

# 打包（Ch 16 會用到）
dpkg-deb --build ./my-package-dir/ my-package.deb
```

## 實際診斷場景

**場景：apt install 失敗，系統處於半裝狀態**

```bash
# 1. 看看什麼壞了
dpkg -l | grep -E "^(rc|iU|iF)"

# 2. 嘗試修復
sudo dpkg --configure -a
sudo apt install -f

# 3. 如果特定套件卡住
sudo dpkg --remove --force-remove-reinstreq <broken-pkg>
sudo apt install -f
```

**場景：想看某個 .deb 裝了哪些東西（不安裝）**

```bash
apt download nginx
dpkg -c nginx_*.deb | head -30
dpkg-deb -f nginx_*.deb   # 看 control 資訊
```

## 自我檢核

- [ ] `dpkg` 不解算依賴、不連網；直接裝 .deb 缺依賴要用 `apt install -f` 補
- [ ] `dpkg -l` 狀態：`ii` = 正常；`rc` = 移除但有設定殘留；`un` = 完全沒有
- [ ] `dpkg -L` 列出已裝檔案；`dpkg -S` 反查檔案所屬套件
- [ ] `dpkg --configure -a` 修復中斷的安裝
- [ ] `dpkg-deb -x` 解包 .deb 到目錄；`dpkg-deb --build` 打包（後面章節用）

→ [Ch 4 apt vs apt-get vs aptitude](./04-apt-variants.md)
