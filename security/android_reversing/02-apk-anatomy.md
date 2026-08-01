# Ch 2 — APK 結構解剖：從 zip 到簽名 scheme

> **目標**：把一個 APK 徹底拆開，搞懂裡面每個檔案是什麼、逆向時各是哪個工具的入口。重點打通兩個常卡人的底層：**DEX 的完整性欄位**（為什麼手改 DEX 會壞）與 **APK 簽名 scheme v1–v4**（為什麼改完重打包一定要重簽名、而且不能只用舊方法簽）。

> **環境**：本章的 zip 結構與 DEX header 示範，用 **Python 3.12** 在本機實際跑出（純檔案/演算法解析，不需 Android），輸出標「實際輸出」。真實 App 的 APK 內容為代表性說明。

## 為什麼要懂 APK 內部？

因為你所有的工具都在讀寫它。apktool 拆的是 APK 裡的 DEX 和資源、jadx 讀的是 DEX、IDA 逆的是 APK 裡的 `.so`、你改完要重打包還得自己把 zip 壓回去並重簽名。如果你把 APK 當成一個不透明的黑盒，你就永遠只能「照著教學按按鈕」，一旦流程出錯（簽名失敗、資源對不上、DEX 壞掉）完全不知道發生什麼。

而且 APK 結構本身就藏著防護的第一道線索：DEX 異常大或異常小、出現某個殼特有的 `.so`、簽名用了哪個 scheme——這些在你 unzip 的第一秒就能看到，是偵察（Ch 1 的 SOP 步驟 1）的起點。

## 先建立直覺：APK 就是一個 zip

最重要、也最多人不知道的一句話：**APK 就是一個 zip 檔，副檔名改一下而已**。不信我們造一個「假 APK」（結構跟真的一樣，內容是佔位符）來看：

```python
# 造一個 zip，塞進 APK 該有的東西，副檔名叫 .apk
import zipfile
z = zipfile.ZipFile('demo.apk', 'w', zipfile.ZIP_DEFLATED)
for p in ['AndroidManifest.xml','classes.dex','resources.arsc',
          'lib/arm64-v8a/libnative.so','META-INF/CERT.RSA']:
    z.write(p)
z.close()
```

用 Python 列出它的內容（**實際輸出**）：

```
File Name                                             Modified             Size
AndroidManifest.xml                            2026-08-01 00:37:58           14
classes.dex                                    2026-08-01 00:37:58           20
resources.arsc                                 2026-08-01 00:37:58            4
lib/arm64-v8a/libnative.so                     2026-08-01 00:37:58            2
META-INF/CERT.RSA                              2026-08-01 00:37:58            4
```

看它的前 4 個 byte（**實際輸出**）：

```python
>>> open('demo.apk','rb').read(4)
b'PK\x03\x04'
```

`PK\x03\x04` 是 zip 的 local file header magic（`PK` 是 zip 發明人 **P**hil **K**atz 的縮寫）。任何 APK 開頭都是這個——因為它就是 zip。這代表你可以用 `unzip`、`7z`、Python `zipfile` 直接解開任何 APK，不需要什麼特殊工具。

> **那 apktool 跟 unzip 差在哪？** `unzip` 給你**原始檔案**——`AndroidManifest.xml` 解出來是**binary XML**（二進位，人看不懂）、`classes.dex` 是二進位 bytecode。apktool 多做一步：把 binary XML **解碼成可讀 XML**、把 DEX **反組譯成 smali**、把 `resources.arsc` 解回 `res/` 目錄。所以「想看檔案清單」用 unzip 就好；「想讀懂內容」用 apktool。

## APK 內部佈局：每個檔案是什麼

真實 APK 解開後的標準結構：

```
app.apk (zip)
├── AndroidManifest.xml     # ← binary XML！App 的身分證：package/元件/權限
├── classes.dex             # ← 主 DEX，Dalvik bytecode（App 邏輯）
├── classes2.dex            # ← 方法數超過 65536 就分多個 DEX（multidex）
├── classes3.dex            # ...
├── resources.arsc          # ← 編譯後的資源表（string/顏色/尺寸的對照）
├── res/                    # ← 資源檔（layout、圖、raw）
│   ├── layout/  drawable/  xml/  ...
├── assets/                 # ← 開發者塞的原始檔（常藏設定、加密資料、Flutter 的 .so）
├── lib/                    # ← native 庫，按 ABI 分目錄
│   ├── arm64-v8a/  libnative.so     # 手機主流
│   ├── armeabi-v7a/                 # 舊 32-bit
│   └── x86_64/                      # 模擬器（你的 AVD 在這）
└── META-INF/               # ← 簽名與完整性資訊
    ├── MANIFEST.MF, CERT.SF, CERT.RSA   # v1 (JAR) 簽名
    └── ...
```

