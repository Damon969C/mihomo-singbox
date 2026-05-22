# mihomo-singbox

Utilities for building mihomo, Xray/Hysteria2, and sing-box tunnel configs.

This repository intentionally keeps generated configs and real node credentials out of version control. Run the scripts locally on the target VPS or workstation and review generated files before deployment.

## Scripts

### `restore_bundle.py`

VPS recovery/config generation helper for:

- Xray VLESS REALITY + Vision server config
- Hysteria2 server config
- mihomo client proxy files:
  - `/root/vless.yaml`
  - `/root/hy2.yaml`
  - `/root/clash-proxies.yaml`
- sysctl network tuning
- Realm forwarding helper
- occupied port display

Run:

```bash
python3 restore_bundle.py
```

### `generate_singbox_bundle.py`

Generates sing-box server and mobile client configs for remote LAN access.

Server:

- VLESS REALITY + `xtls-rprx-vision`
- Hysteria2 + salamander obfs

Client files:

- `vless-10.json`: only routes `10.0.0.0/24` and `10.10.10.0/24`, default tunnel is VLESS
- `hy2-10.json`: only routes `10.0.0.0/24` and `10.10.10.0/24`, default tunnel is Hysteria2
- `vless-lan.json`: all traffic exits from the sing-box server LAN, default tunnel is VLESS
- `hy2-lan.json`: all traffic exits from the sing-box server LAN, default tunnel is Hysteria2
- `vless-mihomo.json`: all traffic goes through the tunnel and then to mihomo at `10.0.0.20:7890`, default tunnel is VLESS
- `hy2-mihomo.json`: all traffic goes through the tunnel and then to mihomo at `10.0.0.20:7890`, default tunnel is Hysteria2

Run interactively:

```bash
python3 generate_singbox_bundle.py
```

Run non-interactively:

```bash
python3 generate_singbox_bundle.py \
  --domain example.com \
  --vless-port 8443 \
  --hy2-port 8444 \
  -o ./singbox-output
```

The script requires `sing-box` to generate REALITY keys. If certificate generation is enabled, it also requires `openssl`.

### `sync_clash_proxy_groups.py`

Synchronizes mihomo/Clash `proxy-groups` node lists from the top-level `proxies` section while preserving block-style YAML formatting.

Run:

```bash
python3 sync_clash_proxy_groups.py basic.yaml -o basic.synced.yaml
```

## Tests

```bash
python3 -m unittest test_restore_bundle.py test_sync_clash_proxy_groups.py test_generate_singbox_bundle.py
python3 -m py_compile restore_bundle.py sync_clash_proxy_groups.py generate_singbox_bundle.py
```

If `sing-box` is installed, generated sing-box configs can be checked with:

```bash
sing-box check -c sing-box-server.json
sing-box check -c vless-10.json
```
