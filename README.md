# Zaka 成人订阅 · 整合版

TVBox 系壳（FongMi / 影视仓 / OK影视 / 蜂蜜影视等）用的成人影视订阅。
点播源逐条实测过完整链路，直播是品牌成人台 7×24 真推流。

## 结构

```
index.json        主订阅，导入这一个就够
live.m3u          品牌成人台直播（24 路）
live-p2p.m3u      P2P 直播（2 路）
jar/              采集驱动
json/ lib/        源配置
js/ plugin/       脚本源
live/ tv/         直播配置
```

## 用法

整套文件必须放**同一目录**（仓库根目录），`index.json` 里用的是相对路径，
单独只传 `index.json` 会导致依赖加载不到、源集体打不开。

订阅地址：

```
https://raw.githubusercontent.com/<用户名>/<仓库名>/main/index.json
```

国内直连 raw 不稳，套加速前缀（推荐 ghproxy 系，见 `使用说明.txt` 里的实测表）：

```
https://ghproxy.net/https://raw.githubusercontent.com/<用户名>/<仓库名>/main/index.json
```

## 壳的要求

- 采集源：任何 TVBox 系壳都能用
- 脚本源（`js/` `plugin/`）：需要壳内置 python + quickjs。FongMi 官方版、蜂蜜影视支持；
  精简壳会自动跳过这类源，不影响其它源

## 说明

源是活的，今天通明天可能断。哪条点开不出片，把名字甩给作者，单独换。
