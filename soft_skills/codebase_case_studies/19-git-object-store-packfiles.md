# Ch 19 — content-addressed store 與 packfile

> **目標**：把 Ch 18 建好的 object model 落到磁碟。讀懂 git 儲存層的兩層設計——loose object（一個 object 一個檔、zlib 壓縮、路徑由 oid 前兩碼分目錄）和 packfile（多 object 打包 + delta 壓縮 + idx 索引），並在 `object-file.c` / `packfile.c` 真讀寫入與讀出的關鍵函式。

> **目標codebase**：git v2.47.1（commit `92999a4`）

## 為什麼需要這個？

Ch 18 我們知道每個 object 的名字是它內容的雜湊。但「名字 = 雜湊」只解決了命名，沒解決兩個現實問題：

1. **怎麼存到磁碟？** 一個 oid 是 40 個 hex 字元，總不能全塞同一個目錄——某些檔案系統一個目錄放幾萬個檔會爛掉。
2. **怎麼不爆炸？** 一個 100 行的檔案改一行，git 會產生一個**全新的 blob**（內容變了，oid 就變了）。改一百次就是一百個幾乎一樣的 blob。純 content addressing 天生浪費空間。

git 的答案是**兩層儲存**：平常用 loose object（簡單、寫入快、一個 object 一個檔），累積多了或要傳輸時用 `git gc` 打包成 packfile（省空間、delta 壓縮相似 object）。這一章我們把這兩層都讀穿。

**這是 `reading_code` Ch 8「data flow 追蹤」的絕佳練習**：一個 object 從「記憶體裡一坨 buffer」到「磁碟上的位元組」，再從磁碟讀回記憶體，是一條完整的資料流。我們順著這條流讀。

## 先建立直覺

```
   一個 object（記憶體裡）
   type = blob, 內容 = "hello git\n"
        │
        │  write_object_file_flags()   ← 寫入入口
        ▼
   ┌──────────────────────────────────────────────────────┐
   │                     兩層儲存                          │
   │                                                       │
   │  第一層：loose object（日常）                          │
   │  .git/objects/8d/0e41234f24b6da...                    │
   │     └─ zlib.compress("blob 10\0hello git\n")          │
   │     路徑 = oid 前 2 碼當目錄 / 後 38 碼當檔名          │
   │                                                       │
   │        ── git gc / git repack ──▶                     │
   │                                                       │
   │  第二層：packfile（打包後）                            │
   │  .git/objects/pack/pack-<hash>.pack   ← 多 object 打包 │
   │  .git/objects/pack/pack-<hash>.idx    ← oid → offset  │
   │     相似 object 之間存 delta（只存差異）               │
   └──────────────────────────────────────────────────────┘
        │
        │  讀取時：先查 pack，查不到再找 loose
        ▼
   repo_read_object_file() ──▶ oid_object_info_extended()
        ├─ find_pack_entry()   有嗎？→ packed_object_info()
        └─ loose_object_info() 沒有？→ 讀 loose 檔 + zlib inflate
```

記住這張圖的兩個重點：**寫入永遠先寫 loose**（快），**讀取永遠先查 pack**（因為 gc 過後大部分 object 在 pack 裡）。

## 底層機制一：loose object 的路徑——oid 前兩碼分目錄

先回答「一個 object 存到哪個路徑」。git 把 oid 的 hex 表示切成 `前2碼/後38碼`——前兩碼當目錄名，其餘當檔名。這件事在 `fill_loose_path()` 做：

```c
// object-file.c:497 (v2.47.1)
static void fill_loose_path(struct strbuf *buf, const struct object_id *oid)
{
	int i;
	for (i = 0; i < the_hash_algo->rawsz; i++) {
		static char hex[] = "0123456789abcdef";
		unsigned int val = oid->hash[i];
		strbuf_addch(buf, hex[val >> 4]);
		strbuf_addch(buf, hex[val & 0xf]);
		if (!i)                           // ← 第一個 byte（前兩碼）之後
			strbuf_addch(buf, '/');   //    插一個斜線變成目錄分隔
	}
}
```

看 `if (!i)`：只有第 0 個 byte（也就是 oid 的前兩個 hex 字元）之後插一個 `/`，其餘都連著寫。所以 oid `8d0e412...` 的路徑是 `8d/0e412...`。外面包一層 `odb_loose_path()` 加上 object database 的根目錄：

