# Final Project — 綜合防護目標 App 完整拆解

> **目標**：把這門課從 Part 1 到 Part 7 學的東西**在同一個目標上串起來**。獨立拿下一個綜合防護的 App——**加固殼 + native 化簽名 + SSL pinning + 反調試**——完整走過 **recon → 脫殼 → native 逆向 → 動態 hook → 協議還原 → 寫報告 + PoC**。完成後你能證明的不是「每章分開會一招」，而是**能獨立把一個真實防護目標從黑盒拆到能離線構造合法請求，並產出一份可放進作品集的逆向報告**。這個 Final 要求整合本課 **≥70% 的核心概念**。

> **環境與合法邊界**：目標 App **代號 `HardenedNotes-Final`（`com.example.hnfinal`）是一個為這個 Final 自建的假想目標**——你要**自己把它做出來**（規格見「任務規格」），這既是練習也保證你只逆自己有權分析的對象。**這門課從頭到尾的紅線：只逆自己寫的、開源的、CTF 的、或明確授權的目標。** 這個 Final 用自建目標，正是為了讓你合法地把整條鏈練完。動態步驟（脫殼、hook）需要 **AVD（Android 13 / API 33）或 arm64 真機 + Frida 16.x**，凡本 repo 沙箱跑不了的標「**未實測，理論預期行為**」並給你自己環境的驗收方法；**協議重放的簽名重算邏輯用 Python 在本機實跑驗證，標「實際輸出」**。反模擬器/反調試若在 x86_64 AVD 上失效，誠實承認需 arm64 真機。

## 背景與動機

Ch 39 我帶你走過一次綜合防護案例。這個 Final 的差別是：**這次沒有人牽你的手**。你要自己建目標、自己判斷、自己選工具、自己踩坑、自己產出報告。這模擬的是真實的 App 安全評估——沒有攻略，只有一個黑盒和你腦中的方法論（Ch 38）。

為什麼要自建目標？三個理由，跟 Ch 39 一脈相承：

1. **合法**。真實商業 App 你多半無權逆向；自建目標讓你把整條攻擊鏈練到底而不越線。
2. **可控難度**。你能精準地把「加殼 + native + pinning + 反調試」四層防護組進去，每一層對應本課哪些章節清清楚楚，練得到位。
3. **可驗證**。因為是你建的，你知道「正確答案」（真實的 key、演算法），能驗證你逆出來的對不對——這是自建靶最大的教學價值。

自建靶打通後，真實 App 的差別只在「防護是別人設計的、你不知道答案」——但你的**流程與工具完全一樣**。你在這個 Final 練的是流程，不是背答案。

## 整合了本課哪些概念

這個 Final 動到的章節（覆蓋本課 ≥70% 的核心概念）：

| 階段 | 用到的技巧 | 對應章節 |
|---|---|---|
| 前置 | APK 結構、zip/DEX header、簽名 scheme | Ch 2 |
| 前置 | 執行與安全模型（為什麼 Frida 要 root） | Ch 3 |
| Recon | 完整方法論 SOP、決策樹、框架/加固判斷 | Ch 1, Ch 38 |
| Recon | Manifest 逆向、`.so` 指紋、assets 掃描 | Ch 2, Ch 9 |
| 脫殼 | 加固分代、脫殼技術、記憶體 dump | Ch 28, Ch 29 |
| 脫殼 | ART/ClassLoader、主動調用（二代殼） | Ch 34, Ch 35, Ch 36 |
| 讀真 DEX | jadx/smali、讀反編譯輸出、JNI 宣告 | Ch 4-8, Ch 19 |
| native 逆向 | ELF/.so、ARM64、IDA/Ghidra、演算法識別 | Ch 20-23 |
| 動態 hook | Frida 架構、hook Java、hook native、拿 key | Ch 12-15, Ch 25 |
| 反調試對抗 | 反調試/反 Frida 偵測與繞過、root 檢測 | Ch 30, Ch 31 |
| 抓包 | SSL pinning bypass、協議還原 | Ch 17, Ch 18 |
| 重放 | 簽名重算、PoC 腳本 | Ch 18 |
| 自動化 | Frida 腳本庫、RPC 主動調用（可選） | Ch 40 |
| 收尾 | 防禦視角反思報告 | Ch 41 |

