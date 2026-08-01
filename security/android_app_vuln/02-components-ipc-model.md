# Ch 2 — 四大元件與 IPC 安全模型

> **目標**：徹底搞懂四大元件（Activity/Service/BroadcastReceiver/ContentProvider）與它們之間的 IPC 安全模型。重點打通三個決定後面所有元件漏洞的底層：**`exported` 的完整判定規則**（顯式 vs 隱式預設、有 intent-filter 的特殊預設、targetSdk 差異）、**permission 保護怎麼疊在 exported 之上**、以及 **Binder 是這一切底層的 IPC 機制**。這章之後，Ch 3–6 的每一個元件漏洞你都知道它為什麼成立、系統在哪一步該擋卻沒擋。

> **環境**：本章的 `exported` 判定與存取控制決策邏輯，用 **Python 3** 模擬 AOSP 的判定規則並在本機實跑，標「實際輸出」。真實系統行為（drozer 攻擊、系統回應）標「未實測，理論預期行為」並附驗證步驟。

## 為什麼需要這個？

Ch 1 說「元件/IPC 是投報率最高的攻擊面」。但「一個 exported 元件是漏洞」這句話，新手常常只記得結論、不懂機制——結果面對真實 App 時判斷全錯：把顯式 `exported="false"` 的當成能打、把沒寫 `exported` 卻因 intent-filter 而暴露的當成安全、看到有 permission 保護就以為打不了（其實那 permission 是 `normal` 等級，任何 App 都能拿）。

這些錯判的根源，是不懂 **Android 的元件存取控制到底怎麼運作**。系統在「App A 想碰 App B 的某個元件」時，內部走了一連串檢查：這元件 exported 嗎？沒寫的話預設是什麼？有 permission 保護嗎？那 permission 是哪個保護等級？呼叫方持有嗎？**每一步都是一個可能被繞過的關卡，也是一個可能被開發者搞錯的地方**。搞懂這條決策鏈，你才能準確判斷「這個元件到底能不能打、怎麼打」，而不是背結論。

而所有元件的 IPC，底層都跑在 **Binder** 上——理解 Binder 是理解「為什麼跨 App 呼叫時系統知道呼叫方是誰、能做權限檢查」的關鍵。這章把這些地基一次打穩。

## 先建立直覺：元件是 App 對外的「插座」

把一個 App 想成一棟房子。四大元件就是這棟房子**對外開的插座/門**——別的 App（或系統）透過這些介面跟你的 App 互動：

```
         別的 App / 系統                        你的 App（一棟房子）
   ┌────────────────────┐              ┌──────────────────────────────┐
   │                    │─ startActivity ─▶│ Activity   （一個畫面/入口）  │
   │                    │─ startService ──▶│ Service    （背景工作）      │
   │  攻擊者的 App        │─ sendBroadcast ─▶│ Receiver   （接收廣播事件）   │
   │                    │─ query/call ────▶│ Provider   （對外供資料）     │
   └────────────────────┘              └──────────────────────────────┘
              │                                        ▲
              └──── 這些箭頭全部走 Binder IPC ──────────┘
                    系統在 Binder 這一層知道「呼叫方是誰」，
                    才能做 exported / permission 檢查
```

四種插座，各接不同用途：

- **Activity**：一個畫面/UI 入口。別人 `startActivity` 能叫起你的某個畫面（登入頁、分享頁、內部設定頁）。
- **Service**：背景工作。別人 `startService`/`bindService` 能叫你的 Service 做事（同步、下載、處理資料）。
- **BroadcastReceiver**：事件接收器。別人 `sendBroadcast` 能觸發你註冊的接收器（收到「開機完成」「簡訊到了」或**自訂事件**）。
- **ContentProvider**：結構化資料的對外介面。別人 `query`/`insert`/`call` 能存取你透過 Provider 暴露的資料（聯絡人、檔案、DB）。

**每個插座都可以是「對內」或「對外」的**。對內（`exported=false`）：只有自家 App 能用，是安全的內部通訊。對外（`exported=true`）：任何別的 App 都能插進來——這就是攻擊面。這章的核心，就是搞懂**一個插座到底是對內還是對外，系統怎麼判定，以及有沒有加鎖（permission）**。

## Intent：元件之間的「信封」