```c
// object-file.c:510 (v2.47.1)
static const char *odb_loose_path(struct object_directory *odb,
				  struct strbuf *buf,
				  const struct object_id *oid)
{
	strbuf_reset(buf);
	strbuf_addstr(buf, odb->path);   // 例如 .git/objects
	strbuf_addch(buf, '/');
	fill_loose_path(buf, oid);        // 8d/0e412...
	return buf->buf;
}
```

為什麼要分目錄？**把幾百萬個檔案攤到 256 個子目錄**（前兩碼 = `00`~`ff`），每個目錄的檔案數降到 1/256，避免單一目錄檔案過多拖垮檔案系統。這是個古老但有效的 sharding 手法，你在很多快取系統（瀏覽器快取、CDN）都會看到同樣的「前幾碼分桶」。

用 Ch 18 的 demo repo 親眼確認（v2.47.1 真跑）：

```bash
$ find .git/objects -type f | sort
.git/objects/8d/0e41234f24b6da002d962a26c2495ea16a425f
.git/objects/c8/bcfef1da123a980537a5fa4cf9b7c4f387d451
.git/objects/e8/24989828dc7522a00ad6b2d950025df0cb1b49
```

三個 object，各自 `前2碼/後38碼`。這和 `fill_loose_path` 的邏輯完全對得上。

## 底層機制二：寫入路徑——header + zlib deflate + 原子 rename

現在讀「一個 object 怎麼被寫進那個路徑」。入口是 `write_object_file`（一個 inline 包裝），實際幹活的是 `write_object_file_flags`：

```c
// object-store-ll.h:274 (v2.47.1)
static inline int write_object_file(const void *buf, unsigned long len,
				    enum object_type type, struct object_id *oid)
{
	return write_object_file_flags(buf, len, type, oid, NULL, 0);
}
```

`write_object_file_flags` 的骨架（省略 SHA-256 相容分支）：

```c
// object-file.c:2465 (v2.47.1)
int write_object_file_flags(const void *buf, unsigned long len,
			    enum object_type type, struct object_id *oid,
			    struct object_id *compat_oid_in, unsigned flags)
{
	...
	char hdr[MAX_HEADER_LEN];
	int hdrlen = sizeof(hdr);
	...
	write_object_file_prepare(algo, buf, len, type, oid, hdr, &hdrlen);   // ① 算 oid
	if (freshen_packed_object(oid) || freshen_loose_object(oid))          // ② 已存在？
		return 0;                                                    //    直接返回（去重）
	if (write_loose_object(oid, hdr, hdrlen, buf, len, 0, flags))         // ③ 真寫 loose
		return -1;
	...
}
```

三步，每步都是一個可遷移的設計點：

**① `write_object_file_prepare` 先算出 oid。** 這裡藏著 content addressing 的實作。它先組表頭再雜湊：

```c
// object-file.c:1952 (v2.47.1)
static void write_object_file_prepare(const struct git_hash_algo *algo,
				      const void *buf, unsigned long len,
				      enum object_type type, struct object_id *oid,
				      char *hdr, int *hdrlen)
{
	git_hash_ctx c;
	/* Generate the header */
	*hdrlen = format_object_header(hdr, *hdrlen, type, len);
	/* Sha1.. */
	hash_object_body(algo, &c, buf, len, oid, hdr, hdrlen);
}
```

`format_object_header` 就是我們 Ch 18 手算時看到的 `"blob 10\0"` 從哪來——它本質是一行 `xsnprintf`：

```c
// object-file.c:1140 (v2.47.1)
static int format_object_header_literally(char *str, size_t size,
					  const char *type, size_t objsize)
{
	return xsnprintf(str, size, "%s %"PRIuMAX, type, (uintmax_t)objsize) + 1;
}
```

`"%s %ju"` 印出 `blob 10`，`+ 1` 把結尾的 `\0` 也算進長度。然後 `hash_object_body` 對 `header || 內容` 一起餵進雜湊：

```c
// object-file.c:1941 (v2.47.1)
static void hash_object_body(const struct git_hash_algo *algo, git_hash_ctx *c,
			     const void *buf, unsigned long len,
			     struct object_id *oid,
			     char *hdr, int *hdrlen)
{
	algo->init_fn(c);
	algo->update_fn(c, hdr, *hdrlen);   // 先餵 "blob 10\0"
	algo->update_fn(c, buf, len);       // 再餵內容
	algo->final_oid_fn(oid, c);         // 得到 oid
}
```

