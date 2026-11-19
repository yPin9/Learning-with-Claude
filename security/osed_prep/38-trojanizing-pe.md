# Ch 38 — 手工 trojanize 合法 PE：插入 shellcode + 還原執行流

> **目標**：把 Ch 37 的原理落到 `pefile` 的實作上——在你自己擁有的一支合法 PE 上，找 code cave、改 entry point、設好 section 權限，並在隔離環境用 WinDbg 驗證「後門先跑、再乾淨跳回 OEP」。重點是 PE 結構的手術與驗證流程，以及反過來的偵測視角。

> **環境**：Win10 x64 22H2（32-bit PE 載體），Python `pefile`、WinDbg（2026）、CFF Explorer/PE-bear 輔助。**只在你自有或明確授權的執行檔與隔離 VM 中操作**；本章用來理解機制與偵測，不涉及對任何第三方散布。

> **本章定位**：OSED 官方模組「Trojanizing PE Files」。本頁示範 **PE 結構的程式化修改（找 cave、改 entry、調權限、存檔）與驗證/偵測方法**；注入的 shellcode 本體沿用 Part 6（由你在自有環境產生），本頁不內嵌可直接開火的完整 payload bytes——那無助於理解 PE 手術本身。

## 為什麼用 pefile？

Ch 37 講了原理（繞路再回家、code cave、改 entry、還原流程、section 權限）。要把這些落到一支真實檔案上，你需要能精準讀寫 PE 欄位的工具。`pefile` 是純 Python 的 PE 解析/修改庫，不需要 Windows 就能讀 PE 結構（改完的檔要在 Windows 上驗證執行）。它讓你用程式把 Ch 37 的每一步做出來、而且可重現——這對寫報告（Ch 39）很重要。

```
pefile 負責的手術：
  讀 header / section → 找 code cave → 寫入 stub → 改 AddressOfEntryPoint
  → 設 section 可執行 → 存檔
WinDbg 負責的驗證：
  在新 entry 下斷 → 單步看 stub 跑 → 確認乾淨跳回 OEP → 程式功能正常
```

## 先建立直覺：這是一場「不改變佈局」的微創手術

Ch 37 強調過：你不能在檔案中間 insert bytes（會位移所有 offset、檔案報廢）。所以整場手術的原則是**只在既有空間動刀、只改必要欄位**：

```
動 cave 裡的補零區（不改檔案大小、不位移任何東西）
動 AddressOfEntryPoint（一個欄位）
動目標 section 的 Characteristics（一個欄位）
——其餘一律不碰
```

這也是為什麼 code cave 路線比「新增 section」乾淨：前者連檔案大小都不變。

## 核心一：用 pefile 讀出手術需要的座標

開啟你自有的載體（例如一支你自己編的 `hello.exe`），把 Ch 37 需要的數字讀出來：

```python
import pefile
pe = pefile.PE("hello.exe")

image_base = pe.OPTIONAL_HEADER.ImageBase
oep_rva    = pe.OPTIONAL_HEADER.AddressOfEntryPoint   # ★ 原 OEP，先抄下來
print(hex(image_base), hex(oep_rva))

for s in pe.sections:
    name   = s.Name.decode(errors="ignore").strip("\x00")
    vsize  = s.Misc_VirtualSize
    rawsz  = s.SizeOfRawData
    cave   = rawsz - vsize            # 對齊補零區的大小（粗估 cave）
    print(f"{name:8} RVA={hex(s.VirtualAddress)} "
          f"vsize={hex(vsize)} raw={hex(rawsz)} cave≈{hex(max(cave,0))} "
          f"exec={bool(s.Characteristics & 0x20000000)}")
```

> **未實測**：我無法在本機對某支實際 `hello.exe` 跑出數字（需要那支檔）。`pefile` 的這些屬性名來自其官方文件。`SizeOfRawData − VirtualSize` 是常用的粗估 cave 大小法——真正可用的連續 `0x00` 區段要再掃一次 raw data 確認（見下）。`0x20000000` 是 `IMAGE_SCN_MEM_EXECUTE`（Ch 37）。

