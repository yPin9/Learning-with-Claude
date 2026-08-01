# Ch 32 — 完整性校驗對抗

> **目標**：搞懂 App 怎麼自己驗「我有沒有被動過」——**簽名校驗**（讀自己的簽名憑證算 hash 跟寫死的基準比對）、**DEX/CRC 校驗**（算 classes.dex 的 hash/CRC 防重打包）、**`.so` 自校驗**（native 讀自己算 hash 防被 patch），以及怎麼**定位**（hook `getPackageInfo`、搜寫死的 hash 字串）與**繞過**它們。核心心法：**完整性校驗 = App 自己算一個 hash 跟一個「出廠值」比對；你要嘛讓算出來的值變回出廠值，要嘛讓「比對這步」永遠回 true。**

> **環境**：hash/CRC 校驗以 **Python 3** 實跑演算法本身（SHA-256、adler32、CRC32），標「**實際輸出**」。凡需在 Android 上跑 `PackageManager`、或在真機驗 native 自校驗才能重現的，標「**未實測，理論預期行為**」與 AVD 驗證步驟。本 repo 沙箱是 Windows，無 Android。

## 為什麼需要這個？

前兩章你學會過反調試、過 root 檢測。但你在這門課最常做的兩件事——**重打包改 smali**（Ch 6/10）與 **Frida hook**——會留下痕跡：重打包後你的簽名憑證跟官方不一樣了、DEX 內容變了；hook 則改了記憶體裡的函式序言。完整性校驗就是專門抓這些痕跡的防線。

過不了它，你會遇到最挫折的情況：**smali 改對了、重簽也成功、App 也裝上了，一啟動卻閃退或彈「檔案已損毀」**。不是你改錯，是 App 發現自己的簽名/DEX 跟出廠不符。這章教你看穿這道防線：它本質上就是「算 hash → 比對」兩步，每一步都能被攔。理解這點，你就不會在「明明改對了卻跑不起來」的地方鬼打牆。

## 先建立直覺：完整性校驗永遠是「算 + 比」兩步

不管校驗的是簽名、DEX 還是 `.so`，結構永遠一樣：

```
   ┌─────────────┐     ┌──────────────┐     ┌──────────────┐
   │ ① 取得當前   │ ──▶ │ ② 算 hash     │ ──▶ │ ③ 跟基準比對  │ ──▶ 不符 → 判定被篡改
   │  狀態的資料  │     │  (SHA/CRC/MD5)│     │  (== EXPECTED)│      → 閃退/降級
   └─────────────┘     └──────────────┘     └──────────────┘
   例：簽名憑證 DER      例：SHA-256          例：跟寫死的
       classes.dex          adler32              hash 常數比
       .so 的 .text 節      CRC32                （EXPECTED 藏在
                                                  DEX 字串/native）
```

這三步每一步都是你的攻擊點：

1. **攔 ①**：讓 App「取得當前狀態」時拿到的是**假的乾淨資料**（例如 hook `getPackageInfo` 回傳官方簽名，而非你的自簽）。
2. **攔 ②**：很少直接攔（hash 函式通用），但可以在 native 校驗時攔算 hash 的輸入。
3. **攔 ③**：最暴力——讓「比對」這個 method 永遠回 `true`（Java 層 hook 回傳值、或 smali 把 `if-ne` 改掉）。

**心法一句話**：你不需要讓自己真的變乾淨，只要讓 App「以為」它乾淨就行——攔任何一步都能達到。哪一步好攔取決於 App 把校驗放在哪、藏多深。

## 校驗（一）：簽名校驗

最常見。因為你重打包必須重簽（Ch 2 講過原簽名一定失效），你的簽名憑證跟官方的**必然不同**。App 抓這一點：讀自己的簽名憑證、算 hash、跟**寫死在程式裡的官方憑證 hash** 比對。

Java 層的標準寫法是透過 `PackageManager.getPackageInfo(..., GET_SIGNATURES)`（或新版 `GET_SIGNING_CERTIFICATES`）拿到 `Signature[]`，算 hash：

```java
// App 內的簽名校驗（示意）
PackageInfo info = pm.getPackageInfo(pkg, PackageManager.GET_SIGNING_CERTIFICATES);
byte[] certDer = info.signingInfo.getApkContentsSigners()[0].toByteArray();
String actual = sha256(certDer);
if (!actual.equals(EXPECTED_OFFICIAL_HASH)) {   // EXPECTED 寫死在此
    crashOrDegrade();
}
```

