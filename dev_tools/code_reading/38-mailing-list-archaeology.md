# Ch 38 — mailing list 考古：lore.kernel.org 與 patch series

> **目標**：從一個 commit 的 `Link:` 或 Message-ID 找到它在 mailing list 上的原始討論，把整串討論抓到本機讀；分辨 cover letter、patch、review 回覆各自提供什麼資訊；看出「被提交的版本」和「最後被合併的版本」之間差了什麼、為什麼；會用 `b4` 把一個 patch series 連同 review trailer 抓下來。
> **鴻溝**：本章主要練**脈絡鴻溝**。Ch 37 用 git 拿到的是作者事後整理的結論；mailing list 上留著結論之前的所有東西——被否決的方案、reviewer 的質疑、作者改變主意的那一刻。

> **環境**：WSL Ubuntu 22.04、curl 7.81、Python 3.10、b4 0.15.2（本章實際安裝，過程見下文）；v6.18 tree 同 Ch 37。lore.kernel.org 的存取於 2026-11-21 實測：從這個環境用 curl 存取**討論串的 mbox 下載端點**（`.../t.mbox.gz`）成功，但 **HTML 網頁、`/raw`、搜尋頁都回 403**（網站對自動化存取的防護）。所以本章貼出的信件內容全部來自實際下載的 mbox；搜尋語法一節**未實測**，會明確標示。

## 為什麼需要這個？

Ch 37 結尾，我們挖出了 `fs/pipe.c` 那行 `if (was_empty || pipe->poll_usage)` 的五層歷史。每一層的 commit message 都寫得很好，但它們有共同的限制：

- commit message 是**最終版本**的說明。被拒絕的 patch 不會進 git。
- commit message 是**作者**的觀點。reviewer 的反對意見、測試數據、作者被說服的過程，都不在裡面。
- patch 正文裡 `---` 之後的「給 reviewer 的附註」，`git am` 時會被丟掉，永遠不會進 git。

kernel 的開發完全在公開的 mailing list 上進行——不是 GitHub PR，不是內部 issue tracker，是 email。每一個進入主線的 patch，都曾經是一封寄到某個 list 的信（[Ch 35](./35-kernel-source-tree-map.md) 用 `get_maintainer.pl` 查到的 `L:` 就是那個 list）。這些信全部被 lore.kernel.org 封存，而且**可以從 commit 精確地連回去**。

Spinellis 在 2003 年的《Code Reading》第 8 章談文件時，把 mailing list 封存視為「非正式文件」來源之一，但當時要從程式碼找到對應的討論，靠的是運氣和搜尋引擎。今天 kernel 的 `Link:` trailer 加上以 Message-ID 為索引的 lore，讓這條路變成確定性的。

## 先建立直覺

把一個 patch 的一生畫出來：

```
  作者                          mailing list（lore 封存）                    git
  ────                          ────────────────────────                    ───
  git format-patch ──寄出──►  [PATCH 0/2] cover letter   ◄─ Message-ID: <...-1-...>
                              ├─ [PATCH 1/2] ...           ◄─ <...-2-...>
                              │    └─ Re: reviewer 意見
                              │         └─ Re: 作者回覆
                              └─ [PATCH 2/2] ...
                                   └─ Re: Reviewed-by: ...
  修改後重寄 ─────────────►  [PATCH v2 0/2] ...
                              ...
                                                            維護者 b4 am / git am
                                                            ──────────────────────►  commit
                                                                                     Link: https://lore.kernel.org/<list>/<Message-ID>/
```

**Message-ID 是整個系統的主鍵**。每封 email 的標頭都有一個全球唯一的 `Message-ID:`；回信的 `In-Reply-To:` 指向它，形成討論樹；lore 用它當 URL；commit 的 `Link:` 也用它。所以從 commit 到討論、從討論到 commit，都是一次精確查詢，不是模糊搜尋。

## lore.kernel.org 的結構

lore 是一套 public-inbox 實例，每個 mailing list 是一個「inbox」，URL 規則：

```
https://lore.kernel.org/<list>/                         某個 list 的首頁
https://lore.kernel.org/<list>/<Message-ID>/            一封信（HTML）
https://lore.kernel.org/<list>/<Message-ID>/T/          整串討論，平鋪（HTML）
https://lore.kernel.org/<list>/<Message-ID>/t/          整串討論，巢狀（HTML）
https://lore.kernel.org/<list>/<Message-ID>/raw         原始 email
https://lore.kernel.org/<list>/<Message-ID>/t.mbox.gz   整串討論的 mbox（gzip）
https://lore.kernel.org/all/<Message-ID>/               跨所有 list 查這個 Message-ID
https://lore.kernel.org/r/<Message-ID>                  短網址轉址器
```

`<list>` 通常就是 list 地址 `@` 前面的部分：`linux-fsdevel@vger.kernel.org` → `lore.kernel.org/linux-fsdevel/`。例外是 LKML（`linux-kernel@vger.kernel.org`），它的 inbox 叫 `lkml`。

一封信通常同時寄給好幾個 list（To 一個、Cc 好幾個），所以同一個 Message-ID 會出現在多個 inbox 裡。實測：Sandeep Patil 的那封 patch，用 `linux-fsdevel` 和 `all` 都抓得到：

```
$ id=20210729222635.2937453-1-sspatil@android.com
$ curl -sS -o /dev/null -w '%{http_code} %{size_download}\n' https://lore.kernel.org/linux-fsdevel/$id/t.mbox.gz
200 18785
```

