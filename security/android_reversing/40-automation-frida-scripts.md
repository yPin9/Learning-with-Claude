# Ch 40 — 自動化：Frida 腳本庫與批量分析

> **目標**：把前面每一章手打的一次性 hook，沉澱成一套**可複用的 Frida 腳本庫**（常用 hook 模板），再學會用 **Frida RPC + Python 驅動**把腳本變成可程式化呼叫的函式，最後把它們串成**批量掃 App** 的流水線、接進 CI，並用 objection 補齊互動式自動化。學完你手上有一套「拆一批 App」的工具，而不是「每個 App 從零打一遍」。

> **環境**：本章以 **Frida 16.x（Python binding `frida` + `frida-tools`）+ AVD（Android 13 / API 33）+ objection** 為準。凡是 Frida 需要 attach 真實進程的段落標「**未實測，理論預期行為**」並給驗證步驟；純 Python 邏輯（RPC message 的資料處理、批量腳本的檔案流程）能在本機驗的標「**實際輸出**」。

## 為什麼需要這個？

走到這裡你已經 hook 過幾十次：印過 Java 方法參數、dump 過記憶體、繞過 pinning、hook 過 native HMAC 拿 key。你有沒有發現——**這些 hook 有大量重複**？「印某個方法的參數與回值」「列出所有已載入的類」「監控加解密 API」，每個 App 你都在重打一遍幾乎一樣的腳本。

這是典型的「手工作坊」問題。手工作坊的產出上限被你的打字速度綁死。要把逆向從「一次拆一個」升級成「一套工具拆一批」，你需要三樣東西：

1. **腳本庫**——把重複的 hook 寫成參數化的模板，下次換個類名就能用。
2. **RPC 驅動**——讓 Python 能像呼叫函式一樣呼叫 Frida 裡的邏輯，把「人盯著 log」變成「程式收集結果」。
3. **批量流水線**——一次對幾十上百個 App 跑同一套分析，自動出報告。

安全評估、malware 分類、SDK 稽核這類工作，量都很大。會自動化，你的產能是只會手動的人的一個數量級以上。這章就是把你從工匠變成工廠。

## 先建立直覺：從「互動 REPL」到「程式化管線」

Frida 有三種用法，複雜度與自動化程度遞增：

```
  用法                    你怎麼跟它互動              自動化程度
 ────────────────────────────────────────────────────────────
  frida CLI (-l script.js)  人看 console.log 的 log      低（人盯）
  objection                 人敲互動指令                 中（半自動）
  Frida Python + RPC        程式 send/recv 結構化資料    高（全自動）
 ────────────────────────────────────────────────────────────

  批量分析的架構：
  ┌──────────────────────────────────────────────────┐
  │  Python 驅動器 (driver.py)                        │
  │   for apk in apk_list:                            │
  │     spawn app → inject agent.js → rpc.exports.xxx │
  │                    │                              │
  │                    ▼  收 send() 的結構化結果        │
  │     收集 JSON → 存檔 → 出報告                       │
  └──────────────────────────────────────────────────┘
                       │ 每個 App 一份
                       ▼
              results/<pkg>.json → 彙總報告
```

關鍵轉變是**從 `console.log` 到 `send()`**：`console.log` 是給人看的文字，程式沒法可靠解析；`send()` 送的是**結構化的 JSON 物件**，Python 端 `on_message` 收到能直接當資料處理。這一個轉變就是「手動 vs 自動」的分水嶺。

## 第一部分：可複用的 Frida 腳本庫

腳本庫的設計原則：**參數化**（用變數而非硬編碼類名）、**模組化**（一個檔一種能力）、**結構化輸出**（`send()` 不是 `console.log`）。以下是幾個我最常用的模板。

### 模板 1：通用 Java 方法追蹤器（印參數 + 回值 + 呼叫堆疊）

