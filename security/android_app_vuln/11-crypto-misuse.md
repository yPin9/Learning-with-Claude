# Ch 11 — 密碼學誤用

> **目標**：讓你看到「有加密」不等於「安全」。App 開發者用了 AES、用了 SHA、呼叫了 Keystore，卻因為 **ECB mode 洩漏明文 pattern、金鑰/IV 硬編在程式裡、IV 固定、用 `Random` 而非 `SecureRandom`、`PBEWithMD5` 這種石器時代 KDF、Keystore 沒硬體背書也沒設 user auth**——這些誤用讓密碼學形同虛設。本章教你辨識每一種誤用、理解它為什麼破功、以及怎麼在逆向時把金鑰挖出來。ECB 洩漏 pattern 這件事我用 Python **實際跑給你看**。

> **環境**：ECB pattern 洩漏、弱亂數可預測、base64 這些純演算法示範用 **Python 3.12** 在本機**實際跑出**（不需 Android），標「**實際輸出**」。Android Keystore 的硬體背書、user auth、TEE/StrongBox 行為需要真機/AVD，標「**未實測，理論預期行為**」並附驗證步驟。

## 為什麼需要這個？

因為**密碼學誤用是「看起來做對了、其實沒有」的重災區**，而它的隱蔽性正是危險所在。上一章（不安全儲存）的洞是「明文攤在那」，一眼看得出來；這一章的洞是「有一層加密殼，但這層殼漏洞百出」——開發者以為加了 AES 就安全，安全評估者若不懂密碼學也可能放過。你要能穿透這層假象。

而且這是**逆向能力直接變現**的地方。App 把值錢的東西（API 簽名金鑰、憑證、加密的本地資料）藏起來時，用的往往就是這些誤用的密碼學。你逆出「它用 AES-ECB、金鑰硬編在某個 native 函式裡」，就等於拿到了解密/偽造的鑰匙。MASVS 的 **MASVS-CRYPTO** 整類講的就是這個，而現實中它的違反率高得驚人——因為正確用密碼學很難，錯誤用密碼學卻很容易編過、跑得動、測起來「有加密」。

密碼學誤用的核心心智模型：**密碼學的安全性不在演算法本身，在你怎麼用它**。AES 是安全的，AES-ECB 不安全；隨機是安全的前提，固定 IV 讓隨機性歸零；金鑰是一切的根本，硬編金鑰讓整個系統的安全性等於「沒有」。

## 先建立直覺：一個加密系統會在哪裡破功

把「App 加密一段資料」拆成它依賴的每個環節，每個環節都是一個可能的誤用點：

```
明文 ──┐
       ├──▶ [ 演算法選對嗎? ]  AES ✓ / DES,RC4 ✗ / 自製 ✗
       │        │
       │        ▼
       ├──▶ [ mode 選對嗎? ]   GCM,CBC ✓ / ECB ✗（洩漏 pattern）
       │        │
       │        ▼
       ├──▶ [ IV 對嗎? ]       每次隨機 ✓ / 固定,硬編,全 0 ✗
       │        │
       │        ▼
       ├──▶ [ 金鑰哪來? ]      Keystore 硬體背書 ✓ / 硬編,弱KDF ✗ ◀── 最致命
       │        │
       │        ▼
       └──▶ [ 亂數來源? ]      SecureRandom ✓ / Random,固定seed ✗
                │
                ▼
             密文（安全性 = 上面最弱的那一環）
```

一句話：**這條鏈的安全性等於最弱一環**。演算法選 AES-256 很威風，但如果金鑰硬編在 smali 裡、或 IV 固定、或 mode 是 ECB，前面選多強的演算法都沒意義。逆向時你就是在找這條鏈最弱的那一環——而它幾乎總是**金鑰的來源**或 **mode/IV 的選擇**。

下面從最直觀、我能實跑給你看的 ECB 開始。

## 誤用一：ECB mode 洩漏明文 pattern（Python 實跑）

