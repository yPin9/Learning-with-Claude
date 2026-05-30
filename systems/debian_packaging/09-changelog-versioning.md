# Ch 9 — debian/changelog 與版本號

> **目標**：理解 `debian/changelog` 的精確格式與它驅動的 build 行為、Debian 版本號的結構（epoch:upstream-revision）、以及版本比較演算法——這個演算法決定了「升級」的方向，寫錯版本號是真正會出事的錯誤。

> **環境**：dpkg 1.21.x。版本比較演算法由 Debian Policy §5.6.12 定義，跨版本穩定。

## 為什麼 changelog 不只是「更新紀錄」？

在大部分專案，CHANGELOG 是給人看的文件，改不改無所謂。在 Debian 打包，`debian/changelog` 是**有功能的**：

- 它的**第一行**決定整個套件的**版本號**和**目標 suite**
- `dpkg-buildpackage` 從它讀套件名、版本、urgency
- 版本號錯了，apt 會拒絕升級、或往錯誤方向「升級」（其實是降級）

所以 changelog 不是隨手寫的紀錄，它是 build 的**輸入**。格式錯一個字元，build 就失敗。

## 先建立直覺：changelog 的格式是有意義的

```
foo (1.2.3-1) unstable; urgency=medium
│    │         │         │
│    │         │         └─ urgency（影響 stable 遷移速度）
│    │         └─ 目標 suite（unstable/stable/...）
│    └─ 版本號（驅動一切）
└─ source package 名稱

  * 這次改了什麼（給人看的條目，每條 * 開頭）
  * 修了 bug #12345

 -- Maintainer Name <email>  Thu, 29 May 2025 12:00:00 +0000
 │                            │
 └─ 簽署行（前面一個空格）       └─ RFC2822 格式時間戳
```

格式極度講究：第一行的套件名後面**一個空格**接括號版本；簽署行**一個空格**開頭、`--` 後**兩個空格**接名字。錯了 dpkg 解析失敗。

正因為格式這麼嚴格，**永遠用工具 `dch` 編輯，不要手寫**。

## 用 dch 編輯 changelog

```bash
# 在 source 目錄裡

# 開新版本條目（自動 bump 版本、填時間戳、開編輯器）
dch -i        # increment：1.2.3-1 → 1.2.3-2（Debian revision +1）
dch -v 1.2.4-1   # 指定新版本（upstream 升到 1.2.4）

# 新增一條 changelog 條目（不開新版本）
dch "Fix segfault when config is empty"

# 標記為可發布（把 UNRELEASED 改成目標 suite）
dch -r

# 首次建立 changelog（新套件）
dch --create --package foo -v 1.0-1
```

`dch` 自動處理格式、時間戳、你的身份（`DEBFULLNAME`/`DEBEMAIL`），你只管寫內容。

## Debian 版本號的完整結構

這是本章最重要的部分。一個完整的 Debian 版本號：

```
        1:2.10.3-2~bpo12+1
        │ │      │ │
        │ │      │ └─ Debian revision（含 backport 標記）
        │ │      └─ Debian revision 開始
        │ └─ upstream version
        └─ epoch（冒號前）

完整格式：[epoch:]upstream_version[-debian_revision]
```

| 部分 | 範例 | 意義 |
|---|---|---|
| epoch | `1:` | 「強制重置」版本序，極少用（見下）|
| upstream_version | `2.10.3` | upstream 的版本 |
| debian_revision | `-2` | Debian 對同一 upstream 版本的第幾次打包修訂 |

### upstream_version：upstream 怎麼版本就怎麼版本

`2.10.3`、`1.0~rc1`、`20240115`（日期版本）、`1.0+git20240115`——跟著 upstream。

### debian_revision：打包修訂

同一個 upstream 版本，Debian 可能打包多次（修 packaging bug、調整依賴），用 `-N` 區分：

```
foo 2.10.3-1   ← 第一次打包 2.10.3
foo 2.10.3-2   ← 同樣的 upstream 2.10.3，但 packaging 改了（如修了 control）
foo 2.10.4-1   ← upstream 升到 2.10.4，revision 重置為 1
```

> 規則：upstream 版本變了，revision 重置成 `-1`。同 upstream 版本只改 packaging，revision +1。這個區分讓人一眼看出「是 upstream 更新還是只是 repackage」。

### epoch：核武級的版本重置（避免使用）

epoch 是版本號最前面的 `N:`，用來解決「upstream 改了版本號編碼方式，導致新版本的版本字串比舊版本『小』」的窘境。

```
情境：upstream 從日期版本改成語意版本
舊：20231231        ← 字串比較：20231231 > 1.0（因為 2 > 1）
新：1.0             ← 但 1.0 是更新的！版本比較會認為它更舊

解法：給新版本加 epoch
新：1:1.0           ← epoch 1 > epoch 0（隱含），強制 1:1.0 > 20231231
```

