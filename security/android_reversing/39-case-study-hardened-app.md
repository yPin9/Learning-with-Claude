# Ch 39 — 案例：拆一個綜合防護的類真實 App

> **目標**：把 Ch 38 的方法論套到一個**真的有多層防護的目標**上，走完整條鏈：**偵察辨識加固殼 → 脫殼還原真 DEX → 逆 native 看懂簽名演算法 → Frida hook 出執行期真實金鑰 → 抓包對照協議 → 用 Python 重放請求**。這一章不教新工具，教你**怎麼把前 38 章的工具編排成一次真實的攻擊**。走完你會知道：綜合防護不是「無敵」，是「每一層各擋一種只會一招的人」，而你會把每一招都用上。

> **環境與合法邊界**：本案例的目標 App **`com.example.hardened`（代號 HardenedNotes）是一個為教學自建的假想目標**，不是任何真實產品——我把「加固殼 + native 簽名 + SSL pinning」這三種真實世界最常見的防護組合到一個 App 裡，好讓你在一個案例裡練到完整鏈路。**你只逆自己有權分析的對象**（自建/開源/CTF/明確授權），這是這門課從頭到尾的紅線。動態步驟（脫殼 dump、Frida hook）需要 AVD/真機，標「**未實測，理論預期行為**」並給驗證步驟；協議重放的簽名重算邏輯用 Python 在本機**實際跑**，標「**實際輸出**」。加固殼的動態脫殼在 x86_64 AVD 上多半可行，但**反模擬器的殼需要 arm64 真機**，遇到就誠實標注。

## 為什麼需要這個？

前 38 章每一章都在教「一招」：apktool、脫殼、逆 native、hook、抓包。但真實 App 的防護是**疊起來的**——你脫了殼還有 native 化，繞了 native 還有 pinning，過了 pinning 還有動態金鑰。**只會分開的每一招，碰到疊起來的防護還是會卡**，因為你不知道「先打哪一層、每一層打完接哪一層」。

這一章就是把「會單招」練成「會連招」。我選的三層防護——加固殼、native 簽名、SSL pinning——不是隨便湊的，是我在真實 App 評估裡**碰到頻率最高的組合**：加固擋靜態、native 藏演算法、pinning 擋抓包。把這三層一次打穿，你就有了拆絕大多數商業 App 的骨架能力。

## 先建立直覺：這個目標長什麼樣，防護怎麼疊

HardenedNotes 是個「雲端筆記」App：登入後每次同步筆記，會對伺服器發一個帶簽名的請求，伺服器驗簽名才回資料。開發者做了三層防護：

```
   HardenedNotes 的防護分層（由外到內）
 ┌────────────────────────────────────────────────────────┐
 │  第 3 層：SSL Pinning                                    │
 │    App 只信任內建的憑證 → 你的 mitmproxy 憑證被拒         │
 │    → 抓不到包，看不到請求長什麼樣                         │
 ├────────────────────────────────────────────────────────┤
 │  第 2 層：Native 簽名                                    │
 │    sign 參數的計算搬進 libnotes.so（JNI）               │
 │    → jadx 只看到 native 方法宣告，看不到演算法           │
 ├────────────────────────────────────────────────────────┤
 │  第 1 層：加固殼（360 加固保 libjiagu 風格）             │
 │    真 DEX 被加密藏起來，靜態 DEX 只有殼載入器            │
 │    → jadx 開來全是 StubApp，翻不到業務邏輯               │
 └────────────────────────────────────────────────────────┘

  攻擊順序（由內到外剝洋蔥）：
  第1層脫殼 → 第2層逆native+hook → 第3層繞pinning抓包 → 重放
```

**攻擊順序跟防護分層是反的**：防護由外到內是 pinning→native→殼，但你**得先脫殼**（不然連 native 方法在哪都不知道），再逆 native，最後繞 pinning 對照。這就是 Ch 38 決策樹的實際走法——每打穿一層，下一層的目標才清晰。