在講存取控制之前，先懂元件之間傳的東西：**Intent**（意圖）。Intent 是一個「信封」，裝著「我想叫哪個元件做什麼、附帶什麼資料」。它分兩種，這個區分是後面 intent redirection（Ch 4）的地基：

```
Explicit Intent（顯式）              Implicit Intent（隱式）
指名道姓叫哪個元件                    只說「我要做什麼」，讓系統挑
┌──────────────────────┐          ┌──────────────────────────┐
│ setClassName(         │          │ setAction("VIEW")         │
│   "com.b",            │          │ setData("https://...")    │
│   "com.b.LoginAct")   │          │ → 系統找誰能處理 VIEW+https │
│ → 明確送給 com.b       │          │   （瀏覽器？你的 App？）    │
└──────────────────────┘          └──────────────────────────┘
   安全：目標明確                     風險：目標由 intent-filter 匹配決定
                                    → 可能被惡意 App 攔截或冒充
```

- **Explicit Intent（顯式）**：直接指定「送給 `com.b` 的 `LoginActivity`」。目標明確，通常是 App 內部或明確跨 App 的呼叫。
- **Implicit Intent（隱式）**：只說「我要 VIEW 一個 https 連結」，由系統根據各 App 註冊的 **intent-filter** 匹配出「誰能處理」。這是 Android 元件解耦的機制（你不用知道用哪個瀏覽器），但也是攻擊面——**惡意 App 可以註冊一個匹配的 intent-filter 來攔截隱式 Intent**，或誘導受害 App 把隱式 Intent 轉發到攻擊者控制的地方。

**intent-filter** 就是元件貼在插座上的標籤：「我能處理 action=VIEW、scheme=https 的 Intent」。系統靠它匹配隱式 Intent。而**貼了這個標籤，會影響元件的 exported 預設**——這是下一節的關鍵。

## 核心：`exported` 的完整判定規則

這是本章最重要、也最多人搞錯的一塊。一個元件「對外可觸及嗎」由 `exported` 屬性決定，但它的判定有三層，每一層都是坑。

### 底層機制：系統怎麼決定一個元件是否 exported

Android 在安裝 App 時（`PackageParser`/`PackageManagerService` 解析 Manifest），對每個元件計算它的 effective exported 值。決策流程：

```
                 這個元件有沒有顯式寫 android:exported？
                          │
         ┌────────────────┴────────────────┐
       有寫                              沒寫（隱式）
         │                                  │
    直接用寫的值              ┌──────────────┴──────────────┐
   true → exported        這個元件是 Provider?          activity/service/receiver?
   false → 不 exported         │                              │
                       targetSdk >= 17?              這個元件有 intent-filter?
                        │        │                    │              │
                      是→false  否→true              有→ true        沒有→ false
                    (Provider 的                   (有 filter 就       (沒 filter
                     預設與 filter                   預設 exported！)    預設關閉)
                     無關)

       ★ targetSdk >= 31 (Android 12+) 的特例：
         有 intent-filter 的元件「必須顯式宣告 exported」，
         否則安裝時直接報錯（拒裝）。這逼開發者不能再靠隱式預設。
```

三個必須刻進腦子的規則：

1. **有寫 `exported` → 直接用寫的值**。`exported="false"` 就是關、`exported="true"` 就是開。這一層沒陷阱。
2. **沒寫 `exported`（隱式）+ activity/service/receiver**：**有 intent-filter → 預設 exported=true**；沒有 intent-filter → 預設 false。這是**最常見的意外暴露來源**——開發者加了 intent-filter（為了讓 App 能被叫起、或處理某個 action），沒意識到這順帶把元件對全世界開放了。
3. **沒寫 `exported` + Provider**：targetSdk ≥ 17（Android 4.2+，也就是幾乎所有現代 App）預設 **false**。Provider 的預設**跟有沒有 intent-filter 無關**——這跟前三種元件不同，很容易記混。

還有一個現代化的關鍵演進：**targetSdk ≥ 31（Android 12+）**，任何有 intent-filter 的元件**必須顯式寫 `exported`**，否則系統拒絕安裝。這是 Google 為了堵「隱式暴露」這個長年坑而做的——但它只對高 targetSdk 生效，**大量舊 targetSdk 的 App 仍在靠隱式預設**，所以規則 2 到 2026 年還是你評估時的重點。

