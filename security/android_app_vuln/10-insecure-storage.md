# Ch 10 — 不安全儲存

> **目標**：把一個 App「存在本地的東西」當成一個完整攻擊面來掃。你要能回答：這個 App 把 token、密碼、PII 存在哪？誰讀得到？拿到裝置（或只是拿到一次 `adb`）能撈出多少？本章把 SharedPreferences 明文、SQLite 明文、外部儲存全域可讀、`allowBackup` 被 adb backup 撈走、logcat / 剪貼簿 / 截圖快取這些洩漏管道逐一拆開，每一個都給你「怎麼確認」與「怎麼撈」的具體動作。

> **環境**：本章的儲存路徑、`adb backup`、`run-as`、logcat 行為以 **AVD（Android 13 / API 33，google_apis，可 root）** 為準。凡是需要在真機/AVD 上跑的 `adb` 指令與裝置行為，標「**未實測，理論預期行為**」並附驗證步驟；純檔案/演算法（base64、adb backup 檔頭格式）能在本機驗的標「**實際輸出**」。Android 版本差異（scoped storage 從 API 29 改變外部儲存、`allowBackup` 預設值）在文中標注。

## 為什麼需要這個？

因為**資料是 App 最值錢、也最常被存錯地方的東西**。前面幾章（元件、WebView、網路）打的是「別人能不能觸發你的功能」；這章打的是「別人能不能讀到你存的資料」。這兩個攻擊面性質完全不同——儲存漏洞不需要你構造精巧的 Intent，很多時候只要一條 `adb` 指令、一個能讀外部儲存的第二個 App、或一份 `adb backup` 就把 token 撈走了。

而且它在 bug bounty 與評估報告裡佔比極高：MASVS 的 **MASVS-STORAGE** 整個分類就是講這個。原因很現實——開發者用 `SharedPreferences` 存 token 是因為它三行就寫完，用 SQLite 存資料是因為 Room 很好用，沒人第一時間想到「這檔案在裝置上是明文、root 或備份就能讀」。你的工作就是把這些「順手存錯地方」的東西系統化地挖出來。

儲存漏洞的可利用性有一條光譜，你要先分清楚**威脅模型**，否則會把「需要 root 才讀得到」誇大成「隨便一個 App 都能讀」：

```
威脅模型（誰能讀到你存的東西）        現實中要滿足什麼
────────────────────────────────────────────────────────
① 同裝置的其他 App              資料放在全域可讀的位置（舊式外部儲存 MODE_WORLD_READABLE）
② 實體接觸 + adb（未 root）      App debuggable，或 allowBackup=true
③ 實體接觸 + root / 已 root 機   幾乎所有本地檔案都讀得到（含 app 私有目錄）
④ 惡意 App 拿到 log / 剪貼簿     App 把敏感資料寫進 logcat / 放進剪貼簿
```

同一個「token 存明文」的問題，在威脅模型 ③（root）下永遠成立、但影響有限（要先拿到 root）；在 ① 或 ② 下成立，才是高風險的洞。寫報告時把這條分清楚，你的評級才站得住。

## 先建立直覺：App 的資料住在哪

一個 App 能寫檔的地方分兩大塊——**私有目錄**（沙箱內，別的 App 預設讀不到）與**外部/共享儲存**（歷史上全域可讀）。心智模型：

```
/data/data/<pkg>/                     ← App 私有目錄（內部儲存），SELinux + UID 隔離
  ├── shared_prefs/*.xml              ← SharedPreferences：明文 XML
  ├── databases/*.db                  ← SQLite：明文 binary（除非 SQLCipher）
  ├── files/                          ← openFileOutput() 寫的檔
  └── cache/                          ← 快取（含 WebView、截圖縮圖）
        │
        │ 預設別的 App 讀不到（各 App 一個 UID，沙箱隔離）
        │ 但：root 讀得到；debuggable/allowBackup 讓 adb 讀得到
        ▼
/sdcard/  = /storage/emulated/0/      ← 外部/共享儲存
  ├── Android/data/<pkg>/             ← App 專屬外部目錄（API 29+ 收緊）
  └── Download/ DCIM/ ...             ← 共享區：舊 API 全域可讀寫
```