`Link:` trailer 的寫法有好幾代，從 `fs/pipe.c` 全部歷史的 `Link:` 統計就看得出來：

```
$ git log --format='%(trailers:key=Link,valueonly)' -- fs/pipe.c | grep -oE 'https?://[^/]+/' | sort | uniq -c | sort -rn
     31 https://lore.kernel.org/
     21 http://lkml.kernel.org/
      9 https://lkml.kernel.org/
      2 https://github.com/
      1 https://bugzilla.kernel.org/
```

而整個 kernel 在 v6.18 之前半年（2025-06-01 起）的 `Link:`：

```
  12098 https://lore.kernel.org/
   7881 https://patch.msgid.link/
   1471 https://lkml.kernel.org/
    252 https://github.com/
     87 https://patchwork.freedesktop.org/
     32 https://bugzilla.kernel.org/
```

- `lkml.kernel.org/r/<msgid>`：舊式轉址器，Andrew Morton 的工具至今仍產生這種格式（Ch 37 的 `f485922d8fe4` 就是）。
- `patch.msgid.link/<msgid>`：較新的格式，b4 套用 patch 時自動加上的。實測它就是一個轉到 lore 的 301：
  ```
  $ curl -sS -o /dev/null -w '%{http_code} %{redirect_url}\n' https://patch.msgid.link/$id
  301 https://lore.kernel.org/20210729222635.2937453-1-sspatil@android.com
  ```

不管哪種格式，**取出最後那段 Message-ID**，就能套進上面任何一個 lore URL。

## 從 commit 到討論：實際走一次

延續 Ch 37。四個 commit 的 `Link:`：

```
3a34b13a88ca  Link: https://lore.kernel.org/lkml/20210729222635.2937453-1-sspatil@android.com/
3b844826b6c6  Link: https://lore.kernel.org/lkml/20210802024945.GA8372@xsang-OptiPlex-9020/
fe67f4dd8daa  Link: https://lore.kernel.org/lkml/20210824151337.GC27667@xsang-OptiPlex-9020/
f485922d8fe4  Link: https://lkml.kernel.org/r/20220322002653.33865-1-kuniyu@amazon.co.jp
```

把四串討論的 mbox 抓下來（存在 `~/code_reading/scratch/38/`）：

```
$ for id in 20210802024945.GA8372@xsang-OptiPlex-9020 20210729222635.2937453-1-sspatil@android.com \
            20210824151337.GC27667@xsang-OptiPlex-9020 20220322002653.33865-1-kuniyu@amazon.co.jp; do
    curl -sS -o "$id.mbox.gz" -w "%{http_code} %{size_download} $id\n" \
         "https://lore.kernel.org/all/$id/t.mbox.gz"
  done
200 155079 20210802024945.GA8372@xsang-OptiPlex-9020
200 18785 20210729222635.2937453-1-sspatil@android.com
200 269110 20210824151337.GC27667@xsang-OptiPlex-9020
200 6929 20220322002653.33865-1-kuniyu@amazon.co.jp
$ for f in *.gz; do gunzip -kf "$f"; done
$ for f in *.mbox; do echo "$f: $(grep -c '^From ' "$f") msgs"; done
20210729222635.2937453-1-sspatil@android.com.mbox: 11 msgs
20210802024945.GA8372@xsang-OptiPlex-9020.mbox: 14 msgs
20210824151337.GC27667@xsang-OptiPlex-9020.mbox: 18 msgs
20220322002653.33865-1-kuniyu@amazon.co.jp.mbox: 4 msgs
```

一個失敗案例值得記下：第一次嘗試時我們給 curl 加了瀏覽器的 User-Agent，四個檔案都只有 7.6 KB，`gunzip` 回報 `not in gzip format`——拿到的是給瀏覽器看的防護頁面，不是 mbox。改回 curl 預設的 User-Agent 才成功。**下載後先確認檔案大小和格式**，不要假設 HTTP 200 就是你要的內容。

mbox 是純文字，可以直接 `less`。要逐封列出標頭與內文，寫個十幾行的 Python（標準函式庫 `mailbox` 就夠）：

```python
# /tmp/show.py：列出 mbox 中每封信（依 Message-ID 去重）
import mailbox, sys
mb = mailbox.mbox(sys.argv[1]); want = sys.argv[2:]
seen = set()
for m in mb:
    mid = (m['Message-ID'] or m['Message-Id'] or '').strip()
    if mid in seen: continue
    seen.add(mid)
    if want and not any(w in mid for w in want): continue
    body = m.get_payload(decode=True)
    if body is None:
        body = b''.join(p.get_payload(decode=True) or b''
                        for p in m.walk() if p.get_content_type() == 'text/plain')
    print('=====', m['From'], '|', m['Date'], '|', mid); print(m['Subject'])
    print(body.decode('utf-8', 'replace')[:4000])
```

**為什麼要去重**：`/all/` 的 mbox 會把同一封信在不同 inbox 的副本都收進來。hackbench 那串「14 msgs」實際上只有 7 封不同的信，每封都出現兩次（分別來自 `lkml` 和另一個 list 的封存）。

### 討論串一：Android 的回報（→ 3a34b13a88ca）

Sandeep Patil 寄出的是一個兩封信的 series。`[PATCH 0/1]` 是 **cover letter**：