逆向時每個入口對應的工具：

| 檔案 | 是什麼 | 逆向切入 |
|---|---|---|
| `AndroidManifest.xml` | **binary XML**，App 的骨架 | apktool 解碼 → 找入口 Activity、權限、元件、`debuggable` |
| `classes*.dex` | Dalvik bytecode | apktool→smali（改）/ jadx→Java（讀） |
| `resources.arsc` | 資源 ID→值 的對照表 | apktool 解回可讀資源；找 string 常有金鑰/URL |
| `res/` | UI 與資源檔 | 找硬編碼字串、layout 對應功能 |
| `assets/` | 開發者原始檔 | **常被忽略的金礦**：設定檔、加密 blob、第二階段 payload |
| `lib/*.so` | native 機器碼 | IDA/Ghidra 逆；核心邏輯常藏這 |
| `META-INF/` | 簽名 | 判斷簽名 scheme；改完重打包要重簽這裡 |

> **`assets/` 值得特別盯**：它裝的是開發者原封不動塞進去的檔案，工具不會動它。惡意 App 的第二階段 payload、加固殼的加密 DEX、App 的預設設定與有時的硬編碼金鑰，都愛藏這裡。偵察時 `unzip -l app.apk | grep assets` 掃一眼常有驚喜。

## AndroidManifest.xml：為什麼 unzip 出來看不懂

直接 unzip 出來的 `AndroidManifest.xml` 開頭是 `\x03\x00\x08\x00...`，不是 `<?xml`。因為打包時 aapt2 把 XML **編譯成了 binary XML**（AXML）——一種用整數索引字串池、省空間又快解析的二進位格式。手機執行時不需要人類可讀，所以編成二進位。

要看懂，用 apktool 解碼（`apktool d`），它會還原成可讀 XML。你關心的欄位：

```xml
<manifest package="com.example.target" ...>
    <uses-permission android:name="android.permission.INTERNET"/>
    <application android:debuggable="true"   ← 逆向大禮包！可直接 attach 除錯器
                 android:name=".MyApp">       ← 自訂 Application 類，殼常從這載入
        <activity android:name=".LoginActivity">
            <intent-filter>
                <action android:name="android.intent.action.MAIN"/>  ← 入口
                <category android:name="android.intent.category.LAUNCHER"/>
            </intent-filter>
        </activity>
    </application>
</manifest>
```

- **`android:name` on `<application>`**：自訂 Application 類。加固殼幾乎都在這裡動手腳（把殼的載入器設成 Application，開機先跑殼），所以看到一個奇怪的 Application 類名是加固的強訊號。
- **`android:debuggable="true"`**：如果 App 開了這個，你能直接用 jdb/Android Studio attach 除錯，不用 root。正式 App 不該開，但常有人忘了關。
- **入口 `MAIN`/`LAUNCHER`**：App 從哪個 Activity 起步，逆向的起點。

## DEX 深挖：完整性欄位與「手改就壞」

`classes.dex` 是 App 邏輯的載體，Ch 4 會逐欄位拆。這裡先搞懂它的 **header 前 32 個 byte**，因為它解釋了逆向一個超常見的坑：**為什麼你手動改一個 byte，App 就裝不上了**。

DEX header 開頭的佈局：

```
offset  欄位            說明
  0     magic[8]        "dex\n035\0"  ← 035 是 DEX 版本號
  8     checksum (u32)  adler32( 從 byte 12 到檔尾 )
 12     signature[20]   SHA-1( 從 byte 32 到檔尾 )
 32     file_size (u32)  整個檔案大小
 36     header_size(u32) 固定 0x70 = 112
 40     endian_tag (u32) 0x12345678（小端標記）
 ...
```

關鍵在 **checksum 與 signature 這兩個完整性欄位**：`checksum` 是後面所有 byte 的 adler32、`signature` 是再後面所有 byte 的 SHA-1。它們是 DEX 的自我校驗——ART 載入 DEX 時會驗，對不上就拒絕載入。

