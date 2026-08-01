# Ch 9 — 網路層漏洞：明文、pinning 缺失、network_security_config 誤配

> **目標**：把 App 的「網路信任模型」看穿。你要能判斷一個 App 走不走明文 HTTP（`usesCleartextTraffic`）、有沒有做憑證釘選（certificate pinning）、`network_security_config.xml` 有沒有被誤配成信任使用者 CA 或開了 `debug-overrides`、以及最粗暴的「全信任 TrustManager」長怎樣。最後用 mitmproxy 把流量抓下來，這是驗證「這個 App 的網路真的能被攔」的實測手段——也是後面幾乎每個網路相關漏洞的前置。

> **環境**：`network_security_config.xml` 與 TrustManager 的 Java/XML 範例手寫、標漏洞點。憑證信任的實際行為與 mitmproxy 抓包屬執行期，本 repo 沙箱無 AVD，標「**未實測，理論預期行為**」並附驗證步驟。SSL pinning 的**繞過**在 [android_reversing Ch 17](../android_reversing/17-ssl-pinning-bypass.md) 已深入，本章聚焦「**判斷有沒有、配得對不對**」的漏洞視角，不重複繞過細節。

## 為什麼需要這個？

網路是 App 跟世界說話的管道，而「這條管道能不能被中間人（MITM）攔截、竄改」直接決定一堆漏洞成不成立。App 傳明文密碼、傳未加密的 API、憑證驗證做假——這些在 2026 年仍大量存在，而且是**評估報告裡最好寫、影響最直接**的一類：能抓到明文 token 就是實錘。

但更重要的是**方法論意義**：你能不能抓到 App 的流量，是後面所有「看它跟伺服器說什麼」分析的前提。如果 App 有 pinning 你抓不到，就得先繞過（Ch 17）；如果它連 pinning 都沒有，你 mitmproxy 一架就全看光。所以這章要先教你**判斷 App 屬於哪一種**，再教你怎麼把流量真的抓下來——這是 Ch 8 WebView 分析、練習 B、乃至 final 報告都要用的地基。

## 先建立直覺：Android 的網路信任是層層設定堆出來的

一個 App 發 HTTPS 請求時，「要不要信任這張伺服器憑證」不是一個布林值，而是一疊設定共同決定的：

```
   App 發 https 請求
        │
        ▼
   ┌─────────────────────────────────────────────┐
   │ 1. 有沒有走明文？ usesCleartextTraffic /       │
   │    network_security_config 的 cleartext 設定   │  ← 明文就沒憑證問題，直接被看光
   ├─────────────────────────────────────────────┤
   │ 2. 用哪個 trust anchor 驗憑證？                 │
   │    system CA（預設）/ user CA / 自帶 CA         │  ← 信任 user CA = 可被裝憑證的人 MITM
   ├─────────────────────────────────────────────┤
   │ 3. 有沒有 pinning？只認特定憑證/公鑰            │  ← 有 pin，換張合法憑證也不認
   ├─────────────────────────────────────────────┤
   │ 4. App 自訂 TrustManager 覆蓋以上？            │  ← 全信任 = 上面全白搭
   └─────────────────────────────────────────────┘
```

四層任一層配錯，MITM 就成立。這章逐層看：明文（層 1）、`network_security_config` 控制的信任錨（層 2、3 的設定面）、以及 App 用程式碼硬幹的全信任 TrustManager（層 4）。**心智模型：每往下一層，就多一個「開發者可能親手把安全關掉」的地方。**

## 底層機制一：明文 HTTP 與 cleartext 政策的版本演進

最基本的洞：App 走 `http://` 明文，任何在路徑上的人（同 WiFi、惡意熱點、電信）都能直接讀寫流量。Android 對明文的預設政策**隨版本收緊**，這是重點：

| targetSdk | cleartext 預設 | 說明 |
|---|---|---|
| < 28（Android 9 以前） | **允許明文** | 老 App 預設可走 `http://`，無需任何設定 |
| ≥ 28（Android 9+） | **預設禁止明文** | 除非明確 opt-in，`http://` 請求會失敗 |

opt-in 的方式有兩個，判斷 App 時都要看：

```xml
<!-- 方式 A：Manifest 全域開明文（最粗，整個 App 都能走 http）-->
<application android:usesCleartextTraffic="true" ... >
```

