# Ch 34 — SVGA / mks GPU 攻擊面（Pwn2Own 最高產）

> **目標**：理解 VMware SVGA II 裝置的命令流程，掌握為何 FIFO 命令集與 surface 狀態機是最高產的 guest-to-host 漏洞來源，並能用 patch-diff 鎖定修補點。

---

## 為什麼需要這個？

2017 年 Pwn2Own 是 VMware 有史以來最慘烈的一屆——光 SVGA / mks 攻擊面就被不同團隊連打四個洞。Qihoo 360 拿走 VMware 類別的最高獎金，Team Sniper (Keen Lab) 緊接其後，連同 VMware SVGA 帶回一筆可觀的賞金。回頭看 2009 年，Kostya Kortchinsky 在 Black Hat USA 發表 Cloudburst（CVE-2009-1244），那是第一個公開的 VMware guest-to-host escape，攻擊路徑同樣指向 SVGA FIFO 的顯示記憶體處理。

時間跨度將近十年，SVGA 一直是高價值目標，理由很簡單：

1. **命令集龐大**：SVGA3D 有數十種 surface 操作、shader 操作，每種命令有獨立的 parser，任一 parser 的邊界計算有誤就是漏洞。
2. **guest 完全控制輸入**：FIFO 是 guest/host 共用記憶體，guest 可以寫入任意 size 欄位和命令內容，沒有任何 hypervisor 外的硬體保護。
3. **閉源難稽核**：vmware-vmx 是 closed-source binary，patch 發出去之前沒有人能走讀所有 parser。

2018 年 Census Labs 的研究員 Zisis Sialveras 在 Black Hat EU 發表「Straight Outta VMware」，把 SVGA3D 攻擊面的 surface 狀態機與 heap spray 技法系統整理成公開 writeup（census-labs.com），讓這個攻擊面的研究生態更完整。

本章的任務是把這條攻擊路徑從頭到尾走一遍：裝置架構 → FIFO 命令流程 → 狀態機設計缺陷 → 歷史 CVE 技法 → 自己怎麼挖。

---

## 先建立直覺

VMware 的 SVGA II 是一個以 PCI 裝置形式呈現給 guest 的虛擬 GPU。guest 不知道背後是模擬的；它看到的是一塊 PCI 顯示卡，跑 Mesa vmwgfx 驅動（Linux）或 VMware Tools 驅動（Windows）。

整個通訊協定分兩層：

**PIO/MMIO 暫存器層**（控制面）：
guest 用 I/O port（預設 `0x5658` / `0x5659`）或 MMIO 讀寫暫存器，設定 framebuffer 位址、FIFO 位址、啟用裝置、觸發中斷等。暫存器清單定義在 vmware 公開的 `vmware-svga.h`（Mesa 專案含這份 header）。

**FIFO 命令層**（資料面）：
實際的繪圖工作透過一塊 guest/host 共用的環形記憶體（FIFO ring）傳遞。guest 寫入命令、更新 NEXT_CMD 指標；host 的 vmware-vmx 輪詢 STOP 指標後讀取並執行命令。

這個設計的核心問題：**FIFO 的內容是 guest 完全可控的，但 vmware-vmx 是帶著 host 特權在執行命令的**。任何一個命令的 parser 出現長度計算錯誤，就直接是 host 的堆積損毀或越界讀取。

SVGA3D 命令集（3D 加速路徑）的複雜度遠高於 2D 路徑：
- `SVGA_3D_CMD_SURFACE_DEFINE`：建立 surface，指定 ID、格式、寬高、mipmap 層級
- `SVGA_3D_CMD_SURFACE_DESTROY`：釋放 surface
- `SVGA_3D_CMD_SET_SHADER`：綁定 shader bytecode 到 context
- `SVGA_3D_CMD_DRAW_PRIMITIVES`：送 draw call
- 其餘 30+ 種操作，每種有自己的 payload 結構

每種命令的 payload 大小由 guest 控制的欄位決定。Parser 在計算 payload 該讀多少 bytes 時，任何整數溢位或遺漏上界檢查就會發生越界。

---

## 底層機制：SVGA FIFO 架構與命令執行流程

