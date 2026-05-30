# Ch 16 — Lintian：靜態品質分析

> **目標**：理解 lintian 在品質保證的角色、tag 的嚴重等級體系、最常見的 error/warning 及其修正方法、override 機制的正確用法、以及為什麼「零 lintian warning」是值得追求的目標。

> **環境**：Lintian 2.116.x（Debian 12）。lintian 的 tag 隨版本增減，本章講通用概念和常見 tag。

## 為什麼需要 lintian？

你的套件 build 成功、能安裝、能執行——但它符合 Debian 的幾百條品質規範嗎？man page 壓縮了嗎？copyright 完整嗎？權限對嗎？binary strip 了嗎？依賴宣告齊了嗎？

人工檢查這幾百條規則不現實。lintian 是 Debian 的**靜態分析器**——它檢查你的套件對照 Debian Policy 和最佳實踐，報告違規。它是上傳前的品質閘門，也是 Debian archive 接受套件的隱性門檻。

> 名字由來：lint（程式碼靜態檢查工具的統稱）+ Debian = lintian。它對套件做的事，就像 lint 對程式碼做的。

## 先建立直覺：lintian 是個規則引擎

```
你的 .deb / .dsc / .changes
        │
        ▼
┌────────────────────────────────────┐
│         lintian 規則引擎            │
│  幾千條 check（對照 Policy + 慣例）  │
│  - 檔案放對位置了嗎？               │
│  - man page 壓縮了嗎？              │
│  - copyright 完整嗎？               │
│  - 依賴宣告對嗎？                   │
│  - 有沒有用 root 不該有的權限？      │
└────────────┬───────────────────────┘
             ▼
   分等級的 tag 報告：
   E: error      ← 嚴重違規，幾乎一定要修
   W: warning    ← 應該修
   I: info       ← 可考慮的建議
   P: pedantic   ← 吹毛求疵（預設不顯示）
```

每個 tag 對應一條規則。理解 tag 的等級和意義，是用好 lintian 的關鍵。

## 跑 lintian

```bash
# 檢查 .deb
lintian greet_1.0-1_amd64.deb

# 檢查 .changes（連帶檢查所有相關 .deb 和 source）
lintian greet_1.0-1_amd64.changes

# 檢查 source package
lintian greet_1.0-1.dsc

# 詳細模式（顯示每個 tag 的完整說明）
lintian -i greet_1.0-1_amd64.changes
#   -i : info，顯示 tag 的詳細解釋和修法

# 顯示更多等級的 tag
lintian -I greet_*.changes      # 也顯示 info tag
lintian --pedantic greet_*.changes  # 連 pedantic 都顯示
lintian -iI --pedantic greet_*.changes  # 全開 + 詳細說明（最嚴格）
```

> 日常用 `lintian -i`（詳細說明很有用）。追求高品質用 `lintian -iI --pedantic`。`debuild` 和 sbuild 可設定自動跑 lintian（Ch 14/15）。

## tag 的嚴重等級

```
E: error      → 違反 Policy 的「must」；Debian archive 通常拒絕
W: warning    → 違反 Policy 的「should」或重要慣例；應該修
I: info       → 建議改進，非強制
P: pedantic   → 風格性的吹毛求疵
X: experimental → 實驗性檢查，可能誤報
O: overridden → 你明確 override 掉的 tag
```

對應 Policy 的助動詞：
- `must` / `required` → 違反通常是 `E`
- `should` / `recommended` → 違反通常是 `W`
- 慣例、建議 → `I` / `P`

## 最常見的 tag 與修法

### Error 級（一定要修）

```
E: greet: no-copyright-file
→ 缺 /usr/share/doc/greet/copyright
→ 修：寫 debian/copyright（Ch 10）

E: greet: maintainer-script-calls-systemctl
→ maintainer script 直接呼叫 systemctl（該用 dh_installsystemd）
→ 修：用 debian/greet.service + dh_installsystemd（Ch 29）

E: greet: binary-without-manpage usr/bin/greet
→ 執行檔沒有 man page（在某些情況是 error）
→ 修：寫 debian/greet.1 man page，用 dh_installman
```

### Warning 級（應該修）