```javascript
// trace_java.js —— 追蹤任意 Java 方法的所有 overload，印參數與回值
// 用法：改 TARGET_CLASS / TARGET_METHOD 就能套任何方法
function traceMethod(className, methodName) {
    var clazz = Java.use(className);
    var overloads = clazz[methodName].overloads;
    overloads.forEach(function (ovl) {
        ovl.implementation = function () {
            var args = [];
            for (var i = 0; i < arguments.length; i++) args.push(String(arguments[i]));
            var ret = ovl.apply(this, arguments);   // 呼叫原實作
            // 用 send 送結構化資料（不是 console.log），Python 端好收
            send({ type: "java_call", cls: className, method: methodName,
                   args: args, ret: String(ret) });
            return ret;
        };
    });
}
Java.perform(function () {
    traceMethod("com.example.hardened.net.NativeSign", "calcSign");
    // 想追多個就多呼叫幾次 traceMethod
});
```

這一個模板就取代了你以前每個方法手寫一遍的 hook。換 App 只改 `traceMethod` 的參數。

### 模板 2：加解密 API 監控（黃金錨點自動化）

Ch 38 說 `Cipher`/`Mac`/`MessageDigest` 是不會被混淆的黃金錨點。把「監控這些 API」寫成一次到位的模板——任何 App 只要做加密就會被它抓到：

```javascript
// crypto_monitor.js —— 監控所有 javax.crypto / java.security 的加解密
Java.perform(function () {
    // Cipher.doFinal：AES/RSA 的實際加解密入口
    var Cipher = Java.use("javax.crypto.Cipher");
    Cipher.doFinal.overload("[B").implementation = function (input) {
        var out = this.doFinal(input);
        send({ type: "crypto", api: "Cipher.doFinal",
               algo: this.getAlgorithm(),
               in_hex: bytesToHex(input), out_hex: bytesToHex(out) });
        return out;
    };
    // Mac（HMAC）：hook init 拿 key，hook doFinal 拿結果
    var SecretKeySpec = Java.use("javax.crypto.spec.SecretKeySpec");
    SecretKeySpec.$init.overload("[B", "java.lang.String").implementation = function (k, algo) {
        send({ type: "crypto_key", algo: String(algo), key_hex: bytesToHex(k) });  // ← 抓 key！
        return this.$init(k, algo);
    };
});
function bytesToHex(b) {
    if (b === null) return "";
    var s = ""; for (var i = 0; i < b.length; i++)
        s += ("0" + (b[i] & 0xff).toString(16)).slice(-2);
    return s;
}
```

`SecretKeySpec.$init` 這個 hook 很值錢：**任何用 Java 層做 HMAC/AES 的 App，key 一定經過 `SecretKeySpec` 建構子**，你在這裡直接抓到明文 key（Ch 39 的 native 案例是 key 在 native，這個模板抓的是 key 在 Java 的情況——兩種都要會）。

### 模板 3：native 模組載入監控 + 自動 hook

```javascript
// native_watch.js —— 監控 .so 載入，載入後自動 hook 它的匯出函式
var dlopen = Module.findExportByName(null, "android_dlopen_ext");
Interceptor.attach(dlopen, {
    onEnter: function (args) { this.path = args[0].readCString(); },
    onLeave: function () {
        if (this.path && this.path.indexOf("libnotes.so") >= 0) {
            send({ type: "so_loaded", path: this.path });
            // .so 剛載入，現在 hook 它的匯出才找得到
            var exp = Module.enumerateExportsSync("libnotes.so");
            send({ type: "so_exports", count: exp.length,
                   names: exp.slice(0, 20).map(function (e) { return e.name; }) });
        }
    }
});
```

hook `android_dlopen_ext` 解決一個常見時序問題：**`.so` 還沒載入時你 hook 不到它的函式**（Ch 25）。這個模板等 `.so` 一載入立刻動手，把時序坑封裝成模板。

## 第二部分：Frida RPC —— 讓 Python 呼叫 Frida

`send()` 是 Frida 主動往 Python 推資料。**RPC 是反過來——Python 主動呼叫 Frida 裡的函式並拿回傳值**。這是把腳本變成「可程式化元件」的關鍵機制。

Frida 端用 `rpc.exports` 把函式暴露出去：