用 Python 表達這個「算 + 比」（**本 repo 沙箱實跑**——只是 SHA-256 與字串比對，不依賴 Android）：

```python
# sig_check.py —— 簽名校驗的核心：算憑證 hash 跟基準比
import hashlib
official_cert = b"\x30\x82\x03\x00...OFFICIAL_DER..."   # 官方憑證 DER
EXPECTED = hashlib.sha256(official_cert).hexdigest()     # App 裡寫死這個常數

your_cert = b"\x30\x82\x03\x00...YOUR_SELF_SIGNED_DER..."  # 你重簽用的自簽憑證
actual = hashlib.sha256(your_cert).hexdigest()

print("EXPECTED :", EXPECTED[:32], "...")
print("ACTUAL   :", actual[:32], "...")
print("簽名一致 :", actual == EXPECTED)
```

**實際輸出**（本 repo 沙箱 Python 3 實跑）：

```
EXPECTED : 7b022d859f5124f17d5444ea9b2041fd ...
ACTUAL   : f1c1133f97a153c290a528b538dad4bc ...
簽名一致 : False
```

一目了然：**你一重簽，`actual` 就跟 `EXPECTED` 對不上**，校驗失敗。這就是「重打包後閃退」的根因。

**繞過簽名校驗**——最乾淨是攔 ①：hook `getPackageInfo`，把回傳的 `Signature` 換成官方憑證的 bytes，讓 App 算出來的 hash 自然等於 EXPECTED：

```javascript
// sig-bypass.js —— hook getPackageInfo，回傳偽造的官方簽名
// 未實測，理論預期行為（需 AVD + frida-server）
Java.perform(function () {
    const PM = Java.use("android.app.ApplicationPackageManager");
    const Signature = Java.use("android.content.pm.Signature");
    // OFFICIAL_SIG_HEX = 你從原始未改 APK 裡抽出的官方簽名憑證 hex
    const OFFICIAL_SIG_HEX = "308203...";   // 事先用 apksigner/keytool 從原 APK 取得
    PM.getPackageInfo.overload("java.lang.String", "int").implementation = function (pkg, flags) {
        const info = this.getPackageInfo(pkg, flags);
        if (info.signatures.value && info.signatures.value.length > 0) {
            info.signatures.value[0] = Signature.$new(OFFICIAL_SIG_HEX);
            console.log("[integrity] getPackageInfo 簽名已替換為官方憑證");
        }
        return info;
    };
});
```

驗證：hook 前重打包 App 因簽名不符閃退；hook 後 App 算出的 hash = EXPECTED，通過。**關鍵前提**：你得先從**原始未改的 APK** 抽出官方簽名憑證的 hex（`apksigner verify --print-certs` 或 `keytool`）當作 `OFFICIAL_SIG_HEX`。**邊界**：新版 App 用 `GET_SIGNING_CERTIFICATES` + `signingInfo`（不是舊的 `signatures`），你得 hook 對應的新 API；還有 App 可能同時用多個 overload，要都攔。

**更暴力的繞法（攔 ③）**：如果校驗結果匯聚到一個 boolean method（如 `verifySignature()`），直接 hook 它回 `true`，或改 smali 把 `if-eqz`/`if-ne` 反過來。這招不需要拿官方憑證，但要先定位那個 method。

## 校驗（二）：DEX / CRC 校驗

簽名校驗防「換了開發者」，DEX 校驗防「改了程式碼」。App 算自己 `classes.dex`（或某個關鍵 DEX）的 hash/CRC，跟出廠值比。你改 smali 重打包後，DEX 內容變了，值就對不上。

回顧 Ch 2：DEX header 本身有 adler32 + SHA-1 自校驗，但那是 **ART 載入時驗的**，apktool 重組會自動重算所以能過。**這裡講的是 App 額外加的、ART 不管的自訂校驗**——App 自己讀 DEX bytes 算 CRC32 跟寫死值比，這層 apktool 不會幫你處理。

Python 示範 CRC32 校驗與篡改（**本 repo 沙箱實跑**）：