一眼可見：這個 Final 橫跨 Part 1（Ch 2/3）、Part 2（Ch 4-8）、Part 3（Ch 12-18）、Part 4（Ch 19-25）、Part 5（Ch 28-31）、Part 6（Ch 34-36）、Part 7（Ch 38/40/41）——**七個 Part 全部動到**。這就是「整合」的意思。

## 任務規格

### 你要交付的東西

1. **目標 App 的原始碼與加固後的 APK**（`HardenedNotes-Final`）——你自己建，含四層防護（規格見下）。
2. **一份完整逆向報告**（用下方「逆向報告模板」）——記錄 recon 到重放的每一步、每個結論的證據。
3. **一套 PoC**：
   - 脫殼腳本（Frida）+ 脫出的真 DEX。
   - hook 出金鑰的 Frida 腳本 + hook log。
   - **離線重放腳本（Python）**：不透過 App，自己構造帶合法簽名的請求。
4. **一段防禦反思**（對照 Ch 41）：作為開發者，你的哪層防護擋了多久、你會怎麼改進。

### 目標 App 規格（你要建成這樣）

**功能**：一個「雲端筆記」App。登入後，「同步」按鈕會對伺服器發一個帶簽名的請求：

```
POST https://<你的測試 server>/v1/sync
Body: {"noteId": "<字串>", "ts": <毫秒時間戳>, "sign": "<HMAC hex>"}
```

**簽名演算法**（你實作在 native）：`sign = hex( HMAC-SHA256(key, "noteId|ts") )`，`key` 是一把 16-byte 的固定密鑰，藏在 `libhnf.so` 裡（`.so` 載入時 xor 一段常數解出，不明文放）。

**四層防護**（對應四個 Part 的對抗技術）：

| 防護層 | 你要做的 | 對應課程能力 |
|---|---|---|
| **加固殼** | 用開源加固方案（或自寫一個一代/二代殼）把真 DEX 加密藏起來，靜態 DEX 只有載入器 | Ch 28/29 脫殼 |
| **native 簽名** | 簽名演算法 + key 放 `libhnf.so`，Java 層只留 `native` 宣告 | Ch 19-25 native 逆向 |
| **SSL pinning** | OkHttp `CertificatePinner` 或自寫 `TrustManager` 釘住你的測試 server 憑證 | Ch 17 pinning bypass |
| **反調試 / 反 Frida** | 查 `TracerPid`、掃 maps 找 `frida`、查 27042 埠，命中就退出 | Ch 30 反調試繞過 |

> **建靶的難度分級**：如果你時間有限或還沒把脫殼練熟，**允許分階段**——先建「無殼版」（只有 native + pinning + 反調試），把 Part 3/4 的鏈打通；再加殼建「完整版」練 Part 5。**先跑通再加難度**，別一開始就卡在建殼上。無殼版已能整合本課 ~60% 概念，完整版才到 ≥70%。

### PoC 的驗收標準（硬指標）

- **脫殼 PoC**：脫出的 DEX 能用 jadx 打開，看到業務類（不再是殼載入器），且能看到 `native String calcSign` 的宣告。
- **hook PoC**：Frida log 印出執行期真實的 16-byte key，且**這個 key 等於你建靶時寫進去的那把**（自建靶的驗證優勢：你知道正確答案）。
- **重放 PoC**：Python 腳本用 hook 出的 key 重算 sign，**重算值 == App 實際發出的 sign（抓包看到的）**，且能對任意 `noteId` 構造合法請求。

## 階段里程碑與每階段驗收

把整個 Final 切成六個里程碑。**每個里程碑有明確驗收，過了才進下一個**——這是 Ch 38 方法論「分階段、有驗收」的實踐。

### 里程碑 0：建靶（Setup）

建出目標 App（至少無殼版）。

**驗收**：App 能在 AVD 上跑、按「同步」能對你的測試 server 發出請求；`adb logcat` 看得到請求發出；反調試在 Frida attach 時會讓 App 退出（證明防護生效）。

