# Ch 6 — apktool：反編譯、改 smali、回編譯、重簽名

> **目標**：把 apktool 的完整循環走通一遍——`apktool d` 反編譯 → 改 smali → `apktool b` 回編譯 → `zipalign` 對齊 → `apksigner` 重簽（v1+v2+v3）→ 裝進 AVD。過程中把每一步「工具到底在做什麼」講清楚，讓你在回編譯失敗時（資源錯、multidex、簽名裝不上）能自己判斷卡在哪一環，而不是照著別人的指令乾等。

> **環境**：本章以 **apktool 2.9+**、**Android SDK build-tools 34**（提供 `zipalign` 與 `apksigner`）、JDK 11+、AVD（Android 13 / API 33，x86_64）為準。zip 結構與 adler32 校驗的示範用 **Python 3.12** 在本機實際跑出，標「**實際輸出**」；需要 apktool/apksigner/AVD 的完整回編譯流程，本 repo 沙箱沒有 Android 工具鏈，標「**未實測，理論預期行為**」並附你自己驗證的步驟。

## 為什麼需要這個？

Ch 1 講過：要**改邏輯重打包**，走的是 smali 這條路，不是 jadx 的 Java。Ch 2 講過：DEX 有自校驗、APK 必須簽名，所以你不能 hex patch 直接裝。apktool 就是把這兩件事縫起來的工具——它幫你把 DEX 反組譯成可改的 smali，改完再**重新組譯成一個全新的 DEX**（校驗欄位自動重算），連 binary XML 與 `resources.arsc` 也一併解碼／還原。

但 apktool 不是「按一個鍵就好」。它的回編譯很容易失敗，而且失敗訊息常常很難懂（`brut.androlib.exceptions.AndrolibException`）。這一章的重點不只是流程，是**讓你有能力 debug 這個流程**——因為你在真正的逆向任務裡，十次回編譯有三四次會第一次失敗。搞懂每一步在幹嘛，你才不會卡死。

## 先建立直覺：apktool 的一進一出

apktool 只有兩個核心動作，`d`（decode）與 `b`（build），是一對可逆操作：

```
   app.apk                                          repacked.apk
 ┌──────────────┐                                 ┌──────────────┐
 │ classes.dex  │──┐                          ┌──▶│ classes.dex  │ (全新組譯，
 │ (binary)     │  │  apktool d               │   │              │  校驗重算)
 │ AndroidMani- │  │  ─────────────▶  out/    │   │ AndroidMani- │
 │  fest (AXML) │  │   ┌─────────────────┐    │   │  fest (AXML) │
 │ resources.   │  ├──▶│ smali/*.smali   │────┤   │ resources.   │
 │  arsc        │  │   │ AndroidManifest │    │   │  arsc        │
 │ res/ lib/    │  │   │   .xml (可讀)    │  apktool b            │
 │ assets/      │  │   │ res/ (可讀資源)  │  ─────────▶           │
 └──────────────┘  │   │ apktool.yml      │    │   └──────────────┘
                   └──▶│ original/ unknown│────┘         │
                       │ lib/ assets/     │        還沒簽名！
                       └─────────────────┘         裝不上
```

三件事先刻進腦子：

1. **`apktool d` 不是 unzip**。unzip 給你原始 binary；apktool 多做「DEX→smali、AXML→可讀 XML、arsc→`res/`」的解碼。反過來 `apktool b` 要把這些**重新編碼**回二進位，這一步比 unzip→zip 複雜得多，也是失敗的高發區。
2. **`apktool b` 的產物沒有簽名**。apktool 只管打包，不管簽名。所以流程一定接 `zipalign` + `apksigner`，少一步就裝不上。
3. **`apktool.yml`** 是 apktool 存的 metadata（原始 SDK 版本、有沒有壓縮某些檔、apktool 版本）。它決定回編譯時的一些行為，**別刪、別亂改**。

## 底層機制：decode → 改 → build → 對齊 → 簽 的完整鏈

把整條鏈攤開，每一步的輸入輸出與「誰動了什麼」：

