# 練習 A — 修復壞掉的依賴環境

> 目標：把 Ch 1–15 學到的 dpkg/apt 底層知識拼起來，用正確工具診斷並修復各種「依賴地獄」情境，不靠猜測，每步都能解釋為什麼這樣做。

## 任務規格

你要處理四個獨立的壞掉情境。每個情境提供初始狀態，你要：

1. 診斷問題根因（不是憑直覺猜，要有指令輸出支撐）
2. 找出修復方案
3. 執行修復並驗證

不要用「砍掉重裝系統」或 `apt-get -f install` 一行解決然後不理解為什麼——理解比修好更重要。

## 情境 A：dpkg 半裝狀態（interrupted install）

### 初始狀態

```bash
# 模擬：在 dpkg -i 過程中強制中斷
sudo dpkg -i some-package_1.0-1_amd64.deb
# <Ctrl+C 強制中斷>

# 現在的狀態
dpkg -l some-package
# iF  some-package   1.0-1   ...   半安裝狀態

sudo apt update
# E: dpkg was interrupted, you must manually run
#    'sudo dpkg --configure -a' to correct the problem.
```

### 你的任務

1. 解釋 `dpkg -l` 輸出中 `iF` 的兩個字母各代表什麼
2. 指出 `/var/lib/dpkg/` 的哪個檔案記錄了這個狀態
3. 執行正確的修復命令，解釋每個旗標的意義
4. 驗證修復後狀態

<details>
<summary>參考解答（寫完再看！）</summary>

```bash
# 1. iF 解析：
# 第一欄 i = 使用者的期望狀態（install）
# 第二欄 F = 目前實際狀態（F = half-configured，配置未完成）
# 完整欄位說明：dpkg -l 輸出格式是「期望/狀態/錯誤」三個字母

# 2. 記錄位置
cat /var/lib/dpkg/status
# 找到 some-package 條目，看到：
# Status: install half-configured
# 或
# Status: install half-installed

# 3. 修復
sudo dpkg --configure -a
# --configure：執行還沒完成的 postinst 腳本
# -a：對所有處於 half-configured/unpacked 狀態的套件執行

# 如果 configure 失敗（套件本身有 bug）：
sudo dpkg --remove --force-remove-reinstreq some-package
# --force-remove-reinstreq：強制移除「要求重新安裝」旗標的套件

# 4. 驗證
dpkg -l some-package
# 應看到 ii（installed, installed）

# 確認 apt 不再報錯
sudo apt update
```

</details>

## 情境 B：手動安裝 .deb 破壞了依賴

### 初始狀態

```bash
# 從網路下載了一個針對 Ubuntu 22.04 打包的 .deb
# 在 Ubuntu 24.04 上強制安裝
sudo dpkg -i --force-depends libfoo3_2.0_amd64.deb
# 警告：ignoring dependency problems with libfoo3

# 現在
sudo apt install anything
# The following packages have unmet dependencies:
#  libfoo3 : Depends: libc6 (>= 2.35) but 2.33 is installed
# E: Unable to correct problems, you have held broken packages.
```

### 你的任務

1. 用指令確認 `libfoo3` 目前的確切狀態（不是猜，要有指令輸出）
2. 找出 `libfoo3` 聲稱需要哪個版本的 `libc6`，實際裝了哪個版本
3. 分析：為什麼 `--force-depends` 裝進去後 apt 完全卡死（不能裝任何東西）
4. 移除 `libfoo3` 並讓系統回到正常狀態

<details>
<summary>參考解答</summary>

```bash
# 1. 確認狀態
dpkg -l libfoo3
# iHR libfoo3 ...  ← H = hold，R = reinst-required（依賴破損）

apt-cache show libfoo3 | grep Depends
# Depends: libc6 (>= 2.35), ...

dpkg -s libfoo3
# Status: install ok installed   ← 或 install reinst-required

# 2. 版本比對
dpkg -s libc6 | grep Version
# Version: 2.33-1

# libfoo3 要求 >= 2.35，實際只有 2.33 → 依賴不滿足

# 3. 為什麼 apt 卡死
# apt 的依賴求解器把整個系統視為一個狀態機。
# 當有套件存在未滿足的依賴時，apt 拒絕執行任何操作，
# 因為它不知道接下來的操作會不會讓狀態更差。
# --force-depends 繞過了 dpkg 的檢查，但留下了「依賴破損」的標記。

# 4. 移除並修復
# 方法一：如果 dpkg -r 能移除
sudo dpkg -r libfoo3
sudo apt -f install     # 修復剩餘依賴問題

# 方法二：如果 dpkg -r 失敗（因為有其他套件依賴 libfoo3）
sudo dpkg -r --force-depends libfoo3

# 方法三：讓 apt 嘗試自動修復
sudo apt install -f     # -f = fix-broken

# 驗證
apt list --installed 2>/dev/null | grep libfoo3  # 應該消失
sudo apt update && sudo apt install curl          # 應該能正常安裝
```

</details>

## 情境 C：Pin 設定造成套件無法更新

### 初始狀態

```bash
# 系統有套件 nginx，但一直停在舊版
apt-cache policy nginx
# nginx:
#   Installed: 1.18.0-1
#   Candidate: 1.18.0-1    ← 明明 repo 有新版但 Candidate 沒更新
#   Version table:
#  *** 1.18.0-1 500
#         100 /var/lib/dpkg/status
#     1.24.0-1 500
#         500 http://archive.ubuntu.com/ubuntu jammy/main amd64 Packages

# 有人在系統上做了什麼設定讓 nginx 被釘住
```

