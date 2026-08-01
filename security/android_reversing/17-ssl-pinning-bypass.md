# Ch 17 — SSL Pinning 與抓包

> **目標**：讓你能看見一個 App 跟伺服器之間到底在說什麼。這條路上有兩道牆：第一道是「HTTPS 加密」，靠中間人代理（mitmproxy）+ 讓裝置信任你的 CA 憑證來破；第二道是 **SSL Pinning**——App 除了信任系統 CA，還額外**只認自己內建的憑證/公鑰**，讓你的 mitmproxy 憑證直接被拒。你會學到 pinning 的三種實作原理（憑證 pinning、公鑰 pinning、`network_security_config`）、系統 CA 的裝法（`-writable-system`）、以及用 objection / Frida 腳本把 pinning 繞掉的完整流程。這章是下一章「協議還原」的前置——看不到流量，就無從還原協議。

> **環境**：AVD（Android 13 / API 33，x86_64，Google APIs，開機帶 `-writable-system`）、`mitmproxy 10.x`、`objection`、`Frida 16.x`。抓包/繞 pinning 涉及裝置與 App 行為，Frida 腳本標「**未實測，理論預期行為**」並附驗證步驟；TLS/pinning 的原理與憑證雜湊計算可用純工具說明，涉及裝置的一律不假裝跑過。

## 為什麼需要這個？

Ch 1 的攻擊鏈第一步就是「抓包看請求長什麼樣」。一個 App 的很多祕密不在程式碼裡，在**它跟伺服器的對話裡**：登入怎麼帶 token、哪個 API 傳了什麼參數、`sign` 是怎麼算出來塞進 header 的。看不到這些對話，你連「要逆哪個函式」都不知道。

問題是現代 App 幾乎全走 HTTPS——流量加密，你在網路層截到的是密文。標準破法是**中間人（man-in-the-middle）**：讓 App 的流量先經過你的 mitmproxy，proxy 用自己的憑證跟 App 建 TLS、再用真憑證跟伺服器建 TLS，兩段各自加密、中間 proxy 看得到明文。這招的前提是 **App 要信任 proxy 的憑證**。

於是防守方加了 **SSL Pinning**：App 在程式碼裡**寫死**「我只信這張特定的憑證/這把特定的公鑰」，你 mitmproxy 的憑證即使被系統信任，App 自己那關也過不了——TLS 握手直接被 App 主動中斷。這一章就是拆穿並繞過這道防線。

## 先建立直覺：中間人怎麼看到明文，pinning 卡在哪

先看沒有 pinning 時，MITM 怎麼運作：

```
   沒有 pinning（標準 MITM）
   App ──TLS(用 proxy 憑證)──▶ mitmproxy ──TLS(用真憑證)──▶ 伺服器
        △ App 檢查憑證：                    △ proxy 這裡看得到明文
          "這憑證是系統信任的 CA 簽的嗎？"
          你把 mitmproxy CA 裝進系統信任 → App 說 OK → 通
```

關鍵：App 預設用**系統的信任錨（trust anchor）清單**驗憑證。你把 mitmproxy 的 CA 憑證裝進「系統信任的 CA」，App 就認為 proxy 的憑證合法，握手成功，proxy 看到明文。

現在看 pinning 怎麼擋：

```
   有 pinning
   App ──TLS(用 proxy 憑證)──▶ mitmproxy ──────▶ 伺服器
        △ App 除了「系統信任嗎」，還多問一句：
          "這憑證/公鑰，是不是我程式碼裡寫死的那一張(把)？"
          proxy 的憑證 ≠ App 寫死的那張 → App 主動中斷連線 ✗
          症狀：App 顯示「網路錯誤」，mitmproxy 看到 TLS handshake 就斷
```

pinning 的本質是**App 把信任範圍從「系統信任的所有 CA」縮小到「我指定的這一張/幾張」**。所以裝系統 CA 對它無效——它根本不看系統信任清單，只比對自己寫死的那個。要繞，你得**改掉 App「比對」的那段程式碼/邏輯**，讓它別再比對（或比對永遠通過）。這就是為什麼 pinning bypass 幾乎都靠 Frida/Xposed hook——它是程式碼層的對抗，不是網路層的。