精確找 cave（掃某 section 的 raw data 裡夠長的連續 `\x00`）：

```python
def find_cave(pe, section, min_len=0x40):
    data = section.get_data()
    run_start, run = None, 0
    for i, b in enumerate(data):
        if b == 0:
            run = run + 1 if run else 1
            if run == 1: run_start = i
            if run >= min_len:
                rva = section.VirtualAddress + run_start
                return rva, run_start          # 回傳 cave 的 RVA 與 section 內 offset
        else:
            run = 0
    return None, None
```

> **為什麼要 `min_len`？** 你的 stub（Ch 37 的 pushad/pushfd + payload + popfd/popad + 跳回 OEP）需要一段連續空間。先估你的 stub 大小，`min_len` 設成略大於它，才找得到塞得下的 cave。payload 太大就得串多個 cave 或改用新 section（Ch 37 對比表）。

## 核心二：組出 stub（結構，payload 來自 Part 6）

stub 的結構 Ch 37 講過，這裡只確立「它由哪幾塊拼成」，實際 payload bytes 由你在自有環境用 Part 6 產生：

```
stub = [ 保存狀態 pushad/pushfd ]
     + [ 你的 payload ]              ← Ch 25/26 產出的 shellcode（如 reverse shell）
     + [ 還原狀態 popfd/popad ]
     + [ 跳回 OEP 的指令 ]            ← 目標 = image_base + oep_rva（執行期 VA）
```

> **跳回 OEP 的位址怎麼算？** OEP 的執行期 VA = `ImageBase + oep_rva`（ASLR 下載入器會整體平移，但「跳回 OEP」常用相對跳轉或在 stub 內動態計算，避免寫死絕對位址）。最單純的教學作法：把 `ImageBase + oep_rva` 推上 stack 後 `ret`。注意這個絕對位址在開 ASLR 的模組上會失準——所以 PE 後門常偏好相對跳轉，或接受「關 ASLR 的載體」這個教學簡化（並誠實標注）。

> **本頁不內嵌 payload bytes**：pushad/pushfd/popfd/popad/跳轉是公開的 x86 指令；中間的 payload 請用你 Part 6 的 shellcode。把兩者拼接、組譯成 bytes 的動作在你自有環境完成。

## 核心三：把 stub 寫進 cave、改 entry、設權限、存檔

有了 cave 的 RVA/offset 和 stub bytes，用 pefile 完成手術：

```python
# cave_rva, cave_off 來自 find_cave；stub_bytes 是你組好的 stub（含 Part 6 payload）
# 1) 把 stub 寫進 cave（用 RVA 對應的 file offset 寫 raw data）
cave_file_off = pe.get_offset_from_rva(cave_rva)
pe.set_bytes_at_offset(cave_file_off, stub_bytes)

# 2) 改 entry point 指向 cave
pe.OPTIONAL_HEADER.AddressOfEntryPoint = cave_rva      # 原 oep_rva 已抄下，stub 會跳回

# 3) 確保 cave 所在 section 可執行（.text 本就有；資料 section 要補）
for s in pe.sections:
    if s.contains_rva(cave_rva):
        s.Characteristics |= 0x20000000                 # IMAGE_SCN_MEM_EXECUTE
        s.Characteristics |= 0x40000000                 # IMAGE_SCN_MEM_READ

# 4) 存成新檔（別覆蓋原檔，方便對照）
pe.write("hello_patched.exe")
```

> **未實測，理論預期**：`get_offset_from_rva`/`set_bytes_at_offset`/`contains_rva`/`write` 是 pefile 官方 API。實際能否成功取決於 cave 夠不夠大、stub 是否正確。改完務必在隔離 VM 驗證（下一節）——不要假設 pefile 寫完就一定能跑。

