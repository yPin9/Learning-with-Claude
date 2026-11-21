# Ch 55 冷讀實戰二：一個 driver 的 probe 函式

> **目標**：冷讀一個同時有 clock、reset、IRQ、DMA 和 runtime PM 的 platform driver：用 [Ch 43](./43-driver-model.md) 的四步法畫出骨架，再用「資源帳本」逐項核對 probe 拿到的每一樣東西，在錯誤路徑和 remove 裡有沒有按正確順序還回去。最後用 v6.18 之後的上游修正對答案。
> **鴻溝**：本章主要練**領域鴻溝**（clock／reset／DMA engine／runtime PM／devres 各自的規則）與**脈絡鴻溝**（driver core 在 unbind 時的呼叫順序），順帶練**慣用法鴻溝**（goto 階梯、`devm_*`）。

> **環境**：WSL Ubuntu 22.04、Linux kernel tree 在 tag v6.18（`~/code_reading/linux`）。同一個 clone 裡也有 `origin/master`（比 v6.18 新），本章最後會用 `git show` 看 v6.18 之後的修正，但**不 checkout**。本章所有指令都實際跑過；引用的程式碼一律標 v6.18 的 `file:line`。沒有 Allwinner 硬體，所以本章是純靜態閱讀。

## 為什麼需要這個？

[Ch 43](./43-driver-model.md) 讀的 hisi-rng 只有 MMIO，probe 十幾行，沒有 `.remove`，因為它拿的東西全部是 `devm_*`，driver core 會自動還。真實的 SoC driver 很少這麼乾淨。一個 SPI 控制器要能動，至少需要：

- 一段 MMIO（暫存器）
- 一條中斷線（傳完了要通知 CPU）
- 一到兩個 clock（匯流排時脈、模組時脈）
- 一條 reset 線（上電後要把它從 reset 狀態放出來）
- 兩個 DMA channel（大量資料不想讓 CPU 一個 byte 一個 byte 搬）
- runtime PM（閒置時關 clock 省電）

六種資源，各有各的取得函式、釋放函式、「拿不到時怎麼辦」的規則，而且彼此有**順序依賴**：clock 沒開，暫存器讀不到；DMA channel 釋放了，就不能再發起傳輸。probe 是這些資源一次全部拿到手的地方，remove 是全部還回去的地方。

冷讀 probe 時最常見的失敗是：一行一行讀懂了，每個函式都查過了，但沒注意到**順序**。而 driver 的 bug 有很大一部分就是順序 bug：錯誤路徑漏還一樣東西、還的順序反了、`devm_*` 和手動釋放混用造成某樣東西在它的使用者之前被釋放。這一章的主角就有一個這樣的問題，而且它在 v6.18 之後被上游修掉了，可以拿來對答案。

## 先建立直覺：資源帳本

把 probe 想成開店：租店面（MMIO）、接電話線（IRQ）、開電源（clock）、拉起鐵捲門（reset deassert）、跟物流簽約（DMA），最後掛招牌開門營業（register）。remove 是關店，**順序要反過來**：先拿下招牌（不再接新客人），再解約、拉下鐵捲門、斷電。如果先斷電再拿招牌，中間那段時間客人還會走進一間黑漆漆的店。

讀 probe 的工具是一張**資源帳本**，每拿到一樣東西就記一行：

```
  #  │ 資源         │ 取得（probe）           │ 釋放者        │ 釋放時機
 ────┼──────────────┼─────────────────────────┼───────────────┼─────────────────
  1  │ controller   │ spi_alloc_host  :636    │ ?             │ ?
  2  │ MMIO         │ devm_..._ioremap :645   │ devres        │ unbind 最後
  …  │              │                         │               │
```

帳本填完，檢查三件事：

1. **錯誤路徑**：probe 在第 k 步失敗時，第 1 到 k−1 步拿到的東西都還了嗎？有沒有多還？
2. **remove**：每一樣非 devres 的東西都在 remove 裡還了嗎？
3. **順序**：釋放順序是不是取得順序的反向？特別是 devres 和手動釋放**混用**時，兩者的相對順序是什麼？

第三點是本章的重點。比喻的限制在這裡：開店的人自己決定關店順序，但 driver 裡有一半的釋放動作（devres）不是 driver 自己呼叫的，是 driver core 在 `.remove` **回傳之後**才做。不知道這件事，就看不出順序錯在哪。

## 選題：怎麼找一個「夠複雜」的 probe

條件是 probe 裡同時有 clock、reset、IRQ、DMA。用 `rg` 疊條件，再按檔案大小排序，取最小的：

```
$ rg -l "devm_reset_control_get" drivers/spi | xargs rg -l "dma_request_chan" \
    | xargs rg -l "devm_request_irq" | xargs rg -l "devm_clk_get\b|devm_clk_get\(" \
    | xargs wc -l | sort -n | head -8
   825 drivers/spi/spi-sun6i.c
   965 drivers/spi/spi-stm32-qspi.c
  2241 drivers/spi/spi-cadence-quadspi.c
  4031 total
```

`drivers/spi/spi-sun6i.c`：Allwinner A31／H3／R329 等 SoC 的 SPI 控制器 driver，825 行，probe 從第 629 行到第 766 行。第二名 `spi-stm32-qspi.c` 留到最後當對照組。

## 望診（10 分鐘，不用工具）