## 底層機制：pinning 的三種實作

「pinning」不是單一技術，是一組把信任收窄的做法。逆向前你得先判斷 App 用哪種，才知道 hook 哪裡。

### 1. 憑證 pinning（cert pinning）

App 內建一份或幾份**憑證（的雜湊）**，握手時比對伺服器憑證是否等於內建的：

```
App 資源裡藏著：  server_cert.crt  或  sha256(cert) = "AB12..."
握手時：收到伺服器/proxy 的憑證 → 算 sha256 → 跟內建值比 → 不等就斷
```

最常見的實作是 **OkHttp 的 `CertificatePinner`**：

```java
CertificatePinner pinner = new CertificatePinner.Builder()
    .add("api.demo.com", "sha256/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    .build();
// 這個 "sha256/..." pin 的是憑證裡「公鑰」的 SHA-256（SPKI），不是整張憑證
```

`sha256/...` 這個值 pin 的其實是憑證裡 **SPKI（Subject Public Key Info）的 SHA-256**——所以嚴格說 OkHttp 的 `CertificatePinner` 做的是**公鑰 pinning**（見下），這是很多人搞混的點。

### 2. 公鑰 pinning（public key pinning）

只 pin **公鑰**而非整張憑證。好處：伺服器換憑證（續期）但公鑰不變時，pin 仍有效，不用改 App。前述 OkHttp 就屬此類。原理相同：算收到的憑證的公鑰雜湊，跟內建值比。

### 3. `network_security_config`（宣告式 pinning）

Android 7+ 提供的**不寫程式碼**的 pinning——在 `res/xml/network_security_config.xml` 用宣告設定：

```xml
<network-security-config>
    <domain-config>
        <domain includeSubdomains="true">api.demo.com</domain>
        <pin-set>
            <pin digest="SHA-256">AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=</pin>
        </pin-set>
        <!-- 這裡若沒開 user CA，你裝的 mitmproxy CA 也不會被信任 -->
        <trust-anchors>
            <certificates src="system" />
        </trust-anchors>
    </domain-config>
</network-security-config>
```

這個檔還控制另一件關鍵事：**App 信不信任「使用者安裝的 CA」**。Android 7+ **預設 App 只信系統 CA、不信使用者 CA**——所以就算你把 mitmproxy CA 裝成「使用者憑證」，App 也不理它。這就是為什麼我們要把 CA 裝成**系統級**（下一節），或改 `network_security_config` 加 `<certificates src="user" />`（若你能重打包）。

整張信任判斷的決策流：

```
   App 發 HTTPS 請求
        │
        ▼
   收到對方憑證
        │
   ┌────┴─────────────────────────────┐
   │ 1. 憑證是「被信任的 trust anchor」簽的嗎？│  ← network_security_config 決定信 system/user
   │    否 → 斷                         │
   └────┬─────────────────────────────┘
        │ 是
   ┌────┴─────────────────────────────┐
   │ 2. 有 pinning 嗎？憑證/公鑰雜湊 == 內建值？│  ← CertificatePinner / pin-set
   │    否(不匹配) → 斷                 │
   └────┬─────────────────────────────┘
        │ 是 → 握手成功，proxy 看得到明文
```

**繞過就是攻擊這兩個判斷**：第 1 關靠「裝系統 CA」讓 proxy 憑證被信任；第 2 關靠「hook 掉 pinning 檢查」讓它別比對。兩關都得過。

## Step 1：讓裝置信任你的 mitmproxy CA（過第 1 關）

先起 mitmproxy，讓 AVD 流量走它：

```bash
# host 端啟動 mitmproxy（監聽 8080）
mitmproxy --listen-port 8080

# 設定 AVD 的全域 proxy 指向 host（AVD 裡 10.0.2.2 = host 的 loopback）
adb shell settings put global http_proxy 10.0.2.2:8080
```

`10.0.2.2` 不是隨便的 IP：**AVD 的 NAT 網路裡，`10.0.2.2` 固定映射到 host 的 `127.0.0.1`**。這是 Android emulator 的特殊約定，記住它。

