import unittest

import restore_bundle as rb


class RestoreBundleTests(unittest.TestCase):
    def test_parse_ip_a_interfaces_skips_local_and_keeps_global_addresses(self):
        output = """
1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 state UNKNOWN group default qlen 1000
    inet 127.0.0.1/8 scope host lo
    inet6 ::1/128 scope host
2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 state UP group default qlen 1000
    inet 203.0.113.10/24 brd 203.0.113.255 scope global eth0
    inet6 fe80::1/64 scope link
    inet6 2001:db8::10/64 scope global
    inet6 2001:db8::11/64 scope global temporary
3: ens18: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 state UP group default qlen 1000
    inet 198.51.100.20/24 brd 198.51.100.255 scope global ens18
"""
        interfaces = rb.parse_ip_a_interfaces(output)

        self.assertEqual(set(interfaces), {"eth0", "ens18"})
        self.assertEqual(interfaces["eth0"].ipv4, ["203.0.113.10"])
        self.assertEqual(interfaces["eth0"].ipv6, ["2001:db8::10", "2001:db8::11"])
        self.assertEqual(interfaces["ens18"].ipv4, ["198.51.100.20"])
        self.assertEqual(interfaces["ens18"].ipv6, [])

    def test_parse_vlessenc_pair_prefers_post_quantum_mlkem_authentication(self):
        output = """
Authentication: X25519, not Post-Quantum
"decryption": "x25519-server"
"encryption": "x25519-client"

Authentication: ML-KEM-768, Post-Quantum
"decryption": "mlkem-server"
"encryption": "mlkem-client"
"""
        pair = rb.parse_vlessenc_pair(output)

        self.assertEqual(pair.decryption, "mlkem-server")
        self.assertEqual(pair.encryption, "mlkem-client")

    def test_build_xray_config_uses_generated_strong_mihomo_compatible_values(self):
        config = rb.build_xray_config(
            uuid_value="uuid-1",
            private_key="private-1",
            short_id="short-1",
            decryption="server-dec",
        )
        inbound = config["inbounds"][0]
        reality = inbound["streamSettings"]["realitySettings"]

        self.assertEqual(inbound["streamSettings"]["network"], "raw")
        self.assertEqual(inbound["settings"]["clients"][0]["id"], "uuid-1")
        self.assertNotIn("users", inbound["settings"])
        self.assertEqual(inbound["settings"]["decryption"], "server-dec")
        self.assertEqual(reality["target"], "www.paypal.com:443")
        self.assertNotIn("mldsa65Seed", reality)

    def test_hy2_artifacts_are_randomized(self):
        first = rb.generate_hy2_artifacts(8443)
        second = rb.generate_hy2_artifacts(8443)

        self.assertEqual(first.port, 8443)
        self.assertEqual(second.port, 8443)
        self.assertNotEqual(first.node_id, second.node_id)
        self.assertNotEqual(first.password, second.password)
        self.assertNotEqual(first.obfs_password, second.obfs_password)

    def test_client_proxy_names_are_short_and_do_not_embed_hy2_id(self):
        network = rb.NetworkSelection(
            interface="eth0",
            ipv4="203.0.113.10",
            ipv6="2001:db8::10",
        )
        xray = rb.XrayArtifacts(
            config={},
            uuid="uuid",
            public_key="pk",
            short_id="sid",
            encryption="enc",
        )
        hy2 = rb.Hy2Artifacts(
            node_id="9d5f77af-0000-4000-8000-000000000000",
            port=8443,
            password="pw",
            obfs_password="obfs",
            config_text="",
        )

        self.assertEqual(
            [proxy["name"] for proxy in rb.build_xray_client_config(xray, network)["proxies"]],
            ["VLESS IPv4 203.0.113.10", "VLESS IPv6 2001:db8::10"],
        )
        self.assertEqual(
            [proxy["name"] for proxy in rb.build_hy2_client_config(hy2, network)["proxies"]],
            ["HY2 IPv4 203.0.113.10", "HY2 IPv6 2001:db8::10"],
        )

    def test_generated_mihomo_client_file_names_are_protocol_focused(self):
        self.assertEqual(rb.CLASH_XRAY_CONFIG_PATH, "/root/vless.yaml")
        self.assertEqual(rb.CLASH_HY2_CONFIG_PATH, "/root/hy2.yaml")
        self.assertEqual(rb.CLASH_COMBINED_CONFIG_PATH, "/root/clash-proxies.yaml")

    def test_yaml_lists_under_mapping_keys_use_four_space_indent(self):
        yaml_text = "\n".join(
            rb.dump_yaml(
                {
                    "rules": [
                        "DOMAIN,sub2.yeshafast.top,DIRECT",
                        "DOMAIN,ipraft.com,🚀 节点选择",
                    ],
                    "proxies": [
                        {
                            "name": "VLESS IPv4 203.0.113.10",
                            "type": "vless",
                            "reality-opts": {
                                "public-key": "pk",
                                "short-ids": ["sid"],
                            },
                        },
                    ],
                }
            )
        )

        self.assertIn('rules:\n    - "DOMAIN,sub2.yeshafast.top,DIRECT"', yaml_text)
        self.assertIn('proxies:\n    - name: "VLESS IPv4 203.0.113.10"', yaml_text)
        self.assertIn('      reality-opts:\n        public-key: "pk"', yaml_text)
        self.assertIn('        short-ids:\n            - "sid"', yaml_text)

    def test_menu_lists_hy2_before_sysctl(self):
        menu = rb.build_menu_text()

        self.assertLess(
            menu.index("4. 生成并恢复 Hysteria2 配置"),
            menu.index("5. 写入 sysctl 网络调优"),
        )
        self.assertIn("7. 安装并配置 Realm 转发", menu)
        self.assertIn("8. 查看占用端口", menu)

    def test_sysctl_config_deduplicates_existing_keys(self):
        existing = """# Existing file
net.core.default_qdisc = pfifo_fast
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = cubic
custom.keep = 1
"""

        config = rb.build_sysctl_config(existing)

        self.assertEqual(config.count("net.core.default_qdisc = fq"), 1)
        self.assertEqual(config.count("net.ipv4.tcp_congestion_control = bbr"), 1)
        self.assertNotIn("net.core.default_qdisc = pfifo_fast", config)
        self.assertNotIn("net.ipv4.tcp_congestion_control = cubic", config)
        self.assertIn("custom.keep = 1", config)

    def test_parse_occupied_ports_groups_programs_by_listening_port(self):
        output = """
udp UNCONN 0 0 0.0.0.0:8443 0.0.0.0:* users:(("hysteria",pid=10,fd=3))
tcp LISTEN 0 4096 [::]:443 [::]:* users:(("xray",pid=20,fd=5))
tcp LISTEN 0 4096 127.0.0.1:443 0.0.0.0:* users:(("helper",pid=30,fd=7))
"""

        self.assertEqual(
            rb.parse_occupied_ports(output),
            [
                (443, "helper(pid=30), xray(pid=20)"),
                (8443, "hysteria(pid=10)"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