**ECB（Electronic Codebook）** 是最簡單的分組加密 mode：把明文切成 16-byte 區塊，每塊**獨立**用同一把金鑰加密。問題就在「獨立」和「同一把金鑰」——**相同的明文區塊，永遠產生相同的密文區塊**。這洩漏了明文的結構：哪裡有重複、哪裡是同一段資料，密文裡一目了然。

這就是有名的「**ECB penguin**」：一張企鵝圖用 ECB 加密後，你還是看得出企鵝輪廓——因為圖裡大片相同顏色的區塊，加密後還是大片相同的密文。加密了，但 pattern 沒藏住。

我用純 Python 實作 AES-128-ECB，加密一段**含重複區塊**的明文，看密文（**實際輸出**）：

```python
# 明文：3 個相同的 "AAAA..." 區塊 + 1 個 "BBBB..." + 2 個 "AAAA..."
# 每個字母重複 16 次 = 剛好一個 16-byte AES 區塊
pt = b"AAAAAAAAAAAAAAAA"*3 + b"BBBBBBBBBBBBBBBB" + b"AAAAAAAAAAAAAAAA"*2
ct = aes_ecb(pt, key=b"0123456789abcdef")
for i in range(0, len(ct), 16):
    print(f"block {i//16}: {ct[i:i+16].hex()}")
```

**實際輸出**：

```
block 0: 3bfd04cc0d7ed55358e2cbe19de21383
block 1: 3bfd04cc0d7ed55358e2cbe19de21383
block 2: 3bfd04cc0d7ed55358e2cbe19de21383
block 3: 64d87547cf781a845aa4b1e907e82051
block 4: 3bfd04cc0d7ed55358e2cbe19de21383
block 5: 3bfd04cc0d7ed55358e2cbe19de21383
```

看清楚了嗎——**block 0、1、2、4、5 的密文完全相同**（都是明文 `AAAA...`），block 3（明文 `BBBB...`）不同。密文一個位元組都沒讓你解密，卻已經洩漏了「這 6 個區塊裡，第 4 個跟其他不一樣」。如果明文是結構化資料（例如一張圖、一份有重複欄位的記錄、一段有固定格式的 token），ECB 就把它的結構印在密文上。這就是為什麼**任何嚴肅的加密都不該用 ECB**。

逆向時怎麼辨識 ECB？Java 層搜 `Cipher.getInstance` 的參數：

```java
// 危險：明確指定 ECB
Cipher c = Cipher.getInstance("AES/ECB/PKCS5Padding");
// 更陰險：只寫 "AES"，預設就是 ECB！
Cipher c = Cipher.getInstance("AES");   // ← 等同 AES/ECB/PKCS5Padding
```

> **`Cipher.getInstance("AES")` 是個陷阱**：只寫演算法名不寫 mode，Java（JCA）預設補上 **ECB**。所以看到 `"AES"`、`"DES"`、`"AES/ECB/..."` 都是 ECB。很多開發者以為「我沒指定 ECB 啊」，但省略 mode 就是 ECB。這是 MASTG 明列的檢測點。

## 誤用二：硬編金鑰與 IV

密碼學的第一鐵律：**金鑰要保密**。但 App 是要發到使用者手上、可被逆向的——**任何硬編在 APK 裡的金鑰，本質上都是公開的**。開發者硬編金鑰通常是為了「加密本地資料」或「跟伺服器對稱加密」，但這把金鑰躺在 smali/native 裡等你來撈。

```java
// 教科書級的反面教材
private static final String KEY = "MySecretKey12345";     // ← 硬編金鑰
private static final byte[] IV = "1234567890123456".getBytes();  // ← 硬編 IV
SecretKeySpec sk = new SecretKeySpec(KEY.getBytes(), "AES");
Cipher c = Cipher.getInstance("AES/CBC/PKCS5Padding");
c.init(Cipher.ENCRYPT_MODE, sk, new IvParameterSpec(IV));
```

**怎麼挖**（由淺到深）：

