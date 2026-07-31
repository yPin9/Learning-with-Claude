# Ch 8 — devtool workflow：該你常用的指令

> **目標**：熟悉 devtool——Yocto 的日常開發工具，簡化 recipe 的 modify/add/upgrade，讓你改 recipe、patch 源碼、測試的迭代快很多。理解 devtool modify（改源碼）、devtool build（快速重建）、devtool finish（產生 patch + .bbappend）的工作流，以及它怎麼自動化 Ch 5 的手動 patch 流程。這是 compiler 工程師 day-to-day 比手動改 recipe 更方便的方式。

> **環境**：Yocto（poky + meta-riscv，Ch 3）。devtool（Yocto 內建）。

## 為什麼需要 devtool？

Ch 5 你手動 patch GCC——寫 .bbappend、放 patch、rebuild、驗證。這流程**可行但繁瑣**，尤其在**開發迭代**時（你還在改 patch，要反覆「改→build→測」）——每次都手動改 patch 檔、cleansstate、rebuild，很慢很煩。

**devtool** 解決這個——它讓你像開發一般專案那樣改 Yocto 的套件：`devtool modify gcc`（把 gcc 的源碼拉出來，你直接改）、`devtool build gcc`（快速重建）、改完 `devtool finish`（自動產生 patch + .bbappend）。這把 Ch 5 的手動流程**自動化**，讓「改源碼→測試」的迭代快很多。對 compiler 工程師，devtool 是 day-to-day 開發 GCC patch 的主力工具——比手動改 recipe 方便得多。這章講 devtool 的核心工作流。

## 先建立直覺:像開發一般專案那樣改套件

```
devtool = 把 Yocto 套件變成「可直接改的開發專案」

  手動方式（Ch 5）：
    寫 .bbappend → 放 patch → cleansstate → rebuild → 測
    每次改 patch 都要重來（繁瑣，迭代慢）
        │
  devtool 方式：
    1. devtool modify gcc
       → 把 gcc 源碼拉到 workspace（一個 git repo）
       → 你直接改源碼（像開發一般專案）
        │
    2. （改源碼）+ devtool build gcc
       → 快速重建（只編你改的，增量）
        │
    3. （測試）+ 反覆改→build→測（快速迭代）
        │
    4. devtool finish gcc <layer>
       → 自動：把你的改動變成 patch + 產生 .bbappend
       → 放到你指定的 layer（Ch 5 的 .bbappend 自動產生！）
        │
  → devtool 把「改源碼→測」變成快速迭代
    最後 finish 自動產生 patch + .bbappend（Ch 5 的手動流程自動化）
```

關鍵心智：**devtool** 把 Yocto 套件變成「可直接改的開發專案」——`devtool modify gcc`（把源碼拉到 workspace，你直接改）→ 改源碼 + `devtool build`（快速增量重建）→ 反覆迭代 → `devtool finish`（**自動產生 patch + .bbappend**）。它把 Ch 5 的手動流程自動化，讓「改源碼→測」快速迭代。

## devtool 的核心工作流

```bash
cd ~/yocto/poky/build
# === devtool modify：把套件源碼拉出來改 ===
devtool modify gcc
# devtool 做的：
#   1. 把 gcc 的源碼拉到 workspace/sources/gcc/（一個 git repo）
#   2. 套用上游的 patches（你在乾淨的、已 patch 的源碼上改）
#   3. 設定讓 bitbake 用這個 workspace 的源碼
# → 現在 workspace/sources/gcc/ 是 gcc 源碼，你直接改

# === 改源碼 ===
cd ~/yocto/poky/build/workspace/sources/gcc
# 直接編輯源碼（如 gcc/config/riscv/riscv.cc）
# vim gcc/config/riscv/riscv.cc   # 做你的改動

# === devtool build：快速重建 ===
cd ~/yocto/poky/build
devtool build gcc
# 增量編譯（只編你改的，比 cleansstate + rebuild 快很多）

# === 測試 + 反覆迭代 ===
# 改源碼 → devtool build → 測 → 改 → build → 測...（快速迭代）

# === devtool finish：產生 patch + .bbappend ===
devtool finish gcc ../meta-mycompany
# devtool 做的：
#   1. 把你在 workspace 的 git 改動變成 patch（git 的 commit → .patch）
#   2. 產生/更新 .bbappend（SRC_URI:append 加你的 patch）
#   3. 放到 meta-mycompany（你指定的 layer）
#   4. 清掉 workspace
# → 自動完成 Ch 5 的「寫 .bbappend + 放 patch」！
```