### 範例 1：跑一遍判定邏輯（實際輸出）

把上面的規則寫成 Python，對一段涵蓋各種情況的 Manifest 跑一次，看每個元件的 effective exported：

```python
import xml.etree.ElementTree as ET
ANDROID = "{http://schemas.android.com/apk/res/android}"

manifest = '''<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.example.target"><application>
  <activity android:name=".A" android:exported="true"/>
  <activity android:name=".B" android:exported="false"/>
  <activity android:name=".C">                                  <!-- 沒寫、有 filter -->
    <intent-filter><action android:name="android.intent.action.VIEW"/></intent-filter>
  </activity>
  <activity android:name=".D"/>                                 <!-- 沒寫、沒 filter -->
  <activity android:name=".E">                                  <!-- LAUNCHER 入口 -->
    <intent-filter>
      <action android:name="android.intent.action.MAIN"/>
      <category android:name="android.intent.category.LAUNCHER"/>
    </intent-filter>
  </activity>
  <provider android:name=".F" android:authorities="com.example.f"/>  <!-- 沒寫 -->
</application></manifest>'''

def effective_exported(elem, tag):
    raw = elem.get(ANDROID + "exported")
    if raw is not None:
        return raw == "true", "顯式宣告"
    has_filter = elem.find("intent-filter") is not None
    if tag == "provider":
        return False, "隱式預設(provider, targetSdk>=17 → false)"
    return has_filter, ("隱式預設(有 intent-filter → true)" if has_filter
                        else "隱式預設(無 intent-filter → false)")

app = ET.fromstring(manifest).find("application")
for tag in ("activity", "provider"):
    for elem in app.findall(tag):
        val, why = effective_exported(elem, tag)
        print(f"{tag:9} {elem.get(ANDROID+'name'):3} exported={str(val):5}  <- {why}")
```

**實際輸出**：

```
activity  .A  exported=True   <- 顯式宣告
activity  .B  exported=False  <- 顯式宣告
activity  .C  exported=True   <- 隱式預設(有 intent-filter → true)
activity  .D  exported=False  <- 隱式預設(無 intent-filter → false)
activity  .E  exported=True   <- 隱式預設(有 intent-filter → true)
provider  .F  exported=False  <- 隱式預設(provider, targetSdk>=17 → false)
```

三個要看懂的地方：

- **`.C`**：開發者**根本沒寫 exported**，卻因為有 intent-filter 而 exported=true。這就是規則 2——最容易被開發者意外暴露、也最容易被評估者漏掉的一類。
- **`.E`**：連 App 的**啟動入口**（MAIN/LAUNCHER）也因為有 intent-filter 而 exported=true。這是**正常且必要**的（不 exported 桌面就叫不起它），但也意味著 LAUNCHER Activity 本身是個 exported 元件，別的 App 也能直接 `startActivity` 它——如果它假設「只會被桌面正常叫起」而沒防畸形輸入，就有洞。
- **`.F`**：Provider 沒寫 exported，預設 false，**跟它有沒有 intent-filter 無關**。這跟 `.C` 的規則不同，別記混。

### 範例 2：exported 之上還有 permission 這一層（實際輸出）

`exported=true` 不等於「任何 App 都能無條件碰」——元件還可以用 **permission** 加鎖。系統的完整存取檢查是**兩層**：先看 exported，再看 permission。模擬這條決策鏈：

```python
def can_access(exported, has_filter, permission, caller_holds):
    if exported is None:              # 隱式：activity/service/receiver 規則
        exported = has_filter
    if not exported:
        return "拒絕（非 exported，只有同 App/同 UID 可存取）"
    if permission is not None:        # exported 但有 permission 保護
        if caller_holds and permission in caller_holds:
            return "允許（exported + 呼叫方持有所需 permission）"
        return f"拒絕（缺 permission: {permission}）"
    return "允許（exported 且無 permission 保護 → 任何 App 可存取）"

cases = [
    ("exported=true, 無 permission",                 True,  False, None,         None),
    ("exported=true, 要 perm, 呼叫方沒有",           True,  False, "com.x.PERM", set()),
    ("exported=true, 要 perm, 呼叫方有",             True,  False, "com.x.PERM", {"com.x.PERM"}),
    ("沒寫 exported, 有 intent-filter",              None,  True,  None,         None),
    ("exported=false（顯式關）",                     False, True,  None,         None),
]
for desc, e, f, p, held in cases:
    print(f"{desc:30} => {can_access(e, f, p, held)}")
```

