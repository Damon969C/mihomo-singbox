#!/usr/bin/env python3

import ipaddress
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
import uuid
from dataclasses import dataclass


XRAY_CONFIG_PATH = "/usr/local/etc/xray/config.json"
HY2_CONFIG_PATH = "/etc/hysteria/config.yaml"
SYSCTL_TUNING_PATH = "/etc/sysctl.d/99-xray-hy2-tuning.conf"

CLASH_XRAY_CONFIG_PATH = "/root/vless.yaml"
CLASH_HY2_CONFIG_PATH = "/root/hy2.yaml"
CLASH_COMBINED_CONFIG_PATH = "/root/clash-proxies.yaml"

XRAY_PORT = 443
XRAY_REALITY_SERVER_NAME = "www.paypal.com"
XRAY_REALITY_TARGET = f"{XRAY_REALITY_SERVER_NAME}:443"
HY2_SNI = "www.bing.com"
HY2_DEFAULT_PORT = 8443

REALM_PACKAGE_URL = "https://github.com/zhboner/realm/releases/download/v2.9.3/realm-x86_64-unknown-linux-musl.tar.gz"
REALM_CONFIG_PATH = "/etc/realm/config.toml"
REALM_SERVICE_PATH = "/etc/systemd/system/realm.service"

SYSCTL_TUNING_VALUES = {
    "net.core.default_qdisc": "fq",
    "net.ipv4.tcp_congestion_control": "bbr",
    "net.core.rmem_max": 67108864,
    "net.core.wmem_max": 67108864,
    "net.ipv4.tcp_rmem": "4096 87380 67108864",
    "net.ipv4.tcp_wmem": "4096 65536 67108864",
    "net.ipv4.tcp_mtu_probing": 1,
    "net.ipv4.tcp_fastopen": 3,
    "net.core.somaxconn": 32768,
    "net.core.netdev_max_backlog": 16384,
    "net.ipv4.tcp_max_syn_backlog": 16384,
    "net.ipv4.tcp_fin_timeout": 15,
    "net.ipv4.tcp_tw_reuse": 1,
    "net.ipv4.tcp_notsent_lowat": 16384,
}

REALM_CONFIG = """[log]
level = "info"
output = "/var/log/realm.log"

[network]
no_tcp = false
use_udp = true

[[endpoints]]
listen = "[::]:1443"
remote = "1.2.3.4:443"

[[endpoints]]
listen = "0.0.0.0:53725"
remote = "1.2.3.4:53723"
"""

REALM_SERVICE = """[Unit]
Description=Realm Port Forwarding
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/realm -c /etc/realm/config.toml
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
"""


@dataclass(frozen=True)
class RealityKeyPair:
    private_key: str
    public_key: str


@dataclass(frozen=True)
class VlessEncPair:
    decryption: str
    encryption: str


@dataclass(frozen=True)
class NetworkInterface:
    name: str
    ipv4: list[str]
    ipv6: list[str]


@dataclass(frozen=True)
class NetworkSelection:
    interface: str | None
    ipv4: str | None
    ipv6: str | None


@dataclass(frozen=True)
class XrayArtifacts:
    config: dict
    uuid: str
    public_key: str
    short_id: str
    encryption: str


@dataclass(frozen=True)
class Hy2Artifacts:
    node_id: str
    port: int
    password: str
    obfs_password: str
    config_text: str


def configure_stdio():
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


def require_command(command):
    if shutil.which(command):
        return
    raise RuntimeError(f"缺少命令: {command}，请先安装后重试")


def run_command(args, *, check=True, capture_output=False):
    return subprocess.run(
        args,
        check=check,
        capture_output=capture_output,
        text=True,
    )


def write_json_file(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"[+] 已写入: {path}")


def write_text_file(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"[+] 已写入: {path}")


def generate_reality_key_pair():
    require_command("xray")
    result = run_command(["xray", "x25519"], capture_output=True)
    return parse_reality_key_pair(result.stdout)


def parse_reality_key_pair(output):
    private_key = None
    public_key = None

    for line in output.splitlines():
        normalized = line.lower().replace(" ", "")
        if normalized.startswith("privatekey:"):
            private_key = line.split(":", 1)[1].strip()
        elif normalized.startswith("publickey:") or normalized.startswith("password(publickey):"):
            public_key = line.split(":", 1)[1].strip()

    if not private_key or not public_key:
        raise RuntimeError("无法从 xray x25519 输出中解析 Reality 密钥对")

    return RealityKeyPair(private_key=private_key, public_key=public_key)