```
From:   Sandeep Patil <sspatil@android.com>
Subject: [PATCH 0/1] Revert change in pipe reader wakeup behavior
Date:   Thu, 29 Jul 2021 22:26:34 +0000

The commit <1b6b26ae7053>("pipe: fix and clarify pipe write wakeup
logic") changed pipe write logic to wakeup readers only if the pipe
was empty at the time of write. ...

One such library 'realm-core'[2] is used by numerous Android applications.
The library uses a similar notification mechanism as GNU Make but it
never drains the pipe until it is full. When Android moved to v5.10
kernel, all applications using this library stopped working.
The C program at the end of this email mimics the library code.
...
I do think the program below is using EPOLLET wrong. However, it
used to work before and now it doesn't. So, I thought it is
worth asking if this counts as userspace break.
```

cover letter 後面附了一個完整的 C 重現程式（`epoll-test-fifo`，用 `EPOLLIN | EPOLLET` 監看一個 FIFO）。**這個重現程式在 git 歷史裡完全找不到**，它只存在於 mailing list 上。想實際驗證「EPOLLET 被誤用」是什麼意思，這是最好的起點。

`[PATCH 1/1]` 是 Sandeep 的修法：引入一個 `do_wakeup` 變數，在每次真正寫入資料後設為 true。這個 patch **沒有被合併**。Linus 的第一封回信：

```
From:   Linus Torvalds <torvalds@linux-foundation.org>
Date:   Thu, 29 Jul 2021 16:01:07 -0700

Ah-hahh.

I've had this slightly smaller patch waiting for the better part of a year:

  https://lore.kernel.org/lkml/CAHk-=wgjR7Nd4CyDoi3SH9kPJp_Td9S-hhFJZMqvp6GS1Ww8eg@mail.gmail.com/

waiting to see if some broken program actually depends on the bogus
epollet semantics.

Can you verify that that patch fixes the realm-core brokenness too?
```

原來 Linus 早就知道這個行為改變（Michael Kerrisk 在 2020 年就寫了測試指出來），手上有 patch，只是在等「真的有程式壞掉」的證據。Sandeep 測試後回報可行，接下來的一封信解釋了 kernel 的 regression 哲學，也是整串討論裡最有教學價值的一段：

```
From:   Linus Torvalds <torvalds@linux-foundation.org>
Date:   Fri, 30 Jul 2021 15:06:00 -0700

The thing about level triggered is that if the condition was already
true, it would not need a wakeup in the first place.
...
This is literally an epoll() confusion about what an "edge" is.

An edge is not "somebody wrote more data". An edge is "there was no
data, now there is data".

And a level triggered event is *also* not "somebody wrote more data".
A level-triggered signal is simply "there is data".
...
But we have the policy that regressions aren't about documentation or
even sane behavior.

Regressions are about whether a user application broke in a noticeable way.
```

這段話回答了程式碼註解「Epoll nonsensically wants a wakeup」的「nonsensically」指的是什麼——commit message 只說了「misused the EPOLL interface」，這裡才把 edge 與 level 的定義講清楚。

接著兩封短信揭露了兩個 git 歷史裡看不到的細節：

```
Date:   Fri, 30 Jul 2021 15:53:34 -0700
Ok, I ended up committing the minimal possible change (and fixing up
the comment above it).
...
End result: the commit message is a lot bigger than the patch, with
most of it being trying to explain the background.

I've pushed it out as commit 3a34b13a88ca ("pipe: make pipe writes
always wake up readers"). Holler if you notice anything odd remaining.

Date:   Fri, 30 Jul 2021 15:55:17 -0700
.. and as I wrote that, I realized that I had forgotten to mark it for
stable even though that was the intent.
```

- 被合併的是 Linus 自己的 patch，不是回報者的；Linus 前一封信還說過「I also have a slight preference towards your patch simply because you did the work of finding this out」，最後仍選了最小改動。
- 它本來就打算 backport 到 stable，只是 commit 裡忘了加 `Cc: stable`。這就是為什麼 `3a34b13a88ca` 沒有 `Cc: stable@vger.kernel.org` trailer，卻還是被 backport 了——Greg Kroah-Hartman 在下一封信回了「Will do so, thanks!」。**只看 git 的人會得出錯誤結論。**

### 討論串二：kernel test robot 的效能回報（→ 3b844826b6c6）

開頭是 Intel 的 kernel test robot（0-day／LKP）自動寄出的報告：

```
From: kernel test robot <oliver.sang@intel.com>
Subject: [pipe] 3a34b13a88: hackbench.throughput -12.6% regression
Date: Mon, 02 Aug 2021 10:49:45 +0800

FYI, we noticed a -12.6% regression of hackbench.throughput due to commit:

commit: 3a34b13a88caeb2800ab44a4918f230041b37dd9 ("pipe: make pipe writes always wake up readers")
...
on test machine: 96 threads 2 sockets Ice Lake with 256G memory
...
If you fix the issue, kindly add following tag
Reported-by: kernel test robot <oliver.sang@intel.com>
```

最後一行解釋了 Ch 37 看到的 `Reported-by: kernel test robot` 從哪裡來——robot 直接告訴修正者要加什麼 trailer。報告裡還有完整的效能計數器對照表（`hackbench.time.involuntary_context_switches` +23.6%、`turbostat.IRQ` +55.4%……），這些數字也只存在於信裡。

Linus 的第一反應和最後的 commit 方向不同：