```xml
<!-- 方式 B：network_security_config 針對特定網域開明文 -->
<network-security-config>
    <domain-config cleartextTrafficPermitted="true">
        <domain includeSubdomains="true">api.legacy.example.com</domain>
    </domain-config>
</network-security-config>
```

看到 `usesCleartextTraffic="true"` 就是紅旗——代表整個 App 願意走明文，抓包時直接看有沒有敏感資料裸奔。方式 B 較收斂（只針對某網域），但那個網域若承載敏感 API 一樣是洞。

> **注意**：`usesCleartextTraffic` 只管「明碼 socket」（http、ftp、明文 WebSocket），不影響 https。而且它是「盡力而為」——某些底層 library 用自己的 socket 可能繞過這個 flag，所以**不能只看 Manifest 就斷定 App 沒明文流量，要抓包實測**。

## 底層機制二：network_security_config 的信任錨與誤配

`network_security_config.xml`（Android 7 / API 24 引入）是宣告式配置 App 網路信任的地方，也是**誤配重災區**。它在 Manifest 用 `android:networkSecurityConfig` 掛上：

```xml
<application android:networkSecurityConfig="@xml/network_security_config" ... >
```

它能配三件事：明文政策（機制一的方式 B）、**信任哪些 CA**、**pinning**。先看 trust anchor——這是最關鍵的信任決策：

**預設信任模型（沒配 or 配對）**：App 只信任**系統 CA**，不信任使用者自己裝的 CA。這正是為什麼 Android 7+ 之後你把 mitmproxy 憑證裝進「使用者憑證」，App 卻抓不到——因為 App 預設不吃 user CA。這是**安全預設**，開發者不該關掉它。

**危險誤配一：信任 user CA**：開發者為了自己方便抓包，加了信任使用者憑證：

```xml
<!-- 漏洞：正式版信任 user CA —— 任何能誘導使用者裝憑證的人都能 MITM -->
<network-security-config>
    <base-config>
        <trust-anchors>
            <certificates src="system"/>
            <certificates src="user"/>    <!-- ← 紅旗：信任使用者 CA -->
        </trust-anchors>
    </base-config>
</network-security-config>
```

信任 `user` CA 意味著：只要攻擊者能讓受害者裝一張憑證（釣魚、惡意 profile、企業 MDM 濫用），就能對這個 App 做 MITM。這在正式版是明確的漏洞。

**危險誤配二：`debug-overrides` 外洩到正式版**：`debug-overrides` 區塊**只在 `android:debuggable="true"` 時生效**，本意是讓開發版能抓包而不污染正式設定。問題是開發者常在裡面塞「信任 user CA / 自帶測試 CA」，然後**如果正式版不小心 `debuggable="true"`（Ch 2 提過這個紅旗），debug-overrides 就生效了**：

```xml
<network-security-config>
    <!-- 只在 debuggable=true 生效；但若正式版誤開 debuggable，就成洞 -->
    <debug-overrides>
        <trust-anchors>
            <certificates src="user"/>
            <certificates src="@raw/test_ca"/>   <!-- 測試用全信任 CA -->
        </trust-anchors>
    </debug-overrides>
</network-security-config>
```

所以評估時 `debug-overrides` 要連 `android:debuggable` 一起看：debug-overrides 本身不是洞，但 `debuggable=true` + debug-overrides 信任 user CA 就是。

**pinning 設定面**：`network_security_config` 也能宣告 pinning（釘公鑰 hash）：

```xml
<domain-config>
    <domain includeSubdomains="true">api.example.com</domain>
    <pin-set expiration="2027-01-01">
        <pin digest="SHA-256">base64EncodedPublicKeyHash==</pin>
        <pin digest="SHA-256">backupPinHash==</pin>    <!-- 備援 pin，換 key 用 -->
    </pin-set>
</domain-config>
```

有 `pin-set` 代表 App 只認這幾個公鑰——就算你的 MITM 憑證由系統信任的 CA 簽發也不認，這是**缺 pinning 的相反面**。評估時：**沒有 pin-set = 沒做 pinning**（層 3 缺失），MITM 只要憑證被信任就成立。注意 `expiration` 過期後 pinning 會失效（fail open），這也是一個要看的點。

## 底層機制三：全信任 TrustManager — 程式碼層的自毀

