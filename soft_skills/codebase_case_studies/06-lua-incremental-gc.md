# Ch 6 — Lua 的增量 GC：三色標記與 write barrier

> **目標**：本課第一個難章，慢慢帶。讀 `lgc.c` 的增量三色標記-清除 GC：為什麼要「增量」（把一次大回收切成很多小步，避免 stop-the-world 卡頓）、三色不變式怎麼保證正確、write barrier（`luaC_barrier`）為什麼是這套機制的命脈、以及 GC 的狀態機（`GCS*` 各階段）怎麼推進。這是動態語言 runtime 最硬也最漂亮的一塊，讀懂它你就懂了所有增量 GC 的骨架。

> **目標codebase**：Lua `v5.4.7`（commit `1ab3208`）

## 為什麼需要這個？

Ch 5 說字串、table、closure 是 heap 物件。誰回收它們？GC。但「回收沒人用的物件」聽起來簡單，做起來是動態語言 runtime 最難的部分，難在兩件事：

1. **正確性**：怎麼確定一個物件「真的沒人用」？漏判會把還在用的物件回收掉（use-after-free，災難）；誤判會留著垃圾（記憶體洩漏）。
2. **延遲**：最樸素的 GC 是「停下整個程式，掃完所有物件，回收垃圾，再繼續」——這叫 stop-the-world。物件一多，這個「停」可能是幾十毫秒到幾秒。遊戲掉幀、伺服器卡頓,都是它。

Lua 的答案是**增量 GC**:把「掃完所有物件」這件大事切成很多小步,每次只做一點點,夾在正常程式執行之間。程式跑一會、GC 走一小步、程式再跑一會。這樣沒有一次長停頓,代價是要非常小心——因為 GC 掃到一半時,程式還在改物件圖,稍不注意就掃漏。這個「一邊掃一邊改」的正確性,靠**三色不變式 + write barrier** 保證。這是本章的核心,也是所有並發/增量 GC 的共通骨架(Go、Java 的 G1/ZGC、V8 都是這套思想的變體)。

先給心理準備:這章比前三章難。第一次讀不用全懂,抓住「三色是什麼、不變式是什麼、barrier 為什麼存在」三個核心即可,細節(weak table、ephemeron、generational)可以先跳過,標「進階」的地方回頭再啃。

## 先建立直覺:三色標記

想像 heap 上所有物件是一張圖(物件互相引用)。GC 要找出「從根(全域變數、stack、registry)出發,順著引用走得到」的所有物件——這些是活的,走不到的是垃圾。

三色標記把物件分三色,代表掃描進度:

```
   白(white)  = 還沒被證明是活的（預設；掃完還是白的就是垃圾）
   灰(gray)   = 已知是活的，但它「指向誰」還沒檢查完
   黑(black)  = 已知是活的，且它指向的所有物件都已經被標記過了
```

掃描過程就是**把物件從白→灰→黑推進**:

```
   1. 起始：所有物件白。把根直接指到的物件塗灰，放進 gray 佇列。
   2. 從 gray 佇列取一個灰物件 o：
        - 看 o 指向誰，把那些白物件塗灰、丟進佇列
        - o 自己塗黑（它的引用都處理完了）
   3. 重複 2 直到 gray 佇列空。
   4. 此時還是白的物件 = 沒人從根走得到 = 垃圾，回收。
```

這叫 tri-color marking。gray 佇列空的那一刻,所有活物件都黑了、所有垃圾都白著,一刀切乾淨。

**關鍵的三色不變式(tri-color invariant)**:任何時候,**不允許黑物件直接指向白物件**。為什麼?因為黑代表「我和我指的都掃過了」,如果黑物件突然多出一個指向白物件的引用,而 GC 不會再回頭看黑物件——那個白物件就會被漏掉、被錯誤回收。守住「黑不指白」,增量 GC 才正確。

## 為什麼「增量」讓不變式變危險

在 stop-the-world GC 裡,標記期間程式完全暫停,物件圖不會變,不變式自動成立。

增量 GC 不一樣:標記做到一半,把控制權還給程式,程式跑幾條 bytecode,可能執行 `black_table[k] = white_object`——**把一個白物件塞進一個已經塗黑的 table**。這一瞬間就違反了不變式:黑(table)指向了白(object)。GC 之後不會再看那個黑 table,那個白 object 就等著被誤殺。