**實際輸出**：

```
exported=true, 無 permission           => 允許（exported 且無 permission 保護 → 任何 App 可存取）
exported=true, 要 perm, 呼叫方沒有     => 拒絕（缺 permission: com.x.PERM）
exported=true, 要 perm, 呼叫方有       => 允許（exported + 呼叫方持有所需 permission）
沒寫 exported, 有 intent-filter        => 允許（exported 且無 permission 保護 → 任何 App 可存取）
exported=false（顯式關）               => 拒絕（非 exported，只有同 App/同 UID 可存取）
```

這條兩層決策鏈就是你評估每個元件時腦中該跑的流程：**exported 嗎？→ 有 permission 保護嗎？→ 那 permission 呼叫方拿得到嗎？** 三個「是」才是真正能打的洞。

但這裡藏著一個大坑，範例本身看不出來：**permission 有「保護等級」**。第二列「呼叫方沒有 permission → 拒絕」看起來安全，但如果那個 permission 是 `normal` 等級——**任何 App 只要在自己 Manifest 宣告 `<uses-permission>` 就自動獲得，安裝時不需使用者同意、不需簽名一致**。也就是說 `normal` permission 的「保護」形同虛設，攻擊者的 App 宣告一下就拿到了。真正擋得住的是 `signature` 等級（呼叫方必須用**同一把簽名 key** 才能拿到），這是 Ch 13 自訂 permission 缺陷的核心。這個 permission 等級的陷阱，用純邏輯模擬不出來，得記住：**看到有 permission 保護，先問它是哪個等級**。

### 邊界情況：exported 但實際打不到

有幾種情況元件 exported=true，攻擊者卻不一定打得到，評估時別誤報：

- **元件所屬 App 本身有簽名級保護，或整個 App 被 `android:permission` 在 `<application>` 層統一加鎖**：`<application>` 上的 permission 會套用到所有元件，個別元件沒寫也繼承。
- **Activity 有 `android:permission` 但實際邏輯還檢查了呼叫來源**：有些 App 在 `onCreate` 裡再用 `getCallingActivity()` 驗來源，程式碼層多一道防線。這種要動態驗才知道打不打得到。
- **元件 exported 但功能無害**：一個 exported 的「顯示版本號」Activity，就算誰都能叫起也造不成危害。exported 是「可觸及」，**可觸及 ≠ 有漏洞**（Ch 1 的 finding vs vulnerability）。

### 範例 3：一個真正能打的 exported 元件長什麼樣

把前面的機制落到一段具體程式碼。假設目標 App 有這個 Manifest 片段：

```xml
<activity android:name=".AdminActivity">
    <intent-filter>
        <action android:name="com.example.target.ADMIN"/>
    </intent-filter>
</activity>
```

`AdminActivity` **沒寫 `exported`**，但有 intent-filter → 依規則 2，effective exported = **true**（若 targetSdk < 31）。它的程式碼（手寫，語法正確的 Java）：

```java
public class AdminActivity extends Activity {
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        Intent intent = getIntent();
        // 危險假設：以為只有自家 App 會用一個「內部」action 叫起它，
        // 所以直接信任 Intent 帶的 extra，跳過登入檢查。
        boolean isInternal = intent.getBooleanExtra("is_admin", false);
        if (isInternal) {
            // 直接進到需要管理員權限的功能，沒有任何身分驗證
            startActivity(new Intent(this, AdminPanelActivity.class));
        }
    }
}
```

漏洞在於：開發者假設「只有自家 App 會傳 `is_admin=true`」，把一個**本該由認證邏輯決定的狀態**交給 Intent 的 extra 決定。但因為元件 exported，**任何 App 都能傳這個 extra**。攻擊者用 drozer（扮演惡意 App）構造：

```bash
dz> run app.activity.start --component com.example.target \
    com.example.target.AdminActivity \
    --action com.example.target.ADMIN \
    --extra boolean is_admin true
```

或用 adb（權限比 App 高，但能快速驗證漏洞存在）：