> **devtool modify（拉源碼出來改）→ devtool build（快速增量重建）→ devtool finish（自動產生 patch + .bbappend）——把 Ch 5 的手動流程自動化，迭代快很多**。devtool 的核心工作流三步：(1) **`devtool modify gcc`**——把 gcc 源碼拉到 **workspace**（一個 git repo，已套用上游 patches，你在乾淨的源碼上改），設定讓 bitbake 用這個 workspace 的源碼；(2) **改源碼 + `devtool build gcc`**——你直接編輯 workspace 的源碼（像開發一般專案），`devtool build` **增量編譯**（只編你改的，**比 cleansstate + rebuild 快很多**——這是 devtool 的關鍵優勢，迭代快）；(3) 反覆「改→build→測」快速迭代，最後 **`devtool finish gcc ../meta-mycompany`**——devtool **自動**把你的 git 改動變成 patch（commit → .patch）、產生/更新 .bbappend（SRC_URI:append 加 patch）、放到你指定的 layer、清掉 workspace。**這自動完成 Ch 5 的手動流程**（寫 .bbappend + 放 patch）！對 compiler 工程師，devtool 是開發 GCC patch 的**主力**——比手動方式方便太多：(1) 你在 git repo 直接改源碼（用熟悉的 git 工作流）；(2) 增量 build 快（不用每次 cleansstate）；(3) finish 自動產生 patch + .bbappend（不用手寫）。所以**開發階段用 devtool**（快速迭代）、最後 finish 產生乾淨的 patch + .bbappend（交付）。這把 Ch 5 的「理解手動流程」變成「高效的日常工具」——你懂底層（手動流程），用 devtool（自動化）高效工作。

## devtool 的其他常用命令

```bash
cd ~/yocto/poky/build
# === devtool add：加一個全新的 recipe（從源碼）===
# devtool add my-app https://github.com/me/my-app.git
# → devtool 自動分析源碼、產生一個 recipe（猜 build 系統、依賴）
# 用於：把一個新軟體加進 Yocto（自動產生初始 recipe）

# === devtool upgrade：升級一個 recipe 的版本 ===
# devtool upgrade gcc -V 13.3.0
# → devtool 自動：抓新版源碼、套用現有 patches（看哪些還適用）
# 用於：升級套件版本（如 gcc 13.2 → 13.3）

# === devtool 的狀態管理 ===
devtool status                  # 看 workspace 裡有哪些在開發的 recipe
# gcc: ...workspace/sources/gcc

# devtool reset gcc             # 放棄 workspace 的改動（不 finish）

# === devtool 的 workspace ===
ls workspace/
# sources/        ← 拉出來的源碼（你改的）
# appends/        ← devtool 自動產生的暫時 .bbappend
# recipes/        ← devtool add 產生的 recipe
```

```
devtool 命令速查（compiler 工程師常用）：

  devtool modify <recipe>   拉源碼出來改（patch 既有套件）★ 最常用
        │
  devtool build <recipe>    增量重建（快速迭代）★ 最常用
        │
  devtool finish <recipe> <layer>  產生 patch + .bbappend ★ 最常用
        │
  devtool add <name> <url>  從源碼產生新 recipe
        │
  devtool upgrade <recipe>  升級套件版本（自動 rebase patches）
        │
  devtool status / reset    看狀態 / 放棄改動
        │
  → modify → build → finish 是 patch 開發的主循環
    upgrade 用於版本升級（自動處理 patch rebase）
```

> **devtool 命令：modify→build→finish 是 patch 開發主循環，add（新 recipe）、upgrade（升級版本自動 rebase patch）也常用**。devtool 的其他常用命令：**`devtool add <name> <url>`**——從源碼**產生一個全新的 recipe**（devtool 自動分析源碼、猜 build 系統和依賴，產生初始 recipe）——用於把新軟體加進 Yocto；**`devtool upgrade <recipe> -V <version>`**——**升級套件版本**（如 gcc 13.2→13.3，devtool 自動抓新版源碼、**套用現有 patches 看哪些還適用**——這對 compiler 工程師升級 gcc 版本時自動處理 patch rebase 很有用，呼應 Ch 5 的 patch 套不上問題）；**`devtool status`**（看 workspace 在開發的 recipe）、**`devtool reset <recipe>`**（放棄改動，不 finish）。**核心循環**：**modify → build → finish**（patch 既有套件的主循環，compiler 工程師最常用），**upgrade** 用於版本升級（自動 rebase patch）。devtool 的 **workspace** 目錄含：sources（拉出來的源碼）、appends（暫時 .bbappend）、recipes（add 產生的）。對 compiler 工程師，這些命令涵蓋日常——**modify/build/finish** 開發和產生 GCC patch、**upgrade** 升級 gcc 版本（自動處理 patch）。**upgrade 的自動 patch rebase** 特別有用——當 gcc 升版本，你的 patch 可能套不上（Ch 5 的版本不 match），`devtool upgrade` 自動嘗試套用現有 patches 並告訴你哪些要手動處理（比手動 rebase 每個 patch 方便）。理解 devtool 的命令，你有了 Yocto 開發的高效工具組——這讓 compiler 工程師的 day-to-day（開發 patch、升級版本）快速且自動化。