前兩個機制是宣告式設定；最粗暴的洞是**開發者用程式碼自己寫一個「什麼憑證都信」的 TrustManager**，直接覆蓋掉系統的憑證驗證。這在 Java/OkHttp 裡長這樣：

```java
// 漏洞：checkServerTrusted 空實作 = 接受任何憑證（含 MITM 的自簽憑證）
TrustManager[] trustAll = new TrustManager[] {
    new X509TrustManager() {
        public void checkClientTrusted(X509Certificate[] c, String a) {}
        public void checkServerTrusted(X509Certificate[] c, String a) {}   // ← 空 = 全信任
        public X509Certificate[] getAcceptedIssuers() { return new X509Certificate[0]; }
    }
};
SSLContext ctx = SSLContext.getInstance("TLS");
ctx.init(null, trustAll, new SecureRandom());
// 再配一個全接受的 hostname verifier，連主機名都不驗
HttpsURLConnection.setDefaultHostnameVerifier((hostname, session) -> true);  // ← 也是洞
```

`checkServerTrusted` 空實作代表**任何伺服器憑證都通過**——攻擊者拿一張自簽憑證就能 MITM，`network_security_config` 配得再好都白搭（因為程式碼層直接繞過了它）。搭配的 `HostnameVerifier` 回傳 `true` 更是連「憑證上的網域對不對」都不驗。

這類洞的靜態特徵很明顯，Ch 15 的 semgrep / mobsfscan 掃 `checkServerTrusted` 空實作、`ALLOW_ALL_HOSTNAME_VERIFIER`、`SSLSocketFactory` 全信任等 pattern 一抓一個準。jadx 搜這些關鍵字也能快速定位。

> **常見來源**：這種全信任 TrustManager 常來自開發階段「為了連自簽測試伺服器」貼的 Stack Overflow 程式碼，忘了在正式版拿掉。它是 OWASP MASTG 明列的高危項，也是 Google Play 會擋上架的問題之一。

## 底層機制四：用 mitmproxy 實測抓包

判斷完設定，要用**抓包驗證**——這是唯一能證明「這個 App 的流量真的能被攔」的方法。流程（**未實測，理論預期行為**，本 repo 沙箱無 AVD，附完整驗證步驟）：

```
   AVD 的流量 ──proxy──▶ mitmproxy（你電腦）──▶ 真正的伺服器
                          │
                          └─ 你在這看到/改所有 http(s)
```

```bash
# 1. 電腦上起 mitmproxy（預設 8080 埠）
mitmproxy    # 或 mitmweb 走瀏覽器介面

# 2. 讓 AVD 走這個 proxy（開機時指定，或設定裡填）
emulator -avd re33 -writable-system -http-proxy 127.0.0.1:8080

# 3. 裝 mitmproxy 的 CA 憑證——關鍵在裝成「系統 CA」而非「使用者 CA」
#    因為 Android 7+ 的 App 預設只信 system CA（機制二）
adb root && adb remount
# mitmproxy 憑證在 ~/.mitmproxy/mitmproxy-ca-cert.cer，
# 轉成 system CA 需算 subject hash 命名後推到 /system/etc/security/cacerts/
#   （android_reversing Ch 17 有完整步驟，這裡不重複）

# 4. 開 App，看 mitmproxy 有沒有流量
```

三種抓包結果對應三種診斷：

| 抓包結果 | 診斷 |
|---|---|
| 明文 http 直接可見（連憑證都不用裝） | `usesCleartextTraffic` / cleartext 開著（機制一）—— 最嚴重 |
| 裝了 system CA 後 https 可見 | 沒 pinning、沒全信任問題，但也代表**它信任系統 CA 就能被有系統 CA 能力者攔**（企業/惡意 MDM 情境） |
| 裝了 system CA 仍抓不到（TLS 握手失敗） | 有 pinning（機制二的 pin-set 或程式碼 pinning）—— 要繞過（Ch 17） |

> 驗證步驟重點：**憑證一定要裝成 system CA**（Android 7+ App 預設不信 user CA，這也正是機制二「信任 user CA」為什麼是洞——它讓攻擊者不用 root 就能靠 user CA 攔）。裝 system CA 需要 `-writable-system` 的 AVD（Ch 0 建過）。抓不到不代表 App 沒問題，可能是有 pinning，換 Ch 17 的繞過再抓。**只抓你有權測試的 App。**

## 對比與取捨