```
Guest 空間
┌─────────────────────────────────────────────────┐
│  vmwgfx driver / VMware Tools display driver    │
│                                                 │
│  1. 寫命令到 FIFO ring                          │
│  2. 更新 SVGA_FIFO_NEXT_CMD 指標               │
│  3. 寫 SVGA_REG_SYNC 暫存器（觸發 vmx 消費）   │
└───────────────┬─────────────────────────────────┘
                │  共用記憶體（GPA 區段）
                ▼
┌─────────────────────────────────────────────────┐
│              SVGA FIFO Ring Buffer              │
│  ┌──────────────────────────────────────────┐  │
│  │ [MIN] [MAX] [NEXT_CMD] [STOP]            │  │  ← 控制欄（FIFO header）
│  │ [CMD_HDR][PAYLOAD][CMD_HDR][PAYLOAD]...  │  │  ← 命令資料
│  └──────────────────────────────────────────┘  │
│  MIN/MAX 定義有效環形範圍                       │
│  NEXT_CMD = guest 下一個要寫的位置             │
│  STOP     = host 下一個要讀的位置              │
└───────────────┬─────────────────────────────────┘
                │  vmware-vmx 輪詢 STOP != NEXT_CMD
                ▼
┌─────────────────────────────────────────────────┐
│              vmware-vmx (host process)          │
│                                                 │
│  SVGA FIFO Reader                               │
│  ├─ 讀 CMD_HDR（cmdType + size）               │
│  ├─ 依 cmdType 分發到對應 parser               │
│  │   ├─ parse_surface_define()                 │
│  │   ├─ parse_set_shader()                     │
│  │   ├─ parse_draw_primitives()                │
│  │   └─ ... (30+ parsers)                      │
│  └─ 更新 STOP 指標                             │
│                                                 │
│  mks 子系統（mouse-keyboard-screen）            │
│  ├─ 接收 parsed 命令結果                       │
│  ├─ 轉發給實際 GPU（硬體加速路徑）             │
│  │   或軟體 renderer（無 GPU 時）              │
│  └─ 管理 surface / shader / context 狀態機     │
└─────────────────────────────────────────────────┘
```

**FIFO 控制欄格式（公開 vmware-svga.h 定義）**：

```c
// FIFO header 的前幾個 dword（以 GPA 偏移計算）
#define SVGA_FIFO_MIN        0   // 有效資料區起始偏移
#define SVGA_FIFO_MAX        1   // 有效資料區結束偏移
#define SVGA_FIFO_NEXT_CMD   2   // guest 寫完後更新此指標
#define SVGA_FIFO_STOP       3   // host 讀完後更新此指標
```

**命令 header 格式**：

```c
// 每個 FIFO 命令的起頭
struct SVGAFifoCmdHeader {
    uint32_t type;   // 命令類型（SVGA_3D_CMD_* 等）
    uint32_t size;   // 後接 payload 的 byte 數（guest 控制）
};
```

`size` 欄位由 guest 完全控制。vmware-vmx 在 `parse_*` 函式中必須在 **使用** size 之前驗證它；如果先做了整數運算（例如 `offset = base + size`）而沒有先檢查 size 是否超出合理範圍，就可能發生整數溢位，導致 vmx 在計算出的錯誤偏移位置讀取 payload。

**surface 狀態機（3D 加速路徑）**：

```
SURFACE_DEFINE  ──────────────────────────────────────────►  surface 存在
                                                              id / width / height
                                                              / format 儲存在
                                                              vmx 的 surface 表
      │
      ▼
SURFACE_DMA / BLIT / SURFACE_COPY ...（操作 surface）
      │
      ▼                                               ┌─────────────────┐
SURFACE_DESTROY  ──────────────────────────────────►  │ surface 釋放    │
                                                      │ id 變無效       │
                                                      └─────────────────┘
                                                               │
                                              若後續命令仍用此 id → UAF
```

Surface 以整數 ID 索引。如果 vmx 在 `SURFACE_DESTROY` 後沒有把對應 slot 清空（或清空邏輯有 race），後續命令用同一個 ID 就能操作已釋放的記憶體。

---

## 對比與取捨