## devtool vs 手動:何時用哪個

```
devtool（自動）vs 手動改 recipe（Ch 5）：

  devtool（開發迭代）：
    優點：快（增量 build）、方便（git 工作流）、自動產生 patch/.bbappend
    用於：開發 patch（反覆改→build→測）、升級版本
        │
  手動（理解 + 細控制）：
    優點：完全掌控、理解每一步、適合複雜的客製
    用於：理解流程、特殊的 recipe 操作、devtool 不好處理的
        │
  → 開發階段用 devtool（快速迭代）
    最後 finish 產生乾淨的 patch + .bbappend
    但要懂手動流程（Ch 5）才知道 devtool 在做什麼
        │
  最佳實踐：
    懂手動（知道 .bbappend/SRC_URI/patch 怎麼運作，Ch 5）
    + 用 devtool（高效開發）
    = 既理解底層又高效
```

> **開發階段用 devtool（快速迭代），但要懂手動流程（Ch 5）才知道 devtool 在做什麼——既理解底層又高效**。devtool（自動）和手動改 recipe（Ch 5）的關係：**devtool**（開發迭代）——快（增量 build）、方便（git 工作流）、自動產生 patch/.bbappend，用於**開發 patch**（反覆改→build→測）和升級版本；**手動**（理解 + 細控制）——完全掌控、理解每一步，用於理解流程、特殊的客製、devtool 不好處理的情況。**最佳實踐**——**懂手動流程（Ch 5，知道 .bbappend/SRC_URI/patch 怎麼運作）+ 用 devtool（高效開發）**。為什麼要懂手動——devtool 是**自動化手動流程**，懂手動你才知道 devtool 在做什麼（modify = 拉源碼+套 patch、finish = 產生 patch+.bbappend），debug devtool 的問題時也能理解（如 finish 產生的 .bbappend 對不對、patch 對不對）。**開發階段用 devtool 快速迭代**（改源碼→devtool build→測，比手動快太多），**最後 finish 產生乾淨的 patch + .bbappend**（交付，這就是 Ch 5 手動產生的東西，但自動化了）。對 compiler 工程師，這是 day-to-day 的工作方式——用 devtool 高效開發 GCC patch，但理解底層（手動流程）讓你掌控和 debug。這呼應整門課的教學——理解底層（手動）+ 用方便工具（devtool）。devtool 不是取代理解，是在理解之上提高效率。記住：**懂 Ch 5 的手動流程 + 日常用 devtool = 既懂又快**。這章讓你有了高效開發 Yocto patch 的工具，補完了 patch GCC 的工作流（Ch 5 理解 + Ch 8 高效）。

## 故意弄壞:devtool finish 產生的 patch 檢查

```bash
cd ~/yocto/poky/build
# devtool finish 後，檢查它產生的 patch + .bbappend 對不對

# finish 後，看 devtool 產生了什麼
ls ../meta-mycompany/recipes-devtools/gcc/
# gcc_%.bbappend        ← devtool 自動產生的 .bbappend
# gcc/
#   0001-my-change.patch  ← devtool 從你的 git commit 產生的 patch

# 檢查 .bbappend（devtool 產生的）
cat ../meta-mycompany/recipes-devtools/gcc/gcc_%.bbappend
# FILESEXTRAPATHS:prepend := "${THISDIR}/${PN}:"
# SRC_URI += "file://0001-my-change.patch"
# → 和 Ch 5 手動寫的一樣（devtool 自動產生）

# 檢查 patch 內容（你的改動對嗎）
cat ../meta-mycompany/recipes-devtools/gcc/gcc/0001-my-change.patch
# 看 patch 是不是你要的改動

# 驗證（和 Ch 5 一樣的五層驗證）
bitbake -e gcc 2>/dev/null | grep '0001-my-change.patch'   # patch 在 SRC_URI
bitbake gcc-cross-riscv64                                   # rebuild 確認

# → devtool finish 自動產生 patch + .bbappend，但要檢查：
#   1. patch 內容對（是你要的改動，git commit message 也會變 patch 描述）
#   2. .bbappend 對（FILESEXTRAPATHS + SRC_URI）
#   3. 一樣要驗證（bitbake -e、rebuild）
# devtool 自動但不是黑盒——檢查它產生的東西，確認對
```