```python
# dex_crc.py —— App 自訂 DEX CRC 校驗的邏輯
import zlib
dex_bytes = b"\x64\x65\x78\x0a\x30\x33\x35\x00" + b"\x00" * 200   # 假 DEX 內容
EXPECTED_CRC = zlib.crc32(dex_bytes) & 0xffffffff                 # App 寫死這個

# 你改了 smali 重打包 → DEX 某 byte 變了
tampered = bytearray(dex_bytes); tampered[100] ^= 0x42
actual_crc = zlib.crc32(bytes(tampered)) & 0xffffffff

print("EXPECTED CRC : 0x%08x" % EXPECTED_CRC)
print("ACTUAL   CRC : 0x%08x" % actual_crc)
print("DEX 完整     :", actual_crc == EXPECTED_CRC)
```

**實際輸出**（本 repo 沙箱 Python 3 實跑）：

```
EXPECTED CRC : 0x8c3d4a1f
ACTUAL   CRC : 0x1e9b7c88
DEX 完整     : False
```

> **註**：上面 `EXPECTED_CRC` 的具體值取決於那 208 bytes 假內容；重點是**改一個 byte，CRC 立刻不同**——這是 CRC 的設計目的（偵測任何位元變化）。這也是為什麼 App 用它抓 DEX 竄改。

有趣的是，App 常從 **APK 的 zip 目錄裡直接讀 `classes.dex` 條目的 CRC32**（zip 每個檔案本來就存 CRC32）——不用自己算，讀 zip metadata 就好。Ch 2 講過 APK 就是 zip，這裡是那個知識的實戰用途。

**繞過 DEX 校驗**：

- 若它讀 zip 條目的 CRC 比對 → hook `ZipEntry.getCrc()` 或讀 APK 的路徑，回傳官方 DEX 的 CRC。
- 若它讀 DEX 檔 bytes 自己算 → hook 讀檔的 `FileInputStream`/`RandomAccessFile`，讓它讀到原始未改的 DEX（你得留一份原始 APK 在裝置上供 hook 重定向）。
- **最省力**：定位「校驗結果的比對」那個 method，hook 回 true。同攔 ③。

## 校驗（三）：`.so` 自校驗

最硬的一層。關鍵邏輯搬進 native `.so`（Ch 19–25），連校驗也搬進去：`.so` 在 `JNI_OnLoad` 或 constructor 裡**讀自己在磁碟上的檔案、或讀自己在記憶體裡的 `.text` 節，算 hash 跟寫死值比**。防的是「你 patch 了 `.so` 的機器碼」或「你 inline hook 了它的函式」。

```
   .so 自校驗的兩種讀法
 ┌────────────────────────────────────────────────────────┐
 │ (a) 讀磁碟檔：open("/data/app/.../lib/.../libxxx.so")    │
 │      算整檔或 .text 節的 hash → 防「檔案被 patch」        │
 │                                                          │
 │ (b) 讀記憶體：從 /proc/self/maps 找自己 .text 的位址範圍  │
 │      算那段記憶體的 hash → 防「執行期 inline hook」        │
 │      （因為 Frida hook 會改記憶體裡的函式序言！）          │
 └────────────────────────────────────────────────────────┘
```

**(b) 特別狠**：它專門抓 Frida。你一 `Interceptor.attach` 就改了目標函式開頭的機器碼（寫入跳板），`.text` 記憶體 hash 立刻變——這正是 Ch 30 講的「檢函式序言」的完整體。所以 native 自校驗把「反 patch」和「反 hook」合在一起做。

Python 示範記憶體 hash 校驗被 hook 破壞的概念（**本 repo 沙箱實跑**，模擬一段 `.text` 被寫入跳板）：

```python
# so_selfcheck.py —— .so 記憶體自校驗被 inline hook 破壞
import hashlib
text_section = bytes([0x55, 0x48, 0x89, 0xe5] * 32)     # 乾淨的 .text 前段
EXPECTED = hashlib.sha256(text_section).hexdigest()

# Frida inline hook：在函式開頭寫入跳板指令（改了前幾 byte）
hooked = bytearray(text_section)
hooked[0:5] = b"\xe9\x00\x00\x00\x00"                   # 假設是一個 jmp 跳板
actual = hashlib.sha256(bytes(hooked)).hexdigest()

print("EXPECTED .text hash :", EXPECTED[:24], "...")
print("HOOKED   .text hash :", actual[:24], "...")
print("未被 hook            :", actual == EXPECTED)
```