| 面向 | SVGA 2D 命令集 | SVGA3D 命令集 |
|------|--------------|--------------|
| 命令數量 | ~10 種（blit、矩形填色等） | 30+ 種（surface、shader、draw call、query...） |
| 結構複雜度 | 低，payload 格式固定 | 高，payload 含可變長度陣列、巢狀結構 |
| Parser 數量 | 少 | 多，每種命令獨立 parser |
| 歷史漏洞密度 | 低（Cloudburst 屬 2D 路徑） | 高（Pwn2Own 2017 系列全在 3D） |
| 公開文件 | vmware-svga.h、VMware 舊版文件 | vmware-svga3d_reg.h、Mesa vmwgfx、Census Labs writeup |
| Fuzzing 難度 | 低（payload 格式已知） | 中高（命令相依性：先 DEFINE 再操作） |
| 修補後可辨識性 | patch 範圍窄 | patch 常改多個 parser，patch-diff 定位較精準 |

| 攻擊路徑 | 說明 | 公開案例 |
|----------|------|---------|
| FIFO size 整數溢位 → heap OOB write | 計算 `dst_offset = base + size` 時溢位 | CVE-2017-4902（heap buffer overflow，公開 Pwn2Own 2017 報告） |
| FIFO size 整數溢位 → OOB read → infoleak | 讀超出合法範圍，洩漏 vmx heap 指標 | CVE-2017-4900/4901（Keen Lab，uninit/OOB read，同屆 Pwn2Own） |
| Surface UAF | DESTROY 後再用同 ID 觸發操作 | 理論路徑；公開 writeup 未直接點名特定 CVE，但 Census Labs 報告提及 surface 狀態機為危險點（逆向推測） |
| Shader bytecode 長度計算 | SET_SHADER 的 bytecode size 可控 | Census Labs「Straight Outta VMware」（BH EU 2018）展示 SVGA_3D_CMD_SET_SHADER 相關 heap spray |
| Uninitialized stack/heap buffer | 未初始化就回傳給 guest（infoleak） | CVE-2017-4901（据 VMSA-2017-0006 描述） |

---

## 踩雷集錦

**1. 以為 FIFO 有硬體邊界保護**

錯誤認知：PCI 裝置的 MMIO 區域由硬體管控，vmx 讀 FIFO 的邊界也受硬體保護。  
正確認知：FIFO 是一塊普通的 guest 實體記憶體（GPA），vmware-vmx 用軟體指標追蹤讀寫位置。沒有任何硬體在檢查「size 欄位是否合理」——這完全是 vmx 軟體邏輯的責任。邊界計算的任何軟體 bug 都直接成立。

**2. 以為整數溢位在 64-bit 環境很少發生**

錯誤認知：vmware-vmx 是 64-bit process，32-bit size 欄位根本溢位不了 64-bit 加法。  
正確認知：問題通常發生在中間計算用了 `uint32_t` 型別或比較邏輯用了 32-bit 截斷。CVE-2017-4902 的 heap buffer overflow 正是在 32-bit 算術做完後才轉型，導致分配大小與實際寫入大小不符。具體數值未實測，屬公開報告描述的推斷。

**3. 以為只有 root guest 才能送 SVGA3D 命令**

錯誤認知：3D 加速需要驅動，driver 需要 root 或特殊權限，一般用戶不能觸發漏洞。  
正確認知：Linux 的 vmwgfx 驅動在 desktop session 下讓普通用戶透過 `/dev/dri/renderD*` 送 3D 命令。Windows 的 VMware Tools display driver 同樣在用戶態可操作。部分提權到 guest kernel 後再打 SVGA 的案例確實存在，但不是必要前提。

**4. 以為 SVGA 命令在 patch 後都有版本號可以追蹤**

錯誤認知：VMware 既然閉源，patch 後根本不知道改哪裡。  
正確認知：VMware 發 VMSA 公告時通常附 CVE 號和受影響版本範圍。兩個版本的 vmware-vmx binary 用 BinDiff 或 Diaphora 做 patch-diff，相似度下降的函式就是修補點，能精確對應到哪個命令的 parser 被改動。Census Labs 報告明確提到這個方法。

**5. 以為 mks 和 SVGA parser 是同一支程式碼**

錯誤認知：所謂「mks 漏洞」和「SVGA 漏洞」是同一件事。  
正確認知：SVGA FIFO reader 負責解析命令（這層出問題是 parser bug）；mks 子系統是 FIFO reader 的下游，負責把解析後的操作送給實際 GPU 或軟體 renderer。兩層都可能有 bug，Pwn2Own 報告用「SVGA / mks」通稱是因為 mks 是 SVGA 後端，但 bug 位置要靠 patch-diff 確認。

---

## 進階：再往深一層