### 里程碑 1：Recon（對應 Ch 1/38 Phase 0-1）

把目標當「陌生 APK」，走 Ch 38 的偵察與量防護：

- `unzip -l` 掃檔案：`classes.dex` 多大？有哪些 `.so`（找 `libhnf.so` 與殼的 `.so`）？`assets/` 有什麼？
- `apktool d` 讀 Manifest：入口、`android:name`（殼載入器？）、權限。
- 量防護清單：混淆？加固（哪代）？pinning？反調試？native 化？

**驗收**：產出一句話偵察結論 + 一張防護清單（每項標阻力與對應章節）。**就算你知道答案（自己建的），也要走一遍偵察流程**——練的是「拿到陌生 App 怎麼系統化偵察」，不是背答案。

### 里程碑 2：脫殼（對應 Ch 28/29/36）

如果建了殼版，把真 DEX 從記憶體撈回來。

- 一代殼：記憶體掃 `"dex\n035"` magic + dump（Ch 39 那套）。
- 二代殼：主動調用逐函式還原（練習 E 的 mini-FART 思路）。
- **注意反調試**：脫殼腳本 attach 時可能觸發反調試退出——你得先繞反調試（里程碑 4 的技術可能要提前用一部分）。

**驗收**：脫出的 DEX jadx 打得開，看到業務類 `com.example.hnfinal.*`，找到 `NativeSign.calcSign` 的 `native` 宣告與 `System.loadLibrary("hnf")`。

### 里程碑 3：native 逆向（對應 Ch 19-23）

逆 `libhnf.so`，看懂簽名演算法。

- 找 JNI 符號 `Java_com_example_hnfinal_..._calcSign`（Ch 19 命名規則）。
- IDA/Ghidra 反編譯，看出 `HMAC-SHA256(key, "noteId|ts")` 的骨架（Ch 23 演算法識別）。
- 定位 `key` 與 HMAC 計算函式的位置（拿 offset 給下一步 hook）。

**驗收**：能畫出 `calcSign` 的邏輯（輸入怎麼拼、對什麼做 HMAC、key 從哪來），並找到 HMAC 計算入口的 `.so` 內 offset。**注意架構**：AVD 上 hook 的是 x86_64 `.so`，IDA 若逆 arm64 版，offset 不通用（Ch 0/39 的環境陷阱）。

### 里程碑 4：動態 hook 拿 key + 繞反調試（對應 Ch 13-15/25/30）

先繞反調試讓 Frida 站穩，再 hook 出執行期真實 key。

- **繞反調試**（Ch 30）：hook 掉 `TracerPid` 讀取 / maps 掃描 / 埠檢查的函式，讓它們回「乾淨」。
- **hook 拿 key**（Ch 25）：hook `libhnf.so` 裡的 HMAC 計算入口（或系統 `HMAC_Init_ex`），讀第一個參數（key）與被簽的 data。

**驗收**：Frida log 印出 16-byte key + 被簽的 data 格式（`noteId|ts`）；**印出的 key == 你建靶時寫的那把**（自建靶驗證）。

### 里程碑 5：繞 pinning 抓包 + 協議還原（對應 Ch 17/18）

- 繞 pinning（Ch 17）：objection `android sslpinning disable` 或 Frida 通用腳本。
- 抓包（Ch 17）：mitmproxy 抓到明文請求，確認 body 的 `noteId`/`ts`/`sign` 三欄。
- 對照（Ch 18）：抓到的 `sign` 跟你 hook 出的 key + data 用 Python 重算的值比對。

**驗收**：mitmproxy 看到明文請求且 App 功能正常；抓到的 `sign` == Python 重算的 `sign`（協議還原成功的鐵證）。

### 里程碑 6：重放 PoC + 報告（對應 Ch 18/41）

- 寫離線重放腳本：Python 用 key + 演算法，對任意 `noteId` 構造合法請求。
- 寫完整逆向報告（用下方模板）。
- 寫防禦反思（Ch 41）。

**驗收**：重放腳本能對任意 `noteId` 產生合法 sign；報告完整、每個結論有證據；防禦反思說得出每層防護擋了多久、怎麼改進。

