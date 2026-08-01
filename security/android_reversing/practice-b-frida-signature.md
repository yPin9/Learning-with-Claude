# 練習 B — 用 Frida 還原請求簽名演算法

> **目標**：獨立走完 Ch 18 那條攻擊鏈，而且難度升級——這次金鑰**不是常數**，是**執行期動態產生**的（`key = sha256(appSecret + ts)[:16]`，`appSecret` 藏在 native）。你的任務：hook 出金鑰生成邏輯與最終演算法、用 Python 重現、寫一支能對**任意新參數**重簽重放的腳本。這是「靜態讀骨架 + 動態抓祕密」兩條腿必須配合的典型題，也是練習 C（native 簽名）、final project 的前置能力。

> **環境**：AVD（Android 13 / API 33，x86_64，Google APIs）、`Frida 16.x`、`jadx`、`mitmproxy`、`python3`。**重放/簽名演算法（HMAC-SHA256、動態金鑰派生、參數排序）我在本 repo 沙箱用 Python 3 實跑**，輸出標「**實際輸出**」；Frida hook 腳本手寫、逐行解釋、標「**未實測，理論預期行為**」並附驗證步驟。你在自己的 AVD 上照驗證步驟跑，數字會因你的 `appSecret`/`ts` 不同而不同，但**驗證方法一致**。

---

## 情境設定

你在評估一個假想的登入 App `com.demo.login`（把它想成你有授權分析的目標）。抓包看到登入請求：

```
POST /api/login HTTP/1.1
Content-Type: application/json

{"username":"alice","password":"pw123","ts":"1717430400",
 "nonce":"9f3a","sign":"b647f8d1...0313cce"}
```

你想寫一支腳本，能**用任意帳密自動構造合法的登入請求**（做自動化測試、驗證伺服器有沒有驗好簽名）。擋路的就是那個 `sign`。

jadx 初步偵察，找到簽名相關的類（**近似反編譯輸出**）：

```java
public class SignUtil {
    // 對外：算 sign
    public static String genSign(Map<String, String> params) {
        String base = buildBase(params);          // 排序拼接
        String key  = deriveKey(getTs(params));    // ← 動態派生金鑰
        return hmacSha256(base, key);
    }
    // 金鑰派生：appSecret 從 native 拿，跟 ts 拼起來雜湊
    private static String deriveKey(String ts) {
        String secret = nativeGetSecret();         // ← native！靜態看不到值
        return sha256(secret + ts).substring(0, 16);
    }
    private static native String nativeGetSecret();
}
```

你**靜態讀出了骨架**：`sign = HMAC-SHA256(排序拼接的參數, key)`，而 `key = sha256(appSecret + ts)[:16]`。但 `appSecret` 在 native，靜態拿不到值——這就是你要動態 hook 的東西。

---

## 規格（你要交出什麼）

寫出兩份產物：

1. **一支 Frida 腳本** `hook_sign.js`：hook 出
   - `nativeGetSecret()` 的回傳值（`appSecret`）
   - `deriveKey(ts)` 的輸入 `ts` 與輸出 `key`（用來驗證你對派生邏輯的理解）
   - `genSign(params)` 的輸入 `params`（抓隱藏參數）與輸出 `sign`
2. **一支 Python 腳本** `replay.py`：不依賴 App，能對**任意** `username/password/ts/nonce` 自己派生金鑰、算 sign、構造合法 body。

**通過標準（黃金驗證）**：用**同一組 `params` 與 `ts`**，你 Python 算出的 `key` 與 `sign`，必須跟 Frida hook 印出來的**逐字元完全一致**。一致 = 還原正確。

---

## 期望輸出（跑通後大概長這樣）

Frida 端（**未實測，理論預期行為**，你 AVD 上的值會不同）：

```
[nativeGetSecret] -> d3m0_app_secret_2024
[deriveKey] ts=1717430400 -> key=8fc0d61aee5177c9
[genSign] params={username=alice, password=pw123, ts=1717430400, nonce=9f3a}
[genSign] sign  =b647f8d110c6664ddf7133a221de357d6f400e97e3a552447ff10cf800313cce
```

Python 端黃金驗證（**實際輸出**，我用 Python 3 實跑同一套邏輯）：

```
my_key == app_key : True
my_sign == app_sign: True
```

---

## 卡點提示（卡住再看，別急著翻解答）