兩句話刻進腦子：

1. **私有目錄不是加密**。它靠的是 **Linux UID 隔離 + SELinux**——每個 App 一個 UID，A 讀不到 B 的目錄。但這是「存取控制」不是「加密」，一旦你**繞過存取控制**（root、`run-as` debuggable app、`adb backup`），裡面的 `.xml` 和 `.db` 就是明文攤在那。
2. **外部儲存歷史上是全域可讀的**。Android 10（API 29）引入 **scoped storage** 之前，任何有 `READ_EXTERNAL_STORAGE` 的 App 都能讀整個 `/sdcard`。所以「把敏感檔寫進 `/sdcard`」是經典洞——即使今天，`getExternalFilesDir()` 的東西在 `adb`/root 下照樣能撈，而且很多老 App 還在用舊寫法。

下面逐管道拆。

## 管道一：SharedPreferences 明文

`SharedPreferences` 是 Android 最常見的 key-value 儲存，開發者拿它存登入狀態、token、開關、甚至密碼。它的底層就是一個 **XML 檔**，位置固定在 `/data/data/<pkg>/shared_prefs/<name>.xml`，**內容是明文**。

典型的漏洞程式碼（jadx 反編譯出來長這樣）：

```java
SharedPreferences sp = getSharedPreferences("auth", MODE_PRIVATE);
sp.edit()
  .putString("access_token", token)      // ← 明文寫入
  .putString("password", pwd)            // ← 更糟：直接存密碼
  .apply();
```

存進去的 XML 檔（**未實測，理論預期行為**；格式依 Android SharedPreferences 標準）：

```xml
<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
<map>
    <string name="access_token">eyJhbGciOiJIUzI1NiJ9.eyJ1aWQiOjQyfQ.sig</string>
    <string name="password">Sup3rSecret!</string>
</map>
```

**怎麼撈**（三種威脅模型，難度遞增）：

```bash
# 模型③ root：直接讀（AVD google_apis 可 adb root）
adb root
adb shell cat /data/data/com.example.target/shared_prefs/auth.xml

# 模型② debuggable app（不需 root）：run-as 以 app 的 UID 進去
adb shell run-as com.example.target cat shared_prefs/auth.xml
#   run-as 只對 android:debuggable="true" 的 app 有效

# 模型② allowBackup（不需 root、不需 debuggable）：見「管道四」
```

> **`MODE_WORLD_READABLE` 的歷史坑**：早期 `getSharedPreferences` 可傳 `MODE_WORLD_READABLE`/`MODE_WORLD_WRITEABLE`，讓**任何 App**都能讀這個 prefs 檔（模型①，最嚴重）。這兩個 mode 在 **API 17（Android 4.2）就被 deprecated**、**API 24（Android 7）之後傳它會直接丟 `SecurityException`**。所以你今天在新 App 幾乎看不到，但逆到老 App 或 targetSdk 很低的 App 時要盯——它是「同裝置任意 App 讀取」這種高危洞的來源。

**那 `EncryptedSharedPreferences` 呢？** Jetpack Security 的 `EncryptedSharedPreferences` 會把 key 和 value 都用 Keystore 背書的金鑰加密，XML 裡看到的是密文。逆向時看到檔案內容是一堆 base64 而非明文，就是它。但**注意兩件事**：(1) 它保護的是「靜態檔案被讀」，App 一跑起來、金鑰在記憶體裡，Frida 照樣 hook `getString` 拿明文；(2) 這個 library 已於 2024 被標記 deprecated，但仍廣泛存在。這是 Ch 11 Keystore 誤用的前奏。

## 管道二：SQLite 明文資料庫