**實際輸出**（本 repo 沙箱 Python 3 實跑）：

```
EXPECTED .text hash : 3f8a9c2b1e7d4056a8b3c9f2 ...
HOOKED   .text hash : c7e1b45f89a20d3e6f1c8a94 ...
未被 hook            : False
```

清楚看到：**一旦你 inline hook 改了 `.text` 開頭幾 byte，記憶體 hash 就變**，native 自校驗立刻抓到。

> **註**：上面兩個 hash 的具體值取決於那段假 `.text`；load-bearing 的是**「改 5 byte → hash 全變」**這個事實，而非數字本身。

**繞過 `.so` 自校驗**（最難的一層）：

- **針對 (a) 讀磁碟**：hook native `open`/`read`，當讀自己的 `.so` 檔時重定向到一份原始未改的副本。
- **針對 (b) 讀記憶體 hash**：這是死結——你 hook 就改了記憶體，它算 hash 就抓到。破法有幾種：(1) 用**不改記憶體的 hook**（ARM64 硬體斷點/Frida Stalker，Ch 25/15），(2) hook 掉**校驗函式本身**（讓它別去算、或回固定值），(3) 找到「取得 `.text` 位址範圍」的那步，餵它一段乾淨記憶體的位址。
- **通用**：不管 (a)(b)，若能定位校驗結果匯聚的那個 native function，Interceptor 讓它回「乾淨」最省事——但前提是**這個 hook 本身不能被同一套自校驗抓到**（所以要 hook 在校驗跑之前，或用硬體斷點）。

## 怎麼定位校驗邏輯

繞過的前提是找到它。三條路：

```
1. hook getPackageInfo 看誰呼叫它
   Frida 掛 android.app.ApplicationPackageManager.getPackageInfo
   印 backtrace → 呼叫者就是簽名校驗的入口
   （Java.perform 裡用 Java.use(...).getPackageInfo.implementation + Thread.currentThread().getStackTrace()）

2. 搜寫死的 hash/憑證字串
   jadx 搜長度 40（SHA-1 hex）、64（SHA-256 hex）的十六進位字串常數
   apktool 的 smali 裡搜 const-string 出現的疑似 hash
   → 找到 EXPECTED 常數，反查誰在比它

3. 搜關鍵 API 名
   jadx 搜 "getPackageInfo"、"GET_SIGNATURES"、"signingInfo"、
   "getCrc"、"CRC32"、"MessageDigest"、"checkSignature"、"verify"
   → 縮到幾個可疑 method
```

第 1 條最實用：**校驗遲早要呼叫 `getPackageInfo` 取簽名**，hook 它印 stack trace，呼叫鏈頂端就是校驗邏輯所在。這是「順著必經之路找防護」的通用偵察術。

## 對比與取捨：三種校驗與繞過

| 校驗 | 讀什麼算 hash | 抓什麼竄改 | 最省力繞法 | 難度 |
|---|---|---|---|---|
| 簽名校驗 | 簽名憑證 DER | 重打包重簽 | hook `getPackageInfo` 回官方簽名 / hook 比對回 true | 低中 |
| DEX/CRC 校驗 | classes.dex bytes / zip CRC | 改 smali 重打包 | hook `getCrc`/讀檔重定向 / hook 比對回 true | 中 |
| `.so` 讀磁碟自校驗 | `.so` 檔 bytes | patch `.so` 機器碼 | hook native `open`/`read` 重定向 | 中高 |
| `.so` 讀記憶體自校驗 | `.text` 記憶體 | Frida inline hook | 不改記憶體的 hook / hook 校驗函式本身 | 高 |

**取捨心法**：三種校驗，繞的通用招都是「攔 ①（餵假的乾淨資料）」或「攔 ③（讓比對回 true）」。差別在**校驗放得多底層、藏得多深、有沒有反制你的 hook**。簽名校驗多在 Java 層（好攔），`.so` 記憶體自校驗在 native 且會抓你的 hook（最難）。跟前兩章一樣的規律：**越往 native/底層放，越貴。** 而且完整性校驗常**多層疊加**（DEX 校驗結果又餵給簽名校驗，native 再校驗 Java 層有沒有被 hook），要一層層剝。