先只看 probe 的形狀，不追任何函式。把 probe 的每一段濃縮成一行（`drivers/spi/spi-sun6i.c:629–766`，v6.18）：

```
:636  host = spi_alloc_host(...)                       ← 沒有 devm_
:642  platform_set_drvdata(pdev, host)
:645  devm_platform_get_and_ioremap_resource(...)      ← MMIO
:651  irq = platform_get_irq(pdev, 0); if (irq < 0) ret = -ENXIO   ← 咦？
:657  devm_request_irq(..., sun6i_spi_handler, ..., sspi)
:665  sspi->cfg = of_device_get_match_data(...)
:666–678  host->xxx = ... 一長串設定，包括 auto_runtime_pm = true
:680  devm_clk_get("ahb")      :687  devm_clk_get("mod")
:697  devm_reset_control_get_exclusive(...)
:704  dma_request_chan("tx")   :715  dma_request_chan("rx")    ← 沒有 devm_
:735  sun6i_spi_runtime_resume(&pdev->dev)              ← 直接呼叫 PM callback？
:741–744  pm_runtime_*  四行
:746  devm_spi_register_controller(...)                 ← 又是 devm_
:752  return 0;
:754–765  err_pm_disable / err_free_dma_rx / err_free_dma_tx / err_free_host
```

```
[望診 sun6i_spi_probe]
- devm 和非 devm 混用：host 和 DMA 是手動，其他是 devm，register 又是 devm
  → 帳本的第三項檢查一定有東西可看
- :651 platform_get_irq 回傳負值時，丟掉原本的錯誤碼，改成 -ENXIO → 可疑
- 對照 :707：DMA 那邊很小心地把 -EPROBE_DEFER 傳上去 → 同一個 probe 兩種態度
- :735 probe 直接呼叫自己的 runtime_resume，然後才 set_active + enable
  → 有一段註解（:731–734），之後讀
- 取得 clock 和 reset 時只是 get，沒有 enable/deassert → enable 在 runtime_resume 裡
- goto 階梯四層，第一層 err_pm_disable 會呼叫 runtime_suspend
- remove（:768）只有三件事：force_suspend、釋放兩個 DMA channel
  → controller 的 unregister 交給 devm；它和 remove 誰先誰後？
```

初步診斷：

| 鴻溝 | 卡點 | 位置 |
|---|---|---|
| 慣用法 | `devm_*` 與手動混用的釋放順序 | `:636`、`:704`、`:746`、`:768` |
| 領域 | `-EPROBE_DEFER` 是什麼、誰會回傳它 | `:651–655`、`:707` |
| 領域 | runtime PM：`set_active`、`enable`、`auto_runtime_pm`、`force_suspend` | `:677`、`:735–744`、`:772` |
| 領域 | DMA engine：`dma_request_chan`、slave config、`dma_addr_tx` 為什麼是實體位址 | `:704–729` |
| 脈絡 | unbind 時 driver core 呼叫 `.remove` 和釋放 devres 的先後 | `drivers/base/dd.c` |
| 目標 | — | 下面定義 |

**問題**：probe 拿到的每一樣資源，在**錯誤路徑**和 **unbind** 時，是不是都按正確順序還回去了？

## 檢查（25 分鐘）：四步法 + 資源帳本

### ① driver struct

`drivers/spi/spi-sun6i.c:795–820`：

```c
static const struct of_device_id sun6i_spi_match[] = {
	{ .compatible = "allwinner,sun6i-a31-spi", .data = &sun6i_a31_spi_cfg },
	{ .compatible = "allwinner,sun8i-h3-spi",  .data = &sun8i_h3_spi_cfg },
	{
		.compatible = "allwinner,sun50i-r329-spi",
		.data = &sun50i_r329_spi_cfg
	},
	{}
};
MODULE_DEVICE_TABLE(of, sun6i_spi_match);

static const struct dev_pm_ops sun6i_spi_pm_ops = {
	.runtime_resume		= sun6i_spi_runtime_resume,
	.runtime_suspend	= sun6i_spi_runtime_suspend,
};

static struct platform_driver sun6i_spi_driver = {
	.probe	= sun6i_spi_probe,
	.remove = sun6i_spi_remove,
	.driver	= {
		.name		= "sun6i-spi",
		.of_match_table	= sun6i_spi_match,
		.pm		= &sun6i_spi_pm_ops,
	},
};
module_platform_driver(sun6i_spi_driver);
```

platform bus，靠 DT 的 compatible 配對。三個 compatible 共用同一份程式碼，差別放在 `.data` 指向的 `sun6i_spi_cfg`（FIFO 深度、有沒有 clock 控制暫存器、支援的 mode）。probe 第 665 行的 `of_device_get_match_data()` 就是把這個 `.data` 拿回來。這是 [Ch 45](./45-design-patterns-in-kernel.md) 講的「用資料表區分硬體變體」，比在程式碼裡寫 `if (is_h3)` 好讀得多。

`.pm` 只有 runtime 的兩個 callback，沒有 system sleep 的 `suspend`／`resume`。記下來，等一下 `force_suspend` 會用到。

### ② probe：逐項填帳本

**MMIO、IRQ（:645–662）**

```c
	sspi->base_addr = devm_platform_get_and_ioremap_resource(pdev, 0, &mem);
	if (IS_ERR(sspi->base_addr)) {
		ret = PTR_ERR(sspi->base_addr);
		goto err_free_host;
	}

	irq = platform_get_irq(pdev, 0);
	if (irq < 0) {
		ret = -ENXIO;
		goto err_free_host;
	}

	ret = devm_request_irq(&pdev->dev, irq, sun6i_spi_handler,
			       0, "sun6i-spi", sspi);
```