```javascript
// agent.js —— 把能力包成 RPC 可呼叫的函式
rpc.exports = {
    // Python 呼叫 rpc.exports.list_classes() 會拿到回傳的陣列
    listClasses: function () {
        var classes = [];
        Java.perform(function () {
            Java.enumerateLoadedClassesSync().forEach(function (c) { classes.push(c); });
        });
        return classes;
    },
    // 呼叫 rpc.exports.get_signature("note_42", 1717430400000)
    getSignature: function (data, ts) {
        var result = null;
        Java.perform(function () {
            var NS = Java.use("com.example.hardened.net.NativeSign");
            result = NS.calcSign(data, ts);   // 主動調用 App 自己的方法！
        });
        return result;
    }
};
```

Python 端驅動：

```python
# driver.py —— 用 RPC 呼叫 Frida 裡的函式
import frida, sys

def on_message(message, data):
    if message["type"] == "send":
        print("[recv]", message["payload"])
    elif message["type"] == "error":
        print("[error]", message["stack"])

device = frida.get_usb_device()
pid = device.spawn(["com.example.hardened"])
session = device.attach(pid)
script = session.create_script(open("agent.js").read())
script.on("message", on_message)
script.load()
device.resume(pid)

# 像呼叫本地函式一樣呼叫 Frida 裡的邏輯
classes = script.exports_sync.list_classes()      # 注意：JS 的 camelCase 映射成 snake_case
print(f"[*] loaded {len(classes)} classes")

# 主動調用 App 自己的簽名函式，不用自己重寫 HMAC！
sign = script.exports_sync.get_signature("note_42", 1717430400000)
print(f"[*] sign = {sign}")
```

> **未實測，理論預期行為**：上面需要 AVD + 目標 App。**RPC 名稱映射**是新手常踩的坑：JS 的 `listClasses` 在 Python 端變 `list_classes`（Frida 自動做 camelCase↔snake_case 轉換）；Frida 12+ 用 `script.exports_sync.xxx`（同步）或 `script.exports.xxx`（舊版/async）。**你自己驗證**：先跑 `list_classes()`，能拿到幾千個類名的陣列就代表 RPC 通了。

**RPC 的殺手級用法是「主動調用」**：`get_signature` 直接呼叫 App 自己的 `calcSign`——你不用逆演算法、不用重寫 HMAC、不用找 key，**讓 App 自己幫你算**。Ch 39 我們辛苦逆出演算法+key 才能重放；有 RPC 主動調用，你可以直接把 App 當成一個「簽名神諭（oracle）」，Python 餵輸入、它吐簽名。這在「演算法太複雜懶得逆」時是抄捷徑的利器。

## 第三部分：批量掃 App

有了模板（第一部分）和 RPC 驅動（第二部分），批量就是**套個迴圈 + 收集結構化結果**。

```python
# batch_scan.py —— 對一批 APK 跑同一套分析，出 JSON 報告
import frida, json, os, time

RESULTS = {}

def make_handler(pkg):
    def on_message(message, data):
        if message["type"] == "send":
            payload = message["payload"]
            RESULTS.setdefault(pkg, []).append(payload)   # 按 App 收集
    return on_message

def scan_one(device, pkg, agent_src):
    try:
        pid = device.spawn([pkg])
        session = device.attach(pid)
        script = session.create_script(agent_src)
        script.on("message", make_handler(pkg))
        script.load()
        device.resume(pid)
        time.sleep(8)          # 給 App 起來、觸發 hook 的時間
        session.detach()
    except Exception as e:
        RESULTS[pkg] = [{"type": "error", "msg": str(e)}]

def main(pkg_list):
    device = frida.get_usb_device()
    agent_src = open("crypto_monitor.js").read()   # 批量跑加密監控
    for pkg in pkg_list:
        print(f"[*] scanning {pkg}")
        scan_one(device, pkg, agent_src)
    # 出報告
    with open("scan_report.json", "w") as f:
        json.dump(RESULTS, f, indent=2)
    # 彙總：哪些 App 用了什麼加密、有沒有抓到 key
    for pkg, events in RESULTS.items():
        keys = [e for e in events if e.get("type") == "crypto_key"]
        print(f"  {pkg}: {len(events)} events, {len(keys)} keys leaked")

if __name__ == "__main__":
    main(["com.example.hardened", "com.example.foo", "com.example.bar"])
```

