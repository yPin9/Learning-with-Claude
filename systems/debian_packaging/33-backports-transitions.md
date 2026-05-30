# Ch 33 — Backports 與版本遷移

> **目標**：理解 backports（把新版套件帶回舊 stable）、library transition（SONAME 變動引發的連鎖重 build）、binNMU（binary-only 重 build）——這些是 archive 演進中真實會碰到的版本管理操作。

> **環境**：本章是流程與概念章，承接 Ch 25（archive）、Ch 26（library transition）、Ch 9（版本）。

## 為什麼需要這些機制？

Debian stable 為了穩定，凍結套件版本（只收安全更新）。但現實需求很多：

- 你想在 stable 伺服器上用某個套件的**新版本**（stable 的太舊）→ backports
- 一個 library 改了 ABI（SONAME 變），所有依賴它的套件要重新 build → transition
- 套件本身沒變，但它依賴的 library 換了，需要重新 build 連結新 library → binNMU

這些是 archive 持續演進時的版本管理操作。理解它們，你才能應對「stable 套件太舊」「升級 library 後一堆東西要重 build」這類真實問題。

## 先建立直覺：三種版本操作

```
backports：把「新版」帶回「舊 stable」
  trixie 的 nginx 1.26  →  backport 到 bookworm（給 bookworm 使用者用新版）
  版本標記：1.26-1~bpo12+1（~bpo = backport，比正式版小，Ch 9）

transition：library ABI 變動引發的連鎖重 build
  libfoo SONAME 1→2  →  所有 Depends libfoo1 的套件重新 build 連結 libfoo2
  影響可能數百個套件，release team 協調

binNMU：source 不變，只 binary 重 build
  套件 source 沒改，但依賴的 library 換版本
  → 不改 source，只重新 build binary 連結新 library
  版本標記：1.0-1+b1（+bN = binNMU）
```

## Backports：新版回舊 stable

stable 的套件版本凍結。backports 提供「在 stable 上裝新版套件」的官方途徑：

```
backports 的運作：
  trixie（testing/新 stable）有 nginx 1.26
        │  維護者把它「backport」到 bookworm
        │  （調整使其能在 bookworm 的環境 build/run）
        ▼
  bookworm-backports suite 提供 nginx 1.26-1~bpo12+1
        │
  使用者選擇性啟用 backports，裝新版
```

啟用和使用 backports：

```bash
# 啟用 bookworm-backports
echo "deb http://deb.debian.org/debian bookworm-backports main" | \
    sudo tee /etc/apt/sources.list.d/backports.list
sudo apt update

# backports 預設不自動裝（priority 低，Ch 3）
# 要明確指定才裝 backport 版本
sudo apt install -t bookworm-backports nginx
#                 ──────────────────
#                 -t 指定從 backports 裝
```

> backports 的設計哲學：**不破壞 stable 的穩定性**。它 priority 低（Ch 3），不會自動升級——你必須**明確** `-t bookworm-backports` 才裝。這樣 stable 系統預設還是穩定的舊版，只有你主動要新版的特定套件才裝 backport。

## Backport 的版本標記：~bpo

```
nginx (1.26-1~bpo12+1) bookworm-backports; urgency=medium
              ────────
              ~bpo12+1 = backport to bookworm (Debian 12)

為什麼用 ~bpo（Ch 9 的 ~ 規則）：
  1.26-1~bpo12+1  <  1.26-1（正式版）
        │
  當 bookworm 升級到下個 stable（trixie），trixie 有正式的 1.26-1
  使用者的 backport 版本（~bpo）「小於」正式版
  → 系統升級時自動從 backport 版升到正式版，無痛
```

這直接應用 Ch 9 的「`~` 比空字串小」——backport 版本故意比正式版小，確保未來升級到正式 release 時能平順覆蓋。

## 製作 backport

```bash
# 1. 抓 trixie 的 source（要 backport 的新版）
dget https://deb.debian.org/.../nginx_1.26-1.dsc
cd nginx-1.26/

# 2. 用 dch 加 backport changelog 條目
dch --bpo
# 自動生成：nginx (1.26-1~bpo12+1) bookworm-backports; urgency=medium
#           * Rebuild for bookworm-backports.

# 3. 在 bookworm 環境 build（重要！要用 bookworm 的依賴）
sbuild -d bookworm nginx_1.26-1~bpo12+1.dsc
#   可能要調整：bookworm 缺某些新依賴，要降級或 patch

# 4. 上傳到 backports
dput backports nginx_1.26-1~bpo12+1_source.changes
```