def generate_vlessenc_pair():
    require_command("xray")
    result = run_command(["xray", "vlessenc"], capture_output=True)
    return parse_vlessenc_pair(result.stdout)


def parse_vlessenc_pair(output):
    current_block = None
    blocks = {}

    for line in output.splitlines():
        line = line.strip()
        if line.startswith("Authentication:"):
            current_block = line
            blocks[current_block] = {}
            continue

        if not current_block:
            continue

        match = re.match(r'"(decryption|encryption)":\s*"([^"]+)"', line)
        if match:
            blocks[current_block][match.group(1)] = match.group(2)

    for block_name, values in blocks.items():
        if "ML-KEM-768" in block_name and {"decryption", "encryption"} <= set(values):
            return VlessEncPair(
                decryption=values["decryption"],
                encryption=values["encryption"],
            )

    raise RuntimeError("无法从 xray vlessenc 输出中解析 ML-KEM-768 配对")


def build_xray_config(uuid_value, private_key, short_id, decryption):
    return {
        "log": {
            "loglevel": "warning",
        },
        "policy": {
            "levels": {
                "0": {
                    "connIdle": 300,
                },
            },
        },
        "inbounds": [
            {
                "tag": "vless-reality-vision-enc",
                "listen": "::",
                "port": XRAY_PORT,
                "protocol": "vless",
                "settings": {
                    "clients": [
                        {
                            "id": uuid_value,
                            "flow": "xtls-rprx-vision",
                        },
                    ],
                    "decryption": decryption,
                },
                "streamSettings": {
                    "network": "raw",
                    "security": "reality",
                    "realitySettings": {
                        "show": False,
                        "target": XRAY_REALITY_TARGET,
                        "xver": 0,
                        "serverNames": [
                            XRAY_REALITY_SERVER_NAME,
                        ],
                        "privateKey": private_key,
                        "shortIds": [
                            short_id,
                        ],
                        "maxTimeDiff": 60000,
                    },
                },
                "sniffing": {
                    "enabled": True,
                    "destOverride": [
                        "http",
                        "tls",
                        "quic",
                    ],
                    "routeOnly": True,
                },
            },
        ],
        "outbounds": [
            {
                "tag": "direct",
                "protocol": "freedom",
            },
            {
                "tag": "blocked",
                "protocol": "blackhole",
            },
        ],
        "routing": {
            "domainStrategy": "AsIs",
            "rules": [
                {
                    "type": "field",
                    "protocol": [
                        "bittorrent",
                    ],
                    "outboundTag": "blocked",
                },
                {
                    "type": "field",
                    "ip": [
                        "geoip:private",
                    ],
                    "outboundTag": "blocked",
                },
            ],
        },
    }


def generate_xray_artifacts():
    keys = generate_reality_key_pair()
    vlessenc = generate_vlessenc_pair()
    uuid_value = str(uuid.uuid4())
    short_id = secrets.token_hex(8)
    config = build_xray_config(
        uuid_value=uuid_value,
        private_key=keys.private_key,
        short_id=short_id,
        decryption=vlessenc.decryption,
    )
    return XrayArtifacts(
        config=config,
        uuid=uuid_value,
        public_key=keys.public_key,
        short_id=short_id,
        encryption=vlessenc.encryption,
    )


def generate_hy2_artifacts(port):
    node_id = str(uuid.uuid4())
    password = secrets.token_urlsafe(32)
    obfs_password = secrets.token_urlsafe(32)
    config_text = build_hy2_config(
        node_id=node_id,
        port=port,
        password=password,
        obfs_password=obfs_password,
    )
    return Hy2Artifacts(
        node_id=node_id,
        port=port,
        password=password,
        obfs_password=obfs_password,
        config_text=config_text,
    )


def build_hy2_config(node_id, port, password, obfs_password):
    return f"""# node-id: {node_id}
listen: :{port}

tls:
  cert: /etc/hysteria/cert.pem
  key: /etc/hysteria/key.pem

auth:
  type: password
  password: {json.dumps(password)}

obfs:
  type: salamander
  salamander:
    password: {json.dumps(obfs_password)}

quic:
  initStreamReceiveWindow: 26843545
  maxStreamReceiveWindow: 26843545
  initConnReceiveWindow: 67108864
  maxConnReceiveWindow: 67108864

bandwidth:
  up: 1 gbps
  down: 1 gbps
"""


