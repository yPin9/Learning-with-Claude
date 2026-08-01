# Ch 14 — 路徑穿越、zip slip 與不安全下載

> **目標**：把「檔名/路徑本身就是攻擊面」這件事徹底打通。核心是三類洞：**zip slip**（解壓縮一個外部 zip 時，entry name 含 `../` 就能把檔案寫到解壓目錄之外、覆蓋任意檔）、**下載檔案落地路徑注入**（把伺服器給的檔名直接當落地路徑）、以及 **Provider `openFile` / FileProvider 誤配造成的讀任意檔**（呼應 Ch 6）。這章你會用 **Python 實際構造惡意 zip entry、跑出 zip slip 的落地位置，並寫出正規化（canonicalization）防禦逐一驗證它擋得住哪些繞法**——親手看到「一個 `../` 能跑多遠、防禦線該畫在哪」。

> **環境**：zip 構造、路徑正規化、繞法比對，全部用 **Python 3.12 在本 repo 沙箱實跑**（純路徑/zip 演算法，不需 Android），輸出標「**實際輸出**」。App 端實際觸發（解壓、下載、Provider 讀檔）需 AVD/靶場，標「**未實測，理論預期行為**」並給驗證步驟。

## 為什麼需要這個？

前面幾類洞（元件、IPC、WebView）的攻擊面是「介面」——攻擊者呼叫你暴露的入口。這章的攻擊面更隱蔽：**是「檔案路徑」這個字串本身**。

App 無時無刻在處理外部來的「名字」：從伺服器下載更新包解壓、接收別人分享的 zip、下載一個檔案存到本地、透過 Provider 把檔案交給別的 App。每一次，只要 App **把外部給的名字直接當成本地路徑用、而沒有檢查**，攻擊者就能用 `../` 跳出預期目錄，把檔案寫到（或讀到）它不該碰的地方。

後果具體且嚴重：**覆蓋 App 私有目錄的設定檔改行為、覆蓋 `.so`/`.dex` 造成程式碼執行、覆蓋登入 token、或反向把別人的私有檔讀出來**。這是一類「開發者完全沒意識到路徑是攻擊面」的洞，掃描器常漏、但危害高，bug bounty 投報率好。而且它的原理跨語言跨平台通用——你在這章學到的正規化防禦，寫後端、寫桌面程式一樣用得上。

## 先建立直覺

一句話講清 path traversal：**當「輸出到哪」由不可信輸入決定，而你只做了拼接、沒做邊界檢查，`../` 就能把輸出點移到你的沙盒之外。**

最小的心智模型：你打算把檔案寫進 `/data/data/com.app/cache/unzip/`，你這樣拼路徑——

```
最終路徑 = 基底目錄 + "/" + 外部給的名字
```

如果外部名字是 `notes.txt`，最終路徑是 `.../cache/unzip/notes.txt`，安全。但如果外部名字是 `../../../../data/data/com.app/files/config.json`，拼完再正規化（把 `..` 消掉）就變成 `/data/data/com.app/files/config.json`——**跳出了 `unzip/`，落在 App 私有的 `files/` 裡**。你以為只會寫進解壓暫存區，其實寫進了關鍵設定。

```
你以為的沙盒                        攻擊者用 ../ 逃出去
┌─────────────────────────┐
│ cache/unzip/            │  entry="notes.txt"      → cache/unzip/notes.txt  ✅ 沙盒內
│   （你打算的解壓目錄）    │  entry="../../files/    → files/config.json     ❌ 逃出！
│                         │           config.json"
└─────────────────────────┘
        │ .. .. ..
        ▼ 一路往上跳
   /data/data/com.app/  ← 整個 App 私有目錄任你覆蓋
```

zip slip 就是這個模型套在「解壓縮」上：zip 檔案格式**允許 entry name 帶路徑分隔符與 `../`**（格式沒禁止），naive 的解壓程式碼 `File(destDir, entry.getName())` 直接拼，就中招。

## zip 格式為什麼放得下 `../`：親手構造一個惡意 zip

