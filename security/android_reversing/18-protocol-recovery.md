# Ch 18 — 協議還原：從抓包到簽名演算法

> **目標**：把 Ch 1 開頭那條攻擊鏈**完整走一遍**，做出一個能重放請求的 PoC。你會學到一套可複製的方法論：抓包看請求 → 認出可疑的 `sign`/加密參數 → 用 jadx 靜態定位加密函式 → 用 Frida hook 印出**執行期真正的參數與金鑰** → 對照靜態程式碼還原演算法 → 自己用 Python 重算、重放。這章是 Part 3（動態插樁）的收束，也是練習 B 的預演——練習 B 讓你獨立走完這條鏈。

> **環境**：AVD（Android 13 / API 33，x86_64，Google APIs）、`mitmproxy`、`jadx`、`Frida 16.x`、`python3`。**簽名/重放演算法（HMAC-SHA256、參數排序拼接）用 Python 3 實跑**，標「**實際輸出**」；Frida hook 腳本手寫、逐行解釋，標「**未實測，理論預期行為**」並附驗證步驟。絕不拿沒跑過的裝置輸出裝成跑過的。

## 為什麼需要這個？

前面 17 章教的每個工具，到這章要**串成一條鏈**。單獨會抓包、會 jadx、會 Frida hook 都不夠——真實任務要你把它們接起來解決一個具體問題：「這個 App 的請求，我能不能自己構造出來、讓伺服器認為是 App 發的？」

這個能力值錢在哪：能重放請求 = 能寫爬蟲/自動化、能做 API 安全評估（測伺服器有沒有驗好簽名）、能理解 App 的風控設計。而擋在你面前的核心難點永遠是那個**簽名參數**（`sign`/`token`/`_s`/`nonce`+加密 body）——伺服器靠它確認「這請求真的是我家 App 算出來的」。你不還原它，重放的請求就會被伺服器以「簽名錯誤」打回。

還原簽名的困難在於**它是靜態動態都缺一不可的典型**：靜態（jadx）看得到演算法骨架但看不到執行期的金鑰值（金鑰常是動態拼出來或藏在 native）；動態（Frida）看得到金鑰值但要靠靜態先定位「hook 哪個函式」。這章就是教你怎麼讓兩條腿配合。

## 先建立直覺：一條請求的簽名是怎麼來的

先把「帶簽名的請求」這件事的心智模型立起來。伺服器收到請求時做的驗證是：

```
   App 端（構造請求）                        伺服器端（驗證）
 ┌────────────────────────┐              ┌────────────────────────┐
 │ 業務參數:               │              │ 收到 params + sign      │
 │  uid, ts, action, amount│─── 傳 ───▶  │                        │
 │                        │              │ 用「同樣的演算法+金鑰」 │
 │ sign = f(params, key)  │─── 傳 ───▶  │ 自己重算 sign'          │
 └────────────────────────┘              │                        │
        △ 你要還原的就是這個 f 和 key      │ sign == sign' ? 通:拒  │
                                          └────────────────────────┘
```

伺服器和 App **共用同一套 `f` 和 `key`**。你重放請求要成功，就得**在你的腳本裡重現這個 `f` 和 `key`**。所以「還原協議」的本質是還原兩樣東西：

1. **演算法 `f`**：參數怎麼排序、怎麼拼接、用什麼雜湊/加密（HMAC-SHA256？MD5？AES？）——這部分**靜態看程式碼**多半能推出來。
2. **金鑰 `key`**：那把祕密。可能寫死在字串裡（好運）、動態拼出來（`appsecret + ts` 之類）、或藏在 native `.so` 裡算出來（難）——這部分**動態 hook 印出來**最可靠。

一句話：**演算法靠靜態讀、金鑰靠動態抓**。這是本章方法論的骨。

## 完整方法論：五步串鏈

我們用一個具體的假想 App（登入/下單類）走完整流程。假設抓到的請求長這樣：

```
POST /api/v2/pay HTTP/1.1
Content-Type: application/json

{"uid":"1001","ts":"1717430400","action":"pay","amount":"500",
 "sign":"17f9d8e8...4ce976"}
```

### Step 1：抓包，認出可疑參數

先照 Ch 17 把 pinning 繞掉、mitmproxy 抓到明文。抓到後，**逐個參數分類**：

```
uid, ts, action, amount   → 業務參數，語意明顯，值會變
sign                      → 可疑！十六進位、長度固定(64 hex = 32 byte)
                            → 64 個 hex char = SHA-256 的長度特徵！
```

