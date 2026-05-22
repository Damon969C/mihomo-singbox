# mihomo-singbox 交接文档

更新时间：2026-05-22

仓库：`git@github.com:Damon969C/mihomo-singbox.git`

当前用途：维护两类自动化脚本。

- `restore_bundle.py`：VPS 侧 Xray、Hysteria2、mihomo 客户端节点配置、sysctl、Realm 的安装和恢复脚本。
- `generate_singbox_bundle.py`：生成 sing-box 服务端与 6 份手机客户端隧道配置，用于异地组网、服务端直出、经隧道转发到局域网 mihomo。

## 当前仓库状态

当前目录已经恢复为普通 Git 仓库，可以直接使用：

```bash
git status
git pull --ff-only
git push
```

如果以后再次遇到“当前目录不是有效 git 仓库，`.git` 是空的只读目录”，本次修复方式是把 `/tmp/mihomo-singbox.git` 中的真实 Git 元数据复制回当前目录 `.git`。修复后应满足：

```bash
git status --short
git remote -v
git log -1 --oneline
```

`origin` 应指向：

```text
git@github.com:Damon969C/mihomo-singbox.git
```

## 重要安全约束

仓库只应提交脚本、测试、说明文档，不应提交真实配置和密钥。

`.gitignore` 已排除：

- `*.json`
- `*.yaml`
- `*.yml`
- `*.pem`
- `sing-box-secrets.txt`
- `singbox-output/`
- `__pycache__/`

当前被 Git 跟踪的文件应仅包含：

```bash
git ls-files
```

预期主要文件：

- `.gitignore`
- `README.md`
- `HANDOFF.md`
- `restore_bundle.py`
- `generate_singbox_bundle.py`
- `sync_clash_proxy_groups.py`
- `test_restore_bundle.py`
- `test_generate_singbox_bundle.py`
- `test_sync_clash_proxy_groups.py`

不要把本地生成的 `vless.yaml`、`hy2.yaml`、`clash-proxies.yaml`、`sing-box-server.json`、`vless-*.json`、`hy2-*.json`、证书、密钥摘要提交到仓库。

## 快速接手流程

新会话或新机器接手建议步骤：

```bash
git clone git@github.com:Damon969C/mihomo-singbox.git
cd mihomo-singbox
python3 -m unittest test_restore_bundle.py test_sync_clash_proxy_groups.py test_generate_singbox_bundle.py
python3 -m py_compile restore_bundle.py sync_clash_proxy_groups.py generate_singbox_bundle.py
```

如果要验证 sing-box 生成结果，需要本机有 `sing-box` 和 `openssl`：

```bash
python3 generate_singbox_bundle.py \
  --domain example.com \
  --vless-port 8443 \
  --hy2-port 8444 \
  -o /tmp/singbox-check

sing-box check -c /tmp/singbox-check/sing-box-server.json
sing-box check -c /tmp/singbox-check/vless-10.json
sing-box check -c /tmp/singbox-check/hy2-10.json
sing-box check -c /tmp/singbox-check/vless-lan.json
sing-box check -c /tmp/singbox-check/hy2-lan.json
sing-box check -c /tmp/singbox-check/vless-mihomo.json
sing-box check -c /tmp/singbox-check/hy2-mihomo.json
```

稳定版兼容性基准：本仓库最近用 `sing-box 1.13.12` 检查通过。不要重新加入 `dns.timeout`，因为稳定版 1.13.12 会报：

```text
dns.timeout: json: unknown field "timeout"
```

## `restore_bundle.py` 说明

用途：在 VPS 上交互式安装或恢复 Xray、Hysteria2，并生成 mihomo 客户端节点配置。

运行：

```bash
python3 restore_bundle.py
```

菜单：

```text
1. 安装 Xray
2. 安装 Hysteria2
3. 生成并恢复 Xray 配置
4. 生成并恢复 Hysteria2 配置
5. 写入 sysctl 网络调优
6. 全部恢复配置
7. 安装并配置 Realm 转发
8. 查看占用端口
0. 退出
```

端口查看也支持非交互：

```bash
python3 restore_bundle.py ports
```

主要写入路径：

- Xray 服务端：`/usr/local/etc/xray/config.json`
- Hysteria2 服务端：`/etc/hysteria/config.yaml`
- sysctl 调优：`/etc/sysctl.d/99-xray-hy2-tuning.conf`
- mihomo VLESS 节点：`/root/vless.yaml`
- mihomo HY2 节点：`/root/hy2.yaml`
- mihomo 合并节点：`/root/clash-proxies.yaml`
- Realm 配置：`/etc/realm/config.toml`
- Realm systemd：`/etc/systemd/system/realm.service`

Xray 当前设计：