### SVGA3D Surface 格式矩陣的攻擊意義

`SVGA_3D_CMD_SURFACE_DEFINE` 的 payload 包含 `SVGA3dSurface1Flags`、`SVGA3dSurfaceFormat` 和各 mipmap 層的 `{width, height, depth}`。Format 決定每個 texel 佔幾個 bytes（`SVGA3D_DEVCAP_*` 列表有數十種格式）。

vmx 在計算這個 surface 要分配多少記憶體時，邏輯大致是：

```c
// 推斷性虛擬碼，非真實 vmx 程式碼
uint32_t total = 0;
for each mipmap_level in payload:
    uint32_t mip_size = width * height * depth * bytes_per_texel(format);
    total += mip_size;
alloc(total);
```

如果 `width * height` 在 `uint32_t` 下溢位，或各層加總的 `total` 溢位，分配的緩衝區會小於後續填充時預期的大小，造成 heap OOB write。不同 format 的 `bytes_per_texel` 不同，讓這個乘積計算的溢位邊界非常多樣——這是 fuzzing 比人工靜態分析更有效率的地方。

### heap spray 搭配 SVGA 命令

Census Labs（Zisis Sialveras，BH EU 2018）展示的技法（以公開 writeup 為準）：

1. 用 `SURFACE_DEFINE` 大量分配特定大小的 surface，塑造 heap 布局
2. 用有缺陷的命令（如 `SET_SHADER` payload 長度計算錯誤）觸發 OOB write，覆蓋相鄰 surface 結構的後設資料（metadata）
3. 利用損毀的 metadata 觸發受控的 OOB read，洩漏 vmx 模組的基址
4. 用基址繞過 ASLR，計算 ROP gadget，構成任意程式碼執行

這是一套完整的 primitive chain：heap OOB write → infoleak → ASLR bypass → RCE。每一步都依賴 SVGA 命令集提供的受控操作。

### Cloudburst (CVE-2009-1244) 的歷史意義

Kostya Kortchinsky 在 Black Hat USA 2009 發表的 Cloudburst 是業界第一個公開的 VMware guest-to-host escape，距今逾 15 年。論文（Black Hat 網站存檔）描述目標是 SVGA 的顯示記憶體（video memory）操作路徑，屬 2D 路徑的 FIFO 處理 bug。

歷史意義：它確立了「SVGA FIFO 是 guest-to-host escape 的可行路徑」的研究認知，後續十年的 SVGA 研究都從這份論文出發。

### 從 vmwgfx 核心驅動學命令格式

Mesa 專案的 `src/gallium/drivers/svga/` 和 Linux kernel 的 `drivers/gpu/drm/vmwgfx/` 含有完整的 SVGA3D 命令組裝程式碼。這些是開源的，可以直接讀懂每個命令的 payload layout，比逆向 vmware-vmx 的 parser 容易得多。

```
Linux kernel vmwgfx 有用的標頭：
  drivers/gpu/drm/vmwgfx/device_include/svga3d_reg.h
  drivers/gpu/drm/vmwgfx/device_include/svga_reg.h
```

讀懂 guest-side 的組裝邏輯後，再到 vmware-vmx 找對應的 parser，能快速定位「這個命令在 host 端怎麼處理 size 欄位」。

### Shader Bytecode 路徑

`SVGA_3D_CMD_SET_SHADER` 的 payload 含 shader type（vertex / fragment）和 bytecode 陣列。bytecode 的長度由 payload size 推算。Census Labs 報告指出這個路徑是 heap spray 的高價值目標（公開 writeup 已說明，具體漏洞細節未完全揭露）。

從攻擊者角度，shader bytecode 路徑的優點是：bytecode 本身是 blob，vmx 必須讀取全部 bytes 才知道 shader 格式是否合法，這代表 vmx 會把我們的 payload 完整複製到 host heap 上的某個緩衝區——這正是 heap spray 需要的「可控資料寫入已知大小緩衝區」的 primitive。

---

## 動手練習

以下練習均在 **guest VM 內**執行，不需要有真實 GPU，SVGA 軟體模擬路徑即可觸發。

**環境需求**：VMware Workstation 或 Fusion（版本建議使用已知漏洞版本做研究，或最新版做觀察），Linux guest，安裝 `mesa-utils`、`libdrm-dev`。

---

**練習 1：確認 SVGA FIFO 的 GPA 映射**