```
W: greet: package-installs-into-obsolete-dir
→ 裝到過時的目錄（如 /usr/X11R6）
→ 修：用現代路徑

W: greet: description-synopsis-starts-with-article
→ Description 第一行用 "A"/"The" 開頭
→ 修：改寫 synopsis，別用冠詞開頭

W: libgreet1: shared-lib-without-dependency-information
→ shared library 沒有 shlibs/symbols 資訊
→ 修：dh_makeshlibs（Ch 19）

W: greet: hardening-no-fortify-functions usr/bin/greet
→ binary 沒啟用 hardening（FORTIFY_SOURCE）
→ 修：用 dh_auto_build（自動套 dpkg-buildflags），別手寫 make
```

### Info / Pedantic（可考慮）

```
I: greet: spelling-error-in-description-* teh the
→ 描述有拼字錯誤
→ 修：改正拼字

P: greet: no-homepage-field
→ 沒有 Homepage 欄位
→ 修：在 control 加 Homepage
```

## override 機制：正確 vs 濫用

有時 lintian 誤報，或你有正當理由違反某規則。可以 override：

```
# debian/greet.lintian-overrides
# 格式：<package> [<type>]: <tag> [<extra>]

# 正當的 override：附理由註解
greet: binary-without-manpage usr/bin/greet-internal-helper
# greet-internal-helper is not meant to be called by users directly,
# it has no man page on purpose.
```

```bash
# 跑 lintian 時被 override 的 tag 顯示為 O:
lintian greet_*.deb
# O: greet: binary-without-manpage usr/bin/greet-internal-helper
```

> **override 是逃生口不是地毯**。每個 override 都該有註解說明**為什麼**這個 tag 不適用。用 override 把 warning 掃到地毯下（不修只壓掉）是壞習慣——下一個維護者會困惑。lintian 自己也檢查 `unused-override`（override 了但其實沒觸發的 tag）。

## 為什麼追求零 warning？

「能裝就好，warning 不管」是常見心態。但追求零 warning 有實質價值：

- **warning 常是真問題的訊號**：`shared-lib-without-dependency-information` 表示你的 library 依賴算不準；`hardening-no-fortify` 表示安全選項沒開
- **Debian archive 的隱性門檻**：很多 warning 雖不直接擋上傳，但 sponsor/reviewer 看到一堆 warning 會質疑品質
- **可維護性**：零 warning 的套件，下次有新 warning 冒出來時一眼看到（不會淹沒在既有 warning 裡）
- **專業度**：乾淨的 lintian 輸出是套件品質的直接體現

練習 C 的目標就是把一個套件做到零 warning。

## 故意製造 lintian 問題

```bash
# 製造一堆 lintian 抱怨來學習
# 1. 刪掉 copyright
rm debian/greet/usr/share/doc/greet/copyright   # build 後手動刪
lintian greet_*.deb
# E: greet: no-copyright-file

# 2. Description 用冠詞開頭
# 改 control: Description: A friendly tool
lintian greet_*.deb
# W: greet: description-synopsis-starts-with-article

# 3. 用 -i 看詳細修法
lintian -i greet_*.deb
# （每個 tag 附完整解釋和修正建議）
```

## lintian 的 profile

lintian 對不同情境用不同的 check 集合（profile）：

```bash
# Debian profile（預設，最嚴格）
lintian greet_*.deb

# Ubuntu profile（Ubuntu 特定的規則）
lintian --profile ubuntu greet_*.deb
```

> 如果你打包給 Ubuntu PPA（Ch 24），用 `--profile ubuntu`——它有 Ubuntu 特定的檢查（如 Maintainer 欄位的處理）。打包給 Debian 用預設 profile。

## 踩雷集錦

1. **忽略 warning 只看 error**：很多 warning 是真問題的早期訊號（依賴算錯、hardening 沒開）。把 warning 也當回事

2. **濫用 override 壓掉 warning**：override 是給「誤報」和「有正當理由的例外」，不是給「我懶得修」。每個 override 要有理由註解

3. **不用 `-i` 不知道怎麼修**：光看 tag 名字常猜不出修法。`lintian -i` 給每個 tag 完整解釋和建議，一定要用