def parse_ip_a_interfaces(output):
    interfaces = {}
    current_name = None

    for raw_line in output.splitlines():
        header = re.match(r"^\d+:\s+([^:@]+)(?:@[^:]+)?:", raw_line)
        if header:
            current_name = header.group(1)
            continue

        if not current_name:
            continue

        stripped = raw_line.strip()
        if not stripped.startswith(("inet ", "inet6 ")):
            continue

        parts = stripped.split()
        if len(parts) < 2:
            continue

        family = parts[0]
        address = parts[1]
        scope = None
        if "scope" in parts:
            scope_index = parts.index("scope")
            if scope_index + 1 < len(parts):
                scope = parts[scope_index + 1]

        if scope and scope != "global":
            continue

        try:
            parsed = ipaddress.ip_interface(address).ip
        except ValueError:
            continue

        if parsed.is_loopback or parsed.is_link_local or parsed.is_unspecified or parsed.is_multicast:
            continue

        item = interfaces.setdefault(
            current_name,
            NetworkInterface(name=current_name, ipv4=[], ipv6=[]),
        )
        if family == "inet":
            item.ipv4.append(str(parsed))
        elif family == "inet6":
            item.ipv6.append(str(parsed))

    return {
        name: item
        for name, item in interfaces.items()
        if item.ipv4 or item.ipv6
    }


def get_ip_a_interfaces():
    require_command("ip")
    result = run_command(["ip", "a"], check=False, capture_output=True)
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"执行 ip a 失败: {message}")
    return parse_ip_a_interfaces(result.stdout)


def prompt_choice(title, options, render):
    if not options:
        return None
    if len(options) == 1:
        value = options[0]
        print(f"[+] {title}: {render(value)}")
        return value

    print(f"\n{title}:")
    for index, option in enumerate(options, start=1):
        print(f"  {index}. {render(option)}")

    while True:
        raw = input("请选择序号: ").strip()
        try:
            choice = int(raw)
        except ValueError:
            print("[!] 请输入数字序号")
            continue

        if 1 <= choice <= len(options):
            return options[choice - 1]

        print("[!] 序号超出范围")


def select_network_addresses(interfaces):
    interface_names = sorted(interfaces)
    selected_name = prompt_choice(
        "检测到可用网卡",
        interface_names,
        lambda name: format_interface(interfaces[name]),
    )

    if not selected_name:
        print("[!] 未检测到可用 IPv4/IPv6 地址，客户端配置不会生成节点")
        return NetworkSelection(interface=None, ipv4=None, ipv6=None)

    selected = interfaces[selected_name]
    ipv4 = prompt_choice(
        f"{selected.name} 可用 IPv4",
        selected.ipv4,
        lambda value: value,
    )
    ipv6 = prompt_choice(
        f"{selected.name} 可用 IPv6",
        selected.ipv6,
        lambda value: value,
    )

    return NetworkSelection(interface=selected.name, ipv4=ipv4, ipv6=ipv6)


def format_interface(item):
    ipv4 = ", ".join(item.ipv4) if item.ipv4 else "-"
    ipv6 = ", ".join(item.ipv6) if item.ipv6 else "-"
    return f"{item.name}  IPv4: {ipv4}  IPv6: {ipv6}"


def detect_network_selection():
    interfaces = get_ip_a_interfaces()
    return select_network_addresses(interfaces)


def prompt_port(prompt, default):
    while True:
        raw = input(f"{prompt} [{default}]: ").strip()
        if not raw:
            return default

        try:
            port = int(raw)
        except ValueError:
            print("[!] 端口必须是数字")
            continue

        if 1 <= port <= 65535:
            if port == XRAY_PORT:
                print("[!] 该端口通常已被 Xray 使用，建议换一个端口")
            return port

        print("[!] 端口范围必须是 1-65535")