```
   GC 標記到一半（table 已黑，object 還白）：

        [root] ──▶ [table 黑]        [object 白]
                                          ▲
   程式執行 table[k] = object：           │
        [root] ──▶ [table 黑] ─新引用─────┘
                                     ← 違反不變式！GC 不會再看黑 table，
                                       object 保持白 → 下次 sweep 被回收
                                       但它其實還活著 → use-after-free
```

**這就是 write barrier 存在的唯一理由**:每當程式把一個引用寫進物件,GC 埋的「barrier」會攔截這次寫入,採取補救,維持不變式。write barrier 是增量 GC 的命脈——沒有它,增量 GC 一定出錯。

## write barrier:`luaC_barrier`

看 Lua 怎麼攔。barrier 的入口巨集(`lgc.h`,v5.4.7):

```c
#define luaC_barrier(L,p,v) (  \
	iscollectable(v) ? luaC_objbarrier(L,p,gcvalue(v)) : cast_void(0))
```

`p` 是被寫入的物件(那個 table),`v` 是被寫進去的值(那個 object)。若 `v` 可回收,呼叫 `luaC_objbarrier`,它再判斷(`lgc.h`,v5.4.7):

```c
#define luaC_objbarrier(L,p,o) (  \
	(isblack(p) && iswhite(o)) ? \
	luaC_barrier_(L,obj2gco(p),obj2gco(o)) : cast_void(0))
```

**只有 `p` 是黑、`o` 是白時才動作**——正是「黑指白」那個危險組合。其他組合(白指白、灰指白……)不違反不變式,barrier 什麼都不做(`cast_void(0)`,零成本)。這是 barrier 設計的精髓:**絕大多數寫入不觸發任何動作,只有真正危險的那種才付出代價**。

觸發時走 `luaC_barrier_`(`lgc.c`,v5.4.7):

```c
void luaC_barrier_ (lua_State *L, GCObject *o, GCObject *v) {
  global_State *g = G(L);
  lua_assert(isblack(o) && iswhite(v) && !isdead(g, v) && !isdead(g, o));
  if (keepinvariant(g)) {  /* must keep invariant? */
    reallymarkobject(g, v);  /* restore invariant */
    ...
  }
  else {  /* sweep phase */
    ...
    if (g->gckind == KGC_INC)  /* incremental mode? */
      makewhite(g, o);  /* mark 'o' as white to avoid other barriers */
  }
}
```

補救手段就一句:`reallymarkobject(g, v)`——**把那個白物件 `v` 立刻標記(塗灰或塗黑)**,讓它不再是白的。這叫「前進式 barrier」(forward barrier):既然黑物件現在指向它,就把它也拉進活物件集合。不變式恢復:黑物件指的不再是白物件了。

(`else` 那條是掃描階段的另一種處理,把黑物件退回白色避免重複觸發,細節先跳過。)

**Lua 還有一種反向 barrier** `luaC_barrierback`(`lgc.h`),用在 table:與其標記被寫入的值,不如把整個 table 退回灰色、重新排隊掃描。table 常被大量寫入,一個個標記值太貴,整批重掃更划算。看 `lvm.c` 裡 `OP_SETTABLE` 下游的 `luaC_barrierback` 呼叫——這就是 Ch 4/Ch 5 的 `t[k]=v` 和 GC 接上的地方:每次你在 Lua 寫 `t[k]=v`,VM 都會過一次 barrier,維持 GC 正確。

## 底層機制:GC 狀態機

增量 GC 既然是「一小步一小步」,就得有個狀態記「上次做到哪」。這個狀態在 `global_State.gcstate`(Ch 5 見過),取值是 `GCS*` 常數(`lgc.h`,v5.4.7):

```c
#define GCSpropagate	0    /* 標記傳播：一步步清空 gray 佇列 */
#define GCSenteratomic	1    /* 準備進入原子階段 */
#define GCSatomic	2        /* 原子階段：一口氣完成標記收尾（不可中斷） */
#define GCSswpallgc	3        /* 清掃一般物件 */
#define GCSswpfinobj	4    /* 清掃有 finalizer 的物件 */
#define GCSswptobefnz	5    /* 清掃待 finalize 物件 */
#define GCSswpend	6        /* 清掃收尾 */
#define GCScallfin	7        /* 呼叫 finalizer（__gc metamethod） */
#define GCSpause	8        /* 暫停：一輪結束，等下一輪 */
```