這就是 Ch 18 手算 `printf 'blob 10\0hello git\n' | sha1sum` 的 C 版本。**你手算的東西，這裡是它的實作。** 注意 `algo->init_fn` 這種寫法——雜湊演算法被抽象成一個 function pointer 表（`struct git_hash_algo`），SHA-1 和 SHA-256 各自填一組。這又是 indirection 的例子（`reading_code` Ch 23）。

**② `freshen_*` 是去重的守門。** 算出 oid 後先問「這個 object 已經存在（在 pack 或 loose）了嗎」，存在就直接返回、不重寫。這就是 content addressing 的去重紅利落到 code 上——**同內容不會存兩次**。

**③ `write_loose_object` 才真寫檔。** 它的關鍵是原子性：

```c
// object-file.c:2277 (v2.47.1)  —— 節錄
static int write_loose_object(const struct object_id *oid, char *hdr, ...)
{
	...
	loose_object_path(the_repository, &filename, oid);   // 最終路徑 8d/0e412...
	fd = start_loose_object_common(&tmp_file, ...);      // 先寫「暫存檔」
	...
	// zlib deflate：先壓 hdr，再壓 buf，串流輸出到 tmp_file
	...
	close_loose_object(fd, tmp_file.buf);
	...
	return finalize_object_file_flags(tmp_file.buf, filename.buf, ...);  // rename 暫存→正式
}
```

**先寫到暫存檔，全部寫完再 `rename` 成正式檔名。** 為什麼？`rename` 在同一檔案系統上是原子操作——要嘛整個 object 出現，要嘛完全不出現，**永遠不會出現寫到一半的壞 object**。這是「防禦式儲存」的經典手法，你在 SQLite 的 WAL（Ch 10）、資料庫的 commit 都會看到同一個「寫暫存 + 原子替換」idiom。

寫入完整資料流：

```
   buf ("hello git\n") + type(blob)
        │
        ▼  format_object_header → "blob 10\0"
   hash( "blob 10\0" + "hello git\n" ) = oid 8d0e412...
        │
        ▼  freshen：已存在？ 是→返回；否→往下
   zlib.deflate("blob 10\0hello git\n")
        │
        ▼  寫暫存檔 → rename 成 .git/objects/8d/0e412...（原子）
   磁碟上多了一個 loose object
```

用 Python 驗證 loose 檔就是 zlib 壓過的 `header + 內容`（v2.47.1 真跑）：

```bash
$ python3 -c "import zlib; \
  d=open('.git/objects/8d/0e41234f24b6da002d962a26c2495ea16a425f','rb').read(); \
  print(repr(zlib.decompress(d)))"
b'blob 10\x00hello git\n'
```

解壓出來正是 `blob 10\0hello git\n`。**磁碟上的 object = zlib.compress(header + 內容)**，一個位元都沒差。

## 底層機制三：讀取路徑——先查 pack，再找 loose

讀出來是寫入的鏡像。入口 `repo_read_object_file`：

```c
// object-file.c:1875 (v2.47.1)
void *repo_read_object_file(struct repository *r,
			    const struct object_id *oid,
			    enum object_type *type, unsigned long *size)
{
	struct object_info oi = OBJECT_INFO_INIT;
	unsigned flags = OBJECT_INFO_DIE_IF_CORRUPT | OBJECT_INFO_LOOKUP_REPLACE;
	void *data;
	oi.typep = type;
	oi.sizep = size;
	oi.contentp = &data;
	if (oid_object_info_extended(r, oid, &oi, flags))
		return NULL;
	return data;
}
```

它用一個 `struct object_info oi` 描述「我想要什麼」（型別填 `typep`、大小填 `sizep`、內容填 `contentp`），交給 `oid_object_info_extended`。這是個好用的 pattern——**用一個「請求描述 struct」取代一堆 out-parameter**，同一個查詢函式既能只問型別、也能要完整內容。

真正的「先 pack 後 loose」決策在 `do_oid_object_info_extended` 的迴圈裡：