```bash
adb shell am start -n com.example.target/.AdminActivity \
    -a com.example.target.ADMIN --ez is_admin true
```

> **未實測，理論預期行為**：這需要目標 App 裝在 AVD。drozer 的 `app.activity.start` 與 adb 的 `am start` 都是啟動 exported Activity 的標準指令；`--ez`（adb）/`--extra boolean`（drozer）傳布林 extra。驗證步驟：對一個埋了類似洞的靶場（AndroGoat 有多個 exported Activity 繞過類的關卡）跑這條指令，若直接跳進本該登入才看得到的畫面，漏洞成立。**drozer vs adb 的差別很重要**：adb shell 是 root/shell 權限，能繞過某些檢查，容易讓你誤判「打得到」；drozer 用 agent App 的身分，才真實反映「一個惡意 App 打不打得到」。判斷可利用性以 drozer 為準。

這個範例把整章串起來了：**exported 判定（規則 2，沒寫但有 filter）→ 系統層放行（無 permission）→ 程式碼層的錯誤信任（信了 Intent 的 extra）→ 可觸發的危害（繞過登入）**。Ch 3 會把這類攻擊系統化。

## 底層：Binder —— 這一切 IPC 的地基

前面所有「跨 App 呼叫」——`startActivity`、`query`、`sendBroadcast`——底層全部走 **Binder**。理解 Binder 才理解「系統為什麼能在跨 App 呼叫時知道呼叫方是誰，從而做 permission 檢查」。

```
   App A (攻擊者)                Binder driver (kernel)              App B (目標)
 ┌──────────────┐              ┌──────────────────────┐          ┌──────────────┐
 │ startActivity│─ transact ──▶│  /dev/binder          │─ onTransact─▶│ 目標元件      │
 │  (Intent)    │              │  ★ 帶上 caller 的      │          │              │
 │              │              │    UID / PID          │          │ 系統可查      │
 │              │◀── reply ────┤  (kernel 保證真實)     │◀─ reply ─┤ getCallingUid│
 └──────────────┘              └──────────────────────┘          └──────────────┘
                                        │
                    關鍵：呼叫方的 UID/PID 由 kernel 填上，
                    App 無法偽造 → 這是 Android 權限檢查可信的根基
```

要點：

- **Binder 是 Android 的核心 IPC 機制**，一個 kernel driver（`/dev/binder`）。Activity 啟動、Service 綁定、廣播、Provider 查詢，全部是 Binder transaction 的包裝。
- **Binder 在每筆 transaction 上，由 kernel 填上呼叫方的真實 UID/PID**——這是 App 無法偽造的。目標 App 用 `Binder.getCallingUid()`/`getCallingPid()` 就能拿到「是誰在呼叫我」。
- **這就是 permission 檢查可信的根基**：系統要判斷「呼叫方有沒有某 permission」，先靠 Binder 拿到呼叫方 UID，再查那個 UID 對應的 App 有沒有被授予該 permission。因為 UID 由 kernel 保證真實，這套檢查偽造不了。
- 每個 App 跑在**獨立 UID** 的沙箱裡（android_reversing Ch 3 的內容），Binder 是它們之間唯一受控的溝通管道——所以攻擊面全集中在 Binder 上暴露的那些元件。

理解 Binder 帶 UID 這件事，還解釋了一個進階攻擊的地基：如果目標元件**自己不做 `getCallingUid` 檢查、只靠 exported/permission**，那 permission 一旦是 normal 級就沒防護；反過來，有些 App 在程式碼裡加 `getCallingUid` 白名單當第二道防線——這種要動態繞（Frida 改 `getCallingUid` 回傳值）才打得動。

## 每種元件怎麼被觸發：從 API 到攻擊指令

四種元件的攻擊入口不同——你要用對的 IPC 動作才能觸發它。把「正常呼叫用的 Android API」對到「攻擊時用的 drozer/adb 指令」，這是 Part 2 每一章的操作基礎：

| 元件 | 正常呼叫的 API | 攻擊觸發（drozer / adb） | 攻擊者主要塞什麼 |
|---|---|---|---|
| **Activity** | `startActivity(intent)` | `app.activity.start` / `am start` | Intent 的 action、data、extra |
| **Service** | `startService`/`bindService` | `app.service.start` / `am startservice` | Intent 的 extra、命令參數 |
| **Receiver** | `sendBroadcast(intent)` | `app.broadcast.send` / `am broadcast` | 偽造的事件與其挾帶資料 |
| **Provider** | `query`/`insert`/`update`/`call` | `app.provider.query` / `content query` | URI path、selection（SQL）、檔案路徑 |