```
Date: Mon, 02 Aug 2021 10:06:59 -0700
I had already forgotten how sensitive hackbench is to pipe wakeups.

I think it's all good for stable, and we can live with this -
particularly since I'm not sure how much hackbench really matters.

But it might be one of those things where it is a good idea to make
the crazy epoll case explicitly special.

Sandeep, does something like the attached patch (written to be on top
of the existing one) work for you?
```

七分鐘後他又寄了一封：「.. and by "attached patch" I obviously meant the patch that wasn't attached. It's here.」——忘了附件這件事很人性，但它也提醒我們：**patch 本體可能在後續的信裡**，抓 mbox 要抓整串，不要只抓 Link 指向的那一封。

真正讓 patch 被合併的是兩週後 Mel Gorman（SUSE）的回覆，附了跨多種機器的數據：

```
From: Mel Gorman <mgorman@techsingularity.net>
Date: Wed, 18 Aug 2021 16:31:55 +0100

Our own test systems also detected the regression and automatically
bisected to 3a34b13a88 but it is not universal. For example, it's not
visible on a single socket 8-cpu skylake machine but it gets progressively
worse the more CPUs there are and potentially the more LLCs there are.
...
hackbench-process-pipes
                          5.14.0-rc3             5.14.0-rc3             5.14.0-rc3
                             vanilla        alwayswake-v1r1       specialcase-v1r1
Amean     1        0.3800 (   0.00%)      0.4520 * -18.95%*      0.3580 (   5.79%)
...
Amean     30       0.9667 (   0.00%)      1.8530 * -91.69%*      0.8547 (  11.59%)
...
I know hackbench is not particularly interesting but it's used often
enough when comparing kernels and patches that commit 3a34b13a88 will be
a regression magnet.
```

Linus 當天回覆：「Fair enough. And I like that patch anyway because of how it makes that EPOLLET behaviour much more explicit, so I just committed it.」

把這個時間點對回 git：`3b844826b6c6` 的 author date 是 2021-08-05，committer date 是 **2021-08-18 11:39:46 -0700**；Linus 回信說「I just committed it」的時間是 08-18 11:17:59 -0700。兩者差了約二十分鐘，committer date 反而比「已經 commit 了」的信晚——可能是之後又 amend 或 rebase 過，這點從現有資料無法確認。commit message 裡的「as Mel points out」指的就是這封信。這個「author date 早、committer date 晚」的落差，Ch 37 踩雷集錦提過，這裡看到了它的成因：patch 寫好後在信裡放了兩週，等到有說服力的數據才合併。

### 討論串三：stress-ng（→ fe67f4dd8daa）

這串的開頭是另一份 robot 報告：`[pipe] 3b844826b6: stress-ng.sigio.ops_per_sec -99.3% regression`。Linus 的第一封回信是讀碼者最該學的態度：

```
Date: Tue, 24 Aug 2021 09:22:25 -0700
Well, that's bad.

You fix one benchmark, you break another..

What's a bit odd is that this commit basically reverts commit
3a34b13a88ca ("pipe: make pipe writes always wake up readers") which
did *not* result in any kernel test robot report.
...
I strace'd the "stress-ng --sigio" case just to make sure I didn't
miss anything.

But I'm clearly missing something. Can anybody see what I'm missing,
and hit me over the head with the clue-bat?
```

一小時後他自己找到答案（SIGIO 和 wakeup 綁在同一個 `if` 裡），10:39:25 寄出「The patch would be something like the attached (UNTESTED!)」。對照 git：

```
$ git log -1 --format='%ad' fe67f4dd8daa2
Tue Aug 24 10:39:25 2021 -0700
```

**commit 的 author date 和那封信的 Date 完全相同，精確到秒**。這是 `git am` 的行為：它把 email 的 `Date:` 當作 author date。所以一個 commit 的 author date 常常可以直接拿來在 lore 上定位原始的那封信。

stress-ng 的作者 Colin Ian King 在同一串回信承認測試的假設太天真（「that's basically the underlying assumption I made with this rather naive test」），並修改了 stress-ng——commit message 裡「Colin King already fixed the stress-ng code in question」的出處就是這裡。

### 討論串四：KCSAN 與一個沒被回答的 review（→ f485922d8fe4）

這是一個標準的 patch series：

```
Tue, 22 Mar 2022 09:26:51 +0900 | Kuniyuki Iwashima | [PATCH 0/2] Fix data-races around epoll reported by KCSAN.
Tue, 22 Mar 2022 09:26:52 +0900 | Kuniyuki Iwashima | [PATCH 1/2] pipe: Make poll_usage boolean and annotate its access.
Tue, 22 Mar 2022 09:26:53 +0900 | Kuniyuki Iwashima | [PATCH 2/2] list: Fix a data-race around ep->rdllist.
Tue, 29 Mar 2022 19:03:20 +0200 | Marco Elver      | Re: [PATCH 1/2] pipe: Make poll_usage boolean and annotate its access.
```

Message-ID 的 `-1-`、`-2-`、`-3-` 就是 `git format-patch` / `git send-email` 產生的序號：cover letter 是 `-1-`，第 N 個 patch 是 `-(N+1)-`。

把信裡的 patch 和最後的 commit 對照，能看到三個 git 歷史不會告訴你的差異：