我手工組一個結構正確的 DEX header，把這兩個欄位算出來，再篡改一個 byte 看會怎樣（**實際輸出**，Python 跑）：

```python
import struct, zlib, hashlib
data = bytearray(b"dex\n035\x00" + b"\x00"*4 + b"\x00"*20 +      # magic|checksum|sig
                 struct.pack("<I",0) + struct.pack("<I",0x70) +  # file_size|header_size
                 struct.pack("<I",0x12345678) + b"\x00"*100)     # endian_tag|body
data[12:32] = hashlib.sha1(bytes(data[32:])).digest()            # signature = SHA-1(bytes[32:])
data[8:12]  = struct.pack("<I", zlib.adler32(bytes(data[12:])) & 0xffffffff)  # checksum
```

```
magic        : b'dex\n035\x00'
checksum     : 0x66f60981   = adler32(bytes[12:])
signature    : 59291b9e153e02cfb85f7a1640b55dae1ae6c036 = SHA-1(bytes[32:])
header_size  : 0x70
endian_tag   : 0x12345678

[改 body 1 byte] signature 仍相符? False
[改 body 1 byte] checksum  仍相符? False
```

看最後兩行：**只改 body 裡一個 byte，signature 和 checksum 立刻雙雙對不上**。這就是為什麼你不能拿 hex editor 手改 DEX 直接用——你改了 body 卻沒重算這兩個欄位，ART 一驗就拒。

那 apktool 為什麼能改？因為 apktool 是**改 smali → 重新組譯成一個全新的 DEX**，新 DEX 的 checksum/signature 由組譯器（smali/baksmali）**重新計算**，自然是對的。你不是在 patch 舊 DEX，是在生一個新的。這也解釋了 Ch 6 的重打包流程為什麼繞這麼一圈，而不是直接 hex 改。

> **記住這個因果**：DEX 有自校驗 → 手改 byte 會破壞校驗 → 所以改邏輯要走「反組譯成 smali、改、重組」的路，讓工具幫你重算完整性欄位。Ch 32 講完整性校驗對抗時，你會看到 App 開發者還會在 DEX 自校驗之上再加自己的校驗，那又是另一層。

## APK 簽名：v1/v2/v3/v4，為什麼重打包要重簽

Android 規定**每個 APK 都必須簽名**才能安裝——簽名確保「這個 App 從打包後沒被動過，而且來自同一個開發者」。這對逆向的直接影響是：**你改完 APK 重打包後，原簽名一定失效，必須用你自己的 key 重簽**，否則裝不上（`INSTALL_PARSE_FAILED_NO_CERTIFICATES` 之類）。

但簽名有四代 scheme，逆向時要知道差別：

| Scheme | 簽的是什麼 | 存在哪 | 逆向影響 |
|---|---|---|---|
| **v1 (JAR)** | 逐檔算 hash，存在 `META-INF/*.SF` | `META-INF/` 內的檔案 | 老方法；**只簽 zip 內的檔案內容**，zip 結構本身沒簽 |
| **v2** (Android 7+) | **整個 APK 檔的位元組** | zip 尾端的 **APK Signing Block** | 改 APK 任何一 byte 都破壞簽名 |
| **v3** (Android 9+) | 同 v2 + 支援 key 輪替 | APK Signing Block | 同 v2，多了金鑰輪替 |
| **v4** (Android 11+) | 增量簽名（配合 `adb install --incremental`） | 額外的 `.idsig` 檔 | 較少碰到 |

關鍵理解 **v1 vs v2 的本質差別**：

- **v1** 是「JAR 簽名」的沿用：對 zip 裡**每個檔案的內容**分別算 hash 存起來。它**不簽 zip 的結構**（檔案順序、對齊、額外欄位都沒簽），所以歷史上有 Janus（CVE-2017-13156）這種在 zip 結構縫隙塞東西的攻擊。
- **v2/v3** 改成對**整個 APK 檔案的原始位元組**做簽名，塞在 zip 中央目錄前面的一個特殊區塊（APK Signing Block）。任何一 byte 變動都會讓簽名驗證失敗——更安全，但也代表你**連對齊都不能錯**。

這對你重打包的實際流程（Ch 6 詳講）意味著：