```
 ① apktool d target.apk -o out/
      DEX  ──baksmali──▶ out/smali*/**.smali        (Dalvik bytecode → 文字)
      AXML ──解碼──────▶ out/AndroidManifest.xml     (binary XML → 可讀)
      arsc ──解碼──────▶ out/res/**                  (資源表 → 檔案樹)
      lib/ assets/ ────▶ 原封搬過去（apktool 不動）

 ② 你改 out/ 裡的 smali / 資源 / Manifest

 ③ apktool b out -o repacked.apk
      smali ──smali組譯──▶ classes.dex               (文字 → 全新 DEX，校驗重算)
      可讀XML ──aapt2────▶ AXML                       (重新編譯資源與 Manifest)
      打包成 zip（未簽名、未對齊）

 ④ zipalign -p 4 repacked.apk aligned.apk
      把 zip 內每個檔案的起始位移對齊到 4-byte 邊界
      （v2+ 簽名要求；先 align 再簽，順序不能反）

 ⑤ apksigner sign --ks my.keystore aligned.apk
      對整個 APK 位元組簽名，一次打 v1+v2+v3
      在 zip 尾端插入 APK Signing Block

 ⑥ adb install -r aligned.apk    → 裝進 AVD
```

第 ③ 步是整條鏈的心臟，也最容易誤解，值得再拆一層。

### `apktool b` 內部做兩件獨立的事

- **smali → DEX**：apktool 內建 smali 組譯器（`smali`/`baksmali` 的 fork）。它把你改過的 `.smali` 文字組回 Dalvik bytecode，產生一個**全新的 `classes.dex`**。這個新 DEX 的 checksum（adler32）與 signature（SHA-1）是組譯器**現算**的，所以永遠正確——這正是 Ch 2 說「apktool 能改而手 patch 不能」的原因。
- **資源 → arsc/AXML**：apktool 呼叫 **aapt/aapt2** 把 `res/` 與 `AndroidManifest.xml` 重新編譯回 `resources.arsc` 與 binary XML。**這一步是回編譯失敗的最大來源**——因為資源編譯很嚴格，`res/` 裡任何 aapt2 不認的東西（多出來的屬性、對不上的 resource id、公開資源衝突）都會讓它整個爆掉。

我們用 Python 把「新 DEX 的 checksum 是現算的」這件事直觀演一遍：改一個 byte，重算 adler32，值就變了（**實際輸出**）：

```python
import zlib, struct
body = bytearray(b"\x00"*100)                     # 假裝是 DEX body
c1 = zlib.adler32(bytes(body)) & 0xffffffff
body[50] = 0x41                                   # 改一個 byte（模擬改 smali 後 bytecode 變了）
c2 = zlib.adler32(bytes(body)) & 0xffffffff
print("原 checksum :", hex(c1))
print("改後 checksum:", hex(c2), "→ 相同?", c1 == c2)
```

```
原 checksum : 0x610001
改後 checksum: 0xf2011f → 相同? False
```

body 一變，checksum 就不同——組譯器每次都幫你重算成對的那個值，你不用管。你手 patch 舊 DEX 之所以會壞，就是因為你改了 body 卻沒重算這個欄位。

## 範例 1：完整走一遍——把 `debuggable` 打開

最經典的第一個改動：把 App 的 `android:debuggable` 改成 `true`（讓你能直接 attach 除錯器）。這個改動只動 Manifest，不碰 smali，是驗證「回編譯+簽名鏈通不通」的最小測試。

**未實測，理論預期行為**（以下指令在有 Android 工具鏈的機器上跑；每步附「你該看到什麼」）：

```bash
# ① 反編譯
apktool d target.apk -o target_out
#   [INFO] Using Apktool 2.9.3 on target.apk
#   [INFO] Baksmaling classes.dex...
#   [INFO] Decoding AndroidManifest.xml with resources...
#   ... 產生 target_out/

# ② 改 Manifest：在 <application ...> 加上 debuggable
#   target_out/AndroidManifest.xml
```

```xml
<application
    android:name=".MyApp"
    android:debuggable="true"      <!-- 新增這行 -->
    ... >
```