接著裝 CA。**Android 7+ 預設 App 不信使用者 CA**，所以要裝成**系統 CA**——這需要 `/system` 可寫（Ch 0 開機加的 `-writable-system` 就是為此）：

```bash
# mitmproxy 的 CA 在 host 的 ~/.mitmproxy/mitmproxy-ca-cert.cer
# 系統 CA 的檔名必須是「subject hash.0」——Android 靠這個 hash 檔名去找 CA
HASH=$(openssl x509 -inform PEM -subject_hash_old \
       -in ~/.mitmproxy/mitmproxy-ca-cert.cer | head -1)
cp ~/.mitmproxy/mitmproxy-ca-cert.cer ${HASH}.0

adb root
adb remount                       # 讓 /system 真的可寫（需 -writable-system 開機）
adb push ${HASH}.0 /system/etc/security/cacerts/
adb shell chmod 644 /system/etc/security/cacerts/${HASH}.0
adb reboot                        # 重啟讓系統 CA 生效
```

`subject_hash_old` + `.0` 檔名不是玄學：**Android（承襲 OpenSSL 慣例）在 `/system/etc/security/cacerts/` 裡用「憑證 subject 的 hash」當檔名來索引 CA**。檔名不對，系統找不到這張 CA、等於沒裝。這是裝系統 CA 最常見的坑。

> **未實測，理論預期行為**。在你 AVD 驗證：裝好系統 CA、設好 proxy 後，用瀏覽器開一個**沒有 pinning** 的 HTTPS 網站，mitmproxy 應該能看到明文請求。看得到 → 第 1 關過了。這時再開有 pinning 的 App，它會「網路錯誤」——那就是第 2 關 pinning 在擋，進入 Step 2。

> **Android 14 的變化**：Android 14+ 系統 CA 改放到 APEX（`/apex/com.android.conscrypt/...`），`/system/etc/security/cacerts` 那套失效，裝法不同。本課用 API 33（Android 13），仍是傳統路徑。碰到 14+ 要查對應方法，別硬套。

## Step 2：繞過 pinning（過第 2 關）

第 1 關過了但 App 還是連不上，就是 pinning。三種繞法，由懶到細：

### 方法 A：objection 一鍵繞（最快）

objection 內建了一組覆蓋主流 pinning 實作的 bypass：

```bash
objection -g com.demo.app explore
# 進 REPL 後：
android sslpinning disable
```

它底層就是一堆 Frida hook 的集合，一次 hook 掉 OkHttp `CertificatePinner`、`TrustManager`、`SSLContext` 等常見點。**能一鍵過就先一鍵過**，先確認流量能出來，再決定要不要細究。

### 方法 B：Frida CodeShare 通用腳本

社群維護的通用 pinning bypass 腳本（最有名的是 `frida-multiple-unpinning`），覆蓋面比 objection 更廣、更新更勤：

```bash
frida -U -f com.demo.app -l frida-multiple-unpinning.js
# 或直接引用 codeshare：
frida -U -f com.demo.app --codeshare akabe1/frida-multiple-unpinning
```

### 方法 C：手寫針對性 hook（最可靠，也最能學到東西）

當 A/B 都失敗（App 用了自訂 pinning 實作，通用腳本沒覆蓋到），就得自己 hook。這也是你真正學會 pinning 原理的時候。針對 OkHttp `CertificatePinner`：

```javascript
// bypass_okhttp.js —— 讓 OkHttp 的 CertificatePinner.check 直接放行
Java.perform(function () {
    var CertificatePinner = Java.use("okhttp3.CertificatePinner");

    // check(String hostname, List peerCertificates) 是驗證入口
    // 把它的實作換成「什麼都不做就 return」= 永遠通過
    CertificatePinner.check.overload(
        'java.lang.String', 'java.util.List'
    ).implementation = function (hostname, peerCertificates) {
        console.log("[bypass] CertificatePinner.check(" + hostname + ") -> skipped");
        return;   // 不呼叫原方法、不丟例外 = pinning 檢查被跳過
    };
});
```