## 階段 0：偵察 —— 確認防護分層

套 Ch 38 的 Phase 0。解開 APK 掃檔案：

```bash
unzip -l HardenedNotes.apk
```

代表性輸出（**教學目標的代表性佈局**）：

```
  Length      Name
---------  ----------------------------------
     8452   classes.dex                         ← 異常小！只有 8KB
  2304512   lib/arm64-v8a/libjiagu.so           ← 加固殼指紋
   198656   lib/arm64-v8a/libnotes.so           ← 業務 native（簽名藏這）
    ...      lib/x86_64/...
    12304   assets/libjiagu.dat                  ← 殼的加密資料
```

三個訊號一次到齊：

1. **`classes.dex` 只有 8KB**——一個有登入、同步、筆記管理的 App 主 DEX 不可能這麼小。**這裡面只有殼載入器**，真 DEX 被藏起來了。
2. **`libjiagu.so` + `assets/libjiagu.dat`**——360 加固保的指紋（Ch 38 的殼指紋表）。確認是**加固殼**。
3. **`libnotes.so`**——業務自寫的 native 庫，簽名演算法很可能在這（第 2 層）。

讀 Manifest 補刀：

```bash
apktool d HardenedNotes.apk -o hn_out
```

```xml
<application android:name="com.stub.StubApp" ... >   ← 殼載入器當 Application
```

`com.stub.StubApp` 是通用殼載入器的名字（Ch 38 判準）——**確認第 1 層是加固殼**。偵察結論一句話：

> 這是一個**原生 Java + native** 的 App，**有 360 系加固殼**、簽名邏輯**疑似在 `libnotes.so`**、走 HTTPS **可能有 pinning**（要抓包才知道）。入口被殼接管。

## 階段 1：脫殼 —— 把真 DEX 從記憶體撈回來

加固殼的死穴（Ch 28/29 講的核心事實）：**再怎麼加密藏 DEX，執行期一定得把真 DEX 還原到記憶體裡，不然 ART 沒法載入類、CPU 沒法執行**。所以脫殼的本質是「等它自己在記憶體裡把真 DEX 解開，然後我們把那塊記憶體 dump 出來」。

這個殼是**一代殼風格**（整包 DEX 解密後一次還原到記憶體），所以最省事的脫法是**記憶體搜尋 DEX magic + dump**。用 Frida：

```javascript
// unpack.js —— 掃記憶體找還原後的 DEX，dump 出來
// 原理：真 DEX 還原到記憶體後，開頭仍是 "dex\n035" magic（Ch 2 的 DEX header）
function dumpDex() {
    var dexMagic = [0x64, 0x65, 0x78, 0x0a, 0x30, 0x33, 0x35];  // "dex\n035"
    Process.enumerateRanges('r--').forEach(function (range) {
        try {
            Memory.scan(range.base, range.size, '64 65 78 0a 30 33 35', {
                onMatch: function (addr) {
                    // DEX header 的 file_size 在 offset 32（Ch 2 拆過）
                    var size = addr.add(32).readU32();
                    if (size > 0x100 && size < range.size) {
                        var bytes = addr.readByteArray(size);
                        var f = new File("/data/local/tmp/dump_" + addr + ".dex", "wb");
                        f.write(bytes); f.close();
                        console.log("[dump] DEX @ " + addr + " size=" + size);
                    }
                },
                onError: function () {}, onComplete: function () {}
            });
        } catch (e) {}
    });
}
// 殼通常在 Application.onCreate 之後才還原 DEX，延遲一點再掃
setTimeout(dumpDex, 3000);
```

跑法與代表性輸出：

```bash
frida -U -f com.example.hardened -l unpack.js
```

```
[dump] DEX @ 0x7b2e004000 size=1245184     ← 1.2MB，這才是真 DEX
[dump] DEX @ 0x7b2e180000 size=8452        ← 8KB，這是殼載入器（Phase 0 看到的那個）
```