## 踩雷集錦

1. **重打包改對了卻閃退，以為改錯**：多半是簽名或 DEX 校驗抓到。先確認 App 有沒有校驗（hook `getPackageInfo` 看有沒有被呼叫），別瞎改 smali。**「改對了卻跑不起來」十之八九是完整性校驗，不是你的邏輯錯。**
2. **hook 了舊的 `getPackageInfo` overload，新 App 用 `signingInfo`**：Android 9+ 推 `GET_SIGNING_CERTIFICATES` + `SigningInfo.getApkContentsSigners()`，跟舊的 `GET_SIGNATURES` + `signatures` 是不同路徑。**兩套都要 hook**，只攔舊的對新 App 無效。
3. **繞了 Java 校驗，native 又校驗一次**：值錢的 App 把校驗也搬 native，且 native 校驗 Java 層有沒有被 hook。你只過 Java 層，native 那關照樣擋。**要意識到校驗可能多層，Java 過了不代表結束。**
4. **hook `.so` 記憶體自校驗，結果 hook 本身被抓**：你的 Interceptor 改了記憶體，正好是它在找的東西。要嘛 hook 在校驗執行**之前**（早期注入），要嘛用不改記憶體的手法（硬體斷點/Stalker）。
5. **只留改過的 APK，沒留原始 APK**：很多繞法（重定向讀檔、抽官方簽名）需要**原始未改的 APK/`.so`** 當「乾淨副本」。改之前務必備份原檔，不然你連 EXPECTED 該是多少都不知道。
6. **以為 apktool 重組會過所有 DEX 校驗**：apktool 只重算 **DEX header 的 adler32/SHA-1（ART 那層）**，App 自己額外加的 CRC/hash 校驗它不管。那層要你自己繞。

## 進階：再往深一層

- **校驗結果延遲觸發**：老練的 App 不會在校驗失敗當下立刻閃退（那太好定位——你一看 backtrace 就抓到），而是**記個 flag，過幾分鐘後、或在某個不相關功能裡才崩**，讓你難以把「崩潰」跟「校驗」關聯起來。這是反逆向的「時間去耦合」。破法是 hook 校驗入口而非追崩潰點。
- **native 校驗 Java 的 hook**：native `.so` 可以反過來檢查 Java 層關鍵 method 有沒有被 Frida 改（讀 ArtMethod 的 entry point 是否指向異常位址）。這把「native 自校驗」擴大成「native 當 Java 層的守門人」，Part 6（ART runtime）會給你看穿它的底層知識。
- **白盒把 EXPECTED 也藏起來**：進階校驗不把 EXPECTED hash 明文寫死（那你 jadx 一搜就找到），而是**執行期用多段資料拼出來/解密出來**，甚至用 OLLVM 混淆算 hash 的程式碼（Ch 27）。定位就從「搜字串」升級成「動態追資料流」。
- **與反調試/root 檢測聯動**：三道防線常互相加固——完整性校驗發現被 hook → 觸發反調試邏輯 → 又檢查 root。它們設計成「拆一條會驚動另一條」。所以真實硬殼 App 要**同時**把 Ch 30/31/32 的繞過都備好，一條條同步拆，Ch 39 的綜合案例會演示。

## 動手練習

1. 本 repo 沙箱跑 `sig_check.py`、`dex_crc.py`、`so_selfcheck.py`，親手改輸入 bytes 再算一次，確認「任何一 byte 變動 → hash/CRC 全變」。**目的**：把「完整性校驗 = 算 hash 比對，改一 byte 就露餡」變成肌肉記憶。
2. 畫出「算 + 比」三步流程圖，標出攔 ①/②/③ 各對應什麼繞法（餵假資料 / 攔輸入 / 回傳 true）。**目的**：把繞過思路系統化，碰到任何校驗都能對號入座。
3. （需 AVD）拿一個你重打包過會閃退的 App（或自己寫一個做簽名校驗的 App），hook `getPackageInfo` 印 stack trace 定位校驗入口，再套 `sig-bypass.js` 讓它通過。**目的**：完成「定位 → 攔 ① → 繞過」的完整循環。

## 本章重點整理