- 入站协议：VLESS
- 传输：`raw`
- TLS 安全层：REALITY
- Flow：`xtls-rprx-vision`
- 伪装目标：`www.paypal.com:443`
- 监听：`::`
- 端口：`443`
- 使用 `xray x25519` 生成 Reality 密钥。
- 使用 `xray vlessenc`，优先提取 `ML-KEM-768, Post-Quantum` 的 `decryption/encryption` 配对。
- 已移除 `mldsa65Seed`，因为 mihomo 客户端不支持 `mldsa65Verify`，保留会造成配置复杂且无收益。

Hysteria2 当前设计：

- 默认端口：`8443`，运行时交互询问。
- 每次运行随机生成节点 ID、密码、obfs 密码。
- 生成自签证书到 `/etc/hysteria/cert.pem` 与 `/etc/hysteria/key.pem`。
- SNI：`www.bing.com`
- salamander 混淆。

mihomo 输出设计：

- 文件名固定为 `/root/vless.yaml`、`/root/hy2.yaml`、`/root/clash-proxies.yaml`。
- 节点名称简化为：
  - `VLESS IPv4 1.2.3.4`
  - `VLESS IPv6 2001:db8::1`
  - `HY2 IPv4 1.2.3.4`
  - `HY2 IPv6 2001:db8::1`
- YAML 列表缩进使用 4 空格，匹配用户本地 mihomo 风格。

网络地址检测：

- 使用 `ip a`。
- 自动过滤本地、链路本地、未指定、多播地址。
- 多个网卡时交互选择。
- 同一网卡多个全局 IPv4 或 IPv6 时交互选择。

服务重启：

- `restart_service()` 使用 `systemctl restart`。
- 重启成功会打印 `[+] 已重启`。
- 失败会打印 `[!] 重启 ... 失败，请手动检查服务状态`。

sysctl 调优：

- 写入前会读取已有文件。
- `build_sysctl_config()` 会按 key 去重，避免相同条目重复写入。
- 当前值包括 `fq`、`bbr`、TCP buffer、MTU probing、fastopen、backlog 等。

Realm：

- 从 `REALM_PACKAGE_URL` 下载 `realm-x86_64-unknown-linux-musl.tar.gz`。
- 写入固定模板到 `/etc/realm/config.toml`。
- 当前模板里的 remote 是占位值，实际使用前需要按环境调整。

## `generate_singbox_bundle.py` 说明

用途：生成 sing-box 服务端和手机客户端配置。主要用于家庭/异地组网，服务端 LAN 为：

- `10.0.0.0/24`
- `10.10.10.0/24`

运行：

```bash
python3 generate_singbox_bundle.py
```

非交互：

```bash
python3 generate_singbox_bundle.py \
  --domain example.com \
  --vless-port 8443 \
  --hy2-port 8444 \
  -o ./singbox-output
```

依赖：

- `sing-box`：生成 Reality keypair。
- `openssl`：生成 Hysteria2 自签证书。

输出文件：

- `sing-box-server.json`
- `vless-10.json`
- `hy2-10.json`
- `vless-lan.json`
- `hy2-lan.json`
- `vless-mihomo.json`
- `hy2-mihomo.json`
- `sing-box-secrets.txt`
- `sing-box-hy2-cert.pem`
- `sing-box-hy2-key.pem`

服务端包含：

- VLESS REALITY + `xtls-rprx-vision`
- Hysteria2 + salamander 混淆
- VLESS Reality 伪装域名：`www.bilibili.com`
- Hysteria2 使用生成的自签证书

六份客户端配置含义：

- `vless-10.json`：只访问 `10.0.0.0/24`、`10.10.10.0/24`，默认 VLESS。
- `hy2-10.json`：只访问 `10.0.0.0/24`、`10.10.10.0/24`，默认 HY2。
- `vless-lan.json`：所有流量走 `10.0.0.15` sing-box 服务端直出，默认 VLESS。
- `hy2-lan.json`：所有流量走 `10.0.0.15` sing-box 服务端直出，默认 HY2。
- `vless-mihomo.json`：所有流量经隧道交给 `10.0.0.20:7890` mihomo，默认 VLESS。
- `hy2-mihomo.json`：所有流量经隧道交给 `10.0.0.20:7890` mihomo，默认 HY2。

注意：脚本中的 `10.0.0.15` 是用户实际 sing-box 服务端地址的语义描述，不是客户端配置里需要显式写死的 socks 目标。客户端通过 `server_domain` 连接服务端，再由服务端直出或访问局域网资源。

当前关键常量：

- `LAN_CIDRS = ["10.0.0.0/24", "10.10.10.0/24"]`
- `GLOBAL_ROUTE_CIDRS = ["0.0.0.0/1", "128.0.0.0/1", "::/1", "8000::/1"]`
- `LAN_DNS_SERVER = "10.0.0.1"`
- `LOCAL_DNS_PRIMARY_SERVER = "180.76.76.76"`
- `LOCAL_DNS_SECONDARY_SERVER = "223.5.5.5"`
- `MIHOMO_SERVER = "10.0.0.20"`
- `MIHOMO_MIXED_PORT = 7890`
- `TUN_MTU = 1280`
- `REALITY_SERVER_NAME = "www.bilibili.com"`

