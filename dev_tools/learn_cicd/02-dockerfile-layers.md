# Ch 2 — Dockerfile 與 layer 原理

> 目標：搞懂 image 是由 read-only layer 疊出來的、cache 怎麼運作、為什麼 COPY 的順序決定 build 快慢。為 `tasktrack` 寫第一版 Dockerfile 並優化。

## 先破一個誤解：Dockerfile **不是** shell script

新手常把 Dockerfile 當 bash：

```dockerfile
# ← 這不是 bash 心智模型
cd /app
pip install requirements.txt
python app.py
```

然後寫出：

```dockerfile
FROM python:3.12
RUN cd /app
RUN pip install -r requirements.txt
CMD python app.py
```

問題：**每一條指令都跑在獨立的容器裡，結束就丟**。`RUN cd /app` 改變的是那一層的 shell，下一條 `RUN` 根本不記得你 cd 過。

正確寫法是用 `WORKDIR /app`，它改變「這個 image 的當前目錄」這件事本身（metadata），而不是 shell state。

記住一件事：**Dockerfile 的每一條指令，都在問「這條指令執行完，image 應該變成什麼樣子？」**。它是宣告式的，不是命令式的。

## Image = read-only layer 疊起來

一張 image 實際上是這樣：

```
┌──────────────────────────────────┐
│  Layer 5: COPY app/ app/         │  ← 你的程式碼
├──────────────────────────────────┤
│  Layer 4: RUN pip install ...    │  ← 依賴
├──────────────────────────────────┤
│  Layer 3: COPY requirements.txt  │
├──────────────────────────────────┤
│  Layer 2: WORKDIR /app           │
├──────────────────────────────────┤
│  Layer 1: python:3.12 base image │  ← 幾百 MB
└──────────────────────────────────┘
      疊成一個 overlayfs，read-only
```

每一層：
- **有一個 content hash**（SHA-256 of layer tarball）
- **只記錄它 _相對上一層_ 的變化**（像 git commit 的 diff）
- **read-only**，容器跑起來時上面疊一層可寫的 `container layer`

三個實務意涵：

1. **很多 image 共享 base layer** — 你拉 `python:3.12` 和 `node:20`，它們的 Debian slim base 可能是同一層，只拉一次
2. **COPY 一個 1GB 檔進 image，那層就是 1GB，後面再刪也刪不掉**（layer 是疊加的，後層的 `rm` 只是「在那層標記為刪除」，前層的資料還在）
3. **cache 是 per-layer 的**，這是下一段的重點

## Layer cache 的三條規則

Docker build 每條指令都會問自己三個問題：

1. **上一層是否命中 cache？** → 沒有，直接重建本層
2. **這條指令的 _輸入_ 變了嗎？** → 變了就重建，沒變就用 cache
3. **本層 cache hit 後，繼續往下問第 1 題**

「輸入」的定義因指令而異：

| 指令 | cache key 看什麼 |
|---|---|
| `FROM` | image 的 digest |
| `RUN` | **整條命令字串**（即使字面改一個空格也會 miss） |
| `COPY` / `ADD` | **檔案內容 hash**（不只是檔名，改內容也 miss） |
| `ENV` / `ARG` | 變數名 + 值 |
| `WORKDIR`, `USER`, `EXPOSE` | 字串值 |

**關鍵踩雷**：`RUN pip install -r requirements.txt` 的 cache key 只是那個字串。如果 PyPI 上 `fastapi` 新版發了，但你的 Dockerfile 沒改字，**cache 命中，你拿到舊版本**。這是為什麼 pin 版本很重要（Ch 3 會再講）。

## 實作：為 tasktrack 寫 v0（天真版）

到你的 `tasktrack/` 根目錄，建一個 `Dockerfile`：