## 期望輸出範例

里程碑 4 的 hook log（**未實測，理論預期行為**——需 AVD/真機）：

```
[anti-debug] TracerPid check bypassed (forced 0)
[anti-frida] maps scan bypassed
[key]  0  61 62 63 64 31 32 33 34 6b 65 79 73 65 63 21 21  abcd1234keysec!!
[data] note_42|1717430400000
```

里程碑 5-6 的協議還原與重放（**簽名重算在本機實跑**）：

```
[capture] sign from mitmproxy = 1510c0d856bcac903d1ed8f11c5be772e043e142a288a69f1c70ecb5bbdb1e9f
[recompute] python HMAC       = 1510c0d856bcac903d1ed8f11c5be772e043e142a288a69f1c70ecb5bbdb1e9f
[match] YES  →  協議完全還原，可離線構造任意合法請求
```

那串 `1510c0d8...` 是 `HMAC-SHA256(b"abcd1234keysec!!", b"note_42|1717430400000")` 的**真實值**，我在本機 Python 3.12 跑出來的（見下方 PoC 腳本，你在自己機器跑會得到一模一樣的值）。

## 交付物一：逆向報告模板

報告是這個 Final 最重要的交付物——它把你的手藝變成別人能驗證、能複現的成果。用這個模板：

```markdown
# HardenedNotes-Final 逆向報告

## 1. 目標概述
- App / package：com.example.hnfinal
- 版本 / 取得方式：自建 v1.0
- 分析授權：自建目標，完全授權
- 分析日期 / 環境：<日期> / AVD API 33 x86_64（或 arm64 真機）

## 2. Recon 結論（Ch 38 Phase 0-1）
- 框架判斷：原生 Java + native
- 防護清單（每項標阻力與繞法）：
  | 防護 | 偵測到的證據 | 阻力 | 繞法（章節） |
  |---|---|---|---|
  | 加固殼 | classes.dex 僅 8KB + lib<殼>.so | 中 | 記憶體 dump（Ch29）|
  | native 簽名 | 只有 native calcSign 宣告 | 高 | 逆 .so + hook（Ch22/25）|
  | SSL pinning | mitmproxy 憑證被拒 | 中 | objection（Ch17）|
  | 反調試 | Frida attach 即退出 | 中 | hook 偵測函式（Ch30）|

## 3. 脫殼過程（Ch 29）
- 殼世代判斷與依據：
- 脫殼方法與腳本：
- 脫出 DEX 的驗證（jadx 截圖 / 業務類名）：

## 4. Native 逆向（Ch 19-23）
- JNI 符號：Java_..._calcSign
- 反編譯出的演算法（附化簡的偽碼）：
- key 與 HMAC 入口的定位（offset）：

## 5. 動態 hook（Ch 25/30）
- 繞反調試的方法：
- hook 出的 key（hex）+ 被簽 data 格式：
- hook log（貼原始輸出）：

## 6. 協議還原與重放（Ch 17/18）
- 繞 pinning 方法 + 抓包的請求：
- 重算 sign vs 抓包 sign 的比對結果：
- 重放腳本能力（能對任意輸入構造合法請求）：

## 7. 漏洞 / 風險評估
- 客戶端可繞過的驗證：
- key 是否可離線恢復（本評估的核心發現）：
- 若伺服器無 nonce/風控，重放攻擊可行性：

## 8. 防禦建議（Ch 41 視角）
- 每層防護的實際拖延時間：
- 建議改進（哪些驗證該搬伺服器、加什麼）：

## 附錄：所有 PoC 腳本
- unpack.js / hookkey.js / replay.py
```

## 交付物二：PoC 腳本骨架

### 脫殼腳本（Frida，未實測需 AVD）

沿用 Ch 39 的記憶體掃描思路（一代殼）。二代殼改用主動調用（練習 E）。**你自己驗證**：`adb pull` dump 出的 DEX，`file` 看 magic、jadx 打開看業務類。