更底層、更通用的一招是 hook `TrustManager`（TLS 憑證驗證的最終仲裁）——用一個「什麼都信」的 `X509TrustManager` 替換掉 App 的：

```javascript
// bypass_trustmanager.js —— 用「全信任」的 TrustManager 覆蓋
Java.perform(function () {
    var X509TrustManager = Java.use('javax.net.ssl.X509TrustManager');
    var SSLContext = Java.use('javax.net.ssl.SSLContext');

    // 造一個什麼都不檢查的 TrustManager（checkServerTrusted 空實作 = 全部信任）
    var TrustManager = Java.registerClass({
        name: 'com.demo.EvilTrustManager',
        implements: [X509TrustManager],
        methods: {
            checkClientTrusted: function () {},          // 空 = 不檢查
            checkServerTrusted: function () {},          // 空 = 伺服器憑證一律信任
            getAcceptedIssuers: function () { return []; }
        }
    });

    // hook SSLContext.init，把 App 傳進去的 trustManager 換成我們的
    var initOverload = SSLContext.init.overload(
        '[Ljavax.net.ssl.KeyManager;',
        '[Ljavax.net.ssl.TrustManager;',
        'java.security.SecureRandom');
    initOverload.implementation = function (km, tm, sr) {
        var myTm = [TrustManager.$new()];               // 用我們的 TrustManager
        initOverload.call(this, km, myTm, sr);          // 呼叫原 init，但塞我們的 tm
        console.log("[bypass] SSLContext.init hooked, using EvilTrustManager");
    };
});
```

逐點解釋：

- **`CertificatePinner.check` 換成 `return;`**：這方法的語意是「不通過就丟 `SSLPeerUnverifiedException`」。我們讓它什麼都不做直接 return，等於「永遠通過」。這是最精準的針對性繞法。
- **`Java.registerClass` 動態造類**：Frida 能在執行期造一個實作某介面的新 Java 類。這裡造一個 `X509TrustManager`，三個方法全空——`checkServerTrusted` 空實作代表「任何伺服器憑證都接受」。
- **hook `SSLContext.init` 換 TrustManager**：App 建 TLS 前會用 `SSLContext.init` 設定它的 TrustManager。我們攔截這步，把 App 的換成我們的全信任版——之後這個 SSLContext 建的所有連線都不驗憑證。這招比針對 OkHttp 更底層，對「自己寫 TLS 驗證」的 App 也有效。
- `'[Ljavax.net.ssl.KeyManager;'` 這種怪字串是 **JVM 的陣列型別簽名**（`[L...;` 表示「...的陣列」），overload 要用這格式指定陣列參數。

> **未實測，理論預期行為**。驗證：先確認第 1 關過了（系統 CA 裝好、無 pinning 網站抓得到），再 `frida -U -f com.demo.app -l bypass_okhttp.js`（或 trustmanager 版），操作 App 觸發網路請求，看 mitmproxy 是否開始出現該 App 的明文請求、且 console 印出 `[bypass] ...`。出得來 → 兩關全過，可以進 Ch 18 還原協議了。

## 對比與取捨：三種繞法怎麼選

| 繞法 | 覆蓋面 | 速度 | 何時用 | 侷限 |
|---|---|---|---|---|
| **objection `sslpinning disable`** | 主流實作 | 最快（一行） | 第一發，先試 | 自訂/native pinning 常失手 |
| **CodeShare 通用腳本** | 廣（社群持續補） | 快 | objection 沒過時 | 仍是「已知實作」的集合 |
| **手寫針對性 hook** | 只針對你分析的那個 | 慢（要先逆） | 前兩者都失敗 | 要看懂 App 的 TLS 實作 |

還有一個**繞不過 Java hook 的情況**：pinning 做在 **native 層**（OpenSSL/BoringSSL 的 `SSL_CTX_set_custom_verify` 或直接在 `.so` 裡比對憑證）。這時 Java hook 全無效，得 hook native——常見招是 hook BoringSSL 的 `ssl_verify` 相關函式，或直接 hook `SSL_read`/`SSL_write` 在 TLS 之下、加密之前拿明文（Ch 14 的 native hook + Ch 25 的進階 hook 會用到）。判斷「pinning 在 Java 還 native」的方法：Java bypass 全上了還是連不出來，八成在 native。

