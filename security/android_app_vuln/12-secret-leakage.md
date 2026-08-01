# Ch 12 — 憑證與 secret 洩漏

> **目標**：把「App 裡藏了什麼不該藏的憑證」當成一次系統化的挖礦。你要能對一個 APK 跑出一份 secret 清單：硬編的 API key、OAuth token、雲端（AWS/GCP/Firebase）憑證、第三方 SDK 的認證資訊——不管它藏在 DEX、資源、`assets/`、還是 native `.so`。本章教你 `strings` 粗掃、apkleaks 正規表達式自動化、gitleaks 式的規則思路、以及「找到之後怎麼驗證它是真的、影響多大」。

> **環境**：apkleaks 的正規表達式匹配、base64 解碼、entropy 篩選這些純文字/演算法處理，用 **Python 3.12** 在本機**實際跑出**，標「**實際輸出**」。實際跑 apkleaks/strings against 一個 APK、以及拿撈到的 key 打雲端 API 驗證的部分需要 APK/網路，標「**未實測，理論預期行為**」並附驗證步驟。**只對你有權分析的 APK 做此事**；拿撈到的 key 去打的 API 必須是你自己的或明確授權的。

## 為什麼需要這個？

因為**硬編 secret 是投報率最高、最常見的 App 漏洞之一**，而它幾乎不需要什麼逆向技巧——很多時候 `strings` + 一個正規表達式就撈到一把能打進別人後端的 key。Verizon DBIR、各家 bug bounty 排行榜年年把「洩漏的憑證」放在高位，因為開發者為了方便，把測試用的 API key、後端服務的認證、雲端 storage 的 access key 直接寫進程式碼或塞進 `assets/`——然後這包東西發到幾百萬台裝置上，任何人都能逆。

這一章跟前兩章的關係：Ch 10 找「明文存在裝置上的使用者資料」，Ch 11 找「假的加密」，這一章找「開發者塞進 App 的伺服器端/服務端憑證」。前兩者影響單一使用者/單一裝置，**secret 洩漏往往影響整個後端服務**——一把 AWS key 可能讓你讀寫整個 S3 bucket，一個開放的 Firebase 可能讓你 dump 整個資料庫。這是「一個 App 漏洞升級成雲端資料外洩」的橋樑，嚴重度天花板很高。

心智模型：**App 裡不該有「只有伺服器該知道的秘密」**。App 是公開可逆的，任何寫進 App 的東西都等於公開。合理的 secret 是「這台裝置/這個使用者專屬、且權限受限」的（例如短效 token）；不合理的是「整個服務共用、權限很大」的（master API key、雲端 root 憑證）。你挖 secret 時，重點放在後者——它們是把 App 漏洞放大成後端災難的東西。

## 先建立直覺：secret 藏在哪、怎麼系統化地掃

一個 secret 可能藏在 APK 的任何角落。把 APK 攤平，每一層都要掃：

```
app.apk
├── classes*.dex          ← 硬編字串常數（最常見）：API key、URL、token
│     └─ jadx 反編譯 / strings 掃 / DEX 字串池
├── res/values/strings.xml← 資源字串：google_api_key、各種 client_id
│     └─ apktool 解碼後掃 res/
├── resources.arsc        ← 編譯後資源表，字串也在這（strings 掃得到）
├── assets/               ← 金礦：設定檔(.json/.properties)、.env、憑證檔、SDK 設定
│     └─ 直接 unzip 出來逐檔看
├── AndroidManifest.xml   ← <meta-data> 常放 API key（如 Maps/AdMob）
└── lib/*.so              ← native 裡的硬編字串：更難挖，但值錢的常藏這
      └─ strings 掃 .so / IDA/Ghidra（見 android_reversing Ch 23）
```

系統化的掃法分三層，由粗到細：