> **devtool finish 自動產生 patch + .bbappend，但要檢查內容（patch 對嗎、.bbappend 對嗎）並驗證——自動但不是黑盒**。devtool finish 自動產生 patch 和 .bbappend，但你**要檢查它產生的東西**——它不是黑盒，產生的東西要確認對：(1) **檢查 patch 內容**（`0001-my-change.patch`——是你要的改動嗎；注意 devtool 從你的 git commit 產生 patch，**git commit message 會變成 patch 的描述**，所以 commit message 要寫好——這是好的 patch 該有的描述）；(2) **檢查 .bbappend**（devtool 產生的 `gcc_%.bbappend` 含 FILESEXTRAPATHS + SRC_URI——應該和 Ch 5 手動寫的一樣）；(3) **一樣要驗證**（`bitbake -e gcc | grep patch` 看 patch 在 SRC_URI、rebuild 確認——Ch 5 的五層驗證）。**devtool 自動但不是黑盒**——它幫你產生 patch + .bbappend（省手寫），但你要檢查產生的對不對（patch 內容、.bbappend、驗證生效）。這呼應「懂手動 + 用 devtool」——你懂手動流程（Ch 5），所以能檢查 devtool 產生的東西對不對（不是盲目相信）。對 compiler 工程師，這個習慣很重要——devtool 高效但你要對交付的 patch 負責（檢查 patch 內容對、描述清楚、驗證生效）。一個好的 patch 要有清楚的 commit message（描述改了什麼、為什麼）——devtool 用你的 git commit message，所以**寫好 commit message** 是好 patch 的一部分。這章完成了 devtool 工作流——高效開發 + 檢查產出。你現在能高效地開發和交付 GCC patch（devtool 快速迭代 + finish 產生 + 檢查驗證），這是 compiler 工程師 day-to-day 的主力工作流。

## 動手練習

1. devtool modify：對一個套件（如 busybox）devtool modify，看源碼拉到 workspace

2. 改 + build：改源碼 + devtool build，體會增量編譯的快速

3. devtool finish：finish 到你的 layer，看自動產生的 patch + .bbappend

4. 檢查產出：檢查 devtool 產生的 patch 內容、.bbappend，驗證生效（bitbake -e）

5. devtool upgrade（選做）：對一個套件試 upgrade，看它怎麼處理版本升級和 patch rebase

## 本章重點整理

- devtool 把 Yocto 套件變成可直接改的開發專案，自動化 Ch 5 的手動 patch 流程，迭代快很多
- 核心循環：devtool modify（拉源碼）→ 改 + devtool build（增量快速）→ devtool finish（產生 patch + .bbappend）
- 其他命令：devtool add（新 recipe）、devtool upgrade（升級版本，自動 rebase patch）、status/reset
- devtool（開發迭代，快）vs 手動（理解 + 細控制）——懂手動 + 用 devtool = 既懂底層又高效
- devtool finish 自動產生 patch + .bbappend，但要檢查內容（patch/.bbappend 對嗎）並驗證——不是黑盒

## 自我檢核

- [ ] 會用 devtool modify/build/finish 的核心循環
- [ ] 理解 devtool 怎麼自動化 Ch 5 的手動流程
- [ ] 知道 devtool add/upgrade 的用途
- [ ] 理解 devtool（高效）和手動（理解）的關係，為什麼都要懂
- [ ] 會檢查 devtool 產生的 patch + .bbappend 並驗證

## 延伸閱讀

### 官方

- **[devtool Reference](https://docs.yoctoproject.org/ref-manual/devtool-reference.html)** — Yocto Project
  - **讀哪裡**：modify/build/finish/add/upgrade 的完整說明
  - **為什麼值得讀**：devtool 的權威

- **[Using devtool](https://docs.yoctoproject.org/sdk-manual/extensible.html#using-devtool-in-your-sdk-workflow)** — Yocto
  - **讀哪裡**：devtool 的工作流範例
  - **為什麼值得讀**：devtool 實戰

### 書籍

- **《Embedded Linux Systems with the Yocto Project》— devtool 章** — Streif
  - **為什麼值得讀**：devtool 在開發流程的角色

下一章看常見雷——sstate-cache、DEPENDS、PREFERRED_VERSION 等讓 Yocto 新手栽跟頭的陷阱。理解這些，你 debug Yocto 問題時知道往哪看。

→ [Ch 9 常見雷：sstate-cache / DEPENDS / PREFERRED_VERSION](./09-common-traps.md)