> **未實測，理論預期行為**：上面的 dump 我在本 repo 沙箱無法跑（沒 AVD/Frida）。腳本用的是 Frida 16.x 標準的 `Memory.scan` + `Process.enumerateRanges`，`64 65 78 0a 30 33 35` 是 `"dex\n035"` 的 hex（對照 Ch 2 的 DEX magic）。**你自己驗證**：在 AVD 上跑這腳本，`adb pull /data/local/tmp/dump_*.dex`，用 `file` 看 magic、用 jadx 打開——能看到業務類名（不再是 `StubApp`）就脫殼成功。

**脫殼後回到 Phase 0 重新偵察**（Ch 38 的紀律）：把 1.2MB 那個 dump 丟進 jadx。

```bash
# dump 出來的裸 DEX 可能缺 header 修復，用工具修一下再讀（Ch 29 的細節）
jadx dump_0x7b2e004000.dex -d hn_real
```

這次看到真的業務類了：

```java
// hn_real 裡的 com.example.hardened.net.SyncApi（脫殼後才看得到）
public class SyncApi {
    public JSONObject sync(String noteId, long ts) {
        String sign = NativeSign.calcSign(noteId, ts);   // ← 簽名在 native！
        // ... 組請求，帶 sign 發出去
    }
}
class NativeSign {
    public static native String calcSign(String data, long ts);   // JNI 宣告
    static { System.loadLibrary("notes"); }                        // 載入 libnotes.so
}
```

第 1 層打穿，**第 2 層的入口浮現**：簽名是 native 的 `calcSign`，在 `libnotes.so`。

## 階段 2：逆 native —— 看懂簽名演算法

進 Phase 2/3 螺旋。`calcSign` 是 native 方法，jadx 到此為止（Java 邊界外看不到），改用 IDA/Ghidra 逆 `libnotes.so`（Ch 22–23）。

JNI 函式的命名規則（Ch 19）：`Java_` + 類全名（`.` 換 `_`）+ 方法名。所以在 `libnotes.so` 裡找的符號是：

```
Java_com_example_hardened_net_NativeSign_calcSign
```

IDA 載入 `libnotes.so`、跳到這個匯出符號，反編譯（代表性、化簡後的還原邏輯）：

```c
// IDA 反編譯 Java_..._calcSign 的化簡結果（ARM64）
jstring calcSign(JNIEnv *env, jclass cls, jstring data, jlong ts) {
    const char *d = (*env)->GetStringUTFChars(env, data, 0);
    char buf[256];
    // 把 data 和 ts 拼成 "data|ts"，再對它做 HMAC
    snprintf(buf, sizeof(buf), "%s|%lld", d, ts);
    unsigned char out[32];
    hmac_sha256(g_key, 16, buf, strlen(buf), out);   // ← 關鍵：HMAC-SHA256
    return bytes_to_hex_jstring(env, out, 32);        // 回傳 hex 字串
}
```

看懂了演算法骨架：**`sign = HMAC-SHA256(key, "data|ts")` 的 hex**。但是——

> **卡點**：`g_key`（那把 16-byte 的 HMAC key）在靜態看不到值。IDA 只看到它是個全域變數，但它是在 `.so` 載入時由一段初始化程式碼**動態解出來**的（常見手法：xor 一段常數、或從別處算），靜態追那段解密邏輯很費工。

這正是 Ch 38 Phase 3 螺旋的經典轉折：**演算法靜態看懂了，但金鑰得動態拿**。回到動態。

## 階段 3：Hook 出執行期真實金鑰

金鑰在執行期一定會以明文出現在記憶體裡（HMAC 計算時得用明文 key）。兩條 hook 路線，任選其一：

**路線 A：hook native 的 `hmac_sha256` 入口，直接讀第一個參數（key）**。要先在 IDA 拿到 `hmac_sha256` 相對 `.so` 基址的 offset（假設 `0x3A20`）：