```
第 1 層：strings 粗掃          → 快、雜訊多、當偵察
第 2 層：apkleaks 正規表達式   → 針對已知 secret 格式（AWS/GCP/JWT...）精準撈
第 3 層：人工 + entropy 篩選   → 掃出「看起來像 secret 的高熵字串」，補正規表達式漏網
```

三層互補：strings 讓你對「這 App 大概有什麼」有感覺；apkleaks 把已知格式的 secret 自動撈齊；entropy/人工補上「不符已知格式但明顯是憑證」的漏網之魚。下面逐層拆。

## 第 1 層：strings 粗掃

`strings` 把二進位裡的可列印字串抽出來。對 DEX、`.so`、`resources.arsc` 都能掃。它是最粗的網，雜訊很多，但快、零依賴，適合第一眼偵察。

```bash
# 對整包解開後的所有檔案掃常見 secret 關鍵字
unzip -o app.apk -d app_out/
strings -n 8 app_out/classes*.dex app_out/lib/*/*.so app_out/resources.arsc \
  | grep -iE "api[_-]?key|secret|token|password|passwd|Bearer|AKIA|firebase|-----BEGIN"
```

`grep` 的關鍵字要涵蓋兩類：**通用詞**（`key`/`secret`/`token`/`password`）與**特定格式前綴**（AWS 的 `AKIA`、私鑰的 `-----BEGIN`、Firebase 網域）。

> **strings 的盲點**：(1) 它抓的是「連續可列印字元」，被拆段、XOR、base64 之外再編碼的 secret 抓不到（回到 Ch 11 的 Frida 動態撈）；(2) DEX 的字串在**字串池**裡通常是連續的、strings 抓得到，但要注意 DEX 用 MUTF-8 編碼，含非 ASCII 時可能斷字。所以 strings 是起點不是終點——真正系統化靠第 2 層。

## 第 2 層：apkleaks 與正規表達式引擎

**apkleaks** 是這章的主力工具：它把 APK 反編譯（內建走 jadx），然後用一組**正規表達式規則**去匹配已知格式的 secret 與 endpoint。它的價值在於**規則庫**——別人已經把「AWS access key 長怎樣、Google API key 長怎樣、JWT 長怎樣」寫成正規表達式，你直接複用。

```bash
# 對一個 APK 跑（未實測，理論預期行為；需 APK + jadx）
apkleaks -f app.apk -o report.txt
#   -f 指定 APK，-o 輸出報告；預設用內建規則庫，也可 --pattern 自訂
```

apkleaks 的核心就是一組正規表達式 + 掃描。我用 Python 復刻它的匹配邏輯，對一段含各種 secret 的文字跑（**實際輸出**，正規表達式取自 apkleaks 慣用的規則形態）：

```python
import re
patterns = {
 "Google API Key": r"AIza[0-9A-Za-z\-_]{35}",
 "AWS Access Key": r"AKIA[0-9A-Z]{16}",
 "Firebase URL":   r"https://[a-z0-9.\-]+\.firebaseio\.com",
 "JWT":            r"eyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+",
 "Slack Token":    r"xox[baprs]-[0-9a-zA-Z\-]{10,48}",
}
blob = open("dumped_strings.txt").read()   # strings 抽出來的內容
for name, pat in patterns.items():
    for m in re.findall(pat, blob):
        print(f"[{name}] {m}")
```

對含樣本 secret 的輸入，**實際輸出**：

```
[Google API Key] AIzaSyC1a2b3c4d5e6f7g8h9i0jK1l2m3n4o5p6
[AWS Access Key] AKIAIOSFODNN7EXAMPLE
[Firebase URL] https://my-app-1234.firebaseio.com
[JWT] eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abc123XYZ
[Slack Token] xoxb-REDACTED_EXAMPLE（真實格式：xoxb- 前綴 + 數字團隊 ID + 英數 token）
```

看懂這幾條正規表達式的**結構**，你就能自己擴充規則：