```bash
# 1. 最粗暴：strings 掃常數字串（下一章 Ch 12 系統化講）
strings classes.dex | grep -iE "key|secret|iv"

# 2. jadx 搜 SecretKeySpec / IvParameterSpec，順著看金鑰哪來
#    金鑰若是字面量 → 直接抄；若是 base64/hex 常數 → decode 出來

# 3. 金鑰在 native？逆 .so 找（見 android_reversing Ch 23 native 演算法識別）
```

金鑰常被「藏」一下（base64、XOR、拆成幾段拼接）以為這樣安全——**這是「隱藏不是加密」的典型誤解**。base64 只是編碼，一秒 decode（**實際輸出**）：

```python
>>> import base64
>>> base64.b64decode("TXlTZWNyZXRLZXkxMjM0NQ==")
b'MySecretKey12345'
```

無論怎麼混淆，金鑰終究要在執行期還原成真正的 bytes 餵給 `Cipher.init`。所以**最穩的挖法是 Frida hook**：不管金鑰在程式碼裡被怎麼藏，hook `SecretKeySpec` 建構子或 `Cipher.init`，執行期它自己把明文金鑰交給你。

```javascript
// Frida：攔 SecretKeySpec 建構子，印出真正用的金鑰 bytes
Java.perform(function () {
    var SKS = Java.use("javax.crypto.spec.SecretKeySpec");
    SKS.$init.overload("[B", "java.lang.String").implementation = function (keyBytes, algo) {
        console.log("[key] algo=" + algo + " bytes=" + bytesToHex(keyBytes));
        return this.$init(keyBytes, algo);
    };
});
```

> **靜態藏金鑰 vs 動態撈金鑰**：開發者可以把金鑰拆八段、XOR、放 native、動態拼——這些都能**拖慢**靜態逆向，但擋不住動態。因為 CPU 最終要拿到真金鑰才能加密，Frida 在那一刻攔下就好。這跟 Ch 1 說的「加固殼執行期一定得還原真程式碼」是同一個道理：**要用的東西，執行期一定得是明的**。

## 誤用三：固定 IV / 弱亂數（Python 實跑）

**IV（Initialization Vector）** 在 CBC/CTR/GCM 等 mode 裡的作用是「讓同樣的明文+同樣的金鑰，每次加密產生不同的密文」。IV 的要求是**每次加密都用新的、隨機的（CBC 要求不可預測，GCM 要求不重複）**。固定 IV（硬編、全 0、寫死一個常數）等於把這個保護關掉——同明文同金鑰又產生同密文，退化成類似 ECB 的 pattern 洩漏，GCM 更是**重用 IV 直接災難性破密**（可還原金鑰流）。

```java
// 固定 IV：每次都用同一個，等於沒有 IV 的作用
byte[] iv = new byte[16];   // ← 全 0 IV，也是固定 IV
c.init(Cipher.ENCRYPT_MODE, sk, new IvParameterSpec(iv));
```

而 IV/金鑰/token/nonce 的隨機性，取決於**亂數來源**。Java 有兩個：

- **`java.security.SecureRandom`**：密碼學安全的亂數（CSPRNG）。✓ 該用這個。
- **`java.util.Random`**：一個 48-bit 種子的**線性同餘產生器（LCG）**，**可預測**。✗ 拿它產金鑰/IV/token 是嚴重誤用。

`java.util.Random` 有多可預測？它是純確定性的 LCG——**同一個種子產生完全相同的序列**。我用 Python 實作 Java 的 LCG 演算法驗證（**實際輸出**）：

```python
class JavaRandom:   # 複刻 java.util.Random 的 48-bit LCG
    def __init__(self, seed): self.seed = (seed ^ 0x5DEECE66D) & ((1<<48)-1)
    def next(self, bits):
        self.seed = (self.seed*0x5DEECE66D + 0xB) & ((1<<48)-1)
        r = self.seed >> (48-bits)
        return r-(1<<32) if r >= (1<<31) else r
    def nextInt(self): return self.next(32)

print([JavaRandom(1234567).nextInt() for _ in "abc"] and
      "outputs:", [ (r:=JavaRandom(1234567)).nextInt() for _ in range(3)])
print("replay  :", [ (r:=JavaRandom(1234567)).nextInt() for _ in range(3)])
```