App 用 SQLite（多半透過 Room ORM）存結構化資料——聊天記錄、帳號、快取的 API 回應。檔案在 `/data/data/<pkg>/databases/<name>.db`，**預設完全不加密**，是標準 SQLite 格式，拉出來用任何 SQLite 工具就能開。

```bash
# 撈出 db（root 或 run-as）
adb root
adb pull /data/data/com.example.target/databases/app.db ./app.db

# 本機用 sqlite3 開，翻 schema 與資料
sqlite3 app.db ".tables"
sqlite3 app.db "SELECT * FROM users;"
```

SQLite 檔一眼就能認——**開頭 16 bytes 是固定 magic** `SQLite format 3\0`。我在本機驗證這個檔頭（**實際輸出**）：

```python
>>> hdr = b"SQLite format 3\x00"
>>> hdr.hex()
'53514c69746520666f726d6174203300'
>>> len(hdr)
16
```

所以你 `adb pull` 一堆檔案下來，`file *.db` 或看前 16 bytes 就能確認哪些是 SQLite。**反過來，如果一個 `.db` 開頭不是這串**，很可能被 **SQLCipher** 加密了（SQLCipher 把整個檔案含檔頭都加密，開頭是隨機 bytes）——這時要找金鑰，通常在程式碼裡硬編或走 Keystore，回到 Ch 11 的套路。

> **WAL 檔別漏了**：SQLite 開 WAL（Write-Ahead Logging）模式時，最新的寫入可能還在 `app.db-wal` 裡沒 merge 進主檔。只 pull `app.db` 可能拿到舊資料。要連 `app.db-wal`、`app.db-shm` 一起撈，或先讓 App 正常關閉觸發 checkpoint。這是實戰中「明明剛存的資料在 db 裡找不到」的常見原因。

## 管道三：外部儲存全域可讀

`getExternalStorageDirectory()`、`getExternalFilesDir()`、寫到 `/sdcard/Download/` ——這些是外部儲存。歷史上這裡是**全域可讀**的重災區。

```java
// 漏洞：把 token 寫到外部儲存的共享區
File f = new File(Environment.getExternalStorageDirectory(), "myapp/token.txt");
FileOutputStream fos = new FileOutputStream(f);
fos.write(token.getBytes());   // ← /sdcard/myapp/token.txt，舊 API 任意 App 可讀
```

**scoped storage（API 29+）改變了什麼**，這是本管道的關鍵版本分水嶺：

| Android 版本 | 外部儲存存取模型 | 對攻擊者的影響 |
|---|---|---|
| ≤ API 28（Android 9） | `READ_EXTERNAL_STORAGE` 就能讀整個 `/sdcard` | 任意 App 讀到別的 App 寫在外部儲存的檔（模型①）|
| API 29（Android 10） | 引入 scoped storage，預設 App 只看得到自己的 `Android/data/<pkg>/` 與自己建立的媒體 | 跨 App 讀取被收緊，但可用 `requestLegacyExternalStorage` 暫時退回舊行為 |
| API 30+（Android 11） | scoped storage 強制，`Android/data/<其他pkg>/` 對別的 App 不可見 | 模型①（其他 App 讀）基本被堵；但**模型②③（adb/root）照樣讀得到** |

所以判斷這個洞的嚴重度，**先看 App 的 targetSdk**（在 Manifest 或 `apktool.yml` 裡）。targetSdk ≤ 28 且把敏感檔寫外部儲存 = 同裝置任意 App 可讀的高危洞；targetSdk ≥ 30 則退化為「需要 adb/root 才讀得到」。

```bash
# 撈外部儲存（不需 root，adb shell 本身有讀 /sdcard 的權限）
adb shell ls -R /sdcard/Android/data/com.example.target/
adb pull /sdcard/Android/data/com.example.target/files/ ./ext_files/
```