這支腳本對三個 App 跑同一套「加密監控」模板，自動彙總「每個 App 用了什麼加密、有沒有洩漏 key」。**未實測（需 AVD + 那些 App）**；驗證步驟：先對一個你確定會做加密的 App 單獨跑，確認 `scan_report.json` 裡有 `crypto` 事件，再擴到多個。

**批量的結果彙總邏輯可以在本機純 Python 驗**（模擬 `send` 收到的資料，驗證彙總對不對）：

```python
# 模擬批量掃描收集到的事件，驗證彙總邏輯（純 Python，本機可跑）
RESULTS = {
    "com.example.a": [{"type": "crypto", "api": "Cipher.doFinal", "algo": "AES"},
                      {"type": "crypto_key", "algo": "HmacSHA256", "key_hex": "6162"}],
    "com.example.b": [{"type": "error", "msg": "anti-frida detected"}],
}
for pkg, events in RESULTS.items():
    keys = [e for e in events if e.get("type") == "crypto_key"]
    errs = [e for e in events if e.get("type") == "error"]
    status = "ERROR:" + errs[0]["msg"] if errs else f"{len(keys)} key(s) leaked"
    print(f"{pkg}: {status}")
```

**實際輸出**（本機 Python 3.12 跑）：

```
com.example.a: 1 key(s) leaked
com.example.b: ERROR:anti-frida detected
```

彙總邏輯正確：一個 App 抓到 key、一個 App 被反 Frida 擋下（error 也被記錄，不會讓整批崩掉）。**批量最重要的工程細節就是這個「單個 App 失敗不能拖垮整批」**——`scan_one` 的 try/except 把每個 App 隔離開。

## 第四部分：接進 CI 與 objection 自動化

### 接 CI：把逆向變成回歸測試

自動化分析可以當**回歸檢查**：每次你的 App 出新版，CI 自動跑一遍「有沒有洩漏 key、pinning 有沒有生效、debuggable 有沒有關」。概念上的 pipeline（以 GitHub Actions 為例的骨架）：

```yaml
# .github/workflows/re-check.yml（概念骨架，未實測）
# CI 裡跑 headless emulator + frida-server + 你的 batch_scan.py
jobs:
  security-scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Start emulator            # reactivecircus/android-emulator-runner 這類 action
        uses: reactivecircus/android-emulator-runner@v2
        with: { api-level: 33, arch: x86_64, target: google_apis }
      - name: Push frida-server & run scan
        run: |
          adb push frida-server /data/local/tmp/ && adb shell "/data/local/tmp/frida-server &"
          python batch_scan.py --apk app-release.apk --fail-on key_leak
```

> **未實測，理論預期行為**：CI 裡跑 Android emulator + Frida 是可行的（`android-emulator-runner` 這類 action 提供 headless AVD），但**細節多、易碎**（emulator 冷啟動慢、frida-server 版本要對、CI runner 要支援 KVM 加速）。**你自己驗證**：先在本機把 `batch_scan.py` 跑穩，再搬 CI；`--fail-on key_leak` 的語意是「掃到 key 洩漏就讓 CI 失敗」，把安全檢查變成擋 PR 的 gate。

### objection：互動式自動化的補位

不是所有事都值得寫 Python 驅動。objection 架在 Frida 上，內建一堆常用 hook，適合**快速探索**與**批次指令腳本**：

```bash
# 用 -s 帶一串指令，非互動式跑完就退出（適合塞進 shell 腳本批量跑）
objection -g com.example.hardened explore \
  -s "android sslpinning disable; android hooking list activities; exit"
```

objection 的定位：**RPC/Python 是「你要寫程式化的自訂分析」時用；objection 是「常見動作有現成的、不想自己寫」時用**。兩者互補——探索期用 objection 快速摸，確定要固化的分析用 RPC 寫成腳本。

## 對比與取捨：三種自動化路線怎麼選

| 你要做 | 用什麼 | 為什麼 |
|---|---|---|
| 快速探索一個 App | objection 互動 | 內建動作多，不用寫腳本 |
| 固化一套自訂分析 | Frida RPC + Python | 可程式化、可測試、可版本控制 |
| 對一批 App 跑同套分析 | Python 批量驅動器 | 迴圈 + 結構化收集 + 隔離失敗 |
| 每次發版自動檢查 | CI + 批量腳本 | 把安全檢查變成回歸 gate |
| 拿 App 當簽名 oracle | RPC 主動調用 | 讓 App 自己算，省掉逆演算法 |
| 給人看的一次性 log | `console.log` + frida CLI | 最快，但沒法程式化收集 |