```dockerfile
FROM python:3.12

WORKDIR /app

COPY . .

RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Build：

```bash
docker build -t tasktrack:v0 .
```

看它跑的過程。第一次大概要 60–90 秒（看網路）。然後：

```bash
docker run --rm -p 8000:8000 tasktrack:v0
```

另開終端 `curl localhost:8000/tasks` 應該回 `[]`。

通了？好，**這個 Dockerfile 有兩個嚴重問題**。

## 看 layer：`docker history`

```bash
docker history tasktrack:v0
```

輸出長這樣（精簡過）：

```
IMAGE          CREATED         CREATED BY                              SIZE
a1b2c3d4...   10 seconds ago  CMD ["uvicorn" ...]                     0B
<missing>     10 seconds ago  EXPOSE map[8000/tcp:{}]                  0B
<missing>     12 seconds ago  RUN pip install ... requirements.txt    45MB
<missing>     50 seconds ago  COPY . . # buildkit                      200KB
<missing>     1 minute ago    WORKDIR /app                             0B
<missing>     2 weeks ago     /bin/sh -c #(nop) CMD ["python3"]        0B
<missing>     2 weeks ago     ... python:3.12 base layers ...          1.02GB
```

看到了嗎？`python:3.12` 本身就 1GB，加上依賴約 1.05GB。**Ch 3 才處理大小**，這章先看 **build 時間**。

## 問題 1：COPY 順序錯，cache 幾乎沒命中

把 `app/main.py` 的某一行改一下（新增空行就好），再 build：

```bash
docker build -t tasktrack:v0 .
```

你會發現 `pip install` **又跑了一次**，50 秒又沒了。為什麼？

回去看 Dockerfile：

```dockerfile
COPY . .                                      # ← 這層 cache 失效（因為 main.py 變了）
RUN pip install -r requirements.txt           # ← 所以這層也失效（下游連坐）
```

你只改了一行程式碼，但因為 `COPY . .` 把所有東西都當 cache key 的一部分，**下游所有 layer 全部 miss**。

### v1：調 COPY 順序

改成：

```dockerfile
FROM python:3.12

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

原理：**把變動頻繁的放後面，穩定的放前面**。`requirements.txt` 幾週才改一次，`app/` 幾分鐘改一次。

測一下：

```bash
docker build -t tasktrack:v1 .         # 完整 build
# 改 app/main.py 一行
docker build -t tasktrack:v1 .         # 應該幾秒內結束
```

第二次 build 的輸出會看到：

```
=> CACHED [2/5] WORKDIR /app
=> CACHED [3/5] COPY requirements.txt .
=> CACHED [4/5] RUN pip install ...
=> [5/5] COPY app/ app/                      ← 只有這層重建
```

## 問題 2：`COPY . .` 把不該進 image 的東西塞進來

`COPY . .` 會把 `.venv/`、`.git/`、`__pycache__/`、`.pytest_cache/`、你本地的 `*.db` 全部 COPY 進 image。

你的 `.venv/` 可能就 300MB。好消息是 v1 我們改成 `COPY app/ app/`，只複製 `app/`，這個問題減輕了。但：

- 如果以後有 `static/`、`templates/` 也要加
- 你可能還是會 `COPY . .`（有時真的需要）
- Build context 本身會被 send 到 daemon — `.venv` 在你家，但 `docker build` 會傳整個資料夾給 daemon，送 300MB 上去很蠢

解法：`.dockerignore`。格式跟 `.gitignore` 一樣：

```
.git
.venv
__pycache__
.pytest_cache
.mypy_cache
.ruff_cache
*.db
*.db-journal
.env
tests
Dockerfile
.dockerignore
```

放在專案根目錄。Build 時它會被 Docker CLI 讀，**送 context 給 daemon 之前**就把這些排除。

**注意**：我把 `tests/` 也排除了 — production image 不需要 test code。這是個判斷，有團隊會留著方便 debug，沒標準答案。

## 核心指令速覽

Dockerfile 有一堆指令，這章用到的：

| 指令 | 幹嘛 | 注意 |
|---|---|---|
| `FROM` | 選 base image | 一定是第一條（ARG 可以在前） |
| `WORKDIR` | 設當前目錄（不存在會建立） | 比 `RUN cd` 好 |
| `COPY src dst` | 從 build context 複製進 image | `dst` 相對於 WORKDIR |
| `RUN cmd` | build 時執行，產生一層 | 儘量合併多條 `&&`（layer 少） |
| `ENV K=V` | 設環境變數 | build 後運行時都有 |
| `EXPOSE 8000` | **宣告** port（不實際開） | `docker run -p` 才真的映射 |
| `CMD [...]` | 容器啟動時的預設指令（可覆蓋） | 用 exec form（JSON 陣列） |
| `ENTRYPOINT [...]` | 容器啟動時的主指令（很難覆蓋） | 進階用，這課後面才碰 |