**實際輸出**：

```
outputs: [1042961893, -1571432423, -1065072994]
replay : [1042961893, -1571432423, -1065072994]
```

兩次用同一個種子 `1234567`，序列**一模一樣**。更糟的是：`java.util.Random` 只有 48-bit 內部狀態，攻擊者看到一兩個輸出就能還原種子、往後預測全部。所以若 App 拿 `new Random()`（用系統時間當種子，攻擊者可猜的時間範圍）產「隨機」token/OTP/session id，這些值是**可預測**的。逆向時搜 `new Random(` 與 `Math.random()`（後者底層也是 `Random`），用在安全相關處就是洞。

> **`0x5DEECE66D` 和 `0xB` 是什麼**：這兩個是 `java.util.Random` LCG 的**乘數與增量常數**，寫死在 JDK 的規格裡（`Random` 的 Javadoc 明列）。任何人都知道這兩個常數——這正是問題所在：演算法公開 + 狀態只有 48-bit + 可從輸出反推 = 完全不適合密碼學用途。

## 誤用四：弱 KDF（PBEWithMD5、低迭代）

當金鑰要從**密碼/passphrase** 導出時（例如用使用者密碼加密本地資料），要用 **KDF（Key Derivation Function）**。KDF 的重點是**慢**——刻意做很多次雜湊迭代，讓暴力破解密碼變昂貴。誤用有幾種：

```java
// 反面教材：PBEWithMD5AndDES —— MD5 已破、DES 只有 56-bit 金鑰
SecretKeyFactory f = SecretKeyFactory.getInstance("PBEWithMD5AndDES");
// 或 PBKDF2 但迭代次數太低
PBEKeySpec spec = new PBEKeySpec(pwd, salt, 1000, 128);  // 1000 次太少
```

| KDF 寫法 | 問題 |
|---|---|
| `PBEWithMD5AndDES` | MD5 弱雜湊 + DES 56-bit 金鑰，兩頭都爛 |
| `PBKDF2` 迭代 < 10000 | 迭代太少，暴力破解便宜（現代建議 ≥ 600000，OWASP 2023） |
| 沒有 salt / 固定 salt | 可用 rainbow table，同密碼產同金鑰 |
| 直接 `MD5(password)` 當金鑰 | 沒 KDF、沒 salt、沒迭代，最糟 |

逆向時搜 `PBEWith`、`PBKDF2`、`MessageDigest.getInstance("MD5")`、`getInstance("SHA-1")`。看到金鑰是「密碼直接雜湊一次」就是弱 KDF。

## 誤用五：Android Keystore 誤用

**Android Keystore** 是「做對」的方向——它把金鑰存在系統的安全區，理想上金鑰**永遠不出 TEE（Trusted Execution Environment）/ StrongBox**，App 只能「請 Keystore 用這把金鑰做加解密」而拿不到金鑰本身。但 Keystore 也充滿誤用，讓這層保護打折甚至歸零：

```java
// 用了 Keystore，但沒設任何額外保護
KeyGenParameterSpec spec = new KeyGenParameterSpec.Builder(
        "myKey", KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
    .setBlockModes(KeyProperties.BLOCK_MODE_CBC)      // 沒用 GCM
    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_PKCS7)
    // .setUserAuthenticationRequired(true)  ← 沒設！任何時候都能用這把金鑰
    // .setIsStrongBoxBacked(true)           ← 沒設！可能只在軟體實作
    .build();
```

**常見誤用**：

