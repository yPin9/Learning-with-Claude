# Ch 15 — Clean build：sbuild 與 pbuilder

> **目標**：理解為什麼必須在乾淨隔離的環境 build、sbuild/pbuilder 如何用 chroot 提供這個環境、如何建立和維護 build chroot、以及怎麼用它抓出「漏宣告的 build 依賴」。

> **環境**：sbuild 0.85.x、schroot、Debian 12。本章以 sbuild 為主（Debian 官方推薦），pbuilder 作對照。

## 為什麼「在我機器上 build 成功」不夠？

你在自己的開發機 `dpkg-buildpackage` 成功了。但你的機器裝了幾百個套件——其中某些 `-dev` 套件「剛好」存在，讓 build 找到了它需要但**沒宣告**的 header 或 library。

到了 Debian build farm（只裝宣告的 Build-Depends 的最小環境）或別人的乾淨機器，build 就失敗了——因為那個「剛好存在」的東西不在。

```
你的開發機：                    乾淨 build 環境（build farm）：
裝了 500 個套件                  只裝 Build-Depends 列的套件
  build 找到 libfoo-dev          libfoo-dev 不在
  （你忘了宣告它）                build 失敗！

「在我機器上能 build」            「在乾淨環境能 build」
≠ 套件正確                       = 套件正確
```

clean build 的鐵律：**只有在「只裝宣告依賴的最小環境」裡 build 成功，才證明你的 Build-Depends 完整。** sbuild/pbuilder 提供這個環境。

## 先建立直覺：chroot 是個拋棄式的最小系統

```
sbuild 的工作流：

  乾淨的 chroot tarball（只有 base system）
        │  每次 build 複製一份（用完即丟）
        ▼
  臨時 build 環境
        │  1. 只裝這個套件宣告的 Build-Depends
        │  2. 解開 source
        │  3. dpkg-buildpackage
        ▼
  產出 .deb（複製出來）
        │
  整個臨時環境銷毀（host 完全不被污染）
```

每次 build 都從乾淨的 base 開始，只裝宣告的依賴。如果 build 需要某個沒宣告的東西，它在這個環境裡不存在，build 就失敗——精準暴露漏宣告的依賴。

## sbuild vs pbuilder

兩者目標相同（乾淨 chroot build），機制略不同：

| 面向 | sbuild | pbuilder |
|---|---|---|
| chroot 管理 | schroot（持久 chroot + overlay）| 每次解壓 tarball |
| 速度 | 較快（overlay/snapshot）| 較慢（每次解壓）|
| Debian 官方 | **推薦**（build farm 用 sbuild）| 也支援，較老 |
| 設定複雜度 | 中 | 低 |

> Debian 官方 build farm 用 sbuild，所以用 sbuild 最貼近「真實的 build 環境」。pbuilder 較簡單但較慢。本章教 sbuild，概念對 pbuilder 通用。

## 設定 sbuild

```bash
# 1. 安裝
sudo apt install sbuild schroot debootstrap

# 2. 把自己加進 sbuild group（之後不用 sudo）
sudo sbuild-adduser $USER
# 登出再登入讓 group 生效

# 3. 建立 bookworm 的 build chroot
sudo sbuild-createchroot \
    --include=eatmydata,ccache,gnupg \
    bookworm \
    /srv/chroot/bookworm-amd64-sbuild \
    http://deb.debian.org/debian
#   --include=eatmydata : 加速（停用 fsync，build 環境不需要持久化）
#   bookworm            : 目標 suite
#   /srv/chroot/...     : chroot 存放位置

# 4. 確認 chroot 建立成功
schroot -l
# chroot:bookworm-amd64-sbuild
```

`debootstrap`（被 sbuild-createchroot 呼叫）從零建立一個最小的 Debian 系統——只有 essential 套件，沒有任何開發工具。這就是 build 的乾淨起點。

## 用 sbuild build 套件

```bash
# 在 source 目錄上層（要有 .dsc）
# 先打包 source
cd greet-1.0/
dpkg-buildpackage -S -us -uc    # 產生 .dsc + tarballs
cd ..

# 用 sbuild build
sbuild -d bookworm greet_1.0-1.dsc
#   -d bookworm : 用 bookworm chroot
#   讀 .dsc 的 Build-Depends，在乾淨 chroot 裡裝齊、build

# sbuild 會：
# 1. 複製乾淨 chroot
# 2. apt install 所有 Build-Depends
# 3. dpkg-buildpackage
# 4. 把 .deb 複製出來
# 5. 跑 lintian
# 6. 銷毀臨時環境
```