DNS 设计：

- `*-10`：只访问内网段，DNS 不走远端，使用本地公共 DNS `180.76.76.76` 与 `223.5.5.5`。
- `*-lan`：所有流量走服务端直出，DNS 使用远端 `10.0.0.1`，通过 `lan-select` 隧道，协议为 TCP。
- `*-mihomo`：所有流量经隧道交给 `10.0.0.20:7890`，DNS 使用 `10.0.0.20:53`，通过 `lan-select` 隧道，协议为 TCP。
- 所有客户端 DNS 都保留 `cache_capacity: 4096`。
- 不使用 `dns.timeout`，保持 sing-box 1.13 稳定版兼容。
- `default_domain_resolver` 使用 `223.5.5.5` 且 `prefer_ipv4`，避免 HY2 优先走不稳定 IPv6 路径。

HY2/QUIC 已处理过的问题：

- 手机端 HY2 出现过类似：

```text
quic: transport closed: read udp ... read: message too long
```

已做的脚本优化：

- TUN MTU 从 `9000` 降为 `1280`。
- `default_domain_resolver.strategy` 从 `prefer_ipv6` 改为 `prefer_ipv4`。
- 全局/ mihomo 模式下隧道内 DNS 从 UDP 改为 TCP。

如果后续 HY2 仍在特定网络报 QUIC/UDP 问题，下一步可考虑给 HY2 单独生成一个更保守的配置变体，但不要直接砍掉所有 UDP 能力，因为这会影响 QUIC 类应用体验。

## `sync_clash_proxy_groups.py` 说明

用途：从 mihomo/Clash 配置顶层 `proxies` 中提取节点名称，同步到 `proxy-groups`，并尽量保持原 YAML 的块状写法和缩进。

运行示例：

```bash
python3 sync_clash_proxy_groups.py basic.yaml -o basic.synced.yaml
```

设计约束：

- 只用 Python 标准库。
- 支持行内对象和块状 YAML。
- 保留换行类型。
- 保留内建策略：`DIRECT`、`REJECT`、`REJECT-DROP`、`PASS`。
- 对无法安全解析的结构会抛出 `SyncError`，避免静默破坏配置。

## 常用验证命令

每次改脚本后建议跑：

```bash
python3 -m unittest test_restore_bundle.py test_sync_clash_proxy_groups.py test_generate_singbox_bundle.py
python3 -m py_compile restore_bundle.py sync_clash_proxy_groups.py generate_singbox_bundle.py
```

改 `generate_singbox_bundle.py` 后建议额外跑：

```bash
python3 generate_singbox_bundle.py \
  --domain example.com \
  --vless-port 8443 \
  --hy2-port 8444 \
  -o /tmp/singbox-check

for f in \
  sing-box-server.json \
  vless-10.json \
  hy2-10.json \
  vless-lan.json \
  hy2-lan.json \
  vless-mihomo.json \
  hy2-mihomo.json
do
  sing-box check -c "/tmp/singbox-check/$f"
done
```

改 `restore_bundle.py` 后重点检查：

```bash
python3 -m unittest test_restore_bundle.py
python3 -m py_compile restore_bundle.py
```

不要在非 VPS 测试环境直接执行会写 `/etc`、`/root`、`/usr/local` 的菜单项，除非明确知道影响。

## 推荐后续调整点

后续如果继续优化，优先考虑这些低风险方向：

- 给 `generate_singbox_bundle.py` 增加参数，允许通过 CLI 覆盖 `LAN_CIDRS`、`LAN_DNS_SERVER`、`MIHOMO_SERVER`、`MIHOMO_MIXED_PORT`，减少硬编码。
- 给 `restore_bundle.py` 的 Realm 模板改成交互输入 listen/remote，而不是固定占位配置。
- 给 sing-box 生成脚本增加 `--check` 参数，生成后自动调用 `sing-box check`。
- 给 `restore_bundle.py` 增加 dry-run 模式，打印将写入的路径和配置但不真正写入系统目录。
- 增加 README 中的“常见故障处理”小节，记录 `dns.timeout`、HY2 `message too long`、mihomo fake-ip DNS 等问题。

## 建议技能

后续代理接手建议优先使用：

- `superpowers:verification-before-completion`：提交或声称完成前必须运行验证命令。
- `superpowers:systematic-debugging`：处理连接失败、DNS 异常、HY2/QUIC 错误时先定位根因。
- `superpowers:test-driven-development`：修改脚本行为时先补测试，再改实现。
- `handoff`：需要再次压缩上下文给下一位代理时使用。

## 最近关键提交

- `f94c59c Remove unsupported sing-box DNS timeout`：移除 sing-box 稳定版不支持的 `dns.timeout`。
- `2e4b0fa Stabilize sing-box HY2 client DNS path`：降低 TUN MTU、改服务端域名解析偏好、隧道内 DNS 改 TCP。

后续新增交接文档后会产生新的提交，以下命令可查看最新历史：

```bash
git log --oneline -5
```
