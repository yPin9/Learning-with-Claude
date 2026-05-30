# Ch 34 — Debian Policy 精讀

> **目標**：理解 Debian Policy 在整個生態的角色（為什麼需要一部「憲法」）、精讀最重要的 10 條規則及其背後的設計理由、Policy 的 must/should/may 助動詞體系、以及 Policy 如何演進。這章把全課學的所有零散規則收束到它們的源頭。

> **環境**：Debian Policy 4.6.x。本章是概念整合章，把前面各章的「規則」連到 Policy 的「為什麼」。

## 為什麼 Debian 需要一部 Policy？

你學了一路的規則：檔案放哪、權限怎麼設、依賴怎麼宣告、版本怎麼比較、library 怎麼命名……這些規則從哪來、為什麼是這樣？答案：**Debian Policy Manual**。

設想沒有 Policy：五萬個套件、上千個維護者，每個人按自己的習慣打包。檔案位置不一致、依賴宣告風格各異、升級行為無法預測——整個系統會變成無法協作的混亂。

Policy 是 Debian 的「憲法」：它規定所有套件**必須**遵守的契約，讓五萬個獨立維護的套件能和諧共存、能被自動化工具統一處理、能給使用者一致的體驗。

> Policy 不是「建議」，是「契約」。lintian（Ch 16）的每個 error/warning 背後都是一條 Policy。理解 Policy 的「為什麼」，那些規則就從「死記的儀式」變成「有道理的設計」。

## 先建立直覺：Policy 是套件間的社會契約

```
Policy 確保的「可預測性」：

  任何套件的檔案 → 都在 Policy 規定的位置（FHS）
        → 使用者/工具知道去哪找

  任何套件的依賴 → 都用 Policy 規定的方式宣告
        → apt 能可靠地解析

  任何套件的升級 → 都遵守 Policy 的 maintainer script 約定
        → dpkg 能可靠地處理

  → 五萬個套件，行為可預測、可協作、可自動化
```

Policy 的核心價值是**可預測性**——因為每個套件都遵守同一套契約，工具和使用者就能對任何套件做出正確假設。

## must / should / may：助動詞體系

Policy 用精確的助動詞表達規則的強制程度（RFC 2119 風格）：

```
must / required    → 強制。違反 = 嚴重 bug，套件不合格
                     （lintian 通常報 E，archive 拒絕）

should / recommended → 強烈建議。違反要有正當理由
                     （lintian 通常報 W）

may / optional     → 可選。隨你
```

| 助動詞 | lintian 對應 | 違反後果 |
|---|---|---|
| must | E (error) | release-critical bug，套件不能進 stable |
| should | W (warning) | 應該修，sponsor/QA 會質疑 |
| may | I / 無 | 隨意 |

> 讀 Policy 時注意助動詞。「must」是不可違反的硬規則；「should」是「除非有好理由否則照做」。這個區分讓你知道哪些是底線、哪些有彈性。lintian 的 E/W 等級就是對應 Policy 的 must/should。

## 10 條最重要的 Policy 規則（含設計理由）

### 1. FHS：檔案系統階層標準（§9.1）

```
規則：套件的檔案必須遵守 FHS（Filesystem Hierarchy Standard）
  /usr/bin       使用者執行檔
  /usr/lib       library
  /etc           設定檔
  /usr/share     架構無關的資料
  /var           可變資料（log、cache、spool）
  /usr/local     ← 套件「絕對不能」碰（保留給本地管理員）

設計理由：可預測的檔案位置。使用者和工具知道去哪找東西。
/usr/local 保留給管理員手動裝的東西，套件碰它會衝突。
```

這是為什麼練習 A 的 `greet` 裝到 `/usr/bin/` 而非 `/usr/local/bin/`——後者是 Policy 禁區。

### 2. 每個套件必須有 copyright（§12.5）

```
規則（must）：每個 binary package 必須在
  /usr/share/doc/<pkg>/copyright 提供授權資訊

設計理由：法律義務（Ch 10）。Debian 散布軟體必須合規，
每個套件的授權必須可追溯。這是 NEW queue（Ch 25）第一個檢查的。
```

### 3. changelog 必須存在且格式正確（§4.4, §12.7）

```
規則：debian/changelog 驅動 build（Ch 9），且每個套件要在
  /usr/share/doc/<pkg>/changelog.Debian.gz 提供變更紀錄（壓縮）

設計理由：版本號驅動升級邏輯；變更紀錄讓使用者知道改了什麼。
壓縮是為了省空間（§ 文件壓縮規則）。
```

### 4. 依賴必須正確且完整宣告（§7）

```
規則（must）：套件執行需要的東西必須在 Depends 宣告；
  build 需要的在 Build-Depends（Ch 7）

設計理由：apt 靠這個解依賴（Ch 3）。漏宣告 = 在乾淨系統裝不起來。
sbuild（Ch 15）就是強制驗證這個。
```