4. **以為 lintian 過了就完美**：lintian 是靜態檢查，抓不到功能性問題（程式跑起來對不對）。它和 autopkgtest（Ch 17，動態測試）互補，不能互相替代

5. **新版 lintian 冒出新 warning 就慌**：lintian 持續新增 check。升級 lintian 後可能冒出以前沒有的 warning——這是 lintian 變嚴格了，不是你的套件壞了。逐一評估

6. **`override` 檔案放錯位置**：override 檔案是 `debian/<package>.lintian-overrides`（每個 binary package 一個），不是 `debian/lintian-overrides`（除非 source-level override）

## 進階：lintian 在 Debian QA 的角色

lintian 不只是個本地工具，它是 Debian 全 archive QA 的一環：

- **lintian.debian.org**（現整合進 tracker）對整個 archive 跑 lintian，產生全域品質報告
- 維護者能看到自己套件的所有 lintian tag，archive-wide 的趨勢也可見
- 新的 lintian check 出現時，能立刻看到全 archive 有多少套件觸發——這驅動了大規模的品質改進

理解這點，你會發現 lintian 不是「煩人的檢查器」，而是維持五萬個套件品質一致的關鍵基礎設施。寫出零 warning 的套件，是對這個生態的尊重。

```bash
# 看一個套件在 archive 的 lintian 狀態
# https://tracker.debian.org/pkg/<package> 的 lintian 區塊
```

## 動手練習

1. 對練習 B 的 greet 跑 `lintian -iI --pedantic`，看它報什麼。逐一用 `-i` 的說明理解每個 tag，能修的修掉

2. 故意製造問題：Description 用 "A" 開頭、刪掉一個 man page、binary 不 strip，分別跑 lintian 看對應的 tag

3. 寫一個 override：對某個你有正當理由的 tag 寫 `debian/greet.lintian-overrides`（附理由註解），確認它變成 `O:` 而非 `W:`

4. 對一個官方套件跑 lintian（`apt source` 後 `lintian`），看高品質套件的 lintian 輸出有多乾淨

## 本章重點整理

- lintian 是 Debian 的靜態品質分析器，對照 Policy 和慣例檢查套件
- tag 分等級：E（error，幾乎一定修）/ W（warning，應該修）/ I（info）/ P（pedantic）
- 常見問題：缺 copyright、Description 格式、shared lib 缺依賴資訊、hardening 沒開
- override（`debian/<pkg>.lintian-overrides`）給誤報和正當例外，每個要有理由註解
- lintian（靜態）和 autopkgtest（動態）互補；零 warning 是值得追求的品質目標

## 自我檢核

- [ ] 知道 lintian tag 的四個等級（E/W/I/P）對應 Policy 的什麼助動詞
- [ ] 能說出至少三個常見 lintian tag 及其修法
- [ ] 知道 override 的正確用法（誤報/正當例外 + 理由註解），以及為什麼不該濫用
- [ ] 能解釋為什麼追求零 warning（warning 是問題訊號、archive 門檻、可維護性）
- [ ] 知道 lintian 和 autopkgtest 的分工（靜態 vs 動態）

## 延伸閱讀

### 官方文件

- **[Lintian Tags 索引](https://lintian.debian.org/tags)**
  - **讀哪裡**：搜尋你遇到的具體 tag，每個有完整說明和修法
  - **學什麼**：所有 tag 的權威解釋；遇到不懂的 tag 第一個查這裡
  - **前提**：無

- **[Lintian User's Manual](https://lintian.debian.org/manual/)**
  - **讀哪裡**:「Overrides」和「Severity」章節
  - **學什麼**：override 的完整語法、severity 體系的設計
  - **前提**：讀完本章

### 部落格 / 文章

- **[Debian Policy Manual](https://www.debian.org/doc/debian-policy/)**（lintian 的規則來源）
  - **這篇說什麼**：lintian 的每個 error/warning 背後都對應一條 Policy
  - **讀哪裡**：遇到 lintian tag 時，查它對應的 Policy 章節理解「為什麼」
  - **為什麼值得讀**：lintian 告訴你「違反了什麼」，Policy 告訴你「為什麼這是規則」

→ [Ch 17 autopkgtest：自動化測試](./17-autopkgtest.md)