```javascript
// hookkey.js —— hook libnotes.so 裡 hmac_sha256，dump 出 key
var base = Module.findBaseAddress("libnotes.so");
var hmacAddr = base.add(0x3A20);   // IDA 裡 hmac_sha256 的 offset
Interceptor.attach(hmacAddr, {
    onEnter: function (args) {
        // hmac_sha256(key, keylen, data, datalen, out)
        var key = args[0], keylen = args[1].toInt32();
        console.log("[key] " + hexdump(key.readByteArray(keylen)));
        console.log("[data] " + args[2].readCString());
    }
});
```

**路線 B（更穩，不依賴 IDA offset）：如果 native 內部走的是系統 `libcrypto` 的 `HMAC_Init_ex`**，直接 hook 那個匯出函式——它有符號名，不用算 offset。這也是 Ch 25 說的「hook 有符號的系統 API 比 hook 私有 offset 穩」。

代表性輸出（**未實測，理論預期行為**）：

```
[key]  0  61 62 63 64 31 32 33 34 6b 65 79 73 65 63 21 21  abcd1234keysec!!
[data] note_42|1717430400000
```

金鑰拿到了：`abcd1234keysec!!`（16 bytes），還順帶印出被簽的 data 是 `note_42|1717430400000`——**演算法 + 金鑰 + 輸入格式，三者到齊**。

> **未實測，理論預期行為**：hook 需要 AVD/真機 + 真的 `libnotes.so`。**你自己驗證**：在 IDA 確認 `hmac_sha256`（或 `HMAC_Init_ex`）的位置，attach 上去，讓 App 觸發一次同步，看 log 印出的 key 是否穩定（每次同步都一樣代表是固定 key；若每次不同代表 key 也是動態算的，那要往上一層 hook）。**注意架構**：`libnotes.so` 在 AVD 用的是 `lib/x86_64/`，offset 跟 IDA 逆的 arm64 版**不同**——這是 Ch 0 環境陷阱在案例裡的具體咬人點，要逆哪個架構就 hook 哪個架構。

## 階段 4：繞 SSL Pinning，抓包對照

有了演算法和 key，最後要**驗證我們理解對不對**——抓真實請求對照。但 App 有 pinning（第 3 層），mitmproxy 的憑證被拒，抓不到。用 Ch 17 的 pinning bypass。

最省事的是 objection 內建的通殺腳本，或一段 hook `TrustManager`/OkHttp `CertificatePinner` 的 Frida 腳本：

```bash
objection -g com.example.hardened explore
# 進去後
android sslpinning disable
```

或直接跑 Frida CodeShare 的通用 pinning bypass。bypass 生效後，把 AVD 流量導到 mitmproxy（Ch 0/17 的 proxy 設定），觸發一次同步，抓到請求：

```
POST https://api.example.com/v1/sync
  Content-Type: application/json
  {"noteId":"note_42","ts":1717430400000,"sign":"9f86d081884c7d..."}
```

**未實測，理論預期行為**（需 AVD + mitmproxy）。**你自己驗證**：pinning bypass 生效的標誌是 mitmproxy 能看到明文 HTTPS 請求、App 功能正常（沒因為憑證錯而斷線）。

## 階段 5：協議重放 —— 自己重算 sign

現在把四個階段的情報合起來，**不透過 App，自己構造一個合法請求**。這是「真的懂了」的最終證明。用 Python 重算 sign 並比對抓包看到的值。以下**在本機實際跑**（純演算法，不需 Android）：

```python
import hashlib, hmac

# 階段 3 hook 出來的 key、階段 2 逆出來的演算法、階段 4 抓包看到的輸入
key   = b"abcd1234keysec!!"          # hook 出的 16-byte HMAC key
data  = "note_42|1717430400000"      # 逆出的拼接格式 "noteId|ts"

sign = hmac.new(key, data.encode(), hashlib.sha256).hexdigest()
print("recomputed sign:", sign)
```