一輪 GC 就是在這些狀態間推進。核心驅動函式 `singlestep`(`lgc.c`,v5.4.7)是個大 switch,**每呼叫一次只推進一點**:

```c
static lu_mem singlestep (lua_State *L) {
  global_State *g = G(L);
  lu_mem work;
  ...
  switch (g->gcstate) {
    case GCSpause: {
      restartcollection(g);      /* 標記根物件 */
      g->gcstate = GCSpropagate; /* 進入傳播 */
      work = 1;
      break;
    }
    case GCSpropagate: {
      if (g->gray == NULL) {     /* gray 佇列空了？ */
        g->gcstate = GCSenteratomic;  /* 傳播完成 */
        work = 0;
      }
      else
        work = propagatemark(g); /* 處理一個灰物件 */
      break;
    }
    case GCSenteratomic: {
      work = atomic(L);          /* 原子收尾（見下） */
      entersweep(L);             /* 進入清掃 */
      ...
      break;
    }
    case GCSswpallgc: {
      work = sweepstep(L, g, GCSswpfinobj, &g->allgc); /* 掃一批 */
      break;
    }
    ...
  }
  ...
  return work;
}
```

看懂這個結構就看懂增量 GC 的骨架了:

- `GCSpause`:一輪開始,標記根,轉到 `GCSpropagate`。
- `GCSpropagate`:**這是增量的主戰場**。每次 `singlestep` 只呼叫一次 `propagatemark`(處理**一個**灰物件),然後就 `return`,把控制權還給程式。gray 佇列還沒空就停在這個狀態,下次再進來繼續。這就是「切成很多小步」——標記幾百萬物件,不是一次做完,是分散在幾百萬次 `singlestep` 呼叫裡。
- `GCSatomic`:**唯一不可中斷的階段**。標記的收尾(處理 weak table、resurrect finalizer 物件、清白物件出 weak table)必須在一個原子動作內完成,因為這期間物件圖的狀態不能被程式打斷。這是增量 GC 裡唯一的 stop-the-world,但它很短。
- `GCSswp*`:清掃。走 `allgc` 鏈,白物件回收、黑物件翻回白(為下一輪準備)。也是分批(`sweepstep` 每次掃一段)。

`propagatemark` 就是三色推進的一步(`lgc.c`,v5.4.7):

```c
static lu_mem propagatemark (global_State *g) {
  GCObject *o = g->gray;
  nw2black(o);                  /* 把這個灰物件塗黑 */
  g->gray = *getgclist(o);      /* 從 gray 佇列移除 */
  switch (o->tt) {
    case LUA_VTABLE: return traversetable(g, gco2t(o));
    case LUA_VLCL: return traverseLclosure(g, gco2lcl(o));
    case LUA_VPROTO: return traverseproto(g, gco2p(o));
    ...
  }
}
```

拿佇列頭的灰物件,塗黑,然後按型別走訪它指向的東西(`traversetable` 把 table 裡的 key/value 塗灰入佇列……)。**白→灰→黑的推進,就發生在這個函式裡**,一次一個物件。

## 清掃階段:白物件怎麼被回收

標記做完(所有活物件黑、垃圾白),進入 `GCSswp*` 清掃階段。核心是 `sweeplist`(`lgc.c`,v5.4.7),它走 `allgc` 鏈,每次處理一批:

```c
static GCObject **sweeplist (lua_State *L, GCObject **p, int countin,
                             int *countout) {
  global_State *g = G(L);
  int ow = otherwhite(g);
  int white = luaC_white(g);  /* current white */
  int i;
  for (i = 0; *p != NULL && i < countin; i++) {
    GCObject *curr = *p;
    int marked = curr->marked;
    if (isdeadm(ow, marked)) {  /* is 'curr' dead? */
      *p = curr->next;  /* remove 'curr' from list */
      freeobj(L, curr);  /* erase 'curr' */
    }
    else {  /* change mark to 'white' */
      curr->marked = cast_byte((marked & ~maskgcbits) | white);
      p = &curr->next;  /* go to next element */
    }
  }
  ...
}
```

兩個關鍵:

- `countin` 限制「這次掃幾個」。清掃也是**分批**的(`sweepstep` 每次餵一個 `GCSWEEPMAX` 之類的量),不是一口氣掃完整條鏈——這是增量性的另一半:標記增量、清掃也增量。
- **回收判斷是 `isdeadm(ow, marked)`**,不是「是不是白」。這裡藏著 Lua 一個精巧設計:**兩種白**(`WHITE0BIT`/`WHITE1BIT`,Ch 5 見過 `marked` 位元)。每輪 GC 用其中一種當「當前白」,另一種是「上輪白=死亡白」。掃描時,marked 是「上輪的白」才算死。**為什麼要兩種白?** 因為增量標記期間新配置的物件會被塗成「當前白」,如果只有一種白,這些剛出生的新物件會被誤判成垃圾當場回收。用兩種白輪替,新物件(當前白)在這輪絕不會被掃掉,下輪才可能——這叫「white flipping」,是增量 GC 避免誤殺新生物件的標準手法。

活物件不回收,但 `curr->marked` 被重設成「當前白」——為下一輪 GC 做準備(下一輪它們得重新從白被證明活)。這就是為什麼 `luaC_barrier_` 在掃描階段那條 `else` 分支要 `makewhite`:配合這個翻白節奏。第一次讀「兩種白」必被繞暈,先接受「有兩種白、輪替使用、避免誤殺新物件」這個結論,細節回頭再啃。

## 誰觸發 GC step?

GC 不會自己跑,得有人踩它。機制是「記帳」:`global_State.GCdebt`(Ch 5 見過)記「配置了多少還沒被回收抵銷的 bytes」。每次配置新物件,debt 增加;debt 超標,就欠 GC 一步。踩點在 `luaC_condGC`(`lgc.h`,v5.4.7):

```c
#define luaC_condGC(L,pre,pos) \
	{ if (G(L)->GCdebt > 0) { pre; luaC_step(L); pos;}; \
	  condchangemem(L,pre,pos); }
```

`GCdebt > 0` 就呼叫 `luaC_step`(Ch 3 抽驗過的那個)走一步。Ch 4 的 `checkGC` 巨集(在 `OP_NEWTABLE`、`OP_CALL` 等會配物件的 opcode 尾巴)就展開成這個。**所以 GC 的推進是被「配置」驅動的**:你的 Lua 程式配越多物件、欠 GC 越多步,GC 就跑越勤。這是一個自我調節的節流閥——配得慢,GC 也慢;配得爆,GC 追上來。

`luaC_step` 內部(Ch 3 抽驗過)分增量(`incstep`)和世代(`genstep`)兩模式,`incstep` 反覆呼叫 `singlestep` 直到「還夠本」(debt 變負)或一輪結束。串起來:

```
   配置物件 → GCdebt++ → checkGC/luaC_condGC → luaC_step → incstep
      → 反覆 singlestep → 每次推進一點三色標記/清掃 → 一輪走完 → GCSpause
```

## atomic:那個短暫的 stop-the-world

`atomic`(`lgc.c`,v5.4.7)是增量 GC 唯一不可打斷的階段,值得看它做什麼(節錄開頭):

```c
static lu_mem atomic (lua_State *L) {
  global_State *g = G(L);
  ...
  g->gcstate = GCSatomic;
  markobject(g, L);  /* mark running thread */
  markvalue(g, &g->l_registry);
  markmt(g);  /* mark global metatables */
  work += propagateall(g);  /* empties 'gray' list */
  work += remarkupvals(g);
  ...
  clearbyvalues(g, g->weak, NULL);   /* 清 weak table */
  ...
}
```

它把增量標記期間 barrier 累積的「grayagain」清空、處理 weak table(值/鍵是弱引用的 table,規則複雜)、決定哪些帶 `__gc` finalizer 的物件要復活。**這些收尾必須原子**,因為它們依賴「此刻的完整物件圖」,不能讓程式插進來改。這也是為什麼即使 Lua 標榜增量,它仍有一個(短暫的)不可中斷點——完全無停頓的 GC 極難做,Lua 的取捨是「把停頓壓到只剩 atomic 這一小段」。

## 對比與取捨