`mem` 是 out 參數，拿到 MMIO 的 `struct resource`，後面算 DMA 位址會用到。

望診時標記的 `ret = -ENXIO`，現在查證。`platform_get_irq()`（`drivers/base/platform.c:260`）只是 `platform_get_irq_optional()` 加一行錯誤訊息；後者在 device tree 系統上的路徑（`drivers/base/platform.c:184–187`）：

```c
	if (is_of_node(fwnode)) {
		ret = of_irq_get(to_of_node(fwnode), num);
		if (ret > 0 || ret == -EPROBE_DEFER)
			goto out;
	}
```

`of_irq_get()` 在中斷控制器的 driver 還沒 probe 好時回傳 `-EPROBE_DEFER`，意思是「現在拿不到，晚點再試」。driver core 看到 probe 回傳 `-EPROBE_DEFER`，會把這個 device 放進延後佇列，等其他 driver probe 成功後再重試。**把它改寫成 `-ENXIO`，等於告訴 driver core「永久失敗」，這個 device 就不會再被重試。**

同一個 probe 在第 707 行處理 DMA 時卻很小心：

```c
	host->dma_tx = dma_request_chan(&pdev->dev, "tx");
	if (IS_ERR(host->dma_tx)) {
		/* Check tx to see if we need defer probing driver */
		if (PTR_ERR(host->dma_tx) == -EPROBE_DEFER) {
			ret = -EPROBE_DEFER;
			goto err_free_host;
		}
		dev_warn(&pdev->dev, "Failed to request TX DMA channel\n");
		host->dma_tx = NULL;
	}
```

**同一個函式對同一種錯誤碼有兩種態度**，這種不一致本身就是冷讀時值得記下的訊號。實際影響有多大？Allwinner SoC 上 SPI 的中斷接在 GIC，而 GIC 在開機很早期就初始化，實務上這裡不太會遇到 `-EPROBE_DEFER`，所以這是一個「違反慣例、但通常不會觸發」的問題。慣例的寫法是 `return irq;` 或 `ret = irq;`，第二名 `spi-stm32-qspi.c` 就是這樣寫的（`drivers/spi/spi-stm32-qspi.c:789–791`）。

**clock、reset（:680–702）**

```c
	sspi->hclk = devm_clk_get(&pdev->dev, "ahb");
	...
	sspi->mclk = devm_clk_get(&pdev->dev, "mod");
	...
	sspi->rstc = devm_reset_control_get_exclusive(&pdev->dev, NULL);
```

只有 **get**，沒有 enable。clock 和 reset 都是「兩段式」資源：get 拿到一個 handle（devres 會在 unbind 時 put），enable／deassert 才是真的讓硬體動起來。這兩段由不同的人負責：get 在 probe，enable／deassert 在 `sun6i_spi_runtime_resume()`（`:566–601`）：

```c
	ret = clk_prepare_enable(sspi->hclk);       /* 匯流排時脈 */
	...
	ret = clk_prepare_enable(sspi->mclk);       /* 模組時脈 */
	...
	ret = reset_control_deassert(sspi->rstc);   /* 放出 reset */
	...
	sun6i_spi_write(sspi, SUN6I_GBL_CTL_REG,
			SUN6I_GBL_CTL_MASTER | SUN6I_GBL_CTL_TP);
	return 0;

err2:
	clk_disable_unprepare(sspi->mclk);
err:
	clk_disable_unprepare(sspi->hclk);
```

順序合理：先開匯流排時脈（不然暫存器存取不到），再開模組時脈，再放 reset，最後寫第一個暫存器。`runtime_suspend`（`:603–613`）正好反過來：assert reset、關 mclk、關 hclk。這一對是**對稱的**，在帳本上記成「runtime PM 負責」。

**DMA（:704–729）**

兩個 channel 都是可選的：拿不到（而且不是 `-EPROBE_DEFER`）就設成 `NULL`，退回 PIO 模式。兩個都拿到才設 `can_dma`：

```c
	if (host->dma_tx && host->dma_rx) {
		sspi->dma_addr_tx = mem->start + SUN6I_TXDATA_REG;
		sspi->dma_addr_rx = mem->start + SUN6I_RXDATA_REG;
		host->can_dma = sun6i_spi_can_dma;
	}
```

`mem->start` 是 MMIO 的**實體**位址，不是 `base_addr`（ioremap 後的虛擬位址）。因為 DMA 控制器是另一個硬體，它看的是匯流排位址，不經過 CPU 的 MMU。這兩個位址之後在 `sun6i_spi_prepare_dma()` 裡填進 `dma_slave_config.src_addr`／`dst_addr`（`:218–245`）。這是典型的**領域鴻溝**：不知道 DMA 控制器看什麼位址，就會覺得這裡「用錯變數」。

`dma_request_chan()` 不是 devm 版本，所以帳本上記：「手動，錯誤路徑和 remove 要 `dma_release_channel`」。

**runtime PM（:731–744）**

