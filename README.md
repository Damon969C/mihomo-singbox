# mihomo-singbox

用于生成和维护 mihomo、Xray/Hysteria2、sing-box 相关配置的脚本集合。

仓库只保存脚本和测试，不保存真实节点、订阅、密钥、证书和生成后的配置文件。运行脚本后请先检查生成内容，再部署到服务器或客户端。

## 脚本说明

### `restore_bundle.py`

VPS 恢复和配置生成脚本，主要功能：

- 生成 Xray VLESS REALITY + Vision 服务端配置
- 生成 Hysteria2 服务端配置
- 生成 mihomo 客户端节点配置：
  - `/root/vless.yaml`
  - `/root/hy2.yaml`
  - `/root/clash-proxies.yaml`
- 写入 sysctl 网络调优配置
- 安装并配置 Realm 转发
- 查看当前监听端口占用

运行：

```bash
python3 restore_bundle.py
```

### `generate_singbox_bundle.py`

生成 sing-box 服务端和手机客户端配置，用于异地组网和隧道加密。

服务端包含：

- VLESS REALITY + `xtls-rprx-vision`
- Hysteria2 + salamander 混淆

客户端会生成 6 份配置：

- `vless-10.json`：只访问 `10.0.0.0/24`、`10.10.10.0/24`，默认使用 VLESS
- `hy2-10.json`：只访问 `10.0.0.0/24`、`10.10.10.0/24`，默认使用 Hysteria2
- `vless-lan.json`：所有流量走 sing-box 服务端直出，默认使用 VLESS
- `hy2-lan.json`：所有流量走 sing-box 服务端直出，默认使用 Hysteria2
- `vless-mihomo.json`：所有流量经隧道后交给 `10.0.0.20:7890` 的 mihomo，默认使用 VLESS
- `hy2-mihomo.json`：所有流量经隧道后交给 `10.0.0.20:7890` 的 mihomo，默认使用 Hysteria2

交互运行：

```bash
python3 generate_singbox_bundle.py
```

非交互运行：

```bash
python3 generate_singbox_bundle.py \
  --domain example.com \
  --vless-port 8443 \
  --hy2-port 8444 \
  -o ./singbox-output
```

依赖：

- `sing-box`：用于生成 REALITY 密钥
- `openssl`：用于生成 Hysteria2 自签证书

### `sync_clash_proxy_groups.py`

根据 mihomo/Clash 配置中顶层 `proxies` 节点列表，同步 `proxy-groups` 里的节点名称，并尽量保持块状 YAML 格式不被破坏。

运行：

```bash
python3 sync_clash_proxy_groups.py basic.yaml -o basic.synced.yaml
```

## 测试

运行单元测试：

```bash
python3 -m unittest test_restore_bundle.py test_sync_clash_proxy_groups.py test_generate_singbox_bundle.py
```

语法检查：

```bash
python3 -m py_compile restore_bundle.py sync_clash_proxy_groups.py generate_singbox_bundle.py
```

如果本机安装了 `sing-box`，可以检查生成后的配置：

```bash
sing-box check -c sing-box-server.json
sing-box check -c vless-10.json
sing-box check -c vless-mihomo.json
```

## 安全说明

`.gitignore` 默认排除了：

- `*.yaml`
- `*.json`
- `*.pem`
- `sing-box-secrets.txt`
- 生成目录和 Python 缓存

这些文件通常包含订阅节点、UUID、Reality 密钥、HY2 密码、证书等敏感信息，不应直接提交到公开仓库。