1. **沒設 `setUserAuthenticationRequired(true)`**：金鑰任何時候都能用。攻擊者只要能讓 App 跑起來、或 Frida 觸發那段解密邏輯，Keystore 照樣乖乖解密給他——因為它不要求「使用者剛剛驗證過生物辨識/PIN」。對高敏資料（付款、私鑰），這是關鍵缺失。
2. **未確認硬體背書**：Keystore 不保證金鑰在硬體裡。若裝置沒 TEE/StrongBox，金鑰可能落在**軟體實作**，保護大打折。要用 `KeyInfo.isInsideSecureHardware()`（舊）/ `getSecurityLevel()`（API 31+）確認，並可要求 `setIsStrongBoxBacked(true)`。
3. **金鑰本身安全，但加解密邏輯可被 Frida 繞**：Keystore 不讓你拿金鑰，但你可以 hook App 呼叫 Keystore 解密後的**回傳明文**——金鑰沒外洩，但明文被你攔到。這是「Keystore 不是萬能」的關鍵理解。

> **Keystore 誤用的本質**：Keystore 保護的是「**金鑰不外洩**」，不保護「**加解密結果不外洩**」。你 hook `Cipher.doFinal` 的回傳值，就拿到解密後的明文，金鑰始終在 TEE 裡沒動——但你要的資料已到手。所以「用了 Keystore」≠「Frida 撈不到資料」。真正提高門檻的是 `setUserAuthenticationRequired` + StrongBox + 把敏感操作綁生物辨識，讓「觸發解密」本身就需要使用者在場。

**驗證步驟（你在 AVD/真機上做，未實測，理論預期行為）**：(1) jadx 找 `KeyGenParameterSpec.Builder`，看有沒有 `setUserAuthenticationRequired`、`setIsStrongBoxBacked`；(2) Frida hook `Cipher.doFinal`（`javax.crypto.Cipher`）印回傳的明文 bytes，確認能不能在「未經使用者驗證」下拿到解密結果；(3) hook `KeyInfo` 或讀 `getSecurityLevel()` 看金鑰是否 `SECURITY_LEVEL_STRONGBOX` / `TRUSTED_ENVIRONMENT` 還是 `SOFTWARE`。AVD（google_apis）通常是軟體/模擬 TEE，硬體背書行為要真機才準——這點在報告要註明。

## 對比與取捨

| 誤用 | 為什麼破功 | 靜態辨識關鍵字 | 挖取/驗證方式 |
|---|---|---|---|
| ECB mode | 相同明文→相同密文，洩漏 pattern | `"AES"`、`"AES/ECB"`、`"DES"` | 加密結構化資料看密文重複；Frida 確認 mode |
| 硬編金鑰/IV | APK 可逆，金鑰=公開 | `SecretKeySpec`、`IvParameterSpec` 字面量 | strings/jadx 挖；Frida hook 建構子 |
| 固定 IV | 同明文→同密文；GCM 重用災難 | `new byte[16]`、常數 IV | 比對兩次密文是否相同 |
| 弱亂數 `Random` | LCG 可預測，48-bit 狀態 | `new Random(`、`Math.random()` | 用 JavaRandom 復刻預測序列 |
| 弱 KDF | MD5/低迭代/無 salt，暴破便宜 | `PBEWithMD5`、`PBKDF2` 低迭代 | 看迭代數、salt 來源 |
| Keystore 誤用 | 不設 user auth / 無硬體背書 / 可 hook 明文 | 缺 `setUserAuthenticationRequired` | Frida hook `doFinal` 攔明文 |

**取捨的主軸是「靜態能看多少、什麼時候非動態不可」**：mode、演算法、金鑰若是字面量——靜態就能定案。但金鑰被動態拼、走 native、或走 Keystore——這時 Frida hook `SecretKeySpec`/`Cipher.init`/`doFinal` 是最省力的通殺解，因為執行期一切都得是明的。

## 踩雷集錦

