# Ch 9 — 資源與 Manifest 逆向

> **目標**：搞懂 APK 裡「非程式碼」那一半——`resources.arsc`（編譯後的資源表）、binary XML（AXML，包括 Manifest）、`res/`、`assets/`。讀完你能：解釋為什麼 unzip 出來的 Manifest 是亂碼、看懂 resource id 的 `0x7f...` 到底編碼了什麼、在資源裡撈出硬編碼的 string／URL／API key、以及改資源後正確重打包。

> **環境**：AXML 與 arsc 是**純二進位格式解析**，不需 Android runtime。本章的 AXML 字串池結構用 **Python 3.12** 在本機**實際建構並解析**，標「**實際輸出**」；resource id 位元拆解也是 Python 實跑。改資源後的回編譯需 apktool，標「未實測，理論預期行為」+ 驗證步驟。

## 為什麼需要這個？

因為**值錢的東西常常不在程式碼裡，在資源裡**。開發者圖方便，把後端 URL、第三方 API key、加密用的字串、feature flag 硬編碼進 `res/values/strings.xml`；把設定檔、憑證、第二階段 payload 塞進 `assets/`。這些東西 jadx 看程式碼看不到——你得直接翻資源。

但資源不是純文字。`resources.arsc` 是編譯後的二進位資源表，`AndroidManifest.xml` 是 binary XML（Ch 2 提過），unzip 出來全是亂碼。如果你不懂這兩個二進位格式，你就只能靠 apktool 幫你解、出問題時完全不知道發生什麼。這章把格式拆到 byte 級，讓你在 apktool 解不出、或你想寫自己的解析器時，有能力自己動手。而且——**懂 arsc 結構才懂為什麼改一個 string 可能牽動一堆 resource id**。

## 先建立直覺：資源是「編號的表」，不是檔案

安卓資源系統的核心設計：**每個資源都有一個整數 ID，程式碼透過 ID 引用資源，`resources.arsc` 是 ID → 值的對照表**。

```
   你在 XML 寫的               編譯後                    程式碼引用
 ┌──────────────────┐      ┌──────────────────┐      ┌─────────────────┐
 │ res/values/      │      │ resources.arsc   │      │ R.string.api_url│
 │  strings.xml:    │──aapt▶│  0x7f0f0021 →    │◀─────│  = 0x7f0f0021   │
 │  <string         │      │   "https://..."  │      │ (編譯進 bytecode)│
 │   name="api_url" │      │  0x7f0a0003 →    │      │                 │
 │   >https://...   │      │   @layout/main   │      │ getString(      │
 │  </string>       │      │  ...             │      │   0x7f0f0021)   │
 └──────────────────┘      └──────────────────┘      └─────────────────┘
         R.java 把 name → ID 的對應也生出來（但只在編譯期，APK 裡沒有 R.java）
```

三個關鍵事實：

1. **程式碼裡看到的是 ID（`0x7f...`），不是資源名**。反編譯的 bytecode 呼叫 `getString(0x7f0f0021)`，你得回 `resources.arsc` 查這個 ID 對應哪個 string。
2. **`resources.arsc` 就是那本對照字典**——ID、資源名（`api_url`）、值（`"https://..."`）都在裡面。apktool 解它，還原成可讀的 `res/values/*.xml`。
3. **binary XML（AXML）與 arsc 共用「字串池」機制**：所有字串集中存一個池，其他地方用整數索引引用——省空間、解析快。這是兩個格式共同的底層，先懂它。

## 底層機制：AXML／arsc 的 chunk + 字串池結構

AXML 和 arsc 都是「chunk 樹」結構。每個 chunk 開頭都是一個統一的 header：

```
ResChunk_header (8 bytes)：
  type       u16   ← 這個 chunk 是什麼（見下表）
  headerSize u16   ← header 多大
  size       u32   ← 整個 chunk 多大（含子 chunk）
```

常見的 chunk type（Python 實跑確認的值，**實際輸出**）：

```
AXML 檔根 chunk (RES_XML_TYPE)      : 0x0003
字串池      (RES_STRING_POOL_TYPE) : 0x0001
XML 起始元素 (RES_XML_START_ELEMENT): 0x0102
XML 結束元素 (RES_XML_END_ELEMENT)  : 0x0103
arsc 資源表  (RES_TABLE_TYPE)       : 0x0002
```