輸出會有完整的 build log，包括它在 chroot 裡裝了哪些依賴。最後告訴你 build 成功與否、lintian 結果。

## 故意弄壞：漏宣告 build 依賴

這是 sbuild 最大的價值——抓出在 host 隱藏的依賴問題。

```bash
# 假設 greet 其實需要 libssl-dev，但你忘了寫進 Build-Depends
# 在你的開發機（裝了 libssl-dev）：
dpkg-buildpackage -b
# 成功！（因為你的機器剛好有 libssl-dev）

# 在 sbuild 乾淨環境：
sbuild -d bookworm greet_1.0-1.dsc
# ...
# configure: error: openssl/ssl.h not found
# E: Build failure (dpkg-buildpackage died)
# ↑ 乾淨環境沒有 libssl-dev，暴露了漏宣告！
```

修正：把 `libssl-dev` 加進 `debian/control` 的 `Build-Depends`，重新 build。這就是 sbuild 的核心價值——它讓你的 Build-Depends 誠實。

## chroot 維護

```bash
# 更新 chroot 裡的套件（定期做，保持與 archive 同步）
sudo sbuild-update -udcar bookworm
#   -u: update  -d: dist-upgrade  -c: clean  -a: autoclean  -r: autoremove

# 進入 chroot 互動（debug 用）
sudo schroot -c bookworm-amd64-sbuild
# （現在你在乾淨的 chroot 裡，可以檢查環境）
exit

# 列出所有 chroot
schroot -l
```

## sbuild 的進階設定

`~/.sbuildrc` 客製化 sbuild 行為：

```perl
# ~/.sbuildrc
$build_arch_all = 1;          # 也 build arch:all 套件（-A）
$build_source = 0;            # 不重新 build source
$run_lintian = 1;             # build 後跑 lintian
$lintian_opts = ['-i', '-I']; # lintian 詳細模式
$run_autopkgtest = 0;         # 是否跑 autopkgtest（Ch 17）
$ccache_dir = "/var/cache/ccache-sbuild";  # ccache 加速重複編譯
```

> 設 `$run_lintian = 1` 讓 sbuild 自動跑 lintian，一站式檢查。進階可開 `$run_autopkgtest` 讓它同時跑功能測試（Ch 17）。

## 用容器替代 chroot：unshare 與 podman

新版 sbuild 支援用 user namespace（`unshare`）或容器後端，不需要 root 設定 schroot：

```bash
# sbuild 的 unshare 後端（不需要 schroot，較新）
sudo apt install sbuild mmdebstrap uidmap

# 用 mmdebstrap 建 chroot tarball
mmdebstrap bookworm /srv/chroot/bookworm.tar.zst \
    http://deb.debian.org/debian

# 用 unshare 後端 build（無需 root）
sbuild --chroot-mode=unshare \
    --chroot=/srv/chroot/bookworm.tar.zst \
    -d bookworm greet_1.0-1.dsc
```

> `--chroot-mode=unshare` 是現代趨勢——用 user namespace 隔離，不需要 setuid 的 schroot，安全性更好，CI 環境（Ch 32）也更容易設定。新環境推薦這個模式。

## 踩雷集錦

1. **在 host build 就以為依賴對了**：host 系統的「剛好存在」會掩蓋漏宣告的 Build-Depends。一定要在 sbuild 乾淨環境驗證才算數

2. **chroot 太久沒更新**：chroot 裡的套件版本停在建立時。久了和 archive 脫節，build 出的東西依賴的版本不對。定期 `sbuild-update`

3. **忘記先打包 source（.dsc）**：sbuild 吃 `.dsc`，不是直接吃 source 目錄。要先 `dpkg-buildpackage -S` 產生 `.dsc`

4. **sbuild group 沒生效**：`sbuild-adduser` 後沒登出登入，group 沒生效，sbuild 報權限錯誤。重新登入