第一件要親眼確認的事：zip 的 entry name 就是一個字串欄位，格式**沒有**規定它不能含 `../`。我用 Python 造一個含惡意 entry 的 zip，讀回來看那個名字有沒有被保留（**實際輸出**，Python 3.12 實跑）：

```python
# buildzip.py —— 構造一個含惡意 entry 的 zip，證明 '../' 原封不動被保存
import zipfile, io
buf = io.BytesIO()
z = zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED)
z.writestr("readme.txt", "normal file")
z.writestr("../../../../data/data/com.victim/files/pwned.txt", "OVERWRITE")
z.close()
data = buf.getvalue()
print("zip size:", len(data), "magic:", data[:4])
z2 = zipfile.ZipFile(io.BytesIO(data))
for n in z2.namelist():
    print("  entry:", repr(n))
```

**實際輸出**：

```
zip size: 314 magic: b'PK\x03\x04'
  entry: 'readme.txt'
  entry: '../../../../data/data/com.victim/files/pwned.txt'
```

看第二行——那串 `../../../../data/...` **原封不動**存進 zip 也讀得回來。`magic: b'PK\x03\x04'` 確認這是合法 zip（跟 APK 同 magic，Ch 2 講過 APK 就是 zip）。**zip 格式不會幫你擋 `../`，防禦責任 100% 在解壓的程式碼。** 這就是 zip slip 的根：解壓者若信任 entry name，攻擊者早在打包時就把逃逸路徑寫死進去了。

## zip slip 的落地位置：naive 拼接 vs 正規化防禦

現在把「解壓程式碼怎麼決定落地路徑」實跑出來。我模擬 App 的解壓邏輯（基底目錄 + entry name），對五種 entry 各算兩件事：**naive 拼接會落在哪**、**正規化防禦判定放行或阻擋**（**實際輸出**，Python 3.12 實跑）：

```python
# zipslip.py —— naive 落地位置 vs 正規化邊界檢查
import posixpath

entries = [
    "notes.txt",                                                 # 正常
    "../../../../data/data/com.victim/files/config.json",        # 經典 ../ 逃逸
    "..\\..\\windows\\system32\\evil.dll",                       # 反斜線繞法（跨平台）
    "sub/../../escape.sh",                                       # 中段 .. 逃逸
    "/etc/crontab",                                              # 絕對路徑注入
]
DEST = "/data/data/com.victim/cache/unzip"

def safe_resolve(dest, name):
    dest = posixpath.normpath(dest)
    name = name.replace("\\", "/")                  # 反斜線也當分隔，堵跨平台繞法
    full = posixpath.normpath(posixpath.join(dest, name))
    # 核心檢查：正規化後必須仍在 dest 之內（== dest 或以 dest + '/' 開頭）
    ok = (full == dest or full.startswith(dest + "/"))
    return full, ok

print("dest =", DEST)
print("-" * 70)
for e in entries:
    naive = posixpath.normpath(posixpath.join(DEST, e.replace("\\","/")))
    full, ok = safe_resolve(DEST, e)
    print(f"entry = {e!r}")
    print(f"   naive 落地 = {naive}")
    print(f"   防禦判定   = {'允許' if ok else '阻擋(逃逸!)'}")
```

**實際輸出**：

```
dest = /data/data/com.victim/cache/unzip
----------------------------------------------------------------------
entry = 'notes.txt'
   naive 落地 = /data/data/com.victim/cache/unzip/notes.txt
   防禦判定   = 允許
entry = '../../../../data/data/com.victim/files/config.json'
   naive 落地 = /data/data/data/com.victim/files/config.json
   防禦判定   = 阻擋(逃逸!)
entry = '..\\..\\windows\\system32\\evil.dll'
   naive 落地 = /data/data/com.victim/windows/system32/evil.dll
   防禦判定   = 阻擋(逃逸!)
entry = 'sub/../../escape.sh'
   naive 落地 = /data/data/com.victim/cache/escape.sh
   防禦判定   = 阻擋(逃逸!)
entry = '/etc/crontab'
   naive 落地 = /etc/crontab
   防禦判定   = 阻擋(逃逸!)
```

逐行讀出五個重點：