**認參數是門手藝**，靠特徵判斷：

| 特徵 | 很可能是 |
|---|---|
| 32 個 hex char | MD5 |
| 40 個 hex char | SHA-1 |
| **64 個 hex char** | **SHA-256 / HMAC-SHA256** |
| base64（`=` 結尾、含 `+/`） | 加密後的 bytes（AES/RSA）或 base64 編碼 |
| 每次請求都變、但參數沒變 | 摻了時間戳/隨機 nonce |

我們的 `sign` 是 64 hex → 鎖定 **SHA-256 家族**。再觀察：改 `amount` 重發，`sign` 就變 → 它跟參數有關（不是固定 token）；固定所有參數但隔一段時間再發，`sign` 也變 → 摻了 `ts`（時間戳）。這些觀察縮小了 `f` 的可能形狀。

### Step 2：jadx 靜態定位加密函式

拿 `sign` 這個字串去 jadx **全域搜**。搜什麼？

```
在 jadx-gui 裡搜（Search everywhere）：
  "sign"          → 太多雜訊
  "\"sign\""      → 找「把 sign 當 JSON key 放進去」的地方 ← 更準
  "HmacSHA256"    → 直接找演算法名（Java crypto API 的字面量）
  "sha256"        → 同上
```

搜 `"HmacSHA256"`（Java 的 `Mac.getInstance("HmacSHA256")` 會有這字面量）通常一擊命中。假設找到：

```java
// jadx 反編譯出來的（近似，變數名可能被混淆成 a/b/c）
public class SignUtil {
    public static String genSign(Map<String, String> params) {
        List<String> keys = new ArrayList<>(params.keySet());
        Collections.sort(keys);                        // ← 參數排序！
        StringBuilder sb = new StringBuilder();
        for (String k : keys) {
            sb.append(k).append("=").append(params.get(k)).append("&");
        }
        sb.deleteCharAt(sb.length() - 1);              // 去掉尾巴的 &
        String base = sb.toString();
        return hmacSha256(base, getKey());             // ← key 從 getKey() 來
    }
    private static native String getKey();             // ← 金鑰在 native！靜態看不到值
}
```

靜態**讀出了演算法骨架**：參數按 key 排序 → `k=v&k=v...` 拼接 → 去尾 `&` → `HMAC-SHA256(base, key)`。但 `getKey()` 是 **native 方法**——金鑰在 `.so` 裡算，靜態看不到值。這正是「演算法靠靜態、金鑰靠動態」的教科書情境。

### Step 3：Frida hook 印出執行期的參數與金鑰

我們有兩個 hook 目標：**確認 `base` 字串**（驗證我們對演算法的理解對不對）和**印出 `getKey()` 回傳的金鑰**。

```javascript
// hook_sign.js —— 印出簽名函式的輸入 params、getKey 的回傳金鑰、與最終 sign
Java.perform(function () {
    var SignUtil = Java.use("com.demo.pay.SignUtil");

    // hook genSign：印出它收到的 params、算出的 sign
    SignUtil.genSign.implementation = function (params) {
        var sign = this.genSign(params);           // 呼叫原方法拿結果
        console.log("[genSign] params = " + params.toString());
        console.log("[genSign] sign   = " + sign);
        return sign;
    };

    // hook getKey（native 方法也能從 Java 層 hook 它的回傳值）
    SignUtil.getKey.implementation = function () {
        var key = this.getKey();                   // 讓 native 算完，攔它的回傳
        console.log("[getKey]  key    = " + key);  // ← 金鑰現形！
        return key;
    };
});
```

逐點解釋：

- **hook `getKey()` 攔回傳值**：`getKey()` 雖是 native（`.so` 裡算），但它是個 Java 宣告的 native method，**從 Java 層 hook 它、讓它算完、攔它的回傳值**就能拿到金鑰——不用逆 `.so`。這是「native 金鑰但不逆 native」的偷懶好招：你不關心它怎麼算出來的，只要那個算出來的值。
- **hook `genSign` 印 `params`**：確認執行期真正參與簽名的參數集（有時 App 會偷偷加你抓包沒看到的參數，例如 `deviceId`——這種「隱藏參數」是重放失敗最常見的原因，hook 印出來就抓包對不到的東西全現形了）。
- **`this.genSign(params)` 先呼叫原方法**：我們是「觀察」不是「改」，所以照樣呼叫原實作、拿到真結果再印、再原樣回傳，不影響 App 行為。