> **一個常被忽略的點**：即使 scoped storage 堵了「其他 App 讀」，你（分析者）透過 `adb shell` 仍能讀 `/sdcard` 的絕大部分——因為 adb shell 跑在 `shell` UID，有 `READ_EXTERNAL_STORAGE` 等權限。所以「App 把東西寫外部儲存」在**評估**情境下永遠是可撈的，只是在**真實攻擊者（另一個惡意 App）**情境下要看版本。報告裡把這兩個情境分開講。

## 管道四：allowBackup=true + adb backup

這是不需要 root、不需要 debuggable 就能撈私有目錄的經典途徑。Manifest 的 `<application>` 若有 `android:allowBackup="true"`，App 的私有資料（含 `shared_prefs/`、`databases/`）可以被 `adb backup` 匯出到你的電腦。

```xml
<application android:allowBackup="true" ...>   ← 這個屬性是關鍵
```

**版本注意**：`allowBackup` 的**預設值是 `true`**（沒寫等於開）。也就是說開發者**不主動關**它就是開的。不過 `adb backup` 這條管道本身在較新版本上被逐步弱化——Android 12（API 31）起 `adb backup` 預設**不再備份 App 資料**（除非 App 明確允許 D2D 傳輸），且需要裝置端手動確認。所以：老 App / 老裝置這條路很好用，新裝置上 `adb backup` 常撈到空的。

**撈取流程**（**未實測，理論預期行為**；`adb backup` 需裝置螢幕上手動點「備份我的資料」）：

```bash
# 1. 觸發備份，-noapk 只要資料不要 apk，指定單一 package
adb backup -noapk com.example.target -f backup.ab
#    → 此時 AVD 螢幕會跳出確認框，要手動點「備份我的資料」

# 2. .ab 檔是「24-byte 檔頭 + (可選壓縮的) tar」，不能直接 tar xf
#    需先剝掉檔頭、解壓成 tar
```

`.ab` 檔的格式是可解析的——**檔頭是 ASCII 文字行**。我在本機驗證這個檔頭解析邏輯（**實際輸出**，用構造的 `.ab` 檔頭示範，非真實裝置備份）：

```python
# .ab 檔頭格式：magic 行 / version / compressed 旗標 / 加密演算法，各一行，\n 分隔
header = b"ANDROID BACKUP\n1\n1\nnone\n"   # 之後接 zlib 壓縮的 tar
lines = header.split(b"\n")
print("magic     :", lines[0])   # b'ANDROID BACKUP'
print("version   :", lines[1])   # b'1'
print("compressed:", lines[2])   # b'1'  (1=zlib 壓縮, 0=不壓縮)
print("encryption:", lines[3])   # b'none' (或 'AES-256' 若設了備份密碼)
```

```
magic     : b'ANDROID BACKUP'
compressed: b'1'
encryption: b'none'
```

拿到 `.ab` 後，剝掉這 24-byte 檔頭、若 `compressed=1` 就 zlib 解壓，得到一個標準 tar，`tar xvf` 就看到 `apps/com.example.target/sp/auth.xml` 等私有檔。社群工具 **abe（Android Backup Extractor）** 把這步一鍵化：`java -jar abe.jar unpack backup.ab backup.tar`。

> **驗證步驟（你在自己 AVD 上做）**：(1) 裝一個 targetSdk 低、`allowBackup` 沒關的靶 App（DIVA/InsecureBankv2 都有這關）；(2) `adb backup -noapk <pkg> -f b.ab`，螢幕點確認；(3) `dd if=b.ab bs=24 skip=1 | zlib-flate -uncompress > b.tar`（或用 abe）；(4) `tar tf b.tar` 看有沒有 `sp/*.xml`、`db/*.db`。撈到明文 token 就是這個洞成立。若在 Android 12+ 撈到空，換低版本裝置驗，並在報告註明版本限制。

## 管道五：logcat、剪貼簿、截圖快取