## 核心四：在 WinDbg 驗證「後門跑完乾淨回家」

這是最關鍵、也最容易出錯的驗證。在隔離 VM 裡用 WinDbg 啟動 `hello_patched.exe`：

```
1. 啟動時停在 entry point（現在=cave 的 VA）
2. u eip                 確認這裡是你的 stub（pushad... 開頭）
3. 單步（t）走過 stub：
     - pushad/pushfd 後，暫存器/旗標被保存
     - payload 執行（reverse shell 連回 / calc 彈出）
     - popfd/popad 後，暫存器/旗標還原成進來時的值 ← 對照 push 前後是否一致
     - 跳回 OEP：確認 EIP 落到 image_base+oep_rva，且那裡是原程式的正常起點
4. g 放行：原程式功能照常（hello 正常印出），使用者無感
```

> **為什麼要逐步比對暫存器還原？** Ch 37 的頭號雷就是「沒還原狀態就跳回 OEP，原程式用亂掉的 CPU 狀態續跑而崩潰」。在 WinDbg 單步，把 `pushad` 前和 `popad` 後的暫存器值對一遍——完全一致，才證明你的「存/還原」對稱無誤。這一步省不得。

> **未實測**：上述流程需 Windows + WinDbg。驗證紀律：**先確認 stub 跑通且乾淨回家，再談 payload 行為**。若 payload 是會阻塞的同步 reverse shell，原程式會卡在那裡——實務上把 payload 丟到新執行緒（Ch 37 進階）避免卡住主流程。

## 對比與取捨

| 決策 | code cave | 新增 section |
|---|---|---|
| 檔案大小 | 不變 | 變大 |
| 空間 | 受 cave 限制（常數十~數百 B） | 任意 |
| 隱蔽性 | 高 | 低（多一個怪 section） |
| pefile 操作 | 寫既有 raw + 改兩欄位 | 要新增 section header + 調 SizeOfImage |
| 偵測難度（對防守方） | 較難（無結構異常） | 較易（entry 指向新 section 很可疑） |

## 踩雷集錦

1. **「改完 entry 忘了讓 stub 跳回 OEP」**：程式跑完你的 stub 就沒有然後了，原功能不執行、一啟動就異常退出。先抄 OEP、stub 結尾跳回去——這是 Ch 37 就強調、實作最常漏的一步。
2. **「cave 其實沒那麼大，stub 寫爆了」**：`SizeOfRawData − VirtualSize` 只是粗估，中間不一定全是連續零。一定用 `find_cave` 掃出「真正連續的 `\x00` 長度」，確認 ≥ stub 大小再寫，否則會蓋到真實資料。
3. **「寫進 .data 卻忘了它不可執行」**：DEP 下跳過去執行=存取違規。cave 在非執行 section 時要補 `IMAGE_SCN_MEM_EXECUTE`。放 `.text` 本就可執行最省事。
4. **「用 RVA 當 file offset 去 set_bytes」**：RVA≠檔案 offset（Ch 6）。寫 raw data 要先 `get_offset_from_rva` 換算，直接拿 RVA 寫會寫錯位置。
5. **「跳回 OEP 寫死絕對位址，開 ASLR 就崩」**：`ImageBase+oep_rva` 在 ASLR 下會平移。教學階段可用關 ASLR 的載體簡化（並標注），正式則用相對跳轉或 stub 內動態計算。

## 進階：再往深一層