> **未實測，理論預期行為**。驗證：`frida -U -f com.demo.pay -l hook_sign.js`，在 App 操作一次下單，終端應印出 `[getKey] key = ...`、`[genSign] params = {...}`、`[genSign] sign = ...`。**把印出的 `params` 跟你抓包看到的請求 body 逐欄比對**——如果 hook 印的 params 比抓包多了欄位（例如多個 `deviceId`），那就是隱藏參數，重放時必須帶上。

### Step 4：對照，還原演算法

現在你手上有三塊拼圖：靜態的演算法骨架、hook 印的 `params`（含隱藏參數）、hook 印的 `key`。把它們對起來，用 Python 重現 `f`，**先驗證能不能算出跟 hook 印的 `sign` 一樣的值**（這是還原正確性的黃金測試）：

```python
import hashlib, hmac

# 從 hook 印出來的（假想值）
params = {"uid": "1001", "ts": "1717430400", "action": "pay", "amount": "500"}
key = "9f8c2a1b3d4e5f60"        # ← Step 3 的 [getKey] 印出來的

# 照 Step 2 靜態讀到的演算法：排序 → k=v&k=v → HMAC-SHA256
base = "&".join(f"{k}={params[k]}" for k in sorted(params))
sign = hmac.new(key.encode(), base.encode(), hashlib.sha256).hexdigest()
print("base =", base)
print("sign =", sign)
```

**實際輸出**（我在沙箱用 Python 3 實跑）：

```
base = action=pay&amount=500&ts=1717430400&uid=1001
sign = 17f9d8e85f998e10767c674f31a263752c2b8c09bf1f374c922eed5a5a4ce976
```

**這就是黃金驗證**：如果這個 `sign` 跟 Step 1 抓包看到的、Step 3 hook 印的 `sign` **完全一致**，代表你的演算法還原**百分之百正確**——排序方式對了、拼接格式對了、金鑰對了、雜湊選對了。任何一處錯（例如漏了排序、`&` 位置錯、金鑰多個換行），算出來就會完全不同（雜湊的雪崩效應）。對不上就回頭逐項查。

### Step 5：重放

還原正確後，寫重放腳本——自己構造參數、算 `sign`、發請求：

```python
import hashlib, hmac, requests, time

KEY = "9f8c2a1b3d4e5f60"          # Step 3 抓到的金鑰

def gen_sign(params: dict, key: str) -> str:
    base = "&".join(f"{k}={params[k]}" for k in sorted(params))
    return hmac.new(key.encode(), base.encode(), hashlib.sha256).hexdigest()

def replay(uid, action, amount):
    params = {
        "uid": uid,
        "ts": str(int(time.time())),   # 用「當下」時間戳，不能重放舊的（可能過期）
        "action": action,
        "amount": amount,
    }
    body = dict(params)
    body["sign"] = gen_sign(params, KEY)   # 簽名只簽業務參數，不含 sign 自己
    r = requests.post("https://api.demo.com/api/v2/pay", json=body)
    print(r.status_code, r.text)

# replay("1001", "pay", "500")   # 對授權測試環境才跑
```

要點：

- **`ts` 用當下時間**：伺服器常檢查時間戳是否在合理窗口內（防重放攻擊）。你用抓包時的舊 `ts` 可能被拒。所以重放要動態生成當下 `ts`、再算 `sign`。
- **簽名的輸入不含 `sign` 自己**：`gen_sign` 只吃業務參數，算完才把 `sign` 加進 `body`。這是簽名機制的常識，但寫錯（把 sign 也算進去）是常見 bug。
- **金鑰的持久性**：如果 `key` 是靜態常數，這腳本一直能用；如果 `key` 是動態的（`md5(appsecret+ts)`），你得把「金鑰生成」也還原進腳本（練習 B 正是這個進階情境——金鑰動態產生，你得連金鑰演算法一起還原）。

## 一個更硬的變體：body 整個被加密

上面 `sign` 還原完就結束，是因為業務參數是明文。更硬的 App 會**把整個 body 加密**（AES），你抓包看到的是 base64 密文：

```
{"data":"U2FsdGVkX1+...(base64)...","sign":"..."}
```

方法論不變，只是多一層：

1. jadx 搜 `"AES"` / `Cipher.getInstance` 定位加密函式。
2. Frida hook `Cipher.doFinal`（所有 Java 加密最後都過這裡），**印出加密前的明文與 key/IV**：

```javascript
// 攔 Cipher.doFinal，印出加密前的輸入（明文）
var Cipher = Java.use("javax.crypto.Cipher");
Cipher.doFinal.overload('[B').implementation = function (input) {
    console.log("[Cipher.doFinal] input(明文?) = " +
                Java.use("java.lang.String").$new(input));
    return this.doFinal(input);
};
// 另外 hook Cipher.init 印出 SecretKeySpec / IvParameterSpec 拿 key/iv
```