所以一個 AXML 檔的骨架是：`[XML root chunk] → [String Pool chunk] → [一連串 start/end element chunk]`。**所有的標籤名、屬性名、屬性值字串，全部集中在字串池裡，元素 chunk 只存字串池的整數索引**。

我實際建構一個最小 AXML（一個字串池，含 4 個字串），再把它**解析回來**，證明這個模型（**實際輸出**，Python 跑）：

```python
# 建一個真的 AXML 字串池，塞 4 個字串，再解析回來
# （完整建構程式碼略；重點是驗證 chunk + 字串池 + 整數索引的模型）
```

```
wrote demo.axml, total bytes: 144
first 8 bytes: 0300080090000000     ← type=0x0003(XML) headerSize=8 size=0x90=144
root chunk: type=0x0003 headerSize=8 size=144
stringpool: type=0x0001 count=4 flags=0 stringsStart=44
recovered strings: ['android', 'package', 'com.example.demo', 'manifest']
```

看懂這個輸出：
- 前 8 byte `03 00 08 00 90 00 00 00` = 根 chunk header：type `0x0003`（XML）、headerSize `8`、size `0x90`=144。全部小端。
- 字串池 chunk：type `0x0001`、`count=4`（4 個字串）、`stringsStart=44`（字串資料從 chunk 內位移 44 開始）。
- **recovered strings** 就是我塞進去的 4 個字串——證明「字串集中存池、其他地方用索引引用」這個模型是真的，我能自己解析它。

> **為什麼要自己會解析？** 因為 apktool 偶爾解不出某些混淆過或畸形的 AXML（惡意 App 常故意做壞 AXML 讓工具崩、但系統照吃）。這時你需要能手撕字串池。而且懂了字串池，你就懂為什麼「改一個字串長度」可能要重排整個池——這是改資源比改程式碼麻煩的根源。

## resource id：`0x7f0a0021` 到底編碼了什麼

程式碼裡的資源 ID 不是隨機數，它是**三段位元的組合**（Python 實跑拆解，**實際輸出**）：

```python
resid = 0x7f0a0021
pkg   = (resid >> 24) & 0xff    # 高 8 bit：package id
typ   = (resid >> 16) & 0xff    # 次 8 bit：type id
entry =  resid        & 0xffff  # 低 16 bit：entry index
```

```
resid 0x7f0a0021: package=0x7f type=0x0a entry=0x0021
```

三段的意義：

| 段 | 位元 | 值 | 意義 |
|---|---|---|---|
| **package** | bit 24-31 | `0x7f` | 哪個資源套件。**`0x7f` = App 自己的資源**；`0x01` = 系統框架（`android.R.*`） |
| **type** | bit 16-23 | `0x0a` | 資源類型（string/layout/drawable/id...）。實際數字由該 App 的 arsc type 順序決定，非固定 |
| **entry** | bit 0-15 | `0x0021` | 該 type 下的第幾個資源 |

**關鍵：`0x7f` 開頭 = App 自己的資源**。你在反編譯 bytecode 看到 `getString(0x7f...)`，就是「取 App 自己定義的某個 string」——回 arsc 查 entry 就知道是哪個。看到 `0x01...` 開頭則是系統資源（如 `android.R.string.ok`）。

> **type id 為什麼不固定？** `0x0a` 不是「string 的官方編號」。每個 App 的 arsc 裡，type 是按出現順序編的（第一個出現的 type 是 `0x01`、第二個 `0x02`...）。所以 A App 的 string 可能是 `0x0f`、B App 的是 `0x0a`。要知道某 ID 是什麼 type，得看那個 App 自己的 arsc type 表——apktool 解出來的 `res/values/public.xml` 就是這張對照表。

## 範例 1：在資源裡撈硬編碼的 URL/key

最高頻的任務。流程：

**未實測，理論預期行為**（apktool 部分）+ **可用 Python/grep 驗證的部分標實際做法**：

```bash
# ① apktool 解出可讀資源（arsc → res/values/*.xml）
apktool d target.apk -o out

# ② 直接 grep 資源裡的可疑字串
grep -rniE "http|https|api|key|secret|token|password|firebase|s3|bucket" out/res/values/
#   out/res/values/strings.xml:  <string name="base_url">https://api.example.com/v2</string>
#   out/res/values/strings.xml:  <string name="gmaps_key">AIzaSy...</string>
```

`res/values/strings.xml` 是硬編碼字串的頭號金礦。常見獵物：