核心取捨：**`console.log` vs `send()`** 決定了「人盯 vs 程式收」；**手寫 hook vs 模板庫** 決定了「一次性 vs 可複用」；**RPC 主動調用 vs 逆向重寫** 決定了「借 App 的力 vs 自己造輪子」。自動化的每一步都是往「可複用、可程式化、可規模化」挪。

## 踩雷集錦

1. **用 `console.log` 做批量分析**：文字 log 沒法可靠解析，App 一多就淹沒在洪流裡。批量一定要用 `send()` 送結構化 JSON，Python 端 `on_message` 收。
2. **RPC 名稱映射搞錯**：JS 的 `getSignature` 在 Python 是 `get_signature`（camelCase→snake_case 自動轉）。照抄 JS 原名去呼叫會 `AttributeError`。
3. **批量沒隔離單個失敗**：一個 App 反 Frida 閃退，沒 try/except 就讓整批掛掉，前面幾十個結果全丟。每個 `scan_one` 都要包 try/except，失敗記成 error 事件繼續跑。
4. **spawn 後沒 `resume` / 沒等夠時間**：`spawn` 出來的進程是暫停的，忘了 `device.resume(pid)` App 根本不動；或 `sleep` 太短，hook 還沒觸發就 detach，什麼都沒收到。給足 App 起來 + 觸發的時間。
5. **腳本庫硬編碼類名**：模板寫死 `com.example.hardened`，換 App 就要改一堆地方。模板要**參數化**（類名/方法名當參數傳），這才叫「庫」。
6. **CI 裡 frida-server 版本跟 client 不一致**：本機對了 CI 沒對，`major versions match` 錯誤（Ch 0 的老坑在 CI 又咬一次）。CI 腳本裡把 client 與 server 版本鎖死成同一個。
7. **主動調用（RPC）碰到反 Frida / 反調試就失效**：oracle 大法很爽，但目標若有強反 Frida，attach 就死，RPC 根本建不起來。這時退回 Ch 39 的「逆演算法 + 離線重算」路線——自動化不是萬能，強防護目標還是得硬逆。

## 進階：再往深一層

- **frida-compile 打包多檔腳本**：腳本庫大了要拆多檔、用 `import`。純 Frida 的 JS 不支援模組化，用 `frida-compile`（基於 esbuild）把多個 `.js`/`.ts` 打包成一個 agent。想寫 TypeScript 有型別檢查的 Frida 腳本也靠它。
- **stalker 做覆蓋率導向的批量**：批量不只 hook 固定 API，還能用 Stalker（Ch 15）trace 每個 App 觸發某功能時走過的程式碼區塊，做 App 間的行為比對、或找「哪個 `.so` 函式在登入時被呼叫」。
- **跟 MobSF 這類框架整合**：MobSF 是現成的自動化靜態+動態分析框架，它的動態部分就是 Frida。你可以把自己的腳本庫塞進 MobSF 的動態分析階段，借它的報告系統與 UI，不用自己造整套。
- **反自動化的對抗**：加固廠商也在偵測「批量自動化」的特徵（同一時間大量 spawn、frida-server 常駐、模擬器指紋）。真實批量跑商業 App 時，這些反自動化會讓你的成功率下降——這是 Ch 41 防禦視角要談的軍備競賽在自動化層的體現。

## 動手練習

1. 把 Ch 13-14 你手打過的某個 hook 改寫成**參數化模板**（類名/方法名當參數），對兩個不同 App 各套一次，體會「一次寫、到處用」。
2. 寫一支 `agent.js` 用 `rpc.exports` 暴露一個 `list_classes`，用 Python 驅動呼叫它，印出載入的類數量。跑通 RPC 這條線——這是所有自動化的地基。
3. **主動調用練習**：找一個你自建的、有 `native`/Java 簽名方法的 App，用 RPC 主動調用它，把 App 當簽名 oracle：Python 餵不同輸入、收不同簽名。對照 Ch 39「逆演算法離線重算」——體會兩條路各自的適用場景。
4. 把本章的「批量彙總邏輯」那段純 Python 片段跑一遍，改改 `RESULTS` 的內容（多加幾個 App、幾種事件），確認彙總與失敗隔離都正確。這段不需要 AVD，先在本機把**資料流**練熟，再接真的 Frida。

