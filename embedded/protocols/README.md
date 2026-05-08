# 嵌入式通訊協議學習筆記：從時序原理到 Register-Level 驅動

> 給有 ARM / C 底子、想在 ESP32 上從 datasheet 自己刻驅動的工程師。

這系列不靠 HAL。每一個協議先讀懂時序圖和封包格式，再對著 ESP32 Technical Reference Manual 打 peripheral register，最後在實體板子上用邏輯分析儀驗波形。覆蓋 SPI / I2C / UART / RS-485 / Modbus / CAN / BLE / LoRa / Zigbee / USB / Ethernet，共九種協議。

## 需要的硬體

| 板子 | 用於 |
|---|---|
| ESP32 DevKitC（classic） | UART、I2C、SPI、CAN/TWAI、BLE、LoRa |
| ESP32-S3 DevKitC | USB OTG（Ch 22–23） |
| ESP32-H2 或 C6 DevKitC | Zigbee（Ch 21） |
| LAN8720 PHY 模組 | Ethernet（Ch 24–25） |
| SX1276/SX1278 LoRa 模組 | LoRa（Ch 05、20） |
| BME280 模組 | I2C 實作目標（Ch 08） |
| MAX485 模組 | RS-485（Ch 10） |
| MCP2551 或 SN65HVD230 | CAN 收發器（Ch 15） |
| 邏輯分析儀（Saleae 或相容品） | 所有協議波形驗證 |

## 為什麼學這個？

- **datasheet 能力**：不靠 HAL 代表要自己查 TRM，這個能力讓你看得懂任何廠商的 peripheral 文件。
- **除錯能力**：register-level 出錯時，邏輯分析儀 + 暫存器 dump 比 HAL 錯誤訊息清楚一百倍。
- **協議深度**：面試問「I2C clock stretching 怎麼處理」，會寫過驅動的人和只用過 HAL 的人差距很明顯。

## 課程地圖

### Part 1 — 基礎工具鏈
- [Ch 0 環境建置](./00-environment-setup.md)
- [Ch 1 ESP32 硬體概覽](./01-esp32-hardware-overview.md)
- [Ch 2 Register-Level 驅動框架](./02-register-level-driver-framework.md)

### Part 2 — SPI
- [Ch 3 SPI 協議原理](./03-spi-protocol.md)
- [Ch 4 ESP32 SPI 暫存器](./04-esp32-spi-registers.md)
- [Ch 5 實作：SPI → SX1276 LoRa 模組初始化](./05-spi-sx1276-init.md)

### Part 3 — I2C
- [Ch 6 I2C 協議原理](./06-i2c-protocol.md)
- [Ch 7 ESP32 I2C 暫存器](./07-esp32-i2c-registers.md)
- [Ch 8 實作：I2C → BME280 溫濕壓感測器](./08-i2c-bme280.md)

### Part 4 — UART / RS-485 / Modbus
- [Ch 9 UART 協議原理與暫存器](./09-uart-protocol-registers.md)
- [Ch 10 RS-485 差分信號](./10-rs485.md)
- [Ch 11 Modbus RTU 協議](./11-modbus-rtu.md)
- [Ch 12 實作：UART Register-Level → Modbus RTU Master](./12-uart-modbus-master.md)
- [練習 A：多協議感測器節點](./practice-a-multi-protocol-node.md)

### Part 5 — CAN Bus
- [Ch 13 CAN 協議原理](./13-can-protocol.md)
- [Ch 14 ESP32 TWAI 暫存器](./14-esp32-twai-registers.md)
- [Ch 15 實作：TWAI Register-Level → CAN Frame 收發](./15-twai-can-frames.md)
- [練習 B：CAN 雙節點仲裁測試](./practice-b-can-arbitration.md)

### Part 6 — BLE
- [Ch 16 BLE 協議堆疊](./16-ble-stack.md)
- [Ch 17 ESP32 BLE 底層](./17-esp32-ble-internals.md)
- [Ch 18 實作：Custom GATT Service](./18-ble-gatt-service.md)

### Part 7 — LoRa / Zigbee
- [Ch 19 LoRa 原理](./19-lora-protocol.md)
- [Ch 20 實作：SPI → SX1276 Register-Level LoRa 收發](./20-lora-sx1276-transceive.md)
- [Ch 21 Zigbee 原理與實作](./21-zigbee.md)
- [練習 C：BLE + LoRa 橋接](./practice-c-ble-lora-bridge.md)

### Part 8 — USB
- [Ch 22 USB 協議原理](./22-usb-protocol.md)
- [Ch 23 實作：ESP32-S3 USB CDC](./23-usb-cdc.md)

### Part 9 — Ethernet
- [Ch 24 Ethernet 原理](./24-ethernet-protocol.md)
- [Ch 25 實作：ESP32 EMAC Register-Level](./25-emac-raw-frames.md)

### Final Project
- [Final Project：工業感測器閘道器](./final-project-industrial-gateway.md)

## 學習方式建議

1. **先買邏輯分析儀**：每一章的波形驗證比看程式碼跑起來更重要，Saleae 相容品台幣 500 以內就有。
2. **TRM 要開著**：ESP32 Technical Reference Manual 是這門課的第二本教材，每次查暫存器都去 TRM 確認 bit 定義。
3. **故意打錯**：把 I2C ACK bit 設錯、把 CAN timing 設偏，看邏輯分析儀和 error register 怎麼反應——這比順利跑過學得多。

## 參考資料

- [ESP32 Technical Reference Manual](https://www.espressif.com/sites/default/files/documentation/esp32_technical_reference_manual_en.pdf) — 暫存器定義聖經
- [ESP-IDF Programming Guide](https://docs.espressif.com/projects/esp-idf/en/latest/) — 官方 SDK 文件
- 《Making Embedded Systems》— Elecia White（嵌入式思維建立）
- [SX1276 Datasheet](https://semtech.com/products/wireless-rf/lora-core/sx1276) — LoRa 模組暫存器
- [BME280 Datasheet](https://www.bosch-sensortec.com/products/environmental-sensors/humidity-sensors-bme280/) — I2C 實作目標