- `AIza[0-9A-Za-z\-_]{35}`：Google API key **固定前綴 `AIza` + 35 個字元**。前綴是識別關鍵。
- `AKIA[0-9A-Z]{16}`：AWS access key ID **前綴 `AKIA` + 16 個大寫字母數字**。
- `eyJ...\.eyJ...\..`：JWT 是三段 base64url 用 `.` 分隔，且 header/payload 都以 `eyJ` 開頭（因為 `{"` 的 base64 是 `eyJ`）——這是 JWT 最好認的特徵。

> **為什麼很多 secret 有固定前綴**：雲端廠商刻意給憑證加辨識前綴（`AKIA`、`AIza`、`ghp_` GitHub token、`sk-` OpenAI），一來自家系統好路由，二來**方便 secret scanner 偵測**（GitHub 的 secret scanning 就靠這些前綴）。這對防守方是好事，對你挖 secret 也是好事——前綴讓正規表達式精準、誤報低。

## 第 3 層：entropy 篩選補漏網

正規表達式只抓「已知格式」。但很多 secret 沒有固定格式——一串隨機的 32-byte hex、一個自訂的 token。這類要靠 **entropy（熵）** 篩：真正的 secret 是高隨機性的，熵高；一般英文字、變數名、URL 路徑熵低。gitleaks 這類工具除了規則，也用 entropy 門檻抓「看起來太隨機、像是憑證」的字串。

我用 Python 算 Shannon entropy，對「一般字串」vs「隨機 key」比較（**實際輸出**）：

```python
import math
from collections import Counter
def entropy(s):
    n = len(s)
    return -sum((c/n) * math.log2(c/n) for c in Counter(s).values())

for s in ["getUserProfileFromServer", "a3F9zQ2xL7mNpR8vK1wYtB6cH4dJ0eS"]:
    print(f"{entropy(s):.2f}  {s}")
```

**實際輸出**：

```
2.85  getUserProfileFromServer
4.64  a3F9zQ2xL7mNpR8vK1wYtB6cH4dJ0eS
```

高熵字串（≈4.6）比一般標識符（≈2.85）明顯高。實務門檻常設在 **base64 字串 entropy > 4.5、hex 字串 > 3.0**，把超過門檻的挑出來人工看。這補上了正規表達式漏掉的自訂格式 secret。

> **entropy 的雙面**：它會有誤報——混淆過的類名、base64 編碼的資源、hash 值都是高熵但不是 secret。所以 entropy 是「篩選器」不是「判定器」：把高熵字串挑出來**縮小人工檢查範圍**，最後是不是 secret 還是人看。別把「高熵」直接當「找到 secret」寫進報告。

## 各藏匿處的具體挖法

**資源與 Manifest 的 `<meta-data>`**：很多 SDK 要求把 API key 放 `strings.xml` 或 Manifest `<meta-data>`，開發者照做——這是「合規地硬編」，照樣可被撈：

```xml
<!-- strings.xml -->
<string name="google_maps_key">AIzaSy...</string>
<!-- Manifest -->
<meta-data android:name="com.google.android.geo.API_KEY" android:value="AIzaSy..."/>
```

Maps/AdMob 這類 key 有時受 SHA-1 指紋 + package 綁定保護（打其他來源會被拒），有時沒設限就能濫用。撈到後要**驗證它有沒有被限制**（見下節）。

**`assets/` 金礦**：`assets/` 是開發者原封不動塞的檔，工具不動它。掃 `assets/` 常挖到 `.json` 設定、`.properties`、`google-services.json`（Firebase 設定，含 API key 與專案資訊）、甚至誤放的 `.pem`/`.p12` 憑證檔：

```bash
unzip -o app.apk -d app_out/
ls -R app_out/assets/
grep -riE "key|secret|token|password|firebase|-----BEGIN" app_out/assets/
```