1. **正常 entry** `notes.txt` 落在 `unzip/` 內，防禦放行——防禦不能誤殺正常檔案。
2. **經典 `../`**：naive 落地是 `/data/data/data/...`（跳出 `unzip/`，甚至位置怪異），防禦擋下。
3. **反斜線繞法**：`..\..\` 在只切 `/` 的解壓器上可能被當成單一檔名而繞過邊界檢查；我們的防禦先把 `\` 換成 `/` 才判斷，所以擋得住。**這是很多 zip slip 修補不完整的縫**——只防 `/` 不防 `\`。
4. **中段 `..`**：`sub/../../escape.sh` 正規化後跳到 `cache/`（`unzip/` 的上一層），證明 `..` 不一定在開頭，中段一樣能逃。**只檢查「開頭是不是 `../`」的防禦會漏掉這種。**
5. **絕對路徑**：`/etc/crontab` 直接無視基底目錄。naive `join` 遇到絕對路徑會**丟棄前面的基底**（POSIX `join` 的語意），落到系統路徑。防禦擋下。

**防禦的正確形狀**：不是「掃掉字串裡的 `../`」（黑名單，永遠有漏），而是 **先把最終路徑正規化（`normpath`／`realpath`），再確認結果仍以「基底目錄 + 分隔符」為前綴**（白名單邊界）。這招對上面五種繞法一次全擋，因為它檢查的是**正規化後的最終位置**，不管字串長什麼樣。

> **一個常見的不完整修補**：只 `if "../" in name: reject`。它擋不住反斜線（`..\`）、擋不住 URL 編碼（`%2e%2e%2f`，若名字經過解碼）、也擋不住絕對路徑。黑名單過濾字串是治標，**正規化後比對前綴才是治本**。

## 對映到 Android：這些洞長在哪

上面是通用路徑模型，落到真實 App，zip slip / path traversal 最常出現在這幾處：

### 1. 解壓更新包 / 外部 zip

App 從伺服器下載一個 zip（熱更新、資源包、匯入備份），用類似這樣的 Java 解壓：

```java
// 有漏洞的解壓（Java 端等價於上面的 naive）
ZipInputStream zis = new ZipInputStream(in);
ZipEntry ze;
while ((ze = zis.getNextEntry()) != null) {
    File out = new File(destDir, ze.getName());   // ← 直接拼，沒檢查！
    FileOutputStream fos = new FileOutputStream(out);
    // ... copy ...
}
```

`ze.getName()` 若含 `../`，`out` 就落在 `destDir` 之外。若攻擊者能控制那個 zip（中間人、或 App 從不可信來源接收 zip），就能覆蓋 App 私有檔。**修法**（Java 端對應我們 Python 防禦）：

```java
File out = new File(destDir, ze.getName());
String destCanon = destDir.getCanonicalPath();
String outCanon  = out.getCanonicalPath();               // 正規化，消掉 ..
if (!outCanon.startsWith(destCanon + File.separator)) {  // 邊界前綴檢查
    throw new SecurityException("zip slip: " + ze.getName());
}
```

`getCanonicalPath()` 是關鍵——它把 `..` 解掉、把 symlink 解掉，拿到真實最終路徑再比前綴。用 `getAbsolutePath()`（不解 `..`）是常見錯誤，擋不住。

### 2. 下載檔案落地路徑注入

App 下載檔案時，若**用伺服器回應的檔名（`Content-Disposition: filename=...` 或 URL 最後一段）當落地路徑**，而伺服器/URL 可被攻擊者影響，同樣中招：

```java
// 有漏洞：用伺服器給的 filename 當落地路徑
String filename = response.header("Content-Disposition"); // 攻擊者可塞 ../
File out = new File(downloadDir, filename);
```

若 `filename` 是 `../../databases/app.db`，下載內容就覆蓋了 App 的資料庫。**修法**：只取檔名的 basename（`new File(filename).getName()` 剝掉路徑），再走跟 zip slip 一樣的正規化+前綴檢查。

### 3. DownloadManager / MediaStore 的路徑

用系統 `DownloadManager` 時 `setDestinationInExternalPublicDir` 等 API 的相對路徑若拼入外部輸入，也可能跨出預期目錄（Android 10 分區儲存收緊了一部分，但 `targetSdk` 低的 App 仍走舊行為）。評估時看 `DownloadManager.Request` 的目的地怎麼組。

## FileProvider 誤配與 `openFile` 路徑穿越（呼應 Ch 6）

反過來的方向——**攻擊者不是寫檔，是把你的私有檔讀出來**——最常出在 ContentProvider 的 `openFile` 與 FileProvider 誤配。

### FileProvider 的 `<paths>` 配太寬

FileProvider 靠一份 XML 白名單決定「能分享哪些目錄」：

```xml
<!-- res/xml/file_paths.xml —— 危險的過寬配置 -->
<paths>
    <root-path name="root" path="/" />          <!-- 分享整個檔案系統根！ -->
    <files-path name="all" path="." />          <!-- 分享整個 files/ -->