def build_vless_proxy(name, server, artifacts):
    proxy = {
        "name": name,
        "type": "vless",
        "server": server,
        "port": XRAY_PORT,
        "uuid": artifacts.uuid,
        "udp": True,
        "tls": True,
        "servername": XRAY_REALITY_SERVER_NAME,
        "flow": "xtls-rprx-vision",
        "client-fingerprint": "chrome",
        "reality-opts": {
            "public-key": artifacts.public_key,
            "short-id": artifacts.short_id,
            "support-x25519mlkem768": True,
        },
        "encryption": artifacts.encryption,
        "network": "tcp",
        "packet-encoding": "xudp",
    }

    if ip_version(server) == 6:
        proxy["ip-version"] = "ipv6"

    return proxy


def build_hy2_proxy(name, server, artifacts):
    proxy = {
        "name": name,
        "type": "hysteria2",
        "server": server,
        "port": artifacts.port,
        "password": artifacts.password,
        "obfs": "salamander",
        "obfs-password": artifacts.obfs_password,
        "sni": HY2_SNI,
        "skip-cert-verify": True,
        "udp": True,
    }

    if ip_version(server) == 6:
        proxy["ip-version"] = "ipv6"

    return proxy


def ip_version(value):
    try:
        return ipaddress.ip_address(value).version
    except ValueError:
        return None


def build_xray_client_config(artifacts, network):
    proxies = []
    if network.ipv4:
        proxies.append(build_vless_proxy(f"VLESS IPv4 {network.ipv4}", network.ipv4, artifacts))
    if network.ipv6:
        proxies.append(build_vless_proxy(f"VLESS IPv6 {network.ipv6}", network.ipv6, artifacts))
    return {"proxies": proxies}


def build_hy2_client_config(artifacts, network):
    proxies = []
    if network.ipv4:
        proxies.append(build_hy2_proxy(f"HY2 IPv4 {network.ipv4}", network.ipv4, artifacts))
    if network.ipv6:
        proxies.append(build_hy2_proxy(f"HY2 IPv6 {network.ipv6}", network.ipv6, artifacts))
    return {"proxies": proxies}


def build_combined_client_config(xray_artifacts, hy2_artifacts, network):
    proxies = []
    if xray_artifacts:
        proxies.extend(build_xray_client_config(xray_artifacts, network)["proxies"])
    if hy2_artifacts:
        proxies.extend(build_hy2_client_config(hy2_artifacts, network)["proxies"])
    return {"proxies": proxies}


def yaml_scalar(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    raise TypeError(f"不支持的 YAML 值类型: {type(value).__name__}")


def dump_yaml(data, indent=0):
    lines = []
    prefix = " " * indent

    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, dict):
                lines.append(f"{prefix}{key}:")
                lines.extend(dump_yaml(value, indent + 2))
            elif isinstance(value, list):
                lines.append(f"{prefix}{key}:")
                lines.extend(dump_yaml(value, indent + 4))
            else:
                lines.append(f"{prefix}{key}: {yaml_scalar(value)}")
        return lines

    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                keys = list(item.keys())
                if not keys:
                    lines.append(f"{prefix}- {{}}")
                    continue
                first_key = keys[0]
                first_value = item[first_key]
                if isinstance(first_value, (dict, list)):
                    lines.append(f"{prefix}- {first_key}:")
                    lines.extend(dump_yaml(first_value, indent + 4))
                else:
                    lines.append(f"{prefix}- {first_key}: {yaml_scalar(first_value)}")
                for key in keys[1:]:
                    value = item[key]
                    if isinstance(value, (dict, list)):
                        lines.append(f"{prefix}  {key}:")
                        lines.extend(dump_yaml(value, indent + 4))
                    else:
                        lines.append(f"{prefix}  {key}: {yaml_scalar(value)}")
            elif isinstance(item, list):
                lines.append(f"{prefix}-")
                lines.extend(dump_yaml(item, indent + 2))
            else:
                lines.append(f"{prefix}- {yaml_scalar(item)}")
        return lines

    raise TypeError(f"不支持的 YAML 根类型: {type(data).__name__}")


def write_yaml_file(path, data):
    if not data["proxies"]:
        print(f"[!] 未生成节点，跳过 {path}")
        return

    write_text_file(path, "\n".join(dump_yaml(data)) + "\n")