```bash
# guest 內，以 root 執行
# 查看 vmwgfx 模組載入
lsmod | grep vmwgfx

# 查看 PCI 裝置資訊
lspci | grep -i vmware
lspci -v -s <SVGA device BDF>

# 查看 vmwgfx debug 資訊（若有 debugfs）
ls /sys/kernel/debug/dri/
cat /sys/kernel/debug/dri/0/vmwgfx-info 2>/dev/null || echo "no debugfs entry"
```

預期：能看到 SVGA II PCI 裝置的 BAR0（MMIO）、BAR1（framebuffer）、BAR2（FIFO）大小。FIFO 通常 4MB。

---

**練習 2：用 Mesa / DRM 送一個合法 SVGA3D 命令，確認路徑通暢**

```c
// fifo_test.c — 透過 libdrm-vmwgfx 送一個 surface define 命令
// 僅做觀察，不觸發任何漏洞
#include <stdio.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/ioctl.h>

// 簡化版：確認 /dev/dri/renderD* 可開啟
int main(void) {
    int fd = open("/dev/dri/renderD128", O_RDWR);
    if (fd < 0) {
        perror("open renderD128");
        // 嘗試 card0
        fd = open("/dev/dri/card0", O_RDWR);
        if (fd < 0) {
            perror("open card0");
            return 1;
        }
    }
    printf("DRM device opened, fd=%d\n", fd);
    close(fd);
    return 0;
}
```

```bash
gcc -o fifo_test fifo_test.c && ./fifo_test
```

---

**練習 3：找一個 VMSA patch，用 BinDiff 定位修補函式（理論流程，需有兩個版本的 vmware-vmx）**

1. 從 VMware 下載 Workstation 17.x.0（patch 前）和 17.x.1（patch 後）的安裝包。
2. 解包（Linux 下 `.bundle` 用 `sh VMware-Workstation-*.bundle --extract /tmp/vmw`）取出 `vmware-vmx`。
3. 用 IDA + BinDiff 或 Ghidra + Diaphora 比對兩個 binary。
4. 篩選相似度低於 0.9 的函式（被改過的），找名稱含 `svga`、`surface`、`shader`、`fifo` 關鍵字的（以 debug symbol 存在為前提；若無 symbol 就按 cross-reference 追蹤）。
5. 記錄：哪個函式被改？改了什麼邏輯（新增了哪種邊界檢查）？

這個流程對所有閉源 VMware 漏洞都通用。練習 3 是概念性的，具體 diff 結果取決於你選的 patch 版本。

---

**練習 4：閱讀 Census Labs writeup 並對應到 vmwgfx 核心程式碼**

1. 下載 Census Labs「Straight Outta VMware」（Black Hat EU 2018）PDF，以及 Zisis Sialveras 的 census-labs.com 詳細版文章。
2. 找出 writeup 中提到的 `SVGA_3D_CMD_SET_SHADER` 相關 payload 結構，對應到 `drivers/gpu/drm/vmwgfx/device_include/svga3d_reg.h` 的 `SVGA3dCmdSetShader` 定義。
3. 比對 guest-side 組裝（kernel driver）和 writeup 描述的 host-side parser 預期行為，找出 size 欄位被如何使用。

這個練習訓練「從開源 guest driver 推斷閉源 host parser 行為」的核心技能。

---

## 本章重點整理

- VMware SVGA II 是以 PCI 裝置形式呈現的虛擬 GPU，guest 透過 FIFO ring 送命令，vmware-vmx 讀取並在 host 執行——FIFO 內容完全由 guest 控制。
- SVGA3D 命令集（surface、shader、draw call 等 30+ 種命令）是主要攻擊面，每種命令有獨立 parser，任一 parser 的長度計算 bug 直接等於 host 記憶體損毀。
- mks 子系統是 SVGA FIFO reader 的下游後端；「SVGA / mks 漏洞」通稱這整條處理鏈上的 bug。
- 歷史 CVE 集中在：整數溢位導致 heap OOB（CVE-2017-4902）、未初始化緩衝區 infoleak（CVE-2017-4900/4901）、surface 狀態機的 UAF 風險（理論路徑，具體 CVE 未公開確認）。
- 完整 exploit chain 如 Census Labs 展示：heap spray（SURFACE_DEFINE 大量分配）→ OOB write（壞命令觸發）→ infoleak（損毀 metadata 造成 OOB read）→ ASLR bypass → RCE。
- patch-diff 方法（BinDiff / Diaphora）可在閉源環境下精確定位修補點，是研究 VMware 漏洞的標準工具。
- 公開資源：Mesa vmwgfx kernel driver 含完整命令格式；Census Labs writeup 是 SVGA3D 攻擊面最詳盡的公開分析；Black Hat 2009 Cloudburst 論文是歷史起點。