</paths>
```

`root-path path="/"` 或 `external-path path="."` 這種**把範圍開到根或整個目錄**的配置，等於允許任何拿到 content URI 的 App 讀 App 私有檔（甚至系統檔）。正確做法是只暴露一個窄的子目錄（如 `files-path name="shared" path="shared_files/"`）。

### 自寫 Provider 的 `openFile` 直接用 URI 段當路徑

自訂 ContentProvider 若這樣實作 `openFile`：

```java
public ParcelFileDescriptor openFile(Uri uri, String mode) {
    String name = uri.getLastPathSegment();          // 攻擊者控制
    File f = new File(getFilesDir(), name);          // ← 沒檢查 ../
    return ParcelFileDescriptor.open(f, MODE_READ_ONLY);
}
```

攻擊者查詢 `content://com.app.provider/../../databases/app.db`，`uri.getLastPathSegment()` 或路徑拼接就可能跳出 `filesDir` 讀到資料庫。這就是 Ch 6 講的 Provider path traversal，在 `openFile` 這條路上的具體形態。修法一樣：正規化後前綴檢查。

> **未實測，理論預期行為**（本 repo 沙箱無 AVD）。在 AVD 上用 drozer 或 `adb shell content` 驗 FileProvider/openFile 穿越。**驗證指令**：

```bash
# 用 content provider 查詢帶穿越路徑的 URI（能讀到 = 有洞）
adb shell content read --uri "content://com.victim.fileprovider/root/../../../../data/data/com.victim/databases/app.db"
# drozer 掃 exported provider 並試 path traversal
run app.provider.info -a com.victim
run scanner.provider.traversal -a com.victim   # drozer 內建的穿越掃描
```

`scanner.provider.traversal` 能自動試一串 `../` 深度，回報哪些讀得到——這是把上面 Python 的邊界測試搬到真 App 上跑。

## 對比與取捨