```c
// object-file.c:1625 (v2.47.1)  —— 節錄迴圈核心
	while (1) {
		if (find_pack_entry(r, real, &e))     // ① 先在 packfile 找
			break;
		/* Most likely it's a loose object. */
		if (!loose_object_info(r, real, oi, flags))   // ② 找不到才找 loose
			return 0;
		/* Not a loose object; someone else may have just packed it. */
		if (!(flags & OBJECT_INFO_QUICK)) {
			reprepare_packed_git(r);          // ③ 都沒有？重掃 pack 再試一次
			if (find_pack_entry(r, real, &e))
				break;
		}
		...
	}
	...
	rtype = packed_object_info(r, e.p, e.offset, oi);  // 在 pack 裡 → 解 pack
```

讀清楚這個順序：**先 `find_pack_entry`（查 pack），失敗才 `loose_object_info`（讀 loose 檔）**。註解 `Most likely it's a loose object` 說明設計者的假設——但程式碼順序是 pack 優先，因為 `git gc` 之後絕大多數 object 都在 pack。loose 那條路最後會呼叫 `unpack_loose_header` + `unpack_loose_rest`，裡面就是 `git_inflate`（zlib 解壓），把磁碟上壓縮的位元組還原成記憶體 buffer。

**踩雷預告**：第一次讀你可能以為「loose 是主路徑、pack 是優化」，於是把注意力全放 loose。但實務上一個活躍 repo 的 object 九成在 pack 裡，pack 路徑才是熱路徑。讀碼時「code 裡先出現的分支」不代表「執行時最常走的分支」——這是 `reading_code` Ch 10「假設驅動」要驗證的：想知道哪條熱，用 gdb 下中斷點數次數，別用讀的猜。

## 底層機制四：packfile——打包 + delta 壓縮

loose object 一個一個存，簡單但浪費（相似 object 各存一份完整內容，且每個檔一份 zlib 表頭開銷）。`git gc` 會把它們打包成 **packfile**。packfile 有兩個檔：`.pack`（資料）+ `.idx`（索引）。

`.pack` 的檔頭定義：

```c
// pack.h:17 (v2.47.1)
struct pack_header {
	uint32_t hdr_signature;   // "PACK" == 0x5041434b
	uint32_t hdr_version;     // 2
	uint32_t hdr_entries;     // 這個 pack 裡有幾個 object
};
```

檔頭之後是一連串 object，每個 object 前面有個變長的 header 編碼「型別 + 解壓後大小」，解析在 `unpack_object_header_buffer`：

```c
// packfile.c:1091 (v2.47.1)  —— 節錄
unsigned long unpack_object_header_buffer(const unsigned char *buf, ...)
{
	...
	while (c & 0x80) {                          // 最高位是「還有下一個 byte」的旗標
		...
		size = st_add(size, st_left_shift(c & 0x7f, shift));  // 每 byte 貢獻 7 bits
		shift += 7;
	}
	...
}
```

這是 git 到處在用的 **varint（變長整數）**編碼：每個 byte 用低 7 bits 存資料，最高位（`0x80`）當「是否還有下一 byte」。小數字用一個 byte，大數字才用多 byte，省空間。認得這個 `while (c & 0x80)` 迴圈，你以後在很多二進位格式（protobuf、LEB128、pack 各處）都會再遇到。

packfile 的省空間關鍵是 **delta 壓縮**：對兩個相似的 object，不各存完整內容，而是存「以 A 為基礎，做這些修改得到 B」。這就是 Ch 18 那個 enum 裡 `OBJ_OFS_DELTA`(6) 和 `OBJ_REF_DELTA`(7) 的用途——一個 delta object 的「內容」是「基準 object 的參照 + 一串 copy/insert 指令」。`packfile.c:1228` 那段 `if (type == OBJ_OFS_DELTA) ... else if (type == OBJ_REF_DELTA)` 就是在解這兩種 delta 各自怎麼找到它的基準。

`.idx`（索引檔）讓你不用掃整個 pack 就能定位一個 oid：

```c
// pack.h:40 (v2.47.1)
#define PACK_IDX_SIGNATURE 0xff744f63	/* "\377tOc" */
```

idx v2 的結構是：256 個 fan-out 桶（按 oid 第一個 byte 快速二分）→ 排序的 oid 列表 → 對應的 pack 內 offset。`find_pack_entry` 就是靠它把「oid → 在 pack 裡的位元組偏移」查出來。

用 demo repo 真跑一次打包（v2.47.1）：