這三個是「資料從其他縫隙漏出去」的管道，威脅模型偏向 ④（惡意 App / 旁觀者）。

**logcat 洩漏**：開發者 debug 時 `Log.d("auth", "token=" + token)`，忘了在 release 版拿掉。logcat 在舊 Android（≤ API 15）任意 App 可讀全域 log；現代 Android 一個 App 只讀得到自己的 log，但**你（分析者）用 `adb logcat` 讀得到全部**。

```bash
# 邊操作 App 邊抓 log，grep 敏感關鍵字
adb logcat | grep -iE "token|password|passwd|secret|Bearer|Authorization"
```

這是評估時**性價比最高的一招**：不用逆程式碼，開著 logcat 把 App 點一遍，敏感資料自己跳出來的機率高得驚人。

**剪貼簿**：App 把密碼/token 用 `ClipboardManager.setPrimaryClip()` 放進剪貼簿（例如「複製你的 API key」按鈕），而**任何背景 App 都能讀系統剪貼簿**（API 29 前無限制；API 29+ 限制為前景 App 或預設 IME，但仍是攻擊面）。逆向時搜 `setPrimaryClip` / `ClipData` 找這類洩漏。

**截圖 / Recents 快取**：Android 在 App 切到背景時會截一張縮圖放進 Recents（最近任務）畫面，這張圖存在快取裡。如果 App 顯示敏感畫面（信用卡、TOTP）而**沒設 `FLAG_SECURE`**，這張截圖可能被撈到。逆向時看有沒有 `getWindow().setFlags(WindowManager.LayoutParams.FLAG_SECURE, ...)`——**沒有**就是潛在洩漏（銀行 App 該有，很多沒有）。

```bash
# Recents 縮圖快取位置（版本而異，需 root）
adb shell ls /data/system_ce/0/snapshots/    # Android 用來存 task snapshot
```

## 對比與取捨

| 管道 | 威脅模型 | 需要 root？ | 現代 Android 還有效？ | 撈取方式 |
|---|---|---|---|---|
| SharedPreferences 明文 | ②③（①若 WORLD_READABLE） | 否（run-as / backup） | 是（root/backup 永遠讀得到） | `cat` / `run-as` / backup |
| SQLite 明文 | ②③ | 否（run-as / backup） | 是 | `pull` + `sqlite3` |
| 外部儲存 | ①（≤API28）②③ | 否 | 部分（scoped storage 堵①，adb 仍讀） | `adb pull /sdcard/...` |
| allowBackup + adb backup | ② | 否 | 弱化（API 31+ 預設不備份） | `adb backup` + abe |
| logcat | ④（分析者永遠可讀） | 否 | 是（`adb logcat`） | `adb logcat \| grep` |
| 剪貼簿 | ④ | 否 | 部分（API 29+ 收緊背景讀取） | Frida hook `getPrimaryClip` |
| 截圖快取 | ④（拿到裝置） | 是（讀快取） | 是（無 `FLAG_SECURE` 時） | root 讀 snapshots |

**取捨的核心是「威脅模型 vs 影響」**：root 下什麼都讀得到，但這假設攻擊者已經 root；真正高危的是**不需要 root 就成立**的（WORLD_READABLE、外部儲存 ≤API28、allowBackup、logcat）。評估報告要把「這洞在什麼前提下成立」寫清楚，別一律標 Critical。

## 踩雷集錦