```javascript
// unpack.js —— 掃記憶體找還原後的 DEX（一代殼）。二代殼見練習 E 主動調用
function dumpDex() {
    Process.enumerateRanges('r--').forEach(function (range) {
        try {
            Memory.scan(range.base, range.size, '64 65 78 0a 30 33 35', {  // "dex\n035"
                onMatch: function (addr) {
                    var size = addr.add(32).readU32();          // DEX header file_size @ off 32
                    if (size > 0x1000 && size < range.size) {
                        var f = new File("/data/local/tmp/hnf_" + addr + ".dex", "wb");
                        f.write(addr.readByteArray(size)); f.close();
                        console.log("[dump] " + addr + " size=" + size);
                    }
                }, onError: function () {}, onComplete: function () {}
            });
        } catch (e) {}
    });
}
setTimeout(dumpDex, 4000);   // 等殼還原真 DEX 後再掃
```

### 繞反調試 + hook key（Frida，未實測需 AVD）

```javascript
// hookkey.js —— 先中和反調試，再 hook HMAC 入口拿 key
Java.perform(function () {
    // --- 繞反調試：hook 掉讀 TracerPid 的邏輯（依你建靶的實作調整）---
    // 若反調試在 native，改用 Interceptor.attach 到那個檢查函式，讓它回 0/false
    // --- hook native HMAC 入口拿 key ---
    var base = Module.findBaseAddress("libhnf.so");
    var hmacOff = 0x0;   // ← 填你在 IDA 逆出的 hmac 入口 offset（里程碑 3）
    if (base && hmacOff) {
        Interceptor.attach(base.add(hmacOff), {
            onEnter: function (args) {
                // hmac_sha256(key, keylen, data, datalen, out)
                var keylen = args[1].toInt32();
                console.log("[key]  " + hexdump(args[0].readByteArray(keylen)));
                console.log("[data] " + args[2].readCString());
            }
        });
    }
});
```

### 離線重放腳本（Python，簽名重算實跑驗證）

```python
# replay.py —— 用 hook 出的 key + 逆出的演算法，離線構造合法請求
import hashlib, hmac, time

KEY = b"abcd1234keysec!!"          # 里程碑 4 hook 出來的 16-byte key

def sign_of(note_id: str, ts: int) -> str:
    data = f"{note_id}|{ts}"       # 里程碑 3 逆出的拼接格式：noteId|ts
    return hmac.new(KEY, data.encode(), hashlib.sha256).hexdigest()

def build_request(note_id: str, ts: int | None = None) -> dict:
    if ts is None:
        ts = int(time.time() * 1000)
    return {"noteId": note_id, "ts": ts, "sign": sign_of(note_id, ts)}

if __name__ == "__main__":
    # 用固定 ts 重現抓包看到的那筆，驗證重算 == 抓包
    print("verify:", build_request("note_42", 1717430400000))
    # 對任意 noteId 構造新的合法請求（重放攻擊的核心能力）
    print("forge :", build_request("note_99"))
```

**實際輸出**（本機 Python 3.12 跑）：

```
verify: {'noteId': 'note_42', 'ts': 1717430400000, 'sign': '1510c0d856bcac903d1ed8f11c5be772e043e142a288a69f1c70ecb5bbdb1e9f'}
forge : {'noteId': 'note_99', 'ts': <當下毫秒時間戳>, 'sign': '<對應該 ts 的合法 HMAC>'}
```

`verify` 那筆的 `sign` 是固定的（輸入固定），你在自己機器跑會得到一模一樣的 `1510c0d8...`——把它跟里程碑 5 抓包看到的 `sign` 比對，**相同就代表協議完全還原**。`forge` 那筆證明你能對任意 `noteId` 離線造出合法請求（`ts` 隨當下變，`sign` 跟著變）——這就是重放攻擊的核心能力。

## 評分標準

用這張表自評（或給同儕互評）。**滿分 100，70 分及格代表你達到了 Final 的整合要求**：