1. **標題被改過**：信裡是「pipe: **M**ake poll_usage boolean and annotate its access**.**」，commit 是「pipe: **m**ake poll_usage boolean and annotate its access」。大寫改小寫、去掉句號——Andrew Morton 套用時順手修正成 kernel 慣用的標題格式。
2. **`---` 之後的附註消失了**。信裡在 `Signed-off-by` 下面的 `---` 之後寫著：
   ```
   Note that the message is false positive for now, so the Fixes tag might not
   be necessary.
   ```
   作者自己都懷疑 `Fixes:` 該不該加，但這句話不會進 git，commit 裡的 `Fixes:` 看起來和其他的一樣權威。
3. **cover letter 的內容被併進第一個 patch**：commit message 開頭的「Patch series "Fix data-races around epoll reported by KCSAN." This series suppresses a false positive...」就是 `[PATCH 0/2]` 的內文，Andrew 的工具把它貼了進去。其他維護者多半不這樣做，cover letter 只留在 list 上。

第四封是 KCSAN 的作者 Marco Elver 的 review：

```
From: Marco Elver <elver@google.com>
Date: Tue, 29 Mar 2022 19:03:20 +0200

This reminds me of [1] (look for "idempotent write").

I'm not sure what KCSAN config you're using, but it looks like it's not
a default config (you seem to have KCSAN_REPORT_VALUE_CHANGE_ONLY off).
My guess is you can't see this data race with the default config, which
was a choice made from discussion in [1].

[1] https://lore.kernel.org/linux-fsdevel/CAHk-=wh_-1pj0vsAHiHf_FVardKkN7AZGX73QwGpViMyF7_mvQ@mail.gmail.com/T/
...
Also see https://lwn.net/Articles/816854/
```

意思是：寫入同一個值（idempotent write）的競爭，在 KCSAN 預設設定下本來就不會回報，這是刻意的設計。這個 patch 嚴格來說是在處理一個非預設設定才看得到的警告。這串討論在 lore 上沒有後續回覆，patch 一個多月後仍經由 Andrew 的 tree 進了 v5.19。

**讀碼結論**：v6.18 的 `WRITE_ONCE(pipe->poll_usage, true);` 不代表那裡有真正的並行 bug，它是一個為了讓工具安靜的標註，而且當時就有工具作者質疑它的必要性。只看程式碼，你會以為這裡有一個需要小心的 race。

## Cover letter：最好的設計文件

上面四串裡，只有 Sandeep 和 Kuniyuki 的是有 cover letter 的 series，但它們展示了 cover letter 的價值：

| 資訊 | commit message | cover letter |
|---|---|---|
| 這個 patch 改了什麼、為什麼 | 有 | 有（整個 series 的層級） |
| 多個 patch 之間的關係、為什麼這樣拆 | 沒有 | **有** |
| 重現程式、測試方法 | 有時有 | **常常有**（Sandeep 附了完整 C 程式） |
| 作者的疑慮（「我不確定這算不算 regression」） | 幾乎沒有 | **常常有** |
| v1→v2→v3 的變更紀錄（changelog） | 沒有（慣例上放 `---` 之後） | **有** |
| 被否決的替代方案 | 偶爾 | 常常有 |

大型功能（一個新的子系統、一個大型重構）的 cover letter 往往就是那個功能最好的設計文件，比 `Documentation/` 寫得早、寫得誠實。讀一個子系統的核心設計時，找出引入它的那個 series 的 `[PATCH 0/N]`，是投資報酬率最高的一步。

### v1 → vN 的演進

一個 series 被 review 後重寄，標題會變成 `[PATCH v2 0/N]`、`[PATCH v3 0/N]`，cover letter 裡通常有：

```
Changes in v3:
- Rename foo_bar() to foo_baz() as suggested by <reviewer>
- Drop patch 4, it's no longer needed after ...
Changes in v2:
- ...
```

並附上前一版的 lore 連結。考古時沿著這些連結往回讀，就能看到 reviewer 要求了什麼、作者接受了什麼、拒絕了什麼。

本章的 pipe 故事沒有多版本的 series（Sandeep 的 v1 直接被 Linus 自己的 patch 取代，其他都是單發），所以這裡**沒有實際示範 v1→vN 的比對**。工具面上，b4 可以用 `b4 diff <msgid>` 比較同一個 series 的不同版本（內部用的是 Ch 37 提過的 `git range-diff`），本章未實測。找一個有 `[PATCH v3` 字樣的 series 練習是本章的動手練習之一。

## b4：把 mailing list 變成 git 操作

b4 是 kernel.org 的維護者 Konstantin Ryabitsev 寫的工具，專門處理「從 lore 抓 patch」這件事。維護者用它套用 patch（`patch.msgid.link` 那種 `Link:` 就是它產生的），讀碼者用它抓討論。

### 安裝（無 sudo、無 pip 的環境）

這個 WSL 環境的 Python 沒有 pip，`python3 -m ensurepip --user` 也失敗（Debian／Ubuntu 的系統 Python 把 ensurepip 拿掉了）。用 pip 官方的 bootstrap 腳本裝到使用者目錄：

```
$ mkdir -p ~/code_reading/scratch/38/getpip && cd ~/code_reading/scratch/38/getpip
$ curl -sS -o get-pip.py https://bootstrap.pypa.io/get-pip.py
$ cd ~ && python3 ~/code_reading/scratch/38/getpip/get-pip.py --user
Successfully installed packaging-26.3 pip-26.3 setuptools-84.2.1 wheel-0.48.0
$ ~/.local/bin/pip install --user b4
Successfully installed b4-0.15.2 certifi-2026.11.20 cffi-2.1.1 charset_normalizer-3.5.2 dkimpy-1.1.8 dnspython-2.8.0 git-filter-repo-2.47.0 idna-3.20 patatt-0.8.0 pycparser-3.11 pynacl-1.6.2 requests-2.34.2 urllib3-2.8.0
$ ~/.local/bin/b4 --version
0.15.2
```