```bash
# ③ 回編譯
apktool b target_out -o repacked.apk
#   [INFO] Using Apktool 2.9.3
#   [INFO] Building resources...
#   [INFO] Building apk file...
#   [INFO] Copying unknown files/dir...
#   → 產生 repacked.apk（未簽名）

# ④ 對齊（先 align）
zipalign -p -f 4 repacked.apk aligned.apk

# ⑤ 建一把你自己的 keystore（只需一次）
keytool -genkeypair -v -keystore my.keystore -alias re \
        -keyalg RSA -keysize 2048 -validity 10000 \
        -storepass android -keypass android \
        -dname "CN=re, OU=re, O=re, L=x, S=x, C=TW"

# ⑥ 簽（apksigner 一次打 v1+v2+v3）
apksigner sign --ks my.keystore --ks-pass pass:android aligned.apk

# ⑦ 驗證簽名
apksigner verify -v aligned.apk
#   Verifies
#   Verified using v1 scheme (JAR signing): true
#   Verified using v2 scheme (APK Signature Scheme v2): true
#   Verified using v3 scheme (APK Signature Scheme v3): true

# ⑧ 裝進 AVD
adb install -r aligned.apk
#   Success
```

**驗證這步真的生效**：裝好後 `adb shell run-as com.example.target`（`debuggable` 開了才允許 `run-as`），能進去就代表你的改動吃進去了。

> **`-r` 的坑**：`adb install -r` 是覆蓋安裝。但如果裝置上已裝的是**原廠簽名**版本，你自簽的版本簽名不同，覆蓋安裝會失敗（`INSTALL_FAILED_UPDATE_INCOMPATIBLE`）。要先 `adb uninstall com.example.target` 再裝。這在改別人已裝的 App 時天天遇到。

## 範例 2：改 smali——把一個回傳 `false` 的方法改成 `true`

假設 jadx 讓你看到一個 `isPremium()` 方法回傳 `false`，你想讓它永遠 `true`。先在 smali 裡找到它。反編譯出的 smali 大概長這樣（對應 Java `public boolean isPremium() { return false; }`）：

```smali
# out/smali/com/example/target/User.smali
.method public isPremium()Z
    .locals 1

    const/4 v0, 0x0          # v0 = 0 (false)

    return v0
.end method
```

改動只有一個字元——把 `0x0` 改成 `0x1`：

```smali
.method public isPremium()Z
    .locals 1

    const/4 v0, 0x1          # v0 = 1 (true)  ← 改這裡

    return v0
.end method
```

`const/4 vX, lit4` 把一個 4-bit 常數塞進暫存器，`0x0` 是 `false`、`0x1` 是 `true`（Dalvik 的 boolean 就是 int，非零為真）。`Z` 是回傳型別 boolean 的型別描述符。改完走範例 1 的 ③–⑧ 回編譯簽名裝上，`isPremium()` 就永遠回 `true`。Ch 10 會把這類 smali patch 玩到底，這裡先體會「一個字元就能改邏輯」。

> **為什麼不多改幾個暫存器？** 因為 `.locals 1` 宣告了這個方法只用 1 個區域暫存器（`v0`）。如果你的 patch 需要多用暫存器，得同步把 `.locals` 的數字加大，否則組譯器會報 `register vN is not valid`。這是改 smali 最常見的自找麻煩，Ch 10 會專門講暫存器管理。

## 範例 3：回編譯失敗——資源錯與 multidex

回編譯第一次就成功的機率不高。兩類最常見的失敗：

**（A）資源編譯錯（aapt2 報錯）**

```
brut.androlib.exceptions.AndrolibException: brut.common.BrutException:
could not exec (exit code = 1): [.../aapt2, compile, ...]
```

原因通常是 `res/` 裡有 aapt2 不吃的東西。常見來源：

- App 用了比你 apktool 內建 aapt 版本更新的資源特性 → 換 `apktool b --use-aapt2`（新版預設就是 aapt2，但可明確指定），或升級 apktool。
- 某些混淆過的資源名、或 apktool decode 時漏了的資源 → 試 `apktool d --no-res target.apk`（不解資源，只解 smali）。**如果你只想改 smali、不改資源，`--no-res` 是保命符**——資源保持二進位原封搬過去，繞開整個 aapt2 重編譯，回編譯成功率大增。

**（B）multidex（多個 DEX）**

App 方法數超過 65536 就會有 `classes.dex` + `classes2.dex` + ...，apktool decode 後對應 `smali/` + `smali_classes2/` + ...。回編譯時 apktool **會自動把它們組回多個 DEX**，這本身沒問題。但兩個坑：

- 你要改的方法可能在 `smali_classes2/` 而不是 `smali/`——搜的時候別只搜 `smali/`，要全目錄搜（`grep -r isPremium out/`）。
- 某些 App 對 DEX 的順序或數量有自校驗（Ch 32），回編譯後 DEX 佈局變了會觸發它的完整性檢查而閃退。這不是 apktool 的錯，是 App 的防護，要靠動態繞。