def generate_hy2_self_signed_cert():
    require_command("openssl")
    os.makedirs("/etc/hysteria", exist_ok=True)
    run_command(
        [
            "openssl",
            "req",
            "-x509",
            "-nodes",
            "-newkey",
            "ec",
            "-pkeyopt",
            "ec_paramgen_curve:prime256v1",
            "-keyout",
            "/etc/hysteria/key.pem",
            "-out",
            "/etc/hysteria/cert.pem",
            "-subj",
            f"/CN={HY2_SNI}",
            "-days",
            "36500",
        ]
    )
    os.chmod("/etc/hysteria/key.pem", 0o644)
    os.chmod("/etc/hysteria/cert.pem", 0o644)
    print("[+] 已生成 Hysteria2 自签证书")


def restart_service(service_name):
    if not shutil.which("systemctl"):
        print(f"[!] 未找到 systemctl，跳过重启 {service_name}")
        return

    result = run_command(["systemctl", "restart", service_name], check=False)
    if result.returncode == 0:
        print(f"[+] 已重启: {service_name}")
    else:
        print(f"[!] 重启 {service_name} 失败，请手动检查服务状态")


def install_xray():
    require_command("curl")
    run_command(
        [
            "bash",
            "-lc",
            'bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install',
        ],
        check=False,
    )


def install_hy2():
    require_command("curl")
    run_command(
        [
            "bash",
            "-lc",
            "bash <(curl -fsSL https://get.hy2.sh/)",
        ],
        check=False,
    )


def install_and_configure_realm():
    require_command("curl")
    with tempfile.TemporaryDirectory(prefix="realm-") as temp_dir:
        archive_path = os.path.join(temp_dir, "realm.tar.gz")
        run_command(["curl", "-L", "-o", archive_path, REALM_PACKAGE_URL])

        with tarfile.open(archive_path, "r:gz") as archive:
            archive.extractall(temp_dir)

        realm_binary = find_extracted_realm_binary(temp_dir)
        if not realm_binary:
            raise RuntimeError("Realm 压缩包中未找到 realm 可执行文件")

        shutil.copy2(realm_binary, "/usr/local/bin/realm")
        os.chmod("/usr/local/bin/realm", 0o755)

    write_text_file(REALM_CONFIG_PATH, REALM_CONFIG)
    write_text_file(REALM_SERVICE_PATH, REALM_SERVICE)

    if shutil.which("systemctl"):
        run_command(["systemctl", "daemon-reload"], check=False)
        run_command(["systemctl", "enable", "--now", "realm"], check=False)
        print("[+] Realm 已安装并尝试启动")
    else:
        print("[!] 未找到 systemctl，Realm 已安装但未启动")


def find_extracted_realm_binary(root):
    for dirpath, _, filenames in os.walk(root):
        for filename in filenames:
            if filename == "realm":
                return os.path.join(dirpath, filename)
    return None


def parse_occupied_ports(ss_output):
    ports = {}

    for line in ss_output.splitlines():
        if not line.strip():
            continue

        port_match = re.search(r"(?:^|\s)(?:\[[^\]]+\]|[^:\s]+):(\d+)\s", line)
        if not port_match:
            continue

        port = int(port_match.group(1))
        processes = []
        for name, pid in re.findall(r'\("([^"]+)",pid=(\d+)', line):
            processes.append(f"{name}(pid={pid})")

        if not processes:
            processes = ["未知程序"]

        ports.setdefault(port, set()).update(processes)

    return [
        (port, ", ".join(sorted(processes)))
        for port, processes in sorted(ports.items(), key=lambda item: item[0])
    ]


def blue_text(text):
    return f"\033[34m{text}\033[0m"


def format_occupied_ports(ports, color=False):
    if not ports:
        return ""

    port_width = max(5, max(len(str(port)) for port, _ in ports))
    header = "端口" + " " * max(2, port_width - len("端口")) + "占用程序/任务"
    separator = f"{'-' * port_width}  {'-' * 17}"
    rows = []
    for port, processes in ports:
        rendered_port = f"{port:>{port_width}}"
        if color:
            rendered_port = blue_text(rendered_port)
        rows.append(f"{rendered_port}  {processes}")
    return "\n".join([header, separator, *rows])


def show_occupied_ports():
    require_command("ss")
    result = run_command(["ss", "-H", "-lntup"], check=False, capture_output=True)
    if result.returncode != 0:
        print(f"[!] 查看端口失败: {(result.stderr or 'ss 命令执行失败').strip()}")
        return

    ports = parse_occupied_ports(result.stdout)
    if not ports:
        print("[+] 未发现监听中的占用端口")
        return

    print(format_occupied_ports(ports, color=True))