```c
	/*
	 * This wake-up/shutdown pattern is to be able to have the
	 * device woken up, even if runtime_pm is disabled
	 */
	ret = sun6i_spi_runtime_resume(&pdev->dev);
	...
	pm_runtime_set_autosuspend_delay(&pdev->dev, SUN6I_AUTOSUSPEND_TIMEOUT);
	pm_runtime_use_autosuspend(&pdev->dev);
	pm_runtime_set_active(&pdev->dev);
	pm_runtime_enable(&pdev->dev);
```

這是 runtime PM 的標準開機舞步，讀懂要三條領域知識（`Documentation/power/runtime_pm.rst`）：

1. runtime PM 預設是**停用**的，device 的狀態預設是 suspended。
2. 如果 kernel 沒開 `CONFIG_PM`，runtime PM 的 callback 永遠不會被呼叫。所以 driver 自己先**手動**呼叫一次 `runtime_resume`，保證不管有沒有 runtime PM，硬體都是開著的（這就是註解的意思）。
3. 手動開完之後用 `pm_runtime_set_active()` 告訴 PM core「它現在是開的」，再 `pm_runtime_enable()`。之後閒置 2000 ms（`SUN6I_AUTOSUSPEND_TIMEOUT`）就會自動 suspend。

`host->auto_runtime_pm = true`（`:677`）讓 SPI core 在每次傳輸前後自動做 get／put，driver 自己不用管。證據在 `drivers/spi/spi.c:1732–1745`：

```c
	if (!was_busy && ctlr->auto_runtime_pm) {
		ret = pm_runtime_get_sync(ctlr->dev.parent);
		if (ret < 0) {
			pm_runtime_put_noidle(ctlr->dev.parent);
			dev_err(&ctlr->dev, "Failed to power device: %d\n",
				ret);
			...
```

注意是 `ctlr->dev.parent`，也就是 platform device，正是 `sun6i_spi_pm_ops` 掛的那個 device。

**register（:746）**

```c
	ret = devm_spi_register_controller(&pdev->dev, host);
```

devm 版本。它做了什麼（`drivers/spi/spi.c:3398–3416`）：先 `devres_alloc(devm_spi_unregister, ...)`，註冊成功後 `devres_add()`。也就是說，unbind 時 devres 會呼叫 `devm_spi_unregister()` → `spi_unregister_controller()`。

### 帳本填完

| # | 資源 | 取得 | 錯誤路徑 | unbind 時的釋放者 |
|---|---|---|---|---|
| 1 | controller（含 `sspi`） | `spi_alloc_host` `:636` | `spi_controller_put` `:764` | `spi_unregister_controller` 的最後一個 `put_device`（`drivers/spi/spi.c:3476–3477`） |
| 2 | MMIO | `devm_..._ioremap` `:645` | devres | devres |
| 3 | IRQ | `devm_request_irq` `:657` | devres | devres |
| 4 | clock handle ×2 | `devm_clk_get` `:680`、`:687` | devres | devres |
| 5 | reset handle | `devm_reset_control_get_exclusive` `:697` | devres | devres |
| 6 | DMA tx／rx | `dma_request_chan` `:704`、`:715` | `dma_release_channel` `:757–762` | `remove` `:774–777` |
| 7 | clock on + reset deassert | `runtime_resume` `:735` | `err_pm_disable` 裡的 `runtime_suspend` `:756` | `remove` 的 `pm_runtime_force_suspend` `:772` |
| 8 | runtime PM enable | `pm_runtime_enable` `:744` | `pm_runtime_disable` `:755` | `force_suspend` 內部會 disable |
| 9 | SPI controller 註冊 | `devm_spi_register_controller` `:746` | （失敗就沒註冊） | devres → `spi_unregister_controller` |

**錯誤路徑檢查**：goto 階梯（`:754–765`）由下往上依序是 put host ← 放 tx ← 放 rx ← 關 PM。對照取得順序：register 失敗跳 `err_pm_disable`（撤銷 7、8，再放 6、1）；runtime_resume 失敗跳 `err_free_dma_rx`（第 7 項沒成功，不用撤銷）；rx 的 `-EPROBE_DEFER` 跳 `err_free_dma_tx`（只放 tx）。每一層都剛好撤銷「之前成功的」，devres 的部分 driver core 會在 probe 失敗後自動釋放。**錯誤路徑是對的。**

**unbind 檢查**：第 6、7 項在 `remove` 裡處理，第 9 項由 devres 處理。問題是：`remove` 和 devres，**誰先？**

### 脈絡：driver core 的 unbind 順序

這要讀 driver core。`__device_release_driver()`（`drivers/base/dd.c:1243`），只留關鍵幾行：

```c
		pm_runtime_put_sync(dev);

		device_remove(dev);                     /* :1274 → drv->remove() */

		if (dev->bus && dev->bus->dma_cleanup)
			dev->bus->dma_cleanup(dev);

		device_unbind_cleanup(dev);             /* :1279 → devres_release_all() */
```

`device_unbind_cleanup()` 的第一行就是 `devres_release_all(dev)`（`drivers/base/dd.c:550`）。所以順序是：