### CMD vs ENTRYPOINT 一句話講清

- `CMD` 是「預設跑什麼」，`docker run image <其他指令>` 會整個 **覆蓋**
- `ENTRYPOINT` 是「這個 image 本質是什麼」，`docker run image <args>` 會把 args 追加在後面
- **9 成情況你要的是 `CMD`**。`ENTRYPOINT` 適合做成「像 CLI 工具」的 image（`docker run myimg --flag`）

### Exec form vs shell form

```dockerfile
CMD ["uvicorn", "app.main:app"]              # ← exec form，直接執行
CMD uvicorn app.main:app                     # ← shell form，實際是 /bin/sh -c "uvicorn app.main:app"
```

**一律用 exec form**。shell form 會多一層 `sh`，訊號處理（SIGTERM）會被 `sh` 吃掉，容器停不下來。這是超常見的坑。

## 動手練習

到 `tasktrack/` 做以下步驟：

1. 建 v0 天真版 Dockerfile，build 並 run，確認 API 能動
2. 改 `app/main.py` 一行、再 build，**記錄時間**
3. 改成 v1（COPY 順序優化）、加 `.dockerignore`
4. 改 `app/main.py` 一行、再 build，**再記錄時間**
5. 跑 `docker history tasktrack:v1`，讀懂每一層

兩次改 code 後的 build 時間差，應該至少 10×。沒有？回頭對 Dockerfile，八成是 COPY 順序還是錯的。

## 常見誤解

- 「**layer 少比較好**」 — 差不多，但過度合併 `RUN` 會犧牲 cache 粒度。合併到 _邏輯一組_ 就好（如 `apt-get update && apt-get install ...` 要一行，不然前者 cache 命中後裝的套件會抓不到 repo metadata）
- 「**刪除檔案可以瘦身**」 — 不行。`RUN rm bigfile` **不會縮小前面那層**，只會在這層加一個 tombstone。要真瘦身要 Ch 3 的 multi-stage
- 「**`ENV FOO=bar` 跟 `ARG FOO=bar` 一樣**」 — 完全不同。`ENV` 是 **image 裡一直都在**的環境變數，`ARG` 只在 **build time** 存在
- 「**`EXPOSE` 會讓 port 開放**」 — 不會。它只是 metadata，`docker run` 不加 `-p` 還是連不進去

## 驗收標準

- [ ] `tasktrack` 根目錄有 Dockerfile 和 `.dockerignore`
- [ ] `docker build -t tasktrack:v1 .` 成功
- [ ] `docker run --rm -p 8000:8000 tasktrack:v1` 能起、`curl` 打得通
- [ ] 改 `app/main.py` 一行後 rebuild，**只有最後一層重建**，時間 < 5 秒
- [ ] `docker history tasktrack:v1` 你能解釋每一層是幹嘛的

**達成就走**。image 還有 1GB+ 很大 — 知道就好，Ch 3 會砍到 100MB 以內，現在不要在這裡糾結。

## 自我檢核

- [ ] 我知道 Dockerfile 是宣告式、不是 shell script
- [ ] 我能畫出 image 的 layer 結構（堆疊、read-only、content hash）
- [ ] 我能解釋 `COPY requirements.txt` 為什麼要放在 `COPY app/` 前面
- [ ] 我理解 `.dockerignore` 是 build context 的過濾器，不是 image 層的
- [ ] 我知道用 `CMD` 的 exec form、為什麼 shell form 危險

下一章處理大小問題。我們會看一個超髒的招：**用一個 image 裝東西、用另一個 image 裝 runtime**。

→ [Ch 3 Multi-stage build：把 image 砍小](./03-multi-stage-build.md)