## 本章重點整理

- 自動化的分水嶺是 **`console.log`（人盯）→ `send()`（程式收結構化 JSON）**。
- **腳本庫**要參數化 + 模組化 + 結構化輸出；`Cipher`/`Mac`/`SecretKeySpec` 是可一次到位的黃金錨點模板。
- **Frida RPC** 讓 Python 呼叫 Frida 裡的函式；**主動調用**能把 App 當簽名 oracle，省掉逆演算法。
- **批量掃**就是「迴圈 + RPC/hook + 結構化收集」，工程重點是**單個 App 失敗要隔離**（try/except），不能拖垮整批。
- **接 CI** 把逆向變回歸 gate；**objection** 補位快速探索與現成動作。三條路線按「探索/固化/規模化」選。

## 自我檢核

- [ ] 我能解釋為什麼批量分析要用 `send()` 而不是 `console.log`，並知道 Python 端怎麼收。
- [ ] 我能寫一個參數化的 Java 方法追蹤模板，換 App 只改參數不改邏輯。
- [ ] 我知道 `rpc.exports` 的函式名在 Python 端會變成什麼（camelCase→snake_case）。
- [ ] 我能說明「RPC 主動調用當 oracle」跟「逆演算法離線重算」各自什麼時候用。
- [ ] 我能寫一個批量驅動器，且知道為什麼每個 App 要 try/except 隔離。
- [ ] 我知道把 Frida 分析接進 CI 的難點（emulator 冷啟動、frida-server 版本、KVM）。

## 延伸閱讀

### Frida 官方

- **[Frida — Messages & RPC](https://frida.re/docs/messages/) 與 [JavaScript API](https://frida.re/docs/javascript-api/)**
  - **讀哪裡**：`send`/`recv` 的訊息機制、`rpc.exports` 那節；JavaScript API 裡的 `Interceptor`/`Java`/`Module`。
  - **和本章的關聯**：本章所有模板與 RPC 的一手依據；`send` 的 payload 格式、`rpc.exports` 的名稱映射規則都在這，是唯一權威。

### 自動化框架

- **[MobSF（Mobile Security Framework）](https://github.com/MobSF/Mobile-Security-Framework-MobSF)**
  - **讀哪裡**：Dynamic Analysis 那部分怎麼用 Frida、它內建的 Frida 腳本（`frida_scripts/`）。
  - **為什麼值得讀**：現成的自動化靜+動分析框架，它的動態核心就是本章教的東西；讀它怎麼組織腳本庫與報告，是把你的手工腳本升級成產品級的範本。前提：讀過本章知道 Frida RPC/批量原理。

### 腳本庫參考

- **[Frida CodeShare](https://codeshare.frida.re/) 與 [objection 原始碼](https://github.com/sensepost/objection)**
  - **讀哪裡**：CodeShare 搜通用 hook 模板；objection 的 `agent/` 目錄看它怎麼把幾十種 hook 包成 RPC 可調用的元件。
  - **為什麼值得讀**：objection 本質就是「一個組織良好的 Frida 腳本庫 + RPC 驅動」，它的原始碼是本章所有概念的完整實作範例；想知道「一個成熟的腳本庫長怎樣」，讀它。前提：本章 RPC 那節。

腳本庫、RPC、批量、CI——你現在能把逆向規模化了。但你有沒有想過，你自動化掃出來的每一個弱點（沒關 debuggable、key 洩漏、pinning 沒生效），對開發者來說都是「該補的洞」？攻與防是同一枚硬幣。下一章我們換到防禦者的椅子上坐一次：如果你是開發者，你會怎麼加固？每種防護能擋你多久？懂了防守，你的攻擊會更準——因為你知道對手在想什麼。

→ [Ch 41 防禦視角：懂防守才更會攻](./41-defense-perspective.md)
