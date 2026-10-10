# Zaka 成人订阅 · 国内镜像版

自包含的 TVBox 系订阅，整站可以放在你自己的 GitHub 仓库里，通过国内可直连的
镜像地址访问——不依赖任何第三方服务器。

## 内容

| 目录 | 说明 |
|---|---|
| `index.json` | 订阅主文件（95 条点播源 + 24 路品牌成人台） |
| `rules/` | 规则文件（42 份，供规则引擎读取） |
| `py/` | 取数引擎（通用规则引擎 + 色播直播） |
| `sebo/` | 色播直播全量数据（136 个平台） |
| `jar/` | 驱动包 |
| `json/` `lib/` `js/` `plugin/` `XYQHike/` `live/` `tv/` | 各类源所依赖的资源 |

## 怎么用

1. 新建一个 **Public** 仓库
2. 把本目录**全部内容**传上去（目录结构别改）
3. 订阅地址填下面任意一条（把 `用户名/仓库名` 换掉）：

```
https://gh-proxy.com/https://raw.githubusercontent.com/用户名/仓库名/main/index.json
https://ghproxy.net/https://raw.githubusercontent.com/用户名/仓库名/main/index.json
https://gh.llkk.cc/https://raw.githubusercontent.com/用户名/仓库名/main/index.json
https://ghfast.top/https://raw.githubusercontent.com/用户名/仓库名/main/index.json
https://fastly.jsdelivr.net/gh/用户名/仓库名@main/index.json
https://cdn.jsdmirror.com/gh/用户名/仓库名@main/index.json
```

4. 删掉壳里的旧订阅，重新添加一次（壳会把配置缓存在内存里）

## 关于依赖路径

默认是**相对路径**写法，所以你填哪条镜像地址，依赖文件都会自动跟着那条地址走，
不用改任何东西。

只有极少数壳不认相对路径（症状：大部分源正常，但需要加载 jar / 脚本的那批源全白），
这时跑一下：

```
python3 set-base.py --repo 用户名/仓库名
```

它会把这包里的引用全部换成绝对地址，跑完自带自检。

## 更新

把新文件覆盖上去即可（壳会自动拉最新的，jsdelivr 系有缓存可能要等一会儿）。

## 免梯子（中转）

包里 133 个上游域名全是墙外的（105 个在 Cloudflare 后），直连必然拿不到数据。
想免掉"每台设备都挂梯子"，用包里带的中转：

1. `relay/relay.py` 传到一台墙外的机器上，跑起来：
   `python3 relay.py -p 8899 --public-base http://你的IP:8899 --token 你的口令`
2. 一条命令把整包切过去：
   `python3 set-relay.py --base http://你的IP:8899 --token 你的口令`
3. 传回仓库、壳里删旧订阅重加。

还原：`python3 set-relay.py --restore`

细节、流量说明和覆盖范围见 `中转版说明.txt`。