3. 拿到 key/IV/明文，Python 用 `pycryptodome` 重現 AES，驗證能加回同樣的密文。

**`Cipher.doFinal` 是加密逆向的萬能攔截點**：不管 App 上層怎麼包裝，Java 的對稱/非對稱加密最終都呼叫它。hook 它 = 一次看到所有加解密的明文與密文。這招記起來，Ch 23 native 加密也是類似思想（找到那個「一定會經過的點」）。

## 對比與取捨：還原路徑怎麼選

| 金鑰藏在哪 | 靜態能拿到？ | 主要手段 | 難度 |
|---|---|---|---|
| 寫死在 Java 字串常數 | ✅ | jadx 搜就有 | 低 |
| Java 動態拼接（`a+b+c`） | ⚠️ 邏輯看得到，值要跑 | Frida hook 印值 | 中 |
| native `.so` 裡算 | ❌ 值看不到 | **hook native 方法回傳值**（本章 Step 3） | 中 |
| native + 反調試 + OLLVM 混淆 | ❌ | 逆 `.so`（Part 4）+ 繞反調試（Part 5） | 高 |

**核心取捨是「要不要逆 native」**：金鑰在 native，你有兩條路——**逆 `.so` 看它怎麼算**（Part 4，累但徹底，能離線復現）、或**hook 它的回傳值直接拿結果**（本章，快但每次要跑 App、金鑰若隨參數變就得每次 hook）。實務先 hook 拿值跑通整條鏈，確認方向對了，需要離線/大量重放時再考慮逆 native 把金鑰生成也搬進腳本。

## 踩雷集錦

1. **重放少帶了「隱藏參數」**：hook 印出的 `params` 常比抓包多欄位（`deviceId`、`appVersion`、固定 salt）。抓包看不到是因為它們可能在 header 或被伺服器忽略顯示。**以 hook 印的 params 為準**，不是以抓包為準——這是重放「簽名錯誤」最常見的原因。
2. **參數排序/拼接格式差一點，sign 完全不同**：雜湊有雪崩效應，`k=v&` 跟 `k:v;` 差一個符號、排序用 `sort` 還是不排、大小寫，算出來天差地別。用 Step 4 的黃金驗證（重算 == hook 印的）逐項對，對不上就是這裡有差。
3. **用抓包時的舊時間戳重放被拒**：伺服器驗 `ts` 在窗口內防重放。重放要**動態生成當下 `ts` 再簽**，不能原封不動重送舊請求。
4. **把 `sign` 自己也算進簽名**：簽名的輸入只有業務參數，算完才附上 `sign`。把 `sign` 欄位也丟進 `gen_sign` 會永遠對不上（雞生蛋）。
5. **金鑰是動態的卻當成常數**：hook 印出來的 key 若是 `md5(secret+ts)` 這種，換個 `ts` 就變。你腳本寫死那個值，換時間就失效。動態金鑰要把**金鑰生成邏輯也還原**進腳本（練習 B 的核心難點）。

## 進階：再往深一層

- **簽名參數的「順序」有時不是字典序**：有些 App 按「參數加入的順序」或「後端約定的固定順序」拼接，不是 `Collections.sort` 的字典序。靜態讀到用了 `TreeMap`（自動排序）還是 `LinkedHashMap`（保插入序），決定你 Python 端要不要 `sorted()`。讀錯資料結構 = 排序錯 = sign 全錯。
- **金鑰在 native 且不想每次 hook**：把 `getKey` 的 native 實作逆出來（Part 4），若它是 `固定salt XOR 某常數` 之類的純計算，就能在 Python 離線復現，擺脫「每次重放都要開 App hook」。這是從「能重放」升級到「離線大量重放」的關鍵。
- **RSA/非對稱簽名**：如果 `sign` 是 App 用**私鑰**簽的（RSA），你光有演算法沒用——私鑰在 App 裡，你得 dump 出私鑰（可能在 keystore/native）才能重放。hook `Signature.sign` 或 keystore 相關 API 是切入點，但硬體 keystore（TEE）裡的私鑰 dump 不出來，這時只能繼續 hook App 幫你簽，做不到完全離線。
- **協議還原是可自動化的**：一旦你摸清一個 App 的簽名套路，可以把「hook 出金鑰 + Python 重算」封裝成半自動工具（Frida RPC 把金鑰吐給 Python，Python 負責構造與重放），Ch 40 自動化會展開這個思路。