- **卡在「hook native 方法卻不知道怎麼拿回傳值」**：`nativeGetSecret` 雖是 native，但它是 Java 宣告的 native method，你可以直接 `Java.use(...).nativeGetSecret.implementation` 從 Java 層 hook 它、`this.nativeGetSecret()` 呼叫原實作、攔它的回傳字串。**不用逆 `.so`**（Ch 18 Step 3 的偷懶招）。
- **卡在「Python 算的 sign 跟 hook 印的對不上」**：99% 是這四項之一——(a) 參數排序方式錯（字典序 vs 插入序，看 jadx 是 `TreeMap` 還 `LinkedHashMap`）、(b) 拼接格式錯（`k=v&` 的分隔符/去尾）、(c) 金鑰派生錯（`substring(0,16)` 是取前 16 個 **hex 字元** 不是 16 bytes）、(d) `sign` 把自己也算進去了。逐項用 hook 印的中間值（`base`、`key`）對。
- **卡在「substring(0,16) 到底取什麼」**：`sha256(...)` 在 Java 通常回傳 **hex 字串**（64 個字元），`.substring(0,16)` 取**前 16 個 hex 字元**。Python 端對應 `hashlib.sha256(...).hexdigest()[:16]`，不是 `.digest()[:16]`（那是 16 bytes，完全不同）。這是最容易錯的一步。
- **卡在「重放時 ts 該用什麼」**：派生金鑰依賴 `ts`，重放要**先定 `ts`（用當下時間）→ 用它派生 key → 用同一個 `ts` 進 params 一起簽**。三處的 `ts` 必須是同一個值，否則 key 對不上簽出來的 base。

---

## 分步指引（≥5 步）

### Step 1：靜態偵察，畫出演算法骨架

jadx 開 `com.demo.login`，搜 `"HmacSHA256"` / `"sign"` 定位 `SignUtil`。讀出並寫下：
- `sign` 的公式：`sign = HMAC-SHA256(base, key)`
- `base` 怎麼拼：參數排序（哪種？）→ `k=v&k=v...`
- `key` 怎麼來：`key = sha256(appSecret + ts)[:16]`，`appSecret` 在 native

**產出**：一張紙上的公式圖，標清楚哪些值靜態已知、哪些要動態抓（`appSecret`）。

### Step 2：hook 印出所有中間值

寫 `hook_sign.js`，hook 三個點：`nativeGetSecret`（拿 `appSecret`）、`deriveKey`（驗派生）、`genSign`（拿 params + 最終 sign）。跑起來、在 App 登入一次、收集印出的值。

### Step 3：比對抓包與 hook 的 params

把 hook 印的 `params` 跟你 mitmproxy 抓到的 body 逐欄比。有沒有多欄位（隱藏參數）？有的話記下來，重放時要帶。

### Step 4：Python 重現，做黃金驗證

用 hook 印的**同一組** `params` 和 `ts`，在 Python 重算 `key` 和 `sign`。跟 hook 印的比對。**不一致就回 Step 1/2 逐項查**（排序？拼接？substring 取 hex 還 bytes？）。一致才往下。

### Step 5：寫重放腳本，對新參數簽

把驗證過的邏輯封成 `replay.py`：給定任意 `username/password`，自己生 `ts`（當下時間）與 `nonce`、派生 key、算 sign、組 body。（對授權測試環境）發出去看伺服器接不接受。

### Step 6（延伸）：離線化

現在 `appSecret` 是你 hook 出來的常數。若它每次啟動都變（更硬的設計），你得逆 native 看它怎麼生成——那是練習 C 的範疇。本練習 `appSecret` 是固定的，hook 一次即可寫死進腳本。

---

## 完整參考解答

<details>
<summary>點開看參考解答（Frida hook 腳本 + Python 重放，含實跑輸出）</summary>

### `hook_sign.js`（**未實測，理論預期行為**）

```javascript
// hook_sign.js —— 還原動態金鑰簽名的三個關鍵點
Java.perform(function () {
    var SignUtil = Java.use("com.demo.login.SignUtil");

    // (1) 攔 native 金鑰種子 appSecret
    //     nativeGetSecret 是 Java 宣告的 native method，從 Java 層即可 hook 回傳值
    SignUtil.nativeGetSecret.implementation = function () {
        var secret = this.nativeGetSecret();          // 讓 native 算完，攔回傳
        console.log("[nativeGetSecret] -> " + secret);
        return secret;                                 // 原樣放行，不改行為
    };

    // (2) 攔金鑰派生，驗證「sha256(secret+ts)[:16]」的理解
    SignUtil.deriveKey.implementation = function (ts) {
        var key = this.deriveKey(ts);
        console.log("[deriveKey] ts=" + ts + " -> key=" + key);
        return key;
    };

    // (3) 攔對外入口，拿到真正參與簽名的 params（含隱藏參數）與最終 sign
    SignUtil.genSign.implementation = function (params) {
        var sign = this.genSign(params);
        console.log("[genSign] params=" + params.toString());
        console.log("[genSign] sign  =" + sign);
        return sign;
    };
});
```