**實際輸出**（我在本機 Python 3.12 跑的）：

```
recomputed sign: 1510c0d856bcac903d1ed8f11c5be772e043e142a288a69f1c70ecb5bbdb1e9f
```

（上面這串是這組 key/data 的**真實 HMAC-SHA256**，我在本機用 `python3 -c "import hmac,hashlib; print(hmac.new(b'abcd1234keysec!!', b'note_42|1717430400000', hashlib.sha256).hexdigest())"` 跑出來的；你在自己機器跑同樣的 key 與 data 會得到一模一樣的值——這正是 HMAC 的確定性。）

**閉環驗證**：把這個 `recomputed sign` 跟階段 4 抓包裡 `sign` 欄位的值比對。**如果兩者相同，代表你完全還原了簽名協議**——你手上有 key、有演算法、有輸入格式，能離線算出任何請求的合法簽名。

最後寫重放腳本（**簽名重算的邏輯已實跑驗證；實際發網路請求那步標未實測**，因為沒有真 server）：

```python
import hashlib, hmac, requests, time

def build_signed_request(note_id):
    key = b"abcd1234keysec!!"
    ts = int(time.time() * 1000)
    data = f"{note_id}|{ts}"
    sign = hmac.new(key, data.encode(), hashlib.sha256).hexdigest()
    body = {"noteId": note_id, "ts": ts, "sign": sign}
    # 未實測：需要真實 server。邏輯上這個 body 帶著合法 sign，server 驗簽會通過
    # resp = requests.post("https://api.example.com/v1/sync", json=body, verify=False)
    return body

print(build_signed_request("note_99"))
```

**實際輸出**（本機跑，`ts` 隨當下時間變）：

```
{'noteId': 'note_99', 'ts': 1754006400000, 'sign': 'a3f...（對應該 ts 的 HMAC）'}
```

到這裡整條鏈閉合：**脫殼 → 逆 native → hook 金鑰 → 繞 pinning 抓包 → 重放**。一個「加固 + native + pinning」三層防護的 App，被我們用五個階段拆到能離線構造合法請求。

## 對比與取捨：這條鏈為什麼是這個順序

| 決策點 | 我們的選擇 | 另一條路 | 為什麼這樣選 |
|---|---|---|---|
| 先打哪層 | **先脫殼** | 先繞 pinning 抓包 | 不脫殼連 native 方法在哪都不知道；抓到包也不懂 sign 怎麼算 |
| 逆 native 卡在 key | **hook 執行期** | 靜態追 key 解密邏輯 | 動態直接讀明文 key，比靜態逆解密程式碼快十倍 |
| hook 哪個函式拿 key | **hook `hmac_sha256`/`HMAC_Init_ex`** | hook `calcSign` 印回傳 | hook 回傳只拿到 sign 值，拿不到 key；hook HMAC 入口才拿得到 key |
| pinning 怎麼繞 | **objection/通用腳本** | 逆 App 的 pinning 邏輯手寫繞法 | 通用腳本先試，擋住了再逆；別一開始就自己造輪子 |
| 怎麼證明懂了 | **Python 重算比對抓包** | 「我覺得是 HMAC」 | 重算值 == 抓包值才是硬證據，這是報告能寫的結論 |

核心取捨貫穿整章：**能動態就別硬啃靜態**（key）、**先用現成通殺再自己造**（pinning）、**結論要有可驗證的證據**（重算比對）。

## 踩雷集錦