> **epoch 是不可逆的、會永遠留在版本號裡的疤痕**。一旦用了 `1:`，這個套件之後的版本永遠帶著 `1:`。社群強烈建議：能不用就不用。只有 upstream 真的把版本編碼搞砸、且無法用其他方式（如 `~` 後綴）解決時才動用。

## 版本比較演算法

apt 怎麼知道 `2.10.3-2` 比 `2.10.3-1` 新？靠 `dpkg --compare-versions` 背後的演算法。理解它，你才不會寫出「看起來升級實際降級」的版本號。

```bash
# 直接測試版本比較
dpkg --compare-versions 2.10.3-2 gt 2.10.3-1 && echo "newer"
# newer

dpkg --compare-versions 1.0~rc1 lt 1.0 && echo "rc1 is older"
# rc1 is older   ← 注意！~ 比空字串還小
```

演算法（簡化）：分別比較 epoch、upstream_version、debian_revision。每部分的比較規則：

```
逐字元比較，但分成「非數字段」和「數字段」交替處理：

非數字段：按 ASCII 比較，但有特殊規則：
  ~ （波浪號）排在所有東西之前，連「空」都比它大
  字母 排在非字母（除了 ~）之前
  順序：~ < 空 < 字母 < 其他符號

數字段：按數值比較（10 > 9，不是字串比較的 "10" < "9"）
```

最反直覺的是 **`~`（波浪號）比空還小**：

```bash
dpkg --compare-versions 1.0~beta lt 1.0 && echo "~beta < release"
# ~beta < release   ← 1.0~beta 比 1.0 舊！

dpkg --compare-versions 1.0~~ lt 1.0~ && echo "yes"
# yes   ← ~~ 比 ~ 還小
```

這個 `~` 規則極其有用：

```
版本演進想要的順序：
  1.0~alpha  →  1.0~beta  →  1.0~rc1  →  1.0  →  1.0+really.final

用 ~ 表達 pre-release：
  1.0~rc1 < 1.0  ✓（rc 在正式版之前）

backport 用 ~：
  2.0-1~bpo12+1 < 2.0-1  ✓
  （backport 版本「小於」正式進 stable 的版本，
   這樣當套件正式進 release 時能無痛升級覆蓋 backport）
```

## 數字 vs 字串比較的陷阱

```bash
# 數字段按數值比，不是字串
dpkg --compare-versions 1.10 gt 1.9 && echo "1.10 > 1.9"
# 1.10 > 1.9   ← 正確！數值比較，10 > 9
#                （如果是字串比較會錯，因為 "1" < "9"）
```

但要小心混合情況：

```bash
dpkg --compare-versions 1.0a gt 1.0 && echo yes || echo no
# no... 等等，1.0a 應該比 1.0 新（字母 > 空）
dpkg --compare-versions 1.0a gt 1.0 && echo "1.0a newer"
# 1.0a newer   ← 字母 a > 空字串（在非~的情況下）
```

> 規則繞，但記住兩個關鍵：**數字段按數值比**（避免 1.10 < 1.9 的錯誤），**`~` 比空還小**（用於 pre-release 和 backport）。其他情況用 `dpkg --compare-versions` 實測，不要猜。

## changelog 的目標 suite

第一行的 suite 欄位（`unstable`、`UNRELEASED`...）告訴 build 工具和 archive 這個版本要進哪裡：

```
foo (1.0-1) unstable; urgency=medium     ← 要上傳到 Debian unstable
foo (1.0-1) UNRELEASED; urgency=medium   ← 還在開發，未發布（dch -i 預設）
foo (1.0-1) bookworm; urgency=medium     ← 進特定 release（backport/stable update）
foo (1.0-1) jammy; urgency=medium        ← Ubuntu suite（PPA）
```

`UNRELEASED` 是個慣例標記：表示「這個版本還在做，別上傳」。完成後用 `dch -r` 改成真正的 suite。

## 故意弄壞：版本號倒退

```bash
# 假設目前 changelog 是 1.0-2，你手滑寫成 1.0-1（更舊）
# build 出 foo_1.0-1.deb
# 但系統已裝 1.0-2

sudo dpkg -i foo_1.0-1_all.deb
# dpkg: warning: downgrading foo from 1.0-2 to 1.0-1
# （dpkg 警告你在降級！）

# apt 更嚴格，直接拒絕「升級」到更舊版本
# 你的「新」套件永遠不會被當成更新
```

dpkg 用版本比較判斷方向。寫了個比現有更舊的版本號，dpkg 認為這是降級，apt 根本不會自動裝它。這就是為什麼版本號是「真正會出事」的欄位——錯了套件升不上去。

## 踩雷集錦

1. **手寫 changelog 格式錯誤**：少一個空格、時間戳格式不對、簽署行錯誤 → `dpkg-parsechangelog` 失敗，build 中止。永遠用 `dch`

2. **版本號倒退**：新版本號比舊的「小」（按比較演算法），套件升不上去。改版本前用 `dpkg --compare-versions OLD lt NEW` 確認方向