一個小失敗：第一次我們用 `python3 -I get-pip.py --user`（`-I` 是隔離模式，避免載入目前目錄的模組），結果報錯 `Can not perform a '--user' install. User site-packages are disabled for this Python.`——隔離模式同時關掉了使用者 site-packages。改成在乾淨的家目錄執行、不加 `-I` 才成功。

### b4 mbox：抓整串討論

```
$ b4 mbox 20210729222635.2937453-1-sspatil@android.com
Looking up https://lore.kernel.org/all/20210729222635.2937453-1-sspatil@android.com/
Grabbing thread from lore.kernel.org/all/20210729222635.2937453-1-sspatil@android.com/t.mbox.gz
11 messages in the thread
Saved ./20210729222635.2937453-1-sspatil@android.com.mbx
```

它做的事和我們手動 curl `t.mbox.gz` 一樣，但會自己處理 URL、解壓縮、命名。得到的 `.mbx` 可以用 `mutt -f`、`neomutt -f` 這類郵件程式開來讀，討論樹會正確顯示。

### b4 am：抓 patch series 並整理 trailer

```
$ b4 am -o am2 20220322002653.33865-1-kuniyu@amazon.co.jp
Looking up https://lore.kernel.org/all/20220322002653.33865-1-kuniyu@amazon.co.jp/
Analyzing 4 messages in the thread
Analyzing 2365 code-review messages
Checking attestation on all messages, may take a moment...
---
  ✗ [PATCH 1/2] pipe: Make poll_usage boolean and annotate its access.
  ✗ [PATCH 2/2] list: Fix a data-race around ep->rdllist.
    + Tested-by: Shuah Khan <skhan@linuxfoundation.org> (✓ DKIM/linuxfoundation.org)
    + Tested-by: Sudip Mukherjee <sudip.mukherjee@codethink.co.uk> (✗ DKIM/gmail.com)
    ...
    + Tested-by: Justin M. Forbes <jforbes@fedoraproject.org> (✓ DKIM/linuxtx.org)
  ---
  ✗ BADSIG: DKIM/amazon.co.jp
---
Total patches: 2
---
...
Cover: ./20220322_kuniyu_fix_data_races_around_epoll_reported_by_kcsan.cover
 Link: https://patch.msgid.link/20220322002653.33865-1-kuniyu@amazon.co.jp
       git am ./20220322_kuniyu_fix_data_races_around_epoll_reported_by_kcsan.mbx
```

產出三樣東西：可以直接 `git am` 的 `.mbx`（patch 本體，trailer 已整理進去）、`.cover`（cover letter），以及建議加上的 `Link:`。

這份輸出有兩個地方要小心讀，它們本身就是考古的教訓：

1. **那十個 `Tested-by:` 不是原始 review 給的**。原始討論串只有 4 封信，沒有任何人回 Tested-by。輸出第三行「Analyzing 2365 code-review messages」顯示 b4 0.15 還去找了「其他討論串裡、內容相同的 patch」的回覆。patch 2/2 後來被 backport 到 stable，stable 每次發 `-rc` 都會有一大串測試者回覆整批 patch 的 `Tested-by`——我們**推測**這十個 trailer 就是從那裡來的（名單裡的 Shuah Khan、Guenter Roeck、Jiri Slaby 等都是 stable `-rc` 的常態測試者），但沒有逐一追到來源信件。對考古者的意義是：**b4 整理出來的 trailer 不等於原始 review 的歷史**，要知道當時誰 review 了什麼，回去讀原始討論串。
2. **`✗ BADSIG: DKIM/amazon.co.jp`**：b4 會驗證信件的 DKIM 簽章（寄件網域的密碼學簽章）。2022 年的信在 2026 年驗證失敗，可能是因為該網域早已輪換金鑰、舊的公鑰從 DNS 上移除了——這是推測，未驗證。對讀碼來說它不影響內容，但提醒你：信件的真實性驗證有時效性。

## lore 的搜尋語法（本節未實測）

lore 的搜尋框支援 public-inbox 的查詢前綴。以下依 public-inbox 的文件與常見用法整理，**在本環境搜尋頁回 403，未能實測**，請在瀏覽器上自行驗證：

| 前綴 | 意義 | 例子 |
|---|---|---|
| `s:` | 標題 | `s:"pipe: make pipe writes"` |
| `f:` | 寄件者 | `f:torvalds` |
| `b:` | 內文 | `b:EPOLLET` |
| `d:` | 日期範圍 | `d:2021-07-01..2021-09-01` |
| `dfn:` | patch 動到的檔名 | `dfn:fs/pipe.c` |
| `nq:` | 非引用的內文（排除 `>` 開頭的引用） | `nq:poll_usage` |
| `m:` | Message-ID | `m:20210729222635.2937453-1-sspatil@android.com` |

組合起來，例如在 `linux-fsdevel` 找 2021 年夏天所有動到 `fs/pipe.c` 的 patch：`dfn:fs/pipe.c d:2021-07-01..2021-09-01`。搜尋結果頁通常也提供 Atom feed 與 mbox 下載。

沒有 `Link:` 的老 commit（2010 年代中期以前很常見），就要靠這種搜尋：用 commit 標題配 `s:`、作者配 `f:`、author date 前後幾天配 `d:`。前面看到的「author date 等於 email Date」在這裡很好用。