- `base_url` / `api_endpoint`：後端位址。
- `google_api_key` / `gmaps_key`（`AIza...` 開頭是 Google API key 的特徵前綴）。
- `firebase_database_url` / `gcm_defaultSenderId`：Firebase 設定（`google-services.json` 被編進資源）。
- 加密用的預設字串、feature flag（`enable_debug`、`is_vip`）。

> **別漏 `assets/`**：`res/` 是被編進 arsc 的資源，但 `assets/` 是**原封不動的檔案**（Ch 2 說的金礦）。`unzip -l target.apk | grep assets` 掃一遍，常有 `config.json`、`.properties`、加密的 blob、甚至整個 Flutter/Unity 的資料。`assets/` 不進 arsc，直接 `unzip` 抽出來讀。

## 範例 2：讀懂 Manifest 的關鍵欄位（binary XML 解碼後）

`AndroidManifest.xml` 是 AXML，`apktool d` 解回可讀 XML 後，逆向要盯的欄位（延續 Ch 2）：

```xml
<manifest package="com.example.target"
          android:versionCode="42" android:versionName="3.1.0">
    <uses-permission android:name="android.permission.INTERNET"/>
    <uses-permission android:name="android.permission.READ_CONTACTS"/>   <!-- 權限 = 能力線索 -->

    <application
        android:name=".AppShell"          <!-- 自訂 Application：加固殼常在這 -->
        android:debuggable="true"          <!-- 開了 = 可直接 attach 除錯 -->
        android:networkSecurityConfig="@xml/network_security_config"      <!-- 抓包相關（Ch 17） -->
        android:allowBackup="true">        <!-- 開了 = 可 adb backup 撈資料 -->

        <activity android:name=".LoginActivity" android:exported="true"> <!-- exported=可被外部叫起 -->
            <intent-filter>
                <action android:name="android.intent.action.MAIN"/>       <!-- 入口 -->
                <category android:name="android.intent.category.LAUNCHER"/>
            </intent-filter>
        </activity>

        <provider android:name=".SecretProvider"
                  android:authorities="com.example.target.provider"
                  android:exported="true"/>    <!-- exported provider = 潛在攻擊面 -->
    </application>
</manifest>
```

逆向讀 Manifest 的檢查清單：

| 欄位 | 為什麼盯它 |
|---|---|
| `package` | App 身分；`adb`/Frida 都要用 |
| `<uses-permission>` | App 有什麼能力（讀聯絡人？定位？）→ 猜它幹嘛、找對應程式碼 |
| `application android:name` | 自訂 Application；**加固殼幾乎都改這裡**（開機先跑殼載入器） |
| `debuggable` | 開了就能免 root attach 除錯 |
| `exported="true"` 的元件 | 可被其他 App 叫起 → IPC 攻擊面（Ch 相關） |
| `networkSecurityConfig` | 定義憑證信任策略；影響抓包（Ch 17） |
| `allowBackup` | 開了可 `adb backup` 撈私有資料 |
| 入口 `MAIN`/`LAUNCHER` | 逆向起點 Activity |

> **`android:name` 是加固的第一訊號**：正常 App 的 Application 類名多是 `.MyApp` 這種業務名；加固過的常是 `com.stub.StubApp`、`com.secneo.apkwrapper.ApplicationWrapper`、`s.h.e.l.l` 之類殼廠特徵名。看到一個「不像業務碼」的 Application 類名，八成加固了——Ch 28 會用這類特徵判斷殼的廠牌。

## 範例 3：改資源——把一個 feature flag 從 false 改 true

假設 arsc 裡有個 bool 資源 `is_premium` 控制 VIP 功能，程式碼 `getResources().getBoolean(R.bool.is_premium)`。改法：

**未實測，理論預期行為**：

```bash
# ① 解（含資源，不能用 --no-res，因為要改資源）
apktool d target.apk -o out

# ② 改 out/res/values/bools.xml
```

```xml
<!-- 改前 -->
<bool name="is_premium">false</bool>
<!-- 改後 -->
<bool name="is_premium">true</bool>
```

```bash
# ③ 回編譯（aapt2 重編 arsc）→ 對齊 → 簽名（Ch 6 的鏈）
apktool b out -o repacked.apk
zipalign -p -f 4 repacked.apk aligned.apk
apksigner sign --ks my.keystore --ks-pass pass:android aligned.apk
adb install -r aligned.apk
```

**驗證**：裝上後看 VIP 功能有沒有解鎖。