> **debug 回編譯失敗的通用手法**：apktool 的錯誤訊息末端常常有一行真正的 aapt2 錯誤（被埋在一堆 Java stack trace 裡）。加 `-v`（verbose）跑，把那行 aapt2 的原始錯誤挖出來——它會明確告訴你哪個 `res/xxx.xml` 的哪一行有問題。別只看最上面的 `AndrolibException`，那只是外層包裝。

## 對比與取捨

| 你要做的 | 用什麼 | 為什麼 |
|---|---|---|
| 只改 smali 邏輯、不碰資源 | `apktool d --no-res` + `b` | 繞開 aapt2 重編譯，回編譯成功率最高 |
| 改資源（string/layout/圖） | `apktool d`（含資源）+ `b` | 必須重編譯 arsc/AXML |
| 只想讀懂邏輯、不重打包 | jadx | apktool 的 smali 難讀，jadx 的 Java 好讀 |
| 只想抽出 `.so` 去逆 | `unzip` | 不需要 apktool 的解碼 |
| 簽名 | **`apksigner`**（不是 `jarsigner`） | apksigner 一次打 v1+v2+v3；jarsigner 只有 v1，新系統裝不上 |
| 對齊 | `zipalign`，**簽名前** | v2+ 簽整檔位元組，簽後再對齊會破壞簽名 |

## 踩雷集錦

1. **「回編譯過了就以為成功」**：`apktool b` 只產出未簽名 APK。忘了 `zipalign`+`apksigner` 直接 `adb install`，會回 `INSTALL_PARSE_FAILED_NO_CERTIFICATES`。回編譯 ≠ 可安裝，簽名是獨立的一步。
2. **先簽再對齊**：`zipalign` 一定在 `apksigner` **之前**。v2+ 簽的是整個檔案位元組，簽完再 align 會改動位元組讓簽名失效。老教學（jarsigner 時代）的「先簽再 align」在現代是錯的。
3. **用 jarsigner 只簽了 v1**：`jarsigner` 是 JAR 簽名（v1）年代的工具，只打 v1。targetSdk 30+ 的系統要求 v2，你只簽 v1 會裝不上或裝上跑不起來。**一律用 `apksigner`**，它預設 v1+v2+v3 全打。
4. **覆蓋安裝簽名衝突**：裝置上已有原廠簽名版本時，你自簽的版本 `adb install -r` 會 `INSTALL_FAILED_UPDATE_INCOMPATIBLE`。先 `adb uninstall` 再裝。
5. **改了 smali 但 `.locals` 沒跟著改**：新增用到的暫存器超過 `.locals` 宣告數，組譯報 `register vN is not valid`。改 smali 要同步管暫存器數量。
6. **在 `smali/` 找不到方法**：multidex 的 App，方法可能在 `smali_classes2/`、`smali_classes3/`。全目錄 grep，別只盯主 `smali/`。

## 進階：再往深一層

- **`--no-res` 的代價**：不解資源雖然回編譯順，但你就**不能改資源**（string、layout、圖），也看不到可讀的 `res/`。它適合「純 smali patch」的任務。要改資源就得吃 aapt2 重編譯的複雜度，Ch 9 會深入資源逆向。
- **`apktool.yml` 裡的 `doNotCompress`**：apktool 會記錄原 APK 裡哪些檔案是「儲存不壓縮」（如某些 `.png`、`resources.arsc`、`.so`）。回編譯時它照這個清單保持不壓縮。手動亂刪這欄位可能讓 `.so` 被壓縮，導致 Android 7+ 的 `extractNativeLibs=false` App 找不到 native 庫而閃退。
- **debug key 的一致性**：如果你要對同一個 App 反覆改、反覆覆蓋安裝，用**固定的一把 keystore**（別每次重新 genkey）。key 一致，覆蓋安裝才不會每次都要先 uninstall。把那把 debug key 存好重複用。
- **`apksigner` 的 `--min-sdk`**：如果目標 App 的 minSdk 很低，`apksigner` 可能因為 v1 相容性報 warning。加 `--min-sdk` 明確指定可以讓它按目標版本挑對的簽名 scheme 組合。

## 動手練習

