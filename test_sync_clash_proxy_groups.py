import textwrap
import unittest

import yaml

import sync_clash_proxy_groups as sync


class SyncClashProxyGroupsTests(unittest.TestCase):
    def test_sync_config_preserves_block_style_proxy_groups(self):
        text = textwrap.dedent(
            """\
            proxies:
                - name: "node-a"
                  type: "anytls"
                - name: "node-b"
                  type: "anytls"
            proxy-groups:
                - name: "🚀 节点选择"
                  type: "select"
                  proxies:
                    - "自动选择"
                    - "old-node"
                  url: "http://example.com"
                - name: "自动选择"
                  type: "url-test"
                  proxies:
                    - "old-node"
                  interval: 600
            """
        )

        synced, report = sync.sync_config(text)

        self.assertEqual(report["proxy_names"], ["node-a", "node-b"])
        self.assertEqual(report["updated_groups"], ["🚀 节点选择", "自动选择"])
        self.assertIn(
            '      proxies:\n'
            '        - "自动选择"\n'
            '        - "node-a"\n'
            '        - "node-b"\n'
            '      url: "http://example.com"',
            synced,
        )
        self.assertIn(
            '      proxies:\n'
            '        - "node-a"\n'
            '        - "node-b"\n'
            '      interval: 600',
            synced,
        )
        self.assertNotIn("proxies: [", synced)

        parsed = yaml.safe_load(synced)
        self.assertEqual(
            parsed["proxy-groups"][0]["proxies"],
            ["自动选择", "node-a", "node-b"],
        )

    def test_sync_config_still_supports_flow_style_proxy_groups(self):
        text = textwrap.dedent(
            """\
            proxies:
                - { name: 'node-a', type: anytls }
                - { name: 'node-b', type: anytls }
            proxy-groups:
                - { name: '🚀 节点选择', type: select, proxies: ['自动选择', 'old-node'] }
            """
        )

        synced, report = sync.sync_config(text)

        self.assertEqual(report["proxy_names"], ["node-a", "node-b"])
        self.assertEqual(report["updated_groups"], ["🚀 节点选择"])
        self.assertIn(
            "proxies: ['node-a', 'node-b']",
            synced,
        )


if __name__ == "__main__":
    unittest.main()