- **把 payload 丟新執行緒**：同步 payload 會卡住原程式主流程（露餡）。stub 先 `CreateThread` 跑 payload、主流程立刻回 OEP，程式表面完全正常——這是後門隱蔽的關鍵。
- **TLS callback 落點**：比改 entry point 更隱蔽（在 entry 之前就執行），也更難被「只看 entry point」的偵測抓到。Ch 37 進階提過，深入見 `windows_pwn`。
- **偵測面（反過來用，重要）**：你做完這場手術，就知道該找什麼異常來偵測它：
  - entry point 不在典型 `.text` 範圍、或指向檔案尾端新 section
  - section 同時可寫又可執行（WX）
  - 檔案尾端多出高熵 section（塞了加密/編碼 payload）
  - PE checksum 與實際不符、`AddressOfEntryPoint` 與編譯器慣例不合
  - 寫成 YARA 規則 / 完整性驗證就靠這些特徵。本 repo [`blue_team_dfir`](../blue_team_dfir/README.md) 從這端展開。
- **AV 為何抓得到（概念）**：靜態靠簽章（已知 stub/payload 樣式）與啟發式（怪 entry、WX section、高熵）；這解釋了為何要編碼 payload（Ch 26）改變樣式——但本課談原理與偵測，不是針對特定產品的規避食譜。

## 本章重點整理

- trojanize 是「不改佈局的微創手術」：只動 cave、`AddressOfEntryPoint`、目標 section 的 Characteristics。
- pefile 流程：讀 header/section → `find_cave` 掃真正連續零 → 寫 stub（`get_offset_from_rva`+`set_bytes_at_offset`）→ 改 entry → 設 `IMAGE_SCN_MEM_EXECUTE` → `write`。
- stub = pushad/pushfd + payload（Part 6）+ popfd/popad + 跳回 OEP；先抄 OEP。
- 必在 WinDbg 單步驗證「暫存器還原對稱 + 乾淨跳回 OEP + 原功能正常」。
- 看懂手術=看得懂偵測：異常 entry、WX section、高熵、checksum 不符都是防守特徵。

## 自我檢核

- [ ] 不看筆記，能說出 pefile 手術的五個步驟與各改了什麼欄位
- [ ] 能解釋為什麼要 `get_offset_from_rva` 而不能直接用 RVA 寫檔
- [ ] 能說出 `find_cave` 為什麼不能只信 `SizeOfRawData − VirtualSize`
- [ ] 知道在 WinDbg 要比對什麼來證明「狀態還原對稱」
- [ ] 能從防守端列出至少三個偵測這類 PE 後門的特徵

## 延伸閱讀

- **[pefile 官方文件 / GitHub](https://github.com/erocarrera/pefile)**
  - **讀哪裡**：`get_offset_from_rva`、`set_bytes_at_offset`、`sections`、`write` 的 API 說明。
  - **學什麼**：本章每個 pefile 呼叫的權威用法。
  - **前提**：Ch 6 的 PE 結構、Ch 37 的原理。

- **[PE Format — Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/debug/pe-format)**
  - **讀哪裡**：Optional Header（entry point）、Section Table（Characteristics 旗標常數）。
  - **學什麼**：`AddressOfEntryPoint`、`IMAGE_SCN_MEM_EXECUTE` 等的權威定義。

- **[`blue_team_dfir` — PE 異常 / 持久化偵測](../blue_team_dfir/README.md) 與 [`malware_analysis`](../malware_analysis/README.md)**
  - **讀哪裡**：entry point 劫持、code cave、packer/高熵偵測相關章節。
  - **和本章的關聯**：本章是攻擊面手術，那兩門是對應的偵測與鑑識——兩面一起看才完整，也符合本課「理解即防禦」的定位。

- **OffSec EXP-301「Trojanizing PE Files」模組**
  - **讀哪裡**：官方教材對應模組的實作步驟。
  - **注意**：以你手上的官方課程版本為準；本章是其原理+pefile 實作+偵測的補充版。

Part 10 完成。接下來是 Part 11「加深一圈」：先看 OSED 為何鎖定 x86、真實世界的 x64 差在哪，再看 CFG/CET 這些現代緩解如何衝擊你學的整套技法——把你從「考得過」推到「看得懂這套技法的時代邊界」。

→ [Ch 39 OSED 考試作戰：時間分配、報告、常見失分](./39-osed-exam-strategy.md)