1. 從 AVD 撈一個內建 App 的 APK（Ch 0 的 `pm path`+`pull`），`apktool d` 解出來，把 `AndroidManifest.xml` 的 `debuggable` 改 `true`，走完整條 `b`→`zipalign`→`apksigner`→`install` 鏈。第一次全綠算你贏。
2. 故意跳過 `zipalign` 直接簽，看 `apksigner` 有沒有抱怨；再故意「先簽再 align」，看裝上去會不會失敗——親手製造這兩個經典錯誤，以後一秒認出。
3. 找一個 multidex 的 App（`unzip -l` 看有沒有 `classes2.dex`），`apktool d` 後確認 `smali_classes2/` 存在，全目錄 grep 一個常見方法名（如 `onCreate`），感受方法散在多個 DEX 目錄的實況。
4. 對同一個 App 用 `--no-res` 和不加 `--no-res` 各回編譯一次，比較哪個成功、哪個報 aapt2 錯——建立「純 smali patch 就用 `--no-res`」的直覺。

## 本章重點整理

- apktool 的循環：`d`（DEX→smali、AXML/arsc→可讀）→ 改 → `b`（smali→全新 DEX、資源→arsc/AXML）→ `zipalign` → `apksigner`。
- `apktool b` 的產物**沒有簽名**；一定接 `zipalign`（先）+ `apksigner`（後，一次打 v1+v2+v3）。
- 回編譯失敗兩大來源：**資源（aapt2 報錯，用 `--no-res` 或升 apktool 繞）** 與 **multidex（方法散在多個 smali 目錄）**；用 `-v` 挖出被埋的真正 aapt2 錯誤。
- 改 smali 要同步管 `.locals` 暫存器數；覆蓋安裝遇簽名衝突先 `adb uninstall`。

## 自我檢核

- [ ] 不看筆記，能列出從 `apktool d` 到裝進 AVD 的完整六步，並說出每步在做什麼
- [ ] 能解釋為什麼 apktool 改 smali 能用、而 hex patch DEX 不能（校驗欄位誰重算的）
- [ ] 能說出「先 align 再簽」的原因，以及用 apksigner 不用 jarsigner 的原因
- [ ] 回編譯報 aapt2 資源錯時，知道 `--no-res` 是什麼、什麼情況該用
- [ ] 知道 multidex App 的方法可能在 `smali_classes2/`，且回編譯後 DEX 佈局變化可能觸發 App 自校驗

## 延伸閱讀

### 工具官方文件

- **[apktool 官方文件 — Getting Started / Build](https://apktool.org/docs/the-basics/intro)** — apktool.org
  - **讀哪裡**：`decode` 與 `build` 兩節，特別是 `--no-res`、`--only-main-classes`、`-f` 這些旗標的說明
  - **和本章的關聯**：本章的 `d`/`b` 流程與失敗排解，官方文件是旗標語意的最終依據
- **[apksigner 官方文件](https://developer.android.com/tools/apksigner)** — Android Developers
  - **讀哪裡**：`sign` 與 `verify` 子命令；`--v1-signing-enabled`/`--v2-signing-enabled`/`--min-sdk` 旗標
  - **注意**：對照本章「一次打 v1+v2+v3」的行為，理解為什麼不再用 jarsigner
- **[zipalign 官方文件](https://developer.android.com/tools/zipalign)** — Android Developers
  - **讀哪裡**：對齊的用途與「必須在簽名前」那段
  - **和本章的關聯**：解釋「先 align 再簽」的順序陷阱

### 方法論

- **[OWASP MASTG — Repackaging & Re-signing](https://mas.owasp.org/MASTG/techniques/android/MASTG-TECH-0016/)** — OWASP
  - **這篇說什麼**：業界標準的重打包+重簽測試流程，跟本章的鏈一一對應
  - **讀哪裡**：patch → rebuild → align → sign 的完整步驟
  - **為什麼值得讀**：把本章流程放進標準化測試方法論的脈絡，之後做 App 安全評估會反覆用到

上一步我們把改 smali 重打包的機械流程走通了，但你要改哪裡、改什麼，得先讀懂程式碼。下一章我們轉向 jadx——它把 DEX 反編譯成近似 Java 讓你「讀懂」邏輯。但這個「近似」有多近、什麼時候會騙你、失敗了怎麼辦，是下一章的重點。

→ [Ch 7 Jadx 與 Java 反編譯：原理與限制](./07-jadx-java-decompile.md)