```
  echo <dev> > /sys/bus/platform/drivers/sun6i-spi/unbind
        │
        ▼
  ① sun6i_spi_remove()                    ← driver 自己寫的
        ├─ pm_runtime_force_suspend()      clock 關掉、reset assert
        ├─ dma_release_channel(tx)         DMA channel 還回去
        └─ dma_release_channel(rx)
        │
        ▼
  ② devres_release_all()  （LIFO：後登記的先釋放）
        ├─ devm_spi_unregister()           ← 第 746 行登記，最後登記，最先釋放
        │     └─ spi_unregister_controller()
        │           ├─ 解除所有 child spi device（child driver 的 remove 在這裡跑）
        │           ├─ spi_destroy_queue()  停掉傳輸佇列
        │           └─ put_device()         controller 和 sspi 的記憶體在這裡釋放
        ├─ reset handle put
        ├─ clock handle put ×2
        ├─ free_irq()                      ← 第 657 行登記
        └─ iounmap
```

問題出來了：**① 和 ② 之間，SPI controller 還是註冊著的。** clock 已經關了、DMA channel 已經還了，但 SPI core 仍然認為這個 controller 可以用，掛在它底下的 child device（例如一顆 SPI flash）也還在。在 ② 的 `spi_unregister_controller()` 解除 child device 時，child driver 的 remove 可能還要對硬體下最後一個命令；使用者空間如果正透過 `spidev` 傳輸，佇列裡也可能還有訊息。這些傳輸要嘛撞上關掉的硬體，要嘛用到已經還回去的 DMA channel。

這正是比喻裡「先斷電再拿招牌」的情況。帳本第 9 項（register，devm）在時間上被排到第 6、7 項（手動，remove）**之後**才撤銷，違反了「反向釋放」原則。

還有一個次要的順序問題：② 裡 `spi_unregister_controller()` 的 `put_device()` 會釋放 `sspi` 的記憶體，而 `free_irq()` 排在它後面。如果在這兩步之間有中斷進來，handler 拿到的 `dev_id`（`sspi`）已經被釋放了。因為 ① 已經把硬體 suspend，實際觸發的機率很低，但「釋放 handler 要用的記憶體」排在「拆掉 handler」之前，順序本身是反的。

### ③ 它註冊了什麼介面

回到四步法。第 666–678 行設定了 SPI core 會回呼的介面：

```c
	host->set_cs = sun6i_spi_set_cs;
	host->transfer_one = sun6i_spi_transfer_one;
	...
	host->auto_runtime_pm = true;
	host->max_transfer_size = sun6i_spi_max_transfer_size;
```

加上可能有的 `host->can_dma`。這是 [Ch 28](./28-ops-tables-indirect-calls.md) 的 ops table，只是欄位直接放在 `struct spi_controller` 裡，沒有獨立的 ops struct。誰會呼叫 `transfer_one`：SPI core 的訊息泵（`__spi_pump_transfer_message`），也就是剛才看到做 `pm_runtime_get_sync` 的地方。

### ④ 中斷與 I/O 路徑

`sun6i_spi_transfer_one()`（`:275–527`）很長，但骨架是 [Ch 33](./33-wait-queues-completion.md) 的 completion 模式：

```
transfer_one:
  reinit_completion(&sspi->done)                       :292
  設定 clock 分頻、FIFO 門檻、CS ...
  if (use_dma) sun6i_spi_prepare_dma()                 :465   設好 DMA、submit、issue_pending
  寫 INT_CTL 打開中斷（TC，PIO 時再加 RF_RDY/TF_ERQ）  :484
  寫 TFR_CTL 的 XCH 位元：開始傳                      :488
  wait_for_completion_timeout(&sspi->done, ...)        :492   ← 睡在這裡
  DMA 模式：再等 dma_rx_done（RX DMA 可能還在搬）       :503
  關中斷                                               :519
  逾時且用 DMA → dmaengine_terminate_sync ×2           :522–523

sun6i_spi_handler（硬體中斷）:529
  TC（傳輸完成）   → 清狀態、complete(&sspi->done)
  RF_RDY（RX 3/4 滿）→ drain FIFO，「之後」才清狀態
  TF_ERQ（TX 3/4 空）→ fill FIFO，沒資料就關這個中斷，「之後」才清狀態
```

handler 裡兩個註解（「Only clear the interrupt _after_ ...」）是寫給讀者的順序提示：如果先清狀態再搬 FIFO，搬的過程中 FIFO 又到門檻，硬體會再設一次狀態位元，然後被晚一步的清除動作吃掉，中斷就漏了。

四步讀完，骨架一行：**「DT 配對 → 拿 MMIO／IRQ／clock／reset／可選的 DMA → 手動 resume 後交給 runtime PM → 把 `transfer_one` 交給 SPI core → SPI core 每次傳輸時 get PM、呼叫 `transfer_one`，它設定硬體、按 XCH、睡在 completion 上，等中斷 handler 叫醒它。」**

## 寫報告（10 分鐘）

```
[冷讀報告] drivers/spi/spi-sun6i.c:629 sun6i_spi_probe（v6.18）

三句話答案：
1. probe 依序拿 MMIO、IRQ、兩個 clock、reset（devm），兩個可選 DMA channel（手動），
   手動 runtime_resume 一次後交給 runtime PM，最後用 devm 註冊 SPI controller。
2. 錯誤路徑的 goto 階梯正確，每層只撤銷之前成功的步驟。
3. unbind 順序有問題：driver core 先呼叫 remove（關 clock、還 DMA），之後才由 devres
   unregister controller（drivers/base/dd.c:1274 → :1279），中間 controller 仍可被使用。

其他發現：
- :653 把 platform_get_irq 的錯誤碼改寫成 -ENXIO，吞掉 -EPROBE_DEFER；
  同一個 probe 在 :707 對 DMA 卻有正確傳遞。實務影響小（GIC 早期初始化）。
- devres 裡 put_device（釋放 sspi）排在 free_irq 之前。

鴻溝：領域（EPROBE_DEFER、runtime PM 舞步、DMA 用實體位址）；
     脈絡（driver core unbind 順序）是找出主要問題的關鍵

未解問題：
- 上游有沒有修？→ git log v6.18..origin/master -- drivers/spi/spi-sun6i.c
- 其他 SPI driver 有沒有同樣的 pattern？→ rg 找 devm_spi_register_controller
  且 remove 裡有 dma_release_channel 或 force_suspend 的 driver
```