```bash
$ git gc -q
$ find .git/objects/pack -type f | sort
.git/objects/pack/pack-2b52f2c5....idx
.git/objects/pack/pack-2b52f2c5....pack
.git/objects/pack/pack-2b52f2c5....rev

$ git verify-pack -v .git/objects/pack/pack-2b52f2c5....idx
e824989828dc7522a00ad6b2d950025df0cb1b49 commit 137 106 12
8d0e41234f24b6da002d962a26c2495ea16a425f blob   10 19 118
c8bcfef1da123a980537a5fa4cf9b7c4f387d451 tree   36 46 137
non delta: 3 objects
```

`git gc` 後三個 loose object 進了 pack。`verify-pack -v` 每行是 `oid 型別 解壓後大小 pack內大小 pack內offset`。這個 demo 太小、object 之間不相似，所以 `non delta: 3 objects`（沒有 delta）；在真實 repo（同一檔案多個版本）你會看到大量 object 被存成 delta，`git verify-pack` 會多出「chain length」欄位。

## 對比與取捨

| 面向 | loose object | packfile |
|---|---|---|
| 一個 object | 一個獨立檔案 | 打包進一個大檔 |
| 壓縮 | 各自 zlib deflate | zlib + object 間 delta |
| 寫入成本 | 低（單檔寫 + rename） | 高（要挑基準、算 delta） |
| 讀取成本 | 開檔 + inflate | 查 idx + 可能解 delta chain |
| 適用時機 | 剛產生的新 object（日常） | gc 後、傳輸（clone/push/fetch） |
| 空間效率 | 差（相似 object 各存全份） | 好（delta 去掉重複） |

取捨的本質：**寫入要快就別當場優化，先 loose 丟著；空間要省就批次打包**。這是「寫入路徑輕、後台批次重整」的通用設計——對照 LSM-tree（先寫 memtable/寫入快，後台 compaction/整理），git 的 loose→pack 是同一個哲學：把昂貴的整理工作從關鍵路徑挪到背景。你在 `database_internals` 見過的 LSM 直覺，這裡完全用得上。

## 踩雷集錦

1. **以為 loose object 是純文字。** 錯。`.git/objects/8d/0e41...` 是 **zlib 壓縮過的二進位**，`cat` 它會看到亂碼。要看內容得 `git cat-file -p <oid>`，或自己 `zlib.decompress`。第一次直接 `cat` 看到亂碼別以為檔壞了。
2. **以為讀取先找 loose。** 錯。`do_oid_object_info_extended` 是**先 `find_pack_entry`（pack）再 `loose_object_info`（loose）**。gc 過的 repo 熱 object 全在 pack。code 裡註解說 "most likely loose" 是針對「剛寫入還沒 gc」的情境，別被註解誤導了執行時的實際分佈。
3. **把 delta 想成「commit 之間的 diff」。** packfile 的 delta 是**任意兩個相似 object 之間**的 delta，跟 commit 歷史無關——base 甚至可以是比較「新」的 object（git 打包時會挑最省空間的基準，不管誰先誰後）。delta 是儲存層的壓縮技巧，不是版本歷史。
4. **改一個字 git 就存整個新檔——覺得 git 很浪費。** 在 loose 階段確實是（新 blob = 全份內容）。但 `git gc` 後 delta 壓縮會把相似版本壓到只存差異。所以「git 浪費空間」只在 gc 前成立；理解兩層儲存才不會下錯判斷。
5. **在 `object-file.c` 找 `write_object_file` 的函式定義卻找不到本體。** 它是 `object-store-ll.h` 裡的 inline 包裝，本體是 `write_object_file_flags`。git 近年把不少函式拆成 `xxx` inline + `xxx_flags` 實體，`rg 'write_object_file'` 要看清楚哪個是宣告、哪個是定義。

## 進階：再往深一層

- **`git verify-pack -v` 讀 delta chain。** 在一個有歷史的真 repo 跑 `git verify-pack -v <idx> | grep -v "non delta"`，你會看到 `chain length = N` 的欄位——某個 object 要先還原它的 base、base 的 base…才能得到最終內容。chain 太長讀取會慢，`git repack` 有參數限制 chain 深度。這是空間 vs 讀取速度的取捨。
- **multi-pack-index（MIDX）。** 大 repo 會有很多 packfile，每次查 oid 要問每個 pack 的 idx。MIDX（`.git/objects/pack/multi-pack-index`）是「pack 的 pack」，一次查完所有 pack。想深入儲存層優化可以往這追。
- **`finalize_object_file` 的碰撞檢查。** 寫 loose 時如果目標路徑已存在（同 oid 已有 object），git 會（在某些模式下）比對內容確認不是 SHA-1 碰撞攻擊。這是 SHAttered（2017 SHA-1 碰撞）之後加的防禦，也是 git 遷移 SHA-256 的動機之一。順著 `check_collision`（`object-file.c:1978` 附近）可以讀到這段。