## 踩雷集錦

1. **只把 mitmproxy CA 裝成「使用者憑證」就想抓 App**：Android 7+ 預設 App **不信使用者 CA**。你在設定裡裝的 user CA 對瀏覽器有效、對多數 App 無效。要裝**系統 CA**（`/system/etc/security/cacerts/`，需 `-writable-system` + `adb remount`），或改 App 的 `network_security_config` 加 `<certificates src="user"/>`。
2. **系統 CA 檔名不是 `subject_hash_old` 的 `.0`**：Android 靠「subject hash + `.0`」的檔名索引 CA。檔名隨便取，系統找不到、等於沒裝，症狀是連無 pinning 網站都抓不到。務必用 `openssl x509 -subject_hash_old` 算出正確檔名。
3. **把「連不上」全歸咎於 pinning，其實是第 1 關沒過**：先用**無 pinning 的網站/App** 確認 mitmproxy + 系統 CA 這條路本身通了，再談 pinning。順序反了會鬼打牆——你以為在繞 pinning，其實 CA 根本沒裝對。
4. **pinning 在 native，卻一直上 Java hook**：Java bypass 全試過還是不出來，八成 pinning 做在 `.so` 裡。Java 層再怎麼 hook 都碰不到 native 的比對邏輯。改用 native hook（hook BoringSSL 驗證函式，或在 `SSL_read`/`SSL_write` 攔明文）。
5. **proxy 設了忘了清，之後上不了網**：`settings put global http_proxy 10.0.2.2:8080` 是全域的，分析完要 `adb shell settings put global http_proxy :0` 清掉，不然 mitmproxy 一關，AVD 全裝置都上不了網，你會以為 AVD 壞了。

## 進階：再往深一層

- **在 TLS 之下抓明文**：與其跟 pinning 鬥，不如**繞到 TLS 底層**。hook BoringSSL 的 `SSL_read`（收）/`SSL_write`（發），拿到的是**加解密前後的明文**——這時 App 有沒有 pinning 都無所謂，因為你不在憑證那層攔，而在應用資料進出 TLS 引擎的那一刻攔。這是對付「native pinning + 自訂加密」的終極招，Ch 18/23 會用到。
- **抓包看到的可能還是密文**：有些 App 在 HTTPS **之上**再加一層應用層加密（body 是 AES 密文，即使你解了 TLS 也看不懂）。這時抓包只是起點，真正的活在 Ch 18——找到那層加密的函式、hook 出金鑰。pinning bypass 讓你看到「加密後的 body」，還原演算法才讓你看懂它。
- **`network_security_config` 的靜態偵察價值**：逆向前先 apktool 解出這個 XML，你能一眼看到 App pin 了哪些 domain、信不信 user CA、debug-overrides 有沒有開——這是「這 App 防護到什麼程度」的免費情報，屬於 Ch 1 SOP 的偵察步驟。
- **憑證透明度與 pinning 的式微**：業界（含 Google）因為 pin 過期造成的當機事故，逐漸從硬 pinning 轉向憑證透明度（CT）等機制。但 App 端的自訂 pinning 仍普遍，尤其金融/風控類——所以這章技能不會過時，只是要認得越來越多樣的實作。

## 動手練習

1. 起 mitmproxy、設好 AVD proxy、把 CA 裝成系統 CA，用瀏覽器開一個 HTTPS 網站，在 mitmproxy 看到明文——先把第 1 關這條路走通。故意用錯的檔名裝一次 CA，看它怎麼失敗，體會 `subject_hash` 檔名的重要。
2. 找一個你自己寫的、用 OkHttp `CertificatePinner` 做 pinning 的 demo App，先直接抓（會被擋、看 App 報網路錯誤），再上 `objection android sslpinning disable`，看流量出來——親眼看到 pinning 從擋到不擋。
3. 把 objection 一鍵繞的效果，改用本章的 `bypass_okhttp.js` 手寫版重做一次，理解 objection 底層做的就是這類 hook。
4. 用 apktool 解出某 App 的 `res/xml/network_security_config.xml`（若有），讀它 pin 了哪些 domain、信不信 user CA——練「抓包前先做靜態偵察」。