> backport 的挑戰：新版套件可能依賴 stable 沒有的新 library/工具。你要在 bookworm 環境 build——可能要 backport 它的依賴（連鎖），或 patch 套件讓它能用舊依賴。簡單的 backport 只是重 build，複雜的要解依賴鏈。

## Transition：SONAME 變動的連鎖反應

Ch 26 提過：library 的 SONAME 變動（ABI 破壞），所有依賴它的套件要重新 build。這在 archive 層級就是 **transition**：

```
libfoo 從 SONAME 1（libfoo1）升到 SONAME 2（libfoo2）：
        │
  問題：所有「Depends: libfoo1」的套件（可能數百個）
        都還連結舊的 libfoo1
        │
  需要：每個下游套件重新 build，連結 libfoo2
        （重 build 後 ${shlibs:Depends} 自動變成 libfoo2）
        │
  挑戰：這數百個套件要「一起」遷移到 testing
        否則 testing 會處於「有些連 libfoo1、有些連 libfoo2」的不一致狀態
        │
  release team 用 transition tracker 協調這個過程
```

transition 的協調：

```bash
# transition 由 release team 管理
# https://release.debian.org/transitions/ 列出進行中的 transition

# 一個 library transition 的步驟（簡化）：
# 1. 新 library（libfoo2）上傳 unstable
# 2. release team 開一個 transition slot
# 3. 所有依賴 libfoo1 的套件逐一 binNMU（重 build 連結 libfoo2）
# 4. 全部重 build 完成且無 RC bug 後，整組一起遷移到 testing
```

> transition 是 archive 最複雜的協調工作之一。一個核心 library（如 openssl）的 transition 影響上千套件，可能持續數週。release team 用工具追蹤「哪些還沒重 build、哪些 build 失敗」，確保整組一致遷移。你作為單一套件維護者，可能收到「你的套件涉及某 transition，需要配合」的通知。

## binNMU：只重 build binary

當套件**source 不需要改**，但要重新 build（如連結新版 library），用 **binNMU**（binary Non-Maintainer Upload）：

```
binNMU 的場景：
  你的套件 myapp 1.0-1 連結 libfoo1
  libfoo transition 到 libfoo2
  myapp 的 source 完全不用改，只要重 build 連結 libfoo2
        │
  binNMU：不碰 source，只用 build farm 重新 build binary
        │
  版本標記：1.0-1+b1
              ────
              +bN = binNMU 編號（source 版本不變，只是重 build）
```

```
版本號的演進對照：
  1.0-1        原始
  1.0-1+b1     第一次 binNMU（source 沒變，重 build）
  1.0-2        維護者改了 source（debian revision +1）
  1.0-1+b2     又一次 binNMU
```

binNMU 由 build farm 自動做（release team 觸發），維護者通常不用手動。`+bN` 的版本標記讓人一眼看出「這是重 build，不是 source 修改」。

## 故意對照：backport vs 直接裝 testing 套件

```
想在 bookworm 用新版 nginx，兩個錯誤做法 vs 正確做法：

  錯誤一：直接加 trixie repo 裝 nginx
    → apt 可能拉一堆 trixie 的依賴（libc6 等）進來
    → 破壞 bookworm 的穩定性（混入 testing 的核心套件）

  錯誤二：手動 dpkg -i trixie 的 nginx .deb
    → 依賴不滿足（trixie nginx 要 trixie 的 library）

  正確：用 bookworm-backports 的 nginx
    → 它是「為 bookworm 環境重新 build 的」，用 bookworm 的依賴
    → 只更新 nginx，不污染系統其他部分
```

backports 的價值就在這：它是「為舊 stable 環境特製的新版」，不像直接混 testing 會破壞系統。

## 踩雷集錦

1. **直接混 testing repo 到 stable**：拉一堆 testing 核心套件進來破壞穩定性。要新版用 backports（特製的），不是混 testing

2. **backport 版本沒用 `~bpo`**：用 `1.26-1bpo12`（沒 `~`）會「大於」正式版 `1.26-1`，未來升級卡在 backport 版。用 `~bpo`（Ch 9）

3. **backport 沒在目標環境 build**：在 unstable build 的「backport」連結了 unstable 的 library，裝到 stable 會缺依賴。必須在 stable（bookworm）環境 build

