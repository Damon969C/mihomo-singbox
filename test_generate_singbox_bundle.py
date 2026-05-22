import json
import tempfile
import unittest
from pathlib import Path

import generate_singbox_bundle as sb


class GenerateSingBoxBundleTests(unittest.TestCase):
    def setUp(self):
        self.params = sb.SingBoxParams(
            server_domain="edge.example.com",
            vless_port=2443,
            hy2_port=2444,
        )
        self.secrets = sb.SingBoxSecrets(
            uuid="11111111-1111-4111-8111-111111111111",
            reality_private_key="reality-private",
            reality_public_key="reality-public",
            reality_short_id="01234567",
            hy2_password="hy2-password",
            hy2_obfs_password="hy2-obfs-password",
        )

    def test_server_config_contains_vless_reality_vision_and_hy2(self):
        self.assertEqual(sb.REALITY_SERVER_NAME, "www.bilibili.com")

        config = sb.build_server_config(
            self.params,
            self.secrets,
            cert_path="/etc/sing-box/hy2-cert.pem",
            key_path="/etc/sing-box/hy2-key.pem",
        )

        self.assertEqual([item["tag"] for item in config["inbounds"]], ["vless-in", "hy2-in"])

        vless = config["inbounds"][0]
        self.assertEqual(vless["type"], "vless")
        self.assertEqual(vless["listen"], "::")
        self.assertEqual(vless["listen_port"], 2443)
        self.assertEqual(vless["users"][0]["uuid"], self.secrets.uuid)
        self.assertEqual(vless["users"][0]["flow"], "xtls-rprx-vision")
        self.assertTrue(vless["tls"]["enabled"])
        self.assertEqual(vless["tls"]["server_name"], sb.REALITY_SERVER_NAME)
        self.assertEqual(vless["tls"]["reality"]["handshake"]["server"], sb.REALITY_SERVER_NAME)
        self.assertEqual(vless["tls"]["reality"]["private_key"], "reality-private")
        self.assertEqual(vless["tls"]["reality"]["short_id"], ["01234567"])

        hy2 = config["inbounds"][1]
        self.assertEqual(hy2["type"], "hysteria2")
        self.assertEqual(hy2["listen_port"], 2444)
        self.assertEqual(hy2["users"][0]["password"], "hy2-password")
        self.assertEqual(hy2["obfs"], {"type": "salamander", "password": "hy2-obfs-password"})
        self.assertEqual(hy2["tls"]["certificate_path"], "/etc/sing-box/hy2-cert.pem")
        self.assertEqual(hy2["tls"]["key_path"], "/etc/sing-box/hy2-key.pem")

        self.assertEqual(config["route"]["final"], "direct")

    def test_client_config_routes_only_lan_cidrs_to_selector_defaulting_to_vless(self):
        config = sb.build_client_lan_config(self.params, self.secrets, default_tunnel="vless-out")

        tun = config["inbounds"][0]
        self.assertEqual(tun["type"], "tun")
        self.assertEqual(tun["route_address"], sb.LAN_CIDRS)
        self.assertEqual(tun["mtu"], 1280)
        self.assertEqual(config["dns"], sb.build_lan_dns_config())
        self.assertEqual(config["route"]["rules"], sb.LAN_ROUTE_RULES)
        self.assertEqual(config["route"]["default_domain_resolver"], sb.BOOTSTRAP_DOMAIN_RESOLVER)
        self.assertEqual(config["route"]["default_domain_resolver"]["strategy"], "prefer_ipv4")
        self.assertTrue(tun["auto_route"])
        self.assertTrue(tun["auto_redirect"])
        self.assertNotIn("sniff", tun)

        selector = config["outbounds"][0]
        self.assertEqual(selector["type"], "selector")
        self.assertEqual(selector["tag"], "lan-select")
        self.assertEqual(selector["outbounds"], ["vless-out", "hy2-out"])
        self.assertEqual(selector["default"], "vless-out")

        vless = next(item for item in config["outbounds"] if item["tag"] == "vless-out")
        self.assertEqual(vless["server"], "edge.example.com")
        self.assertEqual(vless["server_port"], 2443)
        self.assertEqual(vless["flow"], "xtls-rprx-vision")
        self.assertNotIn("network", vless)
        self.assertEqual(vless["packet_encoding"], "xudp")
        self.assertEqual(vless["tls"]["reality"]["public_key"], "reality-public")

        hy2 = next(item for item in config["outbounds"] if item["tag"] == "hy2-out")
        self.assertEqual(hy2["server"], "edge.example.com")
        self.assertEqual(hy2["server_port"], 2444)
        self.assertEqual(hy2["password"], "hy2-password")
        self.assertTrue(hy2["tls"]["insecure"])

        self.assertEqual(config["dns"]["final"], sb.LOCAL_DNS_PRIMARY_TAG)
        self.assertEqual(config["dns"]["strategy"], "prefer_ipv4")
        self.assertEqual(config["dns"]["cache_capacity"], 4096)
        self.assertNotIn("timeout", config["dns"])
        self.assertEqual(config["dns"]["servers"][1]["server"], "180.76.76.76")
        self.assertEqual(config["dns"]["servers"][2]["server"], "223.5.5.5")
        self.assertNotIn("detour", config["dns"]["servers"][1])
        self.assertNotIn("detour", config["dns"]["servers"][2])
        self.assertEqual(config["route"]["final"], "direct")

    def test_global_vless_client_routes_all_traffic_to_vless_selector(self):
        config = sb.build_client_global_lan_config(self.params, self.secrets, default_tunnel="vless-out")

        tun = config["inbounds"][0]
        self.assertEqual(tun["route_address"], sb.GLOBAL_ROUTE_CIDRS)
        self.assertEqual(config["dns"], sb.build_global_dns_config())
        self.assertEqual(config["dns"]["strategy"], "prefer_ipv4")
        self.assertEqual(config["dns"]["cache_capacity"], 4096)
        self.assertNotIn("timeout", config["dns"])
        self.assertEqual(config["dns"]["servers"][1]["type"], "tcp")
        self.assertEqual(config["dns"]["servers"][1]["server"], "10.0.0.1")
        self.assertEqual(config["dns"]["servers"][1]["detour"], "lan-select")
        self.assertEqual(config["route"]["rules"], sb.GLOBAL_ROUTE_RULES)
        self.assertEqual(config["route"]["default_domain_resolver"], sb.BOOTSTRAP_DOMAIN_RESOLVER)
        self.assertEqual(config["route"]["final"], "lan-select")
        self.assertEqual(config["outbounds"][0]["default"], "vless-out")

        vless = next(item for item in config["outbounds"] if item["tag"] == "vless-out")
        hy2 = next(item for item in config["outbounds"] if item["tag"] == "hy2-out")
        self.assertEqual(vless["domain_resolver"], sb.BOOTSTRAP_DOMAIN_RESOLVER)
        self.assertEqual(hy2["domain_resolver"], sb.BOOTSTRAP_DOMAIN_RESOLVER)

    def test_hy2_prefixed_clients_default_to_hy2(self):
        lan = sb.build_client_lan_config(self.params, self.secrets, default_tunnel="hy2-out")
        global_lan = sb.build_client_global_lan_config(self.params, self.secrets, default_tunnel="hy2-out")
        mihomo = sb.build_client_global_mihomo_config(self.params, self.secrets, default_tunnel="hy2-out")

        self.assertEqual(lan["outbounds"][0]["default"], "hy2-out")
        self.assertEqual(global_lan["outbounds"][0]["default"], "hy2-out")
        self.assertEqual(mihomo["outbounds"][0]["default"], "hy2-out")

    def test_global_mihomo_client_routes_all_traffic_to_mihomo_over_tunnel(self):
        config = sb.build_client_global_mihomo_config(self.params, self.secrets, default_tunnel="vless-out")

        tun = config["inbounds"][0]
        self.assertEqual(tun["route_address"], sb.GLOBAL_ROUTE_CIDRS)
        self.assertEqual(config["dns"], sb.build_mihomo_dns_config())
        self.assertEqual(config["route"]["rules"], sb.GLOBAL_ROUTE_RULES)
        self.assertEqual(config["route"]["default_domain_resolver"], sb.BOOTSTRAP_DOMAIN_RESOLVER)
        self.assertEqual(config["route"]["final"], "mihomo-out")

        mihomo = next(item for item in config["outbounds"] if item["tag"] == "mihomo-out")
        self.assertEqual(mihomo["type"], "socks")
        self.assertEqual(mihomo["server"], sb.MIHOMO_SERVER)
        self.assertEqual(mihomo["server_port"], sb.MIHOMO_MIXED_PORT)
        self.assertEqual(mihomo["version"], "5")
        self.assertEqual(mihomo["detour"], "lan-select")

        dns_servers = config["dns"]["servers"]
        self.assertEqual(dns_servers[1]["tag"], sb.MIHOMO_DNS_TAG)
        self.assertEqual(dns_servers[1]["type"], "tcp")
        self.assertEqual(dns_servers[1]["server"], sb.MIHOMO_SERVER)
        self.assertEqual(dns_servers[1]["server_port"], sb.MIHOMO_DNS_PORT)
        self.assertEqual(dns_servers[1]["detour"], "lan-select")
        self.assertEqual(config["dns"]["cache_capacity"], 4096)
        self.assertNotIn("timeout", config["dns"])

    def test_write_bundle_creates_server_client_and_secret_summary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            result = sb.write_bundle(self.params, self.secrets, output_dir)

            self.assertEqual(result.server_config_path, output_dir / "sing-box-server.json")
            self.assertEqual(
                result.client_config_paths,
                {
                    "vless-10": output_dir / "vless-10.json",
                    "vless-lan": output_dir / "vless-lan.json",
                    "vless-mihomo": output_dir / "vless-mihomo.json",
                    "hy2-10": output_dir / "hy2-10.json",
                    "hy2-lan": output_dir / "hy2-lan.json",
                    "hy2-mihomo": output_dir / "hy2-mihomo.json",
                },
            )
            self.assertEqual(result.summary_path, output_dir / "sing-box-secrets.txt")
            self.assertTrue(result.server_config_path.exists())
            for client_path in result.client_config_paths.values():
                self.assertTrue(client_path.exists())
            self.assertTrue(result.summary_path.exists())

            server = json.loads(result.server_config_path.read_text(encoding="utf-8"))
            vless_10 = json.loads((output_dir / "vless-10.json").read_text(encoding="utf-8"))
            vless_lan = json.loads((output_dir / "vless-lan.json").read_text(encoding="utf-8"))
            vless_mihomo = json.loads((output_dir / "vless-mihomo.json").read_text(encoding="utf-8"))
            hy2_10 = json.loads((output_dir / "hy2-10.json").read_text(encoding="utf-8"))
            hy2_lan = json.loads((output_dir / "hy2-lan.json").read_text(encoding="utf-8"))
            hy2_mihomo = json.loads((output_dir / "hy2-mihomo.json").read_text(encoding="utf-8"))
            self.assertEqual(server["inbounds"][0]["listen_port"], 2443)
            self.assertEqual(vless_10["route"]["final"], "direct")
            self.assertEqual(vless_lan["route"]["final"], "lan-select")
            self.assertEqual(vless_mihomo["route"]["final"], "mihomo-out")
            self.assertEqual(hy2_10["route"]["final"], "direct")
            self.assertEqual(hy2_lan["route"]["final"], "lan-select")
            self.assertEqual(hy2_mihomo["route"]["final"], "mihomo-out")
            self.assertEqual(vless_lan["outbounds"][0]["default"], "vless-out")
            self.assertEqual(vless_mihomo["outbounds"][0]["default"], "vless-out")
            self.assertEqual(hy2_lan["outbounds"][0]["default"], "hy2-out")
            self.assertEqual(hy2_mihomo["outbounds"][0]["default"], "hy2-out")

    def test_parse_reality_key_pair_accepts_sing_box_output(self):
        pair = sb.parse_reality_key_pair(
            """
            PrivateKey: private-value
            PublicKey: public-value
            """
        )

        self.assertEqual(pair, ("private-value", "public-value"))


if __name__ == "__main__":
    unittest.main()