## 本章重點整理

- 抓 HTTPS 要過兩關：**第 1 關**（憑證被信任）靠 mitmproxy + **裝系統 CA**（`-writable-system`、`subject_hash.0` 檔名）；**第 2 關**（pinning）靠 hook 掉比對邏輯。
- pinning 三型：**憑證 pinning、公鑰 pinning（OkHttp `CertificatePinner` 屬此）、`network_security_config` 宣告式**；後者還控制「信不信 user CA」，是 Android 7+ 抓包變難的根因。
- 繞法由懶到細：**objection 一鍵 → CodeShare 通用腳本 → 手寫 `CertificatePinner.check` / `TrustManager` hook**；pinning 在 **native** 時 Java hook 全無效，要 hook BoringSSL。
- 終極招是**在 TLS 之下（`SSL_read`/`SSL_write`）抓明文**，繞開整個憑證/pinning 層。
- 抓到的 body 可能還有應用層加密——pinning bypass 只是讓你看到密文，看懂它是下一章的事。

## 自我檢核

- [ ] 能解釋為什麼裝了系統 CA、pinning 的 App 還是連不上（信任範圍被 pinning 收窄）
- [ ] 說得出 Android 7+ 為什麼「裝 user CA 對 App 沒用」，以及兩種解法
- [ ] 知道 OkHttp `CertificatePinner` 的 `sha256/...` pin 的其實是什麼（公鑰 SPKI）
- [ ] 能講出三種繞 pinning 的方法與各自適用時機，以及「pinning 在 native」時該怎麼辦
- [ ] 理解「在 `SSL_read`/`SSL_write` 抓明文」為什麼能繞開整個 pinning 問題

## 延伸閱讀

- **[OWASP MASTG — Testing Network Communication / Certificate Pinning](https://mas.owasp.org/MASTG/techniques/android/)**
  - **讀哪裡**：Android 的 network communication 與 certificate pinning bypass 技術段
  - **和本章的關聯**：本章繞 pinning 流程的業界標準版，含更多實作的判斷與繞法
- **[mitmproxy 官方文件](https://docs.mitmproxy.org/stable/)** — mitmproxy
  - **讀哪裡**：`Getting Started` 與 `Certificates`（含 Android 系統 CA 安裝）那節
  - **為什麼值得讀**：CA 安裝、proxy 設定的權威來源；還有 addon 腳本能自動化改請求（Ch 18 重放會用到）
- **[Frida CodeShare — akabe1/frida-multiple-unpinning](https://codeshare.frida.re/@akabe1/frida-multiple-unpinning/)**
  - **讀哪裡**：整支腳本的原始碼——它覆蓋了十幾種 pinning 實作
  - **為什麼值得讀**：讀它 hook 了哪些類/方法，等於一張「pinning 實作全景圖」，比自己一個個找快
- **[Android 開發者 — Network Security Configuration](https://developer.android.com/privacy-and-security/security-config)**
  - **讀哪裡**：`pin-set`、`trust-anchors`、`debug-overrides` 那幾節
  - **和本章的關聯**：第 3 種 pinning 與「信不信 user CA」的權威定義，也是防守方視角的原始文件
- **[HackTricks — Bypassing SSL Pinning](https://book.hacktricks.wiki/en/mobile-pentesting/android-app-pentesting/index.html)**
  - **讀哪裡**：SSL pinning bypass 段（objection/Frida/native 各法）
  - **前提知識**：讀過本章，這頁給你更多現成指令與 native pinning 的處理思路

下一章我們把抓到的流量用起來。看到請求裡有個 `sign=xxxx` 加密參數——它是怎麼算出來的？我們把 Ch 1 那條攻擊鏈完整走一遍：抓包定位可疑參數 → jadx/Frida 找到加密函式 → hook 印出金鑰與參數 → 還原演算法 → 自己重算重放。這是整個 Part 3 的收束。

→ [Ch 18 協議還原：從抓包到簽名演算法](./18-protocol-recovery.md)