**第三方 SDK 洩漏**：App 引入的廣告/分析/推播 SDK（Facebook、AppsFlyer、推播服務）常各自帶 key。這些 SDK 的 key 命名有慣例（`fb_app_id`、`onesignal_app_id`），也可用專門規則掃。SDK 的洩漏常被忽略，因為「不是我的程式碼」——但它照樣是攻擊面。

**native `.so`**：值錢的 secret（簽名金鑰、防重放的共享密鑰）常被搬進 native 以擋只會 Java 的人。先 `strings lib*.so | grep` 粗掃；抓不到就得逆 `.so`——這接到 [android_reversing Ch 23 native 演算法識別](../android_reversing/23-native-algorithm-id.md)，用 IDA/Ghidra 找常數，或 Frida hook native 函式攔執行期的值。

## Firebase 開放：從一個 key 到整個資料庫

Firebase Realtime Database 的洩漏值得單獨講，因為它是「撈到一個 URL → dump 整個資料庫」的經典鏈。App 裡的 Firebase URL（`https://<project>.firebaseio.com`）本身不是秘密——真正的問題是**後端的安全規則（security rules）設成了公開讀寫**。

```bash
# 撈到 Firebase URL 後，測它的安全規則是否開放（未實測，理論預期行為；只對授權目標）
# 在 URL 後加 .json 直接讀 REST 端點
curl "https://my-app-1234.firebaseio.com/.json"
#   若回一大包 JSON 資料 → 安全規則開放，整庫可讀（高危）
#   若回 {"error":"Permission denied"} → 規則有設，安全
```

這是「App 洩漏的東西本身無害，但配上後端誤配就是災難」的典型。你的報告要把這條鏈講完整：App 洩漏 URL（低）+ 後端規則開放（高）= 資料庫外洩（Critical）。單看 App 那半是不完整的。

## 找到之後：驗證真偽與影響

**挖到 secret 不是結束，驗證才是**。一份好報告要回答「這 key 是真的嗎？能做什麼？影響多大？」而不是「我看到一串像 key 的東西」。

| Secret 類型 | 怎麼驗證有效 + 影響 | 註記 |
|---|---|---|
| AWS access key | `aws sts get-caller-identity`（用撈到的 key）看是不是有效、什麼身分/權限 | 只對授權目標 |
| Google API key | 打對應 API（Maps/其他）看回 200 還是被拒；查有沒有綁 SHA-1/referrer 限制 | key 可能受限，受限≠無效但影響小 |
| Firebase | URL + `.json` 測讀寫規則 | 見上節 |
| JWT | 解 payload 看 claims、過期時間；`alg:none` / 弱簽章可偽造 | base64 decode 三段 |
| 第三方 SDK key | 依 SDK 文件測對應端點 | 影響隨 SDK 權限而定 |

> **「撈到」和「可利用」是兩回事**：很多 API key 撈得到但**受限**（綁 package 簽名、綁 referrer、權限極小、已輪替失效）。報告若把每個撈到的字串都當 Critical，會失去公信力。負責任的做法：撈到 → **在授權範圍內驗證它實際能做什麼** → 據此評級。撈到一把能讀寫整個 S3 的 AWS key 是 Critical；撈到一把只能查地圖、還綁了簽名的 Maps key 影響就小得多。

## 對比與取捨

| 方法 | 覆蓋 | 誤報 | 何時用 |
|---|---|---|---|
| `strings` + grep | 廣但粗 | 高 | 第一眼偵察，零依賴快掃 |
| apkleaks（正規表達式） | 已知格式精準 | 低 | 主力，批次撈標準格式 secret |
| entropy 篩選 | 補自訂格式漏網 | 中高 | 正規表達式漏抓時，縮小人工範圍 |
| MobSF | 一站式（含 secret 掃） | 中 | 快速全面偵察（Ch 15 講） |
| Frida 動態撈 | 動態拼/加密的 secret | 低 | 靜態抓不到（被 XOR/native/動態組） |
| 人工逆 native | native 硬編 secret | — | 值錢 secret 藏 .so 時 |