| 防禦手法 | 擋得住 | 擋不住 | 評價 |
|---|---|---|---|
| `if "../" in name` 黑名單 | 最直白的 `../` | `..\`、URL 編碼、絕對路徑、中段 `..` | **不要單用**，治標 |
| `getAbsolutePath` 比前綴 | 開頭 `../` | 沒解 symlink、沒完全消 `..` 的邊角 | 不夠，用 canonical |
| **`getCanonicalPath`（Java）/ `realpath`（C）後比前綴** | `..`、`..\`、絕對路徑、中段 `..`、symlink | 極少數 TOCTOU 邊角 | **正解，本章防禦** |
| 只取 basename（`File(name).getName()`） | 所有路徑穿越（因為根本不用路徑段） | 若業務真需要子目錄結構就不適用 | 下載檔名場景的最佳解 |
| FileProvider 窄 `<paths>` | 讀方向的過度暴露 | 配錯還是會漏 | 讀方向必做 |

**取捨的一句話**：**能只用 basename 就別接受路徑**（下載/儲存檔名場景）；**必須保留目錄結構就走 canonical + 前綴白名單**（解壓場景）；**讀方向**（FileProvider）把 `<paths>` 收到最窄。黑名單過濾字串永遠是最後不得已、且要疊在白名單之上。

## 踩雷集錦

1. **只擋 `/` 不擋 `\`**：在解壓器只用 `/` 切路徑時，`..\..\` 會被當成一個怪檔名繞過「開頭 `../`」檢查（上面實跑的第 3 例）。防禦要先統一分隔符（`\`→`/`）再正規化。
2. **用 `getAbsolutePath` 而非 `getCanonicalPath`**：`getAbsolutePath` **不解 `..` 也不解 symlink**，`.../unzip/../files/x` 這種它看起來還在 `unzip` 前綴下、其實已逃逸。一定要 canonical。
3. **只檢查開頭有沒有 `../`**：中段 `sub/../../escape.sh`（第 4 例）逃得掉。正確是檢查**正規化後的最終路徑**，不是檢查原始字串。
4. **忘了絕對路徑會吃掉基底**：`join(base, "/etc/x")` 在 POSIX 直接回 `/etc/x`（第 5 例），基底被丟棄。canonical + 前綴檢查會擋，但若你自己做字串拼接要特別留意絕對路徑。
5. **FileProvider `<paths>` 開 `path="/"` 或 `"."`**：等於把整個目錄/根暴露給任何拿到 URI 的 App。永遠只暴露最窄的子目錄。
6. **symlink 沒考慮**：攻擊者若能先在解壓目錄放一個指向外部的 symlink，再讓後續 entry 寫「該 symlink 名」，寫入會跟著 link 跑到外面。`getCanonicalPath` 會解 symlink 所以擋得住，這也是為什麼一定用 canonical 而非 absolute。

## 進階：再往深一層

- **TOCTOU（time-of-check to time-of-use）**：你 `getCanonicalPath` 檢查通過後、真正 `open` 之前，若攻擊者能在這個時間縫把路徑上的某段換成 symlink，檢查就白做了。徹底防要在**開檔時**用 `O_NOFOLLOW`（native）或 `Files.newOutputStream(..., LinkOption.NOFOLLOW_LINKS)`，而不只是事前字串檢查。多數 App 場景 canonical 檢查已夠，但高安全場景要知道這層。
- **zip 炸彈（zip bomb）**：跟 zip slip 同屬「不可信 zip」攻擊面的另一支——一個很小的 zip 解壓後膨脹到耗盡儲存/記憶體。防禦是解壓時限制**總輸出大小**與**單檔大小**，超過就中止。評估解壓邏輯時一起看有沒有大小上限。
- **符號連結攻擊在分享場景**：接收別人透過 `Intent`/`content://` 分享來的檔案時，若不驗證來源 URI 而直接 `openInputStream` 再落地，攻擊者可用 content URI 指向你不預期的檔（Ch 4 intent、Ch 6 provider 的合流）。落地一律走正規化。
- **Android 的 Scoped Storage 邊界**：Android 10+ 的分區儲存把 App 對外部儲存的自由度收緊，一定程度緩解了外部儲存的路徑穿越，但 `targetSdk` < 29 或用 `requestLegacyExternalStorage` 的 App 仍走舊模型。評估時先確認 App 的儲存模型再判斷風險。

## 動手練習

1. 把本章 `zipslip.py` 跑起來，然後**新增你自己的繞法** entry 試著突破防禦：例如 `....//....//x`（雙寫繞單次過濾）、`%2e%2e%2ftest`（若名字會被 URL 解碼）、開頭多個 `/`。確認我們的「canonical 後比前綴」防禦對這些是不是都擋得住；找出任何一個能繞過的，就代表防禦還不完整，補上。
2. 找 AndroGoat/DIVA 裡有「匯入/解壓/下載」功能的靶，或自建一個 naive 解壓的小 App，用 `buildzip.py` 造一個 entry 為 `../../files/pwned.txt` 的惡意 zip，餵給它解壓，`adb shell run-as com.app ls files/` 看 `pwned.txt` 有沒有出現在解壓目錄之外——親眼看 zip slip 覆蓋成功。
3. 在 AVD 上對一個 exported FileProvider 或自寫 Provider，用 `adb shell content read --uri "content://.../../../databases/x.db"` 試路徑穿越；再把 `<paths>` 從 `path="/"` 改窄到單一子目錄，重試，確認穿越被擋——體會「窄化 `<paths>` = 讀方向的邊界白名單」。