### 你的任務

1. 找出是什麼讓 `nginx` 無法更新（可能是 `apt-mark hold` 或 pin 設定）
2. 確認 pin 設定的位置和內容
3. 解除限制，讓 nginx 可以正常更新到最新版
4. 驗證 `apt-cache policy nginx` 的 Candidate 變了

<details>
<summary>參考解答</summary>

```bash
# 1. 找到限制來源
# 方法一：檢查 hold 狀態
apt-mark showhold
# nginx    ← 被 hold 住

# 方法二：如果不是 hold，找 pin 設定
ls /etc/apt/preferences.d/
cat /etc/apt/preferences.d/nginx

# 典型的 pin 設定長這樣：
# Package: nginx
# Pin: version 1.18.*
# Pin-Priority: 1001    ← 比 500 高，強制保持舊版

# 2. 確認 pin 計算
apt-cache policy nginx
# 看 Candidate 和 Version table 旁邊的優先度數字

# 3a. 如果是 hold：解除
sudo apt-mark unhold nginx
apt-mark showhold           # nginx 應該消失

# 3b. 如果是 pin 設定：移除設定檔
sudo rm /etc/apt/preferences.d/nginx
sudo apt update

# 3c. 如果兩個都有，兩個都要清
sudo apt-mark unhold nginx
sudo rm -f /etc/apt/preferences.d/nginx
sudo apt update

# 4. 驗證
apt-cache policy nginx
# Candidate: 1.24.0-1   ← 應該更新了

# 執行升級
sudo apt install nginx   # 或 sudo apt upgrade nginx
```

</details>

## 情境 D：套件 A 和 B 互相衝突，但我都需要

### 初始狀態

```bash
sudo apt install curl wget
# Reading package lists... Done
# The following packages have unmet dependencies:
#  fake-pkg-a : Conflicts: fake-pkg-b
#  fake-pkg-b : Conflicts: fake-pkg-a
# E: Unable to correct problems, you have held broken packages.

# 更真實的版本：
# nginx 和 apache2 都需要 port 80，某些版本的打包加了 Conflicts
sudo apt install nginx apache2
# apache2 : Conflicts: nginx
```

### 你的任務

1. 查出衝突的具體定義（哪個 control 欄位，哪個套件聲明了衝突）
2. 分析三種解法的取捨：
   - a. 只裝其中一個
   - b. 用虛擬 IP / 不同 port 同時跑兩者
   - c. 用 `--force-conflicts`（危險！解釋風險）
3. 查詢 `Breaks` 和 `Conflicts` 的差異，說明什麼情境用哪個

<details>
<summary>參考解答</summary>

```bash
# 1. 找衝突定義
apt-cache show nginx | grep -E "Conflicts|Breaks"
apt-cache show apache2 | grep -E "Conflicts|Breaks"

# 用 apt-cache showpkg 看更完整的衝突資訊
apt-cache showpkg nginx

# 2. 三種解法分析

# a. 只裝其中一個（最安全）
sudo apt install nginx
# 如果不需要 apache2，這是正確答案

# b. 同時跑兩者（如果衝突只是 port）
# 修改其中一個的設定檔讓它用不同 port
# 但注意：如果 Conflicts 寫死了，dpkg 層級就不讓你同時裝
# 這時只能用容器隔離

# c. --force-conflicts（只用於緊急維護，明白風險再用）
sudo dpkg -i --force-conflicts nginx.deb
# 風險：
# - apt 往後每次操作都可能出現警告
# - 兩個套件可能爭用相同的 /etc 設定檔
# - 升級時 dpkg 可能又報衝突

# 3. Conflicts vs Breaks 的差異
# Conflicts：不能同時安裝（dpkg 層級禁止共存）
#   → apt 會提議移除其中一個
#   → 適用：爭用同一個 port / config 路徑 / binary 名稱

# Breaks：可以同時安裝，但某功能會壞掉
#   → apt 會提議升級被 break 的套件
#   → 適用：API 不相容（舊版 libfoo1 + 新版需要 libfoo2 的 app）
#   → Breaks 通常配 Replaces 一起用（替換舊套件的檔案）
```

</details>

## 測試用例（自我驗證）

完成四個情境後，確認：

```bash
# 整體系統狀態乾淨
dpkg -l | grep -E "^[a-z][HIUFWPC]"   # 應該沒有非 ii 的 installed 套件

# apt 能正常運作
sudo apt update && echo "APT OK"

# 沒有殘留的強制標記
apt-mark showhold   # 空的（或只有你明確要 hold 的）
```

## 自我檢核

- [ ] `dpkg -l` 的三欄狀態碼（期望/實際/錯誤）能逐字解讀
- [ ] `dpkg --configure -a` vs `apt -f install` vs `dpkg --remove --force-remove-reinstreq` 的使用時機
- [ ] `--force-depends`：繞過 dpkg 檢查，但 apt 還是知道依賴破損
- [ ] Pin-Priority > 1000 = 強制降版；apt-mark hold = 不升不降
- [ ] `Conflicts` 禁止共存；`Breaks + Replaces` 是「新版取代舊版」的正確寫法

→ [練習 B：把自己的程式打包成可安裝的 deb](./practice-b-package-your-tool.md)