逐點解釋：

- **(1) hook native method 的回傳值**：`nativeGetSecret` 的實作在 `.so`，但它有個 Java 端的宣告，`Java.use(...).nativeGetSecret.implementation` 就能把它包起來。`this.nativeGetSecret()` 呼叫原（native）實作、拿到它算好的 `appSecret`——**你完全不碰 `.so`**，直接拿結果。這是「金鑰在 native 但不逆 native」的核心技巧。
- **(2) hook `deriveKey` 驗證理解**：印出 `ts → key`，讓你確認 Python 端的 `sha256(secret+ts)[:16]` 派生方式正確（尤其驗證 substring 取的是 hex 字元）。
- **(3) hook `genSign` 抓隱藏參數**：`params.toString()` 印出**執行期真正**被簽的參數集。如果它比抓包多欄位，重放必須補上。
- 三個 hook 都是「呼叫原實作 → 印 → 原樣回傳」，純觀察不改行為，不影響 App 正常登入。

驗證步驟（在你 AVD 上）：`frida -U -f com.demo.login -l hook_sign.js`，App 裡登入一次，收集三行輸出。把 `[genSign] params` 跟 mitmproxy 抓的 body 逐欄對，確認有沒有隱藏參數。

### `replay.py`（**實際輸出**，我用 Python 3 實跑）

```python
import hashlib, hmac, time, json

# ============ 從 Frida hook 還原出來的常數與邏輯 ============
RECOVERED_SECRET = "d3m0_app_secret_2024"   # ← [nativeGetSecret] 印出來的

def derive_key(ts: str) -> str:
    # 對應 Java: sha256(secret + ts).substring(0, 16)
    # 關鍵：substring 取的是「hex 字串」的前 16 字元 → hexdigest()[:16]，不是 digest()[:16]
    return hashlib.sha256((RECOVERED_SECRET + ts).encode()).hexdigest()[:16]

def gen_sign(params: dict, key: str) -> str:
    # 對應 Java: 參數字典序排序 → k=v&k=v... → HMAC-SHA256(base, key)
    base = "&".join(f"{k}={params[k]}" for k in sorted(params))
    return hmac.new(key.encode(), base.encode(), hashlib.sha256).hexdigest()

def build_request(username: str, password: str, nonce: str, ts: str = None) -> dict:
    if ts is None:
        ts = str(int(time.time()))          # 重放用當下時間戳
    params = {"username": username, "password": password, "ts": ts, "nonce": nonce}
    key = derive_key(ts)                     # 先派生 key（依賴 ts）
    body = dict(params)
    body["sign"] = gen_sign(params, key)     # sign 只簽業務參數，不含 sign 自己
    return body

# ---------- 黃金驗證：用 hook 印的同一組 ts/params，比對是否一致 ----------
if __name__ == "__main__":
    # 這組是 Frida hook 當時印出來的（拿來驗證還原正確性）
    app_ts = "1717430400"
    app_params = {"username": "alice", "password": "pw123", "ts": app_ts, "nonce": "9f3a"}
    app_key  = "8fc0d61aee5177c9"                  # hook [deriveKey] 印的
    app_sign = ("b647f8d110c6664ddf7133a221de357d"
                "6f400e97e3a552447ff10cf800313cce") # hook [genSign] 印的

    my_key  = derive_key(app_ts)
    my_sign = gen_sign(app_params, my_key)
    print("my_key == app_key :", my_key == app_key)
    print("my_sign == app_sign:", my_sign == app_sign)

    # 重放：對「新」帳密/時間戳自動構造合法請求
    req = build_request("bob", "hunter2", "abcd")
    print("replay body:", json.dumps(req, sort_keys=True))
```

跑它（**實際輸出**——`RECOVERED_SECRET`、`app_key`、`app_sign` 用我在沙箱造的目標值算出，兩個 `True` 證明還原邏輯正確；`replay body` 的 `ts` 隨執行時間變）：

```
my_key == app_key : True
my_sign == app_sign: True
replay body: {"nonce": "abcd", "password": "hunter2", "sign": "...", "ts": "17xxxxxxxx", "username": "bob"}
```

兩個 `True` 是關鍵：代表 `derive_key`（含 substring 取 hex 這關）與 `gen_sign`（含排序、拼接、HMAC）**都跟目標 App 一致**。此後 `build_request` 能對任意輸入產生合法簽名。