關鍵觀念：**攻擊者能控制的是「送進去的那個 Intent / URI」的每一個欄位**。元件收到後怎麼處理這些欄位，決定有沒有洞：

- **Activity/Service**：攻擊者控制 `Intent` 的 extra。元件若把 extra 當成信任的狀態（範例 3 的 `is_admin`）、當成要載入的 URL（WebView redirection）、當成要轉發的目標 Intent（Ch 4 intent redirection），就有洞。
- **Receiver**：攻擊者發一個匹配的偽造廣播。元件若信任廣播內容代表「真的發生了某事件」（如「支付成功」），就被騙。
- **Provider**：攻擊者控制查詢的 URI 與 selection。若 selection 直接拼進 SQL → SQLi（Ch 6）；若 path 直接拼進檔案路徑 → path traversal 讀任意檔。

這張表 + 「攻擊者控制送進去的每個欄位」這個觀念，是你把 Ch 3–6 每一類攻擊對號入座的鑰匙。

## 對比與取捨：四大元件的攻擊面差異

| 元件 | 攻擊者能做什麼 | 隱式 exported 預設 | 典型漏洞 | 危險度 |
|---|---|---|---|---|
| **Activity** | 叫起任意畫面、繞登入、灌畸形 Intent | 有 intent-filter → true | 繞過認證、intent redirection | 中–高 |
| **Service** | 觸發背景工作、傳惡意參數 | 有 intent-filter → true | 未授權操作、confused deputy | 中 |
| **BroadcastReceiver** | 發偽造事件觸發邏輯 | 有 intent-filter → true | 偽造狀態、注入資料 | 中 |
| **ContentProvider** | 查/改資料、SQLi、讀任意檔 | **預設 false**（跟 filter 無關） | SQLi、path traversal、資料洩漏 | **高** |

幾個取捨判斷：

- **Provider 一旦 exported 通常最危險**：它直接對外供資料，SQLi 能拖庫、`openFile` 能讀任意檔（Ch 6）。但它預設 false，所以看到一個 exported Provider 要特別警覺——開發者是刻意開的。
- **Activity 的暴露最普遍**：因為 LAUNCHER 與各種 intent-filter 讓 Activity 常常 exported。多數不危險（就是個畫面），但「繞過登入直接叫起內部畫面」「把隱式 Intent 轉發出去」是常見洞。
- **Receiver 常被低估**：一個接收「支付成功」自訂廣播的 exported Receiver，攻擊者發個偽造廣播就能騙 App 以為付款完成。

## 踩雷集錦

1. **以為「沒寫 exported 就是安全的」**：錯誤直覺「沒宣告 = 關閉」——正確：**有 intent-filter 的 activity/service/receiver，沒寫 exported 預設就是 true**。這是最常見的意外暴露，也最容易在評估時漏掉。永遠算 effective exported，別只看有沒有寫。
2. **把 Provider 的預設規則套到其他元件（或反過來）**：錯誤直覺「有 intent-filter 就 exported，Provider 也一樣」——正確：**Provider 的隱式預設是 false 且跟 intent-filter 無關**；activity/service/receiver 才是「有 filter → true」。兩套規則別記混。
3. **看到有 permission 保護就判定打不了**：錯誤直覺「有加鎖就安全」——正確：先看 permission 的**保護等級**。`normal` 級任何 App 宣告一下就拿到，形同沒鎖；只有 `signature` 級（要同簽名）才真的擋得住。判斷可利用性一定要查 protectionLevel。
4. **忽略 targetSdk 對預設的影響**：錯誤直覺「規則到處一樣」——正確：**targetSdk ≥ 31 有 intent-filter 必須顯式宣告 exported**（否則拒裝），但大量舊 targetSdk App 仍靠隱式預設。評估第一步先看 targetSdk，它決定哪套規則生效。
5. **把「exported」等同「有漏洞」**：錯誤直覺「exported 就是洞」——正確：exported 只是「可觸及」，能不能造成危害要看元件收到 Intent 後**做了什麼**（有沒有敏感操作、有沒有驗證輸入）。exported 是 finding，可利用才是 vulnerability。
6. **忘了 `<application>` 層的 permission 會繼承**：錯誤直覺「元件沒寫 permission 就沒保護」——正確：`<application>` 上的 `android:permission` 套用到所有子元件。個別元件看起來沒防護，可能被 application 層統一加了鎖。要連 application 標籤一起看。