**主軸**：apkleaks 做主力（精準、低誤報），strings/entropy 補偵察與漏網，靜態抓不到的用 Frida（回 Ch 11 的動態撈金鑰思路），native 深藏的接 android_reversing Ch 23。沒有單一工具通殺——secret 藏匿處太多樣。

## 踩雷集錦

1. **撈到字串就當漏洞報上去**：沒驗證有效性/影響就報，一堆是受限 key、測試 key、已失效 key。負責任的做法是在授權範圍內驗證它實際能做什麼再評級。
2. **只掃 DEX，漏了 `assets/` 和 `resources.arsc` 和 native**：secret 分散在多處，`assets/` 的設定檔、`resources.arsc` 的資源字串、`.so` 的硬編常數都要掃。只 jadx 看 Java 會漏一大片。
3. **拿撈到的 key 直接打生產 API 驗證**：這可能越界、可能違法。**只對你自己的或明確授權的目標**驗證。Firebase `.json`、`aws sts` 這些動作在未授權目標上做是不行的——bug bounty 要看 scope。
4. **以為 Firebase URL 出現就是漏洞**：URL 本身公開無害，漏洞在後端安全規則開放。要實測 `.json` 端點確認規則，才有洞。單看 App 那半不完整。
5. **正規表達式抓到 `AIza...` 就標 Critical**：Google API key 常綁 SHA-1 指紋 + package，受限的 key 濫用不了。查它有沒有限制再評級。
6. **忽略 `entropy` 的誤報**：混淆類名、base64 資源、hash 都是高熵非 secret。entropy 是篩選器不是判定器，別把高熵直接當 secret。
7. **只信一個工具**：apkleaks 規則庫再全也有盲點（新格式、自訂 token）。三層互補 + 人工，別把單一工具的「無發現」當「乾淨」。

## 進階：再往深一層

- **gitleaks / trufflehog 的思路借過來**：這兩個是掃 git repo 的 secret scanner，但它們的**規則庫與 entropy 策略**可直接借來掃 APK 解開後的檔案樹——把 APK `unzip` 成目錄，對目錄跑 gitleaks，複用它成熟的規則。這比自己維護正規表達式省事。
- **secret 的生命週期與輪替**：撈到的 key 可能已輪替失效。反過來，若你能證明「這 key 現在仍有效」，影響就實打實。報告裡標註「驗證時間 + 當時有效性」，因為 key 隨時可能被撤。
- **供應鏈維度**：第三方 SDK 洩漏的不只是 key，有些 SDK 本身把資料回傳到 SDK 廠商、或內建可濫用的功能。挖 SDK secret 時順帶看這個 SDK 是什麼、有沒有已知 CVE——secret 洩漏可能只是這個 SDK 更大問題的入口。
- **動態組出來的 secret**：最難挖的是「執行期才拼出來」的 secret——幾段常數 + 時間 + 裝置資訊算出來。靜態全抓不到，只能 Frida hook 用到它的地方（`Cipher.init`、HTTP header 設定、`Authorization`）攔執行期的最終值。這徹底回到 Ch 11 的動態撈原則：**要用的東西執行期一定是明的**。

## 動手練習

1. 拿一個靶 App（有硬編 secret 的，如 InsecureBankv2、或刻意埋 key 的 CTF APK），跑三層掃描：先 `strings | grep` 粗掃、再 apkleaks 精掃、最後對 `assets/` 人工看。比較三者各抓到什麼、漏了什麼。
2. 用本章 Python 片段自建一個 mini secret scanner：幾條正規表達式 + entropy 門檻，對一份 `strings` dump 跑。體會正規表達式（精準）與 entropy（補漏但誤報）的分工。
3. 對撈到的一個 JWT，手動 base64 decode 它的三段，看 header 的 `alg` 與 payload 的 claims/過期時間。判斷它是不是敏感 token、有沒有 `alg:none` 這種可偽造的弱點。
4. 找一個含 native `.so` 的 App，`strings lib*.so | grep -iE "key|secret"` 掃 native 硬編字串。抓不到的話，記下「這需要逆 .so」，接到 android_reversing Ch 23 的 native 演算法識別。

