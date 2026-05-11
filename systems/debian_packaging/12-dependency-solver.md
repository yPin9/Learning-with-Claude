# Ch 12 — APT 依賴解決演算法

> 目標：理解 APT 如何把「我要裝 A」這個請求解算成「需要下載安裝 A、B、C、D 並移除 X」的完整計畫，以及依賴衝突時 APT 和 aptitude 的策略差異。

## 依賴解算是個難問題

從理論上說，套件依賴解算（Dependency Solving）是 **NP-Complete** 問題——在最壞情況下等價於 SAT（布林可滿足性）問題。

但實際上，現實套件庫的結構讓它通常能快速解決。APT 和 aptitude 用不同策略：

| 工具 | 演算法策略 |
|-----|----------|
| APT | 貪婪 + 回溯（速度優先） |
| aptitude | 完整 SAT 解算（更完整，但可能慢） |
| libsolv（dnf 等用）| CDCL SAT solver |

## APT 解算的步驟

輸入：`apt install nginx`

```
Step 1：讀取 Packages 列表
         找到所有 nginx 的版本（來自各個 repo）

Step 2：選擇候選版本（Candidate Selection）
         用 pinning priority 決定用哪個版本的 nginx

Step 3：解析依賴（Dependency Resolution）
         nginx depends: libc6, libssl3, libpcre3, zlib1g...
         對每個依賴遞迴執行同樣步驟

Step 4：衝突偵測（Conflict Detection）
         檢查 Conflicts / Breaks
         如果 nginx Conflicts: apache2 且已裝 apache2 → 計畫移除 apache2

Step 5：生成安裝計畫（Install Plan）
         輸出：安裝 [nginx, libpcre3...]、升級 [libssl3]、移除 [apache2]

Step 6：確認並執行（使用者確認 Y/n 後）
```

## 版本選擇（Candidate Selection）

APT 用 pinning priority 選哪個版本：

```bash
apt-cache policy nginx
```

```
nginx:
  Installed: (none)
  Candidate: 1.24.0-1~jammy   ← 會安裝這個
  Version table:
     1.24.0-1~jammy 500        ← priority 500（nginx 官方 repo）
        500 https://nginx.org/packages/ubuntu jammy/nginx amd64 Packages
     1.18.0-6ubuntu14.4 500    ← priority 500（Ubuntu repo）
        500 http://tw.archive.ubuntu.com/ubuntu jammy-updates/main amd64 Packages
```

同 priority 時，版本號較高的優先。

## OR 依賴的選擇

```
Depends: libcurl4-openssl-dev | libcurl4-gnutls-dev | libcurl4-nss-dev
```

APT 按列出的順序嘗試：
1. 先看 `libcurl4-openssl-dev` 能不能滿足（通常能）
2. 如果衝突，嘗試 `libcurl4-gnutls-dev`
3. 再衝突，嘗試 `libcurl4-nss-dev`

不一定選「最好的」，選第一個能滿足的。

## 看 APT 的解算過程

```bash
# -s = simulate（模擬，不真的安裝）
apt install -s nginx

# 更詳細的輸出
apt install -s nginx --verbose-versions

# 解析 APT 的內部日誌（在執行期間）
APT_LOG=/tmp/apt.log apt install -y nginx 2>&1 | tee /tmp/apt-output.log
```

```
The following NEW packages will be installed:
  libnginx-mod-http-gzip-static libnginx-mod-http-image-filter ...
  nginx nginx-common nginx-core
The following packages will be upgraded:
  libssl3
0 to upgrade, 6 to newly install, 1 to upgrade, 0 to remove
Need to get 1,234 kB of archives.
After this operation, 3,456 kB of additional disk space will be used.
```

## 衝突解算：aptitude 的優勢

當 APT 遇到複雜衝突無法解算時：

```bash
$ sudo apt install package-a package-b
E: Error, pkgProblemResolver::Resolve generated breaks, this may be caused by held packages.
```

這時換用 aptitude：

```bash
$ sudo aptitude install package-a package-b

The following packages have unmet dependencies:
  package-a: Depends: libfoo1 (= 1.0) but 2.0 is installed

The following actions will resolve these dependencies:

  Install the following packages:
  1) libfoo1 1.0 [1.0 MB]

  Keep the following packages at their current version:
  2) package-b [Marked as manual install]

  Accept this solution? [Y/n/q/?]
```

aptitude 給你選擇：
- 方案 1：安裝舊版 libfoo1（可能影響其他套件）
- 方案 2：不安裝 package-b（只裝 package-a）
- 按 `n` 看下一個方案

## 為什麼 `apt remove` 有時跑很慢

`apt remove` 不只是移除一個套件——它要：
1. 確認沒有其他套件依賴它（若有，提示用 `--auto-remove`）
2. 計算孤兒套件（沒有任何套件依賴的自動安裝套件）
3. 生成移除計畫

```bash
# 看 remove 計劃會動到哪些東西
apt remove --dry-run nginx
```

## Debian 的 EDSP（External Dependency Solver Protocol）

APT 4.0+ 支援外部 solver（EDSP）——可以換成別的 SAT solver 來解算依賴：

```bash
# 用 aspcud 替換內建 solver（更完整）
sudo apt install aspcud
apt install -o APT::Solver=aspcud nginx
```

這在超複雜依賴環境（Debian Testing/Unstable）有時能解開 APT 解不了的衝突。

## 自我檢核

- [ ] 依賴解算理論上是 NP-Complete（等價於 SAT），但實際套件庫結構讓它通常很快
- [ ] APT 解算步驟：選候選版本 → 遞迴解析依賴 → 偵測衝突 → 生成計畫
- [ ] OR 依賴（`|`）：APT 按順序選第一個能滿足的，不一定選「最好的」
- [ ] APT 遇到複雜衝突報錯時，改用 `aptitude` 可以得到多個解決方案
- [ ] `apt install -s` 模擬安裝，不動系統，看解算結果

→ [Ch 13 Repository 結構](./13-repo-structure.md)