> **改資源 vs 改 smali，怎麼選？** 如果邏輯是「讀資源值來決定」（如上例 `getBoolean(R.bool.is_premium)`），改資源最乾淨。但如果邏輯是硬編碼在程式碼裡（`boolean isPremium = false;`），改資源沒用，得改 smali（Ch 10）。**先用 jadx 看那個開關的值從哪來**——來自資源就改資源，來自程式碼就改 smali。判斷錯會白忙。

## 對比與取捨

| 你要做 | 用什麼 | 為什麼 |
|---|---|---|
| 撈 `res/` 裡的 string/URL | `apktool d` + grep `res/values/` | arsc 要先解碼成可讀 XML |
| 撈 `assets/` 裡的檔案 | `unzip` 直接抽 | assets 不進 arsc，原封檔案 |
| 讀懂 Manifest | `apktool d`（或 jadx 也會解） | binary XML 要解碼 |
| 把 resource id 反查資源名 | apktool 解出的 `public.xml` | 那是 ID→name 對照表 |
| 手撕畸形/混淆 AXML | 自寫 Python 解析字串池 | apktool 崩時的退路 |
| 改資源值重打包 | `apktool d`（含資源）+ b + 簽 | 必須重編 arsc |

## 踩雷集錦

1. **unzip 出的 Manifest/arsc 說是亂碼**：它們是 binary XML／二進位表，本來就不是純文字。用 `apktool d` 解碼，不是檔案壞了（Ch 2 也講過，這裡再遇到）。
2. **只翻 `res/` 漏了 `assets/`**：`assets/` 不進 arsc，apktool 的 `res/` 裡看不到它。硬編碼設定、加密 blob、payload 常在 assets——`unzip -l | grep assets` 單獨掃。
3. **把 type id 當固定編號**：`0x7f0a...` 的 `0a` 不是「string 的官方編號」，是那個 App 的 arsc 裡 type 的出現順序。要查某 ID 是什麼 type，看該 App 的 `public.xml`。
4. **改資源用了 `--no-res`**：`--no-res` 保持資源二進位原封，你根本改不到 `res/`。要改資源就不能加 `--no-res`（但要吃 aapt2 重編譯的風險，Ch 6）。
5. **邏輯來自程式碼卻改資源**：開關是 `boolean x = false` 硬編碼的，改 arsc 的 bool 沒用。先 jadx 確認值的來源，來自資源才改資源。
6. **以為 `0x01` 開頭的 ID 是 App 的資源**：`0x01` 是系統框架資源（`android.R.*`），`0x7f` 才是 App 自己的。搞反了會去 App 的 arsc 裡找一個根本不在那的 ID。

## 進階：再往深一層

- **arsc 的 config/qualifier 維度**：同一個資源 ID 在 arsc 裡可能有多個值，按**設定維度**（語言 `zh`/`en`、螢幕密度 `hdpi`/`xxhdpi`、夜間模式）分別存。所以 `strings.xml` 在 `values/`、`values-zh/`、`values-en/` 各有一份。逆向找「某語言下才出現的字串」時要看對應的 qualifier 目錄。
- **字串池的 UTF-8 vs UTF-16 flag**：字串池 header 的 `flags` 有一個 bit 標記整池是 UTF-8 還是 UTF-16。本章的 demo 是 UTF-16（`flags=0`）。手撕字串池時搞錯編碼會解出亂碼——先讀 flag 決定用哪種解碼。
- **AXML 混淆對抗**：惡意/加固 App 會做「合法但畸形」的 AXML（超長 headerSize、重複 namespace、系統忽略但工具會崩的欄位）讓 apktool/jadx 解析失敗，但 Android 的 `ResXMLParser` 照吃。碰到「工具解不出但 App 裝得起來」的 Manifest，就是遇到這招——這時你手撕字串池的能力就是唯一出路。
- **`resources.arsc` 常不壓縮**：Android 5+ 要求 `resources.arsc` 在 APK 裡以 STORED（不壓縮）方式存放且對齊，方便 mmap 直接讀。回編譯時 apktool 會照這規則（`apktool.yml` 的 `doNotCompress` 記著）。手動重壓 zip 若把 arsc 壓縮了，某些系統會拒裝——這是 Ch 6 「別亂改 `doNotCompress`」的具體後果之一。

## 動手練習