1. **看到 `Cipher.getInstance("AES")` 以為沒指定 mode 就沒事**：JCA 預設補 **ECB**。只寫演算法名 = ECB。這是最容易漏判的一個。
2. **以為金鑰做了 base64/XOR 混淆就挖不出**：混淆只拖慢靜態，執行期金鑰終究要還原成真 bytes。Frida hook `SecretKeySpec` 建構子直接拿明文金鑰，別在靜態解混淆上耗太久。
3. **把 `Random` 和 `SecureRandom` 當同一個東西**：只差一個字，安全性天差地遠。`java.util.Random` 是可預測的 LCG，`java.security.SecureRandom` 才是 CSPRNG。搜到 `new Random(` 用在金鑰/token/IV 就是洞。
4. **「用了 Android Keystore」就標安全**：Keystore 保護金鑰不外洩，但沒設 user auth 時任何觸發都能解密，且你能 hook 解密後的明文。Keystore 用對很難，看有沒有 `setUserAuthenticationRequired` + 硬體背書再下結論。
5. **在 AVD 上驗硬體背書**：AVD（google_apis）多半是軟體/模擬 TEE，`isInsideSecureHardware` 的結果不代表真機。硬體背書相關結論要真機驗，報告註明。
6. **忽略 GCM 的 IV 重用是災難級**：CBC 固定 IV 是「洩漏 pattern」，GCM **重用同一個 (key, IV)** 是可還原認證金鑰、偽造密文的災難。看到 GCM 而 IV 固定/計數器可能繞回，這比 CBC 固定 IV 嚴重得多。

## 進階：再往深一層

- **ECB penguin 不只是圖**：任何有重複結構的明文（固定格式的 protobuf、有 padding 的記錄、重複欄位的 JSON）在 ECB 下都會洩漏結構。實務上你可以用「餵已知明文、觀察密文區塊重複」來反推對齊與區塊邊界——這是 chosen-plaintext 分析的起手式。
- **padding oracle**：CBC + PKCS7 若解密時把「padding 錯誤」和「其他錯誤」用不同回應/時間洩漏出去，攻擊者能在**不知道金鑰**的情況下逐位元組解密整段密文（padding oracle attack）。逆向時若 App 是某個加密協定的 client/server 一端，留意它怎麼回應解密失敗。
- **常數時間比較**：驗 MAC/簽章/token 時用 `Arrays.equals`（逐位元組、一不同就返回）會洩漏時序，理論上可被 timing attack 逐位元組猜。正確做法是常數時間比較（`MessageDigest.isEqual`）。這在本地不好利用，但在網路端點是真攻擊面。
- **provider 與版本差異**：`Cipher.getInstance` 的預設 mode/provider 隨 Android 版本、是否用 BouncyCastle/Conscrypt 而異。逆向下結論前，確認目標的實際 provider——同一行程式碼在不同版本可能行為不同。

## 動手練習

1. 跑本章的 ECB Python 片段（自己補一個 AES 實作或用 `pycryptodome` 的 `AES.new(key, AES.MODE_ECB)`），加密一段含重複區塊的明文，親眼看密文區塊重複。再改成 CBC 隨機 IV，看重複消失。這是「mode 決定安全」最直接的體感。
2. 用 JavaRandom 的 Python 復刻，固定種子產一串「token」，再另起一個同種子的 instance，證明序列完全相同。體會為什麼 `java.util.Random` 產 token 是可預測的。
3. 拿一個靶（DIVA 的 "Input Validation" / crypto 關，或 AndroGoat 的 crypto 關），jadx 找加密邏輯，判斷它用什麼 mode、金鑰哪來。若金鑰硬編就 decode 出來；若動態就寫 Frida hook `SecretKeySpec` 印出金鑰。目標是「拿到金鑰後自己在電腦上解出密文」。
4. 對一個用 Keystore 的 App，jadx 查 `KeyGenParameterSpec.Builder` 的呼叫，列出它設了/沒設哪些保護（user auth、strongbox、block mode）。就算不動態驗，也先從靜態判斷這把金鑰的保護等級。

## 本章重點整理