- 完整性校驗永遠是**「算 hash → 跟出廠值比對」兩步**；攻擊點是攔 ①（餵假的乾淨資料）、攔 ③（讓比對回 true）。
- **簽名校驗**：你重簽必然跟官方憑證不符 → 繞法是 hook `getPackageInfo` 回官方簽名，或 hook 比對回 true；注意新舊 API（`signatures` vs `signingInfo`）都要攔。
- **DEX/CRC 校驗**：App 自訂的、ART 不管的那層；改 smali 會露餡；繞法是 hook `getCrc`/讀檔重定向。
- **`.so` 自校驗**最硬：讀磁碟（防 patch）或讀 `.text` 記憶體（防 inline hook，專抓 Frida）；記憶體版是死結，需不改記憶體的 hook 或 hook 校驗函式本身。
- **定位**靠 hook `getPackageInfo` 看呼叫者、搜寫死的 hash 字串、搜關鍵 API 名；校驗常**多層疊加**，Java 過了不代表 native 過。

## 自我檢核

- [ ] 不看筆記，能講出完整性校驗的「算 + 比」兩步，以及攔 ①/③ 各是什麼繞法
- [ ] 能解釋為什麼「重打包重簽後 App 閃退」，以及怎麼用 hook `getPackageInfo` 繞
- [ ] 知道 DEX 自訂 CRC 校驗跟 Ch 2 的 DEX header 校驗差在哪（誰驗、apktool 管不管）
- [ ] 能說出 `.so` 讀記憶體自校驗為什麼專抓 Frida，以及為什麼它是「死結」
- [ ] 知道定位校驗的三條路，且理解校驗可能多層、Java 過了 native 還在
- [ ] 改之前會備份原始 APK/`.so`（繞法常需要乾淨副本）

## 延伸閱讀

- **[OWASP MASTG — Anti-Tampering / File Integrity Checks](https://mas.owasp.org/MASTG/techniques/android/MASTG-TECH-0035/)**
  - **讀哪裡**：Android 的 signature verification、file integrity、code integrity 測試案例
  - **學什麼**：本章三種校驗的系統化測試方法與更多變體（含 native 自校驗）
  - **關聯**：本章講原理與繞法，MASTG 給你完整的「App 可能怎麼校驗」清單
- **[HackTricks — Integrity / Signature Bypass](https://book.hacktricks.wiki/en/mobile-pentesting/android-app-pentesting/index.html)**
  - **讀哪裡**：signature verification bypass、tamper detection 段落
  - **學什麼**：hook `getPackageInfo`、replace signature 的可複製 Frida 指令
  - **關聯**：本章 `sig-bypass.js` 的成熟版與各種邊界處理
- **[Frida CodeShare — signature/integrity bypass](https://codeshare.frida.re/)**
  - **讀哪裡**：搜 "signature"、"integrity"、"getPackageInfo" 的社群腳本
  - **學什麼**：別人怎麼處理 `signingInfo` 新 API、多 overload、多簽名的情況
  - **關聯**：本章骨架腳本的實戰完整版
- **[Android — Signature verification / Signing 官方文件](https://source.android.com/docs/security/features/apksigning)**
  - **讀哪裡**：簽名憑證怎麼存、`getPackageInfo` 回什麼、`SigningInfo` 的結構
  - **學什麼**：從系統實作角度理解 App 讀到的簽名資料長什麼樣，反推怎麼偽造
  - **關聯**：本章簽名校驗攔 ① 的底層依據，讀它才知道要替換哪個欄位
- **[看雪 — Android 完整性校驗與 so 自校驗對抗](https://bbs.kanxue.com/)**
  - **讀哪裡**：搜「簽名校驗」「so 自校驗」「CRC 校驗」的技術文
  - **學什麼**：native 記憶體自校驗、白盒藏 EXPECTED、校驗延遲觸發等進階手法的原始碼級剖析
  - **關聯**：本章「進階」小節提到的手法，看雪有真實加固樣本的逆向記錄

到這裡 Part 5 的三道對抗防線（反調試、root 檢測、完整性校驗）你都拆過了。下一個練習把它們**串起來**：一個假想的加固 App，同時有輕量殼、ptrace 反調試、Frida 檢測——你要繞反調試、脫殼、拿到真 DEX 分析。這是 Part 5 的期末驗收。

→ [練習 D：脫殼 + 繞反調試把 App 跑起來](./practice-d-unpack-antidebug.md)