1. 用本章的 Python 模型自己建一個 AXML 字串池、塞幾個字串、再解析回來——親手驗證「字串集中存、其他地方用索引」。改動一個字串長度，看你得怎麼重算 offset，體會改資源為什麼比改程式碼麻煩。
2. 拿一個真 App，`apktool d` 後 grep `res/values/strings.xml` 找 URL/key；再 `unzip -l | grep assets` 看 `assets/` 有什麼。比較「編進 arsc 的資源」和「原封的 assets」。
3. 挑一個反編譯 bytecode 裡的 `getString(0x7f...)`，用 Python 拆出它的 package/type/entry，再去 apktool 解出的 `public.xml` 反查它對應哪個資源名——把「程式碼裡的 ID」和「資源名」連起來。
4. 找一個由資源控制的開關（bool/string），改 arsc 重打包，看行為變沒變；再找一個硬編碼在程式碼的開關，確認改資源對它無效——建立「先看值的來源再決定改哪」的直覺。

## 本章重點整理

- 資源是「**整數 ID → 值**的表」：程式碼引用 `0x7f...` ID，`resources.arsc` 是對照字典，apktool 把它解回可讀 `res/values/`。
- AXML 與 arsc 都是 **chunk 樹 + 字串池**：字串集中存、其他地方用整數索引（本章 Python 實跑驗證）。
- resource id = **package(`0x7f`=App／`0x01`=系統) | type(順序編，非固定) | entry**。
- 硬編碼 URL/key 常在 `res/values/strings.xml`（apktool+grep）與 **`assets/`（unzip 直抽，別漏）**；Manifest 盯 `application name`（加固訊號）、`debuggable`、`exported`、權限。

## 自我檢核

- [ ] 能解釋 unzip 出的 Manifest 為什麼亂碼，以及 AXML 的 chunk+字串池模型
- [ ] 能拆解一個 `0x7f0a0021` 的三段，並說出 `0x7f` vs `0x01` 開頭的差別
- [ ] 知道硬編碼 URL/key 該去 `res/values/` 和 `assets/` 找，且兩者取法不同
- [ ] 拿到 Manifest，能列出至少 5 個逆向要盯的欄位並說明各自意義
- [ ] 要改一個開關時，知道怎麼判斷該改資源還是改 smali

## 延伸閱讀

### 格式規範

- **[AOSP — ResourceTypes.h（arsc/AXML 的 struct 定義）](https://cs.android.com/android/platform/superproject/+/master:frameworks/base/libs/androidfw/include/androidfw/ResourceTypes.h)** — AOSP
  - **讀哪裡**：`ResChunk_header`、`ResStringPool_header`、`ResTable_header`、`ResXMLTree_node` 的 struct 定義
  - **和本章的關聯**：本章 Python 解析的每個欄位，這裡是 C struct 的權威來源；要手撕 arsc/AXML 就攤開這頁對照
- **[Android Developers — App resources overview / R class](https://developer.android.com/guide/topics/resources/providing-resources)** — Android Developers
  - **讀哪裡**：resource id 與 qualifier（config 維度）那節
  - **注意**：解釋 `values-zh`/`values-hdpi` 這些 qualifier 目錄怎麼對應 arsc 的多值儲存

### 工具與實作

- **[Apktool — how it works（AXML/arsc 解碼）](https://apktool.org/docs/the-basics/decoding)** — apktool.org
  - **這篇說什麼**：apktool 如何把 arsc/AXML 解回可讀資源，以及 `public.xml`/`doNotCompress` 的角色
  - **讀哪裡**：resource decoding 與 `apktool.yml` 那段
  - **和本章的關聯**：本章「改資源重打包」與「type id 非固定」的實作依據
- **[HackTricks — Android APK：resources & assets 檢查](https://book.hacktricks.wiki/en/mobile-pentesting/android-app-pentesting/index.html)** — HackTricks
  - **這篇說什麼**：在資源/assets/Manifest 裡找敏感資料的實戰 checklist
  - **讀哪裡**：static analysis 裡 strings.xml、assets、Manifest 權限那幾段
  - **前提知識**：讀過本章格式，這頁給你「該搜哪些關鍵字」的實戰清單

下一章我們回到程式碼，把 Ch 6 學的「改 smali」玩到底。不是改個 boolean 那麼簡單——反轉 `if` 繞過驗證、改返回值破 VIP 開關、插 log 追資料流。下一章給你一整組 before→after 的 smali patch 對照，這是本 Part 的實戰高潮。

→ [Ch 10 Smali patch 實戰：繞校驗與改邏輯](./10-smali-patching.md)