### 5. 不能未經詢問就覆蓋使用者的設定（§10.7）

```
規則：conffile（使用者可能改過的設定）升級時不能無聲覆蓋
  → conffile 機制（Ch 2）

設計理由：尊重使用者。管理員改過的設定是他的意圖，
套件升級不該擅自抹掉。dpkg 的三方比較（Ch 2）實現這個。
```

### 6. maintainer scripts 必須可重入且用 set -e（§6）

```
規則：preinst/postinst/prerm/postrm 必須能安全重跑（Ch 5），
  且失敗要中止（set -e）

設計理由：script 可能被重跑（half-configured 重試，Ch 2）。
不可重入會在重跑時失敗。set -e 確保失敗可見而非靜默損壞。
```

### 7. shared library 必須正確處理 SONAME 和依賴（§8）

```
規則：library 套件名含 SONAME 版本（libfoo1）、提供 shlibs/symbols、
  ABI 破壞要換 SONAME（Ch 19, 26）

設計理由：ABI 相容性。讓依賴 library 的程式能正確連結正確版本，
SONAME 變動時新舊能共存（不破壞已編譯的程式）。
```

### 8. 套件必須能在乾淨環境 build（§4.9, Build-Depends）

```
規則：Build-Depends 必須完整，套件在只裝宣告依賴的環境能 build

設計理由：可重現、多架構。build farm 在乾淨環境為每個架構 build
（Ch 15, 25）。漏依賴 = build farm build 不出來。
```

### 9. 套件必須用標準的編譯 hardening（§ build flags）

```
規則：套件應該用 dpkg-buildflags 的 hardening 選項（Ch 8）

設計理由：安全。stack protector、PIE、RELRO 等讓漏洞更難利用。
全 archive 統一啟用，整個系統更安全。dh_auto_build 自動套用。
```

### 10. 版本號必須正確反映演進方向（§5.6.12）

```
規則：版本號必須讓「升級」是正確方向（Ch 9 的版本比較）

設計理由：apt 靠版本比較決定升級。版本倒退 = 套件升不上去。
~（pre-release/backport）、epoch（重置）的規則都服務這個。
```

## Policy 的演進

Policy 不是一成不變的——它隨著最佳實踐演進：

```
Policy 演進的機制：
  - 透過 debian-policy 郵件清單和 BTS（bug tracker）討論
  - 新規則先在社群達成共識
  - Standards-Version 標記套件遵守的 Policy 版本（Ch 7）
        │
  你的 control 寫 Standards-Version: 4.6.2
  表示「我檢查過，遵守 Policy 4.6.2」
        │
  Policy 更新後，維護者逐步更新套件的 Standards-Version
  （並做對應的調整）
```

`Standards-Version`（Ch 7 的 control 欄位）就是宣告「我遵守哪版 Policy」。更新它表示你檢查過套件符合新版 Policy。lintian 會提醒 `out-of-date-standards-version`。

> Policy 的演進是漸進、有共識的。一個新規則從「提議」到「成為 must」要經過討論、過渡期（先 should 再 must）。這讓五萬個套件有時間適應。Standards-Version 讓每個套件標記自己的「合規時間點」。

## 如何用 Policy（不是從頭讀到尾）

Policy 是參考手冊，不是教科書。用法：

```
日常用法：
  1. lintian 報一個 tag → 查它對應的 Policy 章節 → 理解「為什麼」
  2. 不確定某個做法對不對 → 查 Policy 相關章節
  3. 設計 library/service 打包 → 讀對應的 Policy 章（§8 shared lib、§9 system）

常查的章節：
  §3-4   binary/source 套件基礎
  §5     control 欄位（Ch 7）
  §6     maintainer scripts（Ch 5）
  §7     依賴關係（Ch 7）
  §8     shared libraries（Ch 19, 26）
  §9     系統相關（FHS、init/systemd、users）
  §12    文件（copyright、changelog、man）
```

## 故意對照：違反 Policy 的具體後果

```
違反 FHS（裝到 /usr/local）：
  → lintian: E: package-installs-into-usr-local
  → 和管理員手動裝的東西衝突

缺 copyright（§12.5 must）：
  → lintian: E: no-copyright-file
  → NEW queue 直接退件（法律問題）

依賴漏宣告（§7 must）：
  → sbuild build 失敗 / 使用者裝不起來
  → release-critical bug

版本倒退（§5.6.12）：
  → 套件升不上去，使用者卡在舊版

每一條 Policy 違反都對應一個具體、真實的問題——
Policy 不是官僚規則，是從無數事故中提煉的經驗。
```

## 踩雷集錦

1. **把 Policy 當教科書從頭讀**：Policy 是參考手冊。用 lintian 引導（報什麼 tag 查什麼章節），不要硬啃

2. **忽略 should 規則**：should 不是 must 但也不是 may。違反 should 要有正當理由，不是「我懶得管」