- 密碼學的安全性**不在演算法、在怎麼用**：AES 安全、AES-ECB 不安全；這條鏈的強度等於最弱一環，而最弱一環幾乎總是**金鑰來源**或 **mode/IV**。
- **ECB 相同明文→相同密文**（本章 Python 實跑驗證），洩漏 pattern；`Cipher.getInstance("AES")` 預設就是 ECB。
- **硬編金鑰=公開金鑰**，混淆只拖慢靜態；Frida hook `SecretKeySpec`/`Cipher.init`/`doFinal` 執行期通殺，因為要用的東西執行期一定是明的。
- **`java.util.Random` 是可預測 LCG**（48-bit 狀態、常數公開），拿它產金鑰/IV/token 是洞；要用 `SecureRandom`。**Keystore 保護金鑰不外洩，不保護明文結果**，沒 user auth + 硬體背書就別標安全。

## 自我檢核

- [ ] 能解釋為什麼 ECB 洩漏 pattern，並說出 `Cipher.getInstance("AES")` 為什麼危險
- [ ] 拿到一個硬編/混淆金鑰的 App，知道靜態怎麼挖、什麼時候該改用 Frida hook 哪個 API
- [ ] 能講清楚 `java.util.Random` 和 `SecureRandom` 的差別，以及為什麼前者不能產密碼學材料
- [ ] 能列出 Android Keystore 的三種常見誤用，並解釋「用了 Keystore 也可能被 Frida 撈到明文」
- [ ] 知道固定 IV 對 CBC 和對 GCM 的後果差別（pattern 洩漏 vs 災難性破密）

## 延伸閱讀

- **[OWASP MASTG — Cryptography in Android Apps](https://mas.owasp.org/MASTG/0x05e-Testing-Cryptography/)** — OWASP
  - **讀哪裡**：ECB/mode 檢測、硬編金鑰、亂數、KDF、Keystore 各節的測試步驟與程式碼樣式
  - **和本章的關聯**：本章每種誤用對應這頁一個 MASTG 測試，是把「密碼學誤用」變成可勾選檢查清單的權威來源；報告引用它的測試編號
- **[Android Developers — Cryptography & Keystore](https://developer.android.com/privacy-and-security/cryptography)** — Android 官方
  - **讀哪裡**：`KeyGenParameterSpec` 的 `setUserAuthenticationRequired`、`setIsStrongBoxBacked`；建議的 mode（GCM）與亂數（SecureRandom）
  - **為什麼值得讀**：從「正確該怎麼做」反推「誤用長什麼樣」；硬體背書與 user auth 的官方定義以此為準
- **[`java.util.Random` Javadoc](https://docs.oracle.com/en/java/javase/17/docs/api/java.base/java/util/Random.html)** — Oracle
  - **讀哪裡**：class 說明開頭，LCG 演算法、`0x5DEECE66D`/`0xB` 常數、"not cryptographically secure" 的明確警告
  - **和本章的關聯**：本章 Python 復刻的 LCG 就照這份規格；讀它你會明白為什麼 48-bit + 公開常數 = 可預測
- **[《Cryptographic Misuse in Android Applications》類研究（Egele et al., CCS 2013 起的一系列）](https://dl.acm.org/doi/10.1145/2508859.2516693)** — 學術
  - **這篇說什麼**：大規模掃描 Android App 的密碼學誤用（ECB、固定 IV、弱 KDF、`Random` 當亂數），量化這些誤用有多普遍
  - **讀哪裡**：它歸納的六條「crypto 使用規則」與違反統計
  - **為什麼值得讀**：讓你相信這些誤用不是教科書假想，而是真實 App 的大宗問題——這是本章存在的理由

下一章我們把「找 secret」這件事系統化——不只是加密金鑰，還有 API key、OAuth token、雲端憑證、第三方 SDK 的認證資訊，藏在 DEX、資源、assets、native 各處。你會學到用 `strings`、apkleaks、正規表達式與 gitleaks 思路把它們批次挖出來。

→ [Ch 12 憑證與 secret 洩漏](./12-secret-leakage.md)