## 對比與取捨

| 取得討論的方式 | 優點 | 缺點 |
|---|---|---|
| 瀏覽器開 `Link:` | 最直覺，HTML 有討論樹 | 只看一封時容易漏掉後續（例如「忘了附件」那封） |
| curl `.../t.mbox.gz` | 可腳本化、離線讀、可 grep | 要自己去重、自己解析 MIME |
| `b4 mbox` | 同上但省事 | 要裝工具 |
| `b4 am` | 直接得到可套用的 patch 與 cover | trailer 可能混入非原始 review 的來源 |
| lore 搜尋 | 沒有 `Link:` 時唯一的路 | 模糊；本環境被擋 |
| 只讀 commit message | 最快 | 看不到被否決的方案、reviewer 意見、`---` 後的附註 |

## 踩雷集錦

1. **「commit message 就是完整的故事」**：`3a34b13a88ca` 少了「本來就想 backport 只是忘了標」；`f485922d8fe4` 少了「作者自己懷疑 Fixes 該不該加」和「KCSAN 作者認為預設設定看不到」。commit message 是結論，討論串才是過程。
2. **「Link: 指向的那封信就是全部」**：Linus「忘了附件」的 patch 在七分鐘後的另一封信裡。永遠抓整串（`t.mbox.gz` 或 `/T/`）。
3. **「HTTP 200 就是成功」**：加了瀏覽器 User-Agent 的 curl 拿到 200，內容卻是防護頁面。下載後檢查大小與格式。
4. **「`/all/` 的 mbox 裡每封信都不同」**：跨 list 的副本會重複，hackbench 那串 14 封其實是 7 封。統計「討論有多熱烈」之前先依 Message-ID 去重。
5. **「b4 列出的 Tested-by 都是當時的 review」**：b4 0.15 會從其他討論串（例如 stable review）蒐集 trailer。要還原歷史，讀原始討論串。
6. **「被合併的就是回報者寄的 patch」**：Sandeep 的 patch 沒被合併，Linus 用了自己一年前寫好的版本。`Reported-by:` 和 author 不同人時，去讀討論串，看看被否決的那個版本長什麼樣——它常常解釋了「為什麼不這樣做」。

## 進階：再往深一層

- **lei（local email interface）**：public-inbox 附帶的命令列工具，可以把 lore 的搜尋結果同步到本機 maildir，持續追蹤某個子系統的 list，例如「所有動到 `fs/pipe.c` 的 patch」。適合長期研究一個子系統。本章未安裝。
- **抓整個 list 的 git 封存**：lore 的每個 inbox 本身也是 git repo（public-inbox 的儲存格式），可以 `git clone` 下來離線 grep。LKML 的封存非常大，適合只抓特定 list。
- **patchwork**：部分子系統（網路、DRM 等）用 patchwork 追蹤每個 patch 的狀態（New／Accepted／Rejected／Superseded）。前面的 `Link:` 統計裡有 87 個 `patchwork.freedesktop.org`。MAINTAINERS 的 `Q:` 欄位指向該子系統的 patchwork。
- **commit 與 email 的對應**：b4 套用的 commit 會帶 `Link: https://patch.msgid.link/<msgid>`；沒有的話，用 author date、標題、作者三者在 lore 上定位。反方向——從一封 patch email 找它最後變成哪個 commit——用 `git log --grep='<Message-ID 的一部分>'`，例如 `git log --oneline --grep='20210729222635.2937453-1-sspatil'` 會找到 `3a34b13a88ca`（因為它的 `Link:` 指向那封信）。
- **LWN 的報導**：重要的討論串 LWN 常常會寫成文章（Marco Elver 那封信裡就引了 LWN 816854）。讀很長的設計爭論時，先找有沒有 LWN 的摘要。

## 動手練習

1. 用 `curl .../t.mbox.gz` 或 `b4 mbox` 抓下 Sandeep 那串討論，把 cover letter 裡的 C 重現程式存成檔案，在你的 Linux 上編譯執行。它在 v6.18 上的行為是「正常結束」還是「卡住直到 timeout」？用 Ch 37 的時間線解釋為什麼。（注意：結果取決於你機器的 kernel 版本。）
2. Linus 第一封回信裡的連結 `CAHk-=wgjR7Nd4CyDoi3SH9kPJp_Td9S-hhFJZMqvp6GS1Ww8eg@mail.gmail.com` 指向他「放了快一年」的 patch。抓下那串討論，找出 Michael Kerrisk 當初的測試是怎麼發現這個行為改變的。
3. 在 `git log --oneline -- fs/pipe.c` 裡找一個有 `[PATCH v2` 以上版本的 series（提示：用 `git log --format='%h %(trailers:key=Link,valueonly)' -- fs/pipe.c` 拿到 Message-ID，再抓 mbox 看 cover letter 有沒有「Changes in v2」）。比較 v1 和最終版本的差異，以及 reviewer 要求了什麼。
4. 對 [Ch 37](./37-git-archaeology.md) 動手練習 1 找到的 `c73be61cede5`（watch queue），用 `git log -1 --format=%B` 找它的 `Link:`；如果沒有，用標題和 author date 在 lore 上搜尋（瀏覽器）。它的 cover letter 是否解釋了為什麼通知要「借用」pipe？

## 本章重點整理