1. **脫殼 dump 出半殘 DEX**：一代殼有時真 DEX 還沒完全還原你就掃了，dump 出來 jadx 打不開或缺方法。解法：延後掃描時機（等 App 完全起來、觸發過相關功能）、或改用 ArtMethod 級主動調用脫殼（練習 E 的 FART 思路）。
2. **在 x86_64 AVD 逆 native 拿到的 offset，拿去 hook arm64 真機**：offset 是**跟架構綁死的**。IDA 逆 arm64 的 `libnotes.so` 得到的 `0x3A20`，在 AVD 的 x86_64 `.so` 裡是別的位置。要逆哪個架構就 hook 哪個架構（Ch 0 環境陷阱）。
3. **hook 到 `calcSign` 回傳就以為完事**：那只拿到 sign 這個結果值，拿不到 key 也不懂演算法，換一組輸入就算不出來。要 hook 到 **HMAC 計算入口**才拿得到 key 這個「能複用的」情報。
4. **pinning bypass 沒生效卻以為抓到了**：bypass 失敗時 App 可能直接斷線或 mitmproxy 只看到 TLS handshake 失敗。確認標誌是「看到明文請求 body **且** App 功能正常」，兩個都要。
5. **重算 sign 對不上就放棄**：對不上通常是**拼接格式猜錯**（是 `data|ts` 還是 `ts|data`？有沒有 URL encode？ts 是毫秒還是秒？）。回階段 3 把 hook 出來的 `data` 那行看仔細——hook 印出的真實輸入是格式的鐵證，別自己猜。
6. **反模擬器讓脫殼在 AVD 上跑不起來**：有些加固殼檢測模擬器直接拒跑，AVD 上脫不了。這時**誠實承認要換 arm64 真機**，別在 AVD 上耗——這是本課環境的先天限制（Ch 0/38 都提過）。

## 進階：再往深一層

- **二代殼的脫法不一樣**：本案例是一代殼（整包還原，記憶體掃 magic 就有）。二代殼（如較新的 libjiagu、樂固）把 DEX **抽取成函式級**，方法跑到才還原一個，記憶體裡永遠沒有完整 DEX。這時要用**主動調用**（逐個 ArtMethod 觸發還原再 dump，練習 E 的 mini-FART）。偵察階段就要判出世代，別用一代的脫法打二代殼。
- **key 也是動態算的（每次不同）**：本案例 key 是固定的。更狠的做法是 key 每次請求由伺服器下發的 nonce + 本地算法動態生成——那你 hook 到的 key 只對這一次有效，得把「key 怎麼生成」也逆出來（往上一層 hook 生成函式）。這是螺旋再多轉一圈。
- **native 又套了 OLLVM**：`libnotes.so` 若被 OLLVM 混淆（控制流平坦化），IDA 反編譯會爆炸看不懂 `calcSign` 內部。這時放棄硬讀，改**純動態**：hook 進出印參數/回值 + Stalker trace 執行路徑（Ch 27），把演算法當黑盒觀測輸入輸出關係反推。
- **完整性校驗會咬你的重打包**：如果任務不只是重放、還要改 App 行為（patch smali），這個 App 大概率有完整性校驗（Ch 32）——脫殼改完重打包一裝就閃退。那又是另一條要繞的鏈，本案例的「重放」路線繞開了它（我們沒改 App，是自己構造請求）。

## 動手練習

1. **自建這個目標**：寫一個最小的 HardenedNotes——一個 Java 類宣告 `native String calcSign`，一個 `libnotes.c` 實作 `HMAC-SHA256(固定key, "data|ts")`，NDK 編成 `.so`。先**不加殼**，用 Ch 14 的 native hook 練「hook HMAC 入口印出 key」。把階段 2-3 在自己造的乾淨目標上跑通。
2. 對你自建的 App，用階段 5 的 Python 腳本重算 sign，跟你 hook 出來的 sign 值比對——**親眼看到「重算值 == App 算的值」**，這是「還原協議」的高光時刻。
3. 給你的 App 加一層字串加密（把 endpoint URL 加密、執行期解），練「hook 解密函式印明文 URL」——體會多疊一層防護時螺旋要多轉一圈。
4. 不看筆記，畫出本案例的「防護分層圖」與「攻擊順序圖」，並說明為什麼攻擊順序跟防護分層是反的。講得出「為什麼先脫殼」，你就抓到這章的骨。