| 防護層 | 缺失/誤配的樣子 | MITM 成不成立 | 修法 |
|---|---|---|---|
| 明文政策 | `usesCleartextTraffic="true"` | 成立，連憑證都不用裝 | 全走 https，targetSdk ≥ 28 |
| trust anchor | 信任 `user` CA | 成立（受害者裝憑證即可） | 只信 `system`，別加 `user` |
| debug-overrides | + 正式版 `debuggable=true` | 成立 | 正式版關 debuggable |
| pinning | 沒有 `pin-set` | 成立（憑證被信任即可） | 對敏感 API 加 pinning |
| TrustManager | `checkServerTrusted` 空實作 | 成立，繞過一切 | 別自訂全信任 TrustManager |

取捨的現實：**pinning 是安全與維運的拉鋸**。加了 pinning 擋 MITM 很有效，但憑證輪替時若沒備援 pin（`<pin>` 只放一個）、或 `expiration` 到期，App 會連不上伺服器——這是為什麼很多 App 索性不做 pinning。從評估角度：**沒 pinning 對高敏感 App（金融、醫療）是缺陷；對一般 App 是「可建議」而非「必須」**，寫報告時要按 App 性質定嚴重度。

## 踩雷集錦

1. **只看 Manifest 就斷定「沒明文」**：`usesCleartextTraffic="false"` 不保證沒有明文——某些第三方 library 用自己的 socket 可能繞過。一定要 mitmproxy 實測，看有沒有 http 流量真的跑出來。
2. **把「抓不到包」當成「App 很安全」**：抓不到最可能是有 pinning，不是沒漏洞。繞過 pinning（Ch 17）後可能發現一堆明文 token。抓不到只是「這一關卡住」，不是結論。
3. **憑證裝成 user CA 卻怪 App 抓不到**：Android 7+ App 預設不信 user CA。要裝成 system CA（需 `-writable-system` AVD + remount）。這個坑本身正是機制二那個漏洞的一體兩面——會這個坑才懂為什麼「信任 user CA」是洞。
4. **`debug-overrides` 看到就當漏洞**：它只在 `debuggable=true` 生效。單獨存在不是洞，要**連 `android:debuggable` 一起判**。正式版 debuggable=false 的話，debug-overrides 不生效。
5. **忽略 pinning 的 `expiration` 過期 fail-open**：`pin-set expiration="..."` 過期後 pinning 自動失效退回一般驗證。評估時看到已過期的 pin-set = 實際上沒 pinning 保護。
6. **以為信任 system CA 就完全安全**：信任 system CA 是預設也是合理的，但在「企業 MDM 塞了 CA」「使用者被騙裝了系統級憑證」的威脅模型下仍可能被攔。對高敏感 App，pinning 才是把信任收到「只認我這張」的手段。

## 進階：再往深一層

- **OkHttp 的 `CertificatePinner` 與程式碼 pinning**：除了 `network_security_config` 的宣告式 pinning，很多 App 用 OkHttp 的 `CertificatePinner.Builder().add(host, "sha256/...")` 在程式碼做 pinning。靜態找 pinning 要兩邊都搜（XML 的 `pin-set` + 程式碼的 `CertificatePinner`/`checkServerTrusted`）。繞過手法見 Ch 17。
- **Flutter / React Native 的網路不吃 network_security_config**：Flutter 用自己的 Dart HTTP stack、部分 RN library 也自帶，**不一定套用 Android 的 `network_security_config`**。所以對 Flutter App 設定看起來很安全卻抓不到/或明明配了 pinning 卻抓得到，要意識到「這層設定可能根本沒作用在它的網路庫上」。
- **`cleartextTrafficPermitted` 的細粒度**：可以在 `base-config`（全域）、`domain-config`（特定網域）分別設，甚至巢狀覆蓋。評估時要看清楚「哪個網域被開了明文」，不是只看有沒有這個字。
- **TLS 版本與 cipher 的老化**：除了信任問題，App 若還允許 TLS 1.0/1.1 或弱 cipher 也是網路層缺陷。抓包時 mitmproxy 會顯示協商出的 TLS 版本，順手記下。

## 動手練習