## 對答案：v6.18 之後的上游修正

```
$ git log --oneline v6.18..origin/master -- drivers/spi/spi-sun6i.c
98477036fc35 spi: fix typos in comments
9864636b1cd9 spi: sun6i: switch to managed controller allocation
d874a1c33aee spi: sun6i: fix controller deregistration
3974a585be78 spi: Drop duplicate of_node assignment
c81f30bde5b0 spi: sun6i: Support A523's SPI controllers
```

`d874a1c33aee` 的完整內容：

```
commit d874a1c33aee0d88fb4ba2f8aeadaa9f1965209a
Author: Johan Hovold <johan@kernel.org>
Date:   Fri Apr 10 10:17:49 2026 +0200

    spi: sun6i: fix controller deregistration

    Make sure to deregister the controller before disabling underlying
    resources like clocks during driver unbind.

    Fixes: 3558fe900e8a ("spi: sunxi: Add Allwinner A31 SPI controller driver")
    Cc: stable@vger.kernel.org      # 3.15
    ...
```

```diff
@@ -742,7 +742,7 @@ static int sun6i_spi_probe(struct platform_device *pdev)
 	pm_runtime_set_active(&pdev->dev);
 	pm_runtime_enable(&pdev->dev);
 
-	ret = devm_spi_register_controller(&pdev->dev, host);
+	ret = spi_register_controller(host);
 	if (ret) {
 		dev_err(&pdev->dev, "cannot register SPI host\n");
 		goto err_pm_disable;
@@ -768,12 +768,18 @@ static void sun6i_spi_remove(struct platform_device *pdev)
 {
 	struct spi_controller *host = platform_get_drvdata(pdev);
 
+	spi_controller_get(host);
+
+	spi_unregister_controller(host);
+
 	pm_runtime_force_suspend(&pdev->dev);
 
 	if (host->dma_tx)
 		dma_release_channel(host->dma_tx);
 	if (host->dma_rx)
 		dma_release_channel(host->dma_rx);
+
+	spi_controller_put(host);
 }
```

逐項對照報告：

- **主要問題完全命中。** 修法是把 register 從 devm 改回手動，在 remove 的**第一步** unregister。帳本第 9 項回到正確位置：最後取得、最先釋放。
- **`spi_controller_get()`／`put()` 那兩行是什麼？** `spi_unregister_controller()` 最後會 `put_device()`，對 `spi_alloc_host` 配置的 controller 來說，這是最後一個 reference，`sspi` 會被釋放。但 remove 後面還要用 `host->dma_tx`，所以先多拿一個 reference，用完再放。這兩行證實了報告裡「`put_device` 會釋放 `sspi`」的判斷。
- **Fixes 指向原始 driver**（`3558fe900e8a`，Cc stable 標 3.15），代表這個順序問題從 2014 年 driver 合進 mainline 那天就在了。
- **次要問題（`free_irq` 晚於釋放 `sspi`）由下一個 commit 處理。** `9864636b1cd9` 把 `spi_alloc_host` 換成 `devm_spi_alloc_host`。devres 是 LIFO，`devm_spi_alloc_host` 是 probe 裡**第一個**登記的 devres，所以會**最後**釋放，排到 `free_irq` 之後。commit message 只說「簡化錯誤處理、不用再多拿一個 reference」，沒有提到 IRQ，但順序確實因此變對了。
- **`-ENXIO` 沒有被修。** 在 `9864636b1cd9` 的 diff 裡，那段只是從 `goto err_free_host` 改成 `return -ENXIO`，錯誤碼照舊。報告裡這一項是「仍然存在、影響小」的發現。

最後一個未解問題：這個 pattern 有多普遍？

```
$ git log --oneline v6.18..origin/master --grep="fix controller deregistration" -- drivers/spi | wc -l
56
```

Johan Hovold 在 v7.1 merge window 前後一共送了 56 個同名修正，sun6i 是其中一個。2020 年 Lukas Wunner 也做過同一類的大規模修正（`c371dcf51cef Merge series "Use-after-free be gone"`，v5.10-rc5），當時引入了 `devm_spi_alloc_host` 系列 API。**同一個 pattern 在一個子系統裡反覆出現，代表它不是個別作者的疏忽，而是 API 的設計讓錯誤寫法看起來很自然**：`devm_spi_register_controller` 看起來是「省事版」，但只要 driver 的 remove 裡還有任何手動釋放，它就會把 unregister 排到錯誤的時間點。

### 對照組：spi-stm32-qspi 怎麼寫

選題時的第二名，`drivers/spi/spi-stm32-qspi.c` 的 remove（`:877–892`）：