5. **eatmydata 的誤解**：`--include=eatmydata` 停用 fsync 加速 build，這在拋棄式 build 環境是安全的（反正環境用完就丟）。但別在需要持久化資料的地方用 eatmydata

6. **以為 sbuild 慢就不用**：sbuild 確實比 host build 慢（要複製 chroot、裝依賴），但它抓出的依賴問題在後期（上傳被退、別人裝不起來）修起來貴得多。值得

## 進階：為什麼 build farm 用 sbuild

Debian 的 build farm（buildd）為**每個架構**自動 build 所有套件。它的需求正是 sbuild 提供的：

- **乾淨可重現**：每個 build 從相同的最小環境開始，結果可重現
- **隔離**：一個套件的 build 不影響另一個（chroot 隔離）
- **依賴精確**：只裝宣告的 Build-Depends，暴露任何遺漏
- **自動化**：無人值守，build 幾萬個套件

當你用 sbuild 在本機 build，你模擬的就是 build farm 的環境。所以「sbuild build 成功」幾乎等於「能進 Debian archive」。這是 sbuild 不可替代的價值——它是上傳前的最終真實性檢驗。

## 動手練習

1. 建立一個 bookworm sbuild chroot（照 Step），對練習 B 的 greet 跑 `sbuild`。對比它的 build log 和你 host `dpkg-buildpackage` 的差別——sbuild 在 chroot 裡裝了哪些依賴？

2. 故意製造漏宣告：在 greet 的某處 `#include <openssl/ssl.h>` 但不把 `libssl-dev` 加進 Build-Depends。在 host build（你裝了 libssl-dev 的話會成功），再在 sbuild build（失敗）。體會 sbuild 的價值

3. 進 chroot 探索：`sudo schroot -c bookworm-amd64-sbuild`，跑 `dpkg -l | wc -l` 看乾淨環境裝了多少套件（比你的 host 少非常多）

4. 試試 unshare 模式（`--chroot-mode=unshare`），對比 schroot 模式的設定複雜度

## 本章重點整理

- clean build 的鐵律：只有在「只裝宣告依賴的最小環境」build 成功，才證明 Build-Depends 完整
- sbuild/pbuilder 用 chroot 提供拋棄式的乾淨環境；每次從乾淨 base 開始只裝宣告依賴
- sbuild 是 Debian 官方推薦（build farm 用它）；用它 build 成功幾乎等於能進 archive
- sbuild 最大價值：抓出 host 系統「剛好存在」而掩蓋的漏宣告 Build-Depends
- 現代趨勢用 `--chroot-mode=unshare`（user namespace，無需 root，CI 友善）

## 自我檢核

- [ ] 能解釋為什麼「host build 成功」不能證明套件正確（host 的剛好存在掩蓋遺漏）
- [ ] 知道 sbuild 用 chroot 做什麼（拋棄式最小環境，只裝宣告依賴）
- [ ] 能描述 sbuild 如何抓出漏宣告的 Build-Depends
- [ ] 知道 sbuild build 成功為什麼幾乎等於能進 Debian archive
- [ ] 知道 `--chroot-mode=unshare` 相比 schroot 的優點

## 延伸閱讀

### 官方文件

- **[sbuild wiki](https://wiki.debian.org/sbuild)** — Debian Wiki
  - **讀哪裡**：setup 和 usage，特別是 unshare mode 那節
  - **學什麼**：sbuild 的完整設定（含新的 unshare 後端）；本章是入門，這是完整參考
  - **前提**：讀完本章

- **[sbuild(1) man page](https://manpages.debian.org/bookworm/sbuild/sbuild.1.html)**
  - **讀哪裡**：所有 option 和 `.sbuildrc` 設定
  - **學什麼**：sbuild 的完整選項
  - **前提**：無

### 部落格 / 文章

- **[Clean Debian package builds with sbuild](https://wiki.debian.org/sbuild)** 或 Debian Developer's Reference §5.9
  - **這篇說什麼**：為什麼 clean build 重要、build farm 如何運作
  - **讀哪裡**：Developer's Reference 的 building 章節
  - **為什麼值得讀**：把 sbuild 放進 Debian 開發流程的脈絡，理解它在上傳前的角色

→ [Ch 16 Lintian：靜態品質分析](./16-lintian.md)