```bash
# 1. 改完 smali → apktool 重新打包成新 apk（未簽名）
apktool b target_out -o repacked.apk
# 2. zipalign 對齊（v2+ 簽名要求 4-byte 對齊，順序：先 align 再簽）
zipalign -p 4 repacked.apk aligned.apk
# 3. 用你自己的 keystore 簽（apksigner 會同時打 v1+v2+v3）
apksigner sign --ks my.keystore aligned.apk
# 4. 驗證簽名
apksigner verify -v aligned.apk
```

> **順序陷阱**：`zipalign` 必須在**簽名之前**做。因為 v2+ 簽的是整個檔案位元組，簽完再 align 會改動位元組、破壞簽名。老教學（v1 時代）常寫「先簽再 align」，那在 v2+ 是錯的。用 `apksigner`（不是舊的 `jarsigner`），它預設就會處理好 v1+v2+v3。

## 對比與取捨：unzip vs apktool vs jadx，什麼時候用哪個

| 你想做 | 用什麼 | 為什麼 |
|---|---|---|
| 看 APK 裡有哪些檔案 | `unzip -l` | 最快，不解碼 |
| 撈出 `.so` 去逆 | `unzip` 抽出來 | 不需要解碼，直接拿二進位 |
| 讀懂 Manifest / 資源 | `apktool d` | 會把 binary XML / arsc 解碼 |
| **改邏輯重打包** | `apktool d/b` + `apksigner` | smali 可改可重組，完整性欄位自動重算 |
| 只想讀懂 Java 邏輯 | `jadx` | 反編譯成近似 Java，最好讀 |

## 踩雷集錦

1. **unzip 出 AndroidManifest.xml 說「亂碼」**：那是 **binary XML**，本來就不是純文字。用 `apktool d` 解碼，別以為檔案壞了。
2. **hex editor 手改 DEX 直接裝**：DEX 有 adler32 + SHA-1 自校驗，改 byte 沒重算就會被 ART 拒。改邏輯走 apktool（smali 重組），不要手 patch DEX。
3. **重打包後忘了重簽名 / 用 jarsigner 只簽 v1**：現代 Android（7+ 目標）驗 v2，你只簽 v1 在新系統上裝不上。用 `apksigner`，它一次打 v1+v2+v3。
4. **先簽名再 zipalign**：v2+ 簽整個檔案位元組，簽完再 align 會破壞簽名。順序是**先 align 再簽**。
5. **忽略 split APK**：現代 App 是 `base.apk` + 一堆 `split_config.*.apk`（按 ABI/語言/螢幕密度切）。native `.so` 可能不在 base 裡而在 `split_config.arm64_v8a.apk`。只抓 base 會找不到 `.so`，`pm path` 列出的全部路徑都要看。
6. **以為簽名能驗「是不是官方」**：簽名只保證「沒被動過 + 同一把 key」，不保證「來自 Google Play」。任何人都能用自己的 key 簽一個 App。這也是你重打包後能自簽照裝的原因。

## 進階：再往深一層

- **APK Signing Block 的結構**：v2+ 的簽名塞在「zip 中央目錄之前、最後一個檔案之後」的一個 block，格式是 `size | id-value pairs | size | magic "APK Sig Block 42"`。它巧妙地不破壞 zip 相容性（zip 解析器從尾端的中央目錄讀，看不到這塊）。想深入看 [APK Signature Scheme v2 官方文件]。
- **Janus 漏洞（CVE-2017-13156）**：v1 只簽檔案內容不簽結構，攻擊者可以在 APK 前面塞一個 DEX，讓系統把整包當 DEX 執行、卻又通過 v1 簽名驗證。這是「只簽內容不簽容器」的經典教訓，v2 對整檔簽名正是為了堵它。
- **DEX 版本號的演進**：magic 裡的 `035`/`037`/`038`/`039` 對應不同 Android 版本加的 bytecode 特性（如 `038` 加了 `invoke-polymorphic` 支援 MethodHandle）。逆到高版本 DEX 時，工具太舊可能不認得新 opcode。
- **odex / vdex / oat**：安裝後 ART 會把 DEX 預編譯成機器碼存在別處（Part 6 的 dex2oat）。有時裝置上的 App 目錄裡 DEX 是「殘缺」的、真正跑的是 oat——這在脫殼與逆已安裝 App 時要留意。

## 動手練習