```c
static void stm32_qspi_remove(struct platform_device *pdev)
{
	struct stm32_qspi *qspi = platform_get_drvdata(pdev);

	pm_runtime_get_sync(qspi->dev);
	spi_unregister_controller(qspi->ctrl);
	/* disable qspi */
	writel_relaxed(0, qspi->io_base + QSPI_CR);
	stm32_qspi_dma_free(qspi);
	...
	clk_disable_unprepare(qspi->clk);
}
```

三個差別：controller 用 `devm_spi_alloc_host` 配置（`:766`）、用非 devm 的 `spi_register_controller` 註冊（`:852`）、remove 第一步就 unregister（`:882`），而且 unregister 前先 `pm_runtime_get_sync` 確保硬體開著。IRQ 那段也是 `if (irq < 0) return irq;`（`:789–791`）。這就是修正後的 sun6i 想變成的樣子。

## 對比與取捨

| 寫法 | 優點 | 缺點 | 什麼時候可以用 |
|---|---|---|---|
| 全部 devm（hisi-rng） | 不用寫 remove，不會漏 | 釋放順序完全交給 LIFO | 所有資源都有 devm 版本，而且沒有手動釋放 |
| devm 資源 + 非 devm register + remove 先 unregister（stm32-qspi、修正後的 sun6i） | 「停止服務」一定是第一步 | 要寫 remove | **只要 remove 裡有任何手動釋放，就用這個** |
| devm register + 手動釋放其他東西（v6.18 的 sun6i） | 看起來省事 | unregister 被排到 remove 之後 | 不要用 |

一條可以背下來的規則：**devm 登記的東西，會在 remove 回傳之後才釋放。所以凡是「依賴 remove 裡手動釋放的資源」的東西，都不能用 devm 登記。** SPI controller 依賴 DMA channel 和 clock，DMA channel 在 remove 裡手動釋放，所以 controller 的註冊不能是 devm。

## 踩雷集錦

- **以為 devm 會在 `.remove` 之前或期間釋放。** 不是。順序是 `drv->remove()` → `devres_release_all()`（`drivers/base/dd.c:1274`、`:1279`）。這是本章主要問題的根源，也是冷讀 driver 時最需要記住的一個脈絡事實。
- **只檢查錯誤路徑，不檢查 unbind。** 這個 driver 的錯誤路徑是對的，問題全在 unbind。帳本要同時填兩欄。
- **把 `mem->start` 當成寫錯。** DMA 控制器看的是匯流排位址，不是 CPU 的虛擬位址。「用錯變數」的第一反應，通常是領域鴻溝。
- **以為 `devm_clk_get` 會開 clock。** get 和 enable 是兩件事，enable 常常在 runtime PM callback 裡。只看 probe 會以為 clock 從沒被打開過。
- **看到可疑寫法就直接判定是 bug。** `-ENXIO` 確實違反慣例，但要寫清楚觸發條件（中斷控制器的 driver 晚於這個 driver probe）和實際影響（在這個平台上很低），不要誇大。

## 進階：再往深一層

- **讀一個 child driver 的 remove**：找一個 SPI flash driver（`drivers/mtd/spi-nor/core.c` 的 `spi_nor_remove`），看它在 remove 時會不會對硬體下命令。這決定了本章主要問題在真實系統上會不會觸發。
- **用 `CONFIG_DEBUG_DEVRES`**（`drivers/base/Kconfig:141`）：打開後開機參數加 `devres.log=1`，每次 devres 登記與釋放都會印一行 `DEVRES ADD`／`DEVRES REL`（`drivers/base/devres.c:60–72`），可以實際看到 LIFO 順序。不想重編 kernel 的話，同樣的資訊也有 tracepoint `devres_log` 可以用 ftrace 抓（[Ch 51](./51-dynamic-tracing.md)）。
- **掃描整個 drivers/spi**：寫一個腳本，找出「用 `devm_spi_register_controller`、而且 remove 函式裡有 `dma_release_channel`、`clk_disable` 或 `pm_runtime_force_suspend`」的 driver，再對照 `v6.18..origin/master` 的 56 個修正，看有沒有漏網的。這就是 [練習 G](./practice-g-ops-callback-finder.md) 那種「把讀碼經驗寫成工具」的延伸。

## 動手練習

1. 對 `spi-sun6i.c` 的 `sun6i_spi_runtime_resume()`（`:566–601`）做錯誤路徑檢查：在 `reset_control_deassert` 失敗時，它撤銷了哪些東西？順序對嗎？
2. 在 `drivers/spi/` 隨機挑一個 Johan Hovold 修過的 driver（`git log --oneline v6.18..origin/master --grep="fix controller deregistration" -- drivers/spi` 任選一個），**先不看修正**，只讀 v6.18 版本的 probe 和 remove，填帳本、找出問題，再 `git show` 對答案。
3. 解釋為什麼修正後的 sun6i remove 需要 `spi_controller_get()`，而 `spi-stm32-qspi.c` 的 remove 不需要。

<details>
<summary>練習提示與參考答案</summary>