**這份解答我實跑驗證過的部分**：`derive_key` + `gen_sign` + 黃金驗證的兩個 `True`（Python 3，`hashlib`/`hmac` 標準庫）。**未實測的部分**：Frida 腳本（沒有 AVD），語法為 Frida 16.x 標準寫法。

</details>

---

## 測試表（自己逐項打勾）

| # | 檢查項 | 通過條件 |
|---|---|---|
| 1 | hook 印出 `appSecret` | `[nativeGetSecret]` 有印出非空字串 |
| 2 | hook 印出 `deriveKey` 的 ts→key | 有 `[deriveKey]` 且 key 是 16 hex 字元 |
| 3 | hook 印出 `genSign` 的 params | params 包含所有欄位（比對抓包，抓出隱藏參數） |
| 4 | Python `derive_key` == hook 的 key | `my_key == app_key` 為 `True` |
| 5 | Python `gen_sign` == hook 的 sign | `my_sign == app_sign` 為 `True`（黃金驗證） |
| 6 | 改一個參數值，sign 隨之改變 | 改 `password`，重算 sign 完全不同（雪崩） |
| 7 | 對新 ts 能派生新 key 並簽出 | `build_request` 對任意輸入不報錯、body 帶 sign |
| 8 | （若有授權環境）伺服器接受重放 | 回 200 / 業務成功，非「簽名錯誤」 |

---

## 常見失敗與對照（Debug 表）

| 症狀 | 最可能原因 | 怎麼修 |
|---|---|---|
| `my_key != app_key` | substring 取成 `digest()[:16]`（16 bytes）而非 `hexdigest()[:16]`（16 hex 字元） | 改用 `hexdigest()[:16]` |
| `my_sign != app_sign` 但 key 對 | 排序方式錯，或拼接分隔符/去尾錯 | 用 hook 印 `base` 對照；確認字典序 vs 插入序 |
| sign 永遠對不上，怎麼改都錯 | 把 `sign` 欄位也算進 base | `gen_sign` 只吃業務參數 |
| hook `nativeGetSecret` 報 no such method | 方法名/類名被混淆或簽名不符 | jadx 確認真實類名；用 `enumerateLoadedClasses`（Ch 15）找 |
| 伺服器回「簽名錯誤」但本地驗證 True | 漏帶隱藏參數，或 ts 過期 | 以 hook 印的 params 為準；重放用當下 ts |

---

## 延伸挑戰

1. **金鑰種子每次啟動都變**：把 `appSecret` 改成 App 啟動時隨機生成（存在記憶體）。現在 hook 一次寫死行不通——你得讓重放腳本每次都先 hook 拿當前 secret（Frida RPC 把 secret 吐給 Python），或逆 native 看生成邏輯。
2. **簽名搬進 native**：`genSign` 整個實作在 `.so` 裡（不再有 Java 骨架可讀）。你只能 hook 它的 JNI 入口印進出、或逆 `.so`——這正是練習 C 的內容，本練習是它的暖身。
3. **加上防重放 nonce 校驗**：伺服器記錄用過的 `nonce`，重複的 `nonce` 直接拒。你的重放腳本要每次生成不重複的 `nonce`（`secrets.token_hex`）。
4. **body 再套一層 AES 加密**：業務參數先 AES 加密成 `data` 欄位再簽。你要多 hook `Cipher.doFinal`/`Cipher.init` 拿 key/IV，Python 端用 `pycryptodome` 重現加密（Ch 18 的硬變體）。

---

## 自我檢核

- [ ] 我能不看解答，說出「金鑰在 native 但不逆 native」怎麼拿到金鑰值
- [ ] 我知道 `sha256(...).substring(0,16)` 對應 Python 的 `hexdigest()[:16]` 而非 `digest()[:16]`，並說得出差別
- [ ] 我能解釋「黃金驗證（重算 == hook 印的）為 True」為什麼證明還原完全正確
- [ ] 我知道重放時 `ts` 為什麼三處必須同值、為什麼要用當下時間
- [ ] 我能列出「本地驗證 True 但伺服器仍拒」的兩個原因（隱藏參數、nonce/ts 過期）
- [ ] 我理解這個練習的 `appSecret` 若改成每次變，我的腳本要怎麼調整（RPC 拿值 或 逆 native）

---

做完這題，你已經能獨立走完「抓包 → 定位 → hook → 還原 → 重放」的完整鏈，而且處理過動態金鑰這個現實難點。接下來 Part 4 我們往下鑽一層：當簽名/加密**整個搬進 native `.so`**、連 Java 骨架都沒有時，你要怎麼逆。第一站是搞懂 Java 與 native 之間那道橋——JNI。

→ [Ch 19 JNI 機制：Java 與 native 的邊界](./19-jni-mechanism.md)