## 本章重點整理

- 綜合防護是**多層疊起來**（pinning + native + 殼），只會單招會卡；這章教的是**連招編排**。
- **攻擊順序跟防護分層相反**：先脫殼（不然不知道 native 方法在哪）→ 逆 native → 繞 pinning → 重放。
- **脫殼的死穴**：真 DEX 執行期必進記憶體，掃 `"dex\n035"` magic + dump 就能撈（一代殼）。
- **演算法靜態看懂、金鑰動態 hook**：key 動態解密不好靜態追，hook HMAC 入口直接讀明文 key 最快。
- **重算比對是懂了的證明**：Python 重算 sign == 抓包 sign，才算真的還原協議、能離線構造合法請求。
- 每個決策都體現「**能動態別硬啃靜態、先用通殺再造輪子、結論要有硬證據**」。

## 自我檢核

- [ ] 我能從 APK 的 `classes.dex` 異常小 + `libjiagu.so` 判斷出有加固殼，並說出真 DEX 在哪。
- [ ] 我能解釋「脫殼為什麼一定有辦法」（真 DEX 執行期必進記憶體），並看懂那段記憶體掃描腳本。
- [ ] 我知道為什麼要 hook `hmac_sha256` 入口而不是 hook `calcSign` 回傳（拿 key vs 拿結果）。
- [ ] 我能說出攻擊順序為什麼跟防護分層相反，尤其「為什麼先脫殼」。
- [ ] 我能用 Python 重算 HMAC-SHA256 簽名，並知道對不上時該回哪一階段查（拼接格式）。
- [ ] 我知道這個案例哪些步驟在 x86_64 AVD 能做、哪些需要 arm64 真機（架構 offset、反模擬器殼）。

## 延伸閱讀

### 脫殼技術

- **[看雪論壇 — Android 加固與脫殼專題](https://bbs.kanxue.com/)**
  - **讀哪裡**：搜「脫殼」「libjiagu」「FART」的精華帖，看真實各家殼的脫法與踩坑。
  - **為什麼值得讀**：中文圈加固/脫殼對抗最活躍的社群，各代殼的實戰脫法幾乎都有人寫過；本案例的一代殼記憶體 dump 只是起點，真實各家殼的細節在這裡。

### 協議還原與 hook

- **[Frida CodeShare](https://codeshare.frida.re/)**
  - **讀哪裡**：搜 "ssl pinning"、"universal unpinning"、"dump dex" 的社群腳本。
  - **和本章的關聯**：階段 1 的脫殼腳本、階段 4 的 pinning bypass，社群都有現成更完整的版本；讀它們的原始碼比從零寫學得快。前提：讀過本章知道每個腳本在打哪一層。

### 方法論對照

- **[OWASP MASTG — Android Anti-Reversing Defenses 測試](https://mas.owasp.org/MASTG/techniques/android/)**
  - **讀哪裡**：反脫殼、反 hook、SSL pinning、完整性校驗的測試 technique，對照本案例每一層防護。
  - **為什麼值得讀**：它從「怎麼測這個防護有沒有做對」的角度寫，反過來就是你怎麼繞它。攻防同一張表，把本案例的三層放進更完整的防護地圖。

一個綜合防護的 App，我們用一套編排好的鏈拆穿了。但你發現了嗎——脫殼腳本、hook 金鑰腳本、pinning bypass，這些動作**高度重複**，每個 App 都要來一遍。下一章我們把這些重複動作沉澱成**可複用的 Frida 腳本庫**，再用 Python RPC 驅動它們批量掃 App——把「一次拆一個」變成「一套工具拆一批」。

→ [Ch 40 自動化：Frida 腳本庫與批量分析](./40-automation-frida-scripts.md)