| GC 策略 | 停頓 | 正確性難度 | 代表 |
|---|---|---|---|
| 引用計數(refcount) | 無標記停頓,但循環引用漏收 | 中(要另解循環) | CPython(主),Swift |
| stop-the-world 標記清除 | 長(一次掃完) | 低(圖不變,不需 barrier) | 早期 Lua、教學用 GC |
| **增量標記清除**(Lua 5.4) | 短(切小步,只剩 atomic) | 高(要 write barrier 維持不變式) | Lua、老 V8 |
| 世代式(generational) | 短(多數只掃新物件) | 高 | Lua 5.4 亦支援、JVM、V8 |
| 並發(concurrent) | 幾乎無 | 極高 | Go、ZGC |

Lua 5.4 **同時**有增量和世代兩模式(`luaC_step` 裡 `isdecGCmodegen` 分流),本章講增量;世代是「多數物件很快就死,只頻繁掃新生代」的優化,建在同一套三色+barrier 之上。

**為什麼 CPython 主用 refcount 而 Lua 用標記?** refcount 停頓分散(每次減到 0 立刻回收),但循環引用要另一套 GC 補;Lua 的標記清除天然處理循環,代價是 barrier 複雜度。兩種哲學,Ch 23 讀 CPython object model 時會對照——這是動態語言 runtime 最大的設計岔路之一。

## 踩雷集錦

1. **以為 GC 有個獨立執行緒在背景跑**。官方 Lua 的增量 GC**沒有背景執行緒**。它是「搭便車」式的:程式配置物件時順手走幾步 GC(`checkGC` → `luaC_step`)。GC 和程式在同一個執行緒交錯,不是並行。誤以為有背景 thread,你會找不到「GC 什麼時候跑」的答案——答案是「配物件的時候」。
2. **以為 write barrier 是效能優化**。恰恰相反,barrier 是**正確性的必需品**,不是優化。沒有它,增量 GC 會把還活著的物件當垃圾回收(use-after-free)。它反而是成本(每次寫引用多一次檢查),Lua 靠「只有黑指白才動作」把這成本壓到極低。
3. **以為三色是物件的三個獨立欄位**。三色編碼在 `CommonHeader.marked` 的**位元**裡(Ch 5 見過 `marked`)。`iswhite`/`isgray`/`isblack` 是位元測試,不是三個 bool。一個物件同一時刻只有一種顏色。
4. **把 `gcstate` 的數字大小當成進度百分比**。`GCSpropagate=0`、`GCSpause=8`,但這不是「0% 到 80%」。它們是**狀態機的狀態編號**,推進順序是 pause→propagate→atomic→sweep→callfin→pause,不是按數字遞增(propagate 是 0、pause 是 8,一輪從 8 開始繞回 8)。讀 `singlestep` 的 switch 看真實轉移,別看數字大小猜。
5. **想一次讀懂 `atomic` 全部**。`atomic` 裡的 weak table、ephemeron、resurrection、finalizer 復活是 GC 最深的水,第一次讀必卡。**先跳過**,抓住「atomic = 短暫不可中斷的標記收尾」就好。這些是進階主題,理解主線(propagate/sweep/barrier)之後再回來啃。

## 進階:再往深一層

- **gdb 看 GC 真的在走**:`break luaC_step`,`run` 一個會配很多物件的腳本(`local t={} for i=1,100000 do t[i]={} end`),`continue` 幾次,每次 `print G(L)->gcstate` 看狀態機推進(0→1→2→3…)。這是把本章抽象狀態機變成親眼可見的最快方法,接 `reading_code` Ch 18。Ch 3 已經抽驗過 `luaC_step` 的 backtrace 是真的。
- **weak table 與 ephemeron**:`atomic` 裡 `clearbyvalues`/`clearbykeys`/`convergeephemerons` 處理弱引用 table(`__mode="k"`/`"v"`)。ephemeron(弱鍵表)的「key 活才算 value 活」語意需要不動點迭代(`convergeephemerons`),是 GC 演算法裡最精巧的一段。想深挖 GC 再讀,不是主線。
- **世代式模式**:`luaC_step` 的 `genstep` 分支(`isdecGCmodegen`)。世代假設「多數物件年輕就死」,只頻繁掃新生代、少掃老年代。`lgc.c` 的 `youngcollection`/`atomic2gen` 是入口。它復用同一套三色+barrier,是增量之上的優化層。
- **對照 Go 的並發 GC**:Go 的 GC 也是三色標記 + write barrier,但它真的並發(GC 和 goroutine 同時跑),barrier 更複雜(混合 Dijkstra 插入 + Yuasa 刪除 barrier)。讀懂 Lua 這個「交錯但不並行」的最簡版,再看 Go 的並發版,你會認出同一套三色骨架、只是 barrier 為了並發加碼。這是 pattern 遷移的終點:同一個不變式,不同並發強度下的不同守法。