## 本章重點整理

- path traversal 的本質：**輸出/讀取位置由不可信輸入決定，只拼接沒做邊界檢查，`../` 就能逃出沙盒**。zip slip 是它套在「解壓」上的形態，因為 zip 格式允許 entry name 含 `../`（本章 Python 實測保存不變）。
- **正解防禦是白名單邊界**：把最終路徑**正規化**（Java `getCanonicalPath` / C `realpath`），再確認結果仍以「基底目錄 + 分隔符」為前綴。這對 `../`、`..\`、中段 `..`、絕對路徑、symlink 一次全擋（本章實測）。
- **黑名單過濾字串永遠有漏**：只擋 `/` 漏 `\`、只擋開頭 `../` 漏中段、忘了絕對路徑吃掉基底——別單用字串過濾。
- 下載檔名場景**能只用 basename 就別接受路徑**；讀方向的 **FileProvider `<paths>` 收到最窄**，別開 `path="/"`。
- Android 對應點：解壓更新包、下載落地、DownloadManager 路徑、FileProvider 誤配、自寫 `openFile`（呼應 Ch 6）。

## 自我檢核

- [ ] 不看筆記，能解釋為什麼 zip 格式「允許」`../` 而防禦責任在解壓程式碼
- [ ] 能說出正確防禦的兩步（正規化 → 前綴白名單檢查），以及為什麼 `getAbsolutePath` 不夠、要 `getCanonicalPath`
- [ ] 能舉出三種黑名單過濾會漏掉的繞法（`..\`、中段 `..`、絕對路徑），並解釋白名單為什麼一次全擋
- [ ] 知道下載檔名場景該用 basename、讀方向該窄化 FileProvider `<paths>`
- [ ] 跑過 `zipslip.py`，親眼看到五種 entry 的落地位置與防禦判定
- [ ] 知道 TOCTOU 與 symlink 為什麼要在「開檔時」而非只在「檢查時」防

## 延伸閱讀

- **[Snyk — Zip Slip 漏洞研究](https://security.snyk.io/research/zip-slip-vulnerability)**
  - **讀哪裡**：漏洞原理與「哪些語言/函式庫中招」清單，以及正確修補範例
  - **和本章的關聯**：這是把 zip slip 系統化命名並揭露大量函式庫受影響的原始研究；本章 Java 修法與它的建議一致
- **[OWASP MASTG — Testing Local Storage / File Handling (MASVS-STORAGE)](https://mas.owasp.org/MASTG/tests/android/MASVS-STORAGE/)**
  - **讀哪裡**：檔案處理與 Provider 檔案暴露相關測試案例
  - **和本章的關聯**：把 zip slip / openFile 穿越對齊 MASVS-STORAGE 需求編號，報告可直接引用測試流程
- **[Android 開發者文件 — FileProvider](https://developer.android.com/reference/androidx/core/content/FileProvider)**
  - **讀哪裡**：`<paths>` 各種 path 元素的語意與 `getUriForFile` 的授權模型
  - **為什麼值得讀**：本章「窄化 `<paths>`」的權威依據；看清楚 `root-path`/`files-path`/`external-path` 各自暴露什麼，才知道配過寬有多危險
- **[CWE-22: Path Traversal](https://cwe.mitre.org/data/definitions/22.html)**
  - **讀哪裡**：Mitigations 那節與各種繞法變體（編碼、`..\`、絕對路徑）
  - **前提知識**：讀過本章，這頁給你更完整的繞法清單與跨語言的通用防禦原則，寫報告引 CWE 編號時用

下一章我們把前面十四章手動挖出來的能力**自動化**：MobSF 一鍵靜動態掃、semgrep/mobsfscan 寫規則批量抓、apkleaks 挖 secret，再把一堆自動化輸出**去誤報（triage）**、寫成一份能拿去 bug bounty 或評估的報告。工具會產出一堆「疑似」，把它們變成「確定」並寫清楚，才是這一整條工作流的終點。

→ [Ch 15 自動化掃描與報告撰寫](./15-automation-reporting.md)