---

## 自我檢核

- [ ] 我能描述 SVGA FIFO 的四個控制欄（MIN / MAX / NEXT_CMD / STOP）各自的角色。
- [ ] 我知道 SVGA3D 命令的 `size` 欄位由誰控制，以及它在 host parser 中的危險點。
- [ ] 我能解釋為何 surface 狀態機（DEFINE → 操作 → DESTROY）可能產生 UAF，以及 ID 索引在其中的角色。
- [ ] 我知道 CVE-2017-4902 屬於哪種 bug 類型，以及誰在哪個活動發現它。
- [ ] 我能描述 Census Labs「Straight Outta VMware」展示的 primitive chain 的每個步驟。
- [ ] 我知道如何用 Linux kernel 的 vmwgfx driver 程式碼推斷 vmware-vmx 的 parser 行為。
- [ ] 我能說明 patch-diff 的工作流程，以及為什麼它在閉源研究中是必要的。

---

## 延伸閱讀

1. **Kostya Kortchinsky, "Cloudburst: A VMware Guest to Host Escape Story"**  
   Black Hat USA 2009。讀哪裡：Black Hat 官網存檔 PDF（搜尋 "Cloudburst Kortchinsky Black Hat 2009"）。  
   學什麼：第一個公開的 VMware guest-to-host escape，確立 SVGA FIFO 為可行攻擊路徑的歷史論文。  
   關聯：CVE-2009-1244；理解它讓你明白為何後續 SVGA 研究都從 FIFO parsing 出發。

2. **Zisis Sialveras (Census Labs), "Straight Outta VMware"**  
   Black Hat EU 2018 + census-labs.com 完整版文章。讀哪裡：census-labs.com/news/2018/12/05/vmware-vulnerabilities/。  
   學什麼：SVGA3D surface 狀態機攻擊面系統整理，heap spray + SET_SHADER infoleak + ASLR bypass 完整 chain。  
   關聯：目前最完整的公開 SVGA3D 攻擊面分析，Ch 34 大量引用此源。

3. **VMware Security Advisory VMSA-2017-0006**  
   讀哪裡：vmware.com/security/advisories/VMSA-2017-0006.html。  
   學什麼：Pwn2Own 2017 一批 SVGA / mks 漏洞的官方修補公告，含 CVE 號、嚴重等級、受影響版本。  
   關聯：對應 CVE-2017-4900/4901/4902/4903；用這份公告對齊 BinDiff 的 patch-diff 結果。

4. **Linux kernel vmwgfx driver 源碼**  
   讀哪裡：`drivers/gpu/drm/vmwgfx/` + `device_include/svga3d_reg.h`（kernel.org 或你的 Linux 源碼樹）。  
   學什麼：SVGA3D 命令的 guest-side 組裝格式，包含每個命令的 payload 結構定義。  
   關聯：這是唯一完整公開的 SVGA3D 格式文件，是逆向 vmware-vmx parser 的對照參考。

5. **Pwn2Own 2017 VMware 類別得分板與 Trend Micro ZDI 部落格**  
   讀哪裡：zerodayinitiative.com/blog，搜尋 "Pwn2Own 2017 VMware"。  
   學什麼：Qihoo 360 和 Keen Lab 各自利用的 SVGA 漏洞的高層描述（ZDI 通常在修補後發表概要，不含完整 PoC）。  
   關聯：給你 CVE-2017-4902/4903/4900/4901 的攻擊者視角概覽，搭配 VMSA-2017-0006 一起讀。

---

Ch 34 把 SVGA / mks 從硬體模擬架構、FIFO 協定、surface 狀態機、歷史漏洞分類到 exploit chain 全部打通。下一章把這個攻擊面的知識應用到具體的 case study——帶你逐步拆解一個 Pwn2Own 等級的 VMware escape 的完整過程。

→ [Ch 35](./35-vmware-escape-case.md)