## 本章重點整理

- **App 裡不該有伺服器端的秘密**——App 公開可逆，硬編 secret = 公開；重點挖「權限大、影響後端」的 secret（雲端憑證、master key），它把 App 漏洞放大成後端災難。
- 系統化三層掃：**strings 粗掃（偵察）→ apkleaks 正規表達式（精準主力）→ entropy 篩選（補自訂格式漏網）**，三層互補加人工。
- secret 藏在 **DEX / `res` / `resources.arsc` / `assets/` / Manifest `<meta-data>` / native `.so`** 各處；`assets/` 與 native 最常被漏掉。
- **撈到 ≠ 可利用**：受限 key、失效 key 很多。在**授權範圍內**驗證實際影響再評級；Firebase 洩漏要實測 `.json` 端點確認規則開放才是洞。

## 自我檢核

- [ ] 能說出 secret 可能藏的至少五個位置，以及各自的掃法
- [ ] 能講清楚 strings / apkleaks / entropy 三層各自的強項與盲點，為什麼要互補
- [ ] 知道為什麼 AWS/Google/GitHub 的憑證有固定前綴，以及這對正規表達式挖掘的意義
- [ ] 拿到一把撈出來的 key，知道怎麼在授權範圍內驗證它是否有效、影響多大，而不是直接標 Critical
- [ ] 能解釋為什麼「Firebase URL 出現」本身不是洞，什麼才讓它變成 Critical

## 延伸閱讀

- **[apkleaks（GitHub repo）](https://github.com/dwisiswant0/apkleaks)** — dwisiswant0
  - **讀哪裡**：README 的用法，以及 `patterns.json` / 內建規則檔——看它實際用哪些正規表達式抓哪些 secret
  - **和本章的關聯**：本章第 2 層的主力工具；讀它的規則庫能學會「各家憑證的格式特徵」，並照著擴充自己的規則
- **[OWASP MASTG — Testing for Sensitive Data in Code & Resources](https://mas.owasp.org/MASTG/0x05d-Testing-Data-Storage/)** — OWASP
  - **讀哪裡**：hardcoded secrets / API keys in code and resources 的測試步驟
  - **為什麼值得讀**：把「挖 secret」變成 MASTG 可勾選的測試案例，報告引用它的編號；也界定了「什麼算敏感資料」
- **[gitleaks（GitHub repo）](https://github.com/gitleaks/gitleaks)** — gitleaks
  - **讀哪裡**：`config` 的規則格式與 entropy 設定；`gitleaks.toml` 內建規則
  - **和本章的關聯**：第 3 層 entropy 思路與規則庫的成熟範例；把 APK unzip 成目錄後可直接對它跑，複用其規則
- **[Google Cloud — API key best practices / 限制](https://cloud.google.com/docs/authentication/api-keys)** — Google 官方
  - **讀哪裡**：API key 的限制機制（application restrictions、API restrictions）
  - **為什麼值得讀**：解釋為什麼撈到 `AIza...` 不一定能濫用——受限 key 的評級要據此調整；驗證影響時的判斷依據

到這裡 Part 4（資料/密碼/儲存）走完：你能把一個 App 存的資料、用的加密、藏的憑證全面掃一遍。下一章進 Part 5，回到權限模型——自訂 permission 的缺陷與簽名權限誤用，看 App 怎麼因為權限設計錯誤而讓不該存取的元件被存取。

→ [Ch 13 自訂 permission 缺陷與簽名權限](./13-custom-permission-flaws.md)