## 動手練習

1. 對一個你自己寫的、帶 `sign=HMAC-SHA256(sorted params, key)` 的 demo App/後端，走完整五步：抓包 → 認出 sign 是 64 hex → jadx 搜 `HmacSHA256` → hook 印 key 與 params → Python 重算驗證 == hook 印的 sign。
2. 故意在重放時漏掉一個 hook 才看得到的隱藏參數，看伺服器回「簽名錯誤」，再補上——親身體會「以 hook 的 params 為準」。
3. 把上面 App 的 `sign` 演算法改成不排序（按插入序），觀察你 Python 端還用 `sorted()` 時 sign 對不上，改成不排序才對——體會排序這一步的敏感。
4. 進階：做一個 body 被 AES 加密的 demo，hook `Cipher.doFinal` 印出明文與 hook `Cipher.init` 印出 key/IV，用 Python 重現加密。

## 本章重點整理

- 協議還原 = 還原**演算法 `f`**（靜態 jadx 讀骨架）+ **金鑰 `key`**（動態 Frida hook 印值）；伺服器和 App 共用這對，你重放要重現這對。
- 五步鏈：**抓包認參數（靠 hex 長度等特徵）→ jadx 定位加密函式 → Frida hook 印 params/key → Python 重算做黃金驗證（== hook 印的 sign）→ 重放**。
- **金鑰在 native** 時，hook 那個 native 方法的**回傳值**就能拿到金鑰，不必逆 `.so`（快但要跑 App）。
- **`Cipher.doFinal` 是 Java 加密的萬能攔截點**；body 被 AES 加密時 hook 它拿明文。
- 重放三個坑：**帶齊隱藏參數（以 hook 為準）、動態生成 `ts`、金鑰若動態要連生成邏輯一起還原**。

## 自我檢核

- [ ] 能說出「演算法靠靜態、金鑰靠動態」的理由，以及各用什麼工具
- [ ] 看到一個 64 hex 的參數，能推斷它可能是什麼、下一步怎麼在 jadx 定位
- [ ] 能解釋「重算的 sign == hook 印的 sign」為什麼是還原正確性的黃金驗證
- [ ] 知道金鑰在 native 時，怎麼不逆 `.so` 就拿到金鑰值
- [ ] 說得出重放失敗的三個最常見原因（隱藏參數、舊時間戳、動態金鑰當常數）

## 延伸閱讀

- **[OWASP MASTG — Android Network / Crypto Testing](https://mas.owasp.org/MASTG/techniques/android/)**
  - **讀哪裡**：network communication 與 cryptography 的測試技術段
  - **和本章的關聯**：把「還原簽名/加密」放進標準安全測試脈絡，含更多加密誤用的判斷
- **[Frida — JavaScript API（Interceptor / Java）](https://frida.re/docs/javascript-api/)**
  - **讀哪裡**：`Java.use`/`.implementation`、`Java.registerClass`、RPC exports
  - **和本章的關聯**：本章所有 hook 的 API 依據；進階的「Frida RPC 把金鑰吐給 Python」也在這
- **[HackTricks — Android API/協議分析](https://book.hacktricks.wiki/en/mobile-pentesting/android-app-pentesting/index.html)**
  - **讀哪裡**：抓包、加密 hook、`Cipher` 攔截那幾段
  - **為什麼值得讀**：大量現成的加密 hook 片段，`Cipher.doFinal`/`Cipher.init` 攔截的實戰版
- **[Python `hmac` / `hashlib` 官方文件](https://docs.python.org/3/library/hmac.html)**
  - **讀哪裡**：`hmac.new` 的用法與 `hashlib` 支援的演算法
  - **和本章的關聯**：重放腳本重算 sign 的權威依據；認清 `digestmod` 選對雜湊有多關鍵
- **[Frida CodeShare](https://codeshare.frida.re/)**
  - **讀哪裡**：搜 "cipher"、"crypto capture"、"universal hook" 類腳本
  - **為什麼值得讀**：現成的「一次攔截所有加密」腳本，讀它怎麼覆蓋各種 Cipher/Mac，比自己補全快

到這裡，Part 3 的動態插樁全串起來了——你能 hook、能抓包、能還原協議、能重放。下一個練習讓你**獨立**走完這條鏈，而且難度升級：金鑰不是常數，是動態產生的，你得連金鑰生成演算法一起還原。

→ [練習 B：用 Frida 還原請求簽名演算法](./practice-b-frida-signature.md)