| 維度 | 配分 | 評分要點 |
|---|---|---|
| **Recon 完整性** | 15 | 有系統化偵察結論 + 防護清單，每項對應章節；不是憑感覺 |
| **脫殼** | 15 | 脫出可讀真 DEX；能說明殼世代與脫法選擇的理由 |
| **native 逆向** | 15 | 逆出演算法骨架 + 定位 key/HMAC；架構 offset 判斷正確 |
| **動態 hook** | 15 | 繞反調試站穩 + hook 出真實 key；key == 建靶時寫的 |
| **協議還原** | 15 | 繞 pinning 抓包 + 重算 sign == 抓包 sign（閉環）|
| **重放 PoC** | 10 | 能對任意輸入離線構造合法請求，腳本可跑 |
| **報告品質** | 10 | 每個結論有證據鏈；別人能照著複現 |
| **防禦反思** | 5 | 說得出每層拖延多久、怎麼改進（Ch 41 視角）|

**加分項（各 +5，總分可超過但封頂記 100）**：
- 用 Frida RPC 主動調用把 App 當簽名 oracle（Ch 40），對照離線重算，說明兩者取捨。
- 把脫殼/hook 沉澱成可複用的參數化腳本庫（Ch 40）。
- 建的是**二代殼**並用主動調用脫殼（練習 E 的 mini-FART）。

## 如果你卡住了

1. **建靶就卡住（不會加殼）**：先建**無殼版**（native + pinning + 反調試），把 Part 3/4 的鏈打通拿到 ~60 分的整合，再回頭加殼。別在建殼上耗掉全部時間。
2. **脫殼 dump 出半殘 DEX**：延後掃描時機（`setTimeout` 調大、先觸發過同步功能讓相關類都載入）；或改主動調用脫殼（練習 E）。
3. **Frida attach 就閃退**：這是反調試生效（好事，證明你建對了），但擋了你自己。先繞反調試（里程碑 4 提前做一部分）再脫殼/hook。用空腳本 attach 確認是不是反調試（Ch 38 的探針）。
4. **hook 到的 key 是亂碼 / offset 不對**：99% 是**架構搞錯**——你在 x86_64 AVD 上跑，但 hook offset 是從 arm64 IDA 逆出來的。逆哪個架構就 hook 哪個架構（Ch 0/39 環境陷阱）。或者 HMAC 走的是系統 `libcrypto`，改 hook 有符號的 `HMAC_Init_ex` 不用算 offset。
5. **重算 sign 對不上抓包**：拼接格式猜錯。回里程碑 4 看 hook 印出的 `data` 那行——是 `noteId|ts` 還是 `ts|noteId`？ts 毫秒還是秒？有沒有其他欄位摻進去？**hook 印出的真實 data 是格式的鐵證，別自己猜**。（本機驗過：對同一組 key，`note_42|ts` 和 `ts|note_42` 算出的 HMAC 完全不同，順序錯一位就全錯。）
6. **反模擬器讓 App 在 AVD 上跑不起來**：你若在建靶時加了強反模擬器，AVD 上會被自己的防護擋。降低反模擬器強度，或換 arm64 真機。**這是本課環境的先天限制，誠實承認**（Ch 0/38/39 都提過）。

## 延伸挑戰（做完基本盤再玩）

1. **key 動態化**：把固定 key 改成「每次請求由 server 下發 nonce + 本地算法生成」，逼自己把「key 怎麼生成」也逆出來（螺旋多轉一圈）。
2. **native 套 OLLVM**：用 OLLVM 混淆 `libhnf.so` 的 `calcSign`，讓 IDA 反編譯爆炸，逼你改**純動態**（hook 進出 + Stalker trace，Ch 15/27）當黑盒反推。
3. **加完整性校驗**：讓 App 自校驗被改就閃退（Ch 32），然後嘗試「改 App 行為」的任務（不只重放）——體會為什麼 Ch 39 走重放能繞開這層。
4. **白盒密碼**：把 HMAC key 換成白盒實作（記憶體裡不出現裸 key），讓你的「hook 拿 key」失效，逼你面對 Ch 41 說的 DCA 統計攻擊的門檻。
5. **自動化批量**：建三個防護略不同的靶，用 Ch 40 的批量驅動器一次掃完出報告，把「拆一個」升級成「拆一批」。
6. **接 CI**：把重放 PoC 當回歸測試——每次你「修好一層防護」就重跑，看哪層擋住了你的自動化。這是 Ch 40/41 攻防合流的實踐。