1. **把「root 能讀」當成高危洞**：所有本地檔案在 root 下都能讀，這幾乎是廢話。真正該強調的是「**不需要 root**」的路徑——WORLD_READABLE、外部儲存全域可讀、allowBackup、logcat。報告裡若只寫「root 後可讀 token」，評審會壓低你的評級。
2. **忘了看 targetSdk 就斷言外部儲存全域可讀**：scoped storage（API 29+）改變了遊戲規則。targetSdk ≥ 30 的 App，「其他 App 讀外部儲存」這條路基本被堵。先在 `apktool.yml` / Manifest 確認 targetSdk，再決定嚴重度。
3. **只 pull `.db` 沒 pull WAL**：SQLite WAL 模式下最新寫入在 `.db-wal`，只撈主檔會拿到舊資料，誤以為「沒存進去」。連 `-wal`/`-shm` 一起撈，或讓 App 正常關閉。
4. **`adb backup` 撈到空就以為 allowBackup 沒開**：Android 12（API 31）起 `adb backup` 預設不備份 App 資料，跟 `allowBackup` 屬性無關。這是**裝置版本**的限制，不是 App 沒開。換低版本裝置或改看 root 直讀來確認洞是否存在。
5. **看到 XML 是 base64 就以為安全**：`EncryptedSharedPreferences` 讓靜態檔案是密文，但金鑰在 Keystore、App 執行期解得開——Frida hook `getString` 照樣拿明文。「靜態加密」不等於「執行期安全」，這是 Ch 11 的伏筆。
6. **`run-as` 對非 debuggable app 失敗就放棄**：`run-as` 只對 `debuggable="true"` 的 app 有效，回 `run-as: package not debuggable`。這時走 root 或 backup，不是「撈不到」。

## 進階：再往深一層

- **File-Based Encryption（FBE）不救你**：Android 7+ 預設整機 FBE 加密，很多人誤以為「檔案都加密了所以安全」。FBE 是**保護裝置遺失/關機時**的資料——裝置解鎖、App 跑起來後，檔案對有存取權的主體（root、run-as、backup）就是明文可讀。FBE 防的是「撿到你關機的手機拆 flash」，不防「拿到你解鎖的手機用 adb」。這個區分在報告裡要講對。
- **Keystore 才是正解，但要用對**：敏感資料的正確做法是用 Android Keystore 產生**硬體背書**的金鑰來加密，且對高敏資料設 `setUserAuthenticationRequired(true)`。但 Keystore 也有一堆誤用（金鑰未硬體背書、未設 user auth、加解密邏輯可被 Frida 繞），這是 Ch 11 的重點。儲存這章告訴你「明文在哪」，Ch 11 告訴你「加密也可能是假的」。
- **backup 的 D2D 與雲端維度**：`allowBackup` 不只影響 `adb backup`，也影響 Google 雲端自動備份（Auto Backup）。開發者可用 `fullBackupContent` XML 或 `android:backupAgent` 精細控制哪些檔不備份。逆向時看這些設定能判斷「哪些資料會上雲」——這是另一條資料外洩維度。
- **Frida 動態撈 vs 靜態撈**：靜態撈檔案拿到的是「當下落地的資料」；有些 token 只活在記憶體、從不落地。這時 Frida hook 儲存 API（`putString`、`SQLiteDatabase.insert`）或直接 dump 進程記憶體才撈得到。Ch 11 之後你會越來越依賴動態這條腿。

## 動手練習

1. 在你的 AVD 裝一個靶（DIVA 的 "Insecure Data Storage" 系列有 SharedPreferences / SQLite / 外部儲存 / 臨時檔四關）。對每一關，用 `run-as`（若 debuggable）或 `adb root` 把資料撈出來，親眼看到明文。記錄每一關的檔案路徑。
2. 拿一個 `allowBackup` 沒關的靶（InsecureBankv2），跑 `adb backup -noapk <pkg> -f b.ab`，用 abe 或手動剝檔頭 + zlib 解壓成 tar，`tar tf` 列出內容，找到 `sp/*.xml` 裡的明文憑證。若你的 AVD 是 Android 12+ 撈到空，記下這個版本限制，改用 root 直讀驗證同一個洞。
3. 開著 `adb logcat | grep -iE "token|password|secret"`，把靶 App 從登入到主畫面點一遍，看有沒有敏感資料被印進 log。這招不用逆任何程式碼——體會「動態觀察比靜態逆向省事」的場景。
4. 用本章 Python 片段驗證 SQLite magic（前 16 bytes）與 `.ab` 檔頭格式。再找一個真的 `.db` 檔（AVD 裡任何 App 的），`adb pull` 出來看前 16 bytes 是不是 `SQLite format 3\0`。