1. 拿一個靶 App（DIVA/AndroGoat/InsecureBankv2），用 apktool 解出 Manifest 與 `res/xml/network_security_config.xml`，判斷它屬於機制一~三的哪些情況：有沒有 `usesCleartextTraffic`、信不信 user CA、有沒有 pin-set、有沒有 debug-overrides + debuggable。
2. 用 jadx 搜 `checkServerTrusted`、`X509TrustManager`、`HostnameVerifier`、`CertificatePinner`，看 App 有沒有程式碼層的全信任或 pinning。
3. 架 mitmproxy + 把憑證裝成 system CA，開 App 抓包。對照練習 1 的判斷：明文的直接看得到？有 pinning 的抓不到？把抓到的第一個敏感欄位（token/密碼/API）記下來當報告素材。
4. 找一個有 pinning 的 App，先確認抓不到，記住這個「卡住」的狀態——這正是 Ch 17 繞過的起點。

## 本章重點整理

- **明文（`usesCleartextTraffic` / cleartext 政策）**：targetSdk < 28 預設允許明文、≥ 28 預設禁止；看到全域開明文是紅旗，但要抓包實測（library 可能繞過 flag）。
- **`network_security_config` 的 trust anchor 是核心**：預設只信 system CA（安全）；**信任 user CA** 或 **debug-overrides + debuggable=true** 是誤配；有沒有 `pin-set` 決定有沒有 pinning。
- **全信任 TrustManager（`checkServerTrusted` 空實作）**是程式碼層最粗暴的洞，繞過一切設定，semgrep/jadx 一搜就到。
- **mitmproxy 抓包**是實測手段：憑證要裝成 **system CA**（Android 7+ 不信 user CA）；抓不到最可能是有 pinning，不是沒漏洞。

## 自我檢核

- [ ] 能說出 targetSdk 28 前後明文政策的差別，以及兩種 opt-in 明文的方式
- [ ] 能解釋為什麼 Android 7+ App 預設抓包要裝 system CA 而非 user CA，以及「信任 user CA」為什麼是洞
- [ ] 知道 `debug-overrides` 什麼時候才生效，評估時要連哪個欄位一起看
- [ ] 能認出全信任 TrustManager 的程式碼特徵，並知道它為什麼繞過一切設定
- [ ] 抓不到包時，能說出至少兩個原因並知道下一步（繞 pinning / 換 system CA）

## 延伸閱讀

- **[Android 官方 — Network Security Configuration](https://developer.android.com/privacy-and-security/security-config)**
  - **讀哪裡**：trust anchors、`debug-overrides`、`cleartextTrafficPermitted`、`pin-set` 每個元素的語意與生效條件
  - **學什麼**：機制一~二每個設定的權威定義與版本行為——本章設定判斷的一手依據
  - **關聯**：練習 1 的判斷標準出處
- **[OWASP MASTG — Testing Network Communication](https://mas.owasp.org/MASTG/tests/android/MASVS-NETWORK/)**
  - **讀哪裡**：cleartext、憑證驗證、pinning 的測試流程與 mitmproxy 設定
  - **學什麼**：把網路層評估變成系統化清單，怎麼寫進報告
  - **關聯**：機制四抓包方法論與 final 報告的依據
- **[mitmproxy 官方文件 — Android](https://docs.mitmproxy.org/stable/howto-install-system-trusted-ca-android/)**
  - **讀哪裡**：把 mitmproxy CA 裝成 Android system CA 的完整步驟
  - **學什麼**：機制四抓包的精確操作（subject hash 命名、推到 cacerts）
  - **關聯**：動手練習 3 的操作指南；與 [android_reversing Ch 17](../android_reversing/17-ssl-pinning-bypass.md) 的 pinning 繞過接續
- **[HackTricks — Android Certificate Pinning Bypass](https://book.hacktricks.wiki/en/mobile-pentesting/android-app-pentesting/index.html)**
  - **讀哪裡**：判斷 pinning 存在與繞過的段落
  - **學什麼**：抓不到包時的下一步——確認是不是 pinning、怎麼繞
  - **關聯**：機制四「抓不到」情境的後續

判斷完網路層、也把流量抓下來後，你已經走完 Part 3 的前端與網路面。接下來把這章學的 deeplink（Ch 7）+ WebView（Ch 8）+ 抓包（本章）串成一條完整的攻擊鏈——這正是練習 B：用一個 deeplink 把任意 URL 餵給有 JS bridge 的 WebView，一路鏈成 RCE。

→ [練習 B：WebView + deeplink 鏈成 RCE](./practice-b-webview-deeplink-rce.md)