## 進階：再往深一層

- **Intent 的 category 與 data 也參與匹配**：隱式 Intent 的匹配不只看 action，還要 category 與 data（scheme/host/mimeType）全部對得上 intent-filter。這是 deeplink（Ch 7）的機制基礎——一個 `<data android:scheme="myapp" android:host="pay"/>` 就定義了 `myapp://pay/...` 這條可被外部觸發的深連結。intent-filter 的匹配規則比「看 action」複雜得多，Ch 7 會展開。
- **`android:permission` 之外還有 `readPermission`/`writePermission`（Provider 專屬）**：Provider 能分別對讀與寫設不同 permission，甚至對 path 設 `path-permission`（不同路徑不同權限）。這帶來一種常見誤配：writePermission 設了、readPermission 忘了設，導致資料可讀不可改——但「可讀」往往已經是洩漏。Ch 6 細講。
- **`grantUriPermissions` 與臨時授權**：Provider 可以宣告 `grantUriPermissions="true"`，讓它即使 exported=false，也能透過 Intent 的 `FLAG_GRANT_READ_URI_PERMISSION` **臨時授權**給收到 Intent 的 App。這是 confused deputy 與 intent redirection（Ch 4）能洩漏 Provider 資料的機制——攻擊者不直接打 Provider，而是騙一個有權限的 App 幫它拿。
- **`getCallingUid` 的信任邊界細節**：`Binder.getCallingUid()` 只在**跨進程呼叫**時回傳呼叫方 UID；如果呼叫發生在**同進程**（例如 App 內部直接呼叫，不經 Binder），它回傳的是自己的 UID。有些防護寫成「`getCallingUid() == MY_UID 就放行」，攻擊者若能讓呼叫看起來同進程（或該防護邏輯有邊界 bug）就可能繞過。這類細節是進階元件攻擊與 Frida 動態繞過的著力點。
- **從這章到實戰**：這章講的是「系統層」的存取控制（exported + permission + Binder）。但真正的漏洞常在**元件收到 Intent 之後的程式碼**——它信任了 Intent 裡的哪個 extra？把它當檔名（path traversal）、當 SQL（injection）、當要轉發的目標（redirection）？系統層讓攻擊者「碰得到」，程式碼層決定「碰到之後能造成什麼」。Ch 3 起我們就從系統層走進程式碼層。

## 動手練習

1. 用 apktool 解一個靶場（AndroGoat 或 InsecureBankv2）的 Manifest，對**每一個** activity/service/receiver/provider **手動算 effective exported**（照本章的判定流程），做成一張表。再用 `drozer app.package.attacksurface` 對同一個 App 跑一次，**對照你手算的結果跟 drozer 的輸出**——如果有出入，去查為什麼（通常是你漏了某條規則，或 drozer 的版本對某個 targetSdk 判定不同）。這是把判定規則變成肌肉記憶的最好方式。
2. 把本章範例 1 的 Python 擴充：讓它同時處理 service 與 receiver，並加一個 `.G` service「沒寫 exported、有 intent-filter」，確認你的程式判它 exported=true。再加一個 provider「顯式 exported=true」，確認顯式優先於預設。跑出來對照本章規則。
3. 找出你手算的表裡，哪些元件是「exported 但看起來無害」（如顯示資訊的 Activity）、哪些是「exported 且危險」（如接收敏感廣播的 Receiver、供資料的 Provider）。這一步就是把 finding 分級成 vulnerability candidate——Ch 3 起會逐一驗證它們。

## 本章重點整理

- 四大元件（Activity/Service/Receiver/Provider）是 App 對外的「插座」；`exported` 決定插座對內還是對外，是所有元件漏洞的第一道判定。
- **exported 判定三規則**：(1) 有寫 → 用寫的值；(2) 沒寫 + activity/service/receiver → **有 intent-filter 就預設 true**（最常見意外暴露）；(3) 沒寫 + Provider → **預設 false 且與 intent-filter 無關**。targetSdk ≥ 31 有 filter 必須顯式宣告。
- **存取控制是兩層**：exported → permission。且 permission 有**保護等級**，`normal` 級形同沒鎖，只有 `signature` 級真的擋得住。
- **Binder 是底層 IPC**，kernel 在每筆 transaction 填上呼叫方真實 UID/PID——這是 Android permission 檢查偽造不了的根基。
- **exported ≠ 漏洞**：系統層讓攻擊者「碰得到」，程式碼層（元件收到 Intent 後做什麼）決定「能造成什麼危害」。

## 自我檢核

- [ ] 不看筆記，能畫出「一個元件是否 exported」的完整判定流程（含顯式、隱式、Provider 特例、targetSdk 31 特例）
- [ ] 能解釋為什麼「沒寫 exported 但有 intent-filter」的 Activity 是最常被漏掉的攻擊面
- [ ] 能說出 Provider 的隱式 exported 預設，以及它跟 activity/service/receiver 規則的差別
- [ ] 看到一個元件有 permission 保護，知道下一步要查什麼（protectionLevel），以及為什麼 normal 級形同沒鎖
- [ ] 能解釋 Binder 如何讓系統知道「呼叫方是誰」，以及這為什麼是 permission 檢查可信的根基
- [ ] 能區分「exported（可觸及）」與「vulnerability（可造成危害）」的差別

## 延伸閱讀

### Android 官方文件

- **[Application Fundamentals — App Components](https://developer.android.com/guide/components/fundamentals)** — Android Developers
  - **讀哪裡**：四大元件與 Intent 的定義；`<intent-filter>` 那節
  - **和本章的關聯**：本章的「插座」模型與 Intent 顯式/隱式區分建立在這；先懂正向設計，才懂哪裡被誤用
- **[`<activity>` / `<provider>` Manifest 元素文件](https://developer.android.com/guide/topics/manifest/activity-element)** — Android Developers
  - **讀哪裡**：`android:exported`、`android:permission` 屬性的說明，特別是**各版本的預設值差異**那段
  - **注意**：官方明確寫了 targetSdk ≥ 31 有 intent-filter 必須宣告 exported——本章判定規則的權威來源
- **[Android Interface Definition Language (AIDL) 與 Binder](https://developer.android.com/develop/background-work/services/aidl)** — Android Developers
  - **讀哪裡**：Binder 作為 IPC 機制、`getCallingUid` 那段
  - **和本章的關聯**：本章 Binder 帶 UID 的機制，官方 AIDL 文件是最接近的一手說明

### 權限與安全模型

- **[Android 權限保護等級 `protectionLevel`](https://developer.android.com/guide/topics/manifest/permission-element)** — Android Developers
  - **讀哪裡**：`normal`/`dangerous`/`signature` 各等級的差別
  - **為什麼值得讀**：本章「permission 有等級、normal 形同沒鎖」的判斷，這頁是依據；Ch 13 會再深入

### 攻擊視角

- **[OWASP MASTG — Android IPC 與元件測試](https://mas.owasp.org/MASTG/techniques/android/)**
  - **這篇說什麼**：怎麼系統化測試元件暴露與 IPC 安全（對應 MASVS-PLATFORM）
  - **讀哪裡**：Android platform 的 IPC / component 測試技術那幾節
  - **和本章的關聯**：本章教「機制」，這頁教「怎麼測」，Ch 3 起是「怎麼利用」
- **[HackTricks — Android Components & IPC](https://book.hacktricks.wiki/en/mobile-pentesting/android-app-pentesting/index.html)**
  - **這篇說什麼**：exported 元件的實戰枚舉與攻擊指令（drozer/adb）
  - **讀哪裡**：Manifest 分析與 exported components 那幾段
  - **前提知識**：讀過本章的判定規則，這頁給你對應的攻擊指令

系統層的存取控制講完了——你現在能準確判斷「哪些元件碰得到」。下一章走進程式碼層：一個真正 exported 且危險的元件長什麼樣，怎麼用 drozer/adb 構造 Intent 把它打出來、繞過登入、觸發未授權操作。我們從最普遍的一類開始——exported 元件濫用。

→ [Ch 3 exported 元件濫用](./03-exported-components.md)