## 本章重點整理

- git 儲存分**兩層**：loose object（日常、一 object 一檔）與 packfile（打包 + delta、gc/傳輸用）。
- loose object 路徑 = oid **前 2 碼當目錄 / 後 38 碼當檔名**（`fill_loose_path` 的 `if (!i)` 插斜線），把幾百萬檔攤到 256 個桶。磁碟內容 = `zlib.compress("<type> <len>\0" + 內容)`。
- 寫入：`write_object_file_flags` → 算 oid（`format_object_header` + `hash_object_body`）→ freshen 去重 → `write_loose_object`（**寫暫存檔 + 原子 rename**，永不出現半寫壞檔）。
- 讀取：`repo_read_object_file` → `oid_object_info_extended` → **先 `find_pack_entry`（pack）再 `loose_object_info`（loose + zlib inflate）**。用 `struct object_info` 描述「要什麼」是可遷移的 pattern。
- packfile = `PACK` 檔頭 + 一串 object（varint header + zlib，相似的存 `OBJ_OFS_DELTA`/`OBJ_REF_DELTA`）+ `.idx`（fan-out + oid → offset）。loose→pack 的「寫入輕、後台批次整理」對照 LSM-tree。

## 自我檢核

- [ ] 我能說出 oid `8d0e412...` 對應的 loose 檔路徑，並解釋為什麼要前兩碼分目錄。
- [ ] 我能描述 `write_object_file_flags` 的三步（算 oid → freshen 去重 → write_loose_object），並說明「寫暫存 + rename」的原子性目的。
- [ ] 我能不看教材說出讀取路徑的分支順序（先 pack 後 loose），並解釋為什麼熱 object 多半在 pack。
- [ ] 我能解釋 packfile 為什麼比 loose 省空間（delta），以及 delta 跟 commit 歷史無關。
- [ ] 我能用 `python3 -c "import zlib; ..."` 解壓一個 loose 檔，看到 `<type> <len>\0<內容>`。

## 延伸閱讀

- **[Pro Git — 10.4 Packfiles](https://git-scm.com/book/en/v2/Git-Internals-Packfiles)**
  - **讀哪裡**：整節。它示範 `git gc` 前後 `.git/objects` 的變化、用 `git verify-pack` 觀察 delta，和本章的真跑輸出對照著看。
  - **學什麼**：從使用者視角理解 loose→pack 的空間變化，補足本章的 code 視角。
  - **前提**：讀過 Ch 18 的 object model。
- **`Documentation/gitformat-pack.txt`（本 clone 附帶，git 官方 pack 格式規格）**
  - **讀哪裡**：「Pack file format」與「Deltified representation」兩節。這是 packfile 位元組級的權威定義，`OBJ_OFS_DELTA`/`OBJ_REF_DELTA` 的編碼、delta 指令格式都在這。
  - **學什麼**：把 `packfile.c` 的解析 code 對回官方格式規格，讀二進位格式的標準做法（規格 + 解析器對照）。
  - **前提**：讀得懂位元組佈局圖。
- **本 clone 的 `object-file.c`（`fill_loose_path`、`write_object_file_flags`、`write_loose_object`、`do_oid_object_info_extended`）與 `packfile.c`（`unpack_object_header_buffer`）**
  - **讀哪裡**：`object-file.c:497 / :2465 / :2277 / :1625`、`packfile.c:1091`。
  - **學什麼**：親手 `sed -n` 出這幾段核對，特別注意寫入的「暫存 + rename」與讀取的「pack 優先」兩處。
  - **前提**：已 clone v2.47.1（`92999a4`）。

儲存層讀穿了：object 怎麼算名字、怎麼躺磁碟、怎麼打包、怎麼讀回。下一章我們把「命令分派」和「object store」接起來——挑一個真正的子命令（`git cat-file`），從 `builtin/cat-file.c` 的 entry 一路追到剛剛讀過的 `repo_read_object_file`，示範怎麼把一個 CLI 命令追進實作核心。

→ [Ch 20 讀一個 git 子命令的完整實作](./20-git-reading-a-command.md)