- kernel 的每個 patch 都是一封公開的 email；Message-ID 是主鍵，lore.kernel.org 用它當 URL，commit 的 `Link:` 也用它，所以 commit 和討論之間是精確對應。
- `https://lore.kernel.org/<list>/<msgid>/t.mbox.gz` 抓整串討論；`/all/` 跨 list，`lkml.kernel.org/r/` 與 `patch.msgid.link/` 是轉址器。
- 討論串裡有 commit 沒有的東西：被否決的 patch、重現程式、效能數據、作者的疑慮、`---` 之後的附註、reviewer 的質疑。
- cover letter 是一個 series 最好的設計文件；v2、v3 的 changelog 記錄了 review 如何改變設計。
- b4 把 lore 變成 git 操作（`b4 mbox`、`b4 am`），但它整理的 trailer 可能來自其他討論串；要還原歷史，讀原始信件。
- `git am` 把 email 的 Date 當 author date，所以 author date 可以用來在 lore 上定位原始信件。

## 自我檢核

- [ ] 不看筆記，能不能說出從一個 commit hash 到讀到它的原始討論串，需要哪幾個步驟、各用什麼工具？
- [ ] 有人說「`3a34b13a88ca` 沒有 `Cc: stable`，所以它沒有被 backport」，你會怎麼反駁？證據在哪？
- [ ] 能說出至少三種「只存在於 mailing list、不會進 git」的資訊，並各舉本章一個例子嗎？
- [ ] `b4 am` 列出了一堆 `Tested-by`，你怎麼確認它們是不是原始 review 給的？
- [ ] 想一個你用過的開源專案（不一定是 kernel）：它的設計討論在哪裡？GitHub issue、PR review、mailing list、Discord？如果你要對它做本章這種考古，路徑會是什麼，哪裡會斷？

## 延伸閱讀

### 官方文件

- **`Documentation/process/submitting-patches.rst`**（[docs.kernel.org/process/submitting-patches.html](https://docs.kernel.org/process/submitting-patches.html)）
  - **讀哪裡**：第 116 行附近談 `Link:` 的段落（「use the contents of the ``Message-ID`` header of the message without the surrounding angle brackets」）；第 325 行附近「When sending a next version, add a ``patch changelog`` to the cover letter」；第 638 行起「The canonical patch format」一節（解釋 `---` 之後的內容為什麼不進 git）
  - **學到什麼**：本章 Message-ID → URL 規則與 `---` 附註行為的權威來源
  - **前提**：讀完本章
- **`Documentation/process/5.Posting.rst`**（[docs.kernel.org/process/5.Posting.html](https://docs.kernel.org/process/5.Posting.html)）
  - **讀哪裡**：第 80 行起講「把變更拆成 logical series」的段落（每個 patch 都要能獨立編譯、為什麼不要一次丟出整個新架構）
  - **學到什麼**：從寫的人那一端理解一個 series 為什麼這樣拆；讀 series 時，拆分方式本身就透露了作者的設計思路
  - **前提**：無
- **`Documentation/process/maintainer-tip.rst`**
  - **讀哪裡**：第 392 行附近談 `Link:` trailer 的段落：套用 patch 時用 `patch.msgid.link` 而不是 `lore.kernel.org`，好讓自動化工具分辨「原始 patch」和「相關討論」
  - **學到什麼**：解釋了本章 `Link:` 統計裡 `patch.msgid.link` 為什麼這麼多，以及兩種網域的語意差別

### 工具文件

- **b4 官方文件**（[b4.docs.kernel.org](https://b4.docs.kernel.org/)）
  - **讀哪裡**：「Maintainer overview」裡 `b4 am`、`b4 mbox`、`b4 diff` 的說明
  - **學到什麼**：本章只用了 b4 的讀取功能；`b4 diff` 比對 series 版本、`b4 shazam` 直接套用到工作分支，都是考古時好用的功能
  - **前提**：會用 `git am`
- **lore.kernel.org 首頁與各 inbox 頁面底部的說明**（[lore.kernel.org](https://lore.kernel.org/)）
  - **讀哪裡**：搜尋框旁的「help」連結，列出所有搜尋前綴
  - **學到什麼**：本章搜尋語法一節未能實測的部分，以這裡的說明為準

### 技術文章

- **LWN.net 上關於 lore、b4 與 kernel 開發流程工具的報導**
  - **讀哪裡**：在 [LWN Kernel Index](https://lwn.net/Kernel/Index/) 的 "Development tools" 分類找 b4、public-inbox 相關文章
  - **為什麼值得讀**：解釋了 kernel 為什麼堅持 email 工作流、lore 如何取代舊的 LKML 封存網站，是本章工具背後的脈絡
- **[LWN：Marco Elver 信中引用的 KCSAN 文章（lwn.net/Articles/816854）](https://lwn.net/Articles/816854/)**
  - **這篇說什麼**：KCSAN 在 kernel 裡的使用與設計取捨（Marco Elver 在討論串四裡把它當作「為什麼預設不回報 idempotent write」的參考）
  - **前提**：讀過 [Ch 21](./21-read-once-and-barriers.md) 的 `READ_ONCE`／`WRITE_ONCE`

git 和 mailing list 告訴你一段程式碼的**歷史**；下一章轉向它的**現在式說明**：Documentation、kernel-doc 註解與 coding style——那些作者寫給未來讀者的東西，以及它們什麼時候和程式碼不一致。

→ [Ch 39 文件與風格：Documentation、kernel-doc、coding style](./39-documentation-and-style.md)