1. 把 Ch 0 撈出來的那個 APK 用 `unzip -l` 列內容，數一數有幾個 `classes*.dex`、有沒有 `lib/`、`assets/` 裡有什麼。再用 `apktool d` 解一次，對照「unzip 的原始 Manifest」vs「apktool 解碼後的可讀 Manifest」——親眼看 binary XML 與可讀 XML 的差別。
2. 用本章的 Python 片段自己算一個 buffer 的 adler32 與 SHA-1，然後改一個 byte 重算，確認值變了。把「DEX 有自校驗、手改會壞」這件事用自己的手驗證一遍。
3. 用 `apksigner verify -v <某個APK>` 看它用了哪些簽名 scheme（輸出會列 `Verified using v1/v2/v3 scheme: true/false`）。找一個新 App 和一個老 App 對比，看簽名 scheme 的差異。

## 本章重點整理

- **APK 就是 zip**（開頭 `PK\x03\x04`），可以直接解壓；apktool 比 unzip 多做「binary XML→可讀、DEX→smali、arsc→資源」的解碼。
- **DEX 有 adler32 checksum + SHA-1 signature 自校驗**，手改 byte 會破壞它——所以改邏輯要走 smali 重組（工具重算校驗），不能 hex patch。
- **簽名 scheme v1 簽檔案內容、v2+ 簽整個 APK 位元組**；重打包後必須用 `apksigner` 重簽，且**先 zipalign 再簽**。
- `AndroidManifest.xml` 是 binary XML，藏著入口/權限/`debuggable`/自訂 Application（加固訊號）；`assets/` 是常被忽略的金礦。

## 自我檢核

- [ ] 能解釋 unzip 與 apktool 拆 APK 的差別，並說出各自適合什麼場景
- [ ] 能說出為什麼手動 hex 改一個 DEX byte 會導致裝不上，以及 apktool 為什麼可以改
- [ ] 能講清楚簽名 v1 與 v2 的本質差別，以及為什麼「先 align 再簽」
- [ ] 拿到一個 APK，知道從 Manifest 的哪些欄位判斷入口、權限、是否加固
- [ ] 知道 native `.so` 可能藏在 base 之外的 split APK 裡

## 延伸閱讀

### 官方文件

- **[APK Signature Scheme v2](https://source.android.com/docs/security/features/apksigning/v2)** — AOSP
  - **讀哪裡**：APK Signing Block 的結構圖那節；v2 為什麼要簽整個檔案
  - **和本章的關聯**：本章「重打包要重簽、先 align 再簽」的底層原因就在這；想懂 Janus 為什麼被堵，讀這頁
- **[DEX 檔案格式](https://source.android.com/docs/core/runtime/dex-format)** — AOSP
  - **讀哪裡**：`header_item` 那節，逐欄位對照本章的 header 佈局；checksum/signature 的定義
  - **注意**：整份很長，先讀 header 與 map_list，其餘 Ch 4 再回來
- **[aapt2 與資源編譯](https://developer.android.com/tools/aapt2)** — Android Developers
  - **讀哪裡**：resources 如何被編譯進 `resources.arsc`
  - **和本章的關聯**：解釋為什麼資源是 arsc 二進位而不是純檔案，apktool 在還原什麼

### 漏洞案例

- **[Janus 漏洞分析 (CVE-2017-13156)](https://www.guardsquare.com/blog/new-android-vulnerability-allows-attackers-to-modify-apps-without-affecting-their-signatures)** — GuardSquare
  - **這篇說什麼**：v1 簽名「只簽內容不簽容器」如何被 DEX+APK 混合檔繞過
  - **讀哪裡**：漏洞原理那段（DEX 和 APK 都能從檔案不同位置解析）
  - **為什麼值得讀**：這是理解「為什麼需要 v2 對整檔簽名」最好的反面教材，把抽象的簽名 scheme 差異變成具體的攻擊

### 工具文件

- **[apksigner 官方文件](https://developer.android.com/tools/apksigner)** — Android Developers
  - **讀哪裡**：`sign` 與 `verify` 子命令；`--v1-signing-enabled`/`--v2-signing-enabled` 旗標
  - **前提知識**：讀過本章簽名那節，這頁給你重打包重簽的精確指令

下一章我們從「檔案」升到「執行」：這個 APK 裝進系統後，是怎麼被 Zygote 孵化成進程、被沙箱關進自己的 UID、被權限系統與 SELinux 層層設限的？搞懂執行與安全模型，你才知道 Frida 為什麼要 root、注入是在突破哪一道牆、App 之間為什麼隔離。

→ [Ch 3 執行與安全模型：Zygote、沙箱、權限、SELinux](./03-execution-security-model.md)