## 本章重點整理

- **增量 GC** 把 stop-the-world 的一次大回收切成很多小步,夾在程式執行間,消除長停頓;代價是「一邊掃一邊改」的正確性難題。
- **三色標記**:白(未證明活)/灰(活但引用未查完)/黑(活且引用查完)。掃描就是把物件白→灰→黑推進,gray 佇列空時一刀切,還白的是垃圾。
- **三色不變式**:任何時候不允許「黑物件指向白物件」。守住它,增量標記才不會漏殺活物件。
- **write barrier** 是不變式的守護者、增量 GC 的命脈:程式寫引用時攔截,只在「黑指白」危險組合動作(`luaC_barrier_` 把白物件 `reallymarkobject` 拉活)。不是優化,是正確性必需。**Ch 4/5 的 `t[k]=v` 就在這裡和 GC 接上**。
- **GC 狀態機**(`GCS*`):pause→propagate(增量主戰場,每步只 `propagatemark` 一個灰物件)→atomic(唯一短暫不可中斷的收尾)→sweep(分批回收)→callfin→pause。`singlestep` 是驅動它的大 switch。
- GC 由**配置驅動**:`GCdebt` 記帳,`checkGC`/`luaC_condGC` 在配物件時踩 `luaC_step`。沒有背景執行緒,GC 和程式在同執行緒交錯。

## 自我檢核

- [ ] 我能講清楚白/灰/黑各代表什麼,以及掃描怎麼把物件推進三色
- [ ] 我能說出三色不變式是什麼,並舉一個「黑指白」導致誤殺活物件的具體例子
- [ ] 我理解 write barrier 為什麼是正確性必需而非優化,以及 `luaC_barrier` 為什麼只在「黑 `p` 指白 `v`」時動作
- [ ] 我能複述 GC 狀態機的主要階段順序,並指出哪個階段是增量主戰場、哪個是唯一不可中斷點
- [ ] 我知道 GC 被誰觸發(配置 → GCdebt → checkGC → luaC_step),以及 Lua 沒有背景 GC 執行緒
- [ ] 我能把 Ch 4/5 的 `t[k]=v` 和本章的 barrier 串起來,說明一次 table 寫入怎麼維持 GC 正確

## 延伸閱讀

- **《The Implementation of Lua 5.0》— §5 (Garbage Collection)** 以及 Lua 官方 [gc 設計筆記](https://www.lua.org/wshop18/Ierusalimschy.pdf)（Roberto 在 Lua Workshop 2018 的增量+世代 GC 演講）
  - **讀哪裡**:先讀 jucs05 的 §5 建三色標記直覺,再讀 wshop18 那份看 5.4 增量+世代雙模式的演進與取捨。作者一手講解,和本章 code 直接對得上。
  - **前提**:讀過本章,懂三色與 barrier。
- **[The Garbage Collection Handbook](https://gchandbook.org/) — Jones, Hosking, Moss**（書）
  - **讀哪裡**:第 2 章(標記清除)、第 15 章(增量與並發 GC)、write barrier 那節。這是 GC 領域的聖經,把本章的「三色不變式/barrier」放進完整理論框架。
  - **前提**:讀過本章當實例錨點,再讀理論會事半功倍。
- **`reading_code` Ch 24「讀懂狀態機與事件驅動」**（本 repo）
  - **讀哪裡**:本章的 `GCS*` 狀態機 + `singlestep` 大 switch 就是這章講的「用狀態變數 + 每次推進一步」的典型。回頭對照,把讀狀態機的通法套到 GC 上。
  - **前提**:無。

Lua 的三大機制(VM、值/table、GC)都攻下來了。下一章我們收斂:把這五章讀到的可遷移設計做成 pattern 卡片,標好「怎麼一眼認出、Lua 在哪、可遷移到 SQLite/CPython 哪裡」,為後面幾個 Part 建索引。

→ [Ch 7 從 Lua 萃取的可遷移 pattern](./07-lua-patterns-extracted.md)