3. **Standards-Version 永遠不更新**：套件停在舊 Policy 版本。定期更新 Standards-Version 並做對應調整（lintian 會提醒）

4. **以為 Policy 是死的**：Policy 持續演進。今天的最佳實踐可能明天有新規則。關注 debian-policy 的變化

5. **不理解規則的「為什麼」就死記**：理解設計理由（如 conffile 為什麼存在、SONAME 為什麼要版本），規則就好記且能舉一反三。死記規則容易在邊界情況犯錯

## 進階：Policy、FHS、和更大的標準生態

Debian Policy 不是孤立的，它建立在更大的標準生態上：

```
標準的層次：
  POSIX            → Unix 系統的基礎標準
  FHS (Filesystem Hierarchy Standard) → 檔案位置（Policy §9 引用它）
  LSB (Linux Standard Base)           → 跨發行版的二進位相容（部分過時）
  Debian Policy                       → Debian 特定的打包契約
        │
  Policy 引用 FHS、POSIX，加上 Debian 特定的規則
```

Policy 把通用標準（FHS、POSIX）和 Debian 特定需求（套件命名、依賴、maintainer scripts）結合成一部完整的打包契約。理解這個層次，你會發現很多 Policy 規則其實源自更廣的 Unix 傳統（如 FHS 的檔案位置），Debian 只是明確化並強制執行。

**和其他發行版的對照**：Fedora 有自己的 Packaging Guidelines，Arch 有 PKGBUILD 慣例。每個發行版的「Policy」反映它的價值觀——Debian Policy 特別強調穩定性、可重現性、自由軟體合規，這些價值滲透在每一條規則裡。讀 Policy 不只學規則，也理解 Debian 的價值觀。

## 動手練習

1. 反向學習：跑 `lintian -i` 對你的套件，挑三個 tag，查它們對應的 Policy 章節（lintian tag 頁面會連到 Policy），讀那段 Policy 理解「為什麼」

2. 讀 Policy 的一個完整章節：選 §8（shared libraries），對照你在 Ch 19/26 學的，看 Policy 怎麼規範 SONAME/shlibs/symbols

3. 看一個套件的 Standards-Version：`apt source` 一個套件，看它的 `Standards-Version`，到 Policy 的 upgrading-checklist 看那版到最新版有哪些變化

4. 對照設計理由：挑本章的 10 條規則之一，向別人（或自己）解釋它「為什麼」存在、不遵守會發生什麼——這檢驗你是否真的理解而非死記

## 本章重點整理

- Debian Policy 是套件間的「社會契約」，確保五萬個套件可預測、可協作、可自動化
- 助動詞體系：must（強制，違反=不合格）/ should（強烈建議，要理由）/ may（可選），對應 lintian 的 E/W/I
- 10 條核心規則（FHS、copyright、changelog、依賴、conffile、scripts、SONAME、乾淨 build、hardening、版本）各有具體的設計理由
- `Standards-Version` 標記套件遵守的 Policy 版本；Policy 漸進演進（先 should 再 must）
- Policy 是參考手冊（用 lintian 引導查閱），建立在 FHS/POSIX 之上，反映 Debian 的價值觀

## 自我檢核

- [ ] 能解釋為什麼 Debian 需要 Policy（可預測性、協作、自動化）
- [ ] 知道 must/should/may 的區別，以及對應 lintian 的什麼等級
- [ ] 能說出至少 5 條核心 Policy 規則及其設計理由（不是死記規則，是理解為什麼）
- [ ] 知道 `Standards-Version` 的作用和 Policy 如何演進
- [ ] 能用 lintian 引導查 Policy（報 tag → 查章節 → 理解為什麼）

## 延伸閱讀

### 官方文件

- **[Debian Policy Manual](https://www.debian.org/doc/debian-policy/)**
  - **讀哪裡**：不要從頭讀。先讀 §1（introduction）理解 Policy 的角色，然後當參考手冊用（lintian 引導）
  - **學什麼**：所有打包規則的權威來源；本課所有「規則」的源頭
  - **前提**：讀完本課大部分章節，回頭看 Policy 會發現「啊原來這條規則在這」

- **[Policy upgrading-checklist](https://www.debian.org/doc/debian-policy/upgrading-checklist.html)**
  - **讀哪裡**：每個 Standards-Version 之間的變化
  - **學什麼**：Policy 如何演進、更新 Standards-Version 時要做什麼
  - **前提**：本章

### 部落格 / 文章

- **[How Debian Policy is made (debian-policy process)](https://www.debian.org/doc/debian-policy/process.html)**
  - **這篇說什麼**：Policy 規則如何被提議、討論、達成共識、納入
  - **讀哪裡**：process 那節
  - **為什麼值得讀**：理解 Policy 不是某人欽定的，而是社群共識的產物——這是 Debian 治理的精彩體現

→ [Final Project：私有 APT infrastructure](./final-project-apt-infrastructure.md)