1. 失敗時跳 `err2`：先 `clk_disable_unprepare(mclk)`，再落到 `err` 做 `clk_disable_unprepare(hclk)`。取得順序是 hclk → mclk → reset，撤銷順序是 mclk → hclk，reset 本身失敗不用撤銷。順序正確，是標準的 goto 階梯（[Ch 17](./17-kernel-error-conventions.md)）。
2. 沒有固定答案。檢查方法：找 probe 裡的 `devm_spi_register_controller`，再找 remove 裡有沒有手動釋放（`clk_disable_unprepare`、`dma_release_channel`、`pm_runtime_*`、寫暫存器關硬體）。只要有，就是同一個問題。修正的形狀幾乎都一樣：register 改成非 devm，remove 第一步 unregister，必要時加 get／put。
3. `spi_unregister_controller()` 最後的 `put_device()` 只在 controller **不是** devm 配置時才執行（`drivers/spi/spi.c:3476–3477`：`if (!ctlr->devm_allocated) put_device(&ctlr->dev);`）。修正 `d874a1c33aee` 時 sun6i 還是用 `spi_alloc_host`，unregister 會釋放最後一個 reference，所以要先多拿一個，remove 後面才能安全使用 `host`。stm32-qspi 用 `devm_spi_alloc_host`，unregister 不會 put，reference 由 devres 在最後釋放，所以不需要。這也是 `9864636b1cd9` 說「avoid having to take another reference during deregistration」的意思。

</details>

## 本章重點整理

- 冷讀 probe 的工具是資源帳本：每樣資源記取得位置、錯誤路徑的釋放者、unbind 的釋放者，再檢查順序。
- driver core 在 unbind 時先呼叫 `drv->remove()`，之後才 `devres_release_all()`；devres 內部是 LIFO。
- 只要 remove 裡有任何手動釋放，controller／介面的註冊就不能用 devm，否則「停止服務」會被排到「拆掉資源」之後。
- 冷讀報告裡的可疑點，要分清楚「確定是問題（附觸發條件）」和「違反慣例但影響小」；上游修正是最好的答案卷。

## 自我檢核

- [ ] 不看筆記，畫出 platform device unbind 時 `remove` 與 devres 的先後順序，標出 `drivers/base/dd.c` 的兩個行號
- [ ] 能解釋 `platform_get_irq()` 什麼時候回傳 `-EPROBE_DEFER`，以及把它改寫成 `-ENXIO` 的後果
- [ ] 能說出 runtime PM 開機舞步的四行各做什麼，以及為什麼要先手動呼叫一次 `runtime_resume`
- [ ] 能解釋為什麼 DMA slave config 用 `mem->start` 而不是 `base_addr`
- [ ] 拿一個沒看過的 platform driver，十五分鐘內填完資源帳本

## 延伸閱讀

### 官方文件

- **[Devres - Managed Device Resource](https://docs.kernel.org/driver-api/driver-model/devres.html)**（tree 內 `Documentation/driver-api/driver-model/devres.rst`）
  - **讀哪裡**：開頭的動機說明、「Devres group」一節，以及最後的 managed interface 清單
  - **學到什麼**：devres 的 LIFO 語意、哪些資源有 devm 版本；讀完再回頭看本章的帳本第 9 項
- **[Runtime Power Management Framework for I/O Devices](https://docs.kernel.org/power/runtime_pm.html)**（`Documentation/power/runtime_pm.rst`）
  - **讀哪裡**：第 5 節（Runtime PM Initialization, Device Probing and Removal）
  - **學到什麼**：`pm_runtime_set_active` + `pm_runtime_enable` 的開機舞步，以及 probe／remove 期間 runtime PM 的狀態規則
- **[DMA Engine API Guide](https://docs.kernel.org/driver-api/dmaengine/client.html)**（`Documentation/driver-api/dmaengine/client.rst`）
  - **讀哪裡**：「DMA usage」的五個步驟（allocate channel → set slave config → get descriptor → submit → issue pending）
  - **學到什麼**：`sun6i_spi_prepare_dma()` 每一個呼叫在標準流程裡的位置
- **[Driver Basics: Probe and remove](https://docs.kernel.org/driver-api/driver-model/driver.html)**（`Documentation/driver-api/driver-model/driver.rst`）
  - **學到什麼**：`-EPROBE_DEFER` 的正式語意與 deferred probe 的機制

### 論文

- **An Empirical Study of Operating Systems Errors** — Andy Chou、Junfeng Yang、Benjamin Chelf、Seth Hallem、Dawson Engler，SOSP 2001
  - **核心貢獻**：用靜態分析掃多個版本的 Linux（以及 OpenBSD），統計各類錯誤的分布，發現 `drivers/` 的錯誤率比 kernel 其他部分高出數倍，錯誤類型中有很大一部分和資源的取得／釋放配對（lock、記憶體、中斷開關）有關
  - **讀哪裡**：依錯誤類型分類統計的表格，以及 drivers 與其他目錄的比較
  - **和本章的關聯**：解釋了為什麼冷讀 driver 時，資源帳本是最划算的投資

### 書籍

- **《Linux Device Drivers, 3rd Edition》** — Jonathan Corbet、Alessandro Rubini、Greg Kroah-Hartman（O'Reilly, 2005，[線上免費版](https://lwn.net/Kernel/LDD3/)）
  - **讀哪幾章**：第 10 章（Interrupt Handling）、第 14 章（The Linux Device Model）
  - **定位**：devres 和 runtime PM 都是在這本書之後才出現的，但中斷處理與 device model 的基本觀念沒變；讀完再對照本章看「現代 driver 多了哪些東西」

一個 driver 的 probe 是「拿資源」的完整樣本；下一章換到資料流最密集的地方：一個封包從網卡中斷進來，到交給 IP 層為止，中間經過的那一段。

→ [Ch 56 冷讀實戰三：網路收包路徑的一段](./56-cold-read-network-rx.md)