def restore_xray(network=None, *, restart=True):
    if network is None:
        network = detect_network_selection()

    artifacts = generate_xray_artifacts()
    write_json_file(XRAY_CONFIG_PATH, artifacts.config)
    write_yaml_file(CLASH_XRAY_CONFIG_PATH, build_xray_client_config(artifacts, network))

    if restart:
        restart_service("xray")

    return artifacts


def restore_hy2(network=None, port=None, *, restart=True):
    if network is None:
        network = detect_network_selection()
    if port is None:
        port = prompt_port("请输入 Hysteria2 监听端口", HY2_DEFAULT_PORT)

    artifacts = generate_hy2_artifacts(port)
    write_text_file(HY2_CONFIG_PATH, artifacts.config_text)
    generate_hy2_self_signed_cert()
    write_yaml_file(CLASH_HY2_CONFIG_PATH, build_hy2_client_config(artifacts, network))

    if restart:
        restart_service("hysteria-server")

    return artifacts


def build_sysctl_config(existing_text=""):
    managed_keys = set(SYSCTL_TUNING_VALUES)
    lines = [
        "# Generated by restore_bundle.py",
        "# Network tuning for Xray and Hysteria2.",
        "",
    ]

    preserved_lines = []
    for line in existing_text.splitlines():
        match = re.match(r"^\s*([A-Za-z0-9_.-]+)\s*=", line)
        if match and match.group(1) in managed_keys:
            continue
        if line.startswith("# Generated by restore_bundle.py") or line.startswith("# Network tuning for Xray and Hysteria2."):
            continue
        preserved_lines.append(line.rstrip())

    while preserved_lines and preserved_lines[-1] == "":
        preserved_lines.pop()

    if preserved_lines:
        lines.extend(preserved_lines)
        lines.append("")

    for key, value in SYSCTL_TUNING_VALUES.items():
        lines.append(f"{key} = {value}")
    return "\n".join(lines) + "\n"


def restore_sysctl():
    existing_text = ""
    if os.path.exists(SYSCTL_TUNING_PATH):
        with open(SYSCTL_TUNING_PATH, encoding="utf-8") as f:
            existing_text = f.read()
    write_text_file(SYSCTL_TUNING_PATH, build_sysctl_config(existing_text))
    run_command(["sysctl", "--system"], check=False)


def restore_all():
    network = detect_network_selection()
    hy2_port = prompt_port("请输入 Hysteria2 监听端口", HY2_DEFAULT_PORT)

    xray_artifacts = restore_xray(network=network)
    hy2_artifacts = restore_hy2(network=network, port=hy2_port)
    restore_sysctl()
    write_yaml_file(
        CLASH_COMBINED_CONFIG_PATH,
        build_combined_client_config(xray_artifacts, hy2_artifacts, network),
    )


def build_menu_text():
    return """
========================
 VPS 恢复工具
========================

1. 安装 Xray
2. 安装 Hysteria2
3. 生成并恢复 Xray 配置
4. 生成并恢复 Hysteria2 配置
5. 写入 sysctl 网络调优
6. 全部恢复配置
7. 安装并配置 Realm 转发
8. 查看占用端口
0. 退出

========================
"""


def main():
    configure_stdio()

    if len(sys.argv) > 1:
        command = sys.argv[1].strip().lower()
        if command in ("ports", "port", "ss"):
            show_occupied_ports()
            return
        print(f"无效参数: {sys.argv[1]}")
        print("可用参数: ports")
        return

    while True:
        print(build_menu_text())

        choice = input("请选择: ").strip()

        try:
            if choice == "1":
                install_xray()
            elif choice == "2":
                install_hy2()
            elif choice == "3":
                restore_xray()
            elif choice == "4":
                restore_hy2()
            elif choice == "5":
                restore_sysctl()
            elif choice == "6":
                restore_all()
            elif choice == "7":
                install_and_configure_realm()
            elif choice == "8":
                show_occupied_ports()
            elif choice == "0":
                break
            else:
                print("无效选项")
        except Exception as exc:
            print(f"[!] 操作失败: {exc}")


if __name__ == "__main__":
    main()