4. **以為 transition 是單一套件的事**：transition 是「一整組相互依賴的套件一起遷移」。你的 library 改 SONAME 會牽動所有下游，要和 release team 協調（在 archive）或記得重 build 所有下游（私有 repo）

5. **混淆 binNMU 和 source upload**：`+b1` 是重 build（source 沒變）；`-2` 是 source 修改。binNMU 通常自動，你改 source 才手動上傳新 revision

## 進階：你的私有 repo 的「transition」

雖然 backports/transition/binNMU 是 Debian archive 的概念，但你的私有 repo（aptly，Ch 23/32）也面對同樣的問題：

```
私有 repo 的 library 更新（你的 libgreet 1→2）：
        │
  你的 aptly repo 裡，所有依賴 libgreet1 的套件都還連舊版
        │
  你需要：重新 build 所有下游套件連結 libgreet2
        │  （CI 可以自動化：library 更新觸發下游 rebuild）
        │
  用 aptly snapshot：建立「全部更新後」的一致快照
        │  舊快照（連 libgreet1 的）保留 → 能回滾
        │  新快照（連 libgreet2 的）發布
```

你的私有 repo 是 Debian archive 的縮小版（Ch 25），同樣要處理 library transition——只是規模小、你自己協調。aptly 的 snapshot（Ch 23）讓你能「先建好全部更新的快照再切換」，避免中途不一致。CI（Ch 32）能自動化「library 更新 → 重 build 下游」。

> 理解 Debian 的 transition 機制，你就知道私有 repo 的 library 更新該怎麼做：不是只更新 library，而是「重 build 所有下游 + 用 snapshot 一致切換」。這是 Final Project 要考慮的。

## 動手練習

1. 啟用 bookworm-backports，用 `-t bookworm-backports` 裝一個 backport 套件，對比它和 stable 版的版本號（`apt policy`），看 `~bpo` 標記

2. 用 `dch --bpo` 把練習 B 的 greet 做成 backport（即使版本沒變，體會版本標記），看生成的 `~bpo12+1`

3. 看進行中的 transition：瀏覽 `https://release.debian.org/transitions/`，看現在有哪些 library 在 transition、影響多少套件

4. 模擬私有 repo 的 transition：在你的 aptly repo，把 libgreet 從 1 升到 2，重 build 依賴它的 greet，用 snapshot 建立「全部更新」的一致狀態，對比舊 snapshot（可回滾）

## 本章重點整理

- backports：把新版套件帶回舊 stable，在舊 stable 環境 build，版本標 `~bpo`（比正式版小，未來無痛升級）
- backports priority 低，不自動裝，要明確 `-t bookworm-backports`（不破壞 stable 穩定性）
- transition：library SONAME 變動引發所有下游重 build，整組一起遷移 testing，release team 協調
- binNMU：source 不變只重 build binary（如連結新 library），版本標 `+bN`，build farm 自動做
- 你的私有 repo 也面對 library transition——用 snapshot 建立一致狀態再切換，CI 自動化下游重 build

## 自我檢核

- [ ] 能解釋 backports 解決什麼問題，以及為什麼它不破壞 stable（priority + 明確指定）
- [ ] 知道 backport 版本為什麼用 `~bpo`（和 Ch 9 的 `~` 規則）
- [ ] 能描述 library transition 是什麼、為什麼要整組一起遷移
- [ ] 知道 binNMU（`+bN`）和 source upload（`-N`）的差別
- [ ] 能說出你的私有 repo 遇到 library 更新時該怎麼處理（重 build 下游 + snapshot 切換）

## 延伸閱讀

### 官方文件

- **[Debian Backports](https://backports.debian.org/)**
  - **讀哪裡**：about 和 contribute（如何製作 backport）
  - **學什麼**：backports 的官方運作、製作流程
  - **前提**：讀完本章

- **[Debian Release Team: Transitions](https://wiki.debian.org/Teams/ReleaseTeam/Transitions)**
  - **讀哪裡**：transition 流程和 tracker 使用
  - **學什麼**：library transition 的協調機制
  - **前提**：Ch 26 + 本章

### 部落格 / 文章

- **[The Debian Backports policy and workflow](https://backports.debian.org/Contribute/)**
  - **這篇說什麼**：製作和上傳 backport 的完整流程
  - **讀哪裡**：整頁
  - **為什麼值得讀**：實際製作 backport 的 step-by-step

→ [Ch 34 Debian Policy 精讀](./34-debian-policy.md)