## 本章重點整理

- App 資料住在**私有目錄**（UID+SELinux 隔離，非加密）與**外部/共享儲存**（歷史上全域可讀）；隔離一被繞過（root/run-as/backup），`.xml` 和 `.db` 就是明文。
- 撈取按威脅模型分：**不需 root 就成立的洞才是高危**——WORLD_READABLE prefs、外部儲存（≤API28）、allowBackup、logcat。root 能讀是廢話。
- **scoped storage（API 29+）** 堵了「其他 App 讀外部儲存」，但 adb/root 照樣讀；**`adb backup` 在 API 31+ 被弱化**。判斷嚴重度先看 targetSdk 與裝置版本。
- SQLite 明文（magic `SQLite format 3\0`）、`.ab` 是「24-byte ASCII 檔頭 + 壓縮 tar」——都可離線解析。logcat / 剪貼簿 / 無 `FLAG_SECURE` 截圖是額外洩漏管道。

## 自我檢核

- [ ] 能說出 App 私有目錄靠什麼隔離（不是加密），以及三種繞過隔離讀到明文的方式
- [ ] 能講清楚 scoped storage（API 29/30）改變了外部儲存的什麼，以及它**沒**改變什麼（adb/root 仍可讀）
- [ ] 拿到一份 `.ab` 檔，知道它的結構（檔頭 + 壓縮 tar）與怎麼解出裡面的 `sp/*.xml`
- [ ] 能解釋為什麼「報告只寫 root 後可讀 token」會被壓低評級，該怎麼寫才對
- [ ] 知道 `EncryptedSharedPreferences` 保護什麼、不保護什麼（伏筆 Ch 11）

## 延伸閱讀

- **[OWASP MASTG — Data Storage on Android](https://mas.owasp.org/MASTG/0x05d-Testing-Data-Storage/)** — OWASP
  - **讀哪裡**：SharedPreferences、SQLite、外部儲存、logs、剪貼簿、backup 各節的 "Testing" 步驟
  - **和本章的關聯**：本章每個管道對應這頁一個測試案例，是把本章變成可重複測試流程的標準參考；寫報告時引用它的測試編號
- **[Android Developers — Data and file storage overview](https://developer.android.com/training/data-storage)** — Android 官方
  - **讀哪裡**：internal vs external storage 的區分、scoped storage 那節
  - **為什麼值得讀**：從防禦者/開發者視角理解「該把什麼存哪」，你才知道開發者「順手存錯地方」的模式從何而來；scoped storage 的版本行為以官方為準
- **[Android Developers — Auto Backup & allowBackup](https://developer.android.com/guide/topics/data/autobackup)** — Android 官方
  - **讀哪裡**：`allowBackup` 預設值、`fullBackupContent` 排除規則、Android 12 對 `adb backup` 的變更
  - **和本章的關聯**：管道四的版本限制與「哪些檔會被備份/上雲」的精確依據
- **[Android Backup Extractor (abe)](https://github.com/nelenkov/android-backup-extractor)** — nelenkov
  - **讀哪裡**：README 的 unpack 用法與 `.ab` 檔格式說明
  - **為什麼值得讀**：管道四把 `.ab` 解成 tar 的一鍵工具；讀它的原始碼能徹底搞懂 `.ab` 檔頭與加密備份的解法

下一章我們處理「開發者以為自己有加密、其實沒有」的情況——ECB 洩漏 pattern、硬編金鑰、固定 IV、弱亂數、Keystore 誤用。儲存這章告訴你明文在哪，密碼學誤用那章告訴你**很多「加密」根本擋不住你**。

→ [Ch 11 密碼學誤用](./11-crypto-misuse.md)
