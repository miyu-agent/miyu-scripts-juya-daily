# juya_daily.py 读新环境变量名的测试（审计 2026-11-18 #168）。
#
# 跑法（在仓库根）：
#   python3 -I -B -m unittest discover -s .github/tests
# （不加 -t .：.github 不是合法的包名，加了 unittest 会报 Start directory is not importable。）
#
# 只用标准库；不联网：http_text 换成桩，断网也能跑。HOME、XDG_*、MIYU_* 都指到临时目录。
# 放在 .github/tests/ 而不是 tests/：打包会跳过 .github，测试不会进包。

import importlib.util
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "juya_daily.py"


def load():
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location("juya_daily_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Sandbox(unittest.TestCase):
    """每条测试一个临时目录，环境里去掉所有 MIYU_*，网络函数先换成直接失败的桩。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("MIYU_", "XDG_"))}
        env.update(HOME=str(root / "home"), XDG_CONFIG_HOME=str(root / "cfg"),
                   XDG_CACHE_HOME=str(root / "xdg-cache"), XDG_STATE_HOME=str(root / "xdg-state"),
                   XDG_DATA_HOME=str(root / "xdg-data"))
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.root = root
        self.mod = load()
        self.calls = []

        def no_network(url, allow_404=False):
            self.calls.append(url)
            raise AssertionError(f"测试里不该联网：{url}")

        self.mod.http_text = no_network


class EnvNames(Sandbox):
    # 审计 #168：核心 FH-6 以后只给新名字 MIYU_CACHE_DIR，不再给 MIYU_SCRIPT_CACHE_DIR。
    # 只设新名字时，旧的期应一直缓存在 MIYU_CACHE_DIR。现在 cache_path 只读旧名，什么都不写，每次都联网。
    def test_issue_cached_in_new_env_name(self):
        cache = self.root / "cache"
        cache.mkdir()
        os.environ["MIYU_CACHE_DIR"] = str(cache)
        self.assertNotIn("MIYU_SCRIPT_CACHE_DIR", os.environ)
        text = "# 2020-01-01\n\n## 概览\n\n- 一条新闻\n"

        def fake_http_text(url, allow_404=False):
            self.calls.append(url)
            return text

        self.mod.http_text = fake_http_text
        self.assertEqual(self.mod.issue_markdown("2020-01-01"), text)
        self.assertTrue((cache / "issues" / "2020-01-01.md").is_file(),
                        f"MIYU_CACHE_DIR 里没有这一期的缓存：{sorted(os.listdir(cache))}")
        # 第二次应命中缓存，不再联网。
        self.calls.clear()
        self.assertEqual(self.mod.issue_markdown("2020-01-01"), text)
        self.assertEqual(self.calls, [], "缓存在 MIYU_CACHE_DIR 里，第二次不该再联网")


if __name__ == "__main__":
    unittest.main()