## 自我檢核

- [ ] 我能不看 Ch 39，獨立走完 recon → 脫殼 → native 逆向 → hook → 抓包 → 重放整條鏈。
- [ ] 我的重放腳本能對**任意** `noteId` 構造合法請求，且我能解釋每個欄位怎麼來（沒有魔數）。
- [ ] 我 hook 出的 key **等於**我建靶時寫進去的那把（自建靶的閉環驗證）。
- [ ] 我的重算 sign **等於**抓包看到的 sign，我能解釋這代表協議完全還原。
- [ ] 我知道每個步驟在 x86_64 AVD 能做、哪些需要 arm64 真機（架構 offset、反模擬器）。
- [ ] 我能講出這個 Final 整合了本課哪些章節，且能把每個里程碑對應回去（≥70% 概念）。
- [ ] 我寫出了一份別人能照著複現的報告，每個結論都有證據鏈。
- [ ] 加分：我能說出防禦者每層防護擋了我多久，以及作為開發者我會怎麼改進。

## 延伸閱讀

### 完整方法論標準

- **[OWASP MASTG](https://mas.owasp.org/MASTG/) 與 [MASVS](https://mas.owasp.org/MASVS/)**
  - **讀哪裡**：MASTG 的 Android reverse engineering / anti-reversing technique 全套，對照你這個 Final 的每個里程碑；MASVS-RESILIENCE 對照你建靶的四層防護。
  - **為什麼值得讀**：把你這個 Final 的「個人手藝」升級成「業界標準的稽核流程」——報告可以直接對齊 MASVS 需求編號，這是專業 App 安全評估報告的樣子。前提：本課全部。

### 攻防前沿

- **[看雪論壇](https://bbs.kanxue.com/) 與 [Frida CodeShare](https://codeshare.frida.re/)**
  - **讀哪裡**：看雪搜各家真實殼的脫法與 native 對抗；CodeShare 搜脫殼/pinning/反反調試的社群腳本。
  - **為什麼值得讀**：你這個自建靶打通後，真實 App 的差別在「別人設計的、更狠的防護」——這兩處是追蹤真實對抗前沿、把你的能力從自建靶推到真實目標的地方。前提：本 Final。

### 頂級漏洞研究

- **[Google Project Zero blog](https://googleprojectzero.blogspot.com/)**
  - **讀哪裡**：Android / 行動相關的漏洞分析，看他們怎麼從逆向走到 0day。
  - **為什麼值得讀**：這是這門課之後的天花板。你這個 Final 練的是「拆一個已知結構的防護目標」；Project Zero 練的是「在沒人知道有洞的地方找洞」。讀他們的思路，是你下一個階段的方向。前提：本課全部 + 紮實的二進位分析底子。

---

你走完了。從 Ch 0 建起一台能 root 的 AVD，到現在——你能拿一個帶加固殼、native 簽名、SSL pinning、反調試的 App，把它從一個不透明的黑盒，一路拆到能離線構造任意合法請求，並寫出一份別人能複現的報告。這條鏈——**recon 判形態、脫殼還原真碼、逆 native 看懂演算法、動態 hook 挖出金鑰、繞 pinning 還原協議、重放證明理解**——就是所有 App 逆向的縮影。

更重要的是，你不再是「工具驅動」的人。你腦中有那張攻擊地圖（Ch 1）、有那套決策樹方法論（Ch 38）、有防禦者的視角（Ch 41）。面對一個從沒見過的 App，你知道先看什麼、卡住往哪走、每層防護的縫在哪。這才是這門課真正給你的東西——不是四十幾個工具的用法，是**把它們編排成一次完整攻擊的判斷力**。

接下來去哪？回 [README 的精選資料庫](./README.md)「讀完本課之後」——把二進位分析推更深（《Practical Binary Analysis》）、追世界頂級的行動漏洞研究（Google Project Zero）。你這門課練的是「拆已知結構」，下一個階段是「在沒人知道有洞的地方找洞」。地基已經打好，天花板在前面。

→ 回到 [課程首頁 README](./README.md)