3. **誤用 epoch**：為了「讓版本變大」隨便加 epoch，結果永遠拔不掉。epoch 是最後手段，先考慮 `~` 後綴或其他方法

4. **pre-release 不用 `~`**：把 release candidate 標成 `1.0rc1`（沒有 `~`），結果 `1.0rc1 > 1.0`（字母比空大），rc 版本被當成比正式版新。正確是 `1.0~rc1`

5. **upstream 升級但 revision 沒重置**：upstream 從 1.0 升到 1.1，你寫 `1.1-3`（接續舊 revision）。應該重置成 `1.1-1`。revision 是「對這個 upstream 版本的打包次數」

6. **數字段補零的誤解**：`1.01` 和 `1.1`——數字段 `01` 和 `1` 數值相等？實測 `dpkg --compare-versions 1.01 eq 1.1` 為真（數值比較）。但別依賴這個，版本號別亂補零

## 進階：urgency 與 stable 遷移

`urgency=` 欄位（low/medium/high/critical）影響套件從 unstable 遷移到 testing 的速度：

```
urgency=low      → 需在 unstable 待 10 天才遷移到 testing
urgency=medium   → 5 天
urgency=high     → 2 天
urgency=critical → 1 天（安全修復用）
```

這是 Debian 的品質閘門：新版本先進 unstable，「冷卻」一段時間（讓人發現 bug），無嚴重 bug 才自動遷進 testing（未來的 stable）。urgency 高表示「這個修復很急（如安全漏洞），縮短冷卻期」。

```bash
# 一個套件的遷移狀態可在 tracker 查
# https://tracker.debian.org/pkg/<package>
```

這個機制（migration / britney）是 Debian 維持 stable 品質的核心，Ch 33 會在 transitions 脈絡再碰到。

## 動手練習

1. 用 `dch` 玩版本：`dch --create --package test -v 1.0-1`，然後 `dch -i`（看版本變 1.0-2），`dch -v 2.0-1`（upstream 升級），觀察 changelog 格式

2. 大量測試版本比較，建立直覺：
   ```bash
   for pair in "1.0~rc1:1.0" "1.10:1.9" "1.0a:1.0" "1:1.0:2.0" "2.0-1~bpo:2.0-1"; do
       a=${pair%:*}; b=${pair#*:}
       dpkg --compare-versions "$a" lt "$b" && echo "$a < $b" || echo "$a >= $b"
   done
   ```
   解釋每個結果為什麼

3. 故意製造版本倒退：build 一個 1.0-2，裝起來，再 build 一個 1.0-1，`dpkg -i` 看降級警告

4. 找一個套件的 changelog（`apt source` 後看 `debian/changelog`），找出它用過 epoch 嗎？有 `~` 版本嗎？讀它的歷史看版本怎麼演進

## 本章重點整理

- `debian/changelog` 第一行驅動 build：套件名、版本號、目標 suite 都從這讀
- 版本結構：`[epoch:]upstream_version[-debian_revision]`；upstream 升級時 revision 重置為 -1
- 版本比較：數字段按數值比、`~` 比空還小（用於 pre-release 和 backport）
- epoch 是不可逆的最後手段，避免使用
- 永遠用 `dch` 編輯，不要手寫（格式極嚴格）；改版本前用 `dpkg --compare-versions` 確認方向

## 自我檢核

- [ ] 不看筆記，能說出 Debian 版本號的三個組成部分
- [ ] 能解釋為什麼 `1.0~rc1 < 1.0`，以及這個特性怎麼用在 backport
- [ ] 知道數字段按數值比較（`1.10 > 1.9`），不是字串比較
- [ ] 能說出 epoch 解決什麼問題，以及為什麼要避免用它
- [ ] upstream 從 1.0 升到 1.1，新的 Debian revision 應該是多少（-1，重置）

## 延伸閱讀

### 官方文件

- **[Debian Policy §5.6.12 (Version)](https://www.debian.org/doc/debian-policy/ch-controlfields.html#version)**
  - **讀哪裡**：整節，特別是版本比較演算法的精確定義
  - **學什麼**：本章版本比較規則的權威來源，含所有邊界情況
  - **前提**：讀完本章

- **[deb-changelog(5) man page](https://manpages.debian.org/bookworm/dpkg-dev/deb-changelog.5.html)**
  - **讀哪裡**：format 定義那節
  - **學什麼**：changelog 格式的精確規格；理解 dch 在幫你維護什麼
  - **前提**：無

### 部落格 / 文章

- **[The Debian version string explained](https://readme.phys.ethz.ch/linux/debian_version_string/)** 或 Raphaël Hertzog 關於版本的文章
  - **這篇說什麼**：用大量範例拆解版本比較，特別是 `~` 和 epoch 的實戰用法
  - **讀哪裡**：版本比較範例那部分
  - **為什麼值得讀**：版本比較的規則很繞，多看幾組範例才能建立直覺

→ [Ch 10 debian/copyright：授權追蹤](./10-debian-copyright.md)
